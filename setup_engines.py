"""Install the pinned llama.cpp build for this computer's GPU, and Strata where it can run."""

import argparse
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from collections import namedtuple
from functools import lru_cache
from pathlib import Path

from filelock import FileLock, Timeout
import requests

from runtime import (
    DATA,
    QUIET,
    Cancelled,
    cancelled,
    load,
    owned,
    pause,
    system,
    tell,
    write_json,
)

Asset = namedtuple("Asset", "repo tag name target sha256 size")
LLAMA_REPO, LLAMA_TAG, LLAMA_TARGET = (
    "ggml-org/llama.cpp",
    "b12633",
    "llama-native-b12633",
)
STRATA_REPO, STRATA_TAG, STRATA_TARGET = (
    "Niko1221/Strata",
    "v0.2.19",
    "strata-inkling-0.1.65",
)
STRATA_COMMIT = "c6f6427d9de9016fd92d13e29740f65625261df7"
STRATA_URL = "https://github.com/Niko1221/Strata.git"
LABEL = {"cuda": "CUDA", "vulkan": "Vulkan", "cpu": "CPU", "metal": "Metal"}
MARK = "workbench-setup: "
PROGRAM = {LLAMA_REPO: "llama-server", STRATA_REPO: "strata-inkling"}


def _llama(name, sha256, size):
    return Asset(LLAMA_REPO, LLAMA_TAG, name, LLAMA_TARGET, sha256, size)


def _strata(name, sha256, size):
    return Asset(STRATA_REPO, STRATA_TAG, name, STRATA_TARGET, sha256, size)


PINNED = {
    ("windows", "cuda"): [
        _llama(
            "llama-b12633-bin-win-cuda-13.5-x64.zip",
            "5fadbb7d1f4b7e612f77c7b626c60445d5d1fc9d990425ee7f37d5f17a8c68ad",
            225649717,
        ),
        _llama(
            "cudart-llama-bin-win-cuda-13.5-x64.zip",
            "c2bf4cc202bfbaaa7a0b75f5082457c3811326947bb216059b72839d844e9cba",
            426314199,
        ),
    ],
    ("windows", "vulkan"): [
        _llama(
            "llama-b12633-bin-win-vulkan-x64.zip",
            "720d59998a51423a1c50c99326a5d5358b80e89d75f8fdafc50a26479d1bad68",
            38877434,
        )
    ],
    ("windows", "cpu"): [
        _llama(
            "llama-b12633-bin-win-cpu-x64.zip",
            "bad9fe0c1b554bcb1ce5630d66ad34a2285a8b390400513cc02f56feafd62c8e",
            25038006,
        )
    ],
    ("linux", "cuda"): [
        _llama(
            "llama-b12633-bin-ubuntu-cuda-13.5-x64.tar.gz",
            "d1078e7581e9a681c2b2cce0ba8ca841ef554b242627425a1d7ee0a0bbfb5267",
            224449189,
        ),
        _llama(
            "cudart-llama-b12633-bin-ubuntu-cuda-13.5-x64.tar.gz",
            "96bc888cb8ad7b19e3c9f67050258af16860b47e74124e274128337c110cba6e",
            444078909,
        ),
    ],
    ("linux", "vulkan"): [
        _llama(
            "llama-b12633-bin-ubuntu-vulkan-x64.tar.gz",
            "33223bb1f421eb4120210fc61648acf1f7ba8deece1183c63af417c80a9ff013",
            36909894,
        )
    ],
    ("linux", "cpu"): [
        _llama(
            "llama-b12633-bin-ubuntu-x64.tar.gz",
            "9c4a2c1bd1437f22831e0b56cc75329b321bac87225ffb5c83cd8d13b1f44fa2",
            23225608,
        )
    ],
    ("macos", "metal"): [
        _llama(
            "llama-b12633-bin-macos-arm64.tar.gz",
            "99657876053df9004753d92075813bbb3b484e79f8a402809b51d00c8c540062",
            16600069,
        )
    ],
}
STRATA_ASSETS = {
    "windows": _strata(
        "strata-inkling-0.1.65-windows-x86_64-cuda-cuda13.tar.gz",
        "484e7d39d8767b40ac3a946432effaf3874e8ff04660c873a0fe97d6d758a5df",
        411426893,
    ),
    "linux": _strata(
        "strata-inkling-0.1.65-linux-x86_64-cuda-cuda13.tar.gz",
        "b5131c902915851098dce383c88b1c4a7418aa2a5047892882e86f6a369dbf86",
        432654826,
    ),
}

VENDORS = {"10de": "nvidia", "1002": "amd", "8086": "intel"}
WSL_NVIDIA_SMI = Path("/usr/lib/wsl/lib/nvidia-smi")
DISPLAY_CLASS = (
    r"SYSTEM\CurrentControlSet\Control\Class"
    r"\{4d36e968-e325-11ce-bfc1-08002be10318}"
)
VULKAN_PATHS = (
    "/usr/lib/x86_64-linux-gnu/libvulkan.so.1",
    "/usr/lib64/libvulkan.so.1",
    "/usr/lib/libvulkan.so.1",
)
SAFE_NAME = re.compile(r"[A-Za-z0-9._-]+")
SCRATCH = re.compile(r"engine-[a-z0-9_]{8}")
CANCELLED = "Engine setup was cancelled."
SINGLE = {
    ("macos", "metal"): "llama-{tag}-bin-macos-arm64.tar.gz",
    ("windows", "vulkan"): "llama-{tag}-bin-win-vulkan-x64.zip",
    ("windows", "cpu"): "llama-{tag}-bin-win-cpu-x64.zip",
    ("linux", "vulkan"): "llama-{tag}-bin-ubuntu-vulkan-x64.tar.gz",
    ("linux", "cpu"): "llama-{tag}-bin-ubuntu-x64.tar.gz",
}
CUDA_PAIR = {
    "windows": (
        "llama-{tag}-bin-win-cuda-13.{n}-x64.zip",
        "cudart-llama-bin-win-cuda-13.{n}-x64.zip",
    ),
    "linux": (
        "llama-{tag}-bin-ubuntu-cuda-13.{n}-x64.tar.gz",
        "cudart-llama-{tag}-bin-ubuntu-cuda-13.{n}-x64.tar.gz",
    ),
}


def nvidia_gpu():
    exe = shutil.which("nvidia-smi")
    if not exe and WSL_NVIDIA_SMI.is_file():
        exe = str(WSL_NVIDIA_SMI)
    if not exe:
        return ""
    try:
        done = subprocess.run(
            [exe, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=5,
            stdin=subprocess.DEVNULL,
            creationflags=QUIET,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if done.returncode:
        return ""
    return next((v.strip() for v in done.stdout.splitlines() if v.strip()), "")


def linux_adapters(root=Path("/sys/class/drm")):
    found = set()
    try:
        files = list(Path(root).glob("card*/device/vendor"))
    except OSError:
        return found
    for path in files:
        try:
            code = path.read_text().strip().lower().removeprefix("0x")
        except (OSError, ValueError):
            continue
        if code in VENDORS:
            found.add(VENDORS[code])
    return found


def windows_adapters():
    rows = []
    try:
        import winreg

        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, DISPLAY_CLASS)
    except (ImportError, OSError):
        return rows
    with root:
        for index in range(64):
            try:
                name = winreg.EnumKey(root, index)
            except OSError:
                break
            if not re.fullmatch(r"\d{4}", name):
                continue
            try:
                with winreg.OpenKey(root, name) as node:
                    device = winreg.QueryValueEx(node, "MatchingDeviceId")[0]
                    try:
                        label = winreg.QueryValueEx(node, "DriverDesc")[0]
                    except OSError:
                        label = ""
            except OSError:
                continue
            rows.append((str(device), str(label)))
    return rows


def vendors_from(rows):
    found = set()
    for device, _ in rows:
        match = re.search(r"ven_(10de|1002|8086)", str(device), re.I)
        if match:
            found.add(VENDORS[match[1].lower()])
    return found


def vulkan_loader(name=None):
    name = name or system()
    if name == "windows":
        base = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        return (base / "System32" / "vulkan-1.dll").is_file()
    if name != "linux":
        return False
    if any(Path(v).exists() for v in VULKAN_PATHS):
        return True
    try:
        import ctypes.util

        return ctypes.util.find_library("vulkan") is not None
    except Exception:
        return False


def choose_backend(name, nvidia, vendors, vulkan):
    if nvidia:
        return "cuda", "nvidia", "NVIDIA GPU detected, so the CUDA build is used."
    if "amd" in vendors and vulkan:
        return "vulkan", "amd", "AMD GPU detected, so the Vulkan build is used."
    if "nvidia" in vendors and vulkan:
        return (
            "vulkan",
            "nvidia",
            "An NVIDIA GPU is present but its driver did not respond, so the Vulkan "
            "build is used; install the NVIDIA driver and run engine setup again to "
            "switch to CUDA.",
        )
    if "intel" in vendors and vulkan:
        return "vulkan", "intel", "Intel GPU detected, so the Vulkan build is used."
    if vendors:
        return (
            "cpu",
            next((v for v in ("amd", "nvidia", "intel") if v in vendors), ""),
            "A GPU was found but no Vulkan loader, so the CPU build is used; install "
            "the GPU driver (on Linux also the Vulkan loader package) and run engine "
            "setup again to use the GPU.",
        )
    return "cpu", "", "No supported GPU was detected, so the CPU build is used."


def rosetta():
    try:
        done = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "hw.optional.arm64"],
            capture_output=True,
            text=True,
            timeout=2,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.stdout.strip() == "1"


@lru_cache(maxsize=1)
def detect():
    name, arch = system(), platform.machine().lower()

    def unsupported(reason):
        return dict(
            system=name,
            arch=arch,
            supported=False,
            backend="",
            vendor="",
            gpu="",
            reason=reason,
        )

    if name == "macos":
        if arch == "arm64":
            return dict(
                system=name,
                arch=arch,
                supported=True,
                backend="metal",
                vendor="apple",
                gpu="Apple Silicon",
                reason="Apple Silicon detected, so the Metal build is used.",
            )
        if rosetta():
            return unsupported(
                "This Python runs under Rosetta; open Agent Workbench with "
                '"Launch Workbench.command" in a native Terminal so the Metal engine '
                "can be installed."
            )
        return unsupported(
            "Managed engines need an Apple Silicon Mac; attach a running endpoint in "
            "Connections on this Mac instead."
        )
    if arch not in ("x86_64", "amd64"):
        return unsupported(
            "Managed engines cover Windows x64, Linux x86_64 and Apple Silicon; attach "
            "a running endpoint in Connections on this computer instead."
        )
    gpu = nvidia_gpu()
    rows = windows_adapters() if name == "windows" else []
    vendors = vendors_from(rows) if name == "windows" else linux_adapters()
    backend, vendor, reason = choose_backend(
        name, bool(gpu), vendors, vulkan_loader(name)
    )
    if not gpu and vendor:
        gpu = next((d for i, d in rows if d and vendors_from([(i, d)]) == {vendor}), "")
    return dict(
        system=name,
        arch="x86_64",
        supported=True,
        backend=backend,
        vendor=vendor,
        gpu=gpu,
        reason=reason,
    )


def resolve_backend(requested=None):
    found = detect()
    if not found["supported"]:
        raise ValueError(found["reason"])
    if requested in (None, "", "auto"):
        return found["backend"], found["reason"]
    mac = found["system"] == "macos"
    if requested not in (("metal",) if mac else ("cuda", "vulkan", "cpu")):
        raise ValueError(
            "Only the Metal build runs on this Mac; leave the engine build on auto."
            if mac
            else "Choose auto, cuda, vulkan or cpu for the engine build."
        )
    return requested, f"The {LABEL[requested]} build is used because you chose it."


def wanted(backend, name, strata):
    return list(PINNED[(name, backend)]) + ([STRATA_ASSETS[name]] if strata else [])


def marker(path):
    try:
        return Path(path).read_text().strip()
    except (OSError, ValueError):
        return ""


def complete(folder, assets):
    return all(
        marker(Path(folder) / (a.name + ".installed")) == "sha256:" + a.sha256
        for a in assets
    )


def installed_backend():
    folder, name = DATA / "runtime" / LLAMA_TARGET, system()
    return next(
        (
            backend
            for (kind, backend), assets in PINNED.items()
            if kind == name and complete(folder, assets)
        ),
        "",
    )


def plan(backend=None):
    found = detect()
    if not found["supported"]:
        return found | dict(
            tag=LLAMA_TAG,
            assets=[],
            download_bytes=0,
            installed_backend="",
            engine_ready=False,
            strata=False,
            notes=[],
        )
    import runtime

    chosen, reason = resolve_backend(backend)
    strata, notes = strata_choice(chosen, found["system"], None)
    rows = [
        dict(
            name=a.name,
            size=a.size,
            installed=complete(DATA / "runtime" / a.target, [a]),
        )
        for a in wanted(chosen, found["system"], strata)
    ]
    try:
        runtime.runtime(LLAMA_TARGET, "llama-server.exe")
        ready = True
    except ValueError:
        ready = False
    return found | dict(
        backend=chosen,
        reason=reason,
        tag=LLAMA_TAG,
        assets=rows,
        download_bytes=sum(v["size"] for v in rows if not v["installed"]),
        installed_backend=installed_backend(),
        engine_ready=ready,
        strata=strata,
        notes=notes,
    )


def info(backend=None):
    import runtime

    return plan(backend) | {
        "engines": runtime.available_engines(),
        "support": runtime.engine_support(),
    }


def fetch(asset, folder, progress=None, cancel=None, index=1, count=1):
    url = f"https://github.com/{asset.repo}/releases/download/{asset.tag}/{asset.name}"
    path = Path(folder) / asset.name
    digest, done, shown = hashlib.sha256(), 0, -1.0

    def report(final=False):
        nonlocal shown
        now = time.monotonic()
        if final or now - shown >= 0.25:
            shown = now
            tell(
                progress,
                job="engine-install",
                phase="download",
                asset=asset.name,
                done=done,
                total=asset.size,
                index=index,
                count=count,
                message=f"Downloading {asset.name} ({index} of {count})",
            )

    for attempt in range(4):
        if cancelled(cancel):
            raise Cancelled(CANCELLED)
        headers = {"Accept-Encoding": "identity"}
        if attempt:
            headers["Range"] = f"bytes={done}-"
        try:
            with requests.get(
                url, stream=True, timeout=(15, 60), headers=headers
            ) as reply:
                reply.raise_for_status()
                resumed = bool(attempt) and reply.status_code == 206
                if not resumed:
                    digest, done = hashlib.sha256(), 0
                with path.open("ab" if resumed else "wb") as stream:
                    for chunk in reply.iter_content(1024 * 1024):
                        if cancelled(cancel):
                            raise Cancelled(CANCELLED)
                        stream.write(chunk)
                        digest.update(chunk)
                        done += len(chunk)
                        report()
                        if done > asset.size:
                            break
            break
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", "error")
            raise RuntimeError(
                f"GitHub did not serve {asset.name} (HTTP {status}); try engine setup "
                "again later."
            ) from None
        except requests.RequestException:
            if done == asset.size:
                break
            if attempt == 3:
                raise RuntimeError(
                    f"Could not download {asset.name} from GitHub; check your "
                    "connection and run engine setup again."
                ) from None
            pause(cancel, 1 + attempt)
    report(final=True)
    if done != asset.size or digest.hexdigest() != asset.sha256:
        path.unlink(missing_ok=True)
        raise ValueError(
            f"{asset.name} did not match its pinned SHA-256 and was not installed; "
            "run engine setup again, and report it if this repeats."
        )
    return path


def unpack(archive, folder):
    name = Path(archive).name
    try:
        if name.endswith(".zip"):
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(folder)
        else:
            with tarfile.open(archive) as bundle:
                bundle.extractall(folder, filter="data")
    except OSError:
        raise RuntimeError(
            f"Could not unpack {name} into {folder}; free up disk space, close "
            "programs using that folder, and run engine setup again."
        ) from None
    except (EOFError, tarfile.TarError, zipfile.BadZipFile):
        raise RuntimeError(
            f"{name} is not an archive that can be unpacked safely and was not "
            "installed; report this before trying again."
        ) from None


def sweep():
    for stale in DATA.glob("engine-*"):
        if SCRATCH.fullmatch(stale.name):
            shutil.rmtree(stale, ignore_errors=True)


def needs_clear(folder, assets):
    folder = Path(folder)
    if not folder.is_dir() or not any(folder.iterdir()):
        return False
    pins = {a.name + ".installed": "sha256:" + a.sha256 for a in assets}
    markers = list(folder.glob("*.installed"))
    return not markers or any(pins.get(m.name) != marker(m) for m in markers)


def gutted(folder, assets):
    exe = PROGRAM[assets[0].repo] + (".exe" if system() == "windows" else "")
    return complete(folder, assets[:1]) and not any(
        v.is_file() for v in Path(folder).rglob(exe)
    )


def clear(folder):
    try:
        shutil.rmtree(folder)
    except OSError:
        raise RuntimeError(
            f"Could not replace the engine in {folder}; press Stop server, close "
            "programs using that folder, and run engine setup again."
        ) from None


def move(src, dst):
    for left in range(10 if system() == "windows" else 0, -1, -1):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if left:
                time.sleep(0.3)
        except OSError:
            break
    raise RuntimeError(
        f"Could not move {Path(src).name} into place; close programs using {dst} and "
        "run engine setup again."
    )


def strata_choice(backend, name, strata):
    if name == "macos" or strata is False:
        return False, []
    if backend != "cuda":
        note = "Strata was skipped because it needs an NVIDIA GPU with the CUDA build."
        return False, [note] if strata else []
    if not shutil.which("git"):
        return False, [
            "Strata was skipped because Git is not installed; install Git and run "
            "engine setup again to add Strata."
        ]
    return True, []


def revision(checkout):
    checkout = Path(checkout)
    if not (checkout / ".git").exists():
        return ""
    try:
        return subprocess.check_output(
            [shutil.which("git") or "git", "-C", str(checkout), "rev-parse", "HEAD"],
            text=True,
            stdin=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            creationflags=QUIET,
            env=dict(os.environ, GIT_CEILING_DIRECTORIES=str(checkout.parent)),
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def git_clone(tag, dest, cancel=None):
    failed = (
        "Git could not fetch the Strata source; check your connection and run engine "
        "setup again."
    )
    try:
        child = subprocess.Popen(
            [
                shutil.which("git") or "git",
                "clone",
                "--quiet",
                "--depth",
                "1",
                "--branch",
                tag,
                STRATA_URL,
                str(dest),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
            creationflags=QUIET,
        )
    except OSError:
        raise RuntimeError(failed) from None
    while child.poll() is None:
        if cancelled(cancel):
            child.terminate()
            try:
                child.wait(10)
            except subprocess.TimeoutExpired:
                child.kill()
            raise Cancelled(CANCELLED)
        pause(cancel, 0.25)
    if child.returncode:
        raise RuntimeError(failed)


def strata_source(checkout, tag, commit, scratch, progress=None, cancel=None):
    checkout = Path(checkout)
    have = revision(checkout)
    if checkout.exists():
        if have == commit or (commit is None and have):
            return have
        move(checkout, Path(scratch) / "stale-source")
    tell(
        progress,
        job="engine-install",
        phase="source",
        message="Fetching the Strata server source…",
    )
    fresh = Path(scratch) / "Strata"
    git_clone(tag, fresh, cancel)
    rev = revision(fresh)
    if not rev or (commit and rev != commit):
        raise ValueError(
            "The Strata source did not match its pinned commit and was not installed; "
            "report this before trying again."
        )
    checkout.parent.mkdir(parents=True, exist_ok=True)
    move(fresh, checkout)
    return rev


def record_update(assets, tags, destination, backend, checkout, rev):
    try:
        meta = load(DATA / "updates.json", {})
    except (OSError, ValueError):
        meta = {}
    meta = meta if isinstance(meta, dict) else {}
    for field, fresh in (
        ("engines", {a.repo: str(destination / a.target) for a in assets}),
        ("tags", tags),
    ):
        kept = meta.get(field)
        meta[field] = (kept if isinstance(kept, dict) else {}) | fresh
    meta["backend"] = backend
    if checkout:
        meta["source"], meta["revision"] = str(checkout), rev
    write_json(DATA / "updates.json", meta)


def install(update=False, backend=None, strata=None, progress=None, cancel=None):
    detect.cache_clear()
    chosen, reason = resolve_backend(backend)
    name = detect()["system"]
    want_strata, notes = strata_choice(chosen, name, strata)
    tell(progress, job="engine-install", phase="detect", backend=chosen, message=reason)
    DATA.mkdir(exist_ok=True, parents=True)
    guard = FileLock(str(DATA / "engines.lock"), timeout=1)
    try:
        guard.acquire()
    except Timeout:
        raise RuntimeError(
            "Engine setup is already running in another Agent Workbench window; wait "
            "for it to finish."
        ) from None
    try:
        sweep()
        if update:
            assets, tags = newest_assets(chosen, name, want_strata)
            destination = DATA / "updates"
        else:
            assets, tags = wanted(chosen, name, want_strata), {}
            destination = DATA / "runtime"
        groups = {}
        for asset in assets:
            groups.setdefault(destination / asset.target, []).append(asset)
        todo = {}
        for folder, items in groups.items():
            wipe = needs_clear(folder, items) or gutted(folder, items)
            todo[folder] = (
                wipe,
                [a for a in items if wipe or not complete(folder, [a])],
            )
        missing = [a for _, items in todo.values() for a in items]
        skipped = [a.name for a in assets if a not in missing]
        if any(wipe for wipe, _ in todo.values()) and owned()[0]:
            raise RuntimeError(
                "Press Stop server, then run engine setup again so the engine files "
                "can be replaced."
            )
        need = sum(a.size for a in missing)
        if need and shutil.disk_usage(DATA).free < 3 * need:
            raise ValueError(
                f"Engine setup needs about {3 * need / 1e9:.1f} GB free on the drive "
                f"that holds {DATA}; free up space and run it again."
            )
        checkout, rev = None, ""
        with tempfile.TemporaryDirectory(
            dir=DATA, prefix="engine-", ignore_cleanup_errors=True
        ) as scratch:
            files = {
                a.name: fetch(a, Path(scratch), progress, cancel, i, len(missing))
                for i, a in enumerate(missing, 1)
            }
            if cancelled(cancel):
                raise Cancelled(CANCELLED)
            for folder, (wipe, items) in todo.items():
                if wipe:
                    clear(folder)
                folder.mkdir(parents=True, exist_ok=True)
                for a in items:
                    tell(
                        progress,
                        job="engine-install",
                        phase="extract",
                        asset=a.name,
                        message=f"Unpacking {a.name}",
                    )
                    unpack(files[a.name], folder)
                    (folder / (a.name + ".installed")).write_text("sha256:" + a.sha256)
            if want_strata:
                checkout = (
                    destination / ("src-" + tags[STRATA_REPO])
                    if update
                    else DATA / "Strata"
                )
                rev = strata_source(
                    checkout,
                    tags.get(STRATA_REPO, STRATA_TAG),
                    None if update else STRATA_COMMIT,
                    scratch,
                    progress,
                    cancel,
                )
        if update:
            record_update(assets, tags, destination, chosen, checkout, rev)
    finally:
        guard.release()
    result = dict(
        backend=chosen,
        channel="Updated" if update else "Tested",
        installed=[a.name for a in missing],
        skipped=skipped,
        strata=want_strata,
        notes=notes,
    )
    tell(
        progress,
        job="engine-install",
        phase="done",
        message=f"The llama.cpp {LABEL[chosen]} build is ready.",
        **result,
    )
    return result


def pick(repo, tag, backend, name, names):
    names = {n for n in names if isinstance(n, str)}
    if repo == STRATA_REPO:
        if backend != "cuda" or name not in STRATA_ASSETS:
            return []
        tail = f"{name}-x86_64-cuda-cuda13.tar.gz"
        return sorted(
            n for n in names if n.startswith("strata-inkling-") and n.endswith(tail)
        )[:1]
    if backend == "cuda" and name in CUDA_PAIR:
        engine, libraries = CUDA_PAIR[name]
        head, tail = (re.escape(v) for v in engine.format(tag=tag, n="\0").split("\0"))
        found = [re.fullmatch(head + r"(\d+)" + tail, n) for n in sorted(names)]
        best = max(filter(None, found), key=lambda m: int(m[1]), default=None)
        if not best:
            return []
        picked = [best[0], libraries.format(tag=tag, n=best[1])]
    elif (name, backend) in SINGLE:
        picked = [SINGLE[(name, backend)].format(tag=tag)]
    else:
        return []
    return picked if all(n in names for n in picked) else []


def newest_assets(backend, name, strata):
    assets, tags = [], {}
    for repo in [LLAMA_REPO] + ([STRATA_REPO] if strata else []):
        try:
            reply = requests.get(
                f"https://api.github.com/repos/{repo}/releases",
                params={"per_page": 15},
                timeout=30,
            )
            limited = reply.status_code in (403, 429)
            if not limited:
                reply.raise_for_status()
                releases = reply.json()
        except (requests.RequestException, ValueError):
            raise RuntimeError(
                f"Could not look up {repo} releases on GitHub; check your connection "
                "and try Update again, or keep the Tested engine."
            ) from None
        if limited:
            raise RuntimeError(
                "GitHub is limiting release lookups from this network; wait an hour "
                "and try Update again, or keep the Tested engine."
            )
        llama = repo == LLAMA_REPO
        for release in releases if isinstance(releases, list) else []:
            if not isinstance(release, dict):
                continue
            tag = release.get("tag_name")
            if (
                not isinstance(tag, str)
                or not SAFE_NAME.fullmatch(tag)
                or release.get("draft")
                or (
                    release.get("prerelease")
                    and not (llama and re.fullmatch(r"b[0-9]+", tag))
                )
            ):
                continue
            listed = release.get("assets")
            rows = {
                a["name"]: a
                for a in (listed if isinstance(listed, list) else [])
                if isinstance(a, dict) and isinstance(a.get("name"), str)
            }
            picked = pick(repo, tag, backend, name, rows)
            if not picked:
                continue
            for item in picked:
                digest, size = rows[item].get("digest"), rows[item].get("size")
                if (
                    not SAFE_NAME.fullmatch(item)
                    or not isinstance(digest, str)
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
                    or isinstance(size, bool)
                    or not isinstance(size, int)
                ):
                    raise ValueError(
                        f"GitHub did not publish a digest for {item}, so the update "
                        "was not installed."
                    )
                target = ("llama-" if llama else "strata-") + tag
                assets.append(Asset(repo, tag, item, target, digest[7:], size))
            tags[repo] = tag
            break
        if repo not in tags:
            raise ValueError(
                f"No compatible release was found for {repo}; keep the Tested engine."
            )
    return assets, tags


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Install the llama.cpp engine for this computer."
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="install the newest release beside the tested one",
    )
    parser.add_argument(
        "--backend",
        default="auto",
        metavar="auto|cuda|vulkan|cpu|metal",
        help="engine build to install (default: auto, chosen from the detected GPU)",
    )
    parser.add_argument("--no-strata", action="store_true", help="skip Strata")
    args = parser.parse_args(argv)
    steps, final = {}, []

    def show(event):
        phase = event.get("phase")
        if phase == "download":
            step = min(10, event["done"] * 10 // max(event["total"], 1))
            if steps.get(event["asset"]) != step:
                steps[event["asset"]] = step
                print(f"Downloading {event['asset']} {step * 10}%", flush=True)
        elif phase == "done":
            final.append(event["message"])
        else:
            print(event["message"], flush=True)

    try:
        result = install(
            args.update, args.backend, False if args.no_strata else None, show, None
        )
    except (Cancelled, ValueError, RuntimeError, requests.RequestException) as exc:
        try:
            if not sys.stdout.isatty():
                print(MARK + str(exc), flush=True)
        except (AttributeError, OSError, ValueError):
            pass
        raise SystemExit(str(exc))
    for line in [*result["notes"], *final]:
        print(line, flush=True)


if __name__ == "__main__":
    main()
