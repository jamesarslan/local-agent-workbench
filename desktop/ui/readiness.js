// Readiness: one pure function from the launch choices and what the backend reported to the items, slot levels and "Launch needs:" line (ui.md 6.1, 9.5).
// Fix arguments: source.attach and source.probe take a port; source.key, source.oauth, source.refresh, source.use, model.browse and machine.reconnect a connection_id
// (source.use also "Native login"); source.keypage a provider; model.install a catalog entry id; agent.use, tool.install, account.login and consent.open an agent;
// go.machines a host; go.setup a tool name; tool.off a form key; go.kit a kit id. An item may carry `task` (a job handle drawn as a task row) and a fix may carry
// `field: {label, type}` (an input whose value reaches the fix as ctx.value).
// Inputs beyond ui.md 9.5: `outcomes` ({agent: message} after a failed install), `subscription` (the studio-subscriptions sentence) and `now` (ms).
(function (root) {
  "use strict";
  const AW = (root.AW = root.AW || {});
  const LOCAL = "Managed local";
  const NATIVE = "Native login";
  const ATTACHED = "Attached endpoint";
  const SLOTS = ["project", "model", "agent", "setup"];
  const STOPS = new Set(["block", "work"]);
  const KEY_PAGES = new Set(["OpenAI", "Groq", "DeepSeek", "OpenRouter"]);
  const PREREQS = [
    { key: "firecrawl", tool: "Docker", text: "Page crawling needs Docker, which is not installed." },
    { key: "blender", tool: "Blender", text: "The Blender tool needs Blender, which is not installed." },
  ];
  const STALE = {
    harness: "the agent", provider: "the model source", connection_id: "the connection", auth_provider: "the sign-in provider", preset: "the local model setup",
    remote_model: "the model", image_route: "the reference image setting", plugins: "the tool list", search: "web search", firecrawl: "page crawling",
    blender: "Blender", desktop: "Windows desktop",
  };
  const LOOPBACK = /^[a-z]+:\/\/(?:127\.\d+\.\d+\.\d+|localhost|\[::1\])(?::\d+)?(?:\/|$)/i;

  const list = (value) => (Array.isArray(value) ? value : []);
  const text = (value) => (typeof value === "string" ? value : "");
  const base = (path) => text(path).split(/[\\/]+/).filter(Boolean).pop() || "";
  const fmt = () => AW.fmt || {};
  const bytes = (n) => (fmt().bytes ? fmt().bytes(n) : "");
  const names = (items) => (fmt().list ? fmt().list(items) : items.join(", "));

  function sourceName(form, row, sourceTable) {
    if (row && row.name) return row.name;
    if (form.provider === LOCAL) return "Local model";
    if (form.provider === NATIVE) return "Agent account";
    const found = list(sourceTable && sourceTable.providers).find((item) => item.id === form.provider);
    return (found && found.label) || form.provider || "this source";
  }

  function chosenFile(form, config) {
    const preset = form.preset && config.presets ? config.presets[form.preset] : null;
    if (preset) return base(preset.model);
    return base(config.current && config.current.model);
  }

  function catalogEntry(recommend, file) {
    if (!recommend || !file) return null;
    return [recommend.pick, ...list(recommend.alternatives)].find((entry) => entry && entry.installed && base(entry.installed.model) === file) || null;
  }

  function progressOf(record) {
    const progress = (record && record.progress) || {};
    const done = Number(progress.done);
    const total = Number(progress.total);
    return Number.isFinite(done) && total > 0 ? Math.max(0, Math.min(100, Math.round((done / total) * 100))) : null;
  }

  function projectItems(input, form, add) {
    const folder = input.folder;
    if (!form.workspace) {
      add({ id: "project.missing", slot: "project", level: "block", text: "Choose a project folder.", needs: "a project folder", fixes: [{ label: "Choose folder…", run: "project.pick", primary: true }] });
      return;
    }
    const gone = folder && folder.path && !folder.key;
    if (input.folderError || gone) {
      const sentence = input.folderError && input.folderError.message ? input.folderError.message : "The folder " + form.workspace + " no longer exists. Choose another folder.";
      add({ id: "project.gone", slot: "project", level: "block", text: sentence, needs: "a project folder", fixes: [{ label: "Choose folder…", run: "project.pick", primary: true }] });
      return;
    }
    if (input.scratch && sameFolder(form.workspace, input.scratch))
      add({ id: "project.scratch", slot: "project", level: "info", text: "This is the scratch folder inside Agent Workbench's own data.", fixes: [{ label: "Choose another folder…", run: "project.pick" }] });
  }

  function sameFolder(a, b) {
    const trim = (path) => text(path).replace(/[\\/]+$/, "");
    return trim(a) !== "" && trim(a) === trim(b);
  }

  function localItems(input, form, config, add) {
    const engine = input.engine;
    const recommend = input.recommend;
    const pick = recommend && recommend.pick;
    const file = chosenFile(form, config);
    const live = input.live || {};
    const running = list(input.jobs).filter((record) => record && record.state === "running" && (record.action === "engine-install" || record.action === "model-download"));
    if (running.length) {
      const record = running.find((item) => item.action === "model-download" && (!pick || item.key === "model:" + pick.id)) || running[0];
      const percent = progressOf(record);
      const what = record.action === "engine-install" ? "the engine install to finish" : "the model download to finish";
      add({ id: "model.downloading", slot: "model", level: "work", task: record.handle, needs: what + (percent === null ? "" : " (" + percent + "%)"), fixes: [] });
    } else if (!file) {
      const usable = list(input.accounts).some((row) => row && row.provider !== NATIVE && row.ready !== false);
      if (!usable) {
        const found = list(input.scan && input.scan.endpoints)[0];
        const fixes = [];
        if (found) fixes.push({ label: "Use " + found.label, run: "source.attach", arg: found.port, primary: true });
        if (pick) fixes.push({ label: "Download " + pick.label + sized(pick.download_bytes), run: "model.install", arg: pick.id, primary: !found });
        fixes.push({ label: "Scan this computer", run: "source.scan" }, { label: "Add a connection…", run: "go.accounts" });
        const sentence = "Choose where the model runs." + (found ? " " + found.label + " is running on this computer." : "");
        add({ id: "model.none", slot: "model", level: "block", text: sentence, needs: "a model", fixes });
      } else if (engine && engine.engine_ready === false) engineItem(engine, add);
      else {
        const fixes = [];
        if (pick) fixes.push({ label: "Download " + pick.label + sized(pick.download_bytes), run: "model.install", arg: pick.id, primary: true });
        fixes.push({ label: "Open Model library", run: "go.models" });
        add({ id: "model.weights", slot: "model", level: "block", text: "No local model is chosen.", needs: "a local model", fixes });
      }
    } else if (engine && engine.engine_ready === false) engineItem(engine, add);
    if (live.label === "Port in use") {
      const port = live.port;
      add({
        id: "model.port", slot: "model", level: "block", text: "Port " + port + " is used by another program, so the local model cannot start.", needs: "a free port",
        fixes: [{ label: "Attach the server on port " + port, run: "source.probe", arg: port, primary: true }, { label: "Run Doctor", run: "go.doctor" }],
      });
    } else if (live.owned && input.match === false) {
      const setup = form.preset || file || "your setup";
      add({
        id: "model.conflict", slot: "model", level: "block", text: "A different local model is serving: " + (live.model || "an unknown model") + ".", needs: "one local model",
        fixes: [{ label: "Use the running model", run: "server.adopt", primary: true }, { label: "Stop it and load " + setup, run: "server.swap" }],
      });
    }
    const entry = catalogEntry(recommend, file);
    const fit = entry && entry.sizing ? entry.sizing.fit : "";
    if (fit && fit !== "comfortable") {
      const sizing = entry.sizing;
      const sentence =
        entry === pick && text(recommend.reason) ? recommend.reason
        : Number(sizing.need_gib) > 0 && Number(sizing.usable_gib) > 0 ? entry.label + " needs about " + Number(sizing.need_gib).toFixed(1) + " GiB and this computer can give it " + Number(sizing.usable_gib).toFixed(1) + " GiB."
        : entry.label + " may not fit in this computer's memory.";
      add({ id: "model.fit", slot: "model", level: "warn", text: sentence, fixes: [{ label: "Other models that fit", run: "go.models" }] });
    }
  }

  const sized = (n) => (Number(n) > 0 ? " (" + bytes(n) + ")" : "");

  function engineItem(engine, add) {
    if (engine.supported === false) {
      add({ id: "model.engine", slot: "model", level: "block", text: text(engine.reason) || "The engine for local models cannot run on this computer.", needs: "the local engine", fixes: [{ label: "Add a connection…", run: "go.accounts" }] });
      return;
    }
    add({
      id: "model.engine", slot: "model", level: "block", text: "The engine for local models is not installed yet" + sized(engine.download_bytes) + ".", needs: "the local engine",
      fixes: [{ label: "Install engine", run: "engine.install", primary: true }],
    });
  }

  function remoteItems(input, form, row, name, add) {
    const id = form.connection_id;
    if (form.connection_id && !row && Array.isArray(input.accounts) && input.config) {
      add({ id: "model.saved-missing", slot: "model", level: "block", text: "The " + (form.provider || "saved") + " connection this folder used is no longer saved.", needs: "a model source", fixes: [{ label: "Open Connections", run: "go.accounts", primary: true }] });
      return;
    }
    if (!row) return;
    if (row.ready === false) {
      const fixes = [{ label: "Save key", run: "source.key", arg: id, primary: true, field: { label: "API key for " + name, type: "password" } }];
      if (KEY_PAGES.has(row.provider)) fixes.push({ label: "Get an API key ↗", run: "source.keypage", arg: row.provider });
      if (row.provider === "OpenRouter") fixes.push({ label: "Sign in with OpenRouter ↗", run: "source.oauth", arg: id });
      add({ id: "model.credential", slot: "model", level: "block", text: name + " needs an API key.", needs: "a key for " + name, fixes });
    }
    if (!form.remote_model)
      add({ id: "model.id", slot: "model", level: "block", text: "Choose a model for " + name + ".", needs: "a model for " + name, fixes: [{ label: "Browse models", run: "model.browse", arg: id, primary: true }] });
    const tunnel = list(input.forwards).find((item) => item && item.connection_id === id);
    if (tunnel && tunnel.alive === false) {
      const machine = tunnel.label || tunnel.host || "the machine";
      add({
        id: "model.tunnel", slot: "model", level: "block", text: "The SSH tunnel to " + machine + " is down.", needs: "the tunnel to " + machine,
        fixes: [{ label: "Reconnect", run: "machine.reconnect", arg: id, primary: true }, { label: "Open machine", run: "go.machines", arg: tunnel.host }],
      });
    }
    const failure = input.unreachable && input.unreachable[id];
    if (failure) {
      add({
        id: "model.unreachable", slot: "model", level: "block", text: text(failure.message) || name + " did not answer.", needs: name + " to answer",
        fixes: [{ label: "Check again", run: "source.refresh", arg: id, primary: true }, { label: "Choose another source", run: "focus.source" }],
      });
    } else if (row.provider === ATTACHED && !row.host && LOOPBACK.test(text(row.endpoint)) && input.scan && input.scan.received) {
      const seen = list(input.scan.endpoints).some((item) => item && (item.connection_id === id || (item.base && sameFolder(item.base, row.endpoint))));
      if (!seen) {
        const ago = fmt().ago ? fmt().ago(input.scan.received, input.now) : "";
        add({ id: "model.unseen", slot: "model", level: "info", text: name + " was not answering " + (ago || "at the last check") + ". It is checked again when you launch.", fixes: [{ label: "Check again", run: "source.refresh", arg: id }] });
      }
    }
  }

  function agentItems(input, form, config, row, name, add) {
    const agent = form.harness;
    if (!agent) return;
    const present = config.cli_present || {};
    const compat = input.compat && typeof input.compat === "object" ? input.compat : null;
    const accepts = (who) => !compat || !compat[who] || compat[who].ok !== false;
    const others = list(config.agents)
      .filter((who) => who !== agent && present[who] && accepts(who))
      .sort((a, b) => Boolean(compat && compat[a] && compat[a].reason) - Boolean(compat && compat[b] && compat[b].reason))
      .slice(0, 2);
    const attached = row && row.provider === ATTACHED ? row : null;
    if (present[agent] === false) {
      const outcome = input.outcomes && text(input.outcomes[agent]);
      add({
        id: "agent.missing", slot: "agent", level: "block", text: outcome || agent + " is not installed on this computer.", needs: agent + " installed",
        fixes: [
          { label: "Install " + agent, run: "tool.install", arg: agent, primary: true },
          ...others.map((who) => ({ label: "Use " + who, run: "agent.use", arg: who })),
          { label: "Recheck installations", run: "tool.recheck" },
        ],
      });
    }
    const verdict = compat && compat[agent];
    if (verdict && verdict.ok === false) {
      const fixes = others.map((who, index) => ({ label: "Use " + who, run: "agent.use", arg: who, primary: index === 0 }));
      const sources = list(input.accounts)
        .filter((item) => item && item.connection_id !== form.connection_id && item.provider !== NATIVE && item.ready !== false && item.compat && item.compat[agent] && item.compat[agent].ok)
        .slice(0, 2);
      for (const item of sources) fixes.push({ label: "Use " + (item.name || item.provider), run: "source.use", arg: item.connection_id, primary: !fixes.length });
      if (attached) fixes.push({ label: "Check " + name + " again", run: "source.refresh", arg: attached.connection_id });
      const harness = list(input.sourceTable && input.sourceTable.harnesses).find((item) => item && item.id === agent);
      if (harness && harness.native_only) fixes.push({ label: "Use Agent account", run: "source.use", arg: NATIVE, primary: !fixes.length });
      add({ id: "agent.compat", slot: "agent", level: "block", title: agent + " cannot use " + name, text: text(verdict.reason), needs: "an agent that works with " + name, fixes });
    } else if (verdict && verdict.reason) {
      add({ id: "agent.unconfirmed", slot: "agent", level: "warn", text: verdict.reason, fixes: attached ? [{ label: "Check " + name + " again", run: "source.refresh", arg: attached.connection_id }] : [] });
    }
    if (form.provider === NATIVE) {
      const sentence = agent === "Codex" && text(input.subscription) ? input.subscription : agent + " uses its own sign-in. It asks you to sign in if it needs to.";
      add({ id: "agent.account", slot: "agent", level: "info", text: sentence, fixes: [{ label: "Open " + agent + " sign-in", run: "account.login", arg: agent }] });
    }
  }

  function setupItems(input, form, add) {
    const agent = form.harness || "the agent";
    const consent = input.consent;
    if (consent && consent.needed && !consent.granted)
      add({
        id: "setup.consent", slot: "setup", level: "block", text: "This folder defines programs that " + agent + " will run. Review them before the first launch.",
        needs: "your review of this folder's programs", fixes: [{ label: "Review and allow…", run: "consent.open", arg: form.harness, primary: true }],
      });
    const resolve = input.resolve;
    const warnings = list(resolve && resolve.warnings).filter((item) => typeof item === "string" && item);
    if (warnings.length)
      add({ id: "setup.warnings", slot: "setup", level: "warn", text: warnings.length === 1 ? warnings[0] : warnings.length + " things to check in Project setup.", fixes: [{ label: "Open Project setup", run: "go.project" }] });
    if (resolve && !list(resolve.instructions).some((row) => row && row.status === "read")) {
      const fixes = [];
      if (input.kitHint && input.kitHint.id) fixes.push({ label: "Preview the " + (input.kitHint.label || input.kitHint.id) + " kit", run: "go.kit", arg: input.kitHint.id });
      fixes.push({ label: "Create AGENTS.md", run: "go.instructions" });
      add({ id: "setup.instructions", slot: "setup", level: "info", text: "No instruction file for " + agent + " in this folder yet.", fixes });
    }
    const tools = input.tools;
    if (tools && typeof tools === "object")
      for (const need of PREREQS)
        if (form[need.key] && Object.hasOwn(tools, need.tool) && !tools[need.tool])
          add({
            id: "setup.tool-prereq", slot: "setup", level: "warn", text: need.text,
            fixes: [{ label: "Open Advanced → Setup", run: "go.setup", arg: need.tool }, { label: "Turn it off", run: "tool.off", arg: need.key }],
          });
    const skipped = list(resolve && resolve.mcp && resolve.mcp.launch).filter((row) => row && row.status === "skipped");
    if (skipped.length) {
      const head = skipped.length === 1 ? "1 tool will not reach " + agent + "." : skipped.length + " tools will not reach " + agent + ".";
      const reasons = skipped.map((row) => text(row.reason)).filter(Boolean);
      add({ id: "setup.tools-skipped", slot: "setup", level: "warn", text: [head, ...reasons].join(" "), fixes: [{ label: "Open MCP tools", run: "go.mcp" }] });
    }
    const stale = list(input.folder && input.folder.stale).map((key) => {
      const value = text(key);
      return value.startsWith("plugin:") ? "the " + value.slice(7) + " tool" : STALE[value] || value;
    });
    if (stale.length) add({ id: "setup.stale", slot: "setup", level: "warn", text: "Some remembered choices no longer exist: " + names(stale.filter(Boolean)) + ".", fixes: [] });
  }

  function summaries(input, form, config, row, name) {
    const project = !form.workspace ? "" : input.scratch && sameFolder(form.workspace, input.scratch) ? "Scratch folder" : base(form.workspace);
    let model = name;
    if (form.provider === LOCAL) {
      const file = chosenFile(form, config);
      model = file ? "Local model · " + file : "Local model";
    } else if (form.provider !== NATIVE && form.remote_model) model = name + " · " + form.remote_model;
    const present = config.cli_present || {};
    const agent = form.harness ? form.harness + (present[form.harness] === false ? " · not installed" : "") : "";
    const parts = [];
    const resolve = input.resolve;
    if (resolve) {
      const read = list(resolve.instructions).filter((item) => item && item.status === "read");
      if (read.length === 1) parts.push(base(read[0].path) || "1 instruction file");
      else if (read.length > 1) parts.push(read.length + " instruction files");
      const skills = list(resolve.skills).length;
      if (skills) parts.push(skills === 1 ? "1 skill" : skills + " skills");
      const sent = list(resolve.mcp && resolve.mcp.launch).filter((item) => item && item.status === "sent").length;
      parts.push(sent === 1 ? "1 tool on" : sent + " tools on");
    }
    return { project, model, agent, setup: parts.join(" · ") };
  }

  function evaluate(input) {
    const source = input || {};
    const form = source.form || {};
    const config = source.config || {};
    const items = [];
    const add = (item) => items.push(item);
    const accounts = list(source.accounts);
    const row = form.connection_id ? accounts.find((item) => item && item.connection_id === form.connection_id) || null : null;
    const name = sourceName(form, row, source.sourceTable);
    projectItems(source, form, add);
    if (source.config) {
      if (form.provider === LOCAL || !form.provider) localItems(source, form, config, add);
      else if (form.provider !== NATIVE) remoteItems(source, form, row, name, add);
      agentItems(source, form, config, row, name, add);
    }
    setupItems(source, form, add);
    const summary = summaries(source, form, config, row, name);
    const slots = {};
    for (const slot of SLOTS) {
      const mine = items.filter((item) => item.slot === slot);
      const level = ["block", "work", "warn"].find((kind) => mine.some((item) => item.level === kind)) || "ok";
      slots[slot] = { level, summary: summary[slot] };
    }
    const needs = SLOTS.map((slot) => (items.find((item) => item.slot === slot && STOPS.has(item.level)) || {}).needs).filter(Boolean);
    return { ok: needs.length === 0, needs: needs.length ? "Launch needs: " + needs.join(", ") + "." : "", slots, items };
  }

  const listeners = new Set();
  let cached = null;
  let queued = false;

  function current() {
    if (!cached) {
      const state = AW.state || {};
      cached = evaluate({
        ...state,
        form: AW.form.get(),
        folderError: AW.store.error("folder"),
        jobs: AW.jobs ? AW.jobs.list() : [],
        scratch: AW.project.scratch(),
        now: Date.now(),
      });
    }
    return cached;
  }

  function changed() {
    if (queued) return;
    queued = true;
    Promise.resolve().then(() => {
      queued = false;
      const before = JSON.stringify(cached);
      cached = null;
      const next = current();
      if (JSON.stringify(next) === before) return;
      for (const fn of [...listeners]) {
        try {
          fn(next);
        } catch (thrown) {
          console.error(thrown);
        }
      }
    });
  }

  const INPUTS = ["config", "live", "folder", "accounts", "scan", "forwards", "compat", "consent", "resolve", "match", "engine", "recommend", "tools", "sourceTable", "kitHint", "unreachable", "outcomes", "subscription"];

  AW.readiness = {
    evaluate,
    current,
    on(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    watch() {
      AW.store.on(INPUTS, changed);
      AW.form.on(changed);
      if (AW.jobs) AW.jobs.on(changed);
    },
  };
  if (AW.shell) AW.shell.register(() => AW.readiness.watch());
})(typeof window === "undefined" ? globalThis : window);
