"""Environment checks. Each check is independent and returns {id, title, status, detail, fix}."""

import argparse
import codecs
import http.client
import importlib.util
import json
import os
import platform
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("WORKBENCH_DATA", ROOT / ".local")).resolve()
SYSTEM = {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")
STATUSES = ("ok", "warn", "fail", "skip")
SETUP = "Advanced > Setup"
ENGINE = "llama.cpp / Strata"
NOT_NEEDED = "Not needed on this system."
PACKAGES = (
    "requests",
    "psutil",
    "filelock",
    "keyring",
    "mcp",
    "huggingface_hub",
    "yaml",
    "ddgs",
)
WSL_SMI = "/usr/lib/wsl/lib/nvidia-smi"
TERMINALS = ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm")
APPARMOR = Path("/etc/apparmor.d")
USERNS = (
    ("/proc/sys/kernel/apparmor_restrict_unprivileged_userns", "1"),
    ("/proc/sys/kernel/unprivileged_userns_clone", "0"),
    ("/proc/sys/user/max_user_namespaces", "0"),
)
VAULT_FIX = {
    "linux": "Install and unlock GNOME Keyring or KWallet, then restart Agent Workbench; "
    "until then pass API keys as environment variables.",
    "darwin": "Unlock the login keychain in Keychain Access, then restart Agent Workbench.",
    "windows": "Sign in with a regular Windows account so Credential Manager is available, "
    "then restart Agent Workbench.",
}
SSH_FIX = {
    "windows": "Add the OpenSSH Client under Settings > System > Optional features, "
    "then restart Agent Workbench.",
    "linux": "Install openssh-client with your package manager.",
    "darwin": "Reinstall the macOS command line tools with: xcode-select --install",
}
GIT_FIX = "Install Git, which every agent uses to inspect and change projects."
IMPORTS = threading.Lock()
LOCAL_TIMEOUT = 2


def row(ident, title, status, detail, fix=""):
    return dict(id=ident, title=title, status=status, detail=detail, fix=fix)


def _run(argv, timeout=5):
    """(returncode or None, combined output). None means it could not run or timed out."""
    try:
        r = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, ""
    return r.returncode, r.stdout.decode("utf8", "replace").replace("\x00", "").strip()


def _which(name):
    return shutil.which(name) or ""


def _read(path):
    try:
        return Path(path).read_text(encoding="utf8", errors="replace").strip()
    except OSError:
        return None


def _stat(path):
    try:
        return os.stat(path)
    except OSError:
        return None


def _listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


class _Stay(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect, so a probe of 127.0.0.1 can never be sent to another host."""

    def redirect_request(self, *args, **kwargs):
        return None


def _local_text(url):
    """Up to 4096 bytes of the page a loopback server sends; "" when none arrives in time."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _Stay)
    text = b""
    try:
        with opener.open(url, timeout=LOCAL_TIMEOUT) as r:
            limit = time.monotonic() + LOCAL_TIMEOUT
            while len(text) < 4096 and time.monotonic() < limit:
                piece = r.read1(4096 - len(text))
                if not piece:
                    break
                text += piece
    except (OSError, ValueError, http.client.HTTPException):
        pass
    return text.decode("utf8", "replace")


def _module(name):
    """Import a project or third-party module on first use, one thread at a time."""
    with IMPORTS:
        return importlib.import_module(name)


def _missing(name):
    try:
        return importlib.util.find_spec(name) is None
    except (ImportError, ValueError):
        return name not in sys.modules


def _writable(folder):
    try:
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle, name = tempfile.mkstemp(dir=folder)
        os.close(handle)
        os.unlink(name)
    except OSError:
        return False
    return True


def _inside(path, folder):
    path, folder = Path(path), Path(folder)
    return path == folder or folder in path.parents


def _quote(path):
    """A path as one word of a command the user copies into a terminal."""
    text = str(path)
    if SYSTEM == "windows" or not re.search(r'["$`\\!]', text):
        return f'"{text}"'
    return shlex.quote(text)


def _pip():
    """The install command; PowerShell does not run a line that starts with a quoted path."""
    run = "Run in Command Prompt" if SYSTEM == "windows" else "Run"
    lock = ROOT / "requirements.lock"
    if lock.is_file():
        return (
            f"{run}: {_quote(sys.executable)} -m pip install --require-hashes "
            f"--only-binary :all: -r {_quote(lock)}"
        )
    requirements = ROOT / "requirements.txt"
    return f"{run}: {_quote(sys.executable)} -m pip install -r {_quote(requirements)}"


def first(text):
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def check_python():
    major, minor, micro = sys.version_info[:3]
    version, machine = f"{major}.{minor}.{micro}", platform.machine()
    if (major, minor) < (3, 12):
        return row(
            "python",
            "Python",
            "fail",
            f"Python {version} is too old.",
            "Install Python 3.12 or newer and start Agent Workbench again.",
        )
    if SYSTEM == "darwin" and machine != "arm64":
        return row(
            "python",
            "Python",
            "warn",
            "Python is running under Rosetta (x86_64).",
            "Start Agent Workbench from a native arm64 terminal so the Metal engine "
            "and the desktop runtime can run.",
        )
    detail = f"Python {version} ({machine}) at {sys.executable}"
    return row("python", "Python", "ok", detail)


def check_packages():
    title = "Python packages"
    names = PACKAGES + ("winpty" if SYSTEM == "windows" else "ptyprocess",)
    missing = [name for name in names if _missing(name)]
    if missing:
        detail = "Missing: " + ", ".join(missing)
        return row("packages", title, "fail", detail, _pip())
    return row("packages", title, "ok", "All required packages are present.")


def check_data_dir():
    title = "Data folder"
    if not _writable(DATA):
        return row(
            "data-dir",
            title,
            "fail",
            f"{DATA} is missing or not writable.",
            "Make that folder writable, or set WORKBENCH_DATA to a folder you own.",
        )
    if SYSTEM != "windows" and stat.S_IMODE(DATA.stat().st_mode) & 0o077:
        return row(
            "data-dir",
            title,
            "warn",
            f"Other accounts on this computer can read {DATA}.",
            f"Run: chmod 700 {_quote(DATA)}",
        )
    home = Path.home()
    synced = (home / "Desktop", home / "Documents")
    if SYSTEM == "darwin" and any(_inside(DATA, folder) for folder in synced):
        return row(
            "data-dir",
            title,
            "warn",
            f"{DATA} is inside a folder that iCloud Drive can sync, "
            "including keys and model files.",
            "Move Agent Workbench out of Desktop and Documents, or set WORKBENCH_DATA "
            'to "~/Library/Application Support/Agent Workbench".',
        )
    free = shutil.disk_usage(DATA).free / 1024**3
    if free < 5:
        return row(
            "data-dir",
            title,
            "warn",
            f"Only {free:.1f} GiB free at {DATA}.",
            "Free some disk space before downloading models.",
        )
    return row("data-dir", title, "ok", f"{DATA} · {free:.0f} GiB free")


def check_vault():
    title = "Credential vault"
    try:
        keyring = _module("keyring")
    except ImportError:
        return row("vault", title, "fail", "The keyring package is missing.", _pip())
    backend = keyring.get_keyring()
    kind = type(backend).__module__ + "." + type(backend).__name__
    if ".fail." in kind or ".null." in kind or "plaintext" in kind.lower():
        detail = f"No secure credential vault is available ({kind})."
        return row("vault", title, "fail", detail, VAULT_FIX[SYSTEM])
    name = getattr(backend, "name", kind)
    return row("vault", title, "ok", f"{name} (not opened by this check)")


def _version_line(text):
    """The line where llama-server states its version; a loader error only mentions the word."""
    lines = (line.strip() for line in text.splitlines())
    stated = re.compile(r"(llama-server\s+)?version\b", re.I)
    return next((line for line in lines if stated.match(line)), "")


def check_engine():
    tooling = _module("tooling")
    title = "Local engine"
    path = tooling.location(ENGINE)
    if not path:
        return row(
            "engine",
            title,
            "warn",
            "The managed llama.cpp engine is not installed.",
            f"Install it in {SETUP} > {ENGINE} > Install, "
            "or attach a server that is already running.",
        )
    code, text = _run([path, "--version"], 8)
    version = _version_line(text)
    if code == 0 or version:
        detail = f"{version or first(text) or 'llama-server'} at {path}"
        return row("engine", title, "ok", detail)
    if SYSTEM == "darwin":
        folder = Path(path).parent
        fix = (
            f"Run: xattr -dr com.apple.quarantine {_quote(folder)} and retry, "
            f"or reinstall it in {SETUP}."
        )
    else:
        fix = f"Reinstall it in {SETUP} > {ENGINE} > Install."
    detail = f"The engine at {path} does not start."
    return row("engine", title, "fail", detail, fix)


def _apple_gpu():
    code, text = _run(["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"], 2)
    chip = first(text) if code == 0 and text else "Apple Silicon"
    code, text = _run(["/usr/sbin/sysctl", "-n", "hw.memsize"], 2)
    memory = ""
    if code == 0 and text.isdigit():
        memory = f" with {int(text) / 1024**3:.0f} GiB unified memory"
    return row("gpu", "GPU", "ok", f"{chip}{memory} (Metal)")


def _gpu():
    title = "GPU"
    if SYSTEM == "darwin" and platform.machine() == "arm64":
        return _apple_gpu()
    smi = _which("nvidia-smi") or (WSL_SMI if os.path.isfile(WSL_SMI) else "")
    if smi:
        query = "--query-gpu=name,driver_version,memory.total"
        code, text = _run([smi, query, "--format=csv,noheader"], 5)
        if code == 0 and text:
            line = first(text)
            named = line.lower().startswith("nvidia")
            return row("gpu", title, "ok", line if named else "NVIDIA " + line)
        return row(
            "gpu",
            title,
            "fail",
            "nvidia-smi cannot reach the NVIDIA driver.",
            "Reinstall or update the NVIDIA driver, then restart the computer.",
        )
    vulkan = _which("vulkaninfo")
    if vulkan:
        text = _run([vulkan, "--summary"], 8)[1]
        names = [n.strip() for n in re.findall(r"deviceName\s*=\s*(.+)", text)]
        real = [n for n in names if not re.search("llvmpipe|swiftshader", n, re.I)]
        if real:
            return row("gpu", title, "ok", f"Vulkan device: {real[0]}")
        if names:
            return row(
                "gpu",
                title,
                "warn",
                "Only a software Vulkan device was found.",
                "Install your GPU vendor's driver, or keep using the CPU build, "
                "a hosted API or an attached server.",
            )
    return row(
        "gpu",
        title,
        "warn",
        "No GPU was detected, so local models run on the CPU.",
        "Install your GPU driver (on Linux also vulkan-tools for AMD or Intel), "
        "or use a hosted API or an attached server.",
    )


def check_gpu():
    out = _gpu()
    try:
        reason = _module("setup_engines").detect()["reason"]
        if isinstance(reason, str) and reason:
            out["detail"] += " " + reason
    except Exception:
        pass
    return out


def _git():
    git = _which("git")
    shim = SYSTEM == "darwin" and git == "/usr/bin/git"
    if not git or (shim and _run(["/usr/bin/xcode-select", "-p"], 3)[0] != 0):
        return row("git", "Git", "warn", "Git was not found.", GIT_FIX)
    detail = first(_run([git, "--version"], 5)[1]) or f"Git is available at {git}."
    return row("git", "Git", "ok", detail)


def check_clis():
    tooling = _module("tooling")
    found = tooling.inventory(tooling.AGENTS, versions=True)
    rows = []
    for item in found:
        name, path = item["name"], item["path"]
        ident = "cli-" + name.lower().replace(" ", "-")
        if not item["installed"]:
            fix = f"Install it in {SETUP} if you want to use it."
            rows.append(row(ident, name, "skip", "Not installed.", fix))
        elif item["on_path"]:
            version = item["version"] or "version unknown"
            rows.append(row(ident, name, "ok", f"{version} at {path}"))
        else:
            folder = os.path.dirname(path)
            fix = item["hint"] or (
                f'Add "{folder}" to your PATH to use it in your own terminals.'
            )
            detail = f"Installed at {path}, outside the PATH of your terminals."
            rows.append(row(ident, name, "warn", detail, fix))
    count = sum(bool(item["installed"]) for item in found)
    if count:
        detail = f"{count} of {len(found)} installed."
        rows.append(row("agents", "Agent CLIs", "ok", detail))
    else:
        fix = f"Install OMP in {SETUP} > OMP > Install."
        detail = "No agent CLI is installed."
        rows.append(row("agents", "Agent CLIs", "fail", detail, fix))
    rows.append(_git())
    return rows


def check_ports():
    runtime = _module("runtime")
    port = runtime.PORT
    if runtime.owned()[0]:
        detail = f"The Workbench model server is listening on {port}."
        model = row("port-model", "Model port", "ok", detail)
    elif _listening(port):
        model = row(
            "port-model",
            "Model port",
            "fail",
            f"Port {port} is used by another program.",
            "Stop that program, or set WORKBENCH_PORT to a free port "
            "before starting Agent Workbench.",
        )
    else:
        model = row("port-model", "Model port", "ok", f"Port {port} is free.")
    title = "Page-crawling port"
    if not _listening(3002):
        crawl = row("port-firecrawl", title, "ok", "Port 3002 is free.")
    elif "Firecrawl" in _local_text("http://127.0.0.1:3002/"):
        detail = "The page-crawling stack is running on port 3002."
        crawl = row("port-firecrawl", title, "ok", detail)
    else:
        crawl = row(
            "port-firecrawl",
            title,
            "warn",
            "Port 3002 is used by another program.",
            "Stop that program before you turn on page crawling.",
        )
    return [model, crawl]


def check_docker():
    tooling = _module("tooling")
    if not tooling.find("docker"):
        if (tooling.location("Docker") or "").startswith("WSL"):
            detail = "Docker runs inside the AgentWorkbench-Web WSL distribution."
            return row("docker", "Docker", "ok", detail)
        detail = "Not installed; only page crawling and the sandbox need it."
        return row("docker", "Docker", "skip", detail)
    problem = tooling.docker_problem()
    if problem:
        detail = "Docker is installed but not usable."
        return row("docker", "Docker", "warn", detail, problem)
    return row("docker", "Docker", "ok", "Docker and Compose v2 answer.")


def check_terminal():
    title = "Terminal emulator"
    if SYSTEM != "linux":
        return row("terminal", title, "skip", NOT_NEEDED)
    name = next((n for n in TERMINALS if _which(n)), "")
    if name:
        return row("terminal", title, "ok", f"{name} is available.")
    return row(
        "terminal",
        title,
        "warn",
        "No terminal emulator was found.",
        "Install xterm so agent windows and guided installs can open.",
    )


def _profiled(folders):
    """True when an AppArmor profile names one of folders or any runtime version under the data folder."""
    try:
        files = [
            f
            for f in sorted(APPARMOR.iterdir())
            if f.is_file() and f.stat().st_size <= 65536
        ]
    except OSError:
        return False
    wanted = [*folders, str(DATA / "desktop-runtime") + "/"]
    return any(text in (_read(f) or "") for f in files for text in wanted)


def _data_env():
    """The assignment setup-linux.sh needs in a terminal that does not have this data folder set."""
    if DATA == (ROOT / ".local").resolve():
        return ""
    return f"WORKBENCH_DATA={_quote(DATA)} "


def check_sandbox():
    title = "Desktop sandbox"
    if SYSTEM != "linux":
        return row("sandbox", title, "skip", NOT_NEEDED)
    restricted = any(_read(path) == value for path, value in USERNS)
    if not restricted:
        detail = "Unprivileged user namespaces are available."
        return row("sandbox", title, "ok", detail)
    helpers = sorted(DATA.glob("desktop-runtime/linux-*/chrome-sandbox"))
    helpers.append(ROOT / "desktop/node_modules/electron/dist/chrome-sandbox")
    helpers = [h for h in helpers if h.is_file()]
    for helper in helpers:
        info = _stat(helper)
        if info and info.st_uid == 0 and info.st_mode & 0o4000:
            detail = "The setuid sandbox helper is in place."
            return row("sandbox", title, "ok", detail)
    if _profiled([str(h.parent) for h in helpers]):
        detail = "An AppArmor profile allows the desktop runtime."
        return row("sandbox", title, "ok", detail)
    if helpers:
        helper, script = helpers[0], ROOT / "setup-linux.sh"
        if _inside(helper, DATA / "desktop-runtime") and "--sandbox" in (_read(script) or ""):
            fix = f"Run: {_data_env()}sh {_quote(script)} --sandbox"
        else:
            fix = f"Run: sudo chown root:root {_quote(helper)} && sudo chmod 4755 {_quote(helper)}"
        return row(
            "sandbox",
            title,
            "fail",
            "This system restricts user namespaces and the sandbox helper is not set up.",
            fix,
        )
    return row(
        "sandbox",
        title,
        "warn",
        "This system restricts user namespaces; "
        "the desktop runtime has not been downloaded yet.",
        "Start Agent Workbench once, then run Doctor again for the exact command.",
    )


def _quarantine_targets():
    targets = [ROOT, Path(sys.executable).resolve()]
    try:
        engine = _module("tooling").location(ENGINE)
    except Exception:
        engine = ""
    if engine:
        targets.append(Path(engine))
    return targets + sorted(DATA.glob("desktop-runtime/darwin-*"))


def check_quarantine():
    title = "macOS quarantine"
    if SYSTEM != "darwin":
        return row("quarantine", title, "skip", NOT_NEEDED)
    probe = ["/usr/bin/xattr", "-p", "com.apple.quarantine"]
    flagged = []
    for target in _quarantine_targets():
        if target not in flagged and _run(probe + [str(target)], 3)[0] == 0:
            flagged.append(target)
    if not flagged:
        detail = "No quarantine attribute on the app folder, Python, engine or desktop runtime."
        return row("quarantine", title, "ok", detail)
    roots = []
    for target in flagged:
        if _inside(target, DATA) and not _inside(DATA, ROOT):
            base = DATA
        elif _inside(target, ROOT):
            base = ROOT
        else:
            base = target
        if base not in roots:
            roots.append(base)
    quoted = " ".join(_quote(base) for base in roots)
    return row(
        "quarantine",
        title,
        "fail",
        "macOS quarantine is set on: " + ", ".join(str(t) for t in flagged),
        f"Run: xattr -dr com.apple.quarantine {quoted} and start Agent Workbench again.",
    )


def check_automation(active=False):
    title = "Terminal automation"
    if SYSTEM != "darwin":
        return row("automation", title, "skip", NOT_NEEDED)
    if not active:
        return row(
            "automation",
            title,
            "skip",
            "macOS asks for permission to control Terminal the first time an agent "
            "window opens; this check does not trigger that prompt.",
            "If agent windows do not open, allow Terminal under "
            "System Settings > Privacy & Security > Automation.",
        )
    if _run(["/usr/bin/pgrep", "-x", "Terminal"], 3)[0] != 0:
        return row(
            "automation",
            title,
            "skip",
            "Terminal is not running, so the permission was not tested.",
            "Open Terminal, then run this check again.",
        )
    script = 'tell application "Terminal" to count windows'
    code, text = _run(["/usr/bin/osascript", "-e", script], 20)
    if code == 0:
        detail = "Agent Workbench may control Terminal."
        return row("automation", title, "ok", detail)
    if code is None:
        return row(
            "automation",
            title,
            "warn",
            "macOS is waiting for your answer to its permission prompt.",
            "Choose Allow in the macOS dialog, then run this check again.",
        )
    if "-1743" in text or "not authorized" in text.lower():
        return row(
            "automation",
            title,
            "fail",
            "macOS blocked control of Terminal.",
            "Allow Terminal under System Settings > Privacy & Security > Automation "
            "for the app that started Agent Workbench, then retry.",
        )
    return row(
        "automation",
        title,
        "warn",
        f"Terminal did not answer: {first(text)}",
        "Open Terminal once, then run this check again.",
    )


def check_ssh():
    title = "SSH client"
    ssh = _which("ssh")
    if not ssh:
        detail = "ssh was not found; SSH machines are unavailable."
        return row("ssh", title, "warn", detail, SSH_FIX[SYSTEM])
    if not _which("ssh-keygen"):
        detail = "ssh-keygen was not found; pairing a machine needs it."
        return row("ssh", title, "warn", detail, SSH_FIX[SYSTEM])
    detail = first(_run([ssh, "-V"], 5)[1]) or "ssh is available."
    return row("ssh", title, "ok", detail)


def check_proxy():
    title = "Proxy and loopback"

    def value(name):
        return os.environ.get(name) or os.environ.get(name.lower()) or ""

    if not any(value(name) for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")):
        return row("proxy", title, "ok", "No proxy is configured.")
    skipped = ",".join(os.environ.get(name, "") for name in ("NO_PROXY", "no_proxy"))
    if "*" in skipped or ("127.0.0.1" in skipped and "localhost" in skipped):
        return row("proxy", title, "ok", "Loopback addresses bypass the proxy.")
    return row(
        "proxy",
        title,
        "warn",
        "A proxy is set and loopback addresses are not excluded.",
        "Add 127.0.0.1,localhost to NO_PROXY before starting Agent Workbench.",
    )


CHECKS = (
    ("python", check_python),
    ("packages", check_packages),
    ("data-dir", check_data_dir),
    ("vault", check_vault),
    ("engine", check_engine),
    ("gpu", check_gpu),
    ("clis", check_clis),
    ("ports", check_ports),
    ("docker", check_docker),
    ("terminal", check_terminal),
    ("sandbox", check_sandbox),
    ("quarantine", check_quarantine),
    ("automation", check_automation),
    ("ssh", check_ssh),
    ("proxy", check_proxy),
)


def run(ids=None, active=False):
    """Run the named check groups (all when ids is None). Returns rows in CHECKS order."""
    known = [key for key, _ in CHECKS]
    unknown = [str(i) for i in (ids or []) if i not in known]
    if unknown:
        raise ValueError(
            f"Unknown check: {', '.join(unknown)}. Choose from: {', '.join(known)}."
        )
    wanted = [(key, fn) for key, fn in CHECKS if ids is None or key in ids]

    def guarded(item):
        key, fn = item
        try:
            out = fn(active) if key == "automation" else fn()
        except Exception as exc:
            out = row(
                key,
                key,
                "warn",
                f"This check could not run ({type(exc).__name__}: {exc}).",
                "Send this line with the Doctor output when you report the problem.",
            )
        return out if isinstance(out, list) else [out]

    with ThreadPoolExecutor(max_workers=6) as pool:
        return [r for rows in pool.map(guarded, wanted) for r in rows]


def counts(rows):
    return {s: sum(r["status"] == s for r in rows) for s in STATUSES}


def render(rows):
    lines = []
    for r in rows:
        lines.append(f"{r['status']:<5} {r['title']:<20} {r['detail']}")
        if r["fix"]:
            lines.append(f"      fix: {r['fix']}")
    c = counts(rows)
    total = f"{c['ok']} ok, {c['warn']} warnings, {c['fail']} failed, {c['skip']} skipped"
    return "\n".join(lines + ["", total])


PLAIN = str.maketrans(
    {"·": "-", "→": "->", "…": "...", "—": "-", "–": "-", "‘": "'", "’": "'",
     "“": '"', "”": '"', "\u00a0": " ", "\u202f": " "}
)


def _emit(text):
    encoding = getattr(sys.stdout, "encoding", None)
    if encoding and codecs.lookup(encoding).name != "utf-8":
        # A pipe on Windows (an SSH session, a redirect) gets the ANSI code page, and the
        # reader may decode it as UTF-8 or OEM: plain ASCII punctuation reads the same in all.
        text = text.translate(PLAIN).encode(encoding, "replace").decode(encoding)
    print(text)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="doctor", description="Check this computer for Agent Workbench."
    )
    parser.add_argument(
        "ids", nargs="*", help="Check groups to run; all of them when omitted."
    )
    parser.add_argument("--json", action="store_true", help="Print the rows as JSON.")
    parser.add_argument(
        "--active",
        action="store_true",
        help="Also test Terminal control on macOS, which can show a permission prompt.",
    )
    args = parser.parse_args(argv)
    try:
        rows = run(args.ids or None, args.active)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    _emit(json.dumps(rows, indent=2) if args.json else render(rows))
    return 1 if any(r["status"] == "fail" for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
