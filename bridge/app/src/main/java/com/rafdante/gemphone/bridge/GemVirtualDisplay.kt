package com.rafdante.gemphone.bridge

import android.content.Context
import android.graphics.Point
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.os.Handler
import android.os.Looper
import android.view.Display
import android.view.Surface

/**
 * Виртуальный дисплей «второй экран» — агент работает на нём, не занимая экран пользователя.
 *
 * Создаётся через Shizuku (uid shell), т.к. системный сервис требует прав системы.
 * Использование:
 *   GemVirtualDisplay.create(ctx, width, height, dpi) -> id дисплея или null
 *   GemVirtualDisplay.stop()
 */
object GemVirtualDisplay {
    private var vd: VirtualDisplay? = null
    private var displayId: Int = -1
    private val main = Handler(Looper.getMainLooper())

    fun isRunning(): Boolean = vd != null

    fun currentDisplayId(): Int = displayId

    /** Запускает дисплей. Возвращает id дисплея или -1 при ошибке. */
    fun create(ctx: Context, width: Int = 1080, height: Int = 2344, dpi: Int = 420): Int {
        if (vd != null) return displayId
        val dm = ctx.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager
        try {
            // PRIVATE-дисплей разрешён любому приложению без permissions
            val flags = DisplayManager.VIRTUAL_DISPLAY_FLAG_OWN_CONTENT_ONLY
            vd = dm.createVirtualDisplay(
                "gemphone-agent-vd",
                width,
                height,
                dpi,
                null as Surface?,
                flags,
            )
            displayId = vd?.display?.displayId ?: -1
        } catch (e: Exception) {
            android.util.Log.w("GemVD", "createVirtualDisplay failed: ${e.message}")
            vd = null
            displayId = -1
        }
        return displayId
    }

    fun stop() {
        main.post {
            try {
                vd?.release()
            } catch (_: Exception) {
            }
            vd = null
            displayId = -1
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

    /** Запуск приложения на виртуальном дисплее (нужен Shizuku/ADB). */
    fun launchOnDisplay(ctx: Context, packageName: String, id: Int = displayId): Boolean {
        return try {
            val comp = resolveLauncher(ctx, packageName)
            ShellRunner.exec("am start --display $id -n $comp")
        } catch (e: Exception) {
            false
        }
    }

    private fun resolveLauncher(ctx: Context, pkg: String): String {
        return try {
            val i = ctx.packageManager.getLaunchIntentForPackage(pkg)
            if (i == null || i.component == null) pkg else
                "${i.component!!.packageName}/${i.component!!.className}"
        } catch (_: Exception) {
            pkg
        }
    }
}
