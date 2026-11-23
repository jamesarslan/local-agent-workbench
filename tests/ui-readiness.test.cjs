// AW.readiness.evaluate in desktop/ui/readiness.js: one fixture per row of ui.md 6.1, the "Launch needs:" line and the complete case.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ui = path.join(__dirname, "..", "desktop", "ui");
const context = vm.createContext({ console });
for (const file of ["core.js", "readiness.js"]) vm.runInContext(fs.readFileSync(path.join(ui, file), "utf8"), context, { filename: file });
const evaluate = (input) => JSON.parse(JSON.stringify(context.AW.readiness.evaluate(input)));

const NOW = Date.UTC(2026, 10, 23, 12);
const AGENTS = ["OMP", "Pi", "Claude Code", "Codex", "Hermes", "opencode"];
const ok = (reason = "") => ({ ok: true, reason });

function fixture() {
  return {
    form: {
      workspace: "/Users/me/code/shop", harness: "OMP", provider: "Managed local", connection_id: "", auth_provider: "", preset: "",
      search: true, firecrawl: false, blender: false, desktop: false, image_route: "auto", plugins: "", remote_model: "", endpoint: "http://127.0.0.1:8000/v1",
    },
    config: {
      presets: { Daily: { model: "Qwen3.8-27B-UD-Q4_K_XL.gguf", context: 32768, engine: "Auto" } },
      current: { model: "Qwen3.8-27B-UD-Q4_K_XL.gguf", context: 32768, engine: "Auto" },
      agents: AGENTS,
      cli_present: { OMP: true, Pi: false, "Claude Code": true, Codex: true, Hermes: false, opencode: true },
      data: "/data",
    },
    live: { label: "Stopped", model: "", port: 10532, owned: false },
    folder: { key: "/Users/me/code/shop", path: "/Users/me/code/shop", known: true, recipe: {}, stale: [] },
    folderError: null,
    accounts: [],
    scan: { endpoints: [], hints: [], received: NOW - 8000 },
    forwards: [],
    compat: { OMP: ok(), Pi: ok(), "Claude Code": ok(), Codex: ok(), Hermes: { ok: false, reason: "Hermes signs in with its own account; choose Agent account as the source to use it." }, opencode: ok() },
    consent: { needed: false, granted: false, digest: "", items: [] },
    resolve: {
      instructions: [{ path: "AGENTS.md", status: "read" }], skills: [{ name: "a" }, { name: "b" }],
      mcp: { launch: [{ name: "search", status: "sent", reason: "" }] }, warnings: [],
    },
    match: null,
    engine: { supported: true, engine_ready: true, download_bytes: 16600069, reason: "" },
    recommend: {
      pick: { id: "metal-36-qwen3.8-q4", label: "Qwen3.8-27B UD-Q4_K_XL", download_bytes: 17659407264, sizing: { fit: "comfortable", need_gib: 24.1, usable_gib: 36.44 }, installed: { installed: true, model: "/models/Qwen3.8-27B-UD-Q4_K_XL.gguf" } },
      alternatives: [],
      reason: "Qwen3.8-27B UD-Q4_K_XL at 32K context has an expected peak of 24.1 GiB and this computer can give it 36.4 of 48.0 GiB of unified memory.",
    },
    tools: { Docker: "/usr/local/bin/docker", Blender: "" },
    sourceTable: { harnesses: AGENTS.map((id) => ({ id, native_only: id === "Hermes" })) },
    jobs: [],
    unreachable: {},
    kitHint: null,
    scratch: "/data/workspace",
    subscription: "Use device sign-in to connect a ChatGPT account.",
    now: NOW,
  };
}

function shape(change) {
  const input = fixture();
  change(input);
  return evaluate(input);
}
const only = (result, id) => {
  const found = result.items.filter((item) => item.id === id);
  assert.equal(found.length, 1, `expected one ${id} in ${result.items.map((item) => item.id).join(", ")}`);
  return found[0];
};
const runs = (item) => item.fixes.map((fix) => fix.run);

function hosted(input, row = {}) {
  const account = {
    connection_id: "9f2c41d0a7b3e85c", name: "Work Groq", provider: "Groq", endpoint: "https://api.groq.com/openai/v1", remote_model: "llama-4", ready: true,
    compat: { OMP: ok(), Pi: ok(), "Claude Code": { ok: false, reason: "Claude Code needs the Anthropic Messages API, which Work Groq does not offer; choose OMP, Pi or opencode, or another source." }, Codex: ok(), Hermes: { ok: false, reason: "" }, opencode: ok() },
    ...row,
  };
  input.accounts.push(account);
  Object.assign(input.form, { provider: account.provider, connection_id: account.connection_id, remote_model: account.remote_model });
  return account;
}

function attached(input, row = {}) {
  return hosted(input, { connection_id: "c41aa0", name: "LM Studio", provider: "Attached endpoint", endpoint: "http://127.0.0.1:1234/v1", remote_model: "qwen3-8b", ...row });
}

test("a complete fixture is ready", () => {
  const result = evaluate(fixture());
  assert.equal(result.ok, true);
  assert.equal(result.needs, "");
  assert.deepEqual(result.items, []);
  assert.deepEqual(result.slots, {
    project: { level: "ok", summary: "shop" },
    model: { level: "ok", summary: "Local model · Qwen3.8-27B-UD-Q4_K_XL.gguf" },
    agent: { level: "ok", summary: "OMP" },
    setup: { level: "ok", summary: "AGENTS.md · 2 skills · 1 tool on" },
  });
});

test("project.missing", () => {
  const item = only(shape((i) => (i.form.workspace = "")), "project.missing");
  assert.equal(item.slot, "project");
  assert.equal(item.level, "block");
  assert.equal(item.needs, "a project folder");
  assert.deepEqual(runs(item), ["project.pick"]);
});

test("project.gone from a rejected folder request and from a folder without a key", () => {
  const refused = only(shape((i) => (i.folderError = { message: "Choose an existing project folder." })), "project.gone");
  assert.equal(refused.text, "Choose an existing project folder.");
  assert.equal(refused.level, "block");
  assert.deepEqual(runs(refused), ["project.pick"]);
  const gone = only(shape((i) => (i.folder = { key: "", path: "/Users/me/code/shop", known: false, stale: [] })), "project.gone");
  assert.equal(gone.text, "The folder /Users/me/code/shop no longer exists. Choose another folder.");
});

test("project.scratch", () => {
  const result = shape((i) => (i.form.workspace = "/data/workspace/"));
  const item = only(result, "project.scratch");
  assert.equal(item.level, "info");
  assert.deepEqual(runs(item), ["project.pick"]);
  assert.equal(result.slots.project.summary, "Scratch folder");
  assert.equal(result.ok, true);
});

test("model.none offers the running server, the recommended download, a scan and Connections", () => {
  const result = shape((i) => {
    i.config.current.model = "";
    i.engine.engine_ready = false;
    i.scan.endpoints = [{ label: "Ollama", port: 11434, base: "http://127.0.0.1:11434/v1", connection_id: "" }];
  });
  const item = only(result, "model.none");
  assert.equal(item.slot, "model");
  assert.equal(item.level, "block");
  assert.equal(item.text, "Choose where the model runs. Ollama is running on this computer.");
  assert.deepEqual(runs(item), ["source.attach", "model.install", "source.scan", "go.accounts"]);
  assert.equal(item.fixes[0].primary, true);
  assert.equal(item.fixes[0].arg, 11434);
  assert.equal(item.fixes[1].label, "Download Qwen3.8-27B UD-Q4_K_XL (16.4 GiB)");
  assert.equal(result.items.some((other) => other.id === "model.engine"), false);
});

test("model.saved-missing", () => {
  const item = only(shape((i) => Object.assign(i.form, { provider: "Groq", connection_id: "gone1234", remote_model: "llama-4" })), "model.saved-missing");
  assert.equal(item.text, "The Groq connection this folder used is no longer saved.");
  assert.equal(item.needs, "a model source");
  assert.deepEqual(runs(item), ["go.accounts"]);
});

test("model.engine, installable and unsupported", () => {
  const item = only(shape((i) => (i.engine.engine_ready = false)), "model.engine");
  assert.equal(item.level, "block");
  assert.equal(item.text, "The engine for local models is not installed yet (16 MiB).");
  assert.equal(item.needs, "the local engine");
  assert.deepEqual(runs(item), ["engine.install"]);
  const unsupported = only(shape((i) => Object.assign(i.engine, { engine_ready: false, supported: false, reason: "No engine build exists for this processor." })), "model.engine");
  assert.equal(unsupported.text, "No engine build exists for this processor.");
});

test("model.weights when the engine is ready and another source is saved", () => {
  const item = only(
    shape((i) => {
      i.config.current.model = "";
      i.accounts.push({ connection_id: "x", name: "Work Groq", provider: "Groq", ready: true, compat: {} });
    }),
    "model.weights",
  );
  assert.equal(item.text, "No local model is chosen.");
  assert.equal(item.needs, "a local model");
  assert.deepEqual(runs(item), ["model.install", "go.models"]);
  assert.equal(item.fixes[0].primary, true);
});

test("model.weights still blocks while engine-info has not answered", () => {
  const result = shape((i) => {
    i.config.current.model = "";
    i.engine = null;
    i.accounts.push({ connection_id: "x", name: "Work Groq", provider: "Groq", ready: true, compat: {} });
  });
  only(result, "model.weights");
  assert.equal(result.ok, false);
});

test("model.downloading is work with the task row and a percentage", () => {
  const result = shape((i) => {
    i.config.current.model = "";
    i.jobs = [{ handle: "model-download-3", action: "model-download", key: "model:metal-36-qwen3.8-q4", state: "running", progress: { done: 25, total: 100 } }];
  });
  const item = only(result, "model.downloading");
  assert.equal(item.level, "work");
  assert.equal(item.task, "model-download-3");
  assert.equal(item.needs, "the model download to finish (25%)");
  assert.deepEqual(item.fixes, []);
  assert.equal(result.ok, false);
  assert.equal(result.slots.model.level, "work");
  assert.equal(result.items.some((other) => other.id === "model.none" || other.id === "model.weights"), false);
});

test("model.credential offers a key field, the key page and OpenRouter sign-in", () => {
  const groq = only(shape((i) => hosted(i, { ready: false })), "model.credential");
  assert.equal(groq.text, "Work Groq needs an API key.");
  assert.equal(groq.needs, "a key for Work Groq");
  assert.deepEqual(runs(groq), ["source.key", "source.keypage"]);
  assert.equal(groq.fixes[0].field.type, "password");
  const router = only(shape((i) => hosted(i, { ready: false, provider: "OpenRouter", name: "Personal OpenRouter" })), "model.credential");
  assert.deepEqual(runs(router), ["source.key", "source.keypage", "source.oauth"]);
  const mistral = only(shape((i) => hosted(i, { ready: false, provider: "Mistral", name: "Mistral" })), "model.credential");
  assert.deepEqual(runs(mistral), ["source.key"]);
});

test("model.id", () => {
  const item = only(
    shape((i) => {
      hosted(i);
      i.form.remote_model = "";
    }),
    "model.id",
  );
  assert.equal(item.text, "Choose a model for Work Groq.");
  assert.deepEqual(runs(item), ["model.browse"]);
});

test("model.tunnel", () => {
  const item = only(
    shape((i) => {
      attached(i, { host: "gpu-box", endpoint: "http://127.0.0.1:41234/v1" });
      i.forwards = [{ connection_id: "c41aa0", host: "gpu-box", label: "GPU box", alive: false }];
    }),
    "model.tunnel",
  );
  assert.equal(item.text, "The SSH tunnel to GPU box is down.");
  assert.equal(item.needs, "the tunnel to GPU box");
  assert.deepEqual(runs(item), ["machine.reconnect", "go.machines"]);
});

test("model.unreachable from a failed check", () => {
  const result = shape((i) => {
    attached(i);
    i.unreachable = { c41aa0: { message: "LM Studio did not answer at http://127.0.0.1:1234/v1. Start its server, then check again." } };
  });
  const item = only(result, "model.unreachable");
  assert.equal(item.level, "block");
  assert.equal(item.text, "LM Studio did not answer at http://127.0.0.1:1234/v1. Start its server, then check again.");
  assert.equal(item.needs, "LM Studio to answer");
  assert.deepEqual(runs(item), ["source.refresh", "focus.source"]);
  assert.equal(result.items.some((other) => other.id === "model.unseen"), false);
  assert.equal(result.ok, false);
});

test("model.unseen for an attached server missing from the last scan", () => {
  const result = shape((i) => attached(i));
  const item = only(result, "model.unseen");
  assert.equal(item.level, "info");
  assert.equal(item.text, "LM Studio was not answering 8 s ago. It is checked again when you launch.");
  assert.deepEqual(runs(item), ["source.refresh"]);
  assert.equal(result.ok, true);
  const seen = shape((i) => {
    attached(i);
    i.scan.endpoints = [{ label: "LM Studio", port: 1234, base: "http://127.0.0.1:1234/v1", connection_id: "c41aa0" }];
  });
  assert.equal(seen.items.some((other) => other.id === "model.unseen"), false);
});

test("model.port", () => {
  const item = only(shape((i) => (i.live = { label: "Port in use", port: 10532, owned: false })), "model.port");
  assert.equal(item.text, "Port 10532 is used by another program, so the local model cannot start.");
  assert.equal(item.needs, "a free port");
  assert.deepEqual(runs(item), ["source.probe", "go.doctor"]);
  assert.equal(item.fixes[0].arg, 10532);
});

test("model.conflict", () => {
  const item = only(
    shape((i) => {
      i.form.preset = "Daily";
      i.live = { label: "Serving", model: "gemma-4-12b.gguf", port: 10532, owned: true };
      i.match = false;
    }),
    "model.conflict",
  );
  assert.equal(item.text, "A different local model is serving: gemma-4-12b.gguf.");
  assert.equal(item.needs, "one local model");
  assert.deepEqual(runs(item), ["server.adopt", "server.swap"]);
  assert.equal(item.fixes[1].label, "Stop it and load Daily");
});

test("model.fit warns with the catalog's sizing sentence", () => {
  const result = shape((i) => (i.recommend.pick.sizing.fit = "tight"));
  const item = only(result, "model.fit");
  assert.equal(item.level, "warn");
  assert.equal(item.text, fixture().recommend.reason);
  assert.deepEqual(runs(item), ["go.models"]);
  assert.equal(result.ok, true);
});

test("agent.missing", () => {
  const item = only(shape((i) => (i.form.harness = "Pi")), "agent.missing");
  assert.equal(item.slot, "agent");
  assert.equal(item.text, "Pi is not installed on this computer.");
  assert.equal(item.needs, "Pi installed");
  assert.deepEqual(runs(item), ["tool.install", "agent.use", "agent.use", "tool.recheck"]);
  assert.deepEqual(item.fixes.slice(1, 3).map((fix) => fix.arg), ["OMP", "Claude Code"]);
  const after = only(shape((i) => ((i.form.harness = "Pi"), (i.outcomes = { Pi: "npm could not install Pi. Open the setup log." }))), "agent.missing");
  assert.equal(after.text, "npm could not install Pi. Open the setup log.");
});

test("agent.compat names the agent and the source and lists the ways out", () => {
  const result = shape((i) => {
    hosted(i);
    i.form.harness = "Claude Code";
    i.compat = i.accounts[0].compat;
    i.accounts.push({ connection_id: "or1", name: "Personal OpenRouter", provider: "OpenRouter", ready: true, compat: { "Claude Code": ok() } });
  });
  const item = only(result, "agent.compat");
  assert.equal(item.level, "block");
  assert.equal(item.title, "Claude Code cannot use Work Groq");
  assert.equal(item.text, "Claude Code needs the Anthropic Messages API, which Work Groq does not offer; choose OMP, Pi or opencode, or another source.");
  assert.equal(item.needs, "an agent that works with Work Groq");
  assert.deepEqual(runs(item), ["agent.use", "agent.use", "source.use"]);
  assert.equal(item.fixes[0].primary, true);
  assert.equal(item.fixes[2].arg, "or1");
  const hermes = only(shape((i) => (i.form.harness = "Hermes")), "agent.compat");
  assert.ok(runs(hermes).includes("source.use"));
  assert.equal(hermes.fixes[hermes.fixes.length - 1].arg, "Native login");
  const lmstudio = only(
    shape((i) => {
      attached(i, { compat: { Codex: { ok: false, reason: "Codex needs the OpenAI Responses API, which LM Studio does not offer; choose OMP, Pi or opencode, or another source." } } });
      i.form.harness = "Codex";
      i.compat = i.accounts[0].compat;
    }),
    "agent.compat",
  );
  assert.ok(runs(lmstudio).includes("source.refresh"));
});

test("agent.unconfirmed", () => {
  const item = only(shape((i) => (i.form.harness = "Codex", (i.compat.Codex = ok("Local model is not confirmed to offer the OpenAI Responses API, so Codex may fail to connect.")))), "agent.unconfirmed");
  assert.equal(item.level, "warn");
  assert.equal(item.text, "Local model is not confirmed to offer the OpenAI Responses API, so Codex may fail to connect.");
});

test("agent.account", () => {
  const codex = only(shape((i) => Object.assign(i.form, { provider: "Native login", harness: "Codex" })), "agent.account");
  assert.equal(codex.level, "info");
  assert.equal(codex.text, "Use device sign-in to connect a ChatGPT account.");
  assert.deepEqual(runs(codex), ["account.login"]);
  const claude = only(shape((i) => Object.assign(i.form, { provider: "Native login", harness: "Claude Code" })), "agent.account");
  assert.equal(claude.text, "Claude Code uses its own sign-in. It asks you to sign in if it needs to.");
});

test("setup.consent", () => {
  const item = only(shape((i) => (i.consent = { needed: true, granted: false, digest: "d", items: [{}] })), "setup.consent");
  assert.equal(item.slot, "setup");
  assert.equal(item.level, "block");
  assert.equal(item.text, "This folder defines programs that OMP will run. Review them before the first launch.");
  assert.equal(item.needs, "your review of this folder's programs");
  assert.deepEqual(runs(item), ["consent.open"]);
});

test("setup.warnings, one and several", () => {
  const one = only(shape((i) => (i.resolve.warnings = ["Both Workbench and .mcp.json define search; Claude Code decides which one runs."])), "setup.warnings");
  assert.equal(one.text, "Both Workbench and .mcp.json define search; Claude Code decides which one runs.");
  assert.deepEqual(runs(one), ["go.project"]);
  const many = only(shape((i) => (i.resolve.warnings = ["a", "b", "c"])), "setup.warnings");
  assert.equal(many.text, "3 things to check in Project setup.");
});

test("setup.instructions with and without a kit hint", () => {
  const plain = only(shape((i) => (i.resolve.instructions = [])), "setup.instructions");
  assert.equal(plain.level, "info");
  assert.equal(plain.text, "No instruction file for OMP in this folder yet.");
  assert.deepEqual(runs(plain), ["go.instructions"]);
  const kit = only(shape((i) => ((i.resolve.instructions = []), (i.kitHint = { id: "web", label: "Web app" }))), "setup.instructions");
  assert.deepEqual(runs(kit), ["go.kit", "go.instructions"]);
  assert.equal(kit.fixes[0].label, "Preview the Web app kit");
});

test("setup.tool-prereq", () => {
  const item = only(shape((i) => (i.form.blender = true)), "setup.tool-prereq");
  assert.equal(item.level, "warn");
  assert.equal(item.text, "The Blender tool needs Blender, which is not installed.");
  assert.deepEqual(runs(item), ["go.setup", "tool.off"]);
  assert.deepEqual(item.fixes.map((fix) => fix.arg), ["Blender", "blender"]);
  const docker = only(shape((i) => ((i.form.firecrawl = true), (i.tools.Docker = ""))), "setup.tool-prereq");
  assert.equal(docker.text, "Page crawling needs Docker, which is not installed.");
});

test("setup.tools-skipped", () => {
  const item = only(
    shape((i) => {
      i.form.harness = "Codex";
      i.resolve.mcp.launch.push({ name: "jupyter", status: "skipped", reason: "Codex does not receive registered servers that carry arguments." });
    }),
    "setup.tools-skipped",
  );
  assert.equal(item.text, "1 tool will not reach Codex. Codex does not receive registered servers that carry arguments.");
  assert.deepEqual(runs(item), ["go.mcp"]);
});

test("setup.stale", () => {
  const item = only(shape((i) => (i.folder.stale = ["harness", "plugin:jupyter"])), "setup.stale");
  assert.equal(item.level, "warn");
  assert.equal(item.text, "Some remembered choices no longer exist: the agent and the jupyter tool.");
  assert.deepEqual(item.fixes, []);
});

test("the needs line joins one phrase per blocked slot in slot order", () => {
  const result = shape((i) => {
    hosted(i, { ready: false });
    i.form.harness = "Claude Code";
    i.compat = i.accounts[0].compat;
  });
  assert.equal(result.ok, false);
  assert.equal(result.needs, "Launch needs: a key for Work Groq, an agent that works with Work Groq.");
  assert.equal(result.slots.model.level, "block");
  assert.equal(result.slots.agent.level, "block");
  assert.equal(result.slots.project.level, "ok");
  assert.equal(result.slots.model.summary, "Work Groq · llama-4");
});

test("warn and info items do not stop a launch", () => {
  const result = shape((i) => {
    i.folder.stale = ["harness"];
    i.resolve.instructions = [];
    i.form.workspace = "/data/workspace";
  });
  assert.equal(result.ok, true);
  assert.equal(result.slots.setup.level, "warn");
});

test("evaluate has no reference to window or document", () => {
  const text = fs.readFileSync(path.join(ui, "readiness.js"), "utf8");
  const pure = text.slice(0, text.indexOf("const listeners"));
  assert.equal(/\b(window|document)\b/.test(pure), false);
});
