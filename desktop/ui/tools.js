// Tool setup (AW.tools): install, update, sign in to or open the guide for a tool through studio-setup as a task, and recheck what is
// installed (ui.md 5.6, 9.8). A failed agent install leaves its sentence in AW.state.outcomes, which readiness shows on the agent row.
// Beyond ui.md 9.8: AW.tools.verbs(name, installed) lists the verbs that apply to a tool, AW.tools.AGENTS the agents in harness order.
(function () {
  "use strict";
  const AW = window.AW;
  const AGENTS = ["OMP", "Pi", "Claude Code", "Codex", "Hermes", "opencode"];
  const SIGN_IN = new Set(["GitHub CLI", "AWS CLI", "Hugging Face CLI", "Codex", "opencode", "Claude Code", "Pi", "OMP", "Docker"]);
  const TITLES = { Install: "Installing ", Update: "Updating ", Login: "Opening sign-in for ", Docs: "Opening the guide for " };

  function remember(name, result) {
    if (!AGENTS.includes(name)) return;
    const outcomes = { ...(AW.state.outcomes || {}) };
    if (result && result.outcome === "failed" && typeof result.message === "string" && result.message) outcomes[name] = result.message;
    else if (!Object.hasOwn(outcomes, name)) return;
    else delete outcomes[name];
    AW.store.set("outcomes", outcomes);
  }

  function install(name, { origin, verb = "Install" } = {}) {
    const changes = verb === "Install" || verb === "Update";
    const key = changes ? "tool:" + name : "tool:" + name + ":" + verb;
    return AW.jobs.run("studio-setup", { name, verb }, { title: (TITLES[verb] || "Setting up ") + name, origin, key }).then((result) => {
      const value = result && typeof result === "object" ? result : { outcome: "failed", message: "Agent Workbench did not report what happened. Choose Recheck installations." };
      if (value.checks && typeof value.checks === "object") AW.store.set("tools", value.checks);
      if (changes) remember(name, value);
      if (value.outcome === "installed") AW.store.refresh("config").catch(() => {});
      return value;
    });
  }

  async function recheck() {
    const checks = await AW.store.refresh("tools", { force: true });
    const outcomes = { ...(AW.state.outcomes || {}) };
    let cleared = false;
    for (const name of Object.keys(outcomes))
      if (checks && checks[name]) {
        delete outcomes[name];
        cleared = true;
      }
    if (cleared) AW.store.set("outcomes", outcomes);
    if (AW.state.config) AW.store.refresh("config").catch(() => {});
    return checks;
  }

  function verbs(name, installed) {
    if (!installed) return ["Install", "Docs"];
    return SIGN_IN.has(name) ? ["Update", "Login", "Docs"] : ["Update", "Docs"];
  }

  function said(result) {
    const kind = result.outcome === "failed" ? "danger" : result.outcome === "installed" ? "ok" : "neutral";
    const text = [result.message, result.hint].filter((part) => typeof part === "string" && part).join(" ");
    const action =
      result.outcome === "failed" ? { label: "Open setup log", run: () => AW.request("studio-setup-log") }
      : result.outcome === "opened-docs" || result.outcome === "started-terminal" ? { label: "Recheck installations", run: () => recheck() }
      : undefined;
    if (text) AW.ui.toast(text, { kind, action, timeout: action ? 10000 : 6000 });
  }

  AW.tools = { install, recheck, verbs, AGENTS };

  AW.fix.register("tool.install", (name, ctx) => install(String(name || ""), { origin: ctx && ctx.origin }).then((result) => (said(result), result)));
  AW.fix.register("tool.recheck", () =>
    recheck().then((checks) => {
      AW.ui.announce("Checked the installed tools.");
      return checks;
    }),
  );
  AW.commands.register({
    id: "tools.recheck",
    title: "Recheck installed tools",
    group: "Actions",
    keywords: "installations setup agents cli",
    run: () => recheck().then(() => AW.ui.toast("Checked the installed tools.", { kind: "ok" })),
  });
  AW.commands.register({ id: "tools.log", title: "Open setup log", group: "Actions", keywords: "install log setup", run: () => AW.request("studio-setup-log") });
})();
