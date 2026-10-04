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
import android.graphics.Color
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.os.Build
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat

object Notifications {
    private const val CH_SERVICE = "mesh_service"
    private const val CH_ALERT = "mesh_emergency_alert_v2"
    const val ID_SERVICE = 1
    private const val ID_ALERT = 2

    fun createChannels(context: Context) {
        val nm = context.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CH_SERVICE, "Mesh background service", NotificationManager.IMPORTANCE_LOW)
        )

        val soundUri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)
        val alertChannel = NotificationChannel(
            CH_ALERT,
            "Civil Defense Emergency Alerts",
            NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = "High-priority pop-up emergency warnings received over Bluetooth mesh"
            enableLights(true)
            lightColor = Color.RED
            enableVibration(true)
            vibrationPattern = longArrayOf(0, 800, 300, 800, 300, 1000)
            lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            setBypassDnd(true)
            setSound(
                soundUri,
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build()
            )
        }
        nm.createNotificationChannel(alertChannel)
    }

    private fun openAppIntent(context: Context): PendingIntent =
        PendingIntent.getActivity(
            context, 0,
            Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            },
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

    /** Pops up when a new alert arrives, even with the screen off or app closed. */
    @SuppressLint("MissingPermission")
    fun showAlert(context: Context, text: String) {
        if (Build.VERSION.SDK_INT >= 33 &&
            context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) return

        // 1. Wake screen briefly so user sees the heads-up banner if phone was asleep/locked
        try {
            val pm = context.getSystemService(Context.POWER_SERVICE) as? PowerManager
            val wakeLock = pm?.newWakeLock(
                PowerManager.SCREEN_BRIGHT_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP or PowerManager.ON_AFTER_RELEASE,
                "BlackoutMesh:EmergencyWakeLock"
            )
            wakeLock?.acquire(4000)
        } catch (_: Exception) {}

        val fullScreenPending = openAppIntent(context)
        val soundUri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)
        val vibration = longArrayOf(0, 800, 300, 800, 300, 1000)

        val notification = NotificationCompat.Builder(context, CH_ALERT)
            .setSmallIcon(android.R.drawable.ic_dialog_alert)
            .setContentTitle("🚨 EMERGENCY ALERT")
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setSound(soundUri)
            .setVibrate(vibration)
            .setOnlyAlertOnce(false)
            .setAutoCancel(true)
            .setContentIntent(fullScreenPending)
            .setFullScreenIntent(fullScreenPending, true) // Heads-Up pop-up banner over screen / lockscreen!
            .build()

        val nm = NotificationManagerCompat.from(context)
        nm.cancel(ID_ALERT)
        nm.notify(ID_ALERT, notification)
    }
}
