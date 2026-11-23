"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { PassThrough } = require("node:stream");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {
  createClient,
  normalise,
  LIMITS,
} = require("../desktop/bridge-client.cjs");

const ACTIONS = {
  aliases: { state: "app-state" },
  actions: {
    "app-state": { class: "standard", via: "renderer", retry: true },
    "launch-save": { class: "quick", via: "renderer" },
    "server-start": {
      class: "long",
      via: "renderer",
      events: true,
      cancel: true,
    },
    "machine-install": { class: "long", via: "renderer" },
    "machine-probe": { class: "standard", via: "renderer", retry: true },
    "job-cancel": { class: "quick", via: "main" },
  },
};

const scratch = [];
function folder() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "awb-client-"));
  scratch.push(dir);
  return dir;
}
test.after(() => {
  for (const dir of scratch) fs.rmSync(dir, { recursive: true, force: true });
});

function harness(extra = {}) {
  const dir = folder();
  const children = [];
  const events = [];
  const timers = [];
  let clock = 1000;
  const spawn = (command, args, opts) => {
    if (extra.spawnError) {
      const c = new EventEmitter();
      c.stdin = new PassThrough();
      c.stdout = new PassThrough();
      children.push(c);
      setImmediate(() =>
        c.emit(
          "error",
          Object.assign(new Error("spawn ENOENT"), { code: "ENOENT" }),
        ),
      );
      return c;
    }
    const c = new EventEmitter();
    c.pid = 4000 + children.length;
    c.sent = [];
    c.stdin = new PassThrough();
    readlineOn(c.stdin, (line) => c.sent.push(JSON.parse(line)));
    c.stdout = new PassThrough();
    c.say = (m) => c.stdout.write(JSON.stringify(m) + "\n");
    c.kill = () => {
      c.killed = true;
      setImmediate(() => c.emit("exit", null, "SIGTERM"));
    };
    c.stdin.on("finish", () => {
      c.ended = true;
    });
    c.stdio = opts.stdio;
    children.push(c);
    return c;
  };
  const client = createClient({
    actions: ACTIONS,
    command: "/venv/bin/python",
    args: ["-u", "desktop_bridge.py"],
    cwd: dir,
    env: {},
    logPath: path.join(dir, "data", "bridge.log"),
    emit: (event, data) => events.push([event, data]),
    spawn,
    setTimeout: (fn, ms) => {
      const t = { fn, ms, live: true };
      timers.push(t);
      return t;
    },
    clearTimeout: (t) => {
      if (t) t.live = false;
    },
    now: () => clock,
  });
  const fire = (ms) => {
    for (const t of timers.filter((t) => t.live && t.ms === ms)) {
      t.live = false;
      t.fn();
    }
  };
  return {
    client,
    children,
    events,
    timers,
    fire,
    dir,
    tick: (ms) => (clock += ms),
  };
}
function readlineOn(stream, fn) {
  require("node:readline").createInterface({ input: stream }).on("line", fn);
}
const settle = () => new Promise((r) => setTimeout(r, 20));

test("request, reply, alias and handle stripping", async () => {
  const h = harness();
  const p = h.client.call("state", { handle: "mine", x: 1 });
  await settle();
  const c = h.children[0];
  assert.deepEqual(c.sent[0], {
    id: 1,
    action: "app-state",
    data: { x: 1 },
    handle: "mine",
    lane: "pool",
  });
  assert.equal(typeof c.stdio[2], "number");
  c.say({ event: "bridge-ready", data: { protocol: 2, pid: 9 } });
  c.say({ id: 1, value: { ok: 1 } });
  assert.deepEqual(await p, { ok: 1 });
  assert.deepEqual(
    h.events.map((e) => [e[0], e[1].state]),
    [
      ["bridge-state", "starting"],
      ["bridge-state", "ready"],
    ],
  );
  assert.match(
    fs.readFileSync(path.join(h.dir, "data", "bridge.log"), "utf8"),
    /starting \/venv\/bin\/python/,
  );
});

test("unknown names and prototype keys are refused", async () => {
  const h = harness();
  for (const name of ["nope", "constructor", "__proto__", 7])
    await assert.rejects(
      h.client.call(name, {}),
      (e) => e.message === "Unknown desktop action",
    );
  assert.equal(h.children.length, 0);
});

test("structured, string and empty errors", async () => {
  const h = harness();
  const a = h.client.call("launch-save", {}),
    b = h.client.call("launch-save", {}),
    c2 = h.client.call("launch-save", {});
  await settle();
  const c = h.children[0];
  c.say({
    id: 1,
    error: { message: "Choose an existing project folder.", action: "retry" },
  });
  c.say({ id: 2, error: "old style" });
  c.say({ id: 3, error: "" });
  await assert.rejects(
    a,
    (e) =>
      e.message === "Choose an existing project folder." &&
      e.action === "retry",
  );
  await assert.rejects(b, (e) => e.message === "old style");
  await assert.rejects(c2, (e) => /without a description/.test(e.message));
  assert.equal(normalise(null).message.length > 0, true);
});

test("job events, progress, cancel", async () => {
  const h = harness();
  const p = h.client.call("server-start", { handle: "start-1", preset: "x" });
  await settle();
  const c = h.children[0];
  assert.equal(c.sent[0].lane, "job");
  c.say({
    event: "job-progress",
    data: {
      handle: "start-1",
      action: "server-start",
      progress: { job: "model-start", phase: "loading" },
    },
  });
  c.say({ event: "something-else", data: {} });
  await settle();
  assert.deepEqual(h.client.jobs(), [
    {
      handle: "start-1",
      action: "server-start",
      started: 1000,
      cancellable: true,
      progress: { job: "model-start", phase: "loading" },
    },
  ]);
  await assert.rejects(
    h.client.call("server-start", { handle: "start-1" }),
    (e) => e.code === "busy",
  );
  const cancelled = h.client.cancel("start-1");
  await settle();
  assert.deepEqual(c.sent[1], {
    id: 2,
    action: "job-cancel",
    data: { handle: "start-1" },
    handle: "r3",
    lane: "pool",
  });
  c.say({ id: 2, value: true });
  assert.equal(await cancelled, true);
  c.say({
    id: 1,
    error: { message: "The model start was cancelled.", code: "cancelled" },
  });
  await assert.rejects(p, (e) => e.code === "cancelled");
  assert.equal(await h.client.cancel("start-1"), false);
  const names = h.events
    .filter((e) => e[0] !== "bridge-state")
    .map((e) => [e[0], e[1].state || e[1].progress.phase]);
  assert.deepEqual(names, [
    ["job-state", "started"],
    ["job-progress", "loading"],
    ["job-state", "cancelled"],
  ]);
  assert.equal(h.client.jobs().length, 0);
});

test("non-cancellable job is not cancelled", async () => {
  const h = harness();
  h.client.call("machine-install", { handle: "inst" }).catch(() => {});
  await settle();
  assert.equal(await h.client.cancel("inst"), false);
  assert.equal(h.children[0].sent.length, 1);
});

test("timeouts by class; long has none; late reply is dropped", async () => {
  const h = harness();
  const quick = h.client.call("launch-save", {});
  const long = h.client.call("server-start", { handle: "s" });
  long.catch(() => {});
  await settle();
  assert.deepEqual(
    h.timers.map((t) => t.ms),
    [LIMITS.quick],
  );
  h.fire(LIMITS.quick);
  await assert.rejects(
    quick,
    (e) => e.code === "timeout" && /within 20 seconds/.test(e.message),
  );
  h.children[0].say({ id: 1, value: true });
  await settle();
});

test("crash: retry flag resends once, others get bridge-stopped, then auto restart", async () => {
  const h = harness();
  const read = h.client.call("app-state", {});
  const write = h.client.call("launch-save", {});
  const job = h.client.call("server-start", { handle: "j" });
  await settle();
  h.children[0].say({ event: "bridge-ready", data: { pid: 1 } });
  await settle();
  fs.appendFileSync(
    path.join(h.dir, "data", "bridge.log"),
    "Traceback\nKeyError: 'x'\n",
  );
  h.children[0].emit("exit", 1, null);
  await assert.rejects(
    write,
    (e) =>
      e.code === "bridge-stopped" &&
      /exit code 1/.test(e.detail) &&
      /KeyError/.test(e.detail),
  );
  await assert.rejects(job, (e) => e.code === "bridge-stopped");
  assert.equal(h.client.status().state, "restarting");
  h.fire(250);
  await settle();
  assert.equal(h.children.length, 2);
  assert.equal(h.children[1].sent[0].action, "app-state");
  h.children[1].say({ event: "bridge-ready", data: { pid: 2 } });
  h.children[1].say({ id: h.children[1].sent[0].id, value: "again" });
  assert.equal(await read, "again");
  assert.equal(h.client.status().restarts, 1);
});

test("three crashes within a minute stop the restarts; next call tries again", async () => {
  const h = harness();
  const first = h.client.call("app-state", {});
  await settle();
  h.children[0].emit("exit", 1, null);
  h.fire(250);
  await settle();
  h.children[1].emit("exit", 1, null);
  await assert.rejects(
    first,
    (e) =>
      e.code === "bridge-failed" && /stopped while starting/.test(e.message),
  );
  h.fire(500);
  await settle();
  h.children[2].emit("exit", 1, null);
  await settle();
  assert.equal(h.client.status().state, "failed");
  assert.equal(h.events.at(-1)[1].state, "failed");
  assert.equal(h.timers.filter((t) => t.live).length, 0);
  h.client.call("launch-save", {}).catch(() => {});
  await settle();
  assert.equal(h.children.length, 4);
});

test("missing interpreter", async () => {
  const h = harness({ spawnError: true });
  const p = h.client.call("launch-save", {});
  await assert.rejects(
    p,
    (e) =>
      e.code === "bridge-failed" &&
      /could not start Python at \/venv\/bin\/python/.test(e.message) &&
      /spawn ENOENT/.test(e.detail),
  );
});

test("stop ends stdin, kills after the grace period, rejects pending", async () => {
  const h = harness();
  const p = h.client.call("server-start", { handle: "s" });
  await settle();
  const done = h.client.stop();
  await settle();
  assert.equal(h.children[0].ended, true);
  h.fire(2500);
  await done;
  assert.equal(h.children[0].killed, true);
  await assert.rejects(
    p,
    (e) =>
      e.code === "bridge-stopped" &&
      e.message === "Agent Workbench is closing.",
  );
  await assert.rejects(
    h.client.call("launch-save", {}),
    (e) => e.code === "bridge-stopped",
  );
  assert.equal(h.children.length, 1);
});

test("a spawn that throws is treated as a failed start", async () => {
  const dir = folder();
  const timers = [];
  const client = createClient({
    actions: ACTIONS,
    command: "python",
    args: [],
    cwd: dir,
    env: {},
    logPath: path.join(dir, "bridge.log"),
    spawn: () => {
      throw Object.assign(new Error("bad"), { code: "EINVAL" });
    },
    setTimeout: (fn, ms) => {
      timers.push(fn);
      return fn;
    },
    clearTimeout: () => {},
    now: () => 5,
  });
  const p = client.call("launch-save", {});
  timers.shift()();
  timers.shift()();
  await assert.rejects(
    p,
    (e) => e.code === "bridge-failed" && /spawn EINVAL/.test(e.detail),
  );
  assert.equal(client.status().state, "failed");
});

test("the log tail is cut by bytes, not characters", async () => {
  const h = harness();
  const p = h.client.call("launch-save", {});
  await settle();
  h.children[0].say({ event: "bridge-ready", data: {} });
  await settle();
  fs.appendFileSync(
    path.join(h.dir, "data", "bridge.log"),
    "ValueError: mappe → fel\n",
  );
  h.children[0].emit("exit", 1, null);
  await assert.rejects(
    p,
    (e) => e.detail === "exit code 1\nValueError: mappe → fel",
  );
});

test("a call that meets the last failed start is rejected, not parked", async () => {
  const dir = folder();
  const timers = [];
  const events = [];
  let attempts = 0;
  const client = createClient({
    actions: ACTIONS,
    command: "python",
    args: [],
    cwd: dir,
    env: {},
    logPath: path.join(dir, "bridge.log"),
    emit: (event, data) => events.push([event, data.state]),
    spawn: () => {
      attempts += 1;
      throw Object.assign(new Error("bad"), { code: "EMFILE" });
    },
    setTimeout: (fn) => {
      timers.push(fn);
      return fn;
    },
    clearTimeout: () => {},
    now: () => 5,
  });
  const first = client.call("launch-save", {});
  timers.shift()();
  const second = client.call("server-start", { handle: "s" });
  await assert.rejects(first, (e) => e.code === "bridge-failed");
  await assert.rejects(
    second,
    (e) => e.code === "bridge-failed" && /spawn EMFILE/.test(e.detail),
  );
  assert.equal(attempts, 3);
  assert.equal(client.status().state, "failed");
  assert.deepEqual(client.jobs(), []);
  assert.deepEqual(
    events.filter((e) => e[0] === "job-state"),
    [
      ["job-state", "started"],
      ["job-state", "failed"],
    ],
  );
});

test("a payload that cannot be written as JSON is rejected, not left waiting", async () => {
  const h = harness();
  const loop = { workspace: "/w" };
  loop.self = loop;
  for (const data of [{ handle: "go-1", size: 10n }, loop])
    await assert.rejects(
      h.client.call("machine-install", data),
      (e) => e.code === "internal" && /Restart Agent Workbench/.test(e.message),
    );
  assert.deepEqual(h.client.jobs(), []);
  assert.deepEqual(
    h.events.filter((e) => e[0] === "job-state"),
    [],
  );
  const p = h.client.call("machine-install", { handle: "go-1" });
  await settle();
  assert.equal(h.children[0].sent.length, 1);
  h.children[0].say({ id: h.children[0].sent[0].id, value: true });
  assert.equal(await p, true);
});

test("a job queued behind failing starts ends with a failed job-state", async () => {
  const dir = folder();
  const timers = [];
  const events = [];
  const client = createClient({
    actions: ACTIONS,
    command: "python",
    args: [],
    cwd: dir,
    env: {},
    logPath: path.join(dir, "bridge.log"),
    emit: (event, data) => events.push([event, data.state]),
    spawn: () => {
      throw Object.assign(new Error("bad"), { code: "EMFILE" });
    },
    setTimeout: (fn) => {
      timers.push(fn);
      return fn;
    },
    clearTimeout: () => {},
    now: () => 5,
  });
  const job = client.call("machine-install", { handle: "inst" });
  assert.deepEqual(
    client.jobs().map((row) => row.handle),
    ["inst"],
  );
  timers.shift()();
  timers.shift()();
  await assert.rejects(job, (e) => e.code === "bridge-failed");
  assert.deepEqual(client.jobs(), []);
  assert.deepEqual(
    events.filter((e) => e[0] === "job-state"),
    [
      ["job-state", "started"],
      ["job-state", "failed"],
    ],
  );
});

test("the shipped table loads", () => {
  const actions = require("../desktop/actions.json");
  const client = createClient({ actions, command: "python", args: [] });
  assert.equal(actions.protocol, 2);
  assert.equal(client.lookup("serve").name, "server-start");
  assert.equal(client.lookup("serve").spec.cancel, true);
  assert.equal(client.lookup("studio-api-key").spec.via, "internal");
  assert.equal(client.lookup("toString"), null);
  for (const [name, spec] of Object.entries(actions.actions))
    assert.ok(spec.class in LIMITS, name);
  assert.deepEqual(client.status(), {
    state: "stopped",
    restarts: 0,
    pid: null,
    log: undefined,
  });
});
