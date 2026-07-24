# pylint: disable=import-error

import pytest

from cli.config import FolderConfig, load_config, resolve_folders


def test_missing_local_config_is_an_empty_batch(tmp_path):
    assert load_config(tmp_path / "missing.yaml") == []


def test_load_config_returns_validated_folder_values(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
folders:
  - path: D:\\Music
    strategy: regular
    recursive: false
    lookup: true
""".strip(),
        encoding="utf-8",
    )

    assert load_config(path) == [
        FolderConfig(
            path="D:\\Music",
            strategy="regular",
            recursive=False,
            lookup=True,
        )
    ]


def test_invalid_folder_entry_is_rejected(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("folders:\n  - recursive: true\n", encoding="utf-8")

    with pytest.raises(ValueError, match="non-empty path"):
        load_config(path)


def test_explicit_folder_overrides_local_config(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("folders:\n  - path: ignored\n", encoding="utf-8")

    assert resolve_folders(
        "D:\\Selected",
        str(config),
        strategy="filename_norm",
        lookup=True,
    ) == [
        FolderConfig(
            path="D:\\Selected",
            strategy="filename_norm",
            recursive=True,
            lookup=True,
        )
    ]
