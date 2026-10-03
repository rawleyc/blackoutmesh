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
        add(Manifest.permission.ACCESS_FINE_LOCATION)
        if (Build.VERSION.SDK_INT >= 33) add(Manifest.permission.POST_NOTIFICATIONS)
    }.toTypedArray()

    /** The service can only start if all three Bluetooth permissions are granted. */
    fun hasBluetooth(context: Context): Boolean =
        bluetooth.all { context.checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED }
}
