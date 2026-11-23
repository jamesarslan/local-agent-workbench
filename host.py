"""OS glue: open folders, resolve CLIs, open a native terminal window, identify processes."""

import hashlib
import json
import os
import re
import shlex
import socket
import tempfile
import shutil
import subprocess
import sys
import time
from pathlib import Path

WINDOWS = os.name == "nt"
HARNESS_CLIS = ("omp", "pi", "claude", "codex", "hermes", "opencode")
BATCH = (".cmd", ".bat")
UNSAFE = "&|<>^%!\r\n\x00"
SHIM_TARGET = re.compile(r'"%(?:~dp0|dp0%)\\([^"\r\n]+)"')
SHIM_NODE = re.compile(r'@?(?:node(?:\.exe)?|"%(?:~dp0|dp0%)\\node\.exe")', re.I)
SHIM_PROG = re.compile(r'^\s*@?SET "_prog=node(?:\.exe)?"\s*$', re.I | re.M)
SHIM_FLAG = re.compile(r"--?[A-Za-z][A-Za-z0-9=_.,:/-]*")
EMULATORS = [
    ("x-terminal-emulator", ["-e"]),
    ("gnome-terminal", ["--"]),
    ("konsole", ["-e"]),
    ("xterm", ["-e"]),
]
MAC_WAIT = 120
MAC_SCRIPT = 'on run args\n tell application "Terminal"\n  do script (item 1 of args)\n  activate\n end tell\nend run'


def open_folder(path):
    if os.name == "nt":
        os.startfile(path)
    else:
        subprocess.Popen(
            ["open" if sys.platform == "darwin" else "xdg-open", str(path)],
            stdin=subprocess.DEVNULL,
        )


def blender_executable():
    exe = shutil.which("blender")
    if exe:
        return exe
    if sys.platform == "darwin":
        candidates = [
            base / "Blender.app/Contents/MacOS/Blender"
            for base in (Path("/Applications"), Path.home() / "Applications")
        ]
    else:
        candidates = sorted(
            Path(os.getenv("ProgramFiles", "/opt")).glob(
                "Blender Foundation/Blender */blender.exe"
            ),
            reverse=True,
        )
    return next((str(p) for p in candidates if p.is_file()), None)


def shim_flags(before, text):
    """Node's own options written before a shim's script; None when the shim runs something other than node."""
    words, flags = before.split(), []
    while words and SHIM_FLAG.fullmatch(words[-1]):
        flags.insert(0, words.pop())
    runner = words[-1] if words else ""
    if runner == '"%_prog%"':
        return flags if SHIM_PROG.search(text) else None
    return flags if SHIM_NODE.fullmatch(runner) else None


def resolve_shim(path):
    """The command an npm .cmd shim would run, so cmd.exe never sees the arguments; None when it is not one."""
    try:
        with open(path, encoding="utf8", errors="replace") as stream:
            text = stream.read(20000)
    except OSError:
        return None
    line = next((row for row in reversed(text.splitlines()) if "%*" in row), "")
    found = list(SHIM_TARGET.finditer(line))
    if not found:
        return None
    # npm writes shims that point into their own node_modules; anything else stays behind cmd.exe.
    modules = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(path)), "node_modules"))
    target = Path(os.path.normpath(os.path.join(modules, "..", *found[-1].group(1).split("\\"))))
    if os.path.commonpath([os.path.normcase(modules), os.path.normcase(str(target))]) != os.path.normcase(modules):
        return None
    if not target.is_file():
        return None
    suffix = target.suffix.lower()
    if suffix == ".exe":
        return [str(target)]
    if suffix in (".js", ".cjs", ".mjs"):
        flags = shim_flags(line[: found[-1].start()], text)
        local = Path(path).with_name("node.exe")
        node = str(local) if local.is_file() else shutil.which("node")
        return [node, *flags, str(target)] if node and flags is not None else None
    return None


def command(name):
    path = shutil.which(name)
    if not path:
        raise ValueError(f"{name} is missing. Open Setup to install it.")
    if WINDOWS and name == "pi" and Path(path).with_name("pi-launcher.js").exists():
        return [
            shutil.which("node") or "node",
            str(Path(path).with_name("pi-launcher.js")),
        ]
    if WINDOWS and name in HARNESS_CLIS and path.lower().endswith(BATCH):
        return resolve_shim(path) or [path]
    return [path]


def safe_argv(argv):
    """Refuse arguments cmd.exe would re-parse when the program is a batch file. Returns argv unchanged."""
    if WINDOWS and argv:
        first = str(argv[0])
        if not Path(first).suffix:
            first = shutil.which(first) or first
        if first.lower().endswith(BATCH) and any(
            c in UNSAFE for a in argv[1:] for c in str(a)
        ):
            raise ValueError(
                f"{Path(first).name} is a Windows batch launcher, and this launch has a model name, prompt or path "
                "containing & | < > ^ % ! or a line break; reinstall the agent from Setup or remove those characters."
            )
    return argv


def can_open_window():
    if WINDOWS:
        return True
    if sys.platform == "darwin":
        return not os.environ.get("SSH_CONNECTION")
    return bool(
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ) and any(shutil.which(name) for name, _ in EMULATORS)


def mac_accept(listener, proc, wait=MAC_WAIT):
    """Wait for the attach helper, watching osascript so a refusal is reported at once."""
    deadline = time.monotonic() + wait
    while True:
        try:
            return listener.accept()[0]
        except TimeoutError:
            pass
        code = proc.poll()
        if code:
            last = ((proc.stderr.read() or "").strip().splitlines() or [""])[-1]
            if "-1743" in last or "Not authorized" in last:
                raise ValueError(
                    "macOS blocked Agent Workbench from controlling Terminal; allow it in System Settings > "
                    "Privacy & Security > Automation, then launch again."
                )
            raise ValueError(
                "Terminal could not be opened ("
                + (last[:160] or f"osascript exited with code {code}")
                + "); start the agent from the Workbench terminal dock instead."
            )
        if time.monotonic() > deadline:
            if code is None:
                proc.kill()
            raise ValueError(
                f"Terminal did not start the agent within {wait} seconds; launch again, or start the agent "
                "from the Workbench terminal dock."
            )


def mac_terminal(argv, cwd, env):
    # Only the private socket path goes into Terminal history. Credentials stay in memory.
    with tempfile.TemporaryDirectory(prefix="awb-", dir="/tmp") as folder:
        address = str(Path(folder) / "launch")
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(address)
            listener.listen(1)
            listener.settimeout(0.25)
            attach = shlex.join(
                [sys.executable, str(Path(__file__).resolve()), address]
            )
            proc = subprocess.Popen(
                ["/usr/bin/osascript", "-e", MAC_SCRIPT, attach],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            with mac_accept(listener, proc) as peer:
                peer.settimeout(5)
                peer.sendall(json.dumps([argv, str(cwd or Path.cwd()), env]).encode())
            return proc


def terminal(argv, cwd=None, env=None):
    if WINDOWS:
        return subprocess.Popen(
            safe_argv(argv),
            cwd=cwd,
            env=env,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    if sys.platform == "darwin":
        return mac_terminal(argv, cwd, env if env is not None else os.environ.copy())
    source = env if env is not None else os.environ
    if not source.get("DISPLAY") and not source.get("WAYLAND_DISPLAY"):
        raise ValueError(
            "No graphical terminal is available in this session; run cli.py agent --here in this terminal, "
            "or start the agent from the Workbench terminal dock."
        )
    for name, args in EMULATORS:
        exe = shutil.which(name)
        if exe:
            return subprocess.Popen(
                [exe, *args, *argv], cwd=cwd, env=env, stdin=subprocess.DEVNULL
            )
    raise ValueError(
        "Install a terminal emulator such as xterm, or start the agent from the Workbench terminal dock."
    )


def fingerprint(proc):
    return hashlib.sha256(
        "\0".join(proc.cmdline()).encode("utf8", "replace")
    ).hexdigest()[:16]


def process_record(pid):
    """Identity fields to store next to a pid."""
    import psutil

    proc = psutil.Process(pid)
    try:
        mark = fingerprint(proc)
    except psutil.Error:
        mark = ""
    return dict(pid=pid, born=proc.create_time(), cmd=mark)


def process_state(rec):
    """'live' when rec still names the process it was written for, 'gone' when that process has ended, else 'other'."""
    import psutil

    try:
        proc = psutil.Process(rec["pid"])
        if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
            return "gone"
        if abs(proc.create_time() - rec["born"]) < 0.02:
            return "live"
        return "live" if rec.get("cmd") and fingerprint(proc) == rec["cmd"] else "other"
    except psutil.NoSuchProcess:
        return "gone"
    except (KeyError, TypeError, ValueError):
        return "gone"
    except psutil.Error:
        return "other"


if __name__ == "__main__" and sys.platform == "darwin":
    try:
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(20)
            connection.connect(sys.argv[1])
            with connection.makefile("r", encoding="utf8") as stream:
                args, folder, environment = json.load(stream)
    except (OSError, ValueError, IndexError):
        sys.exit(
            "This Workbench launch has expired. Launch the agent again from Agent Workbench."
        )
    os.chdir(folder)
    os.execvpe(args[0], args, environment)
