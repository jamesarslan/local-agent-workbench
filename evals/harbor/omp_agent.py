"""Harbor adapter: run OMP (Oh My Pi) as a Terminal-Bench / Harbor agent.

Usage:
    PYTHONPATH="$PWD/evals/harbor" \
    harbor run -d terminal-bench/terminal-bench-2-1 \
      -a omp_agent:Omp -m openrouter/deepseek/deepseek-v4.1-flash-1017 \
      --ak thinking=high -l 1 -n 1 -k 1

The adapter installs Node 22 via nvm and a pinned Bun release inside the task
container, installs the pinned OMP package from the npm registry with Bun, then
runs one headless `omp -p` turn on the task instruction. Node is used only to
total token usage from the transcript. The Terminal-Bench verifier, not the
agent exit code, decides success.
"""

import os
import shlex
from typing import Annotated, override

from pydantic import Field

from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.agents.installed.node_install import nvm_node_install_snippet
from harbor.agents.model_connection import ModelConnectionSpec
from harbor.agents.options import Cli, InstalledAgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

OMP_PACKAGE = "@oh-my-pi/pi-coding-agent"
OMP_VERSION = "18.12.5"
BUN_RELEASE = "bun-v1.4.3"

# Runs inside the container after the agent: sums usage once per finished
# assistant message in the NDJSON transcript and prints one line for the host.
_USAGE_JS = """
const fs = require("fs");
const file = process.env.OMP_TRANSCRIPT || "/logs/agent/omp.jsonl";
let t = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 };
let lines = [];
try { lines = fs.readFileSync(file, "utf8").split("\\n"); } catch (e) {}
for (const l of lines) {
  if (!l.trim()) continue;
  let e; try { e = JSON.parse(l); } catch { continue; }
  if (!e || e.type !== "message_end" || !e.message || e.message.role !== "assistant") continue;
  const u = e.message.usage;
  if (u && typeof u.input === "number") {
    t.input += u.input; t.output += u.output || 0;
    t.cacheRead += u.cacheRead || 0; t.cacheWrite += u.cacheWrite || 0;
  }
}
console.log("OMP_USAGE " + JSON.stringify(t));
"""


def _parse_usage(stdout: str) -> dict | None:
    import json
    import re

    match = re.search(r"OMP_USAGE (\{[^\n]*\})", stdout or "")
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


class OmpOptions(InstalledAgentOptions):
    thinking: Annotated[str | None, Cli("--thinking")] = Field(
        default="high",
        description="OMP thinking level: off|minimal|low|medium|high|xhigh|max.",
    )
    max_time: Annotated[int | None, Cli("--max-time")] = Field(
        default=None,
        description="Wall-clock cap for the OMP run, in seconds.",
    )


class Omp(BaseInstalledAgent):
    """OMP (Oh My Pi) coding harness, driven headlessly per task."""

    capabilities = AgentCapabilities()
    MODEL_CONNECTION = ModelConnectionSpec(passthrough=True)
    options_model = OmpOptions
    options: OmpOptions
    _usage: dict | None = None

    @staticmethod
    @override
    def name() -> str:
        return "omp"

    @override
    def get_version_command(self) -> str | None:
        return (
            '. "$HOME/.nvm/nvm.sh"; export PATH="$HOME/.bun/bin:$PATH"; omp --version'
        )

    @override
    def parse_version(self, stdout: str) -> str:
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        first = next(
            (line for line in lines if "omp/" in line), lines[-1] if lines else ""
        )
        return first.split("omp/", 1)[-1].strip() if "omp/" in first else first

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        version = self._version or OMP_VERSION
        await self.ensure_system_dependencies(environment, ("curl", "unzip", "git"))
        await self.exec_as_agent(
            environment,
            command=(
                f"{nvm_node_install_snippet(22)} && "
                f"curl -fsSL https://bun.sh/install | bash -s {BUN_RELEASE} && "
                'export PATH="$HOME/.bun/bin:$PATH" && '
                f"bun add -g {shlex.quote(f'{OMP_PACKAGE}@{version}')} && "
                "omp --version"
            ),
        )

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        usage = self._usage
        if not usage:
            return
        cache = int(usage.get("cacheRead") or 0)
        prompt = int(usage.get("input") or 0)
        context.n_input_tokens = prompt + cache
        context.n_cache_tokens = cache
        context.n_output_tokens = int(usage.get("output") or 0)
        context.metadata = {"omp_usage_raw": usage}

    @override
    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if not self.model_name or "/" not in self.model_name:
            raise ValueError(
                "Model must be provider/model, e.g. "
                "openrouter/deepseek/deepseek-v4.1-flash-1017"
            )

        access = self.model_connection
        env = dict(access.env)
        api_key = access.api_key or os.environ.get("OPENROUTER_API_KEY")
        if api_key:
            env["OPENROUTER_API_KEY"] = api_key

        thinking = self.options.thinking or "high"
        max_time = (
            f"--max-time {int(self.options.max_time)} " if self.options.max_time else ""
        )

        command = (
            '. "$HOME/.nvm/nvm.sh"; export PATH="$HOME/.bun/bin:$PATH"; '
            "omp -p "
            f"--model {shlex.quote(self.model_name)} "
            f"--thinking {shlex.quote(thinking)} "
            "--auto-approve --no-session --no-extensions --no-skills --no-rules "
            "--allow-home --mode=json "
            f"{max_time}"
            f"{shlex.quote(instruction)} "
            "> /logs/agent/omp.jsonl 2>&1; rc=$?; "
            f"node -e {shlex.quote(_USAGE_JS)}; exit $rc"
        )
        result = await self.exec_as_agent(environment, command=command, env=env)
        self._usage = _parse_usage(getattr(result, "stdout", "") or "")
