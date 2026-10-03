# Smoke Auth (login + Redis JWT + verify + logout). Run from repo root with Auth on :5001 and Redis Up.
$ErrorActionPreference = "Stop"
$base = "http://127.0.0.1:5001"
$email = "operator@instrumed.com"
$password = "DemopwdOP78!"

Write-Host "== health =="
$health = Invoke-RestMethod -Uri "$base/health" -Method Get
if (-not $health.ok) { throw "Auth health failed: $($health | ConvertTo-Json -Compress)" }
Write-Host ($health | ConvertTo-Json -Compress)

Write-Host "== login =="
$login = Invoke-WebRequest -Uri "$base/login" -Method Post `
  -ContentType "application/json" `
  -Body (@{ email = $email; password = $password } | ConvertTo-Json) `
  -SessionVariable session
$loginBody = $login.Content | ConvertFrom-Json
if (-not $loginBody.user.ui_preferences.locale) { throw "login missing ui_preferences.locale" }
Write-Host "user=$($loginBody.user.email) locale=$($loginBody.user.ui_preferences.locale)"

$access = $session.Cookies.GetCookies($base) | Where-Object { $_.Name -eq "access_token" } | Select-Object -First 1
if (-not $access) { throw "access_token cookie missing" }

Write-Host "== redis jwt keys =="
docker exec pef_redis redis-cli -a MedRedis --no-auth-warning KEYS "auth:jwt:*"

Write-Host "== verify =="
$verify = Invoke-RestMethod -Uri "$base/verify" -Method Get -Headers @{ Authorization = "Bearer $($access.Value)" }
if (-not $verify.valid) { throw "verify failed" }
Write-Host "valid=$($verify.valid) roles=$($verify.user.roles.code -join ',')"

Write-Host "== logout =="
Invoke-RestMethod -Uri "$base/logout" -Method Post -Headers @{ Authorization = "Bearer $($access.Value)" } | Out-Null

Write-Host "== redis after logout (revoked or empty) =="
docker exec pef_redis redis-cli -a MedRedis --no-auth-warning KEYS "auth:jwt:*"

Write-Host "OK smoke_auth"
