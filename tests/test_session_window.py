"""session_window.py: the launch handshake, failed-start records, live-session identity and pruning."""

import atexit
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

if "runtime" not in sys.modules and "WORKBENCH_DATA" not in os.environ:
    os.environ["WORKBENCH_DATA"] = tempfile.mkdtemp(prefix="awb-test-")
    atexit.register(shutil.rmtree, os.environ["WORKBENCH_DATA"], ignore_errors=True)

import psutil

import host
import session_window

SLEEPER = "import time; time.sleep(0.3)"
BRIEF = "import time; time.sleep(0.15)"


def reaped_pid():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


class SessionWindowTests(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp(prefix="awb-sw-")).resolve()
        self.addCleanup(shutil.rmtree, self.data, ignore_errors=True)
        self.folder = self.data / "interactive"
        self.folder.mkdir()
        self.addCleanup(signal.signal, signal.SIGINT, signal.getsignal(signal.SIGINT))
        for target, name, value in (
            (session_window, "DATA", self.data),
            (session_window, "SETTLE", 0.05),
            (sys, "stderr", io.StringIO()),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        stdin = MagicMock()
        stdin.isatty.return_value = False
        patcher = patch.object(sys, "stdin", stdin)
        patcher.start()
        self.addCleanup(patcher.stop)

    def read(self, name):
        return json.loads((self.folder / name).read_text(encoding="utf8"))

    def write(self, name, data, age=0):
        path = self.folder / name
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf8")
        if age:
            os.utime(path, (time.time() - age, time.time() - age))
        return path

    def test_run_reports_an_early_exit(self):
        with patch.object(session_window, "SETTLE", 10):
            code = session_window.run(
                "t1", "Fake", [sys.executable, "-c", "raise SystemExit(3)"]
            )
        self.assertEqual(code, 3)
        self.assertIn("Fake exited with code 3 right after starting", self.read("t1.ready")["error"])
        mark = self.read("t1.exit")
        self.assertEqual(mark["code"], 3)
        self.assertEqual(mark["harness"], "Fake")
        self.assertEqual(mark["workspace"], os.getcwd())
        self.assertEqual(mark["message"], self.read("t1.ready")["error"])
        self.assertFalse((self.folder / "t1.json").exists())
        self.assertIn("exited with code 3", sys.stderr.getvalue())

    def test_run_confirms_a_surviving_child(self):
        seen = {}

        def watch():
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not (self.folder / "t2.ready").exists():
                time.sleep(0.01)
            try:
                seen["ready"] = self.read("t2.ready")
                seen["record"] = self.read("t2.json")
            except (OSError, ValueError) as exc:
                seen["problem"] = exc

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        code = session_window.run(
            "t2", "Fake", [sys.executable, "-c", SLEEPER, "--model", "qwen3-coder"]
        )
        watcher.join(5)
        self.assertEqual(code, 0)
        self.assertNotIn("problem", seen)
        self.assertEqual(seen["ready"], {"ok": True})
        record = seen["record"]
        self.assertEqual(record["id"], "t2")
        self.assertEqual(record["kind"], "native")
        self.assertEqual(record["harness"], "Fake")
        self.assertEqual(record["workspace"], os.getcwd())
        self.assertEqual(record["model"], "qwen3-coder")
        self.assertIsInstance(record["pid"], int)
        self.assertIsInstance(record["born"], float)
        self.assertRegex(record["cmd"], r"^[0-9a-f]{16}$")
        self.assertLessEqual(record["started"], time.time())
        self.assertFalse((self.folder / "t2.exit").exists())
        self.assertFalse((self.folder / "t2.json").exists())
        self.assertEqual(self.read("t2.ready"), {"ok": True})

    def test_run_inline_writes_no_handshake(self):
        child = (
            "import signal, sys; "
            "sys.exit(0 if signal.getsignal(signal.SIGINT) is signal.default_int_handler else 5)"
        )
        code = session_window.run(
            "t3", "Fake", [sys.executable, "-c", child], env=dict(os.environ), handshake=False
        )
        self.assertEqual(code, 0)
        self.assertEqual(list(self.folder.iterdir()), [])
        self.assertIs(signal.getsignal(signal.SIGINT), signal.SIG_IGN)

    def test_run_inline_records_a_quick_failure_without_a_handshake(self):
        with patch.object(session_window, "SETTLE", 10):
            code = session_window.run(
                "t4", "Fake", [sys.executable, "-c", "raise SystemExit(2)"], handshake=False
            )
        self.assertEqual(code, 2)
        self.assertEqual([p.name for p in self.folder.iterdir()], ["t4.exit"])

    def test_run_reports_a_late_quick_failure_after_confirming(self):
        child = BRIEF + "; raise SystemExit(7)"
        code = session_window.run("t5", "Fake", [sys.executable, "-c", child])
        self.assertEqual(code, 7)
        self.assertEqual(self.read("t5.ready"), {"ok": True})
        mark = self.read("t5.exit")
        self.assertEqual(mark["code"], 7)
        self.assertRegex(mark["message"], r"^Fake exited with code 7 after \d+ seconds; ")
        with patch.object(session_window, "QUICK", 0):
            self.assertEqual(session_window.run("t6", "Fake", [sys.executable, "-c", child]), 7)
        self.assertFalse((self.folder / "t6.exit").exists())

    def test_run_refuses_unsafe_batch_arguments(self):
        with (
            patch.object(host, "WINDOWS", True),
            patch.object(subprocess, "Popen") as popen,
        ):
            code = session_window.run("t7", "Codex", ["x.cmd", "a&b"])
        self.assertEqual(code, 1)
        popen.assert_not_called()
        error = self.read("t7.ready")["error"]
        self.assertIn("x.cmd is a Windows batch launcher", error)
        self.assertNotIn("a&b", error)
        self.assertEqual(self.read("t7.exit")["message"], error)
        self.assertFalse((self.folder / "t7.json").exists())

    def test_run_reports_a_missing_program(self):
        code = session_window.run("t8", "Fake", [str(self.data / "no-such-agent")])
        self.assertEqual(code, 1)
        error = self.read("t8.ready")["error"]
        self.assertIn("Fake could not be started (", error)
        self.assertIn("open Setup and reinstall it.", error)
        self.assertNotIn("Traceback", sys.stderr.getvalue())

    def test_run_keeps_waiting_when_ticket_files_cannot_be_written(self):
        with patch.object(session_window, "write_json", side_effect=OSError("disk full")):
            code = session_window.run("t9", "Fake", [sys.executable, "-c", BRIEF])
        self.assertEqual(code, 0)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_run_reports_the_exit_when_its_record_cannot_be_removed(self):
        remove = Path.unlink

        def held_open(path, missing_ok=False):
            if path.suffix == ".json":
                raise PermissionError(13, "The file is in use by another process")
            return remove(path, missing_ok=missing_ok)

        with (
            patch.object(session_window, "SETTLE", 10),
            patch.object(Path, "unlink", held_open),
        ):
            code = session_window.run(
                "t11", "Fake", [sys.executable, "-c", "raise SystemExit(3)"]
            )
        self.assertEqual(code, 3)
        self.assertIn("exited with code 3", self.read("t11.ready")["error"])
        self.assertEqual(self.read("t11.exit")["code"], 3)
        with patch.object(psutil, "process_iter", return_value=[]):
            self.assertEqual(session_window.active_sessions(), [])
        self.assertFalse((self.folder / "t11.json").exists())

    def test_run_waits_for_the_harness_without_a_ticket_folder(self):
        shutil.rmtree(self.folder)
        self.folder.write_text("not a folder", encoding="utf8")
        self.assertEqual(session_window.run("t12", "Fake", [sys.executable, "-c", BRIEF]), 0)
        with patch.object(session_window, "SETTLE", 10):
            code = session_window.run(
                "t13", "Fake", [sys.executable, "-c", "raise SystemExit(6)"]
            )
        self.assertEqual(code, 6)
        self.assertIn("Fake exited with code 6", sys.stderr.getvalue())
        self.assertTrue(self.folder.is_file())

    def test_run_works_off_the_main_thread(self):
        result = {}
        worker = threading.Thread(
            target=lambda: result.update(
                code=session_window.run("t10", "Fake", [sys.executable, "-c", "pass"])
            )
        )
        worker.start()
        worker.join(10)
        self.assertEqual(result, {"code": 0})
        self.assertEqual(self.read("t10.ready"), {"ok": True})

    def test_failed_start_pauses_only_in_a_window_that_would_close(self):
        sys.stdin.isatty.return_value = True
        with (
            patch.object(host, "WINDOWS", True),
            patch.object(subprocess, "Popen"),
            patch("builtins.input", side_effect=EOFError) as asked,
        ):
            with patch.object(sys, "platform", "linux"):
                session_window.run("p1", "Codex", ["x.cmd", "a&b"])
                self.assertEqual(asked.call_count, 1)
                session_window.run("p2", "Codex", ["x.cmd", "a&b"], handshake=False)
                self.assertEqual(asked.call_count, 1)
            with patch.object(sys, "platform", "darwin"):
                session_window.run("p3", "Codex", ["x.cmd", "a&b"])
                self.assertEqual(asked.call_count, 1)
            sys.stdin.isatty.return_value = False
            with patch.object(sys, "platform", "win32"):
                session_window.run("p4", "Codex", ["x.cmd", "a&b"])
                self.assertEqual(asked.call_count, 1)

    def test_model_of(self):
        self.assertEqual(session_window.model_of(["codex", "--model", "m", "-c", "a=1"]), "m")
        self.assertEqual(session_window.model_of(["codex", "--sandbox", "x"]), "")
        self.assertEqual(session_window.model_of(["codex", "--model"]), "")

    def test_active_sessions_prunes_dead_records(self):
        mine = host.process_record(os.getpid()) | dict(harness="OMP", workspace="/w")
        legacy = dict(
            pid=os.getpid(),
            born=psutil.Process().create_time(),
            harness="Pi",
            workspace="/old",
        )
        self.write("dead.json", mine | {"pid": reaped_pid()})
        self.write("live.json", mine)
        self.write("legacy.json", legacy)
        self.write("list.json", [1, 2])
        self.write("broken.json", "{not json")
        self.write("old.ready", {"ok": True}, age=120)
        self.write("new.ready", {"ok": True})
        self.write("old.exit", {}, age=700)
        self.write("new.exit", {}, age=300)
        with patch.object(psutil, "process_iter", return_value=[]):
            found = session_window.active_sessions()
        self.assertCountEqual(found, [mine, legacy])
        for key in ("pid", "born", "harness", "workspace"):
            self.assertTrue(all(key in row for row in found))
        self.assertEqual(
            sorted(p.name for p in self.folder.iterdir()),
            ["broken.json", "legacy.json", "live.json", "new.exit", "new.ready"],
        )

    def test_active_sessions_survives_a_dead_record_that_cannot_be_removed(self):
        mine = host.process_record(os.getpid()) | dict(harness="OMP", workspace="/w")
        self.write("dead.json", mine | {"pid": reaped_pid()})
        self.write("live.json", mine)
        with (
            patch.object(psutil, "process_iter", return_value=[]),
            patch.object(Path, "unlink", side_effect=PermissionError(13, "in use")),
        ):
            self.assertEqual(session_window.active_sessions(), [mine])
        self.assertTrue((self.folder / "dead.json").exists())

    def test_active_sessions_hides_but_keeps_an_unconfirmed_record(self):
        mine = host.process_record(os.getpid()) | dict(harness="OMP", workspace="/w")
        self.write("moved.json", mine | {"born": mine["born"] + 3600, "cmd": "0" * 16})
        self.write("stepped.json", mine | {"born": mine["born"] + 3600})
        with patch.object(psutil, "process_iter", return_value=[]):
            found = session_window.active_sessions()
        self.assertEqual([row["cmd"] for row in found], [mine["cmd"]])
        self.assertTrue((self.folder / "moved.json").exists())
        self.assertTrue((self.folder / "stepped.json").exists())

    def test_active_sessions_still_finds_ticketless_omp_windows(self):
        def fake(pid, name, argv):
            proc = MagicMock(pid=pid, info={"name": name})
            proc.cmdline.return_value = argv
            proc.create_time.return_value = 5.0
            return proc

        listed = [
            fake(11, "omp", ["omp", "--profile", "workbench-abc", "--cwd", "/proj"]),
            fake(12, "omp", ["omp", "--profile", "personal", "--cwd", "/other"]),
            fake(13, "omp", ["omp", "--profile", "workbench-abc", "--cwd", "/proj", "-p"]),
            fake(14, "zsh", ["zsh"]),
            fake(15, None, []),
        ]
        shutil.rmtree(self.folder)
        with patch.object(psutil, "process_iter", return_value=listed):
            found = session_window.active_sessions()
        self.assertEqual(found, [dict(pid=11, born=5.0, harness="OMP", workspace="/proj")])

    def test_recent_exits_validates(self):
        good = dict(
            harness="Codex", workspace="/w", code=1, seconds=2.4, ended=1.0, message="Codex exited."
        )
        self.write("good.exit", good, age=5)
        self.write("list.exit", [good])
        self.write("old.exit", good | {"message": "old"}, age=700)
        self.write("text-code.exit", good | {"code": "1"})
        self.write("flag-code.exit", good | {"code": True})
        self.write("no-message.exit", {k: v for k, v in good.items() if k != "message"})
        self.write("broken.exit", "{")
        self.assertEqual(session_window.recent_exits(), [good])
        self.assertEqual(
            [row["message"] for row in session_window.recent_exits(max_age=900)],
            ["Codex exited.", "old"],
        )
        for number in range(12):
            self.write(f"n{number}.exit", good | {"message": str(number)}, age=20 + number)
        rows = session_window.recent_exits()
        self.assertEqual(len(rows), 10)
        self.assertEqual([row["message"] for row in rows[:3]], ["Codex exited.", "0", "1"])

    def test_recent_exits_without_a_folder(self):
        shutil.rmtree(self.folder)
        self.assertEqual(session_window.recent_exits(), [])


if __name__ == "__main__":
    unittest.main()
