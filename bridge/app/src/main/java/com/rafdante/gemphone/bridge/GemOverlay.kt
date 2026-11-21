package com.rafdante.gemphone.bridge

import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Плавающая капсула статуса агента поверх всех приложений.
 * Клик — свернуть/развернуть. Показывает, что агент работает в фоне,
 * не забирая у пользователя экран.
 */
object GemOverlay {
    private var view: View? = null
    private var wm: WindowManager? = null
    private var title: TextView? = null
    private var detail: TextView? = null
    private var expanded = true
    private val main = Handler(Looper.getMainLooper())

    fun show(ctx: Context) {
        main.post {
            if (view != null) return@post
            val app = ctx.applicationContext
            val manager = app.getSystemService(Context.WINDOW_SERVICE) as WindowManager
            val params = WindowManager.LayoutParams(
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.WRAP_CONTENT,
                if (Build.VERSION.SDK_INT >= 26)
                    WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                else
                    @Suppress("DEPRECATION") WindowManager.LayoutParams.TYPE_PHONE,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                    or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
                    or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                PixelFormat.TRANSLUCENT,
            )
            params.gravity = Gravity.TOP or Gravity.START
            params.x = 24
            params.y = 140

            val box = LinearLayout(app).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(28, 18, 28, 18)
                background = GradientDrawable().apply {
                    cornerRadius = 46f
                    setColor(Color.parseColor("#E6101418"))
                    setStroke(2, Color.parseColor("#5B8DEF"))
                }
            }
            title = TextView(app).apply {
                text = "GemPhone: работает…"
                textSize = 14f
                setTextColor(Color.WHITE)
            }
            detail = TextView(app).apply {
                text = ""
                textSize = 11f
                setTextColor(Color.parseColor("#9AA4B2"))
                setPadding(0, 4, 0, 0)
            }
            box.addView(title)
            box.addView(detail)

            val drag = object : FrameLayout(app) {
                private var startX = 0
                private var startY = 0
                private var touchX = 0f
                private var touchY = 0f
                private var moved = false
                init {
                    addView(box)
                    setOnTouchListener { _, ev ->
                        when (ev.action) {
                            android.view.MotionEvent.ACTION_DOWN -> {
                                startX = params.x
                                startY = params.y
                                touchX = ev.rawX
                                touchY = ev.rawY
                                moved = false
                                true
                            }
                            android.view.MotionEvent.ACTION_MOVE -> {
                                val dx = (ev.rawX - touchX).toInt()
                                val dy = (ev.rawY - touchY).toInt()
                                if (kotlin.math.abs(dx) > 12 || kotlin.math.abs(dy) > 12) moved = true
                                params.x = startX + dx
                                params.y = startY + dy
                                manager.updateViewLayout(this, params)
                                true
                            }
                            android.view.MotionEvent.ACTION_UP -> {
                                if (!moved) {
                                    expanded = !expanded
                                    detail?.visibility = if (expanded) View.VISIBLE else View.GONE
                                }
                                true
                            }
                            else -> false
                        }
                    }
                }
            }
            manager.addView(drag, params)
            view = drag
            wm = manager
        }
    }

    fun update(state: String, info: String? = null) {
        main.post {
            title?.text = "GemPhone: $state"
            detail?.text = info ?: ""
            detail?.visibility =
                if (expanded && !info.isNullOrBlank()) View.VISIBLE else View.GONE
        }
    }

    fun hide() {
        main.post {
            view?.let { wm?.removeView(it) }
            view = null
            wm = null
            title = null
            detail = null
        }
    }

    fun isShown(): Boolean = view != null
}
