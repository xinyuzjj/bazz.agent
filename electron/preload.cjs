// BAZZ.AGENT 桌面壳的 preload —— 仅暴露窗口控制 IPC
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("bazzWindow", {
  minimize: () => ipcRenderer.send("bazz:win-min"),
  toggleMaximize: () => ipcRenderer.send("bazz:win-max-toggle"),
  close: () => ipcRenderer.send("bazz:win-close"),
  getPid: () => ipcRenderer.invoke("bazz:app-pid"),
  // v1.2.11：统一通过主进程用系统默认浏览器打开外链（设置里的 GitHub 下载页、release 页等）
  openUrl: (url) => ipcRenderer.send("bazz:open-url", url),
});