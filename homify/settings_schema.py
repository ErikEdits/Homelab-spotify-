"""
Alle 100 Einstellungen von Homify an einem Ort.

- scope "user":   gilt pro Benutzer (Wiedergabe, Aussehen, Startseite …), auf allen Geräten gleich
- scope "server": gilt für den ganzen Server (nur Admins)

Die Weboberfläche baut die Einstellungsseite automatisch aus dieser Liste.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Setting:
    key: str
    scope: str            # "user" | "server"
    category: str         # Gruppe auf der Einstellungsseite
    type: str             # bool | int | select | text | password
    default: Any
    label: str
    help: str = ""
    options: list[tuple[Any, str]] = field(default_factory=list)
    min: float | None = None
    max: float | None = None
    step: float | None = None
    unit: str = ""
    restart: bool = False  # wirkt erst nach Neustart des Servers


def _s(key, scope, category, type_, default, label, help_="", **kw) -> Setting:
    return Setting(key, scope, category, type_, default, label, help_, **kw)


QUALITY = [("original", "Original (beste Qualität)"), ("high", "Hoch (bis 320 kbit/s)"),
           ("normal", "Normal (192 kbit/s)"), ("low", "Niedrig (128 kbit/s)"),
           ("minimal", "Datensparend (64 kbit/s)")]
# Dieselbe Auswahl wie im Spotify-Equalizer (gleiche 6 Frequenzen: 60 Hz … 15 kHz)
EQ_PRESETS = [("flat", "Aus (neutral)"), ("acoustic", "Akustik"), ("bass", "Bass-Booster"),
              ("bass_reducer", "Bass-Reduzierer"), ("classical", "Klassik"), ("dance", "Dance"),
              ("deep", "Deep"), ("electronic", "Elektronisch"), ("hiphop", "Hip-Hop"), ("jazz", "Jazz"),
              ("latin", "Latin"), ("loudness", "Loudness (leise hören)"), ("lounge", "Lounge"),
              ("piano", "Piano"), ("pop", "Pop"), ("rnb", "R&B"), ("rock", "Rock"),
              ("small_speakers", "Kleine Lautsprecher"), ("spoken_word", "Gesprochenes Wort"),
              ("treble", "Höhen-Booster"), ("treble_reducer", "Höhen-Reduzierer"), ("vocal", "Stimmen-Booster"),
              ("custom", "Eigene Einstellung")]
EQ_BANDS = [("eq_60", "60 Hz"), ("eq_150", "150 Hz"), ("eq_400", "400 Hz"), ("eq_1k", "1 kHz"),
            ("eq_2k4", "2,4 kHz"), ("eq_15k", "15 kHz")]
PAGES = [("home", "Startseite"), ("search", "Suche"), ("library", "Bibliothek"), ("liked", "Lieblingssongs"),
         ("downloads", "Downloads")]

SETTINGS: list[Setting] = [
    # ================================================================ Wiedergabe (Benutzer)
    _s("quality_wifi", "user", "Wiedergabe", "select", "original", "Streaming-Qualität im WLAN/LAN",
       "Formate, die das Gerät nicht kann, werden immer automatisch umgewandelt.", options=QUALITY),
    _s("quality_mobile", "user", "Wiedergabe", "select", "low", "Streaming-Qualität bei mobilen Daten",
       "Gilt, wenn das Handy über Mobilfunk verbunden ist oder der Datensparmodus an ist.", options=QUALITY),
    _s("crossfade", "user", "Wiedergabe", "int", 0, "Überblenden",
       "Songs gehen fließend ineinander über. 0 = aus.", min=0, max=12, step=1, unit="s"),
    _s("gapless", "user", "Wiedergabe", "bool", True, "Nächsten Song vorladen",
       "Der nächste Song startet ohne Pause (lückenlose Wiedergabe). In Playlists und Mixen wird lange Stille "
       "am Anfang und Ende eines Songs übersprungen – ganze Alben bleiben unverändert."),
    _s("autoplay", "user", "Wiedergabe", "bool", True, "Autoplay",
       "Wenn die Warteschlange zu Ende ist, laufen ähnliche Songs aus deiner Bibliothek weiter."),
    _s("normalize", "user", "Wiedergabe", "select", "album", "Lautstärke angleichen",
       "Alle Songs gleich laut – mit ReplayGain aus den Dateien oder vom Server gemessen. „Automatisch“ macht es "
       "wie Spotify: ganze Alben behalten ihre Dynamik, sonst wird jeder Song einzeln angeglichen.",
       options=[("off", "Aus"), ("track", "Pro Song"), ("album", "Automatisch (Alben am Stück)")]),
    _s("normalize_level", "user", "Wiedergabe", "select", "normal", "Lautstärke-Niveau beim Angleichen",
       "Am PC werden leise Songs auch angehoben – bei „Normal“ nur so weit, dass nichts übersteuert, "
       "bei „Laut“ mit Limiter (wie bei Spotify).",
       options=[("quiet", "Leise"), ("normal", "Normal"), ("loud", "Laut")]),
    _s("fade_pause", "user", "Wiedergabe", "bool", True, "Sanft pausieren",
       "Beim Pausieren und Fortsetzen kurz aus- bzw. einblenden."),
    _s("playback_rate", "user", "Wiedergabe", "select", "1", "Wiedergabegeschwindigkeit",
       "Praktisch für Hörbücher und Podcasts. Die Tonhöhe bleibt gleich.",
       options=[("0.75", "0,75×"), ("1", "Normal"), ("1.1", "1,1×"), ("1.25", "1,25×"), ("1.5", "1,5×"),
                ("2", "2×")]),
    _s("resume_session", "user", "Wiedergabe", "bool", True, "Letzte Warteschlange wiederherstellen",
       "Beim Öffnen geht es dort weiter, wo du aufgehört hast."),
    _s("restart_threshold", "user", "Wiedergabe", "int", 3, "„Zurück“ startet den Song neu nach",
       "Innerhalb dieser Zeit springt „Zurück“ zum vorherigen Song.", min=0, max=15, step=1, unit="s"),
    _s("seek_step", "user", "Wiedergabe", "int", 10, "Sprungweite beim Spulen",
       "Für Tastatur (Umschalt + Pfeile), Kopfhörer und Sperrbildschirm.", min=5, max=60, step=5, unit="s"),
    _s("count_play_after", "user", "Wiedergabe", "int", 30, "Als gehört zählen nach",
       "Ab dann landet ein Song im Verlauf und in „Deine Top-Songs“.", min=5, max=120, step=5, unit="s"),
    _s("private_session", "user", "Wiedergabe", "bool", False, "Private Sitzung",
       "Nichts wird im Verlauf gespeichert, solange das an ist."),
    _s("shuffle_smart", "user", "Wiedergabe", "bool", True, "Intelligenter Zufall",
       "Beim Zufallsmodus nicht zwei Songs desselben Künstlers direkt hintereinander."),

    # ================================================================ Equalizer (Benutzer)
    _s("eq_enabled", "user", "Equalizer", "bool", False, "Equalizer einschalten",
       "Klang nach deinem Geschmack. Tipp: Auf Android bei Aussetzern im Hintergrund wieder ausschalten."),
    _s("eq_preset", "user", "Equalizer", "select", "flat", "Voreinstellung", options=EQ_PRESETS),
    *[_s(key, "user", "Equalizer", "int", 0, label, min=-12, max=12, step=1, unit="dB") for key, label in EQ_BANDS],

    # ================================================================ Aussehen (Benutzer)
    _s("theme", "user", "Aussehen", "select", "dark", "Design",
       options=[("dark", "Dunkel"), ("black", "Schwarz (AMOLED)"), ("dim", "Gedämpft (bläulich)")]),
    _s("accent", "user", "Aussehen", "select", "green", "Akzentfarbe",
       options=[("green", "Grün"), ("blue", "Blau"), ("purple", "Lila"), ("pink", "Pink"), ("orange", "Orange"),
                ("red", "Rot"), ("teal", "Türkis"), ("yellow", "Gelb")]),
    _s("dynamic_colors", "user", "Aussehen", "bool", True, "Farben aus dem Cover",
       "Hintergründe von Album, Künstler und Player passen sich dem Cover an."),
    _s("ui_scale", "user", "Aussehen", "select", "100", "Größe der Oberfläche",
       options=[("85", "85 %"), ("90", "90 %"), ("100", "100 %"), ("110", "110 %"), ("125", "125 %"),
                ("140", "140 %")]),
    _s("card_size", "user", "Aussehen", "select", "medium", "Größe der Kacheln",
       options=[("small", "Klein"), ("medium", "Mittel"), ("large", "Groß")]),
    _s("compact_lists", "user", "Aussehen", "bool", False, "Kompakte Songlisten",
       "Mehr Songs auf einen Blick, niedrigere Zeilen."),
    _s("list_covers", "user", "Aussehen", "bool", True, "Cover in Songlisten"),
    _s("list_album_column", "user", "Aussehen", "bool", True, "Album-Spalte in Songlisten"),
    _s("reduce_motion", "user", "Aussehen", "bool", False, "Animationen reduzieren"),
    _s("start_page", "user", "Aussehen", "select", "home", "Beim Öffnen anzeigen", options=PAGES),
    _s("single_click_play", "user", "Aussehen", "bool", False, "Songs mit einem Klick abspielen",
       "Am PC sonst per Doppelklick (wie bei Spotify). Am Handy reicht immer ein Tippen."),
    _s("remaining_time", "user", "Aussehen", "bool", False, "Restzeit statt Gesamtdauer",
       "Im Player wird rechts die verbleibende Zeit angezeigt."),
    _s("confirm_delete", "user", "Aussehen", "bool", True, "Vor dem Löschen nachfragen"),
    _s("shortcuts", "user", "Aussehen", "bool", True, "Tastenkürzel",
       "Leertaste, Strg + Pfeile usw. (siehe Anleitung)."),
    _s("show_lyrics_button", "user", "Aussehen", "bool", True, "Songtext-Knopf im Player",
       "Zeigt Songtexte aus .lrc-Dateien oder aus den Tags – mitlaufend, wenn sie Zeitstempel haben."),

    # ================================================================ Startseite (Benutzer)
    _s("home_quick", "user", "Startseite", "bool", True, "Schnellzugriff-Kacheln"),
    _s("home_recent", "user", "Startseite", "bool", True, "„Zuletzt gehört“"),
    _s("home_mixes", "user", "Startseite", "bool", True, "„Für dich gemacht“, Top-Genres und „Deine Mixe“"),
    _s("home_new", "user", "Startseite", "bool", True, "„Neu in deiner Bibliothek“"),
    _s("home_top_tracks", "user", "Startseite", "bool", True, "„Deine Top-Songs“"),
    _s("home_artists", "user", "Startseite", "bool", True, "„Deine Künstler“"),
    _s("home_playlists", "user", "Startseite", "bool", True, "„Deine Playlists“"),
    _s("home_discover", "user", "Startseite", "bool", True, "„Entdecken“ und „Weil du … gehört hast“"),
    _s("home_items", "user", "Startseite", "int", 12, "Einträge pro Reihe", min=4, max=30, step=1),

    # ================================================================ Suche & Bibliothek (Benutzer)
    _s("spotify_suggestions", "user", "Suche & Bibliothek", "bool", True, "Spotify-Vorschläge automatisch zeigen",
       "Sonst erscheint ein Knopf „Auf Spotify suchen“."),
    _s("spotify_hide_owned", "user", "Suche & Bibliothek", "bool", False,
       "Vorhandene Songs in Vorschlägen ausblenden", "Zeigt nur Songs, die du noch nicht hast."),
    _s("library_tab", "user", "Suche & Bibliothek", "select", "playlists", "Bibliothek öffnet mit",
       options=[("playlists", "Playlists"), ("albums", "Alben"), ("artists", "Künstler"), ("songs", "Songs")]),
    _s("album_sort", "user", "Suche & Bibliothek", "select", "name", "Alben sortieren nach",
       options=[("name", "Name"), ("artist", "Künstler"), ("added", "Zuletzt hinzugefügt"), ("year", "Jahr")]),
    _s("song_sort", "user", "Suche & Bibliothek", "select", "title", "Songs sortieren nach",
       options=[("title", "Titel"), ("artist", "Künstler"), ("album", "Album"), ("added", "Zuletzt hinzugefügt")]),
    _s("auto_like_downloads", "user", "Suche & Bibliothek", "bool", False,
       "Geholte Songs automatisch zu Lieblingssongs", "Gilt für Downloads, die du selbst startest."),
    _s("mix_size", "user", "Suche & Bibliothek", "int", 60, "Songs pro Mix und Radio", min=10, max=200, step=10),

    # ================================================================ Bibliothek & Scan (Server)
    _s("scan_interval_minutes", "server", "Bibliothek & Scan", "int", 30, "Automatisch neu scannen alle",
       "Findet neue und gelöschte Dateien. 0 = nur von Hand.", min=0, max=1440, step=5, unit="Min."),
    _s("scan_on_start", "server", "Bibliothek & Scan", "bool", True, "Beim Start scannen"),
    _s("scan_workers", "server", "Bibliothek & Scan", "int", 6, "Gleichzeitig gelesene Dateien",
       "Mehr = schnellerer Scan, aber mehr Last für NAS und Netzwerk.", min=1, max=16, step=1),
    _s("min_track_seconds", "server", "Bibliothek & Scan", "int", 0, "Kurze Dateien ignorieren (kürzer als)",
       "Z. B. Klingeltöne oder Jingles. 0 = alles aufnehmen.", min=0, max=120, step=5, unit="s"),
    _s("ignore_folders", "server", "Bibliothek & Scan", "text", "", "Ordner ignorieren",
       "Ordnernamen, getrennt durch Komma, z. B. „Hörbücher, Podcasts, Sprachmemos“."),
    _s("folder_as_album", "server", "Bibliothek & Scan", "bool", True, "Ordnername als Album",
       "Wenn eine Datei keinen Album-Tag hat."),
    _s("filename_parsing", "server", "Bibliothek & Scan", "bool", True, "Künstler und Titel aus dem Dateinamen",
       "Bei Dateien ohne Tags wird „Künstler - Titel.mp3“ ausgewertet."),
    _s("split_feat", "server", "Bibliothek & Scan", "bool", True, "„feat.“-Gäste als eigene Künstler",
       "„A feat. B“ zählt bei A und bei B."),
    _s("artist_separators", "server", "Bibliothek & Scan", "text", ";", "Trennzeichen für mehrere Interpreten",
       "Durch Leerzeichen getrennt, z. B. „; / &“. Vorsicht bei „&“ (Simon & Garfunkel)."),
    _s("prefer_folder_cover", "server", "Bibliothek & Scan", "bool", False, "cover.jpg im Ordner bevorzugen",
       "Sonst wird das in der Datei eingebettete Cover genommen."),
    _s("max_remove_percent", "server", "Bibliothek & Scan", "int", 30, "Sicherheitsgrenze beim Aufräumen",
       "Würde ein Scan mehr als diesen Anteil der Bibliothek löschen (z. B. NAS kurz weg), wird nichts gelöscht. "
       "„Alles neu einlesen“ umgeht die Grenze.", min=5, max=100, step=5, unit="%"),
    _s("loudness_analysis", "server", "Bibliothek & Scan", "bool", True, "Klang analysieren",
       "Misst im Hintergrund Lautheit, echte Spitzen und Stille am Anfang/Ende jedes Songs – für „Lautstärke "
       "angleichen“ (auch Anheben leiser Songs) und Übergänge ohne Pause. Einmal pro Song, schont den Server."),

    # ================================================================ Streaming (Server)
    _s("transcode_format", "server", "Streaming", "select", "mp3", "Format beim Umwandeln",
       "MP3 läuft auf allen Geräten. Bei niedrigen Bitraten (mobile Daten) nimmt Homify automatisch Opus, "
       "wenn das Gerät es kann – klingt dort deutlich besser.",
       options=[("mp3", "MP3"), ("aac", "AAC (m4a)"), ("opus", "Opus")]),
    _s("transcode_high_kbps", "server", "Streaming", "select", "320", "Bitrate für „Hoch“",
       options=[("192", "192 kbit/s"), ("256", "256 kbit/s"), ("320", "320 kbit/s")]),
    _s("transcode_low_kbps", "server", "Streaming", "select", "128", "Bitrate für „Niedrig“",
       options=[("96", "96 kbit/s"), ("128", "128 kbit/s"), ("160", "160 kbit/s")]),
    _s("transcode_cache_mb", "server", "Streaming", "int", 2048, "Speicher für umgewandelte Songs",
       "Umgewandelte Songs werden zwischengespeichert, älteste zuerst gelöscht.", min=100, max=100000, step=100,
       unit="MB"),
    _s("max_transcodes", "server", "Streaming", "int", 2, "Gleichzeitige Umwandlungen",
       "Begrenzt die Rechenlast auf dem Server.", min=1, max=8, step=1),
    _s("allow_file_download", "server", "Streaming", "bool", True, "Songs aufs Gerät laden erlauben",
       "Menüpunkt „Datei aufs Gerät laden“."),

    # ================================================================ Downloads / spotDL (Server)
    _s("download_format", "server", "Downloads (spotDL)", "select", "opus", "Format",
       "Opus = Originalton von YouTube Music (bis 160 kbit/s, klingt wie ~256 kbit/s MP3). M4A nimmt die AAC-Spur – "
       "mit Cookies eines YouTube-Music-Premium-Kontos bis 256 kbit/s. MP3 wird immer umgewandelt und verliert Qualität.",
       options=[("opus", "Opus – beste Qualität (empfohlen)"), ("m4a", "M4A/AAC – beste Qualität mit YouTube Premium"),
                ("mp3", "MP3 – läuft überall, aber umgewandelt"), ("flac", "FLAC (umgewandelt, nur größer)"),
                ("ogg", "Ogg Vorbis (umgewandelt)")]),
    _s("download_bitrate", "server", "Downloads (spotDL)", "select", "disable", "Bitrate",
       "„Original“ übernimmt den Ton ohne Umwandlung, wenn das Format passt (Opus/M4A) – jede Umwandlung kostet Klang.",
       options=[("disable", "Original – nicht umwandeln (empfohlen)"), ("auto", "Wie die Quelle (umgewandelt)"),
                ("128k", "128 kbit/s"), ("192k", "192 kbit/s"), ("256k", "256 kbit/s"), ("320k", "320 kbit/s")]),
    _s("download_threads", "server", "Downloads (spotDL)", "int", 2, "Gleichzeitige Downloads (Threads)",
       min=1, max=8, step=1),
    _s("output_template", "server", "Downloads (spotDL)", "text",
       "{album-artist}/{album}/{artists} - {title}.{output-ext}", "Dateinamen-Vorlage",
       "Variablen: {artists} {artist} {title} {album} {album-artist} {track-number} {year} {output-ext}"),
    _s("download_lyrics", "server", "Downloads (spotDL)", "bool", True, "Songtexte mitladen (.lrc)",
       "Speichert Songtexte – wenn möglich mit Zeitstempeln – neben dem Song."),
    _s("sponsor_block", "server", "Downloads (spotDL)", "bool", False, "Nicht-Musik-Teile entfernen",
       "Schneidet Intros/Outros aus YouTube-Videos heraus (SponsorBlock)."),
    _s("audio_providers", "server", "Downloads (spotDL)", "select", "youtube-music youtube", "Audio-Quellen",
       "Wo spotDL nach dem Audio sucht – in dieser Reihenfolge.",
       options=[("youtube-music", "YouTube Music"), ("youtube-music youtube", "YouTube Music, dann YouTube"),
                ("youtube", "YouTube"), ("youtube-music youtube soundcloud bandcamp", "Alle Quellen")]),
    _s("skip_explicit", "server", "Downloads (spotDL)", "bool", False, "Songs mit „Explicit“ überspringen"),
    _s("filename_restrict", "server", "Downloads (spotDL)", "select", "none", "Dateinamen vereinfachen",
       "Hilft bei alten Geräten/Autoradios mit Umlauten.",
       options=[("none", "Nein"), ("ascii", "Nur ASCII (ä → a)"), ("strict", "Streng (nur Buchstaben/Zahlen)")]),
    _s("download_retries", "server", "Downloads (spotDL)", "int", 1, "Fehlgeschlagene Downloads wiederholen",
       min=0, max=5, step=1, unit="×"),
    _s("daily_download_limit", "server", "Downloads (spotDL)", "int", 0, "Downloads pro Benutzer und Tag",
       "0 = unbegrenzt. Gilt nicht für Admins.", min=0, max=1000, step=5),
    _s("new_users_can_download", "server", "Downloads (spotDL)", "bool", True,
       "Neue Benutzer dürfen herunterladen"),
    _s("spotdl_auto_update", "server", "Downloads (spotDL)", "bool", True, "spotDL wöchentlich aktualisieren",
       "YouTube ändert öfter etwas – aktuelle Versionen laden zuverlässiger."),
    _s("download_history_days", "server", "Downloads (spotDL)", "int", 30, "Erledigte Downloads aufräumen nach",
       "0 = nie.", min=0, max=365, step=1, unit="Tagen"),
    _s("spotdl_cookie_file", "server", "Downloads (spotDL)", "text", "", "Cookie-Datei für YouTube",
       "Hilft, wenn YouTube „Bestätige, dass du kein Bot bist“ meldet."),
    _s("spotdl_extra_args", "server", "Downloads (spotDL)", "text", "", "Zusätzliche spotDL-Argumente",
       "Für Profis, z. B. „--max-retries 5“."),
    _s("spotify_client_id", "server", "Downloads (spotDL)", "text", "", "Spotify Client ID (optional)",
       "Nur nötig, wenn Spotify-Anfragen blockiert werden (developer.spotify.com)."),
    _s("spotify_client_secret", "server", "Downloads (spotDL)", "password", "", "Spotify Client Secret (optional)"),
    _s("spotify_use_official_api", "server", "Downloads (spotDL)", "bool", False, "Offizielle Spotify-API nutzen",
       "Nur zusammen mit eigener Client ID/Secret sinnvoll."),

    # ================================================================ Server & Sicherheit (Server)
    _s("server_name", "server", "Server & Sicherheit", "text", "Homify", "Name des Servers",
       "Erscheint im Titel und bei der Anmeldung, z. B. „Eriks Musik“."),
    _s("host", "server", "Server & Sicherheit", "select", "0.0.0.0", "Erreichbar von",
       options=[("0.0.0.0", "Allen Geräten im Netzwerk"), ("127.0.0.1", "Nur diesem Rechner")], restart=True),
    _s("port", "server", "Server & Sicherheit", "int", 8484, "Port", "Die Apps brauchen danach die neue Adresse.",
       min=1024, max=65535, step=1, restart=True),
    _s("session_days", "server", "Server & Sicherheit", "int", 180, "Angemeldet bleiben für",
       "Danach muss man sich neu anmelden.", min=1, max=365, step=1, unit="Tage"),
    _s("history_days", "server", "Server & Sicherheit", "int", 0, "Wiedergabe-Verlauf behalten",
       "0 = für immer.", min=0, max=3650, step=30, unit="Tage"),
    _s("auto_backup", "server", "Server & Sicherheit", "bool", True, "Tägliche Sicherung",
       "Sichert Benutzer, Playlists und Einstellungen nach data/backups."),
    _s("backup_keep", "server", "Server & Sicherheit", "int", 7, "Anzahl Sicherungen behalten",
       min=1, max=60, step=1),
    _s("log_level", "server", "Server & Sicherheit", "select", "INFO", "Protokoll-Ausführlichkeit",
       options=[("WARNING", "Nur Warnungen"), ("INFO", "Normal"), ("DEBUG", "Sehr ausführlich (Fehlersuche)")]),
    _s("ffmpeg_path", "server", "Server & Sicherheit", "text", "", "Eigener ffmpeg-Pfad",
       "Leer = mitgeliefertes ffmpeg."),
]

BY_KEY: dict[str, Setting] = {s.key: s for s in SETTINGS}
USER_SETTINGS = [s for s in SETTINGS if s.scope == "user"]
SERVER_SETTINGS = [s for s in SETTINGS if s.scope == "server"]
SECRET_KEYS = {s.key for s in SETTINGS if s.type == "password"}

# Kurven der klassischen Equalizer-Presets (wie bei Spotify), umgerechnet auf 60/150/400/1k/2,4k/15k Hz
EQ_PRESET_VALUES: dict[str, list[int]] = {
    "flat": [0, 0, 0, 0, 0, 0],
    "acoustic": [5, 3, 1, 2, 4, 2],
    "bass": [5, 3, 1, 0, 0, 0],
    "bass_reducer": [-5, -3, -1, 0, 0, 0],
    "classical": [4, 3, 0, -2, 1, 3],
    "dance": [5, 4, 1, 4, 5, 1],
    "deep": [4, 1, 2, 2, 1, -4],
    "electronic": [4, 1, -1, 2, 1, 5],
    "hiphop": [4, 2, 0, -1, 1, 3],
    "jazz": [3, 2, 0, -2, 0, 3],
    "latin": [4, 0, -1, -2, -1, 4],
    "loudness": [5, 0, -1, 0, -2, 2],
    "lounge": [-2, 0, 3, 2, 0, 1],
    "piano": [2, 1, 3, 2, 4, 3],
    "pop": [-1, 1, 3, 4, 1, -1],
    "rnb": [5, 5, -1, -2, 2, 4],
    "rock": [4, 3, 0, -1, 1, 4],
    "small_speakers": [5, 3, 1, 0, -1, -4],
    "spoken_word": [-2, 0, 3, 5, 5, 0],
    "treble": [0, 0, 0, 1, 3, 5],
    "treble_reducer": [0, 0, 0, -1, -3, -5],
    "vocal": [-2, -2, 3, 4, 3, -1],
}


def validate(setting: Setting, value: Any) -> Any:
    """Wert prüfen und in den richtigen Typ bringen. Wirft ValueError bei ungültigen Werten."""
    if value is None:
        return setting.default
    if setting.type == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "ja", "on")
        return bool(value)
    if setting.type == "int":
        try:
            number = int(round(float(value)))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"„{setting.label}“: bitte eine Zahl eingeben") from exc
        if setting.min is not None:
            number = max(int(setting.min), number)
        if setting.max is not None:
            number = min(int(setting.max), number)
        return number
    if setting.type == "select":
        allowed = {str(v) for v, _ in setting.options}
        value = str(value)
        if value not in allowed:
            raise ValueError(f"„{setting.label}“: ungültige Auswahl")
        return value
    value = "" if value is None else str(value)
    if len(value) > 2000:
        raise ValueError(f"„{setting.label}“: zu lang")
    return value.strip() if setting.type == "text" else value


def schema_json() -> list[dict[str, Any]]:
    out = []
    for s in SETTINGS:
        d = asdict(s)
        d["options"] = [{"value": v, "label": label} for v, label in s.options]
        out.append(d)
    return out


def user_defaults() -> dict[str, Any]:
    return {s.key: s.default for s in USER_SETTINGS}
