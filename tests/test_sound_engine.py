"""Klang-Engine (Server): Lautheit, echte Spitzen, Stille, Albumpegel und schonendes Umwandeln."""

import os
import time

import pytest
from fastapi.testclient import TestClient

from conftest import FFMPEG, ff
from homify import db, loudness, media

FFMPEG_OUTPUT = """
[silencedetect @ 0x1] silence_start: 0
[silencedetect @ 0x1] silence_end: 1.500042 | silence_duration: 1.500042
[silencedetect @ 0x1] silence_start: 97.2
[silencedetect @ 0x1] silence_end: 97.9 | silence_duration: 0.7
[silencedetect @ 0x1] silence_start: 180.4
[Parsed_ebur128_0 @ 0x2] Summary:
  Integrated loudness:
    I:          -9.3 LUFS
    Threshold: -19.4 LUFS
  True peak:
    Peak:        0.6 dBFS
[silencedetect @ 0x1] silence_end: 183.0 | silence_duration: 2.6
size=N/A time=00:03:03.00 bitrate=N/A speed=80x
"""


@pytest.fixture(scope="module")
def client(scanned):
    from homify import auth
    from homify.server import app

    if not auth.authenticate("klang", "klang1234"):
        auth.create_user("klang", "klang1234")
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "klang", "password": "klang1234"}).status_code == 200
    return c


def test_parse_analysis_finds_loudness_peak_and_silence():
    res = loudness.parse_analysis(FFMPEG_OUTPUT)
    assert res == {"lufs": -9.3, "peak": 0.6, "lead_in": 1.5, "tail": 2.6}
    # Pause mitten im Song zählt nicht, Datei ohne Stille auch nicht
    quiet_middle = "I: -12.0 LUFS\nPeak: -1.0 dBFS\nsilence_start: 50\nsilence_end: 51 | x\ntime=00:02:00.00"
    assert loudness.parse_analysis(quiet_middle) == {"lufs": -12.0, "peak": -1.0, "lead_in": 0.0, "tail": 0.0}
    # komplett stumm oder kaputt -> keine Stille-Kürzung bzw. gar nichts
    silent = "I: -69.0 LUFS\nPeak: -inf dBFS\nsilence_start: 0\nsilence_end: 10 | x\ntime=00:00:10.00"
    res = loudness.parse_analysis(silent)
    assert res["lead_in"] == 0.0 and res["tail"] == 0.0 and res["peak"] is None
    assert loudness.parse_analysis("nichts") is None


def test_analyze_real_file_with_silence(tmp_path):
    path = tmp_path / "stille.opus"
    ff("-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=1.2",
       "-f", "lavfi", "-i", "sine=frequency=440:duration=3:sample_rate=48000",
       "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=2",
       "-filter_complex", "[1]aformat=channel_layouts=stereo,volume=4[t];[0][t][2]concat=n=3:v=0:a=1",
       "-c:a", "libopus", "-b:a", "128k", str(path))
    res = loudness.analyze_file(str(path), 6.2)
    assert res is not None
    assert 1.0 < res["lead_in"] < 1.4 and 1.7 < res["tail"] < 2.3
    assert res["peak"] is not None and -12 < res["peak"] < 3
    assert -30 < res["lufs"] < 0


def test_album_level_is_energy_average():
    gain, peak = loudness.album_values([{"lufs": -10, "peak": -0.5, "duration": 200},
                                        {"lufs": -20, "peak": -6, "duration": 200}])
    assert gain == -5.4 and peak == -0.5  # lauter Song zählt mehr – wie bei ReplayGain-Album
    assert loudness.album_values([{"lufs": None, "duration": 100}]) is None


def test_analyzer_measures_library_and_albums(client, scanned, monkeypatch):
    monkeypatch.setattr(loudness.time, "sleep", lambda _s: None)
    db.execute("UPDATE tracks SET analyzed = 0")
    loudness.analyzer._failed.clear()
    loudness.analyzer._run()
    rows = db.query("SELECT title, analyzed, lufs, peak, lead_in, tail, gain FROM tracks")
    done = [r for r in rows if r["analyzed"] == loudness.ANALYSIS_VERSION]
    assert len(done) >= len(rows) - 1  # höchstens ein exotisches Format darf scheitern
    for r in done:
        assert r["lufs"] is not None and r["gain"] is not None and r["lead_in"] is not None and r["tail"] is not None
    assert loudness.analyzer.remaining() <= 1

    one = db.query_one("SELECT id, album_id FROM tracks WHERE title = 'One More Time'")
    level = db.query_one("SELECT gain, peak FROM album_loudness WHERE album_id = ?", (one["album_id"],))
    assert level is not None and level["peak"] is not None
    album = client.get(f"/api/albums/{one['album_id']}").json()
    track = next(t for t in album["tracks"] if t["id"] == one["id"])
    assert track["peak"] is not None and track["album_peak"] == level["peak"]
    assert track["album_gain"] is not None and track["lead_in"] is not None and "tail" in track


def test_changed_file_is_measured_again(client, scanned, music_dir):
    from homify.scanner import scanner

    path = music_dir / "Loose" / "wave file.wav"
    before = db.query_one("SELECT id FROM tracks WHERE path = ?", (str(path),))
    db.execute("UPDATE tracks SET analyzed = ?, gain = -3.0 WHERE id = ?", (loudness.ANALYSIS_VERSION, before["id"]))
    scanner.scan()  # unverändert: Messwerte bleiben
    row = db.query_one("SELECT analyzed, gain FROM tracks WHERE id = ?", (before["id"],))
    assert row["analyzed"] == loudness.ANALYSIS_VERSION and row["gain"] == -3.0
    stamp = time.time() + 30
    os.utime(path, (stamp, stamp))  # Datei „geändert“
    scanner.scan()
    row = db.query_one("SELECT analyzed, gain FROM tracks WHERE id = ?", (before["id"],))
    assert row["analyzed"] == 0 and row["gain"] is None  # wird neu gemessen


def test_efficient_codecs_are_not_reencoded_for_small_savings(monkeypatch):
    from homify.config import config

    monkeypatch.setitem(config._data, "transcode_low_kbps", 128)
    # Opus 160 kbit/s bei „Niedrig“ (128): nochmal umwandeln würde nur schlechter klingen
    assert not media.needs_transcode_for_quality(160_000, "low", "opus")
    assert not media.needs_transcode_for_quality(160_000, "low", "webm")
    assert media.needs_transcode_for_quality(160_000, "low", "mp3")
    assert media.needs_transcode_for_quality(900_000, "low", "flac")
    assert media.needs_transcode_for_quality(160_000, "minimal", "opus")


def test_low_bitrate_prefers_opus_when_device_can(monkeypatch):
    from homify.config import config

    monkeypatch.setitem(config._data, "transcode_format", "mp3")
    assert media.choose_format("low", client_opus=True) == "opus"
    assert media.choose_format("low", client_opus=False) == "mp3"
    assert media.choose_format("high", client_opus=True) == "mp3"  # 320 kbit/s MP3 ist ohnehin durchsichtig


def test_stream_low_quality_as_opus(client, scanned):
    wav = db.query_one("SELECT id FROM tracks WHERE codec = 'pcm'")  # WAV, 705 kbit/s
    r = client.get(f"/api/tracks/{wav['id']}/stream?quality=low&opus=1")
    assert r.status_code == 200 and r.headers["content-type"].startswith("audio/ogg")
    r = client.get(f"/api/tracks/{wav['id']}/stream?quality=low")
    assert r.status_code == 200 and r.headers["content-type"].startswith("audio/mpeg")
    assert os.path.exists(FFMPEG)
