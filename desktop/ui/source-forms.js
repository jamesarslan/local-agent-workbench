// Source forms (AW.sourceForms): a hosted API, a typed server address, a machine over SSH and an agent's own account (ui.md 5.4, 5.5, 4.2),
// and AW.sources, the small helpers the Connections package shares between its files.
// Beyond ui.md 9.8: every form also takes `onChange(value)`; hosted takes `provider` for a new connection; address takes `onRemote(host)`
// (the guide's "Use a machine over SSH instead") and `address`; ssh takes `onSaved(host)`, `onProbe(result)` and `name`, and its handle
// adds `check()` and, in compact mode, `attach()` (machine-attach as a task, then onAttached).
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const LOCAL = "Managed local";
  const NATIVE = "Native login";
  const ATTACHED = "Attached endpoint";
  const KEY_PAGES = new Set(["OpenAI", "Groq", "DeepSeek", "OpenRouter"]);
  const SUBSCRIPTIONS = [
    { value: "openai-codex-device", label: "ChatGPT device sign-in" },
    { value: "anthropic", label: "Anthropic account" },
  ];
  const LOOPBACK = /^(?:127(?:\.\d{1,3}){3}|localhost|::1|\[::1\])$/i;
  let serial = 0;
  const uid = (prefix) => "accounts-" + prefix + "-" + ++serial;
  const own = (id) => (id && !AW.$(id) ? id : undefined);
  const list = (value) => (Array.isArray(value) ? value : []);
  const text = (value) => (typeof value === "string" ? value : "");

  function table() {
    if (AW.state.sourceTable) return Promise.resolve(AW.state.sourceTable);
    return AW.store.refresh("sourceTable");
  }

  function providerRow(id) {
    return list(AW.state.sourceTable && AW.state.sourceTable.providers).find((row) => row.id === id) || null;
  }

  function label(provider) {
    if (provider === LOCAL) return "Local model";
    if (provider === NATIVE) return "Agent account";
    const row = providerRow(provider);
    return (row && row.label) || provider || "";
  }

  function harnesses() {
    const rows = list(AW.state.sourceTable && AW.state.sourceTable.harnesses).map((row) => row.id);
    if (rows.length) return rows;
    return list(AW.state.config && AW.state.config.agents);
  }

  function sortModels(models) {
    const rank = (m) => (m.loaded === true ? 0 : 1);
    return list(models)
      .filter((m) => m && typeof m.id === "string")
      .slice()
      .sort((a, b) => rank(a) - rank(b));
  }

  function modelText(m) {
    const parts = [m.id];
    if (m.loaded === true) parts.push("loaded");
    else if (m.loaded === false) parts.push("not loaded");
    const context = AW.fmt.context(m.context);
    if (context) parts.push(context + " context");
    return parts.join(" · ");
  }

  function countText(n, one, many) {
    return n === 1 ? "1 " + one : n + " " + many;
  }

  function worksSentence(compat) {
    const rows = Object.entries(compat || {});
    if (!rows.length) return "";
    const yes = rows.filter(([, row]) => row && row.ok).map(([name]) => name);
    const parts = [];
    if (yes.length) parts.push("Works with " + AW.fmt.list(yes) + ".");
    for (const [name, row] of rows) {
      if (row && row.ok === false) parts.push("Not with " + name + ": " + (text(row.reason) || "it cannot use this server."));
      else if (row && row.reason) parts.push("Not confirmed for " + name + ": " + row.reason);
    }
    return parts.join(" ");
  }

  function protocolSentence(protocols) {
    if (!protocols || typeof protocols !== "object") return "";
    const rows = list(AW.state.sourceTable && AW.state.sourceTable.harnesses).filter((row) => !row.native_only);
    const yes = rows.filter((row) => protocols[row.protocol] === true).map((row) => row.id);
    const no = rows.filter((row) => protocols[row.protocol] === false).map((row) => row.id);
    const parts = [];
    if (yes.length) parts.push("Works with " + AW.fmt.list(yes) + ".");
    if (no.length) parts.push("Not with " + AW.fmt.list(no) + ".");
    return parts.join(" ");
  }

  function keyWord(credential) {
    const source = credential && credential.source;
    if (source === "vault") return { mark: "ok", word: "Key saved in the vault", saved: true };
    if (source === "env") return { mark: "ok", word: "Using the key from " + (credential.env || "your environment"), saved: false };
    if (source === "memory") return { mark: "ok", word: "Kept in memory until you quit", saved: true };
    return { mark: "warn", word: "Needs a key", saved: false };
  }

  function hostOf(address) {
    const raw = text(address).trim();
    if (!raw) return "";
    try {
      const url = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : "http://" + raw);
      return url.hostname.replace(/^\[|\]$/g, "");
    } catch {
      return "";
    }
  }

  const sshHost = (host) => (/^(?:[A-Za-z0-9_][\w.-]*@)?[A-Za-z0-9][\w.-]*$/.test(host) ? host : "");

  function machineName(host) {
    const machines = AW.state.machines;
    return (machines && typeof machines === "object" && text(machines[host])) || host;
  }

  function refreshAccounts() {
    if (AW.modelPicker && AW.modelPicker.clear) AW.modelPicker.clear();
    return AW.store.refresh("accounts", { force: true }).catch(() => AW.state.accounts);
  }

  function fieldBlock(labelText, control, { hint, id } = {}) {
    return AW.ui.field(labelText, control, { hint, id });
  }

  function statusLine(className) {
    const mark = AW.ui.mark("idle");
    const word = el("span", { class: "accounts-status-word" });
    const extra = el("span", { class: "accounts-status-extra muted" });
    const node = el("div", { class: "accounts-status " + (className || "") }, mark, el("span", { class: "accounts-status-text" }, word, extra));
    return {
      el: node,
      set(state, line, more) {
        AW.ui.setMark(mark, state);
        if (word.textContent !== line) word.textContent = line || "";
        const tail = more ? " · " + more : "";
        if (extra.textContent !== tail) extra.textContent = tail;
      },
    };
  }

  AW.sources = {
    LOCAL, NATIVE, ATTACHED, KEY_PAGES, table, providerRow, label, harnesses, sortModels, modelText, countText, worksSentence,
    protocolSentence, keyWord, hostOf, sshHost, machineName, refreshAccounts, statusLine,
    isLoopback: (host) => LOOPBACK.test(text(host)),
  };

  function hosted(root, { connection_id, provider, onSaved, onChange, inline = true } = {}) {
    const box = el("div", { class: "accounts-form accounts-hosted" });
    root.append(box);
    const offs = [];
    const credTicket = AW.ticket();
    let row = connection_id ? (AW.state.accounts || []).find((item) => item.connection_id === connection_id) || null : null;
    const fresh = !row;
    let credential = null;
    let destroyed = false;

    const providerSelect = el("select", { id: own("account") });
    const providerField = fieldBlock("Provider", providerSelect);
    const providerNote = el("p", { class: "hint accounts-provider-note", hidden: true });
    providerField.el.append(providerNote);
    const nameInput = el("input", { type: "text", id: own("account-name"), attrs: { autocomplete: "off", spellcheck: "false", maxlength: "90" } });
    const nameField = fieldBlock("Connection name", nameInput);

    const steps = el("ol", { class: "accounts-steps", attrs: { "aria-label": "Steps for a new connection" } });
    const stepRows = ["Save a key", "Choose a default model", "Launch with this"].map((words, index) => {
      const mark = AW.ui.mark("idle");
      const done = el("span", { class: "sr-only" });
      const item = el("li", null, mark, el("span", { class: "accounts-step-n", text: String(index + 1) }), el("span", { text: words }), done);
      steps.append(item);
      return { mark, done };
    });
    steps.hidden = !fresh;

    const keyStatus = statusLine("accounts-key-status");
    if (own("key-status")) keyStatus.el.id = "key-status";
    keyStatus.set("idle", "Checking the key…");
    const keyInput = el("input", { type: "password", id: own("key"), attrs: { autocomplete: "off", spellcheck: "false", placeholder: "Paste the key" } });
    const keyErrors = el("div", { class: "accounts-errors" });
    const keyMessage = el("p", { class: "hint accounts-key-message", role: "status", hidden: true });
    const store = AW.ui.button("Save key", { id: own("store"), pending: "Saving…", onClick: saveKey });
    const keyField = fieldBlock("API key", el("div", { class: "inputrow" }, keyInput, store), { id: keyInput.id || uid("key") });
    keyField.el.append(keyMessage);
    const forget = AW.ui.button("Forget key", { small: true, kind: "danger", pending: "Forgetting…", errorHost: keyErrors, onClick: forgetKey });
    const page = AW.ui.button("Get an API key ↗", { small: true, id: own("manage-key"), errorHost: keyErrors, onClick: openPage });
    const oauth = AW.ui.button("Sign in with OpenRouter ↗", { small: true, id: own("oauth"), errorHost: keyErrors, onClick: signIn });
    const oauthHost = el("div", { class: "accounts-task-host" });
    const keyActions = el("div", { class: "actions" }, forget, page, oauth);

    const modelInput = el("input", { type: "text", id: own("default-model"), attrs: { autocomplete: "off", spellcheck: "false", placeholder: "Model ID" } });
    const browse = AW.ui.button("Browse models", { id: own("choose-default"), pending: "Loading models…", onClick: browseModels });
    const modelField = fieldBlock("Default model", el("div", { class: "inputrow" }, modelInput, browse), { id: modelInput.id || uid("model") });
    const modelErrors = el("div", { class: "accounts-errors" });
    const saveErrors = el("div", { class: "accounts-errors" });
    const save = AW.ui.button("Save", { id: own("save-account"), kind: "primary", pending: "Saving…", onClick: saveAll });

    box.append(
      steps,
      nameField.el,
      providerField.el,
      el("div", { class: "accounts-block" }, el("h3", { class: "group-label", text: "Key" }), keyStatus.el, keyField.el, keyActions, keyErrors, oauthHost),
      el("div", { class: "accounts-block" }, modelField.el, modelErrors),
      saveErrors,
    );
    if (inline) saveErrors.before(el("div", { class: "actions" }, save));

    const current = () => providerSelect.value || (row && row.provider) || "";
    const named = () => nameInput.value.trim();

    function paintProviders() {
      const rows = list(AW.state.sourceTable && AW.state.sourceTable.providers).filter((item) => item.kind === "hosted");
      const chosen = (row && row.provider) || providerSelect.value || provider || (rows[0] && rows[0].id) || "";
      providerSelect.replaceChildren(...rows.map((item) => el("option", { value: item.id, text: item.label })));
      if (chosen && !rows.some((item) => item.id === chosen)) providerSelect.append(el("option", { value: chosen, text: label(chosen) }));
      providerSelect.value = chosen;
      providerSelect.disabled = Boolean(row);
      paintProvider();
    }

    function paintProvider() {
      const id = current();
      const info = providerRow(id);
      const notes = [];
      if (row) notes.push("The provider is fixed once the connection is saved.");
      if (info && info.evidence === "documented") notes.push("Workbench sets up " + info.label + " from its published documentation; this release has not tried it with a real key.");
      providerNote.hidden = !notes.length;
      providerNote.textContent = notes.join(" ");
      page.hidden = !KEY_PAGES.has(id);
      oauth.hidden = id !== "OpenRouter";
      nameInput.placeholder = label(id);
    }

    function paintSteps() {
      const keyed = Boolean(credential && credential.ready);
      const model = Boolean(row && row.remote_model);
      [keyed, model, false].forEach((done, index) => {
        AW.ui.setMark(stepRows[index].mark, done ? "ok" : "idle");
        stepRows[index].done.textContent = done ? ", done" : "";
      });
    }

    function paintKey() {
      const state = keyWord(credential);
      keyStatus.set(state.mark, state.word);
      forget.hidden = !state.saved;
      const env = Boolean(credential && credential.source === "env");
      keyField.el.hidden = env;
      store.hidden = env;
      keyMessage.hidden = !env && !keyMessage.textContent;
      if (env) keyMessage.textContent = "Workbench uses " + (credential.env || "the environment variable") + " while it is set. Remove it from your environment to use a saved key instead.";
      if (env) keyStatus.el.after(keyMessage);
      else keyField.el.append(keyMessage);
      paintSteps();
    }

    function changed() {
      if (onChange) onChange(handle.value());
    }

    async function loadCredential() {
      const mine = credTicket.next();
      if (!row) {
        credential = { ready: false, source: "none" };
        paintKey();
        return;
      }
      try {
        const answer = await AW.request("source-credential", { provider: row.provider, connection_id: row.connection_id });
        if (!credTicket.current(mine) || destroyed) return;
        credential = answer;
        paintKey();
        changed();
      } catch (thrown) {
        if (!credTicket.current(mine) || destroyed) return;
        keyStatus.set("warn", "The key state could not be read");
        AW.ui.showError(keyErrors, thrown, { retry: loadCredential });
      }
    }

    async function ensureSaved() {
      if (row) return row;
      const saved = await AW.request("account-save", { provider: current(), name: named(), remote_model: modelInput.value.trim() });
      row = saved;
      paintProviders();
      await refreshAccounts();
      if (onSaved) onSaved(row);
      return row;
    }

    async function storeKey(key) {
      const saved = await ensureSaved();
      const answer = await AW.request("source-key-save", { provider: saved.provider, connection_id: saved.connection_id, key });
      keyInput.value = "";
      keyMessage.hidden = !answer.message;
      keyMessage.textContent = answer.message || "";
      AW.ui.announce(answer.message || "Key saved.");
      await Promise.all([loadCredential(), refreshAccounts()]);
      if (onSaved) onSaved(row);
      changed();
    }

    async function saveKey() {
      keyField.setError(null);
      AW.ui.clearError(keyErrors);
      const key = keyInput.value.trim();
      if (!key) {
        keyField.setError("Enter the provider API key.");
        keyInput.focus();
        return;
      }
      try {
        await storeKey(key);
      } catch (thrown) {
        keyField.setError(AW.asError(thrown).message);
        keyInput.focus();
      }
    }

    async function forgetKey() {
      if (!row) return;
      const ok = await AW.ui.confirm({ text: "Forget the saved key for " + (row.name || label(row.provider)) + "?", ok: "Forget key", danger: true });
      if (!ok) return;
      await AW.request("source-key-forget", { provider: row.provider, connection_id: row.connection_id });
      keyMessage.hidden = true;
      AW.ui.toast("The key for " + (row.name || label(row.provider)) + " is forgotten.");
      await Promise.all([loadCredential(), refreshAccounts()]);
      changed();
    }

    async function openPage() {
      const opened = await AW.request("account-key-page", { provider: current() });
      if (!opened) AW.ui.toast(label(current()) + " has no key page that Workbench can open.");
    }

    async function signIn() {
      const saved = await ensureSaved();
      const hint = el("p", { class: "hint", text: "Finish the sign-in in your browser. Workbench waits up to 3 minutes." });
      const job = AW.jobs.run("account-oauth", saved, { title: "Waiting for the OpenRouter sign-in in your browser", origin: oauth, key: "oauth:" + saved.connection_id });
      const record = AW.jobs.find("oauth:" + saved.connection_id);
      const task = record ? AW.ui.taskRow(record.handle, { compact: true }) : null;
      oauthHost.replaceChildren(hint, task ? task.el : "");
      try {
        await job;
        AW.ui.toast("The OpenRouter sign-in finished.", { kind: "ok" });
      } finally {
        if (task) task.destroy();
        hint.remove();
        await Promise.all([loadCredential(), refreshAccounts()]);
        changed();
      }
    }

    async function browseModels() {
      AW.ui.clearError(modelErrors);
      modelField.setError(null);
      if (!row || (credential && !credential.ready)) {
        modelField.setError("Save a key first, then browse models.");
        if (!keyField.el.hidden) keyInput.focus();
        return;
      }
      const saved = await ensureSaved();
      const picked = await AW.modelPicker.open({ provider: saved.provider, connection_id: saved.connection_id, name: saved.name || label(saved.provider), current: modelInput.value.trim() });
      if (!picked) return;
      modelInput.value = picked;
      row = await AW.request("account-save", { provider: saved.provider, connection_id: saved.connection_id, name: named(), remote_model: picked });
      AW.ui.toast("Default model saved for " + (row.name || label(row.provider)) + ".");
      await refreshAccounts();
      paintSteps();
      if (onSaved) onSaved(row);
      changed();
    }

    async function saveAll() {
      AW.ui.clearError(saveErrors);
      try {
        await saveRow();
      } catch (thrown) {
        AW.ui.showError(saveErrors, thrown);
        return;
      }
      AW.ui.toast("Saved " + (row.name || label(row.provider)) + ".", { kind: "ok" });
      await settle();
    }

    async function saveRow() {
      const payload = { provider: current(), name: named(), remote_model: modelInput.value.trim() };
      if (row) payload.connection_id = row.connection_id;
      row = await AW.request("account-save", payload);
      paintProviders();
    }

    async function settle() {
      await refreshAccounts();
      paintSteps();
      if (!credential || credential.source === "none") await loadCredential();
      if (onSaved) onSaved(row);
      changed();
    }

    providerSelect.addEventListener("change", () => {
      paintProvider();
      changed();
    });
    nameInput.addEventListener("input", changed);
    modelInput.addEventListener("input", changed);
    keyInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") store.click();
    });

    if (row) {
      nameInput.value = row.name || "";
      modelInput.value = row.remote_model || "";
    }
    paintProviders();
    table().then(() => !destroyed && paintProviders(), () => {});
    loadCredential();

    const handle = {
      value: () => ({ provider: current(), connection_id: row ? row.connection_id : "", name: named() || (row && row.name) || "", remote_model: modelInput.value.trim(), ready: handle.ready() }),
      ready: () => Boolean(row && row.remote_model && credential && credential.ready),
      canSave: () => Boolean(modelInput.value.trim() && ((credential && credential.ready) || keyInput.value.trim())),
      async save() {
        keyField.setError(null);
        modelField.setError(null);
        const key = keyInput.value.trim();
        if (key) await storeKey(key);
        if (!credential || !credential.ready) throw { message: "Save a key for " + label(current()) + " first." };
        if (!modelInput.value.trim()) throw { message: "Enter the model ID to use, or choose Browse models." };
        await saveRow();
        await settle();
        return handle.value();
      },
      destroy() {
        destroyed = true;
        offs.forEach((off) => off());
        box.remove();
      },
    };
    return handle;
  }

  function address(root, { onFound, onRemote, onChange, address: initial } = {}) {
    const box = el("div", { class: "accounts-form accounts-address" });
    root.append(box);
    const ticket = AW.ticket();
    let found = null;
    let destroyed = false;
    const input = el("input", { type: "text", class: "mono", attrs: { autocomplete: "off", spellcheck: "false", placeholder: "127.0.0.1:8080" } });
    const key = el("input", { type: "password", attrs: { autocomplete: "off", spellcheck: "false" } });
    const check = AW.ui.button("Check address", { pending: "Checking…", onClick: run });
    const checkLabel = check.querySelector(".btn-label");
    const addressField = fieldBlock("Server address", el("div", { class: "inputrow" }, input, check), { id: uid("address") });
    const keyField = fieldBlock("API key for this server", key, { id: uid("server-key"), hint: "This server asks for a key. It is kept in your operating system's credential vault." });
    keyField.el.hidden = true;
    const after = el("div", { class: "actions accounts-address-after", hidden: true });
    box.append(addressField.el, keyField.el, after);
    if (initial) input.value = initial;

    function needKey(rejected) {
      keyField.el.hidden = false;
      checkLabel.textContent = "Check with key";
      keyField.setError(rejected ? "That key was not accepted. Paste the right key, then check again." : null);
    }

    function remoteButton(host) {
      after.replaceChildren();
      after.hidden = true;
      if (!host || AW.sources.isLoopback(host)) return;
      const target = sshHost(host);
      const words = onRemote ? "Use a machine over SSH instead" : "Add it as a machine over SSH";
      after.append(
        AW.ui.button(words, {
          small: true,
          onClick: () => (onRemote ? onRemote(target) : AW.router.go("machines", { add: true, host: target || undefined })),
        }),
      );
      after.hidden = false;
    }

    async function run() {
      const typed = input.value.trim();
      addressField.setError(null);
      remoteButton("");
      if (!typed) {
        addressField.setError("Enter the server address, for example http://127.0.0.1:11434/v1.");
        input.focus();
        return;
      }
      const mine = ticket.next();
      const data = { address: typed };
      if (!keyField.el.hidden && key.value.trim()) data.key = key.value.trim();
      try {
        const answer = await AW.request("endpoint-probe", data);
        if (!ticket.current(mine) || destroyed) return;
        if (answer.auth === "required") {
          needKey(false);
          if (data.key) keyField.setError("This server still asks for a key. Paste the right key, then check again.");
          key.focus();
          found = null;
        } else if (answer.auth === "rejected") {
          needKey(true);
          key.focus();
          found = null;
        } else {
          keyField.setError(null);
          found = answer;
          if (onFound) onFound(answer, { address: typed, key: data.key || "" });
        }
        if (onChange) onChange(handle.value());
      } catch (thrown) {
        if (!ticket.current(mine) || destroyed) return;
        found = null;
        addressField.setError(AW.asError(thrown).message);
        remoteButton(hostOf(typed));
        input.focus();
        if (onChange) onChange(handle.value());
      }
    }

    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") check.click();
    });
    key.addEventListener("keydown", (event) => {
      if (event.key === "Enter") check.click();
    });

    const handle = {
      value: () => ({ address: input.value.trim(), key: keyField.el.hidden ? "" : key.value.trim(), found }),
      check: run,
      focus: () => input.focus(),
      destroy() {
        destroyed = true;
        box.remove();
      },
    };
    return handle;
  }

  function probeLines(result) {
    const system = [text(result.system), text(result.arch)].filter(Boolean).join(" ");
    const head = ["Connected", system, Number(result.ram_gib) > 0 ? Number(result.ram_gib).toFixed(1) + " GiB RAM" : ""].filter(Boolean).join(" · ");
    const tail = [];
    if (result.unified) tail.push("Unified memory");
    else if (text(result.gpu_vendor) && Number(result.gpu_gib) > 0) tail.push(Number(result.gpu_gib).toFixed(1) + " GiB GPU memory");
    if (result.installed) tail.push("Managed runtime installed");
    return { head, tail };
  }

  function ssh(root, { host: initial, compact = false, name: initialName, onAttached, onSaved, onProbe, onChange } = {}) {
    const box = el("div", { class: "accounts-form accounts-ssh" + (compact ? " compact" : "") });
    root.append(box);
    const ticket = AW.ticket();
    const discoverTicket = AW.ticket();
    let probe = null;
    let probedAt = 0;
    let servers = null;
    let destroyed = false;
    let agoTimer = null;

    const hostInput = el("input", { type: "text", class: "mono", attrs: { autocomplete: "off", spellcheck: "false", placeholder: "user@hostname" } });
    hostInput.value = text(initial);
    const status = statusLine("accounts-probe");
    status.set("idle", "Not checked yet");
    const errors = el("div", { class: "accounts-errors" });
    const firewall = el("div", { hidden: true });
    const check = AW.ui.button("Check connection", { pending: "Checking…", onClick: () => runProbe() });

    const host = () => hostInput.value.trim();
    const label = () => (initialName && host() === text(initial) ? initialName : machineName(host()));
    const changed = () => onChange && onChange(handle.value());

    let hostField;
    let nameInput = null;
    let saveErrors = null;
    if (compact) {
      hostField = fieldBlock("SSH alias or user@host", el("div", { class: "inputrow" }, hostInput, check), { id: uid("ssh-host") });
      box.append(hostField.el, status.el, errors, firewall);
    } else {
      nameInput = el("input", { type: "text", attrs: { autocomplete: "off", spellcheck: "false", maxlength: "80" } });
      nameInput.value = text(initialName) || machineName(text(initial));
      hostField = fieldBlock("SSH", hostInput, { id: uid("ssh-host") });
      const nameField = fieldBlock("Name", nameInput, { id: uid("ssh-name") });
      saveErrors = el("div", { class: "accounts-errors" });
      const save = AW.ui.button("Save", { pending: "Saving…", onClick: saveMachine });
      box.append(
        el("div", { class: "accounts-probe-head" }, status.el, check),
        errors,
        firewall,
        el("div", { class: "accounts-ssh-fields" }, nameField.el, hostField.el, el("div", { class: "accounts-ssh-save" }, save)),
        saveErrors,
      );
    }

    const serverBox = el("div", { class: "accounts-servers", hidden: true });
    if (compact) box.append(serverBox);

    function paintProbe() {
      if (!probe) return;
      const lines = probeLines(probe);
      const when = probedAt ? "checked " + AW.fmt.ago(probedAt) : "";
      status.set("ok", lines.head, [...lines.tail, when].filter(Boolean).join(" · "));
    }

    function paintFirewall(command) {
      firewall.replaceChildren();
      firewall.hidden = !command;
      if (!command) return;
      firewall.append(
        el(
          "details",
          { class: "accounts-firewall" },
          el("summary", { text: "Firewall rule for this host" }),
          el("pre", { class: "mono", text: command }),
          AW.ui.button("Copy", {
            small: true,
            async onClick() {
              const copied = await AW.clipboard.write(command);
              AW.ui.toast(copied ? "Copied." : "The command could not be copied.", copied ? {} : { kind: "danger" });
            },
          }),
        ),
      );
    }

    async function runProbe() {
      AW.ui.clearError(errors);
      hostField.setError(null);
      const target = host();
      if (!target) {
        hostField.setError("Use an SSH config alias or user@hostname.");
        hostInput.focus();
        return null;
      }
      const mine = ticket.next();
      status.set("busy", "Checking " + target + "…");
      try {
        const result = await AW.request("machine-probe", { host: target });
        if (!ticket.current(mine) || destroyed) return null;
        probe = result;
        probedAt = Date.now();
        paintProbe();
        paintFirewall(text(result.firewall));
        clearInterval(agoTimer);
        agoTimer = setInterval(paintProbe, 5000);
        if (onProbe) onProbe(result);
        if (compact) discover();
        changed();
        return result;
      } catch (thrown) {
        if (!ticket.current(mine) || destroyed) return null;
        probe = null;
        status.set("down", "Not connected");
        AW.ui.showError(errors, thrown, { retry: runProbe });
        changed();
        return null;
      }
    }

    async function saveMachine() {
      AW.ui.clearError(saveErrors);
      hostField.setError(null);
      try {
        const saved = await AW.request("machine-save", { name: nameInput.value.trim(), host: host() });
        hostInput.value = saved;
        await AW.store.refresh("machines", { force: true }).catch(() => {});
        AW.ui.toast("Saved " + (nameInput.value.trim() || saved) + ".", { kind: "ok" });
        if (onSaved) onSaved(saved);
        changed();
      } catch (thrown) {
        hostField.setError(AW.asError(thrown).message);
        hostInput.focus();
      }
    }

    const radioName = uid("server");
    const modelSelect = el("select");
    const modelInput = el("input", { type: "text", attrs: { autocomplete: "off", spellcheck: "false", placeholder: "Model ID" } });
    const modelField = fieldBlock("Model", el("div", null, modelSelect, modelInput), { id: uid("ssh-model") });
    const keyInput = el("input", { type: "password", attrs: { autocomplete: "off", spellcheck: "false" } });
    const keyField = fieldBlock("API key for this server", keyInput, { id: uid("ssh-key"), hint: "This server asks for a key. It is kept in your operating system's credential vault." });
    const serverList = el("div", { class: "accounts-server-list", role: "radiogroup", attrs: { "aria-label": "Servers found" } });
    const works = el("p", { class: "hint accounts-works-line" });
    const look = AW.ui.button("Look again", { small: true, pending: "Looking…", onClick: () => discover() });
    const attachErrors = el("div", { class: "accounts-errors" });

    function chosen() {
      const radio = serverList.querySelector("input:checked");
      return radio && servers ? servers[Number(radio.value)] || null : null;
    }

    function paintChoice() {
      const server = chosen();
      const models = server ? list(server.models) : [];
      modelSelect.replaceChildren(...models.map((m) => el("option", { value: m.id, text: modelText(m) })));
      modelSelect.hidden = !models.length;
      modelInput.hidden = models.length > 0;
      modelField.el.hidden = !server;
      keyField.el.hidden = !(server && server.needs_key);
      works.textContent = server ? protocolSentence(server.protocols) : "";
      works.hidden = !works.textContent;
      changed();
    }

    async function discover() {
      const target = host();
      if (!target) return;
      const mine = discoverTicket.next();
      serverBox.hidden = false;
      serverBox.replaceChildren(el("h3", { class: "group-label", text: "Servers running there" }), AW.ui.skeleton(2, "servers on " + label()));
      try {
        const answer = await AW.request("machine-discover", { host: target });
        if (!discoverTicket.current(mine) || destroyed) return;
        servers = list(answer && answer.endpoints);
        serverList.replaceChildren();
        servers.forEach((server, index) => {
          const radio = el("input", { type: "radio", value: String(index), checked: index === 0, attrs: { name: radioName } });
          radio.addEventListener("change", paintChoice);
          const n = list(server.models).length;
          const words = [server.label || server.kind || "Server", "port " + server.port, server.needs_key ? "needs a key" : countText(n, "model", "models")].join(" · ");
          serverList.append(el("label", { class: "accounts-radio" }, radio, el("span", { text: words })));
        });
        const head = el("div", { class: "accounts-group-head" }, el("h3", { class: "group-label", text: "Servers running there" }), look);
        if (!servers.length) {
          serverBox.replaceChildren(
            head,
            el("p", { class: "muted", text: "No model server answered on " + label() + ". Start one there and choose Look again. To install Workbench's own model on it, finish setup and open Machines." }),
          );
        } else serverBox.replaceChildren(head, serverList, modelField.el, keyField.el, works, attachErrors);
        paintChoice();
      } catch (thrown) {
        if (!discoverTicket.current(mine) || destroyed) return;
        servers = null;
        const notice = el("div");
        serverBox.replaceChildren(el("h3", { class: "group-label", text: "Servers running there" }), notice);
        AW.ui.showError(notice, thrown, { retry: discover });
        changed();
      }
    }

    async function attach() {
      AW.ui.clearError(attachErrors);
      const server = chosen();
      if (!server) throw { message: "Check the connection and choose a server first." };
      const model = (modelSelect.hidden ? modelInput.value : modelSelect.value).trim();
      const data = { host: host(), port: server.port };
      if (model) data.model = model;
      if (!keyField.el.hidden && keyInput.value.trim()) data.key = keyInput.value.trim();
      if (!(AW.state.machines && Object.hasOwn(AW.state.machines, host()))) {
        await AW.request("machine-save", { name: label(), host: host() });
        AW.store.refresh("machines", { force: true }).catch(() => {});
      }
      try {
        const result = await AW.jobs.run("machine-attach", data, { title: "Connecting to " + label(), key: "machine-attach:" + host() + ":" + server.port });
        if (result && result.needs_key && !data.key) {
          server.needs_key = true;
          paintChoice();
          keyField.setError("This server needs an API key. Paste its key and attach again.");
          keyInput.focus();
          return null;
        }
        await refreshAccounts();
        AW.store.refresh("forwards", { force: true }).catch(() => {});
        if (onAttached) onAttached(result);
        return result;
      } catch (thrown) {
        AW.ui.showError(attachErrors, thrown);
        throw thrown;
      }
    }

    hostInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && compact) check.click();
    });
    hostInput.addEventListener("input", () => {
      hostField.setError(null);
      changed();
    });
    modelSelect.addEventListener("change", changed);
    modelInput.addEventListener("input", changed);

    const handle = {
      value() {
        const server = chosen();
        return {
          host: host(),
          name: nameInput ? nameInput.value.trim() : label(),
          probe,
          server,
          port: server ? server.port : null,
          model: server ? (modelSelect.hidden ? modelInput.value : modelSelect.value).trim() : "",
          key: !keyField.el.hidden ? keyInput.value.trim() : "",
        };
      },
      check: runProbe,
      attach,
      destroy() {
        destroyed = true;
        clearInterval(agoTimer);
        box.remove();
      },
    };
    return handle;
  }

  function account(root, { harness, auth_provider, onChange } = {}) {
    const box = el("div", { class: "accounts-form accounts-account" });
    root.append(box);
    const ticket = AW.ticket();
    let destroyed = false;
    const agentSelect = el("select", { id: own("login-agent") });
    const agentField = fieldBlock("Account for", agentSelect);
    const subSelect = el("select", { id: own("auth-provider") }, SUBSCRIPTIONS.map((item) => el("option", { value: item.value, text: item.label })));
    const subField = fieldBlock("Subscription", subSelect);
    if (auth_provider && SUBSCRIPTIONS.some((item) => item.value === auth_provider)) subSelect.value = auth_provider;
    const info = el("p", { class: "accounts-account-info", id: own("subscription-info"), role: "status" });
    const missing = el("p", { class: "hint", hidden: true });
    const result = el("p", { class: "hint", role: "status", hidden: true });
    const errors = el("div", { class: "accounts-errors" });
    const login = AW.ui.button("Open sign-in ↗", { id: own("native-login"), errorHost: errors, onClick: signIn });
    box.append(agentField.el, subField.el, info, missing, el("div", { class: "actions" }, login), result, errors);

    function fill() {
      const names = harnesses();
      const chosen = agentSelect.value || harness || names[0] || "";
      agentSelect.replaceChildren(...names.map((name) => el("option", { value: name, text: name })));
      if (chosen && !names.includes(chosen)) agentSelect.append(el("option", { value: chosen, text: chosen }));
      agentSelect.value = chosen;
      paint();
    }

    function paint() {
      const agent = agentSelect.value;
      subField.el.hidden = !(agent === "OMP" || agent === "Hermes");
      login.querySelector(".btn-label").textContent = "Open " + agent + " sign-in ↗";
      const present = (AW.state.config && AW.state.config.cli_present) || {};
      missing.hidden = present[agent] !== false;
      missing.textContent = agent + " is not installed on this computer. Install it under Advanced → Setup first.";
      result.hidden = true;
      const mine = ticket.next();
      if (agent !== "Codex") {
        info.textContent = agent + " uses its own sign-in. It asks you to sign in if it needs to.";
        return;
      }
      const known = typeof AW.state.subscription === "string" ? AW.state.subscription : "";
      info.textContent = known || "Checking the ChatGPT sign-in for Codex…";
      AW.store.refresh("subscription").then(
        (sentence) => {
          if (ticket.current(mine) && !destroyed && typeof sentence === "string") info.textContent = sentence;
        },
        () => {
          if (ticket.current(mine) && !destroyed && !known) info.textContent = "Codex uses its own sign-in. It asks you to sign in if it needs to.";
        },
      );
    }

    async function signIn() {
      const agent = agentSelect.value;
      const data = { harness: agent };
      if (!subField.el.hidden) data.auth_provider = subSelect.value;
      await AW.jobs.run("account-login", data, { title: "Opening " + agent + " sign-in", origin: login, key: "login:" + agent });
      result.hidden = false;
      result.textContent = "Continue in the terminal that opened.";
      AW.ui.announce(result.textContent);
    }

    agentSelect.addEventListener("change", () => {
      paint();
      if (onChange) onChange(handle.value());
    });
    subSelect.addEventListener("change", () => onChange && onChange(handle.value()));
    fill();
    table().then(() => !destroyed && fill(), () => {});

    const handle = {
      value: () => ({ harness: agentSelect.value, auth_provider: subField.el.hidden ? "" : subSelect.value }),
      destroy() {
        destroyed = true;
        box.remove();
      },
    };
    return handle;
  }

  AW.sourceForms = { hosted, address, ssh, account };
})();
