"""Spotify-Vorschläge (über spotDL) inkl. Abgleich mit der eigenen Bibliothek."""

from __future__ import annotations

import re
import threading
import time
from typing import Any

from . import db
from .dedup import same_length
from .textutil import dup_key
from .tools import bridge

SPOTIFY_URL = re.compile(r"https?://(open\.spotify\.com|spotify\.link)/\S+", re.IGNORECASE)
YOUTUBE_URL = re.compile(r"https?://(www\.|music\.|m\.)?(youtube\.com|youtu\.be)/\S+", re.IGNORECASE)

_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()
CACHE_SECONDS = 15 * 60

_index: dict[str, Any] = {"version": None, "by_key": {}, "by_spotify": {}, "by_isrc": {}}
_index_lock = threading.Lock()
library_version = {"value": 0}


def invalidate_library_index(*_args) -> None:
    library_version["value"] += 1


def _library_index() -> dict[str, Any]:
    with _index_lock:
        if _index["version"] == library_version["value"]:
            return _index
        by_key: dict[str, list[tuple[str, float]]] = {}
        by_spotify, by_isrc = {}, {}
        for row in db.query("SELECT id, spotify_id, isrc, dkey, duration FROM tracks"):
            if row["spotify_id"]:
                by_spotify[row["spotify_id"]] = row["id"]
            if row["isrc"]:
                by_isrc[row["isrc"]] = row["id"]
            if row["dkey"]:
                by_key.setdefault(row["dkey"], []).append((row["id"], row["duration"]))
        _index.update(version=library_version["value"], by_key=by_key, by_spotify=by_spotify, by_isrc=by_isrc)
        return _index


def find_in_library(track: dict) -> str | None:
    """Gleiche Regel wie beim Einlesen: gleicher Song = Titel + Hauptinterpret + fast gleiche Länge."""
    idx = _library_index()
    if track.get("id") in idx["by_spotify"]:
        return idx["by_spotify"][track["id"]]
    if track.get("isrc") and track["isrc"] in idx["by_isrc"]:
        return idx["by_isrc"][track["isrc"]]
    artists = track.get("artists") or [""]
    duration = (track.get("duration_ms") or 0) / 1000
    for track_id, length in idx["by_key"].get(dup_key(track.get("name"), artists[0]), []):
        if same_length(duration, length):
            return track_id
    return None


def _mark(tracks: list[dict]) -> list[dict]:
    for t in tracks:
        t["library_id"] = find_in_library(t)
    return tracks


def _cached(key: str, fn):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    value = fn()
    with _cache_lock:
        if len(_cache) > 300:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:100]:
                _cache.pop(k, None)
        _cache[key] = (now, value)
    return value


def search(q: str) -> dict[str, Any]:
    q = q.strip()
    if not q:
        return {"mode": "empty"}
    url_match = SPOTIFY_URL.search(q)
    if url_match:
        url = url_match.group(0)
        if "spotify.link" in url:
            # Kurzlinks löst spotDL beim Herunterladen selbst auf
            return {"mode": "link", "item": {"kind": "link", "title": "Spotify-Kurzlink", "subtitle": url,
                                             "image": "", "url": url, "tracks": []}}
        item = _cached("resolve:" + url, lambda: bridge.call("resolve", url=url, timeout=180))
        item = dict(item)
        item["tracks"] = _mark([dict(t) for t in item.get("tracks", [])])
        return {"mode": "link", "item": item}
    if YOUTUBE_URL.search(q):
        url = YOUTUBE_URL.search(q).group(0)
        return {"mode": "link", "item": {"kind": "youtube", "title": "YouTube-Link", "subtitle": url,
                                         "image": "", "url": url, "tracks": []}}
    res = _cached("search:" + q.lower(), lambda: bridge.call("search", q=q, limit=10, timeout=45))
    res = dict(res)
    res["tracks"] = _mark([dict(t) for t in res.get("tracks", [])])
    return {"mode": "search", **res}
