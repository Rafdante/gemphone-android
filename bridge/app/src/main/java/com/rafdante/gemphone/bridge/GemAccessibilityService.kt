package com.rafdante.gemphone.bridge

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Path
import android.graphics.Rect
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.util.DisplayMetrics
import android.view.Display
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.security.SecureRandom
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class GemAccessibilityService : AccessibilityService() {

    companion object {
        @Volatile var instance: GemAccessibilityService? = null
        @Volatile var lastPackage: String = "unknown"

        private const val PREFS = "gemphone_bridge_prefs"
        private const val KEY_TOKEN = "auth_token"

        fun getOrCreateToken(context: Context): String {
            val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            val existing = prefs.getString(KEY_TOKEN, null)
            if (!existing.isNullOrBlank()) return existing
            return rotateToken(context)
        }

        fun rotateToken(context: Context): String {
            val bytes = ByteArray(24)
            SecureRandom().nextBytes(bytes)
            val token = bytes.joinToString("") { "%02x".format(it) }
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit().putString(KEY_TOKEN, token).apply()
            return token
        }
    }

    private var server: LocalHttpServer? = null

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        getOrCreateToken(this)
        server = LocalHttpServer(this, port = 7331, tokenProvider = { getOrCreateToken(this) })
        server?.start()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        val pkg = event?.packageName?.toString()
        if (!pkg.isNullOrBlank()) lastPackage = pkg
    }

    override fun onInterrupt() {}

    override fun onDestroy() {
        server?.stop()
        server = null
        if (instance === this) instance = null
        super.onDestroy()
    }

    fun screenSize(): Pair<Int, Int> {
        val wm = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val b = wm.currentWindowMetrics.bounds
            b.width() to b.height()
        } else {
            val dm = DisplayMetrics()
            @Suppress("DEPRECATION") wm.defaultDisplay.getRealMetrics(dm)
            dm.widthPixels to dm.heightPixels
        }
    }

    fun currentApp(): String = rootInActiveWindow?.packageName?.toString() ?: lastPackage

    fun dumpUi(onlyInteractive: Boolean): JSONObject {
        val (w, h) = screenSize()
        val root = rootInActiveWindow
        val arr = JSONArray()
        val rect = Rect()

        fun walk(node: AccessibilityNodeInfo?) {
            if (node == null || arr.length() >= 60) return
            node.getBoundsInScreen(rect)
            val text = node.text?.toString()?.trim().orEmpty()
            val desc = node.contentDescription?.toString()?.trim().orEmpty()
            val resId = node.viewIdResourceName.orEmpty()
            val cls = node.className?.toString().orEmpty()
            val editable = node.isEditable || cls.contains("EditText") || cls.contains("AutoComplete")
            val interactive = node.isClickable || node.isScrollable || editable || node.isFocused
            val validBounds = rect.right > rect.left && rect.bottom > rect.top

            if (validBounds && (!onlyInteractive || interactive) && (interactive || text.isNotEmpty() || desc.isNotEmpty())) {
                arr.put(JSONObject().apply {
                    put("text", text)
                    put("contentDesc", desc)
                    put("resourceId", resId)
                    put("className", cls)
                    put("bounds", JSONArray(listOf(rect.left, rect.top, rect.right, rect.bottom)))
                    put("clickable", node.isClickable)
                    put("scrollable", node.isScrollable)
                    put("editable", editable)
                    put("focused", node.isFocused)
                })
            }
            for (i in 0 until node.childCount) {
                if (arr.length() >= 60) break
                val child = node.getChild(i)
                walk(child)
                @Suppress("DEPRECATION") child?.recycle()
            }
        }

        walk(root)
        return JSONObject().apply {
            put("screen", "${w}x${h}")
            put("app", currentApp())
            put("elements", arr)
        }
    }

    fun performTap(x: Float, y: Float, durationMs: Long): Boolean {
        val path = Path().apply { moveTo(x, y) }
        val stroke = GestureDescription.StrokeDescription(path, 0, durationMs.coerceIn(40, 4000))
        return dispatchSync(GestureDescription.Builder().addStroke(stroke).build())
    }

    fun performSwipe(x1: Float, y1: Float, x2: Float, y2: Float, durationMs: Long): Boolean {
        val path = Path().apply { moveTo(x1, y1); lineTo(x2, y2) }
        val stroke = GestureDescription.StrokeDescription(path, 0, durationMs.coerceIn(50, 3000))
        return dispatchSync(GestureDescription.Builder().addStroke(stroke).build())
    }

    private fun dispatchSync(gesture: GestureDescription): Boolean {
        val latch = CountDownLatch(1)
        var ok = false
        val started = dispatchGesture(gesture, object : GestureResultCallback() {
            override fun onCompleted(g: GestureDescription?) { ok = true; latch.countDown() }
            override fun onCancelled(g: GestureDescription?) { ok = false; latch.countDown() }
        }, null)
        if (!started) return false
        latch.await(5, TimeUnit.SECONDS)
        return ok
    }

    fun performTypeText(text: String, pressEnter: Boolean): Boolean {
        val root = rootInActiveWindow ?: return false
        val target = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT) ?: findFirstEditable(root) ?: return false
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        val ok = target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        if (ok && pressEnter && Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            target.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id)
        }
        return ok
    }

    private fun findFirstEditable(node: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
        if (node == null) return null
        if (node.isEditable) return node
        for (i in 0 until node.childCount) {
            val found = findFirstEditable(node.getChild(i))
            if (found != null) return found
        }
        return null
    }

    fun performKey(key: String): Boolean {
        val action = when (key.uppercase()) {
            "BACK", "KEYCODE_BACK" -> GLOBAL_ACTION_BACK
            "HOME", "KEYCODE_HOME" -> GLOBAL_ACTION_HOME
            "RECENTS", "APP_SWITCH", "KEYCODE_APP_SWITCH" -> GLOBAL_ACTION_RECENTS
            "NOTIFICATIONS" -> GLOBAL_ACTION_NOTIFICATIONS
            "QUICK_SETTINGS" -> GLOBAL_ACTION_QUICK_SETTINGS
            "POWER", "KEYCODE_POWER" -> GLOBAL_ACTION_POWER_DIALOG
            "LOCK" -> if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) GLOBAL_ACTION_LOCK_SCREEN else return false
            else -> return false
        }
        return performGlobalAction(action)
    }

    fun captureScreenshotPng(): ByteArray? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return null
        val latch = CountDownLatch(1)
        var pngBytes: ByteArray? = null
        takeScreenshot(Display.DEFAULT_DISPLAY, mainExecutor, object : TakeScreenshotCallback {
            override fun onSuccess(screenshot: ScreenshotResult) {
                try {
                    val bmp = Bitmap.wrapHardwareBuffer(screenshot.hardwareBuffer, screenshot.colorSpace)
                    if (bmp != null) {
                        val soft = bmp.copy(Bitmap.Config.ARGB_8888, false)
                        val out = ByteArrayOutputStream()
                        soft.compress(Bitmap.CompressFormat.PNG, 100, out)
                        pngBytes = out.toByteArray()
                        soft.recycle()
                        bmp.recycle()
                    }
                } finally {
                    screenshot.hardwareBuffer.close()
                    latch.countDown()
                }
            }
            override fun onFailure(errorCode: Int) { latch.countDown() }
        })
        latch.await(6, TimeUnit.SECONDS)
        return pngBytes
    }

    fun handleAppAction(body: JSONObject): JSONObject {
        return when (body.optString("action").lowercase()) {
            "list" -> {
                val query = body.optString("query").lowercase()
                val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
                val pkgs = packageManager.queryIntentActivities(intent, 0)
                    .map { it.activityInfo.packageName }
                    .distinct()
                    .filter { query.isEmpty() || it.lowercase().contains(query) }
                    .sorted()
                JSONObject().apply {
                    put("count", pkgs.size)
                    put("packages", JSONArray(pkgs.take(60)))
                }
            }
            "launch" -> {
                val pkg = body.optString("package")
                val intent = packageManager.getLaunchIntentForPackage(pkg)
                    ?: return JSONObject().put("ok", false).put("error", "App not found: $pkg")
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                startActivity(intent)
                JSONObject().put("ok", true).put("package", pkg)
            }
            "open_url" -> {
                val url = body.optString("url")
                val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                startActivity(intent)
                JSONObject().put("ok", true).put("url", url)
            }
            else -> JSONObject().put("ok", false).put("error", "Unsupported app action")
        }
    }
}
