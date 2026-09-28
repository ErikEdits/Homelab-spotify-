"""Umzug der Musik von einem Speicherort zum anderen (z. B. App-Ordner -> UGREEN-NAS)."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
import time
from typing import Any, Callable

from . import db
from .config import DATA_DIR
from .storage import LocalStorage, Storage

log = logging.getLogger("homify.migrate")


class Migration:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._cancel = False
        self.status: dict[str, Any] = {
            "running": False, "total": 0, "done": 0, "skipped": 0, "failed": 0, "bytes": 0,
            "total_bytes": 0, "current": "", "message": "", "finished_at": None, "errors": [],
            "source": "", "target": "",
        }

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, source: Storage, target: Storage, keep_copy: bool,
              on_done: Callable[[bool], None] | None = None) -> bool:
        if self.running:
            return False
        self._cancel = False
        self.status.update(running=True, total=0, done=0, skipped=0, failed=0, bytes=0, total_bytes=0,
                           current="", message="Suche Dateien …", finished_at=None, errors=[],
                           source=source.label, target=target.label)
        self._thread = threading.Thread(target=self._run, args=(source, target, keep_copy, on_done),
                                        name="migration", daemon=True)
        self._thread.start()
        return True

    def cancel(self) -> None:
        self._cancel = True

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _run(self, source: Storage, target: Storage, keep_copy: bool, on_done) -> None:
        st = self.status
        ok = False
        try:
            files = list(source.walk())
            files = [f for f in files if not os.path.basename(f[0]).endswith(".part")]
            st.update(total=len(files), total_bytes=sum(f[1] for f in files), message="Übertrage …")
            if hasattr(target, "ensure_base"):
                target.ensure_base()
            tmp_dir = DATA_DIR / "tmp" / "migration"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            for rel, size, _mtime in files:
                if self._cancel:
                    st["message"] = "Abgebrochen"
                    break
                st["current"] = rel
                try:
                    existing = target.stat(rel)
                    if existing and existing[0] == size:
                        st["skipped"] += 1  # schon da (gleiche Größe)
                    else:
                        self._copy(source, target, rel, tmp_dir)
                        check = target.stat(rel)
                        if not check or check[0] != size:
                            raise RuntimeError("Größe stimmt nach dem Kopieren nicht überein")
                        st["done"] += 1
                    if not keep_copy:
                        source.remove(rel)
                except Exception as exc:
                    st["failed"] += 1
                    st["errors"] = (st["errors"] + [f"{rel}: {exc}"])[-20:]
                    log.warning("Umzug fehlgeschlagen für %s: %s", rel, exc)
                st["bytes"] += size
            else:
                ok = st["failed"] == 0
                if not keep_copy:
                    source.remove_empty_dirs()
                st["message"] = (
                    f"Fertig: {st['done']} übertragen, {st['skipped']} waren schon da"
                    + (f", {st['failed']} Fehler" if st["failed"] else "")
                )
        except Exception as exc:
            log.exception("Umzug abgebrochen")
            st["message"] = f"Fehler: {exc}"
        finally:
            st.update(running=False, current="", finished_at=time.time())
            db.close()
            if on_done:
                try:
                    on_done(ok)
                except Exception:
                    log.exception("Nach dem Umzug ist ein Fehler aufgetreten")

    @staticmethod
    def _copy(source: Storage, target: Storage, rel: str, tmp_dir) -> None:
        local = source.local_path(rel)
        if local:
            target.put(local, rel, move=False)
            return
        # Quelle ist selbst ein NAS: erst in eine temporäre Datei holen
        fd, tmp = tempfile.mkstemp(dir=tmp_dir)
        os.close(fd)
        try:
            with source.open(rel) as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            target.put(tmp, rel, move=isinstance(target, LocalStorage))
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


migration = Migration()
