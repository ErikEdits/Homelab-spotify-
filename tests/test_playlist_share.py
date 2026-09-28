"""Playlists veröffentlichen (für alle Homify-Benutzer) und automatisch zusammenstellen."""

import pytest
from fastapi.testclient import TestClient

from homify import db


def login(name: str, admin: bool = False) -> TestClient:
    from homify import auth
    from homify.server import app

    if not auth.authenticate(name, name + "pw1"):
        auth.create_user(name, name + "pw1", is_admin=admin)
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": name, "password": name + "pw1"}).status_code == 200
    return c


@pytest.fixture(scope="module")
def anna(scanned):
    return login("anna", admin=True)


@pytest.fixture(scope="module")
def ben(scanned):
    return login("ben")


def some_tracks(n=3):
    return [r["id"] for r in db.query("SELECT id FROM tracks ORDER BY title LIMIT ?", (n,))]


def test_publish_follow_copy_and_unpublish(anna, ben):
    p = anna.post("/api/playlists", json={"name": "Annas Roadtrip", "track_ids": some_tracks()}).json()
    pid = p["id"]
    assert p["own"] and not p["public"] and p["owner"] == "anna"

    # Privat: Ben sieht sie nicht
    assert ben.get(f"/api/playlists/{pid}").status_code == 404
    assert all(x["id"] != pid for x in ben.get("/api/playlists/public").json())

    # Veröffentlichen per Knopf
    r = anna.patch(f"/api/playlists/{pid}", json={"public": True}).json()
    assert r["public"] and r["published_at"]
    shared = ben.get("/api/playlists/public").json()
    assert any(x["id"] == pid and x["owner"] == "anna" and not x["own"] for x in shared)
    detail = ben.get(f"/api/playlists/{pid}").json()
    assert len(detail["tracks"]) == 3 and not detail["own"]
    assert any(x["id"] == pid for x in ben.get("/api/home").json()["shared"])
    assert any(x["id"] == pid for x in ben.get("/api/search", params={"q": "roadtrip"}).json()["playlists"])

    # Ben darf nichts ändern
    assert ben.patch(f"/api/playlists/{pid}", json={"name": "Meins"}).status_code == 404
    assert ben.post(f"/api/playlists/{pid}/tracks", json={"ids": some_tracks(1)}).status_code == 404
    assert ben.delete(f"/api/playlists/{pid}").status_code == 404

    # Folgen -> erscheint in Bens Bibliothek
    assert ben.put(f"/api/playlists/{pid}/follow").json()["followed"]
    mine = ben.get("/api/playlists").json()
    assert any(x["id"] == pid and x["followed"] and not x["own"] for x in mine)
    assert anna.put(f"/api/playlists/{pid}/follow").status_code == 400  # eigene Playlist

    # Kopie -> eigene, bearbeitbare Playlist
    copy = ben.post(f"/api/playlists/{pid}/copy").json()
    assert copy["own"] and copy["owner"] == "ben" and copy["track_count"] == 3 and not copy["public"]
    assert ben.post(f"/api/playlists/{copy['id']}/tracks", json={"ids": some_tracks(1)}).status_code == 200

    # Nicht mehr veröffentlichen -> verschwindet für Ben (auch trotz Folgen)
    anna.patch(f"/api/playlists/{pid}", json={"public": False})
    assert ben.get(f"/api/playlists/{pid}").status_code == 404
    assert all(x["id"] != pid for x in ben.get("/api/playlists").json())
    assert ben.put(f"/api/playlists/{pid}/follow").status_code == 404

    # Löschen räumt Folgen mit auf
    anna.delete(f"/api/playlists/{pid}")
    assert db.query_one("SELECT COUNT(*) AS n FROM playlist_follows WHERE playlist_id = ?", (pid,))["n"] == 0
    ben.delete(f"/api/playlists/{copy['id']}")


def test_generator_options(anna):
    opts = anna.get("/api/playlists/generator").json()
    assert opts["tracks"] > 0
    assert any(g["name"] == "Electronic" for g in opts["genres"])
    assert any(d["value"] == 2000 for d in opts["decades"])  # „One More Time“ ist von 2001


@pytest.mark.parametrize("source,value,expect_name", [
    ("random", "", "Zufallsmix"),
    ("genre", "Electronic", "Electronic Mix"),
    ("decade", "2000", "Die 2000er"),
    ("new", "", "Neu in der Bibliothek"),
    ("rediscover", "", "Wiederentdecken"),
])
def test_generate_playlist(anna, source, value, expect_name):
    r = anna.post("/api/playlists/generate", json={"source": source, "value": value, "count": 5})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["name"] == expect_name and p["own"] and 1 <= p["track_count"] <= 5
    assert "Automatisch zusammengestellt" in p["description"]
    anna.delete(f"/api/playlists/{p['id']}")


def test_generate_from_song_and_artist_and_publish(anna, ben):
    seed = db.query_one("SELECT id, title FROM tracks WHERE title = 'Aerodynamic'")
    p = anna.post("/api/playlists/generate",
                  json={"source": "song", "value": seed["id"], "count": 10, "name": "Mein Mix", "public": True}).json()
    assert p["name"] == "Mein Mix" and p["public"]
    tracks = anna.get(f"/api/playlists/{p['id']}").json()["tracks"]
    assert tracks[0]["id"] == seed["id"]  # Startsong vorne
    assert any(x["id"] == p["id"] for x in ben.get("/api/playlists/public").json())
    anna.delete(f"/api/playlists/{p['id']}")

    artist = db.query_one("SELECT id, name FROM artists WHERE name = 'Daft Punk'")
    q = anna.post("/api/playlists/generate", json={"source": "artist", "value": artist["id"], "count": 10}).json()
    assert q["name"] == "Daft Punk & Ähnliches" and q["track_count"] >= 2
    anna.delete(f"/api/playlists/{q['id']}")


def test_generate_errors(anna):
    assert anna.post("/api/playlists/generate", json={"source": "song", "value": "gibtsnicht"}).status_code == 400
    assert anna.post("/api/playlists/generate", json={"source": "quatsch"}).status_code == 422
    # Noch nichts gehört -> keine Top-Songs
    db.execute("DELETE FROM plays WHERE user_id = (SELECT id FROM users WHERE username = 'anna')")
    assert anna.post("/api/playlists/generate", json={"source": "top"}).status_code == 400
