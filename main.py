#!/usr/bin/env python3
"""
Song Organizer — rename music files to a consistent format.

Regular music:   Artist - Song Name (feat. X, Y).mp3
OC ReMix music:  Game Name - Song Title (Remixer1, Remixer2) [OC ReMix].mp3

Usage:
  python main.py --gui                      # open the review-first desktop app
  python main.py                            # dry-run configured folders
  python main.py --apply                    # apply reviewed-safe CLI renames
  python main.py --folder "F:\\Music\\Hip-Hop" --apply
  python main.py --auto --folder "D:\\Music\\NewFolder"   # detect strategy
  python main.py --undo                     # undo last run
  python main.py --sync-tags                # preview tag updates (dry-run)
  python main.py --sync-tags --apply        # write tags to match filenames
  python main.py --dedup-regular --folder "F:\\Music\\Instrumentals" # read-only duplicate audit
  python main.py --dedup-ocremix --folder "F:\\Music\\Gamer's Delight" # legacy diagnostic audit
  python main.py --fingerprint --folder "F:\\Music\\Hip-Hop"         # use AcoustID for junk-tag files (dry-run)
  python main.py --fingerprint --folder "F:\\Music\\Hip-Hop" --apply # apply AcoustID renames
"""

import argparse
import os
import re
import sys
from pathlib import Path

import yaml
try:
    from dotenv import load_dotenv
except ImportError:  # Optional for users who do not use an API key.
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv(Path(__file__).with_name('.env'))

# Force UTF-8 output so filenames with non-ASCII characters don't crash the
# Windows cp1252 console when Rich tries to render the table.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from renamer.extractor import extract_track, scan_folder
from renamer.ocremix_dedup import dedup_ocremix_folder
from renamer.apply import apply_review_plan, latest_undoable_batch, undo_batch
from renamer.universal_dedup import dedup_folder as dedup_universal_folder
from renamer.review_api import analyze_folder, plan_renames, plan_tag_updates
from renamer.review_models import ReviewPlan
from renamer.runtime import (
    app_paths,
    ensure_app_dirs,
    resolve_acoustid_key,
)

CONFIG_PATH = str(app_paths()['config'])

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_config(path: str) -> dict:
    if not os.path.exists(path):
        return {'folders': []}
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            config = yaml.safe_load(fh) or {'folders': []}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f'Could not read configuration {path}: {exc}') from exc
    if not isinstance(config, dict) or not isinstance(
        config.get('folders', []), list
    ):
        raise ValueError('Configuration must contain a folders list')
    valid = []
    for entry in config.get('folders', []):
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
            continue
        valid.append(entry)
    return {'folders': valid}


# ---------------------------------------------------------------------------
# Auto-detect
# ---------------------------------------------------------------------------

TRACK_NUM_RE = re.compile(r'^\d{1,3}(\s|[-.]|$)')


def _auto_detect(folder_path: str):
    """Sample the folder, infer the best strategy, print a config suggestion."""
    files = scan_folder(folder_path, recursive=False)
    sample = files[:20]

    if not sample:
        print(f'No audio files found in: {folder_path}')
        return

    print(f'\nSampling {len(sample)} files from:\n  {folder_path}\n')

    counts = {
        'ocremix_tagged': 0,
        'ocremix_old': 0,
        'tag_based': 0,
        'filename_norm': 0,
        'musicbrainz': 0,
    }

    for path in sample:
        track = extract_track(path)
        strat = track.strategy or ''
        if strat == 'ocremix_tagged':
            counts['ocremix_tagged'] += 1
        elif strat in ('ocremix_old_tags', 'ocremix_filename'):
            counts['ocremix_old'] += 1
        elif strat == 'tag_based':
            counts['tag_based'] += 1
        elif strat == 'filename_norm':
            counts['filename_norm'] += 1

        fname = os.path.splitext(os.path.basename(path))[0]
        if TRACK_NUM_RE.match(fname):
            counts['musicbrainz'] += 1

    n = len(sample)
    print('  Strategy detection:')
    for k, v in counts.items():
        print(f'    {k}: {v}/{n} files')

    if counts['musicbrainz'] > n * 0.5:
        strategy = 'musicbrainz'
        note = 'Files appear to be track-number only — MusicBrainz lookup needed'
    elif counts['ocremix_tagged'] > n * 0.3:
        strategy = None
        note = 'OC ReMix with full tags detected — auto-detection handles this'
    elif counts['ocremix_old'] > n * 0.3:
        strategy = None
        note = 'Old OC ReMix format — auto-detection handles this'
    elif counts['tag_based'] > n * 0.4:
        strategy = None
        note = 'Good tags present — auto-detection handles this'
    else:
        strategy = 'filename_norm'
        note = 'No reliable tags — filename parsing will be used'

    print(f'\n  Recommended strategy: {strategy or "auto"}')
    print(f'  Note: {note}')
    print('\n  Suggested config.yaml entry:')
    entry_lines = [f'  - path: "{folder_path}"']
    if strategy:
        entry_lines.append(f'    strategy: {strategy}')
    if strategy == 'musicbrainz':
        entry_lines.append('    lookup: true')
    entry_lines.append('    recursive: false')
    print('\n'.join(entry_lines))


# ---------------------------------------------------------------------------
# Tag sync
# ---------------------------------------------------------------------------

def _run_sync_tags(folder_cfgs: list, apply: bool):
    """Audit or safely apply filename-derived tag repairs."""
    from rich.console import Console

    console = Console()
    totals = {'updated': 0, 'errors': 0, 'total': 0}

    for cfg in folder_cfgs:
        path = cfg.get('path', '')
        if not os.path.isdir(path):
            console.print(f'[yellow]Skipping (not found):[/yellow] {path}')
            continue

        recursive = cfg.get('recursive', True)
        console.print(f'\n[bold cyan]{path}[/bold cyan]')
        total_files = len(scan_folder(path, recursive=recursive))
        proposals, issues = plan_tag_updates(
            folder_path=path,
            recursive=recursive,
        )
        totals['updated'] += len(proposals)
        totals['errors'] += len(issues)
        totals['total'] += len(scan_folder(path, recursive=recursive))
        action = 'Updated' if apply else 'Would update'
        results = []
        if apply and proposals:
            review = ReviewPlan.create(
                root=path,
                recursive=recursive,
                tag_proposals=proposals,
                issues=issues,
            )
            results = apply_review_plan(review, [item.id for item in proposals])
            totals['updated'] = totals['updated'] - len(
                [item for item in results if item.status != 'succeeded']
            )
            totals['errors'] += len(
                [item for item in results if item.status == 'failed']
            )
        updated_count = (
            len(proposals)
            if not apply
            else sum(result.status == 'succeeded' for result in results)
        )
        error_count = len(issues) + sum(
            result.status == 'failed' for result in results
        )
        console.print(
            f'  {action}: [green]{updated_count}[/green]  '
            f'Errors: [red]{error_count}[/red]  '
            f'/ {total_files} files'
        )

        for item in proposals[:4]:
            console.print(
                f'    [dim]{item.path}[/dim] '
                f'{item.before.get("title", "")} → {item.after.get("title", "")}'
            )
        for issue in issues[:3]:
            console.print(f'  [red]ERROR[/red] {issue["path"]}: {issue["message"]}')

    console.print()
    action = 'Updated' if apply else 'Would update'
    console.print(
        f'{action} tags for [green]{totals["updated"]}[/green] of '
        f'{totals["total"]} files.  Errors: {totals["errors"]}.'
    )


# ---------------------------------------------------------------------------
# AcoustID helpers
# ---------------------------------------------------------------------------

def _check_fpcalc():
    """Warn without aborting when the optional fingerprint helper is absent."""
    from renamer.acoustid import is_fpcalc_available
    if not is_fpcalc_available():
        print(
            'WARNING: fpcalc not found.\n'
            '  Download Chromaprint from https://acoustid.org/chromaprint\n'
            '  Add fpcalc.exe to PATH or place it beside the application.\n'
            '  Fingerprinting will fail for all files until fpcalc is available.\n'
        )


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------

def _run_dedup(folder_cfgs: list, apply: bool):
    from rich.console import Console
    console = Console()
    totals = {
        'groups': 0,
        'auto_safe_groups': 0,
        'review_groups': 0,
        'unsafe_groups': 0,
        'errors': 0,
    }

    for cfg in folder_cfgs:
        path = cfg.get('path', '')
        if not os.path.isdir(path):
            console.print(f'[yellow]Skipping (not found):[/yellow] {path}')
            continue

        summary = dedup_universal_folder(
            folder_path=path,
            dry_run=not apply,
            recursive=cfg.get('recursive', False),
        )
        for key in totals:
            totals[key] += summary.get(key, 0)
        for finding in summary.get('findings', [])[:10]:
            console.print(
                f'  [{finding.classification}] '
                f'{finding.paths[0]}  ({len(finding.paths)} files)'
            )

    console.print()
    mode = 'Audit' if not apply else 'Apply blocked'
    console.print(
        f'{mode} — [cyan]{totals["groups"]}[/cyan] duplicate groups: '
        f'[green]{totals["auto_safe_groups"]}[/green] exact-content, '
        f'[yellow]{totals["review_groups"]}[/yellow] review, '
        f'[red]{totals["unsafe_groups"]}[/red] keep-both. '
        'No files were deleted.'
    )


def _run_dedup_ocremix(folder_cfgs: list, _apply: bool):
    from rich.console import Console

    console = Console()
    totals = {
        'groups': 0,
        'auto_safe_groups': 0,
        'review_groups': 0,
        'unsafe_groups': 0,
        'to_delete': 0,
        'deleted': 0,
        'errors': 0,
        'scanned_files': 0,
        'ocremix_files': 0,
    }
    any_fpcalc = False

    for cfg in folder_cfgs:
        path = cfg.get('path', '')
        if not os.path.isdir(path):
            console.print(f'[yellow]Skipping (not found):[/yellow] {path}')
            continue

        summary = dedup_ocremix_folder(
            folder_path=path,
            # Duplicate removal is intentionally read-only until the
            # universal Recycle Bin path is available.
            dry_run=True,
            recursive=cfg.get('recursive', False),
        )
        for key in totals:
            totals[key] += summary.get(key, 0)
        any_fpcalc = any_fpcalc or bool(summary.get('fingerprint_available'))

    console.print()
    fpcalc_note = '' if any_fpcalc else ' (fpcalc unavailable; relied on SHA1 only)'
    console.print(
        f'Audit-only OC ReMix report — '
        f'[green]{totals["auto_safe_groups"]}[/green] auto-safe groups, '
        f'[cyan]{totals["review_groups"]}[/cyan] review groups, '
        f'[yellow]{totals["unsafe_groups"]}[/yellow] unsafe groups. '
        f'No files were deleted.{fpcalc_note}'
    )


def _undo_latest_batch() -> None:
    batch = latest_undoable_batch()
    if batch is None:
        print("No recoverable batch is available.")
        return
    results = undo_batch(batch["batch_id"])
    succeeded = sum(result.status == "succeeded" for result in results)
    failed = sum(result.status == "failed" for result in results)
    print(f"Undo complete: {succeeded} restored, {failed} failed.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description='Rename music files to a consistent Artist - Title format.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument('--folder', metavar='PATH',
                   help='Process a single folder (overrides config.yaml)')
    p.add_argument('--gui', action='store_true',
                   help='Open the review-first desktop application')
    p.add_argument('--strategy', metavar='NAME',
                   help='Force a strategy for --folder (regular, filename_norm, musicbrainz)')
    p.add_argument('--regular', action='store_true',
                   help='Use source-neutral regular music parsing for --folder')
    p.add_argument('--apply', action='store_true',
                   help='Rename files (default: dry-run preview only)')
    p.add_argument('--interactive', action='store_true',
                   help='Approve each rename one by one')
    p.add_argument('--lookup', action='store_true',
                   help='Enable MusicBrainz API lookups where needed')
    p.add_argument('--undo', action='store_true',
                   help='Undo the renames from the last --apply run')
    p.add_argument('--auto', action='store_true',
                   help='Detect the best strategy for --folder and print a config suggestion')
    p.add_argument('--sync-tags', action='store_true',
                   help='Write ID3 tags to match the current filenames (dry-run by default, add --apply to commit)')
    p.add_argument('--audit', action='store_true',
                   help='Build a read-only review report without changing files')
    p.add_argument('--dedup', action='store_true',
                   help='Audit regular-library duplicate candidates (read-only)')
    p.add_argument('--dedup-regular', action='store_true',
                   help='Audit regular-library duplicate candidates (read-only)')
    p.add_argument('--dedup-ocremix', action='store_true',
                   help='Legacy OC ReMix diagnostic audit (read-only; no deletion)')
    p.add_argument('--fingerprint', action='store_true',
                   help='Enable AcoustID audio fingerprinting for files with missing or junk tags. '
                        'Requires fpcalc and an AcoustID API key from the environment or .env.')
    p.add_argument('--acoustid-key', metavar='KEY',
                   help='AcoustID API key (prefer ACOUSTID_API_KEY or .env to avoid shell history).')
    p.add_argument('--config', default=CONFIG_PATH, metavar='FILE',
                   help='Path to config.yaml (default: per-user Local AppData config)')
    return p


def main():
    args = _build_parser().parse_args()
    ensure_app_dirs()

    dedup_modes = sum(
        bool(value)
        for value in (args.dedup, args.dedup_regular, args.dedup_ocremix)
    )
    if dedup_modes > 1:
        print('Choose only one dedup mode')
        sys.exit(1)
    if args.regular and args.strategy:
        print('Choose either --regular or --strategy, not both')
        sys.exit(1)

    if args.gui:
        from gui.app import run

        run()
        return

    if args.undo:
        _undo_latest_batch()
        return

    if args.auto:
        if not args.folder:
            print('--auto requires --folder PATH')
            sys.exit(1)
        _auto_detect(args.folder)
        return

    # Build folder list: CLI override or config.yaml
    if args.folder:
        folder_cfgs = [{
            'path': args.folder,
            'strategy': 'regular' if args.regular else args.strategy,
            'recursive': True,
            'lookup': args.lookup,
        }]
    else:
        try:
            config = _load_config(args.config)
        except ValueError as exc:
            print(f'Configuration error: {exc}')
            sys.exit(1)
        folder_cfgs = config.get('folders', [])

    if not folder_cfgs:
        explicit_action = any(
            (
                args.apply,
                args.undo,
                args.auto,
                args.sync_tags,
                args.audit,
                args.dedup,
                args.dedup_regular,
                args.dedup_ocremix,
                args.fingerprint,
            )
        )
        if not explicit_action:
            from gui.app import run

            run()
            return
        print('No folders configured. Use --folder or open the GUI.')
        sys.exit(1)

    # Resolve AcoustID key: --acoustid-key > ACOUSTID_API_KEY env var
    acoustid_key = None
    if args.fingerprint:
        acoustid_key = args.acoustid_key or resolve_acoustid_key()
        if not acoustid_key:
            print(
                'AcoustID key not configured; continuing without '
                'online identification.'
            )
            args.fingerprint = False
        else:
            _check_fpcalc()

    if args.sync_tags:
        _run_sync_tags(folder_cfgs, apply=args.apply)
        return

    if args.dedup or args.dedup_regular:
        _run_dedup(folder_cfgs, apply=args.apply)
        return

    if args.dedup_ocremix:
        _run_dedup_ocremix(folder_cfgs, args.apply)
        return

    if args.audit:
        from rich.console import Console

        console = Console()
        for cfg in folder_cfgs:
            path = cfg.get('path', '')
            if not os.path.isdir(path):
                console.print(f'[yellow]Skipping (not found):[/yellow] {path}')
                continue
            plan = analyze_folder(
                path,
                strategy=cfg.get('strategy'),
                recursive=cfg.get('recursive', True),
                lookup=args.lookup or cfg.get('lookup', False),
                acoustid_key=acoustid_key,
            )
            console.print(
                f'[cyan]{path}[/cyan]: '
                f'{len(plan.rename_proposals)} renames, '
                f'{len(plan.tag_proposals)} tag repairs, '
                f'{len(plan.duplicate_findings)} duplicate findings, '
                f'{len(plan.issues)} issues'
            )
        return

    total_renamed = 0
    total_would_rename = 0
    total_files = 0
    total_errors = 0

    for cfg in folder_cfgs:
        path = cfg.get('path', '')
        if not os.path.isdir(path):
            print(f'\nSkipping (not found): {path}')
            continue

        recursive = cfg.get('recursive', True)
        proposals, issues = plan_renames(
            folder_path=path,
            strategy=cfg.get('strategy'),
            recursive=recursive,
            lookup=args.lookup or cfg.get('lookup', False),
            acoustid_key=acoustid_key,
        )
        total_files += len(scan_folder(path, recursive=recursive))
        total_errors += len(issues)
        selected = []
        for item in proposals:
            if args.interactive:
                print(f'\n{item.old_path}\n→ {item.new_path}')
                if input('Apply? [Y/n] ').strip().lower() == 'n':
                    continue
            selected.append(item.id)

        if args.apply and selected:
            review = ReviewPlan.create(
                root=path,
                recursive=recursive,
                rename_proposals=proposals,
                issues=issues,
            )
            results = apply_review_plan(review, selected)
            total_renamed += sum(result.status == 'succeeded' for result in results)
            total_errors += sum(result.status == 'failed' for result in results)
        else:
            total_would_rename += len(selected)
            for item in proposals[:20]:
                print(f'  {item.old_path}\n  → {item.new_path}')
        for issue in issues[:10]:
            print(f'  ERROR {issue["path"]}: {issue["message"]}')

    print()
    if not args.apply:
        print(
            f'Dry run complete — {total_would_rename} of {total_files} files would be renamed. '
            f'Issues: {total_errors}. Add --apply to commit selected changes.'
        )
    else:
        print(
            f'Done. Renamed {total_renamed} of {total_files} files. '
            f'Errors: {total_errors}.'
        )


if __name__ == '__main__':
    main()
