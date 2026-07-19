"""Optional AcoustID lookup and local result caching."""

import json
import math
import os
import threading

from .fingerprint import fingerprint_file_details
from .regular_parser import normalize_text, parse_regular_filename
from .runtime import atomic_write_json, app_paths, resolve_fpcalc

_CACHE_PATH = str(app_paths()['cache'] / 'acoustid_cache.json')
_cache: dict | None = None
_CACHE_LOCK = threading.RLock()

MIN_CONFIDENCE = 0.70
_CACHE_VERSION = 3


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
    with _CACHE_LOCK:
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
        return (
            f"v{_CACHE_VERSION}|{os.path.abspath(path)}|"
            f"{s.st_mtime_ns}|{s.st_size}|{s.st_ino}"
        )
    except OSError:
        return f"v{_CACHE_VERSION}|{path}"


def _filename_identity_hint(path: str) -> tuple[str, str]:
    parsed = parse_regular_filename(os.path.basename(path))
    if parsed is None:
        return "", ""
    return parsed.artist, parsed.title


def _identity_similarity(
    filename_hint: tuple[str, str],
    artist: str,
    title: str,
) -> int:
    hint_artist, hint_title = (normalize_text(value) for value in filename_hint)
    candidate_artist = normalize_text(artist)
    candidate_title = normalize_text(title)
    score = 0
    if hint_artist and hint_artist == candidate_artist:
        score += 1
    if hint_title == candidate_title:
        score += 3
    elif hint_title and (
        hint_title in candidate_title or candidate_title in hint_title
    ):
        score += 2
    return score


def _source_count(recording: dict) -> int:
    try:
        return int(recording.get("sources", 0))
    except (TypeError, ValueError):
        return 0


def _recording_artist(recording: dict) -> str | None:
    artists = recording.get("artists") or ()
    if not artists:
        return None
    return "".join(
        artist.get("name", "") + artist.get("joinphrase", "")
        for artist in artists
    )


def _select_recording(response: dict, path: str) -> tuple[float, dict] | None:
    """Choose a supported recording from the strongest acoustic match.

    An AcoustID result identifies an audio fingerprint; it can be linked to
    several MusicBrainz recordings. The match score applies to that fingerprint,
    not to any one linked recording. A matching filename title resolves a
    conflicting group; otherwise source consensus chooses the recording.
    """
    if response.get("status") != "ok":
        return None
    scored_results = []
    for result in response.get("results", ()):
        try:
            score = float(result["score"])
        except (KeyError, TypeError, ValueError):
            continue
        scored_results.append((score, result))
    scored_results.sort(key=lambda value: value[0], reverse=True)

    filename_hint = _filename_identity_hint(path)
    while scored_results:
        score = scored_results[0][0]
        if score < MIN_CONFIDENCE:
            break
        top_results = [
            result
            for candidate_score, result in scored_results
            if math.isclose(candidate_score, score, rel_tol=0, abs_tol=1e-6)
        ]
        scored_results = [
            (candidate_score, result)
            for candidate_score, result in scored_results
            if not math.isclose(candidate_score, score, rel_tol=0, abs_tol=1e-6)
        ]
        recordings = [
            (
                _source_count(recording),
                _identity_similarity(filename_hint, artist, title),
                normalize_text(title) == normalize_text(filename_hint[1]),
                recording,
            )
            for result in top_results
            for recording in result.get("recordings", ())
            if (title := recording.get("title"))
            and (artist := _recording_artist(recording))
        ]
        if recordings:
            exact_title_matches = [
                recording
                for recording in recordings
                if recording[2]
            ]
            choices = exact_title_matches or recordings
            _, _, _, recording = max(choices, key=lambda value: value[:2])
            return score, recording
    return None


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

    with _CACHE_LOCK:
        if key in cache:
            return cache[key]  # None means "looked up, no confident match"

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

        response = acoustid.lookup(
            api_key,
            fingerprint,
            duration,
            meta=['recordings', 'sources'],
        )
        if response.get("status") != "ok":
            raise acoustid.WebServiceError(
                f"AcoustID response status: {response.get('status')}"
            )
        selected = _select_recording(response, path)
        if selected is not None:
            score, recording = selected
            result = _parse_result(
                _recording_artist(recording) or "",
                recording["title"],
                score,
                recording_id=recording.get("id"),
            )
            result["sources"] = _source_count(recording)

    except (
        acoustid.FingerprintGenerationError,
        acoustid.WebServiceError,
        acoustid.NoBackendError,
        OSError,
    ):
        # fpcalc missing/broken, API error, or file unreadable.
        # Don't cache transient errors — allow a retry next run.
        return None

    with _CACHE_LOCK:
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
