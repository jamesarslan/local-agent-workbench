"""Measure decode speed, prompt processing and five short checks on one OpenAI-compatible server."""

import argparse
import hashlib
import http.client
import ipaddress
import json
import os
import platform
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

def base_url(environ=os.environ):
    return (
        (
            environ.get("WORKBENCH_API")
            or "http://127.0.0.1:" + environ.get("WORKBENCH_PORT", "10532")
        )
        .rstrip("/")
        .removesuffix("/v1")
    )


BASE = base_url()
CODE = [
    "Write a complete Python 3 module for an LRU cache with a TTL, a monotonic clock, and unit tests. Output only code. Include all necessary imports.",
    "Write a complete Rust module for a bounded FIFO queue over VecDeque with tests for capacity, ordering, and wraparound. Output only code.",
    "Write a complete Python module for merging overlapping half-open intervals and testing edge cases. Include type annotations and docstrings. Output only code.",
]
CHECKS = [
    ("arithmetic", "Give only the integer. What is 137 * 24 - 19?", "3269"),
    (
        "logic",
        "A list starts [4, 1, 9, 2]. Remove its second element, reverse the result, then append 7. Return only a compact JSON array.",
        [2, 9, 4, 7],
    ),
    (
        "coding",
        "What does this Python expression evaluate to? Return only a compact JSON array. [sum(range(n)) for n in (0, 1, 4, 6)]",
        [0, 0, 6, 15],
    ),
    (
        "extraction",
        'From "Fixture Works orders 17 bolts and 4 nuts", return only the JSON object with integer keys named bolts and nuts.',
        {"bolts": 17, "nuts": 4},
    ),
]
TOOL = {
    "type": "function",
    "function": {
        "name": "lookup_fixture",
        "description": "Retrieve a synthetic fixture by ID.",
        "parameters": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        },
    },
}
# Smallest prompt size recorded for each fixture in results/ (Inkling tokenizer).
FIXTURES = {"4k": 3421, "16k": 13773}
PREFILL_TOKENS = 32
REASONING_KEYS = ("reasoning_content", "reasoning", "thinking")
CONTEXT_TERMS = ("context", "too long", "too large", "maximum input")
TOO_LONG = "The prompt is longer than the server's context window."
FENCED = re.compile(r"```[\w+-]*[ \t\r]*\n(.*)\n[ \t]*```", re.DOTALL)


def loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_base(base=None):
    try:
        url = urllib.parse.urlsplit(BASE if base is None else base)
        if "@" in url.netloc:
            raise SystemExit(
                "Take the user name and password out of WORKBENCH_API and put the server's key in WORKBENCH_API_KEY."
            )
        if url.scheme == "https" or (
            url.scheme == "http" and loopback(url.hostname or "")
        ):
            return
    except ValueError:
        pass
    raise SystemExit(
        "Point WORKBENCH_API at an https address or a loopback http address; "
        "to reach another machine, open an SSH tunnel and use the forwarded 127.0.0.1 port."
    )


def public_model_name(model):
    if re.match(r"(/|~|\\\\|\.{1,2}[\\/]|[A-Za-z]:[\\/])", model):
        return re.split(r"[\\/]", model.rstrip("\\/"))[-1]
    return model


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the API key to an address that was never checked."""

    def redirect_request(self, *args, **kwargs):
        return None


def opener():
    handlers = [NoRedirect]
    if loopback(urllib.parse.urlsplit(BASE).hostname or ""):
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def request(path, body=None):
    return opener().open(
        urllib.request.Request(
            BASE + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + os.getenv("WORKBENCH_API_KEY", "local"),
            },
        ),
        timeout=180,
    )


def first_model():
    try:
        with request("/v1/models") as response:
            model = json.load(response)["data"][0]["id"]
        if not isinstance(model, str) or not model:
            raise TypeError("The first model has no name.")
        return model
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            raise SystemExit(
                "The server rejected the API key, so set WORKBENCH_API_KEY to the server's key or run `cli.py bench`, which supplies it."
            ) from None
        raise SystemExit(
            f"The server at {BASE} answered HTTP {err.code} for /v1/models; check that it is an OpenAI-compatible server and run the benchmark again."
        ) from None
    except (OSError, http.client.HTTPException):
        raise SystemExit(
            f"No model server answered at {BASE}; start the model and run the benchmark again."
        ) from None
    except (ValueError, KeyError, IndexError, TypeError):
        raise SystemExit(
            f"The server at {BASE} did not list a model; load a model and run the benchmark again."
        ) from None


def chat(model, prompt, limit, *, tools=None):
    payload = {
        "model": model,
        "temperature": 0,
        "seed": 42,
        "max_tokens": limit,
        "stream": True,
        "stream_options": {"include_usage": True},
        "reasoning_effort": "none",
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{"role": "user", "content": prompt}],
    }
    if tools:
        payload["tools"] = tools

    chunks, calls, stats, usage = [], {}, {}, {}
    t0 = time.perf_counter()
    first = None
    with request("/v1/chat/completions", payload) as response:
        for line in response:
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                break
            event = json.loads(data)
            if event.get("error"):
                raise RuntimeError(event["error"])
            stats = event.get("timings") or stats
            usage = event.get("usage") or usage
            for choice in event.get("choices", []):
                delta = choice.get("delta") or {}
                content = delta.get("content") or ""
                tool_calls = delta.get("tool_calls") or []
                if first is None and (
                    content or tool_calls or any(delta.get(k) for k in REASONING_KEYS)
                ):
                    first = time.perf_counter()
                chunks.append(content)
                for call in tool_calls:
                    f = call.get("function") or {}
                    acc = calls.setdefault(
                        call.get("index", 0), {"name": "", "arguments": ""}
                    )
                    acc["name"] += f.get("name") or ""
                    acc["arguments"] += f.get("arguments") or ""
    end = time.perf_counter()
    tokens = usage.get("completion_tokens", stats.get("predicted_n"))
    decode, source = stats.get("predicted_per_second"), "engine"
    if decode is None:
        source = "wall" if tokens and end > t0 else None
        decode = tokens / (end - t0) if source else None
    return {
        "text": "".join(chunks),
        "tool_calls": list(calls.values()),
        "ttft_s": round(first - t0, 4) if first is not None else None,
        "wall_s": round(end - t0, 4),
        "prefill_tps": stats.get("prompt_per_second"),
        "decode_tps": round(decode, 3) if decode is not None else None,
        "decode_source": source,
        "prompt_tokens": usage.get("prompt_tokens", stats.get("prompt_n")),
        "completion_tokens": tokens,
        "draft_offered": stats.get("draft_n"),
        "draft_accepted": stats.get("draft_n_accepted"),
    }


def unfenced(text):
    """The answer without one Markdown code fence around the whole of it."""
    stripped = text.strip()
    block = FENCED.fullmatch(stripped)
    if block and "```" not in block.group(1):
        return block.group(1).strip()
    return stripped


def same_value(got, expected):
    """JSON equality in which true and false never stand in for 1 and 0."""
    if isinstance(got, bool) or isinstance(expected, bool):
        return got is expected
    if isinstance(expected, dict):
        return (
            isinstance(got, dict)
            and got.keys() == expected.keys()
            and all(same_value(got[key], value) for key, value in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(got, list)
            and len(got) == len(expected)
            and all(map(same_value, got, expected))
        )
    return got == expected


def equal_content(text, expected):
    answer = unfenced(text)
    if isinstance(expected, str):
        return answer == expected
    try:
        return same_value(json.loads(answer), expected)
    except (ValueError, RecursionError):
        return False


def tool_passed(result):
    calls = result["tool_calls"]
    if len(calls) != 1:
        return False
    try:
        return calls[0]["name"] == "lookup_fixture" and json.loads(
            calls[0]["arguments"]
        ) == {"id": "fixture-52"}
    except (ValueError, RecursionError):
        return False


def record(name, result, passed=None):
    result["name"] = name
    result["text_sha256"] = hashlib.sha256(result["text"].encode()).hexdigest()
    if passed is not None:
        result["passed"] = passed
    print(
        f"{name}: {result['decode_tps']} tok/s, TTFT {result['ttft_s']} s, "
        f"prefill {result['prefill_tps']} tok/s, draft {result['draft_accepted']}/{result['draft_offered']}"
        + (f", passed={passed}" if passed is not None else ""),
        flush=True,
    )
    return result


def miss(name, outcome, reason, graded=False):
    result = {"name": name, "outcome": outcome, "reason": reason}
    if graded:
        result["passed"] = False
    print(f"{name}: {outcome}. {reason}", flush=True)
    return result


def context_limited(status, text):
    return status in (None, 400, 413, 422) and any(
        term in text.lower() for term in CONTEXT_TERMS
    )


def attempt(name, call, grade=None):
    graded = grade is not None
    try:
        result = call()
    except urllib.error.HTTPError as err:
        status = err.code
        try:
            detail = err.read().decode("utf-8", "replace")
        except (OSError, http.client.HTTPException):
            detail = ""
    except RuntimeError as err:
        status, detail = None, str(err)
    except (
        OSError,
        http.client.HTTPException,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        AttributeError,
    ) as err:
        print(f"{name}: {type(err).__name__}: {err}", file=sys.stderr, flush=True)
        return miss(
            name,
            "failed",
            f"The request did not complete ({type(err).__name__}).",
            graded,
        )
    else:
        return record(name, result, grade(result) if graded else None)
    print(f"{name}: {detail[:500]}", file=sys.stderr, flush=True)
    if context_limited(status, detail):
        return miss(name, "skipped", TOO_LONG, graded)
    return miss(
        name,
        "failed",
        f"The server answered HTTP {status}."
        if status
        else "The server reported an error in the response stream.",
        graded,
    )


def fixture_prompt(size):
    path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / f"performance-prompt-{size}-r1.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))["messages"][0]["content"]


def run(model, repeat, context=None):
    speed, prefill, checks = [], [], []
    for turn in range(repeat):
        for i, prompt in enumerate(CODE, 1):
            case = f"code-{turn + 1}-{i}"
            speed.append(
                attempt(
                    case,
                    lambda: chat(model, f"Benchmark case {case}. {prompt}", 192),
                )
            )
    for size, floor in FIXTURES.items():
        name, need = f"prompt-{size}", floor + PREFILL_TOKENS
        if context is not None and context < need:
            prefill.append(
                miss(
                    name,
                    "skipped",
                    f"This fixture needs at least {need:,} tokens of context and the server has {context:,}.",
                )
            )
            continue
        prefill.append(
            attempt(name, lambda: chat(model, fixture_prompt(size), PREFILL_TOKENS))
        )
    for label, prompt, expected in CHECKS:
        checks.append(
            attempt(
                label,
                lambda: chat(model, prompt, 96),
                lambda r: equal_content(r["text"], expected),
            )
        )
    checks.append(
        attempt(
            "tool-call",
            lambda: chat(
                model,
                "Call lookup_fixture with id 'fixture-52'. Use the tool now.",
                96,
                tools=[TOOL],
            ),
            tool_passed,
        )
    )
    return speed, prefill, checks


def summarise(condition, model, speed, prefill, checks, started, context=None):
    rated = [r for r in speed if r.get("decode_tps") is not None]
    firsts = [r["ttft_s"] for r in speed if r.get("ttft_s") is not None]
    sources = sorted({r["decode_source"] for r in rated})
    incomplete = [
        {k: r[k] for k in ("name", "outcome", "reason")}
        for r in speed + prefill + checks
        if "outcome" in r
    ] + [
        {
            "name": r["name"],
            "outcome": "unrated",
            "reason": "The server reported no token count for this request.",
        }
        for r in speed
        if "outcome" not in r and r["decode_tps"] is None
    ]
    summary = {"condition": condition, "model": public_model_name(model), "parallel": 1}
    if rated:
        summary["median_decode_tps"] = statistics.median(r["decode_tps"] for r in rated)
    summary |= {
        "median_ttft_s": statistics.median(firsts) if firsts else None,
        "checks_passed": sum(1 for r in checks if r.get("passed")),
        "checks_total": len(checks),
        "speed": speed,
        "prefill": prefill,
        "checks": checks,
        "incomplete": incomplete,
        "meta": {
            "started_at": started,
            "platform": f"{platform.system()} {platform.machine()}",
            "context": context,
            "rate_source": sources[0]
            if len(sources) == 1
            else "mixed"
            if sources
            else None,
        },
    }
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("condition")
    parser.add_argument("output", type=Path)
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument(
        "--context",
        type=int,
        help="Context length the server was started with. Prefill fixtures that cannot fit are skipped.",
    )
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat must be 1 or more.")
    folder = args.output.resolve().parent
    if not folder.is_dir():
        parser.error("The folder for the report does not exist; create it or choose another path.")
    if args.output.is_dir() or not os.access(folder, os.W_OK):
        parser.error("The report cannot be written there; give a file path in a folder you can write to.")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    check_base()
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    model = first_model()
    if "outcome" in attempt("warm-up", lambda: chat(model, "Warm-up. Reply READY.", 16)):
        raise SystemExit(
            "The warm-up request failed, so nothing was measured; check the server log and run the benchmark again."
        )
    speed, prefill, checks = run(model, args.repeat, args.context)
    summary = summarise(
        args.condition, model, speed, prefill, checks, started, args.context
    )
    try:
        args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    except OSError as err:
        raise SystemExit(
            f"The report could not be written to {args.output} ({type(err).__name__}); choose a file path you can write to and run the benchmark again."
        ) from None
    if "median_decode_tps" not in summary:
        raise SystemExit(
            f"No generation rate could be measured; the partial report is at {args.output}, so check the server log and run the benchmark again."
        )
    missed = [r["name"] for r in summary["incomplete"]]
    print(
        f"{args.condition}: {summary['median_decode_tps']:.2f} tok/s median, "
        f"{summary['checks_passed']}/{len(checks)} quality checks"
        + (f", not completed: {', '.join(missed)}" if missed else "")
    )


if __name__ == "__main__":
    main()
