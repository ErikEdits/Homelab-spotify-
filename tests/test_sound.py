"""Klang: Equalizer-Presets wie bei Spotify und Downloads ohne verlustreiche Umwandlung."""

import json

from homify.config import Config
from homify.settings_schema import BY_KEY, EQ_PRESET_VALUES


def test_all_presets_have_valid_curves():
    options = [v for v, _ in BY_KEY["eq_preset"].options]
    assert len(options) >= 20 and options[0] == "flat" and options[-1] == "custom"
    for key in options[:-1]:
        values = EQ_PRESET_VALUES[key]
        assert len(values) == 6 and all(isinstance(v, int) and -12 <= v <= 12 for v in values), key
    # die bekannten Spotify-Presets sind dabei
    for key in ("bass", "bass_reducer", "dance", "deep", "hiphop", "rnb", "rock", "small_speakers", "vocal"):
        assert key in EQ_PRESET_VALUES
    assert EQ_PRESET_VALUES["bass"][0] > 0 and EQ_PRESET_VALUES["bass_reducer"][0] < 0


def test_default_download_keeps_original_audio(scanned):
    from homify.downloader import build_command

    assert BY_KEY["download_format"].default == "opus" and BY_KEY["download_bitrate"].default == "disable"
    cmd = build_command("https://open.spotify.com/track/abc")
    assert cmd[cmd.index("--format") + 1] == "opus" and cmd[cmd.index("--bitrate") + 1] == "disable"


def test_old_default_mp3_is_upgraded_once(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"download_format": "mp3", "download_bitrate": "auto", "port": 9000}), encoding="utf-8")
    cfg = Config(path)
    assert cfg.get("download_format") == "opus" and cfg.get("download_bitrate") == "disable"
    assert cfg.get("port") == 9000
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["config_version"] == 2 and saved["download_format"] == "opus"
    # Danach bewusst wieder MP3 gewählt -> bleibt so
    cfg.update({"download_format": "mp3", "download_bitrate": "auto"})
    assert Config(path).get("download_format") == "mp3"


def test_user_choice_is_not_touched(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"download_format": "mp3", "download_bitrate": "320k"}), encoding="utf-8")
    cfg = Config(path)
    assert cfg.get("download_format") == "mp3" and cfg.get("download_bitrate") == "320k"
