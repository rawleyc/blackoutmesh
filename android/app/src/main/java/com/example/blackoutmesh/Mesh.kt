package com.example.blackoutmesh

import android.annotation.SuppressLint
import android.bluetooth.*
import android.bluetooth.le.*
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.ParcelUuid
import android.os.SystemClock

@SuppressLint("MissingPermission") // MainActivity asks for permissions before calling anything here
class Mesh(
    private val context: Context,
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
    private var gattServer: BluetoothGattServer? = null
    private var activeGatt: BluetoothGatt? = null
    private val lastAttempt = mutableMapOf<String, Long>()

    // ------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------

    fun start() {
        val ad = adapter
        if (ad == null || !ad.isEnabled) {
            log("Bluetooth is off or unavailable. Turn it on and try again.")
            return
        }
        if (running) return
        running = true
        openGattServer()
        startScan()
        log("Mesh started: listening for alerts")
    }

    fun stop() {
        if (!running) return
        running = false
        stopScan()
        stopAdvertising()
        gattServer?.close()
        gattServer = null
        handler.removeCallbacks(expiry)
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

    // ------------------------------------------------------------------
    // Accepting an alert (from the seed button or from a fetch)
    // ------------------------------------------------------------------

    private fun accept(raw: ByteArray, source: String) {
        handler.post {
            val alert = AlertCodec.verifyAndParse(raw)
            if (alert == null) {
                log("REJECTED packet (bad signature, bad format, or expired)")
                return@post
            }
            val cur = current
            if (cur != null && alert.msgId == cur.msgId && alert.template == cur.template && alert.param == cur.param) {
                return@post
            }
            current = alert
            log("ACCEPTED alert (T${alert.template}/P${alert.param}) from $source")
            onAlert(alert)
            startAdvertising()
            scheduleExpiry(alert)
        }
    }

    /** Clear active alert to test receiving fresh updates. */
    fun clearCurrentAlert() {
        current = null
        stopAdvertising()
        onAlert(null)
        lastAttempt.clear()
        busy = false
        log("Active alert cleared. Ready for fresh broadcast.")
    }

    private val expiry = Runnable {
        current = null
        stopAdvertising()
        onAlert(null)
        log("Alert expired, stopped forwarding")
    }

    private fun scheduleExpiry(a: Alert) {
        handler.removeCallbacks(expiry)
        val ms = (a.issuedAt + a.validMinutes * 60L) * 1000 - System.currentTimeMillis()
        handler.postDelayed(expiry, ms.coerceAtLeast(0))
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
            val raw = current?.raw
            if (raw == null || offset > raw.size) {
                gattServer?.sendResponse(device, requestId, BluetoothGatt.GATT_FAILURE, 0, null)
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
            .setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_LOW_LATENCY)
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
    // ------------------------------------------------------------------
    // The listening: scanning for other phones' beacons & official laptop
    // ------------------------------------------------------------------

    private val scanCallback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) {
            val record = result.scanRecord
            val uuids = record?.serviceUuids
            val name = result.device.name ?: record?.deviceName
            val mfgData = record?.manufacturerSpecificData?.get(0xFFFF)

            val isPhoneMesh = uuids != null && uuids.contains(ParcelUuid(Config.SERVICE_UUID))
            val isOfficialBroadcaster = (name != null && (name.contains("LOBA", ignoreCase = true) || name.contains("Blackout", ignoreCase = true))) ||
                                        (mfgData != null && mfgData.isNotEmpty())

            if (isPhoneMesh || isOfficialBroadcaster) {
                maybeFetch(result.device, result.rssi)
            }
        }

        override fun onScanFailed(errorCode: Int) {
            log("BLE Scan failed! Error code: $errorCode")
        }
    }

    private fun startScan() {
        val scanner = adapter?.bluetoothLeScanner
        if (scanner == null) {
            log("ERROR: Bluetooth LE scanner is null. Ensure Bluetooth is ON.")
            return
        }
        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
            .setReportDelay(0)
            .build()

        scanner.startScan(null, settings, scanCallback)
        log("Mesh started: actively scanning for emergency alerts")
    }

    private fun stopScan() {
        adapter?.bluetoothLeScanner?.stopScan(scanCallback)
    }

    // ------------------------------------------------------------------
    // The walk-up-and-read: GATT client
    // ------------------------------------------------------------------

    private fun maybeFetch(device: BluetoothDevice, rssi: Int) {
        if (busy) return
        val now = SystemClock.elapsedRealtime()
        val last = lastAttempt[device.address] ?: 0L
        if (now - last < 8_000L) return // 8 second cooldown between attempts to same device

        lastAttempt[device.address] = now
        busy = true
        val name = device.name ?: "Official Broadcaster"
        log("Broadcaster in range: $name (${device.address}, $rssi dBm). Connecting...")
        handler.postDelayed(timeout, Config.FETCH_TIMEOUT_MS)
        activeGatt = device.connectGatt(
            context, false, clientCallback, BluetoothDevice.TRANSPORT_LE
        )
    }

    private val timeout = Runnable {
        log("Fetch timed out")
        endFetch()
    }

    private fun endFetch() {
        handler.post {
            handler.removeCallbacks(timeout)
            activeGatt?.close()
            activeGatt = null
            busy = false
        }
    }

    @Volatile private var servicesDiscovered = false

    private val clientCallback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) {
            if (newState == BluetoothProfile.STATE_CONNECTED && status == BluetoothGatt.GATT_SUCCESS) {
                log("Connected to ${gatt.device.address}! Negotiating MTU...")
                servicesDiscovered = false
                val mtuOk = gatt.requestMtu(Config.MTU)
                // Discover services fallback if onMtuChanged is delayed
                handler.postDelayed({
                    if (activeGatt == gatt && !servicesDiscovered) {
                        log("Discovering GATT services...")
                        gatt.discoverServices()
                    }
                }, 350)
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED || status != BluetoothGatt.GATT_SUCCESS) {
                if (status != BluetoothGatt.GATT_SUCCESS) log("Connection lost, status $status")
                endFetch()
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
                val svcs = gatt.services.map { it.uuid.toString().substring(0, 8) }
                log("Alert service not found. Available services: $svcs")
                endFetch()
                return
            }
            val characteristic = service.getCharacteristic(Config.ALERT_CHAR_UUID)
            if (characteristic == null) {
                log("Alert characteristic not found in service")
                endFetch()
                return
            }
            log("Reading 79-byte signed emergency alert packet...")
            gatt.readCharacteristic(characteristic)
        }

        // Android 13 and newer call this one
        override fun onCharacteristicRead(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            value: ByteArray,
            status: Int
        ) {
            handleRead(value, status)
        }

        // Android 12 calls this one
        @Deprecated("Deprecated in Java")
        override fun onCharacteristicRead(
            gatt: BluetoothGatt,
            characteristic: BluetoothGattCharacteristic,
            status: Int
        ) {
            handleRead(characteristic.value ?: ByteArray(0), status)
        }
    }

    private fun handleRead(value: ByteArray, status: Int) {
        if (status == BluetoothGatt.GATT_SUCCESS) {
            log("Received ${value.size} bytes from broadcaster! Verifying signature...")
            accept(value, "fetched")
        } else {
            log("Read failed, status $status")
        }
        endFetch()
    }

    // ------------------------------------------------------------------

    private fun log(message: String) {
        handler.post { onLog(message) }
    }
}
