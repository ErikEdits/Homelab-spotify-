"""Benutzer, Passwörter (PBKDF2) und Sitzungen per Cookie."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request

from . import db

COOKIE = "homify_session"


def session_days() -> int:
    from .config import config

    return int(config.get("session_days") or 180)
_ITERATIONS = 240_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_user(username: str, password: str, is_admin: bool = False, can_download: bool | None = None) -> int:
    if can_download is None:
        from .config import config

        can_download = bool(config.get("new_users_can_download"))
    username = username.strip()
    if not username or len(username) > 64:
        raise ValueError("Ungültiger Benutzername")
    if len(password) < 4:
        raise ValueError("Passwort muss mindestens 4 Zeichen haben")
    cur = db.execute(
        "INSERT INTO users (username, pw_hash, is_admin, can_download, created_at) VALUES (?, ?, ?, ?, ?)",
        (username, hash_password(password), int(is_admin), int(can_download), time.time()),
    )
    return int(cur.lastrowid)


def set_password(user_id: int, password: str) -> None:
    if len(password) < 4:
        raise ValueError("Passwort muss mindestens 4 Zeichen haben")
    db.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (hash_password(password), user_id))
    db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def user_count() -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM users")
    return int(row["n"]) if row else 0


def authenticate(username: str, password: str) -> dict | None:
    user = db.query_one("SELECT * FROM users WHERE username = ?", (username.strip(),))
    if user and verify_password(password, user["pw_hash"]):
        return user
    # Zeitverhalten angleichen, damit Benutzernamen nicht erraten werden können
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_hex(8))
    verify_password(password, _dummy_hash)
    return None


_dummy_hash: str | None = None


def create_session(user_id: int, user_agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    db.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, last_seen, user_agent) VALUES (?, ?, ?, ?, ?)",
        (_token_hash(token), user_id, now, now, user_agent[:200]),
    )
    return token


def delete_session(token: str) -> None:
    db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def user_for_token(token: str | None) -> dict | None:
    if not token:
        return None
    row = db.query_one(
        "SELECT u.id, u.username, u.is_admin, u.can_download, s.last_seen, s.token_hash "
        "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
        (_token_hash(token),),
    )
    if not row:
        return None
    now = time.time()
    if now - row["last_seen"] > session_days() * 86400:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (row["token_hash"],))
        return None
    if now - row["last_seen"] > 3600:
        db.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, row["token_hash"]))
    return {
        "id": row["id"],
        "username": row["username"],
        "is_admin": bool(row["is_admin"]),
        "can_download": bool(row["can_download"] or row["is_admin"]),
    }


# --------------------------------------------------------------------------- #
# Schutz gegen Passwort-Raten: max. 8 Fehlversuche pro IP in 5 Minuten
# --------------------------------------------------------------------------- #
_failures: dict[str, deque] = defaultdict(deque)
_fail_lock = threading.Lock()


def check_rate_limit(ip: str) -> None:
    with _fail_lock:
        q = _failures[ip]
        while q and time.time() - q[0] > 300:
            q.popleft()
        if len(q) >= 8:
            raise HTTPException(429, "Zu viele Fehlversuche – bitte ein paar Minuten warten.")


def record_failure(ip: str) -> None:
    with _fail_lock:
        _failures[ip].append(time.time())


# --------------------------------------------------------------------------- #
# FastAPI-Abhängigkeiten
# --------------------------------------------------------------------------- #

def current_user(request: Request) -> dict:
    user = user_for_token(request.cookies.get(COOKIE))
    if user is None:
        raise HTTPException(401, "Nicht angemeldet")
    return user


def admin_user(user: dict = Depends(current_user)) -> dict:
    if not user["is_admin"]:
        raise HTTPException(403, "Nur für Admins")
    return user


def download_user(user: dict = Depends(current_user)) -> dict:
    if not user["can_download"]:
        raise HTTPException(403, "Keine Berechtigung zum Herunterladen")
    return user
