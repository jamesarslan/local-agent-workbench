"""Engine setup: GPU detection, pinned downloads, placing files and updates."""

import atexit
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch
import zipfile

from filelock import FileLock

if "runtime" not in sys.modules:
    _data = tempfile.mkdtemp(prefix="workbench-tests-")
    atexit.register(shutil.rmtree, _data, ignore_errors=True)
    os.environ["WORKBENCH_DATA"] = _data

import requests

import runtime
import setup_engines
from setup_engines import LLAMA_REPO, LLAMA_TAG, LLAMA_TARGET, STRATA_REPO, Asset

RELEASE = {
    "llama-b12633-bin-win-cuda-13.5-x64.zip": (
        "5fadbb7d1f4b7e612f77c7b626c60445d5d1fc9d990425ee7f37d5f17a8c68ad",
        225649717,
    ),
    "cudart-llama-bin-win-cuda-13.5-x64.zip": (
        "c2bf4cc202bfbaaa7a0b75f5082457c3811326947bb216059b72839d844e9cba",
        426314199,
    ),
    "llama-b12633-bin-win-vulkan-x64.zip": (
        "720d59998a51423a1c50c99326a5d5358b80e89d75f8fdafc50a26479d1bad68",
        38877434,
    ),
    "llama-b12633-bin-win-cpu-x64.zip": (
        "bad9fe0c1b554bcb1ce5630d66ad34a2285a8b390400513cc02f56feafd62c8e",
        25038006,
    ),
    "llama-b12633-bin-ubuntu-cuda-13.5-x64.tar.gz": (
        "d1078e7581e9a681c2b2cce0ba8ca841ef554b242627425a1d7ee0a0bbfb5267",
        224449189,
    ),
    "cudart-llama-b12633-bin-ubuntu-cuda-13.5-x64.tar.gz": (
        "96bc888cb8ad7b19e3c9f67050258af16860b47e74124e274128337c110cba6e",
        444078909,
    ),
    "llama-b12633-bin-ubuntu-vulkan-x64.tar.gz": (
        "33223bb1f421eb4120210fc61648acf1f7ba8deece1183c63af417c80a9ff013",
        36909894,
    ),
    "llama-b12633-bin-ubuntu-x64.tar.gz": (
        "9c4a2c1bd1437f22831e0b56cc75329b321bac87225ffb5c83cd8d13b1f44fa2",
        23225608,
    ),
    "llama-b12633-bin-macos-arm64.tar.gz": (
        "99657876053df9004753d92075813bbb3b484e79f8a402809b51d00c8c540062",
        16600069,
    ),
    "strata-inkling-0.1.65-windows-x86_64-cuda-cuda13.tar.gz": (
        "484e7d39d8767b40ac3a946432effaf3874e8ff04660c873a0fe97d6d758a5df",
        411426893,
    ),
    "strata-inkling-0.1.65-linux-x86_64-cuda-cuda13.tar.gz": (
        "b5131c902915851098dce383c88b1c4a7418aa2a5047892882e86f6a369dbf86",
        432654826,
    ),
}
LAYOUT = {
    ("windows", "cuda"): [
        "llama-b12633-bin-win-cuda-13.5-x64.zip",
        "cudart-llama-bin-win-cuda-13.5-x64.zip",
    ],
    ("windows", "vulkan"): ["llama-b12633-bin-win-vulkan-x64.zip"],
    ("windows", "cpu"): ["llama-b12633-bin-win-cpu-x64.zip"],
    ("linux", "cuda"): [
        "llama-b12633-bin-ubuntu-cuda-13.5-x64.tar.gz",
        "cudart-llama-b12633-bin-ubuntu-cuda-13.5-x64.tar.gz",
    ],
    ("linux", "vulkan"): ["llama-b12633-bin-ubuntu-vulkan-x64.tar.gz"],
    ("linux", "cpu"): ["llama-b12633-bin-ubuntu-x64.tar.gz"],
    ("macos", "metal"): ["llama-b12633-bin-macos-arm64.tar.gz"],
}
PINNED_COMMIT = "c6f6427d9de9016fd92d13e29740f65625261df7"


def tarball(files):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as bundle:
        for name, body in files.items():
            item = tarfile.TarInfo(name)
            item.size, item.mode = len(body), 0o755
            bundle.addfile(item, io.BytesIO(body))
    return out.getvalue()


def zipped(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as bundle:
        for name, body in files.items():
            bundle.writestr(name, body)
    return out.getvalue()


def asset_for(name, body, repo=LLAMA_REPO, tag=LLAMA_TAG, target=LLAMA_TARGET):
    return Asset(repo, tag, name, target, hashlib.sha256(body).hexdigest(), len(body))


def halves(body):
    return [body[: len(body) // 2], body[len(body) // 2 :]]


class Reply:
    def __init__(self, chunks=(), status=200, fail=None, data=None, between=None):
        self.chunks, self.status_code, self.fail = list(chunks), status, fail
        self.data, self.between = data, between

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def iter_content(self, size):
        for index, chunk in enumerate(self.chunks):
            if index and self.between:
                self.between()
            yield chunk
        if self.fail:
            raise self.fail

    def json(self):
        return self.data


class Web:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, url, **options):
        self.calls.append((url, options))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.data = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.object(setup_engines, "DATA", self.data))
        self.enterContext(patch.object(runtime, "DATA", self.data))
        self.enterContext(
            patch.object(setup_engines, "owned", return_value=(None, {}))
        )
        self.pause = self.enterContext(patch.object(setup_engines, "pause"))
        self.get = self.enterContext(
            patch("requests.get", side_effect=AssertionError("network used"))
        )
        setup_engines.detect.cache_clear()
        self.addCleanup(setup_engines.detect.cache_clear)

    def computer(
        self, name="linux", arch="x86_64", gpu="", vendors=(), vulkan=False, rows=()
    ):
        for target, value in (
            ("system", name),
            ("nvidia_gpu", gpu),
            ("linux_adapters", set(vendors)),
            ("windows_adapters", list(rows)),
            ("vulkan_loader", vulkan),
        ):
            self.enterContext(patch.object(setup_engines, target, return_value=value))
        self.enterContext(patch("platform.machine", return_value=arch))
        setup_engines.detect.cache_clear()

    def serve(self, *replies):
        web = Web(*replies)
        self.get.side_effect = web
        return web

    def pinned(self, key, *assets):
        self.enterContext(patch.dict(setup_engines.PINNED, {key: list(assets)}))

    def linux_cpu(self):
        body = tarball({"llama-test/llama-server": b"#!/bin/sh\n"})
        asset = asset_for("llama-test-bin-ubuntu-x64.tar.gz", body)
        self.computer("linux")
        self.pinned(("linux", "cpu"), asset)
        return asset, body

    def other_build(self):
        folder = self.data / "runtime" / LLAMA_TARGET
        (folder / "old").mkdir(parents=True)
        (folder / "old" / "llama-server").write_text("old engine")
        (folder / "llama-old.tar.gz.installed").write_text("sha256:" + "0" * 64)
        return folder

    def scratch_dirs(self):
        return [v.name for v in self.data.glob("engine-*")]


class PinTest(EngineCase):
    def test_pinned_assets_match_the_recorded_release(self):
        self.assertEqual(
            {key: [a.name for a in rows] for key, rows in setup_engines.PINNED.items()},
            LAYOUT,
        )
        self.assertEqual(set(setup_engines.STRATA_ASSETS), {"windows", "linux"})
        llama = [a for rows in setup_engines.PINNED.values() for a in rows]
        strata = list(setup_engines.STRATA_ASSETS.values())
        self.assertEqual(len(llama), 9)
        self.assertEqual(len(strata), 2)
        for asset in llama + strata:
            self.assertRegex(asset.sha256, r"\A[0-9a-f]{64}\Z")
            self.assertGreater(asset.size, 0)
            self.assertEqual((asset.sha256, asset.size), RELEASE[asset.name])
        for asset in llama:
            self.assertEqual(
                (asset.repo, asset.tag, asset.target),
                ("ggml-org/llama.cpp", "b12633", "llama-native-b12633"),
            )
        for name, asset in setup_engines.STRATA_ASSETS.items():
            self.assertIn(name + "-x86_64", asset.name)
            self.assertEqual(
                (asset.repo, asset.tag, asset.target),
                ("Niko1221/Strata", "v0.2.19", "strata-inkling-0.1.65"),
            )
        self.assertEqual(setup_engines.STRATA_COMMIT, PINNED_COMMIT)

    def test_wanted_assets_per_system(self):
        self.assertEqual(
            [a.name for a in setup_engines.wanted("cuda", "windows", True)],
            [
                "llama-b12633-bin-win-cuda-13.5-x64.zip",
                "cudart-llama-bin-win-cuda-13.5-x64.zip",
                "strata-inkling-0.1.65-windows-x86_64-cuda-cuda13.tar.gz",
            ],
        )
        self.assertEqual(
            [a.name for a in setup_engines.wanted("vulkan", "linux", False)],
            ["llama-b12633-bin-ubuntu-vulkan-x64.tar.gz"],
        )
        self.assertEqual(
            [a.name for a in setup_engines.wanted("metal", "macos", False)],
            ["llama-b12633-bin-macos-arm64.tar.gz"],
        )
        setup_engines.wanted("cpu", "linux", False).clear()
        self.assertEqual(len(setup_engines.PINNED[("linux", "cpu")]), 1)


class DetectTest(EngineCase):
    def test_backend_choice_follows_gpu_vendor(self):
        choose = setup_engines.choose_backend
        for nvidia, vendors, vulkan, backend, vendor, words in [
            (True, {"nvidia"}, True, "cuda", "nvidia", "NVIDIA GPU detected"),
            (True, {"nvidia", "intel"}, False, "cuda", "nvidia", "CUDA build"),
            (False, {"amd", "intel"}, True, "vulkan", "amd", "AMD GPU detected"),
            (False, {"nvidia", "intel"}, True, "vulkan", "nvidia", "did not respond"),
            (False, {"intel"}, True, "vulkan", "intel", "Intel GPU detected"),
            (False, {"amd"}, False, "cpu", "amd", "no Vulkan loader"),
            (False, {"nvidia", "intel"}, False, "cpu", "nvidia", "no Vulkan loader"),
            (False, {"intel"}, False, "cpu", "intel", "no Vulkan loader"),
            (False, set(), True, "cpu", "", "No supported GPU"),
            (False, set(), False, "cpu", "", "No supported GPU"),
        ]:
            for name in ("windows", "linux"):
                got = choose(name, nvidia, vendors, vulkan)
                self.assertEqual(got[:2], (backend, vendor), (nvidia, vendors, vulkan))
                self.assertIn(words, got[2])
                self.assertTrue(got[2].endswith("."))

    def test_linux_adapter_scan_reads_vendor_ids(self):
        root = self.data / "drm"
        for card, code in (("card0", "0x1002\n"), ("card1", "0x8086\n")):
            (root / card / "device").mkdir(parents=True)
            (root / card / "device" / "vendor").write_text(code)
        (root / "card2" / "device").mkdir(parents=True)
        (root / "card2" / "device" / "vendor").mkdir()
        self.assertEqual(setup_engines.linux_adapters(root), {"amd", "intel"})
        other = self.data / "other"
        (other / "card0" / "device").mkdir(parents=True)
        (other / "card0" / "device" / "vendor").write_text("0x1af4\n")
        self.assertEqual(setup_engines.linux_adapters(other), set())
        self.assertEqual(setup_engines.linux_adapters(self.data / "missing"), set())

    def test_windows_device_ids_map_to_vendors(self):
        rows = [
            ("PCI\\VEN_10DE&DEV_2684", "x"),
            ("pci\\ven_8086&dev_a780", "y"),
            ("ROOT\\BasicDisplay", "z"),
        ]
        self.assertEqual(setup_engines.vendors_from(rows), {"nvidia", "intel"})
        self.assertEqual(setup_engines.vendors_from([]), set())

    def test_windows_registry_scan_lists_display_adapters(self):
        tree = {
            "0000": {"MatchingDeviceId": "PCI\\VEN_1002&DEV_744C", "DriverDesc": "RX"},
            "0001": {"MatchingDeviceId": "pci\\ven_8086&dev_a780"},
            "0002": {"DriverDesc": "No device id"},
            "0003": None,
            "Properties": {"MatchingDeviceId": "PCI\\VEN_10DE&DEV_2684"},
        }
        opened = []

        class Key:
            def __init__(self, values):
                self.values = values

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def open_key(parent, name):
            opened.append(name)
            if parent == "HKLM":
                return Key(None)
            if tree[name] is None:
                raise PermissionError(name)
            return Key(tree[name])

        def enum_key(key, index):
            if index >= len(tree):
                raise OSError("no more data")
            return list(tree)[index]

        def query(key, value):
            if value not in key.values:
                raise FileNotFoundError(value)
            return key.values[value], 1

        fake = types.ModuleType("winreg")
        fake.HKEY_LOCAL_MACHINE = "HKLM"
        fake.OpenKey, fake.EnumKey, fake.QueryValueEx = open_key, enum_key, query
        with patch.dict(sys.modules, {"winreg": fake}), patch(
            "subprocess.run"
        ) as run, patch("subprocess.Popen") as spawn:
            rows = setup_engines.windows_adapters()
            self.assertEqual(
                rows,
                [("PCI\\VEN_1002&DEV_744C", "RX"), ("pci\\ven_8086&dev_a780", "")],
            )
            self.assertIn("{4d36e968-e325-11ce-bfc1-08002be10318}", opened[0])
            self.assertNotIn("Properties", opened)
            fake.OpenKey = Mock(side_effect=OSError("denied"))
            self.assertEqual(setup_engines.windows_adapters(), [])
            run.assert_not_called()
            spawn.assert_not_called()
        self.assertEqual(setup_engines.vendors_from(rows), {"amd", "intel"})
        with patch.dict(sys.modules, {"winreg": None}):
            self.assertEqual(setup_engines.windows_adapters(), [])

    def test_registry_scan_stops_after_64_keys(self):
        fake = types.ModuleType("winreg")
        fake.HKEY_LOCAL_MACHINE = "HKLM"
        fake.OpenKey = Mock(return_value=contextlib.nullcontext())
        fake.EnumKey = Mock(return_value="Configuration")
        with patch.dict(sys.modules, {"winreg": fake}):
            self.assertEqual(setup_engines.windows_adapters(), [])
        self.assertEqual(fake.EnumKey.call_count, 64)

    def test_nvidia_probe_needs_a_working_driver(self):
        answer = Mock(returncode=0, stdout="RTX 5090\nRTX 4090\n")
        with patch("shutil.which", return_value="/usr/bin/nvidia-smi"), patch(
            "subprocess.run", return_value=answer
        ) as run:
            self.assertEqual(setup_engines.nvidia_gpu(), "RTX 5090")
            argv, options = run.call_args.args[0], run.call_args.kwargs
            self.assertEqual(
                argv,
                ["/usr/bin/nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            )
            self.assertEqual(options["stdin"], subprocess.DEVNULL)
            self.assertEqual(options["timeout"], 5)
            self.assertNotIn("shell", options)
            run.return_value = Mock(returncode=9, stdout="RTX 5090\n")
            self.assertEqual(setup_engines.nvidia_gpu(), "")
            run.return_value = Mock(returncode=0, stdout="\n")
            self.assertEqual(setup_engines.nvidia_gpu(), "")
            run.side_effect = OSError("gone")
            self.assertEqual(setup_engines.nvidia_gpu(), "")
            run.side_effect = subprocess.TimeoutExpired("nvidia-smi", 5)
            self.assertEqual(setup_engines.nvidia_gpu(), "")
        wsl = self.data / "nvidia-smi"
        with patch("shutil.which", return_value=None), patch(
            "subprocess.run", return_value=answer
        ) as run:
            with patch.object(setup_engines, "WSL_NVIDIA_SMI", wsl):
                self.assertEqual(setup_engines.nvidia_gpu(), "")
                run.assert_not_called()
                wsl.write_text("")
                self.assertEqual(setup_engines.nvidia_gpu(), "RTX 5090")
                self.assertEqual(run.call_args.args[0][0], str(wsl))

    def test_vulkan_loader_is_looked_up_per_system(self):
        root = self.data / "Windows"
        (root / "System32").mkdir(parents=True)
        with patch.dict(os.environ, {"SystemRoot": str(root)}):
            self.assertFalse(setup_engines.vulkan_loader("windows"))
            (root / "System32" / "vulkan-1.dll").write_bytes(b"")
            self.assertTrue(setup_engines.vulkan_loader("windows"))
        self.assertFalse(setup_engines.vulkan_loader("macos"))
        library = self.data / "libvulkan.so.1"
        with patch.object(setup_engines, "VULKAN_PATHS", (str(library),)):
            with patch("ctypes.util.find_library", return_value=None) as find:
                self.assertFalse(setup_engines.vulkan_loader("linux"))
                find.return_value = "libvulkan.so.1"
                self.assertTrue(setup_engines.vulkan_loader("linux"))
                find.side_effect = RuntimeError("no linker")
                self.assertFalse(setup_engines.vulkan_loader("linux"))
                library.write_bytes(b"")
                self.assertTrue(setup_engines.vulkan_loader("linux"))
        with patch.object(setup_engines, "system", return_value="macos"):
            self.assertFalse(setup_engines.vulkan_loader())

    def test_detect_reports_unsupported_computers(self):
        self.computer("linux", arch="aarch64")
        found = setup_engines.detect()
        self.assertFalse(found["supported"])
        self.assertEqual((found["backend"], found["system"]), ("", "linux"))
        self.assertIn("attach a running endpoint", found["reason"])
        with self.assertRaises(ValueError) as caught:
            setup_engines.install()
        self.assertEqual(str(caught.exception), found["reason"])
        self.get.assert_not_called()
        self.assertEqual(
            setup_engines.plan(),
            found
            | dict(
                tag="b12633",
                assets=[],
                download_bytes=0,
                installed_backend="",
                engine_ready=False,
                strata=False,
                notes=[],
            ),
        )
        self.computer("windows", arch="ARM64")
        self.assertFalse(setup_engines.detect()["supported"])
        self.computer("macos", arch="x86_64")
        with patch("subprocess.run", return_value=Mock(stdout="1\n")) as run:
            found = setup_engines.detect()
            self.assertEqual(
                run.call_args.args[0], ["/usr/sbin/sysctl", "-n", "hw.optional.arm64"]
            )
        self.assertFalse(found["supported"])
        self.assertIn("runs under Rosetta", found["reason"])
        self.assertIn('"Launch Workbench.command"', found["reason"])
        for reply in (Mock(stdout="0\n"), OSError("no sysctl")):
            setup_engines.detect.cache_clear()
            with patch("subprocess.run", side_effect=[reply]):
                found = setup_engines.detect()
            self.assertFalse(found["supported"])
            self.assertIn("need an Apple Silicon Mac", found["reason"])

    def test_detect_follows_the_gpu_on_each_system(self):
        self.computer("macos", arch="arm64")
        self.assertEqual(
            setup_engines.detect(),
            dict(
                system="macos",
                arch="arm64",
                supported=True,
                backend="metal",
                vendor="apple",
                gpu="Apple Silicon",
                reason="Apple Silicon detected, so the Metal build is used.",
            ),
        )
        self.computer("linux", vendors={"amd"}, vulkan=True)
        found = setup_engines.detect()
        self.assertEqual(
            (found["backend"], found["vendor"], found["gpu"], found["arch"]),
            ("vulkan", "amd", "", "x86_64"),
        )
        rows = [
            ("PCI\\VEN_8086&DEV_A780", "Intel UHD Graphics 770"),
            ("PCI\\VEN_1002&DEV_744C", "AMD Radeon RX 7900 XTX"),
        ]
        self.computer("windows", arch="AMD64", rows=rows, vulkan=True)
        found = setup_engines.detect()
        self.assertEqual(
            (found["backend"], found["vendor"], found["gpu"], found["arch"]),
            ("vulkan", "amd", "AMD Radeon RX 7900 XTX", "x86_64"),
        )
        self.computer("windows", arch="AMD64", gpu="RTX 5090", rows=rows)
        found = setup_engines.detect()
        self.assertEqual(
            (found["backend"], found["vendor"], found["gpu"]),
            ("cuda", "nvidia", "RTX 5090"),
        )
        self.computer("linux")
        found = setup_engines.detect()
        self.assertEqual((found["backend"], found["vendor"]), ("cpu", ""))

    def test_explicit_backend_is_validated(self):
        self.computer("linux", vendors={"amd"}, vulkan=True)
        self.assertEqual(
            setup_engines.resolve_backend(),
            ("vulkan", "AMD GPU detected, so the Vulkan build is used."),
        )
        self.assertEqual(setup_engines.resolve_backend("auto")[0], "vulkan")
        self.assertEqual(setup_engines.resolve_backend("")[0], "vulkan")
        self.assertEqual(
            setup_engines.resolve_backend("vulkan"),
            ("vulkan", "The Vulkan build is used because you chose it."),
        )
        self.assertEqual(setup_engines.resolve_backend("cpu")[0], "cpu")
        for bad in ("metal", "CUDA", "cuda; rm", ["cuda"], 3):
            with self.assertRaises(ValueError) as caught:
                setup_engines.resolve_backend(bad)
            self.assertEqual(
                str(caught.exception),
                "Choose auto, cuda, vulkan or cpu for the engine build.",
            )
        self.computer("macos", arch="arm64")
        self.assertEqual(setup_engines.resolve_backend("metal")[0], "metal")
        with self.assertRaises(ValueError) as caught:
            setup_engines.resolve_backend("cuda")
        self.assertIn("Only the Metal build runs on this Mac", str(caught.exception))

    def test_strata_needs_cuda_and_git(self):
        choice = setup_engines.strata_choice
        with patch("shutil.which", return_value="/usr/bin/git"):
            self.assertEqual(choice("cuda", "linux", None), (True, []))
            self.assertEqual(choice("cuda", "windows", True), (True, []))
            self.assertEqual(choice("cuda", "linux", False), (False, []))
            self.assertEqual(choice("metal", "macos", True), (False, []))
            self.assertEqual(choice("vulkan", "linux", None), (False, []))
            wanted, notes = choice("cpu", "windows", True)
            self.assertFalse(wanted)
            self.assertIn("needs an NVIDIA GPU", notes[0])
        with patch("shutil.which", return_value=None):
            wanted, notes = choice("cuda", "linux", None)
            self.assertFalse(wanted)
            self.assertIn("Git is not installed", notes[0])


class FetchTest(EngineCase):
    def setUp(self):
        super().setUp()
        self.body = bytes(range(256)) * 40
        self.asset = asset_for("llama-test.tar.gz", self.body)

    def test_fetch_accepts_only_the_pinned_digest(self):
        web = self.serve(Reply(halves(self.body)))
        events = []
        path = setup_engines.fetch(self.asset, self.data, events.append, None, 2, 3)
        self.assertEqual(path, self.data / "llama-test.tar.gz")
        self.assertEqual(path.read_bytes(), self.body)
        url, options = web.calls[0]
        self.assertEqual(
            url,
            "https://github.com/ggml-org/llama.cpp/releases/download/b12633/"
            "llama-test.tar.gz",
        )
        self.assertTrue(options["stream"])
        self.assertEqual(options["timeout"], (15, 60))
        self.assertNotIn("Range", options["headers"])
        last = events[-1]
        self.assertEqual(last["done"], last["total"])
        self.assertEqual(
            (last["job"], last["phase"], last["asset"], last["index"], last["count"]),
            ("engine-install", "download", "llama-test.tar.gz", 2, 3),
        )
        self.assertEqual(last["message"], "Downloading llama-test.tar.gz (2 of 3)")
        json.dumps(events)
        for wrong in (
            self.asset._replace(sha256="0" * 64),
            self.asset._replace(size=len(self.body) + 1),
            self.asset._replace(size=len(self.body) - 1),
        ):
            self.serve(Reply(halves(self.body)))
            with self.assertRaises(ValueError) as caught:
                setup_engines.fetch(wrong, self.data)
            self.assertIn("pinned SHA-256", str(caught.exception))
            self.assertFalse(path.exists())

    def test_fetch_resumes_after_a_dropped_connection(self):
        first, rest = halves(self.body)
        web = self.serve(
            Reply([first], fail=requests.ConnectionError("dropped")),
            Reply([rest], status=206),
        )
        path = setup_engines.fetch(self.asset, self.data)
        self.assertEqual(path.read_bytes(), self.body)
        self.assertNotIn("Range", web.calls[0][1]["headers"])
        self.assertEqual(web.calls[1][1]["headers"]["Range"], f"bytes={len(first)}-")
        self.pause.assert_called_once_with(None, 1)

    def test_fetch_starts_again_when_the_range_is_ignored(self):
        first, _ = halves(self.body)
        web = self.serve(
            requests.ConnectionError("refused"),
            Reply([first], fail=requests.Timeout("stalled")),
            Reply(halves(self.body), status=200),
        )
        path = setup_engines.fetch(self.asset, self.data)
        self.assertEqual(path.read_bytes(), self.body)
        self.assertEqual(web.calls[1][1]["headers"]["Range"], "bytes=0-")
        self.assertEqual(web.calls[2][1]["headers"]["Range"], f"bytes={len(first)}-")

    def test_fetch_gives_up_with_a_sentence(self):
        web = self.serve(Reply(status=404))
        with self.assertRaises(RuntimeError) as caught:
            setup_engines.fetch(self.asset, self.data)
        self.assertEqual(
            str(caught.exception),
            "GitHub did not serve llama-test.tar.gz (HTTP 404); try engine setup "
            "again later.",
        )
        self.assertEqual(len(web.calls), 1)
        web = self.serve(*[requests.ConnectionError("offline") for _ in range(4)])
        with self.assertRaises(RuntimeError) as caught:
            setup_engines.fetch(self.asset, self.data)
        self.assertIn("Could not download llama-test.tar.gz", str(caught.exception))
        self.assertEqual(len(web.calls), 4)
        self.assertEqual(
            [call.args[1] for call in self.pause.call_args_list], [1, 2, 3]
        )

    def test_fetch_stops_on_cancel(self):
        token = threading.Event()
        token.set()
        with self.assertRaises(runtime.Cancelled) as caught:
            setup_engines.fetch(self.asset, self.data, None, token)
        self.assertEqual(str(caught.exception), "Engine setup was cancelled.")
        self.get.assert_not_called()
        token.clear()
        web = self.serve(Reply(halves(self.body), between=token.set))
        with self.assertRaises(runtime.Cancelled):
            setup_engines.fetch(self.asset, self.data, None, token)
        self.assertEqual(len(web.calls), 1)
        partial = self.data / "llama-test.tar.gz"
        self.assertLess(partial.stat().st_size, len(self.body))


class InstallTest(EngineCase):
    def test_install_unpacks_and_marks(self):
        asset, body = self.linux_cpu()
        web = self.serve(Reply(halves(body)))
        events = []
        result = setup_engines.install(progress=events.append)
        folder = self.data / "runtime" / LLAMA_TARGET
        self.assertEqual(
            (folder / "llama-test" / "llama-server").read_bytes(), b"#!/bin/sh\n"
        )
        self.assertEqual(
            (folder / (asset.name + ".installed")).read_text(), "sha256:" + asset.sha256
        )
        self.assertEqual(
            result,
            dict(
                backend="cpu",
                channel="Tested",
                installed=[asset.name],
                skipped=[],
                strata=False,
                notes=[],
            ),
        )
        self.assertEqual(
            [e["phase"] for e in events if e["phase"] != "download"],
            ["detect", "extract", "done"],
        )
        self.assertTrue(all(e["job"] == "engine-install" for e in events))
        self.assertEqual(events[0]["backend"], "cpu")
        self.assertEqual(
            events[0]["message"],
            "No supported GPU was detected, so the CPU build is used.",
        )
        self.assertEqual(
            events[-1],
            dict(
                job="engine-install",
                phase="done",
                message="The llama.cpp CPU build is ready.",
                **result,
            ),
        )
        json.dumps(events)
        self.assertEqual(self.scratch_dirs(), [])
        self.assertEqual(setup_engines.installed_backend(), "cpu")
        again = setup_engines.install()
        self.assertEqual((again["installed"], again["skipped"]), ([], [asset.name]))
        self.assertEqual(len(web.calls), 1)

    def test_install_unpacks_a_windows_zip(self):
        body = zipped({"llama-server.exe": b"MZ", "llama.dll": b"MZ"})
        asset = asset_for("llama-test-bin-win-vulkan-x64.zip", body)
        rows = [("PCI\\VEN_1002&DEV_744C", "AMD Radeon RX 7900 XTX")]
        self.computer("windows", arch="AMD64", rows=rows, vulkan=True)
        self.pinned(("windows", "vulkan"), asset)
        self.serve(Reply([body]))
        result = setup_engines.install()
        folder = self.data / "runtime" / LLAMA_TARGET
        self.assertEqual(result["backend"], "vulkan")
        self.assertTrue((folder / "llama-server.exe").is_file())
        self.assertEqual(setup_engines.installed_backend(), "vulkan")

    def test_install_replaces_another_build(self):
        asset, body = self.linux_cpu()
        folder = self.other_build()
        self.serve(Reply([body]))
        result = setup_engines.install()
        self.assertEqual(result["installed"], [asset.name])
        self.assertEqual(
            sorted(v.name for v in folder.iterdir()),
            ["llama-test", asset.name + ".installed"],
        )

    def test_install_repairs_a_tampered_or_unfinished_folder(self):
        asset, body = self.linux_cpu()
        folder = self.data / "runtime" / LLAMA_TARGET
        folder.mkdir(parents=True)
        self.assertFalse(setup_engines.needs_clear(folder, [asset]))
        (folder / "half-unpacked").write_text("partial")
        self.assertTrue(setup_engines.needs_clear(folder, [asset]))
        self.serve(Reply([body]))
        setup_engines.install()
        self.assertFalse((folder / "half-unpacked").exists())
        self.assertFalse(setup_engines.needs_clear(folder, [asset]))
        (folder / (asset.name + ".installed")).write_text("sha256:" + "f" * 64)
        (folder / "planted").write_text("x")
        self.assertTrue(setup_engines.needs_clear(folder, [asset]))
        web = self.serve(Reply([body]))
        result = setup_engines.install()
        self.assertEqual(result["installed"], [asset.name])
        self.assertEqual(len(web.calls), 1)
        self.assertFalse((folder / "planted").exists())
        self.assertTrue(setup_engines.complete(folder, [asset]))

    def test_install_restores_a_deleted_engine_program(self):
        asset, body = self.linux_cpu()
        web = self.serve(Reply([body]), Reply([body]))
        setup_engines.install()
        folder = self.data / "runtime" / LLAMA_TARGET
        self.assertFalse(setup_engines.gutted(folder, [asset]))
        (folder / "llama-test" / "llama-server").unlink()
        (folder / "docs" / "llama-server").mkdir(parents=True)
        self.assertTrue(setup_engines.gutted(folder, [asset]))
        with patch.object(
            setup_engines, "owned", return_value=(Mock(pid=4242), {"pid": 4242})
        ):
            with self.assertRaises(RuntimeError) as caught:
                setup_engines.install()
        self.assertIn("Stop server", str(caught.exception))
        result = setup_engines.install()
        self.assertEqual((result["installed"], result["skipped"]), ([asset.name], []))
        self.assertEqual(len(web.calls), 2)
        self.assertTrue((folder / "llama-test" / "llama-server").is_file())
        self.assertFalse((folder / "docs").exists())
        self.assertFalse(setup_engines.gutted(folder, [asset]))
        self.assertFalse(setup_engines.gutted(self.data / "nowhere", [asset]))

    def test_engine_program_name_follows_the_system(self):
        folder = self.data / "runtime" / "strata-inkling-0.1.65"
        (folder / "bin").mkdir(parents=True)
        for name, program, other in (
            ("windows", "strata-inkling.exe", "strata-inkling"),
            ("linux", "strata-inkling", "strata-inkling.exe"),
        ):
            asset = setup_engines.STRATA_ASSETS[name]
            (folder / (asset.name + ".installed")).write_text("sha256:" + asset.sha256)
            (folder / "bin" / other).write_text("for another system")
            with patch.object(setup_engines, "system", return_value=name):
                self.assertTrue(setup_engines.gutted(folder, [asset]))
                (folder / "bin" / other).unlink()
                (folder / "bin" / program).write_text("server")
                self.assertFalse(setup_engines.gutted(folder, [asset]))
            (folder / "bin" / program).unlink()
        pair = setup_engines.PINNED[("windows", "cuda")]
        folder = self.data / "runtime" / LLAMA_TARGET
        folder.mkdir()
        libraries = folder / (pair[1].name + ".installed")
        libraries.write_text("sha256:" + pair[1].sha256)
        with patch.object(setup_engines, "system", return_value="windows"):
            self.assertFalse(setup_engines.gutted(folder, pair))
            libraries.unlink()
            (folder / (pair[0].name + ".installed")).write_text(
                "sha256:" + pair[0].sha256
            )
            self.assertTrue(setup_engines.gutted(folder, pair))
            (folder / "llama-server.exe").write_bytes(b"MZ")
            self.assertFalse(setup_engines.gutted(folder, pair))

    def test_install_keeps_engine_when_cancelled(self):
        asset, body = self.linux_cpu()
        folder = self.other_build()
        token = threading.Event()
        self.serve(Reply(halves(body), between=token.set))
        with self.assertRaises(runtime.Cancelled):
            setup_engines.install(cancel=token)
        self.assertEqual((folder / "old" / "llama-server").read_text(), "old engine")
        self.assertTrue((folder / "llama-old.tar.gz.installed").is_file())
        self.assertFalse((folder / (asset.name + ".installed")).exists())
        self.assertEqual(self.scratch_dirs(), [])

    def test_cancel_after_the_last_download_changes_nothing(self):
        asset, body = self.linux_cpu()
        folder = self.other_build()
        token = threading.Event()
        real = setup_engines.fetch

        def fetch_then_cancel(*args):
            path = real(*args)
            token.set()
            return path

        self.serve(Reply([body]))
        with patch.object(setup_engines, "fetch", side_effect=fetch_then_cancel):
            with self.assertRaises(runtime.Cancelled):
                setup_engines.install(cancel=token)
        self.assertTrue((folder / "old" / "llama-server").is_file())
        self.assertFalse((folder / "llama-test").exists())

    def test_failed_download_keeps_the_installed_engine(self):
        self.linux_cpu()
        folder = self.other_build()
        self.serve(Reply([b"not the pinned bytes"]))
        with self.assertRaises(ValueError) as caught:
            setup_engines.install()
        self.assertIn("pinned SHA-256", str(caught.exception))
        self.assertTrue((folder / "old" / "llama-server").is_file())
        self.assertEqual(self.scratch_dirs(), [])

    def test_install_refuses_to_replace_a_running_engine(self):
        self.linux_cpu()
        self.other_build()
        with patch.object(
            setup_engines, "owned", return_value=(Mock(pid=4242), {"pid": 4242})
        ):
            with self.assertRaises(RuntimeError) as caught:
                setup_engines.install()
        self.assertIn("Stop server", str(caught.exception))
        self.get.assert_not_called()

    def test_install_reports_low_disk_space(self):
        self.computer("linux")
        with patch("shutil.disk_usage", return_value=Mock(free=1_000_000)):
            with self.assertRaises(ValueError) as caught:
                setup_engines.install()
        self.assertIn("free", str(caught.exception))
        self.assertIn("0.1 GB", str(caught.exception))
        self.get.assert_not_called()

    def test_only_one_engine_setup_runs_at_a_time(self):
        self.linux_cpu()
        guard = Mock()
        guard.acquire.side_effect = setup_engines.Timeout("engines.lock")
        with patch.object(setup_engines, "FileLock", return_value=guard) as lock:
            with self.assertRaises(RuntimeError) as caught:
                setup_engines.install()
        self.assertIn("already running", str(caught.exception))
        self.assertEqual(lock.call_args.args[0], str(self.data / "engines.lock"))
        guard.release.assert_not_called()
        self.get.assert_not_called()

    def test_a_second_setup_leaves_the_first_one_alone(self):
        self.linux_cpu()
        scratch = self.data / "engine-abcd1234"
        scratch.mkdir()

        def impatient(path, timeout):
            return FileLock(path, timeout=0.05)

        with FileLock(str(self.data / "engines.lock")):
            with patch.object(setup_engines, "FileLock", side_effect=impatient):
                with self.assertRaises(RuntimeError) as caught:
                    setup_engines.install()
        self.assertIn("already running", str(caught.exception))
        self.assertTrue(scratch.is_dir())
        self.get.assert_not_called()

    def test_scratch_left_by_a_killed_setup_is_removed(self):
        asset, body = self.linux_cpu()
        stale = self.data / "engine-ab12_cd3"
        stale.mkdir()
        (stale / asset.name).write_bytes(b"half a download")
        kept = [
            self.data / "engine-room",
            self.data / "engine-ABCD1234",
            self.data / "engines",
        ]
        for folder in kept:
            folder.mkdir()
        note = self.data / "engine-notes123"
        note.write_text("a file, not a scratch folder")
        link = self.data / "engine-link1234"
        try:
            link.symlink_to(kept[0], target_is_directory=True)
        except OSError:
            link = None
        (kept[0] / "plans.txt").write_text("keep")
        self.serve(Reply([body]))
        setup_engines.install()
        self.assertFalse(stale.exists())
        self.assertTrue(all(folder.is_dir() for folder in kept))
        self.assertTrue(note.is_file())
        self.assertTrue((kept[0] / "plans.txt").is_file())
        if link:
            self.assertTrue(link.is_symlink())

    def test_unpack_failures_are_sentences(self):
        unsafe = (
            "{} is not an archive that can be unpacked safely and was not "
            "installed; report this before trying again."
        )
        folder = self.data / "engine"
        for name, body in (
            ("llama-bad.tar.gz", b"not a tarball"),
            ("llama-bad.zip", b"not a zip file"),
            ("llama-cut.tar.gz", tarball({"llama/llama-server": b"x" * 4096})[:60]),
            ("llama-escape.tar.gz", tarball({"../escape.txt": b"x"})),
        ):
            archive = self.data / name
            archive.write_bytes(body)
            with self.assertRaises(RuntimeError) as caught:
                setup_engines.unpack(archive, folder)
            self.assertEqual(str(caught.exception), unsafe.format(name))
        self.assertFalse((self.data / "escape.txt").exists())
        for name, body, target in (
            ("llama-ok.tar.gz", tarball({"a": b"x"}), "tarfile.TarFile.extractall"),
            ("llama-ok.zip", zipped({"a": b"x"}), "zipfile.ZipFile.extractall"),
        ):
            archive = self.data / name
            archive.write_bytes(body)
            with patch(target, side_effect=OSError(28, "No space left on device")):
                with self.assertRaises(RuntimeError) as caught:
                    setup_engines.unpack(archive, folder)
            self.assertEqual(
                str(caught.exception),
                f"Could not unpack {name} into {folder}; free up disk space, close "
                "programs using that folder, and run engine setup again.",
            )
            setup_engines.unpack(archive, folder)
        self.assertEqual((folder / "a").read_bytes(), b"x")

    def test_moving_retries_only_on_windows(self):
        busy = PermissionError("in use")
        with patch.object(setup_engines, "system", return_value="windows"), patch(
            "os.replace", side_effect=[busy, busy, None]
        ) as replace, patch("time.sleep") as sleep:
            setup_engines.move("a", "b")
            self.assertEqual(replace.call_count, 3)
            self.assertEqual(sleep.call_count, 2)
            replace.side_effect = busy
            replace.reset_mock()
            with self.assertRaises(RuntimeError) as caught:
                setup_engines.move(self.data / "Strata", self.data / "there")
            self.assertEqual(replace.call_count, 11)
            self.assertIn("Could not move Strata into place", str(caught.exception))
        with patch.object(setup_engines, "system", return_value="linux"), patch(
            "os.replace", side_effect=busy
        ) as replace, patch("time.sleep") as sleep:
            with self.assertRaises(RuntimeError):
                setup_engines.move("a", "b")
            self.assertEqual(replace.call_count, 1)
            sleep.assert_not_called()

    def test_clearing_a_busy_folder_is_a_sentence(self):
        with patch("shutil.rmtree", side_effect=PermissionError("in use")):
            with self.assertRaises(RuntimeError) as caught:
                setup_engines.clear(self.data / "runtime")
        self.assertIn("press Stop server", str(caught.exception))


class StrataTest(EngineCase):
    def cuda(self, name="linux"):
        llama = tarball({"llama-test/llama-server": b"engine"})
        libs = tarball({"cudart-test/libcublas.so.13": b"library"})
        strata = tarball({"strata-inkling": b"server"})
        assets = [
            asset_for("llama-test-bin-ubuntu-cuda-13.5-x64.tar.gz", llama),
            asset_for("cudart-llama-test-bin-ubuntu-cuda-13.5-x64.tar.gz", libs),
            asset_for(
                "strata-inkling-test-linux-x86_64-cuda-cuda13.tar.gz",
                strata,
                STRATA_REPO,
                "v0.2.19",
                "strata-inkling-0.1.65",
            ),
        ]
        self.computer(name, gpu="RTX 5090", vendors={"nvidia"})
        self.pinned((name, "cuda"), *assets[:2])
        self.enterContext(patch.dict(setup_engines.STRATA_ASSETS, {name: assets[2]}))
        return assets, [llama, libs, strata]

    def fake_git(self, heads):
        def clone(tag, dest, cancel=None):
            (Path(dest) / ".git").mkdir(parents=True)
            (Path(dest) / "server.py").write_text(tag)

        def head(folder):
            folder = Path(folder)
            if not (folder / ".git").exists():
                return ""
            return heads["fresh" if (folder / "server.py").exists() else "stale"]

        self.enterContext(patch.object(setup_engines, "revision", side_effect=head))
        return self.enterContext(
            patch.object(setup_engines, "git_clone", side_effect=clone)
        )

    def test_strata_skipped_without_git(self):
        assets, bodies = self.cuda()
        web = self.serve(Reply([bodies[0]]), Reply([bodies[1]]))
        with patch("shutil.which", return_value=None):
            result = setup_engines.install()
        self.assertEqual(result["backend"], "cuda")
        self.assertEqual(result["installed"], [assets[0].name, assets[1].name])
        self.assertFalse(result["strata"])
        self.assertEqual(
            result["notes"],
            [
                "Strata was skipped because Git is not installed; install Git and "
                "run engine setup again to add Strata."
            ],
        )
        self.assertEqual(len(web.calls), 2)
        self.assertFalse(any("strata" in url.lower() for url, _ in web.calls))
        self.assertFalse((self.data / "runtime" / "strata-inkling-0.1.65").exists())
        self.assertEqual(setup_engines.installed_backend(), "cuda")

    def test_cuda_install_adds_strata_and_its_source(self):
        assets, bodies = self.cuda()
        clone = self.fake_git({"fresh": PINNED_COMMIT})
        web = self.serve(*[Reply([body]) for body in bodies])
        events = []
        with patch("shutil.which", return_value="/usr/bin/git"):
            result = setup_engines.install(progress=events.append)
            again = setup_engines.install()
        self.assertTrue(result["strata"])
        self.assertEqual(result["installed"], [a.name for a in assets])
        self.assertIn(
            "/Niko1221/Strata/releases/download/v0.2.19/" + assets[2].name,
            web.calls[2][0],
        )
        strata = self.data / "runtime" / "strata-inkling-0.1.65"
        self.assertTrue((strata / "strata-inkling").is_file())
        self.assertEqual((self.data / "Strata" / "server.py").read_text(), "v0.2.19")
        self.assertIn("source", [e["phase"] for e in events])
        downloads = [e for e in events if e["phase"] == "download"]
        self.assertEqual((downloads[-1]["index"], downloads[-1]["count"]), (3, 3))
        self.assertEqual(again["skipped"], [a.name for a in assets])
        clone.assert_called_once()
        self.assertEqual(len(web.calls), 3)
        with patch("shutil.which", return_value="/usr/bin/git"):
            declined = setup_engines.install(strata=False)
        self.assertFalse(declined["strata"])
        self.assertEqual(declined["skipped"], [a.name for a in assets[:2]])

    def test_bad_strata_source_leaves_llama_installed(self):
        assets, bodies = self.cuda()
        self.fake_git({"fresh": "a" * 40})
        self.serve(*[Reply([body]) for body in bodies])
        with patch("shutil.which", return_value="/usr/bin/git"):
            with self.assertRaises(ValueError) as caught:
                setup_engines.install()
        self.assertIn("pinned commit", str(caught.exception))
        self.assertFalse((self.data / "Strata").exists())
        self.assertEqual(setup_engines.installed_backend(), "cuda")
        self.assertEqual(self.scratch_dirs(), [])

    def test_strata_source_is_checked_before_it_is_placed(self):
        checkout = self.data / "Strata"
        scratch = self.data / "scratch"
        scratch.mkdir()
        heads = {"fresh": "a" * 40}
        clone = self.fake_git(heads)
        events = []
        with self.assertRaises(ValueError) as caught:
            setup_engines.strata_source(
                checkout, "v0.2.19", PINNED_COMMIT, scratch, events.append
            )
        self.assertIn("did not match its pinned commit", str(caught.exception))
        self.assertFalse(checkout.exists())
        self.assertEqual(events[0]["phase"], "source")
        shutil.rmtree(scratch / "Strata")
        heads["fresh"] = ""
        with self.assertRaises(ValueError):
            setup_engines.strata_source(checkout, "v0.2.19", None, scratch)
        self.assertFalse(checkout.exists())
        shutil.rmtree(scratch / "Strata")
        heads["fresh"] = PINNED_COMMIT
        self.assertEqual(
            setup_engines.strata_source(checkout, "v0.2.19", PINNED_COMMIT, scratch),
            PINNED_COMMIT,
        )
        self.assertTrue((checkout / ".git").is_dir())
        self.assertFalse((scratch / "Strata").exists())
        self.assertEqual(clone.call_args.args[:2], ("v0.2.19", scratch / "Strata"))
        clone.reset_mock()
        self.assertEqual(
            setup_engines.strata_source(checkout, "v0.2.19", PINNED_COMMIT, scratch),
            PINNED_COMMIT,
        )
        clone.assert_not_called()

    def test_stale_strata_checkout_is_replaced(self):
        checkout = self.data / "Strata"
        scratch = self.data / "scratch"
        (checkout / ".git").mkdir(parents=True)
        (checkout / "old.txt").write_text("left by a failed check")
        scratch.mkdir()
        clone = self.fake_git({"stale": "b" * 40, "fresh": PINNED_COMMIT})
        self.assertEqual(
            setup_engines.strata_source(checkout, "v0.2.19", None, scratch), "b" * 40
        )
        clone.assert_not_called()
        self.assertEqual(
            setup_engines.strata_source(checkout, "v0.2.19", PINNED_COMMIT, scratch),
            PINNED_COMMIT,
        )
        self.assertTrue((checkout / "server.py").is_file())
        self.assertFalse((checkout / "old.txt").exists())
        self.assertTrue((scratch / "stale-source" / "old.txt").is_file())

    def test_broken_checkout_without_git_metadata_is_replaced(self):
        checkout = self.data / "Strata"
        scratch = self.data / "scratch"
        checkout.mkdir()
        (checkout / "partial.txt").write_text("interrupted clone")
        scratch.mkdir()
        self.fake_git({"fresh": PINNED_COMMIT})
        setup_engines.strata_source(checkout, "v0.2.19", PINNED_COMMIT, scratch)
        self.assertFalse((checkout / "partial.txt").exists())
        self.assertTrue((checkout / "server.py").is_file())

    def test_revision_never_reads_a_parent_repository(self):
        checkout = self.data / "Strata"
        checkout.mkdir()
        with patch("shutil.which", return_value="/usr/bin/git"), patch(
            "subprocess.check_output", return_value=PINNED_COMMIT + "\n"
        ) as run:
            self.assertEqual(setup_engines.revision(checkout), "")
            run.assert_not_called()
            (checkout / ".git").mkdir()
            self.assertEqual(setup_engines.revision(checkout), PINNED_COMMIT)
            argv, options = run.call_args.args[0], run.call_args.kwargs
            self.assertEqual(
                argv, ["/usr/bin/git", "-C", str(checkout), "rev-parse", "HEAD"]
            )
            self.assertEqual(options["env"]["GIT_CEILING_DIRECTORIES"], str(self.data))
            self.assertEqual(options["stdin"], subprocess.DEVNULL)
            self.assertEqual(options["timeout"], 30)
            for failure in (
                subprocess.CalledProcessError(128, "git"),
                subprocess.TimeoutExpired("git", 30),
                OSError("no git"),
            ):
                run.side_effect = failure
                self.assertEqual(setup_engines.revision(checkout), "")

    def test_clone_runs_git_without_a_shell_and_can_be_cancelled(self):
        dest = self.data / "scratch" / "Strata"
        child = Mock(returncode=0)
        child.poll.side_effect = [None, None, 0]
        with patch("shutil.which", return_value="/usr/bin/git"), patch(
            "subprocess.Popen", return_value=child
        ) as spawn:
            setup_engines.git_clone("v0.2.19", dest)
            argv, options = spawn.call_args.args[0], spawn.call_args.kwargs
            self.assertEqual(
                argv,
                [
                    "/usr/bin/git",
                    "clone",
                    "--quiet",
                    "--depth",
                    "1",
                    "--branch",
                    "v0.2.19",
                    "https://github.com/Niko1221/Strata.git",
                    str(dest),
                ],
            )
            self.assertEqual(options["env"]["GIT_TERMINAL_PROMPT"], "0")
            self.assertEqual(options["stdin"], subprocess.DEVNULL)
            self.assertNotIn("shell", options)
            self.assertEqual(self.pause.call_count, 2)
            failed = Mock(returncode=128)
            failed.poll.return_value = 128
            spawn.return_value = failed
            with self.assertRaises(RuntimeError) as caught:
                setup_engines.git_clone("v0.2.19", dest)
            self.assertIn("Git could not fetch the Strata", str(caught.exception))
            spawn.side_effect = FileNotFoundError("git")
            with self.assertRaises(RuntimeError):
                setup_engines.git_clone("v0.2.19", dest)
            token = threading.Event()
            token.set()
            running = Mock()
            running.poll.return_value = None
            spawn.side_effect, spawn.return_value = None, running
            with self.assertRaises(runtime.Cancelled):
                setup_engines.git_clone("v0.2.19", dest, token)
            running.terminate.assert_called_once()
            running.wait.assert_called_once_with(10)


class UpdateTest(EngineCase):
    def release(self, tag, names, **extra):
        return dict(
            tag_name=tag,
            draft=False,
            prerelease=False,
            assets=[
                dict(name=name, digest="sha256:" + "ab" * 32, size=1234)
                for name in names
            ],
        ) | extra

    def test_update_picks_assets_for_the_build(self):
        pick = setup_engines.pick
        linux = [
            "llama-b9-bin-ubuntu-cuda-12.8-x64.tar.gz",
            "llama-b9-bin-ubuntu-cuda-13.5-x64.tar.gz",
            "llama-b9-bin-ubuntu-cuda-13.10-x64.tar.gz",
            "llama-b9-bin-ubuntu-cuda-13.10-arm64.tar.gz",
            "cudart-llama-b9-bin-ubuntu-cuda-13.5-x64.tar.gz",
            "cudart-llama-b9-bin-ubuntu-cuda-13.10-x64.tar.gz",
            "llama-b9-bin-ubuntu-vulkan-x64.tar.gz",
            "llama-b9-bin-ubuntu-x64.tar.gz",
        ]
        windows = [
            "llama-b9-bin-win-cuda-13.5-x64.zip",
            "llama-b9-bin-win-cuda-13.10-x64.zip",
            "cudart-llama-bin-win-cuda-13.5-x64.zip",
            "cudart-llama-bin-win-cuda-13.10-x64.zip",
            "llama-b9-bin-win-vulkan-x64.zip",
            "llama-b9-bin-win-cpu-x64.zip",
        ]
        self.assertEqual(
            pick(LLAMA_REPO, "b9", "cuda", "linux", linux),
            [
                "llama-b9-bin-ubuntu-cuda-13.10-x64.tar.gz",
                "cudart-llama-b9-bin-ubuntu-cuda-13.10-x64.tar.gz",
            ],
        )
        self.assertEqual(
            pick(LLAMA_REPO, "b9", "cuda", "windows", windows),
            [
                "llama-b9-bin-win-cuda-13.10-x64.zip",
                "cudart-llama-bin-win-cuda-13.10-x64.zip",
            ],
        )
        for name, backend, names, expected in [
            ("linux", "vulkan", linux, "llama-b9-bin-ubuntu-vulkan-x64.tar.gz"),
            ("linux", "cpu", linux, "llama-b9-bin-ubuntu-x64.tar.gz"),
            ("windows", "vulkan", windows, "llama-b9-bin-win-vulkan-x64.zip"),
            ("windows", "cpu", windows, "llama-b9-bin-win-cpu-x64.zip"),
            ("macos", "metal", ["llama-b9-bin-macos-arm64.tar.gz"], None),
        ]:
            expected = expected or names[0]
            self.assertEqual(pick(LLAMA_REPO, "b9", backend, name, names), [expected])
            others = [n for n in names if n != expected]
            self.assertEqual(pick(LLAMA_REPO, "b9", backend, name, others), [])
        self.assertEqual(pick(LLAMA_REPO, "b9", "cuda", "linux", linux[:4]), [])
        self.assertEqual(pick(LLAMA_REPO, "b9", "cuda", "linux", linux[4:]), [])
        self.assertEqual(pick(LLAMA_REPO, "b8", "cuda", "linux", linux), [])
        self.assertEqual(pick(LLAMA_REPO, "b9", "metal", "linux", linux), [])
        strata = [
            "strata-inkling-0.1.70-linux-x86_64-cuda-cuda13.tar.gz",
            "strata-inkling-0.1.66-linux-x86_64-cuda-cuda13.tar.gz",
            "strata-inkling-0.1.66-windows-x86_64-cuda-cuda13.tar.gz",
            "strata-other-linux-x86_64-cuda-cuda13.tar.gz",
        ]
        self.assertEqual(
            pick(STRATA_REPO, "v1", "cuda", "linux", strata), [strata[1]]
        )
        self.assertEqual(
            pick(STRATA_REPO, "v1", "cuda", "windows", strata), [strata[2]]
        )
        self.assertEqual(pick(STRATA_REPO, "v1", "cuda", "linux", strata[2:]), [])
        self.assertEqual(pick(STRATA_REPO, "v1", "vulkan", "linux", strata), [])

    def test_update_needs_a_published_digest(self):
        name = "llama-b12700-bin-ubuntu-vulkan-x64.tar.gz"
        for field, value in (
            ("digest", None),
            ("digest", "sha256:" + "AB" * 32),
            ("digest", "md5:" + "ab" * 16),
            ("size", "1234"),
            ("size", True),
        ):
            release = self.release("b12700", [name])
            release["assets"][0][field] = value
            self.serve(Reply(data=[release]))
            with self.assertRaises(ValueError) as caught:
                setup_engines.newest_assets("vulkan", "linux", False)
            self.assertEqual(
                str(caught.exception),
                f"GitHub did not publish a digest for {name}, so the update was not "
                "installed.",
            )

    def test_update_takes_the_first_compatible_release(self):
        vulkan = "llama-{}-bin-ubuntu-vulkan-x64.tar.gz"
        web = self.serve(
            Reply(
                data=[
                    self.release("b12703", [vulkan.format("b12703")], draft=True),
                    self.release(
                        "nightly", [vulkan.format("nightly")], prerelease=True
                    ),
                    self.release("b12702/../x", [vulkan.format("b12702/../x")]),
                    self.release("b12701", ["llama-b12701-bin-macos-arm64.tar.gz"]),
                    self.release("b12700", [vulkan.format("b12700")], prerelease=True),
                    self.release("b12699", [vulkan.format("b12699")]),
                ]
            )
        )
        assets, tags = setup_engines.newest_assets("vulkan", "linux", False)
        self.assertEqual(
            assets,
            [
                Asset(
                    LLAMA_REPO,
                    "b12700",
                    vulkan.format("b12700"),
                    "llama-b12700",
                    "ab" * 32,
                    1234,
                )
            ],
        )
        self.assertEqual(tags, {LLAMA_REPO: "b12700"})
        url, options = web.calls[0]
        self.assertEqual(
            url, "https://api.github.com/repos/ggml-org/llama.cpp/releases"
        )
        self.assertEqual(options["params"], {"per_page": 15})
        self.assertEqual(len(web.calls), 1)

    def test_update_lookup_failures_are_sentences(self):
        for status in (403, 429):
            self.serve(Reply(status=status))
            with self.assertRaises(RuntimeError) as caught:
                setup_engines.newest_assets("cpu", "linux", False)
            self.assertIn("GitHub is limiting release lookups", str(caught.exception))
        self.serve(Reply(data=[self.release("b12700", ["llama-b12700-ui.tar.gz"])]))
        with self.assertRaises(ValueError) as caught:
            setup_engines.newest_assets("cpu", "linux", False)
        self.assertEqual(
            str(caught.exception),
            "No compatible release was found for ggml-org/llama.cpp; keep the Tested "
            "engine.",
        )
        offline = (
            "Could not look up ggml-org/llama.cpp releases on GitHub; check your "
            "connection and try Update again, or keep the Tested engine."
        )
        page = Mock(status_code=200)
        page.json.side_effect = requests.exceptions.JSONDecodeError("x", "<html>", 0)
        refused = requests.ConnectionError("HTTPSConnectionPool(host='api.github.com')")
        for failure in (
            refused,
            requests.Timeout("read timed out"),
            Reply(status=404),
            Reply(status=502),
            page,
        ):
            self.serve(failure)
            with self.assertRaises(RuntimeError) as caught:
                setup_engines.newest_assets("cpu", "linux", False)
            self.assertEqual(str(caught.exception), offline)
        self.computer("linux")
        self.serve(refused)
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                setup_engines.main(["--update"])
        self.assertEqual(caught.exception.code, offline)
        self.assertEqual(self.scratch_dirs(), [])
        odd = [7, {"tag_name": ["b1"]}, {"tag_name": "b1", "assets": 7}]
        odd.append({"tag_name": "b2", "assets": ["x", {"name": ["y"]}, {"name": 3}]})
        for data in ({"message": "unexpected"}, odd):
            self.serve(Reply(data=data))
            with self.assertRaises(ValueError) as caught:
                setup_engines.newest_assets("cpu", "linux", False)
            self.assertIn("No compatible release", str(caught.exception))

    def test_update_installs_beside_the_tested_engine(self):
        body = tarball({"llama-b12700/llama-server": b"newer"})
        name = "llama-b12700-bin-macos-arm64.tar.gz"
        release = self.release("b12700", [name])
        release["assets"][0] |= dict(
            digest="sha256:" + hashlib.sha256(body).hexdigest(),
            size=len(body),
            browser_download_url="https://example.invalid/elsewhere",
        )
        self.computer("macos", arch="arm64")
        runtime.write_json(
            self.data / "updates.json",
            dict(
                engines={STRATA_REPO: "kept-folder"},
                tags={STRATA_REPO: "v0.2.20"},
                source="kept-source",
                revision="kept-revision",
            ),
        )
        web = self.serve(Reply(data=[release]), Reply([body]))
        result = setup_engines.install(update=True)
        folder = self.data / "updates" / "llama-b12700"
        self.assertEqual(result["channel"], "Updated")
        self.assertEqual(result["installed"], [name])
        self.assertEqual(
            (folder / "llama-b12700" / "llama-server").read_bytes(), b"newer"
        )
        self.assertEqual(
            web.calls[1][0],
            "https://github.com/ggml-org/llama.cpp/releases/download/b12700/" + name,
        )
        self.assertFalse((self.data / "runtime").exists())
        self.assertEqual(
            json.loads((self.data / "updates.json").read_text()),
            dict(
                engines={STRATA_REPO: "kept-folder", LLAMA_REPO: str(folder)},
                tags={STRATA_REPO: "v0.2.20", LLAMA_REPO: "b12700"},
                source="kept-source",
                revision="kept-revision",
                backend="metal",
            ),
        )

    def test_update_that_cannot_be_unpacked_is_not_recorded(self):
        body = tarball({"llama-b12700/llama-server": b"x", "../escape.txt": b"x"})
        name = "llama-b12700-bin-macos-arm64.tar.gz"
        release = self.release("b12700", [name])
        release["assets"][0] |= dict(
            digest="sha256:" + hashlib.sha256(body).hexdigest(), size=len(body)
        )
        self.computer("macos", arch="arm64")
        self.serve(Reply(data=[release]), Reply([body]))
        with self.assertRaises(RuntimeError) as caught:
            setup_engines.install(update=True)
        self.assertIn("unpacked safely", str(caught.exception))
        folder = self.data / "updates" / "llama-b12700"
        self.assertEqual(list(folder.glob("*.installed")), [])
        self.assertFalse((self.data / "updates" / "escape.txt").exists())
        self.assertFalse((self.data / "updates.json").exists())
        self.assertEqual(self.scratch_dirs(), [])
        good = tarball({"llama-b12700/llama-server": b"newer"})
        release["assets"][0] |= dict(
            digest="sha256:" + hashlib.sha256(good).hexdigest(), size=len(good)
        )
        self.serve(Reply(data=[release]), Reply([good]))
        setup_engines.install(update=True)
        self.assertEqual(
            (folder / "llama-b12700" / "llama-server").read_bytes(), b"newer"
        )

    def test_update_with_strata_records_its_source(self):
        llama = tarball({"llama-b12700/llama-server": b"engine"})
        libs = tarball({"cudart/libcublas.so.13": b"library"})
        strata = tarball({"strata-inkling": b"server"})
        names = [
            "llama-b12700-bin-ubuntu-cuda-13.5-x64.tar.gz",
            "cudart-llama-b12700-bin-ubuntu-cuda-13.5-x64.tar.gz",
            "strata-inkling-0.1.66-linux-x86_64-cuda-cuda13.tar.gz",
        ]
        releases = [
            self.release("b12700", names[:2]),
            self.release("v0.2.20", names[2:]),
        ]
        for row, body in zip(
            releases[0]["assets"] + releases[1]["assets"], (llama, libs, strata)
        ):
            row |= dict(
                digest="sha256:" + hashlib.sha256(body).hexdigest(), size=len(body)
            )
        self.computer("linux", gpu="RTX 5090", vendors={"nvidia"})

        def clone(tag, dest, cancel=None):
            (Path(dest) / ".git").mkdir(parents=True)

        def head(folder):
            return "c" * 40 if (Path(folder) / ".git").exists() else ""

        self.serve(
            Reply(data=[releases[0]]),
            Reply(data=[releases[1]]),
            Reply([llama]),
            Reply([libs]),
            Reply([strata]),
        )
        with patch("shutil.which", return_value="/usr/bin/git"), patch.object(
            setup_engines, "git_clone", side_effect=clone
        ) as cloned, patch.object(setup_engines, "revision", side_effect=head):
            result = setup_engines.install(update=True)
        updates = self.data / "updates"
        self.assertEqual(result["installed"], names)
        self.assertEqual(cloned.call_args.args[0], "v0.2.20")
        self.assertEqual(
            json.loads((self.data / "updates.json").read_text()),
            dict(
                engines={
                    LLAMA_REPO: str(updates / "llama-b12700"),
                    STRATA_REPO: str(updates / "strata-v0.2.20"),
                },
                tags={LLAMA_REPO: "b12700", STRATA_REPO: "v0.2.20"},
                backend="cuda",
                source=str(updates / "src-v0.2.20"),
                revision="c" * 40,
            ),
        )
        self.assertTrue((updates / "src-v0.2.20" / ".git").is_dir())
        self.assertTrue((updates / "strata-v0.2.20" / "strata-inkling").is_file())


class PlanTest(EngineCase):
    def test_plan_lists_downloads_and_installed_state(self):
        self.computer("linux", vendors={"amd"}, vulkan=True)
        name = "llama-b12633-bin-ubuntu-vulkan-x64.tar.gz"
        with patch.object(runtime, "runtime", side_effect=ValueError("missing")):
            before = setup_engines.plan()
        self.assertEqual(
            before,
            {
                "system": "linux",
                "arch": "x86_64",
                "supported": True,
                "backend": "vulkan",
                "vendor": "amd",
                "gpu": "",
                "reason": "AMD GPU detected, so the Vulkan build is used.",
                "tag": "b12633",
                "assets": [{"name": name, "size": 36909894, "installed": False}],
                "download_bytes": 36909894,
                "installed_backend": "",
                "engine_ready": False,
                "strata": False,
                "notes": [],
            },
        )
        folder = self.data / "runtime" / LLAMA_TARGET
        folder.mkdir(parents=True)
        (folder / (name + ".installed")).write_text("sha256:" + RELEASE[name][0] + "\n")
        server = folder / "llama-server"
        with patch.object(runtime, "runtime", return_value=server) as find:
            after = setup_engines.plan()
            chosen = setup_engines.plan("cpu")
        find.assert_called_with(LLAMA_TARGET, "llama-server.exe")
        self.assertEqual(after["download_bytes"], 0)
        self.assertEqual(after["installed_backend"], "vulkan")
        self.assertTrue(after["assets"][0]["installed"])
        self.assertTrue(after["engine_ready"])
        self.assertEqual(chosen["backend"], "cpu")
        self.assertEqual(
            chosen["reason"], "The CPU build is used because you chose it."
        )
        self.assertEqual(chosen["download_bytes"], 23225608)
        self.assertEqual(chosen["installed_backend"], "vulkan")
        self.assertEqual(setup_engines.detect()["backend"], "vulkan")
        self.get.assert_not_called()

    def test_plan_includes_strata_only_with_cuda_and_git(self):
        self.computer("windows", arch="AMD64", gpu="RTX 5090")
        with patch.object(runtime, "runtime", side_effect=ValueError("missing")):
            with patch("shutil.which", return_value="C:\\Git\\git.exe"):
                ready = setup_engines.plan()
            with patch("shutil.which", return_value=None):
                bare = setup_engines.plan()
        self.assertTrue(ready["strata"])
        self.assertEqual(len(ready["assets"]), 3)
        self.assertEqual(
            ready["download_bytes"], 225649717 + 426314199 + 411426893
        )
        self.assertFalse(bare["strata"])
        self.assertEqual(len(bare["assets"]), 2)
        self.assertIn("Git is not installed", bare["notes"][0])

    def test_markers_from_an_earlier_version_are_recognised(self):
        self.computer("linux", gpu="RTX 5090")
        folder = self.data / "runtime" / LLAMA_TARGET
        folder.mkdir(parents=True)
        self.assertEqual(setup_engines.installed_backend(), "")
        for name in LAYOUT[("linux", "cuda")]:
            (folder / (name + ".installed")).write_text("sha256:" + RELEASE[name][0])
            last = name
        self.assertEqual(setup_engines.installed_backend(), "cuda")
        (folder / "llama-b12633").mkdir()
        (folder / "llama-b12633" / "llama-server").write_text("engine from 0.3")
        with patch("shutil.which", return_value=None):
            result = setup_engines.install()
        self.assertEqual(result["installed"], [])
        self.assertEqual(result["skipped"], LAYOUT[("linux", "cuda")])
        (folder / (last + ".installed")).write_text("sha256:" + "0" * 64)
        self.assertEqual(setup_engines.installed_backend(), "")

    def test_info_adds_the_engine_list(self):
        self.computer("macos", arch="arm64")
        support = {"Auto": "", "Strata": "Strata needs an NVIDIA GPU."}
        with patch.object(
            runtime, "runtime", side_effect=ValueError("missing")
        ), patch.object(
            runtime, "available_engines", create=True, return_value=["Auto"]
        ), patch.object(
            runtime, "engine_support", create=True, return_value=support
        ):
            got = setup_engines.info("auto")
            plan = setup_engines.plan("auto")
        self.assertEqual(got, plan | {"engines": ["Auto"], "support": support})
        self.assertEqual(got["backend"], "metal")
        self.assertEqual(got["download_bytes"], 16600069)

    def test_info_lists_engines_from_the_real_runtime(self):
        missing = str(self.data / "no-preset.json")
        self.enterContext(patch.dict(os.environ, WORKBENCH_FLASH_CONFIG=missing))
        os.environ.pop("WORKBENCH_RUNTIME", None)
        self.computer("macos", arch="arm64")
        with patch.object(runtime, "system", return_value="macos"):
            mac = setup_engines.info()
        self.assertEqual(mac["engines"], ["Auto", "llama.cpp", "Custom"])
        self.assertEqual(set(mac["support"]), set(runtime.ENGINES))
        self.assertIn("on this Mac choose llama.cpp", mac["support"]["vLLM"])
        self.assertFalse(mac["engine_ready"])
        self.computer("linux", gpu="RTX 5090")
        with patch.object(runtime, "system", return_value="linux"):
            box = setup_engines.info("cuda")
        self.assertEqual(
            box["engines"], ["Auto", "Strata", "llama.cpp", "vLLM", "Custom"]
        )
        self.assertEqual(box["support"]["Strata"], "")
        self.assertEqual(json.loads(json.dumps(box)), box)


class CommandLineTest(EngineCase):
    def test_command_line_exits_with_a_sentence(self):
        self.computer("linux")
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        with self.assertRaises(SystemExit) as caught:
            setup_engines.main(["--backend", "bogus"])
        self.assertEqual(
            caught.exception.code,
            "Choose auto, cuda, vulkan or cpu for the engine build.",
        )
        self.get.assert_not_called()
        for failure in (
            runtime.Cancelled("Engine setup was cancelled."),
            RuntimeError("Press Stop server, then run engine setup again."),
            requests.ConnectionError("offline"),
        ):
            with patch.object(setup_engines, "install", side_effect=failure):
                with self.assertRaises(SystemExit) as caught:
                    setup_engines.main([])
            self.assertEqual(caught.exception.code, str(failure))

    def test_captured_failure_is_marked_for_the_setup_tab(self):
        import tooling

        sentence = "Open Advanced → Setup, then run engine setup again."
        log, screen = io.StringIO(), io.StringIO()
        screen.isatty = lambda: True
        narrow = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
        failure = RuntimeError(sentence)
        for out in (log, screen, narrow, None):
            with (
                patch.object(setup_engines, "install", side_effect=failure),
                contextlib.redirect_stdout(out),
                self.assertRaises(SystemExit) as caught,
            ):
                setup_engines.main([])
            self.assertEqual(caught.exception.code, sentence)
        self.assertEqual(setup_engines.MARK, tooling.MARK)
        self.assertEqual(log.getvalue(), tooling.MARK + sentence + "\n")
        self.assertEqual(tooling.marked(log.getvalue() + sentence + "\n"), sentence)
        self.assertEqual(screen.getvalue(), "")

    def test_command_line_prints_plain_progress(self):
        asset, body = self.linux_cpu()
        self.serve(Reply(halves(body)))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            setup_engines.main([])
        lines = out.getvalue().splitlines()
        self.assertEqual(
            lines[0], "No supported GPU was detected, so the CPU build is used."
        )
        self.assertIn(f"Downloading {asset.name} 100%", lines)
        self.assertIn(f"Unpacking {asset.name}", lines)
        self.assertEqual(lines[-1], "The llama.cpp CPU build is ready.")
        step = rf"\ADownloading {re.escape(asset.name)} (0|[1-9]0|100)%\Z"
        for line in lines[1:-2]:
            self.assertRegex(line, step)

    def test_command_line_passes_its_options(self):
        result = dict(backend="cuda", notes=["Strata was skipped."])
        out = io.StringIO()
        with patch.object(setup_engines, "install", return_value=result) as install:
            with contextlib.redirect_stdout(out):
                setup_engines.main(["--update", "--backend", "cuda", "--no-strata"])
                setup_engines.main([])
        first, second = install.call_args_list
        self.assertEqual(first.args[:3], (True, "cuda", False))
        self.assertEqual(second.args[:3], (False, "auto", None))
        self.assertIsNone(first.args[4])
        self.assertEqual(out.getvalue().splitlines()[0], "Strata was skipped.")


if __name__ == "__main__":
    unittest.main()
