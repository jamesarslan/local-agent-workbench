# Agent Workbench

Agent Workbench is a desktop app that lets you pick where a model runs (on this computer, on a GPU machine over SSH, in an Ollama or LM Studio server you run, or at a hosted API) and which coding-agent CLI to use for a project folder, and keeps that choice with the folder's instructions, skills and MCP tools.

Version 0.5.0 · MIT licence · Windows x64, Linux x64, macOS arm64 (preview) · [Website](https://jamesarslan.github.io/local-agent-workbench/) · [Changelog](CHANGELOG.md)

## Is this for you?

Workbench fits when you want a model and agent (OMP, Pi, Claude Code, Codex, Hermes or opencode) per project folder, a model on another machine over plain SSH, or a preview of what an agent will read. Another tool fits better when you want:

- one agent on a model Ollama serves: [`ollama launch`](https://docs.ollama.com/cli) starts it in one command;
- providers, MCP servers and skills synced across many agent tools: [cc-switch](https://github.com/farion1231/cc-switch);
- a Docker stack that launches an agent in one command: [Harbor](https://github.com/av/harbor);
- one local endpoint that swaps models or fronts several providers: [llama-swap](https://github.com/mostlygeek/llama-swap) or [claude-code-router](https://github.com/musistudio/claude-code-router);
- several agents on one repository, each in its own git worktree: [Emdash](https://github.com/generalaction/emdash), [T3 Code](https://github.com/pingdotgg/t3code) or [Claude Squad](https://github.com/smtg-ai/claude-squad);
- a chat app for local models: [LM Studio](https://lmstudio.ai/) or [Jan](https://www.jan.ai/). Workbench attaches to both.

Workbench does not run agents in parallel or review diffs.

## Install

Each kit unpacks to `Agent-Workbench` and includes Python. The first start downloads Electron and Python packages and checks their pinned SHA-256.

The downloads are not code-signed or notarised; check the SHA-256 first.

Kits: `agent-workbench-windows-x64.zip`, `agent-workbench-linux-x64.tar.gz`, `agent-workbench-macos-arm64.tar.gz` (preview) and `agent-workbench-source.tar.gz` for your own Python, each listed in `SHA256SUMS`.

[Download 0.5.0](https://github.com/jamesarslan/local-agent-workbench/releases/tag/v0.5.0). The install scripts check `SHA256SUMS`, install for your user without sudo or administrator rights, and add a `workbench` command; `--uninstall` (`-Uninstall` on Windows) removes the app and keeps your data.

### Windows

Status for 0.5.0: some checks were run on Windows. The tested-configurations table says which.

In PowerShell:

```powershell
irm https://github.com/jamesarslan/local-agent-workbench/releases/latest/download/install.ps1 | iex
```

To read it first:

```powershell
irm https://github.com/jamesarslan/local-agent-workbench/releases/latest/download/install.ps1 -OutFile install.ps1
notepad install.ps1
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

By hand, beside the zip and `SHA256SUMS`; compare the hash with its line:

```powershell
Get-FileHash -Algorithm SHA256 agent-workbench-windows-x64.zip
Unblock-File agent-workbench-windows-x64.zip
Expand-Archive agent-workbench-windows-x64.zip -DestinationPath .
```

Then double-click `Launch Workbench.cmd` in `Agent-Workbench`. If Windows shows "Windows protected your PC", choose More info, then Run anyway. If Smart App Control blocks the kit, try the source archive with your own Python.

### Linux

Status for 0.5.0: some checks were run on Linux. The tested-configurations table says which.

```sh
curl -fsSL https://github.com/jamesarslan/local-agent-workbench/releases/latest/download/install.sh | sh
```

To read it first:

```sh
curl -fsSLO https://github.com/jamesarslan/local-agent-workbench/releases/latest/download/install.sh
less install.sh
sh install.sh
```

Then run `sh ~/.local/lib/agent-workbench/setup-linux.sh` once; it lists the desktop libraries it needs, asks before using sudo and starts the app. Later starts: `workbench`.

By hand, in the folder with the archive and `SHA256SUMS`:

```sh
sha256sum -c SHA256SUMS --ignore-missing
tar -xzf agent-workbench-linux-x64.tar.gz
sh Agent-Workbench/setup-linux.sh
```

Later starts: `sh Agent-Workbench/workbench.sh`.

### macOS (preview)

Status for 0.5.0: preview. The macOS checks listed in the README have not all passed. Intel Macs are not supported.

```sh
curl -fsSL https://github.com/jamesarslan/local-agent-workbench/releases/latest/download/install.sh | sh
```

To read it first, see Linux. By hand, in Terminal:

```sh
cd ~/Downloads
shasum -a 256 -c SHA256SUMS --ignore-missing
tar -xzf agent-workbench-macos-arm64.tar.gz
sh Agent-Workbench/"Launch Workbench.command"
```

The script and launcher remove the quarantine mark so the bundled Python can start. If macOS refuses the unnotarised launcher in Finder, allow it with Open Anyway in System Settings, Privacy & Security ([Apple's steps](https://support.apple.com/guide/mac-help/open-a-mac-app-from-an-unknown-developer-mh40616/mac)).

## What leaves your computer

Agent Workbench has no telemetry, no analytics, no crash reporting, no automatic update check and no account. It contacts:

- Downloads: GitHub, PyPI, Hugging Face, and the publishers of agent CLIs and kit MCP servers.
- A hosted model: the provider gets your key and what the agent sends, including file contents. OpenAI, OpenRouter, DeepSeek and Groq sources also read OpenRouter's public model list, without a key.
- Web tools you switch on: search engines get your queries; the crawler visits the pages you ask for.

Keys stay in the system credential vault, or in memory without one, and reach the agent through its environment, never its command line or project files. [Every host, and why](SECURITY.md#outbound-hosts)

## Tested configurations

From [docs/verification.json](docs/verification.json), with a note per check; "From the 0.4.0 run" means not repeated.

| System | Passed | Failed or partly working | Not run |
| --- | --- | --- | --- |
| macOS 27.2 on a Mac with Apple M3 Max, 48 GB unified memory (preview) | Unit tests; Desktop smoke test; Managed llama.cpp install; Local model start and stop; Managed model on an SSH machine; Attach a server on an SSH machine; OMP session answered; Claude Code session answered; Codex session answered; opencode session answered; Terminal dock: open, type, close; Starter kit applied | Install with the install script; Install by hand from a browser download; First start (Python packages and desktop runtime); Attach Ollama; Attach LM Studio; Pair and check an SSH machine | Others |
| Fedora 44 Workstation, x86_64 with an AMD Radeon RX 9070 series GPU, reached over SSH | Unit tests; Install with the install script; Local model start and stop; Doctor | First start (Python packages and desktop runtime) | Others |
| Fedora 44 Workstation, x86_64 with AMD Radeon 880M graphics (Wayland desktop) | Unit tests; First start (Python packages and desktop runtime); Session with a hosted provider; Terminal dock: open, type, close; Doctor | Install with the install script | Others |
| Windows 11 x64 workstation with an NVIDIA GeForce RTX 5090 | Unit tests; Install with the install script; Local model start and stop; OMP session answered; Terminal dock: open, type, close; Starter kit applied; Doctor | First start (Python packages and desktop runtime); Web search and page crawling | Others |
| GitHub Actions ubuntu-latest | Unit tests; Desktop smoke test; Continuous integration job on this system; Install with the install script; First start (Python packages and desktop runtime) | | Others |
| GitHub Actions windows-latest | Unit tests; Desktop smoke test; Continuous integration job on this system; Install with the install script | | Others |
| GitHub Actions macos-latest (preview) | Unit tests; Desktop smoke test; Continuous integration job on this system; Install with the install script | | Others |

At the 0.5.0 commit 1,373 Python tests ran without failure and 94 Node tests passed on all three computers. All 13 [CI jobs](https://github.com/jamesarslan/local-agent-workbench/actions/runs/45246056684) passed at 96fe980 (0.5.0 plus test fixes), with Python 3.12 to 3.14. Over SSH from the Mac, OMP, Claude Code and Codex wrote a file with models on the Windows machine, OMP also on Fedora ([SSH machines](results/windows-ssh-machines.json), [Strata](results/strata-rtx5090-windows.json)). Pi and Hermes were not run.

macOS stays a preview until a browser download, the desktop runtime's first start, an agent window and Doctor pass on Apple Silicon.

## First run

The **Setup guide** opens on the first start; skip it or reopen it from the sidebar.

1. This computer: memory, running model servers, installed agents. Nothing downloads until you choose.
2. Model: a running server, a model for this computer, a hosted API, an SSH machine, an address, or an agent's own sign-in.
3. Agent: agents the source cannot serve are switched off with the reason; **Install** installs a missing CLI or opens its install page.
4. Project: pick a folder; a suggested starter kit shows every file before writing.
5. Ready: start the agent in the terminal dock or a window, or later from **Launch**, which remembers the folder's setup.

## Run summaries

New in 0.5.0. Each launch from the app leaves a run record without keys: agent and version, model source and model, hashed instruction files and skills, MCP servers and folder prompt. **Copy summary** copies it as Markdown; **Save as profile** makes it a profile to rerun an old task. [More](docs/sessions.md#run-summaries)

## Where settings live

Plain files: `.agent-workbench/profiles.json` (agent, model source, connection and model names, settings) and `project.json` (MCP servers) sit in the project beside `AGENTS.md` and skills, with no keys or machine paths: commit them to move the setup. Connections, last choices, runs and keys stay on this computer. New in 0.5.0: a folder default that another computer offers after review. [More](docs/project-setup.md#settings-that-travel-with-the-folder)

## Guides

- [Getting started](docs/getting-started.md): the setup guide.
- [Model sources](docs/model-sources.md): local models, running servers, SSH machines, hosted APIs.
- [Project setup](docs/project-setup.md): instructions, skills, MCP servers, starter kits.
- [Sessions and terminals](docs/sessions.md): terminals, sessions, sandbox.
- [Troubleshooting](docs/troubleshooting.md): Doctor and uninstalling.
- [Security and your data](docs/security-and-data.md): where data and keys live.
- [Reference](docs/reference.md): commands, variables, engines, ports.
- [Security policy](SECURITY.md), [Measurements](MEASUREMENTS.md), [Contributing](CONTRIBUTING.md).

## Command line

`workbench` alone opens the app (in a kit folder: `sh workbench.sh cli.py <verb>` or `Run.cmd cli.py <verb>`). `doctor` checks the environment, `sessions` lists running and recent sessions, `agent` starts the folder's saved agent and `status` shows the local model.

## Building from source

You need Python 3.12 to 3.14; Git only for Strata, Node only for Pi, Codex, opencode and `npx` kit servers. Start with `sh workbench.sh` (Linux), `sh "Launch Workbench.command"` (macOS) or `Launch Workbench.cmd` (Windows); the first start builds its environment from the hash-pinned `requirements.lock`.

## Licence and credits

Agent Workbench is MIT-licensed ([LICENSE](LICENSE)). The bundled xterm.js and its fit addon keep their MIT notices in `desktop/vendor/xterm`; the adapted Firecrawl Compose file is AGPL-3.0 ([LICENSE-Firecrawl](LICENSE-Firecrawl)); Strata's fixtures keep its MIT notice ([fixtures/LICENSE-Strata.txt](fixtures/LICENSE-Strata.txt)). Models, agent CLIs and engines keep their own licences.

Upstream: [OMP](https://github.com/can1357/oh-my-pi), [Pi](https://github.com/earendil-works/pi), [Strata](https://github.com/Niko1221/Strata), [llama.cpp](https://github.com/ggml-org/llama.cpp), [vLLM](https://github.com/vllm-project/vllm), [Firecrawl](https://github.com/firecrawl/firecrawl), [DDGS](https://github.com/deedy5/ddgs), [Windows-MCP](https://github.com/CursorTouch/Windows-MCP).

Created by [James Arslan](https://jamesarslan.com/) ([project guide](https://jamesarslan.com/agent-workbench)).

Product names belong to their owners. Agent Workbench is not affiliated with or endorsed by them.
