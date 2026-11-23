"""host.py: shim resolution, batch refusal, terminal launch branches, the macOS hand-off and process identity."""

import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import host

NEW_SHIM = (
    "@ECHO off\r\nGOTO start\r\n:find_dp0\r\nSET dp0=%~dp0\r\nEXIT /b\r\n:start\r\nSETLOCAL\r\nCALL :find_dp0\r\n\r\n"
    'IF EXIST "%dp0%\\node.exe" (\r\n  SET "_prog=%dp0%\\node.exe"\r\n) ELSE (\r\n  SET "_prog=node"\r\n'
    "  SET PATHEXT=%PATHEXT:;.JS;=;%\r\n)\r\n\r\n"
    'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  '
    '"%dp0%\\node_modules\\@openai\\codex\\bin\\codex.js" %*\r\n'
)
OLD_SHIM = (
    '@IF EXIST "%~dp0\\node.exe" (\r\n  "%~dp0\\node.exe"  "%~dp0\\node_modules\\pkg\\cli.js" %*\r\n) ELSE (\r\n'
    "  @SETLOCAL\r\n  @SET PATHEXT=%PATHEXT:;.JS;=;%\r\n"
    '  node  "%~dp0\\node_modules\\pkg\\cli.js" %*\r\n)\r\n'
)
EXE_SHIM = (
    "@ECHO off\r\nGOTO start\r\n:find_dp0\r\nSET dp0=%~dp0\r\nEXIT /b\r\n:start\r\nSETLOCAL\r\nCALL :find_dp0\r\n"
    '"%dp0%\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe"   %*\r\n'
)
NPM_SHIM = (
    '@ECHO OFF\r\nSETLOCAL\r\nSET "NODE_EXE=%~dp0\\node.exe"\r\nIF NOT EXIST "%NODE_EXE%" (\r\n'
    '  SET "NODE_EXE=node"\r\n)\r\nSET "NPM_PREFIX_JS=%~dp0\\node_modules\\npm\\bin\\npm-prefix.js"\r\n'
    '"%NODE_EXE%" "%NPM_CLI_JS%" %*\r\n'
)
TARGETS = (
    "node_modules/@openai/codex/bin/codex.js",
    "node_modules/pkg/cli.js",
    "node_modules/@anthropic-ai/claude-code/bin/claude.exe",
)
POSIX_SOCKETS = hasattr(socket, "AF_UNIX") and os.name != "nt"


def shim_folder(test):
    folder = Path(tempfile.mkdtemp(prefix="awb-shim-"))
    test.addCleanup(shutil.rmtree, folder, ignore_errors=True)
    for rel in TARGETS:
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text("x", encoding="utf8")
    for name, text in (
        ("codex.cmd", NEW_SHIM),
        ("old.cmd", OLD_SHIM),
        ("claude.cmd", EXE_SHIM),
        ("npm.cmd", NPM_SHIM),
        ("gone.cmd", NEW_SHIM.replace("codex.js", "missing.js")),
    ):
        (folder / name).write_bytes(text.encode("utf8"))
    return folder


def reaped_pid():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


class ShimTests(unittest.TestCase):
    def test_resolve_shim_formats(self):
        folder = shim_folder(self)
        with patch.object(host.shutil, "which", return_value="NODE"):
            self.assertEqual(
                host.resolve_shim(folder / "codex.cmd"),
                ["NODE", str(folder / TARGETS[0])],
            )
            self.assertEqual(
                host.resolve_shim(str(folder / "old.cmd")),
                ["NODE", str(folder / TARGETS[1])],
            )
            self.assertEqual(
                host.resolve_shim(folder / "claude.cmd"), [str(folder / TARGETS[2])]
            )
            self.assertIsNone(host.resolve_shim(folder / "npm.cmd"))
            self.assertIsNone(host.resolve_shim(folder / "gone.cmd"))
            self.assertIsNone(host.resolve_shim(folder / "absent.cmd"))
            (folder / "node.exe").write_text("x", encoding="utf8")
            self.assertEqual(
                host.resolve_shim(folder / "codex.cmd"),
                [str(folder / "node.exe"), str(folder / TARGETS[0])],
            )

    def test_resolve_shim_needs_node_and_a_known_target(self):
        folder = shim_folder(self)
        with patch.object(host.shutil, "which", return_value=None):
            self.assertIsNone(host.resolve_shim(folder / "codex.cmd"))
            self.assertEqual(
                host.resolve_shim(folder / "claude.cmd"), [str(folder / TARGETS[2])]
            )
        (folder / "node_modules/pkg/run.ps1").write_text("x", encoding="utf8")
        (folder / "odd.cmd").write_text(
            OLD_SHIM.replace("cli.js", "run.ps1"), encoding="utf8"
        )
        with patch.object(host.shutil, "which", return_value="NODE"):
            self.assertIsNone(host.resolve_shim(folder / "odd.cmd"))

    def test_resolve_shim_keeps_node_options_and_refuses_other_runners(self):
        folder = shim_folder(self)
        script = str(folder / TARGETS[0])
        flagged = NEW_SHIM.replace(
            '"%_prog%"  "', '"%_prog%" --no-warnings --max-old-space-size=4096 "'
        )
        bun = NEW_SHIM.replace("node.exe", "bun.exe").replace('"_prog=node"', '"_prog=bun"')
        self.assertNotEqual(flagged, NEW_SHIM)
        self.assertNotIn("node", bun.replace("node_modules", ""))
        for name, text in (
            ("flagged.cmd", flagged),
            ("bun.cmd", bun),
            ("valued.cmd", NEW_SHIM.replace('"%_prog%"  "', '"%_prog%" -r hook "')),
            ("bare-bun.cmd", OLD_SHIM.replace("node  ", "bun  ").replace("pkg\\cli.js", "@openai\\codex\\bin\\codex.js")),
            ("direct.cmd", '@"%~dp0\\node.exe"  "%~dp0\\node_modules\\@openai\\codex\\bin\\codex.js" %*\r\n'),
            ("script-only.cmd", '"%dp0%\\node_modules\\@openai\\codex\\bin\\codex.js"   %*\r\n'),
        ):
            (folder / name).write_bytes(text.encode("utf8"))
        with patch.object(host.shutil, "which", return_value="NODE"):
            self.assertEqual(
                host.resolve_shim(folder / "flagged.cmd"),
                ["NODE", "--no-warnings", "--max-old-space-size=4096", script],
            )
            self.assertEqual(host.resolve_shim(folder / "direct.cmd"), ["NODE", script])
            for name in ("bun.cmd", "valued.cmd", "bare-bun.cmd", "script-only.cmd"):
                self.assertIsNone(host.resolve_shim(folder / name), name)
            with patch.object(host, "WINDOWS", True):
                with patch.object(
                    host.shutil,
                    "which",
                    side_effect=lambda name: "NODE" if name == "node" else str(folder / "bun.cmd"),
                ):
                    self.assertEqual(host.command("omp"), [str(folder / "bun.cmd")])

    def test_command_resolves_harness_shims_only(self):
        folder = shim_folder(self)
        shim = str(folder / "codex.cmd")

        def which(name):
            return "NODE" if name == "node" else shim

        with patch.object(host.shutil, "which", side_effect=which):
            with patch.object(host, "WINDOWS", True):
                self.assertEqual(
                    host.command("codex"), ["NODE", str(folder / TARGETS[0])]
                )
                self.assertEqual(host.command("npm"), [shim])
            with patch.object(host, "WINDOWS", False):
                self.assertEqual(host.command("codex"), [shim])

    def test_command_keeps_an_unresolved_shim_and_the_pi_launcher(self):
        folder = shim_folder(self)
        with patch.object(host, "WINDOWS", True):
            with patch.object(
                host.shutil, "which", return_value=str(folder / "gone.cmd")
            ):
                self.assertEqual(host.command("claude"), [str(folder / "gone.cmd")])
            (folder / "pi-launcher.js").write_text("x", encoding="utf8")
            with patch.object(
                host.shutil,
                "which",
                side_effect=lambda name: "NODE" if name == "node" else str(folder / "pi.cmd"),
            ):
                self.assertEqual(
                    host.command("pi"), ["NODE", str(folder / "pi-launcher.js")]
                )
        with patch.object(host.shutil, "which", return_value=None):
            with self.assertRaises(ValueError) as caught:
                host.command("codex")
        self.assertIn("codex is missing", str(caught.exception))

    def test_safe_argv_refuses_batch_metacharacters(self):
        quoted = ["x.cmd", "-c", 'model_provider="workbench"', "--model", "vendor/model:free"]
        with patch.object(host, "WINDOWS", True):
            for argv in (
                ["x.cmd", "--model", "a&calc"],
                ["x.cmd", "--append-system-prompt", "first\nsecond"],
                ["x.cmd", "--model", "%PATH%"],
                ["X.BAT", "a|b"],
            ):
                with self.assertRaises(ValueError) as caught:
                    host.safe_argv(argv)
                self.assertIn(Path(argv[0]).name, str(caught.exception))
                self.assertNotIn(argv[-1], str(caught.exception))
            self.assertIs(host.safe_argv(quoted), quoted)
            direct = ["x.exe", "a&b"]
            self.assertIs(host.safe_argv(direct), direct)
            self.assertEqual(host.safe_argv([]), [])
            with patch.object(host.shutil, "which", return_value="C:/tools/codex.CMD"):
                with self.assertRaises(ValueError):
                    host.safe_argv(["codex", "a&b"])
        with patch.object(host, "WINDOWS", False):
            for argv in (["x.cmd", "--model", "a&calc"], ["x.cmd", "a\nb"], quoted):
                self.assertIs(host.safe_argv(argv), argv)


class TerminalTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX openers")
    def test_open_folder_does_not_hand_its_stdin_to_the_opener(self):
        for platform, opener in (("darwin", "open"), ("linux", "xdg-open")):
            with (
                patch.object(sys, "platform", platform),
                patch.object(subprocess, "Popen") as popen,
            ):
                host.open_folder(Path("/tmp/my project"))
            popen.assert_called_once_with(
                [opener, "/tmp/my project"], stdin=subprocess.DEVNULL
            )

    def test_linux_terminal_needs_a_display(self):
        with (
            patch.object(sys, "platform", "linux"),
            patch.object(host, "WINDOWS", False),
            patch.object(host.shutil, "which", return_value="/usr/bin/xterm"),
            patch.object(subprocess, "Popen") as popen,
        ):
            with self.assertRaises(ValueError) as caught:
                host.terminal(["x"], env={})
            self.assertIn("--here", str(caught.exception))
            with patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""}):
                with self.assertRaises(ValueError):
                    host.terminal(["x"])
            popen.assert_not_called()

    def test_linux_terminal_uses_the_first_installed_emulator(self):
        def which(name):
            return "/usr/bin/" + name if name in ("konsole", "xterm") else None

        with (
            patch.object(sys, "platform", "linux"),
            patch.object(host, "WINDOWS", False),
            patch.object(subprocess, "Popen") as popen,
        ):
            env = {"WAYLAND_DISPLAY": "wayland-0"}
            with patch.object(host.shutil, "which", side_effect=which):
                self.assertIs(
                    host.terminal(["agent", "--flag"], cwd="/w", env=env),
                    popen.return_value,
                )
            popen.assert_called_once_with(
                ["/usr/bin/konsole", "-e", "agent", "--flag"],
                cwd="/w",
                env=env,
                stdin=subprocess.DEVNULL,
            )
            popen.reset_mock()
            with patch.object(host.shutil, "which", return_value=None):
                with self.assertRaises(ValueError) as caught:
                    host.terminal(["agent"], env={"DISPLAY": ":0"})
            self.assertIn("Install a terminal emulator", str(caught.exception))
            popen.assert_not_called()

    def test_terminal_never_runs_inline_on_a_tty(self):
        stdin = MagicMock()
        stdin.isatty.return_value = True
        with (
            patch.object(sys, "platform", "linux"),
            patch.object(host, "WINDOWS", False),
            patch.object(sys, "stdin", stdin),
            patch.object(host.shutil, "which", return_value=None),
            patch.object(subprocess, "Popen") as popen,
        ):
            with self.assertRaises(ValueError):
                host.terminal(["agent"], env={"DISPLAY": ":0"})
            popen.assert_not_called()

    def test_windows_terminal_opens_a_console_and_guards_batch_files(self):
        with (
            patch.object(host, "WINDOWS", True),
            patch.object(subprocess, "CREATE_NEW_CONSOLE", 16, create=True),
            patch.object(subprocess, "Popen") as popen,
        ):
            argv = ["python.exe", "session_window.py", "ticket", "Codex", "a&b"]
            host.terminal(argv, cwd="C:/w", env={"A": "b"})
            popen.assert_called_once_with(
                argv, cwd="C:/w", env={"A": "b"}, creationflags=16
            )
            popen.reset_mock()
            with self.assertRaises(ValueError):
                host.terminal(["codex.cmd", "a&b"])
            popen.assert_not_called()

    def test_can_open_window(self):
        with patch.object(host, "WINDOWS", True):
            self.assertTrue(host.can_open_window())
        with patch.object(host, "WINDOWS", False):
            with patch.object(sys, "platform", "linux"):
                with patch.object(host.shutil, "which", return_value="/usr/bin/xterm"):
                    with patch.dict(os.environ, {"DISPLAY": ":0"}):
                        self.assertTrue(host.can_open_window())
                    with patch.dict(
                        os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": "wayland-0"}
                    ):
                        self.assertTrue(host.can_open_window())
                    with patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""}):
                        self.assertFalse(host.can_open_window())
                with patch.object(host.shutil, "which", return_value=None):
                    with patch.dict(os.environ, {"DISPLAY": ":0"}):
                        self.assertFalse(host.can_open_window())
            with patch.object(sys, "platform", "darwin"):
                with patch.dict(os.environ, {"SSH_CONNECTION": "10.0.0.2 1 10.0.0.3 22"}):
                    self.assertFalse(host.can_open_window())
                with patch.dict(os.environ, {"SSH_CONNECTION": ""}):
                    self.assertTrue(host.can_open_window())


@unittest.skipUnless(POSIX_SOCKETS, "POSIX sockets")
class MacHandOffTests(unittest.TestCase):
    def listener(self, timeout=0.05):
        folder = tempfile.mkdtemp(prefix="awb-", dir="/tmp")
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        address = str(Path(folder) / "launch")
        listener = socket.socket(socket.AF_UNIX)
        self.addCleanup(listener.close)
        listener.bind(address)
        listener.listen(1)
        listener.settimeout(timeout)
        return listener, address

    def stand_in(self, script):
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

        def finish():
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            proc.stderr.close()

        self.addCleanup(finish)
        return proc

    def test_mac_accept_reports_refusal_quickly(self):
        listener, _ = self.listener()
        proc = self.stand_in(
            "import sys; sys.stderr.write('36:60: execution error: Not authorized to send Apple events "
            "to Terminal. (-1743)\\n'); sys.exit(1)"
        )
        began = time.monotonic()
        with self.assertRaises(ValueError) as caught:
            host.mac_accept(listener, proc)
        self.assertLess(time.monotonic() - began, 1)
        self.assertIn("Automation", str(caught.exception))

    def test_mac_accept_reports_other_failures_and_deadline(self):
        listener, _ = self.listener()
        proc = self.stand_in(
            "import sys; sys.stderr.write('warning\\nexecution error: Terminal is not running. (-600)\\n'); sys.exit(1)"
        )
        with self.assertRaises(ValueError) as caught:
            host.mac_accept(listener, proc)
        self.assertIn("Terminal could not be opened", str(caught.exception))
        self.assertIn("Terminal is not running. (-600)", str(caught.exception))
        self.assertNotIn("warning", str(caught.exception))

        sleeper = self.stand_in("import time; time.sleep(30)")
        began = time.monotonic()
        with self.assertRaises(ValueError) as caught:
            host.mac_accept(listener, sleeper, wait=0.3)
        self.assertLess(time.monotonic() - began, 2)
        self.assertIn("did not start the agent within 0.3 seconds", str(caught.exception))
        self.assertLess(sleeper.wait(2), 0)

    def test_mac_accept_names_the_exit_code_when_osascript_says_nothing(self):
        listener, _ = self.listener()
        proc = self.stand_in("raise SystemExit(4)")
        proc.wait()
        proc.stderr.close()
        proc.stderr = MagicMock()
        proc.stderr.read.return_value = ""
        with self.assertRaises(ValueError) as caught:
            host.mac_accept(listener, proc)
        self.assertIn("osascript exited with code 4", str(caught.exception))

    def test_mac_accept_returns_a_late_connection(self):
        listener, address = self.listener()
        proc = self.stand_in("pass")

        def later():
            time.sleep(0.3)
            with socket.socket(socket.AF_UNIX) as client:
                client.connect(address)
                client.recv(1)

        helper = threading.Thread(target=later, daemon=True)
        helper.start()
        peer = host.mac_accept(listener, proc, wait=5)
        self.assertIsInstance(peer, socket.socket)
        self.assertEqual(proc.poll(), 0)
        peer.close()
        helper.join(2)
        self.assertFalse(helper.is_alive())

    def test_mac_terminal_hands_the_launch_over_a_private_socket(self):
        seen = {}

        class Osascript:
            stderr = MagicMock()

            def __init__(self, argv, **options):
                seen.update(argv=argv, options=options)
                address = shlex.split(argv[-1])[-1]
                self.reader = threading.Thread(target=self.attach, args=(address,), daemon=True)
                self.reader.start()

            def attach(self, address):
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(5)
                    client.connect(address)
                    with client.makefile("r", encoding="utf8") as stream:
                        seen["payload"] = json.load(stream)

            def poll(self):
                return 0

        env = {"WORKBENCH_SESSION_KEY": "not-for-argv"}
        with patch.object(subprocess, "Popen", Osascript):
            proc = host.mac_terminal(["agent", "--model", "m"], "/w", env)
        proc.reader.join(5)
        self.assertEqual(seen["payload"], [["agent", "--model", "m"], "/w", env])
        self.assertEqual(seen["argv"][:3], ["/usr/bin/osascript", "-e", host.MAC_SCRIPT])
        helper = shlex.split(seen["argv"][3])
        self.assertEqual(helper[:2], [sys.executable, str(Path(host.__file__).resolve())])
        self.assertFalse(Path(helper[2]).parent.exists())
        self.assertNotIn("not-for-argv", " ".join(seen["argv"]))
        self.assertEqual(
            seen["options"],
            dict(
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            ),
        )
        self.assertLess(
            host.MAC_SCRIPT.index("do script"), host.MAC_SCRIPT.index("activate")
        )

    @unittest.skipUnless(sys.platform == "darwin", "the attach helper only runs on macOS")
    def test_attach_helper_reports_an_expired_launch(self):
        done = subprocess.run(
            [sys.executable, host.__file__, "/tmp/awb-expired-launch/launch"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
        self.assertEqual(done.returncode, 1)
        self.assertIn("This Workbench launch has expired", done.stderr)
        self.assertNotIn("Traceback", done.stderr)


class IdentityTests(unittest.TestCase):
    def test_process_state(self):
        rec = host.process_record(os.getpid())
        self.assertEqual(sorted(rec), ["born", "cmd", "pid"])
        self.assertRegex(rec["cmd"], r"^[0-9a-f]{16}$")
        self.assertEqual(host.process_state(rec), "live")
        moved = rec | {"born": rec["born"] + 3600}
        self.assertEqual(host.process_state(moved), "live")
        self.assertEqual(host.process_state(moved | {"cmd": "0" * 16}), "other")
        self.assertEqual(host.process_state(moved | {"cmd": ""}), "other")
        self.assertEqual(
            host.process_state({"pid": rec["pid"], "born": rec["born"] + 3600}), "other"
        )
        self.assertEqual(
            host.process_state({"pid": rec["pid"], "born": rec["born"]}), "live"
        )
        self.assertEqual(host.process_state(rec | {"pid": reaped_pid()}), "gone")
        self.assertEqual(host.process_state({}), "gone")
        self.assertEqual(host.process_state({"pid": "x", "born": 1}), "gone")
        self.assertEqual(host.process_state({"pid": rec["pid"], "born": None}), "gone")

    def test_process_state_survives_psutil_refusals(self):
        import psutil

        rec = host.process_record(os.getpid())
        with patch.object(
            psutil.Process, "create_time", side_effect=psutil.AccessDenied(rec["pid"])
        ):
            self.assertEqual(host.process_state(rec), "other")
        with patch.object(psutil.Process, "status", return_value=psutil.STATUS_ZOMBIE):
            self.assertEqual(host.process_state(rec), "gone")
        with patch.object(
            psutil.Process, "cmdline", side_effect=psutil.AccessDenied(rec["pid"])
        ):
            self.assertEqual(host.process_record(os.getpid())["cmd"], "")

    def test_fingerprint_holds_no_argument_text(self):
        proc = MagicMock()
        proc.cmdline.return_value = ["agent", "--model", "private-model-name"]
        mark = host.fingerprint(proc)
        self.assertRegex(mark, r"^[0-9a-f]{16}$")
        other = MagicMock()
        other.cmdline.return_value = ["agent", "--model", "another"]
        self.assertNotEqual(mark, host.fingerprint(other))


if __name__ == "__main__":
    unittest.main()
