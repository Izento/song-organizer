"""Transactional apply services for reviewed rename and tag proposals."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from .media import read_media
from .review_models import (
    ApplyResult,
    RenameProposal,
    ReviewPlan,
    TagProposal,
    canonical_path,
    path_key,
    sha256_file,
)
from .runtime import atomic_write_json, ensure_app_dirs
from .tag_writer import parse_stem, supports_tag_writing, write_tags_to_file


ProgressCallback = Callable[[str, int, int, ApplyResult | None], None]


class ApplyBlocked(RuntimeError):
    """Raised when a reviewed batch cannot pass its global preflight."""


def _error_details(exc: BaseException) -> tuple[int | None, int | None]:
    return getattr(exc, "errno", None), getattr(exc, "winerror", None)


def _result_error(item_id: str, path: str, exc: BaseException) -> ApplyResult:
    errno, winerror = _error_details(exc)
    return ApplyResult(
        proposal_id=item_id,
        status="failed",
        path=path,
        message=str(exc),
        error_type=type(exc).__name__,
        os_error=errno,
        winerror=winerror,
    )


def _same_path(left: str, right: str) -> bool:
    return path_key(left) == path_key(right)


def _retry_filesystem(operation, attempts: int = 3):
    for attempt in range(attempts):
        try:
            return operation()
        except OSError as exc:
            if getattr(exc, "winerror", None) not in {32, 33} or attempt == attempts - 1:
                raise
            time.sleep(0.25 * (attempt + 1))


def _rename_with_retry(source: str, destination: str) -> None:
    _retry_filesystem(lambda: os.rename(source, destination))


def _copy_with_retry(source: str, destination: str) -> None:
    _retry_filesystem(lambda: shutil.copy2(source, destination))


def _blocked_result(item_id: str, path: str, message: str) -> ApplyResult:
    return ApplyResult(
        proposal_id=item_id,
        status="blocked",
        path=path,
        message=message,
        error_type="ApplyBlocked",
    )


def _selected_proposals(
    plan: ReviewPlan, selected_ids: Iterable[str]
) -> tuple[list[RenameProposal], list[TagProposal]]:
    selected = set(selected_ids)
    renames = [item for item in plan.rename_proposals if item.id in selected]
    tags = [item for item in plan.tag_proposals if item.id in selected]
    unknown = selected - {item.id for item in renames + tags}
    if unknown:
        raise ApplyBlocked(f"Unknown proposal IDs: {', '.join(sorted(unknown))}")
    return renames, tags


def _preflight(
    renames: list[RenameProposal],
    tags: list[TagProposal],
) -> tuple[list[RenameProposal], list[TagProposal], list[ApplyResult]]:
    blocked: dict[str, ApplyResult] = {}

    def block(item_id: str, path: str, message: str) -> None:
        blocked.setdefault(item_id, _blocked_result(item_id, path, message))

    for item in renames:
        if not os.path.isfile(item.old_path):
            block(item.id, item.old_path, f"Source file is missing: {item.old_path}")
        elif not item.snapshot.matches(item.old_path):
            block(
                item.id,
                item.old_path,
                f"Source changed since analysis: {item.old_path}",
            )

    for item in tags:
        if not os.path.isfile(item.path):
            block(item.id, item.path, f"Tag source is missing: {item.path}")
        elif not item.snapshot.matches(item.path):
            block(
                item.id,
                item.path,
                f"Tag source changed since analysis: {item.path}",
            )
        elif not supports_tag_writing(item.path):
            extension = Path(item.path).suffix.lower() or "this file type"
            block(
                item.id,
                item.path,
                f"Tag writing is not supported for {extension} files.",
            )
        elif parse_stem(Path(item.path).stem) is None:
            block(
                item.id,
                item.path,
                f"Tag filename is not supported by the writer: {item.path}"
            )

    rename_sources: dict[str, list[RenameProposal]] = {}
    for item in renames:
        if item.id not in blocked:
            rename_sources.setdefault(path_key(item.old_path), []).append(item)
    for items in rename_sources.values():
        if len(items) > 1:
            for item in items:
                block(
                    item.id,
                    item.old_path,
                    "Multiple rename proposals target one source file.",
                )

    tag_sources: dict[str, list[TagProposal]] = {}
    for item in tags:
        if item.id not in blocked:
            tag_sources.setdefault(path_key(item.path), []).append(item)
    for items in tag_sources.values():
        if len(items) > 1:
            for item in items:
                block(
                    item.id,
                    item.path,
                    "Multiple tag proposals target one source file.",
                )

    renames_by_group = {item.decision_group_id: item for item in renames}
    for tag in tags:
        rename = renames_by_group.get(tag.decision_group_id)
        if rename is None:
            continue
        conflicting = any(
            "Conflicts with" in warning
            for warning in (*rename.warnings, *tag.warnings)
        )
        if conflicting:
            message = f"Conflicting rename and tag decisions selected for {tag.path}"
            block(rename.id, rename.old_path, message)
            block(tag.id, tag.path, message)

    eligible_tags = [item for item in tags if item.id not in blocked]
    if eligible_tags:
        try:
            backup_root = ensure_app_dirs()["backups"]
            required = sum(os.path.getsize(item.path) for item in eligible_tags)
            free = shutil.disk_usage(backup_root).free
        except OSError as exc:
            message = f"Cannot inspect backup space: {exc}"
            for item in eligible_tags:
                block(item.id, item.path, message)
        else:
            if free < required:
                message = (
                    f"Insufficient free space for tag backups "
                    f"({required} bytes needed)"
                )
                for item in eligible_tags:
                    block(item.id, item.path, message)

    rename_destinations: dict[str, RenameProposal] = {}
    for item in renames:
        if item.id in blocked:
            continue
        destination_key = path_key(item.new_path)
        if destination_key in rename_destinations:
            other = rename_destinations[destination_key]
            message = f"Multiple selected proposals target {item.new_path}"
            block(other.id, other.old_path, message)
            block(item.id, item.old_path, message)
            continue
        rename_destinations[destination_key] = item

    changed = True
    while changed:
        changed = False
        source_keys = {
            path_key(item.old_path)
            for item in renames
            if item.id not in blocked
        }
        for item in renames:
            if item.id in blocked:
                continue
            parent = Path(item.new_path).parent
            if not parent.is_dir():
                block(
                    item.id,
                    item.old_path,
                    f"Destination folder does not exist: {parent}",
                )
                changed = True
                continue
            try:
                existing_keys = {
                    path_key(str(candidate))
                    for candidate in parent.iterdir()
                    if candidate.exists()
                }
            except OSError as exc:
                block(
                    item.id,
                    item.old_path,
                    f"Cannot inspect destination folder: {exc}",
                )
                changed = True
                continue
            destination_key = path_key(item.new_path)
            if (
                destination_key in existing_keys
                and destination_key not in source_keys
            ):
                block(
                    item.id,
                    item.old_path,
                    f"Destination already exists: {item.new_path}",
                )
                changed = True

    safe_renames = [item for item in renames if item.id not in blocked]
    safe_tags = [item for item in tags if item.id not in blocked]
    blocked_results = [
        blocked[item.id]
        for item in (*renames, *tags)
        if item.id in blocked
    ]
    return safe_renames, safe_tags, blocked_results


def _journal_path(batch_id: str) -> Path:
    return ensure_app_dirs()["journals"] / f"{batch_id}.json"


class BatchJournal:
    def __init__(self, plan: ReviewPlan, selected_ids: Iterable[str]):
        self.path = _journal_path(plan.batch_id)
        self.data = {
            "batch_id": plan.batch_id,
            "plan_digest": plan.digest,
            "schema_version": plan.schema_version,
            "app_version": plan.app_version,
            "root": plan.root,
            "created_at": plan.created_at,
            "status": "preflighting",
            "selected_ids": sorted(selected_ids),
            "plan": plan.to_dict(),
            "events": [],
            "actions": [],
        }
        self.flush()

    def flush(self) -> None:
        atomic_write_json(self.path, self.data)

    def event(self, kind: str, **payload) -> None:
        self.data["events"].append(
            {
                "kind": kind,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **payload,
            }
        )
        self.flush()

    def intent(self, kind: str, **payload) -> int:
        action = {
            "kind": kind,
            "status": "intent",
            "intent_timestamp": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        self.data["actions"].append(action)
        self.flush()
        return len(self.data["actions"]) - 1

    def complete(self, index: int, **payload) -> None:
        self.data["actions"][index].update(
            {
                "status": "completed",
                "completed_timestamp": datetime.now(timezone.utc).isoformat(),
                **payload,
            }
        )
        self.flush()

    def fail(self, index: int, **payload) -> None:
        self.data["actions"][index].update(
            {
                "status": "failed",
                "failed_timestamp": datetime.now(timezone.utc).isoformat(),
                **payload,
            }
        )
        self.flush()

    def finish(self, status: str) -> None:
        self.data["status"] = status
        self.data["finished_at"] = datetime.now(timezone.utc).isoformat()
        self.flush()


def _backup_path(batch_id: str, source: str) -> Path:
    backup_dir = ensure_app_dirs()["backups"] / batch_id
    backup_dir.mkdir(parents=True, exist_ok=True)
    path_digest = hashlib.sha256(canonical_path(source).encode("utf-8")).hexdigest()[:16]
    safe_name = f"{path_digest}-{Path(source).name}"
    return backup_dir / safe_name


def _apply_tag(
    item: TagProposal,
    journal: BatchJournal,
) -> ApplyResult:
    backup = _backup_path(journal.data["batch_id"], item.path)
    action_index: int | None = None
    try:
        if not item.snapshot.matches(item.path):
            raise ApplyBlocked(f"Tag source changed since preflight: {item.path}")
        _retry_filesystem(lambda: shutil.copy2(item.path, backup))
        post_hash_before = sha256_file(item.path)
        action_index = journal.intent(
            "tag",
            proposal_id=item.id,
            path=item.path,
            backup_path=str(backup),
            before=item.before,
            after=item.after,
        )
        result = _retry_filesystem(lambda: write_tags_to_file(item.path))
        if result.get("status") not in {"updated", "already_ok"}:
            raise ApplyBlocked(result.get("reason", "Tag writer skipped file"))
        media = read_media(item.path)
        for key, expected in item.after.items():
            if media.tags.get(key, "") != expected:
                raise ApplyBlocked(
                    f"Tag verification failed for {key}: expected {expected!r}, "
                    f"got {media.tags.get(key, '')!r}"
                )
        post_hash = sha256_file(item.path)
        journal.complete(
            action_index,
            status="completed",
            post_hash=post_hash,
            original_hash=post_hash_before,
        )
        return ApplyResult(
            proposal_id=item.id,
            status="succeeded",
            path=item.path,
            message="Tags written and verified.",
            backup_path=str(backup),
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        if action_index is not None:
            journal.fail(action_index, error=str(exc))
        try:
            shutil.copy2(backup, item.path)
            if action_index is not None:
                journal.data["actions"][action_index]["rollback_status"] = "succeeded"
                journal.flush()
        except OSError as restore_exc:
            exc = RuntimeError(f"{exc}; automatic restore failed: {restore_exc}")
            if action_index is not None:
                journal.data["actions"][action_index]["rollback_status"] = "failed"
                journal.flush()
        return _result_error(item.id, item.path, exc)


def _temporary_path(path: str, batch_id: str, index: int | str) -> str:
    parent = Path(path).parent
    candidate = parent / f".songorganizer-{batch_id}-{index}.tmp"
    if candidate.exists():
        raise ApplyBlocked(f"Temporary rename path already exists: {candidate}")
    return str(candidate)


def _apply_renames(
    renames: list[RenameProposal],
    journal: BatchJournal,
    cancel_event=None,
    progress: ProgressCallback | None = None,
    tag_paths: set[str] | None = None,
) -> list[ApplyResult]:
    if not renames:
        return []

    source_keys = {path_key(item.old_path) for item in renames}
    staged: dict[str, str] = {}
    for index, item in enumerate(renames):
        if path_key(item.new_path) in source_keys or _same_path(
            item.old_path, item.new_path
        ):
            staged[item.id] = _temporary_path(
                item.old_path, journal.data["batch_id"], index
            )

    for index, item in enumerate(renames):
        if cancel_event is not None and cancel_event.is_set():
            break
        if not tag_paths or path_key(item.old_path) not in tag_paths:
            if not item.snapshot.matches(item.old_path):
                return [
                    ApplyResult(
                        proposal_id=item.id,
                        status="stale",
                        path=item.old_path,
                        message="Source changed before rename.",
                        error_type="ApplyBlocked",
                    )
                ]
        current = item.old_path
        if item.id in staged:
            temporary = staged[item.id]
            action_index = journal.intent(
                "rename-stage",
                proposal_id=item.id,
                old=current,
                new=temporary,
            )
            try:
                _rename_with_retry(current, temporary)
                journal.complete(action_index)
                current = temporary
            except OSError as exc:
                journal.fail(action_index, error=str(exc))
                return [_result_error(item.id, item.old_path, exc)]

    results: list[ApplyResult] = []
    for index, item in enumerate(renames):
        if cancel_event is not None and cancel_event.is_set():
            results.append(
                ApplyResult(
                    proposal_id=item.id,
                    status="cancelled",
                    path=item.old_path,
                    message="Cancellation requested before rename.",
                )
            )
            continue
        current = staged.get(item.id, item.old_path)
        action_index = journal.intent(
            "rename",
            proposal_id=item.id,
            old=item.old_path,
            new=item.new_path,
        )
        try:
            _rename_with_retry(current, item.new_path)
            journal.complete(action_index)
            result = ApplyResult(
                proposal_id=item.id,
                status="succeeded",
                path=item.new_path,
                message="Rename completed.",
            )
        except OSError as exc:
            journal.fail(action_index, error=str(exc))
            result = _result_error(item.id, item.old_path, exc)
        results.append(result)
        if progress:
            progress("rename", index + 1, len(renames), result)
        if result.status == "failed":
            break
    return results


def _ordered_results(
    selected_ids: list[str],
    results: list[ApplyResult],
) -> list[ApplyResult]:
    results_by_id = {result.proposal_id: result for result in results}
    return [
        results_by_id[proposal_id]
        for proposal_id in selected_ids
        if proposal_id in results_by_id
    ]


def apply_review_plan(
    plan: ReviewPlan,
    selected_ids: Iterable[str],
    cancel_event=None,
    progress: ProgressCallback | None = None,
) -> list[ApplyResult]:
    """Apply selected proposals while isolating individually blocked items."""
    selected_ids = list(selected_ids)
    if not selected_ids:
        return []
    try:
        renames, tags = _selected_proposals(plan, selected_ids)
    except ApplyBlocked as exc:
        return [
            _blocked_result(proposal_id, "", str(exc))
            for proposal_id in selected_ids
        ]
    journal = BatchJournal(plan, selected_ids)
    try:
        renames, tags, blocked_results = _preflight(renames, tags)
    except ApplyBlocked as exc:
        journal.event("preflight-failed", message=str(exc))
        journal.finish("blocked")
        return [
            _blocked_result(proposal_id, "", str(exc))
            for proposal_id in selected_ids
        ]

    for result in blocked_results:
        journal.event(
            "proposal-blocked",
            proposal_id=result.proposal_id,
            path=result.path,
            message=result.message,
        )
    journal.event(
        "preflight-passed",
        blocked_count=len(blocked_results),
        actionable_count=len(renames) + len(tags),
    )
    if not renames and not tags:
        journal.finish("completed")
        return blocked_results

    journal.data["status"] = "applying"
    journal.flush()
    results: list[ApplyResult] = list(blocked_results)
    for index, item in enumerate(tags):
        if cancel_event is not None and cancel_event.is_set():
            results.append(
                ApplyResult(
                    proposal_id=item.id,
                    status="cancelled",
                    path=item.path,
                    message="Cancellation requested before tag write.",
                )
            )
            break
        result = _apply_tag(item, journal)
        results.append(result)
        if progress:
            progress("tag", index + 1, len(tags), result)

    rename_results = _apply_renames(
        renames,
        journal,
        cancel_event=cancel_event,
        progress=progress,
        tag_paths={path_key(item.path) for item in tags},
    )
    results.extend(rename_results)
    status = (
        "cancelled"
        if cancel_event is not None and cancel_event.is_set()
        else "completed"
    )
    if any(result.status in {"failed", "stale"} for result in results):
        status = "failed"
    journal.finish(status)
    return _ordered_results(selected_ids, results)


def read_batch(batch_id: str) -> dict:
    path = _journal_path(batch_id)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def undo_batch(batch_id: str) -> list[ApplyResult]:
    """Restore completed actions without overwriting unrelated files."""
    data = read_batch(batch_id)
    results: list[ApplyResult] = []

    def mark_undone(action: dict) -> None:
        action.update(
            {
                "status": "undone",
                "undone_timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    for action in reversed(data.get("actions", [])):
        if action.get("status") != "completed":
            continue
        try:
            if action["kind"] == "tag":
                path = action["path"]
                if not os.path.exists(action["backup_path"]):
                    raise FileNotFoundError(action["backup_path"])
                if not os.path.exists(path):
                    raise FileNotFoundError(path)
                if action.get("post_hash") and sha256_file(path) != action["post_hash"]:
                    raise ApplyBlocked(f"File changed after apply: {path}")
                _copy_with_retry(action["backup_path"], path)
                results.append(
                    ApplyResult(
                        proposal_id=action["proposal_id"],
                        status="succeeded",
                        path=path,
                        message="Tags restored.",
                    )
                )
                mark_undone(action)
            elif action["kind"] == "rename":
                old = action["old"]
                new = action["new"]
                if not os.path.exists(new):
                    raise FileNotFoundError(new)
                if not _same_path(old, new) and os.path.exists(old):
                    raise ApplyBlocked(f"Restore destination already exists: {old}")
                if _same_path(old, new):
                    temporary = _temporary_path(
                        new,
                        batch_id,
                        f"undo-{action['proposal_id']}",
                    )
                    _rename_with_retry(new, temporary)
                    _rename_with_retry(temporary, old)
                else:
                    _rename_with_retry(new, old)
                results.append(
                    ApplyResult(
                        proposal_id=action["proposal_id"],
                        status="succeeded",
                        path=old,
                        message="Rename restored.",
                    )
                )
                mark_undone(action)
            elif action["kind"] == "rename-stage":
                old = action["old"]
                temporary = action["new"]
                if os.path.exists(temporary) and not os.path.exists(old):
                    _rename_with_retry(temporary, old)
                    results.append(
                        ApplyResult(
                            proposal_id=action["proposal_id"],
                            status="succeeded",
                            path=old,
                            message="Staged rename restored.",
                        )
                    )
                    mark_undone(action)
                elif os.path.exists(old) and not os.path.exists(temporary):
                    mark_undone(action)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            results.append(
                _result_error(action.get("proposal_id", ""), action.get("path", ""), exc)
            )
    data["status"] = "undone" if not any(
        result.status == "failed" for result in results
    ) else "recovery-required"
    atomic_write_json(_journal_path(batch_id), data)
    return results


def incomplete_batches() -> list[dict]:
    journal_dir = ensure_app_dirs()["journals"]
    batches = []
    for path in sorted(journal_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("status") not in {"completed", "undone"}:
            batches.append(data)
    return batches


def batches_requiring_recovery() -> list[dict]:
    """Return interrupted journals that may still represent a file mutation."""
    batches = []
    for batch in incomplete_batches():
        actions = batch.get("actions", ())
        if any(
            action.get("status") in {"intent", "completed"}
            or (
                action.get("status") == "failed"
                and action.get("rollback_status") != "succeeded"
            )
            for action in actions
        ):
            batches.append(batch)
    return batches


def latest_undoable_batch() -> dict | None:
    """Return the newest batch that still has completed actions to undo."""
    recoverable_statuses = {
        "completed",
        "failed",
        "cancelled",
        "applying",
        "recovery-required",
    }
    return next(
        (
            batch
            for batch in batch_history()
            if batch.get("status") in recoverable_statuses
            and any(
                action.get("status") == "completed"
                for action in batch.get("actions", ())
            )
        ),
        None,
    )


def batch_history() -> list[dict]:
    """Return journal summaries for the GUI history view."""
    journal_dir = ensure_app_dirs()["journals"]
    batches = []
    for path in sorted(
        journal_dir.glob("*.json"),
        key=lambda value: value.stat().st_mtime_ns,
        reverse=True,
    ):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        batches.append(data)
    return batches


__all__ = [
    "ApplyBlocked",
    "apply_review_plan",
    "batch_history",
    "batches_requiring_recovery",
    "incomplete_batches",
    "latest_undoable_batch",
    "read_batch",
    "undo_batch",
]
