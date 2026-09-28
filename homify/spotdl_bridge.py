"""
Brücke zu spotDL – läuft in der separaten spotDL-Umgebung (nicht in Homify selbst!).

spotDL pinnt eigene (alte) Versionen von FastAPI & Co., deshalb wohnt es in einer
eigenen virtuellen Umgebung. Homify startet dieses Skript dort und spricht über
stdin/stdout zeilenweise JSON mit ihm:

    -> {"id": 1, "cmd": "search", "q": "daft punk", "limit": 10}
    <- {"id": 1, "ok": true, "result": {...}}

Befehle: init, ping, search, resolve, setup (Deno für yt-dlp herunterladen)
"""

from __future__ import annotations

import io
import json
import re
import sys
import traceback

_PROTO_OUT = sys.stdout
_client = None
_settings: dict = {}


def _send(obj: dict) -> None:
    _PROTO_OUT.write(json.dumps(obj) + "\n")  # reines ASCII – unabhängig von der Konsolen-Codierung
    _PROTO_OUT.flush()


# --------------------------------------------------------------------------- #
# Spotify-Client (genau wie spotDL ihn erzeugt)
# --------------------------------------------------------------------------- #

def _get_client():
    global _client
    if _client is not None:
        return _client
    from spotdl.utils.config import DEFAULT_CONFIG
    from spotdl.utils.spotify import SpotifyClient

    client_id = _settings.get("client_id") or DEFAULT_CONFIG["client_id"]
    client_secret = _settings.get("client_secret") or DEFAULT_CONFIG["client_secret"]
    kwargs = dict(client_id=client_id, client_secret=client_secret, no_cache=True, max_retries=2)
    if _settings.get("use_official_api"):
        kwargs["use_official_api"] = True
    try:
        _client = SpotifyClient.init(**kwargs)
    except TypeError:  # ältere spotDL-Versionen ohne use_official_api
        kwargs.pop("use_official_api", None)
        _client = SpotifyClient.init(**kwargs)
    except Exception as exc:
        if "already been initialized" in str(exc):
            _client = SpotifyClient()
        else:
            raise
    return _client


# --------------------------------------------------------------------------- #
# Normalisierung – offizielle API und SpotipyFree liefern leicht andere Formate
# --------------------------------------------------------------------------- #

def _image(images, target: int = 300) -> str:
    best, best_diff = "", None
    for img in images or []:
        if not isinstance(img, dict) or not img.get("url"):
            continue
        width = img.get("width") or img.get("maxWidth") or target
        diff = abs(int(width) - target)
        if best_diff is None or diff < best_diff:
            best, best_diff = img["url"], diff
    return best


def _artist_names(artists) -> list[str]:
    names = []
    for a in artists or []:
        if isinstance(a, str):
            name = a
        elif isinstance(a, dict):
            name = a.get("name") or (a.get("profile") or {}).get("name") or ""
        else:
            name = ""
        if name and name not in names:
            names.append(name)
    return names


def _track(t: dict, album: dict | None = None) -> dict | None:
    if not isinstance(t, dict):
        return None
    if "track" in t and isinstance(t["track"], dict) and "name" not in t:
        t = t["track"]
    track_id = t.get("id") or t.get("track_id") or ""
    if not track_id or not t.get("name"):
        return None
    alb = t.get("album") if isinstance(t.get("album"), dict) and t.get("album") else (album or {})
    album_id = alb.get("id") or ""
    return {
        "type": "track",
        "id": track_id,
        "name": t.get("name", ""),
        "artists": _artist_names(t.get("artists")),
        "album": alb.get("name", ""),
        "album_id": album_id,
        "album_url": f"https://open.spotify.com/album/{album_id}" if album_id else "",
        "image": _image(alb.get("images")),
        "duration_ms": int(t.get("duration_ms") or 0),
        "explicit": bool(t.get("explicit")),
        "isrc": ((t.get("external_ids") or {}).get("isrc") or "").upper(),
        "year": str(alb.get("release_date") or "")[:4],
        "url": f"https://open.spotify.com/track/{track_id}",
    }


def _paged(client, first) -> list:
    items = list((first or {}).get("items") or [])
    nxt = first
    guard = 0
    while isinstance(nxt, dict) and nxt.get("next") and isinstance(nxt.get("next"), str) and guard < 20:
        try:
            nxt = client.next(nxt)
        except Exception:
            break
        items.extend((nxt or {}).get("items") or [])
        guard += 1
    return items


def cmd_search(q: str, limit: int = 10) -> dict:
    client = _get_client()
    limit = max(1, min(int(limit or 10), 10))
    raw = client.search(q, type="track,album,artist" if _settings.get("use_official_api") else "track", limit=limit)
    tracks = [x for x in (_track(t) for t in ((raw.get("tracks") or {}).get("items") or [])) if x][:limit]

    albums, seen = [], set()
    for a in ((raw.get("albums") or {}).get("items") or []):
        if a and a.get("id") and a["id"] not in seen:
            seen.add(a["id"])
            albums.append({
                "type": "album", "id": a["id"], "name": a.get("name", ""),
                "artists": _artist_names(a.get("artists")), "image": _image(a.get("images")),
                "year": str(a.get("release_date") or "")[:4],
                "url": f"https://open.spotify.com/album/{a['id']}",
            })
    if not albums:  # SpotipyFree liefert nur Songs -> Alben daraus ableiten
        for t in tracks:
            if t["album_id"] and t["album_id"] not in seen:
                seen.add(t["album_id"])
                albums.append({
                    "type": "album", "id": t["album_id"], "name": t["album"], "artists": t["artists"][:1],
                    "image": t["image"], "year": t["year"], "url": t["album_url"],
                })
    artists = []
    for a in ((raw.get("artists") or {}).get("items") or []):
        if a and a.get("id"):
            artists.append({
                "type": "artist", "id": a["id"], "name": a.get("name", ""), "image": _image(a.get("images")),
                "url": f"https://open.spotify.com/artist/{a['id']}",
            })
    return {"tracks": tracks, "albums": albums[:6], "artists": artists[:6]}


_URL = re.compile(r"open\.spotify\.com/(?:intl-[\w-]+/)?(track|album|playlist|artist)/([A-Za-z0-9]+)")


def cmd_resolve(url: str) -> dict:
    m = _URL.search(url)
    if not m:
        raise ValueError("Kein gültiger Spotify-Link")
    kind, item_id = m.group(1), m.group(2)
    client = _get_client()
    clean_url = f"https://open.spotify.com/{kind}/{item_id}"

    if kind == "track":
        t = _track(client.track(item_id))
        if not t:
            raise ValueError("Song nicht gefunden")
        return {"kind": "track", "title": t["name"], "subtitle": ", ".join(t["artists"]),
                "image": t["image"], "url": clean_url, "tracks": [t]}

    if kind == "album":
        album = client.album(item_id)
        album_info = {"id": item_id, "name": album.get("name", ""), "images": album.get("images"),
                      "release_date": album.get("release_date", "")}
        items = _paged(client, album.get("tracks") or {})
        tracks = [x for x in (_track(t, album_info) for t in items) if x]
        return {"kind": "album", "title": album.get("name", ""),
                "subtitle": ", ".join(_artist_names(album.get("artists"))),
                "image": _image(album.get("images")), "url": clean_url, "tracks": tracks}

    if kind == "playlist":
        pl = client.playlist(item_id)
        try:
            first = client.playlist_items(item_id)
            items = _paged(client, first)
        except Exception:
            items = ((pl.get("tracks") or {}).get("items")) or []
        tracks = [x for x in (_track(t) for t in items[:1000]) if x]
        owner = (pl.get("owner") or {}).get("display_name") or ""
        return {"kind": "playlist", "title": pl.get("name", ""), "subtitle": owner,
                "image": _image(pl.get("images")), "url": clean_url, "tracks": tracks}

    artist = client.artist(item_id)
    name = artist.get("name", "")
    tracks: list = []
    try:
        top = client.artist_top_tracks(item_id)
        tracks = [x for x in (_track(t) for t in (top or {}).get("tracks", [])) if x]
    except Exception:
        pass
    if not tracks and name:
        tracks = cmd_search(name, 10)["tracks"]
    return {"kind": "artist", "title": name, "subtitle": "Künstler", "image": _image(artist.get("images")),
            "url": clean_url, "tracks": tracks}


def cmd_setup() -> dict:
    """Deno (JavaScript-Laufzeit, die yt-dlp für YouTube braucht) bereitstellen."""
    result = {"deno": None}
    try:
        from spotdl.utils.deno import download_deno, get_deno_path

        path = get_deno_path()
        if path is None:
            path = download_deno()
        result["deno"] = str(path) if path else None
    except ImportError:
        result["deno"] = "nicht nötig (ältere spotDL-Version)"
    return result


def cmd_versions() -> dict:
    import importlib.metadata as md

    out = {}
    for pkg in ("spotdl", "yt-dlp", "spotipy", "spotipyfree", "ytmusicapi"):
        try:
            out[pkg] = md.version(pkg)
        except md.PackageNotFoundError:
            out[pkg] = None
    return out


def handle(msg: dict):
    cmd = msg.get("cmd")
    if cmd == "init":
        global _client
        _settings.clear()
        _settings.update(msg.get("settings") or {})
        _client = None
        return {"ok": True}
    if cmd == "ping":
        return "pong"
    if cmd == "search":
        return cmd_search(msg.get("q", ""), msg.get("limit", 10))
    if cmd == "resolve":
        return cmd_resolve(msg.get("url", ""))
    if cmd == "setup":
        return cmd_setup()
    if cmd == "versions":
        return cmd_versions()
    raise ValueError(f"Unbekannter Befehl: {cmd}")


def main() -> None:
    global _PROTO_OUT
    # Alle Ausgaben von Bibliotheken nach stderr umleiten, stdout gehört dem Protokoll.
    _PROTO_OUT = sys.stdout
    sys.stdout = sys.stderr
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8")
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        try:
            _send({"id": msg.get("id"), "ok": True, "result": handle(msg)})
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            _send({"id": msg.get("id"), "ok": False, "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    main()
