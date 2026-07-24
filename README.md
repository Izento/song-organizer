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
uv run ballad
```

Running `ballad` without a command opens the GUI. Power-user commands use
explicit subcommands:

```powershell
uv run ballad rename --folder "D:\Music\Library"
uv run ballad audit --folder "D:\Music\Library"
uv run ballad tags --folder "D:\Music\Library"
uv run ballad dedup --folder "D:\Music\Library"
uv run ballad auto-detect --folder "D:\Music\Library"
uv run ballad undo
```

## Optional online identification

AcoustID lookup requires both `fpcalc.exe` and an `ACOUSTID_API_KEY`. Built
Ballad packages include `fpcalc.exe` automatically, so users only need to
provide a key for online identification. The app works normally without
either one. When enabled, `fpcalc` processes audio locally; the app sends only
the resulting fingerprint and duration to AcoustID to request identity
metadata.

For local development, copy `.env.example` to `.env` and set the key there.
Installed builds also look for `.env` under
`%LOCALAPPDATA%\SongOrganizer` or beside the executable.

The optional fingerprint-based duplicate check also uses `fpcalc` and does not
require an AcoustID key. Direct source runs use `fpcalc` from the environment;
the release build prepares and bundles it automatically. Ballad enables
fingerprint duplicate evidence by default whenever `fpcalc` is available; turn
off the checkbox for a faster metadata/hash-only duplicate scan.

### Get your own AcoustID key

1. Create or sign in to an [AcoustID](https://acoustid.org/) account and
   register an application to obtain a lookup client key. See the
   [AcoustID web-service documentation](https://acoustid.org/documentation/webservice)
   for its API-key and usage rules.
2. In Ballad's folder, copy `.env.example` to `.env`.
3. Open `.env` in a text editor and set your own key:

   ```text
   ACOUSTID_API_KEY=your_key_here
   ```

4. Restart Ballad. The header will show `Online identification: enabled`.

AcoustID lookup is optional; Ballad still analyzes and repairs
filenames/tags without a key.

The GUI uses available online identification for missing or conflicting
metadata. Its fingerprint checkbox controls optional duplicate-check evidence.

## Build

Build a keyless one-folder Windows package with:

```powershell
.\build.ps1
```

The public package is written to `release\public\Ballad`. On the first build,
the script downloads the pinned official Chromaprint archive into the ignored
`build\dependencies` cache and verifies its SHA-256 before packaging
`fpcalc.exe`. Later builds reuse the verified cache.

The package includes `fpcalc.exe`, `.env.example`, `LICENSE`,
`THIRD_PARTY_NOTICES.txt`, and `LGPL-2.1.txt`. It never includes a private
`.env`. The pinned version and checksum are recorded in `chromaprint.json`;
update them together only after reviewing a new official Chromaprint release.

## Safety

- Analysis is read-only until selected changes are confirmed.
- Applied filename and tag changes are journaled for recovery and undo.
- Duplicate findings are read-only; no normal operation permanently deletes
  files.
- CLI rename and tag changes require `--apply`; `ballad undo` restores the
  latest recoverable journaled batch.

## Development checks

```powershell
uv sync --extra test
uv run pytest
```

Application state, cache files, build outputs, virtual environments, API keys,
and private release archives are excluded from Git.

## License

Song Organizer is licensed under the MIT License. See `LICENSE`.
