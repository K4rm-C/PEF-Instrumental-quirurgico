# Detect primary LAN IPv4 and rewrite local endpoint env for portal / Auth CORS /
# VisionWorker browser WS / Android / S3. Re-run after changing Wi-Fi/network.
# Does not hardcode an IP in the repo.
#
# Usage (repo root):
#   .\scripts\pef_lan_endpoint.ps1
#   .\scripts\pef_lan_endpoint.ps1 -Ip 192.168.1.50
# Then recreate services that read .env:
#   docker compose -f docker-compose.integral.yml --env-file .env --profile gateway up -d --force-recreate auth web gateway evidence-worker
# Restart host VisionWorker (python main.py) so VISION_WS_ORIGINS is reloaded.

[CmdletBinding()]
param(
    [string]$Ip = "",
    [int]$WebPort = 5000,
    [int]$AuthPort = 5001,
    [int]$GatewayPort = 8080,
    [int]$S3Port = 3900,
    [int]$VisionPort = 5002,
    [int]$HttpsPort = 5443,
    [switch]$SkipAndroid
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

function Test-VirtualAdapter([string]$alias) {
    $a = $alias.ToLowerInvariant()
    return $a -match 'vmware|virtualbox|vbox|hyper-v|vethernet|docker|wsl|loopback|bluetooth|host-only|vethernet'
}

function Get-LanIPv4 {
    $rows = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and
            ($_.AddressState -eq "Preferred" -or -not $_.AddressState)
        } |
        ForEach-Object {
            $alias = (Get-NetIPInterface -InterfaceIndex $_.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).InterfaceAlias
            if (-not $alias) { $alias = $_.InterfaceAlias }
            [pscustomobject]@{
                IP = $_.IPAddress
                Alias = $alias
                Metric = $_.InterfaceMetric
                Virtual = Test-VirtualAdapter ([string]$alias)
                Wifi = ([string]$alias) -match '(?i)wi-?fi|wlan'
            }
        } |
        Where-Object { -not $_.Virtual })

    if (-not $rows) { throw "No LAN IPv4 found on a physical adapter. Pass -Ip explicitly." }

    $pick = $rows | Sort-Object @{ Expression = { -not $_.Wifi } }, Metric | Select-Object -First 1
    return $pick.IP
}

function Set-EnvKey([string]$Path, [string]$Key, [string]$Value) {
    if (-not (Test-Path $Path)) {
        throw "Missing $Path — copy .env.example to .env first."
    }
    $lines = Get-Content $Path -Encoding UTF8
    $found = $false
    $out = foreach ($line in $lines) {
        if ($line -match ("^\s*" + [regex]::Escape($Key) + "\s*=")) {
            $found = $true
            "$Key=$Value"
        } else {
            $line
        }
    }
    if (-not $found) { $out += "$Key=$Value" }
    # UTF-8 without BOM (Docker Compose --env-file rejects a leading BOM).
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllLines((Resolve-Path $Path), [string[]]$out, $utf8)
}

if (-not $Ip) { $Ip = Get-LanIPv4 }
if ($Ip -notmatch '^\d{1,3}(\.\d{1,3}){3}$') { throw "Invalid IPv4: $Ip" }

$portal = "http://${Ip}:${WebPort}"
$portalHttps = "https://${Ip}:${HttpsPort}"
$auth = "http://${Ip}:${AuthPort}"
# Relative /auth → same-origin through portal-lan HTTPS (no CORS; works on any LAN IP).
$authBrowser = "/auth"
$gateway = "http://${Ip}:${GatewayPort}"
$s3 = "http://${Ip}:${S3Port}"
# Browser WS goes through the LAN HTTPS front (wss) so live camera pages stay same-origin secure.
# Server-side HTTP to Vision stays loopback / host.docker.internal inside Compose.
$visionHttp = "http://${Ip}:${VisionPort}"
$visionWs = "wss://${Ip}:${HttpsPort}/ws"

$origins = @(
    "http://127.0.0.1:${WebPort}",
    "http://localhost:${WebPort}",
    $portal,
    $portalHttps,
    "http://127.0.0.1:${GatewayPort}",
    "http://localhost:${GatewayPort}",
    $gateway
) -join ","

$wsOrigins = @(
    "http://127.0.0.1:${WebPort}",
    "http://localhost:${WebPort}",
    $portal,
    $portalHttps
) -join ","

$envPath = Join-Path $Root ".env"
Set-EnvKey $envPath "HOST_LAN_IP" $Ip
Set-EnvKey $envPath "HOST_BIND" "0.0.0.0"
Set-EnvKey $envPath "PORTAL_HTTPS_PORT" "$HttpsPort"
Set-EnvKey $envPath "AUTH_ALLOWED_ORIGINS" $origins
# Cookie login + CSRF from the HTTPS LAN portal (secure context for getUserMedia).
Set-EnvKey $envPath "AUTH_BROWSER_URL" $authBrowser
Set-EnvKey $envPath "AUTH_SERVICE_URL" $auth
Set-EnvKey $envPath "AUTH_COOKIE_SECURE" "true"
Set-EnvKey $envPath "AUTH_COOKIE_SAMESITE" "Lax"
Set-EnvKey $envPath "AUTH_TRUSTED_PROXIES" "1"
Set-EnvKey $envPath "PORTAL_COOKIE_SECURE" "true"
Set-EnvKey $envPath "CSRF_TRUSTED_ORIGINS" $portalHttps
Set-EnvKey $envPath "VISION_WS_ORIGINS" $wsOrigins
Set-EnvKey $envPath "VISION_WORKER_URL" "http://127.0.0.1:${VisionPort}"
Set-EnvKey $envPath "VISION_WORKER_WS_URL" $visionWs
Set-EnvKey $envPath "VISION_WORKER_PORT" "$VisionPort"
Set-EnvKey $envPath "S3_PUBLIC_ENDPOINT" $s3
Set-EnvKey $envPath "S3_ENDPOINT" $s3

# VisionWorker local .env (host process) if present
$visionEnv = Join-Path $Root "apps\VisionWorker\.env"
if (Test-Path $visionEnv) {
    Set-EnvKey $visionEnv "AUTH_ALLOWED_ORIGINS" $origins
    Set-EnvKey $visionEnv "VISION_WS_ORIGINS" $wsOrigins
    Set-EnvKey $visionEnv "VISION_WORKER_WS_URL" $visionWs
    Set-EnvKey $visionEnv "VISION_WORKER_HOST" "0.0.0.0"
    Set-EnvKey $visionEnv "VISION_WORKER_PORT" "$VisionPort"
    Set-EnvKey $visionEnv "S3_PUBLIC_ENDPOINT" $s3
}

# AuthService local .env (kept in sync for host runs / docs; Compose reads root .env)
$authEnv = Join-Path $Root "apps\BackendAuthService\.env"
if (Test-Path $authEnv) {
    Set-EnvKey $authEnv "AUTH_ALLOWED_ORIGINS" $origins
    Set-EnvKey $authEnv "AUTH_COOKIE_SECURE" "true"
    Set-EnvKey $authEnv "AUTH_COOKIE_SAMESITE" "Lax"
    Set-EnvKey $authEnv "AUTH_TRUSTED_PROXIES" "1"
}

if (-not $SkipAndroid) {
    $localProps = Join-Path $Root "apps\PEF-Android\local.properties"
    if (-not (Test-Path $localProps)) {
        throw "Missing apps/PEF-Android/local.properties (sdk.dir required)."
    }
    $escaped = ($gateway.TrimEnd("/") + "/") -replace ":", "\:"
    $lp = Get-Content $localProps -Encoding UTF8
    $hasUrl = $false
    $hasRev = $false
    $lpOut = foreach ($line in $lp) {
        if ($line -match '^\s*pef\.apiBaseUrl\.debug\s*=') {
            $hasUrl = $true
            "pef.apiBaseUrl.debug=$escaped"
        } elseif ($line -match '^\s*pef\.adbReverse\s*=') {
            $hasRev = $true
            # Garage may still need reverse if AVD cannot reach host LAN firewall; 8080 optional with LAN URL
            "pef.adbReverse=3900"
        } else {
            $line
        }
    }
    if (-not $hasUrl) { $lpOut += "pef.apiBaseUrl.debug=$escaped" }
    if (-not $hasRev) { $lpOut += "pef.adbReverse=3900" }
    Set-Content -Path $localProps -Value $lpOut -Encoding UTF8
}

Write-Host ""
Write-Host "pef_lan_endpoint: HOST_LAN_IP=$Ip"
Write-Host "  Dirígete a (cámara): $portalHttps/"
Write-Host "  portal HTTP          $portal"
Write-Host "  auth browser         $authBrowser"
Write-Host "  gateway              $gateway/"
Write-Host "  vision WS            $visionWs"
Write-Host "  vision HTTP          $visionHttp"
Write-Host "  S3 public            $s3"
Write-Host ""
Write-Host "Next: .\scripts\pef_up.ps1   (cert + compose including lan-https)"
Write-Host "  Or: .\scripts\pef_lan_cert.ps1 then compose with -f docker-compose.lan-https.yml"
Write-Host "  Restart VisionWorker on the host (binds 0.0.0.0:$VisionPort)."
Write-Host "  Firewall: allow TCP $HttpsPort (portal HTTPS), $GatewayPort, $VisionPort."
Write-Host ""
