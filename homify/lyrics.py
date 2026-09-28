"""Songtexte: LRC mit Zeitstempeln ([mm:ss.xx]) oder einfacher Text."""

from __future__ import annotations

import re

_TIME = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_META = re.compile(r"^\[(ar|ti|al|au|by|re|ve|length|offset|#):", re.IGNORECASE)
_OFFSET = re.compile(r"^\[offset:\s*([+-]?\d+)\]", re.IGNORECASE)


def parse_lyrics(text: str) -> dict:
    """{"synced": bool, "lines": [{"t": Sekunden | None, "text": str}]}"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return {"synced": False, "lines": []}
    offset = 0.0
    synced: list[tuple[float, str]] = []
    plain: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        m = _OFFSET.match(line)
        if m:
            offset = int(m.group(1)) / 1000.0
            continue
        if _META.match(line):
            continue
        stamps = list(_TIME.finditer(line))
        if stamps:
            content = _TIME.sub("", line).strip()
            for s in stamps:
                minutes, seconds, frac = s.group(1), s.group(2), s.group(3) or "0"
                t = int(minutes) * 60 + int(seconds) + int(frac) / (10 ** len(frac))
                synced.append((max(0.0, t - offset), content))
        else:
            plain.append(line)
    if synced:
        synced.sort(key=lambda x: x[0])
        return {"synced": True, "lines": [{"t": round(t, 2), "text": s} for t, s in synced]}
    # Leerzeilen am Stück zusammenfassen
    lines, empty = [], False
    for line in plain:
        if not line:
            if not empty and lines:
                lines.append({"t": None, "text": ""})
            empty = True
            continue
        empty = False
        lines.append({"t": None, "text": line})
    return {"synced": False, "lines": lines}
