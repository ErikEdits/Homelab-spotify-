import os
import sys
import textwrap

import pytest

from homify import db, downloader
from homify.downloader import DownloadManager, build_command, friendly_error, validate_query
from homify.tools import SPOTDL_RUNNER


@pytest.mark.parametrize("query", [
    "https://open.spotify.com/track/0DiWol3AO6WpXZgp0goxAV",
    "https://open.spotify.com/intl-de/album/2noRn2Aes5aoNVsU6iWThc?si=abc",
    "https://music.youtube.com/watch?v=abc",
    "Daft Punk - One More Time",
])
def test_valid_queries(query):
    assert validate_query(query) == query.strip()


@pytest.mark.parametrize("query", ["", "   ", "--help", "-o x", "saved", "all-user-playlists", "x.spotdl"])
def test_invalid_queries(query):
    with pytest.raises(ValueError):
        validate_query(query)


def test_build_command_contains_output_and_ffmpeg(scanned):
    cmd = build_command("https://open.spotify.com/track/abc")
    assert cmd[1:4] == [str(SPOTDL_RUNNER), "download", "https://open.spotify.com/track/abc"]
    assert SPOTDL_RUNNER.exists()
    assert "--output" in cmd and "--ffmpeg" in cmd
    assert "{output-ext}" in cmd[cmd.index("--output") + 1]


def test_friendly_errors():
    assert "YouTube Music" in friendly_error("LookupError: No results found for song: x")
    assert "Internet" in friendly_error("ProxyError: Max retries exceeded")


def test_friendly_error_json_decode_explains_block():
    err = "JSONDecodeError: Expecting value: line 1 column 1 (char 0)"
    msg = friendly_error(err)
    assert "YouTube Music" in msg and "Fehlerseite" in msg and "Expecting value" in msg
    # kam der Fehler aus dem Spotify-Zugang ohne API-Schlüssel, steht das dabei – mit Lösung
    trace = "│ /venv/lib/site-packages/spotapi/http/request.py:181 in parse_response │"
    assert "Spotify blockiert" in friendly_error(err, trace) and "Client ID" in friendly_error(err, trace)
    # YouTube Music gesperrt und auch die Ersatzsuche fand nichts
    notice = "Homify-Hinweis: YouTube Music antwortet nicht (JSONDecodeError) – suche stattdessen über YouTube"
    assert "Ersatzsuche" in friendly_error("LookupError: No results found for song: x", notice)


FAKE_SPOTDL = textwrap.dedent('''
    import os, sys
    staging = sys.argv[1]
    os.makedirs(os.path.join(staging, "Daft Punk", "Fake Album"), exist_ok=True)
    with open(os.path.join(staging, "Daft Punk", "Fake Album", "Daft Punk - Harder Better.mp3"), "wb") as f:
        f.write(b"ID3" + b"x" * 100)
    print("Processing query: https://open.spotify.com/album/x")
    print("Found 3 songs in Discovery (Album)")
    print('Downloaded "Daft Punk - Harder Better": https://music.youtube.com/watch?v=1')
    print("Skipping Daft Punk - Aerodynamic (file already exists) ")
    print("LookupError: No results found for song: Daft Punk - Digital Love")
    sys.exit(0)
''')


def test_job_progress_parsing(scanned, tmp_path, monkeypatch, music_dir):
    fake = tmp_path / "fake_spotdl.py"
    fake.write_text(FAKE_SPOTDL, encoding="utf-8")
    seen = {}

    def fake_command(q, staging=None, archive=None):
        seen["archive"] = archive
        return [sys.executable, str(fake), staging]

    monkeypatch.setattr(downloader, "build_command", fake_command)
    monkeypatch.setattr(downloader, "venv_python", lambda: type("P", (), {"exists": lambda self: True})())
    monkeypatch.setattr(downloader.scanner, "start", lambda *a, **k: True)
    monkeypatch.setattr(downloader.time, "sleep", lambda s: None)
    monkeypatch.setitem(downloader.config._data, "download_retries", 1)
    mgr = DownloadManager()
    job = mgr.add(1, "https://open.spotify.com/album/x", kind="album", title="Discovery")
    row = db.query_one("SELECT * FROM downloads WHERE id = ?", (job["id"],))
    mgr._run(row)
    # 1. Versuch teilweise erfolgreich -> wird automatisch wiederholt
    first = mgr.get(job["id"])
    assert first["status"] == "queued" and first["attempts"] == 1
    assert first["message"].startswith("Neuer Versuch")
    mgr._run(db.query_one("SELECT * FROM downloads WHERE id = ?", (job["id"],)))
    done = mgr.get(job["id"])
    assert done["status"] == "partial" and done["attempts"] == 2
    assert done["done"] == 2 and done["total"] == 3 and done["failed"] == 1
    assert "YouTube Music" in done["message"]
    # Datei wurde aus dem Zwischenordner in den Speicherort verschoben
    moved = music_dir / "Daft Punk" / "Fake Album" / "Daft Punk - Harder Better.mp3"
    assert moved.exists()
    moved.unlink()
    moved.parent.rmdir()
    assert not (downloader.STAGING_DIR / f"job-{job['id']}").exists()
    assert seen["archive"] and not os.path.exists(seen["archive"])


def test_build_command_follows_settings(scanned, monkeypatch):
    from homify.config import config

    for key, value in {"download_lyrics": True, "sponsor_block": True, "skip_explicit": True,
                       "filename_restrict": "ascii", "audio_providers": "youtube-music youtube"}.items():
        monkeypatch.setitem(config._data, key, value)
    cmd = build_command("https://open.spotify.com/track/abc")
    assert "--generate-lrc" in cmd and "--sponsor-block" in cmd and "--skip-explicit" in cmd
    assert cmd[cmd.index("--restrict") + 1] == "ascii"
    assert cmd[cmd.index("--audio") + 1: cmd.index("--audio") + 3] == ["youtube-music", "youtube"]
    monkeypatch.setitem(config._data, "download_lyrics", False)
    monkeypatch.setitem(config._data, "filename_restrict", "none")
    cmd = build_command("https://open.spotify.com/track/abc")
    assert "--generate-lrc" not in cmd and "--restrict" not in cmd


def test_daily_download_limit(scanned, monkeypatch):
    from homify import auth
    from homify.config import config

    uid = auth.create_user("limitiert", "limit1234")
    db.execute("DELETE FROM downloads WHERE user_id = ?", (uid,))  # Reste anderer Tests (gleiche ID möglich)
    monkeypatch.setitem(config._data, "daily_download_limit", 2)
    mgr = DownloadManager()
    mgr.add(uid, "Künstler - Song 1")
    mgr.add(uid, "Künstler - Song 2")
    with pytest.raises(ValueError, match="Tageslimit"):
        mgr.add(uid, "Künstler - Song 3")
    db.execute("DELETE FROM downloads WHERE user_id = ?", (uid,))


def test_build_command_uses_staging_and_archive(scanned, tmp_path):
    cmd = build_command("https://open.spotify.com/track/abc", staging=str(tmp_path), archive="a.txt")
    assert cmd[cmd.index("--output") + 1].startswith(str(tmp_path))
    assert cmd[cmd.index("--archive") + 1] == "a.txt"


FAKE_DUPLICATE = textwrap.dedent('''
    import os, shutil, sys
    staging, src = sys.argv[1], sys.argv[2]
    os.makedirs(os.path.join(staging, "Daft Punk", "Anderes Album"), exist_ok=True)
    # Derselbe Song wie in der Bibliothek, nur „Remastered“ und woanders abgelegt – plus Songtext
    shutil.copy(src, os.path.join(staging, "Daft Punk", "Anderes Album", "Daft Punk - One More Time.mp3"))
    open(os.path.join(staging, "Daft Punk", "Anderes Album", "Daft Punk - One More Time.lrc"), "w").write("[00:01.00]x")
    print('Downloaded "Daft Punk - One More Time (Remastered)": https://music.youtube.com/watch?v=2')
''')


def test_download_of_existing_song_is_not_saved_twice(scanned, tmp_path, monkeypatch, music_dir):
    fake = tmp_path / "fake_dup.py"
    fake.write_text(FAKE_DUPLICATE, encoding="utf-8")
    src = music_dir / "Daft Punk" / "Discovery" / "01 One More Time.mp3"
    monkeypatch.setattr(downloader, "build_command", lambda q, staging=None, archive=None: [sys.executable, str(fake), staging, str(src)])
    monkeypatch.setattr(downloader, "venv_python", lambda: type("P", (), {"exists": lambda self: True})())
    monkeypatch.setattr(downloader.scanner, "start", lambda *a, **k: True)
    before = db.query_one("SELECT COUNT(*) AS n FROM tracks")["n"]
    mgr = DownloadManager()
    job = mgr.add(1, "Daft Punk - One More Time", kind="search")
    mgr._run(db.query_one("SELECT * FROM downloads WHERE id = ?", (job["id"],)))
    done = mgr.get(job["id"])
    assert done["status"] == "done"
    assert "schon" in done["message"].lower()
    existing = db.query_one("SELECT id FROM tracks WHERE title = 'One More Time'")["id"]
    assert done["known_ids"] == [existing]
    assert not (music_dir / "Daft Punk" / "Anderes Album").exists()  # weder Song noch Songtext gespeichert
    assert db.query_one("SELECT COUNT(*) AS n FROM tracks")["n"] == before
    db.execute("DELETE FROM downloads WHERE id = ?", (job["id"],))


def test_everything_known_skips_spotdl(scanned, monkeypatch):
    tid = db.query_one("SELECT id FROM tracks WHERE title = 'Aerodynamic'")["id"]
    monkeypatch.setattr(downloader, "resolve_tracks", lambda q: [{"id": "spX1", "library_id": tid}])
    monkeypatch.setattr(downloader, "venv_python", lambda: type("P", (), {"exists": lambda self: True})())
    called = []
    monkeypatch.setattr(downloader, "build_command", lambda *a, **k: called.append(1) or ["false"])
    mgr = DownloadManager()
    job = mgr.add(1, "https://open.spotify.com/track/spX1", kind="track", spotify_ids=["spX1"])
    mgr._run(db.query_one("SELECT * FROM downloads WHERE id = ?", (job["id"],)))
    done = mgr.get(job["id"])
    assert not called
    assert done["status"] == "done" and done["known_ids"] == [tid]
    assert "schon in deiner bibliothek" in done["message"].lower()
    db.execute("DELETE FROM downloads WHERE id = ?", (job["id"],))
