// Project setup, MCP tools tab: what reaches the agent at launch, the folder's project servers, the agent's own files and the
// Plugins registered on this computer, with the folder review card, the "Value for {VAR}" and "Add a server" dialogs (ui.md 5.3).
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const P = AW.projectSetup;
  const BUILTINS = [
    ["search", "Web search"],
    ["firecrawl", "Page crawling"],
    ["blender", "Blender"],
    ["desktop", "Windows desktop"],
  ];
  const STATUS = { sent: ["Sent", "ok"], off: ["Off", "idle"], skipped: ["Skipped", "warn"] };
  const ORIGIN = { builtin: "built in", registry: "this computer", project: "project" };
  const SECRET_WORDS = { env: "from your environment", vault: "in the vault", missing: "missing" };
  const NAME = /^[A-Za-z0-9_-]{1,48}$/;
  const VARIABLE = /^[A-Z][A-Z0-9_]{0,63}$/;

  let ctx = P.context();
  let parts = null;
  let listing = null;
  let listingError = null;
  let listingFor = "";
  let plugins = null;
  let pluginsError = null;
  let consentFor = "";
  const listTicket = AW.ticket();
  const consentTicket = AW.ticket();
  const pluginTicket = AW.ticket();

  const pluginNames = () => String(AW.form.get().plugins || "").split(",").map((name) => name.trim()).filter(Boolean);

  function statusNode(status) {
    const [word, mark] = STATUS[status] || [String(status || ""), "idle"];
    return el("span", { class: "project-status" }, AW.ui.mark(mark), word);
  }

  function switchFor(label, checked, onChange, { busy = false } = {}) {
    const input = el("input", { type: "checkbox", class: "switch", role: "switch", checked, attrs: { "aria-label": label } });
    if (busy) input.disabled = true;
    input.addEventListener("change", () => onChange(input.checked, input));
    return P.keyed(input, "switch:" + label);
  }

  function toolRow({ name, label, origin, transport, status, summary, reason, control, after }) {
    const node = el(
      "div",
      { class: "project-tool" },
      el("span", { class: "project-tool-name", title: name, text: label || name }),
      el(
        "span",
        { class: "project-tool-kind" },
        el("span", { class: "project-tool-origin", text: ORIGIN[origin] || origin || "" }),
        el("span", { class: "project-tool-transport", text: transport && transport !== "builtin" ? transport : "" }),
      ),
      el("span", { class: "project-tool-status" }, statusNode(status)),
      el("span", { class: "project-tool-summary mono", title: summary || "", text: summary || "" }),
      el("span", { class: "project-tool-switch" }, control || null),
    );
    if (reason) node.append(el("p", { class: "project-row-reason" + (status === "skipped" ? " warn" : ""), text: reason }));
    if (after) node.append(after);
    return node;
  }

  function secretActions(server, { compact = false } = {}) {
    const secrets = server && server.secrets && typeof server.secrets === "object" ? server.secrets : {};
    const names = Object.keys(secrets);
    if (!names.length) return null;
    const line = el("div", { class: "project-vars" });
    if (!compact) line.append(el("span", { class: "project-vars-label", text: "Variables:" }));
    for (const name of names) {
      const state = secrets[name];
      if (compact && state !== "missing") continue;
      const item = el("span", { class: "project-var" }, el("span", { class: "mono", text: name }), " ", el("span", { class: state === "missing" ? "project-var-missing" : "muted", text: SECRET_WORDS[state] || state }));
      if (state === "missing") item.append(P.keyed(AW.ui.button("Add value…", { small: true, onClick: () => openSecret(name) }), "secret:" + server.name + ":" + name));
      else if (state === "vault" && !compact) item.append(P.keyed(AW.ui.button("Forget value", { small: true, onClick: () => forgetSecret(name) }), "forget:" + server.name + ":" + name));
      line.append(item);
    }
    return line.children.length ? line : null;
  }

  function launchRows() {
    const data = ctx.resolve;
    return data && data.mcp && Array.isArray(data.mcp.launch) ? data.mcp.launch : [];
  }

  function paintLaunch() {
    const config = AW.state.config || {};
    const form = AW.form.get();
    const launch = launchRows();
    const find = (origin, name) => launch.find((row) => row.origin === origin && row.name === name) || null;
    const own = [];
    for (const [cap, label] of BUILTINS) {
      if (cap === "desktop" && !config.windows) continue;
      const row = find("builtin", cap);
      own.push(
        toolRow({
          name: cap,
          label,
          origin: "builtin",
          transport: row ? row.transport : "",
          status: row ? row.status : "off",
          summary: "",
          reason: row ? row.reason : "",
          control: switchFor(label, Boolean(form[cap]), (on) => AW.form.set({ [cap]: on })),
        }),
      );
    }
    const registered = plugins ? plugins.map((row) => row.name) : Array.isArray(config.registered_plugins) ? config.registered_plugins : [];
    const chosen = pluginNames();
    for (const name of registered) {
      const row = find("registry", name);
      own.push(
        toolRow({
          name,
          origin: "registry",
          transport: row ? row.transport : "",
          status: row ? row.status : "off",
          summary: row ? row.summary : "",
          reason: row ? row.reason : "",
          control: switchFor(name, chosen.includes(name), (on) => {
            const next = on ? [...new Set([...pluginNames(), name])] : pluginNames().filter((item) => item !== name);
            AW.form.set({ plugins: next.join(",") });
          }),
        }),
      );
    }
    for (const row of launch) {
      if (row.origin === "project" || (row.origin === "builtin" && BUILTINS.some(([cap]) => cap === row.name)) || registered.includes(row.name)) continue;
      own.push(toolRow({ name: row.name, origin: row.origin, transport: row.transport, status: row.status, summary: row.summary, reason: row.reason }));
    }
    P.swap(parts.ownRows, own);
    const unsaved = AW.form.dirty() || !(AW.state.folder && AW.state.folder.known);
    parts.saveState.textContent = "Part of this folder's launch choices. " + (unsaved ? "Not saved yet." : "Saved for this folder.");
    if (unsaved) parts.save.removeAttribute("aria-disabled");
    else parts.save.setAttribute("aria-disabled", "true");

    const servers = listing ? listing.servers || [] : null;
    const project = [];
    const source = servers || launch.filter((row) => row.origin === "project").map((row) => ({ name: row.name, transport: row.transport, summary: row.summary, enabled: row.status !== "off" }));
    for (const server of source) {
      const row = find("project", server.name);
      const status = row ? row.status : server.enabled ? "sent" : "off";
      project.push(
        toolRow({
          name: server.name,
          origin: "project",
          transport: server.transport,
          status,
          summary: server.summary,
          reason: row ? row.reason : "",
          control: switchFor(server.name, Boolean(server.enabled), (on, input) => toggleProject(server.name, on, input), { busy: !listing }),
          after: status === "skipped" ? secretActions(server, { compact: true }) : null,
        }),
      );
    }
    parts.projectLaunch.hidden = !project.length;
    P.swap(parts.projectRows, project);
    const empty = !own.length && !project.length;
    parts.launchEmpty.hidden = !empty;
    const delivery = ctx.resolve && ctx.resolve.mcp ? ctx.resolve.mcp.delivery : "";
    parts.delivery.textContent = delivery || "";
    parts.delivery.hidden = !delivery;
  }

  async function toggleProject(name, on, input) {
    input.disabled = true;
    AW.ui.clearError(parts.projectErrors);
    try {
      listing = await AW.request("project-setup-mcp-toggle", { workspace: ctx.workspace, name, enabled: on });
      paintServers();
      await P.refresh();
    } catch (thrown) {
      input.checked = !on;
      AW.ui.showError(parts.projectErrors, thrown);
    } finally {
      input.disabled = false;
    }
  }

  function serverRow(server) {
    const actions = el(
      "div",
      { class: "project-row-actions" },
      P.keyed(AW.ui.button("Remove…", { small: true, onClick: () => removeServer(server.name) }), "remove:" + server.name),
    );
    const node = el(
      "div",
      { class: "project-server" },
      el("span", { class: "project-tool-name", text: server.name }),
      el(
        "span",
        { class: "project-tool-kind" },
        el("span", { class: "project-tool-transport", text: server.transport || "" }),
        el("span", { class: "project-tool-summary mono", title: server.summary || "", text: server.summary || "" }),
      ),
      actions,
    );
    const vars = secretActions(server);
    if (vars) node.append(vars);
    return node;
  }

  function paintServers() {
    const agent = ctx.harness || "the agent";
    parts.exportButton.querySelector(".btn-label").textContent = "Export to " + agent + "'s project file…";
    if (listingError && !listing) {
      P.loaded(parts.serverRows);
      P.swap(parts.serverRows, AW.ui.errorNotice(listingError, { retry: () => loadListing(true) }));
      parts.serverActions.hidden = true;
      return;
    }
    if (!listing) {
      P.loading(parts.serverRows, "project servers");
      parts.serverActions.hidden = true;
      return;
    }
    P.loaded(parts.serverRows);
    const servers = listing.servers || [];
    parts.serverCount.textContent = servers.length ? String(servers.length) : "";
    P.swap(parts.serverNotices, (listing.errors || []).map((text) => AW.ui.notice("warn", text)));
    if (!servers.length) {
      P.swap(
        parts.serverRows,
        AW.ui.empty("No project servers. Add one, or apply a starter kit.", [
          { label: "Add a server…", run: () => openServer() },
          { label: "Open Starter kit", run: () => P.go("kit") },
        ]),
      );
      parts.serverActions.hidden = true;
      return;
    }
    P.swap(parts.serverRows, servers.map(serverRow));
    parts.serverActions.hidden = false;
  }

  function paintFiles() {
    const data = ctx.resolve;
    const agent = ctx.harness || "The agent";
    parts.filesTitle.textContent = "Files " + agent + " reads by itself";
    if (!data) {
      if (ctx.error) {
        P.loaded(parts.fileRows);
        P.swap(parts.fileRows, AW.ui.errorNotice(ctx.error, { retry: () => P.refresh({ fresh: true }) }));
      } else P.loading(parts.fileRows, "agent files");
      return;
    }
    P.loaded(parts.fileRows);
    const files = data.mcp && Array.isArray(data.mcp.files) ? data.mcp.files : [];
    if (!files.length) {
      P.swap(parts.fileRows, el("p", { class: "project-caption", text: agent + " reads no MCP file of its own in this folder." }));
      return;
    }
    P.swap(
      parts.fileRows,
      files.map((file) =>
        el(
          "div",
          { class: "project-entry" },
          el("span", { class: "project-row-name mono", text: AW.fmt.path(file.path) }),
          el("span", { class: "project-row-chip" }, P.chip(file.status)),
          el("span", { class: "project-row-meta" }),
          el("span"),
          (file.servers || []).length ? el("p", { class: "project-row-desc", text: "Defines " + AW.fmt.list(file.servers) + "." }) : null,
          file.reason ? el("p", { class: "project-row-path", text: file.reason }) : null,
        ),
      ),
    );
  }

  function paintPlugins() {
    if (pluginsError && !plugins) {
      P.loaded(parts.pluginRows);
      P.swap(parts.pluginRows, AW.ui.errorNotice(pluginsError, { retry: () => loadPlugins() }));
      return;
    }
    if (!plugins) {
      P.loading(parts.pluginRows, "registered servers");
      return;
    }
    P.loaded(parts.pluginRows);
    parts.pluginCount.textContent = plugins.length ? String(plugins.length) : "";
    if (!plugins.length) {
      P.swap(parts.pluginRows, el("p", { class: "project-caption project-plugins-empty", text: "No MCP servers are registered on this computer." }));
      return;
    }
    P.swap(
      parts.pluginRows,
      plugins.map((row) => {
        const summary = row.command ? [row.command, ...(row.args || [])].join(" ") : row.url || "";
        const keys = [...(row.env_keys || []), ...(row.header_keys || [])];
        return el(
          "div",
          { class: "project-server" },
          el("span", { class: "project-tool-name", text: row.name }),
          el("span", { class: "project-tool-kind" }, el("span", { class: "project-tool-transport", text: row.transport || "" }), el("span", { class: "project-tool-summary mono", title: summary, text: summary })),
          el("div", { class: "project-row-actions" }, P.keyed(AW.ui.button("Remove…", { small: true, onClick: () => removePlugin(row.name) }), "plugin:" + row.name)),
          keys.length ? el("p", { class: "project-row-reason", text: "Sets " + AW.fmt.list(keys) + "." }) : null,
        );
      }),
    );
  }

  function paint() {
    if (!parts) return;
    const agent = ctx.harness || "the agent";
    parts.launchTitle.textContent = "Sent to " + agent + " when Workbench starts it";
    const failed = Boolean(ctx.error && !ctx.resolve);
    const waiting = !ctx.resolve && !failed;
    parts.saveRow.hidden = failed || waiting;
    if (failed || waiting) {
      parts.delivery.hidden = true;
      parts.projectLaunch.hidden = true;
      parts.launchEmpty.hidden = true;
    }
    if (failed) {
      P.loaded(parts.ownRows);
      P.swap(parts.ownRows, AW.ui.errorNotice(ctx.error, { retry: () => P.refresh({ fresh: true }) }));
      parts.projectRows.replaceChildren();
    } else if (waiting) P.loading(parts.ownRows, "tools");
    else {
      if (ctx.loading) parts.ownRows.classList.add("stale");
      else P.loaded(parts.ownRows);
      paintLaunch();
    }
    P.swap(parts.warnings, ctx.resolve ? (ctx.resolve.warnings || []).filter((text) => P.topic(text) === "mcp").map((text) => AW.ui.notice("warn", text)) : []);
    paintServers();
    paintFiles();
    paintPlugins();
  }

  async function loadListing(force = false) {
    if (!ctx.workspace) return;
    if (!force && listingFor === ctx.workspace && listing) return;
    const mine = listTicket.next();
    const workspace = ctx.workspace;
    if (listingFor !== workspace) listing = null;
    listingFor = workspace;
    listingError = null;
    paintServers();
    try {
      const value = await AW.request("project-setup-mcp", { workspace });
      if (!listTicket.current(mine)) return;
      listing = value;
    } catch (thrown) {
      if (!listTicket.current(mine)) return;
      listingError = AW.asError(thrown);
    }
    paint();
  }

  async function loadPlugins() {
    const mine = pluginTicket.next();
    pluginsError = null;
    if (parts) paintPlugins();
    try {
      const value = await AW.request("plugin-describe");
      if (!pluginTicket.current(mine)) return;
      plugins = Array.isArray(value) ? value : [];
    } catch (thrown) {
      if (!pluginTicket.current(mine)) return;
      pluginsError = AW.asError(thrown);
    }
    if (parts) paint();
  }

  let consentFocus = false;
  const changedHere = () => {
    consentFocus = true;
    return P.refresh({ fresh: true });
  };

  async function loadConsent(force = false) {
    const key = ctx.workspace + "\n" + ctx.harness;
    if (!ctx.workspace || (!force && consentFor === key)) return;
    consentFor = key;
    const mine = consentTicket.next();
    const workspace = ctx.workspace;
    const harness = ctx.harness;
    let card = null;
    try {
      card = await AW.consent.card(workspace, harness, { onChange: changedHere });
    } catch (thrown) {
      card = AW.ui.errorNotice(thrown, { retry: () => loadConsent(true) });
    }
    if (!consentTicket.current(mine) || !parts) return;
    const refocus = consentFocus || parts.consent.contains(document.activeElement);
    consentFocus = false;
    parts.consent.replaceChildren(card || fallbackConsent());
    if (refocus && !AW.focus.first(parts.consent)) AW.focus.enter(AW.$("view"));
  }

  function fallbackConsent() {
    const status = ctx.resolve && ctx.resolve.consent;
    if (!status) return el("span");
    const agent = ctx.harness || "the agent";
    if (status.needed)
      return AW.ui.notice("warn", "This folder defines programs that " + agent + " will run. Review them before the first launch.", {
        actions: [
          {
            label: "Review and allow…",
            primary: true,
            run: () => AW.consent.dialog(ctx.workspace, ctx.harness, {}).then((granted) => granted && changedHere()),
          },
        ],
      });
    if (status.granted)
      return el(
        "div",
        { class: "project-consent-granted" },
        el("span", null, AW.ui.mark("ok"), AW.consent.grantedSentence(AW.state.folder && AW.state.folder.path === ctx.workspace ? AW.state.folder.consent_granted : "")),
        AW.ui.button("Revoke", {
          small: true,
          pending: "Revoking…",
          errorHost: parts.consent,
          onClick: () => AW.request("project-setup-consent-revoke", { workspace: ctx.workspace }).then(changedHere),
        }),
      );
    return el("span");
  }

  function removeServer(name) {
    const workspace = ctx.workspace;
    const harness = ctx.harness;
    return P.review({
      title: "Remove " + name,
      host: parts.projectErrors,
      plan: () => AW.request("project-setup-plan", { workspace, harness, pointer: false, mcp_remove: [name] }),
      apply: (plan, confirmed) => AW.request("project-setup-apply", { workspace, harness, pointer: false, mcp_remove: [name], plan: plan.id, confirmed }),
    }).then((result) => result && loadListing(true));
  }

  function exportServers() {
    const workspace = ctx.workspace;
    const harness = ctx.harness;
    return P.review({
      title: "Export to " + harness + "'s project file",
      origin: parts.exportButton,
      host: parts.projectErrors,
      plan: () => AW.request("project-setup-export-plan", { workspace, harness }),
      apply: (plan, confirmed) => AW.request("project-setup-export-apply", { workspace, harness, plan: plan.id, confirmed }),
    });
  }

  async function afterPlugins(changed) {
    await loadPlugins();
    AW.store.refresh("config").catch(() => {});
    P.refresh();
    return changed;
  }

  function removePlugin(name) {
    AW.ui.clearError(parts.pluginErrors);
    return AW.request("plugin-remove", { name })
      .then((done) => {
        if (done === null || done === undefined) return null;
        if (pluginNames().includes(name)) AW.form.set({ plugins: pluginNames().filter((item) => item !== name).join(",") });
        AW.ui.toast("Removed " + name + ".", { kind: "ok" });
        return afterPlugins(done);
      })
      .catch((thrown) => AW.ui.showError(parts.pluginErrors, thrown));
  }

  function importPlugins(update) {
    AW.ui.clearError(parts.pluginErrors);
    const call = update ? AW.request("plugin-update") : AW.request("plugin-import");
    return call
      .then((names) => {
        const list = Array.isArray(names) ? names : [];
        if (!list.length) return null;
        AW.ui.toast((update ? "Replaced the registered servers with " : "Registered ") + AW.fmt.list(list) + ".", { kind: "ok" });
        return afterPlugins(list);
      })
      .catch((thrown) => AW.ui.showError(parts.pluginErrors, thrown));
  }

  function openSecret(name) {
    const dialog = AW.ui.dialog("secret-dialog");
    AW.$("secret-dialog-title").textContent = "Value for " + name;
    const input = el("input", { type: "password", class: "mono", attrs: { autocomplete: "off", spellcheck: "false" } });
    const field = AW.ui.field("Value", input, { hint: "Kept in your operating system's credential vault, never in project files." });
    const errors = el("div", { class: "project-notices" });
    const save = AW.ui.button("Save", {
      kind: "primary",
      pending: "Saving…",
      onClick() {
        const value = input.value;
        if (!value) {
          field.setError("Enter the value.");
          input.focus();
          return null;
        }
        field.setError(null);
        AW.ui.clearError(errors);
        return AW.request("project-setup-secret-save", { name, value })
          .then(() => {
            input.value = "";
            dialog.close(true);
            AW.ui.toast("Saved the value of " + name + ".", { kind: "ok" });
            loadListing(true);
            P.refresh();
          })
          .catch((thrown) => AW.ui.showError(errors, thrown));
      },
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        save.click();
      }
    });
    AW.$("secret-dialog-body").replaceChildren(
      el("p", { class: "project-dialog-intro", text: "A project server in this folder reads " + name + ". Workbench gives it the value when a session starts." }),
      field.el,
      errors,
      el("div", { class: "dialog-actions" }, AW.ui.button("Cancel", { onClick: () => dialog.close(null) }), save),
    );
    dialog.onClose = () => (input.value = "");
    dialog.open();
    input.focus();
  }

  async function forgetSecret(name) {
    const yes = await AW.ui.confirm({ text: "Forget the saved value of " + name + "?", ok: "Forget value", danger: true });
    if (!yes) return;
    try {
      const forgotten = await AW.request("project-setup-secret-forget", { name });
      AW.ui.toast(forgotten ? "Forgot the value of " + name + "." : "No saved value of " + name + " was found.", forgotten ? { kind: "ok" } : {});
      loadListing(true);
      P.refresh();
    } catch (thrown) {
      AW.ui.showError(parts.projectErrors, thrown);
    }
  }

  function words(text) {
    return String(text || "")
      .split(/[\s,]+/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function openServer() {
    const dialog = AW.ui.dialog("server-dialog");
    AW.$("server-dialog-title").textContent = "Add a server";
    const taken = new Set([...(listing ? listing.servers || [] : []).map((row) => row.name), ...BUILTINS.map(([cap]) => cap)]);
    const name = el("input", { type: "text", class: "mono", attrs: { autocomplete: "off", spellcheck: "false", placeholder: "e.g. playwright" } });
    const nameField = AW.ui.field("Name", name, { hint: "Letters, numbers, dashes or underscores." });
    const kind = "server-kind-" + Date.now();
    const program = el("input", { type: "radio", value: "stdio", checked: true, attrs: { name: kind } });
    const web = el("input", { type: "radio", value: "http", attrs: { name: kind } });
    const type = el(
      "fieldset",
      { class: "field project-type" },
      el("legend", { class: "field-label", text: "Type" }),
      el("label", { class: "check" }, program, "Program on this computer"),
      el("label", { class: "check" }, web, "Web address"),
    );
    const command = el("input", { type: "text", class: "mono", attrs: { autocomplete: "off", spellcheck: "false", placeholder: "e.g. npx" } });
    const commandField = AW.ui.field("Command", command, { hint: "A program name such as npx, uvx or docker." });
    const args = el("input", { type: "text", class: "mono", attrs: { autocomplete: "off", spellcheck: "false", placeholder: "e.g. -y @playwright/mcp@0.0.86 --isolated" } });
    const argsField = AW.ui.field("Arguments", args, { hint: "Separate arguments with spaces." });
    const url = el("input", { type: "url", class: "mono", attrs: { autocomplete: "off", spellcheck: "false", placeholder: "e.g. https://example.com/mcp" } });
    const urlField = AW.ui.field("URL", url, { hint: "https://, or http:// on this computer." });
    const vars = el("input", { type: "text", class: "mono", attrs: { autocomplete: "off", spellcheck: "false", placeholder: "e.g. GITHUB_TOKEN" } });
    const varsField = AW.ui.field("Variables it needs", vars, { hint: "" });
    const varsHint = varsField.el.querySelector(".hint");
    const errors = el("div", { class: "project-notices" });
    const programPart = el("div", { class: "project-fields" }, commandField.el, argsField.el);
    const webPart = el("div", { class: "project-fields", hidden: true }, urlField.el);
    function sync() {
      const stdio = program.checked;
      programPart.hidden = !stdio;
      webPart.hidden = stdio;
      if (varsHint)
        varsHint.textContent = stdio
          ? "Names only, separated by spaces, for example GITHUB_TOKEN. You add the values later; they never go into the project file."
          : "Optional. One name; its value is sent as the bearer token. You add the value later; it never goes into the project file.";
    }
    program.addEventListener("change", sync);
    web.addEventListener("change", sync);
    sync();

    function collect() {
      let ok = true;
      const label = name.value.trim();
      nameField.setError(null);
      commandField.setError(null);
      urlField.setError(null);
      varsField.setError(null);
      if (!NAME.test(label)) {
        nameField.setError("Use 1 to 48 letters, numbers, dashes or underscores.");
        ok = false;
      } else if (taken.has(label)) {
        nameField.setError(BUILTINS.some(([cap]) => cap === label) ? "That name belongs to a built-in tool. Choose another name." : "This folder already has a server named " + label + ".");
        ok = false;
      }
      const names = words(vars.value);
      const badVar = names.find((item) => !VARIABLE.test(item));
      if (badVar) {
        varsField.setError("Variable names use capital letters, digits and underscores, and start with a letter.");
        ok = false;
      }
      const entry = { transport: program.checked ? "stdio" : "http", enabled: true };
      if (program.checked) {
        if (!command.value.trim()) {
          commandField.setError("Enter the program name.");
          ok = false;
        }
        entry.command = command.value.trim();
        entry.args = words(args.value);
        if (names.length) entry.env_from = names;
      } else {
        if (!url.value.trim()) {
          urlField.setError("Enter the server's URL.");
          ok = false;
        }
        if (names.length > 1) {
          varsField.setError("A web address takes one variable, sent as the bearer token.");
          ok = false;
        }
        entry.url = url.value.trim();
        if (names.length) entry.bearer_env = names[0];
      }
      if (!ok) {
        const first = dialog.el.querySelector('[aria-invalid="true"]');
        if (first) first.focus();
        return null;
      }
      return { label, entry };
    }

    const workspace = ctx.workspace;
    const harness = ctx.harness;
    const review = AW.ui.button("Review change…", {
      kind: "primary",
      async onClick() {
        const found = collect();
        if (!found) return;
        AW.ui.clearError(errors);
        const request = { workspace, harness, pointer: false, mcp_set: { [found.label]: found.entry } };
        let first;
        try {
          first = await AW.ui.pending(review, "Preparing…", AW.request("project-setup-plan", request));
        } catch (thrown) {
          AW.ui.showError(errors, thrown);
          return;
        }
        dialog.close(true);
        let used = false;
        const result = await P.review({
          title: "Add " + found.label,
          host: parts.projectErrors,
          plan: () => {
            if (!used) {
              used = true;
              return Promise.resolve(first);
            }
            return AW.request("project-setup-plan", request);
          },
          apply: (plan, confirmed) => AW.request("project-setup-apply", { ...request, plan: plan.id, confirmed }),
        });
        if (result) loadListing(true);
      },
    });
    AW.$("server-dialog-body").replaceChildren(
      el("p", { class: "project-dialog-intro", text: "The server is added to .agent-workbench/project.json in this folder, so it travels with the folder. Workbench shows you the change before it writes." }),
      nameField.el,
      type,
      programPart,
      webPart,
      varsField.el,
      errors,
      el("div", { class: "dialog-actions" }, AW.ui.button("Cancel", { onClick: () => dialog.close(null) }), review),
    );
    dialog.open();
    name.focus();
  }

  function mount(panel) {
    const consent = el("div", { class: "project-consent" });
    const warnings = el("div", { class: "project-notices" });
    const launchTitle = el("h2", { class: "panel-title" });
    const delivery = el("p", { class: "project-caption" });
    const ownRows = el("div", { class: "project-tools" });
    const saveState = el("span", { class: "project-caption" });
    const save = AW.ui.button("Save for this folder", { small: true });
    const saveErrors = el("div", { class: "project-notices" });
    save.addEventListener("click", () => {
      if (save.getAttribute("aria-disabled") === "true") return;
      AW.ui.clearError(saveErrors);
      AW.ui.pending(save, "Saving…", AW.form.save()).then(
        () => AW.store.refresh("folder").catch(() => {}),
        (thrown) => AW.ui.showError(saveErrors, thrown),
      );
    });
    const saveRow = el("div", { class: "project-save-row" }, saveState, save);
    const projectRows = el("div", { class: "project-tools" });
    const projectErrors = el("div", { class: "project-notices" });
    const projectLaunch = el("div", { class: "project-launch-project", hidden: true }, projectRows, el("p", { class: "project-caption project-tools-caption", text: "Project servers are switched for this folder at once." }));
    const launchEmpty = el("p", { class: "project-caption", hidden: true, text: "No tools are switched on and this folder has no project servers." });
    const launchPanel = el(
      "section",
      { class: "panel project-panel", attrs: { "aria-label": "Tools sent at launch" } },
      el("div", { class: "panel-head" }, launchTitle),
      delivery,
      ownRows,
      saveRow,
      saveErrors,
      projectLaunch,
      projectErrors,
      launchEmpty,
    );

    const serverCount = el("span", { class: "panel-count" });
    const serverNotices = el("div", { class: "project-notices" });
    const serverRows = el("div", { class: "project-tools" });
    const exportButton = AW.ui.button("Export to the agent's project file…", { onClick: () => exportServers() });
    const serverActions = el("div", { class: "actions project-panel-actions", hidden: true }, AW.ui.button("Add a server…", { onClick: () => openServer() }), exportButton);
    const serverPanel = el(
      "section",
      { class: "panel project-panel", attrs: { "aria-label": "Project servers" } },
      el("div", { class: "panel-head" }, el("h2", { class: "panel-title" }, "Project servers · ", el("span", { class: "project-path-title", text: ".agent-workbench/project.json" })), serverCount),
      serverNotices,
      serverRows,
      serverActions,
    );

    const filesTitle = el("h2", { class: "panel-title" });
    const fileRows = el("div", { class: "project-rows" });
    const filesPanel = el("section", { class: "panel project-panel", attrs: { "aria-label": "The agent's own MCP files" } }, el("div", { class: "panel-head" }, filesTitle), fileRows);

    const pluginCount = el("span", { class: "panel-count" });
    const pluginRows = el("div", { class: "project-tools" });
    const pluginErrors = el("div", { class: "project-notices" });
    const importButton = AW.ui.button("Register MCP file…", { id: "import-plugin", onClick: () => importPlugins(false) });
    const pluginPanel = el(
      "section",
      { class: "panel project-panel", id: "plugins-group", attrs: { "aria-labelledby": "plugins-group-title" } },
      el("div", { class: "panel-head" }, el("h2", { class: "panel-title", id: "plugins-group-title", text: "Plugins · registered on this computer" }), pluginCount),
      pluginRows,
      pluginErrors,
      el("div", { class: "actions project-panel-actions" }, importButton, AW.ui.button("Replace from a file…", { onClick: () => importPlugins(true) })),
      el("p", { class: "project-caption", text: "Registered commands run with your account when a session uses them." }),
    );

    panel.append(consent, warnings, launchPanel, serverPanel, filesPanel, pluginPanel);
    parts = { consent, warnings, launchTitle, delivery, ownRows, saveRow, saveState, save, projectRows, projectErrors, projectLaunch, launchEmpty, serverCount, serverNotices, serverRows, serverActions, exportButton, filesTitle, fileRows, pluginCount, pluginRows, pluginErrors };
    AW.form.on(() => {
      if (parts && ctx.resolve) paintLaunch();
    });
    AW.store.on(["folder"], () => parts && ctx.resolve && paintLaunch());
    loadPlugins();
  }

  P.tab({
    id: "mcp",
    label: "MCP tools",
    order: 3,
    mount,
    update(next) {
      const moved = next.workspace !== ctx.workspace || next.harness !== ctx.harness;
      ctx = next;
      if (!parts || !ctx.workspace) return;
      paint();
      if (next.fresh || moved) loadListing(true);
      if (!next.loading) loadConsent(true);
    },
  });
})();
