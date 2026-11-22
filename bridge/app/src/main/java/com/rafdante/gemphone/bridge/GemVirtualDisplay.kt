package com.rafdante.gemphone.bridge

import android.content.Context
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.graphics.Point
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.Image
import android.media.ImageReader
import android.os.Handler
import android.os.Looper
import android.view.Display
import org.json.JSONObject
import java.io.ByteArrayOutputStream

/**
 * Виртуальный дисплей «второй экран» — агент работает на нём, не занимая экран пользователя.
 *
 * Использование:
 *   GemVirtualDisplay.create(ctx, width, height, dpi, flags) -> id дисплея или -1
 *   GemVirtualDisplay.statusJson()      -> state (ON/OFF), флаги, размер
 *   GemVirtualDisplay.captureFrame()    -> PNG/JPEG последнего кадра дисплея
 *   GemVirtualDisplay.launchOnDisplay() -> результат `am start` вместе с текстом ошибки
 *   GemVirtualDisplay.stop()
 */
object GemVirtualDisplay {
    /**
     * DisplayManager.VIRTUAL_DISPLAY_FLAG_SUPPORTS_TOUCH скрыт (@hide) и отсутствует в публичном SDK:
     * ссылка на него по имени даёт "Unresolved reference" при сборке. Значение 1 shl 6 — из AOSP.
     * Фактически применённые системой флаги видно в statusJson() -> display_flags.
     */
    private const val FLAG_SUPPORTS_TOUCH = 1 shl 6

    /** Флаги по умолчанию: PUBLIC | SUPPORTS_TOUCH. Можно переопределить в запросе create ("flags"). */
    val DEFAULT_FLAGS: Int = DisplayManager.VIRTUAL_DISPLAY_FLAG_PUBLIC or FLAG_SUPPORTS_TOUCH

    private var vd: VirtualDisplay? = null
    private var imageReader: ImageReader? = null
    private var displayId: Int = -1
    private var requestedFlags: Int = 0
    private val main = Handler(Looper.getMainLooper())

    // Последний кадр дисплея: единственный способ сделать скриншот виртуального дисплея
    // (screencap -d работает только с физическими дисплеями).
    private val frameLock = Any()
    private var latestImage: Image? = null

    // Имя пакета и компонент подставляются в `sh -c` от имени shell-пользователя, поэтому
    // принимаем только безопасные символы.
    private val PACKAGE_RE = Regex("^[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z0-9_]+)+$")
    private val COMPONENT_RE = Regex("^[A-Za-z][A-Za-z0-9_.]*/[A-Za-z0-9_.$]+$")

    data class LaunchResult(val ok: Boolean, val command: String, val output: String)

    fun isRunning(): Boolean = vd != null

    fun currentDisplayId(): Int = displayId

    /** Запускает дисплей. Возвращает id дисплея или -1 при ошибке. */
    @Synchronized
    fun create(
        ctx: Context,
        width: Int = 1080,
        height: Int = 2344,
        dpi: Int = 420,
        flags: Int = DEFAULT_FLAGS,
    ): Int {
        if (vd != null) return displayId
        val dm = ctx.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager
        try {
            // Реальный Surface обязателен: с null дисплей создаётся в state OFF
            // и Activity на него не выводятся. Буферов 3: один держим как «последний кадр».
            val reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 3)
            reader.setOnImageAvailableListener({ r ->
                try {
                    val img = r.acquireLatestImage()
                    if (img != null) {
                        synchronized(frameLock) {
                            latestImage?.close()
                            latestImage = img
                        }
                    }
                } catch (_: Exception) {
                }
            }, main)
            imageReader = reader
            requestedFlags = flags
            vd = dm.createVirtualDisplay(
                "gemphone-agent-vd",
                width,
                height,
                dpi,
                reader.surface,
                flags,
            )
            displayId = vd?.display?.displayId ?: -1
            if (displayId < 0) {
                reader.close()
                imageReader = null
                vd = null
            }
        } catch (e: Exception) {
            android.util.Log.w("GemVD", "createVirtualDisplay failed: ${e.message}")
            try { imageReader?.close() } catch (_: Exception) {}
            imageReader = null
            vd = null
            displayId = -1
        }
        return displayId
    }

    /**
     * Останавливает дисплей синхронно: раньше stop() откладывался через main.post, и create()
     * сразу после него видел «живой» vd и возвращал id уже удаляемого дисплея.
     * Внимание: приложения, открытые на дисплее, при его удалении переезжают на основной экран —
     * перед stop() их нужно закрыть (am force-stop).
     */
    @Synchronized
    fun stop() {
        try { vd?.release() } catch (_: Exception) {}
        synchronized(frameLock) {
            try { latestImage?.close() } catch (_: Exception) {}
            latestImage = null
        }
        try { imageReader?.close() } catch (_: Exception) {}
        vd = null
        imageReader = null
        displayId = -1
    }

    /** Состояние дисплея для проверки «не в OFF, а в ON». */
    fun statusJson(): JSONObject {
        val o = JSONObject()
        val d = vd?.display ?: return o.put("running", false)
        val p = Point()
        @Suppress("DEPRECATION")
        d.getRealSize(p)
        return o.put("running", true)
            .put("display_id", displayId)
            .put("name", d.name)
            .put("state", stateName(d.state))
            .put("display_flags", d.flags)
            .put("requested_flags", requestedFlags)
            .put("width", p.x)
            .put("height", p.y)
    }

    private fun stateName(s: Int): String = when (s) {
        Display.STATE_ON -> "ON"
        Display.STATE_OFF -> "OFF"
        Display.STATE_DOZE -> "DOZE"
        else -> "UNKNOWN($s)"
    }

    /** Кадр виртуального дисплея (PNG или JPEG). null — кадров ещё не было. */
    fun captureFrame(asPng: Boolean = true, quality: Int = 85): ByteArray? = synchronized(frameLock) {
        val img = latestImage
        if (img == null) {
            null
        } else {
            try {
                val plane = img.planes[0]
                val pixelStride = plane.pixelStride
                val rowPadding = plane.rowStride - pixelStride * img.width
                val buf = plane.buffer
                buf.rewind()
                val wide = Bitmap.createBitmap(
                    img.width + rowPadding / pixelStride,
                    img.height,
                    Bitmap.Config.ARGB_8888,
                )
                wide.copyPixelsFromBuffer(buf)
                val bmp = if (rowPadding == 0) wide else Bitmap.createBitmap(wide, 0, 0, img.width, img.height)
                val out = ByteArrayOutputStream()
                if (asPng) {
                    bmp.compress(Bitmap.CompressFormat.PNG, 100, out)
                } else {
                    bmp.compress(Bitmap.CompressFormat.JPEG, quality.coerceIn(10, 100), out)
                }
                if (bmp !== wide) bmp.recycle()
                wide.recycle()
                out.toByteArray()
            } catch (e: Exception) {
                android.util.Log.w("GemVD", "captureFrame failed: ${e.message}")
                null
            }
        }
    }

    /** Список всех дисплеев (для отладки). */
    fun list(ctx: Context): List<Point> {
        val dm = ctx.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager
        return dm.displays.map { d: Display ->
            val p = Point()
            @Suppress("DEPRECATION")
            d.getRealSize(p)
            p
        }
    }

    /**
     * Запуск приложения на виртуальном дисплее (нужен Shizuku/ADB).
     * Возвращает вывод `am start` вместе с ошибками (stderr): именно в нём видно, почему
     * ActivityManager отказал (Permission Denial, SecurityException, display state).
     */
    fun launchOnDisplay(ctx: Context, packageName: String, id: Int = displayId): LaunchResult {
        if (id < 0) return LaunchResult(false, "", "ERROR: виртуальный дисплей не создан")
        if (!PACKAGE_RE.matches(packageName)) {
            return LaunchResult(false, "", "ERROR: недопустимое имя пакета: $packageName")
        }
        val component = resolveLauncher(ctx, packageName)
        val cmd = if (component != null) {
            "am start --display $id -n '$component'"
        } else {
            "am start --display $id -a android.intent.action.MAIN " +
                "-c android.intent.category.LAUNCHER -p $packageName"
        }
        val out = ShellRunner.run("$cmd 2>&1").trim()
        val failed = out.isEmpty() ||
            out.contains("Error", ignoreCase = true) ||
            out.contains("Exception", ignoreCase = true) ||
            out.contains("not started", ignoreCase = true) ||
            out.contains("Permission Denial", ignoreCase = true)
        return LaunchResult(!failed, cmd, out)
    }

    private fun resolveLauncher(ctx: Context, pkg: String): String? {
        return try {
            val c = ctx.packageManager.getLaunchIntentForPackage(pkg)?.component ?: return null
            val comp = "${c.packageName}/${c.className}"
            if (COMPONENT_RE.matches(comp)) comp else null
        } catch (_: Exception) {
            null
        }
    }
}
