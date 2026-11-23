// Advanced, Setup: one row per tool of studio-check with its install, update, sign-in and guide verbs as tasks, Doctor and the setup guide
// (ui.md 5.6). It needs only studio-check, studio-setup, studio-setup-log and studio-doctor, so it works when app-state failed; agent
// versions come from the onboarding-state `clis` section when it answers.
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const ui = AW.ui;
  const TOOLS_FRESH_MS = 60000;
  const CLIS_FRESH_MS = 60000;
  const VERB_LABELS = { Install: "Install", Update: "Update", Login: "Sign in", Docs: "Docs ↗" };
  const VERB_NAMES = { Install: "Install {name}", Update: "Update {name}", Login: "Sign in to {name}", Docs: "Open the {name} guide in your browser" };
  const DOCTOR_INTRO = "Doctor checks Python, the data folder, the credential vault, engines, GPU drivers, agent CLIs, ports and SSH on this computer.";
  const WORDS = { ok: "Passed", warn: "Warning", fail: "Failed", skip: "Skipped" };
  const MARKS = { ok: "ok", warn: "warn", fail: "down", skip: "idle" };
  const CHIPS = { ok: "ok", warn: "warn", fail: "danger", skip: null };
  const ENGINE_TOOL = "llama.cpp / Strata";
  const COMMAND = /^Run(?: in Command Prompt)?:\s+([\s\S]+)$/;

  let clis = null;
  let clisFlight = null;
  let doctor = null;

  const slug = (name) => String(name).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const keyOf = (name, verb) => (verb === "Install" || verb === "Update" ? "tool:" + name : "tool:" + name + ":" + verb);
  const plural = (n, one, many) => n + " " + (n === 1 ? one : many);

  function loadClis(force) {
    if (!force && clis && Date.now() - clis.at < CLIS_FRESH_MS) return Promise.resolve(clis.rows);
    if (clisFlight && !force) return clisFlight;
    clisFlight = AW.request("onboarding-state", { sections: ["clis"] })
      .then((value) => {
        const section = value && value.clis;
        const rows = new Map();
        if (section && section.ok && Array.isArray(section.value)) for (const row of section.value) if (row && typeof row.name === "string") rows.set(row.name, row);
        clis = { at: Date.now(), rows };
        return rows;
      })
      .catch(() => (clis ? clis.rows : new Map()))
      .finally(() => {
        clisFlight = null;
      });
    return clisFlight;
  }

  function agentNames(checks) {
    const table = AW.state.sourceTable;
    const listed = table && Array.isArray(table.harnesses) ? table.harnesses.map((row) => row && row.id).filter((id) => typeof id === "string") : null;
    const config = AW.state.config;
    const order = listed && listed.length ? listed : config && Array.isArray(config.agents) ? config.agents : AW.tools.AGENTS;
    return order.filter((name) => Object.hasOwn(checks, name));
  }

  function otherNames(checks) {
    const engines = AW.state.engine && Array.isArray(AW.state.engine.engines) ? AW.state.engine.engines : null;
    return Object.keys(checks).filter((name) => !AW.tools.AGENTS.includes(name) && !(name === "vLLM" && engines && !engines.includes("vLLM")));
  }

  function toolRow(name, ctx) {
    const mark = ui.mark("idle");
    const word = el("span", { class: "sr-only" });
    const title = el("span", { class: "row-title" }, name, word);
    const status = el("span", { class: "row-meta" });
    const path = el("span", { class: "row-meta mono", hidden: true });
    const hint = el("span", { class: "row-meta wrap", hidden: true });
    hint.style.whiteSpace = "normal";
    hint.style.color = "var(--warn-text)";
    const actions = el("div", { class: "row-actions" });
    actions.style.flexWrap = "wrap";
    actions.style.justifyContent = "flex-end";
    const tasks = el("div");
    const result = el("div", { class: "stack" });
    const more = el("div", { class: "stack", hidden: true }, tasks, result);
    more.style.gridColumn = "1 / -1";
    more.style.gap = "var(--space-3)";
    const row = el("div", { class: "row adv-tool", id: "tool-" + slug(name), data: { tool: name } }, mark, el("div", { class: "row-main" }, title, status, path, hint), actions, more);
    let shape = "";
    const followed = new Map();

    const fit = () => {
      more.hidden = !tasks.childElementCount && !result.childElementCount;
    };

    function follow(key) {
      const record = AW.jobs.find(key);
      if (!record || followed.has(record.handle)) return;
      const task = ui.taskRow(record.handle);
      followed.set(record.handle, task);
      tasks.append(task.el);
      fit();
      lock();
    }

    function settled(record) {
      const task = followed.get(record.handle);
      if (!task) return;
      followed.delete(record.handle);
      const focused = task.el.contains(document.activeElement);
      task.destroy();
      fit();
      lock();
      if (focused) {
        const first = actions.querySelector("button");
        if (first) first.focus();
      }
    }

    function lock() {
      const busy = Boolean(AW.jobs.find("tool:" + name));
      for (const button of actions.querySelectorAll("button[data-verb]")) {
        const verb = button.dataset.verb;
        if (verb !== "Install" && verb !== "Update") continue;
        if (busy && !button.dataset.job) button.setAttribute("aria-disabled", "true");
        else if (!busy && !button.dataset.job && !button.dataset.pending) button.removeAttribute("aria-disabled");
      }
    }

    function show(value, verb) {
      const changes = verb === "Install" || verb === "Update";
      const kind = value.outcome === "failed" ? "danger" : value.outcome === "installed" ? "ok" : "info";
      const text = [value.message, value.hint].filter((part) => typeof part === "string" && part).join(" ") || (value.outcome === "failed" ? "The step failed. Open the setup log to see why." : "Done.");
      const extra = [];
      if (value.outcome === "failed") extra.push({ label: "Open setup log", run: () => AW.request("studio-setup-log") });
      else if (changes && (value.outcome === "opened-docs" || value.outcome === "started-terminal")) extra.push({ label: "Recheck installations", run: () => recheckAll(ctx) });
      result.replaceChildren(ui.notice(kind, text, { actions: extra, dismissible: true, onDismiss: fit }));
      fit();
      ui.announce(text, { assertive: value.outcome === "failed" });
    }

    function act(verb, button) {
      result.replaceChildren();
      fit();
      const promise = AW.tools.install(name, { origin: button, verb });
      follow(keyOf(name, verb));
      return promise.then(
        (value) => {
          if (!row.isConnected) return;
          show(value, verb);
          if (value.outcome === "installed" && AW.tools.AGENTS.includes(name)) loadClis(true).then(() => ctx.alive() && paintAll(ctx));
        },
        (error) => {
          if (!row.isConnected) return;
          ui.showError(result, error, { tool: name, retry: () => act(verb, button) });
          for (const notice of result.querySelectorAll("[data-error] .btn")) notice.addEventListener("click", () => setTimeout(fit));
          fit();
        },
      );
    }

    function update(location, cli, managed = false) {
      const installed = Boolean(location) || managed;
      const version = cli && installed && typeof cli.version === "string" ? cli.version : "";
      const offPath = Boolean(cli && installed && cli.on_path === false);
      ui.setMark(mark, installed ? (offPath ? "warn" : "ok") : "idle");
      word.textContent = installed ? ", installed" : ", not installed";
      const text = installed ? "Installed" + (version ? " · " + version : managed && !location ? " · the engine for local models" : "") : "Not installed";
      if (status.textContent !== text) status.textContent = text;
      path.hidden = !location;
      path.textContent = location ? AW.fmt.middle(AW.fmt.path(location), 80) : "";
      path.title = AW.fmt.path(location);
      const hintText = offPath && typeof cli.hint === "string" ? cli.hint : "";
      hint.hidden = !hintText;
      hint.textContent = hintText;
      const verbs = AW.tools.verbs(name, installed);
      if (verbs.join() !== shape) {
        const focused = actions.contains(document.activeElement) ? document.activeElement.dataset.verb : null;
        shape = verbs.join();
        actions.replaceChildren(
          ...verbs.map((verb) => {
            const button = ui.button(VERB_LABELS[verb], { small: true, onClick: () => act(verb, button) });
            button.dataset.verb = verb;
            button.setAttribute("aria-label", VERB_NAMES[verb].replace("{name}", name));
            return button;
          }),
        );
        if (focused) {
          const again = actions.querySelector('[data-verb="' + focused + '"]') || actions.querySelector("button");
          if (again) again.focus();
        }
      }
      lock();
    }

    for (const verb of ["Install", "Update", "Login", "Docs"]) follow(keyOf(name, verb));
    return { el: row, name, update, follow, settled, keys: ["Install", "Update", "Login", "Docs"].map((verb) => keyOf(name, verb)) };
  }

  function recheckAll(ctx) {
    return Promise.all([AW.tools.recheck(), loadClis(true)]).then(() => {
      if (ctx.alive()) paintAll(ctx);
      AW.ui.announce("Checked the installed tools.");
    });
  }

  function paintAll(ctx) {
    const state = ctx.setup;
    const checks = AW.state.tools;
    if (!state || !checks || typeof checks !== "object") return;
    const rows = clis ? clis.rows : new Map();
    for (const [host, names] of [
      [state.agents, agentNames(checks)],
      [state.others, otherNames(checks)],
    ]) {
      const signature = names.join("\n");
      if (host.dataset.names !== signature) {
        host.dataset.names = signature;
        const built = names.map((name) => state.rows.get(name) || toolRow(name, ctx));
        for (const row of built) state.rows.set(row.name, row);
        host.replaceChildren(...built.map((row) => row.el));
        if (!names.length) host.append(ui.empty(host === state.agents ? "No agent was reported. Choose Recheck installations." : "No other tools were reported. Choose Recheck installations."));
      }
      const engine = AW.state.engine;
      for (const name of names) state.rows.get(name).update(checks[name], rows.get(name), name === ENGINE_TOOL && Boolean(engine && engine.engine_ready));
    }
  }

  function reveal(tool) {
    const row = AW.$("tool-" + slug(tool));
    if (!row) return false;
    for (const other of document.querySelectorAll(".adv-tool[aria-selected]")) other.removeAttribute("aria-selected");
    row.setAttribute("aria-selected", "true");
    row.scrollIntoView({ block: "center" });
    const first = row.querySelector(".row-actions button");
    if (first) first.focus();
    return true;
  }

  function toolsPanels(ctx) {
    const recheck = ui.button("Recheck installations", { small: true, pending: "Checking…", onClick: () => run() });
    const agentsPanel = ui.panel("Agents", { actions: [recheck] });
    const othersPanel = ui.panel("Other tools");
    const errors = el("div");
    const agents = el("div", { class: "rows" }, ui.skeleton(3, "agents"));
    const others = el("div", { class: "rows" }, ui.skeleton(3, "other tools"));
    agentsPanel.body.append(errors, agents);
    othersPanel.body.append(others);
    ctx.setup = { agents, others, rows: new Map() };

    function run() {
      ui.clearError(errors);
      return recheckAll(ctx).catch((error) => {
        if (ctx.alive()) ui.showError(errors, error, { retry: run });
      });
    }

    async function load() {
      const fresh = AW.state.tools && typeof AW.state.tools === "object" && AW.store.age("tools") < TOOLS_FRESH_MS && !AW.store.error("tools");
      const versions = loadClis(false);
      if (!AW.state.engine || AW.store.age("engine") > TOOLS_FRESH_MS) AW.store.refresh("engine").catch(() => {});
      if (fresh) paintAll(ctx);
      else {
        agentsPanel.setBusy(true);
        try {
          await AW.store.refresh("tools", { force: true });
        } catch (error) {
          if (!ctx.alive()) return;
          agentsPanel.setBusy(false);
          agents.replaceChildren();
          agents.dataset.names = "";
          othersPanel.el.hidden = true;
          ui.showError(errors, error, {
            retry() {
              ui.clearError(errors);
              agents.replaceChildren(ui.skeleton(3, "agents"));
              othersPanel.el.hidden = false;
              return load();
            },
          });
          return;
        }
        if (!ctx.alive()) return;
        agentsPanel.setBusy(false);
        paintAll(ctx);
      }
      versions.then(() => ctx.alive() && paintAll(ctx));
    }

    ctx.cleanup(AW.store.on(["tools", "sourceTable", "engine"], () => ctx.alive() && paintAll(ctx)));
    ctx.cleanup(
      AW.jobs.on((record, kind) => {
        if (!ctx.alive() || !record.key || !record.key.startsWith("tool:")) return;
        for (const row of ctx.setup.rows.values()) {
          if (!row.keys.includes(record.key)) continue;
          if (kind === "started") row.follow(record.key);
          else if (kind !== "progress") row.settled(record);
        }
      }),
    );
    return { panels: [agentsPanel.el, othersPanel.el], load };
  }

  function summary(counts) {
    const c = counts || {};
    return [Number(c.ok) || 0, Number(c.warn) || 0, Number(c.fail) || 0];
  }

  function tally(checks) {
    const counts = { ok: 0, warn: 0, fail: 0, skip: 0 };
    for (const check of checks) if (Object.hasOwn(counts, check.status)) counts[check.status] += 1;
    return counts;
  }

  function flag(counts) {
    if (AW.rail && typeof AW.rail.mark === "function") AW.rail.mark("nav-advanced", counts && Number(counts.fail) > 0 ? "warn" : null);
  }

  function doctorPanel(ctx) {
    const run = ui.button("Run Doctor", { small: true, id: "doctor", pending: "Checking…", onClick: () => check() });
    const panel = ui.panel("Doctor", { actions: [run] });
    const intro = el("p", { class: "muted", text: DOCTOR_INTRO });
    const head = el("p", { attrs: { role: "status" } });
    head.style.fontWeight = "var(--weight-strong)";
    const when = el("p", { class: "hint" });
    const errors = el("div");
    const list = el("div", { class: "rows", id: "doctor-list", attrs: { "aria-label": "Doctor checks" } });
    const top = el("div", { class: "stack" }, intro, el("div", null, head, when), errors);
    top.style.gap = "var(--space-3)";
    top.style.marginBottom = "var(--space-4)";
    panel.body.append(top, list);

    function doctorRow(item) {
      const status = Object.hasOwn(WORDS, item.status) ? item.status : "warn";
      const main = el("div", { class: "row-main" }, el("span", { class: "row-title" }, String(item.title || item.id || ""), el("span", { class: "sr-only", text: ", " + WORDS[status].toLowerCase() })));
      if (item.detail) main.append(el("p", { class: "hint wrap", text: AW.fmt.paths(item.detail) }));
      const fix = typeof item.fix === "string" ? item.fix : "";
      if (fix && (status === "warn" || status === "fail")) {
        const line = el("p", { class: "wrap", text: AW.fmt.paths(fix) });
        line.style.fontSize = "var(--text-md)";
        line.style.marginTop = "var(--space-2)";
        main.append(line);
      }
      const actions = el("div", { class: "row-actions" }, ui.chip(WORDS[status], CHIPS[status] || undefined));
      actions.style.flexWrap = "wrap";
      actions.style.justifyContent = "flex-end";
      const command = fix && (status === "warn" || status === "fail") ? COMMAND.exec(fix.trim()) : null;
      const copy = command
        ? ui.button("Copy", {
            small: true,
            icon: "copy",
            onClick: async () => {
              const copied = await AW.clipboard.write(command[1].trim());
              ui.toast(copied ? "Copied the command." : "The command could not be copied. Select it and copy it yourself.", copied ? { kind: "ok" } : { kind: "danger" });
            },
          })
        : null;
      if (copy) {
        copy.setAttribute("aria-label", "Copy the command for " + String(item.title || item.id || "this check"));
        actions.append(copy);
      }
      if (item.id === "automation" && AW.platform.mac)
        actions.append(ui.button("Check Terminal permission", { small: true, pending: "Checking…", onClick: () => automation() }));
      const mark = ui.mark(MARKS[status]);
      mark.style.marginTop = "var(--space-3)";
      const row = el("div", { class: "row", data: { check: item.id || "" } }, mark, main, actions);
      row.style.alignItems = "start";
      return row;
    }

    function paint() {
      const value = doctor && doctor.value;
      const checks = value && Array.isArray(value.checks) ? value.checks.filter((item) => item && typeof item === "object") : [];
      intro.hidden = Boolean(value);
      head.hidden = !value;
      when.hidden = !value;
      if (!value) {
        list.replaceChildren();
        return;
      }
      const [ok, warn, fail] = summary(value.counts || tally(checks));
      head.textContent = ok + " passed · " + plural(warn, "warning", "warnings") + " · " + fail + " failed";
      when.textContent = "Checked " + AW.fmt.ago(doctor.at) + ".";
      list.replaceChildren(...checks.map(doctorRow));
      if (!checks.length) list.append(ui.empty("Doctor reported no checks. Run it again."));
    }

    function busy(on) {
      list.setAttribute("aria-busy", String(on));
      if (!on) list.removeAttribute("aria-busy");
      if (!on) return;
      if (!list.querySelector(".row")) {
        list.replaceChildren(ui.skeleton(3, "Doctor"));
        return;
      }
      for (const row of list.querySelectorAll(".row")) {
        ui.setMark(row.querySelector(".mark"), "busy");
        const chip = row.querySelector(".row-actions .chip");
        if (chip) {
          chip.className = "chip";
          chip.textContent = "Checking…";
        }
      }
    }

    async function check() {
      ui.clearError(errors);
      busy(true);
      let value;
      try {
        value = await AW.request("studio-doctor", {});
      } catch (error) {
        if (!ctx.alive()) return;
        busy(false);
        paint();
        ui.showError(errors, error, { retry: () => run.click() });
        return;
      }
      doctor = { value, at: Date.now() };
      flag(value && value.counts);
      if (!ctx.alive()) return;
      busy(false);
      paint();
      const [ok, warn, fail] = summary(value && value.counts);
      ui.announce("Doctor finished: " + ok + " passed, " + plural(warn, "warning", "warnings") + ", " + fail + " failed.");
    }

    async function automation() {
      ui.clearError(errors);
      let value;
      try {
        value = await AW.request("studio-doctor", { ids: ["automation"], active: true });
      } catch (error) {
        if (ctx.alive()) ui.showError(errors, error);
        return;
      }
      const fresh = value && Array.isArray(value.checks) ? value.checks.find((item) => item && item.id === "automation") : null;
      if (!fresh || !doctor || !doctor.value || !Array.isArray(doctor.value.checks)) return;
      const checks = doctor.value.checks.map((item) => (item && item.id === "automation" ? fresh : item));
      doctor = { value: { checks, counts: tally(checks) }, at: doctor.at };
      flag(doctor.value.counts);
      if (!ctx.alive()) return;
      paint();
      const button = list.querySelector('[data-check="automation"] .row-actions button:last-child');
      if (button) button.focus();
      ui.announce("Terminal automation: " + (WORDS[fresh.status] || fresh.status) + ". " + (fresh.detail || ""));
    }

    paint();
    return { el: panel.el, check, button: run, ran: () => Boolean(doctor) };
  }

  function guidePanel() {
    const panel = ui.panel("Setup guide");
    const errors = el("div");
    panel.body.append(
      el("p", { class: "muted", text: "The setup guide takes you through this computer, the model, the agent and the project folder again. Your choices are kept." }),
      el(
        "div",
        { class: "actions" },
        ui.button("Open setup guide", { onClick: () => AW.guide.open() }),
        ui.button("Open setup log", {
          onClick: () => {
            ui.clearError(errors);
            return AW.request("studio-setup-log").catch((error) => ui.showError(errors, error));
          },
        }),
      ),
      errors,
    );
    errors.style.marginTop = "var(--space-4)";
    return panel.el;
  }

  AW.advanced.section("Setup", async (body, ctx) => {
    const tools = toolsPanels(ctx);
    const doc = doctorPanel(ctx);
    body.append(...tools.panels, doc.el, guidePanel());
    const wanted = ctx.tool || (ctx.params && ctx.params.tool) || "";
    if (ctx.focus === "doctor" && (!doctor || Date.now() - doctor.at > 60000)) doc.button.click();
    await tools.load();
    if (!ctx.alive()) return;
    if (wanted) reveal(wanted);
    else if (ctx.focus === "doctor") {
      doc.el.scrollIntoView({ block: "start" });
      doc.button.focus({ preventScroll: true });
    }
  });

  AW.commands.register({
    id: "doctor.run",
    title: "Run Doctor",
    group: "Actions",
    keywords: "check diagnose environment problems",
    run: () => AW.router.go("advanced", { section: "Setup", focus: "doctor", run: Date.now() }),
  });
})();
