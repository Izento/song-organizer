"""Conservative parsing for ordinary music filenames.

The parser deliberately recognizes only explicit feature markers.  Parentheses
such as ``(Remix)`` and ``(Radio Edit)`` remain part of the title instead of
being mistaken for contributor metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_FEATURE_MARKER = r"(?:feat(?:uring)?\.?|ft\.?|w/)"
_FEATURE_BLOCK_START_RE = re.compile(
    rf"^\s*{_FEATURE_MARKER}\s+",
    re.IGNORECASE,
)
_TRAILING_FEATURE_RE = re.compile(
    rf"(?<!\w){_FEATURE_MARKER}\s+(?P<names>[^()\[\]]+?)\s*$",
    re.IGNORECASE,
)
_FEATURE_IN_ARTIST_RE = re.compile(
    rf"(?<!\w){_FEATURE_MARKER}\s+(?P<names>[^()\[\]-]+?)\s*$",
    re.IGNORECASE,
)
_SEPARATOR_RE = re.compile(r"\s+-\s+")
_FALLBACK_SEPARATOR_RE = re.compile(r"\s*-\s*")
_VERSION_RE = re.compile(r"[\(\[]\s*([^\)\]]+?)\s*[\)\]]")
_WHITESPACE_RE = re.compile(r"\s+")
_PRODUCTION_SUFFIX_RE = re.compile(
    r"(?:\s+|\s*[-–—|]\s*)"
    r"[\(\[]?\s*(?:prod(?:uced)?(?:\s+by)?\.?|production\s+by)"
    r"\s+.+?[\)\]]?\s*$",
    re.IGNORECASE,
)
_PROMO_SUFFIX_RE = re.compile(
    r"(?:\s+|\s*[-–—|]\s*)djleak\.com\s*$",
    re.IGNORECASE,
)
_AUDIO_SUFFIX_RE = re.compile(
    r"(?:\.(?:mp3|flac|ogg|m4a|aac|wma|wav))+$",
    re.IGNORECASE,
)
_DUPLICATE_WRAPPER_RE = re.compile(r"\(\s*\(([^()]*)\)\s*\)")
_EMPTY_PARENTHESES_RE = re.compile(r"\(\s*\)")


@dataclass(frozen=True)
class RegularName:
    """Structured identity parsed from a regular music filename."""

    artist: str
    title: str
    features: tuple[str, ...] = ()
    qualifiers: tuple[str, ...] = ()
    original_stem: str = ""

def normalize_text(value: str) -> str:
    """Normalize text for comparisons without changing display text."""
    value = value.casefold().replace("_", " ")
    return _WHITESPACE_RE.sub(" ", value).strip(" .-_")


def strip_audio_extensions(value: str) -> str:
    """Remove one or more leaked audio extensions from identity text."""
    return _AUDIO_SUFFIX_RE.sub("", value or "")


def _normalize_parentheses(value: str) -> str:
    text = (value or "").translate(str.maketrans({"[": "(", "]": ")"}))
    while True:
        collapsed = _DUPLICATE_WRAPPER_RE.sub(r"(\1)", text)
        if collapsed == text:
            break
        text = collapsed
    text = _EMPTY_PARENTHESES_RE.sub("", text)

    balanced: list[str] = []
    depth = 0
    for character in text:
        if character == "(":
            depth += 1
            balanced.append(character)
        elif character == ")":
            if depth:
                depth -= 1
                balanced.append(character)
        else:
            balanced.append(character)
    if depth:
        balanced.extend(")" for _ in range(depth))
    return "".join(balanced)


def normalize_title_text(value: str) -> str:
    """Clean conservative title noise without changing the track identity."""
    text = strip_audio_extensions(value).replace("_", " ")
    text = _PRODUCTION_SUFFIX_RE.sub("", text)
    text = _PROMO_SUFFIX_RE.sub("", text)
    text = _normalize_parentheses(text)
    return _WHITESPACE_RE.sub(" ", text).strip(" \t.-")


def split_feature_names(raw: str) -> tuple[str, ...]:
    """Split an explicit feature block into stable, display-friendly names."""
    normalized = re.sub(r"\s+(?:and|&)\s+", ", ", raw, flags=re.IGNORECASE)
    names = []
    for item in re.split(r",|;", normalized):
        name = _clean_text(item)
        if name and normalize_text(name) not in {
            normalize_text(existing) for existing in names
        }:
            names.append(name)
    return tuple(names)


def _remove_parenthetical_features(text: str, features: list[str]) -> str:
    cleaned: list[str] = []
    index = 0
    while index < len(text):
        if text[index] != "(":
            cleaned.append(text[index])
            index += 1
            continue

        depth = 1
        end = index + 1
        while end < len(text) and depth:
            if text[end] == "(":
                depth += 1
            elif text[end] == ")":
                depth -= 1
            end += 1
        if depth:
            cleaned.append(text[index])
            index += 1
            continue

        block = text[index + 1 : end - 1]
        marker = _FEATURE_BLOCK_START_RE.match(block)
        if marker:
            features.extend(split_feature_names(block[marker.end() :]))
            cleaned.append(" ")
            index = end
            continue

        cleaned.append(text[index])
        index += 1
    return "".join(cleaned)


def split_features(text: str) -> tuple[str, tuple[str, ...]]:
    """Remove explicit feature blocks while preserving all other title text."""
    features: list[str] = []
    cleaned = _remove_parenthetical_features(normalize_title_text(text), features)
    trailing = _TRAILING_FEATURE_RE.search(cleaned)
    if trailing:
        features.extend(split_feature_names(trailing.group("names")))
        cleaned = cleaned[: trailing.start()]

    unique_features: list[str] = []
    seen = set()
    for feature in features:
        key = normalize_text(feature)
        if key and key not in seen:
            unique_features.append(feature)
            seen.add(key)

    return _clean_text(cleaned), tuple(unique_features)


def _split_artist_features(artist: str) -> tuple[str, tuple[str, ...]]:
    match = _FEATURE_IN_ARTIST_RE.search(artist)
    if not match:
        return _clean_text(artist), ()

    base_artist = _clean_text(artist[: match.start()])
    return base_artist, split_feature_names(match.group("names"))


def _clean_text(value: str) -> str:
    value = _WHITESPACE_RE.sub(" ", value)
    return value.strip(" \t.-")


def _extract_parts(stem: str) -> tuple[str, str] | None:
    match = _SEPARATOR_RE.search(stem)
    if not match:
        match = _FALLBACK_SEPARATOR_RE.search(stem)
    if not match:
        return None

    artist = _clean_text(stem[: match.start()])
    title = _clean_text(stem[match.end() :])
    if not artist or not title:
        return None
    return artist, title


def _qualifiers(title: str) -> tuple[str, ...]:
    values = []
    seen = set()
    for match in _VERSION_RE.finditer(title):
        value = _clean_text(match.group(1))
        key = normalize_text(value)
        if value and key not in seen and not re.match(
            rf"^{_FEATURE_MARKER}\b", value, re.IGNORECASE
        ):
            values.append(value)
            seen.add(key)
    return tuple(values)


def parse_regular_stem(stem: str) -> RegularName | None:
    """Parse a stem such as ``Artist - Title (feat. Guest) (Remix)``."""
    stem = normalize_title_text(stem)
    parts = _extract_parts(stem)
    if parts is None:
        return None

    artist, title_with_features = parts
    artist, artist_features = _split_artist_features(artist)
    title, title_features = split_features(title_with_features)
    features = list(artist_features)
    seen = {normalize_text(value) for value in features}
    for feature in title_features:
        if normalize_text(feature) not in seen:
            features.append(feature)
            seen.add(normalize_text(feature))

    if not artist or not title:
        return None
    if normalize_text(artist) in {"unknown artist", "various artists", "va"}:
        return None
    if normalize_text(title) in {"unknown title", "untitled"}:
        return None

    return RegularName(
        artist=artist,
        title=title,
        features=tuple(features),
        qualifiers=_qualifiers(title),
        original_stem=stem,
    )


def parse_regular_filename(filename: str) -> RegularName | None:
    """Parse a filename, removing only its final extension."""
    return parse_regular_stem(filename)


def format_title(name: RegularName) -> str:
    """Return the normalized title with explicit features appended."""
    title = normalize_title_text(name.title)
    features: list[str] = []
    seen: set[str] = set()
    for feature in name.features:
        cleaned = normalize_title_text(feature)
        key = normalize_text(cleaned)
        if cleaned and key not in seen:
            features.append(cleaned)
            seen.add(key)
    if not features:
        return title
    return f"{title} (feat. {', '.join(features)})"


__all__ = [
    "RegularName",
    "format_title",
    "normalize_text",
    "normalize_title_text",
    "parse_regular_filename",
    "parse_regular_stem",
    "split_feature_names",
    "split_features",
    "strip_audio_extensions",
]
