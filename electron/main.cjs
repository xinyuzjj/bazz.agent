// Electron 桌面壳（Hermes 同款）——BAZZ.AGENT
// 两种运行形态：
//   开发/仓库内  : electron .  → 拉起 .venv python desktop_app.py，加载后端托管页面
//   打包分发 exe : app.isPackaged → 拉起内嵌 ScoutBackend.exe（resources/scout-bundle），加载 http://127.0.0.1:8080
const { app, BrowserWindow, ipcMain, shell } = require("electron");
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
let splashWin = null;                 // 启动动画窗（splash），主界面就绪后淡出销毁
const SPLASH_SWAP_MS = 280;           // 动画窗淡出时长
// splash 启动时间锚点；保底展示 = max(SPLASH_MIN_MS, 后端就绪时间)，
// 否则后端拉得太快时动画会被 cut 掉看不到全貌
let splashStartAt = 0;
const SPLASH_MIN_MS = 5200;           // 至少播完 splash 主时序（约 4.8s）+ 短暂留白
const SPLASH_FAILSAFE_MS = 60 * 1000; // 兜底：后端异常迟迟不就绪时也不让动画窗永远挂着

// 来自 renderer preload 的窗口控制 IPC（frameless 模式下需要）
ipcMain.on("bazz:win-min", () => { if (mainWin && !mainWin.isDestroyed()) mainWin.minimize(); });
ipcMain.on("bazz:win-max-toggle", () => {
  if (!mainWin || mainWin.isDestroyed()) return;
  if (mainWin.isMaximized()) mainWin.unmaximize(); else mainWin.maximize();
});
ipcMain.on("bazz:win-close", () => { if (mainWin && !mainWin.isDestroyed()) mainWin.close(); });

// v1.2.11：统一通过主进程用系统默认浏览器打开外链（设置里点「打开下载页」等）
//   只放行 http(s)，避免渲染层误传 file:// / javascript: 等触发任意协议
ipcMain.on("bazz:open-url", (_e, url) => {
  try {
    if (typeof url === "string" && /^(https?:\/\/)/i.test(url)) shell.openExternal(url);
  } catch {}
});

// 兼容保留：v1.2.11 已不再用「传 pid 给更新脚本」流程，但 bazzWindow.getPid() 在前端代码里
// 仍可能被探针/旧逻辑调用，保留以免主进程抛 ipcMain.handle('invoke') 找不到
ipcMain.handle("bazz:app-pid", () => process.pid);

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

// 启动动画窗：与主窗同尺寸同背景色（#0a0b0d），无边框不抢任务栏焦点，盖住后端拉起期间的黑屏
function createSplash() {
  if (splashWin && !splashWin.isDestroyed()) return;
  splashWin = new BrowserWindow({
    width: 1440, height: 900,
    frame: false, resizable: false,
    backgroundColor: "#0a0b0d",
    show: false,
    autoHideMenuBar: true,
    webPreferences: { contextIsolation: true, nodeIntegration: false, spellcheck: false },
  });
  splashWin.setMenuBarVisibility(false);
  splashWin.loadFile(path.join(__dirname, "splash.html"));
  splashWin.once("ready-to-show", () => {
    if (splashWin && !splashWin.isDestroyed()) {
      splashWin.show();
      splashStartAt = Date.now();   // 标记"用户实际看到动画"的瞬间，保底时长从这里算
    }
  });
  splashWin.on("closed", () => { splashWin = null; });
}

// 主窗首帧就绪后：先显示主窗（盖住桌面），动画窗置顶淡出销毁，实现无闪衔接
// 若主窗 ready 过早（后端拉得快），先等到 SPLASH_MIN_MS 再切换，保证动画完整播放
function swapToMain() {
  if (splashStartAt) {
    const elapsed = Date.now() - splashStartAt;
    const remain = SPLASH_MIN_MS - elapsed;
    if (remain > 0 && mainWin && !mainWin.isDestroyed()) {
      setTimeout(swapToMain, remain);
      return;
    }
    splashStartAt = 0;                 // 标记已"放行"，避免 setTimeout 期间被再次触发
  }
  if (mainWin && !mainWin.isDestroyed()) mainWin.show();
  if (splashWin && !splashWin.isDestroyed()) {
    try { splashWin.moveTop(); } catch {}
    let op = 1;
    const iv = setInterval(() => {
      op -= 0.13;
      if (!splashWin || splashWin.isDestroyed()) { clearInterval(iv); return; }
      if (op <= 0) {
        clearInterval(iv);
        splashWin.destroy();
        splashWin = null;
      } else {
        try { splashWin.setOpacity(op); } catch { clearInterval(iv); splashWin.destroy(); splashWin = null; }
      }
    }, Math.max(16, Math.round(SPLASH_SWAP_MS / 8)));
  }
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
  mainWin.once("ready-to-show", swapToMain);
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
  createSplash();                       // 先上启动动画，盖住后端拉起期间的黑屏
  startBackend();
  createWindow();
  // 兜底：后端/页面异常迟迟不就绪时，动画窗不永久悬挂
  setTimeout(() => {
    if (splashWin && !splashWin.isDestroyed()) {
      if (mainWin && !mainWin.isDestroyed()) { splashWin.destroy(); mainWin.show(); }
      else splashWin.destroy();
      splashWin = null;
    }
  }, SPLASH_FAILSAFE_MS);
  app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) { createSplash(); createWindow(); } });
});

app.on("window-all-closed", () => {
  if (backendProc) try { backendProc.kill(); } catch {}
  if (process.platform !== "darwin") app.quit();
});
