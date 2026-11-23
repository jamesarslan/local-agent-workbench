// Terminal dock: one xterm per session with background readers, tabs and the session select, resizing, and the dock's buttons (ui.md 2.5, 5.11, 9.7).
// Beyond ui.md 9.7: AW.terminal.start accepts opts.origin (the control that shows the task) and AW.terminal.end(id) ends a session after asking.
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const ACTIVE_MS = 24;
  const VISIBLE_MS = 120;
  const IDLE_POLLS = 5;
  const BACKGROUND_MS = 1000;
  const SIZE_MS = 100;
  const STEP = 16;
  const MIN_HEIGHT = 160;
  const ROOM = 200;
  const COMPACT_HEIGHT = 200;
  const EMPTY = "Open a shell, start the selected agent, or resume a session in this project.";
  const NO_FOLDER = "Choose a project folder to open a terminal in it.";
  const RESTARTED = "This terminal was closed because the Workbench service restarted.";
  const WATCHING = "Read-only · follows saved OMP messages and tool calls.";
  const EXITED = "Session exited.";
  const LOADING = "Loading the terminal…";
  const KEYS_LINE = AW.platform.mac
    ? "Cmd C copies a selection · Cmd V pastes · F6 leaves the terminal"
    : "Ctrl C copies a selection · Ctrl Shift V pastes · F6 leaves the terminal";
  const ORIGIN = { shell: "term-shell", agent: "term-agent", resume: "term-resume", watch: "term-watch" };
  const TRANSIENT = new Set(["bridge-stopped", "timeout", "busy"]);
  const wide = window.matchMedia("(min-width: 900px)");

  const sessions = new Map();
  const chosen = new Map();
  const tabs = new Map();
  const starting = new Set();
  const listing = AW.ticket();
  let order = [];
  let folder = "";
  let currentId = "";
  let xterm = null;
  let loaded = false;
  let loading = false;
  let note = null;
  let opening = "";
  let height = Number(AW.storage.get("dock.height", "")) || 0;

  const dock = () => AW.$("terminal-dock");
  const dockShown = () => !dock().hidden;
  const current = () => sessions.get(currentId) || null;
  const live = (rec) => !rec.closed && !rec.ended;

  function script(src) {
    return new Promise((resolve, reject) => {
      const node = el("script", { attrs: { src } });
      node.addEventListener("load", resolve, { once: true });
      node.addEventListener("error", () => reject({ message: "The terminal could not be loaded. Restart Agent Workbench, then try again." }), { once: true });
      document.head.append(node);
    });
  }

  function loadXterm() {
    if (!xterm)
      xterm = (window.Terminal ? Promise.resolve() : script("vendor/xterm/xterm.js"))
        .then(() => (window.FitAddon ? null : script("vendor/xterm/addon-fit.js")))
        .then(() => {
          loaded = true;
        })
        .catch((thrown) => {
          xterm = null;
          throw AW.asError(thrown);
        });
    return xterm;
  }

  function look() {
    const css = getComputedStyle(document.documentElement);
    const value = (name) => css.getPropertyValue(name).trim();
    return {
      fontFamily: value("--font-mono") || "monospace",
      fontSize: parseInt(value("--text-md"), 10) || 13,
      theme: {
        background: value("--term-bg"),
        foreground: value("--term-text"),
        cursor: value("--term-text"),
        cursorAccent: value("--term-bg"),
        selectionBackground: value("--term-edge"),
        selectionForeground: value("--term-text"),
      },
    };
  }

  function make(row, workspace) {
    const rec = {
      id: row.id, workspace, title: "", kind: "", harness: "", run: "", watch: false, ended: false, closed: false, done: false, stopped: false,
      detached: false, offset: 0, idle: 0, timer: null, reading: false, queue: Promise.resolve(), sized: "", opened: false,
      box: el("div", { class: "term-box", hidden: true }), term: null, fit: null,
    };
    rec.term = new window.Terminal({ ...look(), scrollback: 1500, disableStdin: Boolean(row.watch), allowProposedApi: false, cursorBlink: false });
    rec.fit = new window.FitAddon.FitAddon();
    rec.term.loadAddon(rec.fit);
    rec.term.onData((text) => send(rec, text));
    rec.term.attachCustomKeyEventHandler((event) => keyInTerminal(rec, event));
    sessions.set(rec.id, rec);
    return rec;
  }

  function update(rec, row) {
    rec.title = typeof row.title === "string" && row.title.trim() ? row.title.trim() : "Terminal";
    rec.kind = typeof row.kind === "string" ? row.kind : "";
    rec.harness = typeof row.harness === "string" ? row.harness : "";
    if (typeof row.run === "string") rec.run = row.run;
    rec.watch = Boolean(row.watch);
    if (row.ended) rec.ended = true;
    rec.detached = false;
    rec.term.options.disableStdin = rec.watch || rec.closed;
  }

  function dispose(rec) {
    clearTimeout(rec.timer);
    rec.stopped = true;
    sessions.delete(rec.id);
    const tab = tabs.get(rec.id);
    if (tab) tab.wrap.remove();
    tabs.delete(rec.id);
    for (const [workspace, id] of chosen) if (id === rec.id) chosen.delete(workspace);
    order = order.filter((id) => id !== rec.id);
    rec.box.remove();
    try {
      rec.term.dispose();
    } catch (thrown) {
      console.error(thrown);
    }
    if (currentId === rec.id) currentId = "";
  }

  function pace(rec) {
    if (rec.id !== currentId || !dockShown() || document.hidden) return BACKGROUND_MS;
    return rec.idle < IDLE_POLLS ? ACTIVE_MS : VISIBLE_MS;
  }

  function schedule(rec, ms) {
    clearTimeout(rec.timer);
    rec.timer = null;
    if (rec.done || rec.closed || rec.stopped || !sessions.has(rec.id)) return;
    rec.timer = setTimeout(() => read(rec), ms === undefined ? pace(rec) : ms);
  }

  async function read(rec) {
    rec.timer = null;
    if (rec.reading) return;
    rec.reading = true;
    let reply;
    try {
      reply = await AW.request("term-read", { workspace: rec.workspace, id: rec.id, offset: rec.offset });
    } catch (thrown) {
      rec.reading = false;
      lost(rec, AW.asError(thrown));
      return;
    }
    rec.reading = false;
    if (!sessions.has(rec.id) || rec.closed || !reply || typeof reply !== "object") return;
    if (reply.reset) rec.term.reset();
    const got = typeof reply.text === "string" && reply.text.length > 0;
    if (got) {
      rec.idle = 0;
      rec.term.write(reply.text);
    } else if (rec.idle < IDLE_POLLS) rec.idle += 1;
    if (Number.isFinite(Number(reply.offset))) rec.offset = Number(reply.offset);
    if (reply.ended) {
      rec.done = true;
      if (!rec.ended) {
        rec.ended = true;
        rec.term.options.disableStdin = true;
        paint();
      }
      return;
    }
    schedule(rec);
  }

  function lost(rec, error) {
    if (!sessions.has(rec.id) || rec.closed) return;
    if (TRANSIENT.has(error.code)) {
      schedule(rec, BACKGROUND_MS);
      return;
    }
    rec.stopped = true;
    if (rec.workspace !== folder) {
      dispose(rec);
      return;
    }
    if (rec.id === currentId) setNote(error);
    refresh();
  }

  function send(rec, text) {
    if (rec.watch || rec.ended || rec.closed || rec.detached) return;
    rec.idle = 0;
    rec.queue = rec.queue
      .then(() => AW.request("term-write", { workspace: rec.workspace, id: rec.id, text }))
      .then(() => {
        if (rec.id === currentId && rec.timer) schedule(rec, 16);
      })
      .catch((thrown) => {
        if (rec.id === currentId) setNote(AW.asError(thrown));
      });
  }

  function keyInTerminal(rec, event) {
    if (event.type !== "keydown") return true;
    const key = (event.key || "").toLowerCase();
    const mod = AW.platform.mac ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey;
    if (mod && !event.altKey && !event.shiftKey && key === "c" && rec.term.hasSelection()) {
      const text = rec.term.getSelection();
      rec.term.clearSelection();
      AW.clipboard.write(text).then((copied) => {
        if (!copied) setNote({ message: "The selection could not be copied to the clipboard." });
      });
      event.preventDefault();
      return false;
    }
    const paste = AW.platform.mac ? mod && !event.shiftKey && !event.altKey && key === "v" : mod && event.shiftKey && !event.altKey && key === "v";
    if (paste) {
      event.preventDefault();
      if (!rec.watch && !rec.ended && !rec.closed)
        navigator.clipboard
          .readText()
          .then((text) => {
            if (text && sessions.has(rec.id)) rec.term.paste(text);
          })
          .catch(() => setNote({ message: "The clipboard could not be read. Paste again, or type the text." }));
      return false;
    }
    return true;
  }

  let fitFrame = 0;
  const sizeLater = AW.debounce(() => size(current()), SIZE_MS);
  function fitNow() {
    cancelAnimationFrame(fitFrame);
    fitFrame = requestAnimationFrame(() => {
      const rec = current();
      if (!rec || !rec.opened || !dockShown()) return;
      try {
        rec.fit.fit();
      } catch (thrown) {
        console.error(thrown);
      }
      sizeLater();
    });
  }

  function size(rec) {
    if (!rec || rec.watch || !live(rec) || !rec.opened || !dockShown()) return;
    const key = rec.term.rows + "x" + rec.term.cols;
    if (key === rec.sized) return;
    rec.sized = key;
    AW.request("term-size", { workspace: rec.workspace, id: rec.id, rows: rec.term.rows, cols: rec.term.cols }).catch(() => {
      rec.sized = "";
    });
  }

  function display(rec) {
    const screen = AW.$("term-screen");
    const already = Boolean(rec && rec.opened && !rec.box.hidden && rec.box.parentNode === screen);
    for (const other of sessions.values()) if (other !== rec) other.box.hidden = true;
    if (!rec) return;
    if (rec.box.parentNode !== screen) screen.append(rec.box);
    rec.box.hidden = false;
    if (!dockShown()) return;
    if (!rec.opened) {
      rec.term.open(rec.box);
      rec.opened = true;
      fitNow();
    } else if (!already) {
      rec.term.refresh(0, rec.term.rows - 1);
      fitNow();
    }
  }

  function select(id, { focus = false } = {}) {
    const rec = sessions.get(id) && order.includes(id) ? sessions.get(id) : null;
    const before = currentId;
    currentId = rec ? rec.id : "";
    if (rec) chosen.set(folder, rec.id);
    if (before !== currentId) note = null;
    display(rec);
    paint();
    if (rec) {
      const parts = tabs.get(rec.id);
      if (parts && dockShown() && wide.matches) parts.wrap.scrollIntoView({ block: "nearest", inline: "nearest" });
      if (rec.timer) schedule(rec, 0);
      if (focus && dockShown()) requestAnimationFrame(() => rec.term.focus());
    }
  }

  function setNote(error) {
    note = error ? AW.asError(error) : null;
    info();
  }

  function info() {
    const node = AW.$("term-info");
    const rec = current();
    let text;
    if (note) text = note.message;
    else if (opening) text = opening;
    else if (loading) text = LOADING;
    else if (!folder) text = NO_FOLDER;
    else if (!rec) text = EMPTY;
    else if (rec.closed) text = RESTARTED;
    else if (rec.ended) text = EXITED;
    else if (rec.detached) text = "This terminal is not taking input until the session list can be read again.";
    else if (rec.watch) text = WATCHING;
    else text = KEYS_LINE;
    const failed = Boolean(note) && note.code !== "cancelled" && note.code !== "busy";
    if (node.dataset.text === text && node.classList.contains("term-error") === failed) return;
    node.dataset.text = text;
    node.classList.toggle("term-error", failed);
    node.replaceChildren(...(failed ? [AW.ui.mark("down")] : []), el("span", { text }));
  }

  function state(rec) {
    return rec.closed ? " · closed" : rec.ended ? " · exited" : rec.watch ? " · read-only" : "";
  }

  function label(rec) {
    return rec.title + state(rec);
  }

  function tabFor(rec) {
    let parts = tabs.get(rec.id);
    if (!parts) {
      const tab = el("button", {
        class: "term-tab",
        type: "button",
        role: "tab",
        id: "term-tab-" + rec.id,
        attrs: { "aria-controls": "term-screen" },
        on: {
          click(event) {
            select(rec.id, { focus: event.detail > 0 });
          },
          keydown: tabKeys,
        },
      });
      const name = el("span", { class: "term-tab-name" });
      const status = el("span", { class: "term-tab-state" });
      tab.append(name, status);
      const close = el("button", { class: "term-tab-close", type: "button", on: { click: () => end(rec.id) } }, AW.ui.icon("close"));
      const wrap = el("div", { class: "term-tab-wrap", role: "presentation", data: { id: rec.id } }, tab, close);
      parts = { wrap, tab, name, status, close };
      tabs.set(rec.id, parts);
    }
    const text = label(rec);
    const selected = rec.id === currentId;
    if (parts.name.textContent !== rec.title) parts.name.textContent = rec.title;
    if (parts.status.textContent !== state(rec)) parts.status.textContent = state(rec);
    parts.tab.title = text;
    parts.tab.setAttribute("aria-selected", String(selected));
    parts.tab.tabIndex = selected ? 0 : -1;
    parts.close.setAttribute("aria-label", "End " + rec.title);
    parts.close.title = "End " + rec.title;
    parts.close.tabIndex = selected ? 0 : -1;
    parts.wrap.classList.toggle("ended", rec.ended || rec.closed);
    return parts;
  }

  function tabKeys(event) {
    const ids = order.filter((id) => sessions.has(id));
    const at = ids.indexOf(currentId);
    let next = null;
    if (event.key === "ArrowRight") next = ids[(at + 1) % ids.length];
    else if (event.key === "ArrowLeft") next = ids[(at - 1 + ids.length) % ids.length];
    else if (event.key === "Home") next = ids[0];
    else if (event.key === "End") next = ids[ids.length - 1];
    else if (event.key === "Delete") {
      event.preventDefault();
      end(currentId);
      return;
    }
    if (!next) return;
    event.preventDefault();
    select(next);
    const parts = tabs.get(next);
    if (parts) parts.tab.focus();
  }

  function paintTabs(rows) {
    const strip = AW.$("term-tabs");
    const wanted = rows.map((rec) => tabFor(rec).wrap);
    const present = [...strip.children];
    for (const node of present) if (!wanted.includes(node)) node.remove();
    const same = wanted.length === strip.children.length && wanted.every((node, index) => strip.children[index] === node);
    if (!same) strip.replaceChildren(...wanted);
  }

  function paintSelect(rows) {
    const list = AW.$("terminal-list");
    const options = [...list.options];
    const same = options.length === rows.length && rows.every((rec, index) => options[index].value === rec.id);
    if (!same) list.replaceChildren(...rows.map((rec) => el("option", { value: rec.id, text: label(rec) })));
    else rows.forEach((rec, index) => {
      if (options[index].textContent !== label(rec)) options[index].textContent = label(rec);
    });
    list.value = currentId;
    if (!currentId) list.selectedIndex = -1;
    list.disabled = rows.length === 0;
  }

  function paintWidth() {
    AW.$("term-tabs").hidden = !wide.matches;
    AW.$("terminal-list").hidden = wide.matches || !order.some((id) => sessions.has(id));
  }

  function paint() {
    const rows = order.map((id) => sessions.get(id)).filter(Boolean);
    paintWidth();
    paintTabs(rows);
    paintSelect(rows);
    const screen = AW.$("term-screen");
    const rec = current();
    screen.setAttribute("role", "tabpanel");
    if (rec) {
      screen.setAttribute("aria-labelledby", "term-tab-" + rec.id);
      screen.removeAttribute("aria-label");
    } else {
      screen.removeAttribute("aria-labelledby");
      screen.setAttribute("aria-label", "Terminal");
    }
    const close = AW.$("term-close");
    if (rec) close.removeAttribute("aria-disabled");
    else close.setAttribute("aria-disabled", "true");
    close.textContent = rec && rec.kind === "remote" && live(rec) ? "Detach" : "End";
    AW.$("term-run").hidden = !(rec && rec.run);
    AW.$("term-quit-hint").hidden = ![...sessions.values()].some((other) => live(other) && !other.watch);
    dock().classList.toggle("term-empty", !rec);
    info();
  }

  async function refresh(want) {
    const workspace = folder;
    const mine = listing.next();
    if (!workspace || !loaded) {
      order = [];
      select("");
      return;
    }
    loading = !order.length && !sessions.size;
    info();
    let rows;
    try {
      rows = await AW.request("term-list", { workspace });
    } catch (thrown) {
      if (!listing.current(mine) || workspace !== folder) return;
      loading = false;
      const rec = current();
      if (rec && live(rec)) {
        rec.detached = true;
        rec.term.options.disableStdin = true;
      }
      setNote(thrown);
      return;
    }
    if (!listing.current(mine) || workspace !== folder) return;
    loading = false;
    const seen = new Set();
    for (const row of Array.isArray(rows) ? rows : []) {
      if (!row || typeof row.id !== "string" || !row.id) continue;
      seen.add(row.id);
      const known = sessions.get(row.id);
      const rec = known || make(row, workspace);
      rec.workspace = workspace;
      update(rec, row);
      if (!known || (rec.stopped && !rec.closed)) {
        rec.stopped = false;
        schedule(rec, 0);
      }
    }
    for (const rec of [...sessions.values()]) if (rec.workspace === workspace && !seen.has(rec.id) && !rec.closed) dispose(rec);
    const closed = [...sessions.values()].filter((rec) => rec.workspace === workspace && rec.closed && !seen.has(rec.id)).map((rec) => rec.id);
    order = [...closed, ...[...seen]];
    const pick = [want, currentId, chosen.get(workspace)].find((id) => id && order.includes(id)) || order[order.length - 1] || "";
    select(pick);
  }

  async function ensure() {
    if (loaded) return;
    loading = true;
    info();
    try {
      await loadXterm();
    } finally {
      loading = false;
    }
  }

  function setDock(open) {
    const node = dock();
    if (node.hidden === !open) return;
    node.hidden = !open;
    AW.$("terminal-toggle").setAttribute("aria-expanded", String(open));
    document.body.classList.toggle("dock-open", open);
    if (open) {
      applyHeight(height || defaultHeight(), false);
      paint();
      display(current());
    }
    for (const rec of sessions.values()) if (rec.timer) schedule(rec);
  }

  function onFolder() {
    const workspace = AW.form.get().workspace || "";
    if (workspace === folder) return;
    folder = workspace;
    note = null;
    order = [...sessions.values()].filter((rec) => rec.workspace === workspace).map((rec) => rec.id);
    select(chosen.get(workspace) || order[order.length - 1] || "");
    if (loaded) refresh();
  }

  function titleFor(kind, form, remote) {
    const agent = form.harness || "the agent";
    if (kind === "shell") return "Opening a shell";
    if (kind === "watch") return "Opening a saved session read-only";
    if (kind === "remote") return "Opening a session on " + ((remote && (remote.label || remote.host)) || "the machine");
    return "Opening " + agent + " in the terminal dock";
  }

  async function start(kind, opts = {}) {
    if (!["shell", "agent", "resume", "watch", "remote"].includes(kind)) throw { message: "Choose what to open in the terminal." };
    if (starting.has(kind)) return null;
    const form = { ...(opts.form || AW.form.get()) };
    const remote = opts.remote || null;
    const origin = opts.origin || AW.$(ORIGIN[kind] || "term-shell");
    const title = titleFor(kind, form, remote);
    starting.add(kind);
    setDock(true);
    note = null;
    opening = title + "…";
    info();
    try {
      await ensure();
      let id;
      if (kind === "remote") id = await AW.jobs.run("term-remote", { workspace: form.workspace, ...remote }, { title, origin });
      else id = await AW.jobs.run("term-open", { ...form, kind, saved: opts.saved || "" }, { title, origin });
      opening = "";
      if (typeof id !== "string" || !id) throw { message: "The terminal did not open. Try again." };
      if (form.workspace === folder) await refresh(id);
      else paint();
      if (currentId === id && dockShown()) requestAnimationFrame(() => current() && current().term.focus());
      AW.store.refresh("sessions").catch(() => {});
      return id;
    } catch (thrown) {
      opening = "";
      const error = AW.asError(thrown);
      setNote(error);
      throw error;
    } finally {
      opening = "";
      starting.delete(kind);
      info();
    }
  }

  function machineOf(rec) {
    const row = (AW.state.sessions || []).find((item) => item.terminal === rec.id);
    return (rec.title.split(" · ")[0] || (row && row.host) || "the other machine").trim();
  }

  async function end(id) {
    const rec = sessions.get(id || currentId);
    if (!rec) return false;
    note = null;
    if (rec.closed) {
      dispose(rec);
      select(chosen.get(folder) || order[order.length - 1] || "");
      return true;
    }
    if (live(rec) && !rec.watch) {
      const remote = rec.kind === "remote";
      const ok = await AW.ui.confirm({
        text: remote ? "Detach from this remote session? It keeps running on " + machineOf(rec) + "." : "End this terminal session?",
        ok: remote ? "Detach" : "End",
        danger: !remote,
      });
      if (!ok) return false;
    }
    try {
      await AW.request("term-close", { workspace: rec.workspace, id: rec.id });
    } catch (thrown) {
      setNote(thrown);
      return false;
    }
    const inTabs = AW.$("term-tabs").contains(document.activeElement) || document.activeElement === document.body;
    dispose(rec);
    await refresh();
    if (inTabs && dockShown()) {
      const parts = tabs.get(currentId);
      if (parts && wide.matches) parts.tab.focus();
      else AW.$(wide.matches || !order.length ? "term-shell" : "terminal-list").focus();
    }
    AW.store.refresh("sessions").catch(() => {});
    return true;
  }

  function discover(rows) {
    if (!loaded || !Array.isArray(rows)) return;
    let here = false;
    for (const row of rows) {
      if (!row || (row.kind !== "embedded" && row.kind !== "remote") || typeof row.terminal !== "string" || !row.terminal) continue;
      if (sessions.has(row.terminal) || typeof row.workspace !== "string" || !row.workspace) continue;
      if (row.workspace === folder) {
        here = true;
        continue;
      }
      const rec = make({ id: row.terminal }, row.workspace);
      update(rec, { title: row.title, kind: row.kind, harness: row.harness, watch: false, ended: false });
      schedule(rec, 0);
    }
    if (here) refresh();
  }

  function restarted(data) {
    if (data.state === "restarting" || data.state === "failed") {
      note = null;
      opening = "";
      for (const rec of sessions.values()) {
        if (rec.closed) continue;
        rec.closed = true;
        clearTimeout(rec.timer);
        rec.timer = null;
        rec.term.options.disableStdin = true;
      }
      paint();
    } else if (data.state === "ready" && loaded) refresh();
  }

  function bounds() {
    return { min: MIN_HEIGHT, max: Math.max(MIN_HEIGHT, window.innerHeight - ROOM) };
  }
  function defaultHeight() {
    return window.innerHeight <= 700 ? COMPACT_HEIGHT : Math.round(window.innerHeight * 0.4);
  }
  function applyHeight(px, save) {
    const { min, max } = bounds();
    const value = Math.round(Math.min(max, Math.max(min, Number(px) || defaultHeight())));
    dock().style.setProperty("--dock-h", value + "px");
    const handle = AW.$("dock-resize");
    handle.setAttribute("aria-valuemin", String(min));
    handle.setAttribute("aria-valuemax", String(max));
    handle.setAttribute("aria-valuenow", String(value));
    handle.setAttribute("aria-valuetext", value + " pixels high");
    if (save) {
      height = value;
      AW.storage.set("dock.height", value);
    }
    fitNow();
    return value;
  }
  const shown = () => Math.round(dock().getBoundingClientRect().height);
  function nudge(delta) {
    applyHeight(shown() + delta, true);
  }

  function wireResize() {
    const handle = AW.$("dock-resize");
    let drag = null;
    handle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      drag = { y: event.clientY, from: shown(), pointer: event.pointerId };
      handle.setPointerCapture(event.pointerId);
      handle.classList.add("dragging");
    });
    handle.addEventListener("pointermove", (event) => {
      if (drag && event.pointerId === drag.pointer) applyHeight(drag.from + drag.y - event.clientY, false);
    });
    const stop = (event) => {
      if (!drag || event.pointerId !== drag.pointer) return;
      drag = null;
      handle.classList.remove("dragging");
      applyHeight(shown(), true);
    };
    handle.addEventListener("pointerup", stop);
    handle.addEventListener("pointercancel", stop);
    handle.addEventListener("keydown", (event) => {
      const { min, max } = bounds();
      const moves = { ArrowUp: () => nudge(STEP), ArrowDown: () => nudge(-STEP), Home: () => applyHeight(min, true), End: () => applyHeight(max, true) };
      if (!moves[event.key] || event.altKey || event.ctrlKey || event.metaKey) return;
      event.preventDefault();
      moves[event.key]();
    });
    document.querySelector("#terminal-dock .term-head").addEventListener("keydown", (event) => {
      if (!event.altKey || event.ctrlKey || event.metaKey || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return;
      event.preventDefault();
      nudge(event.key === "ArrowUp" ? STEP : -STEP);
    });
    window.addEventListener(
      "resize",
      AW.debounce(() => {
        if (dockShown()) applyHeight(height || defaultHeight(), false);
      }, 80),
    );
  }

  function paintAgent() {
    const result = AW.readiness.current();
    const button = AW.$("term-agent");
    if (!button.dataset.job) {
      if (result.ok) button.removeAttribute("aria-disabled");
      else button.setAttribute("aria-disabled", "true");
    }
    AW.$("launch-needs-dock").textContent = result.ok ? "" : result.needs;
  }

  function startAgent(button) {
    if (starting.has("agent") || button.dataset.job) return;
    const result = AW.readiness.current();
    if (!result.ok) return AW.launch.quick(button);
    return AW.launch.start({ where: "dock", origin: button });
  }

  function onClick(id, fn) {
    AW.$(id).addEventListener("click", (event) => {
      const button = event.currentTarget;
      if (button.dataset.job) return;
      Promise.resolve()
        .then(() => fn(button))
        .catch((thrown) => setNote(thrown));
    });
  }

  AW.terminal = {
    show(id) {
      setDock(true);
      return ensure()
        .then(() => refresh(id))
        .then(() => {
          if (id && currentId === id) current().term.focus();
          return currentId;
        })
        .catch((thrown) => {
          setNote(thrown);
          return "";
        });
    },
    hide() {
      const inside = dock().contains(document.activeElement);
      setDock(false);
      if (inside) AW.$("terminal-toggle").focus();
    },
    toggle() {
      if (dockShown()) AW.terminal.hide();
      else AW.terminal.show();
    },
    start,
    end,
    current() {
      const rec = current();
      return { id: rec ? rec.id : "", workspace: rec ? rec.workspace : folder, xterm: rec ? rec.term : null };
    },
  };

  AW.shell.register(() => {
    folder = AW.form.get().workspace || "";
    paintWidth();
    info();
    paintAgent();
    onClick("term-hide", () => AW.terminal.hide());
    onClick("term-shell", () => start("shell"));
    onClick("term-agent", startAgent);
    onClick("term-resume", () => AW.sessions.picker("resume"));
    onClick("term-watch", () => AW.sessions.picker("watch"));
    onClick("term-run", (button) => {
      const rec = current();
      return rec && rec.run ? AW.runs.open({ workspace: rec.workspace, id: rec.run, origin: button }) : null;
    });
    onClick("term-close", (button) => (button.getAttribute("aria-disabled") === "true" ? false : end(currentId)));
    AW.$("terminal-list").addEventListener("change", (event) => select(event.currentTarget.value));
    wireResize();
    wide.addEventListener("change", paint);
    new ResizeObserver(() => fitNow()).observe(AW.$("term-screen"));
    document.addEventListener("visibilitychange", () => {
      const rec = current();
      if (rec && rec.timer) schedule(rec);
    });
    AW.form.on((_form, changed) => {
      if (!Array.isArray(changed) || changed.includes("workspace")) onFolder();
    });
    AW.readiness.on(paintAgent);
    AW.form.on(paintAgent);
    AW.store.on("sessions", discover);
    AW.events.on("bridge-state", restarted);
  });

  AW.keys.register({ combo: "mod+j", inTerminal: "mac", run: () => AW.terminal.toggle() });
  if (!AW.platform.mac) AW.keys.register({ combo: "mod+shift+j", inTerminal: true, run: () => AW.terminal.hide() });
  AW.commands.register({ id: "terminal.show", title: "Show terminal", group: "Actions", hint: AW.platform.mod + " J", when: () => !dockShown(), run: () => AW.terminal.show() });
  AW.commands.register({ id: "terminal.hide", title: "Hide terminal", group: "Actions", hint: AW.platform.mod + " J", when: () => dockShown(), run: () => AW.terminal.hide() });
  AW.commands.register({
    id: "terminal.shell",
    title: () => "New shell in " + (AW.project.name(AW.form.get().workspace) || "this folder"),
    group: "Actions",
    keywords: "terminal console",
    when: () => Boolean(AW.form.get().workspace),
    run: () => start("shell"),
  });
})();
