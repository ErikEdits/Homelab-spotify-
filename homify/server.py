"""FastAPI-Server: REST-API + Weboberfläche."""

from __future__ import annotations

import io
import logging
import json
import mimetypes
import re
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (APP_NAME, __version__, auth, backup, cookies, db, dedup, library, media, playlist_import, recommend, remote,
               user_prefs)
from . import storage as storages
from .config import APP_DIR, DATA_DIR, STATIC_DIR, config
from .covers import get_cover_file
from .downloader import downloads
from .loudness import analyzer
from .migrate import migration
from .scanner import scanner
from .settings_schema import EQ_PRESET_VALUES, SETTINGS, schema_json
from .spotify import invalidate_library_index
from .spotify import search as spotify_search
from .lyrics import parse_lyrics
from .metadata import read_embedded_lyrics
from .streaming import ranged_response
from .tools import BridgeError, bridge, tools

log = logging.getLogger("homify.server")

# Windows liest MIME-Typen aus der Registry – dort steht für .js oft "text/plain",
# dann lädt der Browser die Oberfläche nicht. Deshalb fest vorgeben:
for _mime, _ext in (("text/javascript", ".js"), ("text/css", ".css"), ("image/svg+xml", ".svg"),
                    ("application/manifest+json", ".webmanifest"), ("text/html", ".html"),
                    ("image/png", ".png"), ("application/json", ".json")):
    mimetypes.add_type(_mime, _ext)


# --------------------------------------------------------------------------- #
# Hintergrunddienste
# --------------------------------------------------------------------------- #

class Scheduler:
    """Hintergrund-Aufgaben: regelmäßiger Scan, Sicherung, Aufräumen, spotDL-Update, Lautheit."""

    def __init__(self):
        self._stop = threading.Event()
        self._last_hourly = 0.0

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="scheduler").start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        if config.get("scan_on_start"):
            scanner.start()
        else:
            analyzer.kick()
        while not self._stop.wait(60):
            try:
                interval = int(config.get("scan_interval_minutes") or 0)
                last = scanner.status.get("finished_at") or 0
                if interval > 0 and not scanner.status["running"] and time.time() - last > interval * 60:
                    scanner.start()
                if time.time() - self._last_hourly > 3600:
                    self._last_hourly = time.time()
                    self.hourly()
            except Exception:  # pragma: no cover - Hintergrund darf nie sterben
                log.exception("Zeitplaner-Fehler")
            finally:
                db.close()

    @staticmethod
    def hourly() -> None:
        backup.daily()
        downloads.prune_history()
        days = int(config.get("history_days") or 0)
        if days:
            db.execute("DELETE FROM plays WHERE played_at < ?", (time.time() - days * 86400,))
        db.execute("DELETE FROM skips WHERE skipped_at < ?", (time.time() - 365 * 86400,))
        analyzer.kick()
        if config.get("spotdl_auto_update") and tools.installed() and not downloads.active_count():
            stamp = DATA_DIR / "tools" / "last-update"
            try:
                last = stamp.stat().st_mtime
            except OSError:
                last = 0
            if time.time() - last > 7 * 86400:
                stamp.parent.mkdir(parents=True, exist_ok=True)
                stamp.write_text(time.strftime("%Y-%m-%d %H:%M"), encoding="utf-8")
                log.info("Wöchentliches spotDL-Update")
                bridge.stop()
                tools.install_async(upgrade=True)


scheduler = Scheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    apply_runtime_settings()
    scanner.listeners.append(invalidate_library_index)
    scanner.listeners.append(recommend.invalidate)
    scanner.listeners.append(lambda _st: playlist_import.sync_jobs())  # geholte Songs in importierte Playlists
    scanner.listeners.append(lambda _status: analyzer.kick())
    downloads.start()
    scheduler.start()
    log.info("%s %s läuft", APP_NAME, __version__)
    yield
    scheduler.stop()
    downloads.stop()
    bridge.stop()


app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan, docs_url="/api/docs", redoc_url=None)


@app.middleware("http")
async def headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if not path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(BridgeError)
async def bridge_error_handler(_request: Request, exc: BridgeError):
    return JSONResponse({"detail": str(exc)}, status_code=502)


# --------------------------------------------------------------------------- #
# Anmeldung / Einrichtung
# --------------------------------------------------------------------------- #

class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def _set_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        auth.COOKIE, token, max_age=auth.session_days() * 86400, httponly=True, samesite="lax",
        secure=request.url.scheme == "https", path="/",
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


@app.get("/api/setup")
def setup_status():
    return {"needs_setup": auth.user_count() == 0, "app": APP_NAME, "version": __version__,
            "name": config.get("server_name") or APP_NAME}


@app.post("/api/setup")
def setup(body: Credentials, request: Request, response: Response):
    if auth.user_count() > 0:
        raise HTTPException(409, "Bereits eingerichtet")
    user_id = auth.create_user(body.username, body.password, is_admin=True)
    _set_cookie(response, request, auth.create_session(user_id, request.headers.get("user-agent", "")))
    return {"ok": True}


@app.post("/api/auth/login")
def login(body: Credentials, request: Request, response: Response):
    ip = _client_ip(request)
    auth.check_rate_limit(ip)
    user = auth.authenticate(body.username, body.password)
    if not user:
        auth.record_failure(ip)
        raise HTTPException(401, "Benutzername oder Passwort falsch")
    _set_cookie(response, request, auth.create_session(user["id"], request.headers.get("user-agent", "")))
    return {"ok": True}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(auth.COOKIE)
    if token:
        auth.delete_session(token)
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: dict = Depends(auth.current_user)):
    return {**user, "server": {"name": config.get("server_name") or APP_NAME, "version": __version__,
                               "allow_file_download": bool(config.get("allow_file_download"))}}


# --------------------------------------------------------------------------- #
# Einstellungen: Liste aller 100, eigene (pro Benutzer)
# --------------------------------------------------------------------------- #

@app.get("/api/settings/schema")
def settings_schema(user: dict = Depends(auth.current_user)):
    return {"settings": schema_json(), "eq_presets": EQ_PRESET_VALUES, "count": len(SETTINGS)}


@app.get("/api/me/settings")
def my_settings(user: dict = Depends(auth.current_user)):
    return user_prefs.get_all(user["id"])


@app.put("/api/me/settings")
def put_my_settings(values: dict[str, Any], user: dict = Depends(auth.current_user)):
    return user_prefs.update(user["id"], values)


@app.delete("/api/me/settings")
def reset_my_settings(user: dict = Depends(auth.current_user)):
    return user_prefs.reset_all(user["id"])


class PasswordChange(BaseModel):
    old_password: str
    new_password: str = Field(min_length=4, max_length=256)


@app.post("/api/auth/password")
def change_password(body: PasswordChange, request: Request, response: Response,
                    user: dict = Depends(auth.current_user)):
    row = db.query_one("SELECT pw_hash FROM users WHERE id = ?", (user["id"],))
    if not row or not auth.verify_password(body.old_password, row["pw_hash"]):
        raise HTTPException(400, "Altes Passwort ist falsch")
    auth.set_password(user["id"], body.new_password)
    _set_cookie(response, request, auth.create_session(user["id"], request.headers.get("user-agent", "")))
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Bibliothek
# --------------------------------------------------------------------------- #

@app.get("/api/home")
def home(limit: int = 12, user: dict = Depends(auth.current_user)):
    limit = min(max(limit, 4), 30)
    data = library.home(user["id"], limit)
    data["scan"] = scanner.status
    data["feed"] = _feed(user["id"], limit)
    return data


def _feed(user_id: int, limit: int) -> dict[str, Any]:
    """Persönliche Empfehlungen für die Startseite (Daily Mixes, Mix der Woche, …)."""
    try:
        because = recommend.because_you_listened(user_id, limit)
        return {
            "made_for_you": recommend.made_for_you(user_id),
            "because": {"title": because["title"], "albums": library.album_json(library.albums_by_ids(because["album_ids"]))}
            if because else None,
            "top_genres": library.mixes_for(recommend.top_genres(user_id, limit)),
        }
    except Exception:  # der Feed darf die Startseite nie kaputt machen
        log.exception("Feed konnte nicht berechnet werden")
        return {"made_for_you": [], "because": None, "top_genres": []}


@app.get("/api/search")
def search(q: str = "", user: dict = Depends(auth.current_user)):
    return library.search(q, user["id"])


@app.get("/api/genres")
def genres(user: dict = Depends(auth.current_user)):
    return library.genres()


@app.get("/api/tracks")
def tracks(sort: str = "title", limit: int = 200, offset: int = 0, user: dict = Depends(auth.current_user)):
    return library.list_tracks(user["id"], sort, min(max(limit, 1), 1000), max(offset, 0))


class TrackIds(BaseModel):
    ids: list[str] = Field(max_length=5000)


@app.post("/api/tracks/lookup")
def tracks_lookup(body: TrackIds, user: dict = Depends(auth.current_user)):
    return library.tracks_json(library.get_track_rows(body.ids), user["id"])


def _track_or_404(track_id: str) -> dict[str, Any]:
    row = db.query_one("SELECT * FROM tracks WHERE id = ?", (track_id,))
    if not row:
        raise HTTPException(404, "Song nicht gefunden")
    return row


@app.get("/api/tracks/{track_id}")
def track(track_id: str, user: dict = Depends(auth.current_user)):
    rows = library.get_track_rows([track_id])
    if not rows:
        raise HTTPException(404, "Song nicht gefunden")
    return library.tracks_json(rows, user["id"])[0]


def _source(t: dict[str, Any]):
    """Speicherort + relativer Pfad eines Songs. Wirft 404, wenn die Datei nicht erreichbar ist."""
    st = storages.by_key(t["root"])
    if st is not None and t.get("rel"):
        return st, t["rel"]
    if os.path.isfile(t["path"]):  # Einträge aus älteren Versionen
        return storages.LocalStorage(os.path.dirname(t["path"])), os.path.basename(t["path"])
    raise HTTPException(404, "Datei nicht erreichbar – ist das NAS online?")


def _temp_copy(st, rel: str, track_id: str) -> str:
    """NAS-Datei für ffmpeg in eine temporäre lokale Datei holen."""
    tmp_dir = DATA_DIR / "tmp" / "source"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    target = tmp_dir / f"{track_id}{os.path.splitext(rel)[1]}"
    with st.open(rel) as src, open(target, "wb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
    return str(target)


def _serve(request: Request, st, rel: str, media_type: str, headers: dict[str, str]) -> Response:
    local = st.local_path(rel)
    if local:
        if not os.path.isfile(local):
            raise HTTPException(404, "Datei nicht gefunden – wurde sie verschoben?")
        return FileResponse(local, media_type=media_type, headers=headers)
    info = st.stat(rel)
    if info is None:
        raise HTTPException(404, "Datei auf dem NAS nicht erreichbar")
    size, mtime = info
    return ranged_response(request, lambda: st.open(rel), size, mtime, media_type, headers)


@app.get("/api/tracks/{track_id}/stream")
def stream(track_id: str, request: Request, transcode: int = 0, quality: str = "original",
           user: dict = Depends(auth.current_user)):
    t = _track_or_404(track_id)
    st, rel = _source(t)
    headers = {"Cache-Control": "private, max-age=86400"}
    wanted = quality if quality in media.QUALITIES else "high"
    if transcode or (quality in media.QUALITIES and media.needs_transcode_for_quality(t["bitrate"], quality)):
        local = st.local_path(rel)
        temp: list[str] = []

        def source() -> str:
            if local:
                return local
            temp.append(_temp_copy(st, rel, track_id))
            return temp[0]

        try:
            out, mime = media.transcode(t, wanted, source=source)
        except media.TranscodeError as exc:
            raise HTTPException(500, f"Umwandlung fehlgeschlagen: {exc}") from exc
        except OSError as exc:
            raise HTTPException(404, f"Datei nicht erreichbar: {exc}") from exc
        finally:
            for f in temp:
                try:
                    os.remove(f)
                except OSError:
                    pass
        return FileResponse(out, media_type=mime, headers=headers)
    mime = (t["mime"] or "application/octet-stream").split(";")[0]
    return _serve(request, st, rel, mime, headers)


@app.get("/api/tracks/{track_id}/lyrics")
def lyrics(track_id: str, user: dict = Depends(auth.current_user)):
    """Songtext: .lrc/.txt neben der Datei (mit Zeitstempeln) oder aus den Tags."""
    t = _track_or_404(track_id)
    st, rel = _source(t)
    base = os.path.splitext(rel)[0]
    for ext in (".lrc", ".LRC", ".txt"):
        try:
            with st.open(base + ext) as fh:
                data = fh.read(512 * 1024).decode("utf-8-sig", errors="replace")
            parsed = parse_lyrics(data)
            if parsed["lines"]:
                return parsed
        except OSError:
            continue
    local = st.local_path(rel)
    try:
        if local:
            text = read_embedded_lyrics(local)
        else:
            with st.open(rel) as fh:
                text = read_embedded_lyrics(fh, name=rel)
    except OSError:
        text = ""
    parsed = parse_lyrics(text)
    if not parsed["lines"]:
        raise HTTPException(404, "Kein Songtext vorhanden")
    return parsed


@app.get("/api/tracks/{track_id}/file")
def download_file(track_id: str, request: Request, user: dict = Depends(auth.current_user)):
    if not config.get("allow_file_download") and not user["is_admin"]:
        raise HTTPException(403, "Herunterladen aufs Gerät ist ausgeschaltet")
    t = _track_or_404(track_id)
    st, rel = _source(t)
    name = os.path.basename(rel)
    disposition = "attachment; filename*=UTF-8''" + urllib.parse.quote(name)
    return _serve(request, st, rel, "application/octet-stream", {"Content-Disposition": disposition})


@app.get("/api/covers/{cover_id}")
def cover(cover_id: str, size: int = 0, user: dict = Depends(auth.current_user)):
    found = get_cover_file(cover_id, size or None)
    if not found:
        raise HTTPException(404)
    path, media_type = found
    return FileResponse(path, media_type=media_type,
                        headers={"Cache-Control": "private, max-age=31536000, immutable"})


@app.get("/api/albums")
def albums(sort: str = "name", limit: int = 500, offset: int = 0, user: dict = Depends(auth.current_user)):
    return library.list_albums(sort, min(max(limit, 1), 5000), max(offset, 0))


@app.get("/api/albums/{album_id}")
def album(album_id: str, user: dict = Depends(auth.current_user)):
    data = library.album_detail(album_id, user["id"])
    if not data:
        raise HTTPException(404, "Album nicht gefunden")
    return data


@app.get("/api/artists")
def artists(limit: int = 2000, offset: int = 0, user: dict = Depends(auth.current_user)):
    return library.list_artists(min(max(limit, 1), 10000), max(offset, 0))


@app.get("/api/artists/{artist_id}")
def artist(artist_id: str, user: dict = Depends(auth.current_user)):
    data = library.artist_detail(artist_id, user["id"])
    if not data:
        raise HTTPException(404, "Künstler nicht gefunden")
    return data


@app.get("/api/artists/{artist_id}/tracks")
def artist_tracks(artist_id: str, user: dict = Depends(auth.current_user)):
    return library.artist_all_tracks(artist_id, user["id"])


MIX_TITLES = {"random": ("Zufallsmix", "Quer durch deine Bibliothek")}


def _mix(kind: str, value: str, user_id: int, limit: int) -> dict[str, Any]:
    limit = min(max(limit, 5), 300)
    if kind in recommend.FEED_KINDS:
        ids, name, subtitle = recommend.mix(kind, value, user_id, limit)
        return {"name": name, "subtitle": subtitle, "tracks": library.tracks_json(library.get_track_rows(ids), user_id)}
    if kind not in ("genre", "radio", "random"):
        raise HTTPException(404, "Diesen Mix gibt es nicht")
    tracks = library.mix_tracks(kind, value, user_id, limit)
    if kind == "genre":
        name, subtitle = f"{value} Mix", f"Das Beste aus {value} in deiner Bibliothek"
    elif kind == "radio":
        name = f"{tracks[0]['title']} Radio" if tracks else "Song-Radio"
        subtitle = "Ähnliche Songs aus deiner Bibliothek (gleicher Künstler, Genre und Ära)"
    else:
        name, subtitle = MIX_TITLES["random"]
    return {"name": name, "subtitle": subtitle, "tracks": tracks}


@app.get("/api/mix/{kind}")
def mix(kind: str, value: str = "", limit: int = 60, user: dict = Depends(auth.current_user)):
    return _mix(kind, value, user["id"], limit)["tracks"]


@app.get("/api/mix/{kind}/detail")
def mix_detail(kind: str, value: str = "", limit: int = 60, user: dict = Depends(auth.current_user)):
    data = _mix(kind, value, user["id"], limit)
    return {"kind": kind, "value": value, "feed": kind in recommend.FEED_KINDS, **data}


# --------------------------------------------------------------------------- #
# Lieblingssongs, Verlauf, Playlists
# --------------------------------------------------------------------------- #

@app.get("/api/likes")
def likes(user: dict = Depends(auth.current_user)):
    return library.liked_tracks(user["id"])


@app.put("/api/likes/{track_id}")
def like(track_id: str, user: dict = Depends(auth.current_user)):
    _track_or_404(track_id)
    db.execute("INSERT OR IGNORE INTO likes (user_id, track_id, liked_at) VALUES (?, ?, ?)",
               (user["id"], track_id, time.time()))
    return {"liked": True}


@app.delete("/api/likes/{track_id}")
def unlike(track_id: str, user: dict = Depends(auth.current_user)):
    db.execute("DELETE FROM likes WHERE user_id = ? AND track_id = ?", (user["id"], track_id))
    return {"liked": False}


class PlayEvent(BaseModel):
    track_id: str


@app.post("/api/history")
def add_history(body: PlayEvent, user: dict = Depends(auth.current_user)):
    _track_or_404(body.track_id)
    db.execute("INSERT INTO plays (user_id, track_id, played_at) VALUES (?, ?, ?)",
               (user["id"], body.track_id, time.time()))
    return {"ok": True}


@app.post("/api/history/skip")
def add_skip(body: PlayEvent, user: dict = Depends(auth.current_user)):
    """Früh übersprungen: der Feed schlägt diesen Song seltener vor."""
    _track_or_404(body.track_id)
    recommend.record_skip(user["id"], body.track_id)
    return {"ok": True}


@app.get("/api/history")
def history(user: dict = Depends(auth.current_user)):
    return library.recent_tracks(user["id"], 50)


class PlaylistIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    track_ids: list[str] = Field(default_factory=list, max_length=5000)


class PlaylistPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    public: bool | None = None  # veröffentlichen = für alle Homify-Benutzer sichtbar


class PlaylistGenerate(BaseModel):
    source: str = Field(pattern="^(song|artist|genre|decade|top|liked|new|rediscover|random)$")
    value: str = Field(default="", max_length=300)
    count: int = Field(default=50, ge=5, le=500)
    name: str = Field(default="", max_length=200)
    public: bool = False


class PlaylistOrder(BaseModel):
    entry_ids: list[int] = Field(max_length=20000)


def _own_playlist(playlist_id: int, user: dict) -> dict[str, Any]:
    row = db.query_one("SELECT * FROM playlists WHERE id = ?", (playlist_id,))
    if not row or row["user_id"] != user["id"]:
        raise HTTPException(404, "Playlist nicht gefunden")
    return row


def _append_tracks(playlist_id: int, track_ids: list[str]) -> int:
    existing = {r["id"] for r in db.query("SELECT id FROM tracks WHERE id IN (%s)" % ",".join("?" * len(track_ids)),
                                          track_ids)} if track_ids else set()
    row = db.query_one("SELECT COALESCE(MAX(position), 0) AS p FROM playlist_tracks WHERE playlist_id = ?",
                       (playlist_id,))
    pos = row["p"] if row else 0
    now = time.time()
    added = 0
    with db.transaction() as c:
        for tid in track_ids:
            if tid in existing:
                pos += 1
                c.execute("INSERT INTO playlist_tracks (playlist_id, track_id, position, added_at) VALUES (?,?,?,?)",
                          (playlist_id, tid, pos, now))
                added += 1
        c.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (now, playlist_id))
    return added


@app.get("/api/playlists")
def playlists(user: dict = Depends(auth.current_user)):
    return library.user_playlists(user["id"])


def _new_playlist(user_id: int, name: str, description: str = "", track_ids: list[str] | None = None,
                  public: bool = False) -> int:
    now = time.time()
    cur = db.execute(
        "INSERT INTO playlists (user_id, name, description, public, published_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, name.strip(), description.strip(), int(public), now if public else None, now, now),
    )
    pid = int(cur.lastrowid)
    if track_ids:
        _append_tracks(pid, track_ids[:5000])
    return pid


@app.post("/api/playlists")
def create_playlist(body: PlaylistIn, user: dict = Depends(auth.current_user)):
    pid = _new_playlist(user["id"], body.name, body.description, body.track_ids)
    return library.playlist_summary(pid, user["id"])


@app.get("/api/playlists/public")
def shared_playlists(user: dict = Depends(auth.current_user)):
    """Von anderen Benutzern veröffentlichte Playlists."""
    return library.public_playlists(user["id"])


@app.get("/api/playlists/generator")
def playlist_generator_options(user: dict = Depends(auth.current_user)):
    return library.generator_options(user["id"])


@app.post("/api/playlists/generate")
def generate_playlist(body: PlaylistGenerate, user: dict = Depends(auth.current_user)):
    """Playlist automatisch zusammenstellen (ähnlich wie ein Song/Künstler, Genre, Jahrzehnt, Top-Songs …)."""
    ids, suggested = library.generate_playlist_tracks(body.source, body.value, user["id"], body.count)
    if not ids:
        raise HTTPException(400, "Dafür gibt es (noch) keine passenden Songs.")
    desc = f"Automatisch zusammengestellt am {time.strftime('%d.%m.%Y')}"
    pid = _new_playlist(user["id"], body.name.strip() or suggested, desc, ids, body.public)
    return library.playlist_summary(pid, user["id"])


class PlaylistImport(BaseModel):
    url: str = Field(pattern=r"^https://open\.spotify\.com/", max_length=500)
    title: str = Field(default="", max_length=200)
    spotify_ids: list[str] = Field(min_length=1, max_length=5000)
    library_ids: dict[str, str] = Field(default_factory=dict)


@app.post("/api/playlists/import")
def import_spotify_playlist(body: PlaylistImport, user: dict = Depends(auth.current_user)):
    """Spotify-Playlist mit den vorhandenen Songs als Homify-Playlist speichern (ohne Download)."""
    pid = playlist_import.import_link(user["id"], body.url, body.title, body.spotify_ids, body.library_ids)
    return library.playlist_summary(pid, user["id"])


@app.get("/api/playlists/{playlist_id}")
def playlist(playlist_id: int, user: dict = Depends(auth.current_user)):
    data = library.playlist_detail(playlist_id, user["id"])
    if not data:
        raise HTTPException(404, "Playlist nicht gefunden")
    return data


@app.patch("/api/playlists/{playlist_id}")
def update_playlist(playlist_id: int, body: PlaylistPatch, user: dict = Depends(auth.current_user)):
    row = _own_playlist(playlist_id, user)
    if body.public is not None and bool(row["public"]) != body.public:
        db.execute("UPDATE playlists SET public = ?, published_at = ? WHERE id = ?",
                   (int(body.public), time.time() if body.public else None, playlist_id))
    if body.name is not None:
        db.execute("UPDATE playlists SET name = ?, updated_at = ? WHERE id = ?",
                   (body.name.strip(), time.time(), playlist_id))
    if body.description is not None:
        db.execute("UPDATE playlists SET description = ?, updated_at = ? WHERE id = ?",
                   (body.description.strip(), time.time(), playlist_id))
    return library.playlist_summary(playlist_id, user["id"])


def _public_playlist(playlist_id: int, user: dict) -> dict[str, Any]:
    row = db.query_one("SELECT * FROM playlists WHERE id = ?", (playlist_id,))
    if not row or (row["user_id"] != user["id"] and not row["public"]):
        raise HTTPException(404, "Playlist nicht gefunden")
    return row


@app.put("/api/playlists/{playlist_id}/follow")
def follow_playlist(playlist_id: int, user: dict = Depends(auth.current_user)):
    row = _public_playlist(playlist_id, user)
    if row["user_id"] == user["id"]:
        raise HTTPException(400, "Das ist deine eigene Playlist.")
    db.execute("INSERT OR IGNORE INTO playlist_follows (user_id, playlist_id, followed_at) VALUES (?, ?, ?)",
               (user["id"], playlist_id, time.time()))
    return library.playlist_summary(playlist_id, user["id"])


@app.delete("/api/playlists/{playlist_id}/follow")
def unfollow_playlist(playlist_id: int, user: dict = Depends(auth.current_user)):
    db.execute("DELETE FROM playlist_follows WHERE user_id = ? AND playlist_id = ?", (user["id"], playlist_id))
    return {"ok": True}


@app.post("/api/playlists/{playlist_id}/copy")
def copy_playlist(playlist_id: int, user: dict = Depends(auth.current_user)):
    """Veröffentlichte (oder eigene) Playlist als eigene, bearbeitbare Kopie übernehmen."""
    row = _public_playlist(playlist_id, user)
    ids = [r["track_id"] for r in db.query(
        "SELECT track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position, id", (playlist_id,))]
    name = row["name"] if row["user_id"] != user["id"] else f"{row['name']} (Kopie)"
    pid = _new_playlist(user["id"], name, row["description"], ids)
    return library.playlist_summary(pid, user["id"])


@app.delete("/api/playlists/{playlist_id}")
def delete_playlist(playlist_id: int, user: dict = Depends(auth.current_user)):
    _own_playlist(playlist_id, user)
    db.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
    return {"ok": True}


@app.post("/api/playlists/{playlist_id}/tracks")
def add_to_playlist(playlist_id: int, body: TrackIds, user: dict = Depends(auth.current_user)):
    _own_playlist(playlist_id, user)
    return {"added": _append_tracks(playlist_id, body.ids)}


@app.delete("/api/playlists/{playlist_id}/entries/{entry_id}")
def remove_from_playlist(playlist_id: int, entry_id: int, user: dict = Depends(auth.current_user)):
    _own_playlist(playlist_id, user)
    db.execute("DELETE FROM playlist_tracks WHERE id = ? AND playlist_id = ?", (entry_id, playlist_id))
    db.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (time.time(), playlist_id))
    return {"ok": True}


@app.put("/api/playlists/{playlist_id}/order")
def reorder_playlist(playlist_id: int, body: PlaylistOrder, user: dict = Depends(auth.current_user)):
    _own_playlist(playlist_id, user)
    with db.transaction() as c:
        for pos, entry_id in enumerate(body.entry_ids, start=1):
            c.execute("UPDATE playlist_tracks SET position = ? WHERE id = ? AND playlist_id = ?",
                      (pos, entry_id, playlist_id))
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Spotify-Vorschläge & Downloads
# --------------------------------------------------------------------------- #

@app.get("/api/spotify/search")
def spotify(q: str, user: dict = Depends(auth.current_user)):
    if not tools.installed():
        raise HTTPException(503, "spotDL ist noch nicht installiert. Ein Admin kann es unter Einstellungen installieren.")
    return spotify_search(q)


class DownloadIn(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    kind: str = "track"
    title: str = ""
    subtitle: str = ""
    image: str = ""
    spotify_ids: list[str] = Field(default_factory=list, max_length=5000)
    total: int = 0
    library_ids: dict[str, str] = Field(default_factory=dict)  # Spotify-ID -> Song, der schon da ist


@app.get("/api/downloads")
def list_downloads(user: dict = Depends(auth.current_user)):
    playlist_import.sync_jobs(scanning=bool(scanner.status.get("running")))
    jobs = downloads.list()
    # Fertige Songs gleich mitliefern, damit man sie direkt abspielen kann
    for job in jobs:
        if job["status"] in ("done", "partial") and (job["spotify_ids"] or job["known_ids"]):
            ids = job["spotify_ids"][:500]
            rows = db.query(
                "SELECT id FROM tracks WHERE spotify_id IN (%s)" % ",".join("?" * len(ids)), ids
            ) if ids else []
            # + Songs, die schon vorher da waren (nicht doppelt geladen)
            known = [r["id"] for r in library.get_track_rows(job["known_ids"][:500])] if job["known_ids"] else []
            job["track_ids"] = list(dict.fromkeys([r["id"] for r in rows] + known))
            if job["track_ids"] and not job.get("auto_liked") and not scanner.status.get("running"):
                _auto_like(job)
        if job["kind"] == "playlist":
            job["playlist_id"] = playlist_import.playlist_for(user["id"], job["query"])
        if not user["is_admin"] and job.get("user_id") != user["id"]:
            job.pop("log", None)
    return {"jobs": jobs, "active": downloads.active_count(), "scan": scanner.status}


def _auto_like(job: dict[str, Any]) -> None:
    """„Geholte Songs automatisch zu Lieblingssongs“: einmal pro fertigem Download prüfen."""
    db.execute("UPDATE downloads SET auto_liked = 1 WHERE id = ?", (job["id"],))
    job["auto_liked"] = 1
    uid = job.get("user_id")
    if not uid or not user_prefs.get(uid, "auto_like_downloads"):
        return
    now = time.time()
    with db.transaction() as c:
        for tid in job["track_ids"]:
            c.execute("INSERT OR IGNORE INTO likes (user_id, track_id, liked_at) VALUES (?, ?, ?)", (uid, tid, now))


@app.post("/api/downloads")
def add_download(body: DownloadIn, user: dict = Depends(auth.download_user)):
    return downloads.add(user["id"], body.query, body.kind, body.title, body.subtitle, body.image,
                         body.spotify_ids, body.total, body.library_ids)


@app.post("/api/downloads/{job_id}/cancel")
def cancel_download(job_id: int, user: dict = Depends(auth.download_user)):
    downloads.cancel(job_id)
    return {"ok": True}


@app.post("/api/downloads/{job_id}/retry")
def retry_download(job_id: int, user: dict = Depends(auth.download_user)):
    downloads.retry(job_id)
    return {"ok": True}


@app.delete("/api/downloads/{job_id}")
def delete_download(job_id: int, user: dict = Depends(auth.download_user)):
    downloads.remove(job_id)
    return {"ok": True}


@app.delete("/api/downloads")
def clear_downloads(user: dict = Depends(auth.download_user)):
    downloads.clear_finished()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Admin: Einstellungen, System, Benutzer
# --------------------------------------------------------------------------- #

def _lan_addresses() -> list[str]:
    ips: set[str] = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


@app.get("/api/status")
def status(user: dict = Depends(auth.current_user)):
    return {"scan": scanner.status, "downloads_active": downloads.active_count(), "tools": tools.status}


STORAGE_KEYS = ("storage_mode", "storage_path", "nas_host", "nas_share", "nas_folder", "nas_user", "nas_password")


def _storage_info() -> dict[str, Any]:
    st = storages.primary()
    ok, message = st.available()
    counts = {r["root"]: r["n"] for r in db.query("SELECT root, COUNT(*) AS n FROM tracks GROUP BY root")}
    return {
        "mode": config.get("storage_mode"),
        "label": st.label,
        "kind": st.kind,
        "online": ok,
        "message": message,
        "tracks": counts.get(st.key, 0),
        "free_bytes": st.free_space() if ok else None,
        "local_dir": str(storages.LOCAL_MUSIC_DIR),
        "extra": [{"path": x.label, "online": x.available()[0], "tracks": counts.get(x.key, 0)}
                  for x in storages.extra()],
    }


@app.get("/api/server-info")
def server_info(user: dict = Depends(auth.current_user)):
    """Adressen für die Apps (Heimnetz + Tailscale)."""
    port = int(config.get("port"))
    r = remote.info(port)
    return {"lan": [f"http://{ip}:{port}" for ip in _lan_addresses()], "remote": r.get("urls", []),
            "https": r.get("https_url", "")}


@app.get("/api/settings")
def get_settings(user: dict = Depends(auth.admin_user)):
    port = config.get("port")
    return {
        "settings": config.public(),
        "system": {
            "version": __version__,
            "ffmpeg": media.ffmpeg_version(),
            "spotdl_installed": tools.installed(),
            "spotdl": tools.versions(),
            "tools": tools.status,
            "scan": scanner.status,
            "stats": library.stats(),
            "cache_mb": round(media.cache_size() / 1024 / 1024, 1),
            "urls": [f"http://{ip}:{port}" for ip in _lan_addresses()],
            "hostname": socket.gethostname(),
            "platform": sys.platform,
            "storage": _storage_info(),
            "migration": migration.status,
            "remote": remote.info(port),
            "restart_needed": restart_needed(),
            "loudness": {**analyzer.status, "remaining": analyzer.remaining()},
            "backups": backup.list_backups(),
            "duplicates": dedup.count(),
            "youtube_cookies": cookies.status(),
        },
    }


@app.put("/api/settings")
def put_settings(values: dict[str, Any], user: dict = Depends(auth.admin_user)):
    old_dirs = config.music_dirs
    scan_keys = {"ignore_folders", "min_track_seconds", "folder_as_album", "filename_parsing", "split_feat",
                 "artist_separators", "prefer_folder_cover"}
    before = {k: config.get(k) for k in scan_keys}
    config.update({k: v for k, v in values.items() if k not in STORAGE_KEYS})  # Speicherort nur über /api/storage
    apply_runtime_settings()
    if config.music_dirs != old_dirs:
        scanner.start()
    elif any(config.get(k) != before[k] for k in scan_keys):
        scanner.start(full=True)  # Einlese-Regeln geändert -> alles neu auswerten
    return get_settings(user)


def apply_runtime_settings() -> None:
    """Einstellungen, die sofort wirken sollen (ohne Neustart)."""
    level = getattr(logging, str(config.get("log_level") or "INFO"), logging.INFO)
    logging.getLogger().setLevel(level)
    media.find_ffmpeg(refresh=True)


def restart_needed() -> bool:
    running = os.environ.get("HOMIFY_BIND", "")
    return bool(running) and running != f"{config.get('host')}:{config.get('port')}"


# --------------------------------------------------------------------------- #
# Speicherort: App-Ordner / UGREEN-NAS (SMB) / eigener Ordner + Umzug
# --------------------------------------------------------------------------- #

class StorageIn(BaseModel):
    storage_mode: str = Field(pattern="^(local|nas|folder)$")
    storage_path: str = ""
    nas_host: str = ""
    nas_share: str = ""
    nas_folder: str = ""
    nas_user: str = ""
    nas_password: str = ""
    transfer: bool = True
    keep_copy: bool = False


def _storage_values(body: StorageIn) -> dict[str, Any]:
    values = body.model_dump(include=set(STORAGE_KEYS))
    if values["nas_password"] == "********":  # Platzhalter -> gespeichertes Passwort
        values["nas_password"] = config.get("nas_password")
    return values


@app.get("/api/storage")
def storage_status(user: dict = Depends(auth.admin_user)):
    return {"storage": _storage_info(), "migration": migration.status}


@app.post("/api/storage/test")
def storage_test(body: StorageIn, user: dict = Depends(auth.admin_user)):
    values = _storage_values(body)
    if body.storage_mode == "nas" and not (values["nas_host"] and values["nas_share"]):
        return {"ok": False, "message": "Bitte NAS-Adresse und Freigabe eintragen."}
    st = storages.storage_from_settings(values)
    ok, message = st.available()
    if not ok and body.storage_mode == "nas" and "gibt es auf dem NAS nicht" in message:
        # Unterordner fehlt noch – wird beim Übernehmen angelegt; Schreibrechte prüfen
        writable, wmsg = st.check_writable()
        return {"ok": writable, "message": "Verbunden – der Ordner wird angelegt." if writable else wmsg,
                "label": st.label}
    if ok and hasattr(st, "check_writable"):
        writable, wmsg = st.check_writable()
        if not writable:
            return {"ok": False, "message": f"Verbunden, aber: {wmsg}", "label": st.label}
    return {"ok": ok, "message": message, "label": st.label}


@app.post("/api/storage/apply")
def storage_apply(body: StorageIn, user: dict = Depends(auth.admin_user)):
    if migration.running:
        raise HTTPException(409, "Es läuft bereits ein Umzug.")
    values = _storage_values(body)
    source = storages.primary()
    target = storages.storage_from_settings(values)
    if target.key == source.key:
        config.update(values)
        scanner.start()
        return {"started": False, "message": "Gespeichert."}
    if hasattr(target, "ensure_base"):
        try:
            target.ensure_base()
        except Exception as exc:
            raise HTTPException(400, f"NAS-Ordner konnte nicht angelegt werden: {exc}") from exc
    ok, message = target.available()
    if not ok:
        raise HTTPException(400, message)
    if not body.transfer or not source.available()[0]:
        config.update(values)
        scanner.start()
        return {"started": False, "message": "Speicherort gewechselt."}

    def done(success: bool) -> None:
        config.update(values)  # erst nach dem Umzug umschalten
        scanner.start()

    downloads.pause(True)
    migration.start(source, target, keep_copy=body.keep_copy,
                    on_done=lambda ok: (done(ok), downloads.pause(False)))
    return {"started": True, "migration": migration.status}


@app.get("/api/remote")
def remote_info(user: dict = Depends(auth.admin_user)):
    return remote.info(int(config.get("port")), refresh=True)


@app.post("/api/remote/https")
def remote_https(user: dict = Depends(auth.admin_user)):
    ok, message = remote.setup_https(int(config.get("port")))
    return {"ok": ok, "message": message, "info": remote.info(int(config.get("port")), refresh=True)}


@app.post("/api/storage/cancel")
def storage_cancel(user: dict = Depends(auth.admin_user)):
    migration.cancel()
    return migration.status


# --------------------------------------------------------------------------- #
# Sicherung: Einstellungen + Datenbank (Playlists, Likes, Benutzer) mitnehmen,
# z. B. vom Windows-Test-PC auf den Ubuntu-Homeserver
# --------------------------------------------------------------------------- #

@app.get("/api/backup")
def download_backup(user: dict = Depends(auth.admin_user)):
    name = time.strftime("homify-sicherung-%Y-%m-%d.zip")
    return Response(backup.create_zip_bytes(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/backups")
def create_backup_now(user: dict = Depends(auth.admin_user)):
    path = backup.create_file()
    backup.prune(int(config.get("backup_keep") or 7))
    return {"name": path.name, "backups": backup.list_backups()}


@app.get("/api/backups/{name}")
def download_stored_backup(name: str, user: dict = Depends(auth.admin_user)):
    if not re.fullmatch(r"homify-sicherung-[\w-]+\.zip", name):
        raise HTTPException(400, "Ungültiger Name")
    path = backup.BACKUP_DIR / name
    if not path.is_file():
        raise HTTPException(404, "Sicherung nicht gefunden")
    return FileResponse(path, media_type="application/zip", filename=name)


@app.post("/api/backup/restore")
async def restore(request: Request, keep_storage: bool = True, user: dict = Depends(auth.admin_user)):
    data = await request.body()
    if len(data) > 500 * 1024 * 1024:
        raise HTTPException(413, "Datei zu groß")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        names = set(z.namelist())
        if "homify.db" not in names:
            raise ValueError
    except (zipfile.BadZipFile, ValueError) as exc:
        raise HTTPException(400, "Das ist keine Homify-Sicherung (.zip).") from exc
    restored = DATA_DIR / "tmp" / "restore.db"
    restored.parent.mkdir(parents=True, exist_ok=True)
    restored.write_bytes(z.read("homify.db"))
    try:
        check = sqlite3.connect(str(restored))
        check.execute("SELECT COUNT(*) FROM users").fetchone()
        src = sqlite3.connect(str(restored))
        src.backup(db.conn())  # Inhalt in die laufende Datenbank übernehmen
        src.close()
        check.close()
    finally:
        restored.unlink(missing_ok=True)
    db.init()
    if "config.json" in names:
        values = json.loads(z.read("config.json"))
        for key in ("spotify_client_secret", "nas_password"):
            values.pop(key, None)  # Geheimnisse sind nicht in der Sicherung
        if keep_storage:
            for key in STORAGE_KEYS:
                values.pop(key, None)
        values.pop("port", None)
        values.pop("host", None)
        config.update(values)
    scanner.start()
    return {"ok": True}


@app.post("/api/library/scan")
def scan(full: bool = False, user: dict = Depends(auth.admin_user)):
    scanner.start(full=full)
    return scanner.status


# YouTube-Cookies für spotDL (hilft, wenn YouTube Downloads als „Bot“ blockiert)
class CookieText(BaseModel):
    text: str = Field(max_length=2_000_000)


@app.post("/api/settings/youtube-cookies")
def upload_youtube_cookies(body: CookieText, user: dict = Depends(auth.admin_user)):
    return cookies.save(body.text)


@app.delete("/api/settings/youtube-cookies")
def delete_youtube_cookies(user: dict = Depends(auth.admin_user)):
    return cookies.remove()


# Doppelte Dateien: ausgeblendet, der Admin kann sie löschen, um Platz auf dem NAS zu sparen
@app.get("/api/duplicates")
def list_duplicates(user: dict = Depends(auth.admin_user)):
    return {"count": dedup.count(), "items": dedup.list_all()}


class DuplicatePaths(BaseModel):
    paths: list[str] = Field(default_factory=list, max_length=5000)
    all: bool = False


@app.post("/api/duplicates/delete")
def delete_duplicates(body: DuplicatePaths, user: dict = Depends(auth.admin_user)):
    if body.all:
        rows = db.query("SELECT path, root, rel FROM duplicates")
    else:
        rows = [r for p in body.paths if (r := db.query_one("SELECT path, root, rel FROM duplicates WHERE path = ?", (p,)))]
    by_key = {st.key: st for st in storages.all_storages()}
    deleted, errors = 0, []
    for r in rows:
        st = by_key.get(r["root"])
        try:
            if st is None:
                raise OSError("Speicherort nicht eingebunden")
            st.remove(r["rel"])  # nur Dateien aus der Duplikat-Liste – nie die behaltene Version
            db.execute("DELETE FROM duplicates WHERE path = ?", (r["path"],))
            deleted += 1
        except Exception as exc:  # z. B. SMB-Fehler vom NAS
            errors.append(f"{r['path']}: {exc}")
    return {"deleted": deleted, "errors": errors[:20], "count": dedup.count()}


@app.post("/api/system/spotdl")
def install_spotdl(user: dict = Depends(auth.admin_user)):
    bridge.stop()
    started = tools.install_async(upgrade=True)
    return {"started": started, "status": tools.status}


def _exit_soon(restart: bool) -> None:
    def later():
        time.sleep(0.8)
        downloads.stop()
        bridge.stop()
        # Unter systemd/Docker übernimmt der Dienst-Manager den Neustart (Restart=on-failure)
        if os.environ.get("INVOCATION_ID") or os.environ.get("HOMIFY_SUPERVISED"):
            os._exit(3 if restart else 0)
        if restart:
            cmd = [sys.executable, "-m", "homify", "run", "--wait-port"]
            flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0  # DETACHED_PROCESS | NEW_PROCESS_GROUP
            subprocess.Popen(cmd, cwd=str(APP_DIR), creationflags=flags, start_new_session=os.name != "nt",
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os._exit(0)

    threading.Thread(target=later, daemon=True).start()


@app.post("/api/system/restart")
def restart(user: dict = Depends(auth.admin_user)):
    _exit_soon(restart=True)
    return {"ok": True}


@app.post("/api/system/shutdown")
def shutdown(user: dict = Depends(auth.admin_user)):
    _exit_soon(restart=False)
    return {"ok": True}


@app.delete("/api/system/cache")
def clear_cache(user: dict = Depends(auth.admin_user)):
    media.clear_cache()
    return {"ok": True}


class BrowseIn(BaseModel):
    path: str = ""


@app.post("/api/system/browse")
def browse(body: BrowseIn, user: dict = Depends(auth.admin_user)):
    """Ordnerauswahl für die Einstellungen (nur Admins)."""
    path = body.path.strip()
    if not path:
        if os.name == "nt":
            import string

            drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
            return {"path": "", "parent": None, "dirs": drives}
        path = "/"
    p = Path(path)
    try:
        dirs = sorted(
            (e.name for e in os.scandir(p) if e.is_dir() and not e.name.startswith((".", "$", "@"))),
            key=str.lower,
        )
    except OSError as exc:
        raise HTTPException(400, f"Ordner nicht lesbar: {exc}") from exc
    parent = str(p.parent) if p.parent != p else ("" if os.name == "nt" else None)
    return {"path": str(p), "parent": parent, "dirs": [str(p / d) for d in dirs][:500]}


class UserIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=4, max_length=256)
    is_admin: bool = False
    can_download: bool | None = None  # None = Einstellung „Neue Benutzer dürfen herunterladen“


class UserPatch(BaseModel):
    password: str | None = Field(default=None, min_length=4, max_length=256)
    is_admin: bool | None = None
    can_download: bool | None = None


@app.get("/api/users")
def users(user: dict = Depends(auth.admin_user)):
    return db.query("SELECT id, username, is_admin, can_download, created_at FROM users ORDER BY username")


@app.post("/api/users")
def create_user(body: UserIn, user: dict = Depends(auth.admin_user)):
    if db.query_one("SELECT id FROM users WHERE username = ?", (body.username.strip(),)):
        raise HTTPException(409, "Benutzername existiert bereits")
    uid = auth.create_user(body.username, body.password, body.is_admin, body.can_download)
    return {"id": uid}


@app.patch("/api/users/{user_id}")
def update_user(user_id: int, body: UserPatch, user: dict = Depends(auth.admin_user)):
    if body.password:
        auth.set_password(user_id, body.password)
    if body.is_admin is not None:
        if user_id == user["id"] and not body.is_admin:
            raise HTTPException(400, "Du kannst dir nicht selbst die Admin-Rechte nehmen")
        db.execute("UPDATE users SET is_admin = ? WHERE id = ?", (int(body.is_admin), user_id))
    if body.can_download is not None:
        db.execute("UPDATE users SET can_download = ? WHERE id = ?", (int(body.can_download), user_id))
    return {"ok": True}


@app.delete("/api/users/{user_id}")
def delete_user(user_id: int, user: dict = Depends(auth.admin_user)):
    if user_id == user["id"]:
        raise HTTPException(400, "Du kannst dich nicht selbst löschen")
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Weboberfläche
# --------------------------------------------------------------------------- #

@app.get("/api/{rest:path}")
def api_not_found(rest: str):
    raise HTTPException(404, "Unbekannter API-Endpunkt")


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
