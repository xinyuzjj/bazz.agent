// 独立浏览器预览专用：在挂载 App / 首次请求之前调用 installPreview()。
// 不导入 api、不保存原 fetch / WebSocket，不存在任何网络透传或持久化。
// 本文件不是浏览器沙箱：外链、资源加载、文件选择器和 Electron IPC 不属于 fetch。
// 主入口必须隔离真实桌面桥与外部资源；不要把此模块接入生产入口。

const DEMO = "演示 fixture · 非真实市场/账户数据";
const FIXTURE_MS = Date.UTC(2026, 8, 1, 8);
const FIXTURE_SEC = FIXTURE_MS / 1000;
const META = { preview: true, source: DEMO, fixture_at: FIXTURE_MS, network: "offline" };
const MODEL = "preview-demo（演示，非真实模型）";
const REPLY = "**演示回复 · 离线预览**\n\n当前内容来自固定 fixture，不是模型分析，也不是真实行情或账户数据。\n\n可以浏览八个页面、切换行情图表、删除演示记忆及失败帖子。不会调用任何 API、交易、钱包签名、技能安装或文件操作。";
const INSTALL_KEY = Symbol.for("bazz.preview.mock.installed");

type MemoryRow = { key: string; value: string; kind: string; source: string; hits: number; updated_at: number };
type Message = { role: string; content: string; persona?: string; model?: string; tools?: unknown[] };
type Conversation = {
  id: string; title: string; persona: string; kind: string; archived: boolean;
  created_at: number; updated_at: number; messages: Message[];
};
type Post = {
  id: string; ts: number; kind: string; title: string; text: string; tags: string[];
  status: "posted" | "failed"; error?: string; via: "agent" | "manual";
};

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", "X-BAZZ-Preview": "offline-fixture" },
  });
}

function text(data: string, contentType = "text/plain; charset=utf-8"): Response {
  return new Response(data, { headers: { "Content-Type": contentType, "Cache-Control": "no-store", "X-BAZZ-Preview": "offline-fixture" } });
}

function denied(): Response {
  const detail = "演示模式禁止此操作：不会访问真实 API、账户、钱包、文件或执行交易/安装。";
  return json({ ...META, ok: false, status: "error", code: "PREVIEW_FORBIDDEN", error: detail, message: detail, detail }, 403);
}

function invalid(): Response {
  return json({ ...META, ok: false, status: "error", code: "PREVIEW_INVALID_INPUT", error: "演示请求格式无效。" }, 400);
}

function abortError(): DOMException {
  return new DOMException("演示请求已取消。", "AbortError");
}

// 符号保留现有 Hero 卡的精确匹配键；所有数值均为人为设计的虚构 fixture。
const tickers = [
  ["BTCUSDT", 123.45, 3.2], ["ETHUSDT", 67.89, -1.8],
  ["BNBUSDT", 23.45, 2.4], ["SOLUSDT", 8.76, 5.6],
  ["DEMOAUSDT", 4.2, 9.4], ["DEMOBUSDT", 0.64, -7.2],
  ["DEMOCUSDT", 12.34, 0], ["DEMODUSDT", 1.23, -3.6],
].map(([symbol, price, change], i) => {
  const p = Number(price);
  const volume = 8000 + i * 2400;
  return { ...META, symbol: String(symbol), name: `${symbol}（演示）`, price: p,
    change_pct: Number(change), volume, quote_volume: Number((p * volume).toFixed(2)),
    high: Number((p * 1.12).toFixed(6)), low: Number((p * 0.88).toFixed(6)) };
});
const futures = tickers.map((r, i) => ({ ...r, funding_rate: i % 2 ? -0.0012 : 0.0015 }));
const equity = tickers.slice(0, 2).map((r, i) => ({ ...r, symbol: `DEMOEQ${i + 1}USDT`, name: `演示股票化代币 ${i + 1}（虚构）`, leverage: "仅演示", funding_rate: 0.0001 }));
const signals = tickers.slice(4, 6).map((r, i) => ({ ...r, direction: "WATCH", score: 70 + i * 6, reason: "演示异动样本，不是交易信号", funding_rate: 0 }));
const radar = tickers.slice(4, 7).map((r, i) => ({ ...r, side: "WATCH", tag: "演示观察", note: DEMO, score: 70 + i * 4,
  vol_ratio: 1.8 + i * 0.4, stage: i === 2 ? "VERTICAL" : "IGNITION", stage_label: "演示阶段",
  change7d_pct: 5 + i, change30d_pct: 12 + i, change3d_pct: 2 + i, change24_pct: r.change_pct,
  position_pct: 30 + i * 10, drawdown_pct: -20, floor_rising: true, change1h_pct: 0.8, rvol15: 2.1,
  amp24: 12, cooldown: false, listed_days: 120, factors: { flow: 1.2, jump: 0.6, oi_chg24: 3, funding: 0.0001 },
  reasons: ["固定 fixture，仅用于组件排版", "没有行情采集或实际信号计算"] }));

const agents = [
  ["demo-scout", "演示侦察员", "行情观察", "S", "#F0B90B"],
  ["demo-guard", "演示守卫", "只读风控", "G", "#2EBD85"],
  ["demo-editor", "演示编辑", "广场草稿", "E", "#7B8CDE"],
  ["demo-recall", "演示记忆员", "偏好整理", "M", "#D7955B"],
].map(([id, name, title, avatar, color]) => ({ ...META, id, name, title, avatar, color,
  description: "演示 Agent，仅返回固定文本，不运行工具。",
  config: { model: MODEL, tone: "简洁", skills: [], tools: [], prompt: "仅展示离线演示回复。" } }));
const skills = [
  ["market-data", "演示行情资料", "builtin"], ["coin-report", "演示观察报告", "builtin"],
  ["risk-guard", "演示风险检查", "builtin"], ["portfolio-review", "演示资产复盘", "builtin"],
  ["binance-agentic-wallet", "演示 Agent 钱包", "binance-web3"],
  ["query-token-info", "演示代币资料", "binance-web3"], ["query-token-audit", "演示审计卡", "binance-web3"],
  ["query-address-info", "演示地址资料", "binance-web3"], ["crypto-market-rank", "演示排行榜", "binance-web3"],
  ["meme-rush", "演示新币观察", "binance-web3"], ["trading-signal", "演示观察信号", "binance-web3"],
  ["binance-tokenized-securities-info", "演示代币化资产", "binance-web3"],
  ["square-post", "演示广场发布", "binance"], ["binance", "演示交易终端（禁止执行）", "binance"],
].map(([name, title, group], i) => ({ ...META, name, title, group, desc: "仅展示技能卡片；运行、安装、移除与更新均被禁止。",
  installed: i !== 6, wallet_skill: group === "binance-web3", kind: group === "builtin" ? "builtin" : "preview", url: "" }));

// configured/connected 仅让现有组件进入展示分支，绝不代表真实授权或在线状态。
const cexStatus = { ...META, configured: true, connected: false, masked_key: "DEMO · 无真实密钥", cli: { version: "DEMO", profile: "演示只读", env: "offline" } };
const cexAssets = [
  { asset: "演示 USDT", free: 1000, locked: 0, usdt: 1000, no_usdt_pair: false },
  { asset: "演示 BTC", free: 2, locked: 0, usdt: 246.9, no_usdt_pair: false },
  { asset: "演示 ETH", free: 3, locked: 0, usdt: 203.67, no_usdt_pair: false },
];
const orders = tickers.slice(4, 6).map((r, i) => ({ ...META, orderId: `demo-order-${i + 1}`, symbol: r.symbol,
  side: i ? "SELL" : "BUY", type: "LIMIT", price: String(r.price), origQty: "10", executedQty: i ? "2" : "0", status: "NEW", time: FIXTURE_MS - i * 60000 }));
const trades = tickers.slice(4, 7).map((r, i) => ({ ...META, id: `demo-trade-${i + 1}`, orderId: `demo-filled-${i + 1}`,
  symbol: r.symbol, isBuyer: i % 2 === 0, price: String(r.price), qty: "5", commission: "0", commissionAsset: "DEMO", time: FIXTURE_MS - (i + 1) * 3600000 }));
const walletStatus = { ...META, connected: true, detail: "演示快照：无真实登录、地址或可用资金。" };
const walletRuntime = { ...META, bundled: true, version: "DEMO（无真实 runtime）", mode: "preview", node_exists: false, baw_exists: false };
// 仅这四个精确字符串可读取常量；不是 shell 执行器，不接受额外参数或命令拼接。
const walletReads = new Map<string, unknown>([
  ["baw wallet balance --json", [{ asset: "演示 USDT", balance: "1200", chain: "演示链 A" }, { asset: "演示 BNB", balance: "12", chain: "演示链 B" }]],
  ["baw wallet address --json", [{ chainName: "演示链 A", address: "DEMO-NOT-A-REAL-ADDRESS-A" }, { chainName: "演示链 B", address: "DEMO-NOT-A-REAL-ADDRESS-B" }]],
  ["baw wallet chains --json", ["演示链 A", "演示链 B"]],
  ["baw wallet left-quota --json", { "演示 swap": "0（禁止交易）", "演示 defi": "0（禁止交易）", "演示 x402": "0（禁止支付）" }],
]);
const web3Status = { ...META, configured: false, connected: false, masked_key: "", detail: "演示模式禁止绑定真实钱包。" };
const kernel = { ...META, installed: false, running: false, version: "DEMO", download: { active: false, done: 0, total: 0 } };
const cron = [{ ...META, id: "demo-cron", name: "演示每日摘要（暂停）", schedule: "0 9 * * *", task: "daily_scan_report", persona: agents[0].name,
  enabled: false, last_run: FIXTURE_SEC, next_run: null, last_status: "paused", last_summary: "演示记录，不会建立或运行调度任务。" }];
const mcp = [{ ...META, name: "演示本地资料", url: "", auth: "none", enabled: false, reachable: false, authed: false, tools_count: 0, detail: "离线演示，不连接任何 MCP 服务。" }];
const plugins = [{ ...META, id: "demo-plugin", name: "演示资料插件", description: "展示插件布局，禁止执行命令。", version: "DEMO", enabled: false, commands: [] }];

// EventTarget 保留 add/removeEventListener 与 on* 两套监听方式；从不创建原生 socket。
class OfflineWebSocket extends EventTarget {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  readonly CONNECTING = 0;
  readonly OPEN = 1;
  readonly CLOSING = 2;
  readonly CLOSED = 3;
  readonly url: string;
  readonly protocol = "";
  readonly extensions = "";
  readonly bufferedAmount = 0;
  readonly status = "offline";
  readyState = OfflineWebSocket.CLOSED;
  binaryType: BinaryType = "blob";
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  private closeSent = false;

  constructor(url: string | URL, _protocols?: string | string[]) {
    super();
    this.url = String(url);
    // 异步 close 让 live.ts 来得及绑定 onclose，并把状态从 init 切换到 off。
    queueMicrotask(() => this.close());
  }

  send(_data: string | ArrayBufferLike | Blob | ArrayBufferView): void {
    // 离线 no-op；包括 Vite/行情订阅的 send 都不会抛错或访问网络。
  }

  close(_code?: number, _reason?: string): void {
    if (this.closeSent) return;
    this.closeSent = true;
    this.readyState = OfflineWebSocket.CLOSED;
    const event = new CloseEvent("close", { code: 1000, reason: "演示 offline", wasClean: true });
    this.dispatchEvent(event);
    this.onclose?.call(this, event);
  }
}

/** 安装后不可恢复真实网络；重复调用保留内存状态，刷新页面才重置 fixture。 */
export function installPreview(): void {
  const host = window as Window & { [INSTALL_KEY]?: boolean };
  if (host[INSTALL_KEY]) return;

  let nextId = 10;
  const conversations = new Map<string, Conversation>();
  for (let i = 0; i < 4; i++) {
    const id = `demo-conversation-${i + 1}`;
    conversations.set(id, { id, title: ["演示 · 工作台导览", "演示 · 行情观察", "演示 · 广场草稿", "演示 · 已归档笔记"][i],
      persona: i === 1 ? agents[0].name : "", kind: "chat", archived: i === 3,
      created_at: FIXTURE_SEC - i * 3600, updated_at: FIXTURE_SEC - i * 3600,
      messages: [{ role: "user", content: "展示离线预览能力（演示消息）。" }, { role: "assistant", content: REPLY, model: MODEL, tools: [] }] });
  }
  const memory = new Map<string, MemoryRow>([
    ["pref.preview", "演示偏好：中文、简洁、只读。"], ["pref.safety", "演示偏好：所有操作先确认，真实执行始终禁止。"],
    ["alloc.演示资产A", "40%（演示）"], ["alloc.演示资产B", "60%（演示）"],
    ["habit.preview", "演示习惯：先看概览，再核对细节。"], ["fact.preview", "演示事实：所有数据只存在本页内存。"],
    ["bot.preview", "演示 Agent 只能返回固定模板。"],
  ].map(([key, value], i) => [key, { key, value, kind: key.split(".")[0], source: DEMO, hits: 3 + i, updated_at: FIXTURE_MS - i * 60000 }]));
  let posts: Post[] = [
    { id: "demo-post-1", ts: FIXTURE_MS, kind: "article", title: "演示 · 观察笔记", text: "成功状态样本：这篇文章从未发布，所有内容只存在预览内存。", tags: ["演示", "离线"], status: "posted", via: "agent" },
    { id: "demo-post-2", ts: FIXTURE_MS - 3600000, kind: "text", title: "演示 · 工作台速记", text: "第二条成功状态 fixture；没有真实 post_id 或外链。", tags: ["演示"], status: "posted", via: "manual" },
    { id: "demo-post-3", ts: FIXTURE_MS - 7200000, kind: "article", title: "演示 · 被阻止的发布", text: "可删除此失败样本，不影响任何真实台账。", tags: ["演示", "失败样本"], status: "failed", error: "演示失败：网络已隔离，没有提交发布请求。", via: "agent" },
    { id: "demo-post-4", ts: FIXTURE_MS - 10800000, kind: "text", title: "演示 · 无授权草稿", text: "可通过清空失败项移除此记录；刷新页面恢复 fixture。", tags: ["演示"], status: "failed", error: "演示失败：禁止使用真实凭据。", via: "manual" },
  ];
  const squareKey = { ...META, present: false, masked: "", source: "演示 · 未保存密钥" };
  const settings = { ...META, llm: { provider: "custom", configured: true, base_url: "", api_key: "", model: MODEL, backup_models: [], aux: {} },
    auto_exec: false, deep_thinking: false, workspace: "演示内存（无文件路径）", locale: "zh" };

  const listConversations = (includeArchived: boolean) => [...conversations.values()]
    .filter((c) => includeArchived || !c.archived).sort((a, b) => b.updated_at - a.updated_at)
    .map(({ messages, ...c }) => ({ ...META, ...c, message_count: messages.length }));
  const memoryStats = () => {
    const items = [...memory.values()].sort((a, b) => b.updated_at - a.updated_at);
    const categories = new Map<string, number>();
    items.forEach((r) => categories.set(r.kind, (categories.get(r.kind) ?? 0) + 1));
    return { ...META, ok: true, total: items.length, categories: [...categories].map(([name, count]) => ({ name, count })),
      recent: items.slice(0, 5), last_updated_at: items[0]?.updated_at ?? 0,
      db_path: "演示内存（未读取数据库）", db_size_bytes: 0, engine: "演示 JavaScript Map · 不落盘" };
  };
  const squareData = () => {
    const posted = posts.filter((p) => p.status === "posted").length;
    // 按 fixture 日期统计，不伪称是运行当天的真实发布量。
    return { ...META, ok: true, posts, key: squareKey, stats: { total: posts.length, posted, failed: posts.length - posted,
      today: posted, week: posted, limit_per_day: 100, fixture_day: "2026-09-01" } };
  };

  async function bodyOf(input: RequestInfo | URL, init?: RequestInit): Promise<Record<string, unknown>> {
    const raw = init?.body !== undefined ? init.body : input instanceof Request ? await input.clone().text() : "{}";
    if (typeof raw !== "string" || raw.length > 100000) throw new Error("演示仅接受有限长度 JSON");
    const parsed: unknown = JSON.parse(raw || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("演示 JSON 必须是对象");
    return parsed as Record<string, unknown>;
  }

  function stream(body: Record<string, unknown>, signal: AbortSignal | null | undefined, botReply = false): Response {
    if (signal?.aborted) throw abortError();
    const requestedId = typeof body.conversation_id === "string" ? body.conversation_id : "";
    let conv = conversations.get(requestedId);
    if (!conv) {
      const id = `demo-conversation-${nextId++}`;
      const p = body.persona;
      const persona = typeof p === "object" && p !== null && "name" in p ? String(p.name) : typeof p === "string" ? p : "";
      conv = { id, title: "演示 · 新对话", persona: agents.some((a) => a.name === persona) ? persona : "", kind: "chat", archived: false,
        created_at: Date.now() / 1000, updated_at: Date.now() / 1000, messages: [] };
      conversations.set(id, conv);
    }
    const current = conv;
    // 不保留用户输入、附件、密钥或审批负载；只写固定占位消息。
    current.messages.push({ role: "user", content: "[演示用户消息：输入内容未保存]" });
    current.updated_at = Date.now() / 1000;
    const names = botReply && Array.isArray(body.to) ? agents.filter((a) => (body.to as unknown[]).includes(a.name)).map((a) => a.name) : [];
    const replies = botReply
      ? names.map((name) => ({ type: "bot", name, text: REPLY }))
      : [REPLY.slice(0, 28), REPLY.slice(28, 76), REPLY.slice(76)].map((delta) => ({ type: "text", delta }));
    const events = [
      { ...META, type: "meta", conversation_id: current.id, model: MODEL }, ...replies,
      { ...META, type: "done", conversation_id: current.id, model: MODEL, tools: [], needs_approval: false, needs_clarify: false },
    ];
    let timer: ReturnType<typeof setTimeout> | undefined;
    let ended = false;
    let cancel = () => {};
    const readable = new ReadableStream<Uint8Array>({
      start(controller) {
        const cleanup = () => { if (timer !== undefined) clearTimeout(timer); signal?.removeEventListener("abort", onAbort); };
        const onAbort = () => { if (ended) return; ended = true; cleanup(); controller.error(abortError()); };
        cancel = () => { ended = true; cleanup(); };
        signal?.addEventListener("abort", onAbort, { once: true });
        if (signal?.aborted) { onAbort(); return; }
        let index = 0;
        const encoder = new TextEncoder();
        const pump = () => {
          if (ended) return;
          controller.enqueue(encoder.encode(JSON.stringify(events[index++]) + "\n"));
          if (index === events.length) {
            if (botReply) names.forEach((persona) => current.messages.push({ role: "assistant", content: REPLY, persona, model: MODEL, tools: [] }));
            else current.messages.push({ role: "assistant", content: REPLY, persona: current.persona, model: MODEL, tools: [] });
            ended = true; cleanup(); controller.close();
          } else timer = setTimeout(pump, 45);
        };
        timer = setTimeout(pump, 0);
      },
      cancel() { cancel(); },
    });
    return new Response(readable, { headers: { "Content-Type": "application/x-ndjson; charset=utf-8", "Cache-Control": "no-store", "X-BAZZ-Preview": "offline-fixture" } });
  }

  function get(path: string, query: URLSearchParams): Response {
    switch (path) {
      case "/api/settings": return json(settings);
      case "/api/settings/auto-exec": return json({ ...META, auto_exec: false });
      case "/api/settings/deep-thinking": return json({ ...META, deep_thinking: false });
      case "/api/status": return json({ ...META, ok: true, status: "offline", cpu: "0% · 演示", mem: "演示内存", agents: `${agents.length} DEMO AGENTS`, load: "OFFLINE", build: "PREVIEW · 演示", version: "DEMO" });
      case "/api/bots": return json({ ...META, agents });
      case "/api/bots/activity": return json({ ...META, activity: agents.map((a) => ({ persona: a.name, last_ts: FIXTURE_SEC })) });
      case "/api/conversations": return json(listConversations(query.get("include_archived") === "1"));
      case "/api/conversations/search": {
        const q = (query.get("q") ?? "").toLowerCase();
        return json(listConversations(true).filter((c) => c.title.toLowerCase().includes(q)).map((c) => ({ ...c, snippet: "演示会话，无真实历史。" })));
      }
      case "/api/rooms": return json({ ...META, rooms: [] });
      case "/api/skills": return json(skills);
      case "/api/skills/updates": return json({ ...META, phase: "done", baw: { installed: "DEMO", current: "DEMO", latest: "DEMO", available: false }, skills: { installed: skills.filter((s) => s.installed).map((s) => s.name), updated: [], total: skills.length }, log: [], message: "演示模式不检查或安装更新。" });
      case "/api/llm/models": return json({ ...META, models: [MODEL] });
      case "/api/approvals/whitelist": return json({ ...META, items: [] });
      case "/api/market": return json({ ...META, all: tickers, total: tickers.length, signals, quote: "USDT（演示，非真实行情）", updated_at: FIXTURE_SEC,
        movers: { gainers: tickers.filter((r) => r.change_pct > 0).sort((a, b) => b.change_pct - a.change_pct), losers: tickers.filter((r) => r.change_pct < 0).sort((a, b) => a.change_pct - b.change_pct) } });
      case "/api/market/overview": {
        const up = tickers.filter((r) => r.change_pct > 0).length;
        const down = tickers.filter((r) => r.change_pct < 0).length;
        const crowded = futures.map((r) => ({ ...r, direction: r.funding_rate > 0 ? "LONG" : "SHORT", crowded: true, extreme: true }));
        return json({ ...META, updated_at: FIXTURE_SEC, breadth: { advancers: up, decliners: down, unchanged: tickers.length - up - down, up_ratio: up / tickers.length,
          avg_abs_chg: tickers.reduce((sum, r) => sum + Math.abs(r.change_pct), 0) / tickers.length, extreme_count: 0, total: tickers.length },
          funding: { long_crowded: crowded.filter((r) => r.funding_rate > 0), short_crowded: crowded.filter((r) => r.funding_rate < 0), extremes: crowded, flips: [] },
          volume_top: [...tickers].sort((a, b) => b.quote_volume - a.quote_volume) });
      }
      case "/api/market/futures": return json({ ...META, futures, equity, total_futures: futures.length, total_equity: equity.length, updated_at: FIXTURE_SEC });
      case "/api/market/klines": {
        const symbol = query.get("symbol") ?? "BTCUSDT";
        const row = [...tickers, ...equity].find((r) => r.symbol === symbol);
        const interval = query.get("interval") ?? "1h";
        const n = Number(query.get("limit") ?? 24);
        const limit = Number.isFinite(n) ? Math.max(1, Math.min(500, Math.floor(n))) : 24;
        const steps: Record<string, number> = { "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800 };
        const step = steps[interval] ?? 3600;
        const closes = row ? Array.from({ length: limit }, (_, i) => Number((row.price * (1 + Math.sin(i * 0.6) * 0.02 + (i - limit + 1) / limit * 0.04)).toFixed(6))) : [];
        if (closes.length && row) closes[closes.length - 1] = row.price;
        const klines = closes.map((close, i) => { const open = closes[Math.max(0, i - 1)]; const time = (FIXTURE_SEC - (limit - i) * step) * 1000;
          return [time, String(open), String(Math.max(open, close) * 1.01), String(Math.min(open, close) * 0.99), String(close), String(100 + i), time + step * 1000 - 1]; });
        return json({ ...META, symbol, interval, market: query.get("market") ?? "spot", closes, klines, updated_at: FIXTURE_SEC });
      }
      case "/api/market/oi": return json({ ...META, items: (query.get("symbols") ?? "").split(",").filter(Boolean).map((symbol) => {
        const row = [...futures, ...equity].find((r) => r.symbol === symbol);
        return { ...META, symbol, oi: row ? 1000 : null, notional: row ? row.price * 1000 : null };
      }) });
      case "/api/market/fng": return json({ ...META, value: 52, classification: "演示中性", history: [40, 44, 49, 46, 50, 54, 51, 52].map((value, i) => ({ value, timestamp: FIXTURE_SEC - (7 - i) * 86400 })) });
      case "/api/market/longshort": return json({ ...META, rows: futures.slice(0, 4).map((r, i) => ({ ...r, top_ratio: 1.2 + i * 0.1, top_prev: 1.1, global_ratio: 0.9, divergence: true })) });
      case "/api/market/radar": return json({ ...META, ignition: radar.slice(0, 2), takeoff: radar.slice(2), stage_counts: { IGNITION: 2, VERTICAL: 1 }, engine: "演示 fixture", env: { regime: "DEMO / OFFLINE" } });
      case "/api/market/ignition": return json({ ...META, items: radar.slice(0, 2), ignition: radar.slice(0, 2) });
      case "/api/market/monsters": return json({ ...META, items: radar.slice(2), monsters: radar.slice(2) });
      case "/api/market/radar/tracks": return json({ ...META, pending: [], history: [], stats: { total: 0, pending: 0, moon: 0, dump: 0, expired: 0 }, ts: FIXTURE_SEC });
      case "/api/market/liquidations": return json({ ...META, rows: [], items: [], total: 0 });
      case "/api/wallet/cex/status": return json(cexStatus);
      case "/api/wallet/cex/summary": return json({ ...META, status: "ok", account: { total_usdt: 1450.57, assets: cexAssets, can_trade: false, can_withdraw: false, can_deposit: false } });
      case "/api/wallet/cex/openorders":
      case "/api/wallet/cex/allorders": return json({ ...META, status: "ok", orders: orders.filter((o) => !query.get("symbol") || o.symbol === query.get("symbol")) });
      case "/api/wallet/cex/trades": return json({ ...META, status: "ok", trades: trades.filter((r) => !query.get("symbol") || r.symbol === query.get("symbol")), aggregated: !query.get("symbol"), scanned: trades.map((r) => r.symbol) });
      case "/api/orders/track": return json({ ...META, status: "ok", orders: [] });
      case "/api/wallet": return json({ ...META, cli: { installed: true, version: "DEMO（演示）" }, npm_available: false, status: walletStatus,
        daily_caps: { swap: "0 · 演示禁用", defi: "0 · 演示禁用", x402: "0 · 演示禁用" }, commands: [], install_cmd: "演示模式禁止安装" });
      case "/api/wallet/runtime": return json(walletRuntime);
      case "/api/wallet/status": return json(walletStatus);
      case "/api/wallet/web3/status": return json(web3Status);
      case "/api/wallet/web3/agent-addresses": return json({ ...META, addresses: [], items: [], connected: false });
      case "/api/wallet/campaign": return json({ ...META, active: false, name: "演示活动占位（非真实活动）", official_page: "#", starts_at: FIXTURE_MS - 86400000,
        ends_at: FIXTURE_MS, server_now: FIXTURE_MS, days_remaining: 0, rules_summary: ["演示 fixture，无奖励、无支付、无报名。"], rule_doc: "演示内存", operator: "演示" });
      case "/api/x402/supported": return json({ ...META, supported: [], schemes: [], assets: [], enabled: false });
      case "/api/square/posts": return json(squareData());
      case "/api/square/key": return json(squareKey);
      case "/api/memory": return json([...memory.values()]);
      case "/api/memory/stats": return json(memoryStats());
      case "/api/memory/export": return query.get("format") === "md"
        ? text("# 演示记忆（仅内存）\n\n" + [...memory.values()].map((r) => `- ${r.key}: ${r.value}`).join("\n"), "text/markdown; charset=utf-8")
        : json({ ...META, items: [...memory.values()] });
      case "/api/stats": return json({ ...META, conversations: conversations.size, memory: memory.size, bots: agents.length, skills: skills.length, posts: posts.length });
      case "/api/admin": return json({ ...META, cron, mcp, plugins, gateways: [] });
      case "/api/cron": return json(cron);
      case "/api/mcp": return json(mcp);
      case "/api/plugins": return json(plugins);
      case "/api/gateways": return json({ ...META, gateways: [{ name: "演示网关", id: "demo-gateway", connected: false, status: "offline", detail: DEMO }], mcp_tools: 0 });
      case "/api/proxies": return json({ ...META, active_id: "", entries: [], kernel, env: {} });
      case "/api/proxies/kernel": return json(kernel);
      case "/api/update/check": return json({ ...META, ok: true, available: false, current: "DEMO", latest: "DEMO", notes: "演示模式不检查真实更新。" });
      case "/api/update/status": return json({ ...META, active: false, ready: false, done: 0, total: 0, path: "" });
      case "/api/workspace/files": return json({ ...META, path: "", files: [], items: [], root: "演示内存（文件访问禁用）" });
      case "/api/workspace/read": return json({ ...META, content: "演示模式未读取任何文件。", text: "演示模式未读取任何文件。", path: "" });
    }
    if (/^\/api\/mcp\/[^/]+\/tools$/.test(path)) return json({ ...META, tools: [] });
    const convMatch = path.match(/^\/api\/conversations\/([^/]+)$/);
    if (convMatch) return json({ ...META, ...(conversations.get(decodeURIComponent(convMatch[1])) ?? { messages: [] }) });
    // 任意来源、任意协议的未知 GET 均为空 JSON；绝不回落到真实 fetch。
    return json({});
  }

  const previewFetch: typeof window.fetch = async (input, init) => {
    const request = input instanceof Request ? input : null;
    const method = (init?.method ?? request?.method ?? "GET").toUpperCase();
    const signal = init?.signal ?? request?.signal;
    if (signal?.aborted) throw abortError();
    let url: URL;
    try { url = new URL(request ? request.url : String(input), window.location.href); }
    catch { return method === "GET" ? json({}) : denied(); }
    const path = url.pathname.replace(/\/+$/, "") || "/";
    if (method === "GET") {
      try { return get(path, url.searchParams); }
      catch { return json({}); }
    }
    // 白名单之外的写请求先拒绝，甚至不读取 body（可能包含真实密钥/附件）。
    const convMatch = path.match(/^\/api\/conversations\/([^/]+)(?:\/(archive|rename))?$/);
    const memMatch = path.match(/^\/api\/memory\/([^/]+)$/);
    const allowed = method === "POST" && ["/api/chat/stream", "/api/bots/reply", "/api/conversations", "/api/memory", "/api/square/posts/delete", "/api/wallet/run"].includes(path)
      || method === "POST" && !!convMatch?.[2]
      || method === "DELETE" && (!!memMatch || !!convMatch && !convMatch[2]);
    if (!allowed) return denied();
    let body: Record<string, unknown> = {};
    try {
      if (method === "POST") body = await bodyOf(input, init);
      if (signal?.aborted) throw abortError();
      if (path === "/api/chat/stream" || path === "/api/bots/reply") return stream(body, signal, path === "/api/bots/reply");
      if (path === "/api/wallet/run") {
        if (typeof body.cmd !== "string" || !walletReads.has(body.cmd)) return denied();
        return json({ ...META, status: "ok", code: "PREVIEW_FIXTURE", returncode: 0, stderr: "", stdout: JSON.stringify({ ...META, data: walletReads.get(body.cmd) }) });
      }
      if (path === "/api/square/posts/delete") {
        if (!Array.isArray(body.ids) || !body.ids.every((id) => typeof id === "string")) return invalid();
        const ids = new Set(body.ids);
        // 即使手写请求也不能删除 posted 样本；清空仅由现有组件传入全部 failed id。
        const deleted = posts.filter((p) => p.status === "failed" && ids.has(p.id)).map((p) => p.id);
        posts = posts.filter((p) => !deleted.includes(p.id));
        return json({ ...squareData(), deleted: deleted.length, deleted_ids: deleted });
      }
      if (path === "/api/conversations") {
        const id = `demo-conversation-${nextId++}`;
        const c: Conversation = { id, title: "演示 · 新对话", persona: "", kind: "chat", archived: false, created_at: Date.now() / 1000, updated_at: Date.now() / 1000, messages: [] };
        conversations.set(id, c);
        return json({ ...META, ...c });
      }
      if (convMatch) {
        const id = decodeURIComponent(convMatch[1]);
        const c = conversations.get(id);
        if (!c) return json({ ...META, ok: false, error: "演示会话不存在。" }, 404);
        if (method === "DELETE") conversations.delete(id);
        else if (convMatch[2] === "archive") { if (typeof body.archived !== "boolean") return invalid(); c.archived = body.archived; }
        else if (convMatch[2] === "rename") { if (typeof body.title !== "string") return invalid(); c.title = `演示 · ${body.title.slice(0, 80)}`; }
        return json({ ...META, ok: true, id });
      }
      if (memMatch) {
        const key = decodeURIComponent(memMatch[1]);
        if (!memory.has(key)) return denied();
        memory.delete(key);
        return json({ ...META, ok: true, deleted: 1, total: memory.size });
      }
      if (path === "/api/memory") {
        if (typeof body.key !== "string" || !body.key.trim() || typeof body.value !== "string") return invalid();
        const key = body.key.trim().slice(0, 160);
        memory.set(key, { key, value: `演示 · ${body.value.slice(0, 4000)}`, kind: key.split(".")[0], source: DEMO, hits: 0, updated_at: Date.now() });
        return json({ ...META, ok: true, total: memory.size });
      }
    } catch (error) {
      if (signal?.aborted) throw abortError();
      return invalid();
    }
    return denied();
  };

  // 先替换网络入口，再设置幂等标记；没有卸载/恢复真实网络的 API。
  window.fetch = previewFetch;
  window.WebSocket = OfflineWebSocket as unknown as typeof WebSocket;
  host[INSTALL_KEY] = true;
}
