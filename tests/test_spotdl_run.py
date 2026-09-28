"""spotDL-Starter: fällt YouTube Music aus („JSONDecodeError“), sucht Homify über YouTube weiter."""

import io
import json

from homify.spotdl_run import FALLBACK_NOTICE, GIVE_UP_AFTER, YouTubeFallback, youtube_search


def fake_search(calls):
    def search(term, cookie_file):
        calls.append(term)
        return [f"yt:{term}:{cookie_file}"]
    return search


def make_ytmusic(fail: bool):
    class FakeYTMusic:
        calls = 0

        def __init__(self):
            self.cookie_file = "cookies.txt"

        def get_results(self, search_term, log_search_failures=True, **kwargs):
            type(self).calls += 1
            if fail:
                json.loads("<html>Sorry …</html>")  # wie eine Sperrseite statt JSON
            return [f"ytm:{search_term}"]

    return FakeYTMusic


def test_working_youtube_music_is_untouched():
    cls, out, calls = make_ytmusic(fail=False), io.StringIO(), []
    fb = YouTubeFallback(fake_search(calls), out)
    fb.install(cls)
    assert cls().get_results("Maroon 5 - Animals", filter="songs") == ["ytm:Maroon 5 - Animals"]
    assert fb.connection_check(cls)() is True
    assert out.getvalue() == "" and calls == []


def test_failing_youtube_music_falls_back_to_youtube_with_cookies():
    cls, out, calls = make_ytmusic(fail=True), io.StringIO(), []
    fb = YouTubeFallback(fake_search(calls), out)
    fb.install(cls)
    fb.install(cls)  # zweimal installieren schadet nicht
    provider = cls()
    assert provider.get_results("Maroon 5 - Animals", filter="songs") == ["yt:Maroon 5 - Animals:cookies.txt"]
    assert out.getvalue().strip() == FALLBACK_NOTICE.format("JSONDecodeError")  # nur einmal gemeldet
    assert provider.get_results("USUM71412345") == []  # ISRC-Suche gibt es bei YouTube nicht
    # nach ein paar Fehlschlägen wird YouTube Music für den Rest des Laufs nicht mehr gefragt
    for i in range(5):
        provider.get_results(f"Song {i}")
    assert cls.calls == GIVE_UP_AFTER
    assert out.getvalue().count("Homify-Hinweis") == 1


def test_connection_check_never_crashes_and_skips_youtube_music():
    """spotDL testet beim Start mit der Suche „a“ – das darf weder abbrechen noch die Ersatzsuche auslösen."""
    cls, out, calls = make_ytmusic(fail=True), io.StringIO(), []
    fb = YouTubeFallback(fake_search(calls), out)
    fb.install(cls)
    assert fb.connection_check(cls)() is True
    assert calls == [] and cls.calls == 1  # nur der Test selbst, keine YouTube-Suche nach „a“
    assert "YouTube Music antwortet nicht" in out.getvalue()
    # danach gehen alle Songs direkt zu YouTube
    assert cls().get_results("Maroon 5 - Animals") == ["yt:Maroon 5 - Animals:cookies.txt"]
    assert cls.calls == 1


def test_youtube_search_never_raises(monkeypatch):
    import sys
    import types

    class Boom:
        def __init__(self, opts):
            raise RuntimeError("Requested format is not available")

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=Boom))
    monkeypatch.setitem(sys.modules, "spotdl", types.ModuleType("spotdl"))
    monkeypatch.setitem(sys.modules, "spotdl.types", types.ModuleType("spotdl.types"))
    monkeypatch.setitem(sys.modules, "spotdl.types.result", types.SimpleNamespace(Result=dict))
    assert youtube_search("a") == []
