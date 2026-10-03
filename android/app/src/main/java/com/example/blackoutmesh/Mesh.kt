package com.example.blackoutmesh

import android.annotation.SuppressLint
import android.bluetooth.*
import android.bluetooth.le.*
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.ParcelUuid
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
    private var running = false
    private var advertising = false
    private var ticks = 0
    private var gattServer: BluetoothGattServer? = null
    private var activeGatt: BluetoothGatt? = null
    private val lastAttempt = mutableMapOf<String, Long>()

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
        handler.removeCallbacks(expiryTick)
        handler.postDelayed(expiryTick, 60_000)
        log("Mesh started: listening for alerts")
    }

    fun stop() {
        if (!running) return
        running = false
        stopScan()
        stopAdvertising()
        gattServer?.close()
        gattServer = null
        handler.removeCallbacks(expiryTick)
        handler.removeCallbacks(timeout)
        activeGatt?.close()
        activeGatt = null
        busy = false
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

    private fun accept(raw: ByteArray, source: String) {
        handler.post {
            val alert = AlertCodec.verifyAndParse(raw)
            if (alert == null) {
                log("REJECTED packet (bad signature, bad format, or expired)")
                return@post
            }
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
    }

    // Pick up where we left off after restart. Re-verified so tampering/expiry is caught.
    private fun restoreSavedAlert() {
        val raw = store.load() ?: return
        val alert = AlertCodec.verifyAndParse(raw)
        if (alert == null) {
            store.clear()
            return
        }
        current = alert
        onAlert(alert)
        startAdvertising()
        log("Restored saved alert ${alert.msgId}")
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

    // Once a minute: expiry check, scan restart every 10 min, heartbeat every 15 min
    private val expiryTick = object : Runnable {
        override fun run() {
            if (!running) return
            expireIfNeeded()
            ticks++
            if (ticks % 10 == 0) restartScan()
            if (ticks % 15 == 0) log("Heartbeat: scanning, alert=${current?.msgId ?: "none"}")
            handler.postDelayed(this, 60_000)
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
            val uuids = record?.serviceUuids
            val name = result.device.name ?: record?.deviceName
            val mfgData = record?.manufacturerSpecificData?.get(0xFFFF)

            val isPhoneMesh = uuids != null && uuids.contains(ParcelUuid(Config.SERVICE_UUID))
            val isOfficialBroadcaster = (name != null && (name.contains("LOBA", ignoreCase = true) || name.contains("Blackout", ignoreCase = true))) ||
                                        (mfgData != null && mfgData.isNotEmpty())

            if (isPhoneMesh || isOfficialBroadcaster) {
                expireIfNeeded()
                maybeFetch(result.device, result.rssi)
            }
        }

        override fun onScanFailed(errorCode: Int) {
            log("Scan failed, code $errorCode")
        }
    }

    private fun startScan() {
        val scanner = adapter?.bluetoothLeScanner ?: return
        val settings = ScanSettings.Builder()
            .setScanMode(Config.SCAN_MODE)
            .setReportDelay(0)
            .build()
        scanner.startScan(null, settings, scanCallback)
    }

    private fun stopScan() {
        try {
            adapter?.bluetoothLeScanner?.stopScan(scanCallback)
        } catch (_: Exception) {}
    }

    private fun restartScan() {
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
        val cooldown = if (current != null) Config.REFETCH_COOLDOWN_MS else 4_000L
        if (now - last < cooldown) return

        lastAttempt[device.address] = now
        busy = true

        // Crucial for Error 147: pause scanning while connecting so the radio
        // does not collide frequency-hopping with LE connection parameter establishment
        stopScan()

        val name = device.name ?: "Broadcaster"
        log("Broadcaster in range: $name (${device.address}, $rssi dBm). Connecting...")
        handler.postDelayed(timeout, Config.FETCH_TIMEOUT_MS)
        activeGatt = device.connectGatt(
            context, false, clientCallback, BluetoothDevice.TRANSPORT_LE
        )
    }

    private val timeout = Runnable {
        log("Fetch timed out")
        endFetch(activeGatt)
    }

    private fun endFetch(gatt: BluetoothGatt? = null) {
        handler.post {
            handler.removeCallbacks(timeout)
            try {
                gatt?.disconnect()
                gatt?.close()
            } catch (_: Exception) {}
            try {
                if (activeGatt != null && activeGatt != gatt) {
                    activeGatt?.disconnect()
                    activeGatt?.close()
                }
            } catch (_: Exception) {}
            activeGatt = null
            busy = false
            // Resume scanning after connection completes or fails
            if (running) {
                startScan()
            }
        }
    }

    @Volatile private var servicesDiscovered = false

    private val clientCallback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) {
            if (newState == BluetoothProfile.STATE_CONNECTED && status == BluetoothGatt.GATT_SUCCESS) {
                log("Connected to ${gatt.device.address}! Negotiating MTU...")
                servicesDiscovered = false
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
        if (status == BluetoothGatt.GATT_SUCCESS) {
            log("Received ${value.size} bytes from broadcaster! Verifying signature...")
            accept(value, "fetched")
        } else {
            log("Read failed, status $status")
        }
        endFetch(gatt)
    }

    // ------------------------------------------------------------------

    private fun log(message: String) {
        handler.post { onLog(message) }
    }
}
