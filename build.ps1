$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$root = $PSScriptRoot
$manifestPath = Join-Path $root "chromaprint.json"
$manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
$cachePath = Join-Path $root "build\dependencies\chromaprint"
$archivePath = Join-Path $cachePath $manifest.archive.filename
$extractPath = Join-Path $cachePath ("extracted-" + $manifest.version)
$expectedHash = $manifest.archive.sha256.ToUpperInvariant()

New-Item -ItemType Directory -Path $cachePath -Force | Out-Null

$archiveValid = $false
if (Test-Path $archivePath) {
    $archiveHash = (Get-FileHash $archivePath -Algorithm SHA256).Hash.ToUpperInvariant()
    $archiveValid = $archiveHash -eq $expectedHash
}
if (-not $archiveValid -and (Test-Path $extractPath)) {
    Remove-Item $extractPath -Recurse -Force
}

if (-not $archiveValid) {
    Write-Host "Downloading Chromaprint $($manifest.version)..."
    Invoke-WebRequest -Uri $manifest.archive.url -OutFile $archivePath
}

$archiveHash = (Get-FileHash $archivePath -Algorithm SHA256).Hash.ToUpperInvariant()
if ($archiveHash -ne $expectedHash) {
    throw "Chromaprint archive hash mismatch. Expected $expectedHash, got $archiveHash."
}

$fpcalcFiles = @()
if (Test-Path $extractPath) {
    $fpcalcFiles = @(Get-ChildItem $extractPath -Filter $manifest.binary -File -Recurse)
}
if ($fpcalcFiles.Count -ne 1) {
    if (Test-Path $extractPath) {
        Remove-Item $extractPath -Recurse -Force
    }
    New-Item -ItemType Directory -Path $extractPath -Force | Out-Null
    Expand-Archive -Path $archivePath -DestinationPath $extractPath -Force
    $fpcalcFiles = @(Get-ChildItem $extractPath -Filter $manifest.binary -File -Recurse)
}
if ($fpcalcFiles.Count -ne 1) {
    throw "Expected exactly one $($manifest.binary) in the Chromaprint archive."
}

$env:BALLAD_FPCALC_PATH = $fpcalcFiles[0].FullName
$releasePath = Join-Path $root "release\public"
$workPath = Join-Path $root "build\pyinstaller"

Push-Location $root
try {
    & uv run --extra build pyinstaller --noconfirm --clean `
        --distpath $releasePath --workpath $workPath song_organizer.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

$packagePath = Join-Path $releasePath "Ballad"
foreach ($file in @(".env.example", "LICENSE", "LGPL-2.1.txt", "THIRD_PARTY_NOTICES.txt")) {
    Copy-Item (Join-Path $root $file) $packagePath -Force
}
Write-Host "Built release\public\Ballad with bundled fpcalc"
