"""Rate-limited MusicBrainz lookup helpers."""

from __future__ import annotations

import importlib.util
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .extractor import TrackInfo

_RELEASE_CACHE: dict[str, list] = {}


class _RateLimiter:
    def __init__(self, interval: float) -> None:
        self.interval = interval
        self.last_request = 0.0

    def wait(self) -> None:
        wait = self.interval - (time.monotonic() - self.last_request)
        if wait > 0:
            time.sleep(wait)
        self.last_request = time.monotonic()


_REQUEST_LIMITER = _RateLimiter(1.1)


def _available() -> bool:
    return importlib.util.find_spec("musicbrainzngs") is not None


def _mb():
    import musicbrainzngs  # pylint: disable=import-error

    musicbrainzngs.set_useragent(
        "SongOrganizer",
        "1.0",
        "https://github.com/Izento/song-organizer",
    )
    return musicbrainzngs


def _rate_limit() -> None:
    _REQUEST_LIMITER.wait()


def lookup_track_by_album(album: str, track_num: int, artist_hint: str = '') -> dict | None:
    """
    Find a track title by album name and track number.
    Returns {'artist': str, 'title': str} or None if not found.
    Used for Hikaru Utada style folders where files are "01 Track 1.mp3".

    Caches the full track list per album so only one API call is made per album
    regardless of how many tracks are looked up.
    """
    if not _available():
        return None

    cache_key = f"{album}||{artist_hint}"

    if cache_key not in _RELEASE_CACHE:
        mb = _mb()
        try:
            _rate_limit()
            query_kwargs = {'release': album, 'limit': 3}
            if artist_hint:
                query_kwargs['artist'] = artist_hint

            result = mb.search_releases(**query_kwargs)
            releases = result.get('release-list', [])
            if not releases:
                _RELEASE_CACHE[cache_key] = []
                return None

            _rate_limit()
            release = mb.get_release_by_id(releases[0]['id'], includes=['recordings'])

            tracks = []
            for medium in release['release'].get('medium-list', []):
                tracks.extend(medium.get('track-list', []))
            _RELEASE_CACHE[cache_key] = tracks

        except (mb.MusicBrainzError, KeyError, TypeError, ValueError):
            _RELEASE_CACHE[cache_key] = []
            return None

    for track in _RELEASE_CACHE[cache_key]:
        if int(track.get('position', -1)) == track_num:
            rec = track.get('recording', {})
            artist_credits = rec.get('artist-credit', [])
            artist_name = artist_hint
            if artist_credits and isinstance(artist_credits[0], dict):
                artist_name = artist_credits[0].get('artist', {}).get(
                    'name',
                    artist_hint,
                )
            return {'artist': artist_name, 'title': rec.get('title', '')}

    return None


def lookup_ocremix_remixers(game: str, song_title: str) -> list[str] | None:
    """
    Find OC ReMix remixer names by game and song title.
    Returns a list of remixer names, or None if nothing found.
    Used for Gamer's Delight where the old format has no remixer in the metadata.
    """
    if not _available():
        return None

    mb = _mb()
    try:
        _rate_limit()
        # Search for the recording on OC ReMix's MusicBrainz label
        result = mb.search_recordings(
            recording=song_title,
            artist=game,
            limit=5,
        )
        recordings = result.get('recording-list', [])

        for rec in recordings:
            title = rec.get('title', '')
            if 'OC ReMix' not in title and song_title.lower() not in title.lower():
                continue
            artist_credits = rec.get('artist-credit', [])
            names = [
                c['artist']['name']
                for c in artist_credits
                if isinstance(c, dict) and 'artist' in c
            ]
            if names:
                return names

    except (mb.MusicBrainzError, KeyError, TypeError, ValueError):
        return None

    return None


def enrich_track(track: TrackInfo) -> TrackInfo:
    """Fill a track's missing identity fields from MusicBrainz when possible."""
    if track.strategy == "musicbrainz":
        result = lookup_track_by_album(
            track.mb_album,
            track.mb_track_num,
            artist_hint=track.artist,
        )
        if result:
            track.artist = result["artist"]
            track.title = result["title"]
            track.needs_lookup = False
        else:
            track.skip_reason = (
                f'MusicBrainz: no match for "{track.mb_album}" '
                f"track {track.mb_track_num}"
            )
    elif track.strategy in {"ocremix_old_tags", "ocremix_filename"} and not track.remixers:
        remixers = lookup_ocremix_remixers(track.game, track.title)
        if remixers:
            track.remixers = remixers
            track.needs_lookup = False
    return track
