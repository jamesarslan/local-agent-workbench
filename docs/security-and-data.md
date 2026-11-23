# Security and your data

This guide says what Agent Workbench and the programs it starts can do on your computer, where it keeps data and keys, what it treats as untrusted, and what reaches the network. The [security policy](../SECURITY.md) has the reporting route, every outbound host and how each download is checked.

## What runs as you

Agent Workbench runs with your operating-system account, and so does everything it starts: the agents, the MCP servers, the shells in the terminal dock and the local model server. An agent can read and change whatever your account can, not only the project folder. Workbench starts it in the project folder; how far the agent strays from there is up to the agent and its own permission settings.

Workbench leaves those permission settings to the agent. It does not pass a flag that switches an agent's approval questions off, such as OMP's `--auto-approve`. It sets two permission options: OMP, with a Workbench model source, starts with `--approval-mode write`, one of OMP's own approval modes, and Codex starts with `--sandbox workspace-write`, which limits where Codex may write. For a separate filesystem and user account, use the [Docker sandbox](#docker-sandbox).

## Where Workbench keeps data

Workbench keeps its state in one data folder, private to your account:

| How you installed | Data folder |
| --- | --- |
| Install script on macOS | `~/Library/Application Support/Agent Workbench` |
| Install script on Linux | `${XDG_DATA_HOME:-~/.local/share}/agent-workbench` |
| Install script on Windows | `%LOCALAPPDATA%\Agent Workbench\data` |
| A kit or checkout you start by hand | `.local` inside the app folder |

`WORKBENCH_DATA` overrides all of these. Doctor warns when other accounts can read the folder, and on a Mac when it sits in a folder iCloud Drive can sync.

What is in it:

| Path | What it holds | Holds a secret? |
| --- | --- | --- |
| `settings.json` | Your settings, named setups and the default prompt | No |
| `connections.json` | Saved connections: name, provider, address, model, the APIs a server offered | No; keys are in the vault |
| `api-key.txt` | The key of the local model server | Yes |
| `projects.json` | What is remembered per project folder, your consent answers and per-folder prompts | No, but prompts can be private |
| `onboarding.json` | Progress of the setup guide | No |
| `plugins.json` | The MCP servers you registered, copied as they were in the file you chose | Yes, if that file held keys |
| `clients/` | One settings folder per agent session started with a Workbench source: Claude Code, Codex and Pi settings and session history, the prompt file for opencode, and the MCP files Workbench writes for Claude Code and Pi | Can hold the literal values of registered servers, and your session histories |
| `machines.json`, `machine-links.json`, `ssh/` | Saved machines, their tunnels, and the SSH key Workbench made for each machine | Yes: the SSH private keys in `ssh/` |
| `boxes/` | One folder per Docker sandbox, with its SSH key and tunnel record | Yes: the sandbox keys |
| `remote-sessions.json` | Remote tmux sessions Workbench started | No |
| `models/` | Downloaded model files (your model folder, unless you chose another) | No |
| `runtime/`, `Strata/`, `updates/` | Installed engines | No |
| `templates/` | Corrected chat templates for models Workbench serves | No |
| `desktop-runtime/` | The Electron runtime the window uses | No |
| `workspace/` | The scratch project folder | Whatever you put there |
| `traces/` | Recorded traces from **Advanced → Trace** | Can hold project content |
| `runs/` | From the next release: one record per agent launch, for its run summary ([Run summaries](sessions.md#run-summaries)) | No keys, but folder paths, file names and the folder prompt can be private |
| `launchers/` | Launchers made with **Create launcher** | No |
| `serve.log`, `engine.log`, `setup.log`, `bridge.log` | Logs. `bridge.log` holds start-up lines and errors, not the requests themselves | No |
| `benchmark-*.json`, `latest-tool-load.json` | Results of your own benchmark runs | No |
| `model-context.json`, `openrouter-models.json` | Cached context sizes and model lists | No |

Outside the data folder:

- OMP profiles that Workbench generates, under `~/.omp/profiles` (`local-agent-workbench*` and `workbench-*`). The profile for the local model contains the local model's key.
- On an SSH machine with the managed model, `~/.local/share/agent-workbench-node`, which holds the engine, the model and that model's key, readable by your account there.
- In your project folders, only what you confirmed in a plan: `.agent-workbench/project.json`, `.agent-workbench/profiles.json`, `AGENTS.md`, a `CLAUDE.md` pointer, skills and exported MCP files.

Session histories, traces, generated agent profiles and anything you copy to the clipboard can hold private material from your projects.

## Keys

Workbench handles several kinds of key:

- Hosted provider keys and keys of servers you attached go to the system credential vault (Keychain on macOS, Credential Manager on Windows, a Secret Service vault such as GNOME Keyring, or KWallet, on Linux) under the service name `Agent Workbench`. Each entry is named after the connection's address and ID, so two connections to the same provider keep separate keys.
- Values for variables of project MCP servers go to the same vault, as entries named `mcp-env:` followed by the variable name.
- Without a vault, a provider key is kept in memory until Workbench closes, and a project server value is not saved; set the variable in your environment instead.
- A connection reads `WORKBENCH_KEY_` followed by its connection ID from the environment when that variable is set, and the provider's own variable (such as `OPENAI_API_KEY`) appears as a connection of its own.
- The local model's key is generated by Workbench and kept in `api-key.txt`.

Where a key goes when an agent starts: into the agent's process environment, as `WORKBENCH_SESSION_KEY` or the variable the agent expects (for Claude Code, `ANTHROPIC_AUTH_TOKEN` or `ANTHROPIC_API_KEY`). The settings files Workbench writes for Pi, Codex and opencode refer to that variable by name. Workbench does not put a key on a command line, in a project file or in its logs. Besides `api-key.txt` itself, the OMP profile for the local model holds that key in plain text.

**Forget key** on a connection deletes its saved key, and removing a connection deletes its key too. **Forget value** deletes a project server's value.

## Project folders are untrusted input

A project folder can come from anyone, so Workbench treats everything it reads there as untrusted:

- Files that make an agent start programs need your consent before the first launch in the folder, and again after they change ([Consent](project-setup.md#consent)).
- `.agent-workbench/project.json`, `.agent-workbench/profiles.json` and kit files are checked field by field. A value of the wrong type or with unexpected characters is refused with a sentence, not used.
- Applying a profile shows every setting it would change and its prompt in full, and needs your confirmation. A profile cannot change your global settings.
- Nothing read from a project reaches a shell. Workbench starts programs with argument lists. On Windows it refuses to pass characters that `cmd.exe` would interpret to an agent installed as a `.cmd` or `.bat` file.
- Workbench writes inside a project only to the file names its plans list, and refuses names that would leave the folder.

## Network

Every host Workbench can contact, and why, is in [Outbound hosts](../SECURITY.md#outbound-hosts). In short:

- There is no telemetry, analytics, crash reporting, automatic update check or account.
- Everything Workbench serves itself listens on `127.0.0.1` only: the local model, the page-crawling stack, the SSH port of each sandbox and the local end of every SSH tunnel. A Custom engine receives that address only when its manifest uses `{host}`.
- Plain HTTP is accepted only to this computer. Another machine is reached by an `https://` address or through an SSH tunnel, and SSH checks the host key against your known hosts file.
- Discovery looks at this computer's loopback address, an address you type, or a paired machine's own loopback address. It never scans a network.
- Page crawling refuses private and local addresses unless you set `WORKBENCH_WEB_PRIVATE=1` before you start Workbench. The check happens before the request; redirects and later name lookups are handled inside the Firecrawl containers.
- The agents, package managers, Docker and MCP servers are programs with their own network use. Workbench switches off the telemetry it knows how to switch off for the tools it starts, such as Claude Code's non-essential traffic and opencode's update check in sessions with a Workbench model source; it cannot vouch for the rest.

## The desktop window

The window is an Electron renderer with `contextIsolation`, the renderer sandbox and no Node.js integration. It cannot navigate away from its own page or open new windows. Its Content-Security-Policy loads scripts only from the app itself and blocks every network request (`connect-src 'none'`), so the window cannot reach the internet or load remote code.

The window talks to Workbench's Python backend through one channel, and the main process passes on only the actions listed in `desktop/actions.json`. The spell checker is switched off on Windows and Linux, where it would download dictionaries.

## Docker sandbox

The sandbox runs OMP in a Docker container as the non-root user `node`, with its own volume for `/workspace`. Your project folders, your home folder and the Docker socket are not mounted. The container runs with `no-new-privileges`, at most 256 processes and 3 GB of memory, and can reach the network. It reaches the local model through an SSH tunnel whose container end is published on `127.0.0.1` only. [Sessions and terminals](sessions.md#isolated-workspaces) has the details.

## Removing data

[Uninstall](troubleshooting.md#uninstall) lists every place Workbench writes, including the vault entries, OMP profiles, Docker objects and SSH machines, and how to remove each.

Product names belong to their owners. Agent Workbench is not affiliated with or endorsed by them.
