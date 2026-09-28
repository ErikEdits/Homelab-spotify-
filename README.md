# Homify – dein eigenes Spotify fürs Homelab

Homify sieht aus und fühlt sich an wie Spotify, spielt aber **deine eigene Musik** vom NAS ab –
auf dem PC, dem Handy, dem Laptop oder dem Fernseher, im Heimnetz oder von unterwegs.
Fehlt ein Song, suchst du ihn einfach oder fügst einen Spotify-Link ein: Homify zeigt passende
Vorschläge von Spotify und holt den Song per [spotDL](https://github.com/spotDL/spotify-downloader)
direkt in deine Bibliothek.

![Startseite](docs/screenshot-home.jpg)

| Album | Playlist mit Warteschlange |
|---|---|
| ![Album](docs/screenshot-album.jpg) | ![Playlist](docs/screenshot-playlist.jpg) |

![Handy](docs/screenshot-mobile.jpg)

## Was Homify kann

- **Spotify-Look**: Seitenleiste, Startseite mit Schnellzugriff, Alben, Künstler, Playlists,
  Lieblingssongs, Warteschlange, Zufall/Wiederholen, Vollbild-Player, Kontextmenüs, Tastenkürzel.
- **Streaming vom NAS**: Musikordner per Netzwerkfreigabe (`\\NAS\Musik`, `Z:\Musik`, `/mnt/nas/musik`).
  Neue Dateien werden automatisch gefunden (regelmäßiger Scan).
- **Infos und Cover kommen aus den Dateien**: Titel, Künstler, Album, Jahr, Genre, Tracknummer und
  eingebettete Cover (oder `cover.jpg`/`folder.jpg` im Ordner).
- **Alle Songs funktionieren**: MP3, M4A/AAC, FLAC, OGG, Opus und WAV spielt der Browser direkt.
  Was er nicht kann (WMA, ALAC, APE, WavPack, AIFF, DSD …), wandelt Homify automatisch
  in MP3 um. Klappt ein Song trotzdem nicht, schaltet der Player von selbst auf Umwandlung um.
- **Fehlt ein Song? Holen!** Suche nach Namen oder füge einen Spotify-Link ein (Song, Album,
  Playlist, Künstler). Homify zeigt die Treffer von Spotify, markiert, was du schon hast, und
  lädt Fehlendes mit einem Klick über spotDL herunter. Danach steht der Song mit Cover in deiner
  Bibliothek.
- **Von überall**: Weboberfläche für jedes Gerät, als App installierbar („Zum Startbildschirm“),
  Steuerung über Sperrbildschirm und Medientasten, Datensparmodus für unterwegs.
- **Mehrere Benutzer**: eigene Playlists, Lieblingssongs und Verlauf pro Person. Der Admin
  legt fest, wer herunterladen darf.
- **Extras**: Genre-Mixe, Zufallsmix, Song-Radio, „Mehr auf Spotify finden“ beim Künstler,
  Download einzelner Dateien aufs Gerät.

## So funktioniert es

```
 Handy / PC / Laptop  ──(Browser, WLAN/LAN)──►  Homify-Server (dein Windows-PC)
                                                   │  liest & streamt
                                                   ▼
                                              NAS: \\NAS\Musik
                                                   ▲  speichert neue Songs
                                   spotDL ─────────┘  (Infos: Spotify, Audio: YouTube Music)
```

Homify ist ein kleiner Server (Python), der auf deinem Windows-PC läuft. Das Programmfenster ist
die gleiche Oberfläche, die du auch auf dem Handy im Browser öffnest.

---

## Installation unter Windows

**Voraussetzungen:** Windows 10 oder 11. Python wird bei Bedarf automatisch installiert.

1. Dieses Repository herunterladen: grüner Button **Code → Download ZIP** und entpacken
   (oder `git clone`). Am besten in einen festen Ordner, z. B. `C:\Homify`.
2. Im Ordner `windows` doppelt auf **`Installieren.bat`** klicken.
   - Fehlt Python, fragt das Skript, ob es Python 3.12 installieren soll. Danach das Skript
     **noch einmal** starten.
   - Das Skript installiert alles Nötige, richtet spotDL ein, legt eine **Desktop-Verknüpfung**
     an und fragt einmal nach Admin-Rechten für die **Firewall-Freigabe** (damit das Handy
     Homify erreicht).
3. Homify öffnet sich. Beim ersten Start legst du dein **Admin-Konto** an.
4. Unter **Einstellungen → Bibliothek** den Musikordner eintragen, z. B. `\\NAS\Musik`
   (Knopf „Ordner wählen“ hilft) und **Einstellungen speichern**. Der Scan startet sofort.

### Die Windows-Skripte im Ordner `windows`

| Datei | Wofür |
|---|---|
| `Installieren.bat` | Einmalige Installation (erneut ausführen schadet nicht) |
| `Homify.vbs` (Desktop-Verknüpfung „Homify“) | Startet Homify ohne Konsolenfenster und öffnet das Programmfenster |
| `Starten-mit-Konsole.bat` | Start mit sichtbarem Protokoll – praktisch bei Problemen |
| `Autostart-einrichten.bat` | Homify startet unsichtbar bei jeder Windows-Anmeldung (`… entfernen` zum Rückgängigmachen) |
| `Beenden.bat` | Hintergrund-Server stoppen (geht auch unter Einstellungen → System) |
| `Aktualisieren.bat` | Neue Homify-Version (per `git pull`), Pakete und spotDL aktualisieren |

### Musik vom NAS einbinden

- **UNC-Pfad** (empfohlen): `\\NAS-NAME\Musik` oder `\\192.168.1.10\Musik`.
- **Netzlaufwerk**: im Explorer „Netzlaufwerk verbinden“ (z. B. `Z:`), dann `Z:\` bzw. `Z:\Musik`.
- Homify läuft mit deinem Windows-Benutzer. Wenn du die Freigabe im Explorer öffnen kannst,
  kann Homify es auch. Anmeldedaten für das NAS in Windows speichern („Anmeldedaten merken“).
- Mehrere Ordner sind möglich (einer pro Zeile). Neue Downloads landen im ersten Ordner, oder
  im eigens eingestellten **Download-Ordner**.
- Ist das NAS mal aus, löscht Homify **nichts** aus der Bibliothek. Die Songs sind wieder da,
  sobald das NAS erreichbar ist.

## Auf dem Handy und anderen Geräten

Unter **Einstellungen → Zugriff von anderen Geräten** stehen die Adressen, z. B.
`http://192.168.1.20:8484`. Diese auf dem Handy im Browser öffnen und anmelden.

- **Wie eine App**: im Browser-Menü „Zum Startbildschirm hinzufügen“ (Android/Chrome und iPhone/Safari).
- **Sperrbildschirm**: Titel, Cover, Weiter/Zurück und Spulen funktionieren über die Medien-Steuerung.
- **Unterwegs**: Einstellungen → Wiedergabe → **Datensparend (128 kbit/s)**.

### Von unterwegs (außerhalb des Heimnetzes)

Am einfachsten und sichersten mit **[Tailscale](https://tailscale.com)** (kostenlos für private Nutzung):

1. Tailscale auf dem Homify-PC und auf dem Handy installieren und mit demselben Konto anmelden.
2. Auf dem Handy `http://<Tailscale-Name-des-PCs>:8484` öffnen (z. B. `http://mein-pc:8484`).

Alternativen: WireGuard/VPN auf dem Router oder ein Reverse-Proxy mit HTTPS (z. B. Caddy).
**Den Port nicht ungeschützt per Portweiterleitung ins Internet stellen.**

## Songs holen, die du nicht hast (spotDL)

1. Oben auf **Suchen** gehen und den Songnamen tippen, oder einen Spotify-Link einfügen,
   z. B. `https://open.spotify.com/album/…`.
2. Unter deinen eigenen Treffern erscheint **„Nicht dabei? Von Spotify holen“** mit Vorschlägen.
   Was du schon hast, ist mit **„In Bibliothek“** markiert.
3. Auf **Holen** klicken. Den Fortschritt siehst du unter **Downloads**. Ist der Song fertig,
   kannst du ihn direkt abspielen. Titel, Album und Cover sind schon in der Datei.

Gut zu wissen:

- spotDL holt die Infos von Spotify und das Audio von YouTube Music (meist ~128–256 kbit/s).
- Wenn Downloads plötzlich scheitern (YouTube ändert öfter etwas): **Einstellungen →
  Downloads → spotDL aktualisieren**.
- Meldet YouTube „Bestätige, dass du kein Bot bist“: eine `cookies.txt` deines YouTube-Kontos
  exportieren und unter Einstellungen als Cookie-Datei eintragen.
- Eigene Spotify-API-Daten sind normalerweise **nicht** nötig. Falls Spotify-Anfragen
  blockiert werden, kannst du unter developer.spotify.com eine App anlegen und die Daten eintragen.
- Bitte nur für Musik nutzen, die du privat nutzen darfst. Die Nutzungsbedingungen von
  Spotify und YouTube gelten weiterhin.

## Tastenkürzel

| Taste | Aktion |
|---|---|
| Leertaste | Wiedergabe / Pause |
| Strg + → / ← | Nächster / vorheriger Song |
| Umschalt + → / ← | 5 Sekunden vor / zurück |
| Strg + ↑ / ↓ | Lauter / leiser |
| `S` / `R` / `M` | Zufall / Wiederholen / Stumm |
| `/` oder Strg + K | Suche |

---

## Später: Ubuntu

```bash
git clone https://github.com/ErikEdits/Homelab-spotify-.git homify
cd homify
./ubuntu/install.sh             # installieren
./ubuntu/start.sh               # starten
./ubuntu/install.sh --service   # oder als Dienst mit Autostart (systemd)
```

NAS-Freigabe dauerhaft einbinden (SMB/CIFS), Beispiel für `/etc/fstab`:

```
//192.168.1.10/Musik  /mnt/nas/musik  cifs  credentials=/home/DU/.nas-login,uid=DU,gid=DU,iocharset=utf8,_netdev,nofail  0  0
```

(`sudo apt install cifs-utils`. In `~/.nas-login` stehen `username=…` und `password=…`.)
Dann in Homify den Ordner `/mnt/nas/musik` eintragen. Ist die Freigabe nicht gemountet, erkennt
Homify den leeren Ordner und löscht nichts aus der Bibliothek.

## Oder: Docker (z. B. direkt auf dem NAS)

In `docker-compose.yml` den Musikordner eintragen, dann:

```bash
docker compose up -d
```

## Datenablage

Alles, was Homify speichert, liegt im Ordner `data/`: Datenbank (Playlists, Likes, Verlauf),
Cover-Cache, Umwandlungs-Cache, Einstellungen, Protokolle und die spotDL-Umgebung.
Deine Musikdateien werden nie verändert. Zum Sichern reicht `data/homify.db` + `data/config.json`.

Passwort vergessen? `.venv\Scripts\python -m homify reset-password NAME` (Windows) bzw.
`.venv/bin/python -m homify reset-password NAME` (Ubuntu).

## Probleme?

| Problem | Lösung |
|---|---|
| Handy erreicht Homify nicht | Gleiches WLAN? Windows-Firewall: eingehend TCP 8484 für private Netzwerke erlauben (macht `Installieren.bat`). Netzwerkprofil in Windows auf „Privat“ stellen. |
| Musikordner „nicht erreichbar“ | Pfad im Explorer testen. NAS-Anmeldedaten in Windows speichern. Bei Netzlaufwerken lieber den UNC-Pfad `\\NAS\Musik` nehmen. |
| Downloads schlagen fehl | Einstellungen → Downloads → **spotDL aktualisieren**. Das Protokoll des Downloads (Listen-Symbol) zeigt den Grund. |
| Ein Song spielt nicht | Homify wandelt automatisch um. Sonst unter Einstellungen → System den Cache leeren oder `Starten-mit-Konsole.bat` nutzen und die Meldung ansehen. |
| Oberfläche bleibt schwarz | Seite neu laden (Strg + F5). Protokoll: `data/logs/homify.log` |

## Für Entwickler

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest          # Tests (erzeugen eine kleine Test-Bibliothek mit ffmpeg)
.venv/bin/python -m homify run      # Server auf http://localhost:8484
```

Aufbau: `homify/` enthält den Python-Server (FastAPI, SQLite, mutagen, ffmpeg über
imageio-ffmpeg). `homify/static/` enthält die Oberfläche (reines HTML/CSS/JavaScript ohne
Build-Schritt). spotDL läuft in einer eigenen Python-Umgebung (`data/tools/spotdl-venv`), weil
es eigene, ältere Bibliotheksversionen braucht. Homify spricht über `homify/spotdl_bridge.py`
mit ihr.
