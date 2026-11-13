[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8501,
    [string]$Address = "127.0.0.1",
    [switch]$Headless
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

Set-Location -LiteralPath $ProjectRoot

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Local environment is missing. Run .\setup.ps1 first."
}

& $VenvPython (Join-Path $ProjectRoot "tools\check_environment.py")
if ($LASTEXITCODE -ne 0) {
    throw "Environment validation failed. Run .\setup.ps1, then retry."
}

$StreamlitArgs = @(
    "-m", "streamlit", "run", "app\ui\streamlit_app.py",
    "--server.address", $Address,
    "--server.port", $Port,
    "--browser.gatherUsageStats", "false"
)
if ($Headless) {
    $StreamlitArgs += @("--server.headless", "true")
}

Write-Host "Starting SweaterCo at http://${Address}:$Port" -ForegroundColor Green
& $VenvPython @StreamlitArgs
exit $LASTEXITCODE
