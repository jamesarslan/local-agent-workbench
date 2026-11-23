"""Runtime: engine command lines, process ownership, cancellable start and stop, the vLLM worker and capacity."""

import atexit
from collections import namedtuple
import gc
import itertools
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import MagicMock, Mock, patch
import warnings

from filelock import FileLock

if "runtime" not in sys.modules:
    _data = tempfile.mkdtemp(prefix="workbench-tests-")
    atexit.register(shutil.rmtree, _data, ignore_errors=True)
    os.environ["WORKBENCH_DATA"] = _data

import psutil
import requests

import runtime
import vllm_worker

ROOT = Path(__file__).resolve().parents[1]
IMPORT_DATA = runtime.DATA
SERVER = "llama-server.exe" if os.name == "nt" else "llama-server"
FAKE = """
import http.server, json, sys, time
port, keyfile, delay = int(sys.argv[1]), sys.argv[2], float(sys.argv[3])
time.sleep(delay)
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        good = self.headers.get("Authorization") == "Bearer " + open(keyfile).read().strip()
        body = json.dumps({"data": [{"id": "local-agent"}]}).encode() if good else b"{}"
        self.send_response(200 if good else 401)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    log_message = lambda *args: None
http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""
SLEEP = "import time; time.sleep(30)"
LABEL = {"cuda": "CUDA", "vulkan": "Vulkan", "cpu": "CPU", "metal": "Metal"}
Address = namedtuple("Address", "ip port")
Connection = namedtuple("Connection", "status laddr")


class FakeProcess:
    def __init__(self, pid=424242, born=1000.0, cmd=("engine", "--port", "1")):
        self.pid, self.born, self.cmd = pid, born, cmd
        self.state, self.running = psutil.STATUS_RUNNING, True
        self.tree, self.sockets, self.ended = [], [], False
        self.exits, self.killed = False, False

    def create_time(self):
        return self.born

    def cmdline(self):
        return list(self.cmd)

    def is_running(self):
        return self.running

    def status(self):
        return self.state

    def children(self, recursive=False):
        return list(self.tree)

    def net_connections(self, kind="inet"):
        return list(self.sockets)

    def terminate(self):
        self.ended = True
        if self.exits:
            self.state = psutil.STATUS_ZOMBIE

    def kill(self):
        self.killed, self.state = True, psutil.STATUS_ZOMBIE


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def missing_pid():
    pid = 4194301
    while psutil.pid_exists(pid):
        pid += 2
    return pid


def computer(name, backend, installed="", supported=True):
    found = dict(system=name, supported=supported, backend=backend)
    return types.SimpleNamespace(
        detect=lambda: found, installed_backend=lambda: installed, LABEL=LABEL
    )


class RuntimeCase(unittest.TestCase):
    def setUp(self):
        self.data = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.object(runtime, "DATA", self.data))
        self.enterContext(
            patch.object(
                runtime, "LOCK", FileLock(str(self.data / "server.lock"), timeout=5)
            )
        )
        self.enterContext(patch.object(runtime, "POLL", 0.02))
        self.enterContext(patch.dict(os.environ))
        for name in ("WORKBENCH_RUNTIME", "WORKBENCH_FLASH_CONFIG", "LD_LIBRARY_PATH"):
            os.environ.pop(name, None)

    def settings(self, **changes):
        return (
            runtime.DEFAULTS | dict(model="model", workspace=str(self.data)) | changes
        )

    def system(self, name):
        self.enterContext(patch.object(runtime, "system", return_value=name))

    def custom(self, *argv, **changes):
        path = self.data / "engine.json"
        path.write_text(json.dumps({"argv": list(argv)}), encoding="utf8")
        return self.settings(engine="Custom", engine_manifest=str(path), **changes)

    def model(self, name="model.gguf"):
        path = self.data / name
        path.write_text("weights", encoding="utf8")
        return str(path)

    def llama(self, base="runtime/llama-native-b12633", build="llama-b12633"):
        folder = self.data / base / build
        folder.mkdir(parents=True)
        (folder / "libllama.so").write_text("", encoding="utf8")
        (folder / SERVER).write_text("", encoding="utf8")
        return folder / SERVER

    def save(self, **rec):
        runtime.write_json(self.data / "running.json", rec)

    def plugins(self):
        fake = types.SimpleNamespace(plugin_ids=lambda s: [], registry=lambda: {})
        self.enterContext(patch.dict(sys.modules, {"project_profiles": fake}))


class CommandTests(RuntimeCase):
    def test_wsl_command_never_uses_a_shell(self):
        argv = runtime.wsl_argv(["serve", "abc"])
        self.assertEqual(
            argv,
            ["wsl", "-d", "AgentWorkbench-Web", "-u", "root", "--cd", str(runtime.ROOT)]
            + ["--exec", "/usr/bin/python3", "vllm_worker.py", "serve", "abc"],
        )
        self.assertNotIn("--", argv)

    def test_vllm_parser_is_validated(self):
        self.system("linux")
        s = self.settings(engine="vLLM", vllm_model="owner/model", context=4096)
        with self.assertRaisesRegex(ValueError, "tool parser"):
            runtime.backend(s | dict(vllm_parser="hermes;id>/tmp/x"))
        cmd, env = runtime.backend(s | dict(vllm_parser="hermes"))
        at = cmd.index("--tool-call-parser")
        self.assertEqual(
            cmd[at - 1 : at + 2],
            ["--enable-auto-tool-choice", "--tool-call-parser", "hermes"],
        )
        self.assertEqual(
            cmd[:2], [sys.executable, str(runtime.ROOT / "vllm_worker.py")]
        )
        self.assertNotIn("--tool-call-parser", runtime.backend(s)[0])
        self.assertNotIn(env["VLLM_API_KEY"], cmd)

    def test_vllm_on_windows_goes_through_wsl_exec(self):
        self.system("windows")
        os.environ["WSLENV"] = "EXISTING"
        s = self.settings(engine="vLLM", vllm_model="owner/model", context=4096)
        cmd, env = runtime.backend(s)
        tag = env["WORKBENCH_JOB_ID"]
        self.assertEqual(cmd[: len(runtime.wsl_argv([]))], runtime.wsl_argv([]))
        self.assertEqual(cmd[10:13], ["serve", tag, "owner/model"])
        self.assertEqual(env["WSLENV"], "EXISTING:VLLM_API_KEY:HF_TOKEN:WORKBENCH_VLLM")
        self.assertNotIn(env["VLLM_API_KEY"], cmd)
        self.assertNotIn("--", cmd)

    def test_macos_refuses_cuda_engines_before_field_errors(self):
        self.system("macos")
        for changes in (
            dict(engine="vLLM", vllm_model=""),
            dict(engine="Strata", model=self.model("Inkling-x.strata")),
            dict(engine="Auto", model=self.model("Inkling-y.strata")),
        ):
            with self.assertRaisesRegex(ValueError, "Metal-compatible"):
                runtime.backend(self.settings(**changes))

    def test_custom_engine_without_manifest_is_a_clear_error(self):
        broken = self.data / "broken.json"
        broken.write_text("{not json", encoding="utf8")
        listed = self.data / "list.json"
        listed.write_text("[1]", encoding="utf8")
        for value in ("", str(self.data), str(broken), str(listed), 7):
            s = self.settings(engine="Custom", engine_manifest=value)
            self.assertEqual(runtime.serving_spec(s)["manifest"], {})
            with self.assertRaisesRegex(ValueError, "custom engine manifest"):
                runtime.backend(s)

    def test_custom_tokens_leave_other_braces(self):
        argv = ["x", '{"enable_thinking":false}', "{port}", "{unknown}"]
        with patch.object(runtime, "PORT", 4321):
            cmd, env = runtime.backend(self.custom(*argv))
        self.assertEqual(cmd, ["x", '{"enable_thinking":false}', "4321", "{unknown}"])
        self.assertNotIn(env["WORKBENCH_API_KEY"], cmd)

    def test_custom_batch_file_refuses_shell_characters(self):
        batch = ["C:\\tools\\serve.cmd", "--model", "{model}", "--port", "{port}"]
        risky = "C:\\models\\x&calc.gguf"
        self.system("windows")
        for model in (risky, "C:\\models\\%PATH%.gguf", 'C:\\models\\a"b.gguf'):
            with self.subTest(model=model):
                with self.assertRaisesRegex(ValueError, r"\.cmd or \.bat file"):
                    runtime.backend(self.custom(*batch, model=model))
        with self.assertRaisesRegex(ValueError, r"\.cmd or \.bat file"):
            runtime.backend(
                self.custom("C:\\tools\\SERVE.BAT.", "{model}", model=risky)
            )
        plain = runtime.backend(self.custom(*batch, model="C:\\models\\x y.gguf"))[0]
        self.assertEqual(plain[:3], [batch[0], "--model", "C:\\models\\x y.gguf"])
        program = runtime.backend(
            self.custom("C:\\tools\\serve.exe", "{model}", model=risky)
        )[0]
        self.assertEqual(program, ["C:\\tools\\serve.exe", risky])
        with patch.object(runtime, "system", return_value="linux"):
            self.assertEqual(
                runtime.backend(self.custom("serve.cmd", "{model}", model=risky))[0],
                ["serve.cmd", risky],
            )

    def test_library_path_uses_only_the_selected_build(self):
        tested = self.data / "runtime/llama-native-b12633"
        for name in ("llama-b12633", "cudart-x"):
            (tested / name).mkdir(parents=True)
            (tested / name / "libx.so.1").write_text("", encoding="utf8")
        exe = self.llama("updates/llama-b99999", "llama-b99999")
        chosen = self.data / "updates/llama-b99999"
        parts = runtime.library_path(exe, [chosen, self.data / "absent"]).split(
            os.pathsep
        )
        self.assertEqual(parts[0], str(exe.parent))
        self.assertEqual(
            [p for p in parts if p != "/usr/lib/wsl/lib"], [str(exe.parent)]
        )
        self.assertFalse(any(str(tested) in p for p in parts))
        inherited = os.pathsep.join(["/opt/lib", "", str(exe.parent)])
        parts = runtime.library_path(exe, [chosen], inherited).split(os.pathsep)
        self.assertEqual(parts[-1], "/opt/lib")
        self.assertNotIn("", parts)
        self.assertEqual(len(parts), len(set(parts)))
        both = runtime.library_path(exe, [chosen, tested]).split(os.pathsep)
        self.assertEqual(
            both[:3],
            [str(exe.parent), str(tested / "cudart-x"), str(tested / "llama-b12633")],
        )

    def test_only_llama_and_strata_get_a_library_path(self):
        self.system("linux")
        exe = self.llama()
        llama = self.settings(engine="llama.cpp", model=self.model())
        vllm = self.settings(engine="vLLM", vllm_model="owner/model")
        custom = self.custom("program")
        cmd, env = runtime.backend(llama)
        self.assertEqual(cmd[0], str(exe))
        self.assertEqual(env["LD_LIBRARY_PATH"].split(os.pathsep)[0], str(exe.parent))
        self.assertNotIn("LD_LIBRARY_PATH", runtime.backend(vllm)[1])
        self.assertNotIn("LD_LIBRARY_PATH", runtime.backend(custom)[1])
        os.environ["LD_LIBRARY_PATH"] = "/inherited/lib"
        parts = runtime.backend(llama)[1]["LD_LIBRARY_PATH"].split(os.pathsep)
        self.assertEqual((parts[0], parts[-1]), (str(exe.parent), "/inherited/lib"))
        self.assertEqual(runtime.backend(vllm)[1]["LD_LIBRARY_PATH"], "/inherited/lib")
        self.assertEqual(
            runtime.backend(custom)[1]["LD_LIBRARY_PATH"], "/inherited/lib"
        )

    def test_other_systems_get_no_library_path(self):
        self.system("macos")
        self.llama()
        env = runtime.backend(self.settings(engine="llama.cpp", model=self.model()))[1]
        self.assertNotIn("LD_LIBRARY_PATH", env)

    def test_missing_engine_message_names_this_system(self):
        self.assertIn("Setup engines.cmd", runtime.install_hint("windows"))
        for name in ("macos", "linux"):
            self.assertIn(
                "sh workbench.sh setup_engines.py", runtime.install_hint(name)
            )
            self.assertNotIn(".cmd", runtime.install_hint(name))
        with self.assertRaises(runtime.EngineMissing) as caught:
            runtime.runtime("llama-native-b12633", "llama-server.exe")
        self.assertIsInstance(caught.exception, ValueError)
        self.assertEqual(caught.exception.engine, "llama.cpp")
        self.assertIn(runtime.install_hint(), str(caught.exception))
        with self.assertRaises(runtime.EngineMissing) as caught:
            runtime.runtime("strata-inkling-0.1.65", "strata-inkling.exe", "Updated")
        self.assertEqual(caught.exception.engine, "Strata")
        self.assertIn("Update", str(caught.exception))

    def test_engine_is_found_in_the_data_folder_or_the_override(self):
        exe = self.llama()
        folder = self.data / "runtime/llama-native-b12633"
        self.assertEqual(
            runtime.locate("llama-native-b12633", "llama-server.exe"), (folder, exe)
        )
        self.assertEqual(
            runtime.runtime("llama-native-b12633", "llama-server.exe"), exe
        )
        other = self.llama("elsewhere/llama-native-b12633")
        os.environ["WORKBENCH_RUNTIME"] = str(self.data / "elsewhere")
        self.assertEqual(
            runtime.runtime("llama-native-b12633", "llama-server.exe"), other
        )
        newer = self.llama("updates/llama-b99999", "llama-b99999")
        chosen = str(self.data / "updates/llama-b99999")
        runtime.write_json(
            self.data / "updates.json", {"engines": {"ggml-org/llama.cpp": chosen}}
        )
        found = runtime.locate("llama-native-b12633", "llama-server.exe", "Updated")
        self.assertEqual(found, (Path(chosen), newer))

    def test_parent_folder_is_not_trusted(self):
        self.assertFalse(hasattr(runtime, "PARENT"))
        self.assertEqual(runtime.DEFAULTS["library"], str(IMPORT_DATA / "models"))
        self.assertEqual(
            (runtime.DEFAULTS["start_timeout"], runtime.DEFAULTS["engine_args"]),
            (0, []),
        )

    def test_strata_install_is_checked_before_the_tokenizer(self):
        self.system("linux")
        s = self.settings(engine="Strata", model=self.model("Inkling-x.strata"))
        run = self.enterContext(
            patch.object(subprocess, "run", side_effect=AssertionError("ran"))
        )
        with patch.dict(sys.modules, {"setup_engines": computer("linux", "cuda")}):
            with self.assertRaises(runtime.EngineMissing) as caught:
                runtime.backend(s)
        self.assertEqual(caught.exception.engine, "Strata")
        self.assertIn(runtime.install_hint("linux"), str(caught.exception))
        with patch.dict(sys.modules, {"setup_engines": computer("linux", "vulkan")}):
            with self.assertRaisesRegex(
                runtime.EngineMissing, "NVIDIA GPU with a working driver"
            ):
                runtime.backend(s)
        server = self.data / "Strata/terminal/serve/server.py"
        server.parent.mkdir(parents=True)
        server.write_text("", encoding="utf8")
        with self.assertRaises(runtime.EngineMissing):
            runtime.backend(s)
        engine = self.data / "runtime/strata-inkling-0.1.65"
        engine.mkdir(parents=True)
        (engine / SERVER.replace("llama-server", "strata-inkling")).write_text(
            "", encoding="utf8"
        )
        with self.assertRaisesRegex(
            ValueError, "tokenizer.json and chat_template.jinja"
        ):
            runtime.backend(s)
        run.assert_not_called()

    def test_strata_command_uses_its_own_libraries_first(self):
        self.system("linux")
        model = Path(self.model("Inkling-x.strata"))
        for name in ("tokenizer.json", "chat_template.jinja"):
            model.with_name(name).write_text("{}", encoding="utf8")
        server = self.data / "Strata/terminal/serve/server.py"
        server.parent.mkdir(parents=True)
        server.write_text("", encoding="utf8")
        engine = self.data / "runtime/strata-inkling-0.1.65"
        engine.mkdir(parents=True)
        exe = engine / SERVER.replace("llama-server", "strata-inkling")
        exe.write_text("", encoding="utf8")
        (engine / "libstrata.so").write_text("", encoding="utf8")
        llama = self.llama()
        s = self.settings(engine="Strata", model=str(model))
        with patch.object(
            subprocess, "run", side_effect=subprocess.CalledProcessError(1, "x")
        ) as run:
            with self.assertRaisesRegex(ValueError, "tokenizer could not be converted"):
                runtime.backend(s)
        options = run.call_args.kwargs
        self.assertEqual(
            (options["stdin"], options["timeout"]), (subprocess.DEVNULL, 300)
        )
        with patch.object(subprocess, "run") as run:
            cmd, env = runtime.backend(s)
        self.assertEqual(cmd[:3], [sys.executable, "-u", str(server)])
        self.assertEqual(
            env["LD_LIBRARY_PATH"].split(os.pathsep)[:2],
            [str(engine), str(llama.parent)],
        )
        self.assertNotIn(env["STRATA_API_KEY"], cmd)
        config = json.loads((self.data / "serve.json").read_text(encoding="utf8"))
        self.assertEqual((config["exe"], config["host"]), (str(exe), "127.0.0.1"))
        self.assertNotIn(env["STRATA_API_KEY"], json.dumps(config))

    def test_flash_preset_errors_are_sentences(self):
        self.system("linux")
        s = self.settings(engine="Flash preset", model=self.model("flash-next.gguf"))
        os.environ["WORKBENCH_FLASH_CONFIG"] = str(self.data / "absent.json")
        with self.assertRaisesRegex(ValueError, "WORKBENCH_FLASH_CONFIG"):
            runtime.backend(s)
        preset = self.data / "preset.json"
        preset.write_text(
            json.dumps(
                {
                    "args": ["--native", s["model"]],
                    "cwd": str(self.data / "gone"),
                    "exe": "x",
                }
            ),
            encoding="utf8",
        )
        os.environ["WORKBENCH_FLASH_CONFIG"] = str(preset)
        with self.assertRaisesRegex(
            ValueError, "referenced by the Flash Next preset is missing"
        ):
            runtime.backend(s)
        for text in (
            "{}",
            "{broken",
            "[1]",
            '{"args": ["--native"], "cwd": "x"}',
            '{"args": "--model x", "cwd": "x"}',
            json.dumps({"args": ["--native", s["model"]]}),
            json.dumps({"args": ["--native", s["model"]], "cwd": 7}),
        ):
            preset.write_text(text, encoding="utf8")
            with self.subTest(text=text), self.assertRaises(ValueError) as caught:
                runtime.backend(s)
            self.assertIn(
                f"preset at {preset} could not be read", str(caught.exception)
            )
            self.assertTrue(str(caught.exception).endswith("."))
        server = self.data / "strata/serve/server.py"
        server.parent.mkdir(parents=True)
        server.write_text("", encoding="utf8")
        preset.write_text(
            json.dumps(
                {"args": ["--native", s["model"]], "cwd": str(self.data / "strata")}
            ),
            encoding="utf8",
        )
        cmd, env = runtime.backend(s)
        self.assertEqual(cmd[:3], [sys.executable, "-u", str(server)])
        self.assertEqual(
            env["LD_LIBRARY_PATH"].split(os.pathsep)[0], str(server.parent)
        )

    def test_validate_serving_messages(self):
        good = self.settings()
        for changes in (
            {},
            dict(start_timeout=30),
            dict(start_timeout="7200", kv="fp8", channel="Updated", context="4096"),
            dict(engine="vLLM", vllm_model=" owner/model ", vllm_parser="hermes"),
            dict(engine="vLLM", vllm_model="_o.w-n/m.o-d_el", vllm_parser="x" * 64),
            dict(engine_args=["--cache-ram", "2048"]),
        ):
            runtime.validate_serving(good | changes)
        runtime.validate_serving(self.custom("program"))
        vllm = dict(engine="vLLM", vllm_model="owner/model")
        for changes, message in (
            (dict(engine="bogus"), "listed inference engines"),
            (dict(engine=["Auto"]), "listed inference engines"),
            (dict(context=1023), "1,024 to 1,048,576"),
            (dict(context="many"), "1,024 to 1,048,576"),
            (dict(context=None), "1,024 to 1,048,576"),
            (dict(kv="q4"), "auto or fp8"),
            (dict(channel="Nightly"), "Tested or Updated"),
            (dict(start_timeout=29), "start timeout"),
            (dict(start_timeout=7201), "start timeout"),
            (dict(start_timeout="soon"), "start timeout"),
            (dict(engine_args=["--host", "0.0.0.0"]), "Extra engine options"),
            (dict(engine="vLLM", vllm_model="model"), "owner/model"),
            (dict(engine="vLLM", vllm_model="owner/model; id"), "owner/model"),
            (dict(engine="vLLM", vllm_model="--trust-remote-code/x"), "owner/model"),
            (dict(engine="vLLM", vllm_model="../model"), "owner/model"),
            (dict(engine="vLLM", vllm_model="owner/-model"), "owner/model"),
            (dict(engine="vLLM", vllm_model="owner/.."), "owner/model"),
            (vllm | dict(vllm_parser="--help"), "tool parser"),
            (vllm | dict(vllm_parser="x" * 65), "tool parser"),
            (vllm | dict(vllm_fraction="0.99"), "0.03 and 0.98"),
            (vllm | dict(vllm_fraction="most"), "0.03 and 0.98"),
            (vllm | dict(vllm_parser="hermes id"), "tool parser"),
            (vllm | dict(vllm_parser=["hermes"]), "tool parser"),
            (dict(engine="Custom"), "custom engine manifest"),
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError) as caught:
                runtime.validate_serving(good | changes)
            self.assertIn(message, str(caught.exception))
            self.assertTrue(str(caught.exception).endswith("."))

    def test_llama_serves_a_fixed_template_only_when_the_model_needs_one(self):
        from tests.test_chat_templates import QWEN_LIKE, TOLERANT, gguf

        self.system("macos")
        self.llama()
        strict = self.data / "strict.gguf"
        strict.write_bytes(gguf(QWEN_LIKE))
        tolerant = self.data / "tolerant.gguf"
        tolerant.write_bytes(gguf(TOLERANT))
        extra = ["--cache-ram", "2048"]
        cmd = runtime.backend(
            self.settings(engine="llama.cpp", model=str(strict), engine_args=extra)
        )[0]
        flag = cmd.index("--chat-template-file")
        served = Path(cmd[flag + 1])
        self.assertEqual(served.parent, runtime.DATA / "templates")
        self.assertNotIn("raise_exception", served.read_text(encoding="utf8"))
        self.assertEqual(cmd[-2:], extra)
        plain = runtime.backend(self.settings(engine="llama.cpp", model=str(tolerant)))[0]
        self.assertNotIn("--chat-template-file", plain)
        self.assertEqual(plain[-1], "--no-mmproj")

    def test_extra_engine_options_are_allow_listed(self):
        self.assertEqual(
            runtime.llama_args(["--cache-ram", "2048"]), ["--cache-ram", "2048"]
        )
        self.assertEqual([runtime.llama_args(v) for v in (None, [], ())], [[], [], []])
        for bad in (
            ["--host", "0.0.0.0"],
            ["--cache-ram"],
            ["--cache-ram", "1", "--cache-ram", "2"],
            "--cache-ram 2048",
            ["--cache-ram", "lots"],
            ["--cache-ram", 2048],
            ["--cache-ram", "2048\n"],
            ["--top-k", "٤٠"],
        ):
            with (
                self.subTest(bad=bad),
                self.assertRaisesRegex(ValueError, "Extra engine options"),
            ):
                runtime.llama_args(bad)
        self.system("macos")
        self.llama()
        plain = self.settings(engine="llama.cpp", model=self.model())
        extra = plain | dict(
            engine_args=["--cache-type-k", "q8_0", "--cache-ram", "2048"]
        )
        self.assertEqual(runtime.backend(extra)[0][-4:], extra["engine_args"])
        self.assertEqual(runtime.backend(plain)[0][-1], "--no-mmproj")
        self.assertEqual(
            runtime.serving_spec(extra)["engine_args"], extra["engine_args"]
        )
        old = {
            "spec": {
                k: plain[k]
                for k in (
                    "model",
                    "engine",
                    "context",
                    "draft",
                    "vision",
                    "kv",
                    "lora",
                    "channel",
                )
            }
        }
        self.assertTrue(runtime.same_server(plain, old))
        self.assertFalse(runtime.same_server(extra, old))
        self.assertTrue(
            runtime.same_server(
                extra | dict(engine="Custom"),
                {"spec": old["spec"] | dict(engine="Custom", manifest={})},
            )
        )


class EngineListTests(RuntimeCase):
    def offered(self, name, backend, installed="", supported=True):
        fake = computer(name, backend, installed, supported)
        with (
            patch.dict(sys.modules, {"setup_engines": fake}),
            patch.object(runtime, "system", return_value=name),
        ):
            return runtime.available_engines(), runtime.engine_support()

    def test_engine_list_matches_the_computer(self):
        basic = ["Auto", "llama.cpp", "Custom"]
        self.assertEqual(self.offered("macos", "metal")[0], basic)
        self.assertEqual(self.offered("linux", "vulkan")[0], basic)
        self.assertEqual(self.offered("windows", "cpu")[0], basic)
        self.assertEqual(self.offered("linux", "", supported=False)[0], basic)
        self.assertEqual(
            self.offered("linux", "cuda")[0],
            ["Auto", "Strata", "llama.cpp", "vLLM", "Custom"],
        )
        self.assertEqual(
            self.offered("windows", "vulkan", "cuda")[0],
            ["Auto", "Strata", "llama.cpp", "vLLM", "Custom"],
        )
        preset = self.data / "flash.json"
        preset.write_text("{}", encoding="utf8")
        os.environ["WORKBENCH_FLASH_CONFIG"] = str(preset)
        self.assertEqual(self.offered("linux", "cuda")[0], list(runtime.ENGINES))
        self.assertEqual(self.offered("macos", "metal")[0], basic)
        os.environ["WORKBENCH_FLASH_CONFIG"] = str(self.data / "absent.json")
        for name, backend, supported in (
            ("macos", "metal", True),
            ("linux", "vulkan", True),
            ("windows", "", False),
        ):
            support = self.offered(name, backend, supported=supported)[1]
            self.assertEqual(list(support), list(runtime.ENGINES))
            reasons = [v for v in support.values() if v]
            self.assertEqual(len(reasons), 3)
            self.assertTrue(all(v.endswith(".") for v in reasons))
        self.assertIn("Vulkan build", self.offered("linux", "vulkan")[1]["vLLM"])

    def test_bundled_presets_are_valid_and_filtered(self):
        shipped = json.loads((ROOT / "profiles.json").read_text(encoding="utf8"))
        self.assertEqual(len(shipped), 7)
        self.assertTrue(all(runtime.valid_preset(row) for row in shipped))
        with patch.object(
            runtime, "available_engines", return_value=["Auto", "llama.cpp", "Custom"]
        ):
            rows = runtime.preset_rows()
            self.assertEqual([row["engine"] for row in rows], ["llama.cpp"] * 3)
        good = {
            "name": "Small",
            "file": "small.gguf",
            "engine": "llama.cpp",
            "context": 4096,
        }
        bad = [
            good | {"file": "../small.gguf"},
            good | {"file": "a\\b.gguf"},
            good | {"engine": "bogus"},
            good | {"context": True},
            good | {"context": 512},
            good | {"kv": "q4"},
            good | {"draft": "yes"},
            good | {"name": ""},
            good | {"name": "n" * 81},
            good | {"prompt": "x"},
            "row",
        ]
        (self.data / "profiles.json").write_text(
            json.dumps(bad + [good]), encoding="utf8"
        )
        with (
            patch.object(runtime, "ROOT", self.data),
            patch.object(runtime, "available_engines", return_value=["llama.cpp"]),
        ):
            self.assertEqual(runtime.preset_rows(), [good])
            (self.data / "profiles.json").write_text("{broken", encoding="utf8")
            self.assertEqual(runtime.preset_rows(), [])


class IdentityTests(RuntimeCase):
    def test_identity_survives_a_clock_step(self):
        self.system("macos")
        proc = FakeProcess()
        rec = runtime.process_identity(proc)
        self.assertEqual(
            (rec["pid"], rec["born"], len(rec["cmd"])), (424242, 1000.0, 64)
        )
        self.assertNotIn("ticks", rec)
        self.assertEqual(runtime.same_process(FakeProcess(born=1000.01), rec), "same")
        self.assertEqual(runtime.same_process(FakeProcess(born=1005.0), rec), "same")
        self.assertEqual(
            runtime.same_process(FakeProcess(born=1005.0, cmd=("other",)), rec),
            "different",
        )
        old = {"pid": 424242, "born": 1000.0}
        self.assertEqual(runtime.same_process(FakeProcess(born=1005.0), old), "unknown")
        self.assertEqual(runtime.same_process(FakeProcess(), old), "same")
        zombie = FakeProcess()
        zombie.state = psutil.STATUS_ZOMBIE
        self.assertEqual(runtime.same_process(zombie, rec), "different")
        gone = FakeProcess()
        gone.running = False
        self.assertEqual(runtime.same_process(gone, rec), "different")
        self.assertEqual(
            runtime.same_process(FakeProcess(), {"pid": 424242}), "different"
        )
        self.assertEqual(
            runtime.same_process(FakeProcess(), {"born": "then"}), "different"
        )
        hidden = FakeProcess(born=1005.0)
        hidden.cmdline = Mock(side_effect=psutil.AccessDenied(1))
        self.assertEqual(runtime.same_process(hidden, rec), "unknown")
        self.assertNotIn("cmd", runtime.process_identity(hidden))

    def test_linux_identity_is_the_start_tick(self):
        self.system("linux")
        with patch.object(runtime, "ticks", return_value="8123456"):
            rec = runtime.process_identity(FakeProcess())
            self.assertEqual(rec["ticks"], "8123456")
            self.assertEqual(
                runtime.same_process(FakeProcess(born=1005.0, cmd=("other",)), rec),
                "same",
            )
        with patch.object(runtime, "ticks", return_value="9"):
            self.assertEqual(runtime.same_process(FakeProcess(), rec), "different")
        with patch.object(runtime, "ticks", return_value=""):
            self.assertEqual(runtime.same_process(FakeProcess(), rec), "same")
            self.assertNotIn("ticks", runtime.process_identity(FakeProcess()))

    def test_damaged_record_is_not_owned(self):
        for text in (
            "{broken",
            "[1, 2]",
            '{"pid": "x", "born": 1}',
            '{"pid": -4, "born": 1}',
            '{"born": 1}',
        ):
            (self.data / "running.json").write_text(text, encoding="utf8")
            self.assertEqual(runtime.owned(), (None, {}))
            self.assertTrue((self.data / "running.json").exists())
        self.save(pid=missing_pid(), born=1.0)
        self.assertEqual(runtime.owned(), (None, {}))
        mine = runtime.process_identity(psutil.Process())
        self.save(**mine)
        self.assertEqual(runtime.owned()[0].pid, os.getpid())

    def test_stop_keeps_an_unverified_record(self):
        path = self.data / "running.json"
        self.assertFalse(runtime.stop())
        with patch.object(
            runtime, "end_tree", side_effect=AssertionError("ended a process")
        ):
            self.save(pid=os.getpid(), born=1.0, spec={})
            with self.assertRaisesRegex(
                RuntimeError, f"process {os.getpid()} is its model server"
            ):
                runtime.stop()
            self.assertTrue(path.exists())
            self.save(pid=os.getpid(), born=1.0, cmd="0" * 64)
            events = []
            self.assertFalse(runtime.stop(progress=events.append))
            self.assertFalse(path.exists())
            self.assertEqual([e["phase"] for e in events], ["stopped"])
            self.save(pid=missing_pid(), born=1.0, run="a")
            self.assertFalse(runtime.stop(run="b"))
            self.assertTrue(path.exists())
            self.assertFalse(runtime.stop(run="a"))
            self.assertFalse(path.exists())
            for pid in (None, True, 0, -4, "7", 1.5):
                self.save(pid=pid, born=1.0)
                with self.subTest(pid=pid):
                    self.assertFalse(runtime.stop())
                    self.assertFalse(path.exists())

    def test_stop_does_not_wait_for_another_parent_to_reap(self):
        proc, child = FakeProcess(), FakeProcess(pid=424243)
        proc.tree, proc.exits, child.exits = [child], True, True
        unreaped = patch.object(
            psutil, "wait_procs", side_effect=lambda procs, timeout: ([], list(procs))
        )
        with unreaped as wait:
            began = time.monotonic()
            runtime.end_tree(proc)
            self.assertLess(time.monotonic() - began, 1)
        self.assertEqual(wait.call_count, 1)
        self.assertTrue(proc.ended and child.ended)
        self.assertFalse(proc.killed or child.killed)
        stubborn = FakeProcess()
        with (
            unreaped,
            patch.object(time, "monotonic", side_effect=itertools.count(0, 5)),
        ):
            runtime.end_tree(stubborn)
        self.assertTrue(stubborn.ended and stubborn.killed)
        self.assertEqual(runtime.settle([], 5), [])
        gone = FakeProcess()
        gone.status = Mock(side_effect=psutil.NoSuchProcess(1))
        hidden = FakeProcess()
        hidden.status = Mock(side_effect=psutil.AccessDenied(1))
        self.assertEqual(
            (runtime.ended(gone), runtime.ended(hidden), runtime.ended(FakeProcess())),
            (True, False, False),
        )

    def test_stop_asks_wsl_with_exec(self):
        self.system("windows")
        proc = FakeProcess()
        proc.tree = [FakeProcess(pid=424243)]
        self.save(pid=proc.pid, born=proc.born, wsl_run="abc123", engine="vLLM")
        events = []
        with (
            patch.object(psutil, "Process", return_value=proc),
            patch.object(psutil, "wait_procs", return_value=([], [])),
            patch.object(
                subprocess, "run", side_effect=subprocess.TimeoutExpired("wsl", 20)
            ) as run,
        ):
            self.assertTrue(
                runtime.stop(progress=events.append, cancel=threading.Event())
            )
        argv = run.call_args.args[0]
        self.assertEqual(argv, runtime.wsl_argv(["stop", "abc123"]))
        self.assertIn("--exec", argv)
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertTrue(proc.ended and proc.tree[0].ended)
        self.assertEqual([e["phase"] for e in events], ["stopping", "stopped"])
        self.assertFalse((self.data / "running.json").exists())

    def test_busy_lock_is_reported(self):
        held, done = threading.Event(), threading.Event()

        def hold():
            with runtime.LOCK:
                held.set()
                done.wait(5)

        runtime.LOCK.timeout = 0.05
        thread = threading.Thread(target=hold)
        thread.start()
        try:
            self.assertTrue(held.wait(5))
            with self.assertRaisesRegex(RuntimeError, "Another Agent Workbench window"):
                runtime.stop()
        finally:
            done.set()
            thread.join(5)
        self.assertFalse(runtime.stop())


class ProbeTests(RuntimeCase):
    def test_loopback_session_ignores_proxies(self):
        self.assertFalse(runtime.LOOPBACK.trust_env)
        os.environ.update(
            HTTP_PROXY="http://proxy.invalid:3128",
            http_proxy="http://proxy.invalid:3128",
        )
        merged = runtime.LOOPBACK.merge_environment_settings(
            "http://127.0.0.1:1/", {}, None, None, None
        )
        self.assertFalse(merged["proxies"])
        reply = Mock()
        reply.json.return_value = {"data": [{"id": "local-agent"}]}
        with (
            patch.object(runtime, "PORT", 45678),
            patch.object(runtime.LOOPBACK, "get", return_value=reply) as get,
        ):
            self.assertEqual(runtime.endpoint_model(timeout=1), "local-agent")
        self.assertEqual(get.call_args.args[0], "http://127.0.0.1:45678/v1/models")
        self.assertEqual(
            get.call_args.kwargs["headers"],
            {"Authorization": "Bearer " + runtime.key()},
        )
        self.assertEqual(
            (get.call_args.kwargs["timeout"], get.call_args.kwargs["allow_redirects"]),
            (1, False),
        )

    def test_loopback_env_adds_no_proxy_hosts(self):
        given = {"NO_PROXY": "corp.example, localhost", "PATH": "/bin"}
        out = runtime.loopback_env(given)
        self.assertEqual(given, {"NO_PROXY": "corp.example, localhost", "PATH": "/bin"})
        for name in ("NO_PROXY", "no_proxy"):
            self.assertEqual(
                out[name].split(","), ["corp.example", "localhost", "127.0.0.1", "::1"]
            )
        self.assertEqual(out["PATH"], "/bin")
        self.assertEqual(runtime.loopback_env(out), out)
        self.assertEqual(
            runtime.loopback_env({})["no_proxy"], "127.0.0.1,localhost,::1"
        )

    def test_key_is_not_sent_before_our_own_listener(self):
        port = 45679
        child = FakeProcess(pid=424243)
        proc = FakeProcess()
        proc.tree = [child]
        rec = dict(pid=proc.pid, engine="llama.cpp", port=port)
        with patch.object(psutil, "Process", return_value=proc):
            self.assertFalse(runtime.listening(rec))
            proc.sockets = [
                Connection(psutil.CONN_LISTEN, Address("127.0.0.1", port + 1))
            ]
            child.sockets = [Connection("ESTABLISHED", Address("127.0.0.1", port))]
            self.assertFalse(runtime.listening(rec))
            child.sockets.append(
                Connection(psutil.CONN_LISTEN, Address("127.0.0.1", port))
            )
            self.assertTrue(runtime.listening(rec))
            child.net_connections = Mock(side_effect=psutil.NoSuchProcess(1))
            self.assertFalse(runtime.listening(rec))
            child.net_connections = Mock(side_effect=psutil.AccessDenied(1))
            self.assertTrue(runtime.listening(rec))
        with patch.object(psutil, "Process", side_effect=psutil.NoSuchProcess(1)):
            self.assertFalse(runtime.listening(rec))
            self.assertTrue(runtime.listening(rec | dict(engine="Custom")))
            self.assertTrue(runtime.listening(dict(pid=1)))
            self.assertFalse(
                runtime.listening(rec | dict(engine="vLLM", wsl_run="abc"))
            )
            with patch.object(runtime, "system", return_value="windows"):
                self.assertTrue(
                    runtime.listening(rec | dict(engine="vLLM", wsl_run="abc"))
                )
        waiting = Mock()
        waiting.poll.return_value = None
        rec = dict(
            pid=1,
            run="r1",
            engine="llama.cpp",
            port=port,
            budget=0.1,
            started=int(time.time()),
        )
        self.save(**rec)
        with (
            patch.object(runtime, "listening", return_value=False),
            patch.object(
                runtime, "endpoint_model", side_effect=AssertionError("key sent")
            ) as probe,
            patch.object(runtime, "stop") as stop,
            self.assertRaisesRegex(runtime.StartFailed, "not ready after 0.1 seconds"),
        ):
            runtime.await_ready(rec, waiting)
        probe.assert_not_called()
        stop.assert_called_once_with(run="r1")

    def test_waiter_never_stops_the_model(self):
        rec = runtime.process_identity(psutil.Process()) | dict(
            run="r1", engine="Custom", budget=600, started=int(time.time())
        )
        self.save(**rec)
        token = threading.Event()
        token.set()
        failing = patch.object(
            runtime, "endpoint_model", side_effect=requests.ConnectionError()
        )
        with patch.object(runtime, "stop") as stop, failing:
            with self.assertRaisesRegex(runtime.Cancelled, "start was cancelled"):
                runtime.await_ready(rec, cancel=token)
            self.save(**(rec | dict(run="r2")))
            with self.assertRaisesRegex(
                runtime.Cancelled, "stopped before it finished"
            ):
                runtime.await_ready(rec)
            self.save(**rec)
            with patch.object(runtime, "alive", return_value=False):
                with self.assertRaisesRegex(
                    runtime.StartFailed, "exited while loading \\(no output\\)"
                ):
                    runtime.await_ready(rec)
            late = rec | dict(started=int(time.time()) - 900)
            self.save(**late)
            with patch.object(time, "monotonic", side_effect=itertools.count(0, 3)):
                with self.assertRaisesRegex(RuntimeError, "running but not answering"):
                    runtime.await_ready(late)
        stop.assert_not_called()

    def test_only_the_start_that_spawned_cleans_up(self):
        s = self.custom("program")
        spawned = patch.object(
            subprocess, "Popen", return_value=Mock(pid=missing_pid())
        )
        with (
            patch.object(runtime, "PORT", free_port()),
            patch.object(runtime, "stop") as stop,
            spawned,
        ):
            for error, cleaned in (
                (runtime.StartFailed("The model server exited while loading."), 0),
                (runtime.Cancelled("The model start was cancelled."), 0),
                (KeyboardInterrupt(), 1),
                (OSError("lost"), 1),
            ):
                stop.reset_mock()
                with (
                    self.subTest(error=error),
                    patch.object(runtime, "await_ready", side_effect=error),
                    self.assertRaises(type(error)),
                ):
                    runtime.start(s)
                self.assertEqual(stop.call_count, cleaned)
                if cleaned:
                    stop.assert_called_once_with(run=runtime.record()["run"])
            waiting = runtime.process_identity(psutil.Process()) | dict(
                run="r1", engine="Custom", spec=runtime.serving_spec(s)
            )
            self.save(**waiting)
            stop.reset_mock()
            with (
                patch.object(runtime, "await_ready", side_effect=KeyboardInterrupt),
                self.assertRaises(KeyboardInterrupt),
            ):
                runtime.start(s)
            stop.assert_not_called()
            stop.side_effect = RuntimeError("busy")
            (self.data / "running.json").unlink()
            with (
                patch.object(runtime, "await_ready", side_effect=KeyboardInterrupt),
                self.assertRaises(KeyboardInterrupt),
            ):
                runtime.start(s)

    def test_log_tail_hides_the_key(self):
        self.assertEqual(runtime.log_tail(), [])
        secret = runtime.key()
        lines = [f"line {i}" for i in range(40)] + [
            "",
            f"auth Bearer {secret} ok",
            "  last  ",
        ]
        (self.data / "serve.log").write_text("\n".join(lines), encoding="utf8")
        tail = runtime.log_tail()
        self.assertEqual(len(tail), 20)
        self.assertEqual(tail[-2:], ["auth Bearer [key] ok", "last"])
        self.assertEqual(runtime.log_tail(1), ["last"])
        (self.data / "serve.log").write_text(
            secret[4:] + "x" * 9000 + "\nend\n", encoding="utf8"
        )
        self.assertEqual(runtime.log_tail(), ["end"])
        (self.data / "serve.log").write_bytes(
            "The distribution was not found.\r\n".encode("utf-16-le")
        )
        self.assertEqual(runtime.log_tail(), ["The distribution was not found."])

    def test_start_budget_and_helpers(self):
        self.assertEqual(runtime.start_budget(self.settings()), 600)
        self.assertEqual(runtime.start_budget(self.settings(engine="vLLM")), 1800)
        self.assertEqual(
            runtime.start_budget(self.settings(engine="vLLM", start_timeout=45)), 45
        )
        token = threading.Event()
        self.assertFalse(runtime.cancelled(None) or runtime.cancelled(token))
        token.set()
        self.assertTrue(runtime.cancelled(token))
        runtime.pause(token, 5)
        with patch.object(time, "sleep") as sleep:
            runtime.pause(None, 0.5)
        sleep.assert_called_once_with(0.5)
        seen = []
        runtime.tell(seen.append, job="x", phase="y")
        runtime.tell(Mock(side_effect=OSError("closed pipe")), job="x")
        runtime.tell(None, job="x")
        self.assertEqual(seen, [dict(job="x", phase="y")])
        self.assertEqual(runtime.StartFailed("m", ("a", "b")).tail, ["a", "b"])
        self.assertEqual(
            runtime.system(),
            {"nt": "windows"}.get(
                os.name, "macos" if sys.platform == "darwin" else "linux"
            ),
        )


class LifecycleTests(RuntimeCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(runtime, "PORT", free_port()))
        self.enterContext(warnings.catch_warnings())
        warnings.simplefilter("ignore", ResourceWarning)
        self.events = []
        self.addCleanup(self.tidy)

    def tidy(self):
        try:
            runtime.stop()
        except Exception:
            pass
        gc.collect()

    def refused(self):
        return patch.object(
            runtime, "endpoint_model", side_effect=requests.ConnectionError()
        )

    def pid(self):
        return next(e["pid"] for e in self.events if e["phase"] == "spawn")

    def test_start_serves_and_stop_ends_it(self):
        s = self.custom(sys.executable, "-c", FAKE, "{port}", "{keyfile}", "0")
        probe = runtime.endpoint_model
        with patch.object(runtime, "endpoint_model", lambda timeout=4: probe(0.5)):
            rec = runtime.start(s, progress=self.events.append)
            self.assertEqual(
                (rec["engine"], rec["port"], rec["budget"]),
                ("Custom", runtime.PORT, 600),
            )
            self.assertEqual((len(rec["run"]), len(rec["cmd"])), (16, 64))
            self.assertEqual(
                rec, json.loads((self.data / "running.json").read_text(encoding="utf8"))
            )
            phases = [e["phase"] for e in self.events]
            self.assertEqual(phases[:2], ["prepare", "spawn"])
            self.assertEqual(phases[-1], "ready")
            self.assertEqual(runtime.owned()[0].pid, rec["pid"])
            served = rec | dict(engine="llama.cpp")
            self.assertTrue(runtime.listening(served))
            self.assertFalse(runtime.listening(served | dict(port=free_port())))
            if os.name != "nt":
                self.assertEqual(
                    (self.data / "serve.log").stat().st_mode & 0o777, 0o600
                )
                self.assertEqual(os.getsid(rec["pid"]), rec["pid"])
            with patch.object(
                subprocess, "Popen", side_effect=AssertionError("spawned")
            ):
                self.assertEqual(runtime.start(s, progress=self.events.append), rec)
            self.assertEqual(
                [e["phase"] for e in self.events[-2:]], ["waiting", "ready"]
            )
            with self.assertRaisesRegex(RuntimeError, "Stop the active model"):
                runtime.start(s | dict(context=4096))
        self.assertTrue(runtime.stop(progress=self.events.append))
        self.assertFalse(psutil.pid_exists(rec["pid"]))
        self.assertFalse((self.data / "running.json").exists())
        self.assertEqual(
            [e["phase"] for e in self.events[-2:]], ["stopping", "stopped"]
        )
        self.assertTrue(all({"job", "phase", "message"} <= set(e) for e in self.events))
        self.assertNotIn(runtime.key(), json.dumps(self.events))

    def test_cancel_token_ends_a_loading_model(self):
        token = threading.Event()
        timer = threading.Timer(0.15, token.set)
        timer.start()
        self.addCleanup(timer.cancel)
        with (
            self.refused(),
            self.assertRaisesRegex(runtime.Cancelled, "start was cancelled"),
        ):
            runtime.start(
                self.custom(sys.executable, "-c", SLEEP), self.events.append, token
            )
        self.assertFalse(psutil.pid_exists(self.pid()))
        self.assertFalse((self.data / "running.json").exists())
        self.assertIn("loading", [e["phase"] for e in self.events])

    def test_stop_from_another_thread_ends_a_start(self):
        s, caught = self.custom(sys.executable, "-c", SLEEP), []

        def work():
            try:
                runtime.start(s, progress=self.events.append)
            except BaseException as exc:
                caught.append(exc)

        with self.refused():
            thread = threading.Thread(target=work)
            thread.start()
            limit = time.monotonic() + 5
            while (
                not (self.data / "running.json").exists() and time.monotonic() < limit
            ):
                time.sleep(0.01)
            began = time.monotonic()
            self.assertTrue(runtime.stop())
            took = time.monotonic() - began
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(caught[0], runtime.Cancelled)
        self.assertIn("stopped before it finished loading", str(caught[0]))
        self.assertLess(took, 1)
        self.assertFalse(psutil.pid_exists(self.pid()))

    def test_exit_while_loading_reports_the_log(self):
        s = self.custom(sys.executable, "-c", "print('boom'); raise SystemExit(3)")
        with self.refused(), self.assertRaises(runtime.StartFailed) as caught:
            runtime.start(s, progress=self.events.append)
        self.assertIn("exited while loading (boom)", str(caught.exception))
        self.assertIn(str(self.data / "serve.log"), str(caught.exception))
        self.assertEqual(caught.exception.tail, ["boom"])
        self.assertFalse((self.data / "running.json").exists())

    def test_timeout_stops_the_engine(self):
        s = self.custom(sys.executable, "-c", SLEEP)
        with self.refused(), patch.object(runtime, "start_budget", return_value=0.3):
            with self.assertRaisesRegex(
                runtime.StartFailed, "not ready after 0.3 seconds"
            ) as caught:
                runtime.start(s, progress=self.events.append)
        self.assertEqual(caught.exception.tail, [])
        self.assertFalse(psutil.pid_exists(self.pid()))
        self.assertFalse((self.data / "running.json").exists())

    def test_interrupted_start_stops_its_server(self):
        s = self.custom(sys.executable, "-c", SLEEP)
        with (
            self.refused(),
            patch.object(runtime, "pause", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            runtime.start(s, progress=self.events.append)
        self.assertFalse(psutil.pid_exists(self.pid()))
        self.assertFalse((self.data / "running.json").exists())

    def test_spawn_failure_is_one_sentence(self):
        program = str(self.data / "absent-engine")
        with self.assertRaisesRegex(
            RuntimeError, "could not be started .*custom engine manifest"
        ):
            runtime.start(self.custom(program), progress=self.events.append)
        self.assertFalse((self.data / "running.json").exists())
        error = FileNotFoundError(2, "No such file or directory")
        self.assertIn(
            "reinstall it",
            runtime.spawn_error(self.settings(engine="llama.cpp"), ["x"], error),
        )
        with patch.object(runtime, "system", return_value="windows"):
            self.assertIn(
                "WSL did not start",
                runtime.spawn_error(self.settings(engine="vLLM"), ["wsl"], error),
            )

    def test_refuses_a_port_that_is_taken(self):
        with socket.socket() as other:
            other.bind(("127.0.0.1", runtime.PORT))
            other.listen()
            with patch.object(
                runtime, "backend", side_effect=AssertionError("prepared")
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "already has a listener; stop that program"
                ):
                    runtime.start(self.custom("program"))
        self.assertFalse((self.data / "running.json").exists())


class ToolTests(RuntimeCase):
    def test_desktop_tool_only_on_windows(self):
        self.plugins()
        s = self.settings(search=False, desktop=True)
        with patch.object(shutil, "which", return_value=None):
            self.system("macos")
            self.assertEqual(runtime.mcp_servers(s), {})
            with self.assertRaisesRegex(ValueError, "Install uv in Advanced → Setup"):
                runtime.mcp_servers(s | dict(blender=True))
            with patch.object(runtime, "system", return_value="windows"):
                with self.assertRaisesRegex(
                    ValueError, "Install uv in Advanced → Setup"
                ):
                    runtime.mcp_servers(s)
        with (
            patch.object(shutil, "which", return_value="uvx"),
            patch.object(runtime, "system", return_value="windows"),
        ):
            self.assertEqual(
                runtime.mcp_servers(s)["desktop"]["args"][0], "windows-mcp@0.9.0"
            )

    def test_extra_mcp_errors_name_the_setting(self):
        self.plugins()
        path = self.data / "extra.json"
        s = self.settings(search=True, extra_mcp=str(path))
        for text in (
            None,
            "{broken",
            "[]",
            '{"mcpServers": []}',
            '{"mcpServers": {"docs": "npx"}}',
        ):
            if text is not None:
                path.write_text(text, encoding="utf8")
            with (
                self.subTest(text=text),
                self.assertRaisesRegex(
                    ValueError, "Extra MCP manifest set in Advanced → Agent settings"
                ),
            ):
                runtime.mcp_servers(s)
        path.write_text(
            json.dumps({"mcpServers": {"search": {"command": "x"}}}), encoding="utf8"
        )
        with self.assertRaisesRegex(
            ValueError, "defines search, which Agent Workbench already provides"
        ):
            runtime.mcp_servers(s)
        path.write_text(
            json.dumps({"mcpServers": {"notes": {"command": "x"}}}), encoding="utf8"
        )
        docs = {"type": "stdio", "command": "docs"}
        servers = runtime.mcp_servers(s | dict(_mcp={"docs": docs}))
        self.assertEqual(list(servers), ["search", "notes", "docs"])
        self.assertEqual(servers["docs"], docs)
        for taken in ("search", "notes"):
            with self.assertRaisesRegex(ValueError, "overlapping MCP plugin: " + taken):
                runtime.mcp_servers(s | dict(_mcp={taken: docs}))

    def test_missing_omp_names_the_setup_screen(self):
        with patch.object(shutil, "which", return_value=None):
            with self.assertRaisesRegex(
                ValueError,
                "OMP is not installed; open Advanced → Setup → OMP → Install.",
            ):
                runtime.omp_command(self.settings())

    def test_omp_reads_project_tools_only_with_consent(self):
        self.plugins()
        home = self.data / "home"
        home.mkdir()
        docs = {"type": "stdio", "command": "docs"}
        asked = []

        def entries(s, taken):
            asked.append(set(taken))
            return {"docs": docs}

        fake = types.SimpleNamespace(
            omp_entries=entries, project_config_allowed=lambda workspace: allowed
        )
        s = self.settings(search=True)
        with (
            patch.dict(sys.modules, {"project_setup": fake}),
            patch.object(Path, "home", return_value=home),
            patch.object(shutil, "which", return_value="omp"),
        ):
            for allowed in (False, True):
                cmd = runtime.omp_command(s)
                folder = Path(cmd[cmd.index("--config") + 1]).parent
                overlay = json.loads(
                    (folder / "workbench-overlay.json").read_text(encoding="utf8")
                )
                self.assertEqual(
                    overlay["mcp"],
                    {"startupTimeoutMs": 15000, "enableProjectConfig": allowed},
                )
            sent = json.loads((folder / "mcp.json").read_text(encoding="utf8"))
            self.assertEqual(list(sent["mcpServers"]), ["search", "docs"])
            self.assertEqual(asked, [{"search"}, {"search"}])
            given = runtime.omp_command(s | dict(_mcp={"notes": docs}))
            folder = Path(given[given.index("--config") + 1]).parent
            sent = json.loads((folder / "mcp.json").read_text(encoding="utf8"))
            self.assertEqual(list(sent["mcpServers"]), ["search", "notes"])
            self.assertEqual(len(asked), 2)
            self.assertNotIn(runtime.key(), " ".join(cmd))

    def omp(self, which="omp"):
        self.plugins()
        home = self.data / "home"
        home.mkdir()
        fake = types.SimpleNamespace(
            omp_entries=lambda s, taken: {}, project_config_allowed=lambda folder: False
        )
        self.enterContext(patch.dict(sys.modules, {"project_setup": fake}))
        self.enterContext(patch.object(Path, "home", return_value=home))
        found = which if callable(which) else lambda name: which
        self.enterContext(patch.object(shutil, "which", side_effect=found))
        return home

    def test_omp_runs_the_program_behind_a_windows_shim(self):
        import host

        folder = self.data / "npm"
        script = folder / "node_modules" / "omp" / "cli.js"
        script.parent.mkdir(parents=True)
        script.write_text("", encoding="utf8")
        shim = folder / "omp.cmd"
        shim.write_text(
            '@ECHO off\r\n@node "%~dp0\\node_modules\\omp\\cli.js" %*\r\n',
            encoding="utf8",
        )
        node = str(self.data / "node.exe")
        self.omp({"omp": str(shim), "node": node}.get)
        s = self.settings()
        with patch.object(host, "WINDOWS", True):
            cmd = runtime.omp_command(s)
            self.assertEqual(cmd[:3], [node, str(script), "--profile"])
            shim.write_text("@call other.cmd %*\r\n", encoding="utf8")
            self.assertEqual(runtime.omp_command(s)[:2], [str(shim), "--profile"])
        with patch.object(host, "WINDOWS", False):
            self.assertEqual(runtime.omp_command(s)[:2], [str(shim), "--profile"])

    def test_omp_profile_follows_the_connection_not_the_port(self):
        home = self.omp()
        s = self.settings(search=False)
        old, new = "http://127.0.0.1:5001/v1", "http://127.0.0.1:5002/v1"

        def profile(**options):
            cmd = runtime.omp_command(
                s,
                profile_name="workbench-slot",
                model_id="remote",
                env_key="WORKBENCH_SESSION_KEY",
                **options,
            )
            return cmd[cmd.index("--profile") + 1]

        first = profile(base=old, anchor="machine-1")
        self.assertEqual(profile(base=new, anchor="machine-1"), first)
        models = json.loads(
            (home / ".omp/profiles" / first / "agent/models.yml").read_text(
                encoding="utf8"
            )
        )
        self.assertEqual(models["providers"]["workbench"]["baseUrl"], new)
        self.assertNotEqual(profile(base=old, anchor="machine-2"), first)
        self.assertNotEqual(profile(base=old), profile(base=new))
        self.assertEqual(profile(base=old, anchor=old), profile(base=old))

    def test_damaged_plugin_registry_is_one_sentence(self):
        s = self.settings(search=False, plugins="docs")
        read = Mock()
        fake = types.SimpleNamespace(
            plugin_ids=lambda s: [v for v in s["plugins"].split(",") if v],
            registry=read,
        )
        self.enterContext(patch.dict(sys.modules, {"project_profiles": fake}))
        for damage in (
            dict(side_effect=json.JSONDecodeError("Expecting value", "{broken", 1)),
            dict(side_effect=PermissionError("denied")),
            dict(return_value=["docs"]),
        ):
            read.configure_mock(side_effect=None, return_value=None)
            read.configure_mock(**damage)
            with self.assertRaises(ValueError) as caught:
                runtime.mcp_servers(s)
            self.assertIs(type(caught.exception), ValueError)
            self.assertEqual(
                str(caught.exception),
                f"The plugin registry at {self.data / 'plugins.json'} could not be "
                "read; move that file aside, then register your MCP files again in "
                "Plugins.",
            )
        read.reset_mock()
        self.assertEqual(runtime.mcp_servers(s | dict(plugins="")), {})
        read.assert_not_called()
        docs = {"type": "stdio", "command": "docs"}
        read.configure_mock(side_effect=None, return_value={"docs": docs})
        self.assertEqual(runtime.mcp_servers(s), {"docs": docs})
        with self.assertRaisesRegex(ValueError, "overlapping MCP plugin: gone"):
            runtime.mcp_servers(s | dict(plugins="gone"))

    def test_blender_starts_apart_from_the_terminal(self):
        for name, detached in (("macos", True), ("linux", True), ("windows", False)):
            with (
                self.subTest(system=name),
                patch.object(runtime, "system", return_value=name),
                patch("host.blender_executable", return_value="/opt/blender"),
                patch.object(runtime, "ensure_blender_addon", return_value=False) as addon,
                patch.object(psutil, "process_iter", return_value=[]),
                patch.object(
                    socket, "create_connection", side_effect=[OSError(), MagicMock()]
                ),
                patch.object(subprocess, "Popen") as spawn,
            ):
                runtime.ensure_blender()
            addon.assert_called_once_with("/opt/blender", enable=True)
            options = spawn.call_args.kwargs
            self.assertEqual(spawn.call_args.args[0][0], "/opt/blender")
            self.assertIs(options["stdin"], subprocess.DEVNULL)
            self.assertEqual(options.get("start_new_session", False), detached)

    def test_ensure_blender_addon_installs_and_enables_from_pinned_package(self):
        addons_dir = self.data / "blender-addons"
        bundled = self.data / "bundled_addon.py"
        bundled.write_text("# ADDON_PROTOCOL_VERSION = 15\nprint('ok')\n", encoding="utf8")
        with (
            patch.object(runtime, "blender_addons_dir", return_value=addons_dir),
            patch.object(runtime, "blender_addon_source", return_value=bundled),
            patch.object(subprocess, "run", return_value=MagicMock(returncode=0)) as run,
        ):
            self.assertTrue(runtime.ensure_blender_addon("/opt/blender", enable=True))
            self.assertTrue((addons_dir / "blender_mcp.py").is_file())
            self.assertEqual(
                (addons_dir / "blender_mcp.py").read_text(encoding="utf8"),
                bundled.read_text(encoding="utf8"),
            )
            self.assertEqual(run.call_count, 1)
            # Second call still verifies/re-enables in case user disabled it in Blender
            self.assertFalse(runtime.ensure_blender_addon("/opt/blender", enable=True))
            self.assertEqual(run.call_count, 2)
            self.assertIn("addon_utils.check('blender_mcp')", run.call_args.args[0][-1])
            # Never downgrades a newer protocol version already installed
            (addons_dir / "blender_mcp.py").write_text(
                "# ADDON_PROTOCOL_VERSION = 99\nprint('newer')\n", encoding="utf8"
            )
            self.assertFalse(runtime.ensure_blender_addon("/opt/blender", enable=True))
            self.assertIn("99", (addons_dir / "blender_mcp.py").read_text(encoding="utf8"))

    def test_ensure_blender_when_open_installs_addon_and_tells_user_to_restart(self):
        open_proc = MagicMock(info={"name": "blender"})
        with (
            patch("host.blender_executable", return_value="/opt/blender"),
            patch.object(psutil, "process_iter", return_value=[open_proc]),
            patch.object(socket, "create_connection", side_effect=OSError()),
            patch.object(runtime, "ensure_blender_addon", return_value=True) as addon,
        ):
            with self.assertRaisesRegex(ValueError, "Installed the Blender MCP add-on"):
                runtime.ensure_blender()
            addon.assert_called_once_with("/opt/blender", enable=False)

    def test_extend_path_appends_existing_folders_once(self):
        home = self.data / "home"
        (home / ".local/bin").mkdir(parents=True)
        first = os.pathsep.join(["/usr/bin", ""])
        env = {"PATH": first}
        runtime.extend_path(env, home)
        self.assertTrue(env["PATH"].startswith(first))
        parts = env["PATH"].split(os.pathsep)
        self.assertEqual(parts.count(str(home / ".local/bin")), 1)
        self.assertNotIn(str(home / ".bun/bin"), parts)
        self.assertIn(
            str(home / ".local/bin"), env["WORKBENCH_PATH_ADDED"].split(os.pathsep)
        )
        again = dict(env)
        runtime.extend_path(env, home)
        self.assertEqual(env, again)
        (home / ".bun/bin").mkdir(parents=True)
        runtime.extend_path(env, home)
        self.assertEqual(env["PATH"].split(os.pathsep)[-1], str(home / ".bun/bin"))
        self.assertEqual(
            env["WORKBENCH_PATH_ADDED"].split(os.pathsep)[-1], str(home / ".bun/bin")
        )
        empty = {}
        runtime.extend_path(empty, self.data / "nobody")
        self.assertNotIn("", empty.get("PATH", "x").split(os.pathsep))


class WorkerTests(RuntimeCase):
    def setUp(self):
        super().setUp()
        self.home = self.data / "home"
        self.home.mkdir()
        self.enterContext(patch.object(Path, "home", return_value=self.home))

    def test_worker_validates_arguments(self):
        for argv in ([], ["serve"]):
            with self.assertRaisesRegex(
                SystemExit, "Usage: vllm_worker.py serve TAG MODEL"
            ):
                vllm_worker.main(argv)
        with patch.object(sys, "platform", "darwin"):
            with self.assertRaisesRegex(SystemExit, "vLLM runs on Linux or inside WSL"):
                vllm_worker.main(["stop", "abc"])
        with patch.object(sys, "platform", "linux"):
            with self.assertRaisesRegex(ValueError, "Invalid run ID"):
                vllm_worker.main(["stop", "a;b"])
            with self.assertRaisesRegex(SystemExit, "Usage"):
                vllm_worker.main(["restart", "abc"])
            os.environ.pop("WORKBENCH_VLLM", None)
            with (
                patch.object(vllm_worker, "executable", return_value=None),
                patch.object(subprocess, "Popen") as spawn,
            ):
                with self.assertRaisesRegex(
                    SystemExit,
                    "vLLM is not installed; open Advanced → Setup → vLLM → Install.",
                ):
                    vllm_worker.main(["serve", "abc", "owner/model"])
            spawn.assert_not_called()
        self.assertFalse(list(self.home.rglob("*.json")))

    def test_worker_stop_removes_the_record(self):
        record = self.home / ".cache/agent-workbench/abc.json"
        record.parent.mkdir(parents=True)
        for text, born, ended in (
            ('[4194301, "77"]', "77", [4194301]),
            ('[4194301, "77"]', "78", []),
            ("{broken", "77", []),
        ):
            record.write_text(text, encoding="utf8")
            with (
                patch.object(sys, "platform", "linux"),
                patch.object(vllm_worker, "started", return_value=born),
                patch.object(
                    vllm_worker.os,
                    "killpg",
                    create=True,
                    side_effect=ProcessLookupError,
                ) as kill,
            ):
                vllm_worker.main(["stop", "abc"])
            self.assertEqual([call.args[0] for call in kill.call_args_list], ended)
            self.assertFalse(record.exists())

    def test_worker_serve_cleans_up_when_terminated(self):
        record = self.home / ".cache/agent-workbench/abc.json"
        child, seen = Mock(pid=4321), {}

        def spawn(argv, **options):
            seen.update(argv=argv, options=options, handlers=install.call_count)
            return child

        def serving():
            seen["record"] = json.loads(record.read_text(encoding="utf8"))
            install.call_args.args[1](signal.SIGTERM, None)

        child.wait.side_effect = serving
        child.poll.return_value = None
        with (
            patch.object(sys, "platform", "linux"),
            patch.object(vllm_worker, "executable", return_value="/opt/vllm"),
            patch.object(vllm_worker, "started", return_value="77"),
            patch.object(vllm_worker.signal, "signal") as install,
            patch.object(subprocess, "Popen", side_effect=spawn),
            patch.object(vllm_worker.os, "killpg", create=True) as kill,
        ):
            with self.assertRaises(SystemExit) as caught:
                vllm_worker.main(["serve", "abc", "owner/model", "--port", "1"])
            self.assertEqual(caught.exception.code, 143)
            self.assertEqual(install.call_args.args[0], signal.SIGTERM)
            self.assertEqual(seen["handlers"], 1)
            self.assertEqual(
                seen["argv"], ["/opt/vllm", "serve", "owner/model", "--port", "1"]
            )
            self.assertTrue(seen["options"]["start_new_session"])
            self.assertEqual(seen["record"], [4321, "77"])
            kill.assert_called_once_with(4321, signal.SIGTERM)
            self.assertFalse(record.exists())
            kill.reset_mock()
            child.wait.side_effect, child.wait.return_value = None, 3
            child.poll.return_value = 3
            with self.assertRaises(SystemExit) as caught:
                vllm_worker.main(["serve", "abc", "owner/model"])
            self.assertEqual(caught.exception.code, 3)
            kill.assert_not_called()
            self.assertFalse(record.exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "the worker reads /proc")
    def test_worker_stop_for_a_dead_process(self):
        record = self.home / ".cache/agent-workbench/abc.json"
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps([missing_pid(), "77"]), encoding="utf8")
        vllm_worker.main(["stop", "abc"])
        self.assertFalse(record.exists())

    def test_worker_prefers_the_managed_environment(self):
        managed = self.home / ".local/share/agent-workbench/vllm/bin/vllm"
        linked = self.home / ".local/bin/vllm"
        fixed = "/opt/agent-workbench/vllm/bin/vllm"
        os.environ.pop("WORKBENCH_VLLM", None)
        with patch.object(shutil, "which", return_value=None):
            self.assertIn(vllm_worker.executable(), (None, fixed))
            linked.parent.mkdir(parents=True)
            linked.write_text("", encoding="utf8")
            self.assertIn(vllm_worker.executable(), (str(linked), fixed))
        with patch.object(shutil, "which", return_value="/usr/bin/vllm"):
            self.assertIn(vllm_worker.executable(), ("/usr/bin/vllm", fixed))
            managed.parent.mkdir(parents=True)
            managed.write_text("", encoding="utf8")
            self.assertIn(vllm_worker.executable(), (str(managed), fixed))
            os.environ["WORKBENCH_VLLM"] = "/chosen/vllm"
            self.assertEqual(vllm_worker.executable(), "/chosen/vllm")

    def test_capacity_records_a_failed_run(self):
        fake = types.SimpleNamespace(hardware=lambda: ("gpu", 24.0, 20.0, 0))
        with patch.dict(sys.modules, {"catalog": fake}):
            sys.modules.pop("capacity", None)
            import capacity

            self.assertIs(capacity.LOOPBACK, runtime.LOOPBACK)
            with patch.object(
                capacity.LOOPBACK, "post", side_effect=requests.Timeout()
            ) as post:
                result = capacity.measure(10, timeout=7)
            self.assertEqual(
                (result["passed"], result["error"], result["lines"]),
                (False, "Timeout", 10),
            )
            self.assertEqual(result["peak_device_gib"], 4.0)
            self.assertEqual(post.call_args.kwargs["timeout"], 7)
            reply = Mock()
            reply.json.return_value = {
                "choices": [{"message": {"content": " sky-07382 "}}],
                "usage": {"total_tokens": 9},
            }
            with patch.object(capacity.LOOPBACK, "post", return_value=reply) as post:
                self.assertTrue(capacity.measure(10)["passed"])
            self.assertEqual(post.call_args.kwargs["timeout"], 900)
            self.assertNotIn(runtime.key(), json.dumps(result))

    def test_vllm_setup_script_is_pinned(self):
        text = (ROOT / "setup-vllm.sh").read_text(encoding="utf8")
        for expected in (
            'vllm_version="0.34.0"',
            'uv_version="0.13.9"',
            "uv==$uv_version",
            "ln -sfn",
            "--update",
            "python3-venv",
        ):
            self.assertIn(expected, text)
        self.assertNotIn("pip install -U uv", text)
        self.assertNotIn("\r", text)
        sh = shutil.which("sh")
        if sh:
            done = subprocess.run(
                [sh, "-n", str(ROOT / "setup-vllm.sh")],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":
    unittest.main()
