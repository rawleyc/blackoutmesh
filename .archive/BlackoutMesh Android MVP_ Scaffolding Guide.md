# BlackoutMesh Android MVP: Scaffolding Guide

A step-by-step guide to build the smallest possible client app that proves the core idea: **one phone shouts a tiny beacon, another phone walks up, fetches the full signed alert, verifies the stamp, shows the message, and starts shouting the beacon itself.**

Estimated time for a first working build: a few hours, most of it Android Studio setup.

---

## 0. Scope: what the MVP is (and is not)

**In scope**

- One screen with three buttons: **Start**, **Load test alert**, **Stop**, plus a live log and the alert text.
- BLE beacon (advertise a service UUID) and scanning for that UUID.
- GATT fetch: connect, read the alert, disconnect.
- Ed25519 signature check against a public key built into the app.
- Message codebook: the packet carries a template number and a parameter, the app builds the sentence.
- Expiry: alerts stop being shown and forwarded when their validity ends.
- Re-advertise any verified alert so the next phone can fetch it.

**Out of scope on purpose (do not add these yet)**

Background or foreground service, notifications, maps, iOS, Trickle backoff, hop limit (TTL), multiple alerts at once, saving across app restarts, internet or server connection, codebook updates, multiple languages, UI polish.

**Known MVP limit:** it only works while the app is open and the screen is on. The app keeps the screen awake for you during tests. Background operation is the first thing to add after the MVP works.

---

## 1. Prerequisites

| Item | Notes |
| --- | --- |
| A computer with Windows, Linux or macOS | Android Studio runs on all three |
| Android Studio (latest stable) | Free, from developer.android.com/studio |
| Python 3 with `pip` | Only for the one script that creates test alerts |
| An Android phone, **Android 12 or newer** | Settings > About phone > Android version. If yours is older, tell me and we will adjust the permissions |
| USB cable | For installing the app on the phone |
| Your iPhone with the free **nRF Connect** app | Used as a test receiver in Test 1 |

The Android Studio emulator does **not** support Bluetooth LE. You must use a real phone.

---

## 2. Create the project

1. Open Android Studio > **New Project** > **Empty Activity** (the Compose one).
2. Settings:
   - Name: `BlackoutMesh`
   - Package name: `com.example.blackoutmesh`
   - Language: **Kotlin**
   - Minimum SDK: **API 31 (Android 12)**
3. Click **Finish** and wait for the first Gradle sync to complete.

> If you choose a different package name, change the first line (`package ...`) of every Kotlin file below to match.

### Enable your phone for testing

1. On the phone: Settings > About phone > tap **Build number** 7 times.
2. Settings > System > Developer options > turn on **USB debugging**.
3. Plug the phone into the computer, accept the "Allow USB debugging" prompt.
4. Your phone should appear in the device dropdown at the top of Android Studio.

---

## 3. Gradle and manifest changes

### 3.1 `app/build.gradle.kts` (the one marked "Module :app")

Add the crypto library inside the existing `dependencies { }` block:

```kotlin
implementation("org.bouncycastle:bcprov-jdk18on:1.78.1")
```

Inside the `android { }` block, find the existing `packaging { resources { excludes += ... } }` section and add one more exclude. It should look like this:

```kotlin
packaging {
    resources {
        excludes += "/META-INF/{AL2.0,LGPL2.1}"
        excludes += "META-INF/versions/9/OSGI-INF/MANIFEST.MF"
    }
}
```

Click **Sync Now** when Android Studio asks.

### 3.2 `app/src/main/AndroidManifest.xml`

Add these lines **above** the `<application ...>` tag:

```xml
<uses-feature android:name="android.hardware.bluetooth_le" android:required="true" />

<uses-permission
    android:name="android.permission.BLUETOOTH_SCAN"
    android:usesPermissionFlags="neverForLocation" />
<uses-permission android:name="android.permission.BLUETOOTH_ADVERTISE" />
<uses-permission android:name="android.permission.BLUETOOTH_CONNECT" />
```

---

## 4. Create a test key and a signed test alert (Python)

This script plays the role of the authority server for now. Save it as `make_packet.py` in any folder.

```python
"""
Creates a signed BlackoutMesh test alert (79 bytes) and prints the two values
you paste into Config.kt.

Usage:
    pip install cryptography
    python make_packet.py --template 1 --param 2 --minutes 120

Packet layout (big-endian):
    version(1) msg_id(4) issued_at(4) valid_minutes(2) template(2) param(2)  = 15 bytes
    signature(64)                                                           = 64 bytes

TEST KEY ONLY. The private key is saved to authority.key next to this script.
Never reuse it for anything real.
"""
import argparse
import os
import random
import struct
import time

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat, PublicFormat,
)

KEY_FILE = "authority.key"

parser = argparse.ArgumentParser()
parser.add_argument("--template", type=int, default=1)
parser.add_argument("--param", type=int, default=2)
parser.add_argument("--minutes", type=int, default=120)
args = parser.parse_args()

if os.path.exists(KEY_FILE):
    with open(KEY_FILE, "rb") as f:
        priv = ed25519.Ed25519PrivateKey.from_private_bytes(f.read())
else:
    priv = ed25519.Ed25519PrivateKey.generate()
    with open(KEY_FILE, "wb") as f:
        f.write(priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
    print(f"(new test key saved to {KEY_FILE})")

pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

payload = struct.pack(
    ">BIIHHH",
    1,                          # version
    random.getrandbits(32),     # msg_id
    int(time.time()),           # issued_at (unix seconds)
    args.minutes,               # valid_minutes
    args.template,
    args.param,
)
packet = payload + priv.sign(payload)

print()
print("Paste these into Config.kt:")
print()
print(f'const val PUBLIC_KEY_HEX = "{pub.hex()}"')
print(f'const val SEED_PACKET_HEX = "{packet.hex()}"')
print()
print(f"(packet is {len(packet)} bytes, valid for {args.minutes} min)")
```

Run it:

```
pip install cryptography
python make_packet.py --template 1 --param 2 --minutes 120
```

You will see two lines starting with `const val`. Keep them, you paste them into `Config.kt` in the next step.

**Good to know**

- The private key is saved once in `authority.key`, so the public key stays the same every time you run the script. You paste the public key into the app **once**.
- The alert expires after `--minutes`. When it does, run the script again and paste only the new `SEED_PACKET_HEX`.
- Template and parameter numbers (defined in `Alert.kt` below):

| template | Message |
| --- | --- |
| 1 | "Evacuate now. Go to *(place)*." |
| 2 | "Shelter in place. Stay indoors and close windows." |
| 3 | "All clear. The emergency has ended." |

| param | Place (used by template 1) |
| --- | --- |
| 1 | Town Hall |
| 2 | TAURON Arena, Gate 3 |
| 3 | Main Station |

---

## 5. Add the code

You will create four Kotlin files in `app/src/main/java/com/example/blackoutmesh/`. The template already created `MainActivity.kt`, so replace its entire contents.

To add a new file: right-click the package folder > **New** > **Kotlin Class/File** > name it.

### 5.1 `Config.kt`

```kotlin
package com.example.blackoutmesh

import java.util.UUID

object Config {
    // Our private "channel" on Bluetooth. Both phones must use the same UUIDs.
    val SERVICE_UUID: UUID = UUID.fromString("6e0b1a10-7b1d-4a52-9c1e-5a6f0a1d0001")
    val ALERT_CHAR_UUID: UUID = UUID.fromString("6e0b1a10-7b1d-4a52-9c1e-5a6f0a1d0002")

    // Paste the two lines printed by make_packet.py
    const val PUBLIC_KEY_HEX = "PASTE_PUBLIC_KEY_HERE"
    const val SEED_PACKET_HEX = "PASTE_PACKET_HERE"

    // Do not reconnect to the same phone more often than this
    const val REFETCH_COOLDOWN_MS = 30_000L

    // Ask for a bigger Bluetooth message size so the 79-byte packet fits in one read
    const val MTU = 185

    // Give up on a fetch that takes longer than this
    const val FETCH_TIMEOUT_MS = 10_000L

    val PUBLIC_KEY: ByteArray get() = AlertCodec.hex(PUBLIC_KEY_HEX)
}
```

### 5.2 `Alert.kt`

Parsing, signature check, and the message codebook, all in one file.

```kotlin
package com.example.blackoutmesh

import org.bouncycastle.crypto.params.Ed25519PublicKeyParameters
import org.bouncycastle.crypto.signers.Ed25519Signer
import java.nio.ByteBuffer
import java.nio.ByteOrder

data class Alert(
    val msgId: Long,
    val issuedAt: Long,      // unix seconds
    val validMinutes: Int,
    val template: Int,
    val param: Int,
    val raw: ByteArray       // the exact 79 bytes, forwarded unchanged
)

object AlertCodec {
    private const val PAYLOAD_LEN = 15
    private const val SIG_LEN = 64

    fun hex(s: String): ByteArray =
        s.chunked(2).map { it.toInt(16).toByte() }.toByteArray()

    /** Returns the alert if the packet is well formed, signed by our authority, and not expired. */
    fun verifyAndParse(
        raw: ByteArray,
        nowSec: Long = System.currentTimeMillis() / 1000
    ): Alert? {
        if (raw.size != PAYLOAD_LEN + SIG_LEN) return null

        val payload = raw.copyOfRange(0, PAYLOAD_LEN)
        val sig = raw.copyOfRange(PAYLOAD_LEN, raw.size)
        if (!signatureOk(payload, sig)) return null

        val b = ByteBuffer.wrap(payload).order(ByteOrder.BIG_ENDIAN)
        val version = b.get().toInt() and 0xFF
        if (version != 1) return null

        val msgId = b.int.toLong() and 0xFFFFFFFFL
        val issuedAt = b.int.toLong() and 0xFFFFFFFFL
        val valid = b.short.toInt() and 0xFFFF
        val template = b.short.toInt() and 0xFFFF
        val param = b.short.toInt() and 0xFFFF

        if (nowSec > issuedAt + valid * 60L) return null   // expired

        return Alert(msgId, issuedAt, valid, template, param, raw)
    }

    private fun signatureOk(payload: ByteArray, sig: ByteArray): Boolean = try {
        val verifier = Ed25519Signer()
        verifier.init(false, Ed25519PublicKeyParameters(Config.PUBLIC_KEY, 0))
        verifier.update(payload, 0, payload.size)
        verifier.verifySignature(sig)
    } catch (e: Exception) {
        false
    }
}

/** Turns (template, param) into a sentence. Same table must exist on every phone. */
object Codebook {
    private val templates = mapOf(
        1 to "Evacuate now. Go to %s.",
        2 to "Shelter in place. Stay indoors and close windows.",
        3 to "All clear. The emergency has ended."
    )
    private val places = mapOf(
        1 to "Town Hall",
        2 to "TAURON Arena, Gate 3",
        3 to "Main Station"
    )

    fun render(a: Alert): String {
        val t = templates[a.template]
            ?: return "Official emergency alert received. Follow official channels."
        return if (t.contains("%s")) {
            t.format(places[a.param] ?: "the nearest official shelter")
        } else t
    }
}
```

### 5.3 `Mesh.kt` (the core)

This one class does everything Bluetooth: the mailbox (GATT server), the shouting (advertising), the listening (scanning), and the walk-up-and-read (GATT client).

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
            if (cur != null && (alert.msgId == cur.msgId || alert.issuedAt <= cur.issuedAt)) {
                log("Already have this alert (or a newer one)")
                return@post
            }
            current = alert
            log("ACCEPTED alert ${alert.msgId} ($source)")
            onAlert(alert)
            startAdvertising()
            scheduleExpiry(alert)
        }
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
    // The listening: scanning for other phones' beacons
    // ------------------------------------------------------------------

    private val scanCallback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) {
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
            .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
            .build()
        scanner.startScan(filters, settings, scanCallback)
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

### 5.4 `MainActivity.kt`

Replace the whole file with this:

```kotlin
package com.example.blackoutmesh

import android.Manifest
import android.os.Bundle
import android.view.WindowManager
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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

class MainActivity : ComponentActivity() {

    private lateinit var mesh: Mesh
    private val logs = mutableStateListOf<String>()
    private var alertText by mutableStateOf<String?>(null)
    private var pendingAction: (() -> Unit)? = null

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { grants ->
        if (grants.values.all { it }) {
            pendingAction?.invoke()
        } else {
            logs.add(0, "Bluetooth permissions were denied. The app cannot work without them.")
        }
        pendingAction = null
    }

    private fun withBluetoothPermissions(action: () -> Unit) {
        pendingAction = action
        permissionLauncher.launch(
            arrayOf(
                Manifest.permission.BLUETOOTH_SCAN,
                Manifest.permission.BLUETOOTH_ADVERTISE,
                Manifest.permission.BLUETOOTH_CONNECT
            )
        )
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // MVP only works in the foreground, so keep the screen awake while testing
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        mesh = Mesh(
            context = this,
            onLog = { logs.add(0, it) },
            onAlert = { alert -> alertText = alert?.let { Codebook.render(it) } }
        )

        setContent {
            MaterialTheme { Screen() }
        }
    }

    override fun onDestroy() {
        mesh.stop()
        super.onDestroy()
    }

    @Composable
    private fun Screen() {
        Column(
            modifier = Modifier.fillMaxSize().padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Text("BlackoutMesh MVP", style = MaterialTheme.typography.headlineSmall)

            Card(modifier = Modifier.fillMaxWidth()) {
                Text(
                    text = alertText ?: "No active alert",
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.padding(16.dp)
                )
            }

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { withBluetoothPermissions { mesh.start() } }) { Text("Start") }
                Button(onClick = { withBluetoothPermissions { mesh.seedWithTestAlert() } }) {
                    Text("Load test alert")
                }
                OutlinedButton(onClick = { mesh.stop() }) { Text("Stop") }
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

### 5.5 Paste your keys

Open `Config.kt` and replace the two `PASTE_...` strings with the output of `make_packet.py` (Section 4).

---

## 6. Build and run

1. Select your phone in the device dropdown.
2. Click the green **Run** button.
3. On the phone, tap **Start**, then **Allow** for the Bluetooth ("Nearby devices") permission prompt.
4. The log should say: `Mesh started: listening for alerts`.

If the build fails, see Section 9. Do not move on until the app launches on the phone.

---

## 7. Tests

### Test 1: one Android + your iPhone (proves beacon, mailbox and signature)

1. **Android:** tap **Start**, then **Load test alert**. Expected log: `ACCEPTED alert ... (seeded)` then `Beacon on: advertising our alert`. The card shows "Evacuate now. Go to TAURON Arena, Gate 3."
2. **iPhone:** open nRF Connect > **Scanner**. Find the entry advertising service `6E0B1A10-7B1D-4A52-9C1E-5A6F0A1D0001` (the name will be empty, use the filter or look at the advertising data). Tap **Connect**.
3. Expand the unknown service, tap the **read** arrow (down arrow) next to the characteristic ending `...0002`.
4. **Pass:** the value is **79 bytes**, starts with `01`, and matches the `SEED_PACKET_HEX` you pasted (without the `0x`).
5. The Android log shows `Served alert to <address>`.

### Test 2: two Androids (proves the full relay step)

Needs a second Android phone with the same app installed.

1. Phone A and phone B: tap **Start** on both.
2. Phone A: tap **Load test alert**.
3. Put the phones a meter apart and wait up to a minute.
4. **Pass on phone B:** log shows `Heard beacon from ...`, then `ACCEPTED alert ... (fetched)`, the card shows the alert text, and the log shows `Beacon on: advertising our alert`. Phone B is now a relay.

### Test 3: bad packets must be rejected

1. In `Config.kt`, change the **last character** of `SEED_PACKET_HEX` to a different hex digit and run again.
2. Tap **Start** then **Load test alert**.
3. **Pass:** log shows `REJECTED packet (bad signature, bad format, or expired)` and no alert appears.
4. Expiry check: run `python make_packet.py --minutes 1`, paste the new `SEED_PACKET_HEX`, load it, and wait 60 seconds. **Pass:** `Alert expired, stopped forwarding` and the card returns to "No active alert".

---

## 8. Definition of done (MVP)

- [ ] App installs and starts on an Android 12+ phone
- [ ] Test 1 passes (79-byte packet readable from the iPhone)
- [ ] Test 2 passes (alert hops from phone A to phone B)
- [ ] Test 3 passes (tampered and expired packets are rejected)
- [ ] You wrote down, for Test 2, roughly how many seconds the hop took and at what distance it stopped working

That last item matters most. Real numbers (time to fetch, maximum range, how often it fails) are what you will use to recalibrate the simulation.

---

## 9. Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| Gradle error mentioning `META-INF/versions/9/OSGI-INF/MANIFEST.MF` | The packaging exclude from Section 3.1 is missing or in the wrong block |
| `Unresolved reference` errors | Package line at the top of a file does not match your project's package name |
| App crashes on tap | Permissions not granted. Settings > Apps > BlackoutMesh > Permissions > allow Nearby devices |
| Log: `Bluetooth is off` | Turn Bluetooth on in phone settings |
| Log: `Advertising failed, code 1` | Data too large (should not happen with this code, check you did not add extra advertise data) |
| Log: `Advertising failed, code 2` | Too many advertisers running. Toggle Bluetooth off and on |
| Log: `Advertising failed, code 3` | Already advertising. Tap **Stop**, then **Start** |
| Log: `Advertising failed, code 5` | This phone does not support BLE advertising. Use a different phone |
| Log: `Scan failed, code 2` | Toggle Bluetooth off and on, then restart the app |
| Log: `Connection problem, status 133` | Very common generic Android Bluetooth error. Toggle Bluetooth, retry. Occasional 133s are normal, so log how often it happens |
| Phone B never hears A | Both apps must be open with screen on. Check both tapped **Start**. Move closer. Check A shows `Beacon on` |
| `REJECTED` on a fetched packet | Public key in `Config.kt` does not match `authority.key`, or the alert expired. Re-run `make_packet.py` and paste both lines |
| iPhone nRF Connect cannot find the beacon | Android must show `Beacon on`. In nRF Connect, scan for unnamed devices and check the 128-bit service UUID in the advertising data |

I could not compile this project in my own environment, so expect to fix small things (an import, a version number). Paste any compiler error back and I will correct the code.

---

## 10. What comes after the MVP

In this order, each as its own small step:

1. **Foreground service** so it keeps working with the screen off (the biggest real-world gap).
2. **Persist the current alert** across app restarts.
3. **Trickle-style backoff** so beacons slow down when nothing changes and speed up when a new phone appears.
4. **Hop limit (TTL)** and **update / all-clear** handling by sequence number.
5. **Codebook updates** and more languages.
6. **iOS client** (needs a Mac or cloud Mac, plus background BLE limits to measure).
7. **Server connection** so online phones fetch alerts from the dashboard and seed the mesh.