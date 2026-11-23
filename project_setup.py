"""What each agent CLI reads in a project folder, the project MCP list, and confirmed writes."""

import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import stat
import tomllib
import unicodedata
from urllib.parse import urlsplit

import paths
import project_profiles
from project_profiles import CAPS, NAME
import runtime

RESERVED_ENV = (
    "WORKBENCH_SESSION_KEY",
    "WORKBENCH_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CONFIG_DIR",
    "CODEX_HOME",
    "PI_CODING_AGENT_DIR",
    "PATH",
    "HOME",
    "USERPROFILE",
)
ENV_NAME = r"[A-Z][A-Z0-9_]{0,63}"
PROJECT_FILE = ".agent-workbench/project.json"
HARNESSES = ("OMP", "Pi", "Claude Code", "Codex", "Hermes", "opencode")
CLAUDE_AGENTS_MD = (2, 1, 277)
CODEX_DOC_CAP = 32768
HERMES_CHAR_FLOOR = 20000
LINE_GUIDE = 200

COMMAND = r"[A-Za-z0-9_.+-]{1,64}"
ARG = r"[A-Za-z0-9 _.,:/@=+#~-]{1,300}"
ENV_VALUE = r"[A-Za-z0-9 _.,:/@=+-]{0,200}"
HEADER = r"[A-Za-z0-9_-]{1,64}"
URL_TEXT = r"[A-Za-z0-9._~:/@=+,\[\]-]{1,300}"
BARE_KEY = r"[A-Za-z0-9_-]+"
SECRET_WORDS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "API_KEY",
    "APIKEY",
)
LOOPBACK = ("localhost", "127.0.0.1", "::1")
SERVER_KEYS = (
    "transport",
    "command",
    "args",
    "env",
    "env_from",
    "url",
    "header_env",
    "bearer_env",
)
MAX_SERVERS = 32
MAX_PROJECT = 256 * 1024
MAX_TEXT = 1 << 20
MAX_SKILL = 64 * 1024
MAX_FRONT = 8 * 1024
MAX_FILES = 64
MAX_FILE = 64 * 1024
MAX_DIFF = 20000
DIFF_WORK = 2_000_000
MAX_LEVELS = 32
MAX_SKILLS = 200
HASHED = 262144
VAULT = "Agent Workbench"

BAD_AGENT = "Choose a supported agent."
BAD_PLAN = "That plan holds a file Workbench will not write."
UNREADABLE = (
    f"{PROJECT_FILE} is not a file Workbench can read. "
    "Fix or delete it, then reopen Project setup."
)
OPAQUE = "Could not be read; treat it as able to run programs."
NEEDS_CONSENT = (
    "This folder defines tools that can run programs. "
    "Open Project setup, review them and allow them, then launch again."
)
CODEX_ARGV = (
    "Codex cannot receive {name} because Workbench keeps a registered server's arguments "
    "and working folder off the command line, where a key would be visible; add it in "
    "Project setup as a project server under another name with any key as a variable "
    "instead of an argument, or use another agent."
)
CLAUDE_NAMES = ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")
CLAUDE_RUNS = (
    "hooks",
    "env",
    "apiKeyHelper",
    "statusLine",
    "fileSuggestion",
    "awsAuthRefresh",
    "awsCredentialExport",
    "otelHeadersHelper",
)
OMP_NAMES = (
    ".omp/AGENTS.md",
    ".claude/CLAUDE.md",
    ".agents/AGENTS.md",
    ".agent/AGENTS.md",
    ".gemini/GEMINI.md",
    ".github/copilot-instructions.md",
    "AGENTS.md",
    "CLAUDE.md",
)
OMP_HERE = (".claude/CLAUDE.md", ".gemini/GEMINI.md", ".github/copilot-instructions.md")
PI_NAMES = ("AGENTS.override.md", "AGENTS.md", "AGENTS.MD", "CLAUDE.md", "CLAUDE.MD")
OPENCODE_FILES = (
    "opencode.json",
    "opencode.jsonc",
    ".opencode/opencode.json",
    ".opencode/opencode.jsonc",
)
SCAN = (
    (PROJECT_FILE, "mcp", HARNESSES, "project"),
    (".mcp.json", "mcp", ("Claude Code", "OMP"), "file"),
    ("mcp.json", "mcp", ("OMP",), "mcpServers"),
    (".omp/mcp.json", "mcp", ("OMP",), "file"),
    (".omp/.mcp.json", "mcp", ("OMP",), "file"),
    (".omp/hooks", "code", ("OMP",), "folder"),
    (".omp/tools", "code", ("OMP",), "folder"),
    (".pi/mcp.json", "mcp", ("Pi",), "file"),
    (".pi/settings.json", "settings", ("Pi",), "file"),
    (".pi/extensions", "code", ("Pi",), "folder"),
    (".claude/settings.json", "settings", ("Claude Code",), "runs"),
    (".claude/settings.local.json", "settings", ("Claude Code",), "runs"),
    (".codex/config.toml", "settings", ("Codex", "OMP"), "file"),
    *((name, "settings", ("opencode", "OMP"), "file") for name in OPENCODE_FILES),
    (".gemini/settings.json", "mcp", ("OMP",), "file"),
    (".cursor/mcp.json", "mcp", ("OMP",), "file"),
    (".vscode/mcp.json", "mcp", ("OMP",), "file"),
)
MCP_FILES = {
    "OMP": (
        (
            ".omp/mcp.json",
            ".omp/.mcp.json",
            "mcp.json",
            ".mcp.json",
            ".codex/config.toml",
            ".gemini/settings.json",
            "opencode.json",
            ".cursor/mcp.json",
            ".vscode/mcp.json",
        ),
        "read",
        "OMP starts these without asking.",
    ),
    "Pi": ((".pi/mcp.json",), "needs-trust", "Pi loads these after you trust the project."),
    "Claude Code": ((".mcp.json",), "asks", "Claude Code asks before it uses these."),
    "Codex": (
        (".codex/config.toml",),
        "needs-trust",
        "Codex loads these only for trusted projects.",
    ),
    "Hermes": ((), "", ""),
    "opencode": (OPENCODE_FILES, "read", "opencode starts these without asking."),
}
DELIVERY = {
    "OMP": "Workbench adds these servers to the OMP profile it creates for the session.",
    "Pi": "Workbench writes these servers to the Pi settings folder it creates for the session.",
    "Claude Code": "Workbench passes these servers to Claude Code with --mcp-config when it starts the session.",
    "Codex": "Workbench passes these servers to Codex as configuration overrides when it starts the session.",
    "Hermes": "Hermes reads MCP servers only from its own settings, so Workbench sends none.",
    "opencode": "opencode receives servers through its own configuration.",
}
READABLE = (
    "AGENTS.md",
    "AGENTS.override.md",
    "CLAUDE.md",
    "CLAUDE.local.md",
    ".claude/CLAUDE.md",
    ".omp/AGENTS.md",
    ".hermes.md",
    "HERMES.md",
    PROJECT_FILE,
)
SKILL_FILE = r"(\.agents|\.claude|\.omp|\.pi|\.opencode|\.hermes)/skills/[a-z0-9-]{1,64}/SKILL\.md"
WRITABLE = r"(\.agents|\.claude)/skills/[^/]+/[^/]+(/[^/]+)?"
EXPORTS = {
    "Claude Code": (".mcp.json", "claude"),
    "OMP": (".omp/mcp.json", "omp"),
    "Pi": (".pi/mcp.json", "pi"),
    "Codex": (".codex/config.toml", "toml"),
    "opencode": ("opencode.json", "opencode"),
}


def _clean_text(value, limit):
    """Text for display: no control, format (direction or zero-width) or unassigned characters."""
    return "".join(c for c in str(value) if unicodedata.category(c)[0] != "C")[:limit]


def _sha(text):
    return hashlib.sha256(text.encode("utf8")).hexdigest()


def _unix(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _under(root, rel):
    return root.joinpath(*rel.split("/"))


def _rel(path, root):
    try:
        return os.path.relpath(path, root).replace(os.sep, "/")
    except ValueError:
        return str(path)


def _same(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _peek(path, cap):
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
        with open(fd, "rb", closefd=False) as stream:
            return stream.read(cap + 1)
    finally:
        os.close(fd)


def _head(path, cap=MAX_TEXT):
    try:
        return _peek(path, cap)[:cap]
    except OSError:
        return b""


def _words(path):
    return _head(path).decode("utf8", errors="replace")


def _existing(path):
    try:
        raw = _peek(path, MAX_TEXT)
    except FileNotFoundError:
        return "missing", ""
    except OSError:
        return "opaque", ""
    if len(raw) > MAX_TEXT:
        return "opaque", ""
    try:
        return "text", _unix(raw.decode("utf8"))
    except UnicodeDecodeError:
        return "opaque", ""


def _json_object(path, cap=MAX_TEXT):
    try:
        raw = _peek(path, cap)
    except OSError:
        return None
    if len(raw) > cap:
        return None
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _folder(s):
    try:
        return paths.canonical(s.get("workspace"))
    except ValueError:
        return None


def _neutral(name, origin, transport, **fields):
    return {
        "name": name,
        "origin": origin,
        "transport": transport,
        "command": "",
        "args": [],
        "cwd": "",
        "env": {},
        "env_from": [],
        "url": "",
        "headers": {},
        "header_env": {},
        "bearer_env": "",
    } | fields


def _fail(name, reason):
    shown = _clean_text(name, 48) if isinstance(name, str) else ""
    return ValueError(
        f"{shown or 'This server'}: {reason} Fix {PROJECT_FILE} or remove that server."
    )


def _unset(value):
    return value is None or (isinstance(value, (str, list, dict)) and not value)


def _env_name(value):
    return (
        isinstance(value, str)
        and re.fullmatch(ENV_NAME, value) is not None
        and value not in RESERVED_ENV
    )


def _secret_like(key):
    return key.endswith("_KEY") or any(word in key for word in SECRET_WORDS)


def _project_url(url):
    if not isinstance(url, str) or not re.fullmatch(URL_TEXT, url):
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        parts.port
    except ValueError:
        return False
    return bool(
        (parts.scheme == "https" or parts.scheme == "http" and host in LOOPBACK)
        and (re.fullmatch(r"[A-Za-z0-9.-]+", host) or host == "::1")
        and not parts.username
        and not parts.password
        and not parts.query
        and not parts.fragment
    )


def _stdio_fields(name, raw):
    command = raw.get("command")
    if (
        not isinstance(command, str)
        or not re.fullmatch(COMMAND, command)
        or command.startswith("-")
        or not command.strip(".")
    ):
        raise _fail(
            name, "the command must be a program name such as npx, uvx or docker."
        )
    args = [] if raw.get("args") is None else raw["args"]
    if (
        not isinstance(args, list)
        or len(args) > 32
        or not all(isinstance(a, str) and re.fullmatch(ARG, a) for a in args)
    ):
        raise _fail(
            name, "an argument has characters a shared project file cannot carry."
        )
    env = {} if raw.get("env") is None else raw["env"]
    refused = "an environment entry is not allowed."
    if not isinstance(env, dict) or len(env) > 16:
        raise _fail(name, refused)
    for key, value in env.items():
        if not _env_name(key):
            raise _fail(name, refused)
        if _secret_like(key):
            raise _fail(
                name,
                f"put {key} under env_from so its value stays out of the project file.",
            )
        if not isinstance(value, str) or not re.fullmatch(ENV_VALUE, value):
            raise _fail(name, refused)
    passed = [] if raw.get("env_from") is None else raw["env_from"]
    if (
        not isinstance(passed, list)
        or len(passed) > 16
        or not all(_env_name(item) for item in passed)
        or len(set(passed)) != len(passed)
        or set(passed) & set(env)
    ):
        raise _fail(name, "env_from must list environment variable names.")
    return {
        "command": command,
        "args": list(args),
        "env": dict(env),
        "env_from": list(passed),
    }


def _http_fields(name, raw):
    url = raw.get("url")
    if not _project_url(url):
        raise _fail(
            name,
            "the URL must be https://, or http:// on this computer, with no query string.",
        )
    wrong = "header_env must map a header name to an environment variable name."
    headers = {} if raw.get("header_env") is None else raw["header_env"]
    if (
        not isinstance(headers, dict)
        or len(headers) > 8
        or not all(
            re.fullmatch(HEADER, header) and _env_name(value)
            for header, value in headers.items()
        )
    ):
        raise _fail(name, wrong)
    bearer = "" if raw.get("bearer_env") is None else raw["bearer_env"]
    if bearer != "" and not _env_name(bearer):
        raise _fail(name, wrong)
    return {"url": url, "header_env": dict(headers), "bearer_env": bearer}


def clean_server(name, raw):
    if not isinstance(name, str) or not re.fullmatch(NAME, name) or name in CAPS:
        raise _fail(
            name,
            "the name must use letters, numbers, dashes or underscores "
            "and must not be a built-in tool name.",
        )
    if not isinstance(raw, dict):
        raise _fail(name, "the entry must be an object.")
    transport = raw.get("transport")
    if not isinstance(transport, str) or transport not in ("stdio", "http"):
        raise _fail(name, "the transport must be stdio or http.")
    stdio = transport == "stdio"
    for field in ("url", "header_env", "bearer_env") if stdio else SERVER_KEYS[1:5]:
        if not _unset(raw.get(field)):
            raise _fail(
                name,
                f"{field} does not belong to {'a stdio' if stdio else 'an http'} server.",
            )
    enabled = raw.get("enabled", False)
    if type(enabled) is not bool:
        raise _fail(name, "enabled must be true or false.")
    fields = _stdio_fields(name, raw) if stdio else _http_fields(name, raw)
    return _neutral(name, "project", transport, **fields) | {"enabled": enabled}


def _pairs(value):
    return (
        dict(value)
        if isinstance(value, dict)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items())
        else {}
    )


def from_mcp_json(name, conf, origin):
    if not isinstance(conf, dict):
        return None
    origin = "builtin" if name in CAPS else origin
    command, url = conf.get("command"), conf.get("url")
    if isinstance(command, str) and command:
        args, cwd = conf.get("args"), conf.get("cwd")
        return _neutral(
            name,
            origin,
            "stdio",
            command=command,
            args=list(args)
            if isinstance(args, list) and all(isinstance(a, str) for a in args)
            else [],
            env=_pairs(conf.get("env")),
            cwd=cwd if isinstance(cwd, str) else "",
            enabled=True,
        )
    if isinstance(url, str) and url:
        return _neutral(
            name,
            origin,
            "sse" if conf.get("type") == "sse" else "http",
            url=url,
            headers=_pairs(conf.get("headers")),
            enabled=True,
        )
    return None


def summary(server):
    text = " ".join([server["command"], *server["args"]]) if server["command"] else server["url"]
    return _clean_text(text, 120)


def _load_project(root):
    found = {"path": PROJECT_FILE, "exists": False, "servers": {}, "errors": [], "extra": {}}
    try:
        raw = _peek(_under(root, PROJECT_FILE), MAX_PROJECT)
    except (FileNotFoundError, NotADirectoryError):
        return found, False
    except OSError:
        raw = None
    found["exists"] = True
    data = None
    if raw is not None and len(raw) <= MAX_PROJECT:
        try:
            data = json.loads(raw.decode("utf-8-sig"))
            json.dumps(data, ensure_ascii=False).encode("utf8")
        except (ValueError, RecursionError):
            data = None
    if (
        not isinstance(data, dict)
        or type(data.get("version")) is not int
        or data["version"] != 1
        or not isinstance(data.get("mcp", {}), dict)
    ):
        found["errors"].append(UNREADABLE)
        return found, True
    listed = data.get("mcp", {})
    names = sorted(listed)
    if len(names) > MAX_SERVERS:
        names = names[:MAX_SERVERS]
        found["errors"].append(
            f"{PROJECT_FILE} lists more than {MAX_SERVERS} servers. "
            f"Workbench uses the first {MAX_SERVERS} by name."
        )
    for name in names:
        try:
            found["servers"][name] = clean_server(name, listed[name])
        except ValueError as error:
            found["errors"].append(str(error))
    found["extra"] = {k: v for k, v in data.items() if k not in ("version", "mcp")}
    return found, False


def read_project(workspace):
    return _load_project(paths.canonical(workspace))[0]


def project_text(servers, extra=None):
    mcp = {}
    for name in sorted(servers):
        server = servers[name]
        mcp[name] = {key: server[key] for key in SERVER_KEYS if server.get(key)} | {
            "enabled": bool(server.get("enabled"))
        }
    kept = {k: v for k, v in (extra or {}).items() if k not in ("version", "mcp")}
    return (
        json.dumps({"version": 1} | kept | {"mcp": mcp}, indent=2, ensure_ascii=False)
        + "\n"
    )


def _project_servers(root):
    switches = project_profiles.project_mcp(root)
    found = _load_project(root)[0]["servers"]
    return [
        found[name] | {"enabled": switches.get(name, found[name]["enabled"])}
        for name in sorted(found)
    ]


def project_servers(workspace):
    return _project_servers(paths.canonical(workspace))


def _vault(name):
    try:
        import keyring

        return keyring.get_password(VAULT, "mcp-env:" + name) or ""
    except Exception:
        return ""


def secret_status(names):
    return {
        name: "env" if os.environ.get(name) else "vault" if _vault(name) else "missing"
        for name in names
        if isinstance(name, str)
    }


def _secret_name(name):
    if not _env_name(name):
        raise ValueError("That variable name cannot be used for a project server.")


def store_secret(name, value):
    _secret_name(name)
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 8192
        or any(c in value for c in "\0\r\n")
    ):
        raise ValueError("Enter the value on one line.")
    try:
        import keyring

        if "plaintext" in type(keyring.get_keyring()).__name__.lower():
            raise RuntimeError("plaintext vault")
        keyring.set_password(VAULT, "mcp-env:" + name, value)
    except Exception:
        raise ValueError(
            "This computer has no system credential vault, so Workbench will not save the value. "
            f"Set {name} in your environment before you start Workbench."
        ) from None
    return True


def forget_secret(name):
    _secret_name(name)
    try:
        import keyring

        if keyring.get_password(VAULT, "mcp-env:" + name) is None:
            return False
        keyring.delete_password(VAULT, "mcp-env:" + name)
    except Exception:
        return False
    return True


def _folder_files(folder):
    found = []
    try:
        entries = sorted(os.scandir(folder), key=lambda entry: entry.name)
    except OSError:
        return found
    for entry in entries:
        if len(found) >= MAX_FILES:
            break
        try:
            if entry.is_file():
                found.append(entry.name)
            elif entry.is_dir(follow_symlinks=False):
                inner = sorted(os.scandir(entry.path), key=lambda item: item.name)
                found += [
                    f"{entry.name}/{item.name}" for item in inner if item.is_file()
                ]
        except OSError:
            continue
    return found[:MAX_FILES]


def _file_digest(path):
    try:
        return hashlib.sha256(_peek(path, HASHED)[:HASHED]).hexdigest()
    except OSError:
        return hashlib.sha256(b"unreadable").hexdigest()


def _receives(server):
    return [
        *server["env_from"],
        *server["header_env"].values(),
        *([server["bearer_env"]] if server["bearer_env"] else []),
    ]


def _count(number, noun):
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _server_detail(parts, total):
    if not total:
        return "No servers listed."
    text = f"{_count(total, 'server')}: " + ", ".join(parts)
    return text + (f", and {total - len(parts)} more" if total > len(parts) else "")


def _shown(text, limit):
    text = _clean_text(text, limit + 1)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _project_part(server):
    """One consent line for a project server: its host and what it receives are never cut."""
    needs = _receives(server)
    tail = (f"; receives {', '.join(needs)}" if needs else "") + ")"
    if server["transport"] == "stdio":
        return _shown(f"{server['name']} ({summary(server)}", 80) + tail
    parts = urlsplit(server["url"])
    site = f"{parts.scheme}://{parts.netloc}"
    return f"{server['name']} ({site}{_shown(server['url'][len(site):], 40)}{tail}"


def _named(listed):
    parts = []
    for name in list(listed)[:8]:
        server = from_mcp_json(name, listed[name], "project")
        label = _clean_text(name, 40)
        parts.append(_shown(f"{label} ({summary(server)})" if server else label, 80))
    return _server_detail(parts, len(listed))


def _config_servers(path, rel):
    """Server definitions by name in a harness config file, or None when it cannot be parsed."""
    if rel.endswith(".toml"):
        try:
            raw = _peek(path, MAX_TEXT)
            data = tomllib.loads(raw.decode("utf8")) if len(raw) <= MAX_TEXT else None
        except (OSError, ValueError, RecursionError):
            data = None
        key = "mcp_servers"
    else:
        data = _json_object(path)
        key = (
            "servers"
            if rel == ".vscode/mcp.json"
            else "mcp"
            if rel in OPENCODE_FILES
            else "mcpServers"
        )
    if not isinstance(data, dict):
        return None
    found = data.get(key, {})
    if not isinstance(found, dict):
        return None
    inner = found.get("servers") if key == "mcp" else None
    if isinstance(inner, dict) and "type" not in inner:
        found = inner
    return found


def _scan_item(root, rel, kind, rule):
    """(detail, {path: content hash}) for one listed item, or None when it is not listed."""
    path = _under(root, rel)
    if rule == "folder":
        files = _folder_files(path) if os.path.isdir(path) else []
        if not files:
            return None
        return (
            f"{_count(len(files), 'file')} that this agent runs.",
            {f"{rel}/{sub}": _file_digest(_under(path, sub)) for sub in files},
        )
    if rule == "project":
        found, broken = _load_project(root)
        if not (broken or found["servers"]):
            return None
        parts = [_project_part(server) for server in found["servers"].values()]
        detail = OPAQUE if broken else _server_detail(parts, len(parts))
        return detail, {rel: _file_digest(path)}
    if not os.path.isfile(path):
        return None
    parsed = True
    if rule in ("mcpServers", "runs"):
        data = _json_object(path)
        parsed = data is not None
        keys = CLAUDE_RUNS if rule == "runs" else (rule,)
        if parsed and not any(key in data for key in keys):
            return None
    if kind == "settings":
        detail = (
            "May define hooks, tools or servers that run programs." if parsed else OPAQUE
        )
    else:
        listed = _config_servers(path, rel)
        detail = OPAQUE if listed is None else _named(listed)
    return detail, {rel: _file_digest(path)}


def _consent(root, harness=None):
    items, lines = [], {}
    for rel, kind, readers, rule in SCAN:
        found = _scan_item(root, rel, kind, rule)
        if found is None:
            continue
        lines |= found[1]
        items.append(
            {
                "path": rel,
                "kind": kind,
                "read_by": list(readers),
                "relevant": harness is None or harness in readers or rel == PROJECT_FILE,
                "detail": found[0],
            }
        )
    digest = (
        hashlib.sha256(
            "".join(f"{path}\0{lines[path]}\n" for path in sorted(lines)).encode("utf8")
        ).hexdigest()
        if items
        else ""
    )
    record = project_profiles.consent_record(root)
    granted = bool(digest) and record is not None and record.get("digest") == digest
    status = {
        "needed": not granted and any(item["relevant"] for item in items),
        "granted": granted,
        "digest": digest,
        "items": items,
    }
    return status, lines


def consent_status(workspace, harness=None):
    return _consent(paths.canonical(workspace), harness)[0]


def grant(workspace, digest):
    status = consent_status(workspace)
    if not isinstance(digest, str) or digest != status["digest"]:
        raise ValueError(
            "These files changed while you were reading. Review them again, then allow them."
        )
    if digest:
        project_profiles.grant_consent(
            workspace, digest, [item["path"] for item in status["items"]]
        )
    return True


def revoke(workspace):
    return project_profiles.revoke_consent(workspace)


def project_config_allowed(workspace):
    try:
        return not consent_status(workspace, "OMP")["needed"]
    except ValueError:
        return True


def before_launch(s, harness=None):
    root = _folder(s)
    if root is None:
        return s
    if _consent(root, harness or s.get("harness", "OMP"))[0]["needed"]:
        raise ValueError(NEEDS_CONSENT)
    return project_profiles.launch_settings(s)


def _json_entry(server, dialect, ref):
    typed = {} if dialect == "pi" else {"type": server["transport"]}
    if server["transport"] == "stdio":
        env = server["env"] | {key: ref(key) for key in server["env_from"]}
        return (
            typed
            | {"command": server["command"]}
            | ({"args": list(server["args"])} if server["args"] else {})
            | ({"env": env} if env else {})
            | ({"cwd": server["cwd"]} if server["cwd"] else {})
        )
    headers = (
        server["headers"]
        | {header: ref(name) for header, name in server["header_env"].items()}
        | (
            {"Authorization": "Bearer " + ref(server["bearer_env"])}
            if server["bearer_env"]
            else {}
        )
    )
    return typed | {"url": server["url"]} | ({"headers": headers} if headers else {})


def render_mcp_json(servers, dialect):
    if dialect not in ("claude", "omp", "pi"):
        raise ValueError("Unknown MCP file dialect.")
    entries, skipped = {}, []
    for server in servers:
        if dialect == "pi" and server["transport"] == "sse":
            skipped.append(
                {"name": server["name"], "reason": "Pi does not support SSE servers."}
            )
            continue
        entries[server["name"]] = _json_entry(server, dialect, lambda name: "${" + name + "}")
    return entries, skipped


def _toml(value):
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _plain(texts):
    try:
        "".join(texts).encode("utf8")
    except UnicodeEncodeError:
        return False
    return not any("\0" in text for text in texts)


def _url_secret(url):
    try:
        parts = urlsplit(url)
        return bool(parts.username or parts.password or parts.query)
    except ValueError:
        return True


def _codex_pairs(server, env, count):
    """(key/value pairs, new session variables, problem) for one server's Codex overrides."""
    name, more, pairs = server["name"], {}, []
    literal = server["origin"] != "registry"
    clash = f"Codex cannot receive {name} because its variable {{}} conflicts with this session."
    if server["transport"] == "stdio":
        if not literal and (server["args"] or server["cwd"]):
            return [], {}, CODEX_ARGV.format(name=name)
        pairs.append(("command", server["command"]))
        if server["args"]:
            pairs.append(("args", list(server["args"])))
        if server["cwd"]:
            pairs.append(("cwd", server["cwd"]))
        passed = list(server["env_from"])
        for key in sorted(server["env"]):
            value = server["env"][key]
            if literal:
                if not re.fullmatch(BARE_KEY, key):
                    return [], {}, clash.format(_clean_text(key, 64))
                pairs.append(("env." + key, value))
                continue
            if (
                key in RESERVED_ENV
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
                or os.environ.get(key, value) != value
                or env.get(key, value) != value
            ):
                return [], {}, clash.format(_clean_text(key, 64))
            more[key] = value
            if key not in passed:
                passed.append(key)
        if passed:
            pairs.append(("env_vars", passed))
        return pairs, more, ""
    if _url_secret(server["url"]):
        return (
            [],
            {},
            f"Codex cannot receive {name} because its URL carries a password or query string; "
            f"move that value into a header and register {name} again.",
        )
    pairs.append(("url", server["url"]))
    if server["bearer_env"]:
        pairs.append(("bearer_token_env_var", server["bearer_env"]))
    refs = {}
    for index, header in enumerate(sorted(server["headers"])):
        refs[header] = f"AWB_MCP_H{count + index}"
        more[refs[header]] = server["headers"][header]
    refs |= server["header_env"]
    for header in sorted(refs):
        if not re.fullmatch(HEADER, header):
            return (
                [],
                {},
                f"Codex cannot receive {name} because of its header {_clean_text(header, 64)}.",
            )
        pairs.append(("env_http_headers." + header, refs[header]))
    return pairs, more, ""


def render_codex(servers, held=None):
    """held: variables the session already sets (vault values of project servers)."""
    argv, env, skipped, count = [], {}, [], 0
    for server in sorted(servers, key=lambda row: row["name"]):
        name, flags = server["name"], []
        if server["transport"] == "sse":
            problem = "Codex does not accept SSE servers."
        elif not re.fullmatch(BARE_KEY, name):
            problem = f"Codex cannot receive {_clean_text(name, 48)} because of its name."
        else:
            pairs, more, problem = _codex_pairs(server, (held or {}) | env, count)
            flags = [f"mcp_servers.{name}.{key}={_toml(value)}" for key, value in pairs]
            if not problem and not _plain([*flags, *more.values()]):
                problem = (
                    f"Codex cannot receive {name} because one of its values is not plain text; "
                    f"fix it and register {name} again."
                )
        if problem:
            skipped.append({"name": name, "reason": problem})
            continue
        for flag in flags:
            argv += ["-c", flag]
        count += len(server["headers"])
        env |= more
    return argv, env, skipped


def render_opencode(servers):
    found = {}
    for server in servers:
        if server["transport"] == "sse":
            continue
        entry = _json_entry(server, "pi", lambda name: "{env:" + name + "}")
        if server["transport"] == "stdio":
            found[server["name"]] = {
                "type": "local",
                "command": [entry["command"], *entry.get("args", [])],
                "environment": entry.get("env", {}),
                "enabled": True,
            }
        else:
            found[server["name"]] = {
                "type": "remote",
                "url": entry["url"],
                "headers": entry.get("headers", {}),
                "enabled": True,
            }
    return found


def _row(server, status="sent", reason=""):
    return {"server": server, "status": status, "reason": reason, "vault": {}, "blanket": False}


def _project_rows(root, harness, taken, deliver=True):
    servers = _project_servers(root)
    allowed = (
        bool(servers) and deliver and not _consent(root, harness)[0]["needed"]
    )
    rows = []
    for server in servers:
        name = server["name"]
        row = _row(server)
        if not server["enabled"]:
            row["status"] = "off"
        elif name in taken:
            row |= {
                "status": "skipped",
                "reason": f"Your registered server {name} is used instead of the project's.",
            }
        elif deliver and not allowed:
            row |= {
                "status": "skipped",
                "reason": f"Allow this folder's tools in Project setup to use {name}.",
            }
        elif deliver:
            for variable in _receives(server):
                if os.environ.get(variable):
                    continue
                value = _vault(variable)
                if not value:
                    row |= {
                        "status": "skipped",
                        "reason": f"Set {variable} in Project setup to use {name}.",
                        "vault": {},
                    }
                    break
                row["vault"][variable] = value
        rows.append(row)
    return rows


def _blanket(harness, native):
    if harness == "Hermes":
        return (
            "Hermes reads MCP servers only from its own settings. "
            "Add this one with: hermes mcp add {name} …"
        )
    if harness == "opencode":
        return "opencode receives servers through its own configuration."
    if native and harness in ("OMP", "Pi"):
        return f"With an agent account, {harness} reads only your own MCP files."
    return ""


def _own_servers(s):
    filled = dict.fromkeys(CAPS, False) | {k: v for k, v in s.items() if k != "_mcp"}
    if os.name != "nt":
        filled["desktop"] = False
    found = runtime.mcp_servers(filled)
    return (
        found if isinstance(found, dict) else {},
        {cap for cap in CAPS if filled[cap]},
    )


def _gather(s, harness, native, strict):
    """Every MCP candidate for a session with its status; nothing is written."""
    rows, warnings = [], []
    try:
        own, tools = _own_servers(s)
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
        if strict:
            raise
        own, tools = {}, set()
        warnings.append(
            str(error)
            if type(error) is ValueError and str(error)
            else "Workbench could not list your tools and registered servers. "
            "Check Plugins, then reopen Project setup."
        )
    for name, conf in own.items():
        server = from_mcp_json(name, conf, "registry")
        if server is None:
            label = _clean_text(name, 48)
            rows.append(
                _row(
                    _neutral(name, "registry", ""),
                    "skipped",
                    f"{label} has no command or URL, so it is not sent.",
                )
            )
        else:
            if server["origin"] == "builtin" and name not in tools:
                server["origin"] = "registry"
            rows.append(_row(server))
    blanket = _blanket(harness, native)
    root = _folder(s)
    if root is not None:
        rows += _project_rows(
            root, harness, {row["server"]["name"] for row in rows}, deliver=not blanket
        )
    sent = [row["server"] for row in rows if row["status"] == "sent"]
    if blanket:
        refused = {
            server["name"]: blanket.replace("{name}", _clean_text(server["name"], 48))
            for server in sent
        }
    elif harness == "Pi":
        refused = {r["name"]: r["reason"] for r in render_mcp_json(sent, "pi")[1]}
    elif harness == "Codex":
        held = {}
        for row in rows:
            held |= row["vault"] if row["status"] == "sent" else {}
        refused = {r["name"]: r["reason"] for r in render_codex(sent, held)[2]}
    else:
        refused = {}
    for row in rows:
        name = row["server"]["name"]
        if row["status"] == "sent" and name in refused:
            row |= {
                "status": "skipped",
                "reason": refused[name],
                "vault": {},
                "blanket": bool(blanket),
            }
    return rows, warnings


def _write_pi(folder, entries):
    target, names = folder / "mcp.json", folder / "workbench-mcp-names.json"
    try:
        ours = json.loads(names.read_text(encoding="utf8"))
    except (OSError, ValueError):
        ours = []
    ours = [name for name in ours if isinstance(name, str)] if isinstance(ours, list) else []
    if not entries and not ours:
        return []
    saved = _json_object(target) or {}
    servers = saved.get("mcpServers")
    servers = dict(servers) if isinstance(servers, dict) else {}
    for name in ours:
        servers.pop(name, None)
    saved["mcpServers"] = servers | entries
    folder.mkdir(parents=True, exist_ok=True)
    runtime.write_json(target, saved)
    runtime.write_json(names, sorted(entries))
    return [str(target)]


def _native_home(s):
    key = paths.try_key(s.get("workspace")) or ""
    slot = hashlib.sha256(key.encode("utf8")).hexdigest()[:12]
    return project_profiles.DATA / "clients" / ("native-" + slot)


def launch_mcp(s, harness, home):
    if harness not in HARNESSES:
        raise ValueError(BAD_AGENT)
    rows, _ = _gather(s, harness, home is None, strict=True)
    live = [row for row in rows if row["status"] == "sent"]
    sent = [row["server"] for row in live]
    env, argv, files = {}, [], []
    for row in live:
        env |= row["vault"]
    if harness == "Pi" and home is not None:
        files = _write_pi(Path(home) / "pi", render_mcp_json(sent, "pi")[0])
    elif harness == "Claude Code" and sent:
        folder = (Path(home) if home is not None else _native_home(s)) / "claude-mcp"
        folder.mkdir(parents=True, exist_ok=True)
        file = folder / "mcp.json"
        runtime.write_json(file, {"mcpServers": render_mcp_json(sent, "claude")[0]})
        argv, files = ["--mcp-config", str(file)], [str(file)]
    elif harness == "Codex":
        argv, more, _ = render_codex(sent, env)
        env |= more
    return {
        "argv": argv,
        "env": env,
        "files": files,
        "servers": [server["name"] for server in sent],
        "skipped": [
            {"name": row["server"]["name"], "reason": row["reason"]}
            for row in rows
            if row["status"] == "skipped"
        ],
    }


def omp_entries(s, taken):
    root = _folder(s)
    if root is None:
        return {}
    rows = _project_rows(root, "OMP", set(taken))
    return render_mcp_json(
        [row["server"] for row in rows if row["status"] == "sent"], "omp"
    )[0]


def _hermes_add(server):
    parts = ["hermes", "mcp", "add", server["name"]]
    if server["transport"] == "stdio":
        parts += ["--command", server["command"]]
        parts += ["--args", *server["args"]] if server["args"] else []
    else:
        parts += ["--url", server["url"]]
    return _clean_text(shlex.join(parts), 400)


def _ancestors(root):
    return [root, *root.parents][: MAX_LEVELS + 1]


def _chains(root):
    """(git root or None, folders from it down to root, root and every ancestor root first)."""
    ancestors = _ancestors(root)
    git = next((f for f in ancestors if os.path.lexists(f / ".git")), None)
    all_up = ancestors[::-1]
    down = [root] if git is None else all_up[all_up.index(git) :]
    return git, down, all_up


def _present(folder, names):
    found = []
    for name in names:
        path = _under(folder, name)
        if os.path.isfile(path) and not any(_same(path, other) for _, other in found):
            found.append((name, path))
    return found


def _entry(path, root, status="read", reason=""):
    raw = _head(path)
    return {
        "path": _rel(path, root),
        "abs": str(path),
        "status": status,
        "bytes": len(raw),
        "lines": len(raw.splitlines()),
        "reason": reason,
    }


def _where(folder, root):
    return "this folder" if folder == root else str(folder)


def _one_each(folders, names, root, reason, here_only=()):
    rows, wins = [], []
    for folder in folders:
        found = _present(
            folder, [n for n in names if folder == root or n not in here_only]
        )
        if not found:
            continue
        winner = found[0][0]
        rows.append(_entry(found[0][1], root))
        rows += [
            _entry(path, root, "shadowed", reason.format(winner=winner))
            for _, path in found[1:]
        ]
        wins.append((folder, winner, [name for name, _ in found[1:]]))
    return rows, wins


def _omp_rows(root, down, warnings):
    rows, wins = _one_each(
        down,
        OMP_NAMES,
        root,
        "OMP reads one instruction file per folder and {winner} comes first.",
        OMP_HERE,
    )
    warnings += [
        f"OMP will read {winner} instead of AGENTS.md in {_where(folder, root)}. "
        "Move its content into AGENTS.md or remove it."
        for folder, winner, losers in wins
        if "AGENTS.md" in losers
    ]
    return rows


def _override_warnings(harness, wins, root):
    return [
        f"{harness} will read AGENTS.override.md instead of AGENTS.md in {_where(folder, root)}."
        for folder, winner, losers in wins
        if winner == "AGENTS.override.md" and "AGENTS.md" in losers
    ]


def _pi_rows(root, all_up, warnings):
    rows, wins = _one_each(
        all_up,
        PI_NAMES,
        root,
        "Pi reads one instruction file per folder and {winner} comes first.",
    )
    warnings += _override_warnings("Pi", wins, root)
    return rows


def _codex_rows(root, down, warnings):
    rows, wins = _one_each(
        down,
        ("AGENTS.override.md", "AGENTS.md"),
        root,
        "Codex reads AGENTS.override.md instead in this folder.",
    )
    warnings += _override_warnings("Codex", wins, root)
    total = 0
    for row in rows:
        if row["status"] != "read":
            continue
        if total >= CODEX_DOC_CAP:
            reason = (
                "Codex stops reading instruction files after 32 KiB; "
                f"{row['path']} is past that limit."
            )
            row |= {"status": "truncated", "reason": reason}
            warnings.append(reason)
        total += row["bytes"]
    return rows


def _claude_files(all_up):
    return [
        (folder, name, path)
        for folder in all_up
        for name, path in _present(folder, CLAUDE_NAMES)
    ]


def _claude_agents(path, claude, root, version):
    """(status, reason) Claude Code gives an AGENTS.md at path, which may not exist yet."""
    inner = path.parent.name == ".claude"
    holder = path.parent.parent if inner else path.parent
    for folder, name, file in claude:
        if folder != holder or name == "CLAUDE.local.md":
            continue
        if _same(file, path):
            return "read", f"Same file as {_rel(file, root)}."
        beside = (name == ".claude/CLAUDE.md") == inner
        wanted = (
            ("@AGENTS.md", "@./AGENTS.md")
            if beside
            else ("@.claude/AGENTS.md", "@./.claude/AGENTS.md")
            if inner
            else ("@AGENTS.md", "@../AGENTS.md")
        )
        if any(line.strip() in wanted for line in _words(file).splitlines()):
            return "read", f"Imported by {_rel(file, root)}."
    if claude:
        nearest = next(row for row in claude if row[0] == claude[-1][0])
        return "shadowed", f"Claude Code reads {_rel(nearest[2], root)} instead."
    if version is not None and version >= CLAUDE_AGENTS_MD:
        return "read", ""
    return "ignored", "This Claude Code version does not read AGENTS.md by itself."


def _claude_rows(root, all_up, version, warnings):
    claude = _claude_files(all_up)
    rows = []
    for folder in all_up:
        rows += [_entry(path, root) for owner, _, path in claude if owner == folder]
        for _, path in _present(folder, ("AGENTS.md", ".claude/AGENTS.md")):
            rows.append(_entry(path, root, *_claude_agents(path, claude, root, version)))
    warnings += [
        f"{row['path']} has {row['lines']} lines; "
        "Claude Code follows shorter files better (under 200)."
        for row in rows
        if row["status"] == "read" and row["lines"] > LINE_GUIDE
    ]
    mine = next((row for row in rows if row["path"] == "AGENTS.md"), None)
    if mine and mine["status"] == "shadowed" and os.path.isfile(root / "CLAUDE.md"):
        warnings.append(
            "CLAUDE.md exists, so Claude Code will not read AGENTS.md. "
            "Add the line @AGENTS.md at the top of CLAUDE.md."
        )
    return rows


def _pointer(root, harness, version, assume_agents=False):
    none = {"needed": False, "path": "", "reason": ""}
    if (
        harness != "Claude Code"
        or not (assume_agents or os.path.isfile(root / "AGENTS.md"))
        or os.path.lexists(root / "CLAUDE.md")
        or os.path.lexists(root / ".claude" / "CLAUDE.md")
    ):
        return none
    status, reason = _claude_agents(
        root / "AGENTS.md", _claude_files(_ancestors(root)[::-1]), root, version
    )
    if status not in ("shadowed", "ignored"):
        return none
    return {"needed": True, "path": "CLAUDE.md", "reason": reason}


def _hermes_rows(root, down, warnings):
    kinds = [
        [p for folder in down[::-1] for _, p in _present(folder, (".hermes.md", "HERMES.md"))],
        [p for folder in down for _, p in _present(folder, ("AGENTS.override.md",))],
        [p for folder in down for _, p in _present(folder, ("AGENTS.md",))],
        [p for _, p in _present(root, ("CLAUDE.md",))],
        [p for _, p in _present(root, (".cursorrules",))],
    ]
    first = next((index for index, files in enumerate(kinds) if files), None)
    if first is None:
        return []
    winner = _rel(kinds[first][0], root)
    rows = [_entry(path, root) for path in kinds[first]]
    reason = f"Hermes loads one kind of instruction file per session and {winner} comes first."
    for files in kinds[first + 1 :]:
        rows += [_entry(path, root, "shadowed", reason) for path in files]
    if first < 2 and kinds[2]:
        warnings.append(f"Hermes will read {winner} instead of AGENTS.md.")
    warnings += [
        f"Hermes may shorten {row['path']} on small context windows "
        "(limit from 20,000 characters)."
        for row in rows
        if row["status"] == "read" and len(_words(Path(row["abs"]))) > HERMES_CHAR_FLOOR
    ]
    return rows


def _opencode_rows(root, down):
    rows = []
    for folder in down:
        found = dict(_present(folder, ("AGENTS.md", "CLAUDE.md")))
        if "AGENTS.md" in found:
            rows.append(_entry(found["AGENTS.md"], root))
        if "CLAUDE.md" in found:
            shadowed = "AGENTS.md" in found
            rows.append(
                _entry(
                    found["CLAUDE.md"],
                    root,
                    "shadowed" if shadowed else "read",
                    "opencode reads CLAUDE.md only in folders without AGENTS.md."
                    if shadowed
                    else "",
                )
            )
    return rows


def portable_issues(text):
    issues, fenced = [], False
    lines = text.splitlines()
    for number, line in enumerate(lines, 1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and re.match(r"@\S", line) and len(issues) < 10:
            issues.append(f"Line {number} is an @import; only some agents expand it.")
    if len(text.encode("utf8")) > CODEX_DOC_CAP:
        issues.append("AGENTS.md is larger than 32 KiB; Codex stops reading there.")
    if len(text) > HERMES_CHAR_FLOOR:
        issues.append(
            "AGENTS.md has more than 20,000 characters; "
            "Hermes may shorten it on small context windows."
        )
    if len(lines) > LINE_GUIDE:
        issues.append(
            f"AGENTS.md has {len(lines)} lines; agents follow shorter files better (under 200)."
        )
    return issues


def _skill_roots(harness, root, up, git):
    def each(*names):
        return [folder / name for folder in up for name in names]

    if harness == "Claude Code":
        return each(".claude/skills")
    if harness == "Codex":
        return each(".agents/skills")
    if harness == "OMP":
        return each(".omp/skills", ".agents/skills", ".agent/skills", ".claude/skills") + [
            root / name for name in (".codex/skills", ".opencode/skills", ".github/skills")
        ]
    if harness == "Pi":
        return [root / ".pi/skills", *each(".agents/skills")]
    if harness == "Hermes":
        return [] if git is None else [git / ".hermes/skills", git / ".agents/skills"]
    return each(".opencode/skills", ".claude/skills", ".agents/skills")


def _skill_folders(base):
    try:
        names = sorted(entry.name for entry in os.scandir(base) if entry.is_dir())
    except OSError:
        return []
    return [base / name for name in names if os.path.isfile(base / name / "SKILL.md")][
        : MAX_SKILLS + 1
    ]


def _front_matter(raw):
    try:
        lines = raw.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError:
        return None
    if not lines or lines[0].strip() != "---":
        return None
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        return None
    block = "\n".join(lines[1:end])
    if len(block) > MAX_FRONT:
        return None
    try:
        import yaml

        meta = yaml.safe_load(block)
    except Exception:
        return None
    if (
        not isinstance(meta, dict)
        or not isinstance(meta.get("name"), str)
        or not isinstance(meta.get("description"), str)
        or not meta["name"].strip()
    ):
        return None
    return meta


def _skill_rows(harness, root, up, git, warnings):
    rows, first = [], {}
    folders = [
        folder
        for base in _skill_roots(harness, root, up, git)
        for folder in _skill_folders(base)
    ]
    if len(folders) > MAX_SKILLS:
        warnings.append(
            f"This folder has more than {MAX_SKILLS} skills. "
            f"Workbench lists the first {MAX_SKILLS}; remove the ones you do not use."
        )
    for folder in folders[:MAX_SKILLS]:
        file = folder / "SKILL.md"
        row = {
            "name": _clean_text(folder.name, 80),
            "path": _clean_text(_rel(file, root), 400),
            "status": "invalid",
            "description": "",
            "reason": "SKILL.md needs a name and a description at the top.",
        }
        raw = _head(file, MAX_SKILL + 1)
        meta = _front_matter(raw) if len(raw) <= MAX_SKILL else None
        if meta is not None:
            name = _clean_text(meta["name"].strip(), 80)
            row |= {
                "name": name,
                "description": _clean_text(meta["description"], 300),
                "status": "read",
                "reason": "",
            }
            if name not in first:
                first[name] = (row["path"], raw)
            elif first[name][1] == raw:
                row["status"] = "duplicate"
            else:
                row |= {
                    "status": "shadowed",
                    "reason": f"Another skill named {name} in {first[name][0]} comes first.",
                }
        rows.append(row)
    return rows


def _skill_warnings(harness, root, git, rows):
    found = [row for row in rows if row["status"] != "invalid"]
    if harness == "Hermes":
        if git is None and (
            os.path.isdir(root / ".agents/skills") or os.path.isdir(root / ".hermes/skills")
        ):
            return [
                "Hermes loads project skills only inside a Git checkout. "
                "Run git init in this folder, or install the skills for your user."
            ]
        return (
            ["Run hermes skills trust in this folder once so Hermes loads its project skills."]
            if found
            else []
        )
    if harness == "Pi" and found:
        return ["Pi asks you to trust this project before it loads project skills."]
    if harness == "Claude Code":
        seen = {row["name"] for row in found}
        shared = [f.name for f in _skill_folders(root / ".agents/skills")]
        if any(name not in seen for name in shared):
            return [
                "Claude Code reads skills only from .claude/skills. "
                "Use Project setup to copy them there."
            ]
    return []


def _session_settings(root, extra=None):
    recipe = project_profiles.folder_view(root)["recipe"]
    return runtime.settings() | recipe | (extra or {}) | {"workspace": str(root)}


def _mcp_view(harness, root, s, warnings, notes):
    native = s.get("provider") == "Native login"
    rows, problems = _gather(s, harness, native, strict=False)
    warnings += problems
    launch = []
    for row in rows:
        server = row["server"]
        reason = row["reason"]
        if harness == "Hermes" and row["blanket"] and server["transport"]:
            reason = _hermes_add(server)
        launch.append(
            {
                "name": _clean_text(server["name"], 80),
                "origin": server["origin"],
                "transport": server["transport"],
                "summary": summary(server),
                "status": row["status"],
                "reason": reason,
            }
        )
    sent = {row["name"] for row in launch if row["status"] == "sent"}
    names, status, reason = MCP_FILES[harness]
    files = []
    for rel in names:
        path = _under(root, rel)
        if not os.path.isfile(path):
            continue
        listed = _config_servers(path, rel)
        found = (
            [_clean_text(name, 80) for name in list(listed)[:MAX_FILES]]
            if listed is not None
            else []
        )
        files.append(
            {
                "path": rel,
                "status": status,
                "servers": found,
                "reason": reason
                if listed is not None
                else "Could not read the server names in this file.",
            }
        )
        for name in found:
            if name not in sent:
                continue
            if harness != "OMP":
                warnings.append(
                    f"Both Workbench and {rel} define {name}; {harness} decides which one runs."
                )
            elif rel == ".omp/mcp.json":
                warnings.append(
                    f"This folder's .omp/mcp.json defines {name}, "
                    "which replaces the Workbench server of the same name in OMP."
                )
    if harness == "Hermes":
        notes.append("Hermes has no project MCP file.")
    return {"delivery": DELIVERY[harness], "launch": launch, "files": files}


def _version(cli_version):
    found = re.findall(r"\d+", cli_version) if isinstance(cli_version, str) else []
    return tuple(int(part) for part in found[:3]) if len(found) >= 3 else None


def resolve(workspace, harness, cli_version="", s=None):
    if not isinstance(harness, str) or harness not in HARNESSES:
        raise ValueError(BAD_AGENT)
    root = paths.canonical(workspace)
    git, down, all_up = _chains(root)
    version = _version(cli_version)
    warnings, notes = [], []
    if harness == "OMP":
        instructions = _omp_rows(root, down, warnings)
        notes.append("OMP task subagents do not receive AGENTS.md.")
    elif harness == "Pi":
        instructions = _pi_rows(root, all_up, warnings)
    elif harness == "Claude Code":
        instructions = _claude_rows(root, all_up, version, warnings)
    elif harness == "Codex":
        instructions = _codex_rows(root, down, warnings)
    elif harness == "Hermes":
        instructions = _hermes_rows(root, down, warnings)
    else:
        instructions = _opencode_rows(root, down)
    if harness in ("Pi", "opencode"):
        notes.append(
            f"@file lines in AGENTS.md are not expanded by {harness}; "
            "keep AGENTS.md self-contained."
        )
    notes.append(
        "Sessions that Workbench starts with a model source other than your agent account "
        "use their own settings folder, so your personal instruction files under your home "
        "folder are not read there."
    )
    if os.path.isfile(root / "AGENTS.md"):
        warnings += portable_issues(_words(root / "AGENTS.md"))
    skills = _skill_rows(harness, root, down[::-1], git, warnings)
    warnings += _skill_warnings(harness, root, git, skills)
    session = _session_settings(root) if s is None else s | {"workspace": str(root)}
    mcp = _mcp_view(harness, root, session, warnings, notes)
    return {
        "harness": harness,
        "workspace": str(root),
        "git_root": str(git) if git else "",
        "instructions": instructions,
        "pointer": _pointer(root, harness, version),
        "skills": skills,
        "mcp": mcp,
        "consent": _consent(root, harness)[0],
        "warnings": list(dict.fromkeys(warnings)),
        "notes": notes,
    }


def read_file(workspace, rel):
    if not isinstance(rel, str) or not (
        rel in READABLE or re.fullmatch(SKILL_FILE, rel)
    ):
        raise ValueError("That file cannot be opened here.")
    state, text = _existing(paths.inside(paths.canonical(workspace), rel))
    if state == "missing":
        return {"path": rel, "exists": False, "text": ""}
    size = len(text.encode("utf8"))
    if state != "text" or size > MAX_PROJECT:
        raise ValueError("That file is too large or is not text.")
    return {
        "path": rel,
        "exists": True,
        "text": text,
        "bytes": size,
        "lines": len(text.splitlines()),
    }


def _span(start, count):
    return f"{start + 1 if count else start}" + ("" if count == 1 else f",{count}")


def _coarse_diff(a, b, path):
    """One hunk between the common first and last lines, for texts too large to align line by line."""
    head, limit = 0, min(len(a), len(b))
    while head < limit and a[head] == b[head]:
        head += 1
    tail = 0
    while tail < limit - head and a[-1 - tail] == b[-1 - tail]:
        tail += 1
    gone, come = a[head : len(a) - tail], b[head : len(b) - tail]
    if gone or come:
        yield from (f"--- {path}", f"+++ {path}")
        yield f"@@ -{_span(head, len(gone))} +{_span(head, len(come))} @@"
        yield from ("-" + line for line in gone)
        yield from ("+" + line for line in come)


def _diff(old, new, path):
    a, b = old.splitlines(), new.splitlines()
    lines = (
        _coarse_diff(a, b, path)
        if len(a) * len(b) > DIFF_WORK
        else difflib.unified_diff(a, b, path, path, lineterm="")
    )
    kept, size = [], 0
    for line in lines:
        kept.append(line)
        size += len(line) + 1
        if size > MAX_DIFF + 1:
            break
    text = "\n".join(kept)
    return text if len(text) <= MAX_DIFF else text[:MAX_DIFF] + "\n… diff shortened"


def _item(path, kind, **fields):
    return {
        "path": path,
        "kind": kind,
        "action": "skip",
        "reason": "",
        "diff": "",
        "content": "",
        "sha_before": "",
        "sha_after": "",
    } | fields


def _target(root, path, kind):
    """(item, old text): a finished skip item, or an open item and the file's text (None when missing)."""
    item = _item(path, kind)
    try:
        state, old = _existing(paths.inside(root, path))
    except ValueError as error:
        return item | {"reason": str(error)}, None
    if state == "opaque":
        return item | {
            "reason": f"{path} is not a text file Workbench can compare. Your file is kept."
        }, None
    if state == "missing":
        return item | {"action": "create"}, None
    return item | {"action": "update", "sha_before": _sha(old)}, old


def _file_item(root, path, kind, text, verdict):
    item, old = _target(root, path, kind)
    item["sha_after"] = _sha(text)
    if item["action"] == "skip":
        return item
    if old is None:
        return item | {"content": text}
    if old == text:
        return item | {"action": "skip", "reason": "Already up to date."}
    replace, reason = verdict(item["sha_before"])
    return item | {
        "action": "update" if replace else "skip",
        "reason": reason,
        "diff": _diff(old, text, path),
        "content": text if replace else "",
    }


def _plan_result(root, harness, items, warnings):
    return {
        "id": hashlib.sha256(
            json.dumps(
                [[i["path"], i["action"], i["sha_before"], i["sha_after"]] for i in items]
            ).encode()
        ).hexdigest(),
        "workspace": str(root),
        "harness": harness,
        "items": items,
        "warnings": warnings,
    }


def _wanted_files(root, want):
    files = want.get("files") or {}
    if not isinstance(files, dict) or len(files) > MAX_FILES:
        raise ValueError(BAD_PLAN)
    found = {}
    for path, text in files.items():
        if (
            not isinstance(path, str)
            or not isinstance(text, str)
            or not (path == "AGENTS.md" or re.fullmatch(WRITABLE, path))
            or len(text.encode("utf8", errors="replace")) > MAX_FILE
            or not text.isprintable()
            and any(0xD800 <= ord(c) <= 0xDFFF or c == "\0" for c in text)
        ):
            raise ValueError(BAD_PLAN)
        try:
            paths.inside(root, path)
        except ValueError as error:
            if str(error) == paths.BAD_NAME:
                raise ValueError(BAD_PLAN) from None
        found[path] = _unix(text)
    return found


def _mcp_item(root, want, warnings):
    found, broken = _load_project(root)
    warnings += found["errors"]
    if broken:
        return _item(PROJECT_FILE, "mcp", reason=UNREADABLE)
    wanted, removed = want.get("mcp_set") or {}, want.get("mcp_remove") or []
    if not isinstance(wanted, dict) or not isinstance(removed, list):
        raise ValueError(BAD_PLAN)
    servers = {k: v for k, v in found["servers"].items() if k not in removed}
    kept = []
    for name, raw in wanted.items():
        server = clean_server(name, raw)
        if name in servers and want.get("mcp_keep_existing"):
            kept.append(name)
        else:
            servers[name] = server
    if len(servers) > MAX_SERVERS:
        raise ValueError(
            f"{PROJECT_FILE} can list at most {MAX_SERVERS} servers. "
            "Remove one, then try again."
        )
    note = f"Kept your existing definition of {', '.join(sorted(kept))}." if kept else ""
    return _file_item(
        root,
        PROJECT_FILE,
        "mcp",
        project_text(servers, found["extra"]),
        lambda sha: (True, note),
    )


def plan(workspace, harness, want, cli_version=""):
    if not isinstance(harness, str) or harness not in HARNESSES:
        raise ValueError(BAD_AGENT)
    if not isinstance(want, dict):
        raise ValueError(BAD_PLAN)
    root = paths.canonical(workspace)
    files = _wanted_files(root, want)
    if harness == "Claude Code":
        files |= {
            ".claude" + path.removeprefix(".agents"): text
            for path, text in files.items()
            if path.startswith(".agents/skills/")
        }
    overwrite = want.get("overwrite")
    overwrite = overwrite if isinstance(overwrite, list) else []
    written = project_profiles.written(root)

    def verdict(path):
        def decide(sha):
            if path in overwrite:
                return True, ""
            if written.get(path) == sha:
                return (
                    True,
                    "Workbench wrote this file earlier and it has not been changed since.",
                )
            return False, f"{path} already exists. Your file is kept."

        return decide

    items, warnings = [], []
    writes_agents = "AGENTS.md" in files
    if writes_agents:
        text = files.pop("AGENTS.md")
        items.append(
            _file_item(root, "AGENTS.md", "instructions", text, verdict("AGENTS.md"))
        )
        warnings += portable_issues(text)
    pointer = _pointer(root, harness, _version(cli_version), writes_agents)
    if want.get("pointer") and pointer["needed"]:
        items.append(
            _file_item(root, "CLAUDE.md", "pointer", "@AGENTS.md\n", verdict("CLAUDE.md"))
        )
    items += [
        _file_item(root, path, "skill", files[path], verdict(path))
        for path in sorted(files)
    ]
    if want.get("mcp_set") or want.get("mcp_remove"):
        items.append(_mcp_item(root, want, warnings))
    return _plan_result(root, harness, items, warnings)


def _write(root, item):
    path = item["path"]
    paths.inside(root, path)
    folder = root
    for name in path.split("/")[:-1]:
        folder = folder / name
        folder.mkdir(exist_ok=True)
    target = paths.inside(root, path)
    state, old = _existing(target)
    if state == "opaque" or (_sha(old) if state == "text" else "") != item["sha_before"]:
        raise ValueError(f"{path} changed while applying. Review the plan again.")
    temp = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
    try:
        with open(temp, "x", encoding="utf8", newline="\n") as stream:
            stream.write(item["content"])
        if state == "text":
            shutil.copymode(target, temp)
        os.replace(temp, target)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            os.chmod(temp, stat.S_IREAD | stat.S_IWRITE)
            temp.unlink(missing_ok=True)


def _commit(root, found, plan_id, confirmed, before):
    if not isinstance(plan_id, str) or found["id"] != plan_id:
        raise ValueError(
            "This folder changed after you reviewed the plan. Review it again, then apply it."
        )
    chosen = (
        {path for path in confirmed if isinstance(path, str)}
        if isinstance(confirmed, list)
        else set()
    )
    written, skipped, failed = {}, [], []
    for item in found["items"]:
        path = item["path"]
        if item["action"] == "skip":
            continue
        if path not in chosen:
            skipped.append({"path": path, "reason": "Not selected."})
            continue
        try:
            _write(root, item)
        except ValueError as error:
            skipped.append({"path": path, "reason": str(error)})
        except OSError:
            failed.append(
                {
                    "path": path,
                    "reason": f"Could not write {path}. "
                    "Close programs that have it open, then apply again.",
                }
            )
        else:
            written[path] = item["sha_after"]
    if written:
        project_profiles.note_written(root, written)
        status, lines = _consent(root)
        changed = {
            path
            for path in set(before[1]) | set(lines)
            if before[1].get(path) != lines.get(path)
        }
        if (
            status["digest"]
            and not before[0]["needed"]
            and changed
            and changed <= set(written)
        ):
            project_profiles.grant_consent(
                root, status["digest"], [row["path"] for row in status["items"]]
            )
    return {
        "written": list(written),
        "skipped": skipped,
        "failed": failed,
        "consent": _consent(root)[0],
    }


def apply(workspace, harness, want, plan_id, confirmed, cli_version=""):
    root = paths.canonical(workspace)
    before = _consent(root)
    return _commit(root, plan(root, harness, want, cli_version), plan_id, confirmed, before)


def _codex_table(server):
    name = server["name"]
    lines = [f"[mcp_servers.{name}]"]
    if server["transport"] == "stdio":
        lines.append(f"command = {_toml(server['command'])}")
        if server["args"]:
            lines.append(f"args = {_toml(server['args'])}")
        if server["env_from"]:
            lines.append(f"env_vars = {_toml(server['env_from'])}")
    else:
        lines.append(f"url = {_toml(server['url'])}")
        if server["bearer_env"]:
            lines.append(f"bearer_token_env_var = {_toml(server['bearer_env'])}")
    for table, values in (("env", server["env"]), ("env_http_headers", server["header_env"])):
        if values:
            lines += ["", f"[mcp_servers.{name}.{table}]"]
            lines += [f"{key} = {_toml(values[key])}" for key in sorted(values)]
    return "\n".join(lines)


def _export_toml(path, old, servers):
    """(new text, skip reason) for the Codex project file."""
    known = {}
    if old is not None:
        try:
            known = tomllib.loads(old).get("mcp_servers", {})
        except (ValueError, RecursionError):
            return None, f"{path} could not be read, so Workbench will not add to it."
    fresh = [
        server
        for server in servers
        if not isinstance(known, dict) or server["name"] not in known
    ]
    if not fresh:
        return None, "Already lists these servers."
    tables = "\n\n".join(_codex_table(server) for server in fresh) + "\n"
    text = tables if not old else old.rstrip("\n") + "\n\n" + tables
    try:
        added = tomllib.loads(text).get("mcp_servers", {})
        safe = all(isinstance(added.get(s["name"]), dict) for s in fresh)
    except (ValueError, RecursionError, AttributeError):
        safe = False
    return text, (
        "" if safe else f"Workbench could not add to {path} safely. Add the lines shown yourself."
    )


def _export_json(path, old, key, entries):
    """(new text, skip reason) for a JSON project file that keeps what it already lists."""
    data = {}
    if old is not None:
        try:
            data = json.loads(old)
        except (ValueError, RecursionError):
            data = None
        if not isinstance(data, dict) or not isinstance(data.get(key, {}), dict):
            return None, f"{path} is not plain JSON, so Workbench will not rewrite it."
    listed = data.get(key, {})
    fresh = {name: entry for name, entry in entries.items() if name not in listed}
    if not fresh:
        return None, "Already lists these servers."
    text = json.dumps(data | {key: listed | fresh}, indent=2, ensure_ascii=False) + "\n"
    if not _plain([text]):
        return None, f"{path} is not plain JSON, so Workbench will not rewrite it."
    nested = isinstance(listed.get("servers"), dict) and "type" not in listed["servers"]
    return text, (
        f"Workbench could not add to {path} safely. Add the lines shown yourself."
        if key == "mcp" and nested
        else ""
    )


def export_plan(workspace, harness):
    if not isinstance(harness, str) or harness not in HARNESSES:
        raise ValueError(BAD_AGENT)
    root = paths.canonical(workspace)
    servers = [server for server in _project_servers(root) if server["enabled"]]
    if not servers:
        raise ValueError("Turn on at least one project server before exporting.")
    if harness == "Hermes":
        commands = "\n".join(_hermes_add(server) for server in servers)
        return _plan_result(
            root,
            harness,
            [_item("", "mcp", reason=f"Hermes has no project file. Run: {commands}")],
            [],
        )
    path, dialect = EXPORTS[harness]
    item, old = _target(root, path, "mcp")
    if item["action"] != "skip":
        if dialect == "toml":
            text, reason = _export_toml(path, old, servers)
        elif dialect == "opencode":
            text, reason = _export_json(path, old, "mcp", render_opencode(servers))
        else:
            text, reason = _export_json(
                path, old, "mcpServers", render_mcp_json(servers, dialect)[0]
            )
        if text is not None:
            item |= {"sha_after": _sha(text), "content": "" if reason else text}
            if old is not None:
                item["diff"] = _diff(old, text, path)
            elif reason:
                item["diff"] = _diff("", text, path)
        if reason:
            item |= {"action": "skip", "reason": reason}
    return _plan_result(root, harness, [item], [])


def export_apply(workspace, harness, plan_id, confirmed):
    root = paths.canonical(workspace)
    before = _consent(root)
    return _commit(root, export_plan(root, harness), plan_id, confirmed, before)


def _want(p):
    edited = "agents_md" in p
    wanted, removed = p.get("mcp_set") or {}, p.get("mcp_remove") or []
    if (
        (edited and not isinstance(p["agents_md"], str))
        or not isinstance(wanted, dict)
        or not isinstance(removed, list)
    ):
        raise ValueError(BAD_PLAN)
    return {
        "files": {"AGENTS.md": p["agents_md"]} if edited else {},
        "overwrite": ["AGENTS.md"] if edited else [],
        "pointer": bool(p.get("pointer", True)),
        "mcp_set": {name: clean_server(name, raw) for name, raw in wanted.items()},
        "mcp_remove": list(removed),
        "mcp_keep_existing": False,
    }


def _mcp_listing(workspace):
    root = paths.canonical(workspace)
    found = _load_project(root)[0]
    return {
        "path": PROJECT_FILE,
        "exists": found["exists"],
        "servers": [
            {key: server[key] for key in ("name", *SERVER_KEYS, "enabled")}
            | {"summary": summary(server), "secrets": secret_status(_receives(server))}
            for server in _project_servers(root)
        ],
        "errors": found["errors"],
    }


def _resolve_settings(p):
    carried = {key: p[key] for key in (*CAPS, "plugins") if key in p}
    if not carried:
        return None
    return _session_settings(
        paths.canonical(p.get("workspace")), project_profiles._clean_recipe(carried)[0]
    )


def dispatch(action, p):
    workspace, harness = p.get("workspace"), p.get("harness")
    version = p.get("cli_version", "")
    if action == "project-setup-resolve":
        return resolve(workspace, harness, version, _resolve_settings(p))
    if action == "project-setup-read":
        return read_file(workspace, p.get("path"))
    if action == "project-setup-plan":
        return plan(workspace, harness, _want(p), version)
    if action == "project-setup-apply":
        return apply(
            workspace, harness, _want(p), p.get("plan"), p.get("confirmed", []), version
        )
    if action == "project-setup-mcp":
        return _mcp_listing(workspace)
    if action == "project-setup-mcp-toggle":
        project_profiles.set_project_mcp(workspace, p.get("name"), p.get("enabled"))
        return _mcp_listing(workspace)
    if action == "project-setup-export-plan":
        return export_plan(workspace, harness)
    if action == "project-setup-export-apply":
        return export_apply(workspace, harness, p.get("plan"), p.get("confirmed", []))
    if action == "project-setup-consent":
        return consent_status(workspace, harness)
    if action == "project-setup-consent-grant":
        return grant(workspace, p.get("digest"))
    if action == "project-setup-consent-revoke":
        return revoke(workspace)
    if action == "project-setup-secret-save":
        return store_secret(p.get("name"), p.get("value"))
    if action == "project-setup-secret-forget":
        return forget_secret(p.get("name"))
    raise ValueError("Unknown project setup action.")
