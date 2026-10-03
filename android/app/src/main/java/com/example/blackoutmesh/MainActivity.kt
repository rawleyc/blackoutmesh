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
        val perms = if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.S) {
            arrayOf(
                Manifest.permission.BLUETOOTH_SCAN,
                Manifest.permission.BLUETOOTH_ADVERTISE,
                Manifest.permission.BLUETOOTH_CONNECT,
                Manifest.permission.ACCESS_FINE_LOCATION
            )
        } else {
            arrayOf(
                Manifest.permission.ACCESS_FINE_LOCATION,
                Manifest.permission.ACCESS_COARSE_LOCATION
            )
        }

        // Check if GPS/Location provider is active
        try {
            val lm = getSystemService(LOCATION_SERVICE) as? android.location.LocationManager
            val isLocationOn = lm?.isProviderEnabled(android.location.LocationManager.GPS_PROVIDER) == true ||
                               lm?.isProviderEnabled(android.location.LocationManager.NETWORK_PROVIDER) == true
            if (!isLocationOn) {
                logs.add(0, "⚠️ Location (GPS) is OFF. Turn ON in Android quick settings so BLE discovery works!")
            }
        } catch (_: Exception) {}

        permissionLauncher.launch(perms)
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
                OutlinedButton(onClick = { mesh.clearCurrentAlert() }) { Text("Clear") }
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
