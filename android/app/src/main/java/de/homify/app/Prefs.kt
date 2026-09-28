package de.homify.app

import android.content.Context
import java.net.HttpURLConnection
import java.net.URL

/** Gespeicherte Server-Adressen: Heimnetz + optional Tailscale für unterwegs. */
object Prefs {
    private const val NAME = "homify"
    private const val DEFAULT_PORT = 8484

    fun primary(ctx: Context): String =
        ctx.getSharedPreferences(NAME, Context.MODE_PRIVATE).getString("primary", "") ?: ""

    fun fallback(ctx: Context): String =
        ctx.getSharedPreferences(NAME, Context.MODE_PRIVATE).getString("fallback", "") ?: ""

    fun save(ctx: Context, primary: String, fallback: String) {
        ctx.getSharedPreferences(NAME, Context.MODE_PRIVATE).edit()
            .putString("primary", primary)
            .putString("fallback", fallback)
            .apply()
    }

    fun hasServer(ctx: Context): Boolean = primary(ctx).isNotEmpty() || fallback(ctx).isNotEmpty()

    /** „192.168.1.20“ -> „http://192.168.1.20:8484“, Schrägstriche am Ende weg. */
    fun normalize(input: String): String {
        var url = input.trim()
        if (url.isEmpty()) return ""
        if (!url.startsWith("http://", ignoreCase = true) && !url.startsWith("https://", ignoreCase = true)) {
            url = "http://$url"
            val hostPart = url.removePrefix("http://").substringBefore("/")
            if (!hostPart.contains(":")) {
                url = "http://$hostPart:$DEFAULT_PORT" + url.removePrefix("http://$hostPart")
            }
        }
        return url.trimEnd('/')
    }
}

object Net {
    /** Prüft, ob unter der Adresse ein Homify-Server antwortet. */
    fun check(base: String, timeoutMs: Int = 3500): String? {
        if (base.isEmpty()) return "keine Adresse"
        return try {
            val conn = URL("$base/api/setup").openConnection() as HttpURLConnection
            conn.connectTimeout = timeoutMs
            conn.readTimeout = timeoutMs
            conn.instanceFollowRedirects = true
            val code = conn.responseCode
            val body = (if (code in 200..299) conn.inputStream else conn.errorStream)
                ?.bufferedReader()?.use { it.readText() } ?: ""
            conn.disconnect()
            when {
                code == 200 && body.contains("Homify") -> null
                code == 200 -> "Dort läuft kein Homify"
                else -> "Server antwortet mit Fehler $code"
            }
        } catch (e: java.net.UnknownHostException) {
            "Adresse unbekannt"
        } catch (e: java.net.SocketTimeoutException) {
            "Zeitüberschreitung"
        } catch (e: Exception) {
            e.message ?: e.javaClass.simpleName
        }
    }

    /** Erste erreichbare Adresse (erst Heimnetz, dann Tailscale). */
    fun pick(primary: String, fallback: String): String? {
        if (primary.isNotEmpty() && check(primary) == null) return primary
        if (fallback.isNotEmpty() && check(fallback) == null) return fallback
        return null
    }
}
