"""
Startet spotDL mit Homify-Sicherungen – läuft in der spotDL-Umgebung (wie spotdl_bridge.py).

Aufruf wie „python -m spotdl“:  python spotdl_run.py download <Link> --output … usw.

Sicherung: YouTube Music antwortet nicht
    spotDL sucht jeden Song über die YouTube-Music-Schnittstelle. Schickt YouTube dort statt Daten eine
    Fehler- oder Sperrseite (passiert z. B. nach vielen Downloads am Stück), bricht spotDL den Song mit
    „JSONDecodeError: Expecting value …“ ab – ohne die anderen Quellen zu probieren. Homify sucht den
    Song dann stattdessen über die normale YouTube-Suche (yt-dlp, mit den hinterlegten Cookies).
"""

from __future__ import annotations

import re
import sys
from typing import Any

FALLBACK_NOTICE = "Homify-Hinweis: YouTube Music antwortet nicht ({}) – suche stattdessen über YouTube"
GIVE_UP_AFTER = 3  # so oft hintereinander gescheitert -> für den Rest des Laufs gleich YouTube nehmen
ISRC = re.compile(r"^[A-Z]{2}-?\w{3}-?\d{2}-?\d{5}$")


def install_youtube_fallback(ytmusic_cls: Any, youtube_cls: Any, out=None) -> None:
    """YouTubeMusic.get_results so umbauen, dass Fehler der Schnittstelle zur YouTube-Suche führen."""
    original = ytmusic_cls.get_results
    if getattr(original, "_homify_fallback", False):
        return
    out = out or sys.stdout
    state = {"failures": 0, "noticed": False}

    def get_results(self, search_term: str, *args: Any, **kwargs: Any):
        if state["failures"] < GIVE_UP_AFTER:
            try:
                results = original(self, search_term, *args, **kwargs)
                state["failures"] = 0
                return results
            except Exception as exc:  # noqa: BLE001 – jeder Fehler der Schnittstelle: YouTube versuchen
                state["failures"] += 1
                if not state["noticed"]:
                    state["noticed"] = True
                    print(FALLBACK_NOTICE.format(type(exc).__name__), file=out, flush=True)
        if ISRC.match(search_term.strip()):
            return []  # die YouTube-Suche kennt keine ISRC-Nummern
        fallback = getattr(self, "_homify_youtube", None)
        if fallback is None:
            fallback = youtube_cls(
                output_format=getattr(self, "output_format", "mp3"),
                cookie_file=getattr(self, "cookie_file", None),
                search_query=getattr(self, "search_query", None),
                filter_results=getattr(self, "filter_results", True),
            )
            self._homify_youtube = fallback
        return fallback.get_results(search_term)

    get_results._homify_fallback = True  # type: ignore[attr-defined]
    ytmusic_cls.get_results = get_results


def _install() -> None:
    try:
        from spotdl.providers.audio.youtube import YouTube
        from spotdl.providers.audio.ytmusic import YouTubeMusic
    except Exception:  # andere spotDL-Version: dann eben ohne Sicherung
        return
    install_youtube_fallback(YouTubeMusic, YouTube)


def main() -> None:
    _install()
    from spotdl.console import console_entry_point

    sys.argv = ["spotdl", *sys.argv[1:]]
    console_entry_point()


if __name__ == "__main__":
    main()
