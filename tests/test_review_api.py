# pylint: disable=import-error,protected-access

import threading
import time

from renamer import review_api
from renamer.extractor import TrackInfo
from renamer.media import MediaRead
from renamer.review_models import FileSnapshot, RenameProposal, TagProposal


def test_rename_analysis_is_read_only(tmp_path):
    source = tmp_path / "artist - song.mp3"
    source.write_bytes(b"fixture")

    proposals, issues = review_api.plan_renames(
        str(tmp_path),
        strategy="regular",
        recursive=False,
    )

    assert source.exists()
    assert issues == []
    assert len(proposals) == 1
    assert proposals[0].old_path == str(source)
    assert proposals[0].new_path.endswith(
        "Artist - song.mp3"
    )


def test_rename_proposal_uses_canonical_filename_identity(tmp_path):
    source = tmp_path / (
        "Artist - Song ((feat. Guest)) "
        "prod. Producer-Djleak.com.mp3"
    )
    source.write_bytes(b"fixture")

    proposals, issues = review_api.plan_renames(
        str(tmp_path),
        strategy="regular",
        recursive=False,
    )

    assert issues == []
    assert len(proposals) == 1
    assert proposals[0].new_path.endswith("Artist - Song (feat. Guest).mp3")


def test_feature_layout_difference_is_not_a_decision_conflict():
    rename_values = {
        "artist": "Artist",
        "title": "Song (Remix)",
        "contributors": "Guest",
    }
    tag_values = {
        "artist": "Artist",
        "title": "Song (Remix) (feat. Guest)",
    }

    assert review_api._proposal_identity(rename_values) == (
        review_api._proposal_identity(tag_values)
    )


def test_online_extraction_pipelines_fingerprints_in_path_order(monkeypatch):
    paths = [f"Artist - Track {index}.mp3" for index in range(5)]
    worker_ids = set()

    def fake_extract(path, **_kwargs):
        worker_ids.add(threading.get_ident())
        time.sleep(0.02)
        return TrackInfo(path=path, ext=".mp3", strategy="acoustid")

    monkeypatch.setattr(review_api, "extract_track", fake_extract)

    extracted = review_api._extract_tracks(
        paths,
        strategy=None,
        acoustid_key="test-key",
        progress=None,
        cancel_event=None,
    )

    assert [extracted[index][0].path for index in range(len(paths))] == paths
    assert len(worker_ids) > 1
    assert len(worker_ids) <= review_api._ONLINE_EXTRACTION_WORKERS


def test_rename_coordinates_linked_tag_values(tmp_path):
    source = tmp_path / "Artist - Old Title.mp3"
    source.write_bytes(b"fixture")
    snapshot = FileSnapshot.capture(
        str(source),
        tags={"artist": "Artist", "title": "Old Title"},
    )
    rename = RenameProposal(
        id="rename-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "New Artist - New Title (feat. Guest).mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "New Artist - New Title (feat. Guest).mp3"},
        confidence="medium",
        reason="online identity",
    )
    stale_tag = TagProposal(
        id="tag-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        path=str(source),
        before={"artist": "Artist", "title": "Old Title"},
        after={"artist": "Artist", "title": "Old Title"},
        confidence="high",
        reason="old filename",
    )

    tags, issues, paths = review_api.coordinate_tag_proposals([rename], [stale_tag])

    assert issues == []
    assert paths == {review_api.path_key(str(source))}
    assert len(tags) == 1
    assert tags[0].confidence == "medium"
    assert tags[0].after == {
        "artist": "New Artist",
        "title": "New Title (feat. Guest)",
    }


def test_existing_destination_is_marked_for_review(tmp_path):
    source = tmp_path / "old.mp3"
    destination = tmp_path / "new.mp3"
    source.write_bytes(b"fixture")
    destination.write_bytes(b"existing")
    proposal = RenameProposal(
        id="rename-1",
        decision_group_id="group-1",
        snapshot=FileSnapshot.capture(str(source)),
        old_path=str(source),
        new_path=str(destination),
        current_values={"filename": source.name},
        proposed_values={"filename": destination.name},
        confidence="high",
        reason="test",
    )

    refreshed = review_api.refresh_rename_readiness([proposal])

    assert refreshed[0].warnings == (
        f"Destination already exists: {destination}",
    )


def test_conflicting_filename_and_tags_block_automatic_rename(
    tmp_path, monkeypatch
):
    source = tmp_path / "Xenosaga - Unknown Title.mp3"
    source.write_bytes(b"fixture")

    monkeypatch.setattr(
        review_api,
        "extract_track",
        lambda path, strategy=None, acoustid_key=None: TrackInfo(
            path=path,
            ext=".mp3",
            artist="Final Fantasy VIII",
            title="Rhinoa's Theme",
            strategy="tag_based",
        ),
    )
    monkeypatch.setattr(
        review_api,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Final Fantasy VIII", "title": "Rhinoa's Theme"},
        ),
    )

    proposals, issues = review_api.plan_renames(
        str(tmp_path),
        recursive=False,
    )

    assert proposals == []
    assert issues[0]["category"] == "identity-conflict"
    assert "automatic rename blocked" in issues[0]["message"]


def test_online_identity_resolves_conflict_without_auto_selecting(
    tmp_path, monkeypatch
):
    source = tmp_path / "Xenosaga - Unknown Title.mp3"
    source.write_bytes(b"fixture")
    calls = []

    def fake_extract(path, prefer_acoustid=False, **_kwargs):
        calls.append(prefer_acoustid)
        if prefer_acoustid:
            return TrackInfo(
                path=path,
                ext=".mp3",
                artist="Yasunori Mitsuda",
                title="Nephilim",
                strategy="acoustid",
                acoustid_score=0.971,
                acoustid_recording_id="recording-1",
            )
        return TrackInfo(
            path=path,
            ext=".mp3",
            artist="Final Fantasy VIII",
            title="Rhinoa's Theme",
            strategy="tag_based",
        )

    monkeypatch.setattr(review_api, "extract_track", fake_extract)
    monkeypatch.setattr(
        review_api,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Final Fantasy VIII", "title": "Rhinoa's Theme"},
        ),
    )

    proposals, issues = review_api.plan_renames(
        str(tmp_path),
        recursive=False,
        acoustid_key="test-key",
    )

    assert calls == [False, True]
    assert issues == []
    assert len(proposals) == 1
    assert proposals[0].new_path.endswith("Yasunori Mitsuda - Nephilim.mp3")
    assert proposals[0].confidence == "medium"
    assert any("Embedded tags conflicted" in warning for warning in proposals[0].warnings)
