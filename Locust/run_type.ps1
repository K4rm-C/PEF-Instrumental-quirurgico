# Run one PEF Locust scenario / profile and write English HTML/CSV reports under Locust/reports/.
#
# Usage (repo root or Locust/):
#   .\Locust\run_type.ps1 -Type operator_portal
#   .\Locust\run_type.ps1 -Type baseline
#   .\Locust\run_type.ps1 -Type auth_api -Users 2 -SpawnRate 1 -RunTime 3m
#   .\Locust\run_all.ps1
#
# Does not start Docker; the integral stack (and Gateway for API types) must already be up.
# Max recommended -RunTime: 10m (enforced soft-cap in this script).

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet(
        "auth_api", "operator_portal", "spd_portal", "admin_portal",
        "floor_api", "catalog_api", "session_api", "health", "mixed",
        "baseline", "load", "spike", "stress", "soak"
    )]
    [string]$Type,

    [int]$Users = -1,
    [double]$SpawnRate = 1,
    [string]$RunTime = "",
    [string]$GatewayUrl = "",
    [string]$WebUrl = "",
    [string]$AuthUrl = "",
    [switch]$Headless = $true
)

$ErrorActionPreference = "Stop"
$LocustRoot = $PSScriptRoot
Set-Location $LocustRoot

$defaults = @{
    auth_api         = 2
    operator_portal  = 8
    spd_portal       = 6
    admin_portal     = 4
    floor_api        = 6
    catalog_api      = 4
    session_api      = 4
    health           = 10
    mixed            = 12
    baseline         = 5
    load             = 20
    spike            = 40
    stress           = 50
    soak             = 15
}
$defaultRuntime = @{
    auth_api         = "3m"
    operator_portal  = "4m"
    spd_portal       = "4m"
    admin_portal     = "4m"
    floor_api        = "3m"
    catalog_api      = "3m"
    session_api      = "3m"
    health           = "2m"
    mixed            = "5m"
    baseline         = "3m"
    load             = "5m"
    spike            = "4m"
    stress           = "8m"
    soak             = "10m"
}
$shapeTypes = @("baseline", "load", "spike", "stress", "soak")

if ($Users -lt 1) { $Users = [int]$defaults[$Type] }
if (-not $RunTime) { $RunTime = [string]$defaultRuntime[$Type] }

# Soft-cap: never schedule more than 10 minutes per type.
function Convert-ToSeconds([string]$rt) {
    if ($rt -match '^(\d+)s$') { return [int]$Matches[1] }
    if ($rt -match '^(\d+)m$') { return [int]$Matches[1] * 60 }
    if ($rt -match '^(\d+)h$') { return [int]$Matches[1] * 3600 }
    if ($rt -match '^\d+$') { return [int]$rt }
    throw "Unsupported RunTime format: $rt (use Ns, Nm, or Nh)"
}
$seconds = Convert-ToSeconds $RunTime
if ($seconds -gt 600) {
    Write-Host "RunTime $RunTime exceeds 10m cap; clamping to 10m."
    $RunTime = "10m"
    $seconds = 600
}

if ($GatewayUrl) { $env:PEF_GATEWAY_URL = $GatewayUrl.TrimEnd("/") }
if ($WebUrl) { $env:PEF_WEB_URL = $WebUrl.TrimEnd("/") }
if ($AuthUrl) { $env:PEF_AUTH_URL = $AuthUrl.TrimEnd("/") }

if (-not $env:PEF_GATEWAY_URL) { $env:PEF_GATEWAY_URL = "http://127.0.0.1:8080" }
if (-not $env:PEF_WEB_URL) { $env:PEF_WEB_URL = "http://127.0.0.1:5000" }
if (-not $env:PEF_AUTH_URL) { $env:PEF_AUTH_URL = "http://127.0.0.1:5001" }

$portalTypes = @("operator_portal", "spd_portal", "admin_portal", "mixed") + $shapeTypes
if ($portalTypes -contains $Type) {
    $hostUrl = $env:PEF_WEB_URL
} else {
    $hostUrl = $env:PEF_GATEWAY_URL
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outDir = Join-Path (Join-Path (Join-Path $LocustRoot "reports") $Type) $stamp
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$locustfile = Join-Path (Join-Path $LocustRoot "scenarios") "$Type.py"
if (-not (Test-Path $locustfile)) {
    throw "Missing locustfile: $locustfile"
}

$prefix = Join-Path $outDir "pef_${Type}"
$html = "$prefix`_report.html"
$csvPrefix = $prefix

$locustArgs = @(
    "-f", $locustfile,
    "--host", $hostUrl,
    "--html", $html,
    "--csv", $csvPrefix,
    "--exit-code-on-error", "1"
)
if ($Headless) { $locustArgs = @("--headless") + $locustArgs }

# Shape profiles control users/spawn/time themselves; still pass a run-time safety net.
if ($shapeTypes -contains $Type) {
    $locustArgs += @("--run-time", $RunTime)
    Write-Host "Locust profile=$Type host=$hostUrl shape=on time=$RunTime (users shaped in locustfile)"
} else {
    $locustArgs += @("--users", "$Users", "--spawn-rate", "$SpawnRate", "--run-time", $RunTime)
    Write-Host "Locust type=$Type host=$hostUrl users=$Users spawn=$SpawnRate time=$RunTime"
}
Write-Host "Reports -> $outDir"

$summaryPath = Join-Path $outDir "summary.txt"
@"
PEF Locust load report (English)
================================
Type:          $Type
Started (local): $(Get-Date -Format "yyyy-MM-dd HH:mm:ss K")
Host:          $hostUrl
Gateway:       $($env:PEF_GATEWAY_URL)
Portal:        $($env:PEF_WEB_URL)
Auth:          $($env:PEF_AUTH_URL)
Users:         $(if ($shapeTypes -contains $Type) { "shaped (see profile)" } else { $Users })
Spawn rate:    $(if ($shapeTypes -contains $Type) { "shaped" } else { $SpawnRate })
Run time:      $RunTime (cap 10m)
Headless:      $Headless
Locustfile:    scenarios/$Type.py

Artifacts
---------
- pef_${Type}_report.html   Locust HTML report
- pef_${Type}_stats.csv     Request stats
- pef_${Type}_stats_history.csv
- pef_${Type}_failures.csv
- pef_${Type}_exceptions.csv

Notes
-----
- Demo credentials only (see data/seeds/DEMO_CREDENTIALS.md).
- auth_api: keep users low; Gateway login is rate-limited; refresh uses JSON body.
- admin_portal: kits traffic uses dual.admin.spd (SPD RACI); IT pages use it_admin.
- Profiles baseline/load/spike/stress/soak drive mixed OP+SPD+catalog via LoadTestShape.
- Portal types are read-heavy (no inventory mutations in this suite).
"@ | Set-Content -Encoding utf8 $summaryPath

& locust @locustArgs
$code = $LASTEXITCODE

@"

Finished (local): $(Get-Date -Format "yyyy-MM-dd HH:mm:ss K")
Exit code:        $code
"@ | Add-Content -Encoding utf8 $summaryPath

if ($code -ne 0) {
    Write-Host "Locust finished with exit code $code (see failures CSV / HTML)."
    exit $code
}
Write-Host "OK report written: $html"
