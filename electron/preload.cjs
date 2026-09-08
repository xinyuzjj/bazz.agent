// BAZZ.AGENT 桌面壳的 preload —— 仅暴露窗口控制 IPC
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("bazzWindow", {
  minimize: () => ipcRenderer.send("bazz:win-min"),
  toggleMaximize: () => ipcRenderer.send("bazz:win-max-toggle"),
  close: () => ipcRenderer.send("bazz:win-close"),
  getPid: () => ipcRenderer.invoke("bazz:app-pid"),
});