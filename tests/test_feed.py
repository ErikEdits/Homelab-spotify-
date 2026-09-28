"""Persönlicher Feed: Daily Mixes, Mix der Woche, Auf Dauerschleife, Zeitreise, … aus dem Hörverhalten."""

import time

import pytest
from fastapi.testclient import TestClient

from homify import db, recommend

DAY = 86400

# Künstler -> (Genre, Jahr, erst vor kurzem hinzugefügt?)
ARTISTS = {
    "fxa": ("Rock", 1994, False), "fxb": ("Rock", 1998, False), "fxc": ("Jazz", 1961, False),
    "fxd": ("Jazz", 1965, False), "fxe": ("Hip-Hop", 2004, False), "fxf": ("Rock", 2023, True),
    "fxg": ("Rock", 1996, False),
}
PER_ARTIST = 8


def login(name: str) -> TestClient:
    from homify import auth
    from homify.server import app

    if not auth.authenticate(name, name + "pw1"):
        auth.create_user(name, name + "pw1")
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": name, "password": name + "pw1"}).status_code == 200
    return c


def tid(artist: str, n: int) -> str:
    return f"{artist}t{n}"


def uid(name: str) -> int:
    return db.query_one("SELECT id FROM users WHERE username = ?", (name,))["id"]


@pytest.fixture(scope="module")
def feed(scanned):
    """Kleiner Katalog (7 Künstler, 3 Genres) + Hörverlauf von zwei Benutzern."""
    now = time.time()
    for aid, (genre, year, fresh) in ARTISTS.items():
        added = now - 2 * DAY if fresh else now - 200 * DAY
        name = f"Artist {aid.upper()}"
        db.execute("INSERT INTO artists (id, name, track_count, album_count) VALUES (?, ?, ?, 1)", (aid, name, PER_ARTIST))
        db.execute("INSERT INTO albums (id, name, artist, artist_id, year, genre, track_count, added_at) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (aid + "alb", f"Album {aid}", name, aid, year, genre, PER_ARTIST, added))
        for n in range(PER_ARTIST):
            t = tid(aid, n)
            db.execute("INSERT INTO tracks (id, path, root, title, artist, artists, album, album_artist, album_id, "
                       "track_no, year, genre, duration, added_at) VALUES (?, ?, '/fx', ?, ?, ?, ?, ?, ?, ?, ?, ?, 200, ?)",
                       (t, f"/fx/{t}.mp3", f"{aid} Song {n}", name, f'["{name}"]', f"Album {aid}", name, aid + "alb",
                        n + 1, year, genre, added))
            db.execute("INSERT INTO track_artists (track_id, artist_id, name, position) VALUES (?, ?, ?, 0)", (t, aid, name))

    fan, other = login("feedfan"), login("feedother")
    fan_id, other_id = uid("feedfan"), uid("feedother")

    plays = []
    # feedfan: viel Rock (A, B), etwas Jazz (C) – in Hörsitzungen der letzten drei Wochen
    for day in range(1, 21):
        start = now - day * DAY
        session = [tid("fxa", day % 4), tid("fxb", day % 3), tid("fxa", 0), tid("fxc", day % 2)]
        for i, t in enumerate(session):
            plays.append((fan_id, t, start + i * 200))
    # vor langer Zeit viel Hip-Hop (E) -> Zeitreise
    for day in range(100, 110):
        for n in range(5):
            plays.append((fan_id, tid("fxe", n), now - day * DAY + n * 200))
    # feedother: Rock A zusammen mit Jazz D -> A und D laufen oft gemeinsam
    for day in range(1, 11):
        start = now - day * DAY
        for i, t in enumerate([tid("fxa", 1), tid("fxd", day % 5), tid("fxd", 7), tid("fxg", 2)]):
            plays.append((other_id, t, start + i * 180))
    for p in plays:
        db.execute("INSERT INTO plays (user_id, track_id, played_at) VALUES (?, ?, ?)", p)
    db.execute("INSERT INTO likes (user_id, track_id, liked_at) VALUES (?, ?, ?)", (fan_id, tid("fxb", 5), now - 5 * DAY))
    recommend.invalidate()
    yield {"fan": fan, "other": other, "fan_id": fan_id, "other_id": other_id}

    for table, col in (("plays", "user_id"), ("likes", "user_id"), ("skips", "user_id")):
        db.execute(f"DELETE FROM {table} WHERE {col} IN (?, ?)", (fan_id, other_id))
    db.execute("DELETE FROM tracks WHERE root = '/fx'")
    db.execute("DELETE FROM track_artists WHERE track_id LIKE 'fx%'")
    db.execute("DELETE FROM albums WHERE id LIKE 'fx%'")
    db.execute("DELETE FROM artists WHERE id LIKE 'fx%'")
    recommend.invalidate()


def played_by(user_id: int) -> set[str]:
    return {r["track_id"] for r in db.query("SELECT track_id FROM plays WHERE user_id = ?", (user_id,))}


def artist_of(track_id: str) -> str:
    return track_id[:3]


def test_home_has_personal_feed(feed):
    data = feed["fan"].get("/api/home").json()
    cards = {c["id"]: c for c in data["feed"]["made_for_you"]}
    assert "daily:1" in cards and "discover:" in cards and "repeat:" in cards and "timetravel:" in cards
    assert all(c["feed"] and c["name"] and c["track_count"] > 0 for c in cards.values())
    # Daily Mix 1 = Lieblingsrichtung Rock mit den Lieblingskünstlern im Untertitel
    assert "Artist FXA" in cards["daily:1"]["subtitle"]
    assert data["feed"]["top_genres"][0]["genre"] == "Rock"
    because = data["feed"]["because"]
    assert because and because["title"].startswith("Weil du Artist FX") and because["title"].endswith("gehört hast")
    assert because["albums"]
    # „Entdecken“ zeigt zuerst passende, noch nie gehörte Alben
    assert data["discover"] and data["discover"][0]["id"] in {"fxfalb", "fxgalb"}


def test_daily_mix_is_stable_personal_and_spread(feed):
    fan = feed["fan"]
    a = fan.get("/api/mix/daily?value=1&limit=30").json()
    b = fan.get("/api/mix/daily?value=1&limit=30").json()
    ids = [t["id"] for t in a]
    assert ids == [t["id"] for t in b]  # pro Tag stabil
    assert len(ids) == len(set(ids)) >= 10
    rock = {"fxa", "fxb", "fxf", "fxg"}
    assert sum(artist_of(t) in rock for t in ids) >= len(ids) * 0.8
    # nie zweimal derselbe Künstler direkt hintereinander, solange es anders geht
    assert sum(artist_of(x) == artist_of(y) for x, y in zip(ids, ids[1:])) <= 3


def test_discover_only_unheard_tracks(feed):
    data = feed["fan"].get("/api/mix/discover/detail").json()
    ids = [t["id"] for t in data["tracks"]]
    assert data["name"] == "Mix der Woche" and data["feed"] and ids
    assert not set(ids) & played_by(feed["fan_id"])
    per_artist = {}
    for t in ids:
        per_artist[artist_of(t)] = per_artist.get(artist_of(t), 0) + 1
    assert max(per_artist.values()) <= 2
    # Jazz D läuft bei anderen oft zusammen mit Rock A -> wird entdeckt
    assert any(artist_of(t) == "fxd" for t in ids)


def test_on_repeat_and_time_travel(feed):
    repeat = [t["id"] for t in feed["fan"].get("/api/mix/repeat").json()]
    assert repeat[0] == tid("fxa", 0)  # der meistgehörte Song
    travel = feed["fan"].get("/api/mix/timetravel/detail").json()
    assert travel["name"] == "Zeitreise"
    assert {t["id"] for t in travel["tracks"]} >= {tid("fxe", 0), tid("fxe", 1), tid("fxe", 2)}
    assert all(artist_of(t["id"]) == "fxe" for t in travel["tracks"] if t["id"].startswith("fx"))


def test_release_radar_and_popular(feed):
    radar = [t["id"] for t in feed["fan"].get("/api/mix/radar").json()]
    assert radar and all(artist_of(t) == "fxf" for t in radar if t.startswith("fx"))
    popular = [t["id"] for t in feed["fan"].get("/api/mix/popular").json()]
    assert tid("fxd", 7) in popular and tid("fxa", 1) not in popular  # schon selbst gehört


def test_skip_is_recorded_and_respected(feed):
    fan = feed["fan"]
    skipped = tid("fxb", 7)
    for _ in range(2):
        assert fan.post("/api/history/skip", json={"track_id": skipped}).json() == {"ok": True}
    assert db.query_one("SELECT COUNT(*) AS n FROM skips WHERE user_id = ? AND track_id = ?",
                        (feed["fan_id"], skipped))["n"] == 2
    assert fan.post("/api/history/skip", json={"track_id": "gibtsnicht"}).status_code == 404
    for kind in ("daily", "discover"):
        ids = [t["id"] for t in fan.get(f"/api/mix/{kind}?value=1&limit=100").json()]
        assert skipped not in ids, kind


def test_detail_for_every_kind(feed):
    fan = feed["fan"]
    for kind in sorted(recommend.FEED_KINDS) + ["genre", "radio", "random"]:
        value = {"genre": "Jazz", "radio": tid("fxc", 1), "daily": "2"}.get(kind, "")
        r = fan.get(f"/api/mix/{kind}/detail", params={"value": value})
        assert r.status_code == 200, kind
        data = r.json()
        assert data["name"] and isinstance(data["tracks"], list) and data["kind"] == kind
    assert fan.get("/api/mix/genre/detail?value=Jazz").json()["name"] == "Jazz Mix"
    assert fan.get(f"/api/mix/radio/detail?value={tid('fxc', 1)}").json()["name"] == "fxc Song 1 Radio"
    assert fan.get("/api/mix/daily?value=abc").status_code == 200
    assert fan.get("/api/mix/quatsch").status_code == 404


def test_cold_start_new_user(feed):
    new = login("feednew")
    data = new.get("/api/home").json()
    assert all(not c["id"].startswith("daily:") for c in data["feed"]["made_for_you"])
    assert data["feed"]["because"] is None and data["discover"]
    assert new.get("/api/mix/daily?value=1").json() == []
    assert new.get("/api/mix/discover").status_code == 200
    # Beliebt bei Homify funktioniert schon ohne eigenes Hören
    assert any(c["id"] == "popular:" for c in data["feed"]["made_for_you"])


def test_feed_follows_new_listening(feed):
    fan = feed["fan"]
    before = [t["id"] for t in fan.get("/api/mix/repeat").json()]
    for _ in range(40):
        assert fan.post("/api/history", json={"track_id": tid("fxg", 3)}).status_code == 200
    after = [t["id"] for t in fan.get("/api/mix/repeat").json()]
    assert after[0] == tid("fxg", 3) and after != before
