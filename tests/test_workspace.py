import atexit
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

if "runtime" not in sys.modules and "WORKBENCH_DATA" not in os.environ:
    os.environ["WORKBENCH_DATA"] = tempfile.mkdtemp(prefix="awb-test-")
    atexit.register(shutil.rmtree, os.environ["WORKBENCH_DATA"], ignore_errors=True)

import project_terminal
import runtime
import visual_routes


class WorkspaceTests(unittest.TestCase):
    @patch.object(visual_routes, "public_catalog")
    def test_role_uses_verified_image_input_and_respects_selected_only(self, catalog):
        image = {"architecture": {"input_modalities": ["text", "image"]}}
        catalog.return_value = {
            "sonnet": image,
            visual_routes.PARETO: image,
            "coder": {"architecture": {"input_modalities": ["text"]}},
        }
        s = dict(provider="OpenRouter", remote_model="sonnet", blender=True)
        self.assertEqual(visual_routes.route(s)["reader"], "sonnet")
        self.assertEqual(
            visual_routes.route(s | dict(remote_model="coder"))["reader"],
            visual_routes.PARETO,
        )
        self.assertFalse(
            visual_routes.route(s | dict(remote_model="coder"))["primary_image"]
        )
        self.assertFalse(
            visual_routes.route(s | dict(remote_model="coder", image_route="selected"))[
                "reader"
            ]
        )
        self.assertFalse(
            visual_routes.route(
                s | dict(provider="Attached endpoint", remote_model="private")
            )["reader"]
        )

    def test_generated_omp_image_role_points_to_registered_reader(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(Path, "home", return_value=Path(tmp)),
            patch("runtime.shutil.which", return_value="omp"),
        ):
            s = runtime.DEFAULTS | dict(
                workspace=tmp,
                search=False,
                provider="OpenRouter",
                _visual=dict(primary_image=False, reader="vision"),
            )
            argv = runtime.omp_command(s, model_id="coder", env_key="TEST_KEY")
            overlay = Path(argv[argv.index("--config") + 1])
            cfg = json.loads(overlay.read_text())
            rows = json.loads(overlay.with_name("models.yml").read_text())["providers"][
                "workbench"
            ]["models"]
            self.assertEqual(cfg["modelRoles"]["vision"], "workbench/vision")
            self.assertEqual(
                [(r["id"], r["input"]) for r in rows],
                [("coder", ["text"]), ("vision", ["text", "image"])],
            )
            self.assertIn("ollama", cfg["disabledProviders"])

    def test_project_history_excludes_other_folders(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(Path, "home", return_value=Path(tmp)),
        ):
            root = Path(tmp) / ".omp/profiles/workbench-test/agent/sessions/example"
            root.mkdir(parents=True)
            for name, cwd in [("here", tmp), ("other", str(root))]:
                (root / (name + ".jsonl")).write_text(
                    json.dumps({"type": "title", "title": name})
                    + "\n"
                    + json.dumps(dict(type="session", cwd=cwd, timestamp="2026-11-22"))
                    + "\n"
                )
            self.assertEqual(
                [
                    r["title"]
                    for r in project_terminal.dispatch(
                        "term-history", dict(workspace=tmp)
                    )
                ],
                ["here"],
            )

    def test_project_pty_roundtrip_and_isolation(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            tempfile.TemporaryDirectory() as other,
        ):
            cmd = [
                sys.executable,
                "-u",
                "-c",
                "print('READY',flush=True); print('result='+input()[::-1],flush=True)",
            ]
            session = project_terminal.Session(cmd, os.environ, folder, "Fixture")
            p = dict(workspace=folder, id="test-session")
            project_terminal.SESSIONS[p["id"]] = session
            try:
                with self.assertRaises(ValueError):
                    project_terminal.dispatch(
                        "term-write", p | dict(workspace=other, text="wrong")
                    )
                project_terminal.dispatch("term-write", p | dict(text="sample\r"))
                until = time.monotonic() + 8
                while time.monotonic() < until:
                    if (
                        "result=elpmas"
                        in project_terminal.dispatch("term-read", p)["text"]
                    ):
                        break
                    time.sleep(0.05)
                self.assertIn("result=elpmas", session.snapshot(0)["text"])
            finally:
                project_terminal.dispatch("term-close", p)
