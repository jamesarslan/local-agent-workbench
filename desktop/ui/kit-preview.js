// Starter kit preview (ui.md 4.4, 5.3 Starter kit tab): what one kit writes into a folder for one agent, with its skills and tools to tick.
// AW.kitPreview.mount(root, { workspace, harness, kit, links = true, onPlan(plan | null), cli_version? }) -> { update(opts), plan(), review(), destroy() }
//   plan() is the latest kit-preview answer, or null while one loads or after an error. It carries `request`, the exact kit-preview request,
//   so kit-apply is { ...plan.request, plan: plan.id, confirmed }. onPlan reports every change, null included.
//   review() opens Review changes for the current plan and writes it with kit-apply -> Promise<apply result | null>.
// AW.kitPreview.review(plan) -> the same for a plan object that plan() returned.
// links: false inside the setup guide, which offers no way out to Advanced.
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const ui = AW.ui;
  const PROGRAMS = { node: "Node.js", uv: "uv", docker: "Docker" };
  const ACTIONS = { create: ["Create", "accent"], update: ["Update", "warn"], skip: ["Skip", ""] };
  const text = (value) => (typeof value === "string" ? value : "");
  const rows = (value) => (Array.isArray(value) ? value.filter((row) => row && typeof row === "object") : []);
  const lines = (value) => (value ? value.replace(/\n$/, "").split("\n").length : 0);
  const files = (n) => n + (n === 1 ? " file" : " files");
  const ident = (server) => (text(server.builtin) ? "builtin:" + server.builtin : text(server.name));
  let serial = 0;
  let kitList = null;

  function showError(host, thrown, options) {
    host.hidden = false;
    return ui.showError(host, thrown, options);
  }

  function clearError(host) {
    ui.clearError(host);
    host.hidden = true;
  }

  function kits() {
    if (!kitList)
      kitList = AW.request("kit-list").catch(() => {
        kitList = null;
        return [];
      });
    return kitList;
  }

  function style(node, rules) {
    Object.assign(node.style, rules);
    return node;
  }

  function writeReview(plan) {
    if (!plan || !plan.request) return Promise.resolve(null);
    const request = plan.request;
    return AW.planReview.open({
      title: "Review changes",
      plan,
      apply: (confirmed, shown) => AW.request("kit-apply", { ...request, plan: (shown || plan).id, confirmed }),
      replan: () => AW.request("kit-preview", request).then((fresh) => ({ ...fresh, request })),
    });
  }

  function mount(root, options = {}) {
    const opts = { links: true, ...options };
    const tag = "kit-" + ++serial;
    const ticket = AW.ticket();
    let latest = null;
    let picks = { servers: null, skills: null };
    let destroyed = false;
    let painted = false;
    let label = "";
    let summary = "";

    const head = el("p", { class: "group-label wrap" });
    const lede = el("p", { class: "muted wrap" });
    const body = el("div", { class: "stack" });
    const errors = el("div", { class: "stack", hidden: true });
    const busy = el("span", { class: "sr-only", role: "status" });
    const host = el("div", { class: "stack kit-preview" }, el("div", null, head, lede), errors, body, busy);
    root.append(host);

    const report = (plan) => {
      if (opts.onPlan) opts.onPlan(plan);
    };

    function request() {
      const out = { workspace: opts.workspace, kit: opts.kit, harness: opts.harness };
      if (picks.servers) out.servers = picks.servers;
      if (picks.skills) out.skills = picks.skills;
      if (opts.cli_version) out.cli_version = opts.cli_version;
      return out;
    }

    function toggle(name, target) {
      const button = ui.button(name, { small: true });
      button.setAttribute("aria-expanded", "false");
      button.setAttribute("aria-controls", target.id);
      target.hidden = true;
      button.addEventListener("click", () => {
        const open = target.hidden;
        target.hidden = !open;
        button.setAttribute("aria-expanded", String(open));
        button.querySelector(".btn-label").textContent = open ? "Hide" : name;
      });
      return button;
    }

    function plainPane(content, labelText) {
      const pre = style(el("pre", { class: "diff", tabindex: 0, attrs: { "aria-label": labelText } }), { whiteSpace: "pre-wrap" });
      for (const line of content.replace(/\n$/, "").split("\n")) pre.append(style(el("span", { text: line || " " }), { minWidth: "0" }));
      return pre;
    }

    function line(...children) {
      return style(el("p", { class: "muted wrap" }, ...children), { fontSize: "var(--text-sm)", lineHeight: "1.45" });
    }

    function pick(kind, name, title, checked, describedBy) {
      const box = el("input", { type: "checkbox", checked, data: { pick: kind + ":" + name } });
      if (describedBy) box.setAttribute("aria-describedby", describedBy);
      box.addEventListener("change", changed);
      style(box, { marginTop: "var(--space-1)" });
      const row = el("label", { class: "check" }, box, title);
      style(row, { alignItems: "flex-start" });
      return row;
    }

    function instructions(plan) {
      const content = text(plan.agents_md);
      if (!content) return line("This kit adds no instruction file.");
      const pane = plainPane(content, "The kit's AGENTS.md");
      pane.id = tag + "-agents";
      const row = el("div", { class: "actions" }, el("span", { class: "plan-path", text: "AGENTS.md" }), el("span", { class: "muted", text: lines(content) + " lines" }), toggle("View", pane));
      return el("div", { class: "stack" }, row, pane);
    }

    function skills(plan) {
      const list = rows(plan.skills);
      if (!list.length) return line("This kit adds no skills.");
      const out = style(el("div", { class: "stack" }), { gap: "var(--space-4)" });
      for (const skill of list) {
        const name = text(skill.name);
        const descId = tag + "-skill-" + name;
        const desc = skill.description ? style(el("small", { class: "muted wrap", id: descId, text: text(skill.description) }), { display: "block" }) : null;
        const title = el("span", null, el("span", { class: "plan-path", text: name }), desc);
        out.append(pick("skill", name, title, Boolean(skill.selected), skill.description ? descId : ""));
      }
      return out;
    }

    function requirement(program, missing) {
      const name = PROGRAMS[program] || program;
      if (!missing) return line("Needs " + name + " · installed");
      if (!opts.links) return style(line("Needs " + name + ", which was not found. You can install it later in Advanced → Setup."), { color: "var(--warn-text)" });
      const link = ui.button("Install in Advanced → Setup", { kind: "link", onClick: () => AW.fix.run("go.setup", name) });
      style(link, { fontSize: "var(--text-sm)" });
      const row = el("div", { class: "actions" }, style(line("Needs " + name + " · not installed"), { color: "var(--warn-text)" }), link);
      style(row, { gap: "var(--space-2) var(--space-4)" });
      return row;
    }

    function tools(plan) {
      const list = rows(plan.servers);
      if (!list.length) return line("This kit adds no tools.");
      const out = style(el("div", { class: "stack" }), { gap: "var(--space-5)" });
      list.forEach((server, at) => {
        const builtin = Boolean(text(server.builtin));
        const id = ident(server);
        const infoId = tag + "-tool-" + at;
        const chips = el("span", { class: "actions" });
        style(chips, { display: "inline-flex", gap: "var(--space-3)", marginLeft: "var(--space-4)" });
        if (builtin) chips.append(ui.chip("Built into Agent Workbench"));
        if (server.checked === "registry") chips.append(ui.chip("Not launched by this project's tests"));
        if (server.runs_code === true) chips.append(ui.chip("Runs code", "warn"));
        const title = el("span", null, el("span", { class: "wrap", text: text(server.label) || id }), chips.childNodes.length ? chips : null);
        const info = el("div", { class: "stack", id: infoId });
        style(info, { gap: "var(--space-1)", paddingLeft: "var(--space-8)" });
        const missing = Array.isArray(server.missing) ? server.missing : [];
        for (const program of Array.isArray(server.requires) ? server.requires : []) info.append(requirement(program, missing.includes(program)));
        if (text(server.note)) info.append(style(line(server.note), { color: "var(--warn-text)" }));
        if (text(server.warning)) info.append(line(server.warning));
        if (text(server.network)) info.append(line("Network: " + server.network));
        for (const name of Array.isArray(server.needs) ? server.needs : [])
          if (typeof name === "string" && name) info.append(line("Needs a value for " + name + ". You can add it in Project setup → MCP tools once the kit is written."));
        if (!builtin && text(server.summary)) info.append(style(line(el("span", { class: "mono", text: server.summary })), { overflowWrap: "anywhere" }));
        const by = builtin ? "" : [text(server.publisher), text(server.license), server.version ? "version " + server.version : ""].filter(Boolean).join(" · ");
        if (by) info.append(line(by));
        out.append(el("div", null, pick("server", id, title, Boolean(server.selected), infoId), info));
      });
      return out;
    }

    function fileList(plan) {
      const list = rows(plan.items);
      if (!list.length) return line("Nothing would be written for this kit.");
      const out = style(el("ul", { class: "stack" }), { gap: "var(--space-3)" });
      list.forEach((item, at) => {
        const [word, kind] = ACTIONS[item.action] || [text(item.action), ""];
        const top = el("div", { class: "actions" }, ui.chip(word, kind || undefined), el("span", { class: "plan-path", text: AW.fmt.path(text(item.path)) }));
        style(top, { gap: "var(--space-2) var(--space-4)" });
        const row = el("li", { class: "stack" }, top);
        style(row, { gap: "var(--space-2)" });
        if (text(item.reason)) row.append(line(item.reason));
        if (text(item.diff) && item.action !== "create") {
          const pane = ui.diff(item.diff);
          pane.id = tag + "-diff-" + at;
          pane.setAttribute("aria-label", "Changes in " + AW.fmt.path(text(item.path)));
          top.append(toggle("View change", pane));
          row.append(pane);
        }
        out.append(row);
      });
      return out;
    }

    function paintHead() {
      const folder = AW.state.folder;
      const applied = folder && folder.kit === opts.kit && folder.path && folder.path === latest?.workspace;
      head.replaceChildren((label || opts.kit || "") + (opts.harness ? " · for " + opts.harness : ""));
      if (applied) head.append(" ", ui.chip("applied here", "ok"));
      lede.textContent = summary;
      lede.hidden = !summary;
    }

    function render(plan) {
      const focused = document.activeElement && host.contains(document.activeElement) ? document.activeElement.dataset.pick : null;
      paintHead();
      const writes = rows(plan.items).filter((item) => item.action !== "skip").length;
      const warnings = rows(plan.warnings).length ? plan.warnings : [];
      const sections = ui.kv([
        ["Instructions", instructions(plan)],
        ["Skills", skills(plan)],
        ["Tools", tools(plan)],
        ["Files", el("div", { class: "stack" }, line(writes ? files(writes) + " to write" : "Nothing new to write"), fileList(plan))],
      ]);
      style(sections, { gap: "var(--space-6) var(--space-6)", alignItems: "start" });
      body.replaceChildren(
        ...warnings.filter((sentence) => typeof sentence === "string" && sentence).map((sentence) => ui.notice("warn", sentence)),
        sections,
        line("A file that already exists is never replaced without showing you the change."),
      );
      painted = true;
      if (focused) {
        const again = host.querySelector('[data-pick="' + CSS.escape(focused) + '"]');
        if (again) again.focus();
      }
    }

    function settle(on) {
      if (on) host.setAttribute("aria-busy", "true");
      else host.removeAttribute("aria-busy");
      body.classList.toggle("stale", on && Boolean(latest || body.firstChild));
    }

    function empty(sentence) {
      ticket.next();
      latest = null;
      settle(false);
      paintHead();
      painted = false;
      body.replaceChildren(ui.empty(sentence));
      clearError(errors);
      report(null);
    }

    async function load() {
      if (destroyed) return;
      if (!opts.workspace) return empty("Choose a project folder to see what this kit adds.");
      if (!opts.kit) return empty("Choose a kit to see what it adds.");
      if (!opts.harness) return empty("Choose an agent to see what this kit adds for it.");
      const mine = ticket.next();
      const asked = request();
      const first = !painted;
      latest = null;
      report(null);
      clearError(errors);
      settle(true);
      busy.textContent = "Loading the starter kit preview";
      if (first) body.replaceChildren(ui.skeleton(4, "the starter kit preview"));
      try {
        const [plan, list] = await Promise.all([AW.request("kit-preview", asked), kits()]);
        if (!ticket.current(mine) || destroyed) return;
        const kit = (Array.isArray(list) ? list : []).find((row) => row && row.id === opts.kit);
        label = kit ? text(kit.label) : "";
        summary = kit ? text(kit.summary) : "";
        latest = { ...plan, request: asked };
        settle(false);
        busy.textContent = "";
        render(latest);
        report(latest);
      } catch (thrown) {
        if (!ticket.current(mine) || destroyed) return;
        settle(false);
        busy.textContent = "";
        label = "";
        summary = "";
        paintHead();
        painted = false;
        body.replaceChildren();
        showError(errors, thrown, { retry: () => load() });
      }
    }

    const later = AW.debounce(load, 250);
    const unwatch = AW.store.on("folder", () => {
      if (latest) paintHead();
    });

    function changed() {
      const chosen = (kind) => [...host.querySelectorAll('input[data-pick^="' + kind + ':"]')].filter((box) => box.checked).map((box) => box.dataset.pick.slice(kind.length + 1));
      picks = { servers: chosen("server"), skills: chosen("skill") };
      ticket.next();
      latest = null;
      report(null);
      settle(true);
      later();
    }

    function update(next = {}) {
      const before = { workspace: opts.workspace, kit: opts.kit };
      Object.assign(opts, next);
      if (opts.workspace !== before.workspace || opts.kit !== before.kit) picks = { servers: null, skills: null };
      later.cancel();
      return load();
    }

    load();
    return {
      update,
      plan: () => latest,
      review() {
        const plan = latest;
        return writeReview(plan).then((result) => {
          if (result && !destroyed) load();
          return result;
        });
      },
      destroy() {
        destroyed = true;
        later.cancel();
        unwatch();
        ticket.next();
        host.remove();
      },
    };
  }

  AW.kitPreview = { mount, review: writeReview };
})();
