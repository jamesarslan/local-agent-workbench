"""The desktop bridge: action table, routing, error mapping, job lane and the wire protocol."""

import contextlib
import io
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import account_book
import catalog
import connections
import desktop_bridge as bridge
from paths import canonical
import project_profiles as projects
import runtime

ROOT = Path(__file__).resolve().parent.parent
TABLE = json.loads((ROOT / "desktop/actions.json").read_text(encoding="utf8"))
ACTIONS, TABLE_ALIASES = TABLE["actions"], TABLE["aliases"]
MAIN = (ROOT / "desktop/main.cjs").read_text(encoding="utf8")
PRELOAD = (ROOT / "desktop/preload.cjs").read_text(encoding="utf8")
SHELL_ONLY = {
    "bridge-status",
    "job-list",
    "job-cancel",
    "bridge-log-open",
    "folder-pick",
    "folder-reveal",
    "server-copy-key",
    "account-key-page",
}
LEGACY_SHAPES = {"hardware", "credential", "store-key"}
NAMES_0_3 = (
    "state visual-route hardware accounts account-save status match use-running save launch "
    "serve stop oauth store-key native-login models credential project-list project-save "
    "project-apply plugins benchmark-plugins measure copy-key directory import-plugins key-page "
    "term-history term-list term-open term-read term-write term-size term-close term-remote "
    "studio-import studio-info studio-setup-log studio-check studio-tools studio-library "
    "studio-save studio-shortcut studio-hub studio-download studio-bench studio-results "
    "studio-result studio-web studio-box studio-trace studio-trace-read studio-trace-stop "
    "studio-subscriptions studio-profile-info studio-profile-edit studio-evidence "
    "machine-list machine-save machine-pair machine-key machine-probe machine-install "
    "machine-connect machine-session machine-stop machine-remove machine-unpair"
).split()
ENV_KEYS = ("WORKBENCH_DATA", "HOME", "USERPROFILE")


def canonical(name):
    return TABLE_ALIASES.get(name, name)


def renderer_calls():
    """Every action name the renderer passes to request() as a literal first argument."""
    found = set()
    for file in sorted((ROOT / "desktop").rglob("*.js")):
        if {"node_modules", "vendor"} & set(file.relative_to(ROOT).parts):
            continue
        for call in re.findall(r"\brequest\(([^,)]*)", file.read_text(encoding="utf8")):
            found.update(re.findall(r"[\"']([a-z0-9-]+)[\"']", call))
    return found


def wait_for(check, seconds=3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(0.005)
    raise AssertionError("timed out waiting for the bridge")


class Wire:
    """Captures what the bridge writes to its reply stream."""

    def __init__(self, case):
        self.stream = io.StringIO()
        item = patch.object(bridge, "OUT", self.stream)
        item.start()
        case.addCleanup(item.stop)

    def lines(self):
        return [json.loads(x) for x in self.stream.getvalue().splitlines()]

    def reply(self, ident):
        return next((x for x in self.lines() if x.get("id") == ident), None)

    def events(self):
        return [x for x in self.lines() if "event" in x]


class BridgeCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.data = self.tmp / "data"
        self.folder = self.tmp / "project"
        self.data.mkdir()
        self.folder.mkdir()
        self.quiet = io.StringIO()
        hush = contextlib.redirect_stderr(self.quiet)
        hush.__enter__()
        self.addCleanup(hush.__exit__, None, None, None)

    def use(self, target, name, **how):
        item = patch.object(target, name, **how)
        made = item.start()
        self.addCleanup(item.stop)
        return made

    def saved(self, **values):
        base = dict(workspace=str(self.folder), library=str(self.tmp / "none"))
        return runtime.DEFAULTS | base | values

    def neighbours(self, **values):
        """Stand-ins for everything state() and the launch family read from other modules."""
        s = self.saved(**values)
        self.use(bridge, "DATA", new=self.data)
        self.use(runtime, "DATA", new=self.data)
        self.use(runtime, "settings", return_value=s)
        self.use(runtime, "preset_rows", return_value=[])
        self.use(runtime, "owned", return_value=(None, {}))
        self.use(bridge, "choices", return_value={})
        self.use(catalog, "hardware", return_value=("Fixture GPU", 8.0, 4.0, 16.0))
        self.use(catalog, "unified_memory", return_value=False)
        self.use(account_book, "available", return_value=[])
        self.use(projects, "registry", return_value={})
        self.view = self.use(projects, "folder_view", return_value={"key": "fixture"})
        self.which = self.use(bridge.shutil, "which", return_value=None)
        return s


class TableTest(unittest.TestCase):
    def test_table_is_well_formed(self):
        self.assertEqual(TABLE["protocol"], bridge.PROTOCOL)
        for name, row in ACTIONS.items():
            with self.subTest(name):
                self.assertIn(row["class"], ("quick", "standard", "slow", "long"))
                self.assertIn(row.get("via"), (None, "main", "internal"))
                self.assertLessEqual(
                    set(row), {"class", "via", "events", "cancel", "retry"}
                )
                for flag in ("events", "cancel", "retry"):
                    self.assertIn(row.get(flag), (None, True))
                if row.get("cancel"):
                    self.assertIn(row["class"], ("slow", "long"))
                    self.assertNotIn("retry", row)
        for old, new in TABLE_ALIASES.items():
            self.assertIn(new, ACTIONS)
            self.assertNotIn(old, ACTIONS)

    def test_names_are_prefix_verb(self):
        for name in set(ACTIONS) - LEGACY_SHAPES:
            self.assertRegex(name, r"^[a-z]+(-[a-z]+)+$")

    def test_every_action_has_an_owner(self):
        for name in set(ACTIONS) - SHELL_ONLY:
            self.assertRegex(bridge.owner(name), r"^[a-z_]+$", name)
        self.assertLessEqual(bridge.INLINE | bridge.PLUGIN_AREA, set(ACTIONS))
        self.assertLessEqual(bridge.ALIASES.items(), TABLE_ALIASES.items())
        self.assertEqual(
            set(TABLE_ALIASES) - set(bridge.ALIASES), {"directory", "copy-key", "key-page"}
        )

    def test_every_routed_action_is_handled_by_its_owner(self):
        sources = {}
        for name in set(ACTIONS) - SHELL_ONLY:
            area = bridge.owner(name)
            if area not in sources:
                sources[area] = (ROOT / (area + ".py")).read_text(encoding="utf8")
            # studio answers studio-profile-info as the other half of its studio-profile- branch.
            needle = "studio-profile-" if name == "studio-profile-info" else name
            self.assertIn(f'"{needle}"', sources[area], f"{name} is not handled in {area}.py")
        for area in ("studio", "catalog", "onboarding"):
            self.assertRegex(
                sources[area], r"def dispatch\(action, \w+, progress=None, cancel=None\)"
            )

    def test_main_handles_every_mediated_action(self):
        for name, row in ACTIONS.items():
            if row.get("via") == "main":
                self.assertIn(f'"{name}": async', MAIN)
        for line in (
            "if (event.senderFrame.url !== page || event.sender !== win.webContents)",
            'throw new Error("Unknown desktop view");',
            "contextIsolation: true,",
            "sandbox: true,",
            "nodeIntegration: false,",
            'found.spec.via === "internal"',
        ):
            self.assertIn(line, MAIN)
        for banned in ("no-sandbox", "webSecurity", "allowRunningInsecureContent"):
            self.assertNotIn(banned, MAIN)

    def test_preload_exposes_three_functions_and_one_channel(self):
        exposed = re.findall(r"^  (\w+): ", PRELOAD, re.M)
        self.assertEqual(exposed, ["filePath", "request", "subscribe"])
        self.assertEqual(PRELOAD.count("exposeInMainWorld"), 1)
        self.assertEqual(re.findall(r"ipcRenderer\.(\w+)\(", PRELOAD), ["on", "invoke"])

    def test_owner_rejects_unknown_names(self):
        with self.assertRaisesRegex(ValueError, r"^Unknown desktop action\.$"):
            bridge.owner("nope")
        self.assertEqual(bridge.owner("project-list"), "desktop_bridge")
        self.assertEqual(bridge.owner("project-setup-plan"), "project_setup")
        self.assertEqual(bridge.owner("plugin-remove"), "project_profiles")
        self.assertEqual(bridge.owner("plugin-list"), "desktop_bridge")
        self.assertEqual(bridge.owner("model-download"), "catalog")
        self.assertEqual(bridge.owner("machine-forwards"), "machines")

    def test_every_0_3_name_and_renderer_call_still_resolves(self):
        called = renderer_calls()
        self.assertTrue(called)
        for name in set(NAMES_0_3) | called:
            with self.subTest(name):
                target = canonical(name)
                self.assertIn(target, ACTIONS)
                row = ACTIONS[target]
                self.assertNotEqual(row.get("via"), "internal")
                if row.get("via") == "main":
                    self.assertIn(f'"{target}": async', MAIN)
                else:
                    bridge.owner(target)


class ErrorTest(BridgeCase):
    def test_describe_maps_exceptions(self):
        describe = bridge.describe
        cancelled = type("Cancelled", (RuntimeError,), {})
        self.assertEqual(
            describe(cancelled("The model start was cancelled.")),
            {"message": "The model start was cancelled.", "code": "cancelled"},
        )
        self.assertEqual(describe(cancelled())["message"], "Cancelled.")
        self.assertEqual(describe(runtime.Cancelled("Stopped."))["code"], "cancelled")
        self.assertEqual(
            describe(type("DownloadCancelled", (Exception,), {})("x"))["code"], "cancelled"
        )

        late = describe(subprocess.TimeoutExpired(["/usr/bin/ssh", "h"], 240))
        self.assertTrue(late["message"].startswith("ssh did not finish within 240 seconds."))
        self.assertEqual(late["action"], "retry")

        failed = describe(subprocess.CalledProcessError(1, ["docker"], stderr="boom"))
        self.assertTrue(failed["message"].startswith("docker failed with exit code 1."))
        self.assertIn("boom", failed["detail"])
        self.assertEqual(failed["action"], "doctor")
        killed = describe(subprocess.CalledProcessError(-9, "tool --flag"))
        self.assertIn("tool was stopped by signal 9", killed["message"])

        network = type("ConnectionError", (OSError,), {"__module__": "requests.exceptions"})
        self.assertEqual(describe(network("refused"))["action"], "retry")
        self.assertIn("network request", describe(network("refused"))["message"])
        held = type("Timeout", (TimeoutError,), {"__module__": "filelock._error"})
        self.assertEqual(describe(held("x"))["code"], "busy")

        plain = describe(ValueError("Choose a folder."))
        self.assertEqual(plain, {"message": "Choose a folder."})
        self.assertEqual(describe(RuntimeError("Stop it first.")), {"message": "Stop it first."})
        long = describe(ValueError("First.\nmore"))
        self.assertEqual((long["message"], long["detail"]), ("First.", "First.\nmore"))
        self.assertNotIn("code", long)
        tailed = describe(runtime.StartFailed("It exited.", ["line 1", "line 2"]))
        self.assertEqual(
            (tailed["message"], tailed["detail"], tailed["action"]),
            ("It exited.", "line 1\nline 2", "log"),
        )
        missing = describe(runtime.EngineMissing("llama.cpp", "Install the engine."))
        self.assertEqual(missing, {"message": "Install the engine.", "action": "setup"})

        for exc in (
            KeyError("harness"),
            ValueError(),
            RuntimeError(""),
            json.JSONDecodeError("bad", "{", 0),
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"),
            NotImplementedError("later"),
            TypeError("oops"),
            OSError("no file name"),
        ):
            with self.subTest(type(exc).__name__):
                found = describe(exc)
                self.assertEqual((found["code"], found["action"]), ("internal", "log"))
                self.assertEqual(found["message"], bridge.INTERNAL)
                self.assertTrue(found["detail"])

        timed = describe(TimeoutError())
        self.assertTrue(timed["message"])
        self.assertEqual(timed["action"], "retry")
        gone = describe(FileNotFoundError(2, "No such file", "/x/y"))
        self.assertTrue(gone["message"].startswith("/x/y was not found."))
        denied = describe(PermissionError(13, "Permission denied", "/x/z"))
        self.assertIn("/x/z could not be used (Permission denied)", denied["message"])
        for found in (late, failed, plain, tailed, gone, denied, timed):
            json.dumps(found)
            self.assertLessEqual(len(found.get("detail", "")), 4000)

    def test_first_line_and_program(self):
        self.assertEqual(bridge.first_line("\n\n  one  \ntwo"), "one")
        self.assertEqual(len(bridge.first_line("x" * 900)), 300)
        self.assertEqual(bridge.program(["C:/Tools/ssh.exe", "-v"]), "ssh.exe")
        self.assertEqual(bridge.program([]), "A helper program")

    def test_answer_writes_value_and_error(self):
        wire = Wire(self)
        with patch.object(bridge, "dispatch", return_value={"ok": 1}):
            bridge.answer({"id": 1, "action": "app-status", "data": {}})
        with patch.object(bridge, "dispatch", side_effect=ValueError("Choose a folder.")):
            bridge.answer({"id": 2, "action": "launch-save", "data": {}})
        with patch.object(bridge, "dispatch", return_value={"path": Path("/x")}):
            bridge.answer({"id": 3, "action": "app-state", "data": {}})
        with patch.object(bridge, "dispatch", return_value={"free": float("nan")}):
            bridge.answer({"id": 4, "action": "hardware", "data": {}})
        self.assertEqual(wire.reply(1), {"id": 1, "value": {"ok": 1}})
        self.assertEqual(wire.reply(2), {"id": 2, "error": {"message": "Choose a folder."}})
        for ident in (3, 4):
            self.assertEqual(wire.reply(ident)["error"]["code"], "internal")
        self.assertEqual(len(wire.lines()), 4)
        self.assertNotIn("Choose a folder.", self.quiet.getvalue())
        self.assertIn("Traceback", self.quiet.getvalue())

    def test_a_handler_that_ends_its_thread_is_still_answered(self):
        import asyncio

        wire = Wire(self)
        for ident, ending in enumerate((SystemExit("Docker is not running."), asyncio.CancelledError())):
            with patch.object(bridge, "dispatch", side_effect=ending):
                bridge.answer({"id": ident, "action": "plugin-measure", "data": {}})
            self.assertEqual(
                (wire.reply(ident)["error"]["message"], wire.reply(ident)["error"]["code"]),
                (bridge.INTERNAL, "internal"),
            )
        with patch.object(bridge, "dispatch", side_effect=SystemExit(3)):
            bridge.accept(
                json.dumps({"id": 7, "action": "plugin-measure", "handle": "m", "lane": "job"}),
                Mock(),
            )
            wait_for(lambda: wire.reply(7))
        self.assertEqual(wire.reply(7)["error"]["code"], "internal")
        wait_for(lambda: not bridge.ACTIVE)

    def test_an_error_that_cannot_be_described_is_still_answered(self):
        wire = Wire(self)
        with (
            patch.object(bridge, "dispatch", side_effect=KeyError("x")),
            patch.object(bridge, "describe", side_effect=TypeError("no text")),
        ):
            bridge.answer({"id": 1, "action": "app-status", "data": {}})
        self.assertEqual(
            wire.reply(1), {"id": 1, "error": {"message": bridge.INTERNAL, "code": "internal", "action": "log"}}
        )

    def test_answer_passes_the_canonical_name(self):
        Wire(self)
        seen = []
        with patch.object(bridge, "dispatch", side_effect=lambda a, *rest: seen.append(a)):
            bridge.answer({"id": 1, "action": "serve", "data": {}})
        self.assertEqual(seen, ["server-start"])

    def test_send_without_stream_is_silent(self):
        with patch.object(bridge, "OUT", None):
            bridge.send({"id": 1, "value": True})
            bridge.emit("job-progress", {"handle": "x"})
            bridge.reporter("x", "server-start")({"phase": "a"})

    def test_send_survives_a_closed_stream_and_bad_values(self):
        stream = io.StringIO()
        with patch.object(bridge, "OUT", stream):
            bridge.emit("job-progress", {"bad": object()})
            bridge.emit("job-progress", {"rate": float("inf")})
            self.assertEqual(stream.getvalue(), "")
            stream.close()
            bridge.send({"id": 1, "value": True})


class LaneTest(BridgeCase):
    def test_job_lane_progress_and_cancel(self):
        wire = Wire(self)
        seen = {}

        def job(action, payload, progress=None, cancel=None):
            seen.update(action=action, payload=payload)
            progress({"job": "model-start", "phase": "loading", "elapsed": 1})
            cancel.wait(5)
            raise runtime.Cancelled("The model start was cancelled.")

        request = {"action": "server-start", "data": {"preset": "x"}, "handle": "h1", "lane": "job"}
        with patch.object(bridge, "dispatch", job):
            bridge.accept(json.dumps(request | {"id": 1}), None)
            event = wait_for(wire.events)[0]
            self.assertEqual(
                event,
                {
                    "event": "job-progress",
                    "data": {
                        "handle": "h1",
                        "action": "server-start",
                        "progress": {"job": "model-start", "phase": "loading", "elapsed": 1},
                    },
                },
            )
            bridge.accept(json.dumps(request | {"id": 2}), None)
            self.assertEqual(wire.reply(2)["error"], {"message": bridge.RUNNING, "code": "busy"})
            bridge.accept('{"id": 3, "action": "job-cancel", "data": {"handle": "h1"}}', None)
            self.assertEqual(wire.reply(3), {"id": 3, "value": True})
            done = wait_for(lambda: wire.reply(1))
        self.assertEqual(
            done["error"], {"message": "The model start was cancelled.", "code": "cancelled"}
        )
        self.assertEqual(seen, {"action": "server-start", "payload": {"preset": "x"}})
        wait_for(lambda: not bridge.ACTIVE)
        bridge.accept('{"id": 4, "action": "job-cancel", "data": {"handle": "h1"}}', None)
        bridge.accept('{"id": 5, "action": "job-cancel", "data": []}', None)
        self.assertEqual(wire.reply(4), {"id": 4, "value": False})
        self.assertEqual(wire.reply(5), {"id": 5, "value": False})

    def test_job_limit(self):
        wire = Wire(self)
        release = threading.Event()
        self.addCleanup(release.set)

        def job(action, payload, progress=None, cancel=None):
            release.wait(5)
            return True

        with patch.object(bridge, "dispatch", job), patch.object(bridge, "JOB_LIMIT", 1):
            bridge.accept('{"id": 1, "action": "term-open", "handle": "a", "lane": "job"}', None)
            bridge.accept('{"id": 2, "action": "term-open", "handle": "b", "lane": "job"}', None)
            self.assertEqual(wire.reply(2)["error"], {"message": bridge.BUSY, "code": "busy"})
            self.assertIn("Too many tasks are running.", bridge.BUSY)
            release.set()
            self.assertEqual(wait_for(lambda: wire.reply(1)), {"id": 1, "value": True})
            wait_for(lambda: not bridge.ACTIVE)

    def test_pool_requests_get_no_token(self):
        wire = Wire(self)
        seen = []

        def handler(action, payload, progress=None, cancel=None):
            seen.append((action, payload, callable(progress), cancel))
            return "done"

        with patch.object(bridge, "dispatch", handler), ThreadPoolExecutor(1) as pool:
            bridge.accept('{"id": 7, "action": "app-status", "data": {"a": 1}}', pool)
            bridge.accept('{"id": 8, "action": "app-status", "lane": "pool"}', pool)
        self.assertEqual(seen[0], ("app-status", {"a": 1}, True, None))
        self.assertEqual(seen[1], ("app-status", None, True, None))
        self.assertEqual(wire.reply(7), {"id": 7, "value": "done"})
        self.assertEqual(bridge.ACTIVE, {})

    def test_progress_is_throttled_per_phase(self):
        wire = Wire(self)
        clock = Mock(monotonic=Mock(return_value=50.0))
        with patch.object(bridge, "time", clock):
            report = bridge.reporter("h", "engine-install")
            for done in range(5):
                report({"job": "engine-install", "phase": "download", "done": done})
            report({"job": "engine-install", "phase": "extract"})
            report("not a dict")
            report(None)
            clock.monotonic.return_value = 50.2
            report({"job": "engine-install", "phase": "extract", "done": 9})
        shown = [e["data"]["progress"] for e in wire.events()]
        self.assertEqual(
            shown,
            [
                {"job": "engine-install", "phase": "download", "done": 0},
                {"job": "engine-install", "phase": "extract"},
                {"job": "engine-install", "phase": "extract", "done": 9},
            ],
        )

    def test_malformed_lines_are_skipped(self):
        wire = Wire(self)
        pool = Mock()
        nested = '{"id": 1, "action": "app-status", "data": ' + "[" * 100000 + "]" * 100000 + "}"
        listed = '{"id": 2, "action": "app-status", "data": [1]}'
        deep = '{"id": 3, "action": "app-status", "data": ' + '{"a": ' * 100000 + "1" + "}" * 100000 + "}"
        for text in ("garbage", "[1]", "{}", '{"action": "app-state"}', "", "null", nested, listed):
            bridge.accept(text, pool)
        self.assertEqual(wire.lines(), [])
        pool.submit.assert_not_called()
        self.assertEqual(
            self.quiet.getvalue().count("desktop_bridge: ignored a malformed request line"), 8
        )
        self.assertNotIn("garbage", self.quiet.getvalue())
        bridge.accept(deep, pool)
        self.assertEqual(wire.lines(), [])

    def test_missing_or_null_data_is_still_accepted(self):
        Wire(self)
        pool = Mock()
        bridge.accept('{"id": 4, "action": "app-status"}', pool)
        bridge.accept('{"id": 5, "action": "app-status", "data": null}', pool)
        self.assertEqual([c.args[1]["id"] for c in pool.submit.call_args_list], [4, 5])

    def test_supply_passes_only_accepted_keywords(self):
        def both(a, progress=None, cancel=None):
            return a, progress, cancel

        def one(a, cancel=None):
            return a, cancel

        def none(a, b):
            return a, b

        self.assertEqual(bridge.supply(both, 1, progress="p", cancel="c"), (1, "p", "c"))
        self.assertEqual(bridge.supply(one, 1, progress="p", cancel="c"), (1, "c"))
        self.assertEqual(bridge.supply(none, 1, 2, progress="p", cancel="c"), (1, 2))
        plain = Mock(return_value=5)
        self.assertEqual(bridge.supply(plain, 1, progress="p", cancel="c"), 5)
        plain.assert_called_once_with(1)


class RoutingTest(BridgeCase):
    def test_aliases_reach_the_same_handler(self):
        self.neighbours(provider="OpenRouter", model="saved.gguf")
        start = self.use(runtime, "start")
        write = self.use(runtime, "write_json")
        for name in ("serve", "server-start"):
            self.assertIs(bridge.dispatch(name, {"provider": "OpenRouter", "preset": "x"}), True)
        self.assertEqual(start.call_count, 2)
        for call in start.call_args_list:
            self.assertEqual(call.kwargs, {})
            self.assertEqual(call.args[0]["provider"], "Managed local")
            self.assertEqual(call.args[0]["model"], "saved.gguf")
        write.assert_not_called()

    def test_start_and_stop_pass_progress_and_cancel(self):
        self.neighbours()
        seen = {}

        def start(s, progress=None, cancel=None):
            seen["start"] = (progress, cancel)

        def stop(progress=None, cancel=None, run=None):
            seen["stop"] = (progress, cancel, run)

        self.use(runtime, "start", new=start)
        self.use(runtime, "stop", new=stop)
        bridge.dispatch("server-start", {}, "P", "C")
        bridge.dispatch("stop", None, "P", "C")
        self.assertEqual(seen, {"start": ("P", "C"), "stop": ("P", None, None)})

    def test_engine_actions(self):
        import setup_engines

        info = self.use(setup_engines, "info", return_value={"backend": "cpu"})
        seen = []

        def install(update=False, backend=None, strata=None, progress=None, cancel=None):
            seen.append((update, backend, strata, progress, cancel))
            return {"installed": []}

        self.use(setup_engines, "install", new=install)
        self.assertEqual(bridge.dispatch("engine-info", {"backend": "cpu"}), {"backend": "cpu"})
        info.assert_called_once_with("cpu")
        bridge.dispatch("engine-info", {})
        info.assert_called_with(None)
        self.assertEqual(
            bridge.dispatch("engine-install", {"update": 1}, "P", "C"), {"installed": []}
        )
        self.assertEqual(seen, [(True, None, None, "P", "C")])

    def test_engine_install_passes_the_release(self):
        import setup_engines

        install = self.use(setup_engines, "install", return_value={"installed": []})
        bridge.dispatch("engine-install", {"release": "unsloth"}, "P", "C")
        install.assert_called_once_with(
            False, None, None, progress="P", cancel="C", release="unsloth"
        )

    def test_area_actions_get_progress_only_when_they_take_it(self):
        fake = {
            "studio": types.SimpleNamespace(
                dispatch=lambda action, p, progress=None, cancel=None: (action, progress, cancel)
            ),
            "machines": types.SimpleNamespace(dispatch=lambda action, p: (action, p)),
        }
        with patch.dict(sys.modules, fake):
            self.assertEqual(
                bridge.dispatch("studio-setup", {"name": "OMP"}, "P", "C"),
                ("studio-setup", "P", "C"),
            )
            self.assertEqual(
                bridge.dispatch("machine-probe", {"host": "box"}, "P", "C"),
                ("machine-probe", {"host": "box"}),
            )
            self.assertEqual(bridge.dispatch("machine-list", "not a dict"), ("machine-list", {}))

    def test_term_open_agent_remembers_folder(self):
        remember = self.use(bridge, "remember")
        terminal = types.SimpleNamespace(dispatch=Mock(return_value="a" * 24))
        with patch.dict(sys.modules, {"project_terminal": terminal}):
            agent = {"workspace": str(self.folder), "kind": "agent", "harness": "OMP"}
            self.assertEqual(bridge.dispatch("term-open", agent), "a" * 24)
            remember.assert_called_once_with(str(self.folder), agent)
            bridge.dispatch("term-open", {"workspace": str(self.folder), "kind": "shell"})
            bridge.dispatch("term-read", {"workspace": str(self.folder), "kind": "agent"})
            self.assertEqual(remember.call_count, 1)
            terminal.dispatch.side_effect = ValueError("Choose a supported agent.")
            with self.assertRaises(ValueError):
                bridge.dispatch("term-open", agent)
            self.assertEqual(remember.call_count, 1)

    def test_remember_never_fails_the_caller(self):
        for problem in (OSError("disk"), ValueError("Choose an existing project folder.")):
            with patch.object(projects, "remember", side_effect=problem) as keep:
                bridge.remember("/x", {"harness": "OMP"})
                keep.assert_called_once_with("/x", {"harness": "OMP"})

    def test_missing_area_is_one_sentence(self):
        with patch.object(bridge.importlib, "import_module", side_effect=ImportError("gone")):
            with self.assertRaisesRegex(ValueError, "not included in this build"):
                bridge.dispatch("kit-list", {})
        with patch.dict(sys.modules, {"starter_kits": types.SimpleNamespace()}):
            with self.assertRaises(ValueError) as caught:
                bridge.dispatch("kit-list", {})
        self.assertEqual(str(caught.exception), bridge.MISSING_AREA)
        with self.assertRaisesRegex(ValueError, r"^Unknown desktop action\.$"):
            bridge.dispatch("nope", {})

    def test_keys_and_models_use_bound_settings(self):
        self.neighbours(provider="OpenRouter")
        self.use(connections, "bound", side_effect=lambda s: s | {"marked": True})
        store = self.use(connections, "store_key", return_value="vault")
        secret = self.use(connections, "secret", return_value="k")
        discover = self.use(connections, "discover", return_value=["m"])
        row = {"provider": "Attached endpoint", "endpoint": "http://127.0.0.1:9/v1", "connection_id": "c1", "name": "ignored"}
        self.assertIs(bridge.dispatch("store-key", row | {"key": "  secret-value  "}), True)
        self.assertIs(bridge.dispatch("credential", row), True)
        self.assertEqual(bridge.dispatch("models", row), ["m"])
        for used in (store.call_args.args[0], secret.call_args.args[0], discover.call_args.args[0]):
            self.assertTrue(used["marked"])
            self.assertEqual(
                (used["provider"], used["endpoint"], used["connection_id"]),
                ("Attached endpoint", "http://127.0.0.1:9/v1", "c1"),
            )
            self.assertNotIn("name", used)
        self.assertEqual(store.call_args.args[1], "secret-value")
        secret.side_effect = ValueError("No key.")
        self.assertIs(bridge.dispatch("credential", row), False)
        for payload in (row, row | {"key": "   "}, row | {"key": None}):
            with self.assertRaisesRegex(ValueError, r"^Enter the provider API key\.$"):
                bridge.dispatch("store-key", payload)
        self.assertEqual(store.call_count, 1)
        self.assertNotIn("secret-value", self.quiet.getvalue())

    def test_login_validates_and_covers_opencode(self):
        import host
        import tooling

        terminal = self.use(host, "terminal")
        self.use(host, "command", side_effect=lambda name: [name])
        setup = self.use(tooling, "setup")
        self.use(connections, "HARNESS", new=[*connections.HARNESS, "opencode"])
        with self.assertRaisesRegex(ValueError, r"^Choose a supported agent\.$"):
            bridge.dispatch("native-login", {"harness": "Nope"})
        with self.assertRaisesRegex(ValueError, r"^Choose a supported agent\.$"):
            bridge.dispatch("account-login", {})
        self.assertIs(bridge.dispatch("account-login", {"harness": "opencode"}), True)
        terminal.assert_called_once_with(["opencode", "auth", "login"])
        bridge.dispatch("native-login", {"harness": "OMP", "auth_provider": ""})
        self.assertEqual(terminal.call_args.args[0], ["omp", "login", "openai-codex-device"])
        bridge.dispatch("native-login", {"harness": "Hermes", "auth_provider": "openai-codex-device"})
        self.assertEqual(terminal.call_args.args[0][:4], ["hermes", "auth", "add", "openai-codex"])
        bridge.dispatch("native-login", {"harness": "Claude Code"})
        setup.assert_called_once_with("Claude Code", "Login")
        opened = terminal.call_count
        with patch.object(connections, "clean_auth", side_effect=ValueError("Choose a subscription from the list.")):
            with self.assertRaisesRegex(ValueError, "Choose a subscription"):
                bridge.dispatch("native-login", {"harness": "OMP", "auth_provider": "x; rm"})
        with self.assertRaises(ValueError):
            bridge.dispatch("native-login", {"harness": "OMP", "auth_provider": "x; rm -rf"})
        self.assertEqual(terminal.call_count, opened)

    def test_plugin_import_reads_the_manifest_through_the_projects_area(self):
        read = self.use(projects, "load_manifest", return_value={"mcpServers": {"a": {}}})
        register = self.use(projects, "register", return_value=["a"])
        self.assertEqual(bridge.dispatch("import-plugins", {"file": "/x/mcp.json"}), ["a"])
        read.assert_called_once_with(Path("/x/mcp.json"))
        register.assert_called_once_with({"mcpServers": {"a": {}}})


class StateTest(BridgeCase):
    def test_state_keeps_the_0_3_keys(self):
        s = self.neighbours()
        (self.data / "launcher.json").write_text(
            json.dumps({"preset": "", "recent": [str(self.folder), str(self.tmp / "gone"), 7]})
        )
        found = bridge.dispatch("state", {})
        self.assertEqual(found, bridge.dispatch("app-state", None))
        self.assertLessEqual(
            {
                "settings", "presets", "selected", "current", "recent", "gpu", "total", "free",
                "ram", "providers", "accounts", "unified", "windows", "registered_plugins",
                "cli_present", "agents",
            },
            set(found),
        )
        self.assertEqual(found["settings"], {k: s[k] for k in bridge.UI_KEYS})
        self.assertEqual(found["recent"], [str(self.folder)])
        self.assertEqual((found["gpu"], found["total"], found["ram"]), ("Fixture GPU", 8.0, 16.0))
        self.assertEqual(found["agents"], connections.HARNESS)
        self.assertEqual(found["warnings"], [])
        json.dumps(found)

    def test_state_survives_custom_engine_without_manifest(self):
        self.neighbours(engine="Custom", model="")
        self.use(bridge, "choices", return_value={"Mine": {"model": "", "engine": "Custom"}})
        self.use(runtime, "serving_spec", side_effect=IsADirectoryError(21, "Is a directory"))
        found = bridge.state()
        self.assertIn(found["selected"], ("Mine", ""))
        self.assertEqual(found["presets"]["Mine"]["engine"], "Custom")
        self.assertEqual(found["warnings"], [])

    def test_state_turns_broken_parts_into_warnings(self):
        self.neighbours()
        self.use(catalog, "hardware", side_effect=KeyError("gpu"))
        self.use(account_book, "available", side_effect=ValueError("Vault locked."))
        found = bridge.state()
        self.assertEqual((found["gpu"], found["total"], found["accounts"]), ("", 0.0, []))
        self.assertEqual(len(found["warnings"]), 2)
        self.assertTrue(found["warnings"][0].startswith("Hardware details could not be loaded."))
        self.assertEqual(
            found["warnings"][1], "Connections could not be loaded. Vault locked."
        )

    def test_damaged_registry_and_launcher_are_warnings(self):
        self.neighbours()
        sentence = "The plugin registry could not be read; move that file aside."
        self.use(projects, "registry", side_effect=ValueError(sentence))
        (self.data / "launcher.json").write_text("{broken")
        found = bridge.state()
        self.assertEqual((found["registered_plugins"], found["recent"]), ([], []))
        self.assertEqual(len(found["warnings"]), 2)
        self.assertIn(str(self.data / "launcher.json"), found["warnings"][0])
        self.assertEqual(
            found["warnings"][1], "Registered plugins could not be loaded. " + sentence
        )
        listed = bridge.dispatch("plugins", {})
        self.assertEqual(listed["names"], [])
        self.assertEqual(listed["warnings"], [found["warnings"][1]])
        self.assertIn("measurement", listed)

    def test_plugin_list_keeps_its_shape(self):
        self.neighbours()
        self.use(projects, "registry", return_value={"art": {}, "db": {}})
        listed = bridge.dispatch("plugin-list", {})
        self.assertEqual((listed["names"], listed["warnings"]), (["art", "db"], []))
        self.assertIsInstance(listed["measurement"], dict)

    def test_state_lists_every_harness_and_the_folder(self):
        self.neighbours()
        cli = {"OMP": "omp", "opencode": "opencode"}
        self.use(connections, "HARNESS_CLI", new=cli)
        self.which.side_effect = lambda name: "/bin/x" if name == "opencode" else None
        found = bridge.state()
        self.assertEqual(found["cli_present"], {"OMP": False, "opencode": True})
        self.assertEqual(found["folder"], {"key": "fixture"})
        self.view.assert_called_once_with(str(self.folder), strict=False)
        self.assertEqual(found["data"], str(self.data))
        self.view.side_effect = OSError("locked")
        self.assertIsNone(bridge.state()["folder"])

    def test_state_sends_the_canonical_home_folder(self):
        self.neighbours()
        real = self.tmp / "real home"
        real.mkdir()
        roundabout = self.tmp / "real home" / ".." / "real home"
        with patch.object(Path, "home", return_value=roundabout):
            self.assertEqual(bridge.state()["home"], str(canonical(real)))
        with patch.object(Path, "home", return_value=self.tmp / "missing"):
            self.assertEqual(bridge.state()["home"], "")
        with patch.object(Path, "home", side_effect=RuntimeError("no home")):
            self.assertEqual(bridge.state()["home"], "")

    def test_settings_file_error_is_a_sentence(self):
        self.neighbours()
        for problem in (ValueError("bad json"), OSError("denied"), TypeError("a list")):
            self.use(runtime, "settings", side_effect=problem)
            with self.assertRaises(ValueError) as caught:
                bridge.dispatch("app-state", {})
            text = str(caught.exception)
            self.assertIn(str(self.data / "settings.json"), text)
            self.assertTrue(text.endswith("then restart Agent Workbench."))

    def test_status_reports_starting_inside_the_budget(self):
        self.neighbours()
        spec = {"model": "/models/fixture.gguf", "context": 4096}
        rec = {"spec": spec, "started": int(time.time()), "budget": 600}
        owned = self.use(runtime, "owned", return_value=(object(), rec))
        self.use(runtime, "endpoint_model", side_effect=OSError("refused"))
        self.use(bridge, "active_sessions", return_value=[{"pid": 5, "harness": "OMP"}])
        found = bridge.dispatch("status", {})
        self.assertEqual(
            (found["label"], found["model"], found["context"], found["owned"]),
            ("Starting", "fixture.gguf", 4096, True),
        )
        self.assertEqual((found["started"], found["budget"]), (rec["started"], 600))
        owned.return_value = (object(), {"spec": spec, "started": int(time.time()) - 900, "budget": 600})
        self.assertEqual(bridge.status({})["label"], "Starting or unavailable")
        owned.return_value = (object(), {"spec": spec})
        late = bridge.status({})
        self.assertEqual((late["label"], late["started"]), ("Starting or unavailable", None))
        owned.return_value = (object(), {"spec": spec, "started": "soon", "budget": None})
        self.assertEqual(bridge.status({})["label"], "Starting or unavailable")

    def test_status_when_stopped_and_while_terminals_load(self):
        self.neighbours()
        self.use(bridge, "socket_connect", return_value=False)
        self.use(bridge, "active_sessions", return_value=[{"pid": 5, "harness": "OMP"}])
        loading = types.ModuleType("project_terminal")
        with patch.dict(sys.modules, {"project_terminal": loading}):
            found = bridge.status({})
            self.assertEqual((found["label"], found["owned"]), ("Stopped", False))
            self.assertEqual((found["started"], found["budget"]), (None, None))
            self.assertEqual([s["pid"] for s in found["sessions"]], [5])
            loading.active_agents = lambda: [{"pid": 5}, {"pid": 6, "harness": "Pi"}]
            self.assertEqual([s["pid"] for s in bridge.status({})["sessions"]], [5, 6])


class LaunchTest(BridgeCase):
    def form(self, **values):
        base = dict(
            workspace=str(self.folder), harness="OMP", provider="OpenRouter", preset="", search=False
        )
        return base | values

    def test_launch_remembers_folder_and_keeps_saved_toggles(self):
        self.neighbours(provider="OpenRouter", search=True, plugins="art")
        launched = []
        launch = self.use(connections, "launch", side_effect=lambda s, **_: launched.append(dict(s)))
        write = self.use(runtime, "write_json")
        remember = self.use(bridge, "remember")
        self.which.return_value = "/bin/omp"
        self.assertIs(bridge.dispatch("launch", self.form(plugins="db")), True)
        self.assertEqual((launched[0]["search"], launched[0]["plugins"]), (False, "db"))
        folder, kept = remember.call_args.args
        self.assertEqual(remember.call_count, 1)
        self.assertEqual(folder, str(self.folder))
        self.assertEqual((kept["preset"], kept["search"], kept["harness"]), ("", False, "OMP"))
        target, written = write.call_args_list[0].args
        self.assertEqual(target, self.data / "settings.json")
        self.assertEqual((written["search"], written["plugins"]), (True, "art"))
        recent, prefs = write.call_args_list[1].args
        self.assertEqual(recent, self.data / "launcher.json")
        self.assertEqual(prefs, {"preset": "", "recent": [str(self.folder)]})
        self.assertIs(bridge.dispatch("save", self.form()), True)
        self.assertEqual(remember.call_count, 2)
        self.assertEqual(launch.call_count, 1)
        self.assertIs(write.call_args_list[2].args[1]["search"], False)

    def test_a_damaged_recent_list_does_not_fail_a_save(self):
        self.neighbours(provider="OpenRouter")
        self.use(bridge, "remember")
        (self.data / "launcher.json").write_text("[1, 2")
        self.assertIs(bridge.dispatch("launch-save", self.form()), True)
        self.assertEqual(
            json.loads((self.data / "launcher.json").read_text()),
            {"preset": "", "recent": [str(self.folder)]},
        )
        self.assertEqual(
            json.loads((self.data / "settings.json").read_text())["workspace"], str(self.folder)
        )

    def test_launch_names_setup_for_a_missing_cli(self):
        self.neighbours(provider="OpenRouter")
        launch = self.use(connections, "launch")
        self.use(connections, "HARNESS", new=[*connections.HARNESS, "opencode"])
        self.use(connections, "HARNESS_CLI", new=connections.HARNESS_CLI | {"opencode": "opencode"})
        for harness in ("OMP", "opencode"):
            with self.assertRaises(ValueError) as caught:
                bridge.dispatch("launch-start", self.form(harness=harness))
            self.assertEqual(
                str(caught.exception),
                f"{harness} is missing. Open Advanced → Setup to install it.",
            )
        launch.assert_not_called()

    def test_launch_surfaces_the_sentences_of_the_launch_path(self):
        self.neighbours(provider="OpenRouter")
        self.which.return_value = "/bin/omp"
        write = self.use(runtime, "write_json")
        remember = self.use(bridge, "remember")
        consent = "Review the project's own agent settings before the first launch in this folder."
        self.use(connections, "launch", side_effect=ValueError(consent))
        with self.assertRaises(ValueError) as caught:
            bridge.dispatch("launch", self.form())
        self.assertEqual(bridge.describe(caught.exception), {"message": consent})
        write.assert_not_called()
        remember.assert_not_called()

    def test_selection_refuses_bad_folders_and_names(self):
        s = self.saved()
        for folder in ("", "relative/path", str(self.tmp / "missing")):
            with self.assertRaises(ValueError):
                bridge.selection(s, {"workspace": folder}, {})
        with self.assertRaisesRegex(ValueError, "supported agent and provider"):
            bridge.selection(s, {"harness": "Nope"}, {})
        with self.assertRaisesRegex(ValueError, "preset is unavailable"):
            bridge.selection(s, {"preset": "Gone"}, {})
        names = {"Work": {"context": 65536, "workspace": "older folder", "presets": {"x": 1}}}
        out = bridge.selection(s, {"preset": "Work", "workspace": f" {self.folder} "}, names)
        self.assertEqual((out["context"], out["workspace"]), (65536, str(self.folder)))
        self.assertEqual(out["presets"], s["presets"])

    def test_values_of_the_wrong_type_are_refused_before_anything_is_saved(self):
        self.neighbours(provider="OpenRouter")
        write = self.use(runtime, "write_json")
        remember = self.use(bridge, "remember")
        launch = self.use(connections, "launch")
        self.which.return_value = "/bin/omp"
        wrong = dict(
            plugins={"a": 1},
            endpoint=5,
            search="yes",
            desktop=1,
            remote_model=None,
            auth_provider=["x"],
            connection_id={"x": 1},
            image_route=7,
            harness=["OMP"],
            provider={"a": 1},
            workspace=5,
        )
        for key, value in wrong.items():
            for action in ("launch-save", "launch-start", "server-match"):
                with self.assertRaises(ValueError) as caught:
                    bridge.dispatch(action, self.form(**{key: value}))
                self.assertEqual(
                    str(caught.exception),
                    f"The launch setting {key} has a value Agent Workbench cannot use. "
                    "Restart Agent Workbench, then choose it again.",
                )
        for preset in (["x"], {"a": 1}, 5):
            with self.assertRaisesRegex(ValueError, "preset is unavailable"):
                bridge.dispatch("launch-save", self.form(preset=preset))
        for mock in (write, remember, launch):
            mock.assert_not_called()
        self.assertIs(bridge.dispatch("launch-save", self.form(search=True, plugins="art")), True)

    def test_project_preview_and_apply(self):
        self.neighbours(provider="OpenRouter")
        write = self.use(runtime, "write_json")
        preview = self.use(projects, "preview_project", return_value={"digest": "d1"})
        applied = self.use(projects, "apply_project", side_effect=lambda s, *rest: s | {"context": 4096})
        legacy = self.use(projects, "restore_project", side_effect=lambda s, *rest: s | {"context": 2048})
        form = self.form(name="Research")
        self.assertEqual(bridge.dispatch("project-preview", form), {"digest": "d1"})
        self.assertEqual(preview.call_args.args[1:], ("Research", []))
        write.assert_not_called()
        self.assertIs(bridge.dispatch("project-apply", form | {"confirm": "d1"}), True)
        self.assertEqual(applied.call_args.args[1:], ("Research", [], "d1"))
        self.assertEqual(write.call_args_list[0].args[1]["context"], 4096)
        self.assertEqual(write.call_args_list[1].args[1]["preset"], "")
        legacy.assert_not_called()

    def test_folder_default_actions(self):
        self.neighbours(provider="OpenRouter")
        write = self.use(runtime, "write_json")
        saved = self.use(projects, "save_project", return_value="/x/profiles.json")
        bridge.dispatch("project-save", self.form(name="Folder default", default=True))
        self.assertEqual(saved.call_args.args[1], "Folder default")
        self.assertIs(saved.call_args.kwargs["default"], True)
        bridge.dispatch("project-save", self.form(name="Research", default="yes"))
        self.assertIs(saved.call_args.kwargs["default"], False)
        write.assert_not_called()
        names = self.use(projects, "project_names", return_value=["a"])
        detail = self.use(projects, "project_list", return_value={"names": ["a"], "default": "a"})
        here = {"workspace": str(self.folder)}
        self.assertEqual(bridge.dispatch("project-list", here), ["a"])
        self.assertEqual(
            bridge.dispatch("project-list", here | {"detail": True}),
            {"names": ["a"], "default": "a"},
        )
        self.assertEqual((names.call_count, detail.call_count), (1, 1))
        chosen = self.use(projects, "set_default", return_value={"default": "a"})
        cleared = self.use(projects, "clear_default", return_value={"default": ""})
        self.assertEqual(bridge.dispatch("project-default-set", here | {"name": "a"}), {"default": "a"})
        self.assertEqual(chosen.call_args.args, (str(self.folder), "a"))
        self.assertEqual(bridge.dispatch("project-default-clear", here), {"default": ""})
        self.assertEqual(cleared.call_args.args, (str(self.folder),))
        for name in ("project-default-set", "project-default-clear"):
            self.assertEqual(bridge.owner(name), "desktop_bridge")
        for name in ("folder-default-dismiss", "folder-places"):
            self.assertEqual(bridge.owner(name), "project_profiles")

    def test_project_apply_needs_a_reviewed_digest(self):
        self.neighbours(provider="OpenRouter")
        write = self.use(runtime, "write_json")
        applied = self.use(projects, "apply_project")
        legacy = self.use(projects, "restore_project")
        for extra in ({}, {"confirm": ""}, {"confirm": None}):
            with self.subTest(extra=extra), self.assertRaisesRegex(
                ValueError, r"^Review this profile before you apply it\.$"
            ):
                bridge.dispatch("project-apply", self.form(name="Research") | extra)
        applied.assert_not_called()
        legacy.assert_not_called()
        write.assert_not_called()

    def test_project_setup_gets_the_installed_agent_version(self):
        bridge.CLI_VERSIONS.clear()
        self.addCleanup(bridge.CLI_VERSIONS.clear)
        seen = []
        fake = types.SimpleNamespace(dispatch=lambda action, p: seen.append(p) or True)
        self.use(bridge, "area_dispatch", return_value=fake.dispatch)
        which = self.use(bridge.shutil, "which", return_value="/opt/bin/claude")
        import tooling

        probe = self.use(tooling, "version", return_value="2.1.330")
        base = {"workspace": "/w", "harness": "Claude Code"}
        bridge.dispatch("project-setup-resolve", base)
        bridge.dispatch("project-setup-plan", base)
        bridge.dispatch("project-setup-resolve", base | {"cli_version": "2.0.1"})
        bridge.dispatch("project-setup-mcp", {"workspace": "/w"})
        self.assertEqual([p.get("cli_version") for p in seen], ["2.1.330", "2.1.330", "2.0.1", None])
        which.assert_called_once_with("claude")
        probe.assert_called_once_with("/opt/bin/claude")
        self.assertEqual(bridge.installed_version("Not an agent"), "")
        self.assertEqual(bridge.installed_version(None), "")

    def test_adopt_needs_a_running_server(self):
        self.neighbours(provider="OpenRouter")
        with self.assertRaisesRegex(ValueError, r"^The server has stopped\.$"):
            bridge.dispatch("use-running", self.form())
        self.assertIs(bridge.dispatch("match", self.form()), False)


def bridge_env(base):
    env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
    return env | {
        "WORKBENCH_DATA": str(base / "data"),
        "HOME": str(base),
        "USERPROFILE": str(base),
        "PYTHONUTF8": "1",
    }


class Process:
    """A real bridge process driven over its pipes."""

    def __init__(self, case, base, **extra):
        self.log = (base / "bridge.log").open("w+", encoding="utf8")
        case.addCleanup(self.log.close)
        self.proc = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "desktop_bridge.py")],
            cwd=ROOT,
            env=bridge_env(base) | extra,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log,
            text=True,
            encoding="utf8",
        )
        case.addCleanup(self.close)
        self.lines = queue.Queue()
        threading.Thread(target=self.pump, daemon=True).start()

    def pump(self):
        for line in self.proc.stdout:
            self.lines.put(json.loads(line))
        self.lines.put(None)

    def send(self, text):
        self.proc.stdin.write(text + "\n")
        self.proc.stdin.flush()

    def ask(self, ident, action, data=None, **extra):
        self.send(json.dumps({"id": ident, "action": action, "data": data or {}} | extra))

    def read(self, seconds=10):
        return self.lines.get(timeout=seconds)

    def until(self, check, seconds=10):
        seen = []
        while True:
            line = self.read(seconds)
            seen.append(line)
            if line is None or check(line):
                return line, seen

    def stderr(self):
        self.log.flush()
        self.log.seek(0)
        return self.log.read()

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(5)
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                stream.close()
            except OSError:
                pass


class ProcessTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        (self.base / "project").mkdir()

    def test_children_cannot_read_the_request_pipe(self):
        script = (
            "import subprocess, sys\n"
            "import desktop_bridge\n"
            "reader = desktop_bridge.isolate_stdin()\n"
            "child = subprocess.run([sys.executable, '-c', 'import sys; print(len(sys.stdin.read()))'],"
            " capture_output=True, text=True, timeout=20)\n"
            "print(child.stdout.strip())\n"
            "print(reader.readline().strip())\n"
            "print(repr(sys.stdin.read()))\n"
        )
        done = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=bridge_env(self.base),
            input="hello\nsecond\n",
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(done.stdout.split(), ["0", "hello", "''"], done.stderr)

    def test_main_over_pipes(self):
        bridge_process = Process(self, self.base)
        ready = bridge_process.read()
        self.assertEqual(ready["event"], "bridge-ready")
        self.assertEqual(ready["data"]["protocol"], 2)
        self.assertEqual(ready["data"]["pid"], bridge_process.proc.pid)
        bridge_process.ask(1, "nope")
        self.assertEqual(
            bridge_process.read(), {"id": 1, "error": {"message": "Unknown desktop action."}}
        )
        bridge_process.send("garbage that is not JSON")
        bridge_process.send("[1, 2]")
        bridge_process.ask(2, "project-list", {"workspace": str(self.base / "project")})
        self.assertEqual(bridge_process.read(), {"id": 2, "value": []})
        bridge_process.ask(3, "job-cancel", {"handle": "nothing"})
        self.assertEqual(bridge_process.read(), {"id": 3, "value": False})
        bridge_process.ask(4, "project-list", {})
        failed = bridge_process.read()["error"]
        self.assertEqual((failed["code"], failed["action"]), ("internal", "log"))
        self.assertIn("KeyError", failed["detail"])
        bridge_process.proc.stdin.close()
        self.assertEqual(bridge_process.proc.wait(5), 0)
        self.assertIsNone(bridge_process.read())
        log = bridge_process.stderr()
        self.assertEqual(log.count("desktop_bridge: ignored a malformed request line"), 2)
        self.assertNotIn("garbage", log)
        self.assertIn("KeyError: 'workspace'", log)

    @unittest.skipUnless(
        os.environ.get("WORKBENCH_SLOW_TESTS"),
        "starts a stand-in model server on a free port; set WORKBENCH_SLOW_TESTS=1",
    )
    def test_real_start_reports_progress_and_cancels(self):
        import socket

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        data = self.base / "data"
        data.mkdir()
        manifest = self.base / "engine.json"
        manifest.write_text(
            json.dumps({"argv": [sys.executable, "-c", "import time; time.sleep(120)"]})
        )
        (data / "settings.json").write_text(
            json.dumps(
                {
                    "engine": "Custom",
                    "engine_manifest": str(manifest),
                    "workspace": str(self.base / "project"),
                }
            )
        )
        bridge_process = Process(self, self.base, WORKBENCH_PORT=str(port))
        self.assertEqual(bridge_process.read()["event"], "bridge-ready")
        bridge_process.ask(1, "serve", {"provider": "Managed local"}, handle="start-1", lane="job")
        spawned, seen = bridge_process.until(
            lambda m: m.get("event") == "job-progress" and m["data"]["progress"]["phase"] == "loading"
        )
        self.assertEqual(
            (spawned["data"]["handle"], spawned["data"]["action"]), ("start-1", "server-start")
        )
        phases = [m["data"]["progress"]["phase"] for m in seen]
        self.assertEqual(phases[:2], ["prepare", "spawn"])
        child = next(m["data"]["progress"]["pid"] for m in seen if m["data"]["progress"].get("pid"))
        bridge_process.ask(2, "status")
        status, _ = bridge_process.until(lambda m: m.get("id") == 2)
        self.assertEqual((status["value"]["label"], status["value"]["owned"]), ("Starting", True))
        bridge_process.ask(3, "job-cancel", {"handle": "start-1"})
        cancelled, between = bridge_process.until(lambda m: m.get("id") == 1)
        self.assertIn({"id": 3, "value": True}, between)
        self.assertEqual(
            cancelled["error"], {"message": "The model start was cancelled.", "code": "cancelled"}
        )
        bridge_process.ask(4, "status")
        after, _ = bridge_process.until(lambda m: m.get("id") == 4)
        self.assertEqual((after["value"]["label"], after["value"]["owned"]), ("Stopped", False))
        bridge_process.proc.stdin.close()
        self.assertEqual(bridge_process.proc.wait(5), 0)
        import psutil

        try:
            gone = psutil.Process(child).status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            gone = True
        self.assertTrue(gone)


if __name__ == "__main__":
    unittest.main()
