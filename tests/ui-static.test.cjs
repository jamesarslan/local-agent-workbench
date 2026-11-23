// Static checks on the desktop renderer: no HTML-string sinks, colours only in tokens.css, a complete index.html and known bridge actions.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const desktop = path.join(__dirname, "..", "desktop");
const read = (file) => fs.readFileSync(path.join(desktop, file), "utf8");
const list = (dir, ext) =>
  fs
    .readdirSync(path.join(desktop, dir))
    .filter((name) => name.endsWith(ext))
    .sort()
    .map((name) => dir + "/" + name);
const html = read("index.html");
const scripts = list("ui", ".js");
const styles = list("css", ".css");
const lineOf = (text, index) => text.slice(0, index).split("\n").length;

const CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; connect-src 'none'; base-uri 'none'";
const FORBIDDEN = [/\binnerHTML\b/, /\bouterHTML\b/, /\binsertAdjacentHTML\b/, /\bdocument\s*\.\s*write(ln)?\b/, /\beval\s*\(/, /\bnew\s+Function\b/];
const COLOUR = /#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])|\b(?:rgba?|hsla?)\s*\(/g;
const WRAPPERS = new Set(["ui/core.js", "ui/jobs.js", "ui/compat.js"]);

const KEPT_IDS = [
  "nav-launch", "nav-project", "nav-accounts", "nav-machines", "nav-advanced", "terminal-toggle", "theme-switch", "machine-name",
  "project-switch", "crumb", "project-pop", "workspace", "browse", "folder-profiles",
  "launch-view", "accounts-view", "advanced-view", "machines-view", "project-view",
  "source", "launch-machine", "local-field", "preset", "preset-info", "remote-field", "remote", "list-models", "routing-hint", "account-link",
  "agent", "tools", "search", "firecrawl", "blender", "desktop-tool", "desktop", "session-extra", "visual-panel", "image-route", "visual-preview",
  "launch", "save", "session-indicator",
  "terminal-dock", "terminal-list", "term-shell", "term-agent", "term-resume", "term-watch", "term-close", "term-hide", "term-info", "term-screen",
  "session-picker", "saved-sessions", "model-picker", "model-filter", "picker-count", "picker-list", "advanced-section", "advanced-body",
];
const NEW_IDS = [
  "rail", "main", "header", "view", "notices", "toasts", "live-status", "live-alert", "status-strip", "boot-screen", "boot-failed",
  "nav-overview", "nav-guide", "machine-label", "command-open", "launch-target", "launch-quick",
  "strip-model", "strip-sessions", "strip-tasks", "strip-service", "strip-keys", "project-list",
  "overview-view", "guide-view", "guide-steps", "launch-project", "source-health", "model-context", "agent-cards", "launch-readiness",
  "launch-needs", "launch-needs-dock", "where-dock", "where-window", "save-state", "launch-recent", "term-tabs", "dock-resize", "session-agent",
  "plan-review", "profile-dialog", "consent-dialog", "attach-dialog", "secret-dialog", "server-dialog", "file-dialog", "confirm-dialog", "keys-dialog", "palette",
];
const SCRIPT_ORDER = [
  "core", "components", "feedback", "router", "store", "form", "jobs", "readiness", "rail", "header", "project-switch", "strip", "sessions", "terminal",
  "gallery", "launch", "view-launch", "view-overview", "guide", "guide-model", "guide-steps", "plan-review", "consent", "kit-preview", "view-project",
  "project-instructions", "project-skills", "project-mcp", "project-kit", "project-profiles", "source-forms", "attach", "model-picker", "view-connections",
  "view-machines", "tools", "model-install", "view-advanced", "adv-setup", "adv-models", "adv-agent", "adv-hub", "adv-bench", "adv-trace", "adv-sandbox",
  "adv-appearance", "palette", "compat", "boot",
];

test("no HTML-string sink or code evaluation under desktop/ui", () => {
  const found = [];
  for (const file of scripts) {
    const text = read(file);
    for (const pattern of FORBIDDEN) {
      const hit = pattern.exec(text);
      if (hit) found.push(`${file}:${lineOf(text, hit.index)} ${hit[0]}`);
    }
  }
  assert.deepEqual(found, []);
});

test("colour literals live only in css/tokens.css", () => {
  const found = [];
  for (const file of [...styles.filter((name) => name !== "css/tokens.css"), ...scripts]) {
    const text = read(file);
    for (const hit of text.matchAll(COLOUR)) found.push(`${file}:${lineOf(text, hit.index)} ${hit[0]}`);
  }
  assert.deepEqual(found, []);
  assert.match(read("css/tokens.css"), /--accent:\s*#/);
});

test("every script and stylesheet index.html names exists, in the agreed order", () => {
  const sources = [...html.matchAll(/<script\b[^>]*\bsrc="([^"]+)"/g)].map((hit) => hit[1]);
  const links = [...html.matchAll(/<link\b[^>]*\bhref="([^"]+)"/g)].map((hit) => hit[1]);
  assert.ok(sources.length > 0 && links.length > 0);
  for (const file of [...sources, ...links]) {
    assert.ok(!/^[a-z]+:|^\/\//i.test(file), `${file} must be a local file`);
    assert.ok(fs.existsSync(path.join(desktop, file)), `${file} is missing`);
  }
  assert.equal(sources[0], "ui/theme-boot.js");
  assert.deepEqual(sources.slice(1), SCRIPT_ORDER.map((name) => `ui/${name}.js`));
  assert.deepEqual(links.slice(0, 4), ["css/tokens.css", "css/base.css", "css/components.css", "css/shell.css"]);
  for (const file of scripts) assert.ok(sources.includes(file), `${file} is not loaded by index.html`);
  for (const file of styles) assert.ok(links.includes(file), `${file} is not linked by index.html`);
});

test("the Content-Security-Policy is unchanged and the page has no inline code", () => {
  const meta = /<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]*)"\s*\/>/.exec(html);
  assert.ok(meta, "the Content-Security-Policy meta is missing");
  assert.equal(meta[1], CSP);
  assert.equal(/<script\b(?![^>]*\bsrc=)[^>]*>/.exec(html), null, "inline script");
  assert.equal(/<[^>]+\son[a-z]+\s*=/i.exec(html), null, "inline event handler");
  assert.equal(/<[^>]+\sstyle\s*=/i.exec(html), null, "inline style attribute");
  assert.equal(/\b(?:src|href)="(?:[a-z]+:)?\/\//i.exec(html), null, "remote resource");
});

test("the static ids are present and no id occurs twice", () => {
  const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((hit) => hit[1]);
  const seen = new Set();
  const twice = ids.filter((id) => (seen.has(id) ? true : (seen.add(id), false)));
  assert.deepEqual(twice, []);
  assert.deepEqual([...KEPT_IDS, ...NEW_IDS].filter((id) => !seen.has(id)), []);
});

test("every bridge call names an action in desktop/actions.json", () => {
  const table = JSON.parse(read("actions.json"));
  const known = new Set([...Object.keys(table.actions), ...Object.keys(table.aliases)]);
  const internal = new Set(Object.keys(table.actions).filter((name) => table.actions[name].via === "internal"));
  const unknown = [];
  const dynamic = [];
  let calls = 0;
  for (const file of scripts) {
    const text = read(file);
    for (const hit of text.matchAll(/\bAW\s*\.\s*(?:request|jobs\s*\.\s*run)\s*\(\s*([^\s,)]*)/g)) {
      const literal = /^(["'`])([^"'`$]*)\1$/.exec(hit[1]);
      const where = `${file}:${lineOf(text, hit.index)}`;
      if (!literal) {
        if (!WRAPPERS.has(file)) dynamic.push(`${where} ${hit[1]}`);
        continue;
      }
      calls += 1;
      if (!known.has(literal[2]) || internal.has(literal[2])) unknown.push(`${where} ${literal[2]}`);
    }
  }
  assert.deepEqual(unknown, [], "actions that are not in desktop/actions.json");
  assert.deepEqual(dynamic, [], "AW.request and AW.jobs.run need a string literal as their first argument");
  assert.ok(calls > 0);
});
