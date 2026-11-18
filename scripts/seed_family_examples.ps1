# Manual re-run of family example uploads (also runs automatically via Compose service
# seed-family-examples). From repo root, after Postgres + Garage are up:
#
#   .\scripts\seed_family_examples.ps1
#   .\scripts\seed_family_examples.ps1 -Backend local

param(
    [ValidateSet('s3', 'local')]
    [string]$Backend = 's3'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (Test-Path .\.env) {
    Get-Content .\.env | ForEach-Object {
        if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
        $k, $v = $_.Split('=', 2)
        if (-not [string]::IsNullOrWhiteSpace($k) -and -not (Test-Path "Env:$($k.Trim())")) {
            Set-Item -Path "Env:$($k.Trim())" -Value $v.Trim().Trim('"')
        }
    }
}

$env:ADMIN_STORAGE_BACKEND = $Backend
if (-not $env:ADMIN_S3_BUCKET) { $env:ADMIN_S3_BUCKET = 'pef-admin' }
if (-not $env:S3_ENDPOINT -and -not $env:S3_INTERNAL_ENDPOINT) {
    $env:S3_ENDPOINT = 'http://127.0.0.1:3900'
}

if ($Backend -eq 's3') {
    $garage = docker ps --format '{{.Names}}' | Select-String -Pattern 'garage'
    if ($garage) {
        $name = ($garage | Select-Object -First 1).ToString().Trim()
        docker exec $name /garage bucket info pef-admin 2>$null
        if ($LASTEXITCODE -ne 0) {
            docker exec $name /garage bucket create pef-admin 2>$null
            docker exec $name /garage bucket allow --read --write --owner pef-admin --key MedAdmin3 2>$null
        }
    }
}

python (Join-Path $Root 'scripts\seed_family_examples.py')
exit $LASTEXITCODE
