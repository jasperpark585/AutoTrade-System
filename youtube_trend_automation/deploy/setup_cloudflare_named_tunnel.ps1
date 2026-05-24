param(
    [Parameter(Mandatory = $true)]
    [string]$Hostname,

    [string]$TunnelName = "youtube-automation-studio"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Cloudflared = Join-Path $ProjectRoot "vendor\cloudflared\cloudflared.exe"
$SettingsPath = Join-Path $ProjectRoot "data\studio_settings.json"
$Origincert = Join-Path $env:USERPROFILE ".cloudflared\cert.pem"

if (-not (Test-Path $Cloudflared)) {
    throw "cloudflared.exe not found: $Cloudflared"
}

Write-Host "[1/4] Checking Cloudflare login..."
if (-not (Test-Path $Origincert)) {
    Write-Host "Cloudflare login is required. A browser window will open."
    & $Cloudflared tunnel login
}

Write-Host "[2/4] Ensuring named tunnel exists..."
& $Cloudflared tunnel info $TunnelName *> $null
if ($LASTEXITCODE -ne 0) {
    & $Cloudflared tunnel create $TunnelName
}

Write-Host "[3/4] Routing DNS hostname..."
& $Cloudflared tunnel route dns $TunnelName $Hostname

Write-Host "[4/4] Fetching tunnel token and updating Studio settings..."
$Token = (& $Cloudflared tunnel token $TunnelName | Out-String).Trim()
if (-not $Token) {
    throw "Failed to read tunnel token."
}

if (-not (Test-Path $SettingsPath)) {
    throw "Studio settings file not found: $SettingsPath"
}

$Settings = Get-Content $SettingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $Settings.remote_access) {
    $Settings | Add-Member -NotePropertyName remote_access -NotePropertyValue ([pscustomobject]@{})
}
$Settings.remote_access.enabled = $true
$Settings.remote_access.mode = "named"
$Settings.remote_access.tunnel_name = $TunnelName
$Settings.remote_access.hostname = $Hostname
$Settings.remote_access.tunnel_token = $Token

$Settings | ConvertTo-Json -Depth 10 | Set-Content -Path $SettingsPath -Encoding UTF8

$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python"
}

Write-Host ""
Write-Host "[5/5] Syncing updated settings to the server..."
$SyncCode = @"
from pathlib import Path
from app.runtime.server_sync import sync_server_settings
result = sync_server_settings(Path(r'''$ProjectRoot'''))
print(result)
"@
Push-Location $ProjectRoot
try {
    $SyncCode | & $PythonExe -
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "Named tunnel is ready."
Write-Host "Hostname : https://$Hostname"
Write-Host "Tunnel    : $TunnelName"
Write-Host ""
Write-Host "Server Studio will switch to the fixed hostname after the sync completes."
