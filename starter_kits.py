"""Starter kits: load the bundled kits, suggest one for a folder, and plan what a kit writes."""

from collections import Counter
import fnmatch
import functools
import itertools
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import unicodedata

import paths
import project_profiles
import project_setup

KITS_DIR = Path(__file__).resolve().parent / "kits"
ORDER = (
    "general",
    "web",
    "python-data",
    "3d",
    "research-writing",
    "devops",
    "gamedev",
    "blank",
)

KIT_ID = r"[a-z0-9][a-z0-9-]{0,31}"
SKILL_NAME = r"workbench-[a-z0-9]+(-[a-z0-9]+)*"
KIT_KEYS = (
    "schema",
    "id",
    "label",
    "summary",
    "works_with",
    "version",
    "priority",
    "detect",
    "skills",
    "mcp",
)
LAUNCH_KEYS = project_setup.SERVER_KEYS
SERVER_NEEDS = (
    "name",
    "label",
    "publisher",
    "source",
    "license",
    "version",
    "checked",
    "transport",
    "default",
    "network",
    "warning",
)
SERVER_KEYS = (*SERVER_NEEDS, *LAUNCH_KEYS, "requires", "when", "platforms")
BUILTIN_NEEDS = ("builtin", "label", "default", "network", "warning")
BUILTIN_KEYS = (*BUILTIN_NEEDS, "when", "requires")
CHECKED = ("registry", "launched")
TOOLS = {"node": "npx", "uv": "uvx", "docker": "docker"}
PLATFORMS = ("darwin", "linux", "win32")
RULES = ("file", "dir", "glob", "package_dep", "mostly", "all", "any", "none")
RULE_EXTRAS = {"package_dep": ("min_major",), "mostly": ("share", "min_files")}
FRONT_KEYS = ("name", "description", "license", "compatibility", "metadata")
COMMANDS = ("install", "dev", "test", "lint", "build", "typecheck")
PLACEHOLDER = r"\{\{(project|cmd:(?:" + "|".join(COMMANDS) + r"))\}\}"
NO_COMMAND = "TODO: add the command"
LOCKS = (
    ("pnpm", ("pnpm-lock.yaml",)),
    ("yarn", ("yarn.lock",)),
    ("bun", ("bun.lock", "bun.lockb")),
)
FILE_KINDS = {
    ".md": "Markdown",
    ".tex": "LaTeX",
    ".bib": "BibTeX",
    ".docx": "Word",
    ".typ": "Typst",
    ".qmd": "Quarto",
    ".rst": "reStructuredText",
}
SKIP_DIRS = frozenset(
    [
        "node_modules",
        ".git",
        ".venv",
        "venv",
        "Library",
        "Temp",
        "__pycache__",
        "dist",
        "build",
        "target",
        ".next",
        ".agent-workbench",
    ]
)
DOT_DIRS = (".github", ".obsidian")
SCAN_DEPTH = 2
SCAN_CAP = 5000
MAX_KIT = 256 * 1024
MAX_PACKAGE = 1 << 20
MAX_KIT_FILE = 32 * 1024
MAX_RULES = 24
MAX_NESTING = 3
MAX_SKILLS = 5
MAX_SERVERS = 12
MAX_SKILL_FILES = 8
MAX_BODY_LINES = 500

NO_KIT = "Choose a starter kit from the list."
BAD_CHOICE = "Choose the kit's skills and tools from the lists shown."


class _Flaw(Exception):
    """A packaging mistake inside a kit folder; load_kit reports it with the kit's path."""


def _need(ok, what):
    if not ok:
        raise _Flaw(what)


def _windows():
    return os.name == "nt"


def _platform():
    return sys.platform


def _plain(value, limit):
    """Display text on one line: no control, format, unassigned or line-break characters."""
    kinds = ((c, unicodedata.category(c)) for c in str(value))
    return "".join(c for c, kind in kinds if kind[0] != "C" and kind not in ("Zl", "Zp"))[:limit]


def _peek(path, cap):
    """The first cap + 1 bytes of a regular file, or None when there is no such file to read."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with open(fd, "rb", closefd=False) as stream:
            return stream.read(cap + 1)
    except OSError:
        return None
    finally:
        os.close(fd)


def _kit_text(path, cap, label):
    raw = _peek(path, cap)
    if raw is None:
        return None
    _need(len(raw) <= cap, f"{label} is larger than {cap // 1024} KiB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _Flaw(f"{label} is not UTF-8 text") from None
    _need("\0" not in text, f"{label} is not UTF-8 text")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _line(value, limit, what, empty=False):
    _need(
        isinstance(value, str)
        and len(value) <= limit
        and value.isprintable()
        and (empty or bool(value.strip())),
        f"{what} must be {'' if empty else 'non-empty '}text of at most {limit} characters",
    )


def _keys(found, allowed, needed, what):
    _need(isinstance(found, dict), f"{what} must be an object")
    for key in sorted(map(str, found)):
        _need(key in allowed, f"{what} has the unknown key {_plain(key, 60)}")
    for key in needed:
        _need(key in found, f"{what} needs the key {key}")


def _inside_folder(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 200
        and value.isprintable()
        and not value.startswith("/")
        and ".." not in value
        and "\\" not in value
        and ":" not in value
    )


def _whole(value, least):
    return type(value) is int and value >= least


def _check_rule(rule, depth=1):
    _need(depth <= MAX_NESTING, f"a rule nests more than {MAX_NESTING} deep")
    _need(isinstance(rule, dict), "a rule must be an object")
    kind = next((name for name in RULES if name in rule), None)
    _need(kind is not None, "a rule needs one of " + ", ".join(RULES))
    _keys(rule, (kind, *RULE_EXTRAS.get(kind, ())), (), f"the {kind} rule")
    value = rule[kind]
    if kind in ("file", "dir", "glob"):
        _need(
            _inside_folder(value),
            f"the {kind} rule needs a path inside the folder, with no .. and no leading /",
        )
    elif kind == "package_dep":
        _need(
            isinstance(value, list)
            and 0 < len(value) <= 32
            and all(
                isinstance(name, str) and name.isprintable() and 0 < len(name) <= 100
                for name in value
            ),
            "the package_dep rule needs a list of package names",
        )
        _need(
            "min_major" not in rule or _whole(rule["min_major"], 0),
            "min_major must be a whole number",
        )
    elif kind == "mostly":
        _need(
            isinstance(value, list)
            and 0 < len(value) <= 16
            and all(
                isinstance(suffix, str) and re.fullmatch(r"\.[A-Za-z0-9]{1,16}", suffix)
                for suffix in value
            ),
            "the mostly rule needs a list of file suffixes such as .md",
        )
        share = rule.get("share")
        _need(
            type(share) in (int, float) and 0 < share <= 1 and _whole(rule.get("min_files"), 1),
            "the mostly rule needs a share between 0 and 1 and a min_files count",
        )
    else:
        _need(
            isinstance(value, list) and 0 < len(value) <= MAX_RULES,
            f"the {kind} rule needs a list of rules",
        )
        for inner in value:
            _check_rule(inner, depth + 1)


def _launch(entry):
    return {key: entry[key] for key in LAUNCH_KEYS if key in entry}


def _check_entry(entry):
    _need(isinstance(entry, dict), "each mcp entry must be an object")
    builtin = "builtin" in entry
    what = "a built-in mcp entry" if builtin else "an mcp entry"
    if builtin:
        _keys(entry, BUILTIN_KEYS, BUILTIN_NEEDS, what)
        _need(
            isinstance(entry["builtin"], str) and entry["builtin"] in project_profiles.CAPS,
            "builtin must be one of " + ", ".join(project_profiles.CAPS),
        )
        what = f"built-in {entry['builtin']}"
    else:
        _keys(entry, SERVER_KEYS, SERVER_NEEDS, what)
        try:
            project_setup.clean_server(entry["name"], _launch(entry))
        except ValueError as error:
            raise _Flaw("server " + str(error).split(" Fix ")[0].rstrip(".")) from None
        what = f"server {entry['name']}"
        _line(entry["publisher"], 60, f"{what}: publisher")
        _line(entry["license"], 40, f"{what}: license")
        _line(entry["version"], 40, f"{what}: version")
        source = entry["source"]
        _need(
            isinstance(source, str)
            and len(source) <= 200
            and re.fullmatch(r"https://[A-Za-z0-9._~:/?#@!$&'()*+,;=%-]+", source),
            f"{what}: source must be an https:// address of at most 200 characters",
        )
        _need(
            isinstance(entry["checked"], str) and entry["checked"] in CHECKED,
            f"{what}: checked must be registry or launched",
        )
        platforms = entry.get("platforms", list(PLATFORMS))
        _need(
            isinstance(platforms, list)
            and platforms
            and all(isinstance(item, str) and item in PLATFORMS for item in platforms)
            and len(set(platforms)) == len(platforms),
            f"{what}: platforms must list some of " + ", ".join(PLATFORMS),
        )
    _line(entry["label"], 60, f"{what}: label")
    _line(entry["network"], 200, f"{what}: network")
    _line(entry["warning"], 200, f"{what}: warning", empty=True)
    _need(type(entry["default"]) is bool, f"{what}: default must be true or false")
    requires = entry.setdefault("requires", [])
    _need(
        isinstance(requires, list)
        and all(isinstance(tool, str) and tool in TOOLS for tool in requires)
        and len(set(requires)) == len(requires),
        f"{what}: requires may list " + ", ".join(TOOLS),
    )
    if "when" in entry:
        _check_rule(entry["when"])


def _ident(entry):
    return "builtin:" + entry["builtin"] if "builtin" in entry else entry["name"]


def _check_kit(kit_id, data):
    _keys(data, KIT_KEYS, KIT_KEYS, "kit.json")
    _need(type(data["schema"]) is int and data["schema"] == 1, "schema must be 1")
    _need(data["id"] == kit_id, "id must equal the folder name")
    _line(data["label"], 60, "label")
    _line(data["summary"], 200, "summary")
    _line(data["works_with"], 120, "works_with", empty=True)
    _need(
        isinstance(data["version"], str) and re.fullmatch(r"\d+\.\d+\.\d+", data["version"]),
        "version must look like 0.4.0",
    )
    _need(
        type(data["priority"]) is int and 0 <= data["priority"] <= 100,
        "priority must be a whole number from 0 to 100",
    )
    detect = data["detect"]
    _need(
        isinstance(detect, dict)
        and sorted(detect) == ["strong", "weak"]
        and all(isinstance(rules, list) and len(rules) <= MAX_RULES for rules in detect.values()),
        f"detect needs strong and weak lists of at most {MAX_RULES} rules each",
    )
    for rule in detect["strong"] + detect["weak"]:
        _check_rule(rule)
    skills = data["skills"]
    _need(
        isinstance(skills, list) and len(skills) <= MAX_SKILLS,
        f"skills must be a list of at most {MAX_SKILLS} entries",
    )
    for skill in skills:
        _keys(skill, ("name", "when"), ("name",), "a skills entry")
        name = skill["name"]
        _need(
            isinstance(name, str) and len(name) <= 64 and re.fullmatch(SKILL_NAME, name),
            "a skill name must look like workbench-some-task, at most 64 characters",
        )
        if "when" in skill:
            _check_rule(skill["when"])
    names = [skill["name"] for skill in skills]
    _need(len(set(names)) == len(names), "skills lists the same skill twice")
    servers = data["mcp"]
    _need(
        isinstance(servers, list) and len(servers) <= MAX_SERVERS,
        f"mcp must be a list of at most {MAX_SERVERS} entries",
    )
    for entry in servers:
        _check_entry(entry)
    idents = [_ident(entry) for entry in servers]
    _need(len(set(idents)) == len(idents), "mcp lists the same entry twice")


def _check_template(text):
    _need(text.strip(), "AGENTS.md is empty")
    left = re.sub(PLACEHOLDER, "", text)
    _need(
        "{{" not in left and "}}" not in left,
        "AGENTS.md uses a placeholder Workbench does not fill",
    )
    issues = project_setup.portable_issues(text)
    if issues:
        raise _Flaw("AGENTS.md is not portable: " + issues[0].rstrip("."))


def _entries(folder):
    try:
        with os.scandir(folder) as listing:
            return sorted(
                (entry for entry in listing if not entry.name.startswith(".")),
                key=lambda entry: entry.name,
            )
    except OSError:
        return []


def _skill_paths(folder, where):
    """Relative paths of a skill's files: beside SKILL.md, and one folder level below it."""
    found = []
    for entry in _entries(folder):
        if entry.is_dir(follow_symlinks=False):
            for inner in _entries(folder / entry.name):
                _need(
                    inner.is_file(follow_symlinks=False),
                    f"{where}/{entry.name}/{inner.name} must be a plain file, one folder level deep",
                )
                found.append(f"{entry.name}/{inner.name}")
        else:
            _need(
                entry.is_file(follow_symlinks=False),
                f"{where}/{entry.name} must be a plain file",
            )
            found.append(entry.name)
    return found


def _front_matter(text, where):
    lines = text.split("\n")
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    _need(
        lines[0].strip() == "---" and end is not None,
        f"{where}/SKILL.md must start with front matter between two --- lines",
    )
    import yaml

    try:
        meta = yaml.safe_load("\n".join(lines[1:end]))
    except (yaml.YAMLError, ValueError, RecursionError):
        raise _Flaw(f"{where}/SKILL.md has front matter that is not valid YAML") from None
    _need(isinstance(meta, dict), f"{where}/SKILL.md has front matter that is not valid YAML")
    return meta, len("\n".join(lines[end + 1 :]).strip("\n").split("\n"))


def _skill_description(name, text, where):
    meta, body = _front_matter(text, where)
    for key in sorted(map(str, meta)):
        _need(
            key in FRONT_KEYS,
            f"{where}/SKILL.md uses the front-matter key {_plain(key, 60)}, "
            "which not every agent accepts",
        )
    _need(meta.get("name") == name, f"{where}/SKILL.md must be named {name}, like its folder")
    description = meta.get("description")
    _need(
        isinstance(description, str)
        and 0 < len(description.strip()) <= 200
        and description.strip().isprintable(),
        f"{where}/SKILL.md needs a description of at most 200 characters",
    )
    _need(meta.get("license") == "MIT", f"{where}/SKILL.md must say license: MIT")
    compatibility = meta.get("compatibility")
    _need(
        "compatibility" not in meta
        or (isinstance(compatibility, str) and 0 < len(compatibility) <= 500),
        f"{where}/SKILL.md has a compatibility line that is not short text",
    )
    metadata = meta.get("metadata", {})
    _need(
        isinstance(metadata, dict)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in metadata.items()),
        f"{where}/SKILL.md metadata must map text to text",
    )
    _need(
        body <= MAX_BODY_LINES,
        f"{where}/SKILL.md has more than {MAX_BODY_LINES} lines after its front matter",
    )
    return description.strip()


def _load_skill(folder, name):
    """(files by relative path, description), or None while the skill is not on disk yet."""
    where = f"skills/{name}"
    if not os.path.lexists(folder):
        return None
    _need(folder.is_dir() and not folder.is_symlink(), f"{where} must be a real folder")
    listed = _skill_paths(folder, where)
    _need("SKILL.md" in listed, f"{where} has no SKILL.md")
    _need(
        len(listed) <= MAX_SKILL_FILES,
        f"{where} holds more than {MAX_SKILL_FILES} files",
    )
    files = {}
    for rel in listed:
        try:
            file = paths.inside(folder, rel)
        except ValueError:
            raise _Flaw(
                f"{where}/{_plain(rel, 120)} has a name that cannot be written into a project"
            ) from None
        text = _kit_text(file, MAX_KIT_FILE, f"{where}/{rel}")
        _need(text is not None, f"{where}/{rel} cannot be read")
        files[rel] = text
    return files, _skill_description(name, files["SKILL.md"], where)


def _load(kit_id, folder):
    raw = _kit_text(folder / "kit.json", MAX_KIT, "kit.json")
    if raw is None:
        raise ValueError(NO_KIT)
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        raise _Flaw("kit.json is not valid JSON") from None
    _check_kit(kit_id, data)
    template = _kit_text(folder / "AGENTS.md", MAX_KIT_FILE, "AGENTS.md")
    if template is not None:
        _check_template(template)
    names = [skill["name"] for skill in data["skills"]]
    for entry in _entries(folder / "skills"):
        _need(
            entry.name in names or not entry.is_dir(),
            f"skills/{_plain(entry.name, 80)} is not listed in kit.json",
        )
    files, meta = {}, {}
    for name in names:
        found = _load_skill(folder / "skills" / name, name)
        if found is not None:
            files[name], meta[name] = found[0], {"description": found[1]}
    return data | {"agents_md": template or "", "skill_files": files, "skill_meta": meta}


def _root(root):
    return Path(root) if root is not None else KITS_DIR


def load_kit(kit_id, root=None):
    if not isinstance(kit_id, str) or not re.fullmatch(KIT_ID, kit_id):
        raise ValueError(NO_KIT)
    try:
        return _load(kit_id, _root(root) / kit_id)
    except _Flaw as flaw:
        raise ValueError(f"kits/{kit_id}: {flaw}.") from None


@functools.lru_cache(maxsize=8)
def _catalog(root):
    """Every kit under root that loads: the kits of ORDER first, then the rest by id."""
    try:
        with os.scandir(root) as listing:
            names = {entry.name for entry in listing if entry.is_dir()}
    except OSError:
        names = set()
    ordered = [name for name in ORDER if name in names] + sorted(
        name for name in names if name not in ORDER and re.fullmatch(KIT_ID, name)
    )
    found = []
    for name in ordered:
        try:
            found.append(load_kit(name, root))
        except ValueError:
            continue
    return tuple(found)


def _visible(entry):
    return _platform() in entry.get("platforms", PLATFORMS)


def _receives(server):
    names = [*server["env_from"], *server["header_env"].values(), server["bearer_env"]]
    return list(dict.fromkeys(name for name in names if name))


def _row(entry):
    row = {
        "label": entry["label"],
        "requires": list(entry["requires"]),
        "default": entry["default"],
        "network": entry["network"],
        "warning": entry["warning"],
    }
    if "builtin" in entry:
        return row | {
            "builtin": entry["builtin"],
            "publisher": "Agent Workbench",
            "license": "",
            "version": "",
            "checked": "",
            "transport": "builtin",
            "summary": "",
            "source": "",
            "needs": [],
        }
    server = project_setup.clean_server(entry["name"], _launch(entry))
    return row | {
        "name": entry["name"],
        "publisher": entry["publisher"],
        "license": entry["license"],
        "version": entry["version"],
        "checked": entry["checked"],
        "transport": entry["transport"],
        "summary": project_setup.summary(server),
        "source": entry["source"],
        "needs": _receives(server),
    }


def _offered_skills(kit):
    return [skill for skill in kit["skills"] if skill["name"] in kit["skill_files"]]


def _skill_row(kit, skill):
    name = skill["name"]
    return {"name": name, "description": kit["skill_meta"][name]["description"]}


def _public(kit):
    return {
        "id": kit["id"],
        "label": kit["label"],
        "summary": kit["summary"],
        "works_with": kit["works_with"],
        "version": kit["version"],
        "agents_md_lines": len(kit["agents_md"].splitlines()),
        "skills": [_skill_row(kit, skill) for skill in _offered_skills(kit)],
        "mcp": [_row(entry) for entry in kit["mcp"] if _visible(entry)],
    }


def kits(root=None):
    return [_public(kit) for kit in _catalog(str(_root(root)))]


def _scanned_dir(name):
    return name not in SKIP_DIRS and (not name.startswith(".") or name in DOT_DIRS)


def _package(root):
    raw = _peek(root / "package.json", MAX_PACKAGE)
    if raw is None or len(raw) > MAX_PACKAGE:
        return None
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _scan(root):
    """Names of the files in a folder, its children and grandchildren, without following links."""
    files, seen, truncated = [], 0, False
    level = [(root, "")]
    for depth in range(SCAN_DEPTH + 1):
        deeper = []
        for folder, prefix in level:
            room = SCAN_CAP - seen
            if room <= 0:
                truncated = True
                break
            try:
                with os.scandir(folder) as listing:
                    entries = list(itertools.islice(listing, room + 1))
            except OSError:
                continue
            if len(entries) > room:
                truncated = True
                entries = entries[:room]
            seen += len(entries)
            for entry in sorted(entries, key=lambda entry: entry.name):
                try:
                    if entry.is_symlink() or entry.is_junction():
                        continue
                    if entry.is_file(follow_symlinks=False):
                        files.append((entry.name, prefix + entry.name))
                    elif (
                        depth < SCAN_DEPTH
                        and entry.is_dir(follow_symlinks=False)
                        and _scanned_dir(entry.name)
                    ):
                        deeper.append((Path(entry.path), f"{prefix}{entry.name}/"))
                except OSError:
                    continue
        level = deeper
    return {
        "root": root,
        "files": files,
        "scanned": seen,
        "truncated": truncated,
        "package": _package(root),
    }


def _depends(package, names, least):
    if package is None:
        return None
    listed = {}
    for field in ("devDependencies", "dependencies"):
        if isinstance(package.get(field), dict):
            listed |= package[field]
    for name in names:
        if name not in listed:
            continue
        if least is None:
            return name
        major = re.search(r"\d{1,9}", listed[name]) if isinstance(listed[name], str) else None
        if major and int(major.group()) >= least:
            return name
    return None


def _mostly(rule, files):
    wanted = {suffix.lower() for suffix in rule["mostly"]}
    suffixes = [os.path.splitext(name)[1].lower() for name, _ in files]
    hits = Counter(suffix for suffix in suffixes if suffix in wanted)
    total = sum(hits.values())
    if len(suffixes) < rule["min_files"] or total < rule["share"] * len(suffixes):
        return None
    kinds = [FILE_KINDS.get(suffix, suffix) for suffix, _ in hits.most_common(2)]
    return f"mostly {' and '.join(kinds)} files"


def _evidence(rule, scan):
    """A short sentence when the rule holds for the scanned folder ('' for a bare none), else None."""
    root, files = scan["root"], scan["files"]
    if "file" in rule:
        held = os.path.isfile(root.joinpath(*rule["file"].split("/")))
        return f"found {rule['file']}" if held else None
    if "dir" in rule:
        held = os.path.isdir(root.joinpath(*rule["dir"].split("/")))
        return f"found the {rule['dir']} folder" if held else None
    if "glob" in rule:
        pattern = rule["glob"]
        part = 1 if "/" in pattern else 0
        count = sum(fnmatch.fnmatchcase(item[part], pattern) for item in files)
        if not count:
            return None
        return f"{count} file{' matches' if count == 1 else 's match'} {pattern}"
    if "package_dep" in rule:
        name = _depends(scan["package"], rule["package_dep"], rule.get("min_major"))
        return f"package.json depends on {name}" if name else None
    if "mostly" in rule:
        return _mostly(rule, files)
    kind = next(name for name in ("all", "any", "none") if name in rule)
    found = [_evidence(inner, scan) for inner in rule[kind]]
    if kind == "all":
        return None if None in found else next((text for text in found if text), "")
    if kind == "any":
        return next((text for text in found if text is not None), None)
    return "" if all(text is None for text in found) else None


def _holds(item, scan):
    return "when" not in item or _evidence(item["when"], scan) is not None


def _matched(rules, scan):
    return [text for text in (_evidence(rule, scan) for rule in rules) if text is not None]


def detect(workspace, root=None):
    scan = _scan(paths.canonical(workspace))
    scores, evidence = [], {}
    for kit in _catalog(str(_root(root))):
        strong = _matched(kit["detect"]["strong"], scan)
        weak = _matched(kit["detect"]["weak"], scan)
        if strong or weak:
            evidence[kit["id"]] = [text or "its combined rule holds" for text in strong + weak]
        scores.append((kit["id"], kit["priority"], len(strong), len(weak)))
    ranked = sorted((row for row in scores if row[2]), key=lambda row: row[1:], reverse=True)
    if not ranked:
        ranked = sorted(
            (row for row in scores if row[3] >= 2),
            key=lambda row: (row[3], row[1]),
            reverse=True,
        )
    ids = [row[0] for row in ranked]
    return {
        "suggested": ids[0] if ids else "blank",
        "alternatives": ids[1:3],
        "evidence": evidence,
        "scanned": scan["scanned"],
        "truncated": scan["truncated"],
    }


def _manager(root):
    for name, locks in LOCKS:
        if any(os.path.exists(root / lock) for lock in locks):
            return name
    return "npm"


def fill(template, workspace):
    root = paths.canonical(workspace)
    package = _package(root)
    scripts = package.get("scripts") if package else None
    scripts = scripts if isinstance(scripts, dict) else {}
    manager = _manager(root)

    def value(match):
        key = match.group(1)
        if key == "project":
            return _plain(root.name, 80) or "Project"
        script = key.removeprefix("cmd:")
        if package is None:
            return NO_COMMAND
        if script == "install":
            return f"{manager} install"
        return f"{manager} run {script}" if isinstance(scripts.get(script), str) else NO_COMMAND

    return re.sub(PLACEHOLDER, value, template)


def _chosen(asked, offered, preset):
    if asked is None:
        return set(preset)
    if not isinstance(asked, list) or not all(isinstance(item, str) for item in asked):
        raise ValueError(BAD_CHOICE)
    for item in asked:
        if item not in offered:
            raise ValueError(f"That kit does not include {_plain(item, 80)}.")
    return set(asked)


def _prepare(workspace, kit_id, servers, skills):
    """The kit, the folder, the selected skill names and entry ids, and the write request."""
    kit = load_kit(kit_id)
    root = paths.canonical(workspace)
    scan = _scan(root)
    offered = _offered_skills(kit)
    names = _chosen(
        skills,
        [skill["name"] for skill in offered],
        [skill["name"] for skill in offered if _holds(skill, scan)],
    )
    shown = [entry for entry in kit["mcp"] if _visible(entry)]
    ids = _chosen(
        servers,
        [_ident(entry) for entry in shown],
        [_ident(entry) for entry in shown if entry["default"] and _holds(entry, scan)],
    )
    if not _windows():
        ids.discard("builtin:desktop")
    files = {"AGENTS.md": fill(kit["agents_md"], root)} if kit["agents_md"] else {}
    for name in sorted(names):
        for rel, text in kit["skill_files"][name].items():
            files[f".agents/skills/{name}/{rel}"] = text
    want = {
        "files": files,
        "overwrite": [],
        "pointer": True,
        "mcp_set": {
            entry["name"]: _launch(entry) | {"enabled": True}
            for entry in shown
            if entry.get("name") in ids
        },
        "mcp_remove": [],
        "mcp_keep_existing": True,
    }
    return kit, root, names, ids, want


def _missing(entry):
    return [tool for tool in entry["requires"] if shutil.which(TOOLS[tool]) is None]


def _builtins(kit, ids):
    return {
        entry["builtin"]: True
        for entry in kit["mcp"]
        if "builtin" in entry and _ident(entry) in ids
    }


def _kept(root, want):
    """A warning for each selected server that the folder already defines in another way."""
    present = project_setup.read_project(root)["servers"]
    notes = []
    for name in sorted(want["mcp_set"]):
        mine = present.get(name)
        ours = project_setup.clean_server(name, want["mcp_set"][name])
        if mine is not None and any(mine[key] != ours[key] for key in LAUNCH_KEYS):
            notes.append(
                f"This folder already defines {name} as {project_setup.summary(mine)}, "
                "which is not the kit's version. Your definition is kept and is the one "
                f"that runs. Remove {name} from {project_setup.PROJECT_FILE} first "
                "if you want the kit's."
            )
    return notes


def plan(workspace, kit_id, harness, servers=None, skills=None, cli_version=""):
    kit, root, names, ids, want = _prepare(workspace, kit_id, servers, skills)
    found = project_setup.plan(root, harness, want, cli_version)
    return found | {
        "warnings": found["warnings"] + _kept(root, want),
        "kit": kit_id,
        "agents_md": want["files"].get("AGENTS.md", ""),
        "skills": [
            _skill_row(kit, skill) | {"selected": skill["name"] in names}
            for skill in _offered_skills(kit)
        ],
        "servers": [
            _row(entry) | {"selected": _ident(entry) in ids, "missing": _missing(entry)}
            for entry in kit["mcp"]
            if _visible(entry)
        ],
        "builtins": _builtins(kit, ids),
    }


def apply(
    workspace,
    kit_id,
    harness,
    plan_id,
    confirmed,
    servers=None,
    skills=None,
    cli_version="",
):
    kit, root, _, ids, want = _prepare(workspace, kit_id, servers, skills)
    result = project_setup.apply(root, harness, want, plan_id, confirmed, cli_version)
    project_profiles.set_kit(root, kit_id)
    switched = _builtins(kit, ids)
    if switched:
        project_profiles.remember(root, switched)
    present = project_setup.read_project(root)["servers"]
    for name in sorted(want["mcp_set"]):
        if name in present:
            project_profiles.set_project_mcp(root, name, True)
    return result | {"kit": kit_id}


def dispatch(action, p):
    workspace, version = p.get("workspace"), p.get("cli_version", "")
    if action == "kit-list":
        return kits()
    if action == "kit-detect":
        return detect(workspace)
    if action == "kit-preview":
        return plan(
            workspace, p.get("kit"), p.get("harness"), p.get("servers"), p.get("skills"), version
        )
    if action == "kit-apply":
        return apply(
            workspace,
            p.get("kit"),
            p.get("harness"),
            p.get("plan"),
            p.get("confirmed", []),
            p.get("servers"),
            p.get("skills"),
            version,
        )
    raise ValueError("Unknown kit action.")
