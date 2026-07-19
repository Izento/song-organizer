# pylint: disable=import-error

from renamer import extractor
from renamer.extractor import TrackInfo, extract_track


def test_successful_acoustid_match_precedes_readable_tags(tmp_path, monkeypatch):
    path = tmp_path / "Tagged Artist - Tagged Title.mp3"
    path.write_bytes(b"audio")
    calls = []

    def fake_acoustid(candidate, extension, key):
        calls.append(("acoustid", candidate, extension, key))
        return TrackInfo(
            path=candidate,
            ext=extension,
            artist="AcoustID Artist",
            title="AcoustID Title",
            strategy="acoustid",
        )

    def fake_tags(_path):
        calls.append(("tags",))
        return {"TPE1": "Tagged Artist", "TIT2": "Tagged Title"}

    monkeypatch.setattr(extractor, "_from_acoustid", fake_acoustid)
    monkeypatch.setattr(extractor, "_read_tags", fake_tags)

    result = extract_track(str(path), acoustid_key="test-key")

    assert result.strategy == "acoustid"
    assert result.artist == "AcoustID Artist"
    assert result.title == "AcoustID Title"
    assert calls == [("acoustid", str(path), ".mp3", "test-key")]


def test_missing_acoustid_match_falls_back_to_tags(tmp_path, monkeypatch):
    path = tmp_path / "Tagged Artist - Tagged Title.mp3"
    path.write_bytes(b"audio")

    monkeypatch.setattr(extractor, "_from_acoustid", lambda *_args: None)
    monkeypatch.setattr(
        extractor,
        "_read_tags",
        lambda _path: {"TPE1": "Tagged Artist", "TIT2": "Tagged Title"},
    )

    result = extract_track(str(path), acoustid_key="test-key")

    assert result.strategy == "tag_based"
    assert result.artist == "Tagged Artist"
    assert result.title == "Tagged Title"


def test_missing_acoustid_key_skips_lookup_and_uses_tags(tmp_path, monkeypatch):
    path = tmp_path / "Tagged Artist - Tagged Title.mp3"
    path.write_bytes(b"audio")

    def unexpected_acoustid(*_args):
        raise AssertionError("AcoustID should not run without an API key")

    monkeypatch.setattr(extractor, "_from_acoustid", unexpected_acoustid)
    monkeypatch.setattr(
        extractor,
        "_read_tags",
        lambda _path: {"TPE1": "Tagged Artist", "TIT2": "Tagged Title"},
    )

    result = extract_track(str(path))

    assert result.strategy == "tag_based"
    assert result.artist == "Tagged Artist"
    assert result.title == "Tagged Title"


def test_explicit_filename_strategy_still_overrides_acoustid(
    tmp_path, monkeypatch
):
    path = tmp_path / "Filename Artist - Filename Title.mp3"
    path.write_bytes(b"audio")

    def unexpected_acoustid(*_args):
        raise AssertionError("AcoustID should not run for an explicit strategy")

    monkeypatch.setattr(extractor, "_from_acoustid", unexpected_acoustid)

    result = extract_track(
        str(path),
        strategy="regular",
        acoustid_key="test-key",
    )

    assert result.strategy == "filename_norm"
    assert result.artist == "Filename Artist"
    assert result.title == "Filename Title"
