"""Command line for Agent Workbench: serve or stop the model, open an agent, list sessions, allow a folder's tools, run checks."""

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

if __name__ == "__main__" and sys.argv[1:2] == ["doctor"]:
    # Doctor has to work when the data folder or the settings file is unusable, so it starts before runtime loads.
    import doctor

    raise SystemExit(doctor.main(sys.argv[2:]))

from runtime import (
    DATA,
    ROOT,
    URL,
    Cancelled,
    StartFailed,
    endpoint_model,
    key,
    owned,
    settings,
    start,
    stop,
)


def caller_dir():
    raw = os.environ.get("WORKBENCH_CALLER_CWD", "")
    return Path(raw) if raw and Path(raw).is_absolute() and Path(raw).is_dir() else Path.cwd()


def workspace_path(value):
    path = value.expanduser()
    return (path if path.is_absolute() else caller_dir() / path).resolve()


def on_terminal():
    return bool(sys.stdin) and sys.stdin.isatty()


def plain(text):
    encoding = getattr(sys.stdout, "encoding", None) or "utf8"
    shown = "".join(c if c.isprintable() else " " for c in str(text))
    return shown.encode(encoding, "replace").decode(encoding)


def show_sessions(workspace, as_json):
    import sessions

    folder = sessions.project_dir(str(workspace)) if workspace else None
    live = sessions.running(folder)["sessions"]
    past = sessions.recent(folder, None, 20) if folder else []
    if as_json:
        print(json.dumps(dict(running=live, recent=past), indent=2))
        return
    print("Running")
    for row in live:
        print(
            plain(
                f"  {row['kind']:<9}{row['harness'] or '-':<12} {row['title']}  {row['workspace']}"
            )
        )
    if not live:
        print("  none")
    if folder:
        print(plain(f"Recent in {folder}"))
        for row in past:
            line = f"  {row['updated'][:16].replace('T', ' ')}  {row['harness']:<12} {row['title']}"
            print(plain(line + ("  " + row["command"] if row["command"] else "")))
        if not past:
            print("  none")


def ask_running():
    if on_terminal():
        try:
            answer = input(
                "Another setup is serving a model. Use the running model for this project? "
                "[y = use it, n = open Workbench, Enter = cancel] "
            )
        except (EOFError, KeyboardInterrupt):
            return None
        return {"y": True, "yes": True, "n": False, "no": False}.get(
            answer.strip().lower()
        )
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        try:
            return messagebox.askyesnocancel(
                "Another setup is serving",
                "Use the running model for this project?\n\nYes opens the agent with the running model. No opens Workbench to change the server.",
            )
        finally:
            root.destroy()
    except Exception:
        raise SystemExit(
            "Another setup is serving a model; stop it in Agent Workbench, or run this launcher from a terminal to choose."
        ) from None


def trust(folder, harness):
    import project_setup

    status = project_setup.consent_status(str(folder), harness)
    print(plain(f"Project folder: {folder}"))
    if not status["items"] or not status["needed"]:
        print("Nothing in this folder needs review.")
        return 0
    for item in status["items"]:
        readers = ", ".join(item["read_by"])
        print(plain(f"{item['path']} ({item['kind']}, read by {readers}): {item['detail']}"))
    answer = ""
    if on_terminal():
        try:
            answer = input(
                "Allow these files to start programs when you launch an agent in this folder? [y/N] "
            )
        except (EOFError, KeyboardInterrupt):
            pass
    if answer.strip().lower() not in ("y", "yes"):
        print("Nothing was allowed.")
        return 1
    try:
        project_setup.grant(str(folder), status["digest"])
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print("Allowed for this folder. Launch again.")
    return 0


def quoted(folder, windows=os.name == "nt"):
    """The folder as one word to paste into a shell: double quotes, or single quotes where a POSIX shell would expand it."""
    text = str(folder)
    if not windows and any(c in text for c in '$`"\\!'):
        return shlex.quote(text)
    return f'"{text}"'


def with_trust_hint(exc, s, setup=None):
    """The launch error, naming the trust verb when the folder's unreviewed tools are what stopped the launch."""
    import project_setup

    if str(exc) != project_setup.NEEDS_CONSENT:
        return exc
    saved = f" --setup-id {setup}" if setup else ""
    return ValueError(
        f"{exc} From a terminal: workbench trust{saved} --workspace {quoted(s['workspace'])}"
    )


def agent(s, here, setup=None):
    import host

    try:
        if on_terminal() and (here or not host.can_open_window()):
            import secrets
            import session_window
            from connections import ready_session

            argv, env, cwd = ready_session(s)
            os.chdir(cwd)
            return session_window.run(
                secrets.token_hex(16), s["harness"], argv, env=env, handshake=False
            )
        from connections import launch

        launch(s)
    except ValueError as exc:
        raise with_trust_hint(exc, s, setup) from None
    return 0


def begin(s):
    """Start the model; an interrupt or a failed start ends with its sentence instead of a traceback."""
    try:
        start(s)
    except KeyboardInterrupt:
        raise SystemExit("Stopped before the model finished loading.") from None
    except (Cancelled, StartFailed) as exc:
        raise SystemExit(str(exc)) from None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["doctor"]:
        import doctor

        return doctor.main(argv[1:])
    parser = argparse.ArgumentParser(description="Agent Workbench")
    parser.add_argument(
        "action",
        choices=[
            "serve",
            "stop",
            "status",
            "agent",
            "bench",
            "web-start",
            "web-stop",
            "sessions",
            "trust",
            "doctor",
        ],
    )
    parser.add_argument("--setup-id")
    parser.add_argument(
        "--workspace", type=Path, help="Open the agent in this existing project folder."
    )
    parser.add_argument(
        "--here",
        action="store_true",
        help="Run the agent in this terminal instead of opening a window.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print sessions or doctor results as JSON.",
    )
    args = parser.parse_args(argv)
    if args.action == "doctor":
        import doctor

        return doctor.main(["--json"] if args.json else [])
    import tooling

    tooling.refresh_path()
    workspace = None
    if args.workspace:
        workspace = workspace_path(args.workspace)
        if not workspace.is_dir():
            parser.error(f"The project folder {workspace} does not exist.")
    os.environ.pop("WORKBENCH_CALLER_CWD", None)
    if args.action == "sessions":
        return show_sessions(workspace, args.json)
    s = settings()
    if args.setup_id:
        from shortcuts import ident

        match = next(
            (v for k, v in s["named_profiles"].items() if ident(k) == args.setup_id),
            None,
        )
        if match is None:
            raise SystemExit("Saved setup was removed.")
        s.update(match)
    if workspace:
        s["workspace"] = str(workspace)
    if args.action == "trust":
        return trust(s["workspace"], s.get("harness"))
    if (
        not s["model"]
        and s["engine"] not in ("vLLM", "Custom")
        and (
            args.action in ("serve", "bench")
            or (args.action == "agent" and s["provider"] == "Managed local")
        )
    ):
        raise SystemExit("Choose a model in Agent Workbench first.")
    if args.action == "serve":
        begin(s)
        print(URL + "/v1")
    elif args.action == "agent":
        if args.setup_id and s["provider"] == "Managed local":
            from runtime import same_server

            proc, rec = owned()
            if proc and not same_server(s, rec):
                decision = ask_running()
                if decision is None:
                    return
                if not decision:
                    subprocess.Popen([sys.executable, str(ROOT / "desktop_app.py")])
                    return
                s.update({k: v for k, v in rec["spec"].items() if k in s})
        return agent(s, args.here, setup=args.setup_id)
    elif args.action == "stop":
        stop()
    elif args.action == "status":
        proc, rec = owned()
        if proc:
            try:
                name = endpoint_model()
            except Exception:
                name = "Starting or unavailable"
            print(plain(f"{name} {rec['spec']}"))
        else:
            print("Stopped")
    elif args.action == "bench":
        begin(s)
        path = DATA / ("benchmark-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
        done = subprocess.run(
            [
                sys.executable,
                str(ROOT / "benchmark.py"),
                Path(s["model"]).name,
                str(path),
                "--context",
                str(s["context"]),
            ],
            env=dict(os.environ, WORKBENCH_API_KEY=key()),
        )
        if done.returncode:
            raise SystemExit(done.returncode)
        print(plain(path))
    else:
        web(args.action == "web-start")


def _host_docker_ready(docker_exe):
    try:
        r = subprocess.run(
            [docker_exe, "info", "--format", "{{.ServerVersion}}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _wsl_docker_available():
    import shutil

    if sys.platform != "win32" or not shutil.which("wsl"):
        return False
    try:
        r = subprocess.run(
            [
                "wsl",
                "-d",
                "AgentWorkbench-Web",
                "-u",
                "root",
                "--exec",
                "test",
                "-x",
                "/usr/bin/docker",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def web(up):
    import shutil
    import requests
    from filelock import FileLock

    with FileLock(str(DATA / "web.lock"), timeout=180):
        prefix = []
        compose = ["docker", "compose", "-f", str(ROOT / "firecrawl.compose.yml")]
        docker_exe = shutil.which("docker")
        use_wsl = False
        if not docker_exe:
            if sys.platform != "win32":
                raise ValueError("Install Docker with the Compose plugin and start it before enabling Firecrawl.")
            use_wsl = True
        elif (
            sys.platform == "win32"
            and not _host_docker_ready(docker_exe)
            and _wsl_docker_available()
        ):
            use_wsl = True
        if use_wsl:
            prefix = [
                "wsl",
                "-d",
                "AgentWorkbench-Web",
                "-u",
                "root",
                "--cd",
                str(ROOT),
                "--exec",
            ]
            if up:
                subprocess.run(
                    prefix + ["service", "docker", "start"],
                    check=True,
                    stdin=subprocess.DEVNULL,
                )
            compose = prefix + ["docker", "compose", "-f", "firecrawl.compose.yml"]
        subprocess.run(
            compose + (["up", "-d"] if up else ["stop"]),
            check=True,
            stdin=subprocess.DEVNULL,
        )
        if up:
            if prefix:
                # A foreground WSL client keeps the distribution alive. Docker wait exits on stack stop.
                from runtime import load, write_json
                import psutil

                pidfile = DATA / "web-watch.json"
                state = load(pidfile, {})
                try:
                    proc = psutil.Process(state["pid"])
                    active = proc.is_running() and proc.create_time() == state["born"]
                except (KeyError, psutil.NoSuchProcess):
                    active = False
                if not active:
                    child = subprocess.Popen(
                        prefix + ["docker", "wait", "agent-workbench-web-api-1"],
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    write_json(
                        pidfile,
                        {
                            "pid": child.pid,
                            "born": psutil.Process(child.pid).create_time(),
                        },
                    )
            for _ in range(90):
                try:
                    r = requests.get("http://127.0.0.1:3002/", timeout=2)
                    if r.ok and "Firecrawl" in r.text:
                        return
                except requests.RequestException:
                    pass
                time.sleep(1)
            raise RuntimeError("Firecrawl did not become ready on port 3002.")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from None
    except KeyboardInterrupt:
        raise SystemExit("Cancelled.") from None
