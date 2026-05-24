param(
    [string]$PythonExe = "python",
    [switch]$SkipUpload,
    [string]$ServerHost = "",
    [string]$ServerUser = "ubuntu",
    [string]$ServerPath = "/opt/youtube-trend-automation",
    [string]$KeyPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPath = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvPath "Scripts\\python.exe"
$BundleName = "deploy_bundle_{0}.tar.gz" -f (Get-Date -Format "yyyyMMdd_HHmmss")
$BundlePath = Join-Path $ProjectRoot $BundleName
$EnvPath = Join-Path $ProjectRoot ".env"

if (Test-Path $EnvPath) {
    Get-Content $EnvPath | ForEach-Object {
        if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
        $pair = $_ -split '=', 2
        $name = $pair[0].Trim()
        $value = $pair[1].Trim()
        if ($name -notin @("SSH_DEPLOY_HOST", "SSH_DEPLOY_USER", "SSH_DEPLOY_PATH", "SSH_KEY_FILE")) { return }
        if (-not (Get-Item -Path "Env:$name" -ErrorAction SilentlyContinue)) {
            Set-Item -Path "Env:$name" -Value $value
        }
    }
}

if ([string]::IsNullOrWhiteSpace($ServerHost) -and $env:SSH_DEPLOY_HOST) { $ServerHost = $env:SSH_DEPLOY_HOST }
if (($ServerUser -eq "ubuntu") -and $env:SSH_DEPLOY_USER) { $ServerUser = $env:SSH_DEPLOY_USER }
if (($ServerPath -eq "/opt/youtube-trend-automation") -and $env:SSH_DEPLOY_PATH) { $ServerPath = $env:SSH_DEPLOY_PATH }
if ([string]::IsNullOrWhiteSpace($KeyPath) -and $env:SSH_KEY_FILE) { $KeyPath = $env:SSH_KEY_FILE }

if (-not (Test-Path $VenvPython)) {
    & $PythonExe -m venv $VenvPath
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
& $VenvPython -m compileall (Join-Path $ProjectRoot "app") (Join-Path $ProjectRoot "tests") (Join-Path $ProjectRoot "main.py") (Join-Path $ProjectRoot "studio_app.py") (Join-Path $ProjectRoot "studio_launcher.py")
& $VenvPython -m pytest (Join-Path $ProjectRoot "tests")
& $VenvPython (Join-Path $ProjectRoot "main.py") --mode dry-run --allow-network false
& $VenvPython (Join-Path $ProjectRoot "main.py") --mode run-once --allow-network false --skip-upload --force

if ($SkipUpload -or [string]::IsNullOrWhiteSpace($ServerHost)) {
    Write-Host "Local verification completed. Upload skipped."
    exit 0
}

$sshArgs = @()
if (-not [string]::IsNullOrWhiteSpace($KeyPath)) {
    $sshArgs += @("-i", $KeyPath)
}

tar.exe `
    --exclude=".venv" `
    --exclude="outputs" `
    --exclude="logs" `
    --exclude=".pytest_cache" `
    --exclude="__pycache__" `
    --exclude="build" `
    --exclude="dist" `
    --exclude="vendor" `
    --exclude="ffmpeg-8.1.tar" `
    --exclude="$BundleName" `
    -czf $BundlePath -C $ProjectRoot .
& scp @sshArgs $BundlePath "$ServerUser@$ServerHost`:~/"
$remoteOwner = "${ServerUser}:${ServerUser}"
& ssh @sshArgs "$ServerUser@$ServerHost" "sudo mkdir -p $ServerPath && sudo chown -R $remoteOwner $ServerPath && tar -xzf ~/$BundleName -C $ServerPath && rm -f ~/$BundleName && bash $ServerPath/deploy/deploy.sh"
Remove-Item $BundlePath -ErrorAction SilentlyContinue
