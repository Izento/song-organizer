# pylint: disable=import-error

import json
from pathlib import Path

from renamer import apply as apply_module
from renamer.media import MediaRead
from renamer.review_models import (
    ApplyResult,
    FileSnapshot,
    RenameProposal,
    ReviewPlan,
    TagProposal,
)


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


def test_recovery_queries_can_be_scoped_to_review_root(tmp_path, monkeypatch):
    state = tmp_path / "state"
    paths = _test_app_paths(state)
    monkeypatch.setattr(apply_module, "ensure_app_dirs", lambda: paths)
    first_root = tmp_path / "first-music"
    second_root = tmp_path / "second-music"

    for batch_id, root in (("first", first_root), ("second", second_root)):
        (paths["journals"] / f"{batch_id}.json").write_text(
            json.dumps(
                {
                    "batch_id": batch_id,
                    "root": str(root),
                    "status": "applying",
                    "actions": [{"status": "completed"}],
                }
            ),
            encoding="utf-8",
        )

    assert [
        batch["batch_id"]
        for batch in apply_module.batches_requiring_recovery(str(first_root))
    ] == ["first"]
    assert [
        batch["batch_id"]
        for batch in apply_module.batches_requiring_recovery(str(second_root))
    ] == ["second"]
    assert (
        apply_module.latest_undoable_batch(str(second_root))["batch_id"]
        == "second"
    )


def test_apply_uses_reviewed_rename_and_undo(tmp_path, monkeypatch):
    source = tmp_path / "old.mp3"
    destination = tmp_path / "new.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        apply_module, "ensure_app_dirs", lambda: _test_app_paths(tmp_path / "state")
    )
    snapshot = FileSnapshot.capture(str(source))
    proposal = RenameProposal(
        id="rename-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(destination),
        current_values={"filename": source.name},
        proposed_values={"filename": destination.name},
        confidence="high",
        reason="test",
    )
    plan = ReviewPlan.create(str(tmp_path), False, rename_proposals=[proposal])

    results = apply_module.apply_review_plan(plan, [proposal.id])

    assert results[0].status == "succeeded"
    assert destination.read_bytes() == b"audio"
    assert not source.exists()
    assert apply_module.latest_undoable_batch()["batch_id"] == plan.batch_id

    undo_results = apply_module.undo_batch(plan.batch_id)

    assert undo_results[0].status == "succeeded"
    assert source.read_bytes() == b"audio"
    assert apply_module.latest_undoable_batch() is None


def test_tag_apply_restores_backup(tmp_path, monkeypatch):
    source = tmp_path / "Artist - Song.mp3"
    source.write_bytes(b"audio")
    state = tmp_path / "state"
    monkeypatch.setattr(apply_module, "ensure_app_dirs", lambda: _test_app_paths(state))
    written = []
    monkeypatch.setattr(
        apply_module,
        "write_tags_to_file",
        lambda path, after: written.append((path, after)) or {"status": "updated"},
    )
    monkeypatch.setattr(
        apply_module,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Artist", "title": "Song"},
        ),
    )
    snapshot = FileSnapshot.capture(
        str(source), tags={"artist": "Wrong", "title": "Wrong"}
    )
    proposal = TagProposal(
        id="tag-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        path=str(source),
        before={"artist": "Wrong", "title": "Wrong"},
        after={"artist": "Artist", "title": "Song"},
        confidence="high",
        reason="test",
    )
    plan = ReviewPlan.create(str(tmp_path), False, tag_proposals=[proposal])

    results = apply_module.apply_review_plan(plan, [proposal.id])

    assert results[0].status == "succeeded"
    assert Path(results[0].backup_path).exists()
    assert written == [(str(source), {"artist": "Artist", "title": "Song"})]


def test_apply_rejects_existing_unrelated_destination(tmp_path, monkeypatch):
    source = tmp_path / "old.mp3"
    destination = tmp_path / "new.mp3"
    source.write_bytes(b"source")
    destination.write_bytes(b"unrelated")
    monkeypatch.setattr(
        apply_module, "ensure_app_dirs", lambda: _test_app_paths(tmp_path / "state")
    )
    proposal = RenameProposal(
        id="rename-collision",
        decision_group_id="group",
        snapshot=FileSnapshot.capture(str(source)),
        old_path=str(source),
        new_path=str(destination),
        current_values={"filename": source.name},
        proposed_values={"filename": destination.name},
        confidence="high",
        reason="test",
    )
    plan = ReviewPlan.create(str(tmp_path), False, rename_proposals=[proposal])

    results = apply_module.apply_review_plan(plan, [proposal.id])

    assert results[0].status == "blocked"
    assert source.read_bytes() == b"source"
    assert destination.read_bytes() == b"unrelated"


def test_apply_writes_reviewed_tags_for_nonstandard_filename(tmp_path, monkeypatch):
    source = tmp_path / "NoArtistTitle.mp3"
    source.write_bytes(b"source")
    monkeypatch.setattr(
        apply_module, "ensure_app_dirs", lambda: _test_app_paths(tmp_path / "state")
    )
    monkeypatch.setattr(
        apply_module,
        "write_tags_to_file",
        lambda _path, _after: {"status": "updated"},
    )
    monkeypatch.setattr(
        apply_module,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "New", "title": "Title"},
        ),
    )
    proposal = TagProposal(
        id="tag-unsupported-name",
        decision_group_id="group",
        snapshot=FileSnapshot.capture(str(source)),
        path=str(source),
        before={"artist": "Old", "title": "Title"},
        after={"artist": "New", "title": "Title"},
        confidence="high",
        reason="test",
    )
    plan = ReviewPlan.create(str(tmp_path), False, tag_proposals=[proposal])

    results = apply_module.apply_review_plan(plan, [proposal.id])

    assert results[0].status == "succeeded"
    assert source.read_bytes() == b"source"


def test_apply_preflights_unsupported_tag_file_type(tmp_path, monkeypatch):
    source = tmp_path / "Artist - Song.wav"
    source.write_bytes(b"source")
    monkeypatch.setattr(
        apply_module, "ensure_app_dirs", lambda: _test_app_paths(tmp_path / "state")
    )
    proposal = TagProposal(
        id="tag-unsupported-type",
        decision_group_id="group",
        snapshot=FileSnapshot.capture(str(source)),
        path=str(source),
        before={"artist": "Old", "title": "Song"},
        after={"artist": "Artist", "title": "Song"},
        confidence="high",
        reason="test",
    )
    plan = ReviewPlan.create(str(tmp_path), False, tag_proposals=[proposal])

    results = apply_module.apply_review_plan(plan, [proposal.id])

    assert results[0].status == "blocked"
    assert "not supported for .wav files" in results[0].message
    assert source.read_bytes() == b"source"


def test_apply_continues_after_failed_tag_write(tmp_path, monkeypatch):
    failed_source = tmp_path / "Artist - Failed.mp3"
    safe_source = tmp_path / "Artist - Safe.mp3"
    failed_source.write_bytes(b"failed")
    safe_source.write_bytes(b"safe")
    monkeypatch.setattr(
        apply_module, "ensure_app_dirs", lambda: _test_app_paths(tmp_path / "state")
    )

    def proposal(identifier: str, source: Path) -> TagProposal:
        return TagProposal(
            id=identifier,
            decision_group_id=identifier,
            snapshot=FileSnapshot.capture(str(source)),
            path=str(source),
            before={"artist": "Old", "title": source.stem},
            after={"artist": "Artist", "title": source.stem},
            confidence="high",
            reason="test",
        )

    failed = proposal("tag-failed", failed_source)
    safe = proposal("tag-safe", safe_source)
    calls = []

    def fake_apply_tag(item, _journal):
        calls.append(item.id)
        if item.id == failed.id:
            return ApplyResult(
                proposal_id=item.id,
                status="failed",
                path=item.path,
                message="Simulated tag write failure.",
            )
        return ApplyResult(
            proposal_id=item.id,
            status="succeeded",
            path=item.path,
            message="Tags written and verified.",
        )

    monkeypatch.setattr(apply_module, "_apply_tag", fake_apply_tag)
    plan = ReviewPlan.create(str(tmp_path), False, tag_proposals=[failed, safe])

    results = apply_module.apply_review_plan(plan, [failed.id, safe.id])

    assert calls == [failed.id, safe.id]
    assert [result.status for result in results] == ["failed", "succeeded"]


def test_apply_continues_after_unrelated_destination_block(tmp_path, monkeypatch):
    safe_source = tmp_path / "safe-source.mp3"
    safe_destination = tmp_path / "safe-destination.mp3"
    blocked_source = tmp_path / "blocked-source.mp3"
    blocked_destination = tmp_path / "blocked-destination.mp3"
    safe_source.write_bytes(b"safe")
    blocked_source.write_bytes(b"blocked")
    blocked_destination.write_bytes(b"existing")
    monkeypatch.setattr(
        apply_module, "ensure_app_dirs", lambda: _test_app_paths(tmp_path / "state")
    )

    safe = RenameProposal(
        id="rename-safe",
        decision_group_id="safe-group",
        snapshot=FileSnapshot.capture(str(safe_source)),
        old_path=str(safe_source),
        new_path=str(safe_destination),
        current_values={"filename": safe_source.name},
        proposed_values={"filename": safe_destination.name},
        confidence="high",
        reason="test",
    )
    blocked = RenameProposal(
        id="rename-blocked",
        decision_group_id="blocked-group",
        snapshot=FileSnapshot.capture(str(blocked_source)),
        old_path=str(blocked_source),
        new_path=str(blocked_destination),
        current_values={"filename": blocked_source.name},
        proposed_values={"filename": blocked_destination.name},
        confidence="high",
        reason="test",
    )
    plan = ReviewPlan.create(
        str(tmp_path),
        False,
        rename_proposals=[safe, blocked],
    )

    results = apply_module.apply_review_plan(plan, [blocked.id, safe.id])
    results_by_id = {result.proposal_id: result for result in results}

    assert results_by_id[blocked.id].status == "blocked"
    assert results_by_id[safe.id].status == "succeeded"
    assert not safe_source.exists()
    assert safe_destination.read_bytes() == b"safe"
    assert blocked_source.read_bytes() == b"blocked"
    assert blocked_destination.read_bytes() == b"existing"
    assert apply_module.batches_requiring_recovery() == []


def test_review_plan_round_trips_and_rejects_tampering(tmp_path):
    source = tmp_path / "old.mp3"
    source.write_bytes(b"audio")
    snapshot = FileSnapshot.capture(str(source))
    proposal = RenameProposal(
        id="rename-round-trip",
        decision_group_id="group",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "new.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "new.mp3"},
        confidence="high",
        reason="test",
    )
    plan = ReviewPlan.create(str(tmp_path), False, rename_proposals=[proposal])

    restored = ReviewPlan.from_dict(plan.to_dict())

    assert restored == plan
    tampered = plan.to_dict()
    tampered["rename_proposals"][0]["reason"] = "changed"
    try:
        ReviewPlan.from_dict(tampered)
    except ValueError:
        pass
    else:
        raise AssertionError("Tampered review plan was accepted")


def test_undo_is_idempotent_after_successful_restore(tmp_path, monkeypatch):
    source = tmp_path / "old.mp3"
    destination = tmp_path / "new.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        apply_module, "ensure_app_dirs", lambda: _test_app_paths(tmp_path / "state")
    )
    proposal = RenameProposal(
        id="rename-idempotent",
        decision_group_id="group",
        snapshot=FileSnapshot.capture(str(source)),
        old_path=str(source),
        new_path=str(destination),
        current_values={"filename": source.name},
        proposed_values={"filename": destination.name},
        confidence="high",
        reason="test",
    )
    plan = ReviewPlan.create(str(tmp_path), False, rename_proposals=[proposal])

    apply_module.apply_review_plan(plan, [proposal.id])
    first_undo = apply_module.undo_batch(plan.batch_id)
    second_undo = apply_module.undo_batch(plan.batch_id)

    assert first_undo[0].status == "succeeded"
    assert second_undo == []
    assert source.read_bytes() == b"audio"
