from renamer.regular_dedup import analyze_regular_duplicates


def test_exact_content_duplicates_are_auto_safe(tmp_path):
    first = tmp_path / "Artist - Song.mp3"
    second = tmp_path / "Artist - Song copy.mp3"
    first.write_bytes(b"same audio bytes")
    second.write_bytes(b"same audio bytes")

    findings = analyze_regular_duplicates(str(tmp_path))

    assert len(findings) == 1
    assert findings[0].classification == "auto-safe"
    assert set(findings[0].paths) == {str(first), str(second)}


def test_versioned_files_are_not_grouped_as_safe_duplicates(tmp_path):
    first = tmp_path / "Artist - Song (Live).mp3"
    second = tmp_path / "Artist - Song (Acoustic).mp3"
    first.write_bytes(b"live recording")
    second.write_bytes(b"acoustic recording")

    findings = analyze_regular_duplicates(str(tmp_path))

    assert len(findings) == 1
    assert findings[0].classification == "unsafe"
