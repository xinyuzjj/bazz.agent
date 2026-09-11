// BAZZ.AGENT 桌面壳的 preload —— 仅暴露窗口控制 IPC + 本机鉴权 token
const { contextBridge, ipcRenderer } = require("electron");

// v1.3.6：主进程经 additionalArguments 注入本次启动的随机 token（打包态后端 /api/* 必带）。
// 优先取 argv，兜底读环境变量；都没有（异常形态）则空串 → 请求不带头，由后端自行决定是否放行。
const _authArg = (process.argv || []).find((a) => typeof a === "string" && a.startsWith("--bazz-auth="));
const AUTH_TOKEN = _authArg ? _authArg.slice("--bazz-auth=".length) : (process.env.BAZZ_AUTH_TOKEN || "");

contextBridge.exposeInMainWorld("bazzAuth", { token: AUTH_TOKEN });

contextBridge.exposeInMainWorld("bazzWindow", {
  minimize: () => ipcRenderer.send("bazz:win-min"),
  toggleMaximize: () => ipcRenderer.send("bazz:win-max-toggle"),
  close: () => ipcRenderer.send("bazz:win-close"),
  getPid: () => ipcRenderer.invoke("bazz:app-pid"),
  // v1.2.11：统一通过主进程用系统默认浏览器打开外链（设置里的 GitHub 下载页、release 页等）
  openUrl: (url) => ipcRenderer.send("bazz:open-url", url),
  // v1.5.27：点 X 的「托盘 / 退出」询问改由应用内美化弹窗承担——
  // 主进程发 ask-close，渲染层弹窗后把选择回传 answer-close
  onAskClose: (cb) => {
    const handler = () => { try { cb(); } catch {} };
    ipcRenderer.on("bazz:ask-close", handler);
    return () => ipcRenderer.removeListener("bazz:ask-close", handler);
  },
  answerClose: (payload) => ipcRenderer.send("bazz:answer-close", payload),
});