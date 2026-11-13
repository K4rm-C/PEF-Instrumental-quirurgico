# Apply additive MS migrations 004..019 against the running OP-local Postgres container.
# Idempotent SQL; safe to re-run. Usage (repo root):
#   .\scripts\apply_ms_migrations.ps1
# Optional:
#   .\scripts\apply_ms_migrations.ps1 -Container pef_postgres -User MedAdmin -Database InstruMed

param(
    [string]$Container = 'pef_postgres',
    [string]$User = 'MedAdmin',
    [string]$Database = 'InstruMed'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$dir = Join-Path $root 'data\migrations'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error 'docker not found in PATH'
    exit 1
}

$running = docker ps --filter "name=^/${Container}$" --format '{{.Names}}' 2>$null
if (-not $running) {
    # Compose project names may omit leading slash / use prefix
    $running = docker ps --format '{{.Names}}' | Where-Object { $_ -eq $Container -or $_ -like "*$Container*" } | Select-Object -First 1
}
if (-not $running) {
    Write-Error "Container '$Container' is not running. Start infra first: docker compose up -d"
    exit 1
}
$Container = $running

$files = Get-ChildItem -Path $dir -Filter '*.sql' |
    Where-Object { $_.Name -match '^0(0[4-9]|1[0-9])_' } |
    Sort-Object Name

if ($files.Count -eq 0) {
    Write-Error "No migration files 004..019 under $dir"
    exit 1
}

Write-Host "Applying $($files.Count) migrations on $Container ($User/$Database)..."
foreach ($f in $files) {
    Write-Host "  -> $($f.Name)"
    # Strip lone BEGIN/COMMIT so a host-side multi-file run stays consistent if wrapped later
    $sql = (Get-Content -LiteralPath $f.FullName -Raw) -replace '(?m)^\s*(BEGIN|COMMIT);\s*\r?\n?', ''
    # psql emits NOTICE on stderr; do not treat that as a terminating PowerShell error
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $sql | docker exec -i $Container psql -U $User -d $Database -v ON_ERROR_STOP=1 -q 2>&1 | ForEach-Object {
        $line = "$_"
        if ($line -match '^(ERROR|FATAL):') { Write-Host $line }
    }
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($code -ne 0) {
        Write-Error "Failed on $($f.Name) (exit $code)"
        exit $code
    }
}
Write-Host 'DONE'
