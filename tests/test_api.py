import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(scanned):
    from homify.scanner import scanner
    from homify.server import app

    with TestClient(app) as c:
        scanner.wait(30)
        yield c


@pytest.fixture(scope="module")
def admin(client):
    status = client.get("/api/setup").json()
    if status["needs_setup"]:
        r = client.post("/api/setup", json={"username": "admin", "password": "geheim123"})
        assert r.status_code == 200, r.text
    else:
        r = client.post("/api/auth/login", json={"username": "admin", "password": "geheim123"})
        assert r.status_code == 200, r.text
    return client


def test_requires_login(client):
    fresh = TestClient(client.app)
    assert fresh.get("/api/home").status_code == 401
    assert fresh.get("/api/tracks/x/stream").status_code == 401


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Homify" in r.text
    js = client.get("/js/app.js")
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]


def test_setup_only_once(admin):
    r = admin.post("/api/setup", json={"username": "evil", "password": "evil1234"})
    assert r.status_code == 409


def test_wrong_password(client):
    fresh = TestClient(client.app)
    r = fresh.post("/api/auth/login", json={"username": "admin", "password": "falsch"})
    assert r.status_code == 401


def test_home_and_search(admin):
    home = admin.get("/api/home").json()
    assert home["stats"]["tracks"] >= 9
    res = admin.get("/api/search", params={"q": "daft one more"}).json()
    assert res["tracks"][0]["title"] == "One More Time"
    res = admin.get("/api/search", params={"q": "daft"}).json()
    assert any(a["name"] == "Daft Punk" for a in res["artists"])
    assert any(a["name"] == "Discovery" for a in res["albums"])


def _track(admin, q):
    return admin.get("/api/search", params={"q": q}).json()["tracks"][0]


def test_stream_with_range(admin):
    t = _track(admin, "one more time")
    r = admin.get(f"/api/tracks/{t['id']}/stream", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert len(r.content) == 100
    assert r.headers["content-type"] == "audio/mpeg"


def test_transcode_wma(admin):
    t = _track(admin, "wma song")
    r = admin.get(f"/api/tracks/{t['id']}/stream", params={"transcode": 1})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.content[:3] == b"ID3" or r.content[:2] == b"\xff\xfb"


def test_cover_thumbnail(admin):
    t = _track(admin, "one more time")
    r = admin.get(f"/api/covers/{t['cover']}", params={"size": 64})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"


def test_album_and_artist(admin):
    t = _track(admin, "aerodynamic")
    album = admin.get(f"/api/albums/{t['album_id']}").json()
    assert [x["title"] for x in album["tracks"]] == ["One More Time", "Aerodynamic"]
    artist = admin.get(f"/api/artists/{t['artists'][0]['id']}").json()
    assert artist["name"] == "Daft Punk"
    assert len(artist["albums"]) == 1


def test_likes_playlists_history(admin):
    t1 = _track(admin, "one more time")
    t2 = _track(admin, "aerodynamic")
    assert admin.put(f"/api/likes/{t1['id']}").json() == {"liked": True}
    assert [x["id"] for x in admin.get("/api/likes").json()] == [t1["id"]]
    p = admin.post("/api/playlists", json={"name": "Roadtrip", "track_ids": [t1["id"]]}).json()
    admin.post(f"/api/playlists/{p['id']}/tracks", json={"ids": [t2["id"], "gibtsnicht"]})
    detail = admin.get(f"/api/playlists/{p['id']}").json()
    assert [x["id"] for x in detail["tracks"]] == [t1["id"], t2["id"]]
    entries = [x["entry_id"] for x in detail["tracks"]]
    admin.put(f"/api/playlists/{p['id']}/order", json={"entry_ids": list(reversed(entries))})
    detail = admin.get(f"/api/playlists/{p['id']}").json()
    assert [x["id"] for x in detail["tracks"]] == [t2["id"], t1["id"]]
    admin.delete(f"/api/playlists/{p['id']}/entries/{entries[0]}")
    assert admin.get(f"/api/playlists/{p['id']}").json()["track_count"] == 1
    assert admin.post("/api/history", json={"track_id": t1["id"]}).status_code == 200
    home = admin.get("/api/home").json()
    assert home["recent_albums"][0]["name"] == "Discovery"
    radio = admin.get("/api/mix/radio", params={"value": t1["id"]}).json()
    assert radio[0]["id"] == t1["id"]


def test_other_users_cannot_see_playlists(admin, client):
    p = admin.post("/api/playlists", json={"name": "Privat"}).json()
    admin.post("/api/users", json={"username": "gast", "password": "gast1234"})
    guest = TestClient(client.app)
    assert guest.post("/api/auth/login", json={"username": "gast", "password": "gast1234"}).status_code == 200
    assert guest.get(f"/api/playlists/{p['id']}").status_code == 404
    assert guest.get("/api/settings").status_code == 403


def test_download_validation(admin):
    r = admin.post("/api/downloads", json={"query": "--output /etc"})
    assert r.status_code == 400
    r = admin.post("/api/downloads", json={"query": "saved"})
    assert r.status_code == 400


def test_settings_hide_secret(admin):
    admin.put("/api/settings", json={"spotify_client_secret": "topsecret"})
    s = admin.get("/api/settings").json()["settings"]
    assert s["spotify_client_secret"] == "********"
    admin.put("/api/settings", json={"spotify_client_secret": "********"})
    from homify.config import config
    assert config.get("spotify_client_secret") == "topsecret"
    admin.put("/api/settings", json={"spotify_client_secret": ""})
