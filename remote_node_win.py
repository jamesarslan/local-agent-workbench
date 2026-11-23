"""Standalone SSH worker for a Windows x64 NVIDIA CUDA inference node. Requires Python 3.8+.

The Mac side sends this file over SSH and passes the engine and model it chose as a
base64 JSON plan, so the pinned URLs and SHA-256 values live in one place there.
"""

import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen
import zipfile

if "override" not in globals():
    try:
        from chat_templates import override
    except ImportError:
        override = None

LOCAL = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
BASE = LOCAL / "agent-workbench-node"
MARKER = BASE / "owner-v1"
LOCK = LOCAL / "agent-workbench-node.lock"
PORT = 10542
SERVER = "llama-server.exe"
MIN_DRIVER = 580
BUSY = "Another Workbench operation is still running on this machine; wait for it to finish, then retry."
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000
QUERY = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
TERMINATE = 0x0001
STILL_ACTIVE = 259
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
FOLDERS = (
    "models",
    "Downloads",
    ".lmstudio/models",
    ".cache/lm-studio/models",
    ".cache/huggingface/hub",
    ".cache/llama.cpp",
)


def quiet():
    return CREATE_NO_WINDOW if os.name == "nt" else 0


def plan(arg):
    try:
        doc = json.loads(base64.b64decode(arg.encode(), validate=True).decode())
        model, engine = doc["model"], doc["engine"]
        for item in [model, *engine]:
            if not (
                isinstance(item["url"], str)
                and item["url"].startswith("https://")
                and isinstance(item["sha256"], str)
                and len(item["sha256"]) == 64
                and isinstance(item["bytes"], int)
                and isinstance(item["name"], str)
                and NAME.fullmatch(item["name"])
            ):
                raise ValueError
        if not isinstance(model["context"], int) or not engine:
            raise ValueError
        return doc
    except (ValueError, KeyError, TypeError, IndexError):
        raise ValueError(
            "Workbench sent an install plan this machine could not read; update Workbench, then retry."
        ) from None


def nvidia():
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        out = subprocess.run(
            [
                smi,
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            stdin=subprocess.DEVNULL,
            creationflags=quiet(),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    cards = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            cards.append((int(parts[1]), parts[0], parts[2]))
        except ValueError:
            continue
    if not cards:
        return None
    mib, name, driver = max(cards)
    return dict(name=name, gib=round(mib / 1024, 1), driver=driver)


def unsupported(card=None):
    if os.name != "nt":
        return "This worker runs on Windows only."
    if os.environ.get("PROCESSOR_ARCHITECTURE", "").upper() not in ("AMD64", "X86_64"):
        return "The managed model needs a 64-bit x86 Windows machine; attach a server that is already running on this machine instead."
    if card is None:
        return "The managed model on Windows needs an NVIDIA GPU with its driver installed; attach a server that is already running on this machine instead."
    try:
        major = int(card["driver"].split(".")[0])
    except (ValueError, IndexError):
        major = 0
    if major < MIN_DRIVER:
        return f"The NVIDIA driver on this machine ({card['driver']}) is older than {MIN_DRIVER}; update it for CUDA 13, then retry."
    return ""


def owned():
    if not BASE.exists():
        raise ValueError(
            "The Workbench runtime is not installed on this machine; install the remote model first, then retry."
        )
    if (
        BASE.is_symlink()
        or not MARKER.is_file()
        or MARKER.read_text() != "agent-workbench-node-v1"
    ):
        raise ValueError(
            str(BASE)
            + " on this machine was not created by Workbench; move it away, then retry."
        )


def chosen():
    try:
        doc = json.loads((BASE / "model.json").read_text())
        if (
            isinstance(doc.get("name"), str)
            and NAME.fullmatch(doc["name"])
            and isinstance(doc.get("context"), int)
        ):
            return doc
    except (OSError, ValueError, AttributeError):
        pass
    return None


def complete():
    owned()
    model = chosen()
    if not (
        model
        and (BASE / "engine" / SERVER).is_file()
        and (BASE / model["name"]).is_file()
        and (BASE / "api-key").is_file()
    ):
        raise ValueError(
            "The remote model is not completely installed on this machine; install the remote model again, then retry."
        )
    return model


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url, part, size):
    for attempt in range(4):
        have = part.stat().st_size if part.exists() else 0
        if have >= size:
            return
        request = Request(url, headers={"Range": "bytes=%d-" % have} if have else {})
        try:
            with urlopen(request, timeout=60) as reply:
                if have and reply.status != 206:
                    have = 0
                with open(part, "ab" if have else "wb") as out:
                    shutil.copyfileobj(reply, out, 1 << 22)
            return
        except OSError:
            if attempt == 3:
                raise ValueError(
                    "The download of "
                    + part.stem
                    + " stopped; check this machine's internet connection, then run the install again."
                ) from None
            time.sleep(3)


def fetch(item, dest):
    if dest.exists():
        if sha256(dest) == item["sha256"]:
            return
        raise ValueError(
            dest.name
            + " on this machine does not match its pinned checksum; use Remove remote runtime, then install again."
        )
    part = dest.with_name(dest.name + ".part")
    download(item["url"], part, item["bytes"])
    if sha256(part) != item["sha256"]:
        part.unlink()
        raise ValueError(
            "The download of "
            + dest.name
            + " did not match its pinned checksum; run the install again."
        )
    os.replace(part, dest)


def unzip(archive, dest):
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            parts = Path(name.replace("\\", "/")).parts
            if name.startswith(("/", "\\")) or ":" in name or ".." in parts:
                raise ValueError(
                    archive.name + " contains an unsafe path; it was not unpacked."
                )
        z.extractall(dest)


def install_engine(engine):
    stamp = BASE / "engine.json"
    wanted = [item["sha256"] for item in engine]
    try:
        if json.loads(stamp.read_text()) == wanted and (BASE / "engine" / SERVER).is_file():
            return
    except (OSError, ValueError):
        pass
    archives = []
    for item in engine:
        archive = BASE / item["name"]
        fetch(item, archive)
        archives.append(archive)
    shutil.rmtree(BASE / "engine", ignore_errors=True)
    with tempfile.TemporaryDirectory(dir=BASE) as tmp:
        unzip(archives[0], tmp)
        server = next(Path(tmp).rglob(SERVER), None)
        if server is None:
            raise ValueError("The engine archive has no " + SERVER + "; run the install again.")
        os.replace(server.parent, BASE / "engine")
    for archive in archives[1:]:
        unzip(archive, BASE / "engine")
    stamp.write_text(json.dumps(wanted))
    for archive in archives:
        archive.unlink()


def candidates(model, extra=()):
    """Files on this machine with the planned model's exact size, without reading them."""
    home = Path.home()
    roots = [Path(p) for p in extra] + [home / f for f in FOLDERS]
    found, seen, budget = [], set(), 40000
    for root in roots:
        if root.is_file():
            walk = [(str(root.parent), [], [root.name])]
        elif root.is_dir():
            walk = os.walk(root)
        else:
            continue
        top = len(root.parts)
        for folder, dirs, files in walk:
            budget -= len(dirs) + len(files)
            if budget < 0:
                return found
            if len(Path(folder).parts) - top >= 4:
                dirs[:] = []
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in files:
                path = Path(folder) / name
                try:
                    if path.stat().st_size != model["bytes"] or BASE in path.parents:
                        continue
                    key = os.path.normcase(str(path.resolve()))
                except OSError:
                    continue
                if key not in seen:
                    seen.add(key)
                    found.append(str(path))
    stem = model["name"].split(".")[0].lower()
    found.sort(key=lambda p: Path(p).name.split(".")[0].lower() != stem)
    return found[:20]


def adopt(source, dest, model):
    """Use a file already on this machine when its SHA-256 matches; a hard link costs no space."""
    path = Path(os.path.expandvars(os.path.expanduser(source)))
    if not path.is_file():
        raise ValueError(f"{source} is not a file on this machine; check the path, then retry.")
    if path.stat().st_size != model["bytes"] or sha256(path) != model["sha256"]:
        raise ValueError(
            f"{source} is not the {model.get('label') or model['name']} file Workbench expects "
            "(its size or SHA-256 differs); choose another file or let Workbench download it."
        )
    part = dest.with_name(dest.name + ".part")
    part.unlink(missing_ok=True)
    try:
        os.link(path, part)
        how = "linked"
    except OSError:
        shutil.copyfile(path, part)
        how = "copied"
    os.replace(part, dest)
    return how


def install(doc):
    card = nvidia()
    if unsupported(card):
        raise ValueError(unsupported(card))
    model, engine = doc["model"], doc["engine"]
    if not BASE.exists():
        BASE.mkdir(parents=True)
        MARKER.write_text("agent-workbench-node-v1")
    owned()
    dest = BASE / model["name"]
    need = sum(item["bytes"] for item in engine) * 2 + (0 if dest.exists() else model["bytes"])
    if shutil.disk_usage(BASE).free < need + 2 * 1024**3 and not doc.get("source"):
        raise ValueError(
            "This machine needs %d GiB of free disk space in %s; free some space, then retry."
            % (need // 1024**3 + 2, LOCAL)
        )
    install_engine(engine)
    origin = "kept"
    if not dest.exists():
        if doc.get("source"):
            origin = adopt(doc["source"], dest, model)
        else:
            origin = "downloaded"
            for path in candidates(model):
                try:
                    origin = adopt(path, dest, model)
                    break
                except (ValueError, OSError):
                    continue
            else:
                fetch(model, dest)
    elif sha256(dest) != model["sha256"]:
        raise ValueError(
            dest.name
            + " on this machine does not match its pinned checksum; use Remove remote runtime, then install again."
        )
    previous = chosen()
    (BASE / "model.json").write_text(
        json.dumps({k: model.get(k) for k in ("name", "sha256", "bytes", "context", "label", "id")})
    )
    if previous and previous["name"] != model["name"]:
        (BASE / previous["name"]).unlink(missing_ok=True)
    key = BASE / "api-key"
    if not key.exists():
        key.write_text(secrets.token_urlsafe(40))
    info = probe()
    info["model_source"] = origin
    return info


def _kernel():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def process(pid):
    """(creation time, image path, alive) of a process, or None."""
    k = _kernel()
    k.OpenProcess.restype = ctypes.c_void_p
    handle = k.OpenProcess(QUERY, False, int(pid))
    if not handle:
        return None
    try:
        times = [ctypes.c_ulonglong() for _ in range(4)]
        if not k.GetProcessTimes(ctypes.c_void_p(handle), *[ctypes.byref(t) for t in times]):
            return None
        size = ctypes.c_ulong(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not k.QueryFullProcessImageNameW(ctypes.c_void_p(handle), 0, buf, ctypes.byref(size)):
            return None
        code = ctypes.c_ulong()
        k.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
        return times[0].value, buf.value, code.value == STILL_ACTIVE
    finally:
        k.CloseHandle(ctypes.c_void_p(handle))


def pid():
    try:
        p, born = json.loads((BASE / "pid.json").read_text())
        seen = process(p)
        server = os.path.normcase(str((BASE / "engine" / SERVER).resolve()))
        if seen and seen[2] and seen[0] == born and os.path.normcase(seen[1]) == server:
            return p
    except (OSError, ValueError, TypeError):
        pass
    return None


def omp_installed():
    return bool(shutil.which("omp"))


def probe():
    card = nvidia()
    model = chosen()
    reason = unsupported(card)
    return dict(
        system="Windows",
        arch=os.environ.get("PROCESSOR_ARCHITECTURE", ""),
        python=".".join(str(n) for n in sys.version_info[:3]),
        gpu_gib=card["gib"] if card else 0.0,
        gpu_vendor="nvidia" if card else "",
        gpu_name=card["name"] if card else "",
        driver=card["driver"] if card else "",
        managed=not reason,
        reason=reason,
        installed=bool(
            model
            and (BASE / model["name"]).is_file()
            and (BASE / "api-key").is_file()
            and (BASE / "engine" / SERVER).is_file()
        ),
        model=(model or {}).get("label") or (model or {}).get("name") or "",
        model_file=(model or {}).get("name") or "",
        context=(model or {}).get("context") or 0,
        running=bool(pid()),
        port=PORT,
        omp=omp_installed(),
        folder=str(BASE),
    )


def log_tail(lines=40):
    try:
        text = (BASE / "server.log").read_text(errors="replace")
    except OSError:
        return []
    return [x.rstrip() for x in text.splitlines() if x.strip()][-lines:]


def start():
    card = nvidia()
    if unsupported(card):
        raise ValueError(unsupported(card))
    model = complete()
    if not pid():
        try:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", PORT))
        except OSError:
            raise ValueError(
                f"Port {PORT} on this machine is already used by another program; stop that program, then retry."
            ) from None
        engine = BASE / "engine"
        argv = [
            str(engine / SERVER),
            "-m",
            str(BASE / model["name"]),
            "-ngl",
            "99",
            "-c",
            str(model["context"]),
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
        ]
        template = override(BASE / model["name"], BASE / "templates") if override else None
        if template:
            argv += ["--jinja", "--chat-template-file", str(template)]
        with (BASE / "server.log").open("w") as log:
            try:
                # The SSH session's job object would end the server with the session; break away from it.
                child = subprocess.Popen(
                    argv,
                    cwd=str(engine),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    creationflags=DETACHED_PROCESS
                    | CREATE_NEW_PROCESS_GROUP
                    | CREATE_BREAKAWAY_FROM_JOB,
                )
            except OSError:
                raise ValueError(
                    "Windows did not let Workbench start the model outside this SSH session; "
                    "check that the SSH server is the Windows OpenSSH service, then retry."
                ) from None
        seen = process(child.pid)
        (BASE / "pid.json").write_text(json.dumps([child.pid, seen[0] if seen else 0]))
    key = (BASE / "api-key").read_text().strip()
    for _ in range(240):
        try:
            req = Request(
                f"http://127.0.0.1:{PORT}/v1/models",
                headers={"Authorization": "Bearer " + key},
            )
            with urlopen(req, timeout=2) as r:
                if json.load(r)["data"][0]["id"] == "local-agent":
                    return probe()
        except (OSError, ValueError, KeyError, IndexError):
            pass
        if not pid():
            lines = log_tail(1)
            raise ValueError(
                "The remote model stopped while loading ("
                + (lines[-1][:300] if lines else "no log output")
                + "); check the GPU driver and free GPU memory on that machine, then retry."
            )
        time.sleep(1)
    raise ValueError(
        "The remote model did not start within 4 minutes; use Check connection, then retry."
    )


def stop():
    if not BASE.exists():
        return probe()
    owned()
    p = pid()
    if p:
        k = _kernel()
        k.OpenProcess.restype = ctypes.c_void_p
        handle = k.OpenProcess(TERMINATE | QUERY, False, int(p))
        if handle:
            k.TerminateProcess(ctypes.c_void_p(handle), 1)
            k.CloseHandle(ctypes.c_void_p(handle))
        for _ in range(40):
            if not pid():
                break
            time.sleep(0.25)
        if pid():
            raise ValueError(
                "The remote model is still stopping; wait a few seconds, then retry."
            )
    return probe()


class locked:
    def __init__(self, path, wait=60.0):
        self.path, self.wait = path, wait

    def __enter__(self):
        import msvcrt

        self.file = open(self.path, "a+b")
        end = time.monotonic() + self.wait
        while True:
            try:
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
                return self
            except OSError:
                if time.monotonic() >= end:
                    self.file.close()
                    raise ValueError(BUSY) from None
                time.sleep(0.5)

    def __exit__(self, *exc):
        import msvcrt

        try:
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self.file.close()


def omp_config():
    model = complete()
    directory = BASE / "omp-agent"
    directory.mkdir(parents=True, exist_ok=True)
    entry = dict(
        id="local-agent",
        name=model.get("label") or "Inkling",
        contextWindow=model["context"],
        maxTokens=4096,
        supportsTools=True,
    )
    config = {
        "providers": {
            "workbench": dict(
                api="openai-completions",
                baseUrl=f"http://127.0.0.1:{PORT}/v1",
                apiKey=(BASE / "api-key").read_text().strip(),
                models=[entry],
            )
        }
    }
    with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as tmp:
        json.dump(config, tmp)
    os.replace(tmp.name, directory / "models.yml")
    if not (directory / "config.yml").exists():
        (directory / "config.yml").write_text(
            json.dumps(dict(symbolPreset="ascii", startup=dict(setupWizard=False)))
        )
    return True


def main(argv):
    action = argv[1] if len(argv) > 1 else ""
    arg = argv[2] if len(argv) > 2 else ""
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
    if action == "files":
        doc = plan(arg)
        return candidates(doc["model"], [doc["source"]] if doc.get("source") else [])
    if action == "logs":
        return log_tail(60)
    with locked(LOCK):
        if action == "install":
            return install(plan(arg))
        if action == "start":
            return start()
        if action == "omp-config":
            return omp_config()
        if action == "stop":
            return stop()
        if action == "key":
            complete()
            return (BASE / "api-key").read_text().strip()
        if action == "remove":
            if not BASE.exists():
                return probe()
            stop()
            owned()
            shutil.rmtree(BASE)
            return probe()
        raise ValueError("Unknown node action.")


if __name__ == "__main__":
    try:
        print(json.dumps(main(sys.argv)))
    except Exception as exc:
        sys.exit(str(exc))
