// Setup guide, step 2: where the model runs (ui.md 4.2). Six options in one radio group; the open card holds its own controls.
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const LOCAL = "Managed local";
  const NATIVE = "Native login";
  const ATTACHED = "Attached endpoint";
  const NEED_CHOICE = "Choose where the model runs to continue.";
  const OPTIONS = [
    { id: "running", title: "Use a server that is already running", description: "Attach Ollama, LM Studio or another model server on this computer. Nothing is downloaded." },
    { id: "download", title: "Download a model for this computer", description: "Runs here, offline once downloaded." },
    { id: "hosted", title: "Use a hosted API", description: "Bring an API key for OpenRouter, OpenAI, Anthropic or another provider. Your requests go to that provider." },
    { id: "ssh", title: "Use a machine over SSH", description: "Reach a model server on another computer you can already sign in to with ssh. Model traffic travels inside the SSH connection." },
    { id: "address", title: "Type a server address", description: "For a model server on this computer that the scan did not find." },
    { id: "account", title: "Use an agent's own sign-in", description: "For Claude Code, Codex or another agent that is already signed in to its own account. Workbench does not see or store that login." },
  ];

  const list = (value) => (Array.isArray(value) ? value : []);
  const text = (value) => (typeof value === "string" ? value : "");
  const base = (path) => text(path).split(/[\\/]+/).filter(Boolean).pop() || "";
  let serial = 0;
  const uid = (prefix) => "guide-" + prefix + "-" + ++serial;

  const state = {
    option: null,
    picked: false,
    server: null,
    models: new Map(),
    keys: new Map(),
    local: null,
    found: null,
    sshHost: "",
    sshAttached: null,
    hostedRow: null,
    forms: {},
    installs: new Map(),
  };
  let ctx = null;
  const cards = new Map();
  const bodies = new Map();

  // Step 2 data.
  const endpointsOf = () => list(ctx.value("endpoints") && ctx.value("endpoints").endpoints);
  const hintsOf = () => list(ctx.value("endpoints") && ctx.value("endpoints").hints).filter((hint) => hint && text(hint.text));
  const managed = () => ctx.value("managed") || {};
  const recommendation = () => ctx.value("recommendation") || {};
  const engine = () => ctx.value("engine") || null;
  const library = () => ctx.value("library") || {};
  const settled = (name) => Boolean(ctx.section(name)) || Boolean(ctx.failure(["hardware", "engine", "clis", "endpoints", "managed"].includes(name) ? "first" : "second"));
  const isMac = () => {
    const hw = ctx.value("hardware");
    return Boolean(hw && hw.os === "macos");
  };

  function catalogEntries() {
    const rec = recommendation();
    return [rec.pick, ...list(rec.alternatives)].filter((entry) => entry && entry.id);
  }
  const installed = (entry) => Boolean(entry && entry.installed && entry.installed.installed);

  function localFile() {
    const config = AW.state.config || {};
    const form = AW.form.get();
    const preset = form.preset && config.presets ? config.presets[form.preset] : null;
    return base((preset && preset.model) || (config.current && config.current.model));
  }

  function preselect() {
    const form = AW.form.get();
    const row = AW.form.connection();
    if (form.provider === NATIVE) return "account";
    if (row && row.provider === ATTACHED) {
      if (row.host) return "ssh";
      if (!settled("endpoints")) return null;
      return endpointsOf().some((item) => item.connection_id === row.connection_id || item.base === row.endpoint) ? "running" : "address";
    }
    if (row && row.provider !== LOCAL && row.provider !== NATIVE) return "hosted";
    if (form.provider === LOCAL && (localFile() || runningInstall().length || ctx.choices().source === "local")) return "download";
    if (!settled("endpoints") || !settled("managed")) return null;
    if (endpointsOf().length || managed().running) return "running";
    if (!settled("recommendation")) return null;
    if (recommendation().pick) return "download";
    return "hosted";
  }

  function choose(option, picked) {
    state.option = option;
    if (picked) state.picked = true;
    for (const [id, card] of cards) {
      card.input.checked = id === option;
      card.setOpen(id === option);
    }
    if (option) build(option);
    ctx.paint();
    if (picked && option) requestAnimationFrame(() => reveal(cards.get(option).el));
  }

  // Scrolls an opened card up until its end clears the sticky footer, never past its own top.
  function reveal(node) {
    const view = node.closest("#view");
    const foot = view ? view.querySelector(".guide-foot") : null;
    if (!foot) return;
    const box = node.getBoundingClientRect();
    const over = box.bottom - foot.getBoundingClientRect().top + 8;
    const room = box.top - view.getBoundingClientRect().top - 16;
    if (over > 0 && room > 0) view.scrollBy({ top: Math.min(over, room) });
  }

  // Option bodies are built the first time their card opens.
  function build(option) {
    const host = bodies.get(option);
    if (!host || host.dataset.built) return;
    host.dataset.built = "true";
    const watch = () => ctx.paint();
    if (option === "running") {
      state.serverList = el("div", { class: "guide-picks", role: "radiogroup", attrs: { "aria-label": "Model servers on this computer" } });
      state.serverHints = el("div", { class: "guide-hints" });
      state.serverEmpty = el("p", { class: "guide-empty", hidden: true, text: "No model server answered on this computer. Start Ollama, or turn on the local server in LM Studio, then scan again." });
      const scan = AW.ui.button("Scan again", { small: true, pending: "Scanning…", onClick: rescan });
      host.append(state.serverEmpty, state.serverList, state.serverHints, el("div", { class: "actions" }, scan));
    } else if (option === "download") {
      state.localHost = el("div", { class: "guide-local" });
      host.append(state.localHost);
    } else if (option === "hosted") {
      const root = el("div", { class: "guide-form" });
      host.append(root);
      const row = AW.form.connection();
      const current = row && ![LOCAL, NATIVE, ATTACHED].includes(row.provider) ? row.connection_id : undefined;
      state.forms.hosted = AW.sourceForms.hosted(root, {
        connection_id: current,
        inline: false,
        onSaved(saved) {
          state.hostedRow = saved || null;
          ctx.paint();
        },
      });
      root.addEventListener("input", watch);
      root.addEventListener("change", watch);
    } else if (option === "ssh") {
      state.sshRoot = el("div", { class: "guide-form" });
      host.append(state.sshRoot);
      mountSsh();
      state.sshRoot.addEventListener("input", watch);
      state.sshRoot.addEventListener("change", watch);
    } else if (option === "address") {
      const root = el("div", { class: "guide-form" });
      state.foundHost = el("div", { class: "guide-found" });
      host.append(root, state.foundHost);
      state.forms.address = AW.sourceForms.address(root, {
        onFound(endpoint, typed) {
          state.found = endpoint && typeof endpoint === "object" ? endpoint : null;
          if (state.found && state.found.base) {
            const key = typed && typeof typed === "object" ? text(typed.key).trim() : "";
            if (key) state.keys.set(state.found.base, key);
            else state.keys.delete(state.found.base);
          }
          state.foundKey = "";
          ctx.paint();
        },
        onRemote(target) {
          state.found = null;
          state.foundKey = "";
          state.sshHost = text(target);
          if (state.forms.ssh) {
            if (state.forms.ssh.destroy) state.forms.ssh.destroy();
            state.sshRoot.replaceChildren();
            mountSsh();
          }
          choose("ssh", true);
          cards.get("ssh").input.focus();
        },
      });
      root.addEventListener("input", watch);
      root.addEventListener("change", watch);
    }
  }

  function mountSsh() {
    state.sshAttached = null;
    state.forms.ssh = AW.sourceForms.ssh(state.sshRoot, {
      host: state.sshHost || undefined,
      compact: true,
      onAttached(result) {
        state.sshAttached = result && typeof result === "object" ? result : null;
        ctx.paint();
      },
    });
  }

  function rescan() {
    return AW.store.refresh("scan", { force: true }).then((value) => {
      const clean = { endpoints: list(value && value.endpoints), hints: list(value && value.hints) };
      ctx.put("endpoints", clean);
    });
  }

  // Option 1: one row per running server, and Workbench's own model when it is serving.
  function modelText(model) {
    const context = AW.fmt.context(model.context);
    if (model.loaded === true) return model.id + " · loaded" + (context ? " · " + context + " context" : "");
    if (model.loaded === false) return model.id + " · not loaded";
    return String(model.id);
  }

  function defaultModel(endpoint) {
    const models = list(endpoint.models).filter((model) => model && model.id);
    const loaded = models.find((model) => model.loaded === true);
    return (loaded || models[0] || {}).id || "";
  }

  function compatSentences(compat) {
    const rows = Object.entries(compat || {});
    const yes = rows.filter(([, row]) => row && row.ok).map(([name]) => name);
    const out = [];
    if (yes.length) out.push("Works with " + AW.fmt.list(yes) + ".");
    for (const [name, row] of rows) if (row && row.ok === false) out.push("Not with " + name + ": " + text(row.reason));
    return out;
  }

  function serverDetail(endpoint) {
    const detail = el("div", { class: "guide-pick-detail" });
    const models = list(endpoint.models)
      .filter((model) => model && model.id)
      .sort((a, b) => Number(b.loaded === true) - Number(a.loaded === true));
    if (models.length) {
      const select = el("select", {}, models.map((model) => el("option", { value: String(model.id), text: modelText(model) })));
      select.value = state.models.get(endpoint.base) || defaultModel(endpoint);
      state.models.set(endpoint.base, select.value);
      select.addEventListener("change", () => state.models.set(endpoint.base, select.value));
      detail.append(AW.ui.field("Model", select).el);
    }
    const sentences = compatSentences(endpoint.compat);
    if (sentences.length) detail.append(el("details", { class: "guide-more" }, el("summary", { text: "Which agents can use it" }), sentences.map((line) => el("p", { text: line }))));
    if (endpoint.auth === "required" || endpoint.auth === "rejected") {
      const input = el("input", { type: "password", value: state.keys.get(endpoint.base) || "", attrs: { autocomplete: "off", spellcheck: "false" } });
      input.addEventListener("input", () => state.keys.set(endpoint.base, input.value));
      const field = AW.ui.field("API key for this server", input, { hint: "This server asks for a key. It is kept in your operating system's credential vault." });
      if (endpoint.auth === "rejected") field.setError("That key was not accepted. Paste the right key, then try again.");
      detail.append(field.el);
    }
    for (const warning of list(endpoint.warnings)) if (text(warning)) detail.append(el("p", { class: "guide-warn" }, AW.ui.mark("warn"), text(warning)));
    return detail;
  }

  function pickRow({ name, value, title, meta, chip, detail, checked, onPick }) {
    const id = uid("pick");
    const radio = el("input", { type: "radio", id, value, checked, attrs: { name, "aria-labelledby": id + "-title", "aria-describedby": meta ? id + "-meta" : null } });
    radio.addEventListener("change", () => radio.checked && onPick());
    const head = el("div", { class: "guide-pick-head" }, el("span", { class: "guide-pick-title", id: id + "-title", text: title }), chip || null);
    const row = el("div", { class: "guide-pick", data: { pick: value } }, radio, el("div", { class: "guide-pick-main" }, head, meta ? el("span", { class: "guide-pick-meta", id: id + "-meta", text: meta }) : null));
    if (detail) {
      detail.hidden = !checked;
      row.append(detail);
    }
    return { row, radio, detail };
  }

  let serversKey = "";
  function paintServers() {
    const endpoints = endpointsOf();
    const running = managed().running;
    const loading = ctx.busy("endpoints") && !ctx.section("endpoints");
    const keys = endpoints.map((item) => item.base);
    if (running) keys.unshift("managed");
    if (!state.server || !keys.includes(state.server)) {
      const row = AW.form.connection();
      const saved = row && row.provider === ATTACHED ? endpoints.find((item) => item.connection_id === row.connection_id || item.base === row.endpoint) : null;
      state.server = saved ? saved.base : keys[0] || null;
    }
    const signature = JSON.stringify([endpoints, running && managed(), hintsOf(), loading, ctx.section("endpoints") && ctx.section("endpoints").ok, state.server]);
    if (signature === serversKey) return;
    const focusedValue = state.serverList.contains(document.activeElement) && document.activeElement.value;
    serversKey = signature;
    const rows = [];
    if (running) {
      const m = managed();
      const context = AW.fmt.context(m.context);
      rows.push(
        pickRow({
          name: "guide-server",
          value: "managed",
          title: "Agent Workbench's own local model" + (m.model ? " · " + m.model : "") + (context ? " · " + context + " context" : ""),
          checked: state.server === "managed",
          onPick: () => pickServer("managed"),
        }),
      );
    }
    for (const endpoint of endpoints) {
      const count = list(endpoint.models).length;
      rows.push(
        pickRow({
          name: "guide-server",
          value: endpoint.base,
          title: (endpoint.label || "Model server") + " · port " + endpoint.port + " · " + count + (count === 1 ? " model" : " models"),
          checked: state.server === endpoint.base,
          detail: serverDetail(endpoint),
          onPick: () => pickServer(endpoint.base),
        }),
      );
    }
    state.serverRows = rows;
    if (loading) state.serverList.replaceChildren(AW.ui.skeleton(2, "model servers"));
    else if (ctx.section("endpoints") && !ctx.section("endpoints").ok) state.serverList.replaceChildren(ctx.sectionError("endpoints", "model servers"));
    else state.serverList.replaceChildren(...rows.map((item) => item.row));
    state.serverEmpty.hidden = loading || rows.length > 0 || !ctx.section("endpoints") || !ctx.section("endpoints").ok;
    state.serverHints.replaceChildren(...hintsOf().map((hint) => el("p", { class: "guide-hint-line" }, AW.ui.mark("warn"), text(hint.text))));
    if (focusedValue) {
      const again = rows.find((item) => item.radio.value === focusedValue);
      if (again) again.radio.focus();
    }
  }

  function pickServer(key) {
    state.server = key;
    for (const item of state.serverRows || []) if (item.detail) item.detail.hidden = item.radio.value !== key;
    ctx.paint();
  }

  // Option 5: the server the address form found becomes a row like option 1.
  function paintFound() {
    if (!state.foundHost) return;
    const found = state.found;
    const key = JSON.stringify(found);
    if (key === state.foundKey) return;
    state.foundKey = key;
    if (!found || !found.base) {
      state.foundHost.replaceChildren();
      return;
    }
    const count = list(found.models).length;
    state.foundHost.replaceChildren(
      el(
        "div",
        { class: "guide-pick guide-pick-found" },
        AW.ui.mark("ok"),
        el(
          "div",
          { class: "guide-pick-main" },
          el("span", { class: "guide-pick-title", text: (found.label || "Model server") + " · " + found.base + " · " + count + (count === 1 ? " model" : " models") }),
        ),
        serverDetail(found),
      ),
    );
  }

  // Option 2: the recommended model, others that fit, and files already here.
  function chipFor(entry) {
    const measured = entry.status === "measured";
    const help = AW.models.statusHelp(entry);
    return AW.ui.chip(measured ? "Measured" : "Suggested", measured ? "ok" : "accent", { help });
  }

  function entryDetail(entry, isPick) {
    const detail = el("div", { class: "guide-pick-detail" });
    const context = AW.fmt.context(entry.settings && entry.settings.context);
    const facts = [AW.fmt.bytes(entry.download_bytes), context && context + " context", entry.licence && entry.licence + " licence"].filter(Boolean).join(" · ");
    if (facts) detail.append(el("p", { class: "guide-pick-facts", text: facts }));
    const reason = isPick ? text(recommendation().reason) : text(entry.why);
    if (reason) detail.append(el("p", { text: reason }));
    if (text(entry.licence_note)) detail.append(el("p", { class: "guide-warn" }, AW.ui.mark("warn"), entry.licence_note));
    const arithmetic = entry.sizing && text(entry.sizing.arithmetic);
    const warnings = isPick ? list(recommendation().warnings).filter((line) => text(line)) : [];
    if (arithmetic || warnings.length)
      detail.append(
        el(
          "details",
          { class: "guide-more" },
          el("summary", { text: "How this was sized" }),
          arithmetic ? el("p", { text: arithmetic }) : null,
          entry.status === "measured" ? el("p", { text: "A result file in this project measures this exact model file." }) : null,
        ),
      );
    for (const line of warnings) detail.append(el("p", { class: "guide-warn" }, AW.ui.mark("warn"), line));
    return detail;
  }

  function localChoices() {
    const entries = catalogEntries();
    const rec = recommendation();
    const pick = rec.pick && rec.pick.id ? rec.pick : null;
    const top = pick && !installed(pick) ? pick : null;
    const others = list(rec.alternatives).filter((entry) => entry && entry.id && !installed(entry));
    const present = entries.filter(installed);
    const taken = new Set(present.map((entry) => base(entry.installed.model)));
    const files = list(library().models).filter((row) => row && row.path && !taken.has(row.name));
    return { pick, top, others, present, files };
  }

  function localKey(choice) {
    if (!choice) return "";
    return choice.type === "catalog" ? "catalog:" + choice.entry.id : "file:" + choice.row.path;
  }

  function ensureLocal() {
    const { top, others, present, files } = localChoices();
    const all = [
      ...(top ? [{ type: "catalog", entry: top }] : []),
      ...others.map((entry) => ({ type: "catalog", entry })),
      ...present.map((entry) => ({ type: "catalog", entry })),
      ...files.map((row) => ({ type: "file", row })),
    ];
    const keys = all.map(localKey);
    if (!state.local || !keys.includes(localKey(state.local))) {
      const file = localFile();
      const wanted = text(ctx.choices().model_entry);
      const busy = all.find((item) => item.type === "catalog" && AW.jobs.find("model:" + item.entry.id));
      const mine = file ? all.find((item) => (item.type === "file" ? item.row.name === file : base(item.entry.installed && item.entry.installed.model) === file)) : null;
      const chosen = wanted ? all.find((item) => item.type === "catalog" && item.entry.id === wanted) : null;
      state.local = busy || mine || chosen || all[0] || null;
    } else state.local = all[keys.indexOf(localKey(state.local))];
    return all;
  }

  function runningInstall() {
    const records = AW.jobs.list().filter((record) => record.state === "running" && (record.action === "engine-install" || record.action === "model-download"));
    return records;
  }

  function lastCancelled(entry) {
    if (!entry) return null;
    const key = "model:" + entry.id;
    if (AW.jobs.find(key)) return null;
    const record = AW.jobs.list().find((item) => item.key === key && item.state !== "running");
    return record && record.state === "cancelled" ? record : null;
  }

  let localKeyPainted = "";
  const taskRows = new Map();
  function paintLocal() {
    if (!state.localHost) return;
    const ready = settled("recommendation") && settled("library") && settled("engine");
    const all = ready ? ensureLocal() : [];
    const tasks = runningInstall();
    const entry = state.local && state.local.type === "catalog" ? state.local.entry : null;
    const cancelled = lastCancelled(entry);
    const signature = JSON.stringify([
      ready,
      recommendation(),
      library(),
      engine(),
      localKey(state.local),
      tasks.map((record) => record.handle),
      cancelled && cancelled.handle,
      ctx.section("recommendation") && ctx.section("recommendation").ok,
      state.installs.get(entry && entry.id) ? String(state.installs.get(entry.id).error && state.installs.get(entry.id).error.message) : "",
    ]);
    if (signature === localKeyPainted) return;
    const focused = state.localHost.contains(document.activeElement) ? document.activeElement : null;
    const focusedValue = focused && focused.type === "radio" ? focused.value : null;
    localKeyPainted = signature;
    for (const row of taskRows.values()) row.destroy();
    taskRows.clear();
    const host = state.localHost;
    host.replaceChildren();
    if (entry) host.dataset.jobKey = "model:" + entry.id;
    else delete host.dataset.jobKey;
    if (!ready) {
      const failed = ["recommendation", "library", "engine"].find((name) => ctx.section(name) && !ctx.section(name).ok);
      host.append(failed ? ctx.sectionError(failed, "the model list") : AW.ui.skeleton(3, "models for this computer"));
      return;
    }
    const eng = engine();
    if (eng && eng.supported === false) {
      host.append(el("p", { class: "guide-empty", text: text(eng.reason) || "The engine for local models cannot run on this computer." }));
      return;
    }
    const { pick, top, others, present, files } = localChoices();
    const group = el("div", { class: "guide-picks", role: "radiogroup", attrs: { "aria-label": "Models for this computer" } });
    const rows = [];
    const addRow = (choice, title, chip, detail) => {
      const key = localKey(choice);
      const item = pickRow({
        name: "guide-local",
        value: key,
        title,
        chip,
        detail,
        checked: key === localKey(state.local),
        onPick() {
          state.local = choice;
          localKeyPainted = "";
          ctx.paint();
        },
      });
      rows.push(item);
      group.append(item.row);
    };
    if (top) addRow({ type: "catalog", entry: top }, top.label, chipFor(top), entryDetail(top, true));
    if (!pick) {
      const reason = text(ctx.value("recommendation") && ctx.value("recommendation").reason);
      group.append(el("p", { class: "guide-empty", text: "No model in the catalog fits this computer." + (reason ? " " + reason : "") }));
    }
    if (others.length) {
      group.append(el("h3", { class: "group-label guide-group", text: "Other models that fit" }));
      for (const entry of others) addRow({ type: "catalog", entry }, entry.label + " · " + AW.fmt.bytes(entry.download_bytes), chipFor(entry), entryDetail(entry, false));
    }
    if (present.length || files.length) {
      group.append(el("h3", { class: "group-label guide-group", text: "Already on this computer" }));
      for (const entry of present) {
        const detail = entryDetail(entry, entry === pick);
        detail.prepend(el("p", { text: "Already downloaded." }));
        addRow({ type: "catalog", entry }, entry.label + " · " + AW.fmt.bytes(entry.download_bytes), chipFor(entry), detail);
      }
      for (const row of files) addRow({ type: "file", row }, row.name + " · " + AW.fmt.gib(row.gib), null, null);
    }
    if (!all.length && !pick) group.append(el("p", { class: "guide-empty", text: "Use a running server, a hosted API or a machine over SSH instead." }));
    host.append(group);
    const engineMissing = Boolean(eng && eng.engine_ready === false);
    const engineSize = engineMissing && Number(eng.download_bytes) > 0 ? " (" + AW.fmt.bytes(eng.download_bytes) + ")" : "";
    const folder = text(library().folder);
    if (tasks.length) {
      const box = el("div", { class: "guide-tasks" });
      for (const record of tasks) {
        const row = AW.ui.taskRow(record.handle);
        taskRows.set(record.handle, row);
        box.append(row.el);
      }
      box.append(el("p", { class: "guide-note-line", text: "You can continue. Workbench finishes this in the background." }));
      host.append(box);
    } else if (cancelled) {
      host.append(
        el(
          "div",
          { class: "guide-tasks" },
          el("p", { class: "notice neutral", text: text(cancelled.error && cancelled.error.message) || "The download was cancelled." }),
          el("p", { class: "guide-note-line", text: "The part already downloaded is kept." }),
          el("div", { class: "actions" }, AW.ui.button("Resume download", { small: true, onClick: (event) => resume(entry, event.currentTarget) })),
        ),
      );
    } else if (state.local && state.local.type === "catalog" && !installed(state.local.entry)) {
      const size = AW.fmt.bytes(state.local.entry.download_bytes);
      const parts = engineMissing ? "the model (" + size + ") and the engine that runs it" + engineSize : "the model (" + size + ")";
      host.append(el("p", { class: "guide-note-line", text: "Downloads: " + parts + (folder ? ", into " + AW.fmt.path(folder) : "") + ". You can go on while it downloads." }));
    } else if (state.local && engineMissing) {
      host.append(el("p", { class: "guide-note-line", text: "Downloads: the engine that runs it" + engineSize + ". You can go on while it downloads." }));
    }
    const failure = entry && state.installs.get(entry.id) && state.installs.get(entry.id).error;
    if (failure) host.append(AW.ui.errorNotice(failure, { retry: () => resume(entry) }));
    if (focusedValue) {
      const again = rows.find((item) => item.radio.value === focusedValue);
      if (again) again.radio.focus();
    }
  }

  function track(entry, install) {
    const record = { error: null };
    state.installs.set(entry.id, record);
    install.then(
      async (value) => {
        if (value && text(value.preset_name)) {
          await AW.store.refresh("config", { force: true }).catch(() => null);
          if (AW.form.provider() === LOCAL) AW.form.set({ preset: value.preset_name });
        }
        ctx.paint();
      },
      (thrown) => {
        const error = AW.asError(thrown);
        if (error.code !== "cancelled") record.error = error;
        localKeyPainted = "";
        ctx.paint();
      },
    );
  }

  // Resolves with "started" when the install's first task begins, or with the install's own answer when it ends first.
  function startInstall(entry, origin) {
    const keys = ["model:" + entry.id, "engine"];
    let off = () => {};
    const started = new Promise((resolve) => {
      off = AW.jobs.on((record, kind) => {
        if (kind === "started" && keys.includes(record.key)) resolve({ started: true });
      });
    });
    const install = Promise.resolve().then(() => AW.models.install(entry, { origin }));
    install.catch(() => {});
    return Promise.race([install.then((value) => ({ value }), (error) => ({ error })), started]).then((first) => {
      off();
      return { first, install };
    });
  }

  async function resume(entry, origin) {
    if (!entry) return;
    const { first, install } = await startInstall(entry, origin);
    if (first.error) throw first.error;
    track(entry, install);
    localKeyPainted = "";
    ctx.paint();
  }

  async function useLocal(origin) {
    const choice = state.local;
    if (!choice) return null;
    if (choice.type === "file") {
      const eng = engine();
      if (eng && eng.engine_ready === false && eng.supported !== false && !AW.jobs.find("engine"))
        AW.jobs.run("engine-install", {}, { title: "Installing the engine for local models", key: "engine" }).catch(() => {});
      await AW.request("studio-save", { model: choice.row.path });
      await AW.store.refresh("config", { force: true });
      AW.form.set({ provider: LOCAL, connection_id: "", preset: "" });
      return { source: "local" };
    }
    const entry = choice.entry;
    const done = { source: "local", model_entry: entry.id };
    if (AW.jobs.find("model:" + entry.id)) {
      AW.form.set({ provider: LOCAL, connection_id: "" });
      return done;
    }
    const { first, install } = await startInstall(entry, origin);
    if (first.error) throw first.error;
    if (first.started) {
      track(entry, install);
      AW.form.set({ provider: LOCAL, connection_id: "" });
      return done;
    }
    const value = first.value;
    if (!value) return null;
    await AW.store.refresh("config", { force: true }).catch(() => null);
    AW.form.set({ provider: LOCAL, connection_id: "", ...(text(value.preset_name) ? { preset: value.preset_name } : {}) });
    return done;
  }

  // Finishing an option: save the connection where needed and point the launch form at it.
  async function useConnection(connectionId, model, source) {
    if (!connectionId) throw { message: "Workbench could not save this connection. Try again." };
    await AW.store.refresh("accounts", { force: true }).catch(() => null);
    AW.form.set({ connection_id: connectionId, ...(model ? { remote_model: model } : {}) });
    return { source, connection_id: connectionId };
  }

  async function attach(endpoint) {
    const payload = { address: endpoint.base, name: (endpoint.label || "Model server") + " on this computer", harness: AW.form.get().harness || "OMP" };
    const model = state.models.get(endpoint.base) || defaultModel(endpoint);
    const key = (state.keys.get(endpoint.base) || "").trim();
    if (model) payload.model = model;
    if (key) payload.key = key;
    const result = await AW.request("endpoint-attach", payload);
    const row = (result && result.connection) || {};
    const choices = await useConnection(row.connection_id, row.remote_model || model, "attach");
    if (text(result.message)) AW.ui.toast(result.message, { kind: "ok" });
    ctx.setNotice("warn", list(result.warnings));
    return choices;
  }

  async function attachSsh() {
    const value = sshValue();
    const same = state.sshAttached && state.sshAttached.connection_id && Number(state.sshAttached.port) === Number(value.port) && text(state.sshAttached.model) === text(value.model);
    if (same) return useConnection(state.sshAttached.connection_id, text(state.sshAttached.model), "ssh");
    const form = state.forms.ssh;
    if (!form || typeof form.attach !== "function") throw { message: "Check the connection and choose a server first." };
    let result;
    try {
      result = await form.attach();
    } catch {
      return null;
    }
    if (!result || !result.connection_id) return null;
    state.sshAttached = { ...result, port: value.port };
    return useConnection(result.connection_id, text(result.model), "ssh");
  }

  function sshValue() {
    try {
      const value = state.forms.ssh && state.forms.ssh.value ? state.forms.ssh.value() : null;
      return value && typeof value === "object" ? value : {};
    } catch {
      return {};
    }
  }

  function hostedValue() {
    try {
      const value = state.forms.hosted && state.forms.hosted.value ? state.forms.hosted.value() : null;
      return value && typeof value === "object" ? value : state.hostedRow || {};
    } catch {
      return state.hostedRow || {};
    }
  }

  function hostedReady() {
    try {
      const form = state.forms.hosted;
      return Boolean(form && ((form.ready && form.ready()) || (form.canSave && form.canSave())));
    } catch {
      return false;
    }
  }

  function selectedEndpoint() {
    return endpointsOf().find((item) => item.base === state.server) || null;
  }

  function paintBadges() {
    const labels = endpointsOf().map((item) => item.label || "a model server");
    if (managed().running) labels.unshift("Agent Workbench");
    const found = cards.get("running").badgeNode;
    found.textContent = labels.length ? "Found: " + AW.fmt.list([...new Set(labels)]) : "";
    found.hidden = !labels.length;
    const pick = recommendation().pick;
    const recommended = cards.get("download").badgeNode;
    recommended.textContent = pick ? "Recommended for this " + (isMac() ? "Mac" : "computer") : "";
    recommended.hidden = !pick;
    const desc = cards.get("download").descNode;
    const size = pick && !installed(pick) ? AW.fmt.bytes(pick.download_bytes) : "";
    const sentence = "Runs here, offline once downloaded." + (size ? " " + size + "." : "");
    if (desc.textContent !== sentence) desc.textContent = sentence;
  }

  const step = {
    title: () => "Choose where the model runs",
    lede: () => "The model does the thinking; the agent in the next step drives it. Pick one place now. You can add more later under Connections.",
    mount(host, context) {
      ctx = context;
      const group = el("div", { class: "choices guide-options", role: "radiogroup", attrs: { "aria-labelledby": "guide-title" } });
      for (const option of OPTIONS) {
        const body = option.id === "account" ? null : el("div", { class: "guide-option-body" });
        const badge = el("span", { class: "chip accent", hidden: true });
        const card = ctx.card({ name: "guide-source", value: option.id, title: option.title, description: option.description, badge, body });
        card.badgeNode = badge;
        card.el.classList.add("guide-option");
        card.el.dataset.option = option.id;
        card.input.addEventListener("change", () => card.input.checked && choose(option.id, true));
        cards.set(option.id, card);
        if (body) bodies.set(option.id, body);
        group.append(card.el);
      }
      host.append(group);
    },
    show(context) {
      ctx = context;
      state.picked = false;
      state.option = null;
      for (const card of cards.values()) {
        card.input.checked = false;
        card.setOpen(false);
      }
    },
    paint(context) {
      ctx = context;
      if (!state.picked) {
        const option = preselect();
        if (option && option !== state.option) choose(option, false);
      }
      paintBadges();
      if (state.option === "running" && state.serverList) paintServers();
      if (state.option === "download") paintLocal();
      if (state.option === "address") paintFound();
    },
    focusMissing() {
      const card = (state.option && cards.get(state.option)) || cards.values().next().value;
      if (!card) return false;
      const body = bodies.get(state.option);
      if (body && state.option && AW.focus.first(body)) return true;
      card.input.focus();
      return true;
    },
    footer() {
      const option = state.option;
      const blocked = (label) => ({ primary: { label: label || "Continue", reason: NEED_CHOICE } });
      if (!option) return blocked();
      if (option === "running") {
        if (state.server === "managed" && managed().running)
          return { primary: { label: "Use it and continue", run: () => (AW.form.set({ provider: LOCAL, connection_id: "" }), { source: "local" }) } };
        const endpoint = selectedEndpoint();
        if (!endpoint) return blocked("Attach and continue");
        return { primary: { label: "Attach and continue", pending: "Attaching…", run: () => attach(endpoint) } };
      }
      if (option === "download") {
        const eng = engine();
        if ((eng && eng.supported === false) || !state.local) return blocked(state.local ? "Use this model and continue" : "Download and continue");
        const fresh = state.local.type === "catalog" && !installed(state.local.entry);
        const running = fresh && AW.jobs.find("model:" + state.local.entry.id);
        return { primary: { label: fresh && !running ? "Download and continue" : running ? "Continue" : "Use this model and continue", pending: fresh ? "Starting…" : "Saving…", run: useLocal } };
      }
      if (option === "hosted") {
        if (!hostedReady()) return { primary: { label: "Save and continue", reason: "Paste a key and enter a model to continue." } };
        return {
          primary: {
            label: "Save and continue",
            pending: "Saving…",
            async run() {
              const form = state.forms.hosted;
              const value = form && form.save ? await form.save() : hostedValue();
              return useConnection(text(value.connection_id) || text(state.hostedRow && state.hostedRow.connection_id), text(value.remote_model), "hosted");
            },
          },
        };
      }
      if (option === "ssh") {
        const value = sshValue();
        const ready = (state.sshAttached && state.sshAttached.connection_id) || (text(value.host) && Number(value.port) > 0);
        if (!ready) return blocked("Attach and continue");
        return { primary: { label: "Attach and continue", pending: "Attaching…", run: attachSsh } };
      }
      if (option === "address") {
        if (!state.found || !state.found.base) return blocked("Attach and continue");
        return { primary: { label: "Attach and continue", pending: "Attaching…", run: () => attach(state.found) } };
      }
      return { primary: { label: "Continue", run: () => (AW.form.set({ provider: NATIVE, connection_id: "" }), {}) } };
    },
  };

  const steps = (AW.guideSteps = AW.guideSteps || {});
  steps.model = Object.assign(steps.model || {}, step);
})();
