"""Jeder Song nur einmal in der Bibliothek – doppelte Dateien werden ausgeblendet, nie gelöscht."""

import shutil

import pytest

from conftest import ff
from homify import db, dedup
from homify.textutil import dup_key


@pytest.mark.parametrize("a,b,same", [
    (("One More Time", "Daft Punk"), ("One More Time (Remastered 2011)", "Daft Punk"), True),
    (("One More Time", "Daft Punk"), ("One More Time - 2011 Remaster", "Daft Punk"), True),
    (("One More Time", "Daft Punk"), ("One More Time (feat. Romanthony)", "Daft Punk, Romanthony"), True),
    (("Mr. Brightside", "The Killers"), ("Mr Brightside", "The Killers"), True),
    (("Über den Wolken", "Reinhard Mey"), ("Uber den Wolken", "Reinhard Mey"), True),
    (("One More Time", "Daft Punk"), ("One More Time (Remix)", "Daft Punk"), False),
    (("One More Time", "Daft Punk"), ("One More Time - Live", "Daft Punk"), False),
    (("One More Time", "Daft Punk"), ("One More Time", "Other Artist"), False),
])
def test_dup_key(a, b, same):
    assert (dup_key(*a) == dup_key(*b)) is same


def count_tracks():
    return db.query_one("SELECT COUNT(*) AS n FROM tracks")["n"]


def one_more_time_ids():
    return [r["id"] for r in db.query("SELECT id FROM tracks WHERE title = 'One More Time'")]


def test_duplicate_files_are_hidden_and_best_version_wins(scanned, music_dir):
    from homify.scanner import scanner

    before = count_tracks()
    original = one_more_time_ids()
    assert len(original) == 1
    uid = db.query_one("SELECT id FROM users LIMIT 1") or {"id": None}
    if uid["id"] is None:
        from homify import auth
        uid = {"id": auth.create_user("dedupuser", "dedup1234")}
    db.execute("INSERT OR IGNORE INTO likes (user_id, track_id, liked_at) VALUES (?, ?, 1)", (uid["id"], original[0]))
    src = music_dir / "Daft Punk" / "Discovery" / "01 One More Time.mp3"
    extra = music_dir / "Doppelt"
    extra.mkdir()
    try:
        # 1) Gleiche Datei nochmal an anderer Stelle -> ausgeblendet, Original bleibt
        shutil.copy(src, extra / "One More Time.mp3")
        scanner.scan()
        assert count_tracks() == before
        assert one_more_time_ids() == original
        assert dedup.count() == 1

        # 2) Bessere Version (FLAC) kommt dazu -> ersetzt die MP3, Like wandert mit
        ff("-i", str(src), "-map", "0:a", "-c:a", "flac", str(extra / "One More Time.flac"))
        scanner.scan()
        assert count_tracks() == before
        [kept] = one_more_time_ids()
        assert db.query_one("SELECT codec FROM tracks WHERE id = ?", (kept,))["codec"] == "flac"
        assert dedup.count() == 2
        assert db.query_one("SELECT 1 AS x FROM likes WHERE user_id = ? AND track_id = ?", (uid["id"], kept))
        assert dedup.find_track("One More Time (Remastered)", "Daft Punk", 3.0) == kept

        # 3) FLAC gelöscht -> eine der MP3-Dateien rückt nach, Like bleibt erhalten
        (extra / "One More Time.flac").unlink()
        scanner.scan()
        assert count_tracks() == before
        [kept] = one_more_time_ids()
        assert dedup.count() == 1
        assert db.query_one("SELECT 1 AS x FROM likes WHERE user_id = ? AND track_id = ?", (uid["id"], kept))
    finally:
        shutil.rmtree(extra, ignore_errors=True)
        scanner.scan()
    assert count_tracks() == before
    assert dedup.count() == 0
    [kept] = one_more_time_ids()
    assert db.query_one("SELECT 1 AS x FROM likes WHERE user_id = ? AND track_id = ?", (uid["id"], kept))
    db.execute("DELETE FROM likes WHERE user_id = ? AND track_id = ?", (uid["id"], kept))


def test_remix_is_not_a_duplicate(scanned, music_dir):
    from homify.scanner import scanner

    before = count_tracks()
    extra = music_dir / "Remixe"
    extra.mkdir()
    try:
        ff("-f", "lavfi", "-i", "sine=frequency=500:duration=3", "-c:a", "libmp3lame", "-metadata",
           "title=One More Time (Remix)", "-metadata", "artist=Daft Punk", str(extra / "remix.mp3"))
        scanner.scan()
        assert count_tracks() == before + 1
        assert dedup.count() == 0
    finally:
        shutil.rmtree(extra, ignore_errors=True)
        scanner.scan()
    assert count_tracks() == before


def test_duplicates_api_lists_and_deletes(scanned, music_dir):
    from fastapi.testclient import TestClient

    from homify import auth
    from homify.scanner import scanner
    from homify.server import app

    if not auth.authenticate("dupadmin", "dupadmin1"):
        auth.create_user("dupadmin", "dupadmin1", is_admin=True)
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "dupadmin", "password": "dupadmin1"}).status_code == 200
    extra = music_dir / "Kopie"
    extra.mkdir()
    copy = extra / "Aerodynamic Kopie.flac"
    shutil.copy(music_dir / "Daft Punk" / "Discovery" / "02 Aerodynamic.flac", copy)
    try:
        scanner.scan()
        data = c.get("/api/duplicates").json()
        assert data["count"] == 1 and data["items"][0]["title"] == "Aerodynamic"
        assert data["items"][0]["kept_path"].endswith("02 Aerodynamic.flac")
        assert c.get("/api/settings").json()["system"]["duplicates"] == 1
        r = c.post("/api/duplicates/delete", json={"paths": [data["items"][0]["path"]]}).json()
        assert r["deleted"] == 1 and r["count"] == 0
        assert not copy.exists()
        assert (music_dir / "Daft Punk" / "Discovery" / "02 Aerodynamic.flac").exists()  # Original bleibt
    finally:
        shutil.rmtree(extra, ignore_errors=True)
        scanner.scan()
