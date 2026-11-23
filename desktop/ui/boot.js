// Start-up: binds the form, starts the shell, loads the first state and routes; the failure screen and the service notices (ui.md 3.1, 5.15, 7.4, 9.7).
// Beyond ui.md 9.7: AW.boot.retry() asks for app-state again (the failure screen's Try again, and the Tasks popover's service row).
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const SCREEN_MS = 400;
  const SERVICE = "service";
  let screenTimer = null;
  let failing = false;
  let starting = null;
  let troubled = false;
  let skeleton = null;

  function copyLogPath() {
    return AW.request("bridge-status").then(async (status) => {
      const copied = status && status.log ? await AW.clipboard.write(status.log) : false;
      AW.ui.toast(copied ? "Copied." : "The log path could not be copied.", copied ? {} : { kind: "danger" });
    });
  }

  function pending(on) {
    if (on && !skeleton) {
      skeleton = el("section", { class: "view boot-pending" }, AW.ui.skeleton(3, "Loading Agent Workbench"));
      AW.$("view").insertBefore(skeleton, AW.$("boot-screen"));
    } else if (!on && skeleton) {
      skeleton.remove();
      skeleton = null;
    }
  }

  function failed(error) {
    failing = true;
    pending(false);
    AW.shell.busy(false);
    AW.shell.lock(true);
    AW.$("boot-screen").hidden = true;
    for (const view of AW.$("view").querySelectorAll(":scope > .view")) view.hidden = true;
    const showLog = AW.ui.button("Show log file", {
      onClick: () =>
        AW.request("bridge-log-open").then((shown) => {
          if (!shown) showLog.hidden = true;
        }),
    });
    const parts = [el("p", { class: "wrap", text: error.message })];
    if (error.detail) parts.push(el("details", null, el("summary", { text: "Details" }), el("pre", { text: error.detail })));
    AW.$("boot-failed-body").replaceChildren(
      ...parts,
      el(
        "div",
        { class: "actions" },
        AW.ui.button("Try again", { kind: "primary", pending: "Trying…", onClick: () => start() }),
        AW.ui.button("Copy log path", { onClick: copyLogPath }),
        showLog,
        AW.ui.button("Open Setup and Doctor", {
          onClick() {
            AW.ui.notify("danger", error.message, { id: "boot", actions: [{ label: "Try again", primary: true, run: () => start() }] });
            return AW.router.go("setup");
          },
        }),
      ),
    );
    AW.$("boot-failed").hidden = false;
    AW.$("boot-failed-title").focus();
    AW.ui.announce(error.message, { assertive: true });
  }

  function background() {
    for (const key of ["accounts", "sourceTable", "scan", "tools", "folders"]) AW.store.refresh(key).catch(() => {});
  }

  async function run() {
    const covered = !AW.$("boot-failed").hidden;
    AW.$("boot-failed").hidden = true;
    AW.ui.notify.clear("boot");
    AW.shell.busy(true);
    pending(true);
    clearTimeout(screenTimer);
    screenTimer = setTimeout(() => (AW.$("boot-screen").hidden = false), SCREEN_MS);
    const [state, progress] = await Promise.allSettled([
      AW.store.hydrate(),
      AW.store.refresh("progress"),
      AW.jobs.sync(),
      AW.request("bridge-status").then((status) => AW.store.set("bridge", { ...(AW.state.bridge || {}), ...status })),
    ]);
    clearTimeout(screenTimer);
    AW.$("boot-screen").hidden = true;
    if (state.status === "rejected") {
      failed(AW.asError(state.reason));
      return false;
    }
    failing = false;
    pending(false);
    AW.shell.busy(false);
    const open = progress.status === "fulfilled" && progress.value && progress.value.open === true;
    if (!AW.router.current().id || covered) await AW.router.go(open ? "guide" : "launch");
    AW.store.poll.start();
    for (const warning of state.value.warnings || []) AW.ui.notify("warn", warning);
    background();
    return true;
  }

  function start() {
    if (!starting)
      starting = run().finally(() => {
        starting = null;
      });
    return starting;
  }

  async function reload() {
    await Promise.allSettled([AW.store.refresh("config", { force: true }), AW.store.refresh("accounts", { force: true }), AW.jobs.sync()]);
  }

  function serviceNotice(data) {
    if (data.state === "restarting") {
      troubled = true;
      AW.ui.notify("warn", "The Workbench service stopped and is restarting. Embedded terminals were closed.", { id: SERVICE });
    } else if (data.state === "failed") {
      troubled = true;
      const detail = typeof data.detail === "string" ? data.detail : undefined;
      const lead = "The Workbench service keeps stopping.";
      const message = typeof data.message === "string" ? data.message.trim() : "";
      AW.ui.notify("danger", message.startsWith(lead) ? message : message ? lead + " " + message : lead, {
        id: SERVICE,
        detail,
        actions: [
          { label: "Try again", primary: true, run: () => AW.boot.retry() },
          { label: "Copy log path", run: copyLogPath },
        ],
      });
    } else if (data.state === "ready" && (troubled || Number(data.restarts) > 0)) {
      troubled = false;
      AW.ui.notify("ok", "The Workbench service is running again.", { id: SERVICE });
      reload();
    }
  }

  AW.boot = {
    retry() {
      if (failing || !AW.state.config) return start();
      return AW.store.refresh("config", { force: true }).then(
        () => {
          AW.ui.notify.clear(SERVICE);
          return AW.jobs.sync().then(() => true, () => true);
        },
        (thrown) => {
          AW.ui.toast(AW.asError(thrown).message, { kind: "danger" });
          return false;
        },
      );
    },
  };

  AW.events.on("bridge-state", (data) => {
    if (failing || !AW.state.config) {
      if (data.state === "ready" && failing) start();
      return;
    }
    serviceNotice(data);
  });

  AW.form.bind();
  AW.shell.init();
  start();
})();
