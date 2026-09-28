"""Download-Warteschlange: holt Songs/Alben/Playlists über spotDL auf das NAS."""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import signal
import subprocess
import threading
import time
from typing import Any

from . import db
from .config import DATA_DIR, IS_WINDOWS, config
from .media import find_ffmpeg
from .scanner import scanner
from .tools import child_env, venv_python

log = logging.getLogger("homify.downloader")

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
RE_FOUND = re.compile(r"Found (\d+) songs? in")
RE_DOWNLOADED = re.compile(r'Downloaded "(.+?)"')
RE_SKIPPED = re.compile(r"Skipping (.+?) \((?:file already exists|skip file found)\)")
RE_ERROR = re.compile(r"^\s*(\w+(?:Error|Exception)):\s*(.+)")

FRIENDLY_ERRORS = [
    (re.compile(r"No results found|LookupError", re.I),
     "Kein passender Song auf YouTube Music gefunden."),
    (re.compile(r"Sign in to confirm|not a bot|cookies", re.I),
     "YouTube verlangt eine Bestätigung. Tipp: Cookie-Datei in den Einstellungen hinterlegen."),
    (re.compile(r"rate.?limit|request limit|429", re.I),
     "Spotify-Limit erreicht – später nochmal versuchen oder eigene Spotify-API-Daten eintragen."),
    (re.compile(r"ProxyError|ConnectionError|Max retries|NameResolution|getaddrinfo|timed out", re.I),
     "Keine Internetverbindung zu Spotify/YouTube."),
    (re.compile(r"ffmpeg", re.I), "Problem mit ffmpeg (Umwandlung)."),
    (re.compile(r"Permission|Zugriff verweigert|Access is denied", re.I),
     "Keine Schreibrechte im Download-Ordner."),
]


def friendly_error(message: str) -> str:
    for pattern, text in FRIENDLY_ERRORS:
        if pattern.search(message):
            return f"{text} ({message[:200]})"
    return message
RE_PROCESSING = re.compile(r"Processing query: (.+)")

ALLOWED_QUERY = re.compile(
    r"^(https?://(open\.spotify\.com|spotify\.link|(www\.|music\.|m\.)?youtube\.com|youtu\.be|"
    r"soundcloud\.com|[\w-]+\.bandcamp\.com)/\S+|[^-\s].{0,300})$",
    re.IGNORECASE,
)
FORBIDDEN_QUERIES = {"saved", "all-user-playlists", "all-user-followed-artists", "all-user-saved-albums",
                     "all-saved-playlists"}


def validate_query(query: str) -> str:
    query = (query or "").strip()
    if not query or not ALLOWED_QUERY.match(query):
        raise ValueError("Bitte einen Spotify-/YouTube-Link oder „Künstler - Titel“ eingeben.")
    if query.lower() in FORBIDDEN_QUERIES or query.lower().endswith(".spotdl"):
        raise ValueError("Diese Abfrage wird nicht unterstützt.")
    return query


def build_command(query: str) -> list[str]:
    target = config.download_dir
    template = (config.get("output_template") or DEFAULT_TEMPLATE).strip().lstrip("/\\")
    cmd = [
        str(venv_python()), "-m", "spotdl", "download", query,
        "--output", os.path.join(target, template),
        "--format", config.get("download_format") or "mp3",
        "--bitrate", config.get("download_bitrate") or "auto",
        "--threads", str(max(1, int(config.get("download_threads") or 1))),
        "--overwrite", "skip",
        "--simple-tui", "--print-errors", "--log-level", "INFO",
    ]
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        cmd += ["--ffmpeg", ffmpeg]
    if config.get("spotify_client_id") and config.get("spotify_client_secret"):
        cmd += ["--client-id", config.get("spotify_client_id"), "--client-secret", config.get("spotify_client_secret")]
    if config.get("spotify_use_official_api"):
        cmd += ["--use-official-api"]
    cookie = (config.get("spotdl_cookie_file") or "").strip()
    if cookie:
        cmd += ["--cookie-file", cookie]
    extra = (config.get("spotdl_extra_args") or "").strip()
    if extra:
        cmd += shlex.split(extra, posix=not IS_WINDOWS)
    return cmd


DEFAULT_TEMPLATE = "{album-artist}/{album}/{artists} - {title}.{output-ext}"


def _row_to_job(row: dict[str, Any]) -> dict[str, Any]:
    job = dict(row)
    job["spotify_ids"] = json.loads(job.get("spotify_ids") or "[]")
    job["log"] = (job.get("log") or "").splitlines()[-200:]
    return job


class DownloadManager:
    def __init__(self):
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self._current: int | None = None
        self._cancel: set[int] = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ API
    def start(self) -> None:
        db.execute("UPDATE downloads SET status = 'queued' WHERE status = 'running'")
        self._thread = threading.Thread(target=self._loop, name="downloader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._kill()

    def add(self, user_id: int, query: str, kind: str = "track", title: str = "", subtitle: str = "",
            image: str = "", spotify_ids: list[str] | None = None, total: int = 0) -> dict[str, Any]:
        query = validate_query(query)
        existing = db.query_one(
            "SELECT id FROM downloads WHERE query = ? AND status IN ('queued', 'running')", (query,)
        )
        if existing:
            return self.get(existing["id"])
        cur = db.execute(
            "INSERT INTO downloads (user_id, query, kind, title, subtitle, image, status, total, spotify_ids, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
            (user_id, query, kind, title or query, subtitle, image, int(total or (1 if kind == "track" else 0)),
             json.dumps(spotify_ids or []), time.time()),
        )
        self._wake.set()
        return self.get(int(cur.lastrowid))

    def get(self, job_id: int) -> dict[str, Any] | None:
        row = db.query_one("SELECT * FROM downloads WHERE id = ?", (job_id,))
        return _row_to_job(row) if row else None

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = db.query(
            "SELECT * FROM downloads ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END, "
            "COALESCE(finished_at, created_at) DESC LIMIT ?", (limit,)
        )
        return [_row_to_job(r) for r in rows]

    def cancel(self, job_id: int) -> None:
        with self._lock:
            if self._current == job_id:
                self._cancel.add(job_id)
                self._kill()
        db.execute(
            "UPDATE downloads SET status = 'cancelled', finished_at = ? WHERE id = ? AND status IN ('queued','running')",
            (time.time(), job_id),
        )

    def retry(self, job_id: int) -> None:
        db.execute(
            "UPDATE downloads SET status = 'queued', done = 0, failed = 0, message = '', log = '', "
            "started_at = NULL, finished_at = NULL WHERE id = ? AND status NOT IN ('queued', 'running')",
            (job_id,),
        )
        self._wake.set()

    def remove(self, job_id: int) -> None:
        self.cancel(job_id)
        db.execute("DELETE FROM downloads WHERE id = ? AND status NOT IN ('queued', 'running')", (job_id,))

    def clear_finished(self) -> None:
        db.execute("DELETE FROM downloads WHERE status IN ('done', 'error', 'cancelled', 'partial')")

    def active_count(self) -> int:
        row = db.query_one("SELECT COUNT(*) AS n FROM downloads WHERE status IN ('queued','running')")
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------------ Worker
    def _loop(self) -> None:
        while not self._stop.is_set():
            job = db.query_one("SELECT * FROM downloads WHERE status = 'queued' ORDER BY created_at LIMIT 1")
            if not job:
                self._wake.wait(timeout=30)
                self._wake.clear()
                continue
            try:
                self._run(job)
            except Exception as exc:  # pragma: no cover - Sicherheitsnetz
                log.exception("Download %s fehlgeschlagen", job["id"])
                db.execute(
                    "UPDATE downloads SET status = 'error', message = ?, finished_at = ? WHERE id = ?",
                    (str(exc)[:500], time.time(), job["id"]),
                )

    def _run(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        if not venv_python().exists():
            db.execute(
                "UPDATE downloads SET status = 'error', message = ?, finished_at = ? WHERE id = ?",
                ("spotDL ist nicht installiert – Einstellungen → „spotDL installieren“.", time.time(), job_id),
            )
            return
        target = config.download_dir
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as exc:
            db.execute(
                "UPDATE downloads SET status = 'error', message = ?, finished_at = ? WHERE id = ?",
                (f"Zielordner nicht erreichbar: {target} ({exc})", time.time(), job_id),
            )
            return

        cmd = build_command(job["query"])
        workdir = DATA_DIR / "tmp" / "spotdl"
        workdir.mkdir(parents=True, exist_ok=True)
        db.execute(
            "UPDATE downloads SET status = 'running', started_at = ?, done = 0, failed = 0, message = '' WHERE id = ?",
            (time.time(), job_id),
        )
        log.info("Starte Download %s: %s", job_id, job["query"])
        kwargs: dict[str, Any] = {}
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000
        else:
            kwargs["start_new_session"] = True
        with self._lock:
            self._current = job_id
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                cwd=str(workdir), env=child_env(), text=True, encoding="utf-8", errors="replace", **kwargs,
            )
        proc = self._proc
        lines: list[str] = []
        total = int(job["total"] or 0)
        done = 0
        errors: list[str] = []
        last_flush = 0.0
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = ANSI.sub("", raw).rstrip()
            if not line.strip():
                continue
            lines.append(line)
            if (m := RE_FOUND.search(line)):
                total = max(total, int(m.group(1)))
            elif RE_DOWNLOADED.search(line) or RE_SKIPPED.search(line):
                done += 1
            elif RE_ERROR.search(line):
                errors.append(line.strip())
            now = time.time()
            if now - last_flush > 1.0:
                last_flush = now
                db.execute(
                    "UPDATE downloads SET done = ?, total = ?, log = ? WHERE id = ?",
                    (done, max(total, done), "\n".join(lines[-300:]), job_id),
                )
        code = proc.wait()
        with self._lock:
            self._proc = None
            self._current = None
            cancelled = job_id in self._cancel
            self._cancel.discard(job_id)

        total = max(total, done)
        failed = max(total - done, 0)
        if cancelled:
            status, message = "cancelled", "Abgebrochen"
        elif done > 0 and failed == 0:
            status, message = "done", f"{done} Song{'s' if done != 1 else ''} gespeichert"
        elif done > 0:
            status, message = "partial", f"{done} von {total} Songs gespeichert, {failed} nicht gefunden"
            if errors:
                message = f"{message} – {friendly_error(errors[-1])}"[:500]
        else:
            status = "error"
            message = friendly_error(errors[-1] if errors else f"spotDL beendet mit Code {code}")[:500]
            if code == 0 and not errors:
                message = "Nichts heruntergeladen – Song nicht gefunden?"
        db.execute(
            "UPDATE downloads SET status = ?, message = ?, done = ?, failed = ?, total = ?, log = ?, finished_at = ? "
            "WHERE id = ?",
            (status, message, done, failed, total, "\n".join(lines[-300:]), time.time(), job_id),
        )
        log.info("Download %s: %s (%s)", job_id, status, message)
        if done > 0:
            scanner.start()

    def _kill(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                               creationflags=0x08000000)
            else:
                os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


downloads = DownloadManager()
