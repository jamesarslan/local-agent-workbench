"""Named Docker workspaces with SSH forwarding to the loopback model service."""

import json
import os
import re
import shutil
import subprocess
import time
from filelock import FileLock

from host import command, terminal
from runtime import ALIAS, DATA, PORT, ROOT, QUIET, key, start

IMAGE = "agent-workbench-sandbox:1"
WINDOWS = os.name == "nt"
WSL = ["wsl", "-d", "AgentWorkbench-Web", "-u", "root", "--cd", str(ROOT), "--exec"]
RESTART = "use Stop sandbox, then Open sandbox again."
NO_DOCKER = "Docker was not found; install it in Advanced → Setup → Docker, then retry."
NOT_RUNNING = "Docker is not running; start Docker, then retry."
HELP_LINE = re.compile(r"(?:See|Run) 'docker .*--help'")
_daemon = False


def docker():
    if shutil.which("docker"):
        return [shutil.which("docker")]
    if WINDOWS and shutil.which("wsl"):
        return [*WSL, "/usr/bin/docker"]
    raise ValueError(NO_DOCKER)


def ensure_daemon():
    """Start Docker inside the Workbench WSL distribution when it is not answering."""
    global _daemon
    prefix = docker()
    if prefix[0] != "wsl" or _daemon:
        return

    def call(argv, limit):
        try:
            return subprocess.run(
                argv,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=limit,
                creationflags=QUIET,
            ).returncode
        except (OSError, subprocess.TimeoutExpired):
            return 1

    if call(prefix + ["info"], 30):
        if call(prefix + ["--version"], 30):
            raise ValueError(NO_DOCKER)
        call([*WSL, "/usr/sbin/service", "docker", "start"], 60)
        deadline = time.monotonic() + 60
        tries = 0
        while call(prefix + ["info"], 30):
            tries += 1
            if tries == 30 or time.monotonic() > deadline:
                raise ValueError(
                    "Docker did not start inside WSL; open Advanced → Setup → "
                    "Docker, run Install again, then retry."
                )
            time.sleep(1)
    _daemon = True


def docker_error(stderr):
    text = stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr
    text = (text or "").replace("\x00", "")
    low = text.lower()
    if "permission denied" in low and "docker" in low and "sock" in low:
        return (
            "Your account is not allowed to use Docker; add it to the docker group, "
            "sign in again, then retry."
        )
    if any(
        mark in low
        for mark in (
            "cannot connect to the docker daemon",
            "error during connect",
            "daemon is not running",
        )
    ):
        return NOT_RUNNING
    if any(
        mark in low
        for mark in ("no such image", "unable to find image", "pull access denied")
    ):
        return (
            "The sandbox image is not built yet; open Advanced → Setup → "
            "Sandbox image → Install, then retry."
        )
    if "no such container" in low or "no such object" in low:
        return "This sandbox does not exist yet; use Open sandbox to create it."
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line and not HELP_LINE.match(line)]
    if lines:
        return f"Docker reported: {lines[-1][:300]} Check Docker, then retry."
    return "Docker failed without a message; check that Docker is running, then retry."


def docker_run(args, **kwargs):
    global _daemon
    ensure_daemon()
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("timeout", 120)
    if "input" not in kwargs:
        kwargs.setdefault("stdin", subprocess.DEVNULL)
    if kwargs.get("text"):
        kwargs.setdefault("errors", "replace")
    for last in (False, True):
        try:
            return subprocess.run(
                [*docker(), *args], check=True, creationflags=QUIET, **kwargs
            )
        except subprocess.CalledProcessError as exc:
            reason = docker_error(exc.stderr)
        except subprocess.TimeoutExpired:
            reason = (
                "Docker did not answer in time; check that Docker is running, "
                "then retry."
            )
        if last or not _daemon or reason != NOT_RUNNING:
            raise ValueError(reason)
        _daemon = False
        ensure_daemon()


def image_present():
    try:
        docker_run(["image", "inspect", IMAGE], timeout=20)
        return True
    except (ValueError, OSError):
        return False


def box_name(name):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,35}", name):
        raise ValueError("Use 1–36 lowercase letters, digits or dashes.")
    return "workbench-box-" + name


def build():
    docker_run(
        [
            "build",
            "-t",
            IMAGE,
            "sandbox" if docker()[0] == "wsl" else str(ROOT / "sandbox"),
        ],
        timeout=1800,
    )


def create_args(container):
    return [
        "run",
        "-d",
        "--pull=never",
        "--name",
        container,
        "--label",
        "org.agent-workbench.box=1",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "256",
        "--memory",
        "3g",
        "-p",
        "127.0.0.1::22",
        "--mount",
        "type=volume,source=" + container + "-work,target=/workspace",
        IMAGE,
    ]


def host_key(container):
    for _ in range(40):
        try:
            r = subprocess.run(
                docker()
                + ["exec", container, "cat", "/etc/ssh/ssh_host_ed25519_key.pub"],
                capture_output=True,
                text=True,
                errors="replace",
                stdin=subprocess.DEVNULL,
                timeout=20,
                creationflags=QUIET,
            )
            fields = r.stdout.split()
            if (
                r.returncode == 0
                and len(fields) >= 2
                and fields[0] == "ssh-ed25519"
                and len(fields[1]) == 68
            ):
                return fields[:2]
        except subprocess.TimeoutExpired:
            pass
        time.sleep(0.25)
    raise ValueError("The sandbox did not finish starting; " + RESTART)


def ssh_path(path):
    """A path as one ssh option value: quoted for spaces, with % kept literal."""
    return '"' + path.as_posix().replace("%", "%%") + '"'


def tunnel_args(port, identity, known):
    key = ["-i", str(identity)]
    if "%" in str(identity):
        key = ["-o", "IdentityFile=" + ssh_path(identity)]
    return command("ssh") + [
        "-NT",
        "-p",
        str(port),
        *key,
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "UserKnownHostsFile=" + ssh_path(known),
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=15",
        "-R",
        f"127.0.0.1:10532:127.0.0.1:{PORT}",
        "node@127.0.0.1",
    ]


def model_reachable(container):
    try:
        test = subprocess.run(
            docker()
            + [
                "exec",
                container,
                "curl",
                "-s",
                "--max-time",
                "1",
                "http://127.0.0.1:10532/v1/models",
            ],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=20,
            creationflags=QUIET,
        )
    except subprocess.TimeoutExpired:
        return False
    return test.returncode == 0


def open_tunnel(args, log_path, ready, attempts=5):
    for _ in range(attempts):
        with log_path.open("w") as log:
            proc = subprocess.Popen(
                args,
                creationflags=QUIET,
                stdin=subprocess.DEVNULL,
                stderr=log,
                stdout=subprocess.DEVNULL,
            )
        for _ in range(20):
            if proc.poll() is not None:
                break
            if ready():
                return proc
            time.sleep(0.25)
        else:
            proc.terminate()
            raise ValueError(
                "The sandbox could not reach the local model; check that the model "
                "is serving, then use Open sandbox again."
            )
        time.sleep(0.5)
    lines = [
        line.strip()
        for line in log_path.read_text(errors="replace").splitlines()
        if line.strip()
    ]
    reason = f" ({lines[-1][:300]})" if lines else ""
    raise ValueError("The sandbox SSH tunnel stopped" + reason + "; " + RESTART)


def attach(name, s):
    container = box_name(name)
    if s["provider"] != "Managed local":
        raise ValueError(
            "The sandbox uses the managed local model; choose Managed local as the "
            "model source, then retry."
        )
    start(s)
    work = DATA / "boxes" / name
    work.mkdir(parents=True, exist_ok=True)
    with FileLock(str(work / "box.lock"), timeout=240):
        listing = docker_run(
            [
                "ps",
                "-a",
                "--filter",
                "name=^/" + container + "$",
                "--format",
                "{{.Names}}",
            ],
            text=True,
        )
        if container not in listing.stdout.splitlines():
            docker_run(create_args(container))
        else:
            owner = docker_run(
                [
                    "inspect",
                    "-f",
                    '{{index .Config.Labels "org.agent-workbench.box"}}',
                    container,
                ],
                text=True,
            )
            if owner.stdout.strip() != "1":
                raise ValueError(
                    f"A container named {container} already exists and was not "
                    "created by Workbench; choose another sandbox name."
                )
            docker_run(["start", container])
        info = json.loads(docker_run(["inspect", container]).stdout)[0]
        try:
            port = info["NetworkSettings"]["Ports"]["22/tcp"][0]["HostPort"]
        except (KeyError, TypeError, IndexError):
            raise ValueError(
                "The sandbox container is not running; " + RESTART
            ) from None
        identity = work / "forward"
        if not identity.exists():
            subprocess.run(
                command("ssh-keygen")
                + ["-q", "-t", "ed25519", "-N", "", "-f", str(identity)],
                check=True,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                creationflags=QUIET,
            )
        docker_run(
            [
                "exec",
                "-i",
                "-u",
                "node",
                container,
                "sh",
                "-ec",
                "umask 077; mkdir -p ~/.ssh; cat > ~/.ssh/authorized_keys",
            ],
            # sshd reads authorized_keys line by line; a Windows line ending would end up in the key.
            input=identity.with_suffix(".pub").read_bytes().replace(b"\r\n", b"\n"),
        )
        pub = host_key(container)
        known = work / "known_hosts"
        known.write_text(f"[127.0.0.1]:{port} {pub[0]} {pub[1]}\n")
        # Check ownership through the actual existing SSH connection before considering it reusable.
        import psutil
        from runtime import load, write_json

        rec = load(work / "tunnel.json", {})
        try:
            process = psutil.Process(rec["pid"])
            reuse = (
                process.is_running()
                and process.create_time() == rec["born"]
                and rec["port"] == port
                and rec.get("target") == PORT
            )
        except (KeyError, psutil.NoSuchProcess):
            reuse = False
        if not reuse:
            proc = open_tunnel(
                tunnel_args(port, identity, known),
                work / "ssh.log",
                lambda: model_reachable(container),
            )
            write_json(
                work / "tunnel.json",
                dict(
                    pid=proc.pid,
                    born=psutil.Process(proc.pid).create_time(),
                    port=port,
                    target=PORT,
                ),
            )
        cfg = {
            "providers": {
                "workbench": {
                    "api": "openai-completions",
                    "baseUrl": "http://127.0.0.1:10532/v1",
                    "apiKey": "WORKBENCH_SESSION_KEY",
                    "models": [
                        {
                            "id": ALIAS,
                            "name": ALIAS,
                            "input": ["text"],
                            "supportsTools": True,
                            "contextWindow": int(s["context"]),
                            "maxTokens": min(int(s["context"]) // 2, 16384),
                            "compat": {"supportsDeveloperRole": False},
                        }
                    ],
                }
            }
        }
        docker_run(
            [
                "exec",
                "-i",
                "-u",
                "node",
                container,
                "sh",
                "-ec",
                "mkdir -p ~/.omp/agent; cat > ~/.omp/agent/models.yml",
            ],
            input=json.dumps(cfg).encode(),
        )
    return container


def agent(name, s, interactive=True):
    container = attach(name, s)
    env = dict(os.environ, WORKBENCH_SESSION_KEY=key())
    if docker()[0] == "wsl":
        env["WSLENV"] = ":".join(
            filter(None, [env.get("WSLENV"), "WORKBENCH_SESSION_KEY"])
        )
    argv = docker() + [
        "exec",
        "-it" if interactive else "-i",
        "-u",
        "node",
        "-w",
        "/workspace",
        "-e",
        "WORKBENCH_SESSION_KEY",
        container,
        "omp",
        "--offline",
        "--model",
        "workbench/" + ALIAS,
        "--approval-mode",
        "write",
        "--append-system-prompt",
        s["prompt"],
    ]
    if interactive:
        terminal(argv, env=env)
    return argv, env


def stop_box(name):
    name = box_name(name)
    owner = docker_run(
        ["inspect", "-f", '{{index .Config.Labels "org.agent-workbench.box"}}', name],
        text=True,
    )
    if owner.stdout.strip() != "1":
        raise ValueError(
            f"The container {name} was not created by Workbench; "
            "stop it with Docker yourself."
        )
    docker_run(["stop", name])
