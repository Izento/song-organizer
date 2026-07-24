# PyInstaller one-folder, windowed build.
import os
from pathlib import Path


ROOT = Path(SPECPATH)
fpcalc_override = os.environ.get("BALLAD_FPCALC_PATH")
fpcalc = Path(fpcalc_override) if fpcalc_override else ROOT / "fpcalc.exe"
icon = ROOT / "ballad.ico"
if not fpcalc.is_file():
    raise FileNotFoundError(
        f"Missing {fpcalc.name}. Run .\\build.ps1 to prepare the release dependencies."
    )
binaries = [(str(fpcalc), ".")]
datas = []
if not icon.is_file():
    raise FileNotFoundError(f"Missing application icon: {icon}")
datas.append((str(icon), "."))
for notice in ("LICENSE", "LGPL-2.1.txt", "THIRD_PARTY_NOTICES.txt"):
    candidate = ROOT / notice
    if candidate.is_file():
        datas.append((str(candidate), "."))

a = Analysis(
    [str(ROOT / "gui" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "gui.app",
        "mutagen",
        "mutagen.id3",
        "mutagen.flac",
        "mutagen.oggvorbis",
        "mutagen.mp4",
        "mutagen.asf",
        "musicbrainzngs",
        "acoustid",
    ],
    excludes=["pytest", "rich"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Ballad",
    icon=str(icon),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Ballad",
)
