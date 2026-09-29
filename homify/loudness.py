"""
Klang-Analyse im Hintergrund – ein ffmpeg-Durchlauf pro Song misst:

- Lautheit (EBU R128 / ReplayGain 2.0) -> „Lautstärke angleichen“ auch ohne ReplayGain-Tags
- echte Spitzen (True Peak) -> leise Songs dürfen angehoben werden, ohne zu übersteuern
- Stille am Anfang und am Ende -> kürzere Pausen zwischen Songs, Überblenden über echte Musik

Außerdem den Pegel ganzer Alben (aus den Einzelmessungen), damit „Pro Album“ auch ohne Tags klappt.
Läuft langsam im Hintergrund, der Server bleibt nutzbar.
"""

from __future__ import annotations

import logging
import math
import os
import re
import shutil
import tempfile
import threading
import time
from typing import Any

from . import db
from . import storage as storages
from .config import DATA_DIR, config
from .media import find_ffmpeg, run_hidden

log = logging.getLogger("homify.loudness")

REFERENCE_LUFS = -18.0  # ReplayGain 2.0
ANALYSIS_VERSION = 2    # höher setzen, wenn neue Messwerte dazukommen -> alles wird einmal neu gemessen
SILENCE_DB = -60        # leiser als das gilt als Stille
SILENCE_MIN = 0.3       # Sekunden

_INTEGRATED = re.compile(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS")
_TRUE_PEAK = re.compile(r"Peak:\s*(-?(?:\d+(?:\.\d+)?|inf))\s*dBFS")
_SIL_START = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SIL_END = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")
_TIME = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def _run_analysis(path: str) -> str | None:
    ff = find_ffmpeg()
    if not ff:
        return None
    try:
        res = run_hidden(
            [ff, "-hide_banner", "-nostdin", "-threads", "1", "-i", path, "-map", "0:a:0",
             "-af", f"ebur128=peak=true:framelog=quiet,silencedetect=n={SILENCE_DB}dB:d={SILENCE_MIN}",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=900, encoding="utf-8", errors="replace",
        )
    except Exception:
        return None
    return res.stderr or ""


def parse_analysis(output: str, duration: float = 0.0) -> dict[str, Any] | None:
    """ffmpeg-Ausgabe -> {lufs, peak, lead_in, tail} (None, wenn keine Lautheit gemessen wurde)."""
    found = _INTEGRATED.findall(output)
    if not found:
        return None
    lufs = float(found[-1])
    if not -70 < lufs < 5:
        return None
    peak = None
    peaks = _TRUE_PEAK.findall(output)
    if peaks and "inf" not in peaks[-1]:
        value = float(peaks[-1])
        peak = value if -100 < value < 30 else None
    times = _TIME.findall(output)
    total = (int(times[-1][0]) * 3600 + int(times[-1][1]) * 60 + float(times[-1][2])) if times else duration
    total = total or duration

    # Stille-Abschnitte in Reihenfolge: (Anfang, Ende oder None = bis zum Schluss)
    events: list[list[float | None]] = []
    for line in output.splitlines():
        if (m := _SIL_START.search(line)):
            events.append([max(0.0, float(m.group(1))), None])
        elif (m := _SIL_END.search(line)) and events and events[-1][1] is None:
            events[-1][1] = float(m.group(1))
    lead_in = tail = 0.0
    if events and total:
        first_start, first_end = events[0]
        if first_start <= 0.05 and first_end is not None and first_end < total - 1:
            lead_in = first_end
        last_start, last_end = events[-1]
        if (last_end is None or last_end >= total - 0.05) and last_start > lead_in:
            tail = total - last_start
    return {"lufs": round(lufs, 2), "peak": round(peak, 2) if peak is not None else None,
            "lead_in": round(lead_in, 3), "tail": round(max(0.0, tail), 3)}


def analyze_file(path: str, duration: float = 0.0) -> dict[str, Any] | None:
    out = _run_analysis(path)
    return parse_analysis(out, duration) if out is not None else None


def measure_file(path: str) -> float | None:
    """Integrierte Lautheit in LUFS oder None."""
    res = analyze_file(path)
    return res["lufs"] if res else None


def gain_for(lufs: float) -> float:
    return round(max(-30.0, min(20.0, REFERENCE_LUFS - lufs)), 2)


def album_values(rows: list[dict[str, Any]]) -> tuple[float, float | None] | None:
    """Pegel eines ganzen Albums aus den Einzelmessungen (Energie-Mittel, nach Länge gewichtet)."""
    if not rows or any(r.get("lufs") is None for r in rows):
        return None
    weights = [max(float(r.get("duration") or 0), 1.0) for r in rows]
    energy = sum(w * 10 ** (r["lufs"] / 10) for w, r in zip(weights, rows)) / sum(weights)
    lufs = 10 * math.log10(energy) if energy > 0 else -70.0
    peaks = [r["peak"] for r in rows if r.get("peak") is not None]
    return gain_for(lufs), (max(peaks) if peaks else None)


def update_albums(album_ids: set[str]) -> None:
    for album_id in album_ids:
        if not album_id:
            continue
        rows = db.query("SELECT lufs, peak, duration FROM tracks WHERE album_id = ?", (album_id,))
        values = album_values(rows)
        if values is None:
            db.execute("DELETE FROM album_loudness WHERE album_id = ?", (album_id,))
        else:
            db.execute("INSERT OR REPLACE INTO album_loudness (album_id, gain, peak) VALUES (?, ?, ?)",
                       (album_id, values[0], values[1]))


class Analyzer:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._failed: set[str] = set()
        self.status = {"running": False, "done": 0, "remaining": 0}

    def kick(self) -> None:
        if not config.get("loudness_analysis"):
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="loudness", daemon=True)
            self._thread.start()

    def remaining(self) -> int:
        row = db.query_one("SELECT COUNT(*) AS n FROM tracks WHERE analyzed < ?", (ANALYSIS_VERSION,))
        return int(row["n"]) if row else 0

    def _run(self) -> None:
        self.status.update(running=True)
        try:
            while config.get("loudness_analysis"):
                skip = list(self._failed)[:900]
                marks = ",".join("?" * len(skip)) or "''"
                # Songs ganz ohne Lautstärke-Wert zuerst, dann die neuesten
                rows = db.query(
                    f"SELECT id, root, rel, path, duration, album_id FROM tracks WHERE analyzed < ? "
                    f"AND id NOT IN ({marks}) ORDER BY gain IS NOT NULL, added_at DESC LIMIT 20",
                    [ANALYSIS_VERSION, *skip],
                )
                self.status["remaining"] = self.remaining()
                if not rows:
                    break
                albums: set[str] = set()
                for row in rows:
                    if not config.get("loudness_analysis"):
                        break
                    res = self._measure(row)
                    if res is None:
                        self._failed.add(row["id"])
                        continue
                    db.execute(
                        "UPDATE tracks SET lufs = ?, peak = ?, lead_in = ?, tail = ?, analyzed = ?, "
                        "gain = COALESCE(gain, ?) WHERE id = ?",
                        (res["lufs"], res["peak"], res["lead_in"], res["tail"], ANALYSIS_VERSION,
                         gain_for(res["lufs"]), row["id"]),
                    )
                    albums.add(row["album_id"])
                    self.status["done"] += 1
                    time.sleep(0.2)  # Server nicht auslasten
                update_albums(albums)
        except Exception:
            log.exception("Klang-Analyse abgebrochen")
        finally:
            self.status.update(running=False, remaining=self.remaining())
            db.close()

    def _measure(self, row: dict) -> dict[str, Any] | None:
        st = storages.by_key(row["root"])
        if st is None or not row.get("rel"):
            return None
        local = st.local_path(row["rel"])
        tmp_path = None
        try:
            if not local:
                tmp_dir = DATA_DIR / "tmp" / "loudness"
                tmp_dir.mkdir(parents=True, exist_ok=True)
                fd, tmp_path = tempfile.mkstemp(dir=tmp_dir, suffix=os.path.splitext(row["rel"])[1])
                os.close(fd)
                with st.open(row["rel"]) as src, open(tmp_path, "wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                local = tmp_path
            return analyze_file(local, float(row.get("duration") or 0))
        except Exception as exc:
            log.debug("Klang-Analyse nicht möglich für %s: %s", row["rel"], exc)
            return None
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)


analyzer = Analyzer()
