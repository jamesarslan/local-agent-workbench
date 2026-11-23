const {
  app,
  BrowserWindow,
  clipboard,
  dialog,
  ipcMain,
  shell,
} = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const actions = require("./actions.json");
const { createClient, normalise } = require("./bridge-client.cjs");

const page = pathToFileURL(path.join(__dirname, "index.html")).href;
const root = path.dirname(__dirname);
// Same rule as runtime.DATA, so bridge.log sits beside the other state.
const data =
  process.env.WORKBENCH_DATA === undefined
    ? path.join(root, ".local")
    : path.resolve(root, process.env.WORKBENCH_DATA);
const keyPages = {
  OpenAI: "https://platform.openai.com/api-keys",
  Groq: "https://console.groq.com/keys",
  DeepSeek: "https://platform.deepseek.com/api_keys",
  OpenRouter: "https://openrouter.ai/settings/keys",
};
let win,
  quitting = false;
const client = createClient({
  actions,
  command:
    process.env.WORKBENCH_PYTHON ||
    (process.platform === "win32" ? "python" : "python3"),
  args: ["-u", path.join(root, "desktop_bridge.py")],
  cwd: root,
  env: { ...process.env, PYTHONUTF8: "1" },
  logPath: path.join(data, "bridge.log"),
  emit: (event, payload) => {
    if (win && !win.isDestroyed())
      win.webContents.send("workbench-event", event, payload);
  },
});
async function pickFile(title) {
  const result = await dialog.showOpenDialog(win, {
    properties: ["openFile"],
    filters: [{ name: "MCP configuration", extensions: ["json"] }],
    title,
  });
  return result.canceled ? null : result.filePaths[0];
}
const local = {
  "bridge-status": async () => client.status(),
  "job-list": async () => client.jobs(),
  "job-cancel": async (input) =>
    client.cancel(typeof input.handle === "string" ? input.handle : ""),
  "bridge-log-open": async () => {
    const log = path.join(data, "bridge.log");
    if (!fs.existsSync(log)) return false;
    shell.showItemInFolder(log);
    return true;
  },
  "folder-pick": async () => {
    const result = await dialog.showOpenDialog(win, {
      properties: ["openDirectory"],
      title: "Choose project folder",
    });
    if (result.canceled) return null;
    try {
      return fs.realpathSync.native(result.filePaths[0]);
    } catch {
      return result.filePaths[0];
    }
  },
  "server-copy-key": async () => {
    clipboard.writeText(await client.call("studio-api-key", {}));
    return true;
  },
  "account-key-page": async (input) => {
    const url = Object.hasOwn(keyPages, input.provider)
      ? keyPages[input.provider]
      : null;
    if (url) await shell.openExternal(url);
    return Boolean(url);
  },
  "plugin-import": async () => {
    const file = await pickFile("Register MCP servers from a trusted file");
    return file ? client.call("plugin-import", { file }) : [];
  },
  "plugin-update": async () => {
    const file = await pickFile(
      "Replace registered MCP servers from a trusted file",
    );
    return file ? client.call("plugin-update", { file }) : [];
  },
  "plugin-remove": async (input) => {
    const name = typeof input.name === "string" ? input.name : "";
    if (!/^[A-Za-z0-9_-]{1,48}$/.test(name))
      throw { message: "Choose a registered MCP server to remove." };
    const answer = await dialog.showMessageBox(win, {
      type: "warning",
      buttons: ["Remove", "Cancel"],
      defaultId: 1,
      cancelId: 1,
      title: "Remove MCP server",
      message: `Remove the MCP server "${name}" from Agent Workbench?`,
      detail:
        "It is removed from the registry on this computer, from your saved launch settings and folder settings, and from the agent configuration files Workbench generated. Files in your projects are not changed.",
    });
    return answer.response === 0
      ? client.call("plugin-remove", { name })
      : null;
  },
};
async function route(action, input) {
  const found = client.lookup(action);
  if (!found || found.spec.via === "internal")
    throw { message: "Unknown desktop action", code: "internal" };
  const payload =
    input && typeof input === "object" && !Array.isArray(input) ? input : {};
  return found.spec.via === "main"
    ? local[found.name](payload)
    : client.call(found.name, payload);
}
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on("second-instance", () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });
  app.whenReady().then(() => {
    ipcMain.handle("workbench", async (event, action, input = {}) => {
      if (event.senderFrame.url !== page || event.sender !== win.webContents)
        throw new Error("Unknown desktop view");
      try {
        return { ok: true, value: await route(action, input) };
      } catch (error) {
        return {
          ok: false,
          error: normalise(
            error instanceof Error
              ? { message: error.message, code: "internal" }
              : error,
          ),
        };
      }
    });
    client.start();
    win = new BrowserWindow({
      width: 1200,
      height: 810,
      minWidth: 790,
      minHeight: 590,
      backgroundColor: "#0c0c0e",
      title: "Agent Workbench",
      autoHideMenuBar: true,
      webPreferences: {
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
        // Per Electron's documentation the spell checker fetches dictionaries on Windows and Linux; macOS uses the system one.
        spellcheck: process.platform === "darwin",
        preload: path.join(__dirname, "preload.cjs"),
      },
    });
    win.webContents.on("will-navigate", (e) => e.preventDefault());
    win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    win.loadFile(path.join(__dirname, "index.html"));
  });
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", (event) => {
    if (quitting) return;
    quitting = true;
    event.preventDefault();
    client.stop().finally(() => app.quit());
  });
}
