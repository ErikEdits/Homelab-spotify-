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


_BRACKETS = re.compile(r"\s*[\(\[]([^\)\]]*)[\)\]]")
_DASH_EXTRA = re.compile(r"\s+-\s+(.+)$")
_FEAT_START = re.compile(r"^(feat\.?|ft\.?|featuring|with|mit)\s", re.IGNORECASE)
# Zusätze, die eine andere Aufnahme bezeichnen -> bleibt ein eigener Song
_OTHER_VERSION = re.compile(
    r"remix|\bmix\b|live|acoustic|akustik|unplugged|instrumental|\bedit\b|demo|karaoke|a ?cappella|reprise|"
    r"sped up|slowed|nightcore|extended|\bdub\b|\bclub\b|\bradio\b|cover",
    re.IGNORECASE,
)
# Zusätze ohne Bedeutung für den Vergleich („Remastered 2011“, „Album Version“ …)
_NOISE = re.compile(
    r"remaster|album version|single version|original (mix|version)|explicit|\bclean\b|\bmono\b|\bstereo\b|"
    r"bonus track|deluxe|anniversary",
    re.IGNORECASE,
)
_ARTIST_SPLIT = re.compile(r"\s*(?:,|;|/|\s&\s|\sx\s|\sund\s|\sand\s|\s(?:feat\.?|ft\.?|featuring)\s)\s*")


def _unimportant(extra: str) -> bool:
    extra = extra.strip()
    if not extra or _FEAT_START.match(extra) or re.search(r"original (mix|version)", extra, re.I):
        return True
    if _OTHER_VERSION.search(extra):
        return False
    return bool(_NOISE.search(extra))


def dup_key(title: str | None, artist: str | None) -> str:
    """Schlüssel für „derselbe Song“: Titel + Hauptinterpret, ohne Remaster-/feat.-Zusätze.
    „Song (Remix)“ oder „Song - Live“ bleiben eigene Songs."""
    t = norm(title)
    if not t:
        return ""
    t = _BRACKETS.sub(lambda m: "" if _unimportant(m.group(1)) else " " + m.group(1), t)
    m = _DASH_EXTRA.search(t)
    if m and _unimportant(m.group(1)):
        t = t[:m.start()]
    t = _FEAT.sub("", t)
    a = _ARTIST_SPLIT.split(norm(artist))[0]
    return _NON_WORD.sub("", t) + "|" + _NON_WORD.sub("", a)


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
