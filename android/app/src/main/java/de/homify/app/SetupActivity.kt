package de.homify.app

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/** Erster Start: Adresse des Homify-Servers eintragen. */
class SetupActivity : Activity() {

    private var forceSave = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val density = resources.displayMetrics.density
        fun px(v: Int) = (v * density).toInt()

        val scroll = ScrollView(this).apply {
            setBackgroundColor(Color.parseColor("#121212"))
            isFillViewport = true
        }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(px(24), px(40), px(24), px(24))
        }
        scroll.addView(col)

        val logo = ImageView(this).apply { setImageResource(R.mipmap.ic_launcher) }
        col.addView(logo, LinearLayout.LayoutParams(px(72), px(72)).apply { bottomMargin = px(20) })

        fun text(value: String, sizeSp: Float, color: String, bold: Boolean = false) = TextView(this).apply {
            text = value
            setTextColor(Color.parseColor(color))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, sizeSp)
            if (bold) typeface = Typeface.DEFAULT_BOLD
        }

        col.addView(text("Mit deinem Homify-Server verbinden", 24f, "#FFFFFF", bold = true))
        col.addView(text(
            "Die Adresse steht in Homify unter Einstellungen → Apps. Zuhause z. B. http://192.168.1.20:8484, " +
                "unterwegs die Tailscale-Adresse (https://…ts.net).",
            14f, "#B3B3B3",
        ).apply { setPadding(0, px(8), 0, px(24)) })

        fun field(label: String, hint: String, value: String): EditText {
            col.addView(text(label, 13f, "#FFFFFF", bold = true).apply { setPadding(0, px(8), 0, px(6)) })
            val edit = EditText(this).apply {
                setText(value)
                this.hint = hint
                setTextColor(Color.WHITE)
                setHintTextColor(Color.parseColor("#727272"))
                inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
                isSingleLine = true
                background = GradientDrawable().apply {
                    setColor(Color.parseColor("#2A2A2A"))
                    cornerRadius = px(6).toFloat()
                }
                setPadding(px(12), px(12), px(12), px(12))
            }
            col.addView(edit, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT))
            return edit
        }

        val primary = field("Server-Adresse (Heimnetz)", "http://192.168.1.20:8484", Prefs.primary(this))
        val fallback = field("Adresse für unterwegs (optional, Tailscale)", "https://homeserver.tailxxxx.ts.net", Prefs.fallback(this))

        val status = text("", 14f, "#B3B3B3").apply { setPadding(0, px(16), 0, px(8)) }
        col.addView(status)

        val button = Button(this).apply {
            text = "Verbinden"
            isAllCaps = false
            setTextColor(Color.BLACK)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            typeface = Typeface.DEFAULT_BOLD
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#1ED760"))
                cornerRadius = px(28).toFloat()
            }
        }
        col.addView(button, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, px(52)).apply { topMargin = px(8) })

        val tip = text(
            "Tipp: Für unterwegs die Tailscale-App auf dem Handy installieren und mit demselben Konto anmelden wie der Server.",
            12f, "#727272",
        ).apply {
            setPadding(0, px(24), 0, 0)
            gravity = Gravity.START
        }
        col.addView(tip)
        setContentView(scroll)

        button.setOnClickListener {
            val p = Prefs.normalize(primary.text.toString())
            val f = Prefs.normalize(fallback.text.toString())
            if (p.isEmpty() && f.isEmpty()) {
                status.text = "Bitte mindestens eine Adresse eintragen."
                return@setOnClickListener
            }
            if (forceSave) {
                finishWith(p, f)
                return@setOnClickListener
            }
            button.isEnabled = false
            status.setTextColor(Color.parseColor("#B3B3B3"))
            status.text = "Verbinde …"
            Thread {
                val errP = if (p.isEmpty()) "leer" else Net.check(p)
                val errF = if (f.isEmpty()) "leer" else Net.check(f)
                runOnUiThread {
                    button.isEnabled = true
                    if (errP == null || errF == null) {
                        finishWith(p, f)
                    } else {
                        status.setTextColor(Color.parseColor("#F3727F"))
                        status.text = "Server nicht erreichbar (" + (if (p.isNotEmpty()) errP else errF) + "). " +
                            "Bist du im selben WLAN bzw. ist Tailscale an? Adresse prüfen oder trotzdem speichern."
                        button.text = "Trotzdem speichern"
                        forceSave = true
                    }
                }
            }.start()
        }
        primary.setOnFocusChangeListener { _: View, _: Boolean -> resetForce(button) }
        fallback.setOnFocusChangeListener { _: View, _: Boolean -> resetForce(button) }
    }

    private fun resetForce(button: Button) {
        if (forceSave) {
            forceSave = false
            button.text = "Verbinden"
        }
    }

    private fun finishWith(primary: String, fallback: String) {
        Prefs.save(this, primary, fallback)
        val intent = Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_NEW_TASK)
            .putExtra(MainActivity.EXTRA_RELOAD, true)
        startActivity(intent)
        finish()
    }
}
