import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import MagicMock, patch

if "runtime" not in sys.modules and "WORKBENCH_DATA" not in os.environ:
    os.environ["WORKBENCH_DATA"] = tempfile.mkdtemp(prefix="workbench-test-")
    atexit.register(shutil.rmtree, os.environ["WORKBENCH_DATA"], ignore_errors=True)

import boxes  # noqa: E402

KEY = "AAAAC3NzaC1lZDI1NTE5AAAAI" + "B" * 43
PORTS = '[{"NetworkSettings": {"Ports": {"22/tcp": [{"HostPort": "32768"}]}}}]'
LABEL = '{{index .Config.Labels "org.agent-workbench.box"}}'
BOX = "workbench-box-dev"
RESTART = "use Stop sandbox, then Open sandbox again."
NO_DOCKER = (
    "Docker was not found; install it in Advanced → Setup → Docker, then retry."
)
NO_ANSWER = "Docker did not answer in time; check that Docker is running, then retry."
NO_MESSAGE = (
    "Docker failed without a message; check that Docker is running, then retry."
)
NO_DAEMON = (
    "Docker did not start inside WSL; open Advanced → Setup → Docker, "
    "run Install again, then retry."
)
NOT_RUNNING = "Docker is not running; start Docker, then retry."
STOPPED = b"Cannot connect to the Docker daemon at unix:///var/run/docker.sock."


def done(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


def wsl_only(name):
    return "C:\\Windows\\System32\\wsl.exe" if name == "wsl" else None


def patch_run(**kwargs):
    return patch.object(boxes.subprocess, "run", **kwargs)


def patch_popen(**kwargs):
    return patch.object(boxes.subprocess, "Popen", **kwargs)


def verbs(mock):
    return [call.args[0] for call in mock.mock_calls]


class BoxesTest(unittest.TestCase):
    def setUp(self):
        self.use(patch.object(boxes.time, "sleep"))
        self.use(patch.object(boxes, "_daemon", False))

    def use(self, item):
        value = item.start()
        self.addCleanup(item.stop)
        return value

    def native(self):
        return patch.object(boxes, "docker", return_value=["docker"])

    def wsl(self):
        self.use(patch.object(boxes, "WINDOWS", True))
        self.use(patch.object(boxes.shutil, "which", side_effect=wsl_only))

    def message(self, call, *args, **kwargs):
        with self.assertRaises(ValueError) as caught:
            call(*args, **kwargs)
        return str(caught.exception)

    def test_wsl_prefix_uses_exec_and_prompt_stays_one_argument(self):
        prompt = 'Prefer `rg` over grep and $(id) "quoted" \'single\'\nsecond line'
        self.wsl()
        with (
            patch.object(boxes, "attach", return_value=BOX),
            patch.object(boxes, "key", return_value="session-key"),
            patch.dict(os.environ, {"WSLENV": "OTHER/u"}),
        ):
            prefix = boxes.docker()
            argv, env = boxes.agent("dev", {"prompt": prompt}, interactive=False)
        self.assertEqual(
            prefix,
            [
                "wsl",
                "-d",
                "AgentWorkbench-Web",
                "-u",
                "root",
                "--cd",
                str(boxes.ROOT),
                "--exec",
                "/usr/bin/docker",
            ],
        )
        self.assertEqual(argv[: len(prefix)], prefix)
        self.assertNotIn("--", argv)
        self.assertEqual(argv[-2:], ["--append-system-prompt", prompt])
        self.assertEqual(env["WSLENV"], "OTHER/u:WORKBENCH_SESSION_KEY")
        self.assertEqual(env["WORKBENCH_SESSION_KEY"], "session-key")
        self.assertNotIn("session-key", argv)

    def test_native_docker_is_preferred_and_gets_no_wslenv(self):
        with (
            patch.object(boxes, "WINDOWS", True),
            patch.object(boxes.shutil, "which", side_effect=lambda n: "/bin/" + n),
            patch.object(boxes, "attach", return_value=BOX),
            patch.object(boxes, "key", return_value="session-key"),
            patch.dict(os.environ),
        ):
            os.environ.pop("WSLENV", None)
            self.assertEqual(boxes.docker(), ["/bin/docker"])
            argv, env = boxes.agent("dev", {"prompt": "p"}, interactive=False)
        self.assertEqual(argv[:2], ["/bin/docker", "exec"])
        self.assertNotIn("WSLENV", env)

    def test_no_docker_message(self):
        with patch.object(boxes.shutil, "which", return_value=None):
            with patch.object(boxes, "WINDOWS", False):
                self.assertEqual(self.message(boxes.docker), NO_DOCKER)
            with patch.object(boxes, "WINDOWS", True):
                self.assertEqual(self.message(boxes.docker), NO_DOCKER)
        with (
            patch.object(boxes, "WINDOWS", False),
            patch.object(boxes.shutil, "which", side_effect=wsl_only),
        ):
            self.assertEqual(self.message(boxes.docker), NO_DOCKER)

    def test_known_hosts_option_is_quoted(self):
        known = Path("/tmp/My Data/boxes/dev/known_hosts")
        home = PureWindowsPath(r"C:\Users\My Name\data\boxes\dev")
        with patch.object(boxes, "command", return_value=["ssh"]):
            args = boxes.tunnel_args(32768, known.with_name("forward"), known)
            windows = boxes.tunnel_args("32768", home / "forward", home / "known_hosts")
            spaced = boxes.tunnel_args(
                "32768", Path("/tmp/a/forward"), Path("/tmp/a ~/known_hosts")
            )
        option = 'UserKnownHostsFile="/tmp/My Data/boxes/dev/known_hosts"'
        self.assertEqual(args[args.index(option) - 1], "-o")
        self.assertEqual(args[args.index("-p") + 1], "32768")
        self.assertEqual(args[:2], ["ssh", "-NT"])
        self.assertEqual(
            args[-3:],
            ["-R", f"127.0.0.1:10532:127.0.0.1:{boxes.PORT}", "node@127.0.0.1"],
        )
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertIn("ExitOnForwardFailure=yes", args)
        self.assertIn(
            'UserKnownHostsFile="C:/Users/My Name/data/boxes/dev/known_hosts"', windows
        )
        self.assertEqual(
            windows[windows.index("-i") + 1],
            r"C:\Users\My Name\data\boxes\dev\forward",
        )
        ssh = shutil.which("ssh")
        if os.name != "nt" and ssh:
            out = subprocess.run(
                [ssh, "-G", "-F", os.devnull, *spaced[1:]],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=10,
            ).stdout
            self.assertIn("userknownhostsfile /tmp/a ~/known_hosts\n", out)

    def test_percent_in_the_data_path_stays_literal(self):
        work = Path("/tmp/100% done/boxes/dev")
        home = PureWindowsPath(r"C:\Data 100%\boxes\dev")
        with patch.object(boxes, "command", return_value=["ssh"]):
            args = boxes.tunnel_args(32768, work / "forward", work / "known_hosts")
            windows = boxes.tunnel_args(1, home / "forward", home / "known_hosts")
            plain = boxes.tunnel_args(1, Path("/tmp/a/forward"), Path("/tmp/a/known"))
        self.assertEqual(
            args[:8],
            [
                "ssh",
                "-NT",
                "-p",
                "32768",
                "-o",
                'IdentityFile="/tmp/100%% done/boxes/dev/forward"',
                "-o",
                "IdentitiesOnly=yes",
            ],
        )
        self.assertNotIn("-i", args)
        self.assertIn(
            'UserKnownHostsFile="/tmp/100%% done/boxes/dev/known_hosts"', args
        )
        self.assertIn('IdentityFile="C:/Data 100%%/boxes/dev/forward"', windows)
        self.assertIn(
            'UserKnownHostsFile="C:/Data 100%%/boxes/dev/known_hosts"', windows
        )
        self.assertEqual(plain[4:6], ["-i", str(Path("/tmp/a/forward"))])
        self.assertIn('UserKnownHostsFile="/tmp/a/known"', plain)

    def test_host_key_waits(self):
        replies = [
            done(1),
            done(0, "ssh-ed25519 AAAA"),
            done(0, f"ssh-ed25519 {KEY} root@x\n"),
        ]
        with self.native(), patch_run(side_effect=replies) as run:
            self.assertEqual(boxes.host_key("c"), ["ssh-ed25519", KEY])
            self.assertEqual(run.call_count, 3)
            self.assertEqual(
                run.call_args.args[0],
                ["docker", "exec", "c", "cat", "/etc/ssh/ssh_host_ed25519_key.pub"],
            )
            self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(run.call_args.kwargs["timeout"], 20)
            self.assertEqual(run.call_args.kwargs["errors"], "replace")
        slow = [subprocess.TimeoutExpired("docker", 20), done(0, f"ssh-ed25519 {KEY}")]
        with self.native(), patch_run(side_effect=slow):
            self.assertEqual(boxes.host_key("c"), ["ssh-ed25519", KEY])
        for reply in (done(1), done(0, f"ssh-rsa {KEY}"), done(0, "")):
            with self.native(), patch_run(return_value=reply) as run:
                self.assertEqual(
                    self.message(boxes.host_key, "c"),
                    "The sandbox did not finish starting; " + RESTART,
                )
                self.assertEqual(run.call_count, 40)

    def test_tunnel_restarts_until_sshd_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "ssh.log"
            dead, live = MagicMock(), MagicMock()
            dead.poll.return_value = 255
            live.poll.return_value = None
            with patch_popen(side_effect=[dead, live]) as popen:
                self.assertIs(boxes.open_tunnel(["ssh"], log, lambda: True), live)
                self.assertEqual(popen.call_count, 2)
                self.assertEqual(popen.call_args.args[0], ["ssh"])
                self.assertIs(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
                self.assertIs(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
            live.terminate.assert_not_called()

            def refused(args, **kwargs):
                kwargs["stderr"].write(
                    "kex_exchange_identification: Connection closed by remote host\n\n"
                )
                kwargs["stderr"].flush()
                return dead

            with patch_popen(side_effect=refused) as popen:
                self.assertEqual(
                    self.message(boxes.open_tunnel, ["ssh"], log, lambda: True),
                    "The sandbox SSH tunnel stopped (kex_exchange_identification: "
                    "Connection closed by remote host); " + RESTART,
                )
                self.assertEqual(popen.call_count, 5)
            with patch_popen(return_value=dead) as popen:
                self.assertEqual(
                    self.message(boxes.open_tunnel, ["ssh"], log, lambda: True, 2),
                    "The sandbox SSH tunnel stopped; " + RESTART,
                )
                self.assertEqual(popen.call_count, 2)
            checks = []
            with patch_popen(return_value=live) as popen:
                self.assertEqual(
                    self.message(
                        boxes.open_tunnel, ["ssh"], log, lambda: checks.append(1)
                    ),
                    "The sandbox could not reach the local model; check that the "
                    "model is serving, then use Open sandbox again.",
                )
                self.assertEqual(popen.call_count, 1)
            self.assertEqual(len(checks), 20)
            live.terminate.assert_called_once()

    def test_docker_errors_are_sentences(self):
        daemon = "Docker is not running; start Docker, then retry."
        image = (
            "The sandbox image is not built yet; open Advanced → Setup → "
            "Sandbox image → Install, then retry."
        )
        missing = "This sandbox does not exist yet; use Open sandbox to create it."
        for err, want in [
            (
                "permission denied while trying to connect to the Docker daemon "
                "socket at unix:///var/run/docker.sock",
                "Your account is not allowed to use Docker; add it to the docker "
                "group, sign in again, then retry.",
            ),
            (
                b"Cannot connect to the Docker daemon at unix:///x/docker.sock. "
                b"Is the docker daemon running?",
                daemon,
            ),
            ("error during connect: this error may indicate that ...", daemon),
            ("The docker daemon is not running.", daemon),
            (
                "Unable to find image 'agent-workbench-sandbox:1' locally\n"
                "docker: Error response from daemon: No such image: x",
                image,
            ),
            ("Error response from daemon: No such image: x", image),
            ("docker: Error response from daemon: pull access denied for x", image),
            ("Error response from daemon: No such container: " + BOX, missing),
            (b"Error: No such object: workbench-box-x\n", missing),
            (
                "first line\nsomething odd\n\n",
                "Docker reported: something odd Check Docker, then retry.",
            ),
            ("x" * 400, "Docker reported: " + "x" * 300 + " Check Docker, then retry."),
            (
                "docker: Error response from daemon: address already in use\n\n"
                "Run 'docker run --help' for more information\n",
                "Docker reported: docker: Error response from daemon: address "
                "already in use Check Docker, then retry.",
            ),
            (
                b"docker: Error response from daemon: OCI runtime create failed.\n"
                b"See 'docker run --help'.\n",
                "Docker reported: docker: Error response from daemon: OCI runtime "
                "create failed. Check Docker, then retry.",
            ),
            ("Run 'docker run --help' for more information\n", NO_MESSAGE),
            (
                "The operation timed out.\r\n".encode("utf-16-le"),
                "Docker reported: The operation timed out. Check Docker, then retry.",
            ),
            ("Cannot connect to the Docker daemon".encode("utf-16-le"), daemon),
            (None, NO_MESSAGE),
            (b"", NO_MESSAGE),
            (" \n", NO_MESSAGE),
        ]:
            self.assertEqual(boxes.docker_error(err), want)

    def test_docker_run_maps_failures(self):
        boom = subprocess.CalledProcessError(
            1,
            ["docker"],
            stderr=b"Cannot connect to the Docker daemon at unix:///x/docker.sock.",
        )
        with self.native(), patch_run(side_effect=boom) as run:
            self.assertEqual(
                self.message(boxes.docker_run, ["ps"]),
                "Docker is not running; start Docker, then retry.",
            )
            self.assertEqual(run.call_args.args[0], ["docker", "ps"])
            self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
            self.assertTrue(run.call_args.kwargs["capture_output"])
            self.assertTrue(run.call_args.kwargs["check"])
            self.assertEqual(run.call_args.kwargs["timeout"], 120)
            self.assertFalse(boxes.image_present())
        with self.native(), patch_run(return_value=done()) as run:
            boxes.docker_run(["exec", "-i", "c", "cat"], input=b"x")
            self.assertNotIn("stdin", run.call_args.kwargs)
            self.assertEqual(run.call_args.kwargs["input"], b"x")
            self.assertTrue(boxes.image_present())
            self.assertEqual(
                run.call_args.args[0], ["docker", "image", "inspect", boxes.IMAGE]
            )
            self.assertEqual(run.call_args.kwargs["timeout"], 20)
        late = subprocess.TimeoutExpired("docker", 120)
        with self.native(), patch_run(side_effect=late):
            self.assertEqual(self.message(boxes.docker_run, ["ps"]), NO_ANSWER)
            self.assertFalse(boxes.image_present())
        with self.native(), patch_run(side_effect=FileNotFoundError):
            self.assertFalse(boxes.image_present())
        with patch.object(boxes.shutil, "which", return_value=None):
            self.assertFalse(boxes.image_present())

    def test_docker_output_that_cannot_be_decoded_still_gives_a_sentence(self):
        with self.native(), patch_run(return_value=done()) as run:
            boxes.docker_run(["ps"], text=True)
            self.assertEqual(run.call_args.kwargs["errors"], "replace")
            boxes.docker_run(["ps"])
            self.assertNotIn("errors", run.call_args.kwargs)
        script = (
            "import sys; sys.stderr.buffer.write("
            "b'\\x81\\x8d\\xff Error: No such container: x'); sys.exit(1)"
        )
        with patch.object(boxes, "docker", return_value=[sys.executable, "-c", script]):
            self.assertEqual(
                self.message(boxes.docker_run, ["x"], text=True),
                "This sandbox does not exist yet; use Open sandbox to create it.",
            )

    def test_docker_run_checks_the_daemon_first(self):
        order = []
        with (
            self.native(),
            patch.object(
                boxes, "ensure_daemon", side_effect=lambda: order.append("daemon")
            ),
            patch_run(side_effect=lambda argv, **kwargs: order.append("run") or done()),
        ):
            boxes.docker_run(["ps"])
        self.assertEqual(order, ["daemon", "run"])

    def test_wsl_daemon_started_once(self):
        calls = []

        def fake(argv, **kwargs):
            calls.append(argv)
            self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
            self.assertTrue(kwargs["capture_output"])
            return done(1 if argv[-1] == "info" and len(calls) == 1 else 0)

        self.wsl()
        info = [*boxes.WSL, "/usr/bin/docker", "info"]
        version = [*boxes.WSL, "/usr/bin/docker", "--version"]
        service = [*boxes.WSL, "/usr/sbin/service", "docker", "start"]
        with patch_run(side_effect=fake):
            boxes.ensure_daemon()
            self.assertEqual(calls, [info, version, service, info])
            self.assertNotIn("--", service)
            boxes.ensure_daemon()
            self.assertEqual(len(calls), 4)
        with (
            patch.object(boxes, "_daemon", False),
            patch_run(return_value=done()) as run,
        ):
            boxes.ensure_daemon()
            boxes.ensure_daemon()
            self.assertEqual(run.call_count, 1)
        silent = {"side_effect": lambda argv, **kwargs: done(argv[-1] == "info")}
        with patch.object(boxes, "_daemon", False), patch_run(**silent) as run:
            self.assertEqual(self.message(boxes.ensure_daemon), NO_DAEMON)
            self.assertEqual(run.call_count, 33)
            self.assertEqual(boxes.time.sleep.call_count, 29)
            self.assertFalse(boxes._daemon)
        with (
            patch.object(boxes, "_daemon", False),
            patch.object(boxes, "docker", return_value=["/usr/bin/docker"]),
            patch_run(side_effect=AssertionError),
        ):
            boxes.ensure_daemon()
            self.assertFalse(boxes._daemon)

    def test_wsl_without_docker_fails_at_once(self):
        self.wsl()
        version = [*boxes.WSL, "/usr/bin/docker", "--version"]
        for failure in (
            {"return_value": done(1)},
            {"side_effect": OSError("no wsl")},
            {"side_effect": subprocess.TimeoutExpired("wsl", 30)},
        ):
            with patch_run(**failure) as run:
                self.assertEqual(self.message(boxes.ensure_daemon), NO_DOCKER)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(run.call_args.args[0], version)
                self.assertFalse(boxes.image_present())
                self.assertEqual(run.call_count, 4)
            self.assertFalse(boxes._daemon)
        boxes.time.sleep.assert_not_called()

    def test_wsl_daemon_wait_is_bounded(self):
        clock = [0.0]

        def hung(argv, **kwargs):
            if argv[-1] == "--version":
                return done()
            clock[0] += kwargs["timeout"]
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

        def sleep(seconds):
            clock[0] += seconds

        self.wsl()
        boxes.time.sleep.side_effect = sleep
        with (
            patch.object(boxes.time, "monotonic", side_effect=lambda: clock[0]),
            patch_run(side_effect=hung),
        ):
            self.assertEqual(self.message(boxes.ensure_daemon), NO_DAEMON)
        self.assertLess(clock[0], 200)

    def test_wsl_daemon_restarted_when_it_stops_later(self):
        state = {"up": False}
        calls = []

        def fake(argv, **kwargs):
            calls.append(argv[len(boxes.WSL) :])
            if "service" in argv[-3]:
                state["up"] = True
            elif argv[-1] != "--version" and not state["up"]:
                if kwargs.get("check"):
                    raise subprocess.CalledProcessError(1, argv, stderr=STOPPED)
                return done(1, err=STOPPED)
            return done(0, "names")

        self.wsl()
        with patch.object(boxes, "_daemon", True), patch_run(side_effect=fake) as run:
            self.assertEqual(boxes.docker_run(["ps"], input=b"x").stdout, "names")
            self.assertEqual(
                calls,
                [
                    ["/usr/bin/docker", "ps"],
                    ["/usr/bin/docker", "info"],
                    ["/usr/bin/docker", "--version"],
                    ["/usr/sbin/service", "docker", "start"],
                    ["/usr/bin/docker", "info"],
                    ["/usr/bin/docker", "ps"],
                ],
            )
            self.assertEqual(run.call_args.kwargs["input"], b"x")
            self.assertTrue(boxes._daemon)
        gone = subprocess.CalledProcessError(1, ["wsl"], stderr=STOPPED)
        with (
            patch.object(boxes, "_daemon", True),
            patch_run(side_effect=[gone, done(), gone]) as run,
        ):
            with self.assertRaises(ValueError) as caught:
                boxes.docker_run(["ps"])
            self.assertEqual(str(caught.exception), NOT_RUNNING)
            self.assertIsNone(caught.exception.__context__)
            self.assertEqual(run.call_count, 3)
        with (
            self.native(),
            patch.object(boxes, "_daemon", False),
            patch_run(side_effect=gone) as run,
        ):
            self.assertEqual(self.message(boxes.docker_run, ["ps"]), NOT_RUNNING)
            self.assertEqual(run.call_count, 1)

    def test_create_never_pulls(self):
        args = boxes.create_args(BOX)
        self.assertEqual(args[:3], ["run", "-d", "--pull=never"])
        self.assertEqual(args[-1], boxes.IMAGE)
        self.assertEqual(args[args.index("-p") + 1], "127.0.0.1::22")
        self.assertEqual(args[args.index("--name") + 1], BOX)
        self.assertEqual(args[args.index("--label") + 1], "org.agent-workbench.box=1")
        self.assertEqual(args[args.index("--security-opt") + 1], "no-new-privileges")
        self.assertEqual(
            args[args.index("--mount") + 1],
            "type=volume,source=workbench-box-dev-work,target=/workspace",
        )

    def test_attach_sequence(self):
        seen = []
        keygen = []

        def fake_run(args, **kwargs):
            seen.append((args, kwargs))
            return done(0, PORTS if args[0] == "inspect" else "")

        def fake_keygen(argv, **kwargs):
            keygen.append((argv, kwargs))
            Path(argv[-1]).write_text("placeholder\n", newline="\n")
            Path(argv[-1]).with_suffix(".pub").write_text("ssh-ed25519 client\n", newline="\n")
            return done()

        proc = MagicMock(pid=os.getpid())
        settings = {"provider": "Managed local", "context": 8192}
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(boxes, "DATA", Path(tmp) / "My Data"),
            patch.object(boxes, "start") as start,
            patch.object(boxes, "docker_run", side_effect=fake_run),
            patch.object(boxes, "host_key", return_value=["ssh-ed25519", KEY]) as hk,
            patch.object(boxes, "command", side_effect=lambda name: [name]),
            patch_run(side_effect=fake_keygen),
            patch.object(boxes, "open_tunnel", return_value=proc) as tunnel,
            patch.object(boxes, "model_reachable", return_value=True) as reachable,
        ):
            self.assertEqual(boxes.attach("dev", settings), BOX)
            work = Path(tmp) / "My Data" / "boxes" / "dev"
            start.assert_called_once_with(settings)
            hk.assert_called_once_with(BOX)
            self.assertEqual(
                (work / "known_hosts").read_text(),
                f"[127.0.0.1]:32768 ssh-ed25519 {KEY}\n",
            )
            args, log, ready = tunnel.call_args.args
            self.assertIn(
                'UserKnownHostsFile="' + (work / "known_hosts").as_posix() + '"', args
            )
            self.assertEqual(args[args.index("-p") + 1], "32768")
            self.assertEqual(args[args.index("-i") + 1], str(work / "forward"))
            self.assertEqual(log, work / "ssh.log")
            self.assertTrue(ready())
            reachable.assert_called_once_with(BOX)
            self.assertEqual(
                [call[0][0] for call in seen], ["ps", "run", "inspect", "exec", "exec"]
            )
            self.assertEqual(seen[1][0], boxes.create_args(BOX))
            self.assertEqual(seen[3][1]["input"], b"ssh-ed25519 client\n")
            self.assertTrue(all("capture_output" not in call[1] for call in seen))
            self.assertEqual(keygen[0][0][0], "ssh-keygen")
            self.assertTrue(keygen[0][1]["capture_output"])
            self.assertIs(keygen[0][1]["stdin"], subprocess.DEVNULL)
            record = boxes.json.loads((work / "tunnel.json").read_text())
            self.assertEqual(
                (record["pid"], record["port"], record["target"]),
                (os.getpid(), "32768", boxes.PORT),
            )
            self.assertEqual(boxes.attach("dev", settings), BOX)
            self.assertEqual(tunnel.call_count, 1)
            self.assertEqual(len(keygen), 1)
        self.assertEqual(
            self.message(boxes.attach, "dev", {"provider": "OpenRouter"}),
            "The sandbox uses the managed local model; choose Managed local as the "
            "model source, then retry.",
        )

    def test_attach_refuses_foreign_and_stopped_containers(self):
        replies = {"inspect": PORTS}

        def fake_run(args, **kwargs):
            if args[0] == "ps":
                return done(0, BOX + "\n")
            if args[:2] == ["inspect", "-f"]:
                return done(0, replies["label"])
            return done(0, replies["inspect"] if args[0] == "inspect" else "")

        settings = {"provider": "Managed local", "context": 8192}
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(boxes, "DATA", Path(tmp)),
            patch.object(boxes, "start"),
            patch.object(boxes, "docker_run", side_effect=fake_run) as run,
            patch_run(side_effect=AssertionError),
            patch.object(boxes, "open_tunnel", side_effect=AssertionError),
        ):
            replies["label"] = "\n"
            self.assertEqual(
                self.message(boxes.attach, "dev", settings),
                "A container named workbench-box-dev already exists and was not "
                "created by Workbench; choose another sandbox name.",
            )
            self.assertNotIn(["start", BOX], verbs(run))
            replies["label"] = "1\n"
            for shape in (
                '[{"NetworkSettings": {"Ports": {}}}]',
                '[{"NetworkSettings": {"Ports": {"22/tcp": null}}}]',
                '[{"NetworkSettings": {"Ports": {"22/tcp": []}}}]',
            ):
                replies["inspect"] = shape
                self.assertEqual(
                    self.message(boxes.attach, "dev", settings),
                    "The sandbox container is not running; " + RESTART,
                )
            self.assertIn(["start", BOX], verbs(run))

    def test_stop_box_checks_ownership(self):
        with patch.object(boxes, "docker_run", return_value=done(0, "1\n")) as run:
            boxes.stop_box("dev")
            self.assertEqual(verbs(run), [["inspect", "-f", LABEL, BOX], ["stop", BOX]])
        with patch.object(boxes, "docker_run", return_value=done(0, "\n")) as run:
            self.assertEqual(
                self.message(boxes.stop_box, "dev"),
                "The container workbench-box-dev was not created by Workbench; "
                "stop it with Docker yourself.",
            )
            self.assertEqual(run.call_count, 1)
        gone = subprocess.CalledProcessError(
            1, ["docker"], stderr="Error: No such object: workbench-box-dev\n"
        )
        with self.native(), patch_run(side_effect=gone):
            self.assertEqual(
                self.message(boxes.stop_box, "dev"),
                "This sandbox does not exist yet; use Open sandbox to create it.",
            )
        self.assertIn("lowercase letters", self.message(boxes.stop_box, "../escape"))

    def test_build_and_model_probe(self):
        with self.native(), patch.object(boxes, "docker_run") as run:
            boxes.build()
            run.assert_called_once_with(
                ["build", "-t", boxes.IMAGE, str(boxes.ROOT / "sandbox")], timeout=1800
            )
        self.wsl()
        with patch.object(boxes, "docker_run") as run:
            boxes.build()
            run.assert_called_once_with(
                ["build", "-t", boxes.IMAGE, "sandbox"], timeout=1800
            )
        probe = ["curl", "-s", "--max-time", "1", "http://127.0.0.1:10532/v1/models"]
        for reply, want in [(done(0), True), (done(7), False)]:
            with patch_run(return_value=reply) as run:
                self.assertIs(boxes.model_reachable("c"), want)
                self.assertEqual(
                    run.call_args.args[0],
                    [*boxes.WSL, "/usr/bin/docker", "exec", "c", *probe],
                )
                self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        with patch_run(side_effect=subprocess.TimeoutExpired("docker", 20)):
            self.assertFalse(boxes.model_reachable("c"))


if __name__ == "__main__":
    unittest.main()
