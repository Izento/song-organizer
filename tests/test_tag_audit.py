# pylint: disable=import-error

from renamer.media import MediaRead
from renamer.tag_audit import audit_tag_file, audit_tags_for_folder
from renamer.tag_writer import parse_stem


def test_filename_is_source_of_truth_for_regular_tags(tmp_path, monkeypatch):
    path = tmp_path / "Artist - Song (feat. Guest) (Remix).mp3"
    path.write_bytes(b"fixture")
    monkeypatch.setattr(
        "renamer.tag_audit.read_media",
        lambda _path: MediaRead(
            path=str(path),
            status="ok",
            container="MP3",
            tags={"artist": "Wrong Artist", "title": "Wrong Title"},
        ),
    )

    proposal = audit_tag_file(str(path))

    assert proposal is not None
    assert proposal.before["artist"] == "Wrong Artist"
    assert proposal.after["artist"] == "Artist"
    assert proposal.after["title"] == "Song (Remix) (feat. Guest)"
    assert proposal.confidence == "high"


def test_ocremix_version_label_is_not_written_as_a_remixer():
    parsed = parse_stem("Game - Song Title (Radio Edit) [OC ReMix]")

    assert parsed is not None
    assert parsed["title"] == "Song Title (Radio Edit)"
    assert parsed["remixers"] == []


def test_regular_tag_writer_accepts_compact_hyphen_separator():
    parsed = parse_stem("Noisecontrollers-aliens")

    assert parsed is not None
    assert parsed["artist"] == "Noisecontrollers"
    assert parsed["full_title"] == "aliens"


def test_tag_writer_uses_canonical_regular_identity():
    parsed = parse_stem(
        "Artist - Song ((feat. Guest)) [Extended Mix].mp3.mp3"
    )

    assert parsed is not None
    assert parsed["full_title"] == "Song (Extended Mix) (feat. Guest)"


def test_tag_writer_canonicalizes_ocremix_parentheses_and_extension():
    parsed = parse_stem("Game - Song ((Beatdrop)) [OC ReMix].mp3.mp3")

    assert parsed is not None
    assert parsed["title"] == "Song"
    assert parsed["remixers"] == ["Beatdrop"]


def test_unwritable_tag_format_is_an_analysis_issue(tmp_path, monkeypatch):
    path = tmp_path / "Artist - Song.wav"
    path.write_bytes(b"fixture")
    monkeypatch.setattr(
        "renamer.tag_audit.read_media",
        lambda _path: MediaRead(
            path=str(path),
            status="ok",
            container="WAVE",
            tags={"artist": "Old Artist", "title": "Old Song"},
        ),
    )

    proposals, issues = audit_tags_for_folder(str(tmp_path), recursive=False)

    assert proposals == []
    assert issues == [
        {
            "path": str(path.absolute()),
            "category": "tag-audit",
            "message": "Tag writing is not supported for .wav files",
        }
    ]
