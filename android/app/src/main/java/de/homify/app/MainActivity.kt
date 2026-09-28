package de.homify.app

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.DownloadManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject

/** Die eigentliche App: Homify-Oberfläche vom Server in einer WebView. */
class MainActivity : Activity() {

    companion object {
        const val EXTRA_RELOAD = "reload"
        private const val REQ_FILE = 11
        private const val REQ_NOTIFY = 12

        @Volatile
        var instance: MainActivity? = null

        /** Befehl aus Benachrichtigung / Kopfhörer an den Player in der Weboberfläche. */
        fun sendCommand(cmd: String, arg: Long = 0) {
            val activity = instance ?: return
            activity.runOnUiThread {
                activity.webView.evaluateJavascript(
                    "window.homifyNative && window.homifyNative(${JSONObject.quote(cmd)}, $arg)", null,
                )
            }
        }
    }

    private lateinit var webView: WebView
    private lateinit var overlay: LinearLayout
    private lateinit var overlayText: TextView
    private lateinit var overlaySub: TextView
    private lateinit var overlayButtons: LinearLayout
    private lateinit var progress: ProgressBar
    private var base: String? = null
    private var fileCallback: ValueCallback<Array<Uri>>? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (!Prefs.hasServer(this)) {
            startActivity(Intent(this, SetupActivity::class.java))
            finish()
            return
        }
        instance = this
        buildLayout()
        setupWebView()
        askNotificationPermission()
        connect()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.getBooleanExtra(EXTRA_RELOAD, false)) connect()
    }

    // ------------------------------------------------------------------ Oberfläche
    private fun px(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun buildLayout() {
        val root = FrameLayout(this).apply { setBackgroundColor(Color.parseColor("#121212")) }
        webView = WebView(this).apply { setBackgroundColor(Color.parseColor("#121212")) }
        root.addView(webView, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        overlay = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(Color.parseColor("#121212"))
            setPadding(px(32), px(32), px(32), px(32))
            isClickable = true
        }
        progress = ProgressBar(this)
        overlay.addView(progress)
        overlayText = TextView(this).apply {
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 20f)
            typeface = Typeface.DEFAULT_BOLD
            gravity = Gravity.CENTER
            setPadding(0, px(20), 0, px(8))
        }
        overlaySub = TextView(this).apply {
            setTextColor(Color.parseColor("#B3B3B3"))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            gravity = Gravity.CENTER
        }
        overlayButtons = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(0, px(24), 0, 0)
        }
        overlay.addView(overlayText)
        overlay.addView(overlaySub)
        overlay.addView(overlayButtons)
        root.addView(overlay, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        setContentView(root)
    }

    private fun button(label: String, primary: Boolean, onClick: () -> Unit) = Button(this).apply {
        text = label
        isAllCaps = false
        setTextColor(if (primary) Color.BLACK else Color.WHITE)
        typeface = Typeface.DEFAULT_BOLD
        background = GradientDrawable().apply {
            setColor(Color.parseColor(if (primary) "#1ED760" else "#2A2A2A"))
            cornerRadius = px(24).toFloat()
        }
        setOnClickListener { onClick() }
        layoutParams = LinearLayout.LayoutParams(px(260), px(48)).apply { topMargin = px(10) }
    }

    private fun showLoading(message: String) {
        overlay.visibility = View.VISIBLE
        progress.visibility = View.VISIBLE
        overlayText.text = message
        overlaySub.text = ""
        overlayButtons.removeAllViews()
    }

    private fun showError(detail: String) {
        overlay.visibility = View.VISIBLE
        progress.visibility = View.GONE
        overlayText.text = "Homify-Server nicht erreichbar"
        overlaySub.text = "$detail\n\nBist du im Heimnetz? Unterwegs muss Tailscale auf dem Handy aktiv sein."
        overlayButtons.removeAllViews()
        overlayButtons.addView(button("Erneut versuchen", true) { connect() })
        overlayButtons.addView(button("Server-Adresse ändern", false) { openSettings() })
    }

    // ------------------------------------------------------------------ WebView
    @SuppressLint("SetJavaScriptEnabled", "JavascriptInterface")
    private fun setupWebView() {
        val s = webView.settings
        s.javaScriptEnabled = true
        s.domStorageEnabled = true
        s.mediaPlaybackRequiresUserGesture = false
        s.loadWithOverviewMode = true
        s.useWideViewPort = true
        s.setSupportZoom(false)
        s.cacheMode = WebSettings.LOAD_DEFAULT
        s.userAgentString = s.userAgentString + " HomifyAndroid/" + appVersion()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            // Wiedergabe soll weiterlaufen, auch wenn die App im Hintergrund ist
            webView.setRendererPriorityPolicy(WebView.RENDERER_PRIORITY_IMPORTANT, false)
        }
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, false)

        webView.addJavascriptInterface(Bridge(), "HomifyAndroid")
        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url
                val current = base?.let { Uri.parse(it) }
                if (current != null && url.host == current.host && url.port == current.port) return false
                // Fremde Links (GitHub, Tailscale …) im Browser öffnen
                try {
                    startActivity(Intent(Intent.ACTION_VIEW, url))
                } catch (_: Exception) {
                }
                return true
            }

            override fun onPageFinished(view: WebView, url: String) {
                overlay.visibility = View.GONE
                CookieManager.getInstance().flush()
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) {
                    showError(error.description?.toString() ?: "Verbindungsfehler")
                }
            }
        }
        webView.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                view: WebView,
                callback: ValueCallback<Array<Uri>>,
                params: FileChooserParams,
            ): Boolean {
                fileCallback?.onReceiveValue(null)
                fileCallback = callback
                return try {
                    startActivityForResult(params.createIntent(), REQ_FILE)
                    true
                } catch (_: Exception) {
                    fileCallback = null
                    false
                }
            }
        }
        webView.setDownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
            // „Datei aufs Gerät laden“: mit Anmelde-Cookie über den Android-Download-Manager
            try {
                val name = URLUtil.guessFileName(url, contentDisposition, mimeType)
                val request = DownloadManager.Request(Uri.parse(url))
                    .addRequestHeader("Cookie", CookieManager.getInstance().getCookie(url) ?: "")
                    .addRequestHeader("User-Agent", userAgent)
                    .setTitle(name)
                    .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                    .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name)
                (getSystemService(DOWNLOAD_SERVICE) as DownloadManager).enqueue(request)
                Toast.makeText(this, "Download gestartet: $name", Toast.LENGTH_SHORT).show()
            } catch (e: Exception) {
                Toast.makeText(this, "Download fehlgeschlagen: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun connect() {
        showLoading("Verbinde mit Homify …")
        val primary = Prefs.primary(this)
        val fallback = Prefs.fallback(this)
        Thread {
            val picked = Net.pick(primary, fallback)
            val detail = if (picked == null) Net.check(primary.ifEmpty { fallback }) ?: "" else ""
            runOnUiThread {
                if (picked != null) {
                    base = picked
                    webView.loadUrl(picked)
                } else {
                    showError(detail)
                }
            }
        }.start()
    }

    private fun openSettings() {
        startActivity(Intent(this, SetupActivity::class.java))
    }

    private fun appVersion(): String = try {
        packageManager.getPackageInfo(packageName, 0).versionName ?: "1"
    } catch (_: Exception) {
        "1"
    }

    private fun askNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_NOTIFY)
        }
    }

    // ------------------------------------------------------------------ Lebenszyklus
    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode == REQ_FILE) {
            fileCallback?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data))
            fileCallback = null
            return
        }
        super.onActivityResult(requestCode, resultCode, data)
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack()
        } else {
            moveTaskToBack(true) // App nicht beenden – Musik läuft weiter
        }
    }

    override fun onPause() {
        super.onPause()
        CookieManager.getInstance().flush()
        // Absichtlich KEIN webView.onPause(): sonst stoppt die Musik im Hintergrund
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        if (::webView.isInitialized) {
            webView.destroy()
        }
        stopService(Intent(this, PlaybackService::class.java))
        super.onDestroy()
    }

    /** Schnittstelle für die Weboberfläche: window.HomifyAndroid */
    inner class Bridge {
        @JavascriptInterface
        fun updateState(json: String) {
            PlaybackService.update(applicationContext, json)
        }

        @JavascriptInterface
        fun openSettings() {
            runOnUiThread { this@MainActivity.openSettings() }
        }

        @JavascriptInterface
        fun version(): String = appVersion()

        @JavascriptInterface
        fun serverBase(): String = base ?: ""
    }
}
