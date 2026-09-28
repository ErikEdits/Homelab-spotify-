"""Abfragen für die Oberfläche: Songs, Alben, Künstler, Suche, Startseite, Mixe."""

from __future__ import annotations

import json
import random
import time
from typing import Any, Iterable

from . import db, recommend
from .covers import colors_for
from .textutil import norm

TRACK_COLS = (
    "t.id, t.title, t.artist, t.artists, t.album, t.album_id, t.album_artist, t.track_no, t.disc_no, "
    "t.year, t.genre, t.duration, t.codec, t.mime, t.bitrate, t.sample_rate, t.cover_id, t.added_at, "
    "t.spotify_id, t.gain, t.album_gain"
)


def _chunks(items: list, size: int = 500) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def tracks_json(rows: list[dict[str, Any]], user_id: int | None) -> list[dict[str, Any]]:
    ids = [r["id"] for r in rows]
    liked: set[str] = set()
    artist_map: dict[str, list[dict[str, str]]] = {}
    if ids:
        for chunk in _chunks(list(set(ids))):
            marks = ",".join("?" * len(chunk))
            if user_id is not None:
                liked.update(
                    r["track_id"] for r in db.query(
                        f"SELECT track_id FROM likes WHERE user_id = ? AND track_id IN ({marks})", [user_id, *chunk]
                    )
                )
            for r in db.query(
                f"SELECT track_id, artist_id, name FROM track_artists WHERE track_id IN ({marks}) ORDER BY position",
                chunk,
            ):
                artist_map.setdefault(r["track_id"], []).append({"id": r["artist_id"], "name": r["name"]})
    out = []
    for r in rows:
        item = {
            "id": r["id"], "title": r["title"], "artist": r["artist"],
            "artists": artist_map.get(r["id"]) or [{"id": "", "name": n} for n in json.loads(r["artists"] or "[]")],
            "album": r["album"], "album_id": r["album_id"], "album_artist": r["album_artist"],
            "track_no": r["track_no"], "disc_no": r["disc_no"], "year": r["year"], "genre": r["genre"],
            "duration": r["duration"], "codec": r["codec"], "mime": r["mime"], "bitrate": r["bitrate"],
            "sample_rate": r["sample_rate"], "cover": r["cover_id"], "added_at": r["added_at"],
            "gain": r.get("gain"), "album_gain": r.get("album_gain"),
            "liked": r["id"] in liked,
        }
        for extra in ("entry_id", "entry_added", "played_at", "liked_at", "plays"):
            if extra in r:
                item[extra] = r[extra]
        out.append(item)
    return out


def album_json(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    colors = colors_for([r.get("cover_id") for r in rows])
    return [
        {
            "id": r["id"], "name": r["name"], "artist": r["artist"], "artist_id": r["artist_id"],
            "year": r["year"], "genre": r["genre"], "track_count": r["track_count"], "duration": r["duration"],
            "cover": r["cover_id"], "color": colors.get(r["cover_id"] or "", "#535353"), "added_at": r["added_at"],
        }
        for r in rows
    ]


def artist_json(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    colors = colors_for([r.get("cover_id") for r in rows])
    return [
        {
            "id": r["id"], "name": r["name"], "track_count": r["track_count"], "album_count": r["album_count"],
            "cover": r["cover_id"], "color": colors.get(r["cover_id"] or "", "#535353"),
        }
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Einzelne Objekte
# --------------------------------------------------------------------------- #

def get_track_rows(ids: list[str]) -> list[dict[str, Any]]:
    """Tracks in der übergebenen Reihenfolge (fehlende werden übersprungen)."""
    found: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(list(dict.fromkeys(ids))):
        marks = ",".join("?" * len(chunk))
        for r in db.query(f"SELECT {TRACK_COLS} FROM tracks t WHERE t.id IN ({marks})", chunk):
            found[r["id"]] = r
    return [found[i] for i in ids if i in found]


def albums_by_ids(ids: list[str]) -> list[dict[str, Any]]:
    """Alben in der übergebenen Reihenfolge (fehlende werden übersprungen)."""
    found: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(list(dict.fromkeys(ids))):
        marks = ",".join("?" * len(chunk))
        for r in db.query(f"SELECT * FROM albums WHERE id IN ({marks})", chunk):
            found[r["id"]] = r
    return [found[i] for i in ids if i in found]


def album_detail(album_id: str, user_id: int) -> dict[str, Any] | None:
    row = db.query_one("SELECT * FROM albums WHERE id = ?", (album_id,))
    if not row:
        return None
    tracks = db.query(
        f"SELECT {TRACK_COLS} FROM tracks t WHERE t.album_id = ? ORDER BY t.disc_no, t.track_no, t.title",
        (album_id,),
    )
    album = album_json([row])[0]
    album["tracks"] = tracks_json(tracks, user_id)
    more = db.query(
        "SELECT * FROM albums WHERE artist_id = ? AND id != ? ORDER BY year DESC LIMIT 12",
        (row["artist_id"], album_id),
    )
    album["more"] = album_json(more)
    return album


def artist_detail(artist_id: str, user_id: int) -> dict[str, Any] | None:
    row = db.query_one("SELECT * FROM artists WHERE id = ?", (artist_id,))
    if not row:
        return None
    artist = artist_json([row])[0]
    popular = db.query(
        f"SELECT {TRACK_COLS}, COUNT(p.id) AS plays FROM tracks t "
        "JOIN track_artists ta ON ta.track_id = t.id "
        "LEFT JOIN plays p ON p.track_id = t.id "
        "WHERE ta.artist_id = ? GROUP BY t.id ORDER BY plays DESC, t.year DESC, t.title LIMIT 10",
        (artist_id,),
    )
    artist["popular"] = tracks_json(popular, user_id)
    artist["albums"] = album_json(
        db.query("SELECT * FROM albums WHERE artist_id = ? ORDER BY year DESC, name", (artist_id,))
    )
    artist["appears_on"] = album_json(db.query(
        "SELECT DISTINCT a.* FROM albums a JOIN tracks t ON t.album_id = a.id "
        "JOIN track_artists ta ON ta.track_id = t.id WHERE ta.artist_id = ? AND a.artist_id != ? "
        "ORDER BY a.year DESC LIMIT 20",
        (artist_id, artist_id),
    ))
    return artist


def artist_all_tracks(artist_id: str, user_id: int) -> list[dict[str, Any]]:
    rows = db.query(
        f"SELECT {TRACK_COLS} FROM tracks t JOIN track_artists ta ON ta.track_id = t.id "
        "WHERE ta.artist_id = ? ORDER BY t.year DESC, t.album, t.disc_no, t.track_no",
        (artist_id,),
    )
    return tracks_json(rows, user_id)


# --------------------------------------------------------------------------- #
# Listen
# --------------------------------------------------------------------------- #

ALBUM_SORT = {
    "name": "name COLLATE NOCASE", "artist": "artist COLLATE NOCASE, year", "added": "added_at DESC",
    "year": "year DESC, name COLLATE NOCASE",
}
TRACK_SORT = {
    "title": "t.title COLLATE NOCASE", "artist": "t.artist COLLATE NOCASE, t.album, t.disc_no, t.track_no",
    "album": "t.album COLLATE NOCASE, t.disc_no, t.track_no", "added": "t.added_at DESC",
    "duration": "t.duration DESC",
}


def list_albums(sort: str = "name", limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
    order = ALBUM_SORT.get(sort, ALBUM_SORT["name"])
    return album_json(db.query(f"SELECT * FROM albums ORDER BY {order} LIMIT ? OFFSET ?", (limit, offset)))


def list_artists(limit: int = 1000, offset: int = 0) -> list[dict[str, Any]]:
    return artist_json(db.query(
        "SELECT * FROM artists ORDER BY name COLLATE NOCASE LIMIT ? OFFSET ?", (limit, offset)
    ))


def list_tracks(user_id: int, sort: str = "title", limit: int = 200, offset: int = 0) -> dict[str, Any]:
    order = TRACK_SORT.get(sort, TRACK_SORT["title"])
    rows = db.query(f"SELECT {TRACK_COLS} FROM tracks t ORDER BY {order} LIMIT ? OFFSET ?", (limit, offset))
    total = db.query_one("SELECT COUNT(*) AS n FROM tracks")["n"]
    return {"total": total, "items": tracks_json(rows, user_id)}


def stats() -> dict[str, Any]:
    row = db.query_one(
        "SELECT COUNT(*) AS tracks, COALESCE(SUM(duration),0) AS duration, COALESCE(SUM(size),0) AS size FROM tracks"
    )
    row["albums"] = db.query_one("SELECT COUNT(*) AS n FROM albums")["n"]
    row["artists"] = db.query_one("SELECT COUNT(*) AS n FROM artists")["n"]
    return row


# --------------------------------------------------------------------------- #
# Suche
# --------------------------------------------------------------------------- #

def _like_clause(column: str, terms: list[str]) -> tuple[str, list[str]]:
    clause = " AND ".join(f"{column} LIKE ? ESCAPE '\\'" for _ in terms)
    params = ["%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" for t in terms]
    return clause, params


def search(q: str, user_id: int, limit: int = 50) -> dict[str, Any]:
    terms = norm(q).split()
    if not terms:
        return {"tracks": [], "albums": [], "artists": [], "playlists": []}
    clause, params = _like_clause("t.search", terms)
    tracks = db.query(f"SELECT {TRACK_COLS} FROM tracks t WHERE {clause} LIMIT 400", params)
    joined = " ".join(terms)

    def score(r: dict[str, Any]) -> tuple:
        title, artist = norm(r["title"]), norm(r["artist"])
        return (
            0 if title == joined else 1 if title.startswith(joined) else 2 if joined in title else 3,
            0 if artist.startswith(terms[0]) else 1,
            title,
        )

    tracks.sort(key=score)
    a_clause, a_params = _like_clause("search", terms)
    albums = db.query(f"SELECT * FROM albums WHERE {a_clause} ORDER BY year DESC LIMIT 24", a_params)
    artists = db.query(f"SELECT * FROM artists WHERE {a_clause} ORDER BY track_count DESC LIMIT 24", a_params)
    p_clause, p_params = _like_clause("lower(name)", [t for t in terms])
    playlists = db.query(
        f"SELECT id FROM playlists WHERE (user_id = ? OR public = 1) AND {p_clause} LIMIT 12", [user_id, *p_params]
    )
    return {
        "tracks": tracks_json(tracks[:limit], user_id),
        "albums": album_json(albums),
        "artists": artist_json(artists),
        "playlists": [p for p in (playlist_summary(r["id"]) for r in playlists) if p],
    }


# --------------------------------------------------------------------------- #
# Playlists & Lieblingssongs
# --------------------------------------------------------------------------- #

def playlist_summary(playlist_id: int, viewer_id: int | None = None) -> dict[str, Any] | None:
    row = db.query_one(
        "SELECT p.*, u.username AS owner FROM playlists p LEFT JOIN users u ON u.id = p.user_id WHERE p.id = ?",
        (playlist_id,),
    )
    if not row:
        return None
    agg = db.query_one(
        "SELECT COUNT(t.id) AS n, COALESCE(SUM(t.duration),0) AS d FROM playlist_tracks pt "
        "JOIN tracks t ON t.id = pt.track_id WHERE pt.playlist_id = ?",
        (playlist_id,),
    )
    covers = [
        r["cover_id"] for r in db.query(
            "SELECT t.cover_id, MIN(pt.position) AS pos FROM playlist_tracks pt JOIN tracks t ON t.id = pt.track_id "
            "WHERE pt.playlist_id = ? AND t.cover_id IS NOT NULL GROUP BY t.cover_id ORDER BY pos LIMIT 4",
            (playlist_id,),
        )
    ]
    colors = colors_for(covers[:1])
    out = {
        "id": row["id"], "name": row["name"], "description": row["description"], "user_id": row["user_id"],
        "track_count": agg["n"], "duration": agg["d"], "covers": covers,
        "color": colors.get(covers[0], "#535353") if covers else "#535353",
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "owner": row["owner"] or "", "public": bool(row["public"]), "published_at": row["published_at"],
    }
    if viewer_id is not None:
        out["own"] = row["user_id"] == viewer_id
        out["followed"] = bool(not out["own"] and db.query_one(
            "SELECT 1 AS x FROM playlist_follows WHERE user_id = ? AND playlist_id = ?", (viewer_id, playlist_id)))
    return out


def user_playlists(user_id: int) -> list[dict[str, Any]]:
    """Eigene Playlists + veröffentlichte Playlists anderer, denen man folgt."""
    rows = db.query("SELECT id FROM playlists WHERE user_id = ? ORDER BY updated_at DESC", (user_id,))
    followed = db.query(
        "SELECT p.id FROM playlist_follows f JOIN playlists p ON p.id = f.playlist_id "
        "WHERE f.user_id = ? AND p.public = 1 AND p.user_id != ? ORDER BY f.followed_at DESC", (user_id, user_id))
    return [p for p in (playlist_summary(r["id"], user_id) for r in rows + followed) if p]


def public_playlists(user_id: int, limit: int = 100) -> list[dict[str, Any]]:
    """Von anderen Benutzern veröffentlichte Playlists (neueste zuerst)."""
    rows = db.query(
        "SELECT id FROM playlists WHERE public = 1 AND user_id != ? ORDER BY COALESCE(published_at, updated_at) DESC "
        "LIMIT ?", (user_id, limit))
    return [p for p in (playlist_summary(r["id"], user_id) for r in rows) if p and p["track_count"]]


def playlist_detail(playlist_id: int, user_id: int) -> dict[str, Any] | None:
    """Eigene Playlist oder eine veröffentlichte eines anderen Benutzers."""
    summary = playlist_summary(playlist_id, user_id)
    if not summary or (summary["user_id"] != user_id and not summary["public"]):
        return None
    rows = db.query(
        f"SELECT {TRACK_COLS}, pt.id AS entry_id, pt.added_at AS entry_added FROM playlist_tracks pt JOIN tracks t ON t.id = pt.track_id "
        "WHERE pt.playlist_id = ? ORDER BY pt.position, pt.id",
        (playlist_id,),
    )
    summary["tracks"] = tracks_json(rows, user_id)
    return summary


def liked_tracks(user_id: int) -> list[dict[str, Any]]:
    rows = db.query(
        f"SELECT {TRACK_COLS}, l.liked_at FROM likes l JOIN tracks t ON t.id = l.track_id "
        "WHERE l.user_id = ? ORDER BY l.liked_at DESC",
        (user_id,),
    )
    return tracks_json(rows, user_id)


# --------------------------------------------------------------------------- #
# Startseite, Verlauf & Mixe
# --------------------------------------------------------------------------- #

def recent_tracks(user_id: int, limit: int = 30) -> list[dict[str, Any]]:
    rows = db.query(
        f"SELECT {TRACK_COLS}, MAX(p.played_at) AS played_at FROM plays p JOIN tracks t ON t.id = p.track_id "
        "WHERE p.user_id = ? GROUP BY t.id ORDER BY played_at DESC LIMIT ?",
        (user_id, limit),
    )
    return tracks_json(rows, user_id)


def home(user_id: int, limit: int = 12) -> dict[str, Any]:
    recent_albums = db.query(
        "SELECT a.*, MAX(p.played_at) AS last FROM plays p JOIN tracks t ON t.id = p.track_id "
        "JOIN albums a ON a.id = t.album_id WHERE p.user_id = ? GROUP BY a.id ORDER BY last DESC LIMIT ?",
        (user_id, limit),
    )
    new_albums = db.query("SELECT * FROM albums ORDER BY added_at DESC LIMIT ?", (limit,))
    discover = albums_by_ids(recommend.recommended_albums(user_id, limit))
    if len(discover) < limit:
        seen = {a["id"] for a in discover}
        discover += [a for a in db.query("SELECT * FROM albums ORDER BY RANDOM() LIMIT ?", (limit * 2,))
                     if a["id"] not in seen][: limit - len(discover)]
    since = time.time() - 60 * 86400
    top_tracks = db.query(
        f"SELECT {TRACK_COLS}, COUNT(p.id) AS plays FROM plays p JOIN tracks t ON t.id = p.track_id "
        "WHERE p.user_id = ? AND p.played_at > ? GROUP BY t.id ORDER BY plays DESC LIMIT 10",
        (user_id, since),
    )
    top_artists = db.query(
        "SELECT ar.*, COUNT(p.id) AS plays FROM plays p JOIN track_artists ta ON ta.track_id = p.track_id "
        "JOIN artists ar ON ar.id = ta.artist_id WHERE p.user_id = ? GROUP BY ar.id ORDER BY plays DESC LIMIT ?",
        (user_id, limit),
    )
    if len(top_artists) < 6:
        top_artists = db.query("SELECT * FROM artists ORDER BY track_count DESC LIMIT ?", (limit,))
    return {
        "recent_albums": album_json(recent_albums),
        "new_albums": album_json(new_albums),
        "discover": album_json(discover),
        "top_tracks": tracks_json(top_tracks, user_id),
        "top_artists": artist_json(top_artists),
        "mixes": mixes()[:limit],
        "playlists": user_playlists(user_id)[:limit],
        "shared": public_playlists(user_id, limit),
        "liked_count": db.query_one("SELECT COUNT(*) AS n FROM likes WHERE user_id = ?", (user_id,))["n"],
        "stats": stats(),
    }


def mixes() -> list[dict[str, Any]]:
    genres = db.query(
        "SELECT genre, COUNT(*) AS n FROM tracks WHERE genre != '' GROUP BY lower(genre) ORDER BY n DESC LIMIT 8"
    )
    return [_genre_card(g["genre"], g["n"]) for g in genres]


def mixes_for(genres: list[str]) -> list[dict[str, Any]]:
    """Genre-Mix-Kacheln in der übergebenen Reihenfolge (z. B. die Lieblingsgenres eines Benutzers)."""
    out = []
    for genre in genres:
        n = db.query_one("SELECT COUNT(*) AS n FROM tracks WHERE lower(genre) = lower(?)", (genre,))["n"]
        if n:
            out.append(_genre_card(genre, n))
    return out


def _genre_card(genre: str, count: int) -> dict[str, Any]:
    covers = [r["cover_id"] for r in db.query(
        "SELECT cover_id FROM albums WHERE lower(genre) = lower(?) AND cover_id IS NOT NULL "
        "ORDER BY track_count DESC LIMIT 4", (genre,),
    )]
    return {"id": "genre:" + genre, "name": f"{genre} Mix", "genre": genre, "track_count": count, "covers": covers,
            "color": colors_for(covers[:1]).get(covers[0], "#535353") if covers else "#535353"}


def mix_tracks(kind: str, value: str, user_id: int, limit: int = 60) -> list[dict[str, Any]]:
    if kind == "genre":
        rows = db.query(
            f"SELECT {TRACK_COLS} FROM tracks t WHERE lower(t.genre) = lower(?) ORDER BY RANDOM() LIMIT ?",
            (value, limit),
        )
    elif kind == "radio":
        seed = db.query_one("SELECT * FROM tracks WHERE id = ?", (value,))
        if not seed:
            return []
        artist_ids = [r["artist_id"] for r in db.query("SELECT artist_id FROM track_artists WHERE track_id = ?", (value,))]
        marks = ",".join("?" * len(artist_ids)) or "''"
        same_artist = db.query(
            f"SELECT DISTINCT {TRACK_COLS} FROM tracks t JOIN track_artists ta ON ta.track_id = t.id "
            f"WHERE ta.artist_id IN ({marks}) AND t.id != ? ORDER BY RANDOM() LIMIT 15",
            [*artist_ids, value],
        )
        same_genre = db.query(
            f"SELECT {TRACK_COLS} FROM tracks t WHERE t.genre != '' AND lower(t.genre) = lower(?) AND t.id != ? "
            "ORDER BY RANDOM() LIMIT ?", (seed["genre"], value, limit),
        ) if seed["genre"] else []
        same_era = db.query(
            f"SELECT {TRACK_COLS} FROM tracks t WHERE t.year BETWEEN ? AND ? AND t.id != ? ORDER BY RANDOM() LIMIT 20",
            (seed["year"] - 5, seed["year"] + 5, value),
        ) if seed["year"] else []
        pool: dict[str, dict] = {}
        for r in same_artist + same_genre + same_era:
            pool.setdefault(r["id"], r)
        rows = list(pool.values())
        random.shuffle(rows)
        rows = [db.query_one(f"SELECT {TRACK_COLS} FROM tracks t WHERE t.id = ?", (value,))] + rows[: limit - 1]
    else:
        rows = db.query(f"SELECT {TRACK_COLS} FROM tracks t ORDER BY RANDOM() LIMIT ?", (limit,))
    return tracks_json(rows, user_id)


def genres() -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT genre, COUNT(*) AS n FROM tracks WHERE genre != '' GROUP BY lower(genre) ORDER BY n DESC LIMIT 60"
    )
    out = []
    for g in rows:
        cover = db.query_one(
            "SELECT cover_id FROM albums WHERE lower(genre) = lower(?) AND cover_id IS NOT NULL "
            "ORDER BY track_count DESC LIMIT 1", (g["genre"],),
        )
        out.append({"name": g["genre"], "count": g["n"], "cover": cover["cover_id"] if cover else None})
    return out


# --------------------------------------------------------------------------- #
# Playlist automatisch zusammenstellen
# --------------------------------------------------------------------------- #

GENERATOR_SOURCES = ("song", "artist", "genre", "decade", "top", "liked", "new", "rediscover", "random")


def _ids(sql: str, params: Iterable[Any] = ()) -> list[str]:
    return [r["id"] for r in db.query(sql, params)]


def _spread_artists(ids: list[str]) -> list[str]:
    """Möglichst nie zweimal derselbe Künstler direkt hintereinander."""
    if len(ids) < 3:
        return ids
    artist = {r["id"]: r["artist"] for r in db.query(
        "SELECT id, artist FROM tracks WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)}
    out = list(ids)
    for i in range(1, len(out)):
        if artist.get(out[i]) != artist.get(out[i - 1]):
            continue
        for j in range(i + 1, len(out)):
            if artist.get(out[j]) != artist.get(out[i - 1]):
                out[i], out[j] = out[j], out[i]
                break
    return out


def generate_playlist_tracks(source: str, value: str, user_id: int, count: int) -> tuple[list[str], str]:
    """Songs für eine automatisch zusammengestellte Playlist + Namensvorschlag."""
    count = max(5, min(int(count or 50), 500))
    shuffle = True
    if source == "song":
        seed = db.query_one("SELECT id, title FROM tracks WHERE id = ?", (value,))
        if not seed:
            raise ValueError("Song nicht gefunden")
        ids = [t["id"] for t in mix_tracks("radio", value, user_id, count)]
        name, shuffle = f"Ähnlich wie {seed['title']}", False  # Startsong bleibt vorne
        ids = ids[:1] + _spread_artists(ids[1:])
    elif source == "artist":
        artist = db.query_one("SELECT id, name FROM artists WHERE id = ?", (value,))
        if not artist:
            raise ValueError("Künstler nicht gefunden")
        own = _ids("SELECT t.id FROM tracks t JOIN track_artists ta ON ta.track_id = t.id WHERE ta.artist_id = ? "
                   "ORDER BY RANDOM() LIMIT ?", (value, max(5, count // 2)))
        genres = [r["genre"] for r in db.query(
            "SELECT t.genre, COUNT(*) AS n FROM tracks t JOIN track_artists ta ON ta.track_id = t.id "
            "WHERE ta.artist_id = ? AND t.genre != '' GROUP BY lower(t.genre) ORDER BY n DESC LIMIT 3", (value,))]
        similar = _ids(
            "SELECT t.id FROM tracks t WHERE lower(t.genre) IN (%s) AND t.id NOT IN (SELECT track_id FROM track_artists "
            "WHERE artist_id = ?) ORDER BY RANDOM() LIMIT ?" % ",".join("lower(?)" for _ in genres),
            [*genres, value, count]) if genres else []
        ids = own + similar
        name = f"{artist['name']} & Ähnliches"
    elif source == "genre":
        ids = _ids("SELECT id FROM tracks WHERE lower(genre) = lower(?) ORDER BY RANDOM() LIMIT ?", (value, count))
        name = f"{value} Mix"
    elif source == "decade":
        start = int(value or 0) // 10 * 10
        if start < 1900:
            raise ValueError("Ungültiges Jahrzehnt")
        ids = _ids("SELECT id FROM tracks WHERE year BETWEEN ? AND ? ORDER BY RANDOM() LIMIT ?",
                   (start, start + 9, count))
        name = f"Die {str(start)[2:]}er" if start < 2000 else f"Die {start}er"
    elif source == "top":
        ids = _ids("SELECT t.id FROM plays p JOIN tracks t ON t.id = p.track_id WHERE p.user_id = ? "
                   "GROUP BY t.id ORDER BY COUNT(p.id) DESC, MAX(p.played_at) DESC LIMIT ?", (user_id, count))
        name, shuffle = "Meine Top-Songs", False
    elif source == "liked":
        ids = _ids("SELECT t.id FROM likes l JOIN tracks t ON t.id = l.track_id WHERE l.user_id = ? "
                   "ORDER BY RANDOM() LIMIT ?", (user_id, count))
        name = "Lieblingssongs gemischt"
    elif source == "new":
        ids = _ids("SELECT id FROM tracks ORDER BY added_at DESC LIMIT ?", (count,))
        name, shuffle = "Neu in der Bibliothek", False
    elif source == "rediscover":
        # Songs, die du länger nicht (oder noch nie) gehört hast – Lieblingssongs zuerst
        since = time.time() - 60 * 86400
        ids = _ids(
            "SELECT t.id FROM tracks t LEFT JOIN likes l ON l.track_id = t.id AND l.user_id = ? "
            "WHERE t.id NOT IN (SELECT track_id FROM plays WHERE user_id = ? AND played_at > ?) "
            "ORDER BY (l.track_id IS NULL), RANDOM() LIMIT ?", (user_id, user_id, since, count))
        name = "Wiederentdecken"
    elif source == "random":
        ids = _ids("SELECT id FROM tracks ORDER BY RANDOM() LIMIT ?", (count,))
        name = "Zufallsmix"
    else:
        raise ValueError("Unbekannte Grundlage")
    ids = list(dict.fromkeys(ids))[:count]
    if shuffle:
        random.shuffle(ids)
        ids = _spread_artists(ids)
    return ids, name


def generator_options(user_id: int) -> dict[str, Any]:
    """Was der Dialog „Playlist zusammenstellen“ anbieten kann."""
    decades = [
        {"value": r["d"], "count": r["n"]} for r in db.query(
            "SELECT (year / 10) * 10 AS d, COUNT(*) AS n FROM tracks WHERE year >= 1900 GROUP BY d ORDER BY d DESC")
    ]
    return {
        "genres": [{"name": g["name"], "count": g["count"]} for g in genres()],
        "decades": decades,
        "liked": db.query_one("SELECT COUNT(*) AS n FROM likes WHERE user_id = ?", (user_id,))["n"],
        "played": db.query_one("SELECT COUNT(DISTINCT track_id) AS n FROM plays WHERE user_id = ?", (user_id,))["n"],
        "tracks": stats()["tracks"],
    }
