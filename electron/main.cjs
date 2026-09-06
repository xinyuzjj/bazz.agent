// Electron 桌面壳（Hermes 同款）——BAZZ.AGENT
// 两种运行形态：
//   开发/仓库内  : electron .  → 拉起 .venv python desktop_app.py，加载后端托管页面
//   打包分发 exe : app.isPackaged → 拉起内嵌 ScoutBackend.exe（resources/scout-bundle），加载 http://127.0.0.1:8080
const { app, BrowserWindow, ipcMain } = require("electron");
const path = require("path");
const fs = require("fs");
const http = require("http");
const { spawn } = require("child_process");

const PACKAGED = app.isPackaged;
const ROOT = path.join(__dirname, "..");            // dev=项目根；packaged=resources/app（应用目录）
const FRONTEND = path.join(ROOT, "frontend");
const DIST = path.join(FRONTEND, "dist", "index.html");
const APP_TITLE = "BAZZ.AGENT";
const PORT = process.env.BAZZ_PORT || 8080;
const PROD_URL = `http://127.0.0.1:${PORT}`;
const DEV_URL = "http://127.0.0.1:5173";

// 图标：开发态用仓库 assets/；打包态用 extraResource 落盘的 resources/assets/（真实文件，不经过 asar）
const DEV_ICON_ICO = path.join(ROOT, "assets", "icon.ico");
const DEV_ICON_PNG = path.join(ROOT, "assets", "icon.png");
const PKG_ICON_ICO = path.join(process.resourcesPath, "assets", "icon.ico");
const PKG_ICON_PNG = path.join(process.resourcesPath, "assets", "icon.png");
const RES_ICON_ICO = PACKAGED ? PKG_ICON_ICO : DEV_ICON_ICO;
const RES_ICON_PNG = PACKAGED ? PKG_ICON_PNG : DEV_ICON_PNG;

let backendProc = null;
let mainWin = null;

// 来自 renderer preload 的窗口控制 IPC（frameless 模式下需要）
ipcMain.on("bazz:win-min", () => { if (mainWin && !mainWin.isDestroyed()) mainWin.minimize(); });
ipcMain.on("bazz:win-max-toggle", () => {
  if (!mainWin || mainWin.isDestroyed()) return;
  if (mainWin.isMaximized()) mainWin.unmaximize(); else mainWin.maximize();
});
ipcMain.on("bazz:win-close", () => { if (mainWin && !mainWin.isDestroyed()) mainWin.close(); });

// 若 8080 已被占用（同会话重复双击启动、或用户手滑开了两个），直接复用，不拉第二个后端。
function portBusy() {
  return new Promise((resolve) => {
    const req = http.get(PROD_URL, (r) => { r.resume(); resolve(true); });
    req.on("error", () => resolve(false));
    req.setTimeout(800, () => { req.destroy(); resolve(false); });
  });
}

async function startBackend() {
  if (await portBusy()) return;                     // 已有后端在跑
  let cmd, args, cwd;
  if (PACKAGED) {
    const exeDir = path.join(process.resourcesPath, "scout-bundle", "ScoutBackend");
    cmd = path.join(exeDir, "ScoutBackend.exe");
    args = [];
    cwd = exeDir;
  } else {
    const venv = process.platform === "win32"
      ? path.join(ROOT, ".venv", "Scripts", "python.exe")
      : path.join(ROOT, ".venv", "bin", "python");
    cmd = fs.existsSync(venv) ? venv : "python3";
    args = [path.join(ROOT, "desktop_app.py")];
    cwd = ROOT;
  }
  try {
    backendProc = spawn(cmd, args, { cwd, stdio: "ignore", windowsHide: true });
    backendProc.on("error", (e) => console.error("[backend] 启动失败:", e.message));
  } catch (e) {
    console.error("[backend] spawn 异常:", e.message);
  }
  process.on("exit", () => { if (backendProc) try { backendProc.kill(); } catch {} });
}

function waitServer(cb, tries = 100) {
  http.get(PROD_URL, (r) => { r.resume(); cb(); })
    .on("error", () => { if (tries > 0) setTimeout(() => waitServer(cb, tries - 1), 500); });
}

function createWindow() {
  const icon = fs.existsSync(RES_ICON_ICO) ? RES_ICON_ICO : (fs.existsSync(RES_ICON_PNG) ? RES_ICON_PNG : undefined);
  mainWin = new BrowserWindow({
    width: 1440, height: 900, minWidth: 1150, minHeight: 700,
    title: APP_TITLE, backgroundColor: "#0a0b0d", show: false,
    // 无边框：去掉 OS 标题栏，由应用自带顶栏接管拖动和窗口控制
    frame: false,
    autoHideMenuBar: true, icon,
    webPreferences: {
      contextIsolation: true, nodeIntegration: false, spellcheck: false,
      // 暴露窗口控制 IPC（最小特权）
      preload: path.join(__dirname, "preload.cjs"),
    },
  });
  mainWin.setMenuBarVisibility(false);
  mainWin.on("page-title-updated", (e) => e.preventDefault());   // 固定产品名，不被页面 title 覆盖
  mainWin.once("ready-to-show", () => mainWin.show());
  mainWin.on("maximize", () => mainWin.webContents.send("bazz:win-max-changed", true));
  mainWin.on("unmaximize", () => mainWin.webContents.send("bazz:win-max-changed", false));
  mainWin.webContents.on("did-fail-load", (_e, code, desc) => {
    console.error("[ui] 加载失败:", code, desc);
  });
  waitServer(() => {
    // 生产：后端托管构建产物（同域）；开发：dist 存在则走后端同域，否则走 Vite dev server
    if (PACKAGED) {
      mainWin.loadURL(PROD_URL);
    } else {
      mainWin.loadURL(fs.existsSync(DIST) ? PROD_URL : DEV_URL);
    }
  });
  return mainWin;
}

app.whenReady().then(() => {
  startBackend();
  createWindow();
  app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});

app.on("window-all-closed", () => {
  if (backendProc) try { backendProc.kill(); } catch {}
  if (process.platform !== "darwin") app.quit();
});
