"""Tool catalog tests: PATH helpers, planner, runner and typed outcomes, plus the web tools, the plugin
measurement helpers and the setup files. Offline, no installer is started."""

import ast
import asyncio
import atexit
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

if "runtime" not in sys.modules and "WORKBENCH_DATA" not in os.environ:
    os.environ["WORKBENCH_DATA"] = tempfile.mkdtemp(prefix="awb-test-")
    atexit.register(shutil.rmtree, os.environ["WORKBENCH_DATA"], ignore_errors=True)

import psutil
import requests
import yaml

import plugin_cost
import tool_load
import tooling
import web_tools
from tooling import Host, plan

HOME = str(Path.home())
SEP = os.pathsep
WINDOWS = Host("windows", frozenset({"winget", "npm", "wsl"}), home="C:\\Users\\me")
MAC_BREW = Host("darwin", frozenset({"brew", "npm", "curl", "uv"}), home="/Users/me")
MAC_BARE = Host("darwin", frozenset({"curl"}), home="/Users/me")
APT = Host(
    "linux",
    frozenset({"apt-get", "sudo", "curl", "npm"}),
    apt_docker=("docker.io", "docker-compose-v2"),
    home="/home/me",
)
DNF = Host("linux", frozenset({"dnf", "sudo", "curl"}), home="/home/me")
BARE = Host("linux", home="/home/me")
HOSTS = (WINDOWS, MAC_BREW, MAC_BARE, APT, DNF, BARE)
INSTALLED = dict(path="/opt/x/bin/tool", hint="", on_path=True)


def child(code):
    return [sys.executable, "-c", code]


def fake_boxes(**members):
    return patch.dict(sys.modules, {"boxes": types.SimpleNamespace(IMAGE="sandbox:1", **members)})


class PathTests(unittest.TestCase):
    def test_merge_path_appends_missing_only(self):
        self.assertEqual(
            tooling.merge_path("/usr/bin:/bin", ["/bin/", "/opt/x", "/opt/x"], ":", False),
            ("/usr/bin:/bin:/opt/x", ["/opt/x"]),
        )
        self.assertEqual(
            tooling.merge_path("C:\\Windows;C:\\Tools", ["c:\\tools\\"], ";", True),
            ("C:\\Windows;C:\\Tools", []),
        )
        self.assertEqual(
            tooling.merge_path("C:\\Windows;C:\\Tools", ["c:\\tools\\"], ";", False)[1],
            ["c:\\tools\\"],
        )

    def test_expand_percent_is_case_insensitive(self):
        env = {"UserProfile": "C:\\Users\\me"}
        self.assertEqual(tooling.expand_percent("%USERPROFILE%\\bin", env), "C:\\Users\\me\\bin")
        self.assertEqual(tooling.expand_percent("%Missing%\\bin", env), "%Missing%\\bin")

    def test_candidate_dirs_per_system(self):
        with tempfile.TemporaryDirectory() as home:
            mac = tooling.candidate_dirs("darwin", {"NPM_CONFIG_PREFIX": "/opt/npm"}, home)
            for folder in (
                home + "/.local/bin",
                home + "/.bun/bin",
                home + "/.opencode/bin",
                "/opt/npm/bin",
                "/opt/homebrew/bin",
                home + "/.docker/bin",
            ):
                self.assertIn(folder, mac)
            linux = tooling.candidate_dirs("linux", {}, home)
            self.assertEqual(linux[-1], "/usr/local/bin")
            self.assertNotIn("/opt/homebrew/bin", linux)
            env = {"AppData": "C:\\Users\\me\\AppData\\Roaming", "LOCALAPPDATA": "C:\\Users\\me\\AppData\\Local"}
            win = tooling.candidate_dirs("windows", env, home, ["C:\\Tools;;%appdata%\\x", "D:\\bin"])
            self.assertEqual(
                win,
                [
                    "C:\\Tools",
                    "C:\\Users\\me\\AppData\\Roaming\\x",
                    "D:\\bin",
                    "C:\\Users\\me\\AppData\\Roaming\\npm",
                    "C:\\Users\\me\\AppData\\Local\\Microsoft\\WinGet\\Links",
                ],
            )
            self.assertEqual(tooling.candidate_dirs("windows", {}, home), [])
            self.assertEqual(tooling.candidate_dirs("windows", {"npm_config_prefix": "D:\\npm"}, home), ["D:\\npm"])

    def test_npmrc_prefix_reads_user_config(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(tooling.npmrc_prefix(home), "")
            rc = Path(home, ".npmrc")
            rc.write_text("registry=https://registry.npmjs.org/\n", encoding="utf8")
            self.assertEqual(tooling.npmrc_prefix(home), "")
            rc.write_text("; mine\n prefix = \"~/.npm-global\"\nprefix=/ignored\n", encoding="utf8")
            self.assertEqual(tooling.npmrc_prefix(home), home + "/.npm-global")
            rc.write_text("prefix=${HOME}/.npm\n", encoding="utf8")
            self.assertEqual(tooling.npmrc_prefix(home), home + "/.npm")
            rc.write_text("prefix=/big\n" + "#" * 70000, encoding="utf8")
            self.assertEqual(tooling.npmrc_prefix(home), "")

    def test_refresh_path_adds_existing_dirs_once(self):
        with (
            tempfile.TemporaryDirectory() as real,
            patch.dict(os.environ, {"PATH": "/usr/bin", tooling.ADDED_ENV: ""}),
            patch.object(tooling, "candidate_dirs", return_value=[real, real + "-missing"]),
            patch.object(tooling, "_registry_paths", return_value=[]),
        ):
            self.assertEqual(tooling.refresh_path(), [real])
            self.assertEqual(os.environ["PATH"], "/usr/bin" + SEP + real)
            self.assertEqual(tooling.refresh_path(), [])
            self.assertEqual(os.environ["PATH"], "/usr/bin" + SEP + real)

    def test_refresh_path_records_what_it_added(self):
        with (
            tempfile.TemporaryDirectory() as mine,
            tempfile.TemporaryDirectory() as theirs,
            patch.dict(os.environ, {"PATH": theirs, tooling.ADDED_ENV: ""}),
            patch.object(tooling, "candidate_dirs", return_value=[mine, theirs]),
            patch.object(tooling, "_registry_paths", return_value=[]),
        ):
            tooling.refresh_path()
            self.assertEqual(os.environ[tooling.ADDED_ENV], mine)
            self.assertEqual(tooling.user_dirs(), [theirs])

    def test_user_dirs_counts_a_folder_the_profile_added_too(self):
        folder = HOME + "/.local/bin"
        exe = folder + "/omp"
        inherited = {tooling.ADDED_ENV: folder, "SHELL": "/bin/zsh"}
        with (
            patch.object(tooling, "SYSTEM", "darwin"),
            patch.object(tooling, "find", return_value=exe),
            patch.object(tooling, "location", return_value=exe),
        ):
            with patch.dict(os.environ, dict(inherited, PATH=SEP.join(["/usr/bin", folder]))):
                self.assertEqual(tooling.user_dirs(), ["/usr/bin"])
                self.assertFalse(tooling.detect("OMP")["on_path"])
            with patch.dict(os.environ, dict(inherited, PATH=SEP.join([folder + "/", "/usr/bin", folder]))):
                self.assertEqual(tooling.user_dirs(), [folder + "/", "/usr/bin", folder])
                d = tooling.detect("OMP")
                self.assertEqual((d["on_path"], d["hint"]), (True, ""))

    def test_refresh_path_merges_the_windows_registry(self):
        seen = []

        def dirs(system, env, home, registry=()):
            seen.append(system)
            return tooling.stored_dirs(registry, env) + [guessed]

        with (
            tempfile.TemporaryDirectory() as stored,
            tempfile.TemporaryDirectory() as guessed,
            patch.dict(os.environ, {"PATH": "/usr/bin", tooling.ADDED_ENV: "", "AWB_STORED": stored}),
            patch.object(tooling, "SYSTEM", "windows"),
            patch.object(tooling, "_registry_paths", return_value=["%awb_stored%;" + stored.upper() + "/"]),
            patch.object(tooling, "candidate_dirs", side_effect=dirs),
        ):
            self.assertEqual(tooling.refresh_path(), [stored, guessed])
            self.assertEqual(os.environ["PATH"], SEP.join(["/usr/bin", stored, guessed]))
            self.assertEqual(os.environ[tooling.ADDED_ENV], guessed)
            self.assertEqual(tooling.user_dirs(), ["/usr/bin", stored])
            self.assertEqual(tooling.refresh_path(), [])
        self.assertEqual(seen, ["windows", "windows"])

    def test_registry_paths_read_both_hives(self):
        with patch.object(tooling, "SYSTEM", "linux"):
            self.assertEqual(tooling._registry_paths(), [])
        opened = []

        class Key:
            def __init__(self, hive):
                self.hive = hive

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def open_key(hive, key):
            opened.append((hive, key))
            if hive == "machine":
                raise OSError("denied")
            return Key(hive)

        winreg = types.SimpleNamespace(
            HKEY_CURRENT_USER="user",
            HKEY_LOCAL_MACHINE="machine",
            OpenKey=open_key,
            QueryValueEx=lambda handle, value: ("C:\\Mine;%USERPROFILE%\\bin", 2),
        )
        with patch.object(tooling, "SYSTEM", "windows"), patch.dict(sys.modules, {"winreg": winreg}):
            self.assertEqual(tooling._registry_paths(), ["C:\\Mine;%USERPROFILE%\\bin"])
        self.assertEqual([hive for hive, _ in opened], ["user", "machine"])
        self.assertEqual(opened[0][1], "Environment")

    def test_find_falls_back_to_known_dirs(self):
        name = "awb-fake-tool"
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder, name + (".cmd" if os.name == "nt" else ""))
            exe.write_text("", encoding="utf8")
            exe.chmod(0o755)
            with patch.dict(os.environ, {"PATH": os.path.dirname(sys.executable)}):
                with patch.object(tooling, "candidate_dirs", return_value=[folder, folder + "-missing"]):
                    self.assertEqual(os.path.normcase(tooling.find(name)), os.path.normcase(str(exe)))
                    self.assertEqual(tooling.find("no-such-tool"), "")
                    resolved = tooling.resolve([name, "--x"])
                    self.assertEqual([os.path.normcase(resolved[0]), *resolved[1:]], [os.path.normcase(str(exe)), "--x"])
                    self.assertEqual(tooling.resolve(["no-such-tool", "--x"]), ["no-such-tool", "--x"])
                with patch.object(tooling, "candidate_dirs", return_value=[]):
                    self.assertEqual(tooling.find(name), "")

    def test_path_hint_per_shell(self):
        hint = tooling.path_hint
        self.assertEqual(
            hint("/Users/me/.local/bin", "omp", "/bin/zsh", "darwin", "/Users/me"),
            "To use omp in your own terminals, run: echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> ~/.zshrc",
        )
        self.assertTrue(hint("/Users/me/.bun/bin", "bun", "/bin/bash", "darwin", "/Users/me").endswith(">> ~/.bash_profile"))
        self.assertTrue(hint("/home/me/.bun/bin", "bun", "/usr/bin/bash", "linux", "/home/me").endswith(">> ~/.bashrc"))
        self.assertTrue(hint("/home/me/.bun/bin", "bun", "", "linux", "/home/me").endswith(">> ~/.profile"))
        self.assertEqual(
            hint("/home/me/.local/bin", "pi", "/usr/bin/fish", "linux", "/home/me"),
            "To use pi in your own terminals, run: fish_add_path ~/.local/bin",
        )
        outside = hint("/opt/tools/bin", "gh", "/bin/zsh", "linux", "/home/me")
        self.assertIn('PATH=\"/opt/tools/bin:$PATH\"', outside)
        self.assertEqual(hint("C:\\Users\\me\\.local\\bin", "omp", "", "windows", "C:\\Users\\me"), "")
        self.assertEqual(hint("", "omp", "/bin/zsh", "darwin", "/Users/me"), "")

    def test_path_hint_never_offers_a_line_the_shell_would_reinterpret(self):
        hint = tooling.path_hint
        spaced = hint("/opt/my tools/bin", "pi", "/bin/zsh", "linux", "/home/me")
        self.assertTrue(spaced.endswith("run: echo 'export PATH=\"/opt/my tools/bin:$PATH\"' >> ~/.zshrc"))
        for folder, shell in (
            ("/opt/it's mine/bin", "/bin/zsh"),
            ("/opt/$(touch pwned)/bin", "/bin/bash"),
            ('/opt/a"b/bin', "/bin/bash"),
            ("/opt/a`id`/bin", ""),
            ("/opt/a;b/bin", "/usr/bin/fish"),
            ("/opt/my tools/bin", "/usr/bin/fish"),
            ("/home/me/it's/bin", "/bin/zsh"),
        ):
            text = hint(folder, "pi", shell, "linux", "/home/me")
            shown = "~" + folder[len("/home/me"):] if folder.startswith("/home/me/") else folder
            self.assertEqual(text, f"To use pi in your own terminals, add {shown} to the PATH in your shell profile.")
            self.assertNotIn("run:", text)


class DetectTests(unittest.TestCase):
    def test_detect_reports_off_path_tool_with_hint(self):
        folder = HOME + "/.local/bin"
        exe = folder + "/omp"
        with (
            patch.object(tooling, "SYSTEM", "darwin"),
            patch.object(tooling, "find", return_value=exe),
            patch.object(tooling, "location", return_value=exe),
        ):
            env = {"PATH": "/usr/bin" + SEP + folder, tooling.ADDED_ENV: folder, "SHELL": "/bin/zsh"}
            with patch.dict(os.environ, env):
                d = tooling.detect("OMP")
                self.assertEqual((d["name"], d["exe"], d["path"], d["version"]), ("OMP", "omp", exe, ""))
                self.assertTrue(d["installed"])
                self.assertFalse(d["on_path"])
                self.assertIn('PATH="$HOME/.local/bin:$PATH"', d["hint"])
                self.assertIn("~/.zshrc", d["hint"])
            with patch.dict(os.environ, {"PATH": "/usr/bin", tooling.ADDED_ENV: "", "SHELL": "/bin/zsh"}):
                self.assertFalse(tooling.detect("OMP")["on_path"])
            with patch.dict(os.environ, {"PATH": folder + "/" + SEP + "/usr/bin", tooling.ADDED_ENV: ""}):
                d = tooling.detect("OMP")
                self.assertTrue(d["on_path"])
                self.assertEqual(d["hint"], "")
        with patch.object(tooling, "find", return_value=""):
            with patch.object(tooling, "location", return_value="WSL · /usr/bin/docker"):
                d = tooling.detect("Docker", versions=True)
                self.assertEqual((d["installed"], d["on_path"], d["hint"], d["version"]), (True, True, "", ""))
            with patch.object(tooling, "location", side_effect=OSError("gone")):
                d = tooling.detect("Docker")
                self.assertEqual((d["installed"], d["path"], d["on_path"]), (False, "", True))

    def test_detect_reads_the_version_of_a_cli(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = str(Path(folder, "omp"))
            Path(exe).write_text("", encoding="utf8")
            with patch.object(tooling, "quiet", return_value=(0, "omp v19.1.0 (darwin)\n")) as quiet:
                self.assertEqual(tooling.version(exe), "19.1.0")
                self.assertEqual(quiet.call_args.args[0], [exe, "--version"])
                self.assertEqual(tooling.version(""), "")
                self.assertEqual(tooling.version("WSL · /usr/bin/docker"), "")
                self.assertEqual(tooling.version(exe + "-missing"), "")
                self.assertEqual(quiet.call_count, 1)
                with patch.object(tooling, "find", return_value=exe), patch.object(tooling, "location", return_value=exe):
                    self.assertEqual(tooling.detect("OMP", versions=True)["version"], "19.1.0")
            with patch.object(tooling, "quiet", return_value=(None, "")):
                self.assertEqual(tooling.version(exe), "")

    def test_inventory_refreshes_path_once(self):
        with (
            patch.object(tooling, "refresh_path") as refresh,
            patch.object(tooling, "detect", side_effect=lambda n, v: (n, v)) as detect,
        ):
            self.assertEqual(tooling.inventory(("OMP", "Pi"), versions=True), [("OMP", True), ("Pi", True)])
            self.assertEqual(len(tooling.inventory()), len(tooling.TOOLS))
        self.assertEqual(refresh.call_count, 2)
        self.assertEqual(detect.call_count, 2 + len(tooling.TOOLS))

    def test_quiet_closes_stdin_and_survives_a_missing_program(self):
        code, text = tooling.quiet(child("import sys; print(sys.stdin.read() == ''); sys.exit(4)"), 20)
        self.assertEqual((code, text.strip()), (4, "True"))
        self.assertEqual(tooling.quiet([str(Path(tempfile.gettempdir(), "awb-no-such-program"))]), (None, ""))

    def test_quiet_timeout_stops_the_whole_tree(self):
        proc = MagicMock(pid=4242)
        proc.communicate.side_effect = [subprocess.TimeoutExpired("pi.cmd", 1), subprocess.TimeoutExpired("pi.cmd", 5)]
        with (
            patch.object(tooling.subprocess, "Popen", return_value=proc) as popen,
            patch.object(tooling, "kill_tree") as kill,
        ):
            self.assertEqual(tooling.quiet(["pi.cmd", "--version"], 1), (None, ""))
        kill.assert_called_once_with(4242)
        self.assertEqual([c.kwargs for c in proc.communicate.call_args_list], [{"timeout": 1}, {"timeout": 5}])
        self.assertIs(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(popen.call_args.kwargs["stderr"], subprocess.STDOUT)

    @unittest.skipUnless(os.name == "posix", "uses sh to start a background child")
    def test_quiet_timeout_leaves_no_grandchild_running(self):
        with tempfile.TemporaryDirectory() as folder:
            note = Path(folder, "pid")
            began = time.monotonic()
            script = f"sleep 30 & echo $! > {shlex.quote(str(note))}; wait"
            self.assertEqual(tooling.quiet(["sh", "-c", script], 0.3), (None, ""))
            self.assertLess(time.monotonic() - began, 2)
            pid = int(note.read_text(encoding="utf8"))
            deadline = time.monotonic() + 1
            while psutil.pid_exists(pid) and time.monotonic() < deadline:
                try:
                    if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                        break
                except psutil.Error:
                    break
                time.sleep(0.02)
            else:
                self.assertFalse(psutil.pid_exists(pid), "the probe's background child outlived the timeout")

    def test_wsl_probe_uses_exec_and_survives_timeout(self):
        calls = []

        def runner(codes):
            def run(argv, **kwargs):
                calls.append((argv, kwargs))
                code = codes.pop(0)
                if isinstance(code, Exception):
                    raise code
                return types.SimpleNamespace(returncode=code)

            return run

        self.assertEqual(tooling.wsl_probe("Docker", runner([0])), "WSL · /usr/bin/docker")
        argv, kwargs = calls[0]
        self.assertEqual(argv, ["wsl", "-d", "AgentWorkbench-Web", "-u", "root", "--exec", "test", "-x", "/usr/bin/docker"])
        self.assertNotIn("--", argv)
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["timeout"], 10)
        calls.clear()
        self.assertEqual(tooling.wsl_probe("vLLM", runner([subprocess.TimeoutExpired("wsl", 10), 0])), "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(tooling.wsl_probe("vLLM", runner([OSError("no wsl")])), "")
        calls.clear()
        self.assertEqual(
            tooling.wsl_probe("vLLM", runner([1, 0, 0])),
            "WSL · /root/.local/share/agent-workbench/vllm/bin/vllm",
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(tooling.wsl_probe("vLLM", runner([1, 1, 1])), "")

    def test_location_takes_the_wsl_route_only_on_windows(self):
        with (
            patch.object(tooling, "find", return_value=""),
            patch.object(tooling.shutil, "which", return_value="C:\\Windows\\System32\\wsl.exe"),
            patch.object(tooling, "wsl_probe", return_value="WSL · /usr/bin/docker") as probe,
        ):
            with patch.object(tooling.os, "name", "nt"):
                self.assertEqual(tooling.location("Docker"), "WSL · /usr/bin/docker")
                self.assertEqual(tooling.docker_problem(), "")
                probe.return_value = ""
                self.assertEqual(tooling.location("vLLM"), "")
                self.assertIn("not installed", tooling.docker_problem())
            probe.reset_mock()
            with patch.object(tooling.os, "name", "posix"), patch.object(tooling, "VLLM_LOCAL", ()):
                self.assertEqual(tooling.location("Docker"), "")
                self.assertEqual(tooling.location("vLLM"), "")
            probe.assert_not_called()

    def test_sandbox_image_location(self):
        with patch.object(tooling, "find", return_value="/usr/bin/docker"):
            with fake_boxes(image_present=lambda: True):
                self.assertEqual(tooling.location("Sandbox image"), "sandbox:1")
            with fake_boxes(image_present=lambda: False):
                self.assertEqual(tooling.location("Sandbox image"), "")
            for failure in (
                ValueError("Install Docker through Setup first."),
                OSError("docker"),
                subprocess.TimeoutExpired("docker", 20),
            ):
                with fake_boxes(image_present=MagicMock(side_effect=failure)):
                    self.assertEqual(tooling.location("Sandbox image"), "")
            with (
                fake_boxes(image_present=lambda: True),
                patch.object(tooling.subprocess, "run", side_effect=AssertionError("tooling started Docker itself")),
            ):
                self.assertEqual(tooling.detect("Sandbox image")["path"], "sandbox:1")


class HostTests(unittest.TestCase):
    def test_apt_docker_packages_and_parser(self):
        def candidates(**known):
            return lambda package: known.get(package.replace("-", "_"), "")

        pick = tooling.apt_docker_packages
        self.assertEqual(
            pick(candidates(docker_ce="5:27.3.1", docker_compose_plugin="2.29.7", docker_compose_v2="2.24")),
            ("docker-ce", "docker-ce-cli", "containerd.io", "docker-compose-plugin"),
        )
        self.assertEqual(pick(candidates(docker_compose_v2="2.24.6", docker_compose="1.29.2")), ("docker.io", "docker-compose-v2"))
        self.assertEqual(pick(candidates(docker_compose="1:2.26.1-1")), ("docker.io", "docker-compose"))
        self.assertEqual(pick(candidates(docker_compose="1.29.2-3", docker_ce="5:27")), ())
        parse = tooling.parse_apt_candidate
        self.assertEqual(parse("docker-compose-v2:\n  Installed: (none)\n  Candidate: 2.24.6+ds1-0ubuntu2\n"), "2.24.6+ds1-0ubuntu2")
        self.assertEqual(parse("docker-ce:\n  Installed: (none)\n  Candidate: (none)\n"), "")
        self.assertEqual(parse(""), "")
        with patch.object(tooling, "quiet", return_value=(0, "x:\n  Candidate: 1:2.26.1-1\n")) as quiet:
            self.assertEqual(tooling.apt_candidate("docker-compose"), "1:2.26.1-1")
        self.assertEqual(quiet.call_args.args[0], ["apt-cache", "policy", "docker-compose"])
        self.assertEqual(quiet.call_args.args[2]["LC_ALL"], "C")

    def test_npm_prefix_decides_where_global_installs_go(self):
        with tempfile.TemporaryDirectory() as prefix:
            self.assertTrue(tooling.prefix_writable(prefix))
            self.assertTrue(tooling.prefix_writable(prefix + "/not/made/yet"))
            with patch.object(tooling, "quiet", return_value=(0, "npm warn old config\n" + prefix + "\n\n")) as quiet:
                self.assertFalse(tooling.npm_user_prefix("/usr/bin/npm"))
                self.assertEqual(quiet.call_args.args[0], ["/usr/bin/npm", "config", "get", "prefix"])
                with patch.object(tooling.os, "access", return_value=False):
                    self.assertFalse(tooling.prefix_writable(prefix))
                    self.assertTrue(tooling.npm_user_prefix("/usr/bin/npm"))
        for answer in ((1, "/usr"), (None, ""), (0, "\n")):
            with patch.object(tooling, "quiet", return_value=answer):
                self.assertTrue(tooling.npm_user_prefix("npm"))

    def test_host_facts_computes_slow_facts_only_when_needed(self):
        present = {"npm": "/usr/bin/npm", "apt-get": "/usr/bin/apt-get", "sudo": "/usr/bin/sudo"}
        with (
            patch.object(tooling, "SYSTEM", "linux"),
            patch.object(tooling, "find", side_effect=lambda exe: present.get(exe, "")),
            patch.object(tooling, "npm_user_prefix", return_value=True) as npm,
            patch.object(tooling, "apt_candidate", side_effect=lambda p: "2.24" if p == "docker-compose-v2" else "") as apt,
        ):
            host = tooling.host_facts("Hermes")
            self.assertEqual(host.system, "linux")
            self.assertEqual(host.tools, frozenset(present))
            self.assertEqual((host.npm_user_prefix, host.apt_docker, host.home), (False, (), HOME))
            npm.assert_not_called()
            apt.assert_not_called()
            self.assertTrue(tooling.host_facts("Pi").npm_user_prefix)
            npm.assert_called_once_with("/usr/bin/npm")
            self.assertEqual(tooling.host_facts("Docker").apt_docker, ("docker.io", "docker-compose-v2"))
            with patch.object(tooling, "SYSTEM", "windows"):
                host = tooling.host_facts("Pi")
                self.assertEqual((host.system, host.npm_user_prefix), ("windows", False))
            self.assertEqual(npm.call_count, 1)

    def test_docker_problem_sentences(self):
        def answers(info, compose=(0, "v2.29")):
            return lambda argv, timeout=5, env=None: info if argv[1] == "info" else compose

        with patch.object(tooling, "find", return_value=""):
            self.assertEqual(tooling.docker_problem(), "Docker is not installed; install it in Advanced > Setup > Docker.")
        with patch.object(tooling, "find", return_value="/usr/bin/docker"):
            denied = (1, "permission denied while trying to connect to the Docker daemon socket")
            with patch.object(tooling, "quiet", side_effect=answers(denied)):
                self.assertIn("usermod -aG docker", tooling.docker_problem())
            for down in ((1, "Cannot connect to the Docker daemon"), (None, "")):
                with patch.object(tooling, "quiet", side_effect=answers(down)):
                    self.assertTrue(tooling.docker_problem().startswith("Docker is installed but not running;"))
            with (
                patch.object(tooling, "quiet", side_effect=answers((1, "Cannot connect to the Docker daemon"))),
                patch.object(tooling.os, "name", "nt"),
                patch.object(tooling.shutil, "which", return_value="C:/Windows/System32/wsl.exe"),
                patch.object(tooling, "wsl_probe", return_value="WSL · /usr/bin/docker"),
            ):
                self.assertEqual(tooling.docker_problem(), "")
            with patch.object(tooling, "quiet", side_effect=answers((0, "27.3.1"), (1, "docker: 'compose' is not a docker command"))):
                self.assertTrue(tooling.docker_problem().startswith("Docker Compose v2 is missing;"))
            with patch.object(tooling, "quiet", side_effect=answers((0, "27.3.1"))) as quiet:
                self.assertEqual(tooling.docker_problem(), "")
            self.assertEqual(quiet.call_args_list[0].args[0][:2], ["/usr/bin/docker", "info"])
            self.assertEqual(quiet.call_args_list[1].args[0], ["/usr/bin/docker", "compose", "version"])


class PlanTests(unittest.TestCase):
    def test_opencode_is_in_the_catalog(self):
        for table in (tooling.TOOLS, tooling.LOGIN, tooling.NPM, tooling.AGENTS):
            self.assertIn("opencode", table)
        names = list(tooling.TOOLS)
        self.assertEqual(names.index("opencode"), names.index("Codex") + 1)
        self.assertEqual(tooling.LOGIN["opencode"], ["opencode", "auth", "login"])
        self.assertEqual(tooling.NPM["opencode"], "opencode-ai@1")
        self.assertEqual(plan("opencode", "Update", APT)["argv"], ["npm", "install", "-g", "opencode-ai@1"])
        self.assertTrue(set(tooling.AGENTS) <= set(tooling.TOOLS))

    def test_plan_omp_windows_bypasses_execution_policy(self):
        install = plan("OMP", "Install", WINDOWS)
        self.assertEqual(install["kind"], "run")
        argv = install["argv"]
        self.assertEqual(argv[: len(tooling.POWERSHELL)], tooling.POWERSHELL)
        self.assertLess(argv.index("Bypass"), argv.index("-Command"))
        self.assertEqual(argv[argv.index("Bypass") - 1], "-ExecutionPolicy")
        self.assertIn("-NonInteractive", argv)
        self.assertIn("-Ref v19.1.0", argv[-1])
        self.assertIn("-UseBasicParsing", argv[-1])
        self.assertIn(tooling.OMP_SOURCE + ".ps1", argv[-1])
        self.assertNotIn("-Ref", plan("OMP", "Update", WINDOWS)["argv"][-1])
        posix = plan("OMP", "Install", MAC_BARE)["argv"]
        self.assertEqual(posix[:2], ["sh", "-ec"])
        self.assertIn("--binary --ref v19.1.0", posix[2])
        self.assertNotIn("--ref", plan("OMP", "Update", APT)["argv"][2])
        self.assertEqual(plan("OMP", "Install", BARE), dict(kind="error", message=tooling.CURL_MISSING))

    def test_plan_winget_accepts_agreements(self):
        self.assertEqual(
            plan("Node.js", "Install", WINDOWS),
            dict(
                kind="run",
                argv=[
                    "winget",
                    "install",
                    "--exact",
                    "--id",
                    "OpenJS.NodeJS.LTS",
                    "--source",
                    "winget",
                    "--accept-source-agreements",
                    "--accept-package-agreements",
                ],
            ),
        )
        self.assertEqual(plan("Node.js", "Update", WINDOWS)["argv"][1], "upgrade")
        bare = plan("Node.js", "Install", Host("windows"))
        self.assertEqual(bare["kind"], "error")
        self.assertIn("winget is missing", bare["message"])
        self.assertEqual(plan("Hermes", "Install", WINDOWS)["kind"], "docs")

    def test_plan_windows_docker_route(self):
        fresh = plan("Docker", "Install", WINDOWS)["argv"]
        self.assertEqual(fresh[: len(tooling.POWERSHELL)], tooling.POWERSHELL)
        self.assertEqual(fresh[-2], "-File")
        self.assertTrue(fresh[-1].endswith("setup-web.ps1"))
        present = WINDOWS._replace(tools=WINDOWS.tools | {"docker"})
        self.assertEqual(plan("Docker", "Update", present)["argv"][:5], ["winget", "upgrade", "--exact", "--id", "Docker.DockerDesktop"])

    def test_plan_macos_uses_homebrew(self):
        self.assertEqual(
            plan("Node.js", "Install", MAC_BREW),
            dict(kind="run", argv=["brew", "install", "node"], env={"HOMEBREW_NO_ENV_HINTS": "1"}),
        )
        self.assertEqual(plan("GitHub CLI", "Update", MAC_BREW)["argv"], ["brew", "upgrade", "gh"])
        self.assertEqual(
            plan("Docker", "Install", MAC_BREW),
            dict(kind="terminal", argv=["brew", "install", "--cask", "docker-desktop"], purpose="install"),
        )
        self.assertEqual(plan("Claude Code", "Update", MAC_BREW)["argv"], ["brew", "upgrade", "--cask", "claude-code"])
        node = plan("Node.js", "Install", MAC_BARE)
        self.assertEqual((node["kind"], node["reason"], node["url"]), ("docs", "no-brew", tooling.TOOLS["Node.js"][3]))
        docker = plan("Docker", "Install", MAC_BARE)
        self.assertEqual((docker["kind"], docker["reason"], docker["url"]), ("docs", "no-brew", tooling.MAC_DOCS["Docker"]))
        self.assertEqual(plan("Docker", "Docs", MAC_BREW)["url"], tooling.MAC_DOCS["Docker"])
        self.assertEqual(plan("Docker", "Docs", APT)["url"], tooling.TOOLS["Docker"][3])
        self.assertEqual(plan("Hermes", "Install", MAC_BREW)["reason"], "no-installer")

    def test_plan_npm_prefix_and_prerequisite(self):
        for name, package in tooling.NPM.items():
            self.assertEqual(plan(name, "Install", APT), dict(kind="run", argv=["npm", "install", "-g", package]))
            local = plan(name, "Update", APT._replace(npm_user_prefix=True))
            self.assertEqual(local["argv"], ["npm", "install", "-g", "--prefix", "/home/me/.local", package])
            windows = plan(name, "Install", WINDOWS._replace(npm_user_prefix=True))
            self.assertEqual(windows["argv"], ["npm", "install", "-g", package])
            missing = plan(name, "Install", DNF)
            self.assertEqual(missing["kind"], "error")
            self.assertIn("Node.js", missing["message"])
            self.assertIn(name, missing["message"])
        self.assertEqual(sorted(tooling.NPM), ["Codex", "Pi", "opencode"])
        self.assertEqual(plan("Hugging Face CLI", "Install", MAC_BREW)["argv"], ["uv", "tool", "install", "--upgrade", "huggingface-hub"])
        self.assertIn("Install uv", plan("Hugging Face CLI", "Install", APT)["message"])

    def test_plan_linux_packages(self):
        self.assertEqual(
            plan("Docker", "Install", APT),
            dict(
                kind="terminal",
                argv=["sudo", "apt-get", "install", "-y", "docker.io", "docker-compose-v2"],
                purpose="install",
            ),
        )
        self.assertEqual(
            plan("Docker", "Install", APT._replace(root=True)),
            dict(kind="run", argv=["apt-get", "install", "-y", "docker.io", "docker-compose-v2"]),
        )
        none = plan("Docker", "Install", APT._replace(apt_docker=()))
        self.assertEqual((none["kind"], none["reason"]), ("docs", "no-compose-v2"))
        stuck = plan("GitHub CLI", "Install", APT._replace(tools=frozenset({"apt-get"})))
        self.assertEqual(stuck, dict(kind="error", message="Install sudo, or run this as root: apt-get install -y gh"))
        self.assertEqual(plan("Node.js", "Install", DNF)["argv"], ["sudo", "dnf", "install", "-y", "nodejs", "npm"])
        self.assertEqual(plan("Node.js", "Install", APT)["argv"], ["sudo", "apt-get", "install", "-y", "nodejs", "npm"])
        for name in ("Docker", "AWS CLI", "uv"):
            self.assertEqual(plan(name, "Install", DNF)["reason"], "no-installer")
        self.assertEqual(plan("Node.js", "Install", BARE)["reason"], "no-installer")
        self.assertEqual(plan("Bun", "Install", DNF)["argv"][:2], ["sh", "-ec"])
        self.assertEqual(plan("Bun", "Install", BARE)["message"], tooling.CURL_MISSING)

    def test_plan_vllm_per_platform(self):
        install = plan("vLLM", "Install", WINDOWS)["argv"]
        self.assertEqual(install[: len(tooling.POWERSHELL)], tooling.POWERSHELL)
        self.assertTrue(install[-3].endswith("setup-web.ps1"))
        self.assertEqual(install[-4], "-File")
        self.assertEqual(install[-2:], ["-Mode", "vllm"])
        update = plan("vLLM", "Update", WINDOWS)["argv"]
        self.assertEqual(update[-3:], ["-Mode", "vllm", "-Update"])
        self.assertNotIn("wsl", install + update)
        mac = plan("vLLM", "Install", MAC_BREW)
        self.assertEqual(mac["kind"], "error")
        self.assertIn("does not run on macOS", mac["message"])
        script = str(tooling.ROOT / "setup-vllm.sh")
        self.assertEqual(plan("vLLM", "Install", DNF), dict(kind="run", argv=["sh", script]))
        self.assertEqual(plan("vLLM", "Update", BARE)["argv"], ["sh", script, "--update"])

    def test_plan_login_engine_and_sandbox(self):
        self.assertEqual(
            plan("GitHub CLI", "Login", BARE),
            dict(kind="terminal", argv=["gh", "auth", "login"], purpose="login"),
        )
        self.assertIsNot(plan("GitHub CLI", "Login", BARE)["argv"], tooling.LOGIN["GitHub CLI"])
        self.assertEqual(plan("Bun", "Login", BARE), dict(kind="error", message="Bun does not use an account sign-in."))
        self.assertEqual(plan("Sandbox image", "Install", BARE), dict(kind="sandbox"))
        engine = str(tooling.ROOT / "setup_engines.py")
        self.assertEqual(plan("llama.cpp / Strata", "Install", WINDOWS)["argv"], [sys.executable, engine])
        self.assertEqual(plan("llama.cpp / Strata", "Update", MAC_BARE)["argv"], [sys.executable, engine, "--update"])

    def test_plan_is_total(self):
        kinds = {"run", "terminal", "docs", "error", "sandbox"}
        for host in HOSTS:
            for name in tooling.TOOLS:
                for action in tooling.ACTIONS:
                    step = plan(name, action, host)
                    self.assertIn(step["kind"], kinds, (name, action, host.system))
                    if step["kind"] in ("run", "terminal"):
                        self.assertTrue(all(isinstance(v, str) and v for v in step["argv"]), (name, action))
                    if step["kind"] == "error":
                        self.assertTrue(step["message"], (name, action))
                    if step["kind"] == "docs":
                        self.assertTrue(step["url"].startswith("https://"))
        with self.assertRaisesRegex(ValueError, "Unknown setup action."):
            plan("No such tool", "Install", BARE)
        with self.assertRaisesRegex(ValueError, "Unknown setup action."):
            plan("OMP", "Remove", BARE)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.data = Path(folder.name)
        for target, value in (("DATA", self.data), ("POLL", 0.1)):
            patcher = patch.object(tooling, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_logged_closes_stdin_and_returns_exit_code(self):
        code, reason, text = tooling.logged(
            child("import sys; print(sys.stdin.read() == ''); sys.exit(3)"), "Install Probe"
        )
        self.assertEqual((code, reason), (3, "exit"))
        self.assertIn("True", text)
        self.assertNotIn("Install Probe", text)
        log = (self.data / "setup.log").read_text(encoding="utf8")
        self.assertRegex(log, r"\n\d{4}-\d\d-\d\d \d\d:\d\d:\d\d Install Probe\n")

    def test_logged_passes_the_environment(self):
        env = dict(os.environ, AWB_PROBE="from-the-step")
        code, _, text = tooling.logged(child("import os; print(os.environ['AWB_PROBE'])"), "Install Probe", env)
        self.assertEqual((code, text.strip()), (0, "from-the-step"))

    def test_logged_cancel_kills_child(self):
        cancel = threading.Event()
        cancel.set()
        began = time.monotonic()
        _, reason, _ = tooling.logged(child("import time; time.sleep(30)"), "Install Sleeper", cancel=cancel)
        self.assertEqual(reason, "cancelled")
        self.assertLess(time.monotonic() - began, 2)

    def test_logged_timeout_kills_child(self):
        began = time.monotonic()
        _, reason, _ = tooling.logged(child("import time; time.sleep(30)"), "Install Sleeper", timeout=0)
        self.assertEqual(reason, "timeout")
        self.assertLess(time.monotonic() - began, 2)

    def test_logged_progress_reports_last_line(self):
        events = []

        def progress(event):
            events.append(event)
            raise RuntimeError("the listener went away")

        code, reason, text = tooling.logged(
            child("import time; print('one', flush=True); time.sleep(0.6); print('two', flush=True)"),
            "Install Talker",
            progress=progress,
        )
        self.assertEqual((code, reason), (0, "exit"))
        self.assertEqual(events[0]["message"], "one")
        self.assertIsInstance(events[0]["elapsed"], int)
        self.assertEqual(text.split(), ["one", "two"])

    def test_logged_reports_spawn_failure(self):
        code, reason, text = tooling.logged([str(self.data / "missing-program")], "Install Nothing")
        self.assertEqual((code, reason), (None, "spawn"))
        self.assertTrue(text)

    def test_logged_reports_a_log_that_cannot_be_opened(self):
        with patch.object(tooling, "DATA", self.data / "removed" / "deeper"):
            code, reason, text = tooling.logged(child("print('ran')"), "Install Probe")
            self.assertEqual((code, reason), (None, "log"))
            self.assertTrue(text)
            with (
                patch.object(tooling, "refresh_path", return_value=[]),
                patch.object(tooling, "host_facts", return_value=BARE),
                patch.object(tooling, "plan", return_value=dict(kind="run", argv=child("print('ran')"))),
            ):
                out = tooling.run("OMP", "Install")
        self.assertEqual(out["outcome"], "failed")
        self.assertIn("setup log", out["message"])
        self.assertIn("create that folder or make it writable, then retry.", out["message"])

    def test_engine_setup_failure_reaches_the_outcome_as_its_sentence(self):
        stop = "Press Stop server, then run engine setup again so the engine files can be replaced."
        code = (
            "import setup_engines\n"
            "def install(*args, **kwargs):\n"
            f"    raise RuntimeError({stop!r})\n"
            "setup_engines.install = install\n"
            "setup_engines.main([])\n"
        )
        step = dict(kind="run", argv=child(code), env={"WORKBENCH_DATA": str(self.data)})
        with (
            patch.object(tooling, "refresh_path", return_value=[]),
            patch.object(tooling, "host_facts", return_value=BARE),
            patch.object(tooling, "plan", return_value=step),
        ):
            out = tooling.run("llama.cpp / Strata", "Install")
        self.assertEqual((out["outcome"], out["message"]), ("failed", stop))

    def test_logged_rotates_a_large_log(self):
        log = self.data / "setup.log"
        log.write_bytes(b"x" * 2_000_001)
        code, _, text = tooling.logged(child("print('fresh')"), "Install Probe")
        self.assertEqual((code, text.strip()), (0, "fresh"))
        self.assertEqual((self.data / "setup.log.1").stat().st_size, 2_000_001)
        self.assertLess(log.stat().st_size, 1000)

    def test_tail_and_last_line(self):
        log = self.data / "out.log"
        log.write_bytes(b"old\nfirst\n\x00s\x00e\x00c\x00o\x00n\x00d\n\n  \n")
        self.assertEqual(tooling.tail(log, 4), "first\nsecond\n\n  \n")
        self.assertEqual(tooling.tail(log, 0, limit=4), "\n  \n")
        self.assertEqual(tooling.last_line(tooling.tail(log, 4)), "second")
        self.assertEqual(tooling.last_line(" \n"), "")
        self.assertEqual(len(tooling.last_line("x" * 500)), 200)

    def test_kill_tree_ignores_a_finished_process(self):
        with patch.object(tooling.psutil, "Process", side_effect=tooling.psutil.NoSuchProcess(1)) as process:
            tooling.kill_tree(1)
        process.assert_called_once_with(1)

    @unittest.skipUnless(os.name == "posix", "the terminal wrapper is POSIX only")
    def test_hold_keeps_result_readable(self):
        def shown(argv):
            return subprocess.run(
                tooling.hold(argv), input="\n", capture_output=True, text=True, timeout=20
            ).stdout

        self.assertIn("The command failed with exit code 3.", shown(["sh", "-c", "exit 3"]))
        done = shown(["true"])
        self.assertIn("Finished.", done)
        self.assertIn("Press Enter to close this window.", done)
        self.assertEqual(tooling.hold(["brew", "install", "--cask", "docker-desktop"])[:2], ["sh", "-c"])


class RunTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.data = Path(folder.name)
        self.mocks = {}
        for name, value in dict(
            DATA=self.data,
            SYSTEM="darwin",
            host_facts=MagicMock(return_value=MAC_BREW),
            plan=MagicMock(),
            logged=MagicMock(return_value=(0, "exit", "")),
            terminal=MagicMock(),
            command=MagicMock(side_effect=lambda exe: ["/resolved/" + exe]),
            resolve=MagicMock(side_effect=lambda argv: ["/resolved/" + argv[0], *argv[1:]]),
            detect=MagicMock(return_value=dict(INSTALLED)),
            refresh_path=MagicMock(return_value=[]),
        ).items():
            patcher = patch.object(tooling, name, value)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(tooling.webbrowser, "open", return_value=True)
        self.browser = patcher.start()
        self.addCleanup(patcher.stop)

    def go(self, step, name="OMP", action="Install", **kwargs):
        self.mocks["plan"].return_value = step
        return tooling.run(name, action, **kwargs)

    def test_run_outcomes(self):
        logged, detect = self.mocks["logged"], self.mocks["detect"]
        step = dict(kind="run", argv=["brew", "install", "gh"], env={"HOMEBREW_NO_ENV_HINTS": "1"})

        out = self.go(step, "GitHub CLI")
        self.assertEqual(
            out,
            dict(
                name="GitHub CLI",
                action="Install",
                outcome="installed",
                message="GitHub CLI is ready at /opt/x/bin/tool.",
                path="/opt/x/bin/tool",
                hint="",
                url="",
                log=str(self.data / "setup.log"),
            ),
        )
        self.mocks["host_facts"].assert_called_once_with("GitHub CLI")
        self.mocks["plan"].assert_called_once_with("GitHub CLI", "Install", MAC_BREW)
        argv, header, env, timeout, progress, cancel = logged.call_args.args
        self.assertEqual((argv, header, timeout), (["/resolved/brew", "install", "gh"], "Install GitHub CLI", 3600))
        self.assertEqual(env["HOMEBREW_NO_ENV_HINTS"], "1")
        self.assertEqual(env["PATH"], os.environ["PATH"])
        self.assertEqual((progress, cancel), (None, None))
        self.assertEqual(self.mocks["refresh_path"].call_count, 2)
        detect.assert_called_once_with("GitHub CLI")

        self.go(dict(kind="run", argv=["npm", "install"]))
        self.assertIsNone(logged.call_args.args[2])

        docs = self.go(dict(kind="docs", url="https://example.test/hermes", reason="no-installer"), "Hermes")
        self.assertEqual((docs["outcome"], docs["url"]), ("opened-docs", "https://example.test/hermes"))
        self.assertTrue(docs["message"].startswith("Agent Workbench has no automatic installer for Hermes"))
        self.browser.assert_called_with("https://example.test/hermes")

        started = self.go(dict(kind="terminal", argv=["brew", "install", "--cask", "docker-desktop"], purpose="install"), "Docker")
        self.assertEqual(started["outcome"], "started-terminal")
        self.assertEqual(
            started["message"],
            "Finish the Docker install in the terminal window that opened, then choose Recheck installations.",
        )
        shown = self.mocks["terminal"].call_args.args[0]
        self.assertEqual(shown[:2], ["sh", "-c"])
        self.assertTrue(shown[2].startswith("/resolved/brew install --cask docker-desktop; s=$?"))

        logged.return_value = (1, "exit", "Reading...\r\n  workbench-setup: X \r\nworkbench-setup: Run wsl --update, then retry.\r\ntrailer\r\n")
        marked = self.go(dict(kind="run", argv=["powershell"]), "vLLM")
        self.assertEqual((marked["outcome"], marked["message"]), ("failed", "Run wsl --update, then retry."))
        logged.return_value = (1, "exit", "workbench-setup: X\n")
        self.assertEqual(self.go(dict(kind="run", argv=["powershell"]))["message"], "X")

        logged.return_value = (1, "exit", "npm ERR! code EACCES\n")
        plain = self.go(dict(kind="run", argv=["npm"]), "Pi", "Update")
        self.assertEqual(
            (plain["outcome"], plain["message"], plain["path"]),
            ("failed", "Pi update failed with exit code 1; open the setup log to see the installer's error.", ""),
        )

        logged.return_value = (0, "timeout", "")
        late = self.go(dict(kind="run", argv=["npm"]), "Pi")
        self.assertEqual(
            (late["outcome"], late["message"]),
            ("failed", "Pi install was stopped after 60 minutes; check your internet connection, then try again."),
        )
        logged.return_value = (0, "cancelled", "")
        gone = self.go(dict(kind="run", argv=["npm"]), "Pi", "Update")
        self.assertEqual(
            (gone["outcome"], gone["message"]),
            ("failed", "Pi update was cancelled; choose Update to start it again."),
        )
        logged.return_value = (None, "spawn", "No such file or directory")
        absent = self.go(dict(kind="run", argv=["winget", "install"]), "Bun")
        self.assertEqual(
            (absent["outcome"], absent["message"]),
            ("failed", "winget could not be started (No such file or directory); reinstall it, then retry."),
        )
        logged.return_value = (None, "log", "Permission denied")
        sealed = self.go(dict(kind="run", argv=["npm"]), "Pi")
        self.assertEqual(sealed["outcome"], "failed")
        self.assertIn("could not be opened (Permission denied)", sealed["message"])
        self.assertEqual(detect.call_count, 2)

    def test_run_reports_where_the_tool_landed(self):
        detect = self.mocks["detect"]
        hint = "To use omp in your own terminals, run: fish_add_path ~/.local/bin"
        detect.return_value = dict(path=HOME + "/.local/bin/omp", hint=hint, on_path=False)
        out = self.go(dict(kind="run", argv=["sh"]))
        self.assertEqual(
            out["message"],
            f"OMP is ready at {HOME}/.local/bin/omp; add ~/.local/bin to your PATH to use it in your own terminals.",
        )
        self.assertEqual((out["outcome"], out["path"], out["hint"]), ("installed", HOME + "/.local/bin/omp", hint))
        detect.return_value = dict(path="/opt/tools/bin/omp", hint="", on_path=False)
        self.assertIn("; add /opt/tools/bin to your PATH", self.go(dict(kind="run", argv=["sh"]))["message"])
        detect.return_value = dict(path="", hint="", on_path=True)
        lost = self.go(dict(kind="run", argv=["sh"]))
        self.assertEqual((lost["outcome"], lost["path"]), ("installed", ""))
        self.assertEqual(
            lost["message"],
            "OMP finished installing but was not found; restart Agent Workbench, then choose Recheck installations.",
        )
        self.assertEqual(tooling.short("C:\\Users\\me\\.local\\bin", "C:\\Users\\me"), "~\\.local\\bin")
        self.assertEqual(tooling.short("/Users/me", "/Users/me"), "~")
        self.assertEqual(tooling.short("/Users/melanie/bin", "/Users/me"), "/Users/melanie/bin")

    def test_run_docs_and_error_steps(self):
        asked = self.go(dict(kind="docs", url="https://example.test/d", reason="requested"), "Docker", "Docs")
        self.assertEqual(asked["message"], "The Docker documentation was opened in your browser.")
        self.assertIn("Homebrew was not found", self.go(dict(kind="docs", url="https://example.test/d", reason="no-brew"))["message"])
        self.assertIn("Docker Compose v2", self.go(dict(kind="docs", url="https://example.test/d", reason="no-compose-v2"))["message"])
        self.browser.return_value = False
        headless = self.go(dict(kind="docs", url="https://example.test/d", reason="requested"), "Docker", "Docs")
        self.assertEqual(
            (headless["outcome"], headless["url"], headless["message"]),
            ("failed", "https://example.test/d", "No browser could be opened; visit https://example.test/d to continue with Docker."),
        )
        refused = self.go(dict(kind="error", message="Install Node.js in Setup first, then install Pi."), "Pi")
        self.assertEqual((refused["outcome"], refused["message"]), ("failed", "Install Node.js in Setup first, then install Pi."))
        self.mocks["logged"].assert_not_called()
        self.mocks["terminal"].assert_not_called()
        self.mocks["detect"].assert_not_called()

    def test_run_login_and_terminal_failures(self):
        terminal, command = self.mocks["terminal"], self.mocks["command"]
        login = dict(kind="terminal", argv=["gh", "auth", "login"], purpose="login")
        out = self.go(login, "GitHub CLI", "Login")
        self.assertEqual(
            (out["outcome"], out["message"]),
            ("started-terminal", "Complete the GitHub CLI sign-in in the terminal window that opened."),
        )
        terminal.assert_called_once_with(["/resolved/gh", "auth", "login"])
        terminal.reset_mock()
        command.side_effect = ValueError("gh is missing. Open Setup to install it.")
        missing = self.go(login, "GitHub CLI", "Login")
        self.assertEqual((missing["outcome"], missing["message"]), ("failed", "Install GitHub CLI first, then sign in."))
        terminal.assert_not_called()
        command.side_effect = lambda exe: ["/resolved/" + exe]
        terminal.side_effect = ValueError("Install a terminal emulator or launch from a terminal on Linux.")
        self.assertEqual(self.go(login, "GitHub CLI", "Login")["message"], "Install a terminal emulator or launch from a terminal on Linux.")
        terminal.side_effect = TimeoutError("timed out")
        install = dict(kind="terminal", argv=["sudo", "apt-get", "install", "-y", "gh"], purpose="install")
        for system, words in (("darwin", "Privacy & Security > Automation"), ("linux", "install xterm"), ("windows", "normal desktop session")):
            with patch.object(tooling, "SYSTEM", system):
                blocked = self.go(install, "GitHub CLI")
            self.assertEqual(blocked["outcome"], "failed")
            self.assertIn(words, blocked["message"])
        self.mocks["detect"].assert_not_called()

    def test_run_builds_the_sandbox_image(self):
        build = MagicMock()
        self.mocks["detect"].return_value = dict(path="sandbox:1", hint="", on_path=True)
        with fake_boxes(build=build):
            built = self.go(dict(kind="sandbox"), "Sandbox image")
            self.assertEqual((built["outcome"], built["message"]), ("installed", "Sandbox image is ready at sandbox:1."))
            build.assert_called_once_with()
            build.side_effect = ValueError("X")
            self.assertEqual(self.go(dict(kind="sandbox"), "Sandbox image")["message"], "X")
            for failure in (subprocess.CalledProcessError(1, "docker"), subprocess.TimeoutExpired("docker", 900)):
                build.side_effect = failure
                failed = self.go(dict(kind="sandbox"), "Sandbox image")
                self.assertEqual(
                    (failed["outcome"], failed["message"]),
                    ("failed", "The sandbox image could not be built; start Docker, then retry."),
                )
        self.mocks["logged"].assert_not_called()
        self.assertEqual(self.mocks["detect"].call_count, 1)

    def test_run_wraps_progress_and_passes_cancel(self):
        events, cancel = [], threading.Event()
        self.go(dict(kind="run", argv=["sh"]), progress=events.append, cancel=cancel)
        wrapped, passed = self.mocks["logged"].call_args.args[4:]
        self.assertIs(passed, cancel)
        wrapped(dict(message="Installed omp", elapsed=14))
        self.assertEqual(
            events,
            [dict(job="tool-setup", phase="running", name="OMP", action="Install", message="Installed omp", elapsed=14)],
        )

    def test_run_refreshes_path_before_it_plans(self):
        order = []
        self.mocks["refresh_path"].side_effect = lambda: order.append("refresh") or []
        self.mocks["host_facts"].side_effect = lambda name: order.append("facts") or MAC_BREW
        self.mocks["command"].side_effect = lambda exe: order.append("command") or ["/resolved/" + exe]
        self.go(dict(kind="terminal", argv=["gh", "auth", "login"], purpose="login"), "GitHub CLI", "Login")
        self.assertEqual(order, ["refresh", "facts", "command"])

    def test_run_refuses_unknown_requests_before_anything_starts(self):
        self.mocks["plan"].side_effect = plan
        for name, action in (("No such tool", "Install"), ("OMP", "Remove")):
            with self.assertRaisesRegex(ValueError, "Unknown setup action."):
                tooling.run(name, action)
        self.mocks["logged"].assert_not_called()
        self.browser.assert_not_called()

    def test_setup_raises_on_failure(self):
        done = self.go(dict(kind="run", argv=["sh"]))
        with patch.object(tooling, "run", return_value=done) as run:
            self.assertIs(tooling.setup("OMP", "Install"), done)
            run.assert_called_once_with("OMP", "Install")
            run.return_value = dict(done, outcome="started-terminal")
            self.assertEqual(tooling.setup("OMP", "Login")["outcome"], "started-terminal")
            run.return_value = dict(done, outcome="failed", message="Install curl with your package manager, then retry.")
            with self.assertRaises(RuntimeError) as caught:
                tooling.setup("OMP", "Install")
        self.assertEqual(str(caught.exception), "Install curl with your package manager, then retry.")


@unittest.skipUnless(os.name == "posix", "uses shebang scripts")
class OffPathTests(unittest.TestCase):
    """A tool in a per-user folder that the process PATH lacks, with nobody having refreshed PATH first."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.bin = Path(folder.name, "bin")
        self.bin.mkdir()
        self.data = Path(folder.name, "data")
        self.data.mkdir()
        for target, value in (
            ("DATA", self.data),
            ("POLL", 0.05),
            ("candidate_dirs", MagicMock(return_value=[str(self.bin)])),
            ("_registry_paths", MagicMock(return_value=[])),
            ("host_facts", MagicMock(return_value=BARE)),
        ):
            patcher = patch.object(tooling, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.dict(os.environ, {"PATH": "/usr/bin" + SEP + "/bin", tooling.ADDED_ENV: ""})
        patcher.start()
        self.addCleanup(patcher.stop)

    def script(self, name, body):
        path = self.bin / name
        path.write_text(body, encoding="utf8")
        path.chmod(0o755)
        return str(path)

    def test_installer_finds_the_helper_next_to_it(self):
        self.script("awb-node", "#!/bin/sh\necho node-ran\n")
        self.script("awb-npm", "#!/usr/bin/env awb-node\n")
        with (
            patch.object(tooling, "plan", return_value=dict(kind="run", argv=["awb-npm", "install"])),
            patch.object(tooling, "detect", return_value=dict(INSTALLED)),
        ):
            out = tooling.run("Pi", "Install")
        self.assertEqual(out["outcome"], "installed", out["message"])
        self.assertIn("node-ran", (self.data / "setup.log").read_text(encoding="utf8"))
        self.assertIn(str(self.bin), os.environ["PATH"].split(SEP))

    def test_login_starts_for_a_tool_setup_shows_as_installed(self):
        omp = self.script("omp", "#!/bin/sh\n")
        self.assertTrue(tooling.detect("OMP")["installed"])
        with patch.object(tooling, "terminal") as terminal:
            out = tooling.run("OMP", "Login")
        self.assertEqual(
            (out["outcome"], out["message"]),
            ("started-terminal", "Complete the OMP sign-in in the terminal window that opened."),
        )
        terminal.assert_called_once_with([omp, "login"])


class WebFilterTests(unittest.TestCase):
    def setUp(self):
        self.resolver = MagicMock(return_value=[(2, 1, 6, "", ("93.184.216.34", 0))])
        for patcher in (
            patch.dict(os.environ, {"WORKBENCH_WEB_PRIVATE": ""}),
            patch.object(web_tools.private_host, "__defaults__", (self.resolver,)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def refused(self, url):
        with self.assertRaises(ValueError, msg=url) as caught:
            web_tools.public_url(url)
        return str(caught.exception)

    def test_urls_a_browser_parser_reads_differently_are_refused(self):
        for url in (
            "http://127.0.0.1\\@example.com/",
            "http://example.com\\@127.0.0.1:10532/v1/models",
            "http://%31%32%37.0.0.1:8080/",
            "http://%6c%6f%63%61%6c%68%6f%73%74/",
            "http://exam\tple.com/",
            "http://example.com/a b",
        ):
            self.assertIn("without spaces or backslashes", self.refused(url))
        self.assertIn("valid host name", self.refused("http://a..b.com/"))
        self.assertIn("valid host name", self.refused("http://" + "x" * 70 + ".com/"))
        self.resolver.assert_not_called()

    def test_names_that_only_resolve_on_a_private_network_are_refused(self):
        for url in (
            "http://redis:6379/",
            "http://rabbitmq:15672/",
            "http://playwright-service:3000/scrape",
            "http://nuq-postgres:5432/",
            "http://2130706433/",
            "http://host.docker\u3002internal:10532/",
            "http://loc\u00adalhost/",
            "http://\uff11\uff12\uff17.\uff10.\uff10.\uff11/",
            "http://127.0.0.1:8080",
            "http://[::1]/",
            "http://169.254.169.254/",
        ):
            self.assertIn("private network", self.refused(url))
        self.resolver.assert_not_called()
        with patch.dict(os.environ, {"WORKBENCH_WEB_PRIVATE": "1"}):
            self.assertEqual(web_tools.public_url("http://redis:6379/"), "http://redis:6379/")
            self.assertEqual(web_tools.public_url("http://127.0.0.1:8080"), "http://127.0.0.1:8080")
            self.assertIn("without spaces or backslashes", self.refused("http://127.0.0.1\\@example.com/"))

    def test_public_urls_pass_and_resolved_addresses_decide(self):
        self.assertEqual(web_tools.public_url(" https://example.com/a%20b?q=1 \n"), "https://example.com/a%20b?q=1")
        self.assertEqual(web_tools.public_url("https://m\u00fcnchen.de/"), "https://m\u00fcnchen.de/")
        self.assertEqual(self.resolver.call_args_list[-1].args[0], "xn--mnchen-3ya.de")
        self.resolver.return_value = [(2, 1, 6, "", ("10.0.0.5", 0))]
        self.assertIn("private network", self.refused("https://intranet.example/"))
        for failure in (OSError("no DNS"), UnicodeError("label empty or too long")):
            self.resolver.side_effect = failure
            self.assertFalse(web_tools.private_host("example.com"))
        self.assertTrue(web_tools.private_host("fileserver", MagicMock(side_effect=AssertionError("resolved"))))
        self.assertEqual(self.refused("ftp://example.com/"), "Enter an HTTP or HTTPS URL.")
        self.assertEqual(self.refused("http:///x"), "Enter an HTTP or HTTPS URL.")

    def reply(self, status, body):
        r = requests.Response()
        r.status_code = status
        r._content = body.encode("utf8")
        return r

    def test_firecall_messages(self):
        with patch.object(web_tools.LOCAL, "request") as send:
            send.side_effect = requests.ConnectionError("refused")
            with self.assertRaises(ValueError) as caught:
                web_tools.firecall("POST", "/scrape", {"url": "https://example.com/"})
            self.assertTrue(str(caught.exception).startswith("Firecrawl is not running on 127.0.0.1:3002."))
            send.assert_called_once_with(
                "POST", "http://127.0.0.1:3002/v2/scrape", json={"url": "https://example.com/"}, timeout=100
            )
            send.side_effect = requests.Timeout("slow")
            with self.assertRaises(ValueError) as caught:
                web_tools.firecall("GET", "/crawl/1")
            self.assertIn("did not answer within 100 seconds", str(caught.exception))
            send.side_effect = None
            for status, body, text in (
                (500, '{"success": false, "error": "boom"}', "boom"),
                (200, '{"success": false, "error": "Page blocked."}', "Page blocked."),
                (502, "<html>Bad Gateway</html>", "Firecrawl request failed with HTTP 502."),
                (200, '["unexpected"]', "Firecrawl request failed with HTTP 200."),
            ):
                send.return_value = self.reply(status, body)
                with self.assertRaises(ValueError) as caught:
                    web_tools.firecall("POST", "/scrape", {})
                self.assertEqual(str(caught.exception), text)
            send.return_value = self.reply(200, '{"success": true, "data": {"markdown": "# Page"}}')
            self.assertEqual(web_tools.firecall("POST", "/scrape", {})["data"]["markdown"], "# Page")

    def test_scrape_marks_truncation(self):
        long = dict(success=True, data=dict(markdown="x" * 50000))
        with patch.object(web_tools, "firecall", return_value=long) as call:
            page = web_tools.scrape("https://example.com/")
            self.assertEqual(call.call_args.args[2], {"url": "https://example.com/", "formats": ["markdown"]})
            with self.assertRaises(ValueError):
                web_tools.scrape("http://127.0.0.1:10532/v1/models")
            self.assertEqual(call.call_count, 1)
        self.assertEqual(page, "x" * 45000 + "\n\n[Truncated at 45,000 of 50,000 characters.]")
        with patch.object(web_tools, "firecall", return_value=dict(success=True, data=dict(markdown="short"))):
            self.assertEqual(web_tools.scrape("https://example.com/"), "short")
        job = "123e4567-e89b-12d3-a456-426614174000"
        docs = [dict(metadata=dict(sourceURL="https://example.com/a"), markdown="y" * 50000), dict()]

        def status(*args):
            return dict(status="completed", data=list(docs), next="n")

        with patch.object(web_tools, "firecall", side_effect=status) as call:
            first = web_tools.crawl_status(job)
            self.assertEqual(call.call_args.args, ("GET", "/crawl/" + job))
            self.assertEqual(first["page"]["url"], "https://example.com/a")
            self.assertTrue(first["page"]["markdown"].endswith("[Truncated at 45,000 of 50,000 characters.]"))
            self.assertEqual(web_tools.crawl_status(job, 1), dict(status="completed", page=dict(url=None, markdown="")))
            self.assertEqual(web_tools.crawl_status(job, 2), dict(status="completed"))
            with self.assertRaises(ValueError):
                web_tools.crawl_status("../admin")

    def test_loopback_sessions_ignore_proxies(self):
        self.assertIs(web_tools.LOCAL.trust_env, False)
        self.assertIs(tool_load.LOCAL.trust_env, False)
        with (
            patch.dict(os.environ, {"HTTP_PROXY": "http://proxy.invalid:9", "NO_PROXY": ""}),
            patch.object(tool_load.LOCAL, "send", return_value=self.reply(200, '{"usage": {"prompt_tokens": 7}}')) as send,
            patch.object(tool_load, "key", return_value="local-key"),
        ):
            self.assertEqual(tool_load.complete([dict(role="user", content="hi")], [], 1)["usage"]["prompt_tokens"], 7)
        self.assertEqual(send.call_args.kwargs["proxies"], {})
        self.assertEqual(send.call_args.args[0].url, tool_load.URL + "/v1/chat/completions")

    def test_overflow_helper(self):
        def error(status, body):
            return requests.HTTPError(response=self.reply(status, body))

        self.assertTrue(tool_load.overflow(error(400, "the request exceeds the available context size")))
        self.assertTrue(tool_load.overflow(error(413, "Request Too Large")))
        self.assertTrue(tool_load.overflow(error(422, "prompt is TOO LONG")))
        self.assertFalse(tool_load.overflow(error(500, "context")))
        self.assertFalse(tool_load.overflow(error(400, "tool schema is invalid")))
        self.assertFalse(tool_load.overflow(requests.HTTPError("no response")))


class PluginCostTests(unittest.TestCase):
    def test_child_env_hides_provider_keys(self):
        ours = {
            "OPENAI_API_KEY": "sk-test-value",
            "WORKBENCH_SESSION_KEY": "session-value",
            "WORKBENCH_DATA": "/data",
            "HTTPS_PROXY": "http://proxy.example:8080",
            "PATH": "/usr/bin",
        }
        with patch.dict(os.environ, ours):
            env = plugin_cost.child_env({"env": {"A": "1", "PATH": "/opt/server/bin"}})
            bare = plugin_cost.child_env({})
        for hidden in ("OPENAI_API_KEY", "WORKBENCH_SESSION_KEY", "WORKBENCH_DATA"):
            self.assertNotIn(hidden, env)
            self.assertNotIn(hidden, bare)
        self.assertEqual((env["HTTPS_PROXY"], env["A"], env["PATH"]), ("http://proxy.example:8080", "1", "/opt/server/bin"))
        self.assertEqual(bare["PATH"], "/usr/bin")
        self.assertNotIn("sk-test-value", " ".join(env.values()))

    def failing(self, exc):
        async def schemas(name, conf):
            raise exc

        with patch.object(plugin_cost, "schemas", schemas), self.assertRaises(ValueError) as caught:
            asyncio.run(plugin_cost.inspect_one("srv", {"command": "nope-cmd"}))
        return str(caught.exception)

    def test_inspect_one_names_the_error_inside_a_task_group(self):
        refused = ConnectionRefusedError("All connection attempts failed")
        text = self.failing(ExceptionGroup("unhandled errors in a TaskGroup", [ExceptionGroup("inner", [refused])]))
        self.assertIn("(ConnectionRefusedError: All connection attempts failed)", text)
        self.assertNotIn("TaskGroup", text)
        self.assertTrue(text.endswith("check its command and connection, then measure again."))
        self.assertIn("(nope-cmd)", self.failing(ExceptionGroup("group", [FileNotFoundError("nope-cmd")])))
        self.assertIn("(nope-cmd)", self.failing(FileNotFoundError("nope-cmd")))
        self.assertIn("(RuntimeError: two lines)", self.failing(RuntimeError("two\n  lines")))
        transport = "srv: tool inspection supports stdio and streamable HTTP."
        self.assertEqual(self.failing(ValueError(transport)), transport)
        self.assertIn("within 30 seconds", self.failing(asyncio.TimeoutError()))

    def test_inspect_one_keeps_a_rejected_value_out_of_the_message(self):
        conf = {"command": sys.executable, "args": ["-c", "pass"], "env": {"TOKEN": ["s3cret-value"]}}
        with patch.object(plugin_cost, "stdio_client", side_effect=AssertionError("a server was started")):
            with self.assertRaises(ValueError) as caught:
                asyncio.run(plugin_cost.inspect_one("srv", conf))
        text = str(caught.exception)
        self.assertNotIn("s3cret", text)
        self.assertEqual(text, "The registered settings for srv are not valid (env); register the server again, then measure again.")


class SetupFileTests(unittest.TestCase):
    def text(self, name):
        return (tooling.ROOT / name).read_text(encoding="utf8")

    def test_compose_has_no_host_interpolation(self):
        text = self.text("firecrawl.compose.yml")
        self.assertNotIn("${", text)
        stack = yaml.safe_load(text)
        ports = {name: service["ports"] for name, service in stack["services"].items() if "ports" in service}
        self.assertEqual(ports, {"api": ["127.0.0.1:3002:3002"]})
        self.assertEqual(text.count("ports:"), 1)
        pinned = sorted(s["image"] for s in stack["services"].values() if "@sha256:" in s.get("image", ""))
        self.assertEqual(
            pinned,
            [
                "ghcr.io/firecrawl/firecrawl@sha256:3736b7582b1b261f0fa346f9feb235fbf70fb7c6ef734603bfcfd540c490142b",
                "ghcr.io/firecrawl/nuq-postgres@sha256:d719b36160eac2fcbe16dc7e7360ddb9c82c40ea5da183d07bf4a755c6a0b2ae",
                "ghcr.io/firecrawl/playwright-service@sha256:227ce3d8059173925a5a0697c63a1e947662cca934a609151651f578774edd5d",
            ],
        )
        header = "".join(line for line in text.splitlines(True) if line.startswith("#"))
        self.assertIn("https://github.com/firecrawl/firecrawl", header)
        self.assertIn("AGPL-3.0", header)
        self.assertRegex(header, r"Modified for Agent Workbench, last on \d{4}-\d\d-\d\d")

    def test_setup_web_script_invariants(self):
        text = self.text("setup-web.ps1")
        for piece in ("ValidateSet('web', 'vllm')", "[switch]$Update", "python3-venv", '"workbench-setup: $Message"'):
            self.assertIn(piece, text)
        self.assertTrue(text.isascii())
        inside = [line for line in text.splitlines() if "-d $dist" in line]
        self.assertEqual(len(inside), 3)
        for line in inside:
            self.assertIn(" --exec ", line)
            self.assertNotIn(" -- ", line)
            self.assertNotRegex(line, r"\b(sh|bash) -l?c\b")
        self.assertEqual(tooling.MARK, "workbench-setup: ")

    def test_owned_spawns_close_stdin(self):
        spawns = []
        for name in ("tooling.py", "studio.py", "integration_ui.py", "workbench.py", "doctor.py"):
            for node in ast.walk(ast.parse(self.text(name))):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                owner = node.func.value
                if isinstance(owner, ast.Name) and owner.id == "subprocess" and node.func.attr in (
                    "run",
                    "Popen",
                    "call",
                    "check_call",
                    "check_output",
                ):
                    spawns.append(name)
                    self.assertIn("stdin", [k.arg for k in node.keywords], f"{name}:{node.lineno}")
        self.assertEqual(sorted(set(spawns)), ["doctor.py", "integration_ui.py", "studio.py", "tooling.py", "workbench.py"])
        self.assertNotIn("os.system(", "".join(self.text(n) for n in ("tooling.py", "studio.py", "doctor.py")))


if __name__ == "__main__":
    unittest.main()
