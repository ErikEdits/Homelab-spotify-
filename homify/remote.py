"""Zugriff von unterwegs über Tailscale: Adresse erkennen und HTTPS (tailscale serve) einrichten."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from typing import Any

from .config import IS_WINDOWS

log = logging.getLogger("homify.remote")

_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
_cache: dict[str, Any] = {"at": 0.0, "port": None, "value": None}


def tailscale_bin() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    candidates = []
    if IS_WINDOWS:
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if base:
                candidates.append(os.path.join(base, "Tailscale", "tailscale.exe"))
    else:
        candidates += ["/usr/bin/tailscale", "/usr/local/bin/tailscale", "/snap/bin/tailscale"]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _run(args: list[str], timeout: float = 8) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, encoding="utf-8",
                          errors="replace", creationflags=_NO_WINDOW, stdin=subprocess.DEVNULL)


def info(port: int, refresh: bool = False) -> dict[str, Any]:
    now = time.time()
    if not refresh and _cache["value"] is not None and _cache["port"] == port and now - _cache["at"] < 30:
        return _cache["value"]
    result: dict[str, Any] = {"installed": False, "running": False, "dns": "", "https_url": "", "urls": [],
                              "message": ""}
    binary = tailscale_bin()
    if binary:
        result["installed"] = True
        try:
            res = _run([binary, "status", "--json"])
            status = json.loads(res.stdout or "{}")
            me = status.get("Self") or {}
            result["running"] = status.get("BackendState") == "Running"
            dns = (me.get("DNSName") or "").rstrip(".")
            result["dns"] = dns
            ips = [ip for ip in (me.get("TailscaleIPs") or []) if ":" not in ip]
            if not result["running"]:
                result["message"] = "Tailscale ist installiert, aber nicht verbunden (tailscale up)."
            if dns and _serve_active(binary, dns, port):
                result["https_url"] = f"https://{dns}"
            urls = []
            if result["https_url"]:
                urls.append(result["https_url"])
            if dns:
                urls.append(f"http://{dns.split('.')[0]}:{port}")
            urls += [f"http://{ip}:{port}" for ip in ips]
            result["urls"] = urls
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            result["message"] = f"Tailscale-Status nicht lesbar: {exc}"
    _cache.update(at=now, port=port, value=result)
    return result


def _serve_active(binary: str, dns: str, port: int) -> bool:
    try:
        res = _run([binary, "serve", "status", "--json"])
        data = json.loads(res.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    for host, cfg in (data.get("Web") or {}).items():
        if not host.startswith(dns):
            continue
        for handler in ((cfg or {}).get("Handlers") or {}).values():
            proxy = (handler or {}).get("Proxy") or ""
            if proxy.rstrip("/").endswith(f":{port}"):
                return True
    return False


def setup_https(port: int) -> tuple[bool, str]:
    """Richtet https://<rechner>.<tailnet>.ts.net -> Homify ein (tailscale serve)."""
    binary = tailscale_bin()
    if not binary:
        return False, "Tailscale ist auf diesem Rechner nicht installiert."
    try:
        res = _run([binary, "serve", "--bg", str(port)], timeout=25)
    except subprocess.TimeoutExpired:
        return False, ("Tailscale wartet auf eine Bestätigung. Bitte in der Tailscale-Verwaltung "
                       "(login.tailscale.com → DNS) „HTTPS Certificates“ aktivieren und erneut versuchen.")
    except OSError as exc:
        return False, str(exc)
    output = (res.stdout + "\n" + res.stderr).strip()
    _cache["value"] = None
    if res.returncode == 0:
        return True, "HTTPS über Tailscale ist eingerichtet."
    if "access denied" in output.lower() or "permission" in output.lower():
        return False, ("Keine Berechtigung. Auf dem Server einmal ausführen: "
                       "sudo tailscale set --operator=$USER  (oder: sudo tailscale serve --bg %d)" % port)
    return False, output[-500:] or "tailscale serve ist fehlgeschlagen."
