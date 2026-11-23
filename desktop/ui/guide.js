// Setup guide: the frame, the step list, the open rule, the onboarding data and steps 1 (This computer) and 5 (Ready) (ui.md 4.0, 4.1, 4.5).
// Steps 2 to 4 live in guide-model.js and guide-steps.js and register themselves on AW.guideSteps as {title, lede, mount, show, paint, footer, focusMissing}.
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const ORDER = ["computer", "model", "agent", "project", "ready"];
  const NAMES = { computer: "This computer", model: "Model", agent: "Agent", project: "Project", ready: "Ready" };
  const FIRST = ["hardware", "engine", "clis", "endpoints", "managed"];
  const SECOND = ["recommendation", "library", "connections", "machines"];
  const SLOW_MS = 4000;
  const LOCAL = "Managed local";
  const NATIVE = "Native login";
  const NO_ANSWER = "This check did not answer. Choose Check again.";
  const steps = (AW.guideSteps = AW.guideSteps || {});

  const data = {};
  const busy = new Set();
  const failures = { first: null, second: null };
  const tickets = {};
  const listeners = new Set();
  let serial = 0;
  let slowTimer = null;
  let slow = false;

  let step = "computer";
  let inGuide = false;
  let returnTo = null;
  let restored = false;
  let notice = null;
  let queued = false;
  const frame = {};

  function envelope(section) {
    if (section && typeof section === "object" && section.ok === true) return section;
    if (section && typeof section === "object" && section.ok === false) return { ok: false, error: typeof section.error === "string" && section.error ? section.error : NO_ANSWER };
    return { ok: false, error: NO_ANSWER };
  }

  function armSlow() {
    if (slowTimer) return;
    slowTimer = setTimeout(() => {
      slowTimer = null;
      slow = busy.size > 0;
      schedule();
    }, SLOW_MS);
  }

  function fetchSections(names, { refresh = false, group = null } = {}) {
    const mine = ++serial;
    for (const name of names) {
      tickets[name] = mine;
      busy.add(name);
    }
    if (group) failures[group] = null;
    armSlow();
    schedule();
    const body = { sections: names };
    if (refresh) body.refresh = true;
    return AW.request("onboarding-state", body)
      .then(
        (value) => {
          if (value && value.progress) AW.store.set("progress", value.progress);
          for (const name of names) if (tickets[name] === mine) data[name] = envelope(value && value[name]);
          return true;
        },
        (thrown) => {
          const error = AW.asError(thrown);
          if (group) failures[group] = error;
          else for (const name of names) if (tickets[name] === mine) data[name] = { ok: false, error: error.message };
          return false;
        },
      )
      .finally(() => {
        for (const name of names) if (tickets[name] === mine) busy.delete(name);
        if (!busy.size) slow = false;
        schedule();
        for (const fn of [...listeners]) {
          try {
            fn();
          } catch (thrown) {
            console.error(thrown);
          }
        }
      });
  }

  function loadAll(refresh = false) {
    return Promise.all([fetchSections(FIRST, { refresh, group: "first" }), fetchSections(SECOND, { refresh, group: "second" })]);
  }

  const trim = (path) => String(path || "").replace(/[\\/]+$/, "");
  const sameFolder = (a, b) => trim(a) !== "" && trim(a) === trim(b);
  const progress = () => AW.state.progress || {};
  const choices = () => progress().choices || {};
  const firstRun = () => progress().open === true;
  const marked = (id) => Boolean((progress().steps || {})[id]);
  let scratchChosen = false;

  function scratchUnchosen(path) {
    const scratch = AW.project.scratch();
    return Boolean(scratch) && sameFolder(path, scratch) && !scratchChosen && !sameFolder(choices().workspace, scratch);
  }

  function check(id) {
    const ready = AW.readiness.current();
    const form = AW.form.get();
    const level = (slot) => (ready.slots && ready.slots[slot] ? ready.slots[slot].level : "ok");
    if (id === "computer") return marked("computer") || !firstRun();
    if (id === "model") return level("model") !== "block";
    if (id === "agent") return Boolean(form.harness) && level("agent") !== "block";
    if (id === "project") return Boolean(form.workspace) && level("project") !== "block" && !scratchUnchosen(form.workspace);
    return Boolean(ready.ok) && check("project");
  }
  const firstUnmet = () => ORDER.find((id) => !check(id)) || null;

  function sourceName() {
    const form = AW.form.get();
    const row = AW.form.connection();
    if (row && row.name) return row.name;
    if (form.provider === LOCAL) return "the local model";
    if (form.provider === NATIVE) return "its own account";
    const table = AW.state.sourceTable;
    const found = table && Array.isArray(table.providers) ? table.providers.find((item) => item.id === form.provider) : null;
    return (found && found.label) || form.provider || "this source";
  }

  // A click on plain text inside an open card's body must not move focus to the card's own radio; a row marked
  // data-pick selects its radio from anywhere on the row.
  const INTERACTIVE = "input, select, textarea, button, a, summary, label, [contenteditable], [tabindex]";
  function keepInside(event, body) {
    const hit = event.target.closest ? event.target.closest(INTERACTIVE) : null;
    if (hit && body.contains(hit)) return;
    event.preventDefault();
    const row = event.target.closest ? event.target.closest("[data-pick]") : null;
    const radio = row && body.contains(row) ? row.querySelector('input[type="radio"]') : null;
    if (radio && !radio.checked) {
      radio.checked = true;
      radio.dispatchEvent(new Event("change", { bubbles: true }));
    }
    if (radio) radio.focus();
  }

  function card({ name, value, title, description, badge, body }) {
    const handle = AW.ui.choice({ name, value, title, description, badge, body });
    const titleNode = handle.el.querySelector(".choice-title");
    const descNode = handle.el.querySelector(".choice-desc");
    const reasonNode = handle.el.querySelector(".choice-reason");
    titleNode.id = handle.input.id + "-title";
    if (descNode) descNode.id = handle.input.id + "-desc";
    handle.input.setAttribute("aria-labelledby", titleNode.id);
    const bodyNode = handle.el.querySelector(".choice-body");
    if (bodyNode) bodyNode.addEventListener("click", (event) => keepInside(event, bodyNode));
    let extra = [];
    let disabled = false;
    const describe = () => {
      const ids = [descNode && descNode.id, disabled ? reasonNode.id : null, ...extra].filter(Boolean);
      if (ids.length) handle.input.setAttribute("aria-describedby", ids.join(" "));
      else handle.input.removeAttribute("aria-describedby");
    };
    describe();
    const setDisabled = handle.setDisabled;
    return {
      ...handle,
      titleNode,
      descNode,
      setDisabled(text) {
        setDisabled(text);
        disabled = Boolean(text);
        describe();
      },
      describedBy(ids) {
        extra = (ids || []).filter(Boolean);
        describe();
      },
    };
  }

  function sectionError(name, label) {
    const envelopeOf = data[name];
    return el(
      "div",
      { class: "guide-fail" },
      el("span", { class: "guide-fail-text", text: (envelopeOf && envelopeOf.error) || NO_ANSWER }),
      AW.ui.button("Check again", { small: true, pending: "Checking…", title: "Check " + label + " again", onClick: () => fetchSections([name], { refresh: true }) }),
    );
  }

  const ctx = {
    names: NAMES,
    order: ORDER,
    section: (name) => data[name] || null,
    value: (name) => (data[name] && data[name].ok ? data[name].value : null),
    busy: (name) => busy.has(name),
    failure: (group) => failures[group],
    reload: (names, options) => fetchSections(names, options || {}),
    put(name, value) {
      tickets[name] = ++serial;
      busy.delete(name);
      data[name] = { ok: true, value };
      schedule();
    },
    loadAll,
    on(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    paint: () => schedule(),
    go: (id) => go(id),
    check,
    choices,
    firstRun,
    sourceName,
    card,
    sectionError,
    sameFolder,
    scratchUnchosen,
    chooseScratch(on) {
      scratchChosen = Boolean(on);
      schedule();
    },
    setNotice(kind, lines) {
      const list = (lines || []).filter((line) => typeof line === "string" && line);
      notice = list.length ? { kind, lines: list } : null;
    },
    current: () => step,
  };
  AW.guide = { open, ctx };

  function open(options = {}) {
    const params = options && options.step && ORDER.includes(options.step) ? { step: options.step } : {};
    return AW.router.go("guide", params).then(() => undefined);
  }

  function go(id) {
    if (!ORDER.includes(id)) return Promise.resolve(false);
    return AW.router.go("guide", { step: id });
  }
  const after = (id) => ORDER[Math.min(ORDER.length - 1, ORDER.indexOf(id) + 1)];
  const before = (id) => ORDER[Math.max(0, ORDER.indexOf(id) - 1)];

  function schedule() {
    if (queued) return;
    queued = true;
    Promise.resolve().then(() => {
      queued = false;
      paint();
    });
  }

  function paint() {
    if (!inGuide || !frame.root) return;
    paintList();
    paintExit();
    paintHead();
    const current = steps[step];
    if (current && current.paint) {
      try {
        current.paint(ctx);
      } catch (thrown) {
        console.error(thrown);
      }
    }
    paintFoot();
  }

  // Step list in the rail.
  const items = new Map();
  function buildList() {
    const list = AW.$("guide-steps");
    list.replaceChildren();
    if (!list.previousElementSibling) list.before(el("p", { class: "group-label guide-rail-title", attrs: { "aria-hidden": "true" }, text: "Setup guide" }));
    ORDER.forEach((id, index) => {
      const mark = AW.ui.mark("idle");
      const word = el("span", { class: "sr-only" });
      const reason = el("span", { class: "guide-step-reason", id: "guide-step-reason-" + id });
      const button = el(
        "button",
        { class: "guide-step", type: "button", data: { step: id } },
        mark,
        el("span", { class: "guide-step-num", attrs: { "aria-hidden": "true" }, text: String(index + 1) }),
        el("span", { class: "guide-step-name" }, el("span", { class: "sr-only", text: "Step " + (index + 1) + ": " }), NAMES[id], word),
        reason,
      );
      button.addEventListener("click", () => {
        if (button.getAttribute("aria-disabled") === "true") {
          button.dataset.told = "true";
          AW.ui.announce(reason.textContent);
          return;
        }
        if (id !== step) go(id);
      });
      list.append(el("li", null, button));
      items.set(id, { button, mark, word, reason });
    });
  }

  function paintList() {
    const blocked = firstUnmet();
    const limit = blocked ? ORDER.indexOf(blocked) : ORDER.length;
    ORDER.forEach((id, index) => {
      const item = items.get(id);
      if (!item) return;
      const here = id === step;
      const locked = !here && index > limit;
      const passes = !locked && check(id);
      const state = passes ? "done" : marked(id) && !locked ? "attention" : "todo";
      AW.ui.setMark(item.mark, state === "done" ? "ok" : state === "attention" ? "warn" : "idle");
      item.word.textContent = state === "done" ? ", done" : state === "attention" ? ", needs attention" : "";
      if (here) item.button.setAttribute("aria-current", "step");
      else item.button.removeAttribute("aria-current");
      const why = locked ? "Finish " + NAMES[blocked] + " first." : "";
      if (item.reason.textContent !== why) item.reason.textContent = why;
      if (!locked) delete item.button.dataset.told;
      if (locked) {
        item.button.setAttribute("aria-disabled", "true");
        item.button.setAttribute("aria-describedby", item.reason.id);
      } else {
        item.button.removeAttribute("aria-disabled");
        item.button.removeAttribute("aria-describedby");
      }
      item.button.title = NAMES[id] + (why ? ". " + why : "");
      item.button.dataset.state = state;
    });
  }

  // The one exit: Skip setup on a first run, Close guide when reopened.
  let exitMode = "";
  function exitParts() {
    const first = firstRun();
    const label = first ? "Skip setup" : "Close guide";
    const line = first ? "Launch will show what is still missing." : "Your choices are kept.";
    return { first, label, line };
  }
  function exitButton(where) {
    const parts = exitParts();
    const lineId = "guide-exit-line-" + where;
    const button = AW.ui.button(parts.label, { small: true, kind: where === "rail" ? "quiet" : undefined, onClick: () => (parts.first ? skip() : close()) });
    button.setAttribute("aria-describedby", lineId);
    return el("div", { class: "guide-exit guide-exit-" + where }, button, el("p", { class: "guide-exit-line", id: lineId, text: parts.line }));
  }
  function paintExit() {
    const mode = exitParts().label;
    if (mode === exitMode) return;
    exitMode = mode;
    AW.$("guide-exit").replaceChildren(exitButton("rail"));
    frame.exit.replaceChildren(exitButton("foot"));
  }

  async function skip() {
    const value = await AW.request("onboarding-finish", { skipped: true });
    AW.store.set("progress", value);
    await AW.router.go("launch");
    AW.ui.toast("Setup skipped. Open it again any time from Setup guide in the sidebar.");
  }

  function close() {
    const target = returnTo && returnTo.id !== "guide" ? returnTo : { id: "launch", params: {} };
    return AW.router.go(target.id, target.params);
  }

  function paintHead() {
    const current = steps[step] || {};
    const title = current.title ? current.title(ctx) : NAMES[step];
    if (frame.title.textContent !== title) frame.title.textContent = title;
    const lede = current.lede ? current.lede(ctx) : "";
    const text = Array.isArray(lede) ? lede.filter(Boolean).join(" ") : lede || "";
    if (frame.lede.textContent !== text) frame.lede.textContent = text;
    frame.lede.hidden = !text;
    frame.slow.hidden = !slow;
    AW.header.step(NAMES[step]);
  }

  // Footer: Back, the step count, secondary actions and the primary button.
  let secondaryKey = "";
  let secondarySpecs = [];
  function paintFoot() {
    const current = steps[step] || {};
    const spec = (current.footer && current.footer(ctx)) || {};
    const index = ORDER.indexOf(step);
    frame.back.hidden = index === 0;
    frame.count.textContent = "Step " + (index + 1) + " of " + ORDER.length;
    const primary = spec.primary || null;
    frame.primary.hidden = !primary;
    if (primary) {
      const label = frame.primary.querySelector(".btn-label");
      if (label && !frame.primary.dataset.pending && label.textContent !== primary.label) label.textContent = primary.label;
      if (!frame.primary.dataset.pending && !frame.primary.dataset.job) {
        if (primary.reason) frame.primary.setAttribute("aria-disabled", "true");
        else frame.primary.removeAttribute("aria-disabled");
      }
    }
    const reason = (primary && primary.reason) || "";
    if (frame.reason.textContent !== reason) frame.reason.textContent = reason;
    frame.reason.hidden = !reason;
    secondarySpecs = spec.secondary || [];
    const key = secondarySpecs.map((item) => item.label).join("\n");
    if (key !== secondaryKey) {
      secondaryKey = key;
      frame.secondary.replaceChildren(
        ...secondarySpecs.map((item) =>
          AW.ui.button(item.label, {
            pending: item.pending,
            errorHost: frame.error,
            onClick(event) {
              const latest = secondarySpecs.find((other) => other.label === item.label);
              AW.ui.clearError(frame.error);
              return latest ? latest.run(event.currentTarget) : undefined;
            },
          }),
        ),
      );
    }
  }

  function cleanChoices(raw) {
    const out = {};
    for (const [key, value] of Object.entries(raw || {})) if (typeof value === "string" && value && value.length <= 1024) out[key] = value;
    return out;
  }

  async function runPrimary() {
    const button = frame.primary;
    if (button.dataset.pending || button.dataset.job) return;
    const current = steps[step] || {};
    const spec = current.footer && current.footer(ctx) && current.footer(ctx).primary;
    if (!spec) return;
    if (spec.reason) {
      if (!(current.focusMissing && current.focusMissing(ctx))) AW.focus.first(frame.hosts.get(step));
      return;
    }
    const id = step;
    AW.ui.clearError(frame.error);
    const work = Promise.resolve().then(() => spec.run(button));
    AW.ui.pending(button, spec.pending || null, work);
    let result;
    try {
      result = await work;
    } catch (thrown) {
      AW.ui.showError(frame.error, thrown, { retry: runPrimary });
      return;
    }
    if (!result || step !== id) return;
    try {
      const value = await AW.request("onboarding-mark", { step: id, choices: cleanChoices(result) });
      AW.store.set("progress", value);
    } catch (thrown) {
      AW.ui.showError(frame.error, thrown, { retry: runPrimary });
      return;
    }
    await go(after(id));
  }

  function paintNotice() {
    frame.top.replaceChildren();
    if (!notice) return;
    const body = notice.lines.length === 1 ? notice.lines[0] : el("ul", { class: "guide-notice-list" }, notice.lines.map((line) => el("li", { text: line })));
    frame.top.append(AW.ui.notice(notice.kind, body, { dismissible: true, onDismiss: () => (notice = null) }));
  }

  function mountFrame(root) {
    root.classList.add("guide");
    frame.root = root;
    frame.title = el("h1", { class: "title", id: "guide-title", tabindex: -1, text: NAMES.computer });
    frame.lede = el("p", { class: "lede guide-lede", id: "guide-lede" });
    frame.slow = el("p", { class: "guide-slow", hidden: true }, AW.ui.mark("busy"), "Still checking. The first look at this computer can take a few seconds.");
    frame.top = el("div", { class: "guide-top" });
    frame.body = el("div", { class: "guide-body" });
    frame.error = el("div", { class: "guide-error" });
    frame.hosts = new Map();
    for (const id of ORDER) {
      const host = el("div", { class: "guide-step-body", data: { step: id }, hidden: true });
      frame.hosts.set(id, host);
      frame.body.append(host);
    }
    frame.back = AW.ui.button("Back", { onClick: () => go(before(step)) });
    frame.reason = el("p", { class: "guide-reason", id: "guide-reason", attrs: { "aria-live": "polite" }, hidden: true });
    frame.count = el("span", { class: "guide-count" });
    frame.secondary = el("div", { class: "guide-secondary" });
    frame.primary = el("button", { class: "btn primary", type: "button", id: "guide-next", attrs: { "aria-describedby": "guide-reason" } }, el("span", { class: "btn-label", text: "Continue" }));
    frame.primary.addEventListener("click", runPrimary);
    frame.exit = el("div", { class: "guide-foot-exit" });
    frame.foot = el(
      "footer",
      { class: "guide-foot" },
      frame.exit,
      el("div", { class: "guide-foot-back" }, frame.back),
      frame.reason,
      el("div", { class: "guide-foot-end" }, frame.count, frame.secondary, frame.primary),
    );
    root.append(frame.title, frame.lede, frame.slow, frame.top, frame.body, frame.error, frame.foot);
    buildList();
    AW.store.on(["config", "accounts", "scan", "compat", "folder", "folders", "progress", "resolve", "consent", "engine", "recommend", "tools", "sourceTable", "live", "match", "kitHint"], schedule);
    AW.form.on(schedule);
    AW.readiness.on(schedule);
    AW.jobs.on(schedule);
  }

  function showStep(id) {
    const leaving = step;
    if (leaving !== id) {
      const old = steps[leaving];
      if (old && old.hide) old.hide(ctx);
      if (frame.hosts.get(leaving)) frame.hosts.get(leaving).hidden = true;
      AW.ui.clearError(frame.error);
      paintNotice();
      notice = null;
    }
    step = id;
    const host = frame.hosts.get(id);
    host.hidden = false;
    const current = steps[id];
    if (current && !current.mounted) {
      current.mounted = true;
      if (current.mount) current.mount(host, ctx);
    }
    if (current && current.show) current.show(ctx);
    paint();
  }

  function restore() {
    if (restored || !firstRun()) return;
    restored = true;
    const saved = choices();
    const folder = AW.state.folder;
    if (folder && folder.known) return;
    const accounts = AW.state.accounts || [];
    const patch = {};
    if (saved.connection_id && accounts.some((row) => row.connection_id === saved.connection_id) && !AW.form.get().connection_id) patch.connection_id = saved.connection_id;
    if (saved.harness && (AW.state.config && (AW.state.config.agents || []).includes(saved.harness))) patch.harness = saved.harness;
    const apply = () => Object.keys(patch).length && AW.form.set(patch);
    const here = AW.form.get().workspace;
    if (saved.workspace && !sameFolder(saved.workspace, here) && (!here || sameFolder(here, AW.project.scratch())))
      AW.project.select(saved.workspace).then(apply, apply);
    else apply();
  }

  // Step 1: This computer.
  function factsOf() {
    const rows = [];
    const first = failures.first;
    const pending = (name) => busy.has(name) && !data[name];
    const add = (key, label, section, render, quiet = false) => {
      if (first && FIRST.includes(section) && !data[section]) return;
      if (quiet && (!data[section] || pending(section))) return;
      if (pending(section)) rows.push({ key, label, nodes: [AW.ui.skeleton(1, label)] });
      else if (!data[section] && SECOND.includes(section) && failures.second) {
        if (key === "memory")
          rows.push({
            key,
            label,
            nodes: [
              el(
                "div",
                { class: "guide-fail" },
                el("span", { class: "guide-fail-text", text: failures.second.message }),
                AW.ui.button("Check again", { small: true, pending: "Checking…", onClick: () => fetchSections(SECOND, { refresh: true, group: "second" }) }),
              ),
            ],
          });
      } else if (!data[section]) rows.push({ key, label, nodes: [AW.ui.skeleton(1, label)] });
      else if (!data[section].ok) rows.push({ key, label, nodes: [sectionError(section, label)] });
      else {
        const nodes = render(data[section].value);
        if (nodes) rows.push({ key, label, nodes });
      }
    };
    const line = (mark, text) => el("span", { class: "guide-line" }, mark ? AW.ui.mark(mark) : null, text);
    add("hardware", "Hardware", "hardware", (hw) => {
      const gpus = Array.isArray(hw && hw.gpus) ? hw.gpus : [];
      if (gpus.length && hw.label) return [line(null, hw.label)];
      const cpu = (hw && hw.cpu) || "This computer";
      return [line(null, cpu + " · " + AW.fmt.gib(hw && hw.ram_gib) + " memory. No supported GPU was found.")];
    });
    add("memory", "Memory for a model", "recommendation", (rec) => {
      const budget = rec && rec.budget;
      if (!budget || !(Number(budget.total_gib) > 0)) return null;
      const nodes = [line(null, Number(budget.usable_gib || 0).toFixed(1) + " of " + Number(budget.total_gib).toFixed(1) + " GiB can be used by a local model.")];
      if (budget.rule) nodes.push(el("details", { class: "guide-more" }, el("summary", { text: "How this is set" }), el("p", { text: String(budget.rule) })));
      return nodes;
    });
    add("servers", "Model servers running", "endpoints", (found) => {
      const endpoints = Array.isArray(found && found.endpoints) ? found.endpoints : [];
      const nodes = endpoints.map((row) => {
        const count = Array.isArray(row.models) ? row.models.length : 0;
        return line("ok", (row.label || "A model server") + " on port " + row.port + " · " + count + (count === 1 ? " model" : " models"));
      });
      if (!nodes.length) nodes.push(line(null, "None. If you use Ollama or LM Studio, start it and choose Check again."));
      for (const hint of Array.isArray(found && found.hints) ? found.hints : []) if (hint && hint.text) nodes.push(el("span", { class: "guide-line muted", text: String(hint.text) }));
      return nodes;
    });
    add("agents", "Agents installed", "clis", (rows) => {
      const installed = (Array.isArray(rows) ? rows : []).filter((row) => row && row.installed);
      if (!installed.length) return [line(null, "None yet. You install one in step 3.")];
      return installed.map((row) => line("ok", row.name + (row.version ? " " + row.version : "")));
    });
    add("engine", "Engine for local models", "engine", (engine) => {
      if (engine && engine.supported === false) return [line(null, String(engine.reason || "The engine for local models cannot run on this computer."))];
      if (engine && engine.engine_ready) return [line("ok", "Installed.")];
      const size = Number(engine && engine.download_bytes) > 0 ? " (" + AW.fmt.bytes(engine.download_bytes) + ")" : "";
      return [line(null, "Not installed yet" + size + ". Only needed if you run a model on this computer.")];
    });
    add("library", "Model files on this computer", "library", (library) => {
      const count = Number(library && library.count) || 0;
      if (!count) return null;
      return [el("span", { class: "guide-line guide-path", title: AW.fmt.path(library.folder), text: count + " in " + AW.fmt.path(library.folder) })];
    }, true);
    add("connections", "Saved connections", "connections", (rows) => {
      const names = (Array.isArray(rows) ? rows : []).map((row) => row && (row.name || row.provider)).filter(Boolean);
      if (!names.length) return null;
      return [line(null, names.join(", "))];
    }, true);
    return rows;
  }

  let factsKey = "";
  const computer = {
    title: () => "This computer",
    lede: () =>
      "Agent Workbench starts a coding agent in a project folder and connects it to a model: one on this computer, on another machine of yours, or a hosted service. It remembers that choice for the folder. Here is what it found.",
    mount(host) {
      this.notice = el("div", { class: "guide-computer-notice" });
      this.table = el("dl", { class: "guide-facts" });
      this.panel = el("section", { class: "panel guide-panel", attrs: { "aria-label": "What Agent Workbench found" } }, this.table);
      this.notes = el("div", { class: "guide-notes" });
      this.again = AW.ui.button("Check again", { pending: "Checking…", onClick: () => loadAll(true) });
      host.append(
        this.notice,
        this.panel,
        this.notes,
        el("div", { class: "actions guide-again" }, this.again),
        el("p", { class: "guide-closing" }, "Next you choose three things: where the model runs, an agent, and a project folder. Nothing is downloaded, installed or written until you choose it."),
        el("p", { class: "guide-closing" }, "Agent Workbench has no account, sends no usage data and does not check for updates."),
      );
    },
    paint() {
      const rows = factsOf();
      const hw = data.hardware && data.hardware.ok ? data.hardware.value : null;
      const lines = hw ? [...(Array.isArray(hw.notes) ? hw.notes : []), ...(Array.isArray(hw.unknown) ? hw.unknown : [])].filter((text) => typeof text === "string" && text) : [];
      const key = JSON.stringify([rows.map((row) => [row.key, row.nodes.map((node) => node.textContent)]), lines, failures.first && failures.first.message]);
      if (key === factsKey) return;
      factsKey = key;
      const focused = this.panel.contains(document.activeElement) || this.notice.contains(document.activeElement);
      this.table.replaceChildren(...rows.flatMap((row) => [el("dt", { text: row.label }), el("dd", null, row.nodes)]));
      this.notes.replaceChildren(...lines.map((text) => el("p", { class: "guide-note" }, AW.ui.mark("warn"), el("span", { class: "sr-only", text: "Note: " }), text)));
      this.notice.replaceChildren();
      const error = failures.first;
      if (error) {
        const node =
          error.code === "bridge-failed"
            ? AW.ui.errorNotice(error, { retry: () => loadAll(true) })
            : AW.ui.notice("danger", "Agent Workbench could not check this computer. " + error.message, {
                actions: [
                  { label: "Try again", primary: true, pending: "Checking…", run: () => loadAll(true) },
                  ...(firstRun() ? [{ label: "Skip setup", run: skip }] : []),
                ],
              });
        this.notice.append(node);
      }
      if (focused && !this.panel.contains(document.activeElement) && !this.notice.contains(document.activeElement)) this.again.focus();
    },
    footer: () => ({ primary: { label: "Continue", run: () => ({}) } }),
  };

  // Step 5: Ready.
  let readyKey = "";
  const ready = {
    title: () => (AW.readiness.current().ok ? "Ready" : "Almost ready"),
    lede() {
      const workspace = AW.form.get().workspace;
      const scratch = workspace && sameFolder(workspace, AW.project.scratch());
      const folder = scratch ? "the scratch folder" : AW.project.name(workspace);
      return folder ? "This is the setup for " + folder + ". Start the agent now, or finish and start it later from Launch." : "Start the agent now, or finish and start it later from Launch.";
    },
    mount(host) {
      const panel = el("section", { class: "panel guide-panel guide-ready", attrs: { "aria-label": "Your setup" } });
      this.list = AW.ui.readinessList(panel);
      const target = { project: "project", model: "model", agent: "agent", setup: "project" };
      const words = { project: "project folder", model: "model", agent: "agent", setup: "instructions and tools" };
      for (const row of panel.querySelectorAll(".ready-row[data-slot]")) {
        const slot = row.dataset.slot;
        const change = AW.ui.button("Change", { small: true, onClick: () => go(target[slot]) });
        change.classList.add("guide-change");
        change.setAttribute("aria-label", "Change the " + words[slot]);
        row.append(change);
      }
      const name = "guide-where";
      const option = (value, text) => {
        const input = el("input", { type: "radio", value, attrs: { name } });
        input.addEventListener("change", () => input.checked && AW.form.where(value));
        return { input, node: el("label", { class: "guide-where-option" }, input, el("span", { text })) };
      };
      this.dock = option("dock", "Terminal dock, inside this window");
      this.window = option("window", "New terminal window");
      host.append(
        panel,
        el("fieldset", { class: "guide-where" }, el("legend", { text: "Open in" }), this.dock.node, this.window.node),
        el("p", { class: "guide-closing", text: "You can change any of this on Launch. The guide stays in the sidebar under Setup guide." }),
      );
    },
    paint() {
      const result = AW.readiness.current();
      const key = JSON.stringify(result);
      if (key !== readyKey) {
        readyKey = key;
        this.list.update(result);
      }
      const where = AW.form.where();
      this.dock.input.checked = where === "dock";
      this.window.input.checked = where === "window";
    },
    focusMissing() {
      return this.list.focusFirstBlocker();
    },
    footer() {
      const result = AW.readiness.current();
      const agent = AW.form.get().harness || "the agent";
      return {
        primary: { label: "Start " + agent, reason: result.ok ? "" : result.needs, pending: "Starting…", run: (origin) => start(origin) },
        secondary: [{ label: "Finish without starting", run: () => finish(false) }],
      };
    },
  };

  async function finish(started) {
    const value = await AW.request("onboarding-finish", {});
    AW.store.set("progress", value);
    await AW.router.go("launch");
    return started;
  }

  async function start(origin) {
    const ok = await AW.launch.start({ where: AW.form.where(), origin });
    if (ok) {
      await finish(true);
      return null;
    }
    const run = AW.launch.run ? AW.launch.run() : null;
    if (run && run.error && AW.router.current().id === "guide") AW.ui.showError(frame.error, run.error, { retry: () => start(origin) });
    return null;
  }

  steps.computer = Object.assign(steps.computer || {}, computer);
  steps.ready = Object.assign(steps.ready || {}, ready);

  AW.router.register("guide", {
    title: "Setup guide",
    takeover: true,
    mount: mountFrame,
    show(params) {
      const fresh = !inGuide;
      inGuide = true;
      if (fresh) {
        exitMode = "";
        restore();
        loadAll(false);
        if (!AW.state.sourceTable) AW.store.refresh("sourceTable").catch(() => {});
      }
      const asked = params && ORDER.includes(params.step) ? params.step : null;
      showStep(asked || firstUnmet() || "ready");
    },
    hide() {
      inGuide = false;
      const current = steps[step];
      if (current && current.hide) current.hide(ctx);
    },
  });

  AW.router.on(({ id, params }) => {
    if (id !== "guide") returnTo = { id, params };
  });
})();
