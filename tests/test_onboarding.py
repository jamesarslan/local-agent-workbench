"""Guided setup state and completion tracking: every source is a fake, every file lives in a temporary folder."""

import atexit
import contextlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

if "runtime" not in sys.modules and "WORKBENCH_DATA" not in os.environ:
    os.environ["WORKBENCH_DATA"] = tempfile.mkdtemp(prefix="awb-test-")
    atexit.register(shutil.rmtree, os.environ["WORKBENCH_DATA"], ignore_errors=True)

import catalog
import onboarding

FIXED = {"schema", "steps", "progress", "workspace"}
ROW_KEYS = {"name", "exe", "path", "installed", "on_path", "hint", "version"}
STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
HARDWARE = {"schema": 1, "backend": "metal", "label": "Apple M3 Max · 48 GiB unified memory"}
RECOMMENDATION = {"pick": None, "alternatives": [], "reason": "No catalog model fits.", "warnings": []}
ENDPOINTS = {"endpoints": [], "hints": []}
ENGINE = {"backend": "metal", "engine_ready": False, "engines": ["Auto", "llama.cpp", "Custom"]}
KIT = {"suggested": "general", "alternatives": [], "evidence": {}, "scanned": 3, "truncated": False}
CONNECTION = {"connection_id": "legacy-OpenRouter", "name": "OpenRouter", "provider": "OpenRouter", "ready": True}


def module(name, **members):
    made = types.ModuleType(name)
    for key, value in members.items():
        setattr(made, key, value)
    return made


def cli_row(name, path):
    return dict(name=name, exe=name.lower(), path=path, installed=bool(path), on_path=True, hint="")


def inventory(names):
    return [cli_row(name, f"/bin/{name.lower()}") for name in names]


def fakes(**replaced):
    made = dict(
        endpoints=module("endpoints", scan=Mock(return_value=ENDPOINTS)),
        setup_engines=module("setup_engines", info=Mock(return_value=ENGINE)),
        starter_kits=module("starter_kits", detect=Mock(return_value=KIT)),
        account_book=module("account_book", available=Mock(return_value=[dict(CONNECTION)])),
        machines=module("machines", labels=Mock(return_value={"dev@gpu-box": "GPU box"})),
        tooling=module(
            "tooling",
            AGENTS=("OMP", "Pi"),
            TOOLS={"OMP": ("omp",), "Pi": ("pi",)},
            inventory=Mock(side_effect=inventory),
            version=Mock(return_value="1.2.3"),
        ),
        connections=module("connections", HARNESS=["OMP", "Pi"]),
    )
    made.update(replaced)
    return made


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.data = self.tmp / "data"
        self.data.mkdir()
        self.library = self.tmp / "models"
        self.enterContext(patch.object(onboarding, "DATA", self.data))

    def file(self):
        return self.data / "onboarding.json"

    @contextlib.contextmanager
    def sources(self, **replaced):
        mods = fakes(**replaced)
        weights = str(self.library / "org--repo" / "Small-Q4_K_M.gguf")
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, mods))
            mods["hardware_info"] = stack.enter_context(
                patch.object(catalog, "hardware_info", Mock(return_value=dict(HARDWARE)))
            )
            mods["recommendation"] = stack.enter_context(
                patch.object(catalog, "recommendation", Mock(return_value=dict(RECOMMENDATION)))
            )
            mods["library_rows"] = stack.enter_context(
                patch.object(catalog, "library_rows", Mock(return_value=[(weights, [weights], 3 * catalog.GIB)]))
            )
            stack.enter_context(patch.object(onboarding, "owned", Mock(return_value=(None, {}))))
            stack.enter_context(patch.object(onboarding, "settings", Mock(return_value={"library": str(self.library)})))
            yield mods


class StateTests(Base):
    def test_state_has_every_section_as_envelope(self):
        with self.sources():
            out = onboarding.state(str(self.tmp))
        self.assertEqual(set(out), FIXED | set(onboarding.SECTIONS))
        self.assertEqual(len(onboarding.SECTIONS), 10)
        for name in onboarding.SECTIONS:
            self.assertEqual(out[name]["ok"], "value" in out[name], name)
            self.assertEqual(set(out[name]) - {"ok"}, {"value"} if out[name]["ok"] else {"error"}, name)
            self.assertTrue(out[name]["ok"], name)
        self.assertEqual(out["schema"], 1)
        self.assertEqual(out["steps"], list(onboarding.STEPS))
        self.assertEqual(out["progress"]["next_step"], "computer")
        self.assertEqual(out["workspace"], {"path": str(self.tmp), "exists": True})
        self.assertEqual(out["hardware"]["value"], HARDWARE)
        self.assertEqual(out["recommendation"]["value"], RECOMMENDATION)
        self.assertEqual(out["endpoints"]["value"], ENDPOINTS)
        self.assertEqual(out["engine"]["value"], ENGINE)
        self.assertEqual(out["kit"]["value"], KIT)
        self.assertEqual(out["connections"]["value"], [CONNECTION])
        self.assertEqual(out["machines"]["value"], [{"host": "dev@gpu-box", "label": "GPU box"}])
        self.assertEqual(out["managed"]["value"], {"running": False, "model": "", "context": None})
        self.assertEqual([row["version"] for row in out["clis"]["value"]], ["1.2.3", "1.2.3"])
        json.dumps(out)

    def test_library_section_lists_at_most_fifty_models(self):
        rows = [(str(self.library / f"m{n:02}.gguf"), [], n * catalog.GIB // 4) for n in range(60)]
        with self.sources() as mods:
            mods["library_rows"].return_value = rows
            value = onboarding.state(sections=["library"])["library"]["value"]
        mods["library_rows"].assert_called_once_with(str(self.library))
        self.assertEqual((value["folder"], value["count"], len(value["models"])), (str(self.library), 60, 50))
        self.assertEqual(value["models"][2], {"path": rows[2][0], "name": "m02.gguf", "gib": 0.5})

    def test_managed_section_describes_the_running_model(self):
        cases = [
            ({"engine": "llama.cpp", "model": "/models/a/Qwen-Q4.gguf", "context": 32768}, "Qwen-Q4.gguf", 32768),
            ({"engine": "vLLM", "model": "/models/a/old.gguf", "vllm_model": "org/served", "context": 8192}, "org/served", 8192),
            ({"engine": "Custom", "model": "", "vllm_model": "", "context": "big"}, "", None),
            ("damaged", "", None),
        ]
        for spec, model, context in cases:
            with self.sources(), patch.object(onboarding, "owned", Mock(return_value=(object(), {"spec": spec}))):
                out = onboarding.state(sections=["managed"])["managed"]
            self.assertEqual(out, {"ok": True, "value": {"running": True, "model": model, "context": context}})

    def test_failed_section_is_isolated(self):
        sentence = "Start Ollama, then choose Check again."
        with self.sources(
            endpoints=module("endpoints", scan=Mock(side_effect=ValueError(sentence))),
            setup_engines=module("setup_engines", info=Mock(side_effect=RuntimeError("boom"))),
            machines=module("machines", labels=Mock(side_effect=KeyError("hosts"))),
            account_book=module("account_book", available=Mock(side_effect=ValueError())),
        ):
            out = onboarding.state(str(self.tmp))
        self.assertEqual(out["endpoints"], {"ok": False, "error": sentence})
        self.assertEqual(out["engine"], {"ok": False, "error": onboarding.FAILED})
        self.assertEqual(out["machines"], {"ok": False, "error": onboarding.FAILED})
        self.assertEqual(out["connections"], {"ok": False, "error": onboarding.FAILED})
        for name in set(onboarding.SECTIONS) - {"endpoints", "engine", "machines", "connections"}:
            self.assertTrue(out[name]["ok"], name)

    def test_section_whose_module_cannot_be_imported_costs_one_section(self):
        with self.sources(account_book=None):
            out = onboarding.state(str(self.tmp))
        self.assertEqual(out["connections"], {"ok": False, "error": onboarding.FAILED})
        self.assertTrue(all(out[name]["ok"] for name in onboarding.SECTIONS if name != "connections"))

    def test_slow_section_times_out(self):
        release = threading.Event()
        self.addCleanup(release.set)
        started = time.monotonic()
        out = onboarding.gather(
            {"slow": lambda: release.wait(1), "quick": lambda: 1, "other": lambda: "two"}, deadline=0.2
        )
        spent = time.monotonic() - started
        self.assertEqual(out["slow"], {"ok": False, "error": onboarding.SLOW})
        self.assertEqual(out["quick"], {"ok": True, "value": 1})
        self.assertEqual(out["other"], {"ok": True, "value": "two"})
        self.assertLess(spent, 0.6)
        self.assertGreaterEqual(spent, 0.2)
        self.assertEqual(onboarding.gather({}), {})

    def test_missing_provider_is_unavailable(self):
        unavailable = {"ok": False, "error": onboarding.UNAVAILABLE}
        with self.sources(), patch.dict(onboarding.PROVIDERS, {"kit": ("awb_no_such_module", "detect")}):
            out = onboarding.state(str(self.tmp))
        self.assertEqual(out["kit"], unavailable)
        self.assertTrue(all(out[name]["ok"] for name in onboarding.SECTIONS if name != "kit"))
        with self.sources(starter_kits=module("starter_kits"), endpoints=module("endpoints")):
            out = onboarding.state(str(self.tmp))
        self.assertEqual(out["kit"], unavailable)
        self.assertEqual(out["endpoints"], unavailable)
        self.assertTrue(out["engine"]["ok"])

    def test_lookup_error_inside_a_provider_is_a_failure_not_a_missing_part(self):
        with self.sources(endpoints=module("endpoints", scan=Mock(side_effect=KeyError("port")))):
            out = onboarding.state(sections=["endpoints"])
        self.assertEqual(out["endpoints"], {"ok": False, "error": onboarding.FAILED})

    def test_damaged_file_behind_a_section_gives_the_plain_sentence(self):
        failed = {"ok": False, "error": onboarding.FAILED}
        with (
            self.sources(machines=module("machines", labels=lambda: json.loads("{not json"))),
            patch.object(onboarding, "settings", Mock(side_effect=lambda: b"\xff\xfe".decode("utf8"))),
        ):
            out = onboarding.state(sections=["machines", "library"])
        self.assertEqual(out["machines"], failed)
        self.assertEqual(out["library"], failed)

    def test_machine_without_a_text_label_is_shown_by_its_host(self):
        saved = {"dev@gpu-box": "GPU box", "nas": "", "lab": 5, "rack": {"label": "x"}}
        with self.sources(machines=module("machines", labels=Mock(return_value=saved))):
            out = onboarding.state(sections=["machines"])["machines"]
        self.assertEqual(
            out["value"],
            [
                {"host": "dev@gpu-box", "label": "GPU box"},
                {"host": "nas", "label": "nas"},
                {"host": "lab", "label": "lab"},
                {"host": "rack", "label": "rack"},
            ],
        )

    def test_folder_that_does_not_answer_is_held_to_the_deadline(self):
        release = threading.Event()
        self.addCleanup(release.set)
        stuck = "//offline-host/share"
        real = os.path.isdir

        def isdir(path):
            return release.wait(2) and False if path == stuck else real(path)

        with (
            self.sources() as mods,
            patch.object(onboarding, "DEADLINE", 0.2),
            patch.object(onboarding.os.path, "isdir", isdir),
        ):
            started = time.monotonic()
            out = onboarding.state(stuck, sections=["kit", "managed"])
            spent = time.monotonic() - started
            release.set()
        self.assertLess(spent, 0.8)
        self.assertEqual(out["workspace"], {"path": stuck, "exists": False})
        self.assertEqual(out["kit"], {"ok": False, "error": onboarding.SLOW})
        self.assertTrue(out["managed"]["ok"])
        self.assertEqual(set(out), FIXED | {"managed", "kit"})
        mods["starter_kits"].detect.assert_not_called()

    def test_clis_with_inventory(self):
        probed = []

        def rows(names):
            return [cli_row(name, "" if name == "Pi" else f"/bin/{name.lower()}") for name in names]

        def version(path):
            probed.append(path)
            time.sleep(0.2)
            return "9.9.9" if path == "/bin/codex" else "19.1.0"

        tooling = module(
            "tooling", AGENTS=("OMP", "Pi", "Codex", "Hermes"), TOOLS={}, inventory=Mock(side_effect=rows), version=version
        )
        with self.sources(tooling=tooling, connections=None):
            started = time.monotonic()
            out = onboarding.state(sections=["clis"])["clis"]
            spent = time.monotonic() - started
        tooling.inventory.assert_called_once_with(["OMP", "Pi", "Codex", "Hermes"])
        self.assertTrue(out["ok"])
        value = out["value"]
        self.assertEqual([row["name"] for row in value], ["OMP", "Pi", "Codex", "Hermes"])
        self.assertTrue(all(set(row) == ROW_KEYS for row in value))
        self.assertEqual([row["version"] for row in value], ["19.1.0", "", "9.9.9", "19.1.0"])
        self.assertEqual(value[1], dict(cli_row("Pi", ""), version=""))
        self.assertEqual(sorted(probed), ["/bin/codex", "/bin/hermes", "/bin/omp"])
        self.assertLess(spent, 0.5)

    def test_clis_version_probe_that_fails_leaves_the_version_blank(self):
        tooling = module(
            "tooling", AGENTS=("OMP",), TOOLS={}, inventory=Mock(side_effect=inventory), version=Mock(side_effect=OSError("gone"))
        )
        with self.sources(tooling=tooling):
            out = onboarding.state(sections=["clis"])["clis"]
        self.assertEqual(out, {"ok": True, "value": [dict(cli_row("OMP", "/bin/omp"), version="")]})

    def test_clis_fallback(self):
        tooling = module("tooling", TOOLS={"OMP": ("omp", None), "Pi": ("pi", None), "Node.js": ("node", None)})
        found = {"omp": "/opt/bin/omp"}
        with (
            self.sources(tooling=tooling, connections=module("connections", HARNESS=["OMP", "Pi", "Zed"])),
            patch.object(onboarding.shutil, "which", side_effect=found.get) as which,
        ):
            out = onboarding.state(sections=["clis"])["clis"]
        self.assertEqual(
            out["value"],
            [
                dict(name="OMP", exe="omp", path="/opt/bin/omp", installed=True, on_path=True, hint="", version=""),
                dict(name="Pi", exe="pi", path="", installed=False, on_path=True, hint="", version=""),
            ],
        )
        self.assertTrue(all(set(row) == ROW_KEYS for row in out["value"]))
        self.assertEqual(which.call_args_list, [call("omp"), call("pi")])

    def test_kit_needs_existing_folder(self):
        with self.sources() as mods:
            detect = mods["starter_kits"].detect
            for workspace in ("", str(self.tmp / "missing"), str(self.file()), None, 7):
                out = onboarding.state(workspace, sections=["kit"])
                self.assertEqual(out["kit"], {"ok": True, "value": None}, workspace)
                self.assertFalse(out["workspace"]["exists"], workspace)
            self.assertEqual(out["workspace"], {"path": "", "exists": False})
            detect.assert_not_called()
            link = self.tmp / "link"
            try:
                link.symlink_to(self.data, target_is_directory=True)
            except OSError:
                link = self.data
            out = onboarding.state(str(link), sections=["kit"])
        detect.assert_called_once_with(str(self.data))
        self.assertEqual(out["kit"], {"ok": True, "value": KIT})
        self.assertEqual(out["workspace"], {"path": str(link), "exists": True})

    def test_sections_filter_and_refresh(self):
        order = []
        with self.sources() as mods:
            mods["hardware_info"].side_effect = lambda *args: order.append(("hardware", args)) or dict(HARDWARE)
            mods["recommendation"].side_effect = lambda *args: order.append(("recommendation", args)) or {}
            out = onboarding.state(sections=["hardware", "bogus"])
            self.assertEqual(set(out), FIXED | {"hardware"})
            self.assertEqual(order, [("hardware", ())])
            self.assertEqual(set(onboarding.state(sections=[])), FIXED)
            self.assertEqual(set(onboarding.state(sections="hardware")), FIXED | set(onboarding.SECTIONS))
            order.clear()
            out = onboarding.state(sections=["recommendation", "hardware"], refresh=True)
        self.assertEqual(list(out)[4:], ["hardware", "recommendation"])
        self.assertEqual(order[0], ("hardware", (True,)))
        self.assertEqual(sorted(order[1:]), [("hardware", ()), ("recommendation", ())])
        self.assertEqual(mods["hardware_info"].call_args_list.count(call(True)), 1)

    def test_check_again_rescans_the_model_folder(self):
        self.library.mkdir()
        self.addCleanup(catalog.library_rows.cache_clear)
        catalog.library_rows.cache_clear()

        def count(**more):
            return onboarding.state(sections=["library"], **more)["library"]["value"]["count"]

        with (
            patch.object(catalog, "hardware_info", Mock(return_value=dict(HARDWARE))),
            patch.object(onboarding, "settings", Mock(return_value={"library": str(self.library)})),
        ):
            self.assertEqual(count(), 0)
            (self.library / "Added-Q4_K_M.gguf").write_bytes(b"x")
            self.assertEqual(count(), 0)
            onboarding.state(sections=["hardware"], refresh=True)
            self.assertEqual(count(), 0)
            self.assertEqual(count(refresh=True), 1)
        with self.sources() as mods:
            clear = mods["library_rows"].cache_clear
            onboarding.state(sections=["hardware", "kit"], refresh=True)
            onboarding.state()
            clear.assert_not_called()
            onboarding.state(refresh=True)
            clear.assert_called_once_with()


class ProgressTests(Base):
    def test_first_run_opens_wizard(self):
        self.assertFalse(self.file().exists())
        out = onboarding.progress()
        self.assertEqual(
            out,
            dict(
                schema=1,
                existing_install=False,
                completed_at=None,
                skipped=False,
                steps={},
                choices={},
                open=True,
                next_step="computer",
            ),
        )
        saved = json.loads(self.file().read_text(encoding="utf8"))
        self.assertEqual(saved, {k: v for k, v in out.items() if k not in ("open", "next_step")})
        if os.name != "nt":
            self.assertEqual(self.file().stat().st_mode & 0o077, 0)

    def test_existing_install_does_not_open(self):
        for name in ("settings.json", "connections.json"):
            with tempfile.TemporaryDirectory() as tmp, patch.object(onboarding, "DATA", Path(tmp)):
                marker = Path(tmp) / name
                marker.write_text("{}", encoding="utf8")
                first = onboarding.progress()
                self.assertTrue(first["existing_install"], name)
                self.assertFalse(first["open"], name)
                marker.unlink()
                again = onboarding.progress()
                self.assertTrue(again["existing_install"], name)
                self.assertFalse(again["open"], name)

    def test_fresh_install_stays_fresh_after_settings_are_saved(self):
        self.assertTrue(onboarding.progress()["open"])
        (self.data / "settings.json").write_text("{}", encoding="utf8")
        out = onboarding.progress()
        self.assertFalse(out["existing_install"])
        self.assertTrue(out["open"])

    def test_mark_finish_reset_roundtrip(self):
        out = onboarding.mark("computer", {"source": "local"})
        self.assertRegex(out["steps"]["computer"], STAMP)
        self.assertTrue(STAMP.fullmatch(out["steps"]["computer"]))
        self.assertEqual(out["choices"], {"source": "local"})
        self.assertEqual(out["next_step"], "model")
        self.assertTrue(out["open"])
        out = onboarding.mark("model", {"model_entry": "metal-48-qwen3.8-q6", "source": "attach"})
        self.assertEqual(out["choices"], {"source": "attach", "model_entry": "metal-48-qwen3.8-q6"})
        self.assertEqual(out["next_step"], "agent")
        self.assertEqual(onboarding.mark("project")["next_step"], "agent")
        for step in ("agent", "ready"):
            out = onboarding.mark(step)
        self.assertEqual(out["next_step"], "ready")
        self.assertEqual(set(out["steps"]), set(onboarding.STEPS))

        out = onboarding.finish()
        self.assertTrue(STAMP.fullmatch(out["completed_at"]))
        self.assertFalse(out["skipped"])
        self.assertFalse(out["open"])
        self.assertEqual(onboarding.progress(), out)

        out = onboarding.finish(skipped=True)
        self.assertTrue(out["skipped"])
        self.assertIsNone(out["completed_at"])
        self.assertFalse(out["open"])

        out = onboarding.reset()
        self.assertEqual(out["steps"], {})
        self.assertIsNone(out["completed_at"])
        self.assertFalse(out["skipped"])
        self.assertEqual(out["choices"], {"source": "attach", "model_entry": "metal-48-qwen3.8-q6"})
        self.assertTrue(out["open"])
        self.assertEqual(out["next_step"], "computer")
        self.assertEqual(json.loads(self.file().read_text(encoding="utf8"))["choices"], out["choices"])

    def test_reset_reopens_the_wizard_on_an_existing_install(self):
        (self.data / "settings.json").write_text("{}", encoding="utf8")
        self.assertFalse(onboarding.progress()["open"])
        out = onboarding.reset()
        self.assertFalse(out["existing_install"])
        self.assertTrue(out["open"])

    def test_mark_validation(self):
        def refused():
            for step in ("launch", "", None, ["computer"]):
                with self.assertRaises(ValueError) as caught:
                    onboarding.mark(step, {"source": "local"})
                self.assertEqual(str(caught.exception), "Choose one of the setup steps.")
            bad = (
                {"api_key": "sk-secret"},
                {"harness": 5},
                {"kit": None},
                {"workspace": "x" * 2000},
                {"source": "magic"},
                ["source"],
                "local",
                0,
            )
            for choices in bad:
                with self.assertRaises(ValueError) as caught:
                    onboarding.mark("computer", choices)
                self.assertEqual(str(caught.exception), "Setup choices must be short text values.")

        refused()
        self.assertEqual(list(self.data.iterdir()), [])
        onboarding.mark("computer", {"harness": "OMP", "workspace": "x" * 1024})
        before = self.file().read_bytes()
        refused()
        self.assertEqual(self.file().read_bytes(), before)

    def test_corrupt_progress_file_is_treated_as_new(self):
        fresh = dict(schema=1, existing_install=False, completed_at=None, skipped=False, steps={}, choices={})
        for text in ("{not json", '{"schema": 9}', "[1, 2]", '"done"', "", '{"schema": true, "skipped": true}'):
            self.file().write_bytes(text.encode("utf8"))
            out = onboarding.progress()
            self.assertEqual(out, fresh | dict(open=True, next_step="computer"), text)
            self.assertEqual(json.loads(self.file().read_text(encoding="utf8")), fresh, text)
        for damaged in (b"\xff\xfe\x00", b"[" * 100000):
            self.file().write_bytes(damaged)
            self.assertEqual(onboarding.progress(), fresh | dict(open=True, next_step="computer"), damaged[:8])
            self.assertEqual(json.loads(self.file().read_text(encoding="utf8")), fresh, damaged[:8])

    def test_saved_fields_are_normalised_on_read(self):
        saved = {
            "schema": 1,
            "existing_install": "yes",
            "completed_at": 5,
            "skipped": 1,
            "steps": {"computer": "2026-11-22T21:31:02Z", "model": 7, "launch": "2026-11-22T21:31:02Z"},
            "choices": {"source": "local", "kit": ["general"], "api_key": "sk-secret"},
            "extra": "ignored",
        }
        self.file().write_text(json.dumps(saved), encoding="utf8")
        out = onboarding.progress()
        self.assertEqual(
            out,
            dict(
                schema=1,
                existing_install=False,
                completed_at=None,
                skipped=False,
                steps={"computer": "2026-11-22T21:31:02Z"},
                choices={"source": "local"},
                open=True,
                next_step="model",
            ),
        )
        self.file().write_text(json.dumps(saved | {"steps": [], "choices": "x", "skipped": True}), encoding="utf8")
        out = onboarding.mark("agent", {"kit": "general"})
        self.assertEqual((set(out["steps"]), out["choices"], out["open"]), ({"agent"}, {"kit": "general"}, False))
        self.assertNotIn("api_key", self.file().read_text(encoding="utf8"))

    def test_marks_from_several_threads_are_all_kept(self):
        failures = []

        def work(key):
            try:
                onboarding.mark("computer", {key: "attach" if key == "source" else key})
            except Exception as exc:
                failures.append(exc)

        threads = [threading.Thread(target=work, args=(key,)) for key in onboarding.CHOICES]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(failures, [])
        self.assertEqual(set(onboarding.progress()["choices"]), set(onboarding.CHOICES))
        self.assertEqual(
            sorted(p.name for p in self.data.iterdir() if not p.name.endswith(".lock")), ["onboarding.json"]
        )


class DispatchTests(Base):
    def test_dispatch_routes(self):
        with (
            patch.object(onboarding, "state", Mock(return_value={"schema": 1})) as state,
            patch.object(onboarding, "mark", Mock(return_value={"next_step": "model"})) as mark,
            patch.object(onboarding, "finish", Mock(return_value={"open": False})) as finish,
            patch.object(onboarding, "reset", Mock(return_value={"open": True})) as reset,
        ):
            payload = {"workspace": "/code/app", "sections": ["hardware", 3, "kit"], "refresh": True}
            self.assertEqual(onboarding.dispatch("onboarding-state", payload), {"schema": 1})
            onboarding.dispatch("onboarding-state", {"sections": "hardware"})
            onboarding.dispatch("onboarding-state", None)
            self.assertEqual(
                state.call_args_list,
                [call("/code/app", ["hardware", "kit"], True), call("", None, False), call("", None, False)],
            )

            choices = {"source": "local"}
            self.assertEqual(
                onboarding.dispatch("onboarding-mark", {"step": "computer", "choices": choices}), {"next_step": "model"}
            )
            onboarding.dispatch("onboarding-mark", {"step": "model"})
            self.assertEqual(mark.call_args_list, [call("computer", choices), call("model", None)])

            self.assertEqual(onboarding.dispatch("onboarding-finish", {"skipped": True}), {"open": False})
            onboarding.dispatch("onboarding-finish", {})
            onboarding.dispatch("onboarding-finish", {"skipped": "false"})
            self.assertEqual(finish.call_args_list, [call(True), call(False), call(False)])

            self.assertEqual(
                onboarding.dispatch("onboarding-reset", {}, progress=lambda event: None, cancel=threading.Event()),
                {"open": True},
            )
            reset.assert_called_once_with()

            for action in ("onboarding-", "onboarding-delete", "model-hardware", ""):
                with self.assertRaises(ValueError) as caught:
                    onboarding.dispatch(action, {}, progress=None, cancel=None)
                self.assertEqual(str(caught.exception), "Unknown setup action.")

    def test_dispatch_end_to_end(self):
        with self.sources():
            out = onboarding.dispatch("onboarding-state", {"workspace": str(self.tmp), "sections": ["kit", "managed"]})
            self.assertEqual(set(out), FIXED | {"managed", "kit"})
            self.assertTrue(out["progress"]["open"])
            out = onboarding.dispatch("onboarding-mark", {"step": "computer", "choices": {"harness": "OMP"}})
            self.assertEqual((out["next_step"], out["choices"]), ("model", {"harness": "OMP"}))
            self.assertTrue(onboarding.dispatch("onboarding-finish", {"skipped": True})["skipped"])
            self.assertTrue(onboarding.dispatch("onboarding-reset", {})["open"])
        with self.assertRaises(ValueError) as caught:
            onboarding.dispatch("onboarding-mark", {"step": "computer", "choices": {"source": "magic"}})
        self.assertEqual(str(caught.exception), "Setup choices must be short text values.")


class ShapeTests(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(onboarding.STEPS, ("computer", "model", "agent", "project", "ready"))
        self.assertEqual(
            onboarding.CHOICES, ("source", "connection_id", "model_entry", "harness", "workspace", "kit")
        )
        self.assertEqual(onboarding.SOURCE_KINDS, ("attach", "local", "hosted", "ssh"))
        self.assertEqual(
            onboarding.PROVIDERS,
            {"endpoints": ("endpoints", "scan"), "engine": ("setup_engines", "info"), "kit": ("starter_kits", "detect")},
        )
        self.assertEqual(onboarding.DEADLINE, 8.0)
        for name in onboarding.CHOICES:
            self.assertFalse(re.search(r"key|token|secret|password|url|endpoint", name), name)


if __name__ == "__main__":
    unittest.main()
