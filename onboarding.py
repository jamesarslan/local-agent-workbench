"""Guided setup: one call that gathers what the wizard shows, and completion tracking."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as Late
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import shutil
import time

from filelock import FileLock

import catalog
from runtime import DATA, load, owned, settings, write_json

STEPS = ("computer", "model", "agent", "project", "ready")
CHOICES = ("source", "connection_id", "model_entry", "harness", "workspace", "kit")
SOURCE_KINDS = ("attach", "local", "hosted", "ssh")
SECTIONS = (
    "hardware",
    "recommendation",
    "endpoints",
    "managed",
    "library",
    "connections",
    "machines",
    "clis",
    "engine",
    "kit",
)
# Functions other areas provide. Change a name here if the owning module settles on another.
PROVIDERS = {
    "endpoints": ("endpoints", "scan"),
    "engine": ("setup_engines", "info"),
    "kit": ("starter_kits", "detect"),
}
DEADLINE = 8.0
LIBRARY_LIMIT = 50
CHOICE_LIMIT = 1024
SLOW = "This check took too long. Choose Check again."
FAILED = "Workbench could not finish this check. Choose Check again."
UNAVAILABLE = "This part of guided setup is not included in this build."


class Unavailable(LookupError):
    """The module or function behind a section is not part of this build."""


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _file():
    return DATA / "onboarding.json"


def _texts(value, allowed):
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if k in allowed and isinstance(v, str)}


def _read():
    try:
        saved = load(_file(), None)
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(saved, dict) or type(saved.get("schema")) is not int or saved["schema"] != 1:
        return None
    done = saved.get("completed_at")
    return dict(
        schema=1,
        existing_install=saved.get("existing_install") is True,
        completed_at=done if isinstance(done, str) and done else None,
        skipped=saved.get("skipped") is True,
        steps=_texts(saved.get("steps"), STEPS),
        choices=_texts(saved.get("choices"), CHOICES),
    )


def _fresh():
    return dict(
        schema=1,
        existing_install=(DATA / "settings.json").exists() or (DATA / "connections.json").exists(),
        completed_at=None,
        skipped=False,
        steps={},
        choices={},
    )


def _change(edit=None):
    with FileLock(str(_file()) + ".lock", timeout=10):
        saved = _read()
        if saved is None:
            saved = _fresh()
            write_json(_file(), saved)
        if edit:
            edit(saved)
            write_json(_file(), saved)
    return saved | dict(
        open=not (saved["completed_at"] or saved["skipped"] or saved["existing_install"]),
        next_step=next((s for s in STEPS if s not in saved["steps"]), "ready"),
    )


def progress():
    return _change()


def mark(step, choices=None):
    if not isinstance(step, str) or step not in STEPS:
        raise ValueError("Choose one of the setup steps.")
    choices = {} if choices is None else choices
    if (
        not isinstance(choices, dict)
        or any(k not in CHOICES or not isinstance(v, str) or len(v) > CHOICE_LIMIT for k, v in choices.items())
        or choices.get("source", SOURCE_KINDS[0]) not in SOURCE_KINDS
    ):
        raise ValueError("Setup choices must be short text values.")

    def edit(saved):
        saved["steps"][step] = _now()
        saved["choices"].update(choices)

    return _change(edit)


def finish(skipped=False):
    def edit(saved):
        saved.update(skipped=bool(skipped), completed_at=None if skipped else _now())

    return _change(edit)


def reset():
    def edit(saved):
        saved.update(existing_install=False, completed_at=None, skipped=False, steps={})

    return _change(edit)


def _provided(name, *args):
    module, function = PROVIDERS[name]
    try:
        call = getattr(importlib.import_module(module), function)
    except (ImportError, AttributeError):
        raise Unavailable(name) from None
    return call(*args)


def _version(tooling, row):
    if not row.get("installed"):
        return ""
    try:
        return tooling.version(row["path"]) or ""
    except Exception:
        return ""


def _clis():
    import tooling

    names = list(getattr(tooling, "AGENTS", ()))
    if not names:
        import connections

        names = [n for n in connections.HARNESS if n in tooling.TOOLS]
    if not (hasattr(tooling, "inventory") and hasattr(tooling, "version")):
        rows = []
        for name in names:
            if name not in tooling.TOOLS:
                continue
            exe = tooling.TOOLS[name][0]
            path = shutil.which(exe) or ""
            rows.append(dict(name=name, exe=exe, path=path, installed=bool(path), on_path=True, hint="", version=""))
        return rows
    rows = tooling.inventory(names)
    with ThreadPoolExecutor(max_workers=max(1, len(rows)), thread_name_prefix="onboarding-cli") as pool:
        for row, found in zip(rows, pool.map(lambda r: _version(tooling, r), rows)):
            row["version"] = found
    return rows


def _managed():
    proc, rec = owned()
    spec = rec.get("spec") if proc else None
    if not isinstance(spec, dict):
        spec = {}
    model, hosted, context = spec.get("model"), spec.get("vllm_model"), spec.get("context")
    name = Path(model).name if isinstance(model, str) else ""
    hosted = hosted if isinstance(hosted, str) else ""
    return dict(
        running=bool(proc),
        model=hosted if hosted and spec.get("engine") == "vLLM" else name or hosted,
        context=context if type(context) is int else None,
    )


def _library():
    folder = settings()["library"]
    rows = catalog.library_rows(folder)
    return dict(
        folder=folder,
        count=len(rows),
        models=[
            dict(path=path, name=Path(path).name, gib=round(size / catalog.GIB, 2))
            for path, _, size in rows[:LIBRARY_LIMIT]
        ],
    )


def _connections():
    import account_book

    return account_book.available()


def _machines():
    import machines

    return [
        dict(host=host, label=label if isinstance(label, str) and label else host)
        for host, label in machines.labels().items()
    ]


def _kit(workspace):
    if not workspace or not os.path.isdir(workspace):
        return None
    return _provided("kit", str(Path(workspace).resolve()))


def gather(jobs, deadline=DEADLINE):
    """Run {name: callable} at once; each result is {"ok": True, "value"} or {"ok": False, "error"}."""
    if not jobs:
        return {}
    pool = ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="onboarding")
    futures = {name: pool.submit(job) for name, job in jobs.items()}
    end, out = time.monotonic() + deadline, {}
    for name, future in futures.items():
        try:
            out[name] = dict(ok=True, value=future.result(timeout=max(0.0, end - time.monotonic())))
        except Late:
            out[name] = dict(ok=False, error=SLOW)
        except Unavailable:
            out[name] = dict(ok=False, error=UNAVAILABLE)
        except (UnicodeError, json.JSONDecodeError):
            out[name] = dict(ok=False, error=FAILED)
        except ValueError as exc:
            out[name] = dict(ok=False, error=str(exc) or FAILED)
        except Exception:
            out[name] = dict(ok=False, error=FAILED)
    pool.shutdown(wait=False, cancel_futures=True)
    return out


def state(workspace="", sections=None, refresh=False):
    """Everything the guided setup shows, gathered in one call. Never raises for a failed section."""
    workspace = workspace if isinstance(workspace, str) else ""
    if not isinstance(sections, (list, tuple, set, frozenset)):
        sections = None
    if refresh:
        catalog.hardware_info(True)
        if sections is None or "library" in sections:
            catalog.library_rows.cache_clear()
    jobs = dict(
        hardware=catalog.hardware_info,
        recommendation=catalog.recommendation,
        endpoints=lambda: _provided("endpoints"),
        managed=_managed,
        library=_library,
        connections=_connections,
        machines=_machines,
        clis=_clis,
        engine=lambda: _provided("engine"),
        kit=lambda: _kit(workspace),
    )
    saved = progress()
    checks = {name: jobs[name] for name in SECTIONS if sections is None or name in sections}
    if workspace:
        checks["workspace"] = lambda: os.path.isdir(workspace)
    found = gather(checks, DEADLINE)
    exists = found.pop("workspace", {}).get("value") is True
    return dict(
        schema=1,
        steps=list(STEPS),
        progress=saved,
        workspace=dict(path=workspace, exists=exists),
        **found,
    )


def dispatch(action, payload, progress=None, cancel=None):
    payload = payload if isinstance(payload, dict) else {}
    if action == "onboarding-state":
        sections = payload.get("sections")
        return state(
            payload.get("workspace", ""),
            [s for s in sections if isinstance(s, str)] if isinstance(sections, list) else None,
            payload.get("refresh") is True,
        )
    if action == "onboarding-mark":
        return mark(payload.get("step"), payload.get("choices"))
    if action == "onboarding-finish":
        return finish(payload.get("skipped") is True)
    if action == "onboarding-reset":
        return reset()
    raise ValueError("Unknown setup action.")
