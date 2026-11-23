"""The desktop launcher: pinned runtime download, the Linux sandbox explanation and exit handling."""

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

import requests

import desktop_app

ROOT = Path(__file__).resolve().parent.parent
VERSION = "99.1.2"
PINNED = desktop_app.ELECTRON_SHA256


def archive():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        for name in ("electron", "chrome-sandbox"):
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o100755 << 16
            z.writestr(info, "#!/bin/sh\n")
        z.writestr("resources/default_app.asar", "data")
    return data.getvalue()


class Download:
    def __init__(self, body):
        self.body = body
        self.headers = {"Content-Length": str(len(body))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        for start in range(0, len(self.body), 64):
            yield self.body[start : start + 64]


class DesktopAppCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.data = self.base / "data"
        self.desktop = self.base / "desktop"
        self.data.mkdir()
        self.desktop.mkdir()
        (self.desktop / "package.json").write_text(
            json.dumps({"dependencies": {"electron": VERSION}})
        )
        self.body = archive()
        self.get = Mock(side_effect=lambda *a, **k: Download(self.body))
        self.pins = {VERSION: {"linux-x64": hashlib.sha256(self.body).hexdigest()}}
        for target, name, value in (
            (desktop_app, "DATA", self.data),
            (desktop_app, "DESKTOP", self.desktop),
            (desktop_app, "ELECTRON_SHA256", self.pins),
            (sys, "platform", "linux"),
            (desktop_app.requests, "get", self.get),
        ):
            self.use(target, name, new=value)
        self.use(desktop_app.platform, "machine", return_value="x86_64")
        self.runtime = self.data / "desktop-runtime" / ("linux-" + VERSION)

    def use(self, target, name, **how):
        item = patch.object(target, name, **how)
        made = item.start()
        self.addCleanup(item.stop)
        return made


class RuntimeTest(DesktopAppCase):
    def test_pinned_digest_for_packaged_version(self):
        package = json.loads((ROOT / "desktop/package.json").read_text(encoding="utf8"))
        found = PINNED[package["dependencies"]["electron"]]
        self.assertEqual(set(found), {"darwin-arm64", "linux-x64", "win32-x64"})
        for digest in found.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(len(set(found.values())), 3)

    def test_download_verify_extract(self):
        self.runtime.mkdir(parents=True)
        (self.runtime / "junk.txt").write_text("left by an interrupted start")
        seen = []
        engine = desktop_app.executable(lambda done, total: seen.append((done, total)))
        self.assertEqual(engine, self.runtime / "electron")
        self.assertTrue(engine.is_file())
        self.assertFalse((self.runtime / "junk.txt").exists())
        self.assertTrue((self.runtime / "chrome-sandbox").is_file())
        self.assertEqual(self.get.call_count, 1)
        self.assertEqual(
            self.get.call_args.args[0],
            f"https://github.com/electron/electron/releases/download/v{VERSION}/"
            f"electron-v{VERSION}-linux-x64.zip",
        )
        self.assertEqual(seen[-1], (len(self.body), len(self.body)))
        if os.name != "nt":
            self.assertTrue(engine.stat().st_mode & 0o111)
            self.assertFalse((self.runtime / "resources/default_app.asar").stat().st_mode & 0o111)
        self.assertEqual(desktop_app.executable(), engine)
        self.assertEqual(self.get.call_count, 1)
        left = {p.name for p in (self.data / "desktop-runtime").iterdir()}
        self.assertEqual(left - {"linux-" + VERSION + ".lock"}, {"linux-" + VERSION})

    def test_bad_digest_is_refused(self):
        self.pins[VERSION]["linux-x64"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "failed its release checksum"):
            desktop_app.executable()
        self.assertFalse(self.runtime.exists())

    def test_missing_pin_fails_closed(self):
        for pins in ({}, {VERSION: {"darwin-arm64": "a" * 64}}):
            with patch.object(desktop_app, "ELECTRON_SHA256", pins):
                with self.assertRaises(ValueError) as caught:
                    desktop_app.executable()
            self.assertEqual(
                str(caught.exception),
                f"Electron {VERSION} has no pinned checksum. Add its SHA-256 values to "
                "ELECTRON_SHA256 in desktop_app.py, then start again.",
            )
        self.get.assert_not_called()

    def test_offline_is_one_sentence(self):
        for problem in (requests.ConnectionError("refused"), requests.Timeout("slow")):
            self.get.side_effect = problem
            with self.assertRaises(ValueError) as caught:
                desktop_app.executable()
            self.assertEqual(str(caught.exception), desktop_app.OFFLINE)
        self.assertIn("could not download its desktop runtime", desktop_app.OFFLINE)
        self.assertFalse(self.runtime.exists())

    def test_incomplete_runtime_that_cannot_be_removed_names_the_folder(self):
        self.runtime.mkdir(parents=True)
        real = desktop_app.shutil.rmtree

        def rmtree(path, *args, **kwargs):
            if Path(path) == self.runtime:
                raise PermissionError("in use")
            return real(path, *args, **kwargs)

        self.use(desktop_app.shutil, "rmtree", new=rmtree)
        with self.assertRaises(ValueError) as caught:
            desktop_app.executable()
        self.assertEqual(
            str(caught.exception),
            f"An incomplete desktop runtime is in the way. Delete the folder {self.runtime}, "
            "then start Agent Workbench again.",
        )

    def test_archive_without_the_executable_is_refused(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as z:
            z.writestr("readme.txt", "nothing to run")
        self.body = data.getvalue()
        self.pins[VERSION]["linux-x64"] = hashlib.sha256(self.body).hexdigest()
        with self.assertRaisesRegex(ValueError, "missing its desktop executable"):
            desktop_app.executable()
        self.assertFalse(self.runtime.exists())

    def test_unsupported_machine_is_refused_before_any_request(self):
        self.use(desktop_app.platform, "machine", return_value="riscv64")
        with self.assertRaisesRegex(ValueError, "x64 Windows or Linux"):
            desktop_app.executable()
        self.get.assert_not_called()


class SandboxTest(DesktopAppCase):
    def setUp(self):
        super().setUp()
        self.setting = self.base / "userns"
        self.setting.write_text("1\n")
        self.profiles = self.base / "apparmor.d"
        self.profiles.mkdir()
        self.use(desktop_app, "USERNS", new=((str(self.setting), "1"),))
        self.use(desktop_app, "APPARMOR", new=self.profiles)
        self.nosuid = self.use(desktop_app, "_nosuid", return_value=False)
        self.runtime.mkdir(parents=True)
        self.engine = self.runtime / "electron"
        self.helper = self.runtime / "chrome-sandbox"
        self.helper.write_text("helper")

    def test_sandbox_problem_matrix(self):
        problem = desktop_app.sandbox_problem
        self.assertEqual(problem(self.engine, "darwin"), "")
        self.assertEqual(problem(self.engine, "win32"), "")
        with patch.object(sys, "platform", "darwin"):
            self.assertEqual(problem(self.engine), "")

        self.setting.write_text("0\n")
        self.assertEqual(problem(self.engine), "")
        self.setting.unlink()
        self.assertEqual(problem(self.engine), "")
        self.setting.write_text("1\n")

        self.helper.unlink()
        self.assertEqual(problem(self.engine), "")
        self.helper.write_text("helper")

        with patch.object(desktop_app, "_helper_ready", return_value=False):
            found = problem(self.engine)
        helper = shlex.quote(str(self.helper))
        self.assertEqual(
            found,
            "Linux is blocking the desktop sandbox on this system. Run this once in a terminal, "
            f"then start Agent Workbench again: sudo chown root:root {helper} && sudo chmod 4755 {helper}",
        )
        with patch.object(desktop_app, "_helper_ready", return_value=True):
            self.assertEqual(problem(self.engine), "")

        self.nosuid.return_value = True
        with patch.object(desktop_app, "_helper_ready", return_value=False):
            blocked = problem(self.engine)
        self.assertTrue(blocked.startswith("Linux is blocking the desktop sandbox, and "))
        self.assertIn(f"{self.runtime} is on a disk mounted nosuid", blocked)
        self.assertTrue(blocked.endswith("then start Agent Workbench again."))
        self.assertNotIn("sudo", blocked)
        self.assertNotIn("no-sandbox", found + blocked)

    def test_an_installed_profile_counts_as_set_up(self):
        problem = desktop_app.sandbox_problem
        ready = patch.object(desktop_app, "_helper_ready", return_value=False)
        ready.start()
        self.addCleanup(ready.stop)
        self.assertIn("sudo chown root:root", problem(self.engine))

        nested = self.profiles / "local"
        nested.mkdir()
        runtime = self.data / "desktop-runtime"
        wanted = f"profile agent-workbench {runtime}/*/electron flags=(unconfined) {{}}\n"
        (nested / "agent-workbench").write_text(wanted)
        (self.profiles / "big").write_text(wanted + "#" * 65536)
        (self.profiles / "other").write_text("profile chrome /opt/chrome/chrome {}\n")
        self.assertIn("sudo chown root:root", problem(self.engine))

        (self.profiles / "agent-workbench").write_text(wanted)
        self.assertEqual(problem(self.engine), "")
        self.nosuid.return_value = True
        self.assertEqual(problem(self.engine), "")
        self.nosuid.return_value = False

        (self.profiles / "agent-workbench").write_text(
            f"profile agent-workbench {self.runtime}/electron flags=(unconfined) {{}}\n"
        )
        self.assertEqual(problem(self.engine), "")
        (self.profiles / "agent-workbench").write_text(
            f"profile agent-workbench {runtime} flags=(unconfined) {{}}\n"
        )
        self.assertIn("sudo chown root:root", problem(self.engine))
        with patch.object(desktop_app, "APPARMOR", self.base / "missing"):
            self.assertIn("sudo chown root:root", problem(self.engine))

    def test_fix_command_quotes_the_path(self):
        spaced = self.data / "desktop-runtime" / "linux with space"
        spaced.mkdir()
        (spaced / "chrome-sandbox").write_text("helper")
        with patch.object(desktop_app, "_helper_ready", return_value=False):
            found = desktop_app.sandbox_problem(spaced / "electron")
        quoted = f"'{spaced / 'chrome-sandbox'}'"
        self.assertTrue(
            found.endswith(f"sudo chown root:root {quoted} && sudo chmod 4755 {quoted}")
        )

    def test_helper_and_mount_readers(self):
        self.assertIsNone(desktop_app._helper_ready(self.runtime / "missing"))
        owned = Mock(st_uid=0, st_mode=0o104755)
        plain = Mock(st_uid=0, st_mode=0o100755)
        mine = Mock(st_uid=1000, st_mode=0o104755)
        for info, expected in ((owned, True), (plain, False), (mine, False)):
            helper = Mock(stat=Mock(return_value=info))
            self.assertIs(desktop_app._helper_ready(helper), expected)
        self.assertEqual(desktop_app._read(self.base / "missing"), "")
        self.assertEqual(desktop_app._read(self.setting), "1")


class MountTest(unittest.TestCase):
    def test_nosuid_reads_the_mount_flag(self):
        self.assertIs(desktop_app._nosuid("/definitely/not/a/folder"), False)
        flag = getattr(os, "ST_NOSUID", 2)
        with (
            patch.object(os, "ST_NOSUID", flag, create=True),
            patch.object(os, "statvfs", Mock(return_value=Mock(f_flag=flag | 1)), create=True),
        ):
            self.assertIs(desktop_app._nosuid("/data"), True)
        with (
            patch.object(os, "ST_NOSUID", flag, create=True),
            patch.object(os, "statvfs", Mock(return_value=Mock(f_flag=1)), create=True),
        ):
            self.assertIs(desktop_app._nosuid("/data"), False)


class LaunchTest(DesktopAppCase):
    def test_launch_maps_exit_status(self):
        run = self.use(desktop_app.subprocess, "run", return_value=Mock(returncode=-5))
        engine = self.runtime / "electron"
        with patch.dict(os.environ, {"ELECTRON_RUN_AS_NODE": "1"}):
            with patch.object(desktop_app, "sandbox_problem", return_value="FIX") as why:
                with self.assertRaises(ValueError) as caught:
                    desktop_app.launch(engine)
                self.assertEqual(str(caught.exception), "FIX")
                why.assert_called_once_with(engine)
            with patch.object(desktop_app, "sandbox_problem", return_value=""):
                with self.assertRaises(ValueError) as caught:
                    desktop_app.launch(engine)
        self.assertEqual(
            str(caught.exception),
            "The desktop window closed unexpectedly (signal 5). Start Agent Workbench again; "
            "if it repeats, run cli.py doctor in the Agent Workbench folder.",
        )
        call = run.call_args
        self.assertEqual(call.args[0], [str(engine), str(self.desktop)])
        self.assertNotIn("check", call.kwargs)
        self.assertNotIn("ELECTRON_RUN_AS_NODE", call.kwargs["env"])
        self.assertEqual(call.kwargs["env"]["WORKBENCH_PYTHON"], sys.executable)
        self.assertNotIn("--no-sandbox", call.args[0])

        clock = Mock(monotonic=Mock(side_effect=[0.0, 100.0]))
        shown = io.StringIO()
        with (
            patch.object(desktop_app, "time", clock),
            patch.object(desktop_app, "sandbox_problem") as why,
            contextlib.redirect_stderr(shown),
        ):
            self.assertEqual(desktop_app.launch(engine), -5)
        why.assert_not_called()
        self.assertIn("signal 5", shown.getvalue())

        run.return_value = Mock(returncode=0)
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-1"}, clear=False):
            os.environ.pop("ELECTRON_OZONE_PLATFORM_HINT", None)
            self.assertEqual(desktop_app.launch(engine), 0)
            self.assertEqual(run.call_args.kwargs["env"]["ELECTRON_OZONE_PLATFORM_HINT"], "auto")
        self.assertIn("exit code 7", desktop_app.exit_text(7))

    def test_prepare(self):
        engine = self.runtime / "electron"

        def fake(progress=None):
            for done in (1 << 20, 2 << 20, 3 << 20):
                progress(done, 3 << 20)
            return engine

        self.use(desktop_app, "executable", new=fake)
        for problem, status in (("", 0), ("FIX", 3)):
            out, err = io.StringIO(), io.StringIO()
            with (
                patch.object(desktop_app, "sandbox_problem", return_value=problem),
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
            ):
                self.assertEqual(desktop_app.main(["--prepare"]), status)
            self.assertEqual(out.getvalue(), f"{engine}\n")
            self.assertEqual(err.getvalue().count("Downloading the desktop runtime"), 1)
            self.assertEqual("FIX" in err.getvalue(), bool(problem))

    def test_main_returns_the_window_status(self):
        engine = self.runtime / "electron"
        self.use(desktop_app, "executable", new=lambda progress=None: engine)
        launch = self.use(desktop_app, "launch", return_value=0)
        self.assertEqual(desktop_app.main([]), 0)
        launch.assert_called_once_with(engine)


if __name__ == "__main__":
    unittest.main()
