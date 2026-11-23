"""Benchmark, acceptance and Harbor usage helpers, and MEASUREMENTS.md checked against results/."""

import ast
import email.message
import io
import json
import os
import re
import shutil
import statistics
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from unittest.mock import patch

import requests

import acceptance
import benchmark

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
TEXT = (ROOT / "MEASUREMENTS.md").read_text(encoding="utf-8")
NODE = shutil.which("node") if os.name != "nt" else None
LOCAL = "http://127.0.0.1:10532"


def quiet(fn, *args, **kwargs):
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return fn(*args, **kwargs)


class Stream:
    def __init__(self, events):
        self.lines = [b"data: " + json.dumps(e).encode() + b"\n" for e in events]
        self.lines.append(b"data: [DONE]\n")

    def __enter__(self):
        return iter(self.lines)

    def __exit__(self, *exc):
        return False


def delta(**fields):
    return {"choices": [{"delta": fields}]}


def http_error(code, text):
    return urllib.error.HTTPError(
        "http://127.0.0.1:1/v1/chat/completions",
        code,
        "error",
        None,
        io.BytesIO(text.encode()),
    )


def measured(text="", tps=100.0, tool_calls=(), tokens=192):
    return {
        "text": text,
        "tool_calls": list(tool_calls),
        "ttft_s": 0.2,
        "wall_s": 1.0,
        "prefill_tps": 1000.0,
        "decode_tps": tps,
        "decode_source": "engine" if tps is not None else None,
        "prompt_tokens": 50,
        "completion_tokens": tokens,
        "draft_offered": None,
        "draft_accepted": None,
    }


ANSWERS = {prompt: json.dumps(expected) for _, prompt, expected in benchmark.CHECKS}
ANSWERS[benchmark.CHECKS[0][1]] = "3269"
GOOD_CALL = {"name": "lookup_fixture", "arguments": '{"id": "fixture-52"}'}
FENCED_BOLTS = '```json\n{"bolts": 17, "nuts": 4}\n```'
# Check answers the five Apple M3 Max runs of 2026-11-22 recorded: model file, check, text, right answer.
CAPTURED = [
    ("Qwen3.5-4B-Q4_K_M.gguf", "arithmetic", "3087", False),
    ("Qwen3.5-4B-Q4_K_M.gguf", "logic", "[7, 2, 9, 1]", False),
    ("Qwen3.5-4B-Q4_K_M.gguf", "coding", "[0, 1, 10, 21]", False),
    ("Qwen3.5-4B-Q4_K_M.gguf", "extraction", '```json\n{\n  "bolts": 17,\n  "nuts": 4\n}\n```', True),
    ("Qwen3.5-9B-Q4_K_M.gguf", "arithmetic", "3283", False),
    ("Qwen3.5-9B-Q4_K_M.gguf", "logic", "[9, 2, 1, 7]", False),
    ("Qwen3.5-9B-Q4_K_M.gguf", "coding", "[0, 1, 10, 21]", False),
    ("Qwen3.5-9B-Q4_K_M.gguf", "extraction", FENCED_BOLTS, True),
    ("Qwen3.8-27B-UD-IQ3_XXS.gguf", "arithmetic", "3179", False),
    ("Qwen3.8-27B-UD-IQ3_XXS.gguf", "logic", "[9, 2, 4, 7]", False),
    ("Qwen3.8-27B-UD-IQ3_XXS.gguf", "coding", "[0, 0, 6, 15]", True),
    ("Qwen3.8-27B-UD-IQ3_XXS.gguf", "extraction", '{"bolts": 17, "nuts": 4}', True),
    ("Qwen3.8-27B-UD-Q4_K_XL.gguf", "arithmetic", "3189", False),
    ("Qwen3.8-27B-UD-Q4_K_XL.gguf", "logic", "[2,9,1,7]", False),
    ("Qwen3.8-27B-UD-Q4_K_XL.gguf", "coding", "[0, 0, 6, 15]", True),
    ("Qwen3.8-27B-UD-Q4_K_XL.gguf", "extraction", '{"bolts": 17, "nuts": 4}', True),
    ("Qwen3.8-27B-UD-Q6_K_XL.gguf", "arithmetic", "3189", False),
    ("Qwen3.8-27B-UD-Q6_K_XL.gguf", "logic", "[2, 9, 1, 4, 7]", False),
    ("Qwen3.8-27B-UD-Q6_K_XL.gguf", "coding", "[0, 0, 6, 15]", True),
    ("Qwen3.8-27B-UD-Q6_K_XL.gguf", "extraction", '{"bolts": 17, "nuts": 4}', True),
]


def server(reject_over=None, rated=True):
    sent = []

    def chat(model, prompt, limit, *, tools=None):
        sent.append(prompt)
        if reject_over is not None and len(prompt) > reject_over:
            raise http_error(
                400,
                '{"error":{"message":"the request exceeds the available context size"}}',
            )
        if tools:
            return measured(tool_calls=[dict(GOOD_CALL)])
        return measured(ANSWERS.get(prompt, ""), 100.0 if rated else None)

    return chat, sent


class Answer(io.BytesIO):
    def __init__(self, code=200, location=None):
        super().__init__(b"{}")
        self.code = self.status = code
        self.msg = self.reason = "answer"
        self.headers = email.message.Message()
        if location:
            self.headers["Location"] = location

    def info(self):
        return self.headers


class Wire:
    """Stands in for the socket layer and records what would have been sent, and where."""

    def __init__(self, *answers):
        self.answers, self.sent = list(answers), []

    def open(self, handler, req):
        self.sent.append((req.host, req.full_url, req.get_header("Authorization")))
        return self.answers.pop(0) if self.answers else Answer()

    def __enter__(self):
        wire = self
        self.patches = [
            patch.object(urllib.request, "_opener", None),
            patch.object(urllib.request.HTTPHandler, "http_open", lambda h, req: wire.open(h, req)),
            patch.object(urllib.request.HTTPSHandler, "https_open", lambda h, req: wire.open(h, req)),
        ]
        for item in self.patches:
            item.start()
        return self

    def __exit__(self, *exc):
        for item in self.patches:
            item.stop()
        return False


PROXIES = {
    "http_proxy": "http://198.51.100.9:3128",
    "https_proxy": "http://198.51.100.9:3128",
    "all_proxy": "http://198.51.100.9:3128",
    "no_proxy": "",
}


class RequestTests(unittest.TestCase):
    def test_request_does_not_follow_a_redirect_with_the_key(self):
        for code in (301, 302, 303, 307, 308):
            with (
                self.subTest(code=code),
                patch.object(benchmark, "BASE", LOCAL),
                patch.dict(os.environ, {"WORKBENCH_API_KEY": "key-for-the-server"}),
                Wire(Answer(code, "http://198.51.100.7/v1/models")) as wire,
            ):
                with self.assertRaises(urllib.error.HTTPError) as stop:
                    benchmark.request("/v1/models")
                self.assertEqual(stop.exception.code, code)
                self.assertEqual(
                    wire.sent,
                    [("127.0.0.1:10532", LOCAL + "/v1/models", "Bearer key-for-the-server")],
                )

    def test_first_model_reports_a_redirect_instead_of_following_it(self):
        with (
            patch.object(benchmark, "BASE", LOCAL),
            Wire(Answer(302, "http://198.51.100.7/v1/models")) as wire,
            self.assertRaises(SystemExit) as stop,
        ):
            benchmark.first_model()
        self.assertIn("HTTP 302", stop.exception.code)
        self.assertEqual(len(wire.sent), 1)

    def test_request_to_this_computer_ignores_proxy_settings(self):
        for base in (LOCAL, "http://localhost:8080", "http://[::1]:8080"):
            with (
                self.subTest(base=base),
                patch.object(benchmark, "BASE", base),
                patch.dict(os.environ, PROXIES),
                Wire() as wire,
            ):
                benchmark.request("/v1/models").close()
                self.assertEqual(wire.sent[0][0], base.removeprefix("http://"))

    def test_request_to_a_hosted_address_keeps_proxy_settings(self):
        with (
            patch.object(benchmark, "BASE", "https://example.org"),
            patch.dict(os.environ, PROXIES),
            Wire() as wire,
        ):
            benchmark.request("/v1/models").close()
        self.assertEqual(wire.sent[0][0], "198.51.100.9:3128")


class ChatTests(unittest.TestCase):
    def chat(self, events, clock):
        with (
            patch.object(benchmark, "request", return_value=Stream(events)),
            patch.object(benchmark.time, "perf_counter", side_effect=clock),
        ):
            return benchmark.chat("m", "p", 16)

    def test_chat_reports_engine_rate_when_timings_are_present(self):
        result = self.chat(
            [
                delta(content="RE"),
                delta(content="ADY"),
                {
                    "choices": [],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 4},
                    "timings": {
                        "predicted_per_second": 150.1,
                        "prompt_per_second": 900.0,
                    },
                },
            ],
            [10.0, 10.5, 12.0],
        )
        self.assertEqual(result["text"], "READY")
        self.assertEqual(
            (result["decode_tps"], result["decode_source"]), (150.1, "engine")
        )
        self.assertEqual((result["ttft_s"], result["wall_s"]), (0.5, 2.0))
        self.assertEqual((result["prompt_tokens"], result["completion_tokens"]), (9, 4))

    def test_chat_falls_back_to_wall_clock_rate_when_reasoning_is_hidden(self):
        result = self.chat(
            [
                delta(content="3269"),
                {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 66}},
            ],
            [0.0, 1.9998, 2.0],
        )
        self.assertEqual(
            (result["decode_tps"], result["decode_source"]), (33.0, "wall")
        )
        self.assertIsNone(result["prefill_tps"])

    def test_chat_has_no_rate_when_the_server_reports_no_tokens(self):
        result = self.chat([delta(content="hi")], [0.0, 0.5, 1.0])
        self.assertIsNone(result["decode_tps"])
        self.assertIsNone(result["decode_source"])

    def test_chat_counts_any_reasoning_delta_as_first_token(self):
        for key in benchmark.REASONING_KEYS:
            with self.subTest(key=key):
                result = self.chat(
                    [delta(**{key: "thinking"}), delta(content="done")],
                    [0.0, 0.25, 1.0],
                )
                self.assertEqual(result["ttft_s"], 0.25)

    def test_chat_joins_a_tool_call_split_across_chunks(self):
        part = lambda **f: delta(tool_calls=[{"index": 0, "function": f}])
        result = self.chat(
            [part(name="lookup_fixture", arguments='{"id": '), part(arguments='"fixture-52"}')],
            [0.0, 0.1, 0.2],
        )
        self.assertEqual(result["tool_calls"], [GOOD_CALL])
        self.assertTrue(benchmark.tool_passed(result))

    def test_chat_raises_on_an_error_event_in_the_stream(self):
        with self.assertRaises(RuntimeError):
            self.chat([{"error": {"message": "context size exceeded"}}], [0.0, 1.0])


class GradingTests(unittest.TestCase):
    def test_equal_content_compares_text_exactly_and_json_by_value(self):
        self.assertTrue(benchmark.equal_content(" 3269\n", "3269"))
        self.assertFalse(benchmark.equal_content("3269.", "3269"))
        self.assertTrue(benchmark.equal_content("[2,9,4,7]", [2, 9, 4, 7]))
        self.assertTrue(
            benchmark.equal_content('{\n "nuts": 4, "bolts": 17}', {"bolts": 17, "nuts": 4})
        )
        self.assertFalse(benchmark.equal_content("[2, 9, 4]", [2, 9, 4, 7]))
        self.assertFalse(benchmark.equal_content("The answer is [2,9,4,7]", [2, 9, 4, 7]))

    def test_equal_content_does_not_take_true_or_false_for_a_number(self):
        sums, bolts = [0, 0, 6, 15], {"bolts": 17, "nuts": 4}
        for text in ("[0, false, 6, 15]", "[false, 0, 6, 15]", "[0, 0, 6, true]"):
            with self.subTest(text=text):
                self.assertFalse(benchmark.equal_content(text, sums))
        self.assertFalse(benchmark.equal_content("[true, 9, 4, 7]", [1, 9, 4, 7]))
        self.assertFalse(benchmark.equal_content('{"bolts": 17, "nuts": true}', bolts))
        self.assertFalse(benchmark.equal_content('{"bolts": 17}', bolts))
        self.assertFalse(benchmark.equal_content('{"bolts": 17, "nuts": 4, "washers": 0}', bolts))
        self.assertFalse(benchmark.equal_content('[[0, 0, 6, 15]]', sums))
        self.assertFalse(benchmark.equal_content('{"0": 0}', sums))
        self.assertTrue(benchmark.equal_content('{"bolts": 17.0, "nuts": 4}', bolts))
        self.assertTrue(benchmark.same_value([True, {"a": False}], [True, {"a": False}]))
        self.assertFalse(benchmark.same_value([True, {"a": 0}], [True, {"a": False}]))

    def test_grading_fails_an_answer_nested_too_deeply_to_parse(self):
        deep = "[" * 200000
        with self.assertRaises(RecursionError):
            json.loads(deep)
        self.assertFalse(benchmark.equal_content(deep, [2, 9, 4, 7]))
        self.assertFalse(
            benchmark.tool_passed({"tool_calls": [GOOD_CALL | {"arguments": deep}]})
        )
        self.assertIsNone(acceptance.tool_request(call_message(deep)))
        record = quiet(
            benchmark.attempt,
            "tool-call",
            lambda: measured(tool_calls=[GOOD_CALL | {"arguments": deep}]),
            benchmark.tool_passed,
        )
        self.assertFalse(record["passed"])

    def test_equal_content_ignores_one_code_fence_around_the_whole_answer(self):
        bolts = {"bolts": 17, "nuts": 4}
        for text in (
            FENCED_BOLTS,
            '```\n{"nuts": 4, "bolts": 17}\n```\n',
            FENCED_BOLTS.replace("\n", "\r\n"),
        ):
            with self.subTest(text=text):
                self.assertTrue(benchmark.equal_content(text, bolts))
        self.assertTrue(benchmark.equal_content("```\n3269\n```", "3269"))
        self.assertFalse(benchmark.equal_content("```\n3270\n```", "3269"))
        for text in (
            FENCED_BOLTS.replace("4", "5"),
            "Here it is:\n" + FENCED_BOLTS,
            FENCED_BOLTS + "\nDone.",
            FENCED_BOLTS.removesuffix("\n```"),
            '```json\n{"bolts": 17}\n```\n```json\n{"nuts": 4}\n```',
            '```{"bolts": 17, "nuts": 4}```',
            '`{"bolts": 17, "nuts": 4}`',
            "```\n```",
        ):
            with self.subTest(text=text):
                self.assertFalse(benchmark.equal_content(text, bolts))

    def test_captured_answers_pass_only_when_the_answer_is_right(self):
        expected = {name: value for name, _, value in benchmark.CHECKS}
        for model, check, text, right in CAPTURED:
            with self.subTest(model=model, check=check):
                self.assertEqual(benchmark.equal_content(text, expected[check]), right)
        passed = {}
        for model, _, text, right in CAPTURED:
            passed[model] = passed.get(model, 0) + right
        self.assertEqual(sorted(passed.values()), [1, 1, 2, 2, 2])

    def test_tool_passed_needs_exactly_one_valid_call(self):
        ok = dict(GOOD_CALL)
        self.assertTrue(benchmark.tool_passed({"tool_calls": [ok]}))
        self.assertFalse(benchmark.tool_passed({"tool_calls": []}))
        self.assertFalse(benchmark.tool_passed({"tool_calls": [ok, ok]}))
        self.assertFalse(
            benchmark.tool_passed({"tool_calls": [ok | {"name": "lookup"}]})
        )
        self.assertFalse(
            benchmark.tool_passed({"tool_calls": [ok | {"arguments": '{"id": '}]})
        )

    def test_public_model_name_drops_local_folders_only(self):
        for given, shown in [
            ("/Users/someone/models/Model-Q4.gguf", "Model-Q4.gguf"),
            ("C:\\Users\\someone\\models\\Model-Q4.gguf", "Model-Q4.gguf"),
            ("~/models/Model-Q4.gguf", "Model-Q4.gguf"),
            ("../../Users/someone/models/Model-Q4.gguf", "Model-Q4.gguf"),
            ("./models/Model-Q4.gguf", "Model-Q4.gguf"),
            ("..\\someone\\Model-Q4.gguf", "Model-Q4.gguf"),
            ("unsloth/Inkling-2-Mini-GGUF", "unsloth/Inkling-2-Mini-GGUF"),
            ("local-agent", "local-agent"),
        ]:
            self.assertEqual(benchmark.public_model_name(given), shown)


class AttemptTests(unittest.TestCase):
    def fail(self, error, grade=None):
        def call():
            raise error

        return quiet(benchmark.attempt, "case", call, grade)

    def test_attempt_skips_a_context_rejection(self):
        for error in (
            http_error(400, '{"error":{"message":"the request exceeds the available context size"}}'),
            http_error(413, "Prompt too large"),
            RuntimeError({"message": "maximum input length is 8192 tokens"}),
        ):
            with self.subTest(error=str(error)):
                self.assertEqual(
                    self.fail(error),
                    {"name": "case", "outcome": "skipped", "reason": benchmark.TOO_LONG},
                )

    def test_attempt_records_other_failures_without_raising(self):
        self.assertEqual(
            self.fail(http_error(500, "boom"))["reason"],
            "The server answered HTTP 500.",
        )
        self.assertEqual(
            self.fail(http_error(401, "the context is unauthorized"))["outcome"],
            "failed",
        )
        self.assertEqual(
            self.fail(RuntimeError("slot unavailable"))["reason"],
            "The server reported an error in the response stream.",
        )
        refused = self.fail(urllib.error.URLError("refused"), grade=lambda r: True)
        self.assertEqual(
            refused,
            {
                "name": "case",
                "outcome": "failed",
                "reason": "The request did not complete (URLError).",
                "passed": False,
            },
        )
        self.assertEqual(self.fail(TimeoutError("timed out"))["outcome"], "failed")

    def test_attempt_fails_a_malformed_stream_without_raising(self):
        with patch.object(benchmark, "request", return_value=Stream([{"choices": ["oops"]}])):
            record = quiet(benchmark.attempt, "case", lambda: benchmark.chat("m", "p", 16))
        self.assertEqual(
            record,
            {
                "name": "case",
                "outcome": "failed",
                "reason": "The request did not complete (AttributeError).",
            },
        )

    def test_attempt_keeps_server_text_out_of_the_record(self):
        record = self.fail(http_error(500, "cannot open /home/someone/models/x.gguf"))
        self.assertNotIn("someone", json.dumps(record))

    def test_attempt_grades_a_completed_request(self):
        record = quiet(benchmark.attempt, "arithmetic", lambda: measured("3269"), lambda r: r["text"] == "3269")
        self.assertTrue(record["passed"])
        self.assertNotIn("outcome", record)
        self.assertEqual(len(record["text_sha256"]), 64)


class RunTests(unittest.TestCase):
    def test_fixture_floors_are_the_smallest_published_prompt_sizes(self):
        seen = {}
        for path in (ROOT / "results").rglob("*.json"):
            report = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(report, dict):
                for row in report.get("prefill", []):
                    size = row["name"].removeprefix("prompt-")
                    seen[size] = min(seen.get(size, row["prompt_tokens"]), row["prompt_tokens"])
        self.assertEqual(seen, benchmark.FIXTURES)

    def test_run_skips_a_fixture_that_cannot_fit_without_sending_it(self):
        chat, sent = server()
        with patch.object(benchmark, "chat", chat):
            speed, prefill, checks = quiet(benchmark.run, "m", 2, 8192)
        self.assertEqual((len(speed), len(prefill), len(checks)), (6, 2, 5))
        self.assertNotIn("outcome", prefill[0])
        self.assertEqual(
            prefill[1],
            {
                "name": "prompt-16k",
                "outcome": "skipped",
                "reason": "This fixture needs at least 13,805 tokens of context and the server has 8,192.",
            },
        )
        self.assertEqual(len(sent), 12)
        self.assertLess(max(map(len, sent)), 20000)
        self.assertEqual(sum(r["passed"] for r in checks), 5)

    def test_run_sends_both_fixtures_when_the_context_is_unknown_or_large(self):
        for context in (None, 13805, 32768):
            chat, sent = server()
            with patch.object(benchmark, "chat", chat):
                _, prefill, _ = quiet(benchmark.run, "m", 1, context)
            self.assertEqual(len(sent), 10)
            self.assertFalse([r for r in prefill if "outcome" in r])

    def test_run_continues_after_the_server_rejects_a_fixture(self):
        chat, sent = server(reject_over=20000)
        with patch.object(benchmark, "chat", chat):
            speed, prefill, checks = quiet(benchmark.run, "m", 2)
        self.assertEqual(prefill[1]["outcome"], "skipped")
        self.assertEqual(prefill[1]["reason"], benchmark.TOO_LONG)
        self.assertEqual(len(sent), 13)
        self.assertEqual(len(checks), 5)


class SummaryTests(unittest.TestCase):
    def summary(self, speed, prefill=(), checks=()):
        return benchmark.summarise(
            "cond", "local-agent", list(speed), list(prefill), list(checks), "2026-11-22T21:04:05Z", 8192
        )

    def test_summary_keeps_the_published_keys_first_and_in_order(self):
        published = json.loads((ROOT / "results/sq20.json").read_text(encoding="utf-8"))
        made = self.summary([measured() | {"name": "code-1-1"}])
        self.assertEqual(list(made)[: len(published)], list(published))
        self.assertEqual(list(made)[len(published) :], ["incomplete", "meta"])
        self.assertEqual(made["incomplete"], [])
        self.assertEqual(
            made["meta"],
            {
                "started_at": "2026-11-22T21:04:05Z",
                "platform": made["meta"]["platform"],
                "context": 8192,
                "rate_source": "engine",
            },
        )

    def test_summary_medians_use_only_completed_cases(self):
        skipped = {"name": "prompt-16k", "outcome": "skipped", "reason": benchmark.TOO_LONG}
        failed = {"name": "code-1-3", "outcome": "failed", "reason": "The server answered HTTP 500."}
        made = self.summary(
            [measured(tps=100.0) | {"name": "code-1-1"}, measured(tps=140.0) | {"name": "code-1-2"}, failed],
            [skipped],
            [{"name": "logic", "outcome": "failed", "reason": "x", "passed": False}, measured() | {"name": "coding", "passed": True}],
        )
        self.assertEqual(made["median_decode_tps"], 120.0)
        self.assertEqual((made["checks_passed"], made["checks_total"]), (1, 2))
        self.assertEqual(
            [(r["name"], r["outcome"]) for r in made["incomplete"]],
            [("code-1-3", "failed"), ("prompt-16k", "skipped"), ("logic", "failed")],
        )

    def test_summary_omits_the_median_when_nothing_was_rated(self):
        made = self.summary([measured(tps=None) | {"name": "code-1-1"}])
        self.assertNotIn("median_decode_tps", made)
        self.assertIsNone(made["meta"]["rate_source"])
        self.assertEqual(made["incomplete"][0]["outcome"], "unrated")

    def test_summary_names_the_rate_source(self):
        wall = measured() | {"name": "code-1-2", "decode_source": "wall"}
        engine = measured() | {"name": "code-1-1"}
        self.assertEqual(self.summary([wall])["meta"]["rate_source"], "wall")
        self.assertEqual(self.summary([engine, wall])["meta"]["rate_source"], "mixed")

    def test_summary_stores_a_model_path_as_its_file_name(self):
        made = benchmark.summarise("c", "/home/someone/m/x.gguf", [], [], [], "t")
        self.assertEqual(made["model"], "x.gguf")


class MainTests(unittest.TestCase):
    def run_main(self, chat, *extra):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            with (
                patch.object(benchmark, "BASE", LOCAL),
                patch.object(benchmark, "first_model", return_value="local-agent"),
                patch.object(benchmark, "chat", chat),
            ):
                try:
                    quiet(benchmark.main, ["cond", str(out), *extra])
                    code = None
                except SystemExit as stop:
                    code = stop.code
            return code, json.loads(out.read_text(encoding="utf-8")) if out.exists() else None

    def test_main_writes_the_report_when_the_16k_fixture_is_rejected(self):
        chat, _ = server(reject_over=20000)
        code, report = self.run_main(chat)
        self.assertIsNone(code)
        self.assertEqual(len(report["speed"]), 6)
        self.assertEqual(report["median_decode_tps"], 100.0)
        self.assertEqual(report["checks_passed"], 5)
        self.assertEqual(
            report["incomplete"],
            [{"name": "prompt-16k", "outcome": "skipped", "reason": benchmark.TOO_LONG}],
        )

    def test_main_passes_the_context_through(self):
        chat, sent = server()
        code, report = self.run_main(chat, "--context", "8192")
        self.assertIsNone(code)
        self.assertEqual(report["meta"]["context"], 8192)
        self.assertEqual(report["prefill"][1]["outcome"], "skipped")
        self.assertEqual(len(sent), 13)

    def test_main_writes_a_partial_report_before_reporting_no_rate(self):
        chat, _ = server(rated=False)
        code, report = self.run_main(chat)
        self.assertIn("partial report", code)
        self.assertNotIn("median_decode_tps", report)
        self.assertEqual(report["checks_total"], 5)

    def test_main_stops_before_measuring_when_the_warm_up_fails(self):
        def chat(*args, **kwargs):
            raise urllib.error.URLError("refused")

        code, report = self.run_main(chat)
        self.assertIn("warm-up", code)
        self.assertIsNone(report)

    def test_main_rejects_a_repeat_below_one(self):
        with self.assertRaises(SystemExit) as stop:
            quiet(benchmark.main, ["cond", "out.json", "--repeat", "0"])
        self.assertEqual(stop.exception.code, 2)

    def test_main_checks_the_report_folder_before_any_request(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(benchmark, "request", side_effect=AssertionError),
            self.assertRaises(SystemExit) as stop,
        ):
            quiet(benchmark.main, ["cond", str(Path(tmp) / "missing" / "out.json")])
        self.assertEqual(stop.exception.code, 2)

    def test_main_refuses_plain_http_to_another_machine_before_any_request(self):
        with (
            patch.object(benchmark, "BASE", "http://192.168.1.20:8080"),
            patch.object(benchmark, "request", side_effect=AssertionError),
            self.assertRaises(SystemExit) as stop,
        ):
            quiet(benchmark.main, ["cond", "out.json"])
        self.assertIn("SSH tunnel", stop.exception.code)

    def test_check_base_accepts_loopback_http_and_any_https(self):
        for base in (
            "http://127.0.0.1:10532",
            "http://localhost:8080",
            "http://[::1]:8080",
            "https://example.org",
        ):
            benchmark.check_base(base)
        for base in (
            "http://example.org",
            "http://10.0.0.5:8080",
            "ftp://127.0.0.1",
            "http://[::1",
            "",
        ):
            with self.subTest(base=base), self.assertRaises(SystemExit):
                benchmark.check_base(base)

    def test_an_address_that_carries_a_password_is_refused_without_repeating_it(self):
        for base in (
            "https://someone:hunter2@example.org",
            "http://someone:hunter2@127.0.0.1:10532",
            "https://hunter2@example.org",
        ):
            with self.subTest(base=base), self.assertRaises(SystemExit) as stop:
                benchmark.check_base(base)
            self.assertIn("WORKBENCH_API_KEY", stop.exception.code)
            self.assertNotIn("hunter2", stop.exception.code)
        for script in (benchmark, acceptance):
            errors = io.StringIO()
            with (
                self.subTest(script=script.__name__),
                tempfile.TemporaryDirectory() as tmp,
                patch.object(benchmark, "BASE", "https://someone:hunter2@127.0.0.1:9"),
                patch.object(acceptance, "BASE", "https://someone:hunter2@127.0.0.1:9"),
                patch.object(benchmark, "request", side_effect=AssertionError),
                patch.object(acceptance, "send", side_effect=AssertionError),
                redirect_stdout(errors),
                redirect_stderr(errors),
                self.assertRaises(SystemExit) as stop,
            ):
                out = str(Path(tmp) / "report.json")
                script.main(["cond", out] if script is benchmark else [out])
            self.assertNotIn("hunter2", str(stop.exception.code) + errors.getvalue())

    def test_base_url_reads_the_environment_and_drops_a_trailing_v1(self):
        self.assertEqual(benchmark.base_url({}), "http://127.0.0.1:10532")
        self.assertEqual(benchmark.base_url({"WORKBENCH_PORT": "9"}), "http://127.0.0.1:9")
        self.assertEqual(
            benchmark.base_url({"WORKBENCH_API": "https://example.org/v1/"}),
            "https://example.org",
        )

    def test_first_model_says_what_to_do_when_the_key_is_rejected(self):
        with (
            patch.object(benchmark, "request", side_effect=http_error(401, "{}")),
            self.assertRaises(SystemExit) as stop,
        ):
            benchmark.first_model()
        self.assertIn("WORKBENCH_API_KEY", stop.exception.code)

    def test_first_model_says_what_to_do_when_nothing_answers(self):
        with (
            patch.object(benchmark, "request", side_effect=urllib.error.URLError("refused")),
            self.assertRaises(SystemExit) as stop,
        ):
            benchmark.first_model()
        self.assertIn("start the model", stop.exception.code)

    def test_first_model_needs_a_model_name_it_can_store(self):
        self.assertEqual(self.listed({"data": [{"id": "local-agent"}]}), "local-agent")
        for body in (
            {"data": [{"id": 7}]},
            {"data": [{"id": None}]},
            {"data": [{"id": ""}]},
            {"data": [{"id": ["m"]}]},
            {"data": []},
            {"data": None},
            [],
        ):
            with self.subTest(body=body):
                self.assertIn("did not list a model", self.listed(body))

    def listed(self, body):
        stream = io.BytesIO(json.dumps(body).encode())
        with patch.object(benchmark, "request", return_value=stream):
            try:
                return benchmark.first_model()
            except SystemExit as stop:
                return stop.code

    def test_main_stops_before_measuring_when_the_model_list_names_no_text(self):
        chat, sent = server()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            with (
                patch.object(benchmark, "BASE", LOCAL),
                patch.object(benchmark, "request", return_value=io.BytesIO(b'{"data": [{"id": 7}]}')),
                patch.object(benchmark, "chat", chat),
                self.assertRaises(SystemExit) as stop,
            ):
                quiet(benchmark.main, ["cond", str(out)])
        self.assertIn("did not list a model", stop.exception.code)
        self.assertEqual(sent, [])

    def test_main_refuses_a_report_path_it_cannot_write_before_any_request(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            benchmark, "request", side_effect=AssertionError
        ):
            with self.assertRaises(SystemExit) as stop:
                quiet(benchmark.main, ["cond", tmp])
            self.assertEqual(stop.exception.code, 2)
            with (
                patch.object(benchmark.os, "access", return_value=False),
                self.assertRaises(SystemExit) as stop,
            ):
                quiet(benchmark.main, ["cond", str(Path(tmp) / "report.json")])
            self.assertEqual(stop.exception.code, 2)

    def test_main_says_so_when_the_finished_report_cannot_be_saved(self):
        chat, _ = server()
        with (
            patch.object(Path, "write_text", side_effect=PermissionError(13, "denied")),
        ):
            code, report = self.run_main(chat)
        self.assertIn("could not be written", code)
        self.assertIn("PermissionError", code)
        self.assertIsNone(report)

    def test_main_finishes_when_the_console_cannot_show_the_condition(self):
        chat, _ = server()
        console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            with (
                patch.object(benchmark, "BASE", LOCAL),
                patch.object(benchmark, "first_model", return_value="local-agent"),
                patch.object(benchmark, "chat", chat),
                redirect_stdout(console),
                redirect_stderr(io.StringIO()),
            ):
                benchmark.main(["\u6a21\u578b-Q4.gguf", str(out)])
            report = json.loads(out.read_text(encoding="utf-8"))
        console.flush()
        self.assertEqual(report["condition"], "\u6a21\u578b-Q4.gguf")
        self.assertIn(b"\\u6a21\\u578b-Q4.gguf: 100.00 tok/s median", console.buffer.getvalue())


class Reply:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        if self.body is None:
            raise ValueError("no json")
        return self.body


def completion(message, finish="stop", tokens=5):
    return Reply(
        {
            "choices": [{"message": message, "finish_reason": finish}],
            "usage": {"completion_tokens": tokens},
        }
    )


def call_message(arguments='{"id": "fixture-52"}'):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "lookup_fixture", "arguments": arguments},
            }
        ],
    }


def acceptance_server(first_tool_message, fail_on=None):
    def post(method, path, headers, timeout, json):
        last = json["messages"][-1]
        if fail_on and last.get("content") == fail_on:
            raise requests.ConnectionError("reset")
        if last["role"] == "tool":
            return completion({"role": "assistant", "content": "HERON-8107"}, "stop", 9)
        if json.get("tools"):
            return completion(first_tool_message, "tool_calls", 28)
        return completion({"role": "assistant", "content": ANSWERS[last["content"]]})

    return post


class AcceptanceTests(unittest.TestCase):
    def evaluate(self, post):
        with patch.object(acceptance, "send", post):
            return quiet(acceptance.evaluate, "m", {})

    def test_tool_request_returns_the_call_id_only_for_one_valid_call(self):
        self.assertEqual(acceptance.tool_request(call_message()), "call_1")
        for message in (
            None,
            {"content": "no call"},
            {"tool_calls": None},
            {"tool_calls": []},
            call_message('{"id": '),
            call_message('{"id": "fixture-53"}'),
            {"tool_calls": [{"id": "call_1"}]},
            {"tool_calls": ["lookup_fixture"]},
            {"tool_calls": call_message()["tool_calls"] * 2},
        ):
            with self.subTest(message=message):
                self.assertIsNone(acceptance.tool_request(message))

    def test_evaluate_passes_a_complete_exchange_and_reports_the_second_turn(self):
        records = self.evaluate(acceptance_server(call_message()))
        self.assertEqual([r["passed"] for r in records], [True] * 5)
        self.assertEqual(
            {k: records[4][k] for k in ("name", "answer", "finish", "tokens")},
            {"name": "tool round trip", "answer": "HERON-8107", "finish": "stop", "tokens": 37},
        )
        self.assertEqual(
            list(records[0]), ["name", "passed", "answer", "elapsed_s", "finish", "tokens"]
        )

    def test_evaluate_fails_a_malformed_tool_call_and_keeps_the_other_checks(self):
        for message in (
            call_message('{"id": '),
            {"role": "assistant", "tool_calls": None},
            None,
        ):
            with self.subTest(message=message):
                records = self.evaluate(acceptance_server(message))
                self.assertEqual([r["passed"] for r in records], [True] * 4 + [False])
                self.assertEqual(records[4]["finish"], "tool_calls")
                self.assertNotIn("error", records[4])

    def test_evaluate_records_a_failed_request_and_continues(self):
        records = self.evaluate(
            acceptance_server(call_message(), fail_on=benchmark.CHECKS[1][1])
        )
        self.assertEqual([r["passed"] for r in records], [True, False, True, True, True])
        self.assertEqual(
            records[1],
            {
                "name": "logic",
                "passed": False,
                "answer": "",
                "error": "The request did not complete (ConnectionError).",
            },
        )

    def test_evaluate_records_a_reply_without_a_message(self):
        good = acceptance_server(call_message())

        def post(method, path, headers, timeout, json):
            if json["messages"][-1].get("content") == benchmark.CHECKS[2][1]:
                return completion(None)
            return good(method, path, headers, timeout, json)

        records = self.evaluate(post)
        self.assertEqual([r["passed"] for r in records], [True, True, False, True, True])
        self.assertEqual(
            records[2]["error"], "The request did not complete (AttributeError)."
        )

    def test_main_needs_the_output_path_before_it_contacts_the_server(self):
        with (
            patch.object(acceptance, "send", side_effect=AssertionError),
            self.assertRaises(SystemExit) as stop,
        ):
            acceptance.main([])
        self.assertIn("report path", stop.exception.code)

    def test_main_takes_an_option_or_a_folder_as_a_mistake_not_a_report_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            for given, said in (("--help", "report path"), ("-h", "report path"), (tmp, "cannot be written")):
                with (
                    self.subTest(given=given),
                    patch.object(acceptance, "send", side_effect=AssertionError),
                    self.assertRaises(SystemExit) as stop,
                ):
                    acceptance.main([given])
                self.assertIn(said, stop.exception.code)
            with (
                patch.object(acceptance.os, "access", return_value=False),
                patch.object(acceptance, "send", side_effect=AssertionError),
                self.assertRaises(SystemExit) as stop,
            ):
                acceptance.main([str(Path(tmp) / "acceptance.json")])
            self.assertIn("cannot be written", stop.exception.code)
        self.assertFalse(Path("--help").exists())

    def test_main_says_so_when_the_finished_report_cannot_be_saved(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(benchmark, "BASE", LOCAL),
            patch.object(acceptance, "first_model", return_value="m"),
            patch.object(acceptance, "send", acceptance_server(call_message())),
            patch.object(Path, "write_text", side_effect=PermissionError(13, "denied")),
            self.assertRaises(SystemExit) as stop,
        ):
            quiet(acceptance.main, [str(Path(tmp) / "acceptance.json")])
        self.assertIn("could not be written", stop.exception.code)

    def test_send_keeps_the_key_from_proxies_and_redirects(self):
        made = []

        class Session:
            trust_env = True

            def __enter__(self):
                made.append(self)
                return self

            def __exit__(self, *exc):
                return False

            def request(self, method, url, **kwargs):
                self.call = (method, url, kwargs)
                return Reply({})

        headers = {"Authorization": "Bearer key-for-the-server"}
        for base, trusted in ((LOCAL, False), ("http://localhost:8080", False), ("https://example.org", True)):
            with (
                self.subTest(base=base),
                patch.object(acceptance, "BASE", base),
                patch.object(acceptance.requests, "Session", Session),
            ):
                acceptance.send("POST", "/v1/chat/completions", headers, 180, {"model": "m"})
            session = made[-1]
            self.assertIs(session.trust_env, trusted)
            self.assertEqual(session.call[:2], ("POST", base + "/v1/chat/completions"))
            self.assertEqual(
                session.call[2],
                {"headers": headers, "json": {"model": "m"}, "timeout": 180, "allow_redirects": False},
            )

    def test_first_model_does_not_take_a_redirect_for_a_model_list(self):
        with (
            patch.object(acceptance, "send", return_value=Reply(None, 302)),
            self.assertRaises(SystemExit) as stop,
        ):
            acceptance.first_model({})
        self.assertIn("did not list a model", stop.exception.code)

    def test_main_checks_the_report_folder_before_it_contacts_the_server(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(acceptance, "send", side_effect=AssertionError),
            self.assertRaises(SystemExit) as stop,
        ):
            acceptance.main([str(Path(tmp) / "missing" / "out.json")])
        self.assertIn("folder for the report", stop.exception.code)

    def test_first_model_says_what_to_do_when_the_key_is_rejected(self):
        with (
            patch.object(acceptance, "send", return_value=Reply({"error": "x"}, 401)),
            self.assertRaises(SystemExit) as stop,
        ):
            acceptance.first_model({})
        self.assertIn("WORKBENCH_API_KEY", stop.exception.code)

    def test_first_model_says_what_to_do_when_nothing_answers(self):
        with (
            patch.object(acceptance, "send", side_effect=requests.ConnectionError),
            self.assertRaises(SystemExit) as stop,
        ):
            acceptance.first_model({})
        self.assertIn("start the model", stop.exception.code)

    def test_main_writes_the_report_and_exits_nonzero_when_a_request_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "acceptance.json"
            with (
                patch.object(benchmark, "BASE", LOCAL),
                patch.object(acceptance, "first_model", return_value="m"),
                patch.object(
                    acceptance,
                    "send",
                    acceptance_server(call_message(), fail_on=benchmark.CHECKS[0][1]),
                ),
                self.assertRaises(SystemExit) as stop,
            ):
                quiet(acceptance.main, [str(out)])
            self.assertIn("partial report", stop.exception.code)
            self.assertEqual(len(json.loads(out.read_text(encoding="utf8"))), 5)


def adapter():
    tree = ast.parse((ROOT / "evals/harbor/omp_agent.py").read_text(encoding="utf-8"))
    names, scope = {}, {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            names[node.targets[0].id] = node.value.value
        if isinstance(node, ast.FunctionDef) and node.name == "_parse_usage":
            exec(compile(ast.Module([node], []), "omp_agent.py", "exec"), scope)
    return names, scope["_parse_usage"]


def event(kind, role=None, **usage):
    message = {"role": role, "content": []} | ({"usage": usage} if usage else {})
    return json.dumps({"type": kind, "message": message})


class HarborUsageTests(unittest.TestCase):
    def test_parse_usage_reads_the_marker_line(self):
        _, parse = adapter()
        self.assertEqual(
            parse('noise\nOMP_USAGE {"input": 3, "output": 1}\nmore'),
            {"input": 3, "output": 1},
        )
        self.assertIsNone(parse("no marker"))
        self.assertIsNone(parse("OMP_USAGE {broken}"))
        self.assertIsNone(parse(None))

    def test_bun_and_omp_installs_are_pinned(self):
        names, _ = adapter()
        self.assertRegex(names["OMP_VERSION"], r"^\d+\.\d+\.\d+$")
        self.assertRegex(names["BUN_RELEASE"], r"^bun-v\d+\.\d+\.\d+$")
        source = (ROOT / "evals/harbor/omp_agent.py").read_text(encoding="utf-8")
        self.assertIn("bash -s {BUN_RELEASE}", source)
        self.assertNotIn("| bash &&", source)

    @unittest.skipUnless(NODE, "Needs Node.js; the adapter runs in Linux containers")
    def test_usage_script_counts_each_finished_assistant_message_once(self):
        names, parse = adapter()
        first = dict(input=100, output=20, cacheRead=50, cacheWrite=5)
        second = dict(input=10, output=2, cacheRead=0, cacheWrite=0)
        lines = [
            '{"type":"session","version":3}',
            event("message_start", "user"),
            event("message_end", "user"),
            event("message_start", "assistant", input=0, output=0, cacheRead=0, cacheWrite=0),
            event("message_end", "assistant", **first),
            event("message_end", "toolResult", input=999, output=999),
            json.dumps({"type": "turn_end", "message": json.loads(event("x", "assistant", **first))["message"], "toolResults": []}),
            event("message_start", "assistant", **second),
            event("message_end", "assistant", **second),
            json.dumps({"type": "turn_end", "message": json.loads(event("x", "assistant", **second))["message"], "toolResults": []}),
            json.dumps({"type": "agent_end", "usage": first, "messages": []}),
            "warning: this line came from stderr",
            "null",
            "7",
            "",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "omp.jsonl"
            log.write_text("\n".join(lines), encoding="utf-8")
            done = subprocess.run(
                [NODE, "-e", names["_USAGE_JS"]],
                env=os.environ | {"OMP_TRANSCRIPT": str(log)},
                capture_output=True,
                text=True,
                timeout=20,
                stdin=subprocess.DEVNULL,
            )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            parse(done.stdout),
            {"input": 110, "output": 22, "cacheRead": 50, "cacheWrite": 5},
        )

    @unittest.skipUnless(NODE, "Needs Node.js; the adapter runs in Linux containers")
    def test_usage_script_prints_zeros_when_the_transcript_is_missing(self):
        names, parse = adapter()
        with tempfile.TemporaryDirectory() as tmp:
            done = subprocess.run(
                [NODE, "-e", names["_USAGE_JS"]],
                env=os.environ | {"OMP_TRANSCRIPT": str(Path(tmp) / "absent.jsonl")},
                capture_output=True,
                text=True,
                timeout=20,
                stdin=subprocess.DEVNULL,
            )
        self.assertEqual(
            parse(done.stdout), {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
        )



INKLING = ["sq20", "sq20-off", "sq20-128k", "inkling-q4-mainline", "inkling-q8"]
PERSONAL = [
    r"(?i)[\\/](users|home)[\\/]",
    r"(?i)\b[a-z]:[\\/]",
    r"(?i)appdata|/mnt/[a-z]/|~/|/root\b|/private/|\.local\b",
    r"(?i)\b(desktop|laptop)-[a-z0-9]{5,}\b|\blocalhost\b|\.lan\b",
    r"[\w.+-]+@[\w-]+\.[a-z]{2,}",
    r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b(?!\.)",
    r"https?://",
    r"sk-[A-Za-z0-9]{8,}|hf_[A-Za-z0-9]{8,}|ghp_[A-Za-z0-9]{8,}",
    r"(?i)api[_-]?key|bearer |password|PRIVATE KEY",
]


def result(name):
    return json.loads((RESULTS / name).read_text(encoding="utf-8"))


def half_up(value, places=1):
    exact = value if isinstance(value, Decimal) else Decimal(repr(value))
    return format(exact.quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP), ",")


def table(first_header):
    lines = TEXT.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("| " + first_header))
    rows = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows


def link(cell):
    found = re.fullmatch(r"\[(.+)\]\(results/(.+)\)", cell)
    return (found.group(1), result(found.group(2))) if found else (cell, None)


def held_back(path):
    """A report taken on battery power says not to quote it before a rerun."""
    report = json.loads(path.read_text(encoding="utf-8"))
    machine = report.get("machine") if isinstance(report, dict) else None
    return isinstance(machine, dict) and machine.get("power") == "battery"


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


class MeasurementsTest(unittest.TestCase):
    def test_every_results_link_points_at_a_file(self):
        links = re.findall(r"\]\((results/[^)]*|evals/[^)]*)\)", TEXT)
        self.assertGreater(len(links), 25)
        for target in links:
            self.assertTrue((ROOT / target).exists(), target)

    def test_every_result_file_is_named_in_the_document(self):
        for path in RESULTS.rglob("*.json"):
            if path.name.endswith("-acceptance.json"):
                self.assertIn("`*-acceptance.json`", TEXT)
            elif path.name.startswith("metal-m3-max-"):
                self.assertIn("`metal-m3-max-*.json`", TEXT)
            else:
                self.assertIn(f"`{path.name}`", TEXT, path.name)

    def test_profile_table_matches_the_reports(self):
        rows = table("Profile")
        self.assertEqual(len(rows), 7)
        for profile, context, rate, short, long in rows:
            with self.subTest(profile=profile, context=context):
                shown, report = link(rate)
                self.assertEqual(shown, half_up(report["median_decode_tps"]))
                self.assertEqual(
                    short, f"{report['checks_passed']}/{report['checks_total']}"
                )
                recorded = re.search(r"\b\d{4,6}\b", report["condition"])
                if recorded:
                    self.assertEqual(context.replace(",", ""), recorded.group())
                else:
                    self.assertTrue(context.endswith("³"), context)
                shown, checks = link(long)
                if checks is not None:
                    self.assertEqual(
                        shown, f"{sum(r['passed'] for r in checks)}/{len(checks)}"
                    )
                else:
                    self.assertIn(shown, ("—", "5/5²"))

    def test_inkling_speed_responses_and_fixture_sizes(self):
        speed = [r for name in INKLING for r in result(name + ".json")["speed"]]
        self.assertTrue(all(r["text"] == "" and r["completion_tokens"] == 192 for r in speed))
        self.assertIn(f"All {len(speed)} short coding responses in the five Inkling rows", TEXT)
        sizes = {
            name: result(name + ".json")["prefill"][1]["prompt_tokens"]
            for name in INKLING + ["qwen-pi", "flash-dflash2"]
        }
        self.assertEqual(set(sizes.values()), {13773, 16347})
        self.assertIn("13,773 Inkling tokens and 16,347 Qwen tokens", TEXT)
        for name in INKLING + ["qwen-pi", "flash-dflash2"]:
            report = result(name + ".json")
            rows = report["speed"] + report["prefill"] + report["checks"]
            self.assertTrue(all(r["prefill_tps"] is not None for r in rows), name)

    def test_flash_logic_miss_and_older_acceptance_reports(self):
        logic = result("flash-dflash2.json")["checks"][1]
        self.assertEqual(
            (logic["name"], logic["text"], logic["completion_tokens"], logic["passed"]),
            ("logic", "[2, 9, 4]", 10, False),
        )
        self.assertIn("`logic` returned `[2, 9, 4]` after 10 of its 96 tokens", TEXT)
        self.assertEqual(result("flash-dflash2-acceptance.json")[1]["answer"], "[2, 9, 4, 7]")
        archived = [
            json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((RESULTS / "archive").glob("*.json"))
        ]
        archived = [r for r in archived if "median_decode_tps" in r]
        self.assertEqual(len(archived), 4)
        self.assertTrue(all(r["checks"][1]["passed"] and len(r["speed"]) == 6 for r in archived))
        self.assertIn("in the four archived six-request runs", TEXT)
        finish = {
            p.name.removesuffix("-acceptance.json"): json.loads(p.read_text(encoding="utf-8"))[4]["finish"]
            for p in RESULTS.glob("*-acceptance.json")
        }
        older = sorted(name for name, value in finish.items() if value == "tool_calls")
        self.assertEqual(
            older, sorted(["sq20", "inkling-q4-mainline", "inkling-q8", "qwen-pi", "flash-dflash2"])
        )
        self.assertEqual(finish["sq20-32k"], "stop")
        self.assertEqual(
            [r["tokens"] for r in result("sq20-acceptance.json")],
            [r["tokens"] for r in result("sq20-32k-acceptance.json")],
        )

    def test_cited_reports_keep_their_grades_under_the_current_check(self):
        expected = {name: value for name, _, value in benchmark.CHECKS}
        graded = 0
        for path in sorted(RESULTS.rglob("*.json")):
            if held_back(path):
                continue
            report = json.loads(path.read_text(encoding="utf-8"))
            if path.name.endswith("-acceptance.json"):
                rows = [(r["name"], r["answer"], r["passed"]) for r in report]
            else:
                body = report.get("benchmark", report) if isinstance(report, dict) else {}
                checks = body.get("checks") if isinstance(body, dict) else None
                rows = [
                    (r["name"], r["text"], r["passed"])
                    for r in checks or []
                    if "text" in r
                ]
            for name, text, passed in rows:
                if name in expected:
                    graded += 1
                    self.assertEqual(
                        benchmark.equal_content(text, expected[name]),
                        passed,
                        f"{path.name}: {name}",
                    )
        self.assertGreaterEqual(graded, 68)
        self.assertIn(
            "now also accept a correct answer wrapped in one Markdown code fence, "
            "and no longer accept `true` or `false` in place of `1` or `0`. "
            "Regrading the benchmark and acceptance reports cited in this document "
            "with the current check reproduces every stored pass and fail.",
            TEXT,
        )

    def test_metal_files_are_named_and_their_figures_left_out(self):
        expected = {name: value for name, _, value in benchmark.CHECKS}
        paths = sorted(RESULTS.glob("metal-m3-max-*.json"))
        self.assertEqual(len(paths), 5)
        self.assertEqual(paths, sorted(p for p in RESULTS.rglob("*.json") if held_back(p)))
        regraded = []
        for path in paths:
            report = json.loads(path.read_text(encoding="utf-8"))
            machine, engine, body = report["machine"], report["engine"], report["benchmark"]
            self.assertEqual(
                (machine["chip"], machine["unified_memory_gib"], machine["power"]),
                ("Apple M3 Max", 48.0, "battery"),
            )
            self.assertIn("rerun on mains power before quoting", machine["note"])
            self.assertEqual((engine["backend"], engine["context"]), ("Metal", 32768))
            self.assertEqual(report["date"], "2026-11-22")
            self.assertGreater(report["memory"]["peak_server_rss_gib"], 0)
            self.assertEqual(list(body)[:3], ["condition", "model", "parallel"])
            self.assertNotIn("meta", body)
            calls = [r["passed"] for r in body["checks"] if r["name"] == "tool-call"]
            self.assertEqual(calls, [True])
            regraded += [
                (r["name"], r["passed"])
                for r in body["checks"]
                if r["name"] in expected
                and benchmark.equal_content(r["text"], expected[r["name"]]) != r["passed"]
            ]
        self.assertEqual(regraded, [("extraction", False)] * 2)
        self.assertNotIn("results/metal-", TEXT)
        for phrase in (
            "No figure here was measured on macOS or Apple Metal.",
            "The five `metal-m3-max-*.json` files in `results/` are dated November 22, 2026, "
            "and come from a 48 GB Apple M3 Max on battery power.",
            "a model loaded at 32,768 context, of its peak memory and of a passed tool call",
            "Their speeds are not quoted here, because each file says to rerun on mains power first.",
            "The five Metal files are not among them: an earlier check graded them, "
            "and two store a fail for a correct answer inside a code fence.",
        ):
            self.assertIn(phrase, TEXT)

    def test_terminal_bench_rows_match_the_aggregate(self):
        rows = table("DeepSeek V4.1 Flash 1017, OMP 18.12.5")
        runs = result("harbor-terminal-bench-21.json")
        self.assertEqual(len(rows), len(runs))
        for row, run in zip(rows, runs):
            minutes = (run["wall_seconds"] + 30) // 60
            spend = run["reported_model_spend_usd"]
            self.assertEqual(
                row,
                [
                    run["reasoning"].capitalize() + " reasoning",
                    f"{run['passed']}/{run['attempts']} ({half_up(100 * Decimal(run['passed']) / run['attempts'])}%)",
                    str(run["errored"]),
                    f"{minutes // 60}h {minutes % 60}m",
                    f"${spend:.2f}",
                    "$" + half_up(Decimal(repr(spend)) / run["attempts"], 3),
                ],
            )
            self.assertIsNone(run["harbor_cost_usd"])
            self.assertEqual((run["agent"], run["concurrency"]), ("OMP 18.12.5", 3))

    def test_amd_figures(self):
        amd = result("amd-vulkan-iq3.json")
        probe = amd["longer_probe"]
        self.assertEqual(len(probe), 4)
        for phrase in (
            f"{amd['gpu'].removeprefix('AMD ')}, {amd['gpu_gib']} GiB available device capacity and {amd['ram_gib']} GiB system RAM",
            f"ran at {amd['context']:,} context",
            f"The {probe['output_tokens']}-output-token code probe decoded at **{half_up(probe['decode_tps'])} tokens/s** and sampled **{probe['sampled_peak_device_gib']} GiB**",
            f"decoded at {half_up(amd['timings']['predicted_per_second'])} tokens/s",
            "llama.cpp b12633 with Vulkan",
        ):
            self.assertIn(phrase, TEXT)
        self.assertEqual(amd["engine"], "llama.cpp b12633 Vulkan")

    def test_context_and_vision_figures(self):
        rows = table("SQ-20 setting")
        self.assertEqual(len(rows), 2)
        for row in rows:
            _, run = link(row[2])
            self.assertEqual(
                row[1:2] + row[3:],
                [
                    f"{run['usage']['prompt_tokens']:,} tokens",
                    f"{run['seconds']} s",
                    f"{run['peak_device_gib']} GiB",
                ],
            )
        self.assertTrue(link(rows[0][2])[1]["passed"])
        self.assertFalse(link(rows[1][2])[1]["passed"])
        rates = [half_up(link(r[2])[1]["timings"]["prompt_per_second"], 0) for r in rows]
        self.assertIn(
            f"processed the input at {rates[0]} tokens/s. The 128K FP8 run processed it at {rates[1]} tokens/s",
            TEXT,
        )
        vision = result("inkling-capacity.json")[0]
        self.assertIn(f"“{vision['answer']}” for a generated solid-red image in {vision['seconds']} seconds", TEXT)

    def test_omp_and_single_request_figures(self):
        runs = result("omp-inkling.json")
        times = [r["elapsed_s"] for r in runs]
        self.assertTrue(all(r["passed"] for r in runs))
        self.assertEqual(sorted(r["supplement"] for r in runs), [False, False, True, True])
        self.assertIn(f"ranged from {half_up(min(times), 2)} to {half_up(max(times), 2)} seconds", TEXT)
        single = result("sq20-single-1024.json")
        wall = [half_up(r["wall_tokens_s"]) for r in single["rows"]]
        engine = [half_up(r["timings"]["predicted_per_second"]) for r in single["rows"]]
        self.assertIn(f"Wall-clock rates were {wall[0]}, {wall[1]} and {wall[2]} tokens/s", TEXT)
        self.assertIn(f"Engine decode rates were {engine[0]}, {engine[1]} and {engine[2]} tokens/s", TEXT)
        self.assertEqual((single["context"], single["draft"]), (131072, True))
        self.assertTrue(all(r["visible_chars"] == 0 and r["output_tokens"] == 1024 for r in single["rows"]))
        self.assertIn(
            f"short-prompt 32K result was {half_up(result('sq20.json')['median_decode_tps'])} tokens/s",
            TEXT,
        )

    def test_plugin_and_tool_load_figures(self):
        cost = result("web-plugin-cost.json")
        by_name = {s["name"]: s["tokens"] for s in cost["servers"]}
        self.assertIn(
            f"**{by_name['search']} input tokens** for search alone, **{by_name['firecrawl']}** for Firecrawl alone, and **{cost['tokens']}** together",
            TEXT,
        )
        trace = result("omp-direct-web.json")["calls"]
        self.assertEqual(sorted(c[1] for c in trace if c[0] == "tool_execution_end" and c[2] is False), ["mcp__firecrawl_scrape", "mcp__search_web_search"])
        rows = table("Synthetic tools available")
        load = result("tool-load.json")["rows"]
        self.assertEqual(len(rows), len(load))
        for row, load_row in zip(rows, load):
            cases = load_row["cases"]
            self.assertEqual(
                row,
                [
                    str(load_row["tools"]),
                    half_up(statistics.mean(c["additional_input_tokens"] for c in cases), 0),
                    f"{sum(c['passed'] for c in cases)}/{len(cases)}",
                    f"{statistics.median(c['elapsed_s'] for c in cases)} s",
                ],
            )

    def test_archive_figures(self):
        drafted, plain = result("archive/reserve-dflash2.json"), result("archive/reserve-off.json")
        self.assertIn(
            f"reached {half_up(drafted['median_decode_tps'])} tokens/s with DFlash2 and {half_up(plain['median_decode_tps'])} with drafting off, and both passed 5/5",
            TEXT,
        )
        self.assertEqual((drafted["checks_passed"], plain["checks_passed"]), (5, 5))
        upstream = [result("archive/standard-dflash2.json"), result("archive/standard-off.json")]
        scores = [f"{u['quality']['passed']}/{u['quality']['total']}" for u in upstream]
        self.assertIn(f"{scores[0]} with DFlash2 and {scores[1]} with drafting off", TEXT)
        medians = [
            half_up(next(s["median"] for s in u["summaries"] if (s["phase"], s["case_id"], s["metric"]) == ("performance", "short", "decode_tps")))
            for u in upstream
        ]
        self.assertIn(f"medians were {medians[0]} and {medians[1]} tokens/s", TEXT)
        for u in upstream:
            self.assertEqual(u["run"]["status"], "nonconforming")
            self.assertTrue(u["run"]["started_at"].startswith("2026-11-17"))
            self.assertEqual(u["software"]["engine_version"], "0.1.60")
            sampling = u["configuration"]["sampling"]
            self.assertEqual((sampling["temperature"], sampling["reasoning_effort"]), (1.0, "xhigh"))
        self.assertIn("engine 0.1.60, temperature 1.0 and reasoning effort xhigh", TEXT)
        self.assertIn("`nonconforming`", TEXT)
        self.assertIn(
            f"The {half_up(result('flash-dflash2.json')['median_decode_tps'])} result in the profile table",
            TEXT,
        )
        pack = next(w for w in result("weights.json") if w["file"] == "Inkling-2-Mini-SQ-20.strata")
        self.assertIn(f"{half_up(pack['bytes'] / 1e9, 2)} GB SQ-20 pack", TEXT)

    def test_workstation_matches_the_recorded_hardware(self):
        machine = result("archive/standard-dflash2.json")
        self.assertIn("RTX 5090", machine["hardware"]["gpus"][0]["name"])
        self.assertIn("Ryzen 9 9950X3D", machine["hardware"]["cpu"])
        self.assertIn(f"NVIDIA driver {machine['software']['drivers'][0]['version']}", TEXT)

    def test_result_files_hold_no_personal_data(self):
        for path in sorted(RESULTS.rglob("*.json")):
            text = "\n".join(strings(json.loads(path.read_text(encoding="utf-8"))))
            for pattern in PERSONAL:
                self.assertIsNone(re.search(pattern, text), f"{path.name}: {pattern}")


if __name__ == "__main__":
    unittest.main()
