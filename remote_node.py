"""Standalone SSH worker for a Linux x64 Vulkan inference node. Requires Python 3.8+ and curl."""

import fcntl
import hashlib
import json
import ipaddress
import shlex
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.request import Request, urlopen

BASE = Path.home() / ".local/share/agent-workbench-node"
MARKER = BASE / "owner-v1"
PORT = 10542
MODEL = "Inkling-2-Mini-UD-IQ3_XXS.gguf_file"
MODEL_URL = (
    "https://huggingface.co/unsloth/Inkling-2-Mini-GGUF/resolve/45fd08764f2b0bf16b03a5483a80e5c7aaec8772/Mainline_Support/"
    + MODEL
)
MODEL_SHA = "fd9ccedae17c1bb89744612fbb46730b5275f2a342133d0419776bd1ae311657"
# The catalog's 16 GB Vulkan pick for this file (vulkan-16-inkling-iq3): a 16K window with the
# default f16 cache, computed at 12.9 GiB. OMP's first request alone is about 13,400 tokens.
CONTEXT = 16384
ENGINE_URL = "https://github.com/ggml-org/llama.cpp/releases/download/b12633/llama-b12633-bin-ubuntu-vulkan-x64.tar.gz"
ENGINE_SHA = "33223bb1f421eb4120210fc61648acf1f7ba8deece1183c63af417c80a9ff013"


OMP_URL = "https://github.com/can1357/oh-my-pi/releases/download/v19.1.0/omp-linux-x64"
OMP_SHA = "be2b45c437b623e15c29b1f8c98aacb842f92118196454b5e9eed884359e9a01"
LOCK = Path.home() / ".agent-workbench-node.lock"
BUSY = "Another Workbench operation is still running on this machine; wait for it to finish, then retry."


def omp_installed():
    return bool(
        (BASE / "bin/omp").is_file()
        or shutil.which("omp")
        or (Path.home() / ".local/bin/omp").is_file()
    )


def unsupported():
    system = os.uname()
    if system.sysname != "Linux" or system.machine != "x86_64":
        return "The managed model needs a Linux x86_64 machine; attach a server that is already running on this machine instead."
    return ""


def owned():
    if not BASE.exists() and not BASE.is_symlink():
        raise ValueError(
            "The Workbench runtime is not installed on this machine; install the remote model first, then retry."
        )
    if (
        BASE.is_symlink()
        or not MARKER.is_file()
        or MARKER.read_text() != "agent-workbench-node-v1"
    ):
        raise ValueError(
            "~/.local/share/agent-workbench-node on this machine was not created by Workbench; move it away, then retry."
        )


def complete():
    owned()
    if not all(
        (BASE / name).is_file() for name in ("engine/llama-server", MODEL, "api-key")
    ):
        raise ValueError(
            "The remote model is not completely installed on this machine; install the remote model again, then retry."
        )


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url, dest, digest):
    if dest.exists():
        if sha256(dest) == digest:
            return
        raise ValueError(
            dest.name
            + " on this machine does not match its pinned checksum; use Remove remote runtime, then install again."
        )
    part = dest.with_suffix(".part")
    try:
        subprocess.run(
            [
                "curl",
                "--fail",
                "--location",
                "--retry",
                "3",
                "--connect-timeout",
                "30",
                "--speed-limit",
                "1024",
                "--speed-time",
                "60",
                "--continue-at",
                "-",
                "--silent",
                "--show-error",
                "-o",
                str(part),
                url + "?download=1",
            ],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError(
            f"The download of {dest.name} stopped (curl exit {exc.returncode}); check this machine's internet connection, then run the install again."
        ) from None
    if sha256(part) != digest:
        part.unlink()
        raise ValueError(
            "The download did not match its pinned checksum; run the install again."
        )
    part.replace(dest)


def extract(archive, dest):
    with tarfile.open(archive) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(dest, filter="data")
            return
        members = tar.getmembers()
        for member in members:
            names = [member.name] + (
                [member.linkname] if member.issym() or member.islnk() else []
            )
            if not (
                member.isfile() or member.isdir() or member.issym() or member.islnk()
            ) or any(n.startswith("/") or ".." in Path(n).parts for n in names):
                raise ValueError(
                    "The engine archive could not be unpacked safely with this Python; install Python 3.12 or newer on the machine, then retry."
                )
        tar.extractall(dest, members)


def install():
    if unsupported():
        raise ValueError(unsupported())
    if not shutil.which("curl"):
        raise ValueError(
            "curl was not found on this machine; install curl there, then retry."
        )
    if not BASE.exists():
        BASE.mkdir(mode=0o700, parents=True)
        MARKER.write_text("agent-workbench-node-v1")
    owned()
    if shutil.disk_usage(BASE).free < 13 * 1024**3 and not (BASE / MODEL).exists():
        raise ValueError(
            "This machine needs 13 GiB of free disk space in the home folder; free some space, then retry."
        )
    if not (BASE / "engine").exists():
        fetch(ENGINE_URL, BASE / "engine.tar.gz", ENGINE_SHA)
        with tempfile.TemporaryDirectory(dir=BASE) as tmp:
            extract(BASE / "engine.tar.gz", tmp)
            server = next(Path(tmp).rglob("llama-server"))
            server.parent.rename(BASE / "engine")
    fetch(MODEL_URL, BASE / MODEL, MODEL_SHA)
    if not (BASE / "api-key").exists():
        with os.fdopen(
            os.open(BASE / "api-key", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w"
        ) as f:
            f.write(secrets.token_urlsafe(40))
    if not omp_installed():
        binary = BASE / "bin/omp"
        binary.parent.mkdir(exist_ok=True)
        fetch(OMP_URL, binary, OMP_SHA)
        binary.chmod(0o700)
    return probe()


def pid():
    try:
        p, born = json.loads((BASE / "pid.json").read_text())
        proc = Path(f"/proc/{p}")
        if (proc / "exe").resolve() == (BASE / "engine/llama-server").resolve() and (
            proc / "stat"
        ).read_text().split(") ", 1)[1].split()[19] == born:
            return p
    except (OSError, ValueError, IndexError):
        pass


def memory():
    try:
        if os.uname().sysname == "Darwin":
            out = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=3,
            ).stdout
            return round(int(out) / 1024**3, 1)
        first = Path("/proc/meminfo").read_text().splitlines()[0]
        return round(int(first.split()[1]) / 1024**2, 1)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return 0.0


def gpu(ram):
    system = os.uname()
    if system.sysname == "Darwin":
        return ("apple", ram, True) if system.machine == "arm64" else ("", 0.0, False)
    cards = []
    for f in Path("/sys/class/drm").glob("card*/device/mem_info_vram_total"):
        try:
            cards.append(int(f.read_text()))
        except (OSError, ValueError):
            pass
    if cards:
        return "amd", round(max(cards) / 1024**3, 1), False
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run(
                [smi, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=4,
            ).stdout
            sizes = [int(x) for x in out.split()]
            if sizes:
                return "nvidia", round(max(sizes) / 1024, 1), False
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return "", 0.0, False


def firewall_hint():
    peer = os.environ.get("SSH_CONNECTION", "").split()
    if len(peer) != 4:
        return ""
    try:
        addr = ipaddress.ip_address(peer[0].split("%")[0])
        port = int(peer[3])
    except ValueError:
        return ""
    if addr.is_loopback:
        return ""
    network = str(addr) + ("/32" if addr.version == 4 else "/128")
    if shutil.which("firewall-cmd"):
        rule = f'rule family="ipv{addr.version}" source address="{network}" port port="{port}" protocol="tcp" accept'
        return (
            "sudo firewall-cmd --permanent --add-rich-rule="
            + shlex.quote(rule)
            + " && sudo firewall-cmd --reload"
        )
    if shutil.which("ufw"):
        return f"sudo ufw allow from {network} to any port {port} proto tcp"
    return ""


def probe():
    ram = memory()
    vendor, vram, unified = gpu(ram)
    system = os.uname()
    return dict(
        firewall=firewall_hint(),
        system=system.sysname,
        arch=system.machine,
        python=".".join(str(n) for n in sys.version_info[:3]),
        ram_gib=ram,
        gpu_gib=vram,
        gpu_vendor=vendor,
        unified=unified,
        managed=not unsupported(),
        reason=unsupported(),
        curl=bool(shutil.which("curl")),
        tmux=bool(shutil.which("tmux")),
        installed=(BASE / MODEL).is_file() and (BASE / "api-key").is_file(),
        running=bool(pid()),
        port=PORT,
        context=CONTEXT,
        omp=omp_installed(),
    )


def start():
    if unsupported():
        raise ValueError(unsupported())
    complete()
    if not pid():
        try:
            with socket.socket() as sock:
                # Let the server reuse its port while earlier connections are in TIME_WAIT.
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", PORT))
        except OSError:
            raise ValueError(
                f"Port {PORT} on this machine is already used by another program; stop that program, then retry."
            ) from None
        engine = BASE / "engine"
        with (BASE / "server.log").open("w") as log:
            child = subprocess.Popen(
                [
                    str(engine / "llama-server"),
                    "-m",
                    str(BASE / MODEL),
                    "-ngl",
                    "99",
                    "-c",
                    str(CONTEXT),
                    "--parallel",
                    "1",
                    "--alias",
                    "local-agent",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(PORT),
                    "--api-key-file",
                    str(BASE / "api-key"),
                ],
                env=dict(
                    os.environ,
                    LD_LIBRARY_PATH=str(engine)
                    + ":"
                    + os.getenv("LD_LIBRARY_PATH", ""),
                ),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        (BASE / "pid.json").write_text(
            json.dumps(
                [
                    child.pid,
                    Path(f"/proc/{child.pid}/stat")
                    .read_text()
                    .split(") ", 1)[1]
                    .split()[19],
                ]
            )
        )
    for _ in range(180):
        try:
            req = Request(
                f"http://127.0.0.1:{PORT}/v1/models",
                headers={"Authorization": "Bearer " + (BASE / "api-key").read_text()},
            )
            with urlopen(req, timeout=2) as r:
                served = json.load(r)["data"][0]
                if served["id"] == "local-agent":
                    info = probe()
                    # A server started by an older Workbench keeps its own window.
                    meta = served.get("meta")
                    size = meta.get("n_ctx") if isinstance(meta, dict) else None
                    if isinstance(size, int) and not isinstance(size, bool) and size > 0:
                        info["context"] = size
                    return info
        except OSError:
            pass
        if not pid():
            lines = [
                x.strip()
                for x in (BASE / "server.log").read_text(errors="replace").splitlines()
                if x.strip()
            ]
            raise ValueError(
                "The remote model stopped while loading ("
                + (lines[-1][:300] if lines else "no log output")
                + "); check the GPU driver and free memory on that machine, then retry."
            )
        time.sleep(1)
    raise ValueError(
        "The remote model did not start within 3 minutes; use Check connection, then retry."
    )


def stop():
    if not BASE.exists() and not BASE.is_symlink():
        return probe()
    owned()
    if p := pid():
        os.kill(p, signal.SIGTERM)
        for _ in range(40):
            if not pid():
                break
            time.sleep(0.25)
        if pid():
            raise ValueError(
                "The remote model is still stopping; wait a few seconds, then retry."
            )
    return probe()


def locked(path, wait=60.0):
    lock = path.open("a")
    end = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock
        except OSError:
            if time.monotonic() >= end:
                lock.close()
                raise ValueError(BUSY) from None
            time.sleep(0.5)


def main():
    action = sys.argv[1]
    if action == "probe":
        info = probe()
        info["serving"] = False
        if info["running"]:
            try:
                with urlopen(f"http://127.0.0.1:{PORT}/health", timeout=1) as reply:
                    info["serving"] = json.load(reply).get("status") == "ok"
            except (OSError, ValueError):
                pass
        return info
    with locked(LOCK):
        if action == "install":
            return install()
        if action == "start":
            return start()
        if action == "omp-config":
            complete()
            directory = BASE / "omp-agent"
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            model = dict(
                id="local-agent",
                name="Inkling IQ3",
                contextWindow=CONTEXT,
                maxTokens=4096,
                supportsTools=True,
            )
            config = {
                "providers": {
                    "workbench": dict(
                        api="openai-completions",
                        baseUrl=f"http://127.0.0.1:{PORT}/v1",
                        apiKey=(BASE / "api-key").read_text(),
                        models=[model],
                    )
                }
            }
            with tempfile.NamedTemporaryFile(
                mode="w", dir=directory, delete=False
            ) as tmp:
                json.dump(config, tmp)
            os.replace(tmp.name, directory / "models.yml")
            if not (directory / "config.yml").exists():
                (directory / "config.yml").write_text(
                    json.dumps(
                        dict(symbolPreset="ascii", startup=dict(setupWizard=False))
                    )
                )
            return True
        if action == "stop":
            return stop()
        if action == "key":
            complete()
            return (BASE / "api-key").read_text().strip()
        if action == "remove":
            if not BASE.exists() and not BASE.is_symlink():
                return probe()
            stop()
            owned()
            shutil.rmtree(BASE)
            return probe()
        raise ValueError("Unknown node action.")


if __name__ == "__main__":
    try:
        print(json.dumps(main()))
    except Exception as exc:
        sys.exit(str(exc))
