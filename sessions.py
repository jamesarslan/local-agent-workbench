"""Running sessions from every source, and recent sessions per project read from each harness's own history."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import stat
import subprocess
import sys
import time
from urllib.parse import quote

from filelock import FileLock

from runtime import DATA, QUIET, load, write_json

HEAD = 262144  # most bytes read from one transcript
SCAN = 400  # most files or rows examined per store per call
TITLE = 130
DATE = 40
LINE = 32768
KEEP = 60
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
ROW_ID = re.compile(r"[0-9a-f]{64}")
TMUX_NAME = re.compile(r"awb-[0-9a-f]{16}")
TMUX_LINE = re.compile(r"(awb-[0-9a-f]{16}) ([0-9]{1,12}) ([0-9]{1,6})")
FIELD = rb'(?<!\\)"%s"\s*:\s*"((?:[^"\\]|\\.)*)"'
CWD = re.compile(FIELD % b"cwd")
WHEN = re.compile(FIELD % b"timestamp")
ROLLOUT = re.compile(
    r"rollout-.*-([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\.jsonl(?:\.zst)?$"
)
CLI = {
    "OMP": "omp",
    "Pi": "pi",
    "Claude Code": "claude",
    "Codex": "codex",
    "Hermes": "hermes",
    "opencode": "opencode",
}
# harness: (row field naming the session, arguments before it, arguments when no session is chosen)
RESUME = {
    "OMP": ("path", ["--resume"], ["--resume"]),
    "Pi": ("path", ["--session"], ["--resume"]),
    "Claude Code": ("session", ["--resume"], ["--resume"]),
    "Codex": ("session", ["resume"], ["resume"]),
    "opencode": ("session", ["--session"], ["--continue"]),
    "Hermes": (None, None, ["--continue"]),
}
HOME_ENV = {
    "Claude Code": ("CLAUDE_CONFIG_DIR", ".claude"),
    "Codex": ("CODEX_HOME", ".codex"),
}
REMOTE = "remote-sessions.json"
LIST = "tmux -L agent-workbench list-sessions -F '#{session_name} #{session_created} #{session_attached}' 2>/dev/null || true"
KILL = "tmux -L agent-workbench kill-session -t "
UNAVAILABLE = "This session is no longer available in this project; pick another one."
WINDOWS = os.name == "nt"
PLAIN = re.compile(r"[A-Za-z0-9_.:/\\-]+")
ODD = re.compile("[\"%$`!^\u201c\u201d\u201e\u201f\x00-\x1f\x7f]|\\\\$")


def same_folder(a, b):
    import paths

    try:
        return paths.same(a, b)
    except (OSError, RuntimeError, ValueError, TypeError):
        return False


def project_dir(raw):
    if not isinstance(raw, str) or not raw.strip() or not Path(raw).is_absolute():
        raise ValueError("Choose a project folder first.")
    try:
        path = Path(raw).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise ValueError(
            "This project folder no longer exists; choose another folder."
        ) from None
    if not path.is_dir():
        raise ValueError("Choose a project folder, not a file.")
    import paths

    try:
        found = paths.canonical(str(path))
        return str(found if os.path.samefile(found, path) else path)
    except (OSError, ValueError):
        return str(path)


def clean(value, cap=TITLE):
    shown = "".join(c if c.isprintable() else " " for c in str(value))
    return " ".join(shown.split())[:cap]


def stamp(seconds):
    moment = datetime.fromtimestamp(seconds, timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def text(value):
    return value if isinstance(value, str) else ""


def number(value):
    plain = isinstance(value, (int, float)) and not isinstance(value, bool)
    return value if plain else None


def parse(raw):
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        return None


def head(path, limit=HEAD):
    with open(path, "rb") as stream:
        if path.suffix != ".zst":
            return stream.read(limit)
        import zstandard

        try:
            reader = zstandard.ZstdDecompressor().stream_reader(
                stream, read_across_frames=True
            )
            return reader.read(limit)
        except zstandard.ZstdError as exc:
            raise OSError(str(exc)) from None


def records(data):
    pieces = data.split(b"\n")
    for raw in pieces[:-1] if len(pieces) > 1 else pieces:
        row = parse(raw)
        if isinstance(row, dict):
            yield row


def under(path, base):
    try:
        return path.resolve().is_relative_to(base)
    except (OSError, RuntimeError):
        return False


def newest(root, files):
    try:
        base = root.resolve()
    except (OSError, RuntimeError):
        return []
    found = []
    try:
        for f in files:
            try:
                info = f.stat()
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                found.append((info.st_mtime, f))
    except OSError:
        pass
    found.sort(key=lambda item: item[0], reverse=True)
    kept = []
    for item in found:
        if under(item[1], base):
            kept.append(item)
            if len(kept) == SCAN:
                break
    return kept


def read_pi(root, project, harness):
    for mtime, f in newest(root, root.glob("*/*.jsonl")):
        try:
            with open(f, "rb") as stream:
                first = parse(stream.readline(LINE))
                titled = isinstance(first, dict) and first.get("type") == "title"
                header = parse(stream.readline(LINE)) if titled else first
            if (
                header.get("type") != "session"
                or not isinstance(header["cwd"], str)
                or not same_folder(header["cwd"], project)
            ):
                continue
            yield dict(
                session=text(header.get("id")),
                title=(clean(text(first.get("title"))) if titled else "")
                or harness + " session",
                date=clean(text(header.get("timestamp")), DATE),
                updated=stamp(mtime),
                path=str(f),
            )
        except (OSError, ValueError, KeyError, AttributeError, TypeError):
            continue


def claude_folders(root, project):
    """Transcripts in the folders named after the project; case is left to the cwd check that follows."""
    name = re.sub(r"[^A-Za-z0-9]", "-", project).lower()
    try:
        for folder in root.iterdir():
            seen = folder.name.lower()
            if seen == name or (len(name) > 200 and seen.startswith(name[:200])):
                yield from folder.glob("*.jsonl")
    except OSError:
        return


def claude_header(rows):
    """cwd, date and title from the leading lines of a Claude Code transcript."""
    cwd = date = named = asked = ""
    for row in rows:
        if not cwd:
            cwd = text(row.get("cwd"))
        if not date:
            date = text(row.get("timestamp"))
        for key in ("customTitle", "aiTitle", "summary"):
            if text(row.get(key)).strip():
                named = row[key]
        body = row.get("message")
        if (
            not asked
            and row.get("type") == "user"
            and not row.get("isMeta")
            and isinstance(body, dict)
            and isinstance(body.get("content"), str)
            and not body["content"].lstrip().startswith("<")
        ):
            asked = body["content"]
    return cwd, clean(date, DATE), clean(named) or clean(asked)


def read_claude(root, project, harness):
    for mtime, f in newest(root, claude_folders(root, project)):
        if not SESSION_ID.fullmatch(f.stem):
            continue
        try:
            cwd, date, title = claude_header(records(head(f)))
            if cwd and same_folder(cwd, project):
                yield dict(
                    session=f.stem,
                    title=title or harness + " session",
                    date=date,
                    updated=stamp(mtime),
                    path=str(f),
                )
        except (OSError, ValueError):
            continue


def codex_rollouts(root):
    def down(folder):
        try:
            return sorted(folder.iterdir(), key=lambda p: p.name, reverse=True)
        except OSError:
            return []

    seen = 0
    for year in down(root):
        for month in down(year):
            for day in down(month):
                for f in down(day):
                    found = ROLLOUT.search(f.name)
                    if found:
                        seen += 1
                        if seen > SCAN:
                            return
                        yield f, found.group(1)


def codex_header(first):
    """cwd and date from a rollout's first line, which is cut short when its instructions outgrow HEAD."""
    try:
        row = json.loads(first)
    except (ValueError, RecursionError):
        cwd, date = CWD.search(first), WHEN.search(first)
        return (
            parse(b'"' + cwd.group(1) + b'"') if cwd else None,
            clean(text(parse(b'"' + date.group(1) + b'"')), DATE) if date else "",
        )
    if not isinstance(row, dict):
        return None, ""
    body = row["payload"] if isinstance(row.get("payload"), dict) else row
    date = text(body.get("timestamp")) or text(row.get("timestamp"))
    return body.get("cwd"), clean(date, DATE)


def read_codex(root, project, harness):
    try:
        base = root.resolve()
    except (OSError, RuntimeError):
        return
    for f, ident in codex_rollouts(root):
        try:
            info = f.stat()
            if not stat.S_ISREG(info.st_mode) or not under(f, base):
                continue
            cwd, date = codex_header(head(f).split(b"\n", 1)[0])
            if isinstance(cwd, str) and cwd and same_folder(cwd, project):
                yield dict(
                    session=ident,
                    title=harness + " session",
                    date=date,
                    updated=stamp(info.st_mtime),
                    path=str(f),
                )
        except (OSError, ValueError, ImportError):
            continue


def read_opencode(root, project, harness):
    if not root.is_file():
        return
    con = None
    try:
        con = sqlite3.connect(
            "file:"
            + quote(root.as_posix(), safe="/:", errors="surrogateescape")
            + "?mode=ro",
            uri=True,
            timeout=0.5,
        )
        rows = con.execute(
            "select id, title, directory, time_created, time_updated from session"
            " where parent_id is null and time_archived is null"
            " order by time_updated desc limit ?",
            (SCAN,),
        ).fetchall()
    except sqlite3.Error:
        return
    finally:
        if con is not None:
            con.close()
    for ident, title, folder, created, updated in rows:
        try:
            if (
                isinstance(ident, str)
                and SESSION_ID.fullmatch(ident)
                and isinstance(folder, str)
                and same_folder(folder, project)
            ):
                yield dict(
                    session=ident,
                    title=clean(text(title)) or harness + " session",
                    date=stamp(int(created) / 1000),
                    updated=stamp(int(updated or created) / 1000),
                    path="",
                )
        except (TypeError, ValueError, OverflowError, OSError):
            continue


READERS = {
    "OMP": read_pi,
    "Pi": read_pi,
    "Claude Code": read_claude,
    "Codex": read_codex,
    "opencode": read_opencode,
}


def account_home(var, default):
    """The user's own harness home. A home Workbench relocated is never the account, even when inherited."""
    raw = os.environ.get(var)
    path = Path(raw).absolute() if raw else None
    if path is None or path.is_relative_to(DATA / "clients"):
        return Path.home() / default
    return path


def stores(harness):
    home = Path.home()
    slots = sorted(p for p in (DATA / "clients").glob("*") if p.is_dir())
    if harness == "OMP":
        rows = [(home / ".omp/agent", "account")] + [
            (p / "agent", "workbench")
            for p in sorted((home / ".omp/profiles").glob("*"))
            if p.name.startswith(("workbench-", "local-agent-workbench"))
        ]
        return [(h, h / "sessions", kind) for h, kind in rows]
    if harness == "Pi":
        account = account_home("PI_CODING_AGENT_DIR", ".pi/agent")
        rows = [(account, "account")] + [(s / "pi", "workbench") for s in slots]
        return [(h, h / "sessions", kind) for h, kind in rows]
    if harness in HOME_ENV:
        part, leaf = (
            ("claude", "projects") if harness == "Claude Code" else ("codex", "sessions")
        )
        account = account_home(*HOME_ENV[harness])
        rows = [(account, "account")] + [(s / part, "workbench") for s in slots]
        return [(h, h / leaf, kind) for h, kind in rows]
    if harness == "opencode":
        base = Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share").absolute()
        return [(base / "opencode", base / "opencode" / "opencode.db", "account")]
    return []


def harnesses():
    import connections

    return [h for h in connections.HARNESS if h in READERS]


def resume_args(harness, row=None):
    field, before, bare = RESUME[harness]
    if row is None:
        return list(bare)
    if field is None:
        raise ValueError(
            f"{harness} sessions cannot be resumed one by one from Workbench; resume without choosing a session."
        )
    value = row.get(field)
    if not isinstance(value, str) or not (
        SESSION_ID.fullmatch(value)
        if field == "session"
        else value and not value.startswith("-")
    ):
        raise ValueError(UNAVAILABLE)
    return [*before, value]


def windows_line(args):
    """One line cmd.exe and PowerShell read the same way, or "" when an argument cannot be written for both."""
    if any(ODD.search(a) for a in args):
        return ""
    return " ".join(a if PLAIN.fullmatch(a) else f'"{a}"' for a in args)


def typed(harness, row):
    args = [CLI[harness], *resume_args(harness, row)]
    return windows_line(args) if WINDOWS else shlex.join(args)


def recent(project, harness=None, limit=20):
    rows = {}
    for name in [harness] if harness else harnesses():
        reader = READERS.get(name)
        if reader is None:
            continue
        for home, root, store in stores(name):
            for row in reader(root, project, name):
                if not SESSION_ID.fullmatch(row["session"]):
                    if RESUME[name][0] == "session":
                        continue
                    row["session"] = ""
                key = row["path"] or name + ":" + row["session"]
                ident = hashlib.sha256(key.encode("utf8", "surrogateescape")).hexdigest()
                row.update(id=ident, harness=name, store=store, home=str(home))
                row["command"] = typed(name, row) if store == "account" else ""
                rows.setdefault(ident, row)
    found = sorted(rows.values(), key=lambda r: r["updated"], reverse=True)
    return found if limit is None else found[:limit]


def find(project, ident):
    if not isinstance(ident, str) or not ROW_ID.fullmatch(ident):
        return None
    return next((r for r in recent(project, None, None) if r["id"] == ident), None)


def resume(harness, argv, env, row=None):
    if harness not in RESUME:
        raise ValueError(
            f"{harness} sessions cannot be resumed from Workbench; start a new session instead."
        )
    if row is not None and row["harness"] != harness:
        raise ValueError(
            f"This is a {row['harness']} session; choose {row['harness']} as the agent, then resume."
        )
    extra = resume_args(harness, row)
    if row is not None and harness in HOME_ENV:
        var, slots = HOME_ENV[harness][0], DATA / "clients"
        managed = bool(env.get(var)) and Path(env[var]).is_relative_to(slots)
        if managed and row["store"] == "account":
            raise ValueError(
                f"This session was started with your {harness} account login; choose Agent account as the model source, then resume."
            )
        if not managed and row["store"] == "workbench":
            raise ValueError(
                "This session was started with a Workbench model source; choose that source instead of Agent account, then resume."
            )
        if managed:
            if not Path(text(row.get("home"))).is_relative_to(slots):
                raise ValueError(UNAVAILABLE)
            env = {**env, var: row["home"]}
    argv = list(argv)
    if harness == "Codex":
        at = next(
            (i for i, a in enumerate(argv) if i and str(a).startswith("-")), len(argv)
        )
        return argv[:at] + extra + argv[at:], env
    return argv + extra, env


def remote_rows():
    try:
        rows = load(DATA / REMOTE, {})
    except (OSError, ValueError):
        return {}
    if not isinstance(rows, dict):
        return {}
    return {
        name: rec
        for name, rec in rows.items()
        if TMUX_NAME.fullmatch(name) and isinstance(rec, dict)
    }


def registry_lock():
    return FileLock(str(DATA / "remote-sessions.lock"), timeout=10)


def remember_remote(name, **fields):
    if not isinstance(name, str) or not TMUX_NAME.fullmatch(name):
        return
    with registry_lock():
        rows = remote_rows()
        began = number(rows.get(name, {}).get("started")) or time.time()
        rows[name] = fields | {"started": began}
        keep = sorted(
            rows.items(), key=lambda r: number(r[1].get("started")) or 0, reverse=True
        )
        write_json(DATA / REMOTE, dict(keep[:KEEP]))


def forget_remote(names):
    names = [n for n in names if isinstance(n, str)]
    if not names:
        return
    with registry_lock():
        rows = remote_rows()
        if [rows.pop(n) for n in names if n in rows]:
            write_json(DATA / REMOTE, rows)


def remote_row(name, rec, state):
    host, harness, folder = (text(rec.get(k)) for k in ("host", "harness", "folder"))
    return dict(
        id=f"remote:{host}:{name}",
        kind="remote",
        name=name,
        title=clean(" · ".join(filter(None, [harness or "Shell", folder]))),
        harness=harness,
        workspace=text(rec.get("workspace")),
        host=host,
        folder=folder,
        mode=text(rec.get("mode")),
        pid=None,
        started=number(rec.get("started")),
        state=state,
    )


def docked():
    """Dock terminals, when the dock is loaded; a module another thread is still importing counts as not loaded."""
    active = getattr(sys.modules.get("project_terminal"), "active", None)
    return active() if active else []


def terminal_row(t):
    away = t["kind"] == "remote"
    row = dict(
        id="term:" + t["id"],
        kind=t["kind"],
        terminal=t["id"],
        title=t["title"],
        harness=t["harness"],
        workspace=t["workspace"],
        pid=t["pid"],
        started=t["started"],
        state="attached" if away else "running",
    )
    if away and t.get("lasting") is False:
        row["lasting"] = False
    return row | dict(host=t["host"], name=t["name"]) if away else row


def native_row(rec):
    harness = text(rec.get("harness"))
    return dict(
        id="native:" + (text(rec.get("id")) or str(rec["pid"])),
        kind="native",
        title=clean(" · ".join(filter(None, [harness, text(rec.get("model"))]))),
        harness=harness,
        workspace=text(rec.get("workspace")),
        pid=rec["pid"],
        started=number(rec.get("started")) or number(rec.get("born")),
        state="running",
    )


def model_rows():
    import runtime

    proc, rec = runtime.owned()
    if not proc:
        return []
    spec = rec.get("spec") if isinstance(rec.get("spec"), dict) else {}
    hosted = text(spec.get("vllm_model"))
    name = Path(text(spec.get("model"))).name
    return [
        dict(
            id="model",
            kind="model",
            title=clean((spec.get("engine") == "vLLM" and hosted) or name or hosted),
            harness="",
            workspace="",
            pid=proc.pid,
            started=number(rec.get("started")) or number(rec.get("born")),
            state="running",
            port=number(rec.get("port")) or runtime.PORT,
            context=spec.get("context"),
        )
    ]


def tunnel_rows():
    """Live SSH forwards of saved machines, found from their records without creating a folder."""
    import machines

    try:
        names = machines.labels()
    except (OSError, ValueError):
        return []
    rows = []
    for host, label in names.items() if isinstance(names, dict) else []:
        try:
            folder = DATA / "ssh" / machines.identifier(host)
        except (TypeError, ValueError):
            continue
        files = folder.glob("tunnel*.json")
        for path in sorted(files, key=lambda p: (p.stem != "tunnel", p.name)):
            try:
                rec = load(path, {})
            except (OSError, ValueError):
                continue
            proc = machines.owned_tunnel(rec)
            if not proc:
                continue
            far = path.stem.removeprefix("tunnel").lstrip("-")
            rows.append(
                dict(
                    id=":".join(filter(None, ["tunnel", host, far])),
                    kind="tunnel",
                    title=clean(" · ".join(filter(None, [text(label) or host, far]))),
                    harness="",
                    workspace="",
                    pid=proc.pid,
                    started=number(rec.get("born")),
                    state="running",
                    host=host,
                    port=rec.get("port"),
                )
            )
    return rows


def running(project=None):
    import session_window

    rows, pids, attached = [], set(), set()
    live = [terminal_row(t) for t in docked()]
    for row in live + [native_row(r) for r in session_window.active_sessions()]:
        if row["pid"] not in pids:
            pids.add(row["pid"])
            attached.add(row.get("name"))
            rows.append(row)
    rows += [
        remote_row(name, rec, "unverified")
        for name, rec in remote_rows().items()
        if name not in attached
    ]
    if project:
        rows = [r for r in rows if same_folder(r["workspace"], project)]
    return dict(
        sessions=rows + model_rows() + tunnel_rows(),
        exits=session_window.recent_exits(),
    )


def over_ssh(host, command):
    import machines

    host = machines.target(str(host).strip())
    try:
        names = machines.labels()
    except (OSError, ValueError):
        names = {}
    if not isinstance(names, dict) or host not in names:
        raise ValueError("Save this SSH machine first.")
    label = text(names[host]) or host
    try:
        done = subprocess.run(
            machines.ssh(host) + [host, command],
            capture_output=True,
            text=True,
            encoding="utf8",
            errors="replace",
            timeout=12,
            stdin=subprocess.DEVNULL,
            creationflags=QUIET,
        )
    except subprocess.TimeoutExpired:
        raise ValueError(
            f"SSH to {label} timed out after 12 seconds; check the network and pairing, then retry."
        ) from None
    except FileNotFoundError:
        raise ValueError(machines.NO_SSH) from None
    if done.returncode == 255:
        raise ValueError(
            f"Could not reach {label} over SSH; check it in Machines, then retry."
        )
    return host, done


def _windows(host):
    """A Windows machine has no tmux: its sessions are the dock's own terminals, nothing runs detached."""
    import machines

    host = machines.target(str(host).strip())
    try:
        return host if machines.windows(host) else ""
    except (OSError, ValueError):
        return ""


def remote(host):
    if _windows(host):
        host = _windows(host)
        return [
            remote_row(
                t["name"],
                dict(host=host, harness=t["harness"], workspace=t["workspace"], started=int(t["started"])),
                "attached",
            )
            for t in docked()
            if t["kind"] == "remote" and t["host"] == host and t.get("lasting") is False
        ]
    host, done = over_ssh(host, LIST)
    known = {n: r for n, r in remote_rows().items() if r.get("host") == host}
    rows, live = [], set()
    for line in done.stdout.splitlines():
        found = TMUX_LINE.fullmatch(line.strip())
        if not found or found.group(1) in live:
            continue
        name = found.group(1)
        live.add(name)
        rec = known.get(name, {}) | dict(host=host, started=int(found.group(2)))
        state = "attached" if int(found.group(3)) else "detached"
        rows.append(remote_row(name, rec, state))
    if done.returncode == 0:
        # A session the dock is still connecting to is not in tmux's list yet.
        forget_remote(set(known) - live - {t.get("name") for t in docked()})
    return rows


def end_remote(host, name):
    if not isinstance(name, str) or not TMUX_NAME.fullmatch(name):
        raise ValueError("Choose a remote session from the list.")
    if _windows(host):
        import project_terminal

        for t in docked():
            if t["kind"] == "remote" and t["host"] == _windows(host) and t["name"] == name:
                project_terminal.dispatch("term-close", {"workspace": t["workspace"], "id": t["id"]})
        return True
    over_ssh(host, KILL + name)
    forget_remote([name])
    return True


def dispatch(action, p):
    if action == "sessions-running":
        return running(project_dir(p["workspace"]) if p.get("workspace") else None)
    if action == "sessions-recent":
        import connections

        harness = p.get("harness") or None
        if harness is not None and harness not in connections.HARNESS:
            raise ValueError("Choose a supported agent.")
        try:
            limit = max(1, min(80, int(p.get("limit", 20))))
        except (TypeError, ValueError, OverflowError):
            limit = 20
        return recent(project_dir(p.get("workspace")), harness, limit)
    if action == "sessions-remote":
        return remote(p.get("host", ""))
    if action == "sessions-end":
        return end_remote(p.get("host", ""), p.get("name"))
    raise ValueError("Unknown sessions action.")
