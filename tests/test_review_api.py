# pylint: disable=import-error,protected-access

from renamer import review_api
from renamer.extractor import TrackInfo
from renamer.media import MediaRead


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
