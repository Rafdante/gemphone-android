package com.rafdante.gemphone.bridge

import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast

class MainActivity : Activity() {
    private lateinit var statusView: TextView
    private lateinit var batteryView: TextView
    private lateinit var tokenView: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.parseColor("#0F1115"))
            setPadding(48, 80, 48, 48)
        }

        val title = TextView(this).apply {
            text = "GemPhone Bridge 3.9"
            textSize = 24f
            setTextColor(Color.WHITE)
        }
        statusView = TextView(this).apply {
            textSize = 15f
            setPadding(0, 24, 0, 8)
        }
        batteryView = TextView(this).apply {
            textSize = 13f
            setPadding(0, 0, 0, 12)
        }
        tokenView = TextView(this).apply {
            textSize = 13f
            setTextColor(Color.parseColor("#A5B4FC"))
            setPadding(0, 0, 0, 24)
        }

        val btnAppInfo = Button(this).apply {
            text = "0. Android 13+: Разрешить огр. настройки (⋮)"
            setOnClickListener {
                val i = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName"))
                startActivity(i)
            }
        }
        val btnBattery = Button(this).apply {
            text = "1. Фон без ограничений (оптимизация батареи)"
            setOnClickListener {
                try {
                    startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
                } catch (_: Exception) {
                    startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
                }
            }
        }
        val btnSettings = Button(this).apply {
            text = "2. Включить в Спец. возможностях"
            setOnClickListener { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }
        }
        val btnAgent = Button(this).apply {
            text = "▶ Агент в фоне (капсула поверх приложений)"
            setOnClickListener {
                if (!Settings.canDrawOverlays(this@MainActivity)) {
                    try {
                        try {
                            startActivity(
                                Intent(
                                    "android.settings.action.MANAGE_OVERLAY_PERMISSION",
                                    Uri.parse("package:$packageName"),
                                ),
                            )
                        } catch (_: Exception) {
                            startActivity(Intent(Settings.ACTION_SETTINGS))
                        }
                    } catch (_: Exception) {
                    }
                    return@setOnClickListener
                }
                GemAgentService.start(this@MainActivity, "работает", "можно пользоваться телефоном")
            }
        }
        val btnAgentStop = Button(this).apply {
            text = "■ Остановить фоновый режим"
            setOnClickListener { GemAgentService.stop(this@MainActivity) }
        }
        val btnCopy = Button(this).apply {
            text = "3. Скопировать команду для GemPhone"
            setOnClickListener {
                val tok = GemAccessibilityService.getOrCreateToken(this@MainActivity)
                val cmd = "/bridge token $tok"
                val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                cm.setPrimaryClip(ClipData.newPlainText("GemPhone Bridge Token", cmd))
                Toast.makeText(this@MainActivity, "Скопировано: вставь в GemPhone", Toast.LENGTH_SHORT).show()
            }
        }
        val btnRotate = Button(this).apply {
            text = "Обновить токен"
            setOnClickListener {
                GemAccessibilityService.rotateToken(this@MainActivity)
                refreshUi()
            }
        }

        listOf(title, statusView, batteryView, tokenView, btnAppInfo, btnBattery, btnSettings, btnAgent, btnAgentStop, btnCopy, btnRotate)
            .forEach { root.addView(it) }
        setContentView(root)
    }

    override fun onResume() {
        super.onResume()
        refreshUi()
    }

    private fun refreshUi() {
        val inst = GemAccessibilityService.instance
        val active = inst != null
        val listening = active && (inst!!.httpServer?.isListening() == true)
        val err = inst?.httpServer?.lastError
        statusView.text = when {
            listening -> "● Сервис активен (127.0.0.1:7331 · Keep-Alive)"
            active && err != null -> "⚠ Сервис включён, но порт не слушается: $err"
            active -> "● Сервис активен (ожидание порта…)"
            else -> "○ Сервис выключен — включи ниже"
        }
        statusView.setTextColor(
            if (listening) Color.parseColor("#4ADE80") else Color.parseColor("#FACC15")
        )

        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        val unrestricted = Build.VERSION.SDK_INT < Build.VERSION_CODES.M || pm.isIgnoringBatteryOptimizations(packageName)
        batteryView.text = if (unrestricted) {
            "● Фон: без ограничений батареи"
        } else {
            "⚠ Фон: включена оптимизация батареи (рекомендуется отключить)"
        }
        batteryView.setTextColor(
            if (unrestricted) Color.parseColor("#4ADE80") else Color.parseColor("#FACC15")
        )

        val tok = GemAccessibilityService.getOrCreateToken(this)
        tokenView.text = "Token: ${tok.take(8)}...${tok.takeLast(6)}"
    }
}
