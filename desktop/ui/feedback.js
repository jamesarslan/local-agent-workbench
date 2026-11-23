// Toasts, live regions, the confirm dialog, error notices and focus helpers (ui.md 5.7, 6.2, 9.2).
(function () {
  "use strict";
  const AW = (window.AW = window.AW || {});
  const el = AW.el;
  const ui = (AW.ui = AW.ui || {});
  const nativeConfirm = window.confirm;
  const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

  const visible = (node) => node.getClientRects().length > 0;
  const focusables = (root) => [...root.querySelectorAll(FOCUSABLE)].filter(visible);

  function liveRegion(assertive) {
    const modal = ui.topModal();
    if (!modal) return AW.$(assertive ? "live-alert" : "live-status");
    const kind = assertive ? "alert" : "status";
    let region = modal.querySelector(':scope > [data-live="' + kind + '"]');
    if (!region) {
      region = el("div", { class: "sr-only", role: kind, data: { live: kind }, attrs: { "aria-live": assertive ? "assertive" : "polite" } });
      modal.append(region);
    }
    return region;
  }

  ui.announce = function (text, { assertive = false } = {}) {
    const region = liveRegion(assertive);
    if (!region) return;
    region.textContent = "";
    requestAnimationFrame(() => {
      region.textContent = String(text ?? "");
    });
  };

  function toastHost() {
    const host = AW.$("toasts");
    const home = ui.topModal() || document.body;
    if (host.parentNode !== home) home.append(host);
    return host;
  }
  document.addEventListener(
    "close",
    (event) => {
      if (event.target instanceof HTMLDialogElement && event.target.contains(AW.$("toasts"))) toastHost();
    },
    true,
  );

  ui.toast = function (text, { kind = "neutral", action, timeout = 6000 } = {}) {
    const host = toastHost();
    let timer = null;
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      clearTimeout(timer);
      node.remove();
    };
    const arm = () => {
      clearTimeout(timer);
      if (timeout > 0) timer = setTimeout(close, timeout);
    };
    const hold = () => clearTimeout(timer);
    text = AW.fmt.paths(text);
    const node = el("div", { class: "toast" + (kind === "neutral" ? "" : " " + kind) }, el("span", { class: "toast-text", text }));
    if (action)
      node.append(
        ui.button(action.label, {
          small: true,
          onClick() {
            close();
            return action.run();
          },
        }),
      );
    node.append(ui.button("Dismiss", { kind: "quiet icon", small: true, icon: "close", onClick: close }));
    node.addEventListener("mouseenter", hold);
    node.addEventListener("mouseleave", arm);
    node.addEventListener("focusin", hold);
    node.addEventListener("focusout", arm);
    host.append(node);
    while (host.children.length > 3) host.firstElementChild.remove();
    arm();
    ui.announce(text, { assertive: kind === "danger" });
    return { close };
  };

  ui.confirm = function ({ text, ok = "OK", cancel = "Cancel", danger = false }) {
    if (window.confirm !== nativeConfirm) return Promise.resolve(Boolean(window.confirm(text)));
    const dialog = ui.dialog("confirm-dialog");
    const okButton = AW.$("confirm-ok");
    const cancelButton = AW.$("confirm-cancel");
    AW.$("confirm-dialog-title").textContent = AW.fmt.paths(text);
    okButton.textContent = ok;
    cancelButton.textContent = cancel;
    okButton.className = "btn primary" + (danger ? " danger" : "");
    okButton.onclick = () => dialog.close(true);
    cancelButton.onclick = () => dialog.close(false);
    const token = AW.focus.remember();
    const answer = dialog.opened().then((value) => {
      AW.focus.restore(token);
      return value === true;
    });
    dialog.open({ keepPopover: true });
    (danger ? cancelButton : okButton).focus();
    return answer;
  };

  function copyLogPath() {
    return AW.request("bridge-status").then(async (status) => {
      const copied = status && status.log ? await AW.clipboard.write(status.log) : false;
      ui.toast(copied ? "Copied." : "The log path could not be copied.", copied ? {} : { kind: "danger" });
    });
  }

  function showLogButton() {
    const button = ui.button("Show log file", {
      small: true,
      onClick: () =>
        AW.request("bridge-log-open").then((shown) => {
          if (!shown) button.hidden = true;
        }),
    });
    return button;
  }

  ui.errorNotice = function (thrown, { retry, tool } = {}) {
    const error = AW.asError(thrown);
    const again = retry ? [{ label: "Try again", run: retry, primary: true }] : [];
    if (error.code === "cancelled") return el("p", { class: "notice neutral", data: { error: "cancelled" }, text: AW.fmt.paths(error.message) });
    let kind = "danger";
    let text = error.message;
    let actions = [];
    if (error.code === "busy") kind = "neutral";
    else if (error.code === "timeout") {
      kind = "warn";
      text = error.message + " The work may still be running. Look in Tasks.";
      actions = again;
    } else if (error.code === "bridge-stopped") {
      kind = "warn";
      actions = again;
    } else if (error.code === "bridge-failed") actions = [...again, { label: "Copy log path", run: copyLogPath }, showLogButton()];
    else if (error.action === "retry") actions = again;
    else if (error.action === "setup") actions = [{ label: "Open Advanced → Setup", run: () => AW.router.go("advanced", { section: "Setup", tool }) }];
    else if (error.action === "doctor") actions = [{ label: "Run Doctor", run: () => AW.router.go("doctor") }];
    else if (error.action === "log")
      actions = tool ? [{ label: "Open setup log", run: () => AW.request("studio-setup-log") }] : [showLogButton(), { label: "Copy log path", run: copyLogPath }];
    else if (error.code === "internal") actions = [{ label: "Copy log path", run: copyLogPath }];
    else if (!error.code && !error.action) actions = again;
    const notice = ui.notice(kind, text, { actions, detail: error.detail, dismissible: error.code !== "busy" });
    notice.dataset.error = error.code || error.action || "refused";
    return notice;
  };

  ui.showError = function (host, thrown, options) {
    const error = AW.asError(thrown);
    for (const old of host.querySelectorAll(":scope > [data-error]")) old.remove();
    const notice = ui.errorNotice(error, options);
    host.append(notice);
    ui.announce(AW.fmt.paths(error.message), { assertive: error.code !== "cancelled" && error.code !== "busy" });
    return notice;
  };

  ui.clearError = function (host) {
    for (const old of host.querySelectorAll(":scope > [data-error]")) old.remove();
  };

  ui.pending = function (button, label, promise) {
    const kept = [...button.childNodes];
    const before = button.getAttribute("aria-disabled");
    button.dataset.pending = "true";
    button.setAttribute("aria-disabled", "true");
    if (label) button.replaceChildren(el("span", { class: "btn-label", text: label }));
    const done = () => {
      delete button.dataset.pending;
      if (before === null) button.removeAttribute("aria-disabled");
      else button.setAttribute("aria-disabled", before);
      if (label) button.replaceChildren(...kept);
    };
    Promise.resolve(promise).then(done, done);
    return promise;
  };

  ui.notify = function (kind, text, { id, actions, detail } = {}) {
    const host = AW.$("notices");
    if (id) ui.notify.clear(id);
    const notice = ui.notice(kind, text, { actions, detail, dismissible: true });
    if (id) notice.dataset.notice = id;
    host.append(notice);
    ui.announce(AW.fmt.paths(text), { assertive: kind === "danger" });
    return { el: notice, close: () => notice.remove() };
  };
  ui.notify.clear = function (id) {
    for (const old of AW.$("notices").children) if (old.dataset.notice === id) old.remove();
  };

  const regionOf = (node) => {
    const chain = [];
    let at = node;
    while (at) {
      const pop = at.closest ? at.closest(".popover") : null;
      if (!pop || !pop._awAnchor) break;
      chain.push(pop._awAnchor);
      at = pop._awAnchor;
    }
    const region = (at || node).closest ? (at || node).closest("[data-region]") : null;
    if (region) chain.push(region);
    return chain;
  };
  const usable = (node) => node && node.isConnected && !node.disabled && visible(node);

  AW.focus = {
    remember() {
      const node = document.activeElement && document.activeElement !== document.body ? document.activeElement : null;
      return { node, fallbacks: node ? regionOf(node) : [] };
    },
    restore(token) {
      if (!token) return;
      if (usable(token.node)) {
        token.node.focus();
        return;
      }
      for (const fallback of token.fallbacks) {
        if (!usable(fallback)) continue;
        if (fallback.matches("[data-region]")) AW.focus.enter(fallback);
        else fallback.focus();
        return;
      }
    },
    first(root) {
      const target = focusables(root)[0];
      if (target) target.focus();
      return Boolean(target);
    },
    trap(root) {
      const onKey = (event) => {
        if (event.key !== "Tab") return;
        const items = focusables(root);
        if (!items.length) {
          event.preventDefault();
          return;
        }
        const first = items[0];
        const last = items[items.length - 1];
        if (event.shiftKey && (document.activeElement === first || !root.contains(document.activeElement))) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      };
      root.addEventListener("keydown", onKey);
      return () => root.removeEventListener("keydown", onKey);
    },
    enter(region) {
      const name = region.dataset.region;
      let target = null;
      if (name === "rail") target = region.querySelector('.nav[aria-current="page"]');
      else if (name === "view") target = region.querySelector(".view:not([hidden]) h1") || region;
      else if (name === "dock") target = region.querySelector("#term-screen textarea");
      if (target && visible(target)) {
        if (!target.hasAttribute("tabindex") && !target.matches(FOCUSABLE)) target.tabIndex = -1;
        target.focus();
        return true;
      }
      return AW.focus.first(region);
    },
    region(delta) {
      const regions = [...document.querySelectorAll("[data-region]")].filter(visible);
      if (!regions.length) return;
      const at = regions.findIndex((region) => region.contains(document.activeElement));
      const start = at < 0 ? (delta > 0 ? -1 : 0) : at;
      for (let step = 1; step <= regions.length; step++) {
        const next = regions[(start + delta * step + regions.length * step) % regions.length];
        if (AW.focus.enter(next)) return;
      }
    },
  };
})();
