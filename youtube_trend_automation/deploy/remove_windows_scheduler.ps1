$ErrorActionPreference = "Stop"

$TaskName = "YouTubeTrendAutomationEvery6Hours"

schtasks.exe /Delete /TN $TaskName /F | Out-Null

Write-Host "Removed Windows scheduled task: $TaskName"
