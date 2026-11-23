"""remote_workbench.py run as SSH sends it (python3 - on stdin) against real listening processes."""

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "remote_workbench.py").read_bytes()
LISTENER = """import socket, sys, time
s = socket.socket()
s.bind(("127.0.0.1", 0))
s.listen(1)
print(s.getsockname()[1], flush=True)
time.sleep(60)
"""


def ask(mode, ports):
    out = subprocess.run(
        [sys.executable, "-", mode, ",".join(map(str, ports))],
        input=SOURCE,
        capture_output=True,
        timeout=60,
        check=True,
    ).stdout
    return json.loads(out.decode().strip().splitlines()[-1])


@unittest.skipIf(os.name == "nt", "the Windows form is PowerShell in remote_win.py")
class RemoteWorkbenchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # A space in the folder, as in ~/Library/Application Support/Agent Workbench.
        self.data = Path(tmp.name) / "Agent Workbench"
        self.data.mkdir()
        self.secret = secrets.token_urlsafe(32)
        (self.data / "api-key.txt").write_text(self.secret + "\n")
        script = Path(tmp.name) / "listen.py"
        script.write_text(LISTENER)
        self.flag = "--api-key-file", str(self.data / "api-key.txt")
        self.child = subprocess.Popen(
            [sys.executable, str(script), *self.flag],
            stdout=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )
        self.addCleanup(self.child.wait, 10)
        self.addCleanup(self.child.kill)
        self.port = int(self.child.stdout.readline())
        self.child.stdout.close()

    def running(self, **fields):
        rec = dict(pid=self.child.pid, port=self.port, engine="llama.cpp")
        rec["spec"] = {"context": 16384}
        rec.update(fields)
        (self.data / "running.json").write_text(json.dumps(rec))

    def test_the_server_named_by_its_running_record_gives_alias_context_and_key(self):
        self.running()
        self.assertEqual(
            ask("info", [self.port, 1]),
            {
                "servers": [
                    dict(port=self.port, model="local-agent", context=16384, engine="llama.cpp")
                ]
            },
        )
        self.assertEqual(ask("key", [self.port]), {"key": self.secret})

    def test_the_parent_of_the_listener_may_be_the_recorded_process(self):
        # On Windows a venv's python.exe starts the interpreter that listens as its child.
        self.running(pid=os.getpid(), engine="Strata")
        self.assertEqual(ask("info", [self.port])["servers"][0]["engine"], "Strata")
        self.assertEqual(ask("key", [self.port]), {"key": self.secret})

    def test_a_record_for_another_process_or_port_gives_nothing(self):
        for fields in (dict(pid=1), dict(port=self.port + 1), dict(pid="x")):
            with self.subTest(fields=fields):
                self.running(**fields)
                self.assertEqual(ask("info", [self.port]), {"servers": []})
                self.assertEqual(ask("key", [self.port]), {"key": None})

    def test_a_key_file_that_is_not_a_key_is_not_sent(self):
        self.running()
        (self.data / "api-key.txt").write_text("not a key\n")
        self.assertEqual(ask("key", [self.port]), {"key": None})

    def test_bad_arguments_are_refused(self):
        for argv in (["drop", "1"], ["key", "x"], ["info"]):
            with self.subTest(argv=argv):
                done = subprocess.run(
                    [sys.executable, "-", *argv],
                    input=SOURCE,
                    capture_output=True,
                    timeout=60,
                )
                self.assertNotEqual(done.returncode, 0)
                self.assertEqual(done.stdout, b"")


if __name__ == "__main__":
    unittest.main()
