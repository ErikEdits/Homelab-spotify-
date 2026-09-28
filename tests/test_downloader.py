import sys
import textwrap

import pytest

from homify import db, downloader
from homify.downloader import DownloadManager, build_command, friendly_error, validate_query


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
    assert cmd[1:5] == ["-m", "spotdl", "download", "https://open.spotify.com/track/abc"]
    assert "--output" in cmd and "--ffmpeg" in cmd
    assert "{output-ext}" in cmd[cmd.index("--output") + 1]


def test_friendly_errors():
    assert "YouTube Music" in friendly_error("LookupError: No results found for song: x")
    assert "Internet" in friendly_error("ProxyError: Max retries exceeded")


FAKE_SPOTDL = textwrap.dedent('''
    import sys
    print("Processing query: https://open.spotify.com/album/x")
    print("Found 3 songs in Discovery (Album)")
    print('Downloaded "Daft Punk - One More Time": https://music.youtube.com/watch?v=1')
    print("Skipping Daft Punk - Aerodynamic (file already exists) ")
    print("LookupError: No results found for song: Daft Punk - Digital Love")
    sys.exit(0)
''')


def test_job_progress_parsing(scanned, tmp_path, monkeypatch):
    fake = tmp_path / "fake_spotdl.py"
    fake.write_text(FAKE_SPOTDL, encoding="utf-8")
    monkeypatch.setattr(downloader, "build_command", lambda q: [sys.executable, str(fake)])
    monkeypatch.setattr(downloader, "venv_python", lambda: type("P", (), {"exists": lambda self: True})())
    monkeypatch.setattr(downloader.scanner, "start", lambda *a, **k: True)
    mgr = DownloadManager()
    job = mgr.add(1, "https://open.spotify.com/album/x", kind="album", title="Discovery")
    row = db.query_one("SELECT * FROM downloads WHERE id = ?", (job["id"],))
    mgr._run(row)
    done = mgr.get(job["id"])
    assert done["status"] == "partial"
    assert done["done"] == 2 and done["total"] == 3 and done["failed"] == 1
    assert "YouTube Music" in done["message"]
