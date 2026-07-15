import json
from pathlib import Path

from renamer import recycle_bin
from renamer.review_models import DuplicateFinding, sha256_file


def _test_app_paths(root: Path):
    paths = {
        "root": root,
        "config": root / "config.yaml",
        "cache": root / "Cache",
        "backups": root / "Backups",
        "journals": root / "Journals",
        "logs": root / "Logs",
    }
    for key, path in paths.items():
        if key != "config":
            path.mkdir(parents=True, exist_ok=True)
    return paths


def test_selected_duplicate_requires_unchanged_hash(tmp_path, monkeypatch):
    source = tmp_path / "copy.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        recycle_bin,
        "ensure_app_dirs",
        lambda: _test_app_paths(tmp_path / "state"),
    )
    moved = []
    monkeypatch.setattr(
        recycle_bin,
        "send_to_recycle_bin",
        lambda path: moved.append(path),
    )
    finding = DuplicateFinding(
        id="finding-1",
        paths=(str(source),),
        classification="auto-safe",
        recommendation="test",
        evidence={"hashes": {str(source): sha256_file(str(source))}},
        confidence="high",
    )

    results = recycle_bin.apply_selected_duplicates(finding, [str(source)])

    assert results[0].status == "succeeded"
    assert moved == [str(source)]
    log = tmp_path / "state" / "Logs" / "recycle-finding-1.json"
    assert json.loads(log.read_text(encoding="utf-8"))["paths"][0]["status"] == "succeeded"


def test_selected_duplicate_rejects_missing_hash(tmp_path, monkeypatch):
    source = tmp_path / "copy.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        recycle_bin,
        "ensure_app_dirs",
        lambda: _test_app_paths(tmp_path / "state"),
    )
    finding = DuplicateFinding(
        id="finding-2",
        paths=(str(source),),
        classification="review",
        recommendation="test",
        evidence={"hashes": {str(source): None}},
        confidence="medium",
    )

    results = recycle_bin.apply_selected_duplicates(finding, [str(source)])

    assert results[0].status == "failed"
    assert "hash" in results[0].message.lower()
