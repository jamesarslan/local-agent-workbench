"""cli.py and shortcuts.py: launcher text, workspace resolution, in-terminal agent runs, the trust verb and plain error output."""

import atexit
import contextlib
import io
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

if "runtime" not in sys.modules and "WORKBENCH_DATA" not in os.environ:
    os.environ["WORKBENCH_DATA"] = tempfile.mkdtemp(prefix="awb-test-")
    atexit.register(shutil.rmtree, os.environ["WORKBENCH_DATA"], ignore_errors=True)

import cli
import connections
import host
import project_profiles
import project_setup
import runtime
import session_window
import sessions
import shortcuts
import tooling

FAKE_RUNTIME = """
from pathlib import Path
DATA = ROOT = Path(".")
URL = ""
class Cancelled(RuntimeError):
    pass
class StartFailed(RuntimeError):
    pass
def settings():
    raise %s
endpoint_model = key = owned = start = stop = settings
"""
FAKE_START = """
def settings():
    return dict(model="m.gguf", engine="llama.cpp", provider="Managed local", named_profiles={})
def start(s):
    raise %s
"""
UNREADABLE = 'ValueError("Settings could not be read; restore the settings file, then retry.")'
FAKE_TOOLING = """
def refresh_path():
    return []
"""
FAKE_DOCTOR = """
import sys
def main(argv):
    print("doctor", argv, "runtime" in sys.modules)
    return 3
"""


def terminal(tty):
    stdin = MagicMock()
    stdin.isatty.return_value = tty
    return patch.object(sys, "stdin", stdin)


class Base(unittest.TestCase):
    def folder(self):
        path = Path(tempfile.mkdtemp(prefix="awb-cli-")).resolve()
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        return path

    def main(self, *argv, settings=None):
        out = io.StringIO()
        with (
            patch.object(sys, "argv", ["cli.py", *argv]),
            patch.object(cli, "settings", return_value=settings or self.settings()) as read,
            patch.object(tooling, "refresh_path") as refresh,
            contextlib.redirect_stdout(out),
        ):
            self.settings_read, self.refresh = read, refresh
            result = cli.main()
        return result, out.getvalue()

    def settings(self, **changes):
        return dict(runtime.DEFAULTS, model="m.gguf", named_profiles={}) | changes


class LauncherTests(Base):
    @unittest.skipIf(os.name == "nt", "POSIX launchers")
    def test_launcher_per_platform(self):
        data = self.folder()
        mark = shortcuts.ident("My setup")
        with patch.object(shortcuts, "DATA", data), patch.dict(os.environ):
            os.environ.pop("WORKBENCH_DATA", None)
            with patch.object(sys, "platform", "darwin"):
                mac = shortcuts.create("My setup")
            with patch.object(sys, "platform", "linux"):
                linux = shortcuts.create("My setup")
            os.environ["WORKBENCH_DATA"] = str(data)
            with patch.object(sys, "platform", "darwin"):
                carried = shortcuts.create("Other: setup/1")
        self.assertEqual(mac.name, "My setup-" + mark[:5] + ".command")
        self.assertEqual(mac.parent, data / "launchers")
        self.assertEqual(stat.S_IMODE(mac.stat().st_mode), 0o700)
        text = mac.read_text(encoding="utf8")
        self.assertIn("agent --here --setup-id " + mark, text)
        self.assertNotIn("WORKBENCH_DATA", text)
        lines = text.splitlines()
        self.assertEqual(lines[0], "#!/bin/sh")
        self.assertEqual(
            shlex.split(lines[1]),
            ["exec", sys.executable, str(runtime.ROOT / "cli.py"), "agent", "--here", "--setup-id", mark],
        )
        self.assertEqual(linux.suffix, ".sh")
        self.assertEqual(stat.S_IMODE(linux.stat().st_mode), 0o700)
        self.assertEqual(linux.read_text(encoding="utf8"), text)
        self.assertEqual(carried.name, "Other setup1-" + shortcuts.ident("Other: setup/1")[:5] + ".command")
        self.assertIn(
            "export WORKBENCH_DATA=" + shlex.quote(str(data)) + "\n",
            carried.read_text(encoding="utf8"),
        )

    def test_posix_script_quotes_paths(self):
        # POSIX paths on every host: this script is only ever written on macOS and Linux.
        text = shortcuts.posix_script(
            "/opt/my python/bin/python", PurePosixPath("/apps/it's here"), PurePosixPath("/data/$HOME dir"), "abc", True
        )
        lines = text.splitlines()
        self.assertEqual(shlex.split(lines[1]), ["export", "WORKBENCH_DATA=/data/$HOME dir"])
        self.assertIn("'/data/$HOME dir'", lines[1])
        self.assertEqual(
            shlex.split(lines[2]),
            ["exec", "/opt/my python/bin/python", "/apps/it's here/cli.py",
             "agent", "--here", "--setup-id", "abc"],
        )

    def test_launcher_windows_text(self):
        text = shortcuts.windows_script(Path("C:/100% apps/wb"), Path("D:/data"), "abc", True)
        lines = text.splitlines()
        self.assertEqual(lines[0], "@echo off")
        self.assertEqual(lines[1], "chcp 65001 >nul")
        self.assertEqual(lines[2], 'set "WORKBENCH_DATA=' + str(Path("D:/data")) + '"')
        self.assertEqual(lines[3].count("100%%%% apps"), 2)
        self.assertNotIn("100% apps", lines[3])
        self.assertNotIn("%%%%%", lines[3])
        self.assertTrue(lines[3].startswith('call "'))
        self.assertTrue(lines[3].endswith(" agent --here --setup-id abc"))
        self.assertEqual(lines[4], "if errorlevel 1 pause")
        plain = shortcuts.windows_script(Path("C:/100% apps/wb"), Path("D:/data"), "abc", False)
        self.assertNotIn("WORKBENCH_DATA", plain)
        self.assertEqual(plain.splitlines()[2], lines[3])

    def test_launcher_windows_paths_and_data_percent(self):
        text = shortcuts.windows_script(
            PureWindowsPath("C:/Användare/wb"), PureWindowsPath("D:/50% data"), "abc", True
        )
        lines = text.splitlines()
        self.assertEqual(lines[2], 'set "WORKBENCH_DATA=D:\\50%% data"')
        both = shortcuts.windows_script(PureWindowsPath("C:/100% apps"), PureWindowsPath("D:/50% data"), "abc", True)
        once = [line.replace("%%", "%") for line in both.splitlines()]
        self.assertEqual(once[2], 'set "WORKBENCH_DATA=D:\\50% data"')
        self.assertEqual(
            once[3].replace("%%", "%"),
            'call "C:\\100% apps\\Run.cmd" "C:\\100% apps\\cli.py" agent --here --setup-id abc',
        )
        self.assertEqual(
            lines[3],
            'call "C:\\Användare\\wb\\Run.cmd" "C:\\Användare\\wb\\cli.py" agent --here --setup-id abc',
        )
        self.assertEqual(text.encode("utf8").decode("utf8"), text)


class WorkspaceTests(Base):
    def test_workspace_path_uses_caller_directory(self):
        caller = self.folder()
        (caller / "sub").mkdir()
        with patch.dict(os.environ, {"WORKBENCH_CALLER_CWD": str(caller)}):
            self.assertEqual(cli.workspace_path(Path("sub")), caller / "sub")
            self.assertEqual(cli.workspace_path(Path(".")), caller)
            self.assertEqual(cli.workspace_path(caller / "sub" / ".."), caller)
        for value in (str(caller / "missing"), "relative/folder", ""):
            with patch.dict(os.environ, {"WORKBENCH_CALLER_CWD": value}):
                self.assertEqual(cli.caller_dir(), Path.cwd())
                self.assertEqual(cli.workspace_path(Path("sub")), (Path.cwd() / "sub").resolve())

    def test_relative_workspace_reaches_the_agent_resolved(self):
        caller = self.folder()
        (caller / "proj").mkdir()
        with (
            patch.dict(os.environ, {"WORKBENCH_CALLER_CWD": str(caller)}),
            patch.object(cli, "agent", return_value=7) as agent,
        ):
            result, _ = self.main("agent", "--workspace", "proj", "--here")
        self.assertEqual(result, 7)
        used, here = agent.call_args.args
        self.assertEqual(used["workspace"], str(caller / "proj"))
        self.assertIs(here, True)

    def test_caller_directory_is_not_passed_on_to_the_agent(self):
        caller = self.folder()
        seen = []

        def record(s, here, setup=None):
            seen.append((s["workspace"], "WORKBENCH_CALLER_CWD" in os.environ))
            return 0

        for argv in (("agent", "--workspace", "."), ("agent",)):
            with (
                patch.dict(os.environ, {"WORKBENCH_CALLER_CWD": str(caller)}),
                patch.object(cli, "agent", side_effect=record),
            ):
                self.main(*argv)
        self.assertEqual(seen, [(str(caller), False), (self.settings()["workspace"], False)])

    def test_missing_workspace_is_refused_with_its_path(self):
        caller = self.folder()
        err = io.StringIO()
        with (
            patch.dict(os.environ, {"WORKBENCH_CALLER_CWD": str(caller)}),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as caught,
        ):
            self.main("agent", "--workspace", "nope")
        self.assertEqual(caught.exception.code, 2)
        self.assertIn(f"The project folder {caller / 'nope'} does not exist.", err.getvalue())
        self.settings_read.assert_not_called()


class AgentTests(Base):
    def test_agent_runs_in_place_without_a_window(self):
        s = self.settings(harness="Codex")
        prepared = (["codex", "--model", "m"], {"A": "b"}, "/proj")
        with (
            patch.object(connections, "ready_session", return_value=prepared) as ready,
            patch.object(connections, "launch") as launch,
            patch.object(session_window, "run", return_value=5) as run,
            patch.object(os, "chdir") as chdir,
        ):
            with terminal(True), patch.object(host, "can_open_window", return_value=False):
                self.assertEqual(cli.agent(s, False), 5)
            ready.assert_called_once_with(s)
            chdir.assert_called_once_with("/proj")
            ticket, harness, argv = run.call_args.args
            self.assertRegex(ticket, r"^[0-9a-f]{32}$")
            self.assertEqual((harness, argv), ("Codex", prepared[0]))
            self.assertEqual(run.call_args.kwargs, dict(env={"A": "b"}, handshake=False))
            launch.assert_not_called()

            run.reset_mock()
            with terminal(True), patch.object(host, "can_open_window", return_value=True):
                self.assertEqual(cli.agent(s, False), 0)
                launch.assert_called_once_with(s)
                run.assert_not_called()
                self.assertEqual(cli.agent(s, True), 5)
                self.assertEqual(run.call_count, 1)

            launch.reset_mock()
            run.reset_mock()
            with terminal(False), patch.object(host, "can_open_window", return_value=False):
                self.assertEqual(cli.agent(s, True), 0)
            launch.assert_called_once_with(s)
            run.assert_not_called()
            with patch.object(sys, "stdin", None), patch.object(host, "can_open_window", return_value=True):
                self.assertEqual(cli.agent(s, True), 0)
            run.assert_not_called()

    def test_agent_hint_names_the_trust_verb(self):
        s = self.settings(harness="Codex", workspace="/work/my proj")
        hint = ' From a terminal: workbench trust --workspace "/work/my proj"'
        blocked = ValueError(project_setup.NEEDS_CONSENT)
        refused = ValueError(
            "macOS blocked Agent Workbench from controlling Terminal; allow it in System Settings > "
            "Privacy & Security > Automation, then launch again."
        )
        with (
            patch.object(connections, "ready_session", side_effect=blocked),
            patch.object(connections, "launch", side_effect=blocked) as launch,
            patch.object(session_window, "run") as run,
        ):
            for tty in (True, False):
                with terminal(tty), self.assertRaises(ValueError) as caught:
                    cli.agent(s, True)
                self.assertEqual(str(caught.exception), project_setup.NEEDS_CONSENT + hint)
            run.assert_not_called()
            with terminal(False), self.assertRaises(ValueError) as caught:
                cli.agent(s, True, setup="0123456789abcdef0123")
            self.assertEqual(
                str(caught.exception),
                project_setup.NEEDS_CONSENT
                + ' From a terminal: workbench trust --setup-id 0123456789abcdef0123 --workspace "/work/my proj"',
            )
            launch.side_effect = refused
            with terminal(False), self.assertRaises(ValueError) as caught:
                cli.agent(s, False)
            self.assertIs(caught.exception, refused)

    def test_hint_folder_survives_a_paste_into_the_shell(self):
        for folder in ("/work/my proj", "/work/Användare/项目", "/work/it's"):
            self.assertEqual(cli.quoted(folder, windows=False), f'"{folder}"')
        for folder in ("/work/cash$HOME", "/work/a`id`", '/work/say "hi"', "/work/back\\slash", "/work/wow!"):
            shown = cli.quoted(folder, windows=False)
            self.assertNotEqual(shown[0], '"')
            self.assertEqual(shlex.split(shown), [folder])
        self.assertEqual(shlex.split(cli.quoted("/work/it's $HOME", windows=False)), ["/work/it's $HOME"])
        for folder in ("C:\\Users\\me\\my proj", "C:\\cash$flow\\100%"):
            self.assertEqual(cli.quoted(folder, windows=True), f'"{folder}"')

    @unittest.skipIf(os.name == "nt", "POSIX shell")
    def test_hint_folder_reaches_a_real_shell_unchanged(self):
        folder = "/work/cash$HOME `id` \"q\" é!"
        done = subprocess.run(
            ["/bin/sh", "-c", "printf %s " + cli.quoted(folder)],
            capture_output=True, text=True, encoding="utf8", stdin=subprocess.DEVNULL, timeout=10,
        )
        self.assertEqual((done.returncode, done.stdout), (0, folder))

    def test_saved_setup_hint_leads_through_the_gate(self):
        project, data = self.folder(), self.folder()
        (project / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"files": {"command": "npx", "args": ["-y", "files"]}}}), encoding="utf8"
        )
        name = "Saved"
        mark = shortcuts.ident(name)
        s = self.settings(harness="Codex", provider="Native login",
                          named_profiles={name: {"harness": "Claude Code"}})
        launched = []

        def gate(used):
            project_setup.before_launch(used)
            launched.append(used["harness"])

        def trust(*argv):
            with patch.object(cli, "on_terminal", return_value=True), patch("builtins.input", return_value="y"):
                return self.main("trust", *argv, "--workspace", str(project), settings=dict(s))

        with (
            patch.object(project_profiles, "DATA", data),
            patch.object(connections, "launch", side_effect=gate),
            terminal(False),
        ):
            with self.assertRaises(ValueError) as caught:
                self.main("agent", "--setup-id", mark, "--workspace", str(project), settings=dict(s))
            self.assertTrue(
                str(caught.exception).endswith(f'workbench trust --setup-id {mark} --workspace "{project}"')
            )
            result, out = trust()
            self.assertEqual((result, out.splitlines()[-1]), (0, "Nothing in this folder needs review."))
            with self.assertRaises(ValueError):
                self.main("agent", "--setup-id", mark, "--workspace", str(project), settings=dict(s))
            result, out = trust("--setup-id", mark)
            self.assertEqual((result, out.splitlines()[-1]), (0, "Allowed for this folder. Launch again."))
            self.assertEqual(
                self.main("agent", "--setup-id", mark, "--workspace", str(project), settings=dict(s))[0], 0
            )
        self.assertEqual(launched, ["Claude Code"])

    def test_ask_running_reads_the_terminal(self):
        with terminal(True):
            for typed, expected in (("y", True), (" YES ", True), ("n", False), ("No", False), ("", None), ("maybe", None)):
                with patch("builtins.input", return_value=typed) as asked:
                    self.assertIs(cli.ask_running(), expected)
                self.assertIn("Another setup is serving a model.", asked.call_args.args[0])
            for stop in (EOFError, KeyboardInterrupt):
                with patch("builtins.input", side_effect=stop):
                    self.assertIsNone(cli.ask_running())

    def test_ask_running_without_a_terminal_or_tk_says_what_to_do(self):
        with terminal(False), patch.dict(sys.modules, {"tkinter": None}):
            with patch("builtins.input") as asked, self.assertRaises(SystemExit) as caught:
                cli.ask_running()
        asked.assert_not_called()
        self.assertIn("run this launcher from a terminal to choose", str(caught.exception.code))

    def test_ask_running_uses_a_withdrawn_tk_root(self):
        tk = types.ModuleType("tkinter")
        tk.Tk = MagicMock()
        tk.messagebox = types.ModuleType("tkinter.messagebox")
        tk.messagebox.askyesnocancel = MagicMock(return_value=False)
        with terminal(False), patch.dict(sys.modules, {"tkinter": tk, "tkinter.messagebox": tk.messagebox}):
            self.assertIs(cli.ask_running(), False)
        tk.Tk.return_value.withdraw.assert_called_once_with()
        tk.Tk.return_value.destroy.assert_called_once_with()

    def test_saved_setup_conflict_follows_the_answer(self):
        name = "Saved"
        s = self.settings(named_profiles={name: {"context": 4096}})
        running = (object(), {"spec": {"model": "running.gguf", "context": 8192, "unknown": 1}})
        with (
            patch.object(cli, "owned", return_value=running),
            patch.object(runtime, "same_server", return_value=False),
            patch.object(cli, "agent", return_value=0) as agent,
            patch.object(subprocess, "Popen") as popen,
        ):
            with patch.object(cli, "ask_running", return_value=None):
                self.assertIsNone(self.main("agent", "--setup-id", shortcuts.ident(name), settings=dict(s))[0])
            agent.assert_not_called()
            popen.assert_not_called()
            with patch.object(cli, "ask_running", return_value=False):
                self.assertIsNone(self.main("agent", "--setup-id", shortcuts.ident(name), settings=dict(s))[0])
            agent.assert_not_called()
            self.assertEqual(popen.call_args.args[0][1], str(runtime.ROOT / "desktop_app.py"))
            with patch.object(cli, "ask_running", return_value=True):
                self.assertEqual(self.main("agent", "--setup-id", shortcuts.ident(name), settings=dict(s))[0], 0)
            used = agent.call_args.args[0]
            self.assertEqual((used["model"], used["context"]), ("running.gguf", 8192))
            self.assertNotIn("unknown", used)

    def test_removed_setup_and_missing_model_are_one_sentence(self):
        with self.assertRaises(SystemExit) as caught:
            self.main("agent", "--setup-id", "0" * 20)
        self.assertEqual(caught.exception.code, "Saved setup was removed.")
        with self.assertRaises(SystemExit) as caught:
            self.main("serve", settings=self.settings(model=""))
        self.assertEqual(caught.exception.code, "Choose a model in Agent Workbench first.")


class CommandTests(Base):
    def test_status_survives_a_model_that_is_still_starting(self):
        with (
            patch.object(cli, "owned", return_value=(object(), {"spec": {"port": 1}})),
            patch.object(cli, "endpoint_model", side_effect=OSError("refused")),
        ):
            result, out = self.main("status")
        self.assertIsNone(result)
        self.assertEqual(out, "Starting or unavailable {'port': 1}\n")
        with patch.object(cli, "owned", return_value=(None, {})):
            self.assertEqual(self.main("status")[1], "Stopped\n")

    def test_status_and_bench_output_fit_a_legacy_console_encoding(self):
        def shown(*argv, **settings):
            out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
            with (
                patch.object(sys, "argv", ["cli.py", *argv]),
                patch.object(cli, "settings", return_value=self.settings(**settings)),
                patch.object(tooling, "refresh_path"),
                contextlib.redirect_stdout(out),
            ):
                cli.main()
                out.flush()
            return out.buffer.getvalue().decode("cp1252")

        spec = {"model": "/models/張/café.gguf"}
        with (
            patch.object(cli, "owned", return_value=(object(), {"spec": spec})),
            patch.object(cli, "endpoint_model", return_value="local-agent"),
        ):
            self.assertEqual(shown("status").splitlines(), ["local-agent {'model': '/models/?/café.gguf'}"])
        with (
            patch.object(cli, "start"),
            patch.object(cli, "key", return_value="k"),
            patch.object(cli, "DATA", Path("/data/張")),
            patch.object(subprocess, "run", return_value=types.SimpleNamespace(returncode=0)),
        ):
            printed = shown("bench", context=4096).splitlines()
        self.assertEqual(len(printed), 1)
        self.assertRegex(printed[0], r"\A.data.\?.benchmark-\d{8}-\d{6}\.json\Z")

    def test_main_refreshes_the_path_except_for_doctor(self):
        order = []
        with (
            patch.object(cli, "owned", side_effect=lambda: order.append("verb") or (None, {})),
            patch.object(tooling, "refresh_path", side_effect=lambda: order.append("refresh")),
            patch.object(sys, "argv", ["cli.py", "status"]),
            patch.object(cli, "settings", return_value=self.settings()),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            cli.main()
        self.assertEqual(order, ["refresh", "verb"])
        fake = types.ModuleType("doctor")
        fake.main = MagicMock(return_value=0)
        with patch.dict(sys.modules, {"doctor": fake}):
            del sys.modules["tooling"]
            self.main("doctor")
            self.assertNotIn("tooling", sys.modules)
        self.refresh.assert_not_called()
        self.main("sessions")
        self.refresh.assert_called_once_with()

    def test_bench_passes_the_context_and_a_failed_run_exits_quietly(self):
        s = self.settings(model="/models/m.gguf", context=8192)
        done = types.SimpleNamespace(returncode=0)
        with (
            patch.object(cli, "start") as start,
            patch.object(cli, "key", return_value="secret-key"),
            patch.object(subprocess, "run", return_value=done) as run,
        ):
            result, out = self.main("bench", settings=dict(s))
            self.assertIsNone(result)
            start.assert_called_once()
            argv = run.call_args.args[0]
            self.assertEqual(argv[:3], [sys.executable, str(runtime.ROOT / "benchmark.py"), "m.gguf"])
            self.assertEqual(argv[4:], ["--context", "8192"])
            self.assertEqual(out.strip(), argv[3])
            self.assertEqual(Path(argv[3]).parent, cli.DATA)
            self.assertNotIn("secret-key", " ".join(argv))
            self.assertEqual(run.call_args.kwargs["env"]["WORKBENCH_API_KEY"], "secret-key")
            self.assertNotIn("check", run.call_args.kwargs)
            done.returncode = 1
            out = io.StringIO()
            with (
                patch.object(sys, "argv", ["cli.py", "bench"]),
                patch.object(cli, "settings", return_value=dict(s)),
                patch.object(tooling, "refresh_path"),
                contextlib.redirect_stdout(out),
                self.assertRaises(SystemExit) as caught,
            ):
                cli.main()
            self.assertEqual(caught.exception.code, 1)
            self.assertEqual(out.getvalue(), "")

    def test_serve_interrupt_and_failed_start_exit_with_a_sentence(self):
        endings = (
            (KeyboardInterrupt(), "Stopped before the model finished loading."),
            (runtime.Cancelled("The model start was cancelled."), "The model start was cancelled."),
            (runtime.StartFailed("The model server exited while loading.", ["last line"]),
             "The model server exited while loading."),
        )
        for verb in ("serve", "bench"):
            for raised, sentence in endings:
                err = io.StringIO()
                with (
                    self.subTest(verb=verb, raised=raised),
                    patch.object(cli, "start", side_effect=raised) as start,
                    patch.object(subprocess, "run") as run,
                    contextlib.redirect_stderr(err),
                ):
                    with self.assertRaises(SystemExit) as caught:
                        self.main(verb)
                    start.assert_called_once()
                    run.assert_not_called()
                    self.assertEqual(caught.exception.code, sentence)
                    self.assertIsNone(caught.exception.__cause__)
                    self.assertTrue(caught.exception.__suppress_context__)
                    self.assertEqual(err.getvalue(), "")
        with patch.object(cli, "start", side_effect=OSError("disk")), self.assertRaises(OSError):
            self.main("serve")

    def trust(self, status, tty=True, typed="y", grant=None):
        project = self.folder()
        with (
            patch.object(project_setup, "consent_status", return_value=status) as asked,
            patch.object(project_setup, "grant", side_effect=grant, return_value=True) as granted,
            patch.object(cli, "on_terminal", return_value=tty),
            patch("builtins.input", return_value=typed) as prompt,
        ):
            result, out = self.main("trust", "--workspace", str(project), settings=self.settings(harness="Codex"))
        asked.assert_called_once_with(str(project), "Codex")
        lines = out.splitlines()
        self.assertEqual(lines[0], f"Project folder: {project}")
        return result, lines[1:], granted, prompt, str(project)

    def test_trust_lists_items_and_grants_only_after_yes(self):
        status = dict(
            needed=True,
            granted=False,
            digest="d" * 64,
            items=[
                dict(path=".mcp.json", kind="mcp", read_by=["Claude Code"], relevant=False,
                     detail="Starts: files\x1b[31m (npx)\nand web"),
                dict(path=".codex/config.toml", kind="mcp", read_by=["Codex", "OMP"], relevant=True,
                     detail="Starts: build (make)"),
            ],
        )
        listed = [
            ".mcp.json (mcp, read by Claude Code): Starts: files [31m (npx) and web",
            ".codex/config.toml (mcp, read by Codex, OMP): Starts: build (make)",
        ]
        for typed in ("y", " YES "):
            result, lines, granted, prompt, project = self.trust(status, typed=typed)
            self.assertEqual(result, 0)
            self.assertEqual(lines, listed + ["Allowed for this folder. Launch again."])
            granted.assert_called_once_with(project, "d" * 64)
            self.assertIn("Allow these files to start programs", prompt.call_args.args[0])
        for typed in ("n", "", "maybe"):
            result, lines, granted, _, _ = self.trust(status, typed=typed)
            self.assertEqual(result, 1)
            self.assertEqual(lines, listed + ["Nothing was allowed."])
            granted.assert_not_called()
        result, lines, granted, prompt, _ = self.trust(status, tty=False)
        self.assertEqual((result, lines), (1, listed + ["Nothing was allowed."]))
        prompt.assert_not_called()
        granted.assert_not_called()
        for stop in (EOFError, KeyboardInterrupt):
            with (
                patch.object(project_setup, "consent_status", return_value=status),
                patch.object(project_setup, "grant") as granted,
                patch.object(cli, "on_terminal", return_value=True),
                patch("builtins.input", side_effect=stop),
                contextlib.redirect_stdout(io.StringIO()) as out,
            ):
                self.assertEqual(cli.trust("/x", "Codex"), 1)
            granted.assert_not_called()
            self.assertEqual(out.getvalue().splitlines()[-1], "Nothing was allowed.")

    def test_trust_names_the_folder_it_is_about(self):
        remembered = self.folder()
        for status, last in (
            (dict(needed=False, granted=False, digest="", items=[]), "Nothing in this folder needs review."),
            (dict(needed=True, granted=False, digest="d" * 64,
                  items=[dict(path=".mcp.json", kind="mcp", read_by=["OMP"], relevant=True, detail="x")]),
             "Nothing was allowed."),
        ):
            with (
                patch.object(project_setup, "consent_status", return_value=status) as asked,
                patch.object(project_setup, "grant") as granted,
                patch.object(cli, "on_terminal", return_value=False),
            ):
                _, out = self.main("trust", settings=self.settings(harness="OMP", workspace=str(remembered)))
            asked.assert_called_once_with(str(remembered), "OMP")
            granted.assert_not_called()
            lines = out.splitlines()
            self.assertEqual((lines[0], lines[-1]), (f"Project folder: {remembered}", last))

    def test_trust_reports_files_that_changed_while_reading(self):
        status = dict(needed=True, granted=False, digest="d" * 64,
                      items=[dict(path=".mcp.json", kind="mcp", read_by=["Claude Code"], relevant=True, detail="x")])
        changed = ValueError("These files changed while you were reading. Review them again, then allow them.")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            result, lines, granted, _, _ = self.trust(status, grant=changed)
        self.assertEqual(result, 1)
        granted.assert_called_once()
        self.assertEqual(err.getvalue().strip(), str(changed))
        self.assertNotIn("Allowed for this folder. Launch again.", lines)

    def test_trust_with_nothing_to_review(self):
        item = dict(path=".mcp.json", kind="mcp", read_by=["Claude Code"], relevant=True, detail="x")
        for status in (
            dict(needed=False, granted=False, digest="", items=[]),
            dict(needed=False, granted=True, digest="d" * 64, items=[item]),
            dict(needed=False, granted=False, digest="d" * 64, items=[item | {"relevant": False}]),
        ):
            result, lines, granted, prompt, _ = self.trust(status)
            self.assertEqual((result, lines), (0, ["Nothing in this folder needs review."]))
            granted.assert_not_called()
            prompt.assert_not_called()

    def test_sessions_lists_without_reading_settings(self):
        project = self.folder()
        live = [
            dict(id="native:1", kind="native", title="Codex \x1b[31m· m", harness="Codex",
                 workspace=str(project), pid=1, started=1.0, state="running"),
            dict(id="model", kind="model", title="m.gguf", harness="", workspace="", pid=2,
                 started=1.0, state="running"),
        ]
        past = [
            dict(id="a" * 64, harness="Claude Code", title="Add tests", updated="2026-11-22T21:02:44.000Z",
                 command="claude --resume abc"),
            dict(id="b" * 64, harness="OMP", title="Slot session", updated="2026-11-21T08:00:00.000Z", command=""),
        ]
        stored = str(project / "Stored Case")
        fake = types.ModuleType("sessions")
        fake.project_dir = MagicMock(return_value=stored)
        fake.running = MagicMock(return_value={"sessions": live, "exits": []})
        fake.recent = MagicMock(return_value=past)
        with patch.dict(sys.modules, {"sessions": fake}):
            result, out = self.main("sessions", "--workspace", str(project))
            self.assertIsNone(result)
            self.settings_read.assert_not_called()
            fake.project_dir.assert_called_once_with(str(project))
            fake.running.assert_called_once_with(stored)
            fake.recent.assert_called_once_with(stored, None, 20)
            self.assertEqual(
                out.splitlines(),
                [
                    "Running",
                    f"  native   Codex        Codex  [31m· m  {project}",
                    "  model    -            m.gguf  ",
                    f"Recent in {stored}",
                    "  2026-11-22 21:02  Claude Code  Add tests  claude --resume abc",
                    "  2026-11-21 08:00  OMP          Slot session",
                ],
            )
            _, out = self.main("sessions", "--workspace", str(project), "--json")
            self.assertEqual(json.loads(out), dict(running=live, recent=past))

            fake.project_dir.reset_mock()
            fake.running.reset_mock()
            fake.recent.reset_mock()
            fake.running.return_value = {"sessions": [], "exits": []}
            _, out = self.main("sessions")
            fake.project_dir.assert_not_called()
            fake.running.assert_called_once_with(None)
            fake.recent.assert_not_called()
            self.assertEqual(out.splitlines(), ["Running", "  none"])
            fake.recent.return_value = []
            _, out = self.main("sessions", "--workspace", str(project))
            self.assertEqual(out.splitlines(), ["Running", "  none", f"Recent in {stored}", "  none"])

    def test_sessions_uses_the_folder_as_the_desktop_stores_it(self):
        project = self.folder()
        typed = project.parent / project.name.upper()
        if not typed.is_dir():
            typed = project.parent / ("link-to-" + project.name)
            try:
                typed.symlink_to(project, target_is_directory=True)
            except OSError:
                self.skipTest("This file system has neither case folding nor links.")
            self.addCleanup(typed.unlink)
        with (
            patch.object(sessions, "running", return_value={"sessions": [], "exits": []}) as running,
            patch.object(sessions, "recent", return_value=[]) as recent,
        ):
            _, out = self.main("sessions", "--workspace", str(typed))
        self.assertEqual(sessions.project_dir(str(typed)), str(project))
        running.assert_called_once_with(str(project))
        recent.assert_called_once_with(str(project), None, 20)
        self.assertEqual(out.splitlines()[-2:], [f"Recent in {project}", "  none"])

    def test_sessions_text_fits_a_legacy_console_encoding(self):
        live = [dict(kind="native", harness="Codex", title="修正 tests \U0001f600 café", workspace="/w")]
        fake = types.ModuleType("sessions")
        fake.running = MagicMock(return_value={"sessions": live, "exits": []})
        for encoding, shown in (("cp1252", "?? tests ? café"), ("ascii", "?? tests ? caf?")):
            out = io.TextIOWrapper(io.BytesIO(), encoding=encoding)
            with patch.dict(sys.modules, {"sessions": fake}), contextlib.redirect_stdout(out):
                cli.show_sessions(None, False)
                out.flush()
            self.assertEqual(
                out.buffer.getvalue().decode(encoding).splitlines(),
                ["Running", f"  native   Codex        {shown}  /w"],
            )
        out = io.StringIO()
        with patch.dict(sys.modules, {"sessions": fake}), contextlib.redirect_stdout(out):
            cli.show_sessions(None, False)
        self.assertIn("修正 tests \U0001f600 café", out.getvalue())

    def test_doctor_through_main_passes_the_json_flag(self):
        fake = types.ModuleType("doctor")
        fake.main = MagicMock(return_value=1)
        with patch.dict(sys.modules, {"doctor": fake}):
            self.assertEqual(self.main("doctor", "--json")[0], 1)
            fake.main.assert_called_once_with(["--json"])
            self.main("doctor")
            fake.main.assert_called_with([])
            self.assertEqual(self.main("--json", "doctor")[0], 1)
            fake.main.assert_called_with(["--json"])
        self.settings_read.assert_not_called()

    def test_doctor_route_forwards_ids_and_active(self):
        fake = types.ModuleType("doctor")
        fake.main = MagicMock(return_value=2)
        with (
            patch.dict(sys.modules, {"doctor": fake}),
            patch.object(sys, "argv", ["cli.py", "status"]),
            patch.object(cli, "settings") as read,
            patch.object(tooling, "refresh_path") as refresh,
        ):
            self.assertEqual(cli.main(["doctor", "python", "--active"]), 2)
            fake.main.assert_called_once_with(["python", "--active"])
            self.assertEqual(cli.main(["doctor", "--json", "ports", "--workspace", "x"]), 2)
            fake.main.assert_called_with(["--json", "ports", "--workspace", "x"])
        read.assert_not_called()
        refresh.assert_not_called()

    def test_web_uses_wsl_exec(self):
        import filelock

        with (
            patch.object(sys, "platform", "win32"),
            patch.object(shutil, "which", return_value=None),
            patch.object(filelock, "FileLock"),
            patch.object(subprocess, "run") as run,
        ):
            cli.web(False)
        argv = run.call_args.args[0]
        self.assertEqual(run.call_count, 1)
        self.assertEqual(argv[:2], ["wsl", "-d"])
        self.assertIn("--exec", argv)
        self.assertNotIn("--", argv)
        self.assertEqual(argv[argv.index("--exec") + 1:], ["docker", "compose", "-f", "firecrawl.compose.yml", "stop"])
        self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_web_start_under_wsl_gives_no_child_its_stdin(self):
        import filelock
        import psutil
        import requests

        answer = types.SimpleNamespace(ok=True, text="Firecrawl is ready")
        with (
            patch.object(sys, "platform", "win32"),
            patch.object(shutil, "which", return_value=None),
            patch.object(filelock, "FileLock"),
            patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
            patch.object(subprocess, "run") as run,
            patch.object(subprocess, "Popen") as popen,
            patch.object(psutil, "Process"),
            patch.object(runtime, "load", return_value={}),
            patch.object(runtime, "write_json") as saved,
            patch.object(requests, "get", return_value=answer),
        ):
            cli.web(True)
        started = [call.args[0][call.args[0].index("--exec") + 1:] for call in run.call_args_list]
        self.assertEqual(
            started,
            [["service", "docker", "start"], ["docker", "compose", "-f", "firecrawl.compose.yml", "up", "-d"]],
        )
        watch = popen.call_args.args[0]
        self.assertEqual(watch[watch.index("--exec") + 1:], ["docker", "wait", "agent-workbench-web-api-1"])
        for call in [*run.call_args_list, popen.call_args]:
            self.assertIs(call.kwargs["stdin"], subprocess.DEVNULL)
        saved.assert_called_once()

    def test_web_falls_back_to_wsl_when_windows_docker_desktop_is_stopped(self):
        import filelock
        import psutil
        import requests

        answer = types.SimpleNamespace(ok=True, text="Firecrawl is ready")
        with (
            patch.object(sys, "platform", "win32"),
            patch.object(shutil, "which", return_value="C:/Program Files/Docker/docker.exe"),
            patch.object(cli, "_host_docker_ready", return_value=False),
            patch.object(cli, "_wsl_docker_available", return_value=True),
            patch.object(filelock, "FileLock"),
            patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
            patch.object(subprocess, "run") as run,
            patch.object(subprocess, "Popen") as popen,
            patch.object(psutil, "Process"),
            patch.object(runtime, "load", return_value={}),
            patch.object(runtime, "write_json"),
            patch.object(requests, "get", return_value=answer),
        ):
            cli.web(True)
        started = [call.args[0][call.args[0].index("--exec") + 1:] for call in run.call_args_list]
        self.assertEqual(
            started,
            [["service", "docker", "start"], ["docker", "compose", "-f", "firecrawl.compose.yml", "up", "-d"]],
        )
        self.assertIn("--exec", popen.call_args.args[0])

    def test_web_needs_docker_off_windows(self):
        import filelock

        with (
            patch.object(sys, "platform", "linux"),
            patch.object(shutil, "which", return_value=None),
            patch.object(filelock, "FileLock"),
            patch.object(subprocess, "run") as run,
            self.assertRaises(ValueError),
        ):
            cli.web(True)
        run.assert_not_called()


class ScriptTests(Base):
    def script(self, *argv, error=UNREADABLE, start=None):
        folder = self.folder()
        shutil.copy(runtime.ROOT / "cli.py", folder / "cli.py")
        fake = FAKE_RUNTIME % error + (FAKE_START % start if start else "")
        (folder / "runtime.py").write_text(fake, encoding="utf8")
        (folder / "doctor.py").write_text(FAKE_DOCTOR, encoding="utf8")
        (folder / "tooling.py").write_text(FAKE_TOOLING, encoding="utf8")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        return subprocess.run(
            [sys.executable, str(folder / "cli.py"), *argv],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            cwd=folder,
            env=env,
            timeout=20,
        )

    def test_doctor_starts_before_runtime_loads(self):
        done = self.script("doctor", "--json")
        self.assertEqual(done.returncode, 3)
        self.assertEqual(done.stdout.strip(), "doctor ['--json'] False")

    def test_expected_failures_print_one_sentence(self):
        done = self.script("status")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(
            done.stderr.strip(),
            "Settings could not be read; restore the settings file, then retry.",
        )

    def test_an_interrupt_prints_one_word_and_no_traceback(self):
        done = self.script("serve", error="KeyboardInterrupt")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stderr.strip(), "Cancelled.")

    def test_an_interrupted_or_failed_model_start_prints_its_sentence(self):
        done = self.script("serve", start="KeyboardInterrupt")
        self.assertEqual((done.returncode, done.stdout), (1, ""))
        self.assertEqual(done.stderr.strip(), "Stopped before the model finished loading.")
        done = self.script("bench", start='StartFailed("The model server exited while loading.")')
        self.assertEqual((done.returncode, done.stdout), (1, ""))
        self.assertEqual(done.stderr.strip(), "The model server exited while loading.")


if __name__ == "__main__":
    unittest.main()
