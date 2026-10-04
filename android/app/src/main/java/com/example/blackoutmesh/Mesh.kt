package com.example.blackoutmesh

import android.annotation.SuppressLint
import android.bluetooth.*
import android.bluetooth.le.*
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.ParcelUuid
import android.os.PowerManager
import android.os.SystemClock

@SuppressLint("MissingPermission") // The service only starts after permissions are granted
class Mesh(
    private val context: Context,
    private val store: AlertStore,
    private val onLog: (String) -> Unit,
    private val onAlert: (Alert?) -> Unit
) {
    private val manager = context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager
    private val adapter: BluetoothAdapter? = manager.adapter
    private val handler = Handler(Looper.getMainLooper())

    @Volatile private var current: Alert? = null
    @Volatile private var busy = false
    @Volatile private var fetchStartTime = 0L
    private val myNodeId: Int = java.util.Random().nextInt()
    private var running = false
    private var advertising = false
    private var ticks = 0
    private var gattServer: BluetoothGattServer? = null
    private var activeGatt: BluetoothGatt? = null
    private val lastAttempt = mutableMapOf<String, Long>()
    private var fetchWakeLock: PowerManager.WakeLock? = null

    private fun acquireFetchWakeLock() {
        try {
            if (fetchWakeLock == null) {
                val pm = context.getSystemService(Context.POWER_SERVICE) as? PowerManager
                fetchWakeLock = pm?.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "BlackoutMesh:FetchLock")
            }
            fetchWakeLock?.acquire(12_000L)
        } catch (_: Exception) {}
    }

    private fun releaseFetchWakeLock() {
        try {
            if (fetchWakeLock?.isHeld == true) {
                fetchWakeLock?.release()
            }
        } catch (_: Exception) {}
    }

    // ------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------

    fun start() {
        val ad = adapter
        if (ad == null || !ad.isEnabled) {
            log("Bluetooth is off or unavailable. Will resume when it turns on.")
            return
        }
        if (running) return
        running = true
        openGattServer()
        startScan()
        restoreSavedAlert()
        handler.removeCallbacks(watchdogTick)
        handler.postDelayed(watchdogTick, 10_000)
        log("Mesh started: listening for alerts (background active)")
    }

    fun stop() {
        if (!running) return
        running = false
        stopScan()
        stopAdvertising()
        gattServer?.close()
        gattServer = null
        handler.removeCallbacks(watchdogTick)
        handler.removeCallbacks(timeout)
        activeGatt?.close()
        activeGatt = null
        busy = false
        fetchStartTime = 0L
        releaseFetchWakeLock()
        log("Mesh stopped")
    }

    /** Pretend we are the first phone: load the built-in test alert and start shouting. */
    fun seedWithTestAlert() {
        if (!running) start()
        val raw = runCatching { AlertCodec.hex(Config.SEED_PACKET_HEX) }.getOrNull()
        if (raw == null) {
            log("SEED_PACKET_HEX is not set in Config.kt")
            return
        }
        accept(raw, "seeded")
    }

    /** Clear active alert to test receiving fresh updates. */
    fun clearCurrentAlert() {
        current = null
        store.clear()
        stopAdvertising()
        onAlert(null)
        lastAttempt.clear()
        busy = false
        log("Active alert cleared. Ready for fresh broadcast.")
    }

    // ------------------------------------------------------------------
    // Accepting, restoring and expiring alerts
    // ------------------------------------------------------------------

    private fun accept(raw: ByteArray, source: String, deviceAddress: String? = null) {
        handler.post {
            when (val res = AlertCodec.verifyDetailed(raw)) {
                is AlertCodec.VerificationResult.Valid -> {
                    val alert = res.alert
                    val cur = current
                    if (cur != null && alert.msgId == cur.msgId && alert.template == cur.template && alert.param == cur.param && alert.issuedAt <= cur.issuedAt) {
                        return@post
                    }
                    current = alert
                    store.save(alert.raw)
                    log("ACCEPTED alert ${alert.msgId} (T${alert.template}/P${alert.param}) from $source")
                    onAlert(alert)
                    startAdvertising()
                }
                is AlertCodec.VerificationResult.Expired -> {
                    val minsAgo = (res.nowSec - (res.issuedAt + res.validMinutes * 60L)) / 60
                    log("REJECTED packet: Alert EXPIRED $minsAgo min ago! (Issued ${res.issuedAt}, valid ${res.validMinutes}m). Broadcaster must dispatch a new alert.")
                    if (deviceAddress != null) {
                        lastAttempt[deviceAddress] = SystemClock.elapsedRealtime() + 15_000L
                    }
                }
                is AlertCodec.VerificationResult.InvalidSignature -> {
                    log("REJECTED packet: Signature INVALID! Check authority key in Config.kt.")
                    if (deviceAddress != null) {
                        lastAttempt[deviceAddress] = SystemClock.elapsedRealtime() + 15_000L
                    }
                }
                is AlertCodec.VerificationResult.InvalidLength -> {
                    log("REJECTED packet: Bad length ${res.actual} bytes (expected ${res.expected} bytes)")
                    if (deviceAddress != null) {
                        lastAttempt[deviceAddress] = SystemClock.elapsedRealtime() + 15_000L
                    }
                }
                is AlertCodec.VerificationResult.InvalidVersion -> {
                    log("REJECTED packet: Unsupported protocol version ${res.version}")
                }
            }
        }
    }

    // Pick up where we left off after restart. Re-verified so tampering/expiry is caught.
    private fun restoreSavedAlert() {
        val raw = store.load() ?: return
        when (val res = AlertCodec.verifyDetailed(raw)) {
            is AlertCodec.VerificationResult.Valid -> {
                val alert = res.alert
                current = alert
                onAlert(alert)
                startAdvertising()
                log("Restored saved alert ${alert.msgId}")
            }
            is AlertCodec.VerificationResult.Expired -> {
                val minsAgo = (res.nowSec - (res.issuedAt + res.validMinutes * 60L)) / 60
                log("Saved alert expired $minsAgo min ago. Purging stored alert.")
                store.clear()
            }
            else -> {
                store.clear()
            }
        }
    }

    // Real-clock expiry (timers pause in deep sleep, so we also check on every event)
    private fun alertExpired(a: Alert): Boolean =
        System.currentTimeMillis() / 1000 > a.issuedAt + a.validMinutes * 60L

    private fun expireIfNeeded() {
        val a = current ?: return
        if (!alertExpired(a)) return
        current = null
        store.clear()
        stopAdvertising()
        onAlert(null)
        log("Alert expired, stopped forwarding")
    }

    // Watchdog tick: runs every 10s. Catches stuck connections, checks expiry, and keeps mesh alive
    private val watchdogTick = object : Runnable {
        override fun run() {
            if (!running) return
            expireIfNeeded()

            // Watchdog 1: Never allow busy to stay stuck if connection dropped silently in sleep
            val now = SystemClock.elapsedRealtime()
            if (busy && fetchStartTime > 0 && (now - fetchStartTime > 12_000L)) {
                log("Watchdog: Fetch timed out in background (${(now - fetchStartTime) / 1000}s). Resetting connection.")
                endFetch(activeGatt)
            }

            ticks++
            // Watchdog 2: Every 2 minutes, refresh the scan to avoid Android OS BLE background sleep stall
            if (ticks % 12 == 0) {
                restartScan()
            }
            if (ticks % 60 == 0) {
                log("Heartbeat: mesh alive, alert=${current?.msgId ?: "none"}")
            }
            handler.postDelayed(this, 10_000)
        }
    }

    // ------------------------------------------------------------------
    // The mailbox: GATT server (other phones read our alert from here)
    // ------------------------------------------------------------------

    private fun openGattServer() {
        val server = manager.openGattServer(context, serverCallback)
        val service = BluetoothGattService(
            Config.SERVICE_UUID, BluetoothGattService.SERVICE_TYPE_PRIMARY
        )
        val characteristic = BluetoothGattCharacteristic(
            Config.ALERT_CHAR_UUID,
            BluetoothGattCharacteristic.PROPERTY_READ,
            BluetoothGattCharacteristic.PERMISSION_READ
        )
        service.addCharacteristic(characteristic)
        server.addService(service)
        gattServer = server
    }

    private val serverCallback = object : BluetoothGattServerCallback() {
        override fun onCharacteristicReadRequest(
            device: BluetoothDevice,
            requestId: Int,
            offset: Int,
            characteristic: BluetoothGattCharacteristic
        ) {
            // Never serve an expired alert
            val raw = current?.takeIf { !alertExpired(it) }?.raw
            if (raw == null || offset > raw.size) {
                gattServer?.sendResponse(device, requestId, BluetoothGatt.GATT_FAILURE, 0, null)
                handler.post { expireIfNeeded() }
                return
            }
            val slice = raw.copyOfRange(offset, raw.size)
            gattServer?.sendResponse(device, requestId, BluetoothGatt.GATT_SUCCESS, offset, slice)
            log("Served alert to ${device.address}")
        }
    }

    // ------------------------------------------------------------------
    // The shouting: advertising a tiny beacon
    // ------------------------------------------------------------------

    private val advertiseCallback = object : AdvertiseCallback() {
        override fun onStartSuccess(settingsInEffect: AdvertiseSettings) {
            advertising = true
            log("Beacon on: advertising our alert")
        }

        override fun onStartFailure(errorCode: Int) {
            advertising = false
            log("Advertising failed, code $errorCode")
        }
    }

    private fun startAdvertising() {
        if (advertising) return
        val advertiser = adapter?.bluetoothLeAdvertiser
        if (advertiser == null) {
            log("This phone cannot advertise over BLE")
            return
        }
        val settings = AdvertiseSettings.Builder()
            .setAdvertiseMode(Config.ADVERTISE_MODE)
            .setTxPowerLevel(AdvertiseSettings.ADVERTISE_TX_POWER_HIGH)
            .setConnectable(true)
            .setTimeout(0)
            .build()
        val data = AdvertiseData.Builder()
            .setIncludeDeviceName(false)
            .addServiceUuid(ParcelUuid(Config.SERVICE_UUID))
            .addServiceData(
                ParcelUuid(Config.SERVICE_UUID),
                java.nio.ByteBuffer.allocate(4).putInt(myNodeId).array()
            )
            .build()
        advertiser.startAdvertising(settings, data, advertiseCallback)
    }

    private fun stopAdvertising() {
        if (!advertising) return
        adapter?.bluetoothLeAdvertiser?.stopAdvertising(advertiseCallback)
        advertising = false
    }

    // ------------------------------------------------------------------
    // The listening: scanning for other phones' beacons & official laptop
    // ------------------------------------------------------------------

    private val scanCallback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) {
            // Ignore non-connectable beacons (e.g. background manufacturer telemetry)
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O && !result.isConnectable) {
                return
            }

            val record = result.scanRecord

            // 1. SELF-FILTER: Ignore our own mesh advertisement to prevent deadlocks
            val sData = record?.getServiceData(ParcelUuid(Config.SERVICE_UUID))
            if (sData != null && sData.size >= 4) {
                val senderId = java.nio.ByteBuffer.wrap(sData).int
                if (senderId == myNodeId) {
                    return // Ignore our own beacon
                }
            }

            val uuids = record?.serviceUuids
            val name = result.device.name ?: record?.deviceName
            val mfgData = record?.manufacturerSpecificData?.get(0xFFFF)

            val isPhoneMesh = uuids != null && uuids.contains(ParcelUuid(Config.SERVICE_UUID))
            val isBkoutBeacon = mfgData != null && mfgData.isNotEmpty() &&
                    (mfgData.contentEquals("BKOUT".toByteArray(Charsets.UTF_8)) ||
                     (mfgData.size >= 5 && mfgData.sliceArray(0..4).contentEquals("BKOUT".toByteArray(Charsets.UTF_8))))
            val isOfficialBroadcaster = (name != null && (name.contains("LOBA", ignoreCase = true) || name.contains("Blackout", ignoreCase = true))) ||
                                        isBkoutBeacon

            if (isPhoneMesh || isOfficialBroadcaster) {
                expireIfNeeded()
                maybeFetch(result.device, result.rssi)
            }
        }

        override fun onScanFailed(errorCode: Int) {
            log("Scan failed, code $errorCode")
        }
    }

    private fun buildScanFilters(): List<ScanFilter> = listOf(
        // 1. Mesh peer phones
        ScanFilter.Builder().setServiceUuid(ParcelUuid(Config.SERVICE_UUID)).build(),
        // 2. Official Broadcaster beacon (0xFFFF manufacturer ID)
        ScanFilter.Builder().setManufacturerData(0xFFFF, byteArrayOf()).build(),
        // 3. Broadcaster by device name
        ScanFilter.Builder().setDeviceName("LOBA").build()
    )

    private fun startScan() {
        val scanner = adapter?.bluetoothLeScanner ?: return
        val settings = ScanSettings.Builder()
            .setScanMode(Config.SCAN_MODE)
            .setReportDelay(0)
            .setCallbackType(ScanSettings.CALLBACK_TYPE_ALL_MATCHES)
            .setMatchMode(ScanSettings.MATCH_MODE_AGGRESSIVE)
            .build()
        try {
            // Hardware scan filters allow Android OS to deliver scan results while in background & screen off
            scanner.startScan(buildScanFilters(), settings, scanCallback)
            log("BLE background scan active (hardware filtered)")
        } catch (e: Exception) {
            log("Scan start error: ${e.message}")
        }
    }

    private fun stopScan() {
        try {
            adapter?.bluetoothLeScanner?.stopScan(scanCallback)
        } catch (_: Exception) {}
    }

    private fun restartScan() {
        val now = SystemClock.elapsedRealtime()
        if (busy && (now - fetchStartTime > 10_000L)) {
            endFetch(activeGatt)
        }
        if (!busy) {
            stopScan()
            startScan()
        }
    }

    // ------------------------------------------------------------------
    // The walk-up-and-read: GATT client
    // ------------------------------------------------------------------

    private fun maybeFetch(device: BluetoothDevice, rssi: Int) {
        if (busy) return
        val now = SystemClock.elapsedRealtime()
        val last = lastAttempt[device.address] ?: 0L
        val cooldown = if (current != null) Config.REFETCH_COOLDOWN_MS else 8_000L
        if (now - last < cooldown) return

        lastAttempt[device.address] = now
        busy = true
        fetchStartTime = now
        acquireFetchWakeLock()

        // Crucial for Error 147 / Status 133: pause scanning while connecting so the radio
        // does not collide frequency-hopping with LE connection parameter establishment
        stopScan()

        val name = device.name ?: "Broadcaster"
        log("Broadcaster in range: $name (${device.address}, $rssi dBm). Connecting...")
        handler.postDelayed(timeout, Config.FETCH_TIMEOUT_MS)
        activeGatt = device.connectGatt(
            context.applicationContext, false, clientCallback, BluetoothDevice.TRANSPORT_LE
        )
    }

    private val timeout = Runnable {
        log("Fetch timed out")
        endFetch(activeGatt)
    }

    private fun endFetch(gatt: BluetoothGatt? = null) {
        handler.post {
            handler.removeCallbacks(timeout)
            val target = gatt ?: activeGatt
            if (target != null) {
                try {
                    target.disconnect()
                } catch (_: Exception) {}
                try {
                    target.close()
                } catch (_: Exception) {}
            }
            if (activeGatt != null && activeGatt != target) {
                try {
                    activeGatt?.disconnect()
                } catch (_: Exception) {}
                try {
                    activeGatt?.close()
                } catch (_: Exception) {}
            }
            activeGatt = null
            busy = false
            fetchStartTime = 0L
            releaseFetchWakeLock()
            // 400ms settle delay before restarting BLE scan so Bluetooth chip frequency synthesizer
            // settles after connection teardown (prevents Error 147 and Status 133 collisions)
            handler.postDelayed({
                if (running && !busy) {
                    startScan()
                }
            }, 400)
        }
    }

    @Volatile private var servicesDiscovered = false
    @Volatile private var serviceRetryAttempted = false

    private fun refreshGattCache(gatt: BluetoothGatt): Boolean {
        return try {
            val refreshMethod = gatt.javaClass.getMethod("refresh")
            (refreshMethod.invoke(gatt) as? Boolean) ?: false
        } catch (_: Exception) {
            false
        }
    }

    private val clientCallback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) {
            if (newState == BluetoothProfile.STATE_CONNECTED && status == BluetoothGatt.GATT_SUCCESS) {
                log("Connected to ${gatt.device.address}! Negotiating MTU...")
                servicesDiscovered = false
                serviceRetryAttempted = false
                gatt.requestMtu(Config.MTU)
                // Discover services fallback if onMtuChanged is delayed
                handler.postDelayed({
                    if (activeGatt == gatt && !servicesDiscovered) {
                        log("Discovering GATT services...")
                        gatt.discoverServices()
                    }
                }, 400)
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED || status != BluetoothGatt.GATT_SUCCESS) {
                if (status != BluetoothGatt.GATT_SUCCESS) {
                    log("Connection problem, status $status")
                    // On status 133 or connection error, back off this device address for 15s to prevent GATT table leak loops
                    lastAttempt[gatt.device.address] = SystemClock.elapsedRealtime() + 15_000L
                }
                endFetch(gatt)
            }
        }

        override fun onMtuChanged(gatt: BluetoothGatt, mtu: Int, status: Int) {
            log("MTU negotiated: $mtu bytes")
            if (!servicesDiscovered) {
                servicesDiscovered = true
                gatt.discoverServices()
            }
        }

        override fun onServicesDiscovered(gatt: BluetoothGatt, status: Int) {
            servicesDiscovered = true
            log("Services discovered. Looking for BlackoutMesh alert slot...")
            val service = gatt.getService(Config.SERVICE_UUID)
            if (service == null) {
                // If the remote device was cached by Android before the custom GATT service was running,
                // Android only sees default 1800/1801 services. Force cache clear and retry once.
                if (!serviceRetryAttempted) {
                    serviceRetryAttempted = true
                    log("Alert slot not found on initial scan (stale Android cache). Clearing cache & retrying...")
                    refreshGattCache(gatt)
                    handler.postDelayed({
                        if (activeGatt == gatt) {
                            gatt.discoverServices()
                        }
                    }, 350)
                    return
                }

                val svcs = gatt.services.map { it.uuid.toString().take(8) }
                log("Alert slot not found. Services: $svcs")
                endFetch(gatt)
                return
            }
            val characteristic = service.getCharacteristic(Config.ALERT_CHAR_UUID)
            if (characteristic == null) {
                log("Alert characteristic not found")
                endFetch(gatt)
                return
            }
            log("Reading 79-byte emergency alert packet...")
            gatt.readCharacteristic(characteristic)
        }

        // Android 13 and newer call this one
        override fun onCharacteristicRead(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            value: ByteArray,
            status: Int
        ) {
            handleRead(gatt, value, status)
        }

        // Android 12 calls this one
        @Deprecated("Deprecated in Java")
        override fun onCharacteristicRead(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            status: Int
        ) {
            handleRead(gatt, characteristic.value ?: ByteArray(0), status)
        }
    }

    private fun handleRead(gatt: BluetoothGatt, value: ByteArray, status: Int) {
        val addr = gatt.device.address
        if (status == BluetoothGatt.GATT_SUCCESS) {
            log("Received ${value.size} bytes from broadcaster! Verifying signature...")
            accept(value, "fetched", addr)
        } else {
            log("Read failed, status $status")
            lastAttempt[addr] = SystemClock.elapsedRealtime() + 15_000L
        }
        endFetch(gatt)
    }

    // ------------------------------------------------------------------

    private fun log(message: String) {
        handler.post { onLog(message) }
    }
}
