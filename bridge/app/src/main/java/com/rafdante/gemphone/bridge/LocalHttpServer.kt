package com.rafdante.gemphone.bridge

import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.Executors

class LocalHttpServer(
    private val service: GemAccessibilityService,
    private val port: Int,
    private val tokenProvider: () -> String,
) {
    @Volatile private var running = false
    @Volatile private var serverSocket: ServerSocket? = null
    private val pool = Executors.newCachedThreadPool()
    @Volatile var lastError: String? = null
        private set

    /** true, если сокет реально слушает порт. */
    fun isListening(): Boolean = serverSocket?.isBound == true && running

    fun start() {
        if (running) return
        running = true
        pool.execute {
            try {
                serverSocket = ServerSocket(port, 16, InetAddress.getByName("127.0.0.1"))
                while (running) {
                    val client = serverSocket?.accept() ?: break
                    pool.execute { handleClient(client) }
                }
            } catch (e: java.net.BindException) {
                lastError = "порт ${port} занят (${e.message})"
                running = false
                serverSocket = null
                android.util.Log.e("GemPhoneBridge", "HTTP bind failed: $lastError")
            } catch (e: Exception) {
                lastError = "${e.javaClass.simpleName}: ${e.message}"
                running = false
                serverSocket = null
                android.util.Log.e("GemPhoneBridge", "HTTP server stopped: $lastError")
            }
        }
    }

    fun stop() {
        running = false
        try { serverSocket?.close() } catch (_: Exception) {}
        pool.shutdownNow()
    }

    private fun handleClient(socket: Socket) {
        socket.use { s ->
            s.soTimeout = 10_000
            val input = BufferedInputStream(s.getInputStream())
            val output = s.getOutputStream()

            val headerBytes = readUntilHeadersEnd(input) ?: return
            val headerLines = headerBytes.toString(Charsets.UTF_8).split("\r\n")
            if (headerLines.isEmpty()) return

            val reqParts = headerLines[0].split(" ")
            if (reqParts.size < 2) return
            val method = reqParts[0].uppercase()
            val rawPath = reqParts[1]
            val path = rawPath.substringBefore("?")
            val query = rawPath.substringAfter("?", "")

            val headers = mutableMapOf<String, String>()
            for (line in headerLines.drop(1)) {
                val idx = line.indexOf(':')
                if (idx > 0) headers[line.substring(0, idx).trim().lowercase()] = line.substring(idx + 1).trim()
            }

            if (headers["x-gemphone-token"] != tokenProvider()) {
                writeJson(output, 401, JSONObject().put("error", "unauthorized"))
                return
            }

            val contentLen = headers["content-length"]?.toIntOrNull()?.coerceIn(0, 1_048_576) ?: 0
            val bodyStr = if (contentLen > 0) {
                val buf = ByteArray(contentLen)
                var read = 0
                while (read < contentLen) {
                    val r = input.read(buf, read, contentLen - read)
                    if (r <= 0) break
                    read += r
                }
                String(buf, 0, read, Charsets.UTF_8)
            } else ""

            try {
                val body = if (bodyStr.isNotBlank()) JSONObject(bodyStr) else JSONObject()
                when {
                    method == "GET" && path == "/status" -> {
                        val (w, h) = service.screenSize()
                        writeJson(output, 200, JSONObject().apply {
                            put("ok", true)
                            put("accessibility_enabled", true)
                            put("width", w)
                            put("height", h)
                            put("app", service.currentApp())
                            put("port_bound", isListening())
                            lastError?.let { put("server_error", it) }
                        })
                    }
                    method == "GET" && path == "/ui" -> {
                        val onlyInt = query.contains("only_interactive=1")
                        writeJson(output, 200, service.dumpUi(onlyInt))
                    }
                    method == "GET" && path == "/screenshot" -> {
                        val png = service.captureScreenshotPng()
                        if (png != null) writeBytes(output, 200, "image/png", png)
                        else writeJson(output, 501, JSONObject().put("error", "screenshot unavailable (Android 11+ required)"))
                    }
                    method == "POST" && path == "/tap" -> {
                        val ok = service.performTap(
                            body.getDouble("x").toFloat(),
                            body.getDouble("y").toFloat(),
                            body.optLong("duration_ms", 60L)
                        )
                        writeJson(output, 200, JSONObject().put("ok", ok))
                    }
                    method == "POST" && path == "/swipe" -> {
                        val ok = service.performSwipe(
                            body.getDouble("x1").toFloat(), body.getDouble("y1").toFloat(),
                            body.getDouble("x2").toFloat(), body.getDouble("y2").toFloat(),
                            body.optLong("duration_ms", 320L)
                        )
                        writeJson(output, 200, JSONObject().put("ok", ok))
                    }
                    method == "POST" && path == "/type" -> {
                        val res = service.performTypeText(body.optString("text"), body.optBoolean("press_enter", false))
                        val out = JSONObject().put("ok", res.ok)
                        if (res.warnings.isNotEmpty()) out.put("warnings", org.json.JSONArray(res.warnings))
                        if (res.reason != null) out.put("reason", res.reason)
                        writeJson(output, 200, out)
                    }
                    method == "POST" && path == "/key" -> {
                        val ok = service.performKey(body.optString("key"))
                        writeJson(output, 200, JSONObject().put("ok", ok))
                    }
                    method == "POST" && path == "/app" -> {
                        writeJson(output, 200, service.handleAppAction(body))
                    }
                    else -> writeJson(output, 404, JSONObject().put("error", "not_found"))
                }
            } catch (e: Exception) {
                writeJson(output, 500, JSONObject().put("error", e.message ?: "internal_error"))
            }
        }
    }

    private fun readUntilHeadersEnd(input: BufferedInputStream): ByteArray? {
        val out = ByteArrayOutputStream()
        var matched = 0
        val marker = byteArrayOf('\r'.code.toByte(), '\n'.code.toByte(), '\r'.code.toByte(), '\n'.code.toByte())
        while (out.size() < 16_384) {
            val b = input.read()
            if (b == -1) return null
            out.write(b)
            matched = if (b.toByte() == marker[matched]) matched + 1 else if (b.toByte() == marker[0]) 1 else 0
            if (matched == 4) return out.toByteArray()
        }
        return null
    }

    private fun writeJson(out: OutputStream, code: Int, json: JSONObject) =
        writeBytes(out, code, "application/json; charset=utf-8", json.toString().toByteArray(Charsets.UTF_8))

    private fun writeBytes(out: OutputStream, code: Int, ctype: String, payload: ByteArray) {
        val status = when (code) {
            200 -> "OK"
            401 -> "Unauthorized"
            404 -> "Not Found"
            501 -> "Not Implemented"
            else -> "Error"
        }
        val head = "HTTP/1.1 $code $status\r\nContent-Type: $ctype\r\nContent-Length: ${payload.size}\r\nConnection: close\r\n\r\n"
        out.write(head.toByteArray(Charsets.US_ASCII))
        out.write(payload)
        out.flush()
    }
}
