from types import SimpleNamespace

from renamer.formatter import build_filename, safe_part


def test_windows_reserved_and_control_names_are_safe():
    assert safe_part("CON") == "_CON"
    assert "\x00" not in safe_part("bad\x00name")
    assert safe_part("Title: Subtitle") == "Title - Subtitle"


def test_version_labels_are_not_sanitized_away():
    assert safe_part("Song (Radio Edit)") == "Song (Radio Edit)"


def test_build_filename_emits_one_extension_and_clean_features():
    track = SimpleNamespace(
        ext=".mp3.mp3",
        is_ocremix=False,
        artist="Artist",
        title="Song.mp3",
        feat_artists=["Guest", "Guest"],
    )

    assert build_filename(track) == "Artist - Song (feat. Guest).mp3"
