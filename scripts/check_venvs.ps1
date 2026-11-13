# Verify each app venv can import its entrypoint.
# Usage (repo root):  .\scripts\check_venvs.ps1

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot

$checks = @(
    @{
        Name = 'BackendAuthService'
        Rel = 'apps\BackendAuthService'
        Code = @"
import main
rules = [r.rule for r in main.app.url_map.iter_rules()]
assert '/login' in rules
assert any(r == '/api/v1/auth/login' for r in rules)
print('auth ok')
"@
    },
    @{
        Name = 'BackendWebFlask'
        Rel = 'apps\BackendWebFlask'
        Code = @"
from app import create_app
app = create_app()
rules = [r.rule for r in app.url_map.iter_rules()]
assert any('evidence-clips' in r for r in rules)
assert any(r == '/supervisor/reports' for r in rules)
assert any(r == '/health' for r in rules)
print('web ok')
"@
    },
    @{ Name = 'VisionWorker'; Rel = 'apps\VisionWorker'; Code = "import config`nprint('vision ok')" },
    @{ Name = 'EvidenceWorker'; Rel = 'apps\EvidenceWorker'; Code = "import config`nprint('evidence worker ok')" },
    @{ Name = 'CatalogService'; Rel = 'apps\CatalogService'; SharedMs = $true; Code = "import main`nprint('catalog ok')" },
    @{ Name = 'SessionService'; Rel = 'apps\SessionService'; SharedMs = $true; Code = "import main`nprint('session ok')" },
    @{ Name = 'EvidenceService'; Rel = 'apps\EvidenceService'; SharedMs = $true; Code = "import main`nprint('evidence service ok')" }
)

$sharedMsPy = Join-Path $root 'apps\.venv-ms\Scripts\python.exe'
$fail = 0
foreach ($app in $checks) {
    $dir = Join-Path $root $app.Rel
    if ($app.SharedMs) {
        $py = $sharedMsPy
    } else {
        $py = Join-Path $dir '.venv\Scripts\python.exe'
    }
    Write-Host "=== $($app.Name) ==="
    if (-not (Test-Path $py)) {
        if ($app.SharedMs) {
            Write-Host "SKIP missing apps\.venv-ms — python -m venv apps\.venv-ms && pip install -r apps\requirements-ms.txt"
        } else {
            Write-Host "SKIP no venv — python -m venv .venv && pip install -r requirements.txt"
        }
        continue
    }
    $tmp = Join-Path $dir '_pef_venv_check_tmp.py'
    Set-Content -Path $tmp -Value $app.Code -Encoding UTF8
    try {
        & $py $tmp
        if ($LASTEXITCODE -ne 0) { $fail++; Write-Host "FAIL" } else { Write-Host "OK" }
    } catch {
        $fail++
        Write-Host "FAIL $($_.Exception.Message)"
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
}
exit $fail
