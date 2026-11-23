// Connections view (ui.md 5.4): every place a model runs, as a list with a detail, and the fixes that act on a connection
// (source.attach, source.scan, source.refresh, source.probe, source.key, source.keypage, source.oauth, model.browse, account.login).
// Route params: select (a connection_id, "Managed local" or "found:<port>") and add ("hosted", "account" or "attach").
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const LOCAL = "Managed local";
  const NATIVE = "Native login";
  const ATTACHED = "Attached endpoint";
  const GROUPS = [
    ["local", "On this computer"],
    ["ssh", "Over SSH"],
    ["hosted", "Hosted"],
    ["accounts", "Agent accounts"],
  ];
  const NARROW = "(max-width: 899px)";
  const checks = new Map();

  const S = () => AW.sources;
  const accounts = () => (Array.isArray(AW.state.accounts) ? AW.state.accounts : []);
  const rowOf = (id) => accounts().find((row) => row.connection_id === id) || null;
  const nameOf = (row) => (row && (row.name || S().label(row.provider))) || "this connection";
  const scanRows = () => (AW.state.scan && Array.isArray(AW.state.scan.endpoints) ? AW.state.scan.endpoints : []);
  const narrow = () => window.matchMedia(NARROW).matches;

  function forwardOf(row) {
    return (Array.isArray(AW.state.forwards) ? AW.state.forwards : []).find((item) => item && item.connection_id === row.connection_id) || null;
  }

  function seenIn(row) {
    return scanRows().find((item) => item.connection_id === row.connection_id || (item.base && item.base === row.endpoint)) || null;
  }

  function checkedAt(row) {
    const check = checks.get(row.connection_id);
    const scan = AW.state.scan && AW.state.scan.received;
    return Math.max(check ? check.at : 0, scan || 0);
  }

  function liveView() {
    const live = AW.state.live;
    if (!live) return AW.store.error("live") ? { mark: "warn", word: "Status unavailable" } : { mark: "idle", word: "Checking…" };
    if (live.label === "Serving") return { mark: "ok", word: "Serving", serving: true };
    if (live.label === "Starting") {
      const seconds = live.started ? Math.max(0, Math.round(Date.now() / 1000 - live.started)) : 0;
      return { mark: "busy", word: "Loading… " + seconds + " s", loading: true };
    }
    if (live.label === "Port in use") return { mark: "warn", word: "Port " + live.port + " in use", port: true };
    if (live.label === "Unexpected model") return { mark: "warn", word: "Unexpected model", serving: true, unexpected: true };
    if (live.label === "Stopped") return { mark: "idle", word: "Stopped" };
    return { mark: "warn", word: "Status unavailable" };
  }

  function setupName() {
    const form = AW.form.get();
    const config = AW.state.config || {};
    if (form.preset) return form.preset;
    return AW.fmt.folder(config.current && config.current.model);
  }

  function hostedWord(row) {
    if (row.ready === false) return { mark: "warn", word: "Needs a key" };
    if (row.key_source === "env") {
      const info = S().providerRow(row.provider);
      const env = String(row.connection_id).startsWith("legacy-") ? (info && info.key_env) || "" : "WORKBENCH_KEY_" + String(row.connection_id).toUpperCase();
      return { mark: "ok", word: "Key from " + (env || "your environment") };
    }
    if (row.key_source === "memory") return { mark: "ok", word: "Key kept until you quit" };
    return { mark: "ok", word: "Key saved" };
  }

  function attachedWord(row) {
    if (row.host) {
      const machine = S().machineName(row.host);
      const forward = forwardOf(row);
      if (forward && forward.alive === false) return { mark: "down", word: "Tunnel down", detail: "Through " + machine };
      if (forward) return { mark: "ok", word: "Tunnel open", detail: "Through " + machine };
      return { mark: "idle", word: "Through " + machine };
    }
    const when = checkedAt(row);
    const ago = when ? "checked " + AW.fmt.ago(when) : "";
    const check = checks.get(row.connection_id);
    const failed = (AW.state.unreachable || {})[row.connection_id];
    if (failed) return { mark: "down", word: "Not answering", ago };
    if (check && check.attention) return { mark: "warn", word: "Key needs attention", ago };
    const found = seenIn(row);
    if (found) {
      const n = (found.models || []).length;
      return { mark: "ok", word: "Answering · " + S().countText(n, "model", "models"), ago };
    }
    if (AW.state.scan && AW.state.scan.received) return { mark: "down", word: "Not answering", ago };
    return { mark: "idle", word: "Not checked yet" };
  }

  function groupOf(row) {
    if (row.provider === NATIVE) return "accounts";
    if (row.provider === ATTACHED) return row.host || row.context ? "ssh" : "local";
    return "hosted";
  }

  function items() {
    const out = [];
    const local = liveView();
    const setup = setupName();
    out.push({ key: LOCAL, group: "local", kind: "local", mark: local.mark, title: "Local model", meta: [local.word, setup].filter(Boolean).join(" · ") });
    for (const row of accounts()) {
      const group = groupOf(row);
      let view;
      if (row.provider === NATIVE) view = { mark: "idle", word: "Agent account" + (row.harness ? " · " + row.harness : "") };
      else if (row.provider === ATTACHED) view = attachedWord(row);
      else view = hostedWord(row);
      const meta = [view.word, view.ago, row.provider !== NATIVE && row.provider !== ATTACHED && row.remote_model ? row.remote_model : ""].filter(Boolean).join(" · ");
      out.push({ key: row.connection_id, group, kind: row.provider === NATIVE ? "account" : row.provider === ATTACHED ? "attached" : "hosted", mark: view.mark, title: nameOf(row), meta, row });
    }
    const attached = new Set(accounts().filter((row) => row.provider === ATTACHED && !row.host).map((row) => row.endpoint));
    for (const endpoint of scanRows()) {
      if ((endpoint.connection_id && rowOf(endpoint.connection_id)) || attached.has(endpoint.base)) continue;
      out.push({ key: "found:" + endpoint.port, group: "local", kind: "found", mark: "ok", title: (endpoint.label || "Server") + " · port " + endpoint.port, meta: "Found, not attached", endpoint });
    }
    return out;
  }

  const view = {
    root: null,
    split: null,
    listbox: null,
    lists: new Map(),
    groups: new Map(),
    detailPane: null,
    detail: null,
    selected: null,
    focusKey: null,
    offs: [],
    timer: null,
    scanErrors: null,
    scanLine: null,
    hints: null,
    listState: null,
  };

  function option(item) {
    const node = el("div", { class: "row accounts-option", role: "option", tabindex: -1, data: { key: item.key }, attrs: { "aria-selected": "false" } });
    const mark = AW.ui.mark(item.mark);
    const title = el("span", { class: "row-title" });
    const meta = el("span", { class: "row-meta" });
    const status = el("span", { class: "sr-only" });
    node.append(mark, el("div", { class: "row-main" }, title, meta, status));
    node._parts = { mark, title, meta, status };
    paintOption(node, item);
    node.addEventListener("click", () => choose(item.key, { user: true }));
    return node;
  }

  function paintOption(node, item) {
    const parts = node._parts;
    AW.ui.setMark(parts.mark, item.mark);
    if (parts.title.textContent !== item.title) parts.title.textContent = item.title;
    if (parts.meta.textContent !== item.meta) parts.meta.textContent = item.meta;
    node.setAttribute("aria-selected", String(item.key === view.selected));
  }

  function paintList() {
    if (!view.root) return;
    const loading = AW.store.age("accounts") === Infinity && !AW.store.error("accounts");
    const failed = AW.store.error("accounts");
    view.listState.replaceChildren();
    if (loading) view.listState.append(AW.ui.skeleton(3, "connections"));
    else if (failed && !accounts().length) view.listState.append(AW.ui.errorNotice(failed, { retry: () => AW.store.refresh("accounts", { force: true }).catch(() => {}) }));
    const all = items();
    for (const [group] of GROUPS) {
      const rows = all.filter((item) => item.group === group);
      view.lists.get(group).set(rows);
      view.groups.get(group).hidden = !rows.length || (loading && group !== "local");
    }
    const options = [...view.listbox.querySelectorAll('[role="option"]')];
    const focusable = options.find((node) => node.dataset.key === view.selected) || options[0];
    for (const node of options) node.tabIndex = node === focusable ? 0 : -1;
    const empty = !loading && !accounts().length && !scanRows().length;
    if (!view.detail && view.detailPane && view.detailPane.dataset.empty !== String(empty)) renderDetail(null);
    paintScanLine();
  }

  function paintScanLine() {
    const scan = AW.state.scan;
    const error = AW.store.error("scan");
    const when = scan && scan.received ? "Checked " + AW.fmt.ago(scan.received) + "." : "";
    if (view.scanLine.textContent !== when) view.scanLine.textContent = when;
    view.scanLine.hidden = !when;
    const hints = ((scan && scan.hints) || []).filter((hint) => hint && hint.text).map((hint) => hint.text);
    const signature = JSON.stringify(hints);
    if (view.hints.dataset.signature !== signature) {
      view.hints.dataset.signature = signature;
      view.hints.replaceChildren(...hints.map((line) => el("p", { class: "hint", text: line })));
    }
    if (!error) AW.ui.clearError(view.scanErrors);
    else if (!view.scanErrors.querySelector("[data-error]")) AW.ui.showError(view.scanErrors, error, { retry: scan });
  }

  function scan() {
    AW.ui.clearError(view.scanErrors);
    return AW.store.refresh("scan", { force: true }).catch((thrown) => {
      AW.ui.showError(view.scanErrors, thrown, { retry: scan });
    });
  }

  function addMenu(anchor) {
    AW.ui.menu(anchor, [
      { label: "Attach a running server…", run: () => attachFlow({}) },
      { label: "Hosted API", run: () => choose("new:hosted", { user: true }) },
      { label: "Machine over SSH", run: () => AW.router.go("machines", { add: true }) },
      { label: "Agent account", run: () => choose("new:account", { user: true }) },
    ]);
  }

  function attachFlow(options) {
    return AW.attach.open(options || {});
  }

  function selectAttached() {
    const plain = AW.attach.open;
    AW.attach.open = async (options) => {
      const row = await plain(options);
      if (row && row.connection_id && view.root && AW.router.current().id === "accounts") choose(row.connection_id, { user: true });
      return row;
    };
  }

  function mount(root) {
    view.root = root;
    selectAttached();
    const scanButton = AW.ui.button("Scan this computer", { icon: "refresh", pending: "Scanning…", onClick: scan });
    const addButton = AW.ui.button("Add", { icon: "plus", onClick: () => addMenu(addButton) });
    addButton.setAttribute("aria-haspopup", "menu");
    view.scanErrors = el("div", { class: "accounts-errors" });
    view.scanLine = el("p", { class: "hint accounts-scan-line", hidden: true });
    view.hints = el("div", { class: "stack accounts-hints" });
    view.listState = el("div");
    view.listbox = el("div", { id: "connection-list", class: "accounts-listbox", role: "listbox", attrs: { "aria-label": "Connections" } });
    for (const [group, title] of GROUPS) {
      const headId = "accounts-group-" + group;
      const rows = el("div");
      const node = el("div", { class: "accounts-group", role: "group", attrs: { "aria-labelledby": headId } }, el("h3", { class: "group-label accounts-group-label", id: headId, text: title }), rows);
      view.groups.set(group, node);
      view.lists.set(group, AW.ui.list(rows, { key: (item) => item.key, render: option, update: paintOption }));
      view.listbox.append(node);
    }
    view.listbox.addEventListener("keydown", onListKey);
    view.listbox.addEventListener("focusin", (event) => {
      const node = event.target.closest('[role="option"]');
      if (node) view.focusKey = node.dataset.key;
    });
    const listPane = el(
      "section",
      { class: "panel accounts-list-pane", attrs: { "aria-label": "Connection list" } },
      el("div", { class: "actions accounts-toolbar" }, scanButton, addButton),
      view.scanErrors,
      view.scanLine,
      view.hints,
      view.listState,
      view.listbox,
    );
    view.detailPane = el("section", { class: "panel accounts-detail", id: "connection-detail", attrs: { "aria-live": "off" } });
    view.split = el("div", { class: "split accounts-split" }, listPane, view.detailPane);
    root.append(
      el("h1", { class: "title", id: "accounts-title", tabindex: -1, text: "Connections" }),
      el("p", { class: "lede", text: "Places a model runs that you can launch an agent with: this computer, a machine over SSH, a hosted API, or an agent's own account." }),
      view.split,
    );
    window.matchMedia(NARROW).addEventListener("change", () => {
      if (!narrow()) view.split.classList.remove("show-detail");
    });
  }

  function onListKey(event) {
    const options = [...view.listbox.querySelectorAll('[role="option"]')].filter((node) => node.getClientRects().length);
    const at = options.indexOf(document.activeElement);
    if (at < 0) return;
    let next = null;
    if (event.key === "ArrowDown") next = options[Math.min(options.length - 1, at + 1)];
    else if (event.key === "ArrowUp") next = options[Math.max(0, at - 1)];
    else if (event.key === "Home") next = options[0];
    else if (event.key === "End") next = options[options.length - 1];
    else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      choose(options[at].dataset.key, { user: true });
      return;
    }
    if (!next) return;
    event.preventDefault();
    for (const node of options) node.tabIndex = node === next ? 0 : -1;
    next.focus();
  }

  function itemFor(key) {
    if (key === "new:hosted") return { key, kind: "new-hosted" };
    if (key === "new:account") return { key, kind: "new-account" };
    return items().find((item) => item.key === key) || null;
  }

  function choose(key, { user = false, focus = true } = {}) {
    if (!view.root) return;
    const item = key ? itemFor(key) : null;
    const same = view.detail && view.detail.key === key && item;
    view.selected = item ? key : null;
    paintList();
    if (!same) renderDetail(item);
    if (user && narrow() && item) view.split.classList.add("show-detail");
    if (user && focus && item && narrow()) {
      const heading = view.detailPane.querySelector("h2");
      if (heading) heading.focus();
    }
  }

  function back() {
    view.split.classList.remove("show-detail");
    const node = view.listbox.querySelector('[role="option"][aria-selected="true"]') || view.listbox.querySelector('[role="option"]');
    if (node) node.focus();
  }

  function renderDetail(item) {
    if (view.detail && view.detail.destroy) view.detail.destroy();
    view.detail = null;
    view.detailPane.replaceChildren();
    if (!item) {
      const empty = AW.store.age("accounts") !== Infinity && !accounts().length && !scanRows().length;
      view.detailPane.dataset.empty = String(empty);
      if (empty) {
        const add = AW.ui.button("Add", { icon: "plus", onClick: () => addMenu(add) });
        add.setAttribute("aria-haspopup", "menu");
        view.detailPane.append(el("div", { class: "empty accounts-empty" }, el("p", { text: "No connections yet." }), el("div", { class: "empty-actions" }, AW.ui.button("Scan this computer", { pending: "Scanning…", onClick: scan }), add)));
      } else view.detailPane.append(el("p", { class: "muted accounts-pick-hint", text: "Choose a connection to see and change it." }));
      view.detailPane.classList.add("accounts-detail-idle");
      return;
    }
    delete view.detailPane.dataset.empty;
    view.detailPane.classList.remove("accounts-detail-idle");
    const backButton = AW.ui.button("‹ Connections", { kind: "link", onClick: back });
    backButton.classList.add("accounts-back");
    const heading = el("h2", { class: "accounts-detail-title", tabindex: -1 });
    const body = el("div", { class: "accounts-detail-body" });
    view.detailPane.append(backButton, heading, body);
    const builders = { local: localDetail, attached: attachedDetail, found: foundDetail, hosted: hostedDetail, "new-hosted": hostedDetail, account: accountDetail, "new-account": accountDetail };
    view.detail = builders[item.kind](item, heading, body);
    view.detail.key = item.key;
  }

  function updateDetail() {
    if (!view.detail) return;
    if (view.detail.key && !view.detail.key.startsWith("new:") && !itemFor(view.detail.key)) {
      choose(null);
      return;
    }
    if (view.detail.update) view.detail.update();
  }

  function worksBlock(compat) {
    const host = el("div", { class: "accounts-block" }, el("h3", { class: "group-label", text: "Works with" }));
    const paint = (value) => {
      host.replaceChildren(el("h3", { class: "group-label", text: "Works with" }));
      if (value && Object.keys(value).length) host.append(AW.ui.worksWith(value));
      else host.append(el("p", { class: "muted", text: "Not known yet." }));
    };
    paint(compat);
    return { el: host, paint };
  }

  function footer(row, { removable = true, provider } = {}) {
    const errors = el("div", { class: "accounts-errors" });
    const project = AW.project.current() || AW.form.get().workspace;
    const folder = AW.project.name(project);
    const id = row ? row.connection_id : provider;
    const name = row ? nameOf(row) : "Local model";
    const buttons = [];
    const use = async () => {
      await AW.fix.run("source.use", id);
      const agents = (AW.state.config && AW.state.config.agents) || [];
      if (row && row.provider === NATIVE && row.harness && agents.includes(row.harness)) AW.form.set({ harness: row.harness });
    };
    if (row && removable)
      buttons.push(
        AW.ui.button("Remove…", {
          kind: "danger",
          errorHost: errors,
          async onClick() {
            const ok = await AW.ui.confirm({ text: "Remove " + name + "? Its saved key is removed too.", ok: "Remove", danger: true });
            if (!ok) return;
            const answer = await AW.request("source-remove", { connection_id: row.connection_id });
            checks.delete(row.connection_id);
            AW.ui.toast((answer && answer.message) || "Connection removed.");
            await S().refreshAccounts();
            choose(null);
          },
        }),
      );
    const space = el("span", { class: "accounts-actions-space" });
    buttons.push(space);
    if (folder)
      buttons.push(
        AW.ui.button("Use in " + folder, {
          errorHost: errors,
          async onClick() {
            await use();
            AW.ui.toast(name + " is selected for " + folder + ". Not saved yet.");
          },
        }),
      );
    buttons.push(
      AW.ui.button("Launch with this", {
        kind: "primary",
        id: AW.$("use-provider") ? undefined : "use-provider",
        errorHost: errors,
        async onClick() {
          await use();
          await AW.router.go("launch");
        },
      }),
    );
    return el("div", { class: "accounts-footer" }, el("div", { class: "actions" }, buttons), errors);
  }

  function statusHead(onCheck) {
    const line = S().statusLine("accounts-head-status");
    const actions = el("div", { class: "actions" });
    const errors = el("div", { class: "accounts-errors" });
    const node = el("div", { class: "accounts-head" }, el("div", { class: "accounts-head-row" }, line.el, actions), errors);
    if (onCheck) actions.append(AW.ui.button("Check again", { small: true, pending: "Checking…", onClick: () => onCheck(errors) }));
    return { el: node, line, actions, errors };
  }

  function localDetail(item, heading, body) {
    heading.textContent = "Local model";
    const head = statusHead(null);
    const errors = head.errors;
    const startModel = () =>
      AW.jobs.run("server-start", AW.form.get(), { title: "Starting the local model", origin: start, key: "server" }).then(() => AW.store.refresh("live"));
    const start = AW.ui.button("Start", { small: true, kind: "primary", id: AW.$("serve") ? undefined : "serve", errorHost: errors, onClick: startModel });
    const stop = AW.ui.button("Stop", {
      small: true,
      errorHost: errors,
      async onClick() {
        const ok = await AW.ui.confirm({ text: "Stop the local model? Sessions that use it lose their model.", ok: "Stop", danger: true });
        if (!ok) return;
        await AW.jobs.run("server-stop", {}, { title: "Stopping the local model", origin: stop, key: "server-stop" });
        await AW.store.refresh("live");
      },
    });
    const cancel = AW.ui.button("Cancel", {
      small: true,
      pending: "Cancelling…",
      errorHost: errors,
      async onClick() {
        const job = AW.jobs.find("server");
        if (job) await AW.jobs.cancel(job.handle);
        else await AW.jobs.run("server-stop", {}, { title: "Stopping the local model", key: "server-stop" });
        await AW.store.refresh("live");
      },
    });
    const adopt = AW.ui.button("Use it", { small: true, errorHost: errors, onClick: () => AW.fix.run("server.adopt") });
    const attachPort = AW.ui.button("Attach it", { small: true, errorHost: errors, onClick: () => AW.fix.run("source.probe", (AW.state.live || {}).port) });
    head.actions.append(start, cancel, stop, adopt, attachPort);
    const note = el("p", { class: "hint", id: "accounts-local-note", hidden: true });
    start.setAttribute("aria-describedby", note.id);
    const taskHost = el("div", { class: "accounts-task-host" });

    const presetSelect = el("select");
    const presetField = AW.ui.field("Saved setup", presetSelect, { hint: "Start and launches with the local model use this setup.", id: "accounts-preset" });
    const info = el("p", { class: "accounts-local-info" });
    const copy = AW.ui.button("Copy local API key", {
      small: true,
      errorHost: errors,
      async onClick() {
        await AW.request("server-copy-key");
        AW.ui.toast("Copied.");
      },
    });
    const library = AW.ui.button("Choose or download a model in Advanced → Model library", { kind: "link", onClick: () => AW.router.go("models", { returnTo: "accounts" }) });
    const works = worksBlock(null);
    body.append(head.el, note, taskHost, el("div", { class: "accounts-block" }, presetField.el, info, el("div", { class: "actions" }, copy, library)), works.el, footer(null, { provider: LOCAL }));
    let task = null;

    presetSelect.addEventListener("change", () => AW.form.set({ preset: presetSelect.value }));

    function paintPresets() {
      const config = AW.state.config || {};
      const names = Object.keys(config.presets || {});
      const form = AW.form.get();
      const options = [el("option", { value: "", text: names.length ? "Current settings" : "Current settings (no saved setup yet)" }), ...names.map((name) => el("option", { value: name, text: name }))];
      const signature = JSON.stringify(names);
      if (presetSelect.dataset.signature !== signature) {
        presetSelect.dataset.signature = signature;
        presetSelect.replaceChildren(...options);
      }
      presetSelect.value = names.includes(form.preset) ? form.preset : "";
      const spec = (form.preset && config.presets && config.presets[form.preset]) || config.current || {};
      const file = AW.fmt.folder(spec.model);
      const context = AW.fmt.context(spec.context);
      const words = file ? [file, spec.engine, context ? context + " context" : ""].filter(Boolean).join(" · ") : "No model file is chosen yet.";
      if (info.textContent !== words) info.textContent = words;
    }

    function paint() {
      const state = liveView();
      const live = AW.state.live || {};
      const loading = state.loading || Boolean(AW.jobs.find("server"));
      head.line.set(loading ? "busy" : state.mark, loading && !state.loading ? "Loading…" : state.word, live.label === "Serving" && live.model ? AW.fmt.folder(live.model) : "");
      start.hidden = loading || state.serving || state.port;
      cancel.hidden = !loading;
      stop.hidden = loading || !state.serving;
      adopt.hidden = !state.unexpected;
      attachPort.hidden = !state.port;
      const config = AW.state.config || {};
      const chosen = Boolean(AW.form.get().preset || (config.current && config.current.model));
      let words = "";
      if (state.port) words = "Port " + live.port + " is used by another program, so the local model cannot start.";
      else if (state.unexpected) words = "A different local model is serving than the one this folder selects.";
      else if (!start.hidden && !chosen) words = "Choose a model in Advanced → Model library first.";
      note.hidden = !words;
      if (note.textContent !== words) note.textContent = words;
      if (!start.hidden && !chosen) start.setAttribute("aria-disabled", "true");
      else if (!start.dataset.pending && !start.dataset.job) start.removeAttribute("aria-disabled");
      const job = AW.jobs.find("server");
      if (job && (!task || task.handle !== job.handle)) {
        if (task) task.row.destroy();
        task = { handle: job.handle, row: AW.ui.taskRow(job.handle) };
        taskHost.replaceChildren(task.row.el);
      } else if (!job && task) {
        task.row.destroy();
        task = null;
      }
      paintPresets();
    }

    AW.request("source-compat", { provider: LOCAL }).then(works.paint, (thrown) => works.el.append(AW.ui.errorNotice(thrown)));
    const offs = [AW.jobs.on((record) => record.key === "server" && paint()), AW.form.on(paint)];
    paint();
    return {
      update: paint,
      destroy() {
        offs.forEach((off) => off());
        if (task) task.row.destroy();
      },
    };
  }

  async function refreshConnection(id) {
    const row = rowOf(id);
    const name = nameOf(row);
    try {
      const answer = await AW.request("endpoint-refresh", { connection_id: id });
      const attention = (answer.auth === "required" || answer.auth === "rejected") && !(answer.models || []).length;
      checks.set(id, { at: Date.now(), models: answer.models || [], auth: answer.auth, warnings: answer.warnings || [], attention });
      const unreachable = { ...(AW.state.unreachable || {}) };
      if (unreachable[id]) {
        delete unreachable[id];
        AW.store.set("unreachable", unreachable);
      }
      await S().refreshAccounts();
      return { answer, attention, name };
    } catch (thrown) {
      const error = AW.asError(thrown);
      AW.store.set("unreachable", { ...(AW.state.unreachable || {}), [id]: error });
      throw error;
    }
  }

  function attentionText(name, auth) {
    return auth === "rejected"
      ? name + " did not accept the saved key. Save the right key below, then check again."
      : name + " asks for a key. Save its key below, then check again.";
  }

  function attachedDetail(item, heading, body) {
    let row = item.row;
    const id = row.connection_id;
    const ssh = Boolean(row.host || row.context);
    const managed = Boolean((row.context || row.host) && !row.remote_port);
    const head = statusHead(async (errors) => {
      AW.ui.clearError(errors);
      try {
        const result = await refreshConnection(id);
        AW.ui.announce(result.attention ? attentionText(result.name, result.answer.auth) : result.name + " answered.");
      } catch (thrown) {
        AW.ui.showError(errors, thrown);
      }
      paint();
    });
    const endpointLine = el("p", { class: "mono accounts-endpoint" });
    const tunnel = el("div", { class: "accounts-tunnel", hidden: !ssh });
    const tunnelText = el("span");
    const tunnelErrors = el("div", { class: "accounts-errors" });
    const openMachine = AW.ui.button("Open machine", { small: true, id: AW.$("node-settings") ? undefined : "node-settings", onClick: () => AW.router.go("machines", { select: row.host, host: row.host, returnTo: "accounts" }) });
    const reconnect = AW.ui.button("Reconnect", { small: true, kind: "primary", pending: "Connecting…", errorHost: tunnelErrors, onClick: () => reconnectRow(row, reconnect) });
    tunnel.append(el("div", { class: "accounts-head-row" }, tunnelText, el("div", { class: "actions" }, openMachine, reconnect)), tunnelErrors);
    const attention = el("div");
    const warnings = el("div", { class: "stack" });

    const nameInput = el("input", { type: "text", attrs: { autocomplete: "off", spellcheck: "false", maxlength: "90" } });
    const nameField = AW.ui.field("Name", nameInput, { id: "accounts-name-" + id });
    const modelSelect = el("select");
    const modelInput = el("input", { type: "text", attrs: { autocomplete: "off", spellcheck: "false", placeholder: "Model ID" } });
    const browse = AW.ui.button("Browse models", { pending: "Loading models…", onClick: browseModels });
    const modelField = AW.ui.field("Model", el("div", { class: "inputrow" }, modelSelect, modelInput, browse), { id: "accounts-model-" + id });
    const group = "accounts-context-" + id;
    const reported = el("input", { type: "radio", value: "server", attrs: { name: group } });
    const mine = el("input", { type: "radio", value: "user", attrs: { name: group } });
    const sizeInput = el("input", { type: "text", class: "accounts-size", attrs: { inputmode: "numeric", autocomplete: "off", "aria-label": "Context size in tokens" } });
    const reportedText = el("span");
    const contextSet = el(
      "fieldset",
      { class: "field accounts-context" },
      el("legend", { class: "field-label", text: "Context" }),
      el("label", { class: "accounts-radio" }, reported, reportedText),
      el("label", { class: "accounts-radio" }, mine, el("span", { text: "Set it myself" }), sizeInput, el("span", { class: "muted", text: "tokens" })),
    );
    const contextError = el("p", { class: "field-error", hidden: true, id: "accounts-context-error-" + id });
    contextSet.append(contextError);
    const saveErrors = el("div", { class: "accounts-errors" });
    const save = AW.ui.button("Save", { kind: "primary", pending: "Saving…", onClick: saveRow });

    const keyBox = el("div", { class: "accounts-block" });
    const works = worksBlock(row.compat);
    body.append(
      head.el,
      endpointLine,
      tunnel,
      attention,
      warnings,
      el("div", { class: "accounts-block" }, nameField.el, modelField.el, contextSet, el("div", { class: "actions" }, save), saveErrors),
      keyBox,
      works.el,
      managed ? footer(row, { removable: false }) : footer(row),
    );
    if (managed) {
      const note = el("p", { class: "hint", text: "Workbench runs this model on " + S().machineName(row.host) + ". Remove it under Machines so its tunnel and remote runtime are cleaned up." });
      body.insertBefore(note, body.lastChild);
    }

    nameInput.value = row.name || "";
    const userSize = row.context_source === "user" && row.context_window;
    if (userSize) {
      mine.checked = true;
      sizeInput.value = String(row.context_window);
    } else reported.checked = true;
    sizeInput.addEventListener("focus", () => (mine.checked = true));

    function models() {
      const check = checks.get(id);
      const found = seenIn(row);
      const list = (check && check.models && check.models.length ? check.models : found && found.models) || [];
      return S().sortModels(list);
    }

    function paintModels() {
      const list = models();
      const keep = modelSelect.hidden ? row.remote_model : modelSelect.value || row.remote_model;
      const known = list.some((m) => m.id === keep);
      const options = list.map((m) => el("option", { value: m.id, text: S().modelText(m) }));
      if (keep && !known) options.unshift(el("option", { value: keep, text: keep }));
      const signature = JSON.stringify(options.map((node) => node.textContent));
      if (modelSelect.dataset.signature !== signature) {
        modelSelect.dataset.signature = signature;
        modelSelect.replaceChildren(...options);
      }
      if (keep) modelSelect.value = keep;
      modelSelect.hidden = !options.length;
      modelInput.hidden = options.length > 0;
      if (!options.length && !modelInput.value) modelInput.value = row.remote_model || "";
    }

    function paint() {
      row = rowOf(id) || row;
      heading.textContent = nameOf(row);
      const state = attachedWord(row);
      head.line.set(state.mark, state.word, [state.detail, state.ago].filter(Boolean).join(" · "));
      if (endpointLine.textContent !== row.endpoint) endpointLine.textContent = row.endpoint || "";
      if (ssh) {
        const machine = S().machineName(row.host);
        const forward = forwardOf(row);
        tunnelText.textContent = "Through an SSH tunnel to " + machine + (forward && forward.alive === false ? ". The tunnel is down." : ".");
        reconnect.hidden = !(forward && forward.alive === false);
        openMachine.hidden = !row.host;
      }
      const check = checks.get(id);
      attention.replaceChildren();
      if (check && check.attention) attention.append(AW.ui.notice("warn", attentionText(nameOf(row), check.auth)));
      warnings.replaceChildren(...((check && check.warnings) || []).map((line) => AW.ui.notice("warn", line)));
      const size = row.context_window && row.context_source !== "user" ? AW.fmt.context(row.context_window) + " reported" : "";
      reportedText.textContent = "Use what the server reports" + (size ? " (" + size + ")" : "");
      paintModels();
      works.paint(row.compat);
      paintKey();
    }

    const keyTicket = AW.ticket();
    let credential = null;
    function paintKey() {
      const check = checks.get(id);
      const found = seenIn(row);
      const auth = (check && check.auth) || (found && found.auth) || "";
      const state = credential ? S().keyWord(credential) : null;
      const signature = JSON.stringify([credential && credential.source, auth]);
      if (keyBox.dataset.signature === signature) return;
      keyBox.dataset.signature = signature;
      keyBox.replaceChildren(el("h3", { class: "group-label", text: "Key" }));
      if (!credential) {
        keyBox.append(el("p", { class: "muted", text: "Checking the key…" }));
        return;
      }
      if (state && state.word !== "Needs a key") {
        const line = S().statusLine();
        line.set(state.mark, state.word);
        const errors = el("div", { class: "accounts-errors" });
        const actions = el("div", { class: "actions" });
        if (state.saved)
          actions.append(
            AW.ui.button("Forget key", {
              small: true,
              kind: "danger",
              errorHost: errors,
              async onClick() {
                const ok = await AW.ui.confirm({ text: "Forget the saved key for " + nameOf(row) + "?", ok: "Forget key", danger: true });
                if (!ok) return;
                await AW.request("source-key-forget", { provider: ATTACHED, connection_id: id });
                AW.ui.toast("The key for " + nameOf(row) + " is forgotten.");
                loadKey();
              },
            }),
          );
        keyBox.append(el("div", { class: "accounts-head-row" }, line.el, actions), errors);
        return;
      }
      if (auth === "none") {
        keyBox.append(el("p", { class: "muted", text: "Not needed for this server." }));
        return;
      }
      const input = el("input", { type: "password", attrs: { autocomplete: "off", spellcheck: "false" } });
      const message = el("p", { class: "hint", role: "status", hidden: true });
      const store = AW.ui.button("Save key", {
        pending: "Saving…",
        async onClick() {
          field.setError(null);
          const key = input.value.trim();
          if (!key) {
            field.setError("Enter the key this server asks for.");
            input.focus();
            return;
          }
          try {
            const answer = await AW.request("source-key-save", { provider: ATTACHED, connection_id: id, key });
            input.value = "";
            AW.ui.toast(answer.message || "Key saved.", { kind: "ok" });
            loadKey();
          } catch (thrown) {
            field.setError(AW.asError(thrown).message);
            input.focus();
          }
        },
      });
      const hint = auth === "required" || auth === "rejected" ? "This server asks for a key. It is kept in your operating system's credential vault." : "Only needed when the server asks for one.";
      const field = AW.ui.field("API key for this server", el("div", { class: "inputrow" }, input, store), { id: "accounts-key-" + id, hint });
      input.addEventListener("keydown", (event) => event.key === "Enter" && store.click());
      keyBox.append(field.el, message);
    }

    async function loadKey() {
      const ticketNo = keyTicket.next();
      try {
        const answer = await AW.request("source-credential", { provider: ATTACHED, connection_id: id });
        if (!keyTicket.current(ticketNo)) return;
        credential = answer;
      } catch {
        if (!keyTicket.current(ticketNo)) return;
        credential = { ready: true, source: "none" };
      }
      keyBox.dataset.signature = "";
      paintKey();
    }

    async function browseModels() {
      const picked = await AW.modelPicker.open({ provider: ATTACHED, connection_id: id, name: nameOf(row), current: modelSelect.hidden ? modelInput.value.trim() : modelSelect.value });
      if (!picked) return;
      if (!modelSelect.hidden && ![...modelSelect.options].some((node) => node.value === picked)) modelSelect.prepend(el("option", { value: picked, text: picked }));
      if (modelSelect.hidden) modelInput.value = picked;
      else modelSelect.value = picked;
    }

    async function saveRow() {
      AW.ui.clearError(saveErrors);
      nameField.setError(null);
      contextError.hidden = true;
      sizeInput.removeAttribute("aria-invalid");
      const model = (modelSelect.hidden ? modelInput.value : modelSelect.value).trim();
      const payload = { provider: ATTACHED, connection_id: id, name: nameInput.value.trim(), remote_model: model, endpoint: row.endpoint, harness: row.harness || "OMP", auth_provider: row.auth_provider || "" };
      if (mine.checked) payload.context_window = sizeInput.value.trim();
      else if (row.context_source === "user") payload.context_window = "";
      if (mine.checked && !payload.context_window) {
        contextError.hidden = false;
        contextError.textContent = "Enter a context size between 1,024 and 10,000,000 tokens.";
        sizeInput.setAttribute("aria-invalid", "true");
        sizeInput.setAttribute("aria-describedby", contextError.id);
        sizeInput.focus();
        return;
      }
      try {
        row = await AW.request("account-save", payload);
      } catch (thrown) {
        const error = AW.asError(thrown);
        if (/context size/i.test(error.message) && mine.checked) {
          contextError.hidden = false;
          contextError.textContent = error.message;
          sizeInput.setAttribute("aria-invalid", "true");
          sizeInput.setAttribute("aria-describedby", contextError.id);
          sizeInput.focus();
        } else AW.ui.showError(saveErrors, error);
        return;
      }
      AW.ui.toast("Saved " + nameOf(row) + ".", { kind: "ok" });
      await S().refreshAccounts();
      paint();
    }

    paint();
    loadKey();
    return { update: paint };
  }

  async function reconnectRow(row, origin) {
    const machine = S().machineName(row.host);
    const forward = forwardOf(row);
    const managed = (forward && forward.managed) || ((row.context || row.host) && !row.remote_port);
    if (managed) await AW.jobs.run("machine-connect", { host: row.host }, { title: "Connecting to " + machine, origin, key: "machine-connect:" + row.host });
    else {
      const data = { host: row.host, port: row.remote_port || (forward && forward.remote_port) };
      if (row.remote_model) data.model = row.remote_model;
      await AW.jobs.run("machine-attach", data, { title: "Connecting to " + machine, origin, key: "machine-attach:" + row.host + ":" + data.port });
    }
    await Promise.all([AW.store.refresh("forwards", { force: true }).catch(() => {}), S().refreshAccounts()]);
    AW.ui.toast("The tunnel to " + machine + " is open.", { kind: "ok" });
  }

  function foundDetail(item, heading, body) {
    const endpoint = item.endpoint;
    heading.textContent = (endpoint.label || "Server") + " on this computer";
    const head = statusHead(null);
    head.line.set("ok", "Found, not attached", AW.state.scan && AW.state.scan.received ? "checked " + AW.fmt.ago(AW.state.scan.received) : "");
    const models = S().sortModels(endpoint.models);
    const loaded = models.filter((m) => m.loaded === true).map((m) => m.id);
    const pairs = [
      ["Address", el("span", { class: "mono", text: endpoint.base || "" })],
      ["Server", endpoint.label || endpoint.kind || ""],
      ["Models", models.length ? S().countText(models.length, "model", "models") + (loaded.length ? " · loaded: " + loaded.join(", ") : "") : "None listed"],
    ];
    if (endpoint.auth === "required") pairs.push(["Key", "This server asks for a key."]);
    const warnings = (endpoint.warnings || []).map((line) => AW.ui.notice("warn", line));
    const attachButton = AW.ui.button("Attach…", { kind: "primary", onClick: () => attachFlow({ port: endpoint.port }) });
    body.append(head.el, el("div", { class: "accounts-block" }, AW.ui.kv(pairs)), ...warnings, worksBlock(endpoint.compat).el, el("div", { class: "accounts-footer" }, el("div", { class: "actions" }, el("span", { class: "accounts-actions-space" }), attachButton)));
    return {
      update() {
        head.line.set("ok", "Found, not attached", AW.state.scan && AW.state.scan.received ? "checked " + AW.fmt.ago(AW.state.scan.received) : "");
      },
    };
  }

  function hostedDetail(item, heading, body) {
    let row = item.row || null;
    heading.textContent = row ? nameOf(row) : "New hosted API";
    const formHost = el("div");
    const works = worksBlock(row ? row.compat : null);
    const foot = el("div");
    body.append(formHost, works.el, foot);
    works.el.hidden = !row;
    if (row) foot.append(footer(row));
    const detail = {
      update() {
        if (!row) return;
        row = rowOf(row.connection_id) || row;
        heading.textContent = nameOf(row);
        works.paint(row.compat);
      },
      destroy: () => form.destroy(),
    };
    const form = AW.sourceForms.hosted(formHost, {
      connection_id: row ? row.connection_id : undefined,
      onSaved(saved) {
        const first = !row;
        row = rowOf(saved.connection_id) || saved;
        if (first) {
          detail.key = saved.connection_id;
          view.selected = saved.connection_id;
          foot.replaceChildren(footer(row));
          works.el.hidden = false;
          paintList();
        }
        detail.update();
      },
    });
    return detail;
  }

  function accountDetail(item, heading, body) {
    let row = item.row || null;
    heading.textContent = row ? nameOf(row) : "New agent account";
    const nameInput = el("input", { type: "text", attrs: { autocomplete: "off", spellcheck: "false", maxlength: "90" } });
    const nameField = AW.ui.field("Connection name", nameInput, { id: "accounts-account-name" });
    const formHost = el("div");
    const errors = el("div", { class: "accounts-errors" });
    const save = AW.ui.button("Save", { kind: "primary", pending: "Saving…", onClick: saveAccount });
    const foot = el("div");
    body.append(nameField.el, formHost, el("div", { class: "actions" }, save), errors, foot);
    if (row) {
      nameInput.value = row.name || "";
      foot.append(footer(row));
    }
    const form = AW.sourceForms.account(formHost, {
      harness: row ? row.harness : AW.form.get().harness,
      auth_provider: row ? row.auth_provider : "",
      onChange: (value) => (nameInput.placeholder = value.harness + " account"),
    });
    nameInput.placeholder = form.value().harness + " account";
    const detail = {
      update() {
        if (!row) return;
        row = rowOf(row.connection_id) || row;
        heading.textContent = nameOf(row);
      },
      destroy: () => form.destroy(),
    };

    async function saveAccount() {
      AW.ui.clearError(errors);
      const value = form.value();
      const payload = { provider: NATIVE, harness: value.harness, auth_provider: value.auth_provider, name: nameInput.value.trim() || value.harness + " account" };
      if (row) payload.connection_id = row.connection_id;
      let saved;
      try {
        saved = await AW.request("account-save", payload);
      } catch (thrown) {
        AW.ui.showError(errors, thrown);
        return;
      }
      AW.ui.toast("Saved " + nameOf(saved) + ".", { kind: "ok" });
      await S().refreshAccounts();
      const first = !row;
      row = rowOf(saved.connection_id) || saved;
      if (!nameInput.value.trim()) nameInput.value = row.name || "";
      if (first) {
        detail.key = saved.connection_id;
        view.selected = saved.connection_id;
        foot.replaceChildren(footer(row));
        paintList();
      }
      detail.update();
    }
    return detail;
  }

  function refreshAll() {
    const quiet = (key, options) => AW.store.refresh(key, options).catch(() => {});
    quiet("accounts");
    if (!AW.state.sourceTable) quiet("sourceTable");
    if (AW.store.age("scan") > 10000) quiet("scan");
    quiet("forwards");
    if (!AW.state.machines) quiet("machines");
    if (!AW.state.config) quiet("config");
  }

  function defaultKey() {
    if (!accounts().length && !scanRows().length) return null;
    const form = AW.form.get();
    if (form.connection_id && rowOf(form.connection_id)) return form.connection_id;
    if (form.provider === LOCAL) return LOCAL;
    return accounts()[0] ? accounts()[0].connection_id : LOCAL;
  }

  function resolveKey(select) {
    if (!select) return null;
    if (select === "local" || select === LOCAL) return LOCAL;
    if (select === NATIVE) {
      const native = accounts().find((row) => row.provider === NATIVE);
      return native ? native.connection_id : "new:account";
    }
    return String(select);
  }

  function show(params = {}) {
    refreshAll();
    view.split.classList.remove("show-detail");
    let key = resolveKey(params.select);
    if (params.add === "hosted") key = "new:hosted";
    else if (params.add === "account") key = "new:account";
    if (key && !itemFor(key)) {
      const wanted = key;
      Promise.all([AW.store.refresh("accounts").catch(() => {}), AW.store.refresh("scan").catch(() => {})]).then(() => {
        if (AW.router.current().id === "accounts" && itemFor(wanted)) choose(wanted, { user: true, focus: false });
      });
      key = null;
    }
    if (!key && view.detail && view.detail.key && itemFor(view.detail.key)) key = view.detail.key;
    if (!key) key = defaultKey();
    view.selected = null;
    if (view.detail && view.detail.destroy) view.detail.destroy();
    view.detail = null;
    choose(key, { user: Boolean(params.select || params.add), focus: false });
    if (params.add === "attach" || params.add === true) attachFlow({});
    view.offs.push(
      AW.store.on(["accounts", "scan", "live", "forwards", "machines", "sourceTable", "unreachable", "config"], () => {
        paintList();
        updateDetail();
      }),
    );
    clearInterval(view.timer);
    view.timer = setInterval(() => {
      paintList();
      updateDetail();
    }, 5000);
  }

  function hide() {
    view.offs.splice(0).forEach((off) => off());
    clearInterval(view.timer);
    view.timer = null;
    const key = view.detail ? view.detail.key : null;
    if (view.detail && view.detail.destroy) view.detail.destroy();
    view.detail = null;
    view.detailPane.replaceChildren();
    view.selected = key && !key.startsWith("new:") ? key : null;
  }

  AW.router.register("accounts", { title: "Connections", mount, show, hide });

  const connectionFor = (id) => {
    const row = rowOf(id);
    if (!row) throw { message: "This connection no longer exists. Refresh the list." };
    return row;
  };

  async function attachAndUse(port) {
    const row = await AW.attach.open({ port: Number(port) || undefined });
    if (row && row.connection_id) await AW.fix.run("source.use", row.connection_id);
    return row;
  }

  AW.fix.register("source.attach", (port) => attachAndUse(port));
  AW.fix.register("source.probe", (port) => attachAndUse(port));
  AW.fix.register("source.scan", () => AW.store.refresh("scan", { force: true }));
  AW.fix.register("source.refresh", async (id) => {
    const result = await refreshConnection(id);
    AW.ui.toast(result.attention ? attentionText(result.name, result.answer.auth) : result.name + " answered.", { kind: result.attention ? "neutral" : "ok" });
    return result;
  });
  AW.fix.register("source.key", async (id, ctx) => {
    const row = connectionFor(id);
    const key = String((ctx && ctx.value) || "").trim();
    if (!key) throw { message: "Enter the provider API key." };
    const answer = await AW.request("source-key-save", { provider: row.provider, connection_id: row.connection_id, key });
    await S().refreshAccounts();
    AW.ui.toast(answer.message || "Key saved.", { kind: "ok" });
    return answer;
  });
  AW.fix.register("source.keypage", async (provider) => {
    const opened = await AW.request("account-key-page", { provider });
    if (!opened) AW.ui.toast(S().label(provider) + " has no key page that Workbench can open.");
    return opened;
  });
  AW.fix.register("source.oauth", async (id, ctx) => {
    const row = connectionFor(id);
    AW.ui.toast("Finish the sign-in in your browser. Workbench waits up to 3 minutes.");
    await AW.jobs.run("account-oauth", row, { title: "Waiting for the OpenRouter sign-in in your browser", origin: ctx && ctx.origin, key: "oauth:" + row.connection_id });
    await S().refreshAccounts();
    AW.ui.toast("The OpenRouter sign-in finished.", { kind: "ok" });
  });
  AW.fix.register("model.browse", async (id) => {
    const form = AW.form.get();
    const row = rowOf(id || form.connection_id);
    const picked = await AW.modelPicker.open({
      provider: row ? row.provider : form.provider,
      connection_id: row ? row.connection_id : form.connection_id,
      endpoint: row ? undefined : form.endpoint,
      name: row ? nameOf(row) : S().label(form.provider),
      current: form.remote_model,
    });
    if (picked) AW.form.set({ remote_model: picked });
    return picked;
  });
  AW.fix.register("account.login", async (agent) => {
    const form = AW.form.get();
    const data = { harness: agent };
    if ((agent === "OMP" || agent === "Hermes") && form.auth_provider) data.auth_provider = form.auth_provider;
    await AW.jobs.run("account-login", data, { title: "Opening " + agent + " sign-in", key: "login:" + agent });
    AW.ui.toast("Continue in the terminal that opened.");
  });

  AW.commands.register({ id: "connections.scan", title: "Scan this computer for model servers", group: "Actions", keywords: "ollama lm studio find", run: () => AW.store.refresh("scan", { force: true }) });
  AW.commands.register({ id: "connections.attach", title: "Attach a running server…", group: "Actions", keywords: "ollama lm studio connect", run: () => AW.attach.open({}) });
  AW.commands.register({ id: "connections.hosted", title: "Add a hosted API…", group: "Actions", keywords: "openai openrouter key", run: () => AW.router.go("accounts", { add: "hosted" }) });
})();
