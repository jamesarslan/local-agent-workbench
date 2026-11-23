param([ValidateSet('web', 'vllm')][string]$Mode = 'web', [switch]$Update)
$ErrorActionPreference = 'Stop'
$dist = 'AgentWorkbench-Web'

function Stop-Setup([string]$Message) {
    Write-Output "workbench-setup: $Message"
    exit 1
}

function Confirm-Distro {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        Stop-Setup 'Windows Subsystem for Linux is missing. Run "wsl --install" in an administrator PowerShell, restart Windows, then retry.'
    }
    $first = ((wsl.exe --version) -replace [char]0, '' | Select-Object -First 1)
    if ($LASTEXITCODE -eq 0 -and "$first" -match '(\d+)\.(\d+)' -and [version]"$($Matches[1]).$($Matches[2])" -lt [version]'2.4') {
        Stop-Setup 'WSL is too old for this setup. Run "wsl --update" in PowerShell, then retry.'
    }
    $distros = ((wsl.exe --list --quiet) -replace [char]0, '') -join ' '
    if ($distros -notmatch [regex]::Escape($dist)) {
        wsl.exe --install --distribution Debian --name $dist --location (Join-Path $env:LOCALAPPDATA $dist) --no-launch
        if ($LASTEXITCODE) {
            Stop-Setup 'WSL could not create the Linux distribution. Run "wsl --update" in an administrator PowerShell (or "wsl --install" if WSL is not installed), restart Windows, then retry.'
        }
    }
}

function Install-Packages([string[]]$Packages, [string]$Failure) {
    wsl.exe -d $dist -u root --exec apt-get -qq update
    if (-not $LASTEXITCODE) {
        wsl.exe -d $dist -u root --exec env DEBIAN_FRONTEND=noninteractive apt-get -qy install $Packages
    }
    if ($LASTEXITCODE) { Stop-Setup $Failure }
}

if ($Mode -eq 'vllm') {
    Confirm-Distro
    Install-Packages @('python3', 'python3-venv', 'ca-certificates') 'Python could not be installed inside WSL. Restart Windows if WSL was just enabled, check your internet connection, then retry.'
    $extra = @()
    if ($Update) { $extra = @('--update') }
    wsl.exe -d $dist -u root --cd $PSScriptRoot --exec sh setup-vllm.sh $extra
    if ($LASTEXITCODE) { Stop-Setup 'The vLLM install inside WSL failed. Open the setup log for the installer error, then retry.' }
    exit 0
}

if (-not (Get-Command docker.exe -ErrorAction SilentlyContinue)) {
    Confirm-Distro
    Install-Packages @('docker.io', 'docker-compose', 'ca-certificates') 'Docker could not be installed inside WSL. Restart Windows if WSL was just enabled, then retry.'
}
& (Join-Path $PSScriptRoot 'Run.cmd') (Join-Path $PSScriptRoot 'cli.py') web-start
exit $LASTEXITCODE
