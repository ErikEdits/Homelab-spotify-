"""Test-Setup: eigenes Datenverzeichnis + kleine Musikbibliothek in vielen Formaten."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Muss vor dem ersten Import von homify passieren (Konfiguration wird beim Import gelesen)
_DATA = Path(tempfile.mkdtemp(prefix="homify-test-data-"))
_MUSIC = Path(tempfile.mkdtemp(prefix="homify-test-music-"))
os.environ["HOMIFY_DATA"] = str(_DATA)
os.environ["HOMIFY_MUSIC_DIRS"] = str(_MUSIC)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import imageio_ffmpeg  # noqa: E402
from PIL import Image  # noqa: E402

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def ff(*args):
    subprocess.run([FFMPEG, "-v", "error", "-y", *args], check=True)


def tone(freq, seconds=3):
    return ["-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}"]


def build_library(root: Path) -> dict:
    """Erzeugt Testdateien: MP3 mit Cover, FLAC, AAC, ALAC, Opus, Vorbis ohne Tags, WAV, WMA, WebM."""
    album = root / "Daft Punk" / "Discovery"
    album.mkdir(parents=True)
    (root / "Various").mkdir()
    (root / "Loose").mkdir()
    (root / "@eaDir").mkdir()
    cover = album / "cover.jpg"  # dient als eingebettetes Cover UND als Ordner-Cover
    Image.new("RGB", (400, 400), (200, 40, 60)).save(cover)

    ff(*tone(440), "-i", str(cover), "-map", "0", "-map", "1", "-c:a", "libmp3lame", "-b:a", "128k",
       "-id3v2_version", "3", "-metadata", "title=One More Time", "-metadata", "artist=Daft Punk",
       "-metadata", "album=Discovery", "-metadata", "album_artist=Daft Punk", "-metadata", "track=1/14",
       "-metadata", "date=2001", "-metadata", "genre=Electronic", "-disposition:v", "attached_pic",
       str(album / "01 One More Time.mp3"))
    ff(*tone(550), "-c:a", "flac", "-metadata", "title=Aerodynamic", "-metadata", "artist=Daft Punk",
       "-metadata", "album=Discovery", "-metadata", "album_artist=Daft Punk", "-metadata", "track=2",
       "-metadata", "genre=Electronic", str(album / "02 Aerodynamic.flac"))
    ff(*tone(660), "-c:a", "aac", "-b:a", "128k", "-metadata", "title=Song A", "-metadata", "artist=Artist X",
       "-metadata", "album=Mix", str(root / "Various" / "a.m4a"))
    ff(*tone(770), "-c:a", "alac", "-metadata", "title=Song B", "-metadata", "artist=Artist Y",
       "-metadata", "album=Mix", str(root / "Various" / "b.m4a"))
    ff(*tone(880), "-c:a", "libopus", "-metadata", "title=Opus Song", "-metadata", "artist=Opus Artist",
       str(root / "Loose" / "opus.opus"))
    ff(*tone(990), "-c:a", "libvorbis", str(root / "Loose" / "Some Artist - Untagged Vorbis.ogg"))
    ff(*tone(300), "-c:a", "pcm_s16le", str(root / "Loose" / "wave file.wav"))
    ff(*tone(350), "-c:a", "wmav2", "-metadata", "title=WMA Song", "-metadata", "artist=WMA Artist",
       str(root / "Loose" / "song.wma"))
    ff(*tone(400), "-c:a", "libopus", str(root / "Loose" / "Web Artist - webm song.webm"))
    shutil.copy(root / "Loose" / "wave file.wav", root / "@eaDir" / "thumb.wav")
    return {"count": 9}


@pytest.fixture(scope="session")
def music_dir():
    if not any(_MUSIC.iterdir()):
        build_library(_MUSIC)
    return _MUSIC


@pytest.fixture(scope="session")
def data_dir():
    return _DATA


@pytest.fixture(scope="session")
def scanned(music_dir):
    from homify import db
    from homify.scanner import scanner

    db.init()
    result = scanner.scan()
    return result


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_DATA, ignore_errors=True)
    shutil.rmtree(_MUSIC, ignore_errors=True)
