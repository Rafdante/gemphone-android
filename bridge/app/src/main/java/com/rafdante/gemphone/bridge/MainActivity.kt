package com.rafdante.gemphone.bridge

import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast

class MainActivity : Activity() {
    private lateinit var statusView: TextView
    private lateinit var tokenView: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.parseColor("#0F1115"))
            setPadding(48, 80, 48, 48)
        }

        val title = TextView(this).apply {
            text = "GemPhone Bridge"
            textSize = 24f
            setTextColor(Color.WHITE)
        }
        statusView = TextView(this).apply {
            textSize = 15f
            setPadding(0, 24, 0, 16)
        }
        tokenView = TextView(this).apply {
            textSize = 13f
            setTextColor(Color.parseColor("#A5B4FC"))
            setPadding(0, 0, 0, 28)
        }

        val btnAppInfo = Button(this).apply {
            text = "0. Android 13+: Разрешить огр. настройки (⋮)"
            setOnClickListener {
                val i = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName"))
                startActivity(i)
            }
        }
        val btnSettings = Button(this).apply {
            text = "1. Включить в Спец. возможностях"
            setOnClickListener { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }
        }
        val btnCopy = Button(this).apply {
            text = "2. Скопировать команду для GemPhone"
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

        listOf(title, statusView, tokenView, btnAppInfo, btnSettings, btnCopy, btnRotate).forEach { root.addView(it) }
        setContentView(root)
    }

    override fun onResume() {
        super.onResume()
        refreshUi()
    }

    private fun refreshUi() {
        val active = GemAccessibilityService.instance != null
        statusView.text = if (active) "● Сервис активен (127.0.0.1:7331)" else "○ Сервис выключен — включи ниже"
        statusView.setTextColor(if (active) Color.parseColor("#4ADE80") else Color.parseColor("#FACC15"))
        val tok = GemAccessibilityService.getOrCreateToken(this)
        tokenView.text = "Token: ${tok.take(8)}...${tok.takeLast(6)}"
    }
}
