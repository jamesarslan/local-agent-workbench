// The page globals tests/desktop-smoke.cjs drives the app through, and nothing else (ui.md 9.7).
(function () {
  "use strict";
  const AW = window.AW;
  window.request = (action, data) => AW.request(action, data);
  window.form = () => AW.form.get();
  window.termData = () => {
    const current = AW.terminal.current();
    return { workspace: current.workspace, id: current.id };
  };
  window.projectChanged = () => AW.project.select(AW.$("workspace").value.trim());
  Object.defineProperty(window, "terminal", { configurable: true, get: () => AW.terminal.current().xterm });
  Object.defineProperty(window, "terminalID", { configurable: true, get: () => AW.terminal.current().id });
})();
