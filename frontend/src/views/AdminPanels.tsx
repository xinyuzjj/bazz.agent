import React, { useEffect, useState } from "react";
import { api } from "../api";
import { I } from "../components/icons";

/* ============================================================
 * Cron 面板（真实 /api/cron）— 完善版：自然语言解析 / 任务类型枚举 / 状态高亮
 * ============================================================ */

const TASK_TYPES: { id: string; label: string; desc: string }[] = [
  { id: "daily_scan_report", label: "每日市场扫描 + 日报", desc: "扫描全市场动态池，生成日报" },
  { id: "trend_early_warning", label: "趋势早报", desc: "点火前埋伏窗口扫描" },
  { id: "square_post_digest", label: "广场定时发布", desc: "把日报或行情快讯自动发到币安广场" },
  { id: "custom_prompt", label: "自定义 Prompt", desc: "自定义消息交给 Agent 处理" },
];

function cronHuman(schedule: string): string {
  // 简易 cron 5 字段解析（m h dom mon dow），输出自然语言
  const parts = (schedule || "").trim().split(/\s+/);
  if (parts.length < 5) return schedule || "—";
  const [m, h, dom, mon, dow] = parts;
  const at = (mm: string, hh: string) => `每${hh === "*" ? "" : (Number(hh).toString() + " 点")}${mm === "*" ? "" : (Number(mm).toString() + " 分")}`;
  if (dom === "*" && mon === "*") {
    if (dow === "*") {
      if (m === "0" && /^\d+$/.test(h)) return `每天 ${Number(h)}:00 执行`;
      if (/^\d+$/.test(m) && /^\d+$/.test(h)) return `每天 ${Number(h)}:${String(Number(m)).padStart(2,"0")} 执行`;
      if (m.startsWith("*/")) return `每 ${m.slice(2)} 分钟执行`;
      if (h.startsWith("*/")) return `每 ${h.slice(2)} 小时执行`;
      return `${at(m, h)}执行`;
    }
    const map: any = { "0": "周日", "1": "周一", "2": "周二", "3": "周三", "4": "周四", "5": "周五", "6": "周六", "7": "周日" };
    const days = dow.split(",").map((d) => map[d] || `周${d}`).join("、");
    if (m === "0" && /^\d+$/.test(h)) return `每${days} ${Number(h)}:00 执行`;
    if (/^\d+$/.test(m) && /^\d+$/.test(h)) return `每${days} ${Number(h)}:${String(Number(m)).padStart(2,"0")} 执行`;
    return `每${days} ${at(m, h)}执行`;
  }
  return schedule;
}

function taskTypeLabel(id: string): string {
  return TASK_TYPES.find((t) => t.id === id)?.label ?? (id || "自定义");
}

function fmtT(t?: number) {
  if (!t) return "—";
  const ms = t > 1e12 ? t : t * 1000;
  return new Date(ms).toLocaleString("zh-CN", { hour12: false });
}

export function CronPanel() {
  const [jobs, setJobs] = useState<any[]>([]);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [adding, setAdding] = useState(false);
  const [f, setF] = useState({ name: "", schedule: "0 9 * * *", task: "daily_scan_report", persona: "" });

  const load = async () => {
    try {
      const d: any = await api.cron();
      setJobs(Array.isArray(d) ? d : d?.items ?? []);
    } catch (e: any) { setMsg("加载失败 " + e?.message); }
  };
  useEffect(() => { load(); }, []);

  const add = async () => {
    if (!f.name.trim()) return;
    setBusy("add");
    try {
      await api.addCron({ ...f, name: f.name.trim(), enabled: true });
      setAdding(false);
      setF({ name: "", schedule: "0 9 * * *", task: "daily_scan_report", persona: "" });
      await load();
    } catch (e: any) { setMsg(String(e?.message ?? e)); }
    finally { setBusy(""); }
  };
  const toggle = async (j: any) => { await api.toggleCron(j.id, !j.enabled); load(); };
  const remove = async (j: any) => { if (!window.confirm(`删除任务「${j.name}」？`)) return; await api.deleteCron(j.id); load(); };
  const runNow = async (j: any) => {
    setBusy(j.id);
    try {
      const r: any = await api.cronRun(j.id);
      setMsg(r?.ok ? `✓ ${j.name} 已运行` : `✗ ${j.name}: ${r?.summary ?? r?.error ?? ""}`.slice(0, 300));
    } catch (e: any) { setMsg(String(e?.message ?? e)); }
    finally { setBusy(""); load(); }
  };

  const presets = [
    { label: "每天 9 点", schedule: "0 9 * * *" },
    { label: "每天 8 点", schedule: "0 8 * * *" },
    { label: "每 4 小时", schedule: "0 */4 * * *" },
    { label: "工作日 9 点", schedule: "0 9 * * 1-5" },
  ];

  return (
    <div className="glass p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <I.Refresh size={14} className="text-gold" />
          <span className="font-mono text-[12px] text-ink">自动化定时任务 (CRON)</span>
          <span className="pill pill-dim">{jobs.length} 任务</span>
        </div>
        <div className="flex items-center gap-1.5">
          <button onClick={load} className="btn-ghost text-[11px] py-1.5"><I.Refresh size={10} /> 刷新</button>
          <button onClick={() => setAdding(!adding)} className="btn-gold py-1.5 px-3 text-[12px]"><I.Plus size={11} /> 新增任务</button>
        </div>
      </div>
      {msg && <div className="mb-2 rounded-md border border-gold/40 bg-gold/5 px-3 py-1.5 font-mono text-[11px] text-gold break-all">{msg}</div>}
      {adding && (
        <div className="mb-3 rounded-md border border-line bg-elevated/30 p-3 space-y-2">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            <div>
              <label className="prefix block mb-1">任务名</label>
              <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })}
                placeholder="如 每日市场日报" className="field" />
            </div>
            <div>
              <label className="prefix block mb-1">任务类型</label>
              <select value={f.task} onChange={(e) => setF({ ...f, task: e.target.value })} className="field">
                {TASK_TYPES.map((t) => <option key={t.id} value={t.id}>{t.label} · {t.desc}</option>)}
              </select>
            </div>
          </div>
          <div>
            <label className="prefix block mb-1">调度（cron 5 字段）</label>
            <div className="flex items-center gap-2 flex-wrap">
              <input value={f.schedule} onChange={(e) => setF({ ...f, schedule: e.target.value })}
                placeholder="0 9 * * *" className="field font-mono flex-1 min-w-[160px]" />
              <span className="font-mono text-[10.5px] text-gold/90">→ {cronHuman(f.schedule)}</span>
            </div>
            <div className="mt-1.5 flex flex-wrap gap-1">
              {presets.map((p) => (
                <button key={p.schedule} onClick={() => setF({ ...f, schedule: p.schedule })}
                  className={`pill pill-dim text-[10px] cursor-pointer hover:bg-gold/10 hover:text-gold ${f.schedule === p.schedule ? "bg-gold/10 text-gold" : ""}`}>
                  {p.label}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="prefix block mb-1">绑定 Agent（可选，留空=默认）</label>
            <input value={f.persona} onChange={(e) => setF({ ...f, persona: e.target.value })}
              placeholder="如 bazz-analyst" className="field" />
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <button onClick={() => setAdding(false)} className="btn-ghost text-[12px] py-1.5">取消</button>
            <button onClick={add} disabled={!!busy || !f.name.trim()} className="btn-gold text-[12px] py-1.5"><I.Check size={11} /> 保存任务</button>
          </div>
        </div>
      )}
      <div className="space-y-1.5">
        {jobs.length === 0 && (
          <div className="text-center py-6 rounded-md border border-dashed border-line">
            <div className="font-mono text-[12px] text-ink-mute">暂无定时任务</div>
            <div className="mt-1 font-mono text-[10.5px] text-ink-mute">点右上「新增任务」开始（推荐先选个调度预设）</div>
          </div>
        )}
        {jobs.map((j) => {
          const statusOk = j.last_status === "ok" || j.last_status === "success";
          const statusErr = j.last_status === "error" || j.last_status === "failed";
          const dotCls = !j.enabled ? "dot-dim" : statusErr ? "dot-red live" : statusOk ? "dot-green live" : "dot-gold live";
          return (
            <div key={j.id} className={`rounded-md border p-3 transition-colors ${statusErr ? "border-red/30 bg-red/[0.04]" : "border-line/60 bg-card/40"}`}>
              <div className="flex items-center gap-2 flex-wrap">
                <span className={`dot ${dotCls}`} />
                <span className="font-mono text-[13px] text-ink font-semibold">{j.name}</span>
                <span className="pill pill-dim text-[9.5px]">{taskTypeLabel(j.task)}</span>
                {j.persona && <span className="pill pill-gold text-[9.5px]">@{j.persona}</span>}
                {!j.enabled && <span className="pill pill-dim text-[9.5px]">已停用</span>}
                <div className="ml-auto flex items-center gap-1.5">
                  <button onClick={() => runNow(j)} disabled={busy === j.id} title="立即运行" className="btn-ghost !py-1 !px-2 text-[10.5px] disabled:opacity-50">
                    {busy === j.id ? <I.Refresh size={10} className="animate-spin" /> : <I.Play size={10} />} 运行
                  </button>
                  <button onClick={() => toggle(j)} className={`tgl ${j.enabled ? "on" : ""} scale-75`} title="启停" />
                  <button onClick={() => remove(j)} className="text-red hover:bg-red/10 rounded p-1" title="删除"><I.Trash size={11} /></button>
                </div>
              </div>
              <div className="mt-1.5 grid grid-cols-1 md:grid-cols-3 gap-x-3 gap-y-1 font-mono text-[10.5px]">
                <div className="text-ink-dim">
                  <span className="text-ink-mute">调度 · </span>{cronHuman(j.schedule)}
                </div>
                <div className="text-ink-dim">
                  <span className="text-ink-mute">上次 · </span>{fmtT(j.last_run)}
                  {j.last_status && (
                    <span className={statusErr ? "text-red ml-1" : statusOk ? "text-green ml-1" : "ml-1"}>
                      [{statusErr ? "失败" : statusOk ? "成功" : j.last_status}]
                    </span>
                  )}
                </div>
                <div className="text-ink-dim">
                  <span className="text-ink-mute">下次 · </span>{fmtT(j.next_run)}
                </div>
              </div>
              {j.last_summary && (
                <div className="mt-1.5 font-mono text-[10px] text-ink-mute line-clamp-2 break-all">{j.last_summary}</div>
              )}
              {j.last_error && (
                <div className="mt-1.5 font-mono text-[10px] text-red/80 break-all leading-relaxed">⛔ {j.last_error}</div>
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-3 font-mono text-[10px] text-ink-mute">绑定 Agent 的任务运行后，报告会投递到该 Agent 的专属会话（Hermes Routines）。任务到点会投递到 Agent 的专属会话。</div>
    </div>
  );
}

/* ============================================================
 * Agent 4 通道操作面板 — Agent 在聊天里可直接调用的真实执行通路
 * ============================================================ */

export function ChannelPanel({ onNav }: { onNav?: (n: string) => void }) {
  const [ch, setCh] = useState<any>({});
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const [agent, web3, cex, sq] = await Promise.allSettled([
        fetch("/api/wallet/status").then((x) => x.json()),
        fetch("/api/wallet/web3/status").then((x) => x.json()),
        fetch("/api/wallet/cex/status").then((x) => x.json()),
        fetch("/api/square/key").then((x) => x.json()),
      ]);
      setCh({
        agent: agent.status === "fulfilled" ? agent.value : { connected: false, error: true },
        web3: web3.status === "fulfilled" ? web3.value : { configured: false, error: true },
        cex: cex.status === "fulfilled" ? cex.value : { configured: false, error: true },
        sq: sq.status === "fulfilled" ? sq.value : { present: false, error: true },
      });
    } catch (e) {
      // ignore
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, []);

  const items: { key: string; icon: any; title: string; sub: string; connected: boolean; detail: string; nav: string }[] = [
    {
      key: "agent",
      icon: I.Wallet,
      title: "Agent 钱包",
      sub: "Agentic Wallet · baw CLI (npm)",
      connected: !!ch.agent?.connected,
      detail: ch.agent?.connected
        ? (ch.agent?.address ? `地址 ${ch.agent.address.slice(0,6)}…${ch.agent.address.slice(-4)}` : (ch.agent?.detail || "已扫码登录"))
        : (ch.agent?.detail || "未扫码登录 · 需 Binance App 扫码"),
      nav: "wallet",
    },
    {
      key: "web3",
      icon: I.Plug,
      title: "链上钱包",
      sub: "Web3 Wallet · BX-/Ed25519 (web3.binance.com)",
      connected: !!ch.web3?.configured,
      detail: ch.web3?.configured
        ? (ch.web3?.masked_key || "已配置密钥")
        : (ch.web3?.error ? "后端异常" : "未配置 BX- 密钥"),
      nav: "wallet",
    },
    {
      key: "cex",
      icon: I.Cex,
      title: "币安交易所",
      sub: "Binance CEX · HMAC + binance-cli (profile main/prod)",
      connected: !!ch.cex?.configured,
      detail: ch.cex?.configured
        ? (ch.cex?.cli?.profile
            ? `KEY ${ch.cex?.masked_key || ""} · binance-cli v${ch.cex.cli.version || "?"} ${ch.cex.cli.profile}/${ch.cex.cli.env}`
            : (ch.cex?.masked_key ? `KEY ${ch.cex.masked_key}` : "已配置"))
        : (ch.cex?.error ? "后端异常" : "未填入 HMAC Key + Secret"),
      nav: "cex",
    },
    {
      key: "square",
      icon: I.Megaphone,
      title: "广场发文",
      sub: "Binance Square · OpenAPI key (publish-only)",
      connected: !!ch.sq?.present,
      detail: ch.sq?.present
        ? (ch.sq?.source ? `${ch.sq.masked} · ${ch.sq.source}` : ch.sq.masked)
        : (ch.sq?.error ? "后端异常" : "未配置 OpenAPI Key"),
      nav: "council",
    },
  ];

  const onlineCount = items.filter((i) => i.connected).length;

  return (
    <div className="glass p-4">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-gold text-[13px]">🛰️</span>
        <span className="font-mono text-[12px] text-ink tracking-wider">Agent 4 通道操作面板</span>
        <span className={`pill ${onlineCount === items.length ? "pill-green" : onlineCount > 0 ? "pill-gold" : "pill-red"}`}>
          <span className={`dot ${onlineCount === items.length ? "dot-green live" : onlineCount > 0 ? "dot-gold live" : "dot-red"}`} />
          {onlineCount}/{items.length} 已连通
        </span>
        <button onClick={load} disabled={loading} className="ml-auto btn-ghost !px-2 !py-1 text-[11px]">
          <I.Refresh size={10} className={loading ? "animate-spin" : ""} /> 刷新
        </button>
      </div>
      <div className="font-mono text-[10px] text-ink-mute mb-3">Agent 在聊天里可调用的真实执行通路（4 个：Agent 钱包 / 链上钱包 / 交易所 / 广场），下方为实时连接状态。</div>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        {items.map((it) => {
          const Icon = it.icon;
          return (
            <div key={it.key} className={`rounded-md border p-3 transition-colors ${it.connected ? "border-line bg-card/40" : "border-red/30 bg-red/[0.04]"}`}>
              <div className="flex items-center justify-between mb-1.5">
                <div className="flex items-center gap-2 min-w-0">
                  <Icon size={14} className={it.connected ? "text-gold" : "text-red"} />
                  <span className="font-mono text-[12px] font-semibold text-ink truncate">{it.title}</span>
                </div>
                <span className={`pill ${it.connected ? "pill-green" : "pill-red"} text-[9.5px]`}>
                  {it.connected ? "已连通" : "未连通"}
                </span>
              </div>
              <div className="font-mono text-[9.5px] text-ink-mute leading-relaxed mb-2">{it.sub}</div>
              <div className="font-mono text-[10.5px] text-ink-dim break-all leading-relaxed min-h-[36px]">{it.detail}</div>
              <div className="mt-2 flex items-center gap-1.5">
                <button onClick={() => onNav?.(it.nav)}
                  className="btn-ghost !py-1 !px-2 text-[10.5px] flex items-center gap-1">
                  <I.Arrow size={10} /> 去管理
                </button>
                <span className="ml-auto font-mono text-[9.5px] text-ink-mute">route: {it.nav}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function McpPanel() {
  const [servers, setServers] = useState<any[]>([]);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [adding, setAdding] = useState(false);
  const [f, setF] = useState({ name: "", url: "", auth: "oauth", description: "" });
  // OAuth 授权态：{[name]: {url, openedAt}}
  const [oauthFlow, setOauthFlow] = useState<any>({});
  const [toolsCache, setToolsCache] = useState<any>({});

  const load = async () => {
    try { const d: any = await api.mcp(); setServers(Array.isArray(d) ? d : []); }
    catch (e: any) { setMsg("加载失败 " + e?.message); }
  };
  useEffect(() => { load(); }, []);

  const add = async () => {
    if (!f.name.trim() || !f.url.trim()) { setMsg("需要名称与 URL"); return; }
    try { await api.addMcp({ name: f.name.trim(), url: f.url.trim(), auth: f.auth, description: f.description }); setAdding(false); setF({ name: "", url: "", auth: "oauth", description: "" }); await load(); }
    catch (e: any) { setMsg(String(e?.message ?? e)); }
  };
  const remove = async (s: any) => { if (!window.confirm(`移除 MCP server「${s.name}」？`)) return; await api.deleteMcp(s.name); load(); };
  const toggle = async (s: any) => { await api.toggleMcp(s.name, !s.enabled); load(); };
  const tools = async (s: any) => {
    setBusy(s.name);
    try {
      const r: any = await api.mcpTools(s.name);
      const arr = Array.isArray(r) ? r : r?.tools ?? [];
      setToolsCache((p: any) => ({ ...p, [s.name]: arr }));
      setMsg(`${s.name} 发现 ${arr.length} 个工具${arr.length ? ": " + arr.slice(0, 8).map((t: any) => t?.name ?? t).join(", ") + (arr.length > 8 ? " …" : "") : ""}`);
    } catch (e: any) { setMsg(`工具发现失败: ${e?.message ?? e}`); } finally { setBusy(""); }
  };

  const startOauth = async (s: any) => {
    setBusy(s.name);
    try {
      const r: any = await api.mcpOauthStart(s.name);
      if (r?.ok === false) { setMsg(`OAuth 启动失败: ${r?.error || r?.message || "未知"}`); return; }
      const url = r?.url || r?.authorizationUrl || r?.authorizeUrl || (r?.state ? `state=${r.state}` : "");
      const state = r?.state || r?.pollState || "";
      setOauthFlow((p: any) => ({ ...p, [s.name]: { url, state, openedAt: Date.now() } }));
      setMsg(`已为「${s.name}」生成 OAuth 授权链接，复制到浏览器完成授权。`);
      if (url) window.open(url, "_blank", "noopener,noreferrer");
    } catch (e: any) { setMsg(`OAuth 启动失败: ${e?.message ?? e}`); }
    finally { setBusy(""); }
  };

  const pollOauth = async (s: any) => {
    const flow = oauthFlow[s.name];
    if (!flow?.state) return;
    setBusy(s.name);
    try {
      const r: any = await api.mcpOauthPoll(s.name, flow.state, 8);
      setMsg(r?.done ? `✓ ${s.name} OAuth 授权完成` : (r?.pending ? `等待浏览器授权…${r?.detail || ""}` : `✗ ${s.name}: ${r?.detail || r?.error || "失败"}`));
      if (r?.done) { setOauthFlow((p: any) => { const n = { ...p }; delete n[s.name]; return n; }); await load(); }
    } catch (e: any) { setMsg(`轮询失败: ${e?.message ?? e}`); }
    finally { setBusy(""); }
  };

  return (
    <div className="glass p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <I.Link size={14} className="text-gold" />
          <span className="font-mono text-[12px] text-ink">MCP 服务器 (Binance Agent Native + 自定义)</span>
          <span className="pill pill-dim">{servers.length} 个</span>
        </div>
        <button onClick={() => setAdding(!adding)} className="btn-gold py-1.5 px-3 text-[12px]"><I.Plus size={11} /> 添加 Server</button>
      </div>
      {msg && <div className="mb-2 rounded-md border border-line bg-elevated/40 px-3 py-1.5 font-mono text-[11px] text-ink-dim break-all">{msg}</div>}
      {adding && (
        <div className="mb-3 rounded-md border border-line bg-elevated/30 p-3 space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="名称，如 binance-agentic" className="field" />
            <input value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} placeholder="https://.../mcp 端点" className="field" />
          </div>
          <input value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} placeholder="描述（可选）" className="field" />
          <div className="flex items-center gap-2">
            <span className="prefix">鉴权: </span>
            {["oauth", "token", "none"].map((a) => (
              <button key={a} onClick={() => setF({ ...f, auth: a })}
                className={`px-2 py-1 rounded font-mono text-[11px] border ${f.auth === a ? "bg-gold text-canvas border-gold" : "border-line text-ink-dim"}`}>{a}</button>
            ))}
            <div className="ml-auto flex gap-2">
              <button onClick={() => setAdding(false)} className="btn-ghost text-[12px] py-1">取消</button>
              <button onClick={add} className="btn-gold text-[12px] py-1"><I.Check size={11} /> 保存</button>
            </div>
          </div>
        </div>
      )}
      <div className="space-y-2">
        {servers.length === 0 && <div className="text-center py-6 font-mono text-[12px] text-ink-mute">尚未添加 MCP server</div>}
        {servers.map((s) => {
          const flow = oauthFlow[s.name];
          const isOauth = (s.auth ?? "oauth") === "oauth";
          const needsAuth = isOauth && s.needs_auth && !s.authed;
          const toolCount = (toolsCache[s.name] ?? (s.tools_count ?? (s.tools?.length ?? null)));
          return (
            <div key={s.name} className={`rounded-md border p-3 ${s.reachable ? "border-line bg-card/40" : "border-red/30 bg-red/[0.04]"}`}>
              <div className="flex items-center gap-2 flex-wrap">
                <span className={`dot ${s.authed ? "dot-green live" : s.reachable ? "dot-gold live" : s.enabled ? "dot-gold" : "dot-dim"}`} />
                <span className="font-mono text-[13px] font-semibold text-ink">{s.name}</span>
                <span className="pill pill-dim text-[9px]">{s.auth ?? "oauth"}</span>
                {s.authed && <span className="pill pill-green text-[9px]"><I.Check size={9} /> 已鉴权</span>}
                {needsAuth && <span className="pill pill-gold text-[9px]"><I.Key size={9} /> 待授权</span>}
                {s.reachable === false && <span className="pill pill-red text-[9px]">不可达</span>}
                <div className="ml-auto flex items-center gap-1.5">
                  <button onClick={() => toggle(s)} className={`tgl ${s.enabled ? "on" : ""} scale-75`} title="启停" />
                  <button onClick={() => remove(s)} className="text-red hover:bg-red/10 rounded p-1" title="移除"><I.Trash size={11} /></button>
                </div>
              </div>
              <div className="font-mono text-[10px] text-ink-mute mt-1 break-all">{s.url}</div>

              {/* OAuth 操作区 */}
              {needsAuth && (
                <div className="mt-2.5 rounded-md border border-gold/30 bg-gold/[0.04] p-2.5 flex items-center gap-2 flex-wrap">
                  <I.Key size={12} className="text-gold" />
                  <span className="font-mono text-[11px] text-ink-dim flex-1 min-w-[200px]">
                    需完成 OAuth 授权才能暴露给 Agent，授权后此服务器的工具会自动加入 MCP 运行时。
                  </span>
                  <button onClick={() => startOauth(s)} disabled={busy === s.name} className="btn-gold !py-1 !px-2 text-[11px]">
                    {busy === s.name ? <I.Refresh size={10} className="animate-spin" /> : <I.Key size={10} />} 开始 OAuth 授权
                  </button>
                  {flow?.url && (
                    <a href={flow.url} target="_blank" rel="noreferrer" className="btn-ghost !py-1 !px-2 text-[11px] !text-gold">
                      <I.Link size={10} /> 去授权 ↗
                    </a>
                  )}
                  {flow?.state && (
                    <button onClick={() => pollOauth(s)} disabled={busy === s.name} className="btn-ghost !py-1 !px-2 text-[11px]">
                      <I.Check size={10} /> 我已授权
                    </button>
                  )}
                </div>
              )}

              {/* 工具区 */}
              <div className="mt-2 flex items-center gap-2 flex-wrap">
                <button onClick={() => tools(s)} disabled={!!busy} className="btn-ghost !py-1 !px-2 text-[11px]">
                  {busy === s.name ? <I.Refresh size={10} className="animate-spin" /> : <I.Cpu size={10} />} 发现工具
                </button>
                {toolCount != null && toolCount > 0 && (
                  <span className="pill pill-dim text-[9.5px]">已发现 {toolCount} 个工具</span>
                )}
                {s.last_error && <span className="font-mono text-[10px] text-red/80">最近错误: {s.last_error.slice(0, 80)}</span>}
                {s.detail && !s.last_error && (
                  <span className="font-mono text-[10px] text-ink-mute">{s.detail.slice(0, 100)}</span>
                )}
              </div>

              {toolsCache[s.name]?.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {toolsCache[s.name].slice(0, 16).map((t: any, i: number) => (
                    <span key={i} className="pill pill-dim text-[9.5px]">{t?.name ?? t}</span>
                  ))}
                  {toolsCache[s.name].length > 16 && <span className="font-mono text-[9.5px] text-ink-mute">… +{toolsCache[s.name].length - 16}</span>}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-3 font-mono text-[10px] text-ink-mute">
        Binance Agent Native：<code className="text-gold">https://agent.binance.com/mcp/agentic</code>（OAuth RFC 9728 浏览器流，公开行情免鉴权，写操作需授权）。
      </div>
    </div>
  );
}
