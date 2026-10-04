package com.example.blackoutmesh

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action
        if (action != Intent.ACTION_BOOT_COMPLETED &&
            action != Intent.ACTION_MY_PACKAGE_REPLACED &&
            action != "android.intent.action.QUICKBOOT_POWERON"
        ) return

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
