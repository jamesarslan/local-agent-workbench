// Setup guide, steps 3 (Agent) and 4 (Project) (ui.md 4.3, 4.4).
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const NATIVE = "Native login";
  const AGENTS = ["OMP", "Pi", "Claude Code", "Codex", "Hermes", "opencode"];
  const LINES = {
    OMP: "An open-source terminal coding agent (oh-my-pi).",
    Pi: "An open-source terminal coding agent.",
    "Claude Code": "Anthropic's coding agent for the terminal.",
    Codex: "OpenAI's coding agent for the terminal.",
    Hermes: "An agent that uses its own account and provider settings.",
    opencode: "An open-source terminal coding agent (opencode.ai).",
  };
  const SUBSCRIPTIONS = [
    { value: "openai-codex-device", label: "ChatGPT device sign-in" },
    { value: "anthropic", label: "Anthropic account" },
  ];
  const list = (value) => (Array.isArray(value) ? value : []);
  const text = (value) => (typeof value === "string" ? value : "");
  const steps = (AW.guideSteps = AW.guideSteps || {});

  // Step 3: the agent.
  const agentState = { cards: new Map(), results: new Map(), preselected: false, tasks: new Map(), key: "" };

  function harnesses() {
    const table = AW.state.sourceTable;
    const ids = table && Array.isArray(table.harnesses) ? table.harnesses.map((row) => row && row.id).filter(Boolean) : (AW.state.config && AW.state.config.agents) || AGENTS;
    return [...AGENTS.filter((id) => ids.includes(id)), ...ids.filter((id) => !AGENTS.includes(id))];
  }

  function cliRow(ctx, name) {
    const rows = ctx.value("clis");
    const row = Array.isArray(rows) ? rows.find((item) => item && item.name === name) : null;
    if (row) return row;
    const present = (AW.state.config && AW.state.config.cli_present) || {};
    return { name, installed: Boolean(present[name]), on_path: true, hint: "", version: "" };
  }

  const native = () => AW.form.get().provider === NATIVE;

  function verdict(name) {
    if (native()) return null;
    const compat = AW.state.compat;
    return compat && typeof compat === "object" && compat[name] ? compat[name] : null;
  }

  function usable(ctx, name) {
    if (!name) return false;
    const row = cliRow(ctx, name);
    const answer = verdict(name);
    return Boolean(row.installed) && !(answer && answer.ok === false);
  }

  function agentSentence(ctx) {
    return native() ? "and lets it use its own account." : "and connects it to " + ctx.sourceName() + ".";
  }

  async function install(ctx, name, origin) {
    agentState.results.set(name, null);
    agentState.key = "";
    ctx.paint();
    let outcome;
    try {
      outcome = await AW.tools.install(name, { origin });
    } catch (thrown) {
      agentState.results.set(name, { outcome: "failed", error: AW.asError(thrown) });
      agentState.key = "";
      ctx.paint();
      return;
    }
    if (!outcome) return;
    agentState.results.set(name, outcome);
    agentState.key = "";
    if (outcome.outcome === "installed") AW.form.set({ harness: name });
    await recheck(ctx);
  }

  async function recheck(ctx) {
    await AW.store.refresh("tools", { force: true }).catch(() => null);
    await Promise.all([ctx.reload(["clis"]), AW.store.refresh("config", { force: true }).catch(() => null)]);
    agentState.key = "";
    ctx.paint();
  }

  function resultLine(name, ctx) {
    const result = agentState.results.get(name);
    if (!result) return null;
    if (result.error) return AW.ui.errorNotice(result.error, { tool: name });
    const box = el("div", { class: "guide-result" }, el("p", { text: text(result.message) }));
    const actions = [];
    if (result.outcome === "opened-docs" || result.outcome === "started-terminal")
      actions.push(AW.ui.button("Recheck installations", { small: true, pending: "Checking…", onClick: () => recheck(ctx) }));
    if (result.outcome === "failed") actions.push(AW.ui.button("Open setup log", { small: true, onClick: () => AW.request("studio-setup-log") }));
    if (actions.length) box.append(el("div", { class: "actions" }, actions));
    return box;
  }

  function signInBody(ctx, name) {
    const body = el("div", { class: "guide-signin" });
    const sentence = name === "Codex" && text(AW.state.subscription) ? AW.state.subscription : name + " uses its own sign-in. It asks you to sign in if it needs to.";
    body.append(el("p", { text: sentence }));
    if (name === "OMP" || name === "Hermes") {
      const select = el("select", {}, SUBSCRIPTIONS.map((option) => el("option", { value: option.value, text: option.label })));
      select.value = AW.form.get().auth_provider || SUBSCRIPTIONS[0].value;
      select.addEventListener("change", () => AW.form.set({ auth_provider: select.value }));
      body.append(AW.ui.field("Subscription", select).el);
    }
    const line = el("p", { class: "guide-note-line", hidden: true, attrs: { role: "status" } });
    const open = AW.ui.button("Open " + name + " sign-in", {
      small: true,
      async onClick(event) {
        const form = AW.form.get();
        const payload = { harness: name };
        if ((name === "OMP" || name === "Hermes") && form.auth_provider) payload.auth_provider = form.auth_provider;
        await AW.jobs.run("account-login", payload, { title: "Opening " + name + " sign-in", origin: event.currentTarget });
        line.textContent = "Continue in the terminal that opened.";
        line.hidden = false;
      },
    });
    body.append(el("div", { class: "actions" }, open), line);
    return body;
  }

  function buildAgents(ctx) {
    const names = harnesses();
    if (names.join("|") === [...agentState.cards.keys()].join("|")) return;
    for (const task of agentState.tasks.values()) task.destroy();
    agentState.tasks.clear();
    agentState.cards.clear();
    agentState.list.replaceChildren();
    for (const name of names) {
      const status = el("span", { class: "guide-agent-status" });
      const body = el("div", { class: "guide-agent-body" });
      const card = ctx.card({ name: "guide-agent", value: name, title: name, description: LINES[name] || "", badge: status, body });
      card.el.classList.add("guide-agent");
      const flag = el("span", { class: "guide-flag", hidden: true }, AW.ui.mark("warn"), el("span", { class: "sr-only", text: ", needs attention" }));
      card.titleNode.append(flag);
      const extra = el("div", { class: "guide-agent-extra", id: card.input.id + "-extra" });
      card.el.append(extra);
      card.input.addEventListener("change", () => {
        if (!card.input.checked) return;
        AW.form.set({ harness: name });
        if (native() && name === "Codex" && AW.store.age("subscription") > 60000) AW.store.refresh("subscription").catch(() => {});
      });
      agentState.cards.set(name, { card, status, body, flag, extra });
      agentState.list.append(card.el);
    }
  }

  function paintAgents(ctx) {
    buildAgents(ctx);
    const form = AW.form.get();
    const signature = JSON.stringify([
      ctx.value("clis"),
      ctx.busy("clis"),
      AW.state.compat,
      form.harness,
      form.provider,
      form.auth_provider,
      native() && AW.state.subscription,
      AW.jobs.list().filter((record) => record.state === "running" && record.key && record.key.startsWith("tool:")).map((record) => record.handle),
      [...agentState.results.entries()],
    ]);
    if (signature === agentState.key) return;
    agentState.key = signature;
    const focused = agentState.list.contains(document.activeElement) ? document.activeElement : null;
    for (const task of agentState.tasks.values()) task.destroy();
    agentState.tasks.clear();
    for (const [name, part] of agentState.cards) {
      const { card, status, body, flag, extra } = part;
      const row = cliRow(ctx, name);
      const answer = verdict(name);
      const blocked = Boolean(answer && answer.ok === false);
      const unsure = Boolean(answer && answer.ok && text(answer.reason));
      card.input.checked = form.harness === name;
      flag.hidden = !(blocked || unsure);
      card.setDisabled(blocked ? text(answer.reason) || name + " does not work with " + ctx.sourceName() + "." : null);
      const job = AW.jobs.find("tool:" + name);
      status.replaceChildren();
      if (row.installed) status.append(el("span", { text: row.version && row.on_path !== false ? "Installed · " + row.version : "Installed" }));
      else {
        status.append(el("span", { text: "Not installed" }));
        if (!job) {
          const button = AW.ui.button("Install", { small: true, onClick: (event) => install(ctx, name, event.currentTarget) });
          button.setAttribute("aria-label", "Install " + name);
          status.append(button);
        }
      }
      extra.replaceChildren();
      if (blocked) extra.append(AW.ui.button("Change the model", { small: true, onClick: () => ctx.go("model") }));
      else if (unsure) extra.append(el("p", { class: "guide-warn-text", text: answer.reason }));
      if (row.installed && row.on_path === false && text(row.hint)) extra.append(el("p", { class: "guide-hint-text", text: row.hint }));
      if (job) {
        const task = AW.ui.taskRow(job.handle, { compact: true });
        agentState.tasks.set(name, task);
        extra.append(task.el);
      }
      const result = resultLine(name, ctx);
      if (result) extra.append(result);
      extra.hidden = !extra.childNodes.length;
      card.describedBy(extra.hidden ? [] : [extra.id]);
      const open = native() && form.harness === name && row.installed;
      card.setOpen(open);
      body.replaceChildren();
      if (open) body.append(signInBody(ctx, name));
    }
    const anyUsable = harnesses().some((name) => usable(ctx, name));
    const loaded = Boolean(ctx.section("clis")) && (native() || AW.state.compat !== null);
    agentState.none.hidden = !loaded || anyUsable;
    agentState.none.textContent = "No installed agent works with " + ctx.sourceName() + " yet. Install one of the agents without a warning mark, or go back and choose another place for the model.";
    if (focused && !agentState.list.contains(document.activeElement)) {
      const value = focused.type === "radio" ? focused.value : null;
      const again = value && agentState.cards.get(value);
      if (again) again.card.input.focus();
    }
  }

  function preselectAgent(ctx) {
    if (agentState.preselected) return;
    if (!ctx.section("clis") || (!native() && AW.state.compat === null)) return;
    agentState.preselected = true;
    const form = AW.form.get();
    if (usable(ctx, form.harness)) return;
    const saved = ctx.choices().harness;
    const next = usable(ctx, saved) ? saved : harnesses().find((name) => usable(ctx, name));
    if (next && next !== form.harness) AW.form.set({ harness: next });
  }

  steps.agent = Object.assign(steps.agent || {}, {
    title: () => "Choose an agent",
    lede(ctx) {
      const lines = ["The agent is the command-line program that reads your project and edits files. Workbench starts it in your project folder " + agentSentence(ctx)];
      const marked = !native() && harnesses().some((name) => {
        const answer = verdict(name);
        return answer && (answer.ok === false || text(answer.reason));
      });
      if (marked) lines.push("Each agent speaks one API, so some sources and agents do not pair up. Workbench marks those and says why.");
      return lines;
    },
    mount(host, ctx) {
      agentState.list = el("div", { class: "choices guide-agents", role: "radiogroup", attrs: { "aria-labelledby": "guide-title" } });
      agentState.none = el("p", { class: "notice warn", hidden: true });
      const again = AW.ui.button("Recheck installations", { small: true, pending: "Checking…", onClick: () => recheck(ctx) });
      host.append(agentState.none, agentState.list, el("div", { class: "actions" }, again));
    },
    show(ctx) {
      agentState.preselected = false;
      agentState.key = "";
      if (native() && AW.form.get().harness === "Codex") AW.store.refresh("subscription").catch(() => {});
      if (AW.state.compat === null && !native()) AW.store.refresh("compat").catch(() => {});
      preselectAgent(ctx);
    },
    paint(ctx) {
      preselectAgent(ctx);
      paintAgents(ctx);
    },
    focusMissing() {
      const form = AW.form.get();
      const part = agentState.cards.get(form.harness);
      const target = part ? part.card.input : agentState.list.querySelector('input[type="radio"]');
      if (target) target.focus();
      return Boolean(target);
    },
    footer(ctx) {
      const form = AW.form.get();
      if (!usable(ctx, form.harness) || !ctx.check("agent")) {
        const reason = native() ? "Choose an installed agent to continue." : "Choose an installed agent that works with " + ctx.sourceName() + ".";
        return { primary: { label: "Continue", reason } };
      }
      return { primary: { label: "Continue", run: () => ({ harness: form.harness }) } };
    },
  });

  // Step 4: the project folder and an optional starter kit.
  const project = { kits: null, detect: null, detectError: null, kit: null, applied: "", result: null, ticket: AW.ticket(), preview: null, previewKit: "", loading: false, folder: "" };

  function kitList() {
    if (!project.kits)
      project.kits = AW.request("kit-list").then(
        (rows) => (Array.isArray(rows) ? rows : []),
        (thrown) => {
          project.kits = null;
          throw thrown;
        },
      );
    return project.kits;
  }

  function chosenFolder(ctx) {
    const workspace = AW.form.get().workspace;
    return workspace && !ctx.scratchUnchosen(workspace) ? workspace : "";
  }

  function sentence(line) {
    const value = text(line).trim();
    if (!value) return "";
    const first = value.split(" ")[0];
    const head = /[./\\]/.test(first) ? value : value[0].toUpperCase() + value.slice(1);
    return /[.!?]$/.test(head) ? head : head + ".";
  }

  async function loadKit(ctx) {
    const workspace = chosenFolder(ctx);
    const mine = project.ticket.next();
    project.folder = workspace;
    project.detect = null;
    project.detectError = null;
    project.result = null;
    project.applied = "";
    project.kit = null;
    closePreview();
    if (!workspace) {
      ctx.paint();
      return;
    }
    project.loading = true;
    ctx.paint();
    try {
      const [state, kits] = await Promise.all([AW.request("onboarding-state", { workspace, sections: ["kit"] }), kitList()]);
      if (!project.ticket.current(mine)) return;
      const section = state && state.kit;
      if (section && section.ok) project.detect = section.value || {};
      else project.detectError = { message: text(section && section.error) || "Workbench could not look at this folder. Choose Check again." };
      project.rows = kits;
      const suggested = project.detect && text(project.detect.suggested);
      const saved = ctx.sameFolder(ctx.choices().workspace, workspace) ? text(ctx.choices().kit) : "";
      const known = (id) => kits.some((row) => row.id === id);
      project.kit = saved && known(saved) ? saved : suggested && suggested !== "blank" && known(suggested) ? suggested : "";
    } catch (thrown) {
      if (!project.ticket.current(mine)) return;
      project.detectError = AW.asError(thrown);
      project.rows = project.rows || [];
      project.kit = "";
    } finally {
      if (project.ticket.current(mine)) {
        project.loading = false;
        ctx.paint();
      }
    }
  }

  function closePreview() {
    if (project.preview && project.preview.destroy) project.preview.destroy();
    project.preview = null;
    project.previewKit = "";
    if (project.previewHost) project.previewHost.replaceChildren();
    if (project.details) project.details.open = false;
  }

  function previewOptions(ctx) {
    return { workspace: project.folder, harness: AW.form.get().harness, kit: project.kit, links: false, onPlan: () => ctx.paint() };
  }

  function openPreview(ctx) {
    if (!project.details.open || !project.kit) return;
    if (project.preview && project.previewKit === project.kit) return;
    if (project.preview && project.preview.update) {
      project.preview.update(previewOptions(ctx));
      project.previewKit = project.kit;
      return;
    }
    project.previewHost.replaceChildren();
    project.preview = AW.kitPreview.mount(project.previewHost, previewOptions(ctx));
    project.previewKit = project.kit;
  }

  function kitRow() {
    return list(project.rows).find((row) => row.id === project.kit) || null;
  }

  async function applyKit(ctx) {
    const kit = project.kit;
    const shown = project.preview && project.preview.plan ? project.preview.plan() : null;
    let plan = shown && shown.request && shown.request.kit === kit ? shown : null;
    if (!plan) {
      const request = { workspace: project.folder, kit, harness: AW.form.get().harness };
      plan = { ...(await AW.request("kit-preview", request)), request };
    }
    const result = await AW.kitPreview.review(plan);
    if (!result) return;
    project.applied = kit;
    project.result = { value: result, kept: list(plan.items).filter((item) => item && item.action === "skip").length };
    closePreview();
    for (const key of ["resolve", "consent", "folder", "kitHint"]) AW.store.refresh(key).catch(() => {});
    ctx.paint();
    if (project.resultHost) project.resultHost.focus();
  }

  function resultNodes() {
    const done = project.result;
    if (!done) return [];
    const value = done.value || {};
    const written = list(value.written).length;
    const nodes = [el("p", { text: "Wrote " + written + (written === 1 ? " file" : " files") + ". Kept " + done.kept + (done.kept === 1 ? " existing file." : " existing files.") })];
    const skipped = list(value.skipped).filter((row) => row && row.path);
    const failed = list(value.failed).filter((row) => row && row.path);
    if (skipped.length) nodes.push(el("p", { class: "group-label", text: "Skipped" }), el("ul", { class: "guide-files" }, skipped.map((row) => el("li", null, el("span", { class: "mono", text: AW.fmt.path(row.path) }), " · " + text(row.reason)))));
    if (failed.length) nodes.push(el("p", { class: "group-label", text: "Not written" }), el("ul", { class: "guide-files" }, failed.map((row) => el("li", null, el("span", { class: "mono", text: AW.fmt.path(row.path) }), " · " + text(row.reason)))));
    return nodes;
  }

  // The model and agent chosen in steps 2 and 3 stay chosen when the folder changes.
  const KEEP = ["provider", "connection_id", "remote_model", "auth_provider", "preset", "harness"];
  async function selectKeeping(path) {
    const before = AW.form.get();
    const folder = await AW.project.select(path);
    AW.form.set(Object.fromEntries(KEEP.map((key) => [key, before[key]])));
    return folder;
  }

  async function chooseFolder(ctx, path, { scratch = false, typed = false } = {}) {
    project.field.setError(null);
    const value = String(path || "").trim();
    if (!value) {
      project.field.setError("Choose a project folder to continue.");
      return;
    }
    if (typed) {
      const state = await AW.request("onboarding-state", { workspace: value, sections: [] });
      if (!state || !state.workspace || !state.workspace.exists) {
        project.field.setError("That folder does not exist. Choose another folder.");
        return;
      }
    }
    try {
      await selectKeeping(value);
    } catch (thrown) {
      project.field.setError(AW.asError(thrown).message);
      return;
    }
    ctx.chooseScratch(scratch);
  }

  let projectKey = "";
  function paintProject(ctx) {
    const form = AW.form.get();
    const folder = chosenFolder(ctx);
    const scratch = AW.project.scratch();
    if (folder !== project.folder) loadKit(ctx);
    const onScratch = Boolean(folder) && ctx.sameFolder(folder, scratch);
    if (document.activeElement !== project.input) {
      const shown = onScratch || ctx.scratchUnchosen(form.workspace) ? "" : AW.fmt.path(folder || form.workspace);
      if (project.input.value !== shown) project.input.value = shown;
    }
    project.scratchLine.hidden = !onScratch;
    project.scratchLine.textContent = onScratch ? "Scratch folder: " + AW.fmt.path(folder) : "";
    project.recent.hidden = !AW.project.known().filter((row) => !ctx.scratchUnchosen(row.path)).length;
    const kits = list(project.rows);
    const signature = JSON.stringify([folder, project.loading, project.detect, project.detectError && project.detectError.message, kits.map((row) => row.id), project.kit, project.applied, Boolean(project.result)]);
    if (signature === projectKey) return;
    projectKey = signature;
    project.panel.hidden = !folder;
    project.writeNote.hidden = !folder || Boolean(project.applied) || !project.kit;
    if (!folder) return;
    project.suggest.replaceChildren();
    if (project.loading) project.suggest.append(AW.ui.skeleton(1, "starter kit suggestion"));
    else if (project.detectError)
      project.suggest.append(
        el(
          "div",
          { class: "guide-fail" },
          el("span", { class: "guide-fail-text", text: project.detectError.message }),
          AW.ui.button("Check again", { small: true, pending: "Checking…", onClick: () => loadKit(ctx) }),
        ),
      );
    else {
      const found = project.detect || {};
      const suggested = text(found.suggested);
      const row = kits.find((item) => item.id === suggested);
      let line;
      if (suggested && suggested !== "blank" && row) {
        const evidence = list(found.evidence && found.evidence[suggested]).map(sentence).filter(Boolean);
        line = "Suggested: " + row.label + "." + (evidence.length ? " " + evidence.join(" ") : "");
      } else line = "No kit stood out for this folder. General software is a safe start, or continue without one.";
      if (found.truncated) line += " Only part of this large folder was scanned.";
      project.suggest.append(el("p", { text: line }));
    }
    const select = project.select;
    const options = [...kits.map((row) => el("option", { value: row.id, text: row.label || row.id })), el("option", { value: "", text: "No kit" })];
    select.replaceChildren(...options);
    select.value = project.kit || "";
    select.disabled = project.loading || Boolean(project.applied);
    const row = kitRow();
    const count = row ? (Number(row.agents_md_lines) > 0 ? 1 : 0) + list(row.skills).length + list(row.mcp).length : 0;
    project.summary.textContent = "Preview what it adds" + (count ? " (" + count + ")" : "");
    project.kitSummary.textContent = row ? text(row.summary) : "";
    project.kitSummary.hidden = !row || !text(row.summary);
    project.details.hidden = !row || Boolean(project.applied);
    project.resultHost.replaceChildren(...resultNodes());
    project.resultHost.hidden = !project.result;
    project.writeNote.hidden = Boolean(project.applied) || !project.kit;
  }

  steps.project = Object.assign(steps.project || {}, {
    title: () => "Choose a project folder",
    lede: () => "The agent works inside one folder, and Workbench remembers this setup for that folder. Pick a repository or any folder you want help with.",
    mount(host, ctx) {
      project.input = el("input", { type: "text", class: "mono", attrs: { spellcheck: "false", autocomplete: "off", placeholder: AW.platform.windows ? "C:\\Users\\you\\code\\project" : "~/code/project" } });
      project.input.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        chooseFolder(ctx, AW.fmt.expand(project.input.value), { typed: true }).catch((thrown) => project.field.setError(AW.asError(thrown).message));
      });
      project.input.addEventListener("input", () => project.field.setError(null));
      const browse = AW.ui.button("Browse…", {
        async onClick() {
          project.field.setError(null);
          try {
            const path = await AW.request("folder-pick");
            if (!path) return;
            await selectKeeping(path);
            ctx.chooseScratch(false);
          } catch (thrown) {
            project.field.setError(AW.asError(thrown).message);
          }
        },
      });
      project.field = AW.ui.field("Project folder", el("div", { class: "inputrow" }, project.input, browse), { hint: "Type a path and press Enter, or choose Browse." });
      const scratchButton = AW.ui.button("Use a scratch folder", { onClick: () => chooseFolder(ctx, AW.project.scratch(), { scratch: true }) });
      const scratchText = el("span", { class: "guide-scratch-text", id: "guide-scratch-text", text: "An empty folder inside Agent Workbench's own data, for trying things out." });
      scratchButton.setAttribute("aria-describedby", scratchText.id);
      project.scratchLine = el("p", { class: "guide-scratch-line mono", hidden: true });
      const recentList = el("div", { attrs: { "aria-labelledby": "guide-recent-title" } });
      project.recent = el("section", { class: "guide-recent", hidden: true }, el("h2", { class: "group-label", id: "guide-recent-title", text: "Recent" }), recentList);
      AW.projectSwitch.list(recentList, {
        filter: (row) => !ctx.scratchUnchosen(row.path),
        async onChoose(row) {
          project.field.setError(null);
          try {
            await selectKeeping(row.path);
            ctx.chooseScratch(false);
          } catch (thrown) {
            project.field.setError(AW.asError(thrown).message);
          }
        },
      });
      project.suggest = el("div", { class: "guide-kit-suggest" });
      project.select = el("select", { id: "guide-kit" });
      project.select.addEventListener("change", () => {
        project.kit = project.select.value;
        if (project.preview) {
          if (project.kit) {
            project.previewKit = "";
            openPreview(ctx);
          } else closePreview();
        }
        ctx.paint();
      });
      project.kitSummary = el("p", { class: "hint guide-kit-summary" });
      project.summary = el("summary", { text: "Preview what it adds" });
      project.previewHost = el("div", { class: "guide-kit-preview" });
      project.details = el("details", { class: "guide-more guide-kit-details" }, project.summary, project.previewHost);
      project.details.addEventListener("toggle", () => openPreview(ctx));
      project.resultHost = el("div", { class: "guide-result", tabindex: -1, hidden: true, attrs: { role: "status" } });
      const intro = "A starter kit adds an AGENTS.md instruction file, a few skills and a set of tools that suit the project. You see every file before it is written, and files you already have are kept.";
      project.panel = el(
        "section",
        { class: "panel guide-panel guide-kit", hidden: true, attrs: { "aria-labelledby": "guide-kit-title" } },
        el("h2", { class: "panel-title", id: "guide-kit-title", text: "Starter kit (optional)" }),
        el("p", { class: "guide-kit-intro", text: intro }),
        el("details", { class: "guide-more guide-kit-what" }, el("summary", { text: "What is a starter kit?" }), el("p", { text: intro })),
        project.suggest,
        el("div", { class: "guide-kit-choose" }, AW.ui.field("Kit", project.select).el, project.kitSummary),
        project.details,
        project.resultHost,
      );
      project.writeNote = el("p", { class: "guide-note-line", text: "Nothing is written until you choose Apply." });
      host.append(
        project.field.el,
        el("div", { class: "guide-scratch" }, scratchButton, scratchText),
        project.scratchLine,
        project.recent,
        project.panel,
        project.writeNote,
      );
    },
    show(ctx) {
      projectKey = "";
      AW.store.refresh("folders").catch(() => {});
      paintProject(ctx);
    },
    hide() {
      closePreview();
    },
    paint(ctx) {
      paintProject(ctx);
    },
    focusMissing() {
      project.input.focus();
      return true;
    },
    footer(ctx) {
      const folder = chosenFolder(ctx);
      if (!folder || !ctx.check("project")) return { primary: { label: "Continue without a kit", reason: "Choose a project folder to continue." } };
      const run = () => ({ workspace: folder, ...(project.applied ? { kit: project.applied } : {}) });
      const primary = { label: project.applied ? "Continue" : "Continue without a kit", run };
      const secondary = project.kit && !project.applied && !project.loading ? [{ label: "Apply starter kit…", pending: "Preparing…", run: () => applyKit(ctx) }] : [];
      return { primary, secondary };
    },
  });
})();
