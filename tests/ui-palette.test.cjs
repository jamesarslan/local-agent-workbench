// The command palette in desktop/ui/palette.js: subsequence matching, ranking, grouping, when() filtering, Suggested and Recent.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ui = path.join(__dirname, "..", "desktop", "ui");

function load() {
  const storage = new Map();
  const noop = () => {};
  const document = { addEventListener: noop, getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] };
  const context = vm.createContext({
    console,
    document,
    localStorage: {
      getItem: (key) => (storage.has(key) ? storage.get(key) : null),
      setItem: (key, value) => storage.set(key, String(value)),
      removeItem: (key) => storage.delete(key),
    },
    setTimeout,
    clearTimeout,
  });
  context.window = context;
  context.addEventListener = noop;
  for (const file of ["core.js", "router.js", "palette.js"]) vm.runInContext(fs.readFileSync(path.join(ui, file), "utf8"), context, { filename: file });
  return { AW: context.AW, storage };
}

const plain = (value) => JSON.parse(JSON.stringify(value));
const command = (id, title, extra = {}) => ({ id, title, group: "Actions", run: () => id, ...extra });
const titles = (results) => plain(results.map((result) => result.command.title));

test("matching is a case-insensitive subsequence over the text", () => {
  const { AW } = load();
  const { match } = AW.palette;
  assert.deepEqual(plain(match("Go to Launch", "lau")), { start: 6, span: 3, indices: [6, 7, 8], tier: 0 });
  assert.deepEqual(plain(match("Go to Launch", "GTL")), { start: 0, span: 7, indices: [0, 3, 6], tier: 2 });
  assert.deepEqual(plain(match("Go to Launch", "aun")), { start: 7, span: 3, indices: [7, 8, 9], tier: 1 });
  assert.equal(match("Go to Launch", "lx"), null);
  assert.equal(match("Show terminal", "lt"), null, "order matters");
  assert.deepEqual(plain(match("anything", "")), { start: 0, span: 0, indices: [], tier: 0 });
  assert.deepEqual(plain(match("New shell in shop", "new  shell")), { start: 0, span: 9, indices: [0, 1, 2, 4, 5, 6, 7, 8], tier: 0 });
  assert.deepEqual(plain(match("Go to Launch", "go la")).indices, [0, 1, 6, 7], "spaces in the query are ignored");
});

test("the query as typed wins over a scattered fit, and the tightest scattered fit is kept", () => {
  const { AW } = load();
  assert.deepEqual(plain(AW.palette.match("Switch to shop · OMP", "shop")), { start: 10, span: 4, indices: [10, 11, 12, 13], tier: 0 });
  assert.deepEqual(plain(AW.palette.match("Xab ab", "ab")), { start: 4, span: 2, indices: [4, 5], tier: 0 }, "a word start beats an earlier place inside a word");
  assert.deepEqual(plain(AW.palette.match("axxb ab-c", "abc")), { start: 5, span: 4, indices: [5, 6, 8], tier: 2 });
});

test("results are ranked by match position, title before keywords", () => {
  const { AW } = load();
  const list = [
    command("go.launch", "Go to Launch", { group: "Go to" }),
    command("launch.quick", "Launch in shop · OMP · qwen3:8b · Terminal dock"),
    command("terminal.show", "Show terminal", { keywords: "launch dock" }),
    command("model.stop", "Stop local model"),
    command("connection.use", "Launch with Personal OpenRouter", { group: "Connections" }),
  ];
  assert.deepEqual(titles(AW.palette.search(list, "lau")), [
    "Launch in shop · OMP · qwen3:8b · Terminal dock",
    "Launch with Personal OpenRouter",
    "Go to Launch",
    "Show terminal",
  ]);
  const keywordHit = AW.palette.search(list, "dock").find((result) => result.command.id === "terminal.show");
  assert.equal(keywordHit.field, 1);
  assert.deepEqual(plain(keywordHit.indices), []);
  assert.deepEqual(titles(AW.palette.search(list, "st")), [
    "Stop local model",
    "Show terminal",
    "Launch in shop · OMP · qwen3:8b · Terminal dock",
    "Launch with Personal OpenRouter",
  ]);
  assert.deepEqual(titles(AW.palette.search(list, "zzz")), []);
  const themed = [command("advanced.benchmarks", "Advanced: Benchmarks", { group: "Go to" }), command("theme.dark", "Switch to dark theme")];
  assert.deepEqual(titles(AW.palette.search(themed, "dark")), ["Switch to dark theme", "Advanced: Benchmarks"], "a typed word beats an earlier scattered fit");
});

test("a scattered fit never outranks the query as typed, in the title or the keywords", () => {
  const { AW } = load();
  const list = [
    command("keys.check", "Don't check keys"),
    command("terminal.show", "Show terminal", { keywords: "launch dock" }),
    command("dock.toggle", "Dock height", { keywords: "terminal size" }),
    command("theme.dark", "Switch to dark theme", { keywords: "d o c k" }),
  ];
  const found = AW.palette.search(list, "dock");
  assert.deepEqual(titles(found), ["Dock height", "Show terminal", "Don't check keys", "Switch to dark theme"]);
  assert.deepEqual(plain(found.map((result) => [result.field, result.tier])), [[0, 0], [1, 0], [0, 2], [1, 2]]);
  const both = AW.palette.search([command("a", "Data or cache kept", { keywords: "dock" })], "dock")[0];
  assert.equal(both.field, 1, "a typed keyword beats a scattered title of the same command");
  assert.deepEqual(plain(both.indices), []);
});

test("at most fifty results, and duplicates by id or by title are dropped", () => {
  const { AW } = load();
  const many = Array.from({ length: 80 }, (_, n) => command("c" + n, "Command " + n));
  assert.equal(AW.palette.search(many, "c").length, 50);
  const twice = [command("a", "Open setup guide"), command("a", "Something else"), command("b", "Open setup guide")];
  assert.deepEqual(titles(AW.palette.search(twice, "open")), ["Open setup guide"]);
});

test("groups follow their best match, so the first option is the best one", () => {
  const { AW } = load();
  const list = [
    command("go.launch", "Go to Launch", { group: "Go to" }),
    command("project.launch:a", "Launch in site · Claude Code", { group: "Projects" }),
    command("launch.quick", "Launch in shop · OMP"),
  ];
  const groups = AW.palette.group(AW.palette.search(list, "lau"));
  assert.deepEqual(plain(groups.map((entry) => entry.name)), ["Actions", "Projects", "Go to"]);
  assert.equal(groups[0].items[0].command.id, "launch.quick");
  const other = [command("project.kit", "Apply a starter kit…", { group: "Project setup" }), command("doctor.run", "Run Doctor")];
  assert.deepEqual(plain(AW.palette.group(AW.palette.search(other, "a")).map((entry) => entry.name)), ["Actions"], "a group outside the five is shown under Actions");
});

test("commands whose when() fails or throws are not offered", () => {
  const { AW } = load();
  AW.commands.register(command("test.yes", "Palette test visible", { when: () => true }));
  AW.commands.register(command("test.no", "Palette test hidden", { when: () => false }));
  AW.commands.register(command("test.throws", "Palette test broken", { when: () => { throw new Error("no state"); } }));
  AW.commands.register(command("test.title", "", { title: () => "Palette test computed title" }));
  const found = titles(AW.palette.search(AW.commands.all(), "palette test"));
  assert.deepEqual(found, ["Palette test visible", "Palette test computed title"]);
});

test("Suggested takes the first five that apply, in the spec's order", () => {
  const { AW } = load();
  const list = [
    command("guide.open", "Open setup guide"),
    command("project.switch:/b", "Switch to b", { group: "Projects" }),
    command("model.start", "Start local model"),
    command("attach.open", "Attach a running server…", { group: "Connections" }),
    command("task.cancel:model-download-1", "Cancel: Downloading Qwen"),
    command("session.show:t1", "Show OMP · shop", { group: "Sessions" }),
    command("session.show:t2", "Show Shell · shop", { group: "Sessions" }),
    command("launch.quick", "Launch in shop · OMP"),
  ];
  const sessions = [
    { id: "t1", kind: "embedded", started: 100 },
    { id: "t2", kind: "embedded", started: 200 },
    { id: "n1", kind: "native", started: 300 },
  ];
  const ids = (out) => plain(out.map((item) => item.id));
  assert.deepEqual(ids(AW.palette.suggested(list, { workspace: "/a", sessions, guideOpen: true })), [
    "launch.quick",
    "session.show:t2",
    "task.cancel:model-download-1",
    "attach.open",
    "model.start",
  ]);
  assert.deepEqual(ids(AW.palette.suggested(list.slice(0, 3), { workspace: "/a", sessions: [], guideOpen: true })), ["model.start", "project.switch:/b", "guide.open"]);
  assert.deepEqual(ids(AW.palette.suggested(list.slice(0, 3), { workspace: "", sessions: [], guideOpen: false })), ["model.start", "project.switch:/b"]);
});

test("Recent keeps five command ids, newest first, in palette.recent", () => {
  const { AW, storage } = load();
  for (const id of ["a", "b", "c", "a", "d", "e", "f"]) AW.palette.remember(id);
  assert.deepEqual(plain(AW.palette.recentIds()), ["f", "e", "d", "a", "c"]);
  assert.deepEqual(JSON.parse(storage.get("palette.recent")), ["f", "e", "d", "a", "c"]);
  AW.palette.remember("task.cancel:model-download-3");
  assert.deepEqual(plain(AW.palette.recentIds()), ["f", "e", "d", "a", "c"], "a cancel command is not kept");
  const list = ["a", "c", "e"].map((id) => command(id, "Command " + id));
  assert.deepEqual(plain(AW.palette.recent(list, AW.palette.recentIds(), [list[2]]).map((item) => item.id)), ["a", "c"], "unavailable and suggested ids are skipped");
  storage.set("palette.recent", "not json");
  assert.deepEqual(plain(AW.palette.recentIds()), []);
});

test("view commands show their Mod number as the key hint", () => {
  const { AW } = load();
  const mod = AW.platform.mod;
  assert.equal(AW.palette.hintOf({ id: "go.overview" }), mod + " 2");
  assert.equal(AW.palette.hintOf({ id: "go.launch", hint: mod + " L" }), mod + " L");
  assert.equal(AW.palette.hintOf({ id: "go.project.mcp" }), "");
  assert.equal(AW.palette.hintOf({ id: "model.start" }), "");
});

test("the quick launch command names the target only while Launch is ready", () => {
  const storage = new Map();
  const noop = () => {};
  const document = { addEventListener: noop, getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] };
  const context = vm.createContext({
    console,
    document,
    localStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, String(value)), removeItem: (key) => storage.delete(key) },
    setTimeout,
    clearTimeout,
    matchMedia: () => ({ matches: true, addEventListener: noop }),
  });
  context.window = context;
  context.addEventListener = noop;
  context.AW = { shell: { register: noop } };
  for (const file of ["core.js", "router.js", "header.js"]) vm.runInContext(fs.readFileSync(path.join(ui, file), "utf8"), context, { filename: file });
  const title = context.AW.launch.quickTitle;
  assert.equal(title("shop", { ok: true, needs: "" }, "OMP · qwen3:8b · Terminal dock"), "Launch in shop · OMP · qwen3:8b · Terminal dock");
  assert.equal(title("shop", { ok: false, needs: "Launch needs: a model." }, "OMP · Local model · Terminal dock"), "Launch… · needs a model");
  assert.equal(title("shop", { ok: false, needs: "Launch needs: a model, a key for Groq." }, "x"), "Launch… · needs a model, a key for Groq");
  assert.equal(title("", { ok: false, needs: "" }, "x"), "Launch… · choose a project folder");
});
