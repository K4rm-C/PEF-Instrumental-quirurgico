# Download a portable ffmpeg (Windows) into tools/ffmpeg/bin for EvidenceWorker H.264 encode.
# Usage (from repo root):
#   .\tools\ensure-ffmpeg.ps1
#   .\tools\ensure-ffmpeg.ps1 -Force

param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$DestRoot = Join-Path $RepoRoot 'tools\ffmpeg'
$BinDir = Join-Path $DestRoot 'bin'
$FfmpegExe = Join-Path $BinDir 'ffmpeg.exe'

function Test-FfmpegOk([string]$Path) {
    if (-not (Test-Path $Path)) { return $false }
    try {
        & $Path -version 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

$existing = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($existing -and -not $Force) {
    Write-Host "ffmpeg ya esta en PATH: $($existing.Source)" -ForegroundColor Green
    & ffmpeg -version | Select-Object -First 1
    exit 0
}

if ((Test-FfmpegOk $FfmpegExe) -and -not $Force) {
    Write-Host "ffmpeg local listo: $FfmpegExe" -ForegroundColor Green
    & $FfmpegExe -version | Select-Object -First 1
    Write-Host "EvidenceWorker antepone tools\ffmpeg\bin al PATH al arrancar." -ForegroundColor Cyan
    exit 0
}

$ZipUrl = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
$TempZip = Join-Path $env:TEMP ("pef-ffmpeg-" + [guid]::NewGuid().ToString('N') + '.zip')
$TempExtract = Join-Path $env:TEMP ("pef-ffmpeg-extract-" + [guid]::NewGuid().ToString('N'))

Write-Host "Descargando ffmpeg essentials..." -ForegroundColor Cyan
Write-Host "  $ZipUrl"
try {
    Invoke-WebRequest -Uri $ZipUrl -OutFile $TempZip -UseBasicParsing
} catch {
    Write-Host "Fallo la descarga. Instala ffmpeg manualmente o revisa red/proxy." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

Write-Host "Extrayendo..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $TempExtract | Out-Null
Expand-Archive -Path $TempZip -DestinationPath $TempExtract -Force

$foundBin = Get-ChildItem -Path $TempExtract -Recurse -Filter 'ffmpeg.exe' -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $foundBin) {
    Write-Host "No se encontro ffmpeg.exe dentro del zip." -ForegroundColor Red
    exit 1
}
$srcBin = $foundBin.Directory.FullName

if (Test-Path $DestRoot) {
    Remove-Item -Recurse -Force $DestRoot
}
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
Copy-Item -Path (Join-Path $srcBin '*') -Destination $BinDir -Recurse -Force

Remove-Item -Force $TempZip -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $TempExtract -ErrorAction SilentlyContinue

if (-not (Test-FfmpegOk $FfmpegExe)) {
    Write-Host "Instalacion incompleta: $FfmpegExe no responde." -ForegroundColor Red
    exit 1
}

Write-Host "OK ffmpeg instalado en $FfmpegExe" -ForegroundColor Green
& $FfmpegExe -version | Select-Object -First 1
Write-Host "Reinicia EvidenceWorker para usarlo (config antepone este bin al PATH)." -ForegroundColor Cyan
