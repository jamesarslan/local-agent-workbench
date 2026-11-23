"""Windows SSH machines: PowerShell quoting and scripts, the Windows branches of machines.py
and the Windows managed-model worker. PowerShell itself is not needed to run these."""

import ast
import atexit
import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

if "runtime" not in sys.modules and "WORKBENCH_DATA" not in os.environ:
    os.environ["WORKBENCH_DATA"] = tempfile.mkdtemp(prefix="workbench-test-")
    atexit.register(shutil.rmtree, os.environ["WORKBENCH_DATA"], ignore_errors=True)

import account_book  # noqa: E402
import connections  # noqa: E402
import endpoints  # noqa: E402
import local_permissions  # noqa: E402
import machines  # noqa: E402
import remote_node_win  # noqa: E402
import remote_win  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
QUOTES = "'\u2018\u2019\u201a\u201b"
HOSTILE = [
    "plain",
    "with space",
    "it's",
    "‘smart’ ‚quotes‛",
    "$(Remove-Item C:\\ -Recurse)",
    "$env:USERPROFILE",
    "`n backtick `$x",
    "a; Stop-Computer; b",
    "a & calc & b",
    "a | Out-File x",
    '"double" quotes',
    "%OS% %PATH%",
    "[1] *wild?",
    "Ünïcødé 名前 😀",
    "C:\\Users\\me\\It's $x `y; & ü’q",
    "'",
    "''",
    "",
]


def unquote(literal):
    """Read one PowerShell single-quoted string the way the tokenizer does; fail on anything after it."""
    assert literal[0] in QUOTES, literal
    out, i = [], 1
    while i < len(literal):
        c = literal[i]
        if c in QUOTES:
            if i + 1 < len(literal) and literal[i + 1] in QUOTES:
                out.append(literal[i + 1])
                i += 2
                continue
            assert i == len(literal) - 1, "text after the closing quote: " + literal[i:]
            return "".join(out)
        out.append(c)
        i += 1
    raise AssertionError("unterminated: " + literal)


def decode_encoded(command):
    return base64.b64decode(command.rsplit(" ", 1)[1]).decode("utf-16-le")


def decode_feed(data):
    line = data.decode("ascii")
    assert line.endswith("\n") and line.count("\n") == 1
    payload = line.split("FromBase64String('", 1)[1].split("')", 1)[0]
    return base64.b64decode(payload).decode("utf8")


class QuotingTest(unittest.TestCase):
    def test_quote_round_trips_hostile_text(self):
        for text in HOSTILE:
            with self.subTest(text=text):
                literal = remote_win.quote(text)
                self.assertEqual(unquote(literal), text)

    def test_quote_refuses_control_characters(self):
        for text in ("a\nb", "a\rb", "a\x00b", "a\x1bb"):
            with self.assertRaisesRegex(ValueError, "single-line"):
                remote_win.quote(text)

    def test_folder_expression_expands_only_a_leading_tilde(self):
        self.assertEqual(remote_win.folder_expr("~"), "$HOME")
        self.assertEqual(
            remote_win.folder_expr("~/my proj"), "(Join-Path $HOME 'my proj')"
        )
        self.assertEqual(remote_win.folder_expr("~\\a\\b"), "(Join-Path $HOME 'a\\b')")
        self.assertEqual(remote_win.folder_expr("~other"), "'~other'")
        self.assertEqual(remote_win.folder_expr("$HOME"), "'$HOME'")
        self.assertEqual(remote_win.folder_expr("C:\\w"), "'C:\\w'")

    def test_terminal_keeps_folder_and_arguments_as_data(self):
        for folder in HOSTILE[1:]:
            with self.subTest(folder=folder):
                command = remote_win.terminal(
                    "pwsh", folder, "Codex", "codex", ["login", "--device-auth"]
                )
                self.assertTrue(
                    command.startswith(
                        "pwsh -NoLogo -NoExit -ExecutionPolicy Bypass -EncodedCommand "
                    )
                )
                self.assertRegex(command.rsplit(" ", 1)[1], r"^[A-Za-z0-9+/=]+$")
                script = decode_encoded(command)
                lines = script.splitlines()
                self.assertEqual(lines[0], "$ErrorActionPreference = 'Continue'")
                cd = lines[1]
                expr = cd.split("Set-Location -LiteralPath ", 1)[1].split(
                    " -ErrorAction Stop", 1
                )[0]
                self.assertEqual(unquote(expr), folder)
                self.assertIn("Get-Command -Name 'codex'", lines[2])
                self.assertEqual(
                    lines[3], "& 'codex' 'login' '--device-auth'; exit $LASTEXITCODE"
                )
                self.assertEqual(len(lines), 4)

    def test_terminal_shapes(self):
        shell = decode_encoded(remote_win.terminal("powershell", "~", "OMP", None, []))
        self.assertTrue(
            remote_win.terminal("powershell", "~", "OMP", None, []).startswith(
                "powershell -NoLogo -NoExit "
            )
        )
        self.assertIn("Set-Location -LiteralPath $HOME -ErrorAction Stop", shell)
        self.assertNotIn("Get-Command", shell)
        self.assertIn("Set-Location -LiteralPath $HOME; return }", shell)
        managed = decode_encoded(
            remote_win.terminal(None, ".", "OMP", "omp", ["--offline"], managed=True)
        )
        self.assertIn(
            "$env:PI_CODING_AGENT_DIR = Join-Path $env:LOCALAPPDATA 'agent-workbench-node\\omp-agent'",
            managed,
        )
        self.assertIn("OMP (omp) was not found on this machine", managed)
        long = remote_win.terminal("pwsh", "x" * 1024, "Claude Code", "claude", [])
        self.assertLess(len(long), 8000, "cmd.exe limits a command line to 8191 characters")

    def test_feeds_are_one_ascii_line(self):
        for data in (
            remote_win.node("start"),
            remote_win.node("install", "eyJ4IjoxfQ=="),
            remote_win.check(),
            remote_win.probe(endpoints.remote_probe_table()),
        ):
            script = decode_feed(data)
            self.assertIn("$NoPython", script) if "Find-Python" in script else None
        start = decode_feed(remote_win.node("start"))
        self.assertTrue(start.startswith("$Action = 'start'\n$Arg = ''\n"))
        source = start.split("$NodeSource = '", 1)[1].split("'", 1)[0]
        self.assertEqual(base64.b64decode(source).decode(), remote_win.node_source())
        probe = decode_feed(remote_win.probe(endpoints.remote_probe_table([8080], [1])))
        table = json.loads(unquote(probe.split("$Table = ", 1)[1].splitlines()[0]))
        self.assertEqual(table[:2], [[8080], [1]])
        self.assertEqual(table[3], json.loads(json.dumps(endpoints.remote_probe_table()[3])))
        self.assertIn("function ToJson", probe)

    def test_sources_sent_to_windows_are_ascii(self):
        for name in ("remote_probe.ps1", "remote_node_win.py", "chat_templates.py"):
            (ROOT / name).read_bytes().decode("ascii")
        for script in (
            remote_win.JSON,
            remote_win.FIND_PYTHON,
            remote_win.CHECK,
            remote_win.WORKBENCH,
        ):
            script.encode("ascii")

    def test_workbench_server_query_names_its_mode_and_ports(self):
        script = decode_feed(remote_win.workbench("info", [10532, 8081]))
        head = script.splitlines()[:3]
        self.assertEqual(
            [unquote(line.split(" = ", 1)[1]) for line in head],
            ["info", "10532,8081", "local-agent"],
        )
        self.assertIn("function ToJson", script)
        self.assertIn("function Find-Server", script)
        # The key is read only next to a running.json whose process owns the listening port.
        self.assertIn("$owners.ContainsKey([int]$rec.pid)", script)
        self.assertIn("[int]$rec.port -eq $port", script)
        self.assertTrue(decode_feed(remote_win.workbench("key", [10532])).startswith("$Mode = 'key'"))
        for bad in ("delete", ""):
            with self.assertRaises(ValueError):
                remote_win.workbench(bad, [10532])
        with self.assertRaises(ValueError):
            remote_win.workbench("info", ["10532; rm"])

    def test_python_and_powershell_sweeps_share_one_table(self):
        python = endpoints.remote_probe_script([1, 2], [3])
        self.assertIn(json.dumps(endpoints.remote_probe_table([1, 2], [3])), python)
        ps = (ROOT / "remote_probe.ps1").read_text()
        for name in ("Http", "Identify", "Models", "Protocols", "ProbeBase", "Sweep"):
            self.assertIn("function " + name, ps)
        self.assertNotIn("Invoke-WebRequest", ps)

    def test_pairing_and_revoke_scripts_quote_the_key(self):
        key = "ssh-ed25519 AAAAC3Nz agent-workbench-'x'"
        script = decode_encoded(remote_win.pairing_line(key))
        self.assertEqual(unquote(script.splitlines()[0].split(" = ", 1)[1]), key)
        self.assertIn("administrators_authorized_keys", script)
        revoke = remote_win.revoke_script(key)
        self.assertEqual(unquote(revoke.splitlines()[0].split(" = ", 1)[1]), key)

    def test_detect_reads_both_windows_shells(self):
        self.assertTrue(remote_win.is_windows("Windows_NT $env:OS\r\n"))
        self.assertTrue(remote_win.is_windows("%OS%\r\nWindows_NT\r\n"))
        self.assertFalse(remote_win.is_windows("%OS% :OS\n"))
        self.assertFalse(remote_win.is_windows(""))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        data = Path(self.tmp.name)
        for item in (
            patch.object(machines, "DATA", data),
            patch.object(machines, "HOSTS", data / "machines.json"),
            patch.object(machines, "LINKS", data / "machine-links.json"),
            patch.object(machines, "SYSTEMS", data / "machine-systems.json"),
            patch.object(account_book, "DATA", data),
            patch.object(local_permissions, "protect"),
            patch.object(connections, "store_key"),
            patch.object(connections, "forget_key"),
            patch.object(connections, "secret", return_value=""),
            patch.object(machines.time, "sleep"),
        ):
            item.start()
            self.addCleanup(item.stop)

    def use(self, item):
        value = item.start()
        self.addCleanup(item.stop)
        return value

    def windows_box(self, shell="pwsh"):
        machines.save("Win", "me@win")
        machines._note_system("me@win", os="windows", shell=shell)


INFO = dict(
    os="windows",
    shell="pwsh",
    python="3.12.10",
    gpu_vendor="nvidia",
    gpu_gib=31.8,
    ram_gib=61.4,
    managed=True,
)


class WindowsMachineTest(Base):
    def test_detect_stores_the_system(self):
        replies = iter([b"Windows_NT $env:OS\r\n", b"%OS% :OS\n"])
        run = self.use(
            patch.object(machines, "_ssh_run", side_effect=lambda *a: next(replies))
        )
        self.assertEqual(machines.detect("me@win"), "windows")
        self.assertEqual(run.call_args.args[:2], ("me@win", ["echo %OS% $env:OS"]))
        self.assertEqual(machines.detect("me@linux"), "posix")
        self.assertTrue(machines.windows("me@win"))
        self.assertFalse(machines.windows("me@linux"))
        self.assertFalse(machines.windows("me@never"))
        run.side_effect = ValueError("csh: Undefined variable")
        self.assertEqual(machines.detect("me@csh"), "posix")
        run.side_effect = machines.Unreachable("down")
        with self.assertRaises(machines.Unreachable):
            machines.detect("me@down")
        self.assertNotIn("me@down", machines.systems())

    def test_run_and_discover_use_powershell_on_windows(self):
        self.windows_box()
        execute = self.use(patch.object(machines, "execute", return_value={"ok": 1}))
        machines.run("me@win", "install", 3600, "PLAN")
        host, data, command, timeout = execute.call_args.args
        self.assertEqual((host, command, timeout), ("me@win", remote_win.POWERSHELL, 3600))
        script = decode_feed(data)
        self.assertTrue(script.startswith("$Action = 'install'\n$Arg = 'PLAN'\n"))
        execute.return_value = {"endpoints": [{"port": 8080, "kind": "llamacpp"}]}
        rows = machines.discover("me@win")
        self.assertEqual([r["port"] for r in rows], [8080])
        self.assertEqual(execute.call_args.args[2], remote_win.POWERSHELL)
        self.assertIn("function Sweep", decode_feed(execute.call_args.args[1]))

    def test_a_workbench_server_on_windows_is_asked_through_powershell(self):
        self.windows_box()
        secret = "w" * 43
        locked = dict(port=10532, kind="strata", models=[], protocols=None, needs_key=True)
        sent = []

        def execute(host, data, command, timeout=90):
            self.assertEqual(command, remote_win.POWERSHELL)
            script = decode_feed(data)
            sent.append(script.splitlines()[0])
            if "function Sweep" in script:
                return {"endpoints": [locked]}
            if script.startswith("$Mode = 'info'"):
                return {"servers": [{"port": 10532, "model": "local-agent", "context": 65536, "engine": "Strata"}]}
            return {"key": secret}

        self.use(patch.object(machines, "execute", side_effect=execute))
        rows = machines.discover("me@win")
        self.assertEqual(rows[0]["models"], [{"id": "local-agent", "context": 65536}])
        self.assertFalse(rows[0]["needs_key"])
        self.assertEqual(machines.workbench_key("me@win", 10532), secret)
        self.assertEqual(sent[1:], ["$Mode = 'info'", "$Mode = 'key'"])

    def test_extra_ports_are_checked_saved_and_swept(self):
        import providers

        machines.save("Lin", "me@lin")
        self.assertEqual(machines.set_ports("me@lin", "8081, 9000 8081"), [8081, 9000])
        for bad in ("80", "10542", "70000", "x", list(range(2000, 2009))):
            with self.assertRaises(ValueError):
                machines.set_ports("me@lin", bad)
        self.assertEqual(machines.extra_ports("me@lin"), [8081, 9000])
        execute = self.use(patch.object(machines, "execute", return_value={"endpoints": []}))
        found = machines.dispatch("machine-discover", {"host": "me@lin", "ports": "8081"})
        self.assertEqual(found["ports"], [8081])
        script = execute.call_args.args[1].decode()
        self.assertIn(
            json.dumps([*providers.SWEEP_PORTS, providers.WORKBENCH_PORT, 8081]), script
        )
        self.windows_box()
        machines.set_ports("me@win", [8081])
        machines.discover("me@win")
        probe = decode_feed(execute.call_args.args[1])
        table = json.loads(unquote(probe.split("$Table = ", 1)[1].splitlines()[0]))
        self.assertEqual(table[0], [*providers.SWEEP_PORTS, providers.WORKBENCH_PORT, 8081])

    def test_an_unchecked_windows_machine_is_detected_when_posix_fails(self):
        machines.save("Win", "me@win")
        calls = []

        def execute(host, data, command, timeout=90):
            calls.append(command)
            if command != remote_win.POWERSHELL:
                raise ValueError("'python3' is not recognized as an internal or external command")
            return {"running": False}

        self.use(patch.object(machines, "execute", side_effect=execute))
        detect = self.use(
            patch.object(machines, "detect", side_effect=lambda h: machines._note_system(h, os="windows") or "windows")
        )
        self.assertEqual(machines.run("me@win", "probe", 20), {"running": False})
        self.assertEqual(calls, ["python3 - probe", remote_win.POWERSHELL])
        detect.assert_called_once_with("me@win")
        machines.run("me@win", "probe", 20)
        self.assertEqual(calls[-1], remote_win.POWERSHELL)
        detect.assert_called_once()
        # A POSIX machine that was checked before is not detected again on failure.
        machines._note_system("me@linux", os="posix")
        with self.assertRaisesRegex(ValueError, "boom"):
            calls.clear()
            self.use(patch.object(machines, "execute", side_effect=ValueError("boom")))
            machines.run("me@linux", "probe", 20)
        detect.assert_called_once()

    def test_check_records_shell_python_and_the_plan(self):
        machines.save("Win", "me@win")
        self.use(patch.object(machines, "detect", return_value="windows"))
        self.use(patch.object(machines, "execute", return_value=dict(INFO)))
        info = machines.check("me@win")
        self.assertEqual(
            info["plan"],
            dict(
                label="Inkling 2 Mini UD-Q8_K_XL",
                name="Inkling-2-Mini-UD-Q8_K_XL.gguf_file",
                bytes=22867452896,
                context=32768,
            ),
        )
        row = machines.system("me@win")
        self.assertEqual((row["os"], row["shell"], row["python"]), ("windows", "pwsh", "3.12.10"))
        self.assertEqual(
            machines.dispatch("machine-systems", {})["me@win"]["shell"], "pwsh"
        )

    def test_plan_follows_the_catalog_by_gpu_memory(self):
        def label(gib, vendor="nvidia"):
            info = dict(INFO, gpu_gib=gib, gpu_vendor=vendor)
            return machines._windows_plan(info)["model"]["label"]

        self.assertEqual(label(31.8), "Inkling 2 Mini UD-Q8_K_XL")
        self.assertEqual(label(23.99), "Inkling 2 Mini UD-Q4_K_XL")
        self.assertEqual(label(15.9), "Inkling 2 Mini UD-IQ3_XXS")
        for gib, vendor in ((11.9, "nvidia"), (31.8, "amd"), (0, "nvidia")):
            with self.assertRaisesRegex(ValueError, "needs an NVIDIA GPU with at least 16 GB"):
                label(gib, vendor)
        import setup_engines

        doc = machines._windows_plan(dict(INFO), "C:\\m.gguf")
        pinned = setup_engines.PINNED[("windows", "cuda")]
        self.assertEqual([e["sha256"] for e in doc["engine"]], [a.sha256 for a in pinned])
        self.assertTrue(doc["engine"][0]["url"].endswith("/b12633/" + pinned[0].name))
        self.assertTrue(doc["model"]["url"].startswith("https://huggingface.co/unsloth/Inkling-2-Mini-GGUF/resolve/"))
        self.assertEqual(doc["source"], "C:\\m.gguf")
        self.assertEqual(remote_node_win.plan(remote_win.plan_arg(doc))["model"], doc["model"])

    def test_install_sends_the_plan_and_the_chosen_file(self):
        self.windows_box()
        replies = [dict(INFO), {"installed": True}]
        execute = self.use(patch.object(machines, "execute", side_effect=lambda *a: replies.pop(0)))
        self.assertEqual(
            machines.dispatch("machine-install", {"host": "me@win", "source": " D:\\m\\x.gguf "}),
            {"installed": True},
        )
        script = decode_feed(execute.call_args.args[1])
        arg = unquote(script.splitlines()[1].split(" = ", 1)[1])
        doc = remote_node_win.plan(arg)
        self.assertEqual(doc["source"], "D:\\m\\x.gguf")
        self.assertEqual(execute.call_args.args[3], 3600)
        with self.assertRaisesRegex(ValueError, "single-line path"):
            machines.install("me@win", "a\nb")
        replies[:] = [dict(INFO, managed=False, reason="No NVIDIA GPU.")]
        with self.assertRaisesRegex(ValueError, "No NVIDIA GPU."):
            machines.install("me@win")

    def test_model_files_and_logs(self):
        self.windows_box()
        replies = [dict(INFO), ["C:\\a.gguf", 5, "D:\\b"], ["line"]]
        self.use(patch.object(machines, "execute", side_effect=lambda *a: replies.pop(0)))
        found = machines.dispatch("machine-files", {"host": "me@win"})
        self.assertEqual(found["files"], ["C:\\a.gguf", "D:\\b"])
        self.assertEqual(found["plan"]["context"], 32768)
        self.assertEqual(machines.dispatch("machine-logs", {"host": "me@win"}), ["line"])
        with self.assertRaisesRegex(ValueError, "Windows machines only"):
            machines.model_files("me@linux")

    def test_remote_command_on_windows(self):
        self.windows_box(shell="powershell")
        native = machines.remote_command("me@win", "~/p r", "Codex", "native")
        self.assertTrue(native.startswith("powershell -NoLogo -NoExit "))
        script = decode_encoded(native)
        self.assertIn("(Join-Path $HOME 'p r')", script)
        self.assertIn("& 'codex'; exit $LASTEXITCODE", script)
        shell = decode_encoded(machines.remote_command("me@win", "C:\\w", "OMP", "shell"))
        self.assertNotIn("Get-Command", shell)
        device = decode_encoded(machines.remote_command("me@win", ".", "Claude Code", "device"))
        self.assertIn("& 'claude' 'auth' 'login'", device)
        with patch.object(machines, "run") as ran:
            managed = decode_encoded(machines.remote_command("me@win", ".", "OMP", "local-model"))
        self.assertEqual([c.args for c in ran.call_args_list], [("me@win", "start", 240), ("me@win", "omp-config")])
        self.assertIn("PI_CODING_AGENT_DIR", managed)
        self.assertIn("& 'omp' '--offline' '--model' 'workbench/local-agent'", managed)
        for folder in ("a\nb", "x" * 1025):
            with self.assertRaisesRegex(ValueError, "single-line remote folder"):
                machines.remote_command("me@win", folder, "OMP", "native")

    def test_pairing_and_forget_on_windows(self):
        self.windows_box()
        key = machines.identity("me@win")
        key.write_text("private")
        key.with_suffix(".pub").write_text("ssh-ed25519 AAAAwin c\n")
        line = machines.pairing_line("me@win")
        self.assertTrue(line.startswith("powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand "))
        self.assertIn("'ssh-ed25519 AAAAwin c'", decode_encoded(line))
        with patch.object(machines, "_ssh_run") as ran:
            machines.dispatch("machine-unpair", {"host": "me@win"})
        self.assertEqual(ran.call_args.args[1], [remote_win.POWERSHELL])
        self.assertIn("AAAAwin", decode_feed(ran.call_args.args[2]))
        machines.forget("me@win")
        self.assertNotIn("me@win", machines.systems())

    def test_tunnel_uses_the_context_the_node_reports(self):
        self.windows_box()
        token = "k" * 40
        self.use(patch.object(machines, "run", side_effect=[{"port": 10542, "context": 32768}, token]))
        self.use(patch.object(machines, "forward", return_value=50000))
        self.use(patch.object(machines, "owned_tunnel", return_value=True))
        self.use(patch.object(machines, "_protocols", return_value=None))
        http = self.use(patch.object(machines, "_http"))
        http.return_value.get.return_value.json.return_value = {"data": [{"id": "local-agent"}]}
        published = self.use(patch.object(machines, "_publish"))
        machines.tunnel("me@win")
        self.assertEqual(published.call_args.args[0]["context"], 32768)


class WindowsSessionsTest(Base):
    def test_windows_sessions_are_the_docks_own_terminals(self):
        import sessions

        self.windows_box()
        docked = [
            dict(id="t1", kind="remote", host="me@win", name="awb-0123456789abcdef", harness="Codex", workspace="/p", started=5.0, lasting=False),
            dict(id="t2", kind="remote", host="me@other", name="awb-1123456789abcdef", harness="OMP", workspace="/p", started=6.0),
        ]
        self.use(patch.object(sessions, "docked", return_value=docked))
        ssh = self.use(patch.object(sessions, "over_ssh"))
        rows = sessions.remote("me@win")
        self.assertEqual([(r["name"], r["state"], r["harness"]) for r in rows], [("awb-0123456789abcdef", "attached", "Codex")])
        with patch("project_terminal.dispatch") as closed:
            self.assertTrue(sessions.end_remote("me@win", "awb-0123456789abcdef"))
        closed.assert_called_once_with("term-close", {"workspace": "/p", "id": "t1"})
        ssh.assert_not_called()


class MissingKeychainTest(unittest.TestCase):
    def test_a_missing_login_keychain_is_never_asked(self):
        import keyring
        import types

        import project_setup

        calls = []

        class Mac:
            def get_password(self, *a):
                calls.append(a)

            set_password = delete_password = get_password

        Mac.__module__ = "keyring.backends.macOS"
        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        row = dict(provider="Attached endpoint", endpoint="http://127.0.0.1:9/v1", connection_id="abc")
        with (
            patch.object(keyring, "get_keyring", return_value=Mac()),
            patch.object(keyring, "get_password", side_effect=lambda *a: calls.append(a)),
            patch.object(keyring, "set_password", side_effect=lambda *a: calls.append(a)),
            patch.object(connections.sys, "platform", "darwin"),
            patch.object(connections.Path, "home", return_value=home),
            patch.object(connections, "_MEMORY", {}),
        ):
            status = connections.vault_status()
            self.assertFalse(status["available"])
            self.assertIn("login keychain was not found", status["reason"])
            self.assertEqual(connections.store_key(row, "k"), "memory")
            self.assertEqual(connections.secret(row), "k")
            self.assertTrue(connections.forget_key(row))
            self.assertEqual(project_setup.secret_status(["X_TOKEN"]), {"X_TOKEN": "missing"})
            with self.assertRaisesRegex(ValueError, "no system credential vault"):
                project_setup.store_secret("X_TOKEN", "v")
            self.assertFalse(project_setup.forget_secret("X_TOKEN"))
            self.assertEqual(calls, [])
            (home / "Library" / "Keychains").mkdir(parents=True)
            (home / "Library" / "Keychains" / "login.keychain-db").write_text("")
            self.assertTrue(connections.vault_status()["available"])


class ManagedNodeOmpTest(unittest.TestCase):
    def test_omp_names_the_machine_node_model(self):
        import runtime

        home = Path(tempfile.mkdtemp())
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        s = dict(runtime.settings(), workspace=str(work), model="", context=32768, search=False, firecrawl=False, blender=False, vision=False)
        fake = type(sys)("project_setup")
        fake.omp_entries = lambda s, taken: {}
        fake.project_config_allowed = lambda workspace: False
        with (
            patch.dict(sys.modules, {"project_setup": fake}),
            patch.object(Path, "home", return_value=home),
            patch.object(shutil, "which", return_value="omp"),
        ):
            cmd = runtime.omp_command(s, profile_name="workbench-x", base="http://127.0.0.1:5/v1", model_id=runtime.ALIAS, env_key="K")
        folder = Path(cmd[cmd.index("--config") + 1]).parent
        models = json.loads((folder / "models.yml").read_text())
        self.assertEqual(models["providers"]["workbench"]["models"][0]["name"], "local-agent")


class WindowsNodeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        base = self.root / "Local" / "agent-workbench-node"
        for name, value in (("LOCAL", self.root / "Local"), ("BASE", base), ("MARKER", base / "owner-v1"), ("LOCK", self.root / "Local" / "lock")):
            item = patch.object(remote_node_win, name, value)
            item.start()
            self.addCleanup(item.stop)
        self.base = base

    def model(self, body):
        return dict(name="m.gguf_file", url="https://x/m", sha256=hashlib.sha256(body).hexdigest(), bytes=len(body), context=32768, label="M")

    def test_source_runs_on_python_38_with_the_standard_library(self):
        source = remote_win.node_source()
        ast.parse(source, feature_version=(3, 8))
        allowed = set(sys.stdlib_module_names) | {"chat_templates"}
        for node in ast.walk(ast.parse(source)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            names += [node.module] if isinstance(node, ast.ImportFrom) else []
            for name in names:
                self.assertIn(name.split(".")[0], allowed)
        self.assertIn("Requires Python 3.8+.", (ROOT / "remote_node_win.py").read_text().splitlines()[0])

    def test_plan_is_validated(self):
        good = dict(model=self.model(b"x"), engine=[dict(name="e.zip", url="https://x/e", sha256="0" * 64, bytes=1)])
        self.assertEqual(remote_node_win.plan(remote_win.plan_arg(good))["model"]["name"], "m.gguf_file")
        for bad in (
            dict(good, model=dict(good["model"], name="..\\evil")),
            dict(good, model=dict(good["model"], name="a/b")),
            dict(good, model=dict(good["model"], url="http://x")),
            dict(good, engine=[]),
            dict(good, model=dict(good["model"], context="big")),
        ):
            with self.assertRaisesRegex(ValueError, "install plan"):
                remote_node_win.plan(remote_win.plan_arg(bad))
        with self.assertRaisesRegex(ValueError, "install plan"):
            remote_node_win.plan("not base64!")

    def test_candidates_match_size_only_in_the_usual_folders(self):
        body = b"model-bytes"
        home = self.root / "home"
        (home / "models" / "deep").mkdir(parents=True)
        (home / "models" / "deep" / "m.gguf").write_bytes(body)
        (home / "models" / "other.bin").write_bytes(b"x" * len(body))
        (home / "models" / "wrong.gguf").write_bytes(b"short")
        (home / "Downloads").mkdir()
        (home / "Downloads" / "copy.gguf").write_bytes(body)
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "pointed.gguf").write_bytes(body)
        with patch.object(remote_node_win.Path, "home", return_value=home):
            found = remote_node_win.candidates(self.model(body), [str(elsewhere / "pointed.gguf")])
        self.assertEqual(
            sorted(Path(p).name for p in found), ["copy.gguf", "m.gguf", "other.bin", "pointed.gguf"]
        )
        self.assertEqual(Path(found[0]).name.split(".")[0], "m")

    def test_adopt_links_only_a_matching_file(self):
        body = b"model-bytes"
        model = self.model(body)
        self.base.mkdir(parents=True)
        source = self.root / "src.gguf"
        source.write_bytes(body)
        dest = self.base / model["name"]
        self.assertIn(remote_node_win.adopt(str(source), dest, model), ("linked", "copied"))
        self.assertEqual(dest.read_bytes(), body)
        wrong = self.root / "wrong.gguf"
        wrong.write_bytes(b"model-bytez")
        with self.assertRaisesRegex(ValueError, "size or SHA-256 differs"):
            remote_node_win.adopt(str(wrong), self.base / "other", model)
        with self.assertRaisesRegex(ValueError, "is not a file on this machine"):
            remote_node_win.adopt(str(self.root / "missing"), self.base / "other", model)
        self.assertFalse((self.base / "other").exists())

    def test_unzip_refuses_unsafe_paths(self):
        for name in ("../x.dll", "/abs.dll", "C:/x.dll", "a\\..\\..\\x.dll"):
            archive = self.root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr(name, "x")
            with self.assertRaisesRegex(ValueError, "unsafe path"):
                remote_node_win.unzip(archive, self.root / "out")
        good = self.root / "good.zip"
        with zipfile.ZipFile(good, "w") as z:
            z.writestr("bin/llama-server.exe", "x")
        remote_node_win.unzip(good, self.root / "ok")
        self.assertTrue((self.root / "ok" / "bin" / "llama-server.exe").is_file())

    def test_ownership_and_completeness(self):
        with self.assertRaisesRegex(ValueError, "not installed"):
            remote_node_win.complete()
        self.base.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "was not created by Workbench"):
            remote_node_win.owned()
        (self.base / "owner-v1").write_text("agent-workbench-node-v1")
        with self.assertRaisesRegex(ValueError, "not completely installed"):
            remote_node_win.complete()
        (self.base / "engine").mkdir()
        (self.base / "engine" / "llama-server.exe").write_text("x")
        (self.base / "m.gguf_file").write_text("x")
        (self.base / "api-key").write_text("secret")
        (self.base / "model.json").write_text(json.dumps(dict(name="m.gguf_file", context=32768, label="M")))
        self.assertEqual(remote_node_win.complete()["context"], 32768)
        (self.base / "model.json").write_text(json.dumps(dict(name="..\\x", context=1)))
        self.assertIsNone(remote_node_win.chosen())

    def test_unsupported_reasons(self):
        with patch.object(remote_node_win.os, "name", "nt"), patch.dict(os.environ, {"PROCESSOR_ARCHITECTURE": "AMD64"}):
            self.assertIn("needs an NVIDIA GPU", remote_node_win.unsupported(None))
            self.assertIn("older than 580", remote_node_win.unsupported(dict(driver="572.16")))
            self.assertEqual(remote_node_win.unsupported(dict(driver="621.24")), "")
        with patch.object(remote_node_win.os, "name", "nt"), patch.dict(os.environ, {"PROCESSOR_ARCHITECTURE": "ARM64"}):
            self.assertIn("64-bit x86", remote_node_win.unsupported(dict(driver="621.24")))


if __name__ == "__main__":
    unittest.main()
