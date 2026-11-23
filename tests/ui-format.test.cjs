// AW.fmt in desktop/ui/core.js: binary units, rates, durations, relative times, time left, context sizes, paths and lists;
// and, on a small stand-in DOM, the sentences the shared components build from them: task lines, error notices, toasts and the consent line.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function load() {
  const context = vm.createContext({ console });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "desktop", "ui", "core.js"), "utf8"), context, { filename: "core.js" });
  return context.AW;
}
const AW = load();
const fmt = AW.fmt;
const KIB = 1024;
const MIB = KIB * KIB;
const GIB = KIB * MIB;

test("bytes use binary units with one decimal from 1 GiB", () => {
  assert.equal(fmt.bytes(0), "0 B");
  assert.equal(fmt.bytes(1023), "1023 B");
  assert.equal(fmt.bytes(KIB), "1.0 KiB");
  assert.equal(fmt.bytes(10 * KIB), "10 KiB");
  assert.equal(fmt.bytes(MIB - 1), "1 MiB");
  assert.equal(fmt.bytes(38 * MIB), "38 MiB");
  assert.equal(fmt.bytes(16600069), "16 MiB");
  assert.equal(fmt.bytes(1023.6 * MIB), "1.0 GiB");
  assert.equal(fmt.bytes(GIB), "1.0 GiB");
  assert.equal(fmt.bytes(23.6 * GIB), "23.6 GiB");
  assert.equal(fmt.bytes(17659407264), "16.4 GiB");
  for (const bad of [null, undefined, "", -1, NaN, "many"]) assert.equal(fmt.bytes(bad), "");
});

test("gib and rate reuse the byte units", () => {
  assert.equal(fmt.gib(2.6), "2.6 GiB");
  assert.equal(fmt.gib(48), "48.0 GiB");
  assert.equal(fmt.gib(0.5), "512 MiB");
  assert.equal(fmt.gib(null), "");
  assert.equal(fmt.rate(38 * MIB), "38 MiB/s");
  assert.equal(fmt.rate(2.5 * GIB), "2.5 GiB/s");
  assert.equal(fmt.rate(undefined), "");
});

test("durations read as seconds, minutes and seconds, then hours and minutes", () => {
  assert.equal(fmt.duration(0), "0 s");
  assert.equal(fmt.duration(14), "14 s");
  assert.equal(fmt.duration(59.4), "59 s");
  assert.equal(fmt.duration(60), "1:00");
  assert.equal(fmt.duration(252), "4:12");
  assert.equal(fmt.duration(3599), "59:59");
  assert.equal(fmt.duration(3600), "1 h 0 min");
  assert.equal(fmt.duration(3720), "1 h 2 min");
  assert.equal(fmt.duration(-5), "0 s");
});

test("relative times step from seconds to dates", () => {
  const now = new Date(2026, 10, 23, 12, 0, 0).getTime();
  assert.equal(fmt.ago(now - 8000, now), "8 s ago");
  assert.equal(fmt.ago(now - 5 * 60000, now), "5 min ago");
  assert.equal(fmt.ago(now - 3600000, now), "1 hour ago");
  assert.equal(fmt.ago(now - 2 * 3600000, now), "2 hours ago");
  assert.equal(fmt.ago(now - 30 * 3600000, now), "yesterday");
  assert.equal(fmt.ago(new Date(2026, 10, 20, 9).getTime(), now), "20 Nov");
  assert.equal(fmt.ago(new Date(2025, 0, 3, 9).getTime(), now), "3 Jan 2025");
  assert.equal(fmt.ago(new Date(now - 8000).toISOString(), now), "8 s ago");
  assert.equal(fmt.ago((now - 8000) / 1000, now), "8 s ago");
  assert.equal(fmt.ago("not a date", now), "");
});

test("time left is hidden until two progress samples give a rate", () => {
  const pace = fmt.pace();
  pace.add(0, 1000);
  assert.equal(pace.samples(), 1);
  assert.equal(pace.rate(), 0);
  assert.equal(fmt.left(0, 600 * MIB, pace.rate()), "");
  pace.add(10 * MIB, 11000);
  assert.equal(pace.rate(), MIB);
  assert.equal(fmt.left(10 * MIB, 600 * MIB, pace.rate()), "about 10 min left");
  assert.equal(fmt.left(570 * MIB, 600 * MIB, pace.rate()), "less than a minute left");
  assert.equal(fmt.left(600 * MIB, 600 * MIB, pace.rate()), "");
});

test("the pace keeps only the last ten seconds and restarts when the count goes back", () => {
  const pace = fmt.pace();
  pace.add(0, 0);
  pace.add(100 * MIB, 10000);
  pace.add(140 * MIB, 20000);
  assert.equal(pace.rate(), 4 * MIB);
  pace.add(5, 21000);
  assert.equal(pace.samples(), 1);
  assert.equal(pace.rate(), 0);
});

test("context sizes read as K and M", () => {
  assert.equal(fmt.context(32768), "32K");
  assert.equal(fmt.context(32000), "32K");
  assert.equal(fmt.context(131072), "128K");
  assert.equal(fmt.context(128000), "128K");
  assert.equal(fmt.context(4096), "4K");
  assert.equal(fmt.context(1048576), "1M");
  assert.equal(fmt.context(512), "512");
  assert.equal(fmt.context(0), "");
  assert.equal(fmt.context(null), "");
});

test("folder names, middle ellipsis and lists", () => {
  assert.equal(fmt.folder("/Users/me/code/shop"), "shop");
  assert.equal(fmt.folder("/Users/me/code/shop/"), "shop");
  assert.equal(fmt.folder("C:\\code\\site"), "site");
  assert.equal(fmt.folder(""), "");
  assert.equal(fmt.middle("abcdefghij", 7), "abc…hij");
  assert.equal(fmt.middle("abcdefghij", 7).length, 7);
  assert.equal(fmt.middle("abcdefghijk", 8), "abcd…ijk");
  assert.equal(fmt.middle("short", 10), "short");
  assert.equal(fmt.list([]), "");
  assert.equal(fmt.list(["OMP"]), "OMP");
  assert.equal(fmt.list(["OMP", "Pi"]), "OMP and Pi");
  assert.equal(fmt.list(["OMP", "Pi", "opencode"]), "OMP, Pi and opencode");
});

test("paths inside the home folder read from ~ and others stay as they are", () => {
  const home = "/Users/me";
  assert.equal(fmt.path("/Users/me", home), "~");
  assert.equal(fmt.path("/Users/me/", home), "~");
  assert.equal(fmt.path("/Users/me/code/shop", home), "~/code/shop");
  assert.equal(fmt.path("/Users/meadow/code", home), "/Users/meadow/code");
  assert.equal(fmt.path("/opt/models", home), "/opt/models");
  assert.equal(fmt.path("AGENTS.md", home), "AGENTS.md");
  assert.equal(fmt.path("/Users/me/code", ""), "/Users/me/code");
  assert.equal(fmt.path("/srv/x", "/"), "/srv/x");
  assert.equal(fmt.path(null, home), "");
});

test("both spellings of a macOS temporary home match its canonical form", () => {
  const home = "/private/var/folders/pl/T/shots";
  assert.equal(fmt.path("/var/folders/pl/T/shots/Sample project", home), "~/Sample project");
  assert.equal(fmt.path("/private/var/folders/pl/T/shots/Sample project", home), "~/Sample project");
  assert.equal(fmt.path("/private/var/folders/pl/T/shots", home), "~");
  assert.equal(fmt.path("/var/folders/pl/T/shots/a", "/var/folders/pl/T/shots"), "~/a");
  assert.equal(fmt.path("/private/var/folders/pl/T/other", home), "/private/var/folders/pl/T/other");
});

test("Windows paths compare without case and with either slash", () => {
  const home = "C:\\Users\\Me";
  assert.equal(fmt.path("c:\\users\\me\\code\\Site", home), "~\\code\\Site");
  assert.equal(fmt.path("C:/Users/Me/code", home), "~\\code");
  assert.equal(fmt.path("C:\\Users\\Mel\\code", home), "C:\\Users\\Mel\\code");
  assert.equal(fmt.path("D:\\code", home), "D:\\code");
});

test("sentences shorten every home-folder path they hold", () => {
  const home = "/private/var/folders/T/h";
  assert.equal(fmt.paths("Your model folder is now /var/folders/T/h/models. Found 2 files.", home), "Your model folder is now ~/models. Found 2 files.");
  assert.equal(fmt.paths("Copied /private/var/folders/T/h/a to /var/folders/T/h/b.", home), "Copied ~/a to ~/b.");
  assert.equal(fmt.paths("“/var/folders/T/h” cannot be reached.", home), "“~” cannot be reached.");
  assert.equal(fmt.paths("Left /var/folders/T/hat alone.", home), "Left /var/folders/T/hat alone.");
  assert.equal(fmt.paths("Moved /Users/me.old/a and /Users/me,2 to /Users/me.", "/Users/me"), "Moved /Users/me.old/a and /Users/me,2 to ~.");
  assert.equal(fmt.paths("No path here.", home), "No path here.");
  assert.equal(fmt.paths("Saved C:\\users\\me\\x.json.", "C:\\Users\\Me"), "Saved ~\\x.json.");
  assert.equal(fmt.paths("Kept /Users/me/x.", ""), "Kept /Users/me/x.");
});

test("a typed ~ path expands to the full path before it is sent", () => {
  assert.equal(fmt.expand("~/code/shop", "/Users/me"), "/Users/me/code/shop");
  assert.equal(fmt.expand(" ~ ", "/Users/me"), "/Users/me");
  assert.equal(fmt.expand("~\\code", "C:\\Users\\Me"), "C:\\Users\\Me\\code");
  assert.equal(fmt.expand("~other/x", "/Users/me"), "~other/x");
  assert.equal(fmt.expand("/srv/x", "/Users/me"), "/srv/x");
  assert.equal(fmt.expand("~/x", ""), "~/x");
});

test("the home folder comes from the app-state reply", () => {
  const AW2 = load();
  assert.equal(AW2.fmt.home(), "");
  assert.equal(AW2.fmt.path("/Users/me/a"), "/Users/me/a");
  AW2.state = { config: { home: "/Users/me" } };
  assert.equal(AW2.fmt.home(), "/Users/me");
  assert.equal(AW2.fmt.path("/Users/me/a"), "~/a");
  assert.equal(AW2.fmt.paths("In /Users/me/a."), "In ~/a.");
});

test("errors become objects with a message whatever was thrown", () => {
  assert.deepEqual({ ...AW.asError({ message: " The folder is gone. ", code: "internal", detail: "trace", extra: 1 }) }, { message: "The folder is gone.", code: "internal", detail: "trace" });
  assert.deepEqual({ ...AW.asError("Plain sentence.") }, { message: "Plain sentence." });
  assert.equal(AW.asError(null).message, "Something went wrong. Try again.");
  assert.equal(AW.asError(new Error("")).message, "Something went wrong. Try again.");
});

test("the stale-answer ticket only accepts the latest number", () => {
  const ticket = AW.ticket();
  const first = ticket.next();
  const second = ticket.next();
  assert.equal(ticket.current(first), false);
  assert.equal(ticket.current(second), true);
});

// A stand-in DOM with just what core.js, components.js, feedback.js, router.js and consent.js touch while building nodes.
function fakeDom() {
  class Node {}
  class Text extends Node {
    constructor(text) {
      super();
      this.data = String(text);
    }
    get textContent() {
      return this.data;
    }
  }
  class Element extends Node {
    constructor(tag) {
      super();
      this.tagName = tag.toUpperCase();
      this.childNodes = [];
      this.attributes = {};
      this.dataset = {};
      this.style = { setProperty() {}, removeProperty() {} };
      this.className = "";
      this.hidden = false;
      this.parentNode = null;
      const own = () => this.className.split(" ").filter(Boolean);
      this.classList = {
        add: (...names) => (this.className = [...new Set([...own(), ...names])].join(" ")),
        remove: (...names) => (this.className = own().filter((name) => !names.includes(name)).join(" ")),
        contains: (name) => own().includes(name),
        toggle: (name, on) => ((on ?? !own().includes(name)) ? this.classList.add(name) : this.classList.remove(name)),
      };
    }
    get children() {
      return this.childNodes.filter((node) => node instanceof Element);
    }
    get textContent() {
      return this.childNodes.map((node) => node.textContent).join("");
    }
    set textContent(value) {
      this.childNodes = value === "" ? [] : [new Text(value)];
    }
    append(...nodes) {
      for (const node of nodes) {
        const child = node instanceof Node ? node : new Text(node);
        if (child.parentNode) child.remove();
        child.parentNode = this;
        this.childNodes.push(child);
      }
    }
    prepend(...nodes) {
      const rest = this.childNodes;
      this.childNodes = [];
      this.append(...nodes);
      this.childNodes.push(...rest);
    }
    replaceChildren(...nodes) {
      this.childNodes = [];
      this.append(...nodes);
    }
    remove() {
      if (this.parentNode) this.parentNode.childNodes = this.parentNode.childNodes.filter((node) => node !== this);
      this.parentNode = null;
    }
    setAttribute(name, value) {
      this.attributes[name] = String(value);
    }
    getAttribute(name) {
      return name in this.attributes ? this.attributes[name] : null;
    }
    removeAttribute(name) {
      delete this.attributes[name];
    }
    hasAttribute(name) {
      return name in this.attributes;
    }
    addEventListener() {}
    removeEventListener() {}
    matches() {
      return false;
    }
    closest() {
      return null;
    }
    getClientRects() {
      return [];
    }
    descendants() {
      return this.children.flatMap((node) => [node, ...node.descendants()]);
    }
    querySelectorAll(selector) {
      const cls = selector.startsWith(".") ? selector.slice(1) : null;
      return this.descendants().filter((node) => (cls ? node.classList.contains(cls) : node.tagName === selector.toUpperCase()));
    }
    querySelector(selector) {
      return this.querySelectorAll(selector)[0] || null;
    }
  }
  const ids = new Map();
  const document = {
    body: new Element("body"),
    activeElement: null,
    createElement: (tag) => new Element(tag),
    createElementNS: (_ns, tag) => new Element(tag),
    createTextNode: (text) => new Text(text),
    addEventListener() {},
    getElementById: (id) => ids.get(id) || null,
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  for (const id of ["toasts", "live-status", "live-alert", "notices"]) ids.set(id, new Element("div"));
  return { Node, document, ids };
}

function loadShared(home) {
  const { Node, document, ids } = fakeDom();
  const context = vm.createContext({ console, Node, document, requestAnimationFrame: (fn) => fn(), setTimeout: () => 0, clearTimeout() {}, confirm() {} });
  context.window = context;
  context.addEventListener = () => {};
  context.HTMLDialogElement = class {};
  for (const file of ["core.js", "components.js", "feedback.js", "router.js", "consent.js"])
    vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "desktop", "ui", file), "utf8"), context, { filename: file });
  context.AW.state = { config: { home } };
  return { AW: context.AW, ids };
}

const labels = (node) => node.querySelectorAll("button").map((button) => button.textContent);

test("a task line does not repeat its title or its asset, and downloads show numbers while they download", () => {
  const { AW } = loadShared("/Users/me");
  const text = AW.ui.taskText;
  const hub = { title: "Downloading Qwen3-0.6B-Q4.gguf", action: "studio-download", progress: { phase: "checking", message: "Downloading Qwen3-0.6B-Q4.gguf part 2", done: 0, total: 0 } };
  assert.equal(text(hub, 0).split(" · ")[0], "part 2");
  const moving = { title: "Downloading Qwen3.8-27B UD-Q4_K_XL", action: "model-download", progress: { phase: "downloading", message: "Downloading Qwen3.8-27B-UD-Q4_K_XL.gguf", done: 108 * MIB, total: 16.4 * GIB, rate: 40 * MIB } };
  assert.equal(text(moving, 0), "108 MiB of 16.4 GiB · 40 MiB/s");
  const checking = { ...moving, progress: { ...moving.progress, phase: "checking", message: "Checking Qwen3.8-27B-UD-Q4_K_XL.gguf" } };
  assert.equal(text(checking, 0), "Checking Qwen3.8-27B-UD-Q4_K_XL.gguf · 108 MiB of 16.4 GiB · 40 MiB/s");
  const engine = { title: "Installing the engine for local models", action: "engine-install", progress: { message: "Downloading llama-b12633-bin-macos-arm64.tar.gz (1 of 1)", asset: "llama-b12633-bin-macos-arm64.tar.gz", done: MIB, total: 16 * MIB } };
  assert.equal(text(engine, 0), "Downloading llama-b12633-bin-macos-arm64.tar.gz (1 of 1) · 1 MiB of 16 MiB");
  assert.equal(text({ ...engine, progress: { ...engine.progress, message: "Fetching" } }, 0), "Fetching · llama-b12633-bin-macos-arm64.tar.gz · 1 MiB of 16 MiB");
  const unpack = { title: "Installing the engine for local models", action: "engine-install", progress: { message: "Unpacking into /Users/me/data/engines" } };
  assert.equal(text(unpack, 0).split(" · ")[0], "Unpacking into ~/data/engines");
});

test("an error notice offers Try again for a plain refusal when the caller can retry", () => {
  const { AW } = loadShared("/Users/me");
  const retry = () => 1;
  assert.deepEqual(labels(AW.ui.errorNotice({ message: "Set OPENAI_API_KEY or save a key for OpenAI." }, { retry })), ["Try again", "Dismiss"]);
  assert.deepEqual(labels(AW.ui.errorNotice({ message: "No key." })), ["Dismiss"]);
  assert.deepEqual(labels(AW.ui.errorNotice({ message: "Broke.", code: "internal" }, { retry })), ["Copy log path", "Dismiss"]);
  assert.deepEqual(labels(AW.ui.errorNotice({ message: "Run Doctor.", action: "doctor" }, { retry })), ["Run Doctor", "Dismiss"]);
  const cancelled = AW.ui.errorNotice({ message: "Download cancelled. The part in /Users/me/models is kept.", code: "cancelled" }, { retry });
  assert.equal(cancelled.textContent, "Download cancelled. The part in ~/models is kept.");
  assert.deepEqual(labels(cancelled), []);
});

test("toasts, notices and field errors shorten paths inside the home folder", () => {
  const { AW, ids } = loadShared("/private/var/folders/T/h");
  AW.ui.toast("Your model folder is now /var/folders/T/h/models.");
  assert.equal(ids.get("toasts").querySelector(".toast-text").textContent, "Your model folder is now ~/models.");
  assert.equal(AW.ui.notice("warn", "The folder /private/var/folders/T/h/shop no longer exists.").textContent, "The folder ~/shop no longer exists.");
  const field = AW.ui.field("Project folder", AW.el("input", { type: "text" }));
  field.setError("/var/folders/T/h/gone does not exist.");
  assert.equal(field.el.querySelector(".field-error").textContent, "~/gone does not exist.");
});

test("the consent line names the day the folder's programs were allowed", () => {
  const { AW } = loadShared("/Users/me");
  const now = new Date(2026, 10, 23, 12, 0, 0).getTime();
  const sentence = AW.consent.grantedSentence;
  assert.equal(sentence(new Date(2026, 10, 20, 9).toISOString(), now), "You allowed this folder's programs on 20 Nov. Workbench asks again when they change.");
  assert.equal(sentence(new Date(now - 5 * 60000).toISOString(), now), "You allowed this folder's programs 5 min ago. Workbench asks again when they change.");
  assert.equal(sentence(new Date(now - 30 * 3600000).toISOString(), now), "You allowed this folder's programs yesterday. Workbench asks again when they change.");
  assert.equal(sentence("", now), "You allowed this folder's programs. Workbench asks again when they change.");
  assert.equal(sentence("not a time", now), "You allowed this folder's programs. Workbench asks again when they change.");
});
