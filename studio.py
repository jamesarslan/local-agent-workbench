"""Electron controls for local setup, inference tuning, downloads and trace inspection."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time

import catalog
import connections
import host
import runtime
import tooling

TOOLS = {}
SCAN = threading.Lock()
SCANNED = 0.0  # time.monotonic() of the last full scan
STALE = False  # an install is finishing outside Workbench (terminal or browser)
SETUP = threading.Lock()  # one Install/Update at a time
HUB = {}
TRACE = {}
TUNING = (
    "model",
    "library",
    "engine",
    "context",
    "kv",
    "draft",
    "vision",
    "lora",
    "thinking",
    "prompt",
    "channel",
    "start_timeout",
    "engine_args",
    "engine_manifest",
    "extra_mcp",
    "vllm_model",
    "vllm_fraction",
    "vllm_parser",
    "box",
    "blender",
    "desktop",
    "document_skill",
)
MODES = tuple(
    getattr(
        runtime,
        "ENGINES",
        ("Auto", "Strata", "llama.cpp", "Flash preset", "vLLM", "Custom"),
    )
)
TRACEBACK = "Traceback (most recent call last):"
SERVING = frozenset(
    (
        "engine",
        "context",
        "kv",
        "channel",
        "start_timeout",
        "engine_args",
        "vllm_model",
        "vllm_fraction",
        "vllm_parser",
        "engine_manifest",
    )
)


def settings():
    s = runtime.settings()
    return {k: s.get(k, "") for k in TUNING}


def engines(platform=None):
    """Engines offered in the picker. The runtime decides when it can; the fallback only knows macOS."""
    listed = getattr(runtime, "available_engines", None)
    if listed and platform is None:
        return list(listed())
    if (platform or sys.platform) == "darwin":
        return ["Auto", "llama.cpp", "Custom"]
    return list(MODES)


def probe(name):
    try:
        return tooling.location(name) or ""
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""


def checks(force=False):
    global SCANNED, STALE
    with SCAN:
        if not TOOLS or force or STALE or time.monotonic() - SCANNED > 300:
            tooling.refresh_path()
            names = list(tooling.TOOLS)
            with ThreadPoolExecutor(max_workers=6) as pool:
                found = list(pool.map(probe, names))
            TOOLS.clear()
            TOOLS.update(zip(names, found))
            SCANNED, STALE = time.monotonic(), False
        return dict(TOOLS)


def setup_tool(name, verb, progress=None, cancel=None):
    global STALE
    if (
        not isinstance(name, str)
        or name not in tooling.TOOLS
        or verb not in tooling.ACTIONS
    ):
        raise ValueError("Unknown setup action.")
    exclusive = verb in ("Install", "Update")
    if exclusive and not SETUP.acquire(blocking=False):
        raise ValueError(
            "Another setup job is running; wait for it to finish, then retry."
        )
    try:
        result = tooling.run(name, verb, progress, cancel)
    finally:
        if exclusive:
            SETUP.release()
    checks()
    with SCAN:
        TOOLS[name] = probe(name)
        if exclusive and result["outcome"] in ("started-terminal", "opened-docs"):
            STALE = True
        result["checks"] = dict(TOOLS)
    return result


def reason(stderr):
    """The last line of a child's stderr without its exception class, or "".
    A traceback that only reports a failed grandchild is dropped so that child's own last line is used."""
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    while lines and "CalledProcessError: " in lines[-1]:
        begun = lines[::-1].index(TRACEBACK) if TRACEBACK in lines else 0
        del lines[len(lines) - 1 - begun :]
    return re.sub(r"^[\w.]+(Error|Exception): ", "", lines[-1]) if lines else ""


def validate(s, changed=None):
    changed = set(s) if changed is None else set(changed)
    library, model = s["library"], s["model"]
    if "library" in changed and library != runtime.DEFAULTS["library"]:
        if (
            not isinstance(library, str)
            or not library.strip()
            or not Path(library).is_dir()
        ):
            raise ValueError("Choose an existing library folder.")
    if "model" in changed and model:
        if not isinstance(model, str) or not Path(model).is_file():
            raise ValueError("Selected weights were moved. Rescan the model folder.")
    if changed & SERVING:
        check = getattr(runtime, "validate_serving", None)
        if check:
            check(s)
        else:
            try:
                ok = 1024 <= int(s["context"]) <= 1048576
            except (TypeError, ValueError):
                ok = False
            if not ok:
                raise ValueError("Choose a context between 1,024 and 1,048,576.")
            if s["engine"] not in MODES:
                raise ValueError("Choose one of the listed inference engines.")
        s["context"] = int(s["context"])
    return s


def target(s):
    """The model a trace will talk to, as the header row names it."""
    if s["provider"] == "Managed local":
        return Path(s.get("model") or "").name or "agent default"
    return s.get("remote_model") or "agent default"


def said(message):
    """The text an assistant message carries, or "" when it has none."""
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return ""
    content = message.get("content")
    return "".join(
        x["text"]
        for x in (content if isinstance(content, list) else [])
        if isinstance(x, dict)
        and x.get("type") == "text"
        and isinstance(x.get("text"), str)
    )


def trace_run(s, task, edit):
    if not task.strip():
        raise ValueError("Enter a task for OMP.")
    argv, env, cwd = connections.prepare(s, "OMP")
    argv = host.safe_argv(
        argv
        + [
            "--mode",
            "json",
            "--no-session",
            "--no-extensions",
            "--max-time",
            "300",
            "--tools",
            "read,glob,grep" + (",edit,write" if edit else ""),
            "-p",
            task,
        ]
    )
    folder = runtime.DATA / "traces"
    folder.mkdir(exist_ok=True)
    ident = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    dest = folder / (ident + ".jsonl")
    err_path = dest.with_suffix(".stderr")
    dest.touch()
    try:
        with err_path.open("w", encoding="utf8") as err:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=err,
                text=True,
                encoding="utf8",
                errors="replace",
                creationflags=runtime.QUIET,
            )
    except OSError as exc:
        dest.unlink(missing_ok=True)
        err_path.unlink(missing_ok=True)
        raise ValueError(
            f"The trace could not be started ({exc.strerror or exc}); "
            "reinstall OMP in Advanced > Setup, then run the trace again."
        ) from None
    TRACE[ident] = proc
    where = (
        f"{cwd} · {s['provider']} · {target(s)} · "
        f"{'edits allowed' if edit else 'read-only'}"
    )

    def read():
        t = time.monotonic()

        def row(kind, info):
            return (
                json.dumps(
                    dict(
                        elapsed_s=round(time.monotonic() - t, 2),
                        event=kind,
                        details=info,
                    )
                )
                + "\n"
            )

        try:
            with dest.open("w", encoding="utf8") as log, proc.stdout:
                log.write(
                    json.dumps(dict(elapsed_s=0, event="target", details=where))
                    + "\n"
                )
                log.flush()
                for line in proc.stdout:
                    try:
                        event = json.loads(line)
                    except (ValueError, RecursionError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    kind = event.get("type", "")
                    if kind == "tool_execution_start":
                        info = (
                            str(event.get("toolName", ""))
                            + " "
                            + json.dumps(event.get("args", {}), ensure_ascii=False)
                        )
                    elif kind == "tool_execution_end":
                        info = str(event.get("toolName", "")) + (
                            " failed" if event.get("isError") else " complete"
                        )
                    elif kind == "message_end":
                        info = said(event.get("message"))
                        if not info:
                            continue
                    elif kind in ("agent_start", "agent_end"):
                        info = s["provider"]
                    else:
                        continue
                    log.write(row(kind, info))
                    log.flush()
                code = proc.wait()
                log.write(
                    row(
                        "exit",
                        str(code)
                        if code == 0
                        else f"{code} · see {err_path}",
                    )
                )
        finally:
            TRACE.pop(ident, None)
            if proc.poll() is None:
                proc.terminate()

    threading.Thread(target=read, daemon=True).start()
    return ident


def dispatch(action, p, progress=None, cancel=None):
    from catalog import library_rows

    s = runtime.settings()
    if action.startswith("studio-profile-"):
        import project_profiles as pp

        if action == "studio-profile-edit":
            return pp.edit_profile(
                p["workspace"], p["name"], p["op"], p.get("new_name", "")
            )
        item = pp.load_profiles(p["workspace"]).get(p["name"])
        if item is None:
            raise ValueError("Profile no longer exists.")
        plugins = pp.plugin_ids(item) if isinstance(item.get("plugins"), str) else []
        return {
            "Model": item.get("remote_model") or item.get("weight") or "Agent default",
            "Agent": item.get("harness"),
            "Context": item.get("context"),
            "Tools": ", ".join([k for k in pp.CAPS if item.get(k)] + plugins) or "None",
        }
    if action == "studio-evidence":
        return runtime.load(runtime.ROOT / "results/harbor-terminal-bench-21.json", [])
    if action == "studio-info":
        return dict(
            config=settings(),
            engines=engines(),
            checks=checks(),
            trace_names=sorted(
                x.stem for x in (runtime.DATA / "traces").glob("*.jsonl")
            )[-50:][::-1],
        )
    if action == "studio-setup-log":
        from host import open_folder

        log = runtime.DATA / "setup.log"
        if not log.is_file():
            raise ValueError("Install or update a tool to create a setup log.")
        try:
            open_folder(log)
        except OSError:
            raise ValueError(
                f"The setup log could not be opened here; open {log} in a text editor."
            ) from None
        return True
    if action == "studio-check":
        return checks(True)
    if action == "studio-setup":
        return setup_tool(p["name"], p["verb"], progress, cancel)
    if action == "studio-tools":
        result = setup_tool(p["name"], p["verb"], progress, cancel)
        if result["outcome"] == "failed":
            raise ValueError(result["message"])
        return result["checks"]
    if action == "studio-doctor":
        import doctor

        rows = doctor.run(p.get("ids"), bool(p.get("active")))
        return dict(checks=rows, counts=doctor.counts(rows))
    if action == "studio-import":
        try:
            f = Path(p["path"]).resolve(strict=True)
        except (OSError, RuntimeError, TypeError, ValueError):
            raise ValueError(
                "That file or folder no longer exists; drop it again."
            ) from None
        single = f.is_file()
        if single and not f.name.lower().endswith((".strata", ".gguf", ".gguf_file")):
            raise ValueError("Drop a model folder or a .gguf or .strata weight file.")
        folder, count = (f.parent, 1) if single else (f, 0)
        home = Path.home()
        if folder in (home, home.resolve(), Path(folder.anchor)):
            raise ValueError(
                "Move that weight file into a folder of its own, then drop it again; "
                "your home folder or a whole drive cannot be the model library."
                if single
                else "Drop the folder that holds your model files, not your home folder or a whole drive."
            )
        if not single:
            try:
                count = len(catalog.discover(f))
            except OSError:
                raise ValueError(
                    "That folder could not be read; check its permissions, then drop it again."
                ) from None
            if not count:
                raise ValueError(
                    "No model weights were found in that folder; drop a folder that contains .gguf or .strata files."
                )
        previous = s["library"]
        s["library"] = str(folder)
        if single:
            s["model"] = str(f)
        runtime.write_json(runtime.DATA / "settings.json", s)
        library_rows.cache_clear()
        return dict(
            library=str(folder),
            previous=previous,
            model=str(f) if single else "",
            models=count,
            changed=str(folder) != previous,
        )
    if action == "studio-library":
        library_rows.cache_clear()
        return [
            dict(path=path, gib=round(size / 1024**3, 2))
            for path, _, size in library_rows(p["library"])
        ]
    if action == "studio-save":
        sent = {k: p[k] for k in TUNING if k in p}
        s = validate(s | sent, sent)
        if name := p.get("name", "").strip():
            s["named_profiles"][name] = {
                k: v for k, v in s.items() if k not in ("named_profiles", "presets")
            }
        runtime.write_json(runtime.DATA / "settings.json", s)
        return True
    if action == "studio-shortcut":
        import shortcuts

        if p["name"] not in s["named_profiles"]:
            raise ValueError("Save a named setup first.")
        path = shortcuts.create(p["name"])
        try:
            from host import open_folder

            open_folder(path.parent)
        except OSError:
            pass
        return str(path)
    if action == "studio-hub":
        query = p["query"].strip()
        if not query:
            raise ValueError("Enter a model name or repository.")
        rows = catalog.hub_files(query)
        output = []
        for row in rows:
            if isinstance(row, str):
                output.append(dict(repo=row))
            else:
                ident = secrets.token_hex(8)
                HUB[ident] = row
                output.append(
                    dict(
                        id=ident,
                        name=row[2],
                        gib=round(row[4] / 1024**3, 2),
                        bytes=row[4],
                    )
                )
        return output
    if action == "studio-download":
        row = HUB.get(p["id"])
        if not row:
            raise ValueError("Search again to resolve the download revision.")
        file = catalog.download(row, s["library"], p.get("vision", False))
        library_rows.cache_clear()
        return str(file)
    if action == "studio-bench":
        try:
            r = subprocess.run(
                [sys.executable, str(runtime.ROOT / "cli.py"), "bench"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf8",
                errors="replace",
                timeout=3600,
                creationflags=runtime.QUIET,
            )
        except subprocess.TimeoutExpired:
            raise ValueError(
                "The benchmark did not finish within 60 minutes; stop the model, then run it again."
            ) from None
        if r.returncode:
            raise ValueError(
                reason(r.stderr)
                or "The benchmark failed; start the model from Launch, then run it again."
            )
        return str(
            max(runtime.DATA.glob("benchmark-*.json"), key=lambda f: f.stat().st_mtime)
        )
    if action == "studio-results":
        return sorted(x.name for x in runtime.DATA.glob("benchmark-*.json"))[::-1]
    if action == "studio-result":
        f = runtime.DATA / Path(p["name"]).name
        if not f.name.startswith("benchmark-") or f.suffix != ".json":
            raise ValueError("Choose a benchmark report.")
        return runtime.load(f, {})
    if action == "studio-web":
        from cli import web

        try:
            web(bool(p["start"]))
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise ValueError(
                tooling.docker_problem()
                or "Docker could not start the page-crawling stack; run Doctor to see why."
            ) from None
        return True
    if action == "studio-box":
        import boxes

        if p["start"]:
            boxes.agent(p["name"], s)
        else:
            boxes.stop_box(p["name"])
        return True
    if action == "studio-trace":
        form = p.get("form")
        if form is not None:
            if not isinstance(form, dict):
                raise ValueError("Choose a project folder, then run the trace again.")
            from desktop_bridge import choices, selection

            s = selection(s, form, choices(s))
        return trace_run(s, p["task"], bool(p.get("edit")))
    if action in ("studio-trace-read", "studio-trace-stop"):
        ident = p["id"]
        if Path(ident).name != ident or "/" in ident or "\\" in ident:
            raise ValueError("Invalid trace.")
        if action.endswith("stop"):
            if proc := TRACE.get(ident):
                proc.terminate()
            return True
        running = ident in TRACE
        path = runtime.DATA / "traces" / (ident + ".jsonl")
        with path.open("rb") as f:
            f.seek(max(0, int(p.get("offset", 0))))
            rows = []
            for _ in range(100):
                pos = f.tell()
                line = f.readline()
                if not line.endswith(b"\n"):
                    f.seek(pos)
                    break
                rows.append(json.loads(line))
            offset = f.tell()
        return dict(rows=rows, offset=offset, running=running)
    if action == "studio-api-key":
        return runtime.key()
    if action == "studio-subscriptions":
        from account_book import existing_codex

        if not shutil.which("codex"):
            return "Codex CLI is not installed."
        existing_codex.cache_clear()
        return (
            "ChatGPT account available"
            if existing_codex()
            else "Use device sign-in to connect a ChatGPT account."
        )
    raise ValueError("Unknown advanced action.")
