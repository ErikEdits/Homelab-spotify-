"""Persönlicher Feed: Empfehlungen wie bei Spotify – lokal aus dem eigenen Hörverhalten berechnet.

Geschmacksprofil pro Benutzer aus
- Plays (neuere zählen mehr, Halbwertszeit 30 Tage),
- Lieblingssongs und Songs in eigenen Playlists,
- Überspringen in den ersten Sekunden (negatives Signal),
- Songs, die oft in derselben Hörsitzung laufen (auch bei den anderen Homify-Benutzern),
- dem, was die anderen Homify-Benutzer gerade hören.

Daraus entstehen Daily Mixes, „Mix der Woche“, „Neu für dich“, „Auf Dauerschleife“, „Zeitreise“,
ein Mix für die Tageszeit, „Beliebt bei Homify“, „Weil du … gehört hast“ und die Top-Genres.
Mixe sind pro Tag (bzw. Woche) stabil und ändern sich nicht bei jedem Neuladen.
Keine Daten verlassen den Server.
"""

from __future__ import annotations

import datetime as _dt
import math
import random
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from . import db
from .covers import colors_for

DAY = 86400
HALF_LIFE = 30 * DAY
SESSION_GAP = 30 * 60
MIX_SIZE = 50

_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, ttl: float, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = fn()
    with _lock:
        _cache[key] = (now, value)
        if len(_cache) > 200:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:50]:
                _cache.pop(k, None)
    return value


def invalidate(*_args) -> None:
    with _lock:
        _cache.clear()


def _chunks(items: list, size: int = 500):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _rng(user_id: int, kind: str, weekly: bool = False) -> random.Random:
    today = _dt.date.today()
    period = f"{today.isocalendar()[0]}-W{today.isocalendar()[1]}" if weekly else today.isoformat()
    return random.Random(f"{user_id}|{kind}|{period}")


# --------------------------------------------------------------------------- #
# Katalog (alle Songs mit Künstlern/Genre/Jahr) – wird bei Änderungen neu gebaut
# --------------------------------------------------------------------------- #

@dataclass
class Catalog:
    tracks: dict[str, dict[str, Any]]
    by_artist: dict[str, list[str]]
    by_genre: dict[str, list[str]]
    artist_names: dict[str, str]
    artist_genre: dict[str, str]


def _catalog_sig() -> str:
    sig = db.query_one("SELECT COUNT(*) AS n, COALESCE(MAX(added_at), 0) AS a, COALESCE(SUM(mtime), 0) AS m FROM tracks")
    return f"catalog|{sig['n']}|{sig['a']}|{sig['m']}"


def _catalog_key() -> str:
    # kurz merken: die Startseite fragt ihn für jeden Mix; neue Scans leeren den Cache ohnehin
    return _cached("catalog-key", 5, _catalog_sig)


def catalog() -> Catalog:
    return _cached(_catalog_key(), 3600, _build_catalog)


def _build_catalog() -> Catalog:
    tracks: dict[str, dict[str, Any]] = {}
    for r in db.query("SELECT id, genre, year, added_at, album_id, cover_id FROM tracks"):
        tracks[r["id"]] = {"id": r["id"], "genre": (r["genre"] or "").strip().lower(), "genre_name": r["genre"] or "",
                           "year": r["year"] or 0, "added_at": r["added_at"] or 0, "album_id": r["album_id"],
                           "cover": r["cover_id"], "artists": []}
    by_artist: dict[str, list[str]] = defaultdict(list)
    names: dict[str, str] = {}
    for r in db.query("SELECT track_id, artist_id, name FROM track_artists ORDER BY position"):
        t = tracks.get(r["track_id"])
        if t is None:
            continue
        t["artists"].append(r["artist_id"])
        by_artist[r["artist_id"]].append(r["track_id"])
        names.setdefault(r["artist_id"], r["name"])
    by_genre: dict[str, list[str]] = defaultdict(list)
    for t in tracks.values():
        if t["genre"]:
            by_genre[t["genre"]].append(t["id"])
    artist_genre = {}
    for aid, tids in by_artist.items():
        genres = Counter(tracks[t]["genre"] for t in tids if tracks[t]["genre"])
        artist_genre[aid] = genres.most_common(1)[0][0] if genres else ""
    return Catalog(tracks, dict(by_artist), dict(by_genre), names, artist_genre)


# --------------------------------------------------------------------------- #
# Geschmacksprofil
# --------------------------------------------------------------------------- #

@dataclass
class Profile:
    track: dict[str, float] = field(default_factory=dict)      # wie gern (positiv) / übersprungen (negativ)
    artist: dict[str, float] = field(default_factory=dict)
    genre: dict[str, float] = field(default_factory=dict)
    decade: dict[int, float] = field(default_factory=dict)
    played: set[str] = field(default_factory=set)               # je gehört
    recent: dict[str, float] = field(default_factory=dict)      # Plays der letzten 30 Tage (Anzahl)
    last_played: dict[str, float] = field(default_factory=dict)
    skipped: Counter = field(default_factory=Counter)            # Skips der letzten 90 Tage
    plays: int = 0

    @property
    def empty(self) -> bool:
        return not self.track


def _profile_key(user_id: int) -> str:
    """Ändert sich, sobald der Benutzer etwas hört, liked, überspringt oder in Playlists legt."""
    sig = db.query_one("SELECT COUNT(*) AS n, COALESCE(MAX(played_at), 0) AS t FROM plays WHERE user_id = ?", (user_id,))
    likes = db.query_one("SELECT COUNT(*) AS n, COALESCE(MAX(liked_at), 0) AS t FROM likes WHERE user_id = ?", (user_id,))
    skips = db.query_one("SELECT COUNT(*) AS n FROM skips WHERE user_id = ?", (user_id,))
    lists = db.query_one("SELECT COUNT(*) AS n, COALESCE(MAX(pt.added_at), 0) AS t FROM playlist_tracks pt "
                         "JOIN playlists pl ON pl.id = pt.playlist_id WHERE pl.user_id = ?", (user_id,))
    return (f"profile|{user_id}|{sig['n']}|{sig['t']}|{likes['n']}|{likes['t']}|{skips['n']}|{lists['n']}|{lists['t']}"
            f"|{_dt.date.today()}")


def profile(user_id: int) -> Profile:
    return _cached(_profile_key(user_id), 600, lambda: _build_profile(user_id))


def _build_profile(user_id: int) -> Profile:
    now = time.time()
    cat = catalog()
    p = Profile()
    score: dict[str, float] = defaultdict(float)
    for r in db.query("SELECT track_id, played_at FROM plays WHERE user_id = ? AND played_at > ?", (user_id, now - 730 * DAY)):
        tid = r["track_id"]
        if tid not in cat.tracks:
            continue
        score[tid] += 0.5 ** ((now - r["played_at"]) / HALF_LIFE)
        p.played.add(tid)
        p.plays += 1
        p.last_played[tid] = max(p.last_played.get(tid, 0), r["played_at"])
        if now - r["played_at"] < 30 * DAY:
            p.recent[tid] = p.recent.get(tid, 0) + 1
    for r in db.query("SELECT track_id FROM likes WHERE user_id = ?", (user_id,)):
        if r["track_id"] in cat.tracks:
            score[r["track_id"]] += 2.5
    for r in db.query("SELECT pt.track_id FROM playlist_tracks pt JOIN playlists pl ON pl.id = pt.playlist_id "
                      "WHERE pl.user_id = ?", (user_id,)):
        if r["track_id"] in cat.tracks:
            score[r["track_id"]] += 0.8
    for r in db.query("SELECT track_id, COUNT(*) AS n FROM skips WHERE user_id = ? AND skipped_at > ? GROUP BY track_id",
                      (user_id, now - 90 * DAY)):
        p.skipped[r["track_id"]] = r["n"]
        score[r["track_id"]] -= 1.2 * r["n"]
    p.track = dict(score)
    artist: dict[str, float] = defaultdict(float)
    genre: dict[str, float] = defaultdict(float)
    decade: dict[int, float] = defaultdict(float)
    for tid, s in score.items():
        t = cat.tracks[tid]
        for aid in t["artists"]:
            artist[aid] += s
        if t["genre"]:
            genre[t["genre"]] += s
        if t["year"]:
            decade[t["year"] // 10 * 10] += s
    p.artist = {k: v for k, v in artist.items() if v > 0}
    p.genre = {k: v for k, v in genre.items() if v > 0}
    p.decade = {k: v for k, v in decade.items() if v > 0}
    return p


def _norm(d: dict) -> dict:
    top = max(d.values(), default=0)
    return {k: v / top for k, v in d.items()} if top > 0 else {}


# --------------------------------------------------------------------------- #
# Gemeinsames Hören: Songs/Künstler, die oft in derselben Sitzung laufen (alle Benutzer)
# --------------------------------------------------------------------------- #

def cooccurrence() -> tuple[dict[str, Counter], dict[str, Counter]]:
    sig = db.query_one("SELECT COUNT(*) AS n, COALESCE(MAX(played_at), 0) AS t FROM plays")
    return _cached(f"cooc|{sig['n']}|{sig['t'] // 600}", 600, _build_cooccurrence)


def _build_cooccurrence() -> tuple[dict[str, Counter], dict[str, Counter]]:
    cat = catalog()
    tracks: dict[str, Counter] = defaultdict(Counter)
    artists: dict[str, Counter] = defaultdict(Counter)
    rows = db.query("SELECT user_id, track_id, played_at FROM plays WHERE played_at > ? ORDER BY played_at DESC LIMIT 100000",
                    (time.time() - 365 * DAY,))
    rows.sort(key=lambda r: (r["user_id"], r["played_at"]))
    window: list[tuple[str, float]] = []
    last_user = None
    for r in rows:
        if r["user_id"] != last_user:
            window, last_user = [], r["user_id"]
        window = [(t, at) for t, at in window if r["played_at"] - at < SESSION_GAP][-4:]
        tid = r["track_id"]
        if tid not in cat.tracks:
            continue
        for other, _at in window:
            if other == tid:
                continue
            tracks[tid][other] += 1
            tracks[other][tid] += 1
            for a in cat.tracks[tid]["artists"]:
                for b in cat.tracks.get(other, {}).get("artists", []):
                    if a != b:
                        artists[a][b] += 1
                        artists[b][a] += 1
        window.append((tid, r["played_at"]))
    return dict(tracks), dict(artists)


def _popular_with_others(user_id: int, days: int = 30) -> Counter:
    rows = db.query("SELECT track_id, COUNT(*) AS n FROM plays WHERE user_id != ? AND played_at > ? GROUP BY track_id",
                    (user_id, time.time() - days * DAY))
    return Counter({r["track_id"]: r["n"] for r in rows})


# --------------------------------------------------------------------------- #
# Hilfen
# --------------------------------------------------------------------------- #

def _spread(ids: list[str], cat: Catalog) -> list[str]:
    """Nicht zweimal derselbe Künstler direkt hintereinander."""
    out = list(ids)
    first = lambda t: (cat.tracks.get(t, {}).get("artists") or [""])[0]  # noqa: E731
    for i in range(1, len(out)):
        if first(out[i]) != first(out[i - 1]):
            continue
        for j in range(i + 1, len(out)):
            if first(out[j]) != first(out[i - 1]):
                out[i], out[j] = out[j], out[i]
                break
    return out


def _max_per_artist(ids: list[str], cat: Catalog, limit: int) -> list[str]:
    seen: Counter = Counter()
    out = []
    for t in ids:
        a = (cat.tracks[t]["artists"] or [""])[0]
        if seen[a] < limit:
            out.append(t)
            seen[a] += 1
    return out


def _weighted_sample(pool: list[tuple[str, float]], k: int, rng: random.Random) -> list[str]:
    """k Songs ziehen – höhere Werte werden bevorzugt, aber nicht immer dieselben (Efraimidis–Spirakis)."""
    keyed = [(math.log(max(rng.random(), 1e-12)) / max(w, 1e-6), t) for t, w in pool if w > 0]
    keyed.sort(reverse=True)
    return [t for _k, t in keyed[:k]]


def _names(aids: list[str], cat: Catalog) -> str:
    names = [cat.artist_names.get(a, "") for a in aids if cat.artist_names.get(a)]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} und {names[-1]}"


def _card(kind: str, value: str, name: str, subtitle: str, ids: list[str], cat: Catalog) -> dict[str, Any]:
    covers = list(dict.fromkeys(cat.tracks[t]["cover"] for t in ids if cat.tracks[t]["cover"]))[:4]
    color = colors_for(covers[:1]).get(covers[0], "#535353") if covers else "#535353"
    return {"id": f"{kind}:{value}", "name": name, "subtitle": subtitle, "covers": covers,
            "track_count": len(ids), "color": color, "feed": True}


# --------------------------------------------------------------------------- #
# Die einzelnen Mixe (liefern Song-IDs + Name + Untertitel)
# --------------------------------------------------------------------------- #

def _clusters(p: Profile, cat: Catalog) -> list[list[str]]:
    """Geschmacks-Gruppen für die Daily Mixes: Lieblingskünstler nach Genre gebündelt."""
    top = sorted(p.artist, key=p.artist.get, reverse=True)[:40]
    groups: dict[str, list[str]] = defaultdict(list)
    for aid in top:
        groups[cat.artist_genre.get(aid) or f"artist:{aid}"].append(aid)
    weight = lambda g: sum(p.artist[a] for a in g)  # noqa: E731
    ranked = sorted(groups.values(), key=weight, reverse=True)
    if len(ranked) < 3:  # wenig Genres -> Lieblingskünstler einzeln
        ranked = [[a] for a in top[:6]]
    if not ranked:
        return []
    # nur Richtungen, die wirklich zum Geschmack gehören (nicht jeder einmal gehörte Song wird ein eigener Mix)
    best = weight(ranked[0])
    return [g for g in ranked if weight(g) >= best * 0.12 and sum(len(cat.by_artist.get(a, [])) for a in g) >= 3][:6]


def daily_mix(user_id: int, index: int, limit: int = MIX_SIZE) -> tuple[list[str], str, str]:
    p, cat = profile(user_id), catalog()
    clusters = _clusters(p, cat)
    if not 1 <= index <= len(clusters):
        return [], f"Daily Mix {index}", ""
    group = clusters[index - 1]
    rng = _rng(user_id, f"daily{index}")
    genres = {cat.artist_genre.get(a) for a in group} - {""}
    _cooc_t, cooc_a = cooccurrence()
    related: Counter = Counter()
    for a in group:
        related.update(cooc_a.get(a, {}))
    related_artists = {a for a, _ in related.most_common(8)} - set(group)
    own = [t for a in group for t in cat.by_artist.get(a, [])]
    own = list(dict.fromkeys(t for t in own if p.skipped[t] < 2))
    familiar = [(t, 1.0 + max(p.track.get(t, 0), 0)) for t in own if t in p.played or p.track.get(t, 0) > 0]
    known = {t for t, _ in familiar}
    fresh = [(t, 1.0) for t in own if t not in known]
    own_set = set(own)
    similar = [(t, 0.5 + p.genre.get(cat.tracks[t]["genre"], 0)) for g in genres for t in cat.by_genre.get(g, [])
               if t not in own_set and p.skipped[t] < 2]
    related_tracks = [(t, 1.0) for a in related_artists for t in cat.by_artist.get(a, [])
                      if t not in own_set and p.skipped[t] < 2]
    ids = (_weighted_sample(familiar, int(limit * 0.5), rng) + _weighted_sample(fresh, int(limit * 0.25), rng))
    ids += _weighted_sample([s for s in similar if s[0] not in ids], limit - len(ids), rng)
    # Künstler, die oft zusammen mit dieser Richtung laufen – würzen, aber den Mix nicht übernehmen
    ids += _weighted_sample([s for s in related_tracks if s[0] not in ids], min(limit - len(ids), max(5, len(ids) // 3)), rng)
    if len(ids) < limit:
        ids += _weighted_sample([f for f in familiar + fresh if f[0] not in ids], limit - len(ids), rng)
    rng.shuffle(ids)
    return _spread(list(dict.fromkeys(ids))[:limit], cat), f"Daily Mix {index}", _names(group[:3], cat)


def discover(user_id: int, limit: int = 30) -> tuple[list[str], str, str]:
    """Mix der Woche: Songs, die du noch nie gehört hast, passend zu deinem Geschmack – jede Woche neu."""
    p, cat = profile(user_id), catalog()
    rng = _rng(user_id, "discover", weekly=True)
    genre, decade, artist = _norm(p.genre), _norm(p.decade), _norm(p.artist)
    cooc, cooc_artist = cooccurrence()
    top_tracks = sorted(p.track, key=p.track.get, reverse=True)[:50]
    related: Counter = Counter()
    for t in top_tracks:
        related.update(cooc.get(t, {}))
    related_norm = _norm(dict(related))
    related_artist: Counter = Counter()
    for a in sorted(p.artist, key=p.artist.get, reverse=True)[:20]:
        related_artist.update(cooc_artist.get(a, {}))
    rel_art = _norm(dict(related_artist))
    pool = []
    for t in cat.tracks.values():
        tid = t["id"]
        if tid in p.played or p.skipped[tid]:
            continue
        a_score = max((artist.get(a, 0) for a in t["artists"]), default=0)
        ra_score = max((rel_art.get(a, 0) for a in t["artists"]), default=0)
        s = (0.40 * genre.get(t["genre"], 0) + 0.15 * decade.get(t["year"] // 10 * 10, 0)
             + 0.20 * related_norm.get(tid, 0) + 0.15 * ra_score + 0.10 * a_score)
        pool.append((tid, s + 0.05 + 0.15 * rng.random()))
    pool.sort(key=lambda x: x[1], reverse=True)
    ids = _max_per_artist([t for t, _ in pool], cat, 2)[:limit]
    rng.shuffle(ids)
    return _spread(ids, cat), "Mix der Woche", "Neue Entdeckungen für dich – jeden Montag neu"


def release_radar(user_id: int, limit: int = 30) -> tuple[list[str], str, str]:
    p, cat = profile(user_id), catalog()
    since = time.time() - 21 * DAY
    artist, genre = _norm(p.artist), _norm(p.genre)
    fresh = [t for t in cat.tracks.values() if t["added_at"] > since and t["id"] not in p.played]
    fresh.sort(key=lambda t: (max((artist.get(a, 0) for a in t["artists"]), default=0) + 0.5 * genre.get(t["genre"], 0),
                              t["added_at"]), reverse=True)
    return [t["id"] for t in fresh[:limit]], "Neu für dich", "Frisch in der Bibliothek, passend zu dir"


def on_repeat(user_id: int, limit: int = 30) -> tuple[list[str], str, str]:
    p = profile(user_id)
    ids = [t for t, n in sorted(p.recent.items(), key=lambda x: x[1], reverse=True) if n >= 2 and p.skipped[t] < 2]
    return ids[:limit], "Auf Dauerschleife", "Deine meistgehörten Songs der letzten 30 Tage"


def time_travel(user_id: int, limit: int = 30) -> tuple[list[str], str, str]:
    p, cat = profile(user_id), catalog()
    cutoff = time.time() - 60 * DAY
    liked_long_ago = {r["track_id"] for r in db.query("SELECT track_id FROM likes WHERE user_id = ? AND liked_at < ?",
                                                        (user_id, cutoff))}
    old = [t for t, s in p.track.items() if s > 0 and t in cat.tracks and p.last_played.get(t, 0) < cutoff
           and (t in liked_long_ago or t in p.played)]
    old.sort(key=lambda t: p.track[t], reverse=True)
    ids = old[:limit]
    _rng(user_id, "timetravel").shuffle(ids)
    return _spread(ids, cat), "Zeitreise", "Lieblinge, die du länger nicht gehört hast"


_DAYPARTS = [(5, 11, "Morgen-Mix"), (11, 17, "Nachmittags-Mix"), (17, 22, "Abend-Mix"), (22, 29, "Nacht-Mix")]


def _daypart(hour: int) -> tuple[int, int, str]:
    h = hour if hour >= 5 else hour + 24
    return next(d for d in _DAYPARTS if d[0] <= h < d[1])


def daypart_mix(user_id: int, limit: int = 40) -> tuple[list[str], str, str]:
    start, end, name = _daypart(_dt.datetime.now().hour)
    p, cat = profile(user_id), catalog()
    rows = db.query("SELECT track_id, played_at FROM plays WHERE user_id = ? AND played_at > ?",
                    (user_id, time.time() - 120 * DAY))
    counts: Counter = Counter()
    for r in rows:
        h = _dt.datetime.fromtimestamp(r["played_at"]).hour
        h = h if h >= 5 else h + 24
        if start <= h < end and r["track_id"] in cat.tracks:
            counts[r["track_id"]] += 1
    if sum(counts.values()) < 8:
        return [], f"Dein {name}", ""
    rng = _rng(user_id, "daypart" + name)
    genres = Counter()
    for t, n in counts.items():
        if cat.tracks[t]["genre"]:
            genres[cat.tracks[t]["genre"]] += n
    ids = _weighted_sample(list(counts.items()), int(limit * 0.6), rng)
    similar = [(t, 1.0) for g, _ in genres.most_common(3) for t in cat.by_genre.get(g, [])
               if t not in ids and p.skipped[t] < 2]
    ids += _weighted_sample(similar, limit - len(ids), rng)
    rng.shuffle(ids)
    return _spread(list(dict.fromkeys(ids)), cat), f"Dein {name}", "Was du um diese Tageszeit gern hörst"


def popular(user_id: int, limit: int = 30) -> tuple[list[str], str, str]:
    p = profile(user_id)
    counts = _popular_with_others(user_id)
    ids = [t for t, _ in counts.most_common() if t not in p.played and p.skipped[t] < 1][:limit]
    return ids, "Beliebt bei Homify", "Das hören die anderen gerade"


def because_you_listened(user_id: int, limit: int = 12) -> dict[str, Any] | None:
    """„Weil du X gehört hast“: Alben ähnlicher Künstler (gemeinsam gehört oder gleiches Genre)."""
    cat = catalog()
    recent = db.query("SELECT track_id FROM plays WHERE user_id = ? AND played_at > ? ORDER BY played_at DESC LIMIT 200",
                      (user_id, time.time() - 14 * DAY))
    counts: Counter = Counter()
    for r in recent:
        for a in cat.tracks.get(r["track_id"], {}).get("artists", [])[:1]:
            counts[a] += 1
    if not counts:
        return None
    rng = _rng(user_id, "because")
    seed = rng.choice([a for a, _ in counts.most_common(3)])
    _cooc_t, cooc_a = cooccurrence()
    scores: dict[str, float] = defaultdict(float)
    for other, n in cooc_a.get(seed, {}).items():
        scores[other] += n * 2
    genre = cat.artist_genre.get(seed)
    if genre:
        for other, g in cat.artist_genre.items():
            if g == genre:
                scores[other] += 1 + rng.random() * 0.5
    scores.pop(seed, None)
    albums: list[str] = []
    for aid in sorted(scores, key=scores.get, reverse=True)[:30]:
        for t in cat.by_artist.get(aid, []):
            alb = cat.tracks[t]["album_id"]
            if alb and alb not in albums:
                albums.append(alb)
                break
        if len(albums) >= limit:
            break
    if not albums:
        return None
    return {"title": f"Weil du {cat.artist_names.get(seed, '')} gehört hast", "album_ids": albums}


def top_genres(user_id: int, limit: int = 8) -> list[str]:
    p, cat = profile(user_id), catalog()
    names = {}
    for t in cat.tracks.values():
        if t["genre"] and t["genre"] not in names:
            names[t["genre"]] = t["genre_name"]
    best = max(p.genre.values(), default=0)
    ranked = [g for g in sorted(p.genre, key=p.genre.get, reverse=True) if p.genre[g] >= best * 0.1]
    return [names[g] for g in ranked[:limit] if g in names]


# --------------------------------------------------------------------------- #
# Öffentliche Schnittstelle
# --------------------------------------------------------------------------- #

FEED_KINDS = {"daily", "discover", "radar", "repeat", "timetravel", "daypart", "popular"}


def _compute(kind: str, value: str, user_id: int, limit: int) -> tuple[list[str], str, str]:
    if kind == "daily":
        index = int(value) if value.isdigit() else 1
        return daily_mix(user_id, index, limit)
    fn = {"discover": discover, "radar": release_radar, "repeat": on_repeat, "timetravel": time_travel,
          "daypart": daypart_mix, "popular": popular}[kind]
    return fn(user_id, limit)


def mix(kind: str, value: str, user_id: int, limit: int = MIX_SIZE) -> tuple[list[str], str, str]:
    """Song-IDs, Name und Untertitel eines Feed-Mixes (zwischengespeichert, solange sich nichts ändert)."""
    if kind not in FEED_KINDS:
        raise KeyError(kind)
    daypart = _daypart(_dt.datetime.now().hour)[2] if kind == "daypart" else ""
    key = f"mix|{kind}|{value}|{limit}|{daypart}|{_profile_key(user_id)}|{_catalog_key()}"
    return _cached(key, 900, lambda: _compute(kind, value, user_id, limit))


def made_for_you(user_id: int) -> list[dict[str, Any]]:
    """Die Kacheln für „Für dich gemacht“ auf der Startseite."""
    p, cat = profile(user_id), catalog()
    if not cat.tracks:
        return []
    cards = []
    for i in range(1, len(_clusters(p, cat)) + 1):
        ids, name, sub = mix("daily", str(i), user_id)
        if len(ids) >= 5:
            cards.append(_card("daily", str(i), name, sub, ids, cat))
    for kind, minimum in (("discover", 5), ("radar", 3), ("repeat", 5), ("daypart", 10), ("timetravel", 5),
                          ("popular", 5)):
        ids, name, sub = mix(kind, "", user_id, 30 if kind != "daypart" else 40)
        if len(ids) >= minimum:
            cards.append(_card(kind, "", name, sub, ids, cat))
    return cards


def recommended_albums(user_id: int, limit: int = 12) -> list[str]:
    """„Entdecken“: Alben, die du noch nicht gehört hast, passend zu deinen Genres und Künstlern (täglich neu)."""
    p, cat = profile(user_id), catalog()
    rng = _rng(user_id, "albums")
    genre, artist, decade = _norm(p.genre), _norm(p.artist), _norm(p.decade)
    played_albums = {cat.tracks[t]["album_id"] for t in p.played if t in cat.tracks}
    best: dict[str, float] = {}
    for t in cat.tracks.values():
        alb = t["album_id"]
        if not alb or alb in played_albums:
            continue
        s = (genre.get(t["genre"], 0) + 0.5 * max((artist.get(a, 0) for a in t["artists"]), default=0)
             + 0.3 * decade.get(t["year"] // 10 * 10, 0))
        best[alb] = max(best.get(alb, 0), s)
    ranked = sorted(best, key=lambda a: best[a] + rng.random() * 0.6, reverse=True)
    return ranked[:limit]


def record_skip(user_id: int, track_id: str) -> None:
    db.execute("INSERT INTO skips (user_id, track_id, skipped_at) VALUES (?, ?, ?)", (user_id, track_id, time.time()))
