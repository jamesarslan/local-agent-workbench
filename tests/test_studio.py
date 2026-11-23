"""Studio action tests: setup outcomes, model import, traces, saves, and the classic window's shared fixes.
Offline; no installer, agent, browser or window is started."""

import atexit
import io
import json
import os
import queue
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

import filelock

import cli
import desktop_bridge
import doctor
import host
import project_profiles
import runtime
import shortcuts
import studio
import tooling

try:
    import integration_ui
    import workbench
except Exception:  # tkinter or tkinterdnd2 is absent on this interpreter
    integration_ui = workbench = None

BUSY = "Another setup job is running; wait for it to finish, then retry."
PRESETS = ({"name": "Preset", "file": "a.gguf", "engine": "llama.cpp", "context": 4096},)


def outcome(kind, name="OMP", action="Install", message="", **extra):
    base = dict(name=name, action=action, outcome=kind, message=message or kind, path="", hint="", url="", log="")
    return {**base, **extra}


class Field:
    """A form variable of the classic window."""

    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeProcess:
    def __init__(self, lines="", code=0):
        self.stdout = io.StringIO(lines)
        self.code = code
        self.stopped = False

    def wait(self):
        return self.code

    def poll(self):
        return self.code

    def terminate(self):
        self.stopped = True


class Finishing(dict):
    """A trace table whose agent exits at the moment the bridge asks whether it is still running."""

    def __init__(self, ident, log):
        super().__init__({ident: FakeProcess()})
        self.log = log

    def __contains__(self, ident):
        if dict.__contains__(self, ident):
            with self.log.open("a", encoding="utf8") as log:
                log.write(json.dumps(dict(elapsed_s=1, event="exit", details="0")) + "\n")
            del self[ident]
        return dict.__contains__(self, ident)


class StudioCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.data = self.tmp / "data"
        self.data.mkdir()
        for target, name, value in (
            (runtime, "DATA", self.data),
            (studio, "TOOLS", {}),
            (studio, "TRACE", {}),
            (studio, "SCANNED", 0.0),
            (studio, "STALE", False),
            (studio, "SETUP", threading.Lock()),
        ):
            active = patch.object(target, name, value)
            active.start()
            self.addCleanup(active.stop)
        refresh = patch.object(tooling, "refresh_path", return_value=[])
        self.refresh = refresh.start()
        self.addCleanup(refresh.stop)

    def save(self, **values):
        (self.data / "settings.json").write_text(json.dumps(values), encoding="utf8")

    def saved(self):
        return json.loads((self.data / "settings.json").read_text(encoding="utf8"))

    def folder(self, name, *files):
        path = self.tmp / name
        path.mkdir()
        for file in files:
            (path / file).write_bytes(b"weights")
        return path


class SetupTests(StudioCase):
    def test_setup_action_returns_outcome_and_checks(self):
        done = outcome("installed", message="OMP is ready at /opt/x/omp.", path="/opt/x/omp")
        with (
            patch.object(tooling, "run", return_value=done),
            patch.object(tooling, "location", return_value="/opt/x/omp"),
        ):
            out = studio.dispatch("studio-setup", {"name": "OMP", "verb": "Install"})
        self.assertEqual(out["outcome"], "installed")
        self.assertEqual(out["message"], "OMP is ready at /opt/x/omp.")
        self.assertEqual(set(out["checks"]), set(tooling.TOOLS))
        self.assertEqual(out["checks"]["OMP"], "/opt/x/omp")
        self.refresh.assert_called()

    def test_setup_passes_progress_and_cancel(self):
        seen, token = [].append, threading.Event()
        with (
            patch.object(tooling, "run", return_value=outcome("installed")) as run,
            patch.object(tooling, "location", return_value=""),
        ):
            studio.dispatch("studio-setup", {"name": "OMP", "verb": "Update"}, progress=seen, cancel=token)
            studio.dispatch("studio-tools", {"name": "Pi", "verb": "Install"}, progress=seen, cancel=token)
            studio.dispatch("studio-setup", {"name": "OMP", "verb": "Docs"})
        first, second, third = run.call_args_list
        self.assertEqual(first.args[:2], ("OMP", "Update"))
        self.assertIs(first.args[2], seen)
        self.assertIs(first.args[3], token)
        self.assertEqual(second.args[:2], ("Pi", "Install"))
        self.assertIs(second.args[2], seen)
        self.assertIs(second.args[3], token)
        self.assertEqual(third.args, ("OMP", "Docs", None, None))

    def test_legacy_tools_action(self):
        with patch.object(tooling, "location", return_value="/opt/x/omp"):
            with patch.object(tooling, "run", return_value=outcome("installed")):
                found = studio.dispatch("studio-tools", {"name": "OMP", "verb": "Install"})
            self.assertEqual(found, dict.fromkeys(tooling.TOOLS, "/opt/x/omp"))
            failed = outcome("failed", message="Install Node.js in Setup first, then install Pi.")
            with patch.object(tooling, "run", return_value=failed):
                with self.assertRaises(ValueError) as caught:
                    studio.dispatch("studio-tools", {"name": "Pi", "verb": "Install"})
        self.assertEqual(str(caught.exception), "Install Node.js in Setup first, then install Pi.")

    def test_failed_setup_is_a_value_not_an_error(self):
        failed = outcome("failed", message="OMP install was cancelled; choose Install to start it again.")
        with patch.object(tooling, "run", return_value=failed), patch.object(tooling, "location", return_value=""):
            out = studio.dispatch("studio-setup", {"name": "OMP", "verb": "Install"})
        self.assertEqual(out["outcome"], "failed")
        self.assertIn("cancelled", out["message"])
        self.assertFalse(studio.STALE)

    def test_unknown_setup_is_refused(self):
        with patch.object(tooling, "run") as run:
            for action in ("studio-setup", "studio-tools"):
                for request in (
                    {"name": "Nope", "verb": "Install"},
                    {"name": "OMP", "verb": "Remove"},
                    {"name": ["OMP"], "verb": "Install"},
                ):
                    with self.assertRaises(ValueError) as caught:
                        studio.dispatch(action, request)
                    self.assertEqual(str(caught.exception), "Unknown setup action.")
        run.assert_not_called()

    def test_second_install_is_refused_while_one_runs(self):
        docs = outcome("opened-docs", action="Docs")
        with patch.object(tooling, "run", return_value=docs) as run, patch.object(tooling, "location", return_value=""):
            self.assertTrue(studio.SETUP.acquire(blocking=False))
            try:
                for verb in ("Install", "Update"):
                    with self.assertRaises(ValueError) as caught:
                        studio.dispatch("studio-setup", {"name": "OMP", "verb": verb})
                    self.assertEqual(str(caught.exception), BUSY)
                run.assert_not_called()
                out = studio.dispatch("studio-setup", {"name": "OMP", "verb": "Docs"})
            finally:
                studio.SETUP.release()
        self.assertEqual(out["outcome"], "opened-docs")
        run.assert_called_once()

    def test_setup_lock_is_released_when_the_job_crashes(self):
        with patch.object(tooling, "run", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                studio.dispatch("studio-setup", {"name": "OMP", "verb": "Install"})
        self.assertTrue(studio.SETUP.acquire(blocking=False))
        studio.SETUP.release()

    def test_checks_rescan_when_stale(self):
        request = {"name": "OMP", "verb": "Install"}
        with patch.object(tooling, "location", return_value="") as locate:
            with patch.object(tooling, "run", return_value=outcome("installed")):
                studio.dispatch("studio-setup", request)
            locate.reset_mock()
            studio.checks()
            locate.assert_not_called()

            with patch.object(tooling, "run", return_value=outcome("opened-docs", action="Docs")):
                studio.dispatch("studio-setup", {"name": "OMP", "verb": "Docs"})
            self.assertFalse(studio.STALE)

            with patch.object(tooling, "run", return_value=outcome("started-terminal")):
                studio.dispatch("studio-setup", request)
            self.assertTrue(studio.STALE)
            locate.reset_mock()
            studio.checks()
            self.assertEqual(locate.call_count, len(tooling.TOOLS))
            self.assertFalse(studio.STALE)

            with patch.object(tooling, "run", return_value=outcome("opened-docs")):
                studio.dispatch("studio-setup", request)
            self.assertTrue(studio.STALE)

    def test_checks_expire_and_survive_a_failing_probe(self):
        def locate(name):
            if name == "Docker":
                raise ValueError("docker context is broken")
            if name == "Pi":
                raise subprocess.TimeoutExpired(["wsl"], 10)
            return "/bin/" + name

        with patch.object(tooling, "location", side_effect=locate) as locator:
            found = studio.checks()
            self.assertEqual(list(found), list(tooling.TOOLS))
            self.assertEqual((found["Docker"], found["Pi"], found["OMP"]), ("", "", "/bin/OMP"))
            locator.reset_mock()
            studio.checks()
            locator.assert_not_called()
            with patch.object(studio, "SCANNED", time.monotonic() - 301):
                studio.checks()
            self.assertEqual(locator.call_count, len(tooling.TOOLS))
            locator.reset_mock()
            studio.checks(True)
            self.assertEqual(locator.call_count, len(tooling.TOOLS))

    def test_doctor_action_shape(self):
        rows = [
            dict(id="python", title="Python", status="ok", detail="Python 3.12", fix=""),
            dict(id="git", title="Git", status="warn", detail="Git was not found.", fix="Install Git."),
        ]
        with patch.object(doctor, "run", return_value=rows) as run:
            out = studio.dispatch("studio-doctor", {"ids": ["python", "clis"], "active": 1})
            run.assert_called_once_with(["python", "clis"], True)
            self.assertEqual(out, dict(checks=rows, counts=dict(ok=1, warn=1, fail=0, skip=0)))
            studio.dispatch("studio-doctor", {})
            run.assert_called_with(None, False)


class SettingsTests(StudioCase):
    def test_validate_uses_runtime_validator_for_serving_fields(self):
        refuse = ValueError("Set the context to a whole number.")
        with patch.object(runtime, "validate_serving", create=True, side_effect=refuse) as check:
            with self.assertRaises(ValueError) as caught:
                studio.dispatch("studio-save", {"context": 9})
            self.assertEqual(str(caught.exception), "Set the context to a whole number.")
            self.assertEqual(check.call_count, 1)
            self.assertFalse((self.data / "settings.json").exists())
            self.assertTrue(studio.dispatch("studio-save", {"prompt": "x"}))
            self.assertEqual(check.call_count, 1)
        self.assertEqual(self.saved()["prompt"], "x")
        with patch.object(runtime, "validate_serving", create=True) as check:
            self.assertTrue(studio.dispatch("studio-save", {"context": "4096"}))
            check.assert_called_once()
        self.assertEqual(self.saved()["context"], 4096)

    def test_validate_checks_only_submitted_fields(self):
        missing = str(self.tmp / "gone.gguf")
        self.save(model=missing, library=str(self.tmp / "gone"), engine="Retired", context="broken")
        with patch.object(runtime, "validate_serving", None, create=True):
            self.assertTrue(studio.dispatch("studio-save", {"prompt": "x"}))
            self.assertEqual(self.saved()["prompt"], "x")
            self.assertEqual(self.saved()["model"], missing)
            for sent, text in (
                ({"model": missing}, "Selected weights were moved"),
                ({"library": str(self.tmp / "gone")}, "Choose an existing library folder."),
                ({"context": "", "engine": "Auto"}, "1,024"),
                ({"context": None, "engine": "Auto"}, "1,024"),
                ({"context": 512, "engine": "Auto"}, "1,024"),
                ({"context": 4096}, "Choose one of the listed inference engines."),
            ):
                with self.assertRaises(ValueError) as caught:
                    studio.dispatch("studio-save", sent)
                self.assertIn(text, str(caught.exception))
                self.assertNotIn("invalid literal", str(caught.exception))
            self.assertTrue(studio.dispatch("studio-save", {"context": "2048", "engine": "Auto", "name": " Daily "}))
        stored = self.saved()
        self.assertEqual((stored["context"], stored["engine"]), (2048, "Auto"))
        self.assertEqual(stored["named_profiles"]["Daily"]["context"], 2048)

    def test_save_refuses_a_blank_or_non_text_folder_and_model(self):
        with patch.object(runtime, "validate_serving", None, create=True):
            for sent, text in (
                ({"library": ""}, "Choose an existing library folder."),
                ({"library": "  "}, "Choose an existing library folder."),
                ({"library": 5}, "Choose an existing library folder."),
                ({"library": None}, "Choose an existing library folder."),
                ({"model": 5}, "Selected weights were moved. Rescan the model folder."),
                ({"model": ["a.gguf"]}, "Selected weights were moved. Rescan the model folder."),
            ):
                with self.assertRaises(ValueError) as caught:
                    studio.dispatch("studio-save", sent)
                self.assertEqual(str(caught.exception), text)
            self.assertFalse((self.data / "settings.json").exists())
            self.assertTrue(studio.dispatch("studio-save", {"library": str(self.tmp), "model": ""}))
        self.assertEqual(self.saved()["library"], str(self.tmp))

    def test_tuning_carries_the_engine_settings(self):
        for key in ("start_timeout", "engine_args"):
            self.assertIn(key, studio.TUNING)
            self.assertIn(key, studio.SERVING)
            self.assertIn(key, studio.settings())
        self.assertLessEqual(studio.SERVING - {"start_timeout", "engine_args"}, set(runtime.DEFAULTS))

    def test_engines_follow_runtime_or_platform(self):
        self.assertEqual(studio.engines("darwin"), ["Auto", "llama.cpp", "Custom"])
        self.assertEqual(studio.engines("linux"), list(studio.MODES))
        self.assertEqual(studio.engines("win32"), list(studio.MODES))
        with (
            patch.object(runtime, "available_engines", create=True, return_value=["Auto"]),
            patch.object(tooling, "location", return_value=""),
        ):
            self.assertEqual(studio.engines(), ["Auto"])
            self.assertEqual(studio.engines("darwin"), ["Auto", "llama.cpp", "Custom"])
            info = studio.dispatch("studio-info", {})
        self.assertEqual(info["engines"], ["Auto"])
        self.assertEqual(set(info["checks"]), set(tooling.TOOLS))
        with patch.object(runtime, "available_engines", None, create=True):
            self.assertEqual(studio.engines(), studio.engines(sys.platform))

    def test_profile_actions_use_project_profiles(self):
        rows = {
            "Daily": dict(remote_model="", weight="a.gguf", harness="OMP", context=32768, search=True, plugins="git,db"),
            "Hosted": dict(remote_model="vendor/model", harness="Codex", context=8192),
        }
        with (
            patch.object(project_profiles, "load_profiles", create=True, return_value=rows) as load,
            patch.object(project_profiles, "edit_profile", create=True, return_value=True) as edit,
        ):
            info = studio.dispatch("studio-profile-info", {"workspace": "/w", "name": "Daily"})
            self.assertEqual(info, {"Model": "a.gguf", "Agent": "OMP", "Context": 32768, "Tools": "search, git, db"})
            load.assert_called_once_with("/w")
            hosted = studio.dispatch("studio-profile-info", {"workspace": "/w", "name": "Hosted"})
            self.assertEqual(hosted, {"Model": "vendor/model", "Agent": "Codex", "Context": 8192, "Tools": "None"})
            with self.assertRaises(ValueError) as caught:
                studio.dispatch("studio-profile-info", {"workspace": "/w", "name": "Gone"})
            self.assertEqual(str(caught.exception), "Profile no longer exists.")
            edit.assert_not_called()

            request = {"workspace": "/w", "name": "Daily", "op": "rename", "new_name": "Nightly"}
            self.assertIs(studio.dispatch("studio-profile-edit", request), True)
            edit.assert_called_once_with("/w", "Daily", "rename", "Nightly")
            studio.dispatch("studio-profile-edit", {"workspace": "/w", "name": "Daily", "op": "remove"})
            edit.assert_called_with("/w", "Daily", "remove", "")
        self.assertFalse((Path("/w") / ".agent-workbench").exists())

    def test_profile_info_tolerates_odd_values_from_the_project_file(self):
        rows = {
            "Listed": dict(harness="OMP", search=True, plugins=["git", "db"]),
            "Numbered": dict(remote_model="vendor/model", plugins=7, desktop=1),
            "Empty": dict(plugins=None),
        }
        with patch.object(project_profiles, "load_profiles", create=True, return_value=rows):
            listed = studio.dispatch("studio-profile-info", {"workspace": "/w", "name": "Listed"})
            numbered = studio.dispatch("studio-profile-info", {"workspace": "/w", "name": "Numbered"})
            empty = studio.dispatch("studio-profile-info", {"workspace": "/w", "name": "Empty"})
        self.assertEqual(listed, {"Model": "Agent default", "Agent": "OMP", "Context": None, "Tools": "search"})
        self.assertEqual(numbered["Tools"], "desktop")
        self.assertEqual(empty["Tools"], "None")

    def test_setup_log_says_where_the_file_is_when_nothing_can_open_it(self):
        with patch.object(host, "open_folder", side_effect=FileNotFoundError("xdg-open")) as reveal:
            with self.assertRaises(ValueError) as caught:
                studio.dispatch("studio-setup-log", {})
            self.assertEqual(str(caught.exception), "Install or update a tool to create a setup log.")
            reveal.assert_not_called()
            log = self.data / "setup.log"
            log.write_text("2026-11-23 Install OMP\n", encoding="utf8")
            with self.assertRaises(ValueError) as caught:
                studio.dispatch("studio-setup-log", {})
        self.assertEqual(str(caught.exception), f"The setup log could not be opened here; open {log} in a text editor.")
        with patch.object(host, "open_folder") as reveal:
            self.assertIs(studio.dispatch("studio-setup-log", {}), True)
            reveal.assert_called_once_with(log)

    def test_shortcut_reveals_the_launcher_folder(self):
        self.save(named_profiles={"Daily": {}})
        made = self.data / "launchers" / "Daily-abcde.command"
        with patch.object(shortcuts, "create", return_value=made) as create:
            with patch.object(host, "open_folder") as reveal:
                self.assertEqual(studio.dispatch("studio-shortcut", {"name": "Daily"}), str(made))
                reveal.assert_called_once_with(made.parent)
            with patch.object(host, "open_folder", side_effect=FileNotFoundError("xdg-open")):
                self.assertEqual(studio.dispatch("studio-shortcut", {"name": "Daily"}), str(made))
            create.reset_mock()
            with self.assertRaises(ValueError):
                studio.dispatch("studio-shortcut", {"name": "Other"})
            create.assert_not_called()


class ImportTests(StudioCase):
    def test_import_refuses_folder_without_weights(self):
        folder = self.folder("notes", "README.md")
        with patch.object(runtime, "write_json") as write:
            with self.assertRaises(ValueError) as caught:
                studio.dispatch("studio-import", {"path": str(folder)})
            write.assert_not_called()
        self.assertEqual(
            str(caught.exception),
            "No model weights were found in that folder; drop a folder that contains .gguf or .strata files.",
        )

    def test_import_reports_the_change(self):
        folder = self.folder("models", "a.gguf", "README.md")
        out = studio.dispatch("studio-import", {"path": str(folder)})
        self.assertEqual(
            out,
            dict(library=str(folder), previous=runtime.DEFAULTS["library"], model="", models=1, changed=True),
        )
        self.assertEqual(self.saved()["library"], str(folder))
        self.assertEqual(self.saved()["model"], "")

        weight = folder / "a.gguf"
        again = studio.dispatch("studio-import", {"path": str(weight)})
        self.assertEqual(
            again,
            dict(library=str(folder), previous=str(folder), model=str(weight), models=1, changed=False),
        )
        self.assertEqual(self.saved()["model"], str(weight))

    def test_import_refuses_other_files_home_and_missing_paths(self):
        folder = self.folder("models", "a.gguf", "notes.txt")
        before = studio.dispatch("studio-import", {"path": str(folder)})
        with patch.object(runtime, "write_json") as write:
            with self.assertRaises(ValueError) as caught:
                studio.dispatch("studio-import", {"path": str(folder / "notes.txt")})
            self.assertEqual(str(caught.exception), "Drop a model folder or a .gguf or .strata weight file.")
            with patch.object(Path, "home", return_value=folder):
                with self.assertRaises(ValueError) as caught:
                    studio.dispatch("studio-import", {"path": str(folder)})
            self.assertIn("not your home folder or a whole drive", str(caught.exception))
            with self.assertRaises(ValueError) as caught:
                studio.dispatch("studio-import", {"path": folder.anchor})
            self.assertIn("not your home folder or a whole drive", str(caught.exception))
            for gone in (str(self.tmp / "gone"), None, "a\0b"):
                with self.assertRaises(ValueError) as caught:
                    studio.dispatch("studio-import", {"path": gone})
                self.assertEqual(str(caught.exception), "That file or folder no longer exists; drop it again.")
            with patch.object(studio.catalog, "discover", side_effect=PermissionError("denied")):
                with self.assertRaises(ValueError) as caught:
                    studio.dispatch("studio-import", {"path": str(folder)})
            self.assertIn("could not be read", str(caught.exception))
            write.assert_not_called()
        self.assertEqual(self.saved()["library"], before["library"])


    def test_import_refuses_a_weight_file_kept_in_the_home_folder(self):
        folder = self.folder("me", "a.gguf")
        with patch.object(runtime, "write_json") as write, patch.object(Path, "home", return_value=folder):
            with self.assertRaises(ValueError) as caught:
                studio.dispatch("studio-import", {"path": str(folder / "a.gguf")})
            write.assert_not_called()
        self.assertEqual(
            str(caught.exception),
            "Move that weight file into a folder of its own, then drop it again; "
            "your home folder or a whole drive cannot be the model library.",
        )
        self.assertFalse((self.data / "settings.json").exists())


class TraceTests(StudioCase):
    def run_trace(self, payload, process=None, **saved):
        self.save(**saved)
        work = Path(payload.get("form", {}).get("workspace") or saved["workspace"])
        with (
            patch.object(studio.connections, "prepare", return_value=(["omp"], {"A": "1"}, work)) as prepare,
            patch.object(studio.subprocess, "Popen", return_value=process or FakeProcess()) as spawn,
            patch.object(desktop_bridge, "choices", return_value={}),
        ):
            ident = studio.dispatch("studio-trace", payload)
            deadline = time.monotonic() + 2
            while ident in studio.TRACE and time.monotonic() < deadline:
                time.sleep(0.005)
        self.assertNotIn(ident, studio.TRACE)
        text = (self.data / "traces" / (ident + ".jsonl")).read_text(encoding="utf8")
        return prepare, spawn, [json.loads(line) for line in text.splitlines()], ident

    def test_trace_uses_form_and_closes_stdin(self):
        saved, shown = self.folder("saved"), self.folder("shown")
        form = {"workspace": str(shown), "harness": "OMP", "provider": "Managed local"}
        prepare, spawn, rows, _ = self.run_trace(
            {"task": "Summarise the README.", "form": form},
            workspace=str(saved),
            model=str(self.tmp / "Inkling-Q4.gguf"),
            remote_model="left-over/hosted-model",
        )
        settings, harness = prepare.call_args.args
        self.assertEqual((settings["workspace"], harness), (str(shown), "OMP"))
        self.assertIs(spawn.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(spawn.call_args.kwargs["cwd"], shown)
        argv = spawn.call_args.args[0]
        self.assertEqual(argv[argv.index("--tools") + 1], "read,glob,grep")
        self.assertEqual(argv[-2:], ["-p", "Summarise the README."])
        self.assertEqual(
            rows[0],
            dict(elapsed_s=0, event="target", details=f"{shown} · Managed local · Inkling-Q4.gguf · read-only"),
        )
        self.assertEqual((rows[-1]["event"], rows[-1]["details"]), ("exit", "0"))

    def test_trace_without_form_keeps_saved_settings(self):
        saved = self.folder("saved")
        prepare, spawn, rows, _ = self.run_trace(
            {"task": "Fix the test.", "edit": True},
            workspace=str(saved),
            provider="OpenRouter",
            remote_model="vendor/model",
        )
        self.assertEqual(prepare.call_args.args[0]["workspace"], str(saved))
        argv = spawn.call_args.args[0]
        self.assertEqual(argv[argv.index("--tools") + 1], "read,glob,grep,edit,write")
        self.assertEqual(rows[0]["details"], f"{saved} · OpenRouter · vendor/model · edits allowed")

    def test_trace_refuses_a_bad_form_before_starting(self):
        self.save(workspace=str(self.folder("saved")))
        gone = {"workspace": str(self.tmp / "gone"), "harness": "OMP", "provider": "Managed local"}
        with (
            patch.object(studio.connections, "prepare") as prepare,
            patch.object(studio.subprocess, "Popen") as spawn,
            patch.object(desktop_bridge, "choices", return_value={}),
        ):
            for form, text in (
                ("/somewhere", "Choose a project folder, then run the trace again."),
                (gone, "Choose an existing project folder."),
                ({**gone, "harness": "Nope"}, "Choose a supported agent and provider."),
            ):
                with self.assertRaises(ValueError) as caught:
                    studio.dispatch("studio-trace", {"task": "x", "form": form})
                self.assertEqual(str(caught.exception), text)
            prepare.assert_not_called()
            spawn.assert_not_called()

    def test_trace_skips_stray_lines_and_points_at_stderr_on_failure(self):
        lines = "\n".join(
            [
                "[1, 2]",
                '"text"',
                "not json",
                json.dumps({"type": "tool_execution_start", "toolName": "read", "args": {"path": "README.md"}}),
                json.dumps({"type": "tool_execution_end", "toolName": "read", "isError": True}),
                json.dumps({"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}}),
                json.dumps({"type": "message_end", "message": {"role": "user", "content": []}}),
                json.dumps({"type": "turn_start"}),
            ]
        )
        saved = self.folder("saved")
        _, _, rows, ident = self.run_trace(
            {"task": "x"}, FakeProcess(lines + "\n", code=1), workspace=str(saved), provider="Native login"
        )
        self.assertEqual(
            [(row["event"], row["details"]) for row in rows[:-1]],
            [
                ("target", f"{saved} · Native login · agent default · read-only"),
                ("tool_execution_start", 'read {"path": "README.md"}'),
                ("tool_execution_end", "read failed"),
                ("message_end", "Done."),
            ],
        )
        stderr = self.data / "traces" / (ident + ".stderr")
        self.assertEqual((rows[-1]["event"], rows[-1]["details"]), ("exit", f"1 · see {stderr}"))
        self.assertTrue(stderr.is_file())
        read = studio.dispatch("studio-trace-read", {"id": ident})
        self.assertEqual((len(read["rows"]), read["running"]), (len(rows), False))

    def test_trace_is_forgotten_and_its_agent_stopped_when_the_reader_fails(self):
        class Broken(FakeProcess):
            def wait(self):
                raise OSError("child vanished")

            def poll(self):
                return None

        saved, reported, process = self.folder("saved"), threading.Event(), Broken()
        with patch.object(threading, "excepthook", lambda args: reported.set()):
            _, _, rows, _ = self.run_trace({"task": "x"}, process, workspace=str(saved))
            self.assertTrue(reported.wait(2))
        self.assertEqual([row["event"] for row in rows], ["target"])
        self.assertTrue(process.stopped)

    def test_trace_leaves_a_finished_agent_alone(self):
        process = FakeProcess()
        self.run_trace({"task": "x"}, process, workspace=str(self.folder("saved")))
        self.assertFalse(process.stopped)

    def test_trace_survives_events_of_an_unexpected_shape(self):
        odd = [
            {"type": "message_end", "message": "text"},
            {"type": "message_end", "message": None},
            {"type": "message_end", "message": {"role": "assistant", "content": None}},
            {"type": "message_end", "message": {"role": "assistant", "content": 5}},
            {"type": "message_end", "message": {"role": "assistant", "content": "plain"}},
            {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": None}]}},
            {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text"}, 7, "x"]}},
            {"type": ["agent_start"]},
            {"type": "turn_end", "nested": "DEEP"},
            {"type": "tool_execution_start", "toolName": None, "args": [1]},
            {"type": "tool_execution_end", "toolName": {"a": 1}},
            {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "Still here."}]}},
        ]
        lines = "".join(json.dumps(event) + "\n" for event in odd).replace('"DEEP"', "[" * 100000 + "]" * 100000)
        _, _, rows, _ = self.run_trace({"task": "x"}, FakeProcess(lines), workspace=str(self.folder("saved")))
        self.assertEqual(
            [(row["event"], row["details"]) for row in rows[1:]],
            [
                ("tool_execution_start", "None [1]"),
                ("tool_execution_end", "{'a': 1} complete"),
                ("message_end", "Still here."),
                ("exit", "0"),
            ],
        )

    def test_trace_refuses_a_batch_launcher_with_shell_characters_on_windows(self):
        work = self.folder("saved")
        self.save(workspace=str(work))
        shim = "C:\\Tools\\omp.cmd"
        with (
            patch.object(host, "WINDOWS", True),
            patch.object(studio.connections, "prepare", side_effect=lambda s, harness: ([shim], {}, work)),
            patch.object(studio.subprocess, "Popen", return_value=FakeProcess()) as spawn,
        ):
            for task in ("Summarise the README & calc", "first line\nsecond line", "show %PATH%"):
                with self.assertRaises(ValueError) as caught:
                    studio.dispatch("studio-trace", {"task": task})
                self.assertIn("omp.cmd", str(caught.exception))
            spawn.assert_not_called()
            self.assertEqual(list(self.data.rglob("*.jsonl")) + list(self.data.rglob("*.stderr")), [])
            ident = studio.dispatch("studio-trace", {"task": "Summarise the README."})
            deadline = time.monotonic() + 2
            while ident in studio.TRACE and time.monotonic() < deadline:
                time.sleep(0.005)
        spawn.assert_called_once()
        self.assertEqual(spawn.call_args.args[0][0], shim)

    def test_trace_read_never_reports_a_finished_trace_without_its_exit_row(self):
        folder = self.data / "traces"
        folder.mkdir()
        log = folder / "20261123-000000-abcdef.jsonl"
        log.write_text(json.dumps(dict(elapsed_s=0, event="target", details="x")) + "\n", encoding="utf8")
        with patch.object(studio, "TRACE", Finishing(log.stem, log)):
            read = studio.dispatch("studio-trace-read", {"id": log.stem})
        self.assertTrue(read["running"] or read["rows"][-1]["event"] == "exit")
        self.assertEqual([row["event"] for row in read["rows"]], ["target", "exit"])

    def real_trace(self, script, wait=True):
        work = self.folder("saved")
        self.save(workspace=str(work))
        agent = self.tmp / "agent.py"
        agent.write_text(script, encoding="utf8")
        with patch.object(studio.connections, "prepare", return_value=([sys.executable, str(agent)], dict(os.environ), work)):
            ident = studio.dispatch("studio-trace", {"task": "Summarise the README."})
        if wait:
            self.finish(ident)
        return ident

    def finish(self, ident):
        deadline = time.monotonic() + 10
        while ident in studio.TRACE and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertNotIn(ident, studio.TRACE)

    def test_trace_runs_a_real_child_with_closed_stdin(self):
        script = (
            "import json, sys\n"
            "empty = sys.stdin.read() == ''\n"
            "print(json.dumps({'type': 'tool_execution_start', 'toolName': 'stdin', 'args': {'empty': empty, 'argv': sys.argv[-2:]}}), flush=True)\n"
            "print('caf\\u00e9 is not json', flush=True)\n"
            "print('the agent said why', file=sys.stderr)\n"
            "sys.exit(3)\n"
        )
        ident = self.real_trace(script)
        read = studio.dispatch("studio-trace-read", {"id": ident, "offset": 0})
        stderr = self.data / "traces" / (ident + ".stderr")
        self.assertEqual(
            [(row["event"], row["details"]) for row in read["rows"][1:]],
            [
                ("tool_execution_start", 'stdin {"empty": true, "argv": ["-p", "Summarise the README."]}'),
                ("exit", f"3 · see {stderr}"),
            ],
        )
        self.assertFalse(read["running"])
        self.assertEqual(stderr.read_text(encoding="utf8").strip(), "the agent said why")
        again = studio.dispatch("studio-trace-read", {"id": ident, "offset": read["offset"]})
        self.assertEqual(again["rows"], [])

    def test_trace_stop_ends_a_running_child(self):
        script = (
            "import json, time\n"
            "print(json.dumps({'type': 'agent_start'}), flush=True)\n"
            "time.sleep(30)\n"
        )
        began = time.monotonic()
        ident = self.real_trace(script, wait=False)
        deadline = time.monotonic() + 10
        rows = []
        while len(rows) < 2 and time.monotonic() < deadline:
            rows = studio.dispatch("studio-trace-read", {"id": ident})["rows"]
            time.sleep(0.01)
        self.assertEqual([row["event"] for row in rows], ["target", "agent_start"])
        self.assertTrue(studio.dispatch("studio-trace-read", {"id": ident})["running"])
        self.assertIs(studio.dispatch("studio-trace-stop", {"id": ident}), True)
        self.finish(ident)
        self.assertLess(time.monotonic() - began, 10)
        last = studio.dispatch("studio-trace-read", {"id": ident})["rows"][-1]
        self.assertEqual(last["event"], "exit")
        self.assertNotEqual(last["details"], "0")
        for bad in ("../x", "a/b", "a\\b"):
            with self.assertRaises(ValueError):
                studio.dispatch("studio-trace-read", {"id": bad})

    def test_trace_that_cannot_start_leaves_no_files_and_says_what_to_do(self):
        work = self.folder("saved")
        self.save(workspace=str(work))
        with (
            patch.object(studio.connections, "prepare", return_value=(["/opt/x/omp"], {}, work)),
            patch.object(studio.subprocess, "Popen", side_effect=PermissionError(13, "Permission denied", "/opt/x/omp")),
        ):
            with self.assertRaises(ValueError) as caught:
                studio.dispatch("studio-trace", {"task": "x"})
        self.assertEqual(
            str(caught.exception),
            "The trace could not be started (Permission denied); reinstall OMP in Advanced > Setup, then run the trace again.",
        )
        self.assertEqual(list((self.data / "traces").iterdir()), [])
        self.assertEqual(studio.TRACE, {})

    def test_trace_needs_a_task(self):
        with patch.object(studio.connections, "prepare") as prepare:
            with self.assertRaises(ValueError):
                studio.dispatch("studio-trace", {"task": "  "})
            prepare.assert_not_called()


class HubTests(StudioCase):
    def test_hub_rows_carry_the_exact_size_in_bytes(self):
        size = 640_000_000
        found = [("org/model", "abc", "model-Q4.gguf", 1, size), "org/other"]
        with patch.object(studio.catalog, "hub_files", return_value=found), patch.dict(studio.HUB, clear=True):
            rows = studio.dispatch("studio-hub", {"query": " org/model "})
            self.assertEqual(rows[1], {"repo": "org/other"})
            self.assertEqual(
                {key: rows[0][key] for key in ("name", "gib", "bytes")},
                {"name": "model-Q4.gguf", "gib": 0.6, "bytes": size},
            )
            self.assertEqual(studio.HUB[rows[0]["id"]], found[0])
        with self.assertRaisesRegex(ValueError, "^Enter a model name"):
            studio.dispatch("studio-hub", {"query": "  "})


class ChildProcessTests(StudioCase):
    def test_bench_surfaces_child_error(self):
        trace = 'Traceback (most recent call last):\n  File "cli.py", line 1, in <module>\nValueError: Select a model first.\n'
        with patch.object(studio.subprocess, "run", return_value=types.SimpleNamespace(returncode=1, stderr=trace)) as run:
            with self.assertRaises(ValueError) as caught:
                studio.dispatch("studio-bench", {})
        self.assertEqual(str(caught.exception), "Select a model first.")
        self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(run.call_args.kwargs["stderr"], subprocess.PIPE)
        self.assertEqual(run.call_args.kwargs["timeout"], 3600)
        self.assertNotIn("check", run.call_args.kwargs)

    def test_bench_timeout_silent_failure_and_success(self):
        with patch.object(studio.subprocess, "run", side_effect=subprocess.TimeoutExpired(["bench"], 3600)):
            with self.assertRaises(ValueError) as caught:
                studio.dispatch("studio-bench", {})
        self.assertIn("did not finish within 60 minutes", str(caught.exception))
        with patch.object(studio.subprocess, "run", return_value=types.SimpleNamespace(returncode=2, stderr=" \n")):
            with self.assertRaises(ValueError) as caught:
                studio.dispatch("studio-bench", {})
        self.assertEqual(str(caught.exception), "The benchmark failed; start the model from Launch, then run it again.")
        report = self.data / "benchmark-20261122.json"
        report.write_text("{}", encoding="utf8")
        with patch.object(studio.subprocess, "run", return_value=types.SimpleNamespace(returncode=0, stderr="warning\n")):
            self.assertEqual(studio.dispatch("studio-bench", {}), str(report))

    def test_bench_shows_the_benchmark_sentence_not_the_wrapper_traceback(self):
        sentence = "The warm-up request failed, so nothing was measured; check the server log and run the benchmark again."
        wrapper = (
            "Traceback (most recent call last):\n"
            '  File "cli.py", line 222, in <module>\n'
            "    raise CalledProcessError(retcode, process.args,\n"
            "subprocess.CalledProcessError: Command '['python', 'benchmark.py', 'a.gguf', 'benchmark-1.json']' "
            "returned non-zero exit status 1.\n"
        )
        crashed = 'Traceback (most recent call last):\n  File "benchmark.py", line 9, in <module>\nRuntimeError: No route.\n'
        fallback = "The benchmark failed; start the model from Launch, then run it again."
        for stderr, shown in (
            ("warm-up: HTTPError 500\n" + sentence + "\n" + wrapper, sentence),
            (crashed + wrapper, "No route."),
            (wrapper, fallback),
        ):
            with patch.object(studio.subprocess, "run", return_value=types.SimpleNamespace(returncode=1, stderr=stderr)):
                with self.assertRaises(ValueError) as caught:
                    studio.dispatch("studio-bench", {})
            self.assertEqual(str(caught.exception), shown)

    def test_reason_keeps_the_last_sentence(self):
        self.assertEqual(studio.reason("a\nrequests.exceptions.ConnectionError: Start the model.\n\n"), "Start the model.")
        self.assertEqual(studio.reason("Killed"), "Killed")
        self.assertEqual(studio.reason("  \n"), "")
        self.assertEqual(studio.reason(None), "")

    def test_web_translates_docker_failure(self):
        down = "Docker is installed but not running; start Docker Desktop (on Linux: sudo systemctl start docker), then retry."
        with patch.object(tooling, "docker_problem", return_value=down) as problem:
            with patch.object(cli, "web", side_effect=subprocess.CalledProcessError(1, ["docker"])) as web:
                with self.assertRaises(ValueError) as caught:
                    studio.dispatch("studio-web", {"start": 1})
                web.assert_called_once_with(True)
            self.assertEqual(str(caught.exception), down)
            with patch.object(cli, "web", side_effect=FileNotFoundError("wsl")):
                with self.assertRaises(ValueError) as caught:
                    studio.dispatch("studio-web", {"start": False})
            self.assertEqual(str(caught.exception), down)
            problem.return_value = ""
            with patch.object(cli, "web", side_effect=subprocess.CalledProcessError(1, ["docker"])):
                with self.assertRaises(ValueError) as caught:
                    studio.dispatch("studio-web", {"start": True})
            self.assertEqual(str(caught.exception), "Docker could not start the page-crawling stack; run Doctor to see why.")

    def test_web_passes_its_own_errors_through(self):
        own = ValueError("Install Docker with the Compose plugin and start it before enabling Firecrawl.")
        with patch.object(tooling, "docker_problem") as problem:
            with patch.object(cli, "web", side_effect=own):
                with self.assertRaises(ValueError) as caught:
                    studio.dispatch("studio-web", {"start": True})
            self.assertIs(caught.exception, own)
            with patch.object(cli, "web", side_effect=filelock.Timeout(str(self.data / "web.lock"))):
                with self.assertRaises(filelock.Timeout):
                    studio.dispatch("studio-web", {"start": True})
            with patch.object(cli, "web") as web:
                self.assertIs(studio.dispatch("studio-web", {"start": False}), True)
                web.assert_called_once_with(False)
            problem.assert_not_called()


@unittest.skipIf(workbench is None, "tkinter is not available")
class ClassicWindowTests(unittest.TestCase):
    def window(self):
        return types.SimpleNamespace(q=queue.Queue(), busy=True, status=MagicMock(), win=MagicMock(), poll="again")

    def test_poll_survives_a_failing_callback(self):
        def done(result):
            raise KeyError("message")

        app = self.window()
        app.q.put((done, {"outcome": "installed"}, None))
        with patch.object(workbench.messagebox, "showerror") as shown:
            workbench.Workbench.poll(app)
        self.assertFalse(app.busy)
        app.status.set.assert_called_once_with("Operation failed")
        shown.assert_called_once_with("Agent Workbench", "'message'")
        app.win.after.assert_called_once_with(125, "again")

    def test_poll_reports_results_and_keeps_waiting(self):
        app, seen = self.window(), []
        with patch.object(workbench.messagebox, "showerror") as shown:
            workbench.Workbench.poll(app)
            self.assertTrue(app.busy)
            app.q.put((seen.append, "value", None))
            workbench.Workbench.poll(app)
            self.assertEqual((seen, app.busy), (["value"], False))
            shown.assert_not_called()
            app.q.put((None, None, None))
            workbench.Workbench.poll(app)
            app.status.set.assert_called_with("Done")
            app.q.put((None, None, "Install Node.js in Setup first, then install Pi."))
            workbench.Workbench.poll(app)
            shown.assert_called_once_with("Agent Workbench", "Install Node.js in Setup first, then install Pi.")
        self.assertEqual(app.win.after.call_count, 4)

    def form(self, stored):
        tables = ("presets", "named_profiles")
        fields = {k: Field(v if isinstance(v, bool) else str(v)) for k, v in stored.items() if k not in tables}
        app = types.SimpleNamespace(
            vars=fields,
            s=dict(stored),
            prompt=MagicMock(),
            status=MagicMock(),
            named=MagicMock(),
            remember=MagicMock(),
            scan=MagicMock(),
        )
        app.prompt.get.return_value = stored["prompt"]
        app.save = lambda: workbench.Workbench.save(app)
        return app

    def test_save_keeps_the_settings_the_form_cannot_hold(self):
        options = ["--cache-ram", "2048"]
        stored = {**runtime.DEFAULTS, "engine": "llama.cpp", "engine_args": list(options), "start_timeout": 60}
        stored |= {"presets": {}, "named_profiles": {}}
        app = self.form(stored)
        self.assertEqual(app.vars["engine_args"].get(), str(options))
        with patch.object(workbench, "write_json") as write:
            saved = workbench.Workbench.save(app)
        self.assertEqual(saved["engine_args"], options)
        self.assertEqual(write.call_args.args[1]["engine_args"], options)
        self.assertEqual((saved["context"], saved["named_profiles"]), (stored["context"], {}))
        runtime.validate_serving(saved)
        self.assertEqual(runtime.llama_args(saved["engine_args"]), options)
        self.assertEqual(runtime.start_budget(saved), 60)
        app.vars["context"].set("12")
        with patch.object(workbench, "write_json") as write:
            with self.assertRaises(ValueError):
                workbench.Workbench.save(app)
            write.assert_not_called()

    def test_named_setup_brings_its_engine_options(self):
        stored = {**runtime.DEFAULTS, "presets": {}}
        setup = {"engine": "llama.cpp", "context": 8192, "engine_args": ["--cache-ram", "4096"], "draft": False}
        setup["named_profiles"] = {}
        stored["named_profiles"] = {"Daily": setup}
        app = self.form(stored)
        app.named.get.return_value = "Daily"
        with patch.object(workbench, "write_json") as write:
            workbench.Workbench.apply_named(app)
        saved = write.call_args.args[1]
        self.assertEqual(
            (saved["engine"], saved["context"], saved["engine_args"], saved["draft"]),
            ("llama.cpp", 8192, ["--cache-ram", "4096"], False),
        )
        self.assertEqual(list(saved["named_profiles"]), ["Daily"])
        runtime.validate_serving(saved)
        app.scan.assert_called_once_with()

    def scan(self, rows, named, presets=PRESETS):
        model = MagicMock()
        model.get.return_value = "/gone/old.gguf"
        library = MagicMock()
        library.get.return_value = "/library"
        app = types.SimpleNamespace(
            models=MagicMock(),
            vars={"library": library, "model": model, "prompt": MagicMock(), "harness": MagicMock()},
            s={"named_profiles": named},
            named=MagicMock(),
            status=MagicMock(),
            total=0,
            ram=0,
        )
        app.models.get_children.return_value = []
        app.models.exists.return_value = False
        with (
            patch.object(workbench.catalog, "discover", return_value=rows),
            patch.object(workbench.catalog, "fit", return_value="GPU unknown"),
            patch.object(workbench, "owned", return_value=(None, {})),
        ):
            if presets is None:
                workbench.Workbench.scan(app)
            else:
                with patch.object(workbench, "preset_rows", return_value=[dict(row) for row in presets]):
                    workbench.Workbench.scan(app)
        return app, model

    def test_scan_adds_presets_without_the_current_form(self):
        rows = [("/library/a.gguf", ["/library/a.gguf"], 10)]
        app, _ = self.scan(rows, {})
        self.assertEqual(
            app.s["named_profiles"],
            {"Preset": {"engine": "llama.cpp", "context": 4096, "model": "/library/a.gguf"}},
        )
        app.vars["prompt"].get.assert_not_called()
        app.vars["harness"].get.assert_not_called()
        app.models.selection_set.assert_called_once_with("/library/a.gguf")

    def test_scan_offers_only_the_presets_this_computer_can_run(self):
        rows = [("/library/a.gguf", ["/library/a.gguf"], 10), ("/library/b.strata", ["/library/b.strata"], 10)]
        shipped = [
            {"name": "Llama", "file": "a.gguf", "engine": "llama.cpp", "context": 4096},
            {"name": "Strata", "file": "b.strata", "engine": "Strata", "context": 4096},
            {"name": "Text context", "file": "a.gguf", "engine": "llama.cpp", "context": "4096"},
            "not a preset",
        ]
        with tempfile.TemporaryDirectory() as root:
            listed = Path(root) / "profiles.json"
            listed.write_text(json.dumps(shipped), encoding="utf8")
            with (
                patch.object(runtime, "ROOT", Path(root)),
                patch.object(runtime, "available_engines", return_value=["Auto", "llama.cpp", "Custom"]),
            ):
                app, _ = self.scan(rows, {}, presets=None)
                self.assertEqual(
                    app.s["named_profiles"],
                    {"Llama": {"engine": "llama.cpp", "context": 4096, "model": "/library/a.gguf"}},
                )
                listed.write_text("{damaged", encoding="utf8")
                app, _ = self.scan(rows, {}, presets=None)
            self.assertEqual(app.s["named_profiles"], {})
            app.status.set.assert_called_once_with("2 model configurations in library")

    def test_scan_keeps_a_saved_setup_and_the_model_when_the_library_is_empty(self):
        with tempfile.NamedTemporaryFile(suffix=".gguf") as weights:
            kept = {"model": weights.name, "prompt": "mine"}
            app, _ = self.scan([("/library/a.gguf", ["/library/a.gguf"], 10)], {"Preset": kept})
            self.assertEqual(app.s["named_profiles"], {"Preset": {"model": weights.name, "prompt": "mine"}})
        app, model = self.scan([], {})
        self.assertEqual(app.s["named_profiles"], {})
        model.set.assert_not_called()
        app.models.selection_set.assert_not_called()
        app.status.set.assert_called_once_with("0 model configurations in library")


@unittest.skipIf(integration_ui is None, "tkinter is not available")
class ClassicPanelTests(unittest.TestCase):
    def setUp(self):
        self.app = MagicMock()
        self.tree = self.app.tree.return_value
        with patch.object(integration_ui, "ttk"), patch.object(integration_ui, "tk") as tk:
            integration_ui.panels(self.app)
        self.prompt = tk.Text.return_value
        self.buttons = {call.args[1]: call.args[2] for call in self.app.button.call_args_list}
        self.app.task.reset_mock()

    def test_scan_goes_through_the_shared_tolerant_probe(self):
        self.buttons["Scan"]()
        label, check, show = self.app.task.call_args.args
        found = {"OMP": "/opt/x/omp", "Docker": ""}
        with patch.object(studio, "checks", return_value=found) as checks:
            self.assertEqual(check(), found)
            checks.assert_called_once_with(True)
        show(found)
        self.tree.item.assert_any_call("OMP", values=("OMP", "/opt/x/omp"))
        self.tree.item.assert_any_call("Docker", values=("Docker", "Available to install"))

    def test_setup_shows_the_outcome_sentence(self):
        self.tree.selection.return_value = ("Hermes",)
        self.buttons["Install"]()
        label, work, done = self.app.task.call_args.args
        result = outcome("opened-docs", name="Hermes", message="The Hermes install page was opened.")
        with patch.object(tooling, "setup", return_value=result) as setup:
            done(work())
            setup.assert_called_once_with("Hermes", "Install")
        self.app.status.set.assert_called_with("The Hermes install page was opened.")

    def test_trace_refuses_a_batch_launcher_with_shell_characters_on_windows(self):
        self.prompt.get.return_value = "Summarise the README & calc"
        self.buttons["Run trace"]()
        label, work = self.app.task.call_args.args[:2]
        with tempfile.TemporaryDirectory() as root:
            with (
                patch.object(host, "WINDOWS", True),
                patch.object(integration_ui, "DATA", Path(root)),
                patch.object(integration_ui.connections, "prepare", return_value=(["C:\\Tools\\omp.cmd"], {}, root)),
                patch.object(integration_ui.subprocess, "Popen") as spawn,
            ):
                with self.assertRaises(ValueError) as caught:
                    work()
            self.assertIn("omp.cmd", str(caught.exception))
            spawn.assert_not_called()
            self.assertEqual(list(Path(root).rglob("*.jsonl")), [])

    def test_stored_key_message_says_where_the_key_went(self):
        provider = next(iter(integration_ui.connections.PROVIDER_KEYS))
        variable = integration_ui.connections.PROVIDER_KEYS[provider]
        form = {"provider": provider, "endpoint": "", "connection_id": "", "remote_model": ""}
        self.app.save.return_value = form
        with (
            patch.object(integration_ui.connections, "endpoint", return_value=("https://api.example", "")),
            patch.object(integration_ui.simpledialog, "askstring", return_value="sk-test-value") as ask,
            patch.object(integration_ui.connections, "store_key", return_value="memory") as store,
        ):
            self.buttons["Store API key"]()
            store.assert_called_once_with(form, "sk-test-value")
            kept = self.app.status.set.call_args.args[0]
            self.assertIn("kept only until Agent Workbench closes", kept)
            self.assertIn(variable, kept)
            store.return_value = "vault"
            self.buttons["Store API key"]()
            self.app.status.set.assert_called_with("Key saved in the system credential vault.")
            self.assertNotIn("sk-test-value", str(self.app.status.set.call_args_list))
            ask.return_value = ""
            store.reset_mock()
            self.buttons["Store API key"]()
            store.assert_not_called()

    def test_trace_survives_events_of_an_unexpected_shape(self):
        odd = [
            [1, 2],
            "text",
            {"type": "message_end", "message": "text"},
            {"type": "message_end", "message": {"role": "assistant", "content": 5}},
            {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": None}, 7]}},
            {"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": "Mine."}]}},
            {"type": ["agent_start"]},
            {"type": "tool_execution_start", "toolName": None, "args": [1]},
            {"type": "tool_execution_end", "toolName": {"a": 1}, "isError": True},
            {"type": "agent_start"},
            {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "Still here."}]}},
        ]
        lines = "not json\n" + "".join(json.dumps(event) + "\n" for event in odd)
        self.prompt.get.return_value = "Summarise the README."
        self.app.save.return_value = {"provider": "Managed local"}
        self.buttons["Run trace"]()
        work = self.app.task.call_args.args[1]
        process = FakeProcess(lines)
        with tempfile.TemporaryDirectory() as root:
            with (
                patch.object(integration_ui, "DATA", Path(root)),
                patch.object(integration_ui.connections, "prepare", return_value=(["omp"], {}, root)),
                patch.object(integration_ui.subprocess, "Popen", return_value=process) as spawn,
            ):
                name = work()
            self.assertIs(spawn.call_args.kwargs["stdin"], subprocess.DEVNULL)
            text = (Path(root) / "traces" / name).read_text(encoding="utf8")
        self.assertEqual(
            [(row["event"], row["details"]) for row in map(json.loads, text.splitlines())],
            [
                ("tool_execution_start", "None [1]"),
                ("tool_execution_end", "{'a': 1} · failed"),
                ("agent_start", "Managed local"),
                ("message_end", "Still here."),
            ],
        )
        self.assertFalse(process.stopped)

    def test_every_setup_verb_has_a_button(self):
        for verb in tooling.ACTIONS:
            self.assertIn(verb, self.buttons)


if __name__ == "__main__":
    unittest.main()
