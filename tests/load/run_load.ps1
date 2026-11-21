<#
.SYNOPSIS
  Runs the PEF Locust suite against the integral Docker stack (see GUIA_LOCUST.md).

.EXAMPLE
  .\tests\load\run_load.ps1 -Profile smoke
  .\tests\load\run_load.ps1 -Profile load -Reset          # clean volume first (down -v / up --build)
  .\tests\load\run_load.ps1 -Profile stress -FlowUsers 2
  .\tests\load\run_load.ps1 -Ui                            # Locust web UI at http://127.0.0.1:8089
#>
param(
    [ValidateSet('smoke', 'load', 'stress')] [string] $Profile = 'smoke',
    [switch] $Reset,
    [switch] $SkipSeed,
    [switch] $Ui,
    [int] $FlowUsers = 1,
    [string] $Python = 'python',
    [string] $EnvFile = '.env'
)
# Native tools (docker, psql, locust) log to stderr; Windows PowerShell 5.1 would turn that into errors with 'Stop'.
# Every step checks $LASTEXITCODE instead.
$ErrorActionPreference = 'Continue'
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Set-Location $root
$compose = @('compose', '-f', 'docker-compose.integral.yml', '--env-file', $EnvFile)

function Get-EnvValue([string] $name, [string] $default) {
    $line = Get-Content $EnvFile | Where-Object { $_ -match "^\s*$name\s*=" } | Select-Object -First 1
    if ($line) { return ($line -split '=', 2)[1].Trim().Trim('"') }
    return $default
}

if ($Reset) {
    Write-Host '== Reset: clean volumes and rebuild the stack'
    & docker @compose down -v
    if ($LASTEXITCODE -ne 0) { throw 'docker compose down failed' }
    & docker @compose up -d --build
    if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed' }
}

Write-Host '== Waiting for services'
$health = @('http://127.0.0.1:5000/health/ready', 'http://127.0.0.1:5001/health',
            'http://127.0.0.1:5003/health/ready', 'http://127.0.0.1:5004/health',
            'http://127.0.0.1:5005/health/ready', 'http://127.0.0.1:5007/health/ready')
foreach ($url in $health) {
    $ok = $false
    for ($i = 0; $i -lt 60 -and -not $ok; $i++) {
        try { $ok = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop $url).StatusCode -eq 200 } catch { Start-Sleep 2 }
    }
    if (-not $ok) { throw "Service not ready: $url" }
    Write-Host "   ok $url"
}

if (-not $SkipSeed) {
    Write-Host '== Applying data/seeds/004_seed_demo_locust.sql (idempotent)'
    $user = Get-EnvValue 'POSTGRES_USER' 'MedAdmin'
    $db = Get-EnvValue 'POSTGRES_DB' 'InstruMed'
    Get-Content -Raw 'data\seeds\004_seed_demo_locust.sql' |
        & docker @compose exec -T postgres psql -v ON_ERROR_STOP=1 -q -U $user -d $db
    if ($LASTEXITCODE -ne 0) { throw 'seed 004 failed' }
}

if (-not (Test-Path 'data\seeds\DEMO_CREDENTIALS.local.md') -and -not $env:PEF_LOCUST_CREDENTIALS) {
    throw 'Missing data\seeds\DEMO_CREDENTIALS.local.md (or set PEF_LOCUST_CREDENTIALS)'
}

New-Item -ItemType Directory -Force 'tests\load\reports' | Out-Null
$env:PEF_FLOW_USERS = "$FlowUsers"
if ($Profile -eq 'stress') { $env:PEF_LOAD_SHAPE = 'stress' } else { Remove-Item Env:PEF_LOAD_SHAPE -ErrorAction SilentlyContinue }

Write-Host "== Locust profile '$Profile' (flow users: $FlowUsers)"
if ($Ui) {
    & $Python -m locust -f tests/load/locustfile.py
} else {
    & $Python -m locust --config "tests/load/profiles/$Profile.conf"
}
$code = $LASTEXITCODE
Write-Host "== Reports: tests\load\reports\$Profile.html and $Profile`_stats.csv (exit $code)"
exit $code
