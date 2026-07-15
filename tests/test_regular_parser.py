# pylint: disable=import-error

from renamer.regular_parser import parse_regular_filename


def test_explicit_features_are_extracted_and_versions_preserved():
    parsed = parse_regular_filename(
        "Artist - Song (feat. Guest, Other) (Radio Edit).mp3"
    )

    assert parsed is not None
    assert parsed.artist == "Artist"
    assert parsed.title == "Song (Radio Edit)"
    assert parsed.features == ("Guest", "Other")
    assert parsed.qualifiers == ("Radio Edit",)


def test_version_parenthetical_is_not_a_feature():
    parsed = parse_regular_filename("Artist - Warcraft (Remix).mp3")

    assert parsed is not None
    assert parsed.features == ()
    assert parsed.title == "Warcraft (Remix)"


def test_artist_side_feature_is_supported():
    parsed = parse_regular_filename("Artist ft. Guest - Song (Live).mp3")

    assert parsed is not None
    assert parsed.artist == "Artist"
    assert parsed.features == ("Guest",)
    assert parsed.title == "Song (Live)"


def test_trailing_production_credit_is_removed():
    parsed = parse_regular_filename(
        "Childish Gambino - American Royalty "
        "(feat. Rza, Hypnotic Brass Orchestra) "
        "prod. Childish Gambino-Djleak.com.mp3"
    )

    assert parsed is not None
    assert parsed.title == "American Royalty"
    assert parsed.features == ("Rza", "Hypnotic Brass Orchestra")


def test_known_promo_suffix_is_removed():
    parsed = parse_regular_filename("Artist - Song-Djleak.com.mp3")

    assert parsed is not None
    assert parsed.title == "Song"


def test_extensions_and_parentheses_are_canonicalized():
    parsed = parse_regular_filename(
        "Artist - Song ((feat. Guest)) [Radio Edit].mp3.mp3"
    )

    assert parsed is not None
    assert parsed.title == "Song (Radio Edit)"
    assert parsed.features == ("Guest",)


def test_missing_feature_parenthesis_is_repaired_without_losing_qualifier():
    parsed = parse_regular_filename(
        "Artist - Song (feat. Guest (Ripped Version).mp3"
    )

    assert parsed is not None
    assert parsed.title == "Song"
    assert parsed.features == ("Guest (Ripped Version)",)
