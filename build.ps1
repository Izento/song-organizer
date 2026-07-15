$ErrorActionPreference = "Stop"

uv run --extra build pyinstaller --noconfirm --clean song_organizer.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}
Write-Host "Built dist\SongOrganizer"
