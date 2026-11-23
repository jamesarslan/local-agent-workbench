// Shared helpers: element builder, bridge requests, storage, clipboard and formatters (ui.md 9.1).
(function (root) {
  "use strict";
  const AW = (root.AW = root.AW || {});
  const doc = root.document;
  const nav = root.navigator || {};
  const FALLBACK = "Something went wrong. Try again.";

  AW.$ = (id) => doc.getElementById(id);

  function append(parent, child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) child.forEach((item) => append(parent, item));
    else parent.append(typeof child === "object" ? child : doc.createTextNode(String(child)));
  }

  AW.el = function (tag, props, ...children) {
    const node = doc.createElement(tag);
    const p = props || {};
    if (p.id) node.id = p.id;
    if (p.class) node.className = p.class;
    if (p.text !== undefined && p.text !== null) node.textContent = String(p.text);
    if (p.title) node.title = p.title;
    if (p.hidden) node.hidden = true;
    if (p.disabled) node.disabled = true;
    if (p.type) node.type = p.type;
    if (p.value !== undefined) node.value = p.value;
    if (p.checked) node.checked = true;
    if (p.for) node.htmlFor = p.for;
    if (p.role) node.setAttribute("role", p.role);
    if (p.tabindex !== undefined) node.tabIndex = p.tabindex;
    for (const [name, value] of Object.entries(p.attrs || {}))
      if (value !== null && value !== undefined && value !== false) node.setAttribute(name, value === true ? "" : String(value));
    for (const [key, value] of Object.entries(p.data || {}))
      if (value !== null && value !== undefined) node.dataset[key] = String(value);
    for (const [type, handler] of Object.entries(p.on || {})) node.addEventListener(type, handler);
    append(node, children);
    return node;
  };

  AW.on = function (target, type, handler, options) {
    target.addEventListener(type, handler, options);
    return () => target.removeEventListener(type, handler, options);
  };

  AW.asError = function (thrown) {
    if (thrown && typeof thrown === "object") {
      const out = { message: typeof thrown.message === "string" && thrown.message.trim() ? thrown.message.trim() : FALLBACK };
      for (const key of ["detail", "action", "code"]) if (typeof thrown[key] === "string" && thrown[key]) out[key] = thrown[key];
      return out;
    }
    if (typeof thrown === "string" && thrown.trim()) return { message: thrown.trim() };
    return { message: FALLBACK };
  };

  AW.request = function (action, data = {}) {
    const bridge = root.workbench;
    if (!bridge || typeof bridge.request !== "function")
      return Promise.reject({ message: "The Workbench service is not available in this window. Restart Agent Workbench.", code: "bridge-failed", action: "restart" });
    let call;
    try {
      call = Promise.resolve(bridge.request(action, data && typeof data === "object" ? data : {}));
    } catch (thrown) {
      call = Promise.reject(thrown);
    }
    return call.catch((thrown) => {
      throw AW.asError(thrown);
    });
  };

  const platformText = String(nav.platform || nav.userAgent || "");
  const mac = /mac/i.test(platformText);
  const windows = /win/i.test(platformText);
  AW.platform = { mac, windows, linux: !mac && !windows, mod: mac ? "Cmd" : "Ctrl" };

  AW.debounce = function (fn, ms) {
    let timer = null;
    const run = function (...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), ms);
    };
    run.cancel = () => clearTimeout(timer);
    return run;
  };

  AW.ticket = function () {
    let n = 0;
    return { next: () => ++n, current: (value) => value === n };
  };

  AW.storage = {
    get(key, fallback = null) {
      try {
        const value = root.localStorage.getItem(key);
        return value === null || value === undefined ? fallback : value;
      } catch {
        return fallback;
      }
    },
    set(key, value) {
      try {
        root.localStorage.setItem(key, String(value));
      } catch {
        return;
      }
    },
    remove(key) {
      try {
        root.localStorage.removeItem(key);
      } catch {
        return;
      }
    },
  };

  AW.clipboard = {
    async write(text) {
      try {
        await nav.clipboard.writeText(String(text));
        return true;
      } catch {
        return false;
      }
    },
  };

  const KIB = 1024;
  const MIB = KIB * KIB;
  const GIB = KIB * MIB;
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const one = (x) => (Math.round(x * 10) / 10).toFixed(1);
  const whole = (x, unit) => Math.round(x / unit);
  const PACE_WINDOW = 10000;
  const DRIVE = /^(?:[A-Za-z]:[\\/]|\\\\)/;
  const PRIVATE = /^\/(?:var|tmp|etc)(?=\/|$)/;

  // One spelling of a path for comparison: /var, /tmp and /etc are /private/… on macOS; Windows paths fold case and use backslashes.
  function canon(text, windows) {
    let value = String(text);
    if (windows) value = value.replace(/\//g, "\\");
    else if (PRIVATE.test(value)) value = "/private" + value;
    const sep = windows ? "\\" : "/";
    while (value.length > 1 && value.endsWith(sep) && !/^[A-Za-z]:\\$/.test(value)) value = value.slice(0, -1);
    return { text: value, key: windows ? value.toLowerCase() : value, sep };
  }

  function homeFolder() {
    const config = root.AW && root.AW.state && root.AW.state.config;
    return config && typeof config.home === "string" ? config.home : "";
  }

  function shorten(text, home) {
    if (typeof text !== "string" || !text || !home) return null;
    const windows = DRIVE.test(home);
    const base = canon(home, windows);
    if (base.key.length < 2 || base.key === "/private") return null;
    const full = canon(text, windows);
    if (full.key === base.key) return "~";
    if (!full.key.startsWith(base.key + base.sep)) return null;
    return "~" + full.text.slice(base.text.length);
  }

  AW.fmt = {
    bytes(n) {
      const value = Number(n);
      if (n === null || n === undefined || n === "" || !Number.isFinite(value) || value < 0) return "";
      if (value >= GIB || whole(value, MIB) >= 1024) return one(value / GIB) + " GiB";
      if (value >= MIB || whole(value, KIB) >= 1024) return whole(value, MIB) + " MiB";
      if (value >= 10 * KIB) return whole(value, KIB) + " KiB";
      if (value >= KIB) return one(value / KIB) + " KiB";
      return Math.round(value) + " B";
    },
    gib(x) {
      const value = Number(x);
      if (x === null || x === undefined || x === "" || !Number.isFinite(value) || value < 0) return "";
      return AW.fmt.bytes(value * GIB);
    },
    rate(bytesPerSecond) {
      const text = AW.fmt.bytes(bytesPerSecond);
      return text ? text + "/s" : "";
    },
    duration(seconds) {
      const s = Math.max(0, Math.round(Number(seconds) || 0));
      if (s < 60) return s + " s";
      if (s < 3600) return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
      return Math.floor(s / 3600) + " h " + Math.floor((s % 3600) / 60) + " min";
    },
    ago(msOrIso, now = Date.now()) {
      let then = typeof msOrIso === "number" ? msOrIso : Date.parse(msOrIso);
      if (!Number.isFinite(then) || then <= 0) return "";
      if (then < 1e11) then *= 1000;
      const s = Math.max(0, Math.floor((now - then) / 1000));
      if (s < 1) return "just now";
      if (s < 60) return s + " s ago";
      if (s < 3600) return Math.floor(s / 60) + " min ago";
      if (s < 86400) {
        const h = Math.floor(s / 3600);
        return h + (h === 1 ? " hour ago" : " hours ago");
      }
      if (s < 172800) return "yesterday";
      const date = new Date(then);
      const year = date.getFullYear() === new Date(now).getFullYear() ? "" : " " + date.getFullYear();
      return date.getDate() + " " + MONTHS[date.getMonth()] + year;
    },
    left(done, total, rate) {
      if (!(Number(rate) > 0) || !(Number(total) > Number(done))) return "";
      const seconds = (total - done) / rate;
      return seconds < 60 ? "less than a minute left" : "about " + Math.round(seconds / 60) + " min left";
    },
    // Rate over the last ten seconds of progress: add(done, at) per progress event; rate() stays 0 until two samples exist.
    pace() {
      let samples = [];
      return {
        add(done, at = Date.now()) {
          const value = Number(done);
          if (!Number.isFinite(value)) return;
          if (samples.length && value < samples[samples.length - 1].done) samples = [];
          samples.push({ done: value, at });
          while (samples.length > 2 && at - samples[1].at >= PACE_WINDOW) samples.shift();
        },
        rate() {
          if (samples.length < 2) return 0;
          const first = samples[0];
          const last = samples[samples.length - 1];
          const span = (last.at - first.at) / 1000;
          return span > 0 ? Math.max(0, (last.done - first.done) / span) : 0;
        },
        samples: () => samples.length,
      };
    },
    context(tokens) {
      const value = Number(tokens);
      if (!Number.isFinite(value) || value <= 0) return "";
      if (value < 1000) return String(Math.round(value));
      const k = Math.round(value / (value % 1000 === 0 ? 1000 : 1024));
      if (k < 1000) return k + "K";
      const m = Math.round(k / 100) / 10;
      return (Number.isInteger(m) ? String(m) : m.toFixed(1)) + "M";
    },
    folder(path) {
      const parts = String(path || "").split(/[\\/]+/).filter(Boolean);
      return parts.length ? parts[parts.length - 1] : "";
    },
    home: homeFolder,
    // Display form of a path: inside the home folder it reads "~/…". Requests always send the full path.
    path(text, home = homeFolder()) {
      if (text === null || text === undefined) return "";
      return shorten(String(text), home) ?? String(text);
    },
    // The full path for a typed "~/…", so requests never carry the short form.
    expand(text, home = homeFolder()) {
      const value = String(text ?? "").trim();
      if (!home || !/^~(?:[\\/]|$)/.test(value)) return value;
      return home + value.slice(1);
    },
    // A sentence with absolute paths in it (a toast, a notice, an error from the service) with each home-folder path shortened.
    paths(text, home = homeFolder()) {
      const value = String(text ?? "");
      if (!home) return value;
      const windows = DRIVE.test(home);
      const base = canon(home, windows).text;
      const spellings = new Set([base, base.startsWith("/private/") ? base.slice(8) : "", String(home)].filter((item) => item.length > 1));
      let out = value;
      for (const spelling of [...spellings].sort((a, b) => b.length - a.length)) {
        const escaped = spelling.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        const sep = windows ? "[\\\\/]" : "/";
        const pattern = new RegExp("(^|[\\s\"'“‘(\\[=:])" + escaped + "(?=" + sep + "|$|[\\s\"'”’)\\]]|[,.;:!?](?:$|[\\s\"'”’)\\]]))", windows ? "gi" : "g");
        out = out.replace(pattern, "$1~");
      }
      return out;
    },
    middle(text, max) {
      const value = String(text ?? "");
      if (value.length <= max || max < 5) return value;
      const head = Math.ceil((max - 1) / 2);
      return value.slice(0, head) + "…" + value.slice(value.length - (max - 1 - head));
    },
    list(names) {
      const items = (names || []).filter(Boolean).map(String);
      if (items.length < 2) return items.join("");
      return items.slice(0, -1).join(", ") + " and " + items[items.length - 1];
    },
  };
})(typeof window === "undefined" ? globalThis : window);
