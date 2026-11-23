import atexit
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from filelock import FileLock

if "WORKBENCH_DATA" not in os.environ:
    os.environ["WORKBENCH_DATA"] = tempfile.mkdtemp(prefix="awb-test-")
    atexit.register(shutil.rmtree, os.environ["WORKBENCH_DATA"], ignore_errors=True)

import account_book
import paths
import project_profiles as projects
import runtime

ENGINES = SimpleNamespace(
    MODES=("Auto", "Strata", "llama.cpp", "Flash preset", "vLLM", "Custom")
)
OLD_FILE = {
    "Research": {
        "engine": "Auto",
        "channel": "Tested",
        "context": 32768,
        "kv": "auto",
        "thinking": "off",
        "vision": True,
        "image_route": "auto",
        "draft": True,
        "lora": True,
        "search": True,
        "firecrawl": False,
        "desktop": False,
        "blender": False,
        "harness": "OMP",
        "prompt": "Work in the selected project. Inspect the relevant files before editing.",
        "provider": "OpenRouter",
        "remote_model": "vendor/model-id",
        "document_skill": False,
        "auth_provider": "",
        "plugins": "github,postgres",
        "vllm_model": "",
        "vllm_parser": "",
        "vllm_fraction": "0.80",
        "connection_name": "Model lab",
        "weight": "weights.gguf",
    }
}
LAB = dict(
    provider="OpenRouter",
    name="Model lab",
    connection_id="lab-id",
    endpoint="https://openrouter.ai/api/v1",
)


class ProjectCase(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory(prefix="awb-projects-")
        self.addCleanup(holder.cleanup)
        self.tmp = paths.canonical(holder.name)
        self.data, self.work, self.home = (
            self.tmp / "data",
            self.tmp / "work",
            self.tmp / "home",
        )
        for folder in (self.data, self.work, self.home):
            folder.mkdir()
        self.accounts = []
        for item in (
            patch.object(projects, "DATA", self.data),
            patch.object(account_book, "available", side_effect=lambda: self.accounts),
            patch.object(Path, "home", return_value=self.home),
        ):
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.restore_module, "studio", sys.modules.get("studio"))
        sys.modules["studio"] = ENGINES
        self.s = runtime.DEFAULTS | dict(
            workspace=str(self.work), model="", prompt="Global prompt.", library=""
        )

    @staticmethod
    def restore_module(name, module):
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module

    def put(self, profiles, folder=None):
        file = (folder or self.work) / ".agent-workbench" / "profiles.json"
        file.parent.mkdir(exist_ok=True)
        file.write_text(
            profiles if isinstance(profiles, str) else json.dumps(profiles),
            encoding="utf8",
        )
        return file

    def profile(self, **changes):
        return {k: self.s[k] for k in projects.PORTABLE} | {"weight": ""} | changes

    def plugins(self, *names):
        runtime.write_json(
            self.data / "plugins.json",
            {name: {"command": "fixture", "args": [name]} for name in names},
        )

    def record(self, folder=None):
        store = json.loads((self.data / "projects.json").read_text(encoding="utf8"))
        return store["folders"][paths.project_key(folder or self.work)]

    def apply(self, name, s=None):
        s = s or self.s
        digest = projects.preview_project(s, name, [])["digest"]
        return projects.apply_project(s, name, [], digest)

    def finishes(self, call):
        found = {}

        def run():
            try:
                found["value"] = call()
            except ValueError as error:
                found["error"] = str(error)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(5)
        self.assertFalse(worker.is_alive(), "the call is still waiting")
        return found


class ProfileFileTest(ProjectCase):
    def test_old_profile_file_still_loads(self):
        self.put(OLD_FILE)
        self.plugins("github", "postgres")
        self.accounts.append(LAB)
        self.assertEqual(projects.project_names(self.work), ["Research"])
        out = projects.restore_project(self.s | {"context": 8192}, "Research", [])
        self.assertEqual(
            [out[k] for k in ("provider", "remote_model", "context", "plugins")],
            ["OpenRouter", "vendor/model-id", 32768, "github,postgres"],
        )
        self.assertEqual(
            (out["connection_id"], out["endpoint"]), ("lab-id", LAB["endpoint"])
        )
        self.assertEqual(out["prompt"], "Global prompt.")
        self.assertFalse((self.data / "projects.json").exists())

    def test_loader_names_the_file(self):
        conflict = '{\n"a": {},\n<<<<<<< HEAD\n"b": {}\n=======\n"c": {}\n>>>>>>> x\n}'
        for content, part in (
            (conflict, "not valid JSON near line 3"),
            ("[]", "must hold named profiles"),
            ('{"a": "x"}', "The profile 'a' in"),
            (json.dumps({"n" * 81: {}}), "is damaged"),
            ('{"": {}}', "is damaged"),
            ("[" * 200000, "not valid JSON near line 1"),
        ):
            self.put(content)
            for read in (projects.load_profiles, projects.project_names):
                with self.assertRaises(ValueError, msg=part) as caught:
                    read(self.work)
                self.assertIn(".agent-workbench/profiles.json", str(caught.exception))
                self.assertIn(part, str(caught.exception))
        self.put("x").write_bytes(b'{"caf\xe9": {}}')
        with self.assertRaisesRegex(ValueError, "not valid JSON near line 1"):
            projects.load_profiles(self.work)
        self.put("x").write_bytes(b" " * (projects.MAX_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "larger than 1 MB"):
            projects.load_profiles(self.work)

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(projects.load_profiles(self.work), {})
        self.assertEqual(projects.project_names(str(self.work) + os.sep), [])
        self.assertFalse((self.work / ".agent-workbench").exists())
        for bad in ("", "relative", str(self.work / "missing")):
            with self.assertRaises(ValueError):
                projects.project_names(bad)

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_profile_file_refuses_links(self):
        other = self.tmp / "other"
        other.mkdir()
        (self.work / ".agent-workbench").symlink_to(other, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "goes through a link"):
            projects.save_project(self.s, "Research")
        self.assertEqual(list(other.iterdir()), [])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "named pipes are a POSIX feature")
    def test_special_files_are_refused_without_waiting(self):
        pipe = self.work / ".agent-workbench" / "profiles.json"
        pipe.parent.mkdir()
        os.mkfifo(pipe)
        for call in (
            lambda: projects.project_names(self.work),
            lambda: projects.save_project(self.s, "Research"),
            lambda: projects.preview_project(self.s, "Research", []),
        ):
            self.assertIn(
                ".agent-workbench/profiles.json could not be read",
                self.finishes(call).get("error", ""),
            )
        manifest = self.tmp / "servers.json"
        os.mkfifo(manifest)
        self.assertIn(
            "That file could not be read.",
            self.finishes(lambda: projects.load_manifest(manifest)).get("error", ""),
        )
        pipe.unlink()
        pipe.mkdir()
        with self.assertRaisesRegex(ValueError, "could not be read"):
            projects.project_names(self.work)

    def test_byte_order_mark_is_accepted(self):
        self.put("x").write_bytes(b"\xef\xbb\xbf" + json.dumps(OLD_FILE).encode())
        self.assertEqual(projects.project_names(self.work), ["Research"])
        manifest = self.tmp / "servers.json"
        content = {"mcpServers": {"art": {"command": "one"}}}
        manifest.write_bytes(b"\xef\xbb\xbf" + json.dumps(content).encode())
        self.assertEqual(projects.load_manifest(manifest), content)

    def test_lock_stays_out_of_the_project_folder(self):
        file = self.put({"a": {}})
        old = file.with_name("profiles.json.lock")
        old.write_bytes(b"")
        projects.save_project(self.s, "Research")
        self.assertEqual(sorted(p.name for p in file.parent.iterdir()), ["profiles.json"])
        lock = projects._profile_lock(file)
        self.assertEqual(lock.parent, self.data / "locks")
        self.assertNotEqual(lock, projects._profile_lock(self.tmp / "other" / file.name))
        old.write_text("not ours", encoding="utf8")
        self.assertTrue(projects.edit_profile(self.work, "a", "remove"))
        self.assertEqual(old.read_text(encoding="utf8"), "not ours")

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_old_lock_link_is_not_followed(self):
        victim = self.tmp / "victim.txt"
        victim.write_text("keep")
        file = self.put({"a": {}})
        file.with_name("profiles.json.lock").symlink_to(victim)
        self.assertTrue(projects.edit_profile(self.work, "a", "remove"))
        self.assertEqual(victim.read_text(), "keep")
        self.assertTrue(file.with_name("profiles.json.lock").is_symlink())

    def test_write_failures_say_what_to_do(self):
        file = self.put({"a": {}})
        calls = (
            lambda: projects.save_project(self.s, "Research"),
            lambda: projects.edit_profile(self.work, "a", "remove"),
        )
        with patch.object(projects, "write_json", side_effect=PermissionError(13, "no")):
            for call in calls:
                with self.assertRaises(ValueError) as caught:
                    call()
                self.assertEqual(str(caught.exception), projects.NO_WRITE)
        self.assertIn("Check the folder's permissions", projects.NO_WRITE)
        self.assertEqual(json.loads(file.read_text()), {"a": {}})

    @unittest.skipIf(os.name == "nt", "Windows reports a folder in the way as a busy lock")
    def test_lock_that_cannot_be_opened_says_what_to_do(self):
        file = self.put({"a": {}})
        projects._profile_lock(file).mkdir(parents=True)
        for call in (
            lambda: projects.save_project(self.s, "Research"),
            lambda: projects.edit_profile(self.work, "a", "remove"),
        ):
            with self.assertRaises(ValueError) as caught:
                call()
            self.assertEqual(str(caught.exception), projects.NO_WRITE)
        self.assertEqual(json.loads(file.read_text()), {"a": {}})
        sealed = self.tmp / "sealed"
        sealed.mkdir()
        sealed.chmod(0o555)
        self.addCleanup(sealed.chmod, 0o755)
        if os.access(sealed, os.W_OK):
            self.skipTest("this account can write to a read-only folder")
        with self.assertRaises(ValueError) as caught:
            projects.save_project(self.s | {"workspace": str(sealed)}, "Research")
        self.assertEqual(str(caught.exception), projects.NO_WRITE)

    @staticmethod
    def reads_but_cannot_be_written(depth):
        try:
            rows = json.loads("[" * depth + "]" * depth)
        except RecursionError:
            return False
        try:
            json.dumps(rows, indent=2)
        except RecursionError:
            return True
        return False

    def test_deep_nesting_cannot_crash_a_save(self):
        depth = next(filter(self.reads_but_cannot_be_written, (1500, 3000)), None)
        if depth is None:
            self.skipTest("this Python writes every depth it reads")
        content = '{"deep": {"x": ' + "[" * depth + "]" * depth + "}}"
        file = self.put(content)
        self.assertEqual(projects.project_names(self.work), ["deep"])
        for call in (
            lambda: projects.save_project(self.s, "Research"),
            lambda: projects.edit_profile(self.work, "deep", "rename", "other"),
        ):
            with self.assertRaisesRegex(
                ValueError, r"profiles\.json is nested too deeply to save\. Fix or delete"
            ):
                call()
        self.assertEqual(file.read_text(encoding="utf8"), content)
        self.assertEqual(
            sorted(p.name for p in file.parent.iterdir()),
            ["profiles.json"],
        )

    def test_busy_profile_lock_gives_up(self):
        file = self.put({"a": {}})
        with (
            FileLock(str(projects._profile_lock(file))),
            patch.object(projects, "LOCK_WAIT", 0.1),
        ):
            for call in (
                lambda: projects.save_project(self.s, "Research"),
                lambda: projects.edit_profile(self.work, "a", "remove"),
            ):
                self.assertEqual(self.finishes(call).get("error"), projects.BUSY)
        self.assertTrue(projects.edit_profile(self.work, "a", "remove"))

    def test_missing_profile_message(self):
        for prepare in (lambda: None, lambda: self.put({"other": {}})):
            prepare()
            for call in (
                lambda: projects.restore_project(self.s, "gone", []),
                lambda: projects.preview_project(self.s, "gone", []),
                lambda: projects.apply_project(self.s, "gone", [], "x"),
            ):
                with self.assertRaisesRegex(ValueError, "no longer exists"):
                    call()

    def test_edit_profile_renames_and_removes(self):
        with self.assertRaisesRegex(ValueError, "Profile no longer exists."):
            projects.edit_profile(self.work, "a", "remove")
        self.assertFalse((self.work / ".agent-workbench").exists())
        self.put({"a": {"context": 4096}, "b": {}})
        for name, op, new, message in (
            ("gone", "remove", "", "Profile no longer exists."),
            ("a", "rename", "b", "Enter a new, unused profile name"),
            ("a", "rename", "  ", "Enter a new, unused profile name"),
            ("a", "rename", "n" * 81, "Enter a new, unused profile name"),
            ("a", "copy", "", "Unknown profile operation."),
        ):
            with self.assertRaisesRegex(ValueError, message):
                projects.edit_profile(self.work, name, op, new)
        self.assertEqual(projects.project_names(self.work), ["a", "b"])
        self.assertTrue(projects.edit_profile(self.work, "a", "rename", " c "))
        self.assertEqual(projects.load_profiles(self.work)["c"], {"context": 4096})
        self.assertTrue(projects.edit_profile(self.work, "b", "remove"))
        self.assertEqual(projects.project_names(self.work), ["c"])


class ValidationTest(ProjectCase):
    def refused(self, **item):
        with self.assertRaises(ValueError, msg=repr(item)) as caught:
            projects.validate_profile(item, "lab")
        return str(caught.exception)

    def test_validation_rejects_shell_text(self):
        for key, value in (
            ("remote_model", "a&calc"),
            ("remote_model", 'x" & calc & "'),
            ("remote_model", "-rm"),
            ("remote_model", "a b"),
            ("remote_model", "a\nb"),
            ("remote_model", "m" * 201),
            ("vllm_parser", "hermes;id>/tmp/x"),
            ("vllm_model", "org/model;id"),
            ("vllm_model", "org/" + "m" * 200),
            ("plugins", "a,b c"),
            ("plugins", "a,a"),
            ("plugins", "search"),
            ("auth_provider", "x y"),
            ("auth_provider", "$(id)"),
            ("weight", "../a.gguf"),
            ("weight", "a\\b.gguf"),
            ("weight", ".."),
            ("weight", "a\0.gguf"),
            ("connection_name", "lab\nrm"),
            ("connection_name", "n" * 91),
        ):
            message = self.refused(**{key: value})
            self.assertIn("'lab'", message)
            self.assertIn("for " + projects.LABELS[key], message)
            self.assertNotIn(value, message)

    def test_validation_accepts_real_ids(self):
        for model in (
            "",
            "vendor/model:free",
            "~unbiased/pareto-latest",
            "hf.co/org/repo:Q4_K_M",
            "qwen3-coder:30b",
            "/models/x.gguf",
        ):
            self.assertEqual(
                projects.validate_profile({"remote_model": model}), {"remote_model": model}
            )
        item = {
            "auth_provider": "",
            "vllm_parser": "",
            "plugins": "",
            "vllm_model": "",
            "weight": "",
        }
        self.assertEqual(projects.validate_profile(item), item)
        item = {
            "auth_provider": "openai-codex-device",
            "vllm_parser": "hermes",
            "plugins": "github,,my_tool",
            "vllm_model": "Qwen/Qwen3-Coder",
            "vllm_fraction": "0.9",
            "weight": "Model Q4 (v2).gguf",
            "connection_name": "Model lab · home",
            "prompt": "First line.\r\n\tSecond line.",
        }
        self.assertEqual(
            projects.validate_profile(item),
            item | {"plugins": "github,my_tool", "prompt": "First line.\n\tSecond line."},
        )
        self.assertEqual(item["plugins"], "github,,my_tool")

    def test_validation_types(self):
        for key, value in (
            ("context", "65536"),
            ("context", True),
            ("context", 512),
            ("context", 2000000),
            ("context", 4096.0),
            ("vision", 1),
            ("search", "true"),
            ("engine", "Evil"),
            ("engine", ["Auto"]),
            ("channel", "Nightly"),
            ("kv", "fp4"),
            ("thinking", "extreme"),
            ("image_route", "all"),
            ("vllm_fraction", "2"),
            ("vllm_fraction", "0.01"),
            ("vllm_fraction", "0.99"),
            ("vllm_fraction", 0.8),
            ("vllm_fraction", "0.٣"),
            ("prompt", "p" * 9000),
            ("prompt", "run \x1b[2J"),
            ("prompt", "del \x7f"),
            ("prompt", "lone \r return"),
            ("prompt", None),
            ("remote_model", 7),
            ("plugins", ["a"]),
            ("provider", {"a": 1}),
        ):
            self.assertIn("for " + projects.LABELS[key], self.refused(**{key: value}))
        self.assertIn("uses Nope, which this version", self.refused(harness="Nope"))
        self.assertIn("uses Bedrock, which", self.refused(provider="Bedrock"))
        self.assertIn("for Agent", self.refused(harness="x" * 41))
        self.assertIn("for Agent", self.refused(harness="a\nb"))
        self.assertIn("is damaged", self.refused_item("text"))

    def test_validation_accepts_every_engine_release(self):
        import runtime

        self.assertEqual(projects.CHOICES["channel"], runtime.CHANNELS)
        for channel in ("Tested", "Updated", "Unsloth"):
            self.assertEqual(
                projects.validate_profile({"channel": channel}), {"channel": channel}
            )

    def refused_item(self, item):
        with self.assertRaises(ValueError) as caught:
            projects.validate_profile(item, "lab")
        return str(caught.exception)

    def test_validation_keeps_order_and_drops_unknown_keys(self):
        item = {"weight": "a.gguf", "endpoint": "https://evil", "context": 4096}
        self.assertEqual(
            list(projects.validate_profile(item).items()),
            [("context", 4096), ("weight", "a.gguf")],
        )
        full = self.profile(connection_name="Lab")
        self.assertEqual(
            list(projects.validate_profile(full)),
            [*projects.PORTABLE, "connection_name", "weight"],
        )

    def test_recipe_and_labels_cover_their_keys(self):
        self.assertEqual(len(projects.PORTABLE), 23)
        for key in (*projects.PORTABLE, "connection_name", "weight", "model", "connection"):
            self.assertIn(key, projects.LABELS)
        self.assertTrue(set(projects.CAPS) < set(projects.RECIPE))


class RestoreTest(ProjectCase):
    def test_unknown_keys_are_ignored(self):
        self.put(
            {"p": self.profile(context=4096) | {"endpoint": "https://evil", "model": "/x"}}
        )
        out = projects.restore_project(self.s, "p", [])
        self.assertEqual(out["endpoint"], self.s["endpoint"])
        self.assertEqual(out["model"], "")
        self.assertEqual(out["context"], 4096)
        self.assertNotIn("weight", out)

    def test_apply_keeps_local_model(self):
        s = self.s | {"model": "/models/a.gguf"}
        self.put(
            {
                "hosted": self.profile(provider="OpenAI", remote_model="gpt-x"),
                "vllm": self.profile(
                    engine="vLLM", vllm_model="org/model", weight="missing.gguf"
                ),
            }
        )
        out = self.apply("hosted", s)
        self.assertEqual(out["model"], "/models/a.gguf")
        self.assertEqual(out["connection_id"], "legacy-OpenAI")
        out = self.apply("vllm", s)
        self.assertEqual((out["model"], out["engine"]), ("/models/a.gguf", "vLLM"))

    def test_weight_is_matched_in_the_library(self):
        weight = self.tmp / "weights.gguf"
        weight.touch()
        self.put({"p": self.profile(weight="weights.gguf")})
        s = self.s | {"model": "/previous/other.gguf"}
        preview = projects.preview_project(s, "p", [str(weight)])
        self.assertEqual(
            [(c["field"], c["from"], c["to"], c["scope"]) for c in preview["changes"]],
            [("model", "other.gguf", "weights.gguf", "global")],
        )
        self.assertEqual(preview["changes"][0]["label"], "Local weights")
        self.assertEqual(
            projects.restore_project(s, "p", [str(weight)])["model"], str(weight)
        )

    def test_managed_local_missing_weight_blocks(self):
        self.put({"p": self.profile(weight="big.gguf")})
        s = self.s | {"model": "/models/a.gguf"}
        sentence = "Add big.gguf to your model library before applying this profile."
        preview = projects.preview_project(s, "p", [])
        self.assertEqual(preview["blockers"], [sentence])
        self.assertIsNone(preview["needs"])
        with self.assertRaises(ValueError) as caught:
            projects.restore_project(s, "p", [])
        self.assertEqual(str(caught.exception), sentence)
        with self.assertRaises(ValueError) as caught:
            projects.apply_project(s, "p", [], preview["digest"])
        self.assertEqual(str(caught.exception), sentence)
        self.assertFalse((self.data / "projects.json").exists())

    def test_serving_values_the_engine_refuses_block_a_local_profile(self):
        manifest = self.tmp / "engine.json"
        manifest.write_text(json.dumps({"argv": ["server", "--port", "{port}"]}))
        self.put(
            {
                "cache": self.profile(kv="fp16"),
                "custom": self.profile(engine="Custom"),
                "hosted": self.profile(
                    engine="Custom", kv="fp16", provider="OpenAI", remote_model="gpt-x"
                ),
            }
        )
        cache = "Choose auto or fp8 for the KV cache."
        custom = (
            "Choose a custom engine manifest, a JSON file with an argv array, "
            "in Advanced → Model library."
        )
        for name, sentence in (("cache", cache), ("custom", custom)):
            preview = projects.preview_project(self.s, name, [])
            self.assertEqual(preview["blockers"], [sentence], name)
            with self.assertRaises(ValueError) as caught:
                projects.restore_project(self.s, name, [])
            self.assertEqual(str(caught.exception), sentence)
            with self.assertRaises(ValueError) as caught:
                projects.apply_project(self.s, name, [], preview["digest"])
            self.assertEqual(str(caught.exception), sentence)
        self.assertFalse((self.data / "projects.json").exists())
        ready = self.s | {"engine_manifest": str(manifest)}
        self.assertEqual(projects.preview_project(ready, "custom", [])["blockers"], [])
        self.assertEqual(self.apply("custom", ready)["engine"], "Custom")
        self.assertEqual(projects.preview_project(self.s, "hosted", [])["blockers"], [])
        out = self.apply("hosted")
        self.assertEqual((out["provider"], out["engine"], out["kv"]), ("OpenAI", "Custom", "fp16"))

    def test_serving_check_comes_after_the_other_blockers(self):
        self.put({"p": self.profile(kv="fp16", weight="big.gguf", plugins="gone")})
        refusal = ValueError("Engine says no.")
        with patch.object(projects, "validate_serving", side_effect=refusal) as check:
            preview = projects.preview_project(self.s, "p", [])
        self.assertEqual(
            preview["blockers"],
            [
                "Add big.gguf to your model library before applying this profile.",
                "Register these MCP plugins in Plugins first: gone.",
                "Engine says no.",
            ],
        )
        merged = check.call_args.args[0]
        self.assertEqual((merged["kv"], merged["provider"]), ("fp16", "Managed local"))

    @unittest.skipIf(
        os.name == "nt" or getattr(os, "geteuid", lambda: 1)() == 0,
        "needs a folder this user cannot enter",
    )
    def test_weights_in_a_folder_that_cannot_be_entered_count_as_missing(self):
        locked = self.tmp / "locked"
        locked.mkdir()
        (locked / "big.gguf").touch()
        self.put({"p": self.profile(weight="big.gguf")})
        os.chmod(locked, 0)
        self.addCleanup(os.chmod, locked, 0o755)
        preview = projects.preview_project(self.s, "p", [str(locked / "big.gguf")])
        self.assertEqual(
            preview["blockers"],
            ["Add big.gguf to your model library before applying this profile."],
        )

    def test_prompt_is_folder_scoped(self):
        other = self.tmp / "other"
        other.mkdir()
        self.put({"p": self.profile(prompt="Ask before changing prices.")})
        out = self.apply("p")
        self.assertEqual(out["prompt"], "Global prompt.")
        self.assertEqual(
            projects.launch_settings(self.s)["prompt"], "Ask before changing prices."
        )
        self.assertEqual(self.s["prompt"], "Global prompt.")
        elsewhere = self.s | {"workspace": str(other)}
        self.assertIs(projects.launch_settings(elsewhere), elsewhere)
        view = projects.folder_view(self.work)
        self.assertEqual(
            view["prompt"], {"text": "Ask before changing prices.", "source": "p"}
        )
        self.assertEqual(view["profile"], "p")
        self.assertFalse((self.data / "settings.json").exists())
        self.assertIsNone(projects.clear_prompt(self.work)["prompt"])
        self.assertIs(projects.launch_settings(self.s), self.s)

    def test_launch_settings_never_raises(self):
        for s in ({}, {"workspace": ""}, {"workspace": None}, {"workspace": "gone"}):
            self.assertIs(projects.launch_settings(s), s)
        (self.data / "projects.json").write_text("{broken")
        self.assertIs(projects.launch_settings(self.s), self.s)

    def test_prompt_equal_to_global_clears_override(self):
        self.put(
            {
                "custom": self.profile(prompt="Folder prompt."),
                "plain": self.profile(prompt="Global prompt."),
                "silent": {"context": 4096},
            }
        )
        self.apply("custom")
        self.assertIn("prompt", self.record())
        self.apply("plain")
        self.assertNotIn("prompt", self.record())
        self.assertEqual(self.record()["profile"], "plain")
        self.apply("custom")
        self.apply("silent")
        self.assertNotIn("prompt", self.record())

    def test_preview_lists_every_change(self):
        prompt = "Line one.\nLine two with every word shown. " * 20
        self.put(
            {
                "p": self.profile(
                    harness="Codex", context=65536, prompt=prompt, firecrawl=True
                )
            }
        )
        preview = projects.preview_project(self.s, "p", [])
        rows = {row["field"]: row for row in preview["changes"]}
        self.assertEqual(
            [row["field"] for row in preview["changes"]],
            ["context", "firecrawl", "harness", "prompt"],
        )
        self.assertEqual(
            {k: (v["label"], v["scope"]) for k, v in rows.items()},
            {
                "harness": ("Agent", "folder"),
                "context": ("Context", "global"),
                "prompt": ("Prompt for this folder", "folder"),
                "firecrawl": ("Page crawling", "folder"),
            },
        )
        self.assertEqual(
            (rows["prompt"]["from"], rows["prompt"]["to"]), ("Global prompt.", prompt)
        )
        self.assertEqual((rows["context"]["from"], rows["context"]["to"]), (32768, 65536))
        self.assertEqual(preview["unchanged"], len(projects.PORTABLE) + 1 - 4)
        self.assertEqual(
            (preview["name"], preview["blockers"], preview["notes"], preview["needs"]),
            ("p", [], [], None),
        )
        self.assertRegex(preview["digest"], "^[0-9a-f]{64}$")
        self.assertFalse((self.data / "projects.json").exists())

    def test_preview_shows_a_prompt_that_apply_resets(self):
        self.put({"custom": self.profile(prompt="Folder prompt."), "silent": {"context": 4096}})
        self.assertEqual(
            [c["field"] for c in projects.preview_project(self.s, "silent", [])["changes"]],
            ["context"],
        )
        self.apply("custom")
        preview = projects.preview_project(self.s, "silent", [])
        rows = {c["field"]: c for c in preview["changes"]}
        self.assertEqual(list(rows), ["context", "prompt"])
        self.assertEqual(
            (rows["prompt"]["from"], rows["prompt"]["to"], rows["prompt"]["scope"]),
            ("Folder prompt.", "Global prompt.", "folder"),
        )
        self.assertEqual(preview["unchanged"], 0)
        projects.apply_project(self.s, "silent", [], preview["digest"])
        self.assertEqual(projects.launch_settings(self.s)["prompt"], rows["prompt"]["to"])

    def test_preview_compares_prompt_with_the_folder_prompt(self):
        self.put({"a": self.profile(prompt="Folder prompt."), "b": self.profile()})
        self.apply("a")
        self.assertEqual(projects.preview_project(self.s, "a", [])["changes"], [])
        row = projects.preview_project(self.s, "b", [])["changes"][0]
        self.assertEqual(
            (row["field"], row["from"], row["to"]),
            ("prompt", "Folder prompt.", "Global prompt."),
        )

    def test_apply_needs_matching_confirmation(self):
        file = self.put({"p": self.profile(context=4096)})
        digest = projects.preview_project(self.s, "p", [])["digest"]
        for confirm in ("", None):
            with self.assertRaises(ValueError) as caught:
                projects.apply_project(self.s, "p", [], confirm)
            self.assertEqual(
                str(caught.exception),
                "Review what this profile changes, then confirm to apply it.",
            )
        self.put({"p": self.profile(context=8192)})
        with self.assertRaises(ValueError) as caught:
            projects.apply_project(self.s, "p", [], digest)
        self.assertEqual(
            str(caught.exception),
            "This profile changed after you reviewed it. Review it again, then apply it.",
        )
        self.assertFalse((self.data / "projects.json").exists())
        fresh = projects.preview_project(self.s, "p", [])["digest"]
        self.assertNotEqual(fresh, digest)
        out = projects.apply_project(self.s, "p", [], fresh)
        self.assertEqual(out["context"], 8192)
        self.assertEqual(json.loads(file.read_text())["p"]["context"], 8192)
        self.assertEqual(self.record()["recipe"]["harness"], "OMP")
        self.assertEqual(self.record()["recipe"]["preset"], "")

    def test_preview_reports_missing_connection_and_plugins(self):
        self.put(
            {
                "p": self.profile(
                    provider="OpenRouter",
                    connection_name="Model lab",
                    plugins="github,postgres",
                )
            }
        )
        self.plugins("other")
        preview = projects.preview_project(self.s, "p", [])
        self.assertEqual(
            preview["needs"],
            {"connection": {"provider": "OpenRouter", "name": "Model lab"}},
        )
        self.assertEqual(
            preview["blockers"],
            [
                "Add an OpenRouter connection named Model lab in Connections, then apply this profile.",
                "Register these MCP plugins in Plugins first: github, postgres.",
            ],
        )
        with self.assertRaisesRegex(ValueError, "Model lab"):
            projects.restore_project(self.s, "p", [])
        self.accounts.append(LAB)
        self.plugins("github", "postgres")
        preview = projects.preview_project(self.s, "p", [])
        self.assertEqual((preview["blockers"], preview["needs"]), ([], None))
        row = next(c for c in preview["changes"] if c["field"] == "connection")
        self.assertEqual(
            (row["from"], row["to"], row["scope"], row["label"]),
            ("", "Model lab", "folder", "Connection"),
        )
        self.accounts.append(LAB | {"connection_id": "twin"})
        preview = projects.preview_project(self.s, "p", [])
        self.assertIn("More than one OpenRouter connection", preview["blockers"][0])
        self.assertIsNone(preview["needs"])

    def test_attached_endpoint_without_a_name(self):
        self.put({"p": self.profile(provider="Attached endpoint")})
        preview = projects.preview_project(self.s, "p", [])
        self.assertEqual(
            preview["blockers"],
            ["Add an Attached endpoint connection in Connections, then apply this profile."],
        )
        self.assertEqual(
            preview["needs"],
            {"connection": {"provider": "Attached endpoint", "name": ""}},
        )
        box = dict(
            provider="Attached endpoint",
            name="Box",
            connection_id="box",
            endpoint="http://127.0.0.1:1234/v1",
        )
        self.accounts.append(box)
        out = projects.restore_project(self.s, "p", [])
        self.assertEqual((out["connection_id"], out["endpoint"]), ("box", box["endpoint"]))
        self.accounts.append(box | {"name": "Other", "connection_id": "other"})
        with self.assertRaisesRegex(ValueError, "does not say which"):
            projects.restore_project(self.s, "p", [])

    def test_desktop_off_outside_windows(self):
        self.put({"p": self.profile(desktop=True)})
        note = "Windows desktop control is not available on this computer and stays off."
        with patch.object(projects, "_windows", return_value=False):
            self.assertIs(projects.restore_project(self.s, "p", [])["desktop"], False)
            preview = projects.preview_project(self.s, "p", [])
            self.assertEqual(preview["notes"], [note])
            self.assertEqual(preview["changes"], [])
        with patch.object(projects, "_windows", return_value=True):
            self.assertIs(projects.restore_project(self.s, "p", [])["desktop"], True)
            preview = projects.preview_project(self.s, "p", [])
            self.assertEqual(preview["notes"], [])
            self.assertEqual([c["field"] for c in preview["changes"]], ["desktop"])

    def test_windows_flag_follows_the_platform(self):
        self.assertEqual(projects._windows(), os.name == "nt")

    def test_save_validates_and_uses_folder_prompt(self):
        with self.assertRaisesRegex(ValueError, "for Model"):
            projects.save_project(self.s | {"remote_model": "a&b"}, "bad")
        self.assertFalse((self.work / ".agent-workbench").exists())
        for name in ("", "  ", "n" * 81, "a\nb"):
            with self.assertRaisesRegex(ValueError, "1–80 characters"):
                projects.save_project(self.s, name)
        self.put({"p": self.profile(prompt="Folder prompt.")})
        self.apply("p")
        self.accounts.append(LAB)
        s = self.s | dict(
            provider="OpenRouter",
            connection_id="lab-id",
            model="/library/weights.gguf",
            endpoint="https://private.invalid/v1",
        )
        file = Path(projects.save_project(s, " shared "))
        self.assertEqual(file, self.work / ".agent-workbench" / "profiles.json")
        saved = json.loads(file.read_text(encoding="utf8"))
        self.assertEqual(sorted(saved), ["p", "shared"])
        self.assertEqual(saved["shared"]["prompt"], "Folder prompt.")
        self.assertEqual(
            list(saved["shared"]), [*projects.PORTABLE, "connection_name", "weight"]
        )
        self.assertEqual(
            (saved["shared"]["connection_name"], saved["shared"]["weight"]),
            ("Model lab", "weights.gguf"),
        )
        for private in ("private.invalid", "lab-id", "/library/", str(self.work)):
            self.assertNotIn(private, file.read_text(encoding="utf8"))


class FolderMemoryTest(ProjectCase):
    def test_remember_and_view_round_trip(self):
        self.plugins("github")
        values = self.s | dict(
            harness="Claude Code",
            provider="Attached endpoint",
            connection_id="9f2c41d07a6b3e55",
            remote_model="qwen3-coder:30b",
            plugins="github",
            preset="",
            firecrawl=True,
        )
        view = projects.remember(self.work, values)
        expected = {
            "harness": "Claude Code",
            "provider": "Attached endpoint",
            "connection_id": "9f2c41d07a6b3e55",
            "auth_provider": "",
            "preset": "",
            "remote_model": "qwen3-coder:30b",
            "image_route": "auto",
            "plugins": "github",
            "search": True,
            "firecrawl": True,
            "blender": False,
            "desktop": False,
        }
        self.assertEqual(view["recipe"], expected)
        self.assertEqual(self.record()["recipe"], expected)
        self.assertEqual(
            (view["known"], view["stale"], view["model_label"], view["path"]),
            (True, [], "qwen3-coder:30b", str(self.work)),
        )
        self.assertEqual(view["key"], paths.project_key(self.work))
        self.assertRegex(view["updated"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$")
        again = projects.remember(str(self.work) + os.sep, {"harness": "Codex"})
        self.assertEqual(again["recipe"], expected | {"harness": "Codex"})
        self.assertEqual(again["model_label"], "qwen3-coder:30b")
        store = json.loads((self.data / "projects.json").read_text(encoding="utf8"))
        self.assertEqual(store["version"], 1)
        self.assertEqual(list(store["folders"]), [paths.project_key(self.work)])
        self.assertEqual(projects.folder_view(self.work), again)
        self.assertEqual(projects.dispatch("folder-get", {"workspace": str(self.work)}), again)

    def test_remember_skips_invalid_values_and_labels_the_model(self):
        view = projects.remember(
            self.work,
            {
                "harness": "Nope",
                "provider": "Managed local",
                "model": "/library/weights.gguf",
                "remote_model": "a b",
                "search": "yes",
                "blender": True,
                "context": 4096,
            },
        )
        self.assertEqual(view["recipe"], {"provider": "Managed local", "blender": True})
        self.assertEqual(view["model_label"], "weights.gguf")
        named = projects.remember(self.work, {"preset": "Fast local"})
        self.assertEqual(named["model_label"], "Fast local")
        self.assertEqual(named["recipe"]["preset"], "Fast local")

    def test_view_of_unknown_and_invalid_folders(self):
        view = projects.folder_view(self.work)
        self.assertEqual(
            view,
            {
                "key": paths.project_key(self.work),
                "path": str(self.work),
                "known": False,
                "recipe": {},
                "stale": [],
                "model_label": "",
                "kit": "",
                "profile": "",
                "prompt": None,
                "project_mcp": {},
                "updated": "",
                "consent_granted": "",
                "saved_default": "",
                "offer_default": False,
            },
        )
        self.assertFalse((self.data / "projects.json").exists())
        gone = str(self.work / "gone")
        with self.assertRaises(ValueError):
            projects.folder_view(gone)
        self.assertEqual(
            projects.folder_view(gone, strict=False),
            view | {"key": "", "path": gone},
        )
        self.assertEqual(projects.folder_view(None, strict=False)["path"], "")
        with self.assertRaises(ValueError):
            projects.remember("", {"harness": "OMP"})

    def write_store(self, record, folder=None):
        runtime.write_json(
            self.data / "projects.json",
            {
                "version": 1,
                "folders": {paths.project_key(folder or self.work): record},
            },
        )

    def test_view_filters_stale_values(self):
        self.plugins("github")
        self.write_store(
            {
                "recipe": {
                    "harness": "Nope",
                    "search": "yes",
                    "plugins": "gone,github",
                    "provider": "OpenAI",
                    "connection_id": "x" * 121,
                    "context": 4096,
                },
                "model_label": 5,
                "kit": ["web"],
                "prompt": "text",
                "project_mcp": {"playwright": True, "bad name": True, "x": "on"},
                "updated": "2026-11-22T21:04:11+00:00",
            }
        )
        view = projects.folder_view(self.work)
        self.assertEqual(view["recipe"], {"provider": "OpenAI", "plugins": "github"})
        self.assertCountEqual(
            view["stale"], ["harness", "search", "connection_id", "plugin:gone"]
        )
        self.assertEqual(
            (view["model_label"], view["kit"], view["prompt"], view["project_mcp"]),
            ("", "", None, {"playwright": True}),
        )
        self.write_store({"recipe": {"plugins": "gone", "desktop": True}})
        with patch.object(projects, "_windows", return_value=False):
            view = projects.folder_view(self.work)
        self.assertEqual(view["recipe"], {"plugins": "", "desktop": False})
        self.assertEqual(view["stale"], ["plugin:gone"])
        with patch.object(projects, "_windows", return_value=True):
            self.assertIs(projects.folder_view(self.work)["recipe"]["desktop"], True)

    def test_corrupt_store_is_tolerated(self):
        file, bad = self.data / "projects.json", self.data / "projects.json.bad"
        for content in ("{broken", "[]", '{"version": 2, "folders": {}}', '{"version": 1}'):
            file.write_text(content)
            self.assertFalse(projects.folder_view(self.work)["known"])
            self.assertEqual(projects.folders(), [])
        file.write_text("{broken")
        self.assertFalse(projects.forget(self.work))
        self.assertFalse(bad.exists())
        self.assertEqual(file.read_text(), "{broken")
        projects.remember(self.work, {"harness": "Pi"})
        self.assertEqual(bad.read_text(), "{broken")
        self.assertEqual(projects.folder_view(self.work)["recipe"], {"harness": "Pi"})
        file.write_text("again broken")
        projects.remember(self.work, {"harness": "Codex"})
        self.assertEqual(bad.read_text(), "{broken")
        runtime.write_json(
            file, {"version": 1, "folders": {"/x": "text", "/y": {"kit": "web"}}}
        )
        self.assertEqual([row["key"] for row in projects.folders()], ["/y"])

    def test_prune_keeps_newest(self):
        made = []
        for name in "abcde":
            made.append(self.tmp / name)
            made[-1].mkdir()
        keys = [paths.project_key(folder) for folder in made]
        with patch.object(projects, "MAX_FOLDERS", 3):
            for folder in made[:4]:
                projects.remember(folder, {"harness": "OMP"})
            self.assertEqual([row["key"] for row in projects.folders()], keys[3:0:-1])
            projects.remember(made[1], {"harness": "Pi"})
            projects.remember(made[4], {"harness": "OMP"})
            self.assertEqual(
                [row["key"] for row in projects.folders()], [keys[4], keys[1], keys[3]]
            )
        stamps = iter(["1999-01-03T00:00:00+00:00", "1999-01-01T00:00:00+00:00"])
        with (
            patch.object(projects, "MAX_FOLDERS", 3),
            patch.object(projects, "_now", side_effect=lambda: next(stamps)),
        ):
            projects.remember(made[3], {"harness": "OMP"})
            projects.remember(made[0], {"harness": "OMP"})
        self.assertEqual(
            [row["key"] for row in projects.folders()], [keys[4], keys[1], keys[3]]
        )

    def test_folders_lists_newest_first(self):
        other = self.tmp / "other"
        other.mkdir()
        stamps = iter(["2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"])
        with patch.object(projects, "_now", side_effect=lambda: next(stamps)):
            projects.remember(
                self.work, {"harness": "Pi", "provider": "OpenAI", "remote_model": "gpt-x"}
            )
            projects.set_kit(other, "web")
        other.rmdir()
        self.assertEqual(
            projects.dispatch("folder-list", {}),
            [
                {
                    "key": paths.fold(str(other)),
                    "path": str(other),
                    "exists": False,
                    "harness": "",
                    "provider": "",
                    "model_label": "",
                    "kit": "web",
                    "profile": "",
                    "updated": "2026-01-02T00:00:00+00:00",
                },
                {
                    "key": paths.project_key(self.work),
                    "path": str(self.work),
                    "exists": True,
                    "harness": "Pi",
                    "provider": "OpenAI",
                    "model_label": "gpt-x",
                    "kit": "",
                    "profile": "",
                    "updated": "2026-01-01T00:00:00+00:00",
                },
            ],
        )

    @unittest.skipIf(
        os.name == "nt" or getattr(os, "geteuid", lambda: 1)() == 0,
        "needs a folder this user cannot enter",
    )
    def test_folders_lists_a_folder_that_can_no_longer_be_entered(self):
        locked = self.tmp / "locked"
        inner = locked / "proj"
        inner.mkdir(parents=True)
        projects.set_kit(inner, "web")
        os.chmod(locked, 0)
        self.addCleanup(os.chmod, locked, 0o755)
        rows = projects.dispatch("folder-list", {})
        self.assertEqual(
            [(row["path"], row["exists"], row["kit"]) for row in rows],
            [(str(inner), False, "web")],
        )

    def test_windows_key_is_folded_and_path_keeps_its_case(self):
        with patch.object(
            projects.folder_paths, "fold", side_effect=lambda text: text.upper()
        ):
            projects.remember(self.work, {"harness": "OMP"})
            view = projects.folder_view(self.work)
        store = json.loads((self.data / "projects.json").read_text(encoding="utf8"))
        self.assertEqual(list(store["folders"]), [str(self.work).upper()])
        self.assertEqual(store["folders"][view["key"]]["path"], str(self.work))
        self.assertEqual(view["path"], str(self.work))

    def test_forget_by_key_for_deleted_folder(self):
        other = self.tmp / "other"
        other.mkdir()
        key = projects.remember(other, {"harness": "OMP"})["key"]
        projects.remember(self.work, {"harness": "OMP"})
        other.rmdir()
        self.assertFalse(projects.forget(str(self.tmp / "never")))
        self.assertTrue(projects.dispatch("folder-forget", {"workspace": key}))
        self.assertFalse(projects.forget(key))
        self.assertTrue(projects.forget(self.work))
        self.assertEqual(projects.folders(), [])
        self.assertFalse(projects.forget(None))

    def test_busy_store_lock_gives_up(self):
        self.plugins("art")
        with (
            FileLock(str(self.data / "projects.lock")),
            FileLock(str(self.data / "plugins.lock")),
            patch.object(projects, "LOCK_WAIT", 0.1),
        ):
            for call in (
                lambda: projects.remember(self.work, {"harness": "Pi"}),
                lambda: projects.forget(self.work),
                lambda: projects.register({"mcpServers": {"new": {"command": "x"}}}),
                lambda: projects.unregister("art"),
            ):
                self.assertEqual(self.finishes(call).get("error"), projects.BUSY)
        self.assertFalse((self.data / "projects.json").exists())
        self.assertEqual(list(projects.registry()), ["art"])
        self.assertEqual(projects.remember(self.work, {"harness": "Pi"})["recipe"], {"harness": "Pi"})
        self.assertEqual(projects.LOCK_WAIT, 10)

    def test_consent_record_round_trip(self):
        digest = "3f0a" * 16
        self.assertIsNone(projects.consent_record(self.work))
        self.assertFalse(projects.revoke_consent(self.work))
        self.assertFalse((self.data / "projects.json").exists())
        items = [".agent-workbench/project.json", ".mcp.json"]
        granted = projects.grant_consent(self.work, digest, items)
        self.assertEqual((granted["digest"], granted["items"]), (digest, items))
        self.assertEqual(projects.consent_record(self.work), granted)
        self.assertRegex(granted["granted"], r"^\d{4}-\d\d-\d\dT")
        self.assertEqual(projects.folder_view(self.work)["consent_granted"], granted["granted"])
        for bad_digest, bad_items in (
            ("3F0A" * 16, items),
            (digest[:-1], items),
            (digest + "\n", items),
            (None, items),
            (digest, "x"),
            (digest, [1]),
            (digest, ["x"] * 65),
        ):
            with self.assertRaises(ValueError):
                projects.grant_consent(self.work, bad_digest, bad_items)
        self.assertEqual(projects.consent_record(self.work)["digest"], digest)
        self.assertTrue(projects.revoke_consent(self.work))
        self.assertIsNone(projects.consent_record(self.work))
        self.assertEqual(projects.folder_view(self.work)["consent_granted"], "")
        self.assertFalse(projects.revoke_consent(self.work))
        for odd in ("not a record", {"granted": 7}, {"granted": "x\n"}):
            with self.subTest(odd):
                self.write_store({"consent": odd})
                self.assertEqual(projects.folder_view(self.work)["consent_granted"], "")

    def test_project_mcp_kit_and_written(self):
        self.assertEqual(projects.project_mcp(self.work), {})
        self.assertEqual(projects.written(self.work), {})
        self.assertEqual(
            projects.set_project_mcp(self.work, "playwright", True), {"playwright": True}
        )
        self.assertEqual(
            projects.set_project_mcp(self.work, "context7", False),
            {"playwright": True, "context7": False},
        )
        self.assertEqual(
            projects.project_mcp(self.work), {"playwright": True, "context7": False}
        )
        for name, enabled in (("bad name", True), ("ok", 1), ("ok", "true"), (None, True)):
            with self.assertRaises(ValueError):
                projects.set_project_mcp(self.work, name, enabled)
        self.assertIsNone(projects.set_kit(self.work, "python-data"))
        for bad in ("../x", "Web", "", None):
            with self.assertRaises(ValueError):
                projects.set_kit(self.work, bad)
        view = projects.folder_view(self.work)
        self.assertEqual(
            (view["kit"], view["project_mcp"]),
            ("python-data", {"playwright": True, "context7": False}),
        )
        first, second = "ab" * 32, "cd" * 32
        self.assertIsNone(projects.note_written(self.work, {"AGENTS.md": first}))
        projects.note_written(self.work, {"CLAUDE.md": first, "bad": "nope"})
        projects.note_written(self.work, {"AGENTS.md": second})
        self.assertEqual(
            list(projects.written(self.work).items()),
            [("CLAUDE.md", first), ("AGENTS.md", second)],
        )
        with patch.object(projects, "MAX_WRITTEN", 2):
            projects.note_written(self.work, {"x.md": first})
        self.assertEqual(list(projects.written(self.work)), ["AGENTS.md", "x.md"])
        self.assertEqual(projects.MAX_WRITTEN, 200)


class RegistryTest(ProjectCase):
    def refused(self, conf, name="art"):
        with self.assertRaises(ValueError, msg=repr(conf)) as caught:
            projects.register({"mcpServers": {name: conf}})
        return str(caught.exception)

    def test_register_validates_shapes(self):
        for conf, part in (
            ({"command": "x", "args": "x"}, "art: args has the wrong shape."),
            ({"command": "x", "args": ["a", 1]}, "art: args has the wrong shape."),
            ({"command": "x", "env": []}, "art: env has the wrong shape."),
            ({"command": "x", "env": {"A": 1}}, "art: env has the wrong shape."),
            (
                {"url": "https://h/mcp", "headers": {"A": None}},
                "art: headers has the wrong shape.",
            ),
            ({"command": "x", "cwd": 5}, "art: cwd has the wrong shape."),
            ({"command": "x", "type": "pipe"}, "art: type has the wrong shape."),
            ({"url": "http://example.com/mcp"}, "art: use an https:// URL"),
            ({"url": "https://u:p@h/mcp"}, "art: use an https:// URL"),
            ({"url": "ftp://h/mcp"}, "art: use an https:// URL"),
            ({"url": "https://"}, "art: use an https:// URL"),
            ({"url": "http://[::1"}, "art: use an https:// URL"),
            ({}, "art needs a command or a URL."),
            ({"command": "  "}, "art needs a command or a URL."),
            ({"command": "x\nrm"}, "art needs a command or a URL."),
            ({"command": "x" * 1025}, "art needs a command or a URL."),
            ({"command": 5, "url": "https://h/mcp"}, "art needs a command or a URL."),
            ({"url": 5}, "art needs a command or a URL."),
        ):
            message = self.refused(conf)
            self.assertIn(part, message)
            self.assertTrue(message.endswith("Fix the file and register it again."))
        for name, conf in (
            ("bad name", {"command": "x"}),
            ("desktop", {"command": "x"}),
            ("n" * 49, {"command": "x"}),
            ("art", "text"),
        ):
            self.assertIn("Choose unique server names", self.refused(conf, name))
        for manifest in ({}, {"mcpServers": {}}, {"mcpServers": []}, [], None):
            with self.assertRaisesRegex(ValueError, "nonempty mcpServers object"):
                projects.register(manifest)
        with self.assertRaisesRegex(ValueError, "at most 64 servers"):
            projects.register(
                {"mcpServers": {f"s{n}": {"command": "x"} for n in range(65)}}
            )
        self.assertFalse((self.data / "plugins.json").exists())
        servers = {
            "local": {"url": "http://127.0.0.1:8000/mcp"},
            "six": {"url": "http://[::1]:8000/mcp", "type": "sse"},
            "host": {"url": "http://localhost/mcp", "type": "streamable-http"},
            "remote": {"url": "https://mcp.example.com/mcp", "headers": {"A": "b"}},
            "tool": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "pkg"],
                "env": {"A": "b"},
                "cwd": "/tmp",
                "url": "http://example.com/ignored",
            },
        }
        self.assertEqual(projects.register({"mcpServers": servers}), list(servers))
        self.assertEqual(projects.registry(), servers)

    def test_register_replace_and_unregister(self):
        first = {"command": "fixture", "args": ["serve"]}
        other = {"command": "fixture", "args": ["other"]}
        self.assertEqual(
            projects.register({"mcpServers": {"art": first, "other": other}}),
            ["art", "other"],
        )
        self.assertEqual(projects.register({"mcpServers": {"art": first}}), ["art"])
        session = {"mcpServers": {"art": first, "other": other, "search": {"command": "s"}}}
        files = [
            self.home / ".omp/profiles/workbench-x/agent/mcp.json",
            self.home / ".omp/profiles/local-agent-workbench-1a2b/agent/mcp.json",
            self.data / "clients/slot/pi/mcp.json",
            self.data / "clients/slot/claude-mcp/mcp.json",
        ]
        untouched = [
            self.home / ".omp/profiles/personal/agent/mcp.json",
            self.home / ".omp/profiles/workbench-y/agent/mcp.json",
            self.home / ".omp/profiles/workbench-z/agent/mcp.json",
        ]
        for file in files + untouched:
            file.parent.mkdir(parents=True)
            file.write_text(json.dumps(session), encoding="utf8")
        untouched[1].write_text(json.dumps({"mcpServers": {"other": other}}))
        untouched[2].write_text("{broken")
        history = files[0].parent / "sessions" / "a.jsonl"
        history.parent.mkdir()
        history.write_text("{}\n")

        changed = {"command": "different"}
        with self.assertRaises(ValueError) as caught:
            projects.register({"mcpServers": {"art": changed}})
        self.assertEqual(
            str(caught.exception),
            "A plugin named art is already registered. Use Replace to update it, or rename the new entry.",
        )
        self.assertEqual(projects.registry()["art"], first)
        self.assertEqual(
            projects.register({"mcpServers": {"art": changed}}, replace=True), ["art"]
        )
        self.assertEqual(projects.registry(), {"art": changed, "other": other})
        for file in files:
            self.assertEqual(
                sorted(json.loads(file.read_text())["mcpServers"]), ["other", "search"]
            )
        self.assertIn("art", json.loads(untouched[0].read_text())["mcpServers"])

        for file in files:
            file.write_text(json.dumps(session), encoding="utf8")
        runtime.write_json(
            self.data / "settings.json",
            {
                "plugins": "art,other",
                "context": 4096,
                "named_profiles": {
                    "Work": {"plugins": "art", "context": 8192},
                    "Play": {"plugins": "other"},
                },
            },
        )
        projects.remember(self.work, {"plugins": "other,art", "harness": "Pi"})
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        projects.remember(elsewhere, {"plugins": "other"})

        result = projects.dispatch("plugin-remove", {"name": "art"})
        self.assertEqual(
            result, {"removed": "art", "settings": True, "folders": 1, "files": 4}
        )
        self.assertEqual(projects.registry(), {"other": other})
        self.assertEqual(
            json.loads((self.data / "settings.json").read_text()),
            {
                "plugins": "other",
                "context": 4096,
                "named_profiles": {
                    "Work": {"plugins": "", "context": 8192},
                    "Play": {"plugins": "other"},
                },
            },
        )
        self.assertEqual(
            self.record()["recipe"], {"harness": "Pi", "plugins": "other"}
        )
        self.assertEqual(self.record(elsewhere)["recipe"], {"plugins": "other"})
        for file in files:
            self.assertEqual(
                sorted(json.loads(file.read_text())["mcpServers"]), ["other", "search"]
            )
        self.assertIn("art", json.loads(untouched[0].read_text())["mcpServers"])
        self.assertEqual(untouched[2].read_text(), "{broken")
        self.assertEqual(history.read_text(), "{}\n")

        with self.assertRaises(ValueError) as caught:
            projects.unregister("art")
        self.assertEqual(
            str(caught.exception),
            "That plugin is no longer registered. Refresh the Plugins page.",
        )
        self.assertEqual(
            projects.unregister("other"),
            {"removed": "other", "settings": True, "folders": 2, "files": 5},
        )
        self.assertEqual(projects.registry(), {})

    def test_unregister_tolerates_missing_and_damaged_settings(self):
        self.plugins("art", "other")
        self.assertEqual(
            projects.unregister("art"),
            {"removed": "art", "settings": False, "folders": 0, "files": 0},
        )
        (self.data / "settings.json").write_text("{broken")
        self.assertFalse(projects.unregister("other")["settings"])
        self.assertEqual((self.data / "settings.json").read_text(), "{broken")

    def test_damaged_registry_is_one_sentence_and_is_never_overwritten(self):
        file = self.data / "plugins.json"
        sentence = (
            f"The plugin registry at {file} could not be read; "
            "move that file aside, then register your MCP files again in Plugins."
        )
        self.put({"p": self.profile(plugins="art")})
        for content in ("{broken", "[]", '"text"', "[" * 100000):
            file.write_text(content)
            for call in (
                projects.registry,
                projects.describe,
                lambda: projects.register({"mcpServers": {"art": {"command": "x"}}}),
                lambda: projects.register({"mcpServers": {"art": {"command": "x"}}}, True),
                lambda: projects.unregister("art"),
                lambda: projects.preview_project(self.s, "p", []),
            ):
                with self.assertRaises(ValueError) as caught:
                    call()
                self.assertIs(type(caught.exception), ValueError)
                self.assertEqual(str(caught.exception), sentence)
            self.assertEqual(file.read_text(), content)
        with patch.object(projects, "load", side_effect=PermissionError(13, "denied")):
            with self.assertRaises(ValueError) as caught:
                projects.registry()
        self.assertEqual(str(caught.exception), sentence)
        file.unlink()
        self.assertEqual(projects.registry(), {})

    def test_damaged_registry_marks_remembered_plugins_stale(self):
        self.plugins("art")
        projects.remember(self.work, {"plugins": "art", "harness": "Pi"})
        (self.data / "plugins.json").write_text("[]")
        view = projects.folder_view(self.work)
        self.assertEqual((view["recipe"], view["stale"]), ({"harness": "Pi"}, ["plugins"]))

    def test_plugin_ids_ignores_a_value_that_is_not_text(self):
        self.assertEqual(projects.plugin_ids({"plugins": "a,,b"}), ["a", "b"])
        for odd in (None, 5, ["a"], {"a": 1}, True):
            self.assertEqual(projects.plugin_ids({"plugins": odd}), [], repr(odd))
        self.assertEqual(projects.plugin_ids({}), [])

    def test_removed_plugin_blocks_a_profile_by_name(self):
        self.plugins("art")
        self.put({"p": self.profile(plugins="art")})
        self.assertEqual(projects.restore_project(self.s, "p", [])["plugins"], "art")
        projects.unregister("art")
        with self.assertRaisesRegex(ValueError, "in Plugins first: art."):
            projects.restore_project(self.s, "p", [])

    def test_update_reads_a_manifest_file(self):
        file = self.tmp / "servers.json"
        file.write_text(json.dumps({"mcpServers": {"art": {"command": "one"}}}))
        self.assertEqual(projects.dispatch("plugin-update", {"file": str(file)}), ["art"])
        file.write_text(json.dumps({"mcpServers": {"art": {"command": "two"}}}))
        self.assertEqual(projects.dispatch("plugin-update", {"file": str(file)}), ["art"])
        self.assertEqual(projects.registry(), {"art": {"command": "two"}})
        for content, part in (
            (b"{broken", "not valid JSON"),
            (b"[]", "not valid JSON"),
            (b"\xff\xfe", "not valid JSON"),
            (b" " * (projects.MAX_BYTES + 1), "larger than 1 MB"),
        ):
            file.write_bytes(content)
            with self.assertRaises(ValueError) as caught:
                projects.load_manifest(file)
            self.assertIn(part, str(caught.exception))
            self.assertTrue(
                str(caught.exception).endswith("Choose an MCP configuration file.")
            )
        with self.assertRaisesRegex(ValueError, "could not be read"):
            projects.load_manifest(self.tmp / "missing.json")
        with self.assertRaisesRegex(ValueError, "Unknown project action."):
            projects.dispatch("plugin-install", {})

    def test_describe_hides_values(self):
        runtime.write_json(
            self.data / "plugins.json",
            {
                "remote": {
                    "type": "http",
                    "url": "https://user:hunter2@mcp.example.com:8443/mcp?token=tok-123#frag",
                    "headers": {"X-Workspace": "ws-secret", "Authorization": "Bearer tok-456"},
                },
                "events": {"type": "sse", "url": "http://[::1]:9000/sse"},
                "art": {
                    "command": "npx",
                    "args": ["-y", "art-mcp"],
                    "env": {"TOKEN": "s3cret", "API_KEY": "k-789"},
                },
                "damaged": "text",
            },
        )
        rows = projects.dispatch("plugin-describe", {})
        self.assertEqual(
            rows,
            [
                {
                    "name": "art",
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "art-mcp"],
                    "env_keys": ["API_KEY", "TOKEN"],
                    "url": "",
                    "header_keys": [],
                },
                {
                    "name": "events",
                    "transport": "sse",
                    "command": "",
                    "args": [],
                    "env_keys": [],
                    "url": "http://[::1]:9000/sse",
                    "header_keys": [],
                },
                {
                    "name": "remote",
                    "transport": "http",
                    "command": "",
                    "args": [],
                    "env_keys": [],
                    "url": "https://mcp.example.com:8443/mcp",
                    "header_keys": ["Authorization", "X-Workspace"],
                },
            ],
        )
        text = json.dumps(rows)
        for secret in ("s3cret", "k-789", "ws-secret", "tok-", "hunter2", "frag"):
            self.assertNotIn(secret, text)


GPU_BOX = dict(provider="Attached endpoint", name="GPU box")


class FolderDefaultTest(ProjectCase):
    def saved(self):
        return json.loads(
            (self.work / ".agent-workbench" / "profiles.json").read_text(encoding="utf8")
        )

    def test_old_file_without_a_default_still_works(self):
        self.put(OLD_FILE)
        self.assertEqual(
            projects.project_list(self.work),
            {"names": ["Research"], "default": "", "problem": ""},
        )
        self.assertEqual(projects.folder_default(self.work), "")
        view = projects.folder_view(self.work)
        self.assertEqual((view["saved_default"], view["offer_default"]), ("", False))
        self.assertEqual(projects.project_names(self.work), ["Research"])

    def test_save_marks_one_default_and_replacing_keeps_it(self):
        projects.save_project(self.s, "A")
        projects.save_project(self.s, "B", default=True)
        rows = self.saved()
        self.assertEqual(list(rows["B"])[0], "default")
        self.assertIs(rows["B"]["default"], True)
        self.assertNotIn("default", rows["A"])
        projects.save_project(self.s, "A", default=True)
        self.assertEqual(projects.folder_default(self.work), "A")
        self.assertNotIn("default", self.saved()["B"])
        projects.save_project(self.s | {"context": 8192}, "A")
        rows = self.saved()
        self.assertIs(rows["A"]["default"], True)
        self.assertEqual(rows["A"]["context"], 8192)
        self.assertEqual(list(rows), ["A", "B"])
        self.assertEqual(projects.project_list(self.work)["default"], "A")

    def test_set_and_clear_default(self):
        self.assertEqual(
            projects.clear_default(self.work),
            {"names": [], "default": "", "problem": ""},
        )
        self.assertFalse((self.work / ".agent-workbench").exists())
        with self.assertRaisesRegex(ValueError, "^This profile no longer exists"):
            projects.set_default(self.work, "a")
        self.put({"a": {"context": 4096}, "b": {"default": False, "context": 8192}})
        self.assertEqual(projects.set_default(self.work, "b")["default"], "b")
        self.assertEqual(self.saved(), {"a": {"context": 4096}, "b": {"default": True, "context": 8192}})
        self.assertEqual(projects.set_default(self.work, "a")["default"], "a")
        self.assertEqual(self.saved(), {"a": {"default": True, "context": 4096}, "b": {"context": 8192}})
        with self.assertRaisesRegex(ValueError, "^This profile no longer exists"):
            projects.set_default(self.work, "gone")
        self.assertEqual(projects.clear_default(self.work)["default"], "")
        self.assertEqual(self.saved(), {"a": {"context": 4096}, "b": {"context": 8192}})
        self.assertTrue(projects.edit_profile(self.work, "a", "remove"))
        self.assertEqual(projects.set_default(self.work, "b")["default"], "b")
        self.assertTrue(projects.edit_profile(self.work, "b", "rename", "c"))
        self.assertEqual(projects.folder_default(self.work), "c")

    def test_two_defaults_give_a_clear_message(self):
        self.put(
            {
                "a": self.profile(default=True),
                "b": self.profile(default=True),
                "c": self.profile(),
            }
        )
        sentence = (
            ".agent-workbench/profiles.json marks more than one profile as the folder default: "
            "'a' and 'b'. Choose Make default on the one to keep in Project profiles."
        )
        with self.assertRaises(ValueError) as caught:
            projects.folder_default(self.work)
        self.assertEqual(str(caught.exception), sentence)
        listed = projects.project_list(self.work)
        self.assertEqual(listed, {"names": ["a", "b", "c"], "default": "", "problem": sentence})
        view = projects.folder_view(self.work)
        self.assertEqual((view["saved_default"], view["offer_default"]), ("", False))
        self.assertEqual(projects.preview_project(self.s, "a", [])["blockers"], [])
        self.assertEqual(projects.set_default(self.work, "b")["problem"], "")
        self.assertEqual(projects.folder_default(self.work), "b")

    def test_default_must_be_true_or_false(self):
        self.put({"a": self.profile(default="yes")})
        with self.assertRaisesRegex(ValueError, "does not accept for Folder default"):
            projects.validate_profile({"default": "yes"}, "a")
        with self.assertRaisesRegex(ValueError, "does not accept for Folder default"):
            projects.preview_project(self.s, "a", [])
        self.assertIn("Folder default", projects.project_list(self.work)["problem"])
        self.assertEqual(projects.validate_profile({"default": False, "context": 4096}), {"context": 4096})
        projects.set_default(self.work, "a")
        self.assertIs(self.saved()["a"]["default"], True)

    def test_marking_the_default_does_not_void_a_review(self):
        self.put({"a": self.profile(context=4096), "b": self.profile()})
        before = projects.preview_project(self.s, "a", [])["digest"]
        projects.set_default(self.work, "a")
        self.assertEqual(projects.preview_project(self.s, "a", [])["digest"], before)
        self.assertEqual(projects.apply_project(self.s, "a", [], before)["context"], 4096)

    def test_notice_needs_a_default_and_no_remembered_choices(self):
        self.assertFalse(projects.folder_view(self.work)["offer_default"])
        self.put({"Shared": self.profile(context=4096, default=True)})
        view = projects.folder_view(self.work)
        self.assertEqual(
            (view["known"], view["saved_default"], view["offer_default"]),
            (False, "Shared", True),
        )
        self.assertFalse((self.data / "projects.json").exists())
        view = projects.remember(self.work, self.s | {"harness": "Codex"})
        self.assertEqual(
            (view["known"], view["saved_default"], view["offer_default"]),
            (True, "Shared", False),
        )
        other = self.tmp / "other"
        other.mkdir()
        self.put({"Shared": self.profile(default=True)}, other)
        projects.set_kit(other, "web")
        self.assertFalse(projects.folder_view(other)["offer_default"])

    def test_dismissal_is_remembered_per_folder_and_per_default(self):
        other = self.tmp / "other"
        other.mkdir()
        for folder in (self.work, other):
            self.put({"Shared": self.profile(default=True), "Next": self.profile()}, folder)
        self.assertEqual(
            projects.dispatch("folder-default-dismiss", {"workspace": str(self.work)})[
                "offer_default"
            ],
            False,
        )
        self.assertEqual(
            set(self.record()), {"path", "updated", "default_notice"}
        )
        self.assertEqual(self.record()["default_notice"], "Shared")
        view = projects.folder_view(self.work)
        self.assertEqual(
            (view["known"], view["saved_default"], view["recipe"]), (False, "Shared", {})
        )
        self.assertTrue(projects.folder_view(other)["offer_default"])
        # A default marked on this computer is not offered back to it.
        projects.set_default(self.work, "Next")
        self.assertFalse(projects.folder_view(self.work)["offer_default"])
        self.assertTrue(projects.forget(self.work))
        projects.set_default(self.work, "Shared")
        self.assertFalse(projects.folder_view(self.work)["offer_default"])
        # A default changed by someone else (a pull, another computer) is offered again.
        self.put({"Shared": self.profile(), "Next": self.profile(default=True)}, self.work)
        self.assertTrue(projects.folder_view(self.work)["offer_default"])
        empty = self.tmp / "empty"
        empty.mkdir()
        self.assertFalse(projects.dismiss_default(empty)["offer_default"])
        self.assertNotIn(
            paths.project_key(empty),
            json.loads((self.data / "projects.json").read_text())["folders"],
        )

    def test_default_saved_on_one_computer_applies_on_another(self):
        # Computer A: an attached GPU box and a local copy of the weights.
        self.accounts[:] = [
            GPU_BOX | dict(connection_id="a-111", endpoint="http://10.0.0.5:8080/v1")
        ]
        on_a = self.s | dict(
            harness="Claude Code",
            provider="Attached endpoint",
            connection_id="a-111",
            remote_model="qwen3-coder-30b",
            model="/machine-a/models/qwen.gguf",
            context=65536,
        )
        projects.save_project(on_a, projects.DEFAULT_NAME, default=True)
        projects.save_project(
            on_a | dict(provider="Managed local", connection_id=""), "Local"
        )
        projects.remember(self.work, on_a)
        text = (self.work / ".agent-workbench" / "profiles.json").read_text(encoding="utf8")
        for local in ("a-111", "10.0.0.5", "/machine-a", str(self.data)):
            self.assertNotIn(local, text)

        # Computer B: its own data folder, a GPU box with another id and address, weights elsewhere.
        data_b = self.tmp / "data-b"
        data_b.mkdir()
        library = self.tmp / "library-b"
        library.mkdir()
        (library / "qwen.gguf").touch()
        moved = patch.object(projects, "DATA", data_b)
        moved.start()
        self.addCleanup(moved.stop)
        self.accounts[:] = [
            GPU_BOX | dict(connection_id="b-222", endpoint="http://127.0.0.1:18080/v1")
        ]
        view = projects.folder_view(self.work)
        self.assertEqual(
            (view["known"], view["saved_default"], view["offer_default"]),
            (False, "Folder default", True),
        )
        with self.assertRaisesRegex(ValueError, "Review what this profile changes"):
            projects.apply_project(self.s, "Folder default", [], "")
        self.assertFalse((data_b / "projects.json").exists())
        preview = projects.preview_project(self.s, "Folder default", [])
        self.assertEqual(preview["blockers"], [])
        changed = {row["field"]: row["to"] for row in preview["changes"]}
        self.assertEqual(
            (changed["harness"], changed["provider"], changed["connection"]),
            ("Claude Code", "Attached endpoint", "GPU box"),
        )
        out = projects.apply_project(self.s, "Folder default", [], preview["digest"])
        self.assertEqual(
            (out["connection_id"], out["endpoint"], out["remote_model"], out["context"]),
            ("b-222", "http://127.0.0.1:18080/v1", "qwen3-coder-30b", 65536),
        )
        view = projects.folder_view(self.work)
        self.assertEqual(view["recipe"]["connection_id"], "b-222")
        self.assertEqual((view["known"], view["offer_default"]), (True, False))
        weights = [str(library / "qwen.gguf")]
        local = projects.preview_project(self.s, "Local", weights)
        self.assertEqual(local["blockers"], [])
        self.assertEqual(
            projects.restore_project(self.s, "Local", weights)["model"], weights[0]
        )

    def test_missing_pieces_on_the_new_computer_are_named(self):
        self.put(
            {
                "Folder default": self.profile(
                    provider="Attached endpoint",
                    connection_name="GPU box",
                    default=True,
                ),
                "Local": self.profile(weight="qwen.gguf"),
            }
        )
        preview = projects.preview_project(self.s, "Folder default", [])
        self.assertEqual(
            preview["blockers"],
            ["Add an Attached endpoint connection named GPU box in Connections, then apply this profile."],
        )
        self.assertEqual(
            preview["needs"], {"connection": {"provider": "Attached endpoint", "name": "GPU box"}}
        )
        self.assertEqual(
            projects.preview_project(self.s, "Local", [])["blockers"],
            ["Add qwen.gguf to your model library before applying this profile."],
        )
        self.assertFalse((self.data / "projects.json").exists())

    def test_folder_files_and_records_hold_no_secrets(self):
        secrets = ("pa55word", "sk-live-123", "host.invalid", "conn-secret-id", "tok=abc")
        self.accounts.append(
            dict(
                provider="OpenRouter",
                name="Lab",
                connection_id="conn-secret-id",
                endpoint="https://user:pa55word@host.invalid/v1?tok=abc",
            )
        )
        s = self.s | dict(
            provider="OpenRouter",
            connection_id="conn-secret-id",
            endpoint="https://user:pa55word@host.invalid/v1?tok=abc",
            remote_model="vendor/model",
            model="/private/models/x.gguf",
        )
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-live-123"}):
            projects.save_project(s, projects.DEFAULT_NAME, default=True)
            projects.dismiss_default(self.work)
            rows = projects.places(self.work)
        files = [
            (self.work / ".agent-workbench" / "profiles.json").read_text(encoding="utf8"),
            (self.data / "projects.json").read_text(encoding="utf8"),
            json.dumps(rows),
            json.dumps(projects.folder_view(self.work)),
            json.dumps(projects.project_list(self.work)),
        ]
        for text in files:
            for secret in (*secrets, "/private/models"):
                self.assertNotIn(secret, text)
        self.assertEqual(self.saved()["Folder default"]["connection_name"], "Lab")

    def test_places_lists_what_travels_and_what_stays(self):
        rows = projects.dispatch("folder-places", {"workspace": str(self.work)})
        self.assertEqual(
            [(row["id"], row["travels"]) for row in rows],
            [
                ("profiles", True),
                ("project", True),
                ("instructions", True),
                ("skills", True),
                ("memory", False),
                ("connections", False),
                ("runs", False),
                ("keys", False),
            ],
        )
        where = {row["id"]: row for row in rows}
        self.assertEqual(
            Path(where["profiles"]["path"]), self.work / ".agent-workbench" / "profiles.json"
        )
        self.assertEqual(Path(where["memory"]["path"]), self.data / "projects.json")
        self.assertEqual(Path(where["connections"]["path"]), self.data / "connections.json")
        import run_log

        with patch.object(runtime, "DATA", self.data):
            self.assertEqual(Path(where["runs"]["path"]), run_log.run_file(self.work))
        self.assertEqual((where["keys"]["path"], where["keys"]["exists"]), ("", False))
        self.assertIn("holds no keys", where["profiles"]["note"])
        self.assertFalse(where["profiles"]["exists"])
        projects.save_project(self.s, "a")
        self.assertTrue(projects.places(self.work)[0]["exists"])
        with self.assertRaises(ValueError):
            projects.places(str(self.work / "gone"))


if __name__ == "__main__":
    unittest.main()
