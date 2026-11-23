# Reference

This page lists every command-line verb and flag, every environment variable Agent Workbench reads, the engines, the files you can read and commit, the ports, how to repeat the measurements, and the upstream projects.

## Command line

How you spell a command depends on how you installed:

| Install | Command |
| --- | --- |
| Install script, any system | `workbench <verb> [flags]`. `workbench` alone opens the app; `workbench <script>.py` runs one of Workbench's own scripts, such as `setup_engines.py`, with its Python |
| Kit or checkout on Linux or macOS | `sh workbench.sh cli.py <verb> [flags]` in the `Agent-Workbench` folder |
| Kit or checkout on Windows | `Run.cmd cli.py <verb> [flags]` in the `Agent-Workbench` folder |

The command uses the settings you saved in the app: the model source, model, agent and project folder.

| Verb | What it does |
| --- | --- |
| `serve` | Starts the local model and prints its address, `http://127.0.0.1:10532/v1`. Ctrl+C while it loads stops it with "Stopped before the model finished loading." |
| `stop` | Stops the local model |
| `status` | Prints the model that is serving and its settings, or `Stopped` |
| `agent` | Starts the agent in a new terminal window. With `--here`, or when no window can open, it runs in this terminal |
| `sessions` | Lists running sessions. With `--workspace`, also the recent sessions of that folder with the command that resumes each |
| `trust` | Prints `Project folder: <path>`, lists the folder's files that make an agent start programs, and asks whether to allow them. Exits with 0 when they are allowed or nothing needs review, otherwise 1 |
| `doctor` | Runs the Doctor checks ([Troubleshooting](troubleshooting.md#start-with-doctor)). Exits with 1 when a check failed |
| `bench` | Starts the local model and runs the short benchmark, writing `benchmark-<date>-<time>.json` in the data folder and printing its path |
| `web-start`, `web-stop` | Starts or stops the page-crawling stack (Docker Compose, or inside the `AgentWorkbench-Web` WSL distribution on Windows without Docker) |

| Flag | Used with | Meaning |
| --- | --- | --- |
| `--workspace <folder>` | `agent`, `sessions`, `trust`, and the others that use a folder | The project folder; a relative path is taken from where you typed the command. The folder must exist |
| `--here` | `agent` | Run the agent in this terminal instead of opening a window |
| `--json` | `sessions`, `doctor` | Print JSON |
| `--setup-id <id>` | `serve`, `agent`, `bench`, `trust` | Use a named setup instead of the current settings |

`doctor` also takes check names and `--active`: `workbench doctor gpu ports --json`, or `workbench doctor automation --active` on a Mac.

The setup ID comes from **Create launcher** in **Advanced → Model library**. It is derived from the name of a saved setup, and the launcher file Workbench writes into the `launchers` folder of the data folder runs `cli.py agent --here --setup-id <id>`. When a launch through such a launcher stops at the consent step, the message ends with the command to use, for example `workbench trust --setup-id <id> --workspace "<folder>"`. When the named setup no longer exists, the command stops with "Saved setup was removed." When no model is chosen, `serve`, `bench` and `agent` with the local model stop with "Choose a model in Agent Workbench first."

Engine setup has its own script:

```sh
workbench setup_engines.py [--update] [--backend auto|cuda|vulkan|cpu|metal] [--no-strata]
```

`--update` installs the newest release beside the tested one, `--backend` chooses the build, and `--no-strata` skips Strata. In a Windows kit, `Setup engines.cmd` runs the same script.

## Environment variables

Set these before you start Workbench. None is needed for normal use.

| Variable | Effect | Default |
| --- | --- | --- |
| `WORKBENCH_DATA` | The data folder | `.local` in the app folder; the install scripts' `workbench` command sets the per-user folder ([Security and your data](security-and-data.md#where-workbench-keeps-data)) |
| `WORKBENCH_PORT` | The port of the local model server | `10532` |
| `WORKBENCH_KEY_<ID>` | The API key of the connection with that ID, instead of a saved key. The connection's **Key** line names the exact variable | Not set |
| `WORKBENCH_WEB_PRIVATE` | `1` lets page crawling fetch addresses on this computer and private networks | Not set: they are refused |
| `WORKBENCH_RUNTIME` | A folder to load engines from instead of `runtime` in the data folder | Not set |
| `WORKBENCH_VLLM` | The `vllm` program to run | The environment `setup-vllm.sh` made, then `vllm` on `PATH` |
| `WORKBENCH_VLLM_VENV` | Where `setup-vllm.sh` creates the vLLM environment | `~/.local/share/agent-workbench/vllm` |
| `WORKBENCH_FLASH_CONFIG` | The Strata Flash Next launch preset that the Flash preset engine starts | `Strata/launch/flash-q2-32k-dflash2.json` under `%LOCALAPPDATA%` on Windows or `~/.local/share` elsewhere |
| `WORKBENCH_API`, `WORKBENCH_API_KEY` | The server address and key that `benchmark.py` and `acceptance.py` measure. A Custom engine also receives the local key as `WORKBENCH_API_KEY` | The local model on `127.0.0.1` |
| `WORKBENCH_BOOTSTRAP_PYTHON` | The Python 3.12, 3.13 or 3.14 that `workbench.sh` or `Run.cmd` uses to build its environment when the folder has no bundled Python | On Linux and macOS the first suitable `python3.12`, `python3.13`, `python3.14`, `python3` or `python` on `PATH`; on Windows `python`, then `py -3.12`, `py -3.13` and `py -3.14` |
| `WORKBENCH_VENV` | The folder of the Python environment `workbench.sh` builds | `.venv-linux` in the app folder |
| `WORKBENCH_PYTHON` | The Python the desktop window starts its backend with. The launcher sets it | The launcher's Python |
| `WORKBENCH_SANDBOX_CHECKED` | Set by `setup-linux.sh` so the launcher does not check the Linux desktop sandbox twice | Not set |
| `WORKBENCH_PROC_ROOT` | Where `setup-linux.sh` reads kernel settings, for its tests | `/proc` |
| `WORKBENCH_JOB_ID` | Set by Workbench to tag a vLLM run, so it can stop it again | Set per run |
| `WORKBENCH_INSTALL_DIR` | Where the install script puts the app (on Windows, the folder that holds `app`, `bin` and `data`) | `~/.local/lib/agent-workbench`; on Windows `%LOCALAPPDATA%\Agent Workbench` |
| `WORKBENCH_BIN_DIR` | Where `install.sh` writes the `workbench` command | `~/.local/bin` |
| `WORKBENCH_INSTALL_FROM` | A folder that holds the release files, so the install script downloads nothing (the same as `--from` or `-From`) | Not set |
| `WORKBENCH_INSTALL_TARGET` | The kit `install.sh` installs: `macos-arm64` or `linux-x64` | Chosen from this computer |
| `WORKBENCH_NO_MODIFY_PATH` | Any value stops the install scripts from changing your `PATH` or shell profile | Not set |
| `WORKBENCH_NO_SHORTCUT` | Any value stops `install.ps1` from creating the Start menu entry | Not set |

Agents that Workbench starts receive their key as `WORKBENCH_SESSION_KEY` or in the variable the agent expects; you do not set it. The provider variables such as `OPENAI_API_KEY` are listed in [Model sources](model-sources.md#hosted-providers).

## Engines

The **Engine** setting in **Advanced → Model library**:

| Engine | Systems | What Setup installs | Release channels |
| --- | --- | --- | --- |
| Auto | All | Nothing of its own. It picks Strata for a `.strata` file, Flash preset for a Flash Next file, and llama.cpp otherwise | As for the engine it picks |
| llama.cpp | Windows x64, Linux x64, macOS on Apple Silicon | `llama-server` build b12633 for CUDA, Vulkan, Metal or CPU | Tested (pinned SHA-256) or Updated (the newest release, checked against the digest GitHub reports) |
| Strata | Windows and Linux with an NVIDIA GPU | `strata-inkling` 0.1.65, from its release archive and source at a pinned commit; needs Git | Tested or Updated |
| vLLM | Linux, or Windows through WSL, with an NVIDIA GPU | vLLM 0.34.0 in its own Python environment (**vLLM** in **Advanced → Setup**). Its version is pinned; its files and dependencies are not digest-checked | Version pin |
| Flash preset | Windows and Linux | Nothing. It starts an existing Strata Flash Next launch preset from the reference workstation (`WORKBENCH_FLASH_CONFIG`) | — |
| Custom | All | Nothing. It starts the program your manifest names | — |

A Custom engine is a JSON file with an `argv` list. Choose it under **Custom engine manifest** in **More engine settings**:

```json
{"argv": ["llama-server", "-m", "{model}", "--ctx-size", "{context}", "--alias", "{alias}", "--host", "{host}", "--port", "{port}", "--api-key-file", "{keyfile}"]}
```

Workbench replaces `{model}` with the model file, `{context}` with the context size, `{alias}` with `local-agent`, `{host}` with `127.0.0.1`, `{port}` with the local model port and `{keyfile}` with the path of `api-key.txt`, starts the program, and waits until `GET /v1/models` lists `local-agent`. The engine gets the loopback address and the key only when your manifest uses `{host}` and `{keyfile}`. On Windows, name the engine's `.exe`, not a `.cmd` or `.bat` wrapper.

The **Matched Qwen LoRA** toggle loads the LoRA adapter for Pi that comes with the Qwen3.8-27B UD-Q4_K_XL catalog entry. It runs on llama.cpp; there is no separate engine for it. For 32 GB NVIDIA cards the catalog lists the 128K profile with the adapter as the second choice, for long inputs and for Pi ([Model sources](model-sources.md#recommended-models)).

## Files you can read and commit

Workbench keeps these files in plain JSON. You can read them without Workbench, and none of them holds a key. Paths marked "data folder" are inside the data folder ([Security and your data](security-and-data.md#where-workbench-keeps-data)).

| File | Where | What it holds |
| --- | --- | --- |
| `.agent-workbench/profiles.json` | The project folder | Named profiles: an object from each profile name to its settings ([Project setup](project-setup.md#what-is-remembered)). New in the next release: one profile can carry `"default": true`, which makes it the folder default ([Settings that travel with the folder](project-setup.md#settings-that-travel-with-the-folder)). Safe to commit |
| `.agent-workbench/project.json` | The project folder | The project's MCP servers, with variable names instead of values ([MCP servers](project-setup.md#mcp-servers)). Safe to commit |
| `runs/<folder hash>.json` | The data folder | New in the next release: the run records of one project folder, newest first, up to 100 ([Run summaries](sessions.md#run-summaries)). It stays on this computer |
| `projects.json` | The data folder | The last choices for each folder and your consent answers. It stays on this computer |

## Ports

| Port | What | Where |
| --- | --- | --- |
| 10532 | The local model server (`WORKBENCH_PORT`) | `127.0.0.1` |
| 3002 | The page-crawling stack | `127.0.0.1` |
| 9876 | The Blender MCP add-on, which the Blender tool talks to | `127.0.0.1` |
| 10542 | The Workbench-managed model on an SSH machine | That machine's `127.0.0.1`, reached through a tunnel |
| A free port | The local end of each SSH tunnel | `127.0.0.1` |
| A free port | The SSH port of each Docker sandbox container | `127.0.0.1` |
| 11434, 1234, 9931, 8080, 8000, 1337, 5001, 5000, 13305, 30000, 10240, 9292 | Ports the scan looks at for model servers | `127.0.0.1`, or a paired machine's own loopback |
| 41343, 52993, 16141, 39414, 22931 | LM Studio's control ports, checked to tell you when its server is off | `127.0.0.1` |

## Reproducing the measurements

[MEASUREMENTS.md](../MEASUREMENTS.md) describes what was measured, on which hardware, and with which files. To run the same checks against your own local model, start Workbench once, choose a model, then from the `Agent-Workbench` folder of a kit or a checkout:

On Linux and macOS:

```sh
sh workbench.sh cli.py status
sh workbench.sh cli.py bench
WORKBENCH_API_KEY="$(cat .local/api-key.txt)" sh workbench.sh acceptance.py .local/acceptance.json
WORKBENCH_API_KEY="$(cat .local/api-key.txt)" sh workbench.sh omp_check.py .local/omp-check.json
sh workbench.sh -m unittest discover -s tests
```

On Windows, in PowerShell:

```powershell
.\Run.cmd cli.py status
.\Run.cmd cli.py bench
$env:WORKBENCH_API_KEY = (Get-Content -Raw .local/api-key.txt).Trim()
.\Run.cmd acceptance.py .local/acceptance.json
.\Run.cmd omp_check.py .local/omp-check.json
.\Run.cmd -m unittest discover -s tests
```

With a script install, the key is in `api-key.txt` in your data folder instead of `.local`, and `workbench` takes the place of `sh workbench.sh` and `.\Run.cmd`. `benchmark.py` and `acceptance.py` refuse an address with a user name or password in `WORKBENCH_API`, ignore proxy settings for a loopback address and do not follow redirects. Their exact-answer checks accept an answer inside one Markdown code fence, and do not accept `true` or `false` in place of `1` or `0`.

Results depend on the hardware, the driver, the context size, the reasoning setting and the engine build, so compare runs only on the same setup.

## Upstream projects and licences

Agent Workbench is MIT-licensed ([LICENSE](../LICENSE)). The programs it installs or starts keep their own licences:

- Agents: [OMP](https://github.com/can1357/oh-my-pi), [Pi](https://github.com/earendil-works/pi), [Claude Code](https://code.claude.com/docs/en/setup), [Codex](https://github.com/openai/codex), [Hermes Agent](https://github.com/NousResearch/hermes-agent), [opencode](https://github.com/anomalyco/opencode).
- Engines: [llama.cpp](https://github.com/ggml-org/llama.cpp), [Strata](https://github.com/Niko1221/Strata), [vLLM](https://github.com/vllm-project/vllm).
- Tools: [Firecrawl](https://github.com/firecrawl/firecrawl) (the adapted Compose file is under AGPL-3.0, see [LICENSE-Firecrawl](../LICENSE-Firecrawl)), [DDGS](https://github.com/deedy5/ddgs) for web search, [Windows-MCP](https://github.com/CursorTouch/Windows-MCP), and the Blender MCP server and add-on.
- The desktop window: [Electron](https://github.com/electron/electron) 45.4.1, downloaded on first start, and xterm.js with its fit addon, bundled with their MIT notices in `desktop/vendor/xterm`.
- Models keep the licences listed in [Model sources](model-sources.md#recommended-models); starter-kit MCP servers keep the licences listed in [Project setup](project-setup.md#kit-mcp-servers).

Product names belong to their owners. Agent Workbench is not affiliated with or endorsed by them.
