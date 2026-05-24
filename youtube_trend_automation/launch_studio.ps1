$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPath = Join-Path $ProjectRoot ".venv"
$PythonExe = Join-Path $VenvPath "Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    python -m venv $VenvPath
}

& $PythonExe -m pip install --upgrade pip
& $PythonExe -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
& $PythonExe (Join-Path $ProjectRoot "studio_launcher.py")
