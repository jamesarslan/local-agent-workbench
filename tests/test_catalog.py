"""Hardware detection, library scan, catalog loader and verified downloads."""

import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("WORKBENCH_DATA", tempfile.mkdtemp(prefix="awb-test-"))

import requests

import catalog
import runtime

ROOT = Path(__file__).resolve().parents[1]
GIB = catalog.GIB
REV = "0123456789abcdef0123456789abcdef01234567"
REV_OTHER = "89abcdef0123456789abcdef0123456789abcdef"
SHA = {letter: letter * 64 for letter in "abcde"}
URL = "https://huggingface.co/org/repo/resolve/main/file.gguf"
WSL_SMI = "/usr/lib/wsl/lib/nvidia-smi"
Cancelled = getattr(runtime, "Cancelled", catalog.DownloadCancelled)
ALLOWED = {
    "--cache-type-k",
    "--cache-type-v",
    "--cache-ram",
    "--ctx-checkpoints",
    "--spec-type",
    "--spec-draft-n-max",
}
Q8 = ["--cache-type-k", "q8_0", "--cache-type-v", "q8_0"]
TRIAL = {
    "machine": "a test Mac",
    "peak_memory_gib": 3.5,
    "context": 32768,
    "tool_call": "passed",
}
PCI_DB = (
    "#\n#\tList of PCI ID's\n#\n"
    "1002  Advanced Micro Devices, Inc. [AMD/ATI]\n"
    "\t7550  Navi 48 [Radeon RX 9070/9070 XT/9070 GRE]\n"
    "\t\t1002 0b36  Radeon RX 9070 XT\n"
    "10de  NVIDIA Corporation\n"
    "\t2b85  GB202 [GeForce RTX 5090]\n"
    "\t2c02  GB203GL\n"
    "8086  Intel Corporation\n"
    "\ta780  Raptor Lake-S GT1 [UHD Graphics 770]\n"
)

accept_all = list


def allow_list(args):
    for flag in args[0::2]:
        if flag not in ALLOWED:
            raise ValueError(f"{flag} is not an allowed engine option.")
    return list(args)


def gpu(vendor, name, memory=None, free=None, source="nvidia-smi"):
    return catalog._gpu(vendor, name, memory, free, source)


def computer(gpus, ram=64.0, ram_free=40.0, **more):
    return catalog.summarise("linux", "x86_64", "Test CPU", ram, ram_free, gpus, **more)


def pick(ident, model, **more):
    row = {
        "id": ident,
        "model": model,
        "backends": ["cuda", "vulkan", "metal", "cpu"],
        "min_memory_gib": 0,
        "rank": 1,
        "status": "suggested",
        "tool_calling": "ok",
        "why": "A fixture pick.",
        "settings": {"context": 32768},
    }
    row.update(more)
    return row


def fixture():
    return {
        "schema": 1,
        "updated": "2026-11-22",
        "engine_build": "b12633",
        "families": {
            "plain": {
                "kv_kib_per_token": 64,
                "kv_fixed_gib": 0.0,
                "compute_gib": 1.0,
                "compute_gib_metal": 2.0,
                "token_overhead_bytes": 0,
            },
            "packed": {"sizing": "tier"},
        },
        "models": [
            {
                "id": "alpha-q4",
                "label": "Alpha Q4",
                "family": "plain",
                "repo": "org/Alpha-GGUF",
                "revision": REV,
                "engine": "llama.cpp",
                "licence": "Apache-2.0",
                "files": [
                    {
                        "path": "Alpha-Q4-00001-of-00002.gguf",
                        "bytes": 1000,
                        "sha256": SHA["a"],
                        "role": "weights",
                    },
                    {
                        "path": "Alpha-Q4-00002-of-00002.gguf",
                        "bytes": 400,
                        "sha256": SHA["b"],
                        "role": "weights",
                        "resident": False,
                    },
                    {
                        "path": "chat_template.jinja",
                        "bytes": 50,
                        "sha256": None,
                        "role": "companion",
                    },
                    {
                        "path": "adapters/Alpha-pi-lora-F16.gguf",
                        "bytes": 30,
                        "sha256": SHA["c"],
                        "role": "lora",
                        "repo": "hub/Alpha-adapted",
                        "revision": REV_OTHER,
                    },
                    {
                        "path": "mmproj-Alpha.gguf",
                        "bytes": 70,
                        "sha256": SHA["d"],
                        "role": "vision",
                    },
                ],
            },
            {
                "id": "beta-sq",
                "label": "Beta SQ",
                "family": "packed",
                "repo": "org/Beta-Strata",
                "revision": REV,
                "engine": "Strata",
                "licence": "MIT",
                "licence_note": "Read the Beta terms before you use it.",
                "files": [
                    {
                        "path": "Beta.strata",
                        "bytes": 2000,
                        "sha256": SHA["e"],
                        "role": "weights",
                    }
                ],
            },
        ],
        "picks": [
            pick(
                "alpha-q8",
                "alpha-q4",
                status="measured",
                evidence="results/alpha.json",
                measured_on="a test card",
                settings={"context": 32768, "engine_args": list(Q8)},
            ),
            pick(
                "alpha-mmap",
                "alpha-q4",
                rank=2,
                needs_engine_args=True,
                settings={"context": 32768, "engine_args": ["--load-mode", "mmap"]},
            ),
            pick(
                "alpha-lazy",
                "alpha-q4",
                rank=3,
                settings={
                    "context": 32768,
                    "engine_args": [*Q8, "--lazy-mode", "on"],
                },
            ),
            pick(
                "alpha-pi",
                "alpha-q4",
                rank=4,
                settings={"context": 131072, "lora": True, "thinking": "off"},
                extras=[{"label": "Draft head", "gib": 0.5}],
            ),
            pick("alpha-peak", "alpha-q4", rank=5, need_gib=3.5),
            pick(
                "beta-fp8",
                "beta-sq",
                backends=["cuda"],
                min_memory_gib=16,
                settings={"context": 16384, "kv": "fp8"},
            ),
        ],
    }


def evidence_root(folder):
    results = Path(folder) / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / "alpha.json").write_text("{}", encoding="utf8")
    return Path(folder)


def by_id(entries):
    return {entry["id"]: entry for entry in entries}


class FakeResponse:
    def __init__(self, status, headers=None, body=b"", fail_after=None):
        self.status_code, self.headers = status, headers or {}
        self.body, self.fail_after = body, fail_after

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, size):
        for start in range(0, len(self.body), size):
            if self.fail_after is not None and start >= self.fail_after:
                raise requests.ConnectionError("connection dropped")
            yield self.body[start : start + size]


class FakeSession:
    """Serves bytes like a file host: ranges, one dropped body, fixed statuses."""

    def __init__(
        self,
        data=b"",
        by_url=None,
        honour_range=True,
        fail_after=None,
        status=None,
        offline=False,
        gate=None,
    ):
        self.data, self.by_url, self.honour_range = data, by_url, honour_range
        self.fail_after, self.status, self.offline = fail_after, status, offline
        self.gate, self.waiting = gate, threading.Event()
        self.calls, self.closed = [], False

    def get(self, url, headers=None, stream=False, timeout=None):
        headers = dict(headers or {})
        self.calls.append((url, headers))
        if self.gate is not None:
            self.waiting.set()
            self.gate.wait(5)
        if self.offline:
            raise requests.ConnectionError("no route to host")
        if self.status:
            return FakeResponse(self.status)
        data = self.data if self.by_url is None else self.by_url[url]
        fail, self.fail_after = self.fail_after, None
        if "Range" in headers and self.honour_range:
            start = int(headers["Range"].removeprefix("bytes=").rstrip("-"))
            if start >= len(data):
                return FakeResponse(416, {"Content-Range": f"bytes */{len(data)}"})
            span = f"bytes {start}-{len(data) - 1}/{len(data)}"
            return FakeResponse(206, {"Content-Range": span}, data[start:], fail)
        return FakeResponse(200, {}, data, fail)

    def close(self):
        self.closed = True


class FakeKey:
    def __init__(self, node):
        self.node = node

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_winreg(tree):
    """A winreg stand-in over nested dicts: dict values are keys, others values."""

    def open_key(parent, name):
        node = tree if parent == "HKLM" else parent.node
        child = node.get(name) if isinstance(node, dict) else None
        if not isinstance(child, dict):
            raise OSError("key cannot be opened")
        return FakeKey(child)

    def enum_key(key, index):
        names = list(key.node)
        if index >= len(names):
            raise OSError("no more keys")
        return names[index]

    def query_value(key, name):
        value = key.node.get(name)
        if value is None or isinstance(value, dict):
            raise FileNotFoundError(name)
        return value, 0

    return types.SimpleNamespace(
        HKEY_LOCAL_MACHINE="HKLM",
        OpenKey=open_key,
        EnumKey=enum_key,
        QueryValueEx=query_value,
    )


def on_platform(name):
    return patch.object(catalog, "sys", types.SimpleNamespace(platform=name))


def only_file(path):
    """Make Path.is_file true for one POSIX path, whatever this machine holds."""
    return patch.object(catalog.Path, "is_file", lambda self: self.as_posix() == path)


class TempCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.tmp = Path(folder.name).resolve()

    def patched(self, target, name, *args, **kwargs):
        patcher = patch.object(target, name, *args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()


class HardwareTest(TempCase):
    def card(self, name, pci_id=None, driver=None, total=None, used=None):
        device = self.tmp / "drm" / name / "device"
        device.mkdir(parents=True)
        lines = ["PCI_SLOT_NAME=0000:03:00.0"]
        if driver:
            lines.insert(0, f"DRIVER={driver}")
        if pci_id:
            lines.append(f"PCI_ID={pci_id}")
        (device / "uevent").write_text("\n".join(lines) + "\n", encoding="utf8")
        if total is not None:
            (device / "mem_info_vram_total").write_text(f"{total}\n", encoding="utf8")
        if used is not None:
            (device / "mem_info_vram_used").write_text(f"{used}\n", encoding="utf8")

    def database(self):
        path = self.tmp / "pci.ids"
        path.write_text(PCI_DB, encoding="utf8")
        return (str(path),)

    def test_linux_gpus_reads_vendor_name_and_amd_memory(self):
        self.card("card0", "8086:A780", "i915")
        self.card("card1", "1002:7550", "amdgpu", 17095983104, 1288490189)
        self.card("card2", "10DE:2B85")
        rows = catalog.linux_gpus(self.tmp / "drm", self.database())
        self.assertEqual([row["vendor"] for row in rows], ["intel", "amd", "nvidia"])
        self.assertEqual(
            rows[1],
            {
                "vendor": "amd",
                "name": "AMD Radeon RX 9070/9070 XT/9070 GRE",
                "memory_gib": 15.92,
                "free_gib": 14.72,
                "source": "sysfs",
                "pci_id": "1002:7550",
                "driver": "amdgpu",
            },
        )
        self.assertEqual(rows[0]["name"], "Intel UHD Graphics 770")
        self.assertEqual(rows[0]["driver"], "i915")
        self.assertEqual(rows[2]["name"], "NVIDIA GeForce RTX 5090")
        self.assertIsNone(rows[2]["driver"])
        for row in (rows[0], rows[2]):
            self.assertIsNone(row["memory_gib"])
            self.assertIsNone(row["free_gib"])
            self.assertEqual(row["source"], "sysfs")

    def test_linux_gpus_skips_connectors_and_other_vendors(self):
        self.card("card0", "1AF4:1050", "virtio_gpu")
        self.card("card1", "10DE:2B85", "nvidia")
        self.card("card1-DP-1", "1002:7550", "amdgpu", 17095983104, 0)
        self.card("card2")
        self.card("card3", "1002:75", "amdgpu")
        rows = catalog.linux_gpus(self.tmp / "drm", self.database())
        self.assertEqual([row["pci_id"] for row in rows], ["10de:2b85"])
        self.assertEqual(catalog.linux_gpus(self.tmp / "absent"), [])

    def test_pci_name_without_database(self):
        self.card("card0", "1002:7550", "amdgpu", 17095983104)
        missing = (str(self.tmp / "no-pci.ids"),)
        rows = catalog.linux_gpus(self.tmp / "drm", missing)
        self.assertEqual(rows[0]["name"], "AMD GPU (1002:7550)")
        self.assertEqual(rows[0]["memory_gib"], 15.92)
        self.assertIsNone(rows[0]["free_gib"])
        files = self.database()
        self.assertEqual(catalog.pci_name("10de:2c02", files), "GB203GL")
        self.assertEqual(catalog.pci_name("10de:ffff", files), "")
        self.assertEqual(catalog.pci_name("1002:7550", missing + files)[:6], "Radeon")

    def test_nvidia_smi_parses_every_gpu(self):
        rows = catalog.nvidia_smi_gpus(
            "NVIDIA GeForce RTX 5090, 32607, 31000\n"
            "GeForce RTX 4090, 24564, [N/A]\n"
            "\n"
            "Quadro, Special Edition, [Not Supported], 8000\n"
        )
        self.assertEqual(
            [row["name"] for row in rows],
            [
                "NVIDIA GeForce RTX 5090",
                "NVIDIA GeForce RTX 4090",
                "NVIDIA Quadro, Special Edition",
            ],
        )
        self.assertEqual([row["memory_gib"] for row in rows], [31.84, 23.99, None])
        self.assertEqual([row["free_gib"] for row in rows], [30.27, None, 7.81])
        for row in rows:
            self.assertEqual(
                (row["vendor"], row["source"], row["driver"], row["pci_id"]),
                ("nvidia", "nvidia-smi", "nvidia", None),
            )
        self.assertEqual(catalog.nvidia_smi_gpus(""), [])
        self.assertEqual(catalog.nvidia_smi_gpus("NVIDIA-SMI has failed\n"), [])

    def test_nvidia_smi_spawns_nothing_without_executable(self):
        self.patched(catalog.shutil, "which", return_value=None)
        run = self.patched(catalog.subprocess, "check_output")
        with patch.dict(os.environ, {"ProgramFiles": str(self.tmp)}):
            with on_platform("linux"), only_file(""):
                self.assertEqual(catalog._nvidia_smi(), "")
            with on_platform("win32"):
                self.assertEqual(catalog._nvidia_smi(), "")
            with on_platform("win32"), only_file(WSL_SMI):
                self.assertEqual(catalog._nvidia_smi(), "")
        run.assert_not_called()

    def test_nvidia_smi_query_is_quiet_and_bounded(self):
        exe = self.tmp / "nvidia-smi"
        exe.write_text("", encoding="utf8")
        self.patched(catalog.shutil, "which", return_value=str(exe))
        spawn = subprocess.check_output
        run = self.patched(catalog.subprocess, "check_output", return_value="A, 1, 1\n")
        self.assertEqual(catalog._nvidia_smi(), "A, 1, 1\n")
        argv = run.call_args.args[0]
        self.assertEqual(
            argv,
            [
                str(exe),
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
        )
        options = run.call_args.kwargs
        self.assertEqual(options["timeout"], 5)
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["stderr"], subprocess.DEVNULL)
        self.assertEqual(options["creationflags"], catalog.QUIET)
        self.assertEqual(options["errors"], "replace")
        self.assertNotIn("shell", options)
        emit = (
            "import sys; "
            "sys.stdout.buffer.write(b'GeForce\\xae RTX 4090, 24564, 20000\\n')"
        )
        rows = catalog.nvidia_smi_gpus(spawn([sys.executable, "-c", emit], **options))
        self.assertEqual(
            [(row["memory_gib"], row["free_gib"]) for row in rows], [(23.99, 19.53)]
        )
        for error in (
            OSError("gone"),
            subprocess.TimeoutExpired(argv, 5),
            subprocess.CalledProcessError(9, argv),
        ):
            run.side_effect = error
            self.assertEqual(catalog._nvidia_smi(), "")

    def test_nvidia_smi_windows_fallback_path(self):
        exe = self.tmp / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("", encoding="utf8")
        self.patched(catalog.shutil, "which", return_value=None)
        run = self.patched(catalog.subprocess, "check_output", return_value="")
        with patch.dict(os.environ, {"ProgramFiles": str(self.tmp)}):
            with on_platform("win32"):
                catalog._nvidia_smi()
            self.assertEqual(run.call_args.args[0][0], str(exe))
            run.reset_mock()
            with on_platform("linux"), only_file(""):
                self.assertEqual(catalog._nvidia_smi(), "")
            run.assert_not_called()
            with on_platform("linux"), only_file(WSL_SMI):
                catalog._nvidia_smi()
        self.assertEqual(run.call_args.args[0][0], WSL_SMI)

    def test_registry_gpus_reads_qword_and_binary(self):
        rows = catalog.registry_gpus(
            [
                {
                    "DriverDesc": "NVIDIA GeForce RTX 5090",
                    "MatchingDeviceId": r"PCI\VEN_10DE&DEV_2B85&SUBSYS_89E01043",
                    "HardwareInformation.qwMemorySize": 34190917632,
                    "HardwareInformation.MemorySize": 4293918720,
                },
                {
                    "DriverDesc": " AMD Radeon RX 9070 XT ",
                    "MatchingDeviceId": r"pci\ven_1002&dev_7550&rev_c0",
                    "HardwareInformation.qwMemorySize": (17095983104).to_bytes(
                        8, "little"
                    ),
                },
            ]
        )
        self.assertEqual([row["memory_gib"] for row in rows], [31.84, 15.92])
        self.assertEqual([row["pci_id"] for row in rows], ["10de:2b85", "1002:7550"])
        self.assertEqual(rows[1]["name"], "AMD Radeon RX 9070 XT")
        for row in rows:
            self.assertEqual(row["source"], "registry")
            self.assertIsNone(row["free_gib"])
            self.assertIsNone(row["driver"])

    def test_registry_gpus_filters(self):
        intel = {
            "DriverDesc": "Intel(R) UHD Graphics 770",
            "MatchingDeviceId": r"PCI\VEN_8086&DEV_A780",
        }
        rows = catalog.registry_gpus(
            [
                {
                    "DriverDesc": "Microsoft Basic Display Adapter",
                    "MatchingDeviceId": r"PCI\CC_0300",
                },
                {
                    "DriverDesc": "Remote Display Adapter",
                    "MatchingDeviceId": r"root\rdpidd_indirectdisplay",
                },
                {"MatchingDeviceId": r"PCI\VEN_10DE&DEV_2B85"},
                {**intel, "HardwareInformation.MemorySize": 4293918720},
                {**intel, "HardwareInformation.MemorySize": 4293918720},
                {
                    "DriverDesc": "Intel(R) Arc(TM) Graphics",
                    "MatchingDeviceId": r"PCI\VEN_8086&DEV_7D55",
                    "HardwareInformation.qwMemorySize": "unexpected",
                    "HardwareInformation.MemorySize": (134217728).to_bytes(4, "little"),
                },
                {
                    "DriverDesc": "AMD Radeon(TM) Graphics",
                    "MatchingDeviceId": r"PCI\VEN_1002&DEV_15BF",
                    "HardwareInformation.qwMemorySize": 0,
                },
            ]
        )
        self.assertEqual(
            [(row["name"], row["memory_gib"]) for row in rows],
            [
                ("Intel(R) UHD Graphics 770", None),
                ("Intel(R) Arc(TM) Graphics", 0.12),
                ("AMD Radeon(TM) Graphics", None),
            ],
        )

    def test_registry_rows_reads_driver_keys(self):
        tree = {
            catalog.DISPLAY_CLASS: {
                "0000": {
                    "DriverDesc": "AMD Radeon RX 9070 XT",
                    "MatchingDeviceId": r"PCI\VEN_1002&DEV_7550",
                    "HardwareInformation.qwMemorySize": 17095983104,
                    "ProviderName": "Advanced Micro Devices, Inc.",
                },
                "Properties": {},
                "Configuration": {"DriverDesc": "not a driver key"},
                "0001": None,
                "0002": {"DriverDesc": "Intel(R) UHD Graphics 770"},
            }
        }
        with patch.dict(sys.modules, {"winreg": fake_winreg(tree)}):
            rows = catalog._registry_rows()
        self.assertEqual(
            rows,
            [
                {
                    "DriverDesc": "AMD Radeon RX 9070 XT",
                    "MatchingDeviceId": r"PCI\VEN_1002&DEV_7550",
                    "HardwareInformation.qwMemorySize": 17095983104,
                },
                {"DriverDesc": "Intel(R) UHD Graphics 770"},
            ],
        )
        self.assertEqual(catalog.registry_gpus(rows)[0]["memory_gib"], 15.92)
        with patch.dict(sys.modules, {"winreg": fake_winreg({})}):
            self.assertEqual(catalog._registry_rows(), [])
        with patch.dict(sys.modules, {"winreg": None}):
            self.assertEqual(catalog._registry_rows(), [])

    def test_merge_and_primary(self):
        smi = [gpu("nvidia", "NVIDIA RTX 4060", 8.0, 7.0)]
        stale = gpu("nvidia", "NVIDIA RTX 4060", 8.0, None, "registry")
        radeon = gpu("amd", "AMD Radeon RX 9070 XT", 15.92, None, "registry")
        merged = catalog.merge_gpus([stale, radeon], smi)
        self.assertEqual(merged, [smi[0], radeon])
        self.assertEqual(catalog.merge_gpus([stale, radeon], []), [stale, radeon])
        self.assertEqual(catalog.primary_gpu(merged), 0)
        small = gpu("intel", "Intel UHD", 0.12, None, "registry")
        self.assertEqual(catalog.primary_gpu([small, radeon, stale]), 1)
        unread = [gpu(vendor, vendor, None, None, "sysfs") for vendor in ("intel", "amd")]
        self.assertEqual(catalog.primary_gpu(unread), 1)
        unread.append(gpu("nvidia", "nvidia", None, None, "sysfs"))
        self.assertEqual(catalog.primary_gpu(unread), 2)
        twins = [gpu("nvidia", "NVIDIA RTX 4090", 23.99, 20.0)] * 2
        self.assertEqual(catalog.primary_gpu(twins), 0)
        self.assertIsNone(catalog.primary_gpu([]))

    def test_summarise_cases(self):
        chip = gpu("apple", "Apple M3 Max", 48.0, 30.24, "metal")
        mac = catalog.summarise(
            "macos", "arm64", "Apple M3 Max", 48.0, 30.24, [chip], True, 37.44, "metal"
        )
        self.assertEqual(
            {key: mac[key] for key in mac if key != "gpus"},
            {
                "schema": 1,
                "os": "macos",
                "arch": "arm64",
                "cpu": "Apple M3 Max",
                "ram_gib": 48.0,
                "ram_free_gib": 30.24,
                "unified": True,
                "primary": 0,
                "gpu_vendor": "apple",
                "backend": "metal",
                "memory_kind": "unified",
                "memory_gib": 48.0,
                "free_gib": 30.24,
                "working_set_gib": 37.44,
                "working_set_source": "metal",
                "label": "Apple M3 Max · 48 GiB unified memory",
                "unknown": [],
                "notes": [],
            },
        )
        guessed = catalog.summarise(
            "macos", "arm64", "", 16.0, 8.0, [chip], True, 10.56, "estimate"
        )
        self.assertEqual(guessed["label"], "Apple Silicon · 16 GiB unified memory")
        self.assertEqual(len(guessed["unknown"]), 1)
        self.assertIn("Metal did not report its memory limit", guessed["unknown"][0])

        bare = computer([])
        self.assertEqual(
            (bare["gpu_vendor"], bare["backend"], bare["memory_kind"]),
            ("none", "cpu", "ram"),
        )
        self.assertEqual((bare["memory_gib"], bare["free_gib"]), (64.0, 40.0))
        self.assertIsNone(bare["primary"])
        self.assertEqual(bare["label"], "No supported GPU found · 64 GiB memory")
        self.assertEqual((bare["unknown"], bare["notes"]), ([], []))

        for vendor, backend, words in (
            ("nvidia", "cuda", "nvidia-smi did not answer"),
            ("amd", "vulkan", "AMD GPU's memory from its driver"),
            ("intel", "vulkan", "cannot read Intel GPU memory"),
        ):
            unread = computer([gpu(vendor, "Some GPU", None, 3.0, "sysfs")])
            self.assertEqual((unread["gpu_vendor"], unread["backend"]), (vendor, backend))
            self.assertEqual(unread["memory_kind"], "vram")
            self.assertIsNone(unread["memory_gib"])
            self.assertIsNone(unread["free_gib"])
            self.assertEqual(unread["label"], "Some GPU · memory unknown")
            self.assertEqual(len(unread["unknown"]), 1)
            self.assertIn(words, unread["unknown"][0])

        tiny = computer([gpu("nvidia", "NVIDIA GeForce GT 1030", 2.0, 1.5)])
        self.assertEqual((tiny["gpu_vendor"], tiny["backend"]), ("nvidia", "cpu"))
        self.assertEqual(tiny["memory_kind"], "ram")
        self.assertEqual((tiny["memory_gib"], tiny["free_gib"]), (64.0, 40.0))
        self.assertEqual(tiny["primary"], 0)
        self.assertEqual(
            tiny["label"],
            "NVIDIA GeForce GT 1030 · 2.0 GiB (too small for local models)",
        )
        self.assertEqual(len(tiny["notes"]), 1)
        self.assertIn("models run on the processor", tiny["notes"][0])
        self.assertEqual(tiny["unknown"], [])

        normal = computer([gpu("nvidia", "NVIDIA GeForce RTX 4090", 23.99, 20.0)])
        self.assertEqual((normal["gpu_vendor"], normal["backend"]), ("nvidia", "cuda"))
        self.assertEqual(normal["memory_kind"], "vram")
        self.assertEqual((normal["memory_gib"], normal["free_gib"]), (23.99, 20.0))
        self.assertEqual(normal["label"], "NVIDIA GeForce RTX 4090 · 24.0 GiB")
        self.assertEqual((normal["unknown"], normal["notes"]), ([], []))
        self.assertIsNone(normal["working_set_gib"])
        self.assertIsNone(normal["working_set_source"])

        listed = computer(
            [
                gpu("amd", "AMD Radeon RX 9070 XT", 15.92, None, "registry"),
                gpu("intel", "Intel(R) UHD Graphics 770", 0.12, None, "registry"),
            ]
        )
        self.assertEqual((listed["primary"], listed["backend"]), (0, "vulkan"))
        self.assertEqual(listed["label"], "AMD Radeon RX 9070 XT · 15.9 GiB")
        self.assertEqual(
            listed["unknown"],
            [
                "Workbench cannot read how much GPU memory is in use on this system.",
                "GPU details come from the Windows driver registry, which can still "
                "list a card that was removed.",
            ],
        )
        self.assertEqual(listed["notes"], [])

        pair = computer(
            [
                gpu("nvidia", "NVIDIA GeForce RTX 4090", 23.99, 20.0),
                gpu("nvidia", "NVIDIA GeForce RTX 5090", 31.84, 30.0),
                gpu("nvidia", "NVIDIA GeForce GT 1030", 2.0, 1.0),
            ]
        )
        self.assertEqual((pair["primary"], pair["memory_gib"]), (1, 31.84))
        self.assertEqual(
            pair["notes"],
            ["2 NVIDIA GPUs were found; model picks are sized for the largest one."],
        )

    def test_hardware_tuple(self):
        def legacy(info):
            with patch.object(catalog, "hardware_info", return_value=info) as read:
                result = catalog.hardware()
            read.assert_called_once_with(refresh=True)
            return result

        twins = [
            gpu("nvidia", "NVIDIA GeForce RTX 4090", 23.99, 20.0),
            gpu("nvidia", "NVIDIA GeForce RTX 4090", 23.99, 10.5),
        ]
        self.assertEqual(
            legacy(computer(twins)), ("2× NVIDIA GeForce RTX 4090", 47.98, 30.5, 64.0)
        )
        mixed = [
            gpu("nvidia", "NVIDIA GeForce RTX 5090", 31.84, None),
            gpu("nvidia", "NVIDIA GeForce RTX 4090", 23.99, 20.0),
            gpu("nvidia", "NVIDIA GeForce GT 1030", 2.0, 1.0),
            gpu("amd", "AMD Radeon RX 9070 XT", 15.92, 15.0, "sysfs"),
        ]
        self.assertEqual(
            legacy(computer(mixed)),
            ("NVIDIA GeForce RTX 5090 + NVIDIA GeForce RTX 4090", 55.83, 51.84, 64.0),
        )
        single = computer([gpu("amd", "AMD Radeon RX 9070 XT", 15.92, None, "registry")])
        self.assertEqual(legacy(single), ("AMD Radeon RX 9070 XT", 15.92, 15.92, 64.0))
        unread = computer([gpu("nvidia", "NVIDIA GPU (10de:2b85)", None, None, "sysfs")])
        self.assertEqual(
            legacy(unread), ("NVIDIA GPU (10de:2b85) · memory unknown", 0, 0, 64.0)
        )
        self.assertEqual(
            legacy(computer([])), ("No supported GPU found · 64 GiB memory", 0, 0, 64.0)
        )
        chip = gpu("apple", "Apple M3 Max", 48.0, 30.24, "metal")
        mac = catalog.summarise(
            "macos", "arm64", "Apple M3 Max", 48.0, 30.24, [chip], True, 37.44, "metal"
        )
        self.assertEqual(legacy(mac), ("Apple M3 Max", 48.0, 30.24, 48.0))
        self.assertEqual(legacy({**mac, "cpu": ""})[0], "Apple Silicon")

    def test_hardware_info_never_raises_and_caches(self):
        with patch.dict(catalog._HARDWARE, clear=True):
            with patch.object(catalog, "_detect", side_effect=RuntimeError("broken")):
                info = catalog.hardware_info()
            self.assertEqual((info["backend"], info["os"]), ("cpu", "other"))
            self.assertEqual(
                info["unknown"], ["Workbench could not inspect this computer's hardware."]
            )
        found = computer([gpu("nvidia", "NVIDIA GeForce RTX 4090", 23.99, 20.0)])
        with patch.dict(catalog._HARDWARE, clear=True):
            with patch.object(catalog, "_detect", return_value=found) as detect:
                first = catalog.hardware_info()
                first["label"] = "changed"
                first["gpus"].clear()
                first["notes"].append("changed")
                second = catalog.hardware_info()
                self.assertEqual(detect.call_count, 1)
                self.assertEqual(second, found)
                catalog.hardware_info(refresh=True)
                self.assertEqual(detect.call_count, 2)

    def detect(self, system, unified=False, smi="", machine="x86_64"):
        self.patched(catalog, "unified_memory", return_value=unified)
        self.patched(catalog, "_cpu_name", return_value="Test CPU")
        self.patched(catalog, "_nvidia_smi", return_value=smi)
        self.patched(catalog.platform, "machine", return_value=machine)
        memory = types.SimpleNamespace(total=48 * GIB, available=30 * GIB)
        self.patched(catalog.psutil, "virtual_memory", return_value=memory)
        with on_platform(system):
            return catalog._detect()

    def test_detect_linux_merges_sysfs_and_nvidia_smi(self):
        radeon = gpu("amd", "AMD Radeon RX 9070 XT", 15.92, 14.72, "sysfs")
        nouveau = gpu("nvidia", "NVIDIA GPU (10de:2b85)", None, None, "sysfs")
        self.patched(catalog, "linux_gpus", return_value=[radeon, nouveau])
        registry = self.patched(catalog, "_registry_rows")
        info = self.detect("linux", smi="GeForce RTX 5090, 32607, 31000\n")
        registry.assert_not_called()
        self.assertEqual((info["os"], info["arch"], info["cpu"]), ("linux", "x86_64", "Test CPU"))
        self.assertEqual((info["ram_gib"], info["ram_free_gib"]), (48.0, 30.0))
        self.assertEqual(
            [(row["vendor"], row["source"]) for row in info["gpus"]],
            [("nvidia", "nvidia-smi"), ("amd", "sysfs")],
        )
        self.assertEqual((info["gpu_vendor"], info["backend"]), ("nvidia", "cuda"))
        self.assertEqual(info["memory_gib"], 31.84)
        self.assertFalse(info["unified"])

    def test_detect_windows_reads_registry(self):
        rows = [
            {
                "DriverDesc": "AMD Radeon RX 9070 XT",
                "MatchingDeviceId": r"PCI\VEN_1002&DEV_7550",
                "HardwareInformation.qwMemorySize": 17095983104,
            }
        ]
        self.patched(catalog, "_registry_rows", return_value=rows)
        sysfs = self.patched(catalog, "linux_gpus")
        info = self.detect("win32", machine="AMD64")
        sysfs.assert_not_called()
        self.assertEqual((info["os"], info["arch"]), ("windows", "x86_64"))
        self.assertEqual((info["gpu_vendor"], info["backend"]), ("amd", "vulkan"))
        self.assertEqual((info["memory_gib"], info["free_gib"]), (15.92, None))
        self.assertEqual(len(info["unknown"]), 2)

    def test_detect_apple_reads_metal_or_estimates(self):
        sysfs = self.patched(catalog, "linux_gpus")
        limit = self.patched(catalog, "_metal_working_set", return_value=40200896512)
        info = self.detect("darwin", unified=True, machine="arm64")
        self.assertEqual((info["os"], info["arch"]), ("macos", "arm64"))
        self.assertEqual((info["gpu_vendor"], info["backend"]), ("apple", "metal"))
        self.assertEqual((info["working_set_gib"], info["working_set_source"]), (37.44, "metal"))
        self.assertEqual(
            info["gpus"],
            [
                {
                    "vendor": "apple",
                    "name": "Test CPU",
                    "memory_gib": 48.0,
                    "free_gib": 30.0,
                    "source": "metal",
                    "pci_id": None,
                    "driver": None,
                }
            ],
        )
        self.assertEqual(info["unknown"], [])
        limit.return_value = 0
        with on_platform("darwin"):
            guessed = catalog._detect()
        self.assertEqual(
            (guessed["working_set_gib"], guessed["working_set_source"]),
            (36.0, "estimate"),
        )
        self.assertEqual(len(guessed["unknown"]), 1)
        sysfs.assert_not_called()
        catalog._nvidia_smi.assert_not_called()

    def test_detect_other_systems_probe_only_nvidia_smi(self):
        sysfs = self.patched(catalog, "linux_gpus")
        registry = self.patched(catalog, "_registry_rows")
        info = self.detect("darwin")
        self.assertEqual((info["os"], info["gpu_vendor"]), ("macos", "none"))
        self.assertEqual((info["backend"], info["memory_kind"]), ("cpu", "ram"))
        with on_platform("freebsd14"):
            self.assertEqual(catalog._detect()["os"], "other")
        with on_platform("linux"), patch.object(
            catalog.platform, "machine", return_value="aarch64"
        ):
            sysfs.return_value = []
            self.assertEqual(catalog._detect()["arch"], "arm64")
        registry.assert_not_called()

    def test_cpu_name_platform_branches(self):
        read = catalog._cpu_name.__wrapped__
        cpuinfo = "processor\t: 0\nmodel name\t: AMD Ryzen 7 9800X3D 8-Core Processor\n"
        with on_platform("linux"):
            with patch.object(catalog, "_text", return_value=cpuinfo):
                self.assertEqual(read(), "AMD Ryzen 7 9800X3D 8-Core Processor")
            with patch.object(catalog, "_text", return_value=""):
                self.assertEqual(read(), "")
        tree = {catalog.CPU_KEY: {"ProcessorNameString": "Intel(R) Core(TM) i9  "}}
        with on_platform("win32"):
            with patch.dict(sys.modules, {"winreg": fake_winreg(tree)}):
                self.assertEqual(read(), "Intel(R) Core(TM) i9")
            with patch.dict(sys.modules, {"winreg": fake_winreg({})}):
                self.assertEqual(read(), "")
            with patch.dict(sys.modules, {"winreg": None}):
                self.assertEqual(read(), "")
        with on_platform("darwin"):
            with patch.object(
                catalog.subprocess, "check_output", return_value="Apple M3 Max\n"
            ) as run:
                self.assertEqual(read(), "Apple M3 Max")
            self.assertEqual(run.call_args.kwargs["timeout"], 2)
            self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(run.call_args.kwargs["errors"], "replace")
            failure = subprocess.TimeoutExpired("sysctl", 2)
            with patch.object(catalog.subprocess, "check_output", side_effect=failure):
                self.assertEqual(read(), "")

    def test_apply_engine_build(self):
        radeon = computer([gpu("amd", "AMD Radeon RX 9070 XT", 15.92, 14.72, "sysfs")])
        before = copy.deepcopy(radeon)
        sized = catalog.apply_engine_build(radeon, "cpu")
        self.assertEqual(radeon, before)
        self.assertEqual((sized["backend"], sized["memory_kind"]), ("cpu", "ram"))
        self.assertEqual((sized["memory_gib"], sized["free_gib"]), (64.0, 40.0))
        self.assertEqual(sized["gpu_vendor"], "amd")
        self.assertEqual(len(sized["notes"]), len(radeon["notes"]) + 1)
        self.assertIn("CPU engine build", sized["notes"][-1])

        geforce = computer([gpu("nvidia", "NVIDIA GeForce RTX 4090", 23.99, 20.0)])
        swapped = catalog.apply_engine_build(geforce, "vulkan")
        self.assertEqual((swapped["backend"], swapped["memory_kind"]), ("vulkan", "vram"))
        self.assertEqual((swapped["memory_gib"], swapped["free_gib"]), (23.99, 20.0))
        self.assertEqual(geforce["backend"], "cuda")
        self.assertEqual(catalog.apply_engine_build(radeon, "cuda")["backend"], "cuda")

        for build in ("", "metal", "cuda", "rocm"):
            self.assertIs(catalog.apply_engine_build(geforce, build), geforce)
        tiny = computer([gpu("nvidia", "NVIDIA GeForce GT 1030", 2.0, 1.5)])
        chip = gpu("apple", "Apple M3 Max", 48.0, 30.24, "metal")
        mac = catalog.summarise(
            "macos", "arm64", "Apple M3 Max", 48.0, 30.24, [chip], True, 37.44, "metal"
        )
        for info in (tiny, mac, computer([])):
            for build in ("vulkan", "cuda", "cpu"):
                self.assertIs(catalog.apply_engine_build(info, build), info)

    def test_import_does_not_load_huggingface(self):
        code = (
            "import catalog, sys; "
            "print([name for name in ('huggingface_hub', 'runtime', 'setup_engines') "
            "if name in sys.modules])"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
        self.assertEqual(result.stdout.strip(), "[]", result.stderr)


class LibraryTest(TempCase):
    def test_families_token_match(self):
        listed = [
            "Exploration-7B.gguf",
            "mistral-7b-qlora-merged.Q4_K_M.gguf",
            "x-lorablated.gguf",
        ]
        hidden = [
            "Qwen3.8-27B-pi-lora-F16.gguf",
            "pi-lora.gguf",
            "mmproj-f16.gguf",
            "m.mmproj-f16.gguf",
            "adapter_model.gguf",
            "Projector-Q8.gguf",
            "Inkling-2-Mini-Vision.strata",
        ]
        rows = catalog.families((name, 7) for name in listed + hidden)
        self.assertEqual([name for name, _, _ in rows], listed)

    def test_discover_prunes_cache_git_node_modules(self):
        for folder in (".cache", ".git", "node_modules", "sub"):
            (self.tmp / folder / "deep").mkdir(parents=True)
            (self.tmp / folder / "deep" / "model.gguf").write_bytes(b"12345")
        (self.tmp / "notes.txt").write_text("not a model", encoding="utf8")
        kept = str(self.tmp / "sub" / "deep" / "model.gguf")
        self.assertEqual(catalog.discover(self.tmp), [(kept, [kept], 5)])
        self.assertEqual(catalog.discover(str(self.tmp)), [(kept, [kept], 5)])


class CatalogTest(TempCase):
    def parsed(self, data=None, accepts=accept_all, evidence=True):
        root = evidence_root(self.tmp) if evidence else self.tmp / "empty"
        return catalog.parse_catalog(data or fixture(), root, accepts)

    def test_shipped_catalog_is_valid(self):
        raw = json.loads(catalog.CATALOG_FILE.read_text(encoding="utf8"))
        meta, entries = catalog.parse_catalog(raw, catalog.ROOT, accept_all)
        self.assertEqual(meta["schema"], 1)
        self.assertEqual(len(entries), len(raw["picks"]))
        ids = [entry["id"] for entry in entries]
        self.assertEqual(len(ids), len(set(ids)))
        loaded = by_id(entries)
        for row in raw["picks"]:
            if row["status"] == "measured":
                self.assertEqual(loaded[row["id"]]["status"], "measured", row["id"])
        for backend in catalog.BACKENDS:
            self.assertTrue(
                any(
                    backend in entry["backends"] and entry["min_memory_gib"] == 0
                    for entry in entries
                ),
                backend,
            )
        plain = catalog.parse_catalog(raw, catalog.ROOT, None)[1]
        self.assertTrue(plain)
        self.assertFalse(any("engine_args" in entry["settings"] for entry in plain))

    def test_inkling_gguf_models_list_the_dspark_drafter(self):
        raw = json.loads(catalog.CATALOG_FILE.read_text(encoding="utf8"))
        drafter = {
            "path": "dspark-Inkling-2-Mini-Q8_0.gguf",
            "bytes": 503889952,
            "sha256": "3da00cf7c31bbe5662b3c0d373ecf782d74d32ef43990acc158ce60c2add72e0",
            "role": "draft",
            "repo": "unsloth/Inkling-2-Mini-GGUF",
            "revision": "8078d149286eec7193795413b9e0643da6d11fa1",
        }
        self.assertEqual(drafter["path"], runtime.DSPARK)
        listing = []
        for model in raw["models"]:
            drafts = [f for f in model["files"] if f["role"] == "draft"]
            if model["family"] == "inkling-2-mini":
                listing.append(model["id"])
                self.assertEqual(drafts, [drafter], model["id"])
                self.assertEqual(model["files"][0]["role"], "weights")
            else:
                self.assertEqual(drafts, [], model["id"])
        self.assertEqual(len(listing), 3)
        entries = catalog.parse_catalog(raw, catalog.ROOT, accept_all)[1]
        for entry in entries:
            with self.subTest(entry=entry["id"]):
                self.assertNotIn("draft", [f["role"] for f in entry["files"]])
                if entry["model"] not in listing:
                    self.assertNotIn("drafter", entry)
                    continue
                self.assertFalse(entry["settings"]["draft"])
                self.assertEqual(
                    entry["drafter"],
                    [
                        dict(
                            repo=drafter["repo"],
                            revision=drafter["revision"],
                            path=drafter["path"],
                            name=runtime.DSPARK,
                            bytes=drafter["bytes"],
                            sha256=drafter["sha256"],
                            role="draft",
                        )
                    ],
                )

    def test_draft_files_follow_the_draft_setting(self):
        data = fixture()
        data["models"][0]["files"].append(
            {"path": "Alpha-draft.gguf", "bytes": 5, "sha256": "ab" * 32, "role": "draft"}
        )
        data["picks"][4]["settings"]["draft"] = True
        joined = by_id(self.parsed(data)[1])
        drafted, plain = joined["alpha-peak"], joined["alpha-q8"]
        self.assertEqual(drafted["files"][-1]["name"], "Alpha-draft.gguf")
        self.assertEqual(drafted["download_bytes"], 1455)
        self.assertNotIn("draft", [f["role"] for f in plain["files"]])
        self.assertEqual(plain["drafter"][0]["name"], "Alpha-draft.gguf")
        self.assertNotIn("drafter", joined["beta-fp8"])
        data["models"][0]["files"][-1]["sha256"] = None
        with self.assertRaises(ValueError):
            self.parsed(data)

    def test_parse_catalog_rejects_bad_values(self):
        self.assertEqual(len(self.parsed()[1]), 6)

        def model(data, index=0):
            return data["models"][index]

        def choice(data, index=0):
            return data["picks"][index]

        def setting(key, value, index=0):
            return lambda data: choice(data, index)["settings"].__setitem__(key, value)

        def strata_with_args(data):
            choice(data, 5)["settings"]["engine_args"] = list(Q8)

        mutations = {
            "schema 2": lambda d: d.update(schema=2),
            "schema true": lambda d: d.update(schema=True),
            "models not a list": lambda d: d.update(models={}),
            "branch revision": lambda d: model(d).update(revision="main"),
            "escaping path": lambda d: model(d)["files"][0].update(path="../x.gguf"),
            "dotted path": lambda d: model(d)["files"][0].update(path="a/..b/x.gguf"),
            "weights without sha256": lambda d: model(d)["files"][0].update(sha256=None),
            "zero bytes": lambda d: model(d)["files"][0].update(bytes=0),
            "file repo without revision": lambda d: model(d)["files"][0].update(
                repo="org/Other"
            ),
            "duplicate file name": lambda d: model(d)["files"].append(
                dict(model(d)["files"][2])
            ),
            "no weights": lambda d: model(d, 1)["files"][0].update(role="companion"),
            "unknown family": lambda d: model(d).update(family="missing"),
            "family not text": lambda d: model(d).update(family=["plain"]),
            "family number missing": lambda d: d["families"]["plain"].pop("compute_gib"),
            "family number infinite": lambda d: d["families"]["plain"].update(
                compute_gib=float("inf")
            ),
            "unknown engine": lambda d: model(d).update(engine="Auto"),
            "blank label": lambda d: model(d).update(label="  "),
            "status tested": lambda d: choice(d).update(status="tested"),
            "measured without evidence": lambda d: choice(d).pop("evidence"),
            "measured without measured_on": lambda d: choice(d).pop("measured_on"),
            "evidence outside results": lambda d: choice(d).update(
                evidence="results/../secret.json"
            ),
            "context true": setting("context", True),
            "context too small": setting("context", 512),
            "kv q8_0": setting("kv", "q8_0"),
            "lora not a bool": setting("lora", 1),
            "odd engine_args": setting("engine_args", [*Q8, "--cache-ram"]),
            "engine_args value with space": setting(
                "engine_args", ["--spec-type", "draft mtp"]
            ),
            "engine_args value with semicolon": setting(
                "engine_args", ["--spec-type", "a;b"]
            ),
            "engine_args repeated flag": setting(
                "engine_args", ["--cache-ram", "1", "--cache-ram", "2"]
            ),
            "engine_args bad flag": setting("engine_args", ["-c", "1"]),
            "cache type k without v": setting("engine_args", ["--cache-type-k", "q8_0"]),
            "cache types differ": setting(
                "engine_args", ["--cache-type-k", "q8_0", "--cache-type-v", "f16"]
            ),
            "unknown cache type": setting(
                "engine_args", ["--cache-type-k", "q5_1", "--cache-type-v", "q5_1"]
            ),
            "settings model": setting("model", "/tmp/x.gguf"),
            "settings engine": setting("engine", "Auto"),
            "Strata on vulkan": lambda d: choice(d, 5).update(
                backends=["cuda", "vulkan"]
            ),
            "Strata with engine_args": strata_with_args,
            "duplicate pick id": lambda d: choice(d, 1).update(id="alpha-q8"),
            "pick model unknown": lambda d: choice(d).update(model="gamma"),
            "pick model not text": lambda d: choice(d).update(model=["alpha-q4"]),
            "unknown backend": lambda d: choice(d).update(backends=["cuda", "rocm"]),
            "repeated backend": lambda d: choice(d).update(backends=["cuda", "cuda"]),
            "tier not a number": lambda d: choice(d).update(min_memory_gib=float("nan")),
            "rank zero": lambda d: choice(d).update(rank=0),
            "tool_calling": lambda d: choice(d).update(tool_calling="great"),
            "why too long": lambda d: choice(d).update(why="x" * 241),
            "extras": lambda d: choice(d).update(extras=[{"label": "Head", "gib": -1}]),
            "need_gib": lambda d: choice(d).update(need_gib=0),
            "need_gib beyond a float": lambda d: choice(d).update(need_gib=10**400),
            "needs_engine_args": lambda d: choice(d).update(needs_engine_args="yes"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name):
                data = fixture()
                mutate(data)
                with self.assertRaises(ValueError) as caught:
                    self.parsed(data)
                self.assertEqual(str(caught.exception), catalog.DAMAGED)
                self.assertTrue(
                    str(caught.exception.__cause__).startswith("model_catalog.json: ")
                )
        for data in (None, [], {"schema": 1}):
            with self.assertRaises(ValueError) as caught:
                catalog.parse_catalog(data, self.tmp, None)
            self.assertEqual(str(caught.exception), catalog.DAMAGED)

    def test_parse_catalog_engine_args(self):
        plain = by_id(self.parsed(accepts=None)[1])
        self.assertNotIn("engine_args", plain["alpha-q8"]["settings"])
        self.assertEqual(plain["alpha-q8"]["kv"], "f16")
        self.assertNotIn("alpha-mmap", plain)
        self.assertEqual(plain["alpha-pi"]["kv"], "q8_0")
        self.assertEqual(plain["alpha-peak"]["kv"], "f16")
        self.assertEqual(plain["beta-fp8"]["kv"], "fp8")

        limited = by_id(self.parsed(accepts=allow_list)[1])
        self.assertEqual(limited["alpha-q8"]["settings"]["engine_args"], Q8)
        self.assertEqual(limited["alpha-q8"]["kv"], "q8_0")
        self.assertNotIn("alpha-mmap", limited)
        self.assertNotIn("engine_args", limited["alpha-lazy"]["settings"])
        self.assertEqual(limited["alpha-lazy"]["kv"], "f16")

        every = by_id(self.parsed(accepts=accept_all)[1])
        self.assertIn("alpha-mmap", every)
        self.assertEqual(every["alpha-lazy"]["kv"], "q8_0")
        self.assertEqual(
            every["alpha-lazy"]["settings"]["engine_args"], [*Q8, "--lazy-mode", "on"]
        )

        data = fixture()
        data["picks"][5]["settings"]["kv"] = "auto"
        self.assertEqual(by_id(self.parsed(data)[1])["beta-fp8"]["kv"], "f16")
        data = fixture()
        data["picks"][1]["settings"]["engine_args"] = ["--load-mode", "bad value"]
        with self.assertRaises(ValueError):
            self.parsed(data, accepts=None)

    def test_parse_catalog_downgrades_measured_without_evidence(self):
        kept = by_id(self.parsed()[1])["alpha-q8"]
        self.assertEqual(kept["status"], "measured")
        self.assertEqual(kept["evidence"], "results/alpha.json")
        self.assertEqual(kept["measured_on"], "a test card")
        lowered = by_id(self.parsed(evidence=False)[1])["alpha-q8"]
        self.assertEqual(lowered["status"], "suggested")
        self.assertNotIn("evidence", lowered)
        self.assertNotIn("measured_on", lowered)
        untested = by_id(self.parsed()[1])["alpha-pi"]
        self.assertNotIn("evidence", untested)

    def test_suggested_pick_keeps_its_evidence_object(self):
        data = fixture()
        named = dict(TRIAL, file="results/alpha.json")
        data["picks"][4]["evidence"] = dict(
            named, machine=" a test Mac ", power="battery", tokens_per_second=41.5
        )
        data["picks"][3]["evidence"] = "results/alpha.json"
        for evidence in (True, False):
            loaded = by_id(self.parsed(copy.deepcopy(data), evidence=evidence)[1])
            tried = loaded["alpha-peak"]
            self.assertEqual(tried["evidence"], named if evidence else TRIAL)
            self.assertEqual(tried["status"], "suggested")
            self.assertNotIn("measured_on", tried)
            self.assertEqual(
                tried["memory_model"], {"method": "measured", "need_gib": 3.5}
            )
            self.assertNotIn("evidence", loaded["alpha-pi"])
        mutations = {
            "machine missing": lambda trial: trial.pop("machine"),
            "machine blank": lambda trial: trial.update(machine="  "),
            "machine too long": lambda trial: trial.update(machine="x" * 61),
            "machine not text": lambda trial: trial.update(machine=48),
            "peak zero": lambda trial: trial.update(peak_memory_gib=0),
            "peak true": lambda trial: trial.update(peak_memory_gib=True),
            "peak text": lambda trial: trial.update(peak_memory_gib="3.5"),
            "peak infinite": lambda trial: trial.update(peak_memory_gib=float("inf")),
            "peak beyond a float": lambda trial: trial.update(peak_memory_gib=10**400),
            "context missing": lambda trial: trial.pop("context"),
            "context too small": lambda trial: trial.update(context=512),
            "context not whole": lambda trial: trial.update(context=32768.0),
            "tool_call unknown": lambda trial: trial.update(tool_call="maybe"),
            "tool_call missing": lambda trial: trial.pop("tool_call"),
            "file outside results": lambda trial: trial.update(file="secret.json"),
            "file escaping": lambda trial: trial.update(file="results/../secret.json"),
            "file not text": lambda trial: trial.update(file=["results/alpha.json"]),
            "file null": lambda trial: trial.update(file=None),
        }
        for name, mutate in mutations.items():
            with self.subTest(name):
                data = fixture()
                data["picks"][4]["evidence"] = dict(TRIAL)
                mutate(data["picks"][4]["evidence"])
                with self.assertRaises(ValueError) as caught:
                    self.parsed(data)
                self.assertEqual(str(caught.exception), catalog.DAMAGED)
                self.assertIn(
                    "pick alpha-peak evidence", str(caught.exception.__cause__)
                )
        for odd in ([dict(TRIAL)], [], {}, 3.5, True, None):
            with self.subTest(repr(odd)):
                data = fixture()
                data["picks"][4]["evidence"] = odd
                with self.assertRaises(ValueError) as caught:
                    self.parsed(data)
                self.assertEqual(str(caught.exception), catalog.DAMAGED)
                self.assertIn(
                    "pick alpha-peak evidence", str(caught.exception.__cause__)
                )
        data = fixture()
        data["picks"][0]["evidence"] = dict(TRIAL)
        with self.assertRaises(ValueError):
            self.parsed(data)

    def test_unreadable_results_folder_counts_as_missing(self):
        data = fixture()
        data["picks"][4]["evidence"] = dict(TRIAL, file="results/alpha.json")
        root = evidence_root(self.tmp)
        denied = PermissionError(errno.EACCES, "Permission denied")
        with patch.object(Path, "is_file", side_effect=denied) as asked:
            loaded = by_id(catalog.parse_catalog(data, root, accept_all)[1])
        self.assertEqual(asked.call_count, 2)
        self.assertEqual(loaded["alpha-q8"]["status"], "suggested")
        self.assertNotIn("evidence", loaded["alpha-q8"])
        self.assertNotIn("measured_on", loaded["alpha-q8"])
        self.assertEqual(loaded["alpha-peak"]["evidence"], TRIAL)

    def test_shipped_catalog_carries_apple_evidence(self):
        raw = json.loads(catalog.CATALOG_FILE.read_text(encoding="utf8"))
        for root in (catalog.ROOT, self.tmp):
            loaded = by_id(catalog.parse_catalog(raw, root, accept_all)[1])
            tried = 0
            for row in raw["picks"]:
                if isinstance(row.get("evidence"), dict):
                    tried += 1
                    kept = loaded[row["id"]]["evidence"]
                    stated = dict(row["evidence"])
                    if not (root / stated.get("file", "absent")).is_file():
                        stated.pop("file", None)
                    self.assertEqual(kept, stated, row["id"])
                    self.assertEqual(
                        loaded[row["id"]]["memory_model"]["need_gib"],
                        kept["peak_memory_gib"],
                    )
            self.assertEqual(tried, 6)
        without_results = list(loaded.values())
        mac = catalog.summarise(
            "macos", "arm64", "Apple M3 Max", 48.0, 30.0, [], True, 37.44, "metal"
        )
        self.patched(catalog, "hardware_info", return_value=mac)
        self.patched(catalog, "_engine_build", return_value="metal")
        self.patched(catalog, "_engines", return_value=None)
        self.patched(runtime, "settings", return_value={"library": str(self.tmp)})
        self.patched(
            catalog, "load_catalog", return_value=({"schema": 1}, without_results)
        )
        advice = catalog.dispatch("model-recommend", {})
        self.assertEqual(advice["pick"]["id"], "metal-36-qwen3.8-q4")
        self.assertEqual(
            advice["pick"]["evidence"],
            {
                "machine": "an M3 Max 48 GB",
                "peak_memory_gib": 24.1,
                "context": 32768,
                "tool_call": "passed",
            },
        )
        self.assertEqual(
            advice["reason"],
            "Fits this Mac with room to spare: its recorded peak is 24.1 GiB at 32K "
            "context, and a model can use 36.4 of the 48.0 GiB of unified memory here. "
            "A suggestion tested on an M3 Max 48 GB, where it loaded, answered and "
            "called a tool.",
        )
        self.assertEqual(
            [item["id"] for item in advice["alternatives"]],
            ["metal-32-qwen3.8-iq3", "metal-32-inkling-q4"],
        )
        listed = catalog.dispatch("model-catalog", {})
        self.assertGreaterEqual(len(listed["entries"]), 6)
        for item in listed["entries"]:
            self.assertIn("metal", item["backends"], item["id"])
        shown = json.dumps([advice, listed]).lower()
        for word in ("battery", "mains", "power", "speed", "tok/s", "per second"):
            self.assertNotIn(word, shown)

    def test_entry_join(self):
        data = fixture()
        data["picks"][4]["settings"].update(vision=True, surprise="ignored")
        meta, entries = self.parsed(data)
        self.assertEqual(
            meta, {"schema": 1, "updated": "2026-11-22", "engine_build": "b12633"}
        )
        self.assertEqual([entry["id"] for entry in entries][0], "alpha-q8")
        joined = by_id(entries)

        adapted = joined["alpha-pi"]
        self.assertEqual(
            [file["name"] for file in adapted["files"]],
            [
                "Alpha-Q4-00001-of-00002.gguf",
                "Alpha-Q4-00002-of-00002.gguf",
                "chat_template.jinja",
                "Alpha-pi-lora-F16.gguf",
            ],
        )
        self.assertEqual(
            adapted["files"][3],
            {
                "repo": "hub/Alpha-adapted",
                "revision": REV_OTHER,
                "path": "adapters/Alpha-pi-lora-F16.gguf",
                "name": "Alpha-pi-lora-F16.gguf",
                "bytes": 30,
                "sha256": SHA["c"],
                "role": "lora",
            },
        )
        self.assertEqual(adapted["files"][0]["repo"], "org/Alpha-GGUF")
        self.assertEqual(adapted["files"][0]["revision"], REV)
        self.assertIsNone(adapted["files"][2]["sha256"])
        self.assertEqual(
            (adapted["download_bytes"], adapted["resident_bytes"], adapted["lora_bytes"]),
            (1480, 1000, 30),
        )
        self.assertEqual(
            adapted["settings"],
            {
                "engine": "llama.cpp",
                "kv": "auto",
                "draft": False,
                "lora": True,
                "vision": False,
                "context": 131072,
                "thinking": "off",
            },
        )
        self.assertEqual(adapted["extras"], [{"label": "Draft head", "gib": 0.5}])
        self.assertEqual(
            adapted["memory_model"],
            {
                "method": "formula",
                "kv_kib_per_token": 64,
                "kv_fixed_gib": 0.0,
                "compute_gib": 1.0,
                "compute_gib_metal": 2.0,
                "token_overhead_bytes": 0,
            },
        )
        self.assertEqual(
            (adapted["model"], adapted["label"], adapted["licence"], adapted["licence_note"]),
            ("alpha-q4", "Alpha Q4", "Apache-2.0", ""),
        )

        plain = joined["alpha-q8"]
        self.assertEqual(
            [file["role"] for file in plain["files"]],
            ["weights", "weights", "companion"],
        )
        self.assertEqual((plain["download_bytes"], plain["lora_bytes"]), (1450, 0))

        seeing = joined["alpha-peak"]
        self.assertEqual(seeing["files"][-1]["name"], "mmproj-Alpha.gguf")
        self.assertEqual(seeing["download_bytes"], 1520)
        self.assertNotIn("surprise", seeing["settings"])
        self.assertEqual(seeing["memory_model"], {"method": "measured", "need_gib": 3.5})

        packed = joined["beta-fp8"]
        self.assertEqual(packed["memory_model"], {"method": "tier"})
        self.assertEqual(packed["settings"]["engine"], "Strata")
        self.assertEqual(packed["settings"]["kv"], "fp8")
        self.assertEqual(packed["licence_note"], "Read the Beta terms before you use it.")
        self.assertEqual((packed["backends"], packed["min_memory_gib"]), (["cuda"], 16))

    def test_installed_by_size(self):
        item = by_id(self.parsed()[1])["alpha-q8"]
        library = self.tmp / "models"
        folder = library / "org--Alpha-GGUF"
        self.assertEqual(catalog.destination(item, library), folder)
        self.assertEqual(
            catalog.installed(item, library),
            {"installed": False, "model": "", "have_bytes": 0, "total_bytes": 1450},
        )
        folder.mkdir(parents=True)
        first = folder / "Alpha-Q4-00001-of-00002.gguf"
        (folder / (first.name + ".part")).write_bytes(b"x" * 300)
        self.assertEqual(catalog.installed(item, library)["have_bytes"], 300)
        (folder / "chat_template.jinja.part").write_bytes(b"x" * 90)
        self.assertEqual(catalog.installed(item, library)["have_bytes"], 350)
        first.write_bytes(b"x" * 999)
        (folder / "Alpha-Q4-00002-of-00002.gguf").write_bytes(b"x" * 400)
        (folder / "chat_template.jinja").write_bytes(b"x" * 50)
        partial = catalog.installed(item, library)
        self.assertEqual((partial["installed"], partial["have_bytes"]), (False, 750))
        first.write_bytes(b"x" * 1000)
        self.assertEqual(
            catalog.installed(item, library),
            {
                "installed": True,
                "model": str(first),
                "have_bytes": 1450,
                "total_bytes": 1450,
            },
        )

    def test_recommendation_uses_engine_build_and_engines(self):
        items = self.parsed()[1]
        library = str(self.tmp / "models")
        self.patched(catalog, "entries", side_effect=lambda: copy.deepcopy(items))
        self.patched(runtime, "settings", return_value={"library": library})
        build = self.patched(catalog, "_engine_build", return_value="cpu")
        engines = self.patched(catalog, "_engines", return_value=None)
        radeon = computer(
            [gpu("amd", "AMD Radeon RX 9070 XT", 15.92, 14.72, "sysfs")], ram=32.0
        )
        read = self.patched(catalog, "hardware_info", return_value=radeon)

        sized = catalog.recommendation()
        read.assert_called_once_with(False)
        self.assertIn("cpu", sized["pick"]["backends"])
        self.assertEqual((sized["build"], sized["backend"]), ("cpu", "cpu"))
        self.assertEqual(sized["pick"]["sizing"]["kind"], "ram")
        self.assertEqual(sized["hardware_label"], radeon["label"])
        self.assertTrue(any("CPU engine build" in line for line in sized["warnings"]))
        self.assertEqual(
            sized["pick"]["installed"],
            {
                "installed": False,
                "model": "",
                "have_bytes": 0,
                "total_bytes": sized["pick"]["download_bytes"],
            },
        )
        self.assertTrue(sized["reason"].endswith("."))

        build.return_value = "cuda"
        read.return_value = computer(
            [gpu("nvidia", "NVIDIA GeForce RTX 4090", 23.99, 20.0)]
        )
        open_choice = catalog.recommendation(refresh=True)
        read.assert_called_with(True)
        self.assertEqual(open_choice["pick"]["engine"], "Strata")
        self.assertEqual(open_choice["build"], "cuda")
        for item in [open_choice["pick"], *open_choice["alternatives"]]:
            self.assertIn("installed", item)
        self.assertTrue(open_choice["alternatives"])

        engines.return_value = ["Auto", "llama.cpp", "Custom"]
        limited = catalog.recommendation()
        chosen = [limited["pick"], *limited["alternatives"]]
        self.assertEqual({item["engine"] for item in chosen}, {"llama.cpp"})
        self.assertEqual(limited["pick"]["id"], "alpha-q8")

    def test_engine_probes_degrade_quietly(self):
        broken = types.SimpleNamespace()
        with patch.dict(sys.modules, {"setup_engines": broken}):
            self.assertEqual(catalog._engine_build(), "")
        ready = types.SimpleNamespace(
            installed_backend=lambda: "", detect=lambda: {"backend": "vulkan"}
        )
        with patch.dict(sys.modules, {"setup_engines": ready}):
            self.assertEqual(catalog._engine_build(), "vulkan")
            ready.installed_backend = lambda: "cpu"
            self.assertEqual(catalog._engine_build(), "cpu")
        names = ("Auto", "llama.cpp", "Custom")
        with patch.object(runtime, "available_engines", lambda: names, create=True):
            self.assertEqual(catalog._engines(), list(names))
        with patch.object(
            runtime, "available_engines", Mock(side_effect=OSError), create=True
        ):
            self.assertIsNone(catalog._engines())

    def test_load_catalog_reads_the_file_and_the_runtime_allow_list(self):
        load = catalog.load_catalog.__wrapped__
        root = evidence_root(self.tmp)
        path = root / "model_catalog.json"
        self.patched(catalog, "ROOT", root)
        self.patched(catalog, "CATALOG_FILE", path)
        for text in (None, "{not json", "[]"):
            if text is not None:
                path.write_text(text, encoding="utf8")
            with self.assertRaises(ValueError) as caught:
                load()
            self.assertEqual(str(caught.exception), catalog.DAMAGED)
        path.write_text(json.dumps(fixture()), encoding="utf8")
        with patch.dict(runtime.DEFAULTS, clear=False) as defaults:
            defaults.pop("engine_args", None)
            self.assertNotIn("engine_args", by_id(load()[1])["alpha-q8"]["settings"])
            defaults["engine_args"] = []
            with patch.object(runtime, "llama_args", allow_list, create=True):
                loaded = by_id(load()[1])
        self.assertEqual(loaded["alpha-q8"]["settings"]["engine_args"], Q8)
        self.assertEqual(loaded["alpha-q8"]["status"], "measured")
        self.assertNotIn("alpha-mmap", loaded)

    def test_entries_are_copies_and_unknown_ids_are_refused(self):
        self.patched(catalog, "load_catalog", return_value=self.parsed())
        catalog.entries()[0]["settings"]["context"] = 1
        self.assertEqual(catalog.entry("alpha-q8")["settings"]["context"], 32768)
        with self.assertRaises(ValueError) as caught:
            catalog.entry("gone")
        self.assertEqual(
            str(caught.exception),
            "That model is no longer in the catalog. Reopen the model list and "
            "choose again.",
        )


class DownloadCase(TempCase):
    def setUp(self):
        super().setUp()
        self.patched(catalog, "CHUNK", 1000)
        self.patched(catalog, "RETRY_WAIT", 0)
        self.addCleanup(catalog.library_rows.cache_clear)
        self.data = bytes(range(256)) * 41 + b"tail"
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.dest = self.tmp / "file.gguf"
        self.part = self.tmp / "file.gguf.part"

    def fetch(self, session, **more):
        options = dict(size=len(self.data), sha256=self.sha, session=session)
        options.update(more)
        return catalog.fetch(URL, self.dest, **options)


class DownloadTest(DownloadCase):
    def setUp(self):
        super().setUp()
        self.patched(catalog, "_hub_headers", return_value={})

    def item(self):
        weights, template = self.data, b"{{ messages }}"
        files = [
            dict(
                repo="org/Alpha-GGUF",
                revision=REV,
                path="sub/Alpha-Q4.gguf",
                name="sub/Alpha-Q4.gguf",
                bytes=len(weights),
                sha256=self.sha,
                role="weights",
            ),
            dict(
                repo="org/Alpha-GGUF",
                revision=REV,
                path="chat_template.jinja",
                name="chat_template.jinja",
                bytes=len(template),
                sha256=None,
                role="companion",
            ),
        ]
        base = f"{catalog.HUB}/org/Alpha-GGUF/resolve/{REV}/"
        served = {
            base + "sub/Alpha-Q4.gguf": weights,
            base + "chat_template.jinja": template,
        }
        item = dict(
            id="alpha-q8",
            model="alpha-q4",
            label="Alpha Q4",
            repo="org/Alpha-GGUF",
            files=files,
            download_bytes=len(weights) + len(template),
            settings=dict(
                engine="llama.cpp",
                kv="auto",
                draft=False,
                lora=False,
                vision=False,
                context=32768,
                engine_args=list(Q8),
            ),
        )
        return item, served

    def test_fetch_downloads_and_verifies(self):
        session = FakeSession(self.data)
        phases = []
        result = self.fetch(session, progress=lambda phase, done: phases.append((phase, done)))
        self.assertEqual(result, self.dest)
        self.assertEqual(self.dest.read_bytes(), self.data)
        self.assertFalse(self.part.exists())
        self.assertEqual(session.calls, [(URL, {})])
        self.assertEqual(phases[0], ("downloading", 1000))
        self.assertEqual(phases[-1], ("downloading", len(self.data)))
        self.assertFalse(session.closed)

    def test_fetch_resumes_after_drop(self):
        session = FakeSession(self.data, fail_after=3000)
        self.fetch(session)
        self.assertEqual(len(session.calls), 2)
        self.assertNotIn("Range", session.calls[0][1])
        self.assertEqual(session.calls[1][1]["Range"], "bytes=3000-")
        self.assertEqual(self.dest.read_bytes(), self.data)
        self.assertFalse(self.part.exists())

    def test_fetch_cancel_keeps_part_then_resumes(self):
        stop = threading.Event()

        def watch(phase, done):
            if done >= 4000:
                stop.set()

        with self.assertRaises(Cancelled) as caught:
            self.fetch(FakeSession(self.data), progress=watch, cancel=stop)
        self.assertEqual(str(caught.exception), catalog.CANCELLED)
        self.assertEqual(self.part.stat().st_size, 4000)
        self.assertFalse(self.dest.exists())

        session, phases = FakeSession(self.data), []
        self.fetch(session, progress=lambda phase, done: phases.append(phase))
        self.assertEqual(session.calls[0][1]["Range"], "bytes=4000-")
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(phases[0], "checking")
        self.assertEqual(phases[-1], "downloading")
        self.assertEqual(self.dest.read_bytes(), self.data)

    def test_fetch_stops_before_asking_when_already_cancelled(self):
        stop = threading.Event()
        stop.set()
        session = FakeSession(self.data)
        with self.assertRaises(Cancelled):
            self.fetch(session, cancel=stop)
        self.assertEqual(session.calls, [])
        self.part.write_bytes(self.data[:2500])
        with self.assertRaises(Cancelled):
            self.fetch(session, cancel=stop)
        self.assertEqual(self.part.stat().st_size, 2500)

    def test_fetch_restarts_when_range_ignored(self):
        self.part.write_bytes(b"x" * 2000)
        session = FakeSession(self.data, honour_range=False)
        self.fetch(session)
        self.assertEqual(session.calls[0][1]["Range"], "bytes=2000-")
        self.assertEqual(self.dest.read_bytes(), self.data)

    def test_fetch_checksum_mismatch_removes_part(self):
        with self.assertRaises(ValueError) as caught:
            self.fetch(FakeSession(self.data), sha256="0" * 64)
        self.assertEqual(
            str(caught.exception),
            "file.gguf failed its checksum, so the damaged download was removed. "
            "Start the download again.",
        )
        self.assertFalse(self.dest.exists())
        self.assertFalse(self.part.exists())

    def test_fetch_complete_part_needs_no_request(self):
        self.part.write_bytes(self.data)
        session = FakeSession(self.data)
        self.fetch(session)
        self.assertEqual(session.calls, [])
        self.assertEqual(self.dest.read_bytes(), self.data)
        self.assertFalse(self.part.exists())

    def test_fetch_drops_an_oversized_part(self):
        self.part.write_bytes(self.data + b"extra")
        session = FakeSession(self.data)
        self.fetch(session)
        self.assertEqual(session.calls, [(URL, {})])
        self.assertEqual(self.dest.read_bytes(), self.data)

    def test_fetch_existing_file(self):
        self.dest.write_bytes(self.data)
        session, phases = FakeSession(self.data), []
        self.fetch(session, progress=lambda phase, done: phases.append(phase))
        self.assertEqual(session.calls, [])
        self.assertEqual(set(phases), {"checking"})
        for mine in (b"mine", bytes(len(self.data))):
            self.dest.write_bytes(mine)
            with self.assertRaises(ValueError) as caught:
                self.fetch(session)
            self.assertEqual(
                str(caught.exception),
                "file.gguf is already in your model folder but does not match the "
                "expected file. Move or delete that file, then download again.",
            )
            self.assertEqual(self.dest.read_bytes(), mine)
        self.assertEqual(session.calls, [])
        self.assertFalse(self.part.exists())

    def test_fetch_http_errors(self):
        for status, words in (
            (404, "is no longer available from org/repo"),
            (401, "refused access to org/repo"),
            (403, "refused access to org/repo"),
            (503, "answered with HTTP 503 for file.gguf"),
        ):
            session = FakeSession(self.data, status=status)
            with self.assertRaises(ValueError) as caught:
                self.fetch(session, label="org/repo")
            message = str(caught.exception)
            self.assertEqual(message, catalog._refused(status, "file.gguf", "org/repo"))
            self.assertIn(words, message)
            self.assertEqual(len(session.calls), 1)
            self.assertFalse(self.dest.exists())
            self.assertFalse(self.part.exists())

    def test_fetch_short_body(self):
        session = FakeSession(self.data)
        with self.assertRaises(ValueError) as caught:
            self.fetch(session, size=len(self.data) + 5, sha256=None)
        self.assertEqual(
            str(caught.exception),
            "The download of file.gguf stopped at 0.0 of 0.0 GiB. Check your "
            "connection, then start it again to resume.",
        )
        self.assertEqual(self.part.read_bytes(), self.data)
        self.assertFalse(self.dest.exists())
        self.assertEqual(len(session.calls), 2)

    def test_fetch_gives_up_after_four_attempts(self):
        session = FakeSession(self.data, offline=True)
        with self.assertRaises(ValueError) as caught:
            self.fetch(session)
        self.assertIn("stopped at", str(caught.exception))
        self.assertEqual(len(session.calls), 4)
        session = FakeSession(self.data, offline=True)
        with self.assertRaises(ValueError) as caught:
            self.fetch(session, size=None, sha256=None)
        self.assertIn("stopped before it finished", str(caught.exception))
        self.assertEqual(len(session.calls), 4)
        self.assertFalse(self.dest.exists())

    def test_fetch_without_a_known_size(self):
        session = FakeSession(self.data, fail_after=3000)
        self.fetch(session, size=None)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(self.dest.read_bytes(), self.data)

    def test_fetch_passes_headers(self):
        headers = {"Authorization": "Bearer test-token"}
        session = FakeSession(self.data, fail_after=2000)
        self.fetch(session, headers=headers)
        self.assertEqual(session.calls[0][1], {"Authorization": "Bearer test-token"})
        self.assertEqual(
            session.calls[1][1],
            {"Authorization": "Bearer test-token", "Range": "bytes=2000-"},
        )
        self.assertEqual(headers, {"Authorization": "Bearer test-token"})

    def test_fetch_owns_and_closes_a_default_session(self):
        session = FakeSession(self.data)
        with patch.object(catalog.requests, "Session", return_value=session):
            catalog.fetch(URL, self.dest, len(self.data), self.sha)
        self.assertTrue(session.closed)
        self.assertEqual(self.dest.read_bytes(), self.data)

    def test_cancel_error_type(self):
        class Stopped(RuntimeError):
            pass

        with patch.object(runtime, "Cancelled", Stopped, create=True):
            error = catalog._cancel_error()
        self.assertIs(type(error), Stopped)
        self.assertEqual(str(error), catalog.CANCELLED)
        if not hasattr(runtime, "Cancelled"):
            self.assertIs(type(catalog._cancel_error()), catalog.DownloadCancelled)
        self.assertTrue(issubclass(catalog.DownloadCancelled, RuntimeError))

    def test_stop_token_follows_both_sources(self):
        outer = threading.Event()
        stop = catalog._Stop(outer)
        self.assertFalse(stop.wait(0.01))
        outer.set()
        self.assertTrue(stop.is_set())
        self.assertTrue(stop.wait(5))
        own = catalog._Stop()
        self.assertFalse(own.is_set())
        own.set()
        self.assertTrue(own.wait(5))
        self.assertFalse(catalog._pause(None, 0))
        self.assertTrue(catalog._pause(own, 5))
        polled = types.SimpleNamespace(is_set=lambda: True)
        self.assertTrue(catalog._pause(polled, 0))

    def test_download_entry_writes_files_and_reports(self):
        item, served = self.item()
        self.patched(catalog, "entry", return_value=item)
        library = self.tmp / "models"
        self.assertEqual(catalog.library_rows(str(library)), [])
        events, running = [], []

        def progress(state):
            events.append(state)
            running.append(catalog.jobs())

        session = FakeSession(by_url=served)
        result = catalog.download_entry("alpha-q8", library, progress, None, session)
        folder = library / "org--Alpha-GGUF"
        self.assertEqual((folder / "sub" / "Alpha-Q4.gguf").read_bytes(), self.data)
        self.assertEqual((folder / "chat_template.jinja").read_bytes(), b"{{ messages }}")
        self.assertEqual(list(folder.rglob("*.part")), [])
        self.assertEqual([url for url, _ in session.calls], list(served))

        for state in events:
            self.assertEqual(state["job"], "model-download")
            self.assertTrue(state["message"])
            self.assertEqual((state["id"], state["key"]), ("alpha-q8", "alpha-q4"))
            self.assertEqual(state["total"], item["download_bytes"])
            json.dumps(state)
        self.assertEqual(events[0]["phase"], "checking")
        self.assertEqual(events[0]["message"], "Checking sub/Alpha-Q4.gguf")
        self.assertEqual(events[-1]["phase"], "done")
        self.assertEqual(events[-1]["message"], "Alpha Q4 is ready")
        self.assertEqual(events[-1]["done"], events[-1]["total"])
        self.assertEqual(events[-1]["rate"], 0)
        self.assertIn("alpha-q4", running[0])
        self.assertEqual(running[0]["alpha-q4"]["id"], "alpha-q8")
        self.assertEqual(catalog.jobs(), {})

        model = str(folder / "sub" / "Alpha-Q4.gguf")
        self.assertEqual(
            result,
            {
                "id": "alpha-q8",
                "model": model,
                "folder": str(folder),
                "preset_name": "Alpha Q4 · 32K",
                "settings": {**item["settings"], "model": model},
            },
        )
        self.assertNotIn("model", item["settings"])
        self.assertEqual(
            [path for path, _, _ in catalog.library_rows(str(library))], [model]
        )

        again = FakeSession(by_url=served)
        catalog.download_entry("alpha-q8", library, None, None, again)
        self.assertEqual(again.calls, [])

        other = self.tmp / "other"
        noisy = Mock(side_effect=RuntimeError("renderer went away"))
        catalog.download_entry("alpha-q8", other, noisy, None, FakeSession(by_url=served))
        self.assertTrue(noisy.called)
        self.assertEqual(
            (other / "org--Alpha-GGUF" / "sub" / "Alpha-Q4.gguf").read_bytes(), self.data
        )

    def test_drafter_downloads_beside_the_inkling_weights(self):
        body = b"dspark drafter"
        drafter = dict(
            repo="unsloth/Inkling-2-Mini-GGUF",
            revision=REV_OTHER,
            path=runtime.DSPARK,
            name=runtime.DSPARK,
            bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
            role="draft",
        )
        item = dict(id="metal-32-inkling-q4", drafter=[drafter])
        self.patched(catalog, "entries", return_value=[dict(id="other"), item])
        repo = self.tmp / "models" / "unsloth--Inkling-2-Mini-GGUF"
        model = repo / "Mainline_Support" / "Inkling-2-Mini-UD-Q4_K_XL.gguf_file"
        url = f"{catalog.HUB}/unsloth/Inkling-2-Mini-GGUF/resolve/{REV_OTHER}/{runtime.DSPARK}"
        events = []
        path = catalog.download_drafter(
            model, events.append, None, FakeSession(by_url={url: body})
        )
        self.assertEqual(path, str(repo / runtime.DSPARK))
        self.assertEqual((repo / runtime.DSPARK).read_bytes(), body)
        self.assertEqual(runtime.drafter(model), repo / runtime.DSPARK)
        self.assertEqual(events[-1]["message"], "DSpark drafter is ready")
        self.assertEqual(events[-1]["id"], "dspark-drafter")
        again = FakeSession(by_url={url: body})
        catalog.download_drafter(model, None, None, again)
        self.assertEqual(again.calls, [])
        (repo / runtime.DSPARK).unlink()
        with self.assertRaisesRegex(ValueError, "failed its checksum"):
            catalog.download_drafter(model, None, None, FakeSession(by_url={url: b"x" * len(body)}))
        self.assertFalse((repo / runtime.DSPARK).exists())
        with self.assertRaisesRegex(ValueError, "Inkling 2 Mini GGUF"):
            catalog.download_drafter(self.tmp / "Qwen3.5-4B-Q4_K_M.gguf")

    def test_download_refuses_second_job_and_cancels_by_id(self):
        item, served = self.item()
        gate, errors = threading.Event(), []
        session = FakeSession(by_url=served, gate=gate)
        folder = self.tmp / "models" / "org--Alpha-GGUF"

        def run():
            try:
                catalog._transfer(
                    "alpha-q4", "alpha-q8", "Alpha Q4", folder, item["files"], session=session
                )
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        self.addCleanup(gate.set)
        self.assertTrue(session.waiting.wait(5))
        with self.assertRaises(ValueError) as caught:
            catalog._transfer(
                "alpha-q4", "other-pick", "Alpha Q4", folder, item["files"],
                session=FakeSession(by_url=served),
            )
        self.assertEqual(
            str(caught.exception),
            "This model is already downloading. Wait for it to finish or cancel it "
            "first.",
        )
        self.assertEqual(list(catalog.jobs()), ["alpha-q4"])
        self.assertFalse(catalog.cancel_download("nope"))
        self.assertTrue(catalog.cancel_download("alpha-q8"))
        gate.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], Cancelled)
        self.assertEqual(str(errors[0]), catalog.CANCELLED)
        self.assertEqual(catalog.jobs(), {})
        self.assertFalse(catalog.cancel_download("alpha-q8"))
        with catalog._job("job-key", {"id": "pick-id"}) as stop:
            self.assertTrue(catalog.cancel_download("job-key"))
            self.assertTrue(stop.is_set())

    def test_download_honours_caller_token(self):
        item, served = self.item()
        self.patched(catalog, "entry", return_value=item)
        stop = threading.Event()
        stop.set()
        session = FakeSession(by_url=served)
        with self.assertRaises(Cancelled):
            catalog.download_entry("alpha-q8", self.tmp / "models", None, stop, session)
        self.assertEqual(session.calls, [])
        self.assertEqual(catalog.jobs(), {})

    def test_download_checks_disk_space(self):
        item, served = self.item()
        self.patched(catalog, "entry", return_value=item)
        usage = types.SimpleNamespace(free=10)
        self.patched(catalog.shutil, "disk_usage", return_value=usage)
        session = FakeSession(by_url=served)
        with self.assertRaises(ValueError) as caught:
            catalog.download_entry("alpha-q8", self.tmp / "models", session=session)
        self.assertTrue(str(caught.exception).startswith("Free "))
        self.assertTrue(str(caught.exception).endswith("start the download again."))
        self.assertEqual(session.calls, [])
        self.assertEqual(catalog.jobs(), {})

    def test_transfer_reports_an_unwritable_folder(self):
        blocker = self.tmp / "models"
        blocker.write_text("a file where the folder should be", encoding="utf8")
        item, served = self.item()
        session = FakeSession(by_url=served)
        with self.assertRaises(ValueError) as caught:
            catalog._transfer("k", "k", "L", blocker / "sub", item["files"], session=session)
        self.assertTrue(
            str(caught.exception).startswith("Workbench cannot write to the model folder ")
        )
        self.assertEqual(session.calls, [])

    def test_transfer_reports_file_system_errors(self):
        item, served = self.item()
        folder = self.tmp / "models" / "org--Alpha-GGUF"
        folder.mkdir(parents=True)
        (folder / "sub").write_text("a file where a folder should be", encoding="utf8")
        session = FakeSession(by_url=served)
        with self.assertRaises(ValueError) as caught:
            catalog._transfer("k", "k", "L", folder, item["files"], session=session)
        self.assertEqual(
            str(caught.exception),
            f"Workbench cannot write to the model folder {folder}. Choose another "
            "folder in Advanced → Model library.",
        )
        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertEqual(session.calls, [])
        self.assertEqual(catalog.jobs(), {})

        (folder / "sub").unlink()
        full = OSError(errno.ENOSPC, "No space left on device")
        with patch.object(catalog.os, "replace", side_effect=full):
            with self.assertRaises(ValueError) as caught:
                catalog._transfer("k", "k", "L", folder, item["files"], session=session)
        self.assertEqual(
            str(caught.exception),
            "The drive that holds your model folder is full. Free some space, then "
            "start the download again to resume.",
        )
        part = folder / "sub" / "Alpha-Q4.gguf.part"
        self.assertEqual(part.read_bytes(), self.data)
        self.assertEqual((catalog.jobs(), catalog.WRITING), ({}, set()))
        again = FakeSession(by_url=served)
        catalog._transfer("k", "k", "L", folder, item["files"], session=again)
        self.assertEqual([url for url, _ in again.calls], [list(served)[1]])
        self.assertEqual((folder / "sub" / "Alpha-Q4.gguf").read_bytes(), self.data)

        bundle = "Could not find a suitable TLS CA certificate bundle."
        broken = Mock(get=Mock(side_effect=OSError(bundle)))
        with self.assertRaises(OSError) as caught:
            catalog._transfer(
                "k", "k", "L", self.tmp / "other", item["files"], session=broken
            )
        self.assertEqual(str(caught.exception), bundle)

    def test_jobs_sharing_a_file_take_turns(self):
        item, served = self.item()
        files = item["files"][:1]
        folder = self.tmp / "models" / "org--Alpha-GGUF"
        reached, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        class Stalled(FakeResponse):
            def iter_content(self, size):
                blocks = super().iter_content(size)
                yield next(blocks)
                reached.set()
                release.wait(5)
                yield from blocks

        class StalledSession(FakeSession):
            def get(self, url, headers=None, stream=False, timeout=None):
                self.calls.append((url, dict(headers or {})))
                return Stalled(200, {}, self.by_url[url])

        def start(key, ident, session):
            errors = []

            def run():
                try:
                    catalog._transfer(key, ident, "Alpha", folder, files, session=session)
                except BaseException as error:
                    errors.append(error)

            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            return worker, errors

        def held(worker, key):
            limit = time.monotonic() + 5
            while key not in catalog.jobs() and time.monotonic() < limit:
                time.sleep(0.01)
            worker.join(0.15)
            return worker.is_alive()

        first, first_errors = start("model-a", "pick-a", StalledSession(by_url=served))
        self.assertTrue(reached.wait(5))
        idle = FakeSession(by_url=served)
        second, second_errors = start("model-b", "pick-b", idle)
        self.assertTrue(held(second, "model-b"))
        self.assertEqual(sorted(catalog.jobs()), ["model-a", "model-b"])
        self.assertTrue(catalog.cancel_download("pick-b"))
        second.join(5)
        self.assertEqual([type(error) for error in second_errors], [Cancelled])

        third, third_errors = start("model-c", "pick-c", idle)
        self.assertTrue(held(third, "model-c"))
        release.set()
        first.join(5)
        third.join(5)
        self.assertFalse(first.is_alive() or third.is_alive())
        self.assertEqual((first_errors, third_errors), ([], []))
        self.assertEqual(idle.calls, [])
        self.assertEqual((folder / "sub" / "Alpha-Q4.gguf").read_bytes(), self.data)
        self.assertEqual(list(folder.rglob("*.part")), [])
        self.assertEqual((catalog.jobs(), catalog.WRITING), ({}, set()))

    def test_transfer_refuses_escaping_path(self):
        folder = self.tmp / "models" / "org--Alpha-GGUF"
        session = FakeSession(self.data)
        for name in ("../x.gguf", "sub/../../x.gguf", str(self.tmp / "x.gguf"), "."):
            files = [
                dict(
                    repo="org/Alpha-GGUF",
                    revision=REV,
                    path="x.gguf",
                    name=name,
                    bytes=len(self.data),
                    sha256=self.sha,
                )
            ]
            with self.assertRaises(ValueError) as caught:
                catalog._transfer("k", "k", "L", folder, files, session=session)
            self.assertEqual(
                str(caught.exception),
                "Hugging Face listed a file path that Workbench will not write. "
                "Choose another file.",
            )
        self.assertEqual(session.calls, [])
        self.assertEqual(list(self.tmp.rglob("*.gguf*")), [])
        self.assertEqual(catalog.jobs(), {})


class HubTest(DownloadCase):
    def hub(self, siblings, token=None):
        calls = []

        class Api:
            def model_info(self, repo, revision=None, files_metadata=False):
                calls.append((repo, revision, files_metadata))
                return types.SimpleNamespace(sha=REV, siblings=siblings)

            def list_models(self, **query):
                calls.append(query)
                return [types.SimpleNamespace(id="org/Found-GGUF")]

        def get_token():
            if isinstance(token, Exception):
                raise token
            return token

        module = types.SimpleNamespace(HfApi=Api, get_token=get_token)
        patcher = patch.dict(sys.modules, {"huggingface_hub": module})
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def sibling(self, name, data, sha256=None, lfs=True):
        digest = sha256 or hashlib.sha256(data).hexdigest()
        return types.SimpleNamespace(
            rfilename=name, size=len(data), lfs={"sha256": digest} if lfs else None
        )

    def serve(self, repo, files):
        session = FakeSession(
            by_url={
                f"{catalog.HUB}/{repo}/resolve/{REV}/{name}": data
                for name, data in files.items()
            }
        )
        self.patched(catalog.requests, "Session", return_value=session)
        return session

    def test_hub_download_uses_lfs_checksums(self):
        name, repo = "Model-Q4.gguf", "org/Repo-GGUF"
        calls = self.hub([self.sibling(name, self.data)], token="hf_test_token")
        session = self.serve(repo, {name: self.data})
        library = self.tmp / "models"
        row = (repo, REV, name, [name], len(self.data))
        result = catalog.download(row, str(library))
        self.assertEqual(result, library / "org--Repo-GGUF" / name)
        self.assertEqual(result.read_bytes(), self.data)
        self.assertEqual(calls, [(repo, REV, True)])
        self.assertEqual(
            session.calls,
            [
                (
                    f"{catalog.HUB}/{repo}/resolve/{REV}/{name}",
                    {"Authorization": "Bearer hf_test_token"},
                )
            ],
        )
        self.assertEqual(catalog.jobs(), {})

        self.hub([self.sibling(name, self.data, sha256="0" * 64)])
        with self.assertRaises(ValueError) as caught:
            catalog.download(row, str(self.tmp / "second"))
        self.assertIn("failed its checksum", str(caught.exception))
        self.assertEqual(list((self.tmp / "second").rglob("*.gguf*")), [])

    def test_hub_download_adds_inkling_companions(self):
        repo, pack = "org/Inkling-Strata", "Inkling-2-Mini-SQ10.strata"
        files = {
            pack: self.data,
            "chat_template.jinja": b"{{ messages }}",
            "tokenizer.json": b"{}",
            "Inkling-2-Mini-Vision.strata": b"vision",
        }
        listed = [
            self.sibling(name, data, lfs=name.endswith(".strata"))
            for name, data in files.items()
        ]
        self.hub(listed)
        session = self.serve(repo, files)
        row = (repo, REV, pack, [pack], len(self.data))
        folder = self.tmp / "models" / "org--Inkling-Strata"
        self.assertEqual(catalog.download(row, self.tmp / "models"), folder / pack)
        self.assertEqual(len(session.calls), 3)
        self.assertFalse((folder / "Inkling-2-Mini-Vision.strata").exists())
        catalog.download(row, self.tmp / "models", vision=True)
        self.assertEqual(len(session.calls), 4)
        self.assertEqual((folder / "Inkling-2-Mini-Vision.strata").read_bytes(), b"vision")
        self.assertEqual((folder / "tokenizer.json").read_bytes(), b"{}")

        self.hub([entry for entry in listed if entry.rfilename != "tokenizer.json"])
        with self.assertRaises(ValueError) as caught:
            catalog.download(row, self.tmp / "third")
        self.assertEqual(
            str(caught.exception),
            "tokenizer.json is missing from org/Inkling-Strata at this revision. "
            "Search again and choose another file.",
        )
        self.assertFalse((self.tmp / "third").exists())

    def test_hub_download_is_a_cancellable_job(self):
        name, repo = "Model-Q4.gguf", "org/Repo-GGUF"
        self.hub([self.sibling(name, self.data)])
        self.serve(repo, {name: self.data})
        row = (repo, REV, name, [name], len(self.data))
        events = []

        def progress(state):
            events.append(state)
            catalog.cancel_download(f"hub:{repo}:{name}")

        with self.assertRaises(Cancelled):
            catalog.download(row, self.tmp / "models", progress=progress)
        self.assertEqual(events[0]["key"], f"hub:{repo}:{name}")
        self.assertEqual(events[0]["label"], name)

    def test_hub_files_lists_models_named_like_adapters(self):
        calls = self.hub(
            [
                self.sibling("Exploration-7B.gguf", b"12345"),
                self.sibling("pi-lora.gguf", b"1"),
                self.sibling("README.md", b"1", lfs=False),
            ]
        )
        self.assertEqual(
            catalog.hub_files("org/Repo-GGUF"),
            [("org/Repo-GGUF", REV, "Exploration-7B.gguf", ["Exploration-7B.gguf"], 5)],
        )
        self.assertEqual(catalog.hub_files("qwen"), ["org/Found-GGUF"])
        self.assertEqual(calls[-1]["filter"], "gguf")

    def test_hub_headers_carry_a_saved_token_only(self):
        self.hub([], token="hf_test_token")
        self.assertEqual(
            catalog._hub_headers(), {"Authorization": "Bearer hf_test_token"}
        )
        self.hub([], token=None)
        self.assertEqual(catalog._hub_headers(), {})
        self.hub([], token=OSError("token file unreadable"))
        self.assertEqual(catalog._hub_headers(), {})


class DispatchTest(TempCase):
    def test_dispatch_actions(self):
        loaded = catalog.parse_catalog(fixture(), evidence_root(self.tmp), accept_all)
        library = str(self.tmp / "models")
        geforce = computer([gpu("nvidia", "NVIDIA GeForce RTX 4090", 23.99, 20.0)])
        read = self.patched(catalog, "hardware_info", return_value=geforce)
        self.patched(catalog, "_engine_build", return_value="cuda")
        engines = self.patched(catalog, "_engines", return_value=None)
        self.patched(catalog, "load_catalog", return_value=loaded)
        self.patched(runtime, "settings", return_value={"library": library})
        download = self.patched(catalog, "download_entry", return_value={"id": "x"})

        self.assertEqual(catalog.dispatch("model-hardware", {"refresh": True}), geforce)
        read.assert_called_once_with(True)
        self.assertEqual(catalog.dispatch("model-hardware", None), geforce)
        read.assert_called_with(False)

        advice = catalog.dispatch("model-recommend", {})
        for key in ("pick", "alternatives", "reason", "warnings", "hardware_label", "build"):
            self.assertIn(key, advice)
        self.assertEqual(advice["hardware_label"], geforce["label"])
        self.assertEqual(advice["build"], "cuda")
        self.assertIn("installed", advice["pick"])

        view = catalog.dispatch("model-catalog", {})
        self.assertEqual(
            {key: view[key] for key in view if key != "entries"},
            {
                "schema": 1,
                "updated": "2026-11-22",
                "engine_build": "b12633",
                "hardware_label": geforce["label"],
                "backend": "cuda",
                "build": "cuda",
            },
        )
        self.assertEqual(len(view["entries"]), 6)
        order = [
            (-entry["min_memory_gib"], entry["rank"], entry["id"])
            for entry in view["entries"]
        ]
        self.assertEqual(order, sorted(order))
        self.assertEqual(view["entries"][0]["id"], "beta-fp8")
        for entry in view["entries"]:
            self.assertIn("cuda", entry["backends"])
            self.assertIn(entry["sizing"]["fit"], ("comfortable", "listed", "no"))
            self.assertEqual(entry["installed"]["installed"], False)

        engines.return_value = ["Auto", "llama.cpp", "Custom"]
        read.return_value = computer([gpu("nvidia", "NVIDIA GPU", None, None, "sysfs")])
        unread = catalog.dispatch("model-catalog", {})
        self.assertEqual(unread["backend"], "cuda")
        self.assertEqual(len(unread["entries"]), 5)
        for entry in unread["entries"]:
            self.assertIn("cpu", entry["backends"])
            self.assertEqual(entry["engine"], "llama.cpp")
            self.assertEqual(entry["sizing"]["kind"], "ram")

        self.assertEqual(catalog.dispatch("model-download-status", {}), {})
        self.assertIs(catalog.dispatch("model-download-cancel", {"id": "x"}), False)
        self.assertIs(catalog.dispatch("model-download-cancel", {}), False)

        report, token = Mock(), threading.Event()
        payload = {"id": "alpha-q8", "library": "/somewhere/else"}
        result = catalog.dispatch("model-download", payload, progress=report, cancel=token)
        self.assertEqual(result, {"id": "x"})
        download.assert_called_once_with("alpha-q8", library, report, token)
        drafter = self.patched(catalog, "download_drafter", return_value="/d.gguf")
        payload = {"drafter": "/m/Inkling-2-Mini-UD-Q4_K_XL.gguf_file"}
        result = catalog.dispatch("model-download", payload, progress=report, cancel=token)
        self.assertEqual(result, "/d.gguf")
        drafter.assert_called_once_with(payload["drafter"], report, token)
        download.assert_called_once()

        with self.assertRaises(ValueError) as caught:
            catalog.dispatch("model-unknown", {})
        self.assertEqual(str(caught.exception), "Unknown model action.")


if __name__ == "__main__":
    unittest.main()
