$ErrorActionPreference = "Stop"

$releasePath = "release\public"
$workPath = "build\pyinstaller"

uv run --extra build pyinstaller --noconfirm --clean --distpath $releasePath --workpath $workPath song_organizer.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}
Write-Host "Built release\public\Ballad"
