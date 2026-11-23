import ast
import atexit
import contextlib
import io
import json
import os
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

if "runtime" not in sys.modules and "WORKBENCH_DATA" not in os.environ:
    os.environ["WORKBENCH_DATA"] = tempfile.mkdtemp(prefix="workbench-test-")
    atexit.register(shutil.rmtree, os.environ["WORKBENCH_DATA"], True)

import account_book
import connections
import endpoints
import machines
import providers

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "endpoints"
RECORDED = {
    "root": ("GET", "/"),
    "greeting": ("GET", "/lmstudio-greeting"),
    "v1-models": ("GET", "/v1/models"),
    "api-ps": ("GET", "/api/ps"),
    "api-v1-models": ("GET", "/api/v1/models"),
    "chat-empty": ("POST", "/v1/chat/completions"),
    "responses-empty": ("POST", "/v1/responses"),
    "messages-empty": ("POST", "/v1/messages"),
}
PLAIN = (
    "Plain HTTP is only allowed to this computer. Use an https:// address, "
    "or add the machine under Machines to reach it through an SSH tunnel."
)
BAD = (400, {}, {"error": {"message": "bad request"}})
MISSING = (404, {}, {"detail": "Not Found"})
EXTRA = (
    "kind",
    "protocols",
    "context_window",
    "context_source",
    "host",
    "remote_port",
    "probed",
)
ALL = {"chat": True, "responses": True, "messages": True}


class LoopbackServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


def serve(case, routes, server=None, fallback=MISSING):
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self):
            size = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(size) if size else b""
            hits.append((self.command, self.path, self.headers, body))
            hit = routes.get((self.command, self.path), fallback)
            if callable(hit):
                hit = hit(self.headers, body)
            status, headers, payload = hit
            data = (
                payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            )
            self.send_response_only(status)
            if server:
                self.send_header("Server", server)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = reply

    httpd = LoopbackServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    case.addCleanup(thread.join, 2)
    case.addCleanup(httpd.server_close)
    case.addCleanup(httpd.shutdown)
    return httpd.server_port, hits


def pour(case, size, delay):
    stop = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response_only(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                while not stop.is_set():
                    self.wfile.write(b" " * size)
                    self.wfile.flush()
                    stop.wait(delay)
            except OSError:
                pass

    httpd = LoopbackServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    case.addCleanup(thread.join, 2)
    case.addCleanup(httpd.server_close)
    case.addCleanup(httpd.shutdown)
    case.addCleanup(stop.set)
    return httpd.server_port


def bounded(call, wait=3.0):
    box = {}
    thread = threading.Thread(target=lambda: box.update(value=call()), daemon=True)
    thread.start()
    thread.join(wait)
    return thread.is_alive(), box.get("value")


def rerouted(port, host="https://models.example.org"):
    real = providers.request

    def request(method, url, **kwargs):
        return real(method, url.replace(host, f"http://127.0.0.1:{port}"), **kwargs)

    return patch.object(providers, "request", request)


def closed_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def base(port):
    return f"http://127.0.0.1:{port}/v1"


def listing(*names, **extra):
    return {
        "object": "list",
        "data": [{"id": name, "object": "model", **extra} for name in names],
    }


def api(models, **more):
    routes = {
        ("GET", "/v1/models"): (200, {}, models),
        ("POST", "/v1/chat/completions"): BAD,
        ("POST", "/v1/responses"): BAD,
        ("POST", "/v1/messages"): BAD,
    }
    routes.update(more.pop("routes", {}))
    return routes


def ollama(loaded=None, names=("qwen3:8b", "llama3.2")):
    running = [
        {"name": name, "model": name, "context_length": size}
        for name, size in (loaded or {}).items()
    ]
    return api(
        listing(*names, owned_by="library"),
        routes={
            ("GET", "/"): (200, {}, b"Ollama is running"),
            ("GET", "/api/ps"): (200, {}, {"models": running}),
        },
    )


def tabby(secret="right"):
    def models(headers, body):
        if (
            headers.get("Authorization") == "Bearer " + secret
            and headers.get("x-api-key") == secret
        ):
            return (
                200,
                {},
                listing("exl2-coder", owned_by="tabbyAPI", context_length=24576),
            )
        return 401, {}, {"detail": "Please provide an API key"}

    return api(
        None,
        routes={
            ("GET", "/v1/models"): models,
            ("GET", "/.well-known/serviceinfo"): (
                200,
                {},
                {"software": {"name": "TabbyAPI"}},
            ),
        },
    )


def recorded(server):
    routes = {}
    for probe, route in RECORDED.items():
        stem = f"{server}-{probe}"
        if not (FIXTURES / (stem + ".code")).exists():
            continue
        headers = {}
        for line in (
            (FIXTURES / (stem + ".headers")).read_text(encoding="utf8").splitlines()[1:]
        ):
            name, _, value = line.partition(":")
            if value and name.lower() not in (
                "content-length",
                "connection",
                "keep-alive",
                "date",
            ):
                headers[name.strip()] = value.strip()
        code = int((FIXTURES / (stem + ".code")).read_text(encoding="utf8").strip())
        routes[route] = (code, headers, (FIXTURES / (stem + ".body")).read_bytes())
    return routes


def recopied(case, ending, suffixes):
    """A copy of the recordings whose files with these suffixes use one line ending."""
    folder = tempfile.TemporaryDirectory()
    case.addCleanup(folder.cleanup)
    for path in FIXTURES.iterdir():
        data = path.read_bytes()
        if path.suffix in suffixes:
            data = data.replace(b"\r\n", b"\n").replace(b"\n", ending)
        (Path(folder.name) / path.name).write_bytes(data)
    return Path(folder.name)


def core(port, key="", timeout=2.0):
    return endpoints._probe_base(
        base(port),
        providers.KINDS,
        providers.IDENTITY,
        endpoints._http,
        endpoints._auth(key),
        timeout,
    )


def stub_endpoint(settings):
    text = settings.get("endpoint", "").strip().rstrip("/")
    if "://" not in text:
        text = "http://" + text
    parsed = urlsplit(text)
    if parsed.scheme == "http" and parsed.hostname not in providers.LOOPBACK:
        raise ValueError(PLAIN)
    return text, settings.get("remote_model", "").strip()


def stub_clean_model(value):
    text = str(value or "").strip()
    if (
        len(text) > 300
        or text.startswith("-")
        or any(ord(c) < 32 or ord(c) == 127 for c in text)
    ):
        raise ValueError(
            "This model ID cannot be used. Pick the model from the list or retype its ID."
        )
    return text


def stub_compat(provider, row=None):
    flags = (row or {}).get("protocols") or {}
    return {
        h["id"]: {"ok": flags.get(h["protocol"]) is not False, "reason": ""}
        for h in providers.HARNESSES
    }


class Book:
    """File-backed stand-in for account_book with the trusted-caller contract of save."""

    def __init__(self, folder):
        self.path = Path(folder) / "connections.json"
        self.trusted = []

    def entries(self):
        return (
            json.loads(self.path.read_text(encoding="utf8"))
            if self.path.exists()
            else {}
        )

    def write(self, rows):
        self.path.write_text(json.dumps(rows), encoding="utf8")

    def save(self, data, allow_endpoint_change=False):
        rows = self.entries()
        ident = data["connection_id"]
        row = {
            k: v for k, v in rows.get(ident, {}).items() if k in EXTRA + ("context",)
        }
        row.update(
            name=data["name"],
            provider=data["provider"],
            endpoint=data["endpoint"],
            remote_model=data["remote_model"],
            harness=data.get("harness", "OMP"),
            auth_provider="",
            connection_id=ident,
        )
        for key in EXTRA:
            if key in data:
                row.pop(key, None)
                if data[key] is not None:
                    row[key] = data[key]
        rows[ident] = row
        self.write(rows)
        self.trusted.append(allow_endpoint_change)
        return row

    def remove(self, ident):
        rows = self.entries()
        rows.pop(ident)
        self.write(rows)
        return {"removed": True, "key_removed": True, "message": "Connection removed."}


class Case(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.book = Book(folder.name)
        self.keys = {}
        self.links = SimpleNamespace(
            endpoint=stub_endpoint,
            clean_model=stub_clean_model,
            compat=stub_compat,
            store_key=Mock(return_value="vault"),
            key_message=lambda stored, row: "kept in the " + stored,
            secret=lambda row: self.keys.get(row["connection_id"], ""),
            _forward=Mock(return_value=None),
        )
        for name, value in (
            ("connections", self.links),
            ("account_book", self.book),
            ("PROBE_TIMEOUT", 2.0),
        ):
            patcher = patch.object(endpoints, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def quick(self):
        patcher = patch.object(endpoints, "PROBE_TIMEOUT", 0.3)
        patcher.start()
        self.addCleanup(patcher.stop)


class RecordedServerTests(Case):
    def test_recorded_ollama_is_classified(self):
        port, hits = serve(
            self,
            recorded("ollama"),
            fallback=(404, {"Content-Type": "text/plain"}, b"404 page not found"),
        )
        record = core(port)
        self.assertEqual(record["kind"], "ollama")
        self.assertEqual(record["auth"], "none")
        self.assertEqual(
            record["models"],
            [
                {"id": name, "context": None, "loaded": False}
                for name in (
                    "flint-local:128k",
                    "flint:40b-a2b-qat-int4",
                    "qwen3.8:27b-mlx",
                )
            ],
        )
        self.assertEqual(record["protocols"], ALL)
        self.assertFalse(
            any(h[2].get("Authorization") or h[2].get("x-api-key") for h in hits)
        )

    def test_recorded_lmstudio_is_classified(self):
        routes = recorded("lmstudio")
        port, _ = serve(self, routes, fallback=routes[("GET", "/")])
        record = core(port)
        self.assertEqual(record["kind"], "lmstudio")
        self.assertEqual(
            record["models"],
            [
                {"id": "flint", "context": None, "loaded": False},
                {"id": "qwen/qwen3.8-27b", "context": None, "loaded": False},
            ],
        )
        self.assertEqual(record["protocols"], ALL)
        self.assertIsNone(
            endpoints._probe_base(
                f"http://127.0.0.1:{port}",
                providers.KINDS,
                providers.IDENTITY,
                endpoints._http,
                {},
                2.0,
            )
        )
        found = endpoints.probe(f"127.0.0.1:{port}")
        self.assertEqual(found["base"], base(port))
        self.assertEqual(found["label"], "LM Studio")

    def test_recordings_read_the_same_with_either_line_ending(self):
        names = ("ollama", "lmstudio")
        here = {name: recorded(name) for name in names}
        self.assertEqual(len(here["ollama"]), 6)
        self.assertEqual(len(here["lmstudio"]), 7)
        for ending in (b"\n", b"\r\n"):
            folder = recopied(self, ending, (".headers",))
            sample = (folder / "ollama-root.headers").read_bytes()
            self.assertEqual(sample.count(b"\r\n") > 0, ending == b"\r\n")
            self.assertEqual(sample.count(b"\n"), 5)
            with patch(__name__ + ".FIXTURES", folder):
                self.assertEqual({name: recorded(name) for name in names}, here)
        for routes in here.values():
            for _, headers, _ in routes.values():
                self.assertIn("Content-Type", headers)
                self.assertNotIn("Content-Length", headers)
                for name, value in headers.items():
                    self.assertEqual((name, value), (name.strip(), value.strip()))
        root = here["lmstudio"][("GET", "/")][1]
        self.assertEqual(sorted(root), ["Content-Type", "ETag", "X-Powered-By"])
        self.assertEqual(root["Content-Type"], "application/json; charset=utf-8")

    def test_recordings_classify_the_same_from_a_crlf_checkout(self):
        found = {}
        for label, folder in (
            ("here", FIXTURES),
            ("crlf", recopied(self, b"\r\n", (".headers", ".body"))),
        ):
            with patch(__name__ + ".FIXTURES", folder):
                routes = {name: recorded(name) for name in ("ollama", "lmstudio")}
            port, _ = serve(self, routes["ollama"], fallback=(404, {}, b"404"))
            other, _ = serve(
                self, routes["lmstudio"], fallback=routes["lmstudio"][("GET", "/")]
            )
            found[label] = [
                {k: v for k, v in core(p).items() if k != "base"} for p in (port, other)
            ]
        self.assertEqual(found["crlf"], found["here"])
        self.assertEqual([r["kind"] for r in found["here"]], ["ollama", "lmstudio"])
        self.assertEqual([r["protocols"] for r in found["here"]], [ALL, ALL])

    def test_lmstudio_catch_all_answer_is_not_a_route(self):
        routes = recorded("lmstudio")
        port, hits = serve(self, routes, fallback=routes[("GET", "/")])
        flags = endpoints._protocols(base(port), "/anthropic", endpoints._http, {}, 2.0)
        self.assertEqual(flags, {"chat": True, "responses": True, "messages": False})
        self.assertEqual(hits[-1][1], "/anthropic/v1/messages")

    def test_lmstudio_loaded_context_and_no_embeddings(self):
        routes = recorded("lmstudio")
        native = json.loads(routes[("GET", "/api/v1/models")][2])
        native["models"][0]["loaded_instances"] = [
            {"id": "flint", "config": {"context_length": 4096}}
        ]
        routes[("GET", "/api/v1/models")] = (200, {}, native)
        embedding = "text-embedding-nomic-embed-text-v1.5"
        routes[("GET", "/v1/models")] = (
            200,
            {},
            listing("flint", embedding, owned_by="organization_owner"),
        )
        port, _ = serve(self, routes, fallback=routes[("GET", "/")])
        record = core(port)
        self.assertEqual(record["kind"], "lmstudio")
        self.assertEqual(
            record["models"], [{"id": "flint", "context": 4096, "loaded": True}]
        )


class ClassificationTests(Case):
    def test_llamacpp_identified_by_header_with_loaded_context(self):
        routes = api(
            listing("qwen3-coder.gguf"),
            routes={
                ("GET", "/props"): (
                    200,
                    {},
                    {"default_generation_settings": {"n_ctx": 8192}},
                )
            },
        )
        port, hits = serve(self, routes, server="llama.cpp")
        record = core(port)
        self.assertEqual(record["kind"], "llamacpp")
        self.assertEqual(
            record["models"],
            [{"id": "qwen3-coder.gguf", "context": 8192, "loaded": True}],
        )
        self.assertNotIn("/api/extra/version", [h[1] for h in hits])

    def test_ollama_loaded_and_unloaded_models(self):
        port, _ = serve(self, ollama({"qwen3:8b": 32768}))
        record = core(port)
        self.assertEqual(record["kind"], "ollama")
        self.assertEqual(
            record["models"],
            [
                {"id": "qwen3:8b", "context": 32768, "loaded": True},
                {"id": "llama3.2", "context": None, "loaded": False},
            ],
        )
        self.assertEqual(record["protocols"], ALL)

    def test_empty_list_sent_as_null_is_still_a_model_server(self):
        routes = api(
            {"object": "list", "data": None},
            routes={
                ("GET", "/"): (200, {}, b"Ollama is running"),
                ("GET", "/api/ps"): (200, {}, {"models": []}),
            },
        )
        port, _ = serve(self, routes)
        record = core(port)
        self.assertEqual((record["kind"], record["models"]), ("ollama", []))
        self.assertEqual(endpoints.listed({"object": "list", "data": None}), [])
        self.assertIsNone(endpoints.listed({"data": None}))
        with self.assertRaisesRegex(
            ValueError,
            "This server has no model available. Load a model there, then attach again.",
        ):
            endpoints.attach(f"127.0.0.1:{port}")

    def test_ollama_imitators_are_not_ollama(self):
        routes = api(
            listing("koboldcpp/model", owned_by="library"),
            routes={
                ("GET", "/"): (200, {}, b"Ollama is running"),
                ("GET", "/api/extra/version"): (
                    200,
                    {},
                    {"result": "KoboldCpp", "version": "1.90"},
                ),
                ("GET", "/api/extra/true_max_context_length"): (
                    200,
                    {},
                    {"value": 16384},
                ),
            },
        )
        port, _ = serve(self, routes)
        record = core(port)
        self.assertEqual(record["kind"], "koboldcpp")
        self.assertEqual(
            record["models"],
            [{"id": "koboldcpp/model", "context": 16384, "loaded": None}],
        )

    def test_item_context_for_vllm(self):
        port, _ = serve(
            self, api(listing("coder", owned_by="vllm", max_model_len=65536))
        )
        record = core(port)
        self.assertEqual(record["kind"], "vllm")
        self.assertEqual(
            record["models"], [{"id": "coder", "context": 65536, "loaded": None}]
        )

    def test_mlx_lm_and_generic_fallbacks(self):
        plain, _ = serve(
            self,
            api(
                listing("mlx-community/coder"),
                routes={("GET", "/health"): (200, {}, {"status": "ok"})},
            ),
        )
        other, _ = serve(
            self, api(listing("anything", owned_by="someone", context_length=4096))
        )
        self.assertEqual(core(plain)["kind"], "mlx-lm")
        self.assertEqual(core(plain)["models"][0]["context"], None)
        record = core(other)
        self.assertEqual(record["kind"], "generic")
        self.assertEqual(
            record["models"], [{"id": "anything", "context": 4096, "loaded": None}]
        )

    def test_locked_server_states(self):
        port, hits = serve(self, tabby())
        record = core(port)
        self.assertEqual(
            (record["kind"], record["auth"], record["models"]),
            ("tabbyapi", "required", []),
        )
        self.assertEqual(
            record["protocols"], {"chat": None, "responses": None, "messages": None}
        )
        self.assertFalse([h for h in hits if h[0] == "POST"])
        record = core(port, "right")
        self.assertEqual((record["kind"], record["auth"]), ("tabbyapi", "accepted"))
        self.assertEqual(
            record["models"], [{"id": "exl2-coder", "context": 24576, "loaded": None}]
        )
        record = core(port, "wrong")
        self.assertEqual((record["auth"], record["models"]), ("rejected", []))

    def test_non_model_listeners_are_ignored(self):
        airplay, _ = serve(self, {}, server="AirTunes/1", fallback=(403, {}, b""))
        html = (
            200,
            {"Content-Type": "text/html"},
            b"<!doctype html><title>Dev server</title>",
        )
        web, _ = serve(self, {}, fallback=html)
        self.assertIsNone(core(airplay))
        self.assertIsNone(core(web))
        self.assertIsNone(core(closed_port(), timeout=0.3))

    def test_malformed_server_data_is_tolerated(self):
        native = {
            "models": [
                {"key": ["x"], "type": "embedding", "loaded_instances": 7},
                {
                    "key": "a",
                    "loaded_instances": [{"id": {"x": 1}, "config": "big"}, "text"],
                },
                "text",
            ]
        }
        routes = api(
            {"data": [{"id": "a"}, {"id": 5}, "text", {"name": "b"}]},
            routes={
                ("GET", "/lmstudio-greeting"): (200, {}, {"lmstudio": True}),
                ("GET", "/api/v1/models"): (200, {}, native),
            },
        )
        port, _ = serve(self, routes)
        self.assertEqual(
            core(port)["models"],
            [
                {"id": "a", "context": None, "loaded": True},
                {"id": "b", "context": None, "loaded": False},
            ],
        )
        strange = api(
            listing("a", owned_by="library"),
            routes={
                ("GET", "/"): (200, {}, b"Ollama is running"),
                ("GET", "/api/ps"): (
                    200,
                    {},
                    {"models": [{"name": ["a"], "model": {"a": 1}}, 3]},
                ),
            },
        )
        port, _ = serve(self, strange)
        self.assertEqual(
            core(port)["models"], [{"id": "a", "context": None, "loaded": False}]
        )
        self.assertIsNone(endpoints._json(b"[" * 200000))

    def test_protocol_classification(self):
        cases = [
            (400, b'{"error":"bad"}', True),
            (422, b'{"detail":[]}', True),
            (404, b'{"detail":"Not Found"}', False),
            (404, b'{"error":"model \'\' not found"}', None),
            (405, b"", False),
            (500, b"", None),
            (401, b"", None),
            (0, b"", None),
            (501, b"", False),
            (200, b"{}", True),
        ]
        for code, body, want in cases:
            sent = []

            def get(method, url, headers, payload, timeout):
                sent.append((method, url, headers, payload))
                return (
                    (code, {}, body)
                    if url.endswith("/chat/completions")
                    else (400, {}, b"{}")
                )

            flags = endpoints._protocols(
                "http://127.0.0.1:9/v1", "", get, {"x-api-key": "k"}, 1.0
            )
            self.assertIs(flags["chat"], want, (code, body))
            self.assertEqual((flags["responses"], flags["messages"]), (True, True))
            self.assertEqual(
                [entry[1] for entry in sent],
                [
                    "http://127.0.0.1:9/v1/chat/completions",
                    "http://127.0.0.1:9/v1/responses",
                    "http://127.0.0.1:9/v1/messages",
                ],
            )
            for method, _, headers, payload in sent:
                self.assertEqual((method, payload), ("POST", b"{}"))
                self.assertEqual(
                    headers, {"x-api-key": "k", "Content-Type": "application/json"}
                )

    def test_mlx_omni_messages_prefix(self):
        routes = api(
            listing("mlx-community/coder", owned_by="mlx"),
            routes={
                ("GET", "/openapi.json"): (
                    200,
                    {},
                    {"info": {"title": "MLX Omni Server"}},
                ),
                ("POST", "/anthropic/v1/messages"): BAD,
                ("POST", "/v1/messages"): MISSING,
                ("POST", "/v1/responses"): MISSING,
            },
        )
        port, hits = serve(self, routes)
        record = core(port)
        self.assertEqual(record["kind"], "mlx-omni")
        self.assertEqual(
            record["protocols"], {"chat": True, "responses": False, "messages": True}
        )
        posts = [h[1] for h in hits if h[0] == "POST"]
        self.assertIn("/anthropic/v1/messages", posts)
        self.assertNotIn("/v1/messages", posts)

    def test_listed_shapes(self):
        self.assertEqual(
            endpoints.listed(
                {"data": [{"id": "a", "context_length": 8192}, {"id": 3}]}
            ),
            [{"id": "a", "context": 8192}],
        )
        self.assertEqual(
            endpoints.listed({"models": [{"name": "b", "context_window": True}]}),
            [{"id": "b", "context": None}],
        )
        self.assertEqual(
            endpoints.listed([{"id": "c", "max_model_len": 4096}]),
            [{"id": "c", "context": 4096}],
        )
        for payload in ({"error": "x"}, "text", None, {"data": "x"}):
            self.assertIsNone(endpoints.listed(payload))


class SweepTests(Case):
    def test_sweep_keeps_identified_and_open_only(self):
        open_generic, _ = serve(self, api(listing("anything")))
        locked_generic, _ = serve(
            self, {}, fallback=(401, {}, {"error": "unauthorized"})
        )
        locked_tabby, hits = serve(self, tabby())
        ports = [open_generic, locked_generic, locked_tabby, closed_port()]
        raw = endpoints._sweep(
            ports, [], providers.KINDS, providers.IDENTITY, endpoints._http, 1.0
        )
        self.assertEqual(raw["v"], 1)
        self.assertEqual(raw["hints"], [])
        self.assertEqual(
            [(r["port"], r["kind"], r["auth"]) for r in raw["endpoints"]],
            [(open_generic, "generic", "none"), (locked_tabby, "tabbyapi", "required")],
        )
        self.assertEqual(raw["endpoints"][0]["base"], base(open_generic))
        self.assertFalse(
            any(h[2].get("Authorization") or h[2].get("x-api-key") for h in hits)
        )

    def test_lmstudio_hint_from_control_port(self):
        control, _ = serve(
            self, {("GET", "/lmstudio-greeting"): (200, {}, {"lmstudio": True})}
        )
        other, _ = serve(
            self, {("GET", "/lmstudio-greeting"): (200, {}, {"lmstudio": "yes"})}
        )
        quiet = closed_port()
        raw = endpoints._sweep(
            [],
            [quiet, other, control],
            providers.KINDS,
            providers.IDENTITY,
            endpoints._http,
            1.0,
        )
        self.assertEqual(
            raw, {"v": 1, "endpoints": [], "hints": ["lmstudio-server-off"]}
        )
        raw = endpoints._sweep(
            [],
            [other],
            providers.KINDS,
            providers.IDENTITY,
            endpoints._http,
            1.0,
        )
        self.assertEqual(raw["hints"], [])
        with (
            patch.object(providers, "SWEEP_PORTS", []),
            patch.object(providers, "LMSTUDIO_CONTROL_PORTS", [control]),
        ):
            found = endpoints.scan()
        self.assertEqual(found["endpoints"], [])
        self.assertEqual(
            found["hints"],
            [
                {
                    "id": "lmstudio-server-off",
                    "text": "LM Studio is running, but its local server is off. "
                    "In LM Studio open Developer and start the server, then scan again.",
                }
            ],
        )

    def test_scan_describes_and_skips_the_managed_port(self):
        port, _ = serve(
            self, api(listing("coder", owned_by="vllm", max_model_len=8192))
        )
        managed, _ = serve(self, api(listing("local-agent", owned_by="llamacpp")))
        with (
            patch.object(providers, "SWEEP_PORTS", [port, managed]),
            patch.object(providers, "LMSTUDIO_CONTROL_PORTS", []),
            patch.object(endpoints, "PORT", managed),
        ):
            found = endpoints.scan()
        self.assertEqual(found["hints"], [])
        self.assertEqual(len(found["endpoints"]), 1)
        entry = found["endpoints"][0]
        self.assertEqual(
            (entry["port"], entry["kind"], entry["label"]), (port, "vllm", "vLLM")
        )
        self.assertEqual(entry["connection_id"], "")
        self.assertEqual(set(entry["compat"]), {h["id"] for h in providers.HARNESSES})
        self.assertEqual(len(entry["warnings"]), 1)
        self.assertIn("vLLM listens on every network interface", entry["warnings"][0])


class ProbeTests(Case):
    def test_probe_finds_v1_from_root_address(self):
        routes = api(
            listing("coder", owned_by="vllm"),
            routes={("GET", "/models"): (200, {}, {"error": "unexpected"})},
        )
        port, _ = serve(self, routes)
        for typed in (
            f"127.0.0.1:{port}",
            f"http://127.0.0.1:{port}/",
            f"http://127.0.0.1:{port}/v1",
        ):
            found = endpoints.probe(typed)
            self.assertEqual(found["base"], base(port))
            self.assertEqual(
                (found["kind"], found["label"], found["auth"]), ("vllm", "vLLM", "none")
            )
        self.assertNotIn("port", found)
        rootless = {
            ("GET", "/models"): (200, {}, listing("coder")),
            ("POST", "/chat/completions"): BAD,
        }
        port, _ = serve(self, rootless)
        found = endpoints.probe(f"127.0.0.1:{port}")
        self.assertEqual(found["base"], f"http://127.0.0.1:{port}")
        self.assertEqual(
            found["protocols"], {"chat": True, "responses": False, "messages": False}
        )
        nested = {("GET", "/api/v1/models"): (200, {}, listing("coder"))}
        port, _ = serve(self, nested)
        self.assertEqual(
            endpoints.probe(f"127.0.0.1:{port}/api")["base"],
            f"http://127.0.0.1:{port}/api/v1",
        )

    def test_probe_errors_say_what_to_do(self):
        self.quick()
        quiet = closed_port()
        with self.assertRaises(ValueError) as caught:
            endpoints.probe(f"127.0.0.1:{quiet}")
        self.assertEqual(
            str(caught.exception),
            f"Nothing answered at http://127.0.0.1:{quiet}. "
            "Start the server or check the address and port, then try again.",
        )
        html = (
            200,
            {"Content-Type": "text/html"},
            b"<!doctype html><title>Dev server</title>",
        )
        web, hits = serve(self, {}, fallback=html)
        with self.assertRaises(ValueError) as caught:
            endpoints.probe(f"http://127.0.0.1:{web}")
        self.assertEqual(
            str(caught.exception),
            f"http://127.0.0.1:{web} answered, but not as an OpenAI-compatible API. "
            "Check the address; most servers use a path that ends in /v1.",
        )
        self.assertEqual([h[1] for h in hits], ["/v1/models", "/models"])
        with (
            patch.object(endpoints, "_fetch") as fetch,
            patch.object(endpoints, "_http") as direct,
        ):
            with self.assertRaises(ValueError) as caught:
                endpoints.probe("http://example.org/v1")
            self.assertEqual(str(caught.exception), PLAIN)
            fetch.assert_not_called()
            direct.assert_not_called()

    def test_unanswered_address_is_tried_once(self):
        calls = []

        def fetch(method, url, headers=None, body=None, timeout=1.0):
            calls.append(url)
            return 0, {}, b""

        with patch.object(endpoints, "_fetch", fetch):
            with self.assertRaisesRegex(
                ValueError, "Nothing answered at https://models.example.org."
            ):
                endpoints.probe("https://models.example.org")
        self.assertEqual(calls, ["https://models.example.org/v1/models"])

    def test_non_loopback_goes_through_the_shared_request_helper(self):
        reply = Mock(status_code=200, headers={"Server": "X"})
        reply.iter_content.return_value = iter([b'{"data"', b":[]}"])
        with (
            patch.object(providers, "request", return_value=reply) as request,
            patch.object(endpoints, "_http") as direct,
        ):
            got = endpoints._fetch(
                "GET",
                "https://models.example.org/v1/models",
                {"x-api-key": "k"},
                None,
                5.0,
            )
            self.assertEqual(got, (200, {"server": "X"}, b'{"data":[]}'))
            request.assert_called_once_with(
                "GET",
                "https://models.example.org/v1/models",
                headers={"x-api-key": "k"},
                data=None,
                timeout=5.0,
                stream=True,
            )
            reply.close.assert_called_once_with()
            reply.iter_content.side_effect = endpoints.requests.ConnectionError("cut")
            self.assertEqual(
                endpoints._fetch("GET", "https://models.example.org/v1/models"),
                (0, {}, b""),
            )
            self.assertEqual(reply.close.call_count, 2)
            request.side_effect = endpoints.requests.ConnectionError("down")
            self.assertEqual(
                endpoints._fetch("GET", "https://models.example.org/v1/models"),
                (0, {}, b""),
            )
            endpoints._fetch("GET", "https://127.0.0.1:8443/v1/models")
            self.assertEqual(request.call_count, 4)
            direct.assert_not_called()

    def test_typed_https_address_is_probed_through_requests(self):
        port, hits = serve(self, tabby())
        with rerouted(port):
            found = endpoints.probe("https://models.example.org", "right")
            self.assertEqual(
                (found["base"], found["kind"], found["auth"]),
                ("https://models.example.org/v1", "tabbyapi", "accepted"),
            )
            self.assertEqual(found["models"][0]["context"], 24576)
            self.assertEqual(
                found["protocols"], {"chat": True, "responses": True, "messages": True}
            )
            self.assertEqual(
                endpoints.probe("https://models.example.org")["auth"], "required"
            )
        self.assertEqual(hits[0][2].get("x-api-key"), "right")

    def test_dripping_body_cannot_hold_a_request(self):
        port = pour(self, 1, 0.01)
        stuck, got = bounded(
            lambda: endpoints._http("GET", base(port) + "/models", timeout=0.15)
        )
        self.assertFalse(stuck)
        self.assertEqual(got, (0, {}, b""))

    def test_chunked_and_oversized_bodies_are_read_within_the_cap(self):
        chunked = b"7\r\n" + b'{"data"\r\n' + b"4\r\n" + b":[]}\r\n" + b"0\r\n\r\n"
        routes = {
            ("GET", "/v1/models"): (200, {"Transfer-Encoding": "chunked"}, chunked),
            ("GET", "/big"): (200, {}, b"x" * 3000000),
            ("GET", "/none"): (204, {}, b""),
        }
        port, _ = serve(self, routes)
        root = f"http://127.0.0.1:{port}"
        self.assertEqual(
            endpoints._http("GET", root + "/v1/models")[::2], (200, b'{"data":[]}')
        )
        status, head, body = endpoints._http("GET", root + "/big")
        self.assertEqual((status, len(body)), (200, 2097152))
        self.assertEqual(head["content-length"], "3000000")
        self.assertEqual(endpoints._http("GET", root + "/none")[::2], (204, b""))

    def test_endless_remote_body_is_cut_off(self):
        url = "https://models.example.org/v1/models"
        with rerouted(pour(self, 65536, 0.002)):
            stuck, got = bounded(lambda: endpoints._fetch("GET", url, timeout=2.0))
        self.assertFalse(stuck)
        self.assertEqual((got[0], len(got[2])), (200, 2097152))
        with rerouted(pour(self, 65536, 0.05)):
            stuck, got = bounded(lambda: endpoints._fetch("GET", url, timeout=0.1))
        self.assertFalse(stuck)
        self.assertEqual(got, (0, {}, b""))

    def test_plain_http_is_never_sent_off_this_computer(self):
        self.book.write(
            {
                "eeeeeeeeeeeeeeee": dict(
                    provider="Attached endpoint",
                    endpoint="http://192.0.2.10:8000/v1",
                    connection_id="eeeeeeeeeeeeeeee",
                    remote_model="coder",
                    kind="vllm",
                )
            }
        )
        self.keys["eeeeeeeeeeeeeeee"] = "sk-fixture-key"
        with patch.object(providers, "request") as request:
            self.assertEqual(
                endpoints._fetch("GET", "http://192.0.2.10:8000/v1/models"),
                (0, {}, b""),
            )
            self.assertEqual(endpoints._fetch("GET", "ftp://127.0.0.1/v1/models")[0], 0)
            with self.assertRaisesRegex(ValueError, "Nothing answered at"):
                endpoints.refresh("eeeeeeeeeeeeeeee")
            self.assertIsNone(
                endpoints.model_context(
                    "http://192.0.2.10:8000/v1", "vllm", "coder", "sk-fixture-key"
                )
            )
            self.assertEqual(
                endpoints.probe_protocols(
                    "http://192.0.2.10:8000/v1", "sk-fixture-key"
                ),
                {"chat": None, "responses": None, "messages": None},
            )
            request.assert_not_called()

    def test_key_is_trimmed_and_never_echoed(self):
        port, hits = serve(self, tabby())
        self.assertEqual(
            endpoints.probe(f"127.0.0.1:{port}", " right\n")["auth"], "accepted"
        )
        self.assertEqual(hits[0][2].get("Authorization"), "Bearer right")
        for bad in ("sk-secret\nvalue", "sk-secret…"):
            with self.assertRaises(ValueError) as caught:
                endpoints.probe(f"127.0.0.1:{port}", bad)
            self.assertIn("Paste the key again", str(caught.exception))
            self.assertNotIn("sk-secret", str(caught.exception))
        self.assertEqual(endpoints._auth(""), {})

    def test_describe_warnings_and_saved_connection(self):
        record = {
            "base": "http://127.0.0.1:8000/v1",
            "kind": "vllm",
            "auth": "none",
            "models": [
                {"id": "small", "context": 8192, "loaded": None},
                {"id": "silent", "context": None, "loaded": None},
                {"id": "big", "context": 65536, "loaded": None},
            ],
            "protocols": {"chat": True, "responses": False, "messages": None},
        }
        plain = endpoints.describe(record)
        self.assertEqual(len(plain["warnings"]), 1)
        self.assertEqual(
            plain["warnings"][0],
            "vLLM listens on every network interface by default and answered without a key; "
            "bind it to 127.0.0.1 or set a key if other devices share your network.",
        )
        self.assertEqual(plain["compat"]["Codex"], {"ok": False, "reason": ""})
        self.assertNotIn("label", record)
        self.assertEqual(
            endpoints.describe(record, "small")["warnings"][1],
            "small is loaded with a 8,192-token context, which is small for an agent; "
            "raise the context size in vLLM and check again.",
        )
        for name in ("silent", "typed-by-hand"):
            self.assertEqual(
                endpoints.describe(record, name)["warnings"][1],
                f"vLLM did not report a context size for {name}, so 32,768 tokens is assumed; "
                "set the context size on the connection if the server uses another value.",
            )
        self.assertEqual(len(endpoints.describe(record, "big")["warnings"]), 1)
        locked = dict(record, kind="tabbyapi", auth="required", models=[])
        self.assertEqual(
            endpoints.describe(locked, "x")["warnings"],
            ["TabbyAPI needs its API key before models can be listed."],
        )
        single = dict(
            record,
            kind="ollama",
            models=[{"id": "one", "context": 4096, "loaded": True}],
        )
        self.assertIn(
            "one is loaded with a 4,096-token context",
            endpoints.describe(single)["warnings"][0],
        )
        rows = {
            "aaaaaaaaaaaaaaaa": dict(
                provider="Attached endpoint",
                endpoint=record["base"],
                host="u@box",
                remote_port=8000,
            ),
            "bbbbbbbbbbbbbbbb": dict(
                provider="Attached endpoint", endpoint=record["base"], context=8192
            ),
            "cccccccccccccccc": dict(provider="OpenAI", endpoint=record["base"]),
            "dddddddddddddddd": dict(
                provider="Attached endpoint", endpoint=record["base"]
            ),
        }
        self.book.write(rows)
        self.assertEqual(
            endpoints.describe(record)["connection_id"], "dddddddddddddddd"
        )
        self.assertEqual(endpoints.describe(record, local=False)["connection_id"], "")


class AttachTests(Case):
    def test_attach_saves_named_connection(self):
        port, _ = serve(self, ollama({"qwen3:8b": 32768}))
        result = endpoints.attach(
            f"127.0.0.1:{port}", key=" sk-fixture-key\n", harness="Codex"
        )
        row = result["connection"]
        self.assertEqual(row["provider"], "Attached endpoint")
        self.assertEqual(row["endpoint"], base(port))
        self.assertEqual(row["name"], "Ollama · qwen3:8b")
        self.assertEqual((row["remote_model"], row["harness"]), ("qwen3:8b", "Codex"))
        self.assertEqual((row["kind"], row["protocols"]), ("ollama", ALL))
        self.assertEqual(
            (row["context_window"], row["context_source"]), (32768, "server")
        )
        self.assertIsInstance(row["probed"], int)
        self.assertRegex(row["connection_id"], r"\A[0-9a-f]{16}\Z")
        self.assertNotIn("host", row)
        self.links.store_key.assert_called_once_with(row, "sk-fixture-key")
        self.assertEqual(
            (result["stored"], result["message"]), ("vault", "kept in the vault")
        )
        self.assertEqual(result["warnings"], [])
        self.assertEqual(self.book.entries(), {row["connection_id"]: row})
        self.assertNotIn("sk-fixture-key", self.book.path.read_text(encoding="utf8"))
        self.assertEqual(self.book.trusted, [True])

    def test_attach_without_key_and_with_user_context(self):
        port, _ = serve(self, ollama({"qwen3:8b": 8192}))
        result = endpoints.attach(
            f"127.0.0.1:{port}",
            name="  Desk Ollama  ",
            model="llama3.2",
            context=200000,
        )
        row = result["connection"]
        self.assertEqual(
            (row["name"], row["remote_model"]), ("Desk Ollama", "llama3.2")
        )
        self.assertEqual(
            (row["context_window"], row["context_source"]), (200000, "user")
        )
        self.assertEqual(
            (result["stored"], result["message"], result["warnings"]), ("", "", [])
        )
        self.links.store_key.assert_not_called()
        typed = endpoints.attach(f"127.0.0.1:{port}", model="pulled-later:latest")
        self.assertEqual(typed["connection"]["connection_id"], row["connection_id"])
        self.assertNotIn("context_window", typed["connection"])
        self.assertNotIn("context_source", typed["connection"])
        self.assertIn("so 32,768 tokens is assumed", typed["warnings"][0])
        small = endpoints.attach(f"127.0.0.1:{port}")
        self.assertEqual(small["connection"]["remote_model"], "qwen3:8b")
        self.assertIn(
            "qwen3:8b is loaded with a 8,192-token context", small["warnings"][0]
        )
        self.assertEqual(
            len(
                endpoints.attach(f"127.0.0.1:{port}", name="n" * 200)["connection"][
                    "name"
                ]
            ),
            90,
        )
        for bad in (500, 10000001, True, "8192", 2048.0):
            with self.assertRaisesRegex(
                ValueError, "Enter a context size between 1,024 and 10,000,000 tokens."
            ):
                endpoints.attach(f"127.0.0.1:{port}", context=bad)
        with self.assertRaisesRegex(ValueError, "This model ID cannot be used."):
            endpoints.attach(f"127.0.0.1:{port}", model="-rf")

    def test_attach_needs_model_choice_and_key(self):
        two, _ = serve(self, ollama())
        with self.assertRaisesRegex(
            ValueError, "Choose which model this connection should use."
        ):
            endpoints.attach(f"127.0.0.1:{two}")
        none, _ = serve(self, ollama(names=()))
        with self.assertRaisesRegex(
            ValueError,
            "This server has no model available. Load a model there, then attach again.",
        ):
            endpoints.attach(f"127.0.0.1:{none}")
        locked, _ = serve(self, tabby())
        with self.assertRaisesRegex(
            ValueError, "This server needs an API key. Paste its key and attach again."
        ):
            endpoints.attach(f"127.0.0.1:{locked}")
        with self.assertRaisesRegex(
            ValueError,
            "The server rejected this API key. Check the key and attach again.",
        ):
            endpoints.attach(f"127.0.0.1:{locked}", key="wrong")
        self.assertEqual(self.book.entries(), {})
        self.links.store_key.assert_not_called()
        only, _ = serve(self, api(listing("only-model")))
        self.assertEqual(
            endpoints.attach(f"127.0.0.1:{only}")["connection"]["remote_model"],
            "only-model",
        )

    def test_attach_refuses_an_unusable_id_reported_by_the_server(self):
        routes = api(listing("-rf"))
        port, _ = serve(self, routes)
        for hostile in ("-rf", "coder\x07", "x" * 301):
            routes[("GET", "/v1/models")] = (200, {}, listing(hostile))
            with self.assertRaisesRegex(ValueError, "This model ID cannot be used."):
                endpoints.attach(f"127.0.0.1:{port}")
        port, _ = serve(self, ollama({"-rf": 32768}, names=("-rf", "llama3.2")))
        with self.assertRaisesRegex(ValueError, "This model ID cannot be used."):
            endpoints.attach(f"127.0.0.1:{port}")
        self.assertEqual(self.book.entries(), {})
        row = endpoints.attach(f"127.0.0.1:{port}", model="llama3.2")["connection"]
        self.assertEqual(row["remote_model"], "llama3.2")

    def test_attach_same_address_updates_one_row(self):
        port, _ = serve(self, ollama({"qwen3:8b": 32768}))
        first = endpoints.attach(f"127.0.0.1:{port}")["connection"]
        second = endpoints.attach(f"http://127.0.0.1:{port}/v1/", model="llama3.2")[
            "connection"
        ]
        self.assertEqual(first["connection_id"], second["connection_id"])
        self.assertEqual(list(self.book.entries()), [first["connection_id"]])
        self.assertEqual(
            self.book.entries()[first["connection_id"]]["remote_model"], "llama3.2"
        )

    def test_attach_removes_new_row_when_key_store_fails(self):
        port, _ = serve(self, ollama({"qwen3:8b": 32768}))
        self.links.store_key.side_effect = RuntimeError("vault refused")
        with self.assertRaisesRegex(RuntimeError, "vault refused"):
            endpoints.attach(f"127.0.0.1:{port}", key="sk-fixture-key")
        self.assertEqual(self.book.entries(), {})
        self.links.store_key.side_effect = None
        kept = endpoints.attach(f"127.0.0.1:{port}")["connection"]
        self.links.store_key.side_effect = RuntimeError("vault refused")
        with self.assertRaisesRegex(RuntimeError, "vault refused"):
            endpoints.attach(f"127.0.0.1:{port}", key="sk-fixture-key")
        self.assertEqual(list(self.book.entries()), [kept["connection_id"]])

    def test_attach_with_host_uses_remote_ident(self):
        port, _ = serve(
            self, api(listing("coder", owned_by="vllm", max_model_len=65536))
        )
        ident = endpoints.remote_ident("u@box", 8000)
        self.assertRegex(ident, r"\A[0-9a-f]{16}\Z")
        self.assertEqual(ident, endpoints.remote_ident("u@box", "8000"))
        self.assertNotEqual(ident, endpoints.remote_ident("u@box", 8001))
        local = endpoints.attach(base(port))["connection"]
        row = endpoints.attach(base(port), host="u@box", remote_port=8000)["connection"]
        self.assertEqual(row["connection_id"], ident)
        self.assertNotEqual(local["connection_id"], ident)
        self.assertEqual((row["host"], row["remote_port"]), ("u@box", 8000))
        self.assertEqual(row["name"], "vLLM · coder on u@box")
        again = endpoints.attach(base(port), host="u@box", remote_port=8000)[
            "connection"
        ]
        self.assertEqual(again["connection_id"], ident)
        self.assertEqual(
            sorted(self.book.entries()), sorted([ident, local["connection_id"]])
        )


class RefreshTests(Case):
    def test_refresh_updates_protocols_and_keeps_user_context(self):
        routes = ollama({"qwen3:8b": 32768})
        port, _ = serve(self, routes)
        user = endpoints.attach(f"127.0.0.1:{port}", context=200000)["connection"]
        rows = self.book.entries()
        rows["0123456789abcdef"] = dict(
            rows[user["connection_id"]], connection_id="0123456789abcdef"
        )
        rows["0123456789abcdef"].update(context_window=32768, context_source="server")
        for row in rows.values():
            row["probed"] = 1
        self.book.write(rows)
        routes[("POST", "/v1/responses")] = MISSING
        routes[("GET", "/api/ps")] = (
            200,
            {},
            {"models": [{"name": "qwen3:8b", "context_length": 65536}]},
        )
        result = endpoints.refresh(user["connection_id"])
        saved = result["connection"]
        self.assertEqual(
            (saved["context_window"], saved["context_source"]), (200000, "user")
        )
        self.assertEqual(
            saved["protocols"], {"chat": True, "responses": False, "messages": True}
        )
        self.assertGreater(saved["probed"], 1)
        self.assertEqual(result["auth"], "none")
        self.assertEqual(
            result["models"][0], {"id": "qwen3:8b", "context": 65536, "loaded": True}
        )
        self.assertEqual(result["warnings"], [])
        self.assertEqual(self.book.entries()[user["connection_id"]], saved)
        followed = endpoints.refresh("0123456789abcdef")["connection"]
        self.assertEqual(
            (followed["context_window"], followed["context_source"]), (65536, "server")
        )
        self.assertGreater(followed["probed"], 1)
        routes[("GET", "/api/ps")] = (200, {}, {"models": []})
        result = endpoints.refresh("0123456789abcdef")
        self.assertNotIn("context_window", result["connection"])
        self.assertNotIn("context_source", result["connection"])
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("so 32,768 tokens is assumed", result["warnings"][0])
        kept = endpoints.refresh(user["connection_id"])
        self.assertEqual(
            (kept["connection"]["context_window"], kept["warnings"]), (200000, [])
        )
        self.assertTrue(all(self.book.trusted))

    def test_refresh_errors(self):
        self.quick()
        quiet = closed_port()
        self.book.write(
            {
                "aaaaaaaaaaaaaaaa": dict(
                    provider="OpenAI",
                    endpoint="https://api.openai.com/v1",
                    connection_id="aaaaaaaaaaaaaaaa",
                ),
                "bbbbbbbbbbbbbbbb": dict(
                    provider="Attached endpoint",
                    endpoint=base(quiet),
                    connection_id="bbbbbbbbbbbbbbbb",
                    remote_model="x",
                ),
            }
        )
        for ident in ("aaaaaaaaaaaaaaaa", "missing", ""):
            with self.assertRaisesRegex(
                ValueError, "Only attached endpoints can be checked again."
            ):
                endpoints.refresh(ident)
        with self.assertRaises(ValueError) as caught:
            endpoints.refresh("bbbbbbbbbbbbbbbb")
        self.assertEqual(
            str(caught.exception),
            f"Nothing answered at {base(quiet)}. Start the server, then check again.",
        )
        self.assertNotIn("probed", self.book.entries()["bbbbbbbbbbbbbbbb"])

    def test_refresh_keeps_last_results_when_the_key_is_refused(self):
        port, hits = serve(self, tabby())
        row = endpoints.attach(f"127.0.0.1:{port}", key="right")["connection"]
        self.assertEqual(
            (row["context_window"], row["protocols"]["chat"]), (24576, True)
        )
        self.keys[row["connection_id"]] = "expired"
        result = endpoints.refresh(row["connection_id"])
        self.assertEqual(
            (result["auth"], result["models"], result["warnings"]), ("rejected", [], [])
        )
        self.assertEqual(result["connection"]["protocols"], row["protocols"])
        self.assertEqual(result["connection"]["context_window"], 24576)
        self.assertEqual(
            [h for h in hits if h[1] == "/v1/models"][-1][2].get("x-api-key"), "expired"
        )
        del self.keys[row["connection_id"]]
        result = endpoints.refresh(row["connection_id"])
        self.assertEqual(result["auth"], "required")
        self.assertEqual(
            result["warnings"],
            ["TabbyAPI needs its API key before models can be listed."],
        )
        self.assertEqual(result["connection"]["context_window"], 24576)

    def test_refresh_keeps_an_identified_kind_when_the_key_is_refused(self):
        def models(headers, body):
            if headers.get("Authorization") == "Bearer right":
                return 200, {}, listing("coder", owned_by="vllm", max_model_len=65536)
            return 401, {}, {"error": "unauthorized"}

        port, _ = serve(self, api(None, routes={("GET", "/v1/models"): models}))
        row = endpoints.attach(f"127.0.0.1:{port}", key="right")["connection"]
        self.assertEqual(row["kind"], "vllm")
        result = endpoints.refresh(row["connection_id"])
        self.assertEqual(
            (result["auth"], result["connection"]["kind"]), ("required", "vllm")
        )
        self.assertEqual(
            result["warnings"], ["vLLM needs its API key before models can be listed."]
        )
        locked, _ = serve(self, tabby())
        rows = self.book.entries()
        rows[row["connection_id"]]["endpoint"] = base(locked)
        self.book.write(rows)
        moved = endpoints.refresh(row["connection_id"])["connection"]
        self.assertEqual(moved["kind"], "tabbyapi")

    def test_refresh_respects_changes_made_during_the_check(self):
        port, _ = serve(self, ollama({"qwen3:8b": 32768}))
        row = endpoints.attach(f"127.0.0.1:{port}")["connection"]
        ident = row["connection_id"]
        real = endpoints._probe_base

        def renamed(*args):
            rows = self.book.entries()
            rows[ident].update(name="Desk Ollama", remote_model="llama3.2")
            self.book.write(rows)
            return real(*args)

        with patch.object(endpoints, "_probe_base", renamed):
            result = endpoints.refresh(ident)
        saved = result["connection"]
        self.assertEqual(
            (saved["name"], saved["remote_model"]), ("Desk Ollama", "llama3.2")
        )
        self.assertNotIn("context_window", saved)
        self.assertIn("llama3.2", result["warnings"][0])

        def removed(*args):
            self.book.remove(ident)
            return real(*args)

        with patch.object(endpoints, "_probe_base", removed):
            with self.assertRaisesRegex(
                ValueError, "This connection no longer exists. Refresh the list."
            ):
                endpoints.refresh(ident)
        self.assertEqual(self.book.entries(), {})

    def test_probe_protocols_reports_the_three_routes(self):
        routes = api(listing("coder"), routes={("POST", "/v1/responses"): MISSING})
        routes[("POST", "/v1/messages")] = (500, {}, b"")
        port, hits = serve(self, routes)
        self.assertEqual(
            endpoints.probe_protocols(base(port), " right\n"),
            {"chat": True, "responses": False, "messages": None},
        )
        self.assertEqual([h[0] for h in hits], ["POST"] * 3)
        self.assertEqual(hits[0][2].get("Authorization"), "Bearer right")
        self.assertIsNone(endpoints.probe_protocols(base(port))["messages"])
        self.assertIsNone(hits[-1][2].get("Authorization"))
        self.quick()
        self.assertEqual(
            endpoints.probe_protocols(base(closed_port())),
            {"chat": None, "responses": None, "messages": None},
        )

    def test_refresh_uses_the_forwarded_address_for_remote_rows(self):
        port, _ = serve(
            self, api(listing("coder", owned_by="vllm", max_model_len=65536))
        )
        stale = base(closed_port())
        ident = endpoints.remote_ident("u@box", 8000)
        row = dict(
            provider="Attached endpoint",
            connection_id=ident,
            name="Box",
            endpoint=stale,
            remote_model="coder",
            harness="OMP",
            host="u@box",
            remote_port=8000,
        )
        managed = dict(
            row, connection_id="cccccccccccccccc", endpoint=base(port), context=8192
        )
        del managed["host"], managed["remote_port"]
        self.book.write({ident: row, "cccccccccccccccc": managed})
        self.links._forward.return_value = base(port)
        saved = endpoints.refresh(ident)["connection"]
        self.links._forward.assert_called_once_with(ident, row)
        self.assertEqual(
            (saved["endpoint"], saved["kind"], saved["context_window"]),
            (base(port), "vllm", 65536),
        )
        self.assertEqual((saved["host"], saved["remote_port"]), ("u@box", 8000))
        kept = endpoints.refresh("cccccccccccccccc")["connection"]
        self.links._forward.assert_called_once()
        self.assertEqual(
            (kept["context"], kept["endpoint"], kept["kind"]),
            (8192, base(port), "vllm"),
        )
        self.assertNotIn("context_window", kept)

    def test_model_context_live_lookup(self):
        port, _ = serve(self, ollama({"qwen3:8b": 32768}))
        self.assertEqual(
            endpoints.model_context(base(port), "ollama", "qwen3:8b"), 32768
        )
        self.assertIsNone(endpoints.model_context(base(port), "ollama", "llama3.2"))
        self.assertIsNone(endpoints.model_context(base(port), "ollama", "unknown"))
        self.assertIsNone(endpoints.model_context(base(port), "not-a-kind", "qwen3:8b"))
        self.assertIsNone(endpoints.model_context(base(port), ["ollama"], "qwen3:8b"))
        self.assertIsNone(
            endpoints.model_context(base(port), "ollama", "qwen3:8b", "bad\nkey")
        )
        self.assertIsNone(
            endpoints.model_context(
                base(closed_port()), "ollama", "qwen3:8b", timeout=0.3
            )
        )
        self.assertIsNone(
            endpoints.model_context("http://127.0.0.1:port/v1", "ollama", "qwen3:8b")
        )
        locked, hits = serve(self, tabby())
        self.assertIsNone(
            endpoints.model_context(base(locked), "tabbyapi", "exl2-coder")
        )
        self.assertEqual(
            endpoints.model_context(base(locked), "tabbyapi", "exl2-coder", "right"),
            24576,
        )
        self.assertEqual(hits[-1][2].get("Authorization"), "Bearer right")


class RemoteTests(Case):
    def test_remote_script_is_self_contained(self):
        script = endpoints.remote_probe_script()
        ast.parse(script, feature_version=(3, 8))
        self.assertNotIn("requests", script)
        self.assertNotIn("providers", script)
        self.assertNotIn("removesuffix", script)
        self.assertIn(json.dumps(providers.SWEEP_PORTS), script)
        port, _ = serve(self, ollama({"qwen3:8b": 32768}))
        script = endpoints.remote_probe_script(ports=[port], control=[])
        self.assertIn("[[%d], [], {" % port, script)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            exec(compile(script, "<remote probe>", "exec"), {})
        lines = printed.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        doc = json.loads(lines[0])
        self.assertEqual(set(doc), {"v", "endpoints", "hints"})
        self.assertEqual(
            [(e["port"], e["kind"]) for e in doc["endpoints"]], [(port, "ollama")]
        )
        self.assertEqual(
            doc["endpoints"][0]["models"][0],
            {"id": "qwen3:8b", "context": 32768, "loaded": True},
        )
        parsed = endpoints.parse_remote(lines[0])
        self.assertEqual(parsed["endpoints"][0]["label"], "Ollama")
        self.assertEqual(parsed["endpoints"][0]["protocols"], ALL)

    def test_remote_script_survives_a_dripping_port(self):
        locked, _ = serve(self, tabby())
        slow = pour(self, 1, 0.01)
        script = endpoints.remote_probe_script(ports=[slow, locked], control=[])
        script = script.replace("_http, 1.0)", "_http, 0.2)")
        printed = io.StringIO()

        def run():
            with contextlib.redirect_stdout(printed):
                exec(compile(script, "<remote probe>", "exec"), {})

        stuck, _ = bounded(run)
        self.assertFalse(stuck)
        doc = json.loads(printed.getvalue())
        self.assertEqual(
            [(e["port"], e["kind"], e["auth"]) for e in doc["endpoints"]],
            [(locked, "tabbyapi", "required")],
        )
        self.assertEqual(doc["hints"], [])

    def test_remote_script_runs_on_stdin_of_an_isolated_interpreter(self):
        port, _ = serve(
            self, api(listing("coder", owned_by="vllm", max_model_len=65536))
        )
        done = subprocess.run(
            [sys.executable, "-I", "-"],
            input=endpoints.remote_probe_script(ports=[port], control=[]),
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        doc = json.loads(done.stdout)
        self.assertEqual(done.stdout.count("\n"), 1)
        self.assertEqual(
            [(e["port"], e["kind"], e["base"]) for e in doc["endpoints"]],
            [(port, "vllm", base(port))],
        )

    def test_parse_remote_validates(self):
        self.book.write(
            {
                "dddddddddddddddd": dict(
                    provider="Attached endpoint", endpoint="http://127.0.0.1:8000/v1"
                )
            }
        )
        models = [
            {"id": "coder", "context": 65536, "loaded": True},
            {"id": "bad\x07id", "context": 4096, "loaded": True},
            {"id": "-leading", "context": 4096},
            {"id": "x" * 301},
            {"id": 7},
            "text",
            {"id": "tiny", "context": 12, "loaded": "yes"},
            {"id": "flag", "context": True, "loaded": None},
        ]
        entries = [
            {
                "port": 8000,
                "base": "http://evil.example/v1",
                "kind": "vllm",
                "auth": "none",
                "models": models,
                "protocols": {
                    "chat": True,
                    "responses": "yes",
                    "messages": False,
                    "extra": True,
                },
                "label": "<b>x</b>",
            },
            {"port": 8000, "kind": "ollama"},
            {"port": 0, "kind": "ollama"},
            {"port": 70000, "kind": "ollama"},
            {"port": "11434", "kind": "ollama"},
            {"port": True, "kind": "ollama"},
            {"kind": "ollama"},
            "text",
            {
                "port": 9000,
                "kind": "made-up",
                "auth": "open",
                "models": "x",
                "protocols": None,
            },
            {"port": 9001, "kind": ["ollama"], "auth": "required"},
        ]
        text = (
            "Welcome to box\n\n"
            + json.dumps(
                {
                    "v": 1,
                    "endpoints": entries,
                    "hints": ["lmstudio-server-off", "other", 3],
                }
            )
            + "\n\n"
        )
        parsed = endpoints.parse_remote(text)
        self.assertEqual([e["port"] for e in parsed["endpoints"]], [8000, 9000, 9001])
        first, second, third = parsed["endpoints"]
        self.assertEqual(first["base"], "http://127.0.0.1:8000/v1")
        self.assertEqual(
            (first["kind"], first["label"], first["auth"]), ("vllm", "vLLM", "none")
        )
        self.assertEqual(
            first["models"],
            [
                {"id": "coder", "context": 65536, "loaded": True},
                {"id": "tiny", "context": None, "loaded": None},
                {"id": "flag", "context": None, "loaded": None},
            ],
        )
        self.assertEqual(
            first["protocols"], {"chat": True, "responses": None, "messages": False}
        )
        self.assertEqual(first["connection_id"], "")
        self.assertEqual(first["compat"]["Claude Code"]["ok"], False)
        self.assertEqual(
            (second["kind"], second["auth"], second["models"]), ("generic", "none", [])
        )
        self.assertEqual(
            second["protocols"], {"chat": None, "responses": None, "messages": None}
        )
        self.assertEqual((third["kind"], third["auth"]), ("generic", "required"))
        self.assertEqual([h["id"] for h in parsed["hints"]], ["lmstudio-server-off"])
        self.assertIn("start the server", parsed["hints"][0]["text"])
        many = [{"id": f"m{n}"} for n in range(600)]
        cut = endpoints.parse_remote(
            json.dumps({"endpoints": [{"port": 1, "models": many}]})
        )
        self.assertEqual(len(cut["endpoints"][0]["models"]), 500)
        self.assertEqual(
            endpoints.parse_remote('{"endpoints": []}'), {"endpoints": [], "hints": []}
        )
        for bad in (
            "",
            "bash: python3: command not found",
            "[]",
            '{"endpoints": {}}',
            "{}",
            None,
            "[" * 100000,
        ):
            with self.assertRaises(ValueError) as caught:
                endpoints.parse_remote(bad)
            self.assertEqual(
                str(caught.exception),
                "The machine did not return a readable scan. "
                "Check that python3 is installed there, then scan again.",
            )


class DispatchTests(Case):
    def test_actions_route_and_ignore_untrusted_fields(self):
        port, _ = serve(self, ollama({"qwen3:8b": 32768}))
        address = f"127.0.0.1:{port}"
        with self.assertRaisesRegex(ValueError, "Unknown endpoint action."):
            endpoints.dispatch("endpoint-delete", {})
        with self.assertRaisesRegex(ValueError, "Unknown endpoint action."):
            endpoints.dispatch("endpoint-scan-all", None)
        with (
            patch.object(providers, "SWEEP_PORTS", [port]),
            patch.object(providers, "LMSTUDIO_CONTROL_PORTS", []),
        ):
            found = endpoints.dispatch("endpoint-scan", {})
        self.assertEqual([e["kind"] for e in found["endpoints"]], ["ollama"])
        self.assertEqual(
            endpoints.dispatch("endpoint-probe", {"address": address, "key": 5})[
                "auth"
            ],
            "none",
        )
        payload = {
            "address": address,
            "context": "8192",
            "host": "u@box",
            "remote_port": 22,
            "name": ["x"],
        }
        result = endpoints.dispatch("endpoint-attach", payload)
        row = result["connection"]
        self.assertEqual(set(result), {"connection", "stored", "message", "warnings"})
        self.assertEqual(
            (row["context_window"], row["context_source"], row["harness"]),
            (8192, "user", "OMP"),
        )
        self.assertNotIn("host", row)
        self.assertNotIn("remote_port", row)
        self.assertEqual(found["endpoints"][0]["connection_id"], "")
        self.assertEqual(
            endpoints.dispatch("endpoint-probe", {"address": address})["connection_id"],
            row["connection_id"],
        )
        for empty in ("", " ", 0, None):
            again = endpoints.dispatch(
                "endpoint-attach", {"address": address, "context": empty}
            )["connection"]
            self.assertEqual(
                (again["context_window"], again["context_source"]), (32768, "server")
            )
        for bad in ("lots", "9" * 5000, "99999999999"):
            with self.assertRaisesRegex(
                ValueError, "Enter a context size between 1,024 and 10,000,000 tokens."
            ):
                endpoints.dispatch(
                    "endpoint-attach", {"address": address, "context": bad}
                )
        checked = endpoints.dispatch(
            "endpoint-refresh", {"connection_id": row["connection_id"]}
        )
        self.assertEqual(set(checked), {"connection", "models", "auth", "warnings"})
        with self.assertRaisesRegex(
            ValueError, "Only attached endpoints can be checked again."
        ):
            endpoints.dispatch("endpoint-refresh", {"connection_id": ["x"]})


class RealBookTests(unittest.TestCase):
    """attach and refresh against the real connection book and key lookup."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.data = Path(folder.name)
        self.vault = {}
        for patcher in (
            patch.object(account_book, "DATA", self.data),
            patch.object(connections, "DATA", self.data),
            patch.object(connections, "_MEMORY", {}),
            patch.object(
                connections,
                "vault_status",
                return_value={"available": True, "backend": "test", "reason": ""},
            ),
            patch(
                "keyring.get_password", side_effect=lambda _, slot: self.vault.get(slot)
            ),
            patch(
                "keyring.set_password",
                side_effect=lambda _, slot, value: self.vault.update({slot: value}),
            ),
            patch(
                "keyring.delete_password",
                side_effect=lambda _, slot: self.vault.pop(slot),
            ),
            patch.dict(os.environ),
            patch.object(endpoints, "PROBE_TIMEOUT", 2.0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in [n for n in os.environ if n.startswith("WORKBENCH_KEY_")]:
            del os.environ[name]

    def stored(self):
        return (self.data / "connections.json").read_text(encoding="utf8")

    def test_attach_refresh_and_remove_a_local_server(self):
        routes = ollama({"qwen3:8b": 32768})
        port, hits = serve(self, routes)
        result = endpoints.attach(f"127.0.0.1:{port}", key="sk-local", harness="Codex")
        row = result["connection"]
        ident = row["connection_id"]
        self.assertRegex(ident, r"\A[0-9a-f]{16}\Z")
        self.assertEqual(account_book.entries(), {ident: row})
        self.assertEqual(
            {name: row[name] for name in EXTRA if name in row},
            {
                "kind": "ollama",
                "protocols": ALL,
                "context_window": 32768,
                "context_source": "server",
                "probed": row["probed"],
            },
        )
        self.assertEqual((row["endpoint"], row["harness"]), (base(port), "Codex"))
        self.assertEqual(
            (result["stored"], result["message"]),
            ("vault", "Key saved in the system credential vault."),
        )
        self.assertEqual(self.vault, {f"{base(port)}#{ident}": "sk-local"})
        self.assertNotIn("sk-local", self.stored())
        found = endpoints.probe(f"127.0.0.1:{port}")
        self.assertEqual(found["connection_id"], ident)
        self.assertTrue(found["compat"]["Codex"]["ok"])
        self.assertFalse(found["compat"]["Hermes"]["ok"])
        again = endpoints.attach(base(port))["connection"]
        self.assertEqual(again["connection_id"], ident)
        routes[("GET", "/api/ps")] = (200, {}, {"models": []})
        routes[("POST", "/v1/responses")] = MISSING
        del hits[:]
        checked = endpoints.refresh(ident)
        saved = account_book.entries()[ident]
        self.assertEqual(checked["connection"], saved)
        self.assertEqual(checked["auth"], "accepted")
        self.assertNotIn("context_window", saved)
        self.assertNotIn("context_source", saved)
        self.assertEqual(
            saved["protocols"], {"chat": True, "responses": False, "messages": True}
        )
        self.assertFalse(connections.compat("Attached endpoint", saved)["Codex"]["ok"])
        self.assertEqual(hits[0][2].get("Authorization"), "Bearer sk-local")
        self.assertEqual(
            connections.context_for(
                {"provider": "Attached endpoint", "context": 131072},
                saved,
                base(port),
                "qwen3:8b",
            ),
            (32768, "default"),
        )
        self.assertEqual(
            account_book.remove(ident),
            {"removed": True, "key_removed": True, "message": "Connection removed."},
        )
        self.assertEqual((account_book.entries(), self.vault), ({}, {}))

    def test_remote_server_row_keeps_its_machine(self):
        port, _ = serve(
            self, api(listing("coder", owned_by="vllm", max_model_len=65536))
        )
        ident = endpoints.remote_ident("u@box", 8000)
        self.assertEqual(ident, machines.link_id("u@box", 8000))
        result = endpoints.attach(base(port), key="k", host="u@box", remote_port=8000)
        row = result["connection"]
        self.assertEqual(
            (row["connection_id"], row["host"], row["remote_port"], row["kind"]),
            (ident, "u@box", 8000, "vllm"),
        )
        self.assertEqual(self.vault, {f"ssh://u@box:8000#{ident}": "k"})
        self.assertFalse(account_book.managed(row))
        self.assertEqual(endpoints.probe(base(port))["connection_id"], "")
        with patch.object(machines, "forward", return_value=port) as forward:
            checked = endpoints.refresh(ident)
            forward.assert_called_once_with("u@box", 8000)
        saved = account_book.entries()[ident]
        self.assertEqual(checked["connection"], saved)
        self.assertEqual(
            (saved["host"], saved["remote_port"], saved["context_window"]),
            ("u@box", 8000, 65536),
        )
        self.assertEqual(connections.bound(dict(saved, endpoint="http://x/v1")), saved)
        with patch.object(machines, "drop_forward") as drop:
            self.assertTrue(account_book.remove(ident)["removed"])
            drop.assert_called_once_with("u@box", 8000)
        self.assertEqual((account_book.entries(), self.vault), ({}, {}))


if __name__ == "__main__":
    unittest.main()
