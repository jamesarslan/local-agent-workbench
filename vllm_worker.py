"""Linux vLLM entrypoint used directly or via the dedicated WSL distribution."""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

USAGE = (
    "Usage: vllm_worker.py serve TAG MODEL [vllm options], or vllm_worker.py stop TAG."
)


def executable():
    chosen = os.getenv("WORKBENCH_VLLM")
    if chosen:
        return chosen
    managed = [
        Path("/opt/agent-workbench/vllm/bin/vllm"),
        Path.home() / ".local/share/agent-workbench/vllm/bin/vllm",
    ]
    found = next((str(p) for p in managed if p.exists()), None)
    if found:
        return found
    linked = Path.home() / ".local/bin/vllm"
    return shutil.which("vllm") or (str(linked) if linked.exists() else None)


def started(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def end_group(pid):
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if len(argv) < 2:
        raise SystemExit(USAGE)
    if not sys.platform.startswith("linux"):
        raise SystemExit(
            "vLLM runs on Linux or inside WSL; on this computer attach a running "
            "endpoint instead."
        )
    action, tag, *args = argv
    if not tag.isalnum():
        raise ValueError("Invalid run ID")
    if action not in ("serve", "stop"):
        raise SystemExit(USAGE)
    record = Path.home() / ".cache/agent-workbench" / (tag + ".json")
    record.parent.mkdir(parents=True, exist_ok=True)
    if action == "stop":
        try:
            pid, born = json.loads(record.read_text())
            if started(pid) == born:
                end_group(pid)
        except (OSError, ValueError, TypeError, IndexError):
            pass
        record.unlink(missing_ok=True)
        return
    exe = executable()
    if not exe:
        raise SystemExit(
            "vLLM is not installed; open Advanced → Setup → vLLM → Install."
        )
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    p = subprocess.Popen([exe, "serve", *args], start_new_session=True)
    try:
        record.write_text(json.dumps([p.pid, started(p.pid)]))
        raise SystemExit(p.wait())
    finally:
        if p.poll() is None:
            end_group(p.pid)
        record.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
