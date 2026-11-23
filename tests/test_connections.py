import atexit
import inspect
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler, build_opener

if "runtime" not in sys.modules and "WORKBENCH_DATA" not in os.environ:
    os.environ["WORKBENCH_DATA"] = tempfile.mkdtemp(prefix="workbench-test-")
    atexit.register(shutil.rmtree, os.environ["WORKBENCH_DATA"], ignore_errors=True)

import keyring.errors  # noqa: E402
import requests  # noqa: E402

import account_book  # noqa: E402
import connections  # noqa: E402
import endpoints  # noqa: E402
import machines  # noqa: E402
import paths  # noqa: E402
import project_profiles  # noqa: E402
import project_setup  # noqa: E402
import provider_auth  # noqa: E402
import providers  # noqa: E402
import runtime  # noqa: E402
import visual_routes  # noqa: E402

VAULT = {"available": True, "backend": "test", "reason": ""}
NO_VAULT = {"available": False, "backend": "fail", "reason": "No vault."}
VAULT_STATUS = connections.vault_status
PUBLIC_CATALOG = visual_routes.public_catalog
ALL = {"chat": True, "responses": True, "messages": True}
LOCAL = "http://127.0.0.1:8000/v1"
GATE = (
    "This folder defines tools that can run programs. Open Project setup, review "
    "them and allow them, then launch again."
)


def tools(**sent):
    return dict(argv=[], env={}, files=[], servers=[], skipped=[]) | sent


def unwired(case):
    """Launches in this test skip the folder's consent gate and send no MCP servers."""
    stubs = Mock(side_effect=lambda s, harness=None: s), Mock(return_value=tools())
    for name, stub in zip(("before_launch", "launch_mcp"), stubs):
        patcher = patch.object(project_setup, name, stub)
        patcher.start()
        case.addCleanup(patcher.stop)
    return stubs


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.gate, self.tools = unwired(self)

    def test_named_keys_do_not_fall_back_to_provider_environment(self):
        with (
            patch.dict(os.environ, OPENROUTER_API_KEY="legacy"),
            patch("keyring.get_password", return_value=None),
        ):
            self.assertEqual(connections.secret({"provider": "OpenRouter"}), "legacy")
            with self.assertRaises(ValueError):
                connections.secret({"provider": "OpenRouter", "connection_id": "named"})

    def test_claude_gateways_honor_account_and_model(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(connections, "DATA", Path(tmp)),
            patch.object(connections, "command", return_value=["claude"]),
            patch.object(connections, "secret", return_value="fixture"),
        ):
            for provider, url in [
                ("OpenRouter", "https://openrouter.ai/api"),
                ("DeepSeek", "https://api.deepseek.com/anthropic"),
            ]:
                s = runtime.DEFAULTS | dict(
                    workspace=tmp, provider=provider, remote_model="selected"
                )
                cmd, env, _ = connections.prepare(s, "Claude Code")
                self.assertEqual(cmd[cmd.index("--model") + 1], "selected")
                self.assertEqual(env["ANTHROPIC_BASE_URL"], url)
                self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "fixture")
                self.assertEqual(env["ANTHROPIC_API_KEY"], "")

    def test_project_matches_connection_by_name_across_machines(self):
        accounts = [
            dict(
                provider="OpenRouter",
                name="Model lab",
                connection_id="first",
                endpoint="https://openrouter.ai/api/v1",
            )
        ]
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(account_book, "available", return_value=accounts),
        ):
            s = runtime.DEFAULTS | dict(
                provider="OpenRouter",
                connection_id="first",
                remote_model="selected-model",
                workspace=tmp,
            )
            saved = Path(project_profiles.save_project(s, "lab")).read_text()
            self.assertNotIn(accounts[0]["endpoint"], saved)
            self.assertNotIn('"first"', saved)
            accounts[0]["connection_id"] = "second"
            restored = project_profiles.restore_project(
                s | dict(connection_id="wrong", provider="Groq"), "lab", []
            )
            self.assertEqual(restored["connection_id"], "second")
            self.assertEqual(restored["remote_model"], "selected-model")
            accounts.clear()
            with self.assertRaisesRegex(ValueError, "Model lab"):
                project_profiles.restore_project(s, "lab", [])

    def test_local_server_start_with_hosted_source_keeps_settings(self):
        s = runtime.DEFAULTS | dict(
            provider="OpenRouter", remote_model="hosted", model="saved.gguf"
        )
        import desktop_bridge

        with (
            patch.object(runtime, "settings", return_value=s),
            patch.object(runtime, "start") as start,
            patch.object(runtime, "write_json") as write,
        ):
            desktop_bridge.dispatch(
                "serve", {"provider": "OpenRouter", "preset": "ignored"}
            )
            self.assertEqual(start.call_args.args[0]["model"], "saved.gguf")
            self.assertEqual(start.call_args.args[0]["provider"], "Managed local")
            self.assertEqual(s["provider"], "OpenRouter")
            write.assert_not_called()

    def test_probe_cache_and_ssh_target(self):
        import studio

        with (
            patch.object(studio, "TOOLS", {}),
            patch.object(studio.tooling, "TOOLS", {"OMP": None}),
            patch.object(studio.tooling, "location", return_value="/example") as locate,
        ):
            self.assertEqual(studio.checks(), studio.checks())
            locate.assert_called_once()
        for invalid in ("-F", "remote && echo bad", "local@box/dir", ""):
            with self.assertRaises(ValueError):
                machines.target(invalid)
        self.assertEqual(machines.target("user@test-bench"), "user@test-bench")

    def test_forwarded_profile_uses_remote_context_and_current_port(self):
        saved = {
            "provider": "Attached endpoint",
            "context": 8192,
            "endpoint": "http://127.0.0.1:3333/v1",
        }
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(connections, "DATA", Path(tmp)),
            patch.object(account_book, "entries", return_value={"test": saved}),
            patch.object(machines, "labels", return_value={"node": "Test node"}),
            patch.object(machines, "identity", return_value=Path(tmp) / "test" / "key"),
            patch.object(
                machines,
                "tunnel",
                return_value={"endpoint": "http://127.0.0.1:4444/v1"},
            ),
            patch.object(connections, "secret", return_value="fixture"),
            patch.object(connections, "command", return_value=["claude"]),
        ):
            s = runtime.DEFAULTS | dict(
                workspace=tmp,
                provider="Attached endpoint",
                connection_id="test",
                remote_model="local-agent",
                context=131072,
            )
            _, env, _ = connections.prepare(s, "Claude Code")
            self.assertEqual(env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "8192")
            self.assertEqual(env["ANTHROPIC_BASE_URL"], "http://127.0.0.1:4444")
            self.assertEqual(s["context"], 131072)

    def test_node_removal_only_removes_its_scoped_connection(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(account_book, "DATA", Path(tmp)),
            patch.object(connections, "forget_key") as forget,
        ):
            work = account_book.save(
                dict(provider="OpenRouter", name="Work", remote_model="selected")
            )
            node = account_book.save(
                dict(
                    provider="Attached endpoint",
                    name="Node",
                    endpoint="http://127.0.0.1:9521/v1",
                )
            )
            account_book.remove_node(work["connection_id"])
            forget.assert_not_called()
            account_book.remove_node(node["connection_id"])
            forget.assert_called_once_with(node)
            self.assertEqual(list(account_book.entries()), [work["connection_id"]])


def backend(module, name):
    return type(name, (), {"__module__": module})()


def reply(code=200, payload=None, text=False):
    answer = Mock(status_code=code)
    if text:
        answer.json.side_effect = requests.exceptions.JSONDecodeError("x", "<html>", 0)
    else:
        answer.json.return_value = payload
    return answer


class Case(unittest.TestCase):
    catalog = True
    wired = False

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.data = Path(folder.name).resolve()
        self.work = self.data / "project"
        self.work.mkdir()
        self.keys = {}
        hidden = tuple(connections.PROVIDER_KEYS.values())
        clean = {
            name: value
            for name, value in os.environ.items()
            if name not in hidden
            and not name.startswith(("WORKBENCH_KEY_", "ANTHROPIC_", "OPENCODE_"))
        }
        self.use(patch.dict(os.environ, clean, clear=True))
        self.use(patch.object(connections, "DATA", self.data))
        self.use(patch.object(account_book, "DATA", self.data))
        cache = self.data / "openrouter-models.json"
        self.use(patch.object(visual_routes, "CACHE", cache))
        self.use(patch.object(connections, "_MEMORY", {}))
        self.status = self.use(
            patch.object(connections, "vault_status", return_value=dict(VAULT))
        )
        self.get = self.use(
            patch(
                "keyring.get_password", side_effect=lambda _, slot: self.keys.get(slot)
            )
        )
        self.set = self.use(
            patch(
                "keyring.set_password",
                side_effect=lambda _, slot, value: self.keys.update({slot: value}),
            )
        )
        self.delete = self.use(
            patch(
                "keyring.delete_password",
                side_effect=lambda _, slot: self.keys.pop(slot),
            )
        )
        self.live = self.use(
            patch.object(endpoints, "model_context", return_value=None)
        )
        self.use(patch.object(machines, "labels", return_value={}))
        self.use(patch.object(account_book, "existing_codex", return_value=False))
        if self.catalog:
            self.use(patch.object(visual_routes, "public_catalog", return_value={}))
        if self.wired:
            self.use(patch.object(project_profiles, "DATA", self.data))
        else:
            self.gate, self.tools = unwired(self)

    def use(self, item):
        value = item.start()
        self.addCleanup(item.stop)
        return value

    def settings(self, **extra):
        return runtime.DEFAULTS | dict(workspace=str(self.work), **extra)

    def attached(self, **extra):
        row = dict(
            provider="Attached endpoint",
            name="Box",
            endpoint=LOCAL,
            remote_model="coder",
            connection_id="ab" * 8,
        )
        return account_book.save(row | extra, allow_endpoint_change=True)

    def files(self):
        return [p for p in self.data.rglob("*") if p.is_file()]


class NormaliseTests(unittest.TestCase):
    def test_accepts_and_is_idempotent(self):
        for typed, wanted in [
            ("localhost:1234", "http://localhost:1234"),
            (" http://localhost:11434/ ", "http://localhost:11434"),
            ("127.0.0.1:8080/v1/chat/completions", "http://127.0.0.1:8080/v1"),
            ("myhost.example.com/v1", "https://myhost.example.com/v1"),
            ("http://[::1]:8080/v1/", "http://[::1]:8080/v1"),
            (
                "https://api.example.com/openai/v1/models",
                "https://api.example.com/openai/v1",
            ),
            (LOCAL, LOCAL),
        ]:
            self.assertEqual(connections.normalise(typed), wanted)
            self.assertEqual(connections.normalise(wanted), wanted)

    def test_rejects_with_guidance(self):
        for typed in ("", "   ", None, 7):
            with self.assertRaisesRegex(ValueError, "Enter the server address"):
                connections.normalise(typed)
        for typed in ("http://localhost:abc/v1", "http://localhost:99999/v1"):
            with self.assertRaisesRegex(
                ValueError, "port in this address is not valid"
            ):
                connections.normalise(typed)
        for typed in (
            "http://localhost:0/v1",
            "https://key@example.org/v1",
            "http://127.0.0.1:8000/v1&calc",
            "http://a&b/v1",
            "http://127.0.0.1:8000/v1?x=1",
            "ftp://x/v1",
            "http://127.0.0.1:8000/v1%20x",
            "http://127.0.0.1:8000/v1#top",
            'http://127.0.0.1:8000/v1"x',
        ):
            with self.assertRaisesRegex(ValueError, "Use a plain API address"):
                connections.normalise(typed)

    def test_endpoint_policy(self):
        attached = {"provider": "Attached endpoint", "remote_model": " m "}
        with self.assertRaisesRegex(ValueError, "through an SSH tunnel"):
            connections.endpoint(attached | {"endpoint": "http://example.org/v1"})
        self.assertEqual(
            connections.endpoint(attached | {"endpoint": "https://example.org/v1/"}),
            ("https://example.org/v1", "m"),
        )
        self.assertEqual(
            connections.endpoint(attached | {"endpoint": "localhost:1234/v1/models"})[
                0
            ],
            "http://localhost:1234/v1",
        )
        for unknown in ("Nowhere", None, ["OpenAI"]):
            with self.assertRaisesRegex(ValueError, "Choose a supported model source."):
                connections.endpoint({"provider": unknown})
        self.assertEqual(
            connections.endpoint({"provider": "Mistral", "endpoint": "https://x.test"}),
            ("https://api.mistral.ai/v1", ""),
        )


class ValidationTests(Case):
    def test_clean_model(self):
        for good in (
            "",
            "qwen3:8b",
            "~unbiased/pareto-latest",
            "/models/x.gguf",
            "C:\\models\\my model.gguf",
        ):
            self.assertEqual(connections.clean_model(" " + good + " "), good)
        self.assertEqual(connections.clean_model(None), "")
        for bad in ("-rf", "a\tb", "a\nb", "a\x7fb", "x" * 301):
            with self.assertRaisesRegex(ValueError, "model ID cannot be used"):
                connections.clean_model(bad)

    def test_clean_auth(self):
        for good in ("", "openai-codex-device", "anthropic"):
            self.assertEqual(connections.clean_auth(good), good)
        self.assertEqual(connections.clean_auth(None), "")
        for bad in ("a&b", "-x", "x" * 65, ["anthropic"], "a b"):
            with self.assertRaisesRegex(ValueError, "Choose a subscription"):
                connections.clean_auth(bad)

    def test_batch_unsafe(self):
        shim = "C:\\npm\\codex.CMD"
        self.assertEqual(connections.batch_unsafe(shim, ["gpt-5&calc.exe"], True), "&")
        self.assertEqual(connections.batch_unsafe(shim, ["gpt-5&calc.exe"], False), "")
        self.assertEqual(
            connections.batch_unsafe("C:\\bin\\codex.exe", ["gpt-5&calc.exe"], True), ""
        )
        self.assertEqual(connections.batch_unsafe(shim, ["ok", "a\nb"], True), "\n")
        self.assertEqual(
            connections.batch_unsafe("x.bat", [None, 'say "hi"'], True), '"'
        )
        self.assertEqual(
            connections.batch_unsafe(shim, ["gpt-5", "plain text"], True), ""
        )
        with patch.object(connections, "WINDOWS", True):
            self.assertEqual(connections.batch_unsafe(shim, ["100%"]), "%")

    def test_prepare_refuses_metacharacters_for_batch_shim(self):
        shim = ["C:\\npm\\codex.CMD"]
        native = self.settings(provider="Native login", remote_model="gpt-5&calc.exe")
        with (
            patch.object(connections, "WINDOWS", True),
            patch.object(connections, "command", return_value=shim),
            patch.object(connections, "secret", return_value="fixture"),
        ):
            with self.assertRaisesRegex(ValueError, "Windows batch file.*character &"):
                connections.prepare(native, "Codex")
            argv, _, _ = connections.prepare(
                native | dict(remote_model="gpt-5"), "Codex"
            )
            self.assertEqual(
                argv, shim + ["--model", "gpt-5", "--sandbox", "workspace-write"]
            )
            self.assertEqual(shim, ["C:\\npm\\codex.CMD"])
            hosted = self.settings(
                provider="OpenAI", remote_model="gpt-5", prompt="one\ntwo"
            )
            with self.assertRaisesRegex(ValueError, "Pi is installed.*a line break"):
                connections.prepare(hosted, "Pi")
            connections.prepare(hosted | dict(prompt="one two"), "Pi")
        for bad in (dict(remote_model="-x"), dict(auth_provider="a&b")):
            with patch.object(connections, "command", return_value=["codex"]):
                with self.assertRaises(ValueError):
                    connections.prepare(native | dict(remote_model="") | bad, "Codex")
        with self.assertRaisesRegex(ValueError, "Unknown harness"):
            connections.prepare(native, "Cursor")


class GateTests(Case):
    def test_table_gating(self):
        claude = connections.compat("OpenAI")["Claude Code"]
        self.assertFalse(claude["ok"])
        self.assertIn("Anthropic Messages", claude["reason"])
        self.assertIn("choose OMP, Pi, Codex or opencode, or another", claude["reason"])
        self.assertEqual(
            connections.compat("Groq")["Codex"], {"ok": True, "reason": ""}
        )
        codex = connections.compat("DeepSeek")["Codex"]
        self.assertTrue(codex["ok"])
        self.assertIn(
            "not confirmed to offer the OpenAI Responses API", codex["reason"]
        )
        self.assertIn(
            "choose OMP, Pi, Claude Code or opencode",
            connections.compat("Anthropic")["Codex"]["reason"],
        )
        for name, verdict in connections.compat("OpenRouter").items():
            self.assertEqual(verdict["ok"], name != "Hermes")
        for provider in connections.PROVIDERS:
            verdicts = connections.compat(provider)
            self.assertEqual(list(verdicts), connections.HARNESS)
            if provider == "Native login":
                for verdict in verdicts.values():
                    self.assertEqual(verdict, {"ok": True, "reason": ""})
            else:
                self.assertFalse(verdicts["Hermes"]["ok"])
                self.assertIn("Agent account", verdicts["Hermes"]["reason"])
        with self.assertRaisesRegex(ValueError, "Choose a supported model source."):
            connections.compat("Nowhere")

    def test_attached_row_gating(self):
        row = {"name": "Box", "protocols": {"chat": True, "responses": False}}
        verdicts = connections.compat("Attached endpoint", row)
        self.assertFalse(verdicts["Codex"]["ok"])
        self.assertIn("which Box does not offer", verdicts["Codex"]["reason"])
        self.assertEqual(verdicts["OMP"], {"ok": True, "reason": ""})
        self.assertIn("Box is not confirmed", verdicts["Claude Code"]["reason"])
        for bare in (
            None,
            {},
            {"protocols": "all"},
            {"protocols": {"chat": "no", "responses": 0}},
        ):
            verdicts = connections.compat("Attached endpoint", bare)
            blocked = [name for name, verdict in verdicts.items() if not verdict["ok"]]
            self.assertEqual(blocked, ["Hermes"])
            self.assertIn(
                "Compatible endpoint is not confirmed", verdicts["Codex"]["reason"]
            )
        none = {"protocols": {"chat": False, "responses": False, "messages": False}}
        reason = connections.compat("Attached endpoint", none)["Pi"]["reason"]
        self.assertTrue(reason.endswith("does not offer; choose another source."))

    def test_a_template_that_rejects_a_late_system_message_gates_two_agents(self):
        row = {"name": "Box", "protocols": dict(ALL), "late_system": False}
        verdicts = connections.compat("Attached endpoint", row)
        self.assertEqual(
            verdicts["Claude Code"],
            {
                "ok": False,
                "reason": "This server's chat template rejects the system message Claude "
                "Code sends mid-conversation. Serve the model with Workbench, which fixes "
                "the template, or use OMP, Pi or opencode.",
            },
        )
        self.assertEqual(
            verdicts["Codex"]["reason"],
            "This server's chat template rejects the developer message Codex sends "
            "mid-conversation. Serve the model with Workbench, which fixes the "
            "template, or use OMP, Pi or opencode.",
        )
        self.assertFalse(verdicts["Codex"]["ok"])
        for name in ("OMP", "Pi", "opencode"):
            self.assertEqual(verdicts[name], {"ok": True, "reason": ""})
        # A missing API stays the first reason, and its alternatives skip gated agents.
        no_messages = dict(row, protocols=dict(ALL, messages=False))
        verdicts = connections.compat("Attached endpoint", no_messages)
        self.assertIn("does not offer; choose OMP, Pi or opencode,", verdicts["Claude Code"]["reason"])
        for late in (True, None, "no", 0):
            verdicts = connections.compat("Attached endpoint", dict(row, late_system=late))
            self.assertTrue(verdicts["Claude Code"]["ok"] and verdicts["Codex"]["ok"])
        for provider in ("OpenRouter", "Native login"):
            verdicts = connections.compat(provider, dict(row, name="Hosted"))
            self.assertNotIn("chat template", verdicts["Claude Code"]["reason"])

    def test_launch_refuses_a_gated_agent_with_the_sentence(self):
        self.attached(protocols=dict(ALL), late_system=False)
        s = self.settings(provider="Attached endpoint", connection_id="ab" * 8)
        with patch.object(connections, "command", return_value=["claude"]):
            with self.assertRaisesRegex(ValueError, "rejects the system message Claude Code"):
                connections.prepare(s, "Claude Code")

    def test_prepare_raises_gate_reason(self):
        s = self.settings(provider="OpenAI", remote_model="gpt-5")
        with (
            patch.object(connections, "command", return_value=["claude"]),
            patch.object(connections, "secret") as secret,
        ):
            with self.assertRaises(ValueError) as caught:
                connections.prepare(s, "Claude Code")
            self.assertEqual(
                str(caught.exception),
                connections.compat("OpenAI")["Claude Code"]["reason"],
            )
            with self.assertRaisesRegex(ValueError, "Hermes signs in with its own"):
                connections.prepare(s, "Hermes")
            secret.assert_not_called()
            self.attached(
                protocols={"chat": True, "responses": False, "messages": None}
            )
            pick = self.settings(provider="Attached endpoint", connection_id="ab" * 8)
            with self.assertRaisesRegex(ValueError, "which Box does not offer"):
                connections.prepare(pick | dict(remote_model="coder"), "Codex")
            secret.assert_not_called()


class ContextTests(Case):
    def test_resolution_order(self):
        resolve = connections.context_for
        s = self.settings(context=50000, provider="Attached endpoint")
        self.assertEqual(resolve(s | dict(provider="Managed local")), (50000, "local"))
        node = {"context": 8192, "remote_model": "m"}
        self.assertEqual(resolve(s, node, LOCAL, "m"), (8192, "node"))
        self.assertEqual(
            resolve(s | dict(context=4096), node, LOCAL, "m"), (4096, "node")
        )
        user = {"context_window": 200000, "context_source": "user", "kind": "ollama"}
        self.assertEqual(resolve(s, user, LOCAL, "m"), (200000, "user"))
        self.live.assert_not_called()
        probed = {
            "kind": "ollama",
            "context_window": 4096,
            "context_source": "server",
            "remote_model": "m",
        }
        self.live.return_value = 65536
        self.assertEqual(resolve(s, probed, LOCAL, "m", "tok"), (65536, "server"))
        self.live.assert_called_once_with(LOCAL, "ollama", "m", "tok")
        self.live.return_value = None
        self.assertEqual(resolve(s, probed, LOCAL, "m"), (4096, "server"))
        self.assertEqual(resolve(s, probed, LOCAL, "other"), (32768, "default"))
        self.live.reset_mock()
        self.assertEqual(
            resolve(s, probed | dict(kind="mystery"), LOCAL, "m"), (4096, "server")
        )
        self.live.assert_not_called()
        remote = {
            "context": 65536,
            "host": "box",
            "remote_port": 8000,
            "remote_model": "m",
        }
        self.assertEqual(resolve(s, remote, LOCAL, "m"), (65536, "server"))
        hosted = s | dict(provider="OpenAI")
        base = "https://api.openai.com/v1"
        runtime.write_json(
            self.data / "model-context.json", {base: {"gpt-x": 400000, "odd": "big"}}
        )
        self.assertEqual(resolve(hosted, None, base, "gpt-x"), (400000, "listing"))
        self.assertEqual(resolve(hosted, None, base, "odd"), (128000, "default"))
        with patch.object(
            visual_routes, "context_length", return_value=222000
        ) as known:
            self.assertEqual(resolve(hosted, None, base, "gpt-y"), (222000, "catalog"))
            known.assert_called_once_with("OpenAI", "gpt-y")
            self.assertEqual(resolve(hosted, None, base, "gpt-x"), (400000, "listing"))
        self.assertEqual(resolve(hosted, None, base, "gpt-y"), (128000, "default"))
        self.assertEqual(resolve(s, None, LOCAL, "m"), (32768, "default"))
        (self.data / "model-context.json").write_text("{broken")
        self.assertEqual(resolve(hosted, None, base, "gpt-x"), (128000, "default"))

    def test_prepare_uses_resolved_context(self):
        base = "https://api.openai.com/v1"
        runtime.write_json(self.data / "model-context.json", {base: {"gpt-x": 400000}})
        s = self.settings(provider="OpenAI", remote_model="gpt-x", context=32768)
        with (
            patch.object(connections, "command", side_effect=lambda name: [name]),
            patch.object(connections, "secret", return_value="fixture"),
        ):
            connections.prepare(s, "Pi")
            model = json.loads(next(self.data.rglob("models.json")).read_text())
            entry = model["providers"]["workbench"]["models"][0]
            self.assertEqual(
                (entry["contextWindow"], entry["maxTokens"]), (400000, 16384)
            )
            argv, _, _ = connections.prepare(s, "Codex")
            self.assertIn("model_context_window=400000", argv)
            self.assertEqual(argv[:3], ["codex", "--model", "gpt-x"])
            omp = self.use(
                patch.object(connections, "omp_command", return_value=["omp"])
            )
            connections.prepare(s, "OMP")
            self.assertEqual(omp.call_args.args[0]["context"], 400000)
            self.assertEqual(omp.call_args.kwargs["base"], base)
        self.assertEqual(s["context"], 32768)


class ClaudeTests(Case):
    def launch(self, s):
        with patch.object(connections, "command", return_value=["claude"]):
            return connections.prepare(s, "Claude Code")[1]

    def test_local_and_kind_specific_environment(self):
        os.environ.update(ANTHROPIC_API_KEY="inherited", ANTHROPIC_AUTH_TOKEN="stale")
        pick = self.settings(provider="Attached endpoint", connection_id="ab" * 8)
        self.attached(kind="mlx-omni")
        env = self.launch(pick | dict(remote_model="coder"))
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "http://127.0.0.1:8000/anthropic")
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "local")
        self.assertEqual(env["CLAUDE_CODE_ATTRIBUTION_HEADER"], "0")
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertEqual(env["WORKBENCH_SESSION_KEY"], "local")
        row = self.attached(kind="textgen")
        self.keys[connections.vault_slot(row)] = "sk-tg"
        env = self.launch(pick | dict(remote_model="coder"))
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "http://127.0.0.1:8000")
        self.assertEqual(env["ANTHROPIC_API_KEY"], "sk-tg")
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        self.assertEqual(env["CLAUDE_CODE_ATTRIBUTION_HEADER"], "0")
        with (
            patch.object(connections, "start") as start,
            patch.object(connections, "key", return_value="local-key"),
        ):
            env = self.launch(self.settings(context=40000))
            start.assert_called_once()
        self.assertEqual(env["ANTHROPIC_BASE_URL"], runtime.URL)
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "local-key")
        self.assertEqual(env["ANTHROPIC_DEFAULT_OPUS_MODEL"], runtime.ALIAS)
        self.assertEqual(env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "40000")
        self.assertEqual(env["CLAUDE_CODE_ATTRIBUTION_HEADER"], "0")
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant"
        env = self.launch(self.settings(provider="Anthropic", remote_model="claude-x"))
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://api.anthropic.com")
        self.assertEqual(env["ANTHROPIC_API_KEY"], "sk-ant")
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        self.assertNotIn("CLAUDE_CODE_ATTRIBUTION_HEADER", env)
        self.assertEqual(env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "128000")


class OpencodeTests(Case):
    def test_inline_config_without_secret_on_disk(self):
        base, model = "https://api.openai.com/v1", "org/name:8b"
        runtime.write_json(self.data / "model-context.json", {base: {model: 65536}})
        s = self.settings(
            provider="OpenAI", remote_model=model, prompt="Stay in scope."
        )
        with (
            patch.object(connections, "command", return_value=["/bin/opencode"]),
            patch.object(connections, "secret", return_value="sk-fixture"),
        ):
            argv, env, cwd = connections.prepare(s, "opencode")
        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        provider = config["provider"]["workbench"]
        self.assertEqual(provider["npm"], "@ai-sdk/openai-compatible")
        self.assertEqual(
            provider["options"],
            {"baseURL": base, "apiKey": "{env:WORKBENCH_SESSION_KEY}"},
        )
        self.assertEqual(
            provider["models"],
            {model: {"name": model, "limit": {"context": 65536, "output": 16384}}},
        )
        self.assertEqual(config["model"], "workbench/" + model)
        self.assertIs(config["autoupdate"], False)
        self.assertEqual(env["WORKBENCH_SESSION_KEY"], "sk-fixture")
        self.assertEqual(env["OPENCODE_DISABLE_AUTOUPDATE"], "1")
        self.assertEqual(env["OPENCODE_DISABLE_MODELS_FETCH"], "1")
        self.assertEqual(argv, ["/bin/opencode", "--model", "workbench/" + model])
        self.assertEqual(cwd, self.work)
        self.assertEqual(env["PWD"], str(cwd))

        self.assertFalse(any("sk-fixture" in part for part in argv))
        self.assertNotIn("sk-fixture", env["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(
            [name for name, value in env.items() if "sk-fixture" in value],
            ["WORKBENCH_SESSION_KEY"],
        )
        notes = Path(config["instructions"][0])
        self.assertEqual(notes.read_text(encoding="utf8"), "Stay in scope.")
        self.assertEqual(notes.parent.parent.parent, self.data / "clients")
        written = self.files()
        self.assertIn(notes, written)
        for path in written:
            self.assertNotIn(b"sk-fixture", path.read_bytes(), path)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_every_launch_points_pwd_at_the_project(self):
        for harness in ("OMP", "Pi", "Claude Code", "Codex", "opencode"):
            provider = "OpenRouter" if harness == "Claude Code" else "OpenAI"
            s = self.settings(provider=provider, remote_model="org/model")
            with (
                self.subTest(harness=harness),
                patch.dict(os.environ, {"PWD": "/somewhere/else"}),
                patch.object(connections, "command", return_value=["/bin/agent"]),
                patch.object(connections, "secret", return_value="sk-fixture"),
                patch.object(connections, "omp_command", return_value=["/bin/omp"]),
            ):
                _, env, cwd = connections.prepare(s, harness)
                self.assertEqual(env["PWD"], str(cwd))

    def test_no_key_and_native_login(self):
        s = self.settings(
            provider="Attached endpoint", endpoint=LOCAL, remote_model="m", prompt="  "
        )
        with patch.object(connections, "command", return_value=["opencode"]):
            argv, env, _ = connections.prepare(s, "opencode")
            config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
            self.assertEqual(
                config["provider"]["workbench"]["options"], {"baseURL": LOCAL}
            )
            self.assertNotIn("instructions", config)
            limit = config["provider"]["workbench"]["models"]["m"]["limit"]
            self.assertEqual(limit, {"context": 32768, "output": 16384})
            self.assertEqual(env["WORKBENCH_SESSION_KEY"], "local")
            self.assertEqual(argv, ["opencode", "--model", "workbench/m"])
            self.assertEqual(self.files(), [])
            native = self.settings(provider="Native login", auth_provider="anthropic")
            argv, env, _ = connections.prepare(native, "opencode")
            self.assertEqual(argv, ["opencode"])
            self.assertNotIn("OPENCODE_CONFIG_CONTENT", env)
            self.assertNotIn("WORKBENCH_SESSION_KEY", env)
            choice = native | dict(remote_model="anthropic/claude-x")
            argv, env, _ = connections.prepare(choice, "opencode")
            self.assertEqual(argv, ["opencode", "--model", "anthropic/claude-x"])
            self.assertNotIn("OPENCODE_CONFIG_CONTENT", env)
            argv, _, _ = connections.prepare(
                choice | dict(remote_model="claude-x"), "OMP"
            )
            self.assertEqual(
                argv,
                [
                    "opencode",
                    "--provider",
                    "anthropic",
                    "--model",
                    "anthropic/claude-x",
                ],
            )

    def test_refuses_a_key_or_model_opencode_would_expand(self):
        s = self.settings(
            provider="Attached endpoint", endpoint=LOCAL, remote_model="m", prompt=""
        )
        hostile = (
            "{file:~/.ssh/id_ed25519}",
            'x"}}}, "mcp": {"run": {"type": "local"}}, "provider": {"w": {"options": {"k": "y',
            "back\\slash",
            "tab\tkey",
            "close}",
        )
        with patch.object(connections, "command", return_value=["opencode"]):
            for token in hostile:
                with patch.object(connections, "secret", return_value=token):
                    with self.assertRaisesRegex(
                        ValueError, "opencode cannot safely receive.*replace the key"
                    ) as caught:
                        connections.prepare(s, "opencode")
                    self.assertNotIn(token, str(caught.exception))
                    env = connections.prepare(s, "Claude Code")[1]
                    self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], token)
            reference = s | dict(remote_model="{file:~/.ssh/id_ed25519}")
            with self.assertRaisesRegex(ValueError, "model ID that contains {"):
                connections.prepare(reference, "opencode")
            path = "C:\\models\\my model.gguf"
            with patch.object(connections, "secret", return_value="sk-ok_1.2-3+/=:~"):
                _, env, _ = connections.prepare(s | dict(remote_model=path), "opencode")
        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(list(config["provider"]["workbench"]["models"]), [path])
        self.assertEqual(env["WORKBENCH_SESSION_KEY"], "sk-ok_1.2-3+/=:~")
        self.assertEqual(self.files(), [])


class LaunchTests(Case):
    def hosted(self, **extra):
        return self.settings(provider="OpenRouter", remote_model="m", **extra)

    def run_with(self, call, shim=None):
        with (
            patch.object(
                connections, "command", side_effect=lambda name: shim or [name]
            ),
            patch.object(connections, "secret", return_value="fixture"),
            patch.object(connections, "omp_command", return_value=["omp"]) as omp,
        ):
            return call(), omp

    def test_prepare_stops_at_the_consent_gate(self):
        self.gate.side_effect = ValueError(GATE)
        native = self.settings(provider="Native login")
        with (
            patch.object(connections, "secret") as secret,
            patch.object(connections, "start") as start,
        ):
            for s, harness in (
                (self.hosted(), "OMP"),
                (self.hosted(), "Claude Code"),
                (native, "Codex"),
                (self.settings(), "Pi"),
            ):
                with self.assertRaises(ValueError) as caught:
                    self.run_with(lambda: connections.prepare(s, harness))
                self.assertEqual(str(caught.exception), GATE)
                self.assertEqual(self.gate.call_args.args[1], harness)
            secret.assert_not_called()
            start.assert_not_called()
        self.tools.assert_not_called()
        self.assertEqual(self.files(), [])
        with self.assertRaisesRegex(ValueError, "Unknown harness"):
            connections.prepare(native, "Cursor")
        self.assertEqual(self.gate.call_count, 4)

    def test_prepare_uses_the_folder_prompt(self):
        self.gate.side_effect = lambda s, harness=None: s | {"prompt": "Folder rules."}
        s = self.hosted(prompt="Everywhere.")
        (argv, _, _), _ = self.run_with(lambda: connections.prepare(s, "Claude Code"))
        self.assertEqual(
            argv[argv.index("--append-system-prompt") + 1], "Folder rules."
        )
        self.assertEqual(s["prompt"], "Everywhere.")

    def test_project_servers_follow_the_executable(self):
        self.tools.return_value = tools(
            argv=["--mcp-config", "/x/mcp.json"],
            env={"DOCS_TOKEN": "v", "WORKBENCH_SESSION_KEY": "planted"},
            servers=["docs"],
        )
        s, sent = self.hosted(), []
        (argv, env, _), _ = self.run_with(
            lambda: connections.prepare(s, "Claude Code", sent=sent)
        )
        self.assertEqual(argv[:4], ["claude", "--mcp-config", "/x/mcp.json", "--model"])
        self.assertGreater(argv.index("--append-system-prompt"), 3)
        self.assertEqual(env["DOCS_TOKEN"], "v")
        self.assertEqual(env["WORKBENCH_SESSION_KEY"], "fixture")
        self.assertEqual(sent, ["docs"])
        settings, harness, home = self.tools.call_args.args
        self.assertEqual(harness, "Claude Code")
        self.assertEqual(home, Path(env["CLAUDE_CONFIG_DIR"]).parent)
        self.assertEqual(home.parent, self.data / "clients")
        self.assertEqual(settings["workspace"], str(self.work))
        self.assertNotIn("DOCS_TOKEN", os.environ)
        pair = ["-c", 'mcp_servers.docs.url="https://mcp.example.com/mcp"']
        self.tools.return_value = tools(argv=pair, env={"DOCS_TOKEN": "v"})
        (argv, env, _), _ = self.run_with(lambda: connections.prepare(s, "Codex"))
        self.assertEqual(argv[:4], ["codex", *pair, "--model"])
        self.assertEqual(env["DOCS_TOKEN"], "v")
        resolved = ["node", "C:\\x\\codex.js"]
        (argv, _, _), _ = self.run_with(
            lambda: connections.prepare(s, "Codex"), shim=resolved
        )
        self.assertEqual(argv[:5], [*resolved, *pair, "--model"])
        self.tools.return_value = tools(env={"DOCS_TOKEN": "v"}, servers=["docs"])
        for harness, lead in (("Pi", "pi"), ("opencode", "opencode"), ("OMP", "omp")):
            (argv, env, _), _ = self.run_with(lambda: connections.prepare(s, harness))
            self.assertEqual(argv[0], lead)
            self.assertNotIn("--mcp-config", argv)
            self.assertEqual(env["DOCS_TOKEN"], "v")
            self.assertEqual(self.tools.call_args.args[1], harness)

    def test_native_login_passes_no_home(self):
        self.tools.return_value = tools(
            argv=["--mcp-config", "/n/mcp.json"], env={"DOCS_TOKEN": "v"}
        )
        native = self.settings(provider="Native login", remote_model="opus")
        (argv, env, _), _ = self.run_with(
            lambda: connections.prepare(native, "Claude Code")
        )
        self.assertEqual(
            argv, ["claude", "--mcp-config", "/n/mcp.json", "--model", "opus"]
        )
        self.assertEqual(env["DOCS_TOKEN"], "v")
        self.assertEqual(self.tools.call_args.args[1:], ("Claude Code", None))
        self.assertNotIn("WORKBENCH_SESSION_KEY", env)
        self.assertEqual(self.files(), [])
        self.tools.reset_mock()
        missing = ValueError("claude is missing. Open Setup to install it.")
        with patch.object(connections, "command", side_effect=missing):
            with self.assertRaisesRegex(ValueError, "claude is missing"):
                connections.prepare(native, "Claude Code")
        self.tools.assert_not_called()

    def test_unresolved_batch_shim_refuses_generated_arguments(self):
        pair = ["-c", 'mcp_servers.docs.command="npx"']
        self.tools.return_value = tools(argv=pair)
        native = self.settings(provider="Native login", remote_model="gpt-5")
        refusal = "Codex is installed.*cannot safely receive this folder's MCP tools"
        with patch.object(connections, "WINDOWS", True):
            for s in (native, self.hosted()):
                with self.assertRaisesRegex(ValueError, refusal):
                    self.run_with(
                        lambda: connections.prepare(s, "Codex"),
                        shim=["C:\\x\\codex.cmd"],
                    )
            resolved = ["node", "C:\\x\\codex.js"]
            (argv, _, _), _ = self.run_with(
                lambda: connections.prepare(native, "Codex"), shim=resolved
            )
            self.assertEqual(argv[:4], [*resolved, *pair])
            self.tools.return_value = tools()
            (argv, _, _), _ = self.run_with(
                lambda: connections.prepare(native, "Codex"),
                shim=["C:\\x\\codex.cmd"],
            )
            self.assertEqual(argv[1:3], ["--model", "gpt-5"])
        self.tools.return_value = tools(argv=pair)
        with patch.object(connections, "WINDOWS", False):
            (argv, _, _), _ = self.run_with(
                lambda: connections.prepare(native, "Codex"), shim=["/bin/codex.cmd"]
            )
        self.assertEqual(argv[:3], ["/bin/codex.cmd", *pair])

    def test_agent_environment_reaches_loopback_without_a_proxy(self):
        os.environ.pop("no_proxy", None)
        os.environ.update(
            HTTP_PROXY="http://proxy.example:3128",
            NO_PROXY="corp.example",
            WORKBENCH_CALLER_CWD=str(self.data),
        )
        native = self.settings(provider="Native login")
        for s, harness in ((native, "Codex"), (self.hosted(), "Claude Code")):
            (_, env, _), _ = self.run_with(lambda: connections.prepare(s, harness))
            for name in ("NO_PROXY", "no_proxy"):
                self.assertEqual(env[name], "corp.example,127.0.0.1,localhost,::1")
            self.assertEqual(env["HTTP_PROXY"], "http://proxy.example:3128")
            self.assertNotIn("WORKBENCH_CALLER_CWD", env)
        self.assertEqual(os.environ["NO_PROXY"], "corp.example")
        self.assertEqual(os.environ["WORKBENCH_CALLER_CWD"], str(self.data))

    def test_ready_session_starts_what_the_session_received(self):
        web = Mock()
        s = self.hosted(harness="Claude Code", firecrawl=True, blender=True)
        self.tools.return_value = tools(servers=["search", "firecrawl", "blender"])
        with (
            patch.dict(sys.modules, {"cli": Mock(web=web)}),
            patch.object(runtime, "ensure_blender") as blender,
            patch.object(
                providers, "request", side_effect=requests.ConnectionError()
            ) as probe,
        ):
            (argv, _, cwd), _ = self.run_with(lambda: connections.ready_session(s))
            self.assertEqual((argv[0], cwd), ("claude", self.work))
            probe.assert_called_once_with("GET", "http://127.0.0.1:3002/", timeout=2)
            web.assert_called_once_with(True)
            blender.assert_called_once_with()
            probe.side_effect = None
            probe.return_value = Mock(ok=True, text="Firecrawl API")
            self.run_with(lambda: connections.ready_session(s))
            web.assert_called_once()
            for mock in (web, blender, probe):
                mock.reset_mock()
            off = s | dict(firecrawl=False, blender=False)
            self.run_with(lambda: connections.ready_session(off))
            self.tools.return_value = tools(servers=["search"])
            for harness in ("Claude Code", "OMP"):
                self.run_with(
                    lambda: connections.ready_session(s | dict(harness=harness))
                )
            for mock in (web, blender, probe):
                mock.assert_not_called()
            self.tools.return_value = tools(servers=["firecrawl", "blender"])
            probe.side_effect = requests.ConnectionError()
            self.run_with(lambda: connections.ready_session(s | dict(harness="OMP")))
            web.assert_called_once_with(True)
            blender.assert_called_once_with()

    def test_tunnelled_omp_profile_survives_a_new_forwarded_port(self):
        ident = "ab" * 8
        self.attached(
            host="u@box", remote_port=8000, endpoint="http://127.0.0.1:51877/v1"
        )
        s = self.settings(
            provider="Attached endpoint", connection_id=ident, remote_model="coder"
        )
        calls = []
        with patch.object(machines, "forward", side_effect=[40999, 41000]):
            for _ in range(2):
                _, omp = self.run_with(lambda: connections.prepare(s, "OMP"))
                calls.append(omp.call_args.kwargs)
        self.assertEqual(
            [c["base"] for c in calls],
            ["http://127.0.0.1:40999/v1", "http://127.0.0.1:41000/v1"],
        )
        self.assertEqual([c["anchor"] for c in calls], [ident, ident])
        self.assertEqual(calls[0]["profile_name"], calls[1]["profile_name"])
        direct = self.settings(
            provider="Attached endpoint", endpoint=LOCAL, remote_model="coder"
        )
        _, omp = self.run_with(lambda: connections.prepare(direct, "OMP"))
        self.assertEqual(omp.call_args.kwargs["base"], LOCAL)
        self.assertNotIn("anchor", omp.call_args.kwargs)
        self.assertIn("anchor", inspect.signature(runtime.omp_command).parameters)

    def test_gate_tools_and_agent_share_one_folder(self):
        (self.work / "inner").mkdir()
        typed = str(self.work / "inner" / "..") + os.sep
        native = self.settings(provider="Native login") | dict(workspace=typed)
        hosted = self.hosted() | dict(workspace=typed)
        for s, harness in ((hosted, "Claude Code"), (native, "Codex"), (hosted, "OMP")):
            (_, _, cwd), omp = self.run_with(lambda: connections.prepare(s, harness))
            self.assertEqual(cwd, self.work)
            for stub in (self.gate, self.tools):
                self.assertEqual(stub.call_args.args[0]["workspace"], str(self.work))
        self.assertEqual(omp.call_args.args[0]["workspace"], str(self.work))
        self.assertEqual(hosted["workspace"], typed)
        self.gate.side_effect = lambda s, harness=None: s | {"workspace": typed}
        _, omp = self.run_with(lambda: connections.prepare(hosted, "OMP"))
        self.assertEqual(omp.call_args.args[0]["workspace"], str(self.work))
        for stub in (self.gate, self.tools):
            stub.reset_mock()
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.work)
        absent = self.hosted()
        del absent["workspace"]
        refused = [(absent, paths.BAD_FOLDER)] + [
            (self.hosted() | dict(workspace=value), sentence)
            for value, sentence in (
                ("", paths.BAD_FOLDER),
                (None, paths.BAD_FOLDER),
                (str(self.work / "gone"), paths.BAD_FOLDER),
                (".", paths.RELATIVE),
                ("inner", paths.RELATIVE),
            )
        ]
        for s, sentence in refused:
            for pick in (s, s | dict(provider="Native login")):
                with self.assertRaises(ValueError) as caught:
                    self.run_with(lambda: connections.prepare(pick, "Claude Code"))
                self.assertEqual(str(caught.exception), sentence)
        self.gate.assert_not_called()
        self.tools.assert_not_called()


class WiredTests(Case):
    wired = True

    def test_project_setup_gates_the_folder_and_delivers_tools(self):
        s = self.settings(provider="OpenRouter", remote_model="m", search=True)
        with (
            patch.object(connections, "command", side_effect=lambda name: [name]),
            patch.object(connections, "secret", return_value="fixture"),
        ):
            sent = []
            argv, env, _ = connections.prepare(s, "Claude Code", sent=sent)
            self.assertEqual(
                (argv[0], argv[1], argv[3]), ("claude", "--mcp-config", "--model")
            )
            self.assertEqual(sent, ["search"])
            file = Path(argv[2])
            self.assertEqual(file.parent.parent, Path(env["CLAUDE_CONFIG_DIR"]).parent)
            listed = json.loads(file.read_text(encoding="utf8"))["mcpServers"]
            self.assertEqual(list(listed), ["search"])
            argv, _, _ = connections.prepare(s, "Codex")
            self.assertEqual(argv[1], "-c")
            self.assertTrue(argv[2].startswith("mcp_servers.search.command="))
            self.assertEqual(connections.prepare(s, "opencode")[0][1], "--model")
            (self.work / ".mcp.json").write_text(
                json.dumps({"mcpServers": {"x": {"command": "npx"}}}), encoding="utf8"
            )
            native = s | dict(provider="Native login")
            for pick in (s, native):
                with self.assertRaises(ValueError) as caught:
                    connections.prepare(pick, "Claude Code")
                self.assertEqual(str(caught.exception), project_setup.NEEDS_CONSENT)
        for path in self.files():
            self.assertNotIn(b"fixture", path.read_bytes(), path)

    def stub_clis(self):
        self.use(patch.object(connections, "command", side_effect=lambda name: [name]))
        self.use(patch.object(connections, "secret", return_value="fixture"))
        self.use(patch.object(connections, "omp_command", return_value=["omp"]))

    def test_built_in_tools_reach_every_agent_that_takes_servers(self):
        s = self.settings(provider="OpenRouter", remote_model="m", search=True)
        native = s | dict(provider="Native login")
        takes = [(s, "OMP"), (s, "Pi"), (s, "Claude Code"), (s, "Codex")]
        takes += [(native, "Claude Code"), (native, "Codex")]
        own = [(s, "opencode"), (native, "Hermes"), (native, "OMP"), (native, "Pi")]
        self.stub_clis()
        for names, picks in ((["search"], takes), ([], own)):
            for pick, harness in picks:
                sent = []
                connections.prepare(pick, harness, sent=sent)
                self.assertEqual(sent, names, (pick["provider"], harness))
        _, env, _ = connections.prepare(s, "Pi")
        file = Path(env["PI_CODING_AGENT_DIR"]) / "mcp.json"
        listed = json.loads(file.read_text(encoding="utf8"))["mcpServers"]
        self.assertEqual(list(listed), ["search"])
        argv, env, _ = connections.prepare(native, "Claude Code")
        self.assertEqual((argv[0], argv[1]), ("claude", "--mcp-config"))
        self.assertEqual(Path(argv[2]).parents[2], self.data / "clients")
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)
        argv, _, _ = connections.prepare(native, "Codex")
        self.assertEqual(argv[1], "-c")
        self.assertTrue(argv[2].startswith("mcp_servers.search.command="))
        self.assertEqual(argv[-2:], ["--sandbox", "workspace-write"])

    def test_codex_command_line_holds_only_what_project_setup_rendered(self):
        manifest = self.data / "extra.json"
        servers = {
            "db": {"command": "npx", "args": ["-y", "db-mcp", "--token=in-args"]},
            "feed": {"type": "sse", "url": "https://mcp.example.com/sse"},
        }
        manifest.write_text(json.dumps({"mcpServers": servers}), encoding="utf8")
        s = self.settings(provider="OpenRouter", remote_model="m", search=True)
        s["extra_mcp"] = str(manifest)
        self.stub_clis()
        for pick in (s, s | dict(provider="Native login")):
            sent = []
            argv, env, _ = connections.prepare(pick, "Codex", sent=sent)
            native = pick["provider"] == "Native login"
            home = None if native else Path(env["CODEX_HOME"]).parent
            extra = project_setup.launch_mcp(pick, "Codex", home)
            self.assertEqual(argv[1 : 1 + len(extra["argv"])], extra["argv"])
            self.assertEqual(
                [item for item in argv if item.startswith("mcp_servers.")],
                extra["argv"][1::2],
            )
            self.assertEqual(sent, extra["servers"])
            self.assertIn("search", sent)
            held = [row["name"] for row in extra["skipped"]]
            self.assertEqual(sorted(held), ["db", "feed"])
            for name in held:
                self.assertNotIn(name, sent)
                self.assertFalse(
                    [item for item in argv if f"mcp_servers.{name}." in item]
                )
            for text in (*argv, *env.values()):
                self.assertNotIn("in-args", text)
        sent = []
        connections.prepare(s, "Claude Code", sent=sent)
        self.assertEqual(sent, ["search", "db", "feed"])

    def test_no_spelling_of_the_folder_passes_the_gate(self):
        (self.work / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"x": {"command": "npx"}}}), encoding="utf8"
        )
        (self.work / "inner").mkdir()
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.work)
        self.stub_clis()
        s = self.settings(provider="OpenRouter", remote_model="m")
        spellings = (
            (str(self.work / "inner" / "..") + os.sep, project_setup.NEEDS_CONSENT),
            (".", paths.RELATIVE),
            (os.path.join("inner", ".."), paths.RELATIVE),
            ("", paths.BAD_FOLDER),
        )
        for typed, sentence in spellings:
            for pick in (s, s | dict(provider="Native login")):
                for harness in ("Claude Code", "OMP"):
                    with self.assertRaises(ValueError) as caught:
                        connections.prepare(pick | dict(workspace=typed), harness)
                    self.assertEqual(str(caught.exception), sentence, (typed, harness))
        self.assertEqual([path.name for path in self.files()], [".mcp.json"])


class VaultTests(Case):
    def test_status_by_backend(self):
        cases = [
            (backend("keyring.backends.fail", "Keyring"), False, "fail"),
            (backend("keyring.backends.null", "Keyring"), False, "null"),
            (backend("keyrings.alt.file", "PlaintextKeyring"), False, "file"),
            (backend("keyring.backends.macOS", "Keyring"), True, "macOS"),
            (
                backend("keyring.backends.SecretService", "Keyring"),
                True,
                "SecretService",
            ),
        ]
        for vault, available, name in cases:
            # The login keychain may be absent here (CI runs with a scratch HOME); that case has its own test.
            with patch("keyring.get_keyring", return_value=vault), patch.object(
                connections, "_keychain_missing", return_value=False
            ):
                status = VAULT_STATUS()
            self.assertEqual(
                (status["available"], status["backend"]), (available, name)
            )
            self.assertEqual(bool(status["reason"]), not available)
        for platform, word in (
            ("linux", "Secret Service"),
            ("darwin", "login keychain"),
            ("win32", "Windows Credential Manager"),
            ("freebsd14", "Secret Service"),
        ):
            with (
                patch("keyring.get_keyring", side_effect=RuntimeError("no dbus")),
                patch.object(sys, "platform", platform),
            ):
                status = VAULT_STATUS()
            self.assertEqual((status["available"], status["backend"]), (False, "none"))
            self.assertIn(word, status["reason"])
        self.get.assert_not_called()
        self.set.assert_not_called()

    def test_store_falls_back_to_memory(self):
        s = {"provider": "OpenRouter"}
        slot = "https://openrouter.ai/api/v1"
        self.status.return_value = dict(NO_VAULT)
        self.assertEqual(connections.store_key(s, "sk-one"), "memory")
        self.set.assert_not_called()
        self.assertEqual(connections.secret(s), "sk-one")
        self.assertEqual(connections.secret_source(s), "memory")
        self.status.return_value = dict(VAULT)
        self.set.side_effect = keyring.errors.PasswordSetError("locked")
        self.assertEqual(connections.store_key(s, "sk-two"), "memory")
        self.assertEqual(connections.secret(s), "sk-two")
        self.assertEqual(self.keys, {})
        self.set.side_effect = lambda _, slot, value: self.keys.update({slot: value})
        self.assertEqual(connections.store_key(s, "sk-three"), "vault")
        self.assertEqual(connections._MEMORY, {})
        self.assertEqual(self.keys, {slot: "sk-three"})
        self.assertEqual(connections.secret_source(s), "vault")
        self.assertEqual(connections.secret(s), "sk-three")
        os.environ["OPENROUTER_API_KEY"] = "sk-env"
        self.assertEqual(connections._lookup(s), ("sk-env", "env"))
        for provider in ("Managed local", "Native login", "Nowhere"):
            with self.assertRaisesRegex(ValueError, "Select an API provider"):
                connections.store_key({"provider": provider}, "sk")
        self.assertEqual(
            connections._lookup({"provider": "Native login"}), ("", "none")
        )
        with patch.object(connections, "key", return_value="local-key"):
            self.assertEqual(
                connections.secret_source({"provider": "Managed local"}), "local"
            )

    def test_named_connection_environment_variable(self):
        named = {"provider": "OpenRouter", "connection_id": "abcdef0123456789"}
        os.environ["OPENROUTER_API_KEY"] = "sk-provider"
        with self.assertRaisesRegex(
            ValueError,
            "Set WORKBENCH_KEY_ABCDEF0123456789 or save a key for OpenRouter.",
        ):
            connections.secret(named)
        os.environ["WORKBENCH_KEY_ABCDEF0123456789"] = "sk-named"
        self.assertEqual(connections.secret(named), "sk-named")
        self.assertEqual(connections.secret_source(named), "env")
        self.assertEqual(connections.key_env(named), "WORKBENCH_KEY_ABCDEF0123456789")
        self.assertEqual(
            connections.key_env({"provider": "OpenRouter"}), "OPENROUTER_API_KEY"
        )
        legacy = {"provider": "Groq", "connection_id": "legacy-Groq"}
        self.assertEqual(connections.key_env(legacy), "GROQ_API_KEY")
        self.assertEqual(connections.key_env({"provider": "Attached endpoint"}), "")
        self.assertEqual(self.get.call_count, 1)
        del os.environ["OPENROUTER_API_KEY"]
        with self.assertRaisesRegex(ValueError, "Set OPENROUTER_API_KEY or save a key"):
            connections.secret({"provider": "OpenRouter"})
        self.assertEqual(
            connections.secret({"provider": "Attached endpoint", "endpoint": LOCAL}), ""
        )

    def test_named_variable_stays_with_its_connection(self):
        work = account_book.save(dict(provider="OpenRouter", name="Work"))
        ident = work["connection_id"]
        os.environ["WORKBENCH_KEY_" + ident.upper()] = "sk-or-private"
        self.assertEqual(connections._lookup(work), ("sk-or-private", "env"))
        typed = {"provider": "OpenRouter", "connection_id": ident}
        self.assertEqual(connections.secret(typed), "sk-or-private")
        elsewhere = {
            "provider": "Attached endpoint",
            "connection_id": ident,
            "endpoint": "https://elsewhere.example/v1",
        }
        self.assertEqual(connections._lookup(elsewhere), ("", "none"))
        other = {"provider": "Groq", "connection_id": ident}
        self.assertEqual(connections._lookup(other), ("", "none"))
        with self.assertRaisesRegex(ValueError, "save a key for Groq"):
            connections.secret(other)
        with patch.object(
            providers, "request", return_value=reply(payload={"data": []})
        ) as request:
            connections.discover(elsewhere)
        self.assertEqual(
            request.call_args.args[1], "https://elsewhere.example/v1/models"
        )
        self.assertEqual(request.call_args.kwargs["headers"], {})
        box = self.attached()
        os.environ["WORKBENCH_KEY_" + "AB" * 8] = "sk-box"
        self.assertEqual(connections.secret(box), "sk-box")
        moved = box | {"endpoint": "https://elsewhere.example/v1"}
        self.assertEqual(connections.secret(moved), "")
        self.assertEqual(connections.secret(connections.bound(moved)), "sk-box")

    def test_backend_errors_outside_keyring_are_contained(self):
        class BackendError(Exception):
            pass

        s = {"provider": "OpenRouter"}
        slot = "https://openrouter.ai/api/v1"
        named = account_book.save(dict(provider="OpenRouter", name="Work"))
        self.get.side_effect = BackendError(1312, "CredRead")
        self.assertEqual(connections._lookup(s), ("", "none"))
        self.assertIs(connections.forget_key(s), False)
        rows = {row["connection_id"]: row for row in account_book.available()}
        self.assertEqual(
            (rows[named["connection_id"]]["ready"], list(rows)),
            (False, [named["connection_id"]]),
        )
        self.set.side_effect = BackendError(1783, "CredWrite")
        self.assertEqual(connections.store_key(s, "sk-one"), "memory")
        self.assertEqual(connections.secret(s), "sk-one")
        out = account_book.remove(named["connection_id"])
        self.assertEqual((out["removed"], out["key_removed"]), (True, False))
        self.assertEqual(account_book.entries(), {})
        self.get.side_effect = lambda _, slot: self.keys.get(slot)
        self.keys[slot] = "sk-vault"
        self.delete.side_effect = BackendError(5, "CredDelete")
        self.assertIs(connections.forget_key(s), False)
        self.assertEqual(connections._MEMORY, {})

    def test_forget_never_raises(self):
        s = {"provider": "OpenRouter"}
        slot = "https://openrouter.ai/api/v1"
        connections._MEMORY[slot] = "sk-memory"
        self.get.side_effect = keyring.errors.KeyringLocked("locked")
        self.assertIs(connections.forget_key(s), False)
        self.assertEqual(connections._MEMORY, {})
        self.status.return_value = dict(NO_VAULT)
        self.assertIs(connections.forget_key(s), True)
        self.status.return_value = dict(VAULT)
        self.get.side_effect = lambda _, slot: self.keys.get(slot)
        self.assertIs(connections.forget_key(s), True)
        self.delete.assert_not_called()
        self.keys[slot] = "sk-vault"
        connections._MEMORY[slot] = "sk-memory"
        self.assertIs(connections.forget_key(s), True)
        self.delete.assert_called_once_with(connections.SERVICE, slot)
        self.assertEqual((self.keys, connections._MEMORY), ({}, {}))
        self.keys[slot] = "sk-vault"
        self.delete.side_effect = keyring.errors.PasswordDeleteError("denied")
        self.assertIs(connections.forget_key(s), False)
        bad = {"provider": "Attached endpoint", "endpoint": "http://example.org/v1"}
        self.assertIs(connections.forget_key(bad), False)

    def test_tunnel_slot_and_bound(self):
        ident = "ab" * 8
        row = self.attached(
            host="u@box", remote_port=8000, endpoint="http://127.0.0.1:51877/v1"
        )
        self.assertEqual(connections.vault_slot(row), "ssh://u@box:8000#" + ident)
        moved = row | {"endpoint": "http://127.0.0.1:40000/v1"}
        self.assertEqual(connections.vault_slot(moved), "ssh://u@box:8000#" + ident)
        typed = {
            "provider": "Attached endpoint",
            "connection_id": ident,
            "endpoint": "https://evil.example/v1",
            "host": "evil",
            "remote_port": 1,
            "workspace": "kept",
        }
        seen = connections.bound(typed)
        self.assertEqual(
            (seen["endpoint"], seen["host"], seen["remote_port"], seen["workspace"]),
            ("http://127.0.0.1:51877/v1", "u@box", 8000, "kept"),
        )
        self.assertEqual(typed["host"], "evil")
        self.attached(host=None, remote_port=None, endpoint="http://127.0.0.1:51877/v1")
        seen = connections.bound(typed)
        self.assertEqual(seen["endpoint"], "http://127.0.0.1:51877/v1")
        self.assertNotIn("host", seen)
        self.assertNotIn("remote_port", seen)
        self.assertEqual(
            connections.vault_slot(seen), "http://127.0.0.1:51877/v1#" + ident
        )
        loose = connections.bound(typed | {"connection_id": "cd" * 8})
        self.assertEqual(loose["endpoint"], "https://evil.example/v1")
        self.assertNotIn("host", loose)
        hosted = connections.bound(
            {"provider": "OpenAI", "connection_id": ident, "host": "x"}
        )
        self.assertEqual(hosted, {"provider": "OpenAI", "connection_id": ident})
        self.assertEqual(
            connections.vault_slot(
                {"provider": "OpenAI", "connection_id": "legacy-OpenAI"}
            ),
            "https://api.openai.com/v1",
        )
        self.assertEqual(
            connections.bound(
                {"provider": "Attached endpoint", "connection_id": ["x"]}
            )["connection_id"],
            ["x"],
        )

    def test_key_message(self):
        named = {"provider": "OpenRouter", "connection_id": "abcdef0123456789"}
        self.assertEqual(
            connections.key_message("vault", named),
            "Key saved in the system credential vault.",
        )
        self.assertIn(
            "set WORKBENCH_KEY_ABCDEF0123456789 in your environment",
            connections.key_message("memory", named),
        )
        automatic = connections.key_message("memory", {"provider": "OpenRouter"})
        self.assertIn("kept only until Agent Workbench closes.", automatic)
        self.assertIn("set OPENROUTER_API_KEY in your environment", automatic)
        typed = connections.key_message("memory", {"provider": "Attached endpoint"})
        self.assertTrue(typed.endswith("closes."))


class SlotTests(Case):
    def test_forwarded_port_keeps_client_home(self):
        ident = "ab" * 8
        self.attached(context=8192, remote_model="local-agent")
        homes, bases = [], []
        with (
            patch.object(machines, "labels", return_value={"node": "Test node"}),
            patch.object(machines, "identity", return_value=self.data / ident / "key"),
            patch.object(
                machines,
                "tunnel",
                side_effect=[
                    {"endpoint": "http://127.0.0.1:4444/v1"},
                    {"endpoint": "http://127.0.0.1:5555/v1"},
                ],
            ) as tunnel,
            patch.object(connections, "command", return_value=["claude"]),
        ):
            s = self.settings(
                provider="Attached endpoint",
                connection_id=ident,
                remote_model="local-agent",
            )
            for _ in range(2):
                env = connections.prepare(s, "Claude Code")[1]
                homes.append(env["CLAUDE_CONFIG_DIR"])
                bases.append(env["ANTHROPIC_BASE_URL"])
            self.assertEqual(tunnel.call_args.args, ("node",))
        self.assertEqual(bases, ["http://127.0.0.1:4444", "http://127.0.0.1:5555"])
        self.assertEqual(homes[0], homes[1])
        direct = self.settings(provider="Attached endpoint", remote_model="local-agent")
        with patch.object(connections, "command", return_value=["claude"]):
            first = connections.prepare(direct | dict(endpoint=LOCAL), "Claude Code")[1]
            other = connections.prepare(
                direct | dict(endpoint="http://127.0.0.1:8001/v1"), "Claude Code"
            )[1]
        self.assertNotEqual(first["CLAUDE_CONFIG_DIR"], other["CLAUDE_CONFIG_DIR"])

    def test_forward_reopens_an_attached_remote_port(self):
        ident = "ab" * 8
        row = self.attached(
            host="u@box", remote_port=8000, endpoint="http://127.0.0.1:51877/v1"
        )
        with (
            patch.object(machines, "forward", return_value=40999) as forward,
            patch.object(machines, "tunnel") as tunnel,
        ):
            self.assertEqual(
                connections._forward(ident, row), "http://127.0.0.1:40999/v1"
            )
            forward.assert_called_once_with("u@box", 8000)
            saved = account_book.entries()[ident]
            self.assertEqual(saved["endpoint"], "http://127.0.0.1:40999/v1")
            self.assertEqual((saved["host"], saved["remote_port"]), ("u@box", 8000))
            with patch.object(account_book, "save") as save:
                self.assertEqual(connections._forward(ident, saved), saved["endpoint"])
                save.assert_not_called()
            tunnel.assert_not_called()
            tunnel.return_value = {"endpoint": "http://127.0.0.1:4444/v1"}
            node = {"host": "u@box", "context": 8192, "endpoint": LOCAL}
            self.assertEqual(
                connections._forward(ident, node), "http://127.0.0.1:4444/v1"
            )
            tunnel.assert_called_once_with("u@box")
            self.assertIsNone(
                connections._forward(ident, {"context": 8192, "endpoint": LOCAL})
            )
        self.assertIsNone(connections._forward(ident, {"endpoint": LOCAL}))
        with self.assertRaisesRegex(ValueError, "Reconnect this endpoint"):
            connections.prepare(
                self.settings(provider="Attached endpoint", connection_id="cd" * 8),
                "Pi",
            )

    def test_forward_keeps_edits_made_while_connecting(self):
        ident = "ab" * 8
        row = self.attached(
            host="u@box", remote_port=8000, endpoint="http://127.0.0.1:51877/v1"
        )

        def renamed(host, port):
            edit = dict(name="Renamed", remote_model="other", harness="Codex")
            account_book.save(account_book.entries()[ident] | edit)
            return 40999

        with patch.object(machines, "forward", side_effect=renamed):
            self.assertEqual(
                connections._forward(ident, row), "http://127.0.0.1:40999/v1"
            )
        saved = account_book.entries()[ident]
        self.assertEqual(
            (saved["name"], saved["remote_model"], saved["harness"], saved["endpoint"]),
            ("Renamed", "other", "Codex", "http://127.0.0.1:40999/v1"),
        )
        self.assertEqual((saved["host"], saved["remote_port"]), ("u@box", 8000))

        def removed(host, port):
            with patch.object(machines, "drop_forward"):
                account_book.remove(ident)
            return 41000

        with patch.object(machines, "forward", side_effect=removed):
            self.assertEqual(
                connections._forward(ident, saved), "http://127.0.0.1:41000/v1"
            )
        self.assertEqual(account_book.entries(), {})


class DiscoverTests(Case):
    def listing(self, s, outcome):
        with patch.object(providers, "request", side_effect=[outcome]) as request:
            try:
                return connections.discover(s), request
            except ValueError as error:
                return str(error), request

    def test_error_mapping(self):
        hosted = {"provider": "OpenRouter"}
        typed = {"provider": "Attached endpoint", "endpoint": LOCAL}
        origin = "http://127.0.0.1:8000"
        self.keys["https://openrouter.ai/api/v1"] = "sk-or"
        for s, outcome, wanted in [
            (
                hosted,
                requests.exceptions.SSLError(),
                "The secure connection to https://openrouter.ai failed.",
            ),
            (
                hosted,
                requests.exceptions.ReadTimeout(),
                "OpenRouter did not answer in time.",
            ),
            (
                typed,
                requests.exceptions.ConnectTimeout(),
                origin + " did not answer in time.",
            ),
            (
                typed,
                requests.exceptions.ConnectionError(),
                "Nothing answered at " + origin + ".",
            ),
            (
                typed,
                reply(302),
                origin + " redirected the request. Enter the final API address",
            ),
            (hosted, reply(401), "OpenRouter rejected the saved API key."),
            (typed, reply(403), origin + " needs an API key. Save its key"),
            (
                hosted,
                reply(404),
                "OpenRouter did not return a model list. Type the model ID",
            ),
            (typed, reply(404), "There is no model list at " + LOCAL + "/models."),
            (typed, reply(500), origin + " answered with HTTP 500."),
            (typed, reply(text=True), origin + " answered, but not with a model list."),
            (
                typed,
                reply(payload={"error": "x"}),
                origin + " answered, but not with a model list.",
            ),
        ]:
            message, request = self.listing(s, outcome)
            self.assertIsInstance(message, str, outcome)
            self.assertIn(wanted, message)
            self.assertEqual(request.call_args.kwargs["timeout"], (4, 20))
        self.assertEqual(request.call_args.args, ("GET", LOCAL + "/models"))
        self.assertEqual(request.call_args.kwargs["headers"], {})
        message, request = self.listing(hosted, reply(401))
        self.assertEqual(
            request.call_args.kwargs["headers"], {"Authorization": "Bearer sk-or"}
        )
        self.attached(context=8192)
        node = {"provider": "Attached endpoint", "connection_id": "ab" * 8}
        message, _ = self.listing(node, requests.exceptions.ConnectionError())
        self.assertIn("The SSH tunnel for this connection is not open.", message)
        with self.assertRaisesRegex(ValueError, "native CLI"):
            connections.discover({"provider": "Native login"})
        with self.assertRaisesRegex(ValueError, "Set OPENAI_API_KEY"):
            connections.discover({"provider": "OpenAI"})

    def test_shapes_ids_and_cache(self):
        typed = {"provider": "Attached endpoint", "endpoint": "localhost:8000/v1/"}
        rows = [
            {"id": "b", "context_length": 4096},
            {"id": "a"},
            {"id": "b"},
            {"id": "bad\nid"},
            {"id": "-flag"},
            {"id": 7},
            "junk",
        ]
        for payload in (
            {"data": rows},
            {"models": rows},
            rows,
            {"data": [{"name": "a"}, {"name": "b"}]},
        ):
            found, request = self.listing(typed, reply(payload=payload))
            self.assertEqual(found, ["a", "b"])
        self.assertEqual(request.call_args.args[1], "http://localhost:8000/v1/models")
        self.assertFalse((self.data / "model-context.json").exists())
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant"
        listed = [
            {"id": "claude-x", "context_window": 1000000},
            {"id": "claude-y"},
            {"id": "bad\tid", "context_length": 5},
        ]
        found, request = self.listing(
            {"provider": "Anthropic"}, reply(payload={"data": listed})
        )
        self.assertEqual(found, ["claude-x", "claude-y"])
        self.assertEqual(
            request.call_args.args[1], "https://api.anthropic.com/v1/models"
        )
        self.assertEqual(
            request.call_args.kwargs["headers"],
            {"x-api-key": "sk-ant", "anthropic-version": "2023-06-01"},
        )
        cache = json.loads((self.data / "model-context.json").read_text())
        self.assertEqual(cache, {"https://api.anthropic.com/v1": {"claude-x": 1000000}})
        self.assertNotIn("sk-ant", (self.data / "model-context.json").read_text())
        self.keys["https://api.openai.com/v1"] = "sk-oa"
        self.listing({"provider": "OpenAI"}, reply(payload={"data": [{"id": "gpt-x"}]}))
        self.assertEqual(
            json.loads((self.data / "model-context.json").read_text()), cache
        )
        with (
            patch.object(connections, "start") as start,
            patch.object(connections, "key", return_value="local-key"),
        ):
            s = self.settings()
            found, request = self.listing(
                s, reply(payload={"data": [{"id": "local-agent"}]})
            )
            start.assert_called_once_with(s)
        self.assertEqual(found, ["local-agent"])
        self.assertEqual(
            request.call_args.kwargs["headers"], {"Authorization": "Bearer local-key"}
        )


class AccountTests(Case):
    def test_rename_keeps_probe_fields(self):
        probed = dict(
            kind="ollama",
            protocols=dict(ALL),
            context_window=32768,
            context_source="server",
            probed=1795478400,
        )
        row = self.attached(**probed)
        self.assertEqual({k: row[k] for k in probed}, probed)
        renamed = account_book.save(
            dict(
                provider="Attached endpoint",
                connection_id=row["connection_id"],
                name="Renamed",
                endpoint=LOCAL,
                remote_model="coder",
                harness="Codex",
            )
        )
        self.assertEqual((renamed["name"], renamed["harness"]), ("Renamed", "Codex"))
        self.assertEqual({k: renamed[k] for k in probed}, probed)
        self.assertEqual(account_book.entries()[row["connection_id"]], renamed)
        echoed = account_book.save(renamed | dict(name="Echoed"))
        self.assertEqual({k: echoed[k] for k in probed}, probed)
        other = account_book.save(renamed | dict(remote_model="other"))
        self.assertNotIn("context_window", other)
        self.assertNotIn("context_source", other)
        self.assertEqual(other["protocols"], ALL)
        with self.assertRaisesRegex(ValueError, "Remove this connection and attach"):
            account_book.save(renamed | dict(endpoint="http://127.0.0.1:9/v1"))

    def test_untrusted_payload_cannot_set_probe_fields(self):
        base = dict(
            provider="Attached endpoint", name="Typed", endpoint=LOCAL, remote_model="m"
        )
        row = account_book.save(
            base
            | dict(
                protocols=dict(ALL),
                kind="vllm",
                host="u@box",
                remote_port=8000,
                probed=5,
                context_source="server",
                context=8192,
            )
        )
        for name in (
            "protocols",
            "kind",
            "host",
            "remote_port",
            "probed",
            "context_source",
            "context",
        ):
            self.assertNotIn(name, row)
        self.assertRegex(row["connection_id"], r"^[0-9a-f]{16}$")
        self.assertEqual(row["harness"], "OMP")
        for bad, message in (
            (dict(harness="Cursor"), "Choose a supported agent."),
            (dict(harness=None), "Choose a supported agent."),
            (dict(remote_model="bad\nid"), "model ID cannot be used"),
            (dict(remote_model="-flag"), "model ID cannot be used"),
            (dict(auth_provider="a&b"), "Choose a subscription"),
            (dict(provider="Managed local"), "Choose an API provider"),
            (dict(provider=None), "Choose an API provider"),
            (dict(connection_id=5), "Unknown connection."),
            (dict(connection_id="../x"), "Unknown connection."),
            (dict(endpoint="http://example.org/v1"), "through an SSH tunnel"),
            (dict(endpoint="http://127.0.0.1:1/v1&x"), "Use a plain API address"),
        ):
            with self.assertRaisesRegex(ValueError, message):
                account_book.save(base | bad)
        self.assertEqual(list(account_book.entries()), [row["connection_id"]])
        trusted = self.attached(
            kind="mystery",
            protocols={"chat": True, "responses": "yes", "extra": True},
            context_window=12,
            context_source="guess",
            remote_port=70000,
            probed="now",
        )
        self.assertEqual(
            trusted["protocols"], {"chat": True, "responses": None, "messages": None}
        )
        for name in (
            "kind",
            "context_window",
            "context_source",
            "remote_port",
            "probed",
            "host",
        ):
            self.assertNotIn(name, trusted)
        with self.assertRaisesRegex(ValueError, "SSH config alias"):
            self.attached(host="bad host")
        self.assertNotIn(
            "kind", self.attached(kind="workbench-node", host="u@box", context=8192)
        )

    def test_user_context_set_and_cleared(self):
        row = self.attached(kind="ollama", context_window=4096, context_source="server")
        typed = {
            k: row[k]
            for k in ("provider", "connection_id", "name", "endpoint", "remote_model")
        }
        saved = account_book.save(typed | dict(context_window=200000))
        self.assertEqual(
            (saved["context_window"], saved["context_source"]), (200000, "user")
        )
        self.assertEqual(account_book.save(typed)["context_window"], 200000)
        self.assertEqual(
            account_book.save(typed | dict(context_window=" 65536 "))["context_window"],
            65536,
        )
        for bad in (500, 10000001, "many", True, 4096.5):
            with self.assertRaisesRegex(
                ValueError, "between 1,024 and 10,000,000 tokens"
            ):
                account_book.save(typed | dict(context_window=bad))
        self.assertEqual(
            account_book.entries()[row["connection_id"]]["context_window"], 65536
        )
        for empty in ("", "  ", 0, None):
            account_book.save(typed | dict(context_window=65536))
            cleared = account_book.save(typed | dict(context_window=empty))
            self.assertNotIn("context_window", cleared)
            self.assertNotIn("context_source", cleared)
        self.assertEqual(cleared["kind"], "ollama")
        hosted = account_book.save(
            dict(provider="OpenRouter", name="Work", context_window=400000)
        )
        self.assertEqual(
            (hosted["context_window"], hosted["context_source"]), (400000, "user")
        )
        node = self.attached(connection_id="cd" * 8, context=8192)
        capped = account_book.save(node | dict(context_window=200000))
        self.assertEqual(capped["context"], 8192)
        self.assertNotIn("context_window", capped)

    def test_the_late_system_answer_belongs_to_its_model(self):
        row = self.attached(kind="llamacpp", late_system=False)
        self.assertIs(row["late_system"], False)
        typed = {
            k: row[k]
            for k in ("provider", "connection_id", "name", "endpoint", "remote_model")
        }
        self.assertIs(account_book.save(typed)["late_system"], False)
        # A form cannot clear the answer; only a probe through a trusted caller sets it.
        self.assertIs(account_book.save(typed | dict(late_system=True))["late_system"], False)
        other = account_book.save(typed | dict(remote_model="other"))
        self.assertNotIn("late_system", other)
        for odd in ("no", 0, None):
            self.assertNotIn("late_system", self.attached(late_system=odd))
        self.assertIs(self.attached(late_system=True)["late_system"], True)

    def test_echoed_server_context_stays_a_server_value(self):
        row = self.attached(kind="ollama", context_window=4096, context_source="server")
        typed = {
            k: row[k]
            for k in ("provider", "connection_id", "name", "endpoint", "remote_model")
        }
        for echo in (4096, "4096", " 4096 "):
            saved = account_book.save(typed | dict(context_window=echo))
            self.assertEqual(
                (saved["context_window"], saved["context_source"]), (4096, "server")
            )
        changed = account_book.save(typed | dict(context_window="8192"))
        self.assertEqual(
            (changed["context_window"], changed["context_source"]), (8192, "user")
        )
        kept = account_book.save(typed | dict(context_window="8192"))
        self.assertEqual(
            (kept["context_window"], kept["context_source"]), (8192, "user")
        )

    def test_remove(self):
        forget = self.use(patch.object(connections, "forget_key", return_value=True))
        work = account_book.save(dict(provider="OpenRouter", name="Work"))
        keep = account_book.save(dict(provider="Groq", name="Keep"))
        self.assertEqual(
            account_book.remove(work["connection_id"]),
            {"removed": True, "key_removed": True, "message": "Connection removed."},
        )
        forget.assert_called_once_with(work)
        self.assertEqual(list(account_book.entries()), [keep["connection_id"]])
        forget.return_value = False
        out = account_book.remove(keep["connection_id"])
        self.assertEqual((out["removed"], out["key_removed"]), (True, False))
        self.assertIn("vault refused to delete its key", out["message"])
        self.assertEqual(account_book.entries(), {})
        forget.return_value = True
        node = self.attached(context=8192)
        machine = self.attached(connection_id="cd" * 8, host="u@box")
        for row in (node, machine):
            with self.assertRaisesRegex(
                ValueError, "Remove this machine under Machines"
            ):
                account_book.remove(row["connection_id"])
        self.assertEqual(len(account_book.entries()), 2)
        forget.reset_mock()
        self.assertEqual(
            account_book.remove("legacy-OpenAI"),
            {
                "removed": False,
                "key_removed": True,
                "message": "The saved OpenAI key was removed.",
            },
        )
        forget.assert_called_once_with({"provider": "OpenAI"})
        os.environ["OPENAI_API_KEY"] = "sk-env"
        out = account_book.remove("legacy-OpenAI")
        self.assertEqual(
            out["message"],
            "The saved OpenAI key was removed; this connection stays listed while "
            "OPENAI_API_KEY is set in your environment.",
        )
        saved = account_book.save(
            dict(provider="OpenAI", connection_id="legacy-OpenAI")
        )
        out = account_book.remove("legacy-OpenAI")
        self.assertEqual(
            out["message"],
            "Connection removed; this connection stays listed while OPENAI_API_KEY is "
            "set in your environment.",
        )
        self.assertNotIn(saved["connection_id"], account_book.entries())
        forget.return_value = False
        self.assertIn(
            "refused to delete the saved Groq key",
            account_book.remove("legacy-Groq")["message"],
        )
        forget.return_value = True
        for unknown in ("ef" * 8, "legacy-Native login", "legacy-Nowhere", "", None, 5):
            with self.assertRaisesRegex(
                ValueError, "no longer exists. Refresh the list."
            ):
                account_book.remove(unknown)
        forget.reset_mock()
        native = account_book.save(
            dict(provider="Native login", name="Sub", auth_provider="anthropic")
        )
        self.assertTrue(account_book.remove(native["connection_id"])["key_removed"])
        forget.assert_not_called()

    def test_remove_closes_the_forward_of_an_attached_remote_server(self):
        forget = self.use(patch.object(connections, "forget_key", return_value=True))
        row = self.attached(host="u@box", remote_port=8000, context=65536)
        with patch.object(
            machines, "drop_forward", side_effect=RuntimeError("gone")
        ) as drop:
            out = account_book.remove(row["connection_id"])
        self.assertEqual(out["message"], "Connection removed.")
        drop.assert_called_once_with("u@box", 8000)
        forget.assert_called_once_with(row)
        self.assertEqual(account_book.entries(), {})
        typed = self.attached()
        with patch.object(machines, "drop_forward") as drop:
            account_book.remove(typed["connection_id"])
            drop.assert_not_called()

    def test_available_rows(self):
        os.environ["MISTRAL_API_KEY"] = "sk-mistral"
        named = account_book.save(dict(provider="OpenRouter", name="Work"))
        self.keys[connections.vault_slot(named)] = "sk-or"
        self.keys["https://api.groq.com/openai/v1"] = "sk-groq"
        self.attached(protocols={"chat": True, "responses": False, "messages": None})
        saved = json.loads((self.data / "connections.json").read_text())
        rows = {row["connection_id"]: row for row in account_book.available()}
        self.assertEqual(
            sorted(rows),
            sorted([named["connection_id"], "ab" * 8, "legacy-Groq", "legacy-Mistral"]),
        )
        for ident, ready, source in (
            (named["connection_id"], True, "vault"),
            ("legacy-Groq", True, "vault"),
            ("legacy-Mistral", True, "env"),
            ("ab" * 8, True, ""),
        ):
            self.assertEqual(
                (rows[ident]["ready"], rows[ident]["key_source"]), (ready, source)
            )
            self.assertEqual(list(rows[ident]["compat"]), connections.HARNESS)
        self.assertFalse(rows["ab" * 8]["compat"]["Codex"]["ok"])
        self.assertFalse(rows["legacy-Mistral"]["compat"]["Claude Code"]["ok"])
        self.assertEqual(
            rows["legacy-Mistral"]["endpoint"], "https://api.mistral.ai/v1"
        )
        documented = {
            row["openai_base"]
            for row in providers.SOURCES
            if row["kind"] == "hosted" and row["evidence"] == "documented"
        }
        asked = [call.args[1] for call in self.get.call_args_list]
        self.assertEqual(len(documented), 8)
        self.assertFalse([slot for slot in asked if slot.split("#")[0] in documented])
        self.assertEqual(len(asked), len(set(asked)))
        self.assertEqual(
            json.loads((self.data / "connections.json").read_text()), saved
        )
        self.assertNotIn("sk-", json.dumps(account_book.available()))
        del self.keys[connections.vault_slot(named)]
        again = {row["connection_id"]: row for row in account_book.available()}
        self.assertEqual(
            (
                again[named["connection_id"]]["ready"],
                again[named["connection_id"]]["key_source"],
            ),
            (False, "none"),
        )


class DispatchTests(Case):
    def setUp(self):
        super().setUp()
        self.saved = self.settings(
            provider="OpenRouter",
            connection_id="ef" * 8,
            remote_model="kept",
            endpoint=LOCAL,
        )
        self.use(
            patch.object(runtime, "settings", side_effect=lambda: dict(self.saved))
        )

    def test_table_compat_and_context(self):
        table = connections.dispatch("source-table", {})
        self.assertEqual(
            [row["id"] for row in table["providers"]], list(connections.PROVIDERS)
        )
        self.assertEqual(
            set(table["providers"][0]),
            {"id", "label", "kind", "key_env", "protocols", "evidence"},
        )
        self.assertEqual([h["id"] for h in table["harnesses"]], connections.HARNESS)
        self.assertEqual(table["kinds"]["ollama"], "Ollama")
        self.assertEqual(table["vault"], VAULT)
        self.assertEqual(
            connections.dispatch("source-compat", {"provider": "OpenAI"}),
            connections.compat("OpenAI"),
        )
        row = self.attached(
            protocols={"chat": True, "responses": False, "messages": True},
            kind="ollama",
            context_window=4096,
            context_source="server",
        )
        pick = {"provider": "Attached endpoint", "connection_id": row["connection_id"]}
        self.assertFalse(connections.dispatch("source-compat", pick)["Codex"]["ok"])
        self.keys[connections.vault_slot(row)] = "sk-box"
        self.live.return_value = 8192
        self.assertEqual(
            connections.dispatch(
                "source-context",
                pick | {"remote_model": "coder", "endpoint": "https://evil.example/v1"},
            ),
            {"context": 8192, "source": "server"},
        )
        self.live.assert_called_once_with(LOCAL, "ollama", "coder", "sk-box")
        self.assertEqual(
            connections.dispatch(
                "source-context", {"provider": "Groq", "remote_model": "x"}
            ),
            {"context": 128000, "source": "default"},
        )
        self.assertEqual(
            connections.dispatch("source-context", {"provider": "Native login"}),
            {"context": 32768, "source": "default"},
        )
        with (
            patch.object(connections, "start") as start,
            patch.object(connections, "key") as key,
        ):
            self.assertEqual(
                connections.dispatch("source-context", {"provider": "Managed local"}),
                {"context": 32768, "source": "local"},
            )
            start.assert_not_called()
            key.assert_not_called()
        for action, payload, message in (
            ("source-nope", {}, "Unknown source action."),
            ("endpoint-scan", {}, "Unknown source action."),
            (
                "source-compat",
                {"provider": "Nowhere"},
                "Choose a supported model source.",
            ),
            (
                "source-compat",
                {"provider": ["OpenAI"]},
                "Choose a supported model source.",
            ),
            (
                "source-context",
                {"provider": "OpenAI", "remote_model": "-x"},
                "model ID cannot be used",
            ),
            ("source-remove", {}, "no longer exists"),
        ):
            with self.assertRaisesRegex(ValueError, message):
                connections.dispatch(action, payload)
        self.assertEqual(
            connections.dispatch("source-compat", None),
            connections.compat("OpenRouter"),
        )

    def test_credential_actions(self):
        ask = {"provider": "OpenAI"}
        slot = "https://api.openai.com/v1"
        self.assertEqual(
            connections.dispatch("source-credential", ask),
            {"ready": False, "source": "none", "env": "OPENAI_API_KEY", "vault": VAULT},
        )
        for empty in ({}, {"key": "   "}, {"key": 5}):
            with self.assertRaisesRegex(ValueError, "Enter the provider API key."):
                connections.dispatch("source-key-save", ask | empty)
        with self.assertRaisesRegex(ValueError, "cannot be sent. Paste the key again."):
            connections.dispatch("source-key-save", ask | {"key": "sk\nbroken"})
        self.assertEqual(
            connections.dispatch("source-key-save", ask | {"key": " sk-oa\n"}),
            {"stored": "vault", "message": "Key saved in the system credential vault."},
        )
        self.assertEqual(self.keys, {slot: "sk-oa"})
        seen = connections.dispatch("source-credential", ask)
        self.assertEqual((seen["ready"], seen["source"]), (True, "vault"))
        self.assertNotIn("sk-oa", json.dumps(seen))
        self.assertEqual(
            connections.dispatch("source-key-forget", ask),
            {"removed": True, "ready": False, "source": "none"},
        )
        self.assertEqual(self.keys, {})
        self.status.return_value = dict(NO_VAULT)
        named = ask | {"connection_id": "12" * 8}
        out = connections.dispatch("source-key-save", named | {"key": "sk-named"})
        self.assertEqual(out["stored"], "memory")
        self.assertIn("WORKBENCH_KEY_1212121212121212", out["message"])
        self.assertEqual(connections._MEMORY, {slot + "#" + "12" * 8: "sk-named"})
        seen = connections.dispatch("source-credential", named)
        self.assertEqual(
            (seen["ready"], seen["source"], seen["env"], seen["vault"]),
            (True, "memory", "WORKBENCH_KEY_1212121212121212", NO_VAULT),
        )
        self.assertFalse(connections.dispatch("source-credential", ask)["ready"])
        row = self.attached()
        pick = {"provider": "Attached endpoint", "connection_id": row["connection_id"]}
        swapped = pick | {"endpoint": "https://evil.example/v1", "key": "sk-box"}
        connections.dispatch("source-key-save", swapped)
        self.assertEqual(
            connections._MEMORY[LOCAL + "#" + row["connection_id"]], "sk-box"
        )
        seen = connections.dispatch("source-credential", pick)
        self.assertEqual((seen["ready"], seen["source"]), (True, "memory"))
        self.assertEqual(
            connections.dispatch("source-key-forget", pick),
            {"removed": True, "ready": True, "source": "none"},
        )
        for provider in ("Native login", "Managed local"):
            with self.assertRaisesRegex(ValueError, "Select an API provider"):
                connections.dispatch(
                    "source-key-save", {"provider": provider, "key": "sk"}
                )
            with self.assertRaisesRegex(ValueError, "Select an API provider"):
                connections.dispatch("source-key-forget", {"provider": provider})
        with patch.object(connections, "key") as key:
            self.assertEqual(
                connections.dispatch(
                    "source-credential", {"provider": "Managed local"}
                )["source"],
                "local",
            )
            key.assert_not_called()
        native = connections.dispatch("source-credential", {"provider": "Native login"})
        self.assertEqual(
            (native["ready"], native["source"], native["env"]), (True, "none", "")
        )
        with patch.object(
            account_book, "remove", return_value={"removed": True}
        ) as remove:
            self.assertEqual(
                connections.dispatch("source-remove", {"connection_id": "ab" * 8}),
                {"removed": True},
            )
            remove.assert_called_once_with("ab" * 8)

    def test_saved_selection_is_used_only_without_a_provider(self):
        self.keys["https://openrouter.ai/api/v1#" + "ef" * 8] = "sk-saved"
        seen = connections.dispatch("source-credential", {})
        self.assertEqual(
            (seen["ready"], seen["env"]), (True, "WORKBENCH_KEY_" + "EF" * 8)
        )
        seen = connections.dispatch("source-credential", {"provider": "OpenRouter"})
        self.assertEqual((seen["ready"], seen["env"]), (False, "OPENROUTER_API_KEY"))
        with self.assertRaisesRegex(ValueError, "Enter the server address"):
            connections.dispatch("source-credential", {"provider": "Attached endpoint"})


class AuthTests(Case):
    def test_preflight_stops_before_browser(self):
        with (
            patch.object(provider_auth, "vault_status", return_value=dict(NO_VAULT)),
            patch.object(provider_auth.webbrowser, "open") as browser,
            patch.object(provider_auth, "store_key") as store,
        ):
            with self.assertRaisesRegex(
                ValueError, "set OPENROUTER_API_KEY before starting"
            ):
                provider_auth.connect_openrouter()
            browser.assert_not_called()
            store.assert_not_called()

    def test_silent_client_cannot_block_callback(self):
        held = []

        def browser(url):
            target = parse_qs(urlsplit(url).query)["callback_url"][0]

            def visit():
                held.append(
                    socket.create_connection(("127.0.0.1", urlsplit(target).port))
                )
                direct = build_opener(ProxyHandler({}))
                # The server listens on 127.0.0.1 only. Browsers fall back from ::1 at
                # once, but urllib tries localhost's ::1 first, and Windows takes about
                # two seconds to give up on a refused connection.
                ipv4 = target.replace("//localhost:", "//127.0.0.1:", 1)
                with direct.open(ipv4 + "?code=fixture", timeout=5) as response:
                    response.read()

            threading.Thread(target=visit, daemon=True).start()
            return True

        exchange = Mock(
            return_value=Mock(**{"json.return_value": {"key": "test-only"}})
        )
        with (
            patch.object(provider_auth, "CALLBACK_TIMEOUT", 0.2),
            patch.object(provider_auth, "vault_status", return_value=dict(VAULT)),
            patch.object(provider_auth.webbrowser, "open", browser),
            patch.object(provider_auth.requests, "post", exchange),
            patch.object(provider_auth, "store_key", return_value="vault") as store,
        ):
            began = time.monotonic()
            self.assertEqual(
                provider_auth.connect_openrouter({"provider": "OpenRouter"}), "vault"
            )
            self.assertLess(time.monotonic() - began, 2)
        store.assert_called_once_with({"provider": "OpenRouter"}, "test-only")
        self.assertEqual(exchange.call_args.kwargs["json"]["code"], "fixture")
        for client in held:
            client.close()

    def sign_in(self, exchange, connection=None):
        def browser(url):
            target = parse_qs(urlsplit(url).query)["callback_url"][0]

            def visit():
                direct = build_opener(ProxyHandler({}))
                with direct.open(target + "?code=fixture", timeout=5) as response:
                    response.read()

            threading.Thread(target=visit, daemon=True).start()
            return True

        with (
            patch.object(provider_auth, "vault_status", return_value=dict(VAULT)),
            patch.object(provider_auth.webbrowser, "open", side_effect=browser) as web,
            patch.object(provider_auth.requests, "post", exchange),
            patch.object(provider_auth, "store_key", return_value="vault") as store,
        ):
            try:
                return provider_auth.connect_openrouter(connection), web, store
            except Exception as error:
                return error, web, store

    def test_exchange_failures_are_one_sentence(self):
        refused = Mock()
        refused.raise_for_status.side_effect = requests.HTTPError(
            "403 Client Error: Forbidden for url: https://openrouter.ai/api/v1/auth/keys"
        )
        for answer in (
            Mock(side_effect=requests.ConnectionError("HTTPSConnectionPool")),
            Mock(side_effect=requests.Timeout("read timed out")),
            Mock(return_value=refused),
            Mock(return_value=reply(text=True)),
            Mock(return_value=reply(payload=["key"])),
            Mock(return_value=reply(payload={"key": 5})),
            Mock(return_value=reply(payload={})),
        ):
            error, _, store = self.sign_in(answer)
            self.assertIsInstance(error, ValueError)
            self.assertNotIsInstance(error, requests.RequestException)
            self.assertRegex(
                str(error), r"^OpenRouter did not .*(try again|paste an API key)"
            )
            self.assertNotIn("http", str(error))
            store.assert_not_called()
        done, _, store = self.sign_in(Mock(return_value=reply(payload={"key": "k"})))
        self.assertEqual(done, "vault")
        store.assert_called_once_with({"provider": "OpenRouter"}, "k")

    def test_only_an_openrouter_connection_can_receive_the_key(self):
        exchange = Mock(return_value=reply(payload={"key": "k"}))
        for connection in (
            {"provider": "Attached endpoint", "endpoint": "https://elsewhere.example"},
            {"provider": "Groq", "connection_id": "ab" * 8},
            ["OpenRouter"],
        ):
            error, web, store = self.sign_in(exchange, connection)
            self.assertIsInstance(error, ValueError)
            self.assertIn("Choose an OpenRouter connection", str(error))
            web.assert_not_called()
            store.assert_not_called()
        exchange.assert_not_called()
        named = {"provider": "OpenRouter", "connection_id": "ab" * 8}
        done, _, store = self.sign_in(exchange, named)
        self.assertEqual(done, "vault")
        store.assert_called_once_with(named, "k")


class CatalogTests(Case):
    catalog = False

    def setUp(self):
        super().setUp()
        PUBLIC_CATALOG.cache_clear()
        self.addCleanup(PUBLIC_CATALOG.cache_clear)

    def test_failure_is_memoised_and_uses_disk(self):
        kept = {
            "openai/gpt-x": {
                "architecture": {"input_modalities": ["text", "image"]},
                "context_length": 400000,
            }
        }
        runtime.write_json(visual_routes.CACHE, {"fetched": 1, "models": kept})
        with patch.object(
            visual_routes.requests, "get", side_effect=requests.ConnectionError()
        ) as get:
            self.assertEqual(visual_routes.public_catalog(7), kept)
            self.assertEqual(visual_routes.public_catalog(7), kept)
            get.assert_called_once()
            self.assertEqual(get.call_args.kwargs["timeout"], (3, 6))
            s = dict(provider="OpenAI", remote_model="gpt-x")
            with patch.object(visual_routes.time, "monotonic", return_value=7 * 900):
                self.assertEqual(
                    visual_routes.route(s),
                    dict(primary_image=True, reader="gpt-x", primary="gpt-x"),
                )
            get.assert_called_once()
        listing = {
            "data": [
                {
                    "id": "a/b",
                    "architecture": {
                        "input_modalities": ["text", "image"],
                        "tokenizer": "x",
                    },
                    "context_length": 1000,
                    "pricing": {"prompt": "0.1"},
                },
                {"id": "c/d", "architecture": "odd", "context_length": "big"},
                {"id": 5},
                "junk",
            ]
        }
        fresh = {
            "a/b": {
                "architecture": {"input_modalities": ["text", "image"]},
                "context_length": 1000,
            },
            "c/d": {"architecture": {"input_modalities": []}, "context_length": None},
        }
        with patch.object(
            visual_routes.requests, "get", return_value=reply(payload=listing)
        ):
            self.assertEqual(visual_routes.public_catalog(8), fresh)
        written = json.loads(visual_routes.CACHE.read_text())
        self.assertEqual(written["models"], fresh)
        self.assertIsInstance(written["fetched"], int)
        for broken in (
            reply(payload=[1]),
            reply(payload={"data": 5}),
            reply(text=True),
        ):
            PUBLIC_CATALOG.cache_clear()
            with patch.object(visual_routes.requests, "get", return_value=broken):
                self.assertEqual(visual_routes.public_catalog(9), fresh)

    def test_context_length_without_network(self):
        models = {
            "openai/gpt-x": {"context_length": 400000},
            "x": {"context_length": 5},
            "deepseek/chat": {"context_length": 0},
            "plain": {"context_length": 64000},
            "odd": "text",
        }
        with patch.object(visual_routes.requests, "get") as get:
            self.assertIsNone(visual_routes.context_length("OpenAI", "gpt-x"))
            runtime.write_json(visual_routes.CACHE, {"fetched": 1, "models": models})
            self.assertEqual(visual_routes.context_length("OpenAI", "gpt-x"), 400000)
            self.assertEqual(visual_routes.context_length("OpenRouter", "plain"), 64000)
            self.assertEqual(visual_routes.context_length("Groq", "plain"), 64000)
            for provider, model in (
                ("Mistral", "x"),
                ("Attached endpoint", "x"),
                ("Nowhere", "x"),
                ("DeepSeek", "chat"),
                ("OpenRouter", "odd"),
                ("OpenRouter", "missing"),
                ("OpenRouter", None),
            ):
                self.assertIsNone(visual_routes.context_length(provider, model))
            visual_routes.CACHE.write_text("{broken")
            self.assertIsNone(visual_routes.context_length("OpenAI", "gpt-x"))
            self.assertEqual(visual_routes.stored(), {})
            visual_routes.CACHE.write_text("[1]")
            self.assertEqual(visual_routes.stored(), {})
            get.assert_not_called()
        for provider in ("Mistral", "Attached endpoint", None):
            route = visual_routes.route(dict(provider=provider, remote_model="x"))
            self.assertEqual(route, dict(primary_image=False, reader="", primary="x"))
