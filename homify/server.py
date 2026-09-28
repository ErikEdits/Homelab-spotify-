"""FastAPI-Server: REST-API + Weboberfläche."""

from __future__ import annotations

import logging
import mimetypes
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import APP_NAME, __version__, auth, db, library, media
from .config import APP_DIR, STATIC_DIR, config
from .covers import get_cover_file
from .downloader import downloads
from .scanner import scanner
from .spotify import invalidate_library_index
from .spotify import search as spotify_search
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
    """Scannt die Bibliothek regelmäßig (neue Dateien auf dem NAS werden automatisch gefunden)."""

    def __init__(self):
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="scheduler").start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        scanner.start()
        while not self._stop.wait(60):
            interval = int(config.get("scan_interval_minutes") or 0)
            last = scanner.status.get("finished_at") or 0
            if interval > 0 and not scanner.status["running"] and time.time() - last > interval * 60:
                scanner.start()


scheduler = Scheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    scanner.listeners.append(invalidate_library_index)
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
        auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="lax",
        secure=request.url.scheme == "https", path="/",
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


@app.get("/api/setup")
def setup_status():
    return {"needs_setup": auth.user_count() == 0, "app": APP_NAME, "version": __version__}


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
    return user


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
def home(user: dict = Depends(auth.current_user)):
    data = library.home(user["id"])
    data["scan"] = scanner.status
    return data


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


@app.get("/api/tracks/{track_id}/stream")
def stream(track_id: str, transcode: int = 0, quality: str = "original", user: dict = Depends(auth.current_user)):
    t = _track_or_404(track_id)
    if not os.path.isfile(t["path"]):
        raise HTTPException(404, "Datei nicht erreichbar – ist das NAS online?")
    headers = {"Cache-Control": "private, max-age=86400"}
    if transcode or (quality == "low" and media.needs_transcode_for_quality(t["bitrate"], "low")):
        try:
            out = media.transcode(t, "low" if quality == "low" else "high")
        except media.TranscodeError as exc:
            raise HTTPException(500, f"Umwandlung fehlgeschlagen: {exc}") from exc
        return FileResponse(out, media_type="audio/mpeg", headers=headers)
    mime = (t["mime"] or "application/octet-stream").split(";")[0]
    return FileResponse(t["path"], media_type=mime, headers=headers)


@app.get("/api/tracks/{track_id}/file")
def download_file(track_id: str, user: dict = Depends(auth.current_user)):
    t = _track_or_404(track_id)
    if not os.path.isfile(t["path"]):
        raise HTTPException(404, "Datei nicht erreichbar – ist das NAS online?")
    return FileResponse(t["path"], filename=os.path.basename(t["path"]))


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


@app.get("/api/mix/{kind}")
def mix(kind: str, value: str = "", user: dict = Depends(auth.current_user)):
    return library.mix_tracks(kind, value, user["id"])


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


@app.post("/api/playlists")
def create_playlist(body: PlaylistIn, user: dict = Depends(auth.current_user)):
    now = time.time()
    cur = db.execute(
        "INSERT INTO playlists (user_id, name, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (user["id"], body.name.strip(), body.description.strip(), now, now),
    )
    pid = int(cur.lastrowid)
    if body.track_ids:
        _append_tracks(pid, body.track_ids[:5000])
    return library.playlist_summary(pid)


@app.get("/api/playlists/{playlist_id}")
def playlist(playlist_id: int, user: dict = Depends(auth.current_user)):
    data = library.playlist_detail(playlist_id, user["id"])
    if not data:
        raise HTTPException(404, "Playlist nicht gefunden")
    return data


@app.patch("/api/playlists/{playlist_id}")
def update_playlist(playlist_id: int, body: PlaylistPatch, user: dict = Depends(auth.current_user)):
    _own_playlist(playlist_id, user)
    if body.name is not None:
        db.execute("UPDATE playlists SET name = ?, updated_at = ? WHERE id = ?",
                   (body.name.strip(), time.time(), playlist_id))
    if body.description is not None:
        db.execute("UPDATE playlists SET description = ?, updated_at = ? WHERE id = ?",
                   (body.description.strip(), time.time(), playlist_id))
    return library.playlist_summary(playlist_id)


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


@app.get("/api/downloads")
def list_downloads(user: dict = Depends(auth.current_user)):
    jobs = downloads.list()
    # Fertige Songs gleich mitliefern, damit man sie direkt abspielen kann
    for job in jobs:
        if job["status"] in ("done", "partial") and job["spotify_ids"]:
            ids = job["spotify_ids"][:500]
            rows = db.query(
                "SELECT id FROM tracks WHERE spotify_id IN (%s)" % ",".join("?" * len(ids)), ids
            )
            job["track_ids"] = [r["id"] for r in rows]
        if not user["is_admin"] and job.get("user_id") != user["id"]:
            job.pop("log", None)
    return {"jobs": jobs, "active": downloads.active_count(), "scan": scanner.status}


@app.post("/api/downloads")
def add_download(body: DownloadIn, user: dict = Depends(auth.download_user)):
    return downloads.add(user["id"], body.query, body.kind, body.title, body.subtitle, body.image,
                         body.spotify_ids, body.total)


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
            "download_dir": config.download_dir,
            "music_dirs": [{"path": d, "online": os.path.isdir(d)} for d in config.music_dirs],
        },
    }


@app.put("/api/settings")
def put_settings(values: dict[str, Any], user: dict = Depends(auth.admin_user)):
    old_dirs = config.music_dirs
    config.update(values)
    media.find_ffmpeg(refresh=True)
    if config.music_dirs != old_dirs:
        scanner.start()
    return get_settings(user)


@app.post("/api/library/scan")
def scan(full: bool = False, user: dict = Depends(auth.admin_user)):
    scanner.start(full=full)
    return scanner.status


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
    can_download: bool = True


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
