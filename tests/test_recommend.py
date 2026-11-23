"""Recommender arithmetic, ranking and reasons, plus rules for the shipped catalog."""

import copy
import json
import re
import unittest
from pathlib import Path

import recommend
from recommend import (
    budget,
    need,
    rate,
    reason,
    sizing_backend,
    working_set_estimate,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "model_catalog.json"
SIZING = (
    "kv_kib_per_token",
    "kv_fixed_gib",
    "compute_gib",
    "compute_gib_metal",
    "token_overhead_bytes",
)
QWEN35 = (32, 0.0, 0.6, 2.0, 0)
QWEN38 = (64, 0.0, 1.0, 2.0, 0)
INKLING = (32, 0.16, 1.0, 2.0, 24576)
FLASH = (24, 0.0, 1.0, 2.0, 0)
MB = 1024**2
RTX = "an RTX 5090 (32 GB) on Windows"
SUGGESTED = "A suggestion that has not been run on this hardware."
MACHINE = "an M3 Max 48 GB"
TRIED = f"A suggestion tested on {MACHINE}, where it loaded, answered and called a tool."
TRIAL = {
    "machine": MACHINE,
    "peak_memory_gib": 24.1,
    "context": 32768,
    "tool_call": "passed",
}
POWER_WORDS = re.compile(r"battery|mains|power|speed|slow|fast|token", re.I)


def entry(
    ident, model, backends, tier, rank, size, context, kv="f16", family=QWEN38, **more
):
    item = dict(
        id=ident,
        model=model,
        label=model,
        engine="llama.cpp",
        licence="Apache-2.0",
        licence_note="",
        backends=list(backends),
        min_memory_gib=tier,
        rank=rank,
        status="suggested",
        tool_calling="ok",
        why="A fixture pick.",
        settings=dict(
            engine="llama.cpp",
            kv="auto",
            draft=False,
            lora=False,
            vision=False,
            context=context,
        ),
        kv=kv,
        extras=[],
        resident_bytes=size,
        lora_bytes=0,
        memory_model=dict(method="formula", **dict(zip(SIZING, family))),
    )
    if "peak" in more:
        item["memory_model"] = dict(method="measured", need_gib=more.pop("peak"))
    if more.pop("tier_only", False):
        item["memory_model"] = dict(method="tier")
    item.update(more)
    return item


def small(ident, model, tier, rank, backends=("cuda",), **more):
    return entry(ident, model, backends, tier, rank, 100 * MB, 4096, **more)


def gpu(memory, backend="cuda", **more):
    return dict(backend=backend, ram_gib=64.0, memory_gib=memory, **more)


def mac(ram, working_set=None, **more):
    facts = dict(backend="metal", ram_gib=float(ram), memory_gib=float(ram))
    return dict(facts, working_set_gib=working_set, **more)


def processor(ram, **more):
    return dict(backend="cpu", ram_gib=float(ram), memory_gib=float(ram), **more)


def ids(result):
    return [item["id"] for item in [result["pick"], *result["alternatives"]] if item]


IQ3 = dict(size=10934860704, context=32768, kv="q8_0")
Q4 = dict(size=17659407264, context=32768, kv="q8_0")
Q6 = dict(size=25299061664, context=65536, kv="q8_0")
SMALLEST = dict(size=2740937888, context=16384, family=QWEN35)


class NeedAndBudgetTest(unittest.TestCase):
    def test_need_matches_research_examples(self):
        mtp = [{"label": "MTP draft head", "gib": 2.9}]
        shard, card, apple = 54817524224, ["cuda"], ["metal"]
        nine = entry("a", "9b", card, 8, 1, 5680522464, 16384, family=QWEN35)
        drafted = entry("c", "q4", card, 24, 1, extras=mtp, **Q4)
        mainline = entry("d", "ink", card, 24, 2, 17629760736, 32768, family=INKLING)
        eight = entry("e", "q8", card, 48, 2, 29047086048, 131072, "q8_0")
        widest = entry("h", "fn", apple, 96, 1, shard, 131072, family=FLASH)
        rows = [
            (nine, "cuda", 6.39),
            (entry("b", "iq3", card, 16, 1, **IQ3), "vulkan", 12.18),
            (drafted, "cuda", 21.35),
            (mainline, "cuda", 19.33),
            (eight, "cuda", 32.05),
            (entry("f", "fn", card, 64, 1, shard, 65536, family=FLASH), "cuda", 53.55),
            (entry("g", "q6", apple, 48, 1, **Q6), "metal", 27.56),
            (widest, "metal", 56.05),
        ]
        for item, backend, expected in rows:
            with self.subTest(item["id"]):
                self.assertEqual(need(item, backend)["need_gib"], expected)
        parts = need(drafted, "cuda")
        self.assertEqual(
            (parts["method"], parts["weights_gib"], parts["context_gib"]),
            ("formula", 16.45, 1.0),
        )
        self.assertEqual((parts["compute_gib"], parts["extras_gib"]), (1.0, 2.9))
        adapter = entry("i", "q4", ["cuda"], 32, 2, lora_bytes=318802304, **Q4)
        self.assertEqual(need(adapter, "cuda")["extras_gib"], 0.3)
        quarter = entry("j", "q4", ["cuda"], 24, 1, 17659407264, 32768, "q4_0")
        self.assertEqual(need(quarter, "cuda")["context_gib"], 0.56)

    def test_budget_rules(self):
        self.assertEqual(budget(gpu(23.99))["usable_gib"], 22.07)
        self.assertEqual(budget(gpu(12.0, "vulkan"))["usable_gib"], 10.5)
        self.assertEqual(budget(gpu(12.0))["kind"], "vram")
        self.assertEqual(budget(processor(8))["usable_gib"], 4.0)
        self.assertEqual(budget(processor(64))["usable_gib"], 48.0)
        self.assertEqual(budget(processor(64))["kind"], "ram")
        measured = budget(mac(48, 37.44))
        self.assertEqual(
            (measured["kind"], measured["usable_gib"], measured["overhead_gib"]),
            ("unified", 36.44, 14.0),
        )
        estimated = budget(mac(16))
        self.assertEqual(
            (estimated["usable_gib"], estimated["overhead_gib"]), (9.56, 12.0)
        )
        self.assertEqual(budget(mac(8))["overhead_gib"], 9.5)
        self.assertNotIn("overhead_gib", budget(gpu(24.0)))

    def test_usable_memory_is_never_negative(self):
        for ram in (0, 2, 3.9):
            with self.subTest(ram):
                self.assertEqual(budget(processor(ram))["usable_gib"], 0.0)
        undetected = rate(small("a", "m", 0, 1, ["cpu"]), processor(0))["sizing"]
        self.assertEqual(undetected["fit"], "no")
        self.assertIn("usable 0.00 of 0.00 GiB", undetected["arithmetic"])
        self.assertNotIn("-", undetected["arithmetic"])

    def test_working_set_estimate(self):
        self.assertEqual(working_set_estimate(16), 10.56)
        self.assertEqual(working_set_estimate(32), 21.12)
        self.assertEqual(working_set_estimate(48), 36.0)

    def test_tier_gate_and_slack(self):
        item = small("a", "m", 32, 1)
        below = rate(item, gpu(31.4))["sizing"]
        self.assertEqual((below["fit"], below["tier_ok"]), ("no", False))
        nominal = rate(item, gpu(31.84))["sizing"]
        self.assertEqual((nominal["fit"], nominal["tier_ok"]), ("comfortable", True))
        self.assertTrue(rate(item, gpu(31.5))["sizing"]["tier_ok"])
        self.assertFalse(rate(item, gpu(31.49))["sizing"]["tier_ok"])
        apple = small("b", "m", 32, 1, ["metal"])
        self.assertFalse(rate(apple, mac(31.4))["sizing"]["tier_ok"])

    def test_system_memory_tier_allows_for_reserved_memory(self):
        item = small("a", "m", 32, 1, ["cpu"])
        for ram, expected in ((32.0, True), (30.9, True), (30.0, True), (29.9, False)):
            with self.subTest(ram):
                sizing = rate(item, processor(ram))["sizing"]
                self.assertEqual(sizing["tier_ok"], expected)
                self.assertEqual(sizing["fit"], "comfortable" if expected else "no")
        unread = dict(backend="vulkan", ram_gib=30.9, memory_gib=None)
        self.assertTrue(rate(item, unread)["sizing"]["tier_ok"])
        self.assertFalse(rate(small("b", "m", 32, 1), gpu(30.9))["sizing"]["tier_ok"])

    def test_fit_levels(self):
        def sized(item, hardware, *keys):
            sizing = rate(item, hardware)["sizing"]
            return tuple(sizing[key] for key in ("fit", *keys))

        roomy = entry("a", "q6", ["metal"], 48, 1, **Q6)
        self.assertEqual(
            sized(roomy, mac(48, 37.44), "need_gib"), ("comfortable", 27.56)
        )
        close = entry("b", "q4", ["metal"], 32, 1, **Q4)
        self.assertEqual(
            sized(close, mac(32), "need_gib", "usable_gib"), ("tight", 19.45, 20.12)
        )
        over = entry("c", "q4", ["cuda"], 0, 1, **Q4)
        self.assertEqual(sized(over, gpu(15.99), "tier_ok"), ("no", True))
        packed = entry("d", "sq", ["cuda"], 32, 1, 19346685952, 32768, tier_only=True)
        self.assertEqual(
            sized(packed, gpu(31.84), "need_gib", "method"), ("listed", None, "tier")
        )
        peak = entry("e", "sq", ["cuda"], 48, 1, 19346685952, 65536, "fp8", peak=27.32)
        known = rate(peak, gpu(48.0))["sizing"]
        self.assertEqual(
            (known["fit"], known["need_gib"], known["method"]),
            ("comfortable", 27.32, "measured"),
        )
        self.assertIsNone(known["context_gib"])
        self.assertEqual(rate(peak, gpu(28.0))["sizing"]["fit"], "no")
        exact = entry("f", "m", ["cuda"], 0, 1, 100 * MB, 4096, peak=22.07)
        self.assertEqual(rate(exact, gpu(23.99))["sizing"]["fit"], "comfortable")
        over = dict(exact, memory_model=dict(method="measured", need_gib=22.08))
        self.assertEqual(rate(over, gpu(23.99))["sizing"]["fit"], "no")
        level = entry("g", "m", ["metal"], 0, 1, 100 * MB, 4096, peak=34.0)
        self.assertEqual(rate(level, mac(48, 37.44))["sizing"]["fit"], "comfortable")
        above = dict(level, memory_model=dict(method="measured", need_gib=34.01))
        self.assertEqual(rate(above, mac(48, 37.44))["sizing"]["fit"], "tight")

    def test_arithmetic_text(self):
        mtp = [{"label": "MTP draft head", "gib": 2.9}]
        formula = rate(entry("a", "q4", ["cuda"], 24, 1, extras=mtp, **Q4), gpu(23.99))
        self.assertEqual(
            formula["sizing"]["arithmetic"],
            "16.45 weights + 1.00 context + 1.00 compute + 2.90 extras = 21.35 GiB; "
            "usable 22.07 of 23.99 GiB (GPU memory minus the larger of 1.5 GiB and 8%)",
        )
        peak = entry("b", "q4", ["metal"], 36, 1, 17659407264, 32768, peak=24.06)
        self.assertEqual(
            rate(peak, mac(48, 37.44))["sizing"]["arithmetic"],
            "24.06 GiB measured peak; usable 36.44 of 48.00 GiB "
            "(Metal working set minus 1.0 GiB)",
        )
        packed = entry("c", "sq", ["cuda"], 32, 1, 19346685952, 32768, tier_only=True)
        self.assertEqual(
            rate(packed, gpu(31.84))["sizing"]["arithmetic"],
            "listed for 32 GB; memory use was not recorded; usable 29.29 of 31.84 GiB "
            "(GPU memory minus the larger of 1.5 GiB and 8%)",
        )
        slow = rate(small("d", "m", 0, 1, ["cpu"]), processor(32))["sizing"]
        self.assertTrue(
            slow["arithmetic"].endswith(
                "(system memory minus the larger of 4.0 GiB and 25%)"
            )
        )


class RankingTest(unittest.TestCase):
    def test_order_and_distinct_models(self):
        entries = [
            small("low", "five", 0, 1),
            small("mid", "four", 8, 1),
            small("same-model", "one", 16, 1),
            small("other", "three", 16, 2),
            small("second", "two", 24, 2),
            small("first", "one", 24, 1),
            small("too-big", "six", 32, 1),
        ]
        result = recommend.recommend(gpu(23.99), entries)
        self.assertEqual(ids(result), ["first", "second", "other"])
        self.assertEqual(len(result["alternatives"]), 2)
        self.assertEqual(result["pick"]["sizing"]["fit"], "comfortable")
        tied = [small("b", "two", 24, 1), small("a", "one", 24, 1)]
        self.assertEqual(ids(recommend.recommend(gpu(23.99), tied)), ["a", "b"])
        self.assertEqual(ids(recommend.recommend(gpu(23.99, "vulkan"), entries)), [])

    def test_engines_filter(self):
        packed = small("strata", "sq", 32, 1, engine="Strata", tier_only=True)
        entries = [packed, small("plain", "q4", 32, 2), small("lower", "iq3", 16, 1)]
        engines = ["Auto", "llama.cpp", "Custom"]
        limited = recommend.recommend(gpu(31.84), entries, engines)
        self.assertEqual(ids(limited), ["plain", "lower"])
        chosen = [limited["pick"], *limited["alternatives"]]
        self.assertEqual({item["engine"] for item in chosen}, {"llama.cpp"})
        self.assertEqual(
            ids(recommend.recommend(gpu(31.84), entries)), ["strata", "plain", "lower"]
        )
        self.assertIsNone(recommend.recommend(gpu(31.84), [packed], engines)["pick"])

    def test_try_fallback_and_no_pick(self):
        both = ["metal", "cpu"]
        bigger = entry("9b", "9b", both, 16, 1, 5680522464, 32768, peak=8.57)
        smallest = entry("4b", "4b", both, 0, 1, 2740937888, 32768, peak=5.96)
        result = recommend.recommend(mac(8), [bigger, smallest])
        self.assertEqual(ids(result), ["4b"])
        self.assertEqual(result["pick"]["sizing"]["fit"], "try")
        self.assertEqual(result["alternatives"], [])
        below_tier = recommend.recommend(gpu(6.0), [small("a", "m", 8, 1)])
        fallback = below_tier["pick"]["sizing"]
        self.assertEqual((fallback["fit"], fallback["tier_ok"]), ("try", False))
        none = recommend.recommend(processor(4), [bigger, smallest])
        self.assertIsNone(none["pick"])
        self.assertEqual(none["alternatives"], [])
        self.assertTrue(
            none["reason"].startswith("No catalog model fits 4 GiB of system memory")
        )
        half = entry("h", "h", ["cpu"], 0, 1, 3 * 1024**3, 32768, peak=9.0)
        at_half = recommend.recommend(processor(6), [half])
        self.assertEqual(at_half["pick"]["sizing"]["fit"], "try")
        self.assertIsNone(recommend.recommend(processor(5.9), [half])["pick"])
        empty = recommend.recommend(gpu(24.0), [])
        self.assertIsNone(empty["pick"])
        self.assertTrue(
            empty["reason"].startswith("No catalog model fits 24 GiB of GPU memory")
        )

    def test_unknown_gpu_memory_sizes_on_ram(self):
        hardware = dict(
            backend="cuda", ram_gib=32.0, memory_gib=None, gpu_vendor="nvidia"
        )
        self.assertEqual(sizing_backend(hardware), "cpu")
        self.assertEqual(sizing_backend(gpu(24.0)), "cuda")
        self.assertEqual(sizing_backend(mac(48)), "metal")
        entries = [
            small("card-only", "big", 24, 1),
            small("processor", "9b", 32, 1, ["cpu"]),
            small("anything", "4b", 0, 1, ["cuda", "cpu"]),
        ]
        result = recommend.recommend(hardware, entries)
        self.assertEqual(ids(result), ["processor", "anything"])
        self.assertEqual(
            (result["backend"], result["sizing_backend"]), ("cuda", "cpu")
        )
        self.assertEqual(result["budget"]["kind"], "ram")
        chosen = [result["pick"], *result["alternatives"]]
        self.assertEqual({item["sizing"]["fit"] for item in chosen}, {"unknown"})
        self.assertIn("system memory", result["reason"])
        radeon = dict(hardware, backend="vulkan", gpu_vendor="amd")
        self.assertEqual(sizing_backend(radeon), "cpu")
        other = recommend.recommend(radeon, entries)
        self.assertEqual(ids(other), ["processor", "anything"])
        self.assertEqual((other["backend"], other["sizing_backend"]), ("vulkan", "cpu"))

    def test_recommend_does_not_mutate_inputs(self):
        hardware = gpu(23.99, free_gib=4.0, unknown=["One."], notes=["Two."])
        head = [{"label": "Head", "gib": 2.9}]
        entries = [
            entry("a", "q4", ["cuda"], 24, 1, extras=head, **Q4),
            entry("b", "iq3", ["cuda"], 16, 1, licence_note="Read the terms.", **IQ3),
            entry("c", "big", ["cuda"], 48, 1, 29047086048, 131072),
        ]
        before = copy.deepcopy((hardware, entries))
        result = recommend.recommend(hardware, entries)
        result["pick"]["sizing"]["fit"] = "changed"
        result["warnings"].append("changed")
        self.assertEqual((hardware, entries), before)
        self.assertNotIn("sizing", entries[0])

    def test_module_is_pure(self):
        source = Path(recommend.__file__).read_text(encoding="utf8")
        for line in source.splitlines():
            self.assertFalse(line.startswith(("import ", "from ")), line)
        self.assertNotIn("__import__", source)
        self.assertNotIn("open(", source)


class ReasonTest(unittest.TestCase):
    def advice(self, hardware, *entries):
        return recommend.recommend(hardware, list(entries))

    def test_reason_forms(self):
        plain = entry("a", "Qwen3.8-27B UD-IQ3_XXS", ["cuda"], 16, 1, **IQ3)
        fits = self.advice(gpu(15.99), plain)["reason"]
        self.assertEqual(
            fits,
            "Fits this GPU: it needs about 12.2 GiB at 32K context, and a model can "
            "use 14.5 of the 16.0 GiB of GPU memory here. " + SUGGESTED,
        )
        proven = dict(
            plain, status="measured", evidence="results/a.json", measured_on=RTX
        )
        measured = self.advice(gpu(15.99), proven)["reason"]
        self.assertEqual(
            measured,
            "Fits this GPU: it needs about 12.2 GiB at 32K context, and a model can "
            "use 14.5 of the 16.0 GiB of GPU memory here. This exact file was "
            "measured on an RTX 5090 (32 GB) on Windows.",
        )
        packed = entry(
            "b",
            "Inkling 2 Mini SQ-20",
            ["cuda"],
            32,
            1,
            19346685952,
            32768,
            tier_only=True,
            status="measured",
            measured_on=RTX,
        )
        listed = self.advice(gpu(31.84), packed)["reason"]
        self.assertEqual(
            listed,
            "Listed for 32 GB of GPU memory at 32K context, and this GPU has 31.8 GiB. "
            "This exact file was measured on an RTX 5090 (32 GB) on Windows.",
        )
        tight_entry = entry("c", "Qwen3.8-27B UD-Q4_K_XL", ["metal"], 32, 1, **Q4)
        tight = self.advice(mac(32), tight_entry)["reason"]
        self.assertEqual(
            tight,
            "A tight fit on this Mac: it needs an estimated 19.4 GiB at 32K context, "
            "and a model can use 20.1 of the 32.0 GiB of unified memory here. "
            + SUGGESTED,
        )
        smallest = entry("d", "Qwen3.5-4B Q4_K_M", ["cuda"], 8, 1, **SMALLEST)
        slow = self.advice(gpu(6.0), smallest)["reason"]
        self.assertEqual(
            slow,
            "Even the smallest model in the catalog is listed for 8 GB of GPU memory "
            "and this GPU has 6.0 GiB, so close other apps before you start it. "
            + SUGGESTED,
        )
        nothing = self.advice(processor(4), smallest)["reason"]
        self.assertEqual(
            nothing,
            "No catalog model fits 4 GiB of system memory, so use a hosted API or a "
            "machine over SSH, or choose a file yourself in Advanced → Model "
            "library.",
        )
        for text in (fits, measured, listed, tight, slow, nothing):
            self.assertTrue(text.endswith(".") and not text.endswith(".."), text)
            self.assertEqual("measured on" in text, text in (measured, listed), text)
            self.assertLessEqual(text.count(". "), 1, text)
            self.assertNotIn(";", text)
            self.assertIsNone(POWER_WORDS.search(text), text)

    def test_apple_reason_states_peak_and_what_the_trial_showed(self):
        name = "Qwen3.8-27B UD-Q4_K_XL"
        tested = entry("a", name, ["metal"], 36, 1, 17659407264, 32768, peak=24.1)
        fits = (
            "Fits this Mac with room to spare: its recorded peak is 24.1 GiB at 32K "
            "context, and a model can use 36.4 of the 48.0 GiB of unified memory here. "
        )
        bare = self.advice(mac(48, 37.44), tested)["reason"]
        self.assertEqual(bare, fits + SUGGESTED)
        tried = self.advice(mac(48, 37.44), dict(tested, evidence=TRIAL))["reason"]
        self.assertEqual(tried, fits + TRIED)
        no_call = dict(tested, evidence=dict(TRIAL, tool_call="failed"))
        self.assertEqual(
            self.advice(mac(48, 37.44), no_call)["reason"],
            fits + "A suggestion tested on an M3 Max 48 GB, where it loaded and answered.",
        )
        for odd in ("results/a.json", {}, dict(TRIAL, machine=""), None):
            with self.subTest(odd):
                unnamed = dict(tested, evidence=odd)
                self.assertEqual(self.advice(mac(48, 37.44), unnamed)["reason"], bare)
        computed = entry("b", "Qwen3.8-27B UD-Q6_K_XL", ["metal"], 48, 1, **Q6)
        self.assertEqual(
            self.advice(mac(48, 37.44), computed)["reason"],
            "Fits this Mac with room to spare: it needs an estimated 27.6 GiB at 64K "
            "context, and a model can use 36.4 of the 48.0 GiB of unified memory here. "
            + SUGGESTED,
        )
        nine = entry(
            "c",
            "Qwen3.5-9B Q4_K_M",
            ["metal"],
            16,
            1,
            5680522464,
            32768,
            peak=8.6,
            evidence=TRIAL,
        )
        tight = self.advice(mac(16), nine)["reason"]
        self.assertEqual(
            tight,
            "A tight fit on this Mac: its recorded peak is 8.6 GiB at 32K context, and "
            "a model can use 9.6 of the 16.0 GiB of unified memory here. " + TRIED,
        )
        four = entry(
            "d",
            "Qwen3.5-4B Q4_K_M",
            ["metal"],
            0,
            1,
            2740937888,
            32768,
            peak=5.9,
            evidence=TRIAL,
        )
        slow = self.advice(mac(8), four)["reason"]
        self.assertEqual(
            slow,
            "Even the smallest model in the catalog is more than this Mac can "
            "comfortably hold, so close other apps before you start it: its recorded "
            "peak is 5.9 GiB at 32K context, and a model can use only 4.3 of the 8.0 "
            "GiB of unified memory here. " + TRIED,
        )
        plugged = dict(
            tested,
            status="measured",
            evidence="results/a.json",
            measured_on="an M3 Max",
        )
        proven = self.advice(mac(48, 37.44), plugged)["reason"]
        self.assertTrue(proven.endswith(" This exact file was measured on an M3 Max."))
        self.assertIn("its recorded peak is 24.1 GiB", proven)
        self.assertNotIn("suggestion", proven)
        discrete = dict(tested, backends=["cuda"], min_memory_gib=24, evidence=TRIAL)
        elsewhere = self.advice(gpu(31.84), discrete)["reason"]
        self.assertIn("it needs about 24.1 GiB", elsewhere)
        self.assertNotIn("recorded", elsewhere)
        self.assertTrue(elsewhere.endswith(SUGGESTED))
        for text in (bare, tried, tight, slow, proven, elsewhere):
            self.assertIsNone(POWER_WORDS.search(text), text)

    def test_slow_fallback_reason_survives_unknown_gpu_memory(self):
        smallest = entry("d", "Qwen3.5-4B Q4_K_M", ["vulkan", "cpu"], 0, 1, **SMALLEST)
        laptop = dict(
            backend="vulkan", ram_gib=7.0, memory_gib=None, gpu_vendor="intel"
        )
        result = self.advice(laptop, smallest)
        self.assertEqual(result["pick"]["sizing"]["fit"], "unknown")
        self.assertEqual(
            result["reason"],
            "Even the smallest model in the catalog is more than this computer can "
            "comfortably hold, so close other apps before you start it: it needs about "
            "3.6 GiB at 16K context, and a model can use only 3.0 of the 7.0 GiB of "
            "system memory here. " + SUGGESTED,
        )
        self.assertIsNone(POWER_WORDS.search(result["reason"]), result["reason"])
        roomy = self.advice(dict(laptop, ram_gib=16.0), smallest)
        self.assertEqual(roomy["pick"]["sizing"]["fit"], "unknown")
        self.assertTrue(
            roomy["reason"].startswith(
                "Sized on system memory because the GPU memory could not be read: it "
                "needs about 3.6 GiB at 16K context"
            )
        )
        level = entry("e", "Level", ["cuda"], 0, 1, 100 * MB, 4096, peak=22.07)
        filled = self.advice(gpu(23.99), level)
        self.assertEqual(filled["pick"]["sizing"]["fit"], "comfortable")
        self.assertIn("Fits this GPU: it needs about 22.1 GiB", filled["reason"])

    def test_reason_without_a_pick_names_the_memory_pool(self):
        self.assertIn("8 GiB of unified memory", reason(None, budget(mac(8))))
        self.assertIn("12 GiB of GPU memory", reason(None, budget(gpu(11.99))))

    def test_warnings(self):
        plain = entry("a", "iq3", ["cuda", "vulkan", "cpu", "metal"], 16, 1, **IQ3)
        low = self.advice(gpu(15.99, free_gib=12.0), plain)["warnings"]
        self.assertEqual(
            low,
            [
                "Only 12.0 GiB of GPU memory is free right now; close other GPU apps "
                "before you start this model."
            ],
        )
        margin = self.advice(gpu(15.99, free_gib=13.0), plain)["warnings"]
        self.assertEqual(len(margin), 1)
        self.assertTrue(margin[0].startswith("Only 13.0 GiB of GPU memory is free"))
        quiet = (
            gpu(15.99, free_gib=13.2),
            gpu(15.99, free_gib=13.18),
            gpu(15.99, free_gib=None),
            gpu(15.99, "vulkan", gpu_vendor="amd"),
            mac(32, free_gib=2.0),
            processor(64, free_gib=2.0),
            processor(64, gpu_vendor="intel"),
            dict(backend="vulkan", ram_gib=64.0, memory_gib=None, gpu_vendor="intel"),
        )
        for hardware in quiet:
            self.assertEqual(self.advice(hardware, plain)["warnings"], [], hardware)
        arc = gpu(15.99, "vulkan", gpu_vendor="intel")
        self.assertEqual(
            self.advice(arc, plain)["warnings"],
            ["No Intel GPU has been tested with Agent Workbench."],
        )
        noted = dict(plain, licence_note="Read the model terms before you download.")
        passed = gpu(15.99, unknown=["Unknown fact."], notes=["Other fact."])
        self.assertEqual(
            self.advice(passed, noted)["warnings"],
            [
                "Unknown fact.",
                "Other fact.",
                "Read the model terms before you download.",
            ],
        )
        starved = gpu(
            4.0, free_gib=0.5, gpu_vendor="intel", unknown=["Unknown fact."]
        )
        huge = dict(noted, resident_bytes=29047086048)
        without = self.advice(starved, huge)
        self.assertIsNone(without["pick"])
        self.assertEqual(without["warnings"], ["Unknown fact."])


ID = re.compile(r"[a-z0-9][a-z0-9._-]{1,63}")
HEX40, HEX64 = re.compile(r"[0-9a-f]{40}"), re.compile(r"[0-9a-f]{64}")
RESULT = re.compile(r"results/[A-Za-z0-9._/-]+\.json")
MAC_SIZES = (8, 16, 18, 24, 32, 36, 48, 64, 96, 128)
ENGINE_OPTIONS = {
    "--cache-type-k",
    "--cache-type-v",
    "--cache-ram",
    "--ctx-checkpoints",
    "--spec-type",
    "--spec-draft-n-max",
}
EVIDENCE_KEYS = {"machine", "peak_memory_gib", "context", "tool_call"}
RAN = f"It loads, answers and calls a tool on {MACHINE}"
SPEED = re.compile(r"tok/s|tokens? per second|\btps\b", re.I)


def allow_listed(args):
    return set(args[0::2]) <= ENGINE_OPTIONS


def spare(ram):
    """GiB the sizing rule keeps for macOS and applications on a Mac of this size."""
    return 8.0 if ram > 16 else 6.0


def smallest_mac(peak):
    """The smallest Mac sold that holds this peak plus what macOS keeps."""
    return min(size for size in MAC_SIZES if size >= peak + spare(size))


def joined(raw, usable=lambda args: True):
    """Entries as the recommender receives them, with the evidence object kept."""
    models = {model["id"]: model for model in raw["models"]}
    entries = []
    for pick in raw["picks"]:
        model = models[pick["model"]]
        family = raw["families"][model["family"]]
        settings = {"engine": model["engine"], **pick["settings"]}
        args = settings.pop("engine_args", [])
        if args and usable(args):
            settings["engine_args"] = args
        elif args and pick.get("needs_engine_args"):
            continue
        kept = settings.get("engine_args", [])
        flags = dict(zip(kept[0::2], kept[1::2]))
        if model["engine"] == "Strata":
            cache = "fp8" if settings["kv"] == "fp8" else "f16"
        else:
            adapted = "q8_0" if settings["lora"] else "f16"
            cache = flags.get("--cache-type-k") or adapted
        if "need_gib" in pick:
            memory = dict(method="measured", need_gib=pick["need_gib"])
        elif family.get("sizing") == "tier":
            memory = dict(method="tier")
        else:
            memory = dict(method="formula", **family)
        item = {key: value for key, value in pick.items() if key != "settings"}
        item.update(
            label=model["label"],
            engine=model["engine"],
            licence_note=model.get("licence_note", ""),
            settings=settings,
            kv=cache,
            extras=pick.get("extras", []),
            resident_bytes=sum(
                file["bytes"]
                for file in model["files"]
                if file["role"] == "weights" and file.get("resident", True)
            ),
            lora_bytes=sum(
                file["bytes"]
                for file in model["files"]
                if file["role"] == "lora" and settings["lora"]
            ),
            memory_model=memory,
        )
        entries.append(item)
    return entries


class ShippedCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = json.loads(CATALOG.read_text(encoding="utf8"))
        cls.models = {model["id"]: model for model in cls.raw["models"]}
        cls.picks = cls.raw["picks"]
        cls.apple = [pick for pick in cls.picks if "metal" in pick["backends"]]
        cls.everything = joined(cls.raw)
        cls.allowed = joined(cls.raw, allow_listed)

    def weights(self, pick):
        files = self.models[pick["model"]]["files"]
        return [file for file in files if file["role"] == "weights"]

    def result(self, path):
        return json.loads((ROOT / path).read_text(encoding="utf8"))

    def advice(self, hardware, entries=None):
        chosen = self.allowed if entries is None else entries
        return recommend.recommend(hardware, chosen)

    def test_tables_join_and_ids_are_unique(self):
        raw = self.raw
        self.assertEqual((raw["schema"], raw["engine_build"]), (1, "b12633"))
        self.assertRegex(raw["updated"], r"^\d{4}-\d{2}-\d{2}$")
        for rows in (raw["models"], self.picks):
            names = [row["id"] for row in rows]
            self.assertEqual(len(names), len(set(names)))
            for name in names:
                self.assertTrue(ID.fullmatch(name), name)
        for model in raw["models"]:
            self.assertIn(model["family"], raw["families"], model["id"])
            self.assertIn(model["engine"], ("llama.cpp", "Strata"), model["id"])
        for pick in self.picks:
            self.assertIn(pick["model"], self.models, pick["id"])
            self.assertIn(pick["status"], ("measured", "suggested"), pick["id"])
            self.assertIn(pick["tool_calling"], ("good", "ok", "weak", "unknown"))
            if self.models[pick["model"]]["engine"] == "Strata":
                self.assertEqual(pick["backends"], ["cuda"], pick["id"])
        for backend in ("cuda", "vulkan", "metal", "cpu"):
            fallback = [
                pick
                for pick in self.picks
                if backend in pick["backends"] and pick["min_memory_gib"] == 0
            ]
            self.assertTrue(fallback, backend)

    def test_files_are_pinned_and_text_fits_the_loader_limits(self):
        for model in self.raw["models"]:
            with self.subTest(model["id"]):
                self.assertTrue(HEX40.fullmatch(model["revision"]))
                self.assertTrue(0 < len(model["label"].strip()) <= 80)
                self.assertTrue(0 < len(model["licence"].strip()) <= 80)
                self.assertLessEqual(len(model.get("licence_note", "")), 400)
                self.assertTrue(self.weights({"model": model["id"]}))
                for file in model["files"]:
                    self.assertTrue(HEX64.fullmatch(file["sha256"]), file["path"])
                    self.assertGreaterEqual(file["bytes"], 1, file["path"])
                    self.assertNotIn("..", file["path"])
                    self.assertEqual("repo" in file, "revision" in file)
                    if "revision" in file:
                        self.assertTrue(HEX40.fullmatch(file["revision"]))
        for pick in self.picks:
            with self.subTest(pick["id"]):
                self.assertTrue(0 < len(pick["why"].strip()) <= 240)
                self.assertTrue(pick["why"].endswith("."))
                settings = pick["settings"]
                self.assertTrue(1024 <= settings["context"] <= 1048576)
                self.assertNotIn("engine", settings)
                self.assertNotIn("model", settings)
                args = settings.get("engine_args", [])
                self.assertTrue(len(args) <= 16 and len(args) % 2 == 0)
                flags = dict(zip(args[0::2], args[1::2]))
                self.assertEqual(len(flags), len(args) // 2)
                self.assertEqual(
                    flags.get("--cache-type-k"), flags.get("--cache-type-v")
                )
                if pick.get("needs_engine_args"):
                    self.assertTrue(args)
                else:
                    self.assertTrue(allow_listed(args), args)

    def test_measured_status_needs_a_result_file_for_that_file(self):
        measured = [pick for pick in self.picks if pick["status"] == "measured"]
        self.assertTrue(measured)
        for pick in measured:
            with self.subTest(pick["id"]):
                self.assertTrue(RESULT.fullmatch(pick["evidence"]))
                self.assertTrue((ROOT / pick["evidence"]).is_file())
                self.assertTrue(0 < len(pick["measured_on"].strip()) <= 120)
                self.assertNotIn("metal", pick["backends"])
        for pick in self.picks:
            if pick["status"] == "suggested":
                self.assertNotIn("measured_on", pick, pick["id"])
                self.assertNotIsInstance(pick.get("evidence"), str, pick["id"])

    def test_apple_picks_state_what_was_run(self):
        self.assertTrue(self.apple)
        tried = 0
        for pick in self.apple:
            with self.subTest(pick["id"]):
                self.assertEqual(pick["status"], "suggested")
                self.assertEqual(pick["backends"], ["metal"])
                tier = pick["min_memory_gib"]
                self.assertIn(tier, (0, *MAC_SIZES))
                self.assertIsNone(SPEED.search(pick["why"]))
                self.assertIsNone(POWER_WORDS.search(pick["why"]))
                evidence = pick.get("evidence")
                if evidence is None:
                    self.assertNotIn("need_gib", pick)
                    self.assertIn("Not run on a Mac", pick["why"])
                    self.assertIn("arithmetic estimate", pick["why"])
                    continue
                tried += 1
                self.assertLessEqual(EVIDENCE_KEYS, set(evidence))
                self.assertLessEqual(set(evidence), EVIDENCE_KEYS | {"file"})
                self.assertEqual(evidence["machine"], MACHINE)
                self.assertEqual(evidence["tool_call"], "passed")
                peak = evidence["peak_memory_gib"]
                self.assertEqual(pick["need_gib"], peak)
                self.assertEqual(peak, round(peak, 1))
                self.assertIn(RAN, pick["why"])
                self.assertIn(f"{peak:.1f} GiB", pick["why"])
                self.assertEqual(len(self.weights(pick)), 1)
                settings = pick["settings"]
                self.assertEqual(evidence["context"], settings["context"])
                self.assertNotIn("--cache-type-k", settings.get("engine_args", []))
                if tier:
                    self.assertGreaterEqual(tier, smallest_mac(peak))
                else:
                    sizes = [self.weights(other)[0]["bytes"] for other in self.apple]
                    self.assertEqual(self.weights(pick)[0]["bytes"], min(sizes))
                    joint = next(e for e in self.everything if e["id"] == pick["id"])
                    self.assertEqual(rate(joint, mac(8))["sizing"]["fit"], "no")
        self.assertEqual(tried, 6)
        self.assertNotIn("power", self.raw["note"])

    def test_apple_evidence_agrees_with_the_metal_result_files(self):
        tried = [pick for pick in self.apple if "evidence" in pick]
        named = [p["evidence"]["file"] for p in tried if "file" in p["evidence"]]
        self.assertEqual(len(named), len(set(named)))
        self.assertEqual(len(named), 5)
        for name in named:
            self.assertTrue(RESULT.fullmatch(name) and ".." not in name, name)
        matched = set()
        for path in sorted((ROOT / "results").glob("metal-*.json")):
            data = json.loads(path.read_text(encoding="utf8"))
            tested, engine = data["model"], data["engine"]
            for pick in tried:
                weights = self.weights(pick)[0]
                if weights["sha256"] != tested["sha256"]:
                    continue
                with self.subTest(path=path.name, pick=pick["id"]):
                    self.assertEqual(
                        (tested["file"], tested["size_bytes"]),
                        (weights["path"].rsplit("/", 1)[-1], weights["bytes"]),
                    )
                    self.assertEqual(engine["backend"], "Metal")
                    self.assertEqual(engine["build"], self.raw["engine_build"])
                    self.assertEqual(engine["context"], pick["settings"]["context"])
                    self.assertTrue(engine["kv"].startswith("f16"))
                    self.assertEqual(data["benchmark"]["parallel"], 1)
                    checks = data["benchmark"]["checks"]
                    tool = next(c for c in checks if c["name"] == "tool-call")
                    self.assertTrue(tool["passed"])
                    recorded = data["memory"]["peak_server_rss_gib"]
                    stated = pick["evidence"]["peak_memory_gib"]
                    self.assertLessEqual(recorded, stated + 0.1)
                    if pick["evidence"].get("file") == "results/" + path.name:
                        self.assertLessEqual(abs(recorded - stated), 0.1)
                        matched.add(pick["evidence"]["file"])
        present = {name for name in named if (ROOT / name).is_file()}
        self.assertEqual(matched, present)

    def test_inkling_metal_pick_reuses_the_listed_file(self):
        pick = next(p for p in self.apple if p["id"] == "metal-32-inkling-q4")
        other = next(p for p in self.picks if p["id"] == "cuda-24-inkling-q4")
        self.assertEqual(pick["model"], other["model"])
        self.assertEqual(
            [file["path"] for file in self.models[pick["model"]]["files"]],
            ["Mainline_Support/Inkling-2-Mini-UD-Q4_K_XL.gguf_file"],
        )
        self.assertEqual(pick["evidence"]["peak_memory_gib"], 20.5)
        self.assertEqual(pick["min_memory_gib"], smallest_mac(20.5))
        self.assertEqual((pick["min_memory_gib"], pick["rank"]), (32, 2))
        self.assertEqual(pick["tool_calling"], other["tool_calling"])
        self.assertIn("open Inkling tool-call defects", pick["why"])
        self.assertTrue(allow_listed(pick["settings"]["engine_args"]))

    def test_apple_matrix(self):
        nine, iq3 = "qwen3.5-9b-q4_k_m", "qwen3.8-27b-ud-iq3_xxs"
        q4, q6 = "qwen3.8-27b-ud-q4_k_xl", "qwen3.8-27b-ud-q6_k_xl"
        q8, inkling = "qwen3.8-27b-q8_0", "inkling-2-mini-ud-q4_k_xl"

        def others(ram, working_set=None):
            found = self.advice(mac(ram, working_set))["alternatives"]
            return [(item["model"], item["sizing"]["fit"]) for item in found]

        expected = {
            16: (nine, "tight"),
            18: (nine, "tight"),
            24: (nine, "comfortable"),
            32: (iq3, "comfortable"),
            36: (q4, "tight"),
            48: (q4, "comfortable"),
            64: (q8, "comfortable"),
            96: (q8, "comfortable"),
            128: (q8, "comfortable"),
        }
        for ram, (model, fit) in expected.items():
            with self.subTest(ram):
                result = self.advice(mac(ram, 37.44 if ram == 48 else None))
                pick = result["pick"]
                self.assertEqual(
                    (pick["model"], pick["sizing"]["fit"]), (model, fit)
                )
                self.assertEqual(pick["status"], "suggested")
                for item in [pick, *result["alternatives"]]:
                    peak = item["sizing"]["need_gib"]
                    if item["sizing"]["method"] == "measured":
                        self.assertLessEqual(peak + spare(ram), ram, item["id"])
        for ram in (36, 48):
            pick = self.advice(mac(ram))["pick"]
            self.assertEqual(
                (pick["settings"]["context"], pick["kv"]), (32768, "f16")
            )
            self.assertEqual(pick["sizing"]["need_gib"], 24.1)
            self.assertIn(inkling, dict(others(ram)))
        for ram in MAC_SIZES:
            self.assertNotEqual(self.advice(mac(ram))["pick"]["model"], inkling, ram)
        self.assertNotIn(inkling, dict(others(32)))
        self.assertEqual(others(32, 22.5)[0], (inkling, "tight"))
        roomy = self.advice(mac(64))
        self.assertIn(q6, [item["model"] for item in roomy["alternatives"]])
        starter = self.advice(mac(8))
        self.assertEqual(starter["pick"]["model"], "qwen3.5-4b-q4_k_m")
        self.assertEqual(starter["pick"]["sizing"]["fit"], "try")
        self.assertEqual(starter["alternatives"], [])
        largest = self.advice(mac(96), self.everything)["pick"]
        self.assertEqual(largest["model"], "qwen3.8-flash-next-gsq-rco-iq3_s")
        self.assertTrue(self.advice(mac(96), self.everything)["warnings"])

    def test_apple_reasons_state_peak_and_never_a_speed(self):
        for ram in MAC_SIZES:
            with self.subTest(ram):
                result = self.advice(mac(ram, 37.44 if ram == 48 else None))
                text, pick = result["reason"], result["pick"]
                self.assertIn(f"{pick['sizing']['need_gib']:.1f} GiB", text)
                self.assertNotIn(";", text)
                self.assertIsNone(SPEED.search(text))
                self.assertIsNone(POWER_WORDS.search(text))
                if isinstance(pick.get("evidence"), dict):
                    self.assertIn("its recorded peak is", text)
                    self.assertTrue(text.endswith(TRIED))
                else:
                    self.assertIn("it needs an estimated", text)
                    self.assertTrue(text.endswith(SUGGESTED))

    def test_other_backends_get_a_fitting_pick(self):
        sizes = (6.0, 8.0, 11.99, 15.99, 23.99, 31.84, 48.0, 96.0)
        for backend in ("cuda", "vulkan"):
            for memory in sizes:
                with self.subTest(backend=backend, memory=memory):
                    result = self.advice(gpu(memory, backend))
                    fit = result["pick"]["sizing"]["fit"]
                    self.assertIn(fit, ("comfortable", "listed"))
                    self.assertIn(backend, result["pick"]["backends"])
                    self.assertNotIn("flash-next", " ".join(ids(result)))
                    self.assertEqual(
                        "measured on" in result["reason"],
                        result["pick"]["status"] == "measured",
                    )
        def model(hardware):
            return self.advice(hardware)["pick"]["model"]

        self.assertEqual(model(gpu(23.99)), "qwen3.8-27b-ud-q4_k_xl")
        self.assertEqual(model(gpu(31.84)), "inkling-2-mini-sq-20")
        engines = ["Auto", "llama.cpp", "Custom"]
        without_strata = recommend.recommend(gpu(31.84), self.allowed, engines)
        self.assertEqual(without_strata["pick"]["id"], "cuda-32-qwen3.8-pi")
        self.assertIsNone(self.advice(processor(4))["pick"])
        self.assertEqual(model(processor(8)), "qwen3.5-4b-q4_k_m")
        self.assertEqual(model(processor(32)), "qwen3.5-9b-q4_k_m")
        linux_box = self.result("results/amd-vulkan-iq3.json")["ram_gib"]
        self.assertLess(linux_box, 31.5)
        self.assertEqual(model(processor(linux_box)), "qwen3.5-9b-q4_k_m")
        self.assertEqual(model(processor(24)), "qwen3.5-4b-q4_k_m")
        unread = dict(backend="cuda", ram_gib=32.0, memory_gib=None)
        self.assertEqual(self.advice(unread)["pick"]["sizing"]["fit"], "unknown")
        licensed = recommend.recommend(gpu(64.0), self.everything)
        self.assertEqual(
            licensed["pick"]["model"], "qwen3.8-flash-next-gsq-rco-iq3_s"
        )
        self.assertTrue(
            any("Qwen Community License" in line for line in licensed["warnings"])
        )


if __name__ == "__main__":
    unittest.main()
