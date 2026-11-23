# Run every Locust surface type + performance profile (default times, max 10m each).
#
# Usage (from Locust/ or repo root):
#   .\Locust\run_all.ps1
#   .\Locust\run_all.ps1 -OnlyProfiles
#   .\Locust\run_all.ps1 -OnlySurfaces

[CmdletBinding()]
param(
    [string]$GatewayUrl = "http://127.0.0.1:8080",
    [string]$WebUrl = "http://127.0.0.1:5000",
    [string]$AuthUrl = "http://127.0.0.1:5001",
    [switch]$OnlyProfiles,
    [switch]$OnlySurfaces
)

$ErrorActionPreference = "Continue"
$LocustRoot = $PSScriptRoot
$runner = Join-Path $LocustRoot "run_type.ps1"

$surfaces = @(
    "health", "auth_api", "operator_portal", "spd_portal", "admin_portal",
    "floor_api", "catalog_api", "session_api", "mixed"
)
$profiles = @("baseline", "load", "spike", "stress", "soak")

$types = @()
if ($OnlyProfiles) { $types = $profiles }
elseif ($OnlySurfaces) { $types = $surfaces }
else { $types = $surfaces + $profiles }

$failed = @()
$ok = @()
foreach ($t in $types) {
    Write-Host "`n===== LOCUST $t ====="
    & powershell -NoProfile -ExecutionPolicy Bypass -File $runner `
        -Type $t -GatewayUrl $GatewayUrl -WebUrl $WebUrl -AuthUrl $AuthUrl
    if ($LASTEXITCODE -ne 0) {
        $failed += $t
        Write-Host "FAIL $t exit=$LASTEXITCODE"
    } else {
        $ok += $t
        Write-Host "OK $t"
    }
}

Write-Host "`n===== SUMMARY ====="
Write-Host ("OK: " + ($(if ($ok.Count) { $ok -join ', ' } else { '(none)' })))
if ($failed.Count) {
    Write-Host ("Failed: " + ($failed -join ', '))
    exit 1
}
Write-Host "All Locust types finished with exit 0"
exit 0
