# One-shot bring-up for a flexible LAN demo (no localhost required for live camera):
#   1) Detect Wi‑Fi/LAN IPv4 + rewrite .env (HTTPS portal origins, WSS, CORS)
#   2) Issue lab TLS cert for that IP
#   3) Start integral stack + Gateway + portal-lan HTTPS (:5443)
#
# Usage (repo root):
#   .\scripts\pef_up.ps1
#   .\scripts\pef_up.ps1 -Ip 192.168.1.50
#   .\scripts\pef_up.ps1 -SkipBuild

[CmdletBinding()]
param(
    [string]$Ip = "",
    [switch]$SkipBuild,
    [switch]$SkipAndroid
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

# Docker prints progress on stderr ("Container … Recreate"). With $ErrorActionPreference=Stop,
# PowerShell turns that into NativeCommandError and aborts even when exit code is 0.
function Invoke-DockerCompose {
    param([Parameter(Mandatory)][string[]]$DockerArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & docker @DockerArgs 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($code -ne 0) {
        throw "docker $($DockerArgs -join ' ') failed with exit $code"
    }
}

$lanArgs = @()
if ($Ip) { $lanArgs += @("-Ip", $Ip) }
if ($SkipAndroid) { $lanArgs += "-SkipAndroid" }
& (Join-Path $PSScriptRoot "pef_lan_endpoint.ps1") @lanArgs

$certArgs = @()
if ($Ip) { $certArgs += @("-Ip", $Ip) }
& (Join-Path $PSScriptRoot "pef_lan_cert.ps1") @certArgs

$composeFiles = @(
    "-f", "docker-compose.integral.yml",
    "-f", "docker-compose.lan-https.yml"
)

$upArgs = @("compose") + $composeFiles + @(
    "--env-file", ".env",
    "--profile", "gateway",
    "up", "-d"
)
if (-not $SkipBuild) { $upArgs += "--build" }
$upArgs += "--force-recreate"

Write-Host ""
Write-Host "pef_up: starting stack (gateway + lan-https)…"
Invoke-DockerCompose -DockerArgs $upArgs

Invoke-DockerCompose -DockerArgs (@("compose") + $composeFiles + @(
    "--env-file", ".env",
    "--profile", "gateway",
    "up", "-d", "--no-deps", "--force-recreate",
    "auth", "web", "gateway", "portal-lan"
))

$lan = (Get-Content .env -Encoding UTF8 | Where-Object { $_ -match '^\s*HOST_LAN_IP\s*=' } |
    ForEach-Object { ($_ -split '=', 2)[1].Trim() } | Select-Object -First 1)
if (-not $lan) { $lan = "127.0.0.1" }
$httpsPort = (Get-Content .env -Encoding UTF8 | Where-Object { $_ -match '^\s*PORTAL_HTTPS_PORT\s*=' } |
    ForEach-Object { ($_ -split '=', 2)[1].Trim() } | Select-Object -First 1)
if (-not $httpsPort) { $httpsPort = "5443" }

Write-Host ""
Write-Host "pef_up: ready"
Write-Host "  IP de esta red:     $lan"
Write-Host "  Dirígete al portal: https://${lan}:${httpsPort}/"
Write-Host "  Gateway (App):      http://${lan}:8080/"
Write-Host "  Acepta el certificado de lab una vez en el navegador; luego Iniciar cámara."
Write-Host "  Android debug descubre el Gateway en este Wi‑Fi (Reintentar en login si hace falta)."
Write-Host ""
