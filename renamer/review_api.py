"""UI-neutral analysis APIs for CLI and tkinter."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import os
from pathlib import Path
from typing import Callable

from .extractor import TrackInfo, extract_track, scan_folder
from .formatter import build_filename, split_feat
from .media import read_media
from .musicbrainz import enrich_track
from .universal_dedup import analyze_duplicates
from .review_models import (
    FileSnapshot,
    RenameProposal,
    ReviewPlan,
    TagProposal,
    canonical_path,
    path_key,
    proposal_id,
)
from .regular_parser import (
    normalize_text,
    normalize_title_text,
    parse_regular_filename,
    split_feature_names,
)
from .tag_audit import audit_tags_for_folder, expected_tags_from_filename


ProgressCallback = Callable[[str, int, int, str], None]
_ONLINE_EXTRACTION_WORKERS = 4
_READINESS_WARNING_PREFIXES = (
    "Destination collides with another proposal.",
    "Destination already exists:",
)


def _emit(
    callback: ProgressCallback | None,
    stage: str,
    current: int,
    total: int,
    path: str,
) -> None:
    if callback:
        callback(stage, current, total, path)


def _track_values(track) -> dict[str, str]:
    if track.is_ocremix:
        return {
            "artist": normalize_title_text(track.game),
            "title": normalize_title_text(track.title),
            "contributors": ", ".join(
                normalize_title_text(remixer) for remixer in track.remixers
            ),
        }
    return {
        "artist": track.artist,
        "title": normalize_title_text(track.title),
        "contributors": ", ".join(
            normalize_title_text(feature) for feature in track.feat_artists
        ),
    }


def _filename_identity_hint(path: str) -> tuple[str, str] | None:
    """Return a conservative identity hint from a filename.

    A filename can still provide useful evidence when its title is a
    placeholder such as ``Unknown Title``.  Track-number-style names are
    excluded because they are not independent identity evidence.
    """
    parsed = parse_regular_filename(Path(path).name)
    if parsed is not None:
        return parsed.artist, parsed.title

    stem = Path(path).stem.replace("_", " ")
    if " - " not in stem:
        return None
    artist, title = (part.strip() for part in stem.split(" - ", 1))
    artist_key = normalize_text(artist)
    if (
        not artist
        or not title
        or artist_key in {"track", "unknown artist", "various artists", "va"}
        or artist_key.isdigit()
    ):
        return None
    return artist, title


def _tag_filename_conflict(path: str, track) -> str | None:
    if track.strategy != "tag_based":
        return None
    hint = _filename_identity_hint(path)
    if hint is None or not track.artist or not track.title:
        return None
    filename_artist, filename_title = hint
    if (
        normalize_text(filename_artist) == normalize_text(track.artist)
        and normalize_text(filename_title) == normalize_text(track.title)
    ):
        return None
    return (
        "Filename identity "
        f"{filename_artist!r} - {filename_title!r} conflicts with embedded "
        f"tags {track.artist!r} - {track.title!r}; automatic rename blocked."
    )


def _proposal_identity(values: dict[str, str]) -> tuple[str, str, tuple[str, ...]]:
    """Return a comparable artist/title/features identity for review proposals."""
    title, title_features = split_feat(values.get("title", ""))
    features = list(title_features)
    for key in ("contributors", "subtitle"):
        features.extend(split_feature_names(values.get(key, "")))
    return (
        normalize_text(values.get("artist", "")),
        normalize_text(title),
        tuple(sorted(normalize_text(feature) for feature in features)),
    )


def _with_rename_warning(item: RenameProposal, message: str) -> RenameProposal:
    if message in item.warnings:
        return item
    return RenameProposal(
        **{
            **item.to_dict(),
            "snapshot": item.snapshot,
            "warnings": tuple(item.warnings) + (message,),
        }
    )


def _without_readiness_warnings(item: RenameProposal) -> RenameProposal:
    warnings = tuple(
        warning
        for warning in item.warnings
        if not warning.startswith(_READINESS_WARNING_PREFIXES)
    )
    if warnings == item.warnings:
        return item
    return RenameProposal(
        **{
            **item.to_dict(),
            "snapshot": item.snapshot,
            "warnings": warnings,
        }
    )


def _coordinated_tag_proposal(
    rename: RenameProposal,
    existing: TagProposal | None,
) -> TagProposal | None:
    snapshot = existing.snapshot if existing is not None else rename.snapshot
    current = dict(existing.before if existing is not None else snapshot.tags)
    expected, _ = expected_tags_from_filename(rename.new_path, current)
    relevant = sorted(set(current) | set(expected))
    before = {key: current.get(key, "") for key in relevant}
    after = {key: expected.get(key, "") for key in relevant}
    if before == after:
        return None
    digest = {"before": before, "after": after}
    return TagProposal(
        id=proposal_id("tag", rename.old_path, digest),
        decision_group_id=rename.decision_group_id,
        snapshot=snapshot,
        path=rename.old_path,
        before=before,
        after=after,
        confidence=rename.confidence,
        reason="Sync tags to the proposed filename.",
        warnings=rename.warnings,
    )


def coordinate_tag_proposals(
    rename_proposals: list[RenameProposal],
    tag_proposals: list[TagProposal],
) -> tuple[list[TagProposal], list[dict], set[str]]:
    """Align tags with each rename's reviewed final filename."""
    existing_by_group = {
        item.decision_group_id: item
        for item in tag_proposals
    }
    coordinated: list[TagProposal] = []
    issues: list[dict] = []
    renamed_groups = set()
    synchronized_paths = set()

    for rename in rename_proposals:
        renamed_groups.add(rename.decision_group_id)
        synchronized_paths.add(path_key(rename.old_path))
        try:
            proposal = _coordinated_tag_proposal(
                rename,
                existing_by_group.get(rename.decision_group_id),
            )
        except ValueError as exc:
            issues.append(
                {
                    "path": canonical_path(rename.old_path),
                    "category": "tag-sync",
                    "message": f"Tags were not prepared for this rename: {exc}",
                }
            )
            continue
        if proposal is not None:
            coordinated.append(proposal)

    coordinated.extend(
        item
        for item in tag_proposals
        if item.decision_group_id not in renamed_groups
    )
    return coordinated, issues, synchronized_paths


def _mark_unready_destinations(
    proposals: list[RenameProposal],
    destinations: dict[str, list[int]],
) -> list[RenameProposal]:
    source_keys = {path_key(item.old_path) for item in proposals}
    updated = list(proposals)
    for indexes in destinations.values():
        if len(indexes) < 2:
            continue
        for index in indexes:
            updated[index] = _with_rename_warning(
                updated[index],
                "Destination collides with another proposal.",
            )
    for index, item in enumerate(updated):
        if (
            Path(item.new_path).exists()
            and path_key(item.new_path) not in source_keys
        ):
            updated[index] = _with_rename_warning(
                item,
                f"Destination already exists: {item.new_path}",
            )
    return updated


def refresh_rename_readiness(
    proposals: list[RenameProposal] | tuple[RenameProposal, ...],
) -> list[RenameProposal]:
    """Mark destination conflicts that are known during review."""
    cleaned = [_without_readiness_warnings(item) for item in proposals]
    destinations: dict[str, list[int]] = {}
    for index, item in enumerate(cleaned):
        destinations.setdefault(path_key(item.new_path), []).append(index)
    return _mark_unready_destinations(cleaned, destinations)


def _uses_online_extraction(strategy: str | None, acoustid_key: str | None) -> bool:
    return bool(
        acoustid_key
        and strategy not in {"regular", "filename_norm", "musicbrainz"}
    )


def _extract_tracks(
    paths: list[str],
    strategy: str | None,
    acoustid_key: str | None,
    progress: ProgressCallback | None,
    cancel_event,
) -> dict[int, tuple[TrackInfo | None, Exception | None]]:
    """Extract tracks in order, pipelining local fingerprints when online."""
    def extract(path: str) -> tuple[TrackInfo | None, Exception | None]:
        try:
            return extract_track(
                path,
                strategy=strategy,
                acoustid_key=acoustid_key,
            ), None
        except (OSError, ValueError) as exc:
            return None, exc

    if not paths:
        return {}
    if cancel_event is not None and cancel_event.is_set():
        return {}

    if not _uses_online_extraction(strategy, acoustid_key):
        tracks = {}
        for index, path in enumerate(paths):
            if cancel_event is not None and cancel_event.is_set():
                break
            _emit(progress, "extract", index + 1, len(paths), path)
            tracks[index] = extract(path)
        return tracks

    tracks: dict[int, tuple[object | None, Exception | None]] = {}
    executor = ThreadPoolExecutor(
        max_workers=min(_ONLINE_EXTRACTION_WORKERS, len(paths)),
        thread_name_prefix="ballad-fingerprint",
    )
    futures = {}
    next_index = 0
    completed = 0
    try:
        while next_index < len(paths) and len(futures) < _ONLINE_EXTRACTION_WORKERS:
            futures[executor.submit(extract, paths[next_index])] = next_index
            next_index += 1
        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                index = futures.pop(future)
                tracks[index] = future.result()
                completed += 1
                _emit(progress, "extract", completed, len(paths), paths[index])
            if cancel_event is not None and cancel_event.is_set():
                for future in futures:
                    future.cancel()
                break
            while next_index < len(paths) and len(futures) < _ONLINE_EXTRACTION_WORKERS:
                futures[executor.submit(extract, paths[next_index])] = next_index
                next_index += 1
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    return tracks


def plan_renames(
    folder_path: str,
    strategy: str | None = None,
    recursive: bool = True,
    lookup: bool = False,
    acoustid_key: str | None = None,
    progress: ProgressCallback | None = None,
    cancel_event=None,
) -> tuple[list[RenameProposal], list[dict]]:
    """Analyze a folder and return typed rename proposals without mutation."""
    paths = scan_folder(folder_path, recursive=recursive)
    proposals: list[RenameProposal] = []
    issues: list[dict] = []
    extracted = _extract_tracks(
        paths,
        strategy,
        acoustid_key,
        progress,
        cancel_event,
    )

    for index, path in enumerate(paths, start=1):
        result = extracted.get(index - 1)
        if result is None:
            break
        track, extraction_error = result
        if extraction_error is not None:
            issues.append(
                {
                    "path": canonical_path(path),
                    "category": "rename",
                    "message": str(extraction_error),
                }
            )
            continue
        _emit(progress, "review", index, len(paths), path)
        try:
            if track is None:
                raise ValueError("No extractable identity")
            online_conflict = False
            conflict = _tag_filename_conflict(path, track)
            if conflict:
                if acoustid_key:
                    identified = extract_track(
                        path,
                        strategy=strategy,
                        acoustid_key=acoustid_key,
                        prefer_acoustid=True,
                    )
                    if identified.strategy == "acoustid":
                        track = identified
                        online_conflict = True
                        conflict = None
                if conflict:
                    issues.append(
                        {
                            "path": canonical_path(path),
                            "category": "identity-conflict",
                            "message": conflict,
                        }
                    )
                    continue
            if lookup and track.needs_lookup:
                enrich_track(track)
            if track.skip_reason:
                issues.append(
                    {
                        "path": canonical_path(path),
                        "category": "rename",
                        "message": track.skip_reason,
                    }
                )
                continue
            if not any((track.artist, track.title, track.game)):
                issues.append(
                    {
                        "path": canonical_path(path),
                        "category": "rename",
                        "message": "No extractable identity",
                    }
                )
                continue

            media = read_media(path)
            snapshot = FileSnapshot.capture(path, tags=media.tags, include_hash=True)
            new_name = build_filename(track)
            new_path = os.path.join(os.path.dirname(path), new_name)
            current_name = os.path.basename(path)
            if current_name == new_name:
                continue
            values = _track_values(track)
            confidence = {
                "tag_based": "high",
                "filename_norm": "medium",
                "acoustid": "medium",
                "musicbrainz": "medium",
            }.get(track.strategy, "low")
            warnings = []
            if track.strategy in {"acoustid", "musicbrainz"}:
                warnings.append(f"Identity came from {track.strategy}.")
            if track.strategy == "acoustid" and track.acoustid_score is not None:
                warnings.append(f"Audio match score: {track.acoustid_score:.3f}.")
            if track.version_warning:
                warnings.append(track.version_warning)
            if online_conflict:
                warnings.append(
                    "Embedded tags conflicted with the filename and were not used."
                )
            reason = f"Normalized using {track.strategy or 'automatic'} evidence."
            if track.strategy == "acoustid" and track.acoustid_recording_id:
                reason += (
                    f" AcoustID recording {track.acoustid_recording_id} "
                    "was retained as evidence."
                )
            item = RenameProposal(
                id=proposal_id("rename", path, new_path),
                decision_group_id=path_key(path),
                snapshot=snapshot,
                old_path=snapshot.path,
                new_path=canonical_path(new_path),
                current_values={"filename": current_name, **values},
                proposed_values={"filename": new_name, **values},
                confidence=confidence,
                reason=reason,
                warnings=tuple(warnings),
            )
            proposals.append(item)
        except (OSError, ValueError) as exc:
            issues.append(
                {
                    "path": canonical_path(path),
                    "category": "rename",
                    "message": str(exc),
                }
            )

    return refresh_rename_readiness(proposals), issues


def plan_tag_updates(
    folder_path: str,
    recursive: bool = True,
    progress: ProgressCallback | None = None,
    cancel_event=None,
):
    return audit_tags_for_folder(
        folder_path,
        recursive=recursive,
        progress=(
            (lambda current, total, path: _emit(
                progress, "tag-audit", current, total, path
            ))
            if progress
            else None
        ),
        cancel_event=cancel_event,
    )


def analyze_folder(
    folder_path: str,
    strategy: str | None = None,
    recursive: bool = True,
    lookup: bool = False,
    acoustid_key: str | None = None,
    include_duplicates: bool = True,
    fingerprint: bool = False,
    progress: ProgressCallback | None = None,
    cancel_event=None,
) -> ReviewPlan:
    """Build one immutable review plan for the selected root."""
    rename_proposals, rename_issues = plan_renames(
        folder_path,
        strategy=strategy,
        recursive=recursive,
        lookup=lookup,
        acoustid_key=acoustid_key,
        progress=progress,
        cancel_event=cancel_event,
    )
    tag_proposals, tag_issues = plan_tag_updates(
        folder_path,
        recursive=recursive,
        progress=progress,
        cancel_event=cancel_event,
    )
    tag_proposals, coordination_issues, synchronized_paths = coordinate_tag_proposals(
        rename_proposals,
        tag_proposals,
    )
    tag_issues = [
        issue
        for issue in tag_issues
        if path_key(issue.get("path", "")) not in synchronized_paths
    ]
    tag_issues.extend(coordination_issues)
    duplicate_findings = []
    duplicate_issues = []
    if include_duplicates and not (
        cancel_event is not None and cancel_event.is_set()
    ):
        try:
            duplicate_findings = analyze_duplicates(
                folder_path,
                recursive=recursive,
                progress=(
                    lambda current, total, path: _emit(
                        progress, "duplicate-audit", current, total, path
                    )
                    if progress
                    else None
                ),
                cancel_event=cancel_event,
                fingerprint=fingerprint,
            )
        except (OSError, ValueError) as exc:
            duplicate_issues.append(
                {
                    "path": canonical_path(folder_path),
                    "category": "duplicate-audit",
                    "message": str(exc),
                }
            )
    return ReviewPlan.create(
        root=folder_path,
        recursive=recursive,
        rename_proposals=rename_proposals,
        tag_proposals=tag_proposals,
        duplicate_findings=duplicate_findings,
        issues=rename_issues + tag_issues + duplicate_issues,
    )


__all__ = [
    "analyze_folder",
    "coordinate_tag_proposals",
    "plan_renames",
    "plan_tag_updates",
    "refresh_rename_readiness",
]
