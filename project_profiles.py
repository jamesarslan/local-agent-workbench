"""Portable, explicitly applied project profiles and machine-local MCP registrations."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
from urllib.parse import urlsplit

import account_book
from filelock import FileLock, Timeout
import paths as folder_paths
from runtime import DATA, write_json, load, validate_serving

PORTABLE = (
    "engine",
    "channel",
    "context",
    "kv",
    "thinking",
    "vision",
    "image_route",
    "draft",
    "lora",
    "search",
    "firecrawl",
    "desktop",
    "blender",
    "harness",
    "prompt",
    "provider",
    "remote_model",
    "document_skill",
    "auth_provider",
    "plugins",
    "vllm_model",
    "vllm_parser",
    "vllm_fraction",
)
CAPS = ("search", "firecrawl", "blender", "desktop")
RECIPE = (
    "harness",
    "provider",
    "connection_id",
    "auth_provider",
    "preset",
    "remote_model",
    "image_route",
    "plugins",
    *CAPS,
)
FOLDER_SCOPE = (
    "harness",
    "provider",
    "remote_model",
    "auth_provider",
    "image_route",
    "plugins",
    *CAPS,
    "prompt",
)
NAME = r"[A-Za-z0-9_-]{1,48}"
MAX_FOLDERS = 500
MAX_BYTES = 1 << 20
MAX_WRITTEN = 200
LOCK_WAIT = 10
PROFILES = ".agent-workbench/profiles.json"
LABELS = {
    "harness": "Agent",
    "provider": "Model source",
    "connection": "Connection",
    "remote_model": "Model",
    "model": "Local weights",
    "context": "Context",
    "engine": "Engine",
    "channel": "Engine release",
    "kv": "KV cache",
    "thinking": "Reasoning effort",
    "vision": "Vision",
    "image_route": "Reference images",
    "draft": "Draft model",
    "lora": "LoRA adapter",
    "search": "Web search",
    "firecrawl": "Page crawling",
    "desktop": "Windows desktop control",
    "blender": "Blender tools",
    "plugins": "MCP plugins",
    "prompt": "Prompt for this folder",
    "document_skill": "CSV skill",
    "auth_provider": "Account provider",
    "vllm_model": "vLLM checkpoint",
    "vllm_parser": "vLLM tool parser",
    "vllm_fraction": "vLLM GPU fraction",
    "connection_name": "Connection name",
    "weight": "Local weights",
    "default": "Folder default",
}
DEFAULT_NAME = "Folder default"
# Record keys that only say a notice was dismissed; a record with nothing else remembers no choices.
NOTICE_ONLY = ("path", "updated", "default_notice")
CHOICES = {
    "channel": ("Tested", "Updated", "Unsloth"),
    "kv": ("auto", "fp16", "fp8"),
    "thinking": ("off", "minimal", "low", "medium", "high", "xhigh", "max"),
    "image_route": ("auto", "selected", "pareto", "manual"),
}
FLAGS = (
    "vision",
    "draft",
    "lora",
    "search",
    "firecrawl",
    "desktop",
    "blender",
    "document_skill",
)
PATTERNS = {
    "remote_model": r"[A-Za-z0-9._:/@+~-]{1,200}",
    "auth_provider": r"[a-z0-9][a-z0-9-]{0,39}",
    "vllm_model": r"(?=.{1,200}\Z)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
    "vllm_parser": r"[A-Za-z0-9_.][A-Za-z0-9_.-]{0,39}",
}
MCP_TYPES = ("stdio", "http", "sse", "streamable-http", "streamableHttp")
LOOPBACK = ("localhost", "127.0.0.1", "::1")
REGISTER_AGAIN = "Fix the file and register it again."
REOPEN = "then reopen Project profiles."
BUSY = "Workbench is still saving from another window. Wait a moment, then try again."
NO_WRITE = (
    f"Workbench could not write {PROFILES} in this folder. "
    "Check the folder's permissions, then try again."
)


def _windows():
    return os.name == "nt"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _plain(value, limit):
    return (
        isinstance(value, str)
        and len(value) <= limit
        and not any(ord(c) < 32 for c in value)
    )


def _text(value, limit=200):
    return value if _plain(value, limit) else ""


def _quote(name):
    name = str(name)
    return repr(name if len(name) <= 80 else name[:77] + "...")


@contextmanager
def _locked(file):
    lock = FileLock(str(file), timeout=LOCK_WAIT)
    try:
        lock.acquire()
    except Timeout:
        raise ValueError(BUSY) from None
    try:
        yield
    finally:
        lock.release()


def _read_capped(path):
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
        with open(fd, "rb", closefd=False) as stream:
            return stream.read(MAX_BYTES + 1)
    finally:
        os.close(fd)


def profile_file(workspace):
    return folder_paths.inside(
        folder_paths.canonical(workspace), ".agent-workbench", "profiles.json"
    )


def _drop_old_lock(path):
    """Remove the empty profiles.json.lock that 0.4.0 left beside profiles.json."""
    old = path.with_name(path.name + ".lock")
    try:
        info = os.lstat(old)
        if stat.S_ISREG(info.st_mode) and info.st_size == 0:
            os.unlink(old)
    except OSError:
        pass


def _profile_lock(path):
    """The lock for one profiles.json. It lives in the data folder, so the project
    folder never shows a stray lock file."""
    key = hashlib.sha256(folder_paths.fold(str(path)).encode("utf8")).hexdigest()[:16]
    return DATA / "locks" / f"profiles-{key}.lock"


@contextmanager
def _editing(path, create=False):
    lock = _profile_lock(path)
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        if create:
            path.parent.mkdir(exist_ok=True)
        with _locked(lock):
            _drop_old_lock(path)
            yield
    except OSError:
        raise ValueError(NO_WRITE) from None
    except RecursionError:
        raise ValueError(
            f"{PROFILES} is nested too deeply to save. Fix or delete the file, {REOPEN}"
        ) from None


def _read_profiles(path):
    try:
        raw = _read_capped(path)
    except FileNotFoundError:
        return {}
    except OSError:
        raise ValueError(
            f"{PROFILES} could not be read. Check its permissions, {REOPEN}"
        ) from None
    if len(raw) > MAX_BYTES:
        raise ValueError(
            f"{PROFILES} is larger than 1 MB. Remove what you do not need from it, {REOPEN}"
        )
    try:
        rows = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, RecursionError) as error:
        line = getattr(error, "lineno", 1)
        raise ValueError(
            f"{PROFILES} is not valid JSON near line {line}. Fix or delete the file, {REOPEN}"
        ) from None
    if not isinstance(rows, dict):
        raise ValueError(
            f"{PROFILES} must hold named profiles. Fix or delete the file, {REOPEN}"
        )
    for name, item in rows.items():
        if not 1 <= len(name) <= 80 or not isinstance(item, dict):
            raise ValueError(
                f"The profile {_quote(name)} in {PROFILES} is damaged. Remove that entry, {REOPEN}"
            )
    return rows


def load_profiles(workspace):
    return _read_profiles(profile_file(workspace))


def project_names(workspace):
    return sorted(load_profiles(workspace))


def _field(key, value):
    if key in FLAGS:
        ok = type(value) is bool
    elif key == "context":
        ok = type(value) is int and 1024 <= value <= 1048576
    elif not isinstance(value, str):
        ok = False
    elif key in CHOICES:
        ok = value in CHOICES[key]
    elif key == "engine":
        import studio

        ok = value in studio.MODES
    elif key == "harness":
        import connections

        ok = value in connections.HARNESS
    elif key == "provider":
        import connections

        ok = value in connections.PROVIDERS
    elif key == "prompt":
        value = value.replace("\r\n", "\n")
        ok = len(value) <= 8000 and not any(
            ord(c) < 32 and c not in "\n\t" or c == "\x7f" for c in value
        )
    elif key in PATTERNS:
        ok = not value or (
            re.fullmatch(PATTERNS[key], value) is not None
            and not (key == "remote_model" and value.startswith("-"))
        )
    elif key == "plugins":
        parts = [part for part in value.split(",") if part]
        ok = len(set(parts)) == len(parts) and all(
            re.fullmatch(NAME, part) and part not in CAPS for part in parts
        )
        value = ",".join(parts)
    elif key == "vllm_fraction":
        ok = (
            re.fullmatch(r"0\.[0-9]{1,3}", value) is not None
            and 0.03 <= float(value) <= 0.98
        )
    elif key == "connection_name":
        ok = _plain(value, 90)
    elif key == "weight":
        ok = (
            _plain(value, 255)
            and not any(c in value for c in "/\\")
            and value not in (".", "..")
        )
    else:
        ok = False
    if not ok:
        raise ValueError(key)
    return value


def validate_profile(item, name=""):
    if not isinstance(item, dict):
        raise ValueError(
            f"The profile {_quote(name)} in {PROFILES} is damaged. Remove that entry, {REOPEN}"
        )
    out = {}
    for key in (*PORTABLE, "connection_name", "weight"):
        if key not in item:
            continue
        value = item[key]
        try:
            out[key] = _field(key, value)
        except ValueError:
            if (
                key in ("harness", "provider")
                and isinstance(value, str)
                and 0 < len(value) <= 40
                and value.isprintable()
            ):
                raise ValueError(
                    f"The profile {_quote(name)} uses {value}, which this version of Agent Workbench "
                    "does not offer. Save the profile again with a supported choice."
                ) from None
            raise ValueError(
                f"The profile {_quote(name)} has a value Workbench does not accept for {LABELS[key]}. "
                f"Save the profile again from Launch, or fix {PROFILES}."
            ) from None
    if "default" in item and type(item["default"]) is not bool:
        raise ValueError(
            f"The profile {_quote(name)} has a value Workbench does not accept for {LABELS['default']}. "
            f"Use true or false in {PROFILES}, or choose Make default in Project profiles."
        )
    return out


def _default_of(rows):
    """The one profile marked "default": true, or ""; a clear sentence when the file marks more than one."""
    for name, item in rows.items():
        if "default" in item and type(item["default"]) is not bool:
            validate_profile({"default": item["default"]}, name)
    marked = [name for name, item in rows.items() if item.get("default") is True]
    if len(marked) > 1:
        shown = " and ".join(_quote(name) for name in marked[:2])
        more = f" and {len(marked) - 2} more" if len(marked) > 2 else ""
        raise ValueError(
            f"{PROFILES} marks more than one profile as the folder default: {shown}{more}. "
            "Choose Make default on the one to keep in Project profiles."
        )
    return marked[0] if marked else ""


def folder_default(workspace):
    return _default_of(load_profiles(workspace))


def project_list(workspace):
    """Profile names with the folder default; a damaged default is reported, not raised, so the list still shows."""
    rows = load_profiles(workspace)
    try:
        default, problem = _default_of(rows), ""
    except ValueError as error:
        default, problem = "", str(error)
    return {"names": sorted(rows), "default": default, "problem": problem}


def _marked(item, on):
    rest = {k: v for k, v in item.items() if k != "default"}
    return {"default": True, **rest} if on else rest


def set_default(workspace, name):
    path = profile_file(workspace)
    if not path.is_file():
        raise ValueError("This profile no longer exists. Pick another profile.")
    with _editing(path):
        rows = _read_profiles(path)
        if not isinstance(name, str) or name not in rows:
            raise ValueError("This profile no longer exists. Pick another profile.")
        write_json(path, {key: _marked(item, key == name) for key, item in rows.items()})
    _seen_here(workspace, name)
    return project_list(workspace)


def clear_default(workspace):
    path = profile_file(workspace)
    if not path.is_file():
        return project_list(workspace)
    with _editing(path):
        rows = _read_profiles(path)
        if any("default" in item for item in rows.values()):
            write_json(path, {key: _marked(item, False) for key, item in rows.items()})
    return project_list(workspace)


def _profile_name(name):
    name = name.strip() if isinstance(name, str) else ""
    if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise ValueError("Give this profile a name of 1–80 characters.")
    return name


def save_item(workspace, name, item, replace=True, default=False):
    """Write one checked profile into the folder's profiles.json; returns the file's path."""
    name = _profile_name(name)
    item = validate_profile({k: v for k, v in item.items() if k != "default"}, name)
    path = profile_file(workspace)
    with _editing(path, create=True):
        profiles = _read_profiles(path)
        if not replace and name in profiles:
            raise ValueError(
                f"This folder already has a profile named {name}. Choose another name."
            )
        # Replacing the folder default keeps it the default; saving as the default unmarks the others.
        keep = profiles.get(name, {}).get("default") is True
        if default:
            profiles = {key: _marked(row, False) for key, row in profiles.items()}
        profiles[name] = _marked(item, default or keep)
        write_json(path, profiles)
    return str(path)


def save_project(s, name, default=False):
    name = _profile_name(name)
    connection = (
        next(
            (
                row
                for row in account_book.available()
                if row["connection_id"] == s.get("connection_id")
                and row["provider"] == s.get("provider")
            ),
            None,
        )
        if s.get("connection_id")
        else None
    )
    used = launch_settings(s)
    item = {
        **{k: used[k] for k in PORTABLE if k in used},
        **({"connection_name": connection["name"]} if connection else {}),
        "weight": Path(s["model"]).name,
    }
    path = save_item(s["workspace"], name, item, default=default)
    if default:
        _seen_here(s["workspace"], name)
    return path


def _resolve(s, item, paths):
    blockers, needs, notes = [], {}, []
    current = s.get("model", "")
    weight = item.get("weight", "")
    found = (
        next(
            (
                p
                for p in [current, *paths]
                if Path(p).name == weight and os.path.isfile(p)
            ),
            None,
        )
        if weight
        else None
    )
    if (
        weight
        and found is None
        and item.get("provider") == "Managed local"
        and item.get("engine") not in ("Custom", "vLLM")
    ):
        blockers.append(
            f"Add {weight} to your model library before applying this profile."
        )
    out = (
        s
        | {k: item[k] for k in PORTABLE if k in item and k != "prompt"}
        | {"model": current if found is None else found}
    )
    if out.get("desktop") and not _windows():
        out["desktop"] = False
        notes.append(
            "Windows desktop control is not available on this computer and stays off."
        )
    provider = out.get("provider", "Managed local")
    name = item.get("connection_name")
    out["connection_id"] = ""
    if name or provider == "Attached endpoint":
        matches = [
            r
            for r in account_book.available()
            if r["provider"] == provider and (not name or r["name"] == name)
        ]
        if len(matches) == 1:
            out.update(
                connection_id=matches[0]["connection_id"],
                endpoint=matches[0]["endpoint"],
            )
        elif matches and name:
            blockers.append(
                f"More than one {provider} connection is named {name}. "
                "Rename one in Connections, then apply this profile."
            )
        elif matches:
            blockers.append(
                f"This profile does not say which {provider} connection to use. "
                "Save it again from Launch with the connection selected."
            )
        else:
            blockers.append(
                f"Add {_article(provider)} {provider} connection named {name} in Connections, "
                "then apply this profile."
                if name
                else f"Add {_article(provider)} {provider} connection in Connections, then apply this profile."
            )
            needs = {"connection": {"provider": provider, "name": name or ""}}
    elif provider not in ("Managed local", "Native login"):
        out["connection_id"] = "legacy-" + provider
    wanted = plugin_ids(out)
    known = registry() if wanted else {}
    missing = [n for n in wanted if n not in known]
    if missing:
        blockers.append(
            f"Register these MCP plugins in Plugins first: {', '.join(missing)}."
        )
    if provider == "Managed local":
        try:
            validate_serving(out)
        except ValueError as error:
            blockers.append(str(error))
    return out, blockers, needs, notes


def _article(word):
    return "an" if word[:1].upper() in "AEIOU" else "a"


def _connection_label(rows, ident):
    if not ident:
        return ""
    row = next((r for r in rows if r.get("connection_id") == ident), None)
    if row:
        return _text(row.get("name"))
    return _text(ident.removeprefix("legacy-")) if ident.startswith("legacy-") else ""


def _review(s, name, paths):
    raw = load_profiles(s["workspace"]).get(name)
    if raw is None:
        raise ValueError("This profile no longer exists. Pick another profile.")
    item = validate_profile(raw, name)
    out, blockers, needs, notes = _resolve(s, item, paths)
    changes = []

    def row(field, before, after):
        if before != after:
            changes.append(
                {
                    "field": field,
                    "label": LABELS[field],
                    "from": before,
                    "to": after,
                    "scope": "folder"
                    if field in FOLDER_SCOPE or field == "connection"
                    else "global",
                }
            )

    for key in PORTABLE:
        if key == "prompt":
            row(key, launch_settings(s).get(key), item.get(key, s.get(key)))
        elif key in item:
            row(key, s.get(key), out[key])
    row("model", Path(s.get("model", "")).name, Path(out["model"]).name)
    before, after = s.get("connection_id") or "", out["connection_id"]
    if before != after:
        rows = account_book.available()
        changes.append(
            {
                "field": "connection",
                "label": LABELS["connection"],
                "from": _connection_label(rows, before),
                "to": _connection_label(rows, after),
                "scope": "folder",
            }
        )
    shown = {change["field"] for change in changes}
    alias = {"weight": "model", "connection_name": "connection"}
    # Marking a profile as the folder default changes nothing it applies, so it does not void a review.
    reviewed = {k: v for k, v in raw.items() if k != "default"}
    preview = {
        "name": name,
        "digest": hashlib.sha256(
            json.dumps([name, reviewed], sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest(),
        "changes": changes,
        "unchanged": sum(alias.get(key, key) not in shown for key in item),
        "notes": notes,
        "blockers": blockers,
        "needs": needs or None,
    }
    return item, out, preview


def preview_project(s, name, paths):
    return _review(s, name, paths)[2]


def restore_project(s, name, paths):
    _, out, preview = _review(s, name, paths)
    if preview["blockers"]:
        raise ValueError(preview["blockers"][0])
    return out


def apply_project(s, name, paths, confirm):
    item, out, preview = _review(s, name, paths)
    if not confirm:
        raise ValueError("Review what this profile changes, then confirm to apply it.")
    if confirm != preview["digest"]:
        raise ValueError(
            "This profile changed after you reviewed it. Review it again, then apply it."
        )
    if preview["blockers"]:
        raise ValueError(preview["blockers"][0])
    values = out | {"preset": ""}

    def change(record):
        _note_recipe(record, values)
        record["profile"] = name
        if "prompt" in item and item["prompt"] != s.get("prompt"):
            record["prompt"] = {"text": item["prompt"], "source": name}
        else:
            record.pop("prompt", None)

    _update_folder(out["workspace"], change)
    return out


def edit_profile(workspace, name, op, new_name=""):
    path = profile_file(workspace)
    if not path.is_file():
        raise ValueError("Profile no longer exists.")
    with _editing(path):
        rows = _read_profiles(path)
        if name not in rows:
            raise ValueError("Profile no longer exists.")
        if op == "rename":
            label = new_name.strip() if isinstance(new_name, str) else ""
            if not label or not _plain(label, 80) or label in rows:
                raise ValueError("Enter a new, unused profile name in the name field.")
            rows[label] = rows[name]
        elif op != "remove":
            raise ValueError("Unknown profile operation.")
        del rows[name]
        write_json(path, rows)
    return True


def _read_store():
    empty = {"version": 1, "folders": {}}
    try:
        data = json.loads((DATA / "projects.json").read_text(encoding="utf8"))
    except FileNotFoundError:
        return empty, False
    except (OSError, ValueError, RecursionError):
        return empty, True
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or not isinstance(data.get("folders"), dict)
    ):
        return empty, True
    data["folders"] = {
        key: record for key, record in data["folders"].items() if isinstance(record, dict)
    }
    return data, False


def _store():
    return _read_store()[0]


def _stamp(record):
    return _text(record.get("updated"), 40)


def _prune(folders):
    extra = len(folders) - MAX_FOLDERS
    if extra > 0:
        oldest = sorted(
            enumerate(folders), key=lambda pair: (_stamp(folders[pair[1]]), pair[0])
        )
        for _, key in oldest[:extra]:
            del folders[key]


def _edit_store(change):
    file = DATA / "projects.json"
    with _locked(DATA / "projects.lock"):
        data, damaged = _read_store()
        before = json.dumps(data, sort_keys=True)
        result = change(data["folders"])
        _prune(data["folders"])
        if json.dumps(data, sort_keys=True) != before:
            bad = file.with_name("projects.json.bad")
            if damaged and not bad.exists():
                try:
                    shutil.copyfile(file, bad)
                except OSError:
                    raise ValueError(
                        "Workbench could not read projects.json in its data folder. "
                        "Check that file's permissions, then try again."
                    ) from None
            write_json(file, data)
    return result


def _place(workspace):
    root = folder_paths.canonical(workspace)
    return folder_paths.fold(str(root)), root


def _update_folder(workspace, change):
    key, root = _place(workspace)

    def edit(folders):
        record = {"path": "", "updated": ""} | folders.pop(key, {})
        change(record)
        record.update(path=str(root), updated=_now())
        folders[key] = record
        return record

    return _edit_store(edit)


def _drop(workspace, field):
    key, root = _place(workspace)

    def edit(folders):
        record = folders.get(key)
        if record is None or field not in record:
            return False
        del record[field]
        record.update(path=str(root), updated=_now())
        return True

    return _edit_store(edit)


def _record(workspace):
    key, root = _place(workspace)
    return key, root, _store()["folders"].get(key)


def _recipe_of(record):
    recipe = record.get("recipe")
    return (
        {k: v for k, v in recipe.items() if k in RECIPE}
        if isinstance(recipe, dict)
        else {}
    )


def _clean_recipe(values):
    clean, stale = {}, []
    for key in RECIPE:
        if key not in values:
            continue
        value = values[key]
        try:
            if key in ("connection_id", "preset"):
                if not _plain(value, 120):
                    raise ValueError(key)
            elif key == "plugins":
                if not isinstance(value, str):
                    raise ValueError(key)
                parts = list(dict.fromkeys(p for p in value.split(",") if p))
                known = registry() if parts else {}
                keep = [p for p in parts if p in known and p not in CAPS]
                stale += [
                    "plugin:" + "".join(c for c in p[:48] if c.isprintable())
                    for p in parts
                    if p not in keep
                ]
                value = ",".join(keep)
            else:
                value = _field(key, value)
        except ValueError:
            stale.append(key)
            continue
        clean[key] = value
    if clean.get("desktop") and not _windows():
        clean["desktop"] = False
    return clean, stale


def _model_label(values):
    preset = values.get("preset")
    if isinstance(preset, str) and preset:
        label = preset
    elif values.get("provider", "Managed local") == "Managed local":
        model = values.get("model")
        label = Path(model).name if isinstance(model, str) else ""
    else:
        label = values.get("remote_model")
    return _text(label)


def _note_recipe(record, values):
    record["recipe"] = _recipe_of(record) | _clean_recipe(values)[0]
    if any(k in values for k in ("preset", "provider", "model", "remote_model")):
        record["model_label"] = _model_label(values)


def _prompt(record):
    prompt = record.get("prompt")
    if isinstance(prompt, dict) and isinstance(prompt.get("text"), str):
        return {"text": prompt["text"], "source": _text(prompt.get("source"), 80)}
    return None


def _switches(record):
    found = record.get("project_mcp")
    return (
        {
            name: on
            for name, on in found.items()
            if re.fullmatch(NAME, name) and type(on) is bool
        }
        if isinstance(found, dict)
        else {}
    )


def _hashes(found):
    return (
        {
            rel: digest
            for rel, digest in found.items()
            if _plain(rel, 400)
            and isinstance(digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", digest)
        }
        if isinstance(found, dict)
        else {}
    )


def folder_view(workspace, strict=True):
    view = {
        "key": "",
        "path": "",
        "known": False,
        "recipe": {},
        "stale": [],
        "model_label": "",
        "kit": "",
        "profile": "",
        "prompt": None,
        "project_mcp": {},
        "updated": "",
        "consent_granted": "",
        "saved_default": "",
        "offer_default": False,
    }
    try:
        key, root, record = _record(workspace)
    except ValueError:
        if strict:
            raise
        return view | {
            "path": str(workspace) if isinstance(workspace, (str, os.PathLike)) else ""
        }
    saved = _saved_default(root)
    view |= {
        "key": key,
        "path": str(root),
        "saved_default": saved,
        "offer_default": offer_default(record, saved),
    }
    if not _remembers(record):
        return view
    recipe, stale = _clean_recipe(_recipe_of(record))
    return view | {
        "known": True,
        "recipe": recipe,
        "stale": stale,
        "model_label": _text(record.get("model_label")),
        "kit": _text(record.get("kit"), 64),
        "profile": _text(record.get("profile"), 80),
        "prompt": _prompt(record),
        "project_mcp": _switches(record),
        "updated": _stamp(record),
        "consent_granted": _granted(record),
    }


def _granted(record):
    consent = record.get("consent")
    return _text(consent.get("granted"), 40) if isinstance(consent, dict) else ""


def _remembers(record):
    return record is not None and any(key not in NOTICE_ONLY for key in record)


def _saved_default(root):
    try:
        return folder_default(root)
    except (ValueError, OSError, RecursionError):
        return ""


def offer_default(record, saved):
    """Launch offers the folder's saved default only on a computer that remembers nothing for the folder.

    It never applies it: the notice opens the same review a profile always gets. Dismissing it is remembered per
    folder for that default's name, so a different default saved later is offered again.
    """
    if not saved or _remembers(record):
        return False
    return _text((record or {}).get("default_notice"), 80) != saved


def _seen_here(workspace, name):
    """A default marked on this computer is not offered back to it; another computer still gets the offer."""
    try:
        _update_folder(workspace, lambda record: record.update(default_notice=name))
    except (OSError, ValueError):
        pass


def dismiss_default(workspace):
    root = _place(workspace)[1]
    saved = _saved_default(root)
    if saved:
        _update_folder(root, lambda record: record.update(default_notice=saved))
    return folder_view(root)


VAULTS = {
    "darwin": "In the login keychain on this computer.",
    "win32": "In Windows Credential Manager on this computer.",
}


def places(workspace):
    """Where a folder's settings are kept: the files that travel with the folder, then what stays on this computer."""
    import run_log

    root = folder_paths.canonical(workspace)

    def row(ident, label, path, note, travels):
        return {
            "id": ident,
            "label": label,
            "path": str(path) if path else "",
            "exists": bool(path) and os.path.exists(path),
            "note": note,
            "travels": travels,
        }

    return [
        row(
            "profiles",
            "Folder profiles and default",
            root / PROFILES,
            "Plain JSON that travels with the folder. Safe to commit: it holds no keys.",
            True,
        ),
        row(
            "project",
            "Project setup",
            root / ".agent-workbench" / "project.json",
            "This folder's MCP servers. Travels with the folder.",
            True,
        ),
        row(
            "instructions",
            "Agent instructions",
            root / "AGENTS.md",
            "A project file the agents read. Travels with the folder.",
            True,
        ),
        row(
            "skills",
            "Skills",
            root / ".agents" / "skills",
            "Project files the agents load. Travel with the folder.",
            True,
        ),
        row(
            "memory",
            "Last-used choices",
            DATA / "projects.json",
            "Stays on this computer. Holds connection ids and local paths.",
            False,
        ),
        row(
            "connections",
            "Connections",
            DATA / "connections.json",
            "Stays on this computer. Holds no keys.",
            False,
        ),
        row(
            "runs",
            "Run summaries",
            DATA / "runs" / run_log.file_name(root),
            "Stays on this computer. Holds no keys, but has folder paths, file names and the folder prompt.",
            False,
        ),
        row(
            "keys",
            "API keys",
            "",
            VAULTS.get(sys.platform, "In the system credential vault on this computer."),
            False,
        ),
    ]


def remember(workspace, values):
    _update_folder(workspace, lambda record: _note_recipe(record, values))
    return folder_view(workspace)


def launch_settings(s):
    try:
        prompt = _prompt(_record(s.get("workspace"))[2] or {})
    except (ValueError, OSError):
        return s
    return s | {"prompt": prompt["text"]} if prompt else s


def clear_prompt(workspace):
    _drop(workspace, "prompt")
    return folder_view(workspace)


def forget(workspace):
    keys = []
    try:
        keys.append(folder_paths.project_key(workspace))
    except ValueError:
        pass
    if isinstance(workspace, str):
        keys.append(folder_paths.fold(workspace))

    def edit(folders):
        for key in keys:
            if key in folders:
                del folders[key]
                return True
        return False

    return _edit_store(edit)


def folders():
    import connections

    rows = []
    for key, record in _store()["folders"].items():
        recipe = _recipe_of(record)
        harness, provider = recipe.get("harness"), recipe.get("provider")
        path = _text(record.get("path"), 4096) or key
        rows.append(
            {
                "key": key,
                "path": path,
                "exists": os.path.isdir(path),
                "harness": harness
                if isinstance(harness, str) and harness in connections.HARNESS
                else "",
                "provider": provider
                if isinstance(provider, str) and provider in connections.PROVIDERS
                else "",
                "model_label": _text(record.get("model_label")),
                "kit": _text(record.get("kit"), 64),
                "profile": _text(record.get("profile"), 80),
                "updated": _stamp(record),
            }
        )
    order = sorted(
        enumerate(rows), key=lambda pair: (pair[1]["updated"], pair[0]), reverse=True
    )
    return [row for _, row in order]


def set_kit(workspace, kit_id):
    if not isinstance(kit_id, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9-]{0,31}", kit_id
    ):
        raise ValueError("Choose a starter kit from the list.")
    _update_folder(workspace, lambda record: record.update(kit=kit_id))


def set_project_mcp(workspace, name, enabled):
    if (
        not isinstance(name, str)
        or not re.fullmatch(NAME, name)
        or type(enabled) is not bool
    ):
        raise ValueError("Choose a project server, then turn it on or off.")
    return _switches(
        _update_folder(
            workspace,
            lambda record: record.update(
                project_mcp=_switches(record) | {name: enabled}
            ),
        )
    )


def project_mcp(workspace):
    return _switches(_record(workspace)[2] or {})


def note_written(workspace, hashes):
    fresh = _hashes(hashes)
    if not fresh:
        return

    def change(record):
        merged = {
            rel: digest
            for rel, digest in _hashes(record.get("written")).items()
            if rel not in fresh
        } | fresh
        record["written"] = dict(list(merged.items())[-MAX_WRITTEN:])

    _update_folder(workspace, change)


def written(workspace):
    return _hashes((_record(workspace)[2] or {}).get("written"))


def consent_record(workspace):
    found = (_record(workspace)[2] or {}).get("consent")
    return found if isinstance(found, dict) else None


def grant_consent(workspace, digest, items):
    if (
        not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or not isinstance(items, list)
        or len(items) > 64
        or not all(isinstance(item, str) for item in items)
    ):
        raise ValueError(
            "Review what this folder can run again in Project setup, then allow it."
        )
    consent = {"digest": digest, "granted": _now(), "items": list(items)}
    _update_folder(workspace, lambda record: record.update(consent=consent))
    return consent


def revoke_consent(workspace):
    return _drop(workspace, "consent")


def registry():
    try:
        found = load(DATA / "plugins.json", {})
    except (OSError, ValueError, RecursionError):
        found = None
    if not isinstance(found, dict):
        raise ValueError(
            f"The plugin registry at {DATA / 'plugins.json'} could not be read; "
            "move that file aside, then register your MCP files again in Plugins."
        )
    return found


def plugin_ids(s):
    listed = s.get("plugins", "")
    if not isinstance(listed, str):
        return []
    return [name for name in listed.split(",") if name]


def load_manifest(file):
    again = "Choose an MCP configuration file."
    try:
        raw = _read_capped(Path(file))
    except OSError:
        raise ValueError(f"That file could not be read. {again}") from None
    if len(raw) > MAX_BYTES:
        raise ValueError(f"That file is larger than 1 MB. {again}")
    try:
        manifest = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, RecursionError):
        manifest = None
    if not isinstance(manifest, dict):
        raise ValueError(f"That file is not valid JSON. {again}")
    return manifest


def _strings(value, kind):
    if kind is list:
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    return isinstance(value, dict) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    )


def _reachable(url):
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return False
    return bool(
        host
        and not parts.username
        and not parts.password
        and (parts.scheme == "https" or parts.scheme == "http" and host in LOOPBACK)
    )


def _check_server(name, conf):
    if (
        not isinstance(name, str)
        or not re.fullmatch(NAME, name)
        or name in CAPS
        or not isinstance(conf, dict)
    ):
        raise ValueError(
            "Choose unique server names using letters, numbers, dashes or underscores."
        )
    command, url = conf.get("command"), conf.get("url")
    if command is not None:
        if (
            not isinstance(command, str)
            or not command.strip()
            or len(command) > 1024
            or any(c in command for c in "\0\r\n")
        ):
            raise ValueError(f"{name} needs a command or a URL. {REGISTER_AGAIN}")
    elif not isinstance(url, str) or not url.strip():
        raise ValueError(f"{name} needs a command or a URL. {REGISTER_AGAIN}")
    elif not _reachable(url):
        raise ValueError(
            f"{name}: use an https:// URL, or http:// only for this computer. {REGISTER_AGAIN}"
        )
    shapes = {
        "args": lambda v: _strings(v, list),
        "env": lambda v: _strings(v, dict),
        "headers": lambda v: _strings(v, dict),
        "cwd": lambda v: isinstance(v, str),
        "type": lambda v: isinstance(v, str) and v in MCP_TYPES,
    }
    for field, fits in shapes.items():
        if field in conf and not fits(conf[field]):
            raise ValueError(
                f"{name}: {field} has the wrong shape. {REGISTER_AGAIN}"
            )


def register(manifest, replace=False):
    servers = manifest.get("mcpServers") if isinstance(manifest, dict) else None
    if not isinstance(servers, dict) or not servers:
        raise ValueError("Choose an MCP JSON file with a nonempty mcpServers object.")
    if len(servers) > 64:
        raise ValueError("Choose an MCP JSON file with at most 64 servers.")
    for name, conf in servers.items():
        _check_server(name, conf)
    with _locked(DATA / "plugins.lock"):
        existing = registry()
        changed = [k for k in servers if k in existing and existing[k] != servers[k]]
        if changed and not replace:
            raise ValueError(
                f"A plugin named {changed[0]} is already registered. "
                "Use Replace to update it, or rename the new entry."
            )
        write_json(DATA / "plugins.json", existing | servers)
    for name in changed:
        _scrub(name)
    return list(servers)


def _without(text, name):
    if not isinstance(text, str) or name not in text.split(","):
        return text, False
    return ",".join(part for part in text.split(",") if part and part != name), True


def _strip_settings(name):
    file = DATA / "settings.json"
    try:
        saved = json.loads(file.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return False
    if not isinstance(saved, dict):
        return False
    named = saved.get("named_profiles")
    hit = False
    for row in [saved, *(named.values() if isinstance(named, dict) else [])]:
        if isinstance(row, dict) and "plugins" in row:
            row["plugins"], found = _without(row["plugins"], name)
            hit = hit or found
    if hit:
        write_json(file, saved)
    return hit


def _strip_folders(name):
    def edit(folders):
        count = 0
        for record in folders.values():
            recipe = record.get("recipe")
            if isinstance(recipe, dict) and "plugins" in recipe:
                recipe["plugins"], found = _without(recipe["plugins"], name)
                count += found
        return count

    return _edit_store(edit)


def _scrub(name):
    profiles, clients = Path.home() / ".omp/profiles", DATA / "clients"
    files = [
        *profiles.glob("workbench-*/agent/mcp.json"),
        *profiles.glob("local-agent-workbench*/agent/mcp.json"),
        *clients.glob("*/pi/mcp.json"),
        *clients.glob("*/claude-mcp/mcp.json"),
    ]
    count = 0
    for file in files:
        try:
            saved = json.loads(file.read_text(encoding="utf8"))
            servers = saved.get("mcpServers") if isinstance(saved, dict) else None
            if isinstance(servers, dict) and name in servers:
                del servers[name]
                write_json(file, saved)
                count += 1
        except (OSError, ValueError):
            continue
    return count


def unregister(name):
    with _locked(DATA / "plugins.lock"):
        existing = registry()
        if not isinstance(name, str) or name not in existing:
            raise ValueError(
                "That plugin is no longer registered. Refresh the Plugins page."
            )
        del existing[name]
        write_json(DATA / "plugins.json", existing)
    return {
        "removed": name,
        "settings": _strip_settings(name),
        "folders": _strip_folders(name),
        "files": _scrub(name),
    }


def _public_url(url):
    try:
        parts = urlsplit(url)
        host, port = parts.hostname or "", parts.port
    except ValueError:
        return ""
    if not parts.scheme or not host:
        return ""
    host = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{host}{f':{port}' if port else ''}{parts.path}"


def describe():
    rows = []
    for name, conf in sorted(registry().items()):
        if not isinstance(conf, dict):
            continue
        command = conf.get("command") if isinstance(conf.get("command"), str) else ""
        url = conf.get("url") if isinstance(conf.get("url"), str) else ""
        args, env, headers = conf.get("args"), conf.get("env"), conf.get("headers")
        rows.append(
            {
                "name": name,
                "transport": "stdio"
                if command
                else "sse"
                if conf.get("type") == "sse"
                else "http",
                "command": command,
                "args": [a for a in args if isinstance(a, str)]
                if command and isinstance(args, list)
                else [],
                "env_keys": sorted(map(str, env)) if isinstance(env, dict) else [],
                "url": "" if command else _public_url(url),
                "header_keys": sorted(map(str, headers))
                if isinstance(headers, dict)
                else [],
            }
        )
    return rows


def dispatch(action, p):
    if action == "folder-get":
        return folder_view(p["workspace"])
    if action == "folder-list":
        return folders()
    if action == "folder-forget":
        return forget(p["workspace"])
    if action == "folder-clear-prompt":
        return clear_prompt(p["workspace"])
    if action == "folder-default-dismiss":
        return dismiss_default(p["workspace"])
    if action == "folder-places":
        return places(p["workspace"])
    if action == "plugin-describe":
        return describe()
    if action == "plugin-remove":
        return unregister(p["name"])
    if action == "plugin-update":
        return register(load_manifest(Path(p["file"])), replace=True)
    raise ValueError("Unknown project action.")
