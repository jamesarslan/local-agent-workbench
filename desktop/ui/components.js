// Builders for the shared components of ui.md 8.3. Every builder returns plain DOM made with AW.el.
(function () {
  "use strict";
  const AW = (window.AW = window.AW || {});
  const el = AW.el;
  const ui = (AW.ui = AW.ui || {});
  const SVG = "http://www.w3.org/2000/svg";
  let serial = 0;
  const uid = (prefix) => prefix + "-" + ++serial;

  ui.icon = function (name, { large = false } = {}) {
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("class", large ? "icon lg" : "icon");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    const use = document.createElementNS(SVG, "use");
    use.setAttribute("href", "#i-" + name);
    svg.append(use);
    return svg;
  };

  const MARKS = ["ok", "idle", "warn", "down", "busy"];
  ui.mark = function (state) {
    return el("span", { class: "mark " + (MARKS.includes(state) ? state : "idle"), attrs: { "aria-hidden": "true" } });
  };
  ui.setMark = function (node, state) {
    node.className = "mark " + (MARKS.includes(state) ? state : "idle");
  };

  ui.chip = function (text, kind, { help } = {}) {
    const cls = "chip" + (kind ? " " + kind : "");
    if (!help) return el("span", { class: cls, text });
    const chip = el("button", { class: cls, type: "button", text, attrs: { "aria-haspopup": "dialog", "aria-expanded": "false" } });
    chip.addEventListener("click", () =>
      ui.popover(chip, (root) => root.append(el("p", { class: "chip-help", text: help })), { width: 300, label: text }),
    );
    return chip;
  };

  function blocked(node) {
    return node.disabled || node.getAttribute("aria-disabled") === "true";
  }

  ui.button = function (label, { kind, small = false, icon, id, onClick, title, pending, errorHost } = {}) {
    const kinds = String(kind || "").split(" ").filter(Boolean);
    const link = kinds.includes("link");
    const iconOnly = kinds.includes("icon");
    const classes = (link ? ["link"] : ["btn", ...kinds]).concat(small && !link ? ["sm"] : []);
    const button = el("button", { class: classes.join(" "), type: "button", id, title: title || (iconOnly ? label : undefined) });
    if (icon) button.append(ui.icon(icon));
    if (iconOnly) button.setAttribute("aria-label", label);
    else button.append(el("span", { class: "btn-label", text: label }));
    button.addEventListener("click", (event) => {
      if (blocked(button)) {
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
      if (!onClick) return;
      let result;
      try {
        result = onClick(event);
      } catch (thrown) {
        result = Promise.reject(thrown);
      }
      if (!result || typeof result.then !== "function") return;
      if (pending) ui.pending(button, pending, result);
      result.catch((thrown) => {
        const error = AW.asError(thrown);
        if (errorHost) ui.showError(errorHost, error);
        else if (error.code === "cancelled") ui.toast(error.message);
        else ui.toast(error.message, { kind: "danger" });
      });
    });
    return button;
  };

  function toButton(action, small = true) {
    if (action instanceof Node) return action;
    return ui.button(action.label, {
      kind: action.kind || (action.primary ? "primary" : action.danger ? "danger" : undefined),
      small,
      icon: action.icon,
      pending: action.pending,
      onClick: action.run,
    });
  }
  ui.actionButton = toButton;

  ui.field = function (label, control, { hint, id } = {}) {
    const controlId = id || control.id || uid("field");
    const target = control.matches("input, select, textarea") ? control : control.querySelector("input, select, textarea") || control;
    if (!target.id) target.id = controlId;
    const error = el("p", { class: "field-error", id: target.id + "-error", hidden: true });
    const hintNode = hint ? el("small", { class: "hint", id: target.id + "-hint", text: hint }) : null;
    const root = el("div", { class: "field" }, el("label", { class: "field-label", for: target.id, text: label }), control, hintNode, error);
    const describe = () => {
      const ids = [hintNode && hintNode.id, error.hidden ? null : error.id].filter(Boolean);
      if (ids.length) target.setAttribute("aria-describedby", ids.join(" "));
      else target.removeAttribute("aria-describedby");
    };
    describe();
    return {
      el: root,
      setError(text) {
        error.hidden = !text;
        error.textContent = text ? AW.fmt.paths(text) : "";
        if (text) target.setAttribute("aria-invalid", "true");
        else target.removeAttribute("aria-invalid");
        describe();
      },
    };
  };

  ui.toggle = function (label, { id, description, checked = false, onChange } = {}) {
    const input = el("input", { type: "checkbox", class: "switch", id: id || uid("switch"), role: "switch", checked });
    const note = el("span", { class: "toggle-note", id: input.id + "-note", hidden: true });
    const root = el(
      "label",
      { class: "toggle" },
      el("span", { class: "toggle-text" }, label, description ? el("small", { text: description }) : null),
      input,
      note,
    );
    input.addEventListener("change", () => onChange && onChange(input.checked, input));
    return {
      el: root,
      input,
      setNote(text) {
        note.hidden = !text;
        note.textContent = text || "";
        if (text) input.setAttribute("aria-describedby", note.id);
        else input.removeAttribute("aria-describedby");
      },
    };
  };

  ui.choice = function ({ name, value, title, description, badge, body }) {
    const input = el("input", { type: "radio", id: uid("choice"), value, attrs: { name } });
    const reason = el("span", { class: "choice-reason", id: input.id + "-reason", hidden: true });
    const bodyNode = body ? el("div", { class: "choice-body" }, body) : null;
    const badgeNode = badge ? (badge instanceof Node ? badge : ui.chip(badge)) : el("span");
    badgeNode.classList.add("choice-badge");
    input.addEventListener("click", (event) => {
      if (input.getAttribute("aria-disabled") === "true") event.preventDefault();
    });
    const root = el(
      "label",
      { class: "choice" },
      input,
      el("span", { class: "choice-main" }, el("span", { class: "choice-title", text: title }), description ? el("span", { class: "choice-desc", text: description }) : null, reason),
      badgeNode,
      bodyNode,
    );
    return {
      el: root,
      input,
      setDisabled(text) {
        reason.hidden = !text;
        reason.textContent = text || "";
        for (const node of [root, input]) {
          if (text) node.setAttribute("aria-disabled", "true");
          else node.removeAttribute("aria-disabled");
        }
        if (text) input.setAttribute("aria-describedby", reason.id);
        else input.removeAttribute("aria-describedby");
      },
      setOpen(open) {
        root.classList.toggle("choice-open", Boolean(open));
      },
    };
  };

  ui.segmented = function (name, options, value, onChange) {
    const group = uid("segmented");
    const root = el("div", { class: "segmented", role: "radiogroup", attrs: { "aria-label": name } });
    const inputs = options.map((option) => {
      const item = typeof option === "string" ? { value: option, label: option } : option;
      const input = el("input", { type: "radio", value: item.value, checked: item.value === value, attrs: { name: group } });
      input.addEventListener("change", () => input.checked && onChange && onChange(item.value));
      root.append(el("label", null, input, el("span", { class: "btn sm", text: item.label })));
      return input;
    });
    return {
      el: root,
      value: () => (inputs.find((input) => input.checked) || {}).value,
      set(next) {
        inputs.forEach((input) => (input.checked = input.value === next));
      },
    };
  };

  ui.tabs = function (root, tabs, { vertical = false, selected, onChange } = {}) {
    const list = el("div", { class: "tabs" + (vertical ? " vertical" : ""), role: "tablist", attrs: { "aria-orientation": vertical ? "vertical" : "horizontal" } });
    const panels = el("div", { class: "tab-panels" });
    const parts = new Map();
    let current = null;
    for (const tab of tabs) {
      const count = el("span", { class: "tab-count", hidden: tab.count === undefined, text: tab.count === undefined ? "" : String(tab.count) });
      const button = el("button", { class: "tab", type: "button", id: "tab-" + tab.id, role: "tab", tabindex: -1, attrs: { "aria-selected": "false", "aria-controls": "tabpanel-" + tab.id } }, tab.label, count);
      const panel = el("div", { class: "tab-panel", id: "tabpanel-" + tab.id, role: "tabpanel", hidden: true, attrs: { "aria-labelledby": button.id } });
      button.addEventListener("click", () => select(tab.id, true));
      parts.set(tab.id, { tab, button, panel, count, rendered: false });
      list.append(button);
      panels.append(panel);
    }
    list.addEventListener("keydown", (event) => {
      const ids = [...parts.keys()];
      const at = ids.indexOf(current);
      const forward = vertical ? "ArrowDown" : "ArrowRight";
      const back = vertical ? "ArrowUp" : "ArrowLeft";
      let next = null;
      if (event.key === forward) next = ids[(at + 1) % ids.length];
      else if (event.key === back) next = ids[(at - 1 + ids.length) % ids.length];
      else if (event.key === "Home") next = ids[0];
      else if (event.key === "End") next = ids[ids.length - 1];
      if (next === null) return;
      event.preventDefault();
      select(next, true);
      parts.get(next).button.focus();
    });
    function select(id, notify) {
      const part = parts.get(id);
      if (!part) return;
      for (const [key, other] of parts) {
        const on = key === id;
        other.button.setAttribute("aria-selected", String(on));
        other.button.tabIndex = on ? 0 : -1;
        other.panel.hidden = !on;
      }
      const changed = current !== id;
      current = id;
      if (!part.rendered) {
        part.rendered = true;
        part.tab.render && part.tab.render(part.panel);
      }
      if (changed && notify && onChange) onChange(id);
    }
    root.append(list, panels);
    if (tabs.length) select(parts.has(selected) ? selected : tabs[0].id, false);
    return {
      select: (id) => select(id, true),
      setCount(id, n) {
        const part = parts.get(id);
        if (!part) return;
        part.count.hidden = n === null || n === undefined;
        part.count.textContent = part.count.hidden ? "" : String(n);
      },
      current: () => current,
    };
  };

  ui.panel = function (title, { count, actions, collapsible = false, id } = {}) {
    const titleId = uid("panel-title");
    const countNode = el("span", { class: "panel-count", text: count === undefined || count === null ? "" : String(count) });
    const actionNodes = el("div", { class: "panel-actions" }, (actions || []).map((action) => toButton(action)));
    const heading = el("h2", { class: "panel-title", id: titleId, text: title });
    const body = el("div", { class: "panel-body" });
    const head = el(collapsible ? "summary" : "div", { class: "panel-head" }, heading, countNode, collapsible ? null : actionNodes);
    const root = el(collapsible ? "details" : "section", { class: "panel", id, attrs: { "aria-labelledby": titleId } }, head, body);
    const busy = el("span", { class: "sr-only", text: "Loading " + title, hidden: true });
    root.append(busy);
    return {
      el: root,
      body,
      setCount(n) {
        countNode.textContent = n === undefined || n === null ? "" : String(n);
      },
      setBusy(on) {
        if (on) root.setAttribute("aria-busy", "true");
        else root.removeAttribute("aria-busy");
        busy.hidden = !on;
      },
    };
  };

  ui.list = function (root, { key, render, update, empty }) {
    const nodes = new Map();
    let emptyNode = null;
    root.classList.add("rows");
    return {
      set(items) {
        const rows = items || [];
        if (rows.length && emptyNode) {
          emptyNode.remove();
          emptyNode = null;
        }
        const seen = new Set();
        let cursor = root.firstChild;
        for (const item of rows) {
          const k = String(key(item));
          seen.add(k);
          let node = nodes.get(k);
          if (node) update && update(node, item);
          else {
            node = render(item);
            nodes.set(k, node);
          }
          if (node === cursor) cursor = cursor.nextSibling;
          else root.insertBefore(node, cursor);
        }
        for (const [k, node] of nodes)
          if (!seen.has(k)) {
            node.remove();
            nodes.delete(k);
          }
        if (!rows.length && empty && !emptyNode) {
          emptyNode = typeof empty === "function" ? empty() : empty instanceof Node ? empty : ui.empty(empty);
          root.append(emptyNode);
        }
      },
      get: (k) => nodes.get(String(k)) || null,
    };
  };

  ui.row = function ({ mark, title, meta, actions, onOpen } = {}) {
    const markNode = ui.mark(mark || "idle");
    markNode.hidden = !mark;
    const titleNode = el("span", { class: "row-title", text: title || "" });
    const metaNode = el("span", { class: "row-meta", text: meta || "", hidden: !meta });
    const main = el(onOpen ? "button" : "div", { class: "row-main", type: onOpen ? "button" : undefined }, titleNode, metaNode);
    if (onOpen) main.addEventListener("click", onOpen);
    const root = el("div", { class: "row" }, markNode, main, el("div", { class: "row-actions" }, (actions || []).map((action) => toButton(action))));
    return {
      el: root,
      update(next = {}) {
        if ("mark" in next) {
          markNode.hidden = !next.mark;
          if (next.mark) ui.setMark(markNode, next.mark);
        }
        if ("title" in next && titleNode.textContent !== next.title) titleNode.textContent = next.title || "";
        if ("meta" in next) {
          metaNode.hidden = !next.meta;
          if (metaNode.textContent !== (next.meta || "")) metaNode.textContent = next.meta || "";
        }
      },
    };
  };

  ui.kv = function (pairs) {
    const list = el("dl", { class: "kv" });
    for (const pair of pairs || []) {
      const [name, value] = Array.isArray(pair) ? pair : [pair.label, pair.value];
      list.append(el("dt", { text: name }), el("dd", null, value instanceof Node ? value : String(value ?? "")));
    }
    return list;
  };

  ui.worksWith = function (compat) {
    const list = el("dl", { class: "works" });
    for (const [name, row] of Object.entries(compat || {})) {
      const ok = Boolean(row && row.ok);
      const reason = row && typeof row.reason === "string" ? row.reason : "";
      const word = ok ? (reason ? "Not confirmed" : "Yes") : "No";
      list.append(
        el("dt", { text: name }),
        el("dd", null, el("span", { class: "works-word" }, ui.mark(ok ? (reason ? "warn" : "ok") : "down"), word), reason ? el("span", { class: "works-reason", text: reason }) : null),
      );
    }
    return list;
  };

  ui.notice = function (kind, text, { actions, detail, dismissible = false, onDismiss } = {}) {
    const root = el("div", { class: "notice " + (kind || "info") }, el("div", { class: "notice-text" }, text instanceof Node ? text : AW.fmt.paths(text)));
    if (detail) root.append(el("details", null, el("summary", { text: "Details" }), el("pre", { text: AW.fmt.paths(detail) })));
    const buttons = (actions || []).map((action) => toButton(action));
    if (dismissible)
      buttons.push(
        ui.button("Dismiss", {
          small: true,
          kind: "quiet",
          onClick() {
            root.remove();
            onDismiss && onDismiss();
          },
        }),
      );
    if (buttons.length) root.append(el("div", { class: "notice-actions" }, buttons));
    return root;
  };

  ui.empty = function (text, actions) {
    const buttons = (actions || []).slice(0, 3).map((action) => toButton(action, false));
    return el("div", { class: "empty" }, el("p", { text }), buttons.length ? el("div", { class: "empty-actions" }, buttons) : null);
  };

  ui.skeleton = function (rows = 3, label) {
    const root = el("div", { class: "skeleton", attrs: { "aria-busy": "true" } }, el("span", { class: "sr-only", text: label ? "Loading " + label : "Loading" }));
    for (let i = 0; i < rows; i++) root.append(el("div", { class: "skeleton-bar" }));
    return root;
  };

  const ACTION_WORDS = { create: "Create", update: "Update", skip: "Skip", delete: "Delete", keep: "Keep" };
  ui.planTable = function (items, { onToggle, onSelect } = {}) {
    const body = el("tbody");
    const table = el(
      "table",
      { class: "plan" },
      el("thead", null, el("tr", null, el("th", null, el("span", { class: "sr-only", text: "Apply" })), el("th", { text: "Action" }), el("th", { text: "File" }), el("th", { text: "Reason" }))),
      body,
    );
    const ticks = [];
    const confirmed = () => ticks.filter((tick) => tick.box.checked).map((tick) => tick.path);
    for (const item of items || []) {
      const path = String(item.path ?? "");
      const action = String(item.action ?? "");
      const writes = action !== "skip";
      const box = el("input", { type: "checkbox", checked: writes, disabled: !writes, attrs: { "aria-label": (writes ? "Apply " : "Nothing to apply for ") + AW.fmt.path(path) } });
      if (writes) ticks.push({ box, path });
      const row = el("tr");
      box.addEventListener("change", () => onToggle && onToggle(path, box.checked, confirmed()));
      const name = onSelect
        ? el("button", {
            class: "link plan-path",
            type: "button",
            text: AW.fmt.path(path),
            on: {
              click() {
                for (const other of body.children) other.removeAttribute("aria-selected");
                row.setAttribute("aria-selected", "true");
                onSelect(item);
              },
            },
          })
        : el("span", { class: "plan-path", text: AW.fmt.path(path) });
      row.append(el("td", null, box), el("td", { text: ACTION_WORDS[action] || action }), el("td", null, name), el("td", { class: "plan-reason", text: AW.fmt.paths(item.reason) }));
      body.append(row);
    }
    return { el: table, confirmed };
  };

  ui.diff = function (text) {
    const pre = el("pre", { class: "diff", tabindex: 0, attrs: { "aria-label": "Changes" } });
    for (const line of String(text ?? "").split("\n")) {
      let cls = "";
      if (line.startsWith("@@")) cls = "hunk";
      else if (line.startsWith("+") && !line.startsWith("+++")) cls = "add";
      else if (line.startsWith("-") && !line.startsWith("---")) cls = "del";
      pre.append(el("span", { class: cls, text: line || " " }));
    }
    return pre;
  };

  const TASK_MARK = { running: "busy", done: "ok", failed: "down", cancelled: "idle", interrupted: "warn" };
  const DOWNLOADS = new Set(["model-download", "studio-download"]);
  function taskMessage(record, progress) {
    let message = typeof progress.message === "string" ? progress.message.trim() : "";
    const title = String(record.title || "").trim();
    if (DOWNLOADS.has(record.action) && progress.phase === "downloading") return "";
    if (title && message.toLowerCase().startsWith(title.toLowerCase())) message = message.slice(title.length).replace(/^[\s·:,.…-]+/, "");
    return message;
  }

  function taskText(record, events) {
    const progress = record.progress || {};
    const parts = [];
    const message = taskMessage(record, progress);
    if (message) parts.push(AW.fmt.paths(message));
    const done = Number(progress.done);
    const total = Number(progress.total);
    if (Number.isFinite(done) && Number.isFinite(total) && total > 0) {
      if (progress.asset && !message.includes(String(progress.asset))) parts.push(String(progress.asset));
      parts.push(AW.fmt.bytes(done) + " of " + AW.fmt.bytes(total));
      if (progress.count > 1) parts.push("file " + progress.index + " of " + progress.count);
      if (progress.rate > 0) parts.push(AW.fmt.rate(progress.rate));
      const left = events >= 2 ? AW.fmt.left(done, total, progress.rate) : "";
      if (left) parts.push(left);
    } else if (record.started) {
      parts.push(AW.fmt.duration((Date.now() - record.started) / 1000));
      if (progress.budget > 0) parts.push("gives up after " + Math.round(progress.budget / 60) + " min");
    }
    return parts.join(" · ");
  }

  ui.taskText = taskText;

  ui.taskRow = function (handle, { compact = false } = {}) {
    const fixed = handle && typeof handle === "object" ? handle : null;
    if (fixed) handle = fixed.handle;
    const find = () => fixed || (AW.jobs ? AW.jobs.list().find((record) => record.handle === handle) : null) || null;
    const mark = ui.mark("busy");
    const title = el("span", { class: "task-title" });
    const bar = el("progress", { attrs: { max: "1" } });
    const text = el("div", { class: "task-text" });
    const note = el("div", { class: "task-text", text: "Cannot be cancelled here.", hidden: true });
    const cancel = ui.button("Cancel", {
      small: true,
      onClick() {
        cancel.querySelector(".btn-label").textContent = "Cancelling…";
        cancel.setAttribute("aria-disabled", "true");
        return AW.jobs.cancel(handle).then((stopped) => {
          if (!stopped) text.textContent = "This task had already finished.";
        });
      },
    });
    const root = el("div", { class: "task" + (compact ? " compact" : ""), data: { handle } }, mark, title, cancel, bar, text, note);
    let events = 0;
    function paint(record) {
      if (!record) return;
      const running = record.state === "running";
      ui.setMark(mark, TASK_MARK[record.state] || "idle");
      title.textContent = record.title || record.action || "Task";
      bar.setAttribute("aria-label", title.textContent);
      cancel.hidden = !(running && record.cancellable);
      note.hidden = compact || !running || Boolean(record.cancellable);
      bar.hidden = !running;
      const progress = record.progress || {};
      if (running && Number.isFinite(Number(progress.done)) && Number(progress.total) > 0) {
        bar.max = Number(progress.total);
        bar.value = Math.min(Number(progress.done), Number(progress.total));
      } else bar.removeAttribute("value");
      if (running) text.textContent = taskText(record, events);
      else if (record.state === "interrupted") text.textContent = "Interrupted: the Workbench service restarted.";
      else if (record.error) text.textContent = record.error.message || "";
      else if (record.value && typeof record.value.message === "string") text.textContent = record.value.message;
      else text.textContent = record.state === "done" ? "Finished." : "";
    }
    const off = AW.jobs && !fixed
      ? AW.jobs.on((record, kind) => {
          if (record.handle !== handle) return;
          if (kind === "progress") events += 1;
          paint(record);
        })
      : () => {};
    const timer = setInterval(() => {
      const record = find();
      if (!record || record.state !== "running") clearInterval(timer);
      else if (root.isConnected) text.textContent = taskText(record, events);
    }, 1000);
    paint(find());
    return {
      el: root,
      destroy() {
        off();
        clearInterval(timer);
        root.remove();
      },
    };
  };

  const SLOTS = [
    ["project", "Project"],
    ["model", "Model"],
    ["agent", "Agent"],
    ["setup", "Instructions and tools"],
  ];
  const LEVEL_MARK = { ok: "ok", block: "warn", work: "busy", warn: "warn", info: "ok" };
  const LEVEL_WORD = { ok: "ready", block: "needs attention", work: "working", warn: "worth checking", info: "ready" };

  ui.readinessList = function (root, { onFix } = {}) {
    const list = el("div", { class: "ready" });
    const rows = new Map();
    for (const [slot, name] of SLOTS) {
      const mark = ui.mark("idle");
      const word = el("span", { class: "sr-only" });
      const summary = el("div", { class: "ready-summary" });
      const items = el("div", { class: "ready-items" });
      const row = el("div", { class: "ready-row", data: { slot } }, mark, el("div", null, el("h3", { class: "ready-name" }, name, word), summary), items);
      rows.set(slot, { row, mark, word, summary, items, tasks: [], signature: "" });
      list.append(row);
    }
    root.append(list);

    function fixButton(fix, item, first) {
      const known = AW.fix && AW.fix.has(fix.run);
      const run = (value) => {
        const ctx = { origin: button, item, value };
        if (onFix) return onFix(fix, ctx);
        return AW.fix.run(fix.run, fix.arg, ctx);
      };
      let input = null;
      const button = ui.button(fix.label, {
        small: true,
        kind: fix.primary || first ? "primary" : undefined,
        pending: fix.pending,
        onClick() {
          if (!input) return run();
          const result = Promise.resolve(run(input.value));
          result.then(() => (input.value = ""), () => {});
          return result;
        },
      });
      button.dataset.fix = fix.run;
      if (!known) {
        button.setAttribute("aria-disabled", "true");
        button.title = "Not available yet.";
      }
      if (!fix.field) return button;
      input = el("input", { type: fix.field.type || "text", attrs: { "aria-label": fix.field.label || fix.label, placeholder: fix.field.label || "", autocomplete: "off", spellcheck: "false" } });
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") button.click();
      });
      return el("div", { class: "ready-field" }, input, button);
    }

    function renderItems(part, items) {
      part.tasks.splice(0).forEach((task) => task.destroy());
      part.items.replaceChildren();
      for (const item of items) {
        const node = el("div", { class: "ready-item", data: { item: item.id, level: item.level } });
        if (item.title) node.append(el("span", { class: "ready-item-title", text: item.title }));
        if (item.text) node.append(el("span", { text: AW.fmt.paths(item.text) }));
        if (item.task) {
          const task = ui.taskRow(item.task, { compact: true });
          part.tasks.push(task);
          node.append(task.el);
        }
        const fixes = el("div", { class: "ready-fixes" });
        const primaryAt = (item.fixes || []).findIndex((fix) => fix.primary);
        (item.fixes || []).forEach((fix, index) => fixes.append(fixButton(fix, item, item.level === "block" && primaryAt < 0 && index === 0)));
        node.append(fixes);
        part.items.append(node);
      }
    }

    return {
      update(result) {
        const data = result || { slots: {}, items: [] };
        for (const [slot] of SLOTS) {
          const part = rows.get(slot);
          const state = (data.slots && data.slots[slot]) || {};
          const level = state.level || "ok";
          const items = (data.items || []).filter((item) => item.slot === slot);
          ui.setMark(part.mark, LEVEL_MARK[level] || "idle");
          part.row.dataset.level = level;
          part.word.textContent = ", " + (LEVEL_WORD[level] || level);
          const summary = state.summary || "";
          if (part.summary.textContent !== summary) part.summary.textContent = summary;
          part.summary.hidden = !summary;
          const signature = JSON.stringify(items);
          if (signature !== part.signature) {
            part.signature = signature;
            renderItems(part, items);
          }
        }
      },
      focusFirstBlocker() {
        const blocker = list.querySelector('.ready-item[data-level="block"], .ready-item[data-level="work"]');
        if (!blocker) return false;
        const target = blocker.querySelector('input, button:not([aria-disabled="true"])') || blocker.querySelector("button");
        if (target) target.focus();
        else {
          blocker.tabIndex = -1;
          blocker.focus();
        }
        blocker.scrollIntoView({ block: "nearest" });
        return true;
      },
      destroy() {
        for (const part of rows.values()) part.tasks.splice(0).forEach((task) => task.destroy());
        list.remove();
      },
    };
  };

  const dialogs = new WeakMap();
  const modals = [];
  ui.topModal = function () {
    for (let at = modals.length - 1; at >= 0; at--) if (modals[at].open && modals[at].matches(":modal")) return modals[at];
    const open = [...document.querySelectorAll("dialog[open]")].filter((node) => node.matches(":modal"));
    return open.length ? open[open.length - 1] : null;
  };
  ui.dialog = function (idOrElement, { onClose } = {}) {
    const node = typeof idOrElement === "string" ? AW.$(idOrElement) : idOrElement;
    let api = dialogs.get(node);
    if (api) {
      if (onClose !== undefined) api.onClose = onClose;
      return api;
    }
    let value = null;
    let waiters = [];
    api = {
      el: node,
      onClose,
      open({ keepPopover = false } = {}) {
        value = null;
        for (const pop of [...stack]) if (!keepPopover || !pop.el.contains(document.activeElement)) pop.close(false);
        if (!node.open) node.showModal();
        if (modals.includes(node)) modals.splice(modals.indexOf(node), 1);
        modals.push(node);
      },
      close(result) {
        value = result === undefined ? null : result;
        if (node.open) node.close();
      },
      opened() {
        return new Promise((resolve) => waiters.push(resolve));
      },
    };
    node.addEventListener("close", () => {
      if (modals.includes(node)) modals.splice(modals.indexOf(node), 1);
      const result = value;
      value = null;
      const pending = waiters;
      waiters = [];
      if (api.onClose) api.onClose(result);
      pending.forEach((resolve) => resolve(result));
    });
    dialogs.set(node, api);
    return api;
  };
  document.addEventListener("click", (event) => {
    const closer = event.target.closest ? event.target.closest("dialog [data-close]") : null;
    const node = closer ? closer.closest("dialog") : null;
    if (!node) return;
    if (dialogs.has(node)) dialogs.get(node).close(null);
    else node.close();
  });

  const stack = [];
  const within = (pop, target) => stack.slice(stack.indexOf(pop)).some((other) => other.el.contains(target) || other.anchor.contains(target));
  const foreignModal = (pop, target) => {
    const modal = target && target.closest ? target.closest("dialog[open]") : null;
    return Boolean(modal) && !modal.contains(pop.el);
  };
  document.addEventListener(
    "pointerdown",
    (event) => {
      for (const pop of [...stack]) if (!within(pop, event.target) && !foreignModal(pop, event.target)) pop.close(false);
    },
    true,
  );
  document.addEventListener("focusin", (event) => {
    for (const pop of [...stack]) if (!within(pop, event.target) && !foreignModal(pop, event.target)) pop.close(false);
  });
  document.addEventListener(
    "keydown",
    (event) => {
      if (event.key !== "Escape" || !stack.length) return;
      const top = stack[stack.length - 1];
      if (foreignModal(top, event.target)) return;
      event.preventDefault();
      event.stopPropagation();
      top.close(true);
    },
    true,
  );
  window.addEventListener("resize", () => stack.forEach((pop) => pop.place()));
  document.addEventListener(
    "scroll",
    (event) => {
      for (const pop of stack) if (!pop.el.contains(event.target)) pop.place();
    },
    true,
  );

  ui.popover = function (anchor, build, { width = 380, placement = "below", label, element } = {}) {
    const existing = stack.find((pop) => pop.anchor === anchor);
    if (existing) {
      existing.close(true);
      return existing;
    }
    for (const pop of [...stack]) if (!pop.el.contains(anchor)) pop.close(false);
    const node = element || el("div", { class: "popover", role: "dialog" });
    if (label) node.setAttribute("aria-label", label);
    const token = AW.focus.remember();
    let release = () => {};
    let closed = false;
    const pop = {
      el: node,
      anchor,
      place() {
        if (!anchor.getClientRects().length) return;
        const box = anchor.getBoundingClientRect();
        const wide = Math.min(width, window.innerWidth - 32);
        node.style.width = wide + "px";
        const left = Math.max(16, Math.min(box.left, window.innerWidth - wide - 16));
        const tall = node.offsetHeight;
        let top = placement === "above" ? box.top - tall - 6 : box.bottom + 6;
        if (placement === "above" && top < 16) top = box.bottom + 6;
        if (top + tall > window.innerHeight - 16) top = Math.max(16, placement === "above" ? window.innerHeight - tall - 16 : Math.min(top, window.innerHeight - tall - 16));
        node.style.left = left + "px";
        node.style.top = top + "px";
      },
      close(restore = true) {
        if (closed) return;
        closed = true;
        stack.splice(stack.indexOf(pop), 1);
        release();
        const inside = node.contains(document.activeElement);
        if (element) node.hidden = true;
        else node.remove();
        anchor.setAttribute("aria-expanded", "false");
        if (pop.onClose) pop.onClose();
        if (restore || inside) AW.focus.restore(token);
      },
    };
    node._awAnchor = anchor;
    if (element) node.hidden = false;
    else (anchor.closest("dialog") || document.body).append(node);
    anchor.setAttribute("aria-expanded", "true");
    stack.push(pop);
    if (build) build(node, pop.close);
    pop.place();
    release = AW.focus.trap(node);
    if (!AW.focus.first(node)) {
      node.tabIndex = -1;
      node.focus();
    }
    return pop;
  };
  ui.popoverFor = (anchor) => stack.find((pop) => pop.anchor === anchor) || null;

  ui.menu = function (anchor, items) {
    anchor.setAttribute("aria-haspopup", "menu");
    return ui.popover(
      anchor,
      (root, close) => {
        const name = anchor.getAttribute("aria-label") || anchor.textContent.trim() || "Menu";
        const menu = el("div", { class: "menu", role: "menu", attrs: { "aria-label": name } });
        root.removeAttribute("role");
        for (const item of items) {
          const button = el("button", { type: "button", role: "menuitem", class: item.danger ? "danger" : "", text: item.label });
          button.addEventListener("click", () => {
            close(true);
            Promise.resolve()
              .then(() => item.run())
              .catch((thrown) => ui.toast(AW.asError(thrown).message, { kind: "danger" }));
          });
          menu.append(button);
        }
        menu.addEventListener("keydown", (event) => {
          const buttons = [...menu.children];
          const at = buttons.indexOf(document.activeElement);
          let next = null;
          if (event.key === "ArrowDown") next = buttons[(at + 1) % buttons.length];
          else if (event.key === "ArrowUp") next = buttons[(at - 1 + buttons.length) % buttons.length];
          else if (event.key === "Home") next = buttons[0];
          else if (event.key === "End") next = buttons[buttons.length - 1];
          if (!next) return;
          event.preventDefault();
          next.focus();
        });
        root.append(menu);
      },
      { width: 220 },
    );
  };
})();
