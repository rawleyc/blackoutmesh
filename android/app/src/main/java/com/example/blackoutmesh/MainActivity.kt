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
            Text("⚡ BlackoutMesh", style = MaterialTheme.typography.headlineSmall)
            Text(
                if (running) "Service: RUNNING (Always-On)" else "Service: STOPPED",
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
                OutlinedButton(onClick = { startMeshService(MeshService.ACTION_CLEAR) }) { Text("Clear") }
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
