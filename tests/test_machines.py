import ast
import atexit
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

if "runtime" not in sys.modules and "WORKBENCH_DATA" not in os.environ:
    os.environ["WORKBENCH_DATA"] = tempfile.mkdtemp(prefix="workbench-test-")
    atexit.register(shutil.rmtree, os.environ["WORKBENCH_DATA"], ignore_errors=True)

import requests  # noqa: E402

import account_book  # noqa: E402
import connections  # noqa: E402
import local_permissions  # noqa: E402
import machines  # noqa: E402

try:
    import remote_node
except ImportError:
    remote_node = None

ROOT = Path(__file__).resolve().parent.parent
LABEL = machines._label
ALL = {"chat": True, "responses": True, "messages": True}
CHAT = {"chat": True, "responses": False, "messages": False}
TOKEN = "node-key_" + "A1b2" * 8
BATCH = [
    "ssh",
    "-o",
    "ConnectTimeout=7",
    "-o",
    "ServerAliveInterval=20",
    "-o",
    "BatchMode=yes",
    "-o",
    "StrictHostKeyChecking=yes",
]
ROWS = [
    {"port": 10542, "kind": "llamacpp", "models": ["local-agent"]},
    {"port": "x"},
    "junk",
    {"port": True},
    {"port": 70000},
    {
        "port": 11434,
        "kind": "ollama",
        "label": "Ollama 0.40",
        "base": "http://127.0.0.1:11434/v1",
        "auth": "none",
        "models": [
            {"id": "qwen3:14b", "context": 32768, "loaded": True},
            {"id": "bad\nid"},
            {"id": "x" * 201},
            "gemma3",
            {"id": "huge", "context": 4000001},
            7,
            "-rf",
            " padded ",
        ],
        "protocols": dict(ALL),
        "needs_key": False,
        "evil": {"x": 1},
    },
    {
        "port": 1234,
        "kind": "lmstudio",
        "models": [],
        "protocols": {"chat": "yes"},
        "needs_key": True,
        "base_path": "/v1; rm",
    },
]


def done(code=0, out=b"", err=b""):
    return subprocess.CompletedProcess([], code, out, err)


def fake_endpoints(script="import json\nprint(json.dumps([]))\n", **extra):
    module = types.ModuleType("endpoints")
    module.remote_probe_script = lambda: script
    module.probe_protocols = lambda base, key="": dict(CHAT)
    for name, value in extra.items():
        setattr(module, name, value)
    return patch.dict(sys.modules, {"endpoints": module})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = data = Path(self.tmp.name)
        for item in (
            patch.object(machines, "DATA", data),
            patch.object(machines, "HOSTS", data / "machines.json"),
            patch.object(machines, "LINKS", data / "machine-links.json"),
            patch.object(account_book, "DATA", data),
            patch.object(local_permissions, "protect"),
            patch.object(connections, "store_key"),
            patch.object(connections, "forget_key"),
            patch.object(connections, "secret", return_value=""),
            patch.object(machines.time, "sleep"),
            patch.object(machines, "_label", return_value=""),
        ):
            item.start()
            self.addCleanup(item.stop)

    def use(self, item):
        value = item.start()
        self.addCleanup(item.stop)
        return value

    def http(self, payload=None, error=None):
        session = MagicMock()
        if error:
            session.get.side_effect = error
        else:
            session.get.return_value.json.return_value = payload
        self.use(patch.object(machines, "_http", return_value=session))
        return session

    def found(self, rows=ROWS, **extra):
        self.use(fake_endpoints(**extra))
        return self.use(patch.object(machines, "execute", return_value=rows))

    def record(self, host, name="tunnel.json"):
        return machines.DATA / "ssh" / machines.identifier(host) / name


class MachinesTest(Base):
    def test_ssh_arguments_unchanged(self):
        self.assertEqual(machines.ssh("user@box"), BATCH)
        key = machines.identity("user@box")
        key.write_text("k")
        self.assertEqual(
            machines.ssh("user@box", True),
            [
                "ssh",
                "-o",
                "ConnectTimeout=7",
                "-o",
                "ServerAliveInterval=20",
                "-i",
                str(key),
            ],
        )
        self.assertEqual(machines.ssh("user@box"), BATCH + ["-i", str(key)])

    def test_dispatch_trims_host(self):
        saved = machines.dispatch(
            "machine-save", {"host": " user@box ", "name": " Lab "}
        )
        self.assertEqual(saved, "user@box")
        self.assertEqual(machines.labels(), {"user@box": "Lab"})
        self.assertEqual(machines.dispatch("machine-list", {}), {"user@box": "Lab"})
        for bad in (
            {},
            {"host": None},
            {"host": 5},
            {"host": "a b"},
            {"host": "-oProxyCommand=x"},
        ):
            with self.assertRaisesRegex(ValueError, "SSH config alias"):
                machines.dispatch("machine-save", bad)
        machines.dispatch("machine-save", {"host": "other", "name": None})
        self.assertEqual(machines.labels()["other"], "other")

    def test_explain_maps_failures(self):
        changed = (
            "@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@\n"
            "Host key verification failed."
        )
        busy = (
            "bind [127.0.0.1]:8888: Address already in use\n"
            "channel_setup_fwd_listener_tcpip: cannot listen to port: 8888\n"
            "Could not request local forwarding."
        )
        denied = "[Errno 13] Permission denied: '/home/u/.local'"
        old = '  File "<stdin>", line 230\n    if p := pid():\nSyntaxError: invalid syntax'
        for code, err, want in [
            (255, changed, "The host key of box has changed; if that machine"),
            (255, "Host key verification failed.", "use Pair through SSH once"),
            (
                255,
                "Received disconnect: Too many authentication failures",
                "add IdentitiesOnly yes",
            ),
            (
                255,
                "user@box: Permission denied (publickey,password).",
                "box refused the login; pair",
            ),
            (
                255,
                "ssh: Could not resolve hostname box: nodename nor servname",
                "The name box could not be found",
            ),
            (
                255,
                "ssh: connect to host box port 22: Connection refused",
                "refused the SSH connection",
            ),
            (255, busy, "SSH could not open the local forward for box"),
            (
                255,
                "Could not request local forwarding.",
                "SSH could not open the local forward for box",
            ),
            (
                255,
                "ssh: connect to host box port 22: Operation timed out",
                "box did not answer",
            ),
            (
                255,
                "ssh: connect to host box port 22: No route to host",
                "box did not answer",
            ),
            (255, "connect: Network is unreachable", "box did not answer"),
            (
                255,
                "first\nkex_exchange_identification: read: reset",
                "SSH to box failed (kex_exchange_identification: read: reset); check pairing",
            ),
            (
                255,
                "",
                "SSH to box failed; check pairing, the host key and the SSH port, then retry.",
            ),
            (
                127,
                "bash: line 1: python3: command not found",
                "python3 was not found on box; install Python 3.8",
            ),
            (1, "zsh:1: command not found: python3", "python3 was not found on box"),
            (1, "sh: 1: python3: not found", "python3 was not found on box"),
            (
                1,
                "xcode-select: note: No developer tools were found.",
                "run xcode-select --install on that Mac",
            ),
            (1, old, "The python3 on box is older than 3.8"),
            (
                1,
                "",
                "The command on box failed without a message; use Check connection",
            ),
        ]:
            self.assertIn(want, machines.explain("box", code, err), (code, err))
        self.assertEqual(machines.explain("box", 1, denied), denied)
        self.assertEqual(machines.explain("box", 1, "x" * 700), "x" * 650)
        notice = (
            "** WARNING: connection is not using a post-quantum key exchange algorithm.\n"
            '** This session may be vulnerable to "store now, decrypt later" attacks.\n'
            "** The server may need to be upgraded. See https://openssh.com/pq.html\n"
        )
        self.assertEqual(machines.explain("box", 1, notice + denied), denied)
        self.assertIn("failed without a message", machines.explain("box", 1, notice))
        self.assertEqual(
            machines.explain("box", 255, notice),
            "SSH to box failed; check pairing, the host key and the SSH port, then retry.",
        )
        self.assertIn(
            "(client_loop: send disconnect: Broken pipe)",
            machines.explain(
                "box", 255, notice + "client_loop: send disconnect: Broken pipe\n"
            ),
        )

    def test_execute_parses_last_line_and_maps_errors(self):
        reply = done(0, b'motd line\n{"a": 1}\n\n')
        with patch.object(machines.subprocess, "run", return_value=reply) as ran:
            self.assertEqual(machines.execute("box", b"src", "python3 -"), {"a": 1})
            self.assertEqual(ran.call_args.args[0], BATCH + ["box", "python3 -"])
            self.assertEqual(ran.call_args.kwargs["input"], b"src")
            self.assertNotIn("stdin", ran.call_args.kwargs)
            self.assertEqual(ran.call_args.kwargs["timeout"], 90)
            self.assertEqual(machines._ssh_run("box", ["true"], None, 5), reply.stdout)
            self.assertIs(ran.call_args.kwargs["stdin"], subprocess.DEVNULL)
            self.assertNotIn("input", ran.call_args.kwargs)
            self.assertEqual(machines.run("box", "probe", 20), {"a": 1})
            self.assertEqual(ran.call_args.args[0][-1], "python3 - probe")
            self.assertEqual(
                ran.call_args.kwargs["input"], (ROOT / "remote_node.py").read_bytes()
            )
        lost = done(255, err=b"Host key verification failed.")
        with patch.object(machines.subprocess, "run", return_value=lost):
            with self.assertRaisesRegex(machines.Unreachable, "Pair through SSH"):
                machines.run("box", "probe")
        failed = done(1, err=b"This machine needs 13 GiB of free disk space.")
        with patch.object(machines.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(ValueError, "13 GiB") as caught:
                machines.run("box", "install")
            self.assertNotIsInstance(caught.exception, machines.Unreachable)
        slow = subprocess.TimeoutExpired("ssh", 20)
        with patch.object(machines.subprocess, "run", side_effect=slow):
            with self.assertRaisesRegex(machines.Unreachable, "within 20 seconds"):
                machines.run("box", "probe", 20)
        for out in (b"hello\n", b"", b"[" * 100000):
            with patch.object(machines.subprocess, "run", return_value=done(0, out)):
                with self.assertRaisesRegex(ValueError, "could not read"):
                    machines.run("box", "probe")
        with patch.object(machines.subprocess, "run", side_effect=FileNotFoundError()):
            with self.assertRaises(ValueError) as caught:
                machines.run("box", "probe")
            self.assertEqual(str(caught.exception), machines.NO_SSH)

    def test_forward_reuses_live_record_and_prefers_saved_port(self):
        path = machines.identity("box").parent / "tunnel.json"
        machines.write_json(path, dict(pid=1, born=1.0, port=40111))
        with (
            patch.object(machines, "owned_tunnel", return_value=MagicMock()),
            patch.object(machines.subprocess, "Popen") as popen,
        ):
            self.assertEqual(machines.forward("box", 10542), 40111)
            popen.assert_not_called()
        child = MagicMock(pid=os.getpid())
        child.poll.return_value = None
        with (
            patch.object(machines, "owned_tunnel", return_value=None),
            patch.object(machines, "_free_port", side_effect=lambda p=None: p) as free,
            patch.object(machines.subprocess, "Popen", return_value=child) as popen,
            patch.object(machines, "_listening", return_value=True),
        ):
            self.assertEqual(machines.forward("box", 10542), 40111)
            free.assert_called_once_with(40111)
            self.assertEqual(
                popen.call_args.args[0],
                BATCH
                + [
                    "-NT",
                    "-o",
                    "ExitOnForwardFailure=yes",
                    "-L",
                    "127.0.0.1:40111:127.0.0.1:10542",
                    "box",
                ],
            )
            self.assertIs(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
            self.assertIs(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
            self.assertEqual(
                popen.call_args.kwargs["stderr"].name, str(path.with_suffix(".log"))
            )
        rec = machines.load(path, {})
        self.assertEqual(
            (rec["port"], rec["remote"], rec["pid"]), (40111, 10542, os.getpid())
        )
        self.assertIsInstance(rec["born"], float)

    def test_forward_replaces_a_forward_to_another_port(self):
        path = machines.identity("box").parent / "tunnel.json"
        machines.write_json(path, dict(pid=1, born=1.0, port=40111, remote=9000))
        live, child = MagicMock(), MagicMock(pid=os.getpid())
        child.poll.return_value = None
        with (
            patch.object(machines, "owned_tunnel", return_value=live),
            patch.object(machines, "_free_port", return_value=40111),
            patch.object(machines.subprocess, "Popen", return_value=child),
            patch.object(machines, "_listening", return_value=None),
            patch.object(machines, "_accepts", side_effect=[False, True]) as accepts,
        ):
            self.assertEqual(machines.forward("box", 10542, "tunnel.json"), 40111)
        live.terminate.assert_called_once()
        self.assertEqual(accepts.call_count, 2)
        self.assertEqual(machines.load(path, {})["remote"], 10542)

    def test_forward_uses_one_record_per_attached_port(self):
        child = MagicMock(pid=os.getpid())
        child.poll.return_value = None
        with (
            patch.object(machines, "_free_port", return_value=40555),
            patch.object(machines.subprocess, "Popen", return_value=child),
            patch.object(machines, "_listening", return_value=True),
        ):
            self.assertEqual(machines.forward("box", 11434), 40555)
            self.assertEqual(machines.forward("box", "10542"), 40555)
        self.assertEqual(
            machines.load(self.record("box", "tunnel-11434.json"), {})["remote"], 11434
        )
        self.assertEqual(machines.load(self.record("box"), {})["remote"], 10542)

    def test_forward_reports_reason_when_ssh_exits(self):
        log = machines.identity("box").parent / "tunnel-11434.log"

        def spawn(argv, **kw):
            kw["stderr"].write("user@box: Permission denied (publickey).\n")
            kw["stderr"].flush()
            child = MagicMock(pid=os.getpid())
            child.poll.return_value = 255
            return child

        with patch.object(machines.subprocess, "Popen", side_effect=spawn):
            with self.assertRaisesRegex(machines.Unreachable, "refused the login"):
                machines.forward("box", 11434, "tunnel-11434.json")
        rec = machines.load(log.with_suffix(".json"), {})
        self.assertEqual(sorted(rec), ["port", "remote"])
        self.assertEqual(rec["remote"], 11434)
        self.assertIsInstance(rec["port"], int)
        with patch.object(
            machines.subprocess, "Popen", side_effect=FileNotFoundError()
        ):
            with self.assertRaises(ValueError) as caught:
                machines.forward("box", 11434, "tunnel-11434.json")
            self.assertEqual(str(caught.exception), machines.NO_SSH)

    def test_forward_rejects_a_port_another_program_answers_on(self):
        child = MagicMock(pid=os.getpid())
        child.poll.side_effect = [None, 255, 255]
        with (
            patch.object(machines.subprocess, "Popen", return_value=child),
            patch.object(machines, "_listening", return_value=None),
            patch.object(machines, "_accepts", return_value=True),
        ):
            with self.assertRaises(machines.Unreachable):
                machines.forward("box", 11434)
        self.assertNotIn(
            "pid", machines.load(self.record("box", "tunnel-11434.json"), {})
        )

    def test_forward_gives_up_after_ten_seconds(self):
        child = MagicMock(pid=os.getpid())
        child.poll.return_value = None
        with (
            patch.object(machines.subprocess, "Popen", return_value=child),
            patch.object(machines, "_listening", return_value=False),
            patch.object(machines.time, "sleep") as sleep,
        ):
            with self.assertRaisesRegex(machines.Unreachable, "within 10 seconds"):
                machines.forward("box", 11434)
            self.assertEqual(sleep.call_count, 50)
        child.terminate.assert_called_once()
        self.assertNotIn(
            "pid", machines.load(self.record("box", "tunnel-11434.json"), {})
        )

    def test_free_port_skips_busy_port(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen()
            busy = s.getsockname()[1]
            self.assertNotEqual(machines._free_port(busy), busy)
            self.assertTrue(machines._accepts(busy))
            if os.name != "nt":
                self.assertIs(machines._listening(os.getpid(), busy), True)
        self.assertEqual(machines._free_port(busy), busy)
        self.assertFalse(machines._accepts(busy))
        for ignored in ("x", None, True, 80, 70000, 1.5):
            self.assertGreaterEqual(machines._free_port(ignored), 1024)
        with patch.object(machines.socket, "socket", side_effect=OSError()):
            with self.assertRaisesRegex(ValueError, "no free loopback port"):
                machines._free_port(busy)

    def test_port_helpers_branch_on_windows(self):
        for name, reused in (("nt", False), ("posix", True)):
            with (
                patch.object(machines.os, "name", name),
                patch.object(machines.socket, "socket") as made,
            ):
                sock = made.return_value.__enter__.return_value
                sock.getsockname.return_value = ("127.0.0.1", 40001)
                self.assertEqual(machines._free_port(40001), 40001)
                sock.bind.assert_called_once_with(("127.0.0.1", 40001))
                self.assertEqual(sock.setsockopt.called, reused)
        with (
            patch.object(machines.os, "name", "nt"),
            patch.object(machines.psutil, "Process") as process,
        ):
            self.assertIsNone(machines._listening(1, 40001))
            process.assert_not_called()
        with (
            patch.object(machines.os, "name", "posix"),
            patch.object(
                machines.psutil, "Process", side_effect=machines.psutil.AccessDenied(1)
            ),
        ):
            self.assertIsNone(machines._listening(1, 40001))

    def test_owned_tunnel_rejects_zombies_and_bad_records(self):
        for rec in (
            {},
            {"pid": os.getpid(), "born": "x"},
            {"pid": "x", "born": 1.0},
            {"pid": -5, "born": 1.0},
        ):
            self.assertIsNone(machines.owned_tunnel(rec))
        fake = MagicMock()
        fake.create_time.return_value = 5.0
        fake.name.return_value = "ssh"
        fake.status.return_value = machines.psutil.STATUS_ZOMBIE
        with patch.object(machines.psutil, "Process", return_value=fake):
            self.assertIsNone(machines.owned_tunnel({"pid": 9, "born": 5.0}))
            fake.status.return_value = machines.psutil.STATUS_RUNNING
            self.assertIs(machines.owned_tunnel({"pid": 9, "born": 5.0}), fake)
            self.assertIsNone(machines.owned_tunnel({"pid": 9, "born": 6.0}))
            fake.name.return_value = "python"
            self.assertIsNone(machines.owned_tunnel({"pid": 9, "born": 5.0}))
            fake.name.side_effect = machines.psutil.AccessDenied(9)
            self.assertIsNone(machines.owned_tunnel({"pid": 9, "born": 5.0}))

    def test_disconnect_keeps_the_port_for_the_next_forward(self):
        folder = machines.identity("box").parent
        machines.write_json(folder / "tunnel.json", dict(pid=1, born=1.0, port=40111))
        machines.write_json(
            folder / "tunnel-11434.json",
            dict(pid=2, born=1.0, port=40222, remote=11434),
        )
        machines.write_json(folder / "tunnel-8000.json", dict(pid=3, born=1.0))
        live = MagicMock()
        with patch.object(machines, "owned_tunnel", return_value=live):
            machines.disconnect("box", "tunnel-11434.json")
        live.terminate.assert_called_once()
        live.wait.assert_called_once_with(3)
        self.assertEqual(
            machines.load(folder / "tunnel-11434.json", {}),
            {"port": 40222, "remote": 11434},
        )
        self.assertIn("pid", machines.load(folder / "tunnel.json", {}))
        slow = MagicMock()
        slow.wait.side_effect = machines.psutil.TimeoutExpired(3)
        with patch.object(machines, "owned_tunnel", return_value=slow):
            machines.disconnect("box")
        slow.kill.assert_called()
        self.assertEqual(machines.load(folder / "tunnel.json", {}), {"port": 40111})
        self.assertFalse((folder / "tunnel-8000.json").exists())
        machines.disconnect("box", "tunnel-9.json")
        self.assertFalse((folder / "tunnel-9.json").exists())

    def test_loopback_requests_skip_proxies(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "http://proxy.invalid:3128"}):
            session = machines._http()
            self.assertIs(session.trust_env, False)
            self.assertEqual(
                session.merge_environment_settings(
                    "http://127.0.0.1:1/v1/models", {}, None, None, None
                )["proxies"],
                {},
            )

    def test_links_ignore_damaged_rows(self):
        good = dict(
            host="box", remote_port=11434, managed=False, kind="ollama", base_path="/v1"
        )
        machines.write_json(
            machines.LINKS,
            {
                "a": good,
                "b": "junk",
                "c": {"host": "bad host", "remote_port": 1},
                "d": {"host": "box", "remote_port": "1"},
                "e": {"host": "box"},
            },
        )
        self.assertEqual(machines.links(), {"a": good})
        machines.LINKS.write_text("not json")
        self.assertEqual(machines.links(), {})
        self.assertEqual(machines.forwards(), [])
        machines.LINKS.write_text("[1]")
        self.assertEqual(machines.links(), {})
        self.assertEqual(
            machines.link_id("box", 11434),
            hashlib.sha256(b"box:11434").hexdigest()[:16],
        )
        self.assertEqual(machines._record(good), "tunnel-11434.json")
        self.assertEqual(machines._record(dict(good, managed=True)), "tunnel.json")

    def test_publish_moves_the_vault_entry(self):
        old = account_book.save(
            dict(
                provider="Attached endpoint",
                name="N",
                endpoint="http://127.0.0.1:4001/v1",
                connection_id="a" * 16,
            )
        )
        new = dict(old, endpoint="http://127.0.0.1:4002/v1")
        connections.secret.return_value = "kept"
        row = machines._publish(new)
        self.assertEqual(row["endpoint"], "http://127.0.0.1:4002/v1")
        connections.secret.assert_called_once_with(old)
        connections.store_key.assert_called_once_with(new, "kept")
        connections.forget_key.assert_called_once_with(old)
        for mock in (connections.secret, connections.store_key, connections.forget_key):
            mock.reset_mock()
        machines._publish(dict(new, name="Renamed"))
        connections.secret.assert_not_called()
        connections.store_key.assert_not_called()
        connections.forget_key.assert_not_called()
        with patch.object(connections, "vault_slot", return_value="one slot"):
            machines._publish(dict(new, endpoint="http://127.0.0.1:4003/v1"), "given")
        connections.store_key.assert_called_once()
        self.assertEqual(connections.store_key.call_args.args[1], "given")
        connections.forget_key.assert_not_called()
        connections.secret.side_effect = ValueError("no key")
        connections.forget_key.side_effect = RuntimeError("vault locked")
        connections.store_key.reset_mock()
        moved = machines._publish(dict(new, endpoint="http://127.0.0.1:4004/v1"))
        self.assertEqual(moved["endpoint"], "http://127.0.0.1:4004/v1")
        connections.store_key.assert_not_called()

    def test_protocols_are_three_flags_or_unknown(self):
        mixed = {"chat": True, "responses": None, "messages": False}
        self.assertEqual(machines._flags(dict(mixed, extra=1)), mixed)
        self.assertEqual(
            machines._flags({"chat": True}),
            {"chat": True, "responses": None, "messages": None},
        )
        for bad in (
            None,
            "x",
            {},
            {"chat": "yes"},
            {"chat": 1, "responses": True, "messages": True},
            dict.fromkeys(ALL),
        ):
            self.assertIsNone(machines._flags(bad))
        with fake_endpoints():
            self.assertEqual(machines._protocols("http://127.0.0.1:1/v1", "k"), CHAT)
        probe = MagicMock(return_value={"protocols": mixed})
        with fake_endpoints(probe_protocols=None, probe=probe):
            self.assertEqual(machines._protocols("http://127.0.0.1:1/v1", "k"), mixed)
            probe.assert_called_once_with("http://127.0.0.1:1/v1", "k")
        with fake_endpoints(probe_protocols=MagicMock(side_effect=OSError())):
            self.assertIsNone(machines._protocols("http://127.0.0.1:1/v1"))
        with patch.dict(sys.modules, {"endpoints": None}):
            self.assertIsNone(machines._protocols("http://127.0.0.1:1/v1"))
        for flags, harness in (
            (None, "OMP"),
            (ALL, "OMP"),
            ({"chat": False, "responses": True, "messages": True}, "Codex"),
            ({"chat": False, "responses": False, "messages": True}, "Claude Code"),
            ({"chat": False, "responses": False, "messages": False}, "Claude Code"),
            ({"chat": None, "responses": True, "messages": False}, "Codex"),
            ({"chat": None, "responses": False, "messages": False}, "OMP"),
        ):
            self.assertEqual(machines._harness(flags), harness, flags)

    def test_tunnel_saves_unsaved_host_and_links(self):
        self.http({"data": [{"id": "local-agent"}]})
        with (
            patch.object(machines, "run", side_effect=[{"port": 10542}, TOKEN]) as ran,
            patch.object(machines, "forward", return_value=40222) as fwd,
            patch.object(machines, "owned_tunnel", return_value=MagicMock()),
            patch.object(machines, "_protocols", return_value=dict(ALL)) as probed,
            patch.object(machines, "_publish", wraps=machines._publish) as publish,
        ):
            out = machines.tunnel("user@gpu")
        ident = machines.identifier("user@gpu")
        self.assertEqual(
            out,
            dict(
                endpoint="http://127.0.0.1:40222/v1",
                model="local-agent",
                machine="user@gpu",
                connection_id=ident,
            ),
        )
        self.assertEqual(
            [c.args for c in ran.call_args_list],
            [("user@gpu", "start", 240), ("user@gpu", "key")],
        )
        self.assertEqual(machines.labels(), {"user@gpu": "user@gpu"})
        self.assertEqual(
            machines.links()[ident],
            dict(
                host="user@gpu",
                remote_port=10542,
                managed=True,
                kind="workbench-node",
                base_path="/v1",
            ),
        )
        fwd.assert_called_once_with("user@gpu", 10542, "tunnel.json")
        probed.assert_called_once_with("http://127.0.0.1:40222/v1", TOKEN)
        account = publish.call_args.args[0]
        self.assertEqual((account["host"], account["protocols"]), ("user@gpu", ALL))
        self.assertNotIn("remote_port", account)
        self.assertNotIn("kind", account)
        row = account_book.entries()[ident]
        self.assertEqual(
            (
                row["provider"],
                row["endpoint"],
                row["context"],
                row["remote_model"],
                row["harness"],
            ),
            (
                "Attached endpoint",
                "http://127.0.0.1:40222/v1",
                8192,
                "local-agent",
                "OMP",
            ),
        )
        connections.store_key.assert_called_once()
        self.assertEqual(connections.store_key.call_args.args[1], TOKEN)
        get = machines._http.return_value.get
        self.assertEqual(get.call_args.args[0], "http://127.0.0.1:40222/v1/models")
        self.assertEqual(
            get.call_args.kwargs["headers"], {"Authorization": "Bearer " + TOKEN}
        )
        self.assertIs(get.call_args.kwargs["allow_redirects"], False)

    def test_tunnel_keeps_what_a_later_check_stored(self):
        self.http({"data": [{"id": "local-agent"}]})
        ident = machines.identifier("gpu")
        for port in (40222, 40555):
            with (
                patch.object(machines, "run", side_effect=[{"port": 10542}, TOKEN]),
                patch.object(machines, "forward", return_value=port),
                patch.object(machines, "owned_tunnel", return_value=MagicMock()),
                patch.object(machines, "_protocols", return_value=dict(CHAT)) as probed,
            ):
                machines.tunnel("gpu")
            row = account_book.entries()[ident]
            self.assertEqual(
                (row["endpoint"], row["context"], row["host"], row["protocols"]),
                (f"http://127.0.0.1:{port}/v1", 8192, "gpu", CHAT),
            )
            self.assertNotIn("remote_port", row)
            self.assertTrue(account_book.managed(row))
            if port == 40222:
                probed.assert_called_once()
                self.assertNotIn("kind", row)
                checked = dict(row, kind="llamacpp")
                account_book.save(checked, allow_endpoint_change=True)
            else:
                probed.assert_not_called()
                self.assertEqual(row["kind"], "llamacpp")

    def test_tunnel_explains_a_forward_that_dies_or_never_answers(self):
        machines.save("Lab", "box")
        path = machines.identity("box").parent / "tunnel.json"
        path.with_suffix(".log").write_text(
            "ssh: connect to host box port 22: Connection refused\n"
        )
        self.http(error=requests.ConnectionError())
        with (
            patch.object(machines, "run", side_effect=[{"port": 10542}, TOKEN]),
            patch.object(machines, "forward", return_value=40222),
            patch.object(machines, "owned_tunnel", return_value=None),
            patch.object(machines, "disconnect") as closed,
        ):
            with self.assertRaisesRegex(
                machines.Unreachable, "refused the SSH connection"
            ):
                machines.tunnel("box")
            closed.assert_called_once_with("box", "tunnel.json")
        with (
            patch.object(machines, "run", side_effect=[{"port": 10542}, TOKEN]),
            patch.object(machines, "forward", return_value=40222),
            patch.object(machines, "owned_tunnel", return_value=MagicMock()),
            patch.object(machines, "disconnect") as closed,
        ):
            with self.assertRaisesRegex(
                ValueError, "did not answer through the SSH forward"
            ) as caught:
                machines.tunnel("box")
            self.assertNotIsInstance(caught.exception, machines.Unreachable)
            closed.assert_called_once_with("box", "tunnel.json")
            self.assertEqual(machines._http.return_value.get.call_count, 25)
        for reply in (
            ["junk", TOKEN],
            [{"port": "x"}, TOKEN],
            [{"port": 10542}, {"k": 1}],
            [{"port": 10542}, ""],
            [{"port": 10542}, "short"],
            [{"port": 10542}, TOKEN + '"}'],
            [{"port": 10542}, TOKEN + "\n" + TOKEN],
            [{"port": 10542}, "k" * 201],
        ):
            with (
                patch.object(machines, "run", side_effect=reply),
                patch.object(machines, "forward") as fwd,
            ):
                with self.assertRaisesRegex(ValueError, "could not read"):
                    machines.tunnel("box")
                fwd.assert_not_called()
        connections.store_key.assert_not_called()
        self.assertEqual(account_book.entries(), {})
        self.assertEqual(machines.links(), {})

    def test_discover_normalises_rows(self):
        execute = self.found()
        rows = machines.discover("box")
        self.assertEqual(execute.call_args.args[0], "box")
        self.assertEqual(execute.call_args.args[2:], ("python3 -", 30))
        self.assertIsInstance(execute.call_args.args[1], bytes)
        self.assertEqual([r["port"] for r in rows], [11434, 1234])
        self.assertEqual(
            rows[0],
            dict(
                port=11434,
                kind="ollama",
                label="Ollama 0.40",
                models=[
                    {"id": "qwen3:14b", "context": 32768},
                    {"id": "gemma3", "context": None},
                    {"id": "huge", "context": None},
                ],
                protocols=ALL,
                needs_key=False,
                base_path="/v1",
            ),
        )
        self.assertEqual(
            rows[1],
            dict(
                port=1234,
                kind="lmstudio",
                label="",
                models=[],
                protocols=None,
                needs_key=True,
                base_path="/v1",
            ),
        )
        execute.return_value = {
            "v": 1,
            "endpoints": ROWS,
            "hints": ["lmstudio-server-off"],
        }
        self.assertEqual(machines.discover("box"), rows)
        shaped = [
            {
                "port": 8080,
                "kind": "k" * 41,
                "label": "bad\x07label",
                "auth": "required",
                "base_path": "",
            },
            {
                "port": 8000,
                "kind": "vllm",
                "auth": "none",
                "base_path": "/api/v1",
                "protocols": {"chat": True, "responses": None, "messages": False},
            },
        ]
        execute.return_value = shaped
        with patch.object(
            machines, "_label", side_effect=lambda kind: {"vllm": "vLLM"}.get(kind, "")
        ):
            first, second = machines.discover("box")
        self.assertEqual(
            (first["kind"], first["label"], first["needs_key"], first["base_path"]),
            ("", "", True, ""),
        )
        self.assertEqual(
            (second["label"], second["needs_key"], second["base_path"]),
            ("vLLM", False, "/api/v1"),
        )
        self.assertEqual(
            second["protocols"], {"chat": True, "responses": None, "messages": False}
        )
        execute.return_value = [{"port": 2000 + n} for n in range(40)]
        self.assertEqual(len(machines.discover("box")), 32)
        for unreadable in ("nope", 5, None, {"endpoints": "x"}, {}):
            execute.return_value = unreadable
            with self.assertRaisesRegex(
                ValueError, "could not read; use Check connection, then search again"
            ):
                machines.discover("box")
        with fake_endpoints("#!/bin/sh\necho '[]'\n"):
            execute.return_value = []
            self.assertEqual(machines.discover("box"), [])
            self.assertEqual(execute.call_args.args[2], "sh -s")

    def test_label_comes_from_the_provider_table(self):
        table = types.ModuleType("providers")
        table.KINDS = {"ollama": {"label": "Ollama"}, "odd": {"label": 5}}
        self.use(patch.object(machines, "_label", LABEL))
        with patch.dict(sys.modules, {"providers": table}):
            self.assertEqual(machines._label("ollama"), "Ollama")
            self.assertEqual(machines._label("odd"), "")
            self.assertEqual(machines._label("missing"), "")
        with patch.dict(sys.modules, {"providers": None}):
            self.assertEqual(machines._label("ollama"), "")

    def test_attach_forwards_chosen_port_and_saves_row(self):
        self.found()
        self.http({})
        fwd = self.use(patch.object(machines, "forward", return_value=40333))
        with self.assertRaisesRegex(
            ValueError, "No model server is answering on port 8000 of box"
        ):
            machines.attach("box", 8000)
        with self.assertRaisesRegex(
            ValueError, "No model server is answering on port 10542"
        ):
            machines.attach("box", 10542)
        with self.assertRaisesRegex(
            ValueError, "Choose one of the models this server lists"
        ):
            machines.attach("box", 11434, "nope")
        for bad in ("11434", True, 0, 70000, None, 11434.0):
            with self.assertRaisesRegex(ValueError, "Choose one of the servers found"):
                machines.attach("box", bad)
        with self.assertRaisesRegex(
            ValueError, "Type the model ID this server should use"
        ):
            machines.attach("box", 1234)
        for typed in ("bad\nid", "-option"):
            with self.assertRaisesRegex(
                ValueError, "Type the model ID this server should use"
            ):
                machines.attach("box", 1234, typed, key="sk")
        with self.assertRaisesRegex(
            ValueError, "Choose one of the models this server lists"
        ):
            machines.attach("box", 11434, "-rf", key="sk")
        connections.store_key.assert_not_called()
        with self.assertRaisesRegex(ValueError, "SSH config alias"):
            machines.attach("box; rm", 11434)
        fwd.assert_not_called()
        self.assertEqual(machines.labels(), {})

        out = machines.attach("box", 11434, "", "", None)
        fwd.assert_called_once_with("box", 11434, "tunnel-11434.json")
        ident = machines.link_id("box", 11434)
        self.assertEqual(
            out,
            dict(
                connection_id=ident,
                endpoint="http://127.0.0.1:40333/v1",
                model="qwen3:14b",
                name="box · Ollama 0.40",
                machine="box",
                kind="ollama",
                protocols=ALL,
                context=32768,
                needs_key=False,
            ),
        )
        row = account_book.entries()[ident]
        self.assertEqual(
            (
                row["provider"],
                row["context_window"],
                row["context_source"],
                row["remote_model"],
                row["endpoint"],
                row["harness"],
                row["host"],
                row["remote_port"],
                row["kind"],
                row["protocols"],
            ),
            (
                "Attached endpoint",
                32768,
                "server",
                "qwen3:14b",
                "http://127.0.0.1:40333/v1",
                "OMP",
                "box",
                11434,
                "ollama",
                ALL,
            ),
        )
        self.assertNotIn("context", row)
        self.assertFalse(account_book.managed(row))
        connections.store_key.assert_not_called()
        self.assertEqual(machines.labels(), {"box": "box"})
        self.assertEqual(
            machines.links()[ident],
            dict(
                host="box",
                remote_port=11434,
                managed=False,
                kind="ollama",
                base_path="/v1",
            ),
        )
        get = machines._http.return_value.get
        self.assertEqual(get.call_args.args[0], "http://127.0.0.1:40333/v1/models")
        self.assertNotIn("headers", get.call_args.kwargs)
        self.assertIs(get.call_args.kwargs["allow_redirects"], False)

        fwd.reset_mock()
        fwd.return_value = 40444
        waiting = machines.attach("box", 1234, " my-model ")
        fwd.assert_called_once_with("box", 1234, "tunnel-1234.json")
        self.assertEqual(
            (
                waiting["needs_key"],
                waiting["protocols"],
                waiting["name"],
                waiting["context"],
                waiting["model"],
            ),
            (True, CHAT, "box · lmstudio", None, "my-model"),
        )
        connections.store_key.assert_not_called()
        stored = account_book.entries()[waiting["connection_id"]]
        self.assertFalse({"context", "context_window", "context_source"} & set(stored))
        keyed = machines.attach("box", 1234, "my-model", "LM", " sk ")
        self.assertEqual((keyed["needs_key"], keyed["name"]), (False, "LM"))
        connections.store_key.assert_called_once()
        self.assertEqual(connections.store_key.call_args.args[1], "sk")
        again = machines.attach("box", 1234, "my-model", "", "   ")
        self.assertEqual((again["needs_key"], again["name"]), (True, "LM"))
        long = machines.dispatch(
            "machine-attach",
            {"host": " box ", "port": 11434, "name": "n" * 120, "model": "gemma3"},
        )
        self.assertEqual(
            (long["name"], long["model"], long["context"]), ("n" * 90, "gemma3", None)
        )
        stored = account_book.entries()[long["connection_id"]]
        self.assertFalse({"context", "context_window", "context_source"} & set(stored))

    def test_attach_stores_the_context_where_connections_can_change_it(self):
        self.found()
        self.http({})
        self.use(patch.object(machines, "forward", return_value=40333))
        ident = machines.attach("box", 11434)["connection_id"]
        row = account_book.entries()[ident]
        account_book.save(dict(row, context=4096), allow_endpoint_change=True)
        machines.attach("box", 11434)
        row = account_book.entries()[ident]
        self.assertNotIn("context", row)
        typed = account_book.save(dict(row, context_window="16384"))
        self.assertEqual(
            (typed["context_window"], typed["context_source"]), (16384, "user")
        )
        out = machines.attach("box", 11434, "gemma3")
        row = account_book.entries()[ident]
        self.assertEqual(
            (out["context"], row["remote_model"], row["context_window"]),
            (None, "gemma3", 16384),
        )
        self.assertEqual(row["context_source"], "user")
        self.assertEqual(connections.context_for(row, row), (16384, "user"))
        account_book.save(dict(row, context_window=""))
        machines.attach("box", 11434)
        row = account_book.entries()[ident]
        self.assertEqual(
            (row["context_window"], row["context_source"]), (32768, "server")
        )

    def test_ids_and_reply_shape_match_the_endpoints_module(self):
        import endpoints

        self.assertEqual(
            machines.link_id("user@gpu", 8000),
            endpoints.remote_ident("user@gpu", 8000),
        )
        reply = dict(
            v=1,
            hints=[],
            endpoints=[
                dict(
                    base="http://127.0.0.1:8080/v1",
                    port=8080,
                    kind="llamacpp",
                    auth="required",
                    models=[],
                    protocols=dict.fromkeys(ALL),
                ),
                dict(
                    base="http://127.0.0.1:11434/v1",
                    port=11434,
                    kind="ollama",
                    auth="none",
                    models=[{"id": "qwen3:14b", "context": 32768, "loaded": True}],
                    protocols={"chat": True, "responses": True, "messages": None},
                ),
            ],
        )
        machines._label.side_effect = LABEL
        with patch.object(
            machines,
            "_ssh_run",
            return_value=b"motd\n" + json.dumps(reply).encode() + b"\n",
        ) as ran:
            rows = machines.discover("gpu")
        self.assertEqual(
            ran.call_args.args,
            ("gpu", ["python3 -"], endpoints.remote_probe_script().encode(), 30),
        )
        self.assertEqual(
            rows,
            [
                dict(
                    port=8080,
                    kind="llamacpp",
                    label=LABEL("llamacpp"),
                    models=[],
                    protocols=None,
                    needs_key=True,
                    base_path="/v1",
                ),
                dict(
                    port=11434,
                    kind="ollama",
                    label=LABEL("ollama"),
                    models=[{"id": "qwen3:14b", "context": 32768}],
                    protocols={"chat": True, "responses": True, "messages": None},
                    needs_key=False,
                    base_path="/v1",
                ),
            ],
        )
        self.assertTrue(LABEL("ollama"))

    def test_attach_closes_a_new_forward_that_cannot_be_used(self):
        self.found()
        self.use(patch.object(machines, "forward", return_value=40333))
        closed = self.use(patch.object(machines, "disconnect"))
        session = self.http(error=requests.ConnectionError())
        with self.assertRaisesRegex(ValueError, "port 11434 of box stopped answering"):
            machines.attach("box", 11434)
        closed.assert_called_once_with("box", "tunnel-11434.json")
        self.assertEqual((machines.links(), account_book.entries()), ({}, {}))
        session.get.side_effect = None
        closed.reset_mock()
        with patch.object(
            machines,
            "_publish",
            side_effect=RuntimeError("Use a system credential vault."),
        ):
            with self.assertRaisesRegex(RuntimeError, "credential vault"):
                machines.attach("box", 11434, key="sk")
        closed.assert_called_once_with("box", "tunnel-11434.json")
        machines.attach("box", 11434)
        closed.reset_mock()
        with patch.object(
            machines,
            "_publish",
            side_effect=RuntimeError("Use a system credential vault."),
        ):
            with self.assertRaises(RuntimeError):
                machines.attach("box", 11434, key="sk")
        closed.assert_not_called()
        self.assertIn(machines.link_id("box", 11434), machines.links())

    def test_ensure_reopens_and_moves_endpoint(self):
        self.found()
        session = self.http({})
        with patch.object(machines, "forward", return_value=40333):
            ident = machines.attach("box", 11434, name="Lab")["connection_id"]
        with patch.object(machines, "forward", return_value=40999) as fwd:
            self.assertEqual(machines.ensure(ident), "http://127.0.0.1:40999/v1")
            fwd.assert_called_once_with("box", 11434, "tunnel-11434.json")
        row = account_book.entries()[ident]
        self.assertEqual(
            (
                row["endpoint"],
                row["remote_model"],
                row["name"],
                row["context_window"],
                row["context_source"],
                row["harness"],
            ),
            ("http://127.0.0.1:40999/v1", "qwen3:14b", "Lab", 32768, "server", "OMP"),
        )
        with (
            patch.object(machines, "forward", return_value=40999),
            patch.object(machines, "_publish") as publish,
        ):
            self.assertEqual(machines.ensure(ident), "http://127.0.0.1:40999/v1")
            publish.assert_not_called()
        session.get.side_effect = requests.ConnectionError()
        with (
            patch.object(machines, "forward", return_value=40999),
            patch.object(machines, "disconnect") as closed,
        ):
            with self.assertRaisesRegex(
                ValueError, "port 11434 of box is not answering"
            ):
                machines.ensure(ident)
            closed.assert_called_once_with("box", "tunnel-11434.json")
        self.assertIsNone(machines.ensure("0" * 16))
        session.get.side_effect = None
        machines._link(
            "e" * 16,
            dict(host="box", remote_port=8000, managed=False, base_path="@host/x"),
        )
        with patch.object(machines, "forward", return_value=40777):
            self.assertEqual(machines.ensure("e" * 16), "http://127.0.0.1:40777/v1")
        self.assertEqual(
            session.get.call_args.args[0], "http://127.0.0.1:40777/v1/models"
        )
        machines.save("Old", "legacy")
        with patch.object(machines, "tunnel", return_value={"endpoint": "E"}) as opened:
            self.assertEqual(machines.ensure(machines.identifier("legacy")), "E")
            opened.assert_called_once_with("legacy")
            machines._link("f" * 16, dict(host="gpu", remote_port=10542, managed=True))
            self.assertEqual(machines.ensure("f" * 16), "E")
            opened.assert_called_with("gpu")

    def test_detach_and_forwards(self):
        self.found()
        self.http({})
        with patch.object(machines, "forward", return_value=40333):
            ident = machines.attach("box", 11434)["connection_id"]
            other = machines.attach("box", 1234, "m")["connection_id"]
        machines.save("Lab GPU", "box")
        machines._link(
            "f" * 16,
            dict(host="gpu", remote_port=10542, managed=True, kind="workbench-node"),
        )
        folder = machines.identity("box").parent
        machines.write_json(
            folder / "tunnel-11434.json",
            dict(port=40333, remote=11434, pid=1, born=1.0),
        )
        (folder / "tunnel-11434.log").write_text("log")
        with patch.object(
            machines, "identity", side_effect=AssertionError("creates a folder")
        ):
            with patch.object(
                machines, "owned_tunnel", side_effect=lambda rec: rec.get("pid") == 1
            ):
                rows = {
                    row["connection_id"]: row
                    for row in machines.dispatch("machine-forwards", {})
                }
        self.assertEqual(
            rows[ident],
            dict(
                connection_id=ident,
                host="box",
                label="Lab GPU",
                remote_port=11434,
                local_port=40333,
                alive=True,
                managed=False,
                kind="ollama",
            ),
        )
        self.assertEqual(
            (rows[other]["local_port"], rows[other]["alive"]), (None, False)
        )
        self.assertEqual(
            rows["f" * 16],
            dict(
                connection_id="f" * 16,
                host="gpu",
                label="gpu",
                remote_port=10542,
                local_port=None,
                alive=False,
                managed=True,
                kind="workbench-node",
            ),
        )
        self.assertFalse((machines.DATA / "ssh" / machines.identifier("gpu")).exists())
        with (
            patch.object(
                machines.subprocess, "run", side_effect=AssertionError("no ssh")
            ),
            patch.object(
                machines.subprocess, "Popen", side_effect=AssertionError("no ssh")
            ),
        ):
            self.assertIs(
                machines.dispatch("machine-detach", {"connection_id": ident}), True
            )
        self.assertNotIn(ident, machines.links())
        self.assertNotIn(ident, account_book.entries())
        self.assertIn(other, account_book.entries())
        self.assertFalse((folder / "tunnel-11434.json").exists())
        self.assertFalse((folder / "tunnel-11434.log").exists())
        for unknown in ({"connection_id": ident}, {}, {"connection_id": None}):
            with self.assertRaisesRegex(
                ValueError, "does not belong to an SSH machine"
            ):
                machines.dispatch("machine-detach", unknown)

    def test_drop_forward_leaves_the_saved_row(self):
        self.found()
        self.http({})
        with patch.object(machines, "forward", return_value=40333):
            ident = machines.attach("box", 11434)["connection_id"]
        folder = machines.identity("box").parent
        machines.write_json(
            folder / "tunnel-11434.json", dict(port=40333, remote=11434)
        )
        machines.write_json(folder / "tunnel.json", dict(port=40111, remote=10542))
        with patch.object(
            machines.subprocess, "run", side_effect=AssertionError("no ssh")
        ):
            machines.drop_forward("box", 11434)
        self.assertEqual(machines.links(), {})
        self.assertFalse((folder / "tunnel-11434.json").exists())
        self.assertTrue((folder / "tunnel.json").exists())
        self.assertIn(ident, account_book.entries())
        (folder / "tunnel-11434.log").mkdir()
        machines.drop_forward("box", 11434)

    def test_forget_is_local_only(self):
        self.found()
        self.http({})
        with patch.object(machines, "forward", return_value=40333):
            ident = machines.attach("box", 11434)["connection_id"]
            kept = machines.attach("other", 11434)["connection_id"]
        account_book.save(
            dict(
                provider="Attached endpoint",
                name="Lab",
                endpoint="http://127.0.0.1:40111/v1",
                connection_id=machines.identifier("box"),
            )
        )
        machines._link(
            machines.identifier("box"),
            dict(host="box", remote_port=10542, managed=True),
        )
        key = machines.identity("box")
        key.write_text("private")
        key.with_suffix(".pub").write_text("ssh-ed25519 AAAA agent-workbench-x\n")
        machines.write_json(
            key.parent / "tunnel-11434.json", dict(port=40333, remote=11434)
        )
        machines.save("Other", "other")
        with (
            patch.object(
                machines.subprocess, "run", side_effect=AssertionError("no ssh")
            ),
            patch.object(
                machines.subprocess, "Popen", side_effect=AssertionError("no ssh")
            ),
        ):
            out = machines.dispatch("machine-forget", {"host": " box "})
        self.assertEqual(
            out, {"host": "box", "public_key": "ssh-ed25519 AAAA agent-workbench-x"}
        )
        self.assertFalse(key.parent.exists())
        self.assertEqual(machines.labels(), {"other": "Other"})
        self.assertEqual(list(machines.links()), [kept])
        self.assertEqual(list(account_book.entries()), [kept])
        self.assertNotEqual(ident, kept)
        self.assertEqual(machines.forget("box"), {"host": "box", "public_key": ""})
        with patch.object(
            account_book, "remove_node", side_effect=RuntimeError("vault locked")
        ):
            with self.assertRaisesRegex(
                ValueError, "other was forgotten, but a saved connection"
            ):
                machines.forget("other")
        self.assertEqual((machines.labels(), machines.links()), ({}, {}))

    def test_remove_and_unpair_offer_forget(self):
        key = machines.identity("box")
        key.write_text("private")
        key.with_suffix(".pub").write_text("ssh-ed25519 AAAA c\n")
        machines._link(
            machines.identifier("box"),
            dict(host="box", remote_port=10542, managed=True),
        )
        with patch.object(machines, "_ssh_run", side_effect=machines.Unreachable("x")):
            with self.assertRaisesRegex(
                ValueError, "its runtime was not removed.*Forget machine"
            ):
                machines.dispatch("machine-remove", {"host": "box"})
            with self.assertRaisesRegex(
                ValueError, "its key was not revoked.*Forget machine"
            ):
                machines.dispatch("machine-unpair", {"host": "box"})
        self.assertTrue(key.exists() and key.with_suffix(".pub").exists())
        self.assertIn(machines.identifier("box"), machines.links())
        with patch.object(
            machines,
            "_ssh_run",
            side_effect=ValueError("SSH keys changed concurrently. Retry."),
        ):
            with self.assertRaisesRegex(ValueError, "changed concurrently"):
                machines.dispatch("machine-unpair", {"host": "box"})
        self.assertTrue(key.exists())
        with patch.object(
            machines, "_ssh_run", return_value=b'{"installed": false}\n'
        ) as ran:
            self.assertEqual(
                machines.dispatch("machine-remove", {"host": "box"}),
                {"installed": False},
            )
            self.assertEqual(ran.call_args.args[1], ["python3 - remove"])
        self.assertEqual(machines.links(), {})
        with (
            patch.object(machines, "_ssh_run", return_value=b"") as ran,
            patch.object(machines, "disconnect") as closed,
        ):
            self.assertIs(machines.dispatch("machine-unpair", {"host": "box"}), True)
            host, tail, source, timeout = ran.call_args.args
            self.assertEqual((host, source, timeout), ("box", None, 30))
            self.assertTrue(tail[0].startswith("python3 -c "))
            self.assertTrue(tail[0].endswith(" 'ssh-ed25519 AAAA c'"))
            closed.assert_called_once_with("box")
        self.assertFalse(key.exists() or key.with_suffix(".pub").exists())
        with self.assertRaisesRegex(
            ValueError, "uses your own SSH key.*Forget machine"
        ):
            machines.dispatch("machine-unpair", {"host": "box"})

    def test_dispatch_routes_actions(self):
        with (
            patch.object(machines, "run", return_value={"system": "Linux"}) as ran,
            patch.object(machines, "disconnect") as closed,
            patch.object(machines, "detect", return_value="posix") as detected,
        ):
            self.assertEqual(
                machines.dispatch("machine-probe", {"host": "box"}),
                {"system": "Linux", "os": "posix"},
            )
            detected.assert_called_once_with("box")
            ran.assert_called_once_with("box", "probe", 20)
            machines.dispatch("machine-install", {"host": "box"})
            ran.assert_called_with("box", "install", 3600)
            machines.dispatch("machine-stop", {"host": "box"})
            ran.assert_called_with("box", "stop")
            closed.assert_called_once_with("box", "tunnel.json")
        with patch.object(machines, "discover", return_value=[{"port": 1}]) as found:
            self.assertEqual(
                machines.dispatch("machine-discover", {"host": "box "}),
                {"host": "box", "endpoints": [{"port": 1}], "ports": []},
            )
            found.assert_called_once_with("box")
        with patch.object(machines, "tunnel", return_value={"endpoint": "E"}) as opened:
            self.assertEqual(
                machines.dispatch("machine-connect", {"host": "box"}), {"endpoint": "E"}
            )
            opened.assert_called_once_with("box")
        with patch.object(machines, "remote_session") as session:
            self.assertIs(
                machines.dispatch("machine-session", {"host": "box", "folder": 5}), True
            )
            session.assert_called_once_with("box", "5", "OMP", "native")
            absent = dict.fromkeys(("folder", "harness", "mode"))
            machines.dispatch("machine-session", {"host": "box", **absent})
            session.assert_called_with("box", ".", "OMP", "native")
        with patch.object(machines, "attach", return_value={}) as attached:
            machines.dispatch(
                "machine-attach",
                {"host": "box", "port": 11434, "model": None, "name": None},
            )
            attached.assert_called_once_with("box", 11434, "", "", None)
        with (
            patch.object(machines, "terminal") as opened,
            patch.object(machines, "pairing_line", return_value="LINE"),
        ):
            self.assertIs(machines.dispatch("machine-pair", {"host": "box"}), True)
            self.assertEqual(opened.call_args.args[0][-3:], ["-t", "box", "LINE"])
            self.assertNotIn("BatchMode=yes", opened.call_args.args[0])
            self.assertEqual(machines.dispatch("machine-key", {"host": "box"}), "LINE")
        with self.assertRaisesRegex(ValueError, "Unknown machine operation"):
            machines.dispatch("machine-nope", {"host": "box"})

    def test_pairing_line_creates_the_key_without_a_terminal(self):
        def keygen(argv, **kw):
            Path(argv[-1]).write_text("private")
            Path(argv[-1] + ".pub").write_text("ssh-ed25519 AAAAnew c\n")

        with patch.object(machines.subprocess, "run", side_effect=keygen) as ran:
            line = machines.pairing_line("box")
            self.assertEqual(ran.call_args.args[0][0], "ssh-keygen")
            self.assertIs(ran.call_args.kwargs["stdin"], subprocess.DEVNULL)
            self.assertIs(ran.call_args.kwargs["check"], True)
            self.assertEqual(machines.pairing_line("box"), line)
            ran.assert_called_once()
        self.assertEqual(
            line,
            "umask 077; mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && "
            "(grep -qxF 'ssh-ed25519 AAAAnew c' ~/.ssh/authorized_keys || "
            '{ [ -z "$(tail -c1 ~/.ssh/authorized_keys)" ] || echo >> ~/.ssh/authorized_keys; '
            "printf '%s\\n' 'ssh-ed25519 AAAAnew c' >> ~/.ssh/authorized_keys; })",
        )
        with patch.object(machines.subprocess, "run", side_effect=FileNotFoundError()):
            with self.assertRaises(ValueError) as caught:
                machines.pairing_line("other")
            self.assertEqual(str(caught.exception), machines.NO_SSH)

    def test_remote_command_guards(self):
        with self.assertRaisesRegex(ValueError, "Unknown harness"):
            machines.remote_command("box", ".", "Nope", "native")
        with self.assertRaisesRegex(
            ValueError, "no tested sign-in command for opencode"
        ):
            machines.remote_command("box", ".", "opencode", "device")
        for folder in ("a\nb", "a\x7fb", "a\tb", "x" * 1025):
            with self.assertRaisesRegex(ValueError, "single-line remote folder"):
                machines.remote_command("box", folder, "OMP", "native")
        with self.assertRaisesRegex(ValueError, "currently use OMP"):
            machines.remote_command("box", ".", "Pi", "local-model")
        self.assertEqual(machines._remote_dir("~"), '"$HOME"')
        self.assertEqual(machines._remote_dir("~/my proj"), "\"$HOME\"/'my proj'")
        self.assertEqual(machines._remote_dir("~other/x"), "'~other/x'")
        self.assertEqual(machines._remote_dir("$(reboot)"), "'$(reboot)'")
        shell = machines.remote_command("box", "  ", "OMP", "shell")
        self.assertTrue(shell.startswith("exec /bin/sh -c "))
        self.assertIn("cd -- .", shell)
        for harness, cli in machines.REMOTE_CLI.items():
            native = machines.remote_command("box", ".", harness, "native")
            self.assertTrue(native.startswith('exec "$SHELL" -lic '))
            self.assertIn(f"command -v {cli} >/dev/null", native)
            self.assertNotIn("\n", native)
        self.assertEqual(
            sorted(machines.REMOTE_CLI),
            ["Claude Code", "Codex", "Hermes", "OMP", "Pi", "opencode"],
        )
        with patch.object(machines, "terminal") as opened:
            machines.remote_session("box", "~/p", "Codex", "native")
            argv = opened.call_args.args[0]
            self.assertEqual(argv[-3:-1], ["-t", "box"])
            self.assertEqual(
                argv[-1], machines.remote_command("box", "~/p", "Codex", "native")
            )


@unittest.skipIf(os.name == "nt", "needs a POSIX shell")
class RemoteShellTest(Base):
    def remote(self, command, home):
        stub = Path(home) / "stub-shell"
        stub.write_text(
            '#!/bin/sh\nif [ "$1" = "-lic" ]; then exec /bin/sh -c "$2"; fi\n'
            'echo "LOGIN in $(pwd)"\n'
        )
        stub.chmod(0o755)
        return subprocess.run(
            ["/bin/sh", "-c", command],
            env={"HOME": home, "SHELL": str(stub), "PATH": "/usr/bin:/bin"},
            cwd=home,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=10,
        ).stdout

    def test_remote_command_runs(self):
        home = os.path.realpath(self.tmp.name)
        (Path(home) / "my proj").mkdir()
        tools = Path(home) / ".local/bin"
        tools.mkdir(parents=True)
        omp = tools / "omp"
        omp.write_text('#!/bin/sh\necho "OMP in $(pwd) $* [$PI_CODING_AGENT_DIR]"\n')
        omp.chmod(0o755)
        out = self.remote(
            machines.remote_command("box", "~/my proj", "OMP", "device"), home
        )
        self.assertEqual(
            out.strip(), f"OMP in {home}/my proj login openai-codex-device []"
        )
        out = self.remote(machines.remote_command("box", "~", "OMP", "native"), home)
        self.assertEqual(out.strip(), f"OMP in {home}  []")
        out = self.remote(
            machines.remote_command("box", f"{home}/my proj", "OMP", "native"), home
        )
        self.assertEqual(out.strip(), f"OMP in {home}/my proj  []")
        with patch.object(machines, "run") as ran:
            command = machines.remote_command("box", "~/my proj", "OMP", "local-model")
            self.assertEqual(
                [c.args for c in ran.call_args_list],
                [("box", "start", 240), ("box", "omp-config")],
            )
        self.assertEqual(
            self.remote(command, home).strip(),
            f"OMP in {home}/my proj --offline --model workbench/local-agent "
            f"[{home}/.local/share/agent-workbench-node/omp-agent]",
        )
        out = self.remote(
            machines.remote_command("box", "~/missing", "OMP", "native"), home
        )
        self.assertIn("Workbench could not open that folder on this machine", out)
        self.assertIn(f"LOGIN in {home}\n", out)
        self.assertNotIn("OMP in", out)
        out = self.remote(
            machines.remote_command("box", "~/my proj", "Codex", "native"), home
        )
        self.assertIn("Codex (codex) was not found on this machine", out)
        self.assertIn(f"LOGIN in {home}/my proj", out)
        out = self.remote(
            machines.remote_command("box", "~/my proj", "OMP", "shell"), home
        )
        self.assertEqual(out.strip(), f"LOGIN in {home}/my proj")
        out = self.remote(
            machines.remote_command("box", "~/missing", "OMP", "shell"), home
        )
        self.assertIn("could not open that folder", out)
        self.assertEqual(out.count("LOGIN in"), 1)
        marker = Path(home) / "injected"
        self.remote(
            machines.remote_command(
                "box", f"$(touch {marker})'; touch {marker}; '", "OMP", "native"
            ),
            home,
        )
        self.assertFalse(marker.exists())

    def test_pairing_line_terminates_last_line(self):
        home = self.tmp.name
        key = machines.identity("box")
        key.write_text("private")
        key.with_suffix(".pub").write_text("ssh-ed25519 AAAAfake agent-workbench-x\n")
        keys = Path(home) / ".ssh/authorized_keys"
        keys.parent.mkdir()
        keys.write_bytes(b"ssh-rsa AAAA first")
        env = {"HOME": home, "PATH": "/usr/bin:/bin"}
        for _ in range(2):
            subprocess.run(
                ["/bin/sh", "-c", machines.pairing_line("box")],
                env=env,
                check=True,
                timeout=10,
            )
        self.assertEqual(
            keys.read_text(),
            "ssh-rsa AAAA first\nssh-ed25519 AAAAfake agent-workbench-x\n",
        )
        keys.unlink()
        subprocess.run(
            ["/bin/sh", "-c", machines.pairing_line("box")],
            env=env,
            check=True,
            timeout=10,
        )
        self.assertEqual(keys.read_text(), "ssh-ed25519 AAAAfake agent-workbench-x\n")


class Uname:
    def __init__(self, sysname, machine):
        self.sysname, self.machine = sysname, machine


LINUX = Uname("Linux", "x86_64")
AMD_CARDS = list(Path("/sys/class/drm").glob("card*/device/mem_info_vram_total"))


@unittest.skipIf(remote_node is None, "the remote worker is POSIX-only")
class RemoteNodeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = base = Path(self.tmp.name) / "node"
        for item in (
            patch.object(remote_node, "BASE", base),
            patch.object(remote_node, "MARKER", base / "owner-v1"),
            patch.object(remote_node, "LOCK", Path(self.tmp.name) / "lock"),
            patch.object(remote_node.time, "sleep"),
        ):
            item.start()
            self.addCleanup(item.stop)

    def linux(self):
        return patch.object(remote_node.os, "uname", return_value=LINUX)

    def verb(self, name):
        with patch.object(sys, "argv", ["-", name]):
            return remote_node.main()

    def installed(self):
        (self.base / "engine").mkdir(parents=True, exist_ok=True)
        (self.base / "owner-v1").write_text("agent-workbench-node-v1")
        (self.base / "engine/llama-server").write_text("bin")
        (self.base / remote_node.MODEL).write_text("weights")
        (self.base / "api-key").write_text("secret\n")

    def test_source_runs_on_python_38(self):
        source = (ROOT / "remote_node.py").read_text()
        ast.parse(source, feature_version=(3, 8))
        self.assertNotIn("file_digest", source)
        self.assertIn("Requires Python 3.8+ and curl.", source.splitlines()[0])
        allowed = set(sys.stdlib_module_names)
        for node in ast.walk(ast.parse(source)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            names += [node.module] if isinstance(node, ast.ImportFrom) else []
            for name in names:
                self.assertIn(name.split(".")[0], allowed)

    def test_sha256_matches_hashlib(self):
        blob = Path(self.tmp.name) / "blob"
        blob.write_bytes(b"x" * 3_000_000)
        self.assertEqual(
            remote_node.sha256(blob), hashlib.sha256(b"x" * 3_000_000).hexdigest()
        )

    def test_fetch_checks_the_pinned_digest(self):
        dest = Path(self.tmp.name) / "engine.tar.gz"
        good = hashlib.sha256(b"good").hexdigest()

        def curl(argv, **kw):
            Path(argv[argv.index("-o") + 1]).write_bytes(curl.body)

        with patch.object(remote_node.subprocess, "run", side_effect=curl) as ran:
            curl.body = b"bad"
            with self.assertRaisesRegex(
                ValueError, "The download did not match its pinned checksum"
            ):
                remote_node.fetch("https://example.invalid/f", dest, good)
            self.assertFalse(dest.with_suffix(".part").exists() or dest.exists())
            curl.body = b"good"
            remote_node.fetch("https://example.invalid/f", dest, good)
            argv = ran.call_args.args[0]
            self.assertEqual(argv[argv.index("--connect-timeout") + 1], "30")
            self.assertEqual(argv[argv.index("--speed-limit") + 1], "1024")
            self.assertEqual(argv[argv.index("--speed-time") + 1], "60")
            self.assertEqual(argv[-1], "https://example.invalid/f?download=1")
            self.assertEqual(dest.read_bytes(), b"good")
            ran.reset_mock()
            remote_node.fetch("https://example.invalid/f", dest, good)
            ran.assert_not_called()
        other = Path(self.tmp.name) / "model.gguf"
        failed = subprocess.CalledProcessError(6, ["curl", "https://example.invalid"])
        with patch.object(remote_node.subprocess, "run", side_effect=failed):
            with self.assertRaises(ValueError) as caught:
                remote_node.fetch("https://example.invalid/f", other, good)
        self.assertEqual(
            str(caught.exception),
            "The download of model.gguf stopped (curl exit 6); check this machine's "
            "internet connection, then run the install again.",
        )
        with patch.object(remote_node.subprocess, "run", side_effect=curl):
            with self.assertRaisesRegex(
                ValueError, "engine.tar.gz on this machine does not match"
            ):
                remote_node.fetch("https://example.invalid/f", dest, "0" * 64)

    def test_extract_fallback_checks_members(self):
        root = Path(self.tmp.name)
        (root / "src/pkg").mkdir(parents=True)
        (root / "src/pkg/llama-server").write_text("bin")
        os.symlink("llama-server", root / "src/pkg/link")
        good = root / "good.tar.gz"
        with tarfile.open(good, "w:gz") as tar:
            tar.add(root / "src/pkg", "pkg")
        escape = root / "escape.tar"
        with tarfile.open(escape, "w") as tar:
            tar.addfile(tarfile.TarInfo("../escape.txt"))
        absolute = root / "absolute.tar"
        with tarfile.open(absolute, "w") as tar:
            info = tarfile.TarInfo("pkg/out")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
        device = root / "device.tar"
        with tarfile.open(device, "w") as tar:
            info = tarfile.TarInfo("pkg/dev")
            info.type = tarfile.CHRTYPE
            tar.addfile(info)
        old = patch.object(remote_node, "hasattr", lambda *_: False, create=True)
        with old, patch.object(tarfile.TarFile, "extractall", autospec=True) as unpack:
            remote_node.extract(good, root / "out")
            self.assertEqual(unpack.call_args.args[1], root / "out")
            self.assertEqual(
                sorted(m.name for m in unpack.call_args.args[2]),
                ["pkg", "pkg/link", "pkg/llama-server"],
            )
            self.assertNotIn("filter", unpack.call_args.kwargs)
            unpack.reset_mock()
            for bad in (escape, absolute, device):
                with self.assertRaisesRegex(ValueError, "could not be unpacked safely"):
                    remote_node.extract(bad, root / "bad")
            unpack.assert_not_called()
        with old, warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            remote_node.extract(good, root / "plain")
        self.assertEqual((root / "plain/pkg/link").read_text(), "bin")
        self.assertTrue((root / "plain/pkg/link").is_symlink())
        self.assertFalse((root / "escape.txt").exists() or (root / "bad").exists())
        if hasattr(tarfile, "data_filter"):
            with patch.object(tarfile.TarFile, "extractall", autospec=True) as unpack:
                remote_node.extract(good, root / "filtered")
                self.assertEqual(unpack.call_args.kwargs, {"filter": "data"})

    def test_probe_on_a_mac(self):
        with (
            patch.object(
                remote_node.os, "uname", return_value=Uname("Darwin", "arm64")
            ),
            patch.object(remote_node, "memory", return_value=48.0),
            patch.dict(os.environ, {"SSH_CONNECTION": ""}),
        ):
            info = remote_node.probe()
            self.assertEqual(
                (info["system"], info["arch"], info["managed"]),
                ("Darwin", "arm64", False),
            )
            self.assertIn("needs a Linux x86_64 machine", info["reason"])
            self.assertEqual(
                (info["gpu_gib"], info["gpu_vendor"], info["unified"]),
                (48.0, "apple", True),
            )
            self.assertRegex(info["python"], r"^3\.\d+\.\d+$")
            self.assertEqual(
                (info["installed"], info["running"], info["port"]),
                (False, False, 10542),
            )
            for key in ("curl", "tmux", "omp"):
                self.assertIsInstance(info[key], bool)
            self.assertEqual(info["firewall"], "")
            self.assertIs(self.verb("probe")["serving"], False)
            for verb in (remote_node.install, remote_node.start):
                with self.assertRaises(ValueError) as caught:
                    verb()
                self.assertEqual(str(caught.exception), info["reason"])
        self.assertFalse(self.base.exists())
        with patch.object(
            remote_node.os, "uname", return_value=Uname("Darwin", "x86_64")
        ):
            self.assertEqual(remote_node.gpu(16.0), ("", 0.0, False))
        with patch.object(
            remote_node.os, "uname", return_value=Uname("Linux", "aarch64")
        ):
            self.assertIn("Linux x86_64", remote_node.unsupported())
        with self.linux(), patch.object(remote_node, "memory", return_value=31.3):
            info = remote_node.probe()
            self.assertEqual(
                (info["managed"], info["reason"], info["ram_gib"]), (True, "", 31.3)
            )

    def test_memory_never_raises(self):
        self.assertIsInstance(remote_node.memory(), float)
        with self.linux(), patch.object(remote_node, "Path", side_effect=OSError):
            self.assertEqual(remote_node.memory(), 0.0)

        class Sysctl:
            stdout = "51539607552\n"

        darwin = patch.object(
            remote_node.os, "uname", return_value=Uname("Darwin", "arm64")
        )
        with (
            darwin,
            patch.object(remote_node.subprocess, "run", return_value=Sysctl()) as ran,
        ):
            self.assertEqual(remote_node.memory(), 48.0)
            self.assertEqual(
                ran.call_args.args[0], ["/usr/sbin/sysctl", "-n", "hw.memsize"]
            )
        slow = subprocess.TimeoutExpired("sysctl", 3)
        with darwin, patch.object(remote_node.subprocess, "run", side_effect=slow):
            self.assertEqual(remote_node.memory(), 0.0)

    @unittest.skipIf(AMD_CARDS, "this host reports amdgpu memory through sysfs")
    def test_gpu_falls_back_to_nvidia_smi(self):
        class Done:
            stdout = "24564\n11264\n"

        with (
            self.linux(),
            patch.object(
                remote_node.shutil, "which", return_value="/usr/bin/nvidia-smi"
            ),
            patch.object(remote_node.subprocess, "run", return_value=Done()) as ran,
        ):
            self.assertEqual(remote_node.gpu(64.0), ("nvidia", 24.0, False))
            self.assertEqual(
                ran.call_args.args[0],
                [
                    "/usr/bin/nvidia-smi",
                    "--query-gpu=memory.total",
                    "--format=csv,noheader,nounits",
                ],
            )
            Done.stdout = "N/A\n"
            self.assertEqual(remote_node.gpu(64.0), ("", 0.0, False))
        with self.linux(), patch.object(remote_node.shutil, "which", return_value=None):
            self.assertEqual(remote_node.gpu(64.0), ("", 0.0, False))

    def test_nothing_installed(self):
        with self.assertRaisesRegex(
            ValueError, "runtime is not installed on this machine"
        ):
            remote_node.owned()
        with self.assertRaisesRegex(
            ValueError, "runtime is not installed on this machine"
        ):
            self.verb("key")
        self.assertIs(remote_node.stop()["installed"], False)
        self.assertIs(self.verb("stop")["installed"], False)
        self.assertIs(self.verb("remove")["installed"], False)
        self.base.mkdir()
        (self.base / "theirs.txt").write_text("keep")
        with self.assertRaisesRegex(
            ValueError, "was not created by Workbench; move it away"
        ):
            remote_node.owned()
        for verb in ("remove", "stop"):
            with self.assertRaisesRegex(ValueError, "was not created by Workbench"):
                self.verb(verb)
        self.assertEqual((self.base / "theirs.txt").read_text(), "keep")
        (self.base / "owner-v1").write_text("agent-workbench-node-v1")
        self.assertIs(self.verb("remove")["installed"], False)
        self.assertFalse(self.base.exists())
        with self.assertRaisesRegex(ValueError, "Unknown node action"):
            self.verb("nope")

    def test_partial_install_is_reported(self):
        self.base.mkdir()
        (self.base / "owner-v1").write_text("agent-workbench-node-v1")
        with self.linux(), patch.object(remote_node.subprocess, "Popen") as spawned:
            for call in (
                remote_node.start,
                lambda: self.verb("key"),
                lambda: self.verb("omp-config"),
            ):
                with self.assertRaisesRegex(
                    ValueError, "not completely installed on this machine; install"
                ):
                    call()
            spawned.assert_not_called()
        self.assertFalse((self.base / "omp-agent").exists())
        self.installed()
        self.assertEqual(self.verb("key"), "secret")

    def test_start_reports_a_busy_port(self):
        self.installed()
        with self.linux(), patch.object(remote_node.socket, "socket") as made:
            made.return_value.__enter__.return_value.bind.side_effect = OSError(
                98, "in use"
            )
            with self.assertRaisesRegex(
                ValueError, "Port 10542 on this machine is already used"
            ):
                remote_node.start()
        self.assertFalse((self.base / "pid.json").exists())

    def test_lock_reports_busy(self):
        held = remote_node.locked(remote_node.LOCK, 0.1)
        try:
            with self.assertRaises(ValueError) as caught:
                remote_node.locked(remote_node.LOCK, 0.0)
            self.assertEqual(str(caught.exception), remote_node.BUSY)
            ticks = iter([0.0, 0.0, 30.0])
            with patch.object(
                remote_node.time, "monotonic", side_effect=lambda: next(ticks, 61.0)
            ):
                with self.assertRaisesRegex(ValueError, "Another Workbench operation"):
                    self.verb("stop")
            self.assertEqual(remote_node.time.sleep.call_args.args, (0.5,))
            self.assertIn("system", self.verb("probe"))
        finally:
            held.close()
        with remote_node.locked(remote_node.LOCK, 0.0):
            pass
        self.assertIs(self.verb("stop")["installed"], False)

    def test_firewall_hint(self):
        def only(tool):
            return lambda name: "/usr/bin/" + name if name == tool else None

        def peer(value):
            return patch.dict(os.environ, {"SSH_CONNECTION": value})

        with patch.object(
            remote_node.shutil, "which", side_effect=only("firewall-cmd")
        ):
            with peer("192.168.1.20 50000 192.168.1.9 22"):
                hint = remote_node.firewall_hint()
                self.assertTrue(
                    hint.startswith(
                        'sudo firewall-cmd --permanent --add-rich-rule=\'rule family="ipv4"'
                    )
                )
                self.assertTrue(hint.endswith("' && sudo firewall-cmd --reload"))
                self.assertIn('source address="192.168.1.20/32" port port="22"', hint)
            for value in (
                "127.0.0.1 50000 127.0.0.1 22",
                "::1 50000 ::1 22",
                "bogus 1 2 x",
                "1.2.3.4 5 6 x",
                "1.2.3.4 5 6",
                "",
            ):
                with peer(value):
                    self.assertEqual(remote_node.firewall_hint(), "", value)
        with patch.object(remote_node.shutil, "which", side_effect=only("ufw")):
            with peer("fd00::5 50000 fd00::9 2222"):
                self.assertEqual(
                    remote_node.firewall_hint(),
                    "sudo ufw allow from fd00::5/128 to any port 2222 proto tcp",
                )
            with peer("fe80::1c2d%eth0;id 50000 fe80::9%eth0 22"):
                self.assertEqual(
                    remote_node.firewall_hint(),
                    "sudo ufw allow from fe80::1c2d/128 to any port 22 proto tcp",
                )
        with patch.object(remote_node.shutil, "which", return_value=None):
            with peer("192.168.1.20 50000 192.168.1.9 22"):
                self.assertEqual(remote_node.firewall_hint(), "")

    def test_install_needs_curl(self):
        with self.linux(), patch.object(remote_node.shutil, "which", return_value=None):
            with self.assertRaisesRegex(
                ValueError, "curl was not found on this machine"
            ):
                remote_node.install()
        self.assertFalse(self.base.exists())
        with (
            self.linux(),
            patch.object(remote_node.shutil, "which", return_value="/usr/bin/curl"),
            patch.object(
                remote_node.shutil, "disk_usage", return_value=MagicMock(free=1024**3)
            ),
            patch.object(remote_node, "fetch") as fetched,
        ):
            with self.assertRaisesRegex(ValueError, "needs 13 GiB of free disk space"):
                remote_node.install()
            fetched.assert_not_called()


if __name__ == "__main__":
    unittest.main()
