"""Measure tool selection and serialized tool overhead on the currently served local model."""

import argparse
import json
import time
from pathlib import Path
import requests
from runtime import URL, ALIAS, key, owned, write_json

LOCAL = requests.Session()
LOCAL.trust_env = False


def overflow(err):
    """True when an HTTP error says the request exceeded the model's input limit."""
    r = err.response
    return (
        r is not None
        and r.status_code in (400, 413, 422)
        and any(
            t in r.text.lower()
            for t in ("context", "too long", "too large", "maximum input")
        )
    )


def tool(name, description):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {
                        "type": "string",
                        "description": "The exact supplied identifier.",
                    }
                },
                "required": ["value"],
                "additionalProperties": False,
            },
        },
    }


def complete(messages, tools, budget):
    body = dict(model=ALIAS, messages=messages, max_tokens=budget, temperature=0)
    if tools:
        body["tools"] = tools
    r = LOCAL.post(
        URL + "/v1/chat/completions",
        headers={"Authorization": "Bearer " + key()},
        json=body,
        timeout=180,
    )
    r.raise_for_status()
    return r.json()


def benchmark():
    _, rec = owned()
    if not rec:
        raise ValueError("Start the local model before measuring.")
    rows = []
    targets = [
        ("find_manual", "Find a product manual", "Find the product manual"),
        ("read_ticket", "Read a support ticket", "Read the support ticket"),
        ("lookup_asset", "Look up an asset record", "Look up the asset record"),
    ]
    for extra in (0, 8, 24, 48, 96):
        cases = []
        for i, (name, desc, task) in enumerate(targets):
            token = f"WB-{513 + i}"
            msgs = [
                dict(
                    role="user",
                    content=f"{task} for {token}. Call its tool once with the exact identifier.",
                )
            ]
            nouns = (
                "orders",
                "invoices",
                "parts",
                "suppliers",
                "returns",
                "tickets",
                "assets",
                "manuals",
            )
            schemas = []
            for j in range(extra):
                noun = nouns[j % len(nouns)]
                schemas.append(
                    tool(f"list_{noun}_{j}", f"List {noun} records in collection {j}.")
                )
            schemas.insert(round(extra * i / 2), tool(name, desc))
            baseline = complete(msgs, [], 1)["usage"]["prompt_tokens"]
            t = time.monotonic()
            try:
                answer = complete(msgs, schemas, 512)
            except requests.HTTPError as err:
                if not overflow(err):
                    raise
                return dict(
                    model=Path(rec["spec"]["model"]).name,
                    context=rec["spec"]["context"],
                    rows=rows,
                    limit=f"The server reached its input limit at {extra + 1} tools.",
                )
            elapsed = round(time.monotonic() - t, 2)
            calls = answer["choices"][0]["message"].get("tool_calls") or []
            passed = False
            if len(calls) == 1:
                fn = calls[0]["function"]
                try:
                    passed = fn["name"] == name and json.loads(fn["arguments"]) == {
                        "value": token
                    }
                except (TypeError, ValueError):
                    pass
            cases.append(
                dict(
                    target=name,
                    passed=passed,
                    additional_input_tokens=answer["usage"]["prompt_tokens"] - baseline,
                    elapsed_s=elapsed,
                    output_tokens=answer["usage"]["completion_tokens"],
                )
            )
        rows.append(dict(tools=extra + 1, cases=cases))
    spec = rec["spec"]
    return dict(model=Path(spec["model"]).name, context=spec["context"], rows=rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    write_json(args.output, benchmark())
