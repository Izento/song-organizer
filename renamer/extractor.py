import os
import re
from dataclasses import dataclass, field

from .formatter import split_feat, strip_ocremix_suffix
from .media import read_media
from .regular_parser import (
    has_instrumental_qualifier,
    normalize_text,
    normalize_title_text,
    parse_regular_filename,
    parse_regular_stem,
    split_title_version_qualifiers,
)

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.m4a', '.aac', '.wma', '.wav'}

# Detects "_OC_ReMix" suffix on the bare filename stem
OCREMIX_STEM_RE = re.compile(r'_OC_ReMix$', re.IGNORECASE)

# Tag values that are placeholders and should not be trusted
_JUNK_TAG_RE = re.compile(
    r'^(va|v\.a\.|v/a|various\s*artists?|various|'
    r'unknown\s*(artist|title)?|unknown|artist|title|'
    r'track\s*\d+|untitled|\d+|audio(\s*track)?|'
    r'mpeg\s*audio|no\s*artist|no\s*title)$',
    re.IGNORECASE,
)

# Detects track-number-only filenames like "01 Track 1", "02", "03 Track 3"
TRACK_NUM_ONLY_RE = re.compile(r'^\d{1,3}(\s+(track\s+\d+)?)?$', re.IGNORECASE)

# Strips date/time junk from ripped folder names: "Deep River (7_26_2009 9_47_51 PM)"
FOLDER_DATE_RE = re.compile(r'\s*\([0-9_/\s:APMapm]+\)\s*$')


@dataclass
class TrackInfo:
    path: str
    ext: str
    # Regular music fields
    artist: str = ''
    title: str = ''
    feat_artists: list = field(default_factory=list)
    # OC ReMix fields
    is_ocremix: bool = False
    game: str = ''
    remixers: list = field(default_factory=list)
    # Metadata
    strategy: str = ''
    needs_lookup: bool = False
    skip_reason: str = ''
    # MusicBrainz lookup context
    mb_album: str = ''
    mb_track_num: int = 0
    duration: float | None = None
    bitrate: int | None = None
    acoustid_score: float | None = None
    acoustid_recording_id: str = ''
    version_warning: str = ''


def scan_folder(folder_path: str, recursive: bool = True) -> list[str]:
    """Return sorted list of audio file paths under folder_path."""
    paths = []
    if recursive:
        for root, _dirs, files in os.walk(folder_path):
            for f in files:
                if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS:
                    paths.append(os.path.join(root, f))
    else:
        for f in os.listdir(folder_path):
            full = os.path.join(folder_path, f)
            if os.path.isfile(full) and os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS:
                paths.append(full)
    return sorted(paths)


# ---------------------------------------------------------------------------
# Tag reading
# ---------------------------------------------------------------------------

def _read_tags(path: str) -> dict:
    """Read tags through the shared format-aware media reader."""
    media = read_media(path)
    tags = media.tags
    return {
        key: value
        for key, value in {
            'TPE1': tags.get('artist', ''),
            'TIT2': tags.get('title', ''),
            'TALB': tags.get('album', ''),
            'TPE2': tags.get('album_artist', ''),
            'TIT1': tags.get('grouping', ''),
            'TIT3': tags.get('subtitle', ''),
        }.items()
        if value
    }


# ---------------------------------------------------------------------------
# OC ReMix detection
# ---------------------------------------------------------------------------

_OCREMIX_PAREN_RE = re.compile(r'\(\s*OC\s*Re[Mm]ix\s*\)', re.IGNORECASE)


def _detect_ocremix(tags: dict, filename: str) -> bool:
    stem = os.path.splitext(filename)[0]
    if OCREMIX_STEM_RE.search(stem):              # _OC_ReMix suffix (collection format)
        return True
    if _OCREMIX_PAREN_RE.search(stem):            # (OC ReMix) in filename (Gamer's Delight)
        return True
    if 'ocremix' in tags.get('TALB', '').lower():
        return True
    if tags.get('TPE2', '') == 'OverClocked ReMix':
        return True
    if 'OC ReMix' in tags.get('TIT2', ''):
        return True
    return False


# ---------------------------------------------------------------------------
# Extraction strategies
# ---------------------------------------------------------------------------

def _split_ocremix_artists(raw: str) -> list[str]:
    """
    Split OC ReMix TPE1 into individual remixer names.
    Handles both comma separation and 'feat.' notation within the artist field:
      'ArtistA feat. ArtistB, ArtistC' → ['ArtistA', 'ArtistB', 'ArtistC']
    """
    feat_parts = re.split(r'\s+(?:feat(?:uring)?\.?|ft\.?)\s+', raw, flags=re.IGNORECASE)
    result = []
    for part in feat_parts:
        result.extend(
            normalize_title_text(artist)
            for artist in re.split(r',\s*', part)
            if artist.strip()
        )
    return result


def _from_ocremix_new_tags(path: str, ext: str, tags: dict) -> TrackInfo:
    """Read the legacy OC ReMix Collection tag layout."""
    game = normalize_title_text(tags.get('TIT1', '').strip())
    title = normalize_title_text(
        strip_ocremix_suffix(tags.get('TIT3', '').strip())
    )
    artists_raw = tags.get('TPE1', '').strip()
    remixers = _split_ocremix_artists(artists_raw)
    return TrackInfo(
        path=path,
        ext=ext,
        is_ocremix=True,
        game=game,
        title=title,
        remixers=remixers,
        strategy='ocremix_tagged',
    )


def _from_ocremix_writer_tags(path: str, ext: str, tags: dict) -> TrackInfo:
    """Read the schema written by tag_writer.py without reinterpreting TIT3."""
    game = normalize_title_text(
        (tags.get('TPE1') or tags.get('TALB') or tags.get('TIT1', '')).strip()
    )
    title = normalize_title_text(
        strip_ocremix_suffix(tags.get('TIT2', '').strip())
    )
    remixers = _split_ocremix_artists(tags.get('TIT3', '').strip())
    return TrackInfo(
        path=path,
        ext=ext,
        is_ocremix=True,
        game=game,
        title=title,
        remixers=remixers,
        strategy='ocremix_tagged',
    )


def _from_ocremix_old_tags(path: str, ext: str, tags: dict) -> TrackInfo:
    """Gamer's Delight: TPE1=game name, TIT2=title (OC ReMix). No remixer in metadata."""
    game = normalize_title_text(tags.get('TPE1', '').strip())
    title = normalize_title_text(strip_ocremix_suffix(tags.get('TIT2', '').strip()))
    return TrackInfo(path=path, ext=ext, is_ocremix=True, game=game,
                     title=title, remixers=[], needs_lookup=True,
                     strategy='ocremix_old_tags')


def _from_tags(path: str, ext: str, tags: dict) -> TrackInfo:
    """Regular music with good ID3 tags."""
    artist_raw = tags.get('TPE1', '').strip()
    title_raw = tags.get('TIT2', '').strip()

    # No artist in tags — the TIT2 may be "Artist - Title" (some taggers store it that way)
    if not artist_raw:
        return _from_filename(path, ext, is_ocremix=False)

    artist, feat_from_artist = split_feat(artist_raw)
    title, feat_from_title = split_feat(title_raw)

    # Deduplicate feat. artists across both fields
    seen = set(feat_from_artist)
    combined_feat = feat_from_artist + [f for f in feat_from_title if f not in seen]

    return TrackInfo(path=path, ext=ext, artist=artist, title=title,
                     feat_artists=combined_feat, strategy='tag_based')


def _from_filename(path: str, ext: str, is_ocremix: bool) -> TrackInfo:
    """Parse artist/title from the filename when no usable tags exist."""
    stem = os.path.splitext(os.path.basename(path))[0]

    if is_ocremix:
        return _from_ocremix_filename(path, ext, stem)

    parsed = parse_regular_stem(stem)
    if parsed is None:
        return TrackInfo(path=path, ext=ext,
                         skip_reason='No artist–title separator found in filename')

    artist = _smart_capitalize(parsed.artist)
    return TrackInfo(
        path=path,
        ext=ext,
        artist=artist,
        title=parsed.title,
        feat_artists=list(parsed.features),
        strategy='filename_norm',
    )


def _from_ocremix_filename(path: str, ext: str, stem: str) -> TrackInfo:
    """OC ReMix file with no usable tags — parse game/title from filename."""
    clean = OCREMIX_STEM_RE.sub('', stem).replace('_', ' ')
    clean = strip_ocremix_suffix(clean).strip()

    if ' - ' not in clean:
        return TrackInfo(path=path, ext=ext,
                         skip_reason='Cannot split game/title in OC ReMix filename')

    game, title = clean.split(' - ', 1)
    return TrackInfo(path=path, ext=ext, is_ocremix=True, game=game.strip(),
                     title=normalize_title_text(title), remixers=[], needs_lookup=True,
                     strategy='ocremix_filename')


def _from_musicbrainz_lookup(path: str, ext: str) -> TrackInfo:
    """Files like '01 Track 1.mp3' — we only know the track number and the album (from the parent folder)."""
    stem = os.path.splitext(os.path.basename(path))[0]
    parent_dir = os.path.dirname(path)
    parent = os.path.basename(parent_dir)
    grandparent = os.path.basename(os.path.dirname(parent_dir))
    album = FOLDER_DATE_RE.sub('', parent).strip()

    # Use grandparent folder as artist hint (structure: Artist/Album/tracks)
    # Exclude obviously non-artist folder names
    _skip = {'music', 'mp3', 'flac', 'downloads', 'albums', ''}
    artist_hint = grandparent if grandparent.lower() not in _skip else ''

    track_match = re.match(r'^(\d+)', stem)
    if not track_match:
        return TrackInfo(path=path, ext=ext,
                         skip_reason='Cannot extract track number for MusicBrainz lookup')

    return TrackInfo(path=path, ext=ext, needs_lookup=True, strategy='musicbrainz',
                     mb_album=album, mb_track_num=int(track_match.group(1)),
                     artist=artist_hint)


# ---------------------------------------------------------------------------
# Junk tag detection
# ---------------------------------------------------------------------------

def _is_junk_tag(value: str) -> bool:
    """
    Returns True when a tag value is a known placeholder that shouldn't be trusted.
    Catches: 'VA', 'Unknown Artist', 'Track 01', all-underscore filename copies, etc.
    """
    if not value or not value.strip():
        return True
    v = value.strip()
    if _JUNK_TAG_RE.match(v):
        return True
    # Copied from a filename: underscores but no spaces, length > 3
    if '_' in v and ' ' not in v and len(v) > 3:
        return True
    return False


# ---------------------------------------------------------------------------
# AcoustID fingerprint strategy
# ---------------------------------------------------------------------------

def _from_acoustid(path: str, ext: str, api_key: str) -> 'TrackInfo | None':
    """Fingerprint the audio and return a TrackInfo if a confident match is found."""
    from .acoustid import lookup
    result = lookup(path, api_key)
    if not result:
        return None
    track = TrackInfo(
        path=path,
        ext=ext,
        artist=result['artist'],
        title=result['title'],
        feat_artists=result['feat_artists'],
        strategy='acoustid',
        acoustid_score=result.get('score'),
        acoustid_recording_id=result.get('recording_id', ''),
    )
    return _preserve_filename_version_qualifiers(path, track)


def _qualifiers_agree(
    filename_qualifiers: tuple[str, ...],
    acoustid_qualifiers: tuple[str, ...],
) -> bool:
    if not filename_qualifiers or not acoustid_qualifiers:
        return True
    filename_values = {normalize_text(value) for value in filename_qualifiers}
    acoustid_values = {normalize_text(value) for value in acoustid_qualifiers}
    return all(
        any(
            filename_value in acoustid_value
            or acoustid_value in filename_value
            for acoustid_value in acoustid_values
        )
        for filename_value in filename_values
    )


def _preserve_filename_version_qualifiers(path: str, track: TrackInfo) -> TrackInfo:
    """Retain local version labels that AcoustID metadata omits."""
    filename = parse_regular_filename(os.path.basename(path))
    if filename is None:
        return track
    if has_instrumental_qualifier(filename.title):
        return _preserve_local_instrumental(track)

    filename_base, filename_qualifiers = split_title_version_qualifiers(
        filename.title
    )
    acoustid_base, acoustid_qualifiers = split_title_version_qualifiers(track.title)
    if (
        not filename_qualifiers
        or normalize_text(filename.artist) != normalize_text(track.artist)
        or normalize_text(filename_base) != normalize_text(acoustid_base)
    ):
        return track
    if not _qualifiers_agree(filename_qualifiers, acoustid_qualifiers):
        track.version_warning = (
            "Version qualifier conflicts with AcoustID metadata; "
            "review the proposed filename."
        )
        return track

    known = {normalize_text(value) for value in acoustid_qualifiers}
    missing = [
        value
        for value in filename_qualifiers
        if normalize_text(value) not in known
    ]
    if missing:
        track.title = " ".join(
            [acoustid_base, *(f"({value})" for value in missing)]
        )
    return track


def _preserve_local_instrumental(track: TrackInfo) -> TrackInfo:
    """Treat an explicit filename Instrumental label as authoritative."""
    if has_instrumental_qualifier(track.title):
        return track

    _base_title, acoustid_qualifiers = split_title_version_qualifiers(track.title)
    if acoustid_qualifiers:
        track.version_warning = (
            "Version qualifier conflicts with AcoustID metadata; "
            "review the proposed filename."
        )
    track.title = f"{normalize_title_text(track.title)} (Instrumental)"
    return track


# ---------------------------------------------------------------------------
# Smart capitalization
# ---------------------------------------------------------------------------

def _smart_capitalize(s: str) -> str:
    """Capitalize only when the string is entirely lowercase."""
    return s.title() if s == s.lower() else s


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def extract_track(
    path: str,
    strategy: str = None,
    acoustid_key: str = None,
    prefer_acoustid: bool = False,
) -> TrackInfo:
    """
    Extract structured track info from an audio file.

    strategy overrides:
      'regular' — use the ordinary artist/title filename parser
      'filename_norm' — force filename parsing regardless of tags
      'musicbrainz'   — treat as track-number file, flag for lookup
      None / anything else — auto-detect from tags + optional fingerprinting

    acoustid_key: when provided, a successful AcoustID match takes precedence over
                  embedded tags. If no match is available, normal tag and filename
                  fallbacks remain in effect.
    prefer_acoustid: retained for compatibility; AcoustID is now preferred
                     automatically whenever a key is provided.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext not in AUDIO_EXTENSIONS:
        return TrackInfo(path=path, ext=ext,
                         skip_reason=f'Unsupported format ({ext})')

    if strategy == 'musicbrainz':
        return _from_musicbrainz_lookup(path, ext)

    if strategy == 'regular':
        return _from_filename(path, ext, is_ocremix=False)

    if strategy == 'filename_norm':
        tags = _read_tags(path)
        return _from_filename(
            path,
            ext,
            is_ocremix=_detect_ocremix(tags, os.path.basename(path)),
        )

    if acoustid_key and (prefer_acoustid or strategy not in {
        'regular',
        'filename_norm',
        'musicbrainz',
    }):
        result = _from_acoustid(path, ext, acoustid_key)
        if result:
            return result

    tags = _read_tags(path)
    filename = os.path.basename(path)
    is_ocremix = _detect_ocremix(tags, filename)

    # Auto-detect from tags
    has_writer_ocremix_tags = bool(
        tags.get('TIT2')
        and tags.get('TPE2', '').casefold() == 'overclocked remix'
    )
    has_new_ocremix_tags = bool(
        tags.get('TIT1') and tags.get('TIT3') and not has_writer_ocremix_tags
    )
    artist_tag = tags.get('TPE1', '')
    title_tag  = tags.get('TIT2', '')
    has_basic_tags = bool(artist_tag or title_tag)
    tags_are_good  = has_basic_tags and not (_is_junk_tag(artist_tag) or _is_junk_tag(title_tag))

    if is_ocremix and has_writer_ocremix_tags:
        return _from_ocremix_writer_tags(path, ext, tags)

    if is_ocremix and has_new_ocremix_tags:
        return _from_ocremix_new_tags(path, ext, tags)

    if is_ocremix and has_basic_tags:
        return _from_ocremix_old_tags(path, ext, tags)

    if tags_are_good:
        return _from_tags(path, ext, tags)

    return _from_filename(path, ext, is_ocremix)
