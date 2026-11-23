// Model install (AW.models): the engine for local models when it is missing, the catalog download and the saved setup, each as a task with
// progress and Cancel (ui.md 7, 9.8). A model whose licence carries a note asks before its download starts.
// Beyond ui.md 9.8: AW.models.engine({origin, update}) installs or updates the engine alone; AW.models.statusHelp(entry) is the help text
// of an entry's Measured or Suggested chip; AW.models.chip(entry) builds that chip.
(function () {
  "use strict";
  const AW = window.AW;
  const flights = new Map();

  function engine({ origin, update = false } = {}) {
    const title = update ? "Updating the engine for local models" : "Installing the engine for local models";
    return AW.jobs.run("engine-install", update ? { update: true } : {}, { title, origin, key: "engine" }).then((value) => {
      AW.store.refresh("engine", { force: true }).catch(() => {});
      return value;
    });
  }

  async function lookup(id) {
    const recommend = AW.state.recommend;
    const known = recommend ? [recommend.pick, ...(Array.isArray(recommend.alternatives) ? recommend.alternatives : [])] : [];
    const found = known.find((entry) => entry && entry.id === id);
    if (found) return found;
    const catalog = await AW.request("model-catalog");
    const entry = (catalog && Array.isArray(catalog.entries) ? catalog.entries : []).find((row) => row && row.id === id);
    if (!entry) throw { message: "That model is no longer in the catalog. Open Model library and choose another one." };
    return entry;
  }

  async function engineInfo() {
    if (AW.state.engine && AW.store.age("engine") < 60000) return AW.state.engine;
    try {
      return await AW.store.refresh("engine", { force: true });
    } catch {
      return AW.state.engine || null;
    }
  }

  async function run(entry, origin) {
    const item = entry && typeof entry === "object" ? entry : await lookup(String(entry || ""));
    const label = item.label || item.id;
    const onDisk = Boolean(item.installed && item.installed.installed);
    if (!onDisk && typeof item.licence_note === "string" && item.licence_note) {
      const size = AW.fmt.bytes(item.download_bytes);
      const ok = await AW.ui.confirm({ text: "Download " + label + (size ? " (" + size + ")" : "") + "? " + item.licence_note, ok: "Download" });
      if (!ok) throw { message: "The download of " + label + " was not started.", code: "cancelled" };
    }
    const info = await engineInfo();
    if (info && info.supported === false) throw { message: info.reason || "The engine for local models cannot run on this computer. Attach a running server or add a hosted API instead." };
    const needEngine = Boolean(info && info.engine_ready === false);
    const engineJob = needEngine ? engine({}) : Promise.resolve(null);
    const download = AW.jobs.run("model-download", { id: item.id }, { title: "Downloading " + label, origin, key: "model:" + item.id });
    const [installed, fetched] = await Promise.allSettled([engineJob, download]);
    if (fetched.status === "rejected") throw fetched.reason;
    if (installed.status === "rejected") throw installed.reason;
    const result = fetched.value || {};
    const name = typeof result.preset_name === "string" && result.preset_name ? result.preset_name : label;
    await AW.request("studio-save", { ...(result.settings || {}), name });
    await AW.store.refresh("config").catch(() => {});
    AW.store.refresh("recommend").catch(() => {});
    return { ...result, preset_name: name, label };
  }

  function install(entry, { origin } = {}) {
    const id = entry && typeof entry === "object" ? entry.id : String(entry || "");
    if (flights.has(id)) return flights.get(id);
    const promise = run(entry, origin).finally(() => flights.delete(id));
    flights.set(id, promise);
    return promise;
  }

  function recommended() {
    if (AW.state.recommend && !AW.store.error("recommend")) return Promise.resolve(AW.state.recommend);
    return AW.store.refresh("recommend");
  }

  function statusHelp(entry) {
    if (entry && entry.status === "measured") return "A result file in this project measures this exact model file.";
    const evidence = entry && entry.evidence;
    if (evidence && typeof evidence === "object" && typeof evidence.machine === "string" && evidence.machine) {
      const did = evidence.tool_call === "passed" ? "it loads, answers and calls a tool" : "it loads and answers";
      return "Chosen for this class of hardware. Tried on " + evidence.machine + ": " + did + ".";
    }
    return "Chosen for this class of hardware. This project has not measured it.";
  }

  function chip(entry) {
    const measured = entry && entry.status === "measured";
    return AW.ui.chip(measured ? "Measured" : "Suggested", measured ? "ok" : null, { help: statusHelp(entry) });
  }

  AW.models = { install, recommended, engine, statusHelp, chip };

  AW.fix.register("model.install", (id, ctx) => install(String(id || ""), { origin: ctx && ctx.origin }));
  AW.fix.register("engine.install", (arg, ctx) => engine({ origin: ctx && ctx.origin }));
})();
