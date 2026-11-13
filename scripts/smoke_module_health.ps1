# Brief request-level health checks for OP-local + integral MS modules.
# Usage (repo root):  .\scripts\smoke_module_health.ps1
# Exit 0 if all probed URLs that are expected respond; skips quietly when a service is optional/down unless -Strict.

param(
    [switch]$Strict,
    [switch]$IntegralOnly,
    [switch]$OpLocalOnly
)

$ErrorActionPreference = 'Continue'
$failed = @()
$ok = @()
$skipped = @()

function Probe([string]$Name, [string]$Url, [int]$TimeoutSec = 4) {
    try {
        $resp = Invoke-WebRequest -Uri $Url -Method GET -TimeoutSec $TimeoutSec -UseBasicParsing
        if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) {
            $script:ok += "$Name $($resp.StatusCode) $Url"
            Write-Host "OK  $Name [$($resp.StatusCode)] $Url"
            return
        }
        $script:failed += "$Name unexpected $($resp.StatusCode) $Url"
        Write-Host "FAIL $Name [$($resp.StatusCode)] $Url"
    } catch {
        if ($Strict) {
            $script:failed += "$Name $($_.Exception.Message) $Url"
            Write-Host "FAIL $Name $Url — $($_.Exception.Message)"
        } else {
            $script:skipped += "$Name $Url"
            Write-Host "SKIP $Name (unreachable) $Url"
        }
    }
}

Write-Host "=== PEF module health smoke ==="

if (-not $IntegralOnly) {
    Write-Host "-- OP-local / host processes --"
    Probe 'Auth /health' 'http://127.0.0.1:5001/health'
    Probe 'Auth /api/v1 ready' 'http://127.0.0.1:5001/health/ready'
    Probe 'Web /sign-in' 'http://127.0.0.1:5000/sign-in'
    Probe 'Web /health' 'http://127.0.0.1:5000/health'
    Probe 'VisionWorker' 'http://127.0.0.1:5002/health'
}

if (-not $OpLocalOnly) {
    Write-Host "-- Integral MS (defaults from .env.example) --"
    Probe 'Catalog' 'http://127.0.0.1:5003/health/ready'
    Probe 'Session' 'http://127.0.0.1:5004/health'
    Probe 'EvidenceService' 'http://127.0.0.1:5005/health/ready'
    Probe 'Audit' 'http://127.0.0.1:5006/health/ready'
    Probe 'Notifications' 'http://127.0.0.1:5007/health/ready'
    Probe 'Monitoring' 'http://127.0.0.1:5008/health/ready'
    Probe 'Integration' 'http://127.0.0.1:5009/health/ready'
    Probe 'Gateway' 'http://127.0.0.1:8080/health'
}

Write-Host ""
Write-Host "OK: $($ok.Count)  SKIP: $($skipped.Count)  FAIL: $($failed.Count)"
if ($failed.Count -gt 0) {
    $failed | ForEach-Object { Write-Host " - $_" }
    exit 1
}
exit 0
