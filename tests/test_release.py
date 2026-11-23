import atexit
import concurrent.futures
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

import yaml

if "WORKBENCH_DATA" not in os.environ:
    os.environ["WORKBENCH_DATA"] = tempfile.mkdtemp(prefix="awb-test-")
    atexit.register(shutil.rmtree, os.environ["WORKBENCH_DATA"], ignore_errors=True)

import catalog
import desktop_app
import runtime

ROOT = Path(__file__).resolve().parents[1]
SH = shutil.which("sh")
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
HAS_GIT = (ROOT / ".git").exists() and bool(shutil.which("git"))
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
REGENERATE = "python release/build.py --write-manifest"
spec = importlib.util.spec_from_file_location("release_build", ROOT / "release/build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
FAKE_PYTHON = """#!/bin/sh
case "$1" in
  -c) exit 0 ;;
  -m) case "$2" in venv) mkdir -p "$3/bin" && cp "$0" "$3/bin/python" && : > "$3/pyvenv.cfg" ;; esac ;;
  *) echo "ran $* data=$WORKBENCH_DATA caller=$WORKBENCH_CALLER_CWD" ;;
esac
"""


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args]).decode()


def listed():
    return (ROOT / build.MANIFEST).read_text(encoding="utf8").splitlines()


def tar_bytes(files, links=()):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as tar:
        for name, (content, mode) in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(content), mode, 5
            tar.addfile(info, io.BytesIO(content))
        for name, target in links:
            info = tarfile.TarInfo(name)
            info.type, info.linkname = tarfile.SYMTYPE, target
            tar.addfile(info)
    return data.getvalue()


def fake_kit(folder, target, version="1"):
    """Write a kit archive and SHA256SUMS laid out the way release/build.py writes them."""
    files = {
        name: (ROOT / name).read_bytes()
        for name in ("workbench.sh", "Launch Workbench.command", "install.sh", "setup-linux.sh")
    }
    files |= {
        "requirements.lock": f"fixture=={version}\n".encode(),
        "desktop_app.py": b"",
        "VERSION": f"{version}\n".encode(),
        "python/bin/python3.12": FAKE_PYTHON.encode(),
    }
    name = f"agent-workbench-{target}.tar.gz"
    with tarfile.open(folder / name, "w:gz") as tar:
        for path, data in files.items():
            info = tarfile.TarInfo("Agent-Workbench/" + path)
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256((folder / name).read_bytes()).hexdigest()
    (folder / "SHA256SUMS").write_text(f"{digest}  {name}\n")


def windows_kit(dist):
    pack = dist / "agent-workbench-windows-x64.zip"
    with zipfile.ZipFile(pack, "w") as z:
        for name in ("Run.cmd", "desktop_app.py", "requirements.lock", "python/python.exe", "install.ps1"):
            z.writestr("Agent-Workbench/" + name, "")
    return pack, hashlib.sha256(pack.read_bytes()).hexdigest()


def shipped_sources():
    names = listed()
    return {p: (ROOT / p).read_bytes() for p in names if p.endswith(build.SCANNED)}, set(names)


class ManifestTest(unittest.TestCase):
    def test_manifest_is_valid(self):
        self.assertEqual(build.manifest_errors(listed()), [])

    def test_private_hidden_and_generated_paths_are_rejected(self):
        for bad in (
            "private/notes.md",
            "docs/launch-posts.md",
            ".env",
            ".local/settings.json",
            "../outside.txt",
            "/etc/passwd",
            "a\\b.txt",
            "docs/",
            "VERSION",
            "python/bin/python3.12",
            "desktop/.npmrc",
            "tests/.agent-workbench/profiles.json",
            "kits/general/.claude/settings.local.json",
            "docs/.DS_Store",
            ".github/.env",
        ):
            errors = build.manifest_errors(sorted({".gitattributes", "README.md", bad}))
            self.assertEqual(len(errors), 1, (bad, errors))
            self.assertTrue(bad in errors[0] or repr(bad) in errors[0], (bad, errors))
        for bad in ([".gitattributes", "b.txt", "a.txt"], [".gitattributes", "a.txt", "a.txt"], []):
            self.assertIn("must be sorted by code point", " ".join(build.manifest_errors(bad)), bad)
        self.assertEqual(
            build.manifest_errors([".gitattributes", ".github/CODEOWNERS", ".github/workflows/test.yml", ".gitignore", "README.md"]), []
        )
        self.assertEqual(len(build.manifest_errors(["README.md"])), 1)

    def test_private_files_and_promotional_drafts_are_never_listed(self):
        self.assertIn("private/", build.EXCLUDED)
        self.assertIn("docs/launch-posts.md", build.EXCLUDED)
        names = listed()
        self.assertEqual([p for p in names if build.excluded(p)], [])
        self.assertEqual([p for p in names if p.startswith("private/") or p == "docs/launch-posts.md"], [])
        on_disk = [p.relative_to(ROOT).as_posix() for p in (ROOT / "private").rglob("*") if p.is_file()]
        self.assertTrue(all(build.excluded(p) for p in on_disk), on_disk)

    def test_every_listed_file_exists(self):
        missing = [p for p in listed() if not (ROOT / p).is_file()]
        self.assertEqual(missing, [], f"Listed but not on disk; run: {REGENERATE}")

    def test_shipped_modules_import_only_shipped_files(self):
        files, names = shipped_sources()
        known = set(build.tree_files()) if HAS_GIT else names
        self.assertEqual(build.dependency_errors(files, names, known), [])

    def test_dependency_check_names_what_is_missing(self):
        files = {
            "cli.py": b"import os, json as j\nfrom runtime import DATA\nimport yaml\ndef f():\n    import doctor\n",
            "tests/test_cli.py": b"import cli\nimport sys, helper\nfrom . import x\n",
            "studio.py": b"from paths import (\n    canonical,\n)\nimport providers.table as t\n",
            "desktop/index.html": b'<script src="ui/a.js"></script><link href="css/b.css"><a href="#x"></a>'
            b'<a href="https://example.com/"></a><img src="data:image/png;base64,AA">',
            "desktop/main.cjs": b'const a = require("./actions.json"); const b = require("./client"); require("node:path");',
            "desktop/css/b.css": b'@import "base.css"; .x { background: url("../img/x.png"); }',
        }
        shipped = set(files) | {"runtime.py", "desktop/ui/a.js", "desktop/client.cjs", "desktop/css/base.css"}
        known = shipped | {"doctor.py", "tests/helper.py", "paths.py", "providers/__init__.py", "private/notes.py"}
        self.assertEqual(
            build.dependency_errors(files, shipped, known),
            [
                "cli.py imports doctor, but doctor.py is not listed in release/public-files.txt.",
                "desktop/css/b.css refers to ../img/x.png, but desktop/img/x.png is not listed in release/public-files.txt.",
                "desktop/main.cjs refers to ./actions.json, but desktop/actions.json is not listed in release/public-files.txt.",
                "studio.py imports paths, but paths.py is not listed in release/public-files.txt.",
                "studio.py imports providers, but providers/__init__.py is not listed in release/public-files.txt.",
                "tests/test_cli.py imports helper, but tests/helper.py is not listed in release/public-files.txt.",
            ],
        )

    def test_generator_takes_the_tree_and_leaves_out_exclusions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in (".gitattributes", "a.py", "kits/x/kit.json", "private/notes.md", "docs/launch-posts.md"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("x")
            listing = "\0".join(
                ["a.py", "kits/x/kit.json", "deleted.py", "private/notes.md", "docs/launch-posts.md", ".gitattributes", "a.py", ""]
            ).encode()
            target = root / build.MANIFEST
            target.parent.mkdir()
            target.write_text(".gitattributes\ndeleted.py\n")
            with patch.object(build, "ROOT", root), patch.object(build, "git", return_value=listing) as g:
                self.assertEqual(build.write_manifest(target), (["a.py", "kits/x/kit.json"], ["deleted.py"]))
                self.assertEqual(g.call_args.args, ("ls-files", "-z", "--cached", "--others", "--exclude-standard"))
                self.assertEqual(target.read_bytes(), b".gitattributes\na.py\nkits/x/kit.json\n")
                (root / ".env").write_text("SECRET=1")
                (root / "kits/x/.npmrc").write_text("//registry.npmjs.org/:_authToken=x")
                for leak, sentence in ((b".env", r"\.env is a hidden path"), (b"kits/x/.npmrc", r"kits/x/\.npmrc is a hidden path")):
                    with (
                        patch.object(build, "git", return_value=listing + leak + b"\0"),
                        self.assertRaisesRegex(ValueError, sentence + ".*EXCLUDED"),
                    ):
                        build.write_manifest(target)
            self.assertEqual(target.read_bytes(), b".gitattributes\na.py\nkits/x/kit.json\n")

    @unittest.skipUnless(HAS_GIT, "needs the Git checkout")
    def test_every_file_is_listed_or_excluded(self):
        present = git("ls-files", "--cached", "--others", "--exclude-standard").splitlines()
        present = {p for p in present if (ROOT / p).is_file() and not build.excluded(p)}
        names = set(listed())
        self.assertEqual(
            sorted(present - names),
            [],
            f"Run: {REGENERATE}, or add a file that must stay private to EXCLUDED in release/build.py",
        )
        self.assertEqual(sorted(names - present), [], f"Listed but not on disk; run: {REGENERATE}")

    @unittest.skipUnless(HAS_GIT, "needs the Git checkout")
    def test_listed_files_hold_no_personal_address_or_home_folder(self):
        people = set(git("log", "--format=%ae%n%ce").split())
        needles = {a for a in people if not a.endswith("@users.noreply.github.com")}
        needles.add(str(Path.home()))
        for name in listed():
            data = (ROOT / name).read_bytes()
            for needle in needles:
                self.assertNotIn(needle.encode(), data, f"{name} contains {needle}")

    def test_git_attributes_keep_windows_scripts_crlf(self):
        rules = (ROOT / ".gitattributes").read_text().splitlines()
        for rule in ("*.cmd text eol=crlf", "*.vbs text eol=crlf", "*.sh text eol=lf", "*.command text eol=lf"):
            self.assertIn(rule, rules)
        ignored = (ROOT / ".gitignore").read_text().splitlines()
        for rule in (".local/", ".venv/", ".venv-linux/", "python/", "dist/"):
            self.assertIn(rule, ignored)


class BuilderTest(unittest.TestCase):
    def test_members_rejects_unsafe_entries(self):
        def names(data, runtime=False):
            with tarfile.open(fileobj=io.BytesIO(data)) as arc:
                return [e.name for e in build.members(arc, runtime)]

        ok = {"python/bin/python3.12": (b"x", 0o755)}
        self.assertEqual(
            names(tar_bytes(ok, [("python/bin/python3", "python3.12")]), True),
            ["python/bin/python3.12", "python/bin/python3"],
        )
        for files, links, runtime in (
            ({"../x": (b"", 0o644)}, (), False),
            ({"/x": (b"", 0o644)}, (), False),
            ({"a\\b": (b"", 0o644)}, (), False),
            ({"a/./b": (b"", 0o644)}, (), False),
            ({}, [("link", "target")], False),
            ({"other/x": (b"", 0o644)}, (), True),
            ({}, [("python/bin/x", "../../../etc/passwd")], True),
        ):
            with self.assertRaises(ValueError, msg=str((files, links))):
                names(tar_bytes(files, links), runtime)

    def test_package_writes_stamped_reproducible_archives(self):
        stamp = 1700000000
        sources = tar_bytes({"Run.cmd": (b"@echo off\r\n", 0o644), "workbench.sh": (b"#!/bin/sh\n", 0o755)})
        extra = {"VERSION": b"9.9.9\nabc\n"}
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            runtime = tmp / "runtime.tar"
            runtime.write_bytes(tar_bytes({"python/bin/python3.12": (b"x", 0o755)}))
            built = {}
            for name in ("one", "two"):
                (tmp / name).mkdir()
                built[name] = [
                    build.package(target, tmp / name, sources, None if target == "source" else runtime, stamp, extra)
                    for target in ("source", "linux-x64", "windows-x64")
                ]
            for a, b in zip(built["one"], built["two"]):
                self.assertEqual(a.read_bytes(), b.read_bytes(), a.name)
            self.assertEqual(
                [p.name for p in built["one"]],
                ["agent-workbench-source.tar.gz", "agent-workbench-linux-x64.tar.gz", "agent-workbench-windows-x64.zip"],
            )
            with tarfile.open(built["one"][1]) as tar:
                entries = {e.name: e for e in tar}
                self.assertEqual(
                    sorted(entries),
                    ["Agent-Workbench/" + n for n in ("Run.cmd", "VERSION", "python/bin/python3.12", "workbench.sh")],
                )
                self.assertEqual({(e.mtime, e.uid, e.gid, e.uname) for e in entries.values()}, {(stamp, 0, 0, "")})
                self.assertEqual(entries["Agent-Workbench/workbench.sh"].mode, 0o755)
                self.assertEqual(tar.extractfile("Agent-Workbench/VERSION").read(), b"9.9.9\nabc\n")
            with tarfile.open(built["one"][0]) as tar:
                self.assertNotIn("Agent-Workbench/python/bin/python3.12", tar.getnames())
            with zipfile.ZipFile(built["one"][2]) as z:
                self.assertEqual({i.create_system for i in z.infolist()}, {3})
                self.assertEqual({i.date_time for i in z.infolist()}, {time.gmtime(stamp)[:6]})
                self.assertEqual(z.read("Agent-Workbench/Run.cmd"), b"@echo off\r\n")
                self.assertEqual(z.getinfo("Agent-Workbench/workbench.sh").external_attr >> 16, 0o100755)
                self.assertIn("Agent-Workbench/VERSION", z.namelist())

    def test_cached_runtime_with_a_wrong_checksum_is_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            triple = build.TARGETS["linux-x64"][0]
            bad = Path(tmp) / f"cpython-{build.PYTHON}+{build.BUILD}-{triple}-install_only.tar.gz"
            bad.write_bytes(b"not the archive")
            with self.assertRaisesRegex(ValueError, "was deleted"):
                build.fetch("linux-x64", Path(tmp))
            self.assertFalse(bad.exists())

    @unittest.skipUnless(HAS_GIT, "needs the Git checkout")
    def test_worktree_snapshot_leaves_the_repository_untouched(self):
        before = git("status", "--porcelain"), git("count-objects", "-v")
        with tempfile.TemporaryDirectory() as tmp:
            tree, env = build.snapshot([".gitattributes", "LICENSE", "Run.cmd"], Path(tmp))
            data = build.git("archive", "--format=tar", tree, env=env)
        with tarfile.open(fileobj=io.BytesIO(data)) as arc:
            self.assertEqual(sorted(arc.getnames()), [".gitattributes", "LICENSE", "Run.cmd"])
            self.assertIn(b"\r\n", arc.extractfile("Run.cmd").read())
        self.assertEqual((git("status", "--porcelain"), git("count-objects", "-v")), before)

    @unittest.skipUnless(HAS_GIT, "needs the Git checkout")
    def test_archive_modes_do_not_depend_on_the_builders_git_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "gitconfig"
            config.write_text("[tar]\n\tumask = 0077\n[core]\n\tautocrlf = true\n")
            data = build.archive("HEAD", ["LICENSE", "Run.cmd", "workbench.sh"], dict(os.environ, GIT_CONFIG_GLOBAL=str(config)))
        with tarfile.open(fileobj=io.BytesIO(data)) as arc:
            modes = {e.name: e.mode for e in arc}
            self.assertEqual(modes, {"LICENSE": 0o644, "Run.cmd": 0o644, "workbench.sh": 0o755})
            self.assertNotIn(b"\r\n", arc.extractfile("workbench.sh").read())
            self.assertIn(b"\r\n", arc.extractfile("Run.cmd").read())


class VersionTest(unittest.TestCase):
    def test_one_version_everywhere(self):
        self.assertRegex(build.VERSION, r"^\d+\.\d+\.\d+$")
        stated = {
            "install.sh": re.search(r'^VERSION="([^"]+)"$', (ROOT / "install.sh").read_text(), re.M).group(1),
            "install.ps1": re.search(r"^\$Version = '([^']+)'$", (ROOT / "install.ps1").read_text(), re.M).group(1),
            "desktop/package.json": json.loads((ROOT / "desktop/package.json").read_text())["version"],
        }
        self.assertEqual(stated, dict.fromkeys(stated, build.VERSION))

    def test_one_repository_name_everywhere(self):
        owner = build.REPOSITORY.split("/")[0]
        sh = re.search(r'^REPOSITORY="([^"]+)"$', (ROOT / "install.sh").read_text(), re.M).group(1)
        ps = re.search(r"^\$Repository = '([^']+)'$", (ROOT / "install.ps1").read_text(), re.M).group(1)
        self.assertEqual((sh, ps), (build.REPOSITORY, build.REPOSITORY))
        for name in ("release/README.md", ".github/ISSUE_TEMPLATE/config.yml", "install.sh", "install.ps1"):
            text = (ROOT / name).read_text(encoding="utf8")
            for found in re.findall(r"github\.com/(" + re.escape(owner) + r"/[\w.-]+)", text):
                self.assertEqual(found, build.REPOSITORY, name)


class LockTest(unittest.TestCase):
    def test_lock_pins_every_requirement_with_hashes(self):
        def key(name):
            return re.sub(r"[-_.]+", "-", name).lower()

        wanted = {}
        for line in (ROOT / "requirements.txt").read_text().splitlines():
            m = re.match(r"([A-Za-z0-9_.-]+)==([^\s;]+)", line)
            self.assertTrue(m, f"requirements.txt line is not an exact pin: {line}")
            wanted[key(m.group(1))] = m.group(2)
        lock = (ROOT / "requirements.lock").read_text()
        self.assertIn(
            "uv pip compile requirements.txt --universal --generate-hashes --python-version 3.12 -o requirements.lock",
            lock,
        )
        blocks = re.split(r"\n(?=[A-Za-z0-9])", "\n" + "\n".join(l for l in lock.splitlines() if not l.startswith("#")))
        pins = {}
        for block in filter(None, (b.strip() for b in blocks)):
            m = re.match(r"([A-Za-z0-9_.-]+)==([^\s;\\]+)", block)
            self.assertTrue(m, block[:60])
            self.assertIn("--hash=sha256:", block, m.group(1))
            pins[key(m.group(1))] = m.group(2)
        self.assertEqual({k: pins.get(k) for k in wanted}, wanted)
        self.assertGreater(len(pins), len(wanted))

    def test_launchers_and_ci_install_only_from_the_lock(self):
        for name in ("workbench.sh", "Run.cmd", ".github/workflows/test.yml"):
            text = (ROOT / name).read_text()
            self.assertIn("--require-hashes --only-binary :all: -r requirements.lock", text, name)
            self.assertNotIn("-r requirements.txt", text, name)


class WorkflowTest(unittest.TestCase):
    def load(self, name):
        return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())

    def test_ci_runs_on_push_and_pull_request_on_three_systems_with_time_limits(self):
        ci = self.load("test.yml")
        triggers = ci[True]
        self.assertEqual(triggers["push"], {"branches": ["main"]})
        self.assertIn("pull_request", triggers)
        self.assertIn("workflow_call", triggers)
        self.assertEqual(ci["permissions"], {"contents": "read"})
        self.assertEqual(
            set(ci["jobs"]["test"]["strategy"]["matrix"]["os"]),
            {"windows-latest", "ubuntu-latest", "macos-latest"},
        )
        for name, job in ci["jobs"].items():
            self.assertLessEqual(job["timeout-minutes"], 30, name)

    def test_ci_runs_every_desktop_unit_test(self):
        steps = self.load("test.yml")["jobs"]["test"]["steps"]
        run = next(s["run"] for s in steps if s.get("name") == "Run the desktop unit tests")
        self.assertEqual(run, "node --test tests/*.test.cjs")
        self.assertTrue((ROOT / "tests/bridge-client.test.cjs").is_file())

    def test_required_checks_match_the_test_job_names(self):
        ci = self.load("test.yml")
        self.assertEqual(ci["jobs"]["test"]["name"], "test (${{ matrix.os }})")
        rules = json.loads((ROOT / ".github/main-ruleset.json").read_text())["rules"]
        checks = next(r for r in rules if r["type"] == "required_status_checks")
        self.assertEqual(
            {c["context"] for c in checks["parameters"]["required_status_checks"]},
            {f"test ({o})" for o in ci["jobs"]["test"]["strategy"]["matrix"]["os"]},
        )

    def test_every_action_is_pinned_to_a_commit(self):
        for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
            text = path.read_text()
            uses = re.findall(r"uses:\s*(\S+)\s*#\s*v\d", text)
            local = re.findall(r"uses:\s*(\./\.github/workflows/[\w-]+\.yml)\s*$", text, re.M)
            self.assertEqual(len(uses) + len(local), text.count("uses:"), path.name)
            self.assertTrue(uses, path.name)
            for ref in uses:
                self.assertRegex(ref, r"^actions/[\w-]+@[0-9a-f]{40}$", path.name)
            for ref in local:
                self.assertTrue((ROOT / ref).is_file(), ref)

    def test_checkouts_keep_no_token_and_piped_steps_stop_on_any_failure(self):
        for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
            for name, job in yaml.safe_load(path.read_text())["jobs"].items():
                for step in job.get("steps", []):
                    if step.get("uses", "").startswith("actions/checkout@"):
                        self.assertIs(step.get("with", {}).get("persist-credentials"), False, f"{path.name} {name}")
                    if re.search(r"(?<!\|)\|(?!\|)", step.get("run", "")):
                        self.assertIn(step.get("shell"), ("bash", "pwsh"), f"{path.name} {name}: {step.get('name')}")

    def test_release_workflow_builds_attests_and_only_drafts(self):
        text = (ROOT / ".github/workflows/release.yml").read_text()
        release = yaml.safe_load(text)
        self.assertEqual(release[True], {"push": {"tags": ["v*"]}})
        self.assertEqual(release["permissions"], {"contents": "read"})
        jobs = release["jobs"]
        job = jobs["build"]
        self.assertEqual(
            job["permissions"],
            {"contents": "write", "id-token": "write", "attestations": "write", "artifact-metadata": "write"},
        )
        self.assertEqual([n for n, j in jobs.items() if "permissions" in j], ["build"])
        self.assertIn("subject-checksums: dist/SHA256SUMS", text)
        self.assertEqual(text.count("gh release create"), 1)
        self.assertIn("--draft", text)
        self.assertNotIn("pull_request_target", text)
        for name, j in jobs.items():
            if "runs-on" in j:
                self.assertLessEqual(j["timeout-minutes"], 30, name)

    def test_release_runs_only_after_the_full_test_workflow_and_only_from_main(self):
        jobs = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())["jobs"]
        self.assertEqual(jobs["test"], {"needs": "check", "uses": "./.github/workflows/test.yml"})
        self.assertEqual(set(jobs["build"]["needs"]), {"check", "test"})
        checkout = jobs["check"]["steps"][0]
        self.assertEqual(checkout["with"]["fetch-depth"], 0)
        runs = "\n".join(s.get("run", "") for s in jobs["check"]["steps"])
        self.assertIn('git merge-base --is-ancestor "$GITHUB_SHA" refs/remotes/origin/main', runs)
        self.assertIn('test "$GITHUB_REF_NAME" = "v$(python', runs)
        self.assertNotIn("${{", runs)

    def test_smoke_test_cannot_pass_silently(self):
        smoke = (ROOT / "tests/desktop-smoke.cjs").read_text()
        for needed in (
            'process.on("exit"',
            "Promise.race([test(), aborted])",
            "process.exit(passed ? 0 : 1)",
            ".venv-linux/bin/python",
            "passed = true;",
        ):
            self.assertTrue(needed in smoke, f"tests/desktop-smoke.cjs does not contain {needed!r}")


class InstallerTextTest(unittest.TestCase):
    def test_shell_installer_is_pipe_safe_and_never_elevates(self):
        text = (ROOT / "install.sh").read_text()
        self.assertEqual(text.rstrip().splitlines()[-1], 'main "$@"')
        self.assertEqual(text.count("sudo"), 1)
        self.assertIn("No step uses sudo.", text)
        self.assertNotIn("eval ", text)
        self.assertIn("--proto '=https'", text)
        self.assertIn("releases/download/v$VERSION", text)

    def test_powershell_installer_verifies_and_never_elevates(self):
        text = (ROOT / "install.ps1").read_text()
        for needed in (
            "Get-FileHash -LiteralPath $pack -Algorithm SHA256",
            "releases/download/v$Version",
            "Set-ItemProperty -Type ExpandString",
            "[System.Text.Encoding]::ASCII",
            '"`r`n"',
        ):
            self.assertIn(needed, text)
        for banned in ("Invoke-Expression", "iex ", "RunAs", "Set-ExecutionPolicy"):
            self.assertNotIn(banned, text)


@unittest.skipIf(os.name == "nt" or not SH, "POSIX installer")
class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home dir"
        self.home.mkdir()
        self.dist = self.tmp / "dist"
        self.dist.mkdir()
        self.target = "macos-arm64" if sys.platform == "darwin" else "linux-x64"
        fake_kit(self.dist, self.target)
        self.app = self.home / ".local/lib/agent-workbench"
        self.command = self.home / ".local/bin/workbench"
        self.data = (
            self.home / "Library/Application Support/Agent Workbench"
            if sys.platform == "darwin"
            else self.home / ".local/share/agent-workbench"
        )

    def install(self, *args, **env):
        base = {
            "PATH": SYSTEM_PATH,
            "HOME": str(self.home),
            "SHELL": "/bin/zsh",
            "WORKBENCH_INSTALL_TARGET": self.target,
        }
        return subprocess.run(
            [SH, str(ROOT / "install.sh"), *args],
            cwd=self.tmp,
            env=base | env,
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    def leftovers(self):
        return list(self.app.parent.glob(".agent-workbench-install.*"))

    def test_local_install_verifies_and_adds_a_command(self):
        r = self.install("--from", str(self.dist))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SHA-256 verified", r.stdout)
        self.assertTrue((self.app / "workbench.sh").is_file())
        self.assertEqual(self.data.stat().st_mode & 0o777, 0o700)
        self.assertIn("is not on your PATH", r.stdout)
        self.assertFalse((self.home / ".zshrc").exists())
        ran = subprocess.run(
            [str(self.command), "status"],
            cwd=self.tmp,
            env={"PATH": "/usr/bin:/bin", "HOME": str(self.home)},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn(f"ran cli.py status data={self.data} caller={self.tmp}", ran.stdout)
        self.assertEqual(self.leftovers(), [])

    def test_download_mode_fetches_the_pinned_release_then_verifies(self):
        fakes = self.tmp / "fakes"
        fakes.mkdir()
        (fakes / "curl").write_text(
            f'#!/bin/sh\necho "$*" >>"{self.tmp}/calls"\nout=; url=; prev=\n'
            'for a in "$@"; do\n  case "$prev" in --output) out=$a ;; esac\n  url=$a; prev=$a\ndone\n'
            f'cp "{self.dist}/${{url##*/}}" "$out"\n'
        )
        (fakes / "curl").chmod(0o755)
        r = self.install(PATH=f"{fakes}:{SYSTEM_PATH}")
        self.assertEqual(r.returncode, 0, r.stderr)
        base = f"https://github.com/{build.REPOSITORY}/releases/download/v{build.VERSION}"
        calls = (self.tmp / "calls").read_text().splitlines()
        self.assertEqual([c.rsplit(" ", 1)[1] for c in calls], [f"{base}/SHA256SUMS", f"{base}/agent-workbench-{self.target}.tar.gz"])
        self.assertTrue(all(c.startswith("--fail --silent --show-error --location --proto =https --tlsv1.2") for c in calls))
        self.assertIn("SHA-256 verified", r.stdout)
        self.assertTrue((self.app / "workbench.sh").is_file())

    def test_wrong_checksum_installs_nothing(self):
        sums = self.dist / "SHA256SUMS"
        sums.write_text("0" * 64 + sums.read_text()[64:])
        r = self.install("--from", str(self.dist))
        self.assertEqual(r.returncode, 1)
        self.assertIn("does not match its published SHA-256", r.stderr)
        self.assertFalse(self.app.exists())
        self.assertFalse(self.command.exists())
        self.assertEqual(self.leftovers(), [])

    def test_missing_entry_and_missing_files_are_named(self):
        (self.dist / "SHA256SUMS").write_text("")
        self.assertIn("no usable entry", self.install("--from", str(self.dist)).stderr)
        r = self.install("--from", str(self.tmp / "nowhere"))
        self.assertIn("must contain SHA256SUMS", r.stderr)
        self.assertFalse(self.app.exists())

    def test_upgrade_and_uninstall_keep_data(self):
        self.install("--from", str(self.dist))
        (self.data / "settings.json").write_text("{}")
        (self.app / ".local").mkdir()
        (self.app / ".local/settings.json").write_text('{"kept": true}')
        (self.app / ".venv-linux").mkdir()
        fake_kit(self.dist, self.target, version="2")
        r = self.install("--from", str(self.dist))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Replaced the previous version", r.stdout)
        self.assertEqual((self.app / "VERSION").read_text(), "2\n")
        self.assertFalse((self.app / ".venv-linux").exists())
        self.assertEqual((self.app / ".local/settings.json").read_text(), '{"kept": true}')
        r = self.install("--uninstall")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Move that folder somewhere safe", r.stderr)
        shutil.rmtree(self.app / ".local")
        r = self.install("--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.app.exists())
        self.assertFalse(self.command.exists())
        self.assertEqual((self.data / "settings.json").read_text(), "{}")

    def test_foreign_folder_and_foreign_command_are_left_alone(self):
        self.app.mkdir(parents=True)
        (self.app / "notes.txt").write_text("mine")
        r = self.install("--from", str(self.dist))
        self.assertEqual(r.returncode, 1)
        self.assertIn("is not an Agent Workbench install", r.stderr)
        self.assertEqual((self.app / "notes.txt").read_text(), "mine")
        shutil.rmtree(self.app)
        self.command.parent.mkdir(parents=True)
        self.command.write_text("#!/bin/sh\necho other tool\n")
        r = self.install("--from", str(self.dist))
        self.assertEqual(r.returncode, 1)
        self.assertIn("was not written by this installer", r.stderr)
        self.assertEqual(self.command.read_text(), "#!/bin/sh\necho other tool\n")
        self.assertFalse(self.app.exists())

    def test_path_line_is_added_only_with_consent(self):
        r = self.install("--from", str(self.dist), "--yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        rc = (self.home / ".zshrc").read_text()
        self.assertIn("# Added by the Agent Workbench installer", rc)
        self.assertIn(f"export PATH='{self.command.parent}':\"$PATH\"", rc)
        self.install("--from", str(self.dist), "--yes")
        self.assertEqual((self.home / ".zshrc").read_text(), rc)

    def test_unknown_kit_and_unknown_option_are_named(self):
        r = self.install("--from", str(self.dist), WORKBENCH_INSTALL_TARGET="windows-x64")
        self.assertIn("There is no kit named windows-x64", r.stderr)
        r = self.install("--force")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Unknown option --force", r.stderr)

    def test_printed_uninstall_line_works_for_custom_folders(self):
        app, bins = self.tmp / "custom app", self.tmp / "custom bin"
        r = self.install("--from", str(self.dist), WORKBENCH_INSTALL_DIR=str(app), WORKBENCH_BIN_DIR=str(bins))
        self.assertEqual(r.returncode, 0, r.stderr)
        plain = self.install("--uninstall")
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertIn(f"No Agent Workbench install was found in {self.app}", plain.stdout)
        self.assertTrue((app / "workbench.sh").is_file())
        hint = [line for line in r.stdout.splitlines() if line.startswith("To uninstall: ")][0]
        gone = subprocess.run(
            [SH, "-c", hint.removeprefix("To uninstall: ")],
            cwd=self.tmp,
            env={"PATH": SYSTEM_PATH, "HOME": str(self.home)},
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.assertEqual(gone.returncode, 0, gone.stderr)
        self.assertNotIn("No Agent Workbench install was found", gone.stdout)
        self.assertFalse(app.exists())
        self.assertFalse((bins / "workbench").exists())

    def test_source_checkout_is_never_replaced_or_removed(self):
        (self.app / ".git").mkdir(parents=True)
        for name in ("workbench.sh", "desktop_app.py", ".git/HEAD"):
            (self.app / name).write_text("mine\n")
        r = self.install("--from", str(self.dist))
        self.assertEqual(r.returncode, 1)
        self.assertIn("is not an Agent Workbench install", r.stderr)
        r = self.install("--uninstall")
        self.assertEqual(r.returncode, 1)
        self.assertIn("so it was left alone", r.stderr)
        self.assertEqual((self.app / ".git/HEAD").read_text(), "mine\n")
        self.assertEqual((self.app / "workbench.sh").read_text(), "mine\n")
        self.assertEqual(self.leftovers(), [])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can write a read-only file")
    def test_profile_that_cannot_be_written_does_not_fail_the_install(self):
        profile = self.home / ".zshrc"
        profile.write_text("# mine\n")
        profile.chmod(0o444)
        r = self.install("--from", str(self.dist), "--yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        self.assertIn(f"{profile} could not be changed, so PATH was left alone.", r.stdout)
        self.assertIn("add this line to your shell profile", r.stdout)
        self.assertIn("To uninstall: ", r.stdout)
        self.assertEqual(profile.read_text(), "# mine\n")

    def test_from_needs_a_value_and_takes_a_folder_that_starts_with_a_dash(self):
        fakes = self.tmp / "fakes"
        fakes.mkdir()
        for tool in ("curl", "wget"):
            (fakes / tool).write_text(f'#!/bin/sh\necho called >>"{self.tmp}/network"\nexit 1\n')
            (fakes / tool).chmod(0o755)
        r = self.install("--from", "", PATH=f"{fakes}:{SYSTEM_PATH}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--from needs a folder", r.stderr)
        self.assertFalse((self.tmp / "network").exists())
        self.assertFalse(self.app.exists())
        self.dist.rename(self.tmp / "-dist")
        r = self.install("--from", "-dist")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        self.assertTrue((self.app / "workbench.sh").is_file())


@unittest.skipUnless(POWERSHELL, "needs PowerShell")
class PowerShellInstallerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dist = self.tmp / "dist"
        self.dist.mkdir()
        self.pack, self.digest = windows_kit(self.dist)
        self.base = self.tmp / "Agent Workbench"
        (self.tmp / "home").mkdir()
        self.env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.tmp / "home"),
            "POWERSHELL_TELEMETRY_OPTOUT": "1",
            "WORKBENCH_INSTALL_DIR": str(self.base),
            "WORKBENCH_NO_MODIFY_PATH": "1",
            "WORKBENCH_NO_SHORTCUT": "1",
        }
        if os.name == "nt":
            self.env = dict(os.environ, **self.env)
        else:
            self.env |= dict(OS="Windows_NT", LOCALAPPDATA=str(self.tmp / "local"), PROCESSOR_ARCHITECTURE="AMD64")

    def run_ps(self, *args, command=None, **more):
        start = ["-Command", command] if command else ["-File", str(ROOT / "install.ps1"), *args]
        return subprocess.run(
            [POWERSHELL, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", *start],
            cwd=self.tmp,
            env=self.env | more,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_local_install_wrong_checksum_and_uninstall(self):
        (self.dist / "SHA256SUMS").write_text("0" * 64 + f"  {self.pack.name}\n")
        r = self.run_ps("-From", "dist")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("does not match its published SHA-256", r.stdout)
        self.assertFalse((self.base / "app").exists())
        (self.dist / "SHA256SUMS").write_text(f"{self.digest}  {self.pack.name}\n")
        r = self.run_ps("-From", "dist")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.base / "app/Run.cmd").is_file())
        self.assertTrue((self.base / "data").is_dir())
        command = (self.base / "bin/workbench.cmd").read_bytes()
        command.decode("ascii")
        self.assertEqual(command.count(b"\r\n"), command.count(b"\n"))
        self.assertIn(b'call "%base%\\app\\Run.cmd" cli.py %*', command)
        self.assertFalse((self.base / ".install").exists())
        self.assertIn(f"To uninstall, keep WORKBENCH_INSTALL_DIR set to {self.base} and run: powershell", r.stdout)
        r = self.run_ps("-Uninstall")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("No Agent Workbench install was found", r.stdout)
        self.assertFalse((self.base / "app").exists())
        self.assertTrue((self.base / "data").is_dir())
        r = self.run_ps("-Uninstall")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"No Agent Workbench install was found in {self.base}", r.stdout)

    def test_empty_from_and_an_uncreatable_folder_are_named(self):
        offline = "function Invoke-WebRequest { throw 'no network in tests' }; & $env:INSTALLER -From ''; exit $LASTEXITCODE"
        r = self.run_ps(command=offline, INSTALLER=str(ROOT / "install.ps1"))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("-From needs a folder", r.stdout)
        self.assertNotIn("Downloading", r.stdout)
        self.assertFalse(self.base.exists())
        if os.name != "nt":
            blocked = self.tmp / "blocked"
            blocked.mkdir()
            blocked.chmod(0o555)
            try:
                r = self.run_ps("-From", "dist", WORKBENCH_INSTALL_DIR=str(blocked / "base"))
            finally:
                blocked.chmod(0o755)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("could not be created. Make its parent folder writable", r.stdout)


class ReleaseTest(unittest.TestCase):
    def test_shared_memory_estimate(self):
        with patch.object(catalog, "unified_memory", return_value=True):
            self.assertEqual(
                catalog.fit(22 * catalog.GIB, 48, 48), "Unified memory candidate"
            )
            self.assertEqual(
                catalog.fit(60 * catalog.GIB, 48, 48), "Exceeds memory estimate"
            )

    def test_macos_packaged_electron_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "package.json").write_text('{"dependencies":{"electron":"45.4.1"}}')
            exe = (
                base
                / "desktop-runtime/darwin-45.4.1/Electron.app/Contents/MacOS/Electron"
            )
            exe.parent.mkdir(parents=True)
            exe.touch()
            with (
                patch.object(desktop_app, "DATA", base),
                patch.object(desktop_app, "DESKTOP", base),
                patch.object(sys, "platform", "darwin"),
                patch("platform.machine", return_value="arm64"),
            ):
                self.assertEqual(desktop_app.executable(), exe)

    def test_concurrent_key_creation_and_private_profile(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runtime, "DATA", Path(tmp)),
        ):
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                keys = list(pool.map(lambda _: runtime.key(), range(18)))
            self.assertEqual(len(set(keys)), 1)
            self.assertGreaterEqual(len(keys[0]), 40)
            profile = Path(tmp) / "models.json"
            runtime.write_json(profile, {"provider": "example"})
            if os.name != "nt":
                self.assertEqual(
                    (Path(tmp) / "api-key.txt").stat().st_mode & 0o777, 0o600
                )
                self.assertEqual(profile.stat().st_mode & 0o777, 0o600)

            else:
                import win32security as sec

                sd = sec.GetNamedSecurityInfo(
                    str(Path(tmp) / "api-key.txt"),
                    sec.SE_FILE_OBJECT,
                    sec.DACL_SECURITY_INFORMATION,
                )
                self.assertTrue(
                    sd.GetSecurityDescriptorControl()[0] & sec.SE_DACL_PROTECTED
                )
                acl = sd.GetSecurityDescriptorDacl()
                self.assertEqual(acl.GetAceCount(), 3)
                self.assertFalse(
                    any(acl.GetAce(i)[0][1] & 0x10 for i in range(acl.GetAceCount()))
                )
