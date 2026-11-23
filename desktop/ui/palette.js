// Command palette: Mod K and #command-open search and run AW.commands; matching, ranking, Suggested and Recent (ui.md 5.14).
(function () {
  "use strict";
  const AW = (window.AW = window.AW || {});
  const LIMIT = 50;
  const RECENT_KEY = "palette.recent";
  const RECENT_MAX = 5;
  const SUGGESTED_MAX = 5;
  const GROUPS = ["Actions", "Projects", "Connections", "Sessions", "Go to"];
  const ROUTES = ["launch", "overview", "project", "accounts", "machines", "advanced"];
  const TABS = [
    ["instructions", "Instructions", "go to agents.md claude.md instruction file"],
    ["skills", "Skills", "go to skill.md"],
    ["mcp", "MCP tools", "go to plugins servers"],
    ["kit", "Starter kit", "go to template"],
    ["profiles", "Project profiles", "go to recipes"],
  ];
  const phrase = (query) =>
    String(query || "")
      .toLowerCase()
      .trim()
      .replace(/\s+/g, " ");
  const keywordsOf = (command) => (Array.isArray(command.keywords) ? command.keywords.join(" ") : String(command.keywords || ""));
  const groupOf = (command) => (GROUPS.includes(command.group) ? command.group : "Actions");
  const textOf = (command) => String(command.title === undefined || command.title === null ? "" : command.title);
  const wordStart = (hay, at) => at === 0 || !/[a-z0-9]/.test(hay[at - 1]);
  const typedAt = (start, typed, tier) => ({
    start,
    span: typed.length,
    indices: [...typed].map((ch, i) => (ch === " " ? -1 : start + i)).filter((i) => i >= 0),
    tier,
  });

  // Case-insensitive subsequence match with a tier: 0 the query as typed at a word start, 1 as typed inside a word,
  // 2 scattered, where the tightest place it fits is kept (shortest span, then earliest start). Spaces in the query are ignored for 2.
  function match(text, query) {
    const hay = String(text || "").toLowerCase();
    const typed = phrase(query);
    const needle = typed.replace(/ /g, "");
    if (!needle) return { start: 0, span: 0, indices: [], tier: 0 };
    let inside = -1;
    for (let at = hay.indexOf(typed); at >= 0; at = hay.indexOf(typed, at + 1)) {
      if (wordStart(hay, at)) return typedAt(at, typed, 0);
      if (inside < 0) inside = at;
    }
    if (inside >= 0) return typedAt(inside, typed, 1);
    let best = null;
    for (let start = hay.indexOf(needle[0]); start >= 0; start = hay.indexOf(needle[0], start + 1)) {
      const indices = [start];
      let at = start;
      for (let i = 1; i < needle.length && at >= 0; i++) {
        at = hay.indexOf(needle[i], at + 1);
        indices.push(at);
      }
      if (at < 0) break;
      const span = at - start + 1;
      if (!best || span < best.span) best = { start, span, indices, tier: 2 };
    }
    return best;
  }

  function unique(commands) {
    const ids = new Set();
    const titles = new Set();
    const out = [];
    for (const command of commands || []) {
      if (!command || !command.id || typeof command.run !== "function") continue;
      const title = textOf(command);
      const named = groupOf(command) + "\n" + title;
      if (!title || ids.has(command.id) || titles.has(named)) continue;
      ids.add(command.id);
      titles.add(named);
      out.push(command);
    }
    return out;
  }

  const groupRank = (command) => GROUPS.indexOf(groupOf(command));

  // Ranked by match position: the query as typed before a scattered fit, then title before keywords, then the tier of the match,
  // then where it starts and how tight it is. A command counts its keywords when they fit better than a scattered title.
  const scattered = (hit) => hit.tier === 2;
  function search(commands, query, limit = LIMIT) {
    const found = [];
    unique(commands).forEach((command, index) => {
      const named = match(textOf(command), query);
      const keyed = match(keywordsOf(command), query);
      const field = named && !(scattered(named) && keyed && !scattered(keyed)) ? 0 : 1;
      const hit = field ? keyed : named;
      if (!hit) return;
      found.push({ command, field, tier: hit.tier, start: hit.start, span: hit.span, indices: field ? [] : hit.indices, index });
    });
    found.sort(
      (a, b) =>
        scattered(a) - scattered(b) ||
        a.field - b.field ||
        a.tier - b.tier ||
        a.start - b.start ||
        a.span - b.span ||
        groupRank(a.command) - groupRank(b.command) ||
        a.index - b.index,
    );
    return found.slice(0, limit);
  }

  // Groups appear in the order of their best match, so the first option is always the best one. A group outside the five of ui.md 5.14 is shown under Actions.
  function group(results) {
    const groups = new Map();
    for (const result of results) {
      const name = groupOf(result.command);
      if (!groups.has(name)) groups.set(name, []);
      groups.get(name).push(result);
    }
    return [...groups].map(([name, items]) => ({ name, items }));
  }

  // The first five that apply, in the order of ui.md 5.14.
  function suggested(commands, { workspace = "", sessions = [], guideOpen = false } = {}) {
    const list = unique(commands);
    const byId = new Map(list.map((command) => [command.id, command]));
    const out = [];
    const add = (command) => {
      if (command && !out.includes(command) && out.length < SUGGESTED_MAX) out.push(command);
    };
    if (workspace) add(byId.get("launch.quick"));
    const newest = (sessions || [])
      .filter((row) => row && row.kind === "embedded" && row.id)
      .sort((a, b) => (Number(b.started) || 0) - (Number(a.started) || 0))[0];
    if (newest) add(byId.get("session.show:" + newest.id));
    add(list.find((command) => String(command.id).startsWith("task.cancel:")));
    add(byId.get("attach.open") || list.find((command) => /^Attach a running server/.test(textOf(command))));
    add(byId.get("model.start") || byId.get("model.stop"));
    add(list.find((command) => String(command.id).startsWith("project.switch:")));
    if (guideOpen) add(byId.get("guide.open"));
    return out;
  }

  function recentIds() {
    let ids = [];
    try {
      ids = JSON.parse(AW.storage.get(RECENT_KEY, "[]"));
    } catch {
      ids = [];
    }
    return Array.isArray(ids) ? ids.filter((id) => typeof id === "string" && id).slice(0, RECENT_MAX) : [];
  }

  function remember(id) {
    if (!id || String(id).startsWith("task.cancel:")) return recentIds();
    const ids = [id, ...recentIds().filter((other) => other !== id)].slice(0, RECENT_MAX);
    AW.storage.set(RECENT_KEY, JSON.stringify(ids));
    return ids;
  }

  function recent(commands, ids, skip = []) {
    const byId = new Map(unique(commands).map((command) => [command.id, command]));
    return (ids || [])
      .map((id) => byId.get(id))
      .filter((command) => command && !skip.includes(command))
      .slice(0, RECENT_MAX);
  }

  function hintOf(command) {
    if (command.hint) return String(command.hint);
    const route = /^go\.([a-z]+)$/.exec(String(command.id));
    const at = route ? ROUTES.indexOf(route[1]) : -1;
    return at >= 0 ? AW.platform.mod + " " + (at + 1) : "";
  }

  function goCommands() {
    const views = AW.router.views();
    if (!views.some((view) => view.id === "project" && !view.takeover && AW.$(view.nav))) return [];
    return TABS.map(([tab, label, keywords]) => ({
      id: "go.project." + tab,
      title: "Project setup: " + label,
      group: "Go to",
      keywords,
      run: () => AW.router.go("project", { tab }),
    }));
  }

  let dialog = null;
  let input = null;
  let list = null;
  let status = null;
  let options = [];
  let active = -1;
  let token = null;
  let chosen = null;
  let pointer = null;
  const announce = AW.debounce((text) => {
    if (status) status.textContent = text;
  }, 350);

  function readCommands() {
    return AW.commands.all();
  }

  function titleNode(title, indices) {
    const marked = new Set(indices);
    const split = title.indexOf(" · ");
    const node = AW.el("span", { class: "palette-title" });
    const part = (from, to, cls) => {
      const span = AW.el("span", { class: cls });
      let run = "";
      for (let i = from; i < to; i++) {
        if (marked.has(i)) {
          if (run) span.append(run);
          run = "";
          span.append(AW.el("mark", { text: title[i] }));
        } else run += title[i];
      }
      if (run) span.append(run);
      return span;
    };
    if (split < 0) node.append(part(0, title.length, "palette-main"));
    else node.append(part(0, split, "palette-main"), part(split, title.length, "palette-detail"));
    return node;
  }

  function keysNode(hint) {
    if (!hint) return null;
    return AW.el(
      "span",
      { class: "palette-keys" },
      hint
        .split(/\s+/)
        .filter(Boolean)
        .map((key) => AW.el("kbd", { text: key })),
    );
  }

  function optionNode(command, indices) {
    const title = textOf(command);
    const id = "palette-option-" + options.length;
    const node = AW.el(
      "div",
      { class: "palette-option", role: "option", id, title, attrs: { "aria-selected": "false" }, data: { command: command.id } },
      titleNode(title, indices),
      keysNode(hintOf(command)),
    );
    node.addEventListener("mousedown", (event) => event.preventDefault());
    node.addEventListener("click", () => choose(command));
    node.addEventListener("mousemove", (event) => {
      const moved = Boolean(pointer) && (pointer.x !== event.screenX || pointer.y !== event.screenY);
      pointer = { x: event.screenX, y: event.screenY };
      if (!moved) return;
      const at = options.findIndex((entry) => entry.node === node);
      if (at !== active) setActive(at, false);
    });
    options.push({ node, command });
    return node;
  }

  function groupNode(name, entries, at) {
    const labelId = "palette-group-" + at;
    const node = AW.el(
      "div",
      { class: "palette-group", role: "group", attrs: { "aria-labelledby": labelId } },
      AW.el("div", { class: "group-label palette-group-label", id: labelId, role: "presentation", text: name }),
    );
    for (const entry of entries) node.append(optionNode(entry.command, entry.indices || []));
    return node;
  }

  function setActive(index, scroll = true) {
    active = options.length ? (index + options.length) % options.length : -1;
    options.forEach((entry, at) => entry.node.setAttribute("aria-selected", String(at === active)));
    if (active >= 0) {
      input.setAttribute("aria-activedescendant", options[active].node.id);
      if (scroll) options[active].node.scrollIntoView({ block: "nearest" });
    } else input.removeAttribute("aria-activedescendant");
  }

  const count = (n, one, many) => (n === 1 ? "1 " + one : n + " " + many);

  function render() {
    const query = input.value.trim();
    options = [];
    let commands;
    try {
      commands = readCommands();
    } catch (thrown) {
      list.replaceChildren(AW.el("p", { class: "palette-empty", text: "The command list could not be read: " + AW.asError(thrown).message }));
      setActive(-1);
      announce("The command list could not be read.");
      return;
    }
    const groups = [];
    if (query) {
      for (const entry of group(search(commands, query))) groups.push(entry);
    } else {
      const state = AW.state || {};
      const progress = state.progress;
      const top = suggested(commands, {
        workspace: AW.form.get().workspace,
        sessions: state.sessions,
        guideOpen: Boolean(progress && progress.open),
      });
      const last = recent(commands, recentIds(), top);
      if (top.length) groups.push({ name: "Suggested", items: top.map((command) => ({ command })) });
      if (last.length) groups.push({ name: "Recent", items: last.map((command) => ({ command })) });
    }
    list.replaceChildren(...groups.map((entry, at) => groupNode(entry.name, entry.items, at)));
    input.setAttribute("aria-expanded", String(options.length > 0));
    if (!options.length) {
      const text = query ? "No command matches “" + query + "”." : "Type to find a command, a project folder or a view.";
      list.append(AW.el("p", { class: "palette-empty", text }));
      announce(query ? text : "");
    } else announce(query ? count(options.length, "command", "commands") : count(options.length, "suggestion", "suggestions"));
    list.scrollTop = 0;
    setActive(0);
  }

  function onKey(event) {
    if (event.isComposing) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (options.length) setActive(active + (event.key === "ArrowDown" ? 1 : -1));
    } else if (event.key === "PageDown" || event.key === "PageUp") {
      event.preventDefault();
      if (options.length) setActive(Math.max(0, Math.min(options.length - 1, active + (event.key === "PageDown" ? 8 : -8))));
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (active >= 0) choose(options[active].command);
    }
  }

  function build() {
    if (dialog) return;
    dialog = AW.$("palette");
    const body = AW.$("palette-body");
    input = AW.el("input", {
      id: "palette-input",
      type: "text",
      role: "combobox",
      attrs: {
        "aria-label": "Search or run a command",
        "aria-controls": "palette-list",
        "aria-expanded": "true",
        "aria-autocomplete": "list",
        placeholder: "Search or run a command",
        spellcheck: "false",
        autocomplete: "off",
      },
    });
    list = AW.el("div", { id: "palette-list", class: "palette-list", role: "listbox", attrs: { "aria-label": "Commands" } });
    status = AW.el("p", { class: "sr-only", role: "status" });
    const field = AW.el("div", { class: "palette-field" }, AW.ui.icon("search"), input);
    const foot = AW.el("p", { class: "palette-foot", text: "Up/Down choose · Enter run · Esc close" });
    body.replaceChildren(AW.el("div", { class: "palette-head" }, field), list, foot, status);
    input.addEventListener("input", render);
    input.addEventListener("keydown", onKey);
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) close();
    });
    AW.ui.dialog(dialog, { onClose: closed });
  }

  function inTakeover() {
    const route = AW.router.current();
    const view = AW.router.views().find((item) => item.id === route.id);
    return Boolean(view && view.takeover);
  }

  function open() {
    build();
    if (dialog.open) return true;
    if (inTakeover() || AW.ui.topModal()) return false;
    token = AW.focus.remember();
    chosen = null;
    pointer = null;
    input.value = "";
    status.textContent = "";
    render();
    AW.ui.dialog(dialog).open();
    AW.$("command-open").setAttribute("aria-expanded", "true");
    input.focus();
    return true;
  }

  function close() {
    if (dialog && dialog.open) AW.ui.dialog(dialog).close(null);
  }

  function toggle() {
    if (dialog && dialog.open) {
      close();
      return true;
    }
    return open();
  }

  function choose(command) {
    chosen = command;
    close();
  }

  function closed() {
    AW.$("command-open").setAttribute("aria-expanded", "false");
    announce.cancel();
    const command = chosen;
    chosen = null;
    AW.focus.restore(token);
    token = null;
    if (!command) return;
    if (!readCommands().some((item) => item.id === command.id)) {
      AW.ui.toast("That command is no longer available.", { kind: "danger" });
      return;
    }
    remember(command.id);
    AW.commands.run(command.id);
  }

  AW.palette = { match, search, group, suggested, recent, recentIds, remember, hintOf, open, close, toggle, opened: () => Boolean(dialog && dialog.open) };

  AW.commands.provide(goCommands);
  AW.keys.register({ combo: "mod+k", inTerminal: "mac", inDialog: true, run: toggle });
  if (!AW.platform.mac) AW.keys.register({ combo: "mod+shift+k", inTerminal: true, inDialog: true, run: toggle });

  AW.shell.register(() => {
    const opener = AW.$("command-open");
    opener.setAttribute("aria-haspopup", "dialog");
    opener.setAttribute("aria-controls", "palette");
    opener.setAttribute("aria-expanded", "false");
    opener.setAttribute("aria-keyshortcuts", AW.platform.mac ? "Meta+K" : "Control+K");
    opener.title = "Search or run a command (" + AW.platform.mod + " K)";
    opener.addEventListener("click", open);
    opener.hidden = false;
  });
})();
