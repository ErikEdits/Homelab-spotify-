"""ffmpeg: finden, Dauer ermitteln und Formate umwandeln, die der Browser nicht abspielen kann."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from .config import DATA_DIR, IS_WINDOWS, config

log = logging.getLogger("homify.media")

TRANSCODE_DIR = DATA_DIR / "cache" / "transcode"
QUALITIES = {
    # name: (ffmpeg-Argumente, maximale Quell-Bitrate, ab der umgewandelt wird)
    "high": (["-c:a", "libmp3lame", "-q:a", "0"], None),
    "low": (["-c:a", "libmp3lame", "-b:a", "128k"], 170_000),
}

_ffmpeg_cache: str | None = None
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0  # kein schwarzes Konsolenfenster unter Windows


def find_ffmpeg(refresh: bool = False) -> str | None:
    global _ffmpeg_cache
    if _ffmpeg_cache and not refresh and os.path.exists(_ffmpeg_cache):
        return _ffmpeg_cache
    candidates: list[str] = []
    configured = (config.get("ffmpeg_path") or "").strip()
    if configured:
        candidates.append(configured)
    on_path = shutil.which("ffmpeg")
    if on_path:
        candidates.append(on_path)
    try:
        import imageio_ffmpeg  # bringt eine fertige ffmpeg.exe / ffmpeg-Binary mit

        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    spotdl_dir = Path.home() / ".spotdl"
    candidates.append(str(spotdl_dir / ("ffmpeg.exe" if IS_WINDOWS else "ffmpeg")))
    for c in candidates:
        if c and os.path.isfile(c):
            _ffmpeg_cache = c
            return c
    _ffmpeg_cache = None
    return None


def run_hidden(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, creationflags=_NO_WINDOW, **kwargs)


def ffmpeg_version() -> str | None:
    ff = find_ffmpeg()
    if not ff:
        return None
    try:
        out = run_hidden([ff, "-version"], capture_output=True, text=True, timeout=15).stdout
        return out.splitlines()[0] if out else None
    except Exception:
        return None


_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def probe_duration(path: str) -> float:
    ff = find_ffmpeg()
    if not ff:
        return 0.0
    try:
        res = run_hidden(
            [ff, "-hide_banner", "-nostdin", "-i", path], capture_output=True, text=True,
            timeout=30, encoding="utf-8", errors="replace",
        )
        m = _DURATION.search(res.stderr or "")
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    return 0.0


def needs_transcode_for_quality(bitrate: int, quality: str) -> bool:
    if quality not in QUALITIES:
        return False
    limit = QUALITIES[quality][1]
    return limit is not None and (bitrate or 10**9) > limit


class TranscodeError(RuntimeError):
    pass


def transcode(track: dict, quality: str = "high") -> Path:
    """Wandelt einen Song in MP3 um (Ergebnis wird zwischengespeichert)."""
    if quality not in QUALITIES:
        quality = "high"
    ff = find_ffmpeg()
    if not ff:
        raise TranscodeError("ffmpeg wurde nicht gefunden")
    key = f"{track['id']}_{quality}_{int(track.get('mtime') or 0)}"
    out = TRANSCODE_DIR / key[:2] / f"{key}.mp3"
    if out.exists():
        _touch(out)
        return out
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        if out.exists():
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part")
        args, _ = QUALITIES[quality]
        cmd = [ff, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", track["path"],
               "-map", "0:a:0", "-vn", "-map_metadata", "-1"]
        if (track.get("sample_rate") or 0) > 48000 or track.get("codec") == "dsd":
            cmd += ["-ar", "44100"]
        cmd += args + ["-f", "mp3", str(tmp)]
        log.info("Wandle um: %s (%s)", track["path"], quality)
        try:
            res = run_hidden(cmd, capture_output=True, text=True, timeout=900, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired as exc:
            tmp.unlink(missing_ok=True)
            raise TranscodeError("Umwandlung hat zu lange gedauert") from exc
        if res.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            raise TranscodeError((res.stderr or "ffmpeg-Fehler").strip()[-500:])
        os.replace(tmp, out)
    threading.Thread(target=cleanup_cache, daemon=True).start()
    return out


def _touch(path: Path) -> None:
    try:
        os.utime(path, None)
    except OSError:
        pass


def cache_size() -> int:
    total = 0
    if TRANSCODE_DIR.exists():
        for p in TRANSCODE_DIR.rglob("*.mp3"):
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def cleanup_cache() -> None:
    limit = max(int(config.get("transcode_cache_mb") or 0), 100) * 1024 * 1024
    if not TRANSCODE_DIR.exists():
        return
    files = []
    for p in TRANSCODE_DIR.rglob("*.mp3"):
        try:
            st = p.stat()
            files.append((st.st_mtime, st.st_size, p))
        except OSError:
            pass
    total = sum(f[1] for f in files)
    for _, size, p in sorted(files):
        if total <= limit:
            break
        try:
            p.unlink()
            total -= size
        except OSError:
            pass


def clear_cache() -> None:
    shutil.rmtree(TRANSCODE_DIR, ignore_errors=True)
