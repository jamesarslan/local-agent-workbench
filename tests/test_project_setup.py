import atexit
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import tomllib
from types import SimpleNamespace
import unittest
from unittest.mock import patch

if "WORKBENCH_DATA" not in os.environ:
    os.environ["WORKBENCH_DATA"] = tempfile.mkdtemp(prefix="awb-test-")
    atexit.register(shutil.rmtree, os.environ["WORKBENCH_DATA"], ignore_errors=True)

import paths
import project_profiles as projects
import project_setup as setup
import runtime

PLAYWRIGHT = {
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "@playwright/mcp@0.0.86", "--isolated"],
    "enabled": True,
}
DOCS = {
    "transport": "http",
    "url": "https://mcp.example.com/mcp",
    "bearer_env": "DOCS_MCP_TOKEN",
    "header_env": {"X-Workspace": "DOCS_MCP_WORKSPACE"},
    "enabled": True,
}
JUPYTER = {
    "transport": "stdio",
    "command": "uvx",
    "args": ["jupyter-mcp-server@2.2.6"],
    "env": {"JUPYTER_URL": "http://127.0.0.1:8888"},
    "env_from": ["JUPYTER_TOKEN"],
    "enabled": True,
}
KIT_STDIO = {
    "fetch": ("uvx mcp-server-fetch@2026.11.18", {}, []),
    "playwright": ("npx -y @playwright/mcp@0.0.86 --isolated", {}, []),
    "chrome-devtools": (
        "npx -y chrome-devtools-mcp@1.13.0 --no-usage-statistics --no-performance-crux",
        {"CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS": "1"},
        [],
    ),
    "eslint": ("npx -y @eslint/mcp@0.3.17", {}, []),
    "shadcn": ("npx -y shadcn@4.23.0 mcp", {}, []),
    "next-devtools": (
        "npx -y next-devtools-mcp@0.4.0",
        {"NEXT_TELEMETRY_DISABLED": "1"},
        [],
    ),
    "svelte": ("npx -y @sveltejs/mcp@0.1.31", {}, []),
    "duckdb": (
        "uvx mcp-server-motherduck@1.1.1 --db-path :memory: --read-write",
        {},
        [],
    ),
    "jupyter": (
        "uvx jupyter-mcp-server@2.2.6",
        {"JUPYTER_URL": "http://127.0.0.1:8888"},
        ["JUPYTER_TOKEN"],
    ),
    "arxiv": ("uvx arxiv-mcp-server@0.8.2", {}, []),
    "markitdown": ("uvx markitdown-mcp@0.0.1a8", {}, []),
    "terraform": ("docker run -i --rm hashicorp/terraform-mcp-server:1.4.1", {}, []),
    "aws-docs": ("uvx awslabs.aws-documentation-mcp-server@1.2.6", {}, []),
    "kubernetes": ("npx -y kubernetes-mcp-server@0.1.3", {}, []),
    "github": (
        "docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN "
        "ghcr.io/github/github-mcp-server:v2.2.0",
        {},
        ["GITHUB_PERSONAL_ACCESS_TOKEN"],
    ),
    "godot": ("npx -y @coding-solo/godot-mcp@0.1.1", {}, []),
    "mcp-for-unity": (
        "uvx --from mcpforunityserver==10.3.0 mcp-for-unity --transport stdio",
        {"DISABLE_TELEMETRY": "true"},
        [],
    ),
}
KIT_HTTP = {
    "context7": "https://mcp.context7.com/mcp",
    "huggingface": "https://huggingface.co/mcp",
    "aws-knowledge": "https://knowledge-mcp.global.api.aws",
    "microsoft-learn": "https://learn.microsoft.com/api/mcp",
    "unreal": "http://127.0.0.1:8000/mcp",
}
TEMPLATE = """# shop

TODO: one line that says what this project is and names the main language and framework with versions.

## Commands

- Install: `npm install`
- Run: `npm run dev`
- Test everything: `npm run test`
- Test one file: `TODO: add the command`

## Verify before finishing

- Run the tests that cover the files you changed and read the output.
- Say which checks you ran, which passed, and which you could not run.

## Boundaries

Always:
- Read a file before you edit it.

Never:
- Commit secrets, keys or tokens.

## Gotchas

<!-- Add one line here each time you have corrected the agent twice about the same thing. -->
"""
SKILL = """---
name: workbench-verify-change
description: Verify a code change before reporting done.
license: MIT
---

## Steps
1. Run the tests.
"""
REAL_MCP_SERVERS = runtime.mcp_servers
CLEARED = ("DOCS_MCP_TOKEN", "DOCS_MCP_WORKSPACE", "JUPYTER_TOKEN", "TOKEN", "AWB_MCP_H0")


def sha(text):
    return hashlib.sha256(text.encode("utf8")).hexdigest()


class SetupCase(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory(prefix="awb-setup-")
        self.addCleanup(holder.cleanup)
        self.tmp = paths.canonical(holder.name)
        self.data, self.work = self.tmp / "data", self.tmp / "work"
        self.data.mkdir()
        self.work.mkdir()
        self.home = self.data / "clients" / "slot"
        self.own, self.vault, self.asked = {}, {}, []
        self.backend = SimpleNamespace()
        keyring = SimpleNamespace(
            get_password=lambda service, user: self.vault.get((service, user)),
            set_password=lambda service, user, value: self.vault.update(
                {(service, user): value}
            ),
            delete_password=lambda service, user: self.vault.pop((service, user)),
            get_keyring=lambda: self.backend,
        )
        real = setup._ancestors

        def bounded(root):
            return [f for f in real(root) if f == self.tmp or self.tmp in f.parents]

        def own(s):
            self.asked.append(dict(s))
            return json.loads(json.dumps(self.own))

        for item in (
            patch.object(projects, "DATA", self.data),
            patch.object(runtime, "mcp_servers", side_effect=own),
            patch.object(runtime, "settings", side_effect=lambda: dict(runtime.DEFAULTS)),
            patch.object(setup, "_ancestors", side_effect=bounded),
            patch.dict(sys.modules, {"keyring": keyring}),
            patch.dict(os.environ),
        ):
            item.start()
            self.addCleanup(item.stop)
        for name in CLEARED:
            os.environ.pop(name, None)
        self.s = runtime.DEFAULTS | dict(workspace=str(self.work), prompt="Global prompt.")

    def folder(self, name):
        made = self.tmp / name
        made.mkdir(parents=True)
        return made

    def put(self, rel, text, root=None):
        file = (root or self.work).joinpath(*rel.split("/"))
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(
            text if isinstance(text, str) else json.dumps(text),
            encoding="utf8",
            newline="\n",
        )
        return file

    def text(self, rel, root=None):
        return (root or self.work).joinpath(*rel.split("/")).read_bytes().decode("utf8")

    def project(self, servers, root=None, **extra):
        return self.put(
            setup.PROJECT_FILE, {"version": 1, **extra, "mcp": servers}, root
        )

    def allow(self, root=None):
        root = root or self.work
        setup.grant(root, setup.consent_status(root)["digest"])

    def secret(self, name, value):
        self.vault[("Agent Workbench", "mcp-env:" + name)] = value

    def rows(self, found):
        return {row["path"]: row for row in found}

    def items(self, plan):
        return {item["path"]: item for item in plan["items"]}

    def apply_all(self, harness, want, root=None, version=""):
        root = root or self.work
        plan = setup.plan(root, harness, want, version)
        paths_ = [item["path"] for item in plan["items"]]
        return setup.apply(root, harness, want, plan["id"], paths_, version)


class ServerModelTest(SetupCase):
    def test_clean_server_accepts_research_commands(self):
        for name, (line, env, passed) in KIT_STDIO.items():
            command, *args = line.split(" ")
            raw = dict(
                transport="stdio", command=command, args=args, env=env, env_from=passed
            )
            server = setup.clean_server(name, raw)
            self.assertEqual(setup.summary(server), line)
            self.assertEqual((server["origin"], server["enabled"]), ("project", False))
            self.assertEqual(setup.clean_server(name, server), server)
        for name, url in KIT_HTTP.items():
            server = setup.clean_server(name, {"transport": "http", "url": url})
            self.assertEqual(server["url"], url)

    def test_clean_server_rejects_shell_and_secrets(self):
        stdio = {"transport": "stdio", "command": "npx"}
        bad = [
            stdio | {"command": "./run.sh"},
            stdio | {"command": "sh -c x"},
            stdio | {"command": "-x"},
            stdio | {"args": ["a&b"]},
            stdio | {"args": ["$(id)"]},
            stdio | {"args": ["a;b"]},
            stdio | {"args": ['a"b']},
            stdio | {"args": ["a\nb"]},
            stdio | {"args": "x"},
            stdio | {"env": {"API_KEY": "x"}},
            stdio | {"env": {"PATH": "/x"}},
            stdio | {"env": {"OK": "a;b"}},
            stdio | {"env_from": ["ANTHROPIC_API_KEY"]},
            stdio | {"env_from": ["lower"]},
            stdio | {"url": "https://h/mcp"},
            stdio | {"enabled": "yes"},
            {"transport": "http", "url": "http://example.com/mcp"},
            {"transport": "http", "url": "https://h/mcp?token=1"},
            {"transport": "http", "url": "https://u:p@h/mcp"},
            {"transport": "http", "url": "https://h/mcp\n"},
            {"transport": "http", "url": "https://h/mcp", "bearer_env": "PATH"},
            {"transport": "http", "url": "https://h/mcp", "header_env": {"X Y": "A"}},
            {"transport": "http", "url": "https://h/mcp", "command": "npx"},
            {"transport": "sse", "url": "https://h/sse"},
            "npx",
        ]
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(ValueError) as caught:
                setup.clean_server("tool", raw)
            self.assertIn("tool: ", str(caught.exception))
            self.assertIn(setup.PROJECT_FILE, str(caught.exception))
        with self.assertRaisesRegex(ValueError, "put API_KEY under env_from"):
            setup.clean_server("tool", stdio | {"env": {"API_KEY": "x"}})
        for name in ("search", "a b", "", "x" * 49):
            with self.subTest(name=name), self.assertRaises(ValueError):
                setup.clean_server(name, stdio)

    def test_from_mcp_json_tolerates_odd_entries(self):
        found = setup.from_mcp_json(
            "search", {"command": "python", "args": ["a", 1], "env": {"A": 1}}, "registry"
        )
        self.assertEqual(
            (found["origin"], found["transport"], found["args"], found["env"]),
            ("builtin", "stdio", [], {}),
        )
        remote = setup.from_mcp_json(
            "live", {"url": "https://h/sse", "type": "sse", "headers": {"A": "b"}}, "registry"
        )
        self.assertEqual(
            (remote["origin"], remote["transport"], remote["headers"]),
            ("registry", "sse", {"A": "b"}),
        )
        self.assertIsNone(setup.from_mcp_json("x", {"args": []}, "registry"))
        self.assertIsNone(setup.from_mcp_json("x", "npx", "registry"))
        long = setup.from_mcp_json("x", {"command": "a\x1b", "args": ["b" * 200]}, "registry")
        self.assertEqual(len(setup.summary(long)), 120)
        self.assertNotIn("\x1b", setup.summary(long))
        self.own = {"bad": {"args": []}, "good": {"command": "x"}}
        found = setup.launch_mcp(self.s, "Codex", self.home)
        self.assertEqual(found["servers"], ["good"])
        self.assertEqual(
            found["skipped"],
            [{"name": "bad", "reason": "bad has no command or URL, so it is not sent."}],
        )

    def test_read_project_tolerates_bad_input(self):
        for text in ("{not json", "[]", json.dumps({"version": 2, "mcp": {}})):
            self.put(setup.PROJECT_FILE, text)
            found = setup.read_project(self.work)
            self.assertEqual((found["servers"], len(found["errors"])), ({}, 1))
            self.assertIn(setup.PROJECT_FILE, found["errors"][0])
            self.assertTrue(found["exists"])
        self.project(
            {"good": PLAYWRIGHT, "bad": {"transport": "stdio", "command": "sh -c x"}, "also": DOCS}
        )
        found = setup.read_project(self.work)
        self.assertEqual(sorted(found["servers"]), ["also", "good"])
        self.assertEqual(len(found["errors"]), 1)
        self.assertIn("bad: ", found["errors"][0])
        self.project({f"s{n:02}": PLAYWRIGHT for n in range(40)})
        found = setup.read_project(self.work)
        self.assertEqual(len(found["servers"]), 32)
        self.assertEqual(len(found["errors"]), 1)
        empty = setup.read_project(self.folder("empty"))
        self.assertEqual((empty["exists"], empty["servers"], empty["errors"]), (False, {}, []))

    def test_project_text_is_canonical(self):
        servers = {
            "playwright": setup.clean_server("playwright", PLAYWRIGHT),
            "docs": setup.clean_server("docs", DOCS | {"enabled": False}),
        }
        text = setup.project_text(servers, {"note": "kept", "version": 9})
        self.assertTrue(text.endswith("}\n"))
        data = json.loads(text)
        self.assertEqual(list(data), ["version", "note", "mcp"])
        self.assertEqual(data["version"], 1)
        self.assertEqual(list(data["mcp"]), ["docs", "playwright"])
        self.assertEqual(
            list(data["mcp"]["docs"]), ["transport", "url", "header_env", "bearer_env", "enabled"]
        )
        self.assertEqual(
            list(data["mcp"]["playwright"]), ["transport", "command", "args", "enabled"]
        )
        self.put(setup.PROJECT_FILE, text)
        found = setup.read_project(self.work)
        self.assertEqual((found["servers"], found["errors"]), (servers, []))
        self.assertEqual(found["extra"], {"note": "kept"})
        self.assertEqual(setup.project_text(found["servers"], found["extra"]), text)

    def test_project_file_that_cannot_be_written_back_is_unreadable(self):
        self.put(setup.PROJECT_FILE, '{"version": 1, "mcp": {}, "note": "\\ud800"}')
        found = setup.read_project(self.work)
        self.assertEqual((found["servers"], found["errors"]), ({}, [setup.UNREADABLE]))
        item = setup.plan(self.work, "Codex", {"mcp_set": {"playwright": PLAYWRIGHT}})["items"][0]
        self.assertEqual((item["action"], item["reason"]), ("skip", setup.UNREADABLE))
        self.assertEqual(setup.consent_status(self.work)["items"][0]["detail"], setup.OPAQUE)

    def test_machine_override_wins(self):
        self.project({"docs": DOCS | {"enabled": False}, "playwright": PLAYWRIGHT})
        self.assertEqual(
            [(s["name"], s["enabled"]) for s in setup.project_servers(self.work)],
            [("docs", False), ("playwright", True)],
        )
        projects.set_project_mcp(self.work, "docs", True)
        projects.set_project_mcp(self.work, "playwright", False)
        self.assertEqual(
            [(s["name"], s["enabled"]) for s in setup.project_servers(self.work)],
            [("docs", True), ("playwright", False)],
        )
        self.assertFalse(json.loads(self.text(setup.PROJECT_FILE))["mcp"]["docs"]["enabled"])


class ConsentTest(SetupCase):
    def test_consent_lists_and_hashes(self):
        mcp = {
            "mcpServers": {
                "github": {
                    "command": "docker",
                    "args": ["run", "-i", "ghcr.io/github/github-mcp-server"],
                    "env": {"GITHUB_TOKEN": "s3cret-value"},
                },
                "notes": {"url": "https://h/mcp", "headers": {"Authorization": "s3cret-head"}},
            }
        }
        self.put(".mcp.json", mcp)
        self.project({"jupyter": JUPYTER})
        status = setup.consent_status(self.work)
        found = self.rows(status["items"])
        self.assertEqual(list(found), [setup.PROJECT_FILE, ".mcp.json"])
        self.assertEqual(
            found[".mcp.json"]["detail"],
            "2 servers: github (docker run -i ghcr.io/github/github-mcp-server), notes (https://h/mcp)",
        )
        self.assertIn("jupyter (uvx jupyter-mcp-server@2.2.6", found[setup.PROJECT_FILE]["detail"])
        self.assertIn("receives JUPYTER_TOKEN", found[setup.PROJECT_FILE]["detail"])
        self.assertEqual(found[".mcp.json"]["read_by"], ["Claude Code", "OMP"])
        self.assertNotIn("s3cret", json.dumps(status))
        self.assertEqual((status["needed"], status["granted"]), (True, False))
        self.assertRegex(status["digest"], "^[0-9a-f]{64}$")
        mcp["mcpServers"]["extra"] = {"command": "sh"}
        self.put(".mcp.json", mcp)
        changed = setup.consent_status(self.work)
        self.assertNotEqual(changed["digest"], status["digest"])
        with self.assertRaisesRegex(ValueError, "changed while you were reading"):
            setup.grant(self.work, status["digest"])
        self.assertTrue(setup.grant(self.work, changed["digest"]))
        after = setup.consent_status(self.work)
        self.assertEqual((after["needed"], after["granted"]), (False, True))
        self.assertTrue(setup.revoke(self.work))
        self.assertTrue(setup.consent_status(self.work)["needed"])
        empty = self.folder("empty")
        self.assertEqual(
            setup.consent_status(empty),
            {"needed": False, "granted": False, "digest": "", "items": []},
        )
        self.assertTrue(setup.project_config_allowed(empty))
        self.assertFalse(setup.project_config_allowed(self.work))
        self.assertTrue(setup.project_config_allowed(self.tmp / "missing"))

    def test_consent_relevance_by_harness(self):
        self.put(".codex/config.toml", '[mcp_servers.x]\ncommand = "sh"\n')
        for harness, needed in (("Codex", True), ("OMP", True), ("Claude Code", False)):
            status = setup.consent_status(self.work, harness)
            self.assertEqual(status["needed"], needed, harness)
            self.assertEqual(status["items"][0]["relevant"], needed)
        self.assertTrue(setup.consent_status(self.work)["needed"])

    def test_consent_listing_rules(self):
        self.put("mcp.json", {"name": "not a server file"})
        self.put(".claude/settings.json", {"permissions": {}})
        self.put(".omp/hooks/.keep", "")
        (self.work / ".omp" / "hooks" / ".keep").unlink()
        self.assertEqual(setup.consent_status(self.work)["items"], [])
        for runs in (
            {"env": {"ANTHROPIC_BASE_URL": "https://elsewhere.example"}},
            {"apiKeyHelper": "./key.sh"},
            {"statusLine": {"type": "command", "command": "./line.sh"}},
        ):
            self.put(".claude/settings.local.json", {"permissions": {}} | runs)
            status = setup.consent_status(self.work, "Claude Code")
            self.assertEqual(
                [(item["path"], item["kind"]) for item in status["items"]],
                [(".claude/settings.local.json", "settings")],
                runs,
            )
            self.assertTrue(status["needed"], runs)
            self.assertFalse(setup.consent_status(self.work, "Codex")["needed"], runs)
        self.put("mcp.json", "{broken")
        self.put(".claude/settings.json", {"hooks": {"Stop": []}})
        self.put(".claude/settings.local.json", "{broken")
        self.put(".omp/hooks/run.ts", "x")
        self.put(".omp/hooks/lib/util.ts", "y")
        self.put(".omp/hooks/lib/deep/skip.ts", "z")
        self.put(".vscode/mcp.json", {"servers": {"vs": {"command": "node"}}})
        found = self.rows(setup.consent_status(self.work)["items"])
        self.assertEqual(found["mcp.json"]["detail"], setup.OPAQUE)
        self.assertEqual(found[".claude/settings.local.json"]["detail"], setup.OPAQUE)
        self.assertIn("May define hooks", found[".claude/settings.json"]["detail"])
        self.assertEqual(found[".omp/hooks"]["detail"], "2 files that this agent runs.")
        self.assertEqual(found[".omp/hooks"]["kind"], "code")
        self.assertEqual(found[".vscode/mcp.json"]["detail"], "1 server: vs (node)")
        before = setup.consent_status(self.work)["digest"]
        self.put(".omp/hooks/lib/util.ts", "changed")
        self.assertNotEqual(setup.consent_status(self.work)["digest"], before)

    def test_consent_detail_never_hides_hosts_or_variables(self):
        host = "mcp.github.com." + "a" * 60 + "." + "b" * 40 + ".evil.example"
        servers = {f"a{n}": PLAYWRIGHT for n in range(8)}
        servers["docs"] = {
            "transport": "http",
            "url": f"https://{host}/" + "p" * 80,
            "bearer_env": "GITHUB_TOKEN",
            "header_env": {"X-Key": "OPENAI_API_KEY"},
        }
        servers["zz"] = {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "pkg@1", *["--pad"] * 14, "last-argument"],
            "env_from": ["AWS_SECRET_ACCESS_KEY"],
        }
        self.project(servers)
        detail = setup.consent_status(self.work)["items"][0]["detail"]
        self.assertTrue(detail.startswith("10 servers: "))
        self.assertNotIn(" more", detail)
        self.assertIn(f"docs (https://{host}/ppp", detail)
        self.assertIn("…; receives OPENAI_API_KEY, GITHUB_TOKEN)", detail)
        self.assertIn("zz (npx -y pkg@1 --pad", detail)
        self.assertIn("…; receives AWS_SECRET_ACCESS_KEY)", detail)
        self.assertNotIn("last-argument", detail)
        self.assertIn("a7 (npx -y @playwright/mcp@0.0.86 --isolated)", detail)
        self.put(".mcp.json", {"mcpServers": {"long": {"command": "sh", "args": ["x" * 200]}}})
        listed = self.rows(setup.consent_status(self.work)["items"])[".mcp.json"]["detail"]
        self.assertEqual(listed, "1 server: long (sh " + "x" * 70 + "…")

    def test_shown_text_drops_direction_and_invisible_characters(self):
        self.put(
            ".mcp.json",
            {"mcpServers": {"safe\u202etool": {"command": "sh", "args": ["\u202egnp.exe", "a\u200bb", "c\x85d"]}}},
        )
        self.put(
            ".claude/skills/a\u202eb/SKILL.md", SKILL.replace("Verify a code", "Verify\u2066 a code")
        )
        detail = setup.consent_status(self.work)["items"][0]["detail"]
        self.assertEqual(detail, "1 server: safetool (sh gnp.exe ab cd)")
        found = setup.resolve(self.work, "Claude Code", "2.1.330", self.s)
        self.assertEqual(found["mcp"]["files"][0]["servers"], ["safetool"])
        self.assertEqual(
            (found["skills"][0]["path"], found["skills"][0]["description"]),
            (".claude/skills/ab/SKILL.md", "Verify a code change before reporting done."),
        )
        self.assertEqual(setup.summary(setup.clean_server("docs", DOCS)), DOCS["url"])
        self.assertEqual(setup._clean_text("naïve 日本語 ok\n", 40), "naïve 日本語 ok")

    def test_before_launch_blocks_without_consent(self):
        self.project({"playwright": PLAYWRIGHT})
        projects._update_folder(
            self.work,
            lambda record: record.update(prompt={"text": "Folder prompt.", "source": "kit"}),
        )
        with self.assertRaisesRegex(ValueError, "This folder defines tools that can run programs"):
            setup.before_launch(self.s)
        with self.assertRaises(ValueError):
            setup.before_launch(self.s, "Codex")
        self.allow()
        out = setup.before_launch(self.s, "Codex")
        self.assertEqual(out["prompt"], "Folder prompt.")
        self.assertEqual(self.s["prompt"], "Global prompt.")
        gone = self.s | {"workspace": str(self.tmp / "missing")}
        self.assertIs(setup.before_launch(gone), gone)


class DeliveryTest(SetupCase):
    def test_project_servers_need_consent_and_secrets(self):
        self.project({"jupyter": JUPYTER})
        first = setup.launch_mcp(self.s, "Claude Code", self.home)
        self.assertEqual((first["servers"], first["argv"], first["env"]), ([], [], {}))
        self.assertEqual(first["skipped"][0]["name"], "jupyter")
        self.allow()
        second = setup.launch_mcp(self.s, "Claude Code", self.home)
        self.assertEqual(second["servers"], [])
        self.assertEqual(
            second["skipped"],
            [{"name": "jupyter", "reason": "Set JUPYTER_TOKEN in Project setup to use jupyter."}],
        )
        self.secret("JUPYTER_TOKEN", "vault-value")
        third = setup.launch_mcp(self.s, "Claude Code", self.home)
        self.assertEqual((third["servers"], third["skipped"]), (["jupyter"], []))
        self.assertEqual(third["env"], {"JUPYTER_TOKEN": "vault-value"})
        os.environ["JUPYTER_TOKEN"] = "from-env"
        fourth = setup.launch_mcp(self.s, "Claude Code", self.home)
        self.assertEqual((fourth["servers"], fourth["env"]), (["jupyter"], {}))
        projects.set_project_mcp(self.work, "jupyter", False)
        off = setup.launch_mcp(self.s, "Claude Code", self.home)
        self.assertEqual((off["servers"], off["skipped"], off["argv"]), ([], [], []))

    def test_claude_delivery(self):
        self.project({"docs": DOCS, "jupyter": JUPYTER})
        self.allow()
        for name in ("DOCS_MCP_TOKEN", "DOCS_MCP_WORKSPACE", "JUPYTER_TOKEN"):
            self.secret(name, "value-of-" + name)
        self.own = {"reg": {"url": "https://h/sse", "type": "sse", "headers": {"A": "b"}}}
        found = setup.launch_mcp(self.s, "Claude Code", self.home)
        file = Path(found["argv"][1])
        self.assertEqual(found["argv"], ["--mcp-config", str(file)])
        self.assertEqual(found["files"], [str(file)])
        self.assertEqual(file, self.home / "claude-mcp" / "mcp.json")
        self.assertIn(self.data, file.parents)
        text = file.read_text(encoding="utf8")
        self.assertNotIn("value-of-", text)
        servers = json.loads(text)["mcpServers"]
        self.assertEqual(
            servers["docs"],
            {
                "type": "http",
                "url": "https://mcp.example.com/mcp",
                "headers": {
                    "X-Workspace": "${DOCS_MCP_WORKSPACE}",
                    "Authorization": "Bearer ${DOCS_MCP_TOKEN}",
                },
            },
        )
        self.assertEqual(
            servers["jupyter"]["env"],
            {"JUPYTER_URL": "http://127.0.0.1:8888", "JUPYTER_TOKEN": "${JUPYTER_TOKEN}"},
        )
        self.assertEqual(servers["jupyter"]["type"], "stdio")
        self.assertEqual(
            servers["reg"], {"type": "sse", "url": "https://h/sse", "headers": {"A": "b"}}
        )
        self.assertEqual(len(found["env"]), 3)
        native = setup.launch_mcp(self.s, "Claude Code", None)
        native_file = Path(native["argv"][1])
        self.assertEqual(native_file.name, "mcp.json")
        self.assertEqual(native_file.parent.parent.parent, self.data / "clients")
        self.assertTrue(native_file.parent.parent.name.startswith("native-"))
        self.own = {}
        bare = setup.launch_mcp(
            self.s | {"workspace": str(self.folder("bare"))}, "Claude Code", self.home
        )
        self.assertEqual((bare["argv"], bare["files"], bare["servers"]), ([], [], []))

    def test_pi_delivery_keeps_user_entries(self):
        file = self.home / "pi" / "mcp.json"
        file.parent.mkdir(parents=True)
        file.write_text(
            json.dumps({"mcpServers": {"mine": {"command": "m"}}, "other": 1}), encoding="utf8"
        )
        self.own = {
            "alpha": {"command": "a", "args": ["x"]},
            "live": {"url": "https://h/sse", "type": "sse"},
        }
        first = setup.launch_mcp(self.s, "Pi", self.home)
        self.assertEqual((first["argv"], first["servers"]), ([], ["alpha"]))
        self.assertEqual(
            first["skipped"], [{"name": "live", "reason": "Pi does not support SSE servers."}]
        )
        saved = json.loads(file.read_text(encoding="utf8"))
        self.assertEqual(
            saved["mcpServers"],
            {"mine": {"command": "m"}, "alpha": {"command": "a", "args": ["x"]}},
        )
        self.assertEqual(saved["other"], 1)
        self.own = {}
        second = setup.launch_mcp(self.s, "Pi", self.home)
        self.assertEqual(second["files"], [str(file)])
        self.assertEqual(
            json.loads(file.read_text(encoding="utf8"))["mcpServers"],
            {"mine": {"command": "m"}},
        )
        self.assertEqual(setup.launch_mcp(self.s, "Pi", self.home)["files"], [])
        self.assertIn("mine", json.loads(file.read_text(encoding="utf8"))["mcpServers"])

    def test_codex_delivery_flags(self):
        self.project({"docs": DOCS, "playwright": PLAYWRIGHT})
        self.allow()
        self.secret("DOCS_MCP_TOKEN", "t")
        self.secret("DOCS_MCP_WORKSPACE", "w")
        found = setup.launch_mcp(self.s, "Codex", self.home)
        self.assertEqual(
            found["argv"],
            [
                "-c",
                'mcp_servers.docs.url="https://mcp.example.com/mcp"',
                "-c",
                'mcp_servers.docs.bearer_token_env_var="DOCS_MCP_TOKEN"',
                "-c",
                'mcp_servers.docs.env_http_headers.X-Workspace="DOCS_MCP_WORKSPACE"',
                "-c",
                'mcp_servers.playwright.command="npx"',
                "-c",
                'mcp_servers.playwright.args=["-y", "@playwright/mcp@0.0.86", "--isolated"]',
            ],
        )
        self.assertEqual(found["env"], {"DOCS_MCP_TOKEN": "t", "DOCS_MCP_WORKSPACE": "w"})
        self.assertEqual(found["files"], [])
        for item in found["argv"][1::2]:
            key, value = item.split("=", 1)
            self.assertEqual(
                tomllib.loads("v = " + value)["v"], json.loads(value), key
            )
        argv, _, _ = setup.render_codex([setup.clean_server("jupyter", JUPYTER)])
        self.assertEqual(
            argv[1::2],
            [
                'mcp_servers.jupyter.command="uvx"',
                'mcp_servers.jupyter.args=["jupyter-mcp-server@2.2.6"]',
                'mcp_servers.jupyter.env.JUPYTER_URL="http://127.0.0.1:8888"',
                'mcp_servers.jupyter.env_vars=["JUPYTER_TOKEN"]',
            ],
        )

    def test_codex_registry_secrets_stay_out_of_argv(self):
        self.own = {
            "reg": {"command": "srv", "env": {"TOKEN": "s3cret"}},
            "web": {"url": "https://h/mcp", "headers": {"Authorization": "Bearer s3cret"}},
            "live": {"url": "https://h/sse", "type": "sse"},
            "odd": {"url": "https://h/mcp", "headers": {"Bad Header": "v"}},
        }
        found = setup.launch_mcp(self.s, "Codex", self.home)
        self.assertEqual(found["env"], {"TOKEN": "s3cret", "AWB_MCP_H0": "Bearer s3cret"})
        self.assertFalse([item for item in found["argv"] if "s3cret" in item])
        self.assertIn('mcp_servers.reg.command="srv"', found["argv"])
        self.assertIn('mcp_servers.reg.env_vars=["TOKEN"]', found["argv"])
        self.assertIn('mcp_servers.web.env_http_headers.Authorization="AWB_MCP_H0"', found["argv"])
        self.assertEqual(found["servers"], ["reg", "web"])
        self.assertEqual(
            found["skipped"],
            [
                {"name": "live", "reason": "Codex does not accept SSE servers."},
                {"name": "odd", "reason": "Codex cannot receive odd because of its header Bad Header."},
            ],
        )
        os.environ["TOKEN"] = "another"
        clash = setup.launch_mcp(self.s, "Codex", self.home)
        self.assertEqual(clash["servers"], ["web"])
        self.assertNotIn("TOKEN", clash["env"])
        self.assertEqual(
            clash["skipped"][0],
            {
                "name": "reg",
                "reason": "Codex cannot receive reg because its variable TOKEN conflicts with this session.",
            },
        )
        os.environ.pop("TOKEN")
        self.own = {
            "a": {"command": "x", "env": {"SHARED": "1"}},
            "b": {"command": "y", "env": {"SHARED": "2"}},
            "c": {"command": "z", "env": {"PATH": "/p"}},
        }
        self.assertEqual(setup.launch_mcp(self.s, "Codex", self.home)["servers"], ["a"])

    def test_codex_skips_registered_servers_with_arguments_or_a_folder(self):
        self.project({"playwright": PLAYWRIGHT})
        self.allow()
        self.own = {
            "keyed": {
                "command": "srv",
                "args": ["--api-key", "s3cret"],
                "env": {"TOKEN": "s3cret"},
            },
            "homed": {"command": "srv", "cwd": "/abs/s3cret"},
            "bare": {"command": "srv"},
            "search": {"command": "python", "args": ["web_tools.py", "search"], "cwd": "/app"},
        }
        reasons = {
            "keyed": "Codex cannot receive keyed because Workbench keeps a registered server's "
            "arguments and working folder off the command line, where a key would be visible; "
            "add it in Project setup as a project server under another name with any key as a "
            "variable instead of an argument, or use another agent.",
            "homed": setup.CODEX_ARGV.format(name="homed"),
        }
        self.assertEqual(reasons["keyed"], setup.CODEX_ARGV.format(name="keyed"))
        self.assertEqual(reasons["keyed"].count(". "), 0)
        found = setup.launch_mcp(self.s, "Codex", self.home)
        self.assertEqual(found["servers"], ["bare", "search", "playwright"])
        self.assertEqual(
            found["skipped"], [{"name": name, "reason": reasons[name]} for name in reasons]
        )
        self.assertEqual(
            found["argv"][1::2],
            [
                'mcp_servers.bare.command="srv"',
                'mcp_servers.playwright.command="npx"',
                'mcp_servers.playwright.args=["-y", "@playwright/mcp@0.0.86", "--isolated"]',
                'mcp_servers.search.command="python"',
                'mcp_servers.search.args=["web_tools.py", "search"]',
                'mcp_servers.search.cwd="/app"',
            ],
        )
        self.assertEqual(set(found["argv"][0::2]), {"-c"})
        self.assertEqual(found["env"], {})
        view = setup.resolve(self.work, "Codex", "", self.s)["mcp"]["launch"]
        self.assertEqual(
            [(row["name"], row["origin"], row["status"], row["reason"]) for row in view],
            [
                ("keyed", "registry", "skipped", reasons["keyed"]),
                ("homed", "registry", "skipped", reasons["homed"]),
                ("bare", "registry", "sent", ""),
                ("search", "builtin", "sent", ""),
                ("playwright", "project", "sent", ""),
            ],
        )
        off = setup.launch_mcp(self.s | {"search": False}, "Codex", self.home)
        self.assertEqual(off["servers"], ["bare", "playwright"])
        self.assertEqual(
            off["skipped"][2],
            {"name": "search", "reason": setup.CODEX_ARGV.format(name="search")},
        )
        self.assertFalse([item for item in off["argv"] if "mcp_servers.search." in item])
        claude = setup.launch_mcp(self.s, "Claude Code", self.home)
        self.assertEqual(claude["skipped"], [])
        saved = json.loads(Path(claude["argv"][1]).read_text(encoding="utf8"))["mcpServers"]
        self.assertEqual(saved["keyed"]["args"], ["--api-key", "s3cret"])
        self.assertEqual(saved["homed"]["cwd"], "/abs/s3cret")

    def test_codex_skip_holds_for_stored_registrations_and_the_extra_manifest(self):
        self.put(
            "plugins.json",
            {
                "keyed": {"command": "srv", "args": ["--api-key", "s3cret"]},
                "homed": {"command": "srv", "cwd": "/abs/s3cret"},
                "bare": {"type": "stdio", "command": "/opt/my tools/srv", "args": [], "cwd": ""},
            },
            self.data,
        )
        manifest = self.put(
            "extra.json",
            {
                "mcpServers": {
                    "listed": {"command": "srv", "args": ["--token=s3cret"]},
                    "firecrawl": {"command": "mine", "args": ["s3cret"]},
                }
            },
            self.tmp,
        )
        s = self.s | dict.fromkeys(projects.CAPS, False)
        s |= {"search": True, "plugins": "keyed,homed,bare", "extra_mcp": str(manifest)}
        with patch.object(runtime, "mcp_servers", REAL_MCP_SERVERS):
            found = setup.launch_mcp(s, "Codex", self.home)
            view = setup.resolve(self.work, "Codex", "", s)["mcp"]["launch"]
        self.assertEqual(found["servers"], ["search", "bare"])
        self.assertEqual(
            found["skipped"],
            [
                {"name": name, "reason": setup.CODEX_ARGV.format(name=name)}
                for name in ("listed", "firecrawl", "keyed", "homed")
            ],
        )
        self.assertEqual(
            found["argv"][1::2],
            [
                'mcp_servers.bare.command="/opt/my tools/srv"',
                "mcp_servers.search.command=" + json.dumps(sys.executable, ensure_ascii=False),
                "mcp_servers.search.args="
                + json.dumps([str(runtime.ROOT / "web_tools.py"), "search"], ensure_ascii=False),
            ],
        )
        self.assertFalse([item for item in found["argv"] if "s3cret" in item])
        self.assertEqual(found["env"], {})
        self.assertEqual(
            [(row["name"], row["origin"], row["status"]) for row in view],
            [
                ("search", "builtin", "sent"),
                ("listed", "registry", "skipped"),
                ("firecrawl", "registry", "skipped"),
                ("keyed", "registry", "skipped"),
                ("homed", "registry", "skipped"),
                ("bare", "registry", "sent"),
            ],
        )

    def test_render_codex_decides_by_origin(self):
        conf = {"command": "srv", "args": ["go"], "cwd": "/abs/dir"}
        registered = setup.from_mcp_json("reg", conf, "registry")
        argv, env, skipped = setup.render_codex([registered])
        self.assertEqual((argv, env), ([], {}))
        self.assertEqual(skipped, [{"name": "reg", "reason": setup.CODEX_ARGV.format(name="reg")}])
        for origin in ("builtin", "project"):
            argv, _, skipped = setup.render_codex([registered | {"origin": origin}])
            self.assertEqual(skipped, [], origin)
            self.assertEqual(
                argv[1::2],
                [
                    'mcp_servers.reg.command="srv"',
                    'mcp_servers.reg.args=["go"]',
                    'mcp_servers.reg.cwd="/abs/dir"',
                ],
                origin,
            )
        for conf in ({"command": "srv", "args": []}, {"command": "srv", "cwd": ""}):
            plain = setup.from_mcp_json("reg", conf, "registry")
            self.assertEqual(
                setup.render_codex([plain]), (["-c", 'mcp_servers.reg.command="srv"'], {}, [])
            )

    def test_codex_argv_never_carries_registered_credentials(self):
        self.own = {
            "search": {"command": "mine", "env": {"TOKEN": "s3cret"}},
            "query": {"url": "https://mcp.example.com/mcp?apiKey=s3cret"},
            "login": {"url": "https://user:s3cret@mcp.example.com/mcp"},
            "plain": {"url": "https://mcp.example.com/mcp"},
        }
        found = setup.launch_mcp(self.s | {"search": False}, "Codex", self.home)
        self.assertFalse([item for item in found["argv"] if "s3cret" in item])
        self.assertEqual(found["servers"], ["search", "plain"])
        self.assertEqual(found["env"], {"TOKEN": "s3cret"})
        self.assertIn('mcp_servers.search.env_vars=["TOKEN"]', found["argv"])
        self.assertEqual(
            found["skipped"],
            [
                {
                    "name": name,
                    "reason": f"Codex cannot receive {name} because its URL carries a password or "
                    f"query string; move that value into a header and register {name} again.",
                }
                for name in ("query", "login")
            ],
        )
        view = setup.resolve(self.work, "Codex", "", self.s | {"search": False})["mcp"]["launch"]
        self.assertEqual(
            [(row["name"], row["origin"], row["status"]) for row in view],
            [
                ("search", "registry", "sent"),
                ("query", "registry", "skipped"),
                ("login", "registry", "skipped"),
                ("plain", "registry", "sent"),
            ],
        )
        self.own = {"search": {"command": "python", "env": {"PYTHONUTF8": "1"}}}
        builtin = setup.launch_mcp(self.s | {"search": True}, "Codex", self.home)
        self.assertIn('mcp_servers.search.env.PYTHONUTF8="1"', builtin["argv"])
        self.assertEqual(builtin["env"], {})

    def test_codex_values_stay_valid_for_toml_and_the_process(self):
        self.own = {
            "search": {"command": "x", "args": ["a\x7fb", "tab\there"]},
            "blender": {"command": "x", "args": ["a\ud800b"]},
            "nul": {"command": "x", "env": {"A_VALUE": "a\0b"}},
        }
        found = setup.launch_mcp(self.s | {"search": True, "blender": True}, "Codex", self.home)
        self.assertEqual(found["servers"], ["search"])
        value = found["argv"][3].split("=", 1)[1]
        self.assertEqual(tomllib.loads("v = " + value)["v"], ["a\x7fb", "tab\there"])
        self.assertEqual(
            found["skipped"],
            [
                {
                    "name": name,
                    "reason": f"Codex cannot receive {name} because one of its values is not "
                    f"plain text; fix it and register {name} again.",
                }
                for name in ("blender", "nul")
            ],
        )
        for item in found["argv"]:
            item.encode("utf8")

    def test_codex_vault_value_is_not_replaced_by_a_registered_literal(self):
        self.project({"jupyter": JUPYTER})
        self.allow()
        self.secret("JUPYTER_TOKEN", "mine")
        self.own = {"reg": {"command": "x", "env": {"JUPYTER_TOKEN": "other"}}}
        found = setup.launch_mcp(self.s, "Codex", self.home)
        self.assertEqual(found["env"], {"JUPYTER_TOKEN": "mine"})
        self.assertEqual(found["servers"], ["jupyter"])
        self.assertEqual(
            found["skipped"],
            [
                {
                    "name": "reg",
                    "reason": "Codex cannot receive reg because its variable JUPYTER_TOKEN "
                    "conflicts with this session.",
                }
            ],
        )
        self.own = {"reg": {"command": "x", "env": {"JUPYTER_TOKEN": "mine"}}}
        same = setup.launch_mcp(self.s, "Codex", self.home)
        self.assertEqual((same["servers"], same["env"]), (["reg", "jupyter"], {"JUPYTER_TOKEN": "mine"}))

    def test_project_servers_already_in_the_session_are_not_taken_for_registered_ones(self):
        self.project({"jupyter": JUPYTER})
        self.allow()
        self.secret("JUPYTER_TOKEN", "mine")
        s = self.s | dict.fromkeys(projects.CAPS, False)
        s["_mcp"] = setup.omp_entries(s, set())
        self.assertEqual(s["_mcp"]["jupyter"]["env"]["JUPYTER_TOKEN"], "${JUPYTER_TOKEN}")
        with patch.object(runtime, "mcp_servers", REAL_MCP_SERVERS):
            for harness in ("OMP", "Claude Code", "Codex"):
                found = setup.launch_mcp(s, harness, self.home)
                self.assertEqual(
                    (found["servers"], found["env"], found["skipped"]),
                    (["jupyter"], {"JUPYTER_TOKEN": "mine"}, []),
                    harness,
                )
        self.assertIn('mcp_servers.jupyter.env_vars=["JUPYTER_TOKEN"]', found["argv"])

    def test_omp_entries_and_native_sessions(self):
        self.project({"playwright": PLAYWRIGHT, "docs": DOCS | {"enabled": False}})
        self.assertEqual(setup.omp_entries(self.s, set()), {})
        self.allow()
        self.assertEqual(
            setup.omp_entries(self.s, set()),
            {
                "playwright": {
                    "type": "stdio",
                    "command": "npx",
                    "args": ["-y", "@playwright/mcp@0.0.86", "--isolated"],
                }
            },
        )
        self.assertEqual(setup.omp_entries(self.s, {"playwright"}), {})
        self.assertEqual(setup.omp_entries({"workspace": ""}, set()), {})
        self.own = {"search": {"command": "python", "args": ["web_tools.py", "search"]}}
        omp = setup.launch_mcp(self.s, "OMP", self.home)
        self.assertEqual((omp["argv"], omp["servers"]), ([], ["search", "playwright"]))
        for harness, home in (("Pi", None), ("OMP", None)):
            found = setup.launch_mcp(self.s, harness, home)
            self.assertEqual((found["servers"], found["argv"], found["files"]), ([], [], []))
            self.assertEqual(
                {row["reason"] for row in found["skipped"]},
                {f"With an agent account, {harness} reads only your own MCP files."},
            )
            self.assertEqual(len(found["skipped"]), 2)
        hermes = setup.launch_mcp(self.s, "Hermes", self.home)
        self.assertEqual(hermes["servers"], [])
        self.assertEqual(
            hermes["skipped"][1]["reason"],
            "Hermes reads MCP servers only from its own settings. "
            "Add this one with: hermes mcp add playwright …",
        )
        opencode = setup.launch_mcp(self.s, "opencode", self.home)
        self.assertEqual(
            {row["reason"] for row in opencode["skipped"]},
            {"opencode receives servers through its own configuration."},
        )
        with self.assertRaisesRegex(ValueError, "Choose a supported agent."):
            setup.launch_mcp(self.s, "Nope", self.home)

    def test_registry_name_wins_over_project(self):
        self.project({"playwright": PLAYWRIGHT})
        self.allow()
        self.own = {"playwright": {"command": "mine"}}
        found = setup.launch_mcp(self.s, "Claude Code", self.home)
        self.assertEqual(found["servers"], ["playwright"])
        self.assertEqual(
            found["skipped"],
            [
                {
                    "name": "playwright",
                    "reason": "Your registered server playwright is used instead of the project's.",
                }
            ],
        )
        saved = json.loads(Path(found["argv"][1]).read_text(encoding="utf8"))
        self.assertEqual(saved["mcpServers"]["playwright"]["command"], "mine")

    def test_render_opencode_shape(self):
        servers = [
            setup.clean_server("jupyter", JUPYTER),
            setup.clean_server("docs", DOCS),
            setup.from_mcp_json("live", {"url": "https://h/sse", "type": "sse"}, "registry"),
        ]
        self.assertEqual(
            setup.render_opencode(servers),
            {
                "jupyter": {
                    "type": "local",
                    "command": ["uvx", "jupyter-mcp-server@2.2.6"],
                    "environment": {
                        "JUPYTER_URL": "http://127.0.0.1:8888",
                        "JUPYTER_TOKEN": "{env:JUPYTER_TOKEN}",
                    },
                    "enabled": True,
                },
                "docs": {
                    "type": "remote",
                    "url": "https://mcp.example.com/mcp",
                    "headers": {
                        "X-Workspace": "{env:DOCS_MCP_WORKSPACE}",
                        "Authorization": "Bearer {env:DOCS_MCP_TOKEN}",
                    },
                    "enabled": True,
                },
            },
        )
        entries, skipped = setup.render_mcp_json(servers, "pi")
        self.assertEqual(list(entries), ["jupyter", "docs"])
        self.assertNotIn("type", entries["docs"])
        self.assertEqual(skipped[0]["name"], "live")
        with self.assertRaises(ValueError):
            setup.render_mcp_json(servers, "yaml")

    @unittest.skipIf(os.name == "nt", "the Windows desktop tool exists on Windows")
    def test_desktop_tool_stays_off_outside_windows(self):
        setup.launch_mcp(self.s | {"desktop": True}, "Codex", self.home)
        self.assertIs(self.asked[-1]["desktop"], False)

    def test_secret_store_rules(self):
        for name in ("PATH", "ANTHROPIC_API_KEY", "lower", "A B", "", None):
            with self.subTest(name=name), self.assertRaisesRegex(
                ValueError, "That variable name cannot be used"
            ):
                setup.store_secret(name, "v")
        for value in ("", "a\nb", "x" * 8193, None):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "Enter the value on one line."
            ):
                setup.store_secret("MY_TOKEN", value)
        self.backend = type("PlaintextKeyring", (), {})()
        with self.assertRaisesRegex(ValueError, "no system credential vault.*Set MY_TOKEN"):
            setup.store_secret("MY_TOKEN", "v")
        self.assertEqual(self.vault, {})
        self.backend = SimpleNamespace()
        self.assertTrue(setup.store_secret("MY_TOKEN", "v"))
        os.environ["DOCS_MCP_TOKEN"] = "set"
        self.assertEqual(
            setup.secret_status(["DOCS_MCP_TOKEN", "MY_TOKEN", "JUPYTER_TOKEN"]),
            {"DOCS_MCP_TOKEN": "env", "MY_TOKEN": "vault", "JUPYTER_TOKEN": "missing"},
        )
        self.assertTrue(setup.forget_secret("MY_TOKEN"))
        self.assertFalse(setup.forget_secret("MY_TOKEN"))
        broken = SimpleNamespace(get_password=lambda service, user: 1 / 0)
        with patch.dict(sys.modules, {"keyring": broken}):
            self.assertEqual(setup.secret_status(["MY_TOKEN"]), {"MY_TOKEN": "missing"})


class ResolveTest(SetupCase):
    def resolve(self, root, harness, version=""):
        return setup.resolve(root, harness, version, self.s | {"workspace": str(root)})

    def test_resolve_claude_code(self):
        a = self.folder("a")
        self.put("AGENTS.md", "# a\n", a)
        found = self.resolve(a, "Claude Code", "2.1.330 (Claude Code)")
        self.assertEqual(found["instructions"][0]["status"], "read")
        self.assertEqual(found["instructions"][0]["abs"], str(a / "AGENTS.md"))
        self.assertEqual((found["instructions"][0]["bytes"], found["instructions"][0]["lines"]), (4, 1))
        self.assertEqual(found["pointer"], {"needed": False, "path": "", "reason": ""})
        self.assertEqual(self.resolve(a, "Claude Code", "2.1.276")["instructions"][0]["status"], "ignored")
        old = self.resolve(a, "Claude Code")
        self.assertEqual(old["instructions"][0]["status"], "ignored")
        self.assertEqual(
            old["pointer"],
            {
                "needed": True,
                "path": "CLAUDE.md",
                "reason": "This Claude Code version does not read AGENTS.md by itself.",
            },
        )
        c = self.folder("c")
        self.put("AGENTS.md", "# c\n", c)
        self.put("CLAUDE.local.md", "mine\n", c)
        local = self.resolve(c, "Claude Code", "2.1.330")
        rows = self.rows(local["instructions"])
        self.assertEqual(rows["CLAUDE.local.md"]["status"], "read")
        self.assertEqual(rows["AGENTS.md"]["status"], "shadowed")
        self.assertEqual(rows["AGENTS.md"]["reason"], "Claude Code reads CLAUDE.local.md instead.")
        self.assertTrue(local["pointer"]["needed"])
        d = self.folder("d")
        self.put("AGENTS.md", "# d\n", d)
        self.put("CLAUDE.md", "intro\n  @AGENTS.md  \n", d)
        imported = self.resolve(d, "Claude Code", "")
        rows = self.rows(imported["instructions"])
        self.assertEqual(
            (rows["AGENTS.md"]["status"], rows["AGENTS.md"]["reason"]),
            ("read", "Imported by CLAUDE.md."),
        )
        self.assertFalse(imported["pointer"]["needed"])
        e = self.folder("e")
        self.put("AGENTS.md", "# e\n", e)
        self.put("CLAUDE.md", "See AGENTS.md for more.\n" * 201, e)
        plain = self.resolve(e, "Claude Code", "2.1.330")
        rows = self.rows(plain["instructions"])
        self.assertEqual(rows["AGENTS.md"]["status"], "shadowed")
        self.assertFalse(plain["pointer"]["needed"])
        self.assertIn(
            "CLAUDE.md exists, so Claude Code will not read AGENTS.md. "
            "Add the line @AGENTS.md at the top of CLAUDE.md.",
            plain["warnings"],
        )
        self.assertIn(
            "CLAUDE.md has 201 lines; Claude Code follows shorter files better (under 200).",
            plain["warnings"],
        )
        with self.assertRaisesRegex(ValueError, "Choose a supported agent."):
            self.resolve(a, "Cursor")

    def test_resolve_claude_code_parent_file_shadows(self):
        self.put("CLAUDE.md", "parent\n", self.tmp)
        self.put("AGENTS.md", "# work\n")
        found = self.resolve(self.work, "Claude Code", "2.1.330")
        rows = self.rows(found["instructions"])
        self.assertEqual(list(rows), ["../CLAUDE.md", "AGENTS.md"])
        self.assertEqual(rows["AGENTS.md"]["reason"], "Claude Code reads ../CLAUDE.md instead.")
        self.assertEqual(found["pointer"]["path"], "CLAUDE.md")

    def test_resolve_omp_shadowing(self):
        self.put("AGENTS.md", "# work\n")
        self.put("CLAUDE.md", "claude\n")
        self.put(".github/copilot-instructions.md", "copilot\n")
        found = self.resolve(self.work, "OMP")
        rows = self.rows(found["instructions"])
        self.assertEqual(rows[".github/copilot-instructions.md"]["status"], "read")
        self.assertEqual(rows["AGENTS.md"]["status"], "shadowed")
        self.assertEqual(
            rows["AGENTS.md"]["reason"],
            "OMP reads one instruction file per folder and "
            ".github/copilot-instructions.md comes first.",
        )
        self.assertEqual(rows["CLAUDE.md"]["status"], "shadowed")
        self.assertIn(
            "OMP will read .github/copilot-instructions.md instead of AGENTS.md in this folder. "
            "Move its content into AGENTS.md or remove it.",
            found["warnings"],
        )
        self.assertIn("OMP task subagents do not receive AGENTS.md.", found["notes"])
        self.assertEqual(found["pointer"]["needed"], False)

    def test_resolve_pi_codex_override(self):
        self.put("AGENTS.md", "# work\n")
        self.put("AGENTS.override.md", "override\n")
        for harness in ("Pi", "Codex"):
            found = self.resolve(self.work, harness)
            rows = self.rows(found["instructions"])
            self.assertEqual(rows["AGENTS.override.md"]["status"], "read", harness)
            self.assertEqual(rows["AGENTS.md"]["status"], "shadowed", harness)
            self.assertIn(
                f"{harness} will read AGENTS.override.md instead of AGENTS.md in this folder.",
                found["warnings"],
            )
        self.assertEqual(
            rows["AGENTS.md"]["reason"], "Codex reads AGENTS.override.md instead in this folder."
        )
        repo = self.folder("repo")
        (repo / ".git").mkdir()
        self.put("AGENTS.md", "x" * 40000 + "\n", repo)
        self.put("AGENTS.md", "# inner\n", repo / "inner")
        found = self.resolve(repo / "inner", "Codex")
        self.assertEqual(
            [(row["path"], row["status"]) for row in found["instructions"]],
            [("../AGENTS.md", "read"), ("AGENTS.md", "truncated")],
        )
        self.assertIn(
            "Codex stops reading instruction files after 32 KiB; AGENTS.md is past that limit.",
            found["warnings"],
        )
        pi = self.resolve(repo / "inner", "Pi")
        self.assertIn("@file lines in AGENTS.md are not expanded by Pi", pi["notes"][0])
        self.assertEqual(len([r for r in pi["instructions"] if r["status"] == "read"]), 2)

    def test_resolve_hermes_one_kind(self):
        self.put(".hermes.md", "hermes\n")
        self.put("AGENTS.md", "# work\n")
        self.put("CLAUDE.md", "claude\n")
        found = self.resolve(self.work, "Hermes")
        self.assertEqual(
            [(row["path"], row["status"]) for row in found["instructions"]],
            [(".hermes.md", "read"), ("AGENTS.md", "shadowed"), ("CLAUDE.md", "shadowed")],
        )
        self.assertEqual(
            found["instructions"][1]["reason"],
            "Hermes loads one kind of instruction file per session and .hermes.md comes first.",
        )
        self.assertIn("Hermes will read .hermes.md instead of AGENTS.md.", found["warnings"])
        self.assertIn("Hermes has no project MCP file.", found["notes"])
        (self.work / ".hermes.md").unlink()
        self.put("AGENTS.md", "y" * 20001)
        found = self.resolve(self.work, "Hermes")
        self.assertEqual(found["instructions"][0]["status"], "read")
        self.assertIn(
            "Hermes may shorten AGENTS.md on small context windows (limit from 20,000 characters).",
            found["warnings"],
        )

    def test_resolve_opencode_claude_fallback(self):
        alone = self.folder("alone")
        self.put("CLAUDE.md", "claude\n", alone)
        found = self.resolve(alone, "opencode")
        self.assertEqual(
            [(r["path"], r["status"]) for r in found["instructions"]], [("CLAUDE.md", "read")]
        )
        self.put("AGENTS.md", "# both\n", alone)
        rows = self.rows(self.resolve(alone, "opencode")["instructions"])
        self.assertEqual(rows["AGENTS.md"]["status"], "read")
        self.assertEqual(
            (rows["CLAUDE.md"]["status"], rows["CLAUDE.md"]["reason"]),
            ("shadowed", "opencode reads CLAUDE.md only in folders without AGENTS.md."),
        )

    def test_resolve_git_chain(self):
        repo = self.folder("repo")
        (repo / ".git").mkdir()
        inner = repo / "a" / "b"
        self.put("AGENTS.md", "# root\n", repo)
        self.put("AGENTS.md", "# nested\n", inner)
        self.put("AGENTS.md", "# above the checkout\n", self.tmp)
        found = self.resolve(inner, "Codex")
        self.assertEqual(found["git_root"], str(repo))
        self.assertEqual(
            [(row["path"], row["status"]) for row in found["instructions"]],
            [("../../AGENTS.md", "read"), ("AGENTS.md", "read")],
        )
        shutil.rmtree(repo / ".git")
        found = self.resolve(inner, "Codex")
        self.assertEqual(found["git_root"], "")
        self.assertEqual([row["path"] for row in found["instructions"]], ["AGENTS.md"])

    def test_resolve_skills(self):
        shared = ".agents/skills/workbench-verify-change/SKILL.md"
        self.put(shared, SKILL)
        claude = self.resolve(self.work, "Claude Code", "2.1.330")
        self.assertEqual(claude["skills"], [])
        self.assertIn(
            "Claude Code reads skills only from .claude/skills. "
            "Use Project setup to copy them there.",
            claude["warnings"],
        )
        self.put(".claude/skills/workbench-verify-change/SKILL.md", SKILL)
        self.put(".claude/skills/broken/SKILL.md", "No front matter here.\n")
        self.put(".claude/skills/not-a-skill/readme.md", "x")
        claude = self.resolve(self.work, "Claude Code", "2.1.330")
        self.assertEqual(
            [(row["name"], row["status"]) for row in claude["skills"]],
            [("broken", "invalid"), ("workbench-verify-change", "read")],
        )
        self.assertEqual(
            claude["skills"][0]["reason"], "SKILL.md needs a name and a description at the top."
        )
        self.assertEqual(
            claude["skills"][1]["description"], "Verify a code change before reporting done."
        )
        self.assertFalse([w for w in claude["warnings"] if "only from .claude/skills" in w])
        self.put(".omp/skills/workbench-verify-change/SKILL.md", SKILL + "Changed.\n")
        omp = self.resolve(self.work, "OMP")
        self.assertEqual(
            [(row["path"], row["status"]) for row in omp["skills"] if row["status"] != "invalid"],
            [
                (".omp/skills/workbench-verify-change/SKILL.md", "read"),
                (shared, "shadowed"),
                (".claude/skills/workbench-verify-change/SKILL.md", "shadowed"),
            ],
        )
        self.assertIn(
            "Another skill named workbench-verify-change in "
            ".omp/skills/workbench-verify-change/SKILL.md comes first.",
            omp["skills"][1]["reason"],
        )
        shutil.rmtree(self.work / ".omp")
        omp = self.resolve(self.work, "OMP")
        self.assertEqual(
            [row["status"] for row in omp["skills"] if row["status"] != "invalid"],
            ["read", "duplicate"],
        )
        hermes = self.resolve(self.work, "Hermes")
        self.assertEqual(hermes["skills"], [])
        self.assertIn(
            "Hermes loads project skills only inside a Git checkout. "
            "Run git init in this folder, or install the skills for your user.",
            hermes["warnings"],
        )
        (self.work / ".git").mkdir()
        hermes = self.resolve(self.work, "Hermes")
        self.assertEqual([row["path"] for row in hermes["skills"]], [shared])
        self.assertIn(
            "Run hermes skills trust in this folder once so Hermes loads its project skills.",
            hermes["warnings"],
        )
        pi = self.resolve(self.work, "Pi")
        self.assertIn(
            "Pi asks you to trust this project before it loads project skills.", pi["warnings"]
        )
        self.assertEqual([row["path"] for row in self.resolve(self.work, "Codex")["skills"]], [shared])

    def test_resolve_skill_scan_is_bounded(self):
        padding = "".join(f"key{n}: [a, b]\n" for n in range(700))
        self.assertGreater(len(padding), setup.MAX_FRONT)
        self.put(
            ".agents/skills/huge/SKILL.md",
            f"---\nname: huge\ndescription: Too much front matter.\n{padding}---\n",
        )
        for n in range(4):
            self.put(f".agents/skills/s{n}/SKILL.md", SKILL.replace("verify-change", f"s{n}"))
        with patch("yaml.safe_load") as parse:
            only = setup._front_matter((self.work / ".agents/skills/huge/SKILL.md").read_bytes())
        parse.assert_not_called()
        self.assertIsNone(only)
        found = self.resolve(self.work, "Codex")
        self.assertEqual(
            [(row["name"], row["status"]) for row in found["skills"]],
            [("huge", "invalid")] + [(f"workbench-s{n}", "read") for n in range(4)],
        )
        self.assertFalse([w for w in found["warnings"] if "skills" in w])
        with patch.object(setup, "MAX_SKILLS", 3):
            capped = self.resolve(self.work, "Codex")
        self.assertEqual(len(capped["skills"]), 3)
        self.assertIn(
            "This folder has more than 3 skills. "
            "Workbench lists the first 3; remove the ones you do not use.",
            capped["warnings"],
        )

    @unittest.skipIf(
        os.name == "nt" or getattr(os, "geteuid", lambda: 1)() == 0,
        "needs a folder this user cannot enter",
    )
    def test_resolve_and_plan_survive_a_folder_that_cannot_be_entered(self):
        skill = ".agents/skills/workbench-verify-change/SKILL.md"
        self.put(skill, SKILL)
        self.put(".claude/CLAUDE.md", "mine\n")
        self.put(".omp/hooks/run.ts", "x")
        for name in (".agents", ".claude", ".omp"):
            os.chmod(self.work / name, 0)
            self.addCleanup(os.chmod, self.work / name, 0o755)
        for harness in setup.HARNESSES:
            found = self.resolve(self.work, harness, "2.1.330")
            self.assertEqual((found["instructions"], found["skills"]), ([], []), harness)
        self.assertEqual(setup.consent_status(self.work)["items"], [])
        want = {"files": {"AGENTS.md": TEMPLATE, skill: SKILL}}
        plan = setup.plan(self.work, "Codex", want)
        reasons = {item["path"]: (item["action"], item["reason"]) for item in plan["items"]}
        self.assertEqual(reasons["AGENTS.md"], ("create", ""))
        self.assertEqual(
            reasons[skill],
            ("skip", f"{skill} cannot be reached. Check the permissions of its folder, then try again."),
        )
        with self.assertRaisesRegex(ValueError, "cannot be reached"):
            setup.read_file(self.work, ".claude/CLAUDE.md")

    def test_resolve_mcp_files_and_shadow(self):
        self.project({"playwright": PLAYWRIGHT, "docs": DOCS | {"enabled": False}})
        self.put(".mcp.json", {"mcpServers": {"github": {"command": "docker"}, "search": {"command": "x"}}})
        self.put(".omp/mcp.json", {"mcpServers": {"playwright": {"command": "other"}}})
        self.put(".codex/config.toml", "[mcp_servers.ctx]\ncommand = 'c'\n")
        self.put("opencode.json", "{ // comment\n}")
        self.allow()
        self.own = {"search": {"command": "python", "args": ["web_tools.py", "search"]}}
        claude = self.resolve(self.work, "Claude Code", "2.1.330")
        self.assertEqual(
            claude["mcp"]["files"],
            [
                {
                    "path": ".mcp.json",
                    "status": "asks",
                    "servers": ["github", "search"],
                    "reason": "Claude Code asks before it uses these.",
                }
            ],
        )
        self.assertEqual(
            claude["mcp"]["launch"],
            [
                {
                    "name": "search",
                    "origin": "builtin",
                    "transport": "stdio",
                    "summary": "python web_tools.py search",
                    "status": "sent",
                    "reason": "",
                },
                {
                    "name": "docs",
                    "origin": "project",
                    "transport": "http",
                    "summary": "https://mcp.example.com/mcp",
                    "status": "off",
                    "reason": "",
                },
                {
                    "name": "playwright",
                    "origin": "project",
                    "transport": "stdio",
                    "summary": "npx -y @playwright/mcp@0.0.86 --isolated",
                    "status": "sent",
                    "reason": "",
                },
            ],
        )
        self.assertIn("--mcp-config", claude["mcp"]["delivery"])
        self.assertIn(
            "Both Workbench and .mcp.json define search; Claude Code decides which one runs.",
            claude["warnings"],
        )
        self.assertEqual((claude["consent"]["needed"], claude["consent"]["granted"]), (False, True))
        self.assertFalse((self.data / "clients").exists())
        omp = self.resolve(self.work, "OMP")
        files = self.rows(omp["mcp"]["files"])
        self.assertEqual(
            (files[".mcp.json"]["status"], files[".mcp.json"]["reason"]),
            ("read", "OMP starts these without asking."),
        )
        self.assertEqual(files[".codex/config.toml"]["servers"], ["ctx"])
        self.assertEqual(
            (files["opencode.json"]["servers"], files["opencode.json"]["reason"]),
            ([], "Could not read the server names in this file."),
        )
        self.assertIn(
            "This folder's .omp/mcp.json defines playwright, "
            "which replaces the Workbench server of the same name in OMP.",
            omp["warnings"],
        )
        hermes = self.resolve(self.work, "Hermes")
        self.assertEqual(hermes["mcp"]["files"], [])
        reasons = {row["name"]: row["reason"] for row in hermes["mcp"]["launch"]}
        self.assertEqual(
            reasons["playwright"],
            "hermes mcp add playwright --command npx --args -y @playwright/mcp@0.0.86 --isolated",
        )
        self.assertEqual(reasons["docs"], "")
        with patch.object(runtime, "mcp_servers", side_effect=ValueError("Install uv first.")):
            failed = self.resolve(self.work, "Codex")
        self.assertIn("Install uv first.", failed["warnings"])
        self.assertEqual([row["name"] for row in failed["mcp"]["launch"]], ["docs", "playwright"])
        unlisted = (
            "Workbench could not list your tools and registered servers. "
            "Check Plugins, then reopen Project setup."
        )
        with patch.object(runtime, "mcp_servers", side_effect=AttributeError("split")):
            broken = self.resolve(self.work, "Codex")
        self.assertIn(unlisted, broken["warnings"])
        self.assertEqual([row["name"] for row in broken["mcp"]["launch"]], ["docs", "playwright"])
        damaged = dict.fromkeys(projects.CAPS, False) | {"workspace": str(self.work), "plugins": 5}
        with patch.object(runtime, "mcp_servers", REAL_MCP_SERVERS):
            odd = setup.resolve(self.work, "Codex", "", damaged)
        self.assertNotIn(unlisted, odd["warnings"])
        self.assertEqual([row["name"] for row in odd["mcp"]["launch"]], ["docs", "playwright"])
        default = setup.resolve(self.work, "Codex")
        self.assertEqual(default["workspace"], str(self.work))
        self.assertEqual(self.asked[-1]["workspace"], str(self.work))

    def test_portable_issues(self):
        self.assertEqual(setup.portable_issues(TEMPLATE), [])
        self.assertEqual(
            setup.portable_issues("# x\n@docs/rules.md\n```python\n@decorator\n```\nmail @ home\n"),
            ["Line 2 is an @import; only some agents expand it."],
        )
        big = setup.portable_issues("# x\n" + "word " * 7000)
        self.assertTrue([line for line in big if "32 KiB" in line])
        self.assertTrue([line for line in big if "20,000 characters" in line])
        long = setup.portable_issues("line\n" * 201)
        self.assertEqual(
            long, ["AGENTS.md has 201 lines; agents follow shorter files better (under 200)."]
        )
        self.put("AGENTS.md", "# x\n@other.md\n")
        found = setup.resolve(self.work, "Codex", "", self.s)
        self.assertIn("Line 2 is an @import; only some agents expand it.", found["warnings"])

    def test_read_file_allow_list(self):
        self.put("AGENTS.md", "# shop\r\nline\r\n")
        skill = ".agents/skills/workbench-verify-change/SKILL.md"
        self.put(skill, SKILL)
        self.put(".env", "SECRET=1")
        self.put(".git/config", "[core]")
        self.assertEqual(
            setup.read_file(self.work, "AGENTS.md"),
            {"path": "AGENTS.md", "exists": True, "text": "# shop\nline\n", "bytes": 12, "lines": 2},
        )
        self.assertEqual(setup.read_file(self.work, skill)["text"], SKILL)
        self.assertEqual(
            setup.read_file(self.work, "CLAUDE.md"),
            {"path": "CLAUDE.md", "exists": False, "text": ""},
        )
        self.assertFalse(setup.read_file(self.work, setup.PROJECT_FILE)["exists"])
        for rel in ("../x", ".env", ".git/config", "docs/AGENTS.md", "", None,
                    ".agents/skills/../../.env/SKILL.md", ".agents/skills/Up/SKILL.md"):
            with self.subTest(rel=rel), self.assertRaisesRegex(
                ValueError, "That file cannot be opened here."
            ):
                setup.read_file(self.work, rel)
        (self.work / "HERMES.md").write_bytes(b"\xff\xfe\x00")
        self.put(".hermes.md", "x" * (256 * 1024 + 1))
        for rel in ("HERMES.md", ".hermes.md"):
            with self.assertRaisesRegex(ValueError, "too large or is not text"):
                setup.read_file(self.work, rel)


class PlanTest(SetupCase):
    skill = ".agents/skills/workbench-verify-change/SKILL.md"

    def want(self, **changes):
        return {
            "files": {"AGENTS.md": TEMPLATE, self.skill: SKILL},
            "overwrite": [],
            "pointer": True,
            "mcp_set": {"playwright": PLAYWRIGHT},
            "mcp_remove": [],
            "mcp_keep_existing": True,
        } | changes

    def test_plan_create_skip_update(self):
        want = self.want()
        first = setup.plan(self.work, "Codex", want)
        self.assertEqual(
            [(i["path"], i["kind"], i["action"]) for i in first["items"]],
            [
                ("AGENTS.md", "instructions", "create"),
                (self.skill, "skill", "create"),
                (setup.PROJECT_FILE, "mcp", "create"),
            ],
        )
        self.assertEqual(first["items"][0]["content"], TEMPLATE)
        self.assertEqual(first["items"][0]["sha_after"], sha(TEMPLATE))
        self.assertEqual((first["workspace"], first["harness"]), (str(self.work), "Codex"))
        self.assertEqual(first["warnings"], [])
        self.assertEqual(setup.plan(self.work, "Codex", want)["id"], first["id"])
        self.assertFalse(list(self.work.iterdir()))
        done = self.apply_all("Codex", want)
        self.assertEqual(done["failed"], [])
        self.assertEqual(len(done["written"]), 3)
        second = setup.plan(self.work, "Codex", want)
        self.assertEqual(
            {(i["action"], i["reason"]) for i in second["items"]},
            {("skip", "Already up to date.")},
        )
        self.assertNotEqual(second["id"], first["id"])
        newer = self.want(files={"AGENTS.md": TEMPLATE + "\n## More\n", self.skill: SKILL})
        update = self.items(setup.plan(self.work, "Codex", newer))["AGENTS.md"]
        self.assertEqual(
            (update["action"], update["reason"]),
            ("update", "Workbench wrote this file earlier and it has not been changed since."),
        )
        self.assertIn("+## More", update["diff"])
        self.put("AGENTS.md", TEMPLATE + "\nMy own line.\n")
        kept = self.items(setup.plan(self.work, "Codex", newer))["AGENTS.md"]
        self.assertEqual(
            (kept["action"], kept["reason"], kept["content"]),
            ("skip", "AGENTS.md already exists. Your file is kept.", ""),
        )
        self.assertIn("-My own line.", kept["diff"])
        self.assertEqual(kept["sha_before"], sha(TEMPLATE + "\nMy own line.\n"))
        forced = self.items(
            setup.plan(self.work, "Codex", newer | {"overwrite": ["AGENTS.md"]})
        )["AGENTS.md"]
        self.assertEqual((forced["action"], forced["reason"]), ("update", ""))
        loose = setup.plan(self.work, "Codex", newer | {"overwrite": "AGENTS.md"})
        self.assertEqual(loose["items"][0]["action"], "skip")
        (self.work / "AGENTS.md").write_bytes(b"\xff\xfe binary")
        opaque = self.items(setup.plan(self.work, "Codex", newer | {"overwrite": ["AGENTS.md"]}))
        self.assertEqual(
            (opaque["AGENTS.md"]["action"], opaque["AGENTS.md"]["reason"]),
            ("skip", "AGENTS.md is not a text file Workbench can compare. Your file is kept."),
        )

    def test_diff_of_large_files_stays_fast_and_correct(self):
        old = "".join(f"{n}\n" for n in range(140000))
        new = "".join(f"{n}\n" for n in range(0, 20000, 2))
        self.put("AGENTS.md", old)
        want = {"files": {"AGENTS.md": new}, "overwrite": ["AGENTS.md"]}
        with patch.object(
            setup.difflib, "unified_diff", side_effect=AssertionError("aligned a huge file")
        ):
            item = setup.plan(self.work, "Codex", want)["items"][0]
        self.assertEqual(item["action"], "update")
        self.assertTrue(
            item["diff"].startswith("--- AGENTS.md\n+++ AGENTS.md\n@@ -2,139999 +2,9999 @@\n-1\n-2\n")
        )
        self.assertTrue(item["diff"].endswith("\n… diff shortened"))
        self.assertLessEqual(len(item["diff"]), setup.MAX_DIFF + 20)
        with patch.object(setup, "DIFF_WORK", 0):
            self.assertEqual(
                setup._diff("a\nb\nc\nd\n", "a\nx\ny\nd\n", "f"),
                "--- f\n+++ f\n@@ -2,2 +2,2 @@\n-b\n-c\n+x\n+y",
            )
            self.assertEqual(
                setup._diff("a\nb\n", "a\nnew\nb\n", "f"), "--- f\n+++ f\n@@ -1,0 +2 @@\n+new"
            )
            self.assertEqual(
                setup._diff("a\ngone\nb\n", "a\nb\n", "f"), "--- f\n+++ f\n@@ -2 +1,0 @@\n-gone"
            )
            self.assertEqual(setup._diff("a\nb", "a\nb\n", "f"), "")
        self.assertEqual(
            setup._diff("a\nb\n", "a\nc\n", "f"), "--- f\n+++ f\n@@ -1,2 +1,2 @@\n a\n-b\n+c"
        )

    def test_plan_limits(self):
        for files in (
            {".env": "x"},
            {".git/hooks/pre-commit": "x"},
            {"../AGENTS.md": "x"},
            {".agents/skills/con/SKILL.md": "x"},
            {".agents/skills/a b/SKILL.md": "x"},
            {"AGENTS.md": "x" * (64 * 1024 + 1)},
            {"AGENTS.md": 5},
            {"AGENTS.md": "lone \ud800 surrogate"},
            {"AGENTS.md": "nul \0 byte"},
            {f".agents/skills/s{n}/SKILL.md": "x" for n in range(65)},
        ):
            with self.subTest(files=list(files)[:1]), self.assertRaisesRegex(
                ValueError, "That plan holds a file Workbench will not write."
            ):
                setup.plan(self.work, "Codex", {"files": files})
        with self.assertRaisesRegex(ValueError, "Choose a supported agent."):
            setup.plan(self.work, "Nope", {"files": {}})
        with self.assertRaisesRegex(ValueError, "bad: "):
            setup.plan(self.work, "Codex", {"mcp_set": {"bad": {"transport": "pipe"}}})
        self.assertEqual(setup.plan(self.work, "Codex", {})["items"], [])

    def test_plan_mirrors_skills_only_for_claude(self):
        want = self.want(mcp_set={})
        claude = self.items(setup.plan(self.work, "Claude Code", want, "2.1.330"))
        mirror = ".claude/skills/workbench-verify-change/SKILL.md"
        self.assertEqual(list(claude), ["AGENTS.md", self.skill, mirror])
        self.assertEqual(claude[mirror]["content"], claude[self.skill]["content"])
        self.assertEqual(claude[mirror]["kind"], "skill")
        codex = self.items(setup.plan(self.work, "Codex", want))
        self.assertEqual(list(codex), ["AGENTS.md", self.skill])
        self.apply_all("Claude Code", want, version="2.1.330")
        self.assertEqual(self.text(mirror), SKILL)
        self.assertFalse((self.work / ".claude" / "skills").is_symlink())

    def test_plan_pointer_only_when_needed(self):
        want = self.want(files={"AGENTS.md": TEMPLATE}, mcp_set={})
        old = setup.plan(self.work, "Claude Code", want, "")
        self.assertEqual(
            [(i["path"], i["kind"], i["action"], i["content"]) for i in old["items"]][1],
            ("CLAUDE.md", "pointer", "create", "@AGENTS.md\n"),
        )
        new = setup.plan(self.work, "Claude Code", want, "2.1.330")
        self.assertEqual([i["path"] for i in new["items"]], ["AGENTS.md"])
        self.assertEqual(
            [i["path"] for i in setup.plan(self.work, "Codex", want, "")["items"]], ["AGENTS.md"]
        )
        declined = setup.plan(self.work, "Claude Code", want | {"pointer": False}, "")
        self.assertEqual([i["path"] for i in declined["items"]], ["AGENTS.md"])
        self.put("CLAUDE.local.md", "mine\n")
        local = setup.plan(self.work, "Claude Code", want, "2.1.330")
        self.assertEqual([i["path"] for i in local["items"]], ["AGENTS.md", "CLAUDE.md"])
        self.put("CLAUDE.md", "my own\n")
        mine = setup.plan(self.work, "Claude Code", want, "")
        self.assertEqual([i["path"] for i in mine["items"]], ["AGENTS.md"])
        self.assertEqual(self.text("CLAUDE.md"), "my own\n")

    def test_plan_project_json_merge(self):
        mine = PLAYWRIGHT | {"args": ["-y", "@playwright/mcp@0.0.1"]}
        self.project({"playwright": mine, "old": DOCS}, team="web")
        want = self.want(files={}, mcp_set={"playwright": PLAYWRIGHT, "docs": DOCS})
        kept = self.items(setup.plan(self.work, "Codex", want))[setup.PROJECT_FILE]
        self.assertEqual(
            (kept["action"], kept["kind"], kept["reason"]),
            ("update", "mcp", "Kept your existing definition of playwright."),
        )
        merged = json.loads(kept["content"])
        self.assertEqual(merged["team"], "web")
        self.assertEqual(list(merged["mcp"]), ["docs", "old", "playwright"])
        self.assertEqual(merged["mcp"]["playwright"]["args"], mine["args"])
        self.assertIn('+    "docs": {', kept["diff"])
        replaced = self.items(
            setup.plan(
                self.work, "Codex", want | {"mcp_keep_existing": False, "mcp_remove": ["old"]}
            )
        )[setup.PROJECT_FILE]
        self.assertEqual(replaced["reason"], "")
        merged = json.loads(replaced["content"])
        self.assertEqual(list(merged["mcp"]), ["docs", "playwright"])
        self.assertEqual(merged["mcp"]["playwright"]["args"], PLAYWRIGHT["args"])
        self.assertEqual(merged["team"], "web")
        only_remove = setup.plan(self.work, "Codex", {"mcp_remove": ["old"]})
        self.assertEqual(list(json.loads(only_remove["items"][0]["content"])["mcp"]), ["playwright"])
        self.put(setup.PROJECT_FILE, "{broken")
        broken = setup.plan(self.work, "Codex", want)
        self.assertEqual(
            (broken["items"][0]["action"], broken["items"][0]["reason"]), ("skip", setup.UNREADABLE)
        )
        self.assertEqual(broken["warnings"], [setup.UNREADABLE])
        self.assertEqual(self.text(setup.PROJECT_FILE), "{broken")

    def test_apply_writes_only_confirmed(self):
        want = self.want()
        plan = setup.plan(self.work, "Codex", want)
        with self.assertRaisesRegex(ValueError, "This folder changed after you reviewed the plan"):
            setup.apply(self.work, "Codex", want, "0" * 64, ["AGENTS.md"])
        with self.assertRaises(ValueError):
            setup.apply(self.work, "Codex", want, None, ["AGENTS.md"])
        self.assertFalse(list(self.work.iterdir()))
        done = setup.apply(self.work, "Codex", want, plan["id"], ["AGENTS.md", self.skill])
        self.assertEqual(done["written"], ["AGENTS.md", self.skill])
        self.assertEqual(
            done["skipped"], [{"path": setup.PROJECT_FILE, "reason": "Not selected."}]
        )
        self.assertEqual(done["failed"], [])
        self.assertFalse((self.work / ".agent-workbench").exists())
        raw = (self.work / "AGENTS.md").read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), plan["items"][0]["sha_after"])
        self.assertEqual(
            projects.written(self.work), {"AGENTS.md": sha(TEMPLATE), self.skill: sha(SKILL)}
        )
        left = [p.name for p in self.work.rglob("*") if p.name.endswith(".tmp")]
        self.assertEqual(left, [])
        with self.assertRaises(ValueError):
            setup.apply(self.work, "Codex", want, plan["id"], [setup.PROJECT_FILE])
        self.assertEqual(done["consent"]["items"], [])

    def test_apply_reports_a_file_that_changed(self):
        want = self.want(files={"AGENTS.md": TEMPLATE}, mcp_set={})
        plan = setup.plan(self.work, "Codex", want)
        real = setup.plan

        def racing(*args, **kwargs):
            found = real(*args, **kwargs)
            self.put("AGENTS.md", "written by someone else\n")
            return found

        with patch.object(setup, "plan", side_effect=racing):
            done = setup.apply(self.work, "Codex", want, plan["id"], ["AGENTS.md"])
        self.assertEqual(
            (done["written"], done["skipped"]),
            ([], [{"path": "AGENTS.md", "reason": "AGENTS.md changed while applying. Review the plan again."}]),
        )
        self.assertEqual(self.text("AGENTS.md"), "written by someone else\n")
        fresh = self.folder("fresh")
        plan = setup.plan(fresh, "Codex", want)
        with patch.object(setup.os, "replace", side_effect=PermissionError("in use")):
            done = setup.apply(fresh, "Codex", want, plan["id"], ["AGENTS.md"])
        self.assertEqual(
            done["failed"],
            [
                {
                    "path": "AGENTS.md",
                    "reason": "Could not write AGENTS.md. Close programs that have it open, then apply again.",
                }
            ],
        )
        self.assertEqual(list(fresh.iterdir()), [])
        self.assertEqual(projects.written(fresh), {})

    def test_apply_leaves_no_temporary_file_beside_a_read_only_target(self):
        """Windows refuses to replace or delete a read-only file; both refusals are imitated."""
        target = self.put("AGENTS.md", "mine\n")
        os.chmod(target, 0o444)
        self.addCleanup(os.chmod, target, 0o644)
        want = self.want(files={"AGENTS.md": TEMPLATE}, overwrite=["AGENTS.md"], mcp_set={})
        plan = setup.plan(self.work, "Codex", want)
        self.assertEqual(self.items(plan)["AGENTS.md"]["action"], "update")
        refused = PermissionError(13, "Access is denied")
        real = Path.unlink

        def unlink(path, missing_ok=False):
            if path.exists() and not path.stat().st_mode & stat.S_IWRITE:
                raise refused
            return real(path, missing_ok=missing_ok)

        with (
            patch.object(setup.os, "replace", side_effect=refused),
            patch.object(Path, "unlink", unlink),
        ):
            done = setup.apply(self.work, "Codex", want, plan["id"], ["AGENTS.md"])
        self.assertEqual([row["path"] for row in done["failed"]], ["AGENTS.md"])
        self.assertEqual(done["written"], [])
        self.assertEqual([p.name for p in self.work.iterdir()], ["AGENTS.md"])
        self.assertEqual(self.text("AGENTS.md"), "mine\n")

    @unittest.skipIf(os.name == "nt", "symbolic links need extra rights on Windows")
    def test_apply_refuses_links(self):
        outside = self.folder("outside")
        (self.work / ".agents").symlink_to(outside, target_is_directory=True)
        (self.work / "AGENTS.md").symlink_to(outside / "target.md")
        want = self.want(mcp_set={})
        plan = setup.plan(self.work, "Codex", want)
        found = self.items(plan)
        self.assertEqual(found[self.skill]["action"], "skip")
        self.assertIn(".agents goes through a link", found[self.skill]["reason"])
        self.assertEqual(found["AGENTS.md"]["action"], "skip")
        self.assertIn("AGENTS.md goes through a link", found["AGENTS.md"]["reason"])
        done = setup.apply(self.work, "Codex", want, plan["id"], ["AGENTS.md", self.skill])
        self.assertEqual((done["written"], done["skipped"], done["failed"]), ([], [], []))
        self.assertEqual(list(outside.iterdir()), [])
        with self.assertRaisesRegex(ValueError, "goes through a link"):
            setup.read_file(self.work, "AGENTS.md")
        clean = self.folder("clean")
        plan = setup.plan(clean, "Codex", want)
        real = setup.plan

        def racing(*args, **kwargs):
            found = real(*args, **kwargs)
            (clean / ".agents").symlink_to(outside, target_is_directory=True)
            return found

        with patch.object(setup, "plan", side_effect=racing):
            done = setup.apply(clean, "Codex", want, plan["id"], [self.skill])
        self.assertEqual(done["written"], [])
        reasons = {row["path"]: row["reason"] for row in done["skipped"]}
        self.assertIn("goes through a link", reasons[self.skill])
        self.assertEqual(list(outside.iterdir()), [])

    def test_apply_keeps_consent_current(self):
        self.project({"docs": DOCS})
        self.allow()
        want = self.want(files={"AGENTS.md": TEMPLATE})
        done = self.apply_all("Codex", want)
        self.assertIn(setup.PROJECT_FILE, done["written"])
        self.assertEqual((done["consent"]["needed"], done["consent"]["granted"]), (False, True))
        self.assertFalse(setup.consent_status(self.work)["needed"])
        other = self.folder("other")
        self.project({"docs": DOCS}, other)
        done = self.apply_all("Codex", want, other)
        self.assertIn(setup.PROJECT_FILE, done["written"])
        self.assertTrue(done["consent"]["needed"])
        fresh = self.folder("fresh")
        done = self.apply_all("Codex", want, fresh)
        self.assertEqual((done["consent"]["needed"], done["consent"]["granted"]), (False, True))
        self.put(".mcp.json", {"mcpServers": {"sneaky": {"command": "sh"}}})
        wanted = self.want(files={}, mcp_set={"docs": DOCS, "extra": PLAYWRIGHT})
        done = self.apply_all("Codex", wanted)
        self.assertIn(setup.PROJECT_FILE, done["written"])
        self.assertTrue(done["consent"]["needed"])

    def test_export_json_and_toml(self):
        self.project({"docs": DOCS, "jupyter": JUPYTER, "off": PLAYWRIGHT | {"enabled": False}})
        self.put(".mcp.json", {"mcpServers": {"docs": {"command": "mine"}}, "other": True})
        claude = setup.export_plan(self.work, "Claude Code")
        item = claude["items"][0]
        self.assertEqual((item["path"], item["kind"], item["action"]), (".mcp.json", "mcp", "update"))
        merged = json.loads(item["content"])
        self.assertEqual(merged["mcpServers"]["docs"], {"command": "mine"})
        self.assertEqual(merged["mcpServers"]["jupyter"]["env"]["JUPYTER_TOKEN"], "${JUPYTER_TOKEN}")
        self.assertEqual(merged["other"], True)
        self.assertNotIn("off", merged["mcpServers"])
        self.assertIn('+    "jupyter": {', item["diff"])
        with self.assertRaisesRegex(ValueError, "This folder changed after you reviewed"):
            setup.export_apply(self.work, "Claude Code", "stale", [".mcp.json"])
        done = setup.export_apply(self.work, "Claude Code", claude["id"], [".mcp.json"])
        self.assertEqual(done["written"], [".mcp.json"])
        self.assertEqual(json.loads(self.text(".mcp.json")), merged)
        self.assertIn(".mcp.json", projects.written(self.work))
        again = setup.export_plan(self.work, "Claude Code")["items"][0]
        self.assertEqual((again["action"], again["reason"]), ("skip", "Already lists these servers."))
        self.put(".codex/config.toml", 'model = "x"\n\n[mcp_servers.docs]\nurl = "https://mine/mcp"\n')
        codex = setup.export_plan(self.work, "Codex")["items"][0]
        self.assertEqual((codex["path"], codex["action"]), (".codex/config.toml", "update"))
        parsed = tomllib.loads(codex["content"])
        self.assertEqual(parsed["model"], "x")
        self.assertEqual(parsed["mcp_servers"]["docs"], {"url": "https://mine/mcp"})
        self.assertEqual(
            parsed["mcp_servers"]["jupyter"],
            {
                "command": "uvx",
                "args": ["jupyter-mcp-server@2.2.6"],
                "env_vars": ["JUPYTER_TOKEN"],
                "env": {"JUPYTER_URL": "http://127.0.0.1:8888"},
            },
        )
        (self.work / ".codex" / "config.toml").unlink()
        created = setup.export_plan(self.work, "Codex")["items"][0]
        self.assertEqual(created["action"], "create")
        self.assertEqual(
            tomllib.loads(created["content"])["mcp_servers"]["docs"],
            {
                "url": "https://mcp.example.com/mcp",
                "bearer_token_env_var": "DOCS_MCP_TOKEN",
                "env_http_headers": {"X-Workspace": "DOCS_MCP_WORKSPACE"},
            },
        )
        self.put(".codex/config.toml", "mcp_servers = { other = { command = 'x' } }\n")
        unsafe = setup.export_plan(self.work, "Codex")["items"][0]
        self.assertEqual(
            (unsafe["action"], unsafe["reason"], unsafe["content"]),
            ("skip", "Workbench could not add to .codex/config.toml safely. Add the lines shown yourself.", ""),
        )
        self.assertIn("+[mcp_servers.jupyter]", unsafe["diff"])
        self.put(".codex/config.toml", "not = = toml")
        self.assertEqual(
            setup.export_plan(self.work, "Codex")["items"][0]["reason"],
            ".codex/config.toml could not be read, so Workbench will not add to it.",
        )
        self.put("opencode.json", '{\n  // my comment\n  "mcp": {}\n}\n')
        commented = setup.export_plan(self.work, "opencode")["items"][0]
        self.assertEqual(
            (commented["action"], commented["reason"]),
            ("skip", "opencode.json is not plain JSON, so Workbench will not rewrite it."),
        )
        (self.work / "opencode.json").unlink()
        opencode = setup.export_plan(self.work, "opencode")["items"][0]
        self.assertEqual(opencode["action"], "create")
        self.assertEqual(json.loads(opencode["content"])["mcp"]["docs"]["type"], "remote")
        pi = setup.export_plan(self.work, "Pi")["items"][0]
        self.assertEqual((pi["path"], pi["action"]), (".pi/mcp.json", "create"))
        self.assertNotIn("type", json.loads(pi["content"])["mcpServers"]["docs"])
        self.assertEqual(setup.export_plan(self.work, "OMP")["items"][0]["path"], ".omp/mcp.json")
        hermes = setup.export_plan(self.work, "Hermes")
        self.assertEqual(len(hermes["items"]), 1)
        self.assertEqual((hermes["items"][0]["path"], hermes["items"][0]["action"]), ("", "skip"))
        self.assertEqual(
            hermes["items"][0]["reason"],
            "Hermes has no project file. Run: "
            "hermes mcp add docs --url https://mcp.example.com/mcp\n"
            "hermes mcp add jupyter --command uvx --args jupyter-mcp-server@2.2.6",
        )
        self.assertEqual(
            setup.export_apply(self.work, "Hermes", hermes["id"], [""])["written"], []
        )
        projects.set_project_mcp(self.work, "docs", False)
        projects.set_project_mcp(self.work, "jupyter", False)
        for harness in ("Claude Code", "Hermes"):
            with self.assertRaisesRegex(
                ValueError, "Turn on at least one project server before exporting."
            ):
                setup.export_plan(self.work, harness)

    def test_export_skips_a_json_file_it_cannot_write_back(self):
        self.project({"docs": DOCS})
        self.put(".mcp.json", '{"mcpServers": {}, "note": "\\ud800"}')
        item = setup.export_plan(self.work, "Claude Code")["items"][0]
        self.assertEqual(
            (item["action"], item["reason"], item["content"]),
            ("skip", ".mcp.json is not plain JSON, so Workbench will not rewrite it.", ""),
        )

    def test_export_keeps_consent_current(self):
        self.project({"docs": DOCS})
        self.allow()
        plan = setup.export_plan(self.work, "Claude Code")
        done = setup.export_apply(self.work, "Claude Code", plan["id"], [".mcp.json"])
        self.assertEqual(done["written"], [".mcp.json"])
        self.assertEqual((done["consent"]["needed"], done["consent"]["granted"]), (False, True))
        self.assertEqual(
            [item["path"] for item in done["consent"]["items"]], [setup.PROJECT_FILE, ".mcp.json"]
        )


class DispatchTest(SetupCase):
    def test_dispatch_routes_every_action(self):
        base = {"workspace": str(self.work), "harness": "Codex"}
        request = base | {"agents_md": TEMPLATE, "mcp_set": {"docs": DOCS | {"enabled": False}}}
        plan = setup.dispatch("project-setup-plan", request)
        self.assertEqual(
            [(i["path"], i["action"]) for i in plan["items"]],
            [("AGENTS.md", "create"), (setup.PROJECT_FILE, "create")],
        )
        done = setup.dispatch(
            "project-setup-apply",
            request | {"plan": plan["id"], "confirmed": ["AGENTS.md", setup.PROJECT_FILE]},
        )
        self.assertEqual(done["written"], ["AGENTS.md", setup.PROJECT_FILE])
        edited = setup.dispatch("project-setup-plan", base | {"agents_md": "# mine\n"})
        self.assertEqual(edited["items"][0]["action"], "update")
        self.assertEqual(
            setup.dispatch("project-setup-read", base | {"path": "AGENTS.md"})["text"], TEMPLATE
        )
        listing = setup.dispatch("project-setup-mcp", base)
        self.assertEqual(
            (listing["path"], listing["exists"], listing["errors"]),
            (setup.PROJECT_FILE, True, []),
        )
        self.assertEqual(
            listing["servers"],
            [
                {
                    "name": "docs",
                    "transport": "http",
                    "command": "",
                    "args": [],
                    "env": {},
                    "env_from": [],
                    "url": "https://mcp.example.com/mcp",
                    "header_env": {"X-Workspace": "DOCS_MCP_WORKSPACE"},
                    "bearer_env": "DOCS_MCP_TOKEN",
                    "enabled": False,
                    "summary": "https://mcp.example.com/mcp",
                    "secrets": {"DOCS_MCP_WORKSPACE": "missing", "DOCS_MCP_TOKEN": "missing"},
                }
            ],
        )
        toggled = setup.dispatch(
            "project-setup-mcp-toggle", base | {"name": "docs", "enabled": True}
        )
        self.assertTrue(toggled["servers"][0]["enabled"])
        with self.assertRaises(ValueError):
            setup.dispatch("project-setup-mcp-toggle", base | {"name": "docs", "enabled": "yes"})
        self.assertTrue(
            setup.dispatch("project-setup-secret-save", {"name": "DOCS_MCP_TOKEN", "value": "v"})
        )
        self.assertEqual(
            setup.dispatch("project-setup-mcp", base)["servers"][0]["secrets"]["DOCS_MCP_TOKEN"],
            "vault",
        )
        self.assertTrue(setup.dispatch("project-setup-secret-forget", {"name": "DOCS_MCP_TOKEN"}))
        status = setup.dispatch("project-setup-consent", base)
        self.assertEqual((status["needed"], status["granted"]), (False, True))
        self.assertTrue(setup.dispatch("project-setup-consent-revoke", base))
        status = setup.dispatch("project-setup-consent", {"workspace": str(self.work)})
        self.assertTrue(status["needed"])
        self.assertTrue(
            setup.dispatch("project-setup-consent-grant", base | {"digest": status["digest"]})
        )
        export = setup.dispatch("project-setup-export-plan", base)
        self.assertEqual(export["items"][0]["path"], ".codex/config.toml")
        sent = setup.dispatch(
            "project-setup-export-apply",
            base | {"plan": export["id"], "confirmed": [".codex/config.toml"]},
        )
        self.assertEqual(sent["written"], [".codex/config.toml"])
        found = setup.dispatch("project-setup-resolve", base | {"cli_version": "0.171.0"})
        self.assertEqual(found["harness"], "Codex")
        self.assertIs(self.asked[-1]["search"], True)
        setup.dispatch("project-setup-resolve", base | {"search": False, "plugins": "gone"})
        self.assertIs(self.asked[-1]["search"], False)
        self.assertEqual(self.asked[-1]["plugins"], "")
        for action, payload in (
            ("project-setup-nope", base),
            ("project-setup-resolve", {"harness": "Codex"}),
            ("project-setup-resolve", {"workspace": str(self.work)}),
            ("project-setup-plan", base | {"agents_md": 5}),
            ("project-setup-plan", base | {"mcp_set": ["docs"]}),
            ("project-setup-plan", base | {"mcp_remove": "docs"}),
            ("project-setup-read", base),
            ("project-setup-consent-grant", base),
        ):
            with self.subTest(action=action), self.assertRaises(ValueError):
                setup.dispatch(action, payload)
        with self.assertRaisesRegex(ValueError, "Unknown project setup action."):
            setup.dispatch("kit-list", {})


if __name__ == "__main__":
    unittest.main()
