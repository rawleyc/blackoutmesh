# BlackoutMesh: Always-On Service (WBS and Conversion Guide)

Goal: turn the working MVP into an app that keeps listening for alerts, holding them, and relaying them **with the screen off, the app closed, and the phone in a pocket**.

Estimated effort: about half a day to build, then one or two nights of soak testing. Most of the real work is testing on a real phone, because Android decides in the end whether your service lives.

> **Honesty note.** I could not compile or run Android code in my environment, and Android's background rules change between versions and phone brands. The code below follows the documented rules for Android 12 to 15 as I know them. Treat the first run as a test, and paste any error back to me.

---

## 1. What changes, in plain words

**Today (MVP):** the Bluetooth code lives inside the screen. Close the screen, Bluetooth stops.

**After this guide:**

```
  [ Screen (MainActivity) ]  <-- only shows status, has buttons
            |
            |  start / stop / test-alert commands
            v
  [ MeshService ]  <-- a "foreground service": lives on its own,
            |          shows a permanent notification
            v
  [ Mesh ]  <-- your existing Bluetooth code (beacon, mailbox, scan, fetch)
            |
            v
  [ AlertStore ]  <-- remembers the alert on disk, so a restart does not forget it
```

The screen and the service talk through one small shared object (`MeshRepository`), so there is no complicated binding code.

---

## 2. The Android rules we are designing around

1. **Foreground service.** To keep running in the background, the app must run a foreground service and show a notification the user can see. That notification is the price of staying alive.
2. **Service type (Android 14 and newer).** The manifest must declare the service type. Ours is `connectedDevice`, which is the type meant for Bluetooth work. It also requires the Bluetooth permissions to be granted *before* the service starts.
3. **Notification permission (Android 13 and newer).** The app must ask for it, or the status notification and the alert notification may not show.
4. **No starting from the background.** On Android 12 and newer, a foreground service generally must be started while the app is visible. So the user taps **Start** once. After that the service keeps going. A reboot restart is "best effort" (see Section 5, Step 9).
5. **Battery optimization and phone makers.** Samsung, Xiaomi, Huawei, OnePlus and others add extra "sleeping app" killers. A phone setting, not code, often decides the outcome. The app asks the user to exempt it, and we test on a real phone.
6. **Timers sleep when the phone sleeps.** Android timers based on "uptime" pause in deep sleep. So expiry is checked against the real clock on every event, not only by one timer.

---

## 3. Work Breakdown Structure (WBS)

Size key: **S** = up to about 2 hours, **M** = about half a day, **L** = 1 to 2 days.

### 1.0 Requirements and decisions

| ID | Work package | Deliverable and acceptance | Size |
| --- | --- | --- | --- |
| 1.1 | Define "always running" | Written list of what must survive: screen off, app swiped from recents, Bluetooth toggled, phone idle overnight. Reboot is best effort. | S |
| 1.2 | Decide scan and advertise policy | Constants in `Config.kt` (BALANCED now; LOW_LATENCY for lab tests). Trade-off noted: faster discovery costs battery. | S |
| 1.3 | Set measurable targets | After the first soak test, record a baseline (battery per hour, hop time) and set targets from real numbers, not guesses. | S |

### 2.0 Platform compliance

| ID | Work package | Deliverable and acceptance | Size |
| --- | --- | --- | --- |
| 2.1 | Manifest permissions | `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_CONNECTED_DEVICE`, `POST_NOTIFICATIONS`, `RECEIVE_BOOT_COMPLETED`, `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` added. | S |
| 2.2 | Service declaration | `<service>` with `foregroundServiceType="connectedDevice"`. No `MissingForegroundServiceTypeException` on start. | S |
| 2.3 | Runtime permission flow | One prompt for Bluetooth plus notifications. Service never starts without Bluetooth permissions. | S |
| 2.4 | Policy review (later) | Notes on Google Play requirements for foreground service declarations and battery-optimization exemptions, before any store release. | S |

### 3.0 Service architecture

| ID | Work package | Deliverable and acceptance | Size |
| --- | --- | --- | --- |
| 3.1 | `MeshRepository` | Shared state (log lines, alert text, running flag) the screen observes. | S |
| 3.2 | `MeshService` | Foreground service that owns the `Mesh`, handles START / SEED / STOP commands, returns `START_STICKY`. | M |
| 3.3 | Refactor `Mesh` | Takes an `AlertStore`, no longer owned by the screen. All MVP tests still pass. | M |
| 3.4 | Refactor `MainActivity` | Screen only sends commands and shows state. Closing the app does not stop the mesh. | S |

### 4.0 Persistence and time handling

| ID | Work package | Deliverable and acceptance | Size |
| --- | --- | --- | --- |
| 4.1 | `AlertStore` | Saves the raw signed packet. On restart it is **re-verified** before use, so tampering or expiry is caught. | S |
| 4.2 | Restore on start | Service restart resumes advertising a still-valid alert. | S |
| 4.3 | Wall-clock expiry | Expiry checked on every scan result, every read request and every minute. Expired alerts are never served. | M |

### 5.0 Resilience

| ID | Work package | Deliverable and acceptance | Size |
| --- | --- | --- | --- |
| 5.1 | Bluetooth off / on handling | Receiver pauses the mesh when Bluetooth turns off and resumes when it turns back on, without opening the app. | M |
| 5.2 | Periodic scan restart | Scan restarted every 10 minutes (well inside Android's start-rate limit) to recover from silently stalled scans. | S |
| 5.3 | Sticky restart | If Android kills the service, it asks to restart it. Behavior verified on the test phone. | S |
| 5.4 | Boot auto-start (best effort) | After reboot the service starts if the user had it enabled and permissions exist. Failures are swallowed safely. | M |
| 5.5 | Stop control | Notification has a **Stop** button. User can always switch it off. | S |

### 6.0 Power management

| ID | Work package | Deliverable and acceptance | Size |
| --- | --- | --- | --- |
| 6.1 | Balanced scan and advertise | BALANCED modes in use. Discovery latency compared with LOW_LATENCY and written down. | S |
| 6.2 | Battery exemption prompt | Button opens Android's exemption dialog. Status visible in the log. | S |
| 6.3 | Battery measurement | Overnight drain recorded with the service on versus off. | M |
| 6.4 | Wake lock decision | Decision recorded: none by default, add one only if tests show missed events. | S |

### 7.0 Notifications and user experience

| ID | Work package | Deliverable and acceptance | Size |
| --- | --- | --- | --- |
| 7.1 | Two channels | "Mesh status" (quiet) and "Emergency alerts" (high importance). | S |
| 7.2 | Status notification | Permanent "BlackoutMesh is active" with Stop action, tap opens the app. | S |
| 7.3 | Alert notification | Pops up when a **new** alert is accepted, even with the screen off. Same alert never notifies twice. | S |
| 7.4 | Onboarding hints | Button for battery exemption. Notes (Section 6) for phone-maker settings. | S |

### 8.0 Observability

| ID | Work package | Deliverable and acceptance | Size |
| --- | --- | --- | --- |
| 8.1 | Log file | Every log line is timestamped and appended to `mesh.log` (size capped). Readable in the morning. | S |
| 8.2 | Heartbeat | One line every 15 minutes: "Heartbeat: scanning, alert=...". Gaps in the log reveal when the service slept or died. | S |

### 9.0 Testing

| ID | Work package | Deliverable and acceptance | Size |
| --- | --- | --- | --- |
| 9.1 | Regression | MVP Tests 1 to 3 still pass with the service. | S |
| 9.2 | Lifecycle tests | Screen off, swipe from recents, Bluetooth off/on, airplane mode, battery saver, reinstall, revoked permission (Section 7). | M |
| 9.3 | Doze test | Forced idle mode test with `adb` (Section 7). | S |
| 9.4 | Overnight soak | 8+ hours screen off, heartbeat log complete, morning "wake from silence" test passes. | L |
| 9.5 | Device matrix | Repeat 9.2 and 9.4 on at least two phone brands when you can borrow them. | L |

### 10.0 Documentation and handover

| ID | Work package | Deliverable and acceptance | Size |
| --- | --- | --- | --- |
| 10.1 | Tester sheet | One page: install, permissions, battery setting, how to read `mesh.log`. | S |
| 10.2 | Known limits list | Honest list of what does not work yet (Section 9). | S |

### 11.0 Deferred hardening (not in this build)

Scan with a system `PendingIntent` so Android can wake the app on a beacon, exact-alarm expiry, Trickle-style backoff, hop limit and update/all-clear messages, `CompanionDeviceManager`, and iOS parity.

### Order of work

```
Phase A (build, about half a day):   2.x > 3.1 > 4.1 > 3.3 > 3.2 > 7.x > 3.4 > 5.x
Phase B (verify, same day):          9.1 > 9.2 > 9.3
Phase C (overnight, 1 to 2 nights):  8.x running > 9.4 > 6.3
Phase D (when possible):             9.5 > 10.x > decide on section 11
```

---

## 4. Conversion steps

Do these in order. After each step the project should still build.

### Step 0: Back up the working MVP

Copy the whole project folder somewhere safe (or use git: `git init`, `git add .`, `git commit -m "working MVP"`). If anything goes wrong you can return to a phone-to-laptop transfer that works.

### Step 1: Manifest

In `app/src/main/AndroidManifest.xml`, keep your existing Bluetooth lines and add these **above** `<application>`:

```xml
<uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
<uses-permission android:name="android.permission.FOREGROUND_SERVICE_CONNECTED_DEVICE" />
<uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
<uses-permission android:name="android.permission.RECEIVE_BOOT_COMPLETED" />
<uses-permission android:name="android.permission.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS" />
```

Inside `<application ...>`, **below** the existing `<activity>` block, add:

```xml
<service
    android:name=".MeshService"
    android:exported="false"
    android:foregroundServiceType="connectedDevice" />

<receiver
    android:name=".BootReceiver"
    android:exported="true">
    <intent-filter>
        <action android:name="android.intent.action.BOOT_COMPLETED" />
    </intent-filter>
</receiver>
```

### Step 2: `Config.kt` additions

Add these two imports at the top of the file:

```kotlin
import android.bluetooth.le.AdvertiseSettings
import android.bluetooth.le.ScanSettings
```

Add these two lines inside `object Config`. **Keep your pasted `PUBLIC_KEY_HEX` and `SEED_PACKET_HEX` as they are.**

```kotlin
    // Always-on tuning. BALANCED saves battery. Use the LOW_LATENCY versions for quick lab tests.
    const val SCAN_MODE = ScanSettings.SCAN_MODE_BALANCED
    const val ADVERTISE_MODE = AdvertiseSettings.ADVERTISE_MODE_BALANCED
```

### Step 3: Four small new files

Create each in the same package folder as your other files (right-click the folder > New > Kotlin Class/File).

#### 3a. `MeshRepository.kt`

Shared state, plus a timestamped log that is also saved to a file.

```kotlin
package com.example.blackoutmesh

import kotlinx.coroutines.flow.MutableStateFlow
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

object MeshRepository {
    val logs = MutableStateFlow<List<String>>(emptyList())
    val alertText = MutableStateFlow<String?>(null)
    val running = MutableStateFlow(false)

    private var logFile: File? = null
    private val timeFormat = SimpleDateFormat("MM-dd HH:mm:ss", Locale.US)

    fun attachLogFile(file: File) {
        logFile = file
    }

    /** Always call on the main thread (Mesh already does). */
    fun log(message: String) {
        val line = "${timeFormat.format(Date())}  $message"
        logs.value = (listOf(line) + logs.value).take(200)
        runCatching {
            val f = logFile ?: return@runCatching
            if (f.length() > 512_000) f.writeText("")   // simple size cap
            f.appendText(line + "\n")
        }
    }
}
```

> `kotlinx.coroutines` comes with Compose projects. If Android Studio cannot find it, add `implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")` to the `dependencies { }` block and sync.

#### 3b. `AlertStore.kt`

Remembers the alert (and a few flags) on disk.

```kotlin
package com.example.blackoutmesh

import android.content.Context

class AlertStore(context: Context) {
    private val prefs = context.getSharedPreferences("blackoutmesh", Context.MODE_PRIVATE)

    // The signed packet. It is re-verified every time it is loaded.
    fun save(raw: ByteArray) {
        prefs.edit().putString("alert", raw.joinToString("") { "%02x".format(it) }).apply()
    }

    fun load(): ByteArray? =
        prefs.getString("alert", null)?.let { runCatching { AlertCodec.hex(it) }.getOrNull() }

    fun clear() {
        prefs.edit().remove("alert").apply()
    }

    // Did the user want the service on? Used after a reboot.
    fun setEnabled(on: Boolean) {
        prefs.edit().putBoolean("enabled", on).apply()
    }

    fun isEnabled(): Boolean = prefs.getBoolean("enabled", false)

    // So the same alert never pops up twice after a restart.
    fun lastNotified(): Long = prefs.getLong("notified", -1L)

    fun setLastNotified(id: Long) {
        prefs.edit().putLong("notified", id).apply()
    }
}
```

#### 3c. `Permissions.kt`

```kotlin
package com.example.blackoutmesh

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build

object Permissions {
    private val bluetooth = listOf(
        Manifest.permission.BLUETOOTH_SCAN,
        Manifest.permission.BLUETOOTH_ADVERTISE,
        Manifest.permission.BLUETOOTH_CONNECT
    )

    /** Everything we ask for in one prompt. */
    fun required(): Array<String> = buildList {
        addAll(bluetooth)
        if (Build.VERSION.SDK_INT >= 33) add(Manifest.permission.POST_NOTIFICATIONS)
    }.toTypedArray()

    /** The service can only start if all three Bluetooth permissions are granted. */
    fun hasBluetooth(context: Context): Boolean =
        bluetooth.all { context.checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED }
}
```

#### 3d. `Notifications.kt`

```kotlin
package com.example.blackoutmesh

import android.Manifest
import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat

object Notifications {
    private const val CH_SERVICE = "mesh_service"
    private const val CH_ALERT = "mesh_alert"
    const val ID_SERVICE = 1
    private const val ID_ALERT = 2

    fun createChannels(context: Context) {
        val nm = context.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CH_SERVICE, "Mesh status", NotificationManager.IMPORTANCE_LOW)
        )
        nm.createNotificationChannel(
            NotificationChannel(CH_ALERT, "Emergency alerts", NotificationManager.IMPORTANCE_HIGH)
        )
    }

    private fun openAppIntent(context: Context): PendingIntent =
        PendingIntent.getActivity(
            context, 0,
            Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

    /** The permanent notification that keeps the service alive. */
    fun serviceNotification(context: Context): Notification {
        val stop = PendingIntent.getService(
            context, 1,
            Intent(context, MeshService::class.java).setAction(MeshService.ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        return NotificationCompat.Builder(context, CH_SERVICE)
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setContentTitle("BlackoutMesh is active")
            .setContentText("Listening for emergency alerts")
            .setOngoing(true)
            .setContentIntent(openAppIntent(context))
            .addAction(0, "Stop", stop)
            .build()
    }

    /** Pops up when a new alert arrives, even with the screen off. */
    @SuppressLint("MissingPermission")
    fun showAlert(context: Context, text: String) {
        if (Build.VERSION.SDK_INT >= 33 &&
            context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) return

        val notification = NotificationCompat.Builder(context, CH_ALERT)
            .setSmallIcon(android.R.drawable.ic_dialog_alert)
            .setContentTitle("Emergency alert")
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setAutoCancel(true)
            .setContentIntent(openAppIntent(context))
            .build()
        NotificationManagerCompat.from(context).notify(ID_ALERT, notification)
    }
}
```

### Step 4: Replace `Mesh.kt` completely

This is your existing class with these changes (marked `// CHANGED` or `// NEW` in the code):

- Takes an `AlertStore` and saves, restores and clears the alert.
- Expiry uses the real clock and runs on every event plus a one-minute tick (the old one-shot timer would drift while the phone sleeps).
- Serves only unexpired alerts.
- Restarts the scan every 10 minutes and writes a heartbeat every 15.
- Uses the scan and advertise modes from `Config`.

```kotlin
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
    private val store: AlertStore,                       // CHANGED: new parameter
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
    private var ticks = 0                                // NEW
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
        restoreSavedAlert()                              // NEW
        handler.removeCallbacks(expiryTick)              // NEW
        handler.postDelayed(expiryTick, 60_000)          // NEW
        log("Mesh started: listening for alerts")
    }

    fun stop() {
        if (!running) return
        running = false
        stopScan()
        stopAdvertising()
        gattServer?.close()
        gattServer = null
        handler.removeCallbacks(expiryTick)              // CHANGED
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
            if (cur != null && (alert.msgId == cur.msgId || alert.issuedAt <= cur.issuedAt)) {
                log("Already have this alert (or a newer one)")
                return@post
            }
            current = alert
            store.save(alert.raw)                        // NEW
            log("ACCEPTED alert ${alert.msgId} ($source)")
            onAlert(alert)
            startAdvertising()
        }
    }

    // NEW: after a restart, pick up where we left off. Re-verified, so tampering or expiry is caught.
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

    // NEW: real-clock expiry (timers pause in deep sleep, so we also check on every event)
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

    // NEW: once a minute: expiry check, scan restart every 10 min, heartbeat every 15 min
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
            // CHANGED: never serve an expired alert
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
            .setAdvertiseMode(Config.ADVERTISE_MODE)     // CHANGED
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
    // The listening: scanning for other phones' beacons
    // ------------------------------------------------------------------

    private val scanCallback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) {
            expireIfNeeded()                             // NEW
            maybeFetch(result.device, result.rssi)
        }

        override fun onScanFailed(errorCode: Int) {
            log("Scan failed, code $errorCode")
        }
    }

    private fun startScan() {
        val scanner = adapter?.bluetoothLeScanner ?: return
        val filters = listOf(
            ScanFilter.Builder().setServiceUuid(ParcelUuid(Config.SERVICE_UUID)).build()
        )
        val settings = ScanSettings.Builder()
            .setScanMode(Config.SCAN_MODE)               // CHANGED
            .build()
        scanner.startScan(filters, settings, scanCallback)
    }

    private fun stopScan() {
        adapter?.bluetoothLeScanner?.stopScan(scanCallback)
    }

    private fun restartScan() {                          // NEW
        stopScan()
        startScan()
    }

    // ------------------------------------------------------------------
    // The walk-up-and-read: GATT client
    // ------------------------------------------------------------------

    private fun maybeFetch(device: BluetoothDevice, rssi: Int) {
        if (busy) return
        val now = SystemClock.elapsedRealtime()
        val last = lastAttempt[device.address] ?: 0L
        if (now - last < Config.REFETCH_COOLDOWN_MS) return

        lastAttempt[device.address] = now
        busy = true
        log("Heard beacon from ${device.address} (signal $rssi dBm), connecting")
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

    private val clientCallback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) {
            if (newState == BluetoothProfile.STATE_CONNECTED && status == BluetoothGatt.GATT_SUCCESS) {
                gatt.requestMtu(Config.MTU)
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED || status != BluetoothGatt.GATT_SUCCESS) {
                if (status != BluetoothGatt.GATT_SUCCESS) log("Connection problem, status $status")
                endFetch()
            }
        }

        override fun onMtuChanged(gatt: BluetoothGatt, mtu: Int, status: Int) {
            gatt.discoverServices()
        }

        override fun onServicesDiscovered(gatt: BluetoothGatt, status: Int) {
            val characteristic = gatt.getService(Config.SERVICE_UUID)
                ?.getCharacteristic(Config.ALERT_CHAR_UUID)
            if (characteristic == null) {
                log("Alert slot not found on that phone")
                endFetch()
                return
            }
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
```

### Step 5: Create `MeshService.kt` (the heart of this guide)

```kotlin
package com.example.blackoutmesh

import android.app.Service
import android.bluetooth.BluetoothAdapter
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.content.ContextCompat
import java.io.File

class MeshService : Service() {

    companion object {
        const val ACTION_SEED = "com.example.blackoutmesh.SEED"
        const val ACTION_STOP = "com.example.blackoutmesh.STOP"
    }

    private var mesh: Mesh? = null
    private lateinit var store: AlertStore

    override fun onBind(intent: Intent?): IBinder? = null

    // Pause when Bluetooth is switched off, resume when it comes back
    private val btReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            when (intent.getIntExtra(BluetoothAdapter.EXTRA_STATE, -1)) {
                BluetoothAdapter.STATE_OFF -> {
                    MeshRepository.log("Bluetooth turned off, pausing mesh")
                    mesh?.stop()
                }
                BluetoothAdapter.STATE_ON -> {
                    MeshRepository.log("Bluetooth turned on, resuming mesh")
                    Handler(Looper.getMainLooper()).postDelayed({ mesh?.start() }, 1000)
                }
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        store = AlertStore(applicationContext)
        MeshRepository.attachLogFile(File(filesDir, "mesh.log"))
        Notifications.createChannels(this)

        // Android must see a foreground notification within a few seconds
        try {
            startForeground(
                Notifications.ID_SERVICE,
                Notifications.serviceNotification(this),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE
            )
        } catch (e: Exception) {
            MeshRepository.log("Could not start foreground service: ${e.message}")
            stopSelf()
            return
        }

        ContextCompat.registerReceiver(
            this, btReceiver,
            IntentFilter(BluetoothAdapter.ACTION_STATE_CHANGED),
            ContextCompat.RECEIVER_NOT_EXPORTED
        )

        mesh = Mesh(
            context = applicationContext,
            store = store,
            onLog = { MeshRepository.log(it) },
            onAlert = { alert ->
                MeshRepository.alertText.value = alert?.let { Codebook.render(it) }
                // Notify once per alert, even across restarts
                if (alert != null && store.lastNotified() != alert.msgId) {
                    store.setLastNotified(alert.msgId)
                    Notifications.showAlert(applicationContext, Codebook.render(alert))
                }
            }
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val m = mesh ?: return START_NOT_STICKY

        when (intent?.action) {
            ACTION_STOP -> {
                store.setEnabled(false)
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_SEED -> m.seedWithTestAlert()
            else -> m.start()          // also runs when Android restarts us (intent is null)
        }

        MeshRepository.running.value = true
        return START_STICKY
    }

    override fun onDestroy() {
        runCatching { unregisterReceiver(btReceiver) }
        mesh?.stop()
        mesh = null
        MeshRepository.running.value = false
        super.onDestroy()
    }
}
```

### Step 6: Create `BootReceiver.kt` (best effort restart after reboot)

```kotlin
package com.example.blackoutmesh

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        if (!AlertStore(context).isEnabled()) return        // user had it switched off
        if (!Permissions.hasBluetooth(context)) return      // would crash without these

        try {
            ContextCompat.startForegroundService(
                context, Intent(context, MeshService::class.java)
            )
        } catch (e: Exception) {
            // Some phones or Android versions block this. Opening the app once restarts it.
        }
    }
}
```

### Step 7: Replace `MainActivity.kt` completely

The screen now only sends commands and shows what the service reports. It no longer stops the mesh when closed, and no longer keeps the screen awake (so you can truly test with the screen off).

```kotlin
package com.example.blackoutmesh

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat

class MainActivity : ComponentActivity() {

    private var pendingAction: (() -> Unit)? = null

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) {
        if (Permissions.hasBluetooth(this)) {
            pendingAction?.invoke()
        } else {
            MeshRepository.log("Bluetooth permissions were denied. The service cannot run without them.")
        }
        pendingAction = null
    }

    /** Runs the action once Bluetooth permissions are granted (asks if needed). */
    private fun withPermissions(action: () -> Unit) {
        if (Permissions.hasBluetooth(this)) {
            action()
            return
        }
        pendingAction = action
        permissionLauncher.launch(Permissions.required())
    }

    private fun startMeshService(action: String? = null) {
        AlertStore(this).setEnabled(true)
        val intent = Intent(this, MeshService::class.java).setAction(action)
        ContextCompat.startForegroundService(this, intent)
    }

    private fun stopMeshService() {
        AlertStore(this).setEnabled(false)
        stopService(Intent(this, MeshService::class.java))
    }

    private fun requestBatteryExemption() {
        val pm = getSystemService(PowerManager::class.java)
        if (pm.isIgnoringBatteryOptimizations(packageName)) {
            MeshRepository.log("Battery optimization is already off for this app")
            return
        }
        startActivity(
            Intent(
                Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                Uri.parse("package:$packageName")
            )
        )
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme { Screen() }
        }
    }

    @Composable
    private fun Screen() {
        val logs by MeshRepository.logs.collectAsState()
        val alertText by MeshRepository.alertText.collectAsState()
        val running by MeshRepository.running.collectAsState()

        Column(
            modifier = Modifier.fillMaxSize().padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Text("BlackoutMesh", style = MaterialTheme.typography.headlineSmall)
            Text(
                if (running) "Service: RUNNING" else "Service: STOPPED",
                style = MaterialTheme.typography.titleSmall
            )

            Card(modifier = Modifier.fillMaxWidth()) {
                Text(
                    text = alertText ?: "No active alert",
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.padding(16.dp)
                )
            }

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { withPermissions { startMeshService() } }) { Text("Start") }
                Button(onClick = {
                    withPermissions { startMeshService(MeshService.ACTION_SEED) }
                }) { Text("Test alert") }
                OutlinedButton(onClick = { stopMeshService() }) { Text("Stop") }
            }

            OutlinedButton(onClick = { requestBatteryExemption() }) {
                Text("Allow unrestricted battery")
            }

            LazyColumn {
                items(logs) { line ->
                    Text(line, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
    }
}
```

### Step 8: Build and run

1. Click **Run**. If the build fails, check Section 8.
2. Tap **Start** and accept the prompts (Nearby devices, Notifications).
3. Look for a permanent **"BlackoutMesh is active"** notification and `Service: RUNNING` on screen.
4. Tap **Allow unrestricted battery** and accept.
5. Press the phone's **Home** button. The notification must stay.

### Step 9: Phone settings that decide whether it survives

Do this on the actual test phone. These are settings, not code.

- **Battery:** Settings > Apps > BlackoutMesh > Battery > **Unrestricted** (wording varies).
- **Samsung:** Battery > Background usage limits: make sure BlackoutMesh is **not** in "Sleeping apps" or "Deep sleeping apps".
- **Xiaomi, Huawei, OnePlus, Oppo:** enable **Autostart** for the app and lock it in the recent-apps list if offered.
- **Notifications:** keep both channels enabled.
- Reference for per-brand steps: dontkillmyapp.com.

---

## 5. Quick verification (about 15 minutes)

| # | Do this | Pass looks like |
| --- | --- | --- |
| 1 | Tap Start, press Home, lock the screen, wait 5 minutes | Notification still there. Log has no "Mesh stopped" |
| 2 | Swipe the app away from recent apps | Notification stays. Reopen the app: `Service: RUNNING` |
| 3 | Laptop sends an alert while the **screen is off** | Heads-up **Emergency alert** notification appears |
| 4 | Turn Bluetooth off, wait 10 s, turn it on | Log: "Bluetooth turned off, pausing mesh", then "turned on, resuming mesh". Next laptop alert is received |
| 5 | Receive an alert, tap **Stop**, then **Start** | Log: "Restored saved alert ...". iPhone nRF Connect can still read the same 79 bytes |
| 6 | Tamper test (MVP Test 3) | Still shows `REJECTED` |
| 7 | Tap the notification's **Stop** button | Notification disappears, screen shows `Service: STOPPED` |

---

## 6. Test procedures

### Forcing Doze (fast, from your computer, phone connected by USB)

```
adb shell dumpsys battery unplug
adb shell dumpsys deviceidle force-idle
```

Wait a few minutes, send an alert from the laptop, then restore normal behavior:

```
adb shell dumpsys deviceidle unforce
adb shell dumpsys battery reset
```

Pass: alert still arrives, or you note exactly how long it was delayed.

### Overnight soak test

1. Charge to 100%, unplug, note the time and battery level.
2. Start the service, tap **Allow unrestricted battery** once, lock the phone, leave it still.
3. Optional but useful: set a reminder to check an iPhone nRF Connect read at the 1-hour mark.
4. Next morning, **before opening the app**, send an alert from the laptop. Pass: the alert notification appears on its own ("wake from silence").
5. Read the log: Android Studio > View > Tool Windows > **Device Explorer** > `data/data/com.example.blackoutmesh/files/mesh.log`.
6. Pass: a `Heartbeat` line roughly every 15 minutes with no long gaps. Record battery used overnight.
7. Repeat with the service **stopped** to get a baseline, so you know what the service itself costs.

### Lifecycle checklist (record pass or fail per phone brand)

Screen off 10 min, swipe from recents, Bluetooth off and on, airplane mode on and off, battery saver on, reboot (auto-start), app reinstall, permission revoked while running, alert expiring while the phone sleeps.

---

## 7. What to write down (feeds the simulation)

- Hours the service ran without gaps
- Battery percent per hour, service on versus off
- Time from "laptop sends" to "notification shown" with screen off
- Any heartbeat gaps and what was happening then
- Phone model and Android version

---

## 8. Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| `MissingForegroundServiceTypeException` | Manifest `<service>` is missing `foregroundServiceType="connectedDevice"` |
| `SecurityException` mentioning foreground service type | Bluetooth permissions not granted before the service started. Use the Start button flow |
| `ForegroundServiceStartNotAllowedException` | Service was started from the background. Start it from the visible app. For reboot start, see Step 9 |
| `Unresolved reference: MutableStateFlow` | Add the coroutines dependency noted under Step 3a |
| No notification appears | Notification permission denied. Settings > Apps > BlackoutMesh > Notifications |
| Alert notification is silent, not a pop-up | Channel importance was lowered. Settings > Apps > BlackoutMesh > Notifications > Emergency alerts > set to Urgent/High |
| Service dies when the screen turns off | Battery restriction or phone-maker killer. Redo Step 9 |
| Heartbeat gaps of hours | Doze or phone-maker killer. Note the brand, battery settings, and repeat with LOW_LATENCY modes in `Config.kt` to compare |
| `Scan failed, code 6` | Scan started too often. Do not restart scans more often than the 10-minute tick |
| `Scan failed, code 2` | Toggle Bluetooth off and on |
| Nothing after reboot | Auto-start blocked by the phone. Open the app once. Check the phone's Autostart setting |
| Same alert notifies repeatedly | `AlertStore.lastNotified` not saving. Check that `AlertStore` is the one used in `MeshService` |

---

## 9. Known limits of this build

- Reboot and auto-start are best effort and depend on the phone maker.
- Doze and aggressive battery managers can still delay or block it. The soak test tells you how bad that is on your phone.
- Expiry checks run on events and once a minute while the phone is awake. A phone in deep sleep can briefly advertise an alert just past its expiry, but it will refuse to serve it.
- Alerts are English only, one alert at a time, no hop limit or update messages yet.
- The battery exemption button is fine for testing. Google Play restricts it, so review before any store release.
- iPhone behavior is different and not covered here.

---

## 10. After this works, in this order

1. Run the overnight soak on a second phone brand.
2. Trickle-style backoff so beacons slow down when nothing changes.
3. Scan with a system `PendingIntent`, so Android can wake the app for a beacon even if the process was killed.
4. Exact-alarm expiry.
5. Hop limit and update/all-clear messages.
6. Server connection so online phones fetch alerts and seed the mesh.