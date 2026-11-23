import atexit
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from filelock import FileLock

if "runtime" not in sys.modules:
    _data = tempfile.mkdtemp(prefix="workbench-tests-")
    atexit.register(shutil.rmtree, _data, ignore_errors=True)
    os.environ["WORKBENCH_DATA"] = _data

import boxes
import catalog
import connections
import runtime
from shortcuts import ident


class WorkbenchTest(unittest.TestCase):
    def test_split_models_require_all_shards(self):
        parts = [
            ("folder/model-00001-of-00002.gguf", 9),
            ("folder/model-00002-of-00002.gguf", 10),
        ]
        self.assertEqual(list(catalog.families(parts[:1])), [])
        self.assertEqual(list(catalog.families(parts))[0][2], 19)
        self.assertFalse(
            list(catalog.families([("mmproj-f16.gguf", 11), ("pi-lora.gguf", 3)]))
        )

    def test_provider_credentials_have_fixed_origins(self):
        for name, base in [
            ("OpenAI", "https://api.openai.com/v1"),
            ("OpenRouter", "https://openrouter.ai/api/v1"),
        ]:
            self.assertEqual(
                connections.endpoint(
                    {"provider": name, "endpoint": "https://example.org/v1"}
                )[0],
                base,
            )
        with self.assertRaises(ValueError):
            connections.endpoint(
                {
                    "provider": "Attached endpoint",
                    "endpoint": "https://key@example.org/v1",
                }
            )
        with self.assertRaises(ValueError):
            connections.endpoint(
                {"provider": "Attached endpoint", "endpoint": "http://example.org/v1"}
            )

    def test_cloud_key_only_in_child_environment(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(connections, "DATA", Path(folder)),
            patch.dict(os.environ, OPENAI_API_KEY="synthetic-only"),
            patch.object(connections, "command", return_value=["pi"]),
            patch("visual_routes.public_catalog", return_value={}),
        ):
            s = runtime.settings() | dict(
                provider="OpenAI", remote_model="mock", workspace=folder
            )
            cmd, env, _ = connections.prepare(s, "Pi")
            config = next(Path(folder).rglob("models.json")).read_text()
            self.assertEqual(env["WORKBENCH_SESSION_KEY"], "synthetic-only")
            self.assertNotIn("synthetic-only", config + str(cmd))
            self.assertIn("WORKBENCH_SESSION_KEY", config)

    def test_vllm_and_custom_commands_preserve_arguments(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runtime, "DATA", Path(tmp)),
        ):
            s = runtime.settings() | dict(
                engine="vLLM", vllm_model="owner/model", context=4096
            )
            for name in ("linux", "windows"):
                with patch.object(runtime, "system", return_value=name):
                    cmd, env = runtime.backend(s)
                self.assertIn("owner/model", cmd)
                self.assertNotIn(env["VLLM_API_KEY"], cmd)
            with patch.object(runtime, "system", return_value="macos"):
                with self.assertRaisesRegex(ValueError, "Metal-compatible"):
                    runtime.backend(s)
            p = Path(tmp) / "engine.json"
            runtime.write_json(
                p, {"argv": ["program", "--model", "{model}", "--port", "{port}"]}
            )
            cmd, _ = runtime.backend(
                s | dict(engine="Custom", model="a b.gguf", engine_manifest=str(p))
            )
            self.assertEqual(
                cmd, ["program", "--model", "a b.gguf", "--port", str(runtime.PORT)]
            )

    def test_sandbox_and_shortcut_names(self):
        with self.assertRaises(ValueError):
            boxes.box_name("../escape")
        self.assertNotEqual(ident("A"), ident("B"))
        self.assertRegex(ident("special & chars"), r"^[0-9a-f]{20}$")

    def test_refuse_foreign_server(self):
        import socket

        with socket.socket() as other, tempfile.TemporaryDirectory() as tmp:
            other.bind(("127.0.0.1", 0))
            other.listen()
            with (
                patch.object(runtime, "DATA", Path(tmp)),
                patch.object(runtime, "LOCK", FileLock(str(Path(tmp) / "server.lock"))),
                patch.object(runtime, "PORT", other.getsockname()[1]),
            ):
                with self.assertRaisesRegex(RuntimeError, "listener"):
                    runtime.start(runtime.settings())
                self.assertIsNone(runtime.owned()[0])


if __name__ == "__main__":
    unittest.main()
