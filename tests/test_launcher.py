import atexit
import base64
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

if "runtime" not in sys.modules and "WORKBENCH_DATA" not in os.environ:
    os.environ["WORKBENCH_DATA"] = tempfile.mkdtemp(prefix="awb-test-")
    atexit.register(shutil.rmtree, os.environ["WORKBENCH_DATA"], ignore_errors=True)

import desktop_bridge as bridge
import project_profiles as projects
import provider_auth
import runtime


class LauncherTest(unittest.TestCase):
    def test_omp_overlay_keeps_onboarding_and_isolates_different_toolsets(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(Path, "home", return_value=Path(tmp)),
            patch("runtime.shutil.which", return_value="omp"),
            patch("runtime.key", return_value="local"),
        ):
            s = runtime.DEFAULTS | dict(workspace=tmp, search=True, firecrawl=False)
            cmd = runtime.omp_command(s)
            overlay = Path(cmd[cmd.index("--config") + 1])
            pref = overlay.with_name("config.yml")
            pref.write_text("setupVersion: 3\ntheme: dark\n")
            self.assertEqual(runtime.omp_command(s), cmd)
            self.assertIn("theme: dark", pref.read_text())
            cfg = runtime.load(overlay, {})
            self.assertFalse(cfg["startup"]["setupWizard"])
            self.assertFalse(cfg["web_search"]["enabled"])
            self.assertFalse(cfg["tools"]["commands"])
            other = runtime.omp_command(s | {"search": False})
            self.assertNotEqual(other[2], cmd[2])
            self.assertIn(
                "search", runtime.load(overlay.with_name("mcp.json"), {})["mcpServers"]
            )

    def test_server_reuse_compares_effective_settings(self):
        s = runtime.DEFAULTS | dict(model="weights.strata", engine="Auto")
        rec = {"spec": runtime.serving_spec(s | dict(engine="Strata", lora=False))}
        self.assertTrue(runtime.same_server(s, rec))
        self.assertFalse(runtime.same_server(s | dict(context=65536), rec))

    def test_folder_profile_can_move_and_keeps_credentials_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a", Path(tmp) / "b"
            a.mkdir()
            b.mkdir()
            weight = b / "weights.gguf"
            weight.touch()
            s = runtime.DEFAULTS | dict(
                workspace=str(a),
                model="/previous/weights.gguf",
                firecrawl=True,
                endpoint="https://private.invalid/v1",
                extra_mcp="/private/keys.json",
            )
            f = Path(projects.save_project(s, "Research"))
            content = f.read_text()
            for sensitive in [str(a), "/previous/", "private.invalid", "keys.json"]:
                self.assertNotIn(sensitive, content)
            target = projects.profile_file(b)
            target.parent.mkdir()
            target.write_text(content)
            out = projects.restore_project(
                s | dict(workspace=str(b), firecrawl=False), "Research", [str(weight)]
            )
            self.assertEqual(
                (out["model"], out["workspace"], out["firecrawl"]),
                (str(weight), str(b), True),
            )

    def test_template_preserves_chosen_folder(self):
        with tempfile.TemporaryDirectory(prefix="space & folder ") as tmp:
            src = runtime.DEFAULTS | dict(workspace=tmp)
            s = bridge.selection(
                src,
                dict(preset="Work", workspace=tmp),
                {"Work": {"workspace": "older folder", "context": 65536}},
            )
            self.assertEqual(s["workspace"], str(Path(tmp).resolve()))
            self.assertEqual(s["context"], 65536)

    def test_plugin_registration_does_not_execute_and_rejects_collisions(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(projects, "DATA", Path(tmp)),
        ):
            cfg = {"command": "fixture", "args": ["serve"]}
            self.assertEqual(projects.register({"mcpServers": {"art": cfg}}), ["art"])
            self.assertEqual(projects.registry()["art"], cfg)
            with self.assertRaises(ValueError):
                projects.register({"mcpServers": {"art": {"command": "different"}}})
            with self.assertRaises(ValueError):
                projects.register({"mcpServers": {"search": cfg}})

    def test_openrouter_callback_uses_pkce_and_vault(self):
        observed = {}

        def browser(url):
            qs = parse_qs(urlsplit(url).query)
            observed.update(qs)

            def visit():
                with urlopen(
                    qs["callback_url"][0] + "?code=fixture", timeout=5
                ) as response:
                    response.read()

            threading.Thread(target=visit, daemon=True).start()
            return True

        def exchange(url, json, **kwargs):
            self.assertEqual(url, "https://openrouter.ai/api/v1/auth/keys")
            proof = (
                base64.urlsafe_b64encode(
                    hashlib.sha256(json["code_verifier"].encode()).digest()
                )
                .rstrip(b"=")
                .decode()
            )
            self.assertEqual(observed["code_challenge"], [proof])
            self.assertEqual(json["code"], "fixture")
            return Mock(**{"json.return_value": {"key": "test-only"}})

        with (
            patch("provider_auth.webbrowser.open", browser),
            patch("provider_auth.requests.post", exchange),
            patch("provider_auth.store_key") as vault,
            patch(
                "provider_auth.vault_status",
                return_value={"available": True, "backend": "test", "reason": ""},
            ),
        ):
            provider_auth.connect_openrouter()
            vault.assert_called_once_with({"provider": "OpenRouter"}, "test-only")


ROOT = Path(__file__).resolve().parents[1]
SH = shutil.which("sh")
FAKE_PYTHON = """#!/bin/sh
echo "$(basename "$(dirname "$(dirname "$0")")")/python $*" >> "$FAKE_LOG"
case "$1" in
  -c) exit "${FAKE_VERSION_RC:-0}" ;;
  -m)
    case "$2" in
      venv) mkdir -p "$3/bin" && cp "$0" "$3/bin/python" && : > "$3/pyvenv.cfg" ;;
      pip) exit "${FAKE_PIP_RC:-0}" ;;
    esac ;;
  *) echo "ran $* in $PWD caller=$WORKBENCH_CALLER_CWD" ;;
esac
"""
FAKE_TOOL = '#!/bin/sh\necho "$(basename "$0") $*" >> "$FAKE_LOG"\n'


@unittest.skipIf(os.name == "nt" or not SH, "POSIX launchers")
class PosixLauncherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.kit = self.tmp / "kit"
        self.kit.mkdir()
        for name in ("workbench.sh", "Launch Workbench.command"):
            shutil.copy(ROOT / name, self.kit / name)
        (self.kit / "requirements.lock").write_text("fixture==1\n")
        (self.kit / "desktop_app.py").write_text("")
        tools = self.tmp / "tools" / "bin"
        tools.mkdir(parents=True)
        self.fake = tools / "python"
        self.fake.write_text(FAKE_PYTHON)
        self.fake.chmod(0o755)
        self.log = self.tmp / "log"
        self.caller = self.tmp / "caller"
        self.caller.mkdir()

    def env(self, **extra):
        base = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(self.tmp),
            "FAKE_LOG": str(self.log),
            "WORKBENCH_BOOTSTRAP_PYTHON": str(self.fake),
        }
        return base | extra

    def run_launcher(self, *args, script="workbench.sh", **env):
        return subprocess.run(
            [SH, str(self.kit / script), *args],
            cwd=self.caller,
            env=self.env(**env),
            capture_output=True,
            text=True,
            timeout=20,
        )

    def calls(self, word=""):
        lines = self.log.read_text().splitlines() if self.log.exists() else []
        return [c for c in lines if word in c]

    def test_first_start_builds_environment_and_installs_locked_packages(self):
        r = self.run_launcher("cli.py", "status", WORKBENCH_CALLER_CWD="/stale")
        self.assertEqual(r.returncode, 0, r.stderr)
        pip = self.calls(" -m pip ")
        self.assertEqual(len(pip), 1)
        self.assertIn(
            "--require-hashes --only-binary :all: -r requirements.lock", pip[0]
        )
        self.assertTrue(pip[0].startswith(".venv-linux/python"))
        before = self.calls()[self.calls().index(pip[0]) - 1]
        self.assertEqual(before, f".venv-linux/python -I -c {self.snippet('unfinished')}")
        self.assertEqual(
            (self.kit / ".venv-linux/.workbench-requirements").read_text(),
            "fixture==1\n",
        )
        self.assertIn(f"ran cli.py status in {self.kit} caller={self.caller}", r.stdout)

    def test_second_start_skips_install_and_defaults_to_desktop(self):
        self.run_launcher()
        self.log.unlink()
        r = self.run_launcher()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(" -m "), [])
        self.assertIn("ran desktop_app.py", r.stdout)

    def test_changed_lock_reinstalls_without_rebuilding(self):
        self.run_launcher()
        (self.kit / "requirements.lock").write_text("fixture==2\n")
        self.log.unlink()
        self.run_launcher()
        self.assertEqual(len(self.calls(" -m pip ")), 1)
        self.assertEqual(self.calls(" -m venv "), [])

    def snippet(self, name):
        """One of the Python one-liners the launchers share, checked to be safe inside cmd.exe quotes."""
        code = re.search(rf'^{name}="(.+)"$', (ROOT / "workbench.sh").read_text(), re.M)[1]
        self.assertIn(f'set "{name}={code}"', (ROOT / "Run.cmd").read_text())
        self.assertNotRegex(code, r'["%!^&|$`\\]')
        return code

    def site(self, *present):
        """A stand-in site-packages folder under a stand-in environment, and Python code that adopts both."""
        prefix = Path(tempfile.mkdtemp(dir=self.tmp))
        site = prefix / "lib/site-packages"
        for name in present:
            (site / name).parent.mkdir(parents=True, exist_ok=True)
            (site / name).write_text("")
        return site, f"import sys; sys.path.insert(0, {str(site)!r}); sys.prefix = {str(prefix)!r}\n"

    def isolated(self, code):
        return subprocess.run([sys.executable, "-I", "-S", "-c", code], timeout=20)

    @unittest.skipUnless((3, 12) <= sys.version_info[:2] <= (3, 14), "supported Python")
    def test_environment_whose_pip_was_cut_off_mid_install_is_not_ready(self):
        probe = self.snippet("ready")
        states = (
            ("pip/__init__.py", "pip-26.3.dist-info/INSTALLER"),
            ("pip/__init__.py", "pip-26.3.dist-info/RECORD"),
            ("pip/__init__.py", "~ip-24.0.dist-info/INSTALLER"),
            ("pip/__main__.py", "pip-26.3.dist-info/INSTALLER"),
            ("pip-26.3.dist-info/INSTALLER",),
        )
        sites = [str(self.site(*present)[0]) for present in states]
        code = (
            "import sys\nbase = list(sys.path)\n"
            f"for site in {sites!r}:\n"
            "    sys.path[:] = [site, *base]\n"
            "    try:\n"
            f"        exec({probe!r})\n"
            "    except SystemExit as stop:\n"
            "        print(int(bool(stop.code)))\n"
        )
        r = subprocess.run(
            [sys.executable, "-I", "-S", "-c", code],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(r.stdout.split(), ["0", "1", "1", "1", "1"], r.stderr)

    def test_packages_cut_off_mid_install_are_forgotten_before_the_next_install(self):
        forget = self.snippet("unfinished")
        files = (
            "pip/__init__.py",
            "pip-26.3.dist-info/INSTALLER",
            "whole/__init__.py",
            "whole-1.dist-info/INSTALLER",
            "partial/__init__.py",
            "partial-2.dist-info/METADATA",
            "partial-2.dist-info/RECORD",
            "~hole/__init__.py",
            "~hole-0.9.dist-info/INSTALLER",
        )
        site, adopt = self.site(*files)
        self.isolated(adopt + forget)
        self.assertEqual(
            sorted(p.name for p in site.iterdir()),
            ["partial", "pip", "pip-26.3.dist-info", "whole", "whole-1.dist-info"],
        )
        site, adopt = self.site(*files)
        elsewhere = Path(tempfile.mkdtemp(dir=self.tmp))
        self.isolated(adopt + f"sys.prefix = {str(elsewhere)!r}\n" + forget)
        self.assertEqual(len(list(site.iterdir())), 8)

    def test_moved_or_partial_environment_is_rebuilt(self):
        venv = self.kit / ".venv-linux"
        (venv / "bin").mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("home = /gone\n")
        (venv / "bin/python").symlink_to("/gone/python3.12")
        (venv / ".workbench-requirements").write_text("fixture==1\n")
        r = self.run_launcher()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Rebuilding the Python environment", r.stdout)
        self.assertEqual(len(self.calls(" -m venv ")), 1)
        self.assertEqual(len(self.calls(" -m pip ")), 1)

    def test_folder_that_is_not_an_environment_is_kept(self):
        other = self.tmp / "documents"
        other.mkdir()
        (other / "keep.txt").write_text("mine")
        r = self.run_launcher(WORKBENCH_VENV=str(other))
        self.assertEqual(r.returncode, 1)
        self.assertIn("is not a Python environment", r.stderr)
        self.assertEqual((other / "keep.txt").read_text(), "mine")

    def test_unsupported_python_is_named(self):
        r = self.run_launcher(FAKE_VERSION_RC="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("is not Python 3.12, 3.13 or 3.14", r.stderr)
        self.assertFalse((self.kit / ".venv-linux").exists())

    def test_no_python_on_path_is_named(self):
        bare = self.tmp / "bare"
        bare.mkdir()
        for tool in ("dirname", "uname", "cmp", "cp", "rm"):
            (bare / tool).symlink_to(shutil.which(tool))
        r = self.run_launcher(WORKBENCH_BOOTSTRAP_PYTHON="", PATH=str(bare))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("needs Python 3.12, 3.13 or 3.14", r.stderr)

    def test_failed_install_leaves_no_stamp(self):
        r = self.run_launcher(FAKE_PIP_RC="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("pypi.org", r.stderr)
        self.assertFalse((self.kit / ".venv-linux/.workbench-requirements").exists())

    @unittest.skipIf(os.name != "nt" and os.geteuid() == 0, "root can write anywhere")
    def test_environment_this_user_cannot_change_is_named_before_installing(self):
        self.run_launcher()
        venv = self.kit / ".venv-linux"
        (self.kit / "requirements.lock").write_text("fixture==2\n")
        self.log.unlink()
        venv.chmod(0o555)
        self.addCleanup(venv.chmod, 0o755)
        r = self.run_launcher()
        self.assertEqual(r.returncode, 1)
        self.assertIn("this user cannot write to it", r.stderr)
        self.assertEqual(self.calls(" -m pip "), [])

    def test_linked_script_stops_before_creating_anything(self):
        links = self.tmp / "links"
        links.mkdir()
        (links / "workbench").symlink_to(self.kit / "workbench.sh")
        r = subprocess.run(
            [SH, str(links / "workbench")],
            cwd=self.caller,
            env={"PATH": "/usr/bin:/bin", "FAKE_LOG": str(self.log)},
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn("from the Agent Workbench folder itself", r.stderr)
        self.assertEqual([p.name for p in links.iterdir()], ["workbench"])

    def test_exported_cdpath_cannot_send_the_launcher_to_another_folder(self):
        decoy = self.tmp / "decoy"
        (decoy / "kit").mkdir(parents=True)
        r = subprocess.run(
            [SH, "kit/workbench.sh", "cli.py"],
            cwd=self.tmp,
            env=self.env(CDPATH=str(decoy)),
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[-1], f"ran cli.py in {self.kit} caller={self.tmp}")

    def test_linux_desktop_start_runs_the_sandbox_step_once(self):
        tools = self.fake.parent
        (tools / "uname").write_text("#!/bin/sh\necho Linux\n")
        (tools / "uname").chmod(0o755)
        (self.kit / "setup-linux.sh").write_text('echo "setup $*" >> "$FAKE_LOG"\nexit 1\n')
        path = f"{tools}:/usr/bin:/bin"
        r = self.run_launcher(PATH=path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ran desktop_app.py", r.stdout)
        self.run_launcher("cli.py", PATH=path)
        self.run_launcher(PATH=path, WORKBENCH_SANDBOX_CHECKED="1")
        self.assertEqual(self.calls("setup"), ["setup --sandbox"])

    @unittest.skipUnless(
        sys.platform == "darwin" and os.uname().machine == "arm64", "Apple Silicon"
    )
    def test_macos_launcher_clears_quarantine_once_and_keeps_the_caller_folder(self):
        mark = ["/usr/bin/xattr", "-w", "com.apple.quarantine", "0083;00000000;Safari;"]
        probe = ["/usr/bin/xattr", "-p", "com.apple.quarantine"]
        inner = self.kit / "python"
        (inner / "bin").mkdir(parents=True)
        outside = self.tmp / "outside"
        outside.mkdir()
        (self.kit / "library").symlink_to(outside)
        marked = (self.kit / "Launch Workbench.command", inner, self.kit / "workbench.sh")
        for path in (*marked, outside):
            subprocess.run([*mark, str(path)], check=True)
        r = self.run_launcher("cli.py", script="Launch Workbench.command")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Removing the macOS download mark", r.stdout)
        for path in marked:
            self.assertNotEqual(
                subprocess.run([*probe, str(path)], capture_output=True).returncode, 0
            )
        self.assertEqual(
            subprocess.run([*probe, str(outside)], capture_output=True).returncode, 0
        )
        r = self.run_launcher("cli.py", script="Launch Workbench.command")
        self.assertNotIn("Removing", r.stdout)
        self.assertIn(f"caller={self.caller}", r.stdout)


@unittest.skipIf(os.name == "nt" or not SH, "POSIX script")
class LinuxSetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.kit = self.tmp / "kit"
        (self.kit / "desktop").mkdir(parents=True)
        shutil.copy(ROOT / "setup-linux.sh", self.kit / "setup-linux.sh")
        (self.kit / "desktop/package.json").write_text(
            '{"dependencies": {"electron": "45.4.1"}}'
        )
        self.proc = self.tmp / "proc"
        (self.proc / "sys/kernel").mkdir(parents=True)
        self.tools = self.tmp / "tools"
        self.tools.mkdir()
        for tool in ("sudo", "apparmor_parser"):
            self.tool(tool, FAKE_TOOL)
        self.tool("id", "#!/bin/sh\necho 1000\n")
        self.log = self.tmp / "log"
        self.data = self.tmp / "data"

    def tool(self, name, text):
        (self.tools / name).write_text(text)
        (self.tools / name).chmod(0o755)

    def sandbox(self, *args, **world):
        return self.setup_script("--sandbox", *args, **world)

    def setup_script(self, *args, restricted="1", script=None, **env):
        (self.proc / "sys/kernel/apparmor_restrict_unprivileged_userns").write_text(
            restricted + "\n"
        )
        base = {
            "PATH": f"{self.tools}:/usr/bin:/bin",
            "HOME": str(self.tmp),
            "FAKE_LOG": str(self.log),
            "WORKBENCH_PROC_ROOT": str(self.proc),
            "WORKBENCH_DATA": str(self.data),
        }
        return subprocess.run(
            [SH, script or str(self.kit / "setup-linux.sh"), *args],
            cwd=self.tmp,
            env=base | env,
            capture_output=True,
            text=True,
            timeout=20,
            stdin=subprocess.DEVNULL,
        )

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_unrestricted_system_needs_nothing(self):
        r = self.sandbox(restricted="0")
        self.assertEqual((r.returncode, r.stdout, self.calls()), (0, "", []))

    def test_restricted_system_explains_and_changes_nothing_without_consent(self):
        r = self.sandbox()
        self.assertEqual(r.returncode, 1)
        self.assertIn(
            f"{self.data}/desktop-runtime/linux-*/electron flags=(unconfined)", r.stdout
        )
        self.assertIn(f"{self.data}/desktop-runtime/linux-45.4.1", r.stdout)
        self.assertIn("sudo apparmor_parser -r /etc/apparmor.d/agent-workbench-", r.stdout)
        self.assertIn("Nothing was changed.", r.stdout)
        self.assertEqual(self.calls(), [])

    def test_consent_installs_the_profile_through_sudo(self):
        r = self.sandbox("--yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        self.assertRegex(
            calls[0], r"^sudo install -m 644 \S+ /etc/apparmor\.d/agent-workbench-\d+$"
        )
        self.assertRegex(
            calls[1], r"^sudo apparmor_parser -r /etc/apparmor\.d/agent-workbench-\d+$"
        )

    def test_helper_route_when_a_profile_cannot_name_the_data_folder(self):
        self.data = self.tmp / "my data"
        helper = self.data / "desktop-runtime/linux-45.4.1/chrome-sandbox"
        helper.parent.mkdir(parents=True)
        helper.write_text("")
        r = self.sandbox("--yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            self.calls(),
            [f"sudo chown root:root {helper}", f"sudo chmod 4755 {helper}"],
        )

    def test_refused_sudo_is_reported_and_leaves_no_unloaded_profile(self):
        (self.tools / "sudo").write_text(
            FAKE_TOOL + 'case "$1" in "$FAKE_REFUSED") exit 1 ;; esac\n'
        )
        r = self.sandbox("--yes", FAKE_REFUSED="install")
        self.assertEqual((r.returncode, len(self.calls())), (1, 1))
        self.assertNotIn("The profile is installed.", r.stdout)
        self.assertIn("The profile could not be installed", r.stdout)
        self.log.unlink()
        r = self.sandbox("--yes", FAKE_REFUSED="apparmor_parser")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("The profile is installed.", r.stdout)
        installed = self.calls()[0].split()[-1]
        self.assertEqual(
            self.calls()[1:],
            [f"sudo apparmor_parser -r {installed}", f"sudo rm -f {installed}"],
        )
        self.log.unlink()
        self.data = self.tmp / "my data"
        helper = self.data / "desktop-runtime/linux-45.4.1/chrome-sandbox"
        helper.parent.mkdir(parents=True)
        helper.write_text("")
        r = self.sandbox("--yes", FAKE_REFUSED="chown")
        self.assertEqual((r.returncode, len(self.calls())), (1, 1))
        self.assertNotIn("The sandbox helper is set up.", r.stdout)
        self.assertIn("The sandbox helper could not be changed", r.stdout)

    def test_helper_of_the_runtime_this_kit_starts_is_the_one_judged_and_fixed(self):
        self.data = self.tmp / "my data"
        old, new = (
            self.data / f"desktop-runtime/linux-{version}/chrome-sandbox"
            for version in ("44.0.0", "45.4.1")
        )
        old.parent.mkdir(parents=True)
        old.write_text("")
        self.tool("find", '#!/bin/sh\ncase "$1" in *"$FAKE_ROOT_OWNED"*) echo "$1" ;; esac\n')
        r = self.sandbox("--yes", FAKE_ROOT_OWNED="linux-44.0.0")
        self.assertEqual((r.returncode, self.calls()), (1, []))
        self.assertIn("has not been downloaded yet", r.stdout)
        new.parent.mkdir()
        new.write_text("")
        r = self.sandbox("--yes", FAKE_ROOT_OWNED="linux-44.0.0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            self.calls(), [f"sudo chown root:root {new}", f"sudo chmod 4755 {new}"]
        )
        self.log.unlink()
        r = self.sandbox("--yes", FAKE_ROOT_OWNED="linux-45.4.1")
        self.assertEqual((r.returncode, r.stdout, self.calls()), (0, "", []))

    def test_package_step_survives_a_failed_refresh_and_explains_a_failed_install(self):
        self.tool("apt-get", FAKE_TOOL)
        self.tool("sudo", FAKE_TOOL + 'case "$2" in "$FAKE_REFUSED") exit 100 ;; esac\n')
        (self.kit / "workbench.sh").write_text('echo "started $*"\n')
        r = self.setup_script("--yes", "cli.py", restricted="0", FAKE_REFUSED="update")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c.split()[2] for c in self.calls()], ["update", "install"])
        self.assertIn("libgtk-3-0", self.calls()[1])
        self.assertEqual(r.stdout.splitlines()[-1], "started cli.py")
        r = self.setup_script("--yes", "cli.py", restricted="0", FAKE_REFUSED="install")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("started cli.py", r.stdout)
        self.assertIn("The system packages could not be installed", r.stdout)
        self.assertIn(f'run: sh "{self.kit}/setup-linux.sh"', r.stdout)

    def test_relative_paths_resolve_against_the_kit_despite_cdpath(self):
        decoy = self.tmp / "decoy"
        (decoy / "kit").mkdir(parents=True)
        r = self.sandbox(
            script="kit/setup-linux.sh", WORKBENCH_DATA="state", CDPATH=str(decoy)
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn(
            f"profile agent-workbench-{self.profile_number(self.kit / 'state')} "
            f"{self.kit}/state/desktop-runtime/linux-*/electron flags=(unconfined)",
            r.stdout,
        )
        self.assertIn(f'run: sh "{self.kit}/setup-linux.sh" --sandbox', r.stdout)

    def profile_number(self, data):
        sums = subprocess.run(
            ["cksum"], input=str(data).encode(), capture_output=True, check=True
        )
        return sums.stdout.split()[0].decode()


FOLDER = "K:\\kit\\"
ENVIRONMENT = {".venv", ".venv\\pyvenv.cfg", ".venv\\Scripts\\python.exe"}
STAMP = ".venv\\workbench-requirements.txt"
PROBE = '.venv\\Scripts\\python.exe" -c'
STEPS = {
    "-m venv": "venv",
    "-m pip": "pip",
    "fc /b": "compare",
    "copy /y": "stamp",
    "rmdir": "remove",
    '" -I -c "': "forget",
    '" -c "': "check",
}
CRASHED = -1073741515


class BatchFlow:
    """Follows the labels and conditions of Run.cmd with stand-in files and programs.

    It models only the statements that file uses, so it checks the order of steps and
    says nothing about how cmd.exe parses a line.
    """

    def __init__(self, text, files=(), answers=None, found=None, env=None):
        self.lines = text.splitlines()
        self.labels = {
            line[1:].strip(): n for n, line in enumerate(self.lines) if line.startswith(":")
        }
        self.files = set(files)
        self.answers, self.found = answers or {}, found or {}
        self.env = dict(env or {})
        self.at, self.level, self.code = 0, 0, None
        self.frames, self.out, self.ran = [], [], []
        while self.code is None:
            self.step()

    def step(self):
        if self.at >= len(self.lines):
            raise AssertionError("ran past the end of the file")
        line, self.at = self.lines[self.at], self.at + 1
        line = line.replace("%~dp0", FOLDER).replace("%CD%", "C:\\caller")
        line = line.replace("%*", self.frames[-1] if self.frames else "cli.py status")
        self.statement(re.sub(r"%(\w+)%", self.variable, line).strip())

    def variable(self, match):
        name = match[1].lower()
        return str(self.level) if name == "errorlevel" else self.env.get(name, "")

    def statement(self, s):
        if s.startswith((":", "@echo off", "setlocal", "cd /d ")) or not s:
            return
        test = re.fullmatch(
            r'if (not )?(exist "([^"]+)"|defined (\w+)|errorlevel ([01])) (.+)', s
        )
        if test:
            negate, _, path, name, least, then = test.groups()
            if path:
                truth = path in self.files
            else:
                truth = name.lower() in self.env if name else self.level >= int(least)
            if truth != bool(negate):
                self.statement(then)
        elif found := re.fullmatch(r"for %%V in \((.+)\) do (.+)", s):
            for value in found[1].split():
                self.statement(found[2].replace("%%V", value))
        elif found := re.fullmatch(r"""for /f "delims=" %%P in \('(.+)'\) do (.+)""", s):
            path = self.found.get(found[1].split(" -c ")[0])
            if path:
                self.statement(found[2].replace("%%P", path))
        elif found := re.fullmatch(r'set "(\w+)=(.*)"', s):
            self.env.pop(found[1].lower(), None)
            if found[2]:
                self.env[found[1].lower()] = found[2]
        elif found := re.fullmatch(r"goto (\w+)", s):
            self.at = self.labels[found[1]]
        elif found := re.fullmatch(r"call :(\w+) (.+)", s):
            back, self.at = self.at, self.labels[found[1]]
            self.frames.append(found[2])
            depth = len(self.frames)
            while len(self.frames) >= depth:
                self.step()
            self.at = back
        elif found := re.fullmatch(r"exit /b (\d+)", s):
            self.level = int(found[1])
            if self.frames:
                self.frames.pop()
            else:
                self.code = self.level
        elif s.startswith("echo "):
            self.out.append(s[5:])
        else:
            self.level = self.program(s)

    def program(self, s):
        self.ran.append(s)
        code = next((rc for word, rc in self.answers.items() if word in s), None)
        if code is None:
            code = 0 if not s.startswith("fc /b") or STAMP in self.files else 2
        if s.startswith("copy /y"):
            self.files.add(STAMP)
        elif code == 0 and "-m venv" in s:
            self.files |= ENVIRONMENT
        elif code == 0 and s.startswith("rmdir"):
            self.files -= ENVIRONMENT | {STAMP}
        return code

    def steps(self):
        return [
            next((step for word, step in STEPS.items() if word in s), "start")
            for s in self.ran
        ]


class WindowsLauncherTextTest(unittest.TestCase):
    def text(self, name):
        return (ROOT / name).read_bytes().decode("ascii").replace("\r\n", "\n")

    def flow(self, files=(), **world):
        return BatchFlow(self.text("Run.cmd"), {"requirements.lock", *files}, **world)

    def test_run_cmd_installs_from_the_lock_and_never_pauses(self):
        run = self.text("Run.cmd")
        self.assertIn(
            "-m pip install --disable-pip-version-check --no-input --require-hashes --only-binary :all: -r requirements.lock",
            run,
        )
        self.assertIn('fc /b requirements.lock ".venv\\workbench-requirements.txt"', run)
        self.assertNotIn("pause", run)
        self.assertNotIn("-r requirements.txt", run)
        self.assertLess(
            run.index('set "WORKBENCH_CALLER_CWD=%CD%"'), run.index('cd /d "%~dp0"')
        )

    def test_run_cmd_labels_resolve_and_no_block_can_break_on_a_folder_name(self):
        lines = self.text("Run.cmd").splitlines()
        labels = {line[1:].strip() for line in lines if line.startswith(":")}
        for line in lines:
            for target in re.findall(r"(?:goto |call :)(\w+)", line):
                self.assertIn(target, labels, line)
            self.assertFalse(line.rstrip().endswith("("), line)
        self.assertEqual(
            labels,
            {
                "build", "check", "create", "fresh", "packages", "run", "find",
                "misplaced", "nopython", "badpython", "foreign", "novenv", "nopackages",
            },
        )

    def test_run_cmd_checks_versions_and_tries_the_py_launcher(self):
        run = self.text("Run.cmd")
        self.assertEqual(run.count("(3, 12) <= sys.version_info[:2] <= (3, 14)"), 2)
        self.assertIn("call :find python", run)
        self.assertIn(
            "for %%V in (3.12 3.13 3.14) do if not defined bootstrap call :find py -%%V",
            run,
        )
        self.assertIn("WORKBENCH_BOOTSTRAP_PYTHON", run)
        self.assertIn('if not exist ".venv\\pyvenv.cfg" goto fresh', run)

    def test_run_cmd_never_expands_its_arguments_twice_or_replaces_the_data_folder(self):
        run = self.text("Run.cmd")
        passing = [line for line in run.splitlines() if "%*" in line]
        self.assertEqual(passing[0], '".venv\\Scripts\\python.exe" %*')
        self.assertEqual(len(passing), 2)
        self.assertFalse([line for line in passing if "call" in line.lower()])
        self.assertNotIn("WORKBENCH_DATA", run)
        self.assertNotIn("enabledelayedexpansion", run.lower())

    def test_batch_files_keep_windows_line_endings(self):
        for name in ("Run.cmd", "Launch Workbench.cmd", "Start Agent.cmd", "Setup engines.cmd"):
            data = (ROOT / name).read_bytes()
            self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), name)
            self.assertTrue(data.endswith(b"\r\n"), name)

    def test_run_cmd_flow_builds_once_then_starts_directly(self):
        first = self.flow({"python\\python.exe"}, answers={"cli.py status": 7})
        self.assertEqual(
            first.steps(), ["check", "venv", "compare", "forget", "pip", "stamp", "start"]
        )
        self.assertEqual(first.ran[1], f'"{FOLDER}python\\python.exe" -m venv ".venv"')
        self.assertEqual(first.ran[-1], '".venv\\Scripts\\python.exe" cli.py status')
        self.assertEqual(first.code, 7)
        self.assertEqual(
            first.out,
            ["Installing Python packages from pypi.org (first start and after an update)."],
        )
        self.assertEqual(first.env["workbench_caller_cwd"], "C:\\caller")
        second = self.flow(first.files)
        self.assertEqual(second.steps(), ["check", "compare", "start"])
        self.assertEqual((second.code, second.out), (0, []))
        changed = self.flow(first.files, answers={"fc /b": 1})
        self.assertEqual(
            changed.steps(), ["check", "compare", "forget", "pip", "stamp", "start"]
        )

    def test_run_cmd_flow_forgets_unfinished_packages_before_every_install(self):
        run = self.text("Run.cmd")
        forget = re.search(r'^set "unfinished=(.+)"$', run, re.M)[1]
        self.assertIn("'INSTALLER'", forget)
        self.assertIn("sys.prefix", forget)
        first = self.flow({"python\\python.exe"}, answers={'" -I -c "': 1})
        self.assertEqual(first.ran[3], f'".venv\\Scripts\\python.exe" -I -c "{forget}" >nul 2>nul')
        self.assertEqual(first.steps()[3:], ["forget", "pip", "stamp", "start"])
        self.assertEqual(first.code, 0)

    def test_run_cmd_flow_treats_a_crashed_program_as_a_failure(self):
        kit = ENVIRONMENT | {STAMP, "python\\python.exe"}
        broken = self.flow(kit, answers={PROBE: CRASHED})
        self.assertEqual(broken.steps()[:4], ["check", "check", "remove", "venv"])
        install = self.flow({"python\\python.exe"}, answers={"-m pip": CRASHED})
        self.assertEqual((install.code, install.steps()[-1]), (1, "pip"))
        self.assertIn("could not be installed", install.out[-1])
        self.assertNotIn(STAMP, install.files)
        create = self.flow({"python\\python.exe"}, answers={"-m venv": CRASHED})
        self.assertEqual((create.code, create.steps()[-1]), (1, "venv"))
        self.assertIn("could not create its environment", create.out[-1])
        bundled = self.flow({"python\\python.exe"}, answers={'python.exe" -c': CRASHED})
        self.assertEqual((bundled.code, bundled.steps()), (1, ["check"]))
        self.assertIn("did not start", bundled.out[-1])
        old, new = "C:\\Old\\python.exe", "C:\\Py313\\python.exe"
        found = self.flow(
            found={"python": old, "py -3.13": new}, answers={f'"{old}" -c': CRASHED}
        )
        self.assertEqual(found.code, 0)
        self.assertFalse([s for s in found.ran if old in s and "-m" in s])

    def test_run_cmd_flow_rebuilds_a_moved_environment_and_keeps_a_foreign_folder(self):
        kit = ENVIRONMENT | {STAMP, "python\\python.exe"}
        moved = self.flow(kit, answers={PROBE: 103})
        self.assertEqual(
            moved.steps(),
            ["check", "check", "remove", "venv", "compare", "forget", "pip", "stamp", "start"],
        )
        self.assertIn("Rebuilding the Python environment", moved.out[0])
        locked = self.flow(kit, answers={PROBE: 103, "rmdir": 1})
        self.assertEqual((locked.code, locked.steps()[-1]), (1, "remove"))
        self.assertIn(f'delete "{FOLDER}.venv" yourself', locked.out[-1])
        foreign = self.flow({".venv", "python\\python.exe"})
        self.assertEqual((foreign.code, foreign.steps()), (1, ["check"]))
        self.assertIn("could not be replaced", foreign.out[-1])

    def test_run_cmd_flow_finds_a_supported_python_or_says_what_is_missing(self):
        old, new = "C:\\Old\\python.exe", "C:\\Py313\\python.exe"
        found = self.flow(
            found={"python": old, "py -3.13": new}, answers={f'"{old}" -c': 1}
        )
        self.assertEqual(found.code, 0)
        self.assertIn(f'"{new}" -m venv ".venv"', found.ran)
        self.assertFalse([s for s in found.ran if old in s and "-m" in s])
        missing = self.flow(found={"python": old}, answers={f'"{old}" -c': 1})
        self.assertEqual((missing.code, missing.steps()), (1, ["check"]))
        self.assertIn("needs Python 3.12, 3.13 or 3.14", missing.out[-1])
        named = self.flow(
            env={"workbench_bootstrap_python": "D:\\py.exe"}, answers={"D:\\py.exe": 1}
        )
        self.assertEqual((named.code, named.steps()), (1, ["check"]))
        self.assertIn('The Python at "D:\\py.exe" did not start', named.out[-1])

    def test_run_cmd_flow_stops_on_each_failure_without_a_stamp(self):
        bundled = {"python\\python.exe"}
        failed = self.flow(bundled, answers={"-m pip": 1})
        self.assertEqual((failed.code, failed.steps()[-1]), (1, "pip"))
        self.assertIn("pypi.org", failed.out[-1])
        self.assertNotIn(STAMP, failed.files)
        blocked = self.flow(bundled, answers={"-m venv": 1})
        self.assertEqual((blocked.code, blocked.steps()[-1]), (1, "venv"))
        self.assertIn("could not create its environment", blocked.out[-1])
        misplaced = BatchFlow(self.text("Run.cmd"), bundled)
        self.assertEqual((misplaced.code, misplaced.ran), (1, []))
        self.assertIn("from the Agent Workbench folder itself", misplaced.out[-1])

    def test_hidden_launcher_reports_failures(self):
        vbs = self.text("Workbench.vbs")
        self.assertIn("requirements.lock", vbs)
        self.assertNotIn("requirements.txt", vbs.replace("workbench-requirements.txt", ""))
        self.assertIn('" desktop_app.py", 0, True)', vbs)
        self.assertIn("MsgBox", vbs)
        self.assertIn("import pip", vbs)

    def test_double_click_launchers_forward_arguments(self):
        self.assertIn('"%~dp0cli.py" agent %*', self.text("Start Agent.cmd"))
        self.assertTrue(
            self.text("Start Agent.cmd").endswith("if errorlevel 1 pause\nif not errorlevel 0 pause\n")
        )
        self.assertIn('"%~dp0setup_engines.py" %*', self.text("Setup engines.cmd"))
        self.assertIn("if errorlevel 1 pause", self.text("Launch Workbench.cmd"))

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_posix_scripts_are_executable(self):
        for name in ("workbench.sh", "Launch Workbench.command", "setup-linux.sh", "install.sh"):
            self.assertTrue(os.access(ROOT / name, os.X_OK), name)
