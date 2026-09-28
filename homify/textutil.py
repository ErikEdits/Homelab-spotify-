"""Hilfsfunktionen für Texte und IDs."""

from __future__ import annotations

import hashlib
import re
import unicodedata

_PAREN = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_FEAT = re.compile(r"\s+(feat\.?|ft\.?|featuring)\s+.*$", re.IGNORECASE)
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


def norm(text: str | None) -> str:
    """Kleinschreibung, ohne Akzente, zusammengefasste Leerzeichen – für Suche/Vergleiche."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.casefold().split())


def match_key(text: str | None) -> str:
    """Aggressive Normalisierung für den Abgleich Spotify <-> Bibliothek."""
    text = norm(text)
    text = _PAREN.sub("", text)
    text = _FEAT.sub("", text)
    text = re.sub(r"\s+-\s+.*(remaster|version|edit|mix).*$", "", text)
    return _NON_WORD.sub("", text)


def make_id(*parts: str, length: int = 16) -> str:
    raw = "\x1f".join(norm(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:length]


def path_id(relpath: str) -> str:
    return hashlib.sha1(relpath.replace("\\", "/").encode("utf-8")).hexdigest()[:16]


def parse_int(value) -> int:
    if value is None:
        return 0
    if isinstance(value, (tuple, list)):
        value = value[0] if value else 0
    if isinstance(value, int):
        return value
    m = re.match(r"\s*(\d+)", str(value))
    return int(m.group(1)) if m else 0


def parse_year(value) -> int:
    if value is None:
        return 0
    m = re.search(r"(\d{4})", str(value))
    return int(m.group(1)) if m else 0
