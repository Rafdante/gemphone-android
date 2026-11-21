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
import java.util.Collections
import java.util.IdentityHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.math.max
import kotlin.math.roundToInt

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
    val httpServer: LocalHttpServer? get() = server

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

    @Suppress("DEPRECATION")
    private fun safeRecycle(node: AccessibilityNodeInfo?) {
        if (node != null && Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
            try {
                node.recycle()
            } catch (_: IllegalStateException) {
                // уже освобождён
            }
        }
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

    fun currentApp(): String {
        val root = rootInActiveWindow
        val pkg = root?.packageName?.toString()
        safeRecycle(root)
        return if (!pkg.isNullOrBlank()) pkg else lastPackage
    }

    fun dumpUi(onlyInteractive: Boolean, maxElements: Int = 60): JSONObject {
        val (w, h) = screenSize()
        val limit = maxElements.coerceIn(1, 120)
        val root = rootInActiveWindow
        val activePkg = root?.packageName?.toString()?.takeIf { it.isNotBlank() } ?: lastPackage
        val arr = JSONArray()
        val rect = Rect()

        fun walk(node: AccessibilityNodeInfo?, depth: Int) {
            if (node == null || arr.length() >= limit || depth > 40) return
            if (node.isVisibleToUser) {
                node.getBoundsInScreen(rect)
                val onScreen = rect.right > rect.left &&
                    rect.bottom > rect.top &&
                    rect.right > 0 && rect.bottom > 0 &&
                    rect.left < w && rect.top < h

                if (onScreen) {
                    val text = node.text?.toString()?.trim().orEmpty()
                    val desc = node.contentDescription?.toString()?.trim().orEmpty()
                    val resId = node.viewIdResourceName.orEmpty()
                    val cls = node.className?.toString().orEmpty()
                    val editable = node.isEditable || cls.contains("EditText") || cls.contains("AutoComplete")
                    val interactive = node.isClickable || node.isScrollable || editable || node.isFocused

                    if ((!onlyInteractive || interactive) && (interactive || text.isNotEmpty() || desc.isNotEmpty())) {
                        val cl = rect.left.coerceIn(0, w)
                        val ct = rect.top.coerceIn(0, h)
                        val cr = rect.right.coerceIn(0, w)
                        val cb = rect.bottom.coerceIn(0, h)
                        arr.put(JSONObject().apply {
                            put("text", text)
                            put("content_desc", desc)
                            put("resource_id", resId)
                            put("class_name", cls)
                            put("bounds", JSONArray(listOf(cl, ct, cr, cb)))
                            put("center_x", (cl + cr) / 2)
                            put("center_y", (ct + cb) / 2)
                            put("clickable", node.isClickable)
                            put("scrollable", node.isScrollable)
                            put("editable", editable)
                            put("focused", node.isFocused)
                        })
                    }
                }
            }
            val count = node.childCount
            for (i in 0 until count) {
                if (arr.length() >= limit) break
                val child = node.getChild(i)
                try {
                    walk(child, depth + 1)
                } finally {
                    safeRecycle(child)
                }
            }
        }

        try {
            walk(root, 0)
        } finally {
            safeRecycle(root)
        }

        return JSONObject().apply {
            put("ok", true)
            put("screen", "${w}x${h}")
            put("screen_width", w)
            put("screen_height", h)
            put("app", activePkg)
            put("foreground_package", activePkg)
            put("elements", arr)
        }
    }

    fun performTap(x: Float, y: Float, durationMs: Long): Boolean {
        val path = Path().apply { moveTo(x, y) }
        val stroke = GestureDescription.StrokeDescription(path, 0, durationMs.coerceIn(30, 4000))
        return dispatchSync(GestureDescription.Builder().addStroke(stroke).build())
    }

    fun performSwipe(x1: Float, y1: Float, x2: Float, y2: Float, durationMs: Long): Boolean {
        val path = Path().apply { moveTo(x1, y1); lineTo(x2, y2) }
        val stroke = GestureDescription.StrokeDescription(path, 0, durationMs.coerceIn(40, 3000))
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
        latch.await(4, TimeUnit.SECONDS)
        return ok
    }

    fun performTypeText(text: String, pressEnter: Boolean): TypeResult {
        val activeRoot = rootInActiveWindow
        val activeWindowId = activeRoot?.windowId
        safeRecycle(activeRoot)

        val candidates = mutableListOf<Triple<AccessibilityNodeInfo, Boolean, Boolean>>()
        val toRecycle = Collections.newSetFromMap(IdentityHashMap<AccessibilityNodeInfo, Boolean>())

        try {
            val allWindows = windows ?: emptyList()
            for (win in allWindows) {
                val root = win.root ?: continue
                toRecycle.add(root)
                findFocusedInput(root)?.let { focused ->
                    toRecycle.add(focused)
                    candidates.add(
                        Triple(focused, true, activeWindowId != null && focused.windowId == activeWindowId)
                    )
                }
                collectEditables(root, candidates, toRecycle, activeWindowId, 0)
            }

            if (candidates.isEmpty()) {
                return TypeResult(
                    false,
                    listOf("окно без editable-поля (activeWindowId=$activeWindowId)"),
                    "no editable field found"
                )
            }

            candidates.sortWith(
                compareByDescending<Triple<AccessibilityNodeInfo, Boolean, Boolean>> { it.second && it.third }
                    .thenByDescending { it.second }
                    .thenByDescending { it.third }
            )

            val errors = mutableListOf<String>()
            for ((node, _, _) in candidates) {
                if (!supportsSetText(node)) {
                    errors.add("field has no ACTION_SET_TEXT action")
                    continue
                }
                val args = Bundle().apply {
                    putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
                }
                if (!node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) {
                    errors.add("ACTION_SET_TEXT rejected")
                    continue
                }
                if (pressEnter) {
                    val enterOk = Build.VERSION.SDK_INT >= Build.VERSION_CODES.R &&
                        node.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id)
                    if (!enterOk) errors.add("enter not delivered")
                }
                return TypeResult(true, errors, null)
            }
            return TypeResult(false, errors, "ACTION_SET_TEXT unsupported (custom/WebView field?)")
        } finally {
            for (node in toRecycle) safeRecycle(node)
        }
    }

    /** Обход дерева в поисках узла с focus input (AccessibilityWindowInfo.findFocus недоступен). */
    private fun findFocusedInput(root: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
        if (root == null) return null
        if (root.isFocused && (root.isEditable || root.inputType != 0)) return root
        for (i in 0 until root.childCount) {
            val child = root.getChild(i) ?: continue
            val hit = findFocusedInput(child)
            if (hit == null) continue
            if (hit === child) return child
            safeRecycle(child)
            return hit
        }
        return null
    }

    private fun supportsSetText(node: AccessibilityNodeInfo): Boolean {
        val actions = node.actionList ?: return node.isEditable
        return actions.any { it.id == AccessibilityNodeInfo.ACTION_SET_TEXT }
    }

    private fun collectEditables(
        node: AccessibilityNodeInfo?,
        out: MutableList<Triple<AccessibilityNodeInfo, Boolean, Boolean>>,
        toRecycle: MutableSet<AccessibilityNodeInfo>,
        activeWindowId: Int?,
        depth: Int,
    ) {
        if (node == null || depth > 35 || out.size >= 25) return
        if (node.isEditable && node.isVisibleToUser) {
            out.add(Triple(node, node.isFocused, activeWindowId != null && node.windowId == activeWindowId))
            toRecycle.add(node)
        }
        val count = node.childCount
        for (i in 0 until count) {
            val child = node.getChild(i) ?: continue
            collectEditables(child, out, toRecycle, activeWindowId, depth + 1)
            if (child !in toRecycle) safeRecycle(child)
        }
    }

    data class TypeResult(val ok: Boolean, val warnings: List<String>, val reason: String?)

    fun performKey(key: String): Boolean {
        val action = when (key.uppercase()) {
            "BACK", "KEYCODE_BACK" -> GLOBAL_ACTION_BACK
            "HOME", "KEYCODE_HOME" -> GLOBAL_ACTION_HOME
            "RECENTS", "APP_SWITCH", "KEYCODE_APP_SWITCH" -> GLOBAL_ACTION_RECENTS
            "NOTIFICATIONS", "KEYCODE_NOTIFICATION" -> GLOBAL_ACTION_NOTIFICATIONS
            "QUICK_SETTINGS" -> GLOBAL_ACTION_QUICK_SETTINGS
            "POWER", "POWER_DIALOG", "KEYCODE_POWER" -> GLOBAL_ACTION_POWER_DIALOG
            "LOCK" -> if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) GLOBAL_ACTION_LOCK_SCREEN else return false
            else -> return false
        }
        return performGlobalAction(action)
    }

    fun captureScreenshot(
        maxDim: Int = 1280,
        quality: Int = 82,
        asPng: Boolean = false,
    ): Pair<ByteArray, String>? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return null
        val latch = CountDownLatch(1)
        var result: Pair<ByteArray, String>? = null
        val targetDim = maxDim.coerceIn(320, 2400)
        val targetQuality = quality.coerceIn(40, 96)

        takeScreenshot(Display.DEFAULT_DISPLAY, mainExecutor, object : TakeScreenshotCallback {
            override fun onSuccess(screenshot: ScreenshotResult) {
                var hwBmp: Bitmap? = null
                var softBmp: Bitmap? = null
                var scaledBmp: Bitmap? = null
                try {
                    hwBmp = Bitmap.wrapHardwareBuffer(screenshot.hardwareBuffer, screenshot.colorSpace)
                    if (hwBmp != null) {
                        softBmp = hwBmp.copy(Bitmap.Config.ARGB_8888, false)
                        val w = softBmp.width
                        val h = softBmp.height
                        val longest = max(w, h)
                        val finalBmp = if (longest > targetDim) {
                            val scale = targetDim.toFloat() / longest.toFloat()
                            val nw = max(1, (w * scale).roundToInt())
                            val nh = max(1, (h * scale).roundToInt())
                            scaledBmp = Bitmap.createScaledBitmap(softBmp, nw, nh, true)
                            scaledBmp
                        } else {
                            softBmp
                        }
                        val out = ByteArrayOutputStream(65_536)
                        if (asPng) {
                            finalBmp.compress(Bitmap.CompressFormat.PNG, 100, out)
                            result = out.toByteArray() to "image/png"
                        } else {
                            finalBmp.compress(Bitmap.CompressFormat.JPEG, targetQuality, out)
                            result = out.toByteArray() to "image/jpeg"
                        }
                    }
                } finally {
                    scaledBmp?.recycle()
                    softBmp?.recycle()
                    hwBmp?.recycle()
                    screenshot.hardwareBuffer.close()
                    latch.countDown()
                }
            }

            override fun onFailure(errorCode: Int) {
                latch.countDown()
            }
        })
        latch.await(6, TimeUnit.SECONDS)
        return result
    }

    fun performBatch(body: JSONObject): JSONObject {
        val steps = body.optJSONArray("steps") ?: JSONArray()
        val autoDump = body.optBoolean("auto_dump", true)
        val onlyInteractive = body.optBoolean("only_interactive", false)
        val settleMs = body.optLong("settle_ms", 220L).coerceIn(0L, 3000L)
        val continueOnError = body.optBoolean("continue_on_error", false)

        val results = JSONArray()
        var allOk = true

        for (i in 0 until steps.length().coerceAtMost(20)) {
            val step = steps.optJSONObject(i) ?: continue
            val op = step.optString("op").lowercase()
            val stepRes = JSONObject().put("index", i).put("op", op)
            var ok = false

            when (op) {
                "tap" -> {
                    val x = step.optDouble("x", Double.NaN)
                    val y = step.optDouble("y", Double.NaN)
                    if (!x.isNaN() && !y.isNaN()) {
                        ok = performTap(x.toFloat(), y.toFloat(), step.optLong("duration_ms", 50L))
                    } else {
                        stepRes.put("error", "missing x/y")
                    }
                    stepRes.put("ok", ok)
                }
                "swipe" -> {
                    val x1 = step.optDouble("x1", Double.NaN)
                    val y1 = step.optDouble("y1", Double.NaN)
                    val x2 = step.optDouble("x2", Double.NaN)
                    val y2 = step.optDouble("y2", Double.NaN)
                    if (!x1.isNaN() && !y1.isNaN() && !x2.isNaN() && !y2.isNaN()) {
                        ok = performSwipe(
                            x1.toFloat(), y1.toFloat(), x2.toFloat(), y2.toFloat(),
                            step.optLong("duration_ms", 260L)
                        )
                    } else {
                        stepRes.put("error", "missing swipe coords")
                    }
                    stepRes.put("ok", ok)
                }
                "type" -> {
                    val tr = performTypeText(step.optString("text"), step.optBoolean("press_enter", false))
                    ok = tr.ok
                    stepRes.put("ok", tr.ok)
                    if (tr.warnings.isNotEmpty()) stepRes.put("warnings", JSONArray(tr.warnings))
                    if (tr.reason != null) stepRes.put("reason", tr.reason)
                }
                "key" -> {
                    ok = performKey(step.optString("key"))
                    stepRes.put("ok", ok)
                }
                "app" -> {
                    val ar = handleAppAction(step)
                    ok = ar.optBoolean("ok", false)
                    stepRes.put("ok", ok).put("detail", ar)
                }
                "sleep" -> {
                    val ms = step.optLong("ms", 200L).coerceIn(10L, 5000L)
                    Thread.sleep(ms)
                    ok = true
                    stepRes.put("ok", true).put("ms", ms)
                }
                else -> stepRes.put("ok", false).put("error", "unknown op: $op")
            }

            results.put(stepRes)
            if (!ok) {
                allOk = false
                if (!continueOnError) break
            }
            val delayAfter = step.optLong("delay_ms", 0L).coerceIn(0L, 3000L)
            if (delayAfter > 0) Thread.sleep(delayAfter)
        }

        val response = JSONObject().apply {
            put("ok", allOk)
            put("results", results)
        }
        if (autoDump) {
            if (settleMs > 0) Thread.sleep(settleMs)
            response.put("ui_dump", dumpUi(onlyInteractive))
        }
        return response
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
                    put("ok", true)
                    put("count", pkgs.size)
                    put("packages", JSONArray(pkgs.take(60)))
                }
            }
            "launch" -> {
                val pkg = body.optString("package").trim()
                val intent = packageManager.getLaunchIntentForPackage(pkg)
                    ?: return JSONObject().put("ok", false).put("error", "App not found: $pkg")
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                startActivity(intent)
                JSONObject().put("ok", true).put("package", pkg)
            }
            "open_url" -> {
                val url = body.optString("url").trim()
                val scheme = Uri.parse(url).scheme?.lowercase()
                if (scheme != "http" && scheme != "https") {
                    return JSONObject().put("ok", false).put("error", "Only http(s) URLs are allowed")
                }
                val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                startActivity(intent)
                JSONObject().put("ok", true).put("url", url)
            }
            else -> JSONObject().put("ok", false).put("error", "Unsupported app action")
        }
    }
}
