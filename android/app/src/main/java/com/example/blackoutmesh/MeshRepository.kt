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
