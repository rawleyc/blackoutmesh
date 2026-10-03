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
        const val ACTION_CLEAR = "com.example.blackoutmesh.CLEAR"
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
            ACTION_CLEAR -> m.clearCurrentAlert()
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
