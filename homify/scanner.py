"""Durchsucht die Musikordner (lokal oder NAS) und hält die Bibliothek aktuell."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterator

from . import db
from .config import config
from .covers import CoverStore
from .media import probe_duration
from .metadata import AUDIO_EXTENSIONS, TrackInfo, read_track, spotify_id_from_url
from .textutil import make_id, norm, path_id

log = logging.getLogger("homify.scanner")

UNKNOWN_ARTIST = "Unbekannter Künstler"
UNKNOWN_ALBUM = "Unbekanntes Album"
VARIOUS = "Verschiedene Interpreten"

# Typische System-/Papierkorb-Ordner auf NAS-Systemen (Synology, QNAP, Windows)
SKIP_DIRS = {
    "@eadir", "#recycle", "$recycle.bin", "system volume information", "#snapshot",
    ".snapshot", "lost+found", "@recycle", ".@__thumb", "@__thumb", ".trash", ".trashes",
}

BATCH = 200


class Scanner:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._pending = False
        self.listeners: list = []
        self.status: dict[str, Any] = {
            "running": False, "phase": "idle", "files": 0, "checked": 0, "added": 0,
            "updated": 0, "removed": 0, "errors": 0, "offline_roots": [],
            "started_at": None, "finished_at": None, "message": "",
        }

    # ------------------------------------------------------------------ API
    def start(self, full: bool = False) -> bool:
        """Scan im Hintergrund starten. Läuft schon einer, wird danach erneut gescannt."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._pending = True
                return False
            self._thread = threading.Thread(target=self._run, args=(full,), name="scanner", daemon=True)
            self._thread.start()
            return True

    def wait(self, timeout: float | None = None) -> None:
        t = self._thread
        if t:
            t.join(timeout)

    def _run(self, full: bool) -> None:
        while True:
            try:
                self.scan(full=full)
            except Exception as exc:  # pragma: no cover - Sicherheitsnetz
                log.exception("Scan fehlgeschlagen")
                self.status.update(running=False, phase="error", message=str(exc))
            finally:
                db.close()
            with self._lock:
                if not self._pending:
                    return
                self._pending = False
                full = False

    # ------------------------------------------------------------------ Scan
    def scan(self, full: bool = False) -> dict[str, Any]:
        roots = [os.path.abspath(r) for r in config.music_dirs]
        st = self.status
        st.update(
            running=True, phase="scanning", files=0, checked=0, added=0, updated=0, removed=0,
            errors=0, offline_roots=[], started_at=time.time(), finished_at=None, message="",
        )
        covers = CoverStore()
        existing = {
            row["path"]: row
            for row in db.query("SELECT id, path, root, size, mtime, sig FROM tracks")
        }
        first_import = not existing
        id_to_path = {row["id"]: path for path, row in existing.items()}

        seen: set[str] = set()
        todo: list[tuple[str, str, int, float]] = []
        healthy_roots: list[str] = []

        for root in roots:
            if not os.path.isdir(root):
                st["offline_roots"].append(root)
                log.warning("Musikordner nicht erreichbar: %s", root)
                continue
            errors_before = st["errors"]
            count = 0
            for path, size, mtime in self._walk(root):
                count += 1
                st["files"] += 1
                seen.add(path)
                old = existing.get(path)
                if full or not old or old["size"] != size or abs(old["mtime"] - mtime) > 1 or old["root"] != root:
                    todo.append((root, path, size, mtime))
            had_tracks = any(r["root"] == root for r in existing.values())
            if st["errors"] > errors_before or (count == 0 and had_tracks):
                # NAS weg oder Freigabe leer gemountet -> nichts löschen!
                st["offline_roots"].append(root)
            else:
                healthy_roots.append(root)

        st["phase"] = "reading"
        new_ids: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=6) as pool:
            for i in range(0, len(todo), BATCH):
                chunk = todo[i:i + BATCH]
                infos = list(pool.map(lambda item: _safe_read(item[1]), chunk))
                rows = []
                for (root, path, size, mtime), info in zip(chunk, infos):
                    track_id = _assign_id(root, path, id_to_path, seen)
                    id_to_path[track_id] = path
                    cover_id = covers.store(info.cover) or covers.folder_cover(os.path.dirname(path))
                    info.cover = None
                    old = existing.get(path)
                    added_at = time.time() if not first_import else mtime
                    rows.append(_build_row(track_id, root, path, size, mtime, info, cover_id, added_at))
                    if old:
                        st["updated"] += 1
                    else:
                        st["added"] += 1
                        new_ids[rows[-1]["sig"]] = track_id
                    st["checked"] += 1
                self._write(rows)

        # Entfernte Dateien (nur in erreichbaren Ordnern!)
        st["phase"] = "cleanup"
        removed = [
            row for path, row in existing.items()
            if path not in seen and (row["root"] in healthy_roots or row["root"] not in roots)
        ]
        if removed:
            with db.transaction() as c:
                for row in removed:
                    new_id = new_ids.get(row["sig"])
                    if new_id and new_id != row["id"]:
                        _remap(c, row["id"], new_id)  # Datei verschoben -> Playlists/Likes behalten
                    if id_to_path.get(row["id"]) not in (None, row["path"]):
                        continue  # ID gehört inzwischen einer anderen Datei (Ordner umbenannt)
                    c.execute("DELETE FROM tracks WHERE id = ? AND path = ?", (row["id"], row["path"]))
                    c.execute(
                        "DELETE FROM track_artists WHERE track_id = ? AND NOT EXISTS "
                        "(SELECT 1 FROM tracks WHERE id = ?)", (row["id"], row["id"]),
                    )
            st["removed"] = len(removed)

        if todo or removed or full:
            st["phase"] = "indexing"
            rebuild_aggregates()

        st.update(running=False, phase="done", finished_at=time.time())
        if st["offline_roots"]:
            st["message"] = "Nicht erreichbar: " + ", ".join(st["offline_roots"])
        log.info(
            "Scan fertig: %s Dateien, +%s ~%s -%s", st["files"], st["added"], st["updated"], st["removed"]
        )
        for listener in list(self.listeners):
            try:
                listener(dict(st))
            except Exception:  # pragma: no cover
                log.exception("Scan-Listener fehlgeschlagen")
        return dict(st)

    def _walk(self, root: str) -> Iterator[tuple[str, int, float]]:
        stack = [root]
        linked: set[str] = set()  # Schutz vor Endlosschleifen durch Symlinks
        while stack:
            directory = stack.pop()
            try:
                it = os.scandir(directory)
            except OSError as exc:
                log.warning("Ordner nicht lesbar: %s (%s)", directory, exc)
                self.status["errors"] += 1
                continue
            with it:
                for entry in it:
                    name = entry.name
                    if name.startswith("."):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=True):
                            if name.lower() in SKIP_DIRS:
                                continue
                            if entry.is_symlink():
                                real = os.path.realpath(entry.path)
                                if real in linked or os.path.realpath(directory).startswith(real):
                                    continue
                                linked.add(real)
                            stack.append(entry.path)
                        elif os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS:
                            s = entry.stat()
                            yield entry.path, s.st_size, s.st_mtime
                    except OSError:
                        self.status["errors"] += 1

    def _write(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        cols = list(rows[0].keys())
        cols.remove("_artists")
        placeholders = ",".join("?" * len(cols))
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("id", "added_at"))
        sql = (
            f"INSERT INTO tracks ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}"
        )
        with db.transaction() as c:
            for row in rows:
                # Falls der Pfad schon unter anderer ID existiert (Kollision), alten Eintrag ersetzen
                c.execute("DELETE FROM tracks WHERE path = ? AND id != ?", (row["path"], row["id"]))
                c.execute(sql, [row[col] for col in cols])
                c.execute("DELETE FROM track_artists WHERE track_id = ?", (row["id"],))
                for pos, name in enumerate(row["_artists"]):
                    c.execute(
                        "INSERT OR IGNORE INTO track_artists (track_id, artist_id, name, position) "
                        "VALUES (?, ?, ?, ?)",
                        (row["id"], make_id(name), name, pos),
                    )


def _safe_read(path: str) -> TrackInfo:
    try:
        info = read_track(path)
    except Exception:  # pragma: no cover - read_track fängt schon alles ab
        log.exception("Konnte %s nicht lesen", path)
        info = TrackInfo(title=os.path.splitext(os.path.basename(path))[0], readable=False)
    if not info.duration:
        info.duration = probe_duration(path)  # z. B. .webm/.mka – mutagen kennt die Dauer nicht
    return info


def _assign_id(root: str, path: str, id_to_path: dict[str, str], seen: set[str]) -> str:
    rel = os.path.relpath(path, root)
    track_id = path_id(rel)
    other = id_to_path.get(track_id)
    if other and other != path and (other in seen or os.path.exists(other)):
        track_id = path_id(path)
    return track_id


def _build_row(track_id, root, path, size, mtime, info: TrackInfo, cover_id, added_at) -> dict[str, Any]:
    artists = info.artists or [UNKNOWN_ARTIST]
    first_artist = artists[0]
    album_tag = info.album.strip()
    album_artist_tag = info.album_artist.strip()
    parent = os.path.dirname(path)
    if album_tag and album_artist_tag:
        album, album_key = album_tag, make_id("aa", album_artist_tag, album_tag)
    elif album_tag:
        album, album_key = album_tag, make_id("dir", parent, album_tag)
    else:
        album = os.path.basename(parent) if os.path.normcase(parent) != os.path.normcase(root) else UNKNOWN_ALBUM
        album_key = make_id("art", first_artist, album)
    album_artist = album_artist_tag or first_artist
    artist = ", ".join(artists)
    title = info.title or os.path.splitext(os.path.basename(path))[0]
    return {
        "id": track_id,
        "path": path,
        "root": root,
        "size": size,
        "mtime": mtime,
        "title": title,
        "artist": artist,
        "artists": json.dumps(artists, ensure_ascii=False),
        "album": album,
        "album_artist": album_artist,
        "album_id": album_key,
        "track_no": info.track_no,
        "disc_no": info.disc_no,
        "year": info.year,
        "genre": info.genre,
        "duration": round(info.duration, 3),
        "bitrate": info.bitrate,
        "sample_rate": info.sample_rate,
        "codec": info.codec,
        "mime": info.mime,
        "cover_id": cover_id,
        "isrc": info.isrc,
        "spotify_id": spotify_id_from_url(info.source_url),
        # Wiedererkennung verschobener Dateien – ohne Ordner/Album, die sich beim Verschieben ändern können
        "sig": make_id(title, artist, str(round(info.duration))),
        "search": norm(f"{title} {artist} {album} {album_artist}"),
        "added_at": added_at,
        "_artists": artists,
    }


def _remap(c, old_id: str, new_id: str) -> None:
    c.execute("UPDATE OR IGNORE likes SET track_id = ? WHERE track_id = ?", (new_id, old_id))
    c.execute("UPDATE playlist_tracks SET track_id = ? WHERE track_id = ?", (new_id, old_id))
    c.execute("UPDATE plays SET track_id = ? WHERE track_id = ?", (new_id, old_id))


def rebuild_aggregates() -> None:
    """Alben- und Künstlertabellen aus den Tracks neu berechnen."""
    tracks = db.query(
        "SELECT id, album_id, album, album_artist, year, genre, duration, cover_id, added_at "
        "FROM tracks ORDER BY album_id, disc_no, track_no, title"
    )
    albums: dict[str, dict[str, Any]] = {}
    album_artists: dict[str, Counter] = defaultdict(Counter)
    genres: dict[str, Counter] = defaultdict(Counter)
    for t in tracks:
        a = albums.get(t["album_id"])
        if a is None:
            a = albums[t["album_id"]] = {
                "id": t["album_id"], "name": t["album"], "year": 0, "track_count": 0,
                "duration": 0.0, "cover_id": None, "added_at": 0.0,
            }
        a["track_count"] += 1
        a["duration"] += t["duration"] or 0
        a["year"] = max(a["year"], t["year"] or 0)
        a["added_at"] = max(a["added_at"], t["added_at"] or 0)
        if not a["cover_id"] and t["cover_id"]:
            a["cover_id"] = t["cover_id"]
        album_artists[t["album_id"]][t["album_artist"]] += 1
        if t["genre"]:
            genres[t["album_id"]][t["genre"]] += 1

    for album_id, a in albums.items():
        counter = album_artists[album_id]
        top, top_count = counter.most_common(1)[0]
        a["artist"] = top if len(counter) == 1 or top_count / a["track_count"] >= 0.6 else VARIOUS
        a["artist_id"] = make_id(a["artist"])
        a["genre"] = genres[album_id].most_common(1)[0][0] if genres[album_id] else ""
        a["search"] = norm(f"{a['name']} {a['artist']}")

    # Künstler: alle beteiligten Interpreten + Album-Interpreten
    artist_rows = db.query("SELECT artist_id, name, track_id FROM track_artists")
    names: dict[str, Counter] = defaultdict(Counter)
    track_counts: Counter = Counter()
    track_covers: dict[str, str] = {}
    cover_by_track = {t["id"]: t["cover_id"] for t in tracks}
    for r in artist_rows:
        names[r["artist_id"]][r["name"]] += 1
        track_counts[r["artist_id"]] += 1
        if r["artist_id"] not in track_covers and cover_by_track.get(r["track_id"]):
            track_covers[r["artist_id"]] = cover_by_track[r["track_id"]]
    album_counts: Counter = Counter()
    best_album: dict[str, dict[str, Any]] = {}
    for a in albums.values():
        if a["artist"] == VARIOUS:
            continue
        names[a["artist_id"]][a["artist"]] += 0
        album_counts[a["artist_id"]] += 1
        cur = best_album.get(a["artist_id"])
        if a["cover_id"] and (cur is None or a["track_count"] > cur["track_count"]):
            best_album[a["artist_id"]] = a

    with db.transaction() as c:
        c.execute("DELETE FROM albums")
        c.executemany(
            "INSERT INTO albums (id, name, artist, artist_id, year, genre, track_count, duration, "
            "cover_id, added_at, search) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (a["id"], a["name"], a["artist"], a["artist_id"], a["year"], a["genre"],
                 a["track_count"], a["duration"], a["cover_id"], a["added_at"], a["search"])
                for a in albums.values()
            ],
        )
        c.execute("DELETE FROM artists")
        c.executemany(
            "INSERT INTO artists (id, name, track_count, album_count, cover_id, search) VALUES (?,?,?,?,?,?)",
            [
                (
                    aid,
                    counter.most_common(1)[0][0],
                    track_counts[aid],
                    album_counts[aid],
                    (best_album[aid]["cover_id"] if aid in best_album else track_covers.get(aid)),
                    norm(counter.most_common(1)[0][0]),
                )
                for aid, counter in names.items()
                if counter
            ],
        )


scanner = Scanner()
