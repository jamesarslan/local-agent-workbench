"""JSON-lines RPC with the Electron main process over inherited pipes: requests, replies and job events."""

import importlib
import inspect
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import catalog
import account_book
from catalog import library_rows
import connections
import runtime
import project_profiles as projects
from paths import canonical
from provider_auth import connect_openrouter
from session_window import active_sessions

ROOT, DATA = runtime.ROOT, runtime.DATA
PROTOCOL = 2
POOL_SIZE = 8
JOB_LIMIT = 16
UI_KEYS = (
    "workspace",
    "image_route",
    "connection_id",
    "auth_provider",
    "harness",
    "provider",
    "remote_model",
    "endpoint",
    *projects.CAPS,
    "plugins",
)
ALIASES = {
    "state": "app-state",
    "status": "app-status",
    "accounts": "account-list",
    "models": "account-models",
    "oauth": "account-oauth",
    "native-login": "account-login",
    "serve": "server-start",
    "stop": "server-stop",
    "match": "server-match",
    "use-running": "server-adopt",
    "launch": "launch-start",
    "save": "launch-save",
    "visual-route": "launch-route",
    "plugins": "plugin-list",
    "benchmark-plugins": "plugin-benchmark",
    "measure": "plugin-measure",
    "import-plugins": "plugin-import",
}
ROUTES = (
    ("term-", "project_terminal"),
    ("sessions-", "sessions"),
    ("machine-", "machines"),
    ("studio-", "studio"),
    ("endpoint-", "endpoints"),
    ("source-", "connections"),
    ("folder-", "project_profiles"),
    ("project-setup-", "project_setup"),
    ("kit-", "starter_kits"),
    ("model-", "catalog"),
    ("onboarding-", "onboarding"),
    ("run-", "run_log"),
)
PLUGIN_AREA = frozenset(("plugin-describe", "plugin-remove", "plugin-update"))
INLINE = frozenset(
    (
        "app-state",
        "app-status",
        "hardware",
        "account-list",
        "account-save",
        "account-models",
        "account-oauth",
        "account-login",
        "credential",
        "store-key",
        "engine-info",
        "engine-install",
        "server-start",
        "server-stop",
        "server-match",
        "server-adopt",
        "launch-start",
        "launch-save",
        "launch-route",
        "plugin-list",
        "plugin-benchmark",
        "plugin-measure",
        "plugin-import",
        "project-list",
        "project-save",
        "project-preview",
        "project-apply",
        "project-default-set",
        "project-default-clear",
    )
)
INTERNAL = (
    "Agent Workbench hit an unexpected problem with this request. Try again; "
    "if it repeats, run Doctor in Advanced → Setup and check bridge.log in your data folder."
)
BUSY = "Too many tasks are running. Wait for one to finish, then try again."
RUNNING = "That task is already running. Wait for it to finish or cancel it first."
MISSING_AREA = "This feature is not included in this build of Agent Workbench. Update Agent Workbench, then try again."
OUT = None
OUT_LOCK = threading.Lock()
ACTIVE = {}
ACTIVE_LOCK = threading.Lock()
AREA_LOCK = threading.Lock()
guard = threading.Lock()


def settings():
    try:
        return runtime.settings()
    except (OSError, ValueError, TypeError):
        raise ValueError(
            f"The settings file {DATA / 'settings.json'} cannot be read. "
            "Repair or rename that file, then restart Agent Workbench."
        ) from None


def shipped():
    rows = getattr(runtime, "preset_rows", None)
    return rows() if rows else runtime.load(ROOT / "profiles.json", [])


def harness_cli():
    return dict(
        getattr(connections, "HARNESS_CLI", None)
        or {
            "OMP": "omp",
            "Pi": "pi",
            "Codex": "codex",
            "Claude Code": "claude",
            "Hermes": "hermes",
        }
    )


def choices(s):
    rows = library_rows(s["library"])
    paths = {Path(path).name: path for path, _, _ in rows}
    named = dict(s["named_profiles"])
    for item in shipped():
        if (
            item["file"] in paths
            and not Path(named.get(item["name"], {}).get("model", "")).is_file()
        ):
            named[item["name"]] = {
                **{k: v for k, v in item.items() if k not in ("file", "name")},
                "model": paths[item["file"]],
            }
    return named


def selection(s, payload, names):
    out = dict(s)
    name = payload.get("preset")
    if name:
        if not isinstance(name, str) or name not in names:
            raise ValueError(
                "This preset is unavailable. Refresh your library in Advanced."
            )
        out.update(
            {
                k: v
                for k, v in names[name].items()
                if k in runtime.DEFAULTS and k not in ("presets", "named_profiles")
            }
        )
    sent = {k: payload[k] for k in UI_KEYS if k in payload}
    for k, v in sent.items():
        if type(v) is not type(runtime.DEFAULTS[k]):
            raise ValueError(
                f"The launch setting {k} has a value Agent Workbench cannot use. "
                "Restart Agent Workbench, then choose it again."
            )
    out.update(sent)
    if (
        out["harness"] not in connections.HARNESS
        or out["provider"] not in connections.PROVIDERS
    ):
        raise ValueError("Choose a supported agent and provider.")
    out["workspace"] = str(canonical(out["workspace"]))
    return out


def launcher():
    try:
        prefs = runtime.load(DATA / "launcher.json", {})
    except (OSError, ValueError):
        raise ValueError(
            f"The file {DATA / 'launcher.json'} cannot be read; "
            "it is replaced the next time you launch an agent or save your settings."
        ) from None
    return prefs if isinstance(prefs, dict) else {}


def recent_folders(prefs):
    found = prefs.get("recent")
    return [p for p in found if isinstance(p, str)] if isinstance(found, list) else []


def guarded(label, fn, default, warnings):
    try:
        return fn()
    except Exception as exc:
        problem = describe(exc)
        if problem.get("code") == "internal":
            traceback.print_exc()
        warnings.append(f"{label} could not be loaded. {problem['message']}")
        return default


def pick(s, names, prefs):
    picked = prefs.get("preset", "")

    def matches(name):
        try:
            return runtime.same_server(
                s, {"spec": runtime.serving_spec(s | names[name])}
            )
        except (KeyError, ValueError, OSError):
            return False

    if "preset" not in prefs or (
        picked and (picked not in names or not matches(picked))
    ):
        preferred = [row["name"] for row in shipped()]
        ranked = [
            name for name in dict.fromkeys(preferred + list(names)) if name in names
        ]
        picked = next((name for name in ranked if matches(name)), "")
        if not s["model"] and not picked and ranked:
            picked = ranked[0]
    return picked


def home_folder():
    try:
        return str(canonical(Path.home()))
    except (RuntimeError, ValueError):
        return ""


def state():
    s = settings()
    warnings = []
    names = guarded("Model setups", lambda: choices(s), {}, warnings)
    prefs = guarded("Recent folders", launcher, {}, warnings)
    picked = guarded("The selected setup", lambda: pick(s, names, prefs), "", warnings)
    gpu, total, free, ram = guarded(
        "Hardware details", catalog.hardware, ("", 0.0, 0.0, 0.0), warnings
    )
    view = getattr(projects, "folder_view", None)
    return dict(
        settings={k: s[k] for k in UI_KEYS},
        presets={
            k: dict(
                model=Path(v.get("model", "")).name,
                context=v.get("context", s["context"]),
                engine=v.get("engine", "Auto"),
            )
            for k, v in names.items()
        },
        selected=picked,
        current=dict(
            model=Path(s["model"]).name, context=s["context"], engine=s["engine"]
        ),
        recent=[p for p in recent_folders(prefs) if Path(p).is_dir()],
        gpu=gpu,
        total=total,
        free=free,
        ram=ram,
        providers=list(connections.PROVIDERS),
        accounts=guarded("Connections", account_book.available, [], warnings),
        unified=guarded("Hardware details", catalog.unified_memory, False, warnings),
        windows=os.name == "nt",
        registered_plugins=guarded(
            "Registered plugins", lambda: list(projects.registry()), [], warnings
        ),
        cli_present={k: bool(shutil.which(v)) for k, v in harness_cli().items()},
        agents=connections.HARNESS,
        folder=guarded(
            "Folder settings",
            lambda: view(s["workspace"], strict=False),
            None,
            warnings,
        )
        if view
        else None,
        data=str(DATA),
        home=home_folder(),
        warnings=warnings,
    )


def starting(rec):
    try:
        return time.time() - rec.get("started", 0) < rec.get("budget", 0)
    except TypeError:
        return False


def status(payload):
    proc, rec = runtime.owned()
    label, model, spec = "Stopped", "", {}
    if proc:
        spec = rec["spec"]
        model = Path(spec["model"]).name or spec.get("vllm_model", "")
        try:
            label = (
                "Serving"
                if runtime.endpoint_model() == runtime.ALIAS
                else "Unexpected model"
            )
        except Exception:
            label = "Starting" if starting(rec) else "Starting or unavailable"
    elif socket_connect():
        label = "Port in use"
    sessions = active_sessions()
    # Another thread may still be importing the module, so the function can be missing for a moment.
    agents = getattr(sys.modules.get("project_terminal"), "active_agents", None)
    if agents:
        tracked = {s["pid"] for s in sessions}
        sessions.extend(s for s in agents() if s["pid"] not in tracked)
    return dict(
        label=label,
        model=model,
        context=spec.get("context"),
        port=runtime.PORT,
        spec=spec,
        sessions=sessions,
        owned=bool(proc),
        started=rec.get("started") if proc else None,
        budget=rec.get("budget") if proc else None,
    )


def socket_connect():
    try:
        with socket.create_connection(("127.0.0.1", runtime.PORT), timeout=0.25):
            return True
    except OSError:
        return False


def owner(action):
    if action in INLINE:
        return "desktop_bridge"
    if action in PLUGIN_AREA:
        return "project_profiles"
    for prefix, module in ROUTES:
        if action.startswith(prefix):
            return module
    raise ValueError("Unknown desktop action.")


def supply(fn, *args, progress=None, cancel=None):
    try:
        names = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        names = ()
    extra = {
        k: v for k, v in (("progress", progress), ("cancel", cancel)) if k in names
    }
    return fn(*args, **extra)


def remember(workspace, values):
    keep = getattr(projects, "remember", None)
    if not keep:
        return
    try:
        keep(workspace, values)
    except (OSError, ValueError):
        traceback.print_exc()


def bound(s):
    bind = getattr(connections, "bound", None)
    return bind(s) if bind else s


def login(payload):
    import tooling

    from host import command, terminal

    h = payload.get("harness")
    if h not in connections.HARNESS:
        raise ValueError("Choose a supported agent.")
    clean = getattr(connections, "clean_auth", None)
    provider = str(payload.get("auth_provider") or "")
    if clean:
        provider = clean(provider)
    if h == "Codex":
        terminal(command("codex") + ["login", "--device-auth"])
    elif h == "OMP":
        terminal(command("omp") + ["login", provider or "openai-codex-device"])
    elif h == "Hermes":
        terminal(
            command("hermes")
            + [
                "auth",
                "add",
                "openai-codex" if provider.startswith("openai-codex") else "anthropic",
                "--type",
                "oauth",
                "--no-browser",
            ]
        )
    elif h == "opencode":
        terminal(command("opencode") + ["auth", "login"])
    else:
        tooling.setup(h, "Login")
    return True


def manifest(payload):
    read = getattr(projects, "load_manifest", None)
    file = Path(payload["file"])
    return read(file) if read else runtime.load(file, {})


def area_dispatch(area):
    try:
        with AREA_LOCK:
            return importlib.import_module(area).dispatch
    except (ImportError, AttributeError):
        traceback.print_exc()
        raise ValueError(MISSING_AREA) from None


CLI_VERSIONS = {}
CLI_VERSION_TTL = 300


def installed_version(harness):
    """Version of the installed CLI for harness, cached briefly; '' when unknown."""
    cli = connections.HARNESS_CLI.get(harness) if isinstance(harness, str) else None
    if not cli:
        return ""
    now = time.monotonic()
    found = CLI_VERSIONS.get(cli)
    if found and now - found[0] < CLI_VERSION_TTL:
        return found[1]
    import tooling

    try:
        value = tooling.version(shutil.which(cli) or "")
    except (OSError, ValueError, subprocess.SubprocessError):
        value = ""
    CLI_VERSIONS[cli] = (now, value)
    return value


def note_run(harness, kind, facts):
    """Keep the run summary of a launch that has started; a failure here never fails the launch."""
    if not isinstance(facts, dict) or not facts.get("settings"):
        return ""
    try:
        import run_log

        return run_log.note(facts, harness, kind, installed_version(harness))
    except Exception:
        traceback.print_exc()
        return ""


def dispatch(action, payload, progress=None, cancel=None):
    action = ALIASES.get(action, action)
    payload = payload if isinstance(payload, dict) else {}
    area = owner(action)
    if area == "project_setup" and not payload.get("cli_version") and payload.get("harness"):
        payload = {**payload, "cli_version": installed_version(payload["harness"])}
    if area != "desktop_bridge":
        value = supply(
            area_dispatch(area), action, payload, progress=progress, cancel=cancel
        )
        if action == "term-open" and payload.get("kind") == "agent":
            remember(payload.get("workspace"), payload)
        return value
    if action == "launch-route":
        from visual_routes import route

        return route(payload)
    if action == "hardware":
        gpu, total, free, ram = catalog.hardware()
        return dict(
            gpu=gpu, total=total, free=free, ram=ram, unified=catalog.unified_memory()
        )
    if action == "account-list":
        return account_book.available()
    if action == "account-save":
        return account_book.save(payload)
    if action == "app-state":
        return state()
    if action == "app-status":
        return status(payload)
    if action == "account-oauth":
        connect_openrouter(payload or None)
        return True
    if action == "account-login":
        return login(payload)
    if action in ("store-key", "account-models", "credential"):
        s = bound(
            settings()
            | {
                k: payload[k]
                for k in ("provider", "endpoint", "connection_id")
                if k in payload
            }
        )
        if action == "store-key":
            token = payload.get("key")
            token = token.strip() if isinstance(token, str) else ""
            if not token:
                raise ValueError("Enter the provider API key.")
            connections.store_key(s, token)
            return True
        if action == "credential":
            try:
                return bool(connections.secret(s))
            except ValueError:
                return False
        return connections.discover(s)
    if action == "engine-info":
        import setup_engines

        return setup_engines.info(payload.get("backend"))
    if action == "engine-install":
        import setup_engines

        return supply(
            setup_engines.install,
            bool(payload.get("update")),
            payload.get("backend"),
            payload.get("strata"),
            progress=progress,
            cancel=cancel,
        )
    if action == "plugin-list":
        warnings = []
        return dict(
            names=guarded(
                "Registered plugins", lambda: list(projects.registry()), [], warnings
            ),
            measurement=runtime.load(
                DATA / "latest-tool-load.json",
                runtime.load(ROOT / "results/tool-load.json", {}),
            ),
            warnings=warnings,
        )
    if action == "plugin-benchmark":
        from tool_load import benchmark

        report = benchmark()
        runtime.write_json(DATA / "latest-tool-load.json", report)
        return True
    if action == "plugin-measure":
        from plugin_cost import measure

        s = settings()
        return measure(selection(s, payload, choices(s)))
    if action == "plugin-import":
        return projects.register(manifest(payload))
    if action == "project-list":
        # The plain name list is the 0.3 shape; detail adds the folder default.
        if payload.get("detail") is True:
            return projects.project_list(payload["workspace"])
        return projects.project_names(payload["workspace"])
    if action == "project-default-set":
        return projects.set_default(payload["workspace"], payload["name"])
    if action == "project-default-clear":
        return projects.clear_default(payload["workspace"])
    s = settings()
    if action == "server-start":
        chosen = (
            payload.get("preset") if payload.get("provider") == "Managed local" else ""
        )
        local = (
            s
            | (choices(s).get(chosen, {}) if chosen else {})
            | {"provider": "Managed local"}
        )
        supply(runtime.start, local, progress=progress, cancel=cancel)
        return True
    if action == "server-stop":
        supply(runtime.stop, progress=progress)
        return True
    names = choices(s)
    out = selection(s, payload, names)
    if action == "project-save":
        return projects.save_project(
            out, payload["name"], default=payload.get("default") is True
        )
    if action in ("project-preview", "project-apply"):
        weights = (
            [p for p, _, _ in catalog.discover(s["library"])]
            if Path(s["library"]).is_dir()
            else []
        )
        if action == "project-preview":
            return projects.preview_project(out, payload["name"], weights)
        if not payload.get("confirm"):
            raise ValueError("Review this profile before you apply it.")
        out = projects.apply_project(out, payload["name"], weights, payload["confirm"])
        payload = {**payload, "preset": ""}
    proc, rec = runtime.owned()
    if action == "server-match":
        return bool(proc and runtime.same_server(out, rec))
    if action == "server-adopt":
        if not proc:
            raise ValueError("The server has stopped.")
        out.update({k: v for k, v in rec["spec"].items() if k in out})
        out["provider"] = "Managed local"
        payload = {**payload, "preset": ""}
    if (
        action == "launch-start"
        and out["provider"] == "Managed local"
        and proc
        and not runtime.same_server(out, rec)
    ):
        raise ValueError(
            "Another setup is serving. Choose Use running model, or stop the server to switch."
        )
    if action == "launch-start":
        cli = harness_cli()[out["harness"]]
        if not shutil.which(cli):
            raise ValueError(
                f"{out['harness']} is missing. Open Advanced → Setup to install it."
            )
        facts = {}
        connections.launch(out, facts=facts)
        note_run(out["harness"], "window", facts)
    if action in ("launch-start", "launch-save"):
        remember(out["workspace"], {**out, "preset": payload.get("preset", "")})
    if action == "launch-start":
        for name in (*projects.CAPS, "plugins"):
            out[name] = s[name]

    with guard:
        out["named_profiles"] = names
        runtime.write_json(DATA / "settings.json", out)
        try:
            prefs = launcher()
        except ValueError:
            prefs = {}
        recent = list(dict.fromkeys([out["workspace"], *recent_folders(prefs)]))[:8]
        runtime.write_json(
            DATA / "launcher.json",
            dict(preset=payload.get("preset", ""), recent=recent),
        )
    return True


def write(line):
    if OUT is None:
        return
    with OUT_LOCK:
        try:
            OUT.write(line + "\n")
            OUT.flush()
        except (OSError, ValueError):
            pass


def send(message):
    if OUT is None:
        return
    try:
        # Node's JSON.parse refuses NaN and Infinity, so they must not reach the wire.
        write(json.dumps(message, allow_nan=False))
    except (TypeError, ValueError, RecursionError):
        pass


def emit(event, data):
    send({"event": event, "data": data})


def reporter(handle, action):
    last = {"at": 0.0, "phase": None}

    def report(info):
        if not isinstance(info, dict):
            return
        now = time.monotonic()
        if info.get("phase") == last["phase"] and now - last["at"] < 0.1:
            return
        last.update(at=now, phase=info.get("phase"))
        emit("job-progress", {"handle": handle, "action": action, "progress": info})

    return report


def first_line(text, limit=300):
    return next((x.strip() for x in str(text).splitlines() if x.strip()), "")[:limit]


def program(cmd):
    if isinstance(cmd, (list, tuple)):
        head = cmd[0] if cmd else ""
    else:
        head = str(cmd).split(" ")[0]
    return Path(str(head)).name or "A helper program"


def describe(exc):
    kind = type(exc).__name__
    origin = type(exc).__module__ or ""
    text = str(exc).strip()
    if kind in ("Cancelled", "DownloadCancelled"):
        return {"message": first_line(text) or "Cancelled.", "code": "cancelled"}
    if isinstance(exc, subprocess.TimeoutExpired):
        return {
            "message": f"{program(exc.cmd)} did not finish within {int(exc.timeout)} seconds. "
            "Check that it is installed and responding, then try again.",
            "detail": text[-4000:],
            "action": "retry",
        }
    if isinstance(exc, subprocess.CalledProcessError):
        how = (
            f"was stopped by signal {-exc.returncode}"
            if exc.returncode < 0
            else f"failed with exit code {exc.returncode}"
        )
        tail = exc.stderr if isinstance(exc.stderr, str) else ""
        return {
            "message": f"{program(exc.cmd)} {how}. Run Doctor in Advanced → Setup to find the cause, then try again.",
            "detail": (text + "\n" + tail).strip()[-4000:],
            "action": "doctor",
        }
    if origin.startswith("requests"):
        return {
            "message": "The network request did not complete. Check your connection, then try again.",
            "detail": text[-4000:],
            "action": "retry",
        }
    if origin.startswith("filelock"):
        return {
            "message": "Another Agent Workbench task is using this data right now. Wait a few seconds, then try again.",
            "code": "busy",
            "action": "retry",
        }
    if isinstance(exc, (ValueError, RuntimeError)) and not isinstance(
        exc, (UnicodeError, RecursionError, NotImplementedError, json.JSONDecodeError)
    ):
        if not text:
            return {"message": INTERNAL, "detail": kind, "code": "internal", "action": "log"}
        out = {"message": first_line(text)}
        tail = getattr(exc, "tail", None)
        if isinstance(tail, (list, tuple)) and tail:
            out.update(detail="\n".join(str(x) for x in tail)[-4000:], action="log")
        elif text != out["message"]:
            out["detail"] = text[-4000:]
        if kind == "EngineMissing":
            out["action"] = "setup"
        return out
    if isinstance(exc, TimeoutError):
        return {
            "message": "The operation timed out. Try again.",
            "detail": text or kind,
            "action": "retry",
        }
    if isinstance(exc, OSError) and exc.filename:
        what = (
            "was not found"
            if isinstance(exc, FileNotFoundError)
            else "could not be used" + (f" ({exc.strerror})" if exc.strerror else "")
        )
        return {
            "message": f"{exc.filename} {what}. Check that it exists and that you may use it, then try again.",
            "detail": f"{kind}: {text}"[-4000:],
        }
    return {
        "message": INTERNAL,
        "detail": "".join(traceback.format_exception(exc))[-4000:],
        "code": "internal",
        "action": "log",
    }


def encode(ident, value):
    try:
        return json.dumps({"id": ident, "value": value}, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise TypeError(f"The reply cannot be written as JSON: {exc}") from None


def answer(req, token=None):
    ident, action = req.get("id"), str(req.get("action", ""))
    action = ALIASES.get(action, action)
    handle = str(req.get("handle") or f"r{ident}")
    try:
        line = encode(
            ident, dispatch(action, req.get("data"), reporter(handle, action), token)
        )
    except BaseException as exc:
        # SystemExit or a cancelled event loop would end this thread silently; the request must still be answered.
        try:
            problem = describe(exc)
        except Exception:
            problem = {"message": INTERNAL, "code": "internal", "action": "log"}
        if problem.get("code") == "internal":
            traceback.print_exc()
        line = json.dumps({"id": ident, "error": problem})
    finally:
        if token is not None:
            with ACTIVE_LOCK:
                if ACTIVE.get(handle) is token:
                    del ACTIVE[handle]
    write(line)


def cancel_job(handle):
    with ACTIVE_LOCK:
        token = ACTIVE.get(str(handle))
    if token is None:
        return False
    token.set()
    return True


def accept(text, pool):
    try:
        req = json.loads(text)
    except (ValueError, RecursionError):
        req = None
    if (
        not isinstance(req, dict)
        or "id" not in req
        or not isinstance(req.get("data") or {}, dict)
    ):
        print("desktop_bridge: ignored a malformed request line", file=sys.stderr, flush=True)
        return
    if req.get("action") == "job-cancel":
        data = req.get("data") if isinstance(req.get("data"), dict) else {}
        send({"id": req["id"], "value": cancel_job(data.get("handle", ""))})
        return
    if req.get("lane") != "job":
        pool.submit(answer, req)
        return
    handle = str(req.get("handle") or f"r{req['id']}")
    token = threading.Event()
    with ACTIVE_LOCK:
        refused = (
            RUNNING if handle in ACTIVE else BUSY if len(ACTIVE) >= JOB_LIMIT else ""
        )
        if not refused:
            ACTIVE[handle] = token
    if refused:
        send({"id": req["id"], "error": {"message": refused, "code": "busy"}})
        return
    threading.Thread(
        target=answer, args=(req, token), daemon=True, name="job-" + str(req.get("action"))
    ).start()


def isolate_stdin():
    reader = os.fdopen(
        os.dup(sys.stdin.fileno()), "r", encoding="utf-8", errors="replace"
    )
    null = os.open(os.devnull, os.O_RDONLY)
    try:
        os.dup2(null, sys.stdin.fileno())
    finally:
        os.close(null)
    return reader


def shutdown(code=0):
    close = getattr(sys.modules.get("project_terminal"), "close_sessions", None)
    if close:
        try:
            close()
        except Exception:
            traceback.print_exc()
    for stream in (OUT, sys.stderr):
        try:
            stream.flush()
        except (AttributeError, OSError, ValueError):
            pass
    os._exit(code)


def main():
    global OUT
    # Child engines and setup programs can write to stdout or read stdin; keep both off the RPC channel.
    OUT = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    requests_in = isolate_stdin()
    try:
        import tooling

        getattr(tooling, "refresh_path", lambda: None)()
    except Exception:
        traceback.print_exc()
    emit("bridge-ready", {"protocol": PROTOCOL, "pid": os.getpid()})
    pool = ThreadPoolExecutor(max_workers=POOL_SIZE, thread_name_prefix="rpc")
    code = 0
    try:
        for text in requests_in:
            accept(text, pool)
    except Exception:
        traceback.print_exc()
        code = 1
    shutdown(code)


if __name__ == "__main__":
    main()
