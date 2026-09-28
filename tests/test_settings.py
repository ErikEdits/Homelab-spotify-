"""Die 100 Einstellungen: Schema, Speichern, Prüfen – und dass sie wirklich wirken."""

import shutil

import pytest
from fastapi.testclient import TestClient
from mutagen.id3 import ID3, TXXX, USLT

from homify import db
from homify.config import config
from homify.settings_schema import BY_KEY, SETTINGS, USER_SETTINGS, SERVER_SETTINGS, validate


@pytest.fixture(scope="module")
def client(scanned):
    from homify import auth
    from homify.server import app

    if not auth.authenticate("setadmin", "setadmin1"):
        auth.create_user("setadmin", "setadmin1", is_admin=True)
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "setadmin", "password": "setadmin1"}).status_code == 200
    return c


def test_exactly_100_settings():
    assert len(SETTINGS) == 100
    assert len({s.key for s in SETTINGS}) == 100
    assert len(USER_SETTINGS) + len(SERVER_SETTINGS) == 100
    for s in SETTINGS:
        assert s.label and s.category
        assert validate(s, s.default) == s.default, s.key
        if s.type == "select":
            assert s.default in [v for v, _ in s.options], s.key


def test_validate_clamps_and_rejects():
    assert validate(BY_KEY["crossfade"], 99) == 12
    assert validate(BY_KEY["crossfade"], -3) == 0
    assert validate(BY_KEY["gapless"], "false") is False
    with pytest.raises(ValueError):
        validate(BY_KEY["theme"], "neon")
    with pytest.raises(ValueError):
        validate(BY_KEY["crossfade"], "viel")


def test_schema_endpoint(client):
    data = client.get("/api/settings/schema").json()
    assert data["count"] == 100
    assert "rock" in data["eq_presets"]


def test_user_settings_roundtrip(client):
    values = client.get("/api/me/settings").json()
    assert values["crossfade"] == 0 and values["theme"] == "dark"
    r = client.put("/api/me/settings", json={"crossfade": 6, "theme": "black", "eq_1k": 40})
    assert r.status_code == 200
    values = r.json()
    assert values["crossfade"] == 6 and values["theme"] == "black" and values["eq_1k"] == 12
    assert client.put("/api/me/settings", json={"theme": "neon"}).status_code == 400
    assert client.put("/api/me/settings", json={"port": 1}).status_code == 400  # Server-Einstellung
    values = client.put("/api/me/settings", json={"crossfade": None}).json()  # zurücksetzen
    assert values["crossfade"] == 0
    assert client.delete("/api/me/settings").json()["theme"] == "dark"


def test_user_settings_are_per_user(client, scanned):
    from homify import auth, user_prefs

    uid = auth.create_user("anderer", "anderer1")
    client.put("/api/me/settings", json={"accent": "pink"})
    assert user_prefs.get(uid, "accent") == "green"
    client.delete("/api/me/settings")


def test_server_settings_validation(client):
    assert client.put("/api/settings", json={"transcode_format": "wav"}).status_code == 400
    r = client.put("/api/settings", json={"server_name": "Eriks Musik", "scan_workers": 99})
    assert r.status_code == 200
    s = r.json()["settings"]
    assert s["server_name"] == "Eriks Musik" and s["scan_workers"] == 16
    assert client.get("/api/setup").json()["name"] == "Eriks Musik"
    assert client.get("/api/auth/me").json()["server"]["name"] == "Eriks Musik"
    client.put("/api/settings", json={"server_name": "Homify", "scan_workers": 6})


def test_ignore_folders_and_min_length(scanned, music_dir, monkeypatch):
    from homify.scanner import scanner

    before = db.query_one("SELECT COUNT(*) AS n FROM tracks")["n"]
    monkeypatch.setitem(config._data, "ignore_folders", "Various")
    scanner.scan(full=True)
    assert db.query_one("SELECT COUNT(*) AS n FROM tracks WHERE album = 'Mix'")["n"] == 0
    monkeypatch.setitem(config._data, "ignore_folders", "")
    monkeypatch.setitem(config._data, "min_track_seconds", 4)  # Testdateien sind 3–4 s lang
    scanner.scan(full=True)
    assert db.query_one("SELECT COUNT(*) AS n FROM tracks WHERE duration < 3.9")["n"] == 0
    monkeypatch.setitem(config._data, "min_track_seconds", 0)
    scanner.scan(full=True)
    assert db.query_one("SELECT COUNT(*) AS n FROM tracks")["n"] == before


def test_safety_limit_blocks_mass_removal(scanned, music_dir, tmp_path, monkeypatch):
    from homify.scanner import scanner

    before = db.query_one("SELECT COUNT(*) AS n FROM tracks")["n"]
    moved = tmp_path / "weg"
    shutil.move(str(music_dir / "Loose"), moved)  # „NAS-Aussetzer“: ein ganzer Ordner fehlt
    try:
        monkeypatch.setitem(config._data, "max_remove_percent", 10)
        result = scanner.scan()
        assert "Sicherheitsgrenze" in result["message"]
        assert db.query_one("SELECT COUNT(*) AS n FROM tracks")["n"] == before
    finally:
        shutil.move(str(moved), music_dir / "Loose")
        scanner.scan()


def test_replaygain_and_lyrics(client, scanned, music_dir):
    from homify.scanner import scanner

    mp3 = music_dir / "Daft Punk" / "Discovery" / "01 One More Time.mp3"
    tags = ID3(mp3)
    tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=["-7.25 dB"]))
    tags.add(USLT(encoding=3, lang="deu", desc="", text="Erste Zeile\nZweite Zeile"))
    tags.save(mp3)
    (music_dir / "Daft Punk" / "Discovery" / "02 Aerodynamic.lrc").write_text(
        "[ti:Aerodynamic]\n[00:01.00]Hallo\n[00:02.50]Welt\n", encoding="utf-8")
    scanner.scan()
    one = db.query_one("SELECT id, gain FROM tracks WHERE title = 'One More Time'")
    assert one["gain"] == -7.25
    lyr = client.get(f"/api/tracks/{one['id']}/lyrics").json()
    assert lyr["synced"] is False and lyr["lines"][0]["text"] == "Erste Zeile"
    aero = db.query_one("SELECT id FROM tracks WHERE title = 'Aerodynamic'")
    lyr = client.get(f"/api/tracks/{aero['id']}/lyrics").json()
    assert lyr["synced"] is True and lyr["lines"] == [{"t": 1.0, "text": "Hallo"}, {"t": 2.5, "text": "Welt"}]
    wma = db.query_one("SELECT id FROM tracks WHERE codec = 'wma'")
    assert client.get(f"/api/tracks/{wma['id']}/lyrics").status_code == 404


def test_loudness_measurement(scanned, music_dir):
    from homify.loudness import gain_for, measure_file

    lufs = measure_file(str(music_dir / "Loose" / "wave file.wav"))
    assert lufs is not None and -40 < lufs < 0
    assert gain_for(-9.0) == -9.0 and gain_for(-30.0) == 12.0


@pytest.mark.parametrize("fmt,mime", [("aac", "audio/mp4"), ("opus", "audio/ogg"), ("mp3", "audio/mpeg")])
def test_transcode_formats(client, fmt, mime, monkeypatch):
    monkeypatch.setitem(config._data, "transcode_format", fmt)
    wma = db.query_one("SELECT id FROM tracks WHERE codec = 'wma'")
    r = client.get(f"/api/tracks/{wma['id']}/stream", params={"transcode": 1, "quality": "low"})
    assert r.status_code == 200 and r.headers["content-type"] == mime and len(r.content) > 500


def test_quality_levels():
    from homify import media

    assert media.needs_transcode_for_quality(1_000_000, "high")      # FLAC -> 320
    assert not media.needs_transcode_for_quality(320_000, "high")
    assert media.needs_transcode_for_quality(320_000, "low")
    assert not media.needs_transcode_for_quality(96_000, "low")


def test_file_download_switch(client, monkeypatch):
    from homify import auth

    if not auth.authenticate("nurhoerer", "nurhoerer1"):
        auth.create_user("nurhoerer", "nurhoerer1")
    user = TestClient(client.app)
    user.post("/api/auth/login", json={"username": "nurhoerer", "password": "nurhoerer1"})
    tid = db.query_one("SELECT id FROM tracks LIMIT 1")["id"]
    monkeypatch.setitem(config._data, "allow_file_download", False)
    assert user.get(f"/api/tracks/{tid}/file").status_code == 403
    monkeypatch.setitem(config._data, "allow_file_download", True)
    assert user.get(f"/api/tracks/{tid}/file").status_code == 200


def test_backups(client, monkeypatch):
    from homify import backup

    monkeypatch.setitem(config._data, "backup_keep", 2)
    for _ in range(3):
        backup.create_file()
        backup.BACKUP_DIR.joinpath("x").touch()
    names = {b["name"] for b in backup.list_backups()}
    assert names
    backup.prune(2)
    assert len(backup.list_backups()) <= 2
    r = client.post("/api/backups")
    assert r.status_code == 200
    name = r.json()["name"]
    assert client.get(f"/api/backups/{name}").status_code == 200
    assert client.get("/api/backups/..%2Fconfig.json").status_code in (400, 404)


def test_new_users_can_download_default(scanned, monkeypatch):
    from homify import auth

    monkeypatch.setitem(config._data, "new_users_can_download", False)
    uid = auth.create_user("keinload", "keinload1")
    assert db.query_one("SELECT can_download FROM users WHERE id = ?", (uid,))["can_download"] == 0


def test_auto_like_downloads(client, scanned):
    import time

    from homify import user_prefs

    uid = client.get("/api/auth/me").json()["id"]
    tid = db.query_one("SELECT id FROM tracks WHERE title = 'Aerodynamic'")["id"]
    db.execute("UPDATE tracks SET spotify_id = 'spAuto1' WHERE id = ?", (tid,))
    add_job = lambda q: db.execute(  # noqa: E731
        "INSERT INTO downloads (user_id, query, status, spotify_ids, created_at, finished_at) "
        "VALUES (?, ?, 'done', '[\"spAuto1\"]', ?, ?)", (uid, q, time.time(), time.time()))
    liked = lambda: db.query_one("SELECT 1 AS x FROM likes WHERE user_id = ? AND track_id = ?", (uid, tid))  # noqa: E731
    try:
        add_job("alt")  # fertig, während die Einstellung aus ist -> kein Like
        client.get("/api/downloads")
        assert not liked()
        user_prefs.update(uid, {"auto_like_downloads": True})
        client.get("/api/downloads")
        assert not liked()  # alter Download wird nicht nachträglich geliked
        add_job("neu")
        jobs = client.get("/api/downloads").json()["jobs"]
        assert any(j["query"] == "neu" and j["track_ids"] == [tid] for j in jobs)
        assert liked()
    finally:
        user_prefs.update(uid, {"auto_like_downloads": None})
        db.execute("DELETE FROM likes WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM downloads WHERE query IN ('alt', 'neu')")
        db.execute("UPDATE tracks SET spotify_id = '' WHERE id = ?", (tid,))


def test_lyrics_parser():
    from homify.lyrics import parse_lyrics

    res = parse_lyrics("[offset:+500]\n[00:10.00][00:20.00]Refrain\n[00:05.5]Start")
    assert res["synced"] and [x["t"] for x in res["lines"]] == [5.0, 9.5, 19.5]
    assert parse_lyrics("")["lines"] == []
