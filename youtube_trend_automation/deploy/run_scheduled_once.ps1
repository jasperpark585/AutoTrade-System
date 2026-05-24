$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"
$LogPath = Join-Path $LogDir "windows-scheduled.log"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

if (-not (Test-Path $VenvPython)) {
    python -m venv (Join-Path $ProjectRoot ".venv")
}

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$timestamp] scheduled-run start" | Out-File -FilePath $LogPath -Encoding utf8 -Append

$ScriptPath = Join-Path $ProjectRoot "main.py"
$CommandLine = "`"$VenvPython`" `"$ScriptPath`" --mode scheduled-run"
$pythonOutput = & cmd.exe /d /c $CommandLine 2>&1
$exitCode = $LASTEXITCODE

if ($pythonOutput) {
    $pythonOutput | Out-File -FilePath $LogPath -Encoding utf8 -Append
    $pythonOutput
}

exit $exitCode
