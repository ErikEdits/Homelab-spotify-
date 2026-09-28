"""Kommandozeile:  python -m homify [run|setup|scan|add-user|reset-password]"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler

from . import APP_NAME, __version__
from .config import DATA_DIR, IS_WINDOWS, config


def _setup_logging(verbose: bool = False) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "logs").mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    file_handler = RotatingFileHandler(DATA_DIR / "logs" / "homify.log", maxBytes=2_000_000, backupCount=3,
                                       encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


def _open_app_window(url: str) -> None:
    """Öffnet Homify wie ein eigenes Programm (Edge/Chrome im App-Modus, ohne Adressleiste)."""
    candidates: list[str] = []
    if IS_WINDOWS:
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
            if base:
                candidates += [
                    os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                    os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                ]
    else:
        for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"):
            found = shutil.which(name)
            if found:
                candidates.append(found)
    for exe in candidates:
        if exe and os.path.isfile(exe):
            try:
                subprocess.Popen([exe, f"--app={url}", "--window-size=1280,800"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except OSError:
                continue
    webbrowser.open(url)


def _is_running(port: int) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # localhost nie über Proxy
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/setup", timeout=1.5) as res:
            return b"Homify" in res.read()
    except Exception:
        return False


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host if host not in ("", "0.0.0.0") else "0.0.0.0", port))
            return True
        except OSError:
            return False


def _wait_and_open(port: int) -> None:
    for _ in range(60):
        if _is_running(port):
            break
        time.sleep(0.5)
    _open_app_window(f"http://localhost:{port}/")


def cmd_run(args) -> None:
    import uvicorn

    host = args.host or config.get("host")
    port = int(args.port or config.get("port"))
    if _is_running(port):
        # Läuft schon (z. B. Autostart) -> nur das Fenster öffnen
        print(f"{APP_NAME} läuft bereits auf Port {port}.")
        if args.open:
            _open_app_window(f"http://localhost:{port}/")
        return
    for _ in range(40 if args.wait_port else 0):  # nach „Neu starten“ auf den alten Prozess warten
        if _port_free(host, port):
            break
        time.sleep(0.25)
    print(f"\n  {APP_NAME} {__version__}")
    print(f"  Daten:       {DATA_DIR}")
    print(f"  Musik:       {', '.join(config.music_dirs) or '(noch nicht eingestellt)'}")
    print(f"  Im Browser:  http://localhost:{port}\n")
    pid_file = DATA_DIR / "homify.pid"
    try:
        pid_file.write_text(str(os.getpid()), encoding="ascii")
    except OSError:
        pass
    if args.open:
        threading.Thread(target=_wait_and_open, args=(port,), daemon=True).start()
    try:
        uvicorn.run("homify.server:app", host=host, port=port, log_level="warning", proxy_headers=True,
                    forwarded_allow_ips="127.0.0.1")
    finally:
        try:
            if pid_file.read_text(encoding="ascii").strip() == str(os.getpid()):
                pid_file.unlink()
        except OSError:
            pass


def cmd_setup(args) -> None:
    from . import db
    from .media import find_ffmpeg
    from .tools import tools

    db.init()
    ff = find_ffmpeg()
    print(f"ffmpeg: {ff or 'NICHT gefunden'}")
    print("Installiere/aktualisiere spotDL (das kann ein paar Minuten dauern) …")
    tools.echo = lambda line: print("  " + line, flush=True)
    ok = tools.install(upgrade=True)
    from .tools import bridge

    bridge.stop()
    sys.exit(0 if ok else 1)


def cmd_scan(args) -> None:
    from . import db
    from .scanner import scanner

    db.init()
    result = scanner.scan(full=args.full)
    print(result)


def cmd_add_user(args) -> None:
    from . import auth, db

    db.init()
    password = args.password or getpass.getpass("Passwort: ")
    uid = auth.create_user(args.username, password, is_admin=args.admin)
    print(f"Benutzer {args.username} angelegt (id {uid}).")


def cmd_reset_password(args) -> None:
    from . import auth, db

    db.init()
    row = db.query_one("SELECT id FROM users WHERE username = ?", (args.username,))
    if not row:
        print("Benutzer nicht gefunden.")
        sys.exit(1)
    password = args.password or getpass.getpass("Neues Passwort: ")
    auth.set_password(row["id"], password)
    print("Passwort geändert.")


def main(argv: list[str] | None = None) -> None:
    # pythonw.exe (Start ohne Konsolenfenster) hat kein stdout/stderr
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    parser = argparse.ArgumentParser(prog="homify", description=f"{APP_NAME} – dein eigenes Spotify fürs Homelab")
    parser.add_argument("--verbose", "-v", action="store_true")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Server starten (Standard)")
    run.add_argument("--host")
    run.add_argument("--port", type=int)
    run.add_argument("--open", action="store_true", help="Programmfenster/Browser öffnen")
    run.add_argument("--wait-port", action="store_true", help=argparse.SUPPRESS)

    sub.add_parser("setup", help="spotDL + Deno installieren/aktualisieren")
    scan = sub.add_parser("scan", help="Bibliothek einmal scannen")
    scan.add_argument("--full", action="store_true")
    add = sub.add_parser("add-user", help="Benutzer anlegen")
    add.add_argument("username")
    add.add_argument("--password")
    add.add_argument("--admin", action="store_true")
    reset = sub.add_parser("reset-password", help="Passwort zurücksetzen")
    reset.add_argument("username")
    reset.add_argument("--password")

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    if args.command in (None, "run"):
        if args.command is None:
            args.host, args.port, args.open, args.wait_port = None, None, False, False
        cmd_run(args)
    elif args.command == "setup":
        cmd_setup(args)
    elif args.command == "scan":
        cmd_scan(args)
    elif args.command == "add-user":
        cmd_add_user(args)
    elif args.command == "reset-password":
        cmd_reset_password(args)


if __name__ == "__main__":
    main()
