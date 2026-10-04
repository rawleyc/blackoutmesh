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

    // Did the user want the service on? Defaults to true for auto-start.
    fun setEnabled(on: Boolean) {
        prefs.edit().putBoolean("enabled", on).apply()
    }

    fun isEnabled(): Boolean = prefs.getBoolean("enabled", true)

    // So the same alert never pops up twice after a restart.
    fun lastNotified(): Long = prefs.getLong("notified", -1L)

    fun setLastNotified(id: Long) {
        prefs.edit().putLong("notified", id).apply()
    }
}
