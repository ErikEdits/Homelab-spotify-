import os
import shutil
import time

from homify import db
from homify.config import config
from homify.scanner import scanner


def test_scan_finds_all_formats(scanned):
    assert scanned["files"] == 9  # @eaDir wird übersprungen
    assert scanned["added"] == 9
    codecs = {r["codec"] for r in db.query("SELECT codec FROM tracks")}
    assert {"mp3", "flac", "aac", "alac", "opus", "vorbis", "pcm", "wma"} <= codecs


def test_albums_artists_and_covers(scanned):
    album = db.query_one("SELECT * FROM albums WHERE name = 'Discovery'")
    assert album["artist"] == "Daft Punk"
    assert album["track_count"] == 2
    assert album["year"] == 2001
    assert album["cover_id"]
    mix = db.query_one("SELECT * FROM albums WHERE name = 'Mix'")
    assert mix["artist"] == "Verschiedene Interpreten"
    # FLAC ohne eingebettetes Cover bekommt das cover.jpg aus dem Ordner
    flac = db.query_one("SELECT cover_id FROM tracks WHERE title = 'Aerodynamic'")
    assert flac["cover_id"]
    webm = db.query_one("SELECT duration FROM tracks WHERE path LIKE '%.webm'")
    assert webm["duration"] > 2  # Dauer über ffmpeg ermittelt


def test_rescan_is_incremental(scanned):
    result = scanner.scan()
    assert result["added"] == 0 and result["updated"] == 0 and result["removed"] == 0


def test_moved_file_keeps_playlist_and_like(scanned, music_dir):
    from homify import auth

    uid = auth.create_user("mover", "secret12")
    track = db.query_one("SELECT id, path FROM tracks WHERE title = 'Opus Song'")
    now = time.time()
    db.execute("INSERT INTO likes (user_id, track_id, liked_at) VALUES (?, ?, ?)", (uid, track["id"], now))
    cur = db.execute("INSERT INTO playlists (user_id, name, created_at, updated_at) VALUES (?, 'P', ?, ?)", (uid, now, now))
    db.execute("INSERT INTO playlist_tracks (playlist_id, track_id, position, added_at) VALUES (?, ?, 1, ?)",
               (cur.lastrowid, track["id"], now))

    new_dir = music_dir / "Moved"
    new_dir.mkdir(exist_ok=True)
    shutil.move(track["path"], new_dir / "opus.opus")
    result = scanner.scan()
    assert result["added"] == 1 and result["removed"] == 1

    moved = db.query_one("SELECT id FROM tracks WHERE title = 'Opus Song'")
    assert moved["id"] != track["id"]
    assert db.query_one("SELECT track_id FROM likes WHERE user_id = ?", (uid,))["track_id"] == moved["id"]
    assert db.query_one("SELECT track_id FROM playlist_tracks WHERE playlist_id = ?", (cur.lastrowid,))["track_id"] == moved["id"]


def test_offline_nas_does_not_wipe_library(scanned, music_dir, tmp_path):
    before = db.query_one("SELECT COUNT(*) AS n FROM tracks")["n"]
    original = config.get("music_dirs")
    # Musikordner „verschwindet“ (NAS aus): Pfad existiert nicht mehr
    offline = str(tmp_path / "gone")
    try:
        db.execute("UPDATE tracks SET root = ?", (offline,))
        config._data["music_dirs"] = [offline]
        result = scanner.scan()
        assert offline in result["offline_roots"]
        assert db.query_one("SELECT COUNT(*) AS n FROM tracks")["n"] == before
        # Leer gemounteter Ordner (Ubuntu: Mountpoint ohne NAS) -> ebenfalls nichts löschen
        empty = tmp_path / "empty_mount"
        empty.mkdir()
        db.execute("UPDATE tracks SET root = ?", (str(empty),))
        config._data["music_dirs"] = [str(empty)]
        result = scanner.scan()
        assert str(empty) in result["offline_roots"]
        assert db.query_one("SELECT COUNT(*) AS n FROM tracks")["n"] == before
    finally:
        config._data["music_dirs"] = original
        db.execute("UPDATE tracks SET root = ?", (os.path.abspath(original[0]),))
        scanner.scan()
