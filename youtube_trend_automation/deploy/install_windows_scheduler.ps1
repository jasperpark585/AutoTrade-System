param(
    [int]$Hours = 6
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$TaskName = "YouTubeTrendAutomationEvery6Hours"
$NormalizedHours = [Math]::Max(1, $Hours)
$StartTime = (Get-Date).AddMinutes(5).ToString("HH:mm")

$ExeRunner = Join-Path $ProjectRoot "YouTubeAutomationStudio.exe"
$DistExeRunner = Join-Path $ProjectRoot "dist\YouTubeAutomationStudio\YouTubeAutomationStudio.exe"
$PsRunner = Join-Path $ProjectRoot "deploy\run_scheduled_once.ps1"

if (Test-Path $ExeRunner) {
    $TaskCommand = "`"$ExeRunner`" --scheduled-run-once"
}
elseif (Test-Path $DistExeRunner) {
    $TaskCommand = "`"$DistExeRunner`" --scheduled-run-once"
}
else {
    $TaskCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$PsRunner`""
}

schtasks.exe /Delete /TN $TaskName /F 2>$null | Out-Null
schtasks.exe /Create /TN $TaskName /SC MINUTE /MO ($NormalizedHours * 60) /ST $StartTime /TR $TaskCommand /F | Out-Null

Write-Host "Installed Windows scheduled task: $TaskName"
Write-Host "Runs every $NormalizedHours hour(s) starting at $StartTime and skips automatically while Studio UI is open."
