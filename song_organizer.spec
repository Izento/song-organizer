# PyInstaller one-folder, windowed build.
from pathlib import Path


ROOT = Path(SPECPATH)
fpcalc = ROOT / "fpcalc.exe"
icon = ROOT / "ballad.ico"
binaries = [(str(fpcalc), ".")] if fpcalc.is_file() else []
datas = []
if not icon.is_file():
    raise FileNotFoundError(f"Missing application icon: {icon}")
datas.append((str(icon), "."))
for notice in ("LICENSE", "LICENSE.md", "THIRD_PARTY_NOTICES.txt"):
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
