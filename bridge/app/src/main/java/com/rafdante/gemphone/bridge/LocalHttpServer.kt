package com.rafdante.gemphone.bridge

import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.security.MessageDigest
import java.util.concurrent.Executors

class LocalHttpServer(
    private val service: GemAccessibilityService,
    private val port: Int,
    private val tokenProvider: () -> String,
) {
    @Volatile private var running = false
    @Volatile private var serverSocket: ServerSocket? = null
    @Volatile var lastError: String? = null
        private set

    private val acceptPool = Executors.newSingleThreadExecutor { r ->
        Thread(r, "gemphone-http-accept").apply { isDaemon = true }
    }
    private val workerPool = Executors.newFixedThreadPool(8) { r ->
        Thread(r, "gemphone-http-worker").apply { isDaemon = true }
    }

    fun isListening(): Boolean = serverSocket?.isBound == true && running

    fun start() {
        if (running) return
        running = true
        lastError = null
        acceptPool.execute {
            try {
                val ss = ServerSocket().apply {
                    reuseAddress = true
                    bind(InetSocketAddress(InetAddress.getByName("127.0.0.1"), port), 32)
                }
                serverSocket = ss
                while (running) {
                    val client = ss.accept() ?: break
                    client.tcpNoDelay = true
                    workerPool.execute { handleClient(client) }
                }
            } catch (e: java.net.BindException) {
                lastError = "порт $port занят (${e.message})"
                running = false
                serverSocket = null
                android.util.Log.e("GemPhoneBridge", "HTTP bind failed: $lastError")
            } catch (e: Exception) {
                if (running) {
                    lastError = "${e.javaClass.simpleName}: ${e.message}"
                    android.util.Log.e("GemPhoneBridge", "HTTP server stopped: $lastError")
                }
                running = false
                serverSocket = null
            }
        }
    }

    fun stop() {
        running = false
        try { serverSocket?.close() } catch (_: Exception) {}
        serverSocket = null
        acceptPool.shutdownNow()
        workerPool.shutdownNow()
    }

    private fun parseQuery(query: String): Map<String, String> {
        if (query.isBlank()) return emptyMap()
        val map = mutableMapOf<String, String>()
        for (pair in query.split("&")) {
            val idx = pair.indexOf('=')
            if (idx > 0) {
                map[pair.substring(0, idx).lowercase()] = pair.substring(idx + 1)
            } else if (pair.isNotBlank()) {
                map[pair.lowercase()] = "1"
            }
        }
        return map
    }

    private fun isTokenValid(provided: String?): Boolean {
        if (provided.isNullOrEmpty()) return false
        val expected = tokenProvider()
        return MessageDigest.isEqual(
            provided.toByteArray(Charsets.UTF_8),
            expected.toByteArray(Charsets.UTF_8)
        )
    }

    private fun handleClient(socket: Socket) {
        socket.use { s ->
            s.soTimeout = 30_000
            val input = BufferedInputStream(s.getInputStream(), 16_384)
            val output = s.getOutputStream()

            var requestsHandled = 0
            while (running && requestsHandled < 100) {
                val headerBytes = try {
                    readUntilHeadersEnd(input)
                } catch (_: SocketTimeoutException) {
                    break
                } catch (_: Exception) {
                    break
                } ?: break

                requestsHandled++
                val headerLines = headerBytes.toString(Charsets.UTF_8).split("\r\n")
                if (headerLines.isEmpty()) break

                val reqParts = headerLines[0].split(" ")
                if (reqParts.size < 2) break
                val method = reqParts[0].uppercase()
                val rawPath = reqParts[1]
                val path = rawPath.substringBefore("?")
                val qParams = parseQuery(rawPath.substringAfter("?", ""))

                val headers = HashMap<String, String>(12)
                for (i in 1 until headerLines.size) {
                    val line = headerLines[i]
                    val idx = line.indexOf(':')
                    if (idx > 0) {
                        headers[line.substring(0, idx).trim().lowercase()] = line.substring(idx + 1).trim()
                    }
                }

                val keepAlive = headers["connection"]?.lowercase() != "close" && requestsHandled < 100

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

                if (!isTokenValid(headers["x-gemphone-token"])) {
                    writeJson(output, 401, JSONObject().put("error", "unauthorized"), keepAlive = false)
                    break
                }

                try {
                    val body = if (bodyStr.isNotBlank()) JSONObject(bodyStr) else JSONObject()
                    when {
                        method == "GET" && path == "/status" -> {
                            val (w, h) = service.screenSize()
                            val app = service.currentApp()
                            writeJson(output, 200, JSONObject().apply {
                                put("ok", true)
                                put("service_running", true)
                                put("accessibility_enabled", true)
                                put("width", w)
                                put("height", h)
                                put("screen_width", w)
                                put("screen_height", h)
                                put("app", app)
                                put("foreground_package", app)
                                put("port_bound", isListening())
                                lastError?.let { put("server_error", it) }
                            }, keepAlive)
                        }
                        method == "GET" && path == "/ui" -> {
                            val onlyInt = qParams["only_interactive"] == "1" || qParams["only_interactive"] == "true"
                            val maxEl = qParams["max_elements"]?.toIntOrNull() ?: 60
                            writeJson(output, 200, service.dumpUi(onlyInt, maxEl), keepAlive)
                        }
                        method == "GET" && path == "/screenshot" -> {
                            val maxDim = qParams["max_dim"]?.toIntOrNull() ?: 1280
                            val quality = qParams["quality"]?.toIntOrNull() ?: 82
                            val asPng = qParams["format"]?.lowercase() == "png"
                            val shot = service.captureScreenshot(maxDim, quality, asPng)
                            if (shot != null) {
                                writeBytes(output, 200, shot.second, shot.first, keepAlive)
                            } else {
                                writeJson(
                                    output,
                                    501,
                                    JSONObject().put("error", "screenshot unavailable (Android 11+ required)"),
                                    keepAlive
                                )
                            }
                        }
                        method == "POST" && path == "/tap" -> {
                            val x = body.optDouble("x", Double.NaN)
                            val y = body.optDouble("y", Double.NaN)
                            if (x.isNaN() || y.isNaN()) {
                                writeJson(output, 400, JSONObject().put("error", "x and y are required"), keepAlive)
                            } else {
                                val ok = service.performTap(x.toFloat(), y.toFloat(), body.optLong("duration_ms", 50L))
                                val resp = JSONObject().put("ok", ok)
                                if (ok && body.optBoolean("auto_dump", false)) {
                                    Thread.sleep(body.optLong("settle_ms", 200L).coerceIn(0L, 2000L))
                                    resp.put("ui_dump", service.dumpUi(body.optBoolean("only_interactive", false)))
                                }
                                writeJson(output, 200, resp, keepAlive)
                            }
                        }
                        method == "POST" && path == "/swipe" -> {
                            val x1 = body.optDouble("x1", Double.NaN)
                            val y1 = body.optDouble("y1", Double.NaN)
                            val x2 = body.optDouble("x2", Double.NaN)
                            val y2 = body.optDouble("y2", Double.NaN)
                            if (x1.isNaN() || y1.isNaN() || x2.isNaN() || y2.isNaN()) {
                                writeJson(output, 400, JSONObject().put("error", "x1,y1,x2,y2 are required"), keepAlive)
                            } else {
                                val ok = service.performSwipe(
                                    x1.toFloat(), y1.toFloat(), x2.toFloat(), y2.toFloat(),
                                    body.optLong("duration_ms", 260L)
                                )
                                val resp = JSONObject().put("ok", ok)
                                if (ok && body.optBoolean("auto_dump", false)) {
                                    Thread.sleep(body.optLong("settle_ms", 200L).coerceIn(0L, 2000L))
                                    resp.put("ui_dump", service.dumpUi(body.optBoolean("only_interactive", false)))
                                }
                                writeJson(output, 200, resp, keepAlive)
                            }
                        }
                        method == "POST" && path == "/type" -> {
                            val res = service.performTypeText(body.optString("text"), body.optBoolean("press_enter", false))
                            val out = JSONObject().put("ok", res.ok)
                            if (res.warnings.isNotEmpty()) out.put("warnings", JSONArray(res.warnings))
                            if (res.reason != null) out.put("reason", res.reason)
                            if (res.ok && body.optBoolean("auto_dump", false)) {
                                Thread.sleep(body.optLong("settle_ms", 180L).coerceIn(0L, 2000L))
                                out.put("ui_dump", service.dumpUi(body.optBoolean("only_interactive", false)))
                            }
                            writeJson(output, 200, out, keepAlive)
                        }
                        method == "POST" && path == "/key" -> {
                            val ok = service.performKey(body.optString("key"))
                            val resp = JSONObject().put("ok", ok)
                            if (ok && body.optBoolean("auto_dump", false)) {
                                Thread.sleep(body.optLong("settle_ms", 200L).coerceIn(0L, 2000L))
                                resp.put("ui_dump", service.dumpUi(body.optBoolean("only_interactive", false)))
                            }
                            writeJson(output, 200, resp, keepAlive)
                        }
                        method == "POST" && path == "/app" -> {
                            writeJson(output, 200, service.handleAppAction(body), keepAlive)
                        }
                        method == "POST" && path == "/batch" -> {
                            writeJson(output, 200, service.performBatch(body), keepAlive)
                        }
                        else -> writeJson(output, 404, JSONObject().put("error", "not_found"), keepAlive)
                    }
                } catch (e: Exception) {
                    writeJson(output, 500, JSONObject().put("error", e.message ?: "internal_error"), keepAlive = false)
                    break
                }

                if (!keepAlive) break
            }
        }
    }

    private fun readUntilHeadersEnd(input: BufferedInputStream): ByteArray? {
        val out = ByteArrayOutputStream(512)
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

    private fun writeJson(out: OutputStream, code: Int, json: JSONObject, keepAlive: Boolean) =
        writeBytes(out, code, "application/json; charset=utf-8", json.toString().toByteArray(Charsets.UTF_8), keepAlive)

    private fun writeBytes(out: OutputStream, code: Int, ctype: String, payload: ByteArray, keepAlive: Boolean) {
        val status = when (code) {
            200 -> "OK"
            400 -> "Bad Request"
            401 -> "Unauthorized"
            404 -> "Not Found"
            501 -> "Not Implemented"
            else -> "Error"
        }
        val connHeader = if (keepAlive) "Connection: keep-alive\r\nKeep-Alive: timeout=30, max=100" else "Connection: close"
        val head = "HTTP/1.1 $code $status\r\nContent-Type: $ctype\r\nContent-Length: ${payload.size}\r\n$connHeader\r\n\r\n"
        out.write(head.toByteArray(Charsets.US_ASCII))
        out.write(payload)
        out.flush()
    }
}
