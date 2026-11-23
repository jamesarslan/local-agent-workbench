// Bridge events, tasks (requests on the job lane with progress and Cancel) and the Tasks popover (ui.md 5.13, 7, 9.6).
// Contract with ui/strip.js: this file opens the Tasks popover from #strip-tasks, #strip-service and Mod+. ; strip.js only writes the two segments' text.
// Beyond ui.md 9.6: records carry `ended` (ms), AW.jobs.open(anchor?) shows the Tasks popover, and a job-lane request sent with plain
// AW.request still appears as a task under a general title, because main reports every job with job-state.
(function (root) {
  "use strict";
  const AW = (root.AW = root.AW || {});
  const doc = root.document;
  const el = AW.el;
  const INTERRUPTED = "Interrupted: the Workbench service restarted.";
  const SPEAK_MS = 5000;
  const DOWNLOAD_POLL_MS = 2000;
  const SETUP_VERBS = { Install: "Installing ", Update: "Updating ", Login: "Opening sign-in for ", Docs: "Opening the guide for " };
  const TITLES = {
    "engine-install": "Installing the engine for local models",
    "model-download": (progress) => "Downloading " + (progress.label || "a model"),
    "server-start": "Starting the local model",
    "server-stop": "Stopping the local model",
    "studio-setup": (progress) => (SETUP_VERBS[progress.action] || "Setting up ") + (progress.name || "a tool"),
    "studio-tools": (progress) => (SETUP_VERBS[progress.action] || "Setting up ") + (progress.name || "a tool"),
    "launch-start": "Opening an agent in a terminal window",
    "term-open": "Opening a terminal session",
    "term-remote": "Opening a session on another machine",
    "account-oauth": "Waiting for the OpenRouter sign-in in your browser",
    "account-login": "Opening an agent sign-in",
    "machine-attach": "Connecting to a machine",
    "machine-connect": "Connecting to a machine",
    "machine-install": "Installing the managed model on a machine",
    "machine-pair": "Pairing with a machine",
    "machine-stop": "Stopping the model on a machine",
    "machine-remove": "Removing the runtime from a machine",
    "machine-session": "Opening an SSH terminal",
    "studio-download": (progress) => "Downloading " + (progress.label || "a model"),
    "studio-bench": "Running the local benchmark",
    "plugin-measure": "Measuring tool context cost",
    "plugin-benchmark": "Running the tool selection benchmark",
    "studio-box": "Opening the sandbox",
    "studio-web": "Starting or stopping Firecrawl",
    "studio-trace": "Starting a trace",
    "studio-import": "Scanning a model folder",
  };
  const KEYS = {
    "server-start": () => "server",
    "server-stop": () => "server-stop",
    "engine-install": () => "engine",
    "model-download": (progress) => (progress.id ? "model:" + progress.id : null),
    "studio-setup": (progress) => (progress.name ? "tool:" + progress.name : null),
  };

  const eventListeners = new Map();
  const jobListeners = new Set();
  const records = new Map();
  const own = new Map();
  const waits = new Map();
  const holds = new WeakMap();
  const paces = new Map();
  const spoken = new Map();
  const hubKeys = new Map();
  let counter = 0;
  let downloadTimer = null;

  AW.events = {
    on(name, fn) {
      if (!eventListeners.has(name)) eventListeners.set(name, new Set());
      eventListeners.get(name).add(fn);
      return () => eventListeners.get(name).delete(fn);
    },
  };
  function dispatch(name, data) {
    for (const key of [name, "*"])
      for (const fn of [...(eventListeners.get(key) || [])]) {
        try {
          fn(data, name);
        } catch (thrown) {
          console.error(thrown);
        }
      }
  }
  if (root.workbench && typeof root.workbench.subscribe === "function")
    root.workbench.subscribe((name, data) => dispatch(String(name), data && typeof data === "object" ? data : {}));

  function tell(record, kind) {
    for (const fn of [...jobListeners]) {
      try {
        fn(record, kind);
      } catch (thrown) {
        console.error(thrown);
      }
    }
  }

  const running = () => [...records.values()].filter((record) => record.state === "running");
  const shown = (node) => Boolean(node && node.isConnected && node.getClientRects().length);
  const titleFor = (action, progress) => {
    const title = TITLES[action];
    return typeof title === "function" ? title(progress || {}) : title || "Working";
  };

  function adopt(row) {
    const progress = row.progress && typeof row.progress === "object" ? row.progress : null;
    const action = String(row.action || "");
    const record = {
      handle: row.handle, action, key: KEYS[action] ? KEYS[action](progress || {}) : null, title: titleFor(action, progress), origin: null,
      started: Number(row.started) > 0 ? Number(row.started) : Date.now(), ended: null, cancellable: Boolean(row.cancellable), progress, state: "running", error: null, value: null,
    };
    records.set(record.handle, record);
    let settle;
    const promise = new Promise((resolve, reject) => (settle = { resolve, reject }));
    promise.catch(() => {});
    waits.set(record.handle, { promise, ...settle });
    watchDownloads();
    return record;
  }

  function hold(record) {
    const origin = record.origin;
    if (!origin || typeof origin.setAttribute !== "function") return;
    Promise.resolve().then(() => {
      if (record.state !== "running" || origin.dataset.pending || origin.dataset.job) return;
      holds.set(record, origin.getAttribute("aria-disabled"));
      origin.dataset.job = record.handle;
      origin.setAttribute("aria-disabled", "true");
    });
  }

  function release(record) {
    const origin = record.origin;
    if (!origin || !holds.has(record) || origin.dataset.job !== record.handle) return;
    const before = holds.get(record);
    holds.delete(record);
    delete origin.dataset.job;
    if (before === null) origin.removeAttribute("aria-disabled");
    else origin.setAttribute("aria-disabled", before);
  }

  function line(record) {
    const progress = record.progress || {};
    const parts = [typeof progress.message === "string" ? progress.message : ""];
    if (Number.isFinite(Number(progress.done)) && Number(progress.total) > 0) parts.push(AW.fmt.bytes(progress.done) + " of " + AW.fmt.bytes(progress.total));
    return parts.filter(Boolean).join(", ");
  }

  function speak(record) {
    const now = Date.now();
    if (now - (spoken.get(record.handle) || 0) < SPEAK_MS) return;
    spoken.set(record.handle, now);
    const text = line(record);
    if (text) AW.ui.announce(record.title + ". " + text);
  }

  function progressed(record, progress) {
    if (!progress || typeof progress !== "object") return;
    const next = { ...progress };
    if (Number.isFinite(Number(next.done)) && Number(next.total) > 0) {
      if (!paces.has(record.handle)) paces.set(record.handle, AW.fmt.pace());
      const pace = paces.get(record.handle);
      pace.add(Number(next.done));
      if (!(Number(next.rate) > 0)) next.rate = pace.rate();
    }
    record.progress = next;
    if (!record.origin && !own.has(record.handle)) {
      record.title = titleFor(record.action, next);
      if (!record.key && KEYS[record.action]) record.key = KEYS[record.action](next);
    }
    tell(record, "progress");
    speak(record);
  }

  function conclude(record, mine, focusWasInRow) {
    const title = record.title;
    const origin = record.origin;
    const held = Boolean(record.key) && [...doc.querySelectorAll("[data-job-key]")].some((node) => node.dataset.jobKey === record.key && shown(node));
    const watched = held || shown(origin) || tasksOpen() || [...doc.querySelectorAll(".task")].some((row) => row.dataset.handle === record.handle && shown(row));
    if (record.state === "done") {
      if (mine && !watched) AW.ui.toast(title + " finished.", { kind: "ok" });
      else AW.ui.announce(title + " finished.");
    } else if (record.state === "failed" && record.error && record.error.code === "busy") {
      if (mine && !watched) AW.ui.toast(record.error.message);
      else AW.ui.announce(record.error.message);
    } else if (record.state === "failed") {
      if (mine && !watched) AW.ui.toast(title + " failed.", { kind: "danger", action: { label: "Show", run: () => AW.jobs.open() } });
      else AW.ui.announce(title + " failed.");
    } else if (record.state === "cancelled" && record.error) {
      if (mine && !watched) AW.ui.toast(record.error.message);
      else AW.ui.announce(record.error.message);
    }
    if (focusWasInRow && shown(origin))
      requestAnimationFrame(() => {
        const active = doc.activeElement;
        if ((!active || active === doc.body) && shown(origin) && !doc.querySelector("dialog:modal")) origin.focus();
      });
  }

  function finish(record, state, { value = null, error = null } = {}) {
    if (record.state !== "running") return;
    const active = doc.activeElement;
    const row = active && active.closest ? active.closest(".task") : null;
    const focusWasInRow = Boolean(row && row.dataset.handle === record.handle && !row.closest(".popover"));
    record.state = state;
    record.ended = Date.now();
    record.value = value;
    record.error = error;
    paces.delete(record.handle);
    spoken.delete(record.handle);
    hubKeys.delete(record.handle);
    release(record);
    watchDownloads();
    tell(record, state === "done" ? "done" : state === "cancelled" ? "cancelled" : "failed");
    if (state !== "interrupted") conclude(record, own.has(record.handle), focusWasInRow);
    own.delete(record.handle);
    const wait = waits.get(record.handle);
    waits.delete(record.handle);
    if (wait) {
      if (state === "done") wait.resolve(value);
      else wait.reject(error || { message: INTERRUPTED, code: "bridge-stopped" });
    }
  }

  const stateOf = (error) => (error.code === "cancelled" ? "cancelled" : error.code === "bridge-stopped" ? "interrupted" : "failed");

  AW.events.on("job-state", (data) => {
    if (typeof data.handle !== "string" || !data.handle) return;
    const record = records.get(data.handle);
    if (!record) {
      if (data.state === "started") tell(adopt(data), "started");
      return;
    }
    if (data.state === "started") {
      if (!hubKeys.has(record.handle)) record.cancellable = Boolean(data.cancellable);
      if (Number(data.started) > 0) record.started = Number(data.started);
      tell(record, "progress");
      return;
    }
    if (own.has(record.handle)) return;
    if (data.state === "done") finish(record, "done");
    else finish(record, stateOf(AW.asError(data.error)), { error: AW.asError(data.error) });
  });
  AW.events.on("job-progress", (data) => {
    const record = records.get(data.handle);
    if (record && record.state === "running") progressed(record, data.progress);
  });
  AW.events.on("bridge-state", (data) => {
    const before = AW.state.bridge || {};
    const next = { log: before.log, ...data };
    if (data.state === "failed") next.error = { message: data.message || "The Workbench service stopped.", detail: data.detail, code: "bridge-failed", action: "log" };
    AW.store.set("bridge", next);
    if (data.state !== "restarting" && data.state !== "failed") return;
    for (const record of running()) finish(record, "interrupted", { error: { message: INTERRUPTED, code: "bridge-stopped" } });
  });

  function watchDownloads() {
    const any = running().some((record) => record.action === "studio-download");
    if (any && !downloadTimer) downloadTimer = setInterval(pollDownloads, DOWNLOAD_POLL_MS);
    else if (!any && downloadTimer) {
      clearInterval(downloadTimer);
      downloadTimer = null;
    }
  }
  async function pollDownloads() {
    let status;
    try {
      status = await AW.request("model-download-status");
    } catch {
      return;
    }
    if (!status || typeof status !== "object") return;
    const live = running().filter((record) => record.action === "studio-download");
    const claimed = new Set(live.map((record) => hubKeys.get(record.handle)).filter(Boolean));
    for (const record of live) {
      let key = hubKeys.get(record.handle);
      if (!key) {
        key = Object.keys(status).find((name) => name.startsWith("hub:") && !claimed.has(name));
        if (!key) continue;
        hubKeys.set(record.handle, key);
        claimed.add(key);
      }
      if (!status[key]) continue;
      record.cancellable = true;
      progressed(record, status[key]);
    }
  }

  AW.jobs = {
    run(action, data = {}, { title, origin, key } = {}) {
      const twin = key ? AW.jobs.find(key) : null;
      if (twin) return own.get(twin.handle) || waits.get(twin.handle).promise;
      const handle = action.slice(0, 30) + "-" + ++counter;
      const record = {
        handle, action, key: key || null, title: title || titleFor(action, null), origin: origin || null,
        started: Date.now(), ended: null, cancellable: false, progress: null, state: "running", error: null, value: null,
      };
      records.set(handle, record);
      const promise = AW.request(action, { ...(data || {}), handle }).then(
        (value) => {
          finish(record, value && value.outcome === "failed" ? "failed" : "done", { value });
          return value;
        },
        (error) => {
          finish(record, stateOf(error), { error });
          throw error;
        },
      );
      own.set(handle, promise);
      hold(record);
      watchDownloads();
      tell(record, "started");
      return promise;
    },
    cancel(handle) {
      const hub = hubKeys.get(handle);
      if (hub) return AW.request("model-download-cancel", { id: hub }).then(Boolean);
      return AW.request("job-cancel", { handle }).then(Boolean);
    },
    list: () => [...records.values()].sort((a, b) => (b.state === "running") - (a.state === "running") || (b.ended || b.started) - (a.ended || a.started)),
    find: (key) => running().find((record) => record.key === key) || null,
    on(fn) {
      jobListeners.add(fn);
      return () => jobListeners.delete(fn);
    },
    clearFinished() {
      for (const [handle, record] of records) if (record.state !== "running") records.delete(handle);
    },
    dismiss(handle) {
      const record = records.get(handle);
      if (record && record.state !== "running") records.delete(handle);
    },
    async sync() {
      const rows = await AW.request("job-list");
      const seen = new Set();
      for (const row of Array.isArray(rows) ? rows : []) {
        if (!row || typeof row.handle !== "string") continue;
        seen.add(row.handle);
        const number = /-(\d+)$/.exec(row.handle);
        if (number) counter = Math.max(counter, Number(number[1]));
        const record = records.get(row.handle);
        if (!record) tell(adopt(row), "started");
        else if (record.state === "running" && row.progress && !record.progress) progressed(record, row.progress);
      }
      for (const record of running())
        if (!seen.has(record.handle) && !own.has(record.handle)) finish(record, "interrupted", { error: { message: INTERRUPTED, code: "bridge-stopped" } });
      return AW.jobs.list();
    },
    open(anchor) {
      openTasks(anchor || defaultAnchor(), true);
    },
  };

  const MARKS = { done: "ok", failed: "down", cancelled: "idle", interrupted: "warn" };
  const tasksOpen = () => Boolean(doc.querySelector(".popover[data-tasks]"));
  function defaultAnchor() {
    for (const id of ["strip-tasks", "strip-service"]) if (!AW.$(id).hidden) return AW.$(id);
    return AW.$("strip-keys");
  }

  function arrange(host, nodes) {
    let cursor = host.firstChild;
    for (const node of nodes) {
      if (node === cursor) cursor = cursor.nextSibling;
      else host.insertBefore(node, cursor);
    }
    while (cursor) {
      const next = cursor.nextSibling;
      cursor.remove();
      cursor = next;
    }
  }

  function finishedRow(record, repaint) {
    const age = el("span", { class: "muted" });
    const result =
      record.state === "interrupted" ? INTERRUPTED
      : record.state === "cancelled" ? (record.error && record.error.message) || ""
      : record.value && typeof record.value.message === "string" ? record.value.message
      : record.state === "done" ? (record.progress && record.progress.phase === "done" && typeof record.progress.message === "string" && record.progress.message) || "Finished."
      : "";
    const head = el(
      "div",
      { class: "task", data: { handle: record.handle } },
      AW.ui.mark(MARKS[record.state] || "idle"),
      el("span", { class: "task-title", text: record.title }),
      age,
      result ? el("div", { class: "task-text", text: result }) : null,
    );
    const node = el("div", null, head);
    if (record.state === "failed" && record.error && !result) {
      const notice = AW.ui.errorNotice(record.error);
      node.append(notice);
      node.addEventListener("click", () =>
        setTimeout(() => {
          if (notice.isConnected) return;
          AW.jobs.dismiss(record.handle);
          repaint();
        }),
      );
    }
    return { el: node, tick: () => (age.textContent = AW.fmt.ago(record.ended || record.started)) };
  }

  function serviceView() {
    const bridge = AW.state.bridge || {};
    const state = bridge.state || "ready";
    if (state === "ready") return { mark: "ok", text: "Workbench service running" };
    if (state === "starting") return { mark: "busy", text: "Workbench service starting…" };
    if (state === "restarting") return { mark: "busy", text: "Workbench service restarting…" };
    return { mark: "down", text: "Workbench service stopped", error: bridge.error || null };
  }

  function openTasks(anchor, keep) {
    if (AW.ui.popoverFor(anchor) && keep) return;
    let offs = [];
    const live = new Map();
    const past = new Map();
    const pop = AW.ui.popover(
      anchor,
      (host) => {
        host.dataset.tasks = "true";
        const body = el("div", { class: "popover-body" });
        const none = el("p", { class: "muted", text: "No tasks." });
        const liveHead = el("h3", { class: "group-label popover-group", text: "Running" });
        const liveHost = el("div");
        const pastHead = el("h3", { class: "group-label popover-group", text: "Finished" });
        const pastHost = el("div", { class: "stack" });
        const mark = AW.ui.mark("ok");
        const word = el("span");
        const serviceHead = el("h3", { class: "group-label popover-group", text: "Service" });
        const service = el("div", { class: "model-state" }, mark, word);
        const trouble = el("div", { class: "stack" });
        const hint = el("p", { class: "model-note muted", text: "Closing Agent Workbench stops downloads in progress and ends its terminals." });
        let troubleKey = "";
        const paint = () => {
          const focused = host.contains(doc.activeElement);
          const bottom = host.isConnected ? host.getBoundingClientRect().bottom : 0;
          const all = AW.jobs.list();
          const now = all.filter((record) => record.state === "running");
          const done = all.filter((record) => record.state !== "running").slice(0, 10);
          for (const [handle, row] of live)
            if (!now.some((record) => record.handle === handle)) {
              row.destroy();
              live.delete(handle);
            }
          for (const record of now) if (!live.has(record.handle)) live.set(record.handle, AW.ui.taskRow(record.handle));
          for (const handle of [...past.keys()]) if (!done.some((record) => record.handle === handle)) past.delete(handle);
          for (const record of done) if (!past.has(record.handle)) past.set(record.handle, finishedRow(record, paint));
          arrange(liveHost, now.map((record) => live.get(record.handle).el));
          arrange(pastHost, done.map((record) => past.get(record.handle).el));
          for (const row of past.values()) row.tick();
          clear.hidden = done.length === 0;
          const view = serviceView();
          AW.ui.setMark(mark, view.mark);
          if (word.textContent !== view.text) word.textContent = view.text;
          const key = view.error ? JSON.stringify(view.error) : "";
          if (key !== troubleKey) {
            troubleKey = key;
            trouble.replaceChildren();
            if (view.error) trouble.append(AW.ui.errorNotice(view.error, { retry: () => (AW.boot ? AW.boot.retry() : AW.store.refresh("live", { force: true })) }));
          }
          arrange(body, [
            ...(all.length ? [] : [none]),
            ...(now.length ? [liveHead, liveHost] : []),
            ...(done.length ? [pastHead, pastHost] : []),
            serviceHead,
            service,
            ...(view.error ? [trouble] : []),
            hint,
          ]);
          if (focused && !host.contains(doc.activeElement) && !AW.focus.first(host)) host.focus();
          const mine = AW.ui.popoverFor(anchor);
          if (mine && anchor.getClientRects().length) mine.place();
          else if (mine && bottom > 0) host.style.top = Math.max(16, bottom - host.offsetHeight) + "px";
        };
        const clear = AW.ui.button("Clear finished", {
          small: true,
          onClick() {
            AW.jobs.clearFinished();
            paint();
          },
        });
        host.append(el("div", { class: "popover-head" }, el("h2", { class: "popover-title", text: "Tasks" }), clear), body);
        offs = [AW.jobs.on(paint), AW.store.on("bridge", paint)];
        paint();
        AW.jobs.sync().then(paint, () => {});
        AW.request("bridge-status").then(
          (status) => AW.store.set("bridge", status),
          () => {},
        );
      },
      { placement: "above", label: "Tasks" },
    );
    pop.onClose = () => {
      offs.forEach((off) => off());
      for (const row of live.values()) row.destroy();
    };
  }

  if (AW.shell)
    AW.shell.register(() => {
      for (const id of ["strip-tasks", "strip-service"]) AW.$(id).addEventListener("click", () => openTasks(AW.$(id), false));
      AW.request("bridge-status").then(
        (status) => AW.store.set("bridge", status),
        () => {},
      );
    });
  if (AW.keys) AW.keys.register({ combo: "mod+.", inTerminal: "mac", run: () => openTasks(defaultAnchor(), false) });
  if (AW.commands)
    AW.commands.provide(() =>
      running()
        .filter((record) => record.cancellable)
        .map((record) => ({
          id: "task.cancel:" + record.handle,
          title: "Cancel: " + record.title,
          group: "Actions",
          keywords: "stop task",
          run: () => AW.jobs.cancel(record.handle),
        })),
    );
})(typeof window === "undefined" ? globalThis : window);
