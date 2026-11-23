# Changelog

All notable changes to Agent Workbench are listed here. Versions follow semantic versioning.

## [Unreleased]

### Added

- Run summaries. Each agent the app starts in a folder on this computer (terminal dock, resumed session or own window) is kept as a run record in `runs/` in the data folder: one plain JSON file per folder with its newest 100 runs. **Run summary** in the terminal dock, or **Summary…** on **Project setup** → **Recent runs**, shows what ran: the agent and its version, the model source, connection name and API address (scheme, host and port only), the model, context, reasoning effort, engine and weights file, the profile and kit, the MCP servers, the instruction files and skills with their SHA-256, and the folder prompt when the agent received it (Codex and agents on their own account say "No prompt."). **Copy summary** copies it as Markdown and **Copy as JSON** as the full record. The summary ends with "Changed since this run", the instruction files and skills that changed, are gone or are new. **Save as profile** writes the run's choices to `.agent-workbench/profiles.json` for the usual review and apply. No keys, tokens or environment values are recorded ([Run summaries](docs/sessions.md#run-summaries)).
- Settings that travel with the folder. **Save as folder default** on **Launch** writes the current choices to `.agent-workbench/profiles.json` as the folder default, plain JSON with no keys that is safe to commit. On **Project profiles**, **Make default** marks the folder default, which then carries the **Folder default** label, and **Clear default** removes the mark; **Make it the folder default** does the same as you save a profile. On a computer that has no setup for the folder yet, **Launch** shows "This folder has saved settings" with **Review and apply…** and never applies the profile on its own. Connections are matched by name and model files by file name, and the review says what to add when one is missing. **Where this folder's settings live** lists what travels with the folder and what stays on this computer, with **Show in folder** ([Settings that travel with the folder](docs/project-setup.md#settings-that-travel-with-the-folder)).
- Unsloth's llama.cpp build as a third **Engine release** (**Unsloth**, beside **Tested** and **Updated**), pinned to `b12649-mix-c7059d6` by SHA-256 for each platform and installed beside the default build. With Inkling 2 Mini it adds Unsloth's DSpark drafter (`dspark-Inkling-2-Mini-Q8_0.gguf`, checked by SHA-256) when drafting is on; otherwise the model starts without it and says why. Opt-in; **Tested** stays the default ([Inkling 2 Mini with Unsloth's build](docs/model-sources.md)).
- Roblox Studio in the Game development kit. When Roblox Studio is installed, the kit turns on Studio's built-in MCP server with the command Roblox documents for macOS and Windows, detects Roblox and Rojo projects, and adds the `workbench-game-project-for-roblox` skill. A kit server can now give a command per system and name the file that shows the app is installed; Windows user names with spaces work.

### Changed

- The Workbench-managed model on a Linux machine serves Inkling 2 Mini UD-IQ3_XXS with a 16K-token window instead of 8K, the catalog's choice for a 16 GB Vulkan card, and OMP is told the same window. OMP's first request is about 13,400 tokens, so it no longer fails against the 8K window. On a Radeon RX 9070 XT (16 GB) OMP wrote a file through it from a Mac, with 13.3 GiB of the card in use.
- The scan also looks at port 8081, where a second llama.cpp server is often run.
- Windows: the suite passes on Windows 11. The SSH public key sent during pairing ends in LF, the `profiles.json` lock lives in the data folder instead of the project's `.agent-workbench/` folder, npm shims run only when they point into their own `node_modules`, and `workbench doctor` prints ASCII punctuation when the console is not UTF-8.
- Redesigned project site. The first screen has the install command for each system; the page shows one setup per folder, the three setup steps with screenshots, the two features above marked as new in the next release, and a status table built from `docs/verification.json`.

## [0.4.0] - 2026-11-23

### Added

- Setup guide. Five steps lead from a fresh install to a running agent: This computer, Model, Agent, Project and Ready. You can skip it and reopen it from **Setup guide** in the sidebar; your choices are kept.
- Attach a model server that is already running. Workbench looks for Ollama, LM Studio, llama.cpp, vLLM, Jan, KoboldCpp, LocalAI, llama-swap, SGLang and other OpenAI-compatible servers on this computer's own ports, reads the loaded context size where the server reports it, checks which APIs it serves (Chat Completions, Responses, Anthropic Messages) and saves it as a named connection. It does not scan your network. When LM Studio runs with its server off, Workbench says so.
- On a paired SSH machine, Workbench looks for model servers on that machine's own loopback and forwards only the port you choose.
- Hosted presets for Anthropic, Google Gemini, Together, Fireworks, Mistral, xAI, Cerebras and Hugging Face, beside OpenAI, OpenRouter, Groq and DeepSeek.
- opencode as a sixth agent, beside OMP, Pi, Claude Code, Codex and Hermes.
- Per-folder memory. The model source, connection, model, agent, tool switches and kit you last used are restored when you pick the folder again. This record stays on your computer; portable profiles in `.agent-workbench/profiles.json` still travel with the project.
- Project setup. For each agent, Workbench shows which instruction files, skills and MCP servers it will read in the folder, and which files shadow others. A project's own MCP list lives in `.agent-workbench/project.json` without secrets. Every write is shown first as a plan (create, update or skip, with a diff) and only what you confirm is written.
- Eight starter kits: General software, Web front end, Python and data, 3D scenes and assets, Research and writing, DevOps and infrastructure, Game development, and Blank. A kit holds an `AGENTS.md` template, up to five skills and optional MCP servers. Workbench suggests one from the files in the folder. Kit text is original and MIT-licensed.
- Sessions. One list shows terminals in the dock, agent windows, remote tmux sessions, the local model and SSH tunnels. Recent sessions are read from each agent's own history and can be resumed. `workbench sessions` prints the same from a terminal.
- Doctor, under **Advanced → Setup** and as `workbench doctor`. It checks Python and its packages, the data folder, the credential vault, the engine, the GPU driver, agent CLIs, ports, Docker, the terminal, the Linux desktop sandbox, macOS quarantine and Automation consent, SSH and proxies, and gives one sentence that says what to fix for each problem. `--json` prints machine-readable results.
- One-line installers, `install.sh` for macOS and Linux and `install.ps1` for Windows. They download the kit for your system, check it against `SHA256SUMS`, install for your user without sudo or administrator rights, add a `workbench` command and can uninstall again while keeping your data.
- A recommended model for your hardware. The catalog in `model_catalog.json` lists each model file with its size, SHA-256 and licence, and each pick with the memory it needs and whether it was measured or is only suggested. Downloads are checked against the SHA-256.
- Progress and Cancel for engine installs, model downloads and model starts.
- `workbench trust`, to give consent for a folder's own tools from a terminal.
- Saved connections can be removed, and a saved key forgotten, from **Connections**.
- Release kits are built from a reviewed file list and come with `SHA256SUMS`. Two builds in a row of the same commit gave byte-identical archives.

### Changed

- The desktop window is rebuilt around Launch, Overview, Project setup, Connections, Machines and Advanced, with a terminal dock and light and dark themes.
- The Launch view switches off an agent the selected model source cannot serve and says why in one sentence. OMP, Pi and opencode need Chat Completions, Codex needs Responses and Claude Code needs Anthropic Messages. Hermes starts only with its own account.
- Built-in tools such as web search, and MCP servers you registered, now reach every agent that accepts MCP servers at launch: OMP, Pi, Claude Code and Codex. Hermes, opencode, and OMP and Pi signed in with their own accounts keep their own MCP settings.
- The managed llama.cpp build is chosen from your GPU: CUDA for NVIDIA, Vulkan for AMD and Intel, Metal on Apple Silicon and CPU otherwise. When no local engine can run on your system, Workbench says so and points you to an attached server or a hosted API.
- When Workbench serves a Qwen model whose chat template rejects a system message after the first turn, it serves a corrected copy of the template, so Claude Code and Codex work with it.
- Python packages are installed from the hash-pinned `requirements.lock`. The launchers accept Python 3.12, 3.13 and 3.14, say what to do otherwise, and rebuild an incomplete Python environment.
- The Electron runtime is checked against SHA-256 values pinned in this repository.
- A copy installed by script keeps its data in your user data folder, so an upgrade does not touch it: `~/Library/Application Support/Agent Workbench` on macOS, `${XDG_DATA_HOME:-~/.local/share}/agent-workbench` on Linux, `%LOCALAPPDATA%\Agent Workbench\data` on Windows.
- `setup-linux.sh` lists the system packages it needs and asks before it uses sudo. It also sets up the desktop sandbox on systems that limit user namespaces, again after asking.
- Every request from the desktop window goes through one allow-listed action table. Long jobs report progress, a request that gets no answer times out with a sentence, and the window restarts the Python backend when it stops.

### Fixed

- Closing a terminal in the dock no longer hangs on macOS.
- The embedded terminal holds incomplete synchronized TUI redraws and trailing escape sequences instead of splitting them mid-frame, avoids redundant full-screen repaints, and uses native Wayland on Linux when `WAYLAND_DISPLAY` is set.
- Enabling the Blender tool now automatically installs and enables the pinned `blender_mcp` add-on inside Blender's own add-ons folder (including Flatpak Blender on Linux), and re-enables it if it was later turned off in Blender.
- On Windows, starting Firecrawl and checking Docker readiness now fall back to the managed `AgentWorkbench-Web` WSL distribution when Docker Desktop is installed on `PATH` but stopped.
- An agent now starts in the project folder in every case. Before, opencode could write files into Workbench's own folder.
- Claude Code and Codex no longer fail with "System message must be at the beginning" on Qwen models that Workbench serves.
- On macOS, a kit downloaded with a browser could not start its bundled Python. `Launch Workbench.command` now removes the download mark from its own folder, and the install script does the same.
- The benchmark stops with a clear sentence when a prompt does not fit the server's context window, and the adapter for the Harbor evaluation framework counts each finished assistant message's tokens once.

### Security

- Values read from a project profile are validated before use. Applying a profile shows every field it will change, including the prompt, and needs your confirmation. A profile's prompt applies to that folder only.
- Before the first launch in a folder whose own files define MCP servers, hooks or other tools that run programs, Workbench asks for consent, remembers it for that folder and asks again when those files change.
- On Windows, an agent installed as a `.cmd` or `.bat` file is started through the program the file wraps when Workbench can find it. Otherwise a model ID, prompt or folder path that contains shell metacharacters is not passed to it; Workbench refuses with a sentence instead. Commands sent to WSL use `--exec`.
- Engine archives for the Tested channel (llama.cpp b12633 and Strata) are pinned by SHA-256.
- Without a system credential vault, a hosted key is kept in memory until Workbench closes, or read from its environment variable. Keys are never put on a command line or in project files.
- Codex receives MCP servers on its command line, so a server registered on this computer that has arguments or a working folder is not passed to Codex. Project setup and the Launch view say so.
- The desktop bridge refuses requests whose data is not an object.

### Removed

- The 0.3 desktop interface. The classic interface (`workbench.py`) stays as a fallback.

### Known issues

- On Windows 11 x64, packaged desktop launch, installer/reinstall/uninstall, local llama.cpp and Strata inference, OMP file editing, PowerShell terminal dock and starter kits passed, while the clean-checkout Python unit-test run still had 15 failures and 1 error (platform-specific test expectations and process/listener checks) under investigation.
- macOS is a preview. These macOS checks have not passed yet: the desktop smoke test with the 0.4 interface, a CI job, an install from a published release, a browser download with the Gatekeeper dialog, the first start of the desktop runtime, an agent in a native terminal window, the terminal dock, and Doctor.
- Hosted CI has not run for this release.
- The kits are not code-signed or notarised. The release workflow is set up to record build attestations but has not run, so 0.4.0 may ship without them.
- The setup guide has not been run end to end to a working agent, and attaching Ollama or LM Studio has not been run from the desktop window.
- Pi and Hermes were not run.
- OpenRouter was run in an OMP session on Fedora 44; the other new hosted presets (Anthropic, Google Gemini, Together, Fireworks, Mistral, xAI, Cerebras and Hugging Face) are configured from each provider's documentation and were not run end to end.
- The kit MCP servers are listed from their package registries. None was started for this release.
- SSH machines, the Docker sandbox and the web tools were not run for this release.
- When Ollama does not report the loaded context size, as with Ollama 0.40.8, Workbench assumes 32,768 tokens and says so when you attach it.
- The chat template correction applies only to models Workbench serves. Claude Code against an attached LM Studio server was not tried.
- With Blender switched on and `uvx` missing, or with an unreadable extra MCP file, launching Hermes or opencode stops with that error, although those agents receive no MCP servers from Workbench.

What was run for this release: see [Tested configurations](README.md#tested-configurations).

## [0.3.0-rc.1]

Private release candidate. It was not published.
