const { contextBridge, ipcRenderer } = require("electron");
const listeners = new Set();
ipcRenderer.on("workbench-event", (_event, name, data) => {
  for (const listener of [...listeners]) {
    try {
      listener(name, data);
    } catch {}
  }
});
contextBridge.exposeInMainWorld("workbench", {
  filePath: (file) => require("electron").webUtils.getPathForFile(file),
  request: async (name, data) => {
    const reply = await ipcRenderer.invoke("workbench", name, data);
    if (reply && reply.ok === true) return reply.value;
    throw (reply && reply.error) || { message: "Workbench service error." };
  },
  subscribe: (listener) => {
    if (typeof listener !== "function")
      throw new TypeError("subscribe needs a function");
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
});
