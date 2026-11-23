# Agent Workbench

Agent Workbench is a desktop app that lets you pick where a model runs (on this computer, on a GPU machine over SSH, in an Ollama or LM Studio server you already use, or at a hosted API) and which coding-agent CLI to use for a project folder, and keeps that choice with the folder, together with its instructions, skills and MCP tools.

Version 0.4.0 · MIT licence · Windows x64, Linux x64, macOS arm64 (preview) · [Website](https://jamesarslan.github.io/local-agent-workbench/) · [Changelog](CHANGELOG.md)

## Is this for you?

Workbench fits when you want a model and agent (OMP, Pi, Claude Code, Codex, Hermes or opencode) per project folder, a model on another machine over plain SSH, or a preview of what an agent will read. Another tool fits better when you want:

- one agent on a model Ollama serves: [`ollama launch`](https://docs.ollama.com/cli) starts it in one command;
- providers, MCP servers and skills synced across many agent tools: [cc-switch](https://github.com/farion1231/cc-switch);
- a Docker stack that launches an agent in one command: [Harbor](https://github.com/av/harbor);
- one local endpoint that swaps models on request, or that sits in front of several providers: [llama-swap](https://github.com/mostlygeek/llama-swap) or [claude-code-router](https://github.com/musistudio/claude-code-router);
- several agents on one repository, each in its own git worktree: [Emdash](https://github.com/generalaction/emdash), [T3 Code](https://github.com/pingdotgg/t3code) or [Claude Squad](https://github.com/smtg-ai/claude-squad);
- a chat app for local models: [LM Studio](https://lmstudio.ai/) or [Jan](https://www.jan.ai/). Workbench can attach to either.

Workbench does not run agents in parallel, create git worktrees or review diffs.

## Install

Each kit unpacks to `Agent-Workbench` and includes Python. The first start downloads Electron and Python packages and checks their pinned SHA-256 values.

The downloads are not code-signed or notarised. Your system may warn you before it runs them. Check the SHA-256 first.

The kits are `agent-workbench-windows-x64.zip`, `agent-workbench-linux-x64.tar.gz` and `agent-workbench-macos-arm64.tar.gz` (preview), plus `agent-workbench-source.tar.gz` for any system with your own Python. Check each against `SHA256SUMS` as shown below.

[Download 0.4.0](https://github.com/jamesarslan/local-agent-workbench/releases/tag/v0.4.0). The install scripts check `SHA256SUMS`, install for your user without sudo or administrator rights, and add a `workbench` command; `--uninstall` (`-Uninstall` on Windows) removes the app and keeps your data.

### Windows

Status for 0.4.0: some checks were run on Windows. The tested-configurations table says which.

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

By hand, beside the zip and `SHA256SUMS`; compare the hash with its line there:

```powershell
Get-FileHash -Algorithm SHA256 agent-workbench-windows-x64.zip
Unblock-File agent-workbench-windows-x64.zip
Expand-Archive agent-workbench-windows-x64.zip -DestinationPath .
```

Then double-click `Launch Workbench.cmd` in `Agent-Workbench`. If Windows shows "Windows protected your PC", choose More info, then Run anyway. If Smart App Control blocks the kit, try the source archive with your own Python.

### Linux

Status for 0.4.0: some checks were run on Linux. The tested-configurations table says which.

```sh
curl -fsSL https://github.com/jamesarslan/local-agent-workbench/releases/latest/download/install.sh | sh
```

To read it first:

```sh
curl -fsSLO https://github.com/jamesarslan/local-agent-workbench/releases/latest/download/install.sh
less install.sh
sh install.sh
```

Then run `sh ~/.local/lib/agent-workbench/setup-linux.sh` once; it lists the desktop libraries it needs, asks before using sudo, then starts the app. Later starts: `workbench`.

By hand, in the folder with the archive and `SHA256SUMS`:

```sh
sha256sum -c SHA256SUMS --ignore-missing
tar -xzf agent-workbench-linux-x64.tar.gz
sh Agent-Workbench/setup-linux.sh
```

Later starts: `sh Agent-Workbench/workbench.sh`.

### macOS (preview)

Status for 0.4.0: preview. The macOS checks listed in the README have not all passed. Intel Macs are not supported.

```sh
curl -fsSL https://github.com/jamesarslan/local-agent-workbench/releases/latest/download/install.sh | sh
```

To read it first:

```sh
curl -fsSLO https://github.com/jamesarslan/local-agent-workbench/releases/latest/download/install.sh
less install.sh
sh install.sh
```

By hand, in Terminal:

```sh
cd ~/Downloads
shasum -a 256 -c SHA256SUMS --ignore-missing
tar -xzf agent-workbench-macos-arm64.tar.gz
sh Agent-Workbench/"Launch Workbench.command"
```

The script and the launcher remove the download mark (quarantine) so the bundled Python can start. If macOS refuses the unnotarised launcher when you double-click it in Finder, allow it with Open Anyway in System Settings, Privacy & Security ([Apple's steps](https://support.apple.com/guide/mac-help/open-a-mac-app-from-an-unknown-developer-mh40616/mac)). For agent windows, macOS asks once whether Workbench may control Terminal.

## What leaves your computer

Agent Workbench has no telemetry, no analytics, no crash reporting, no automatic update check and no account. It contacts other computers for these things:

- Downloads: GitHub, PyPI, Hugging Face, and the publishers of agent CLIs and kit MCP servers.
- A hosted model: the provider gets your key and what the agent sends, including file contents. OpenAI, OpenRouter, DeepSeek and Groq sources also read OpenRouter's public model list, without a key.
- Web tools you switch on: search engines get your queries; the crawler visits the pages you ask for.

Keys stay in the system credential vault, or in memory without one, and reach the agent through its environment, never its command line or project files. [Every host, and why](SECURITY.md#outbound-hosts)

## Tested configurations

From [docs/verification.json](docs/verification.json).

| System | Passed | Failed or partly working | Not run |
| --- | --- | --- | --- |
| macOS 27.2 on a Mac with Apple M3 Max, 48 GB unified memory (preview) | Unit tests; Managed llama.cpp install; Local model start and stop; OMP session answered; Claude Code session answered; Codex session answered; opencode session answered | Desktop smoke test; Install with the install script; Install by hand from a browser download; First start (Python packages and desktop runtime); Attach Ollama; Attach LM Studio | Every other check |
| Fedora 44 Workstation, x86_64 with an AMD Radeon RX 9070 series GPU, reached over SSH | Unit tests | Install with the install script; First start (Python packages and desktop runtime) | Every other check |
| Fedora 44 Workstation, x86_64 with AMD Radeon 880M graphics (Wayland desktop) | Unit tests; First start (Python packages and desktop runtime); Session with a hosted provider; Terminal dock: open, type, close; Doctor | Install with the install script | Every other check |
| Windows 11 x64 workstation with an NVIDIA GeForce RTX 5090 | Install with the install script; First start (Python packages and desktop runtime); Local model start and stop; OMP session answered; Terminal dock: open, type, close; Starter kit applied | Unit tests; Web search and page crawling | Every other check |

Full notes per check: [docs/verification.json](docs/verification.json). On the Mac, OMP, Claude Code, Codex and opencode each wrote a requested file in an empty folder through Workbench's launch code (managed model and Ollama 0.40.8). Pi, Hermes and hosted CI were not run.

macOS stays a preview until these checks pass on Apple Silicon: Unit tests; Desktop smoke test; Continuous integration job on this system; Install with the install script; Install by hand from a browser download; First start (Python packages and desktop runtime); Managed llama.cpp install; Local model start and stop; Agent in a native terminal window; Terminal dock: open, type, close; Doctor.

Checks that are not listed for a system were not run on it for 0.4.0.

Earlier measurements on other hardware: [MEASUREMENTS.md](MEASUREMENTS.md).

## First run

The **Setup guide** opens on the first start; skip it or reopen it from the sidebar.

1. This computer: memory, running model servers, installed agents. Nothing downloads until you choose.
2. Model: a running server, a model for this computer, a hosted API, an SSH machine, an address, or an agent's own sign-in.
3. Agent: agents the source cannot serve are switched off with the reason; **Install** installs a missing CLI or opens its install page.
4. Project: pick a folder; a suggested starter kit shows every file before writing.
5. Ready: start the agent in the terminal dock or a window, or later from **Launch**, which remembers the folder's setup.

## Run summaries

New in the next release. Each launch from the app leaves a run record without keys: agent and version, model source and model, hashed instruction files and skills, MCP servers and the folder prompt it received. **Copy summary** copies it as Markdown; **Save as profile** makes it a profile to rerun an old task. [More](docs/sessions.md#run-summaries)

## Where settings live

In plain files. `.agent-workbench/profiles.json` (agent, model source, connection and model names, settings) and `project.json` (MCP servers) sit in the project beside `AGENTS.md` and skills, with no keys or machine paths: commit them to move the setup. Connections, last choices, runs and keys stay on this computer. New in the next release: a folder default that another computer offers after review. [More](docs/project-setup.md#settings-that-travel-with-the-folder)

## Guides

- [Getting started](docs/getting-started.md): the setup guide, step by step.
- [Model sources](docs/model-sources.md): local models, running servers, SSH machines, hosted APIs.
- [Project setup](docs/project-setup.md): instructions, skills, MCP servers, starter kits.
- [Sessions and terminals](docs/sessions.md): terminals, sessions, sandbox.
- [Troubleshooting](docs/troubleshooting.md): Doctor and uninstalling.
- [Security and your data](docs/security-and-data.md): where data and keys live.
- [Reference](docs/reference.md): commands, variables, engines, ports.
- [Security policy](SECURITY.md), [Changelog](CHANGELOG.md), [Measurements](MEASUREMENTS.md), [Contributing](CONTRIBUTING.md).

## Command line

After a script install the command is `workbench`; alone, it opens the app (in a kit folder: `sh workbench.sh cli.py <verb>` or `Run.cmd cli.py <verb>`). `doctor` checks the environment, `sessions` lists running and recent sessions, `agent` starts the folder's saved agent and `status` shows the local model. All verbs: [Reference](docs/reference.md).

## Building from source

You need Python 3.12, 3.13 or 3.14; Git only for Strata, Node only for Pi, Codex, opencode and `npx` kit servers. Start with `sh workbench.sh` (Linux), `sh "Launch Workbench.command"` (macOS) or `Launch Workbench.cmd` (Windows); the first start builds its environment from the hash-pinned `requirements.lock`. Tests: see [Contributing](CONTRIBUTING.md).

## Licence and credits

Agent Workbench is MIT-licensed ([LICENSE](LICENSE)). The bundled xterm.js and its fit addon keep their MIT notices in `desktop/vendor/xterm`; the adapted Firecrawl Compose file is AGPL-3.0 ([LICENSE-Firecrawl](LICENSE-Firecrawl)); Strata's fixtures keep its MIT notice ([fixtures/LICENSE-Strata.txt](fixtures/LICENSE-Strata.txt)). Models, agent CLIs and engines keep their own licences.

Upstream: [OMP](https://github.com/can1357/oh-my-pi), [Pi](https://github.com/earendil-works/pi), [Strata](https://github.com/Niko1221/Strata), [llama.cpp](https://github.com/ggml-org/llama.cpp), [vLLM](https://github.com/vllm-project/vllm), [Firecrawl](https://github.com/firecrawl/firecrawl), [DDGS](https://github.com/deedy5/ddgs), [Windows-MCP](https://github.com/CursorTouch/Windows-MCP).

Created by James Arslan.

Product names belong to their owners. Agent Workbench is not affiliated with or endorsed by them.
