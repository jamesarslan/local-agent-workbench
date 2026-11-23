# Agent Workbench measurements

The figures below were committed to this repository on November 22, 2026. They come from three sources, and each section names its own hardware: one Windows workstation with an RTX 5090 (every section from "RTX 5090 profile selection" onward), a separate Linux machine with a Radeon RX 9070 XT, and a hosted model run under Terminal-Bench. The workstation has a Ryzen 9 9950X3D, 32 GB RTX 5090, 64 GB installed RAM and NVIDIA driver 621.24. Its Linux checks used Debian under WSL2 on the same GPU. The result files behind these figures carry no timestamps, except the two upstream-runner archive files, which record November 17, 2026. No figure here was measured on macOS or Apple Metal. The five `metal-m3-max-*.json` files in `results/` are dated November 22, 2026, and come from a 48 GB Apple M3 Max on battery power. Each wraps a report from an earlier revision of `benchmark.py` in a record of the machine, engine and peak memory that has no generating script in this repository. The model catalog cites them as evidence that a model loaded at 32,768 context, of its peak memory and of a passed tool call. Their speeds are not quoted here, because each file says to rerun on mains power first. CPU, driver, desktop VRAM use, prompts, reasoning effort, context length and runtime affect results.

## Linux AMD test bench

Hardware: a separate Linux machine with a Radeon RX 9070 XT, 15.9 GiB available device capacity and 30.9 GiB system RAM, running llama.cpp b12633 with Vulkan.

Inkling 2 Mini Mainline IQ3_XXS ran at 8,192 context. The 256-output-token code probe decoded at **44.6 tokens/s** and sampled **12.15 GiB** of device-wide VRAM use. The shorter exact-answer probe decoded at 35.3 tokens/s. A real remote OMP session returned the expected word and a Windows client reached the model through the SSH forward. These figures include reasoning tokens. This run measures the 16 GB GPU case. The record has no generating script in this repository. Its 256-token probe is stored as four summary fields without per-request timings, and no report from the benchmark suite used for the RTX 5090 table exists for this machine. [Recorded timings](results/amd-vulkan-iq3.json)

## Terminal-Bench 2.1

These two runs drive OMP against a hosted model inside Harbor task containers. Agent Workbench is not in the loop.

| DeepSeek V4.1 Flash 1017, OMP 18.12.5 | Passed | Errored trials | Elapsed | Reported model spend | Spend per attempted task |
| --- | ---: | ---: | ---: | ---: | ---: |
| High reasoning | 71/89 (79.8%) | 6 | 4h 45m | $5.85 | $0.066 |
| Max reasoning | 78/89 (87.6%) | 4 | 5h 36m | $9.21 | $0.103 |

Aggregated from two Harbor jobs. The job directories and per-trial results are not included in this repository, so the table cannot be recomputed from published data. The aggregate file holds two summary rows and no per-task outcomes. Each job ran 89 distinct tasks with one attempt each and concurrency three. Harbor's cost field is empty in both jobs. Spend comes from the contemporaneous run summaries and covers model usage. It is author-reported and cannot be regenerated from the published data. Both runs disabled skills and extensions. One run at each setting leaves sampling variability unresolved. An independent Opus run and its pricing would be needed for a controlled cost comparison.

The Harbor version used for the two jobs was not recorded. Before the changes described next, the adapter imported successfully with Harbor 0.24.0 on the bench. The adapter has changed since these runs. Its token summary now counts each finished assistant message once. It previously summed every transcript event that carried usage, which, going by the OMP 18.12.5 source, counts a finished message at least twice. It also pins Bun 1.4.3, where it previously installed whichever Bun release was current. No figure in the table derives from the adapter's token counts. The changed adapter has not been re-run under Harbor. Its usage example runs one smoke task. To repeat the full suite, remove `-l 1`, use `-n 3 -k 1` and select `--ak thinking=max` or `--ak thinking=high`. [Aggregate data](results/harbor-terminal-bench-21.json) · [Harbor adapter](evals/harbor/omp_agent.py)

## RTX 5090 profile selection

**Inkling SQ-20 with DSpark at 32K is the daily agent preset.** It fits the native weights on the GPU, supports the matching vision companion and completed real OMP tool loops. Use **64K FP8** for longer inputs. Keep **Flash Next DFlash2** for this workstation's fastest measured short-prompt decoding. It needs the existing expert pack and uses substantial system RAM.

| Profile | Configured context | Median generated tokens/s | Five short checks, 96-token limit | Five checks with room for reasoning¹ |
| --- | ---: | ---: | ---: | ---: |
| Inkling SQ-20, Strata, DSpark | 32,768 | [157.6](results/sq20.json) | 2/5 | [5/5](results/sq20-32k-acceptance.json) |
| Inkling SQ-20, Strata, DSpark off | 32,768 | [120.6](results/sq20-off.json) | 2/5 | — |
| Inkling SQ-20, Strata, DSpark | 131,072 | [158.0](results/sq20-128k.json) | 2/5 | 5/5² |
| Inkling Q4 mainline, llama.cpp | 32,768³ | [85.2](results/inkling-q4-mainline.json) | 2/5 | [5/5](results/inkling-q4-mainline-acceptance.json) |
| Inkling Q8 mainline, llama.cpp | 32,768 | [79.4](results/inkling-q8.json) | 2/5 | [5/5](results/inkling-q8-acceptance.json) |
| Qwen3.8-27B Q4, Pi LoRA and MTP | 131,072 | [142.3](results/qwen-pi.json) | 3/5 | [3/5](results/qwen-pi-acceptance.json) |
| Qwen3.8-Flash-Next Q2, DFlash2 | 32,768³ | [264.0](results/flash-dflash2.json) | 4/5 | [5/5](results/flash-dflash2-acceptance.json) |

Each rate is the report's `median_decode_tps`, rounded half up to one decimal.

¹ Four exact-answer tasks and a two-turn tool exchange with a 1,024-token completion allowance per turn. The short suite instead grades a single returned function call as its fifth task. These are small integration checks. They cannot establish general coding or reasoning parity. The acceptance reports store no model or context, so each is matched to its row by file name. Five of the six (`sq20`, `inkling-q4-mainline`, `inkling-q8`, `qwen-pi` and `flash-dflash2`) were written by an earlier revision of `acceptance.py` that kept the first turn's `finish` value, `tool_calls`, for the tool exchange. The shipped script records the second turn's value, `stop`, as in the 32K SQ-20 report. Pass and fail are unaffected. The Flash short-suite miss was a wrong answer, not a truncated one: `logic` returned `[2, 9, 4]` after 10 of its 96 tokens. The same prompt returned `[2, 9, 4, 7]` in the acceptance run and in the four archived six-request runs.

² No result file identifies itself as this run. `results/sq20-acceptance.json` is the only SQ-20 acceptance report not assigned to another row. It stores no context, and its token counts are identical to the 32,768 report, so this cell cannot be verified from the published data.

³ The report does not store the context. 32,768 is the launch setting. For the other five rows the context is part of the report's `condition`.

The decode benchmark uses two passes over three coding prompts, one request at a time, temperature zero and a 192-token cap. Reported throughput includes reasoning and speculative decoding. All 30 short coding responses in the five Inkling rows, Strata and llama.cpp alike, exhausted their budget before emitting visible code. Their rates measure generated tokens, not completed programs. The longer-input fixture tokenized to 13,773 Inkling tokens and 16,347 Qwen tokens. Native engine timing fields are retained in the raw reports, and every rate in the table is the engine-reported one. First-token times are not comparable across engines. In these reports llama.cpp streamed reasoning as `reasoning_content`, so a time is present for its reasoning-only responses and can mark the first reasoning token. Strata streamed nothing the script counted before visible text or a tool call, so its reasoning-only responses have no first-token time.

The original GGUF Q4 targeted Unsloth's own runtime. Strata 0.1.65 recognized its architecture with `--info`, then rejected its magic during CUDA inference. The two GGUF numbers above are for the working Mainline_Support files using llama.cpp b12633. Strata results use release v0.2.19, engine 0.1.65 and its native 19.35 GB SQ-20 pack with the embedded draft model. The Qwen Pi comparison uses the existing b12483 runtime. The independent Flash configuration uses its pinned Strata installation. Only the Q8 report names its engine build, in `condition`. The other builds are the ones the launcher pins.

## Context and vision

| SQ-20 setting | Actual test input | Result | End-to-end time | Peak sampled device use |
| --- | ---: | --- | ---: | ---: |
| 65,536 context, FP8 KV | 49,644 tokens | [Exact retrieval pass](results/sq20-long-64k.json) | 15.64 s | 27.32 GiB |
| 131,072 context, FP8 KV | 109,044 tokens | [Recovered the embedded value, leaked reasoning into output and failed exact match](results/sq20-long-fp8.json) | 141.45 s | 31.75 GiB |

A third attempt, at 131,072 context with automatic KV and the same input, has no result file. The client gave up after 300 s during prefill with device memory near capacity. The shipped `capacity.py` waits up to 900 s and writes a report only when the request completes. The context and KV mode of the two recorded runs are launch settings. The result files do not store them.

The 64K test processed the input at 3,398 tokens/s. The 128K FP8 run processed it at 778 tokens/s. The larger setting is available as an experiment. Neither run validates the model's published maximum context. The vision companion correctly returned “Red” for a generated solid-red image in 1.49 seconds. [Vision record](results/inkling-capacity.json)

## OMP, tools and interoperability

- Four OMP sessions edited a requested JSON field, preserved the other fields, reread the file and returned a random marker. The stock prompt and the supplemented prompt each passed twice. End-to-end times ranged from 3.38 to 4.01 seconds. This fixture establishes out-of-the-box compatibility. It did not establish a prompt-quality advantage. [OMP comparison](results/omp-inkling.json)
- The Qwen adapter remained paired with Qwen. Inkling passed the real edit and two-turn tool checks on its own weights. No cloud GPU was rented and no Inkling LoRA was trained.
- OMP invoked its mounted Firecrawl command through the bash tool and received Markdown from the local API. [OMP MCP result](results/omp-web.json) In a separate OMP session the search MCP tool and the Firecrawl MCP scrape tool were each called once and both calls ended without an error flag. [OMP trace summary](results/omp-direct-web.json) Two further checks were observed and have no result file: Blender MCP connected to an installed Blender 4.5 scene, and Windows-MCP returned a screenshot. Personal screenshots and scene contents are excluded.
- Pi, Claude Code and Codex each returned the expected text from local Strata. A non-root OMP Docker session returned the expected text through its reverse tunnel with only a Docker volume mounted. [Client checks](results/clients.json) · [Docker check](results/docker-workspace.json)
- Linux Strata and Linux llama.cpp generated responses on the same GPU inside WSL2. A separate vLLM environment served a small Qwen checkpoint to the Windows client. This vLLM test verifies backend integration using a small supported checkpoint. The result file records only each reply and its token usage. The vLLM version (0.34.0) and checkpoint (Qwen2.5-0.5B-Instruct) are from the author's notes, and `setup-vllm.sh` does not pin a vLLM version. [Engine checks](results/engines.json)

## Single-request speed and tool load

Three fresh single-stream requests asked for a long Python tutorial on the 128K SQ-20 FP8 + DSpark setup. The file records context 131,072 and drafting on. It does not store the KV mode. Each request hit the 1,024-token limit during reasoning, leaving zero visible answer characters. Wall-clock rates were 109.8, 116.8 and 118.8 tokens/s. Engine decode rates were 110.7, 117.7 and 119.9 tokens/s. The earlier short-prompt 32K result was 157.6 tokens/s. Wall time includes prompt processing and response delivery. Upstream lists solo output, concurrent aggregate output and prompt processing separately. A post needs the same metric and workload for a direct comparison. [Raw trials](results/sq20-single-1024.json) · [Upstream benchmark definitions](https://huggingface.co/strata-engine/Inkling-2-Mini-Strata/blob/main/README.md)

The launcher exposed search and Firecrawl directly in a real OMP session opened in a new folder. Both tool calls succeeded. With the same one-line user message, listing and submitting the enabled MCP schemas to the running Inkling server added **149 input tokens** for search alone, **396** for Firecrawl alone, and **538** together. The combined figure shares template overhead. This measures schema input only. A scraped page adds more context after a tool call. [OMP trace summary](results/omp-direct-web.json) · [Live plugin costs](results/web-plugin-cost.json)

| Synthetic tools available | Mean added prompt tokens | Correct calls | Median request time |
| ---: | ---: | ---: | ---: |
| 1 | 101 | 3/3 | 0.72 s |
| 9 | 909 | 3/3 | 0.86 s |
| 25 | 2,525 | 3/3 | 1.88 s |
| 49 | 4,949 | 3/3 | 2.06 s |
| 97 | 9,797 | 3/3 | 4.11 s |

Each trial required the correct tool name and exact identifier. The targets occupied the start, middle or end of the schema list. These short, explicit tasks on one 128K model show a latency and context cost. They did not identify a tool count at which real-world agent accuracy declines. Run the same benchmark on a different local model in **Plugins**, or use `tool_load.py <output.json>`. [Per-request data](results/tool-load.json)

## Archive and reproducibility

The [archive](results/archive/) contains the earlier Flash Next reserve, stock and pinning trials, per-request upstream results, model checksums and the older Qwen launch measurements. The earlier six-request Flash runs reached 285.7 tokens/s with DFlash2 and 140.0 with drafting off, and both passed 5/5 short checks at temperature zero. [DFlash2 report](results/archive/reserve-dflash2.json) · [Drafting-off report](results/archive/reserve-off.json) The instruction, retrieval and tool scores of 12/12 with DFlash2 and 11/12 with drafting off come from a different run: the upstream runner on November 17, 2026, with engine 0.1.60, temperature 1.0 and reasoning effort xhigh. The runner marked both of those runs `nonconforming`. Their short-case decode medians were 219.2 and 140.0 tokens/s. [Upstream DFlash2 run](results/archive/standard-dflash2.json) · [Upstream drafting-off run](results/archive/standard-off.json) The 264.0 result in the profile table is a separate, later run. The archive omits machine-specific paths and the upstream test launcher command.

Current per-run CSV-equivalent fields, completions, token counts and timings are available as [JSON reports](results/). [Weight sizes and SHA-256](results/weights.json) identify the local Inkling and Qwen Pi assets. [Flash shard checksums](results/archive/model-checksums.json) identify that split model.

Scripts in the source tree wrote these reports: `benchmark.py` wrote `sq20.json`, `sq20-off.json`, `sq20-128k.json`, `inkling-q4-mainline.json`, `inkling-q8.json`, `qwen-pi.json` and `flash-dflash2.json`, and earlier revisions of it wrote the archived `reserve-dflash2.json`, `reserve-dflash2-pin.json`, `reserve-off.json` and `stock-off.json`. `acceptance.py` wrote the six `*-acceptance.json` reports. `omp_check.py` wrote `omp-inkling.json`. `capacity.py` wrote `sq20-long-64k.json` and `sq20-long-fp8.json`. `tool_load.py` wrote `tool-load.json`, and the launcher's plugin measurement in `plugin_cost.py` produced `web-plugin-cost.json`. The archived `standard-dflash2.json` and `standard-off.json` are output of the upstream Strata runner, which is not included.

These records have no generating script in the tree: `amd-vulkan-iq3.json`, `windows-ssh-machines.json` (SSH machine checks, no speed figures), `clients.json`, `docker-workspace.json`, `engines.json`, `harbor-terminal-bench-21.json`, `inkling-capacity.json`, `omp-direct-web.json`, `omp-web.json`, `sq20-single-1024.json`, `weights.json`, and the archived `model-checksums.json` and `qwen-windows.json`.

The committed benchmark reports predate three keys the current `benchmark.py` adds: `decode_source` on each request, and `incomplete` and `meta` on the report. On a server whose context is smaller than a prefill fixture, the script now records that case as skipped and still writes the report. The exact-answer checks in `benchmark.py` and `acceptance.py` now also accept a correct answer wrapped in one Markdown code fence, and no longer accept `true` or `false` in place of `1` or `0`. Regrading the benchmark and acceptance reports cited in this document with the current check reproduces every stored pass and fail. The five Metal files are not among them: an earlier check graded them, and two store a fail for a correct answer inside a code fence.

[Inkling model card](https://huggingface.co/thinkingmachines/Inkling-2-Mini) · [Strata pack card](https://huggingface.co/strata-engine/Inkling-2-Mini-Strata) · [Unsloth GGUFs](https://huggingface.co/unsloth/Inkling-2-Mini-GGUF) · [Strata release](https://github.com/Niko1221/Strata/releases/tag/v0.2.19)
