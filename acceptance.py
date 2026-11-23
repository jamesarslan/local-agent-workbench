"""Grade completed answers and a two-turn tool exchange with room for reasoning."""

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
import requests
from benchmark import CHECKS, TOOL, equal_content, BASE, check_base, loopback


def send(method, path, headers, timeout, body=None):
    with requests.Session() as session:
        session.trust_env = not loopback(urlsplit(BASE).hostname or "")
        return session.request(
            method,
            BASE + path,
            headers=headers,
            json=body,
            timeout=timeout,
            allow_redirects=False,
        )


def tool_request(msg):
    try:
        calls = msg.get("tool_calls") or []
        if (
            len(calls) == 1
            and calls[0]["function"]["name"] == "lookup_fixture"
            and json.loads(calls[0]["function"]["arguments"]) == {"id": "fixture-52"}
        ):
            return calls[0]["id"]
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
        pass
    return None


def evaluate(model, headers):
    records = []

    def chat(messages, tools=None):
        body = dict(
            model=model,
            messages=messages,
            temperature=0,
            seed=42,
            max_tokens=1024,
            reasoning_effort="none",
            chat_template_kwargs={"enable_thinking": False},
        )
        if tools:
            body["tools"] = tools
        start = time.perf_counter()
        r = send("POST", "/v1/chat/completions", headers, 180, body)
        r.raise_for_status()
        answer = r.json()
        choice = answer["choices"][0]
        return choice["message"], {
            "elapsed_s": round(time.perf_counter() - start, 3),
            "finish": choice["finish_reason"],
            "tokens": answer["usage"]["completion_tokens"],
        }

    def failed(name, err):
        print(f"{name}: {type(err).__name__}: {err}", file=sys.stderr, flush=True)
        return dict(
            name=name,
            passed=False,
            answer="",
            error=f"The request did not complete ({type(err).__name__}).",
        )

    problems = (
        requests.RequestException,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        AttributeError,
    )
    for name, prompt, expected in CHECKS:
        try:
            msg, metrics = chat([dict(role="user", content=prompt)])
            answer = msg.get("content") or ""
            passed = equal_content(answer, expected)
        except problems as err:
            records.append(failed(name, err))
            continue
        records.append(dict(name=name, passed=passed, answer=answer, **metrics))
    history = [
        dict(
            role="user",
            content="Call lookup_fixture with id 'fixture-52', then reply with only the value returned in the field code.",
        )
    ]
    try:
        msg, stats = chat(history, [TOOL])
        call_id = tool_request(msg)
        answer = ""
        if call_id is not None:
            history.extend(
                [
                    msg,
                    dict(
                        role="tool",
                        tool_call_id=call_id,
                        content='{"code":"HERON-8107"}',
                    ),
                ]
            )
            msg, metrics = chat(history, [TOOL])
            answer = msg.get("content") or ""
            stats["elapsed_s"] += metrics["elapsed_s"]
            stats["tokens"] += metrics["tokens"]
            stats["finish"] = metrics["finish"]
        records.append(
            dict(
                name="tool round trip",
                passed=call_id is not None and answer.strip() == "HERON-8107",
                answer=answer,
                **stats,
            )
        )
    except problems as err:
        records.append(failed("tool round trip", err))
    return records


def first_model(headers):
    try:
        r = send("GET", "/v1/models", headers, 8)
    except requests.RequestException:
        raise SystemExit(
            f"No model server answered at {BASE}; start the model and run the check again."
        ) from None
    if r.status_code in (401, 403):
        raise SystemExit(
            "The server rejected the API key; set WORKBENCH_API_KEY to the key in api-key.txt in your Workbench data folder and run the check again."
        )
    try:
        r.raise_for_status()
        return r.json()["data"][0]["id"]
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError):
        raise SystemExit(
            f"The server at {BASE} did not list a model; load a model and run the check again."
        ) from None


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0].startswith("-"):
        raise SystemExit(
            "Give one report path, for example: acceptance.py acceptance.json"
        )
    folder = Path(args[0]).resolve().parent
    if not folder.is_dir():
        raise SystemExit(
            "The folder for the report does not exist; create it or choose another path."
        )
    if Path(args[0]).is_dir() or not os.access(folder, os.W_OK):
        raise SystemExit(
            "The report cannot be written there; give a file path in a folder you can write to."
        )
    check_base()
    hdr = {"Authorization": "Bearer " + os.getenv("WORKBENCH_API_KEY", "local")}
    result = evaluate(first_model(hdr), hdr)
    try:
        Path(args[0]).write_text(json.dumps(result, indent=2), encoding="utf8")
    except OSError as err:
        raise SystemExit(
            f"The report could not be written to {args[0]} ({type(err).__name__}); choose a file path you can write to and run the check again."
        ) from None
    print([(r["name"], r["passed"]) for r in result])
    if any("error" in r for r in result):
        raise SystemExit(
            f"Some requests did not complete; the partial report is at {args[0]}, so check the server log and run the check again."
        )


if __name__ == "__main__":
    main()
