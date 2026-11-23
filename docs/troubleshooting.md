# Troubleshooting

This guide starts with Doctor, then has one section for each Doctor check with its fix, the messages you can meet when a model, an agent or an SSH machine does not start, where the logs are, and how to remove Agent Workbench completely.

## Start with Doctor

For 0.5.0, `workbench doctor` was run from installed kits on Windows 11 and Fedora 44. The Doctor page in the desktop window was not run.

Doctor checks this computer and gives one sentence for each problem it finds, saying what to do.

- In the app: **Advanced → Setup**, then **Run Doctor**. Where a fix is a command, **Copy** copies it.
- After a script install: `workbench doctor`.
- From a kit folder: `sh workbench.sh cli.py doctor` on Linux and macOS, `Run.cmd cli.py doctor` on Windows.

On the command line you can name the checks to run, for example `workbench doctor gpu ports`, and add `--json` for machine-readable output. `--active` also tests on macOS whether Workbench may control Terminal, which can show a permission prompt.

Each row has one of four statuses. The app shows the words in brackets.

| Status | Meaning |
| --- | --- |
| ok (Passed) | Nothing to do |
| warn (Warning) | Workbench works, but something will not, or could go wrong; the fix says what to change |
| fail (Failed) | Something Workbench needs is missing or broken; follow the fix |
| skip (Skipped) | The check does not apply to this system, or a tool is not installed and is optional |

`workbench doctor` exits with status 1 when any check failed. When a check itself cannot run, its row says "This check could not run" with the reason; send that line with the Doctor output when you report the problem.

## Python

Doctor check: `python`

Checks the Python that runs Workbench. Each kit includes a suitable Python, so this concerns source checkouts and the source archive, where the launchers accept Python 3.12, 3.13 and 3.14.

- "Python … is too old." Fix: "Install Python 3.12 or newer and start Agent Workbench again."
- On a Mac, "Python is running under Rosetta (x86_64)." Fix: "Start Agent Workbench from a native arm64 terminal so the Metal engine and the desktop runtime can run." Check that Terminal is not set to open using Rosetta.

## Python packages

Doctor check: `packages`

Checks that every Python package Workbench needs is installed: requests, psutil, filelock, keyring, mcp, huggingface_hub, PyYAML, ddgs, and ptyprocess (pywinpty on Windows).

- "Missing: …" Fix: a command to run, of the form `Run: <python> -m pip install --require-hashes --only-binary :all: -r <app folder>/requirements.lock` ("Run in Command Prompt" on Windows). Copy it from Doctor; it names the exact paths.

The launchers install the same packages on the next start when the environment is incomplete, so starting Workbench again usually repairs this too.

## Data folder

Doctor check: `data-dir`

Checks that the data folder exists, is writable, is private to your account and has room for models.

- "… is missing or not writable." Fix: "Make that folder writable, or set WORKBENCH_DATA to a folder you own."
- "Other accounts on this computer can read …" Fix: `chmod 700` on the folder, with the exact command in the row.
- On a Mac, "… is inside a folder that iCloud Drive can sync, including keys and model files." Fix: "Move Agent Workbench out of Desktop and Documents, or set WORKBENCH_DATA to "~/Library/Application Support/Agent Workbench"."
- "Only … GiB free at …" (under 5 GiB). Fix: "Free some disk space before downloading models."

## Credential vault

Doctor check: `vault`

Checks that a system credential vault is available for saved API keys. Doctor does not open the vault, so it does not show an unlock prompt.

- "No secure credential vault is available (…)." Fix:
  - macOS: "Unlock the login keychain in Keychain Access, then restart Agent Workbench."
  - Windows: "Sign in with a regular Windows account so Credential Manager is available, then restart Agent Workbench."
  - Linux: "Install and unlock GNOME Keyring or KWallet, then restart Agent Workbench; until then pass API keys as environment variables."
- "The keyring package is missing." Fix: the package command from [Python packages](#python-packages).

Without a vault, a key you save is kept in memory until Workbench closes, and values for project MCP servers are not saved at all. [Model sources](model-sources.md#where-keys-go) explains the environment variables you can use instead.

## Local engine

Doctor check: `engine`

Checks that the managed llama.cpp engine is installed and starts.

- "The managed llama.cpp engine is not installed." Fix: "Install it in Advanced > Setup > llama.cpp / Strata > Install, or attach a server that is already running." You need it only to run a model on this computer.
- "The engine at … does not start." Fix on macOS: "Run: xattr -dr com.apple.quarantine … and retry, or reinstall it in Advanced > Setup.", with the engine's folder in place of the dots. Elsewhere: "Reinstall it in Advanced > Setup > llama.cpp / Strata > Install."

## GPU

Doctor check: `gpu`

Reports the GPU and the engine build Workbench picks for it, with the reason.

- On Apple Silicon: the chip and its unified memory (Metal). Nothing to fix.
- "nvidia-smi cannot reach the NVIDIA driver." Fix: "Reinstall or update the NVIDIA driver, then restart the computer."
- "Only a software Vulkan device was found." Fix: "Install your GPU vendor's driver, or keep using the CPU build, a hosted API or an attached server."
- "No GPU was detected, so local models run on the CPU." Fix: "Install your GPU driver (on Linux also vulkan-tools for AMD or Intel), or use a hosted API or an attached server."

After you fix a driver, install the engine again so Workbench picks the matching build.

## Agent CLIs and Git

Doctor check: `clis`

One row per agent (OMP, Pi, Claude Code, Codex, Hermes, opencode), one summary row and one row for Git.

- An agent "Not installed." (Skipped). Fix: "Install it in Advanced > Setup if you want to use it."
- "Installed at …, outside the PATH of your terminals." Workbench can start it, but your own terminals cannot. Fix: the row's hint, or "Add "…" to your PATH to use it in your own terminals.", naming the agent's folder.
- "No agent CLI is installed." (Failed). Fix: "Install OMP in Advanced > Setup > OMP > Install." Any of the six agents will do.
- "Git was not found." Fix: "Install Git, which every agent uses to inspect and change projects."

## Ports

Doctor check: `ports`

Two rows: the local model's port (10532 unless you set `WORKBENCH_PORT`) and the page-crawling port 3002.

- Model port: "Port … is used by another program." Fix: "Stop that program, or set WORKBENCH_PORT to a free port before starting Agent Workbench." If that program is a model server you want to use, attach it instead; **Launch** offers that.
- Page-crawling port: "Port 3002 is used by another program." Fix: "Stop that program before you turn on page crawling."

## Docker

Doctor check: `docker`

Docker is needed only for page crawling, the sandbox and kit servers started with `docker`.

- "Not installed; only page crawling and the sandbox need it." (Skipped.)
- "Docker is installed but not usable." with one of these fixes:
  - "Docker Compose v2 is missing; install the Docker Compose plugin with Advanced > Setup > Docker > Install, then retry."
  - "Your account cannot use the Docker socket; run "sudo usermod -aG docker $USER", sign out and back in, then retry (that group has root-level access)."
  - "Docker is installed but not running; start Docker Desktop (on Linux: sudo systemctl start docker), then retry."
- On Windows without Docker, Setup can run Docker inside a WSL distribution named `AgentWorkbench-Web`; the row then says so.

## Terminal emulator

Doctor check: `terminal`

Linux only. Agents in their own window and guided installs open in a terminal emulator.

- "No terminal emulator was found." Fix: "Install xterm so agent windows and guided installs can open." Until then, use the terminal dock.

## Desktop sandbox

Doctor check: `sandbox`

Linux only. Some distributions, Ubuntu among them, restrict the user namespaces the desktop window uses for its sandbox.

- "This system restricts user namespaces and the sandbox helper is not set up." Fix: a command to run. When Workbench can set it up with `setup-linux.sh`, the command is `sh <app folder>/setup-linux.sh --sandbox`, with `WORKBENCH_DATA=<data folder>` in front when your data folder is not the kit's own; copy it from Doctor rather than typing it. Otherwise it is the pair of `sudo chown root:root` and `sudo chmod 4755` commands for the runtime's `chrome-sandbox` helper.
- "This system restricts user namespaces; the desktop runtime has not been downloaded yet." Fix: "Start Agent Workbench once, then run Doctor again for the exact command."

`setup-linux.sh --sandbox` explains what it changes and asks before it uses sudo. It installs a small AppArmor profile for the desktop runtime in your data folder where AppArmor is in use, or sets up the sandbox helper otherwise. Until the sandbox is set up, the classic interface opens instead of the desktop window.

## macOS quarantine

Doctor check: `quarantine`

macOS only. A browser download marks files with a quarantine attribute, and macOS then refuses to run the bundled Python, the engine or the desktop runtime.

- "macOS quarantine is set on: …" Fix: "Run: xattr -dr com.apple.quarantine … and start Agent Workbench again.", naming the folders to clear. **Copy** copies the command.

The install script and `Launch Workbench.command` remove the mark themselves, which is why the by-hand steps start the launcher from Terminal ([Getting started](getting-started.md#macos-and-linux)).

## Terminal automation

Doctor check: `automation`

macOS only. To open an agent in its own window, Workbench asks Terminal to run it, and macOS asks you once to allow that.

- By default the check does not trigger the prompt and is Skipped, with the fix "If agent windows do not open, allow Terminal under System Settings > Privacy & Security > Automation."
- **Check Terminal permission** in the app, or `workbench doctor automation --active`, tests it:
  - "macOS blocked control of Terminal." Fix: "Allow Terminal under System Settings > Privacy & Security > Automation for the app that started Agent Workbench, then retry."
  - "macOS is waiting for your answer to its permission prompt." Fix: "Choose Allow in the macOS dialog, then run this check again."
  - "Terminal is not running, so the permission was not tested." Fix: "Open Terminal, then run this check again."

## SSH client

Doctor check: `ssh`

SSH machines need the OpenSSH client.

- "ssh was not found; SSH machines are unavailable." or "ssh-keygen was not found; pairing a machine needs it." Fix:
  - Windows: "Add the OpenSSH Client under Settings > System > Optional features, then restart Agent Workbench."
  - Linux: "Install openssh-client with your package manager."
  - macOS: "Reinstall the macOS command line tools with: xcode-select --install"

## Proxy and loopback

Doctor check: `proxy`

Workbench talks to the local model and local servers on `127.0.0.1`. A proxy that also catches loopback addresses breaks that for the agents it starts.

- "A proxy is set and loopback addresses are not excluded." Fix: "Add 127.0.0.1,localhost to NO_PROXY before starting Agent Workbench."

## The model does not start

These are the messages of the local model, with what to do. The model server writes its output to `serve.log` in the data folder; Workbench hides the local key when it shows lines from it.

- "Choose a model in Agent Workbench first." (command line) or "Select a model file in the library." Choose a model in the setup guide or in **Advanced → Model library**.
- "Port 10532 already has a listener; stop that program, attach to it as a connection, or set WORKBENCH_PORT to a free port and restart Agent Workbench." **Launch** offers to attach the server on that port.
- "The model server exited while loading (…); open … serve.log for the full log." The engine stopped; the last log line is in the brackets. A model too large for the memory, or a file the engine cannot read, are the usual causes. Choose a smaller model or context.
- "The model was not ready after … seconds and was stopped; choose a smaller model or context, or set a longer start_timeout in … settings.json, then start again."
- "The model server is running but not answering; press Stop server, then start it again." Choose **Stop** in the status bar, then start again.
- "Stop the active model before changing its serving settings." Another model, or the same model with other settings, is serving. The status bar then reads "A different model is serving"; choose **Use it** to switch this folder to the running model, or stop it first.
- "The engine at … could not be started (…); reinstall it: …" or "The llama.cpp engine is not installed: …" Install the engine again in **Advanced → Setup**.
- On a Mac: "Choose a Metal-compatible GGUF with llama.cpp, or attach a running endpoint." Strata, vLLM and Flash preset do not run on macOS.
- A download that stopped: "The download was cancelled. The part already downloaded is kept." Choose **Resume download**. A file that does not match its pinned SHA-256 is not used.

## The agent does not start

These are the messages you can meet when you launch an agent, with what to do.

- "… is missing. Open Advanced → Setup to install it." Install the agent, then **Recheck installations**.
- "… needs the … API, which … does not offer; choose …, or another source." The source cannot serve this agent. Choose one of the agents the sentence names, or another source ([Model sources](model-sources.md#three-apis-six-agents)).
- "Hermes signs in with its own account; choose Agent account as the source to use it."
- "This folder defines tools that can run programs. Open Project setup, review them and allow them, then launch again." See [Consent](project-setup.md#consent), or run `workbench trust --workspace <folder>`.
- "Set … or save a key for …." Save the provider's key in **Connections**, or set the variable the sentence names.
- "Select or type the backend model ID." Enter the model on **Launch**, or choose **Browse models**.
- "Reconnect this endpoint in Connections or Machines." The saved connection is gone or incomplete; attach the server again.
- "This model ID cannot be used. Pick the model from the list or retype its ID."
- On Windows: "… is installed as a Windows batch file, which cannot safely receive … in the model ID, prompt or folder path. Remove it, or reinstall … with its native installer." Workbench refuses to pass such characters to a `.cmd` or `.bat` file, because Windows would interpret them.
- opencode: "opencode cannot use a model ID that contains {, because it reads that as a reference to a file. Choose another agent for this model." and a matching sentence for a key that contains a quote, backslash, brace or control character.
- On a Mac: "macOS blocked Agent Workbench from controlling Terminal; allow it in System Settings > Privacy & Security > Automation, then launch again." or "Terminal did not start the agent within 120 seconds; launch again, or start the agent from the Workbench terminal dock."
- On Linux: "No graphical terminal is available in this session; run cli.py agent --here in this terminal, or start the agent from the Workbench terminal dock." or "Install a terminal emulator such as xterm, or start the agent from the Workbench terminal dock."
- "Install uv in Advanced → Setup, then enable the Blender or Windows desktop tool again."
- "Firecrawl did not become ready on port 3002." or "Install Docker with the Compose plugin and start it before enabling Firecrawl." Page crawling needs Docker; turn it off on **Launch** or fix Docker ([Docker](#docker)).

### Claude Code or Codex stops with HTTP 500

If Claude Code or Codex fails with HTTP 500 "System message must be at the beginning.", the model's chat template rejects the system or developer message these agents send after the first user message.

- With a model Workbench serves, Workbench serves a corrected copy of the template when the GGUF file's own template contains exactly that rejection, as the Qwen files in the catalog do. If you still see the error, the model file has a template Workbench does not recognise; use OMP, Pi or opencode with it, or another model.
- With a server you attached, such as Ollama or LM Studio, that server's own template handling decides, and Workbench does not change it. Claude Code and Codex completed a task against one model in Ollama 0.40.8 for 0.4.0; other models and servers were not run. Use another agent or model, or a server that serves that model with a template that accepts a later system message.

### Listing models fails

**Browse models** and the model list on a connection can stop with:

- "… rejected the saved API key. Save a valid key for this connection." or "… needs an API key. Save its key, then list models again."
- "… did not return a model list. Type the model ID instead." (hosted) or "There is no model list at …/models. Check the address; most servers use a path that ends in /v1."
- "… redirected the request. Enter the final API address instead."
- "The secure connection to … failed. Check the address and the server's certificate."
- "… did not answer in time. Check that it is running, then list models again."
- "The SSH tunnel for this connection is not open. Connect the machine under Machines, then list models again."

### An unexpected problem

"Agent Workbench hit an unexpected problem with this request. Try again; if it repeats, run Doctor in Advanced → Setup and check bridge.log in your data folder." `bridge.log` records start-up lines and errors of Workbench's backend; it does not log the requests the window sends.

## An SSH machine does not connect

Workbench turns SSH's own errors into one sentence each:

- "The host key of … has changed; if that machine was reinstalled, remove its old line from your known_hosts file and pair again." Workbench never accepts a changed host key. Check why it changed before you remove the line.
- "This computer has not accepted the host key of … yet; use Pair through SSH once, then retry."
- "… refused the login; pair this machine again, then retry."
- "… closed the login after too many keys were offered; add IdentitiesOnly yes for this host to your SSH config, then retry."
- "The name … could not be found; check the SSH alias or host name, then retry."
- "… refused the SSH connection; check that its SSH server is running, then retry."
- "… did not answer; check that it is switched on and reachable from this network, then retry."
- "SSH could not open the local forward for …; close other SSH sessions that forward ports for this host, then retry."
- "python3 was not found on …; install Python 3.8 or newer there, then retry." On a Mac: "python3 is not installed on …; run xcode-select --install on that Mac, then retry."
- "OpenSSH was not found on this computer; install the OpenSSH client, then retry." See [SSH client](#ssh-client).

For the Workbench-managed model on the machine:

- "The managed model needs a Linux x86_64 machine; attach a server that is already running on this machine instead."
- "This machine needs 13 GiB of free disk space in the home folder; free some space, then retry."
- "The download of … stopped (curl exit …); check this machine's internet connection, then run the install again." or "The download did not match its pinned checksum; run the install again."
- "Port 10542 on this machine is already used by another program; stop that program, then retry."
- "The remote model did not start within 3 minutes; use Check connection, then retry."
- "Another Workbench operation is still running on this machine; wait for it to finish, then retry."

When a tunnel is down, **Before you launch** says "The SSH tunnel to … is down." with **Reconnect**.

## Logs and data

| File in the data folder | What it holds |
| --- | --- |
| `serve.log` | Output of the local model server |
| `engine.log` | Output of the Strata and Flash preset engines |
| `setup.log` | Output of installs from **Advanced → Setup**; **Open setup log** opens it |
| `bridge.log` | Start-up lines and errors of the backend, not the requests themselves; private to your account, and moved to `bridge.log.1` when it passes 1 MiB |
| `traces/` | Recorded traces from **Advanced → Trace**, which can contain your project's content |

[Security and your data](security-and-data.md#where-workbench-keeps-data) lists every file and folder and where the data folder is on each system.

## Uninstall

Removing the app and removing your data are separate steps, so an upgrade or a reinstall keeps your settings.

### The app and the command

With a script install on macOS or Linux, run the uninstall line the installer printed, normally:

```sh
sh ~/.local/lib/agent-workbench/install.sh --uninstall
```

It removes the app folder and the `workbench` command and keeps your data. If you installed with `WORKBENCH_INSTALL_DIR` or `WORKBENCH_BIN_DIR` set, set them to the same folders for the uninstall.

On Windows (run for 0.5.0 on a copy installed into a temporary folder, without the Start menu entry):

```powershell
powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Agent Workbench\app\install.ps1" -Uninstall
```

It removes the app, the `workbench` command and the Start menu entry, takes the command folder out of your user `PATH`, and keeps your data.

With a kit you unpacked by hand, delete the `Agent-Workbench` folder. Its data is in `.local` inside it, unless you set `WORKBENCH_DATA`.

### Your data

Delete the data folder:

| System | Data folder of a script install |
| --- | --- |
| macOS | `~/Library/Application Support/Agent Workbench` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/agent-workbench` |
| Windows | `%LOCALAPPDATA%\Agent Workbench\data` |

### Other things Workbench created

Check each of these; they are outside the app and data folders.

- OMP profiles Workbench generated: the folders `local-agent-workbench*` and `workbench-*` under `~/.omp/profiles`.
- Saved keys in the system credential vault, service name `Agent Workbench`. On macOS, search for "Agent Workbench" in Keychain Access and delete the entries. On Windows, open Credential Manager, then Windows Credentials. On Linux, use Seahorse ("Passwords and Keys"), or `secret-tool search --all service "Agent Workbench"` to list them and `secret-tool clear service "Agent Workbench"` to delete them.
- On Windows, the WSL distribution `AgentWorkbench-Web`, if Setup created it for page crawling: `wsl --unregister AgentWorkbench-Web`.
- Docker: sandbox containers named `workbench-box-<name>`, their volumes `workbench-box-<name>-work`, the image `agent-workbench-sandbox:1`, and the page-crawling stack, a Compose project named `agent-workbench-web`. Before you delete the app, run `docker compose -f firecrawl.compose.yml down --volumes` in the app folder to remove the stack.
- On each SSH machine where you installed the managed model: first **Remove the managed runtime** from **Machines → Pairing and removal**, which deletes `~/.local/share/agent-workbench-node` there; then revoke the Workbench key, which deletes its line from `~/.ssh/authorized_keys` there. If you already removed Workbench, delete that folder and the key line, whose comment starts with `agent-workbench-`, yourself. Remote tmux sessions run on a tmux server named `agent-workbench`; `tmux -L agent-workbench kill-server` ends them.
- The vLLM environment, if you installed vLLM: `~/.local/share/agent-workbench/vllm` (or `WORKBENCH_VLLM_VENV`) and the link `~/.local/bin/vllm`, on Linux or inside WSL.
- If you let `install.sh` add the command folder to your `PATH`, the line under `# Added by the Agent Workbench installer` in `~/.zshrc`, `~/.bashrc` or `~/.bash_profile`.
- Agents and tools that **Advanced → Setup** installed (for example with npm, Homebrew or winget) stay installed; remove them with the same tool.

Files in your project folders are yours, and no uninstall step touches them: `.agent-workbench/`, `AGENTS.md`, a `CLAUDE.md` pointer, skills under `.agents/skills` and `.claude/skills`, and MCP files you exported. Delete them yourself if you no longer want them.

## Report a problem

Open an issue at [github.com/jamesarslan/local-agent-workbench/issues](https://github.com/jamesarslan/local-agent-workbench/issues) and attach:

- the Agent Workbench version (0.5.0) and your system: name, version and processor;
- the Doctor output (`workbench doctor`, or the rows from **Advanced → Setup**);
- the steps that lead to the problem, and the message you see.

Never attach API keys, the `api-key.txt` file, session histories or traces that hold private data, or other people's data. Report a security problem privately, as the [security policy](../SECURITY.md#reporting-a-vulnerability) describes, not in an issue.

Product names belong to their owners. Agent Workbench is not affiliated with or endorsed by them.
