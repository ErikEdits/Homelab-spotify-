"""Normalisierung der Spotify-Antworten (offizielle API und SpotipyFree)."""

from homify import spotdl_bridge as bridge


class FakeFreeClient:
    """Liefert Daten im Format von SpotipyFree (nur Songs bei der Suche)."""

    def search(self, q, type="track", limit=10):
        return {"tracks": {"items": [
            {
                "name": "One More Time", "id": "t1", "track_id": "t1", "duration_ms": 320000,
                "artists": [{"name": "Daft Punk"}], "explicit": False,
                "album": {"id": "a1", "name": "Discovery", "release_date": "2001-03-12",
                          "images": [{"url": "https://i.scdn.co/640", "width": 640}, {"url": "https://i.scdn.co/300", "width": 300}]},
                "external_ids": {"isrc": "gbduw0000059"},
            },
            {"name": "", "id": ""},  # kaputter Eintrag wird ignoriert
        ]}}

    def album(self, album_id):
        return {"name": "Discovery", "artists": [{"name": "Daft Punk"}], "images": [{"url": "https://i.scdn.co/300", "width": 300}],
                "tracks": {"items": [{"name": "Aerodynamic", "id": "t2", "duration_ms": 200000, "artists": [{"name": "Daft Punk"}], "album": []}],
                           "next": False}}


def test_search_normalizes_free_client(monkeypatch):
    monkeypatch.setattr(bridge, "_client", FakeFreeClient())
    res = bridge.cmd_search("daft punk", 10)
    assert len(res["tracks"]) == 1
    t = res["tracks"][0]
    assert t["url"] == "https://open.spotify.com/track/t1"
    assert t["artists"] == ["Daft Punk"]
    assert t["image"] == "https://i.scdn.co/300"
    assert t["isrc"] == "GBDUW0000059"
    assert t["year"] == "2001"
    assert res["albums"][0]["url"] == "https://open.spotify.com/album/a1"


def test_resolve_album(monkeypatch):
    monkeypatch.setattr(bridge, "_client", FakeFreeClient())
    res = bridge.cmd_resolve("https://open.spotify.com/intl-de/album/a1?si=x")
    assert res["kind"] == "album"
    assert res["url"] == "https://open.spotify.com/album/a1"
    assert res["tracks"][0]["album"] == "Discovery"
    assert res["tracks"][0]["image"] == "https://i.scdn.co/300"


def test_playlist_item_wrapper():
    t = bridge._track({"added_at": "x", "track": {"name": "X", "id": "i", "artists": [{"profile": {"name": "Y"}}]}})
    assert t["name"] == "X" and t["artists"] == ["Y"]
