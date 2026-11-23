"""Chat-template compatibility for llama.cpp: read a GGUF's embedded template and, when it
refuses a system message after the first turn, serve a copy that renders one instead.

Claude Code sends a system-role message after the first user message and Codex sends
developer messages, which several ChatML templates reject with a server error. Only the
header of the model file is read, with size limits, because model files are untrusted.
"""

import hashlib
import os
from pathlib import Path
import struct
import tempfile

KEY = "tokenizer.chat_template"
LATE_SYSTEM = "raise_exception('System message must be at the beginning.')"
CHATML_TURN = "'<|im_start|>system\\n' + content + '<|im_end|>\\n'"
SCALARS = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
STRING, ARRAY = 8, 9
MAX_KEYS = 1 << 20
MAX_KEY_BYTES = 1 << 16
MAX_TEMPLATE_BYTES = 1 << 20
MAX_SKIP_BYTES = 1 << 32


class _Malformed(Exception):
    pass


def _take(f, size):
    data = f.read(size)
    if len(data) != size:
        raise _Malformed
    return data


def _count(f):
    return struct.unpack("<Q", _take(f, 8))[0]


def _seek(f, size):
    if size > MAX_SKIP_BYTES:
        raise _Malformed
    f.seek(size, os.SEEK_CUR)


def _skip(f, kind, depth=0):
    if kind in SCALARS:
        _seek(f, SCALARS[kind])
    elif kind == STRING:
        _seek(f, _count(f))
    elif kind == ARRAY and depth < 4:
        inner = struct.unpack("<I", _take(f, 4))[0]
        n = _count(f)
        if inner in SCALARS:
            _seek(f, SCALARS[inner] * n)
        elif inner == STRING:
            for _ in range(n):
                _seek(f, _count(f))
        else:
            for _ in range(n):
                _skip(f, inner, depth + 1)
    else:
        raise _Malformed


def embedded(path):
    """The chat template stored in a GGUF file's metadata, or None."""
    try:
        with open(path, "rb") as f:
            if _take(f, 4) != b"GGUF" or struct.unpack("<I", _take(f, 4))[0] < 2:
                return None
            _count(f)
            keys = _count(f)
            if keys > MAX_KEYS:
                return None
            for _ in range(keys):
                size = _count(f)
                if size > MAX_KEY_BYTES:
                    return None
                name = _take(f, size).decode("utf8", "replace")
                kind = struct.unpack("<I", _take(f, 4))[0]
                if name == KEY and kind == STRING:
                    size = _count(f)
                    if size > MAX_TEMPLATE_BYTES:
                        return None
                    return _take(f, size).decode("utf8", "replace")
                _skip(f, kind)
    except (OSError, _Malformed, struct.error, OverflowError, ValueError):
        return None
    return None


def late_system_fix(template):
    """The template with a late system message rendered as a ChatML turn, or None."""
    if (
        not template
        or template.count(LATE_SYSTEM) != 1
        or "<|im_start|>" not in template
        or "set content" not in template
    ):
        return None
    return template.replace(LATE_SYSTEM, CHATML_TURN)


def override(model, folder):
    """Path of a corrected template for this model, written once into folder, or None."""
    fixed = late_system_fix(embedded(model))
    if fixed is None:
        return None
    folder = Path(folder)
    target = folder / (hashlib.sha256(fixed.encode("utf8")).hexdigest()[:16] + ".jinja")
    if target.is_file():
        return target
    folder.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=folder, prefix=target.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf8", newline="\n") as out:
            out.write(fixed)
        os.replace(temp, target)
    except OSError:
        Path(temp).unlink(missing_ok=True)
        return None
    return target
