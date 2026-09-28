"""Pfade und Einstellungen (data/config.json)."""

from __future__ import annotations

import copy
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
DATA_DIR = Path(os.environ.get("HOMIFY_DATA", APP_DIR / "data")).resolve()

IS_WINDOWS = sys.platform.startswith("win")

DEFAULTS: dict[str, Any] = {
    # Server
    "host": "0.0.0.0",
    "port": 8484,
    # Speicherort der Musik: "local" = App-Ordner (data/music), "nas" = NAS per SMB,
    # "folder" = eigener Pfad (z. B. /mnt/nas/musik oder \\\\NAS\\Musik)
    "storage_mode": "local",
    "storage_path": "",
    "nas_host": "",
    "nas_share": "",
    "nas_folder": "Musik",
    "nas_user": "",
    "nas_password": "",
    # Zusätzliche Ordner, die nur gelesen werden (z. B. vorhandene Sammlung)
    "music_dirs": [],
    "scan_interval_minutes": 30,
    # Downloads über spotDL
    "output_template": "{album-artist}/{album}/{artists} - {title}.{output-ext}",
    "download_format": "mp3",
    "download_bitrate": "auto",
    "download_threads": 2,
    "spotdl_extra_args": "",
    "spotdl_cookie_file": "",
    # Spotify (optional – ohne Zugangsdaten nutzt spotDL einen freien Client)
    "spotify_client_id": "",
    "spotify_client_secret": "",
    "spotify_use_official_api": False,
    # Streaming
    "transcode_cache_mb": 2048,
    "ffmpeg_path": "",
}

# Werte, die niemals ans Frontend gehen
SECRET_KEYS = {"spotify_client_secret", "nas_password"}


class Config:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        with self._lock:
            if self.path.exists():
                try:
                    stored = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    stored = {}
                for key, value in stored.items():
                    if key in DEFAULTS:
                        self._data[key] = value
                # Ältere Versionen kannten nur „music_dirs“: erster Ordner wird zum Speicherort
                if "storage_mode" not in stored and self._data["music_dirs"]:
                    first, *rest = self._data["music_dirs"]
                    self._data.update(storage_mode="folder", storage_path=first, music_dirs=rest)
            env_dirs = os.environ.get("HOMIFY_MUSIC_DIRS")
            if env_dirs and self._data["storage_mode"] == "local" and not self._data["storage_path"]:
                dirs = [d for d in env_dirs.split(os.pathsep) if d]
                if dirs:
                    self._data.update(storage_mode="folder", storage_path=dirs[0], music_dirs=dirs[1:])

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)  # enthält ggf. NAS-Passwort – nur für den eigenen Benutzer lesbar
            except OSError:
                pass
            os.replace(tmp, self.path)

    def get(self, key: str) -> Any:
        with self._lock:
            return copy.deepcopy(self._data.get(key, DEFAULTS.get(key)))

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def update(self, values: dict[str, Any]) -> None:
        with self._lock:
            for key, value in values.items():
                if key not in DEFAULTS:
                    continue
                if key in SECRET_KEYS and value == "********":
                    continue  # Platzhalter aus dem Frontend -> altes Secret behalten
                self._data[key] = _coerce(key, value)
            self.save()

    def public(self) -> dict[str, Any]:
        with self._lock:
            data = copy.deepcopy(self._data)
        for key in SECRET_KEYS:
            data[key] = "********" if data.get(key) else ""
        return data

    @property
    def music_dirs(self) -> list[str]:
        return [d for d in (self.get("music_dirs") or []) if d and str(d).strip()]



def _coerce(key: str, value: Any) -> Any:
    default = DEFAULTS[key]
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes", "ja", "on")
        return bool(value)
    if isinstance(default, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, list):
        if isinstance(value, str):
            value = [line for line in value.splitlines()]
        return [str(v).strip() for v in (value or []) if str(v).strip()]
    return "" if value is None else str(value).strip()


config = Config(DATA_DIR / "config.json")
