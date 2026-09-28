"""
Startet spotDL mit Homify-Sicherungen – läuft in der spotDL-Umgebung (wie spotdl_bridge.py).

Aufruf wie „python -m spotdl“:  python spotdl_run.py download <Link> --output … usw.

Sicherung: YouTube Music antwortet nicht
    spotDL sucht jeden Song über die YouTube-Music-Schnittstelle. Schickt YouTube dort statt Daten eine
    Fehler- oder Sperrseite (passiert z. B. nach vielen Downloads am Stück), bricht spotDL mit
    „JSONDecodeError: Expecting value …“ ab – schon bei seinem Verbindungstest beim Start, ohne die anderen
    Quellen zu probieren. Homify macht daraus:
    - Verbindungstest gescheitert -> nur eine Warnung, alle Songs gleich über YouTube suchen.
    - Suche gescheitert -> diesen Song über die normale YouTube-Suche finden (mit den hinterlegten Cookies).
    Die YouTube-Suche (auch spotDLs eigene Quelle „youtube“) liest nur die Trefferliste und prüft nicht
    jedes Video einzeln – ein einzelnes kaputtes oder gesperrtes Video in den Treffern bricht nichts mehr ab.
"""

from __future__ import annotations

import re
import sys
from typing import Any, Callable

FALLBACK_NOTICE = "Homify-Hinweis: YouTube Music antwortet nicht ({}) – suche stattdessen über YouTube"
GIVE_UP_AFTER = 3  # so oft hintereinander gescheitert -> für den Rest des Laufs gleich YouTube nehmen
ISRC = re.compile(r"^[A-Z]{2}-?\w{3}-?\d{2}-?\d{5}$")
SKIP_AVAILABILITY = {"premium_only", "subscriber_only", "needs_auth", "private"}

SearchFn = Callable[[str, "str | None"], list]


class _QuietLogger:
    """yt-dlp-Meldungen der Ersatzsuche verschlucken (spotDLs eigener Logger bricht bei Fehlern ab)."""

    def debug(self, msg: str) -> None:
        pass

    info = warning = error = debug


def youtube_search(search_term: str, cookie_file: str | None = None) -> list:
    """Trefferliste der YouTube-Suche als spotDL-Ergebnisse – leer statt Absturz, wenn etwas schiefgeht."""
    try:
        from spotdl.types.result import Result
        from yt_dlp import YoutubeDL

        opts: dict[str, Any] = {
            "quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": "in_playlist",
            "ignoreerrors": True, "logger": _QuietLogger(),
        }
        if cookie_file:
            opts["cookiefile"] = cookie_file
        ydl = YoutubeDL(opts)
        try:
            info = ydl.extract_info(f"ytsearch10:{search_term}", download=False) or {}
        finally:
            try:
                ydl.close()  # schreibt die Cookies zurück – darf die Treffer nicht kosten
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        return []
    results = []
    for entry in info.get("entries") or []:
        if not entry or not entry.get("id"):
            continue
        if entry.get("live_status") in ("is_live", "is_upcoming") or entry.get("availability") in SKIP_AVAILABILITY:
            continue
        results.append(Result(
            source="youtube", url=f"https://www.youtube.com/watch?v={entry['id']}", verified=False,
            name=entry.get("title") or "", duration=entry.get("duration") or 0,
            author=entry.get("uploader") or entry.get("channel") or "", search_query=search_term,
            views=entry.get("view_count") or 0, result_id=entry["id"],
        ))
    return results


class YouTubeFallback:
    def __init__(self, search: SearchFn = youtube_search, out=None) -> None:
        self.search = search
        self.out = out or sys.stdout
        self.failures = 0
        self.noticed = False

    def _failed(self, exc: BaseException | str, give_up: bool = False) -> None:
        self.failures = GIVE_UP_AFTER if give_up else self.failures + 1
        if not self.noticed:
            self.noticed = True
            name = exc if isinstance(exc, str) else type(exc).__name__
            print(FALLBACK_NOTICE.format(name), file=self.out, flush=True)

    def install(self, ytmusic_cls: Any) -> None:
        """YouTubeMusic.get_results so umbauen, dass Fehler der Schnittstelle zur YouTube-Suche führen."""
        original = ytmusic_cls.get_results
        if getattr(original, "_homify_fallback", False):
            return
        fallback = self

        def get_results(self, search_term: str, *args: Any, **kwargs: Any):
            if fallback.failures < GIVE_UP_AFTER:
                try:
                    results = original(self, search_term, *args, **kwargs)
                    fallback.failures = 0
                    return results
                except Exception as exc:  # noqa: BLE001 – jeder Fehler der Schnittstelle: YouTube versuchen
                    fallback._failed(exc)
            if ISRC.match(search_term.strip()):
                return []  # die YouTube-Suche kennt keine ISRC-Nummern
            return fallback.search(search_term, getattr(self, "cookie_file", None))

        get_results._homify_fallback = True  # type: ignore[attr-defined]
        get_results._homify_original = original  # type: ignore[attr-defined]
        ytmusic_cls.get_results = get_results

    def connection_check(self, ytmusic_cls: Any) -> Callable[[], bool]:
        """Ersatz für spotDLs Verbindungstest: scheitert er, nur warnen und direkt über YouTube suchen."""
        def check() -> bool:
            get_results = ytmusic_cls.get_results
            original = getattr(get_results, "_homify_original", get_results)
            try:
                return bool(original(ytmusic_cls(), "a", log_search_failures=False))
            except Exception as exc:  # noqa: BLE001
                self._failed(exc, give_up=True)
                return True  # Homify weicht aus – spotDLs Warnung wäre nur verwirrend

        return check


def _install() -> None:
    try:
        import spotdl.console.entry_point as entry_point
        import spotdl.utils.downloader as downloader_utils
        from spotdl.providers.audio.youtube import YouTube
        from spotdl.providers.audio.ytmusic import YouTubeMusic
    except Exception:  # andere spotDL-Version: dann eben ohne Sicherung
        return
    # spotDLs eigene YouTube-Suche prüft jedes der 10 Videos einzeln – ein kaputtes bricht den Song ab
    YouTube.get_results = lambda self, search_term, *args, **kwargs: youtube_search(
        search_term, getattr(self, "cookie_file", None))
    fallback = YouTubeFallback()
    fallback.install(YouTubeMusic)
    check = fallback.connection_check(YouTubeMusic)
    for module in (entry_point, downloader_utils):
        if hasattr(module, "check_ytmusic_connection"):
            module.check_ytmusic_connection = check


def main() -> None:
    _install()
    from spotdl.console import console_entry_point

    sys.argv = ["spotdl", *sys.argv[1:]]
    console_entry_point()


if __name__ == "__main__":
    main()
