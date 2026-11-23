# Model sources

This guide covers every place a model can run for Agent Workbench, which agent can use which, and what each source needs from you: a model on this computer, a server you already run, a GPU machine over SSH, a hosted provider, or an agent's own account.

## Three APIs, six agents

Each agent talks to its model through one API. Workbench does not translate between APIs, so a source has to offer the API the agent needs.

| Agent | API it needs |
| --- | --- |
| OMP | OpenAI Chat Completions |
| Pi | OpenAI Chat Completions |
| opencode | OpenAI Chat Completions |
| Codex | OpenAI Responses |
| Claude Code | Anthropic Messages |
| Hermes | Its own account only (choose **Agent account** as the source) |

When you attach a server, Workbench sends an empty request to each of the three API routes and records which ones exist. It then decides per agent:

- The source offers the API: the agent is available.
- The source does not offer it: the agent is switched off. On **Launch** its card reads "Not with this source", and the setup guide's Agent step shows the card disabled. The reason is one sentence, for example "Codex needs the OpenAI Responses API, which MLX LM server does not offer; choose OMP, Pi or opencode, or another source."
- The source did not say: the agent stays available with a warning mark ("May not work"), and the sentence says the API is not confirmed.

Hermes signs in with its own account, so with any other source its card says "Hermes signs in with its own account; choose Agent account as the source to use it."

If you want one local address in front of several providers or engines, look at [claude-code-router](https://github.com/musistudio/claude-code-router), a gateway for coding agents, or [llama-swap](https://github.com/mostlygeek/llama-swap), which exposes all three API routes and forwards each request to the engine behind it. Workbench recognises llama-swap when it scans; neither tool was run with Workbench for this release.

## A local model on this computer

Workbench can install a pinned build of llama.cpp, download a model that fits your memory, and serve it to your agents on `127.0.0.1`.

### The engine

The engine is llama.cpp build b12633. Workbench picks the build from your hardware, and Doctor's GPU row says which build and why:

| Hardware found | Build |
| --- | --- |
| An NVIDIA GPU with a working driver | CUDA |
| An AMD or Intel GPU with a Vulkan loader | Vulkan |
| An NVIDIA GPU whose driver did not answer, with a Vulkan loader | Vulkan (install the driver and run engine setup again to switch to CUDA) |
| Apple Silicon | Metal |
| No supported GPU, or a GPU without a Vulkan loader | CPU |

To choose the build yourself, run engine setup from a terminal with `--backend` (`auto`, `cuda`, `vulkan` or `cpu`; on a Mac only `metal`):

```sh
workbench setup_engines.py --backend vulkan
```

```powershell
workbench setup_engines.py --backend cpu
```

Install the engine with **Install engine** in **Advanced → Model library**, with **Install** next to llama.cpp / Strata in **Advanced → Setup**, or let the setup guide download it with a model. The **Engine release** setting in **Advanced → Model library** has three channels:

- Tested: the pinned build. Each archive is checked against a SHA-256 value pinned in the repository before it is unpacked.
- Updated: the newest llama.cpp release, checked against the digest GitHub reports for it at that moment. It is not pinned in the repository and has not been run with Workbench.
- Unsloth: Unsloth's llama.cpp build, pinned like Tested. It adds the DSpark drafter for Inkling 2 Mini ([below](#inkling-2-mini-with-unsloths-build)).

With the CUDA build on Windows or Linux, engine setup also installs Strata, which runs Strata model packs, when Git is installed; `--no-strata` skips it. vLLM is a separate item in **Advanced → Setup**. Strata and vLLM both need an NVIDIA GPU with a working driver; on a Mac, Workbench serves GGUF models with llama.cpp and Metal. On Windows or Linux, when a Strata Flash Next launch preset already exists, the engine list also offers Flash preset, which starts that preset; Setup does not install it. Custom starts an engine you describe in a JSON manifest ([Reference](reference.md#engines)).

### Inkling 2 Mini with Unsloth's build

Unsloth's llama.cpp build adds DSpark, a small drafter model for Inkling 2 Mini: it proposes the next few tokens and Inkling checks them. The build is opt-in, and Tested stays the default.

1. In **Advanced → Model library**, open **More engine settings** and set **Engine release** to **Unsloth**.
2. Choose **Install Unsloth build**, or run `workbench setup_engines.py --unsloth`. Workbench downloads Unsloth's release `b12649-mix-c7059d6` from GitHub, checks it against a SHA-256 value pinned in the repository and unpacks it into `runtime/llama-unsloth-b12649` in the data folder, beside the Tested build. It picks the build as for Tested: Metal on Apple Silicon, the CUDA build that matches the NVIDIA GPU and driver, Vulkan for AMD and Intel GPUs, and CPU otherwise. AMD GPUs get Vulkan because Workbench does not detect the ROCm target that Unsloth's ROCm builds need.
3. Select an Inkling 2 Mini GGUF in **Weights**, turn on **Speculative decoding** and choose **Download drafter**. It downloads `dspark-Inkling-2-Mini-Q8_0.gguf` (504 MB) from `unsloth/Inkling-2-Mini-GGUF` at a pinned revision, checks its SHA-256 and places it in the model's folder. The Inkling entries in the catalog list it as an optional file, so a model download does not fetch it.

Workbench then starts `llama-server` with `-md <drafter> --spec-type draft-dspark --spec-draft-n-max 2 -ngld 99`. When the drafter is missing, or **Engine release** is Tested or Updated, Inkling runs without it and the start message says why; the mainline build has no DSpark support. With the drafter, replies can differ slightly from replies without it, even at temperature 0, as is usual for speculative decoding. One run of this setup on a Mac is recorded in [`results/inkling-unsloth-dspark-m3-max.json`](../results/inkling-unsloth-dspark-m3-max.json).

The build and the drafter are Unsloth's work: [unslothai/unsloth](https://github.com/unslothai/unsloth) and [unsloth/Inkling-2-Mini-GGUF](https://huggingface.co/unsloth/Inkling-2-Mini-GGUF).

### Recommended models

The model catalog (`model_catalog.json`) lists files whose Hugging Face repository, revision, size, SHA-256 and licence were checked. Downloads are checked against that SHA-256 before Workbench uses them.

Workbench sizes a pick in two parts.

The budget is the memory a model may use:

| Memory kind | Usable for a model |
| --- | --- |
| Apple Silicon unified memory | The Metal working set minus 1.0 GiB |
| GPU memory | GPU memory minus the larger of 1.5 GiB and 8% |
| System memory (CPU build) | System memory minus the larger of 4.0 GiB and 25% |

The need is the model's weights, plus the context cache for the pick's window, plus a compute allowance, plus extras such as a draft model. For a file whose peak memory was recorded, the recorded peak is used instead. A pick fits when its need is within the budget; on a Mac it is a tight fit when the rest of the system would have little room left. **How this was sized** shows the arithmetic for your computer.

Each pick carries a status:

- Measured: a result file in this repository measures that exact model file. The recommendation names the hardware it was measured on; the figures are in [MEASUREMENTS.md](../MEASUREMENTS.md).
- Suggested: chosen from the model's size; not measured. For some Apple Silicon picks the label's help text adds that the file was tried on an M3 Max with 48 GB, where it loaded, answered and called a tool. That is evidence of loading and tool calling, not of speed or quality.

The files in the catalog:

| Model | Engine | Download | Licence |
| --- | --- | --- | --- |
| Qwen3.5-4B Q4_K_M | llama.cpp | 2.6 GiB | Apache-2.0 |
| Qwen3.5-9B Q4_K_M | llama.cpp | 5.3 GiB | Apache-2.0 |
| Qwen3.5-9B Q6_K | llama.cpp | 6.9 GiB | Apache-2.0 |
| Qwen3.8-27B UD-IQ3_XXS | llama.cpp | 10.2 GiB | Apache-2.0 |
| Qwen3.8-27B UD-Q4_K_XL | llama.cpp | 16.4 GiB (16.7 GiB with the Pi adapter) | Apache-2.0 |
| Qwen3.8-27B UD-Q6_K_XL | llama.cpp | 23.6 GiB | Apache-2.0 |
| Qwen3.8-27B Q8_0 | llama.cpp | 27.1 GiB | Apache-2.0 |
| Inkling 2 Mini UD-IQ3_XXS | llama.cpp | 10.9 GiB | Apache-2.0 with the Thinking Machines Lab Model Acceptable Use Policy |
| Inkling 2 Mini UD-Q4_K_XL | llama.cpp | 16.4 GiB | Apache-2.0 with the Thinking Machines Lab Model Acceptable Use Policy |
| Inkling 2 Mini UD-Q8_K_XL | llama.cpp | 21.3 GiB | Apache-2.0 with the Thinking Machines Lab Model Acceptable Use Policy |
| Inkling 2 Mini SQ-10 | Strata | 9.8 GiB | Apache-2.0 with the Thinking Machines Lab Model Acceptable Use Policy |
| Inkling 2 Mini SQ-15 | Strata | 13.4 GiB | Apache-2.0 with the Thinking Machines Lab Model Acceptable Use Policy |
| Inkling 2 Mini SQ-20 | Strata | 18.6 GiB | Apache-2.0 with the Thinking Machines Lab Model Acceptable Use Policy |
| Qwen3.8-Flash-Next GSQ-RCO IQ3_S | llama.cpp | 77.9 GiB | Qwen Community License 1.0 (commercial use is conditional) |

Before an Inkling or Flash Next download, Workbench shows the licence note and asks you to confirm.

Which file is offered depends on the engine build and the memory listed for it:

| Build | Listed for | Model and window | Status |
| --- | --- | --- | --- |
| CUDA, Vulkan, CPU | any | Qwen3.5-4B Q4_K_M, 16K | Suggested; not measured |
| CPU | 32 GB | Qwen3.5-9B Q4_K_M, 16K | Suggested; not measured |
| CUDA, Vulkan | 8 GB | Qwen3.5-9B Q4_K_M, 16K | Suggested; not measured |
| CUDA, Vulkan | 12 GB | Qwen3.5-9B Q6_K, 32K | Suggested; not measured |
| CUDA | 12 GB | Inkling 2 Mini SQ-10, 16K | Suggested; not measured |
| CUDA, Vulkan | 16 GB | Qwen3.8-27B UD-IQ3_XXS, 32K | Suggested; not measured |
| CUDA | 16 GB | Inkling 2 Mini SQ-15, 16K | Suggested; not measured |
| Vulkan | 16 GB | Inkling 2 Mini UD-IQ3_XXS, 16K | Measured on a Radeon RX 9070 XT (16 GB) on Linux |
| CUDA | 24 GB | Qwen3.8-27B UD-Q4_K_XL, 32K | Suggested; not measured |
| CUDA | 24 GB | Inkling 2 Mini UD-Q4_K_XL, 32K | Measured on an RTX 5090 (32 GB) on Windows |
| Vulkan | 24 GB | Qwen3.8-27B UD-Q4_K_XL, 32K | Suggested; not measured |
| CUDA | 32 GB | Inkling 2 Mini SQ-20, 32K | Measured on an RTX 5090 (32 GB) on Windows |
| CUDA | 32 GB | Qwen3.8-27B UD-Q4_K_XL with the Pi adapter, 128K | Measured on an RTX 5090 (32 GB) on Windows |
| CUDA | 32 GB | Inkling 2 Mini UD-Q8_K_XL, 32K | Measured on an RTX 5090 (32 GB) on Windows |
| Vulkan | 32 GB | Qwen3.8-27B UD-Q4_K_XL, 64K | Suggested; not measured |
| CUDA | 48 GB | Inkling 2 Mini SQ-20, 64K | Measured on an RTX 5090 (32 GB) on Windows |
| CUDA, Vulkan | 48 GB | Qwen3.8-27B Q8_0, 128K | Suggested; not measured |
| CUDA | 64 GB | Qwen3.8-Flash-Next GSQ-RCO IQ3_S, 64K | Suggested; not measured |
| CUDA | 96 GB | Qwen3.8-Flash-Next GSQ-RCO IQ3_S, 128K | Suggested; not measured |
| Metal | any | Qwen3.5-4B Q4_K_M, 32K | Suggested; loaded, answered and called a tool on an M3 Max 48 GB |
| Metal | 16 GB | Qwen3.5-9B Q4_K_M, 32K | Suggested; loaded, answered and called a tool on an M3 Max 48 GB |
| Metal | 32 GB | Qwen3.8-27B UD-IQ3_XXS, 32K | Suggested; loaded, answered and called a tool on an M3 Max 48 GB |
| Metal | 32 GB | Inkling 2 Mini UD-Q4_K_XL, 32K | Suggested; loaded, answered and called a tool on an M3 Max 48 GB |
| Metal | 36 GB | Qwen3.8-27B UD-Q4_K_XL, 32K | Suggested; loaded, answered and called a tool on an M3 Max 48 GB |
| Metal | 64 GB | Qwen3.8-27B Q8_0, 128K | Suggested; not measured |
| Metal | 64 GB | Qwen3.8-27B UD-Q6_K_XL, 32K | Suggested; loaded, answered and called a tool on an M3 Max 48 GB |
| Metal | 96 GB | Qwen3.8-Flash-Next GSQ-RCO IQ3_S, 128K | Suggested; not measured |

When no catalog model fits, the guide says so and points you to a running server, a hosted API or a machine over SSH.

### The model library

**Advanced → Model library** holds:

- **Recommended for this computer** and the **Catalog**, with a download or use button for each file.
- **Your model folder**: by default `models` in the data folder. **Browse folders…** points it elsewhere, **Rescan** reads it again, and you can drop a model file onto the panel. **Weights** picks the file the local model loads.
- **Context**, **Engine**, and under **More engine settings** the **KV cache**, **Engine release** and the toggles **Speculative decoding**, **Vision companion** and **Matched Qwen LoRA**.
- **Save as a named setup (optional)**: a named setup appears in the model list on **Launch**, and **Create launcher** makes a launcher file that starts it.

**Advanced → Hugging Face** searches Hugging Face for other GGUF models and downloads them into your model folder. Those files have no SHA-256 pinned in the repository; Workbench checks each one against the SHA-256 Hugging Face lists for it, when it lists one.

### Start, stop and the port

The local model starts when you launch an agent with **Local model** as the source, or with **Start** in the status bar (the "Local model" item at the bottom of the window) or on the **Local model** entry in **Connections**. **Stop** ends it. From a terminal: `workbench serve`, `workbench status` and `workbench stop`.

The server listens on `127.0.0.1` port 10532 (set `WORKBENCH_PORT` to change it), serves the model under the ID `local-agent`, and refuses requests without the key Workbench generates in `api-key.txt` in the data folder. **Copy local API key** in **Advanced → Agent settings** copies it if you want to use the model from another program on this computer.

Closing Workbench does not stop the model server. Stop it in the status bar or with `workbench stop`.

### Chat templates for Claude Code and Codex

Claude Code sends a system message after the first user message, and Codex sends developer messages. The chat templates embedded in the Qwen model files reject a system message that is not first, and llama.cpp then answers with HTTP 500 "System message must be at the beginning."

For models it serves itself, Workbench handles this: when the template of a GGUF file contains exactly that rejection, it serves a corrected copy that renders the message as a system turn. With the correction in place, on a Mac, Claude Code completed a file-writing task against Qwen3.5-9B and Qwen3.8-27B, and Codex against Qwen3.5-9B, started through Workbench's launch code from the command line. The Inkling template does not need the correction.

For a server you attach, that server's own template handling decides. Workbench does not change it. Claude Code and Codex completed the same task against one model in Ollama 0.40.8; other Ollama models, LM Studio and the other servers were not run with Claude Code or Codex for this release.

## Attach a server that is already running

### What Workbench looks for

The scan looks only at this computer's loopback address, `127.0.0.1`, on these ports: 11434, 1234, 9931, 8080, 8081, 8000, 1337, 5001, 5000, 13305, 30000, 10240 and 9292, leaving out Workbench's own model port. It also checks LM Studio's control ports, so it can tell you when LM Studio is open but its server is off. It never scans your network. A server on another machine is reached through [SSH](#a-gpu-machine-over-ssh) or by an `https://` address you type.

For each server that answers, Workbench reads the model list, the context size of each model where the server reports it, and which of the three APIs it offers. It names the server from what it answers.

The server kinds Workbench recognises, with the APIs their documentation says they serve. Workbench uses what the server itself answers when you attach it, not this table. Except for Ollama and LM Studio, whose answers were recorded for 0.4.0, each row is configured from the server's documentation and was not exercised for this release.

| Server | Ports scanned | Chat Completions | Responses | Anthropic Messages |
| --- | --- | --- | --- | --- |
| Ollama | 11434 | Yes | Yes | Yes |
| LM Studio | 1234 | Yes | Yes | Yes |
| llama.cpp server | 9931, 8080, 8081 | Yes | Yes | Yes |
| vLLM | 8000 | Yes | Yes | Yes |
| MLX LM server | 8080 | Yes | No | No |
| MLX Omni Server | 10240 | Yes | No | Yes |
| Jan | 1337 | Yes | No | Yes |
| KoboldCpp | 5001 | Yes | Yes | Yes |
| text-generation-webui | 5000 | Yes | No | Yes |
| LocalAI | 8080 | Yes | Yes | Yes |
| llama-swap | 8080, 9292 | Yes | Whatever the engine behind it offers | Whatever the engine behind it offers |
| TabbyAPI | 5000 | Yes | No | No |
| SGLang | 30000 | Yes | Yes | Yes |
| Lemonade Server | 13305 | Yes | Yes | Yes |
| Another OpenAI-compatible server | Any address you type | As probed | As probed | As probed |

vLLM, MLX Omni Server, KoboldCpp, LocalAI and llama-swap listen on every network interface by default. When one of them answers without a key, Workbench warns you to bind it to `127.0.0.1` or set a key if other devices share your network.

### Ollama

These steps were not run for 0.4.0 in the desktop window. Ollama 0.40.8 answered the scan, was attached, and served OMP, Claude Code, Codex and opencode through the same code from the command line on a Mac.

1. Start Ollama. Pull or load the model you want.
2. In the setup guide's Model step choose **Use a server that is already running**, or in **Connections** choose **Add**, then **Attach a running server…**.
3. Pick Ollama on port 11434 and the model, then choose **Attach and continue** in the guide or **Attach** in the dialog.

Ollama sets a model's context size from the GPU memory it finds, and its documentation says agents need at least 64,000 tokens ([Ollama: context length](https://github.com/ollama/ollama/blob/main/docs/context-length.mdx)). When Ollama does not report the context, Workbench assumes 32,768 tokens and says so. Set `OLLAMA_CONTEXT_LENGTH` before you start Ollama, then check the connection again.

If you use Claude Code, Codex or opencode with Ollama, [`ollama launch`](https://docs.ollama.com/cli) starts it against an Ollama model in one command. Workbench adds a per-folder setup, the other agents and sources, and project setup.

### LM Studio

These steps were not run for 0.4.0. LM Studio answered the scan's probes; attaching it was not run.

1. In LM Studio, open Developer and start the server, or run `lms server start`. Load a model.
2. Attach it as for Ollama: pick LM Studio on port 1234.

When LM Studio is open but its server is off, the scan shows: "LM Studio is running, but its local server is off. In LM Studio open Developer and start the server, then scan again."

LM Studio loads a model with its own context size, which can be far below the model's maximum ([LM Studio documentation](https://github.com/lmstudio-ai/docs)). When the loaded context is under 16,000 tokens, Workbench warns that it is small for an agent; load the model again with a larger context in LM Studio and check again. If you turned on "Require Authentication" in LM Studio, paste one of its API tokens in the key field.

### Other servers and typed addresses

Choose **Type a server address** in the setup guide, or the address field under "Or an address on this computer" in the attach dialog, and choose **Check address**.

You can type `127.0.0.1:8080`, `http://127.0.0.1:8080/v1`, a full route such as `.../v1/chat/completions`, or an `https://` address. Workbench removes a trailing route, adds `http://` for this computer and `https://` for any other host, and tries the address with and without `/v1`. It refuses an address with a user name, password, query string or unusual characters, and it refuses plain HTTP to another computer: "Plain HTTP is only allowed to this computer. Use an https:// address, or add the machine under Machines to reach it through an SSH tunnel."

When nothing answers you see, for example, "Nothing answered at http://127.0.0.1:8080. Start the server or check the address and port, then try again." When something answers but not as a model API you see "http://127.0.0.1:8080 answered, but not as an OpenAI-compatible API. Check the address; most servers use a path that ends in /v1."

### Keys for local servers

When a server answers that it needs a key, an "API key for this server" field appears. Paste the key and choose **Check with key**. Workbench keeps it in your system's credential vault, or in memory until it closes when there is no vault.

### Context size

Workbench takes the context size from the server when the server reports it. When it does not, Workbench assumes 32,768 tokens and warns you. To set it yourself, open the connection in **Connections**, choose "Set it myself" under **Context** and enter a number between 1,024 and 10,000,000. **Launch** shows the size it will pass to the agent and where it came from.

### Checking again

**Connections** shows each attached server as "Answering" or "Not answering", with the time of the last scan. **Check again** probes it again and updates the APIs it offers. **Scan this computer** runs a new scan.

## A GPU machine over SSH

Workbench reaches another computer through SSH only. Model traffic travels inside that connection, so only the SSH port needs to be open.

### What you need

On this computer: the OpenSSH client (`ssh` and `ssh-keygen`). Doctor checks it.

On the machine:

- An SSH server that accepts a login from you.
- On Linux and macOS, `python3` 3.8 or newer, which Workbench uses to check the machine and find servers. A Windows machine needs only Windows PowerShell, which Windows includes; see [Windows machines](#windows-machines).
- For the Workbench-managed model: Linux on x86_64, `curl`, and 13 GiB free in your home folder, or Windows on x86_64 with an NVIDIA GPU of 16 GB or more and Python 3.8 or newer. On any other machine, Workbench can check it and attach the servers running there, but cannot run the managed model.

### Add and pair a machine

These steps were not run for 0.4.0.

1. Open **Machines** and choose **Add machine…**. Enter a name and an SSH alias from your SSH config or `user@hostname`, then **Save**.
2. Open **Pairing and removal** and choose **Pair through SSH**. A terminal opens: confirm the host key and sign in once. Workbench adds its own key for this machine to `~/.ssh/authorized_keys` there. **Show pairing command** gives you the command to run on the machine yourself instead.
3. Choose **Check connection**. Workbench shows the machine's system, memory and GPU memory. If the machine's firewall blocks SSH, **Firewall rule for this host** has a command for it.

Workbench uses a separate key for each machine, stored in the data folder, and always checks the host key against your SSH known hosts file. It never accepts a changed host key.

### Use a server that runs there

After the connection check, **Servers running there** lists the model servers answering on the machine's own loopback address. Workbench finds them with one SSH command that runs a small probe on the machine; it does not scan the machine's network. Choose **Attach…** next to a server, pick the model, and attach it. **Look again** repeats the search. A server on a port outside the usual list, such as 9000, is found once you add the port under **Other ports** and choose **Look again**; Workbench remembers the ports for that machine.

Attaching opens an SSH tunnel from a free port on this computer's `127.0.0.1` to that server's port on the machine's `127.0.0.1`, one port per server. The connection appears in **Connections** under "Over SSH", and agents talk to the model through the tunnel. **Reconnect** opens a tunnel again after the machine or this computer restarted; **Detach** closes it and removes the connection.

### The Workbench-managed model

On a Linux x86_64 machine, **Workbench-managed model** offers **Install…**. Workbench then installs, under `~/.local/share/agent-workbench-node` on the machine, the Vulkan build of llama.cpp b12633, the Inkling 2 Mini UD-IQ3_XXS model and, unless OMP is already installed there, the OMP binary, each checked against a SHA-256 pinned in Workbench. The machine downloads them itself from github.com and huggingface.co. Workbench waits up to an hour for this.

**Start and connect** starts the model on the machine's port 10542 with a 16K-token window, the catalog's choice for a 16 GB Vulkan card, bound to its loopback address with its own key, and opens the tunnel. **Stop…** stops it. The Inkling licence note applies to this model too.

### Remove a machine

Remove things in this order, all under **Pairing and removal**:

1. Remove the managed runtime from the machine, if you installed it. Its engine and model files are deleted there and its connection here.
2. Revoke the Workbench key on the machine. Workbench deletes its own key line from `authorized_keys` there and closes its tunnels; the machine stays listed.
3. **Forget this machine…**: Workbench deletes its key, tunnels and connections for the machine on this computer and changes nothing on the machine. If you forget a machine without revoking first, Workbench shows the key line you can remove from `~/.ssh/authorized_keys` there yourself.

Remote agent sessions on a machine are covered in [Sessions and terminals](sessions.md#remote-sessions).

### Windows machines

Workbench works with the OpenSSH server that Windows includes, whether its SSH shell is Command Prompt (the default) or PowerShell. **Check connection** finds out which system the machine runs each time, and the machine's row then says Windows.

- Finding servers: the probe runs in Windows PowerShell, so the machine needs no Python for it. It checks the same ports and answers the same way as on Linux. Attaching and tunnels work as above.
- Remote sessions: Windows has no tmux, so a session opens PowerShell (PowerShell 7 when it is installed) in the folder you chose and starts the agent there. A folder that starts with `~` is under your user folder, and the agent is found the way PowerShell finds it, including `.cmd` and `.ps1` installs. The session ends when its tab or window closes; there is nothing to reattach, so **End** in the dock ends it.
- Pairing: the Workbench key goes into `.ssh\authorized_keys` in your user folder, or into `administrators_authorized_keys` under `C:\ProgramData\ssh` for an administrator account, as the Windows OpenSSH server expects.

The Workbench-managed model on Windows needs Python 3.8 or newer for your user and an NVIDIA GPU whose driver is version 580 or newer. Workbench installs it under `%LOCALAPPDATA%\agent-workbench-node`: the CUDA build of llama.cpp b12633 with its CUDA runtime, and an Inkling 2 Mini file chosen from the model catalog by the GPU's memory, UD-Q8_K_XL on a 32 GB card, UD-Q4_K_XL on 24 GB and UD-IQ3_XXS on 16 GB, each checked against its pinned SHA-256. If the model file is already on the machine, type its path under the managed model, or choose **Look for it** and then **Use this file**, before **Install…**. Workbench reads the whole file, uses it only if its SHA-256 matches, and links it into the folder instead of copying it when it can; otherwise the machine downloads the file. **Start and connect** starts the model outside the SSH session, so it keeps running after the connection closes, until you choose **Stop…**.

## Hosted providers

These steps were not run for 0.4.0.

To add a provider, use **Use a hosted API** in the setup guide, or **Add** then **Hosted API** in **Connections**. Then:

1. Choose the **Provider** and, if you like, a **Connection name**.
2. Paste the key under **API key** and choose **Save key**.
3. Enter the model under **Default model**, or choose **Browse models** to pick from the provider's list.
4. Choose **Save**.

| Provider | Key variable | Chat Completions | Responses | Anthropic Messages | Status for 0.4.0 |
| --- | --- | --- | --- | --- | --- |
| OpenAI | `OPENAI_API_KEY` | Yes | Yes | No | Carried over from earlier versions; not exercised for this release |
| OpenRouter | `OPENROUTER_API_KEY` | Yes | Yes | Yes | Carried over from earlier versions; not exercised for this release |
| Groq | `GROQ_API_KEY` | Yes | Yes | No | Carried over from earlier versions; not exercised for this release |
| DeepSeek | `DEEPSEEK_API_KEY` | Yes | Not known | Yes | Carried over from earlier versions; not exercised for this release |
| Anthropic | `ANTHROPIC_API_KEY` | Yes | No | Yes | Configured from the provider's documentation; not exercised for this release |
| Google Gemini | `GEMINI_API_KEY` | Yes | Not known | Not known | Configured from the provider's documentation; not exercised for this release |
| Together | `TOGETHER_API_KEY` | Yes | Yes | Not known | Configured from the provider's documentation; not exercised for this release |
| Fireworks | `FIREWORKS_API_KEY` | Yes | Not known | Yes | Configured from the provider's documentation; not exercised for this release |
| Mistral | `MISTRAL_API_KEY` | Yes | Not known | No | Configured from the provider's documentation; not exercised for this release |
| xAI | `XAI_API_KEY` | Yes | Yes | No | Configured from the provider's documentation; not exercised for this release |
| Cerebras | `CEREBRAS_API_KEY` | Yes | Not known | Not known | Configured from the provider's documentation; not exercised for this release |
| Hugging Face | `HF_TOKEN` | Yes | Not known | Yes | Configured from the provider's documentation; not exercised for this release |

"No" switches the agent that needs that API off; "Not known" leaves it on with a warning that it may fail to connect. A provider page itself, not this table, is the authority on what it serves today.

### Where keys go

A key you save goes to your system's credential vault (Keychain on macOS, Credential Manager on Windows, a Secret Service vault such as GNOME Keyring or KWallet on Linux) under the service name `Agent Workbench`. Without a vault, Workbench keeps the key in memory until it closes and says so.

To keep a key outside the vault, set an environment variable before you start Workbench. A connection you add reads `WORKBENCH_KEY_` followed by its connection ID; the **Key** line of the connection shows the exact name, and while the variable is set Workbench uses it instead of a saved key. When the provider's own variable from the table is set at start-up, **Connections** also lists a connection named after the provider that uses it.

The agent receives the key in its process environment. Workbench does not put it on a command line, in a project file or in its logs. **Forget key** deletes a saved key; removing a connection deletes its key too.

**Get an API key ↗** opens the key page of OpenAI, Groq, DeepSeek or OpenRouter in your browser. For OpenRouter, **Sign in with OpenRouter ↗** signs in through your browser instead of a pasted key; Workbench waits up to 3 minutes for it.

### Context size and model details

For a hosted model, **Launch** shows the context size the agent receives and where it came from: the provider's own model list, OpenRouter's public model list, or the default of 128,000 tokens. For OpenAI, OpenRouter, DeepSeek and Groq sources, Workbench also reads OpenRouter's public model list, without a key, to learn which models accept images and their context sizes.

Everything the agent sends, which can include your files and images, goes to the provider you chose, under that provider's pricing and terms.

## Agent account

**Agent account** as the source means the agent uses its own sign-in and its own model settings, as it would in your terminal. Workbench starts it in the project folder and does not see or store that login. The **Model (optional)** field passes a model to the agent; leave it empty to use the agent's own choice.

The setup guide's Agent step and **Connections → Agent account** offer a sign-in button that opens the agent's own login in a terminal window:

| Agent | What the sign-in runs |
| --- | --- |
| Codex | `codex login --device-auth` |
| OMP | `omp login openai-codex-device` or `omp login anthropic`, for the **Subscription** you choose: ChatGPT device sign-in or Anthropic account |
| Hermes | `hermes auth add openai-codex` or `hermes auth add anthropic` with `--type oauth --no-browser`, for the **Subscription** you choose |
| opencode | `opencode auth login` |
| Claude Code | `claude auth login` |
| Pi | `pi auth login` |

When Codex is already signed in with ChatGPT, **Connections** lists a connection named "ChatGPT subscription · Codex".

With an agent account, Claude Code and Codex still receive the MCP tools Workbench sends at launch; OMP, Pi, Hermes and opencode use only their own MCP settings. [Project setup](project-setup.md#mcp-servers) has the details.

Product names belong to their owners. Agent Workbench is not affiliated with or endorsed by them.
