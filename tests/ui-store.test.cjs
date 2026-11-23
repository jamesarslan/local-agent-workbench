// AW.form and AW.project in desktop/ui/form.js and store.js: the 0.3 form keys, drafts per folder, the order of preference and no writes on selection.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ui = path.join(__dirname, "..", "desktop", "ui");
const KEYS = ["workspace", "harness", "provider", "connection_id", "auth_provider", "preset", "search", "firecrawl", "blender", "desktop", "image_route", "plugins", "remote_model", "endpoint"];
const AGENTS = ["OMP", "Pi", "Claude Code", "Codex", "Hermes", "opencode"];
const SETTINGS = {
  workspace: "/code/alpha", image_route: "auto", connection_id: "", auth_provider: "", harness: "OMP", provider: "Managed local",
  remote_model: "", endpoint: "http://127.0.0.1:8000/v1", search: true, firecrawl: false, blender: false, desktop: false, plugins: "",
};

function folderView(path, recipe) {
  return { key: path, path, known: Boolean(recipe), recipe: recipe || {}, stale: [], model_label: "", kit: "", profile: "", prompt: null, project_mcp: {}, updated: "" };
}

function world({ folders = {}, stored = {} } = {}) {
  const calls = [];
  const storage = new Map(Object.entries(stored));
  const table = {
    "/code/alpha": folderView("/code/alpha", { harness: "Codex", search: false }),
    "/code/beta": folderView("/code/beta", { harness: "Pi", firecrawl: true }),
    "/code/gamma": folderView("/code/gamma", null),
    "/code/delta": folderView("/code/delta", null),
    ...folders,
  };
  const config = () => ({
    settings: { ...SETTINGS }, presets: { Daily: { model: "a.gguf", context: 32768, engine: "Auto" } }, selected: "", current: { model: "a.gguf", context: 32768, engine: "Auto" },
    providers: ["Managed local", "Attached endpoint", "OpenAI", "Groq", "Native login"], accounts: [], agents: AGENTS, windows: false, registered_plugins: ["jupyter"],
    cli_present: {}, folder: table[SETTINGS.workspace], data: "/data", warnings: [],
  });
  const request = async (action, data) => {
    calls.push({ action, data: JSON.parse(JSON.stringify(data || {})) });
    if (action === "app-state") return config();
    if (action === "folder-get") {
      const found = table[String(data.workspace).replace(/\/+$/, "")];
      if (!found) throw { message: "Choose an existing project folder." };
      return JSON.parse(JSON.stringify(found));
    }
    if (action === "launch-save") return true;
    if (action === "sessions-running") return { sessions: [], exits: [] };
    if (action === "kit-list") return [];
    if (action === "kit-detect") return { suggested: "blank", alternatives: [], evidence: {} };
    return {};
  };
  const context = vm.createContext({
    console,
    setTimeout,
    clearTimeout,
    workbench: { request, subscribe: () => () => {} },
    localStorage: {
      getItem: (key) => (storage.has(key) ? storage.get(key) : null),
      setItem: (key, value) => storage.set(key, String(value)),
      removeItem: (key) => storage.delete(key),
    },
  });
  for (const file of ["core.js", "store.js", "form.js"]) vm.runInContext(fs.readFileSync(path.join(ui, file), "utf8"), context, { filename: file });
  return { AW: context.AW, calls, storage };
}

const plain = (value) => JSON.parse(JSON.stringify(value));
const settle = () => new Promise((resolve) => setTimeout(resolve, 20));

test("AW.form.get() has exactly the 0.3 launch form keys", async () => {
  const { AW } = world();
  assert.deepEqual(Object.keys(AW.form.get()), KEYS);
  await AW.store.hydrate();
  const form = AW.form.get();
  assert.deepEqual(Object.keys(form), KEYS);
  assert.equal(form.workspace, "/code/alpha");
  assert.equal(form.harness, "Codex");
  assert.equal(form.search, false);
  assert.equal(typeof form.desktop, "boolean");
});

test("the saved folder's recipe fills the form at start, the global settings fill the rest", async () => {
  const { AW } = world();
  await AW.store.hydrate();
  assert.equal(AW.project.current(), "/code/alpha");
  assert.equal(AW.form.get().harness, "Codex");
  assert.equal(AW.form.get().provider, "Managed local");
  assert.equal(AW.form.dirty(), false);
});

test("each folder keeps its unsaved choices for the run; a draft wins over the recipe", async () => {
  const { AW } = world();
  await AW.store.hydrate();
  AW.form.set({ harness: "Claude Code", blender: true });
  assert.equal(AW.form.dirty(), true);
  await AW.project.select("/code/beta");
  assert.equal(AW.form.get().workspace, "/code/beta");
  assert.equal(AW.form.get().harness, "Pi");
  assert.equal(AW.form.get().firecrawl, true);
  assert.equal(AW.form.get().blender, false);
  assert.equal(AW.form.dirty(), false);
  await AW.project.select("/code/alpha");
  assert.equal(AW.form.get().harness, "Claude Code");
  assert.equal(AW.form.get().blender, true);
  assert.equal(AW.form.dirty(), true);
  await AW.project.select("/code/beta");
  assert.equal(AW.form.get().harness, "Pi");
});

test("an unknown folder takes the 0.3 localStorage recipe once, validated, then removes it", async () => {
  const stored = { "project//code/gamma": JSON.stringify({ harness: "opencode", provider: "Groq", preset: "Gone", plugins: "jupyter,unknown", image_route: "<b>", search: "yes" }) };
  const { AW, storage } = world({ stored });
  await AW.store.hydrate();
  await AW.project.select("/code/gamma");
  const form = AW.form.get();
  assert.equal(form.harness, "opencode");
  assert.equal(form.provider, "Groq");
  assert.equal(form.preset, "");
  assert.equal(form.plugins, "jupyter");
  assert.equal(form.image_route, "auto");
  assert.equal(form.search, true);
  assert.equal(storage.has("project//code/gamma"), false);
  await AW.project.select("/code/delta");
  await AW.project.select("/code/gamma");
  assert.equal(AW.form.get().harness, "OMP");
});

test("a folder's recipe wins over the localStorage recipe, which wins over the global settings", async () => {
  const stored = { "project//code/beta": JSON.stringify({ harness: "opencode" }), "project//code/gamma": JSON.stringify({ harness: "Hermes" }) };
  const { AW, storage } = world({ stored });
  await AW.store.hydrate();
  await AW.project.select("/code/beta");
  assert.equal(AW.form.get().harness, "Pi");
  await AW.project.select("/code/gamma");
  assert.equal(AW.form.get().harness, "Hermes");
  assert.equal(storage.has("project//code/gamma"), false);
  await AW.project.select("/code/delta");
  assert.equal(AW.form.get().harness, "OMP");
  assert.equal(AW.form.get().search, true);
});

test("a refused folder leaves the form and the current folder as they were", async () => {
  const { AW } = world();
  await AW.store.hydrate();
  AW.form.set({ harness: "Pi" });
  const before = plain(AW.form.get());
  await assert.rejects(AW.project.select("/no/such/folder"), (error) => error.message === "Choose an existing project folder.");
  assert.deepEqual(plain(AW.form.get()), before);
  assert.equal(AW.project.current(), "/code/alpha");
});

test("selecting folders and refreshing the settings never sends launch-save or touches the form", async () => {
  const { AW, calls } = world();
  await AW.store.hydrate();
  AW.form.set({ harness: "Pi" });
  await AW.project.select("/code/beta");
  await AW.project.select("/code/gamma");
  await AW.project.select("/code/alpha");
  const before = plain(AW.form.get());
  await AW.store.refresh("config");
  await settle();
  assert.deepEqual(plain(AW.form.get()), before);
  assert.equal(calls.filter((call) => call.action === "launch-save").length, 0);
  assert.ok(calls.some((call) => call.action === "folder-get"));
});

test("Save for this folder sends launch-save once with the form and marks it saved", async () => {
  const { AW, calls } = world();
  await AW.store.hydrate();
  AW.form.set({ harness: "Pi" });
  await AW.form.save();
  const saves = calls.filter((call) => call.action === "launch-save");
  assert.equal(saves.length, 1);
  assert.deepEqual(Object.keys(saves[0].data), KEYS);
  assert.equal(saves[0].data.harness, "Pi");
  assert.equal(AW.form.dirty(), false);
});

test("the scratch folder is the data folder's workspace", async () => {
  const { AW } = world();
  await AW.store.hydrate();
  assert.equal(AW.project.scratch(), "/data/workspace");
  assert.equal(AW.project.name("/code/alpha"), "alpha");
});

test("the known folders add the current folder and the recent folders that are not remembered yet", async () => {
  const { AW } = world();
  await AW.store.hydrate();
  const paths = () => plain(AW.project.known().map((row) => row.path));
  AW.store.set("folders", []);
  assert.deepEqual(paths(), ["/code/alpha"]);
  assert.deepEqual(plain(AW.project.known()[0]), { key: "/code/alpha", path: "/code/alpha", exists: true, harness: "", provider: "", model_label: "", kit: "", profile: "", updated: "" });
  const beta = { key: "/code/beta", path: "/code/beta", exists: true, harness: "Pi", provider: "", model_label: "", kit: "", profile: "", updated: "2026-11-22T10:00:00+00:00" };
  AW.store.set("folders", [beta]);
  assert.deepEqual(paths(), ["/code/alpha", "/code/beta"]);
  AW.state.config.recent = ["/code/beta", "/code/gamma", 5, "", "/code/gamma"];
  assert.deepEqual(paths(), ["/code/alpha", "/code/beta", "/code/gamma"]);
  assert.equal(AW.project.known()[1].harness, "Pi");
  await AW.project.select("/code/beta");
  assert.deepEqual(paths(), ["/code/beta", "/code/gamma"]);
  AW.store.set("folders", [{ ...beta, path: "/code/alpha", key: "/code/alpha" }]);
  await AW.project.select("/code/alpha");
  assert.deepEqual(paths(), ["/code/alpha", "/code/beta", "/code/gamma"]);
});
