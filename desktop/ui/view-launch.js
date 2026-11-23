// Launch view (ui.md 5.1): the project and model, the agent cards, the session tools, readiness, the action panel and recent sessions.
// The static controls of index.html stay in place; this file fills, labels and arranges them around AW.form.
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const LOCAL = "Managed local";
  const NATIVE = "Native login";
  const ATTACHED = "Attached endpoint";
  const BUILTIN = [
    { key: "search", name: "search" },
    { key: "firecrawl", name: "firecrawl", tool: "Docker", text: "Needs Docker, which is not installed." },
    { key: "blender", name: "blender", tool: "Blender", text: "Needs Blender, which is not installed." },
    { key: "desktop", name: "desktop" },
  ];
  const CONTEXT_FROM = {
    server: "reported by the server",
    node: "reported by the machine",
    user: "set by you",
    listing: "from the provider's list",
    catalog: "from the catalog",
    local: "set for the local model",
    default: "default",
  };
  const STALE = {
    harness: "the agent", provider: "the model source", connection_id: "the connection", auth_provider: "the sign-in provider", preset: "the local model setup",
    remote_model: "the model", image_route: "the reference image setting", plugins: "the tool list", search: "web search", firecrawl: "page crawling",
    blender: "Blender", desktop: "Windows desktop",
  };
  const KEY_WORD = { vault: "Key saved", memory: "Key kept until you quit" };
  const AGO_MS = 15000;

  const bar = window.matchMedia("(max-width: 899px), (max-height: 699px)");
  const list = (value) => (Array.isArray(value) ? value : []);
  const trimSlash = (text) => String(text || "").replace(/\/+$/, "");
  const dismissed = new Set();
  const signatures = new Map();
  let readiness = null;
  let recent = null;
  let recentFor = null;
  let agoTimer = null;
  let stepTimer = null;
  let shown = false;
  const parts = {};

  function changed(key, value) {
    const text = JSON.stringify(value);
    if (signatures.get(key) === text) return false;
    signatures.set(key, text);
    return true;
  }

  const config = () => AW.state.config || {};
  const accounts = () => list(AW.state.accounts);
  const connection = () => AW.form.connection();

  function providerLabel(provider) {
    const found = list(AW.state.sourceTable && AW.state.sourceTable.providers).find((item) => item.id === provider);
    return (found && found.label) || provider;
  }

  function localFile(form) {
    const c = config();
    const preset = form.preset && c.presets ? c.presets[form.preset] : null;
    return AW.fmt.folder(preset ? preset.model : c.current && c.current.model);
  }

  const modelLabel = (form) => (form.provider === LOCAL ? localFile(form) : form.remote_model);

  function answered(row) {
    const scan = AW.state.scan;
    if (!scan || row.host) return false;
    return list(scan.endpoints).some((item) => item && (item.connection_id === row.connection_id || (item.base && trimSlash(item.base) === trimSlash(row.endpoint))));
  }

  function foundServers() {
    const attached = accounts().filter((row) => row.provider === ATTACHED).map((row) => trimSlash(row.endpoint));
    return list(AW.state.scan && AW.state.scan.endpoints).filter((item) => item && !item.connection_id && !attached.includes(trimSlash(item.base)));
  }

  // Project and model

  function paintProject(form) {
    const button = AW.$("launch-project");
    const path = form.workspace;
    if (!changed("project", path)) return;
    button.replaceChildren();
    if (!path) button.append(el("span", { class: "launch-project-name", text: "Choose a project folder…" }));
    else button.append(el("span", { class: "launch-project-name", text: AW.project.name(path) }), el("span", { class: "launch-project-path", text: AW.fmt.middle(AW.fmt.path(path), 64) }));
    button.append(AW.ui.icon("caret"));
    button.title = path ? AW.fmt.path(path) + " · choose another folder" : "Choose a project folder";
  }

  function sourceOptions() {
    const live = AW.state.live || {};
    const unreachable = AW.state.unreachable || {};
    const running = [];
    const saved = [];
    const agents = [{ value: NATIVE, text: "Agent account" }];
    const local = { value: LOCAL, text: "Local model" + (live.label === "Serving" ? " · serving" : "") };
    (live.label === "Serving" ? running : saved).push(local);
    for (const row of accounts()) {
      const text = (row.name || row.provider) + (row.ready === false ? " · needs a key" : "");
      const option = { value: row.connection_id, text };
      if (row.provider === NATIVE) agents.push(option);
      else if (row.provider === ATTACHED && answered(row) && !unreachable[row.connection_id]) running.push(option);
      else saved.push(option);
    }
    for (const found of foundServers()) running.push({ value: "found:" + found.port, text: (found.label || "Server") + " · port " + found.port + " · attach" });
    return [
      ["Running now", running],
      ["Saved", saved],
      ["Agent accounts", agents],
    ].filter(([, rows]) => rows.length);
  }

  function paintSource(form) {
    const select = AW.$("source");
    const groups = sourceOptions();
    const value = form.connection_id || form.provider || LOCAL;
    const known = groups.some(([, rows]) => rows.some((row) => row.value === value));
    const missing = known ? null : { value, text: (form.provider || "Saved") + " connection · no longer saved" };
    if (changed("source", [groups, missing])) {
      const nodes = groups.map(([label, rows]) => el("optgroup", { attrs: { label } }, rows.map((row) => el("option", { value: row.value, text: row.text }))));
      if (missing) nodes.unshift(el("option", { value: missing.value, text: missing.text }));
      nodes.push(el("option", { value: "add", text: "Add a connection…" }));
      select.replaceChildren(...nodes);
    }
    if (select.value !== value) select.value = value;
    paintHealth(form);
  }

  function health(form) {
    const live = AW.state.live || {};
    const row = connection();
    if (form.provider === LOCAL) {
      if (live.label === "Serving") return { mark: "ok", word: "Serving" + (live.model ? " · " + live.model : "") };
      if (live.label === "Starting") return { mark: "busy", word: "Loading…" + (live.started ? " " + Math.max(0, Math.round(Date.now() / 1000 - Number(live.started))) + " s" : "") };
      if (live.label === "Port in use") return { mark: "warn", word: "Port " + live.port + " in use" };
      if (live.label === "Unexpected model") return { mark: "warn", word: "Unexpected model" };
      if (live.label === "Starting or unavailable") return { mark: "busy", word: "Starting or unavailable" };
      return { mark: "idle", word: "Stopped" };
    }
    if (form.provider === NATIVE) return { mark: "idle", word: "Agent account" };
    if (!row) return null;
    if ((AW.state.unreachable || {})[row.connection_id]) return { mark: "down", word: "Not answering at the last check" };
    if (row.provider === ATTACHED) {
      const tunnel = list(AW.state.forwards).find((item) => item && item.connection_id === row.connection_id);
      if (tunnel && tunnel.alive === false) return { mark: "down", word: "Tunnel down" };
      if (row.host) return { mark: "idle", word: "Through an SSH tunnel to " + row.host };
      const scan = AW.state.scan;
      if (!scan || !scan.received) return { mark: "idle", word: "Not checked yet" };
      const ago = " · checked " + AW.fmt.ago(scan.received);
      return answered(row) ? { mark: "ok", word: "Answering" + ago } : { mark: "down", word: "Not answering" + ago };
    }
    if (row.ready === false) return { mark: "warn", word: "Needs a key" };
    if (row.key_source === "env") {
      const found = list(AW.state.sourceTable && AW.state.sourceTable.providers).find((item) => item.id === row.provider);
      return { mark: "ok", word: found && found.key_env ? "Key from " + found.key_env : "Key from the environment" };
    }
    return { mark: "ok", word: KEY_WORD[row.key_source] || "Key saved" };
  }

  function paintHealth(form) {
    const host = AW.$("source-health");
    const state = health(form);
    host.hidden = !state;
    if (state && changed("health", state)) host.replaceChildren(AW.ui.mark(state.mark), el("span", { text: state.word }));
    const row = connection();
    AW.$("launch-machine").hidden = !(row && row.host);
  }

  function paintModel(form) {
    const c = config();
    const provider = AW.form.provider();
    const local = provider === LOCAL;
    const native = provider === NATIVE;
    AW.$("local-field").hidden = !local;
    AW.$("remote-field").hidden = local;
    if (local) {
      const preset = AW.$("preset");
      const names = Object.keys(c.presets || {});
      if (changed("presets", names)) preset.replaceChildren(el("option", { value: "", text: "Current configuration" }), ...names.map((name) => el("option", { value: name, text: name })));
      if (preset.value !== form.preset) preset.value = form.preset;
      const spec = form.preset && c.presets ? c.presets[form.preset] || {} : c.current || {};
      const file = AW.fmt.folder(spec.model);
      const context = AW.fmt.context(spec.context);
      AW.$("preset-info").textContent = file ? [file, spec.engine, context && context + " context"].filter(Boolean).join(" · ") : "No local model is chosen yet.";
      return;
    }
    parts.remoteLabel.textContent = native ? "Model (optional)" : "Model";
    const remote = AW.$("remote");
    remote.placeholder = native ? "Optional agent default" : "Choose or enter a model";
    AW.$("list-models").hidden = native;
    parts.nativeHint.hidden = !native;
    AW.$("routing-hint").hidden = form.remote_model !== "openrouter/auto";
    const context = AW.state.context;
    const size = context && !native && form.remote_model ? AW.fmt.context(context.context) : "";
    const hint = AW.$("model-context");
    hint.hidden = !size;
    hint.textContent = size ? size + " context, " + (CONTEXT_FROM[context.source] || "default") : "";
    const row = connection();
    AW.$("account-link").hidden = !row;
  }

  // Agent cards

  function agents() {
    const all = list(config().agents);
    const table = list(AW.state.sourceTable && AW.state.sourceTable.harnesses).map((item) => item && item.id);
    return table.length ? all.filter((agent) => table.includes(agent)) : all;
  }

  function agentState(agent) {
    const present = config().cli_present || {};
    const compat = AW.state.compat;
    const verdict = compat && compat[agent];
    if (present[agent] === false) return { line: "Not installed", warn: false, off: true, reason: agent + " is not installed on this computer." };
    if (verdict && verdict.ok === false) return { line: "Not with this source", warn: true, off: true, reason: verdict.reason };
    if (verdict && verdict.reason) return { line: "May not work", warn: true, off: false, reason: verdict.reason };
    return { line: "Installed", warn: false, off: false, reason: "" };
  }

  const cards = new Map();
  function card(agent) {
    const input = el("input", { type: "radio", value: agent, id: "launch-agent-" + agent.toLowerCase().replace(/[^a-z0-9]+/g, "-"), attrs: { name: "launch-agent" } });
    const line = el("span", { class: "choice-desc launch-agent-line" });
    const reason = el("span", { class: "choice-reason", id: input.id + "-reason", hidden: true });
    const root = el("label", { class: "choice launch-agent", data: { agent } }, input, el("span", { class: "choice-main" }, el("span", { class: "choice-title", text: agent }), line, reason));
    input.addEventListener("change", () => {
      if (input.checked) AW.form.set({ harness: agent });
    });
    return { root, input, line, reason, signature: "" };
  }

  function paintAgents(form) {
    const host = AW.$("agent-cards");
    const names = agents();
    if (changed("agents", names)) {
      cards.clear();
      host.replaceChildren(...names.map((agent) => {
        const made = card(agent);
        cards.set(agent, made);
        return made.root;
      }));
    }
    for (const [agent, item] of cards) {
      const state = agentState(agent);
      const selected = agent === form.harness;
      if (item.input.checked !== selected) item.input.checked = selected;
      const signature = JSON.stringify([state, selected]);
      if (signature === item.signature) continue;
      item.signature = signature;
      item.line.replaceChildren(...(state.warn ? [AW.ui.mark("warn")] : []), el("span", { text: state.line }));
      for (const node of [item.root, item.input]) {
        if (state.off) node.setAttribute("aria-disabled", "true");
        else node.removeAttribute("aria-disabled");
      }
      const explain = selected && Boolean(state.reason);
      item.reason.hidden = !explain;
      item.reason.textContent = explain ? state.reason : "";
      if (explain) item.input.setAttribute("aria-describedby", item.reason.id);
      else item.input.removeAttribute("aria-describedby");
    }
  }

  // Tools for this session

  function launchRows() {
    const resolve = AW.state.resolve;
    return list(resolve && resolve.mcp && resolve.mcp.launch);
  }

  function noteFor(name, tool, text) {
    const tools = AW.state.tools;
    if (tool && tools && typeof tools === "object" && Object.hasOwn(tools, tool) && !tools[tool]) return text;
    const row = launchRows().find((item) => item && item.name === name && item.status === "skipped");
    return row ? row.reason || "" : "";
  }

  function setNote(input, note, text) {
    note.hidden = !text;
    if (note.textContent !== text) note.textContent = text || "";
    if (text) input.setAttribute("aria-describedby", note.id);
    else input.removeAttribute("aria-describedby");
  }

  function builtinNotes() {
    for (const item of BUILTIN) {
      const input = AW.$(item.key);
      if (!input) continue;
      let note = AW.$(item.key + "-note");
      if (!note) {
        note = el("span", { class: "toggle-note", id: item.key + "-note", hidden: true });
        input.closest(".toggle").append(note);
      }
      setNote(input, note, noteFor(item.name, item.tool, item.text));
    }
  }

  const pendingSwitches = new Set();
  const switchErrors = new Map();
  function extraRows(form) {
    const plugins = String(form.plugins || "").split(",").map((name) => name.trim()).filter(Boolean);
    const project = launchRows()
      .filter((row) => row && row.origin === "project")
      .map((row) => ({ key: "project:" + row.name, name: row.name, scope: "project", on: row.status !== "off", reason: row.status === "skipped" ? row.reason || "" : "" }));
    const registered = list(config().registered_plugins).map((name) => {
      const row = launchRows().find((item) => item && item.name === name && item.origin === "registry" && item.status === "skipped");
      return { key: "registered:" + name, name, scope: "registered", on: plugins.includes(name), reason: row ? row.reason || "" : "" };
    });
    return { project, registered };
  }

  function toggleRegistered(name, on) {
    const names = String(AW.form.get().plugins || "").split(",").map((item) => item.trim()).filter(Boolean);
    const next = on ? [...new Set([...names, name])] : names.filter((item) => item !== name);
    AW.form.set({ plugins: next.join(",") });
  }

  async function toggleProject(item, input, note) {
    if (pendingSwitches.has(item.name)) {
      input.checked = !input.checked;
      return;
    }
    pendingSwitches.add(item.name);
    switchErrors.delete(item.name);
    input.setAttribute("aria-disabled", "true");
    const wanted = input.checked;
    try {
      await AW.request("project-setup-mcp-toggle", { workspace: AW.form.get().workspace, name: item.name, enabled: wanted });
      await AW.store.refresh("resolve", { force: true }).catch(() => null);
    } catch (thrown) {
      input.checked = !wanted;
      switchErrors.set(item.name, AW.asError(thrown).message);
      setNote(input, note, switchErrors.get(item.name));
    } finally {
      pendingSwitches.delete(item.name);
      input.removeAttribute("aria-disabled");
      if (AW.state.config) paintTools(AW.form.get());
    }
  }

  function extraToggle(item) {
    const id = "launch-extra-" + item.scope + "-" + item.name.replace(/[^A-Za-z0-9_-]+/g, "-");
    const input = el("input", { type: "checkbox", class: "switch", role: "switch", id, checked: item.on });
    const note = el("span", { class: "toggle-note", id: id + "-note", hidden: true });
    const scope = item.scope === "project" ? "project" : "this computer";
    const root = el("label", { class: "toggle", data: { key: item.key } }, el("span", { class: "toggle-text" }, item.name, el("small", { text: scope })), input, note);
    input.addEventListener("change", () => {
      if (item.scope === "project") toggleProject(item, input, note);
      else toggleRegistered(item.name, input.checked);
    });
    root._input = input;
    root._note = note;
    return root;
  }

  function paintTools(form) {
    const c = config();
    AW.$("desktop-tool").hidden = !c.windows;
    builtinNotes();
    const rows = extraRows(form);
    const host = AW.$("session-extra");
    const shape = [rows.project.map((row) => row.key), rows.registered.map((row) => row.key)];
    if (changed("extra", shape)) {
      const nodes = [];
      if (rows.project.length) nodes.push(el("p", { class: "launch-extra-caption", text: "Project servers are switched for this folder at once." }));
      for (const row of [...rows.project, ...rows.registered]) nodes.push(extraToggle(row));
      host.replaceChildren(...nodes);
      host.hidden = !nodes.length;
    }
    for (const row of [...rows.project, ...rows.registered]) {
      const node = host.querySelector('[data-key="' + CSS.escape(row.key) + '"]');
      if (!node) continue;
      if (!pendingSwitches.has(row.name) && node._input.checked !== row.on) node._input.checked = row.on;
      if (!pendingSwitches.has(row.name)) setNote(node._input, node._note, (row.scope === "project" && switchErrors.get(row.name)) || row.reason);
    }
    const builtinOn = BUILTIN.filter((item) => form[item.key] && (item.key !== "desktop" || c.windows)).length;
    const on = builtinOn + [...rows.project, ...rows.registered].filter((row) => row.on).length;
    AW.$("tools-count").textContent = on + " on";
    const skipped = launchRows().filter((row) => row && row.status === "skipped").length;
    parts.skipped.hidden = !skipped;
    parts.skipped.textContent = skipped ? (skipped === 1 ? "1 of these will not reach " : skipped + " of these will not reach ") + (form.harness || "the agent") + "." : "";
    paintVisual(form);
  }

  let visualKey = "";
  const visualTicket = AW.ticket();
  const lookVisual = AW.debounce(async (form, key) => {
    const mine = visualTicket.next();
    try {
      const answer = await AW.request("launch-route", form);
      if (!visualTicket.current(mine) || key !== visualKey) return;
      const reader = answer && answer.reader;
      AW.$("visual-preview").textContent =
        "3D editing: " + ((answer && answer.primary) || "choose a model") + " + Blender. " +
        (reader
          ? "Reference images: " + reader + ". Images sent to this endpoint use its normal pricing."
          : "Image support is not confirmed. Choose a vision-capable model, or confirm image support yourself.");
    } catch (thrown) {
      if (visualTicket.current(mine) && key === visualKey) AW.$("visual-preview").textContent = AW.asError(thrown).message;
    }
  }, 220);

  function paintVisual(form) {
    const panel = AW.$("visual-panel");
    const show = Boolean(form.blender);
    panel.hidden = !show;
    const key = JSON.stringify([show, form.provider, form.connection_id, form.remote_model, form.image_route, form.preset]);
    if (key === visualKey) return;
    visualKey = key;
    lookVisual.cancel();
    if (!show) return;
    const choice = AW.$("image-route");
    const pareto = choice.querySelector('[value="pareto"]');
    if (pareto) pareto.disabled = form.provider !== "OpenRouter";
    choice.disabled = form.provider === LOCAL;
    if (form.provider === LOCAL) {
      AW.$("visual-preview").textContent = "Blender builds locally. Image input needs a vision-enabled setup with its matching companion weights.";
      return;
    }
    AW.$("visual-preview").textContent = "Checking image input…";
    lookVisual(form, key);
  }

  // Right column

  const ROW_ERRORS = [
    ["compat", "agent"],
    ["resolve", "setup"],
    ["consent", "setup"],
  ];
  function paintRowErrors() {
    for (const [key, slot] of ROW_ERRORS) {
      const row = AW.$("launch-readiness").querySelector('.ready-row[data-slot="' + slot + '"]');
      if (!row) continue;
      let host = row.querySelector(':scope > .launch-row-error[data-key="' + key + '"]');
      if (!host) {
        host = el("div", { class: "launch-row-error", data: { key } });
        row.append(host);
      }
      const error = AW.store.error(key);
      if (!changed("error:" + key, error)) continue;
      if (error) AW.ui.showError(host, error, { retry: () => AW.store.refresh(key, { force: true }).catch(() => {}) });
      else AW.ui.clearError(host);
    }
  }

  function runningHere(form) {
    return list(AW.state.sessions).filter((row) => row && ["embedded", "native"].includes(row.kind) && row.workspace === form.workspace && row.harness === form.harness).length;
  }

  function onlyConsent(result) {
    const stops = result.items.filter((item) => item.level === "block" || item.level === "work");
    return stops.length > 0 && stops.every((item) => item.id === "setup.consent");
  }

  function paintAction(form) {
    const result = AW.readiness.current();
    if (readiness) readiness.update(result);
    paintRowErrors();
    const launch = AW.$("launch");
    const needs = AW.$("launch-needs");
    const run = AW.launch.run && AW.launch.run();
    const starting = !AW.state.config;
    const here = runningHere(form);
    if (!launch.dataset.pending) {
      const label = starting ? "Starting Agent Workbench…" : !form.harness ? "Launch" : here ? "Launch another " + form.harness : "Launch " + form.harness;
      if (launch.textContent !== label) launch.textContent = label;
    }
    const blocked = starting || !result.ok;
    if (!launch.dataset.job && !launch.dataset.starting && !launch.dataset.pending) {
      if (blocked) launch.setAttribute("aria-disabled", "true");
      else launch.removeAttribute("aria-disabled");
    }
    const save = AW.$("save");
    if (!save.dataset.pending && !save.dataset.starting) {
      if (starting || !form.workspace) save.setAttribute("aria-disabled", "true");
      else save.removeAttribute("aria-disabled");
    }
    const busy = Boolean(run && !run.error);
    const status = starting ? { mark: "busy", text: "Starting Agent Workbench…" } : result.ok ? { mark: "ok", text: "Ready" } : { mark: "warn", text: result.needs };
    if (changed("needs", [status, bar.matches])) {
      needs.replaceChildren(...(bar.matches ? [AW.ui.mark(status.mark)] : []), el("span", { text: status.text }));
      AW.$("launch-needs-dock").textContent = result.ok ? "" : result.needs;
    }
    needs.hidden = busy || (!bar.matches && result.ok && !starting);
    needs.classList.toggle("ready", result.ok && !starting);
    needs.classList.toggle("blocked", !result.ok);
    const saveState = !form.workspace ? "" : AW.form.dirty() ? "Not saved yet." : AW.state.folder && AW.state.folder.known ? "Saved for this folder." : "";
    if (AW.$("save-state").textContent !== saveState) AW.$("save-state").textContent = saveState;
    AW.$("save-state").hidden = !saveState;
    const indicator = here ? here + " running here" : "";
    if (AW.$("session-indicator").textContent !== indicator) AW.$("session-indicator").textContent = indicator;
    AW.$("session-indicator").hidden = !indicator;
    if (parts.where.value !== AW.form.where()) parts.where.value = AW.form.where();
  }

  function stepRow(item, index, run) {
    const node = el("li", { class: "launch-step", data: { state: item.state } }, el("span", { class: "launch-step-number", text: String(index + 1) }));
    if (item.key === "model" && item.handle && item.state === "running") {
      node.append(AW.ui.taskRow(item.handle, { compact: true }).el);
      return node;
    }
    const mark = { waiting: "idle", running: "busy", done: "ok", failed: "down", cancelled: "idle" }[item.state] || "idle";
    const body = el("div", { class: "launch-step-body" }, AW.ui.mark(mark), el("span", { class: "launch-step-title", text: item.title }));
    if (item.state === "running" && item.key === "open") body.append(el("div", { class: "task-text launch-step-text", text: "Opening " + run.agent + "… " + AW.fmt.duration((Date.now() - item.started) / 1000) }));
    node.append(body);
    return node;
  }

  function paintRun() {
    const run = AW.launch.run();
    const host = parts.steps;
    const errors = parts.errors;
    clearInterval(stepTimer);
    stepTimer = null;
    const launch = AW.$("launch");
    if (!run || run.checking || !run.steps.some((item) => item.state !== "waiting")) host.replaceChildren();
    else {
      host.replaceChildren(...run.steps.map((item, index) => stepRow(item, index, run)));
      if (run.steps.some((item) => item.key === "open" && item.state === "running"))
        stepTimer = setInterval(() => {
          const line = host.querySelector(".launch-step-text");
          const step = run.steps.find((item) => item.key === "open");
          if (line && step) line.textContent = "Opening " + run.agent + "… " + AW.fmt.duration((Date.now() - step.started) / 1000);
        }, 1000);
    }
    host.hidden = !host.children.length;
    if (run && run.error) {
      if (changed("run-error", run.error)) {
        const notice = AW.ui.showError(errors, run.error, { retry: () => AW.launch.start({ where: run.where, origin: launch }), tool: run.agent });
        notice.addEventListener("click", () =>
          setTimeout(() => {
            if (!notice.isConnected) AW.launch.clear();
          }),
        );
      }
    } else {
      signatures.delete("run-error");
      AW.ui.clearError(errors);
    }
    paintAction(AW.form.get());
  }

  function onLaunch(event) {
    const button = event.currentTarget;
    if (button.dataset.pending || button.dataset.starting || button.dataset.job || AW.launch.busy()) return;
    const result = AW.readiness.current();
    if (!AW.state.config) return;
    if (!result.ok && !onlyConsent(result)) {
      readiness.focusFirstBlocker();
      return;
    }
    AW.ui.clearError(parts.errors);
    AW.ui.pending(button, "Launching…", AW.launch.start({ where: AW.form.where(), origin: button })).finally(() => paintAction(AW.form.get()));
  }

  function onSave(event) {
    const button = event.currentTarget;
    if (button.getAttribute("aria-disabled") === "true") return;
    AW.ui.clearError(parts.errors);
    AW.ui
      .pending(button, "Saving…", AW.form.save())
      .then(
        () => AW.store.refresh("folder").catch(() => {}),
        (thrown) => AW.ui.showError(parts.errors, thrown, { retry: () => AW.$("save").click() }),
      )
      .finally(() => paintAction(AW.form.get()));
  }

  // Recent sessions

  function paintRecent(force) {
    const workspace = AW.form.get().workspace;
    const panel = AW.$("launch-recent");
    if (!recent || !shown || (!force && workspace === recentFor)) return;
    recentFor = workspace;
    if (!workspace) {
      panel.hidden = true;
      return;
    }
    recent.refresh().then((rows) => {
      if (recentFor !== workspace) return;
      panel.hidden = !rows.length && !parts.recentList.querySelector("[data-error]");
    });
  }

  // Notices under the lede

  function staleList(folder) {
    return list(folder && folder.stale)
      .map((key) => {
        const value = String(key || "");
        return value.startsWith("plugin:") ? "the " + value.slice(7) + " tool" : STALE[value] || value;
      })
      .filter(Boolean);
  }

  function lastUsed() {
    const c = config();
    const s = c.settings || {};
    const row = s.connection_id ? accounts().find((item) => item.connection_id === s.connection_id) : null;
    const provider = (row && row.provider) || s.provider || LOCAL;
    const base = { provider, connection_id: (row && row.connection_id) || "", preset: c.selected || "", remote_model: String(s.remote_model || "").trim() };
    const source = row ? row.name || row.provider : provider === LOCAL ? "Local model" : provider === NATIVE ? "Agent account" : providerLabel(provider);
    return [s.harness, source, modelLabel(base)].filter(Boolean).join(" · ");
  }

  function noticesWanted(form) {
    const c = config();
    const folder = AW.state.folder;
    const out = [];
    const progress = AW.state.progress;
    const agentReady = Object.values(c.cli_present || {}).some(Boolean);
    const modelReady = Boolean((c.current && c.current.model) || Object.keys(c.presets || {}).length || accounts().some((row) => row.provider !== NATIVE && row.ready !== false));
    if (AW.state.config && !agentReady && !modelReady && progress && !progress.completed_at)
      out.push({
        id: "nothing",
        kind: "info",
        text: "Nothing is set up yet. The setup guide takes a few steps and ends with a running agent.",
        actions: [{ label: "Open setup guide", primary: true, run: () => AW.guide.open() }],
      });
    if (folder && folder.key && folder.known === false && form.workspace) {
      const kit = AW.state.kitHint;
      const used = lastUsed();
      const text = "New folder. It starts with the choices you used last: " + used + "." + (kit ? " " + kit.label + " looks like a fit." : "");
      const actions = [{ label: "Use this setup", primary: true, pending: "Saving…", run: () => AW.form.save().then(() => AW.store.refresh("folder").catch(() => {})) }];
      if (kit) actions.push({ label: "Preview the " + kit.label + " kit", run: () => AW.router.go("project", { tab: "kit", returnTo: "launch" }) });
      out.push({ id: "new:" + folder.key, kind: "info", text, actions });
    }
    const stale = staleList(folder);
    if (stale.length) out.push({ id: "stale:" + folder.key, kind: "warn", text: "Some remembered choices no longer exist here: " + AW.fmt.list(stale) + ". Choose again below." });
    return out.filter((item) => !dismissed.has(item.id));
  }

  function paintNotices(form) {
    const wanted = noticesWanted(form);
    const host = AW.$("launch-notices");
    const shape = wanted.map((item) => [item.id, item.text, (item.actions || []).map((action) => action.label)]);
    if (!changed("notices", shape)) return;
    for (const node of [...host.children]) if (node.dataset.launchNotice) node.remove();
    for (const item of wanted) {
      const node = AW.ui.notice(item.kind, item.text, { actions: item.actions, dismissible: true, onDismiss: () => dismissed.add(item.id) });
      node.dataset.launchNotice = item.id;
      host.append(node);
    }
  }

  // Layout: the action panel is a sticky bar below 900 px wide or 700 px high

  function placeAction() {
    const panel = parts.action;
    const view = AW.$("launch-view");
    panel.classList.toggle("launch-bar", bar.matches);
    if (bar.matches && panel.parentNode !== view) view.append(panel);
    else if (!bar.matches && panel.parentNode !== parts.side) parts.side.insertBefore(panel, AW.$("launch-recent"));
    signatures.delete("needs");
  }

  function pinSide() {
    const side = parts.side;
    const view = AW.$("view");
    if (bar.matches) {
      side.style.removeProperty("top");
      return;
    }
    const room = view.clientHeight - side.offsetHeight - 24;
    side.style.top = Math.min(0, room) + "px";
  }

  // Painting

  function paint() {
    const form = AW.form.get();
    paintProject(form);
    if (AW.state.config) {
      paintSource(form);
      paintModel(form);
      paintAgents(form);
      paintTools(form);
    }
    paintNotices(form);
    paintAction(form);
    paintRecent(false);
  }

  function build() {
    const remoteField = AW.$("remote-field");
    parts.remoteLabel = remoteField.querySelector('label[for="remote"]');
    parts.nativeHint = el("small", { class: "hint", id: "remote-native", text: "Uses the agent's own model picker.", hidden: true });
    remoteField.insertBefore(parts.nativeHint, AW.$("model-context"));
    AW.$("local-field").append(el("button", { type: "button", class: "link launch-field-link", id: "launch-models", text: "Model library", on: { click: () => AW.fix.run("go.models") } }));

    const tools = AW.$("tools");
    parts.skipped = el("p", { class: "hint launch-skipped", id: "launch-tools-skipped", role: "status", hidden: true });
    AW.$("session-extra").after(parts.skipped);
    tools.append(el("button", { type: "button", class: "link launch-manage", text: "Manage in Project setup", on: { click: () => AW.router.go("project", { tab: "mcp", returnTo: "launch" }) } }));

    parts.side = document.querySelector("#launch-view .launch-side");
    parts.action = document.querySelector("#launch-view .launch-action");
    const fieldset = parts.action.querySelector("fieldset");
    fieldset.classList.add("launch-where-radios");
    parts.where = el(
      "select",
      { id: "where", on: { change: () => AW.form.where(parts.where.value) } },
      el("option", { value: "dock", text: "Terminal dock" }),
      el("option", { value: "window", text: "New terminal window" }),
    );
    fieldset.after(el("div", { class: "launch-where-pick" }, el("label", { for: "where", class: "field-label", text: "Open in" }), parts.where));
    parts.steps = el("ol", { class: "launch-steps", attrs: { "aria-label": "Launch steps" }, hidden: true });
    parts.errors = el("div", { class: "launch-errors" });
    AW.$("launch-needs").before(parts.steps);
    AW.$("launch-needs").after(parts.errors);
    AW.$("launch-needs").addEventListener("click", () => {
      if (!bar.matches) return;
      AW.$("launch-readiness").scrollIntoView({ block: "start", behavior: "smooth" });
    });
    parts.action.querySelector(".launch-buttons").classList.add("launch-bar-buttons");
    const hints = el("div", { class: "launch-hints" });
    AW.$("save-state").before(hints);
    hints.append(AW.$("save-state"), AW.$("session-indicator"));

    const recentPanel = AW.$("launch-recent");
    parts.recentList = el("div", { class: "launch-recent-list", attrs: { "aria-labelledby": "launch-recent-head" } });
    recentPanel.append(parts.recentList, el("button", { type: "button", class: "link", text: "All sessions…", on: { click: () => AW.sessions.picker("resume") } }));
    recent = AW.sessions.recentList(parts.recentList, {
      workspace: () => recentFor || "",
      limit: 3,
      empty: "No saved sessions in this folder.",
    });

    AW.$("launch-project").addEventListener("click", () => AW.projectSwitch.open());
    AW.$("launch-machine").addEventListener("click", () => {
      const row = connection();
      return AW.fix.run("go.machines", row && row.host);
    });
    AW.$("account-link").addEventListener("click", () => {
      const row = connection();
      if (row) AW.router.go("accounts", { select: row.connection_id, returnTo: "launch" });
    });
    AW.$("list-models").addEventListener("click", (event) => {
      const button = event.currentTarget;
      if (button.dataset.pending) return;
      const form = AW.form.get();
      const row = connection();
      AW.ui
        .pending(button, "Loading models…", AW.modelPicker.open({ provider: form.provider, connection_id: form.connection_id, endpoint: form.endpoint, name: row ? row.name : form.provider }))
        .then(
          (id) => {
            if (typeof id === "string" && id) AW.form.set({ remote_model: id });
          },
          (thrown) => AW.ui.toast(AW.asError(thrown).message, { kind: "danger" }),
        );
    });
    AW.$("source").addEventListener("change", (event) => {
      const select = event.currentTarget;
      const value = select.value;
      if (value !== "add" && !value.startsWith("found:")) return;
      const form = AW.form.get();
      select.value = form.connection_id || form.provider || LOCAL;
      if (value === "add") {
        AW.router.go("accounts", { add: true, returnTo: "launch" });
        return;
      }
      const port = Number(value.slice(6));
      const found = foundServers().find((item) => Number(item.port) === port);
      AW.attach
        .open({ port, address: found ? found.base : "" })
        .then((row) => {
          if (!row || !row.connection_id) return;
          return AW.store.refresh("accounts", { force: true }).then(() => AW.form.set({ connection_id: row.connection_id }));
        })
        .then(() => AW.store.refresh("scan").catch(() => {}))
        .catch((thrown) => AW.ui.toast(AW.asError(thrown).message, { kind: "danger" }));
    });

    AW.$("launch").addEventListener("click", onLaunch);
    AW.$("save").addEventListener("click", onSave);
  }

  AW.router.register("launch", {
    title: "Launch",
    mount() {
      readiness = AW.ui.readinessList(AW.$("launch-readiness"));
      AW.launch.focusBlocker = () => readiness.focusFirstBlocker();
      build();
      placeAction();
      bar.addEventListener("change", () => {
        placeAction();
        paint();
        pinSide();
      });
      new ResizeObserver(pinSide).observe(parts.side);
      new ResizeObserver(pinSide).observe(AW.$("view"));
      AW.store.on(["config", "accounts", "folder", "sessions", "live", "scan", "forwards", "compat", "context", "resolve", "consent", "tools", "sourceTable", "kitHint", "progress", "unreachable"], paint);
      AW.store.on("sessions", () => {
        if (shown) paintRecent(true);
      });
      AW.form.on(paint);
      AW.readiness.on(() => paintAction(AW.form.get()));
      AW.launch.on(paintRun);
    },
    show() {
      shown = true;
      if (AW.state.config) {
        if (AW.store.age("scan") > 10000) AW.store.refresh("scan").catch(() => {});
        if (AW.store.age("accounts") > 10000) AW.store.refresh("accounts").catch(() => {});
        if (AW.store.age("tools") > 60000) AW.store.refresh("tools").catch(() => {});
      }
      signatures.delete("health");
      paint();
      paintRun();
      paintRecent(true);
      clearInterval(agoTimer);
      agoTimer = setInterval(() => {
        if (document.hidden) return;
        signatures.delete("health");
        paintHealth(AW.form.get());
      }, AGO_MS);
    },
    hide() {
      shown = false;
      clearInterval(agoTimer);
      agoTimer = null;
    },
  });
})();
