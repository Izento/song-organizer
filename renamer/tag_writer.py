# pylint: disable=broad-exception-caught,import-error

"""
tag_writer.py — Sync ID3/metadata tags from the current filename.

Parses the normalized filename that formatter.py produced and writes
the correct artist/title/album tags back into the audio file so
Winamp (and other players) display the right information.

Supported formats:
  Regular:   "Artist - Title (feat. X, Y).mp3"  -> TPE1=Artist, TIT2=full title
  OC ReMix:  "Game - Title (Remixer1, Remixer2) [OC ReMix].mp3"
                -> TPE1=game, TIT2=title, TALB=game, TIT3=remixers, TPE2=OverClocked ReMix
"""
import os
import re

from .media import canonical_to_id3, read_media
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


# ---------------------------------------------------------------------------
# Current-tag reading (for the "already OK?" check)
# ---------------------------------------------------------------------------

def _read_current_tags(path: str) -> dict:
    """Read the key display tags from a file. Returns {} on any failure."""
    media = read_media(path)
    return canonical_to_id3(media.tags)


def _tags_match(parsed: dict, current: dict) -> bool:
    """Return True if the file's existing tags already match what we'd write."""
    if parsed['is_ocremix']:
        expected_remixers = ', '.join(parsed['remixers']) if parsed['remixers'] else ''
        return (
            current.get('TPE1', '') == parsed['game']
            and current.get('TIT2', '') == parsed['title']
            and current.get('TALB', '') == parsed['game']
            and current.get('TPE2', '') == 'OverClocked ReMix'
            and current.get('TIT1', '') == parsed['game']
            and current.get('TIT3', '') == expected_remixers
        )
    return (
        current.get('TPE1', '') == parsed['artist']
        and current.get('TIT2', '') == parsed['full_title']
    )


# ---------------------------------------------------------------------------
# Tag writing — format-specific helpers
# ---------------------------------------------------------------------------

def _write_mp3(path: str, parsed: dict):
    from mutagen.id3 import (
        ID3, TIT2, TPE1, TALB, TPE2, TIT1, TIT3, ID3NoHeaderError,
    )
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        # File has only ID3v1 or no tags at all — create a fresh ID3v2 block
        tags = ID3()

    if parsed['is_ocremix']:
        # Artist = game name so Winamp groups all tracks from the same game together
        tags.setall('TPE1', [TPE1(encoding=3, text=[parsed['game']])])
        tags.setall('TIT2', [TIT2(encoding=3, text=[parsed['title']])])
        tags.setall('TALB', [TALB(encoding=3, text=[parsed['game']])])
        tags.setall('TPE2', [TPE2(encoding=3, text=['OverClocked ReMix'])])
        # TIT1 = content group (game), TIT3 = subtitle (remixer list)
        tags.setall('TIT1', [TIT1(encoding=3, text=[parsed['game']])])
        remixer_str = ', '.join(parsed['remixers']) if parsed['remixers'] else ''
        if remixer_str:
            tags.setall('TIT3', [TIT3(encoding=3, text=[remixer_str])])
        else:
            tags.delall('TIT3')
    else:
        tags.setall('TPE1', [TPE1(encoding=3, text=[parsed['artist']])])
        tags.setall('TIT2', [TIT2(encoding=3, text=[parsed['full_title']])])

    tags.save(path, v2_version=3)


def _write_vorbis(path: str, parsed: dict):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.flac':
        from mutagen.flac import FLAC
        f = FLAC(path)
    else:
        from mutagen.oggvorbis import OggVorbis
        f = OggVorbis(path)

    if parsed['is_ocremix']:
        f['artist'] = [parsed['game']]
        f['title'] = [parsed['title']]
        f['album'] = [parsed['game']]
        f['albumartist'] = ['OverClocked ReMix']
        f['grouping'] = [parsed['game']]
        f['subtitle'] = [', '.join(parsed['remixers'])]
    else:
        f['artist'] = [parsed['artist']]
        f['title'] = [parsed['full_title']]

    f.save()


def _write_mp4(path: str, parsed: dict):
    from mutagen.mp4 import MP4
    f = MP4(path)
    if f.tags is None:
        f.add_tags()

    if parsed['is_ocremix']:
        f.tags['\xa9ART'] = [parsed['game']]
        f.tags['\xa9nam'] = [parsed['title']]
        f.tags['\xa9alb'] = [parsed['game']]
        f.tags['aART'] = ['OverClocked ReMix']
        f.tags['\xa9grp'] = [parsed['game']]
        f.tags['----:com.apple.iTunes:SUBTITLE'] = [
            ', '.join(parsed['remixers']).encode('utf-8')
        ]
    else:
        f.tags['\xa9ART'] = [parsed['artist']]
        f.tags['\xa9nam'] = [parsed['full_title']]

    f.save()


def _write_asf(path: str, parsed: dict):
    from mutagen.asf import ASF, ASFUnicodeAttribute
    f = ASF(path)

    if parsed['is_ocremix']:
        f['Author'] = [ASFUnicodeAttribute(parsed['game'])]
        f['Title'] = [ASFUnicodeAttribute(parsed['title'])]
        f['WM/AlbumTitle'] = [ASFUnicodeAttribute(parsed['game'])]
        f['WM/AlbumArtist'] = [ASFUnicodeAttribute('OverClocked ReMix')]
        f['WM/ContentGroupDescription'] = [ASFUnicodeAttribute(parsed['game'])]
        f['WM/SubTitle'] = [
            ASFUnicodeAttribute(', '.join(parsed['remixers']))
        ]
    else:
        f['Author'] = [ASFUnicodeAttribute(parsed['artist'])]
        f['Title'] = [ASFUnicodeAttribute(parsed['full_title'])]

    f.save()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_tags_to_file(path: str) -> dict:
    """
    Parse the filename and write matching tags to the audio file.

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

    stem = os.path.splitext(os.path.basename(path))[0]
    parsed = parse_stem(stem)
    if parsed is None:
        return {'status': 'skipped', 'reason': 'Filename not in expected format'}

    current = _read_current_tags(path)
    if _tags_match(parsed, current):
        return {'status': 'already_ok'}

    try:
        if ext == '.mp3':
            _write_mp3(path, parsed)
        elif ext in ('.flac', '.ogg'):
            _write_vorbis(path, parsed)
        elif ext in ('.m4a', '.aac'):
            _write_mp4(path, parsed)
        elif ext == '.wma':
            _write_asf(path, parsed)
        else:
            return {'status': 'skipped', 'reason': f'No writer for {ext}'}
        return {'status': 'updated'}
    except Exception as exc:
        return {'status': 'error', 'reason': str(exc)}
