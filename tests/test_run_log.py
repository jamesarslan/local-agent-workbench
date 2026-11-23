"""Run summaries: what is kept for each launch, that no secret is kept, drift, profiles and bridge actions."""

import atexit
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

if "WORKBENCH_DATA" not in os.environ:
    os.environ["WORKBENCH_DATA"] = tempfile.mkdtemp(prefix="awb-test-")
    atexit.register(shutil.rmtree, os.environ["WORKBENCH_DATA"], ignore_errors=True)

import account_book  # noqa: E402
import connections  # noqa: E402
import desktop_bridge as bridge  # noqa: E402
import paths  # noqa: E402
import project_profiles as projects  # noqa: E402
import project_setup  # noqa: E402
import project_terminal as pt  # noqa: E402
import run_log  # noqa: E402
import runtime  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
KEY = "sk-fixture-SECRET-key-0001"
USER_TOKEN = "tok-SECRET-userinfo-0002"
QUERY_TOKEN = "q-SECRET-query-0003"
ADDRESS = f"https://lab:{USER_TOKEN}@models.example:8443/v1?api_key={QUERY_TOKEN}"
SECRETS = (KEY, USER_TOKEN, QUERY_TOKEN, "api_key", "lab:")
AGENTS = "# Rules\n\nRun the tests before you finish.\n"
SKILL = "---\nname: {name}\ndescription: Review a change.\n---\n\nRead the diff first.\n"


def sha(text):
    return hashlib.sha256(text.encode("utf8")).hexdigest()


class RunCase(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory(prefix="awb-runs-")
        self.addCleanup(holder.cleanup)
        self.tmp = paths.canonical(holder.name)
        self.data = self.tmp / "data"
        self.repo = self.tmp / "repo"
        self.work = self.repo / "app"
        for folder in (self.data, self.repo / ".git", self.work):
            folder.mkdir(parents=True)
        (self.work / "AGENTS.md").write_text(AGENTS, encoding="utf8")
        self.skill("review")
        self.lab = {
            "lab": dict(
                connection_id="lab",
                provider="Attached endpoint",
                name="Model lab",
                endpoint="https://models.example:8443/v1",
            )
        }
        for item in (
            patch.object(runtime, "DATA", self.data),
            patch.object(projects, "DATA", self.data),
            patch.object(account_book, "entries", side_effect=lambda: dict(self.lab)),
            patch.object(account_book, "available", side_effect=lambda: list(self.lab.values())),
            patch.object(projects, "registry", return_value={}),
            patch.dict(os.environ, {"WORKBENCH_KEY_LAB": KEY, "OPENROUTER_API_KEY": KEY}),
        ):
            item.start()
            self.addCleanup(item.stop)

    def skill(self, name):
        folder = self.work / ".agents" / "skills" / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(SKILL.format(name=name), encoding="utf8")

    def facts(self, **values):
        s = runtime.DEFAULTS | dict(
            workspace=str(self.work),
            harness="OMP",
            provider="Attached endpoint",
            connection_id="lab",
            endpoint=ADDRESS,
            remote_model="qwen3-coder",
            context=65536,
            thinking="high",
            prompt="Keep answers short.",
            model="/models/old.gguf",
            api_key=KEY,
        )
        s |= values
        return dict(settings=s, base=ADDRESS, model="qwen3-coder", sent=["search", "docs"], prompt=s["harness"] != "Codex")

    def record(self, harness="OMP", kind="embedded", **values):
        ident = run_log.note(self.facts(harness=harness, **values), harness, kind, "0.99.1")
        self.assertRegex(ident, r"^[0-9a-f]{16}$")
        return run_log.find(self.work, ident)

    def stored(self):
        return run_log.run_file(self.work).read_text(encoding="utf8")


class RecordTest(RunCase):
    def test_a_run_keeps_what_the_agent_used(self):
        run = self.record()
        self.assertEqual(run["project"], str(self.work))
        self.assertEqual((run["kind"], run["harness"], run["harness_version"]), ("embedded", "OMP", "0.99.1"))
        self.assertEqual(run["workbench"], json.loads((ROOT / "desktop/package.json").read_text())["version"])
        self.assertRegex(run["started"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$")
        self.assertEqual(run["source"], "Attached endpoint")
        self.assertEqual(run["connection"], "Model lab")
        self.assertEqual(run["endpoint"], "https://models.example:8443")
        self.assertEqual((run["model"], run["context"], run["reasoning"]), ("qwen3-coder", 65536, "high"))
        self.assertEqual((run["engine"], run["weights"], run["weights_sha256"]), ("", "", ""))
        self.assertEqual(run["prompt"], "Keep answers short.")
        self.assertEqual(run["mcp"], ["search", "docs"])
        self.assertEqual(run["consent"], "not needed")
        self.assertEqual(
            run["instructions"],
            [{"path": "AGENTS.md", "status": "read", "bytes": len(AGENTS), "sha256": sha(AGENTS)}],
        )
        self.assertEqual(
            run["skills"],
            [{"name": "review", "path": ".agents/skills/review/SKILL.md", "sha256": sha(SKILL.format(name="review"))}],
        )
        self.assertEqual(run["settings"]["harness"], "OMP")
        self.assertNotIn("prompt", run["settings"])
        file = run_log.run_file(self.work)
        key = hashlib.sha256(paths.project_key(self.work).encode()).hexdigest()[:16]
        self.assertEqual(file, self.data / "runs" / (key + ".json"))
        data = json.loads(self.stored())
        self.assertEqual((data["version"], data["project"]), (1, str(self.work)))
        self.assertEqual([row["id"] for row in data["runs"]], [run["id"]])

    def test_local_weights_carry_the_catalog_hash_without_reading_the_file(self):
        catalog = json.loads((ROOT / "model_catalog.json").read_text())
        listed = catalog["models"][0]["files"][0]
        weights = "/models/" + listed["path"].rsplit("/", 1)[-1]
        with patch.object(run_log, "_digest", wraps=run_log._digest) as digest:
            run = self.record(provider="Managed local", model=weights, engine="llama.cpp")
        self.assertNotIn(weights, [str(call.args[0]) for call in digest.call_args_list])
        self.assertEqual((run["weights"], run["weights_sha256"]), (Path(weights).name, listed["sha256"]))
        self.assertEqual((run["engine"], run["connection"]), ("llama.cpp", ""))
        self.assertEqual(run_log.rows(self.work)[0]["model"], Path(weights).name)
        self.assertEqual(run_log.rows(self.work)[0]["source"], "Local model")
        unknown = self.record(provider="Managed local", model="/models/mine.gguf")
        self.assertEqual((unknown["weights"], unknown["weights_sha256"]), ("mine.gguf", ""))

    def test_no_secret_is_kept_anywhere(self):
        run = self.record()
        project_setup_rows = run_log.dispatch("run-summary", {"workspace": str(self.work), "id": run["id"]})
        places = {
            "file": self.stored(),
            "markdown": run_log.summary_markdown(run, self.work),
            "json": run_log.summary_json(run),
            "summary": json.dumps(project_setup_rows),
            "list": json.dumps(run_log.rows(self.work)),
            "profile": json.dumps(run_log.to_profile(run)),
        }
        for where, text in places.items():
            for secret in SECRETS:
                self.assertNotIn(secret, text, f"{secret} is in the {where}")
        self.assertIn("https://models.example:8443", places["markdown"])

    def test_addresses_keep_only_scheme_host_and_port(self):
        cases = {
            ADDRESS: "https://models.example:8443",
            "http://127.0.0.1:10532/v1": "http://127.0.0.1:10532",
            "https://openrouter.ai/api/v1?key=x#frag": "https://openrouter.ai",
            "http://[::1]:8000/v1": "http://[::1]:8000",
            "ftp://files.example/": "",
            "https://host:99999/": "",
            "": "",
            None: "",
        }
        for given, shown in cases.items():
            self.assertEqual(run_log.origin(given), shown, given)

    def test_the_list_keeps_the_newest_hundred_runs(self):
        for n in range(105):
            run_log.save({"id": f"{n:016x}", "project": str(self.work), "harness": "OMP"})
        kept = json.loads(self.stored())["runs"]
        self.assertEqual(len(kept), run_log.MAX_RUNS)
        self.assertEqual((kept[0]["id"], kept[-1]["id"]), (f"{104:016x}", f"{5:016x}"))
        self.assertEqual(len(run_log.rows(self.work, 10)), 10)
        self.assertEqual(len(run_log.rows(self.work, "all")), 100)

    def test_the_prompt_is_kept_only_when_the_agent_received_it(self):
        codex = self.record(harness="Codex")
        self.assertEqual(codex["prompt"], "")
        self.assertNotIn("prompt", run_log.to_profile(codex))
        self.assertIn("## Folder prompt\n\nNo prompt.", run_log.summary_markdown(codex, self.work))

    def test_a_long_prompt_is_capped(self):
        run = self.record(prompt="x" * (run_log.MAX_PROMPT + 5))
        self.assertEqual((len(run["prompt"]), run["prompt_truncated"]), (run_log.MAX_PROMPT, True))
        self.assertNotIn("prompt", run_log.to_profile(run))
        self.assertIn("Only the first", run_log.summary_markdown(run, self.work))

    def test_recording_never_fails_a_launch(self):
        self.assertEqual(run_log.note({}, "Codex", "window"), "")
        self.assertEqual(run_log.note(None, "Codex", "window"), "")
        with patch("sys.stderr"):
            self.assertEqual(run_log.note(self.facts(workspace=str(self.tmp / "gone")), "Codex", "window"), "")
            with patch.object(runtime, "write_json", side_effect=OSError("disk full")):
                self.assertEqual(run_log.note(self.facts(), "Codex", "window"), "")
        with patch.object(run_log, "note", side_effect=RuntimeError("broken")) as note, patch("sys.stderr"):
            self.assertEqual(bridge.note_run("Codex", "window", self.facts()), "")
            note.assert_called_once()
            note.reset_mock()
            self.assertEqual(bridge.note_run("Codex", "window", {}), "")
            note.assert_not_called()

    def test_a_damaged_list_is_reported_and_replaced_by_the_next_run(self):
        file = run_log.run_file(self.work)
        file.parent.mkdir(parents=True)
        file.write_text("[1, 2", encoding="utf8")
        with self.assertRaisesRegex(ValueError, "Clear runs"):
            run_log.rows(self.work)
        run = self.record()
        self.assertEqual([row["id"] for row in run_log.rows(self.work)], [run["id"]])


class SummaryTest(RunCase):
    def test_markdown_names_everything_needed_to_rerun(self):
        run = self.record(prompt="Use ``` fences\nwith care.")
        text = run_log.summary_markdown(run, self.work)
        self.assertTrue(text.startswith("# Agent Workbench run · "))
        for line in (
            "- Agent: OMP 0.99.1",
            "- Started in: Terminal dock",
            "- Model source: Attached endpoint",
            "- Connection: Model lab",
            "- Model: qwen3-coder",
            "- Context: 65,536 tokens",
            "- Reasoning effort: high",
            "- MCP servers: search, docs",
            f"- AGENTS.md · {len(AGENTS)} B · sha256 {sha(AGENTS)[:12]}",
            "- review · .agents/skills/review/SKILL.md · sha256 ",
            "````text\nUse ``` fences\nwith care.\n````",
            "- Nothing: the instruction files and skills are as they were.",
        ):
            self.assertIn(line, text)
        self.assertEqual(json.loads(run_log.summary_json(run)), run)

    def test_changes_since_the_run_are_listed(self):
        run = self.record()
        (self.work / "AGENTS.md").write_text(AGENTS + "Also lint.\n", encoding="utf8")
        shutil.rmtree(self.work / ".agents" / "skills" / "review")
        self.skill("deploy")
        (self.repo / "AGENTS.md").write_text("# Repository rules\n", encoding="utf8")
        changes = run_log.drift(run, self.work)
        self.assertEqual(
            sorted((row["kind"], row["path"], row["change"]) for row in changes),
            [
                ("instruction", "../AGENTS.md", "new"),
                ("instruction", "AGENTS.md", "changed"),
                ("skill", ".agents/skills/deploy/SKILL.md", "new"),
                ("skill", ".agents/skills/review/SKILL.md", "missing"),
            ],
        )
        text = run_log.summary_markdown(run, self.work)
        self.assertIn("- AGENTS.md: changed", text)
        self.assertIn("- ../AGENTS.md: new instruction file the agent now reads", text)
        self.assertIn("- .agents/skills/review/SKILL.md: skill no longer there", text)
        (self.work / "AGENTS.md").unlink()
        self.assertIn(
            ("instruction", "AGENTS.md", "missing"),
            [(row["kind"], row["path"], row["change"]) for row in run_log.drift(run, self.work)],
        )


class ProfileTest(RunCase):
    def test_a_run_becomes_a_profile_that_validates(self):
        run = self.record(search=False, plugins="")
        item = run_log.to_profile(run)
        self.assertEqual(projects.validate_profile(item, "x"), item)
        expected = {
            "harness": "OMP",
            "provider": "Attached endpoint",
            "remote_model": "qwen3-coder",
            "context": 65536,
            "thinking": "high",
            "prompt": "Keep answers short.",
            "connection_name": "Model lab",
            "search": False,
        }
        self.assertLessEqual(expected.items(), item.items())
        self.assertNotIn("weight", item)
        self.assertLessEqual(set(item), {*projects.PORTABLE, "connection_name", "weight"})

    def test_saved_runs_go_through_the_reviewed_apply_flow(self):
        run = self.record()
        saved = run_log.save_profile(self.work, run["id"])
        self.assertRegex(saved["name"], r"^Run \d{4}-\d\d-\d\d \d\d:\d\d$")
        self.assertEqual(saved["path"], str(self.work / ".agent-workbench" / "profiles.json"))
        self.assertEqual(projects.project_names(self.work), [saved["name"]])
        with self.assertRaisesRegex(ValueError, "already has a profile named"):
            run_log.save_profile(self.work, run["id"])
        for bad in ("\x01", "x" * 81):
            with self.assertRaisesRegex(ValueError, "1–80 characters"):
                run_log.save_profile(self.work, run["id"], bad)
        named = run_log.save_profile(self.work, run["id"], "  Rerun lab  ")
        self.assertEqual(named["name"], "Rerun lab")
        s = runtime.DEFAULTS | dict(workspace=str(self.work), harness="Codex", provider="OpenRouter")
        preview = projects.preview_project(s, "Rerun lab", [])
        self.assertEqual(preview["blockers"], [])
        changed = {row["field"]: row["to"] for row in preview["changes"]}
        self.assertEqual((changed["harness"], changed["provider"]), ("OMP", "Attached endpoint"))
        self.assertEqual(changed["connection"], "Model lab")
        with self.assertRaisesRegex(ValueError, "Review what this profile changes"):
            projects.apply_project(s, "Rerun lab", [], "")


class BridgeTest(RunCase):
    def call(self, action, **payload):
        return bridge.dispatch(action, {"workspace": str(self.work), **payload})

    def test_actions_are_routed_to_run_log(self):
        table = json.loads((ROOT / "desktop/actions.json").read_text())["actions"]
        for action in ("run-list", "run-summary", "run-save-profile", "run-forget", "run-clear"):
            self.assertEqual(bridge.owner(action), "run_log")
            self.assertIn(action, table)
        first, second = self.record(), self.record(kind="window")
        rows = self.call("run-list", limit=5)
        self.assertEqual([row["id"] for row in rows], [second["id"], first["id"]])
        self.assertEqual(
            rows[0],
            dict(id=second["id"], started=second["started"], harness="OMP", model="qwen3-coder", source="Model lab", kind="window"),
        )
        summary = self.call("run-summary", id=first["id"])
        self.assertEqual(set(summary), {"markdown", "json", "drift", "name"})
        self.assertIn("- Started in: Terminal dock", summary["markdown"])
        self.assertEqual(json.loads(summary["json"])["id"], first["id"])
        self.assertEqual(summary["drift"], [])
        saved = self.call("run-save-profile", id=first["id"], name="Lab rerun")
        self.assertEqual(saved["name"], "Lab rerun")
        self.assertIn("Lab rerun", projects.project_names(self.work))
        self.assertIs(self.call("run-forget", id=first["id"]), True)
        self.assertEqual([row["id"] for row in self.call("run-list")], [second["id"]])
        for gone in (first["id"], "../etc", 5, None):
            with self.assertRaisesRegex(ValueError, "no longer in the list"):
                self.call("run-summary", id=gone)
        self.assertIs(self.call("run-clear"), True)
        self.assertEqual(self.call("run-list"), [])
        self.assertFalse(run_log.run_file(self.work).exists())
        with self.assertRaisesRegex(ValueError, "Unknown run action"):
            run_log.dispatch("run-nope", {})

    def test_a_window_launch_is_recorded_with_the_agent_version(self):
        s = runtime.DEFAULTS | dict(workspace=str(self.work), library=str(self.tmp / "none"))
        facts = self.facts()

        def launch(out, facts=None):
            facts.update(self.facts())

        for item in (
            patch.object(runtime, "settings", return_value=s),
            patch.object(runtime, "owned", return_value=(None, {})),
            patch.object(runtime, "write_json"),
            patch.object(bridge, "DATA", self.data),
            patch.object(bridge, "choices", return_value={}),
            patch.object(bridge, "remember"),
            patch.object(bridge, "installed_version", return_value="0.99.1"),
            patch.object(bridge.shutil, "which", return_value="/bin/codex"),
            patch.object(connections, "launch", side_effect=launch),
        ):
            item.start()
            self.addCleanup(item.stop)
        with patch.object(run_log, "note", return_value="ab" * 8) as note:
            self.assertIs(self.call("launch-start", harness="Codex", provider="OpenRouter"), True)
        given, harness, kind, version = note.call_args.args
        self.assertEqual((harness, kind, version), ("Codex", "window", "0.99.1"))
        self.assertEqual(given, facts)

    def test_the_dock_tab_knows_its_run(self):
        s = dict(harness="Codex", provider="OpenRouter", remote_model="vendor/model", model="")
        fake = types.SimpleNamespace(
            selection=Mock(return_value=s), choices=Mock(return_value={}), note_run=Mock(return_value="cd" * 8)
        )

        def ready(chosen, facts=None):
            facts.update(self.facts())
            return ["codex"], {}, str(self.work)

        made = Mock(side_effect=lambda *a, **k: types.SimpleNamespace(project=str(self.work), ended=False))
        with (
            patch.dict(sys.modules, {"desktop_bridge": fake}),
            patch.object(pt, "Session", made),
            patch.object(pt.runtime, "settings", return_value={}),
            patch.object(pt.connections, "ready_session", side_effect=ready),
            patch.object(pt.sessions, "project_dir", return_value=str(self.work)),
            patch.object(pt.sessions, "find", return_value={"id": "x"}),
            patch.object(pt.sessions, "resume", side_effect=lambda h, argv, env, row: (argv, env)),
            patch.dict(pt.SESSIONS, clear=True),
        ):
            ident = pt.dispatch("term-open", {"workspace": str(self.work), "kind": "agent"})
            self.assertEqual(pt.SESSIONS[ident].run, "cd" * 8)
            self.assertEqual(fake.note_run.call_args.args[:2], ("Codex", "embedded"))
            pt.dispatch("term-open", {"workspace": str(self.work), "kind": "resume", "saved": "a" * 64})
            self.assertEqual(fake.note_run.call_args.args[:2], ("Codex", "resume"))
            fake.note_run.reset_mock()
            pt.dispatch("term-open", {"workspace": str(self.work), "kind": "shell"})
            fake.note_run.assert_not_called()
            del fake.note_run
            other = pt.dispatch("term-open", {"workspace": str(self.work), "kind": "agent"})
            self.assertEqual(getattr(pt.SESSIONS[other], "run", ""), "")


class ConnectionFactsTest(unittest.TestCase):
    def test_prepare_reports_what_it_sent_and_never_the_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = paths.canonical(tmp)
            s = runtime.DEFAULTS | dict(
                workspace=str(work), harness="Claude Code", provider="OpenRouter", remote_model="vendor/model"
            )
            facts = {}
            with (
                patch.object(connections, "DATA", work),
                patch.object(project_setup, "before_launch", side_effect=lambda s, harness=None: s),
                patch.object(project_setup, "launch_mcp", return_value=dict(argv=[], env={}, files=[], servers=["search"], skipped=[])),
                patch.object(connections, "command", side_effect=lambda name: [name]),
                patch.object(connections, "secret", return_value=KEY),
                patch.object(connections, "context_for", return_value=(131072, "listed")),
            ):
                argv, env, _ = connections.ready_session(s, facts=facts)
            self.assertIn(KEY, env.values())
            self.assertEqual((facts["base"], facts["model"], facts["sent"]), (connections.PROVIDERS["OpenRouter"], "vendor/model", ["search"]))
            self.assertEqual(facts["settings"]["context"], 131072)
            self.assertIs(facts["prompt"], True)
            self.assertNotIn(KEY, json.dumps(facts, default=str))
            native = {}
            with (
                patch.object(project_setup, "before_launch", side_effect=lambda s, harness=None: s),
                patch.object(project_setup, "launch_mcp", return_value=dict(argv=[], env={}, files=[], servers=[], skipped=[])),
                patch.object(connections, "command", side_effect=lambda name: [name]),
            ):
                connections.prepare(s | dict(provider="Native login", remote_model="gpt-5"), "Codex", facts=native)
            self.assertEqual((native["base"], native["model"], native["prompt"]), ("", "gpt-5", False))
            self.assertEqual(native["settings"]["provider"], "Native login")


if __name__ == "__main__":
    unittest.main()
