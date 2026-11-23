"""Windows side of SSH machines: PowerShell scripts sent over SSH, their quoting, and the
remote agent terminal. Everything runs through Windows PowerShell read from stdin, which
works whether the machine's SSH shell is cmd.exe (the OpenSSH default) or PowerShell."""

import base64
import json

from runtime import ROOT

# Works in cmd.exe and in PowerShell; a POSIX shell prints neither variable.
DETECT = "echo %OS% $env:OS"
POWERSHELL = "powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command -"
NODE_DIR = "agent-workbench-node"
QUOTES = "'‘’‚‛"
NO_PYTHON = (
    "Python 3.8 or newer was not found on this machine; install it from python.org "
    "(an install for your user is enough), then use Check connection again."
)


def is_windows(output):
    return "Windows_NT" in (output or "")


def quote(text):
    """A PowerShell single-quoted literal: every quote character PowerShell accepts is doubled."""
    text = str(text)
    if any(ord(c) < 32 for c in text):
        raise ValueError("Use a single-line value.")
    return "'" + "".join(c + c if c in QUOTES else c for c in text) + "'"


def _b64(text):
    return base64.b64encode(text.encode("utf8")).decode("ascii")


def feed(script):
    """One stdin line for POWERSHELL: the script travels base64-encoded, so no shell parses it."""
    return (
        "$s=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
        + _b64(script)
        + "'));& ([scriptblock]::Create($s))\n"
    ).encode("ascii")


def encoded(script):
    """Base64 UTF-16LE for powershell -EncodedCommand, which survives cmd.exe untouched."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def node_source():
    # The chat-template fix rides along so the node can correct a model's template itself.
    return (
        (ROOT / "chat_templates.py").read_text(encoding="utf8")
        + "\n\n"
        + (ROOT / "remote_node_win.py").read_text(encoding="utf8")
    )


# JavaScriptSerializer cannot write PowerShell's wrapped values, so JSON is written by hand.
# Non-ASCII characters are escaped because the console code page would garble them.
JSON = r"""
function ToJson($v) {
    if ($null -eq $v) { return 'null' }
    if ($v -is [bool]) { if ($v) { return 'true' } else { return 'false' } }
    if ($v -is [string] -or $v -is [char]) {
        $o = New-Object System.Text.StringBuilder
        [void]$o.Append('"')
        foreach ($c in ([string]$v).ToCharArray()) {
            $n = [int]$c
            if ($c -eq '"') { [void]$o.Append('\"') }
            elseif ($c -eq '\') { [void]$o.Append('\\') }
            elseif ($n -lt 32 -or $n -gt 126) { [void]$o.Append('\u' + $n.ToString('x4')) }
            else { [void]$o.Append($c) }
        }
        [void]$o.Append('"')
        return $o.ToString()
    }
    if ($v -is [int] -or $v -is [long] -or $v -is [decimal]) { return ([decimal]$v).ToString([Globalization.CultureInfo]::InvariantCulture) }
    if ($v -is [double] -or $v -is [single]) { return ([double]$v).ToString('R', [Globalization.CultureInfo]::InvariantCulture) }
    if ($v -is [System.Collections.IDictionary]) {
        $parts = foreach ($k in @($v.Keys)) { (ToJson ([string]$k)) + ':' + (ToJson $v[$k]) }
        return '{' + (@($parts) -join ',') + '}'
    }
    if ($v -is [System.Collections.IEnumerable]) {
        $parts = foreach ($x in $v) { ToJson $x }
        return '[' + (@($parts) -join ',') + ']'
    }
    return (ToJson ([string]$v))
}
"""

FIND_PYTHON = r"""
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
function Find-Python {
    foreach ($c in @(@('py', '-3'), @('python'), @('python3'))) {
        $cmd = Get-Command $c[0] -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -eq $cmd) { continue }
        $pre = @($c | Select-Object -Skip 1)
        try { $v = & $cmd.Source @pre -c 'import sys; print(sys.version.split()[0])' 2>$null } catch { continue }
        if ($LASTEXITCODE -eq 0 -and "$v" -match '^3\.(\d+)\.' -and [int]$Matches[1] -ge 8) {
            return @{ exe = $cmd.Source; pre = $pre; version = "$v".Trim() }
        }
    }
    return $null
}
function Run-Node($py, [string]$action, [string]$arg) {
    $OutputEncoding = New-Object System.Text.UTF8Encoding $false
    $src = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($NodeSource))
    if ($arg) { $src | & $py.exe @($py.pre) - $action $arg } else { $src | & $py.exe @($py.pre) - $action }
}
"""

NODE = r"""
$py = Find-Python
if ($null -eq $py) { [Console]::Error.WriteLine($NoPython); exit 3 }
Run-Node $py $Action $Arg
exit $LASTEXITCODE
"""

CHECK = r"""
Add-Type -AssemblyName System.Web.Extensions
$js = New-Object System.Web.Script.Serialization.JavaScriptSerializer
$js.MaxJsonLength = [int]::MaxValue
$info = [ordered]@{
    os = 'windows'; system = 'Windows'; release = ''; arch = ''; python = ''
    ram_gib = 0.0; gpu_gib = 0.0; gpu_vendor = ''; gpu_name = ''; driver = ''; unified = $false
    shell = 'powershell'; tmux = $false; curl = $false; omp = $false; firewall = ''
    managed = $false; reason = ''; installed = $false; running = $false; serving = $false
    port = 10542; model = ''; context = 0
}
try {
    $nt = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -ErrorAction Stop
    $build = [int]$nt.CurrentBuild
    $name = 'Windows 10'
    if ($build -ge 22000) { $name = 'Windows 11' }
    $info.system = $name
    $info.release = ([string]$nt.DisplayVersion + ' build ' + $build).Trim()
} catch {}
switch ($env:PROCESSOR_ARCHITECTURE) { 'AMD64' { $info.arch = 'x86_64' } 'ARM64' { $info.arch = 'arm64' } default { $info.arch = [string]$env:PROCESSOR_ARCHITECTURE } }
try { $info.ram_gib = [math]::Round((Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop).TotalPhysicalMemory / 1GB, 1) } catch {}
if (Get-Command pwsh -CommandType Application -ErrorAction SilentlyContinue) { $info.shell = 'pwsh' }
$info.curl = [bool](Get-Command curl.exe -CommandType Application -ErrorAction SilentlyContinue)
$info.omp = [bool](Get-Command omp -ErrorAction SilentlyContinue)
$smi = Get-Command nvidia-smi -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if ($smi) {
    $rows = & $smi.Source --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits 2>$null
    foreach ($row in @($rows)) {
        $p = "$row".Split(',') | ForEach-Object { $_.Trim() }
        if ($p.Count -eq 3 -and $p[1] -match '^\d+$' -and [double]$p[1] / 1024 -gt $info.gpu_gib) {
            $info.gpu_gib = [math]::Round([double]$p[1] / 1024, 1); $info.gpu_name = $p[0]; $info.driver = $p[2]; $info.gpu_vendor = 'nvidia'
        }
    }
}
if (-not $info.gpu_vendor) {
    try {
        foreach ($card in Get-CimInstance -ClassName Win32_VideoController -ErrorAction Stop) {
            $n = [string]$card.Name
            if ($n -match 'Radeon|AMD') { $info.gpu_vendor = 'amd'; $info.gpu_name = $n }
            elseif ($n -match 'Intel' -and -not $info.gpu_vendor) { $info.gpu_vendor = 'intel'; $info.gpu_name = $n }
        }
    } catch {}
}
$py = Find-Python
if ($null -eq $py) {
    $info.reason = $NoPython
} else {
    $info.python = $py.version
    $lines = @(Run-Node $py 'probe' '' | Where-Object { "$_".Trim() })
    if ($LASTEXITCODE -eq 0 -and $lines.Count) {
        try {
            $node = $js.DeserializeObject([string]$lines[-1])
            foreach ($k in @('gpu_gib', 'gpu_vendor', 'gpu_name', 'driver', 'managed', 'reason', 'installed', 'running', 'serving', 'port', 'model', 'model_file', 'context', 'omp', 'folder')) {
                if ($node.ContainsKey($k)) { $info[$k] = $node[$k] }
            }
        } catch { $info.reason = 'The Workbench node on this machine sent an unreadable reply.' }
    } else {
        $info.reason = 'The Workbench node could not run with Python ' + $py.version + ' on this machine.'
    }
}
[Console]::Out.WriteLine((ToJson $info))
"""


# The Windows form of remote_workbench.py: a port is a Workbench server when the process
# listening on it, or its parent, is the one in running.json of the data folder named on
# the listener's command line (--api-key-file or --config) or of the install script's
# default folder, for the same port. $Mode 'info' prints alias and context; 'key' the key.
WORKBENCH = r"""
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
function Find-Server([int]$port) {
    $owners = @{}; $folders = @()
    $pids = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | ForEach-Object { [int]$_.OwningProcess } | Select-Object -Unique)
    foreach ($p in $pids) {
        $proc = Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId = ' + $p) -ErrorAction SilentlyContinue
        if ($null -eq $proc) { continue }
        $owners[[int]$p] = $true
        if ($proc.ParentProcessId) { $owners[[int]$proc.ParentProcessId] = $true }
        foreach ($m in [regex]::Matches([string]$proc.CommandLine, '--(?:api-key-file|config)\s+(?:"([^"]+)"|(\S+))')) {
            $path = $m.Groups[1].Value
            if (-not $path) { $path = $m.Groups[2].Value }
            try { $folders += (Split-Path -Parent $path) } catch {}
        }
    }
    if ($owners.Count -eq 0) { return $null }
    if ($env:LOCALAPPDATA) { $folders += (Join-Path $env:LOCALAPPDATA 'Agent Workbench\data') }
    foreach ($folder in ($folders | Select-Object -Unique)) {
        $file = Join-Path $folder 'running.json'
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { continue }
        try { $rec = Get-Content -LiteralPath $file -Raw -Encoding UTF8 | ConvertFrom-Json } catch { continue }
        if ($null -eq $rec -or $null -eq $rec.pid -or $null -eq $rec.port) { continue }
        if ($owners.ContainsKey([int]$rec.pid) -and [int]$rec.port -eq $port) { return @{ folder = $folder; rec = $rec } }
    }
    return $null
}
$ports = @("$Ports".Split(',') | Where-Object { $_ -match '^\d{1,5}$' } | ForEach-Object { [int]$_ } | Select-Object -First 16)
if ($Mode -eq 'key') {
    $value = $null
    $found = $null
    if ($ports.Count) { $found = Find-Server $ports[0] }
    if ($found) {
        try { $text = (Get-Content -LiteralPath (Join-Path $found.folder 'api-key.txt') -Raw -ErrorAction Stop).Trim() } catch { $text = '' }
        if ($text -cmatch '^[A-Za-z0-9_-]{16,200}$') { $value = $text }
    }
    [Console]::Out.WriteLine((ToJson @{ key = $value }))
} else {
    $rows = @()
    foreach ($port in $ports) {
        $found = Find-Server $port
        if (-not $found) { continue }
        $size = $null
        if ($found.rec.spec -and $found.rec.spec.context -is [int]) { $size = [int]$found.rec.spec.context }
        $rows += , ([ordered]@{ port = $port; model = $Alias; context = $size; engine = [string]$found.rec.engine })
    }
    [Console]::Out.WriteLine((ToJson @{ servers = $rows }))
}
"""


def workbench(mode, ports):
    """stdin for POWERSHELL that finds the Workbench model servers on these ports (see WORKBENCH)."""
    if mode not in ("info", "key"):
        raise ValueError("Unknown Workbench server query.")
    return feed(
        _prelude(Mode=mode, Ports=",".join(str(int(p)) for p in ports), Alias="local-agent")
        + JSON
        + WORKBENCH
    )


def _prelude(**values):
    return "".join(f"${name} = {quote(value)}\n" for name, value in values.items())


def node(action, arg=""):
    """stdin for POWERSHELL that runs one remote_node_win.py action with the machine's Python."""
    return feed(
        _prelude(
            Action=action,
            Arg=arg,
            NoPython=NO_PYTHON,
            NodeSource=_b64(node_source()),
        )
        + FIND_PYTHON
        + NODE
    )


def check():
    """stdin for POWERSHELL that reports the system, Python, GPU and the managed node's state."""
    return feed(
        _prelude(NoPython=NO_PYTHON, NodeSource=_b64(node_source()))
        + JSON
        + FIND_PYTHON
        + CHECK
    )


def probe(table):
    """stdin for POWERSHELL that runs the loopback sweep (remote_probe.ps1) with this table."""
    script = (ROOT / "remote_probe.ps1").read_text(encoding="utf8")
    return feed(_prelude(Table=json.dumps(table)) + JSON + script)


def plan_arg(doc):
    return base64.b64encode(json.dumps(doc).encode("utf8")).decode("ascii")


def folder_expr(folder):
    """PowerShell expression for a remote folder: only a leading ~ means the home folder."""
    if folder == "~":
        return "$HOME"
    if folder.startswith(("~/", "~\\")):
        return "(Join-Path $HOME " + quote(folder[2:]) + ")"
    return quote(folder)


def terminal(shell, folder, harness, cli, args, managed=False):
    """The remote command for an interactive Windows session: PowerShell opens the folder and
    starts the agent CLI; the window stays at a prompt if either is missing. No tmux, so the
    session ends when the SSH connection closes."""
    say = "Write-Host -ForegroundColor Yellow "
    lines = ["$ErrorActionPreference = 'Continue'"]
    if managed:
        lines.append(
            "$env:PI_CODING_AGENT_DIR = Join-Path $env:LOCALAPPDATA "
            + quote(NODE_DIR + "\\omp-agent")
        )
    lines.append(
        "try { Set-Location -LiteralPath "
        + folder_expr(folder)
        + " -ErrorAction Stop } catch { "
        + say
        + quote(
            "Workbench could not open that folder on this machine; check the remote project folder."
        )
        + "; Set-Location -LiteralPath $HOME; return }"
    )
    if cli:
        lines.append(
            "if (-not (Get-Command -Name "
            + quote(cli)
            + " -ErrorAction SilentlyContinue)) { "
            + say
            + quote(
                f"{harness} ({cli}) was not found on this machine; install it here, then open the session again."
            )
            + "; return }"
        )
        lines.append("& " + " ".join(quote(a) for a in [cli, *args]) + "; exit $LASTEXITCODE")
    exe = "pwsh" if shell == "pwsh" else "powershell"
    return (
        exe
        + " -NoLogo -NoExit -ExecutionPolicy Bypass -EncodedCommand "
        + encoded("\n".join(lines))
    )


def pairing_script(public_key):
    """PowerShell that adds the Workbench key for this user; administrators use the shared file."""
    return "\n".join(
        [
            "$key = " + quote(public_key),
            "$groups = [Security.Principal.WindowsIdentity]::GetCurrent().Groups | ForEach-Object { $_.Value }",
            "if ($groups -contains 'S-1-5-32-544') { $file = Join-Path $env:ProgramData 'ssh\\administrators_authorized_keys' } else { $file = Join-Path $HOME '.ssh\\authorized_keys' }",
            "New-Item -ItemType Directory -Force -Path (Split-Path $file) | Out-Null",
            "$have = @(); if (Test-Path -LiteralPath $file) { $have = @(Get-Content -LiteralPath $file) }",
            "if ($have -notcontains $key) { Add-Content -LiteralPath $file -Value $key -Encoding ascii }",
            "Write-Host ('The Workbench key is in ' + $file)",
        ]
    )


def pairing_line(public_key):
    return "powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand " + encoded(
        pairing_script(public_key)
    )


def revoke_script(public_key):
    return "\n".join(
        [
            "$key = " + quote(public_key),
            "foreach ($file in @((Join-Path $HOME '.ssh\\authorized_keys'), (Join-Path $env:ProgramData 'ssh\\administrators_authorized_keys'))) {",
            "  if (-not (Test-Path -LiteralPath $file)) { continue }",
            "  $lines = @(Get-Content -LiteralPath $file)",
            "  $kept = @($lines | Where-Object { $_.Trim() -cne $key })",
            "  if ($kept.Count -ne $lines.Count) { try { Set-Content -LiteralPath $file -Value $kept -Encoding ascii -ErrorAction Stop } catch { [Console]::Error.WriteLine('Could not edit ' + $file + '; remove the Workbench key there yourself.'); exit 1 } }",
            "}",
        ]
    )
