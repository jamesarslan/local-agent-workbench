// Overview: what is running, what each folder is set up with and what you can launch with (ui.md 5.2).
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const LOCAL = "Managed local";
  const NATIVE = "Native login";
  const ATTACHED = "Attached endpoint";
  const SESSION_KINDS = ["embedded", "native", "remote"];
  const SCAN_STALE_MS = 10000;
  const CLOSED_KEY = "overview.closed";
  const DEFAULT_CLOSED = ["connections", "machines", "computer"];
  const narrow = window.matchMedia("(max-width: 899px)");
  const low = window.matchMedia("(max-height: 700px)");
  const TOOL_NAMES = { search: "Web search", firecrawl: "Page crawling", blender: "Blender", desktop: "Windows desktop" };
  const REMOTE_WORD = { attached: "attached", detached: "detached", unverified: "not checked", ended: "ended" };
  const SOURCE_WORDS = { [LOCAL]: "Local model", [NATIVE]: "Agent account" };
  const FACTS = [
    ["agent", "Agent"],
    ["model", "Model"],
    ["instructions", "Instructions"],
    ["skills", "Skills"],
    ["tools", "Tools"],
    ["used", "Last used"],
    ["sessions", "Sessions"],
  ];

  const list = (value) => (Array.isArray(value) ? value : []);
  const text = (value) => (typeof value === "string" ? value : "");
  const plural = (n, one, many) => n + " " + (n === 1 ? one : many);
  const config = () => AW.state.config || {};
  const quiet = (key, options) => AW.store.refresh(key, options).catch(() => {});
  const projectName = () => AW.project.name(AW.form.get().workspace);
  const same = (a, b) => Boolean(a) && Boolean(b) && String(a).replace(/[\\/]+$/, "") === String(b).replace(/[\\/]+$/, "");

  function setText(node, value) {
    const next = String(value ?? "");
    if (node.textContent !== next) node.textContent = next;
  }

  function label(button, value) {
    const node = button.querySelector(".btn-label");
    setText(node || button, value);
  }

  function age(started) {
    const at = Number(started);
    if (!(at > 0)) return "";
    const s = Math.max(0, Math.round(Date.now() / 1000 - at));
    if (s < 60) return s + " s";
    const m = Math.floor(s / 60);
    if (m < 60) return m + " min";
    const h = Math.floor(m / 60);
    return h + " h" + (m % 60 ? " " + (m % 60) + " min" : "");
  }

  function toast(thrown) {
    const error = AW.asError(thrown);
    AW.ui.toast(error.message, { kind: error.code === "cancelled" ? "neutral" : "danger" });
  }

  function chosenModel() {
    const form = AW.form.get();
    return Boolean(form.preset || (config().current && config().current.model));
  }

  function sourceLabel(provider) {
    const table = AW.state.sourceTable;
    const row = list(table && table.providers).find((item) => item && item.id === provider);
    return SOURCE_WORDS[provider] || (row && row.label) || provider || "";
  }

  function keyEnv(provider) {
    const table = AW.state.sourceTable;
    const row = list(table && table.providers).find((item) => item && item.id === provider);
    return row ? text(row.key_env) : "";
  }

  function machineLabel(host) {
    const machines = AW.state.machines;
    return (machines && typeof machines === "object" && text(machines[host])) || host || "";
  }

  // Panels: a section at full width, a details element with the count and attention phrase in its summary below 900 px.
  function closedList() {
    try {
      const value = JSON.parse(AW.storage.get(CLOSED_KEY, "null"));
      return Array.isArray(value) ? value.filter((id) => typeof id === "string") : null;
    } catch {
      return null;
    }
  }

  function remember(id, open) {
    const next = (closedList() || DEFAULT_CLOSED).filter((item) => item !== id);
    if (!open) next.push(id);
    AW.storage.set(CLOSED_KEY, JSON.stringify(next));
  }

  function panel(id, title, tools) {
    const part = {
      id,
      title,
      heading: el("h2", { class: "panel-title", id: "overview-" + id + "-title", text: title }),
      count: el("span", { class: "panel-count" }),
      attention: el("span", { class: "overview-attention" }),
      tools: tools ? el("div", { class: "panel-actions overview-tools" }, tools) : null,
      errors: el("div", { class: "overview-errors" }),
      body: el("div", { class: "panel-body" }),
      busy: el("span", { class: "sr-only", hidden: true, text: "Loading " + title }),
      el: null,
    };
    part.body.append(part.errors);
    frame(part);
    return part;
  }

  function frame(part) {
    const fold = narrow.matches;
    const head = el(fold ? "summary" : "div", { class: "panel-head" }, part.heading, part.count, part.attention, fold ? null : part.tools);
    const root = el(
      fold ? "details" : "section",
      { class: "panel overview-panel", id: "overview-" + part.id, attrs: { "aria-labelledby": part.heading.id } },
      head,
      fold && part.tools ? el("div", { class: "overview-tools-row" }, part.tools) : null,
      part.body,
      part.busy,
    );
    if (fold) root.open = !(closedList() || DEFAULT_CLOSED).includes(part.id);
    const old = part.el;
    if (old) {
      const focused = old.contains(document.activeElement) ? document.activeElement : null;
      if (old.getAttribute("aria-busy")) root.setAttribute("aria-busy", "true");
      old.replaceWith(root);
      if (focused && focused.isConnected) focused.focus();
    }
    if (fold) root.addEventListener("toggle", () => remember(part.id, root.open));
    part.el = root;
  }

  function busy(part, on) {
    if (on) part.el.setAttribute("aria-busy", "true");
    else part.el.removeAttribute("aria-busy");
    part.busy.hidden = !on;
  }

  function counted(part, count, attention) {
    setText(part.count, count === null || count === undefined ? "" : String(count));
    setText(part.attention, attention ? attention : "");
  }

  // A row with a mark, a main button (title and meta), action buttons and a note line; updated in place so focus survives the poll.
  function row() {
    const mark = AW.ui.mark("idle");
    const title = el("span", { class: "row-title" });
    const meta = el("span", { class: "row-meta" });
    const main = el("button", { class: "row-main", type: "button" }, title, meta);
    const actions = el("div", { class: "row-actions" });
    const note = el("p", { class: "overview-note", hidden: true });
    const errors = el("div", { class: "overview-row-errors" });
    const root = el("div", { class: "row overview-row" }, mark, main, actions, note, errors);
    let current = {};
    let signature = "";
    main.addEventListener("click", () => current.open && current.open(main));
    return {
      el: root,
      errors,
      update(item) {
        current = item;
        mark.hidden = !item.mark;
        if (item.mark) AW.ui.setMark(mark, item.mark);
        setText(title, item.title || "");
        main.title = item.title || "";
        setText(meta, item.meta || "");
        meta.hidden = !item.meta;
        meta.classList.toggle("warn", Boolean(item.warn));
        note.hidden = !item.note;
        setText(note, item.note || "");
        root.classList.toggle("is-current", Boolean(item.current));
        root.classList.toggle("is-off", Boolean(item.dim));
        main.disabled = !item.open;
        if (item.openLabel) main.setAttribute("aria-label", item.openLabel);
        else main.removeAttribute("aria-label");
        const next = JSON.stringify((item.actions || []).map((action) => [action.label, action.kind || "", action.disabled || "", action.icon || ""]));
        if (next !== signature) {
          signature = next;
          const focused = actions.contains(document.activeElement) ? document.activeElement.textContent.trim() || document.activeElement.getAttribute("aria-label") : null;
          actions.replaceChildren(...(item.actions || []).map((action) => actionButton(action, () => current, errors)));
          if (focused) {
            const again = [...actions.querySelectorAll("button")].find((button) => (button.textContent.trim() || button.getAttribute("aria-label")) === focused) || actions.querySelector("button") || main;
            again.focus();
          }
        } else {
          [...actions.children].forEach((button, at) => (button._overviewAction = (item.actions || [])[at]));
        }
      },
    };
  }

  function actionButton(action, item, errors) {
    const iconOnly = String(action.kind || "").includes("icon");
    const button = AW.ui.button(action.label, {
      small: true,
      kind: action.kind,
      icon: action.icon,
      pending: action.pending,
      onClick() {
        const now = button._overviewAction || action;
        if (!now.run) return undefined;
        AW.ui.clearError(errors);
        const result = now.run(button, item());
        if (result && typeof result.then === "function")
          return result.catch((thrown) => {
            const error = AW.asError(thrown);
            if (error.code === "cancelled") AW.ui.toast(error.message);
            else AW.ui.showError(errors, error);
          });
        return result;
      },
    });
    button._overviewAction = action;
    if (action.after) button.append(AW.ui.icon(action.after));
    if (action.disabled) {
      button.setAttribute("aria-disabled", "true");
      button.title = action.disabled;
    }
    if (iconOnly) button.setAttribute("aria-haspopup", "menu");
    return button;
  }

  function keyed(host, empty) {
    const rows = new Map();
    return {
      set(items) {
        const seen = new Set();
        let cursor = host.firstChild;
        for (const item of items) {
          seen.add(item.key);
          let part = rows.get(item.key);
          if (!part) {
            part = row();
            rows.set(item.key, part);
          }
          part.update(item);
          if (part.el === cursor) cursor = cursor.nextSibling;
          else host.insertBefore(part.el, cursor);
        }
        for (const [key, part] of rows)
          if (!seen.has(key)) {
            part.el.remove();
            rows.delete(key);
          }
        if (empty) empty(items.length === 0);
      },
    };
  }

  const view = {};

  // Current project
  const cp = { rows: {}, expanded: false };

  function buildCurrent() {
    const part = panel("current", "Current project");
    cp.name = el("h3", { class: "overview-cp-name" });
    cp.path = el("span", { class: "overview-cp-path" });
    cp.setup = AW.ui.button("Project setup", { onClick: () => AW.router.go("project", { returnTo: "overview" }) });
    cp.launch = AW.ui.button("Launch…", { kind: "primary", id: "overview-launch", onClick: () => AW.launch.quick(cp.launch) });
    cp.needs = el("p", { class: "overview-needs", id: "overview-launch-needs", role: "status" });
    cp.launch.setAttribute("aria-describedby", "overview-launch-needs");
    cp.head = el(
      "div",
      { class: "overview-cp-head" },
      el("div", { class: "overview-cp-id" }, cp.name, cp.path),
      el("div", { class: "overview-cp-actions" }, cp.setup, cp.launch),
    );
    cp.attention = el("div", { class: "overview-cp-attention" });
    cp.facts = el("dl", { class: "overview-facts", id: "overview-facts" });
    for (const [key, name] of FACTS) {
      const dt = el("dt", { text: name });
      const dd = el("dd");
      const extra = key !== "agent" && key !== "model";
      if (extra) {
        dt.classList.add("overview-extra");
        dd.classList.add("overview-extra");
      }
      cp.facts.append(dt, dd);
      cp.rows[key] = { dt, dd };
    }
    const session = cp.rows.sessions;
    session.text = el("span", { class: "overview-session-text" });
    session.resume = AW.ui.button("Resume", { small: true, onClick: () => resumeNewest(session.resume) });
    session.all = AW.ui.button("All…", { small: true, onClick: () => AW.sessions.picker("resume") });
    session.all.setAttribute("aria-label", "All saved sessions in this folder");
    session.errors = el("div", { class: "overview-row-errors" });
    session.dd.append(el("div", { class: "overview-session" }, session.text, session.resume, session.all), session.errors);
    cp.more = AW.ui.button("Show all", {
      kind: "link",
      onClick() {
        cp.expanded = !cp.expanded;
        paintCompact();
      },
    });
    cp.more.classList.add("overview-more");
    cp.more.setAttribute("aria-controls", "overview-facts");
    cp.empty = el("div", { class: "overview-cp-empty" });
    part.body.append(cp.head, cp.needs, cp.attention, cp.facts, cp.more, cp.empty);
    cp.part = part;
    return part;
  }

  function paintCompact() {
    const compact = low.matches && !cp.expanded;
    cp.facts.classList.toggle("compact", compact);
    cp.more.hidden = !low.matches || cp.facts.hidden;
    label(cp.more, cp.expanded ? "Show less" : "Show all");
    cp.more.setAttribute("aria-expanded", String(!compact));
  }

  function fact(key, parts, { warn = false, go, title } = {}) {
    const slot = cp.rows[key];
    if (!slot.button) {
      slot.button = el("button", { class: "link overview-fact", type: "button" });
      slot.button.addEventListener("click", () => slot.go && slot.go());
      slot.dd.append(slot.button);
    }
    slot.go = go;
    slot.button.title = title || "";
    slot.button.classList.toggle("warn", warn);
    const nodes = (Array.isArray(parts) ? parts : [parts]).filter((part) => part !== null && part !== undefined && part !== "");
    const sig = JSON.stringify(nodes.map((node) => (node instanceof Node ? node.className + ":" + node.textContent : node)));
    if (slot.sig !== sig) {
      slot.sig = sig;
      slot.button.replaceChildren(...nodes);
    }
  }

  function sourceName(form) {
    const row = AW.form.connection();
    if (row && row.name) return row.name;
    return sourceLabel(form.provider) || "the model source";
  }

  function agentFact(form) {
    const agent = form.harness;
    const go = () => AW.router.go("launch", { returnTo: "overview" });
    if (!agent) return fact("agent", "No agent chosen", { go, title: "Choose in Launch" });
    const present = config().cli_present || {};
    if (present[agent] === false) return fact("agent", agent + " · Not installed", { warn: true, go, title: "Change in Launch" });
    const version = view.versions && view.versions[agent] ? " " + view.versions[agent] : "";
    const verdict = AW.state.compat && AW.state.compat[agent];
    const doubt = verdict && (verdict.ok === false || text(verdict.reason));
    const tail = doubt ? text(verdict.reason) || "does not work with " + sourceName(form) : "works with " + sourceName(form);
    fact("agent", agent + version + " · " + tail, { warn: Boolean(doubt), go, title: "Change in Launch" });
  }

  function modelFact(form) {
    const go = () => AW.router.go("launch", { focus: "source", returnTo: "overview" });
    const c = config();
    const parts = [sourceName(form)];
    let context = 0;
    if (form.provider === LOCAL) {
      const preset = form.preset && c.presets ? c.presets[form.preset] : null;
      const file = AW.fmt.folder((preset && preset.model) || (c.current && c.current.model));
      parts.push(form.preset || file || "No local model chosen");
      if (form.preset || file) context = (preset && preset.context) || (c.current && c.current.context) || 0;
    } else if (form.provider !== NATIVE) {
      parts.push(form.remote_model || "No model chosen");
      context = (AW.state.context && AW.state.context.context) || 0;
    }
    const ctx = AW.fmt.context(context);
    if (ctx) parts.push(ctx + " context");
    const missing = (form.provider === LOCAL && !chosenModel()) || (form.provider !== LOCAL && form.provider !== NATIVE && !form.remote_model);
    fact("model", parts.join(" · "), { warn: missing, go, title: "Change in Launch" });
  }

  function instructionsFact(form, resolve, error) {
    const go = () => AW.router.go("project", { tab: "instructions", returnTo: "overview" });
    const agent = form.harness || "the agent";
    if (!resolve) return fact("instructions", error ? "Not checked: " + error.message : "Checking…", { warn: Boolean(error), go, title: "Open Project setup" });
    const rows = list(resolve.instructions);
    const read = rows.filter((item) => item && item.status === "read");
    const shadowed = rows.find((item) => item && item.status === "shadowed");
    let value = "No instruction file";
    if (shadowed && read.length) value = agent + " reads " + read[0].path + "; " + shadowed.path + " is shadowed";
    else if (read.length === 1) value = read[0].path + " read by " + agent + (Number(read[0].lines) >= 0 ? " · " + plural(Number(read[0].lines), "line", "lines") : "");
    else if (read.length > 1) value = plural(read.length, "instruction file", "instruction files") + " read by " + agent;
    fact("instructions", value, { warn: Boolean(shadowed), go, title: "Open Project setup" });
  }

  function skillsFact(resolve) {
    const go = () => AW.router.go("project", { tab: "skills", returnTo: "overview" });
    if (!resolve) return fact("skills", "Checking…", { go, title: "Open Project setup" });
    const groups = new Map();
    for (const skill of list(resolve.skills)) {
      if (!skill || skill.status !== "read") continue;
      const parts = text(skill.path).split(/[\\/]+/);
      const folder = parts.length > 2 ? parts.slice(0, -2).join("/") : parts.slice(0, -1).join("/") || ".";
      groups.set(folder, (groups.get(folder) || 0) + 1);
    }
    const value = groups.size ? [...groups].map(([folder, n]) => n + " from " + folder).join(", ") : "None";
    fact("skills", value, { go, title: "Open Project setup" });
  }

  function toolsFact(form, resolve) {
    const go = () => AW.router.go("project", { tab: "mcp", returnTo: "overview" });
    if (!resolve) return fact("tools", "Checking…", { go, title: "Open Project setup" });
    const launch = list(resolve.mcp && resolve.mcp.launch);
    const sent = launch.filter((item) => item && item.status === "sent");
    const skipped = launch.filter((item) => item && item.status === "skipped").length;
    const c = config();
    const caps = ["search", "firecrawl", "blender"].concat(c.windows ? ["desktop"] : []);
    const plugins = text(form.plugins).split(",").filter(Boolean);
    const off =
      caps.filter((key) => !form[key]).length +
      list(c.registered_plugins).filter((name) => !plugins.includes(name)).length +
      launch.filter((item) => item && item.status === "off").length;
    const parts = sent.map((item) => AW.ui.chip(TOOL_NAMES[item.name] || item.name));
    if (!sent.length) parts.push("None on");
    if (off) parts.push(el("span", { class: "overview-quiet", text: (sent.length ? plural(off, "more is", "more are") : plural(off, "tool is", "tools are")) + " off" }));
    if (skipped) parts.push(el("span", { class: "overview-warn", text: plural(skipped, "tool", "tools") + " will not reach " + (form.harness || "the agent") }));
    fact("tools", parts, { go, title: "Open Project setup" });
  }

  function usedFact(folder) {
    const go = () => AW.router.go("project", { tab: "kit", returnTo: "overview" });
    const when = folder && folder.updated ? AW.fmt.ago(folder.updated) : "";
    const kit = folder && folder.kit ? "Kit: " + ((view.kits && view.kits[folder.kit]) || folder.kit) : "No kit";
    fact("used", [when || "Not launched yet", kit].join(" · "), { go, title: "Open Project setup" });
  }

  function sessionsFact() {
    const slot = cp.rows.sessions;
    const recent = view.recent || {};
    const newest = list(recent.rows)[0];
    slot.resume.hidden = !newest;
    if (recent.loading && !newest) setText(slot.text, "Reading saved sessions…");
    else if (recent.error) setText(slot.text, recent.error.message);
    else if (!newest) setText(slot.text, "No saved sessions in this folder.");
    else setText(slot.text, [newest.title || "Untitled session", newest.harness, AW.sessions.when(newest)].filter(Boolean).join(" · "));
    slot.text.classList.toggle("overview-warn", Boolean(recent.error));
    if (newest) slot.resume.setAttribute("aria-label", "Resume " + (newest.title || "the newest session"));
  }

  function resumeNewest(button) {
    const newest = list((view.recent || {}).rows)[0];
    if (!newest) return undefined;
    const errors = cp.rows.sessions.errors;
    AW.ui.clearError(errors);
    const work = AW.sessions.resume(newest).catch((thrown) => AW.ui.showError(errors, thrown));
    return AW.ui.pending(button, "Opening…", work);
  }

  async function loadRecent() {
    const workspace = AW.form.get().workspace;
    const mine = (view.recentTicket = (view.recentTicket || 0) + 1);
    if (!workspace || !AW.state.folder || !AW.state.folder.key) {
      view.recent = { rows: [] };
      return sessionsFact();
    }
    view.recent = { rows: view.recent && view.recent.workspace === workspace ? view.recent.rows : [], loading: true, workspace };
    sessionsFact();
    try {
      const rows = await AW.sessions.recent(workspace, { limit: 3 });
      if (mine !== view.recentTicket) return;
      view.recent = { rows, workspace };
    } catch (thrown) {
      if (mine !== view.recentTicket) return;
      view.recent = { rows: [], error: AW.asError(thrown), workspace };
    }
    sessionsFact();
  }

  function attentionLinks(form) {
    const links = [];
    const consent = AW.state.consent;
    if (consent && consent.needed && !consent.granted)
      links.push(AW.ui.button("Needs your review", { kind: "link", onClick: () => AW.router.go("project", { returnTo: "overview" }) }));
    const warnings = list(AW.state.resolve && AW.state.resolve.warnings).filter((item) => typeof item === "string" && item);
    if (warnings.length)
      links.push(AW.ui.button(plural(warnings.length, "setup warning", "setup warnings"), { kind: "link", onClick: () => AW.router.go("project", { returnTo: "overview" }) }));
    cp.attention.hidden = !links.length;
    const sig = links.map((link) => link.textContent).join("|") + "|" + form.harness;
    if (cp.attentionSig === sig) return links.length;
    cp.attentionSig = sig;
    const focused = cp.attention.contains(document.activeElement) ? document.activeElement.textContent : null;
    cp.attention.replaceChildren(...(links.length ? [AW.ui.mark("warn"), ...links] : []));
    if (focused) (links.find((link) => link.textContent === focused) || cp.launch).focus();
    return links.length;
  }

  function paintCurrent() {
    if (!cp.part) return;
    const form = AW.form.get();
    const folder = AW.state.folder;
    const workspace = form.workspace;
    const hasFolder = Boolean(workspace);
    const gone = Boolean(folder && folder.path && !folder.key);
    const unchosen = hasFolder && !gone && folder && folder.key && !folder.known && !AW.form.dirty();
    cp.head.hidden = !hasFolder;
    setText(cp.name, AW.project.name(workspace) || "");
    setText(cp.path, AW.fmt.path(workspace));
    cp.path.title = AW.fmt.path(workspace);
    counted(cp.part, null, "");
    if (!hasFolder) {
      cp.facts.hidden = true;
      cp.needs.hidden = true;
      cp.attention.hidden = true;
      cp.more.hidden = true;
      empty(cp.empty, "No project folder selected.", [{ label: "Choose a folder…", primary: true, run: () => AW.fix.run("project.pick") }], "none");
      return;
    }
    if (unchosen) {
      cp.facts.hidden = true;
      cp.needs.hidden = true;
      cp.attention.hidden = true;
      cp.launch.hidden = true;
      cp.more.hidden = true;
      empty(cp.empty, "Nothing chosen for this folder yet.", [{ label: "Choose and launch…", primary: true, run: () => AW.router.go("launch", { returnTo: "overview" }) }], "unchosen");
      return;
    }
    empty(cp.empty, null);
    cp.launch.hidden = false;
    cp.facts.hidden = false;
    const ready = AW.readiness.current();
    const wide = !narrow.matches;
    label(cp.launch, ready.ok && wide ? "Launch in " + AW.project.name(workspace) : "Launch…");
    cp.launch.title = ready.ok ? "Launch " + form.harness + " in " + AW.fmt.path(workspace) : "";
    cp.needs.hidden = ready.ok;
    setText(cp.needs, ready.ok ? "" : ready.needs);
    const flagged = attentionLinks(form);
    setText(cp.part.attention, !ready.ok ? "· needs attention" : flagged ? "· " + plural(flagged, "thing to check", "things to check") : "");
    const resolve = AW.state.resolve;
    const resolveError = AW.store.error("resolve");
    agentFact(form);
    modelFact(form);
    instructionsFact(form, resolve, resolveError);
    skillsFact(resolve);
    toolsFact(form, resolve);
    usedFact(folder);
    sessionsFact();
    paintCompact();
  }

  function empty(host, sentence, actions, key) {
    if (!sentence) {
      if (host.dataset.key) {
        host.replaceChildren();
        delete host.dataset.key;
      }
      host.hidden = true;
      return;
    }
    host.hidden = false;
    if (host.dataset.key === key) return;
    host.dataset.key = key;
    host.replaceChildren(AW.ui.empty(sentence, actions));
  }

  // Running now
  function buildRunning() {
    const part = panel("running", "Running now");
    const loading = AW.ui.skeleton(3, "running sessions");
    const live = el("div", { class: "rows" });
    const endedHead = el("h3", { class: "group-label overview-group", text: "Ended recently" });
    const ended = el("div", { class: "rows" });
    const emptyHost = el("div");
    part.body.append(loading, live, endedHead, ended, emptyHost);
    view.running = { part, loading, live, endedHead, ended, emptyHost, checked: new Map(), notes: new Map(), dismissed: new Set() };
    view.running.liveList = keyed(live);
    view.running.endedList = keyed(ended);
    return part;
  }

  async function showSession(item) {
    if (item.workspace && !same(item.workspace, AW.project.current())) await AW.project.select(item.workspace);
    await AW.terminal.show(item.terminal);
  }

  async function endSession(item) {
    if (item.kind === "remote") {
      const where = machineLabel(item.host);
      if (!(await AW.ui.confirm({ text: "End the remote session " + (item.title || item.name) + " on " + where + "?", ok: "End", danger: true }))) return;
      await AW.request("sessions-end", { host: item.host, name: item.name });
    } else {
      if (!(await AW.ui.confirm({ text: "End this terminal session?", ok: "End", danger: true }))) return;
      await AW.request("term-close", { workspace: item.workspace, id: item.terminal });
    }
    await AW.store.refresh("sessions");
  }

  async function checkRemote(item) {
    const r = view.running;
    const rows = await AW.request("sessions-remote", { host: item.host });
    const found = list(rows).find((other) => other && other.name === item.name);
    r.checked.set(item.id, found ? found.state : "ended");
    if (found) r.notes.delete(item.id);
    else r.notes.set(item.id, "This session has ended on " + machineLabel(item.host) + ".");
    paintRunning();
    quiet("sessions");
  }

  function sessionItem(item) {
    const r = view.running;
    const where = AW.fmt.folder(item.workspace) || "No folder";
    const since = age(item.started);
    const base = { key: item.id, title: item.title || item.harness || "Session" };
    if (item.kind === "embedded")
      return {
        ...base,
        mark: "ok",
        meta: [where, "Terminal dock", since].filter(Boolean).join(" · "),
        open: () => showSession(item).catch(toast),
        openLabel: "Show " + base.title + " in the terminal dock",
        actions: [
          { label: "Show", run: () => showSession(item) },
          { label: "End", run: () => endSession(item) },
        ],
      };
    if (item.kind === "native")
      return { ...base, mark: "ok", meta: [where, "Terminal window", since].filter(Boolean).join(" · "), note: "Close its window to end it.", actions: [] };
    const state = r.checked.get(item.id) || item.state;
    return {
      ...base,
      mark: state === "attached" ? "ok" : state === "ended" ? "down" : "idle",
      meta: [machineLabel(item.host), "SSH, " + (REMOTE_WORD[state] || "not checked"), since].filter(Boolean).join(" · "),
      note: r.notes.get(item.id) || "",
      open: () => AW.router.go("machines", { select: item.host, host: item.host, returnTo: "overview" }),
      openLabel: "Open " + machineLabel(item.host) + " in Machines",
      actions: [
        { label: "Check", pending: "Checking…", run: () => checkRemote(item) },
        { label: "End", run: () => endSession(item) },
      ],
    };
  }

  const exitKey = (item) => [item.harness, item.workspace, item.ended, item.code].join("\n");

  async function launchAgain(item) {
    if (item.workspace && !same(item.workspace, AW.project.current())) await AW.project.select(item.workspace);
    if (item.harness && list(config().agents).includes(item.harness)) AW.form.set({ harness: item.harness });
    await AW.router.go("launch", { focus: "launch", returnTo: "overview" });
  }

  function exitItem(item) {
    const r = view.running;
    const key = exitKey(item);
    const seconds = Number(item.seconds);
    const after = Number.isFinite(seconds) ? " after " + (seconds < 10 ? seconds : Math.round(seconds)) + " s" : "";
    return {
      key: "exit:" + key,
      mark: item.code !== 0 ? "warn" : null,
      title: (item.harness || "The agent") + " ended" + after + " · exit code " + item.code,
      meta: AW.fmt.folder(item.workspace),
      note: text(item.message),
      actions: [
        { label: "Launch again", run: () => launchAgain(item) },
        {
          label: "Dismiss",
          kind: "quiet",
          run() {
            r.dismissed.add(key);
            paintRunning();
          },
        },
      ],
    };
  }

  function paintRunning() {
    const r = view.running;
    if (!r) return;
    const rows = list(AW.state.sessions).filter((item) => item && SESSION_KINDS.includes(item.kind));
    rows.sort((a, b) => (Number(b.started) || 0) - (Number(a.started) || 0));
    const exits = list(AW.state.exits).filter((item) => item && typeof item === "object" && !r.dismissed.has(exitKey(item)));
    const loaded = AW.store.age("sessions") !== Infinity;
    const error = AW.store.error("sessions");
    r.loading.hidden = loaded || Boolean(error);
    busy(r.part, !loaded && !error);
    r.liveList.set(rows.map(sessionItem));
    r.endedList.set(exits.map(exitItem));
    r.endedHead.hidden = !exits.length;
    const nothing = loaded && !rows.length && !exits.length;
    if (error && !loaded) {
      if (!r.errorShown) AW.ui.showError(r.part.errors, error, { retry: () => AW.store.refresh("sessions").catch(() => {}) });
      r.errorShown = true;
    } else if (r.errorShown) {
      AW.ui.clearError(r.part.errors);
      r.errorShown = false;
    }
    empty(r.emptyHost, nothing ? "Nothing is running. Launch an agent to see it here." : null, [{ label: "Go to Launch", run: () => AW.router.go("launch") }], "empty");
    const failed = exits.filter((item) => item.code !== 0).length;
    counted(r.part, rows.length, failed ? "· " + plural(failed, "ended with an error", "ended with errors") : "");
  }

  // Projects
  function buildProjects() {
    const filter = el("input", { type: "search", class: "overview-filter", attrs: { "aria-label": "Filter folders", placeholder: "Filter folders", autocomplete: "off", spellcheck: "false" } });
    const part = panel("projects", "Projects", filter);
    const loading = AW.ui.skeleton(3, "project folders");
    const host = el("div", { class: "rows" });
    const emptyHost = el("div");
    const open = AW.ui.button("Open another folder…", {
      kind: "link",
      icon: "folder",
      onClick: () =>
        AW.project.pick().catch((thrown) => {
          AW.ui.showError(part.errors, thrown);
        }),
    });
    open.classList.add("overview-foot");
    part.body.append(loading, host, emptyHost, open);
    filter.addEventListener("input", () => paintProjects());
    view.projects = { part, filter, loading, host, emptyHost, open, list: keyed(host) };
    return part;
  }

  function runningIn(path, key) {
    return list(AW.state.sessions).filter((item) => item && SESSION_KINDS.includes(item.kind) && (same(item.workspace, path) || same(item.workspace, key))).length;
  }

  async function launchIn(button, folder) {
    if (!same(folder.path, AW.project.current())) await AW.project.select(folder.path);
    return AW.launch.quick(button);
  }

  function projectItem(folder) {
    const p = view.projects;
    const name = AW.fmt.folder(folder.path) || folder.path;
    const current = same(folder.path, AW.project.current()) || same(folder.key, AW.project.current());
    const running = runningIn(folder.path, folder.key);
    const title = name + (current ? " · current" : "") + (running ? " · " + running + " running" : "");
    if (folder.exists === false)
      return {
        key: folder.key || folder.path,
        title,
        meta: "Folder not found",
        warn: true,
        dim: true,
        actions: [
          {
            label: "Forget",
            pending: "Forgetting…",
            run: () =>
              AW.request("folder-forget", { workspace: folder.key || folder.path }).then((forgotten) => {
                if (forgotten) AW.ui.toast(name + " is forgotten.");
                return AW.store.refresh("folders", { force: true });
              }),
          },
        ],
      };
    const kit = folder.kit ? "Kit: " + ((view.kits && view.kits[folder.kit]) || folder.kit) : "";
    const meta = [folder.harness, sourceLabel(folder.provider), folder.model_label, kit].filter(Boolean).join(" · ") || "Nothing chosen yet";
    return {
      key: folder.key || folder.path,
      title,
      meta,
      current,
      open: () => AW.project.select(folder.path).catch((thrown) => AW.ui.showError(p.part.errors, thrown)),
      openLabel: current ? name + ", current project, " + meta : "Switch to " + name + ", " + meta,
      actions: [{ label: "Launch", run: (button) => launchIn(button, folder) }],
    };
  }

  function paintProjects() {
    const p = view.projects;
    if (!p) return;
    const folders = AW.project.known();
    const loaded = AW.store.age("folders") !== Infinity;
    const error = AW.store.error("folders");
    p.loading.hidden = loaded || Boolean(error);
    busy(p.part, !loaded && !error);
    const query = p.filter.value.trim().toLowerCase();
    const shown = query ? folders.filter((folder) => [folder.path, AW.fmt.path(folder.path)].some((value) => String(value).toLowerCase().includes(query))) : folders;
    p.list.set(shown.map(projectItem));
    if (error && !loaded) {
      if (!p.errorShown) AW.ui.showError(p.part.errors, error, { retry: () => AW.store.refresh("folders").catch(() => {}) });
      p.errorShown = true;
    } else if (p.errorShown) {
      AW.ui.clearError(p.part.errors);
      p.errorShown = false;
    }
    if (loaded && !folders.length) empty(p.emptyHost, "No project folders yet.", [{ label: "Choose a folder…", primary: true, run: () => AW.project.pick() }], "none");
    else if (loaded && query && !shown.length) empty(p.emptyHost, "No folder matches “" + p.filter.value.trim() + "”.", [], "filter:" + query);
    else empty(p.emptyHost, null);
    p.open.hidden = loaded && !folders.length;
    p.filter.hidden = !folders.length;
    const missing = folders.filter((folder) => folder.exists === false).length;
    counted(p.part, loaded ? folders.length : null, missing ? "· " + plural(missing, "folder not found", "folders not found") : "");
  }

  // Connections
  function buildConnections() {
    const scan = AW.ui.button("Scan", { small: true, pending: "Scanning…", onClick: () => scanNow() });
    scan.title = "Scan this computer for model servers";
    const add = AW.ui.button("Add", { small: true, onClick: () => addMenu(add) });
    add.append(AW.ui.icon("caret"));
    add.setAttribute("aria-haspopup", "menu");
    const part = panel("connections", "Connections", [scan, add]);
    const loading = AW.ui.skeleton(3, "connections");
    const host = el("div", { class: "rows" });
    const hints = el("div", { class: "overview-hints" });
    const emptyHost = el("div");
    part.body.append(loading, host, hints, emptyHost);
    view.conns = { part, scan, add, loading, host, hints, emptyHost, list: keyed(host) };
    return part;
  }

  function scanNow() {
    const c = view.conns;
    AW.ui.clearError(c.part.errors);
    return AW.store.refresh("scan", { force: true }).catch((thrown) => AW.ui.showError(c.part.errors, thrown, { retry: () => scanNow() }));
  }

  function addMenu(anchor) {
    AW.ui.menu(anchor, [
      { label: "Attach a running server…", run: () => AW.attach.open({}) },
      { label: "Add a hosted API…", run: () => AW.router.go("accounts", { add: "hosted", returnTo: "overview" }) },
      { label: "Add a machine over SSH…", run: () => AW.router.go("machines", { add: true, returnTo: "overview" }) },
      { label: "Add an agent account…", run: () => AW.router.go("accounts", { add: "account", returnTo: "overview" }) },
    ]);
  }

  function useIn(arg, name) {
    return AW.fix.run("source.use", arg).then(() => AW.ui.toast(name + " is selected for " + projectName() + ". Not saved yet."));
  }

  function launchWith(arg) {
    return AW.fix.run("source.use", arg).then(() => AW.router.go("launch", { returnTo: "overview" }));
  }

  function more(arg, name) {
    return {
      label: "More actions for " + name,
      kind: "quiet icon",
      icon: "more",
      run(button) {
        const items = [];
        if (AW.form.get().workspace) items.push({ label: "Use in " + projectName(), run: () => useIn(arg, name) });
        items.push({ label: "Launch with this", run: () => launchWith(arg) });
        AW.ui.menu(button, items);
      },
    };
  }

  function startModel(button) {
    return AW.jobs.run("server-start", AW.form.get(), { title: "Starting the local model", origin: button, key: "server" }).then(() => AW.store.refresh("live"));
  }

  async function stopModel(button) {
    const using = list((AW.state.live || {}).sessions).length;
    const question = using ? "Stop the local model? " + (using === 1 ? "1 session is" : using + " sessions are") + " using it." : "Stop the local model?";
    if (!(await AW.ui.confirm({ text: question, ok: "Stop", danger: true }))) return;
    await AW.jobs.run("server-stop", {}, { title: "Stopping the local model", origin: button, key: "server-stop" });
    await AW.store.refresh("live");
  }

  async function cancelLoad() {
    const job = AW.jobs.find("server");
    if (job) await AW.jobs.cancel(job.handle);
    else await AW.jobs.run("server-stop", {}, { title: "Stopping the local model", key: "server-stop" });
    await AW.store.refresh("live");
  }

  function localItem() {
    const live = AW.state.live;
    const c = config();
    const form = AW.form.get();
    const preset = form.preset && c.presets ? c.presets[form.preset] : null;
    const setup = form.preset || AW.fmt.folder(c.current && c.current.model);
    const context = AW.fmt.context((preset && preset.context) || (c.current && c.current.context));
    const loading = Boolean(AW.jobs.find("server")) || (live && live.label === "Starting");
    const base = { key: "local", title: "Local model", open: () => AW.router.go("accounts", { select: LOCAL, returnTo: "overview" }), openLabel: "Open Local model in Connections" };
    let mark = "idle";
    let word = "Checking…";
    let action = null;
    let level = "";
    let parts = setup ? [setup, context] : [];
    if (!live) {
      if (AW.store.error("live")) {
        mark = "warn";
        word = "Status unavailable";
      }
    } else if (loading) {
      mark = "busy";
      word = "Loading…" + (live.started ? " " + Math.max(0, Math.round(Date.now() / 1000 - live.started)) + " s" : "");
      action = { label: "Cancel", pending: "Cancelling…", run: () => cancelLoad() };
    } else if (live.label === "Serving") {
      mark = "ok";
      word = "Serving";
      parts = [AW.fmt.folder(live.model) || setup, AW.fmt.context(live.context) || context];
      action = { label: "Stop", run: (button) => stopModel(button) };
    } else if (live.label === "Port in use") {
      mark = "warn";
      level = "warn";
      word = "Port " + live.port + " in use";
      action = { label: "Attach it", run: (button) => AW.fix.run("source.probe", live.port, { origin: button }), disabled: AW.fix.has("source.probe") ? "" : "Not available yet." };
    } else if (live.label === "Unexpected model") {
      mark = "warn";
      level = "warn";
      word = "Unexpected model";
      parts = [AW.fmt.folder(live.model), AW.fmt.context(live.context)];
      action = { label: "Use it", kind: "primary", run: () => AW.fix.run("server.adopt") };
    } else {
      word = "Stopped";
      action = { label: "Start", run: (button) => startModel(button), disabled: chosenModel() ? "" : "Choose a model in Advanced → Model library first." };
    }
    return { ...base, mark, level, meta: [word, ...parts].filter(Boolean).join(" · "), warn: level === "warn", actions: [action, more(LOCAL, "Local model")].filter(Boolean) };
  }

  function seenIn(scan, row) {
    return list(scan && scan.endpoints).find((item) => item && (item.connection_id === row.connection_id || (item.base && same(item.base, row.endpoint)))) || null;
  }

  function reconnect(button, row, forward) {
    const host = forward.host;
    const after = () => Promise.all([AW.store.refresh("forwards"), AW.store.refresh("accounts")]);
    if (AW.fix.has("machine.reconnect")) return AW.fix.run("machine.reconnect", row.connection_id, { origin: button }).then(after);
    const title = "Connecting to " + (forward.label || host);
    const work = forward.managed
      ? AW.jobs.run("machine-connect", { host }, { title, origin: button, key: "machine:" + host })
      : AW.jobs.run("machine-attach", { host, port: forward.remote_port, model: row.remote_model || "", name: row.name || "" }, { title, origin: button, key: "machine:" + host });
    return work.then(after);
  }

  function accountItem(row) {
    const id = row.connection_id;
    const name = row.name || sourceLabel(row.provider) || "Connection";
    const base = { key: "account:" + id, title: name, open: () => AW.router.go("accounts", { select: id, returnTo: "overview" }), openLabel: "Open " + name + " in Connections" };
    const extra = row.remote_model ? [row.remote_model] : [];
    if (row.provider === NATIVE) return { ...base, mark: "idle", meta: ["Agent account", row.harness].filter(Boolean).join(" · "), actions: [more(id, name)] };
    if (row.ready === false)
      return {
        ...base,
        mark: "warn",
        level: "warn",
        warn: true,
        meta: ["Needs a key", ...extra].join(" · "),
        actions: [{ label: "Add key", run: () => AW.router.go("accounts", { select: id, focus: "key", returnTo: "overview" }) }, more(id, name)],
      };
    if (row.provider === ATTACHED && row.host) {
      const forward = list(AW.state.forwards).find((item) => item && item.connection_id === id);
      const machine = machineLabel(row.host);
      if (forward && forward.alive === false)
        return {
          ...base,
          mark: "down",
          level: "down",
          warn: true,
          meta: ["Tunnel down", machine].join(" · "),
          actions: [{ label: "Reconnect", pending: "Connecting…", run: (button) => reconnect(button, row, forward) }, more(id, name)],
        };
      return { ...base, mark: forward ? "ok" : "idle", meta: [forward ? "Tunnel open" : "Over SSH", machine, ...extra].join(" · "), actions: [more(id, name)] };
    }
    if (row.provider === ATTACHED) {
      const scan = AW.state.scan;
      if (!scan || !scan.received) return { ...base, mark: "idle", meta: ["Not checked yet", ...extra].join(" · "), actions: [more(id, name)] };
      const ago = AW.fmt.ago(scan.received);
      const found = seenIn(scan, row);
      if (found) {
        const models = list(found.models).length;
        return { ...base, mark: "ok", meta: ["Answering", models ? plural(models, "model", "models") : "", ago].filter(Boolean).join(" · "), actions: [more(id, name)] };
      }
      return {
        ...base,
        mark: "down",
        level: "down",
        warn: true,
        meta: ["Not answering", ago].join(" · "),
        actions: [{ label: "Scan", pending: "Scanning…", run: () => scanNow() }, more(id, name)],
      };
    }
    let word = "Key saved";
    if (row.key_source === "env") word = keyEnv(row.provider) ? "Key from " + keyEnv(row.provider) : "Key from an environment variable";
    else if (row.key_source === "memory") word = "Key kept until you quit";
    return { ...base, mark: "ok", meta: [word, ...extra].join(" · "), actions: [more(id, name)] };
  }

  function foundItem(endpoint) {
    const name = (endpoint.label || "Model server") + " · port " + endpoint.port;
    const models = list(endpoint.models).length;
    const attach = () => AW.attach.open({ port: endpoint.port, address: endpoint.base });
    return {
      key: "found:" + endpoint.port,
      mark: "ok",
      title: name,
      meta: ["Found, not attached", models ? plural(models, "model", "models") : ""].filter(Boolean).join(" · "),
      open: attach,
      openLabel: "Attach " + name,
      actions: [{ label: "Attach", run: attach }],
    };
  }

  function paintConnections() {
    const c = view.conns;
    if (!c) return;
    const accounts = list(AW.state.accounts).filter((row) => row && row.connection_id);
    const scan = AW.state.scan;
    const found = list(scan && scan.endpoints).filter((item) => item && !item.connection_id && item.port);
    const loaded = AW.store.age("accounts") !== Infinity;
    const error = AW.store.error("accounts");
    c.loading.hidden = loaded || Boolean(error);
    busy(c.part, !loaded && !error);
    const live = AW.state.live;
    const quietLocal = (!live || live.label === "Stopped") && !chosenModel() && !AW.jobs.find("server");
    const nothing = loaded && !accounts.length && !found.length && quietLocal;
    const items = nothing ? [] : [localItem(), ...accounts.map(accountItem), ...found.map(foundItem)];
    c.list.set(items);
    if (error && !loaded) {
      if (!c.errorShown) AW.ui.showError(c.part.errors, error, { retry: () => AW.store.refresh("accounts").catch(() => {}) });
      c.errorShown = true;
    } else if (c.errorShown) {
      AW.ui.clearError(c.part.errors);
      c.errorShown = false;
    }
    empty(
      c.emptyHost,
      nothing ? "No place to run a model yet. Scan for a running server, download a model, or add a hosted API." : null,
      [
        { label: "Scan", pending: "Scanning…", run: () => scanNow() },
        { label: "Model library", run: () => AW.router.go("models", { returnTo: "overview" }) },
        { label: "Add a hosted API…", run: () => AW.router.go("accounts", { add: "hosted", returnTo: "overview" }) },
      ],
      "nothing",
    );
    const hintText = list(scan && scan.hints).map((hint) => text(hint && hint.text)).filter(Boolean);
    if (c.hints.dataset.sig !== hintText.join("\n")) {
      c.hints.dataset.sig = hintText.join("\n");
      c.hints.replaceChildren(...hintText.map((line) => el("p", { class: "hint", text: line })));
    }
    const needs = items.filter((item) => item.level).length;
    counted(c.part, loaded ? items.length : null, needs ? "· " + plural(needs, "needs attention", "need attention") : "");
  }

  // Machines
  function buildMachines() {
    const part = panel("machines", "Machines");
    const loading = AW.ui.skeleton(2, "machines");
    const host = el("div", { class: "rows" });
    const emptyHost = el("div");
    const add = AW.ui.button("Add machine…", { kind: "link", icon: "plus", onClick: () => AW.router.go("machines", { add: true, returnTo: "overview" }) });
    add.classList.add("overview-foot");
    part.body.append(loading, host, emptyHost, add);
    view.machines = { part, loading, host, emptyHost, add, probes: new Map(), list: keyed(host) };
    return part;
  }

  async function probe(host) {
    const m = view.machines;
    try {
      const value = await AW.request("machine-probe", { host });
      m.probes.set(host, { value });
    } catch (thrown) {
      m.probes.set(host, { error: AW.asError(thrown) });
    }
    paintMachines();
  }

  function probeLine(value) {
    const system = [text(value.system), text(value.arch)].filter(Boolean).join(" ");
    const gpu = text(value.gpu_vendor) && Number(value.gpu_gib) > 0 ? AW.fmt.gib(value.gpu_gib) + " GPU memory" : "GPU memory unknown";
    return ["Connected", system, gpu].filter(Boolean).join(" · ");
  }

  function machineItem(host, name) {
    const m = view.machines;
    const tunnels = list(AW.state.forwards).filter((item) => item && item.host === host);
    const remote = list(AW.state.sessions).filter((item) => item && item.kind === "remote" && item.host === host).length;
    const down = tunnels.some((item) => item.alive === false);
    const result = m.probes.get(host);
    let mark = down ? "down" : tunnels.length ? "ok" : "idle";
    let meta = tunnels.length || remote ? [plural(tunnels.length, "tunnel", "tunnels"), plural(remote, "remote session", "remote sessions")].join(" · ") : "Not checked yet";
    let warn = down;
    if (down) meta = "Tunnel down · " + meta;
    if (result && result.value) {
      mark = down ? "down" : "ok";
      meta = probeLine(result.value) + (tunnels.length || remote ? " · " + plural(tunnels.length, "tunnel", "tunnels") : "");
    } else if (result && result.error) {
      mark = "down";
      warn = true;
      meta = result.error.message;
    }
    return {
      key: host,
      mark,
      level: mark === "down" ? "down" : "",
      warn,
      title: name && name !== host ? name + " · " + host : host,
      meta,
      open: () => AW.router.go("machines", { select: host, host, returnTo: "overview" }),
      openLabel: "Open " + (name || host) + " in Machines",
      actions: [{ label: "Check", pending: "Checking…", run: () => probe(host) }],
    };
  }

  function paintMachines() {
    const m = view.machines;
    if (!m) return;
    const saved = AW.state.machines && typeof AW.state.machines === "object" ? AW.state.machines : null;
    const loaded = saved !== null;
    const error = AW.store.error("machines");
    m.loading.hidden = loaded || Boolean(error);
    busy(m.part, !loaded && !error);
    const items = loaded ? Object.entries(saved).map(([host, name]) => machineItem(host, text(name))) : [];
    m.list.set(items);
    if (error && !loaded) {
      if (!m.errorShown) AW.ui.showError(m.part.errors, error, { retry: () => AW.store.refresh("machines").catch(() => {}) });
      m.errorShown = true;
    } else if (m.errorShown) {
      AW.ui.clearError(m.part.errors);
      m.errorShown = false;
    }
    empty(m.emptyHost, loaded && !items.length ? "No SSH machines." : null, [{ label: "Add machine…", run: () => AW.router.go("machines", { add: true, returnTo: "overview" }) }], "none");
    m.add.hidden = loaded && !items.length;
    const needs = items.filter((item) => item.level).length;
    counted(m.part, loaded ? items.length : null, needs ? "· " + plural(needs, "needs attention", "need attention") : "");
  }

  // This computer
  function buildComputer() {
    const refresh = AW.ui.button("Refresh", { small: true, id: "refresh", icon: "refresh", pending: "Refreshing…", onClick: () => refreshHardware() });
    const part = panel("computer", "This computer");
    const loading = AW.ui.skeleton(3, "hardware");
    const gpu = el("p", { class: "overview-chip-name", id: "gpu" });
    const kind = el("span", { id: "memory-kind" });
    const vram = el("span", { class: "overview-num", id: "vram" });
    const meter = el("progress", { id: "memory", attrs: { max: "100", "aria-labelledby": "memory-kind" } });
    const ramValue = el("span", { class: "overview-num" });
    const ram = el("div", { class: "overview-meter-line", id: "ram" }, el("span", { text: "System memory in use" }), ramValue);
    const engineLine = el("span");
    const engineReason = el("p", { class: "hint", hidden: true });
    const install = AW.ui.button("Install", { small: true, onClick: () => installEngine(install) });
    const task = el("div", { class: "overview-task", tabindex: -1 });
    const port = el("span");
    const copy = AW.ui.button("Copy key", {
      small: true,
      icon: "copy",
      onClick: () => AW.request("server-copy-key").then(() => AW.ui.toast("Copied.")),
    });
    copy.setAttribute("aria-label", "Copy the local API key");
    const facts = el(
      "div",
      { class: "overview-computer" },
      gpu,
      el("div", { class: "overview-meter" }, el("div", { class: "overview-meter-line" }, kind, vram), meter),
      ram,
      el("div", { class: "overview-line" }, engineLine, install),
      engineReason,
      task,
      el("div", { class: "overview-line" }, port, copy),
      el("div", { class: "overview-line-end" }, refresh),
    );
    part.body.append(loading, facts);
    view.computer = { part, loading, facts, gpu, kind, vram, meter, ram, ramValue, engineLine, engineReason, install, task, port, copy, refresh, taskRow: null };
    return part;
  }

  function refreshHardware() {
    const k = view.computer;
    AW.ui.clearError(k.part.errors);
    return Promise.all([AW.store.refresh("hardware", { force: true }), AW.store.refresh("engine", { force: true })]).catch((thrown) =>
      AW.ui.showError(k.part.errors, thrown, { retry: () => refreshHardware() }),
    );
  }

  function installEngine(button) {
    const k = view.computer;
    AW.ui.clearError(k.part.errors);
    const run = AW.fix.has("engine.install")
      ? AW.fix.run("engine.install", null, { origin: button })
      : AW.jobs.run("engine-install", {}, { title: "Installing the engine for local models", origin: button, key: "engine" });
    paintComputer();
    return Promise.resolve(run)
      .then(() => AW.store.refresh("engine"))
      .catch((thrown) => {
        const error = AW.asError(thrown);
        if (error.code === "cancelled") AW.ui.toast(error.message);
        else AW.ui.showError(k.part.errors, error, { retry: () => installEngine(k.install) });
      })
      .finally(paintComputer);
  }

  function memory(hardware) {
    const total = Number(hardware.memory_gib);
    const free = hardware.free_gib === null || hardware.free_gib === undefined ? null : Number(hardware.free_gib);
    const kind = hardware.memory_kind === "unified" ? "Unified memory in use" : hardware.memory_kind === "vram" ? "GPU memory in use" : "Memory in use";
    if (!(total > 0)) return { kind, value: "unknown", used: null, total: null };
    if (free === null || !Number.isFinite(free)) return { kind, value: "in use: unknown · " + total.toFixed(1) + " GiB", used: null, total };
    const used = Math.max(0, total - free);
    return { kind, value: used.toFixed(1) + " / " + total.toFixed(1) + " GiB", used, total };
  }

  function paintComputer() {
    const k = view.computer;
    if (!k) return;
    const hardware = AW.state.hardware;
    const error = AW.store.error("hardware");
    k.loading.hidden = Boolean(hardware) || Boolean(error);
    k.facts.hidden = !hardware && !k.facts.dataset.shown;
    busy(k.part, !hardware && !error);
    if (error && !hardware) {
      if (!k.errorShown) AW.ui.showError(k.part.errors, error, { retry: () => refreshHardware() });
      k.errorShown = true;
      k.facts.hidden = true;
    } else if (k.errorShown && hardware) {
      AW.ui.clearError(k.part.errors);
      k.errorShown = false;
    }
    k.facts.classList.toggle("stale", Boolean(hardware && error));
    if (hardware) {
      k.facts.dataset.shown = "true";
      const primary = list(hardware.gpus)[Number(hardware.primary) || 0];
      setText(k.gpu, (primary && primary.name) || hardware.cpu || hardware.label || "Unknown hardware");
      const line = memory(hardware);
      setText(k.kind, line.kind);
      setText(k.vram, line.value);
      k.meter.hidden = line.used === null;
      if (line.used !== null) k.meter.value = Math.round((line.used / line.total) * 100);
      const total = Number(hardware.ram_gib);
      const free = Number(hardware.ram_free_gib);
      k.ram.hidden = Boolean(hardware.unified) || !(total > 0);
      setText(k.ramValue, total > 0 ? (Number.isFinite(free) && hardware.ram_free_gib !== null ? Math.max(0, total - free).toFixed(1) + " / " : "in use: unknown · ") + total.toFixed(1) + " GiB" : "");
    }
    const engine = AW.state.engine;
    const job = AW.jobs.list().find((record) => record.state === "running" && record.action === "engine-install");
    const active = document.activeElement;
    const focusHere = active === k.install || k.task.contains(active);
    if (job && focusHere && active === k.install) k.focusTask = true;
    if (job && (!k.taskRow || k.taskRow.handle !== job.handle)) {
      if (k.taskRow) k.taskRow.part.destroy();
      k.taskRow = { handle: job.handle, part: AW.ui.taskRow(job.handle, { compact: true }) };
      k.task.replaceChildren(k.taskRow.part.el);
    } else if (!job && k.taskRow) {
      k.taskRow.part.destroy();
      k.taskRow = null;
    }
    k.task.hidden = !job;
    if (!engine) setText(k.engineLine, AW.store.error("engine") ? "Engine for local models: not checked" : "Engine for local models: checking…");
    else if (engine.engine_ready) setText(k.engineLine, "Engine for local models: installed");
    else if (engine.supported === false) setText(k.engineLine, "Engine for local models: not available here");
    else setText(k.engineLine, "Engine for local models: not installed" + (Number(engine.download_bytes) > 0 ? " (" + AW.fmt.bytes(engine.download_bytes) + ")" : ""));
    k.install.hidden = !engine || Boolean(engine.engine_ready) || engine.supported === false || Boolean(job);
    if (job && k.focusTask) {
      const cancel = [...k.task.querySelectorAll("button")].find((button) => !button.hidden);
      if (cancel) {
        cancel.focus();
        k.focusTask = false;
      } else if (focusHere && !k.task.contains(document.activeElement)) k.task.focus();
    } else if (!job && (focusHere || k.focusTask)) {
      k.focusTask = false;
      (k.install.hidden ? k.refresh : k.install).focus();
    }
    k.engineReason.hidden = !(engine && engine.supported === false && text(engine.reason));
    setText(k.engineReason, engine && engine.supported === false ? text(engine.reason) : "");
    const live = AW.state.live;
    setText(k.port, live && live.port ? "Local API port " + live.port : "Local API port: not known yet");
    const attention = engine && !engine.engine_ready && engine.supported !== false ? "· engine not installed" : "";
    counted(k.part, null, attention);
  }

  // Setup notice
  function paintNotice() {
    const host = view.notice;
    if (!host) return;
    const progress = AW.state.progress;
    const setUp = list(AW.state.accounts).length > 0 || chosenModel() || list(AW.state.folders).some((folder) => folder && folder.harness);
    const show = Boolean(progress && progress.skipped && !progress.open && !setUp && !view.noticeDismissed);
    if (show === !host.hidden) return;
    host.hidden = !show;
    host.replaceChildren(
      ...(show
        ? [
            AW.ui.notice("info", "Nothing is set up yet. The setup guide takes a few steps and ends with a running agent.", {
              actions: [{ label: "Open setup guide", primary: true, run: () => AW.guide.open() }],
              dismissible: true,
              onDismiss() {
                view.noticeDismissed = true;
                host.hidden = true;
              },
            }),
          ]
        : []),
    );
  }

  // Loading what the board shows
  function loadVersions() {
    if (view.versionsAsked) return;
    view.versionsAsked = true;
    AW.request("onboarding-state", { sections: ["clis"] }).then(
      (state) => {
        const rows = state && state.clis && state.clis.ok ? list(state.clis.value) : [];
        view.versions = Object.fromEntries(rows.filter((item) => item && item.name && item.version).map((item) => [item.name, String(item.version)]));
        paintCurrent();
      },
      () => {
        view.versionsAsked = false;
      },
    );
  }

  function loadKits() {
    if (view.kitsAsked) return;
    view.kitsAsked = true;
    AW.request("kit-list").then(
      (rows) => {
        view.kits = Object.fromEntries(list(rows).filter((item) => item && item.id).map((item) => [item.id, text(item.label) || item.id]));
        paintCurrent();
        paintProjects();
      },
      () => {
        view.kitsAsked = false;
      },
    );
  }

  function refreshAll() {
    const form = AW.form.get();
    if (form.workspace) {
      quiet("folder");
      quiet("resolve");
      quiet("consent");
    }
    quiet("compat");
    if (form.provider !== LOCAL && form.provider !== NATIVE && form.remote_model) quiet("context");
    quiet("folders");
    quiet("accounts");
    quiet("machines");
    quiet("forwards");
    quiet("sessions");
    if (!AW.state.hardware) quiet("hardware");
    if (!AW.state.engine) quiet("engine");
    if (!AW.state.sourceTable) quiet("sourceTable");
    if (!AW.state.progress) quiet("progress");
    if (AW.store.age("scan") > SCAN_STALE_MS) quiet("scan");
    loadVersions();
    loadKits();
    loadRecent();
  }

  function paintAll() {
    paintNotice();
    paintCurrent();
    paintRunning();
    paintProjects();
    paintConnections();
    paintMachines();
    paintComputer();
  }

  function mount(root) {
    view.notice = el("div", { class: "overview-notice", hidden: true });
    const current = buildCurrent();
    const running = buildRunning();
    const projects = buildProjects();
    const conns = buildConnections();
    const machines = buildMachines();
    const computer = buildComputer();
    view.parts = [current, running, projects, conns, machines, computer];
    root.append(
      el("h1", { class: "title", id: "overview-title", tabindex: -1, text: "Overview" }),
      el("p", { class: "lede", text: "What is set up and what is running." }),
      view.notice,
      current.el,
      el("div", { class: "overview-board" }, el("div", { class: "overview-main" }, running.el, projects.el), el("div", { class: "overview-side" }, conns.el, machines.el, computer.el)),
    );
    narrow.addEventListener("change", () => {
      view.parts.forEach(frame);
      paintCurrent();
    });
    low.addEventListener("change", paintCompact);
    const visible = (fn) => (...args) => {
      if (!root.hidden) fn(...args);
    };
    AW.store.on(["folder", "resolve", "compat", "consent", "config", "context", "accounts", "sourceTable"], visible(paintCurrent));
    AW.form.on(visible(paintCurrent));
    AW.readiness.on(visible(paintCurrent));
    AW.store.on(["sessions", "exits"], visible(() => {
      paintRunning();
      paintProjects();
      paintMachines();
    }));
    AW.store.on(["folders", "folder"], visible(() => {
      paintProjects();
      paintNotice();
    }));
    AW.store.on(["accounts", "scan", "live", "forwards", "config", "sourceTable", "machines"], visible(paintConnections));
    AW.form.on(visible(paintConnections));
    AW.store.on(["machines", "forwards"], visible(paintMachines));
    AW.store.on(["hardware", "engine", "live"], visible(paintComputer));
    AW.store.on(["progress", "accounts", "config"], visible(paintNotice));
    AW.jobs.on(
      visible((record) => {
        if (record.action === "engine-install") paintComputer();
        if (record.action === "server-start" || record.action === "server-stop") paintConnections();
      }),
    );
    AW.form.on((_form, changed) => {
      if (!root.hidden && Array.isArray(changed) && changed.includes("workspace")) loadRecent();
    });
    AW.store.on("folder", visible(() => {
      if (view.recent && view.recent.workspace !== AW.form.get().workspace) loadRecent();
    }));
  }

  AW.router.register("overview", {
    title: "Overview",
    mount,
    show() {
      paintAll();
      refreshAll();
    },
  });

  AW.commands.register({ id: "overview.hardware", title: "Refresh hardware", group: "Actions", keywords: "memory gpu computer", run: () => AW.store.refresh("hardware", { force: true }) });
})();
