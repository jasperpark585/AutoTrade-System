$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$PythonExe = Join-Path $ProjectRoot ".venv\\Scripts\\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python"
}

Write-Host "[1/5] Installing PyInstaller if needed..."
& $PythonExe -m pip install pyinstaller

Write-Host "[2/5] Ensuring cloudflared bundle..."
$CloudflareDir = Join-Path $ProjectRoot "vendor\cloudflared"
$CloudflareExe = Join-Path $CloudflareDir "cloudflared.exe"
if (-not (Test-Path $CloudflareDir)) {
    New-Item -ItemType Directory -Path $CloudflareDir | Out-Null
}
if (-not (Test-Path $CloudflareExe)) {
    Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile $CloudflareExe
}

Write-Host "[3/5] Cleaning old build artifacts..."
Get-Process -Name "YouTubeAutomationStudio" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-Process -Name "cloudflared" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Remove-Item -LiteralPath (Join-Path $ProjectRoot "data\\runtime\\studio_session.json") -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $ProjectRoot "data\\runtime\\studio_access.json") -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $ProjectRoot "data\\runtime\\studio_access.txt") -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $ProjectRoot "dist\\YouTubeAutomationStudio\\StudioAccess.json") -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $ProjectRoot "dist\\YouTubeAutomationStudio\\StudioAccess.txt") -Force -ErrorAction SilentlyContinue
if (Test-Path (Join-Path $ProjectRoot "build")) {
    Remove-Item -LiteralPath (Join-Path $ProjectRoot "build") -Recurse -Force
}
if (Test-Path (Join-Path $ProjectRoot "dist\\YouTubeAutomationStudio")) {
    Remove-Item -LiteralPath (Join-Path $ProjectRoot "dist\\YouTubeAutomationStudio") -Recurse -Force
}

Write-Host "[4/5] Building EXE..."
& $PythonExe -m PyInstaller --noconfirm .\studio_launcher.spec

Write-Host "[5/5] Build complete."
Write-Host "Executable folder: $ProjectRoot\\dist\\YouTubeAutomationStudio"
Write-Host "Executable file:   $ProjectRoot\\dist\\YouTubeAutomationStudio\\YouTubeAutomationStudio.exe"
