package com.rafdante.gemphone.bridge

import android.os.Build

/**
 * Исполнитель shell-команд: Shizuku (новый API) с фолбэком на Runtime.exec.
 * Shell-права дают возможность запускать приложения на виртуальном дисплее
 * и посылать input-события в конкретный display id.
 */
object ShellRunner {
    private var shizukuAvailable = false

    fun init(): Boolean {
        return try {
            val cls = Class.forName("rikka.shizuku.Shizuku")
            val binder = cls.getMethod("getBinder").invoke(null)
            shizukuAvailable = binder != null && checkSelfPermission()
            shizukuAvailable
        } catch (e: Throwable) {
            shizukuAvailable = false
            false
        } finally {
            android.util.Log.i("ShellRunner", "shizukuAvailable=$shizukuAvailable sdk=${Build.VERSION.SDK_INT}")
        }
    }

    /** Есть ли уже разрешение Shizuku. */
    fun checkSelfPermission(): Boolean = try {
        val cls = Class.forName("rikka.shizuku.Shizuku")
        (cls.getMethod("checkSelfPermission").invoke(null) as Int) == 0
    } catch (e: Throwable) {
        false
    }

    /** Запросить разрешение — покажет диалог/уведомление Shizuku. */
    fun requestPermission() {
        try {
            val cls = Class.forName("rikka.shizuku.Shizuku")
            val argCls = Class.forName("rikka.shizuku.RequestPermissionResultListener")
            // Простой путь: Shizuku.requestPermission(code)
            cls.methods.firstOrNull { it.name == "requestPermission" && it.parameterTypes.size == 1 }
                ?.invoke(null, 7331)
        } catch (e: Throwable) {
            android.util.Log.w("ShellRunner", "requestPermission: ${e.message}")
        }
    }

    fun isShizuku(): Boolean = shizukuAvailable

    /** Выполнить shell-команду. Возвращает stdout. */
    fun exec(cmd: String): Boolean {
        return try {
            if (shizukuAvailable) {
                execShizuku(cmd)
            } else {
                execRuntime(cmd)
            }
        } catch (e: Exception) {
            android.util.Log.w("ShellRunner", "exec failed: ${e.message}")
            false
        }
    }

    fun run(cmd: String): String = try {
        if (shizukuAvailable) execShizukuOut(cmd) else execRuntimeOut(cmd)
    } catch (e: Exception) {
        // Причину возвращаем текстом, а не пустой строкой: по ней видно, что сломалось
        // (Shizuku недоступен, newProcess, отказ ActivityManager).
        val c = e.cause ?: e
        "ERROR: ${c.javaClass.simpleName}: ${c.message}"
    }

    /**
     * В Shizuku API 13.x метод newProcess не публичный: getMethod() его не находит
     * (NoSuchMethodException), и любая shell-команда молча возвращала false.
     * getDeclaredMethod + isAccessible работает и для публичного, и для скрытого варианта.
     */
    private fun newProcessMethod(cls: Class<*>): java.lang.reflect.Method {
        val m = cls.getDeclaredMethod(
            "newProcess",
            Array<String>::class.java,
            Array<String>::class.java,
            String::class.java,
        )
        m.isAccessible = true
        return m
    }

    private fun execShizuku(cmd: String): Boolean {
        val cls = Class.forName("rikka.shizuku.Shizuku")
        val newProcess = newProcessMethod(cls)
        val proc = newProcess.invoke(null, arrayOf("sh", "-c", cmd), null, null) as Process
        return proc.waitFor() == 0
    }

    private fun execShizukuOut(cmd: String): String {
        val cls = Class.forName("rikka.shizuku.Shizuku")
        val newProcess = newProcessMethod(cls)
        val proc = newProcess.invoke(null, arrayOf("sh", "-c", cmd), null, null) as Process
        val out = proc.inputStream.bufferedReader().readText()
        proc.waitFor()
        return out
    }

    private fun execRuntime(cmd: String): Boolean {
        val p = Runtime.getRuntime().exec(arrayOf("sh", "-c", cmd))
        return try {
            p.waitFor() == 0
        } catch (_: Exception) {
            false
        }
    }

    private fun execRuntimeOut(cmd: String): String {
        val p = Runtime.getRuntime().exec(arrayOf("sh", "-c", cmd))
        val out = p.inputStream.bufferedReader().readText()
        p.waitFor()
        return out
    }

    /** Тап на конкретном дисплее. */
    fun tapOnDisplay(displayId: Int, x: Int, y: Int): Boolean =
        exec("input -d $displayId tap $x $y")

    /** Свайп на конкретном дисплее. */
    fun swipeOnDisplay(displayId: Int, x1: Int, y1: Int, x2: Int, y2: Int, ms: Int = 300): Boolean =
        exec("input -d $displayId swipe $x1 $y1 $x2 $y2 $ms")

    /** Текст на конкретном дисплее. */
    fun typeOnDisplay(displayId: Int, text: String): Boolean {
        val escaped = text.replace("'", "'\\''").replace(" ", "%s")
        return exec("input -d $displayId text '$escaped'")
    }

    /** Скриншот дисплея в файл. */
    fun screenshotDisplay(displayId: Int, path: String): Boolean =
        exec("screencap -d $displayId -p $path")
}
