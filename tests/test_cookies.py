"""YouTube-Cookies hochladen: nur YouTube-Cookies werden gespeichert und an spotDL übergeben."""

import pytest

from homify import cookies
from homify.config import config

# Ausgedachte Werte im Format der Chrome-Erweiterung „Export All Cookies“
SAMPLE = "\n".join([
    "# Netscape HTTP Cookie File",
    "# Source: all domains",
    ".google.com\tTRUE\t/\tTRUE\t1825171257\tSID\tgoogle-geheim",
    ".youtube.com\tTRUE\t/\tFALSE\t1825171257\tSID\tyt-sid-wert",
    ".youtube.com\tTRUE\t/\tTRUE\t1825171257\t__Secure-3PSID\tyt-3psid-wert",
    "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1825171258\tLOGIN_INFO\tyt-login-wert",
    ".youtube.com\tTRUE\t/\tTRUE\t1825171261\tPREF\tf6=40000000",
    ".accounts.google.com\tTRUE\t/\tTRUE\t1825171271\tLSID\tkonto-geheim",
])


@pytest.fixture
def clean_cookie_setting(monkeypatch):
    monkeypatch.setitem(config._data, "spotdl_cookie_file", "")
    yield
    cookies.COOKIE_FILE.unlink(missing_ok=True)


def test_only_youtube_cookies_are_kept(scanned, clean_cookie_setting):
    st = cookies.save(SAMPLE)
    assert st["exists"] and st["logged_in"] and st["count"] == 4
    saved = cookies.COOKIE_FILE.read_text(encoding="utf-8")
    assert "google-geheim" not in saved and "konto-geheim" not in saved
    assert "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1825171258\tLOGIN_INFO\tyt-login-wert" in saved
    assert config.get("spotdl_cookie_file") == str(cookies.COOKIE_FILE)

    from homify.downloader import build_command
    cmd = build_command("https://open.spotify.com/track/abc")
    assert cmd[cmd.index("--cookie-file") + 1] == str(cookies.COOKIE_FILE)

    assert not cookies.remove()["exists"]
    assert config.get("spotdl_cookie_file") == ""
    assert not cookies.COOKIE_FILE.exists()


def test_pasted_text_with_spaces_instead_of_tabs(scanned, clean_cookie_setting):
    pasted = SAMPLE.replace("\t", "    ")
    assert cookies.save(pasted)["count"] == 4


def test_rejects_wrong_files(scanned, clean_cookie_setting):
    with pytest.raises(ValueError, match="keine Cookie-Datei"):
        cookies.save("Hallo, das ist nur Text")
    with pytest.raises(ValueError, match="keine YouTube-Cookies"):
        cookies.save(".google.com\tTRUE\t/\tTRUE\t1825171257\tSID\tx")
    st = cookies.save(".youtube.com\tTRUE\t/\tTRUE\t1825171261\tPREF\tf6=1")
    assert st["exists"] and not st["logged_in"]  # nicht angemeldet exportiert


def test_cookie_api(scanned, clean_cookie_setting):
    from fastapi.testclient import TestClient

    from homify import auth
    from homify.server import app

    if not auth.authenticate("cookieadmin", "cookieadmin1"):
        auth.create_user("cookieadmin", "cookieadmin1", is_admin=True)
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "cookieadmin", "password": "cookieadmin1"}).status_code == 200
    assert c.post("/api/settings/youtube-cookies", json={"text": "kaputt"}).status_code == 400
    r = c.post("/api/settings/youtube-cookies", json={"text": SAMPLE})
    assert r.status_code == 200 and r.json()["logged_in"]
    assert c.get("/api/settings").json()["system"]["youtube_cookies"]["count"] == 4
    assert c.delete("/api/settings/youtube-cookies").json()["exists"] is False


def test_bot_error_mentions_expired_cookies(scanned, monkeypatch):
    from homify.downloader import friendly_error

    msg = "ERROR: Sign in to confirm you're not a bot"
    monkeypatch.setitem(config._data, "spotdl_cookie_file", "")
    assert "hochladen" in friendly_error(msg)
    monkeypatch.setitem(config._data, "spotdl_cookie_file", "/x/cookies.txt")
    assert "abgelaufen" in friendly_error(msg)
