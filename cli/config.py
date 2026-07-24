"""Validated folder configuration for CLI commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from renamer.runtime import app_paths


@dataclass(frozen=True)
class FolderConfig:
    path: str
    strategy: str | None = None
    recursive: bool | None = None
    lookup: bool = False

    def recursive_or(self, default: bool) -> bool:
        return default if self.recursive is None else self.recursive


def default_config_path() -> Path:
    return app_paths()["config"]


def _folder_config(value: object, index: int) -> FolderConfig:
    if not isinstance(value, dict):
        raise ValueError(f"Folder entry {index} must be a mapping.")
    path = value.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError(f"Folder entry {index} must contain a non-empty path.")
    strategy = value.get("strategy")
    if strategy is not None and not isinstance(strategy, str):
        raise ValueError(f"Folder entry {index} strategy must be text.")
    recursive = value.get("recursive")
    if recursive is not None and not isinstance(recursive, bool):
        raise ValueError(f"Folder entry {index} recursive must be true or false.")
    lookup = value.get("lookup", False)
    if not isinstance(lookup, bool):
        raise ValueError(f"Folder entry {index} lookup must be true or false.")
    return FolderConfig(
        path=path,
        strategy=strategy,
        recursive=recursive,
        lookup=lookup,
    )


def load_config(path: str | Path) -> list[FolderConfig]:
    config_path = Path(path)
    if not config_path.exists():
        return []
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Could not read configuration {config_path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("Configuration must be a mapping.")
    folders = value.get("folders", [])
    if not isinstance(folders, list):
        raise ValueError("Configuration must contain a folders list.")
    return [_folder_config(entry, index) for index, entry in enumerate(folders, 1)]


def resolve_folders(
    folder: str | None,
    config_path: str | None,
    *,
    strategy: str | None = None,
    lookup: bool = False,
) -> list[FolderConfig]:
    if folder:
        return [
            FolderConfig(
                path=folder,
                strategy=strategy,
                recursive=True,
                lookup=lookup,
            )
        ]
    return load_config(config_path or default_config_path())


__all__ = [
    "FolderConfig",
    "default_config_path",
    "load_config",
    "resolve_folders",
]
