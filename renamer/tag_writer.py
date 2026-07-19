# pylint: disable=broad-exception-caught,import-error

"""
tag_writer.py — Write reviewed canonical metadata tags.

The filename parser remains available for legacy CLI callers, but reviewed
GUI plans write their explicit artist/title/album values directly.

Supported formats:
  Regular:   "Artist - Title (feat. X, Y).mp3"  -> TPE1=Artist, TIT2=full title
  OC ReMix:  "Game - Title (Remixer1, Remixer2) [OC ReMix].mp3"
                -> TPE1=game, TIT2=title, TALB=game, TIT3=remixers, TPE2=OverClocked ReMix
"""
import os
import re

from .media import read_media
from .regular_parser import (
    format_title,
    normalize_title_text,
    parse_regular_stem,
    strip_audio_extensions,
)

OCREMIX_RE = re.compile(r'\[OC\s*Re[Mm]ix\]', re.IGNORECASE)
OCREMIX_LABEL_RE = re.compile(r'\bOC\s*Re[Mm]ix\b', re.IGNORECASE)
_VERSION_LABEL_RE = re.compile(
    r"\b(?:acoustic|album|clean|club|demo|edit|extended|instrumental|"
    r"karaoke|live|mix|mono|original|radio|remaster(?:ed)?|reprise|"
    r"single|stereo|version)\b",
    re.IGNORECASE,
)

_SUPPORTED = {'.mp3', '.flac', '.ogg', '.m4a', '.aac', '.wma'}


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------

def supports_tag_writing(path: str) -> bool:
    """Return whether Ballad has a tag writer for this file extension."""
    return os.path.splitext(path)[1].lower() in _SUPPORTED


def _split_final_parenthetical(text: str) -> tuple[str, str] | None:
    """Return (prefix, final_parenthetical) while allowing nested parentheses."""
    text = text.rstrip()
    if not text.endswith(')'):
        return None

    depth = 0
    for idx in range(len(text) - 1, -1, -1):
        ch = text[idx]
        if ch == ')':
            depth += 1
        elif ch == '(':
            depth -= 1
            if depth == 0:
                prefix = text[:idx].strip()
                content = text[idx + 1 : -1].strip()
                if prefix and content:
                    return prefix, content
                return None
    return None


def _clean_ocremix_names(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = normalize_title_text(value)
        key = name.casefold()
        if name and key not in seen:
            cleaned.append(name)
            seen.add(key)
    return cleaned


def _is_title_version_label(text: str) -> bool:
    """True when a final parenthetical is a title/version label, not a remixer."""
    return bool(
        OCREMIX_LABEL_RE.search(text or "") or _VERSION_LABEL_RE.search(text or "")
    )


def parse_stem(stem: str) -> dict | None:
    """
    Parse a normalized filename stem into tag components.

    Returns a dict ready to pass to write_tags_to_file(), or None if the
    filename doesn't match either expected format.
    """
    stem = strip_audio_extensions(stem)
    is_ocremix = bool(OCREMIX_RE.search(stem))

    if is_ocremix:
        clean = normalize_title_text(OCREMIX_RE.sub('', stem).strip())
        if ' - ' not in clean:
            return None
        game, rest = clean.split(' - ', 1)
        rest = rest.strip()
        final_paren = _split_final_parenthetical(rest)
        if final_paren and not _is_title_version_label(final_paren[1]):
            title, remixer_text = final_paren
            remixers = _clean_ocremix_names(remixer_text.split(','))
        else:
            remixers = []
            title = rest
        return {
            'is_ocremix': True,
            'game': normalize_title_text(game),
            'title': normalize_title_text(title),
            'remixers': remixers,
            'artist': normalize_title_text(game),
            # Winamp groups by game name in the Artist field.
        }

    regular = parse_regular_stem(stem)
    if regular is None:
        return None
    artist = regular.artist
    full_title = format_title(regular)

    if not artist or artist == 'Unknown Artist':
        return None
    if not full_title or full_title == 'Unknown Title':
        return None

    return {'is_ocremix': False, 'artist': artist, 'full_title': full_title}


_CANONICAL_TAG_KEYS = {
    "artist",
    "title",
    "album",
    "album_artist",
    "grouping",
    "subtitle",
}


def _parsed_tag_values(parsed: dict) -> dict[str, str]:
    if not parsed["is_ocremix"]:
        return {
            "artist": parsed["artist"],
            "title": parsed["full_title"],
        }
    return {
        "artist": parsed["game"],
        "title": parsed["title"],
        "album": parsed["game"],
        "album_artist": "OverClocked ReMix",
        "grouping": parsed["game"],
        "subtitle": ", ".join(parsed["remixers"]),
    }


def _expected_tag_values(values: dict[str, str]) -> dict[str, str]:
    return {
        key: str(value or "")
        for key, value in values.items()
        if key in _CANONICAL_TAG_KEYS
    }


def _tags_match(expected: dict[str, str], current: dict[str, str]) -> bool:
    return all(current.get(key, "") == value for key, value in expected.items())


# ---------------------------------------------------------------------------
# Tag writing — format-specific helpers
# ---------------------------------------------------------------------------

def _write_mp3(path: str, values: dict[str, str]):
    from mutagen.id3 import (
        ID3, TIT2, TPE1, TALB, TPE2, TIT1, TIT3, ID3NoHeaderError,
    )
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        # File has only ID3v1 or no tags at all — create a fresh ID3v2 block
        tags = ID3()

    frames = {
        "artist": ("TPE1", TPE1),
        "title": ("TIT2", TIT2),
        "album": ("TALB", TALB),
        "album_artist": ("TPE2", TPE2),
        "grouping": ("TIT1", TIT1),
        "subtitle": ("TIT3", TIT3),
    }
    for key, value in values.items():
        frame_id, frame_type = frames[key]
        if value:
            tags.setall(frame_id, [frame_type(encoding=3, text=[value])])
        else:
            tags.delall(frame_id)

    tags.save(path, v2_version=3)


def _write_vorbis(path: str, values: dict[str, str]):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.flac':
        from mutagen.flac import FLAC
        f = FLAC(path)
    else:
        from mutagen.oggvorbis import OggVorbis
        f = OggVorbis(path)

    keys = {
        "artist": "artist",
        "title": "title",
        "album": "album",
        "album_artist": "albumartist",
        "grouping": "grouping",
        "subtitle": "subtitle",
    }
    for key, value in values.items():
        target = keys[key]
        if value:
            f[target] = [value]
        elif target in f:
            del f[target]

    f.save()


def _write_mp4(path: str, values: dict[str, str]):
    from mutagen.mp4 import MP4
    f = MP4(path)
    if f.tags is None:
        f.add_tags()

    keys = {
        "artist": "\xa9ART",
        "title": "\xa9nam",
        "album": "\xa9alb",
        "album_artist": "aART",
        "grouping": "\xa9grp",
        "subtitle": "----:com.apple.iTunes:SUBTITLE",
    }
    for key, value in values.items():
        target = keys[key]
        if value:
            f.tags[target] = [value.encode("utf-8")] if key == "subtitle" else [value]
        else:
            f.tags.pop(target, None)

    f.save()


def _write_asf(path: str, values: dict[str, str]):
    from mutagen.asf import ASF, ASFUnicodeAttribute
    f = ASF(path)

    keys = {
        "artist": "Author",
        "title": "Title",
        "album": "WM/AlbumTitle",
        "album_artist": "WM/AlbumArtist",
        "grouping": "WM/ContentGroupDescription",
        "subtitle": "WM/SubTitle",
    }
    for key, value in values.items():
        target = keys[key]
        if value:
            f[target] = [ASFUnicodeAttribute(value)]
        elif target in f:
            del f[target]

    f.save()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_tags_to_file(
    path: str,
    expected_tags: dict[str, str] | None = None,
) -> dict:
    """
    Write reviewed canonical tags to an audio file.

    When expected_tags is omitted, preserve the legacy filename-derived
    behavior for CLI callers.

    Returns:
      {'status': 'updated'}                      — tags were written
      {'status': 'already_ok'}                   — tags already matched
      {'status': 'skipped', 'reason': str}       — filename not parseable / unsupported format
      {'status': 'error',   'reason': str}       — mutagen write failure
    """
    ext = os.path.splitext(path)[1].lower()
    if not supports_tag_writing(path):
        return {'status': 'skipped', 'reason': f'Unsupported format: {ext}'}

    media = read_media(path)
    if not media.usable:
        return {
            'status': 'skipped',
            'reason': f'{media.status}: {media.error or "cannot read media"}',
        }
    if ext == '.aac' and media.container not in {'MP4', 'MP4Cover'}:
        return {
            'status': 'skipped',
            'reason': 'Raw AAC is not a writable MP4 container',
        }

    if expected_tags is None:
        stem = os.path.splitext(os.path.basename(path))[0]
        parsed = parse_stem(stem)
        if parsed is None:
            return {'status': 'skipped', 'reason': 'Filename not in expected format'}
        expected_tags = _parsed_tag_values(parsed)

    expected = _expected_tag_values(expected_tags)
    if not expected:
        return {'status': 'skipped', 'reason': 'No supported tag values to write'}
    if _tags_match(expected, media.tags):
        return {'status': 'already_ok'}

    try:
        if ext == '.mp3':
            _write_mp3(path, expected)
        elif ext in ('.flac', '.ogg'):
            _write_vorbis(path, expected)
        elif ext in ('.m4a', '.aac'):
            _write_mp4(path, expected)
        elif ext == '.wma':
            _write_asf(path, expected)
        else:
            return {'status': 'skipped', 'reason': f'No writer for {ext}'}
        return {'status': 'updated'}
    except Exception as exc:
        return {'status': 'error', 'reason': str(exc)}
