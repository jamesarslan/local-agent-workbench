"""sessions.py: history readers per harness, resume commands, the remote registry and the running list."""

import atexit
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import MagicMock, patch

if "runtime" not in sys.modules and "WORKBENCH_DATA" not in os.environ:
    os.environ["WORKBENCH_DATA"] = tempfile.mkdtemp(prefix="awb-test-")
    atexit.register(shutil.rmtree, os.environ["WORKBENCH_DATA"], ignore_errors=True)

import zstandard

import sessions

HARNESSES = sessions.harnesses
READABLE = ["OMP", "Pi", "Claude Code", "Codex", "opencode"]
HOMES = ("PI_CODING_AGENT_DIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_DATA_HOME")
POSIX = os.name != "nt"
UUID = "{0}{0}{0}{0}{0}{0}{0}{0}-{0}{0}{0}{0}-4{0}{0}{0}-8{0}{0}{0}-{0}{0}{0}{0}{0}{0}{0}{0}{0}{0}{0}{0}"
ONE, TWO, THREE, FOUR, FIVE, SIX, SEVEN = (UUID.format(n) for n in "1234567")
TMUX = "awb-0123456789abcdef"
HOST = "me@gpu-box"
TABLE = (
    "create table session (id text primary key, parent_id text, directory text, title text,"
    " time_created integer, time_updated integer, time_archived integer)"
)


def typed(*args):
    return shlex.join(args) if POSIX else sessions.windows_line(args)


def jsonl(path, *rows, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf8")
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


def done(code=0, stdout=""):
    return types.SimpleNamespace(returncode=code, stdout=stdout, stderr="")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="awb-ses-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home, self.data, self.proj, self.other = (
            self.tmp / name for name in ("home", "data", "proj", "other")
        )
        for folder in (self.home, self.data, self.proj, self.other):
            folder.mkdir()
        self.project = str(self.proj)
        self.encoded = re.sub(r"[^A-Za-z0-9]", "-", self.project)
        plain = {k: v for k, v in os.environ.items() if k not in HOMES}
        for item in (
            patch.object(Path, "home", return_value=self.home),
            patch.object(sessions, "DATA", self.data),
            patch.dict(os.environ, plain, clear=True),
            patch.object(sessions, "harnesses", return_value=list(READABLE)),
        ):
            item.start()
            self.addCleanup(item.stop)

    def rows(self, harness=None):
        return sessions.recent(self.project, harness, None)

    def omp(self, name, *rows, profile=None, mtime=None):
        home = f".omp/profiles/{profile}/agent" if profile else ".omp/agent"
        return jsonl(self.home / home / "sessions/x" / name, *rows, mtime=mtime)

    def claude(self, ident, *rows, slot=None, mtime=None):
        root = self.data / "clients" / slot / "claude" if slot else self.home / ".claude"
        path = root / "projects" / self.encoded / (ident + ".jsonl")
        return jsonl(path, *rows, mtime=mtime)

    def rollout(self, ident, day="22", slot=None, suffix=".jsonl"):
        root = self.data / "clients" / slot / "codex" if slot else self.home / ".codex"
        name = f"rollout-2026-11-{day}T09-00-00-{ident}{suffix}"
        path = root / "sessions/2026/11" / day / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def meta(self, ident, cwd=None, **more):
        payload = dict(id=ident, cwd=cwd or self.project, **more)
        return dict(
            timestamp="2026-11-22T09:00:00.000Z", type="session_meta", payload=payload
        )

    def database(self, folder=None):
        path = (folder or self.home / ".local/share") / "opencode/opencode.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def machines(self, **more):
        fields = dict(
            target=lambda value: value,
            labels=lambda: {HOST: "GPU box"},
            ssh=lambda host: ["ssh", "-o", "BatchMode=yes"],
            windows=lambda host: False,
            NO_SSH="OpenSSH was not found on this computer; install the OpenSSH client, then retry.",
        )
        return types.SimpleNamespace(**fields | more)

    def standins(self, **modules):
        quiet = types.SimpleNamespace(active_sessions=lambda: [], recent_exits=lambda: [])
        base = dict(
            session_window=quiet,
            machines=self.machines(labels=lambda: {}),
            project_terminal=types.SimpleNamespace(active=lambda: []),
        )
        return patch.dict(sys.modules, base | modules)


class RecentTests(Fixture):
    def test_omp_headers_and_folder_filter(self):
        header = dict(type="session", cwd=self.project, timestamp="2026-11-20T10:00:00.000Z")
        mine = self.omp("a.jsonl", dict(type="title", title="Fix login"), header | {"id": "omp-1"})
        self.omp("b.jsonl", header | dict(id="omp-2", cwd=str(self.other)), profile="workbench-abc")
        bare = self.omp("c.jsonl", header, profile="workbench-abc")
        self.omp("bad.jsonl", [1, 2], profile="workbench-abc")
        self.omp("deep.jsonl", profile="workbench-abc").write_text("[" * 200000)
        self.omp("p.jsonl", header | {"id": "omp-3"}, profile="personal")
        rows = {r["path"]: r for r in self.rows("OMP")}
        self.assertEqual(set(rows), {str(mine), str(bare)})
        row = rows[str(mine)]
        self.assertEqual(
            (row["store"], row["session"], row["title"], row["date"], row["harness"]),
            ("account", "omp-1", "Fix login", "2026-11-20T10:00:00.000Z", "OMP"),
        )
        self.assertEqual(row["home"], str(self.home / ".omp/agent"))
        self.assertEqual(row["command"], typed("omp", "--resume", str(mine)))
        self.assertEqual(row["id"], hashlib.sha256(str(mine).encode()).hexdigest())
        self.assertRegex(row["updated"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.000Z$")
        row = rows[str(bare)]
        self.assertEqual(
            (row["store"], row["session"], row["title"], row["command"]),
            ("workbench", "", "OMP session", ""),
        )

    def test_titles_are_sanitised(self):
        title = "Fix\x1b[31m login\nnow \u202e" + "x" * 300
        header = dict(type="session", id="omp-1", cwd=self.project, timestamp="t")
        self.omp("a.jsonl", dict(type="title", title=title), header)
        shown = self.rows("OMP")[0]["title"]
        self.assertTrue(shown.startswith("Fix [31m login now x"))
        self.assertTrue(all(c.isprintable() for c in shown))
        self.assertLessEqual(len(shown), 130)

    def test_claude_cwd_confirms_the_folder(self):
        asked = dict(type="user", message=dict(role="user", content="Add   tests\nplease"))
        mine = self.claude(
            ONE,
            dict(type="mode", sessionId="x"),
            dict(
                type="user",
                isMeta=True,
                message=dict(role="user", content="meta"),
                cwd=self.project,
                timestamp="2026-11-21T10:00:00.000Z",
            ),
            dict(type="user", message=dict(role="user", content="<command-name>x")),
            asked | {"cwd": self.project},
            dict(type="user", message=dict(role="user", content="second question")),
        )
        self.claude(TWO, asked | {"cwd": str(self.other)})
        self.claude(THREE, asked)
        self.claude(
            FOUR,
            asked | {"cwd": self.project},
            dict(type="custom-title", customTitle="Named run"),
            slot="aaaaaaaaaaaa",
        )
        rows = {r["session"]: r for r in self.rows("Claude Code")}
        self.assertEqual(set(rows), {ONE, FOUR})
        row = rows[ONE]
        self.assertEqual(
            (row["title"], row["store"], row["date"], row["path"]),
            ("Add tests please", "account", "2026-11-21T10:00:00.000Z", str(mine)),
        )
        self.assertEqual(row["command"], typed("claude", "--resume", ONE))
        row = rows[FOUR]
        self.assertEqual((row["title"], row["store"], row["command"]), ("Named run", "workbench", ""))
        self.assertEqual(row["home"], str(self.data / "clients/aaaaaaaaaaaa/claude"))

    def test_codex_plain_zst_and_legacy(self):
        pack = zstandard.ZstdCompressor()
        jsonl(self.rollout(ONE, slot="bbbbbbbbbbbb"), self.meta(ONE))
        long = self.meta(TWO, base_instructions=dict(text="x" * (sessions.HEAD + 100000)))
        cut = self.rollout(TWO, "21", suffix=".jsonl.zst")
        cut.write_bytes(pack.compress((json.dumps(long) + "\n").encode()))
        split = dict(type="session_meta", payload=dict(pad="p" * 1500, cwd=self.project))
        late = json.dumps(split).encode() + b"\n"
        frames = pack.compress(late[:700]) + pack.compress(late[700:])
        self.rollout(THREE, "20", suffix=".jsonl.zst").write_bytes(frames)
        legacy = dict(id="old", timestamp="x", instructions=None)
        jsonl(self.rollout(FOUR, "19"), legacy)
        self.rollout(FIVE, "18", suffix=".jsonl.zst").write_bytes(b"not a zstd frame")
        jsonl(self.rollout(SIX, "17"), [dict(cwd=self.project)])
        jsonl(self.rollout(SEVEN, "16").with_name("notes.jsonl"), self.meta(SEVEN))
        rows = {r["session"]: r for r in self.rows("Codex")}
        self.assertEqual(set(rows), {ONE, TWO, THREE})
        self.assertEqual(
            [(rows[k]["store"], rows[k]["title"], rows[k]["date"]) for k in (ONE, TWO)],
            [
                ("workbench", "Codex session", "2026-11-22T09:00:00.000Z"),
                ("account", "Codex session", "2026-11-22T09:00:00.000Z"),
            ],
        )
        self.assertEqual(rows[TWO]["path"], str(cut))
        self.assertEqual(rows[TWO]["command"], typed("codex", "resume", TWO))
        self.assertEqual(rows[ONE]["command"], "")

    def test_reader_stops_at_head(self):
        first = dict(type="user", message=dict(role="user", content="Big one"), cwd=self.project)
        filler = dict(type="assistant", message=dict(role="assistant", content="y" * 4000))
        path = self.claude(ONE, first, *[filler] * 520)
        self.assertGreater(path.stat().st_size, 2 * 1024 * 1024)
        sizes, real = [], sessions.head

        def recorder(*args, **kwargs):
            data = real(*args, **kwargs)
            sizes.append(len(data))
            return data

        with patch.object(sessions, "head", side_effect=recorder):
            rows = self.rows("Claude Code")
        self.assertEqual([r["title"] for r in rows], ["Big one"])
        self.assertEqual(sizes, [sessions.HEAD])
        packed = self.tmp / "big.jsonl.zst"
        packed.write_bytes(zstandard.ZstdCompressor().compress(b"z" * (4 * sessions.HEAD)))
        self.assertEqual(len(sessions.head(packed)), sessions.HEAD)
        self.assertEqual(len(sessions.head(path, 10)), 10)

    def test_opencode_rows(self):
        path = self.database()
        con = sqlite3.connect(path)
        con.execute(TABLE)
        for row in (
            ("ses_1", None, self.project, "Open\x07 one", 1795000000000, 1795000100000, None),
            ("ses_2", "ses_1", self.project, "child", 1795000000000, 1795000200000, None),
            ("ses_3", None, str(self.other), "elsewhere", 1795000000000, 1795000300000, None),
            ("ses_4", None, self.project, "archived", 1795000000000, 1795000400000, 1795000500000),
            ("-x", None, self.project, "option", 1795000000000, 1795000600000, None),
        ):
            con.execute("insert into session values (?, ?, ?, ?, ?, ?, ?)", row)
        con.commit()
        con.close()
        rows = self.rows("opencode")
        self.assertEqual([r["session"] for r in rows], ["ses_1"])
        row = rows[0]
        self.assertEqual(
            (row["title"], row["path"], row["store"], row["date"], row["updated"]),
            ("Open one", "", "account", sessions.stamp(1795000000), sessions.stamp(1795000100)),
        )
        self.assertEqual(row["command"], typed("opencode", "--session", "ses_1"))
        self.assertEqual(row["id"], hashlib.sha256(b"opencode:ses_1").hexdigest())
        odd = self.tmp / "x#y z%41"
        shutil.copytree(self.home / ".local/share", odd)
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(odd)}):
            self.assertEqual([r["session"] for r in self.rows("opencode")], ["ses_1"])
        path.unlink()
        con = sqlite3.connect(path)
        con.execute("create table other (id text)")
        con.commit()
        con.close()
        self.assertEqual(self.rows("opencode"), [])
        path.write_bytes(b"not a database at all")
        self.assertEqual(self.rows("opencode"), [])

    def test_recent_orders_and_limits(self):
        header = dict(type="session", id="omp-1", cwd=self.project, timestamp="t")
        old = self.omp("a.jsonl", header, mtime=1795000100)
        text = dict(type="user", message=dict(role="user", content="Hello"), cwd=self.project)
        self.claude(ONE, text, mtime=1795000300)
        os.utime(jsonl(self.rollout(TWO), self.meta(TWO)), (1795000200, 1795000200))
        rows = self.rows()
        self.assertEqual([r["harness"] for r in rows], ["Claude Code", "Codex", "OMP"])
        self.assertEqual([r["updated"] for r in rows], sorted((r["updated"] for r in rows), reverse=True))
        self.assertEqual(rows[2]["updated"], sessions.stamp(1795000100))
        self.assertEqual(len(sessions.recent(self.project, None, 2)), 2)
        self.assertEqual(len(sessions.recent(self.project)), 3)
        self.assertEqual([r["harness"] for r in self.rows("Codex")], ["Codex"])
        self.assertEqual(self.rows("Hermes"), [])
        for row in rows:
            self.assertRegex(row["id"], r"^[0-9a-f]{64}$")
        self.assertEqual([r["id"] for r in rows], [r["id"] for r in self.rows()])
        self.assertEqual(rows[2]["id"], hashlib.sha256(str(old).encode()).hexdigest())
        self.assertEqual(len({r["id"] for r in rows}), 3)

    def test_find_requires_a_row_id(self):
        header = dict(type="session", id="omp-1", cwd=self.project, timestamp="t")
        self.omp("a.jsonl", header)
        row = self.rows()[0]
        self.assertEqual(sessions.find(self.project, row["id"]), row)
        for ident in ("../x", None, "", row["id"].upper(), "0" * 64, row["id"] + "0", 7):
            self.assertIsNone(sessions.find(self.project, ident))

    def test_session_ids_cannot_become_options(self):
        text = dict(type="user", message=dict(role="user", content="Hello"), cwd=self.project)
        self.claude("--dangerously-skip-permissions", text)
        self.claude("bad id", text)
        self.assertEqual(self.rows("Claude Code"), [])
        for harness, row in (
            ("Claude Code", dict(session="--continue")),
            ("Codex", dict(session="a b")),
            ("opencode", dict(session="")),
            ("OMP", dict(path="--config=/tmp/x")),
            ("Pi", dict(path=None)),
        ):
            with self.assertRaisesRegex(ValueError, "no longer available"):
                sessions.resume_args(harness, row)

    def test_dates_are_sanitised(self):
        odd = "2026-11-20\x1b]0;x\x07T10:00\n" + "9" * 5000
        self.omp("a.jsonl", dict(type="session", id="omp-1", cwd=self.project, timestamp=odd))
        asked = dict(type="user", message=dict(role="user", content="Hello"), cwd=self.project)
        self.claude(ONE, asked | {"timestamp": odd})
        jsonl(self.rollout(TWO), self.meta(TWO) | {"timestamp": odd})
        long = self.meta(THREE, base_instructions=dict(text="x" * (sessions.HEAD + 1000)))
        jsonl(self.rollout(THREE, "21"), long | {"timestamp": odd})
        rows = self.rows()
        self.assertEqual(sorted(r["harness"] for r in rows), ["Claude Code", "Codex", "Codex", "OMP"])
        for row in rows:
            self.assertTrue(row["date"].startswith("2026-11-20 ]0;x T10:00 9"), row["date"])
            self.assertTrue(all(c.isprintable() for c in row["date"]))
            self.assertLessEqual(len(row["date"]), 40)

    def test_claude_folder_name_ignores_case(self):
        asked = dict(type="user", message=dict(role="user", content="Hello"), cwd=self.project)
        mine = self.claude(ONE, asked)
        root = self.home / ".claude/projects"
        self.assertEqual(list(sessions.claude_folders(root, self.project.swapcase())), [mine])
        self.assertEqual(list(sessions.claude_folders(root, self.project + "x")), [])
        other = self.project.swapcase()
        if other != self.project and os.path.isdir(other):
            self.assertEqual(
                [r["session"] for r in sessions.recent(other, "Claude Code", None)], [ONE]
            )

    def test_windows_command_is_safe_to_paste(self):
        line = sessions.windows_line
        self.assertEqual(line(["claude", "--resume", ONE]), "claude --resume " + ONE)
        self.assertEqual(line(["opencode", "--session", "ses_1:a.b"]), "opencode --session ses_1:a.b")
        self.assertEqual(
            line(["omp", "--resume", "C:\\Users\\me\\.omp\\a_b-1.jsonl"]),
            "omp --resume C:\\Users\\me\\.omp\\a_b-1.jsonl",
        )
        for path in (
            "C:\\Users\\a&calc\\x.jsonl",
            "C:\\Users\\Ann Lee\\x.jsonl",
            "C:\\Users\\J\u00f6rg\\x|y<z>(1);'2'.jsonl",
        ):
            self.assertEqual(line(["omp", "--resume", path]), 'omp --resume "' + path + '"')
        for path in (
            "C:\\a\\%PATH%\\x.jsonl",
            "C:\\a\\$(calc)\\x.jsonl",
            "C:\\a\\`calc`\\x.jsonl",
            'C:\\a\\"x.jsonl',
            "C:\\a\\\u201cx.jsonl",
            "C:\\a\\!x!.jsonl",
            "C:\\a\\x^.jsonl",
            "C:\\a\\x\ny.jsonl",
            "C:\\a b\\",
        ):
            self.assertEqual(line(["omp", "--resume", path]), "", path)
        header = dict(type="session", id="omp-1", cwd=self.project, timestamp="t")
        plain = self.omp("a.jsonl", header)
        quoted = jsonl(self.home / ".omp/agent/sessions/a&calc b/b.jsonl", header)
        refused = jsonl(self.home / ".omp/agent/sessions/%PATH%/c.jsonl", header)
        with patch.object(sessions, "WINDOWS", True):
            rows = {r["path"]: r["command"] for r in self.rows("OMP")}
        self.assertEqual(
            rows,
            {
                str(plain): "omp --resume " + str(plain),
                str(quoted): 'omp --resume "' + str(quoted) + '"',
                str(refused): "",
            },
        )

    @unittest.skipUnless(POSIX, "FIFOs and unprivileged symlinks are POSIX")
    def test_readers_skip_fifos_and_links_out_of_the_store(self):
        header = dict(type="session", id="omp-1", cwd=self.project, timestamp="t")
        asked = dict(type="user", message=dict(role="user", content="Hello"), cwd=self.project)
        outside = jsonl(self.tmp / "outside.jsonl", header | asked)
        kept = self.omp("a.jsonl", header)
        os.mkfifo(kept.with_name("fifo.jsonl"))
        kept.with_name("link.jsonl").symlink_to(outside)
        (kept.parent.parent / "away").symlink_to(self.tmp, target_is_directory=True)
        named = self.claude(ONE, asked)
        os.mkfifo(named.with_name(TWO + ".jsonl"))
        named.with_name(THREE + ".jsonl").symlink_to(outside)
        rolled = jsonl(self.rollout(FOUR), self.meta(FOUR))
        os.mkfifo(self.rollout(FIVE))
        self.rollout(SIX).symlink_to(jsonl(self.tmp / "meta.jsonl", self.meta(SIX)))
        found = []
        worker = threading.Thread(target=lambda: found.extend(self.rows()), daemon=True)
        worker.start()
        worker.join(5)
        self.assertFalse(worker.is_alive(), "a reader is blocked on a FIFO")
        self.assertEqual(sorted(r["path"] for r in found), sorted(map(str, (kept, named, rolled))))

    def test_account_home_ignores_a_relocated_home(self):
        slot = self.data / "clients/s/claude"
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(slot)}):
            self.assertEqual(sessions.stores("Claude Code")[0][0], self.home / ".claude")
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.other)}):
            self.assertEqual(
                sessions.stores("Claude Code")[0],
                (self.other, self.other / "projects", "account"),
            )
        slot.mkdir(parents=True)
        self.assertEqual(
            sessions.stores("Codex"),
            [
                (self.home / ".codex", self.home / ".codex/sessions", "account"),
                (slot.parent / "codex", slot.parent / "codex/sessions", "workbench"),
            ],
        )
        self.assertEqual(sessions.stores("Pi")[1][1], slot.parent / "pi/sessions")
        self.assertEqual(sessions.stores("Hermes"), [])


class ResumeTests(Fixture):
    def row(self, harness, store="account", slot="a", **more):
        part = "claude" if harness == "Claude Code" else "codex"
        home = self.data / "clients" / slot / part if store == "workbench" else self.home
        base = dict(harness=harness, session=ONE, path="/h/s.jsonl", store=store, home=str(home))
        return base | more

    def managed(self, harness="Claude Code", slot="c"):
        var, part = ("CLAUDE_CONFIG_DIR", "claude") if harness == "Claude Code" else ("CODEX_HOME", "codex")
        return {var: str(self.data / "clients" / slot / part), "KEEP": "1"}

    def test_resume_table(self):
        for harness, tail in (
            ("OMP", ["--resume"]),
            ("Pi", ["--resume"]),
            ("Claude Code", ["--resume"]),
            ("Codex", ["resume"]),
            ("opencode", ["--continue"]),
            ("Hermes", ["--continue"]),
        ):
            self.assertEqual(sessions.resume(harness, ["cli"], {}), (["cli", *tail], {}))
        for harness, tail in (
            ("OMP", ["--resume", "/h/s.jsonl"]),
            ("Pi", ["--session", "/h/s.jsonl"]),
            ("Claude Code", ["--resume", ONE]),
            ("opencode", ["--session", ONE]),
        ):
            argv = ["cli", "--model", "m"]
            self.assertEqual(
                sessions.resume(harness, argv, {}, self.row(harness))[0], [*argv, *tail]
            )
            self.assertEqual(argv, ["cli", "--model", "m"])
        with self.assertRaisesRegex(ValueError, "cannot be resumed one by one"):
            sessions.resume("Hermes", ["hermes"], {}, self.row("Hermes"))
        with self.assertRaisesRegex(ValueError, "Aider sessions cannot be resumed from Workbench"):
            sessions.resume("Aider", ["aider"], {})
        self.assertEqual(set(sessions.RESUME), set(sessions.CLI))

    def test_codex_resume_goes_before_options(self):
        argv = ["node", "codex.js", "--model", "m", "-c", "a=1"]
        self.assertEqual(
            sessions.resume("Codex", argv, {}, self.row("Codex"))[0],
            ["node", "codex.js", "resume", ONE, "--model", "m", "-c", "a=1"],
        )
        self.assertEqual(
            sessions.resume("Codex", ["codex", "--model", "m"], {})[0],
            ["codex", "resume", "--model", "m"],
        )
        self.assertEqual(sessions.resume("Codex", ["codex"], {})[0], ["codex", "resume"])

    def test_resume_moves_to_the_session_home(self):
        env = self.managed()
        row = self.row("Claude Code", "workbench")
        argv, moved = sessions.resume("Claude Code", ["claude"], env, row)
        self.assertEqual(argv, ["claude", "--resume", ONE])
        self.assertEqual(moved, {"CLAUDE_CONFIG_DIR": str(self.data / "clients/a/claude"), "KEEP": "1"})
        self.assertEqual(env, self.managed())
        moved = sessions.resume("Codex", ["codex"], self.managed("Codex"), self.row("Codex", "workbench"))[1]
        self.assertEqual(moved["CODEX_HOME"], str(self.data / "clients/a/codex"))
        with self.assertRaisesRegex(ValueError, "no longer available"):
            sessions.resume("Claude Code", ["claude"], env, row | {"home": str(self.other)})
        self.assertEqual(sessions.resume("Claude Code", ["claude"], env)[1], env)

    def test_resume_refuses_a_store_mismatch(self):
        with self.assertRaisesRegex(ValueError, "started with your Claude Code account login"):
            sessions.resume("Claude Code", ["claude"], self.managed(), self.row("Claude Code"))
        for env in ({}, {"CLAUDE_CONFIG_DIR": str(self.other)}):
            with self.assertRaisesRegex(ValueError, "started with a Workbench model source"):
                sessions.resume("Claude Code", ["claude"], env, self.row("Claude Code", "workbench"))
            argv, same = sessions.resume("Claude Code", ["claude"], env, self.row("Claude Code"))
            self.assertEqual((argv, same), (["claude", "--resume", ONE], env))
        with self.assertRaisesRegex(ValueError, "started with your Codex account login"):
            sessions.resume("Codex", ["codex"], self.managed("Codex"), self.row("Codex"))

    def test_resume_refuses_another_harness(self):
        with self.assertRaisesRegex(ValueError, "This is a Codex session; choose Codex as the agent"):
            sessions.resume("Claude Code", ["claude"], {}, self.row("Codex"))


class FolderTests(Fixture):
    def test_project_dir(self):
        for raw in ("", "   ", "relative", "docs/x", None, 5, ["/"]):
            with self.assertRaisesRegex(ValueError, "Choose a project folder first."):
                sessions.project_dir(raw)
        with self.assertRaisesRegex(ValueError, "no longer exists; choose another folder"):
            sessions.project_dir(str(self.tmp / "missing"))
        note = self.tmp / "note.txt"
        note.write_text("x")
        with self.assertRaisesRegex(ValueError, "Choose a project folder, not a file."):
            sessions.project_dir(str(note))
        self.assertEqual(sessions.project_dir(self.project), self.project)
        self.assertEqual(sessions.project_dir(self.project + os.sep), self.project)

    @unittest.skipUnless(POSIX, "Windows does not keep a trailing space in a folder name")
    def test_project_dir_keeps_a_folder_whose_name_ends_in_a_space(self):
        spaced = self.tmp / "proj "
        spaced.mkdir()
        self.assertEqual(sessions.project_dir(str(spaced)), str(spaced))
        self.assertEqual(sessions.project_dir(self.project), self.project)

    @unittest.skipUnless(POSIX, "symlinks need a privilege on Windows")
    def test_project_dir_resolves_a_symlinked_folder(self):
        link = self.tmp / "link"
        link.symlink_to(self.proj, target_is_directory=True)
        self.assertEqual(sessions.project_dir(str(link)), self.project)

    @unittest.skipUnless(sys.platform == "darwin", "case-insensitive volume")
    def test_project_dir_returns_the_stored_case(self):
        mixed = self.tmp / "MixedCase"
        mixed.mkdir()
        if not (self.tmp / "mixedcase").is_dir():
            self.skipTest("case-sensitive volume")
        self.assertEqual(sessions.project_dir(str(self.tmp / "mixedcase")), str(mixed))
        self.assertTrue(sessions.same_folder(str(self.tmp / "MIXEDCASE"), str(mixed)))

    @unittest.skipUnless(POSIX, "symlinks need a privilege on Windows")
    def test_same_folder_resolves_symlinks(self):
        link = self.tmp / "link"
        link.symlink_to(self.proj, target_is_directory=True)
        self.assertTrue(sessions.same_folder(str(link), self.project))
        self.assertTrue(sessions.same_folder(self.proj, self.project))
        self.assertFalse(sessions.same_folder(str(self.other), self.project))
        for odd in (None, "", "proj", 5, str(self.tmp / "missing"), "/x\0y"):
            self.assertFalse(sessions.same_folder(odd, self.project))


class RemoteTests(Fixture):
    def remember(self, name=TMUX, host=HOST, **more):
        fields = dict(folder="~/proj", harness="OMP", mode="native", workspace=self.project)
        sessions.remember_remote(name, host=host, **fields | more)

    def ssh(self, result):
        effect = dict(side_effect=result) if isinstance(result, Exception) else dict(return_value=result)
        return patch.object(sessions.subprocess, "run", **effect)

    def test_remote_registry_round_trip(self):
        with patch.object(sessions.time, "time", return_value=1795400000.0):
            self.remember()
        for name in ("bad name", "awb-XYZ", TMUX + "0", "x; " + TMUX, None):
            sessions.remember_remote(name, host=HOST)
        saved = json.loads((self.data / "remote-sessions.json").read_text())
        self.assertEqual(
            saved,
            {
                TMUX: dict(
                    host=HOST,
                    folder="~/proj",
                    harness="OMP",
                    mode="native",
                    workspace=self.project,
                    started=1795400000.0,
                )
            },
        )
        self.remember(folder="~/other")
        row = sessions.remote_rows()[TMUX]
        self.assertEqual((row["started"], row["folder"]), (1795400000.0, "~/other"))
        sessions.forget_remote(["awb-" + "9" * 16, None])
        self.assertEqual(list(sessions.remote_rows()), [TMUX])
        sessions.forget_remote([TMUX])
        self.assertEqual(sessions.remote_rows(), {})
        full = {f"awb-{n:016x}": dict(host=HOST, started=1000.0 + n) for n in range(60)}
        (self.data / "remote-sessions.json").write_text(json.dumps(full))
        self.remember()
        kept = sessions.remote_rows()
        self.assertEqual(len(kept), 60)
        self.assertIn(TMUX, kept)
        self.assertNotIn("awb-" + "0" * 16, kept)
        (self.data / "remote-sessions.json").write_text("{broken")
        self.assertEqual(sessions.remote_rows(), {})
        self.remember()
        self.assertEqual(list(sessions.remote_rows()), [TMUX])

    def test_remote_parses_only_valid_lines(self):
        gone, elsewhere, fresh = ("awb-" + c * 16 for c in "fe1")
        self.remember()
        self.remember(gone)
        self.remember(elsewhere, host="me@other-box")
        out = "\n".join(
            [
                TMUX + " 1795400000 1",
                "evil; rm -rf 1 1",
                "",
                TMUX + " 1795400000 1 extra",
                "x" + TMUX + " 1 1",
                TMUX.upper() + " 1 1",
                fresh + " 1795400001 0",
                TMUX + " 5 0",
            ]
        )
        with patch.dict(sys.modules, {"machines": self.machines()}), self.ssh(done(0, out)) as run:
            rows = sessions.remote(" " + HOST + " ")
        self.assertEqual([r["name"] for r in rows], [TMUX, fresh])
        self.assertEqual(
            rows[0],
            dict(
                id=f"remote:{HOST}:{TMUX}",
                kind="remote",
                name=TMUX,
                title="OMP · ~/proj",
                harness="OMP",
                workspace=self.project,
                host=HOST,
                folder="~/proj",
                mode="native",
                pid=None,
                started=1795400000,
                state="attached",
            ),
        )
        self.assertEqual(
            [rows[1][k] for k in ("state", "harness", "folder", "workspace", "title", "host")],
            ["detached", "", "", "", "Shell", HOST],
        )
        self.assertEqual(sorted(sessions.remote_rows()), sorted([TMUX, elsewhere]))
        argv = run.call_args.args[0]
        self.assertEqual(argv, ["ssh", "-o", "BatchMode=yes", HOST, sessions.LIST])
        self.assertEqual(run.call_count, 1)
        kwargs = run.call_args.kwargs
        self.assertEqual((kwargs["timeout"], kwargs["stdin"]), (12, subprocess.DEVNULL))
        self.assertNotIn("shell", kwargs)

    def test_remote_keeps_a_session_the_dock_is_still_opening(self):
        opening, ended = TMUX, "awb-" + "c" * 16
        self.remember(opening)
        self.remember(ended)
        dock = types.SimpleNamespace(
            active=lambda: [dict(id="t1", kind="remote", host=HOST, name=opening, pid=1)]
        )
        modules = {"machines": self.machines(), "project_terminal": dock}
        with patch.dict(sys.modules, modules), self.ssh(done(0, "")):
            self.assertEqual(sessions.remote(HOST), [])
        self.assertEqual(list(sessions.remote_rows()), [opening])
        half = types.ModuleType("project_terminal")
        with patch.dict(sys.modules, modules | {"project_terminal": half}), self.ssh(done(0, "")):
            self.assertEqual(sessions.remote(HOST), [])
        self.assertEqual(sessions.remote_rows(), {})

    def test_remote_reports_unreachable_and_timeout(self):
        self.remember()
        with patch.dict(sys.modules, {"machines": self.machines()}):
            with self.ssh(done(255)), self.assertRaisesRegex(ValueError, "Could not reach GPU box over SSH"):
                sessions.remote(HOST)
            late = subprocess.TimeoutExpired(["ssh"], 12)
            with self.ssh(late), self.assertRaisesRegex(ValueError, "SSH to GPU box timed out after 12 seconds"):
                sessions.remote(HOST)
            with self.ssh(FileNotFoundError("ssh")), self.assertRaisesRegex(ValueError, "OpenSSH was not found"):
                sessions.remote(HOST)
            with self.ssh(done(1)):
                self.assertEqual(sessions.remote(HOST), [])
            self.assertEqual(list(sessions.remote_rows()), [TMUX])
            with self.ssh(done()) as run:
                with self.assertRaisesRegex(ValueError, "Save this SSH machine first."):
                    sessions.remote("me@unsaved")
                refusal = ValueError("Use an SSH config alias or user@hostname.")
                strict = self.machines(target=MagicMock(side_effect=refusal))
                with patch.dict(sys.modules, {"machines": strict}):
                    with self.assertRaisesRegex(ValueError, "SSH config alias"):
                        sessions.remote("-oProxyCommand=x")
                run.assert_not_called()

    def test_end_remote_validates_the_name(self):
        self.remember()
        with patch.dict(sys.modules, {"machines": self.machines()}):
            with self.ssh(done()) as run:
                for name in ("x; reboot", TMUX + "; reboot", None, ""):
                    with self.assertRaisesRegex(ValueError, "Choose a remote session from the list."):
                        sessions.end_remote(HOST, name)
                run.assert_not_called()
            with self.ssh(done(255)), self.assertRaisesRegex(ValueError, "Could not reach"):
                sessions.end_remote(HOST, TMUX)
            self.assertEqual(list(sessions.remote_rows()), [TMUX])
            with self.ssh(done(1)) as run:
                self.assertIs(sessions.end_remote(HOST, TMUX), True)
            self.assertEqual(run.call_count, 1)
            argv = run.call_args.args[0]
            self.assertEqual(argv[-2:], [HOST, "tmux -L agent-workbench kill-session -t " + TMUX])
            self.assertEqual(sessions.remote_rows(), {})


class RunningTests(Fixture):
    def test_running_merges_and_filters(self):
        docked, waiting = "awb-" + "a" * 16, "awb-" + "b" * 16
        shell = dict(id="t1", kind="embedded", harness="OMP", title="OMP · m.gguf", workspace=self.project)
        away = dict(id="t2", kind="remote", harness="", title="GPU box · Shell · ~", workspace=self.project)
        terminals = types.SimpleNamespace(
            active=lambda: [
                shell | dict(pid=11, started=1.0, host="", name=""),
                away | dict(pid=12, started=2.0, host=HOST, name=docked),
            ]
        )
        exits = [dict(harness="Codex", workspace=self.project, code=1, message="Codex exited.")]
        window = types.SimpleNamespace(
            active_sessions=lambda: [
                dict(pid=11, born=1.0, harness="OMP", workspace=self.project),
                dict(
                    pid=22,
                    born=3.0,
                    id="ticket",
                    kind="native",
                    harness="Claude Code",
                    workspace=self.project,
                    started=4.0,
                    model="qwen\x1b[31m",
                ),
                dict(pid=33, born=5.0, harness="OMP", workspace=str(self.other)),
            ],
            recent_exits=lambda: exits,
        )
        for name in (docked, waiting):
            sessions.remember_remote(name, host=HOST, folder="~/p", harness="OMP", mode="native", workspace=self.project)
        folder = self.data / "ssh" / "abc"
        folder.mkdir(parents=True)
        (folder / "tunnel.json").write_text(json.dumps(dict(pid=77, born=7.0, port=4000)))
        (folder / "tunnel-8080.json").write_text(json.dumps(dict(pid=78, born=8.0, port=4001, remote=8080)))
        (folder / "tunnel-9090.json").write_text(json.dumps(dict(port=4002, remote=9090)))
        (folder / "tunnel-7070.json").write_text("{broken")
        machines = self.machines(
            identifier=lambda host: "abc",
            identity=MagicMock(),
            owned_tunnel=lambda rec: types.SimpleNamespace(pid=rec["pid"]) if "pid" in rec else None,
        )
        spec = dict(model=str(self.tmp / "models/qwen3-coder.gguf"), engine="llama.cpp", context=32768)
        owned = (types.SimpleNamespace(pid=5151), dict(born=5.0, started=6, port=10999, spec=spec))
        with (
            self.standins(project_terminal=terminals, session_window=window, machines=machines),
            patch("runtime.owned", return_value=owned),
            patch("subprocess.run") as run,
            patch("subprocess.Popen") as popen,
            patch("socket.create_connection") as connect,
        ):
            out = sessions.running()
            here = sessions.running(self.project)
            there = sessions.running(str(self.other))
            unused = sessions.running(str(self.tmp))
            for network in (run, popen, connect, machines.identity):
                network.assert_not_called()
        rows = out["sessions"]
        self.assertEqual(
            [(r["id"], r["kind"], r["state"], r["pid"]) for r in rows],
            [
                ("term:t1", "embedded", "running", 11),
                ("term:t2", "remote", "attached", 12),
                ("native:ticket", "native", "running", 22),
                ("native:33", "native", "running", 33),
                (f"remote:{HOST}:{waiting}", "remote", "unverified", None),
                ("model", "model", "running", 5151),
                (f"tunnel:{HOST}", "tunnel", "running", 77),
                (f"tunnel:{HOST}:8080", "tunnel", "running", 78),
            ],
        )
        for row in rows:
            self.assertLessEqual(
                {"id", "kind", "title", "harness", "workspace", "pid", "started", "state"}, set(row)
            )
        self.assertEqual((rows[0]["terminal"], rows[0]["title"]), ("t1", "OMP · m.gguf"))
        self.assertNotIn("host", rows[0])
        self.assertEqual((rows[1]["host"], rows[1]["name"], rows[1]["terminal"]), (HOST, docked, "t2"))
        self.assertEqual((rows[2]["title"], rows[2]["started"]), ("Claude Code · qwen [31m", 4.0))
        self.assertEqual((rows[3]["title"], rows[3]["started"]), ("OMP", 5.0))
        self.assertEqual(
            [rows[4][k] for k in ("host", "name", "folder", "mode", "title")],
            [HOST, waiting, "~/p", "native", "OMP · ~/p"],
        )
        self.assertEqual(
            [rows[5][k] for k in ("title", "port", "context", "started", "workspace", "harness")],
            ["qwen3-coder.gguf", 10999, 32768, 6, "", ""],
        )
        self.assertEqual(
            [(r["title"], r["host"], r["port"], r["started"]) for r in rows[6:]],
            [("GPU box", HOST, 4000, 7.0), ("GPU box · 8080", HOST, 4001, 8.0)],
        )
        self.assertEqual(out["exits"], exits)
        self.assertEqual([r["id"] for r in here["sessions"]], [r["id"] for r in rows if r["id"] != "native:33"])
        self.assertEqual(
            [r["id"] for r in there["sessions"]],
            ["native:33", "model", f"tunnel:{HOST}", f"tunnel:{HOST}:8080"],
        )
        self.assertEqual([r["kind"] for r in unused["sessions"]], ["model", "tunnel", "tunnel"])

    def test_running_names_a_vllm_model_and_survives_bad_records(self):
        spec = dict(model="/models/old.gguf", engine="vLLM", vllm_model="Qwen/Qwen3-Coder")
        owned = (types.SimpleNamespace(pid=5151), dict(born=5.0, spec=spec))
        (self.data / "remote-sessions.json").write_text(json.dumps({TMUX: "x", "bad": {}, TMUX[:-1] + "0": {}}))
        machines = self.machines(labels=MagicMock(side_effect=ValueError("bad json")))
        with self.standins(machines=machines), patch("runtime.owned", return_value=owned):
            rows = sessions.running()["sessions"]
        self.assertEqual([r["kind"] for r in rows], ["remote", "model"])
        self.assertEqual((rows[0]["title"], rows[0]["workspace"], rows[0]["started"]), ("Shell", "", None))
        self.assertEqual((rows[1]["title"], rows[1]["started"]), ("Qwen/Qwen3-Coder", 5.0))
        self.assertEqual(rows[1]["port"], sys.modules["runtime"].PORT)
        with self.standins(), patch("runtime.owned", return_value=(None, {})):
            with patch.dict(sys.modules):
                sys.modules.pop("project_terminal", None)
                self.assertEqual([r["kind"] for r in sessions.running()["sessions"]], ["remote"])
                self.assertNotIn("project_terminal", sys.modules)

    def test_running_survives_a_half_imported_terminal_module(self):
        half = types.ModuleType("project_terminal")
        with self.standins(project_terminal=half), patch("runtime.owned", return_value=(None, {})):
            self.assertEqual(sessions.running(), dict(sessions=[], exits=[]))

    def test_dispatch_validates(self):
        known = types.SimpleNamespace(HARNESS=READABLE + ["Hermes"])
        with patch.dict(sys.modules, {"connections": known}), patch.object(sessions, "recent", return_value=[]) as recent:
            with self.assertRaisesRegex(ValueError, "Choose a supported agent."):
                sessions.dispatch("sessions-recent", dict(workspace=self.project, harness="Nope"))
            with self.assertRaisesRegex(ValueError, "Choose a project folder first."):
                sessions.dispatch("sessions-recent", dict(harness="Codex"))
            recent.assert_not_called()
            for payload, expected in (
                (dict(limit=500), (None, 80)),
                (dict(limit=0), (None, 1)),
                (dict(limit="7", harness="Codex"), ("Codex", 7)),
                (dict(limit="many", harness=""), (None, 20)),
                (dict(limit=None), (None, 20)),
                ({}, (None, 20)),
            ):
                self.assertEqual(sessions.dispatch("sessions-recent", payload | {"workspace": self.project}), [])
                self.assertEqual(recent.call_args.args, (self.project, *expected))
        for action in ("sessions-nope", "term-list", ""):
            with self.assertRaisesRegex(ValueError, "Unknown sessions action."):
                sessions.dispatch(action, {})
        with self.standins(), patch("runtime.owned", return_value=(None, {})):
            self.assertEqual(sessions.dispatch("sessions-running", {}), dict(sessions=[], exits=[]))
            self.assertEqual(
                set(sessions.dispatch("sessions-running", dict(workspace=self.project))), {"sessions", "exits"}
            )
            with self.assertRaisesRegex(ValueError, "Choose a project folder first."):
                sessions.dispatch("sessions-running", dict(workspace="docs"))
        with patch.object(sessions, "remote", return_value=[]) as remote:
            self.assertEqual(sessions.dispatch("sessions-remote", {}), [])
            remote.assert_called_once_with("")
        with patch.object(sessions, "end_remote", return_value=True) as end:
            self.assertIs(sessions.dispatch("sessions-end", dict(host=HOST, name=TMUX)), True)
            end.assert_called_once_with(HOST, TMUX)

    def test_harnesses_are_the_readable_ones(self):
        known = types.SimpleNamespace(HARNESS=["OMP", "Pi", "Claude Code", "Codex", "Hermes", "opencode"])
        with patch.dict(sys.modules, {"connections": known}):
            self.assertEqual(HARNESSES(), READABLE)
        known.HARNESS.remove("opencode")
        with patch.dict(sys.modules, {"connections": known}):
            self.assertEqual(HARNESSES(), READABLE[:4])


if __name__ == "__main__":
    unittest.main()
