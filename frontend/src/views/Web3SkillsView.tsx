import React, { useEffect, useState } from "react";
import { api } from "../api";
import { I } from "../components/icons";
import { useT } from "../i18n/i18n";

/* 技能库 —— Binance Skills Hub 全部官方技能（binance-web3 链上/钱包组 + binance 交易组）。
 * 统一在这里：安装 / 移除 / 运行；每个技能一个专属运行面板。
 * 运行真实机制：
 *   - binance-agentic-wallet → baw CLI
 *   - 数据类技能（带 scripts/cli.mjs）→ node <skill>/scripts/cli.mjs <cmd> '<json>'
 *   - HTTP/扩展型（无本地脚本）→ 返回官方 SKILL.md 使用指引
 * 不存在 npx skills run —— 那是错误用法。
 */

type Skill = { name: string; title: string; desc: string; group: string; installed: boolean; wallet_skill?: boolean; url?: string };

const GROUP_ORDER = ["binance-web3", "binance"];
const GROUP_TITLES: Record<string, string> = {
  "binance-web3": "skills.group.binance-web3",
  "binance": "skills.group.binance",
};

// 桌面版预装只保留两个 binance 账户组技能：广场发帖 + 交易 CLI
const BINANCE_ALLOWED = new Set(["square-post", "binance"]);

// HTTP / 扩展型技能：无本地 cli.mjs，只能给官方使用指引
const NO_CLI: Record<string, string> = {
  "binance-leaderboard": "链上排行榜（PnL / 胜率 / Gem Hunter 六维评分）— 依赖 baw / 官方 API，需在真机按 SKILL.md 调用",
  "binance-wallet-tracker": "聪明钱 / KOL 钱包追踪 — 依赖 baw tracker 扩展，需真机调用",
  "binance-tokenized-securities-info": "代币化美股 RWA 数据 — 官方为 HTTP bapi 接口（curl），无本地 CLI",
  "query-token-audit": "代币安全审计（防 scam/honeypot）— 官方为 HTTP bapi 接口（curl），无本地 CLI",
};

// 可执行技能：预设真实命令（shell 风格，JSON 紧凑）
const PRESETS: Record<string, { label: string; cmd: string }[]> = {
  "binance-agentic-wallet": [
    { label: "账户状态", cmd: "wallet status" },
    { label: "代币余额", cmd: "wallet balance --json" },
    { label: "多链地址", cmd: "wallet address --json" },
    { label: "支持公链", cmd: "wallet chains --json" },
    { label: "钱包设置/devMode", cmd: "wallet settings --json" },
    { label: "剩余每日额度", cmd: "wallet left-quota --json" },
    { label: "待确认交易", cmd: "wallet tx-history --type pending --json" },
    { label: "交易历史", cmd: "wallet tx-history --json" },
    { label: "交易锁", cmd: "wallet tx-lock --json" },
    { label: "市价单列表", cmd: "market-order list --json" },
    { label: "限价单列表", cmd: "limit-order list --json" },
    { label: "报价 USDT→BNB(BSC)", cmd: "market-order quote --binanceChainId 56 --fromTokenQty 10 --fromToken 0x55d398326f99059fF775485246999027B3197955 --toToken 0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE --slippage auto --json" },
    { label: "代币授权(风控)", cmd: "approvals list --json" },
    { label: "高/中风险授权", cmd: "approvals list --filterTypes high_risk --json" },
    { label: "预测市场列表", cmd: "prediction market list --limit 10 --json" },
    { label: "加密预测市场", cmd: "prediction market list --l1Category crypto --limit 10 --json" },
    { label: "我的预测持仓", cmd: "prediction position list --json" },
    { label: "待兑付预测", cmd: "prediction position list --tab PENDING_CLAIM --json" },
    { label: "预测 PnL", cmd: "prediction position pnl --json" },
    { label: "预测成交历史", cmd: "prediction order history --limit 10 --json" },
    { label: "DeFi 协议排行", cmd: "defi protocol-list --sortField tvl --json" },
    { label: "DeFi Earn 机会", cmd: "defi investment-list --investType Earn --sortField apy --json" },
    { label: "DeFi LP 机会", cmd: "defi investment-list --investType LiquidityPool --sortField tvl --json" },
    { label: "我的 DeFi 持仓", cmd: "defi position --json" },
  ],
  "meme-rush": [
    { label: "新发射 bonding", cmd: "meme-rush '{\"chainId\":\"CT_501\",\"rankType\":10,\"limit\":20}'" },
    { label: "即将迁移", cmd: "meme-rush '{\"chainId\":\"CT_501\",\"rankType\":20,\"limit\":20}'" },
    { label: "刚上 DEX", cmd: "meme-rush '{\"chainId\":\"CT_501\",\"rankType\":30,\"limit\":20}'" },
    { label: "AI 热门叙事", cmd: "topic-rush '{\"chainId\":\"CT_501\",\"rankType\":10,\"sort\":10,\"asc\":false}'" },
  ],
  "query-token-info": [
    { label: "关键词搜索", cmd: "search '{\"keyword\":\"BNB\",\"chainIds\":\"56\"}'" },
    { label: "BSC WBNB 元数据", cmd: "meta '{\"chainId\":\"56\",\"contractAddress\":\"0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c\"}'" },
    { label: "实时行情", cmd: "dynamic '{\"chainId\":\"56\",\"contractAddress\":\"0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c\"}'" },
    { label: "K 线 1min", cmd: "kline '{\"chainId\":\"56\",\"contractAddress\":\"0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c\",\"interval\":\"1min\",\"limit\":100}'" },
  ],
  "crypto-market-rank": [
    { label: "社交热度榜", cmd: "social-hype '{\"chainId\":\"56\",\"targetLanguage\":\"en\",\"timeRange\":1}'" },
    { label: "统一热度榜", cmd: "token-rank '{\"rankType\":10,\"chainId\":\"56\",\"page\":1,\"size\":20}'" },
    { label: "聪明钱净流入", cmd: "smart-money-inflow '{\"chainId\":\"56\",\"period\":\"24h\"}'" },
    { label: "Meme 突破榜", cmd: "meme-rank '{\"chainId\":\"56\"}'" },
  ],
  "trading-signal": [
    { label: "BSC 聪明钱买卖", cmd: "smart-money '{\"chainId\":\"56\",\"page\":1,\"pageSize\":50}'" },
  ],
  "binance-trading-signal": [
    { label: "Solana 聪明钱", cmd: "smart-money '{\"chainId\":\"CT_501\",\"page\":1,\"pageSize\":50}'" },
    { label: "BSC 聪明钱", cmd: "smart-money '{\"chainId\":\"56\",\"page\":1,\"pageSize\":50}'" },
  ],
  "query-address-info": [
    { label: "地址持仓快照", cmd: "positions '{\"address\":\"0x...\",\"chainId\":\"56\",\"offset\":0}'" },
  ],
  "binance-sports-ai-analyzer": [
    { label: "未结束赛事", cmd: "recent-unfinished '{}'" },
    { label: "可投注选项", cmd: "recent-match-options '{\"limit\":10}'" },
  ],
};

const FREE_HINTS: Record<string, string> = {
  "binance-agentic-wallet": "例如 wallet balance --json · prediction market list --json · defi investment-list --investType Earn --json · approvals list --json · contract-call preview --json",
  "meme-rush": "例如 meme-rush '{\"chainId\":\"CT_501\",\"rankType\":10}'",
  "query-token-info": "例如 search '{\"keyword\":\"BNB\"}'",
  "crypto-market-rank": "例如 social-hype '{\"chainId\":\"56\",\"targetLanguage\":\"en\"}'",
  "trading-signal": "例如 smart-money '{\"chainId\":\"56\",\"page\":1}'",
  "binance-trading-signal": "例如 smart-money '{\"chainId\":\"CT_501\",\"page\":1}'",
  "query-address-info": "例如 positions '{\"address\":\"0x...\",\"chainId\":\"56\"}'",
  "binance-sports-ai-analyzer": "例如 recent-unfinished '{}'",
};

export function Web3SkillsView() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selected, setSelected] = useState<Skill | null>(null);
  const [args, setArgs] = useState("");
  const [busy, setBusy] = useState(false);
  const [output, setOutput] = useState("");
  const [err, setErr] = useState("");
  const [history, setHistory] = useState<{ ts: number; cmd: string; out: string; ok: boolean }[]>([]);
  const [status, setStatus] = useState("");
  const [upd, setUpd] = useState<any>(null);
  const t = useT();

  // v1.3.9：baw / 技能包 更新状态（启动后台自动检查；这里展示 + 手动触发）
  const loadUpd = async (refresh?: boolean) => {
    try { setUpd(await api.skillsUpdates(refresh)); } catch { /* 后端未升级/离线时静默 */ }
  };
  useEffect(() => { loadUpd(); }, []);
  const updBusy = upd && (upd.phase === "updating" || upd.phase === "checking");
  useEffect(() => {
    if (!updBusy) return;
    const iv = setInterval(async () => {
      try {
        const d: any = await api.skillsUpdates();
        setUpd(d);
        if (d?.phase === "done" || d?.phase === "error") load(); // 更新结束刷新安装状态
      } catch { /* ignore */ }
    }, 2500);
    return () => clearInterval(iv);
  }, [updBusy]);
  const doUpdate = async () => {
    try { setUpd(await api.skillsUpdate("all")); } catch (e: any) { setErr(e?.message ?? String(e)); }
  };

  const load = async () => {
    try {
      const d: any = await api.skills();
      const list: Skill[] = Array.isArray(d) ? d : [];
      // 只保留官方 catalog（binance / binance-web3），去掉 hermes-ref / 其它
      setSkills(list.filter((s: any) => (s.group === "binance-web3" || s.group === "binance")));
    } catch (e: any) { setErr(e?.message ?? String(e)); }
  };
  useEffect(() => { load(); }, []);

  const actSkill = async (s: Skill, wantInstall: boolean) => {
    if (busy) return;
    setBusy(true); setErr(""); setStatus("");
    try {
      const r: any = wantInstall ? await api.skillsInstall(s.name) : await api.skillsRemove(s.name);
      if (r?.status === "ok") setStatus(wantInstall ? t("skills.installedMsg", { name: s.name }) : t("skills.removedMsg", { name: s.name }));
      else setErr((r?.detail || r?.stderr || t("skills.opFailed")).slice(0, 300));
      await load();
    } catch (e: any) { setErr(e?.message ?? String(e)); }
    finally { setBusy(false); }
  };

  const open = (s: Skill) => { setSelected(s); setArgs(""); setOutput(""); setErr(""); };
  const back = () => { setSelected(null); setArgs(""); setOutput(""); setErr(""); };

  const run = async (finalArgs?: string) => {
    if (!selected || busy) return;
    const a = (finalArgs ?? args).trim();
    setBusy(true); setErr(""); setOutput("");
    try {
      const r: any = await api.skillsRun(selected.name, a);
      const text = (r.stdout ?? "").trim() || (r.stderr ?? "").trim();
      const ok = r?.status === "ok";
      if (!ok) setErr((r?.detail || text || t("skills.runFailed")).slice(0, 1200));
      setOutput(text ? pretty(text) : t("skills.emptyOutput"));
      setHistory((h) => [{ ts: Date.now(), cmd: `${selected.name}${a ? " · " + a : " · " + t("skills.noArgs")}`, out: text || "", ok }, ...h].slice(0, 8));
    } catch (e: any) {
      setErr(e?.message ?? String(e));
      setHistory((h) => [{ ts: Date.now(), cmd: `${selected.name} · ${a || t("skills.noArgs")}`, out: "", ok: false }, ...h].slice(0, 8));
    } finally { setBusy(false); }
  };

  if (!selected) {
    return (
      <div className="p-5 space-y-4">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-2.5">
            <I.Grid className="text-gold" size={24} />
            <span className="font-mono text-[15px] font-bold tracking-wide text-ink">{t("skills.title")}</span>
            <span className="prefix">{t("skills.hubSubtitle")}</span>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <button onClick={load} className="btn-ghost py-1.5 px-3 text-[12px]"><I.Refresh size={12} /> {t("skills.refresh")}</button>
            <span className="pill pill-green">{t("skills.installedCount", { n: skills.filter((s) => s.installed).length, m: skills.length })}</span>
          </div>
        </div>
        {/* v1.3.9：baw CLI + 技能包 更新条（后端启动后台自动检查，这里展示与手动触发） */}
        {upd && (
          <div className="glass px-4 py-2.5 flex items-center gap-2.5 flex-wrap" style={{ borderRadius: 12 }}>
            <I.Cpu size={13} className="text-gold" />
            <span className="font-mono text-[12px] text-ink">{t("skills.bawVersion", { v: upd.baw?.installed || "?" })}</span>
            {upd.baw?.available
              ? <span className="pill pill-gold">{t("skills.bawNew", { v: upd.baw.latest })}</span>
              : <span className="pill pill-green">{t("skills.bawUpToDate")}</span>}
            <span className="pill pill-dim">{t("skills.skillsCount", { n: upd.skills?.installed?.length ?? 0 })}</span>
            {updBusy ? (
              <span className="pill pill-gold">
                <span className="inline-block w-2.5 h-2.5 border border-canvas/40 border-t-canvas rounded-full animate-spin mr-1 align-middle" />
                {t("skills.updating")}{upd.skills?.total ? ` ${upd.skills.updated.length}/${upd.skills.total}` : ""}
              </span>
            ) : upd.message ? <span className="prefix">{upd.message}</span> : null}
            <span className="prefix ml-auto hidden xl:inline">{t("skills.autoNote")}</span>
            <div className="ml-auto flex items-center gap-1.5 xl:ml-0">
              <button onClick={() => loadUpd(true)} disabled={!!updBusy} className="btn-ghost py-1 px-2.5 text-[11px]">{t("skills.checkUpdate")}</button>
              <button onClick={doUpdate} disabled={!!updBusy} className={`btn-gold text-[11px] py-1 ${updBusy ? "opacity-50 cursor-not-allowed" : ""}`}>
                <I.Download size={11} /> {t("skills.updateNow")}
              </button>
            </div>
          </div>
        )}
        {err && <div className="rounded-lg border border-red/40 bg-red/5 px-4 py-2.5 text-[12.5px] text-red font-mono">{err}</div>}
        {status && <div className="rounded-lg border border-gold/30 bg-gold/5 px-4 py-2 text-[12.5px] text-gold font-mono">{status}</div>}
        {skills.length === 0 ? (
          <div className="glass p-8 text-center font-mono text-[12px] text-ink-mute" style={{ borderRadius: 12 }}>{t("skills.loading")}</div>
        ) : (
          GROUP_ORDER.map((g) => {
            const items = skills.filter((s) => s.group === g).filter((s) => g !== "binance" || BINANCE_ALLOWED.has(s.name));
            if (!items.length) return null;
            const inst = items.filter((s) => s.installed).length;
            return (
              <div key={g} className="glass p-4" style={{ borderRadius: 12 }}>
                <div className="flex items-center gap-2 mb-3">
                  <I.Grid size={13} className="text-gold" />
                  <span className="font-mono text-[12px] tracking-wider text-ink">{t(GROUP_TITLES[g] ?? g)}</span>
                  <span className="pill pill-dim">{t("skills.installedCount", { n: inst, m: items.length })}</span>
                  {g === "binance-web3" && <span className="prefix ml-auto">{t("skills.walletSkillNote")}</span>}
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-2.5">
                  {items.map((s) => (
                    <div key={s.name} className={`rounded-lg border p-3.5 transition-colors ${s.installed ? "border-line bg-card/40" : "border-line bg-elevated/20 opacity-80"}`}>
                      <button onClick={() => s.installed && open(s)} disabled={!s.installed} className="w-full text-left">
                        <div className="flex items-start gap-2">
                          <span className="font-mono text-[12.5px] font-semibold text-ink break-all">{s.title || s.name}</span>
                          <span className={`pill ${s.installed ? "pill-green" : "pill-dim"} shrink-0 ml-auto`}>{s.installed ? t("skills.installed") : t("skills.notInstalled")}</span>
                        </div>
                        <div className="text-[11px] text-ink-dim mt-1.5 leading-relaxed min-h-[30px]">{s.desc}</div>
                      </button>
                      <div className="mt-2 flex items-center gap-1.5">
                        {s.wallet_skill && <span className="pill pill-gold text-[9.5px]" title={t("skills.walletSkillTitle")}>Wallet Skill</span>}
                        {s.name === "binance-agentic-wallet" && <span className="prefix text-gold text-[10px]">CORE</span>}
                        {NO_CLI[s.name] && <span className="pill pill-dim text-[9.5px]">{t("skills.httpGuide")}</span>}
                        <div className="ml-auto flex items-center gap-1.5">
                          {s.installed ? (
                            <>
                              <button onClick={() => open(s)} className="btn-ghost text-[11px] py-1"><I.Play size={11} /> {t("skills.run")}</button>
                              <button onClick={() => actSkill(s, false)} disabled={busy} className="btn-ghost text-[11px] py-1 text-red/80 hover:text-red" title={t("skills.remove")}><I.Trash size={11} /> {t("skills.remove")}</button>
                            </>
                          ) : (
                            <button onClick={() => actSkill(s, true)} disabled={busy} className="btn-gold text-[11px] py-1"><I.Download size={11} /> {t("skills.install")}</button>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })
        )}
        <div className="font-mono text-[10.5px] text-ink-mute px-1">
          {t("skills.installSource")}
        </div>
      </div>
    );
  }

  const isApiRef = !!NO_CLI[selected.name];
  const presets = PRESETS[selected.name] ?? [];

  return (
    <div className="p-5 space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <button onClick={back} className="btn-ghost py-1.5 px-3 text-[12px]"><I.Arrow size={11} className="rotate-180" /> {t("skills.backToList")}</button>
        <span className="font-mono text-[15px] font-bold tracking-wide text-ink">{selected.name}</span>
        <span className={`pill ${selected.installed ? "pill-green" : "pill-dim"}`}>{selected.installed ? t("skills.installedState") : t("skills.notInstalledState")}</span>
        {selected.wallet_skill && <span className="pill pill-gold">Wallet Skill</span>}
        {selected.name === "binance-agentic-wallet" && <span className="pill pill-gold">CORE · baw CLI</span>}
        {isApiRef && <span className="pill pill-dim">{t("skills.httpApiGuide")}</span>}
        <span className="ml-auto prefix">{selected.desc}</span>
      </div>

      {err && <div className="rounded-lg border border-red/40 bg-red/5 px-4 py-2.5 text-[12.5px] text-red font-mono whitespace-pre-wrap break-all">{err}</div>}

      {isApiRef ? (
        /* HTTP / 扩展型：不给假运行，只给官方指引 */
        <div className="glass p-4" style={{ borderRadius: 12 }}>
          <div className="flex items-center gap-2 mb-2">
            <I.Cpu size={13} className="text-gold" />
            <span className="font-mono text-[12px] tracking-wider text-ink">{t("skills.usageTitle")}</span>
          </div>
          <p className="text-[12.5px] text-ink-dim leading-relaxed mb-3">{NO_CLI[selected.name]}。{t("skills.viewOfficialGuideHint")}</p>
          <button onClick={() => run("")} disabled={busy} className="btn-gold">
            {busy ? <span className="inline-block w-3 h-3 border-2 border-canvas/40 border-t-canvas rounded-full animate-spin" /> : <I.Play size={11} />}
            {t("skills.viewOfficialGuide")}
          </button>
        </div>
      ) : (
        <div className="glass p-4" style={{ borderRadius: 12 }}>
          <div className="flex items-center gap-2 mb-3">
            <I.Cpu size={13} className="text-gold" />
            <span className="font-mono text-[12px] tracking-wider text-ink">{t("skills.runParams")}</span>
            <span className="prefix">
              {t("skills.cmdLabel")} {selected.name === "binance-agentic-wallet"
                ? <code className="text-gold">baw &lt;args&gt;</code>
                : <code className="text-gold">node &lt;skill&gt;/scripts/cli.mjs &lt;args&gt;</code>}
            </span>
          </div>
          {presets.length > 0 && (
            <div className="mb-3">
              <div className="prefix mb-2">{t("skills.commonPresets")}</div>
              <div className="flex flex-wrap gap-2">
                {presets.map((p, i) => (
                  <button key={i} onClick={() => run(p.cmd)} disabled={busy || !selected.installed}
                    className={`rounded-md border px-3 py-1.5 font-mono text-[11.5px] transition-colors ${args.trim() === p.cmd ? "bg-gold text-canvas border-gold" : "border-line bg-card/40 text-ink hover:border-gold/40"}`}>
                    {p.label}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="flex items-center gap-2">
            <input value={args} onChange={(e) => setArgs(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") run(); }}
              placeholder={FREE_HINTS[selected.name] ?? t("skills.argPlaceholder")}
              disabled={!selected.installed}
              className="field flex-1 font-mono" />
            <button onClick={() => run()} disabled={busy || !selected.installed}
              className={`btn-gold ${busy || !selected.installed ? "opacity-50 cursor-not-allowed" : ""}`}>
              {busy ? <span className="inline-block w-3 h-3 border-2 border-canvas/40 border-t-canvas rounded-full animate-spin" /> : <I.Play size={11} />}
              {t("skills.run")}
            </button>
          </div>
          {!selected.installed && (
            <div className="mt-2 font-mono text-[10.5px] text-ink-mute">{t("skills.notInstalledNote")}</div>
          )}
        </div>
      )}

      {/* 输出区 */}
      <div className="glass p-4" style={{ borderRadius: 12 }}>
        <div className="flex items-center gap-2 mb-3">
          <I.Cpu size={13} className="text-gold" />
          <span className="font-mono text-[12px] tracking-wider text-ink">{t("skills.output")}</span>
          {args && <span className="pill pill-dim font-mono text-[10px]"><code className="text-gold">{selected.name} {args}</code></span>}
          {busy && <span className="pill pill-gold">{t("skills.running")}</span>}
          {output && (
            <button onClick={() => { try { navigator.clipboard?.writeText(output); } catch {} }} className="ml-auto btn-ghost text-[11px] py-1">{t("skills.copyOutput")}</button>
          )}
        </div>
        {!output && !busy ? (
          <div className="py-6 text-center font-mono text-[11.5px] text-ink-mute">
            {isApiRef ? t("skills.apiRefHint") : t("skills.presetHint")}
          </div>
        ) : (
          <pre className="rounded-md border border-line bg-canvas p-3 font-mono text-[11.5px] text-ink-dim overflow-auto max-h-96 whitespace-pre-wrap break-all">{output || "…"}</pre>
        )}
      </div>

      {/* 历史 */}
      {history.length > 0 && (
        <div className="glass p-4" style={{ borderRadius: 12 }}>
          <div className="flex items-center gap-2 mb-2">
            <I.Refresh size={13} className="text-gold" />
            <span className="font-mono text-[12px] tracking-wider text-ink">{t("skills.sessionHistory")}</span>
            <span className="pill pill-dim">{history.length}</span>
          </div>
          <div className="space-y-1">
            {history.map((h, i) => (
              <div key={i} className="flex items-center gap-2 py-1.5 border-b border-line/40 last:border-0">
                <span className={`dot ${h.ok ? "dot-green" : "dot-red"}`} />
                <code className="font-mono text-[11px] text-ink truncate flex-1">{h.cmd}</code>
                <span className="font-mono text-[10px] text-ink-mute tabular">{new Date(h.ts).toLocaleTimeString()}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function pretty(text: string) {
  try { return JSON.stringify(JSON.parse(text), null, 2); } catch { return text; }
}
