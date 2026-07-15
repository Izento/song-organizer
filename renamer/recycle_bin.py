"""Windows Recycle Bin integration for a future duplicate-apply flow."""

from __future__ import annotations

import ctypes
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .review_models import DuplicateFinding, path_key, sha256_file
from .runtime import atomic_write_json, ensure_app_dirs


class RecycleBinUnavailable(RuntimeError):
    """The current platform cannot provide the requested reversible action."""


@dataclass(frozen=True)
class RecycleResult:
    path: str
    status: str
    message: str = ""


def send_to_recycle_bin(path: str) -> None:
    """Move one file to the Windows Recycle Bin without permanent deletion."""
    if os.name != "nt":
        raise RecycleBinUnavailable("The Windows Recycle Bin is unavailable here")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("wFunc", ctypes.c_uint),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", ctypes.c_int),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]

    operation = SHFILEOPSTRUCTW()
    operation.wFunc = 3  # FO_DELETE
    operation.pFrom = f"{str(Path(path).resolve())}\0\0"
    operation.fFlags = 0x40 | 0x10 | 0x400 | 0x4  # undo, no UI, silent
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result:
        raise OSError(result, f"Recycle Bin move failed for {path}")
    if operation.fAnyOperationsAborted:
        raise OSError("Recycle Bin move was aborted")


def apply_selected_duplicates(
    finding: DuplicateFinding,
    selected_paths: list[str] | tuple[str, ...],
) -> list[RecycleResult]:
    """Move explicitly selected, unchanged losers to the Recycle Bin.

    The finding must contain hashes captured during analysis.  This function
    records what Windows accepted but deliberately does not promise app-level
    restoration.
    """
    expected_hashes = finding.evidence.get("hashes", {})
    allowed_paths = {path_key(path) for path in finding.paths}
    results: list[RecycleResult] = []
    log = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "finding_id": finding.id,
        "paths": [],
        "restore_note": "Restore is managed by Windows Recycle Bin.",
    }
    for path in selected_paths:
        try:
            if path_key(path) not in allowed_paths:
                raise ValueError(f"Path is not part of the reviewed finding: {path}")
            expected = expected_hashes.get(path)
            if expected is None:
                expected = next(
                    (
                        value
                        for candidate, value in expected_hashes.items()
                        if path_key(candidate) == path_key(path)
                    ),
                    None,
                )
            if not expected:
                raise RuntimeError(f"No content hash was captured for {path}")
            if sha256_file(path) != expected:
                raise RuntimeError(f"File changed since review: {path}")
            send_to_recycle_bin(path)
            result = RecycleResult(path, "succeeded", "Sent to Recycle Bin.")
        except Exception as exc:
            result = RecycleResult(path, "failed", str(exc))
        results.append(result)
        log["paths"].append(asdict(result))

    state = ensure_app_dirs()
    atomic_write_json(
        state["logs"] / f"recycle-{finding.id}.json",
        log,
    )
    return results


__all__ = [
    "RecycleBinUnavailable",
    "RecycleResult",
    "apply_selected_duplicates",
    "send_to_recycle_bin",
]
