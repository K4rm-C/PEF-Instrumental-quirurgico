# Run one microservice using the shared apps/.venv-ms (host/dev).
# Usage (repo root):
#   .\scripts\run_ms.ps1 CatalogService
#   .\scripts\run_ms.ps1 SessionService
#   .\scripts\run_ms.ps1 EvidenceService
# Docker images still bake each app's own requirements; this venv is for local smokes/atomic host runs.

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('CatalogService','SessionService','EvidenceService','AuditService','NotificationsService','MonitoringService','IntegrationService')]
    [string]$Service
)

$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root 'apps\.venv-ms\Scripts\python.exe'
$dir = Join-Path $root "apps\$Service"

if (-not (Test-Path $py)) {
    Write-Error "Missing apps\.venv-ms — create with: python -m venv apps\.venv-ms && apps\.venv-ms\Scripts\pip install -r apps\requirements-ms.txt"
    exit 1
}
if (-not (Test-Path $dir)) {
    Write-Error "Missing $dir"
    exit 1
}

# Prefer service .env, else repo root .env
$envFile = Join-Path $dir '.env'
if (-not (Test-Path $envFile)) { $envFile = Join-Path $root '.env' }
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
        $k, $v = $_.Split('=', 2)
        [Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim().Trim('"'), 'Process')
    }
}

Set-Location $dir
$env:PYTHONPATH = $dir
Write-Host "Starting $Service with apps\.venv-ms from $dir"
& $py main.py
