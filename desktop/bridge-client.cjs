"use strict";
// Transport between the Electron main process and desktop_bridge.py. No Electron imports: testable with plain Node.
const nodePath = require("node:path");
const readline = require("node:readline");

const LIMITS = { quick: 20000, standard: 75000, slow: 480000, long: 0 };
const HANDLE = /^[A-Za-z0-9_-]{1,40}$/;
const FORWARDED = new Set(["job-progress"]);
const CRASH_WINDOW = 60000;
const CRASH_LIMIT = 3;
const QUIT_GRACE = 2500;
const CLOSING = {
  message: "Agent Workbench is closing.",
  code: "bridge-stopped",
};
const UNSENDABLE = {
  message:
    "This request holds a value Agent Workbench cannot send to its service. Restart Agent Workbench, then try again.",
  code: "internal",
};
const CONTROL = { class: "quick" };
const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);

function normalise(error) {
  const out =
    error && typeof error === "object" && !Array.isArray(error)
      ? { ...error }
      : { message: typeof error === "string" ? error : "" };
  if (typeof out.message !== "string" || !out.message.trim())
    out.message =
      "The Workbench service reported a problem without a description. Try again.";
  return out;
}

function createClient(options) {
  const spawn = options.spawn || require("node:child_process").spawn;
  const fs = options.fs || require("node:fs");
  const later = options.setTimeout || setTimeout;
  const never = options.clearTimeout || clearTimeout;
  const now = options.now || Date.now;
  const emit = options.emit || (() => {});
  const table = options.actions.actions;
  const aliases = options.actions.aliases || {};
  const pending = new Map(); // wire id -> entry
  let queue = []; // entries waiting for the next bridge process
  let child = null;
  let state = "stopped";
  let sequence = 0;
  let calls = 0;
  let crashes = [];
  let restarts = 0;
  let lastError = null;
  let logStart = 0;
  let stopping = null;

  function lookup(name) {
    if (typeof name !== "string") return null;
    const canonical = own(aliases, name) ? aliases[name] : name;
    return own(table, canonical)
      ? { name: canonical, spec: table[canonical] }
      : null;
  }

  function announce(next, extra = {}) {
    state = next;
    emit("bridge-state", { state, restarts, ...extra });
  }

  function jobEvent(entry, stateName, error) {
    if (entry.lane !== "job") return;
    const data = {
      handle: entry.handle,
      action: entry.action,
      state: stateName,
      cancellable: Boolean(entry.spec.cancel),
      started: entry.started,
    };
    if (error) data.error = error;
    emit("job-state", data);
  }

  function logTail() {
    try {
      const text = fs
        .readFileSync(options.logPath)
        .subarray(logStart)
        .toString("utf8")
        .slice(-4000);
      return text
        .split(/\r?\n/)
        .filter((line) => line.trim())
        .slice(-20)
        .join("\n");
    } catch {
      return "";
    }
  }

  function openLog() {
    try {
      fs.mkdirSync(nodePath.dirname(options.logPath), {
        recursive: true,
        mode: 0o700,
      });
      try {
        if (fs.statSync(options.logPath).size > 1048576)
          fs.renameSync(options.logPath, options.logPath + ".1");
      } catch {}
      const fd = fs.openSync(options.logPath, "a", 0o600);
      fs.writeSync(
        fd,
        `--- ${new Date(now()).toISOString()} starting ${options.command}\n`,
      );
      logStart = fs.fstatSync(fd).size;
      return fd;
    } catch {
      logStart = 0;
      return "ignore";
    }
  }

  function start() {
    if (child || stopping) return;
    const stderr = openLog();
    announce("starting");
    let proc = null;
    let failure = null;
    try {
      proc = spawn(options.command, options.args, {
        cwd: options.cwd,
        env: options.env,
        windowsHide: true,
        stdio: ["pipe", "pipe", stderr],
      });
    } catch (error) {
      failure = error;
    }
    if (typeof stderr === "number") {
      try {
        fs.closeSync(stderr);
      } catch {}
    }
    if (!proc) {
      lost({ error: failure });
      return;
    }
    child = proc;
    let over = false;
    const down = (info) => {
      if (over) return;
      over = true;
      if (child === proc) child = null;
      lost(info);
    };
    proc.on("error", (error) => down({ error }));
    proc.on("exit", (code, signal) => down({ code, signal }));
    proc.stdin.on("error", () => {});
    readline.createInterface({ input: proc.stdout }).on("line", (line) => {
      if (child === proc) receive(line);
    });
    const waiting = queue;
    queue = [];
    for (const entry of waiting) transmit(entry);
  }

  function receive(line) {
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      return;
    }
    if (!message || typeof message !== "object") return;
    if (typeof message.event === "string") {
      const data =
        message.data && typeof message.data === "object" ? message.data : {};
      if (message.event === "bridge-ready") {
        if (state === "starting") announce("ready", { pid: data.pid });
      } else if (FORWARDED.has(message.event)) {
        for (const entry of pending.values())
          if (entry.handle === data.handle) entry.progress = data.progress;
        emit(message.event, data);
      }
      return;
    }
    const entry = pending.get(message.id);
    if (!entry) return;
    pending.delete(message.id);
    never(entry.timer);
    if (own(message, "error")) {
      const error = normalise(message.error);
      jobEvent(
        entry,
        error.code === "cancelled" ? "cancelled" : "failed",
        error,
      );
      entry.reject(error);
    } else {
      jobEvent(entry, "done");
      entry.resolve(message.value);
    }
  }

  function transmit(entry) {
    start();
    if (!child) {
      if (state === "failed") {
        jobEvent(entry, "failed", lastError);
        entry.reject(lastError);
      } else queue.push(entry);
      return;
    }
    entry.id = ++sequence;
    pending.set(entry.id, entry);
    const limit = LIMITS[entry.spec.class] || 0;
    if (limit)
      entry.timer = later(() => {
        if (pending.get(entry.id) !== entry) return;
        pending.delete(entry.id);
        if (entry.spec.cancel)
          write({
            id: ++sequence,
            action: "job-cancel",
            data: { handle: entry.handle },
          });
        const error = {
          message: `Agent Workbench did not get an answer within ${limit / 1000} seconds. Try again; if it keeps happening, restart Agent Workbench.`,
          detail: `${entry.action} (${entry.spec.class})`,
          code: "timeout",
          action: "retry",
        };
        jobEvent(entry, "failed", error);
        entry.reject(error);
      }, limit);
    write({
      id: entry.id,
      action: entry.action,
      data: entry.data,
      handle: entry.handle,
      lane: entry.lane,
    });
  }

  function write(message) {
    try {
      child.stdin.write(JSON.stringify(message) + "\n");
    } catch {}
  }

  function lost(info) {
    const how = info.error
      ? `spawn ${info.error.code || info.error.message}`
      : info.signal
        ? `signal ${info.signal}`
        : `exit code ${info.code}`;
    const detail = [how, logTail()].filter(Boolean).join("\n");
    const entries = [...pending.values()];
    pending.clear();
    for (const entry of entries) never(entry.timer);
    if (stopping) {
      for (const entry of [...entries, ...queue]) entry.reject({ ...CLOSING });
      queue = [];
      state = "stopped";
      stopping.done();
      return;
    }
    const wasReady = state === "ready";
    const stamp = now();
    crashes = crashes.filter((t) => stamp - t < CRASH_WINDOW);
    crashes.push(stamp);
    const failed = crashes.length >= CRASH_LIMIT;
    const error =
      info.error && info.error.code === "ENOENT"
        ? {
            message: `Agent Workbench could not start Python at ${options.command}. Start it with its launcher so the right Python is used.`,
            detail,
            code: "bridge-failed",
            action: "restart",
          }
        : !wasReady || failed
          ? {
              message: `The Workbench service ${wasReady ? "keeps stopping" : "stopped while starting"}. Open ${options.logPath} to see why, then restart Agent Workbench.`,
              detail,
              code: "bridge-failed",
              action: "log",
            }
          : {
              message:
                "The Workbench service stopped unexpectedly and was restarted. Embedded terminals were closed; try again.",
              detail,
              code: "bridge-stopped",
              action: "retry",
            };
    lastError = error;
    for (const entry of entries) {
      if (!failed && entry.spec.retry && !entry.retried) {
        entry.retried = true;
        queue.push(entry);
      } else {
        jobEvent(entry, "failed", error);
        entry.reject(error);
      }
    }
    if (failed) {
      for (const entry of queue) {
        jobEvent(entry, "failed", error);
        entry.reject(error);
      }
      queue = [];
      announce("failed", { message: error.message, detail });
      return;
    }
    restarts += 1;
    announce("restarting", {
      message: "The Workbench service stopped and is being restarted.",
      detail,
    });
    later(() => start(), 250 * 2 ** (crashes.length - 1));
  }

  function enqueue(name, spec, data, handle) {
    const lane =
      spec.class === "slow" || spec.class === "long" ? "job" : "pool";
    if (stopping) return Promise.reject({ ...CLOSING });
    if (lane === "job")
      for (const entry of [...pending.values(), ...queue])
        if (entry.lane === "job" && entry.handle === handle)
          return Promise.reject({
            message:
              "That task is already running. Wait for it to finish or cancel it first.",
            code: "busy",
          });
    return new Promise((resolve, reject) => {
      const entry = {
        action: name,
        spec,
        data,
        handle,
        lane,
        resolve,
        reject,
        started: now(),
        retried: false,
        progress: null,
        timer: null,
      };
      if (state === "failed") crashes = [];
      jobEvent(entry, "started");
      transmit(entry);
    });
  }

  function call(name, data) {
    const found = lookup(name);
    if (!found)
      return Promise.reject({
        message: "Unknown desktop action",
        code: "internal",
      });
    const body =
      data && typeof data === "object" && !Array.isArray(data)
        ? { ...data }
        : {};
    let handle = "r" + ++calls;
    if (typeof body.handle === "string" && HANDLE.test(body.handle))
      handle = body.handle;
    delete body.handle;
    // A BigInt or a circular value would make the wire write throw and leave the request without an answer.
    try {
      JSON.stringify(body);
    } catch {
      return Promise.reject({ ...UNSENDABLE });
    }
    return enqueue(found.name, found.spec, body, handle);
  }

  function cancel(handle) {
    const entry = [...pending.values()].find(
      (e) => e.lane === "job" && e.handle === handle,
    );
    if (!entry || !entry.spec.cancel) return Promise.resolve(false);
    return enqueue("job-cancel", CONTROL, { handle }, "r" + ++calls).then(
      Boolean,
      () => false,
    );
  }

  function jobs() {
    return [...pending.values(), ...queue]
      .filter((entry) => entry.lane === "job")
      .map((entry) => ({
        handle: entry.handle,
        action: entry.action,
        started: entry.started,
        cancellable: Boolean(entry.spec.cancel),
        progress: entry.progress,
      }));
  }

  function status() {
    const out = {
      state,
      restarts,
      pid: child ? child.pid : null,
      log: options.logPath,
    };
    if (lastError && state !== "ready") out.error = lastError;
    return out;
  }

  function stop() {
    if (stopping) return stopping.promise;
    let done;
    const promise = new Promise((resolve) => (done = resolve));
    stopping = { promise, done };
    if (!child) {
      for (const entry of queue) entry.reject({ ...CLOSING });
      queue = [];
      done();
      return promise;
    }
    const proc = child;
    const timer = later(() => {
      try {
        proc.kill();
      } catch {}
    }, QUIT_GRACE);
    promise.then(() => never(timer));
    try {
      proc.stdin.end();
    } catch {}
    return promise;
  }

  return { start, call, cancel, jobs, status, stop, lookup };
}

module.exports = { createClient, normalise, LIMITS };
