"""Verwaltet spotDL in einer eigenen Python-Umgebung (data/tools/spotdl-venv)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from .config import DATA_DIR, IS_WINDOWS, PACKAGE_DIR, config
from .media import _NO_WINDOW

log = logging.getLogger("homify.tools")

SPOTDL_VENV = Path(os.environ.get("HOMIFY_SPOTDL_VENV", DATA_DIR / "tools" / "spotdl-venv"))
BRIDGE_SCRIPT = PACKAGE_DIR / "spotdl_bridge.py"
SPOTDL_RUNNER = PACKAGE_DIR / "spotdl_run.py"  # startet spotDL mit Homify-Sicherungen (statt „-m spotdl“)


def venv_python() -> Path:
    if IS_WINDOWS:
        return SPOTDL_VENV / "Scripts" / "python.exe"
    return SPOTDL_VENV / "bin" / "python"


def child_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", NO_COLOR="1", TERM="dumb", COLUMNS="160")
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env.pop("VIRTUAL_ENV", None)
    return env


# --------------------------------------------------------------------------- #
# Installation / Update
# --------------------------------------------------------------------------- #

class ToolManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.status: dict[str, Any] = {"running": False, "action": "", "ok": None, "log": []}
        self._log: deque[str] = deque(maxlen=400)
        self._version_cache: tuple[float, dict] | None = None
        self.echo = None  # optional: Ausgabe direkt auf die Konsole (python -m homify setup)

    def installed(self) -> bool:
        return venv_python().exists()

    def _emit(self, line: str) -> None:
        line = line.rstrip()
        if line:
            self._log.append(line)
            self.status["log"] = list(self._log)[-120:]
            if self.echo:
                self.echo(line)
            else:
                log.info("[spotdl-setup] %s", line)

    def _run(self, cmd: list[str], timeout: int = 1800) -> int:
        self._emit("$ " + " ".join(str(c) for c in cmd))
        proc = subprocess.Popen(
            [str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", env=child_env(), creationflags=_NO_WINDOW,
        )
        start = time.time()
        assert proc.stdout is not None
        for line in proc.stdout:
            self._emit(line)
            if time.time() - start > timeout:
                proc.kill()
                self._emit("Abbruch: Zeitüberschreitung")
                return -1
        return proc.wait()

    def install(self, upgrade: bool = True) -> bool:
        """spotDL installieren/aktualisieren (blockierend)."""
        if not self.lock.acquire(blocking=False):
            return False
        try:
            self._log.clear()
            self.status.update(running=True, action="upgrade" if self.installed() else "install", ok=None)
            if not self.installed():
                self._emit("Erstelle eigene Python-Umgebung für spotDL …")
                SPOTDL_VENV.parent.mkdir(parents=True, exist_ok=True)
                if self._run([sys.executable, "-m", "venv", str(SPOTDL_VENV)]) != 0:
                    raise RuntimeError("venv konnte nicht erstellt werden")
            py = venv_python()
            self._run([py, "-m", "pip", "install", "--upgrade", "pip"], timeout=600)
            pip_cmd = [py, "-m", "pip", "install", "spotdl"]
            if upgrade:
                # „eager“: auch yt-dlp und ytmusicapi auf den neuesten passenden Stand – YouTube ändert
                # häufig etwas, und ein altes yt-dlp ist der häufigste Grund für fehlgeschlagene Downloads
                pip_cmd[4:4] = ["--upgrade", "--upgrade-strategy", "eager"]
            if self._run(pip_cmd) != 0:
                raise RuntimeError("pip install spotdl fehlgeschlagen")
            self._emit("Lade Deno (wird von yt-dlp für YouTube benötigt) …")
            try:
                result = bridge.call("setup", timeout=600, restart=True)
                self._emit(f"Deno: {result.get('deno')}")
            except Exception as exc:
                self._emit(f"Hinweis: Deno konnte nicht geladen werden ({exc}). "
                           "Downloads funktionieren evtl. trotzdem.")
            self._version_cache = None
            self._emit("Fertig - spotDL ist bereit.")
            self.status.update(ok=True)
            return True
        except Exception as exc:
            self._emit(f"Fehler: {exc}")
            self.status.update(ok=False)
            return False
        finally:
            self.status["running"] = False
            self.lock.release()

    def install_async(self, upgrade: bool = True) -> bool:
        if self.status["running"]:
            return False
        threading.Thread(target=self.install, args=(upgrade,), daemon=True, name="spotdl-install").start()
        return True

    def versions(self) -> dict:
        if not self.installed():
            return {}
        if self._version_cache and time.time() - self._version_cache[0] < 600:
            return self._version_cache[1]
        try:
            v = bridge.call("versions", timeout=60)
        except Exception as exc:
            v = {"error": str(exc)}
        self._version_cache = (time.time(), v)
        return v


# --------------------------------------------------------------------------- #
# Langlebiger Bridge-Prozess (Spotify-Suche ohne jedes Mal Python neu zu starten)
# --------------------------------------------------------------------------- #

class BridgeError(RuntimeError):
    pass


class Bridge:
    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._settings_sent: dict | None = None
        self._stderr_tail: deque[str] = deque(maxlen=40)

    def _settings(self) -> dict:
        return {
            "client_id": config.get("spotify_client_id"),
            "client_secret": config.get("spotify_client_secret"),
            "use_official_api": bool(config.get("spotify_use_official_api")),
        }

    def _start(self) -> None:
        py = venv_python()
        if not py.exists():
            raise BridgeError("spotDL ist noch nicht installiert (Einstellungen → spotDL installieren).")
        self._proc = subprocess.Popen(
            [str(py), "-u", str(BRIDGE_SCRIPT)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=child_env(), creationflags=_NO_WINDOW,
        )
        self._settings_sent = None
        threading.Thread(target=self._drain_stderr, args=(self._proc,), daemon=True).start()

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        assert proc.stderr is not None
        for raw in proc.stderr:
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self._stderr_tail.append(line)
                log.debug("[bridge] %s", line)

    def stop(self) -> None:
        with self._lock:
            self._kill()

    def _kill(self) -> None:
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None

    def _request(self, payload: dict, timeout: float) -> Any:
        assert self._proc and self._proc.stdin and self._proc.stdout
        self._next_id += 1
        payload = dict(payload, id=self._next_id)
        self._proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        self._proc.stdin.flush()
        result: dict = {}

        def reader():
            assert self._proc and self._proc.stdout
            while True:
                line = self._proc.stdout.readline()
                if not line:
                    return
                try:
                    msg = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if msg.get("id") == payload["id"]:
                    result.update(msg)
                    return

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            self._kill()
            raise BridgeError("Zeitüberschreitung bei spotDL/Spotify")
        if not result:
            tail = " | ".join(list(self._stderr_tail)[-3:])
            self._kill()
            raise BridgeError(f"spotDL-Prozess beendet. {tail}".strip())
        if not result.get("ok"):
            raise BridgeError(result.get("error") or "Unbekannter Fehler")
        return result.get("result")

    def call(self, cmd: str, timeout: float = 45, restart: bool = False, **params) -> Any:
        with self._lock:
            if restart:
                self._kill()
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            settings = self._settings()
            if settings != self._settings_sent:
                self._request({"cmd": "init", "settings": settings}, timeout=30)
                self._settings_sent = settings
            return self._request({"cmd": cmd, **params}, timeout=timeout)


bridge = Bridge()
tools = ToolManager()
