"""Optional AcoustID lookup and local result caching."""

import json
import os
import threading

from .fingerprint import fingerprint_file_details
from .runtime import atomic_write_json, app_paths, resolve_fpcalc

_CACHE_PATH = str(app_paths()['cache'] / 'acoustid_cache.json')
_cache: dict | None = None
_CACHE_LOCK = threading.Lock()

MIN_CONFIDENCE = 0.70


def _load_cache() -> dict:
    global _cache  # noqa: PLW0603  # pylint: disable=global-statement
    with _CACHE_LOCK:
        if _cache is None:
            try:
                with open(_CACHE_PATH, 'r', encoding='utf-8') as fh:
                    _cache = json.load(fh)
            except (FileNotFoundError, json.JSONDecodeError):
                _cache = {}
    return _cache


def _save_cache() -> None:
    if _cache is None:
        return
    try:
        atomic_write_json(app_paths()['cache'] / 'acoustid_cache.json', _cache)
    except OSError:
        pass


def _file_key(path: str) -> str:
    """Stable cache key: path + mtime + size. Invalidates if file is modified."""
    try:
        s = os.stat(path)
        return f"{os.path.abspath(path)}|{s.st_mtime_ns}|{s.st_size}|{s.st_ino}"
    except OSError:
        return path


def lookup(path: str, api_key: str) -> dict | None:
    """
    Fingerprint an audio file and query AcoustID.

    Returns dict(artist, title, feat_artists, score) on a confident match,
    or None if no match found, fpcalc is unavailable, or confidence is low.

    Results are persisted to acoustid_cache.json so re-running on unchanged
    files is instant.
    """
    cache = _load_cache()
    key = _file_key(path)

    if key in cache:
        return cache[key]  # None stored here means "looked up, no confident match"

    try:
        import acoustid
    except ImportError as exc:
        raise RuntimeError(
            'pyacoustid is not installed. Run: uv pip install pyacoustid'
        ) from exc

    fpcalc = resolve_fpcalc()
    if not fpcalc:
        return None
    result = None
    try:
        fingerprint, duration, fingerprint_error = fingerprint_file_details(path)
        if fingerprint_error or not fingerprint or duration is None:
            return None

        best_score = 0.0
        best_title = None
        best_artist = None
        best_recording_id = None

        response = acoustid.lookup(
            api_key,
            fingerprint,
            duration,
            meta=['recordings'],
        )
        for score, recording_id, title, artist in acoustid.parse_lookup_result(
            response
        ):
            if score > best_score and title and artist:
                best_score = score
                best_title = title
                best_artist = artist
                best_recording_id = recording_id

        if best_score >= MIN_CONFIDENCE and best_title and best_artist:
            result = _parse_result(
                best_artist,
                best_title,
                best_score,
                recording_id=best_recording_id,
            )

    except (acoustid.FingerprintGenerationError, acoustid.WebServiceError,
            acoustid.NoBackendError, OSError):
        # fpcalc missing/broken, API error, or file unreadable.
        # Don't cache transient errors — allow a retry next run.
        return None

    cache[key] = result
    _save_cache()
    return result


def is_fpcalc_available() -> bool:
    """Quick check: is the bundled or PATH fpcalc reachable?"""
    return resolve_fpcalc() is not None


def _parse_result(
    artist: str,
    title: str,
    score: float,
    recording_id: str | None = None,
) -> dict:
    """
    Normalize AcoustID result into our structured format.
    Splits feat. artists out of both the title and the artist string.
    """
    from .formatter import split_feat

    clean_title, feat_from_title = split_feat(title)
    clean_artist, feat_from_artist = split_feat(artist)

    # Deduplicate across both sources
    seen = {a.lower() for a in feat_from_artist}
    feat_artists = feat_from_artist + [f for f in feat_from_title if f.lower() not in seen]

    result = {
        'artist': clean_artist.strip(),
        'title': clean_title.strip(),
        'feat_artists': feat_artists,
        'score': round(score, 3),
    }
    if recording_id:
        result['recording_id'] = recording_id
    return result
