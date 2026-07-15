"""Universal, source-neutral duplicate analysis facade."""

from __future__ import annotations

from .regular_dedup import analyze_regular_duplicates, dedup_regular_folder


def analyze_duplicates(
    folder_path: str,
    recursive: bool = False,
    progress=None,
    cancel_event=None,
    fingerprint: bool = False,
):
    """Analyze all supported audio files with one evidence policy."""
    return analyze_regular_duplicates(
        folder_path,
        recursive=recursive,
        progress=progress,
        cancel_event=cancel_event,
        fingerprint=fingerprint,
    )


def dedup_folder(
    folder_path: str,
    dry_run: bool = True,
    recursive: bool = False,
    progress=None,
    cancel_event=None,
    fingerprint: bool = False,
):
    """Compatibility entry point; remains read-only by design."""
    return dedup_regular_folder(
        folder_path,
        dry_run=dry_run,
        recursive=recursive,
        progress=progress,
        cancel_event=cancel_event,
        fingerprint=fingerprint,
    )


__all__ = ["analyze_duplicates", "dedup_folder"]
