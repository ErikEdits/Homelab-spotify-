"""Sicherungen: Datenbank (Benutzer, Playlists, Likes, Verlauf) + Einstellungen als .zip."""

from __future__ import annotations

import io
import json
import logging
import sqlite3
import time
import zipfile
from pathlib import Path

from . import __version__, db
from .config import DATA_DIR, config

log = logging.getLogger("homify.backup")

BACKUP_DIR = DATA_DIR / "backups"


def create_zip_bytes() -> bytes:
    """Sicherung im Speicher (für den Download im Browser)."""
    snapshot = DATA_DIR / "tmp" / f"backup-{time.time_ns()}.db"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    dst = sqlite3.connect(str(snapshot))
    try:
        db.conn().backup(dst)
    finally:
        dst.close()
    buf = io.BytesIO()
    try:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snapshot, "homify.db")
            z.writestr("config.json", json.dumps(config.public(), indent=2, ensure_ascii=False))  # ohne Passwörter
            z.writestr("homify-backup.txt", f"Homify {__version__} Sicherung vom {time.strftime('%d.%m.%Y %H:%M')}")
    finally:
        snapshot.unlink(missing_ok=True)
    return buf.getvalue()


def create_file() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / time.strftime("homify-sicherung-%Y-%m-%d_%H-%M.zip")
    tmp = target.with_suffix(".part")
    tmp.write_bytes(create_zip_bytes())
    tmp.replace(target)
    log.info("Sicherung erstellt: %s", target.name)
    return target


def list_backups() -> list[dict]:
    if not BACKUP_DIR.exists():
        return []
    files = sorted(BACKUP_DIR.glob("homify-sicherung-*.zip"), reverse=True)
    return [{"name": f.name, "size": f.stat().st_size, "created": f.stat().st_mtime} for f in files]


def prune(keep: int) -> None:
    files = sorted(BACKUP_DIR.glob("homify-sicherung-*.zip"), reverse=True) if BACKUP_DIR.exists() else []
    for f in files[max(1, keep):]:
        try:
            f.unlink()
        except OSError:
            pass


def daily() -> None:
    """Einmal am Tag (vom Zeitplaner aufgerufen)."""
    if not config.get("auto_backup"):
        return
    latest = list_backups()
    if latest and time.time() - latest[0]["created"] < 20 * 3600:
        return
    try:
        create_file()
        prune(int(config.get("backup_keep") or 7))
    except Exception:
        log.exception("Automatische Sicherung fehlgeschlagen")
