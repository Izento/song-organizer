"""Source-neutral duplicate analysis for ordinary music libraries.

This module only analyzes.  It never deletes or moves a file.  Exact
cryptographic matches are classified as ``auto-safe`` evidence; audio
fingerprints and metadata similarities remain review evidence.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .extractor import AUDIO_EXTENSIONS
from .fingerprint import fingerprint_file
from .identity import TrackIdentity
from .media import read_media
from .regular_parser import RegularName, normalize_text, parse_regular_stem
from .review_models import DuplicateFinding, proposal_id


@dataclass
class RegularTrack:
    path: str
    name: RegularName | None
    tags: dict[str, str]
    duration: float | None
    bitrate: int | None
    sha256: str | None
    fingerprint: str | None = None
    fingerprint_error: str = ""
    error: str = ""


def _sha256(path: str, chunk_size: int = 1024 * 1024) -> str:
    before = os.stat(path)
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    after = os.stat(path)
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise OSError(f"File changed while being hashed: {path}")
    return digest.hexdigest()


def _audio_paths(folder_path: str, recursive: bool) -> list[str]:
    root = Path(folder_path)
    iterator = root.rglob("*") if recursive else root.glob("*")
    return sorted(
        str(path)
        for path in iterator
        if path.is_file() and path.suffix.casefold() in AUDIO_EXTENSIONS
    )


def _parse_path(path: str) -> RegularName | None:
    return parse_regular_stem(Path(path).stem)


def _collect_tracks(
    folder_path: str,
    recursive: bool,
    progress: Callable[[int, int, str], None] | None = None,
    cancel_event=None,
    fingerprint: bool = False,
) -> list[RegularTrack]:
    paths = _audio_paths(folder_path, recursive)
    tracks: list[RegularTrack] = []
    total = len(paths)
    for index, path in enumerate(paths, start=1):
        if cancel_event is not None and cancel_event.is_set():
            break
        media = read_media(path)
        try:
            digest = _sha256(path)
            error = media.error
        except OSError as exc:
            digest = None
            error = str(exc)
        audio_fingerprint = None
        fingerprint_error = ""
        if fingerprint and digest is not None:
            audio_fingerprint, fingerprint_error = fingerprint_file(path)
        tracks.append(
            RegularTrack(
                path=path,
                name=_parse_path(path),
                tags=media.tags,
                duration=media.duration,
                bitrate=media.bitrate,
                sha256=digest,
                fingerprint=audio_fingerprint,
                fingerprint_error=fingerprint_error,
                error=error,
            )
        )
        if progress:
            progress(index, total, path)
    return tracks


def _core_key(track: RegularTrack) -> tuple:
    if track.name is not None:
        return TrackIdentity.from_regular(track.name).core_key
    return (
        normalize_text(track.tags.get("artist", "")),
        normalize_text(track.tags.get("title", "")),
    )


def _version_key(track: RegularTrack) -> tuple:
    if track.name is None:
        return ()
    identity = TrackIdentity.from_regular(track.name)
    return identity.key[2:]


def _duration_close(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return False
    delta = abs(left - right)
    return delta <= max(2.0, max(left, right) * 0.02)


def _duration_spread(tracks: list[RegularTrack]) -> float | None:
    values = [track.duration for track in tracks if track.duration is not None]
    if len(values) < 2:
        return None
    return max(values) - min(values)


def _classify(tracks: list[RegularTrack]) -> str:
    hashes = {track.sha256 for track in tracks if track.sha256}
    if len(hashes) == 1 and len(hashes) == len(
        [track for track in tracks if track.sha256]
    ):
        return "auto-safe"

    versions = {_version_key(track) for track in tracks}
    fingerprints = {track.fingerprint for track in tracks if track.fingerprint}
    if (
        len(fingerprints) == 1
        and len(fingerprints) == len(tracks)
        and len(versions) == 1
    ):
        return "review"
    durations_close = all(
        _duration_close(left.duration, right.duration)
        for index, left in enumerate(tracks)
        for right in tracks[index + 1 :]
    )
    if len(versions) == 1 and durations_close:
        return "review"
    return "unsafe"


def _finding(tracks: list[RegularTrack], classification: str) -> DuplicateFinding:
    paths = tuple(sorted(track.path for track in tracks))
    evidence = {
        "hashes": {track.path: track.sha256 for track in tracks},
        "fingerprints": {track.path: track.fingerprint for track in tracks},
        "fingerprint_errors": {
            track.path: track.fingerprint_error
            for track in tracks
            if track.fingerprint_error
        },
        "durations": {track.path: track.duration for track in tracks},
        "bitrates": {track.path: track.bitrate for track in tracks},
        "versions": {track.path: _version_key(track) for track in tracks},
        "tags": {track.path: track.tags for track in tracks},
    }
    if classification == "auto-safe":
        recommendation = "Keep one copy; exact content hashes match."
        confidence = "high"
    elif classification == "review":
        recommendation = "Review the recordings before removing either file."
        confidence = "medium"
    else:
        recommendation = "Keep both unless a user confirms they are equivalent."
        confidence = "low"
    return DuplicateFinding(
        id=proposal_id("duplicate", paths[0], paths),
        paths=paths,
        classification=classification,
        recommendation=recommendation,
        evidence=evidence,
        confidence=confidence,
    )


def analyze_regular_duplicates(
    folder_path: str,
    recursive: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
    cancel_event=None,
    fingerprint: bool = False,
) -> list[DuplicateFinding]:
    """Return duplicate evidence without performing any filesystem mutation."""
    tracks = _collect_tracks(
        folder_path,
        recursive,
        progress=progress,
        cancel_event=cancel_event,
        fingerprint=fingerprint,
    )
    by_hash: dict[str, list[RegularTrack]] = {}
    for track in tracks:
        if track.sha256:
            by_hash.setdefault(track.sha256, []).append(track)

    findings: list[DuplicateFinding] = []
    consumed: set[str] = set()
    for group in by_hash.values():
        if len(group) > 1:
            findings.append(_finding(group, "auto-safe"))
            consumed.update(track.path for track in group)

    by_core: dict[tuple, list[RegularTrack]] = {}
    for track in tracks:
        if track.path not in consumed and _core_key(track) != ("", ""):
            by_core.setdefault(_core_key(track), []).append(track)

    for group in by_core.values():
        if len(group) < 2:
            continue
        findings.append(_finding(group, _classify(group)))
    return sorted(findings, key=lambda item: item.paths)


def dedup_regular_folder(
    folder_path: str,
    dry_run: bool = True,
    recursive: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
    cancel_event=None,
    fingerprint: bool = False,
) -> dict:
    """Compatibility wrapper returning a read-only regular dedup report."""
    findings = analyze_regular_duplicates(
        folder_path,
        recursive=recursive,
        progress=progress,
        cancel_event=cancel_event,
        fingerprint=fingerprint,
    )
    counts = {
        "groups": len(findings),
        "auto_safe_groups": sum(
            item.classification == "auto-safe" for item in findings
        ),
        "review_groups": sum(item.classification == "review" for item in findings),
        "unsafe_groups": sum(item.classification == "unsafe" for item in findings),
        "to_delete": 0,
        "deleted": 0,
        "errors": 0,
        "findings": findings,
        "read_only": True,
    }
    if not dry_run:
        counts["errors"] = 1
        counts["message"] = (
            "Duplicate removal is review-only until the Recycle Bin apply path "
            "is enabled."
        )
    return counts


__all__ = [
    "RegularTrack",
    "analyze_regular_duplicates",
    "dedup_regular_folder",
]
