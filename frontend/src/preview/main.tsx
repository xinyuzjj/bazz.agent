import React from "react";
import ReactDOM from "react-dom/client";
import App from "../App";
import { installPreview } from "./mock";
import "../index.css";

// Only this preview entry installs fixtures; the production entry does not import them.
if ((window as any).bazzWindow || (window as any).bazzAuth) {
  throw new Error("隔离预览不能在真实桌面桥中运行，请在独立浏览器打开。");
}
installPreview();
try {
  if (!sessionStorage.getItem("bazz.preview.initialized")) {
    localStorage.setItem("bazz.theme", "light");
    localStorage.setItem("bazz.locale", "zh");
    sessionStorage.setItem("bazz.preview.initialized", "1");
  }
} catch { /* Preview remains usable without browser storage. */ }
const notice = () => {
  const banner = document.querySelector(".ui-preview-banner");
  if (banner) banner.textContent = "界面预览：外链、文件选择、录音与系统操作已隔离，不会执行。";
};
window.open = () => { notice(); return null; };
window.XMLHttpRequest = class extends EventTarget {
  open() { throw new Error("预览禁止真实网络请求"); }
} as any;
if (typeof navigator.sendBeacon === "function") navigator.sendBeacon = () => false;
document.addEventListener("click", e => {
  const target = e.target as Element;
  const anchor = target.closest("a[href]");
  const input = target.closest("input[type=file]");
  const button = target.closest("button");
  if (anchor || input || /语音|录音|voice|microphone/i.test(button?.getAttribute("title") || "")) {
    e.preventDefault(); e.stopImmediatePropagation(); notice();
  }
}, true);
ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
