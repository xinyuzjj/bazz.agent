import React, { useEffect, useState } from "react";
import { api } from "../api";
import { I } from "../components/icons";
import { useT, useI18n } from "../i18n/i18n";

/* ============================================================
 * Cron 面板（真实 /api/cron）— 完善版：自然语言解析 / 任务类型枚举 / 状态高亮
 * ============================================================ */

const TASK_TYPES: { id: string; lk: string; dk: string }[] = [
  { id: "daily_scan_report", lk: "admin.cron.taskDaily", dk: "admin.cron.taskDailyDesc" },
  { id: "trend_early_warning", lk: "admin.cron.taskTrend", dk: "admin.cron.taskTrendDesc" },
  { id: "square_post_digest", lk: "admin.cron.taskSquare", dk: "admin.cron.taskSquareDesc" },
  { id: "custom_prompt", lk: "admin.cron.taskCustom", dk: "admin.cron.taskCustomDesc" },
];

function cronHuman(schedule: string, t: any, loc: string): string {
  // 简易 cron 5 字段解析（m h dom mon dow），输出自然语言
  const parts = (schedule || "").trim().split(/\s+/);
  if (parts.length < 5) return schedule || "—";
  const [m, h, dom, mon, dow] = parts;
  const num = (s: string) => (s === "*" ? "*" : String(Number(s)));
  const dayZh: any = { "0": "周日", "1": "周一", "2": "周二", "3": "周三", "4": "周四", "5": "周五", "6": "周六", "7": "周日" };
  const dayEn: any = { "0": "Sun", "1": "Mon", "2": "Tue", "3": "Wed", "4": "Thu", "5": "Fri", "6": "Sat", "7": "Sun" };
  const dayList = dow.split(",").map((d) => (loc === "en" ? (dayEn[d] || d) : (dayZh[d] || `周${d}`))).join(loc === "en" ? ", " : "、");
  const timeZh = (hh: string, mm: string) => `${hh === "*" ? "" : num(hh) + " 点"}${mm === "*" ? "" : num(mm) + " 分"}`;
  const timeEn = (hh: string, mm: string) => `${hh === "*" ? "*" : num(hh)}:${mm === "*" ? "00" : String(Number(mm)).padStart(2, "0")}`;
  if (dom === "*" && mon === "*") {
    if (dow === "*") {
      if (m === "0" && /^\d+$/.test(h)) return t("admin.cron.dailyAt", { h: num(h), m: "00" });
      if (/^\d+$/.test(m) && /^\d+$/.test(h)) return t("admin.cron.dailyAt", { h: num(h), m: String(Number(m)).padStart(2, "0") });
      if (m.startsWith("*/")) return t("admin.cron.everyMin", { n: m.slice(2) });
      if (h.startsWith("*/")) return t("admin.cron.everyHour", { n: h.slice(2) });
      return t("admin.cron.atExec", { s: loc === "en" ? timeEn(h, m) : timeZh(h, m) });
    }
    if (m === "0" && /^\d+$/.test(h)) return t("admin.cron.dayDailyAt", { d: dayList, h: num(h), m: "00" });
    if (/^\d+$/.test(m) && /^\d+$/.test(h)) return t("admin.cron.dayDailyAt", { d: dayList, h: num(h), m: String(Number(m)).padStart(2, "0") });
    return t("admin.cron.dayAtExec", { d: dayList, s: loc === "en" ? timeEn(h, m) : timeZh(h, m) });
  }
  return schedule;
}

function taskTypeLabel(id: string, t: any): string {
  const it = TASK_TYPES.find((x) => x.id === id);
  return it ? t(it.lk) : t("admin.cron.custom");
}

function fmtT(ts?: number, loc?: string) {
  if (!ts) return "—";
  const ms = ts > 1e12 ? ts : ts * 1000;
  return new Date(ms).toLocaleString(loc === "en" ? "en-US" : "zh-CN", { hour12: false });
}

export function CronPanel() {
  const t = useT();
  const { locale } = useI18n();
  const [jobs, setJobs] = useState<any[]>([]);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [adding, setAdding] = useState(false);
  const [f, setF] = useState({ name: "", schedule: "0 9 * * *", task: "daily_scan_report", persona: "" });

  const load = async () => {
    try {
      const d: any = await api.cron();
      setJobs(Array.isArray(d) ? d : d?.items ?? []);
    } catch (e: any) { setMsg(t("admin.cron.loadFail") + e?.message); }
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
  const remove = async (j: any) => { if (!window.confirm(t("admin.cron.delConfirm", { name: j.name }))) return; await api.deleteCron(j.id); load(); };
  const runNow = async (j: any) => {
    setBusy(j.id);
    try {
      const r: any = await api.cronRun(j.id);
      setMsg(r?.ok ? t("admin.cron.ran", { name: j.name }) : t("admin.cron.runFail", { name: j.name, detail: r?.summary ?? r?.error ?? "" }).slice(0, 300));
    } catch (e: any) { setMsg(String(e?.message ?? e)); }
    finally { setBusy(""); load(); }
  };

  const presets = [
    { label: t("admin.cron.preset1"), schedule: "0 9 * * *" },
    { label: t("admin.cron.preset2"), schedule: "0 8 * * *" },
    { label: t("admin.cron.preset3"), schedule: "0 */4 * * *" },
    { label: t("admin.cron.preset4"), schedule: "0 9 * * 1-5" },
  ];

  return (
    <div className="glass p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <I.Refresh size={14} className="text-gold" />
          <span className="font-mono text-[12px] text-ink">{t("admin.cron.title")}</span>
          <span className="pill pill-dim">{t("admin.cron.taskCount", { n: jobs.length })}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <button onClick={load} className="btn-ghost text-[11px] py-1.5"><I.Refresh size={10} /> {t("admin.refresh")}</button>
          <button onClick={() => setAdding(!adding)} className="btn-gold py-1.5 px-3 text-[12px]"><I.Plus size={11} /> {t("admin.cron.addJob")}</button>
        </div>
      </div>
      {msg && <div className="mb-2 rounded-md border border-gold/40 bg-gold/5 px-3 py-1.5 font-mono text-[11px] text-gold break-all">{msg}</div>}
      {adding && (
        <div className="mb-3 rounded-md border border-line bg-elevated/30 p-3 space-y-2">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            <div>
              <label className="prefix block mb-1">{t("admin.cron.fName")}</label>
              <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })}
                placeholder={t("admin.cron.phName")} className="field" />
            </div>
            <div>
              <label className="prefix block mb-1">{t("admin.cron.fType")}</label>
              <select value={f.task} onChange={(e) => setF({ ...f, task: e.target.value })} className="field">
                {TASK_TYPES.map((tt) => <option key={tt.id} value={tt.id}>{t(tt.lk)} · {t(tt.dk)}</option>)}
              </select>
            </div>
          </div>
          <div>
            <label className="prefix block mb-1">{t("admin.cron.fSchedule")}</label>
            <div className="flex items-center gap-2 flex-wrap">
              <input value={f.schedule} onChange={(e) => setF({ ...f, schedule: e.target.value })}
                placeholder="0 9 * * *" className="field font-mono flex-1 min-w-[160px]" />
              <span className="font-mono text-[10.5px] text-gold/90">→ {cronHuman(f.schedule, t, locale)}</span>
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
            <label className="prefix block mb-1">{t("admin.cron.fAgent")}</label>
            <input value={f.persona} onChange={(e) => setF({ ...f, persona: e.target.value })}
              placeholder={t("admin.cron.phAgent")} className="field" />
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <button onClick={() => setAdding(false)} className="btn-ghost text-[12px] py-1.5">{t("admin.cancel")}</button>
            <button onClick={add} disabled={!!busy || !f.name.trim()} className="btn-gold text-[12px] py-1.5"><I.Check size={11} /> {t("admin.cron.saveJob")}</button>
          </div>
        </div>
      )}
      <div className="space-y-1.5">
        {jobs.length === 0 && (
          <div className="text-center py-6 rounded-md border border-dashed border-line">
            <div className="font-mono text-[12px] text-ink-mute">{t("admin.cron.empty")}</div>
            <div className="mt-1 font-mono text-[10.5px] text-ink-mute">{t("admin.cron.emptyHint")}</div>
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
                <span className="pill pill-dim text-[9.5px]">{taskTypeLabel(j.task, t)}</span>
                {j.persona && <span className="pill pill-gold text-[9.5px]">@{j.persona}</span>}
                {!j.enabled && <span className="pill pill-dim text-[9.5px]">{t("admin.cron.disabled")}</span>}
                <div className="ml-auto flex items-center gap-1.5">
                  <button onClick={() => runNow(j)} disabled={busy === j.id} title={t("admin.cron.runTitle")} className="btn-ghost !py-1 !px-2 text-[10.5px] disabled:opacity-50">
                    {busy === j.id ? <I.Refresh size={10} className="animate-spin" /> : <I.Play size={10} />} {t("admin.cron.run")}
                  </button>
                  <button onClick={() => toggle(j)} className={`tgl ${j.enabled ? "on" : ""} scale-75`} title={t("admin.cron.toggleTitle")} />
                  <button onClick={() => remove(j)} className="text-red hover:bg-red/10 rounded p-1" title={t("admin.cron.delTitle")}><I.Trash size={11} /></button>
                </div>
              </div>
              <div className="mt-1.5 grid grid-cols-1 md:grid-cols-3 gap-x-3 gap-y-1 font-mono text-[10.5px]">
                <div className="text-ink-dim">
                  <span className="text-ink-mute">{t("admin.cron.schedLabel")}</span>{cronHuman(j.schedule, t, locale)}
                </div>
                <div className="text-ink-dim">
                  <span className="text-ink-mute">{t("admin.cron.prevLabel")}</span>{fmtT(j.last_run, locale)}
                  {j.last_status && (
                    <span className={statusErr ? "text-red ml-1" : statusOk ? "text-green ml-1" : "ml-1"}>
                      [{statusErr ? t("admin.cron.failed") : statusOk ? t("admin.cron.ok") : j.last_status}]
                    </span>
                  )}
                </div>
                <div className="text-ink-dim">
                  <span className="text-ink-mute">{t("admin.cron.nextLabel")}</span>{fmtT(j.next_run, locale)}
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
      <div className="mt-3 font-mono text-[10px] text-ink-mute">{t("admin.cron.footnote")}</div>
    </div>
  );
}

/* ============================================================
 * Agent 4 通道操作面板 — Agent 在聊天里可直接调用的真实执行通路
 * ============================================================ */

export function ChannelPanel({ onNav }: { onNav?: (n: string) => void }) {
  const t = useT();
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
      title: t("admin.ch.agentWallet"),
      sub: "Agentic Wallet · baw CLI (npm)",
      connected: !!ch.agent?.connected,
      detail: ch.agent?.connected
        ? (ch.agent?.address ? t("admin.ch.addr", { a: ch.agent.address.slice(0, 6), b: ch.agent.address.slice(-4) }) : (ch.agent?.detail || t("admin.ch.agentScanned")))
        : (ch.agent?.detail || t("admin.ch.agentNotScanned")),
      nav: "wallet",
    },
    {
      key: "web3",
      icon: I.Plug,
      title: t("admin.ch.web3Wallet"),
      sub: "Web3 Wallet · BX-/Ed25519 (web3.binance.com)",
      connected: !!ch.web3?.configured,
      detail: ch.web3?.configured
        ? (ch.web3?.masked_key || t("admin.ch.keyConfigured"))
        : (ch.web3?.error ? t("admin.ch.backendErr") : t("admin.ch.web3NoKey")),
      nav: "wallet",
    },
    {
      key: "cex",
      icon: I.Cex,
      title: t("admin.ch.cex"),
      sub: "Binance CEX · HMAC + binance-cli (profile main/prod)",
      connected: !!ch.cex?.configured,
      detail: ch.cex?.configured
        ? (ch.cex?.cli?.profile
            ? `KEY ${ch.cex?.masked_key || ""} · binance-cli v${ch.cex.cli.version || "?"} ${ch.cex.cli.profile}/${ch.cex.cli.env}`
            : (ch.cex?.masked_key ? `KEY ${ch.cex.masked_key}` : t("admin.ch.configured")))
        : (ch.cex?.error ? t("admin.ch.backendErr") : t("admin.ch.cexNoKey")),
      nav: "cex",
    },
    {
      key: "square",
      icon: I.Megaphone,
      title: t("admin.ch.square"),
      sub: "Binance Square · OpenAPI key (publish-only)",
      connected: !!ch.sq?.present,
      detail: ch.sq?.present
        ? (ch.sq?.source ? `${ch.sq.masked} · ${ch.sq.source}` : ch.sq.masked)
        : (ch.sq?.error ? t("admin.ch.backendErr") : t("admin.ch.sqNoKey")),
      nav: "council",
    },
  ];

  const onlineCount = items.filter((i) => i.connected).length;

  return (
    <div className="glass p-4">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-gold text-[13px]">🛰️</span>
        <span className="font-mono text-[12px] text-ink tracking-wider">{t("admin.ch.title")}</span>
        <span className={`pill ${onlineCount === items.length ? "pill-green" : onlineCount > 0 ? "pill-gold" : "pill-red"}`}>
          <span className={`dot ${onlineCount === items.length ? "dot-green live" : onlineCount > 0 ? "dot-gold live" : "dot-red"}`} />
          {t("admin.ch.onlineCount", { n: onlineCount, total: items.length })}
        </span>
        <button onClick={load} disabled={loading} className="ml-auto btn-ghost !px-2 !py-1 text-[11px]">
          <I.Refresh size={10} className={loading ? "animate-spin" : ""} /> {t("admin.refresh")}
        </button>
      </div>
      <div className="font-mono text-[10px] text-ink-mute mb-3">{t("admin.ch.desc")}</div>
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
                  {it.connected ? t("admin.ch.connected") : t("admin.ch.disconnected")}
                </span>
              </div>
              <div className="font-mono text-[9.5px] text-ink-mute leading-relaxed mb-2">{it.sub}</div>
              <div className="font-mono text-[10.5px] text-ink-dim break-all leading-relaxed min-h-[36px]">{it.detail}</div>
              <div className="mt-2 flex items-center gap-1.5">
                <button onClick={() => onNav?.(it.nav)}
                  className="btn-ghost !py-1 !px-2 text-[10.5px] flex items-center gap-1">
                  <I.Arrow size={10} /> {t("admin.ch.manage")}
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
  const t = useT();
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
    catch (e: any) { setMsg(t("admin.mcp.loadFail") + e?.message); }
  };
  useEffect(() => { load(); }, []);

  const add = async () => {
    if (!f.name.trim() || !f.url.trim()) { setMsg(t("admin.mcp.needNameUrl")); return; }
    try { await api.addMcp({ name: f.name.trim(), url: f.url.trim(), auth: f.auth, description: f.description }); setAdding(false); setF({ name: "", url: "", auth: "oauth", description: "" }); await load(); }
    catch (e: any) { setMsg(String(e?.message ?? e)); }
  };
  const remove = async (s: any) => { if (!window.confirm(t("admin.mcp.delConfirm", { name: s.name }))) return; await api.deleteMcp(s.name); load(); };
  const toggle = async (s: any) => { await api.toggleMcp(s.name, !s.enabled); load(); };
  const tools = async (s: any) => {
    setBusy(s.name);
    try {
      const r: any = await api.mcpTools(s.name);
      const arr = Array.isArray(r) ? r : r?.tools ?? [];
      setToolsCache((p: any) => ({ ...p, [s.name]: arr }));
      setMsg(t("admin.mcp.toolsFound", { name: s.name, n: arr.length }) + (arr.length ? ": " + arr.slice(0, 8).map((tt: any) => tt?.name ?? tt).join(", ") + (arr.length > 8 ? " …" : "") : ""));
    } catch (e: any) { setMsg(t("admin.mcp.toolsFail") + (e?.message ?? e)); } finally { setBusy(""); }
  };

  const startOauth = async (s: any) => {
    setBusy(s.name);
    try {
      const r: any = await api.mcpOauthStart(s.name);
      if (r?.ok === false) { setMsg(t("admin.mcp.oauthStartFail") + (r?.error || r?.message || t("admin.mcp.unknown"))); return; }
      const url = r?.url || r?.authorizationUrl || r?.authorizeUrl || (r?.state ? `state=${r.state}` : "");
      const state = r?.state || r?.pollState || "";
      setOauthFlow((p: any) => ({ ...p, [s.name]: { url, state, openedAt: Date.now() } }));
      setMsg(t("admin.mcp.oauthLinkGen", { name: s.name }));
      if (url) window.open(url, "_blank", "noopener,noreferrer");
    } catch (e: any) { setMsg(t("admin.mcp.oauthStartFail") + (e?.message ?? e)); }
    finally { setBusy(""); }
  };

  const pollOauth = async (s: any) => {
    const flow = oauthFlow[s.name];
    if (!flow?.state) return;
    setBusy(s.name);
    try {
      const r: any = await api.mcpOauthPoll(s.name, flow.state, 8);
      setMsg(r?.done ? t("admin.mcp.oauthDone", { name: s.name }) : (r?.pending ? t("admin.mcp.oauthPending") + (r?.detail || "") : t("admin.mcp.oauthFail", { name: s.name, detail: r?.detail || r?.error || t("admin.mcp.fail") })));
      if (r?.done) { setOauthFlow((p: any) => { const n = { ...p }; delete n[s.name]; return n; }); await load(); }
    } catch (e: any) { setMsg(t("admin.mcp.pollFail") + (e?.message ?? e)); }
    finally { setBusy(""); }
  };

  return (
    <div className="glass p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <I.Link size={14} className="text-gold" />
          <span className="font-mono text-[12px] text-ink">{t("admin.mcp.title")}</span>
          <span className="pill pill-dim">{t("admin.mcp.count", { n: servers.length })}</span>
        </div>
        <button onClick={() => setAdding(!adding)} className="btn-gold py-1.5 px-3 text-[12px]"><I.Plus size={11} /> {t("admin.mcp.addServer")}</button>
      </div>
      {msg && <div className="mb-2 rounded-md border border-line bg-elevated/40 px-3 py-1.5 font-mono text-[11px] text-ink-dim break-all">{msg}</div>}
      {adding && (
        <div className="mb-3 rounded-md border border-line bg-elevated/30 p-3 space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder={t("admin.mcp.phName")} className="field" />
            <input value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} placeholder={t("admin.mcp.phUrl")} className="field" />
          </div>
          <input value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} placeholder={t("admin.mcp.phDesc")} className="field" />
          <div className="flex items-center gap-2">
            <span className="prefix">{t("admin.mcp.authLabel")}</span>
            {["oauth", "token", "none"].map((a) => (
              <button key={a} onClick={() => setF({ ...f, auth: a })}
                className={`px-2 py-1 rounded font-mono text-[11px] border ${f.auth === a ? "bg-gold text-canvas border-gold" : "border-line text-ink-dim"}`}>{a}</button>
            ))}
            <div className="ml-auto flex gap-2">
              <button onClick={() => setAdding(false)} className="btn-ghost text-[12px] py-1">{t("admin.cancel")}</button>
              <button onClick={add} className="btn-gold text-[12px] py-1"><I.Check size={11} /> {t("admin.mcp.save")}</button>
            </div>
          </div>
        </div>
      )}
      <div className="space-y-2">
        {servers.length === 0 && <div className="text-center py-6 font-mono text-[12px] text-ink-mute">{t("admin.mcp.empty")}</div>}
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
                {s.authed && <span className="pill pill-green text-[9px]"><I.Check size={9} /> {t("admin.mcp.authed")}</span>}
                {needsAuth && <span className="pill pill-gold text-[9px]"><I.Key size={9} /> {t("admin.mcp.pendingAuth")}</span>}
                {s.reachable === false && <span className="pill pill-red text-[9px]">{t("admin.mcp.unreachable")}</span>}
                <div className="ml-auto flex items-center gap-1.5">
                  <button onClick={() => toggle(s)} className={`tgl ${s.enabled ? "on" : ""} scale-75`} title={t("admin.mcp.toggleTitle")} />
                  <button onClick={() => remove(s)} className="text-red hover:bg-red/10 rounded p-1" title={t("admin.mcp.delTitle")}><I.Trash size={11} /></button>
                </div>
              </div>
              <div className="font-mono text-[10px] text-ink-mute mt-1 break-all">{s.url}</div>

              {/* OAuth 操作区 */}
              {needsAuth && (
                <div className="mt-2.5 rounded-md border border-gold/30 bg-gold/[0.04] p-2.5 flex items-center gap-2 flex-wrap">
                  <I.Key size={12} className="text-gold" />
                  <span className="font-mono text-[11px] text-ink-dim flex-1 min-w-[200px]">
                    {t("admin.mcp.oauthHint")}
                  </span>
                  <button onClick={() => startOauth(s)} disabled={busy === s.name} className="btn-gold !py-1 !px-2 text-[11px]">
                    {busy === s.name ? <I.Refresh size={10} className="animate-spin" /> : <I.Key size={10} />} {t("admin.mcp.startOauth")}
                  </button>
                  {flow?.url && (
                    <a href={flow.url} target="_blank" rel="noreferrer" className="btn-ghost !py-1 !px-2 text-[11px] !text-gold">
                      <I.Link size={10} /> {t("admin.mcp.goAuth")}
                    </a>
                  )}
                  {flow?.state && (
                    <button onClick={() => pollOauth(s)} disabled={busy === s.name} className="btn-ghost !py-1 !px-2 text-[11px]">
                      <I.Check size={10} /> {t("admin.mcp.iAuthorized")}
                    </button>
                  )}
                </div>
              )}

              {/* 工具区 */}
              <div className="mt-2 flex items-center gap-2 flex-wrap">
                <button onClick={() => tools(s)} disabled={!!busy} className="btn-ghost !py-1 !px-2 text-[11px]">
                  {busy === s.name ? <I.Refresh size={10} className="animate-spin" /> : <I.Cpu size={10} />} {t("admin.mcp.discoverTools")}
                </button>
                {toolCount != null && toolCount > 0 && (
                  <span className="pill pill-dim text-[9.5px]">{t("admin.mcp.foundTools", { n: toolCount })}</span>
                )}
                {s.last_error && <span className="font-mono text-[10px] text-red/80">{t("admin.mcp.lastErr")}{s.last_error.slice(0, 80)}</span>}
                {s.detail && !s.last_error && (
                  <span className="font-mono text-[10px] text-ink-mute">{s.detail.slice(0, 100)}</span>
                )}
              </div>

              {toolsCache[s.name]?.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {toolsCache[s.name].slice(0, 16).map((tt: any, i: number) => (
                    <span key={i} className="pill pill-dim text-[9.5px]">{tt?.name ?? tt}</span>
                  ))}
                  {toolsCache[s.name].length > 16 && <span className="font-mono text-[9.5px] text-ink-mute">{t("admin.mcp.more", { n: toolsCache[s.name].length - 16 })}</span>}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-3 font-mono text-[10px] text-ink-mute">
        Binance Agent Native：<code className="text-gold">https://agent.binance.com/mcp/agentic</code>（{t("admin.mcp.nativeDesc")}）
      </div>
    </div>
  );
}
