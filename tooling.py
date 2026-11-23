"""Tool catalog: detect, install, update and sign in to external CLIs. Nothing runs without an explicit request."""

import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import NamedTuple

import psutil

from host import blender_executable, command, terminal
from runtime import DATA, QUIET, ROOT

SYSTEM = {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")
ADDED_ENV = "WORKBENCH_PATH_ADDED"  # os.pathsep list of folders Workbench appended to PATH itself
INSTALL_TIMEOUT = 3600  # seconds, one limit for every installer
MARK = "workbench-setup: "  # a child prints this prefix to hand the user one sentence
POLL = 0.5  # seconds between looks at a running installer
ACTIONS = ("Install", "Update", "Login", "Docs")
AGENTS = ("OMP", "Pi", "Claude Code", "Codex", "Hermes", "opencode")
PROBED = ("winget", "brew", "apt-get", "dnf", "npm", "uv", "curl", "sudo", "wsl", "docker")
OMP_SOURCE = "https://raw.githubusercontent.com/can1357/oh-my-pi/a7135b8f31826c878734388b648f52bed8da436e/scripts/install"
OMP_REF = "v19.1.0"
POWERSHELL = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
CURL_MISSING = "Install curl with your package manager, then retry."
WSL = ["wsl", "-d", "AgentWorkbench-Web", "-u", "root", "--exec"]
# Same places, in the same order, as vllm_worker.executable(); inside WSL the account is root.
VLLM_WSL = (
    "/opt/agent-workbench/vllm/bin/vllm",
    "/root/.local/share/agent-workbench/vllm/bin/vllm",
    "/root/.local/bin/vllm",
)
VLLM_LOCAL = (
    "/opt/agent-workbench/vllm/bin/vllm",
    str(Path.home() / ".local/share/agent-workbench/vllm/bin/vllm"),
)
PATH_LOCK = threading.Lock()
PLAIN_DIR = re.compile(r"[\w@+=,./~ -]+")  # folder names that can sit inside a pasted shell line unescaped

# name: executable, Windows package, Debian package, upstream documentation
TOOLS = {
    "Hermes": (
        "hermes",
        None,
        None,
        "https://hermes-agent.nousresearch.com/docs/getting-started/installation",
    ),
    "Node.js": (
        "node",
        "OpenJS.NodeJS.LTS",
        "nodejs npm",
        "https://nodejs.org/en/download",
    ),
    "Bun": ("bun", "Oven-sh.Bun", None, "https://bun.sh/docs/installation"),
    "uv": (
        "uv",
        "astral-sh.uv",
        None,
        "https://docs.astral.sh/uv/getting-started/installation/",
    ),
    "Docker": (
        "docker",
        "Docker.DockerDesktop",
        "docker.io docker-compose",
        "https://docs.docker.com/engine/install/",
    ),
    "GitHub CLI": ("gh", "GitHub.cli", "gh", "https://cli.github.com/"),
    "AWS CLI": (
        "aws",
        "Amazon.AWSCLI",
        "awscli",
        "https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html",
    ),
    "Hugging Face CLI": (
        "hf",
        None,
        None,
        "https://huggingface.co/docs/huggingface_hub/guides/cli",
    ),
    "OMP": ("omp", None, None, "https://github.com/can1357/oh-my-pi"),
    "Pi": ("pi", None, None, "https://github.com/earendil-works/pi"),
    "Claude Code": (
        "claude",
        "Anthropic.ClaudeCode",
        None,
        "https://code.claude.com/docs/en/setup",
    ),
    "Codex": ("codex", None, None, "https://developers.openai.com/codex/cli/"),
    "opencode": ("opencode", None, None, "https://opencode.ai/docs/"),
    "Blender": (
        "blender",
        "BlenderFoundation.Blender",
        "blender",
        "https://www.blender.org/download/",
    ),
    "llama.cpp / Strata": (
        "llama-server",
        None,
        None,
        "https://github.com/Niko1221/Strata",
    ),
    "vLLM": (
        "vllm",
        None,
        None,
        "https://docs.vllm.ai/en/latest/getting_started/installation/gpu/",
    ),
    "Sandbox image": ("docker", None, None, "https://docs.docker.com/"),
}
LOGIN = {
    "GitHub CLI": ["gh", "auth", "login"],
    "AWS CLI": ["aws", "configure", "sso"],
    "Hugging Face CLI": ["hf", "auth", "login"],
    "Codex": ["codex", "login"],
    "opencode": ["opencode", "auth", "login"],
    "Claude Code": ["claude", "auth", "login"],
    "Pi": ["pi", "auth", "login"],
    "OMP": ["omp", "login"],
    "Docker": ["docker", "login"],
}
NPM = {
    "Pi": "@earendil-works/pi-coding-agent@latest",
    "Codex": "@openai/codex@latest",
    "opencode": "opencode-ai@1",  # the 1.x line, whose configuration shape connections.prepare writes
}
# macOS, used only when brew is found
BREW = {
    "Node.js": ("formula", "node"),
    "uv": ("formula", "uv"),
    "GitHub CLI": ("formula", "gh"),
    "AWS CLI": ("formula", "awscli"),
    "Docker": ("cask", "docker-desktop"),
    "Claude Code": ("cask", "claude-code"),
    "Blender": ("cask", "blender"),
}
DNF = {"Node.js": "nodejs npm", "GitHub CLI": "gh", "Blender": "blender"}
MAC_DOCS = {"Docker": "https://docs.docker.com/desktop/setup/install/mac-install/"}
HOLD = (
    '; s=$?; echo; if [ "$s" -eq 0 ]; then echo "Finished. Return to Agent Workbench and choose Recheck installations."; '
    'else echo "The command failed with exit code $s."; fi; printf "Press Enter to close this window. "; read _'
)
DOCS_MESSAGES = {
    "requested": "The {name} documentation was opened in your browser.",
    "no-installer": "Agent Workbench has no automatic installer for {name} on this system, so its install page "
    "was opened; follow it, then choose Recheck installations.",
    "no-brew": "Homebrew was not found, so the {name} install page was opened; follow it, then choose "
    "Recheck installations.",
    "no-compose-v2": "This Linux distribution does not package Docker Compose v2, so the Docker install guide "
    "was opened; follow it, then choose Recheck installations.",
}
NO_TERMINAL = {
    "darwin": "No Terminal window could be opened; allow Terminal under System Settings > Privacy & Security > "
    "Automation, then retry.",
    "linux": "No terminal window could be opened; install xterm, then retry.",
    "windows": "No console window could be opened; start Agent Workbench from a normal desktop session, then retry.",
}


def expand_percent(value, env):
    """Expand %NAME% the way Windows does, case-insensitively; unknown names stay as written."""
    low = {k.lower(): v for k, v in env.items()}
    return re.sub(r"%([^%]+)%", lambda m: low.get(m.group(1).lower(), m.group(0)), value)


def norm(entry, fold):
    entry = entry.strip().strip('"').rstrip("/\\")
    return entry.lower() if fold else entry


def merge_path(current, extra, sep, fold):
    """Append entries of extra that current lacks. Returns (new PATH string, entries added)."""
    parts = [p for p in current.split(sep) if p]
    seen = {norm(p, fold) for p in parts}
    added = []
    for d in extra:
        if d and norm(d, fold) not in seen:
            seen.add(norm(d, fold))
            added.append(d)
    return sep.join(parts + added), added


def npmrc_prefix(home):
    path = Path(home) / ".npmrc"
    try:
        if not path.is_file() or path.stat().st_size > 65536:
            return ""
        text = path.read_text(encoding="utf8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        m = re.match(r"^\s*prefix\s*=\s*(.+)$", line)
        if m:
            value = m.group(1).strip().strip("\"'")
            if value.startswith("~"):
                value = home + value[1:]
            return value.replace("${HOME}", home)
    return ""


def stored_dirs(registry, env):
    """The folders named by raw Windows PATH strings, with %NAME% expanded."""
    return [expand_percent(p.strip(), env) for raw in registry for p in raw.split(";") if p.strip()]


def candidate_dirs(system, env, home, registry=()):
    """Folders where installers put executables, most specific first. Existence is not checked."""
    prefix = env.get("NPM_CONFIG_PREFIX") or env.get("npm_config_prefix") or npmrc_prefix(home)
    if system == "windows":
        low = {k.lower(): v for k, v in env.items()}
        out = stored_dirs(registry, env)
        for var, rest in (
            ("appdata", "npm"),
            ("userprofile", ".bun\\bin"),
            ("userprofile", ".local\\bin"),
            ("userprofile", ".opencode\\bin"),
            ("localappdata", "Microsoft\\WinGet\\Links"),
        ):
            if low.get(var):
                out.append(low[var].rstrip("\\") + "\\" + rest)
        return out + ([prefix] if prefix else [])
    out = [home + "/.local/bin", home + "/.bun/bin", home + "/.opencode/bin"]
    if prefix:
        out.append(prefix.rstrip("/") + "/bin")
    if system == "darwin":
        return out + ["/opt/homebrew/bin", "/usr/local/bin", home + "/.docker/bin"]
    return out + ["/usr/local/bin"]


def _registry_paths():
    if SYSTEM != "windows":
        return []
    import winreg

    out = []
    for hive, key in (
        (winreg.HKEY_CURRENT_USER, "Environment"),
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    ):
        try:
            with winreg.OpenKey(hive, key) as handle:
                out.append(str(winreg.QueryValueEx(handle, "Path")[0]))
        except OSError:
            pass
    return out


def refresh_path():
    """Append existing per-user install folders to this process's PATH. Returns the folders added."""
    fold = SYSTEM == "windows"
    with PATH_LOCK:
        registry = _registry_paths()
        found = candidate_dirs(SYSTEM, os.environ, str(Path.home()), registry)
        dirs = [d for d in found if os.path.isdir(d)]
        new, added = merge_path(os.environ.get("PATH", ""), dirs, os.pathsep, fold)
        os.environ["PATH"] = new
        stored = {norm(d, fold) for d in stored_dirs(registry, os.environ)}
        ours = [d for d in added if norm(d, fold) not in stored]
        os.environ[ADDED_ENV] = merge_path(os.environ.get(ADDED_ENV, ""), ours, os.pathsep, fold)[0]
        return added


def user_dirs():
    """PATH entries Workbench did not append itself: what the user's own terminals search."""
    fold = SYSTEM == "windows"
    ours = {norm(p, fold) for p in os.environ.get(ADDED_ENV, "").split(os.pathsep) if p}
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    keys = [norm(p, fold) for p in parts]
    # Workbench appends a folder once. A second entry came from the user's shell profile, read by a shell
    # that inherited both variables (a Workbench terminal), so the folder is the user's as well.
    return [p for p, key in zip(parts, keys) if key not in ours or keys.count(key) > 1]


def find(exe):
    found = shutil.which(exe)
    if found:
        return found
    known = [d for d in candidate_dirs(SYSTEM, os.environ, str(Path.home())) if os.path.isdir(d)]
    return (shutil.which(exe, path=os.pathsep.join(known)) if known else None) or ""


def path_hint(directory, exe, shell, system, home):
    if system == "windows" or not directory:
        return ""
    inside = directory == home or directory.startswith(home + "/")
    shown = "~" + directory[len(home):] if inside else directory
    ref = shown.replace("~", "$HOME", 1) if inside else shown
    name = os.path.basename(shell)
    if not PLAIN_DIR.fullmatch(shown) or (name == "fish" and " " in shown):
        return f"To use {exe} in your own terminals, add {shown} to the PATH in your shell profile."
    if name == "fish":
        line = f"fish_add_path {shown}"
    else:
        rc = {"zsh": "~/.zshrc", "bash": "~/.bash_profile" if system == "darwin" else "~/.bashrc"}.get(
            name, "~/.profile"
        )
        line = f"echo 'export PATH=\"{ref}:$PATH\"' >> {rc}"
    return f"To use {exe} in your own terminals, run: {line}"


def quiet(argv, timeout=5, env=None):
    """(returncode, combined output) of a short probe; (None, "") when it cannot run or times out."""
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=QUIET,
        )
    except OSError:
        return None, ""
    try:
        out = proc.communicate(timeout=timeout)[0]
    except subprocess.TimeoutExpired:
        # A shim's child keeps the pipe open after the shim dies, so the whole tree goes.
        kill_tree(proc.pid)
        try:
            proc.communicate(timeout=5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        return None, ""
    return proc.returncode, out.decode("utf8", "replace").replace("\x00", "")


def version(path):
    if not path or path.startswith("WSL") or not os.path.isfile(path):
        return ""
    m = re.search(r"\d+\.\d+(?:\.\d+)?", quiet([path, "--version"], 5)[1])
    return m.group(0) if m else ""


def wsl_probe(name, runner=subprocess.run):
    for candidate in ("/usr/bin/docker",) if name == "Docker" else VLLM_WSL:
        try:
            r = runner(
                WSL + ["test", "-x", candidate],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=10,
                creationflags=QUIET,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if r.returncode == 0:
            return "WSL · " + candidate
    return ""


def location(name):
    exe = find(TOOLS[name][0])
    if name == "llama.cpp / Strata":
        from runtime import runtime

        try:
            return str(runtime("llama-native-b12633", "llama-server.exe"))
        except ValueError:
            pass
    if name == "Blender" and not exe:
        exe = blender_executable()
    if name == "vLLM" and not exe and os.name != "nt":
        exe = next((p for p in VLLM_LOCAL if os.path.isfile(p)), "")
    if name in ("Docker", "vLLM") and os.name == "nt" and not exe and shutil.which("wsl"):
        found = wsl_probe(name)
        if found:
            return found
    if name == "Sandbox image":
        import boxes

        try:
            return boxes.IMAGE if boxes.image_present() else ""
        except (ValueError, OSError, subprocess.SubprocessError):
            return ""
    return exe or ""


def detect(name, versions=False):
    exe = TOOLS[name][0]
    try:
        path = location(name) or ""
    except (OSError, ValueError, subprocess.SubprocessError):
        path = ""
    cli = bool(path) and path == find(exe)
    fold = SYSTEM == "windows"
    folder = os.path.dirname(path)
    on_path = not cli or norm(folder, fold) in {norm(d, fold) for d in user_dirs()}
    hint = ""
    if cli and not on_path:
        hint = path_hint(folder, exe, os.environ.get("SHELL", ""), SYSTEM, str(Path.home()))
    return dict(
        name=name,
        exe=exe,
        path=path,
        installed=bool(path),
        on_path=on_path,
        hint=hint,
        version=version(path) if versions and cli else "",
    )


def inventory(names=None, versions=False):
    refresh_path()
    return [detect(n, versions) for n in (names or TOOLS)]


class Host(NamedTuple):
    system: str
    tools: frozenset = frozenset()
    root: bool = False
    npm_user_prefix: bool = False
    apt_docker: tuple = ()
    home: str = "~"


def parse_apt_candidate(text):
    m = re.search(r"^\s*Candidate:\s*(\S+)", text, re.M)
    return m.group(1) if m and m.group(1) != "(none)" else ""


def apt_candidate(package):
    return parse_apt_candidate(quiet(["apt-cache", "policy", package], 10, dict(os.environ, LC_ALL="C"))[1])


def apt_docker_packages(candidate):
    """candidate(package) -> version string or "". Picks a set that provides `docker compose`."""

    def major(version):
        m = re.match(r"(?:\d+:)?(\d+)", version)
        return int(m.group(1)) if m else 0

    if candidate("docker-ce") and candidate("docker-compose-plugin"):
        return ("docker-ce", "docker-ce-cli", "containerd.io", "docker-compose-plugin")
    if candidate("docker-compose-v2"):
        return ("docker.io", "docker-compose-v2")
    if major(candidate("docker-compose")) >= 2:
        return ("docker.io", "docker-compose")
    return ()


def prefix_writable(prefix):
    for target in (Path(prefix) / "lib" / "node_modules", Path(prefix) / "bin"):
        while not target.exists() and target != target.parent:
            target = target.parent
        if not os.access(target, os.W_OK):
            return False
    return True


def npm_user_prefix(npm):
    """True when npm's global prefix is not writable, so installs go under ~/.local instead."""
    code, out = quiet([npm, "config", "get", "prefix"], 20)
    line = next((v.strip() for v in reversed(out.splitlines()) if v.strip()), "")
    if code != 0 or not line:
        return True
    return not prefix_writable(line)


def host_facts(name=""):
    tools = frozenset(t for t in PROBED if find(t))
    apt = ()
    if SYSTEM == "linux" and name == "Docker" and "apt-get" in tools:
        apt = apt_docker_packages(apt_candidate)
    user = SYSTEM != "windows" and name in NPM and "npm" in tools and npm_user_prefix(find("npm"))
    return Host(SYSTEM, tools, getattr(os, "geteuid", lambda: 1)() == 0, bool(user), apt, str(Path.home()))


def docs_url(name, system):
    return MAC_DOCS.get(name, TOOLS[name][3]) if system == "darwin" else TOOLS[name][3]


def plan(name, action, host):
    """One step for a tool and an action on a host: run, terminal, docs, error or sandbox."""
    if name not in TOOLS or action not in ACTIONS:
        raise ValueError("Unknown setup action.")
    _, winget, deb, _ = TOOLS[name]
    have = host.tools.__contains__
    update = action == "Update"

    def docs(reason):
        return dict(kind="docs", url=docs_url(name, host.system), reason=reason)

    def error(message):
        return dict(kind="error", message=message)

    def run(argv, **extra):
        return dict(kind="run", argv=argv, **extra)

    if action == "Docs":
        return docs("requested")
    if action == "Login":
        if name not in LOGIN:
            return error(f"{name} does not use an account sign-in.")
        return dict(kind="terminal", argv=list(LOGIN[name]), purpose="login")
    if name == "Sandbox image":
        return dict(kind="sandbox")
    if name == "llama.cpp / Strata":
        return run([sys.executable, str(ROOT / "setup_engines.py")] + (["--update"] if update else []))
    if name == "Docker" and host.system == "windows" and not have("docker"):
        return run(POWERSHELL + ["-File", str(ROOT / "setup-web.ps1")])
    if name == "vLLM":
        if host.system == "darwin":
            return error("vLLM does not run on macOS; use llama.cpp here or attach a Linux vLLM endpoint.")
        if host.system == "windows":
            return run(
                POWERSHELL
                + ["-File", str(ROOT / "setup-web.ps1"), "-Mode", "vllm"]
                + (["-Update"] if update else [])
            )
        return run(["sh", str(ROOT / "setup-vllm.sh")] + (["--update"] if update else []))
    if name == "OMP":
        if host.system == "windows":
            ref = "" if update else " -Ref " + OMP_REF
            return run(
                POWERSHELL
                + [
                    "-Command",
                    "$ErrorActionPreference='Stop'; $p=Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString()+'.ps1'); "
                    f"try {{ Invoke-WebRequest -UseBasicParsing '{OMP_SOURCE}.ps1' -OutFile $p; & $p -Binary{ref} }} "
                    "finally { Remove-Item -LiteralPath $p -ErrorAction SilentlyContinue }",
                ]
            )
        if not have("curl"):
            return error(CURL_MISSING)
        ref = "" if update else " --ref " + OMP_REF
        return run(
            [
                "sh",
                "-ec",
                "p=$(mktemp); trap 'rm -f \"$p\"' EXIT; "
                + f'curl -fsSL {OMP_SOURCE}.sh -o "$p"; sh "$p" --binary{ref}',
            ]
        )
    if name in NPM:
        if not have("npm"):
            return error(f"Install Node.js in Setup first, then install {name}.")
        prefix = ["--prefix", host.home + "/.local"] if host.npm_user_prefix and host.system != "windows" else []
        return run(["npm", "install", "-g", *prefix, NPM[name]])
    if name == "Hugging Face CLI":
        if not have("uv"):
            return error("Install uv in Setup first, then install the Hugging Face CLI.")
        return run(["uv", "tool", "install", "--upgrade", "huggingface-hub"])
    if host.system == "windows" and winget:
        if not have("winget"):
            return error("winget is missing; install App Installer from the Microsoft Store, then retry.")
        return run(
            [
                "winget",
                "upgrade" if update else "install",
                "--exact",
                "--id",
                winget,
                "--source",
                "winget",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ]
        )
    if host.system == "darwin" and name in BREW:
        if not have("brew"):
            return docs("no-brew")
        kind, token = BREW[name]
        verb = "upgrade" if update else "install"
        if kind == "cask":
            return dict(kind="terminal", argv=["brew", verb, "--cask", token], purpose="install")
        return run(["brew", verb, token], env={"HOMEBREW_NO_ENV_HINTS": "1"})
    if host.system == "linux":
        manager, packages = "", []
        if have("apt-get") and (deb or name == "Docker"):
            manager, packages = "apt-get", list(host.apt_docker) if name == "Docker" else deb.split()
        elif have("dnf") and name in DNF:
            manager, packages = "dnf", DNF[name].split()
        if manager and not packages:
            return docs("no-compose-v2")
        if manager:
            argv = [manager, "install", "-y", *packages]
            if host.root:
                return run(argv)
            if not have("sudo"):
                return error("Install sudo, or run this as root: " + " ".join(argv))
            return dict(kind="terminal", argv=["sudo", *argv], purpose="install")
    if name == "Bun" and host.system != "windows":
        if not have("curl"):
            return error(CURL_MISSING)
        return run(["sh", "-ec", 'p=$(mktemp); curl -fsSL https://bun.sh/install -o "$p"; bash "$p"; rm -f "$p"'])
    return docs("no-installer")


def hold(argv):
    """Keep the terminal open after a package install so its result stays readable. POSIX only."""
    return ["sh", "-c", shlex.join(argv) + HOLD]


def kill_tree(pid):
    try:
        parent = psutil.Process(pid)
        procs = parent.children(recursive=True) + [parent]
    except psutil.Error:
        return
    for p in procs:
        try:
            p.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(procs, timeout=5)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass


def tail(log, offset, limit=8192):
    with open(log, "rb") as f:
        f.seek(max(offset, os.fstat(f.fileno()).st_size - limit))
        return f.read().decode("utf8", "replace").replace("\x00", "")


def last_line(text):
    return next((v.strip()[:200] for v in reversed(text.splitlines()) if v.strip()), "")


def logged(argv, header, env=None, timeout=INSTALL_TIMEOUT, progress=None, cancel=None):
    """Run argv into setup.log. Returns (returncode, reason, text); reason is exit, timeout, cancelled, spawn or log."""
    log = DATA / "setup.log"
    try:
        if log.stat().st_size > 2_000_000:
            os.replace(log, log.with_name("setup.log.1"))
    except OSError:
        pass
    try:
        f = log.open("a", encoding="utf8")
    except OSError as exc:
        return None, "log", exc.strerror or str(exc)
    with f:
        f.write("\n" + time.strftime("%Y-%m-%d %H:%M:%S") + " " + header + "\n")
        f.flush()
        offset = log.stat().st_size
        try:
            proc = subprocess.Popen(
                argv,
                cwd=ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=f,
                stderr=subprocess.STDOUT,
                creationflags=QUIET,
            )
        except OSError as exc:
            return None, "spawn", exc.strerror or str(exc)
        began, seen, reason = time.monotonic(), "", "exit"
        while True:
            try:
                proc.wait(timeout=POLL)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancel is not None and cancel.is_set():
                reason = "cancelled"
            elif time.monotonic() - began > timeout:
                reason = "timeout"
            if reason != "exit":
                kill_tree(proc.pid)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                break
            line = last_line(tail(log, offset))
            if progress and line != seen:
                seen = line
                try:
                    progress(dict(message=line, elapsed=int(time.monotonic() - began)))
                except Exception:
                    pass
    return proc.returncode, reason, tail(log, offset)


def resolve(argv):
    return [find(argv[0]) or argv[0], *argv[1:]]


def marked(text):
    """The sentence a child handed over on its last MARK line, or ""."""
    lines = [v.strip() for v in text.splitlines()]
    return next((v[len(MARK):].strip() for v in reversed(lines) if v.startswith(MARK)), "")


def short(folder, home):
    """A folder with the home prefix shown as ~."""
    if folder == home:
        return "~"
    for sep in ("/", "\\"):
        if folder.startswith(home + sep):
            return "~" + folder[len(home):]
    return folder


def open_terminal(name, verb, step):
    """Start a sign-in or a password-prompting install in a terminal. Returns (started, sentence)."""
    login = step["purpose"] == "login"
    if login:
        try:
            argv = command(step["argv"][0]) + step["argv"][1:]
        except ValueError:
            return False, f"Install {name} first, then sign in."
    else:
        argv = hold(resolve(step["argv"]))
    try:
        terminal(argv)
    except ValueError as exc:
        return False, str(exc)
    except OSError:
        return False, NO_TERMINAL[SYSTEM]
    if login:
        return True, f"Complete the {name} sign-in in the terminal window that opened."
    return True, f"Finish the {name} {verb} in the terminal window that opened, then choose Recheck installations."


def build_sandbox():
    """Build the sandbox image. Returns "" on success, else one sentence."""
    import boxes

    try:
        boxes.build()
    except ValueError as exc:
        return str(exc)
    except (subprocess.SubprocessError, OSError):
        return "The sandbox image could not be built; start Docker, then retry."
    return ""


def execute(name, action, step, progress, cancel):
    """Run a captured step into the setup log. Returns "" on success, else one sentence."""
    verb = action.lower()
    argv = resolve(step["argv"])
    env = {**os.environ, **step["env"]} if step.get("env") else None
    wrapped = None
    if progress is not None:

        def wrapped(event):
            progress(dict(job="tool-setup", phase="running", name=name, action=action, **event))

    code, reason, text = logged(argv, action + " " + name, env, INSTALL_TIMEOUT, wrapped, cancel)
    if reason == "log":
        return (
            f"The setup log in {DATA} could not be opened ({text}); create that folder or make it writable, "
            "then retry."
        )
    if reason == "spawn":
        return f"{os.path.basename(argv[0])} could not be started ({text}); reinstall it, then retry."
    if reason == "cancelled":
        return f"{name} {verb} was cancelled; choose {action} to start it again."
    if reason == "timeout":
        return f"{name} {verb} was stopped after 60 minutes; check your internet connection, then try again."
    if code:
        return marked(text) or (
            f"{name} {verb} failed with exit code {code}; open the setup log to see the installer's error."
        )
    return ""


def run(name, action, progress=None, cancel=None):
    """Carry out one setup request. Returns a typed outcome: installed, opened-docs, started-terminal or failed."""
    refresh_path()
    step = plan(name, action, host_facts(name))
    out = dict(
        name=name,
        action=action,
        outcome="failed",
        message="",
        path="",
        hint="",
        url="",
        log=str(DATA / "setup.log"),
    )
    kind = step["kind"]
    if kind == "error":
        out["message"] = step["message"]
        return out
    if kind == "docs":
        out["url"] = step["url"]
        if webbrowser.open(step["url"]):
            out["outcome"] = "opened-docs"
            out["message"] = DOCS_MESSAGES[step["reason"]].format(name=name)
        else:
            out["message"] = f"No browser could be opened; visit {step['url']} to continue with {name}."
        return out
    if kind == "terminal":
        started, out["message"] = open_terminal(name, action.lower(), step)
        if started:
            out["outcome"] = "started-terminal"
        return out
    problem = build_sandbox() if kind == "sandbox" else execute(name, action, step, progress, cancel)
    if problem:
        out["message"] = problem
        return out
    refresh_path()
    d = detect(name)
    out.update(outcome="installed", path=d["path"], hint=d["hint"])
    if name == "Blender" and d["path"] and find("uvx"):
        try:
            import runtime

            runtime.ensure_blender_addon(d["path"], enable=True)
        except Exception:
            pass
    if not d["path"]:
        out["message"] = (
            f"{name} finished installing but was not found; restart Agent Workbench, then choose Recheck installations."
        )
    elif d["on_path"]:
        out["message"] = f"{name} is ready at {d['path']}."
    else:
        folder = short(os.path.dirname(d["path"]), str(Path.home()))
        out["message"] = f"{name} is ready at {d['path']}; add {folder} to your PATH to use it in your own terminals."
    return out


def setup(name, action):
    """run() for callers that expect a failure as an exception."""
    result = run(name, action)
    if result["outcome"] == "failed":
        raise RuntimeError(result["message"])
    return result


def docker_problem():
    """One sentence on why Docker cannot be used, or "" when Docker and Compose answer."""
    exe = find("docker")
    if not exe:
        if os.name == "nt" and location("Docker").startswith("WSL"):
            return ""
        return "Docker is not installed; install it in Advanced > Setup > Docker."
    code, text = quiet([exe, "info", "--format", "{{.ServerVersion}}"], 8)
    if code == 0:
        if quiet([exe, "compose", "version"], 8)[0] == 0:
            return ""
        return (
            "Docker Compose v2 is missing; install the Docker Compose plugin with Advanced > Setup > Docker > "
            "Install, then retry."
        )
    if "permission denied" in text.lower():
        return (
            'Your account cannot use the Docker socket; run "sudo usermod -aG docker $USER", sign out and back '
            "in, then retry (that group has root-level access)."
        )
    if os.name == "nt" and shutil.which("wsl") and wsl_probe("Docker"):
        return ""
    return (
        "Docker is installed but not running; start Docker Desktop (on Linux: sudo systemctl start docker), "
        "then retry."
    )
