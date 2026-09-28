"""Spotify-Playlist-Link holen -> dieselbe Playlist entsteht in Homify (Original-Reihenfolge)."""

import pytest
from fastapi.testclient import TestClient

from homify import db, playlist_import
from homify.downloader import DownloadManager

URL = "https://open.spotify.com/playlist/TESTLISTE1"


def login(name: str) -> tuple[TestClient, int]:
    from homify import auth
    from homify.server import app

    if not auth.authenticate(name, name + "pw1"):
        auth.create_user(name, name + "pw1")
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": name, "password": name + "pw1"}).status_code == 200
    return c, c.get("/api/auth/me").json()["id"]


@pytest.fixture
def tracks(scanned):
    """Drei Songs: A und C haben eine Spotify-Kennung, B ist eine eigene Datei ohne (per Abgleich bekannt)."""
    rows = db.query("SELECT id FROM tracks ORDER BY title LIMIT 4")
    a, b, c, extra = (r["id"] for r in rows)
    db.execute("UPDATE tracks SET spotify_id = 'spA' WHERE id = ?", (a,))
    db.execute("UPDATE tracks SET spotify_id = 'spC' WHERE id = ?", (c,))
    yield {"a": a, "b": b, "c": c, "extra": extra}
    db.execute("UPDATE tracks SET spotify_id = '' WHERE spotify_id LIKE 'sp%'")
    db.execute("DELETE FROM playlists WHERE source_url = ?", (URL,))
    db.execute("DELETE FROM downloads WHERE query = ?", (URL,))


def entries(pid):
    return [r["track_id"] for r in db.query(
        "SELECT track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position", (pid,))]


def test_import_button_keeps_order_and_is_idempotent(tracks):
    c, _ = login("importer")
    body = {"url": URL, "title": "Party 2026", "spotify_ids": ["spA", "spMISSING", "spB", "spC"],
            "library_ids": {"spB": tracks["b"]}}
    p = c.post("/api/playlists/import", json=body).json()
    assert p["name"] == "Party 2026" and p["own"] and p["track_count"] == 3
    assert entries(p["id"]) == [tracks["a"], tracks["b"], tracks["c"]]  # fehlender Song übersprungen
    again = c.post("/api/playlists/import", json=body).json()
    assert again["id"] == p["id"] and again["track_count"] == 3  # keine zweite Playlist, keine Dopplungen
    assert c.post("/api/playlists/import", json={**body, "url": "https://evil.example/x"}).status_code == 422


def test_download_creates_playlist_and_fills_missing_songs_later(tracks):
    c, uid = login("importer")
    mgr = DownloadManager()
    job = mgr.add(uid, URL, kind="playlist", title="Party 2026", spotify_ids=["spA", "spB", "spC"],
                  total=3, library_ids={})
    pid = playlist_import.playlist_for(uid, URL)
    assert pid and entries(pid) == [tracks["a"], tracks["c"]]  # vorhandene sofort drin

    # eigene Ergänzung bleibt erhalten
    c.post(f"/api/playlists/{pid}/tracks", json={"ids": [tracks["extra"]]})

    # Download fertig: B wurde geholt (hat jetzt eine Spotify-Kennung)
    db.execute("UPDATE tracks SET spotify_id = 'spB' WHERE id = ?", (tracks["b"],))
    db.execute("UPDATE downloads SET status = 'done', playlist_synced = 0 WHERE id = ?", (job["id"],))
    assert playlist_import.sync_jobs() == 1
    assert entries(pid) == [tracks["a"], tracks["b"], tracks["c"], tracks["extra"]]
    assert playlist_import.sync_jobs() == 0  # nur einmal pro fertigem Download

    # Download-Liste verlinkt die Playlist
    listed = [j for j in c.get("/api/downloads").json()["jobs"] if j["id"] == job["id"]][0]
    assert listed["playlist_id"] == pid


def test_second_user_gets_own_copy(tracks):
    c1, u1 = login("importer")
    c2, u2 = login("importer2")
    mgr = DownloadManager()
    mgr.add(u1, URL, kind="playlist", title="Party", spotify_ids=["spA", "spB"], total=2)
    job = mgr.add(u2, URL, kind="playlist", title="Party", spotify_ids=["spA", "spB"], total=2)  # gleicher Job
    p1, p2 = playlist_import.playlist_for(u1, URL), playlist_import.playlist_for(u2, URL)
    assert p1 and p2 and p1 != p2
    db.execute("UPDATE tracks SET spotify_id = 'spB' WHERE id = ?", (tracks["b"],))
    db.execute("UPDATE downloads SET status = 'done', playlist_synced = 0 WHERE id = ?", (job["id"],))
    playlist_import.sync_jobs()
    assert entries(p1) == entries(p2) == [tracks["a"], tracks["b"]]


def test_everything_known_completes_playlist_without_spotdl(tracks, monkeypatch):
    from homify import downloader

    _, uid = login("importer")
    monkeypatch.setattr(downloader, "resolve_tracks", lambda q: [
        {"id": "spA", "library_id": tracks["a"]}, {"id": "spB", "library_id": tracks["b"]}])
    monkeypatch.setattr(downloader, "venv_python", lambda: type("P", (), {"exists": lambda self: True})())
    monkeypatch.setattr(downloader, "build_command", lambda *a, **k: pytest.fail("spotDL darf nicht starten"))
    mgr = DownloadManager()
    job = mgr.add(uid, URL, kind="playlist", title="Party", spotify_ids=["spA", "spB"], total=2)
    pid = playlist_import.playlist_for(uid, URL)
    assert entries(pid) == [tracks["a"]]  # B ist nur über den Abgleich bekannt
    mgr._run(db.query_one("SELECT * FROM downloads WHERE id = ?", (job["id"],)))
    assert mgr.get(job["id"])["status"] == "done"
    assert entries(pid) == [tracks["a"], tracks["b"]]


def test_single_song_download_fills_imported_playlist(tracks):
    c, uid = login("importer")
    p = c.post("/api/playlists/import", json={"url": URL, "title": "Party", "spotify_ids": ["spA", "spB", "spC"]}).json()
    assert entries(p["id"]) == [tracks["a"], tracks["c"]]
    # „Holen“ nur für Song B (eigener Job vom Typ „track“)
    job = DownloadManager().add(uid, "https://open.spotify.com/track/spB", kind="track", spotify_ids=["spB"])
    db.execute("UPDATE tracks SET spotify_id = 'spB' WHERE id = ?", (tracks["b"],))
    db.execute("UPDATE downloads SET status = 'done', playlist_synced = 0 WHERE id = ?", (job["id"],))
    playlist_import.sync_jobs()
    assert entries(p["id"]) == [tracks["a"], tracks["b"], tracks["c"]]
    db.execute("DELETE FROM downloads WHERE id = ?", (job["id"],))
