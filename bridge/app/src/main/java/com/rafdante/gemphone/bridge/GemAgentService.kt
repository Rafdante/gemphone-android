package com.rafdante.gemphone.bridge

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import android.os.PowerManager

/**
 * Foreground-сервис «агент работает в фоне».
 * Держит процесс бриджа живым, показывает уведомление и плавающую капсулу статуса,
 * чтобы пользователь мог спокойно работать в других приложениях.
 */
class GemAgentService : Service() {
    private var wakeLock: PowerManager.WakeLock? = null

    private fun channelId() = "gemphone_agent"

    private fun ensureChannel(nm: NotificationManager) {
        if (Build.VERSION.SDK_INT >= 26) {
            val ch = NotificationChannel(
                channelId(),
                "GemPhone агент",
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = "Статус фоновой работы ИИ-агента"
                setShowBadge(false)
            }
            nm.createNotificationChannel(ch)
        }
    }

    private fun buildNotification(text: String): Notification {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        ensureChannel(nm)
        val open = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val stop = PendingIntent.getService(
            this, 1,
            Intent(this, GemAgentService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val builder = if (Build.VERSION.SDK_INT >= 26)
            Notification.Builder(this, channelId())
        else
            @Suppress("DEPRECATION") Notification.Builder(this)
        builder.setContentTitle("GemPhone: агент работает")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_menu_compass)
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(
                Notification.Action.Builder(
                    null, "Стоп",
                    stop,
                ).build(),
            )
        return builder.build()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val builder = if (Build.VERSION.SDK_INT >= 26)
            Notification.Builder(this, channelId())
        else
            @Suppress("DEPRECATION") Notification.Builder(this)
        builder.setContentTitle("GemPhone: агент работает")
            .setContentText("Фоновая сессия активна")
            .setSmallIcon(android.R.drawable.ic_menu_compass)
        ensureChannel(nm)
        try {
            if (Build.VERSION.SDK_INT >= 29) {
                startForeground(NOTIFICATION_ID, builder.build(), 0x40000000)
            } else {
                startForeground(NOTIFICATION_ID, builder.build())
            }
        } catch (e: Exception) {
            android.util.Log.w("GemAgentService", "startForeground failed: ${e.message}")
        }
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "GemPhone:agent").apply {
            setReferenceCounted(false)
            try {
                acquire(4 * 60 * 60 * 1000L)
            } catch (_: Exception) {
            }
        }
        GemOverlay.show(this)
        GemOverlay.update("работает", "сессия в фоне")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        val text = intent?.getStringExtra(EXTRA_TEXT) ?: "Фоновая сессия активна"
        val detail = intent?.getStringExtra(EXTRA_DETAIL)
        GemOverlay.update(text, detail)
        try {
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            nm.notify(NOTIFICATION_ID, buildNotification(text))
        } catch (_: Exception) {
        }
        val overlay = intent?.getBooleanExtra(EXTRA_OVERLAY, true) ?: true
        if (overlay) GemOverlay.show(this) else GemOverlay.hide()
        return START_STICKY
    }

    override fun onDestroy() {
        GemOverlay.hide()
        try {
            wakeLock?.let { if (it.isHeld) it.release() }
        } catch (_: Exception) {
        }
        wakeLock = null
        super.onDestroy()
    }

    companion object {
        const val NOTIFICATION_ID = 7331
        const val ACTION_STOP = "com.rafdante.gemphone.bridge.STOP_AGENT"
        const val EXTRA_TEXT = "text"
        const val EXTRA_DETAIL = "detail"
        const val EXTRA_OVERLAY = "overlay"

        fun start(ctx: Context, text: String?, detail: String? = null, overlay: Boolean = true) {
            val i = Intent(ctx, GemAgentService::class.java)
                .putExtra(EXTRA_TEXT, text)
                .putExtra(EXTRA_DETAIL, detail)
                .putExtra(EXTRA_OVERLAY, overlay)
            if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i) else ctx.startService(i)
        }

        /** Обновление статуса без перезапуска сервиса. */
        fun update(ctx: Context, text: String, detail: String? = null) {
            start(ctx, text, detail, true)
        }

        fun stop(ctx: Context) {
            ctx.stopService(Intent(ctx, GemAgentService::class.java))
            GemOverlay.hide()
        }
    }
}
