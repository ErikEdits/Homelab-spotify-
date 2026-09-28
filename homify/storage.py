"""
Speicherorte für die Musik.

- App-Ordner (Standard): data/music – solange noch kein NAS eingestellt ist
- NAS (z. B. UGREEN) direkt per SMB: Adresse, Freigabe, Benutzer, Passwort in Homify eintragen
- Eigener Ordner: ein lokaler oder vom Betriebssystem eingebundener Pfad (z. B. /mnt/nas/musik)

Alle Speicherorte haben dieselbe Schnittstelle, der Rest von Homify arbeitet nur mit
relativen Pfaden („Künstler/Album/Song.mp3“).
"""

from __future__ import annotations

import errno
import logging
import os
import posixpath
import shutil
import threading
from typing import BinaryIO, Callable, Iterator

from .config import DATA_DIR, config

log = logging.getLogger("homify.storage")

LOCAL_MUSIC_DIR = DATA_DIR / "music"

# Typische System-/Papierkorb-Ordner auf NAS-Systemen (UGREEN, Synology, QNAP, Windows)
SKIP_DIRS = {
    "@eadir", "#recycle", "$recycle.bin", "system volume information", "#snapshot", ".snapshot",
    "lost+found", "@recycle", ".@__thumb", "@__thumb", ".trash", ".trashes", "@recently-snapshot",
    ".ugreen_nas", "@tmp", "@appstore",
}

ErrorCallback = Callable[[str], None]


class StorageError(RuntimeError):
    pass


def norm_rel(rel: str) -> str:
    rel = rel.replace("\\", "/").strip("/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise StorageError("Ungültiger Pfad")
    return "/".join(parts)


class Storage:
    key: str = ""
    label: str = ""
    kind: str = ""

    def available(self) -> tuple[bool, str]:
        raise NotImplementedError

    def walk(self, on_error: ErrorCallback | None = None) -> Iterator[tuple[str, int, float]]:
        raise NotImplementedError

    def open(self, rel: str) -> BinaryIO:
        raise NotImplementedError

    def stat(self, rel: str) -> tuple[int, float] | None:
        raise NotImplementedError

    def dir_files(self, rel_dir: str) -> list[tuple[str, int]]:
        raise NotImplementedError

    def local_path(self, rel: str) -> str | None:
        return None

    def display_path(self, rel: str) -> str:
        raise NotImplementedError

    def put(self, local_file: str, rel: str, move: bool = False) -> None:
        raise NotImplementedError

    def remove(self, rel: str) -> None:
        raise NotImplementedError

    def remove_empty_dirs(self) -> None:
        pass

    def exists(self, rel: str) -> bool:
        return self.stat(rel) is not None

    def free_space(self) -> int | None:
        return None


# --------------------------------------------------------------------------- #
# Lokaler Ordner (App-Ordner oder eigener Pfad)
# --------------------------------------------------------------------------- #

class LocalStorage(Storage):
    kind = "local"

    def __init__(self, root: str | os.PathLike, label: str | None = None):
        self.root = os.path.abspath(str(root))
        self.key = "local:" + self.root
        self.label = label or self.root

    def _abs(self, rel: str) -> str:
        return os.path.join(self.root, *norm_rel(rel).split("/")) if rel else self.root

    def available(self) -> tuple[bool, str]:
        if not os.path.isdir(self.root):
            return False, f"Ordner nicht gefunden: {self.root}"
        try:
            os.listdir(self.root)
        except OSError as exc:
            return False, f"Ordner nicht lesbar: {exc}"
        return True, "OK"

    def walk(self, on_error: ErrorCallback | None = None) -> Iterator[tuple[str, int, float]]:
        stack = [self.root]
        linked: set[str] = set()  # Schutz vor Endlosschleifen durch Symlinks
        while stack:
            directory = stack.pop()
            try:
                it = os.scandir(directory)
            except OSError as exc:
                if on_error:
                    on_error(f"{directory}: {exc}")
                continue
            with it:
                for entry in it:
                    name = entry.name
                    if name.startswith("."):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=True):
                            if name.lower() in SKIP_DIRS:
                                continue
                            if entry.is_symlink():
                                real = os.path.realpath(entry.path)
                                if real in linked or os.path.realpath(directory).startswith(real):
                                    continue
                                linked.add(real)
                            stack.append(entry.path)
                        elif entry.is_file():
                            s = entry.stat()
                            rel = os.path.relpath(entry.path, self.root).replace(os.sep, "/")
                            yield rel, s.st_size, s.st_mtime
                    except OSError as exc:
                        if on_error:
                            on_error(f"{entry.path}: {exc}")

    def open(self, rel: str) -> BinaryIO:
        return open(self._abs(rel), "rb")

    def stat(self, rel: str) -> tuple[int, float] | None:
        try:
            s = os.stat(self._abs(rel))
            return s.st_size, s.st_mtime
        except OSError:
            return None

    def dir_files(self, rel_dir: str) -> list[tuple[str, int]]:
        out = []
        try:
            for e in os.scandir(self._abs(rel_dir)):
                if e.is_file():
                    out.append((posixpath.join(norm_rel(rel_dir), e.name) if rel_dir else e.name, e.stat().st_size))
        except OSError:
            pass
        return out

    def local_path(self, rel: str) -> str | None:
        return self._abs(rel)

    def display_path(self, rel: str) -> str:
        return self._abs(rel)

    def put(self, local_file: str, rel: str, move: bool = False) -> None:
        dest = self._abs(rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if move:
            shutil.move(local_file, dest)
        else:
            tmp = dest + ".part"
            shutil.copyfile(local_file, tmp)
            os.replace(tmp, dest)

    def remove(self, rel: str) -> None:
        os.remove(self._abs(rel))

    def remove_empty_dirs(self) -> None:
        for dirpath, dirnames, filenames in os.walk(self.root, topdown=False):
            if dirpath != self.root and not os.listdir(dirpath):
                try:
                    os.rmdir(dirpath)
                except OSError:
                    pass

    def free_space(self) -> int | None:
        try:
            return shutil.disk_usage(self.root).free
        except OSError:
            return None


# --------------------------------------------------------------------------- #
# NAS per SMB (UGREEN, Synology, QNAP, Windows-Freigaben …)
# --------------------------------------------------------------------------- #

_smb_lock = threading.Lock()
_smb_sessions: dict[tuple[str, str], str] = {}
_FILE_ERRNOS = {errno.ENOENT, errno.EEXIST, errno.EACCES, errno.EPERM, errno.ENOTDIR, errno.EISDIR, errno.ENOTEMPTY}


class SmbStorage(Storage):
    kind = "smb"

    def __init__(self, host: str, share: str, folder: str = "", username: str = "", password: str = "",
                 port: int = 445):
        self.host = host.strip().strip("\\/").removeprefix("smb://")
        self.share = share.strip().strip("\\/")
        self.folder = norm_rel(folder or "")
        self.username = username
        self.password = password
        self.port = port
        base = f"\\\\{self.host}\\{self.share}"
        self.base = base + ("\\" + self.folder.replace("/", "\\") if self.folder else "")
        self.key = f"smb://{self.host}/{self.share}/{self.folder}".rstrip("/")
        self.label = self.base

    # ------------------------------------------------------------ Verbindung
    def _ensure_session(self, force: bool = False) -> None:
        import smbclient  # erst hier importieren – nur nötig, wenn ein NAS genutzt wird

        with _smb_lock:
            sess_key = (self.host, self.username)
            if not force and _smb_sessions.get(sess_key) == self.password:
                return
            if force:
                try:
                    smbclient.reset_connection_cache()
                except Exception:
                    pass
                _smb_sessions.clear()
            smbclient.register_session(
                self.host, username=self.username or None, password=self.password or None,
                port=self.port, connection_timeout=10,
            )
            _smb_sessions[sess_key] = self.password

    def _call(self, fn, *args, **kwargs):
        """SMB-Aufruf mit einem automatischen Neuverbinden (z. B. nach NAS-Standby)."""
        self._ensure_session()
        try:
            return fn(*args, **kwargs)
        except OSError as exc:
            if getattr(exc, "errno", None) in _FILE_ERRNOS:
                raise  # Datei fehlt / keine Rechte – kein Verbindungsproblem
            log.info("SMB-Verbindung wird neu aufgebaut (%s)", exc)
        except Exception as exc:
            log.info("SMB-Verbindung wird neu aufgebaut (%s)", exc)
        self._ensure_session(force=True)
        return fn(*args, **kwargs)

    def _path(self, rel: str) -> str:
        rel = norm_rel(rel)
        return self.base + ("\\" + rel.replace("/", "\\") if rel else "")

    def available(self) -> tuple[bool, str]:
        try:
            import smbclient
        except ImportError:
            return False, "Python-Paket smbprotocol fehlt (pip install smbprotocol)"
        try:
            self._ensure_session(force=True)
        except Exception as exc:
            return False, _friendly_smb_error(exc, self)
        try:
            if not smbclient.path.isdir(self.base):
                return False, f"Ordner „{self.folder or self.share}“ gibt es auf dem NAS nicht."
            smbclient.listdir(self.base)
        except Exception as exc:
            return False, _friendly_smb_error(exc, self)
        return True, "Verbunden"

    def ensure_base(self) -> None:
        import smbclient

        self._call(smbclient.makedirs, self.base, exist_ok=True)

    def check_writable(self) -> tuple[bool, str]:
        import smbclient

        probe = self._path(".homify-schreibtest")
        try:
            self._call(smbclient.makedirs, self.base, exist_ok=True)
            with self._call(smbclient.open_file, probe, mode="wb") as f:
                f.write(b"ok")
            self._call(smbclient.remove, probe)
            return True, "Schreiben möglich"
        except Exception as exc:
            return False, _friendly_smb_error(exc, self)

    # ------------------------------------------------------------ Dateien
    def walk(self, on_error: ErrorCallback | None = None) -> Iterator[tuple[str, int, float]]:
        import smbclient

        stack = [""]
        while stack:
            rel_dir = stack.pop()
            try:
                entries = list(self._call(smbclient.scandir, self._path(rel_dir)))
            except Exception as exc:
                if on_error:
                    on_error(f"{self._path(rel_dir)}: {exc}")
                continue
            for entry in entries:
                name = entry.name
                if name.startswith(".") or name in (".", ".."):
                    continue
                rel = f"{rel_dir}/{name}" if rel_dir else name
                try:
                    if entry.is_dir():
                        if name.lower() not in SKIP_DIRS:
                            stack.append(rel)
                    elif entry.is_file():
                        s = entry.stat()
                        yield rel, s.st_size, s.st_mtime
                except Exception as exc:
                    if on_error:
                        on_error(f"{rel}: {exc}")

    def open(self, rel: str) -> BinaryIO:
        import smbclient

        return self._call(smbclient.open_file, self._path(rel), mode="rb", buffering=256 * 1024,
                          share_access="r")

    def stat(self, rel: str) -> tuple[int, float] | None:
        import smbclient

        try:
            s = self._call(smbclient.stat, self._path(rel))
            return s.st_size, s.st_mtime
        except Exception:
            return None

    def dir_files(self, rel_dir: str) -> list[tuple[str, int]]:
        import smbclient

        out = []
        try:
            for e in self._call(smbclient.scandir, self._path(rel_dir)):
                if e.is_file():
                    out.append((f"{norm_rel(rel_dir)}/{e.name}" if rel_dir else e.name, e.stat().st_size))
        except Exception:
            pass
        return out

    def display_path(self, rel: str) -> str:
        return self._path(rel)

    def put(self, local_file: str, rel: str, move: bool = False) -> None:
        import smbclient

        dest = self._path(rel)
        parent = dest.rsplit("\\", 1)[0]
        self._call(smbclient.makedirs, parent, exist_ok=True)
        tmp = dest + ".part"
        with open(local_file, "rb") as src, self._call(smbclient.open_file, tmp, mode="wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        try:
            self._call(smbclient.replace, tmp, dest)
        except AttributeError:  # sehr alte smbprotocol-Versionen
            if self.exists(rel):
                self._call(smbclient.remove, dest)
            self._call(smbclient.rename, tmp, dest)
        if move:
            os.remove(local_file)

    def remove(self, rel: str) -> None:
        import smbclient

        self._call(smbclient.remove, self._path(rel))

    def download(self, rel: str, local_file: str) -> None:
        with self.open(rel) as src, open(local_file, "wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)


def _friendly_smb_error(exc: Exception, st: SmbStorage) -> str:
    text = str(exc)
    low = text.lower()
    if "failed to connect" in low or "timed out" in low or "connection refused" in low or "getaddrinfo" in low:
        return f"NAS {st.host} nicht erreichbar. Adresse prüfen und ob SMB auf dem NAS aktiviert ist."
    if "logon" in low or "authentication" in low or "access_denied" in low or "0xc000006d" in low:
        return "Anmeldung am NAS fehlgeschlagen – Benutzername oder Passwort falsch."
    if "bad_network_name" in low or "0xc00000cc" in low:
        return f"Freigabe „{st.share}“ gibt es auf dem NAS nicht. Namen der freigegebenen Ordner prüfen."
    if "access is denied" in low or "0xc0000022" in low or isinstance(exc, PermissionError):
        return "Keine Berechtigung für diesen Ordner auf dem NAS."
    return text[:300]


# --------------------------------------------------------------------------- #
# Aus der Konfiguration
# --------------------------------------------------------------------------- #

def storage_from_settings(values: dict) -> Storage:
    mode = values.get("storage_mode") or "local"
    if mode == "nas":
        return SmbStorage(values.get("nas_host", ""), values.get("nas_share", ""), values.get("nas_folder", ""),
                          values.get("nas_user", ""), values.get("nas_password", ""))
    if mode == "folder" and (values.get("storage_path") or "").strip():
        return LocalStorage(values["storage_path"].strip())
    LOCAL_MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    return LocalStorage(LOCAL_MUSIC_DIR, label=f"App-Ordner ({LOCAL_MUSIC_DIR})")


_cache: dict[str, Storage] = {}
_cache_lock = threading.Lock()


def primary() -> Storage:
    """Hauptspeicherort: hier landen Downloads, von hier wird gestreamt."""
    st = storage_from_settings({k: config.get(k) for k in (
        "storage_mode", "storage_path", "nas_host", "nas_share", "nas_folder", "nas_user", "nas_password")})
    with _cache_lock:
        cached = _cache.get(st.key)
        if cached is None or getattr(cached, "password", None) != getattr(st, "password", None):
            _cache[st.key] = st
            cached = st
    return cached


def extra() -> list[Storage]:
    """Zusätzliche Ordner, die nur gelesen werden (z. B. alte Sammlung)."""
    out = []
    for d in config.music_dirs:
        st = LocalStorage(d)
        with _cache_lock:
            out.append(_cache.setdefault(st.key, st))
    return out


def all_storages() -> list[Storage]:
    seen, out = set(), []
    for st in [primary(), *extra()]:
        if st.key not in seen:
            seen.add(st.key)
            out.append(st)
    return out


def by_key(key: str) -> Storage | None:
    for st in all_storages():
        if st.key == key:
            return st
    with _cache_lock:
        return _cache.get(key)
