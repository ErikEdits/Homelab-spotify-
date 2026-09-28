package de.homify.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.drawable.Icon
import android.media.MediaMetadata
import android.media.session.MediaSession
import android.media.session.PlaybackState
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.webkit.CookieManager
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * Hält die Wiedergabe im Hintergrund am Leben und zeigt die Steuerung in der
 * Benachrichtigung, auf dem Sperrbildschirm und für Bluetooth-Kopfhörer.
 * Den Zustand meldet die Weboberfläche über window.HomifyAndroid.updateState().
 */
class PlaybackService : Service() {

    companion object {
        private const val CHANNEL_ID = "playback"
        private const val NOTIFICATION_ID = 42
        private const val ACTION_UPDATE = "de.homify.app.UPDATE"
        private const val ACTION_COMMAND = "de.homify.app.COMMAND"
        private const val EXTRA_JSON = "json"
        private const val EXTRA_COMMAND = "command"

        @Volatile
        var running = false

        @Volatile
        private var instance: PlaybackService? = null

        private val mainHandler = Handler(Looper.getMainLooper())

        /**
         * Neuer Zustand aus der Weboberfläche. Läuft der Dienst schon, wird er direkt
         * aktualisiert (Android verbietet Neustarts von Vordergrunddiensten aus dem Hintergrund).
         */
        fun update(context: Context, json: String) {
            val state = try {
                JSONObject(json)
            } catch (_: Exception) {
                return
            }
            val service = instance
            if (service != null) {
                mainHandler.post { service.handleState(state) }
                return
            }
            if (state.optString("title").isEmpty() || !state.optBoolean("playing")) return
            val intent = Intent(context, PlaybackService::class.java)
                .setAction(ACTION_UPDATE)
                .putExtra(EXTRA_JSON, json)
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    context.startForegroundService(intent)
                } else {
                    context.startService(intent)
                }
            } catch (_: Exception) {
                // Start nicht erlaubt (App im Hintergrund) – beim nächsten Update erneut
            }
        }
    }

    private lateinit var session: MediaSession
    private var state = JSONObject()
    private var artUrl = ""
    private var art: Bitmap? = null
    private var foreground = false
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        running = true
        instance = this
        createChannel()
        session = MediaSession(this, "Homify").apply {
            setCallback(object : MediaSession.Callback() {
                override fun onPlay() = MainActivity.sendCommand("play")
                override fun onPause() = MainActivity.sendCommand("pause")
                override fun onSkipToNext() = MainActivity.sendCommand("next")
                override fun onSkipToPrevious() = MainActivity.sendCommand("prev")
                override fun onStop() = MainActivity.sendCommand("pause")
                override fun onSeekTo(pos: Long) = MainActivity.sendCommand("seek", pos)
            })
            setSessionActivity(openAppIntent())
            isActive = true
        }
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "homify:playback").apply { setReferenceCounted(false) }
        val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        @Suppress("DEPRECATION")
        val mode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) WifiManager.WIFI_MODE_FULL_LOW_LATENCY else WifiManager.WIFI_MODE_FULL_HIGH_PERF
        wifiLock = wm.createWifiLock(mode, "homify:playback").apply { setReferenceCounted(false) }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_UPDATE -> {
                handleState(
                    try {
                        JSONObject(intent.getStringExtra(EXTRA_JSON) ?: "{}")
                    } catch (_: Exception) {
                        JSONObject()
                    },
                )
            }
            ACTION_COMMAND -> {
                val cmd = intent.getStringExtra(EXTRA_COMMAND) ?: ""
                if (cmd == "close") {
                    MainActivity.sendCommand("pause")
                    stopForegroundCompat(true)
                    stopSelf()
                } else {
                    MainActivity.sendCommand(cmd)
                }
            }
            else -> if (!foreground && state.optString("title").isEmpty()) stopSelf()
        }
        return START_NOT_STICKY
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        // App aus der Übersicht gewischt -> Wiedergabe endet
        stopForegroundCompat(true)
        stopSelf()
        super.onTaskRemoved(rootIntent)
    }

    override fun onDestroy() {
        running = false
        if (instance === this) instance = null
        releaseLocks()
        session.isActive = false
        session.release()
        super.onDestroy()
    }

    // ------------------------------------------------------------------ Zustand anwenden
    fun handleState(newState: JSONObject) {
        state = newState
        apply()
    }

    private fun apply() {
        val title = state.optString("title")
        if (title.isEmpty()) {
            stopForegroundCompat(true)
            stopSelf()
            return
        }
        val playing = state.optBoolean("playing")
        val positionMs = (state.optDouble("position", 0.0) * 1000).toLong()
        val durationMs = (state.optDouble("duration", 0.0) * 1000).toLong()

        val newArt = state.optString("artwork")
        if (newArt != artUrl) {
            artUrl = newArt
            art = null
            if (newArt.isNotEmpty()) loadArtwork(newArt)
        }

        val meta = MediaMetadata.Builder()
            .putString(MediaMetadata.METADATA_KEY_TITLE, title)
            .putString(MediaMetadata.METADATA_KEY_ARTIST, state.optString("artist"))
            .putString(MediaMetadata.METADATA_KEY_ALBUM, state.optString("album"))
            .putLong(MediaMetadata.METADATA_KEY_DURATION, durationMs)
        art?.let { meta.putBitmap(MediaMetadata.METADATA_KEY_ALBUM_ART, it) }
        session.setMetadata(meta.build())

        val actions = PlaybackState.ACTION_PLAY or PlaybackState.ACTION_PAUSE or PlaybackState.ACTION_PLAY_PAUSE or
            PlaybackState.ACTION_SKIP_TO_NEXT or PlaybackState.ACTION_SKIP_TO_PREVIOUS or
            PlaybackState.ACTION_SEEK_TO or PlaybackState.ACTION_STOP
        session.setPlaybackState(
            PlaybackState.Builder()
                .setActions(actions)
                .setState(
                    if (playing) PlaybackState.STATE_PLAYING else PlaybackState.STATE_PAUSED,
                    positionMs,
                    if (playing) 1f else 0f,
                )
                .build(),
        )

        val notification = buildNotification(title, playing)
        if (!foreground) {
            // Einmal in den Vordergrund – danach nur noch die Benachrichtigung aktualisieren
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK)
            } else {
                startForeground(NOTIFICATION_ID, notification)
            }
            foreground = true
        } else {
            (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).notify(NOTIFICATION_ID, notification)
        }
        if (playing) acquireLocks() else releaseLocks()
    }

    private fun buildNotification(title: String, playing: Boolean): Notification {
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        builder
            .setSmallIcon(R.drawable.ic_stat_homify)
            .setContentTitle(title)
            .setContentText(state.optString("artist"))
            .setSubText(state.optString("album"))
            .setContentIntent(openAppIntent())
            .setDeleteIntent(commandIntent("close", 9))
            .setVisibility(Notification.VISIBILITY_PUBLIC)
            .setOngoing(true)
            .setShowWhen(false)
            .addAction(action(android.R.drawable.ic_media_previous, "Zurück", "prev", 1))
            .addAction(
                if (playing) action(android.R.drawable.ic_media_pause, "Pause", "pause", 2)
                else action(android.R.drawable.ic_media_play, "Wiedergabe", "play", 3),
            )
            .addAction(action(android.R.drawable.ic_media_next, "Weiter", "next", 4))
            .addAction(action(android.R.drawable.ic_menu_close_clear_cancel, "Beenden", "close", 5))
            .setStyle(
                Notification.MediaStyle()
                    .setMediaSession(session.sessionToken)
                    .setShowActionsInCompactView(0, 1, 2),
            )
        art?.let { builder.setLargeIcon(it) }
        return builder.build()
    }

    private fun action(icon: Int, label: String, cmd: String, code: Int): Notification.Action =
        Notification.Action.Builder(Icon.createWithResource(this, icon), label, commandIntent(cmd, code)).build()

    private fun commandIntent(cmd: String, code: Int): PendingIntent {
        val intent = Intent(this, PlaybackService::class.java)
            .setAction(ACTION_COMMAND)
            .putExtra(EXTRA_COMMAND, cmd)
        return PendingIntent.getService(this, code, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    private fun openAppIntent(): PendingIntent {
        val intent = Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP)
        return PendingIntent.getActivity(this, 0, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(CHANNEL_ID, "Wiedergabe", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Steuerung der laufenden Musik"
                setShowBadge(false)
            }
            (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).createNotificationChannel(channel)
        }
    }

    private fun stopForegroundCompat(remove: Boolean) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(if (remove) STOP_FOREGROUND_REMOVE else STOP_FOREGROUND_DETACH)
        } else {
            @Suppress("DEPRECATION")
            stopForeground(remove)
        }
        foreground = false
    }

    private fun acquireLocks() {
        try {
            if (wakeLock?.isHeld != true) wakeLock?.acquire(6 * 60 * 60 * 1000L)
            if (wifiLock?.isHeld != true) wifiLock?.acquire()
        } catch (_: Exception) {
        }
    }

    private fun releaseLocks() {
        try {
            if (wakeLock?.isHeld == true) wakeLock?.release()
            if (wifiLock?.isHeld == true) wifiLock?.release()
        } catch (_: Exception) {
        }
    }

    /** Cover vom Server laden (mit Anmelde-Cookie der WebView). */
    private fun loadArtwork(url: String) {
        Thread {
            val bitmap = try {
                val conn = URL(url).openConnection() as HttpURLConnection
                conn.connectTimeout = 5000
                conn.readTimeout = 8000
                CookieManager.getInstance().getCookie(url)?.let { conn.setRequestProperty("Cookie", it) }
                conn.inputStream.use { BitmapFactory.decodeStream(it) }
            } catch (_: Exception) {
                null
            }
            mainHandler.post {
                if (url == artUrl && bitmap != null && running) {
                    art = bitmap
                    apply()
                }
            }
        }.start()
    }
}
