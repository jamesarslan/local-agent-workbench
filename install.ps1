<#
.SYNOPSIS
Install Agent Workbench for the current Windows user.

.DESCRIPTION
Fetches the pinned Windows x64 kit, checks its SHA-256 against the published SHA256SUMS, unpacks it under
%LOCALAPPDATA%\Agent Workbench, adds a workbench command and a Start menu entry, and puts the command folder
first in your user PATH. No step needs administrator rights.
Set WORKBENCH_INSTALL_DIR to use another folder and WORKBENCH_NO_MODIFY_PATH=1 to leave PATH alone.

.PARAMETER From
A folder that already holds agent-workbench-windows-x64.zip and SHA256SUMS. Nothing is downloaded.

.PARAMETER Uninstall
Remove the app, the command and the Start menu shortcut. Your data is kept.

.PARAMETER NoModifyPath
Do not add the command folder to your user PATH.
#>
param(
    [string]$From = $env:WORKBENCH_INSTALL_FROM,
    [switch]$Uninstall,
    [switch]$NoModifyPath
)

$Version = '0.5.0'
$Repository = 'jamesarslan/local-agent-workbench'

$CommandLines = @(
    '@echo off',
    'setlocal',
    'for %%I in ("%~dp0..") do set "base=%%~fI"',
    'if not defined WORKBENCH_DATA set "WORKBENCH_DATA=%base%\data"',
    'set "first=%~1"',
    'if not defined first goto desktop',
    'if /i "%~x1"==".py" goto direct',
    'if "%first%"=="-m" goto direct',
    'if "%first%"=="-c" goto direct',
    'call "%base%\app\Run.cmd" cli.py %*',
    'exit /b %errorlevel%',
    ':desktop',
    'call "%base%\app\Run.cmd" desktop_app.py',
    'exit /b %errorlevel%',
    ':direct',
    'call "%base%\app\Run.cmd" %*',
    'exit /b %errorlevel%'
)
$ShortcutLines = @(
    '@echo off',
    'call "%~dp0workbench.cmd"',
    'if errorlevel 1 pause'
)

function Write-WorkbenchBatch([string]$Path, [string[]]$Lines) {
    [System.IO.File]::WriteAllText($Path, (($Lines -join "`r`n") + "`r`n"), [System.Text.Encoding]::ASCII)
}

function Get-WorkbenchUserPath {
    $key = Get-Item -LiteralPath 'registry::HKEY_CURRENT_USER\Environment'
    return @($key.GetValue('Path', '', 'DoNotExpandEnvironmentNames') -split ';' | Where-Object { $_ -ne '' })
}

function Set-WorkbenchUserPath([string[]]$Folders) {
    Set-ItemProperty -Type ExpandString -LiteralPath 'registry::HKEY_CURRENT_USER\Environment' -Name Path -Value ($Folders -join ';')
    # Writing and removing a throwaway user variable makes Windows tell running programs that the environment changed.
    $signal = 'AgentWorkbench' + [guid]::NewGuid().ToString('N')
    [Environment]::SetEnvironmentVariable($signal, '1', 'User')
    [Environment]::SetEnvironmentVariable($signal, $null, 'User')
}

function Get-WorkbenchFolders {
    if ($env:OS -ne 'Windows_NT') { throw 'install.ps1 is for Windows. On macOS and Linux use install.sh.' }
    if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is not set, so there is no per-user folder to install into. Sign in as a regular user and run the installer again.' }
    $base = Join-Path $env:LOCALAPPDATA 'Agent Workbench'
    if ($env:WORKBENCH_INSTALL_DIR) { $base = $env:WORKBENCH_INSTALL_DIR }
    if (-not [System.IO.Path]::IsPathRooted($base)) { throw 'WORKBENCH_INSTALL_DIR must be an absolute path.' }
    $shortcut = ''
    $programs = [Environment]::GetFolderPath('Programs')
    if ($programs -and -not $env:WORKBENCH_NO_SHORTCUT) { $shortcut = Join-Path $programs 'Agent Workbench.lnk' }
    return @{
        Base     = $base
        App      = Join-Path $base 'app'
        Bin      = Join-Path $base 'bin'
        Data     = Join-Path $base 'data'
        Shortcut = $shortcut
    }
}

function Test-WorkbenchInstall([string]$Folder) {
    return (Test-Path -LiteralPath (Join-Path $Folder 'Run.cmd')) -and (Test-Path -LiteralPath (Join-Path $Folder 'desktop_app.py'))
}

function Test-WorkbenchHeld([string]$Stage, [string]$App) {
    $previous = Join-Path $Stage 'previous'
    if (Test-Path -LiteralPath (Join-Path $previous '.local')) { return $true }
    return (Test-Path -LiteralPath $previous) -and -not (Test-Path -LiteralPath $App)
}

function Uninstall-AgentWorkbench {
    $f = Get-WorkbenchFolders
    $found = $false
    if (Test-Path -LiteralPath $f.App) {
        if (-not (Test-WorkbenchInstall $f.App)) { throw "$($f.App) does not look like an Agent Workbench install, so it was left alone. Remove it yourself if it is no longer needed." }
        if (Test-Path -LiteralPath (Join-Path $f.App '.local')) { throw "$(Join-Path $f.App '.local') holds settings or models from running the app without the workbench command. Move that folder somewhere safe, then run the uninstall again." }
        $removed = Join-Path $f.Base '.uninstall'
        try {
            if (Test-Path -LiteralPath $removed) { Remove-Item -LiteralPath $removed -Recurse -Force }
            [System.IO.Directory]::Move($f.App, $removed)
        }
        catch { throw "$($f.App) is in use. Close every Agent Workbench window and run the uninstall again." }
        Remove-Item -LiteralPath $removed -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "Removed $($f.App)."
        $found = $true
    }
    foreach ($name in 'workbench.cmd', 'Agent Workbench.cmd') {
        $file = Join-Path $f.Bin $name
        if (Test-Path -LiteralPath $file) {
            Remove-Item -LiteralPath $file -Force
            $found = $true
        }
    }
    if (-not $found) { Write-Host "No Agent Workbench install was found in $($f.Base). If it was installed with WORKBENCH_INSTALL_DIR set, set it to the same folder and run the uninstall again." }
    if ((Test-Path -LiteralPath $f.Bin) -and -not (Get-ChildItem -LiteralPath $f.Bin -Force)) { Remove-Item -LiteralPath $f.Bin -Force }
    if ($f.Shortcut -and (Test-Path -LiteralPath $f.Shortcut)) { Remove-Item -LiteralPath $f.Shortcut -Force }
    try {
        $folders = @(Get-WorkbenchUserPath)
        if ($folders -contains $f.Bin) {
            Set-WorkbenchUserPath @($folders | Where-Object { $_ -ne $f.Bin })
            Write-Host "Removed $($f.Bin) from your user PATH."
        }
    }
    catch { Write-Host "Your user PATH could not be read. If it still lists $($f.Bin), remove that entry in Settings > System > About > Advanced system settings > Environment Variables." }
    Write-Host "Your settings, models and downloads are kept in: $($f.Data)"
    Write-Host "To delete them as well: Remove-Item -Recurse -Force '$($f.Data -replace "'", "''")'"
    Write-Host 'Saved API keys stay in Windows Credential Manager under the name Agent Workbench until you delete them there.'
}

function Install-AgentWorkbench {
    $f = Get-WorkbenchFolders
    $machine = $env:PROCESSOR_ARCHITEW6432
    if (-not $machine) { $machine = $env:PROCESSOR_ARCHITECTURE }
    if ($machine -ne 'AMD64') { throw "Agent Workbench has a Windows kit for 64-bit Intel and AMD processors, not for $machine. Download the source archive from https://github.com/$Repository/releases and run it with your own Python 3.12." }
    $archive = 'agent-workbench-windows-x64.zip'
    if ((Test-Path -LiteralPath $f.App) -and -not (Test-WorkbenchInstall $f.App)) { throw "$($f.App) exists and is not an Agent Workbench install. Move it away, or set WORKBENCH_INSTALL_DIR to another folder, and run the installer again." }
    try { New-Item -ItemType Directory -Force -Path $f.Base | Out-Null }
    catch { throw "$($f.Base) could not be created. Make its parent folder writable, or set WORKBENCH_INSTALL_DIR to another folder, and run the installer again." }
    $stage = Join-Path $f.Base '.install'
    if (Test-WorkbenchHeld $stage $f.App) { throw "$(Join-Path $stage 'previous') holds files that an earlier install did not put back. Move that folder somewhere safe and run the installer again." }
    try {
        if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
        New-Item -ItemType Directory -Path $stage | Out-Null
    }
    catch { throw "$stage could not be cleared for the new files. Delete that folder, check that $($f.Base) is writable, and run the installer again." }
    try {
        if ($From) {
            if (Test-Path -LiteralPath $From) { $From = (Resolve-Path -LiteralPath $From).ProviderPath }
            $sums = Join-Path $From 'SHA256SUMS'
            $pack = Join-Path $From $archive
            if (-not ((Test-Path -LiteralPath $sums) -and (Test-Path -LiteralPath $pack))) { throw "$From must contain SHA256SUMS and $archive. Point -From at the folder that holds the release files." }
        }
        else {
            $url = "https://github.com/$Repository/releases/download/v$Version"
            Write-Host "Downloading Agent Workbench $Version for Windows x64 from $url"
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            $sums = Join-Path $stage 'SHA256SUMS'
            $pack = Join-Path $stage $archive
            try {
                Invoke-WebRequest -UseBasicParsing -Uri "$url/SHA256SUMS" -OutFile $sums
                Invoke-WebRequest -UseBasicParsing -Uri "$url/$archive" -OutFile $pack
            }
            catch { throw "Could not download the release files from $url. Check the connection to github.com and run the installer again." }
        }
        $pattern = '^[0-9a-f]{64} [ *]' + [regex]::Escape($archive) + '$'
        $entry = @(Get-Content -LiteralPath $sums | Where-Object { $_ -cmatch $pattern })
        if ($entry.Count -eq 0) { throw "SHA256SUMS has no usable entry for $archive, so nothing was installed. Download the release files again." }
        $expected = $entry[0].Substring(0, 64)
        $actual = (Get-FileHash -LiteralPath $pack -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) { throw "$archive does not match its published SHA-256, so nothing was installed. Download it again; if it fails twice, report it at https://github.com/$Repository/issues" }
        Write-Host "SHA-256 verified: $expected"
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        try { [System.IO.Compression.ZipFile]::ExtractToDirectory($pack, $stage) }
        catch { throw "$archive could not be unpacked into $($f.Base). Check free disk space and that the folder is writable, then run the installer again." }
        $fresh = Join-Path $stage 'Agent-Workbench'
        if (-not ((Test-WorkbenchInstall $fresh) -and (Test-Path -LiteralPath (Join-Path $fresh 'requirements.lock')) -and (Test-Path -LiteralPath (Join-Path $fresh 'python\python.exe')))) { throw "$archive is not a complete Agent Workbench kit, so nothing was installed. Download the release files again." }
        if (Test-Path -LiteralPath $f.App) {
            $previous = Join-Path $stage 'previous'
            try { [System.IO.Directory]::Move($f.App, $previous) }
            catch { throw "The previous version in $($f.App) is in use, so it was kept. Close every Agent Workbench window and run the installer again." }
            try { [System.IO.Directory]::Move($fresh, $f.App) }
            catch {
                try { [System.IO.Directory]::Move($previous, $f.App) }
                catch { throw "The new version could not be installed and the previous one could not be put back. It is in $previous; move it to $($f.App) yourself." }
                throw "The new version could not be moved into $($f.App), so the previous version was kept. Check that the folder is writable and run the installer again."
            }
            $kept = Join-Path $previous '.local'
            if (Test-Path -LiteralPath $kept) {
                try { [System.IO.Directory]::Move($kept, (Join-Path $f.App '.local')) }
                catch { throw "The new version is installed, but the data folder of the previous one could not be moved back. It is in $kept; move it to $(Join-Path $f.App '.local') yourself." }
            }
            Write-Host "Replaced the previous version in $($f.App)."
        }
        else {
            try { [System.IO.Directory]::Move($fresh, $f.App) }
            catch { throw "The new version could not be moved into $($f.App), so nothing was installed. Check that $($f.Base) is writable and run the installer again." }
        }
    }
    finally {
        if ((Test-Path -LiteralPath $stage) -and -not (Test-WorkbenchHeld $stage $f.App)) { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue }
    }
    New-Item -ItemType Directory -Force -Path $f.Data, $f.Bin | Out-Null
    Write-WorkbenchBatch (Join-Path $f.Bin 'workbench.cmd') $CommandLines
    Write-WorkbenchBatch (Join-Path $f.Bin 'Agent Workbench.cmd') $ShortcutLines
    $menu = ''
    if ($f.Shortcut) {
        try {
            $link = (New-Object -ComObject WScript.Shell).CreateShortcut($f.Shortcut)
            $link.TargetPath = Join-Path $f.Bin 'Agent Workbench.cmd'
            $link.WorkingDirectory = $env:USERPROFILE
            $link.WindowStyle = 7
            $link.Description = 'Agent Workbench'
            $link.Save()
            $menu = ' or from the Start menu (Agent Workbench)'
        }
        catch { Write-Host 'The Start menu shortcut could not be created. Start Agent Workbench with the workbench command instead.' }
    }
    Write-Host "Agent Workbench $Version is installed."
    Write-Host "  App:      $($f.App)"
    Write-Host "  Data:     $($f.Data)"
    Write-Host "  Command:  $(Join-Path $f.Bin 'workbench.cmd')"
    if ($NoModifyPath -or $env:WORKBENCH_NO_MODIFY_PATH) {
        Write-Host "PATH was left alone. Start it with: `"$(Join-Path $f.Bin 'workbench.cmd')`""
    }
    else {
        try {
            $folders = @(Get-WorkbenchUserPath)
            if ($folders -notcontains $f.Bin) {
                Set-WorkbenchUserPath (@($f.Bin) + $folders)
                Write-Host "Added $($f.Bin) to your user PATH. Open a new terminal to use the workbench command."
            }
        }
        catch { Write-Host "Your user PATH could not be changed. Start it with: `"$(Join-Path $f.Bin 'workbench.cmd')`"" }
    }
    Write-Host "Start it with the workbench command$menu. The first start downloads Python packages from pypi.org and the desktop runtime from github.com."
    $remove = "powershell -ExecutionPolicy Bypass -File `"$(Join-Path $f.App 'install.ps1')`" -Uninstall"
    if ($env:WORKBENCH_INSTALL_DIR) { Write-Host "To uninstall, keep WORKBENCH_INSTALL_DIR set to $($f.Base) and run: $remove" }
    else { Write-Host "To uninstall: $remove" }
}

$previousErrorAction = $ErrorActionPreference
$previousProgress = $ProgressPreference
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$failed = $false
try {
    if ($PSBoundParameters.ContainsKey('From') -and -not $From) { throw '-From needs a folder. Example: powershell -ExecutionPolicy Bypass -File install.ps1 -From .\dist' }
    if ($Uninstall) { Uninstall-AgentWorkbench } else { Install-AgentWorkbench }
}
catch {
    $failed = $true
    Write-Host $_.Exception.Message -ForegroundColor Red
}
finally {
    $ErrorActionPreference = $previousErrorAction
    $ProgressPreference = $previousProgress
}
if ($failed -and $PSCommandPath) { exit 1 }
