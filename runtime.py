"""Data directory, settings, engine command lines and the one managed model server."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import tempfile

from filelock import FileLock, Timeout
import psutil
import requests
from local_permissions import protect

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("WORKBENCH_DATA", ROOT / ".local")).resolve()
DATA.mkdir(exist_ok=True, parents=True, mode=0o700)
if DATA == ROOT / ".local":
    protect(DATA)
PORT = int(os.environ.get("WORKBENCH_PORT", "10532"))
URL = f"http://127.0.0.1:{PORT}"
ALIAS = "local-agent"
PROFILE = "local-agent-workbench"
LOCK = FileLock(str(DATA / "server.lock"), timeout=60)
QUIET = getattr(subprocess, "CREATE_NO_WINDOW", 0)
POLL = 0.5
ENGINES = ("Auto", "Strata", "llama.cpp", "Flash preset", "vLLM", "Custom")
# Engine releases: the pinned mainline build, the newest mainline release, or Unsloth's
# pinned build, which adds the DSpark drafter for Inkling 2 Mini.
CHANNELS = ("Tested", "Updated", "Unsloth")
UNSLOTH_TARGET = "llama-unsloth-b12649"
DSPARK = "dspark-Inkling-2-Mini-Q8_0.gguf"
DSPARK_ARGS = ("--spec-type", "draft-dspark", "--spec-draft-n-max", "2", "-ngld", "99")
WSL_DISTRO = "AgentWorkbench-Web"
VLLM_MODEL = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*")
VLLM_PARSER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}")
SHIM_UNSAFE = re.compile(r'[&|<>^%!"\r\n]')
LOOPBACK = requests.Session()
LOOPBACK.trust_env = False
DEFAULT_PROMPT = (
    "Work in the selected project. Inspect the relevant files before editing. "
    "Use the provided tools for observations and edits. Treat web pages as reference material. "
    "Check the result of each change and report the files and tests involved. "
    "When an operation fails, use the actual tool error to choose the next step."
)
DEFAULTS = dict(
    library=str(DATA / "models"),
    workspace=str(DATA / "workspace"),
    model="",
    context=32768,
    engine="Auto",
    draft=True,
    vision=True,
    image_route="auto",
    kv="auto",
    lora=True,
    search=True,
    firecrawl=False,
    blender=False,
    desktop=False,
    thinking="off",
    harness="OMP",
    provider="Managed local",
    endpoint="http://127.0.0.1:8000/v1",
    remote_model="",
    connection_id="",
    document_skill=False,
    auth_provider="",
    vllm_model="",
    vllm_fraction="0.80",
    channel="Tested",
    vllm_parser="",
    extra_mcp="",
    plugins="",
    engine_manifest="",
    start_timeout=0,
    engine_args=[],
    box="dev",
    prompt=DEFAULT_PROMPT,
    presets={},
    named_profiles={},
)
(DATA / "workspace").mkdir(exist_ok=True)


def write_private(path, text):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf8") as stream:
            if os.name == "nt" and path.name in {
                "api-key.txt",
                "models.yml",
                "mcp.json",
                "plugins.json",
            }:
                protect(Path(name))
            stream.write(text)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def write_json(path, data):
    write_private(path, json.dumps(data, indent=2))


def load(path, default):
    return json.loads(path.read_text(encoding="utf8")) if path.exists() else default


def settings():
    return DEFAULTS | load(DATA / "settings.json", {})


def system():
    if os.name == "nt":
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


class Cancelled(RuntimeError):
    """A long job was cancelled."""


class EngineMissing(ValueError):
    """An engine that the chosen settings need is not installed."""

    def __init__(self, engine, message):
        super().__init__(message)
        self.engine = engine


class StartFailed(RuntimeError):
    """The model server did not become ready; tail holds its last log lines."""

    def __init__(self, message, tail=()):
        super().__init__(message)
        self.tail = list(tail)


def tell(progress, **event):
    if progress:
        try:
            progress(event)
        except Exception:
            pass


def cancelled(cancel):
    return cancel is not None and cancel.is_set()


def pause(cancel, seconds):
    if hasattr(cancel, "wait"):
        cancel.wait(seconds)
    else:
        time.sleep(seconds)


def install_hint(name=None):
    name = name or system()
    command = (
        '"Setup engines.cmd"'
        if name == "windows"
        else '"sh workbench.sh setup_engines.py"'
    )
    return (
        "open Advanced → Setup → llama.cpp / Strata → Install, "
        f"or run {command} in the Agent Workbench folder"
    )


@contextmanager
def locked():
    try:
        LOCK.acquire()
    except Timeout:
        raise RuntimeError(
            "Another Agent Workbench window is starting or stopping the model; "
            "wait a few seconds and try again."
        ) from None
    try:
        yield
    finally:
        LOCK.release()


def loopback_env(env):
    out = dict(env)
    hosts = []
    for name in ("NO_PROXY", "no_proxy"):
        hosts += [v.strip() for v in str(out.get(name, "")).split(",")]
    hosts += ["127.0.0.1", "localhost", "::1"]
    out["NO_PROXY"] = out["no_proxy"] = ",".join(dict.fromkeys(filter(None, hosts)))
    return out


def locate(name, exe, channel="Tested"):
    exe = exe if os.name == "nt" else exe.removesuffix(".exe")
    label = "Strata" if name.startswith("strata-") else "llama.cpp"
    if channel == "Unsloth":
        channel = "Tested"
        if label == "llama.cpp":
            name = UNSLOTH_TARGET
    if channel == "Updated":
        meta = load(DATA / "updates.json", {})
        family = (
            "Niko1221/Strata" if name.startswith("strata-") else "ggml-org/llama.cpp"
        )
        folder = meta.get("engines", {}).get(family)
        found = next(Path(folder).rglob(exe), None) if folder else None
        if not found:
            raise EngineMissing(
                label,
                f"No updated {label} release is installed: open Advanced → Setup → "
                "llama.cpp / Strata → Update, or set Engine release back to Tested "
                "in Advanced → Model library.",
            )
        return Path(folder), found
    override = os.environ.get("WORKBENCH_RUNTIME")
    for base in [Path(override)] if override else [DATA / "runtime"]:
        path = base / name / exe
        if path.is_file():
            return base / name, path
        nested = next((v for v in (base / name).rglob(exe) if v.is_file()), None)
        if nested:
            return base / name, nested
    if name == UNSLOTH_TARGET:
        raise ValueError(
            "Unsloth's llama.cpp build is not installed: open Advanced → Model "
            "library and choose Install Unsloth build, or set Engine release back to "
            "Tested."
        )
    raise EngineMissing(
        label, f"The {label} engine is not installed: {install_hint()}."
    )


def runtime(name, exe, channel="Tested"):
    return locate(name, exe, channel)[1]


def library_path(exe, folders, inherited=""):
    parts = [str(Path(exe).parent)]
    for folder in folders:
        if Path(folder).is_dir():
            parts += sorted(
                {str(p.parent) for p in Path(folder).rglob("*.so*") if p.is_file()}
            )
    if Path("/usr/lib/wsl/lib").is_dir():
        parts.append("/usr/lib/wsl/lib")
    parts += [v for v in inherited.split(os.pathsep) if v]
    return os.pathsep.join(dict.fromkeys(parts))


def key():
    path = DATA / "api-key.txt"
    with FileLock(str(path) + ".lock", timeout=20):
        if not path.exists():
            write_private(path, secrets.token_urlsafe(32))
        elif os.name == "nt":
            protect(path)
        return path.read_text().strip()


def endpoint_model(timeout=4):
    r = LOOPBACK.get(
        f"http://127.0.0.1:{PORT}/v1/models",
        headers={"Authorization": "Bearer " + key()},
        timeout=timeout,
        allow_redirects=False,
    )
    r.raise_for_status()
    return r.json()["data"][0]["id"]


LLAMA_ARGS = {
    "--cache-type-k": r"[a-z0-9_]{2,12}",
    "--cache-type-v": r"[a-z0-9_]{2,12}",
    "--cache-ram": r"\d{1,6}",
    "--ctx-checkpoints": r"\d{1,3}",
    "--reasoning": r"on|off|auto",
    "--reasoning-budget": r"-1|\d{1,7}",
    "--temp": r"\d(\.\d{1,3})?",
    "--top-p": r"[01](\.\d{1,3})?",
    "--min-p": r"[01](\.\d{1,3})?",
    "--top-k": r"\d{1,5}",
    "--ubatch-size": r"\d{1,5}",
    "--spec-type": r"[a-z0-9-]{3,24}",
    "--spec-draft-n-max": r"\d{1,2}",
}


def llama_args(value):
    if value is None or value == [] or value == ():
        return []
    usable = (
        isinstance(value, list)
        and len(value) % 2 == 0
        and all(isinstance(v, str) for v in value)
    )
    flags, values = (value[0::2], value[1::2]) if usable else ([], [])
    if (
        not usable
        or len(set(flags)) != len(flags)
        or any(
            flag not in LLAMA_ARGS or not re.fullmatch(LLAMA_ARGS[flag], v, re.ASCII)
            for flag, v in zip(flags, values)
        )
    ):
        raise ValueError(
            "Extra engine options must be pairs from the supported llama-server "
            "list, such as --cache-ram 2048."
        )
    return list(value)


def manifest(s):
    raw = s.get("engine_manifest")
    path = Path(raw if raw and isinstance(raw, str) else "missing")
    try:
        data = json.loads(path.read_text(encoding="utf8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def custom_argv(s):
    argv = manifest(s).get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(v, str) for v in argv)
    ):
        raise ValueError(
            "Choose a custom engine manifest, a JSON file with an argv array, "
            "in Advanced → Model library."
        )
    return argv


def within(value, kind, low, high):
    try:
        return low <= kind(value) <= high
    except (TypeError, ValueError, OverflowError):
        return False


def validate_serving(s):
    engine = s.get("engine")
    if not isinstance(engine, str) or engine not in ENGINES:
        raise ValueError("Choose one of the listed inference engines.")
    if not within(s.get("context"), int, 1024, 1048576):
        raise ValueError("Choose 1,024 to 1,048,576 context tokens.")
    if s.get("kv", "auto") not in ("auto", "fp8"):
        raise ValueError("Choose auto or fp8 for the KV cache.")
    if s.get("channel", "Tested") not in CHANNELS:
        raise ValueError("Choose Tested, Updated or Unsloth for the engine release.")
    wait = s.get("start_timeout") or 0
    if not within(wait, int, 0, 0) and not within(wait, int, 30, 7200):
        raise ValueError(
            "Set the start timeout to 0 for automatic, or to 30 to 7,200 seconds."
        )
    llama_args(s.get("engine_args"))
    if engine == "vLLM":
        if not VLLM_MODEL.fullmatch(str(s.get("vllm_model", "")).strip()):
            raise ValueError(
                "Enter the vLLM checkpoint as owner/model in Advanced → Model library."
            )
        if not within(s.get("vllm_fraction"), float, 0.03, 0.98):
            raise ValueError("Set the vLLM GPU memory fraction between 0.03 and 0.98.")
        parser = s.get("vllm_parser")
        if parser and not (isinstance(parser, str) and VLLM_PARSER.fullmatch(parser)):
            raise ValueError(
                "Enter a vLLM tool parser name made of letters, digits, dots, "
                "dashes or underscores, such as hermes."
            )
    if engine == "Custom":
        custom_argv(s)


def ticks(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return ""


def command_digest(proc):
    text = "\0".join(proc.cmdline())
    return hashlib.sha256(text.encode("utf8", "surrogateescape")).hexdigest()


def process_identity(proc):
    ident = {"pid": proc.pid, "born": proc.create_time()}
    started = ticks(proc.pid) if system() == "linux" else ""
    if started:
        ident["ticks"] = started
    try:
        ident["cmd"] = command_digest(proc)
    except psutil.Error:
        pass
    return ident


def same_process(proc, rec):
    try:
        if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
            return "different"
        now = ticks(proc.pid) if rec.get("ticks") else ""
        if now:
            return "same" if now == rec["ticks"] else "different"
        if abs(proc.create_time() - rec["born"]) < 0.02:
            return "same"
        if rec.get("cmd"):
            return "same" if command_digest(proc) == rec["cmd"] else "different"
    except psutil.NoSuchProcess:
        return "different"
    except psutil.AccessDenied:
        return "unknown"
    except (KeyError, TypeError):
        return "different"
    return "unknown"


def record():
    try:
        rec = load(DATA / "running.json", {})
    except (OSError, ValueError):
        return {}
    return rec if isinstance(rec, dict) else {}


def recorded(rec):
    pid = rec["pid"]
    if type(pid) is not int or pid <= 0:
        raise ValueError("The recorded process ID is not usable.")
    return psutil.Process(pid)


def owned():
    rec = record()
    try:
        proc = recorded(rec)
        if same_process(proc, rec) == "same":
            return proc, rec
    except (psutil.Error, KeyError, TypeError, ValueError):
        pass
    return None, {}


def wsl_argv(args):
    return [
        "wsl",
        "-d",
        WSL_DISTRO,
        "-u",
        "root",
        "--cd",
        str(ROOT),
        "--exec",
        "/usr/bin/python3",
        "vllm_worker.py",
        *args,
    ]


def ended(proc):
    try:
        return not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True
    except psutil.Error:
        return False


def settle(procs, seconds):
    limit = time.monotonic() + seconds
    while procs and time.monotonic() < limit:
        _, procs = psutil.wait_procs(procs, timeout=0.2)
        procs = [item for item in procs if not ended(item)]
    return procs


def end_tree(proc):
    try:
        tree = [proc, *proc.children(recursive=True)]
    except psutil.Error:
        tree = [proc]
    for item in tree:
        try:
            item.terminate()
        except psutil.NoSuchProcess:
            pass
    alive = settle(tree, 8)
    for item in alive:
        try:
            item.kill()
        except psutil.NoSuchProcess:
            pass
    settle(alive, 4)


def stop(progress=None, cancel=None, run=None):
    with locked():
        rec = record()
        if not rec or (run and rec.get("run") != run):
            return False
        try:
            proc = recorded(rec)
            verdict = same_process(proc, rec)
        except (psutil.Error, KeyError, TypeError, ValueError):
            proc, verdict = None, "different"
        if verdict == "unknown":
            raise RuntimeError(
                f"Agent Workbench cannot confirm that process {rec['pid']} is its "
                "model server; end that process yourself, then press Stop server again."
            )
        if verdict == "same":
            tell(
                progress,
                job="model-stop",
                phase="stopping",
                pid=proc.pid,
                message="Stopping the model…",
            )
            if rec.get("wsl_run") and system() == "windows":
                try:
                    subprocess.run(
                        wsl_argv(["stop", rec["wsl_run"]]),
                        timeout=20,
                        stdin=subprocess.DEVNULL,
                        creationflags=QUIET,
                    )
                except (OSError, subprocess.SubprocessError):
                    pass
            end_tree(proc)
        (DATA / "running.json").unlink(missing_ok=True)
        tell(
            progress,
            job="model-stop",
            phase="stopped",
            message="The model is stopped.",
        )
        return verdict == "same"


def flash_config():
    return Path(
        os.environ.get(
            "WORKBENCH_FLASH_CONFIG",
            Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share"))
            / "Strata/launch/flash-q2-32k-dflash2.json",
        )
    )


def inkling_gguf(model):
    name = Path(model).name.lower()
    return name.startswith("inkling-2-mini") and name.endswith((".gguf", ".gguf_file"))


def drafter_folder(model):
    """Where the DSpark drafter goes for these weights: their repository folder."""
    model = Path(model)
    nested = model.parent.name == "Mainline_Support"
    return model.parent.parent if nested else model.parent


def drafter(model):
    """The DSpark drafter beside an Inkling GGUF or in its repository folder, or None."""
    model = Path(model)
    places = (model.parent / DSPARK, drafter_folder(model) / DSPARK)
    return next((path for path in places if path.is_file()), None)


def draft_note(s):
    """Why an Inkling GGUF with drafting on starts without the DSpark drafter, or ""."""
    model = s.get("model") or ""
    if (
        not s.get("draft")
        or s.get("engine") not in ("Auto", "llama.cpp")
        or not inkling_gguf(model)
    ):
        return ""
    if s.get("channel") != "Unsloth":
        return (
            "Inkling 2 Mini runs without the DSpark drafter, which needs Unsloth's "
            "llama.cpp build; set Engine release to Unsloth in Advanced → Model "
            "library to use it."
        )
    if not drafter(model):
        return (
            f"Inkling 2 Mini runs without the DSpark drafter, because {DSPARK} is not "
            "next to the model; choose Download drafter in Advanced → Model library."
        )
    return ""


def backend(s):
    model = Path(s["model"])
    engine = s["engine"]
    name = model.name.lower()
    if engine not in ("vLLM", "Custom") and not model.is_file():
        raise ValueError("Select a model file in the library.")
    if system() == "macos" and (
        engine in ("vLLM", "Strata", "Flash preset")
        or engine == "Auto"
        and (model.suffix == ".strata" or "flash-next" in name)
    ):
        raise ValueError(
            "Choose a Metal-compatible GGUF with llama.cpp, or attach a running endpoint."
        )
    validate_serving(s)
    ctx = int(s["context"])
    env = dict(os.environ, PYTHONUTF8="1")
    inherited = os.environ.get("LD_LIBRARY_PATH", "")
    if engine == "Custom":
        argv = custom_argv(s)
        env["WORKBENCH_API_KEY"] = key()
        tokens = {
            "model": str(model),
            "port": str(PORT),
            "context": str(ctx),
            "alias": ALIAS,
            "host": "127.0.0.1",
            "keyfile": str(DATA / "api-key.txt"),
        }
        pattern = r"\{(model|port|context|alias|host|keyfile)\}"
        cmd = [re.sub(pattern, lambda m: tokens[m[1]], v) for v in argv]
        if (
            system() == "windows"
            and cmd[0].rstrip(". ").lower().endswith((".cmd", ".bat"))
            and any(SHIM_UNSAFE.search(v) for v in cmd)
        ):
            raise ValueError(
                "The custom engine manifest starts a .cmd or .bat file, which cannot "
                "safely receive these arguments; set argv to the engine program "
                "itself, then start again."
            )
        return cmd, env
    if engine == "vLLM":
        env["VLLM_API_KEY"] = key()
        tag = secrets.token_hex(12)
        env["WORKBENCH_JOB_ID"] = tag
        args = [
            "serve",
            tag,
            str(s["vllm_model"]).strip(),
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
            "--served-model-name",
            ALIAS,
            "--max-model-len",
            str(ctx),
            "--gpu-memory-utilization",
            str(float(s["vllm_fraction"])),
        ]
        if s.get("vllm_parser"):
            args += [
                "--enable-auto-tool-choice",
                "--tool-call-parser",
                s["vllm_parser"],
            ]
        if system() == "windows":
            env["WSLENV"] = ":".join(
                filter(
                    None,
                    [env.get("WSLENV"), "VLLM_API_KEY", "HF_TOKEN", "WORKBENCH_VLLM"],
                )
            )
            return wsl_argv(args), env
        return [sys.executable, str(ROOT / "vllm_worker.py"), *args], env
    if engine == "Auto":
        if "flash-next" in name:
            engine = "Flash preset"
        elif model.suffix == ".strata":
            engine = "Strata"
        elif (
            name == "inkling-2-mini-ud-q4_k_xl.gguf"
            and model.stat().st_size == 17629759456
        ):
            raise ValueError(
                "This Unsloth Inkling GGUF needs the vendor runtime. Select the Mainline_Support GGUF or the Strata SQ pack."
            )
        else:
            engine = "llama.cpp"
    if engine == "Flash preset":
        source = flash_config()
        if not source.is_file():
            raise ValueError(
                "The Strata Flash Next preset was not found; set "
                "WORKBENCH_FLASH_CONFIG to its JSON file, or choose llama.cpp."
            )
        try:
            cfg = load(source, {})
            native = Path(cfg["args"][cfg["args"].index("--native") + 1])
            server = Path(cfg["cwd"]) / "serve/server.py"
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            raise ValueError(
                f"The Strata Flash Next preset at {source} could not be read; "
                "create it again with Strata, or choose llama.cpp."
            ) from None
        if native.resolve() != model.resolve():
            raise ValueError(
                "Select the Flash Next weights referenced by the local Strata preset."
            )
        if ctx != 32768 or not s["draft"]:
            raise ValueError(
                "This imported Flash Next preset uses 32768 context and DFlash2."
            )
        cfg["log"] = str(DATA / "engine.log")
        folders = []
    elif engine == "Strata":
        if "inkling" not in name or model.suffix != ".strata":
            raise ValueError(
                "Strata CUDA 0.1.65 requires a native Inkling SQ pack for this profile."
            )
        source = (
            Path(load(DATA / "updates.json", {}).get("source", DATA / "Strata"))
            if s["channel"] == "Updated"
            else DATA / "Strata"
        )
        server = source / "terminal/serve/server.py"
        if not server.is_file():
            raise EngineMissing(
                "Strata",
                engine_support()["Strata"]
                or f"The Strata engine is not installed: {install_hint()}.",
            )
        folder, exe = locate(
            "strata-inkling-0.1.65", "strata-inkling.exe", s["channel"]
        )
        folders = [folder]
        converter = server.parents[1] / "tools/inkling_tokenizer.py"
        tokenizer = DATA / "ink-tokenizer"
        if not (tokenizer / "merges.txt").exists():
            parts = [
                model.with_name("tokenizer.json"),
                model.with_name("chat_template.jinja"),
            ]
            if not all(part.is_file() for part in parts):
                raise ValueError(
                    "Place tokenizer.json and chat_template.jinja next to the "
                    "Strata pack, then start again."
                )
            try:
                subprocess.run(
                    [
                        sys.executable,
                        str(converter),
                        "--hf-json",
                        str(parts[0]),
                        "--template",
                        str(parts[1]),
                        "--out",
                        str(tokenizer),
                    ],
                    check=True,
                    stdin=subprocess.DEVNULL,
                    timeout=300,
                    creationflags=QUIET,
                )
            except (OSError, subprocess.SubprocessError):
                raise ValueError(
                    "The Strata tokenizer could not be converted; download the pack "
                    "again with its tokenizer.json and chat_template.jinja, then "
                    "start again."
                ) from None
        args = [
            "--native",
            str(model),
            "--kv",
            s["kv"],
            "--draft",
            "dspark" if s["draft"] else "off",
            "--parallel",
            "1",
            "--max-context",
            str(ctx),
        ]
        vision = model.with_name("Inkling-2-Mini-Vision.strata")
        if s["vision"] and vision.is_file():
            args += ["--vision", str(vision)]
        cfg = dict(
            exe=str(exe),
            arch="inkling2_loop",
            tokenizer=str(tokenizer),
            args=args,
            log=str(DATA / "engine.log"),
        )
        env["STRATA_DATA_DIR"] = str(DATA / "strata-data")
    else:
        qwen = name == "qwen3.8-27b-ud-q4_k_xl.gguf"
        adapter = model.with_name("Qwen3.8-27B-pi-lora-F16.gguf")
        try:
            folder, exe = locate(
                "llama-native-b12483"
                if qwen and adapter.exists()
                else "llama-native-b12633",
                "llama-server.exe",
                s["channel"],
            )
        except ValueError:
            folder, exe = locate(
                "llama-native-b12633", "llama-server.exe", s["channel"]
            )
        cmd = [
            str(exe),
            "-m",
            str(model),
            "--alias",
            ALIAS,
            "--jinja",
            "-fa",
            "on",
            "-ngl",
            "all",
            "-c",
            str(ctx),
            "-np",
            "1",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
            "--api-key-file",
            str(DATA / "api-key.txt"),
        ]
        if qwen and adapter.exists():
            if s["lora"]:
                cmd += ["--lora", str(adapter)]
            cmd += [
                "--reasoning",
                "off",
                "--chat-template-kwargs",
                '{"enable_thinking":false}',
                "-ctk",
                "q8_0",
                "-ctv",
                "q8_0",
                "--no-mmproj",
            ]
            if s["draft"]:
                cmd += ["--spec-type", "draft-mtp", "--spec-draft-n-max", "9"]
        else:
            cmd += ["--no-mmproj"]
        dspark = (
            drafter(model)
            if s["draft"] and s["channel"] == "Unsloth" and inkling_gguf(model)
            else None
        )
        if dspark:
            cmd += ["-md", str(dspark), *DSPARK_ARGS]
        import chat_templates

        fixed = chat_templates.override(model, DATA / "templates")
        if fixed:
            cmd += ["--chat-template-file", str(fixed)]
        cmd += llama_args(s.get("engine_args"))
        if system() == "linux":
            env["LD_LIBRARY_PATH"] = library_path(exe, [folder], inherited)
        return cmd, env
    if not server.is_file():
        raise ValueError(
            "The Strata server referenced by the Flash Next preset is missing; "
            "reinstall Strata or choose llama.cpp."
        )
    cfg.update(model_name=ALIAS, port=PORT, host="127.0.0.1", open_browser=False)
    write_json(DATA / "serve.json", cfg)
    env.update(PYTHONPATH=str(server.parents[1]), STRATA_API_KEY=key())
    if system() == "linux":
        try:
            channel = "Updated" if s["channel"] == "Updated" else "Tested"
            folders.append(
                locate("llama-native-b12633", "llama-server.exe", channel)[0]
            )
        except ValueError:
            pass
        exe = cfg.get("exe")
        env["LD_LIBRARY_PATH"] = library_path(
            exe if isinstance(exe, str) else server, folders, inherited
        )
    return [
        sys.executable,
        "-u",
        str(server),
        "--engine",
        "strata",
        "--config",
        str(DATA / "serve.json"),
        "--port",
        str(PORT),
        "--host",
        "127.0.0.1",
    ], env


def serving_spec(s):
    spec = {
        k: s[k]
        for k in (
            "model",
            "engine",
            "context",
            "draft",
            "vision",
            "kv",
            "lora",
            "channel",
        )
    }
    if s["engine"] == "vLLM":
        spec.update(
            vllm_model=s["vllm_model"],
            vllm_fraction=s["vllm_fraction"],
            vllm_parser=s.get("vllm_parser", ""),
        )
    if s["engine"] == "Custom":
        spec["manifest"] = manifest(s)
    extra = s.get("engine_args")
    spec["engine_args"] = list(extra) if isinstance(extra, (list, tuple)) else []
    return spec


def comparable_spec(spec):
    values = dict(spec)
    name = Path(values["model"]).name.lower()
    if values["engine"] == "Auto":
        values["engine"] = (
            "Flash preset"
            if "flash-next" in name
            else "Strata"
            if name.endswith(".strata")
            else "llama.cpp"
        )
    if "qwen3.8-27b" not in name or values["engine"] != "llama.cpp":
        values.pop("lora", None)
    if values["engine"] != "llama.cpp" or not values.get("engine_args"):
        values.pop("engine_args", None)
    return values


def same_server(s, record):
    return comparable_spec(serving_spec(s)) == comparable_spec(record["spec"])


def start_budget(s):
    return int(s.get("start_timeout") or 0) or (1800 if s["engine"] == "vLLM" else 600)


def open_log(path):
    if system() == "windows":
        return path.open("w", encoding="utf8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "w", encoding="utf8")


def spawn_error(s, cmd, exc):
    reason = exc.strerror or type(exc).__name__
    if s["engine"] == "Custom":
        return (
            f"The engine program {cmd[0]} could not be started ({reason}); "
            "correct the argv in your custom engine manifest."
        )
    if s["engine"] == "vLLM" and system() == "windows":
        return (
            "vLLM runs inside WSL on Windows and WSL did not start; open "
            "Advanced → Setup → vLLM → Install, then try again."
        )
    return (
        f"The engine at {cmd[0]} could not be started ({reason}); "
        f"reinstall it: {install_hint()}."
    )


def log_tail(lines=20):
    try:
        with (DATA / "serve.log").open("rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            stream.seek(max(0, size - 8192))
            text = stream.read().decode("utf8", "replace").replace("\0", "")
    except OSError:
        return []
    if size > 8192:
        text = text.partition("\n")[2]
    try:
        secret = (DATA / "api-key.txt").read_text().strip()
    except OSError:
        secret = ""
    if secret:
        text = text.replace(secret, "[key]")
    return [v.strip() for v in text.splitlines() if v.strip()][-lines:]


def listening(rec):
    if rec.get("engine") in (None, "Custom") or (
        rec.get("wsl_run") and system() == "windows"
    ):
        return True
    try:
        proc = recorded(rec)
        tree = [proc, *proc.children(recursive=True)]
    except (psutil.Error, KeyError, TypeError, ValueError):
        return False
    port = rec.get("port", PORT)
    for item in tree:
        try:
            for conn in item.net_connections(kind="tcp"):
                if (
                    conn.status == psutil.CONN_LISTEN
                    and conn.laddr
                    and conn.laddr.port == port
                ):
                    return True
        except psutil.NoSuchProcess:
            continue
        except psutil.Error:
            return True
    return False


def alive(rec, child=None):
    if child is not None:
        return child.poll() is None
    try:
        return same_process(recorded(rec), rec) == "same"
    except (psutil.Error, KeyError, TypeError, ValueError):
        return False


def stopped(run):
    with locked():
        return record().get("run") != run


def await_ready(rec, child=None, progress=None, cancel=None):
    mine, run = child is not None, rec.get("run")
    budget = rec.get("budget", 600)
    waited = 0 if mine else max(0, time.time() - rec.get("started", time.time()))
    began = time.monotonic()
    deadline = began + (budget if mine else max(5, budget - waited))
    halted = "The model was stopped before it finished loading."
    shown = -1
    while True:
        if cancelled(cancel):
            if mine:
                stop(run=run)
            raise Cancelled("The model start was cancelled.")
        if record().get("run") != run:
            raise Cancelled(halted)
        if not alive(rec, child):
            tail = log_tail()
            if stopped(run):
                raise Cancelled(halted)
            if mine:
                stop(run=run)
            raise StartFailed(
                "The model server exited while loading "
                f"({tail[-1][:200] if tail else 'no output'}); "
                f"open {DATA / 'serve.log'} for the full log.",
                tail,
            )
        elapsed = waited + time.monotonic() - began
        if listening(rec):
            try:
                if endpoint_model(timeout=2) == ALIAS:
                    tell(
                        progress,
                        job="model-start",
                        phase="ready",
                        elapsed=int(elapsed),
                        message=" ".join(
                            filter(None, ["The model is ready.", rec.get("note")])
                        ),
                    )
                    return rec
            except (
                requests.RequestException,
                KeyError,
                IndexError,
                TypeError,
                ValueError,
            ):
                pass
        if time.monotonic() >= deadline:
            if not mine:
                raise RuntimeError(
                    "The model server is running but not answering; press Stop "
                    "server, then start it again."
                )
            tail = log_tail()
            stop(run=run)
            raise StartFailed(
                f"The model was not ready after {budget} seconds and was stopped; "
                "choose a smaller model or context, or set a longer start_timeout "
                f"in {DATA / 'settings.json'}, then start again.",
                tail,
            )
        if int(elapsed) != shown:
            shown = int(elapsed)
            last = log_tail(1)
            tell(
                progress,
                job="model-start",
                phase="loading",
                elapsed=shown,
                budget=budget,
                message=last[0][:200] if last else "Loading the model…",
            )
        pause(cancel, POLL)


def start(s, progress=None, cancel=None):
    spec = serving_spec(s)
    with locked():
        current, rec = owned()
        if current:
            if not same_server(s, rec):
                raise RuntimeError(
                    "Stop the active model before changing its serving settings."
                )
            child = None
            tell(
                progress,
                job="model-start",
                phase="waiting",
                message="Waiting for the model that is already loading…",
            )
        else:
            with socket.socket() as sock:
                sock.settimeout(0.4)
                if sock.connect_ex(("127.0.0.1", PORT)) == 0:
                    raise RuntimeError(
                        f"Port {PORT} already has a listener; stop that program, "
                        "attach to it as a connection, or set WORKBENCH_PORT to a "
                        "free port and restart Agent Workbench."
                    )
            engine = comparable_spec(spec)["engine"]
            tell(
                progress,
                job="model-start",
                phase="prepare",
                engine=engine,
                message=f"Preparing {engine}…",
            )
            key()
            cmd, env = backend(s)
            note = draft_note(s)
            if note:
                tell(progress, job="model-start", phase="prepare", message=note)
            detach = {} if system() == "windows" else {"start_new_session": True}
            with open_log(DATA / "serve.log") as log:
                try:
                    child = subprocess.Popen(
                        cmd,
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        creationflags=QUIET,
                        **detach,
                    )
                except OSError as exc:
                    raise RuntimeError(spawn_error(s, cmd, exc)) from None
            try:
                try:
                    ident = process_identity(psutil.Process(child.pid))
                except psutil.Error:
                    ident = dict(pid=child.pid, born=0.0)
                rec = ident | dict(
                    run=secrets.token_hex(8),
                    spec=spec,
                    wsl_run=env.get("WORKBENCH_JOB_ID"),
                    engine=engine,
                    exe=cmd[0],
                    port=PORT,
                    started=int(time.time()),
                    budget=start_budget(s),
                    log=str(DATA / "serve.log"),
                )
                if note:
                    rec["note"] = note
                write_json(DATA / "running.json", rec)
            except BaseException:
                child.kill()
                raise
            tell(
                progress,
                job="model-start",
                phase="spawn",
                pid=child.pid,
                engine=engine,
                budget=rec["budget"],
                message=f"Starting {engine}…",
            )
    try:
        return await_ready(rec, child, progress, cancel)
    except (Cancelled, StartFailed):
        raise
    except BaseException:
        if child is not None:
            try:
                stop(run=rec["run"])
            except Exception:
                pass
        raise


def engine_support():
    import setup_engines

    info, name = setup_engines.detect(), system()
    cuda = (
        name != "macos"
        and info["supported"]
        and "cuda" in (info["backend"], setup_engines.installed_backend())
    )
    support = dict.fromkeys(ENGINES, "")
    for engine in ("Strata", "vLLM"):
        if name == "macos":
            support[engine] = (
                f"{engine} needs an NVIDIA GPU on Windows or Linux; on this Mac "
                "choose llama.cpp with a GGUF model, or attach a running endpoint."
            )
        elif not info["supported"]:
            support[engine] = (
                f"{engine} is not available on this computer; attach a running "
                "endpoint in Connections instead."
            )
        elif not cuda:
            support[engine] = (
                f"{engine} needs an NVIDIA GPU with a working driver; choose "
                f"llama.cpp, which uses the {setup_engines.LABEL[info['backend']]} "
                "build on this computer."
            )
    if name == "macos" or not flash_config().is_file():
        support["Flash preset"] = (
            "Flash preset needs an existing Strata Flash Next launch preset on "
            "Windows or Linux; choose llama.cpp instead."
        )
    return support


def available_engines():
    support = engine_support()
    return [engine for engine in ENGINES if not support[engine]]


def valid_preset(row):
    allowed = {"name", "file", "engine", "context", "kv", "draft", "vision", "lora"}
    return (
        isinstance(row, dict)
        and set(row) <= allowed
        and isinstance(row.get("name"), str)
        and 0 < len(row["name"]) <= 80
        and isinstance(row.get("file"), str)
        and bool(row["file"])
        and "/" not in row["file"]
        and "\\" not in row["file"]
        and isinstance(row.get("engine"), str)
        and row["engine"] in ENGINES
        and type(row.get("context")) is int
        and 1024 <= row["context"] <= 1048576
        and row.get("kv", "auto") in ("auto", "fp8")
        and all(
            isinstance(row.get(k, False), bool) for k in ("draft", "vision", "lora")
        )
    )


def preset_rows():
    try:
        rows = load(ROOT / "profiles.json", [])
    except (OSError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    engines = available_engines()
    return [row for row in rows if valid_preset(row) and row["engine"] in engines]


BLENDER_MCP_PACKAGE = "mcp-for-blender@2.2.7"
BLENDER_PROTOCOL_RE = re.compile(r"ADDON_PROTOCOL_VERSION\s*=\s*(\d+)")


def mcp_servers(s):
    servers = {}
    for cap in ["search", "firecrawl"]:
        if s[cap]:
            servers[cap] = dict(
                type="stdio",
                command=sys.executable,
                args=[str(ROOT / "web_tools.py"), cap],
            )
    for cap, package in [
        ("blender", BLENDER_MCP_PACKAGE),
        ("desktop", "windows-mcp@0.9.0"),
    ]:
        if s[cap] and (cap != "desktop" or system() == "windows"):
            uvx = shutil.which("uvx")
            if not uvx:
                raise ValueError(
                    "Install uv in Advanced → Setup, then enable the Blender or "
                    "Windows desktop tool again."
                )
            args = (
                [package, "serve", "--transport", "stdio"]
                if cap == "desktop"
                else [package]
            )
            servers[cap] = dict(
                type="stdio",
                command=uvx,
                args=args,
                env={
                    "PYTHONUTF8": "1",
                    "ANONYMIZED_TELEMETRY": "false",
                    "BLENDER_MCP_DISABLE_TELEMETRY": "1",
                },
            )
    if s.get("extra_mcp"):
        try:
            additions = load(Path(s["extra_mcp"]), None)
        except (OSError, ValueError):
            additions = None
        added = additions.get("mcpServers") if isinstance(additions, dict) else None
        if not isinstance(added, dict) or not all(
            isinstance(conf, dict) for conf in added.values()
        ):
            raise ValueError(
                "The Extra MCP manifest set in Advanced → Agent settings could not "
                "be read; choose a JSON file with an mcpServers object, or clear "
                "that field."
            )
        for name in added:
            if name in servers:
                raise ValueError(
                    f"The Extra MCP manifest defines {name}, which Agent Workbench "
                    "already provides; rename that server in the file."
                )
        servers.update(added)
    from project_profiles import plugin_ids, registry

    chosen = plugin_ids(s)
    if chosen:
        try:
            installed = registry()
        except (OSError, ValueError):
            installed = None
        if not isinstance(installed, dict):
            raise ValueError(
                f"The plugin registry at {DATA / 'plugins.json'} could not be read; "
                "move that file aside, then register your MCP files again in Plugins."
            )
    for name in chosen:
        if name not in installed or name in servers:
            raise ValueError("Missing or overlapping MCP plugin: " + name)
        servers[name] = installed[name]
    for name, conf in (s.get("_mcp") or {}).items():
        if name in servers:
            raise ValueError("Missing or overlapping MCP plugin: " + name)
        servers[name] = conf
    return servers


def omp_command(
    s,
    profile_name=PROFILE,
    base=URL + "/v1",
    model_id=ALIAS,
    env_key=None,
    anchor=None,
):
    from host import command

    try:
        omp = command("omp")
    except ValueError:
        raise ValueError(
            "OMP is not installed; open Advanced → Setup → OMP → Install."
        ) from None
    if not Path(s["workspace"]).is_dir():
        raise ValueError("Choose an existing workspace.")

    import project_setup

    servers = mcp_servers(s)
    if "_mcp" not in s:
        servers.update(project_setup.omp_entries(s, set(servers)))
    # Keep generated provider and tool files stable while sessions with other configurations launch.
    identity = [
        model_id,
        base if anchor is None else anchor,
        s["model"],
        int(s["context"]),
        s["vision"],
        s["thinking"],
        servers,
        bool(s.get("document_skill")),
    ]
    if s.get("_visual"):
        identity.append(s["_visual"])
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[
        :12
    ]
    profile_name = f"{profile_name}-{digest}"
    profile = Path.home() / ".omp/profiles" / profile_name / "agent"
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    protect(profile)
    if s.get("document_skill"):
        dest = profile / "skills" / "workbench-csv"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "packs" / "csv" / "SKILL.md", dest / "SKILL.md")
    model = Path(s["model"])
    visual = s.get("_visual", {})
    vision = visual.get("primary_image", False) or (
        s.get("provider", "Managed local") == "Managed local"
        and s["vision"]
        and model.suffix == ".strata"
        and model.with_name("Inkling-2-Mini-Vision.strata").exists()
    )
    entry = dict(
        id=model_id,
        # A machine's managed node also serves "local-agent", and there is no local file to name it after.
        name=(model.stem or model_id) if model_id == ALIAS else model_id,
        input=["text", "image"] if vision else ["text"],
        reasoning=s["thinking"] != "off",
        contextWindow=int(s["context"]),
        maxTokens=min(int(s["context"]) // 2, 16384),
        supportsTools=True,
        compat=dict(
            supportsDeveloperRole=False, supportsStore=False, supportsStrictMode=False
        ),
    )
    entries = [entry]
    reader = visual.get("reader") or model_id
    if reader != model_id:
        entries.append(
            {**entry, "name": reader, "id": reader, "input": ["text", "image"]}
        )
    write_json(
        profile / "models.yml",
        {
            "providers": {
                "workbench": {
                    "api": "openai-completions",
                    "baseUrl": base,
                    "apiKey": env_key or key(),
                    "models": entries,
                }
            }
        },
    )
    roles = "default slow plan task smol tiny memory commit web advisor review reviewer validator orchestrator thinker work secondary judge vision".split()
    # OMP owns config.yml and writes wizard completion, preferences and account setup there.
    overlay = profile / "workbench-overlay.json"
    write_json(
        overlay,
        {
            "modelRoles": dict.fromkeys(roles, "workbench/" + model_id)
            | {"vision": "workbench/" + reader},
            "disabledProviders": ["ollama"],
            "defaultThinkingLevel": s["thinking"],
            "startup": {"setupWizard": False},
            "symbolPreset": "ascii",
            "mcp": {
                "startupTimeoutMs": 15000,
                "enableProjectConfig": project_setup.project_config_allowed(
                    s["workspace"]
                ),
            },
            "tools": {"commands": False},
            "web_search": {"enabled": False},
        },
    )
    write_json(profile / "mcp.json", {"mcpServers": servers})
    return [
        *omp,
        "--profile",
        profile_name,
        "--config",
        str(overlay),
        "--offline",
        "--model",
        "workbench/" + model_id,
        "--thinking",
        s["thinking"],
        "--cwd",
        s["workspace"],
        "--append-system-prompt",
        "\n".join(
            filter(
                None,
                [
                    s["prompt"],
                    "Use the search MCP web_search tool to find public pages."
                    if s["search"]
                    else "",
                    "Use the Firecrawl MCP scrape tool with a URL to read a page."
                    if s["firecrawl"]
                    else "",
                ],
            )
        ),
        "--approval-mode",
        "write",
    ]


def open_agent(s):
    if s["firecrawl"]:
        from cli import web

        web(True)
    if s["blender"]:
        ensure_blender()
    start(s)
    from host import terminal

    terminal(omp_command(s), cwd=s["workspace"])


def _blender_addon_protocol(path):
    try:
        m = BLENDER_PROTOCOL_RE.search(Path(path).read_text(encoding="utf8", errors="ignore"))
        return int(m.group(1)) if m else None
    except OSError:
        return None


def blender_addon_source():
    """Return a cached copy of the bundled addon.py from the pinned mcp-for-blender package."""
    folder = DATA / "blender-mcp"
    cached = folder / "blender_mcp.py"
    stamp = folder / "package.txt"
    try:
        if cached.is_file() and stamp.is_file() and stamp.read_text(encoding="utf8").strip() == BLENDER_MCP_PACKAGE:
            return cached
    except OSError:
        pass
    uvx = shutil.which("uvx")
    if not uvx:
        raise ValueError(
            "Install uv in Advanced → Setup, then enable the Blender tool again."
        )
    folder.mkdir(parents=True, exist_ok=True)
    part = folder / f".blender_mcp.{os.getpid()}.tmp"
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "ANONYMIZED_TELEMETRY": "false",
        "BLENDER_MCP_DISABLE_TELEMETRY": "1",
    }
    extract = (
        "import shutil, sys; "
        "from blender_mcp.addon_manager import get_bundled_addon_path; "
        "shutil.copyfile(get_bundled_addon_path(), sys.argv[1])"
    )
    try:
        result = subprocess.run(
            [uvx, "--from", BLENDER_MCP_PACKAGE, "python", "-c", extract, str(part)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            creationflags=QUIET,
        )
        if result.returncode != 0 or not part.is_file() or part.stat().st_size == 0:
            raise ValueError(
                "Could not prepare the Blender MCP add-on from "
                f"{BLENDER_MCP_PACKAGE}. Check your internet connection and retry."
            )
        os.replace(part, cached)
        stamp.write_text(BLENDER_MCP_PACKAGE + "\n", encoding="utf8")
    except (OSError, subprocess.SubprocessError):
        raise ValueError(
            "Could not prepare the Blender MCP add-on from "
            f"{BLENDER_MCP_PACKAGE}. Check your internet connection and retry."
        ) from None
    finally:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
    return cached


def blender_addons_dir(blender):
    """Ask Blender itself for its user scripts/addons directory (works for native, Flatpak, Snap, macOS, Windows)."""
    probe = "import bpy; print('AWB_ADDONS_DIR:' + bpy.utils.user_resource('SCRIPTS', path='addons', create=True))"
    try:
        result = subprocess.run(
            [blender, "--background", "--factory-startup", "--python-expr", probe],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=QUIET,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (result.stdout or "").splitlines():
        if line.startswith("AWB_ADDONS_DIR:"):
            raw = line[len("AWB_ADDONS_DIR:") :].strip()
            if raw:
                return Path(raw)
    return None


def ensure_blender_addon(blender, enable=True):
    """Install the pinned Blender MCP add-on into Blender's addons folder if missing or behind. Returns True if written."""
    addons_dir = blender_addons_dir(blender)
    if not addons_dir:
        return False
    source = blender_addon_source()
    addons_dir.mkdir(parents=True, exist_ok=True)
    target = addons_dir / "blender_mcp.py"
    source_bytes = source.read_bytes()
    written = False
    if target.is_file():
        try:
            existing_bytes = target.read_bytes()
        except OSError:
            existing_bytes = b""
        if existing_bytes != source_bytes:
            installed_proto = _blender_addon_protocol(target)
            bundled_proto = _blender_addon_protocol(source)
            if installed_proto is None or (bundled_proto is not None and installed_proto < bundled_proto):
                try:
                    shutil.copy2(target, target.with_suffix(".py.bak"))
                except OSError:
                    pass
                shutil.copy2(source, target)
                written = True
    else:
        shutil.copy2(source, target)
        written = True
    if enable:
        enable_code = (
            "import addon_utils, bpy\n"
            "enabled, _ = addon_utils.check('blender_mcp')\n"
            "if not enabled:\n"
            "    addon_utils.enable('blender_mcp', default_set=True, persistent=True)\n"
            "    bpy.ops.wm.save_userpref()\n"
        )
        try:
            subprocess.run(
                [blender, "--background", "--python-expr", enable_code],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=QUIET,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    return written


def ensure_blender():
    def listening():
        try:
            with socket.create_connection(("127.0.0.1", 9876), timeout=0.5):
                return True
        except OSError:
            return False

    if listening():
        return
    from host import blender_executable

    blender = blender_executable()
    if not blender:
        raise ValueError("Install Blender in Advanced → Setup, then try again.")
    if any(
        (proc.info["name"] or "").lower() in ("blender", "blender.exe")
        for proc in psutil.process_iter(["name"])
    ):
        installed = ensure_blender_addon(blender, enable=False)
        if installed:
            raise ValueError(
                "Installed the Blender MCP add-on. Restart Blender, or enable "
                "'MCP for Blender' in Edit → Preferences → Add-ons and start its server."
            )
        raise ValueError("Enable the Blender MCP server in the open Blender window.")
    ensure_blender_addon(blender, enable=True)
    subprocess.Popen(
        [
            blender,
            "--addons",
            "blender_mcp",
            "--python-expr",
            "import bpy; bpy.app.timers.register(lambda: (bpy.ops.blendermcp.start_server(), None)[1], first_interval=2)",
        ],
        stdin=subprocess.DEVNULL,
        **({} if system() == "windows" else {"start_new_session": True}),
    )
    for _ in range(35):
        if listening():
            return
        time.sleep(1)
    raise RuntimeError(
        "Blender opened. Enable the Blender MCP add-on and start its server."
    )


def extend_path(environ=None, home=None):
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    path = environ.get("PATH", "")
    known, added = path.split(os.pathsep), []
    for folder in (
        home / ".local/bin",
        home / ".bun/bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
    ):
        if folder.is_dir() and str(folder) not in known + added:
            added.append(str(folder))
    if added:
        ours = environ.get("WORKBENCH_PATH_ADDED", "").split(os.pathsep)
        environ["PATH"] = os.pathsep.join(filter(None, [path, *added]))
        environ["WORKBENCH_PATH_ADDED"] = os.pathsep.join(
            dict.fromkeys(filter(None, ours + added))
        )


if sys.platform == "darwin":
    extend_path()
