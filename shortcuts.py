"""Export a launcher for a saved setup using an opaque, stable argument."""

import hashlib
import os
import re
import shlex
import sys
from runtime import ROOT, DATA


def ident(name):
    return hashlib.sha256(name.encode()).hexdigest()[:20]


def windows_script(root, data, setup, carry):
    def literal(path, called=False):
        # cmd.exe expands percent signs once per line, and once more on a line that begins with call.
        return str(path).replace("%", "%%%%" if called else "%%")

    lines = ["@echo off", "chcp 65001 >nul"]
    if carry:
        lines.append(f'set "WORKBENCH_DATA={literal(data)}"')
    run, script = (literal(root / name, True) for name in ("Run.cmd", "cli.py"))
    lines.append(f'call "{run}" "{script}" agent --here --setup-id {setup}')
    lines.append("if errorlevel 1 pause")
    return "\n".join(lines) + "\n"


def posix_script(python, root, data, setup, carry):
    lines = ["#!/bin/sh"]
    if carry:
        lines.append("export WORKBENCH_DATA=" + shlex.quote(str(data)))
    lines.append(
        "exec "
        + shlex.join(
            [str(python), str(root / "cli.py"), "agent", "--here", "--setup-id", setup]
        )
    )
    return "\n".join(lines) + "\n"


def create(name):
    label = re.sub(r"[^\w -]", "", name).strip()[:60] or "Agent"
    folder = DATA / "launchers"
    folder.mkdir(exist_ok=True)
    stem = label + "-" + ident(name)[:5]
    carry = bool(os.environ.get("WORKBENCH_DATA"))
    if os.name == "nt":
        path = folder / (stem + ".cmd")
        path.write_text(
            windows_script(ROOT, DATA, ident(name), carry), encoding="utf8"
        )
    else:
        path = folder / (stem + (".command" if sys.platform == "darwin" else ".sh"))
        path.write_text(
            posix_script(sys.executable, ROOT, DATA, ident(name), carry),
            encoding="utf8",
        )
        path.chmod(0o700)
    return path
