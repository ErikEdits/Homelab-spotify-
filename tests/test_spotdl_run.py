"""spotDL-Starter: fällt YouTube Music aus („JSONDecodeError“), sucht Homify über YouTube weiter."""

import io
import json

from homify.spotdl_run import FALLBACK_NOTICE, GIVE_UP_AFTER, install_youtube_fallback


class FakeYouTube:
    created = 0

    def __init__(self, output_format, cookie_file, search_query, filter_results):
        FakeYouTube.created += 1
        self.cookie_file = cookie_file

    def get_results(self, search_term):
        return [f"yt:{search_term}:{self.cookie_file}"]


def make_ytmusic(fail: bool):
    class FakeYTMusic:
        calls = 0

        def __init__(self):
            self.output_format, self.cookie_file = "opus", "cookies.txt"
            self.search_query, self.filter_results = None, True

        def get_results(self, search_term, log_search_failures=True, **kwargs):
            type(self).calls += 1
            if fail:
                json.loads("<html>Sorry …</html>")  # wie eine Sperrseite statt JSON
            return [f"ytm:{search_term}"]

    return FakeYTMusic


def test_working_youtube_music_is_untouched():
    cls = make_ytmusic(fail=False)
    out = io.StringIO()
    install_youtube_fallback(cls, FakeYouTube, out)
    assert cls().get_results("Maroon 5 - Animals", filter="songs") == ["ytm:Maroon 5 - Animals"]
    assert out.getvalue() == ""


def test_failing_youtube_music_falls_back_to_youtube_with_cookies():
    cls = make_ytmusic(fail=True)
    out = io.StringIO()
    install_youtube_fallback(cls, FakeYouTube, out)
    install_youtube_fallback(cls, FakeYouTube, out)  # zweimal installieren schadet nicht
    provider = cls()
    assert provider.get_results("Maroon 5 - Animals", filter="songs") == ["yt:Maroon 5 - Animals:cookies.txt"]
    assert out.getvalue().strip() == FALLBACK_NOTICE.format("JSONDecodeError")  # nur einmal gemeldet
    assert provider.get_results("USUM71412345") == []  # ISRC-Suche gibt es bei YouTube nicht
    # nach ein paar Fehlschlägen wird YouTube Music für den Rest des Laufs nicht mehr gefragt
    for i in range(5):
        provider.get_results(f"Song {i}")
    assert cls.calls == GIVE_UP_AFTER
    assert out.getvalue().count("Homify-Hinweis") == 1
