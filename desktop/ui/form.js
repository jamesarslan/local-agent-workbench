// The launch form: its state, the 0.3 request shape, what the folder remembers and the binding of the static Launch controls (ui.md 9.4).
// Beyond ui.md 9.4: AW.form.markSaved() records the current choices as remembered after a launch, and AW.form.on listeners are also called
// with an empty key list when only the saved state or the "Open in" choice changed.
(function (root) {
  "use strict";
  const AW = (root.AW = root.AW || {});
  const LOCAL = "Managed local";
  const NATIVE = "Native login";
  const KEYS = ["workspace", "harness", "provider", "connection_id", "auth_provider", "preset", "search", "firecrawl", "blender", "desktop", "image_route", "plugins", "remote_model", "endpoint"];
  const BOOLS = ["search", "firecrawl", "blender", "desktop"];
  const REMEMBERED = ["harness", "provider", "connection_id", "preset", "remote_model", "image_route", "plugins", ...BOOLS];
  const listeners = new Set();
  const typedModels = new Map();
  const blank = () => Object.fromEntries(KEYS.map((key) => [key, BOOLS.includes(key) ? false : ""]));
  let state = { ...blank(), provider: LOCAL, image_route: "auto" };
  let where = null;

  const config = () => (AW.state && AW.state.config) || {};
  const settings = () => config().settings || {};
  const accounts = () => (AW.state && Array.isArray(AW.state.accounts) ? AW.state.accounts : []);
  const sourceOf = (values) => values.connection_id || values.provider || LOCAL;

  function rowFor(values) {
    if (values.connection_id) return accounts().find((row) => row.connection_id === values.connection_id) || null;
    if (values.provider && values.provider !== LOCAL && values.provider !== NATIVE) return accounts().find((row) => row.connection_id === "legacy-" + values.provider) || null;
    return null;
  }

  function signature() {
    const c = config();
    return JSON.stringify(REMEMBERED.map((key) => (key === "remote_model" ? state[key].trim() : key === "desktop" ? Boolean(c.windows) && state[key] : state[key])));
  }
  let saved = signature();

  function clean(key, value) {
    if (BOOLS.includes(key)) return typeof value === "boolean" ? value : undefined;
    if (typeof value === "string") return value;
    return typeof value === "number" && Number.isFinite(value) ? String(value) : undefined;
  }

  function write(patch) {
    const changed = [];
    for (const key of KEYS) {
      if (!Object.hasOwn(patch, key)) continue;
      const value = clean(key, patch[key]);
      if (value === undefined || state[key] === value) continue;
      state[key] = value;
      changed.push(key);
    }
    return changed;
  }

  function notify(changed) {
    const current = AW.form.get();
    for (const fn of [...listeners]) {
      try {
        fn(current, changed);
      } catch (thrown) {
        console.error(thrown);
      }
    }
  }

  function valid(values) {
    const c = config();
    const base = settings();
    const out = {};
    for (const key of KEYS) {
      const value = clean(key, values[key]);
      if (value !== undefined) out[key] = value;
    }
    if (Array.isArray(c.agents) && !c.agents.includes(out.harness)) out.harness = c.agents.includes(base.harness) ? base.harness : c.agents[0] || "";
    if (Array.isArray(c.providers) && !c.providers.includes(out.provider)) {
      out.provider = c.providers.includes(base.provider) ? base.provider : LOCAL;
      out.connection_id = "";
      out.auth_provider = "";
    }
    if (out.preset && !(c.presets && Object.hasOwn(c.presets, out.preset))) out.preset = "";
    if (!out.image_route) out.image_route = "auto";
    return out;
  }

  AW.form = {
    get() {
      const c = config();
      const row = rowFor(state);
      const provider = row ? row.provider : state.provider || LOCAL;
      return {
        workspace: state.workspace.trim(),
        harness: state.harness,
        provider,
        connection_id: row ? row.connection_id : state.connection_id,
        auth_provider: row ? row.auth_provider || "" : provider === NATIVE ? state.auth_provider : "",
        preset: state.preset,
        search: state.search,
        firecrawl: state.firecrawl,
        blender: state.blender,
        desktop: Boolean(c.windows) && state.desktop,
        image_route: state.image_route || "auto",
        plugins: state.plugins,
        remote_model: state.remote_model.trim(),
        endpoint: (row && row.endpoint) || settings().endpoint || state.endpoint,
      };
    },
    set(patch) {
      const next = { ...(patch || {}) };
      const from = sourceOf(state);
      if (next.connection_id && !Object.hasOwn(next, "provider")) {
        const row = accounts().find((item) => item.connection_id === next.connection_id);
        if (row) next.provider = row.provider;
      } else if (Object.hasOwn(next, "provider") && !Object.hasOwn(next, "connection_id")) next.connection_id = "";
      const target = { provider: next.provider ?? state.provider, connection_id: next.connection_id ?? state.connection_id };
      if (sourceOf(target) !== from) {
        typedModels.set(from, state.remote_model);
        const row = rowFor(target);
        if (!Object.hasOwn(next, "remote_model")) next.remote_model = typedModels.get(sourceOf(target)) ?? ((row && row.remote_model) || "");
        if (!Object.hasOwn(next, "auth_provider")) next.auth_provider = (row && row.auth_provider) || "";
      }
      const changed = write(next);
      if (changed.length) notify(changed);
      return changed;
    },
    apply(recipe, draft) {
      const before = state;
      const base = { ...settings(), preset: config().selected || "" };
      state = { ...blank(), provider: LOCAL, image_route: "auto" };
      typedModels.clear();
      write(valid({ ...base, ...(recipe || {}) }));
      saved = signature();
      if (draft) write(valid({ ...state, ...draft, workspace: state.workspace }));
      notify(KEYS.filter((key) => before[key] !== state[key]));
    },
    on(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    connection: () => rowFor(state),
    provider() {
      const row = rowFor(state);
      return row ? row.provider : state.provider || LOCAL;
    },
    dirty: () => signature() !== saved,
    async save() {
      const sent = AW.form.get();
      const mark = signature();
      await AW.request("launch-save", sent);
      saved = mark;
      notify([]);
      for (const key of ["folder", "folders", "config"]) AW.store.refresh(key).catch(() => {});
      return true;
    },
    markSaved() {
      saved = signature();
      notify([]);
    },
    where(value) {
      const read = () => where || (AW.storage.get("launch.where", "dock") === "window" ? "window" : "dock");
      if ((value === "dock" || value === "window") && value !== read()) {
        where = value;
        AW.storage.set("launch.where", value);
        notify([]);
      }
      return read();
    },
    bind() {
      const $ = AW.$;
      const texts = { preset: "preset", remote: "remote_model", agent: "harness", "image-route": "image_route" };
      for (const [id, key] of Object.entries(texts)) {
        const control = $(id);
        if (control) control.addEventListener(id === "remote" ? "input" : "change", () => AW.form.set({ [key]: control.value }));
      }
      for (const key of BOOLS) {
        const control = $(key);
        if (control) control.addEventListener("change", () => AW.form.set({ [key]: control.checked }));
      }
      const source = $("source");
      if (source)
        source.addEventListener("change", () => {
          const value = source.value;
          if (value === LOCAL || value === NATIVE) AW.form.set({ provider: value, connection_id: "" });
          else if (accounts().some((row) => row.connection_id === value)) AW.form.set({ connection_id: value });
        });
      for (const id of ["where-dock", "where-window"]) {
        const control = $(id);
        if (control) control.addEventListener("change", () => control.checked && AW.form.where(control.value));
      }
      const paint = () => {
        const current = AW.form.get();
        for (const [id, key] of Object.entries(texts)) {
          const control = $(id);
          if (control && control.value !== state[key]) control.value = state[key];
        }
        for (const key of BOOLS) if ($(key)) $(key).checked = state[key];
        if (source) source.value = current.connection_id || current.provider;
        const chosen = $(AW.form.where() === "window" ? "where-window" : "where-dock");
        if (chosen) chosen.checked = true;
      };
      listeners.add(paint);
      paint();
    },
  };
})(typeof window === "undefined" ? globalThis : window);
