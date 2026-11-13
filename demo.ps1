[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8501,
    [switch]$Headless
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

Set-Location -LiteralPath $ProjectRoot

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Local environment is missing. Run .\setup.cmd first."
}

$env:SWEATERCO_LOCAL_REVIEW = "1"
$env:PROACTIVE_DB_PATH = Join-Path $ProjectRoot "runtime_data\sprint3-demo.sqlite3"

Write-Host "SWEATERCO SPRINT 3 LOGIN DEMO" -ForegroundColor Yellow
Write-Host "Local-only URL: http://127.0.0.1:$Port" -ForegroundColor Yellow
Write-Host "Accounts: preview_alice / preview_bob / Xintong" -ForegroundColor Yellow
Write-Host "Password: sweaterco" -ForegroundColor Yellow
Write-Host "Account and chat data reset when this server stops." -ForegroundColor Yellow
Write-Host "Proactive task storage: $env:PROACTIVE_DB_PATH" -ForegroundColor Yellow
Write-Host ""

& $VenvPython (Join-Path $ProjectRoot "tools\check_environment.py")
if ($LASTEXITCODE -ne 0) {
    throw "Environment validation failed. Run .\setup.cmd, then retry."
}

$StreamlitArgs = @(
    "-m", "streamlit", "run", "tools\review_accounts_voice.py",
    "--server.address", "127.0.0.1",
    "--server.port", $Port,
    "--browser.gatherUsageStats", "false"
)
if ($Headless) {
    $StreamlitArgs += @("--server.headless", "true")
}

& $VenvPython @StreamlitArgs
exit $LASTEXITCODE
