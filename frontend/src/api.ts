// 统一 API 层。开发（Vite 5173 代理）与生产（后端 8080 同源）都用相对 /api。
import { getLocale } from "./i18n/i18n";

const BASE = "/api";

// v1.3.6：打包态 Electron 经 preload 注入本次启动的随机本机 token；
// 后端（BAZZ_AUTH_TOKEN 已设置时）对 /api/* 校验 X-BAZZ-Token。无 token 时不带头，
// 后端未启用鉴权（dev 直跑）照常放行。
const AUTH_TOKEN: string = String((window as any)?.bazzAuth?.token || "");

export function authHeaders(): Record<string, string> {
  return AUTH_TOKEN ? { "X-BAZZ-Token": AUTH_TOKEN } : {};
}

async function jget(path: string) {
  const r = await fetch(BASE + path, { headers: authHeaders() });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function jpost(path: string, body: any) {
  const r = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function jdel(path: string) {
  const r = await fetch(BASE + path, { method: "DELETE", headers: authHeaders() });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export const api = {
  BASE,
  status: () => jget("/status"),
  market: () => jget("/market"),
  marketMonsters: (force = false) => jget("/market/monsters" + (force ? "?force=1" : "")),
  marketIgnition: (force = false) => jget("/market/ignition" + (force ? "?force=1" : "")),
  marketOverview: () => jget("/market/overview"),
  marketFutures: () => jget("/market/futures"),
  marketKlines: (symbol: string, interval = "1h", limit = 24, market = "spot") =>
    jget(`/market/klines?symbol=${encodeURIComponent(symbol)}&interval=${interval}&limit=${limit}&market=${market}`),
  marketOI: (symbols: string[]) =>
    jget(`/market/oi?symbols=${encodeURIComponent(symbols.join(","))}`),
  marketFNG: () => jget("/market/fng"),
  marketRadar: (force = false) => jget("/market/radar" + (force ? "?force=1" : "")),
  marketRadarTracks: () => jget("/market/radar/tracks"),
  marketLongshort: () => jget("/market/longshort"),
  marketLiquidations: (limit = 60, window = 300) =>
    jget(`/market/liquidations?limit=${limit}&window=${window}`),
  conversations: (includeArchived = false) =>
    jget("/conversations" + (includeArchived ? "?include_archived=1" : "")),
  rooms: () => jget("/rooms"),
  createRoom: (b: any) => jpost("/rooms", b),
  roomMembers: (rid: string, action: "add" | "remove", name: string) =>
    jpost(`/rooms/${encodeURIComponent(rid)}/members`, { action, name }),
  deleteRoom: (rid: string) => jdel("/rooms/" + encodeURIComponent(rid)),
  newConversation: () => jpost("/conversations", {}),
  getConversation: (id: string) => jget("/conversations/" + id),
  deleteConversation: (id: string) => jdel("/conversations/" + id),
  archiveConversation: (id: string, archived: boolean) =>
    jpost("/conversations/" + encodeURIComponent(id) + "/archive", { archived }),
  renameConversation: (id: string, title: string) =>
    jpost("/conversations/" + encodeURIComponent(id) + "/rename", { title }),
  searchConversations: (q: string) =>
    jget("/conversations/search?q=" + encodeURIComponent(q)),
  workspaceFiles: (path: string = "") =>
    jget("/workspace/files?path=" + encodeURIComponent(path)),
  workspaceRead: (path: string) =>
    jget("/workspace/read?path=" + encodeURIComponent(path)),
  // v1.5.24：图片原文（blob URL 预览用）——带鉴权头，非 JSON
  workspaceRawBlob: async (path: string): Promise<Blob> => {
    const r = await fetch(BASE + "/workspace/raw?path=" + encodeURIComponent(path), { headers: authHeaders() });
    if (!r.ok) throw new Error(await r.text());
    return r.blob();
  },
  workspaceDelete: (path: string) =>
    jdel("/workspace/file?path=" + encodeURIComponent(path)),
  approvalWhitelist: () => jget("/approvals/whitelist"),
  approvalWhitelistAdd: (rule: string) => jpost("/approvals/whitelist", { rule }),
  approvalWhitelistRemove: (rule: string) => jdel("/approvals/whitelist?rule=" + encodeURIComponent(rule)),
  uploadFile: async (file: File): Promise<any> => {
    // base64 走 JSON（与后端一致、无 multipart 依赖）
    const data = await new Promise<string>((res, rej) => {
      const fr = new FileReader();
      fr.onload = () => res(String(fr.result).split(",")[1] ?? "");
      fr.onerror = () => rej(fr.error);
      fr.readAsDataURL(file);
    });
    return jpost("/upload", { name: file.name, data });
  },
  settings: () => jget("/settings"),
  saveSettings: (b: any) => jpost("/settings", b),
  llmTest: (b: any) => jpost("/llm/test", b),
  llmModels: (provider: string, base_url: string, api_key: string) =>
    jget("/llm/models?provider=" + encodeURIComponent(provider) + "&base_url=" + encodeURIComponent(base_url) + "&api_key=" + encodeURIComponent(api_key)),
  memory: () => jget("/memory"),
  memoryStats: () => jget("/memory/stats"),
  addMemory: (key: string, value: string) => jpost("/memory", { key, value }),
  deleteMemory: (key: string) => jdel("/memory/" + encodeURIComponent(key)),
  exportMemory: async () => {
    const r = await fetch(BASE + "/memory/export", { headers: authHeaders() });
    if (!r.ok) throw new Error(await r.text());
    return r.text();
  },
  exportMemoryMd: async () => {
    const r = await fetch(BASE + "/memory/export?format=md", { headers: authHeaders() });
    if (!r.ok) throw new Error(await r.text());
    return r.text();
  },
  mcp: () => jget("/mcp"),
  addMcp: (b: any) => jpost("/mcp", b),
  deleteMcp: (name: string) => jdel("/mcp/" + encodeURIComponent(name)),
  toggleMcp: (name: string, enabled: boolean) => jpost(`/mcp/${encodeURIComponent(name)}/toggle`, { enabled }),
  mcpTools: (name: string) => jget(`/mcp/${encodeURIComponent(name)}/tools`),
  mcpOauthStart: (name: string) => jpost(`/mcp/${encodeURIComponent(name)}/oauth/start`, {}),
  mcpOauthPoll: (name: string, state: string, timeout?: number) =>
    jpost(`/mcp/${encodeURIComponent(name)}/oauth/poll`, { state, timeout: timeout ?? 120 }),
  mcpSetToken: (name: string, token: string) => jpost(`/mcp/${encodeURIComponent(name)}/token`, { token }),
  cron: () => jget("/cron"),
  addCron: (b: any) => jpost("/cron", b),
  deleteCron: (id: string) => jdel("/cron/" + encodeURIComponent(id)),
  toggleCron: (id: string, enabled: boolean) => jpost(`/cron/${encodeURIComponent(id)}/toggle`, { enabled }),
  cronRun: (id: string) => jpost(`/cron/${encodeURIComponent(id)}/run`, {}),
  plugins: () => jget("/plugins"),
  togglePlugin: (name: string, enabled: boolean) => jpost("/plugins", { name, enabled }),
  pluginCommand: (id: string, name: string, params?: any) =>
    jpost(`/plugins/${encodeURIComponent(id)}/command`, { name, params: params ?? {} }),
  wallet: (force = false) => jget("/wallet" + (force ? "?force=1" : "")),
  walletRun: (cmd: string) => jpost("/wallet/run", { cmd }),
  // v1.2.11：内置 runtime 状态（Node + @binance/agentic-wallet 是否就绪）
  walletRuntime: () => jget("/wallet/runtime"),
  walletInstall: () => jpost("/wallet/install", {}),
  // Agent 钱包：Binance App 扫码登录
  walletSignin: () => jpost("/wallet/signin", {}),
  walletVerify: (qrCodeId: string, wait?: number) => jpost("/wallet/verify", { qrCodeId, wait: wait ?? 10 }),
  walletSignout: () => jpost("/wallet/signout", {}),
  walletCampaign: () => jget("/wallet/campaign"),
  walletQr: async (text: string) => {
    const r = await fetch(BASE + "/wallet/qr?text=" + encodeURIComponent(text), { headers: authHeaders() });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  // 链上钱包（CEX，API Key + Secret）：只读资产
  cexStatus: () => jget("/wallet/cex/status"),
  cexConnect: (api_key: string, secret: string) => jpost("/wallet/cex/connect", { api_key, secret }),
  cexDisconnect: () => jpost("/wallet/cex/disconnect", {}),
  cexSummary: (force = false) => jget("/wallet/cex/summary" + (force ? "?force=1" : "")),
  cexOpenOrders: (symbol?: string) => jget("/wallet/cex/openorders" + (symbol ? "?symbol=" + encodeURIComponent(symbol) : "")),
  cexTrades: (symbol = "", limit = 50) => {
    const q = symbol ? `?symbol=${encodeURIComponent(symbol)}&limit=${limit}` : `?limit=${limit}`;
    return jget("/wallet/cex/trades" + q);
  },
  cexAllOrders: (symbol: string, limit = 50) => jget("/wallet/cex/allorders?symbol=" + encodeURIComponent(symbol) + "&limit=" + limit),
  // v1.4.0 订单跟踪（下单后状态流转 + SL/TP 提醒）
  ordersTrack: () => jget("/orders/track"),
  orderUntrack: (id: string) => jdel(`/orders/track?id=${encodeURIComponent(id)}`),
  // 链上钱包（Binance Web3 Wallet API，BX- Key）：官方连接器桥
  web3Status: () => jget("/wallet/web3/status"),
  web3Connect: (api_key: string, secret: string) => jpost("/wallet/web3/connect", { api_key, secret }),
  web3Disconnect: () => jpost("/wallet/web3/disconnect", {}),
  web3Balance: (address: string, chains: string[], pageSize = 50) =>
    jpost("/wallet/web3/balance", { address, chains, pageSize }),
  web3AgentAddresses: () => jget("/wallet/web3/agent-addresses"),
  x402Supported: () => jget("/x402/supported"),
  x402Demo: (asset: string, amount: string) => jpost("/x402/demo", { asset, amount }),
  skills: () => jget("/skills"),
  bots: () => jget("/bots"),
  botActivity: () => jget("/bots/activity"),
  addBot: (b: any) => jpost("/bots", b),
  updateBot: (id: string, b: any) => jpost("/bots/" + encodeURIComponent(id), b),
  deleteBot: (id: string) => jdel("/bots/" + encodeURIComponent(id)),
  importBot: (md: string) => jpost("/bots/import", { md }),
  exportBot: async (id: string) => {
    const r = await fetch(BASE + "/bots/" + encodeURIComponent(id) + "/export", { headers: authHeaders() });
    if (!r.ok) throw new Error(await r.text());
    return r.text();
  },
  skillsInstall: (key: string) => jpost("/skills/install", { key }),
  skillsInstallWallet: () => jpost("/skills/install-wallet-skills", {}),
  skillsRun: (key: string, args?: string) => jpost("/skills/run", { key, args: args ?? "" }),
  skillsRemove: (key: string) => jpost("/skills/remove", { key }),
  skillsUpdates: (refresh?: boolean) => jget(`/skills/updates${refresh ? "?refresh=1" : ""}`),
  skillsUpdate: (scope?: "baw" | "skills" | "all") => jpost("/skills/update", { scope: scope ?? "all" }),
  // 币安广场发文台账（Agent 已发帖子记录，只读展示）
  squarePosts: () => jget("/square/posts"),
  squareKey: () => jget("/square/key"),
  squareConnect: (api_key: string) => jpost("/square/connect", { api_key }),
  squareDisconnect: () => jpost("/square/disconnect", {}),
  getAutoExec: async () => {
    const r = await fetch(BASE + "/settings/auto-exec", { headers: authHeaders() });
    return r.ok ? (await r.json()).auto_exec : true;
  },
  setAutoExec: (on: boolean) => jpost("/settings/auto-exec", { auto_exec: on }),
  getDeepThinking: async () => {
    const r = await fetch(BASE + "/settings/deep-thinking", { headers: authHeaders() });
    return r.ok ? (await r.json()).deep_thinking : true;
  },
  setDeepThinking: (on: boolean) => jpost("/settings/deep-thinking", { deep_thinking: on }),
  getSettings: async () => {
    const r = await fetch(BASE + "/settings", { headers: authHeaders() });
    return r.ok ? await r.json() : {};
  },
  // 自动更新（检查 / 后台下载 / 应用）：v1.2.13 恢复应用内自动更新链路
  updateCheck: () => jget("/update/check"),
  updateDownload: (url = "") => jpost("/update/download", { url }),
  updateStatus: () => jget("/update/status"),
  updateApply: (zip: string, pid: number) => jpost("/update/apply", { zip, pid }),
  // 统一的外链打开：Electron 下走主进程 shell.openExternal（防被 WebView 拦截）；
  // 浏览器开发态下退化到 window.open。包外 URL 必须 https://，否则忽略。
  // v1.3.7 代理池 / mihomo 内核
  proxies: () => jget("/proxies"),
  proxyImport: (body: { text?: string; url?: string; group?: string }) => jpost("/proxies/import", body),
  proxyRefresh: () => jpost("/proxies/refresh", {}),
  proxyTest: (id?: string) => jpost("/proxies/test", { id: id || null }),
  proxyActive: (id: string) => jpost("/proxies/active", { id }),
  proxyDelete: (id: string) => jdel(`/proxies/${id}`),
  kernelStatus: () => jget("/proxies/kernel"),
  kernelDownload: () => jpost("/proxies/kernel/download", {}),
  kernelStart: () => jpost("/proxies/kernel/start", {}),
  kernelStop: () => jpost("/proxies/kernel/stop", {}),
  openExternal: (url: string) => {
    const u = String(url || "").trim();
    if (!/^https?:\/\//i.test(u)) return false;
    try {
      const w: any = window as any;
      if (w?.bazzWindow?.openUrl) { w.bazzWindow.openUrl(u); return true; }
      window.open(u, "_blank", "noopener,noreferrer");
      return true;
    } catch { return false; }
  },
};

// 流式对话：返回 reader，调用方逐行解析 NDJSON
export async function streamChat(body: any, signal?: AbortSignal): Promise<Response> {
  return fetch(BASE + "/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ ...body, locale: getLocale() }),
    signal,
  });
}
