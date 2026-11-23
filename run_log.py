"""Run summaries: what each agent launch Workbench started actually used, kept per folder in plain JSON."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import traceback
from urllib.parse import urlsplit

import paths
import project_profiles
import runtime
from runtime import ROOT

MAX_RUNS = 100
MAX_PROMPT = 8000
HASH_CAP = 1 << 20
MAX_FILE = 4 << 20
LIST_LIMIT = 100
VERSION = 1
KINDS = {"window": "Own window", "embedded": "Terminal dock", "resume": "Resumed session"}
SKIP = ("shadowed", "ignored")
RUN_ID = r"[0-9a-f]{16}"
GONE = "This run is no longer in the list. Refresh Recent runs, then try again."
DAMAGED = (
    "The run list for this folder could not be read. Choose Clear runs to start a new list."
)
# Workbench reads the folder's files; with the agent account the agent also reads its own settings folder.
NATIVE_FILES = (
    "This run used the agent account, so the agent may also have read personal instruction files "
    "in your home folder. They are not listed here."
)
_VERSION = []


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def app_version():
    """The Workbench version from desktop/package.json; '' when it cannot be read."""
    if not _VERSION:
        try:
            found = json.loads((ROOT / "desktop" / "package.json").read_text("utf8"))
            value = found.get("version") if isinstance(found, dict) else ""
        except (OSError, ValueError):
            value = ""
        _VERSION.append(value if isinstance(value, str) and len(value) <= 40 else "")
    return _VERSION[0]


def origin(url):
    """scheme://host:port of an address, without user name, password, path or query."""
    try:
        parts = urlsplit(str(url or ""))
        host, port = parts.hostname or "", parts.port
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not host:
        return ""
    shown = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{shown}" + (f":{port}" if port else "")


def _digest(path):
    """(size, sha256 of the first 1 MiB) of a regular file; (None, '') when it cannot be read."""
    try:
        size = os.stat(path).st_size
        raw = project_profiles._read_capped(path)[:HASH_CAP]
    except (OSError, ValueError):
        return None, ""
    return size, hashlib.sha256(raw).hexdigest()


def _catalog_sha(name):
    if not name:
        return ""
    try:
        data = json.loads((ROOT / "model_catalog.json").read_text("utf8"))
        for model in data.get("models", []):
            for file in model.get("files", []):
                digest = file.get("sha256")
                if (
                    str(file.get("path", "")).rsplit("/", 1)[-1] == name
                    and isinstance(digest, str)
                    and re.fullmatch(r"[0-9a-f]{64}", digest)
                ):
                    return digest
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return ""


def _connection(s):
    ident = s.get("connection_id")
    if not isinstance(ident, str) or not ident:
        return ""
    if ident.startswith("legacy-"):
        return ""
    import account_book

    row = account_book.entries().get(ident) or {}
    name = row.get("name") if row.get("provider") == s.get("provider") else ""
    return project_profiles._text(name, 90)


def _settings(s):
    """The portable launch choices of this run, each checked the way a saved profile is."""
    out = {}
    for key in project_profiles.PORTABLE:
        if key == "prompt" or key not in s:
            continue
        try:
            out[key] = project_profiles._field(key, s[key])
        except (ValueError, TypeError):
            continue
    return out


def _files(root, rows):
    out = []
    for row in rows:
        if row.get("status") in SKIP:
            continue
        size, digest = _digest(row.get("abs") or Path(root) / row["path"])
        out.append(
            {"path": row["path"], "status": row.get("status", "read"), "bytes": size, "sha256": digest}
        )
    return out


def _skills(root, rows):
    out = []
    for row in rows:
        if row.get("status") != "read":
            continue
        _, digest = _digest(Path(root) / row["path"])
        out.append({"name": row["name"], "path": row["path"], "sha256": digest})
    return out


def build(facts, harness, kind, cli_version=""):
    """One run record from what connections.prepare reported. Holds no secret: no key, no full address."""
    import project_setup

    s = facts["settings"]
    root = paths.canonical(s["workspace"])
    provider = s.get("provider", "Managed local")
    local = provider == "Managed local"
    view = project_profiles.folder_view(root, strict=False)
    try:
        found = project_setup.resolve(root, harness, cli_version, s)
    except ValueError:
        found = {"instructions": [], "skills": [], "consent": {}}
    consent = found.get("consent") or {}
    weights = Path(str(s.get("model") or "")).name if local else ""
    prompt = s.get("prompt") if facts.get("prompt") and isinstance(s.get("prompt"), str) else ""
    return {
        "id": secrets.token_hex(8),
        "started": _now(),
        "workbench": app_version(),
        "project": str(root),
        "kind": kind if kind in KINDS else "embedded",
        "harness": harness,
        "harness_version": project_profiles._text(cli_version, 80),
        "source": provider,
        "connection": _connection(s),
        "endpoint": origin(facts.get("base")),
        "model": project_profiles._text(facts.get("model"), 300),
        "context": s.get("context")
        if type(s.get("context")) is int and facts.get("base")
        else None,
        "reasoning": project_profiles._text(s.get("thinking"), 20),
        "engine": project_profiles._text(s.get("engine"), 40) if local else "",
        "weights": weights,
        "weights_sha256": _catalog_sha(weights),
        "auth_provider": project_profiles._text(s.get("auth_provider"), 40)
        if provider == "Native login"
        else "",
        "profile": view.get("profile", ""),
        "kit": view.get("kit", ""),
        "prompt": prompt[:MAX_PROMPT],
        "prompt_truncated": len(prompt) > MAX_PROMPT,
        "instructions": _files(root, found.get("instructions", [])),
        "skills": _skills(root, found.get("skills", [])),
        "mcp": [str(name) for name in facts.get("sent", []) if isinstance(name, str)],
        "consent": "granted" if consent.get("granted") else "not needed",
        "settings": _settings(s),
    }


def file_name(workspace):
    """runs/<this name> in the data folder holds the folder's runs: the first 16 hex digits of a hash of its path."""
    key = paths.project_key(workspace)
    return hashlib.sha256(key.encode("utf8")).hexdigest()[:16] + ".json"


def run_file(workspace):
    return runtime.DATA / "runs" / file_name(workspace)


def _read(file):
    try:
        if file.stat().st_size > MAX_FILE:
            raise ValueError(DAMAGED)
        data = json.loads(file.read_text(encoding="utf8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError, RecursionError):
        raise ValueError(DAMAGED) from None
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise ValueError(DAMAGED)
    rows = data.get("runs")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _fitting(rows):
    """The newest runs that fit in a file _read still accepts; the oldest go first."""
    kept, used = [], 65536
    for row in rows:
        used += len(json.dumps({"runs": [row]}, indent=2).encode("utf8"))
        if kept and used > MAX_FILE:
            break
        kept.append(row)
    return kept


def _edit(workspace, change):
    file = run_file(workspace)
    file.parent.mkdir(parents=True, exist_ok=True)
    with project_profiles._locked(file.with_suffix(".lock")):
        try:
            rows = _read(file)
        except ValueError:
            rows = []
        rows, result = change(rows)
        rows = _fitting(rows[:MAX_RUNS])
        if rows:
            runtime.write_json(
                file, {"version": VERSION, "project": str(paths.canonical(workspace)), "runs": rows}
            )
        else:
            file.unlink(missing_ok=True)
    return result


def save(run):
    return _edit(run["project"], lambda rows: ([run, *rows], run["id"]))


def note(facts, harness, kind, cli_version=""):
    """Keep a record of one launch and return its id; '' when nothing was recorded. Never raises."""
    if not isinstance(facts, dict) or not isinstance(facts.get("settings"), dict):
        return ""
    try:
        return save(build(facts, harness, kind, cli_version))
    except Exception:
        traceback.print_exc()
        return ""


def runs(workspace):
    return _read(run_file(workspace))


def find(workspace, ident):
    if not isinstance(ident, str) or not re.fullmatch(RUN_ID, ident):
        raise ValueError(GONE)
    run = next((row for row in runs(workspace) if row.get("id") == ident), None)
    if run is None:
        raise ValueError(GONE)
    return run


def label(run):
    source = run.get("source", "")
    if source == "Managed local":
        return "Local model"
    if source == "Native login":
        return "Agent account"
    return run.get("connection") or source


def model_name(run):
    return run.get("weights") or run.get("model") or ""


def rows(workspace, limit=LIST_LIMIT):
    limit = limit if type(limit) is int and 0 < limit <= MAX_RUNS else LIST_LIMIT
    return [
        {
            "id": run.get("id", ""),
            "started": run.get("started", ""),
            "harness": run.get("harness", ""),
            "model": model_name(run),
            "source": label(run),
            "kind": run.get("kind", ""),
        }
        for run in runs(workspace)[:limit]
    ]


def forget(workspace, ident):
    find(workspace, ident)

    def change(rows):
        kept = [row for row in rows if row.get("id") != ident]
        return kept, len(kept) != len(rows)

    return _edit(workspace, change)


def clear(workspace):
    return _edit(workspace, lambda rows: ([], True))


def drift(run, workspace):
    """What changed in the run's instruction files and skills since it started."""
    import project_setup

    root = paths.canonical(workspace)
    out = []
    for kind, key in (("instruction", "instructions"), ("skill", "skills")):
        for row in run.get(key, []):
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                continue
            size, digest = _digest(os.path.normpath(root / row["path"]))
            if size is None:
                out.append({"kind": kind, "path": row["path"], "change": "missing"})
            elif digest != row.get("sha256"):
                out.append({"kind": kind, "path": row["path"], "change": "changed"})
    try:
        now = project_setup.resolve(root, run.get("harness"), run.get("harness_version", ""))
    except (ValueError, OSError):
        return out
    seen = {
        key: {row.get("path") for row in run.get(key, []) if isinstance(row, dict)}
        for key in ("instructions", "skills")
    }
    for row in now.get("instructions", []):
        if row.get("status") not in SKIP and row["path"] not in seen["instructions"]:
            out.append({"kind": "instruction", "path": row["path"], "change": "new"})
    for row in now.get("skills", []):
        if row.get("status") == "read" and row["path"] not in seen["skills"]:
            out.append({"kind": "skill", "path": row["path"], "change": "new"})
    return out


def _size(value):
    if not isinstance(value, int) or value < 0:
        return "size unknown"
    if value < 1024:
        return f"{value} B"
    if value < 1 << 20:
        return f"{value / 1024:.1f} KiB"
    return f"{value / (1 << 20):.1f} MiB"


def _short(digest):
    return "sha256 " + digest[:12] if isinstance(digest, str) and digest else "not readable"


def _when(stamp):
    try:
        return datetime.fromisoformat(stamp).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        return str(stamp or "")


def _fence(text):
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


CHANGES = {
    ("instruction", "changed"): "changed",
    ("instruction", "missing"): "no longer there",
    ("instruction", "new"): "new instruction file the agent now reads",
    ("skill", "changed"): "skill changed",
    ("skill", "missing"): "skill no longer there",
    ("skill", "new"): "new skill the agent now reads",
}


def summary_markdown(run, workspace, changes=None):
    """A copyable Markdown summary of one run, ending with what changed in the folder since."""
    changes = drift(run, workspace) if changes is None else changes
    agent = " ".join(filter(None, (run.get("harness"), run.get("harness_version"))))
    weights = run.get("weights", "")
    if weights and run.get("weights_sha256"):
        weights += f" ({_short(run['weights_sha256'])})"
    context = run.get("context")
    lines = [
        f"# Agent Workbench run · {_when(run.get('started'))}",
        "",
        f"- Folder: {run.get('project', '')}",
        f"- Agent: {agent}",
        f"- Started in: {KINDS.get(run.get('kind'), run.get('kind', ''))}",
        f"- Model source: {run.get('source', '')}",
    ]
    rows = (
        ("Connection", run.get("connection")),
        ("Address", run.get("endpoint")),
        ("Account", run.get("auth_provider")),
        ("Model", run.get("model") if not weights else ""),
        ("Engine", run.get("engine")),
        ("Weights", weights),
        ("Context", f"{context:,} tokens" if isinstance(context, int) else ""),
        ("Reasoning effort", run.get("reasoning")),
        ("Profile", run.get("profile")),
        ("Starter kit", run.get("kit")),
        ("MCP servers", ", ".join(run.get("mcp", [])) or "none"),
        ("Folder programs", "allowed" if run.get("consent") == "granted" else "none needed"),
        ("Workbench", run.get("workbench")),
    )
    lines += [f"- {name}: {value}" for name, value in rows if value]
    lines += ["", "## Instruction files", ""]
    files = run.get("instructions", [])
    lines += [
        f"- {row['path']} · {_size(row.get('bytes'))} · {_short(row.get('sha256'))}"
        + ("" if row.get("status", "read") == "read" else f" · {row['status']}")
        for row in files
    ] or ["- none"]
    if run.get("source") == "Native login":
        lines += ["", NATIVE_FILES]
    lines += ["", "## Skills", ""]
    lines += [
        f"- {row['name']} · {row['path']} · {_short(row.get('sha256'))}"
        for row in run.get("skills", [])
    ] or ["- none"]
    prompt = run.get("prompt", "")
    lines += ["", "## Folder prompt", ""]
    if prompt:
        fence = _fence(prompt)
        lines += [fence + "text", prompt.rstrip("\n"), fence]
        if run.get("prompt_truncated"):
            lines.append(f"(Only the first {MAX_PROMPT} characters were kept.)")
    else:
        lines.append("No prompt.")
    lines += ["", "## Changed since this run", ""]
    lines += [
        f"- {row['path']}: {CHANGES.get((row['kind'], row['change']), row['change'])}"
        for row in changes
    ] or ["- Nothing: the instruction files and skills are as they were."]
    return "\n".join(lines) + "\n"


def summary_json(run):
    return json.dumps(run, indent=2, ensure_ascii=False) + "\n"


def to_profile(run):
    """A profile item for .agent-workbench/profiles.json that repeats this run's choices."""
    item = dict(run.get("settings") or {})
    if run.get("prompt") and not run.get("prompt_truncated"):
        item["prompt"] = run["prompt"]
    if run.get("connection"):
        item["connection_name"] = run["connection"]
    if run.get("weights"):
        item["weight"] = run["weights"]
    return project_profiles.validate_profile(item, "this run")


def default_name(run):
    try:
        stamp = datetime.fromisoformat(run["started"]).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError):
        stamp = datetime.now(timezone.utc)
    return "Run " + stamp.strftime("%Y-%m-%d %H:%M")


def save_profile(workspace, ident, name=""):
    run = find(workspace, ident)
    label = name.strip() if isinstance(name, str) and name.strip() else default_name(run)
    path = project_profiles.save_item(workspace, label, to_profile(run), replace=False)
    return {"name": label, "path": path}


def dispatch(action, p):
    workspace = p.get("workspace")
    if action == "run-list":
        return rows(workspace, p.get("limit", LIST_LIMIT))
    if action == "run-summary":
        run = find(workspace, p.get("id"))
        changes = drift(run, workspace)
        return {
            "markdown": summary_markdown(run, workspace, changes),
            "json": summary_json(run),
            "drift": changes,
            "name": default_name(run),
        }
    if action == "run-save-profile":
        return save_profile(workspace, p.get("id"), p.get("name", ""))
    if action == "run-forget":
        return forget(workspace, p.get("id"))
    if action == "run-clear":
        return clear(workspace)
    raise ValueError("Unknown run action.")
