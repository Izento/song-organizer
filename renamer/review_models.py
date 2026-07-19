"""Immutable, UI-neutral review and apply models."""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


APP_VERSION = "0.1.0"
PLAN_SCHEMA_VERSION = 1


def canonical_path(path: str) -> str:
    """Return a stable absolute path representation for comparisons."""
    return os.path.abspath(os.path.expanduser(path))


def path_key(path: str) -> str:
    """Return a Windows-like collision key even when tested on another OS."""
    return unicodedata.normalize("NFC", canonical_path(path)).casefold()


def proposal_id(kind: str, path: str, value: Any) -> str:
    payload = json.dumps(
        [kind, canonical_path(path), value],
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    file_id: str | None
    size: int
    mtime_ns: int
    tags: dict[str, str] = field(default_factory=dict)
    sha256: str | None = None

    @classmethod
    def capture(
        cls,
        path: str,
        tags: dict[str, str] | None = None,
        include_hash: bool = False,
    ) -> "FileSnapshot":
        stat = os.stat(path)
        digest = None
        if include_hash:
            digest = sha256_file(path)
            after_hash_stat = os.stat(path)
            if (
                after_hash_stat.st_size != stat.st_size
                or after_hash_stat.st_mtime_ns != stat.st_mtime_ns
            ):
                raise OSError(f"File changed while being read: {path}")
        file_id = f"{getattr(stat, 'st_dev', 0)}:{getattr(stat, 'st_ino', 0)}"
        return cls(
            path=canonical_path(path),
            file_id=file_id,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            tags=dict(tags or {}),
            sha256=digest,
        )

    def matches(self, path: str, require_hash: bool = False) -> bool:
        require_hash = require_hash or self.sha256 is not None
        try:
            current = FileSnapshot.capture(path, include_hash=require_hash)
        except OSError:
            return False
        if path_key(path) != path_key(self.path):
            return False
        if (
            current.file_id != self.file_id
            or current.size != self.size
            or current.mtime_ns != self.mtime_ns
        ):
            return False
        return not require_hash or current.sha256 == self.sha256

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RenameProposal:
    id: str
    decision_group_id: str
    snapshot: FileSnapshot
    old_path: str
    new_path: str
    current_values: dict[str, str]
    proposed_values: dict[str, str]
    confidence: str
    reason: str
    warnings: tuple[str, ...] = ()
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TagProposal:
    id: str
    decision_group_id: str
    snapshot: FileSnapshot
    path: str
    before: dict[str, str]
    after: dict[str, str]
    confidence: str
    reason: str
    warnings: tuple[str, ...] = ()
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DuplicateFinding:
    id: str
    paths: tuple[str, ...]
    classification: str
    recommendation: str
    evidence: dict[str, Any]
    confidence: str
    status: str = "read_only"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewPlan:
    batch_id: str
    schema_version: int
    app_version: str
    root: str
    recursive: bool
    created_at: str
    rename_proposals: tuple[RenameProposal, ...] = ()
    tag_proposals: tuple[TagProposal, ...] = ()
    duplicate_findings: tuple[DuplicateFinding, ...] = ()
    issues: tuple[dict[str, Any], ...] = ()
    digest: str = ""

    @classmethod
    def create(
        cls,
        root: str,
        recursive: bool,
        rename_proposals: list[RenameProposal] | tuple[RenameProposal, ...] = (),
        tag_proposals: list[TagProposal] | tuple[TagProposal, ...] = (),
        duplicate_findings: list[DuplicateFinding] | tuple[DuplicateFinding, ...] = (),
        issues: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    ) -> "ReviewPlan":
        plan = cls(
            batch_id=uuid.uuid4().hex,
            schema_version=PLAN_SCHEMA_VERSION,
            app_version=APP_VERSION,
            root=canonical_path(root),
            recursive=recursive,
            created_at=datetime.now(timezone.utc).isoformat(),
            rename_proposals=tuple(rename_proposals),
            tag_proposals=tuple(tag_proposals),
            duplicate_findings=tuple(duplicate_findings),
            issues=tuple(issues),
        )
        digest = plan._computed_digest()
        return cls(
            batch_id=plan.batch_id,
            schema_version=plan.schema_version,
            app_version=plan.app_version,
            root=plan.root,
            recursive=plan.recursive,
            created_at=plan.created_at,
            rename_proposals=plan.rename_proposals,
            tag_proposals=plan.tag_proposals,
            duplicate_findings=plan.duplicate_findings,
            issues=plan.issues,
            digest=digest,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReviewPlan":
        def snapshot_from(value: dict[str, Any]) -> FileSnapshot:
            return FileSnapshot(**value)

        def rename_from(value: dict[str, Any]) -> RenameProposal:
            return RenameProposal(
                **{
                    **value,
                    "snapshot": snapshot_from(value["snapshot"]),
                    "warnings": tuple(value.get("warnings", ())),
                }
            )

        def tag_from(value: dict[str, Any]) -> TagProposal:
            return TagProposal(
                **{
                    **value,
                    "snapshot": snapshot_from(value["snapshot"]),
                    "warnings": tuple(value.get("warnings", ())),
                }
            )

        def duplicate_from(value: dict[str, Any]) -> DuplicateFinding:
            return DuplicateFinding(
                **{
                    **value,
                    "paths": tuple(value.get("paths", ())),
                }
            )

        plan = cls(
            batch_id=data["batch_id"],
            schema_version=data["schema_version"],
            app_version=data["app_version"],
            root=data["root"],
            recursive=bool(data["recursive"]),
            created_at=data["created_at"],
            rename_proposals=tuple(
                rename_from(value) for value in data.get("rename_proposals", ())
            ),
            tag_proposals=tuple(
                tag_from(value) for value in data.get("tag_proposals", ())
            ),
            duplicate_findings=tuple(
                duplicate_from(value)
                for value in data.get("duplicate_findings", ())
            ),
            issues=tuple(data.get("issues", ())),
            digest=data.get("digest", ""),
        )
        if not plan.validate_digest():
            raise ValueError("Review plan digest does not match its contents")
        return plan

    def _computed_digest(self) -> str:
        payload = self.to_dict(include_digest=False)
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode(
                "utf-8"
            )
        ).hexdigest()

    def validate_digest(self) -> bool:
        return bool(self.digest) and self.digest == self._computed_digest()

    def with_rename_proposals(
        self,
        proposals: list[RenameProposal] | tuple[RenameProposal, ...],
    ) -> "ReviewPlan":
        """Return an equivalent plan with reviewed rename changes re-signed."""
        return self.with_proposals(proposals, self.tag_proposals)

    def with_proposals(
        self,
        rename_proposals: list[RenameProposal] | tuple[RenameProposal, ...],
        tag_proposals: list[TagProposal] | tuple[TagProposal, ...],
    ) -> "ReviewPlan":
        """Return a re-signed plan with coordinated rename and tag changes."""
        updated = replace(
            self,
            rename_proposals=tuple(rename_proposals),
            tag_proposals=tuple(tag_proposals),
            digest="",
        )
        return replace(updated, digest=updated._computed_digest())

    def to_dict(self, include_digest: bool = True) -> dict[str, Any]:
        result = {
            "batch_id": self.batch_id,
            "schema_version": self.schema_version,
            "app_version": self.app_version,
            "root": self.root,
            "recursive": self.recursive,
            "created_at": self.created_at,
            "rename_proposals": [item.to_dict() for item in self.rename_proposals],
            "tag_proposals": [item.to_dict() for item in self.tag_proposals],
            "duplicate_findings": [
                item.to_dict() for item in self.duplicate_findings
            ],
            "issues": list(self.issues),
        }
        if include_digest:
            result["digest"] = self.digest
        return result


@dataclass(frozen=True)
class ApplyResult:
    proposal_id: str
    status: str
    path: str
    message: str = ""
    error_type: str | None = None
    os_error: int | None = None
    winerror: int | None = None
    backup_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = [
    "APP_VERSION",
    "ApplyResult",
    "DuplicateFinding",
    "FileSnapshot",
    "RenameProposal",
    "ReviewPlan",
    "TagProposal",
    "canonical_path",
    "path_key",
    "proposal_id",
    "sha256_file",
]
