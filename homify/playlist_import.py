"""Spotify-Playlists übernehmen.

Holt man einen Spotify-Playlist-Link, entsteht dieselbe Playlist auch in Homify – in der Original-
Reihenfolge. Songs, die schon in der Bibliothek sind, stehen sofort drin; frisch geholte kommen nach
dem Download (und dem Einlesen) automatisch dazu. Holt man denselben Link später nochmal (z. B. weil
die Spotify-Playlist gewachsen ist), wird die vorhandene Playlist ergänzt statt neu angelegt.
Eigene Ergänzungen bleiben erhalten (hinten angehängt).
"""

from __future__ import annotations

import json
import time
from typing import Any

from . import db


def find_or_create(user_id: int, url: str, title: str, spotify_ids: list[str] | None = None) -> int:
    """Playlist zu diesem Link (pro Benutzer eine); merkt sich die Spotify-Songliste für spätere Ergänzungen."""
    ids = json.dumps(list(dict.fromkeys(spotify_ids or [])))
    row = db.query_one("SELECT id FROM playlists WHERE user_id = ? AND source_url = ?", (user_id, url))
    if row:
        if spotify_ids:
            db.execute("UPDATE playlists SET source_ids = ? WHERE id = ?", (ids, row["id"]))
        return int(row["id"])
    now = time.time()
    cur = db.execute(
        "INSERT INTO playlists (user_id, name, description, source_url, source_ids, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, (title or "Spotify-Playlist").strip()[:200], "Von Spotify übernommen", url, ids, now, now),
    )
    return int(cur.lastrowid)


def _desired(spotify_ids: list[str], known: dict[str, str]) -> list[str]:
    """Songs der Playlist in Original-Reihenfolge, soweit sie in der Bibliothek sind."""
    by_spotify: dict[str, str] = {}
    for i in range(0, len(spotify_ids), 500):
        chunk = spotify_ids[i:i + 500]
        for r in db.query("SELECT id, spotify_id FROM tracks WHERE spotify_id IN (%s)" % ",".join("?" * len(chunk)),
                          chunk):
            by_spotify[r["spotify_id"]] = r["id"]
    candidates = [by_spotify.get(sid) or known.get(sid) for sid in spotify_ids]
    candidates = [c for c in candidates if c]
    existing: set[str] = set()
    for i in range(0, len(candidates), 500):
        chunk = candidates[i:i + 500]
        existing |= {r["id"] for r in db.query("SELECT id FROM tracks WHERE id IN (%s)" % ",".join("?" * len(chunk)),
                                                chunk)}
    return list(dict.fromkeys(c for c in candidates if c in existing))


def sync(playlist_id: int, spotify_ids: list[str], known: dict[str, str] | None = None) -> int:
    """Fehlende Songs ergänzen und die Original-Reihenfolge herstellen. Gibt die Zahl neuer Songs zurück."""
    desired = _desired(spotify_ids, known or {})
    entries = db.query(
        "SELECT id, track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position, id", (playlist_id,))
    present = {e["track_id"] for e in entries}
    new = [t for t in desired if t not in present]
    if not new:
        return 0
    now = time.time()
    with db.transaction() as c:
        for tid in new:
            cur = c.execute("INSERT INTO playlist_tracks (playlist_id, track_id, position, added_at) VALUES (?,?,?,?)",
                            (playlist_id, tid, 0, now))
            entries.append({"id": cur.lastrowid, "track_id": tid})
        # Reihenfolge: erst die Playlist-Songs wie bei Spotify, dann eigene Ergänzungen wie bisher
        rank = {tid: i for i, tid in enumerate(desired)}
        ordered = sorted(entries, key=lambda e: (0, rank[e["track_id"]]) if e["track_id"] in rank else (1, 0))
        for pos, e in enumerate(ordered, start=1):
            c.execute("UPDATE playlist_tracks SET position = ? WHERE id = ?", (pos, e["id"]))
        c.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (now, playlist_id))
    return len(new)


def import_link(user_id: int, url: str, title: str, spotify_ids: list[str],
                known: dict[str, str] | None = None) -> int:
    pid = find_or_create(user_id, url, title, spotify_ids)
    sync(pid, spotify_ids, known)
    return pid


def sync_jobs(scanning: bool = False) -> int:
    """Nach Downloads: frisch geholte Songs in alle Playlists eintragen, die aus diesem Link stammen."""
    if scanning:
        return 0  # erst nach dem Einlesen – sonst fehlen die neuen Songs noch
    jobs = db.query(
        "SELECT id, query, spotify_ids, known_map FROM downloads WHERE playlist_synced = 0 "
        "AND status IN ('done', 'partial')")
    if not jobs:
        return 0
    imported = [(p["id"], p["source_url"], json.loads(p["source_ids"] or "[]"))
                for p in db.query("SELECT id, source_url, source_ids FROM playlists WHERE source_url != ''")]
    added = 0
    for job in jobs:
        sids = set(json.loads(job["spotify_ids"] or "[]"))
        known = json.loads(job["known_map"] or "{}")
        for pid, url, source_ids in imported:
            # Playlist-Link selbst geholt – oder einzelne Songs daraus („Holen“ bei einem Song)
            if url == job["query"] or sids.intersection(source_ids):
                added += sync(pid, source_ids or list(sids), known)
        db.execute("UPDATE downloads SET playlist_synced = 1 WHERE id = ?", (job["id"],))
    return added


def playlist_for(user_id: int, url: str) -> int | None:
    row = db.query_one("SELECT id FROM playlists WHERE user_id = ? AND source_url = ?", (user_id, url))
    return int(row["id"]) if row else None


def merge_known(job_id: int, known: dict[str, Any]) -> dict[str, str]:
    row = db.query_one("SELECT known_map FROM downloads WHERE id = ?", (job_id,))
    merged = {**json.loads((row or {}).get("known_map") or "{}"), **{k: v for k, v in known.items() if v}}
    return merged
