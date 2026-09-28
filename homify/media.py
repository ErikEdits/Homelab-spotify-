"""ffmpeg: finden, Dauer ermitteln und Formate umwandeln, die der Browser nicht abspielen kann."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

from .config import DATA_DIR, IS_WINDOWS, config

log = logging.getLogger("homify.media")

TRANSCODE_DIR = DATA_DIR / "cache" / "transcode"
# Qualitätsstufen (kbit/s). „high“/„low“ kommen aus den Einstellungen.
QUALITIES = ("high", "normal", "low", "minimal")

# Zielformat: (Encoder-Argumente, ffmpeg-Format, Dateiendung, MIME-Typ)
FORMATS = {
    "mp3": (["-c:a", "libmp3lame"], "mp3", "mp3", "audio/mpeg"),
    "aac": (["-c:a", "aac", "-movflags", "+faststart"], "ipod", "m4a", "audio/mp4"),
    "opus": (["-c:a", "libopus", "-vbr", "on"], "ogg", "ogg", "audio/ogg"),
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


def quality_kbps(quality: str) -> int:
    if quality == "high":
        return int(config.get("transcode_high_kbps") or 320)
    if quality == "low":
        return int(config.get("transcode_low_kbps") or 128)
    if quality == "minimal":
        return 64
    return 192  # normal


# Moderne Codecs: nochmal verlustbehaftet umwandeln kostet mehr Klang, als die paar gesparten kbit/s wert sind
EFFICIENT_CODECS = {"opus", "webm", "aac", "vorbis"}
LOW_BITRATE_KBPS = 192  # bis hier klingt Opus deutlich besser als MP3/AAC


def needs_transcode_for_quality(bitrate: int, quality: str, codec: str = "") -> bool:
    """Muss für diese Qualitätsstufe umgewandelt werden? (Quelle deutlich größer als das Ziel)"""
    if quality not in QUALITIES:
        return False
    tolerance = 1.35 if (codec or "").lower() in EFFICIENT_CODECS else 1.15
    return (bitrate or 10**9) > quality_kbps(quality) * 1000 * tolerance


def choose_format(quality: str, client_opus: bool = False) -> str:
    """Zielformat: das eingestellte – bei niedrigen Bitraten Opus, wenn das Gerät es abspielen kann."""
    fmt = config.get("transcode_format") or "mp3"
    if client_opus and quality_kbps(quality) <= LOW_BITRATE_KBPS:
        return "opus"
    return fmt if fmt in FORMATS else "mp3"


def output_format(fmt: str | None = None) -> tuple[list[str], str, str, str]:
    return FORMATS.get(fmt or config.get("transcode_format") or "mp3", FORMATS["mp3"])


_sem: threading.BoundedSemaphore | None = None
_sem_size = 0
_sem_guard = threading.Lock()


def _semaphore() -> threading.BoundedSemaphore:
    """Begrenzt gleichzeitige Umwandlungen (Einstellung „Gleichzeitige Umwandlungen“)."""
    global _sem, _sem_size
    size = max(1, int(config.get("max_transcodes") or 2))
    with _sem_guard:
        if _sem is None or size != _sem_size:
            _sem, _sem_size = threading.BoundedSemaphore(size), size
        return _sem


class TranscodeError(RuntimeError):
    pass


def transcode(track: dict, quality: str = "high", source: Callable[[], str] | None = None,
              fmt: str | None = None) -> tuple[Path, str]:
    """
    Wandelt einen Song um (Format und Bitrate aus den Einstellungen, Ergebnis wird zwischengespeichert).
    source: liefert den lokalen Pfad der Quelldatei (bei NAS-Dateien eine temporäre Kopie).
    fmt: Zielformat (Standard: Einstellung „Format beim Umwandeln“)
    Rückgabe: (Datei, MIME-Typ)
    """
    if quality not in QUALITIES:
        quality = "high"
    ff = find_ffmpeg()
    if not ff:
        raise TranscodeError("ffmpeg wurde nicht gefunden")
    fmt = fmt if fmt in FORMATS else (config.get("transcode_format") or "mp3")
    args, container, ext, mime = output_format(fmt)
    kbps = quality_kbps(quality)
    key = f"{track['id']}_{fmt}{kbps}_{int(track.get('mtime') or 0)}"
    out = TRANSCODE_DIR / key[:2] / f"{key}.{ext}"
    if out.exists():
        _touch(out)
        return out, mime
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock, _semaphore():
        if out.exists():
            return out, mime
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part")
        input_path = source() if source else track["path"]
        cmd = [ff, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", input_path,
               "-map", "0:a:0", "-vn", "-map_metadata", "-1"]
        if fmt == "mp3" and ((track.get("sample_rate") or 0) > 48000 or track.get("codec") == "dsd"):
            cmd += ["-ar", "44100"]
        cmd += args + ["-b:a", f"{kbps}k", "-f", container, str(tmp)]
        log.info("Wandle um: %s (%s, %s kbit/s)", track["path"], fmt, kbps)
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
    return out, mime


def _touch(path: Path) -> None:
    try:
        os.utime(path, None)
    except OSError:
        pass


def _cache_files():
    return (p for p in TRANSCODE_DIR.rglob("*") if p.is_file() and p.suffix != ".part")


def cache_size() -> int:
    total = 0
    if TRANSCODE_DIR.exists():
        for p in _cache_files():
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
    for p in _cache_files():
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
