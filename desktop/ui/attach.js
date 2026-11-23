// Attach a running server dialog (AW.attach, ui.md 5.4): servers found on this computer or on a machine over SSH, or a typed address.
// open({port}) preselects the scanned server on that port, or checks 127.0.0.1:{port} when the scan did not list it.
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const ticket = AW.ticket();
  let serial = 0;

  function open({ address: typed, host, port } = {}) {
    const dialog = AW.ui.dialog("attach-dialog");
    const body = AW.$("attach-dialog-body");
    for (const old of dialog.el.querySelectorAll(":scope > .dialog-actions")) old.remove();
    const machine = typeof host === "string" && host.trim() ? host.trim() : "";
    const label = () => AW.sources.machineName(machine);
    const group = "attach-server-" + ++serial;
    const mine = ticket.next();
    const live = () => ticket.current(mine) && dialog.el.open;
    let rows = [];
    let extra = null;
    let address = null;
    let nameEdited = false;
    let result = null;

    const listHost = el("div", { class: "accounts-server-list", role: "radiogroup", attrs: { "aria-label": machine ? "Servers found on " + label() : "Servers found on this computer" } });
    const hints = el("div", { class: "stack accounts-hints" });
    const again = AW.ui.button(machine ? "Look again" : "Scan again", { small: true, pending: machine ? "Looking…" : "Scanning…", onClick: () => load(true) });
    const head = el("div", { class: "accounts-group-head" }, el("h3", { class: "group-label", text: machine ? "Found on " + label() : "Found on this computer" }), again);

    const nameInput = el("input", { type: "text", attrs: { autocomplete: "off", spellcheck: "false", maxlength: "90" } });
    const nameField = AW.ui.field("Name", nameInput, { id: "attach-name-" + serial });
    const modelSelect = el("select");
    const modelInput = el("input", { type: "text", attrs: { autocomplete: "off", spellcheck: "false", placeholder: "Model ID" } });
    const modelField = AW.ui.field("Model", el("div", null, modelSelect, modelInput), { id: "attach-model-" + serial });
    const keyInput = el("input", { type: "password", attrs: { autocomplete: "off", spellcheck: "false" } });
    const keyCheck = AW.ui.button("Check with key", { small: true, pending: "Checking…", onClick: checkWithKey });
    const keyField = AW.ui.field("API key for this server", el("div", { class: "inputrow" }, keyInput, keyCheck), {
      id: "attach-key-" + serial,
      hint: "This server asks for a key. It is kept in your operating system's credential vault.",
    });
    const works = el("p", { class: "accounts-works-line" });
    const warnings = el("div", { class: "stack" });
    const choice = el("div", { class: "stack accounts-attach-choice", hidden: true }, nameField.el, modelField.el, keyField.el, works);
    const addressHost = el("div", { class: "accounts-attach-address" });
    const errors = el("div", { class: "accounts-errors" });
    const cancel = AW.ui.button("Cancel", { onClick: () => dialog.close(null) });
    const attach = AW.ui.button("Attach", { kind: "primary", pending: machine ? "Connecting…" : "Attaching…", onClick: run });
    const actions = el("div", { class: "dialog-actions" }, cancel, el("span", { class: "accounts-actions-space" }), attach);

    body.replaceChildren(head, listHost, hints);
    if (!machine) {
      body.append(el("h3", { class: "group-label accounts-or", text: "Or an address on this computer" }), addressHost);
      address = AW.sourceForms.address(addressHost, { onFound: found, address: typed || (port ? "127.0.0.1:" + port : "") });
    }
    body.append(choice, warnings, errors);
    dialog.el.append(actions);

    function chosen() {
      const radio = listHost.querySelector("input:checked");
      if (!radio) return null;
      return radio.value === "typed" ? extra : rows[Number(radio.value)] || null;
    }

    function defaultName(server) {
      if (!server) return "";
      const existing = server.connection_id ? (AW.state.accounts || []).find((row) => row.connection_id === server.connection_id) : null;
      if (existing && existing.name) return existing.name;
      return (server.label || server.kind || "Server") + (machine ? " on " + label() : "");
    }

    function currentModel() {
      return (modelSelect.hidden ? modelInput.value : modelSelect.value).trim();
    }

    function needsKey(server) {
      if (!server) return false;
      if (machine) return Boolean(server.needs_key);
      return server.auth === "required" || server.auth === "rejected";
    }

    function paint() {
      const server = chosen();
      choice.hidden = !server;
      AW.ui.clearError(errors);
      warnings.replaceChildren();
      if (!server) {
        attach.setAttribute("aria-disabled", "true");
        return;
      }
      const models = AW.sources.sortModels(server.models);
      const existing = server.connection_id ? (AW.state.accounts || []).find((row) => row.connection_id === server.connection_id) || null : null;
      const keep = (paint.server === server && currentModel()) || (existing && existing.remote_model) || "";
      paint.server = server;
      modelSelect.replaceChildren(...models.map((m) => el("option", { value: m.id, text: AW.sources.modelText(m) })));
      if (keep && models.some((m) => m.id === keep)) modelSelect.value = keep;
      modelSelect.hidden = !models.length;
      modelInput.hidden = models.length > 0;
      const keyed = needsKey(server);
      keyField.el.hidden = !keyed;
      keyCheck.hidden = Boolean(machine);
      keyField.setError(!machine && server.auth === "rejected" ? "That key was not accepted. Paste the right key, then check again." : null);
      if (!nameEdited) nameInput.value = existing && existing.name ? existing.name : defaultName(server);
      works.textContent = machine ? AW.sources.protocolSentence(server.protocols) : AW.sources.worksSentence(server.compat);
      works.hidden = !works.textContent;
      for (const line of server.warnings || []) if (typeof line === "string" && line) warnings.append(AW.ui.notice("warn", line));
      const blocked = !machine && server.auth === "required" && !(server.models || []).length;
      if (blocked) attach.setAttribute("aria-disabled", "true");
      else attach.removeAttribute("aria-disabled");
    }

    function radio(value, words, meta, checked) {
      const input = el("input", { type: "radio", value, checked, attrs: { name: group } });
      input.addEventListener("change", paint);
      return el("label", { class: "accounts-radio" }, input, el("span", { class: "accounts-radio-text" }, el("span", { text: words }), meta ? el("span", { class: "accounts-radio-meta", text: meta }) : null));
    }

    function attachedName(server) {
      if (!server.connection_id) return "";
      const row = (AW.state.accounts || []).find((item) => item.connection_id === server.connection_id);
      return row ? "Already attached as " + (row.name || "a connection") : "Already attached";
    }

    function paintList(preferPort) {
      const before = listHost.querySelector("input:checked");
      const keep = before ? before.value : null;
      listHost.replaceChildren();
      rows.forEach((server, index) => {
        const n = (server.models || []).length;
        const words = [server.label || server.kind || "Server", "port " + server.port, needsKey(server) && !n ? "needs a key" : AW.sources.countText(n, "model", "models")].join(" · ");
        const pick = keep !== null ? keep === String(index) : preferPort ? Number(server.port) === Number(preferPort) : index === 0;
        listHost.append(radio(String(index), words, machine ? "" : attachedName(server), pick));
      });
      if (extra) {
        const n = (extra.models || []).length;
        listHost.append(radio("typed", [extra.label || "Server", extra.base, AW.sources.countText(n, "model", "models")].join(" · "), "", keep === "typed" || !listHost.querySelector("input:checked")));
      }
      if (!listHost.querySelector("input:checked") && listHost.querySelector("input")) listHost.querySelector("input").checked = true;
    }

    function found(server, { key }) {
      extra = { ...server, typedKey: key || "" };
      paintList();
      const typed = listHost.querySelector('input[value="typed"]');
      if (typed) typed.checked = true;
      paint();
    }

    async function checkWithKey() {
      const server = chosen();
      if (!server) return;
      keyField.setError(null);
      const key = keyInput.value.trim();
      if (!key) {
        keyField.setError("Paste the key this server asks for.");
        keyInput.focus();
        return;
      }
      try {
        const answer = await AW.request("endpoint-probe", { address: server.base, key });
        if (!live()) return;
        Object.assign(server, answer);
        paint();
        if (answer.auth === "required") keyField.setError("This server still asks for a key. Paste the right key, then check again.");
      } catch (thrown) {
        if (live()) keyField.setError(AW.asError(thrown).message);
      }
    }

    async function load(force) {
      const wanted = port;
      listHost.replaceChildren(AW.ui.skeleton(2, machine ? "servers on " + label() : "servers on this computer"));
      hints.replaceChildren(el("p", { class: "muted", role: "status", text: machine ? "Looking for model servers on " + label() + "…" : "Scanning this computer…" }));
      try {
        if (machine) {
          const answer = await AW.request("machine-discover", { host: machine });
          if (!live()) return;
          rows = (answer && answer.endpoints) || [];
          hints.replaceChildren();
        } else {
          const scan = force || !AW.state.scan || AW.store.age("scan") > 10000 ? await AW.store.refresh("scan", { force: true }) : AW.state.scan;
          if (!live()) return;
          rows = (scan && scan.endpoints) || [];
          hints.replaceChildren(...((scan && scan.hints) || []).filter((hint) => hint && hint.text).map((hint) => el("p", { class: "hint", text: hint.text })));
        }
        paintList(wanted);
        if (!rows.length && !extra) {
          listHost.replaceChildren(
            el(
              "p",
              { class: "muted accounts-none" },
              machine ? "No model server answered on " + label() + "." : "No model server answered on this computer. Start Ollama, or turn on the local server in LM Studio, then scan again.",
            ),
          );
        }
        if (!machine && wanted && !rows.some((server) => Number(server.port) === Number(wanted)) && address) address.check();
        paint();
      } catch (thrown) {
        if (!live()) return;
        listHost.replaceChildren();
        hints.replaceChildren();
        AW.ui.showError(hints, thrown, { retry: () => load(true) });
        paint();
      }
    }

    async function run() {
      AW.ui.clearError(errors);
      nameField.setError(null);
      modelField.setError(null);
      const server = chosen();
      if (!server) return;
      const model = currentModel();
      const name = nameInput.value.trim();
      let key = keyField.el.hidden ? "" : keyInput.value.trim();
      if (!key && server.typedKey) key = server.typedKey;
      if (!model && !(server.models || []).length) {
        modelField.setError("Type the model ID this server should use.");
        modelInput.focus();
        return;
      }
      try {
        if (machine) {
          const data = { host: machine, port: server.port };
          if (model) data.model = model;
          if (name) data.name = name;
          if (key) data.key = key;
          const answer = await AW.jobs.run("machine-attach", data, { title: "Connecting to " + label(), origin: attach, key: "machine-attach:" + machine + ":" + server.port });
          if (!live()) return;
          if (answer && answer.needs_key && !key) {
            server.needs_key = true;
            paint();
            keyField.setError("This server needs an API key. Paste its key and attach again.");
            keyInput.focus();
            return;
          }
          await AW.sources.refreshAccounts();
          AW.store.refresh("forwards", { force: true }).catch(() => {});
          const row = (AW.state.accounts || []).find((item) => item.connection_id === answer.connection_id);
          result = row || { connection_id: answer.connection_id, name: answer.name, provider: AW.sources.ATTACHED, endpoint: answer.endpoint, remote_model: answer.model, host: machine };
          AW.ui.toast((result.name || "The server") + " is attached through " + label() + ".", { kind: "ok" });
        } else {
          const data = { address: server.base };
          if (model) data.model = model;
          if (name) data.name = name;
          if (key) data.key = key;
          const answer = await AW.request("endpoint-attach", data);
          if (!live()) return;
          result = answer.connection;
          const unreachable = { ...(AW.state.unreachable || {}) };
          if (result && unreachable[result.connection_id]) {
            delete unreachable[result.connection_id];
            AW.store.set("unreachable", unreachable);
          }
          await AW.sources.refreshAccounts();
          AW.store.refresh("scan", { force: true }).catch(() => {});
          const lines = [answer.message || (result.name || "The server") + " is attached.", ...(answer.warnings || [])].filter(Boolean);
          AW.ui.toast(lines.join(" "), { kind: "ok", timeout: answer.warnings && answer.warnings.length ? 12000 : 6000 });
        }
        dialog.close(result);
      } catch (thrown) {
        if (live()) AW.ui.showError(errors, thrown);
      }
    }

    nameInput.addEventListener("input", () => (nameEdited = nameInput.value.trim() !== ""));
    keyInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !keyCheck.hidden) keyCheck.click();
    });

    const answer = dialog.opened().then((value) => {
      ticket.next();
      if (address) address.destroy();
      keyInput.value = "";
      if (extra) extra.typedKey = "";
      body.replaceChildren();
      actions.remove();
      return value || null;
    });
    paint();
    dialog.open();
    load(false);
    again.focus();
    return answer;
  }

  AW.attach = { open };
})();
