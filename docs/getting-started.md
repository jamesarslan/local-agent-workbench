# Getting started

This guide takes you from a download to a coding agent running in one of your project folders: what you need, how to install, each step of the setup guide, and three short routes for common situations.

## Before you start

Agent Workbench has kits for three systems:

- Windows x64.
- Linux x64 with glibc. A system that uses musl, such as Alpine, needs the source archive.
- macOS on Apple Silicon (preview). Intel Macs are not supported; the install script stops on one and points you to the source archive.

You do not need a GPU. Workbench picks the build of its local engine from the hardware it finds: CUDA for an NVIDIA GPU, Vulkan for AMD or Intel, Metal on Apple Silicon, and the CPU build when it finds no supported GPU. Without a GPU you can also attach a model server you already run, use a machine over SSH, or use a hosted API.

Each kit includes Python. You need your own Python (3.12, 3.13 or 3.14) only when you run Workbench from the source archive or a checkout.

The first start needs a network connection. It downloads the desktop runtime (Electron) from github.com and the Python packages from pypi.org, and checks both against SHA-256 values pinned in the repository. A model is a separate download that you choose in the setup guide; the guide shows its size before anything starts. The model files in the catalog range from 2.6 GiB to 77.9 GiB. Doctor warns when the data folder has less than 5 GiB free.

To install the agents from inside Workbench you need their usual tools: Pi, Codex and opencode install with npm, so Node.js must be present. Where Workbench has no installer for a tool on your system, its **Install** button opens the tool's own installation page instead.

## Install

The [README](../README.md#install) has the same commands with the status of each system for this release.

The downloads are not code-signed or notarised. Your system may warn you before it runs them. Check the SHA-256 of what you downloaded against `SHA256SUMS` from the same release before you run it. The install scripts do that check for you and stop on a mismatch.

### macOS and Linux

Parts of these steps were run for 0.5.0: on Fedora 44 the install script installed a kit built by the release builder from a local folder, checked its SHA-256, ran the test suite and Doctor through the installed `workbench` command and uninstalled it, keeping the data folder. For 0.4.0 the same was run on macOS, where a changed archive and a wrong checksum were refused. Downloading the script and the kits from GitHub was not run, because no release is published yet.

With the install script:

```sh
curl -fsSL https://github.com/jamesarslan/local-agent-workbench/releases/latest/download/install.sh | sh
```

To read the script before it runs:

```sh
curl -fsSLO https://github.com/jamesarslan/local-agent-workbench/releases/latest/download/install.sh
less install.sh
sh install.sh
```

The script downloads the kit for your system and `SHA256SUMS`, stops unless they match, and installs under your user account without sudo:

| What | Where |
| --- | --- |
| The app | `~/.local/lib/agent-workbench` (set `WORKBENCH_INSTALL_DIR` to change it) |
| The `workbench` command | `~/.local/bin/workbench` (set `WORKBENCH_BIN_DIR` to change the folder) |
| Your data | `~/Library/Application Support/Agent Workbench` on macOS, `${XDG_DATA_HOME:-~/.local/share}/agent-workbench` on Linux |

When `~/.local/bin` is not on your `PATH` and your shell is zsh or bash, the script asks before it adds one line to your shell profile; otherwise it prints the line for you to add. On macOS it also removes the download mark (quarantine) from the files it unpacks. On Linux, run `sh ~/.local/lib/agent-workbench/setup-linux.sh` once: it lists the desktop libraries it needs and asks before it uses sudo. Then start the app with `workbench`.

By hand on Linux: download `agent-workbench-linux-x64.tar.gz` and `SHA256SUMS` from the [0.5.0 release](https://github.com/jamesarslan/local-agent-workbench/releases/tag/v0.5.0), then in the folder that holds them:

```sh
sha256sum -c SHA256SUMS --ignore-missing
tar -xzf agent-workbench-linux-x64.tar.gz
sh Agent-Workbench/setup-linux.sh
```

After that, start it with `sh Agent-Workbench/workbench.sh`.

By hand on macOS: download `agent-workbench-macos-arm64.tar.gz` and `SHA256SUMS`, then in Terminal:

```sh
cd ~/Downloads
shasum -a 256 -c SHA256SUMS --ignore-missing
tar -xzf agent-workbench-macos-arm64.tar.gz
sh Agent-Workbench/"Launch Workbench.command"
```

The launcher removes the quarantine mark from its own folder so the bundled Python can start, which is why you start it from Terminal. If you double-click `Launch Workbench.command` in Finder instead, macOS refuses it because the kit is not notarised. To allow it:

1. Choose Done in the message.
2. Open System Settings, then Privacy & Security.
3. Under Security, find the message about `Launch Workbench.command` and choose Open Anyway.
4. Confirm with your password.

Apple describes these steps in [Open a Mac app from an unknown developer](https://support.apple.com/guide/mac-help/open-a-mac-app-from-an-unknown-developer-mh40616/mac). For 0.4.0 the launcher was run on a folder marked with the quarantine attribute by hand, and it removed the mark; a real browser download and the Gatekeeper dialog were not tried.

If you move or rename the `Agent-Workbench` folder after the first start, the launcher notices that its Python environment was moved and rebuilds it on the next start.

### Windows

Parts of these steps were run for 0.5.0 on Windows 11: `install.ps1` installed a kit built by the release builder from a local folder into a temporary folder, checked its SHA-256, ran the test suite and Doctor through the installed `workbench` command and uninstalled it. Downloading from GitHub, the `PATH` change and the Start menu entry were not run.

With the install script, in PowerShell:

```powershell
irm https://github.com/jamesarslan/local-agent-workbench/releases/latest/download/install.ps1 | iex
```

To read the script before it runs:

```powershell
irm https://github.com/jamesarslan/local-agent-workbench/releases/latest/download/install.ps1 -OutFile install.ps1
notepad install.ps1
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The script downloads the Windows kit and `SHA256SUMS`, stops unless they match, installs under `%LOCALAPPDATA%\Agent Workbench` without administrator rights (the app in `app`, your data in `data`), adds a `workbench` command and a Start menu entry, and puts the command folder first in your user `PATH`.

By hand: download `agent-workbench-windows-x64.zip` and `SHA256SUMS` from the [0.5.0 release](https://github.com/jamesarslan/local-agent-workbench/releases/tag/v0.5.0). In PowerShell, in the folder that holds them, print the hash and compare it with the line for that file in `SHA256SUMS`, then unblock and extract the archive:

```powershell
Get-FileHash -Algorithm SHA256 agent-workbench-windows-x64.zip
Unblock-File agent-workbench-windows-x64.zip
Expand-Archive agent-workbench-windows-x64.zip -DestinationPath .
```

Then double-click `Launch Workbench.cmd` inside `Agent-Workbench`.

If the archive was extracted without `Unblock-File`, Windows can show "Windows protected your PC" or a security warning before it runs the launcher:

1. Check that the file named is `Launch Workbench.cmd` from the folder you extracted.
2. Choose More info and then Run anyway, or Run in the security warning.

With Smart App Control switched on, Windows may block the unsigned Python inside the kit however it arrives. Then use `agent-workbench-source.tar.gz` with Python 3.12, 3.13 or 3.14 from python.org.

### What the first start does

The first start downloads the desktop runtime into the data folder and builds a Python environment from the hash-pinned `requirements.lock`. A tampered hash stops it with a message. On Linux, when the system restricts the sandbox the desktop window uses, `setup-linux.sh` explains the one change it needs and asks before it makes it.

The data folder is the one in the table above for a script install, `%LOCALAPPDATA%\Agent Workbench\data` on Windows, and a `.local` folder inside `Agent-Workbench` when you start a kit by hand. [Security and your data](security-and-data.md#where-workbench-keeps-data) lists what goes into it.

## Guided setup

These steps were not run for 0.5.0 in the finished desktop window. The pieces behind them were run from the command line; see [Tested configurations](../README.md#tested-configurations).

The **Setup guide** opens on the first start. It has five steps in the sidebar: This computer, Model, Agent, Project and Ready. A step you cannot open yet says which step to finish first.

Every step can be left. On a first run the guide shows **Skip setup**, which takes you to **Launch** and lists there what is still missing. Later the same place shows **Close guide** and keeps your choices. To open the guide again, choose **Setup guide** at the bottom of the sidebar (it reads **Continue setup** until the first run is finished or skipped), or **Open setup guide** in **Advanced → Setup**.

### This computer

Workbench looks at this computer and shows what it found:

- Hardware: the GPU, or the processor and memory when no supported GPU was found.
- Memory for a model: how much of this computer's memory a local model can use. **How this is set** gives the rule behind the number.
- Model servers running: Ollama, LM Studio and other servers that answered on this computer, with the port and the number of models. When LM Studio is open but its server is off, a line says so.
- Agents installed, with their versions.
- Engine for local models: installed, or the size of the download.
- Model files on this computer and saved connections, when there are any.

Nothing is downloaded or installed in this step, and nothing is written to your project. Choose **Check again** after you start a server or install an agent, then **Continue**.

### Model

The step is titled "Choose where the model runs". Pick one of six options; the open card holds its controls, and the button at the bottom changes with your choice.

- **Use a server that is already running.** Lists every model server that answered on this computer, and Workbench's own local model when it is serving. Pick a server and a model; **Which agents can use it** says which agents work with that server and why the others do not. When the server asks for a key, an API key field appears. **Scan again** looks again. **Attach and continue** saves the server as a connection.
- **Download a model for this computer.** Shows the model recommended for this computer with a Measured or Suggested label, **Other models that fit**, and **Already on this computer**. **How this was sized** shows the memory arithmetic. **Download and continue** downloads the model and, when it is missing, the engine. You can go on while they download; a cancelled download keeps what it fetched and offers **Resume download**.
- **Use a hosted API.** Choose the provider, paste the key and choose **Save key**, then enter a model or choose **Browse models**. **Get an API key ↗** opens the provider's key page for OpenAI, Groq, DeepSeek and OpenRouter, and **Sign in with OpenRouter ↗** signs in through your browser. **Save and continue** saves the connection.
- **Use a machine over SSH.** Type an SSH alias or `user@host` and choose **Check connection**. Workbench lists the model servers running on that machine; pick one and choose **Attach and continue**. The machine must accept an SSH login from this computer without a password prompt, with your own key or with the key Workbench adds when you pair it in **Machines**; [Model sources](model-sources.md#a-gpu-machine-over-ssh) explains pairing.
- **Type a server address.** For a server on this computer that the scan did not find. Type its address and choose **Check address**. An address on another computer is refused for plain HTTP; the step offers **Use a machine over SSH instead**.
- **Use an agent's own sign-in.** The agent uses its own account and picks its own model. Workbench does not see or store that login.

[Model sources](model-sources.md) covers each option in depth.

### Agent

The step is titled "Choose an agent". Each card shows one of OMP, Pi, Claude Code, Codex, Hermes and opencode, with "Installed" and its version, or "Not installed" and an **Install** button.

An agent that cannot work with the model you chose is switched off, and the card says why in one sentence. Each agent speaks one API, and Workbench does not translate between them. **Change the model** takes you back one step. After you install an agent outside Workbench, choose **Recheck installations**.

With an agent's own sign-in, the chosen card offers its sign-in. OMP and Hermes also ask which **Subscription** to use. The sign-in opens in a terminal window; continue there.

### Project

The step is titled "Choose a project folder". Type a path and press Enter, choose **Browse…**, pick a folder under **Recent**, or choose **Use a scratch folder** to work in a `workspace` folder inside Workbench's data folder.

Workbench then looks at the folder and suggests a starter kit, with the files that led to the suggestion. Choose another kit or "No kit" in the **Kit** list. **Preview what it adds** shows the `AGENTS.md` text, the skills and the MCP servers the kit would add. **Apply starter kit…** opens **Review changes**, where every file is listed as create, update or skip, and nothing is written until you choose **Write files**. A file you already have is kept. [Project setup](project-setup.md#starter-kits) lists the kits.

**Continue without a kit** moves on without writing anything.

### Ready

The step is titled "Ready", or "Almost ready" while something is missing. It lists the project folder, the model, the agent, and the instructions and tools, each with **Change**. Under **Open in**, choose "Terminal dock, inside this window" or "New terminal window".

The main button carries the agent's name, for example "Start Claude Code". **Finish without starting** closes the guide and keeps the setup; you start it later from **Launch**.

## Three short routes

### You already run Ollama or LM Studio

These steps were not run for 0.5.0 in the desktop window. For 0.4.0, attaching Ollama 0.40.8 and starting OMP, Claude Code, Codex and opencode against it was run through the same code from the command line on a Mac.

1. Start Ollama, or in LM Studio open Developer and start the server. Load the model you want.
2. Open the setup guide. Under "Model servers running" you see the server and its port.
3. Choose **Continue**, then **Use a server that is already running**. Pick the server and the model.
4. Open **Which agents can use it** and note the agents it names. Choose **Attach and continue**.
5. If Workbench warns that the context is small or was not reported, raise it in the server; see [Model sources](model-sources.md#context-size).
6. Choose an installed agent, then a project folder, then start the agent on the Ready step.

If you use Claude Code, Codex or opencode with Ollama and want nothing else, [`ollama launch`](https://docs.ollama.com/cli) starts it against an Ollama model in one command, with no extra app.

### You have an API key and no GPU

These steps were not run for 0.5.0.

1. In the setup guide's Model step, choose **Use a hosted API**.
2. Choose the provider. Paste the key and choose **Save key**. The key goes to your system's credential vault; without a vault it is kept in memory until Workbench closes.
3. Enter a model, or choose **Browse models** and pick one. Choose **Save and continue**.
4. Choose an agent that the provider can serve. Claude Code needs the Anthropic Messages API and Codex needs the OpenAI Responses API; [Model sources](model-sources.md#hosted-providers) has the table.
5. Choose the project folder and start the agent.

Everything the agent sends, which can include your files and images, goes to the provider you chose.

### A local model on this computer

These steps were not run for 0.5.0 in the desktop window. On a Mac, installing the engine, starting and stopping a model, and starting OMP, Claude Code, Codex and opencode against it were run from the command line.

1. In the Model step, choose **Download a model for this computer**.
2. Read the recommendation and **How this was sized**. Pick another model under **Other models that fit** if you prefer.
3. Choose **Download and continue**. The model and the engine download in the background, each checked against its SHA-256. You can go on.
4. Choose an agent and a project folder.
5. On the Ready step, start the agent. Workbench starts the local model first if it is not running, then opens the agent.

The local model listens on `127.0.0.1` only and needs a key that Workbench generates. The status bar shows it as "Local model" with **Start** and **Stop**.

## Start again later

**Launch** is the page you use after setup. From top to bottom it holds the project folder, the **Model source**, the model, the agent cards, **Tools for this session**, **Before you launch** (what is ready and what is missing, each with a fix), **Open in**, the **Launch** button and **Save for this folder**, and **Recent sessions here**.

Workbench remembers the source, connection, model, agent, tools and starter kit for each folder on this computer. It saves them when you launch an agent in the folder and when you choose **Save for this folder**, and restores them when you select the folder again. A folder you have not used before starts with the choices you used last; a notice says so and offers **Use this setup**.

To switch folders, choose the folder name at the top of the window, or press Ctrl+O (Cmd+O on macOS). Ctrl+Enter (Cmd+Enter) launches with the current choices. [Project setup](project-setup.md#what-is-remembered) explains what is remembered and what travels with the folder.

## If something fails

Open **Advanced → Setup** and choose **Run Doctor**, or run `workbench doctor` in a terminal. Each row says what it checked and, when something is wrong, one sentence on how to fix it. [Troubleshooting](troubleshooting.md) has a section for every check and for the messages you can meet when a model, an agent or a machine does not start.

Product names belong to their owners. Agent Workbench is not affiliated with or endorsed by them.
