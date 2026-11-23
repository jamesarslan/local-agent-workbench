// Shared state, its refresh map, the 5 s status poll, window-focus refreshes and the current project folder (ui.md 3, 9.4).
// Keys beyond the list in ui.md 9.4, all read by AW.readiness: `unreachable` ({connection_id: error} from a failed endpoint-refresh, written by the
// Launch and Connections packages with AW.store.set), `outcomes` ({agent: message} after a failed install, written by AW.tools) and `subscription`
// (the studio-subscriptions sentence). AW.store.focus() runs the window-focus refreshes; AW.store.poll.now() runs one poll tick.
(function (root) {
  "use strict";
  const AW = (root.AW = root.AW || {});
  const LOCAL = "Managed local";
  const NATIVE = "Native login";
  const POLL_MS = 5000;
  const SCAN_STALE_MS = 10000;
  const TOOLS_STALE_MS = 60000;
  const TOOL_KEYS = ["search", "firecrawl", "blender", "desktop", "plugins"];
  const IMAGE_ROUTES = ["auto", "selected", "pareto", "manual"];
  const QUIET_WHEN_SAME = new Set(["live", "sessions", "exits"]);

  AW.state = {
    config: null, live: null, sessions: [], exits: [], accounts: [], scan: null, forwards: [], machines: null, folders: [], folder: null,
    resolve: null, compat: null, consent: null, context: null, match: null, hardware: null, engine: null, recommend: null, tools: null,
    sourceTable: null, progress: null, bridge: null, kitHint: null, unreachable: {}, outcomes: {}, subscription: null,
  };

  const listeners = new Map();
  const times = new Map();
  const errors = new Map();
  const flights = new Map();
  const serial = new Map();
  const form = () => AW.form.get();
  const place = () => {
    const f = form();
    return f.workspace && f.harness ? { workspace: f.workspace, harness: f.harness } : null;
  };
  const source = () => {
    const f = form();
    return { provider: f.provider, connection_id: f.connection_id, endpoint: f.endpoint };
  };

  let kits = null;
  function kitList() {
    if (!kits)
      kits = AW.request("kit-list").catch((thrown) => {
        kits = null;
        throw thrown;
      });
    return kits;
  }

  const LOADERS = {
    config: { run: () => AW.request("app-state") },
    live: { run: () => AW.request("app-status") },
    sessions: { run: () => AW.request("sessions-running", {}) },
    accounts: { run: () => AW.request("account-list") },
    scan: { run: () => AW.request("endpoint-scan"), keep: (value) => ({ ...value, received: Date.now() }) },
    forwards: { run: () => AW.request("machine-forwards") },
    machines: { run: () => AW.request("machine-list") },
    folders: { run: () => AW.request("folder-list") },
    folder: {
      args: () => (form().workspace ? { workspace: form().workspace } : null),
      run: (args) => AW.request("folder-get", args),
    },
    resolve: {
      args() {
        const at = place();
        const f = form();
        return at ? { ...at, search: f.search, firecrawl: f.firecrawl, blender: f.blender, desktop: f.desktop, plugins: f.plugins } : null;
      },
      run: (args) => AW.request("project-setup-resolve", args),
    },
    compat: { args: source, run: (args) => AW.request("source-compat", args) },
    consent: { args: place, run: (args) => AW.request("project-setup-consent", args) },
    context: {
      args() {
        const f = form();
        if (f.provider === LOCAL || f.provider === NATIVE || !f.remote_model) return null;
        return { ...source(), remote_model: f.remote_model };
      },
      run: (args) => AW.request("source-context", args),
    },
    match: {
      args: () => (form().provider === LOCAL ? form() : null),
      run: (args) => AW.request("server-match", args),
    },
    hardware: { run: (args, force) => AW.request("model-hardware", force ? { refresh: true } : {}) },
    engine: { run: () => AW.request("engine-info") },
    recommend: { run: (args, force) => AW.request("model-recommend", force ? { refresh: true } : {}) },
    tools: { run: () => AW.request("studio-check") },
    sourceTable: { run: () => AW.request("source-table") },
    progress: { run: () => AW.request("onboarding-state", { sections: [] }), keep: (value) => (value && value.progress) || null },
    subscription: { run: () => AW.request("studio-subscriptions") },
    kitHint: {
      args: () => (form().workspace ? { workspace: form().workspace } : null),
      async run(args) {
        const [found, list] = await Promise.all([AW.request("kit-detect", args), kitList()]);
        const id = found && typeof found.suggested === "string" ? found.suggested : "";
        const kit = (Array.isArray(list) ? list : []).find((row) => row.id === id);
        if (!kit || id === "blank") return null;
        return { id, label: kit.label || id, alternatives: found.alternatives || [], evidence: (found.evidence && found.evidence[id]) || [] };
      },
    },
  };

  function emit(key, value) {
    for (const fn of [...(listeners.get(key) || [])]) {
      try {
        fn(value, key);
      } catch (thrown) {
        console.error(thrown);
      }
    }
  }

  function put(key, value) {
    const quiet = QUIET_WHEN_SAME.has(key) && !errors.has(key) && times.has(key) && JSON.stringify(AW.state[key]) === JSON.stringify(value);
    AW.state[key] = value;
    times.set(key, Date.now());
    errors.delete(key);
    if (!quiet) emit(key, value);
  }

  function keep(key, value) {
    if (key !== "sessions") {
      put(key, value);
      return value;
    }
    const rows = value && Array.isArray(value.sessions) ? value.sessions : [];
    put("exits", value && Array.isArray(value.exits) ? value.exits : []);
    put("sessions", rows);
    return rows;
  }

  function drop(key) {
    serial.set(key, (serial.get(key) || 0) + 1);
    flights.delete(key);
    if (AW.state[key] !== null || errors.has(key)) put(key, null);
  }

  const quiet = (key, options) => AW.store.refresh(key, options).catch(() => {});
  const lazyResolve = AW.debounce(() => quiet("resolve"), 300);
  const lazyContext = AW.debounce(() => quiet("context"), 300);

  function extras() {
    const f = form();
    if (f.provider === LOCAL) for (const key of ["engine", "recommend"]) if (!times.has(key) && !flights.has(key)) quiet(key);
    if (f.provider === NATIVE && f.harness === "Codex" && AW.store.age("subscription") > TOOLS_STALE_MS) quiet("subscription");
  }

  function derive() {
    for (const key of ["resolve", "compat", "consent", "match", "context", "kitHint"]) quiet(key);
    extras();
  }

  function react(_form, changed) {
    if (!AW.state.config || !Array.isArray(changed) || !changed.length) return;
    const has = (...keys) => keys.some((key) => changed.includes(key));
    if (has("provider", "connection_id")) {
      for (const key of ["compat", "match", "context"]) drop(key);
      for (const key of ["compat", "match", "context"]) quiet(key);
    } else {
      if (has("preset")) drop("match");
      if (has("preset", "harness")) quiet("match");
      if (has("remote_model")) lazyContext();
    }
    if (has("workspace", "harness")) {
      for (const key of ["resolve", "consent"]) drop(key);
      for (const key of ["resolve", "consent"]) quiet(key);
    } else if (has(...TOOL_KEYS)) lazyResolve();
    if (has("workspace")) {
      drop("kitHint");
      quiet("kitHint");
    }
    if (has("provider", "connection_id", "harness")) extras();
  }

  function onFocus() {
    if (!AW.state.config) return;
    if (form().workspace) {
      quiet("folder");
      quiet("resolve");
    }
    const view = AW.router ? AW.router.current().id : "";
    if (["launch", "overview", "accounts"].includes(view) && AW.store.age("scan") > SCAN_STALE_MS) quiet("scan");
    if (AW.store.age("tools") > TOOLS_STALE_MS) quiet("tools");
  }

  const hidden = () => Boolean(root.document && root.document.hidden);
  let pollTimer = null;
  let pollBusy = false;
  async function tick() {
    if (pollBusy || hidden()) return;
    pollBusy = true;
    const before = AW.state.live ? AW.state.live.label + "\n" + AW.state.live.model : "";
    await Promise.allSettled([AW.store.refresh("live"), AW.store.refresh("sessions")]);
    pollBusy = false;
    const after = AW.state.live ? AW.state.live.label + "\n" + AW.state.live.model : "";
    if (before !== after && AW.state.config) quiet("match");
  }

  AW.store = {
    get: (key) => AW.state[key],
    set(key, value) {
      AW.state[key] = value;
      times.set(key, Date.now());
      errors.delete(key);
      emit(key, value);
    },
    on(keyOrKeys, fn) {
      const keys = Array.isArray(keyOrKeys) ? keyOrKeys : [keyOrKeys];
      for (const key of keys) {
        if (!listeners.has(key)) listeners.set(key, new Set());
        listeners.get(key).add(fn);
      }
      return () => keys.forEach((key) => listeners.get(key).delete(fn));
    },
    refresh(key, { force = false } = {}) {
      const loader = LOADERS[key];
      if (!loader) return Promise.reject({ message: "Agent Workbench has nothing to load for “" + key + "”.", code: "internal" });
      let args;
      try {
        args = loader.args ? loader.args() : {};
      } catch (thrown) {
        return Promise.reject(AW.asError(thrown));
      }
      if (args === null) {
        drop(key);
        return Promise.resolve(null);
      }
      const signature = JSON.stringify(args);
      const flying = flights.get(key);
      if (flying && flying.signature === signature && !force) return flying.promise;
      const mine = (serial.get(key) || 0) + 1;
      serial.set(key, mine);
      const latest = () => serial.get(key) === mine;
      const promise = Promise.resolve()
        .then(() => loader.run(args, force))
        .then(
          (value) => {
            const kept = loader.keep ? loader.keep(value) : value;
            if (!latest()) return key === "sessions" ? (kept && kept.sessions) || [] : kept;
            flights.delete(key);
            return keep(key, kept);
          },
          (thrown) => {
            const error = AW.asError(thrown);
            if (latest()) {
              flights.delete(key);
              errors.set(key, error);
              emit(key, AW.state[key]);
            }
            throw error;
          },
        );
      flights.set(key, { signature, promise });
      return promise;
    },
    age: (key) => (times.has(key) ? Date.now() - times.get(key) : Infinity),
    error: (key) => errors.get(key) || null,
    async hydrate() {
      const before = AW.state.config;
      const config = await AW.store.refresh("config", { force: true });
      const settings = config.settings || {};
      const folder = config.folder || null;
      const workspace = (folder && folder.path) || settings.workspace || "";
      const exists = Boolean(folder && folder.key);
      if (Array.isArray(config.accounts)) put("accounts", config.accounts);
      drafts.delete(workspace);
      const recipe = exists && folder.known ? folder.recipe : (exists && legacy([workspace, settings.workspace], config)) || {};
      AW.state.folder = folder;
      AW.form.apply({ ...recipe, workspace });
      if (before && before.settings && before.settings.workspace === settings.workspace) {
        const was = { ...before.settings, preset: before.selected || "" };
        const now = { ...settings, preset: config.selected || "" };
        const moved = Object.keys(now).filter((key) => key !== "workspace" && was[key] !== now[key]);
        if (moved.length) AW.form.set(Object.fromEntries(moved.map((key) => [key, now[key]])));
      }
      AW.store.set("folder", folder);
      derive();
      return config;
    },
    poll: {
      start() {
        if (pollTimer) return;
        tick();
        pollTimer = setInterval(tick, POLL_MS);
      },
      stop() {
        clearInterval(pollTimer);
        pollTimer = null;
      },
      now: tick,
    },
    focus: onFocus,
  };

  const drafts = new Map();
  const selecting = AW.ticket();
  const plain = (value, max) => typeof value === "string" && value.length <= max && !/[\u0000-\u001f\u007f]/.test(value);

  function legacy(paths, config) {
    let found = null;
    for (const path of [...new Set(paths.filter(Boolean))]) {
      const key = "project/" + path;
      const text = AW.storage.get(key, null);
      if (text === null) continue;
      AW.storage.remove(key);
      if (found) continue;
      try {
        const raw = JSON.parse(text);
        if (raw && typeof raw === "object" && !Array.isArray(raw)) found = raw;
      } catch {
        found = null;
      }
    }
    if (!found) return null;
    const out = {};
    const settings = (config && config.settings) || {};
    if ((config.agents || []).includes(found.harness)) out.harness = found.harness;
    if ((config.providers || []).includes(found.provider)) {
      out.provider = found.provider;
      out.connection_id = plain(found.connection_id, 120) ? found.connection_id : "";
      out.auth_provider = plain(found.auth_provider, 80) ? found.auth_provider : "";
    }
    if (found.preset === "" || (plain(found.preset, 120) && config.presets && Object.hasOwn(config.presets, found.preset))) out.preset = found.preset;
    for (const key of ["search", "firecrawl", "blender", "desktop"]) if (typeof found[key] === "boolean") out[key] = found[key];
    if (IMAGE_ROUTES.includes(found.image_route)) out.image_route = found.image_route;
    if (typeof found.plugins === "string") {
      const known = config.registered_plugins || [];
      out.plugins = found.plugins.split(",").filter((name) => known.includes(name)).join(",");
    }
    if (plain(found.remote_model, 300)) out.remote_model = found.remote_model;
    return Object.keys(out).length ? { ...settings, ...out } : null;
  }

  AW.project = {
    current: () => (AW.state.folder && AW.state.folder.key && AW.state.folder.path) || "",
    async select(path) {
      const mine = selecting.next();
      const from = AW.project.current();
      const asked = String(path ?? "").trim();
      const folder = await AW.request("folder-get", { workspace: asked });
      if (!selecting.current(mine)) return folder;
      if (from && from !== folder.path) {
        if (AW.form.dirty()) drafts.set(from, form());
        else drafts.delete(from);
      }
      const draft = from === folder.path ? form() : drafts.get(folder.path) || null;
      const recipe = folder.known ? folder.recipe : draft ? {} : legacy([folder.path, asked], AW.state.config || {}) || {};
      AW.state.folder = folder;
      AW.form.apply({ ...recipe, workspace: folder.path }, draft);
      AW.store.set("folder", folder);
      derive();
      return folder;
    },
    async pick() {
      const path = await AW.request("folder-pick");
      if (!path) return null;
      const folder = await AW.project.select(path);
      return folder.path;
    },
    scratch() {
      const config = AW.state.config;
      return config && config.data ? config.data + (config.windows ? "\\" : "/") + "workspace" : "";
    },
    name: (path) => AW.fmt.folder(path === undefined ? AW.project.current() || form().workspace : path),
    // Remembered folders (folder-list), plus the current folder and the recent folders from app-state when they are not remembered yet.
    known() {
      const rows = (Array.isArray(AW.state.folders) ? AW.state.folders : []).filter((row) => row && row.path);
      const has = (path) => rows.some((row) => row.path === path || row.key === path);
      const bare = (key, path) => ({ key, path, exists: true, harness: "", provider: "", model_label: "", kit: "", profile: "", updated: "" });
      const folder = AW.state.folder;
      const out = [...rows];
      if (folder && folder.key && folder.path && !has(folder.path) && !has(folder.key)) out.unshift({ ...bare(folder.key, folder.path), kit: typeof folder.kit === "string" ? folder.kit : "" });
      const recent = AW.state.config && Array.isArray(AW.state.config.recent) ? AW.state.config.recent : [];
      for (const path of recent) if (typeof path === "string" && path && !out.some((row) => row.path === path || row.key === path)) out.push(bare(path, path));
      return out;
    },
  };

  const AFTER_JOB = {
    "engine-install": ["engine"],
    "model-download": ["recommend", "config"],
    "server-start": ["live", "match"],
    "server-stop": ["live", "match"],
    "server-adopt": ["live", "match"],
    "studio-setup": ["tools", "config"],
  };
  function settled(record, kind) {
    if (kind === "started" || kind === "progress" || !AW.state.config) return;
    for (const key of AFTER_JOB[record.action] || []) quiet(key);
  }

  if (AW.shell)
    AW.shell.register(() => {
      if (AW.jobs) AW.jobs.on(settled);
      AW.form.on(react);
      root.addEventListener("focus", onFocus);
      root.document.addEventListener("visibilitychange", () => {
        if (!hidden() && pollTimer) tick();
      });
    });
})(typeof window === "undefined" ? globalThis : window);
