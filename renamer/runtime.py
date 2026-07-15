"""Portable application paths and atomic state persistence."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


APP_NAME = "SongOrganizer"


def app_data_dir() -> Path:
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if root:
            return Path(root) / APP_NAME
    root = os.environ.get("XDG_DATA_HOME")
    return Path(root) / APP_NAME if root else Path.home() / ".local" / "share" / APP_NAME


def app_paths() -> dict[str, Path]:
    root = app_data_dir()
    return {
        "root": root,
        "config": root / "config.yaml",
        "cache": root / "Cache",
        "backups": root / "Backups",
        "journals": root / "Journals",
        "logs": root / "Logs",
    }


def ensure_app_dirs() -> dict[str, Path]:
    paths = app_paths()
    for key, path in paths.items():
        if key != "config":
            path.mkdir(parents=True, exist_ok=True)
    return paths


def resource_path(name: str) -> Path:
    """Resolve a bundled read-only resource for source and frozen runs."""
    if getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        root = Path(__file__).resolve().parent.parent
    return root / name


def resolve_fpcalc() -> str | None:
    """Find the bundled fingerprint helper before consulting PATH."""
    for candidate in (
        resource_path("fpcalc.exe"),
        resource_path("bin") / "fpcalc.exe",
        resource_path("fpcalc"),
    ):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("fpcalc") or shutil.which("fpcalc.exe")


def resolve_acoustid_key() -> str | None:
    """Resolve an optional AcoustID key without requiring one to be present."""
    key = os.environ.get("ACOUSTID_API_KEY", "").strip()
    if key:
        return key

    try:
        from dotenv import dotenv_values
    except ImportError:
        return None

    candidates = [
        app_data_dir() / ".env",
        Path(sys.executable).parent / ".env",
        resource_path(".env"),
    ]
    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        values = dotenv_values(candidate)
        key = (values.get("ACOUSTID_API_KEY") or "").strip()
        if key:
            return key
    return None


def atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


__all__ = [
    "APP_NAME",
    "app_data_dir",
    "app_paths",
    "atomic_write_json",
    "ensure_app_dirs",
    "resolve_acoustid_key",
    "resolve_fpcalc",
    "resource_path",
]
