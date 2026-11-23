# Security policy

This page says how to report a security problem in Agent Workbench, what the app protects and what it does not, every host it can contact, and how each download is checked.

## Supported versions

| Version | Supported |
| --- | --- |
| 0.4.x | Yes |
| 0.3.0-rc.1 and earlier | No. These were private release candidates |

Fixes are released as a new version. There are no back-ports to older versions.

## Reporting a vulnerability

Report it privately through GitHub: https://github.com/jamesarslan/local-agent-workbench/security/advisories/new

That form is the reporting route. No email address is published for security reports. If the form is not available, open an issue that says only "security contact wanted" and you will be contacted.

Include:

- the Agent Workbench version and your system;
- the affected feature or file;
- the steps that reproduce the problem.

Leave out API keys, private traces and other people's data.

What to expect: an acknowledgement within seven days. Agent Workbench is maintained by one person, so fixes are scheduled by severity.

Scope: any vulnerability in Agent Workbench, including how it launches agents, handles keys, reads project files and uses its internal IPC channel. Out of scope: flaws in the agent CLIs, model servers and MCP servers themselves (report those to their projects), and attacks that need someone who already controls your user account.

Disclosure is coordinated: the fix is released first, then the advisory is published. The reporter is credited in the changelog unless they prefer not to be.

## Security model

- Agent Workbench runs with your operating-system account. So do the agents, MCP servers and terminals it starts: they can read and change what your account can.
- Anything read from a project folder is untrusted input. Values from project profiles and MCP lists are checked before use, nothing from a project reaches a shell, and a folder that carries its own executable configuration (MCP servers, hooks) needs your consent before the first launch there.
- The desktop window is sandboxed. Its Content-Security-Policy blocks network requests and remote code, and the window talks to the Python backend through one channel that accepts a fixed list of actions.
- The local model server that Workbench manages listens on `127.0.0.1` and requires a generated key. A Custom engine gets the same protection only when its manifest uses the `{host}` and `{keyfile}` placeholders.
- Plain HTTP is accepted only to this computer. Another machine is reached through an SSH tunnel, and the host key must already be known to your SSH client.
- Hosted API keys live in the system credential vault or in a process environment. Workbench does not put them on a command line, in project files or in its logs.
- There is no telemetry, analytics, crash reporting, automatic update check or account.
- For a separate filesystem and user account, start the agent in the Docker sandbox and review the folders it mounts.

## Outbound hosts

This table lists every host that Workbench's code names or that the tools it runs for you contact, with the reason. "You" means an action you take in the interface or on the command line.

| Host | When | Why | What is sent |
| --- | --- | --- | --- |
| `github.com` and GitHub's release download hosts | Install script; starting Workbench the first time; **Install** for an engine or OMP; setting up a model on an SSH machine (from that machine); building a kit | Kit archives and `SHA256SUMS`; the Electron runtime; llama.cpp and Strata archives; the Strata source; the OMP binary; CPython for the kit builder | Plain downloads |
| `api.github.com` | When you choose the Updated engine channel | The newest engine release and the digests GitHub reports | An unauthenticated request |
| `raw.githubusercontent.com` | **Install** for OMP; building the sandbox image | The OMP install script at a pinned commit | Plain download |
| `pypi.org`, `files.pythonhosted.org` | Starting Workbench the first time and after an update; installing the Hugging Face CLI or vLLM; a tool started with `uvx` (Blender, Windows desktop, some kit servers) | Python packages | Plain downloads |
| The PyTorch package index that uv selects | Installing vLLM | PyTorch builds | Plain downloads |
| `registry.npmjs.org` | Installing Pi, Codex or opencode; a kit server started with `npx` | Packages | Plain downloads |
| `bun.sh` | **Install** for Bun | The Bun install script | Plain download |
| Homebrew's hosts (`formulae.brew.sh`, `ghcr.io`) | macOS: installs that go through Homebrew | Packages | Homebrew's own requests |
| `cdn.winget.microsoft.com` and each package's publisher | Windows: installs that go through winget | Packages | winget's own requests |
| Your distribution's package mirrors | Linux: `setup-linux.sh` and installs through apt or dnf; Windows: setting up the `AgentWorkbench-Web` WSL distribution | Desktop libraries, Docker, tools | The package manager's own requests |
| `ghcr.io`, `docker.io` | Starting page crawling; building the sandbox image; a kit server started with `docker` | Container images | Docker's own requests |
| `huggingface.co` and the download hosts it redirects to | Downloading or searching for a model; setting up the model on an SSH machine (from that machine) | Model files and listings | Plain requests. A token saved with `hf auth login` goes to Hugging Face |
| `openrouter.ai` | (1) You use an OpenRouter connection. (2) You select an OpenAI, OpenRouter, DeepSeek or Groq source | (1) The provider you chose: sign-in, model list and the agent's requests. (2) OpenRouter's public model list, to read which models accept images and their context size | (1) Your key and what the agent sends. (2) The request alone, without a key |
| `api.openai.com`, `api.groq.com`, `api.deepseek.com`, `api.anthropic.com`, `generativelanguage.googleapis.com`, `api.together.ai`, `api.fireworks.ai`, `api.mistral.ai`, `api.x.ai`, `api.cerebras.ai`, `router.huggingface.co` | When you select that provider | The model list (Workbench) and the session (the agent) | Your key for that provider and everything the agent sends, which can include file contents and images |
| An address you type | Attaching, re-checking or using a compatible server | The server you chose | Probe requests, your key for it if you saved one, then the agent's requests |
| Your SSH machine | Pairing, checking, connecting, remote sessions | SSH | SSH traffic to the host you named |
| Public search engines | The agent calls the web search tool you switched on | Search | The query |
| Sites the agent asks to read | The agent calls the page-crawling tool you switched on | Pages fetched through the local Firecrawl containers | The address |
| Hosted kit servers: `mcp.context7.com`, `huggingface.co`, `knowledge-mcp.global.api.aws`, `learn.microsoft.com` | When you enable that server from a starter kit | The server you enabled | What the agent sends to that tool |
| `127.0.0.1` | Always | The managed model, discovery of local servers, the page-crawling stack, Blender, editor bridges of some kit servers | Nothing leaves this computer |

Pages opened in your browser. When you choose a guide or a "get a key" link, Workbench opens the page in your browser: `platform.openai.com`, `console.groq.com`, `platform.deepseek.com`, `openrouter.ai`, `code.claude.com`, `developers.openai.com`, `opencode.ai`, `hermes-agent.nousresearch.com`, `nodejs.org`, `bun.sh`, `docs.astral.sh`, `docs.docker.com`, `cli.github.com`, `docs.aws.amazon.com`, `www.blender.org`, `docs.vllm.ai`, and from the kit and model descriptions `dev.epicgames.com` and `thinkingmachines.ai`. Workbench itself sends nothing to them. The address `opencode.ai/config.json` also appears as the schema name inside the opencode configuration Workbench writes; Workbench does not fetch it.

Programs Workbench starts. The agent CLIs, package managers, Docker and MCP servers are separate programs with their own network use and telemetry settings. Workbench sets the opt-out variables it knows for the tools it starts; it cannot vouch for the rest.

## How downloads are verified

| Download | Checked against | Limits |
| --- | --- | --- |
| Kit archive | `SHA256SUMS` of the same release. The install scripts check it and stop on a mismatch; the by-hand steps show the command | A checksum shows the file is intact, not who made it. The kits are not code-signed or notarised. The release workflow is set up to record a build attestation for each file; it has not run yet, so 0.4.0 may ship without one. When a release has one, `gh attestation verify <file> --repo jamesarslan/local-agent-workbench` checks it |
| Python inside the kit | SHA-256 pinned in `release/build.py`, checked when the kit is built | — |
| Python packages | `requirements.lock`, installed with `pip --require-hashes --only-binary :all:` | — |
| Electron runtime | SHA-256 pinned in `desktop_app.py` for each system | — |
| llama.cpp, Tested channel | SHA-256 pinned in `setup_engines.py` | For 0.4.0 the macOS arm64 archive was downloaded and matched its pin; the other pins were taken from the digests GitHub reports |
| Strata | SHA-256 and source commit pinned in `setup_engines.py` | Pins taken from the digests GitHub reports |
| Engines, Updated channel | The digest GitHub reports at that moment | Not pinned in this repository |
| vLLM | A version pin in `setup-vllm.sh` | No digest; its dependencies are not pinned |
| Catalog models | SHA-256 and size in `model_catalog.json`, at a pinned revision | A model you pick from a Hugging Face search has no pinned digest |
| Engine, model and OMP on an SSH machine | SHA-256 values pinned in `remote_node.py` | — |
| OMP on this computer | The upstream install script at a pinned commit, asked for a pinned OMP version | Workbench does not check the binary's digest itself |
| Pi, Codex, opencode; Homebrew, winget, apt and dnf packages | The package manager's own checks | Versions are not pinned by Workbench, apart from the opencode 1.x line |
| Page-crawling images | Image digests for the Firecrawl images; version tags for the others | See `firecrawl.compose.yml` |
| Kit MCP servers | The version in the launch command where the publisher provides one | No digest. Fetched from the publisher when an agent first starts the server |

## Local data and credentials

- Workbench keeps settings, logs, downloaded models and the local model's key in its data folder, which is private to your account: `~/Library/Application Support/Agent Workbench` on macOS, `${XDG_DATA_HOME:-~/.local/share}/agent-workbench` on Linux, `%LOCALAPPDATA%\Agent Workbench\data` on Windows when installed with the script, and a `.local` folder inside the app folder otherwise.
- Hosted keys are stored in the system credential vault under the service name `Agent Workbench`. Without a vault, a key is kept in memory until Workbench closes, or you set its environment variable before starting Workbench. The agent receives its key in its process environment.
- The local model's key is in `api-key.txt` in the data folder. The OMP profiles Workbench generates under `~/.omp/profiles/` can contain it. On an SSH machine, the remote model's key is in `~/.local/share/agent-workbench-node/api-key`, readable by your account there.
- Session histories, traces, generated agent profiles and the clipboard can hold private material.
- A portable project profile carries a prompt and tool choices. Review it before you share the folder.
- Every location and how to remove it: [Security and your data](docs/security-and-data.md).

## Stopping what Workbench started

- The local model: **Stop** in the status bar, or `workbench stop`.
- A model on an SSH machine: **Stop…** on that machine in **Machines**.
- Page crawling: `workbench web-stop`.
- When Workbench quits, its Python backend closes the terminal sessions in the dock before it exits. This was not checked in a full desktop run for 0.4.0.
- Native agent windows, the local model server and detached remote tmux sessions keep running after Workbench closes. Stop them yourself when you no longer need them.
