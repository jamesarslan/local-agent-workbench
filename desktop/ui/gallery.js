// Component gallery: route "dev", registered only when localStorage.dev is "1" (ui.md 5.10). Shows every shared component in the current theme.
(function () {
  "use strict";
  const AW = window.AW;
  if (AW.storage.get("dev") !== "1") return;
  const el = AW.el;
  const ui = AW.ui;
  const ICONS = ["launch", "overview", "project", "connections", "machines", "advanced", "guide", "terminal", "sun", "moon", "search", "caret", "close", "folder", "check", "warning", "external", "copy", "refresh", "more", "back", "plus", "computer", "server", "key", "plug"];
  const SWATCHES = ["bg", "surface", "surface-sunken", "surface-control", "surface-hover", "border", "border-strong", "border-control", "text", "text-muted", "text-faint", "accent", "accent-hover", "accent-soft", "focus", "ok", "ok-soft", "warn", "warn-soft", "danger", "danger-soft", "info-soft", "idle", "term-bg"];

  const row = (...children) => el("div", { class: "dev-row" }, children);
  function panel(title, wide, ...children) {
    const part = ui.panel(title);
    if (wide) part.el.classList.add("dev-wide");
    part.body.append(...children);
    return part.el;
  }
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function theme() {
    const control = ui.segmented("Theme", [{ value: "light", label: "Light" }, { value: "dark", label: "Dark" }, { value: "system", label: "Match system" }], AW.theme.get().choice, (value) => AW.theme.set(value));
    AW.theme.on((next) => control.set(next.choice));
    return control.el;
  }

  function colours() {
    return row(
      SWATCHES.map((name) => {
        const chip = el("i");
        chip.style.background = "var(--" + name + ")";
        return el("span", { class: "dev-swatch" }, chip, name);
      }),
    );
  }

  function type() {
    return [
      el("p", { class: "title", text: "Page title" }),
      el("p", { class: "lede", text: "Lede: one sentence that says what the page is for." }),
      el("h2", { text: "Panel heading, 15 px" }),
      el("p", { text: "Body text, 14 px. Choose where your agent works and what powers it." }),
      el("p", { class: "muted", text: "Muted text for secondary lines." }),
      el("p", { class: "group-label", text: "Group label" }),
      el("small", { class: "hint", text: "Caption, 12 px." }),
      el("p", { class: "mono", text: "/Users/me/code/shop · monospace" }),
    ];
  }

  function buttons() {
    const off = ui.button("Disabled", {});
    off.setAttribute("aria-disabled", "true");
    const offPrimary = ui.button("Disabled", { kind: "primary" });
    offPrimary.setAttribute("aria-disabled", "true");
    return [
      row(ui.button("Default", {}), ui.button("Primary", { kind: "primary" }), ui.button("Quiet", { kind: "quiet" }), ui.button("Remove", { kind: "danger" }), ui.button("Remove", { kind: "primary danger" }), off, offPrimary),
      row(ui.button("Small", { small: true }), ui.button("Small primary", { small: true, kind: "primary" }), ui.button("With icon", { icon: "refresh", small: true }), ui.button("Refresh", { kind: "icon", icon: "refresh" }), ui.button("Close", { kind: "icon quiet", icon: "close", small: true })),
      row(ui.button("Link button", { kind: "link" }), ui.button("Get an API key ↗", { kind: "link" }), ui.button("Scan", { pending: "Scanning…", onClick: () => wait(1600) }), ui.button("Fails", { onClick: () => Promise.reject({ message: "This button always fails. Nothing was changed." }) })),
    ];
  }

  function fields() {
    const name = ui.field("Connection name", el("input", { type: "text", attrs: { placeholder: "Personal OpenRouter" } }), { hint: "Shown in the Model source list." });
    const host = ui.field("SSH host", el("input", { type: "text", value: "gpu box" }));
    host.setError("Use an SSH config alias or user@hostname.");
    const select = ui.field("Model source", el("select", null, el("option", { text: "Local model" }), el("option", { text: "Ollama on this computer" })));
    const model = ui.field("Model", el("div", { class: "inputrow" }, el("input", { type: "text", class: "mono", value: "qwen3:8b" }), ui.button("Browse models", {})), { hint: "32K context, reported by the server" });
    const prompt = ui.field("Prompt supplement", el("textarea", { attrs: { placeholder: "Extra instructions for the agent" } }));
    const inline = ui.field("Reasoning effort", el("select", null, el("option", { text: "medium" }), el("option", { text: "high" })));
    inline.el.classList.add("inline");
    return [name.el, host.el, select.el, model.el, prompt.el, inline.el];
  }

  function switches() {
    const search = ui.toggle("Web search", { description: "Search public sources", checked: true });
    const crawl = ui.toggle("Page crawling", { description: "Read pages with local Firecrawl" });
    crawl.setNote("Needs Docker, which is not installed.");
    const group = el("div", { class: "choices" });
    const one = ui.choice({ name: "dev-agent", value: "OMP", title: "OMP", description: "19.1.0", badge: "Measured" });
    const two = ui.choice({ name: "dev-agent", value: "Codex", title: "Codex", description: "0.42.0" });
    const three = ui.choice({ name: "dev-agent", value: "Hermes", title: "Hermes", description: "Not with this source", body: el("p", { class: "muted", text: "The body opens when the card is chosen." }) });
    one.input.checked = true;
    two.setDisabled("Codex needs the Responses API, which this source does not offer.");
    three.setOpen(true);
    group.append(one.el, two.el, three.el);
    const check = el("label", { class: "check" }, el("input", { type: "checkbox", checked: true }), "Allow file edits");
    return [search.el, crawl.el, el("div", { class: "dev-row" }), group, row(ui.segmented("Open in", ["Terminal dock", "New window"], "Terminal dock").el, check)];
  }

  function tabs() {
    const host = el("div");
    const text = (words) => (root) => root.append(el("p", { class: "muted", text: words }));
    ui.tabs(host, [
      { id: "dev-instructions", label: "Instructions", render: text("Instruction files this agent reads.") },
      { id: "dev-skills", label: "Skills", count: 5, render: text("Skills are added by a starter kit.") },
      { id: "dev-mcp", label: "MCP tools", count: 3, render: text("MCP tools are extra programs an agent can call, such as a browser or a search service.") },
    ]);
    const side = el("div", { class: "split" });
    ui.tabs(side, [
      { id: "dev-setup", label: "Setup", render: text("Vertical tabs list the Advanced sections.") },
      { id: "dev-library", label: "Model library", render: text("Your model folder.") },
    ], { vertical: true });
    return [host, side];
  }

  function rows() {
    const host = el("div");
    const list = ui.list(host, {
      key: (item) => item.id,
      render: (item) => {
        const part = ui.row({ mark: item.mark, title: item.title, meta: item.meta, onOpen: item.open ? () => ui.toast(item.title + " opened.") : undefined, actions: item.actions });
        if (item.selected) part.el.setAttribute("aria-selected", "true");
        return part.el;
      },
      empty: "No connections yet.",
    });
    list.set([
      { id: 1, mark: "ok", title: "Ollama on this computer", meta: "Answering · checked 8 s ago", open: true, actions: [{ label: "Check" }] },
      { id: 2, mark: "idle", title: "Local model", meta: "Stopped", open: true, selected: true, actions: [{ label: "Start" }] },
      { id: 3, mark: "warn", title: "Work Groq", meta: "Needs an API key", open: true },
      { id: 4, mark: "down", title: "gpu-box", meta: "The SSH tunnel is down", actions: [{ label: "Reconnect", primary: true }] },
      { id: 5, mark: "busy", title: "LM Studio · port 1234", meta: "Checking…" },
    ]);
    const emptyHost = el("div");
    ui.list(emptyHost, { key: (item) => item.id, render: () => el("div"), empty: "Nothing is running." }).set([]);
    return [
      host,
      row(ui.mark("ok"), "ok", ui.mark("idle"), "idle", ui.mark("warn"), "warn", ui.mark("down"), "down", ui.mark("busy"), "busy"),
      row(ui.chip("Suggested"), ui.chip("Measured", "ok"), ui.chip("Not confirmed", "warn"), ui.chip("Failed", "danger"), ui.chip("Current", "accent"), ui.chip("Suggested", null, { help: "This pick follows the sizing rule for your memory. It has not been measured on this computer." })),
      emptyHost,
    ];
  }

  function notices() {
    return el(
      "div",
      { class: "stack" },
      ui.notice("info", "New folder. It starts with the choices you used last: OMP · Ollama · qwen3:8b.", { actions: [{ label: "Use this setup" }], dismissible: true }),
      ui.notice("ok", "The Workbench service is running again.", { dismissible: true }),
      ui.notice("warn", "Some remembered choices no longer exist here: Work Groq. Choose again below."),
      ui.notice("danger", "Codex could not be installed. Open Advanced → Setup to try again.", { detail: "npm ERR! code EACCES\nnpm ERR! syscall mkdir", actions: [{ label: "Open Advanced → Setup" }], dismissible: true }),
      ui.errorNotice({ message: "Agent Workbench did not get an answer within 75 seconds. Try again; if it keeps happening, restart Agent Workbench.", code: "timeout", action: "retry" }, { retry: () => ui.toast("Tried again.") }),
      ui.errorNotice({ message: "The download was cancelled. The partial file is kept.", code: "cancelled" }),
    );
  }

  function readiness() {
    const host = el("div");
    ui.readinessList(host).update({
      ok: false,
      needs: "Launch needs: a key for Work Groq, an agent that works with Work Groq.",
      slots: { project: { level: "ok", summary: "shop" }, model: { level: "block", summary: "Work Groq" }, agent: { level: "block", summary: "Codex 0.42.0" }, setup: { level: "warn", summary: "AGENTS.md · 5 skills · 3 tools on" } },
      items: [
        { id: "model.credential", slot: "model", level: "block", text: "Work Groq needs an API key.", fixes: [{ label: "Save key", run: "dev.none", field: { label: "API key", type: "password" } }, { label: "Get an API key ↗", run: "go.accounts" }] },
        { id: "agent.compat", slot: "agent", level: "block", title: "Codex cannot use Work Groq", text: "Codex needs the Responses API, which Work Groq does not offer; choose OMP, Pi or opencode or another source.", fixes: [{ label: "Use OMP", run: "agent.use", arg: "OMP", primary: true }, { label: "Check Work Groq again", run: "source.refresh" }] },
        { id: "setup.tool-prereq", slot: "setup", level: "warn", text: "Page crawling needs Docker, which is not installed.", fixes: [{ label: "Open Advanced → Setup", run: "go.setup" }] },
      ],
    });
    return host;
  }

  function tasks() {
    const now = Date.now();
    const base = { action: "model-download", key: null, origin: null, error: null, value: null };
    return [
      ui.taskRow({ ...base, handle: "dev-1", title: "Downloading Qwen3 8B Q4", started: now - 95000, cancellable: true, state: "running", progress: { message: "Downloading", done: 9.4 * 2 ** 30, total: 23.6 * 2 ** 30, rate: 38 * 2 ** 20 } }).el,
      ui.taskRow({ ...base, handle: "dev-2", title: "Installing on gpu-box", started: now - 252000, cancellable: false, state: "running", progress: null }).el,
      ui.taskRow({ ...base, handle: "dev-3", title: "Starting the local model", started: now - 14000, cancellable: true, state: "running", progress: { message: "Loading the model…", budget: 600 } }).el,
      ui.taskRow({ ...base, handle: "dev-4", title: "Installing Codex", started: now - 300000, cancellable: false, state: "failed", error: { message: "Codex could not be installed. Open Advanced → Setup to try again." } }).el,
      ui.taskRow({ ...base, handle: "dev-5", title: "Installing the engine for local models", started: now - 400000, cancellable: false, state: "done" }).el,
    ];
  }

  function data() {
    const meter = el("progress", { attrs: { max: "100", "aria-label": "Unified memory in use" } });
    meter.value = 49;
    const plan = ui.planTable(
      [
        { path: "AGENTS.md", action: "create", reason: "" },
        { path: ".agents/skills/review/SKILL.md", action: "update", reason: "The kit's version differs from yours." },
        { path: "CLAUDE.md", action: "skip", reason: "Already up to date." },
      ],
      { onSelect: () => {} },
    );
    return [
      ui.kv([["Context", "32K"], ["API port", "10579"], ["Unified memory in use", "23.4 / 48.0 GiB"]]),
      meter,
      el("div", { class: "dev-row" }),
      ui.worksWith({ OMP: { ok: true, reason: "" }, Codex: { ok: false, reason: "Codex needs the Responses API, which this server does not offer." }, "Claude Code": { ok: true, reason: "The Messages API was not checked on this server." } }),
      el("div", { class: "dev-row" }),
      plan.el,
      ui.diff("@@ -1,3 +1,4 @@\n # Project instructions\n-Run tests with npm test.\n+Run tests with npm test before every commit.\n+Never edit files under vendor/."),
      ui.skeleton(3, "connections"),
      ui.empty("No folders yet. Choose Browse to pick a project folder.", [{ label: "Browse…" }, { label: "Open setup guide", primary: true }]),
    ];
  }

  function overlays() {
    const menu = ui.button("Menu", { onClick: () => ui.menu(menu, [{ label: "Rename", run() {} }, { label: "Copy the resume command", run() {} }, { label: "Remove", danger: true, run() {} }]) });
    const pop = ui.button("Popover", {
      onClick: () =>
        ui.popover(pop, (root) => root.append(el("div", { class: "popover-head" }, el("h2", { class: "popover-title", text: "Local model" })), el("div", { class: "popover-body" }, el("p", { class: "muted", text: "Popovers close on Esc, on a click outside and when focus leaves." }), el("div", { class: "popover-actions" }, ui.button("Start", { kind: "primary", small: true }), ui.button("Copy key", { small: true })))), { label: "Local model" }),
    });
    return row(
      ui.button("Toast", { onClick: () => ui.toast("OMP is running in shop.") }),
      ui.button("Toast with action", { onClick: () => ui.toast("Downloading Qwen3 8B Q4 finished.", { kind: "ok", action: { label: "Show", run() {} } }) }),
      ui.button("Error toast", { onClick: () => ui.toast("Installing Codex failed.", { kind: "danger" }) }),
      ui.button("Confirm", { onClick: () => ui.confirm({ text: "Stop the local model? 2 sessions are using it.", ok: "Stop" }).then((yes) => ui.toast(yes ? "Confirmed." : "Cancelled.")) }),
      ui.button("Confirm removal", { onClick: () => ui.confirm({ text: "Remove Work Groq? Its saved key is removed too.", ok: "Remove", danger: true }) }),
      ui.button("Keyboard shortcuts", { onClick: () => AW.strip.openKeys() }),
      ui.button("Notice in the notices row", { onClick: () => ui.notify("warn", "The Workbench service stopped and is restarting. Embedded terminals were closed.", { id: "dev" }) }),
      menu,
      pop,
    );
  }

  function icons() {
    return el("div", { class: "dev-icons" }, ICONS.map((name) => el("span", null, ui.icon(name), name)));
  }

  AW.router.register("dev", {
    title: "Components",
    mount(root) {
      root.append(
        el("h1", { class: "title", id: "dev-title", tabindex: -1, text: "Components" }),
        el("p", { class: "lede", text: "Every shared component in the current theme." }),
        row(theme()),
        el(
          "div",
          { class: "dev-grid" },
          panel("Colours", true, colours()),
          panel("Type", false, ...type()),
          panel("Buttons", false, ...buttons()),
          panel("Fields", false, ...fields()),
          panel("Switches, radio cards, segmented control", false, ...switches()),
          panel("Tabs", false, ...tabs()),
          panel("Rows, marks and chips", false, ...rows()),
          panel("Notices and errors", false, notices()),
          panel("Readiness list", false, readiness()),
          panel("Task rows", false, ...tasks()),
          panel("Lists, tables and loading", false, ...data()),
          panel("Overlays", true, overlays(), row(el("kbd", { text: "Cmd" }), el("kbd", { text: "K" }), el("kbd", { text: "F6" }), el("kbd", { text: "Esc" }))),
          panel("Icons", true, icons()),
        ),
      );
    },
    show() {},
  });
})();
