"""Standalone SSH helper: the model servers a Workbench on this Linux or macOS machine runs. Python 3.8+.

Usage: python3 - info PORT[,PORT...]  or  python3 - key PORT

A port counts as Workbench's when the process listening on it, or that process's
parent, is the one recorded in running.json of a Workbench data folder, for the same
port. The folder comes from the listener's own command line (--api-key-file or
--config) or the install scripts' default data folder. "info" prints the model alias
and context of each such port; "key" prints the API key of one, which only the SSH
user who owns that folder can read. Nothing here opens a network connection.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

ALIAS = "local-agent"
KEY = re.compile(r"[A-Za-z0-9_-]{16,200}")
FLAGS = ("--api-key-file", "--config")
TEXT_FLAGS = re.compile(r"--(?:api-key-file|config) (.+?(?:api-key\.txt|serve\.json))(?= --| *$)")


def _run(argv):
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _inodes(port):
    found = set()
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            rows = Path(name).read_text().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            f = row.split()
            try:
                if len(f) > 9 and f[3] == "0A" and int(f[1].rsplit(":", 1)[1], 16) == port:
                    found.add(f[9])
            except ValueError:
                continue
    return found


def listeners(port):
    """Process IDs of this user's processes listening on the port."""
    if Path("/proc/net/tcp").exists():
        inodes, pids = _inodes(port), set()
        if not inodes:
            return pids
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                for fd in (proc / "fd").iterdir():
                    link = os.readlink(fd)
                    if link.startswith("socket:[") and link[8:-1] in inodes:
                        pids.add(int(proc.name))
                        break
            except OSError:
                continue
        return pids
    out = _run(["lsof", "-nP", "-a", "-iTCP:%d" % port, "-sTCP:LISTEN", "-t"])
    return {int(v) for v in out.split() if v.isdigit()}


def parent(pid):
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        out = _run(["ps", "-o", "ppid=", "-p", str(pid)]).strip()
        return int(out) if out.isdigit() else 0


def folders_of(pid):
    try:
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
        argv = argv.split("\0")
        return [
            str(Path(argv[i + 1]).parent)
            for i, v in enumerate(argv[:-1])
            if v in FLAGS and argv[i + 1]
        ]
    except OSError:
        line = _run(["ps", "-ww", "-o", "args=", "-p", str(pid)]).strip()
        return [str(Path(m).parent) for m in TEXT_FLAGS.findall(line)]


def defaults():
    home = Path.home()
    xdg = os.environ.get("XDG_DATA_HOME") or str(home / ".local/share")
    return [
        str(home / "Library/Application Support/Agent Workbench"),
        str(Path(xdg) / "agent-workbench"),
    ]


def server(port):
    """(data folder, running record) of the Workbench server on this port, or (None, None)."""
    owners, folders = set(), []
    for pid in listeners(port):
        owners.update({pid, parent(pid)})
        folders += folders_of(pid)
    owners.discard(0)
    if not owners:
        return None, None
    for folder in dict.fromkeys(folders + defaults()):
        try:
            rec = json.loads((Path(folder) / "running.json").read_text(encoding="utf8"))
        except (OSError, ValueError):
            continue
        if (
            isinstance(rec, dict)
            and rec.get("pid") in owners
            and rec.get("port") == port
        ):
            return Path(folder), rec
    return None, None


def info(ports):
    rows = []
    for port in ports:
        folder, rec = server(port)
        if folder is None:
            continue
        spec = rec.get("spec") if isinstance(rec.get("spec"), dict) else {}
        size = spec.get("context")
        rows.append(
            dict(
                port=port,
                model=ALIAS,
                context=size if isinstance(size, int) and not isinstance(size, bool) else None,
                engine=rec.get("engine") if isinstance(rec.get("engine"), str) else "",
            )
        )
    return {"servers": rows}


def key(port):
    folder, _ = server(port)
    if folder is None:
        return {"key": None}
    try:
        text = (folder / "api-key.txt").read_text(encoding="utf8").strip()
    except OSError:
        return {"key": None}
    return {"key": text if KEY.fullmatch(text) else None}


def main(argv):
    if len(argv) != 2 or argv[0] not in ("info", "key"):
        raise SystemExit("usage: info PORT[,PORT...] | key PORT")
    ports = [int(p) for p in argv[1].split(",") if p.isdigit() and 0 < int(p) < 65536][:16]
    if not ports:
        raise SystemExit("no port")
    out = info(ports) if argv[0] == "info" else key(ports[0])
    print(json.dumps(out))


if __name__ == "__main__":
    main(sys.argv[1:])
