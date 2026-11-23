"""Model discovery, hardware detection, the model catalog and verified downloads."""

import contextlib
import copy
import ctypes
import ctypes.util
import errno
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from urllib.parse import quote

import psutil
import requests

from recommend import rate, recommend, sizing_backend, working_set_estimate

GIB = 1024**3
ROOT = Path(__file__).resolve().parent
CATALOG_FILE = ROOT / "model_catalog.json"
HUB = "https://huggingface.co"
CHUNK = 1024 * 1024
RETRY_WAIT = 1.0
ATTEMPTS = 4
MIN_GPU_GIB = 5.0
SHARD = re.compile(r"^(.*)-(\d{5})-of-(\d{5})(\.gguf)$", re.I)
COMPANION = re.compile(
    r"mmproj|vision\.strata|(^|[-_.])(lora|adapter|projector)([-_.]|$)"
)
SKIP_DIRS = {".cache", ".git", "node_modules"}
VENDORS = {"10de": "nvidia", "1002": "amd", "8086": "intel"}
VENDOR_LABEL = {"nvidia": "NVIDIA", "amd": "AMD", "intel": "Intel", "apple": "Apple"}
PCI_IDS = (
    "/usr/share/hwdata/pci.ids",
    "/usr/share/misc/pci.ids",
    "/usr/share/pci.ids",
)
DISPLAY_CLASS = (
    r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
)
CPU_KEY = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
REGISTRY_VALUES = (
    "DriverDesc",
    "MatchingDeviceId",
    "HardwareInformation.qwMemorySize",
    "HardwareInformation.MemorySize",
)
QUIET = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DAMAGED = "The model catalog file is damaged. Reinstall Agent Workbench to restore it."
CANCELLED = "Download cancelled. Start it again to resume where it stopped."
JOBS, JOBS_LOCK = {}, threading.Lock()
WRITING = set()
_HARDWARE, _HARDWARE_LOCK = {}, threading.Lock()
MEMORY_UNREAD = {
    "nvidia": (
        "Workbench could not read this NVIDIA GPU's memory because nvidia-smi did "
        "not answer; install or repair the NVIDIA driver, then choose Check again."
    ),
    "amd": "Workbench could not read this AMD GPU's memory from its driver.",
    "intel": "Workbench cannot read Intel GPU memory on this system.",
}


def unified_memory():
    return sys.platform == "darwin" and platform.machine() == "arm64"


def _text(path):
    try:
        return Path(path).read_text(encoding="utf8", errors="replace").strip()
    except OSError:
        return ""


def _gib(value):
    try:
        return round(int(value) / GIB, 2)
    except (TypeError, ValueError):
        return None


def _gpu(vendor, name, memory=None, free=None, source="", pci_id=None, driver=None):
    return dict(
        vendor=vendor,
        name=name,
        memory_gib=memory,
        free_gib=free,
        source=source,
        pci_id=pci_id,
        driver=driver,
    )


@lru_cache(maxsize=32)
def pci_name(pci_id, files=PCI_IDS):
    vendor, device = pci_id.split(":")
    for file in files:
        inside = False
        for line in _text(file).splitlines():
            if line.startswith(vendor):
                inside = True
            elif inside and line.startswith("\t" + device):
                name = line.split("  ", 1)[-1].strip()
                bracket = re.search(r"\[([^\]]+)\]\s*$", name)
                return bracket[1] if bracket else name
            elif inside and line[:1] not in ("\t", "#", ""):
                break
    return ""


def linux_gpus(root="/sys/class/drm", files=PCI_IDS):
    rows = []
    for card in sorted(Path(root).glob("card[0-9]*")):
        if "-" in card.name:
            continue
        info = dict(
            line.split("=", 1)
            for line in _text(card / "device/uevent").splitlines()
            if "=" in line
        )
        pci_id = info.get("PCI_ID", "").strip().lower()
        vendor = VENDORS.get(pci_id[:4])
        if not vendor or not re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{4}", pci_id):
            continue
        label, name = VENDOR_LABEL[vendor], pci_name(pci_id, tuple(files))
        if not name:
            name = f"{label} GPU ({pci_id})"
        elif not name.startswith(label):
            name = f"{label} {name}"
        total = _gib(_text(card / "device/mem_info_vram_total"))
        used = _gib(_text(card / "device/mem_info_vram_used"))
        free = None if total is None or used is None else round(total - used, 2)
        driver = info.get("DRIVER", "").strip() or None
        rows.append(_gpu(vendor, name, total, free, "sysfs", pci_id, driver))
    return rows


def nvidia_smi_gpus(output):
    def gib(mib):
        try:
            return round(int(mib) / 1024, 2)
        except ValueError:
            return None

    rows = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.rsplit(",", 2)]
        if len(parts) != 3 or not parts[0]:
            continue
        name = parts[0]
        if not name.upper().startswith("NVIDIA"):
            name = "NVIDIA " + name
        total, free = gib(parts[1]), gib(parts[2])
        rows.append(_gpu("nvidia", name, total, free, "nvidia-smi", None, "nvidia"))
    return rows


def _nvidia_smi():
    candidates = [shutil.which("nvidia-smi")]
    if sys.platform != "win32":
        candidates.append("/usr/lib/wsl/lib/nvidia-smi")
    elif os.environ.get("ProgramFiles"):
        candidates.append(
            os.path.join(
                os.environ["ProgramFiles"],
                "NVIDIA Corporation",
                "NVSMI",
                "nvidia-smi.exe",
            )
        )
    exe = next((c for c in candidates if c and Path(c).is_file()), None)
    if not exe:
        return ""
    try:
        return subprocess.check_output(
            [
                exe,
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            errors="replace",
            timeout=5,
            stdin=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=QUIET,
        )
    except (OSError, subprocess.SubprocessError):
        return ""


def _number(value, width):
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, (bytes, bytearray)) and len(value) >= width:
        return int.from_bytes(value[:width], "little")
    return None


def registry_gpus(rows):
    seen, found = set(), []
    for row in rows:
        match = re.search(
            r"ven_([0-9a-f]{4})&dev_([0-9a-f]{4})",
            str(row.get("MatchingDeviceId", "")).lower(),
        )
        vendor = VENDORS.get(match[1]) if match else None
        name = str(row.get("DriverDesc", "")).strip()
        if not vendor or not name or (match[0], name) in seen:
            continue
        seen.add((match[0], name))
        size = _number(row.get("HardwareInformation.qwMemorySize"), 8)
        if size is None:
            size = _number(row.get("HardwareInformation.MemorySize"), 4)
            if size is not None and size >= 4095 * 1024**2:
                size = None
        memory = _gib(size) if size else None
        pci_id = f"{match[1]}:{match[2]}"
        found.append(_gpu(vendor, name, memory, None, "registry", pci_id))
    return found


def _registry_rows():
    try:
        import winreg
    except ImportError:
        return []
    rows = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, DISPLAY_CLASS) as root:
            for index in range(64):
                try:
                    name = winreg.EnumKey(root, index)
                except OSError:
                    break
                if not re.fullmatch(r"\d{4}", name):
                    continue
                try:
                    with winreg.OpenKey(root, name) as key:
                        row = {}
                        for value in REGISTRY_VALUES:
                            try:
                                row[value] = winreg.QueryValueEx(key, value)[0]
                            except OSError:
                                pass
                        rows.append(row)
                except OSError:
                    continue
    except OSError:
        return []
    return rows


def merge_gpus(detected, smi):
    if not smi:
        return detected
    return smi + [gpu for gpu in detected if gpu["vendor"] != "nvidia"]


def primary_gpu(gpus):
    if not gpus:
        return None
    order = ("nvidia", "amd", "intel")

    def key(index):
        gpu = gpus[index]
        return (
            gpu["memory_gib"] is not None and gpu["source"] != "registry",
            gpu["memory_gib"] or 0,
            -order.index(gpu["vendor"]),
            -index,
        )

    return max(range(len(gpus)), key=key)


@lru_cache(maxsize=1)
def _metal_working_set():
    try:
        metal = ctypes.CDLL("/System/Library/Frameworks/Metal.framework/Metal")
        objc = ctypes.CDLL(ctypes.util.find_library("objc"))
        metal.MTLCreateSystemDefaultDevice.restype = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        objc.objc_msgSend.restype = ctypes.c_uint64
        objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        device = metal.MTLCreateSystemDefaultDevice()
        if not device:
            return 0
        selector = objc.sel_registerName(b"recommendedMaxWorkingSetSize")
        return int(objc.objc_msgSend(device, selector))
    except Exception:
        return 0


@lru_cache(maxsize=1)
def _cpu_name():
    try:
        if sys.platform == "darwin":
            return subprocess.check_output(
                ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
                text=True,
                errors="replace",
                timeout=2,
                stdin=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=QUIET,
            ).strip()
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CPU_KEY) as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        for line in _text("/proc/cpuinfo").splitlines():
            if line.startswith("model name") and ":" in line:
                return line.split(":", 1)[1].strip()
    except (OSError, subprocess.SubprocessError, ImportError):
        pass
    return ""


def summarise(
    system,
    arch,
    cpu,
    ram,
    ram_free,
    gpus,
    unified=False,
    working_set=None,
    working_source=None,
):
    unknown, notes = [], []
    index = 0 if unified and gpus else primary_gpu(gpus)
    vendor, backend, kind, memory, free = "none", "cpu", "ram", ram, ram_free
    if unified:
        vendor, backend, kind = "apple", "metal", "unified"
        label = f"{cpu or 'Apple Silicon'} · {ram:.0f} GiB unified memory"
        if working_source == "estimate":
            unknown.append(
                "Metal did not report its memory limit, so Workbench estimated it "
                "from the installed memory."
            )
    elif index is None:
        label = f"No supported GPU found · {ram:.0f} GiB memory"
    else:
        gpu = gpus[index]
        vendor, name, size = gpu["vendor"], gpu["name"], gpu["memory_gib"]
        gpu_backend = "cuda" if vendor == "nvidia" else "vulkan"
        if size is None:
            backend, kind, memory, free = gpu_backend, "vram", None, None
            label = f"{name} · memory unknown"
            unknown.append(MEMORY_UNREAD[vendor])
        elif size < MIN_GPU_GIB:
            label = f"{name} · {size:.1f} GiB (too small for local models)"
            notes.append(
                f"{name} has {size:.1f} GiB, which is too little for the catalog "
                "models, so models run on the processor."
            )
        else:
            backend, kind, memory, free = gpu_backend, "vram", size, gpu["free_gib"]
            label = f"{name} · {size:.1f} GiB"
            if free is None:
                unknown.append(
                    "Workbench cannot read how much GPU memory is in use on this "
                    "system."
                )
            if gpu["source"] == "registry":
                unknown.append(
                    "GPU details come from the Windows driver registry, which can "
                    "still list a card that was removed."
                )
            same = [
                g
                for g in gpus
                if g["vendor"] == vendor and (g["memory_gib"] or 0) >= MIN_GPU_GIB
            ]
            if len(same) > 1:
                notes.append(
                    f"{len(same)} {VENDOR_LABEL[vendor]} GPUs were found; model picks "
                    "are sized for the largest one."
                )
    return dict(
        schema=1,
        os=system,
        arch=arch,
        cpu=cpu,
        ram_gib=ram,
        ram_free_gib=ram_free,
        unified=unified,
        gpus=gpus,
        primary=index,
        gpu_vendor=vendor,
        backend=backend,
        memory_kind=kind,
        memory_gib=memory,
        free_gib=free,
        working_set_gib=working_set,
        working_set_source=working_source,
        label=label,
        unknown=unknown,
        notes=notes,
    )


def _detect():
    if sys.platform == "win32":
        system = "windows"
    elif sys.platform == "darwin":
        system = "macos"
    else:
        system = "linux" if sys.platform.startswith("linux") else "other"
    machine = platform.machine().lower()
    arch = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    memory = psutil.virtual_memory()
    ram, ram_free = round(memory.total / GIB, 2), round(memory.available / GIB, 2)
    cpu = _cpu_name()
    if unified_memory():
        limit = _metal_working_set()
        chip = _gpu("apple", cpu or "Apple Silicon", ram, ram_free, "metal")
        return summarise(
            system,
            arch,
            cpu,
            ram,
            ram_free,
            [chip],
            True,
            round(limit / GIB, 2) if limit else working_set_estimate(ram),
            "metal" if limit else "estimate",
        )
    if system == "linux":
        found = linux_gpus()
    elif system == "windows":
        found = registry_gpus(_registry_rows())
    else:
        found = []
    gpus = merge_gpus(found, nvidia_smi_gpus(_nvidia_smi()))
    return summarise(system, arch, cpu, ram, ram_free, gpus)


def hardware_info(refresh=False):
    """Stable description of this computer; never raises."""
    with _HARDWARE_LOCK:
        if refresh or "value" not in _HARDWARE:
            try:
                _HARDWARE["value"] = _detect()
            except Exception:
                blank = summarise("other", "", "", 0.0, 0.0, [])
                blank["unknown"].append(
                    "Workbench could not inspect this computer's hardware."
                )
                _HARDWARE["value"] = blank
        return copy.deepcopy(_HARDWARE["value"])


def hardware():
    """Legacy view (name, total GiB, free GiB, RAM GiB) of hardware_info()."""
    info = hardware_info(refresh=True)
    ram = info["ram_gib"]
    if info["unified"]:
        return info["cpu"] or "Apple Silicon", ram, info["ram_free_gib"], ram
    if info["memory_kind"] != "vram" or info["memory_gib"] is None:
        return info["label"], 0, 0, ram
    group = [
        gpu
        for gpu in info["gpus"]
        if gpu["vendor"] == info["gpu_vendor"]
        and (gpu["memory_gib"] or 0) >= MIN_GPU_GIB
    ]
    names = [gpu["name"] for gpu in group]
    if len(names) == 1:
        name = names[0]
    elif len(set(names)) == 1:
        name = f"{len(names)}× {names[0]}"
    else:
        name = " + ".join(names)
    total = sum(gpu["memory_gib"] for gpu in group)
    free = sum(
        gpu["memory_gib"] if gpu["free_gib"] is None else gpu["free_gib"]
        for gpu in group
    )
    return name, round(total, 2), round(free, 2), ram


def families(files):
    """Group split weights by directory and quant. Exclude partial sets and companions."""
    entries = dict(files)
    for name, size in sorted(entries.items()):
        leaf = Path(name).name.lower()
        if COMPANION.search(leaf):
            continue
        if not leaf.endswith((".gguf", ".gguf_file", ".strata")):
            continue
        group = [name]
        match = SHARD.fullmatch(name)
        if match:
            if int(match[2]) != 1:
                continue
            group = [
                f"{match[1]}-{i:05}-of-{match[3]}{match[4]}"
                for i in range(1, int(match[3]) + 1)
            ]
            if any(part not in entries for part in group):
                continue
        yield name, group, sum(entries[part] for part in group)


def discover(folder):
    found = []
    for base, dirs, names in os.walk(Path(folder).resolve()):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            path = os.path.join(base, name)
            try:
                info = os.stat(path)
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                found.append((path, info.st_size))
    return list(families(found))


@lru_cache(maxsize=8)
def library_rows(folder):
    return discover(folder) if Path(folder).is_dir() else []


def fit(size, vram, ram):
    gib = size / GIB
    if unified_memory():
        return (
            "Unified memory candidate"
            if gib <= ram * 0.80
            else "Exceeds memory estimate"
        )
    if not vram:
        return "GPU unknown"
    if gib <= vram * 0.80:
        return "GPU candidate"
    if gib <= 0.80 * (ram + vram):
        return "Offload candidate"
    return "Exceeds estimate"


ENGINES = ("llama.cpp", "Strata")
BACKENDS = ("cuda", "vulkan", "metal", "cpu")
ROLES = ("weights", "companion", "lora", "vision", "draft")
KV_SETTINGS = ("auto", "fp8")
CACHE_TYPES = ("f16", "q8_0", "q4_0")
SIZING = (
    "kv_kib_per_token",
    "kv_fixed_gib",
    "compute_gib",
    "compute_gib_metal",
    "token_overhead_bytes",
)
IDENT = re.compile(r"[a-z0-9][a-z0-9._-]{1,63}")
REPO = re.compile(r"[A-Za-z0-9][\w.-]*/[\w.-]+")
REVISION = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
FILE_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*")
EVIDENCE = re.compile(r"results/[A-Za-z0-9._/-]+\.json")
TRIAL = ("machine", "peak_memory_gib", "context", "tool_call")
THINKING = re.compile(r"[a-z]{2,10}")
FLAG = re.compile(r"--[a-z][a-z0-9-]{1,30}")
FLAG_VALUE = re.compile(r"[A-Za-z0-9_.-]{1,24}")


def _check(ok, detail):
    if not ok:
        raise ValueError(DAMAGED) from ValueError("model_catalog.json: " + detail)


def _num(value, low=0):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value >= low
    except OverflowError:
        return False


def _int(value, low):
    return isinstance(value, int) and not isinstance(value, bool) and value >= low


def _str(value, limit):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _match(pattern, value):
    return isinstance(value, str) and bool(pattern.fullmatch(value))


def _on_disk(root, name):
    try:
        return (Path(root) / name).is_file()
    except OSError:
        return False


def _file_name(file):
    return file["path"].rsplit("/", 1)[-1] if "repo" in file else file["path"]


def _check_file(file, where):
    path = file.get("path")
    _check(_match(FILE_PATH, path) and ".." not in path, where + " file path")
    where = f"{where} {path}"
    _check(file.get("role") in ROLES, where + " role")
    _check(_int(file.get("bytes"), 1), where + " bytes")
    digest = file.get("sha256")
    _check(
        _match(SHA256, digest) or (digest is None and file["role"] == "companion"),
        where + " sha256",
    )
    _check(("repo" in file) == ("revision" in file), where + " repo and revision")
    if "repo" in file:
        _check(
            _match(REPO, file["repo"]) and _match(REVISION, file["revision"]),
            where + " repo or revision",
        )
    _check(isinstance(file.get("resident", True), bool), where + " resident")


def _check_model(model, sizing, known):
    _check(
        isinstance(model, dict)
        and _match(IDENT, model.get("id"))
        and model["id"] not in known,
        "model id",
    )
    where = "model " + model["id"]
    _check(_str(model.get("label"), 80), where + " label")
    _check(_str(model.get("licence"), 80), where + " licence")
    note = model.get("licence_note", "")
    _check(isinstance(note, str) and len(note) <= 400, where + " licence_note")
    family = model.get("family")
    _check(isinstance(family, str) and family in sizing, where + " family")
    _check(_match(REPO, model.get("repo")), where + " repo")
    _check(_match(REVISION, model.get("revision")), where + " revision")
    _check(model.get("engine") in ENGINES, where + " engine")
    files = model.get("files")
    _check(
        isinstance(files, list) and files and all(isinstance(f, dict) for f in files),
        where + " files",
    )
    for file in files:
        _check_file(file, where)
    _check(any(f["role"] == "weights" for f in files), where + " weights")
    names = [_file_name(f) for f in files]
    _check(len(set(names)) == len(names), where + " duplicate file name")


def _check_args(args, engine, where):
    _check(
        isinstance(args, list)
        and len(args) <= 16
        and len(args) % 2 == 0
        and all(_match(FLAG, flag) for flag in args[0::2])
        and all(_match(FLAG_VALUE, value) for value in args[1::2])
        and len(set(args[0::2])) == len(args) // 2,
        where + " engine_args",
    )
    _check(engine == "llama.cpp" or not args, where + " engine_args need llama.cpp")
    flags = dict(zip(args[0::2], args[1::2]))
    cache = flags.get("--cache-type-k")
    _check(
        cache == flags.get("--cache-type-v")
        and (cache is None or cache in CACHE_TYPES),
        where + " cache types",
    )
    return cache


def _check_trial(trial, where):
    """The evidence object of a suggested pick: one run of that file on a named machine."""
    where += " evidence"
    _check(isinstance(trial, dict), where)
    _check(_str(trial.get("machine"), 60), where + " machine")
    _check(_num(trial.get("peak_memory_gib"), 0.1), where + " peak_memory_gib")
    context = trial.get("context")
    _check(_int(context, 1024) and context <= 1048576, where + " context")
    _check(trial.get("tool_call") in ("passed", "failed"), where + " tool_call")
    file = trial.get("file")
    _check(
        "file" not in trial or (_match(EVIDENCE, file) and ".." not in file),
        where + " file",
    )


def _check_pick(pick, models, known):
    _check(
        isinstance(pick, dict)
        and _match(IDENT, pick.get("id"))
        and pick["id"] not in known,
        "pick id",
    )
    where = "pick " + pick["id"]
    model = models.get(pick["model"]) if isinstance(pick.get("model"), str) else None
    _check(model is not None, where + " model")
    backends = pick.get("backends")
    _check(
        isinstance(backends, list)
        and bool(backends)
        and all(isinstance(b, str) and b in BACKENDS for b in backends)
        and len(set(backends)) == len(backends),
        where + " backends",
    )
    _check(
        model["engine"] != "Strata" or backends == ["cuda"],
        where + " Strata runs on cuda only",
    )
    tier = pick.get("min_memory_gib")
    _check(_num(tier) and tier <= 4096, where + " min_memory_gib")
    _check(_int(pick.get("rank"), 1), where + " rank")
    _check(pick.get("status") in ("measured", "suggested"), where + " status")
    if pick["status"] == "measured":
        evidence = pick.get("evidence")
        _check(_match(EVIDENCE, evidence) and ".." not in evidence, where + " evidence")
        _check(_str(pick.get("measured_on"), 120), where + " measured_on")
    elif not isinstance(pick.get("evidence", ""), str):
        _check_trial(pick["evidence"], where)
    _check(
        pick.get("tool_calling") in ("good", "ok", "weak", "unknown"),
        where + " tool_calling",
    )
    _check(_str(pick.get("why"), 240), where + " why")
    settings = pick.get("settings")
    _check(isinstance(settings, dict), where + " settings")
    context = settings.get("context")
    _check(_int(context, 1024) and context <= 1048576, where + " context")
    _check(settings.get("kv", "auto") in KV_SETTINGS, where + " kv")
    switches = ("draft", "lora", "vision")
    _check(
        all(isinstance(settings.get(key, False), bool) for key in switches),
        where + " draft, lora or vision",
    )
    _check(
        "thinking" not in settings or _match(THINKING, settings["thinking"]),
        where + " thinking",
    )
    _check(
        "engine" not in settings and "model" not in settings,
        where + " settings may not name an engine or a model",
    )
    cache = _check_args(settings.get("engine_args", []), model["engine"], where)
    _check(
        isinstance(pick.get("needs_engine_args", False), bool),
        where + " needs_engine_args",
    )
    extras = pick.get("extras", [])
    _check(
        isinstance(extras, list)
        and all(
            isinstance(x, dict) and _str(x.get("label"), 60) and _num(x.get("gib"))
            for x in extras
        ),
        where + " extras",
    )
    _check("need_gib" not in pick or _num(pick["need_gib"], 0.1), where + " need_gib")
    return model, cache


def _accepted(accepts, args):
    if accepts is None:
        return False
    try:
        accepts(list(args))
    except ValueError:
        return False
    return True


def _join(pick, model, family, cache, root, accepts):
    chosen = pick["settings"]
    settings = dict(
        engine=model["engine"],
        kv=chosen.get("kv", "auto"),
        draft=chosen.get("draft", False),
        lora=chosen.get("lora", False),
        vision=chosen.get("vision", False),
        context=chosen["context"],
    )
    if "thinking" in chosen:
        settings["thinking"] = chosen["thinking"]
    args = chosen.get("engine_args", [])
    if args and _accepted(accepts, args):
        settings["engine_args"] = list(args)
    elif args:
        if pick.get("needs_engine_args", False):
            return None
        cache = None
    if model["engine"] == "Strata":
        precision = "fp8" if settings["kv"] == "fp8" else "f16"
    else:
        precision = cache or ("q8_0" if settings["lora"] else "f16")
    listed = [
        dict(
            repo=file.get("repo", model["repo"]),
            revision=file.get("revision", model["revision"]),
            path=file["path"],
            name=_file_name(file),
            bytes=file["bytes"],
            sha256=file.get("sha256"),
            role=file["role"],
        )
        for file in model["files"]
    ]
    files = [
        file
        for file in listed
        if file["role"] in ("weights", "companion") or settings[file["role"]]
    ]
    if "need_gib" in pick:
        memory = dict(method="measured", need_gib=pick["need_gib"])
    elif family.get("sizing") == "tier":
        memory = dict(method="tier")
    else:
        memory = dict(method="formula", **{key: family[key] for key in SIZING})
    status = pick["status"]
    if status == "measured" and not _on_disk(root, pick["evidence"]):
        status = "suggested"
    item = dict(
        id=pick["id"],
        model=model["id"],
        label=model["label"].strip(),
        repo=model["repo"],
        revision=model["revision"],
        engine=model["engine"],
        licence=model["licence"].strip(),
        licence_note=model.get("licence_note", "").strip(),
        backends=list(pick["backends"]),
        min_memory_gib=pick["min_memory_gib"],
        rank=pick["rank"],
        status=status,
        tool_calling=pick["tool_calling"],
        why=pick["why"].strip(),
        settings=settings,
        kv=precision,
        extras=[
            dict(label=extra["label"].strip(), gib=extra["gib"])
            for extra in pick.get("extras", [])
        ],
        files=files,
        download_bytes=sum(file["bytes"] for file in files),
        resident_bytes=sum(
            file["bytes"]
            for file in model["files"]
            if file["role"] == "weights" and file.get("resident", True)
        ),
        lora_bytes=sum(file["bytes"] for file in files if file["role"] == "lora"),
        memory_model=memory,
    )
    drafter = [file for file in listed if file["role"] == "draft"]
    if drafter:
        item["drafter"] = drafter
    if status == "measured":
        item.update(evidence=pick["evidence"], measured_on=pick["measured_on"].strip())
    elif isinstance(pick.get("evidence"), dict):
        trial = pick["evidence"]
        item["evidence"] = {key: trial[key] for key in TRIAL}
        item["evidence"]["machine"] = trial["machine"].strip()
        if "file" in trial and _on_disk(root, trial["file"]):
            item["evidence"]["file"] = trial["file"]
    return item


def parse_catalog(data, root, accepts=None):
    """Validate catalog data; return (meta, entries), one entry per usable pick."""
    _check(
        isinstance(data, dict)
        and type(data.get("schema")) is int
        and data["schema"] == 1,
        "schema must be 1",
    )
    sizing, listed, picks = data.get("families"), data.get("models"), data.get("picks")
    _check(isinstance(sizing, dict), "families")
    _check(isinstance(listed, list) and isinstance(picks, list), "models and picks")
    for name, family in sizing.items():
        _check(
            isinstance(family, dict)
            and (
                family.get("sizing") == "tier"
                or all(_num(family.get(key)) for key in SIZING)
            ),
            f"family {name}",
        )
    models = {}
    for model in listed:
        _check_model(model, sizing, models)
        models[model["id"]] = model
    joined, known = [], set()
    for pick in picks:
        model, cache = _check_pick(pick, models, known)
        known.add(pick["id"])
        item = _join(pick, model, sizing[model["family"]], cache, root, accepts)
        if item:
            joined.append(item)
    meta = dict(schema=1)
    for key in ("updated", "engine_build"):
        meta[key] = data[key] if isinstance(data.get(key), str) else ""
    return meta, joined


@lru_cache(maxsize=1)
def load_catalog():
    import runtime

    try:
        data = json.loads(CATALOG_FILE.read_text(encoding="utf8"))
    except (OSError, ValueError) as error:
        raise ValueError(DAMAGED) from error
    usable = "engine_args" in runtime.DEFAULTS and hasattr(runtime, "llama_args")
    return parse_catalog(data, ROOT, runtime.llama_args if usable else None)


def entries():
    return copy.deepcopy(load_catalog()[1])


def entry(ident):
    found = next((item for item in entries() if item["id"] == ident), None)
    if not found:
        raise ValueError(
            "That model is no longer in the catalog. Reopen the model list and "
            "choose again."
        )
    return found


def destination(item, library):
    return Path(library) / item["repo"].replace("/", "--")


def _weights(item):
    return next(file for file in item["files"] if file["role"] == "weights")


def installed(item, library):
    folder, have, complete = destination(item, library), 0, True
    for file in item["files"]:
        path = folder / file["name"]
        part = path.with_name(path.name + ".part")
        if path.is_file() and path.stat().st_size == file["bytes"]:
            have += file["bytes"]
            continue
        complete = False
        if part.is_file():
            have += min(file["bytes"], part.stat().st_size)
    return dict(
        installed=complete,
        model=str(folder / _weights(item)["name"]) if complete else "",
        have_bytes=have,
        total_bytes=item["download_bytes"],
    )


def apply_engine_build(info, build):
    """Hardware as the recommender should see it once the engine build is known."""
    gpu_builds = ("cuda", "vulkan")
    if info["backend"] not in gpu_builds or build == info["backend"]:
        return info
    if build in gpu_builds:
        return {**info, "backend": build}
    if build != "cpu":
        return info
    note = (
        "Agent Workbench uses the CPU engine build on this computer, so model picks "
        "are sized for the processor."
    )
    return {
        **info,
        "backend": "cpu",
        "memory_kind": "ram",
        "memory_gib": info["ram_gib"],
        "free_gib": info["ram_free_gib"],
        "notes": [*info["notes"], note],
    }


def _engine_build():
    try:
        import setup_engines

        return setup_engines.installed_backend() or setup_engines.detect()["backend"]
    except Exception:
        return ""


def _engines():
    try:
        import runtime

        return list(runtime.available_engines())
    except Exception:
        return None


def recommendation(refresh=False):
    import runtime

    build = _engine_build()
    info = apply_engine_build(hardware_info(refresh), build)
    library = runtime.settings()["library"]
    result = recommend(info, entries(), _engines())
    for item in [result["pick"], *result["alternatives"]]:
        if item:
            item["installed"] = installed(item, library)
    return {**result, "hardware_label": info["label"], "build": build}


class DownloadCancelled(RuntimeError):
    pass


def _cancel_error():
    import runtime

    return getattr(runtime, "Cancelled", DownloadCancelled)(CANCELLED)


def _pause(cancel, seconds):
    if cancel is None:
        time.sleep(seconds)
        return False
    if hasattr(cancel, "wait"):
        cancel.wait(seconds)
    else:
        time.sleep(seconds)
    return cancel.is_set()


class _Stop:
    """Cancel token of one job: its own event or the caller's token stops it."""

    def __init__(self, outer=None):
        self.event, self.outer = threading.Event(), outer

    def set(self):
        self.event.set()

    def is_set(self):
        return self.event.is_set() or (self.outer is not None and self.outer.is_set())

    def wait(self, seconds):
        end = time.monotonic() + seconds
        while not self.is_set():
            left = end - time.monotonic()
            if left <= 0:
                break
            self.event.wait(min(0.05, left))
        return self.is_set()


def _refused(status, name, repo):
    if status in (401, 403):
        return (
            f"Hugging Face refused access to {repo}. Sign in with the Hugging Face "
            "CLI in Advanced → Setup, accept the model's terms on its page, then "
            "try again."
        )
    if status == 404:
        return (
            f"{name} is no longer available from {repo}. Update Agent Workbench to "
            "get a current model catalog."
        )
    return (
        f"Hugging Face answered with HTTP {status} for {name}. Try again in a few "
        "minutes."
    )


def fetch(
    url,
    dest,
    size=None,
    sha256=None,
    progress=None,
    cancel=None,
    session=None,
    headers=None,
    label="",
):
    """Download url to dest through a .part file: resumable, cancellable, verified."""
    dest = Path(dest)
    name, part = dest.name, dest.with_name(dest.name + ".part")
    tell = progress or (lambda phase, done: None)

    def stopped():
        return cancel is not None and cancel.is_set()

    def hashed(path):
        digest, done = hashlib.sha256(), 0
        with path.open("rb") as stream:
            while block := stream.read(CHUNK):
                if stopped():
                    raise _cancel_error()
                digest.update(block)
                done += len(block)
                tell("checking", done)
        return digest, done

    if dest.exists():
        same = size is None or dest.stat().st_size == size
        if same and (not sha256 or hashed(dest)[0].hexdigest() == sha256):
            return dest
        raise ValueError(
            f"{name} is already in your model folder but does not match the expected "
            "file. Move or delete that file, then download again."
        )
    if size is not None and part.exists() and part.stat().st_size > size:
        part.unlink()
    digest, done = hashed(part) if part.exists() else (hashlib.sha256(), 0)
    own = requests.Session() if session is None else None
    web, ended = session if own is None else own, False
    try:
        for attempt in range(ATTEMPTS):
            if size is not None and done >= size:
                break
            if stopped():
                raise _cancel_error()
            ask = dict(headers or {})
            if done:
                ask["Range"] = f"bytes={done}-"
            try:
                with web.get(url, headers=ask, stream=True, timeout=(10, 60)) as reply:
                    status = reply.status_code
                    if status == 416 and done:
                        ended = True
                        break
                    if status == 200:
                        digest, done = hashlib.sha256(), 0
                    elif status != 206 or not reply.headers.get(
                        "Content-Range", ""
                    ).startswith(f"bytes {done}-"):
                        raise ValueError(_refused(status, name, label or url))
                    with part.open("ab" if done else "wb") as stream:
                        for block in reply.iter_content(CHUNK):
                            if stopped():
                                raise _cancel_error()
                            stream.write(block)
                            digest.update(block)
                            done += len(block)
                            tell("downloading", done)
            except requests.RequestException:
                if attempt < ATTEMPTS - 1:
                    _pause(cancel, RETRY_WAIT * 2**attempt)
                if stopped():
                    raise _cancel_error() from None
                continue
            ended = True
            if size is None:
                break
    finally:
        if own is not None:
            own.close()
    if size is None and not ended:
        raise ValueError(
            f"The download of {name} stopped before it finished. Check your "
            "connection, then start it again to resume."
        )
    if size is not None and done != size:
        if done > size:
            part.unlink(missing_ok=True)
        raise ValueError(
            f"The download of {name} stopped at {done / GIB:.1f} of {size / GIB:.1f} "
            "GiB. Check your connection, then start it again to resume."
        )
    if sha256 and digest.hexdigest() != sha256:
        part.unlink(missing_ok=True)
        raise ValueError(
            f"{name} failed its checksum, so the damaged download was removed. "
            "Start the download again."
        )
    os.replace(part, dest)
    return dest


@contextlib.contextmanager
def _job(key, state, cancel=None):
    with JOBS_LOCK:
        if key in JOBS:
            raise ValueError(
                "This model is already downloading. Wait for it to finish or cancel "
                "it first."
            )
        stop = _Stop(cancel)
        JOBS[key] = dict(cancel=stop, state=state)
    try:
        yield stop
    finally:
        with JOBS_LOCK:
            JOBS.pop(key, None)


@contextlib.contextmanager
def _claim(target, stop):
    """One job at a time writes a destination; another job waits for its turn."""
    key = os.path.normcase(str(Path(target).resolve()))
    while True:
        with JOBS_LOCK:
            if key not in WRITING:
                WRITING.add(key)
                break
        if _pause(stop, 0.2):
            raise _cancel_error()
    try:
        yield
    finally:
        with JOBS_LOCK:
            WRITING.discard(key)


def jobs():
    with JOBS_LOCK:
        return {key: dict(job["state"]) for key, job in JOBS.items()}


def cancel_download(ident):
    with JOBS_LOCK:
        hits = [
            job["cancel"]
            for key, job in JOBS.items()
            if ident in (key, job["state"]["id"])
        ]
    for stop in hits:
        stop.set()
    return bool(hits)


def _hub_headers():
    try:
        from huggingface_hub import get_token

        token = get_token()
    except Exception:
        token = None
    return {"Authorization": "Bearer " + token} if token else {}


def _unwritable(folder, error):
    if error.errno == errno.ENOSPC:
        return (
            "The drive that holds your model folder is full. Free some space, then "
            "start the download again to resume."
        )
    return (
        f"Workbench cannot write to the model folder {folder}. Choose another "
        "folder in Advanced → Model library."
    )


def _prepare(folder, files):
    folder.mkdir(parents=True, exist_ok=True)
    root, missing = folder.resolve(), 0
    for file in files:
        target = (folder / file["name"]).resolve()
        if target == root or not target.is_relative_to(root):
            raise ValueError(
                "Hugging Face listed a file path that Workbench will not write. "
                "Choose another file."
            )
        part = target.with_name(target.name + ".part")
        if not target.exists():
            have = part.stat().st_size if part.is_file() else 0
            missing += max(0, file["bytes"] - have)
    short = missing + 256 * 1024**2 - shutil.disk_usage(folder).free
    if short > 0:
        raise ValueError(
            f"Free {short / GIB:.1f} GiB on the drive that holds your model folder, "
            "then start the download again."
        )


def _transfer(
    key,
    ident,
    label,
    folder,
    files,
    progress=None,
    cancel=None,
    session=None,
    headers=None,
):
    """Fetch files ({repo, revision, path, name, bytes, sha256}) as one job."""
    folder = Path(folder)
    total = sum(file["bytes"] for file in files)
    state = dict(
        job="model-download",
        phase="checking",
        message=f"Checking {label}",
        id=ident,
        key=key,
        label=label,
        file="",
        done=0,
        total=total,
        rate=0,
    )
    words = dict(
        checking="Checking {file}",
        downloading="Downloading {file}",
        done="{label} is ready",
    )
    mark = dict(phase="", at=0.0, done=0, sent=0.0)

    def report(phase, done, force=False):
        now = time.monotonic()
        state.update(phase=phase, done=done, message=words[phase].format(**state))
        if phase != mark["phase"]:
            mark.update(phase=phase, at=now, done=done)
            state["rate"] = 0
        if not force and now - mark["sent"] < 0.5:
            return
        span, moved = now - mark["at"], done - mark["done"]
        if phase == "downloading" and span > 0:
            state["rate"] = max(0, int(moved / span))
        mark.update(at=now, done=done, sent=now)
        if progress:
            try:
                progress(dict(state))
            except Exception:
                pass

    with _job(key, state, cancel) as stop:
        try:
            _prepare(folder, files)
            base = 0
            for file in files:
                target = folder / file["name"]
                target.parent.mkdir(parents=True, exist_ok=True)
                state["file"] = file["name"]
                report("checking", base, force=True)
                revision = quote(str(file["revision"]), safe="")
                url = f"{HUB}/{file['repo']}/resolve/{revision}/{quote(file['path'])}"
                with _claim(target, stop):
                    fetch(
                        url,
                        target,
                        file["bytes"] or None,
                        file["sha256"],
                        lambda phase, done, base=base: report(phase, base + done),
                        stop,
                        session,
                        headers,
                        file["repo"],
                    )
                base += file["bytes"]
            report("done", total, force=True)
        except OSError as error:
            if error.errno is None:
                raise
            raise ValueError(_unwritable(folder, error)) from error
        finally:
            library_rows.cache_clear()
    return folder


def download_entry(ident, library, progress=None, cancel=None, session=None):
    """Download one catalog entry into the library; returns what studio-save needs."""
    item = entry(ident)
    folder = _transfer(
        item["model"],
        item["id"],
        item["label"],
        destination(item, library),
        item["files"],
        progress,
        cancel,
        session,
        _hub_headers(),
    )
    model = str(folder / _weights(item)["name"])
    return dict(
        id=item["id"],
        model=model,
        folder=str(folder),
        preset_name=f"{item['label']} · {item['settings']['context'] // 1024}K",
        settings={**item["settings"], "model": model},
    )


def download_drafter(model, progress=None, cancel=None, session=None):
    """Download the DSpark drafter that the catalog lists for an Inkling GGUF into the
    folder where the runtime looks for it; returns its path."""
    import runtime

    model = Path(str(model or ""))
    item = next((item for item in entries() if item.get("drafter")), None)
    if item is None or not runtime.inkling_gguf(model):
        raise ValueError(
            "The DSpark drafter is for Inkling 2 Mini GGUF files; choose one in "
            "Weights first."
        )
    folder = _transfer(
        "drafter",
        "dspark-drafter",
        "DSpark drafter",
        runtime.drafter_folder(model),
        item["drafter"],
        progress,
        cancel,
        session,
        _hub_headers(),
    )
    return str(folder / item["drafter"][0]["name"])


def hub_files(query):
    from huggingface_hub import HfApi

    api = HfApi()
    if "/" in query:
        repo = query.strip()
    else:
        return [
            m.id
            for m in api.list_models(
                search=query, filter="gguf", sort="downloads", limit=24
            )
        ]
    info = api.model_info(repo, files_metadata=True)
    return [
        (repo, info.sha, name, parts, size)
        for name, parts, size in families(
            (f.rfilename, f.size or 0) for f in info.siblings
        )
    ]


def _lfs_sha256(sibling):
    lfs = getattr(sibling, "lfs", None)
    if isinstance(lfs, dict):
        digest = lfs.get("sha256")
    else:
        digest = getattr(lfs, "sha256", None)
    return digest.lower() if isinstance(digest, str) and digest else None


def download(row, folder, vision=False, progress=None, cancel=None):
    from huggingface_hub import HfApi

    repo, sha, name, parts, _ = row
    info = HfApi().model_info(repo, revision=sha, files_metadata=True)
    listed = {sibling.rfilename: sibling for sibling in info.siblings}
    wanted = list(parts)
    if name.endswith(".strata") and "Inkling" in name:
        wanted += ["chat_template.jinja", "tokenizer.json"]
        if vision and "Inkling-2-Mini-Vision.strata" in listed:
            wanted.append("Inkling-2-Mini-Vision.strata")
    absent = next((path for path in [name, *wanted] if path not in listed), None)
    if absent:
        raise ValueError(
            f"{absent} is missing from {repo} at this revision. Search again and "
            "choose another file."
        )
    files = [
        dict(
            repo=repo,
            revision=sha,
            path=path,
            name=path,
            bytes=listed[path].size or 0,
            sha256=_lfs_sha256(listed[path]),
        )
        for path in wanted
    ]
    key = f"hub:{repo}:{name}"
    dest = _transfer(
        key,
        key,
        Path(name).name,
        Path(folder) / repo.replace("/", "--"),
        files,
        progress,
        cancel,
        None,
        _hub_headers(),
    )
    return dest / name


def _catalog_view():
    import runtime

    build = _engine_build()
    info = apply_engine_build(hardware_info(), build)
    library = runtime.settings()["library"]
    backend, engines = sizing_backend(info), _engines()
    shown = [
        {**rate(item, info), "installed": installed(item, library)}
        for item in entries()
        if backend in item["backends"]
        and (engines is None or item["engine"] in engines)
    ]
    shown.sort(key=lambda item: (-item["min_memory_gib"], item["rank"], item["id"]))
    return dict(
        load_catalog()[0],
        hardware_label=info["label"],
        backend=info["backend"],
        build=build,
        entries=shown,
    )


def dispatch(action, payload, progress=None, cancel=None):
    import runtime

    payload = payload if isinstance(payload, dict) else {}
    if action == "model-hardware":
        return hardware_info(bool(payload.get("refresh")))
    if action == "model-recommend":
        return recommendation(bool(payload.get("refresh")))
    if action == "model-catalog":
        return _catalog_view()
    if action == "model-download" and payload.get("drafter"):
        return download_drafter(payload["drafter"], progress, cancel)
    if action == "model-download":
        return download_entry(
            str(payload.get("id", "")), runtime.settings()["library"], progress, cancel
        )
    if action == "model-download-status":
        return jobs()
    if action == "model-download-cancel":
        return cancel_download(str(payload.get("id", "")))
    raise ValueError("Unknown model action.")
