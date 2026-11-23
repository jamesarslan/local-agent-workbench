// Project setup view: the frame, "Show for", the five tabs and the helpers the tab files share (ui.md 5.3).
// Each tab file registers itself with AW.projectSetup.tab({id, label, order, mount(panel), update(ctx), leave?()}).
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const P = (AW.projectSetup = AW.projectSetup || {});
  const defs = [];
  const mounted = new Map();
  const ticket = AW.ticket();
  const NARROW = window.matchMedia("(max-width: 899px)");

  let parts = null;
  let tabs = null;
  let agent = "";
  let shownTab = "instructions";
  let lastFolder = "";
  let visible = false;
  let focusHeld = 0;
  let ctx = { workspace: "", name: "", harness: "", resolve: null, error: null, loading: false, fresh: true, config: {} };

  const CHIPS = {
    read: ["Read", "ok"],
    shadowed: ["Shadowed", "warn"],
    ignored: ["Ignored", "warn"],
    truncated: ["Truncated", "warn"],
    duplicate: ["Duplicate", ""],
    invalid: ["Invalid", "danger"],
    asks: ["Asks", ""],
    "needs-trust": ["Needs trust", "warn"],
  };

  P.tab = function (def) {
    defs.push(def);
  };

  P.chip = function (status) {
    const known = CHIPS[status];
    const word = known ? known[0] : String(status || "").replace(/^./, (c) => c.toUpperCase());
    return AW.ui.chip(word, known ? known[1] : "");
  };

  P.agents = function () {
    const config = AW.state.config || {};
    return Array.isArray(config.agents) && config.agents.length ? config.agents : ["OMP", "Pi", "Claude Code", "Codex", "Hermes"];
  };

  // The resolver returns one warning list; each sentence is shown on the tab it is about: skills, MCP tools, or instructions.
  P.topic = function (text) {
    const value = String(text || "");
    if (/\bskills?\b/i.test(value)) return "skills";
    if (/\b(MCP|servers?|tools?|plugins?)\b/i.test(value)) return "mcp";
    return "instructions";
  };

  P.context = () => ctx;
  P.agent = () => agent;
  P.go = (id) => (tabs ? tabs.select(id) : undefined);
  P.setCount = (id, n) => tabs && tabs.setCount(id, n);

  P.section = function (title, { count, id, caption } = {}) {
    const panel = AW.ui.panel(title, { count, id });
    panel.el.classList.add("project-panel");
    if (caption) panel.body.append(el("p", { class: "project-caption", text: caption }));
    return panel;
  };

  // Replace host's children while keeping focus on the control with the same data-k, so a refresh never drops the user's place.
  P.swap = function (host, ...children) {
    const active = document.activeElement;
    const key = active && host.contains(active) ? active.dataset.k : null;
    host.replaceChildren(...children.flat().filter(Boolean));
    if (key) {
      const again = [...host.querySelectorAll("[data-k]")].find((node) => node.dataset.k === key);
      if (again) again.focus({ preventScroll: true });
    }
  };

  P.keyed = function (node, key) {
    node.dataset.k = key;
    return node;
  };

  P.loading = function (host, label) {
    if (!host.querySelector(":scope > .skeleton") && !host.children.length) host.append(AW.ui.skeleton(3, label));
    host.classList.add("stale");
    host.setAttribute("aria-busy", "true");
  };
  P.loaded = function (host) {
    host.classList.remove("stale");
    host.removeAttribute("aria-busy");
  };

  P.lines = function (text) {
    const value = String(text || "");
    if (!value) return 0;
    return value.split("\n").length - (value.endsWith("\n") ? 1 : 0);
  };

  P.bytes = (text) => new TextEncoder().encode(String(text || "")).length;

  // The files project-setup-read opens: the folder's own instruction files, project.json and skills (project_setup.READABLE, SKILL_FILE).
  const READABLE = ["AGENTS.md", "AGENTS.override.md", "CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md", ".omp/AGENTS.md", ".hermes.md", "HERMES.md", ".agent-workbench/project.json"];
  const SKILL_FILE = /^(\.agents|\.claude|\.omp|\.pi|\.opencode|\.hermes)\/skills\/[a-z0-9-]{1,64}\/SKILL\.md$/;
  P.readable = (path) => READABLE.includes(path) || SKILL_FILE.test(String(path || ""));

  P.viewFile = async function (path, { workspace = ctx.workspace, origin } = {}) {
    const dialog = AW.ui.dialog("file-dialog");
    const body = AW.$("file-dialog-body");
    AW.$("file-dialog-title").textContent = path;
    body.replaceChildren(AW.ui.skeleton(4, path));
    dialog.open();
    try {
      const file = await AW.request("project-setup-read", { workspace, path });
      if (!file || !file.exists) {
        body.replaceChildren(AW.ui.empty(path + " is no longer in this folder."));
        return;
      }
      const size = file.lines === 1 ? "1 line" : file.lines + " lines";
      body.replaceChildren(
        el("p", { class: "project-caption", text: size + " · " + AW.fmt.bytes(file.bytes) + " · read only" }),
        el("pre", { class: "project-file", tabindex: 0, attrs: { "aria-label": path }, text: file.text }),
      );
    } catch (thrown) {
      body.replaceChildren(AW.ui.errorNotice(thrown, { retry: () => P.viewFile(path, { workspace, origin }) }));
    }
  };

  // Review changes for a project-setup plan: plan() asks for the plan, apply(plan, confirmed) writes it. Resolves with the result or null.
  P.review = async function ({ title, plan, apply, origin, host }) {
    let current;
    try {
      current = await (origin ? AW.ui.pending(origin, "Preparing…", plan()) : plan());
    } catch (thrown) {
      if (host) AW.ui.showError(host, thrown);
      else AW.ui.toast(AW.asError(thrown).message, { kind: "danger" });
      return null;
    }
    if (host) AW.ui.clearError(host);
    const result = await AW.planReview.open({
      title,
      plan: current,
      apply: (confirmed, shown) => apply(shown || current, confirmed),
      replan: async () => {
        current = await plan();
        return current;
      },
    });
    if (result) await P.refresh({ fresh: true });
    return result;
  };

  P.resultNotice = function (result) {
    if (!result || typeof result !== "object") return null;
    const written = Array.isArray(result.written) ? result.written : [];
    const skipped = Array.isArray(result.skipped) ? result.skipped : [];
    const failed = Array.isArray(result.failed) ? result.failed : [];
    const text = written.length === 1 ? "Wrote 1 file." : "Wrote " + written.length + " files.";
    const list = (title, rows) =>
      rows.length
        ? el("div", { class: "project-result" }, el("span", { class: "project-result-title", text: title }), el("ul", null, rows.map((row) => el("li", null, el("span", { class: "mono", text: AW.fmt.path(row.path) }), " " + (row.reason || "")))))
        : null;
    const body = el("div", null, el("p", { text }), list("Not written", skipped), list("Failed", failed));
    return AW.ui.notice(failed.length ? "warn" : "ok", body, { dismissible: true });
  };

  function paintAgent() {
    if (!parts) return;
    const agents = P.agents();
    if (parts.agentList !== agents.join("|")) {
      parts.agentList = agents.join("|");
      const segmented = AW.ui.segmented("Show for", agents, agent, (value) => choose(value));
      parts.segmented = segmented;
      parts.select.replaceChildren(...agents.map((name) => el("option", { value: name, text: name })));
      parts.segmentHost.replaceChildren(segmented.el);
    }
    parts.segmented.set(agent);
    parts.select.value = agent;
  }

  function dirty() {
    for (const [id, def] of mounted) if (def.dirty && def.dirty()) return id;
    return null;
  }

  async function keepEdits() {
    if (!dirty()) return false;
    const discard = await AW.ui.confirm({ text: "Discard your edits to AGENTS.md?", ok: "Discard", danger: true });
    if (!discard) return true;
    for (const def of mounted.values()) if (def.discard) def.discard();
    return false;
  }

  async function choose(value) {
    if (value === agent) return;
    if (await keepEdits()) {
      paintAgent();
      return;
    }
    agent = value;
    paintAgent();
    await P.refresh({ fresh: true });
  }

  function paintHead() {
    const workspace = AW.project.current() || AW.form.get().workspace;
    const name = workspace ? AW.project.name(workspace) : "";
    parts.lede.textContent =
      (name ? "Instruction files, skills and MCP tools in " + name + ", as each agent reads them." : "Instruction files, skills and MCP tools in your project folder, as each agent reads them.") +
      " MCP tools are extra programs an agent can call, such as a browser or a search service.";
    parts.empty.hidden = Boolean(workspace);
    parts.body.hidden = !workspace;
  }

  function publish(next) {
    ctx = { ...ctx, ...next };
    for (const def of mounted.values()) {
      try {
        def.update(ctx);
      } catch (thrown) {
        console.error(thrown);
      }
    }
  }

  P.refresh = async function ({ fresh = false } = {}) {
    if (!parts) return null;
    paintHead();
    const form = AW.form.get();
    const workspace = AW.project.current() || form.workspace;
    if (!workspace) {
      ticket.next();
      publish({ workspace: "", name: "", harness: agent, resolve: null, error: null, loading: false, fresh: true });
      return null;
    }
    const mine = ticket.next();
    const changed = workspace !== ctx.workspace || agent !== ctx.harness;
    publish({
      workspace,
      name: AW.project.name(workspace),
      harness: agent,
      config: AW.state.config || {},
      resolve: changed ? null : ctx.resolve,
      error: changed ? null : ctx.error,
      loading: true,
      fresh: fresh || changed,
    });
    if (changed) for (const id of ["instructions", "skills", "mcp"]) P.setCount(id, null);
    AW.ui.clearError(parts.stale);
    if ((fresh || changed) && !mounted.has("profiles")) P.countProfiles(workspace);
    try {
      const value = await AW.request("project-setup-resolve", {
        workspace,
        harness: agent,
        search: form.search,
        firecrawl: form.firecrawl,
        blender: form.blender,
        desktop: form.desktop,
        plugins: form.plugins,
      });
      if (!ticket.current(mine)) return value;
      counts(value);
      publish({ resolve: value, error: null, loading: false, fresh: false });
      return value;
    } catch (thrown) {
      if (!ticket.current(mine)) return null;
      const error = AW.asError(thrown);
      if (ctx.resolve) AW.ui.showError(parts.stale, { ...error, message: "This page may be out of date. " + error.message }, { retry: () => P.refresh({ fresh: true }) });
      publish({ error, loading: false, fresh: false });
      return null;
    }
  };

  function counts(value) {
    const size = (list) => (Array.isArray(list) ? list.length : 0);
    P.setCount("instructions", size(value.instructions));
    P.setCount("skills", size(value.skills));
    const launch = value.mcp && Array.isArray(value.mcp.launch) ? value.mcp.launch : [];
    P.setCount("mcp", launch.filter((row) => row.status === "sent").length);
  }

  let kitList = null;
  P.kits = function () {
    if (!kitList)
      kitList = AW.request("kit-list").catch((thrown) => {
        kitList = null;
        throw thrown;
      });
    return kitList;
  };

  P.countProfiles = async function (workspace = ctx.workspace) {
    if (!workspace) return null;
    try {
      const names = await AW.request("project-list", { workspace });
      if (workspace === ctx.workspace) P.setCount("profiles", Array.isArray(names) ? names.length : null);
      return Array.isArray(names) ? names : [];
    } catch {
      return null;
    }
  };

  const lazy = AW.debounce(() => visible && P.refresh(), 300);

  async function onFolder() {
    if (!parts) return;
    const now = AW.project.current() || AW.form.get().workspace;
    if (now === lastFolder) return;
    const before = lastFolder;
    lastFolder = now;
    if (visible && before && dirty()) {
      const discard = await AW.ui.confirm({ text: "Discard your edits to AGENTS.md?", ok: "Discard", danger: true });
      if (!discard) {
        lastFolder = before;
        AW.project.select(before).catch((thrown) => AW.ui.toast(AW.asError(thrown).message, { kind: "danger" }));
        return;
      }
      for (const def of mounted.values()) if (def.discard) def.discard();
    }
    if (visible) P.refresh({ fresh: true });
  }

  function build(host) {
    const select = el("select", { class: "project-agent-select", attrs: { "aria-label": "Show for" } });
    select.addEventListener("change", () => choose(select.value));
    const segmentHost = el("div", { class: "project-agent-segments" });
    const lede = el("p", { class: "lede" });
    const tabHost = el("div", { class: "project-tabs" });
    const body = el(
      "div",
      { class: "project-body" },
      el("div", { class: "project-agent", id: "setup-agent", role: "group", attrs: { "aria-label": "Show for" } }, el("span", { class: "project-agent-label", attrs: { "aria-hidden": "true" }, text: "Show for" }), segmentHost, select),
      tabHost,
    );
    const empty = AW.ui.empty("Choose a project folder to see what its agents read.", [{ label: "Choose a folder…", primary: true, run: () => AW.projectSwitch.open() }]);
    empty.classList.add("project-nofolder");
    host.append(el("h1", { class: "title", id: "project-title", tabindex: -1, text: "Project setup" }), lede, empty, body);
    parts = { select, segmentHost, lede, body, empty, agentList: "" };
    const list = [...defs].sort((a, b) => (a.order || 0) - (b.order || 0));
    tabs = AW.ui.tabs(
      tabHost,
      list.map((def) => ({
        id: def.id,
        label: def.label,
        count: undefined,
        render(panel) {
          panel.classList.add("project-tab-panel");
          mounted.set(def.id, def);
          def.mount(panel);
          def.update(ctx);
        },
      })),
      { selected: shownTab, onChange: onTab },
    );
    guardTabs(tabHost.querySelector('[role="tablist"]'), list.map((def) => def.id));
    parts.stale = el("div", { class: "project-notices project-stale" });
    tabHost.insertBefore(parts.stale, tabHost.querySelector(".tab-panels"));
  }

  // Ask before the tab changes away from unsaved AGENTS.md text; the tab stays where it is until the answer.
  function guardTabs(bar, ids) {
    if (!bar) return;
    const target = (event) => {
      if (event.type === "click") {
        const tab = event.target.closest('[role="tab"]');
        return tab ? tab.id.replace(/^tab-/, "") : null;
      }
      const at = ids.indexOf(shownTab);
      if (event.key === "ArrowRight") return ids[(at + 1) % ids.length];
      if (event.key === "ArrowLeft") return ids[(at - 1 + ids.length) % ids.length];
      if (event.key === "Home") return ids[0];
      if (event.key === "End") return ids[ids.length - 1];
      return null;
    };
    const stop = async (event) => {
      const next = target(event);
      if (!next || next === shownTab || dirty() !== shownTab) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (await keepEdits()) return;
      tabs.select(next);
      const tab = AW.$("tab-" + next);
      if (tab) tab.focus();
    };
    bar.addEventListener("click", stop, true);
    bar.addEventListener("keydown", stop, true);
  }

  function onTab(id) {
    shownTab = id;
  }

  AW.router.register("project", {
    title: "Project setup",
    mount(host) {
      build(host);
      AW.store.on(["folder"], onFolder);
      AW.form.on((form, changed) => {
        if (!Array.isArray(changed)) return;
        if (changed.includes("workspace")) onFolder();
        else if (["search", "firecrawl", "blender", "desktop", "plugins"].some((key) => changed.includes(key))) lazy();
      });
      AW.store.on(["config"], () => parts && paintAgent());
      NARROW.addEventListener("change", () => parts && paintAgent());
      window.addEventListener("focus", () => {
        if (!visible || Date.now() - focusHeld < 2000) return;
        focusHeld = Date.now();
        P.refresh();
      });
    },
    async show(params = {}) {
      visible = true;
      const form = AW.form.get();
      const agents = P.agents();
      if (!dirty()) agent = agents.includes(params.harness) ? params.harness : agents.includes(form.harness) ? form.harness : agents[0];
      lastFolder = AW.project.current() || form.workspace;
      paintAgent();
      paintHead();
      if (params.tab && params.tab !== shownTab) {
        shownTab = params.tab;
        tabs.select(params.tab);
      }
      const ready = P.refresh({ fresh: true });
      if (params.focus === "plugins") {
        await ready;
        const group = AW.$("plugins-group");
        if (group) {
          group.scrollIntoView({ block: "start" });
          const heading = group.querySelector(".panel-title");
          if (heading) {
            heading.tabIndex = -1;
            heading.focus({ preventScroll: true });
          }
        }
      }
      for (const def of mounted.values()) if (def.show) def.show(params);
    },
    hide() {
      visible = false;
    },
    async leave() {
      return !(await keepEdits());
    },
  });

  AW.commands.register({ id: "project.instructions", title: "Edit AGENTS.md for this folder", group: "Actions", when: () => Boolean(AW.project.current()), run: () => AW.router.go("project", { tab: "instructions" }) });
  AW.commands.register({ id: "project.mcp", title: "Show MCP tools for this folder", group: "Actions", when: () => Boolean(AW.project.current()), run: () => AW.router.go("project", { tab: "mcp" }) });
  AW.commands.register({ id: "project.kit", title: "Apply a starter kit…", group: "Actions", when: () => Boolean(AW.project.current()), run: () => AW.router.go("project", { tab: "kit" }) });
})();
