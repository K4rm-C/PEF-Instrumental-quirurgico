# Generate a lab self-signed TLS cert for the current LAN IP (camera secure context)
# and render infra/certs/lan/nginx.conf for docker-compose.lan-https.yml.
#
# Usage (repo root):
#   .\scripts\pef_lan_cert.ps1
#   .\scripts\pef_lan_cert.ps1 -Ip 192.168.68.71

[CmdletBinding()]
param(
    [string]$Ip = "",
    [int]$HttpsPort = 5443
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

function Get-LanIPv4 {
    $rows = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and
            ($_.AddressState -eq "Preferred" -or -not $_.AddressState)
        } |
        ForEach-Object {
            $alias = (Get-NetIPInterface -InterfaceIndex $_.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).InterfaceAlias
            if (-not $alias) { $alias = $_.InterfaceAlias }
            $a = ([string]$alias).ToLowerInvariant()
            [pscustomobject]@{
                IP = $_.IPAddress
                Alias = $alias
                Metric = $_.InterfaceMetric
                Virtual = $a -match 'vmware|virtualbox|vbox|hyper-v|vethernet|docker|wsl|loopback|bluetooth|host-only'
                Wifi = $a -match 'wi-?fi|wlan'
            }
        } |
        Where-Object { -not $_.Virtual })
    if (-not $rows) { throw "No LAN IPv4 found. Pass -Ip explicitly." }
    return ($rows | Sort-Object @{ Expression = { -not $_.Wifi } }, Metric | Select-Object -First 1).IP
}

if (-not $Ip) {
    $envIp = (Get-Content (Join-Path $Root ".env") -ErrorAction SilentlyContinue |
        Where-Object { $_ -match '^\s*HOST_LAN_IP\s*=' } |
        ForEach-Object { ($_ -split '=', 2)[1].Trim() } | Select-Object -First 1)
    $Ip = if ($envIp) { $envIp } else { Get-LanIPv4 }
}
if ($Ip -notmatch '^\d{1,3}(\.\d{1,3}){3}$') { throw "Invalid IPv4: $Ip" }

$certDir = Join-Path $Root "infra\certs\lan"
New-Item -ItemType Directory -Force -Path $certDir | Out-Null
$fullchain = Join-Path $certDir "fullchain.pem"
$privkey = Join-Path $certDir "privkey.pem"
$nginxOut = Join-Path $certDir "nginx.conf"
$tpl = Join-Path $Root "infra\lan-https\nginx.conf.template"

$openssl = $null
foreach ($c in @(
    "openssl",
    "$env:ProgramFiles\Git\usr\bin\openssl.exe",
    "${env:ProgramFiles(x86)}\Git\usr\bin\openssl.exe"
)) {
    if ($c -eq "openssl") {
        $cmd = Get-Command openssl -ErrorAction SilentlyContinue
        if ($cmd) { $openssl = $cmd.Source; break }
    } elseif (Test-Path $c) { $openssl = $c; break }
}

if ($openssl) {
    $cnf = Join-Path $env:TEMP "pef_lan_openssl.cnf"
    @"
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req

[dn]
CN = $Ip

[v3_req]
subjectAltName = @alt_names
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth

[alt_names]
IP.1 = $Ip
DNS.1 = localhost
IP.2 = 127.0.0.1
"@ | Set-Content -Path $cnf -Encoding ASCII
    # openssl prints progress dots to stderr; do not treat that as a terminating error.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $openssl req -x509 -newkey rsa:2048 -nodes `
        -keyout $privkey -out $fullchain -days 825 `
        -config $cnf -extensions v3_req 1>$null 2>$null
    $opensslExit = $LASTEXITCODE
    $ErrorActionPreference = $prevEap
    if ($opensslExit -ne 0 -or -not (Test-Path $fullchain) -or -not (Test-Path $privkey)) {
        throw "openssl failed to write $fullchain / $privkey (exit $opensslExit)"
    }
} else {
    # Fallback: .NET self-signed (Windows PowerShell 5+ / .NET)
    $cert = New-SelfSignedCertificate -DnsName $Ip, "localhost" -CertStoreLocation "Cert:\CurrentUser\My" `
        -KeyExportPolicy Exportable -NotAfter (Get-Date).AddYears(2) `
        -KeyAlgorithm RSA -KeyLength 2048 -HashAlgorithm SHA256 `
        -TextExtension @("2.5.29.17={text}IPAddress=$Ip&DNSName=localhost&IPAddress=127.0.0.1")
    $pwd = ConvertTo-SecureString -String "pef-lab" -Force -AsPlainText
    $pfx = Join-Path $env:TEMP "pef_lan.pfx"
    Export-PfxCertificate -Cert $cert -FilePath $pfx -Password $pwd | Out-Null
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { throw "Need openssl or python to export PEM from PFX." }
    python -c @"
from pathlib import Path
try:
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption, pkcs12
except ImportError:
    raise SystemExit('pip install cryptography')
data = Path(r'$pfx').read_bytes()
key, cert, _ = pkcs12.load_key_and_certificates(data, b'pef-lab')
Path(r'$privkey').write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()))
Path(r'$fullchain').write_bytes(cert.public_bytes(Encoding.PEM))
"@
    Remove-Item $pfx -Force -ErrorAction SilentlyContinue
    Remove-Item "Cert:\CurrentUser\My\$($cert.Thumbprint)" -Force -ErrorAction SilentlyContinue
}

$conf = (Get-Content $tpl -Raw -Encoding UTF8).
    Replace("__AUTH_UPSTREAM__", "auth:5001").
    Replace("__PORTAL_UPSTREAM__", "web:5000").
    Replace("__VISION_UPSTREAM__", "host.docker.internal:5002")
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($nginxOut, $conf, $utf8)

Write-Host "pef_lan_cert: IP=$Ip"
Write-Host "  cert     $fullchain"
Write-Host "  key      $privkey"
Write-Host "  nginx    $nginxOut"
Write-Host "  portal   https://${Ip}:${HttpsPort}/"
Write-Host "  (browser will warn once — accept the lab certificate to enable the camera)"
