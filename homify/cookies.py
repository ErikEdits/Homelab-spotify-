"""YouTube-Cookies für spotDL: hochgeladene cookies.txt prüfen, auf YouTube kürzen und sicher speichern.

YouTube blockiert manchmal Downloads („Bestätige, dass du kein Bot bist“). Mit den Cookies eines
angemeldeten YouTube-Kontos lädt yt-dlp wie ein normaler Nutzer. Gespeichert werden nur die
YouTube-Cookies (keine von Google, Gmail usw.), die Datei ist nur für Homify lesbar.
"""

from __future__ import annotations

import os
from typing import Any

from .config import DATA_DIR, config

COOKIE_FILE = DATA_DIR / "youtube-cookies.txt"
# Diese Cookies gibt es nur, wenn man bei YouTube angemeldet ist
LOGIN_COOKIES = {"SID", "HSID", "SSID", "APISID", "SAPISID", "LOGIN_INFO", "__Secure-1PSID", "__Secure-3PSID"}
_HTTPONLY = "#HttpOnly_"


def parse(text: str) -> list[list[str]]:
    """Zeilen einer cookies.txt (Netscape-Format) in 7 Felder zerlegen; Kommentare werden übersprungen."""
    rows = []
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line or (line.startswith("#") and not line.startswith(_HTTPONLY)):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            parts = line.split(None, 6)  # beim Kopieren wurden Tabs zu Leerzeichen
        if len(parts) != 7 or parts[1].upper() not in ("TRUE", "FALSE"):
            continue
        rows.append(parts)
    return rows


def _domain(row: list[str]) -> str:
    d = row[0][len(_HTTPONLY):] if row[0].startswith(_HTTPONLY) else row[0]
    return d.lstrip(".").lower()


def _youtube(rows: list[list[str]]) -> list[list[str]]:
    return [r for r in rows if _domain(r) == "youtube.com" or _domain(r).endswith(".youtube.com")]


def save(text: str) -> dict[str, Any]:
    rows = parse(text)
    if not rows:
        raise ValueError("Das ist keine Cookie-Datei. Gebraucht wird eine cookies.txt im Netscape-Format "
                         "(z. B. von der Chrome-Erweiterung „Get cookies.txt LOCALLY“).")
    yt = _youtube(rows)
    if not yt:
        raise ValueError("In der Datei sind keine YouTube-Cookies. Bitte auf youtube.com angemeldet sein und "
                         "„Export All Cookies“ benutzen.")
    content = ("# Netscape HTTP Cookie File\n# Von Homify gespeichert – nur YouTube-Cookies\n\n"
               + "\n".join("\t".join(r) for r in yt) + "\n")
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = COOKIE_FILE.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(COOKIE_FILE)
    config.update({"spotdl_cookie_file": str(COOKIE_FILE)})
    return status()


def remove() -> dict[str, Any]:
    if (config.get("spotdl_cookie_file") or "") == str(COOKIE_FILE):
        config.update({"spotdl_cookie_file": ""})
    COOKIE_FILE.unlink(missing_ok=True)
    return status()


def status() -> dict[str, Any]:
    path = (config.get("spotdl_cookie_file") or "").strip()
    out: dict[str, Any] = {"path": path, "exists": False, "count": 0, "logged_in": False, "updated": None}
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                yt = _youtube(parse(fh.read()))
        except OSError:
            yt = []
        out.update(exists=True, count=len(yt), logged_in=bool({r[5] for r in yt} & LOGIN_COOKIES),
                   updated=os.path.getmtime(path))
    return out
