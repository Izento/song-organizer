# Song Organizer

Song Organizer is a review-first Windows music-library organizer. Its desktop
interface is branded **Ballad**. It analyzes a folder without changing files,
presents filename and tag repairs for review, and applies only the actions the
user selects.

## What it does

- Normalizes filenames to `Artist - Title (feat. Guest).ext`.
- Audits and repairs tags to match approved filenames.
- Identifies tracks with missing or conflicting metadata through optional
  AcoustID lookup.
- Finds duplicate candidates without deleting files.
- Journals applied changes and supports guarded undo.

## Run from source

Requires Windows, Python 3.11+, and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync
uv run python -m gui
```

The command-line interface remains available:

```powershell
uv run python main.py --folder "D:\Music\Library" --audit
uv run python main.py --folder "D:\Music\Library" --sync-tags
uv run python main.py --folder "D:\Music\Library" --dedup-regular
uv run python main.py --undo
```

## Optional online identification

AcoustID lookup requires both `fpcalc.exe` and an `ACOUSTID_API_KEY`. The app
works normally without either one. When enabled, `fpcalc` processes audio
locally; the app sends only the resulting fingerprint and duration to AcoustID
to request identity metadata.

For local development, copy `.env.example` to `.env` and set the key there.
The `.env` file is ignored by Git and must never be committed or included in a
public release. Installed builds also look for `.env` under
`%LOCALAPPDATA%\SongOrganizer` or beside the executable.

The GUI uses available online identification for missing or conflicting
metadata. Its fingerprint checkbox controls optional duplicate-check evidence.

## Build

Build a keyless one-folder Windows package with:

```powershell
.\build.ps1
```

The package is written to `dist\SongOrganizer`. `fpcalc.exe` is optional and
ignored by Git. Place a vetted copy beside `song_organizer.spec` before a build
only when fingerprinting is needed. The build bundles that helper when present;
include its required third-party notices with any public binary release.

## Safety

- Analysis is read-only until selected changes are confirmed.
- Applied filename and tag changes are journaled for recovery and undo.
- Duplicate findings are read-only; no normal operation permanently deletes
  files.
- CLI changes require `--apply`; `--undo` restores the latest recoverable
  journaled batch.

## Development checks

```powershell
uv sync --extra test
uv run pytest
```

Application state, cache files, build outputs, virtual environments, API keys,
and private release archives are excluded from Git.

## License

Song Organizer is licensed under the MIT License. See `LICENSE`.
