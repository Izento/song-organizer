# pylint: disable=import-error

from pathlib import Path

from renamer import apply as apply_module
from renamer.review_api import analyze_folder


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


def test_gui_services_apply_exact_reviewed_plan_and_undo(tmp_path, monkeypatch):
    source = tmp_path / "artist - song.mp3"
    source.write_bytes(b"disposable audio fixture")
    monkeypatch.setattr(
        apply_module,
        "ensure_app_dirs",
        lambda: _test_app_paths(tmp_path / "state"),
    )

    plan = analyze_folder(
        str(tmp_path),
        recursive=False,
        include_duplicates=False,
    )

    assert len(plan.rename_proposals) == 1
    proposal = plan.rename_proposals[0]
    results = apply_module.apply_review_plan(plan, [proposal.id])

    assert results[0].status == "succeeded"
    names_after_apply = {path.name for path in tmp_path.iterdir()}
    assert Path(proposal.new_path).name in names_after_apply
    assert source.name not in names_after_apply

    undo_results = apply_module.undo_batch(plan.batch_id)

    assert undo_results[0].status == "succeeded"
    names_after_undo = {path.name for path in tmp_path.iterdir()}
    assert source.name in names_after_undo
    assert Path(proposal.new_path).name not in names_after_undo
