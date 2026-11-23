"""One canonical form for project folder paths, and guarded joins inside a project."""

import ntpath
import os
from pathlib import Path
import re
import stat
import sys

BAD_FOLDER = "Choose an existing project folder."
RELATIVE = "Enter the full path of the project folder, starting from the drive or root."
BAD_NAME = "That file name is not allowed inside a project."
UNREACHABLE = "{} cannot be reached. Check the permissions of its folder, then try again."
SEGMENT = r"[A-Za-z0-9._-]{1,100}"
DEVICES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{n}" for n in range(1, 10)]
    + [f"LPT{n}" for n in range(1, 10)]
)


def _true_case(text):
    import fcntl

    if not hasattr(fcntl, "F_GETPATH"):
        return text
    try:
        fd = os.open(text, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return text
    try:
        found = fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).rstrip(b"\0")
    except OSError:
        return text
    finally:
        os.close(fd)
    return os.fsdecode(found) if found else text


def _resolved(text):
    text = os.path.expanduser(text)
    if not os.path.isabs(text):
        raise ValueError(RELATIVE)
    try:
        p = Path(text).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise ValueError(BAD_FOLDER) from None
    return Path(_true_case(str(p))) if sys.platform == "darwin" else p


def canonical(path, folder=True):
    """The path as typed when it exists; surrounding spaces are dropped only when it does not."""
    if not isinstance(path, (str, os.PathLike)):
        raise ValueError(BAD_FOLDER)
    text = os.fspath(path)
    if not isinstance(text, str) or not text.strip():
        raise ValueError(BAD_FOLDER)
    try:
        p = _resolved(text)
    except ValueError:
        if text == text.strip():
            raise
        p = _resolved(text.strip())
    if folder and not p.is_dir():
        raise ValueError(BAD_FOLDER)
    return p


def fold(text, windows=None):
    if windows is None:
        windows = os.name == "nt"
    return ntpath.normcase(text) if windows else text


def project_key(path):
    return fold(str(canonical(path)))


def try_key(path):
    try:
        return fold(str(canonical(path, folder=False)))
    except ValueError:
        return None


def same(a, b):
    key = try_key(a)
    return key is not None and key == try_key(b)


def _segments(parts):
    found = []
    for part in parts:
        if not isinstance(part, str):
            raise ValueError(BAD_NAME)
        found.extend(part.split("/"))
    for name in found:
        if (
            not re.fullmatch(SEGMENT, name)
            or name in (".", "..")
            or name.endswith((".", " "))
            or name.split(".")[0].upper() in DEVICES
        ):
            raise ValueError(BAD_NAME)
    if not found:
        raise ValueError(BAD_NAME)
    return found


def inside(root, *parts):
    names = _segments(parts)
    path = Path(root)
    for index, name in enumerate(names):
        path = path / name
        rel = "/".join(names[: index + 1])
        try:
            mode = os.lstat(path).st_mode
        except (FileNotFoundError, NotADirectoryError):
            mode = None
        except (OSError, ValueError):
            raise ValueError(UNREACHABLE.format("/".join(names))) from None
        if (mode is not None and stat.S_ISLNK(mode)) or path.is_junction():
            raise ValueError(
                f"{rel} goes through a link, so Workbench will not write there. "
                "Replace the link with a real folder or file."
            )
        if index < len(names) - 1 and mode is not None and not stat.S_ISDIR(mode):
            raise ValueError(
                f"{rel} is a file, so Workbench cannot create a folder there. "
                "Move it, then try again."
            )
    return path
