"""Jeder Song nur einmal in der Bibliothek.

Derselbe Song = gleicher Titel + Hauptinterpret (ohne „Remastered“, „feat. …“ usw.) und fast gleiche Länge.
Liegt ein Song mehrfach im Speicherort (z. B. als MP3 und FLAC), bleibt die beste Datei in der
Bibliothek; die anderen werden ausgeblendet (Tabelle `duplicates`) – gelöscht wird nichts automatisch.
Likes, Playlists und Verlauf zeigen immer auf die behaltene Datei. Playlists dürfen einen Song
trotzdem mehrfach enthalten.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any

from . import db
from .textutil import dup_key

TOLERANCE_SECONDS = 6.0
LOSSLESS = {"flac", "alac", "pcm", "aiff", "ape", "wavpack", "tta", "tak", "dsd", "ofr"}


def quality(codec: str | None, bitrate: int | None) -> tuple[int, int]:
    return (1 if (codec or "").lower() in LOSSLESS else 0, int(bitrate or 0))


def same_length(a: float | None, b: float | None) -> bool:
    return not a or not b or abs(float(a) - float(b)) <= TOLERANCE_SECONDS


UNKNOWN_ARTIST = "Unbekannter Künstler"


def key_for_row(title: str, artists_json: str, artist: str = "") -> str:
    try:
        first = (json.loads(artists_json or "[]") or [""])[0]
    except ValueError:
        first = ""
    first = first or artist
    if not first or first == UNKNOWN_ARTIST:
        return ""  # ohne Interpret lieber nicht zusammenfassen
    return dup_key(title, first)


def find_track(title: str = "", artist: str = "", duration: float = 0.0, spotify_id: str = "",
               isrc: str = "") -> str | None:
    """ID des Songs in der Bibliothek – oder None, wenn es ihn noch nicht gibt."""
    if spotify_id:
        row = db.query_one("SELECT id FROM tracks WHERE spotify_id = ?", (spotify_id,))
        if row:
            return row["id"]
    if isrc:
        row = db.query_one("SELECT id FROM tracks WHERE isrc = ?", (isrc,))
        if row:
            return row["id"]
    key = dup_key(title, artist)
    if not key:
        return None
    for row in db.query("SELECT id, duration FROM tracks WHERE dkey = ?", (key,)):
        if same_length(duration, row["duration"]):
            return row["id"]
    return None


def backfill_keys() -> None:
    """Ältere Datenbanken: Schlüssel für vorhandene Songs nachtragen."""
    rows = db.query("SELECT id, title, artists, artist FROM tracks WHERE dkey = ''")
    updates = [(k, r["id"]) for r in rows if (k := key_for_row(r["title"], r["artists"], r["artist"]))]
    if updates:
        with db.transaction() as c:
            c.executemany("UPDATE tracks SET dkey = ? WHERE id = ?", updates)


def remap(c, old_id: str, new_id: str) -> None:
    """Likes, Playlists und Verlauf auf eine andere Datei desselben Songs umhängen."""
    c.execute("UPDATE OR IGNORE likes SET track_id = ? WHERE track_id = ?", (new_id, old_id))
    c.execute("DELETE FROM likes WHERE track_id = ?", (old_id,))
    c.execute("UPDATE playlist_tracks SET track_id = ? WHERE track_id = ?", (new_id, old_id))
    c.execute("UPDATE plays SET track_id = ? WHERE track_id = ?", (new_id, old_id))
    c.execute("UPDATE duplicates SET track_id = ? WHERE track_id = ?", (new_id, old_id))


def consolidate() -> int:
    """Doppelte Songs in der Bibliothek zusammenfassen. Gibt die Zahl neu ausgeblendeter Dateien zurück."""
    rows = db.query(
        "SELECT id, path, root, rel, size, mtime, title, artist, album, codec, bitrate, duration, dkey, added_at "
        "FROM tracks WHERE dkey IN (SELECT dkey FROM tracks WHERE dkey != '' GROUP BY dkey HAVING COUNT(*) > 1)"
    )
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[r["dkey"]].append(r)
    hide: list[tuple[dict[str, Any], str]] = []
    for items in groups.values():
        # Beste Qualität zuerst, bei Gleichstand die Datei, die schon länger da ist
        items.sort(key=lambda r: (-quality(r["codec"], r["bitrate"])[0], -quality(r["codec"], r["bitrate"])[1],
                                  r["added_at"] or 0, r["id"]))
        kept: list[dict[str, Any]] = []
        for r in items:
            match = next((k for k in kept if same_length(k["duration"], r["duration"])), None)
            if match:
                hide.append((r, match["id"]))
            else:
                kept.append(r)
    if not hide:
        return 0
    now = time.time()
    with db.transaction() as c:
        for r, keep_id in hide:
            remap(c, r["id"], keep_id)
            c.execute(
                "INSERT OR REPLACE INTO duplicates (path, root, rel, size, mtime, track_id, title, artist, album, codec, "
                "bitrate, duration, found_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["path"], r["root"], r["rel"], r["size"], r["mtime"], keep_id, r["title"], r["artist"], r["album"],
                 r["codec"], r["bitrate"], r["duration"], now),
            )
            c.execute("DELETE FROM tracks WHERE id = ?", (r["id"],))
            c.execute("DELETE FROM track_artists WHERE track_id = ?", (r["id"],))
    return len(hide)


def count() -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM duplicates")
    return int(row["n"]) if row else 0


def list_all(limit: int = 500) -> list[dict[str, Any]]:
    return db.query(
        "SELECT d.path, d.root, d.rel, d.size, d.title, d.artist, d.album, d.codec, d.bitrate, d.duration, "
        "d.track_id, t.path AS kept_path, t.rel AS kept_rel, t.codec AS kept_codec, t.bitrate AS kept_bitrate "
        "FROM duplicates d LEFT JOIN tracks t ON t.id = d.track_id ORDER BY d.artist, d.title LIMIT ?", (limit,)
    )
