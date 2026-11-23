"""project_terminal.py: PTY close and shutdown, the output buffer and mode replay, and the term-* actions."""

import atexit
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import MagicMock, patch

if "runtime" not in sys.modules and "WORKBENCH_DATA" not in os.environ:
    os.environ["WORKBENCH_DATA"] = tempfile.mkdtemp(prefix="awb-test-")
    atexit.register(shutil.rmtree, os.environ["WORKBENCH_DATA"], ignore_errors=True)

import project_terminal as pt
import sessions

try:
    import resource
except ImportError:
    resource = None

POSIX = os.name != "nt"
NOFILE = None
ENDED = "This terminal has ended; open a new one."
WAITING = "The program in this terminal has not read your last input yet; wait for it, or close the terminal."
STUBBORN = ["/bin/sh", "-c", "trap '' HUP INT; echo READY; exec sleep 60"]
DEAF = ["/bin/sh", "-c", "echo READY; exec sleep 60"]
DETACHED = ["/bin/sh", "-c", "echo READY; exec sleep 60 <&- >&- 2>&-"]
SHOW_ENV = ["/bin/sh", "-c", 'echo "T=$TERM;P=$TERM_PROGRAM;C=$WORKBENCH_CALLER_CWD;"']


def setUpModule():
    """ptyprocess closes every descriptor number up to the limit in each child, so a huge limit slows every spawn."""
    global NOFILE
    if resource:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft == resource.RLIM_INFINITY or soft > 4096:
            NOFILE = (soft, hard)
            resource.setrlimit(resource.RLIMIT_NOFILE, (4096, hard))


def tearDownModule():
    if NOFILE:
        resource.setrlimit(resource.RLIMIT_NOFILE, NOFILE)


def fake_sessions():
    return types.SimpleNamespace(
        project_dir=MagicMock(side_effect=sessions.project_dir),
        recent=MagicMock(return_value=[]),
        find=MagicMock(return_value=None),
        resume=MagicMock(side_effect=lambda harness, argv, env, row=None: (argv + ["--again"], dict(env, AGAIN="1"))),
        remember_remote=MagicMock(),
    )


def timed(call, limit):
    """Run call in a worker so a blocked close fails the test instead of hanging it. No 'seconds' key means blocked."""
    box = {}

    def work():
        began = time.monotonic()
        try:
            box["value"] = call()
        except BaseException as exc:
            box["error"] = exc
        box["seconds"] = time.monotonic() - began

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(limit)
    return box


def running(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def reap(session):
    try:
        if session.proc.isalive():
            session.proc.kill(signal.SIGKILL)
    except pt.GONE:
        pass


class WindowsOS:
    """Stands in for the os module inside project_terminal; patching os.name itself breaks pathlib on the test host."""

    name = "nt"

    def __getattr__(self, key):
        return getattr(os, key)


def bare(**fields):
    session = pt.Session.__new__(pt.Session)
    values = dict(
        lock=threading.Lock(), modes={}, offset=0, output="", ended=True, harness=None, size=(20, 96),
        closing=threading.Lock(), done=False, stop=threading.Event(), writing=threading.Lock(),
    )
    for key, value in (values | fields).items():
        setattr(session, key, value)
    return session


class Case(unittest.TestCase):
    def setUp(self):
        self.folder = str(Path(tempfile.mkdtemp(prefix="awb-pt-")).resolve())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.sessions = fake_sessions()
        for item in (patch.object(pt, "sessions", self.sessions), patch.dict(pt.SESSIONS, clear=True)):
            item.start()
            self.addCleanup(item.stop)

    def call(self, action, **payload):
        return pt.dispatch(action, dict(workspace=self.folder) | payload)

    def spawn(self, argv, env=None, folder=None, patient=False, **more):
        session = pt.Session(argv, os.environ if env is None else env, folder or self.folder, "Test", **more)
        self.addCleanup(reap, session)
        if not patient:
            # ptyprocess waits this long after each signal; term-open and the tests that pass patient keep its default.
            session.proc.delayafterterminate = 0.02
        return session

    def wait(self, check, limit=3):
        until = time.monotonic() + limit
        while time.monotonic() < until:
            if check():
                return True
            time.sleep(0.01)
        return bool(check())

    def idle(self, session, marker=""):
        """Wait for the child's prompt or marker, so the reader is blocked in read() when the test continues."""
        found = self.wait(lambda: marker in session.snapshot(0)["text"] and session.snapshot(0)["text"], 2)
        time.sleep(0.02)
        return found

    def assert_closed(self, session, outcome):
        self.assertIn("seconds", outcome, "close did not return in time")
        self.assertNotIn("error", outcome)
        self.assertLess(outcome["seconds"], 2)
        self.assertFalse(session.reader.is_alive())
        self.assertFalse(running(session.proc.pid))
        self.assertTrue(session.ended)


class BufferTests(Case):
    def test_safe_cut_keeps_escape_sequences_whole(self):
        mode = "plain\x1b[?2004h"
        for cut in range(6, len(mode)):
            self.assertEqual(pt.safe_cut(mode, cut), 5)
        self.assertEqual(pt.safe_cut(mode, len(mode)), len(mode))
        title = "plain\x1b]0;title"
        self.assertEqual(pt.safe_cut(title, len(title)), 5)
        self.assertEqual(pt.safe_cut(title + "\x07", len(title) + 1), len(title) + 1)
        self.assertEqual(pt.safe_cut(title + "\x1b\\", len(title) + 2), len(title) + 2)
        self.assertEqual(pt.safe_cut("ab\x1b", 3), 2)
        self.assertEqual(pt.safe_cut("plain text only", 7), 7)
        self.assertEqual(pt.safe_cut("\x1b[31mred", 6), 6)
        self.assertEqual(pt.safe_cut("", 0), 0)

    def test_snapshot_holds_incomplete_sync_frame_and_trailing_escape(self):
        frame = "before\x1b[?2026h\x1b[2Jpartial"
        now = time.monotonic()
        session = bare(output=frame, ended=False, last_read=now)
        first = session.snapshot(0)
        self.assertEqual(first["text"], "before")
        self.assertEqual(first["offset"], 6)
        session.output += "\x1b[?2026l"
        session.last_read = time.monotonic()
        done = session.snapshot(first["offset"])
        self.assertEqual(done["text"], "\x1b[?2026h\x1b[2Jpartial\x1b[?2026l")
        self.assertEqual(done["offset"], len(session.output))
        stalled = bare(output=frame, ended=False, last_read=now - pt.SYNC_HOLD - 0.1)
        self.assertEqual(stalled.snapshot(0)["text"], frame)
        partial_esc = bare(output="hello\x1b[38;2;", ended=False, last_read=time.monotonic())
        held = partial_esc.snapshot(0)
        self.assertEqual(held["text"], "hello")
        self.assertEqual(held["offset"], 5)

    def test_safe_cut_keeps_long_strings_and_charset_selects_whole(self):
        link = "plain\x1b]8;;https://example.test/" + "a" * 300
        self.assertEqual(pt.safe_cut(link, len(link)), 5)
        self.assertEqual(pt.safe_cut(link + "\x07shown", len(link) + 3), len(link) + 3)
        self.assertEqual(pt.safe_cut(link + "\x1b\\shown", len(link) + 1), len(link))
        self.assertEqual(pt.safe_cut(link + "\x1b\\shown", len(link) + 4), len(link) + 4)
        for opener in ("P", "_", "^", "X"):
            block = "plain\x1b" + opener + "q" * 200
            self.assertEqual(pt.safe_cut(block, len(block)), 5)
            self.assertEqual(pt.safe_cut(block + "\x1b\\after", len(block) + 4), len(block) + 4)
        self.assertEqual(pt.safe_cut("ab\x1b(B", 4), 2)
        self.assertEqual(pt.safe_cut("ab\x1b(Bcd", 5), 5)
        self.assertEqual(pt.safe_cut("ab\x1b#8", 4), 2)
        self.assertEqual(pt.safe_cut("ab\x1b7cd", 4), 4)
        far = "\x1b]0;never ended" + "x" * (pt.REACH + 10)
        self.assertEqual(pt.safe_cut(far, len(far)), len(far))

    def test_trim_never_starts_the_tail_inside_a_long_title(self):
        title = "\x1b]0;" + "t" * 500 + "\x07"
        session = bare(output="a" * (pt.SLACK - 100) + title + "x" * (pt.LIMIT + 100 - len(title)))
        session.trim()
        self.assertEqual(session.offset, pt.SLACK - 100)
        self.assertTrue(session.output.startswith(title + "x"))

    def test_reset_reply_restores_modes(self):
        written = "\x1b[?1049h\x1b[?2004h" + "x" * (pt.LIMIT + pt.SLACK)
        session = bare(output=written)
        session.trim()
        self.assertEqual(session.offset + len(session.output), len(written))
        self.assertGreater(session.offset, 0)
        late = session.snapshot(0)
        self.assertTrue(late["reset"])
        self.assertTrue(late["text"].startswith("\x1b[?1049h\x1b[?2004h"))
        self.assertEqual(late["text"][16:], session.output)
        self.assertEqual(late["offset"], len(written))
        current = session.snapshot(session.offset)
        self.assertFalse(current["reset"])
        self.assertEqual(current["text"], session.output)
        self.assertEqual(session.snapshot(len(written)), dict(text="", offset=len(written), ended=True, reset=False))

    def test_trim_records_the_last_state_of_known_modes_only(self):
        session = bare(output="\x1b[?25l\x1b[?1049;2004h\x1b[?1049l\x1b[?7h\x1b[31m" + "x" * (pt.LIMIT + pt.SLACK))
        session.trim()
        self.assertEqual(session.modes, {25: False, 1049: False, 2004: True})
        self.assertTrue(session.snapshot(0)["text"].startswith("\x1b[?25l\x1b[?1049l\x1b[?2004h" + "x"))

    def test_trim_waits_for_slack_and_never_splits_a_sequence(self):
        session = bare(output="a" * (pt.LIMIT + pt.SLACK - 1))
        session.trim()
        self.assertEqual((session.offset, len(session.output)), (0, pt.LIMIT + pt.SLACK - 1))
        session = bare(output="a" * pt.SLACK + "\x1b[?25l" + "x" * (pt.LIMIT - 3))
        session.trim()
        self.assertEqual(session.offset, pt.SLACK)
        self.assertTrue(session.output.startswith("\x1b[?25lx"))
        self.assertEqual(session.modes, {})

    def test_reset_nudges_agent_sessions_only(self):
        proc = MagicMock()
        session = bare(proc=proc, harness="OMP", ended=False, offset=10, output="tail")
        self.assertTrue(session.snapshot(0)["reset"])
        self.assertEqual([c.args for c in proc.setwinsize.call_args_list], [(20, 97)])
        self.assertTrue(self.wait(lambda: proc.setwinsize.call_count == 2, 0.5))
        self.assertEqual(proc.setwinsize.call_args_list[-1].args, (20, 96))
        proc.reset_mock()
        self.assertFalse(session.snapshot(10)["reset"])
        proc.setwinsize.assert_not_called()
        for fields, repaint in (
            (dict(ended=False, modes={1049: True}), True),
            (dict(ended=False, modes={1047: True}), True),
            (dict(ended=False), False),
            (dict(ended=False, modes={1049: False, 2004: True}), False),
            (dict(harness="OMP", ended=True), False),
        ):
            with self.subTest(fields=fields), patch.object(pt.Session, "nudge") as nudge:
                self.assertTrue(bare(offset=10, output="tail", **fields).snapshot(0)["reset"])
                self.assertEqual(nudge.call_count, int(repaint))

    def test_nudge_settles_on_the_newest_size_and_survives_a_closed_terminal(self):
        proc = MagicMock()
        session = bare(proc=proc)
        session.nudge()
        session.resize(30, 100)
        self.assertTrue(self.wait(lambda: proc.setwinsize.call_count == 3, 0.5))
        self.assertEqual(proc.setwinsize.call_args_list[-1].args, (30, 100))
        proc = MagicMock()
        proc.setwinsize.side_effect = OSError("closed")
        bare(proc=proc).nudge()
        self.assertEqual(proc.setwinsize.call_count, 1)

    def test_resize_clamps(self):
        proc = MagicMock()
        session = bare(proc=proc)
        session.resize("1000", 5)
        self.assertEqual(session.size, (120, 16))
        session.resize(1, 1000)
        self.assertEqual(session.size, (4, 400))
        self.assertEqual([c.args for c in proc.setwinsize.call_args_list], [(120, 16), (4, 400)])


class DispatchTests(Case):
    def recorder(self):
        made = MagicMock(side_effect=lambda *a, **k: types.SimpleNamespace(project=self.folder, ended=False))
        item = patch.object(pt, "Session", made)
        item.start()
        self.addCleanup(item.stop)
        return made

    def agent(self, **chosen):
        s = dict(harness="Codex", provider="OpenRouter", remote_model="vendor/model", model="/models/old.gguf") | chosen
        bridge = types.SimpleNamespace(selection=MagicMock(return_value=s), choices=MagicMock(return_value=[]))
        for item in (
            patch.dict(sys.modules, {"desktop_bridge": bridge}),
            patch.object(pt.runtime, "settings", return_value={}),
        ):
            item.start()
            self.addCleanup(item.stop)
        ready = patch.object(pt.connections, "ready_session", return_value=(["codex", "--model", "m"], {"KEY": "1"}, "/x"))
        self.ready = ready.start()
        self.addCleanup(ready.stop)
        return self.recorder()

    def machines(self, **changes):
        def target(value):
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError("Use an SSH config alias or user@hostname.")
            return value

        fields = dict(
            target=target,
            labels=lambda: {"me@box": "GPU box"},
            remote_command=MagicMock(return_value="exec omp"),
            ssh=lambda host: ["ssh"],
            windows=lambda host: False,
        )
        return types.SimpleNamespace(**(fields | changes))

    def test_empty_or_relative_workspace_is_refused(self):
        for payload, raw in (({"workspace": ""}, ""), ({"workspace": "docs"}, "docs"), ({}, None)):
            with self.subTest(payload=payload):
                for action in ("term-list", "term-open", "term-close"):
                    with self.assertRaises(ValueError) as caught:
                        pt.dispatch(action, payload)
                    self.assertIn("Choose a project folder", str(caught.exception))
                    self.assertEqual(self.sessions.project_dir.call_args.args, (raw,))

    def test_history_rows_come_from_sessions(self):
        row = dict(
            id="a" * 64, title="Fix login", date="2026-11-22T20:14:03.120Z", updated="2026-11-22T21:02:44.000Z",
            harness="OMP", store="account", command="omp --resume /x.jsonl", session="s", home="/h", path="/x.jsonl",
        )
        self.sessions.recent.return_value = [row]
        shown = {k: row[k] for k in ("id", "title", "date", "updated", "harness", "store", "command")}
        self.assertEqual(self.call("term-history"), [shown])
        self.sessions.recent.assert_called_with(self.folder, "OMP", 80)
        self.call("term-history", harness="")
        self.sessions.recent.assert_called_with(self.folder, "OMP", 80)
        self.call("term-history", harness="Claude Code")
        self.sessions.recent.assert_called_with(self.folder, "Claude Code", 80)
        self.sessions.recent.reset_mock()
        for harness in ("Nope", ["OMP"]):
            with self.assertRaises(ValueError) as caught:
                self.call("term-history", harness=harness)
            self.assertEqual(str(caught.exception), "Choose a supported agent.")
        self.sessions.recent.assert_not_called()

    def test_list_reports_kind_and_harness(self):
        transcript = Path(self.folder) / "saved.jsonl"
        transcript.write_text("", encoding="utf8")
        other = tempfile.mkdtemp(prefix="awb-pt-")
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        pt.SESSIONS.update(
            shell=bare(project=self.folder, title="Shell", kind="embedded", ended=False),
            agent=bare(project=self.folder, title="OMP", kind="embedded", harness="OMP", ended=True),
            far=bare(project=self.folder, title="Box", kind="remote", harness="Pi", ended=False),
            watch=pt.Conversation(self.folder, (transcript, "Saved", "2026-11-22")),
            elsewhere=bare(project=str(Path(other).resolve()), title="Other", kind="embedded", ended=False),
        )
        self.assertEqual(
            self.call("term-list"),
            [
                dict(id="shell", title="Shell", ended=False, watch=False, kind="embedded", harness="", run=""),
                dict(id="agent", title="OMP", ended=True, watch=False, kind="embedded", harness="OMP", run=""),
                dict(id="far", title="Box", ended=False, watch=False, kind="remote", harness="Pi", run=""),
                dict(id="watch", title="Watch · Saved", ended=False, watch=True, kind="watch", harness="", run=""),
            ],
        )

    def test_watch_needs_a_saved_omp_session_in_the_project(self):
        transcript = Path(self.folder) / "saved.jsonl"
        transcript.write_text("", encoding="utf8")
        row = dict(harness="OMP", path=str(transcript), title="Saved", date="2026-11-22")
        for found in (None, row | {"harness": "Codex"}, row | {"path": ""}):
            self.sessions.find.return_value = found
            with self.assertRaises(ValueError) as caught:
                self.call("term-open", kind="watch", saved="b" * 64)
            self.assertEqual(str(caught.exception), "Choose a saved OMP session in this project.")
        self.assertEqual(pt.SESSIONS, {})
        self.sessions.find.return_value = row
        ident = self.call("term-open", kind="watch", saved="b" * 64)
        self.sessions.find.assert_called_with(self.folder, "b" * 64)
        self.assertIsInstance(pt.SESSIONS[ident], pt.Conversation)
        self.assertEqual((pt.SESSIONS[ident].path, pt.SESSIONS[ident].title), (transcript, "Watch · Saved"))
        self.assertEqual(self.call("term-open", kind="watch", saved="b" * 64), ident)
        self.assertEqual(len(pt.SESSIONS), 1)
        self.assertFalse(self.call("term-write", id=ident, text="x"))
        self.assertFalse(self.call("term-size", id=ident, rows=10, cols=40))
        self.assertTrue(self.call("term-close", id=ident))
        self.assertEqual(pt.SESSIONS, {})

    def test_admit_evicts_an_ended_terminal(self):
        closed = []
        for n in range(pt.CAP):
            pt.SESSIONS[str(n)] = types.SimpleNamespace(ended=n == 7, close=lambda n=n: closed.append(n))
        pt.admit()
        self.assertEqual(closed, [7])
        self.assertNotIn("7", pt.SESSIONS)
        self.assertEqual(len(pt.SESSIONS), pt.CAP - 1)
        pt.admit()
        self.assertEqual(closed, [7])
        pt.SESSIONS["again"] = types.SimpleNamespace(ended=False, close=lambda: closed.append("again"))
        with self.assertRaises(ValueError) as caught:
            pt.admit()
        self.assertEqual(
            str(caught.exception), "24 terminals are open across your projects; close one before opening another."
        )
        self.assertEqual(len(pt.SESSIONS), pt.CAP)

    def test_admit_makes_room_even_when_the_ended_terminal_fails_to_close(self):
        made = self.recorder()
        for n in range(pt.CAP):
            pt.SESSIONS[str(n)] = types.SimpleNamespace(ended=n == 3, project=self.folder, close=MagicMock())
        stale = pt.SESSIONS["3"]
        stale.close.side_effect = OSError("Could not terminate the child.")
        ident = self.call("term-open", kind="shell")
        stale.close.assert_called_once_with()
        self.assertNotIn("3", pt.SESSIONS)
        self.assertIn(ident, pt.SESSIONS)
        made.assert_called_once()

    def test_open_refuses_a_full_dock_before_spawning(self):
        made = self.recorder()
        for n in range(pt.CAP):
            pt.SESSIONS[str(n)] = types.SimpleNamespace(ended=False, project=self.folder)
        with self.assertRaises(ValueError) as caught:
            self.call("term-open", kind="shell")
        self.assertIn("24 terminals are open", str(caught.exception))
        made.assert_not_called()

    @unittest.skipUnless(POSIX, "POSIX shell selection")
    def test_shell_argv_is_a_login_shell_on_macos(self):
        made = self.recorder()
        for platform, argv in (("darwin", ["/bin/zsh", "-l"]), ("linux", ["/bin/zsh"])):
            with patch.dict(os.environ, {"SHELL": "/bin/zsh"}), patch.object(pt.sys, "platform", platform):
                ident = self.call("term-open", kind="shell")
            self.assertEqual(made.call_args.args[0], argv)
            self.assertEqual(made.call_args.args[2:], (self.folder, Path(self.folder).name + " · Shell", None))
            self.assertEqual(made.call_args.kwargs, {})
            self.assertIn(ident, pt.SESSIONS)
        with patch.dict(os.environ), patch.object(pt.sys, "platform", "linux"):
            os.environ.pop("SHELL", None)
            self.call("term-open", kind="shell")
        self.assertEqual(made.call_args.args[0], ["/bin/sh"])

    def test_resume_works_for_every_harness(self):
        made = self.agent()
        ident = self.call("term-open", kind="resume", saved="")
        self.sessions.find.assert_not_called()
        self.sessions.resume.assert_called_once_with("Codex", ["codex", "--model", "m"], {"KEY": "1"}, None)
        self.assertEqual(
            made.call_args.args,
            (["codex", "--model", "m", "--again"], {"KEY": "1", "AGAIN": "1"}, self.folder, "Codex · vendor/model", "Codex"),
        )
        self.assertIn(ident, pt.SESSIONS)
        row = dict(id="c" * 64, harness="Codex", session="s1", path="", store="account", home="/h")
        self.sessions.find.return_value = row
        self.call("term-open", kind="resume", saved="c" * 64)
        self.sessions.find.assert_called_once_with(self.folder, "c" * 64)
        self.assertEqual(self.sessions.resume.call_args.args[3], row)

    def test_resume_refuses_a_session_that_is_gone(self):
        made = self.agent()
        with self.assertRaises(ValueError) as caught:
            self.call("term-open", kind="resume", saved="d" * 64)
        self.assertEqual(
            str(caught.exception), "This session is no longer available in this project; pick another one."
        )
        self.sessions.resume.assert_not_called()
        made.assert_not_called()
        self.ready.assert_not_called()
        self.sessions.resume.side_effect = ValueError("This is a Codex session; choose Codex as the agent, then resume.")
        with self.assertRaises(ValueError) as caught:
            self.call("term-open", kind="resume")
        self.assertIn("This is a Codex session", str(caught.exception))
        made.assert_not_called()

    def test_agent_title_names_the_model_in_use(self):
        made = self.agent(harness="OMP", provider="Managed local", remote_model="stale/hosted", model="/models/qwen.gguf")
        self.call("term-open", kind="agent")
        self.assertEqual(made.call_args.args[3:], ("OMP · qwen.gguf", "OMP"))
        self.sessions.resume.assert_not_called()
        made = self.agent(harness="Claude Code", provider="Agent account", remote_model="")
        self.call("term-open", kind="agent")
        self.assertEqual(made.call_args.args[3:], ("Claude Code · Agent account", "Claude Code"))

    def test_remote_session_is_recorded(self):
        made = self.recorder()
        machines = self.machines(ssh=lambda host: ["ssh", "-o", "BatchMode=yes"])
        with patch.dict(sys.modules, {"machines": machines}):
            ident = self.call("term-remote", host=" me@box ", folder="~/proj", harness="OMP", mode="native")
            machines.remote_command.assert_called_once_with("me@box", "~/proj", "OMP", "native")
            name = made.call_args.kwargs["name"]
            self.assertRegex(name, r"\Aawb-[0-9a-f]{16}\Z")
            self.assertEqual(made.call_args.kwargs, dict(kind="remote", host="me@box", name=name))
            argv = made.call_args.args[0]
            self.assertEqual(argv[:5], ["ssh", "-o", "BatchMode=yes", "-tt", "me@box"])
            self.assertEqual(argv[5], f"tmux -L agent-workbench new-session -A -s {name} 'exec omp'")
            self.assertEqual(made.call_args.args[2:], (self.folder, "GPU box · OMP · ~/proj", "OMP"))
            self.sessions.remember_remote.assert_called_once_with(
                name, host="me@box", folder="~/proj", harness="OMP", mode="native", workspace=self.folder
            )
            self.assertIn(ident, pt.SESSIONS)
            self.call("term-remote", host="me@box", folder="~/proj", harness="OMP", mode="shell")
            self.assertEqual(made.call_args.args[3:], ("GPU box · Shell · ~/proj", None))
            self.assertEqual(self.sessions.remember_remote.call_args.kwargs["harness"], "")
            self.assertNotEqual(self.sessions.remember_remote.call_args.args[0], name)
            made.reset_mock()
            self.sessions.remember_remote.reset_mock()
            with self.assertRaises(ValueError) as caught:
                self.call("term-remote", host="stranger@box")
            self.assertEqual(str(caught.exception), "Save this SSH machine first.")
            made.assert_not_called()
            self.sessions.remember_remote.assert_not_called()

    def test_remote_host_is_trimmed_and_absent_fields_take_their_defaults(self):
        made = self.recorder()
        machines = self.machines()
        with patch.dict(sys.modules, {"machines": machines}):
            for payload in ({}, dict(host=None), dict(host="  ")):
                with self.subTest(payload=payload), self.assertRaises(ValueError) as caught:
                    self.call("term-remote", **payload)
                self.assertEqual(str(caught.exception), "Use an SSH config alias or user@hostname.")
            with self.assertRaises(ValueError) as caught:
                self.call("term-remote", host=5)
            self.assertEqual(str(caught.exception), "Save this SSH machine first.")
            made.assert_not_called()
            self.call("term-remote", host="\tme@box\n")
            machines.remote_command.assert_called_once_with("me@box", ".", "OMP", "native")
            self.assertEqual(made.call_args.args[3:], ("GPU box · OMP · .", "OMP"))
            self.call("term-remote", host="me@box", folder=None, harness=None, mode=None)
            self.assertEqual(machines.remote_command.call_args.args, ("me@box", ".", "OMP", "native"))
            self.assertEqual(made.call_args.kwargs["name"], self.sessions.remember_remote.call_args_list[0].args[0])
            self.call("term-remote", host="me@box", folder=7, harness=["OMP"], mode="shell")
            self.assertEqual(machines.remote_command.call_args.args, ("me@box", "7", "['OMP']", "shell"))

    def test_remote_terminal_opens_when_it_cannot_be_recorded(self):
        made = self.recorder()
        machines = self.machines()
        for error in (OSError("disk full"), TimeoutError("lock busy"), ValueError("bad registry")):
            self.sessions.remember_remote.side_effect = error
            with patch.dict(sys.modules, {"machines": machines}):
                ident = self.call("term-remote", host="me@box", folder="~/proj", harness="OMP", mode="native")
            self.assertIn(ident, pt.SESSIONS)
        self.assertEqual(made.call_count, 3)
        self.assertEqual(self.sessions.remember_remote.call_count, 3)

    def test_term_close_removes_the_session_when_close_fails(self):
        pt.SESSIONS["stuck"] = bare(project=self.folder)
        with patch.object(pt.Session, "close", side_effect=OSError("boom")), self.assertRaises(OSError):
            self.call("term-close", id="stuck")
        self.assertNotIn("stuck", pt.SESSIONS)

    def test_write_and_size_report_an_ended_terminal(self):
        proc = MagicMock()
        pt.SESSIONS["t"] = bare(project=self.folder, proc=proc)
        self.assertTrue(self.call("term-write", id="t", text="ls\r"))
        proc.write.assert_called_once_with("ls\r" if os.name == "nt" else b"ls\r")
        self.assertTrue(self.call("term-size", id="t", rows=30, cols=100))
        proc.setwinsize.assert_called_once_with(30, 100)
        for error in pt.GONE:
            proc.write.side_effect = error("gone")
            with self.assertRaises(ValueError) as caught:
                self.call("term-write", id="t", text="x")
            self.assertEqual(str(caught.exception), ENDED)
            proc.setwinsize.side_effect = error("gone")
            self.assertFalse(self.call("term-size", id="t", rows=30, cols=100))
        with self.assertRaises(ValueError) as caught:
            self.call("term-write", id="t", text="x" * 65537)
        self.assertEqual(str(caught.exception), "Paste up to 64 KiB at once.")
        if POSIX:
            proc.write.side_effect = None
            self.assertTrue(self.call("term-write", id="t", text="a\ud800b"))
            proc.write.assert_called_with(b"a?b")
        proc.write.side_effect = RuntimeError("unexpected")
        with self.assertRaises(RuntimeError):
            self.call("term-write", id="t", text="x")
        self.assertFalse(pt.SESSIONS["t"].writing.locked())

    def test_write_gives_up_on_a_program_that_is_not_reading(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        proc = MagicMock()
        proc.write.side_effect = lambda data: gate.wait(5)
        pt.SESSIONS["t"] = session = bare(project=self.folder, proc=proc)
        with patch.object(pt, "WRITE_WAIT", 0.05):
            for _ in range(2):
                outcome = timed(lambda: self.call("term-write", id="t", text="x"), 1)
                self.assertIn("seconds", outcome, "term-write did not return in time")
                self.assertLess(outcome["seconds"], 0.5)
                self.assertEqual(str(outcome.get("error")), WAITING)
            self.assertEqual(proc.write.call_count, 1)
            gate.set()
            self.assertTrue(self.wait(lambda: not session.writing.locked(), 1))
            self.assertTrue(self.call("term-write", id="t", text="y"))
        self.assertEqual(proc.write.call_count, 2)

    def test_write_to_a_closed_terminal_reports_the_end(self):
        proc = MagicMock()
        pt.SESSIONS["t"] = bare(project=self.folder, proc=proc, done=True)
        with self.assertRaises(ValueError) as caught:
            self.call("term-write", id="t", text="x")
        self.assertEqual(str(caught.exception), ENDED)
        proc.write.assert_not_called()

    def test_actions_stay_inside_their_project(self):
        other = str(Path(tempfile.mkdtemp(prefix="awb-pt-")).resolve())
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        pt.SESSIONS["t"] = bare(project=self.folder, proc=MagicMock())
        for action in ("term-read", "term-write", "term-size", "term-close"):
            with self.assertRaises(ValueError) as caught:
                pt.dispatch(action, dict(workspace=other, id="t", text="x", rows=1, cols=1))
            self.assertEqual(str(caught.exception), "Choose a terminal in this project.")
        self.assertIn("t", pt.SESSIONS)
        with self.assertRaises(ValueError) as caught:
            self.call("term-nothing", id="t")
        self.assertEqual(str(caught.exception), "Unknown terminal action.")

    def test_active_lists_running_terminals(self):
        proc = types.SimpleNamespace(pid=4242)
        pt.SESSIONS.update(
            one=bare(project=self.folder, proc=proc, title="OMP · m", kind="embedded", harness="OMP", ended=False,
                     started=5.0, host="", name=""),
            two=bare(project=self.folder, proc=proc, title="Box", kind="remote", ended=False, started=6.0,
                     host="me@box", name="awb-0123456789abcdef"),
            done=bare(project=self.folder, proc=proc, title="Old", kind="embedded", harness="Pi", started=1.0,
                      host="", name=""),
            watch=types.SimpleNamespace(project=self.folder, ended=False, title="Watch"),
        )
        self.assertEqual(
            pt.active(),
            [
                dict(id="one", kind="embedded", harness="OMP", title="OMP · m", workspace=self.folder, pid=4242,
                     started=5.0, host="", name=""),
                dict(id="two", kind="remote", harness="", title="Box", workspace=self.folder, pid=4242, started=6.0,
                     host="me@box", name="awb-0123456789abcdef"),
            ],
        )
        self.assertEqual(pt.active_agents(), [dict(pid=4242, workspace=self.folder, harness="OMP")])

    def test_batch_launchers_with_unsafe_arguments_are_refused_before_spawning(self):
        with (
            patch.object(pt, "safe_argv", side_effect=ValueError("x.cmd is a Windows batch launcher")),
            patch.object(pt.Pty, "spawn") as spawn,
            self.assertRaises(ValueError),
        ):
            pt.Session(["x.cmd", "a&calc"], {}, self.folder, "Test")
        spawn.assert_not_called()

    def test_session_applies_the_real_batch_launcher_check(self):
        import host

        with patch.object(host, "WINDOWS", True), patch.object(pt.Pty, "spawn") as spawn, patch.object(pt.Session, "collect"):
            with self.assertRaises(ValueError) as caught:
                pt.Session(["x.cmd", "--model", "a&calc"], {}, self.folder, "Test")
            self.assertIn("x.cmd is a Windows batch launcher", str(caught.exception))
            spawn.assert_not_called()
            pt.Session(["x.cmd", "--model", "vendor/model:free"], {}, self.folder, "Test").reader.join(1)
        spawn.assert_called_once()
        self.assertEqual(spawn.call_args.args[0], ["x.cmd", "--model", "vendor/model:free"])

    def test_spawn_receives_the_checked_argv_and_a_clean_environment(self):
        self.assertIn("WORKBENCH_CALLER_CWD", pt.STRIP)
        given = {"TERM": "dumb", "KEEP": "1"} | {name: "host" for name in pt.STRIP}
        with patch.object(pt.Pty, "spawn") as spawn, patch.object(pt.Session, "collect"):
            session = pt.Session(["agent", "--model", "m"], given, self.folder, "Test", "OMP")
            session.reader.join(1)
        spawn.assert_called_once_with(
            ["agent", "--model", "m"], cwd=self.folder, env={"TERM": "xterm-256color", "KEEP": "1"}, dimensions=(20, 96)
        )
        self.assertEqual(given["TERM"], "dumb")
        self.assertEqual((session.kind, session.host, session.name, session.harness), ("embedded", "", "", "OMP"))

    @unittest.skipUnless(POSIX, "symlinked folder")
    def test_unresolved_folder_matches_the_resolved_workspace(self):
        link = Path(self.folder) / "link"
        target = Path(self.folder) / "real"
        target.mkdir()
        link.symlink_to(target, target_is_directory=True)
        with patch.object(pt.Pty, "spawn") as spawn, patch.object(pt.Session, "collect"):
            pt.SESSIONS["s"] = session = pt.Session(["agent"], {}, str(link), "Test")
        self.assertEqual(session.project, str(target))
        self.assertEqual(spawn.call_args.kwargs["cwd"], str(target))
        for workspace in (str(link), str(target)):
            self.assertEqual(pt.dispatch("term-read", dict(workspace=workspace, id="s"))["text"], "")
        self.assertEqual([row["id"] for row in pt.dispatch("term-list", dict(workspace=str(link)))], ["s"])


class CloseLogicTests(Case):
    def stuck(self, **fields):
        reader = MagicMock()
        reader.is_alive.return_value = True
        return bare(proc=MagicMock(), reader=reader, **fields)

    @unittest.skipUnless(POSIX, "POSIX close order")
    def test_close_signals_the_child_before_closing_the_descriptor(self):
        session = self.stuck()
        session.reader.is_alive.return_value = False
        order = MagicMock()
        order.attach_mock(session.proc.terminate, "terminate")
        order.attach_mock(session.reader.join, "join")
        order.attach_mock(session.proc.close, "close")
        session.proc.terminate.return_value = True
        session.close()
        self.assertEqual([c[0] for c in order.mock_calls], ["terminate", "join", "close"])
        session.proc.terminate.assert_called_once_with(force=True)
        session.reader.join.assert_called_once_with(1.5)
        session.proc.close.assert_called_once_with(force=True)
        self.assertEqual(session.proc.delayafterclose, 0)
        self.assertTrue(session.stop.is_set())
        session.close()
        self.assertEqual(len(order.mock_calls), 3)

    @unittest.skipUnless(POSIX, "POSIX close order")
    def test_close_leaves_a_descriptor_the_reader_still_holds_only_on_macos(self):
        for platform, closed in (("darwin", 0), ("linux", 1)):
            session = self.stuck()
            with patch.object(pt.sys, "platform", platform):
                session.close()
            self.assertEqual(session.proc.close.call_count, closed)
            session.proc.terminate.assert_called_once_with(force=True)

    @unittest.skipUnless(POSIX, "POSIX close order")
    def test_close_survives_a_child_another_thread_already_reaped(self):
        for error in pt.GONE:
            session = self.stuck()
            session.reader.is_alive.return_value = False
            session.proc.terminate.side_effect = error("reaped")
            session.proc.close.side_effect = error("reaped")
            session.close()
            session.reader.join.assert_called_once_with(1.5)
            session.proc.close.assert_called_once_with(force=True)

    @unittest.skipUnless(POSIX, "POSIX signals")
    def test_hangup_signals_a_live_child_and_stops_the_reader(self):
        session = self.stuck()
        session.hangup()
        session.proc.kill.assert_called_once_with(signal.SIGHUP)
        self.assertTrue(session.stop.is_set())
        session.proc.close.assert_not_called()
        session = self.stuck()
        session.proc.isalive.return_value = False
        session.hangup()
        session.proc.kill.assert_not_called()
        for error in pt.GONE:
            session = self.stuck()
            session.proc.isalive.side_effect = error("reaped")
            session.hangup()
            self.assertTrue(session.stop.is_set())

    def test_windows_close_is_a_single_forced_close(self):
        session = self.stuck()
        with patch.object(pt, "os", WindowsOS()):
            session.hangup()
            self.assertTrue(session.stop.is_set())
            self.assertEqual(session.proc.mock_calls, [])
            session.close()
            session.close()
        session.proc.close.assert_called_once_with(force=True)
        session.proc.terminate.assert_not_called()
        session.reader.join.assert_not_called()

    def test_windows_reader_and_writer_use_text(self):
        proc = MagicMock()
        proc.read.side_effect = ["dir\r\n", "", "done", EOFError("Pty is closed")]
        proc.isalive.return_value = True
        session = bare(proc=proc, project=self.folder, ended=False)
        pt.SESSIONS["w"] = session
        with patch.object(pt, "os", WindowsOS()), patch.object(pt.time, "sleep"):
            session.collect()
            self.assertTrue(self.call("term-write", id="w", text="dir\r"))
        self.assertEqual(session.snapshot(0), dict(text="dir\r\ndone", offset=9, ended=True, reset=False))
        proc.write.assert_called_once_with("dir\r")

    def test_windows_shell_is_powershell_without_login_flag(self):
        made = MagicMock(side_effect=lambda *a, **k: types.SimpleNamespace(project=self.folder, ended=False))
        for found, exe in ((None, "powershell.exe"), ("C:/Program Files/PowerShell/7/pwsh.exe",) * 2):
            with (
                patch.object(pt, "Session", made),
                patch.object(pt, "os", WindowsOS()),
                patch.object(pt.sys, "platform", "win32"),
                patch.object(pt.shutil, "which", return_value=found),
            ):
                self.call("term-open", kind="shell")
            self.assertEqual(made.call_args.args[0], [exe])

    def test_reader_stops_when_asked_and_keeps_partial_characters(self):
        proc = MagicMock()
        proc.fd = 7
        session = bare(proc=proc, ended=False)
        chunks = ["caf\xc3".encode("latin1"), "\xa9!".encode("latin1")]

        def read(fd, size):
            if not chunks:
                session.stop.set()
                return b""
            return chunks.pop(0)

        with patch.object(pt, "os", types.SimpleNamespace(name="posix", read=read)):
            session.collect()
        self.assertEqual(session.snapshot(0)["text"], "café!")
        self.assertTrue(session.ended)
        proc.isalive.assert_not_called()


@unittest.skipUnless(POSIX, "POSIX PTY")
class PtyTests(Case):
    def test_close_idle_shell_returns(self):
        session = self.spawn(["/bin/sh"])
        self.idle(session)
        self.assertTrue(running(session.proc.pid))
        self.assert_closed(session, timed(session.close, 2))

    def test_term_close_ends_an_idle_shell_within_two_seconds(self):
        with patch.dict(os.environ, {"SHELL": "/bin/sh", "HOME": self.folder}):
            ident = self.call("term-open", kind="shell")
        session = pt.SESSIONS[ident]
        self.addCleanup(reap, session)
        self.idle(session)
        self.assertEqual(
            self.call("term-list"),
            [dict(id=ident, title=Path(self.folder).name + " · Shell", ended=False, watch=False, kind="embedded", harness="", run="")],
        )
        self.assertEqual(
            pt.active(),
            [dict(id=ident, kind="embedded", harness="", title=Path(self.folder).name + " · Shell", workspace=self.folder,
                  pid=session.proc.pid, started=session.started, host="", name="")],
        )
        self.assertTrue(self.call("term-size", id=ident, rows=30, cols=100))
        self.assertTrue(self.call("term-write", id=ident, text="stty size\r"))
        self.assertTrue(self.wait(lambda: "30 100" in self.call("term-read", id=ident, offset=0)["text"]))
        outcome = timed(lambda: self.call("term-close", id=ident), 2)
        self.assert_closed(session, outcome)
        self.assertTrue(outcome["value"])
        self.assertEqual(self.call("term-list"), [])
        self.assertEqual(pt.active(), [])

    def test_second_close_is_a_no_op(self):
        pt.SESSIONS["s"] = session = self.spawn(["/bin/sh"])
        self.idle(session)
        self.assert_closed(session, timed(session.close, 2))
        again = timed(session.close, 0.5)
        self.assertIn("seconds", again)
        self.assertNotIn("error", again)
        with self.assertRaises(ValueError) as caught:
            self.call("term-write", id="s", text="x")
        self.assertEqual(str(caught.exception), ENDED)
        self.assertFalse(self.call("term-size", id="s", rows=30, cols=100))
        self.assertTrue(self.call("term-read", id="s", offset=0)["ended"])
        self.assertEqual(pt.active(), [])

    @unittest.skipUnless(sys.platform == "darwin", "macOS blocks a write once the terminal input queue is full")
    def test_term_write_returns_when_the_program_is_not_reading(self):
        pt.SESSIONS["s"] = session = self.spawn(DEAF)
        self.assertTrue(self.idle(session, "READY"))
        with patch.object(pt, "WRITE_WAIT", 0.03):
            outcome = timed(lambda: self.call("term-write", id="s", text="echo typed ahead\n" * 300), 2)
            self.assertIn("seconds", outcome, "term-write did not return in time")
            self.assertEqual(str(outcome.get("error")), WAITING)
            again = timed(lambda: self.call("term-write", id="s", text="\x03"), 2)
            self.assertEqual(str(again.get("error")), WAITING)
        self.assert_closed(session, timed(lambda: self.call("term-close", id="s"), 2))
        self.assertTrue(self.wait(lambda: not session.writing.locked(), 1))

    def test_close_ends_a_child_that_ignores_hangup_and_interrupt(self):
        session = self.spawn(STUBBORN)
        self.assertTrue(self.idle(session, "READY"))
        self.assert_closed(session, timed(session.close, 2))

    def test_close_returns_while_a_background_job_holds_the_terminal(self):
        session = self.spawn(["/bin/sh"])
        self.idle(session)
        session.proc.write(b"sleep 2 & echo started\r")
        self.assertTrue(self.wait(lambda: session.snapshot(0)["text"].count("started") > 1))
        outcome = timed(session.close, 3)
        self.assertIn("seconds", outcome, "close did not return in time")
        self.assertNotIn("error", outcome)
        self.assertFalse(running(session.proc.pid))
        if sys.platform == "darwin":
            self.assert_closed(session, outcome)

    def test_close_sessions_ends_every_child(self):
        transcript = Path(self.folder) / "saved.jsonl"
        transcript.write_text("", encoding="utf8")
        shells = [self.spawn(["/bin/sh"], patient=True) for _ in range(4)]
        watch = pt.Conversation(self.folder, (transcript, "Saved", "2026-11-22"))
        broken = types.SimpleNamespace(close=MagicMock(side_effect=RuntimeError("boom")))
        pt.SESSIONS.update({str(n): shell for n, shell in enumerate(shells[:2])}, broken=broken, watch=watch)
        pt.SESSIONS.update({str(n): shell for n, shell in enumerate(shells) if n > 1})
        self.idle(shells[-1])
        outcome = timed(pt.close_sessions, 4)
        self.assertEqual(pt.SESSIONS, {})
        for shell in shells:
            self.assert_closed(shell, outcome)
        self.assertTrue(watch.ended)
        broken.close.assert_called_once_with()
        again = timed(pt.close_sessions, 0.5)
        self.assertIn("seconds", again)
        self.assertNotIn("error", again)

    def test_reader_ends_after_child_exit(self):
        session = self.spawn(["/bin/sh", "-c", "echo one line"])
        self.assertTrue(self.wait(lambda: session.ended))
        self.assertIn("one line", session.snapshot(0)["text"])
        session.reader.join(1)
        self.assertFalse(session.reader.is_alive())
        self.assert_closed(session, timed(session.close, 2))

    def test_reader_does_not_spin_when_the_child_lets_go_of_the_terminal(self):
        session = self.spawn(DETACHED)
        self.assertTrue(self.idle(session, "READY"))
        polls, real = [], session.proc.isalive

        def counted():
            polls.append(1)
            return real()

        session.proc.isalive = counted
        time.sleep(0.1)
        self.assertLess(len(polls), 10)
        session.proc.isalive = real
        outcome = timed(session.close, 2)
        self.assertIn("seconds", outcome, "close did not return in time")
        self.assertFalse(running(session.proc.pid))
        session.reader.join(1)
        self.assertFalse(session.reader.is_alive())

    def test_session_environment_drops_host_terminal_variables(self):
        given = dict(
            os.environ, TERM="dumb", TERM_PROGRAM="Apple_Terminal", TERM_SESSION_ID="w0t0p0",
            WORKBENCH_CALLER_CWD="/where/workbench/was/started",
        )
        session = self.spawn(SHOW_ENV, env=given)
        self.assertTrue(self.wait(lambda: session.ended))
        self.assertIn("T=xterm-256color;P=;C=;", session.snapshot(0)["text"])
        self.assertEqual(given["TERM_PROGRAM"], "Apple_Terminal")
        self.assertEqual(given["WORKBENCH_CALLER_CWD"], "/where/workbench/was/started")


if __name__ == "__main__":
    unittest.main()
