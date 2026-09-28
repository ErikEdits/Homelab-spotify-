"""Einstellungen pro Benutzer (Wiedergabe, Aussehen, Startseite …) – auf allen Geräten gleich."""

from __future__ import annotations

import json
from typing import Any

from . import db
from .settings_schema import BY_KEY, USER_SETTINGS, user_defaults, validate


def get_all(user_id: int) -> dict[str, Any]:
    values = user_defaults()
    for row in db.query("SELECT key, value FROM user_settings WHERE user_id = ?", (user_id,)):
        setting = BY_KEY.get(row["key"])
        if setting is None or setting.scope != "user":
            continue
        try:
            values[row["key"]] = validate(setting, json.loads(row["value"]))
        except (ValueError, TypeError):
            pass
    return values


def get(user_id: int, key: str) -> Any:
    setting = BY_KEY[key]
    row = db.query_one("SELECT value FROM user_settings WHERE user_id = ? AND key = ?", (user_id, key))
    if not row:
        return setting.default
    try:
        return validate(setting, json.loads(row["value"]))
    except (ValueError, TypeError):
        return setting.default


def update(user_id: int, values: dict[str, Any]) -> dict[str, Any]:
    """Werte speichern; None setzt auf Standard zurück. Unbekannte Schlüssel -> ValueError."""
    cleaned: dict[str, Any] = {}
    for key, value in values.items():
        setting = BY_KEY.get(key)
        if setting is None or setting.scope != "user":
            raise ValueError(f"Unbekannte Einstellung: {key}")
        cleaned[key] = None if value is None else validate(setting, value)
    with db.transaction() as c:
        for key, value in cleaned.items():
            if value is None or value == BY_KEY[key].default:
                c.execute("DELETE FROM user_settings WHERE user_id = ? AND key = ?", (user_id, key))
            else:
                c.execute(
                    "INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?) "
                    "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
                    (user_id, key, json.dumps(value)),
                )
    return get_all(user_id)


def reset_all(user_id: int) -> dict[str, Any]:
    db.execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))
    return get_all(user_id)


def user_keys() -> list[str]:
    return [s.key for s in USER_SETTINGS]
