"""OC ReMix duplicate analysis that never removes files."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .fingerprint import fingerprint_file
from .media import canonical_to_id3, read_media
from .runtime import resolve_fpcalc


_AUDIO_EXTS = {'.mp3', '.wav', '.flac', '.ogg', '.m4a', '.wma', '.aac'}

_WS_RE = re.compile(r'\s+')
_DASH_RE = re.compile(r'[\u2013\u2014-]+')
_OC_SUFFIX_RE = re.compile(r'\s*[\(\[]\s*OC\s*Re[Mm]ix\s*[\)\]]\s*$', re.IGNORECASE)
_OC_ANY_RE = re.compile(r'OC\s*Re[Mm]ix', re.IGNORECASE)
_OC_STEM_SUFFIX_RE = re.compile(r'_OC_ReMix$', re.IGNORECASE)
_TRAILING_PAREN_RE = re.compile(r'\s*\(([^()]*)\)\s*$')
_TITLE_SOFT_WORDS = {
    'a', 'an', 'and', 'in', 'of', 'the', 'to', 'for', 'with',
    'oc', 'remix', 'mix', 'version', 'ver', 'edit',
    'piano', 'instrumental',
}
_GENERIC_REMIXER_WORDS = {
    'oc', 'remix', 'mix', 'version', 'ver', 'edit',
    'piano', 'instrumental',
}


@dataclass
class OCRemixTrack:
    path: str
    name: str
    tags: dict[str, str]
    game: str
    title: str
    remixers: list[str]
    game_norm: str
    title_norm: str
    title_core_norm: str
    title_tokens: tuple[str, ...]
    title_core_tokens: tuple[str, ...]
    remixers_norm: tuple[str, ...]
    duration: float | None
    bitrate: int | None
    size_bytes: int
    fingerprint: str = ''
    fingerprint_error: str = ''
    sha1: str = ''
def _normalize_text(value: str) -> str:
    clean = value or ''
    clean = clean.replace('_', ' ')
    clean = clean.replace('’', "'")
    clean = _DASH_RE.sub(' ', clean)
    clean = _OC_SUFFIX_RE.sub(' ', clean)
    clean = clean.replace("'", '')
    clean = re.sub(r'[^0-9a-zA-Z]+', ' ', clean)
    clean = _WS_RE.sub(' ', clean).strip().lower()
    # Collapse split initialisms ("A D" -> "ad") so "A.D." and "AD" align.
    while True:
        collapsed = re.sub(r'\b([a-z])\s+([a-z])\b', r'\1\2', clean)
        if collapsed == clean:
            break
        clean = collapsed
    clean = _WS_RE.sub(' ', clean).strip()
    return clean


def _normalize_core_title(title: str) -> str:
    match = _TRAILING_PAREN_RE.search(title)
    if not match:
        return _normalize_text(title)
    stripped = title[: match.start()].strip()
    return _normalize_text(stripped) or _normalize_text(title)


def _title_tokens(title: str) -> tuple[str, ...]:
    tokens = [tok for tok in _normalize_text(title).split() if tok]
    return tuple(tokens)


def _title_core_tokens(title: str) -> tuple[str, ...]:
    return tuple(tok for tok in _title_tokens(title) if tok not in _TITLE_SOFT_WORDS)


def _is_generic_remixer_label(name: str) -> bool:
    normalized = _normalize_text(name)
    if not normalized:
        return True
    tokens = normalized.split()
    return bool(tokens) and set(tokens).issubset(_GENERIC_REMIXER_WORDS)


def _split_people(raw: str) -> list[str]:
    if not raw:
        return []
    chunk = raw
    chunk = re.sub(r'\s+(?:and|&|/)\s+', ', ', chunk, flags=re.IGNORECASE)
    chunk = re.sub(r'\s+(?:feat(?:uring)?\.?|ft\.?)\s+', ', ', chunk, flags=re.IGNORECASE)
    names = [name.strip() for name in re.split(r',\s*', chunk) if name.strip()]
    names = [name for name in names if not _is_generic_remixer_label(name)]
    return names


def _strip_oc_suffix(text: str) -> str:
    return _OC_SUFFIX_RE.sub('', text or '').strip()


def _is_ocremix_file(name: str, tags: dict[str, str]) -> bool:
    stem = os.path.splitext(name)[0]
    if _OC_STEM_SUFFIX_RE.search(stem):
        return True
    if _OC_ANY_RE.search(stem):
        return True

    album = tags.get('TALB', '')
    album_artist = tags.get('TPE2', '')
    title = tags.get('TIT2', '')
    if _OC_ANY_RE.search(album):
        return True
    if album_artist.strip().lower() == 'overclocked remix':
        return True
    if _OC_ANY_RE.search(title):
        return True
    return False


def _parse_from_tags(tags: dict[str, str]) -> tuple[str, str, list[str]]:
    # Tag-writer style: TPE1=game, TIT2=title, TIT3=remixers
    if tags.get('TPE1') and tags.get('TIT2'):
        game = tags.get('TPE1', '').strip()
        title = _strip_oc_suffix(tags.get('TIT2', '').strip())
        remixers = _split_people(tags.get('TIT3', '').strip())
        return game, title, remixers

    # OC ReMix Collection style: TIT1=game, TIT3=title, TPE1=remixers
    # Only use this mapping when TIT2 is absent; in our current writer TIT3 is
    # remixer metadata, not title.
    if tags.get('TIT1') and tags.get('TIT3') and not tags.get('TIT2'):
        game = tags.get('TIT1', '').strip()
        title = _strip_oc_suffix(tags.get('TIT3', '').strip())
        remixers = _split_people(tags.get('TPE1', '').strip())
        return game, title, remixers

    return '', '', []


def _looks_like_remixer_block(text: str) -> bool:
    probe = text.strip()
    if not probe:
        return False
    if any(token in probe for token in (',', '&', '/')):
        return True
    if re.search(r'\b(?:and|feat(?:uring)?\.?|ft\.?)\b', probe, re.IGNORECASE):
        return True
    return False


def _parse_from_filename(name: str) -> tuple[str, str, list[str]]:
    stem = os.path.splitext(name)[0].replace('_', ' ')
    stem = _OC_STEM_SUFFIX_RE.sub('', stem).strip()
    stem = _strip_oc_suffix(stem)

    if ' - ' in stem:
        game, rest = stem.split(' - ', 1)
    elif '-' in stem:
        game, rest = stem.split('-', 1)
    else:
        return '', stem.strip(), []

    game = game.strip()
    title = rest.strip()
    remixers: list[str] = []

    match = _TRAILING_PAREN_RE.search(title)
    if match:
        maybe_remixers = match.group(1).strip()
        has_multiple_paren_groups = title[: match.start()].count('(') > 0
        if has_multiple_paren_groups or _looks_like_remixer_block(maybe_remixers):
            remixers = _split_people(maybe_remixers)
            title = title[: match.start()].strip()

    return game, title, remixers
def _collect_ocremix_tracks(folder_path: str, recursive: bool) -> tuple[list[OCRemixTrack], int]:
    glob = '**/*' if recursive else '*'
    files = sorted(
        str(p)
        for p in Path(folder_path).glob(glob)
        if p.is_file() and p.suffix.lower() in _AUDIO_EXTS
    )

    tracks: list[OCRemixTrack] = []
    for path in files:
        name = os.path.basename(path)
        media = read_media(path)
        tags = canonical_to_id3(media.tags)
        if not _is_ocremix_file(name, tags):
            continue

        game, title, remixers = _parse_from_tags(tags)
        if not game or not title:
            file_game, file_title, file_remixers = _parse_from_filename(name)
            game = game or file_game
            title = title or file_title
            remixers = remixers or file_remixers

        size_bytes = os.path.getsize(path)

        game_norm = _normalize_text(game)
        title_norm = _normalize_text(title)
        title_core_norm = _normalize_core_title(title)
        title_tokens = _title_tokens(title)
        title_core_tokens = _title_core_tokens(title)
        remixers_norm = tuple(sorted(_normalize_text(r) for r in remixers if _normalize_text(r)))

        if not game_norm or not title_norm:
            # Keep unparseable files out of duplicate scoring to reduce unsafe matches.
            continue

        tracks.append(
            OCRemixTrack(
                path=path,
                name=name,
                tags=tags,
                game=game,
                title=title,
                remixers=remixers,
                game_norm=game_norm,
                title_norm=title_norm,
                title_core_norm=title_core_norm,
                title_tokens=title_tokens,
                title_core_tokens=title_core_tokens,
                remixers_norm=remixers_norm,
                duration=media.duration,
                bitrate=media.bitrate,
                size_bytes=size_bytes,
            )
        )

    return tracks, len(files)


def _make_parent(size: int) -> list[int]:
    return list(range(size))


def _find(parent: list[int], idx: int) -> int:
    while parent[idx] != idx:
        parent[idx] = parent[parent[idx]]
        idx = parent[idx]
    return idx


def _union(parent: list[int], a: int, b: int) -> None:
    ra = _find(parent, a)
    rb = _find(parent, b)
    if ra != rb:
        parent[rb] = ra


def _is_variant_title_match(left: OCRemixTrack, right: OCRemixTrack) -> bool:
    if left.game_norm != right.game_norm:
        return False
    if left.title_norm == right.title_norm:
        return False

    # Variant matching is only for rich-vs-poor metadata copies.
    if bool(left.remixers_norm) == bool(right.remixers_norm):
        return False

    if left.duration is not None and right.duration is not None:
        if abs(left.duration - right.duration) > 4.0:
            return False

    core_left = set(left.title_core_tokens)
    core_right = set(right.title_core_tokens)
    if core_left and core_right:
        if core_left == core_right:
            return True
        small, large = (core_left, core_right) if len(core_left) <= len(core_right) else (core_right, core_left)
        if small and small.issubset(large) and (large - small).issubset(_TITLE_SOFT_WORDS):
            return True

    full_left = set(left.title_tokens)
    full_right = set(right.title_tokens)
    if full_left and full_right:
        small, large = (full_left, full_right) if len(full_left) <= len(full_right) else (full_right, full_left)
        if small and small.issubset(large) and (large - small).issubset(_TITLE_SOFT_WORDS):
            return True

    return False


def _build_candidate_groups(tracks: list[OCRemixTrack]) -> list[list[OCRemixTrack]]:
    parent = _make_parent(len(tracks))

    primary: dict[tuple[str, str], list[int]] = {}
    bridge: dict[tuple[str, str], list[int]] = {}
    for idx, track in enumerate(tracks):
        primary.setdefault((track.game_norm, track.title_norm), []).append(idx)
        if track.title_core_norm and track.title_core_norm != track.title_norm:
            bridge.setdefault((track.game_norm, track.title_core_norm), []).append(idx)

    for indices in primary.values():
        if len(indices) < 2:
            continue
        head = indices[0]
        for idx in indices[1:]:
            _union(parent, head, idx)

    # Bridge groups are only used when remixer metadata is present in one file but
    # absent in another; this avoids broad false merges between similarly named songs.
    for indices in bridge.values():
        if len(indices) < 2:
            continue
        with_remixers = [idx for idx in indices if tracks[idx].remixers_norm]
        without_remixers = [idx for idx in indices if not tracks[idx].remixers_norm]
        if not with_remixers or not without_remixers:
            continue
        for left in with_remixers:
            for right in without_remixers:
                _union(parent, left, right)

    # Fuzzy title variant bridge (e.g. "600 A.D. in Piano" vs "600 AD"), but
    # still restricted to rich-vs-poor metadata pairs and close duration.
    by_game: dict[str, list[int]] = {}
    for idx, track in enumerate(tracks):
        by_game.setdefault(track.game_norm, []).append(idx)
    for indices in by_game.values():
        if len(indices) < 2:
            continue
        with_remixers = [idx for idx in indices if tracks[idx].remixers_norm]
        without_remixers = [idx for idx in indices if not tracks[idx].remixers_norm]
        if not with_remixers or not without_remixers:
            continue
        for left in with_remixers:
            for right in without_remixers:
                if _is_variant_title_match(tracks[left], tracks[right]):
                    _union(parent, left, right)

    components: dict[int, list[OCRemixTrack]] = {}
    for idx, track in enumerate(tracks):
        root = _find(parent, idx)
        components.setdefault(root, []).append(track)

    groups = [group for group in components.values() if len(group) > 1]
    groups.sort(key=lambda g: (g[0].game_norm, g[0].title_norm, len(g)), reverse=False)
    return groups


def _sha1(path: str) -> str:
    h = hashlib.sha1()
    with open(path, 'rb') as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()
def _prepare_evidence(tracks: list[OCRemixTrack]) -> bool:
    fpcalc_available = resolve_fpcalc() is not None
    for track in tracks:
        if not track.sha1:
            track.sha1 = _sha1(track.path)
        if not fpcalc_available:
            track.fingerprint_error = 'fpcalc unavailable'
            continue
        if not track.fingerprint and not track.fingerprint_error:
            fingerprint, error = fingerprint_file(track.path)
            track.fingerprint = fingerprint or ''
            track.fingerprint_error = error or ''
    return fpcalc_available


def _pair_is_auto_safe(left: OCRemixTrack, right: OCRemixTrack) -> bool:
    if left.sha1 and right.sha1 and left.sha1 == right.sha1:
        return True
    return False


def _duration_spread(group: list[OCRemixTrack]) -> float | None:
    known = [track.duration for track in group if track.duration is not None]
    if len(known) < 2:
        return None
    return max(known) - min(known)


def _pick_keeper(group: list[OCRemixTrack]) -> OCRemixTrack:
    def score(track: OCRemixTrack) -> tuple:
        has_remixers = int(bool(track.remixers_norm))
        has_tagged_remixers = int(bool(track.tags.get('TIT3', '').strip()))
        bitrate = track.bitrate or 0
        return (has_remixers, has_tagged_remixers, bitrate, track.size_bytes, len(track.name), track.name)

    return max(group, key=score)


def _component_members(indices: list[int], parent: list[int]) -> list[list[int]]:
    groups: dict[int, list[int]] = {}
    for idx in indices:
        groups.setdefault(_find(parent, idx), []).append(idx)
    return [chunk for chunk in groups.values() if len(chunk) > 1]


def _classify_candidate_group(group: list[OCRemixTrack]) -> tuple[list[dict], dict | None]:
    index = {id(track): idx for idx, track in enumerate(group)}
    parent = _make_parent(len(group))

    for i in range(len(group)):
        for j in range(i + 1, len(group)):
            if _pair_is_auto_safe(group[i], group[j]):
                _union(parent, i, j)

    auto_safe: list[dict] = []
    used_ids: set[int] = set()
    for member_indices in _component_members(list(range(len(group))), parent):
        members = [group[idx] for idx in member_indices]
        keeper = _pick_keeper(members)
        losers = [track for track in members if track is not keeper]
        for loser in losers:
            used_ids.add(id(loser))
        used_ids.add(id(keeper))

        has_same_sha = len({track.sha1 for track in members if track.sha1}) == 1
        has_same_fp = len({track.fingerprint for track in members if track.fingerprint}) == 1
        evidence_parts = []
        if has_same_sha:
            evidence_parts.append('exact SHA1')
        if has_same_fp:
            evidence_parts.append('exact Chromaprint')
        evidence = ' + '.join(evidence_parts) if evidence_parts else 'strong evidence'

        auto_safe.append({'keep': keeper, 'delete': losers, 'reason': evidence})

    remaining = [track for track in group if id(track) not in used_ids]
    if len(remaining) < 2:
        return auto_safe, None

    spread = _duration_spread(remaining)
    remixer_sets = {track.remixers_norm for track in remaining if track.remixers_norm}
    missing_remixers = any(not track.remixers_norm for track in remaining)

    if spread is not None and spread <= 2.5 and (missing_remixers or len(remixer_sets) <= 1):
        reason = f'close duration ({spread:.2f}s), but fingerprints/hashes differ'
        return auto_safe, {'kind': 'review', 'tracks': remaining, 'reason': reason}

    if spread is not None:
        reason = f'duration spread {spread:.2f}s and no exact fingerprint/hash match'
    else:
        reason = 'insufficient duration evidence and no exact fingerprint/hash match'
    return auto_safe, {'kind': 'unsafe', 'tracks': remaining, 'reason': reason}


def _format_track(track: OCRemixTrack) -> str:
    sec = f'{track.duration:.2f}s' if track.duration is not None else '?s'
    kbps = f'{(track.bitrate // 1000)}kbps' if track.bitrate else '?kbps'
    rem = 'yes' if track.remixers_norm else 'no'
    return f'{track.name}  ({sec}, {kbps}, remixers={rem})'


def _display_report(auto_safe_groups: list[dict], review_groups: list[dict], unsafe_groups: list[dict], folder_path: str, dry_run: bool) -> None:
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console(highlight=False)
        mode = '[yellow]DRY RUN[/yellow]' if dry_run else '[red]DELETING AUTO-SAFE[/red]'
        console.print(f'\n{mode} OC ReMix dedup — {os.path.basename(folder_path)}')
        console.print(
            f'Auto-safe groups: [green]{len(auto_safe_groups)}[/green]  |  '
            f'Review groups: [cyan]{len(review_groups)}[/cyan]  |  '
            f'Unsafe groups: [yellow]{len(unsafe_groups)}[/yellow]'
        )

        if auto_safe_groups:
            table = Table(show_header=True, header_style='bold green', show_lines=True)
            table.add_column('Keep', style='green', max_width=68)
            table.add_column('Delete', style='red', max_width=68)
            table.add_column('Evidence', style='cyan', max_width=24)
            for row in auto_safe_groups[:100]:
                table.add_row(
                    _format_track(row['keep']),
                    '\n'.join(_format_track(track) for track in row['delete']),
                    row['reason'],
                )
            console.print('\n[bold green]Auto-safe deletions[/bold green]')
            console.print(table)
            remaining = len(auto_safe_groups) - min(len(auto_safe_groups), 100)
            if remaining > 0:
                console.print(f'[dim]... and {remaining} more auto-safe groups[/dim]')

        if review_groups:
            table = Table(show_header=True, header_style='bold cyan', show_lines=True)
            table.add_column('Review Candidates', style='cyan', max_width=110)
            table.add_column('Reason', style='white', max_width=48)
            for row in review_groups[:120]:
                table.add_row('\n'.join(_format_track(track) for track in row['tracks']), row['reason'])
            console.print('\n[bold cyan]Review-only groups (never auto-deleted)[/bold cyan]')
            console.print(table)
            remaining = len(review_groups) - min(len(review_groups), 120)
            if remaining > 0:
                console.print(f'[dim]... and {remaining} more review groups[/dim]')

        if unsafe_groups:
            table = Table(show_header=True, header_style='bold yellow', show_lines=True)
            table.add_column('Unsafe Candidates', style='yellow', max_width=110)
            table.add_column('Reason', style='white', max_width=48)
            for row in unsafe_groups[:120]:
                table.add_row('\n'.join(_format_track(track) for track in row['tracks']), row['reason'])
            console.print('\n[bold yellow]Unsafe groups (kept as-is)[/bold yellow]')
            console.print(table)
            remaining = len(unsafe_groups) - min(len(unsafe_groups), 120)
            if remaining > 0:
                console.print(f'[dim]... and {remaining} more unsafe groups[/dim]')

        console.print(
            '\n[dim]Audit only: duplicate removal is deferred until the '
            'reversible Windows Recycle Bin path is available.[/dim]'
        )

    except ImportError:
        mode = 'DRY RUN' if dry_run else 'DELETING AUTO-SAFE'
        print(f'\n{mode} OC ReMix dedup — {folder_path}')
        print(f'  auto-safe: {len(auto_safe_groups)}')
        print(f'  review:    {len(review_groups)}')
        print(f'  unsafe:    {len(unsafe_groups)}')


def dedup_ocremix_folder(folder_path: str, dry_run: bool = True, recursive: bool = False) -> dict:
    tracks, scanned = _collect_ocremix_tracks(folder_path, recursive=recursive)
    if not tracks:
        _display_report([], [], [], folder_path, dry_run)
        return {
            'groups': 0,
            'auto_safe_groups': 0,
            'review_groups': 0,
            'unsafe_groups': 0,
            'to_delete': 0,
            'deleted': 0,
            'errors': 0,
            'scanned_files': scanned,
            'ocremix_files': 0,
            'fingerprint_available': resolve_fpcalc() is not None,
        }

    candidate_groups = _build_candidate_groups(tracks)
    candidate_tracks = []
    for group in candidate_groups:
        candidate_tracks.extend(group)
    _prepare_evidence(candidate_tracks)

    auto_safe_groups: list[dict] = []
    review_groups: list[dict] = []
    unsafe_groups: list[dict] = []

    for group in candidate_groups:
        group_auto_safe, remainder = _classify_candidate_group(group)
        auto_safe_groups.extend(group_auto_safe)
        if not remainder:
            continue
        if remainder['kind'] == 'review':
            review_groups.append(remainder)
        else:
            unsafe_groups.append(remainder)

    _display_report(auto_safe_groups, review_groups, unsafe_groups, folder_path, dry_run)

    deleted = 0
    errors = 0
    if not dry_run and auto_safe_groups:
        print(
            '  Duplicate removal is disabled until the reversible Windows '
            'Recycle Bin path is available.'
        )
        errors = 1

    to_delete = sum(len(group['delete']) for group in auto_safe_groups)
    return {
        'groups': len(candidate_groups),
        'auto_safe_groups': len(auto_safe_groups),
        'review_groups': len(review_groups),
        'unsafe_groups': len(unsafe_groups),
        'to_delete': to_delete,
        'deleted': deleted,
        'errors': errors,
        'scanned_files': scanned,
        'ocremix_files': len(tracks),
        'fingerprint_available': resolve_fpcalc() is not None,
    }
