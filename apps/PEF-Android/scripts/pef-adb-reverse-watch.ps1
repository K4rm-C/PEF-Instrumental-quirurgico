# Keeps `adb reverse` alive across emulator / device reconnects.
# Reads ports from ../local.properties (pef.adbReverse). Default: 8080,3900.
#
# Usage (leave open while developing):
#   powershell -ExecutionPolicy Bypass -File scripts\pef-adb-reverse-watch.ps1
#
# Ctrl+C to stop.

$ErrorActionPreference = "Continue"
$proj = Split-Path $PSScriptRoot -Parent
$localProps = Join-Path $proj "local.properties"
$sdkProp = $null
$ports = @("8080", "3900")
if (Test-Path $localProps) {
    Get-Content $localProps | ForEach-Object {
        if ($_ -match '^\s*sdk\.dir=(.+)\s*$') {
            $sdkProp = $Matches[1].Trim() -replace '\\\\', '\' -replace '\\:', ':'
        }
        if ($_ -match '^\s*pef\.adbReverse=(.+)\s*$') {
            $ports = @($Matches[1].Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^\d{1,5}$' })
        }
    }
}

$adb = $null
if ($sdkProp) {
    $candidate = Join-Path $sdkProp "platform-tools\adb.exe"
    if (Test-Path $candidate) { $adb = $candidate }
}
if (-not $adb) {
    $defaultAdb = Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe"
    if (Test-Path $defaultAdb) { $adb = $defaultAdb }
}
if (-not $adb) {
    Write-Error "adb.exe not found. Set sdk.dir in local.properties."
    exit 1
}

Write-Host "pef-adb-reverse-watch: adb=$adb ports=$($ports -join ',')"
Write-Host "Watching devices; Ctrl+C to stop."

$applied = @{}  # serial -> "port,port,..."

function Get-Devices {
    & $adb devices 2>$null |
        Select-Object -Skip 1 |
        ForEach-Object {
            $parts = ($_ -split '\s+')
            if ($parts.Count -ge 2 -and $parts[1] -eq 'device') { $parts[0] }
        }
}

function Ensure-Reverse([string]$serial) {
    $key = ($ports -join ",")
    $prev = $applied[$serial]
    # Always re-apply: reverse is cleared on emulator reboot even if serial stays the same.
    foreach ($port in $ports) {
        $out = & $adb -s $serial reverse "tcp:$port" "tcp:$port" 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0) {
            Write-Host "$(Get-Date -Format 'HH:mm:ss') reverse OK  $serial  tcp:$port"
        } else {
            Write-Warning "$(Get-Date -Format 'HH:mm:ss') reverse FAIL $serial tcp:$port  $($out.Trim())"
        }
    }
    $applied[$serial] = $key
}

try {
    while ($true) {
        $devices = @(Get-Devices)
        foreach ($serial in $devices) {
            Ensure-Reverse $serial
        }
        # Drop stale serials from cache when disconnected (next reconnect reapplies).
        foreach ($gone in @($applied.Keys | Where-Object { $_ -notin $devices })) {
            $applied.Remove($gone) | Out-Null
            Write-Host "$(Get-Date -Format 'HH:mm:ss') device gone  $gone"
        }
        Start-Sleep -Seconds 5
    }
} catch {
    if ($_.Exception -is [System.Management.Automation.PipelineStoppedException]) { exit 0 }
    throw
}
