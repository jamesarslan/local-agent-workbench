"""Exercise OMP's read/edit loop in fresh workspaces with and without the workbench supplement."""

import argparse
import json
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from runtime import DATA, omp_command, settings, start, write_json


def check(s, supplement, case):
    with tempfile.TemporaryDirectory(dir=DATA) as tmp:
        folder = Path(tmp)
        code = secrets.token_hex(4)
        original = {
            "project": "fixture",
            "jobs": [{"name": "render", "limit": 4}, {"name": "export", "limit": 2}],
            "marker": code,
        }
        expected = json.loads(json.dumps(original))
        expected["jobs"][case]["limit"] = 7
        target = folder / "jobs.json"
        target.write_text(json.dumps(original, indent=2) + "\n", encoding="utf8")
        cmd = omp_command(
            s
            | {
                "workspace": str(folder),
                "search": False,
                "firecrawl": False,
                "desktop": False,
                "blender": False,
            }
        )
        if not supplement:
            at = cmd.index("--append-system-prompt")
            del cmd[at : at + 2]
        name = original["jobs"][case]["name"]
        prompt = f"Read jobs.json. Change only the {name} job limit to 7 using the edit tool. Read it back and reply with only the marker value."
        t = time.perf_counter()
        try:
            r = subprocess.run(
                cmd
                + [
                    "--tools",
                    "read,edit",
                    "--no-extensions",
                    "--no-skills",
                    "--no-context-files",
                    "--no-session",
                    "--max-time",
                    "90",
                    "--mode",
                    "json",
                    "-p",
                    prompt,
                ],
                capture_output=True,
                timeout=110,
                encoding="utf8",
                errors="replace",
            )
            events = []
            for line in r.stdout.splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
            calls = [
                e.get("toolName")
                for e in events
                if isinstance(e, dict) and e.get("type") == "tool_execution_start"
            ]
            assistant = [
                e["message"]
                for e in events
                if isinstance(e, dict)
                and e.get("type") == "message_end"
                and e.get("message", {}).get("role") == "assistant"
            ]
            final = "".join(
                b.get("text", "")
                for b in (assistant[-1].get("content", []) if assistant else [])
                if isinstance(b, dict) and b.get("type") == "text"
            )
            changed = json.loads(target.read_text(encoding="utf8")) == expected
            return dict(
                job=name,
                supplement=supplement,
                elapsed_s=round(time.perf_counter() - t, 3),
                returncode=r.returncode,
                tools=calls,
                correct_file=changed,
                correct_reply=final.strip() == code,
                passed=changed and final.strip() == code and r.returncode == 0,
            )
        except (ValueError, subprocess.TimeoutExpired) as err:
            return dict(
                job=name, supplement=supplement, passed=False, error=type(err).__name__
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    s = settings()
    start(s)
    runs = [
        check(s, use_prompt, case)
        for case, order in enumerate([(False, True), (True, False)])
        for use_prompt in order
    ]
    write_json(args.output, runs)
    print(json.dumps(runs, indent=2))
