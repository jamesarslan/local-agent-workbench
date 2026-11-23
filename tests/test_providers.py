import atexit
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import requests

if "runtime" not in sys.modules and "WORKBENCH_DATA" not in os.environ:
    os.environ["WORKBENCH_DATA"] = tempfile.mkdtemp(prefix="workbench-test-")
    atexit.register(shutil.rmtree, os.environ["WORKBENCH_DATA"], True)

import connections
import providers

SCHEMA = {
    "id",
    "label",
    "kind",
    "selectable",
    "openai_base",
    "anthropic_base",
    "anthropic_prefix",
    "anthropic_auth",
    "protocols",
    "models_route",
    "models_auth",
    "context_source",
    "context_default",
    "key_env",
    "catalog_prefix",
    "default_ports",
    "identity",
    "exposed",
    "evidence",
}
EXISTING = {
    "Managed local": None,
    "Attached endpoint": None,
    "OpenAI": "https://api.openai.com/v1",
    "OpenRouter": "https://openrouter.ai/api/v1",
    "Groq": "https://api.groq.com/openai/v1",
    "DeepSeek": "https://api.deepseek.com/v1",
    "Native login": None,
}
EXISTING_KEYS = {
    "OpenAI": "OPENAI_API_KEY",
    "OpenRouter": "OPENROUTER_API_KEY",
    "Groq": "GROQ_API_KEY",
    "DeepSeek": "DEEPSEEK_API_KEY",
}


class LoopbackServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


class ProviderTableTests(unittest.TestCase):
    def test_existing_names_and_bases_unchanged(self):
        bases = {
            r["id"]: r["openai_base"] for r in providers.SOURCES if r["selectable"]
        }
        keys = {
            r["id"]: r["key_env"] for r in providers.SOURCES if r["kind"] == "hosted"
        }
        for names, variables in (
            (bases, keys),
            (connections.PROVIDERS, connections.PROVIDER_KEYS),
        ):
            for name, base in EXISTING.items():
                self.assertIn(name, names)
                self.assertEqual(names[name], base)
            for name, variable in EXISTING_KEYS.items():
                self.assertEqual(variables[name], variable)
            self.assertEqual(list(names)[:2], ["Managed local", "Attached endpoint"])
            self.assertEqual(list(names)[-1], "Native login")
            self.assertFalse(
                set(variables) & {"Managed local", "Attached endpoint", "Native login"}
            )

    def test_every_row_has_the_schema(self):
        seen = set()
        for row in providers.SOURCES:
            self.assertEqual(set(row), SCHEMA, row["id"])
            self.assertIn(row["kind"], ("hosted", "local-server", "managed", "native"))
            self.assertIn(row["anthropic_auth"], ("bearer", "x-api-key"))
            self.assertIn(row["models_auth"], ("bearer", "x-api-key"))
            self.assertIn(row["evidence"], ("existing", "documented"))
            self.assertEqual(set(row["protocols"]), {"chat", "responses", "messages"})
            for value in row["protocols"].values():
                self.assertTrue(value is True or value is False or value is None)
            self.assertIsInstance(row["selectable"], bool)
            self.assertIsInstance(row["exposed"], bool)
            self.assertIsInstance(row["context_default"], int)
            self.assertIsInstance(row["default_ports"], list)
            self.assertLessEqual(set(row["identity"]), {"server", "owned_by", "realm"})
            self.assertNotIn(row["id"], seen)
            seen.add(row["id"])
        self.assertEqual(
            [
                r["id"]
                for r in providers.SOURCES
                if r["kind"] == "local-server" and r["selectable"]
            ],
            ["Attached endpoint"],
        )
        self.assertNotIn("Attached endpoint", providers.KINDS)
        self.assertIn("generic", providers.KINDS)

    def test_hosted_rows_are_https_with_key_env(self):
        hosted = [row for row in providers.SOURCES if row["kind"] == "hosted"]
        self.assertLessEqual(set(EXISTING_KEYS), {row["id"] for row in hosted})
        for row in hosted:
            self.assertTrue(row["openai_base"].startswith("https://"), row["id"])
            self.assertFalse(row["openai_base"].endswith("/"), row["id"])
            self.assertTrue(row["key_env"], row["id"])
            self.assertEqual(row["context_source"], "listing")
            self.assertEqual(row["context_default"], 128000)
            if row["anthropic_base"]:
                self.assertTrue(row["anthropic_base"].startswith("https://"), row["id"])
                self.assertFalse(row["anthropic_base"].endswith("/"), row["id"])
        variables = [row["key_env"] for row in hosted]
        self.assertEqual(len(variables), len(set(variables)))

    def test_sweep_ports_match_kind_defaults(self):
        defaults = {
            port for row in providers.KINDS.values() for port in row["default_ports"]
        }
        self.assertEqual(sorted(providers.SWEEP_PORTS), sorted(defaults))
        self.assertEqual(len(providers.SWEEP_PORTS), len(set(providers.SWEEP_PORTS)))
        # A second llama.cpp server commonly runs on 8081; the list stays short.
        self.assertIn(8081, providers.KINDS["llamacpp"]["default_ports"])
        self.assertLessEqual(len(providers.SWEEP_PORTS), 16)

    def test_docs_list_the_sweep_ports(self):
        root = Path(__file__).resolve().parent.parent
        listed = ", ".join(map(str, providers.SWEEP_PORTS))
        self.assertIn("| " + listed + " |", (root / "docs/reference.md").read_text())
        spoken = ", ".join(map(str, providers.SWEEP_PORTS[:-1]))
        spoken += " and " + str(providers.SWEEP_PORTS[-1])
        self.assertIn(spoken, (root / "docs/model-sources.md").read_text())

    def test_identity_routes_name_known_kinds(self):
        order = [route[0] for route in providers.IDENTITY]
        for route in providers.IDENTITY:
            self.assertIn(route[0], providers.KINDS)
            self.assertEqual(len(route), 5)
            self.assertTrue(route[1].startswith("/"))
            self.assertIn(route[2], ("eq", "has", "text", "text_is", "server"))
        for imitator in ("koboldcpp", "localai", "lemonade"):
            self.assertLess(order.index(imitator), order.index("ollama"))

    def test_harness_requirements(self):
        self.assertEqual(
            {h["id"]: h["protocol"] for h in providers.HARNESSES},
            {
                "OMP": "chat",
                "Pi": "chat",
                "Hermes": "chat",
                "opencode": "chat",
                "Codex": "responses",
                "Claude Code": "messages",
            },
        )
        self.assertEqual(len(providers.HARNESSES), 6)
        self.assertEqual(
            [h["id"] for h in providers.HARNESSES if h["native_only"]], ["Hermes"]
        )
        for row in providers.HARNESSES:
            self.assertEqual(set(row), {"id", "cli", "protocol", "native_only"})
            self.assertIn(row["protocol"], providers.PROTOCOL_LABEL)

    def test_lookups(self):
        self.assertEqual(
            providers.source("OpenRouter")["key_env"], "OPENROUTER_API_KEY"
        )
        self.assertEqual(
            providers.source("Attached endpoint")["label"], "Compatible endpoint"
        )
        self.assertEqual(providers.harness("Claude Code")["cli"], "claude")
        for unknown in ("ollama", "", None, ["OpenAI"]):
            with self.assertRaisesRegex(ValueError, "Choose a supported model source."):
                providers.source(unknown)
        with self.assertRaisesRegex(ValueError, "Choose a supported agent."):
            providers.harness("Cursor")
        for host in ("localhost", "LOCALHOST", "127.0.0.1", "::1"):
            self.assertTrue(providers.is_loopback(host))
        for host in ("", None, "127.0.0.2", "example.org", "localhost.example.org"):
            self.assertFalse(providers.is_loopback(host))

    def test_request_bypasses_proxies_on_loopback(self):
        seen = []

        def record(session, method, url, **kwargs):
            seen.append((session.trust_env, method, url, kwargs))
            return "loopback"

        with (
            patch.dict(
                os.environ,
                HTTP_PROXY="http://proxy.invalid:9",
                ALL_PROXY="http://proxy.invalid:9",
            ),
            patch.object(requests.Session, "request", record),
            patch.object(requests, "request", return_value="remote") as direct,
        ):
            for host in ("127.0.0.1", "localhost", "[::1]", "LocalHost"):
                self.assertEqual(
                    providers.request("GET", f"http://{host}:9/models"), "loopback"
                )
            self.assertEqual(
                seen[0],
                (
                    False,
                    "GET",
                    "http://127.0.0.1:9/models",
                    {"allow_redirects": False, "timeout": 20},
                ),
            )
            self.assertEqual([entry[0] for entry in seen], [False] * 4)
            direct.assert_not_called()
            providers.request(
                "POST", "http://127.0.0.1:9/x", timeout=2, allow_redirects=True
            )
            self.assertEqual(seen[-1][3], {"allow_redirects": True, "timeout": 2})
            self.assertEqual(
                providers.request("GET", "https://example.org/v1/models"), "remote"
            )
            direct.assert_called_once_with(
                "GET",
                "https://example.org/v1/models",
                allow_redirects=False,
                timeout=20,
            )
            self.assertEqual(len(seen), 5)

    def test_loopback_request_reaches_server_despite_proxy_environment(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response_only(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

        server = LoopbackServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        dead = "http://127.0.0.1:9"
        with patch.dict(
            os.environ,
            dict(
                HTTP_PROXY=dead,
                http_proxy=dead,
                ALL_PROXY=dead,
                all_proxy=dead,
                NO_PROXY="",
                no_proxy="",
            ),
        ):
            reply = providers.request(
                "GET", f"http://127.0.0.1:{server.server_port}/", timeout=2
            )
        self.assertEqual((reply.status_code, reply.text), (200, "ok"))

    def test_model_headers(self):
        self.assertEqual(
            providers.model_headers(providers.source("OpenAI"), "sk-fixture"),
            {"Authorization": "Bearer sk-fixture"},
        )
        self.assertEqual(
            providers.model_headers(providers.source("Anthropic"), "sk-fixture"),
            {"x-api-key": "sk-fixture", "anthropic-version": "2023-06-01"},
        )
        for name in ("OpenAI", "Anthropic", "Attached endpoint"):
            self.assertEqual(providers.model_headers(providers.source(name), ""), {})


if __name__ == "__main__":
    unittest.main()
