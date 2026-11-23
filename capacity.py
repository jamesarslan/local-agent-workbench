"""Measure long-context recall on the selected profile and save observed memory and timing."""

import argparse
from pathlib import Path
import threading
import time
import requests
from runtime import LOOPBACK, URL, ALIAS, key, write_json
from catalog import hardware


def measure(lines, timeout=900):
    messages = "".join(
        f"Log entry {i} completed with state pending.\n" for i in range(lines // 2)
    )
    tail = "".join(
        f"Log entry {i} completed with state pending.\n"
        for i in range(lines // 2, lines)
    )
    prompt = (
        "Read the log and give only the value of field final_code.\n"
        + messages
        + "final_code: SKY-07382\n"
        + tail
        + "What was final_code?"
    )
    peak = [0.0]
    finished = threading.Event()

    def sample():
        while not finished.is_set():
            _, total, free, _ = hardware()
            peak[0] = max(peak[0], total - free)
            finished.wait(2)

    monitor = threading.Thread(target=sample, daemon=True)
    monitor.start()
    t = time.perf_counter()
    try:
        r = LOOPBACK.post(
            URL + "/v1/chat/completions",
            headers={"Authorization": "Bearer " + key()},
            json={
                "model": ALIAS,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 512,
                "temperature": 0,
            },
            timeout=timeout,
        )
        r.raise_for_status()
        j = r.json()
        answer = j["choices"][0]["message"].get("content") or ""
        return dict(
            lines=lines,
            answer=answer,
            passed=answer.strip().upper() == "SKY-07382",
            usage=j["usage"],
            seconds=round(time.perf_counter() - t, 2),
            peak_device_gib=round(peak[0], 2),
            timings=j.get("timings", {}),
        )
    except requests.RequestException as exc:
        return dict(
            lines=lines,
            passed=False,
            error=type(exc).__name__,
            seconds=round(time.perf_counter() - t, 2),
            peak_device_gib=round(peak[0], 2),
        )
    finally:
        finished.set()
        monitor.join(6)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("output", type=Path)
    p.add_argument("--lines", type=int, default=10000)
    p.add_argument("--timeout", type=int, default=900)
    a = p.parse_args()
    result = measure(a.lines, a.timeout)
    write_json(a.output, result)
    print(result)
