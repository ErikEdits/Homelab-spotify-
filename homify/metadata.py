"""Liest Titel, Interpreten, Album, Cover usw. direkt aus den Audiodateien (mutagen)."""

from __future__ import annotations

import base64
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

import mutagen
from mutagen.apev2 import APEv2
from mutagen.asf import ASFTags
from mutagen.flac import Picture
from mutagen.id3 import ID3
from mutagen.mp4 import MP4Tags

from .textutil import parse_int, parse_year

AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".m4b", ".mp4", ".aac", ".flac", ".ogg", ".oga", ".opus",
    ".wav", ".wma", ".ape", ".wv", ".mpc", ".aif", ".aiff", ".aifc", ".dsf",
    ".dff", ".tta", ".tak", ".webm", ".mka", ".spx", ".ofr",
}

# MIME-Typen nach mutagen-Klasse. Der Browser entscheidet per canPlayType(),
# ob er die Datei direkt abspielen kann – sonst wird serverseitig umgewandelt.
_MIME_BY_CLASS = {
    "MP3": ("mp3", "audio/mpeg"),
    "EasyMP3": ("mp3", "audio/mpeg"),
    "FLAC": ("flac", "audio/flac"),
    "OggVorbis": ("vorbis", 'audio/ogg; codecs="vorbis"'),
    "OggOpus": ("opus", 'audio/ogg; codecs="opus"'),
    "OggFLAC": ("flac", 'audio/ogg; codecs="flac"'),
    "OggSpeex": ("speex", 'audio/ogg; codecs="speex"'),
    "WAVE": ("pcm", "audio/wav"),
    "AIFF": ("aiff", "audio/aiff"),
    "AAC": ("aac", "audio/aac"),
    "ASF": ("wma", "audio/x-ms-wma"),
    "MonkeysAudio": ("ape", "audio/x-ape"),
    "WavPack": ("wavpack", "audio/x-wavpack"),
    "Musepack": ("musepack", "audio/x-musepack"),
    "TrueAudio": ("tta", "audio/x-tta"),
    "TAK": ("tak", "audio/x-tak"),
    "DSF": ("dsd", "audio/x-dsf"),
    "DSDIFF": ("dsd", "audio/x-dff"),
    "OptimFROG": ("ofr", "audio/x-optimfrog"),
}

_MIME_BY_EXT = {
    ".webm": ("webm", 'audio/webm; codecs="opus"'),
    ".mka": ("matroska", "audio/x-matroska"),
    ".mp3": ("mp3", "audio/mpeg"),
    ".flac": ("flac", "audio/flac"),
    ".wav": ("pcm", "audio/wav"),
    ".ogg": ("vorbis", "audio/ogg"),
    ".opus": ("opus", 'audio/ogg; codecs="opus"'),
    ".m4a": ("aac", "audio/mp4"),
}


@dataclass
class TrackInfo:
    title: str = ""
    artists: list[str] = field(default_factory=list)
    album: str = ""
    album_artist: str = ""
    track_no: int = 0
    disc_no: int = 0
    year: int = 0
    genre: str = ""
    duration: float = 0.0
    bitrate: int = 0
    sample_rate: int = 0
    codec: str = ""
    mime: str = ""
    isrc: str = ""
    source_url: str = ""
    cover: bytes | None = None
    readable: bool = True


# --------------------------------------------------------------------------- #
# Formatabhängige Tag-Leser
# --------------------------------------------------------------------------- #

def _clean(values) -> list[str]:
    out: list[str] = []
    for v in values or []:
        if v is None:
            continue
        if isinstance(v, bytes):
            v = v.decode("utf-8", "replace")
        s = str(v).replace("\x00", ";").strip()
        if s:
            out.append(s)
    return out


class _Reader:
    def text(self, key: str) -> list[str]:  # pragma: no cover - Interface
        return []

    def first(self, key: str) -> str:
        values = self.text(key)
        return values[0] if values else ""

    def cover(self) -> bytes | None:
        return None

    def source_url(self) -> str:
        return ""


class _ID3Reader(_Reader):
    FRAMES = {
        "title": "TIT2", "artist": "TPE1", "album": "TALB", "albumartist": "TPE2",
        "track": "TRCK", "disc": "TPOS", "date": "TDRC", "genre": "TCON", "isrc": "TSRC",
        "origdate": "TDOR", "year": "TYER",
    }

    def __init__(self, tags: ID3):
        self.tags = tags

    def text(self, key: str) -> list[str]:
        frame_id = self.FRAMES.get(key)
        if not frame_id:
            return []
        values: list[str] = []
        for frame in self.tags.getall(frame_id):
            if frame_id == "TCON" and hasattr(frame, "genres"):
                values.extend(frame.genres)
            else:
                values.extend(str(t) for t in getattr(frame, "text", []))
        if key == "albumartist" and not values:
            for frame in self.tags.getall("TXXX"):
                if frame.desc.lower() in ("album artist", "albumartist"):
                    values.extend(str(t) for t in frame.text)
        return _clean(values)

    def cover(self) -> bytes | None:
        pics = self.tags.getall("APIC")
        if not pics:
            return None
        front = [p for p in pics if getattr(p, "type", None) == 3]
        return (front or pics)[0].data

    def source_url(self) -> str:
        for frame in self.tags.getall("WOAS"):
            if getattr(frame, "url", ""):
                return frame.url
        return ""


class _MP4Reader(_Reader):
    KEYS = {
        "title": ["\xa9nam"], "artist": ["\xa9ART"], "album": ["\xa9alb"],
        "albumartist": ["aART"], "date": ["\xa9day"], "genre": ["\xa9gen"],
        "isrc": ["----:com.apple.iTunes:ISRC", "----:spotdl:ISRC"],
    }

    def __init__(self, tags: MP4Tags):
        self.tags = tags

    def text(self, key: str) -> list[str]:
        if key in ("track", "disc"):
            atom = "trkn" if key == "track" else "disk"
            val = self.tags.get(atom)
            if val and isinstance(val[0], tuple):
                return [str(val[0][0])]
            return []
        for atom in self.KEYS.get(key, []):
            val = self.tags.get(atom)
            if val:
                return _clean(val)
        return []

    def cover(self) -> bytes | None:
        covr = self.tags.get("covr")
        return bytes(covr[0]) if covr else None

    def source_url(self) -> str:
        val = self.tags.get("----:spotdl:WOAS")
        return _clean(val)[0] if val else ""


class _VorbisReader(_Reader):
    KEYS = {
        "title": ["title"], "artist": ["artist"], "album": ["album"],
        "albumartist": ["albumartist", "album artist", "album_artist"],
        "track": ["tracknumber"], "disc": ["discnumber"], "date": ["date", "year", "originaldate"],
        "genre": ["genre"], "isrc": ["isrc"],
    }

    def __init__(self, tags, file_obj):
        self.tags = tags
        self.file = file_obj

    def text(self, key: str) -> list[str]:
        for k in self.KEYS.get(key, []):
            try:
                val = self.tags.get(k)
            except (KeyError, ValueError):
                val = None
            if val:
                return _clean(val)
        return []

    def cover(self) -> bytes | None:
        pictures = list(getattr(self.file, "pictures", []) or [])
        if not pictures:
            for b64 in self.tags.get("metadata_block_picture", []) or []:
                try:
                    pictures.append(Picture(base64.b64decode(b64)))
                except Exception:
                    continue
        if pictures:
            front = [p for p in pictures if p.type == 3]
            return (front or pictures)[0].data
        legacy = self.tags.get("coverart")
        if legacy:
            try:
                return base64.b64decode(legacy[0])
            except Exception:
                return None
        return None

    def source_url(self) -> str:
        val = self.tags.get("woas")
        return _clean(val)[0] if val else ""


class _APEReader(_Reader):
    KEYS = {
        "title": ["Title"], "artist": ["Artist"], "album": ["Album"],
        "albumartist": ["Album Artist", "AlbumArtist"], "track": ["Track"], "disc": ["Disc"],
        "date": ["Year", "Date"], "genre": ["Genre"], "isrc": ["ISRC"],
    }

    def __init__(self, tags: APEv2):
        self.tags = tags

    def text(self, key: str) -> list[str]:
        for k in self.KEYS.get(key, []):
            val = self.tags.get(k)
            if val is not None:
                try:
                    return _clean(list(val))
                except TypeError:
                    return _clean([str(val)])
        return []

    def cover(self) -> bytes | None:
        for k in ("Cover Art (Front)", "Cover Art (Back)"):
            val = self.tags.get(k)
            if val is not None:
                data = bytes(val.value)
                _, _, image = data.partition(b"\x00")
                return image or None
        return None


class _ASFReader(_Reader):
    KEYS = {
        "title": ["Title"], "artist": ["Author", "WM/AlbumArtist"], "album": ["WM/AlbumTitle"],
        "albumartist": ["WM/AlbumArtist"], "track": ["WM/TrackNumber", "WM/Track"],
        "disc": ["WM/PartOfSet"], "date": ["WM/Year"], "genre": ["WM/Genre"], "isrc": ["WM/ISRC"],
    }

    def __init__(self, tags: ASFTags):
        self.tags = tags

    def text(self, key: str) -> list[str]:
        for k in self.KEYS.get(key, []):
            val = self.tags.get(k)
            if val:
                return _clean(str(v) for v in val)
        return []

    def cover(self) -> bytes | None:
        pics = self.tags.get("WM/Picture")
        if not pics:
            return None
        try:
            data = bytes(pics[0].value)
            # Aufbau: Typ (1) | Größe (4, LE) | MIME (UTF-16, \0\0) | Beschreibung (UTF-16, \0\0) | Bild
            size = struct.unpack_from("<I", data, 1)[0]
            pos = 5
            for _ in range(2):
                end = pos
                while data[end:end + 2] != b"\x00\x00":
                    end += 2
                pos = end + 2
            return data[pos:pos + size]
        except Exception:
            return None


def _reader_for(f) -> _Reader | None:
    tags = getattr(f, "tags", None)
    if tags is None:
        return None
    if isinstance(tags, ID3):
        return _ID3Reader(tags)
    if isinstance(tags, MP4Tags):
        return _MP4Reader(tags)
    if isinstance(tags, APEv2):
        return _APEReader(tags)
    if isinstance(tags, ASFTags):
        return _ASFReader(tags)
    if hasattr(tags, "get") and hasattr(tags, "keys"):
        return _VorbisReader(tags, f)
    return None


# --------------------------------------------------------------------------- #
# Öffentliche Funktion
# --------------------------------------------------------------------------- #

_LEADING_NUMBER = re.compile(r"^\s*(\d{1,3})\s*[-._)]\s*")


def guess_from_filename(path: Path, root: Path | None = None) -> TrackInfo:
    stem = path.stem
    info = TrackInfo()
    m = _LEADING_NUMBER.match(stem)
    if m:
        info.track_no = int(m.group(1))
        stem = stem[m.end():]
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        info.artists = [artist.strip()]
        info.title = title.strip()
    else:
        info.title = stem.strip()
    return info


def split_artists(values: list[str], album_artist: str = "", slash_is_separator: bool = False) -> list[str]:
    out: list[str] = []
    for value in values:
        parts = [p.strip() for p in value.split(";")]
        expanded: list[str] = []
        for part in parts:
            if slash_is_separator and "/" in part and part != album_artist:
                expanded.extend(x.strip() for x in part.split("/"))
            else:
                expanded.append(part)
        for part in expanded:
            if part and part not in out:
                out.append(part)
    return out


def read_track(path, name: str | None = None) -> TrackInfo:
    """
    Liest alle Infos einer Datei (Pfad oder geöffnete Datei vom NAS).
    Wirft keine Exceptions – im Zweifel wird der Dateiname als Titel genommen.
    """
    is_file_obj = hasattr(path, "read")
    p = Path(name if name else (getattr(path, "name", "") if is_file_obj else path) or "unbekannt")
    try:
        f = mutagen.File(path if is_file_obj else str(p))
    except Exception:
        f = None

    if f is None:
        info = guess_from_filename(p)
        info.readable = False
        info.codec, info.mime = _MIME_BY_EXT.get(p.suffix.lower(), ("", ""))
        return info

    info = TrackInfo()
    stream = getattr(f, "info", None)
    info.duration = float(getattr(stream, "length", 0) or 0)
    info.bitrate = int(getattr(stream, "bitrate", 0) or 0)
    info.sample_rate = int(getattr(stream, "sample_rate", 0) or 0)

    cls = type(f).__name__
    if cls in ("MP4", "EasyMP4"):
        codec = str(getattr(stream, "codec", "") or "mp4a.40.2")
        info.codec = "alac" if codec == "alac" else ("aac" if codec.startswith("mp4a") else codec)
        info.mime = f'audio/mp4; codecs="{codec}"'
    else:
        info.codec, info.mime = _MIME_BY_CLASS.get(cls, _MIME_BY_EXT.get(p.suffix.lower(), ("", "")))

    reader = _reader_for(f)
    if reader is None:
        guessed = guess_from_filename(p)
        guessed.duration, guessed.bitrate, guessed.sample_rate = info.duration, info.bitrate, info.sample_rate
        guessed.codec, guessed.mime = info.codec, info.mime
        return guessed

    try:
        info.title = reader.first("title")
        info.album = reader.first("album")
        info.album_artist = reader.first("albumartist")
        info.source_url = reader.source_url()
        is_spotdl = "open.spotify.com" in info.source_url
        info.artists = split_artists(
            reader.text("artist"), info.album_artist, slash_is_separator=is_spotdl and isinstance(reader, _ID3Reader)
        )
        info.track_no = parse_int(reader.first("track"))
        info.disc_no = parse_int(reader.first("disc"))
        info.year = parse_year(reader.first("date") or reader.first("year") or reader.first("origdate"))
        genres = reader.text("genre")
        info.genre = genres[0] if genres else ""
        info.isrc = reader.first("isrc").upper()
        info.cover = reader.cover()
    except Exception:
        pass

    if not info.title or not info.artists:
        guessed = guess_from_filename(p)
        info.title = info.title or guessed.title
        info.artists = info.artists or guessed.artists
        info.track_no = info.track_no or guessed.track_no
    return info


def spotify_id_from_url(url: str) -> str:
    m = re.search(r"open\.spotify\.com/(?:intl-[\w-]+/)?track/([A-Za-z0-9]+)", url or "")
    return m.group(1) if m else ""
