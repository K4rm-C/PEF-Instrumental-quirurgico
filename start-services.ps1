$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path "$Root\.env")) {
    Write-Host "Falta .env en la raiz. Copia .env.example a .env y continua." -ForegroundColor Red
    exit 1
}

Copy-Item "$Root\.env" "$Root\apps\BackendAuthService\.env" -Force
Copy-Item "$Root\.env" "$Root\apps\BackendWebFlask\.env" -Force

function Start-AppWindow {
    param([string]$Title, [string]$Directory, [string]$Command)
    $TempScript = [System.IO.Path]::GetTempFileName() + ".ps1"
    @"
`$Host.UI.RawUI.WindowTitle = '$Title'
Set-Location '$Directory'
Write-Host '$Title' -ForegroundColor Green
$Command
"@ | Out-File -FilePath $TempScript -Encoding UTF8
    Start-Process powershell.exe -ArgumentList "-NoExit", "-File", $TempScript
    Start-Sleep -Milliseconds 400
}

Write-Host "Comprobando Docker..." -ForegroundColor Yellow
docker info | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker no esta corriendo." -ForegroundColor Red
    exit 1
}

Write-Host "Levantando contenedores..." -ForegroundColor Yellow
docker compose up -d
if ($LASTEXITCODE -ne 0) {
    Write-Host "docker compose falto." -ForegroundColor Red
    exit 1
}

$AuthCmd = @"
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
"@

$WebCmd = @"
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
"@

$VisionCmd = @"
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
if (-not (Test-Path weights\best.pt)) {
  Write-Host 'Falta weights\best.pt (gitignored). Copialo a apps\VisionWorker\weights\best.pt' -ForegroundColor Red
}
python main.py
"@

Write-Host "Abriendo Auth (5001), Backend Web (5000) y VisionWorker (5002)..." -ForegroundColor Yellow
Start-AppWindow -Title "PEF Auth :5001" -Directory "$Root\apps\BackendAuthService" -Command $AuthCmd
Start-AppWindow -Title "PEF BackendWeb :5000" -Directory "$Root\apps\BackendWebFlask" -Command $WebCmd
Start-AppWindow -Title "PEF VisionWorker :5002" -Directory "$Root\apps\VisionWorker" -Command $VisionCmd

Write-Host "Listo. Web http://127.0.0.1:5000  Auth http://127.0.0.1:5001  Vision http://127.0.0.1:5002" -ForegroundColor Green
Write-Host "Garage S3 http://127.0.0.1:3900" -ForegroundColor Green
Write-Host "Pesos YOLO: apps\VisionWorker\weights\best.pt (no se suben a GitHub)" -ForegroundColor Cyan
