Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Write-Step($Message) {
    Write-Host "[AutoTrade] $Message" -ForegroundColor Cyan
}

Write-Step "Checking Python"
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    throw "Python is not installed or not in PATH. Install Python 3.11+ from https://www.python.org/downloads/windows/ and rerun this script."
}

Write-Step "Creating virtual environment"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

Write-Step "Installing Python packages"
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt

Write-Step "Creating data and log directories"
New-Item -ItemType Directory -Force -Path data, logs | Out-Null

Write-Step "Writing local start scripts"
@'
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
& ".\.venv\Scripts\python.exe" -m app.main
'@ | Set-Content -Path "Start-AutoTrade-Engine.ps1" -Encoding UTF8

@'
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
& ".\.venv\Scripts\streamlit.exe" run app/ui/streamlit_app.py --server.port 8501 --server.address 0.0.0.0 --browser.gatherUsageStats false
'@ | Set-Content -Path "Start-AutoTrade-UI.ps1" -Encoding UTF8

Write-Step "Installation complete"
Write-Host ""
Write-Host "Start engine: powershell -ExecutionPolicy Bypass -File .\Start-AutoTrade-Engine.ps1"
Write-Host "Start UI:     powershell -ExecutionPolicy Bypass -File .\Start-AutoTrade-UI.ps1"
Write-Host "Open UI:      http://localhost:8501"
Write-Host ""
Write-Host "For 24-hour Windows operation, run these scripts through Task Scheduler or a Windows service wrapper."
