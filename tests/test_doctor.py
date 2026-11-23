"""Doctor checks: every seam is patched, so nothing here touches the network, the vault or the real data folder."""

import ast
import contextlib
import io
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

if "runtime" not in sys.modules:
    os.environ.setdefault("WORKBENCH_DATA", tempfile.mkdtemp(prefix="awb-test-"))

import doctor

AGENTS = ("OMP", "Pi", "Claude Code", "Codex", "Hermes", "opencode")
ENGINE = "llama.cpp / Strata"
PROXIES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
RESTRICTED = "/proc/sys/kernel/apparmor_restrict_unprivileged_userns"
HINT = "To use pi in your own terminals, run: echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> ~/.zshrc"
DENIED = "execution error: Not authorized to send Apple events to Terminal. (-1743)"
SETUP_LINUX = doctor.ROOT / "setup-linux.sh"


def agent(name, path="", on_path=True, version="", hint=""):
    return dict(
        name=name,
        exe=name.lower().replace(" ", ""),
        path=path,
        installed=bool(path),
        on_path=on_path,
        hint=hint,
        version=version,
    )


def backend(module, name):
    return type(name, (), {"__module__": module, "name": name + " label"})()


def listener(reply):
    """A loopback server that hands every connection to reply(peer). Returns (server, port)."""
    server = socket.create_server(("127.0.0.1", 0))

    def loop():
        while True:
            try:
                peer, _ = server.accept()
            except OSError:
                return
            with peer:
                try:
                    reply(peer)
                except OSError:
                    pass

    threading.Thread(target=loop, daemon=True).start()
    return server, server.getsockname()[1]


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="awb-doctor-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data, self.root, self.home = (
            self.tmp / "data",
            self.tmp / "app",
            self.tmp / "home",
        )
        for folder in (self.data, self.root, self.home):
            folder.mkdir(mode=0o700)
        self.calls = []
        self.answers = {}
        self.fallback = (1, "")
        self.found = {}
        self.everything = False
        self.files = {}
        self.ports = set()
        self.page = ""
        self.free = 100 * 1024**3
        self.machine = "arm64"
        self.paths = {}
        self.items = [agent(name) for name in AGENTS]
        self.problem = ""
        self.owned = None
        self.vault = backend("keyring.backends.macOS", "Keyring")
        self.tooling = SimpleNamespace(
            AGENTS=AGENTS,
            location=lambda name: self.paths.get(name, ""),
            inventory=Mock(side_effect=lambda names, versions=False: self.items),
            find=lambda exe: self.paths.get("find:" + exe, ""),
            docker_problem=lambda: self.problem,
        )
        self.runtime = SimpleNamespace(PORT=10532, owned=lambda: (self.owned, {}))
        self.keyring = SimpleNamespace(
            get_keyring=lambda: self.vault, get_password=Mock()
        )
        self.engines = SimpleNamespace()
        self.module("tooling", self.tooling)
        self.module("runtime", self.runtime)
        self.module("keyring", self.keyring)
        self.module("setup_engines", self.engines)
        for item in (
            patch.dict(os.environ),
            patch.object(doctor, "DATA", self.data),
            patch.object(doctor, "ROOT", self.root),
            patch.object(doctor, "SYSTEM", "linux"),
            patch.object(doctor, "WSL_SMI", str(self.tmp / "no-nvidia-smi")),
            patch.object(doctor, "APPARMOR", self.tmp / "apparmor.d"),
            patch.object(doctor, "_run", self.fake_run),
            patch.object(doctor, "_which", self.fake_which),
            patch.object(doctor, "_read", lambda path: self.files.get(str(path))),
            patch.object(doctor, "_listening", lambda port: port in self.ports),
            patch.object(doctor, "_local_text", lambda url: self.page),
            patch.object(doctor.Path, "home", lambda: self.home),
            patch.object(doctor.platform, "machine", lambda: self.machine),
            patch.object(
                doctor.shutil,
                "disk_usage",
                lambda path: SimpleNamespace(free=self.free),
            ),
            patch.object(doctor.importlib.util, "find_spec", lambda name: object()),
        ):
            item.start()
            self.addCleanup(item.stop)
        for name in PROXIES:
            os.environ.pop(name, None)
            os.environ.pop(name.lower(), None)

    def module(self, name, value):
        previous = sys.modules.get(name, self)

        def restore():
            if previous is self:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous

        sys.modules[name] = value
        self.addCleanup(restore)

    def fake_run(self, argv, timeout=5):
        self.calls.append([str(a) for a in argv])
        answer = self.answers.get(Path(str(argv[0])).name, self.fallback)
        return answer(argv) if callable(answer) else answer

    def fake_which(self, name):
        if self.everything:
            return "/usr/bin/" + name
        return self.found.get(name, "")

    def system(self, name):
        item = patch.object(doctor, "SYSTEM", name)
        item.start()
        self.addCleanup(item.stop)

    def helper(self, data=None):
        path = (data or self.data) / "desktop-runtime" / "linux-41.0.0" / "chrome-sandbox"
        path.parent.mkdir(parents=True)
        path.write_text("")
        return path

    def assertRow(self, row, status, detail="", fix=""):
        self.assertEqual(row["status"], status, row)
        self.assertIn(detail, row["detail"])
        self.assertIn(fix, row["fix"])

    def assertContract(self, rows):
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(list(row), ["id", "title", "status", "detail", "fix"])
            self.assertIn(row["status"], doctor.STATUSES)
            self.assertTrue(all(isinstance(v, str) for v in row.values()), row)
            self.assertTrue(row["title"] and row["detail"], row)
            if row["status"] in ("warn", "fail"):
                self.assertTrue(row["fix"], row)
            if row["status"] == "ok":
                self.assertEqual(row["fix"], "", row)
        ids = [row["id"] for row in rows]
        self.assertEqual(len(ids), len(set(ids)), ids)

    def test_rows_follow_the_contract(self):
        expected = {
            "python",
            "packages",
            "data-dir",
            "vault",
            "engine",
            "gpu",
            "agents",
            "git",
            "port-model",
            "port-firecrawl",
            "docker",
            "terminal",
            "sandbox",
            "quarantine",
            "automation",
            "ssh",
            "proxy",
        } | {"cli-" + name.lower().replace(" ", "-") for name in AGENTS}
        for name in ("darwin", "linux", "windows"):
            for active in (False, True):
                with patch.object(doctor, "SYSTEM", name):
                    rows = doctor.run(None, active)
                self.assertContract(rows)
                self.assertEqual({row["id"] for row in rows}, expected)
        self.everything = True
        self.fallback = (0, "tool 1.2.3")
        self.answers["xattr"] = (0, "0081;quarantined")
        self.answers["osascript"] = (1, DENIED)
        self.paths = {ENGINE: "/opt/engine/llama-server", "find:docker": "/usr/bin/docker"}
        self.problem = "Docker is installed but not running; start Docker Desktop, then retry."
        self.items = [
            agent("OMP", "/usr/local/bin/omp", True, "19.1.0"),
            agent("Pi", "/home/me/.local/bin/pi", False),
        ] + [agent(name) for name in AGENTS[2:]]
        self.ports = {10532, 3002}
        self.files[RESTRICTED] = "1"
        self.helper()
        self.vault = backend("keyring.backends.fail", "Keyring")
        self.engines.detect = lambda: {"reason": "No supported GPU was detected."}
        os.environ["HTTPS_PROXY"] = "http://proxy.example:8080"
        for name in ("darwin", "linux", "windows"):
            for active in (False, True):
                with patch.object(doctor, "SYSTEM", name):
                    rows = doctor.run(None, active)
                self.assertContract(rows)
                self.assertEqual({row["id"] for row in rows}, expected)
        self.keyring.get_password.assert_not_called()

    def test_python_gate(self):
        with patch.object(sys, "version_info", (3, 11, 9, "final", 0)):
            old = doctor.check_python()
        self.assertRow(old, "fail", "Python 3.11.9 is too old.", "Python 3.12 or newer")
        with patch.object(sys, "version_info", (3, 12, 0, "final", 0)):
            self.assertRow(doctor.check_python(), "ok", "Python 3.12.0 (arm64) at ")
            self.system("darwin")
            self.assertEqual(doctor.check_python()["status"], "ok")
            self.machine = "x86_64"
            self.assertRow(doctor.check_python(), "warn", "Rosetta", "native arm64")
            self.system("linux")
            self.assertRow(doctor.check_python(), "ok", "(x86_64)")

    def test_packages_missing_names_pip_command(self):
        wanted = []

        def find_spec(name):
            wanted.append(name)
            return None if name == "mcp" else object()

        with patch.object(doctor.importlib.util, "find_spec", find_spec):
            row = doctor.check_packages()
            self.assertRow(row, "fail", "Missing: mcp", "-m pip install -r")
            self.assertIn(str(self.root / "requirements.txt"), row["fix"])
            self.assertIn("ptyprocess", wanted)
            self.assertNotIn("winpty", wanted)
            self.system("windows")
            del wanted[:]
            doctor.check_packages()
            self.assertIn("winpty", wanted)
            self.assertNotIn("ptyprocess", wanted)
        self.assertRow(doctor.check_packages(), "ok", "All required packages")
        with patch.object(doctor.importlib.util, "find_spec", side_effect=ValueError):
            self.assertFalse(doctor._missing("keyring"))
            self.assertTrue(doctor._missing("awb_no_such_module"))

    def test_data_dir_states(self):
        self.system("windows")
        self.assertRow(doctor.check_data_dir(), "ok", f"{self.data} · 100 GiB free")
        self.assertEqual(os.listdir(self.data), [])
        if os.name == "posix":
            self.system("linux")
            self.assertEqual(doctor.check_data_dir()["status"], "ok")
            os.chmod(self.data, 0o755)
            row = doctor.check_data_dir()
            self.assertRow(row, "warn", "Other accounts", f'chmod 700 "{self.data}"')
            os.chmod(self.data, 0o700)
            self.assertEqual(os.listdir(self.data), [])
        self.system("windows")
        self.free = 2 * 1024**3
        self.assertRow(doctor.check_data_dir(), "warn", "Only 2.0 GiB free", "Free some")
        self.free = 100 * 1024**3
        synced = self.home / "Documents" / "Agent Workbench" / ".local"
        synced.mkdir(parents=True)
        with patch.object(doctor, "DATA", synced):
            self.assertEqual(doctor.check_data_dir()["status"], "ok")
            if os.name == "posix":
                self.system("darwin")
                os.chmod(synced, 0o700)
                row = doctor.check_data_dir()
                self.assertRow(row, "warn", "iCloud Drive", "WORKBENCH_DATA")
        blocker = self.tmp / "blocker"
        blocker.write_bytes(b"")
        for missing in (blocker, blocker / "data"):
            with patch.object(doctor, "DATA", missing):
                row = doctor.check_data_dir()
            self.assertRow(row, "fail", "missing or not writable", "WORKBENCH_DATA")
        self.assertTrue(blocker.is_file())
        fresh = self.tmp / "fresh" / "data"
        self.system("windows")
        with patch.object(doctor, "DATA", fresh):
            self.assertEqual(doctor.check_data_dir()["status"], "ok")
        self.assertEqual(os.listdir(fresh), [])
        if os.name == "posix":
            self.assertEqual(fresh.stat().st_mode & 0o777, 0o700)
        with patch.object(doctor.tempfile, "mkstemp", side_effect=PermissionError):
            self.assertEqual(doctor.check_data_dir()["status"], "fail")

    @unittest.skipUnless(os.name == "posix", "the fix commands are run with a POSIX shell")
    def test_fix_commands_survive_shell_characters_in_paths(self):
        odd = self.tmp / 'my "$HOME" data'
        odd.mkdir(mode=0o755)
        os.chmod(odd, 0o755)
        with patch.object(doctor, "DATA", odd):
            row = doctor.check_data_dir()
            self.assertRow(row, "warn", "Other accounts", "Run: chmod 700 ")
            command = row["fix"][len("Run: "):]
            subprocess.run(["/bin/sh", "-c", command], stdin=subprocess.DEVNULL, check=True)
            self.assertEqual(odd.stat().st_mode & 0o777, 0o700)
            self.assertEqual(doctor.check_data_dir()["status"], "ok")

            self.files[RESTRICTED] = "1"
            helper = odd / "desktop-runtime" / "linux-41.0.0" / "chrome-sandbox"
            helper.parent.mkdir(parents=True)
            helper.write_text("")
            fix = doctor.check_sandbox()["fix"]
            self.assertEqual(
                shlex.split(fix[len("Run: "):]),
                ["sudo", "chown", "root:root", str(helper), "&&", "sudo", "chmod", "4755", str(helper)],
            )

            self.system("darwin")
            self.answers["xattr"] = lambda argv: (0 if argv[-1].startswith(str(odd)) else 1, "")
            (odd / "desktop-runtime" / "darwin-41.0.0").mkdir()
            fix = doctor.check_quarantine()["fix"]
            self.assertEqual(
                shlex.split(fix[len("Run: "):])[:4],
                ["xattr", "-dr", "com.apple.quarantine", str(odd)],
            )
            self.paths[ENGINE] = str(odd / "runtime" / "llama-server")
            self.answers["llama-server"] = (None, "")
            fix = doctor.check_engine()["fix"]
            self.assertEqual(
                shlex.split(fix[len("Run: "):])[:4],
                ["xattr", "-dr", "com.apple.quarantine", str(odd / "runtime")],
            )
        with patch.object(doctor, "ROOT", odd), patch.object(sys, "executable", str(odd / "python")):
            words = shlex.split(doctor._pip()[len("Run: "):])
        self.assertEqual(
            words,
            [str(odd / "python"), "-m", "pip", "install", "-r", str(odd / "requirements.txt")],
        )
        self.assertEqual(doctor._quote(self.data), f'"{self.data}"')
        self.system("windows")
        self.assertEqual(doctor._quote(r"C:\Users\me\$data"), r'"C:\Users\me\$data"')

    def test_vault_is_classified_without_opening(self):
        self.vault = backend("keyring.backends.fail", "Keyring")
        row = doctor.check_vault()
        self.assertRow(row, "fail", "keyring.backends.fail.Keyring", "GNOME Keyring")
        self.vault = backend("keyrings.alt.file", "PlaintextKeyring")
        self.system("windows")
        self.assertRow(doctor.check_vault(), "fail", "Plaintext", "Credential Manager")
        self.vault = backend("keyring.backends.null", "Keyring")
        self.system("darwin")
        self.assertRow(doctor.check_vault(), "fail", ".null.", "login keychain")
        self.vault = backend("keyring.backends.macOS", "Keyring")
        row = doctor.check_vault()
        self.assertRow(row, "ok", "Keyring label (not opened by this check)")
        self.keyring.get_password.assert_not_called()
        self.module("keyring", None)
        row = doctor.check_vault()
        self.assertRow(row, "fail", "keyring package is missing", "-m pip install -r")

    def test_engine_states(self):
        row = doctor.check_engine()
        self.assertRow(row, "warn", "not installed", "Advanced > Setup > llama.cpp")
        self.paths[ENGINE] = "/opt/engine/llama-server"
        self.answers["llama-server"] = (0, "version: 12633 (abc)\nbuilt with clang")
        row = doctor.check_engine()
        self.assertRow(row, "ok", "version: 12633 (abc) at /opt/engine/llama-server")
        self.assertEqual(self.calls[-1], ["/opt/engine/llama-server", "--version"])
        self.answers["llama-server"] = (1, "llama-server version 1")
        self.assertEqual(doctor.check_engine()["status"], "ok")
        self.answers["llama-server"] = (None, "")
        self.assertRow(doctor.check_engine(), "fail", "does not start", "Reinstall it")
        self.system("darwin")
        folder = Path("/opt/engine/llama-server").parent
        fix = f'xattr -dr com.apple.quarantine "{folder}"'
        self.assertRow(doctor.check_engine(), "fail", "does not start", fix)

    def test_engine_loader_errors_are_not_a_version(self):
        self.paths[ENGINE] = "/opt/engine/llama-server"
        self.answers["llama-server"] = (
            1,
            "/opt/engine/llama-server: /lib/x86_64-linux-gnu/libc.so.6: "
            "version `GLIBC_2.38' not found (required by /opt/engine/llama-server)",
        )
        self.assertRow(doctor.check_engine(), "fail", "does not start", "Reinstall it")
        self.answers["llama-server"] = (
            -6,
            "dyld[4242]: Library not loaded: @rpath/libllama.dylib\n"
            "  Reason: Incompatible library version: llama-server requires version 1.0.0",
        )
        self.assertRow(doctor.check_engine(), "fail", "does not start", "Reinstall it")
        self.answers["llama-server"] = (
            1,
            "load_backend: loaded CPU backend from /opt/engine/libggml-cpu.so\n"
            "version: 12633 (abc)\nbuilt with clang",
        )
        row = doctor.check_engine()
        self.assertRow(row, "ok", "version: 12633 (abc) at /opt/engine/llama-server")
        self.answers["llama-server"] = (0, "")
        self.assertRow(doctor.check_engine(), "ok", "llama-server at /opt/engine/")

    def test_gpu_states(self):
        self.found["nvidia-smi"] = "/usr/bin/nvidia-smi"
        self.answers["nvidia-smi"] = (0, "NVIDIA GeForce RTX 4090, 560.94, 24564 MiB")
        row = doctor.check_gpu()
        self.assertEqual(row["detail"], "NVIDIA GeForce RTX 4090, 560.94, 24564 MiB")
        self.assertEqual(row["status"], "ok")
        self.assertIn("--format=csv,noheader", self.calls[-1])
        self.answers["nvidia-smi"] = (0, "Tesla T4, 550.54, 15360 MiB")
        self.assertRow(doctor.check_gpu(), "ok", "NVIDIA Tesla T4")
        self.answers["nvidia-smi"] = (9, "NVIDIA-SMI has failed")
        self.assertRow(doctor.check_gpu(), "fail", "cannot reach", "NVIDIA driver")
        self.answers["nvidia-smi"] = (0, "")
        self.assertEqual(doctor.check_gpu()["status"], "fail")
        self.found = {"vulkaninfo": "/usr/bin/vulkaninfo"}
        self.answers["vulkaninfo"] = (0, "GPU0:\n\tdeviceName = llvmpipe (LLVM 19.1, 256 bits)")
        self.assertRow(doctor.check_gpu(), "warn", "software Vulkan", "vendor's driver")
        self.answers["vulkaninfo"] = (
            0,
            "deviceName         = AMD Radeon RX 7900 XT (RADV NAVI31)\n"
            "deviceName = llvmpipe (LLVM 19.1, 256 bits)",
        )
        row = doctor.check_gpu()
        self.assertRow(row, "ok", "Vulkan device: AMD Radeon RX 7900 XT (RADV NAVI31)")
        self.answers["vulkaninfo"] = (None, "")
        self.assertRow(doctor.check_gpu(), "warn", "No GPU was detected", "vulkan-tools")
        self.found = {}
        self.assertRow(doctor.check_gpu(), "warn", "No GPU was detected", "GPU driver")
        self.engines.detect = lambda: {"reason": "No supported GPU was detected."}
        self.assertTrue(
            doctor.check_gpu()["detail"].endswith(
                "run on the CPU. No supported GPU was detected."
            )
        )
        self.engines.detect = Mock(side_effect=RuntimeError("boom"))
        self.assertRow(doctor.check_gpu(), "warn", "No GPU was detected")
        self.assertTrue(doctor.check_gpu()["detail"].endswith("CPU."))

    def test_gpu_on_apple_silicon(self):
        self.system("darwin")
        self.found["nvidia-smi"] = "/usr/bin/nvidia-smi"

        def sysctl(argv):
            key = argv[-1]
            return (0, "Apple M3 Max") if key.endswith("brand_string") else (0, "68719476736")

        self.answers["sysctl"] = sysctl
        row = doctor.check_gpu()
        self.assertRow(row, "ok", "Apple M3 Max with 64 GiB unified memory (Metal)")
        self.assertFalse(any("nvidia-smi" in call[0] for call in self.calls))
        self.answers["sysctl"] = (None, "")
        self.assertEqual(doctor.check_gpu()["detail"], "Apple Silicon (Metal)")

    def test_cli_rows_and_summary(self):
        self.items = [
            agent("OMP", "/usr/local/bin/omp", True, "19.1.0"),
            agent("Pi", "/home/me/.local/bin/pi", False, "", HINT),
        ] + [agent(name) for name in AGENTS[2:]]
        self.found["git"] = "/usr/bin/git"
        self.answers["git"] = (0, "git version 2.50.1")
        rows = {row["id"]: row for row in doctor.check_clis()}
        self.tooling.inventory.assert_called_once_with(AGENTS, versions=True)
        self.assertEqual(
            list(rows),
            [
                "cli-omp",
                "cli-pi",
                "cli-claude-code",
                "cli-codex",
                "cli-hermes",
                "cli-opencode",
                "agents",
                "git",
            ],
        )
        self.assertRow(rows["cli-omp"], "ok", "19.1.0 at /usr/local/bin/omp")
        self.assertEqual(rows["cli-claude-code"]["title"], "Claude Code")
        self.assertRow(rows["cli-pi"], "warn", "outside the PATH of your terminals")
        self.assertEqual(rows["cli-pi"]["fix"], HINT)
        for name in ("cli-claude-code", "cli-codex", "cli-hermes", "cli-opencode"):
            self.assertRow(rows[name], "skip", "Not installed.", "Advanced > Setup")
        self.assertRow(rows["agents"], "ok", "2 of 6 installed.")
        self.assertRow(rows["git"], "ok", "git version 2.50.1")

        self.items = [
            agent("OMP", "/home/me/.local/bin/omp", False),
            agent("Codex", "/usr/local/bin/codex", True),
        ]
        rows = {row["id"]: row for row in doctor.check_clis()}
        fix = 'Add "/home/me/.local/bin" to your PATH'
        self.assertRow(rows["cli-omp"], "warn", "/home/me/.local/bin/omp", fix)
        self.assertRow(rows["cli-codex"], "ok", "version unknown at ")

        self.items = []
        self.found = {}
        rows = {row["id"]: row for row in doctor.check_clis()}
        self.assertEqual(list(rows), ["agents", "git"])
        self.assertRow(rows["agents"], "fail", "No agent CLI", "OMP > Install")
        self.assertRow(rows["git"], "warn", "Git was not found.", "Install Git")
        self.items = [agent(name) for name in AGENTS]
        self.assertEqual(doctor.check_clis()[-2]["status"], "fail")

    def test_git_shim_is_not_started_without_developer_tools(self):
        self.system("darwin")
        self.found["git"] = "/usr/bin/git"
        self.answers["git"] = (0, "git version 2.50.1 (Apple Git-155)")
        self.assertRow(doctor.check_clis()[-1], "warn", "Git was not found.")
        self.assertFalse(any(call[0] == "/usr/bin/git" for call in self.calls))
        self.answers["xcode-select"] = (0, "/Library/Developer/CommandLineTools")
        self.assertRow(doctor.check_clis()[-1], "ok", "Apple Git-155")

    def test_port_states(self):
        model, crawl = doctor.check_ports()
        self.assertEqual((model["id"], crawl["id"]), ("port-model", "port-firecrawl"))
        self.assertRow(model, "ok", "Port 10532 is free.")
        self.assertRow(crawl, "ok", "Port 3002 is free.")
        self.ports = {10532, 3002}
        model, crawl = doctor.check_ports()
        self.assertRow(model, "fail", "used by another program", "WORKBENCH_PORT")
        self.assertRow(crawl, "warn", "used by another program", "Stop that program")
        self.page = '{"message":"Firecrawl API"}'
        self.assertRow(doctor.check_ports()[1], "ok", "running on port 3002")
        self.owned = object()
        self.assertRow(doctor.check_ports()[0], "ok", "listening on 10532")

    def test_docker_states(self):
        self.assertRow(doctor.check_docker(), "skip", "Not installed")
        self.paths["Docker"] = "WSL · /usr/bin/docker"
        self.assertRow(doctor.check_docker(), "ok", "WSL distribution")
        self.paths["find:docker"] = "/usr/bin/docker"
        self.assertRow(doctor.check_docker(), "ok", "Docker and Compose v2 answer.")
        self.problem = "Docker is installed but not running; start Docker Desktop, then retry."
        self.assertRow(doctor.check_docker(), "warn", "not usable", self.problem)

    def test_terminal_states(self):
        row = doctor.check_terminal()
        self.assertRow(row, "warn", "No terminal emulator", "Install xterm")
        self.found["konsole"] = "/usr/bin/konsole"
        self.assertRow(doctor.check_terminal(), "ok", "konsole is available.")

    def test_sandbox_matrix(self):
        row = doctor.check_sandbox()
        self.assertRow(row, "ok", "Unprivileged user namespaces are available.")
        for path, value in doctor.USERNS:
            self.files = {path: value}
            row = doctor.check_sandbox()
            self.assertRow(row, "warn", "has not been downloaded", "run Doctor again")
        helper = self.helper()
        root = SimpleNamespace(st_uid=0, st_mode=0o104755)
        user = SimpleNamespace(st_uid=1000, st_mode=0o100755)
        unset = SimpleNamespace(st_uid=0, st_mode=0o100755)
        with patch.object(doctor, "_stat", lambda path: root):
            self.assertRow(doctor.check_sandbox(), "ok", "setuid sandbox helper")
        for info in (user, unset, None):
            with patch.object(doctor, "_stat", lambda path: info):
                row = doctor.check_sandbox()
            self.assertRow(row, "fail", "helper is not set up", "chmod 4755")
            self.assertIn(f"sudo chown root:root {doctor._quote(helper)}", row["fix"])
        doctor.APPARMOR.mkdir()
        (doctor.APPARMOR / "other").write_text("profile other /opt/other/** {}")
        (doctor.APPARMOR / "nested").mkdir()
        with patch.object(doctor, "_stat", lambda path: user):
            with patch.object(doctor, "_read", self.read_file):
                self.assertEqual(doctor.check_sandbox()["status"], "fail")
                profile = f"profile workbench {helper.parent}/electron flags=(unconfined) {{}}"
                (doctor.APPARMOR / "agent-workbench").write_text(profile)
                self.assertRow(doctor.check_sandbox(), "ok", "AppArmor profile")
        self.files = {}
        self.assertEqual(doctor.check_sandbox()["status"], "ok")

    def read_file(self, path):
        if str(path) in self.files:
            return self.files[str(path)]
        try:
            return Path(path).read_text(encoding="utf8").strip()
        except OSError:
            return None

    def test_pip_hint_uses_the_lock_when_present(self):
        def words(hint):
            self.assertTrue(hint.startswith("Run: "), hint)
            return shlex.split(hint[len("Run: "):])

        install = [sys.executable, "-m", "pip", "install"]
        plain = doctor._pip()
        self.assertEqual(words(plain), install + ["-r", str(self.root / "requirements.txt")])
        lock = self.root / "requirements.lock"
        lock.mkdir()
        self.assertEqual(doctor._pip(), plain)
        lock.rmdir()
        lock.write_text("requests==2.32.5 --hash=sha256:0\n", encoding="utf8")
        locked = doctor._pip()
        self.assertEqual(words(locked), install + ["--require-hashes", "--only-binary", ":all:", "-r", str(lock)])
        self.assertIn(" --require-hashes --only-binary :all: -r ", locked)
        with patch.object(doctor.importlib.util, "find_spec", lambda name: None):
            self.assertEqual(doctor.check_packages()["fix"], locked)
        with patch.object(doctor, "_module", side_effect=ImportError("keyring")):
            self.assertEqual(doctor.check_vault()["fix"], locked)
        self.system("windows")
        start = f'Run in Command Prompt: "{sys.executable}" -m pip install '
        self.assertEqual(doctor._pip(), f'{start}--require-hashes --only-binary :all: -r "{lock}"')
        lock.unlink()
        self.assertEqual(doctor._pip(), f'{start}-r "{self.root / "requirements.txt"}"')

    def test_sandbox_fix_names_the_setup_script(self):
        self.files[RESTRICTED] = "1"
        helper = self.helper()
        script = self.root / "setup-linux.sh"
        user = SimpleNamespace(st_uid=1000, st_mode=0o100755)
        with patch.object(doctor, "_stat", lambda path: user), patch.object(doctor, "_read", self.read_file):
            self.assertRow(doctor.check_sandbox(), "fail", "helper is not set up", "chmod 4755")
            script.write_text('#!/bin/sh\nexec sh ./workbench.sh "$@"\n', encoding="utf8")
            row = doctor.check_sandbox()
            self.assertRow(row, "fail", "helper is not set up", f"sudo chown root:root {doctor._quote(helper)}")
            self.assertNotIn("setup-linux.sh", row["fix"])
            script.write_text('#!/bin/sh\nif [ "${1:-}" = "--sandbox" ]; then sandbox; fi\n', encoding="utf8")
            row = doctor.check_sandbox()
            self.assertRow(row, "fail", "helper is not set up")
            self.assertEqual(
                row["fix"],
                f"Run: WORKBENCH_DATA={doctor._quote(self.data)} sh {doctor._quote(script)} --sandbox",
            )
            local = self.root / ".local"
            self.helper(local)
            with patch.object(doctor, "DATA", local):
                row = doctor.check_sandbox()
            self.assertEqual(row["fix"], f"Run: sh {doctor._quote(script)} --sandbox")
            self.assertEqual(shlex.split(row["fix"][len("Run: "):]), ["sh", str(script), "--sandbox"])
            odd = self.tmp / "my $app"
            odd.mkdir()
            (odd / "setup-linux.sh").write_text("--sandbox", encoding="utf8")
            with patch.object(doctor, "ROOT", odd):
                words = shlex.split(doctor.check_sandbox()["fix"][len("Run: "):])
            self.assertEqual(
                words,
                [f"WORKBENCH_DATA={self.data}", "sh", str(odd / "setup-linux.sh"), "--sandbox"],
            )
            shutil.rmtree(self.data / "desktop-runtime")
            dev = self.root / "desktop/node_modules/electron/dist/chrome-sandbox"
            dev.parent.mkdir(parents=True)
            dev.write_text("")
            row = doctor.check_sandbox()
            self.assertRow(row, "fail", "helper is not set up", f"sudo chown root:root {doctor._quote(dev)}")
            self.assertNotIn("setup-linux.sh", row["fix"])

    @unittest.skipUnless(os.name == "posix", "the fix command is run with a POSIX shell")
    def test_sandbox_fix_reaches_the_data_folder_doctor_checked(self):
        if "--sandbox" not in (SETUP_LINUX.read_text(encoding="utf8") if SETUP_LINUX.is_file() else ""):
            self.skipTest("this checkout has no setup-linux.sh with a sandbox step")
        shutil.copy(SETUP_LINUX, self.root / "setup-linux.sh")
        proc = self.tmp / "proc"
        (proc / "sys/kernel").mkdir(parents=True)
        (proc / "sys/kernel/unprivileged_userns_clone").write_text("0\n", encoding="utf8")
        userns = tuple((str(proc) + path[len("/proc"):], value) for path, value in doctor.USERNS)
        data = self.tmp / "my $data"
        helper = self.helper(data)
        with patch.object(doctor, "USERNS", userns), patch.object(doctor, "DATA", data):
            with patch.object(doctor, "_read", self.read_file):
                row = doctor.check_sandbox()
        self.assertRow(row, "fail", "helper is not set up", "Run: WORKBENCH_DATA=")
        done = subprocess.run(
            ["/bin/sh", "-c", row["fix"][len("Run: "):]],
            env={"PATH": "/usr/bin:/bin", "WORKBENCH_PROC_ROOT": str(proc)},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
        )
        said = done.stdout.decode("utf8", "replace")
        self.assertIn(str(helper), said)
        self.assertNotIn("has not been downloaded", said)

    def test_wildcard_profile_counts_as_set_up(self):
        self.files[RESTRICTED] = "1"
        doctor.APPARMOR.mkdir()
        profile = doctor.APPARMOR / "agent-workbench-31987500"
        user = SimpleNamespace(st_uid=1000, st_mode=0o100755)
        with patch.object(doctor, "_stat", lambda path: user), patch.object(doctor, "_read", self.read_file):
            profile.write_text(
                "profile agent-workbench /srv/other/data/desktop-runtime/linux-*/electron flags=(unconfined) {\n"
                f"  # {self.data}/desktop-runtime-old/electron\n  userns,\n}}\n",
                encoding="utf8",
            )
            self.assertRow(doctor.check_sandbox(), "warn", "has not been downloaded")
            profile.write_text(
                f"profile agent-workbench {self.data}/desktop-runtime/linux-*/electron flags=(unconfined) {{\n"
                "  userns,\n}\n",
                encoding="utf8",
            )
            self.assertRow(doctor.check_sandbox(), "ok", "AppArmor profile")
            self.assertFalse((self.data / "desktop-runtime").exists())
            self.helper()
            self.assertRow(doctor.check_sandbox(), "ok", "AppArmor profile")
            profile.unlink()
            self.assertRow(doctor.check_sandbox(), "fail", "helper is not set up", "chmod 4755")

    def test_macos_checks(self):
        self.system("darwin")
        self.answers["xattr"] = (0, "0081;65a1b2c3;Safari;")
        row = doctor.check_quarantine()
        self.assertRow(row, "fail", f"quarantine is set on: {self.root}", "xattr -dr")
        self.assertIn(f'"{self.root}"', row["fix"])
        probes = [call for call in self.calls if call[0] == "/usr/bin/xattr"]
        self.assertEqual(probes[0][1:], ["-p", "com.apple.quarantine", str(self.root)])
        self.answers["xattr"] = (1, "No such xattr: com.apple.quarantine")
        self.assertRow(doctor.check_quarantine(), "ok", "No quarantine attribute")

        runtime = self.data / "desktop-runtime" / "darwin-41.0.0"
        runtime.mkdir(parents=True)
        self.paths[ENGINE] = str(self.data / "runtime" / "llama-server")

        def inside(argv):
            return (0 if argv[-1].startswith(str(self.data)) else 1, "")

        self.answers["xattr"] = inside
        row = doctor.check_quarantine()
        self.assertRow(row, "fail", str(runtime), f'quarantine "{self.data}" and start')
        self.assertIn(self.paths[ENGINE], row["detail"])
        self.assertNotIn(str(self.root), row["fix"])
        with patch.object(doctor, "DATA", self.root / ".local"):
            self.answers["xattr"] = (0, "")
            fix = doctor.check_quarantine()["fix"]
        self.assertIn(f'"{self.root}"', fix)
        self.assertIn(f'"{Path(sys.executable).resolve()}"', fix)
        self.assertIn(f'"{self.paths[ENGINE]}"', fix)

        del self.calls[:]
        row = doctor.check_automation()
        self.assertRow(row, "skip", "does not trigger that prompt", "Automation")
        self.assertEqual(self.calls, [])
        self.answers["pgrep"] = (1, "")
        row = doctor.check_automation(True)
        self.assertRow(row, "skip", "Terminal is not running", "Open Terminal")
        self.assertFalse(any("osascript" in call[0] for call in self.calls))
        self.answers["pgrep"] = (0, "512")
        self.answers["osascript"] = (1, DENIED)
        row = doctor.check_automation(True)
        self.assertRow(row, "fail", "blocked control of Terminal", "Automation")
        self.assertEqual(self.calls[-1][0], "/usr/bin/osascript")
        self.answers["osascript"] = (None, "")
        self.assertRow(doctor.check_automation(True), "warn", "waiting", "Choose Allow")
        self.answers["osascript"] = (1, "execution error: Terminal got an error (-600)")
        row = doctor.check_automation(True)
        self.assertRow(row, "warn", "did not answer: execution error", "Open Terminal")
        self.answers["osascript"] = (0, "2")
        self.assertRow(doctor.check_automation(True), "ok", "may control Terminal")
        rows = doctor.run(["automation"], True)
        self.assertEqual([row["status"] for row in rows], ["ok"])
        self.assertEqual(doctor.run(["automation"])[0]["status"], "skip")

    def test_ssh_and_proxy(self):
        row = doctor.check_ssh()
        self.assertRow(row, "warn", "ssh was not found", "openssh-client")
        self.system("windows")
        self.assertRow(doctor.check_ssh(), "warn", "ssh was not found", "OpenSSH Client")
        self.system("darwin")
        self.found["ssh"] = "/usr/bin/ssh"
        self.assertRow(doctor.check_ssh(), "warn", "ssh-keygen", "xcode-select")
        self.found["ssh-keygen"] = "/usr/bin/ssh-keygen"
        self.answers["ssh"] = (0, "OpenSSH_10.0p2, LibreSSL 3.3.6")
        self.assertRow(doctor.check_ssh(), "ok", "OpenSSH_10.0p2")
        self.assertEqual(self.calls[-1], ["/usr/bin/ssh", "-V"])
        self.answers["ssh"] = (None, "")
        self.assertRow(doctor.check_ssh(), "ok", "ssh is available.")

        self.assertRow(doctor.check_proxy(), "ok", "No proxy is configured.")
        os.environ["HTTP_PROXY"] = "http://proxy.example:8080"
        self.assertRow(doctor.check_proxy(), "warn", "not excluded", "NO_PROXY")
        os.environ["NO_PROXY"] = "localhost"
        self.assertEqual(doctor.check_proxy()["status"], "warn")
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        self.assertRow(doctor.check_proxy(), "ok", "bypass the proxy")
        os.environ["NO_PROXY"] = "*"
        self.assertEqual(doctor.check_proxy()["status"], "ok")
        del os.environ["HTTP_PROXY"], os.environ["NO_PROXY"]
        os.environ["all_proxy"] = "socks5://proxy.example:1080"
        self.assertEqual(doctor.check_proxy()["status"], "warn")

    def test_platform_only_checks_skip_elsewhere(self):
        self.system("windows")
        self.files[RESTRICTED] = "1"
        self.answers["xattr"] = (0, "")
        rows = doctor.run(["terminal", "sandbox", "quarantine", "automation"], True)
        self.assertEqual(
            [row["id"] for row in rows],
            ["terminal", "sandbox", "quarantine", "automation"],
        )
        for row in rows:
            self.assertRow(row, "skip", "Not needed on this system.")
            self.assertEqual(row["fix"], "")
        self.assertEqual(self.calls, [])
        self.system("linux")
        rows = {row["id"]: row for row in doctor.run(["quarantine", "automation"])}
        self.assertEqual({row["status"] for row in rows.values()}, {"skip"})
        self.system("darwin")
        rows = {row["id"]: row for row in doctor.run(["terminal", "sandbox"])}
        self.assertEqual({row["status"] for row in rows.values()}, {"skip"})

    def test_crashing_check_becomes_warn(self):
        def boom():
            raise RuntimeError("no such thing")

        checks = (("python", doctor.check_python), ("engine", boom), ("proxy", doctor.check_proxy))
        with patch.object(doctor, "CHECKS", checks):
            rows = doctor.run()
        self.assertEqual([row["id"] for row in rows], ["python", "engine", "proxy"])
        self.assertRow(rows[1], "warn", "RuntimeError: no such thing", "Doctor output")
        self.assertEqual(rows[2]["status"], "ok")
        self.assertContract(rows)
        self.tooling.inventory.side_effect = AttributeError("inventory")
        rows = doctor.run(["clis", "ssh"])
        self.assertEqual([row["id"] for row in rows], ["clis", "ssh"])
        self.assertRow(rows[0], "warn", "could not run (AttributeError")

    def test_unknown_id_and_exit_codes(self):
        with self.assertRaises(ValueError) as caught:
            doctor.run(["python", "nope"])
        self.assertIn("Unknown check: nope. Choose from: python, packages", str(caught.exception))
        with self.assertRaises(ValueError):
            doctor.run([{"id": "python"}])
        self.assertEqual(doctor.run([]), [])

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = doctor.main(["--json", "python"])
        rows = json.loads(out.getvalue())
        self.assertEqual((code, [row["id"] for row in rows]), (0, ["python"]))
        self.assertContract(rows)

        failing = (("python", lambda: doctor.row("python", "Python", "fail", "Too old.", "Upgrade.")),)
        out = io.StringIO()
        with patch.object(doctor, "CHECKS", failing), contextlib.redirect_stdout(out):
            code = doctor.main([])
        self.assertEqual(code, 1)
        self.assertEqual(
            out.getvalue(),
            "fail  Python               Too old.\n"
            "      fix: Upgrade.\n"
            "\n"
            "0 ok, 0 warnings, 1 failed, 0 skipped\n",
        )

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = doctor.main(["nope"])
        self.assertEqual((code, out.getvalue()), (2, ""))
        self.assertIn("Unknown check: nope.", err.getvalue())

    def test_counts_and_render(self):
        rows = [
            doctor.row("a", "Alpha", "ok", "Fine."),
            doctor.row("b", "Beta", "warn", "Hm.", "Do this."),
            doctor.row("c", "Gamma", "skip", "Not needed on this system."),
        ]
        self.assertEqual(doctor.counts(rows), dict(ok=1, warn=1, fail=0, skip=1))
        self.assertEqual(
            doctor.render(rows).splitlines(),
            [
                "ok    Alpha                Fine.",
                "warn  Beta                 Hm.",
                "      fix: Do this.",
                "skip  Gamma                Not needed on this system.",
                "",
                "1 ok, 1 warnings, 0 failed, 1 skipped",
            ],
        )

    def test_module_keeps_to_the_standard_library_and_closes_stdin(self):
        tree = ast.parse(Path(doctor.__file__).read_text(encoding="utf8"))
        names = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add(node.module.split(".")[0])
        self.assertTrue(names)
        self.assertEqual(names - set(sys.stdlib_module_names), set())
        spawns = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.func.attr in ("run", "Popen", "call", "check_call", "check_output")
        ]
        self.assertEqual(len(spawns), 1)
        self.assertIn("stdin", [keyword.arg for keyword in spawns[0].keywords])


class SeamTests(unittest.TestCase):
    def test_run_closes_stdin_and_survives_failures(self):
        done = SimpleNamespace(returncode=3, stdout=b" one\x00\n two \n")
        with patch.object(doctor.subprocess, "run", return_value=done) as run:
            self.assertEqual(doctor._run(["tool", "--version"], 7), (3, "one\n two"))
        self.assertEqual(run.call_args.args, (["tool", "--version"],))
        self.assertIs(run.call_args.kwargs["stdin"], doctor.subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["timeout"], 7)
        expired = doctor.subprocess.TimeoutExpired(["tool"], 7)
        for error in (expired, FileNotFoundError("tool"), PermissionError("tool")):
            with patch.object(doctor.subprocess, "run", side_effect=error):
                self.assertEqual(doctor._run(["tool"]), (None, ""))

    def test_small_helpers(self):
        self.assertEqual(doctor.first("\n\n  one \ntwo"), "one")
        self.assertEqual(doctor.first(""), "")
        self.assertIs(doctor._module("json"), json)
        with self.assertRaises(ImportError):
            doctor._module("awb_no_such_module")
        self.assertFalse(doctor.IMPORTS.locked())
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "note"
            note.write_bytes(b" 1\n")
            self.assertEqual(doctor._read(note), "1")
            self.assertIsNone(doctor._read(Path(tmp) / "missing"))
            self.assertIsNone(doctor._stat(Path(tmp) / "missing"))
            self.assertEqual(doctor._stat(note).st_size, 3)
        with patch.object(doctor.socket, "create_connection", side_effect=OSError):
            self.assertFalse(doctor._listening(10532))
        with patch.object(doctor.socket, "create_connection") as connect:
            self.assertTrue(doctor._listening(10532))
        self.assertEqual(connect.call_args.args, (("127.0.0.1", 10532),))
        opener = Mock()
        opener.open.side_effect = OSError("refused")
        with patch.object(doctor.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(doctor._local_text("http://127.0.0.1:3002/"), "")
        self.assertEqual(opener.open.call_args.args, ("http://127.0.0.1:3002/",))
        with patch.object(doctor.urllib.request, "build_opener") as build:
            reply = build.return_value.open.return_value.__enter__.return_value
            reply.read1.side_effect = [b'{"message":', b'"Firecrawl API"}', b""]
            self.assertIn("Firecrawl", doctor._local_text("http://127.0.0.1:3002/"))
        self.assertEqual(build.call_args.args[0].proxies, {})

    def serve(self, reply):
        server, port = listener(reply)
        self.addCleanup(server.close)
        return f"http://127.0.0.1:{port}/"

    def test_local_text_reads_a_page_and_ignores_proxies(self):
        seen = []

        def page(peer):
            seen.append(peer.recv(4096))
            peer.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 27\r\n\r\n" b'{"message":"Firecrawl API"}')

        url = self.serve(page)
        with patch.dict(os.environ, HTTP_PROXY="http://127.0.0.1:9", http_proxy="http://127.0.0.1:9"):
            self.assertEqual(doctor._local_text(url), '{"message":"Firecrawl API"}')
        self.assertTrue(seen[0].startswith(b"GET / HTTP/1.1"))

    def test_local_text_does_not_follow_a_redirect(self):
        hits = []

        def page(peer):
            hits.append(peer.recv(4096))
            peer.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 9\r\n\r\nFirecrawl")

        elsewhere = self.serve(page)
        for status in (b"301 Moved Permanently", b"302 Found", b"307 Temporary Redirect"):

            def moved(peer, status=status):
                peer.recv(4096)
                head = b"HTTP/1.0 " + status + b"\r\nLocation: " + elsewhere.encode()
                peer.sendall(head + b"\r\nContent-Length: 0\r\n\r\n")

            self.assertEqual(doctor._local_text(self.serve(moved)), "")
        self.assertEqual(hits, [])

    def test_local_text_survives_a_listener_that_is_not_http(self):
        def banner(peer):
            peer.recv(4096)
            peer.sendall(b"SSH-2.0-OpenSSH_9.9\r\n")

        self.assertEqual(doctor._local_text(self.serve(banner)), "")

        def short(peer):
            peer.recv(4096)
            peer.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\n")

        self.assertEqual(doctor._local_text(self.serve(short)), "")

    def test_local_text_stops_reading_a_stream_that_never_ends(self):
        def drip(peer):
            peer.recv(4096)
            peer.sendall(b"HTTP/1.0 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
            for _ in range(100):
                peer.sendall(b": Firecrawl ping\n")
                time.sleep(0.05)

        url = self.serve(drip)
        began = time.monotonic()
        with patch.object(doctor, "LOCAL_TIMEOUT", 0.3):
            text = doctor._local_text(url)
        self.assertLess(time.monotonic() - began, 2)
        self.assertTrue(text.startswith(": Firecrawl ping"), text)

        def stall(peer):
            peer.recv(4096)
            peer.sendall(b"HTTP/1.0 200 OK\r\n\r\nFirecrawl")
            time.sleep(1)

        began = time.monotonic()
        with patch.object(doctor, "LOCAL_TIMEOUT", 0.2):
            self.assertEqual(doctor._local_text(self.serve(stall)), "Firecrawl")
        self.assertLess(time.monotonic() - began, 0.9)


if __name__ == "__main__":
    unittest.main()
