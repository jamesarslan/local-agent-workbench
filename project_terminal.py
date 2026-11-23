"""Interactive PTYs scoped to project folders, served only over Electron's inherited IPC."""

import atexit
import codecs
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import signal
import sys
import threading
import time

import connections
from host import safe_argv
import runtime
import sessions

if os.name == "nt":
    from winpty import PtyProcess as Pty

    GONE = (OSError, EOFError, ValueError)
else:
    from ptyprocess import PtyProcess as Pty, PtyProcessError

    GONE = (OSError, EOFError, ValueError, PtyProcessError)

LOCK = threading.Lock()
SESSIONS = {}
LIMIT = 256 * 1024
SLACK = 64 * 1024
CAP = 24
REACH = 4096
READ_CHUNK = 32768
WRITE_WAIT = 2
SYNC_BEGIN = "\x1b[?2026h"
SYNC_END = "\x1b[?2026l"
SYNC_HOLD = 0.25
ESC_HOLD = 0.05
ENDED = "This terminal has ended; open a new one."
WAITING = "The program in this terminal has not read your last input yet; wait for it, or close the terminal."
STRIP = (
    "TERM_PROGRAM",
    "TERM_PROGRAM_VERSION",
    "TERM_SESSION_ID",
    "ITERM_SESSION_ID",
    "ITERM_PROFILE",
    "WORKBENCH_CALLER_CWD",
)
MODES = (1, 25, 1000, 1002, 1003, 1004, 1005, 1006, 1015, 1047, 1049, 2004)
MODE_RE = re.compile(r"\x1b\[\?([0-9;]{1,40})([hl])")


def safe_cut(text, cut):
    """A cut position at or before cut that does not split an escape sequence."""
    i = text.rfind("\x1b", max(0, cut - REACH), cut)
    if i < 0:
        return cut
    tail = text[i:cut]
    if len(tail) < 2:
        return i
    if tail[1] == "[":
        return cut if any("@" <= c <= "~" for c in tail[2:]) else i
    if tail[1] == "]":
        return cut if "\x07" in tail else i
    if tail[1] in "P_^X":
        return i
    if " " <= tail[1] <= "/":
        return cut if len(tail) > 2 else i
    return cut


def stable_end(text, idle):
    """End offset that avoids splitting an in-flight synchronized frame or trailing escape sequence."""
    end = len(text)
    if not end:
        return 0
    if idle < SYNC_HOLD:
        opened = text.rfind(SYNC_BEGIN)
        if opened >= 0 and opened > text.rfind(SYNC_END):
            end = opened
    if end and idle < ESC_HOLD:
        end = safe_cut(text, end)
    return end


class Session:
    def __init__(
        self, argv, env, project, title, harness=None, kind="embedded", host="", name=""
    ):
        self.harness, self.kind, self.host, self.name = harness, kind, host, name
        self.project = str(Path(project).resolve())
        self.title = title
        self.output, self.offset, self.ended = "", 0, False
        self.last_read = 0.0
        self.modes = {}
        self.size = (20, 96)
        self.started = time.time()
        self.lock = threading.Lock()
        self.closing = threading.Lock()
        self.writing = threading.Lock()
        self.done = False
        self.stop = threading.Event()
        env = {k: v for k, v in env.items() if k not in STRIP} | {
            "TERM": "xterm-256color"
        }
        self.proc = Pty.spawn(
            safe_argv(argv), cwd=self.project, env=env, dimensions=self.size
        )
        self.reader = threading.Thread(target=self.collect, daemon=True)
        self.reader.start()

    def collect(self):
        decoder = codecs.getincrementaldecoder("utf8")(errors="replace")
        try:
            while not self.stop.is_set():
                # Avoid holding BufferedReader’s lock while another thread closes the PTY.
                chunk = (
                    self.proc.read(READ_CHUNK)
                    if os.name == "nt"
                    else os.read(self.proc.fd, READ_CHUNK)
                )
                if not chunk:
                    # macOS returns an empty read once no process holds the terminal; Linux raises EIO instead.
                    if self.stop.is_set() or not self.proc.isalive():
                        break
                    time.sleep(0.05)
                    continue
                if isinstance(chunk, bytes):
                    chunk = decoder.decode(chunk)
                with self.lock:
                    self.output += chunk
                    self.last_read = time.monotonic()
                    self.trim()
        except GONE:
            pass
        finally:
            with self.lock:
                self.ended = True

    def trim(self):
        excess = len(self.output) - LIMIT
        if excess < SLACK:
            return
        cut = safe_cut(self.output, excess)
        for found in MODE_RE.finditer(self.output, 0, cut):
            for number in found.group(1).split(";"):
                if number.isdigit() and int(number) in MODES:
                    self.modes[int(number)] = found.group(2) == "h"
        self.output = self.output[cut:]
        self.offset += cut

    def snapshot(self, cursor):
        with self.lock:
            reset = cursor < self.offset
            start = max(0, cursor - self.offset)
            text = self.output[start:]
            end = len(text)
            if not reset and not self.ended and text:
                last = getattr(self, "last_read", 0.0)
                idle = time.monotonic() - last if last else SYNC_HOLD
                end = stable_end(text, idle)
                text = text[:end]
            next_offset = self.offset + start + end
            if reset:
                text = (
                    "".join(
                        f"\x1b[?{n}{'h' if on else 'l'}"
                        for n, on in sorted(self.modes.items())
                    )
                    + text
                )
            out = dict(
                text=text,
                offset=next_offset,
                ended=self.ended,
                reset=reset,
            )
            repaint = (
                reset
                and not self.ended
                and bool(
                    self.harness or self.modes.get(1049) or self.modes.get(1047)
                )
            )
        if repaint:
            self.nudge()
        return out

    def resize(self, rows, cols):
        self.size = (max(4, min(120, int(rows))), max(16, min(400, int(cols))))
        self.proc.setwinsize(*self.size)

    def nudge(self):
        rows, cols = self.size
        try:
            self.proc.setwinsize(rows, cols + 1)
        except Exception:
            return
        timer = threading.Timer(0.08, self.settle)
        timer.daemon = True
        timer.start()

    def settle(self):
        try:
            self.proc.setwinsize(*self.size)
        except Exception:
            pass

    def write(self, text):
        """Send input; a program that is not reading it must not hold the caller."""
        data = text if os.name == "nt" else text.encode("utf8", "replace")
        if self.done:
            raise ValueError(ENDED)
        if not self.writing.acquire(timeout=WRITE_WAIT):
            raise ValueError(WAITING)
        failed = []

        def send():
            try:
                self.proc.write(data)
            except Exception as exc:
                failed.append(exc)
            finally:
                self.writing.release()

        try:
            worker = threading.Thread(target=send, daemon=True)
            worker.start()
        except BaseException:
            self.writing.release()
            raise
        worker.join(WRITE_WAIT)
        if worker.is_alive():
            raise ValueError(WAITING)
        if failed:
            if isinstance(failed[0], GONE):
                raise ValueError(ENDED) from None
            raise failed[0]

    def hangup(self):
        self.stop.set()
        if os.name != "nt":
            try:
                if self.proc.isalive():
                    self.proc.kill(signal.SIGHUP)
            except GONE:
                pass

    def close(self):
        with self.closing:
            if self.done:
                return
            self.done = True
            self.stop.set()
            if os.name == "nt":
                self.proc.close(force=True)
                return
            # macOS blocks close() on a PTY master while another thread is inside read() on it,
            # so the child ends first and the reader lets go of the descriptor before it is closed.
            try:
                if self.proc.terminate(force=True):
                    self.proc.delayafterclose = 0
            except GONE:
                pass
            self.reader.join(1.5)
            if self.reader.is_alive() and sys.platform == "darwin":
                return
            try:
                self.proc.close(force=True)
            except GONE:
                pass


class Conversation:
    """A read-only view of newly saved OMP events. Session files stay on disk."""

    def __init__(self, project, item):
        self.project = project
        self.path, title, _ = item
        self.title = "Watch · " + title
        self.ended = False
        self.pos = max(0, self.path.stat().st_size - 131072)
        self.partial = bool(self.pos)
        self.output = ""
        self.offset = 0
        self.lock = threading.Lock()

    def snapshot(self, cursor):
        with self.lock, self.path.open("rb") as f:
            f.seek(self.pos)
            for _ in range(100):
                line = f.readline(262144)
                if not line:
                    break
                if not line.endswith(b"\n") and len(line) < 262144:
                    break
                self.pos += len(line)
                if self.partial or not line.endswith(b"\n"):
                    self.partial = not line.endswith(b"\n")
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                msg = event.get("message") or {}
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role")
                if role not in ("assistant", "user", "toolResult"):
                    continue
                body = msg.get("content")
                if role == "toolResult":
                    text = str(msg.get("toolName", "Tool")) + (
                        " failed" if msg.get("isError") else " complete"
                    )
                elif isinstance(body, str):
                    text = body
                elif isinstance(body, list):
                    text = " ".join(
                        str(v.get("text") or v.get("name") or "")
                        for v in body
                        if isinstance(v, dict) and v.get("type") in ("text", "toolCall")
                    )
                else:
                    continue
                # Transcripts are untrusted text. Keep their escape sequences out of xterm.
                safe = "".join(
                    c if c.isprintable() or c == "\n" else " " for c in text[:3000]
                ).strip()
                if safe:
                    self.output += role + "  " + safe.replace("\n", "\r\n") + "\r\n\r\n"
            excess = max(0, len(self.output) - LIMIT)
            self.offset += excess
            self.output = self.output[excess:]
            return dict(
                text=self.output[max(0, cursor - self.offset) :],
                offset=self.offset + len(self.output),
                ended=False,
                reset=cursor < self.offset,
            )

    def close(self):
        self.ended = True


@atexit.register
def close_sessions():
    with LOCK:
        items = list(SESSIONS.values())
        SESSIONS.clear()
    for session in items:
        if isinstance(session, Session):
            session.hangup()
    for session in items:
        try:
            session.close()
        except Exception:
            pass


def active_agents():
    with LOCK:
        return [
            dict(pid=s.proc.pid, workspace=s.project, harness=s.harness)
            for s in SESSIONS.values()
            if isinstance(s, Session) and s.harness and not s.ended
        ]


def active():
    with LOCK:
        items = list(SESSIONS.items())
    return [
        dict(
            id=k,
            kind=s.kind,
            harness=s.harness or "",
            title=s.title,
            workspace=s.project,
            pid=s.proc.pid,
            started=s.started,
            host=s.host,
            name=s.name,
        )
        for k, s in items
        if isinstance(s, Session) and not s.ended
    ]


def admit():
    """Make room for one more terminal. Call with LOCK held, before inserting."""
    if len(SESSIONS) < CAP:
        return
    stale = next((k for k, s in SESSIONS.items() if s.ended), None)
    if stale is None:
        raise ValueError(
            f"{CAP} terminals are open across your projects; close one before opening another."
        )
    try:
        SESSIONS.pop(stale).close()
    except Exception:
        pass


def dispatch(action, p):
    project = sessions.project_dir(p.get("workspace"))
    if action == "term-history":
        harness = p.get("harness") or "OMP"
        if harness not in connections.HARNESS:
            raise ValueError("Choose a supported agent.")
        return [
            dict(
                id=r["id"],
                title=r["title"],
                date=r["date"],
                updated=r["updated"],
                harness=r["harness"],
                store=r["store"],
                command=r["command"],
            )
            for r in sessions.recent(project, harness, 80)
        ]
    if action == "term-list":
        with LOCK:
            return [
                dict(
                    id=k,
                    title=s.title,
                    ended=s.ended,
                    watch=isinstance(s, Conversation),
                    kind=getattr(s, "kind", "watch"),
                    harness=getattr(s, "harness", None) or "",
                )
                for k, s in SESSIONS.items()
                if s.project == project
            ]
    if action == "term-open" and p.get("kind") == "watch":
        row = sessions.find(project, p.get("saved"))
        if not row or row["harness"] != "OMP" or not row["path"]:
            raise ValueError("Choose a saved OMP session in this project.")
        item = (Path(row["path"]), row["title"], row["date"])
        with LOCK:
            for ident, session in SESSIONS.items():
                if (
                    isinstance(session, Conversation)
                    and session.project == project
                    and session.path == item[0]
                ):
                    return ident
            admit()
            ident = secrets.token_hex(12)
            SESSIONS[ident] = Conversation(project, item)
        return ident
    if action in ("term-open", "term-remote"):
        agent = None
        where = {}
        env = os.environ.copy()
        if action == "term-remote":
            import machines

            host = machines.target(str(p.get("host") or "").strip())
            if host not in machines.labels():
                raise ValueError("Save this SSH machine first.")
            folder = str(p.get("folder") or ".")
            harness = str(p.get("harness") or "OMP")
            mode = str(p.get("mode") or "native")
            remote = machines.remote_command(host, folder, harness, mode)
            name = (
                "awb-"
                + hashlib.sha256(
                    repr((project, host, folder, harness, mode)).encode()
                ).hexdigest()[:16]
            )
            tmux = shlex.join(
                [
                    "tmux",
                    "-L",
                    "agent-workbench",
                    "new-session",
                    "-A",
                    "-s",
                    name,
                    remote,
                ]
            )
            agent = harness if mode != "shell" else None
            where = dict(kind="remote", host=host, name=name)
            argv = machines.ssh(host) + ["-tt", host, tmux]
            title = f"{machines.labels()[host]} · {'Shell' if mode == 'shell' else harness} · {folder}"
        elif p.get("kind") == "shell":
            exe = (
                (shutil.which("pwsh") or "powershell.exe")
                if os.name == "nt"
                else os.getenv("SHELL") or "/bin/sh"
            )
            argv = [exe, "-l"] if sys.platform == "darwin" else [exe]
            title = Path(project).name + " · Shell"
        else:
            from desktop_bridge import selection, choices

            base = runtime.settings()
            s = selection(base, p, choices(base))
            agent = s["harness"]
            saved = p.get("saved") if p.get("kind") == "resume" else None
            row = sessions.find(project, saved) if saved else None
            if saved and not row:
                raise ValueError(
                    "This session is no longer available in this project; pick another one."
                )
            argv, env, _ = connections.ready_session(s)
            if p.get("kind") == "resume":
                argv, env = sessions.resume(s["harness"], argv, env, row)
            title = (
                s["harness"]
                + " · "
                + (
                    Path(s["model"]).name
                    if s["provider"] == "Managed local"
                    else s["remote_model"] or s["provider"]
                )
            )
        ident = secrets.token_hex(12)
        with LOCK:
            admit()
            SESSIONS[ident] = Session(argv, env, project, title, agent, **where)
        if action == "term-remote":
            # The terminal is open; a failed registry write must not lose its id.
            try:
                sessions.remember_remote(
                    name,
                    host=host,
                    folder=folder,
                    harness=agent or "",
                    mode=mode,
                    workspace=project,
                )
            except Exception:
                pass
        return ident
    with LOCK:
        session = SESSIONS.get(p.get("id"))
    if not session or session.project != project:
        raise ValueError("Choose a terminal in this project.")
    if action == "term-read":
        return session.snapshot(max(0, int(p.get("offset", 0))))
    if isinstance(session, Conversation) and action in ("term-write", "term-size"):
        return False
    if action == "term-write":
        data = p["text"]
        if len(data) > 65536:
            raise ValueError("Paste up to 64 KiB at once.")
        session.write(data)
    elif action == "term-size":
        try:
            session.resize(p["rows"], p["cols"])
        except Exception:
            return False
    elif action == "term-close":
        try:
            session.close()
        finally:
            with LOCK:
                SESSIONS.pop(p["id"], None)
    else:
        raise ValueError("Unknown terminal action.")
    return True
