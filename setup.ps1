[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LockFile = Join-Path $ProjectRoot "requirements-lock.txt"

Set-Location -LiteralPath $ProjectRoot

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $PythonCommand) {
        throw "Python was not found. Install Python 3.10 or newer and retry."
    }

    & $PythonCommand.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
    if ($LASTEXITCODE -ne 0) {
        throw "Python 3.10 or newer is required."
    }

    Write-Host "Creating .venv..." -ForegroundColor Cyan
    & $PythonCommand.Source -m venv (Join-Path $ProjectRoot ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to create .venv."
    }
}

Write-Host "Installing the validated dependency set..." -ForegroundColor Cyan
& $VenvPython -m pip install -r $LockFile
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed."
}

$EnvFile = Join-Path $ProjectRoot ".env"
if (-not (Test-Path -LiteralPath $EnvFile)) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot ".env.example") -Destination $EnvFile
    Write-Host "Created .env from .env.example. Add your API key locally if needed." -ForegroundColor Yellow
} else {
    Write-Host "Existing .env preserved." -ForegroundColor Green
}

& $VenvPython (Join-Path $ProjectRoot "tools\check_environment.py")
if ($LASTEXITCODE -ne 0) {
    throw "Environment validation failed."
}

Write-Host ""
Write-Host "Setup complete. Start the app with: .\start.ps1" -ForegroundColor Green
