// Header: the view name, the launch target line and the quick launch button (ui.md 2.3, 9.7).
(function () {
  "use strict";
  const AW = (window.AW = window.AW || {});
  const wide = window.matchMedia("(min-width: 900px)");
  AW.launch = AW.launch || {};

  function modelLabel(form) {
    const config = AW.state.config || {};
    const provider = AW.form.provider();
    if (provider === "Native login") return "Agent account";
    if (provider === "Managed local") {
      const folder = AW.state.folder;
      if (form.preset) return form.preset;
      if (folder && folder.model_label && !AW.form.dirty()) return folder.model_label;
      return AW.fmt.folder(config.current && config.current.model) || "Local model";
    }
    const connection = AW.form.connection();
    return form.remote_model || (connection && connection.name) || provider;
  }

  const MODEL_MAX = 28;
  const MODEL_MIN = 8;
  function target(max = MODEL_MAX) {
    const form = AW.form.get();
    const live = AW.state.live;
    const loads = AW.form.provider() === "Managed local" && live && live.label !== "Serving";
    const where = AW.form.where() === "window" ? "New window" : "Terminal dock";
    const parts = [form.harness, modelLabel(form), where].filter(Boolean);
    return {
      short: [form.harness, AW.fmt.middle(modelLabel(form), max), where].filter(Boolean).join(" · ") + (loads ? " · loads the model first" : ""),
      full: parts.join(" · ") + (loads ? " · loads the model first" : ""),
    };
  }

  function fit(line) {
    for (let max = MODEL_MAX - 4; max >= MODEL_MIN && line.scrollWidth > line.clientWidth; max -= 4) line.textContent = target(max).short;
  }

  function paint() {
    const line = AW.$("launch-target");
    const button = AW.$("launch-quick");
    const route = AW.router.current();
    const view = AW.router.views().find((item) => item.id === route.id);
    const off = !AW.state.config || route.id === "launch" || Boolean(view && view.takeover);
    line.hidden = off;
    button.hidden = off;
    if (off) return;
    const form = AW.form.get();
    const ready = AW.readiness.current();
    const folder = AW.project.name(form.workspace);
    line.classList.remove("wide-only");
    line.removeAttribute("title");
    button.removeAttribute("title");
    if (!form.workspace) {
      line.textContent = "No project folder";
      button.textContent = "Launch…";
    } else if (!ready.ok) {
      line.textContent = ready.needs;
      line.title = ready.needs;
      button.textContent = "Launch…";
    } else {
      const text = target();
      line.textContent = text.short;
      line.title = text.full;
      line.classList.add("wide-only");
      fit(line);
      button.textContent = wide.matches ? "Launch in " + folder : "Launch…";
      button.title = text.full + " · " + AW.fmt.path(form.workspace);
    }
  }

  // The palette's quick launch title follows #launch-quick: it names the target only when Launch is ready.
  AW.launch.quickTitle = function (folder, ready, full) {
    if (ready && ready.ok) return "Launch in " + (folder || "a project folder") + " · " + full;
    const needs = String((ready && ready.needs) || "")
      .replace(/^Launch needs:\s*/, "")
      .replace(/\.$/, "");
    return "Launch… · " + (needs ? "needs " + needs : "choose a project folder");
  };

  AW.launch.quick = async function (origin) {
    const form = AW.form.get();
    const ready = AW.readiness.current();
    if (!form.workspace) {
      await AW.router.go("launch", { focus: "launch-project" });
      return AW.fix.run("project.pick", null, { origin });
    }
    if (!ready.ok) {
      await AW.router.go("launch", { focus: "launch" });
      const fix = AW.$("launch-readiness").querySelector('.ready-item[data-level="block"] input, .ready-item[data-level="block"] button, .ready-item[data-level="work"] button');
      if (fix) fix.focus();
      return false;
    }
    if (!wide.matches) return AW.router.go("launch", { focus: "launch" });
    return AW.launch.start({ origin });
  };

  AW.header = {
    step(text) {
      const node = AW.$("crumb-step");
      node.hidden = !text;
      node.textContent = text || "";
    },
    refresh: paint,
  };

  AW.shell.register(() => {
    AW.$("command-key").textContent = AW.platform.mod + " K";
    AW.$("launch-quick").addEventListener("click", (event) => {
      const button = event.currentTarget;
      if (button.getAttribute("aria-disabled") === "true") return;
      AW.launch.quick(button).catch((thrown) => AW.ui.toast(AW.asError(thrown).message, { kind: "danger" }));
    });
    AW.router.on(({ takeover }) => {
      if (!takeover) AW.header.step(null);
      paint();
    });
    AW.store.on(["config", "live", "folder", "accounts"], paint);
    AW.form.on(paint);
    AW.readiness.on(paint);
    wide.addEventListener("change", paint);
    window.addEventListener("resize", AW.debounce(paint, 120));
    paint();
  });

  AW.keys.register({ combo: "mod+l", inTerminal: "mac", run: () => AW.router.go("launch") });
  AW.keys.register({
    combo: "mod+enter",
    inTerminal: "mac",
    run() {
      if (AW.router.current().id === "launch") {
        AW.$("launch").click();
        return true;
      }
      return AW.launch.quick(AW.$("launch-quick"));
    },
  });
  AW.commands.register({
    id: "launch.quick",
    group: "Actions",
    keywords: "start run agent",
    title: () => AW.launch.quickTitle(AW.project.name(AW.form.get().workspace), AW.readiness.current(), target().full),
    run: () => AW.launch.quick(AW.$("launch-quick")),
  });
})();
