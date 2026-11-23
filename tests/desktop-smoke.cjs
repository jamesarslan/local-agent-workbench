// Run with Node 22+ and a sandbox-capable desktop (or xvfb-run on Linux).
const { spawn } = require("node:child_process");
const fs = require("node:fs"),
  os = require("node:os"),
  path = require("node:path"),
  assert = require("node:assert/strict");
const root = path.resolve(__dirname, ".."),
  win = process.platform === "win32",
  mac = process.platform === "darwin";
const python =
  process.env.WORKBENCH_PYTHON ||
  path.join(root, win ? ".venv/Scripts/python.exe" : ".venv-linux/bin/python");
const electron =
  process.env.WORKBENCH_ELECTRON ||
  require(path.join(root, "desktop/node_modules/electron"));
const base = fs.mkdtempSync(path.join(os.tmpdir(), "workbench-smoke-"));
const folder = path.join(base, "Sample project"),
  other = path.join(base, "Second project"),
  data = path.join(base, "data"),
  user = path.join(base, "chromium"),
  fresh = path.join(base, "fresh-data"),
  freshUser = path.join(base, "fresh-chromium"),
  cloned = path.join(base, "Cloned project"),
  declined = path.join(base, "Declined project");
for (const dir of [folder, other, data, fresh]) fs.mkdirSync(dir);
// Two folders that arrive with a saved folder default, as a clone from another computer would.
for (const dir of [cloned, declined]) {
  fs.mkdirSync(path.join(dir, ".agent-workbench"), { recursive: true });
  fs.writeFileSync(
    path.join(dir, ".agent-workbench", "profiles.json"),
    JSON.stringify({ "Folder default": { default: true, harness: "OMP", search: false, context: 8192 } }),
  );
}
fs.writeFileSync(
  path.join(data, "settings.json"),
  JSON.stringify({
    workspace: folder,
    harness: "OMP",
    blender: false,
    search: true,
    firecrawl: false,
  }),
);
fs.writeFileSync(
  path.join(data, "onboarding.json"),
  JSON.stringify({
    schema: 1,
    existing_install: true,
    completed_at: null,
    skipped: false,
    steps: {},
    choices: {},
  }),
);
const history = path.join(
  base,
  ".omp/profiles/workbench-fixture/agent/sessions/sample",
);
fs.mkdirSync(history, { recursive: true });
fs.writeFileSync(
  path.join(history, "sample.jsonl"),
  [
    { type: "title", title: "Fixture session" },
    { type: "session", cwd: folder, timestamp: "2026-11-22" },
    {
      type: "message",
      message: {
        role: "assistant",
        content: [{ type: "text", text: "Fixture observed" }],
      },
    },
  ]
    .map((x) => JSON.stringify(x))
    .join("\n") + "\n",
);
const { ELECTRON_RUN_AS_NODE: _nodeMode, ...parentEnv } = process.env;
function launch(dataDir, userDir, port) {
  return spawn(
    electron,
    [
      "--remote-debugging-address=127.0.0.1",
      "--remote-debugging-port=0",
      "--user-data-dir=" + userDir,
      path.join(root, "desktop"),
    ],
    {
      cwd: root,
      env: {
        ...parentEnv,
        HOME: base,
        USERPROFILE: base,
        WORKBENCH_PYTHON: python,
        WORKBENCH_DATA: dataDir,
        WORKBENCH_PORT: port,
      },
      stdio: "ignore",
    },
  );
}
const app = launch(data, user, "10578");
let second = null;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const LIMIT = 240000,
  CALL = 30000;
let passed = false,
  abort;
const aborted = new Promise((_, rej) => (abort = rej));
process.on("exit", () => {
  if (!passed) process.exitCode = 1;
});
app.once("error", (e) => abort(Error("Electron did not start: " + e.message)));
app.once("exit", (code, signal) =>
  abort(Error("Electron exited before the test finished (" + (signal || code) + ")")),
);
const limit = setTimeout(
  () => abort(Error("Timed out: the whole run took longer than " + LIMIT / 1000 + " s")),
  LIMIT,
);
async function until(fn, label) {
  for (let i = 0; i < 180; i++) {
    if (await fn()) return;
    await sleep(100);
  }
  throw Error("Timed out: " + label);
}
// One DevTools connection to the page of an Electron started with `userDir`; onClose runs when the socket closes unexpectedly.
async function connect(userDir, onClose) {
  const portFile = path.join(userDir, "DevToolsActivePort");
  await until(() => fs.existsSync(portFile), "Electron startup");
  const port = fs.readFileSync(portFile, "utf8").split("\n")[0];
  let tabs = [];
  await until(async () => {
    tabs = (
      await (await fetch("http://127.0.0.1:" + port + "/json/list")).json()
    ).filter((tab) => tab.type === "page");
    return tabs.length > 0;
  }, "the Agent Workbench window");
  const socket = new WebSocket(tabs[0].webSocketDebuggerUrl);
  let seq = 0,
    closing = false;
  const pending = new Map();
  await new Promise((res, rej) => {
    socket.addEventListener("open", res, { once: true });
    socket.addEventListener(
      "error",
      () => rej(Error("The DevTools socket did not open")),
      { once: true },
    );
  });
  socket.addEventListener("close", () => {
    if (!closing) onClose(Error("The DevTools socket closed before the test finished"));
  });
  socket.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (pending.has(m.id)) {
      pending.get(m.id)(m);
      pending.delete(m.id);
    }
  };
  function cdp(method, params = {}) {
    return new Promise((res, rej) => {
      const id = ++seq;
      const timer = setTimeout(() => {
        pending.delete(id);
        rej(Error("Timed out: no reply to " + method + " in " + CALL / 1000 + " s"));
      }, CALL);
      pending.set(id, (m) => {
        clearTimeout(timer);
        m.error ? rej(Error(m.error.message)) : res(m.result);
      });
      socket.send(JSON.stringify({ id, method, params }));
    });
  }
  async function js(expression) {
    const r = await cdp("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (r.exceptionDetails)
      throw Error(
        r.exceptionDetails.exception?.description || r.exceptionDetails.text,
      );
    return r.result.value;
  }
  await cdp("Page.bringToFront");
  await cdp("Emulation.setFocusEmulationEnabled", { enabled: true });
  return {
    cdp,
    js,
    wait: (exp) => until(() => js(exp), exp),
    click: (id) =>
      js("document.getElementById(" + JSON.stringify(id) + ").click()"),
    close() {
      closing = true;
      socket.close();
    },
  };
}
let page;
async function test() {
  page = await connect(user, abort);
  const { cdp, js, wait, click } = page;
  await wait(
    "document.getElementById('project-switch')?.textContent.includes('Sample project')",
  );
  // (1) An existing install with onboarding.json lands on Launch, not in the setup guide.
  await wait("!document.getElementById('launch-view').hidden");
  assert.equal(await js("document.getElementById('guide-view').hidden"), true);
  await click("theme-switch");
  assert.equal(await js("document.documentElement.dataset.theme"), "dark");
  await click("blender");
  assert.equal(await js("form().blender"), true);
  await click("terminal-toggle");
  await click("term-watch");
  await wait("document.getElementById('session-picker').open");
  await wait(
    "!document.getElementById('saved-sessions').hasAttribute('aria-busy') && !!document.querySelector('#saved-sessions button')",
  );
  await js("document.querySelector('#saved-sessions button').click()");
  const buffer =
    "Array.from({length:terminal?.buffer.active.length||0},(_,i)=>terminal.buffer.active.getLine(i).translateToString(true)).join(' ')";
  await wait(buffer + ".includes('Fixture observed')");
  assert.equal(await js("document.getElementById('session-picker').open"), false);
  await click("term-shell");
  await wait(
    "document.getElementById('terminal-list').selectedOptions[0]?.textContent.includes('Shell')",
  );
  const line = win
    ? "Write-Output ('verified-' + (6*7))"
    : "printf 'verified-%s\\n' $((6*7))";
  await js(
    "request('term-write',{...termData(),text:" +
      JSON.stringify(line + "\r") +
      "})",
  );
  await wait(buffer + ".includes('verified-42')");
  if (process.env.WORKBENCH_TEST_CLIPBOARD === "1") {
    // Run on a disposable display so the test never replaces a user's clipboard.
    await js(`(() => {
      terminal.focus();
      const b = terminal.buffer.active;
      for (let y = 0; y < b.length; y++) {
        const x = b.getLine(y).translateToString().indexOf("verified-42");
        if (x >= 0) { terminal.select(x, y, 11); return; }
      }
      throw Error("Expected terminal selection");
    })()`);
    async function shortcut(key, code, modifiers) {
      for (const type of ["keyDown", "keyUp"]) {
        await cdp("Input.dispatchKeyEvent", { type, key, code, modifiers });
      }
    }
    // Copy and paste keys inside the terminal (ui.md 2.5): Cmd C and Cmd V on macOS, Ctrl C and Ctrl Shift V elsewhere.
    await shortcut("c", "KeyC", mac ? 4 : 2);
    await wait("navigator.clipboard.readText().then(t => t === 'verified-42')");
    assert.equal(await js("terminal.hasSelection()"), false);
    await js("terminal.focus()");
    await js("request('term-write',{...termData(),text:\"echo pasted-\"})");
    await shortcut(mac ? "v" : "V", "KeyV", mac ? 4 : 10);
    await wait(buffer + ".includes('echo pasted-verified-42')");
    await js("request('term-write',{...termData(),text:'\\r'})");
    await wait(buffer + ".includes('pasted-verified-42')");
  }
  await js(
    "document.getElementById('workspace').value=" +
      JSON.stringify(other) +
      ";projectChanged()",
  );
  await wait("!terminalID && !document.getElementById('blender').checked");
  await js(
    "document.getElementById('workspace').value=" +
      JSON.stringify(folder) +
      ";projectChanged()",
  );
  await wait("terminalID && document.getElementById('blender').checked");
  await js("window.confirm=()=>true");
  await click("term-close");
  await wait("document.getElementById('terminal-list').options.length===1");

  // (2) A refused action reaches the page as an error object with a sentence, both raw from the bridge and through request().
  const refused = (call) =>
    js(
      call +
        ".then(() => 'resolved', (e) => e && typeof e === 'object' && typeof e.message === 'string' && e.message.trim() ? 'rejected' : 'malformed: ' + JSON.stringify(e))",
    );
  assert.equal(await refused("window.workbench.request('no-such-action', {})"), "rejected");
  assert.equal(await refused("request('no-such-action')"), "rejected");

  // (3) Killing this window's Python service shows the restarting notice, then the running-again notice, and requests work again.
  const status = await js("request('bridge-status')");
  assert.equal(status.state, "ready");
  assert.ok(Number.isInteger(status.pid) && status.pid > 0, "bridge-status has no pid");
  await js(`(() => {
    window.smokeNotices = [];
    const record = (node) => {
      if (node.nodeType === 1 && node.classList.contains("notice")) window.smokeNotices.push(node.textContent);
    };
    new MutationObserver((changes) => changes.forEach((c) => c.addedNodes.forEach(record)))
      .observe(document.getElementById("notices"), { childList: true });
  })()`);
  process.kill(status.pid, "SIGKILL");
  await wait(
    "smokeNotices.findIndex(t => t.includes('is restarting')) >= 0 && smokeNotices.findIndex(t => t.includes('running again')) > smokeNotices.findIndex(t => t.includes('is restarting'))",
  );
  await wait("request('bridge-status').then(s => s.state === 'ready')");
  const after = await js("request('bridge-status')");
  assert.notEqual(after.pid, status.pid);
  const state = await js("request('app-state')");
  assert.equal(typeof state.settings, "object");
  await wait(
    "document.getElementById('term-info').textContent === 'This terminal was closed because the Workbench service restarted.'",
  );

  // (4) A folder with a saved default on a computer that remembers nothing for it: Launch offers a review, never applies
  // on its own; applying goes through the confirm dialog, and a dismissal is remembered for the folder.
  const notice = (word) =>
    "[...document.querySelectorAll('#launch-notices .notice')].find(n => n.textContent.includes(" + JSON.stringify(word) + "))";
  await js("AW.project.select(" + JSON.stringify(cloned) + ")");
  await wait(notice("This folder has saved settings: Folder default."));
  const before = await js("request('folder-get',{workspace:" + JSON.stringify(cloned) + "})");
  assert.equal(before.known, false);
  assert.equal(before.offer_default, true);
  await js(notice("This folder has saved settings") + ".querySelector('button').click()");
  await wait("document.getElementById('profile-dialog').open && [...document.querySelectorAll('#profile-dialog-body button')].some(b => b.textContent.trim() === 'Apply profile')");
  await js("[...document.querySelectorAll('#profile-dialog-body button')].find(b => b.textContent.trim() === 'Apply profile').click()");
  await wait("!document.getElementById('profile-dialog').open");
  await wait("request('folder-get',{workspace:" + JSON.stringify(cloned) + "}).then(f => f.known && !f.offer_default && f.profile === 'Folder default')");
  await wait("!" + notice("This folder has saved settings"));
  const places = await js("request('folder-places',{workspace:" + JSON.stringify(cloned) + "})");
  assert.deepEqual(places.map((row) => row.id), ["profiles", "project", "instructions", "skills", "memory", "connections", "keys"]);
  await js("AW.project.select(" + JSON.stringify(declined) + ")");
  await wait(notice("This folder has saved settings: Folder default."));
  await js(notice("This folder has saved settings") + ".querySelector('.notice-actions button:last-child').click()");
  await wait("request('folder-get',{workspace:" + JSON.stringify(declined) + "}).then(f => !f.known && !f.offer_default && f.saved_default === 'Folder default')");
  await js("AW.project.select(" + JSON.stringify(folder) + ")");
  await wait("!" + notice("This folder has saved settings"));

  // (5) A second start with an empty data folder opens the setup guide; leaving it shows Launch with Launch blocked.
  second = launch(fresh, freshUser, "10579");
  const secondGone = new Promise((_, rej) => {
    second.once("error", (e) => rej(Error("The second Electron did not start: " + e.message)));
    second.once("exit", (code, signal) =>
      rej(Error("The second Electron exited before its case finished (" + (signal || code) + ")")),
    );
  });
  let fresher;
  try {
    await Promise.race([
      secondGone,
      (async () => {
        fresher = await connect(freshUser, abort);
        await fresher.wait("document.getElementById('guide-view')?.hidden === false");
        const exit = await fresher.js(`(() => {
          const buttons = [...document.querySelectorAll('#guide-view button, #guide-rail button')];
          const skip = buttons.find((b) => b.textContent.trim() === 'Skip setup');
          if (skip) { skip.click(); return 'Skip setup'; }
          if (document.querySelector('#guide-view .stub-text')) {
            const close = buttons.find((b) => b.textContent.trim() === 'Close guide');
            if (close) { close.click(); return 'Close guide'; }
          }
          return '';
        })()`);
        assert.ok(exit, "The setup guide has no Skip setup button");
        if (exit !== "Skip setup")
          console.log(
            "Notice: the setup guide is still the foundation stub, so case 4 left it with Close guide instead of Skip setup.",
          );
        await fresher.wait(
          "!document.getElementById('launch-view').hidden && document.getElementById('guide-view').hidden",
        );
        await fresher.wait(
          "(b => b && !b.dataset.starting && b.getAttribute('aria-disabled') === 'true')(document.getElementById('launch'))",
        );
      })(),
    ]);
  } finally {
    fresher?.close();
    second.removeAllListeners("exit");
    if (second.exitCode === null) second.kill();
  }
  passed = true;
  console.log(
    "Passed: theme, per-project tool choices, OMP transcript watcher, interactive PTY, project switch and close, refused action, service restart, folder default review, first run",
  );
}
Promise.race([test(), aborted])
  .catch((e) => {
    passed = false;
    console.error(e);
  })
  .finally(async () => {
    clearTimeout(limit);
    page?.close();
    if (second && second.exitCode === null) second.kill();
    if (app.exitCode === null) app.kill();
    await sleep(400);
    try {
      fs.rmSync(base, { recursive: true, force: true, maxRetries: 3 });
    } catch {}
    process.exit(passed ? 0 : 1);
  });
