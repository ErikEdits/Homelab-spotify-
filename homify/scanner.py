"""Durchsucht die Speicherorte (App-Ordner, NAS, eigene Ordner) und hält die Bibliothek aktuell."""

from __future__ import annotations

import json
import logging
import os
import posixpath
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import db, dedup
from . import storage as storages
from .config import config
from .covers import CoverStore
from .media import probe_duration
from .metadata import AUDIO_EXTENSIONS, TrackInfo, read_track, spotify_id_from_url
from .storage import Storage
from .textutil import dup_key, make_id, norm, path_id

log = logging.getLogger("homify.scanner")

UNKNOWN_ARTIST = "Unbekannter Künstler"
UNKNOWN_ALBUM = "Unbekanntes Album"
VARIOUS = "Verschiedene Interpreten"

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
            thread = threading.Thread(target=self._run, args=(full,), name="scanner", daemon=True)
            thread.start()
            self._thread = thread
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
        all_st = storages.all_storages()
        keys = [st.key for st in all_st]
        st_status = self.status
        st_status.update(
            running=True, phase="scanning", files=0, checked=0, added=0, updated=0, removed=0,
            errors=0, offline_roots=[], started_at=time.time(), finished_at=None, message="",
        )
        covers = CoverStore()
        opts = scan_options()
        dedup.backfill_keys()
        dups = {row["path"]: row for row in db.query("SELECT * FROM duplicates")}
        dup_seen: set[str] = set()
        existing = {
            row["path"]: row
            for row in db.query("SELECT id, path, root, size, mtime, sig, duration FROM tracks")
        }
        first_import = not existing
        id_to_path = {row["id"]: path for path, row in existing.items()}

        seen: set[str] = set()
        todo: list[tuple[Storage, str, str, int, float]] = []
        healthy: list[str] = []
        messages: list[str] = []

        def on_error(msg: str) -> None:
            st_status["errors"] += 1
            log.warning("Nicht lesbar: %s", msg)

        for st in all_st:
            ok, msg = st.available()
            if not ok:
                st_status["offline_roots"].append(st.label)
                messages.append(f"{st.label}: {msg}")
                log.warning("Speicherort nicht erreichbar: %s (%s)", st.label, msg)
                continue
            errors_before = st_status["errors"]
            count = 0
            for rel, size, mtime in st.walk(on_error, skip=opts["ignore_folders"]):
                name = posixpath.basename(rel)
                if name.startswith("._") or os.path.splitext(name)[1].lower() not in AUDIO_EXTENSIONS:
                    continue
                count += 1
                st_status["files"] += 1
                path = st.display_path(rel)
                known_dup = dups.get(path)
                if known_dup and not full and known_dup["size"] == size and abs(known_dup["mtime"] - mtime) <= 1 \
                        and known_dup["root"] == st.key:
                    dup_seen.add(path)  # bekannte doppelte Datei, unverändert -> bleibt ausgeblendet
                    continue
                old = existing.get(path)
                if old and opts["min_seconds"] and 0 < (old["duration"] or 0) < opts["min_seconds"] \
                        and old["size"] == size and abs(old["mtime"] - mtime) <= 1:
                    continue  # zu kurz (Einstellung) -> nicht aufnehmen
                seen.add(path)
                if full or not old or old["size"] != size or abs(old["mtime"] - mtime) > 1 or old["root"] != st.key:
                    todo.append((st, rel, path, size, mtime))
            had_tracks = any(r["root"] == st.key for r in existing.values())
            if st_status["errors"] > errors_before or (count == 0 and had_tracks):
                # NAS weg oder Freigabe leer gemountet -> nichts löschen!
                st_status["offline_roots"].append(st.label)
            else:
                healthy.append(st.key)

        st_status["phase"] = "reading"
        new_ids: dict[str, str] = {}
        workers = opts["workers"] if all(st.kind == "local" for st in all_st) else min(opts["workers"], 8)
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for i in range(0, len(todo), BATCH):
                chunk = todo[i:i + BATCH]
                infos = list(pool.map(lambda item: _safe_read(item[0], item[1], opts), chunk))
                rows = []
                for (st, rel, path, size, mtime), info in zip(chunk, infos):
                    if opts["min_seconds"] and 0 < info.duration < opts["min_seconds"]:
                        seen.discard(path)  # zu kurz -> nicht in die Bibliothek
                        continue
                    track_id = _assign_id(st, rel, path, id_to_path, seen)
                    id_to_path[track_id] = path
                    if opts["prefer_folder_cover"]:
                        cover_id = covers.folder_cover(st, posixpath.dirname(rel)) or covers.store(info.cover)
                    else:
                        cover_id = covers.store(info.cover) or covers.folder_cover(st, posixpath.dirname(rel))
                    info.cover = None
                    old = existing.get(path)
                    added_at = time.time() if not first_import else mtime
                    rows.append(_build_row(track_id, st, rel, path, size, mtime, info, cover_id, added_at,
                                           folder_as_album=opts["folder_as_album"]))
                    if old:
                        st_status["updated"] += 1
                    else:
                        st_status["added"] += 1
                        new_ids[rows[-1]["sig"]] = track_id
                    st_status["checked"] += 1
                self._write(rows)

        # Entfernte Dateien (nur in erreichbaren Speicherorten!)
        st_status["phase"] = "cleanup"
        removed = [
            row for path, row in existing.items()
            if path not in seen and (row["root"] in healthy or row["root"] not in keys)
        ]
        gone_dups = [p for p, d in dups.items()
                     if p not in dup_seen and p not in seen and (d["root"] in healthy or d["root"] not in keys)]
        if gone_dups:
            with db.transaction() as c:
                c.executemany("DELETE FROM duplicates WHERE path = ?", [(p,) for p in gone_dups])
        limit = max(3, len(existing) * opts["max_remove_percent"] // 100)
        if removed and len(removed) > limit and not full and opts["max_remove_percent"] < 100:
            # Sicherheitsgrenze: lieber nichts löschen als die halbe Bibliothek (NAS-Aussetzer o. ä.)
            messages.append(
                f"Sicherheitsgrenze: {len(removed)} Songs wären entfernt worden – es wurde nichts gelöscht. "
                "Prüfe den Speicherort oder nutze „Alles neu einlesen“."
            )
            log.warning("Sicherheitsgrenze: %s Songs würden entfernt – übersprungen", len(removed))
            removed = []
        # Wird ein Song gelöscht, von dem es noch eine andere Datei gibt, rückt diese nach
        replacement = {} if not removed else self._promote_duplicates(
            [r for r in removed if not new_ids.get(r["sig"])], dups, dup_seen, all_st, opts, id_to_path, seen)
        if removed:
            with db.transaction() as c:
                for row in removed:
                    new_id = new_ids.get(row["sig"]) or replacement.get(row["id"])
                    if new_id and new_id != row["id"]:
                        _remap(c, row["id"], new_id)  # Datei verschoben -> Playlists/Likes behalten
                    if id_to_path.get(row["id"]) not in (None, row["path"]):
                        continue  # ID gehört inzwischen einer anderen Datei (z. B. nach Umzug aufs NAS)
                    c.execute("DELETE FROM tracks WHERE id = ? AND path = ?", (row["id"], row["path"]))
                    c.execute(
                        "DELETE FROM track_artists WHERE track_id = ? AND NOT EXISTS "
                        "(SELECT 1 FROM tracks WHERE id = ?)", (row["id"], row["id"]),
                    )
            st_status["removed"] = len(removed)

        hidden = dedup.consolidate()  # jeder Song nur einmal – doppelte Dateien ausblenden
        st_status["duplicates"] = dedup.count()
        if hidden:
            log.info("%s doppelte Dateien ausgeblendet", hidden)
        if todo or removed or full or hidden:
            st_status["phase"] = "indexing"
            rebuild_aggregates()

        st_status.update(running=False, phase="done", finished_at=time.time())
        if messages:
            st_status["message"] = "; ".join(messages)
        log.info(
            "Scan fertig: %s Dateien, +%s ~%s -%s", st_status["files"], st_status["added"],
            st_status["updated"], st_status["removed"],
        )
        for listener in list(self.listeners):
            try:
                listener(dict(st_status))
            except Exception:  # pragma: no cover
                log.exception("Scan-Listener fehlgeschlagen")
        return dict(st_status)

    def _promote_duplicates(self, removed_rows, dups, dup_seen, all_st, opts, id_to_path, seen) -> dict[str, str]:
        """Song gelöscht, aber eine weitere Datei davon ist noch da -> diese kommt in die Bibliothek.
        Gibt {alte ID: neue ID} zurück, damit Likes und Playlists erhalten bleiben."""
        by_track: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for d in dups.values():
            if d["path"] in dup_seen:
                by_track[d["track_id"]].append(d)
        if not by_track:
            return {}
        st_by_key = {st.key: st for st in all_st}
        covers = CoverStore()
        replacement: dict[str, str] = {}
        rows = []
        for r in removed_rows:
            candidates = by_track.get(r["id"])
            if not candidates:
                continue
            best = max(candidates, key=lambda d: dedup.quality(d["codec"], d["bitrate"]))
            st = st_by_key.get(best["root"])
            if st is None:
                continue
            info = _safe_read(st, best["rel"], opts)
            track_id = _assign_id(st, best["rel"], best["path"], id_to_path, seen)
            id_to_path[track_id] = best["path"]
            cover_id = covers.store(info.cover) or covers.folder_cover(st, posixpath.dirname(best["rel"]))
            info.cover = None
            rows.append(_build_row(track_id, st, best["rel"], best["path"], best["size"], best["mtime"], info,
                                   cover_id, time.time(), folder_as_album=opts["folder_as_album"]))
            dup_seen.discard(best["path"])
            seen.add(best["path"])
            replacement[r["id"]] = track_id
        self._write(rows)
        return replacement

    def _write(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        cols = list(rows[0].keys())
        cols.remove("_artists")
        placeholders = ",".join("?" * len(cols))
        # Gemessene Lautheit nicht mit „leer“ überschreiben – außer die Datei selbst hat sich geändert,
        # dann wird alles neu gemessen
        changed = "(excluded.size != tracks.size OR ABS(excluded.mtime - tracks.mtime) > 1)"
        updates = ",".join(
            f"{c}=CASE WHEN {changed} THEN excluded.{c} ELSE COALESCE(excluded.{c}, tracks.{c}) END"
            if c in ("gain", "album_gain") else f"{c}=excluded.{c}"
            for c in cols if c not in ("id", "added_at")
        )
        updates += f", analyzed=CASE WHEN {changed} THEN 0 ELSE tracks.analyzed END"
        sql = (
            f"INSERT INTO tracks ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}"
        )
        with db.transaction() as c:
            for row in rows:
                # Falls der Pfad schon unter anderer ID existiert (Kollision), alten Eintrag ersetzen
                c.execute("DELETE FROM tracks WHERE path = ? AND id != ?", (row["path"], row["id"]))
                c.execute("DELETE FROM duplicates WHERE path = ?", (row["path"],))
                c.execute(sql, [row[col] for col in cols])
                c.execute("DELETE FROM track_artists WHERE track_id = ?", (row["id"],))
                for pos, name in enumerate(row["_artists"]):
                    c.execute(
                        "INSERT OR IGNORE INTO track_artists (track_id, artist_id, name, position) "
                        "VALUES (?, ?, ?, ?)",
                        (row["id"], make_id(name), name, pos),
                    )


def scan_options() -> dict:
    """Einlese-Regeln aus den Einstellungen."""
    separators = tuple(x for x in (config.get("artist_separators") or ";").split() if x) or (";",)
    return {
        "ignore_folders": {x.strip().lower() for x in (config.get("ignore_folders") or "").split(",") if x.strip()},
        "min_seconds": int(config.get("min_track_seconds") or 0),
        "workers": int(config.get("scan_workers") or 6),
        "folder_as_album": bool(config.get("folder_as_album")),
        "prefer_folder_cover": bool(config.get("prefer_folder_cover")),
        "max_remove_percent": int(config.get("max_remove_percent") or 30),
        "read": {
            "parse_filename": bool(config.get("filename_parsing")),
            "separators": separators,
            "split_feat": bool(config.get("split_feat")),
        },
    }


def _safe_read(st: Storage, rel: str, opts: dict | None = None) -> TrackInfo:
    local = st.local_path(rel)
    read_opts = (opts or {}).get("read")
    try:
        if local:
            info = read_track(local, options=read_opts)
        else:
            with st.open(rel) as fh:
                info = read_track(fh, name=rel, options=read_opts)
    except Exception:
        log.exception("Konnte %s nicht lesen", rel)
        info = TrackInfo(title=os.path.splitext(posixpath.basename(rel))[0], readable=False)
    if not info.duration and local:
        info.duration = probe_duration(local)  # z. B. .webm/.mka – mutagen kennt die Dauer nicht
    return info


def _assign_id(st: Storage, rel: str, path: str, id_to_path: dict[str, str], seen: set[str]) -> str:
    """ID aus dem relativen Pfad – so bleibt sie beim Umzug (PC -> NAS) gleich."""
    track_id = path_id(rel)
    other = id_to_path.get(track_id)
    if other and other != path and other in seen:
        track_id = path_id(st.key + "/" + rel)
    return track_id


def _build_row(track_id, st: Storage, rel, path, size, mtime, info: TrackInfo, cover_id, added_at,
               folder_as_album: bool = True) -> dict[str, Any]:
    artists = info.artists or [UNKNOWN_ARTIST]
    first_artist = artists[0]
    album_tag = info.album.strip()
    album_artist_tag = info.album_artist.strip()
    parent = posixpath.dirname(rel)
    if album_tag and album_artist_tag:
        album, album_key = album_tag, make_id("aa", album_artist_tag, album_tag)
    elif album_tag:
        album, album_key = album_tag, make_id("dir", parent, album_tag)
    else:
        album = posixpath.basename(parent) if parent and folder_as_album else UNKNOWN_ALBUM
        album_key = make_id("art", first_artist, album)
    album_artist = album_artist_tag or first_artist
    artist = ", ".join(artists)
    title = info.title or os.path.splitext(posixpath.basename(rel))[0]
    return {
        "id": track_id,
        "path": path,
        "root": st.key,
        "rel": rel,
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
        "gain": info.gain,
        "album_gain": info.album_gain,
        # Wiedererkennung verschobener Dateien – ohne Ordner/Album, die sich beim Verschieben ändern können
        "sig": make_id(title, artist, str(round(info.duration))),
        "dkey": dup_key(title, first_artist) if info.artists else "",
        "search": norm(f"{title} {artist} {album} {album_artist}"),
        "added_at": added_at,
        "_artists": artists,
    }


def _remap(c, old_id: str, new_id: str) -> None:
    dedup.remap(c, old_id, new_id)


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
