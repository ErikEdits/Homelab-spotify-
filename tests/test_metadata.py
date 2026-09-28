from pathlib import Path

from homify.metadata import guess_from_filename, read_track, split_artists, spotify_id_from_url


def test_mp3_tags_and_cover(music_dir):
    info = read_track(music_dir / "Daft Punk" / "Discovery" / "01 One More Time.mp3")
    assert info.title == "One More Time"
    assert info.artists == ["Daft Punk"]
    assert info.album == "Discovery"
    assert info.album_artist == "Daft Punk"
    assert info.track_no == 1
    assert info.year == 2001
    assert info.genre == "Electronic"
    assert info.mime == "audio/mpeg"
    assert info.cover and len(info.cover) > 100
    assert 2.5 < info.duration < 3.5


def test_flac(music_dir):
    info = read_track(music_dir / "Daft Punk" / "Discovery" / "02 Aerodynamic.flac")
    assert info.title == "Aerodynamic"
    assert info.track_no == 2
    assert info.codec == "flac"


def test_mp4_aac_and_alac(music_dir):
    aac = read_track(music_dir / "Various" / "a.m4a")
    alac = read_track(music_dir / "Various" / "b.m4a")
    assert aac.codec == "aac" and "mp4a" in aac.mime
    assert alac.codec == "alac" and 'codecs="alac"' in alac.mime
    assert aac.title == "Song A" and alac.artists == ["Artist Y"]


def test_opus_wma_and_untagged(music_dir):
    opus = read_track(music_dir / "Loose" / "opus.opus")
    assert opus.codec == "opus" and opus.title == "Opus Song"
    wma = read_track(music_dir / "Loose" / "song.wma")
    assert wma.codec == "wma" and wma.title == "WMA Song" and wma.artists == ["WMA Artist"]
    vorbis = read_track(music_dir / "Loose" / "Some Artist - Untagged Vorbis.ogg")
    assert vorbis.title == "Untagged Vorbis" and vorbis.artists == ["Some Artist"]


def test_unreadable_file_falls_back_to_filename(tmp_path):
    f = tmp_path / "03 - Kaputt.mp3"
    f.write_bytes(b"not really audio")
    info = read_track(f)
    assert info.title == "Kaputt"
    assert info.track_no == 3


def test_guess_from_filename():
    info = guess_from_filename(Path("07. Queen - Bohemian Rhapsody.flac"))
    assert info.track_no == 7
    assert info.artists == ["Queen"]
    assert info.title == "Bohemian Rhapsody"


def test_split_artists_spotdl_slash():
    assert split_artists(["Daft Punk/Pharrell Williams"], "Daft Punk", slash_is_separator=True) == [
        "Daft Punk", "Pharrell Williams"]
    # AC/DC bleibt heil, weil es der Album-Interpret ist
    assert split_artists(["AC/DC"], "AC/DC", slash_is_separator=True) == ["AC/DC"]
    assert split_artists(["AC/DC"], "", slash_is_separator=False) == ["AC/DC"]
    assert split_artists(["A; B", "B"]) == ["A", "B"]


def test_spotify_id_from_url():
    assert spotify_id_from_url("https://open.spotify.com/track/0DiWol3AO6WpXZgp0goxAV") == "0DiWol3AO6WpXZgp0goxAV"
    assert spotify_id_from_url("https://open.spotify.com/intl-de/track/abc123?si=x") == "abc123"
    assert spotify_id_from_url("https://open.spotify.com/album/abc") == ""
