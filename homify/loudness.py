"""
Lautheit messen (EBU R128 / ReplayGain 2.0) für Songs ohne ReplayGain-Tags.
Läuft langsam im Hintergrund – Grundlage für „Lautstärke angleichen“.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import threading
import time

from . import db
from . import storage as storages
from .config import DATA_DIR, config
from .media import find_ffmpeg, run_hidden

log = logging.getLogger("homify.loudness")

REFERENCE_LUFS = -18.0  # ReplayGain 2.0
_INTEGRATED = re.compile(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS")


def measure_file(path: str) -> float | None:
    """Integrierte Lautheit in LUFS oder None."""
    ff = find_ffmpeg()
    if not ff:
        return None
    try:
        res = run_hidden(
            [ff, "-hide_banner", "-nostdin", "-threads", "1", "-i", path, "-map", "0:a:0",
             "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
            capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace",
        )
    except Exception:
        return None
    found = _INTEGRATED.findall(res.stderr or "")
    if not found:
        return None
    value = float(found[-1])
    return value if -70 < value < 5 else None


def gain_for(lufs: float) -> float:
    return round(max(-30.0, min(20.0, REFERENCE_LUFS - lufs)), 2)


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
        row = db.query_one("SELECT COUNT(*) AS n FROM tracks WHERE gain IS NULL")
        return int(row["n"]) if row else 0

    def _run(self) -> None:
        self.status.update(running=True)
        try:
            while config.get("loudness_analysis"):
                skip = list(self._failed)[:900]
                marks = ",".join("?" * len(skip)) or "''"
                rows = db.query(
                    f"SELECT id, root, rel, path FROM tracks WHERE gain IS NULL AND id NOT IN ({marks}) "
                    "ORDER BY added_at DESC LIMIT 20", skip,
                )
                self.status["remaining"] = self.remaining()
                if not rows:
                    break
                for row in rows:
                    if not config.get("loudness_analysis"):
                        break
                    gain = self._measure(row)
                    if gain is None:
                        self._failed.add(row["id"])
                    else:
                        db.execute("UPDATE tracks SET gain = ? WHERE id = ? AND gain IS NULL", (gain, row["id"]))
                        self.status["done"] += 1
                    time.sleep(0.2)  # Server nicht auslasten
        except Exception:
            log.exception("Lautheitsmessung abgebrochen")
        finally:
            self.status.update(running=False, remaining=self.remaining())
            db.close()

    def _measure(self, row: dict) -> float | None:
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
            lufs = measure_file(local)
            return gain_for(lufs) if lufs is not None else None
        except Exception as exc:
            log.debug("Lautheit nicht messbar für %s: %s", row["rel"], exc)
            return None
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)


analyzer = Analyzer()
