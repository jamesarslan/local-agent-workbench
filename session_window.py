"""Run inside the user's terminal and record the real harness process for the launcher."""

import os
import signal
import subprocess
import sys
import time

import psutil
from host import process_record, process_state, safe_argv
from runtime import DATA, write_json, load

SETTLE = 1.0  # seconds a harness must survive before the launch counts as started
QUICK = 30  # a non-zero exit sooner than this is recorded as a failed start


def drop(path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def prune(folder):
    now = time.time()
    for pattern, age in (("*.ready", 60), ("*.exit", 600)):
        for path in folder.glob(pattern):
            try:
                if now - path.stat().st_mtime > age:
                    path.unlink(missing_ok=True)
            except OSError:
                pass


def active_sessions():
    sessions = []
    folder = DATA / "interactive"
    for path in folder.glob("*.json"):
        try:
            rec = load(path, {})
        except (OSError, ValueError):
            continue
        state = process_state(rec) if isinstance(rec, dict) else "gone"
        if state == "live":
            sessions.append(rec)
        elif state == "gone":
            drop(path)
    prune(folder)
    # Interactive Workbench windows opened by the earlier launcher have no ticket.
    known = {entry["pid"] for entry in sessions}
    for proc in psutil.process_iter(["name"]):
        if proc.pid in known or (proc.info["name"] or "").lower() not in (
            "omp",
            "omp.exe",
        ):
            continue
        try:
            argv = proc.cmdline()
            if (
                "--profile" not in argv
                or "--cwd" not in argv
                or "-p" in argv
                or "--print" in argv
            ):
                continue
            profile = argv[argv.index("--profile") + 1]
            if profile.startswith(("local-agent-workbench", "workbench-")):
                sessions.append(
                    dict(
                        pid=proc.pid,
                        born=proc.create_time(),
                        harness="OMP",
                        workspace=argv[argv.index("--cwd") + 1],
                    )
                )
        except (IndexError, psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return sessions


def recent_exits(max_age=600):
    """Failed starts left by run(), newest first, for the UI to show after the launcher has returned."""
    now, found = time.time(), []
    for path in (DATA / "interactive").glob("*.exit"):
        try:
            age = now - path.stat().st_mtime
            rec = load(path, {})
        except (OSError, ValueError):
            continue
        if (
            age < max_age
            and isinstance(rec, dict)
            and all(isinstance(rec.get(k), str) for k in ("harness", "workspace", "message"))
            and type(rec.get("code")) is int
        ):
            found.append((age, rec))
    return [rec for _, rec in sorted(found, key=lambda item: item[0])[:10]]


def model_of(argv):
    spot = argv.index("--model") + 1 if "--model" in argv else len(argv)
    return str(argv[spot]) if spot < len(argv) else ""


def keep_interrupts_for_child():
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except ValueError:
        pass


def put(path, data):
    try:
        write_json(path, data)
    except OSError:
        pass


def run(ticket, harness, argv, env=None, handshake=True):
    folder = DATA / "interactive"
    try:
        folder.mkdir(exist_ok=True)
    except OSError:
        pass
    ready, record, mark = (
        folder / (ticket + suffix) for suffix in (".ready", ".json", ".exit")
    )
    began, code, error = time.time(), 1, ""

    try:
        proc = subprocess.Popen(safe_argv(argv), env=env)
        keep_interrupts_for_child()
        try:
            meta = process_record(proc.pid)
        except Exception:
            meta = dict(pid=proc.pid, born=began, cmd="")
        meta |= dict(
            id=ticket,
            kind="native",
            harness=harness,
            workspace=os.getcwd(),
            started=began,
            model=model_of(argv),
        )
        put(record, meta)
        try:
            code = proc.wait(timeout=SETTLE)
        except subprocess.TimeoutExpired:
            code = None
        if code:
            error = f"{harness} exited with code {code} right after starting; start it from the Workbench terminal dock to read its message."
        else:
            if handshake:
                put(ready, {"ok": True})
            if code is None:
                try:
                    put(record, meta | {"cmd": process_record(proc.pid)["cmd"]})
                except Exception:
                    pass
                code = proc.wait()
    except (OSError, ValueError) as exc:
        error = (
            str(exc)
            if isinstance(exc, ValueError)
            else f"{harness} could not be started ({exc.strerror or exc}); open Setup and reinstall it."
        )
    finally:
        drop(record)
    seconds = time.time() - began
    if error and handshake:
        put(ready, {"error": error})
    if code and seconds < QUICK:
        message = (
            error
            or f"{harness} exited with code {code} after {round(seconds)} seconds; start it from the Workbench terminal dock to read its message."
        )
        put(
            mark,
            dict(
                harness=harness,
                workspace=os.getcwd(),
                code=code,
                seconds=round(seconds, 1),
                ended=time.time(),
                message=message,
            ),
        )
        print(message, file=sys.stderr)
        if handshake and sys.platform != "darwin" and sys.stdin.isatty():
            try:
                input("Press Enter to close this window.")
            except (EOFError, KeyboardInterrupt):
                pass
    return code


if __name__ == "__main__":
    sys.exit(run(sys.argv[1], sys.argv[2], sys.argv[3:]))
