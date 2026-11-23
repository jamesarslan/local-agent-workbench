// Machines view: computers reached over SSH, their servers, tunnels, managed model and remote sessions (ui.md 5.5).
// Also registers the fix `machine.reconnect` (argument: a connection_id from machine-forwards) and the command "Add a machine over SSH…".
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const ui = AW.ui;

  const AGENTS = ["OMP", "Pi", "Claude Code", "Codex", "Hermes", "opencode"];
  const MODES = [
    ["native", "Use the remote CLI account"],
    ["shell", "Project shell"],
    ["local-model", "Use the managed model with OMP"],
    ["device", "Sign in on the remote machine"],
  ];
  const MANAGED_JOBS = ["machine-install", "machine-connect", "machine-stop", "machine-remove"];
  const narrow = window.matchMedia("(max-width: 899px)");

  const cache = new Map();
  let root = null;
  let parts = null;
  let listApi = null;
  let detail = null;
  let selected = "";
  let adding = null;
  let level = "list";
  let listError = null;
  let loaded = false;
  let systems = {};

  const hostsOf = () => Object.keys(AW.state.machines || {});
  const labelOf = (host) => ((AW.state.machines || {})[host] || "").trim() || host;
  const forwardsOf = (host) => (AW.state.forwards || []).filter((row) => row && row.host === host);
  const accountOf = (id) => (AW.state.accounts || []).find((row) => row && row.connection_id === id) || null;
  const plural = (n, one, many) => n + " " + (n === 1 ? one : many);
  // What the last connection check learned: Windows machines have no tmux and run PowerShell.
  const isWindows = (host) => Boolean(systems[host] && systems[host].os === "windows");
  function osLabel(host) {
    if (isWindows(host)) return "Windows";
    const p = cache.has(host) ? cache.get(host).probe : null;
    return p && p.system === "Linux" ? "Linux" : p && p.system === "Darwin" ? "macOS" : "";
  }
  const jobKey = (action, host) => action + ":" + host;
  const running = (action, host) => (AW.jobs ? AW.jobs.find(jobKey(action, host)) : null);

  function entry(host) {
    if (!cache.has(host))
      cache.set(host, {
        probe: null, probeAt: 0, probeError: null, probing: false, firewall: "",
        servers: null, serversError: null, finding: false,
        sessions: null, sessionsError: null, reading: false,
        note: {},
      });
    return cache.get(host);
  }

  function label(button, text) {
    const node = button.querySelector(".btn-label");
    if (node && node.textContent !== text) node.textContent = text;
  }

  function lock(button, on) {
    if (on) button.setAttribute("aria-disabled", "true");
    else if (!button.dataset.job && !button.dataset.pending) button.removeAttribute("aria-disabled");
  }

  function copyButton(text, name) {
    return ui.button("Copy", {
      small: true,
      title: name ? "Copy " + name : undefined,
      async onClick() {
        const copied = await AW.clipboard.write(typeof text === "function" ? text() : text);
        ui.toast(copied ? "Copied." : "That could not be copied. Select the text and copy it yourself.", copied ? {} : { kind: "danger" });
      },
    });
  }

  function errorSlot() {
    const host = el("div", { class: "machines-errors" });
    let shown = null;
    return {
      el: host,
      set(error, options) {
        if (error === shown) return;
        shown = error;
        if (error) ui.showError(host, error, options);
        else ui.clearError(host);
      },
    };
  }

  function refreshShared(keys) {
    return Promise.allSettled(keys.map((key) => AW.store.refresh(key, { force: true })));
  }

  function repaint(host) {
    paintList();
    if (detail && detail.host === host) detail.paint();
  }

  // Requests whose answers belong to one machine. Answers are kept per host, so switching machines never shows a stale one.
  function probe(host) {
    const c = entry(host);
    if (c.probing) return c.probing;
    c.probeError = null;
    c.probing = AW.request("machine-probe", { host })
      .then(
        (value) => {
          c.probe = value && typeof value === "object" ? value : {};
          c.probeAt = Date.now();
          c.firewall = typeof c.probe.firewall === "string" ? c.probe.firewall : "";
          if (c.probe.os === "windows" || c.probe.os === "posix") systems[host] = { ...(systems[host] || {}), os: c.probe.os, shell: c.probe.shell };
          if (!c.servers && !c.finding) findServers(host);
          if (!c.sessions && !c.reading && !isWindows(host)) readSessions(host);
        },
        (thrown) => {
          c.probeError = AW.asError(thrown);
          c.probeAt = Date.now();
        },
      )
      .finally(() => {
        c.probing = false;
        repaint(host);
      });
    repaint(host);
    return c.probing;
  }

  function findServers(host, ports) {
    const c = entry(host);
    if (c.finding) return c.finding;
    c.serversError = null;
    c.finding = AW.request("machine-discover", typeof ports === "string" ? { host, ports } : { host })
      .then(
        (value) => {
          c.servers = value && Array.isArray(value.endpoints) ? value.endpoints : [];
          if (value && Array.isArray(value.ports)) c.ports = value.ports.join(", ");
        },
        (thrown) => (c.serversError = AW.asError(thrown)),
      )
      .finally(() => {
        c.finding = false;
        repaint(host);
      });
    repaint(host);
    return c.finding;
  }

  function readSessions(host) {
    const c = entry(host);
    if (c.reading) return c.reading;
    c.sessionsError = null;
    c.reading = AW.request("sessions-remote", { host })
      .then(
        (value) => (c.sessions = Array.isArray(value) ? value : []),
        (thrown) => (c.sessionsError = AW.asError(thrown)),
      )
      .finally(() => {
        c.reading = false;
        repaint(host);
      });
    repaint(host);
    return c.reading;
  }

  function reconnect(id, origin) {
    const row = (AW.state.forwards || []).find((item) => item && item.connection_id === id);
    if (!row) return Promise.reject({ message: "This tunnel is no longer saved. Open Machines and attach the server again." });
    const name = row.label || row.host;
    const title = "Connecting to " + name;
    let job;
    if (row.managed) job = AW.jobs.run("machine-connect", { host: row.host }, { title, origin, key: jobKey("machine-connect", row.host) });
    else {
      const account = accountOf(id);
      const data = { host: row.host, port: row.remote_port };
      if (account && account.remote_model) data.model = account.remote_model;
      job = AW.jobs.run("machine-attach", data, { title, origin, key: jobKey("machine-attach", id) });
    }
    return job.then(async (value) => {
      await refreshShared(["forwards", "accounts"]);
      ui.toast("The tunnel to " + name + " is open again.", { kind: "ok" });
      return value;
    });
  }

  // List of machines.
  function statusOf(host) {
    const c = entry(host);
    const tunnels = forwardsOf(host);
    if (tunnels.some((row) => row.alive === false)) return { mark: "down", word: "Tunnel down" };
    if (c.probing) return { mark: "busy", word: "Checking…" };
    if (c.probeError) return { mark: "down", word: "Could not connect" };
    if (c.probe) return { mark: "ok", word: "Connected" };
    if (tunnels.length) return { mark: "ok", word: plural(tunnels.length, "tunnel open", "tunnels open") };
    return { mark: "idle", word: "Not checked yet" };
  }

  function machineRow(host) {
    const row = ui.row({ mark: "idle", title: labelOf(host), meta: host, onOpen: () => open(host, true) });
    row.el.dataset.host = host;
    row.el._update = row.update;
    return row.el;
  }

  function paintList() {
    if (!parts) return;
    const hosts = hostsOf().sort((a, b) => labelOf(a).localeCompare(labelOf(b)));
    parts.listError.set(listError, { retry: () => load() });
    const ready = AW.state.machines !== null;
    parts.listSkeleton.hidden = ready || Boolean(listError);
    if (!ready) return;
    listApi.set(hosts);
    for (const host of hosts) {
      const node = listApi.get(host);
      const status = statusOf(host);
      node._update({ mark: status.mark, title: labelOf(host), meta: [host, osLabel(host), status.word].filter(Boolean).join(" · ") });
      const main = node.querySelector(".row-main");
      if (host === selected && adding === null) {
        node.dataset.selected = "true";
        main.setAttribute("aria-current", "true");
      } else {
        delete node.dataset.selected;
        main.removeAttribute("aria-current");
      }
    }
    const empty = !hosts.length && adding === null;
    parts.noneYet.hidden = Boolean(hosts.length);
    parts.empty.hidden = !empty;
    parts.layout.hidden = empty;
    parts.addButton.setAttribute("aria-pressed", String(adding !== null));
  }

  function paintLevel() {
    if (!parts) return;
    const small = narrow.matches;
    parts.layout.dataset.level = small ? level : "both";
    parts.back.hidden = !small || level !== "detail";
  }

  // Detail: one block per concern, each painted from the cache, the shared store and running jobs.
  function block(title, { actions = [], id } = {}) {
    const heading = el("h3", { class: "group-label machines-block-title", id, text: title });
    const head = el("div", { class: "machines-block-head" }, heading, actions.length ? el("div", { class: "machines-block-actions" }, actions) : null);
    const body = el("div", { class: "machines-block-body" });
    const errors = errorSlot();
    const node = el("section", { class: "machines-block", attrs: { "aria-labelledby": id } }, head, body, errors.el);
    return { el: node, body, errors };
  }

  let blockSerial = 0;
  const blockId = (name) => "machines-" + name + "-" + ++blockSerial;

  function statusBlock(host, d) {
    const mark = ui.mark("idle");
    const word = el("span", { class: "machines-status-word" });
    const meta = el("div", { class: "machines-status-meta" });
    const check = ui.button("Check connection", { small: true, onClick: () => probe(host) });
    const errors = errorSlot();
    const command = el("pre", { class: "mono machines-command", tabindex: 0, attrs: { "aria-label": "Firewall command" } });
    const firewall = el(
      "details",
      { class: "machines-disclosure", hidden: true },
      el("summary", { text: "Firewall rule for this host" }),
      el("p", { class: "hint", text: "Run this on " + labelOf(host) + " if its firewall blocks SSH from this computer." }),
      command,
      el("div", { class: "actions" }, copyButton(() => command.textContent, "the firewall command")),
    );
    const node = el(
      "div",
      { class: "machines-status" },
      el("div", { class: "machines-status-line" }, mark, el("div", { class: "machines-status-text" }, word, meta), check),
      errors.el,
      firewall,
    );
    d.paints.push(() => {
      const c = entry(host);
      const p = c.probe;
      let state = "idle";
      let text = "Not checked yet";
      let more = "Check the connection to see this machine's system and memory.";
      if (c.probing) {
        state = "busy";
        text = "Checking the connection…";
        more = p ? "Last checked " + AW.fmt.ago(c.probeAt) : "";
      } else if (c.probeError) {
        state = "down";
        text = "Could not connect";
        more = "Checked " + AW.fmt.ago(c.probeAt);
      } else if (p) {
        state = "ok";
        const ram = AW.fmt.gib(p.ram_gib);
        text = ["Connected", [p.system, p.arch].filter(Boolean).join(" "), ram && ram + " RAM"].filter(Boolean).join(" · ");
        const gpu = p.unified ? "Shared CPU and GPU memory" : p.gpu_vendor ? [p.gpu_name, AW.fmt.gib(p.gpu_gib) + " GPU memory"].filter(Boolean).join(", ") : "GPU memory unknown";
        const shell = p.os === "windows" ? (p.shell === "pwsh" ? "PowerShell 7" : "Windows PowerShell") + (p.python ? ", Python " + p.python : ", no Python") : "";
        more = [gpu, shell, "checked " + AW.fmt.ago(c.probeAt), p.installed ? "Managed runtime installed" : ""].filter(Boolean).join(" · ");
      }
      ui.setMark(mark, state);
      if (word.textContent !== text) word.textContent = text;
      if (meta.textContent !== more) meta.textContent = more;
      meta.hidden = !more;
      label(check, c.probing ? "Checking…" : "Check connection");
      lock(check, Boolean(c.probing));
      errors.set(c.probing ? null : c.probeError);
      firewall.hidden = !c.firewall;
      if (command.textContent !== c.firewall) command.textContent = c.firewall;
    });
    return node;
  }

  function identityForm(host, d) {
    const name = el("input", { type: "text", value: host ? (AW.state.machines || {})[host] || "" : "", attrs: { autocomplete: "off", spellcheck: "false", maxlength: "80", placeholder: host || "gpu-box" } });
    const ssh = el("input", { type: "text", class: "mono", value: host || (adding && adding.host) || "", attrs: { autocomplete: "off", spellcheck: "false", autocapitalize: "off", placeholder: "me@gpu-box" } });
    const nameField = ui.field("Name", name, { hint: host ? undefined : "How Workbench lists it. Leave it empty to use the SSH name." });
    const sshField = ui.field(host ? "SSH" : "SSH alias or user@host", ssh, { hint: host ? undefined : "An alias from your SSH config, or user@hostname." });
    const save = ui.button("Save", {
      kind: host ? undefined : "primary",
      pending: "Saving…",
      async onClick() {
        const typed = ssh.value.trim();
        sshField.setError(null);
        if (!typed) {
          sshField.setError("Use an SSH config alias or user@hostname.");
          ssh.focus();
          return;
        }
        let saved;
        try {
          saved = await AW.request("machine-save", { name: name.value.trim(), host: typed });
        } catch (thrown) {
          sshField.setError(AW.asError(thrown).message);
          ssh.focus();
          return;
        }
        const next = typeof saved === "string" && saved ? saved : typed;
        await refreshShared(["machines"]);
        const moved = host && next !== host;
        adding = null;
        ui.toast(moved ? "Saved " + labelOf(next) + " as a new machine. " + labelOf(host) + " is still listed." : "Saved " + labelOf(next) + ".", { kind: "ok" });
        if (!host || moved) open(next, true, { probe: true });
        else {
          buildDetail(host).focusName();
          paintList();
        }
      },
    });
    const submit = (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        save.click();
      }
    };
    name.addEventListener("keydown", submit);
    ssh.addEventListener("keydown", submit);
    ssh.addEventListener("input", () => sshField.setError(null));
    const buttons = [save];
    if (!host && hostsOf().length)
      buttons.push(
        ui.button("Cancel", {
          onClick() {
            adding = null;
            const back = selected && hostsOf().includes(selected) ? selected : hostsOf()[0];
            open(back, !narrow.matches);
            if (narrow.matches) showList(back);
          },
        }),
      );
    return {
      el: el("div", { class: "machines-identity" + (host ? "" : " adding") }, nameField.el, sshField.el, el("div", { class: "machines-identity-actions" }, buttons)),
      focus: () => (host ? name : ssh).focus(),
    };
  }

  function serversBlock(host, d) {
    // An empty field clears saved ports only once they are known, so a quick click never forgets them.
    const typed = () => (entry(host).ports === undefined && !ports.value.trim() ? undefined : ports.value);
    const again = ui.button("Look again", { small: true, onClick: () => findServers(host, typed()) });
    const b = block("Servers running there", { actions: [again], id: blockId("servers") });
    const portsId = blockId("ports");
    const ports = el("input", { type: "text", class: "mono", id: portsId, value: entry(host).ports || "", attrs: { autocomplete: "off", spellcheck: "false", inputmode: "numeric", placeholder: "9000", "aria-describedby": portsId + "-hint" } });
    const portsField = {
      el: el(
        "div",
        { class: "machines-ports" },
        el("label", { for: portsId, text: "Other ports" }),
        ports,
        el("span", { class: "hint", id: portsId + "-hint", text: "Beyond the usual ones; separate with commas, then Look again." }),
      ),
    };
    ports.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      findServers(host, typed());
    });
    const skeleton = ui.skeleton(2, "servers on " + labelOf(host));
    const idle = el("p", { class: "machines-quiet", text: "Workbench looks for model servers on this machine's own loopback after the connection check." });
    const rows = el("div", { class: "machines-rows" });
    function fill(node, row) {
      const models = (Array.isArray(row.models) ? row.models : []).map((model) => model.id + (model.context ? " · " + AW.fmt.context(model.context) + " context" : ""));
      const name = row.label || row.kind || "Model server";
      const meta =
        row.needs_key && !models.length ? "Needs a key; you type its model ID when you attach it"
        : models.length ? models[0] + (models.length > 1 ? " and " + (models.length - 1) + " more" : "")
        : "No models listed";
      node._update({ title: name + " · port " + row.port + (models.length ? " · " + plural(models.length, "model", "models") : ""), meta });
      const attached = forwardsOf(host).some((item) => !item.managed && item.remote_port === row.port);
      node._attach.hidden = attached;
      node._tag.hidden = !attached;
      node._attach.setAttribute("aria-label", "Attach " + name + " on port " + row.port);
    }
    const list = ui.list(rows, {
      key: (row) => row.port,
      render(row) {
        const attach = ui.button("Attach…", {
          small: true,
          async onClick() {
            const added = await AW.attach.open({ host, port: row.port });
            if (added) await refreshShared(["forwards", "accounts"]);
            d.paint();
          },
        });
        const tag = el("span", { class: "chip ok", text: "Attached" });
        const item = ui.row({ title: "", meta: "", actions: [attach, tag] });
        Object.assign(item.el, { _attach: attach, _tag: tag, _update: item.update });
        fill(item.el, row);
        return item.el;
      },
      update: fill,
      empty: () => el("p", { class: "machines-quiet", text: "No model server answered on " + labelOf(host) + "." }),
    });
    b.body.append(skeleton, idle, rows, portsField.el);
    d.paints.push(() => {
      const c = entry(host);
      if (document.activeElement !== ports && typeof c.ports === "string" && ports.value !== c.ports) ports.value = c.ports;
      label(again, c.finding ? "Looking…" : "Look again");
      lock(again, Boolean(c.finding));
      skeleton.hidden = !(c.finding && !c.servers);
      idle.hidden = Boolean(c.finding || c.servers || c.serversError);
      rows.hidden = !c.servers;
      rows.classList.toggle("stale", Boolean(c.finding && c.servers));
      if (c.servers) list.set(c.servers);
      b.errors.set(c.finding ? null : c.serversError, { retry: () => findServers(host, typed()) });
    });
    return b.el;
  }

  function tunnelsBlock(host, d) {
    const b = block("Tunnels", { id: blockId("tunnels") });
    const rows = el("div", { class: "machines-rows" });
    const tasks = el("div", { class: "machines-tasks" });
    const live = new Map();
    const busyFor = (row) => (row.managed ? running("machine-connect", host) : running("machine-attach", row.connection_id));
    function fill(node, row) {
      const account = accountOf(row.connection_id);
      const name = (account && account.name) || nameOf(row);
      const ports = "Remote port " + row.remote_port + " → " + (row.local_port ? "local port " + row.local_port : "no local port");
      node._update({ mark: row.alive ? "ok" : "down", title: name, meta: ports + " · " + (row.alive ? "Open" : "Tunnel down") });
      node._again.hidden = Boolean(row.alive);
      node._again.setAttribute("aria-label", "Reconnect " + name);
      node._detach.setAttribute("aria-label", "Detach " + name);
      lock(node._again, Boolean(busyFor(row)));
    }
    const list = ui.list(rows, {
      key: (row) => row.connection_id,
      render(row) {
        const again = ui.button("Reconnect", {
          small: true,
          kind: "primary",
          onClick: () => reconnect(row.connection_id, again).catch((thrown) => b.errors.set(AW.asError(thrown), { retry: () => reconnect(row.connection_id, again) })),
        });
        const detach = ui.button("Detach", {
          small: true,
          async onClick() {
            const account = accountOf(row.connection_id);
            const name = (account && account.name) || nameOf(row);
            const yes = await ui.confirm({
              text: "Detach " + name + "? The tunnel closes and its connection is removed from Connections.",
              ok: "Detach",
              danger: true,
            });
            if (!yes) return;
            try {
              await ui.pending(detach, "Detaching…", AW.request("machine-detach", { connection_id: row.connection_id }));
            } catch (thrown) {
              b.errors.set(AW.asError(thrown));
              return;
            }
            b.errors.set(null);
            await refreshShared(["forwards", "accounts"]);
            ui.toast("Detached " + name + ".", { kind: "ok" });
          },
        });
        const item = ui.row({ mark: "ok", title: "", meta: "", actions: [again, detach] });
        Object.assign(item.el, { _again: again, _detach: detach, _update: item.update });
        fill(item.el, row);
        return item.el;
      },
      update: fill,
      empty: () => el("p", { class: "machines-quiet", text: "No tunnels to " + labelOf(host) + ". Attach a server above to open one." }),
    });
    b.body.append(rows, tasks);
    d.paints.push(() => {
      const items = forwardsOf(host);
      list.set(items);
      syncTasks(tasks, live, items.map(busyFor).filter(Boolean));
    });
    d.cleanups.push(() => live.forEach((task) => task.destroy()));
    return b.el;
  }

  function nameOf(row) {
    if (row.managed) return "Workbench-managed model";
    const found = (entry(row.host).servers || []).find((server) => server.port === row.remote_port);
    return ((found && (found.label || found.kind)) || row.kind || "Model server") + " · port " + row.remote_port;
  }

  function syncTasks(host, live, records) {
    const want = new Set(records.map((record) => record.handle));
    for (const [handle, task] of live)
      if (!want.has(handle)) {
        task.destroy();
        live.delete(handle);
      }
    for (const record of records)
      if (!live.has(record.handle)) {
        const task = ui.taskRow(record.handle);
        live.set(record.handle, task);
        host.append(task.el);
      }
  }

  function managedBlock(host, d) {
    const b = block("Workbench-managed model", { id: blockId("managed") });
    const state = el("p", { class: "machines-line" });
    const install = ui.button("Install…", { small: true, onClick: () => managed("machine-install", install) });
    const start = ui.button("Start and connect", { small: true, kind: "primary", onClick: () => managed("machine-connect", start) });
    const stop = ui.button("Stop…", { small: true, onClick: () => managed("machine-stop", stop) });
    const buttons = el("div", { class: "actions" }, install, start, stop);
    const tasks = el("div", { class: "machines-tasks" });
    const live = new Map();
    // Windows: the model file may already be on the machine; Workbench reuses it only when its SHA-256 matches.
    const reuse = el("input", { type: "text", class: "mono", attrs: { autocomplete: "off", spellcheck: "false", autocapitalize: "off", placeholder: "C:\\Users\\me\\models\\model.gguf" } });
    const reuseField = ui.field("Model file already on " + labelOf(host), reuse, { hint: "Optional. Used only if its SHA-256 matches; otherwise Workbench downloads the file." });
    const look = ui.button("Look for it", { small: true, onClick: findFiles });
    const files = el("div", { class: "machines-rows" });
    const reuseBox = el("div", { class: "machines-reuse", hidden: true }, reuseField.el, el("div", { class: "actions" }, look), files);
    b.body.append(el("div", { class: "machines-split-line" }, state, buttons), reuseBox, tasks);
    d.cleanups.push(() => live.forEach((task) => task.destroy()));

    async function findFiles() {
      b.errors.set(null);
      let value;
      try {
        value = await ui.pending(look, "Looking…", AW.request("machine-files", { host }));
      } catch (thrown) {
        b.errors.set(AW.asError(thrown));
        return;
      }
      const paths = value && Array.isArray(value.files) ? value.files.filter((item) => typeof item === "string") : [];
      files.replaceChildren(
        ...(paths.length
          ? paths.map((path) => ui.row({ title: path, actions: [ui.button("Use this file", { small: true, onClick: () => { reuse.value = path; reuse.focus(); } })] }).el)
          : [el("p", { class: "machines-quiet", text: "No file of that size in models, Downloads, LM Studio or the Hugging Face cache. Type its path above, or let Workbench download it." })]),
      );
    }

    async function managed(action, origin) {
      const name = labelOf(host);
      const source = isWindows(host) ? reuse.value.trim() : "";
      if (action === "machine-install") {
        const plan = (entry(host).probe || {}).plan;
        const model = plan ? plan.label + " (" + AW.fmt.bytes(plan.bytes) + ")" : "the model";
        const yes = await ui.confirm({
          text: isWindows(host)
            ? "Install the managed model on " + name + "? Workbench downloads llama.cpp for CUDA (about 650 MB) and " + (source ? "checks " + source + " as " : "") + model + " into %LOCALAPPDATA%\\agent-workbench-node."
            : "Install the Workbench-managed runtime and model on " + name + "? It downloads the runtime and model files on that machine and can take up to an hour.",
          ok: "Install",
        });
        if (!yes) return;
      }
      if (action === "machine-stop") {
        const yes = await ui.confirm({ text: "Stop the model on " + name + "? Sessions that use it lose their model.", ok: "Stop", danger: true });
        if (!yes) return;
      }
      b.errors.set(null);
      const key = jobKey(action, host);
      const job =
        action === "machine-install" ? AW.jobs.run("machine-install", source ? { host, source } : { host }, { title: "Installing the managed model on " + name, origin, key })
        : action === "machine-stop" ? AW.jobs.run("machine-stop", { host }, { title: "Stopping the model on " + name, origin, key })
        : AW.jobs.run("machine-connect", { host }, { title: "Connecting to " + name, origin, key });
      d.paint();
      try {
        const value = await job;
        if (action === "machine-connect") {
          await refreshShared(["forwards", "accounts"]);
          const account = value && value.connection_id ? accountOf(value.connection_id) : null;
          ui.toast(name + " is connected. Choose " + ((account && account.name) || name) + " as the model on Launch.", { kind: "ok" });
        } else if (action === "machine-stop") await refreshShared(["forwards"]);
      } catch (thrown) {
        b.errors.set(AW.asError(thrown), { retry: () => managed(action, origin) });
      }
      probe(host);
    }

    d.paints.push(() => {
      const c = entry(host);
      const p = c.probe;
      const busy = MANAGED_JOBS.map((action) => running(action, host)).filter(Boolean);
      syncTasks(tasks, live, busy);
      const tunnel = forwardsOf(host).find((row) => row.managed && row.alive);
      let text;
      let show = { install: false, start: false, stop: false };
      const model = p && typeof p.model === "string" && p.model ? p.model + " · " : "";
      if (!p) text = c.probing ? "Checking whether this machine can run it…" : "Check the connection to see whether " + labelOf(host) + " can run the Workbench-managed model.";
      else if (p.managed === false) text = p.reason || "This machine cannot run the Workbench-managed model.";
      else if (!p.installed) {
        const plan = p.os === "windows" && p.plan ? " It uses " + p.plan.label + ", " + AW.fmt.bytes(p.plan.bytes) + ", with llama.cpp for CUDA." : "";
        text = "Not installed." + plan;
        show.install = true;
      } else if (p.serving) {
        text = model + "Serving on port " + p.port + (tunnel ? " · connected through a tunnel" : " · not connected to this computer");
        show = { install: false, start: !tunnel, stop: true };
      } else if (p.running) {
        text = model + "Loading the model on port " + p.port + ".";
        show = { install: false, start: true, stop: true };
      } else {
        text = model + "Installed · stopped.";
        show.start = true;
      }
      reuseBox.hidden = !(p && p.os === "windows" && p.managed !== false && !p.installed);
      if (state.textContent !== text) state.textContent = text;
      state.classList.toggle("muted", !p || p.managed === false);
      install.hidden = !show.install;
      start.hidden = !show.start;
      stop.hidden = !show.stop;
      buttons.hidden = !(show.install || show.start || show.stop);
      for (const button of [install, start, stop]) lock(button, busy.length > 0);
      label(start, tunnel || !p || !p.running ? "Start and connect" : "Connect");
    });
    return b.el;
  }

  function sessionsBlock(host, d) {
    const refresh = ui.button("Refresh", { small: true, onClick: () => readSessions(host) });
    const b = block("Remote sessions", { actions: [refresh], id: blockId("sessions") });
    const skeleton = ui.skeleton(2, "remote sessions on " + labelOf(host));
    const idle = el("p", { class: "machines-quiet", text: "Remote sessions are listed after the connection check." });
    const noTmux = el("p", { class: "machines-quiet", hidden: true, text: "Windows has no tmux, so sessions on " + labelOf(host) + " end when their tab or window closes. There is nothing to reattach here." });
    const rows = el("div", { class: "machines-rows" });
    let checking = "";
    const WORDS = { attached: "Attached", detached: "Detached" };
    function fill(node, row) {
      const since = row.started ? "started " + AW.fmt.ago(row.started) : "";
      node._update({ mark: row.state === "attached" ? "ok" : "idle", title: row.title || row.name, meta: [WORDS[row.state] || "Running", since].filter(Boolean).join(" · ") });
      node._check.setAttribute("aria-label", "Check " + (row.title || row.name));
      node._end.setAttribute("aria-label", "End " + (row.title || row.name) + "…");
      node._row = row;
    }
    const list = ui.list(rows, {
      key: (row) => row.name,
      render(row) {
        const check = ui.button("Check", {
          small: true,
          onClick() {
            checking = row.name;
            return readSessions(host).finally(() => (checking = ""));
          },
        });
        const end = ui.button("End…", { small: true, kind: "danger", onClick: () => endSession(item.el._row) });
        const item = ui.row({ mark: "ok", title: "", meta: "", actions: [check, end] });
        Object.assign(item.el, { _check: check, _end: end, _update: item.update });
        fill(item.el, row);
        return item.el;
      },
      update: fill,
      empty: () => el("p", { class: "machines-quiet", text: "No remote sessions on " + labelOf(host) + "." }),
    });
    b.body.append(skeleton, idle, noTmux, rows);

    async function endSession(row) {
      const title = row.title || row.name;
      const yes = await ui.confirm({ text: "End the remote session " + title + " on " + labelOf(host) + "?", ok: "End session", danger: true });
      if (!yes) return;
      const node = list.get(row.name);
      try {
        await ui.pending(node._end, "Ending…", AW.request("sessions-end", { host, name: row.name }));
      } catch (thrown) {
        b.errors.set(AW.asError(thrown));
        return;
      }
      b.errors.set(null);
      ui.toast("Ended " + title + " on " + labelOf(host) + ".", { kind: "ok" });
      AW.store.refresh("sessions").catch(() => {});
      readSessions(host);
    }

    d.paints.push(() => {
      const c = entry(host);
      const win = isWindows(host);
      noTmux.hidden = !win;
      refresh.hidden = win;
      label(refresh, c.reading && !checking ? "Refreshing…" : "Refresh");
      lock(refresh, Boolean(c.reading));
      skeleton.hidden = win || !(c.reading && !c.sessions);
      idle.hidden = win || Boolean(c.reading || c.sessions || c.sessionsError);
      rows.hidden = !c.sessions;
      rows.classList.toggle("stale", Boolean(c.reading && c.sessions));
      if (c.sessions) list.set(c.sessions);
      for (const row of c.sessions || []) {
        const node = list.get(row.name);
        if (!node) continue;
        label(node._check, c.reading && checking === row.name ? "Checking…" : "Check");
        lock(node._check, Boolean(c.reading));
      }
      b.errors.set(c.reading ? null : c.sessionsError, { retry: () => readSessions(host) });
    });
    return b.el;
  }

  function newSessionBlock(host, d) {
    const b = block("New remote session", { id: blockId("new-session") });
    const c = entry(host);
    const folder = el("input", { type: "text", class: "mono", value: c.note.folder || "", attrs: { autocomplete: "off", spellcheck: "false", autocapitalize: "off", placeholder: "~/project" } });
    const agents = (AW.state.config && Array.isArray(AW.state.config.agents) && AW.state.config.agents.length ? AW.state.config.agents : AGENTS).map(String);
    const agent = el("select", null, agents.map((name) => el("option", { value: name, text: name })));
    agent.value = c.note.agent && agents.includes(c.note.agent) ? c.note.agent : agents.includes("OMP") ? "OMP" : agents[0];
    const mode = el("select", null, MODES.map(([value, text]) => el("option", { value, text })));
    mode.value = c.note.mode || "native";
    const fields = el(
      "div",
      { class: "machines-session-fields" },
      ui.field("Folder", folder, { hint: "A folder on " + labelOf(host) + ". Leave it empty to start in your home folder; ~ means that folder." }).el,
      ui.field("Agent", agent).el,
      ui.field("Mode", mode).el,
    );
    const hint = el("p", { class: "hint machines-hint", hidden: true });
    const dock = ui.button("Open in terminal dock", {
      small: true,
      kind: "primary",
      async onClick() {
        b.errors.set(null);
        try {
          await AW.terminal.start("remote", { origin: dock, remote: { ...request(), label: labelOf(host) } });
        } catch (thrown) {
          b.errors.set(AW.asError(thrown));
        }
      },
    });
    const sshTerm = ui.button("Open SSH terminal", {
      small: true,
      async onClick() {
        b.errors.set(null);
        const job = AW.jobs.run("machine-session", request(), { title: "Opening an SSH terminal to " + labelOf(host), origin: sshTerm, key: jobKey("machine-session", host) });
        d.paint();
        try {
          await job;
          ui.toast("A terminal window opened for " + labelOf(host) + ".", { kind: "ok" });
        } catch (thrown) {
          b.errors.set(AW.asError(thrown), { retry: () => sshTerm.click() });
        }
        d.paint();
      },
    });
    const tasks = el("div", { class: "machines-tasks" });
    const live = new Map();
    d.cleanups.push(() => live.forEach((task) => task.destroy()));
    b.body.append(fields, hint, el("div", { class: "actions" }, dock, sshTerm), tasks);

    function request() {
      return { host, folder: folder.value.trim() || ".", harness: agent.value, mode: mode.value };
    }
    function remember() {
      c.note = { folder: folder.value, agent: agent.value, mode: mode.value };
      d.paint();
    }
    for (const node of [folder, agent, mode]) node.addEventListener("change", remember);

    d.paints.push(() => {
      const managed = mode.value === "local-model";
      if (managed && agent.value !== "OMP" && agents.includes("OMP")) agent.value = "OMP";
      agent.disabled = managed;
      const notes = [];
      if (managed) notes.push("The managed model is used with OMP, and Workbench starts it on " + labelOf(host) + " first.");
      const p = entry(host).probe;
      const win = isWindows(host);
      const noTmux = Boolean(p && p.tmux === false && !win);
      if (win) notes.push("The session runs in " + (systems[host].shell === "pwsh" ? "PowerShell 7" : "Windows PowerShell") + " and ends when its tab or window closes; Windows has no tmux to keep it running.");
      if (noTmux) notes.push("tmux is not installed on " + labelOf(host) + ", so the terminal dock cannot keep a session there. Install tmux there, or open an SSH terminal.");
      const noFolder = !AW.form.get().workspace;
      if (noFolder) notes.push("The terminal dock keeps sessions per project. Choose a project folder first, or open an SSH terminal.");
      hint.hidden = !notes.length;
      const text = notes.join(" ");
      if (hint.textContent !== text) hint.textContent = text;
      label(dock, win ? "Open in terminal dock" : "Open in terminal dock · tmux");
      folder.placeholder = win ? "~\\project" : "~/project";
      lock(dock, noTmux || noFolder);
      if (noTmux || noFolder) dock.setAttribute("aria-describedby", hint.id || (hint.id = blockId("hint")));
      else dock.removeAttribute("aria-describedby");
      const busy = running("machine-session", host);
      syncTasks(tasks, live, busy ? [busy] : []);
    });
    return b.el;
  }

  function pairingBlock(host, d) {
    const name = labelOf(host);
    const errors = errorSlot();
    const command = el("pre", { class: "mono machines-command", tabindex: 0, attrs: { "aria-label": "Pairing command" } });
    const commandWrap = el("div", { class: "machines-command-wrap", hidden: true }, command, el("div", { class: "actions" }, copyButton(() => command.textContent, "the pairing command")));
    const tasks = el("div", { class: "machines-tasks" });
    const live = new Map();
    d.cleanups.push(() => live.forEach((task) => task.destroy()));
    const note = el("p", { class: "machines-line", hidden: true });
    const windowsKeys = el("p", { class: "hint", hidden: true, text: "On Windows the key goes into .ssh\\authorized_keys in your user folder, or into C:\\ProgramData\\ssh\\administrators_authorized_keys for an administrator account." });
    const pair = ui.button("Pair through SSH", {
      small: true,
      async onClick() {
        errors.set(null);
        const job = AW.jobs.run("machine-pair", { host }, { title: "Pairing with " + name, origin: pair, key: jobKey("machine-pair", host) });
        d.paint();
        try {
          await job;
          note.hidden = false;
          note.textContent = "The pairing terminal opened. When you have signed in there, choose Check connection.";
        } catch (thrown) {
          errors.set(AW.asError(thrown), { retry: () => pair.click() });
        }
        d.paint();
      },
    });
    const show = ui.button("Show pairing command", {
      small: true,
      pending: "Loading…",
      async onClick() {
        errors.set(null);
        try {
          const line = await AW.request("machine-key", { host });
          command.textContent = typeof line === "string" ? line : "";
          commandWrap.hidden = !command.textContent;
          show.hidden = true;
          command.focus();
        } catch (thrown) {
          errors.set(AW.asError(thrown), { retry: () => show.click() });
        }
      },
    });
    const revoke = ui.button("Revoke the Workbench key on " + name + "…", {
      small: true,
      kind: "danger",
      async onClick() {
        const yes = await ui.confirm({
          text: "Revoke the Workbench key on " + name + "? Workbench removes its own key there and closes its tunnels. " + name + " stays listed here.",
          ok: "Revoke key",
          danger: true,
        });
        if (!yes) return;
        errors.set(null);
        try {
          await ui.pending(revoke, "Revoking…", AW.request("machine-unpair", { host }));
        } catch (thrown) {
          errors.set(AW.asError(thrown));
          return;
        }
        const c = entry(host);
        c.probe = null;
        c.probeError = null;
        commandWrap.hidden = true;
        show.hidden = false;
        await refreshShared(["forwards", "accounts"]);
        ui.toast("The Workbench key was removed from " + name + ".", { kind: "ok" });
        repaint(host);
      },
    });
    const remove = ui.button("Remove the managed runtime from " + name + "…", {
      small: true,
      kind: "danger",
      async onClick() {
        const yes = await ui.confirm({
          text: "Remove the managed runtime from " + name + "? Its engine and model files are deleted on that machine, and its connection is removed here.",
          ok: "Remove runtime",
          danger: true,
        });
        if (!yes) return;
        errors.set(null);
        const job = AW.jobs.run("machine-remove", { host }, { title: "Removing the runtime from " + name, origin: remove, key: jobKey("machine-remove", host) });
        d.paint();
        try {
          await job;
          ui.toast("The managed runtime was removed from " + name + ".", { kind: "ok" });
        } catch (thrown) {
          errors.set(AW.asError(thrown));
        }
        await refreshShared(["forwards", "accounts"]);
        probe(host);
      },
    });
    const forget = ui.button("Forget this machine…", { small: true, kind: "danger", onClick: () => forgetMachine(host, forget, errors) });
    const node = el(
      "details",
      { class: "machines-block machines-pairing" },
      el("summary", null, el("span", { class: "group-label", text: "Pairing and removal" })),
      el(
        "div",
        { class: "machines-block-body" },
        el("p", { class: "hint", text: "Pair through SSH: a terminal opens. Confirm the host key and sign in once. Workbench then reaches " + name + " with its own key." }),
        el("div", { class: "actions" }, pair, show),
        note,
        commandWrap,
        el("p", { class: "hint", text: "Or run the pairing command on " + name + " to add the Workbench key yourself." }),
        windowsKeys,
        el("div", { class: "actions machines-danger-zone" }, revoke, remove, forget),
        tasks,
        errors.el,
      ),
    );
    d.paints.push(() => {
      windowsKeys.hidden = !isWindows(host);
      const busy = ["machine-pair", "machine-remove"].map((action) => running(action, host)).filter(Boolean);
      syncTasks(tasks, live, busy);
      if (busy.length) node.open = true;
    });
    return node;
  }

  async function forgetMachine(host, button, errors) {
    const name = labelOf(host);
    const yes = await ui.confirm({
      text: "Forget " + name + "? Workbench deletes its key, tunnels and connections for this machine on this computer. Nothing is changed on " + name + ".",
      ok: "Forget machine",
      danger: true,
    });
    if (!yes) return;
    let result;
    let failure = null;
    try {
      result = await ui.pending(button, "Forgetting…", AW.request("machine-forget", { host }));
    } catch (thrown) {
      failure = AW.asError(thrown);
    }
    await refreshShared(["machines", "forwards", "accounts"]);
    if (failure && hostsOf().includes(host)) {
      errors.set(failure);
      return;
    }
    cache.delete(host);
    const key = result && typeof result.public_key === "string" ? result.public_key : "";
    showForgotten(name, key, failure, isWindows(host));
    delete systems[host];
    const next = hostsOf()[0] || "";
    selected = "";
    if (next && !narrow.matches) open(next, false);
    else {
      closeDetail();
      level = "list";
      paintLevel();
      paintList();
    }
    if (!failure && !key) ui.toast("Workbench forgot " + name + ".", { kind: "ok" });
    const row = next && listApi ? listApi.get(next) : null;
    const target = parts.notices.querySelector("button") || (row && row.querySelector(".row-main")) || (parts.empty.hidden ? parts.addButton : parts.empty.querySelector("button"));
    if (target) target.focus();
  }

  function showForgotten(name, key, failure, win) {
    if (!parts) return;
    parts.notices.replaceChildren();
    if (failure) ui.showError(parts.notices, failure);
    if (!key) return;
    const text = el(
      "div",
      null,
      el("p", { text: "Workbench forgot " + name + ". Its key line stays in " + (win ? "the authorized_keys file" : "~/.ssh/authorized_keys") + " on that machine until you remove it there:" }),
      el("pre", { class: "mono machines-command", tabindex: 0, text: key, attrs: { "aria-label": "Public key of " + name } }),
    );
    parts.notices.append(ui.notice("info", text, { actions: [copyButton(key, "the key line")], dismissible: true }));
  }

  function closeDetail() {
    if (detail) detail.destroy();
    detail = null;
    if (parts) parts.detail.replaceChildren();
  }

  function buildDetail(host) {
    closeDetail();
    const d = { host, paints: [], cleanups: [] };
    d.paint = () => d.paints.forEach((fn) => fn());
    d.destroy = () => d.cleanups.forEach((fn) => fn());
    const title = el("h2", { class: "machines-name", tabindex: -1, id: "machine-detail-title" });
    const sub = el("p", { class: "machines-host mono" });
    d.retitle = () => {
      title.textContent = host ? labelOf(host) : "Add a machine";
      sub.textContent = host || "";
      sub.hidden = !host || labelOf(host) === host;
    };
    d.retitle();
    d.focus = () => title.focus({ preventScroll: false });
    const panel = el("section", { class: "panel machines-detail-panel", attrs: { "aria-labelledby": "machine-detail-title" } }, el("div", { class: "machines-detail-head" }, title, sub));
    if (!host) {
      const form = identityForm("", d);
      panel.append(
        el("p", { class: "machines-line", text: "Workbench reaches the machine through SSH. Model traffic travels inside that connection, so only the SSH port needs to be reachable." }),
        form.el,
      );
      d.focus = form.focus;
    } else {
      const form = identityForm(host, d);
      d.focusName = form.focus;
      panel.append(
        statusBlock(host, d),
        form.el,
        serversBlock(host, d),
        tunnelsBlock(host, d),
        managedBlock(host, d),
        sessionsBlock(host, d),
        newSessionBlock(host, d),
        pairingBlock(host, d),
      );
    }
    parts.detail.append(panel);
    detail = d;
    d.paint();
    return d;
  }

  function open(host, focusDetail, { probe: probeNow = false } = {}) {
    if (!host) return;
    adding = null;
    const changed = !detail || detail.host !== host;
    selected = host;
    if (changed) buildDetail(host);
    level = "detail";
    paintLevel();
    paintList();
    const c = entry(host);
    if (probeNow || (!c.probe && !c.probeError && !c.probing)) probe(host);
    if (focusDetail && narrow.matches && detail) detail.focus();
  }

  function startAdd(host) {
    adding = { host: typeof host === "string" ? host.trim() : "" };
    buildDetail("");
    level = "detail";
    paintLevel();
    paintList();
    if (detail) detail.focus();
  }

  function showList(host) {
    level = "list";
    paintLevel();
    const node = host && listApi ? listApi.get(host) : null;
    const target = node ? node.querySelector(".row-main") : parts.addButton;
    if (target) target.focus();
  }

  async function load() {
    listError = null;
    paintList();
    const [list] = await Promise.allSettled([
      AW.store.refresh("machines", { force: !loaded }),
      AW.request("machine-systems").then((value) => {
        for (const [host, row] of Object.entries(value && typeof value === "object" ? value : {})) if (row && !systems[host]) systems[host] = row;
      }),
      AW.store.refresh("forwards", { force: true }),
      (AW.state.accounts || []).length ? Promise.resolve() : AW.store.refresh("accounts"),
    ]);
    if (list.status === "rejected") listError = AW.asError(list.reason);
    else loaded = true;
    paintList();
    if (detail) detail.paint();
  }

  function hostFor(value) {
    if (typeof value !== "string" || !value.trim()) return "";
    const text = value.trim();
    if (hostsOf().includes(text)) return text;
    const tunnel = (AW.state.forwards || []).find((row) => row && row.connection_id === text);
    return tunnel && hostsOf().includes(tunnel.host) ? tunnel.host : "";
  }

  function mount(view) {
    root = view;
    const addButton = ui.button("Add machine…", { icon: "plus", onClick: () => startAdd("") });
    const listHost = el("div", { id: "machine-list", class: "machines-list", role: "group", attrs: { "aria-label": "Saved machines" } });
    const noneYet = el("p", { class: "machines-quiet machines-none", text: "No machines saved yet.", hidden: true });
    const listSkeleton = ui.skeleton(3, "machines");
    const listErrors = errorSlot();
    const back = ui.button("‹ Machines", { kind: "link", onClick: () => showList(selected) });
    const detailHost = el("div", { id: "machine-detail", class: "machines-detail" });
    const side = el("div", { class: "machines-side" }, el("div", { class: "machines-side-head" }, addButton), listSkeleton, listErrors.el, noneYet, listHost);
    const layout = el("div", { class: "split machines-layout", data: { level: "both" } }, side, el("div", { class: "machines-main" }, back, detailHost));
    const empty = ui.empty("No machines yet. Add one to use a model server on another computer through SSH.", [{ label: "Add machine…", primary: true, run: () => startAdd("") }]);
    empty.hidden = true;
    const notices = el("div", { class: "machines-notices" });
    view.replaceChildren(
      el("h1", { class: "title", id: "machines-title", tabindex: -1, text: "Machines" }),
      el("p", { class: "lede", text: "Computers you reach over SSH. Model traffic travels inside the SSH connection; only the SSH port needs to be reachable." }),
      notices,
      empty,
      layout,
    );
    parts = { layout, side, list: listHost, noneYet, listSkeleton, listError: listErrors, back, detail: detailHost, empty, notices, addButton };
    listApi = ui.list(listHost, { key: (host) => host, render: machineRow });
    narrow.addEventListener("change", () => {
      if (!narrow.matches && !detail && hostsOf().length) open(selected && hostsOf().includes(selected) ? selected : hostsOf()[0], false);
      paintLevel();
    });
    AW.store.on(["machines", "forwards", "accounts"], () => {
      if (AW.router.current().id !== "machines") return;
      paintList();
      if (detail) {
        if (detail.host && !hostsOf().includes(detail.host) && AW.state.machines) return;
        detail.paint();
      }
    });
    AW.store.on("folder", () => detail && detail.paint());
    if (AW.jobs)
      AW.jobs.on((record) => {
        if (!detail || !/^(machine-|term-remote)/.test(record.action)) return;
        detail.paint();
      });
    paintLevel();
  }

  async function show(params) {
    const p = params || {};
    if (detail) detail.paint();
    await load();
    if (p.add) {
      startAdd(p.host || "");
      return;
    }
    const wanted = hostFor(p.select) || hostFor(p.host);
    if (wanted) {
      open(wanted, true);
      return;
    }
    if (typeof p.host === "string" && p.host.trim() && !wanted) {
      startAdd(p.host);
      return;
    }
    const hosts = hostsOf();
    if (adding !== null) return;
    if (selected && hosts.includes(selected)) {
      if (!detail || detail.host !== selected) open(selected, false);
      else {
        paintLevel();
        detail.paint();
      }
      if (narrow.matches) level = "list";
      paintLevel();
      return;
    }
    selected = "";
    closeDetail();
    if (hosts.length && !narrow.matches) open(hosts.sort((a, b) => labelOf(a).localeCompare(labelOf(b)))[0], false);
    level = "list";
    paintLevel();
    paintList();
  }

  AW.router.register("machines", { title: "Machines", mount, show });
  AW.fix.register("machine.reconnect", (id, ctx) => reconnect(id, ctx && ctx.origin));
  AW.commands.register({ id: "machines.add", title: "Add a machine over SSH…", group: "Actions", keywords: "ssh remote gpu server", run: () => AW.router.go("machines", { add: true }) });
})();
