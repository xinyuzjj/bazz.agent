// scripts/prepare-runtime.js
// ============================================================
// 准备 BAZZ.AGENT 内置运行时（v1.2.11 起）：
//   runtime/node/        —— Node 20 LTS Windows x64 运行时
//   runtime/node_modules/ —— npm i @binance/agentic-wallet（含 deps）
//   runtime/.stamp        —— 准备完成的标记（存在则跳过）
//   最终由 build-desktop.js 整个 runtime/ 目录打进 APP 的 resources/runtime/
// ============================================================
// 用法：
//   node scripts/prepare-runtime.js
//   PROJ=/path/to/proj node scripts/prepare-runtime.js
//   FORCE=1 node scripts/prepare-runtime.js   (强制重下)
//
// 环境变量：
//   PROJ       项目根（含 frontend/、electron/、build-desktop.js）。默认 = 本文件父目录的父目录
//   RUNTIME    runtime/ 输出根。默认 = <PROJ>/runtime
//   NODE_VER   Node 版本号。默认 v20.18.0（LTS Iron）
//   NODE_DIST  Node 下载基址。默认 https://nodejs.org/dist
//   NPM_REG    npm registry。默认 https://registry.npmjs.org
//   BAZZ_PROXY HTTP/HTTPS 代理（可选，给 Node 下载/npm install 走）。例如 http://127.0.0.1:7897
// ============================================================

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");
const https = require("https");
const http = require("http");

const PROJ = process.env.PROJ || path.resolve(__dirname, "..");
const RUNTIME = process.env.RUNTIME || path.join(PROJ, "runtime");
const NODE_VER = process.env.NODE_VER || "v20.18.0";
const NODE_DIST = process.env.NODE_DIST || "https://nodejs.org/dist";
const NPM_REG = process.env.NPM_REG || "https://registry.npmjs.org";
const BAZZ_PROXY = process.env.BAZZ_PROXY || "";
const FORCE = !!process.env.FORCE;

const STAMP = path.join(RUNTIME, ".stamp");

function log(m) { console.log("  • " + m); }
function fail(m) { console.error("✗ " + m); process.exit(1); }
function ok(m) { console.log("✓ " + m); }

function proxyAgent() {
  if (!BAZZ_PROXY) return null;
  // 简单代理分发：https 模块 + 显式 path
  return { host: BAZZ_PROXY.replace(/^https?:\/\//, "").split(":")[0],
           port: Number(BAZZ_PROXY.replace(/^https?:\/\//, "").split(":")[1] || 7890) };
}

function downloadTo(url, dest, redirectsLeft = 5) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const client = u.protocol === "http:" ? http : https;
    const opts = { headers: { "User-Agent": "BAZZ.AGENT-prepare-runtime" } };
    const proxy = proxyAgent();
    if (proxy) {
      opts.host = proxy.host;
      opts.port = proxy.port;
      opts.path = url;
      opts.headers.Host = u.host;
    }
    const req = client.get(opts, (res) => {
      if ([301, 302, 303, 307, 308].includes(res.statusCode)) {
        res.resume();
        if (!res.headers.location) return reject(new Error("redirect without location"));
        if (redirectsLeft <= 0) return reject(new Error("too many redirects"));
        const next = new URL(res.headers.location, url).toString();
        return downloadTo(next, dest, redirectsLeft - 1).then(resolve, reject);
      }
      if (res.statusCode !== 200) return reject(new Error("HTTP " + res.statusCode + " for " + url));
      const total = Number(res.headers["content-length"] || 0);
      const f = fs.createWriteStream(dest);
      let done = 0;
      res.on("data", (c) => { done += c.length; if (total) process.stdout.write(`\r  • 下载中 ${(done/1024/1024).toFixed(1)} / ${(total/1024/1024).toFixed(1)} MB`); });
      res.pipe(f);
      f.on("finish", () => { f.close(); if (total) process.stdout.write("\n"); resolve(dest); });
      f.on("error", reject);
    });
    req.on("error", reject);
    req.setTimeout(180000, () => req.destroy(new Error("download timeout")));
  });
}

function extractZip(zipPath, outDir) {
  // 优先用系统 tar（Windows 10+ 自带 tar.exe），失败回退 node:zlib + yauzl
  if (process.platform === "win32" && spawnSync("tar", ["--version"], { stdio: "ignore" }).status === 0) {
    const r = spawnSync("tar", ["-xf", zipPath, "-C", outDir], { stdio: "inherit" });
    if (r.status === 0) return;
    throw new Error("系统 tar 解压失败");
  }
  // 回退：用 node 内置的 unzip 不可行，改调用 PowerShell Expand-Archive
  if (process.platform === "win32") {
    const r = spawnSync("powershell", ["-NoProfile", "-Command", `Expand-Archive -Path "${zipPath}" -DestinationPath "${outDir}" -Force`], { stdio: "inherit" });
    if (r.status === 0) return;
    throw new Error("PowerShell 解压失败");
  }
  const r = spawnSync("unzip", ["-q", zipPath, "-d", outDir], { stdio: "inherit" });
  if (r.status !== 0) throw new Error("unzip 解压失败");
}

function rmrf(p) {
  if (!fs.existsSync(p)) return;
  spawnSync("cmd", ["/c", "rd", "/s", "/q", p], { stdio: "ignore" });
}

async function main() {
  console.log(`ℹ PROJ=${PROJ}  RUNTIME=${RUNTIME}  NODE_VER=${NODE_VER}`);
  if (!FORCE && fs.existsSync(STAMP)) { ok(`runtime 已就绪（stamp 存在，FORCE=1 重下）：${RUNTIME}`); return; }

  fs.mkdirSync(RUNTIME, { recursive: true });

  // 1) 下载 Node
  const nodeTmpZip = path.join(RUNTIME, `node-${NODE_VER}-win-x64.zip`);
  const nodeUrl = `${NODE_DIST}/${NODE_VER}/node-${NODE_VER}-win-x64.zip`;
  if (!fs.existsSync(nodeTmpZip) || FORCE) {
    log(`下载 Node ${NODE_VER} Windows x64（~30MB）：${nodeUrl}`);
    await downloadTo(nodeUrl, nodeTmpZip);
  } else {
    log(`Node zip 已存在：${nodeTmpZip}`);
  }

  // 2) 解压到 runtime/_node_tmp/，再把内部 node-vX.Y.Z-win-x64/ 内容搬到 runtime/node/
  const tmpExtract = path.join(RUNTIME, "_node_tmp");
  rmrf(tmpExtract); fs.mkdirSync(tmpExtract, { recursive: true });
  log("解压 Node zip…");
  extractZip(nodeTmpZip, tmpExtract);
  const inner = path.join(tmpExtract, `node-${NODE_VER}-win-x64`);
  if (!fs.existsSync(path.join(inner, "node.exe"))) fail("解压后未找到 node.exe：" + inner);
  const nodeDir = path.join(RUNTIME, "node");
  rmrf(nodeDir);
  fs.renameSync(inner, nodeDir);
  rmrf(tmpExtract);
  // 清理 zip
  try { fs.unlinkSync(nodeTmpZip); } catch {}
  ok(`Node 运行时就绪：${path.join(nodeDir, "node.exe")}`);

  // 3) npm install @binance/agentic-wallet 到 runtime/node_modules
  const nodeExe = path.join(nodeDir, process.platform === "win32" ? "node.exe" : "bin/node");
  const npmCli = path.join(nodeDir, "node_modules", "npm", "bin", "npm-cli.js");
  if (!fs.existsSync(nodeExe)) fail("找不到 node.exe：" + nodeExe);
  if (!fs.existsSync(npmCli)) fail("找不到 npm-cli.js：" + npmCli + "（Node zip 可能不完整）");

  const env = { ...process.env, npm_config_prefix: RUNTIME, npm_config_registry: NPM_REG };
  if (BAZZ_PROXY) {
    env.HTTPS_PROXY = BAZZ_PROXY; env.HTTP_PROXY = BAZZ_PROXY; env.NPM_CONFIG_PROXY = BAZZ_PROXY;
    env.HTTPS_PROXY_LOWER = BAZZ_PROXY; env.HTTP_PROXY_LOWER = BAZZ_PROXY;
  }
  log("npm install @binance/agentic-wallet （~70MB，可能需要 1-2 分钟）…");
  const r = spawnSync(nodeExe, [npmCli, "install", "--no-audit", "--no-fund", "--loglevel=error", "@binance/agentic-wallet"], {
    cwd: RUNTIME, stdio: "inherit", env,
  });
  if (r.status !== 0) fail("npm install 失败");

  // 4) 验证 + 写 stamp
  const bawPkg = path.join(RUNTIME, "node_modules", "@binance", "agentic-wallet", "package.json");
  if (!fs.existsSync(bawPkg)) fail("安装后未找到 @binance/agentic-wallet：" + bawPkg);
  ok(`baw CLI 就绪：${bawPkg}`);

  fs.writeFileSync(STAMP, `node=${NODE_VER} npm-reg=${NPM_REG} ts=${new Date().toISOString()}\n`);
  // 打印 runtime 体积
  let total = 0;
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p); else { try { total += fs.statSync(p).size; } catch {} }
    }
  };
  walk(RUNTIME);
  ok(`runtime 准备完成：${(total / 1024 / 1024).toFixed(0)} MB @ ${RUNTIME}`);
}

main().catch((e) => { console.error("✗ " + (e && e.message ? e.message : e)); process.exit(1); });
