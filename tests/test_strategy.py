# pylint: disable=import-error

from renamer.strategy import StrategySample, infer_strategy


def test_track_number_majority_recommends_musicbrainz():
    result = infer_strategy(
        [
            StrategySample("01 Track 1", "filename_norm"),
            StrategySample("02", "filename_norm"),
            StrategySample("Artist - Song", "tag_based"),
        ]
    )

    assert result.strategy == "musicbrainz"
    assert result.counts["musicbrainz"] == 2


def test_reliable_tags_keep_automatic_strategy():
    result = infer_strategy(
        [
            StrategySample("Artist - One", "tag_based"),
            StrategySample("Artist - Two", "tag_based"),
            StrategySample("Artist - Three", "filename_norm"),
        ]
    )

    assert result.strategy is None
    assert "embedded tags" in result.note


def test_weak_evidence_recommends_filename_normalization():
    result = infer_strategy(
        [
            StrategySample("Unknown One", "filename_norm"),
            StrategySample("Unknown Two", "filename_norm"),
        ]
    )

    assert result.strategy == "filename_norm"
