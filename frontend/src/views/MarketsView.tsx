import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { I } from "../components/icons";
import { useT } from "../i18n/i18n";

/* 行情 · MARKETS —— 全市场交易对浏览（不再固定 20 币）
 * 数据源 /api/market：
 *   signals  智能异动扫描（成交额前 150 交易对内，波动 / 资金费率异常）
 *   movers   全市场涨 / 跌幅 TOP（流动性过滤）
 *   all      全 USDT 现货对 24h 快照（最多 400，前端搜索 / 排序 / 加载更多）
 *   total    全 USDT 现货对总数
 */

type Ticker = { symbol: string; price: number; change_pct: number; volume: number; quote_volume: number; high: number; low: number };
type MinTicker = { symbol: string; price: number; change_pct: number };
type Signal = {
  symbol: string; price: number; change_pct: number; volume: number;
  funding_rate?: number; direction: string; emoji?: string; reason?: string; score?: number;
};
type MarketData = {
  signals: Signal[];
  movers?: { gainers: MinTicker[]; losers: MinTicker[] };
  all: Ticker[];
  total?: number;
  quote?: string;
  updated_at?: number;
};

const PAGE = 120; // 每批展示行数（"加载更多"）
type SortKey = "price" | "change_pct" | "quote_volume";
const SORT_META: Record<SortKey, string> = { price: "现价", change_pct: "24h 涨跌", quote_volume: "24h 成交额" };

type OrderMode = "spot-long" | "futures-long" | "futures-short";
/* 妖币雷达行：ignition(启动前) 与 takeoff(起飞中) 共用，可选字段按模式出现 */
type RadarRow = {
  symbol: string; price: number; quote_volume: number; vol_ratio: number;
  tag: string; side: "LONG" | "WATCH_SHORT" | "WATCH"; note: string; score: number;
  change7d_pct?: number; change30d_pct?: number; drawdown_pct?: number;
  change3d_pct?: number; position_pct?: number; floor_rising?: boolean;
};
const SIDE_META: Record<RadarRow["side"], { label: string; cls: string }> = {
  LONG: { label: "做多窗口", cls: "pill-green" },
  WATCH_SHORT: { label: "做空观察", cls: "pill-red" },
  WATCH: { label: "观望", cls: "pill-dim" },
};
const RADAR_COLS = {
  ignition: { tpl: "2.2fr 0.9fr 0.9fr 0.9fr 1fr 0.9fr 1fr 2.3fr", head: ["标的 / 状态", "现价", "3D", "30D", "90日位置", "量比", "研判", "操作"] },
  takeoff:  { tpl: "2fr 0.9fr 0.9fr 0.9fr 0.9fr 1.1fr 0.8fr 1fr 2.2fr", head: ["标的 / 状态", "现价", "7D", "30D", "距高点", "24h 成交额", "放量", "研判", "操作"] },
} as const;

export function MarketsView({ onTrade, onOrder }: {
  onTrade?: (symbol: string) => void;
  onOrder?: (symbol: string, mode: OrderMode) => void;
}) {
  const t = useT();
  const [data, setData] = useState<MarketData | null>(null);
  const [tab, setTab] = useState<"all" | "gainers" | "losers">("all");
  const [q, setQ] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "quote_volume", dir: -1 });
  const [loading, setLoading] = useState(true);

  // —— 妖币雷达：启动前(ignition) / 起飞中(takeoff) 双模式 ——
  const [mode, setMode] = useState<"ignition" | "takeoff">("ignition");
  const [ign, setIgn] = useState<RadarRow[]>([]);
  const [tk, setTk] = useState<RadarRow[]>([]);
  const [env, setEnv] = useState("");
  const [mLoading, setMLoading] = useState(false);
  const [mErr, setMErr] = useState("");
  const [mSide, setMSide] = useState<"ALL" | RadarRow["side"]>("ALL");
  const [mLimit, setMLimit] = useState(24);

  const load = async () => {
    try { const d = await api.market(); setData(d); } finally { setLoading(false); }
  };
  const fetchRadar = async (md: "ignition" | "takeoff", force = false) => {
    setMLoading(true); setMErr("");
    try {
      const d: any = md === "ignition" ? await api.marketIgnition(force) : await api.marketMonsters(force);
      const rows: RadarRow[] = d?.coins ?? [];
      if (md === "ignition") setIgn(rows); else setTk(rows);
      if (d?.env?.regime) setEnv(d.env.regime);
      if (d?.error) setMErr(d.error);
    } catch (e: any) { setMErr(e?.message ?? String(e)); }
    finally { setMLoading(false); }
  };
  useEffect(() => { load(); const t = setInterval(load, 30_000); return () => clearInterval(t); }, []);
  useEffect(() => {
    fetchRadar("ignition"); fetchRadar("takeoff");
    const t = setInterval(() => { fetchRadar("ignition"); fetchRadar("takeoff"); }, 300_000);
    return () => clearInterval(t);
  }, []);
  const radarRows = mode === "ignition" ? ign : tk;
  const mRows = useMemo(
    () => (mSide === "ALL" ? radarRows : radarRows.filter((m) => m.side === mSide)).slice(0, mLimit),
    [radarRows, mSide, mLimit],
  );

  const rows: Ticker[] = useMemo(() => {
    const base = data?.all ?? [];
    let r = base;
    if (tab === "gainers") r = r.filter((x) => x.change_pct > 0);
    if (tab === "losers")  r = r.filter((x) => x.change_pct < 0);
    if (q.trim()) {
      const s = q.trim().toLowerCase();
      r = r.filter((x) => x.symbol.toLowerCase().includes(s) || x.symbol.replace(/USDT$/, "").toLowerCase().includes(s));
    }
    const { key, dir } = sort;
    return [...r].sort((a, b) => (a[key] - b[key]) * dir);
  }, [data, tab, q, sort]);

  const visible = rows.slice(0, limit);
  const total = data?.total ?? rows.length;
  const up = (n: number) => n >= 0;

  const switchTab = (t: "all" | "gainers" | "losers") => {
    setTab(t);
    setLimit(PAGE);
    setSort(t === "all" ? { key: "quote_volume", dir: -1 }
      : t === "gainers" ? { key: "change_pct", dir: -1 }
      : { key: "change_pct", dir: 1 });
  };
  const toggleSort = (key: SortKey) => {
    setTab("all");
    setSort((s) => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : key === "quote_volume" ? -1 : 1 }));
  };

  const fmtPrice = (n: number) => {
    if (!n && n !== 0) return "—";
    if (n >= 1000) return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (n >= 1) return n.toFixed(4);
    return n.toPrecision(4);
  };
  const fmtVol = (v: number) => {
    if (!v && v !== 0) return "—";
    if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
    if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
    if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
    return `$${v.toFixed(0)}`;
  };
  const fmtRate = (r?: number) => r === undefined ? "—" : `${r > 0 ? "+" : ""}${(r * 100).toFixed(4)}%`;
  const updated = data?.updated_at ? new Date(data.updated_at * 1000).toLocaleTimeString() : "—";
  const baseName = (s: string) => s.replace(/USDT$/, "");

  const Arrow = ({ on }: { on: boolean }) => (
    <span className={`inline-block ml-0.5 align-middle ${on ? "text-gold" : "text-ink-mute opacity-30"}`}>
      {sort.key === "price" || sort.key === "quote_volume" || sort.key === "change_pct" ? (sort.dir === -1 ? "▼" : "▲") : "▼"}
    </span>
  );

  return (
    <div className="p-5 space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2.5">
          <I.Market className="text-gold" size={20} />
          <span className="font-mono text-[15px] font-bold tracking-wide text-ink">{t("markets.title")}</span>
          <span className="pill pill-green"><span className="dot dot-green live" /> {t("markets.live")}</span>
          <span className="pill pill-dim">{data?.quote ?? "USDT"} 现货 · {total} 交易对</span>
        </div>
        <span className="prefix ml-auto">每 30s 自动刷新 · 最后 <span className="text-ink-dim tabular">{updated}</span></span>
        <button onClick={load} className="btn-ghost py-1 px-2.5 text-[12px]"><I.Refresh size={11} /> 刷新</button>
      </div>

      {/* 妖币雷达：启动前·埋伏（量在价先） / 起飞中·追涨高风险 */}
      <div className="glass overflow-hidden" style={{ borderRadius: 12 }}>
        <div className="px-4 pt-3.5 pb-1">
          <div className="flex items-center gap-2 flex-wrap">
            <I.Flame className="text-gold" size={16} />
            <span className="font-mono text-[13px] tracking-wider text-ink">妖币雷达 · Monster Radar</span>
            {env && <span className="pill pill-gold text-[10px]" title="大盘环境（BTC 30日走势）">环境: {env}</span>}
            <div className="ml-auto flex items-center gap-2">
              <span className="pill pill-dim text-[10px]">埋伏 {ign.length} · 起飞 {tk.length} · 每 5 分钟重扫</span>
              <button onClick={() => fetchRadar(mode, true)} disabled={mLoading} className="btn-ghost py-1 px-2.5 text-[11px]">
                <I.Refresh size={11} className={mLoading ? "animate-spin" : ""} /> {mLoading ? "扫描中…" : "强制重扫"}
              </button>
            </div>
          </div>
          {/* 模式切换 */}
          <div className="flex items-center gap-1.5 mt-2">
            <button onClick={() => { setMode("ignition"); setMSide("ALL"); setMLimit(24); }}
              className={`px-3.5 py-1.5 rounded-md font-mono text-[12px] tracking-wide border transition-colors
                ${mode === "ignition" ? "bg-gold text-canvas border-gold font-semibold" : "border-transparent text-ink-dim hover:text-ink"}`}>
              启动前 · 埋伏窗口 <span className="ml-0.5 opacity-70">({ign.length})</span>
            </button>
            <button onClick={() => { setMode("takeoff"); setMSide("ALL"); setMLimit(24); }}
              className={`px-3.5 py-1.5 rounded-md font-mono text-[12px] tracking-wide border transition-colors
                ${mode === "takeoff" ? "bg-gold text-canvas border-gold font-semibold" : "border-transparent text-ink-dim hover:text-ink"}`}>
              起飞中 · 追涨高风险 <span className="ml-0.5 opacity-70">({tk.length})</span>
            </button>
            <span className="prefix ml-auto hidden md:inline">
              {mode === "ignition"
                ? "妖币启动前 = 低位放量吸筹、价被压制 → 点火前埋伏（小仓+破位止损）"
                : "妖币 = 已暴涨数倍币 → 仅跟踪，追高风险大，非启动前埋伏"}
            </span>
          </div>
          <p className="text-[11px] text-ink-dim mt-1.5 leading-relaxed font-mono">
            {mode === "ignition" ? (
              <>日线 90 日量价：90日位置 + 放量(量在价先) + 价格压制 + 底部抬升四因子共振 · 现货只能 <span className="text-green">买入 / 卖出持仓</span>，做空需 U 本位合约 · 无链上筹码数据，属价量近似</>
            ) : (
              <>已启动的暴涨币跟踪：7D / 30D / 距 90 日高点 · 现货只能 <span className="text-green">买入 / 卖出持仓</span>，做空需 U 本位合约 · 明示追高风险</>
            )}
          </p>
          {mErr && <div className="rounded-md border border-red/40 bg-red/5 px-3 py-1.5 text-[11.5px] text-red font-mono mt-2">{mErr}</div>}
        </div>

        {/* 方向过滤 chips（按当前模式集合） */}
        <div className="flex items-center gap-1.5 px-4 pb-1.5 pt-1 flex-wrap">
          {(["ALL", "LONG", "WATCH_SHORT", "WATCH"] as const)
            .filter((k) => k === "ALL" || radarRows.some((m) => m.side === k))
            .map((k) => (
              <button key={k} onClick={() => { setMSide(k); setMLimit(24); }}
                className={`px-3 py-1 rounded-md font-mono text-[11px] tracking-wider transition-colors border
                  ${mSide === k ? "bg-elevated text-gold border-line" : "border-transparent text-ink-dim hover:text-ink"}`}>
                {k === "ALL" ? `全部 (${radarRows.length})` : SIDE_META[k].label}
              </button>
            ))}
        </div>

        {/* 表头 */}
        <div className="grid items-center px-4 py-2 border-t border-b border-line text-[10px] font-mono tracking-[0.08em] text-ink-dim"
          style={{ gridTemplateColumns: RADAR_COLS[mode].tpl }}>
          {RADAR_COLS[mode].head.map((h) => <div key={h}>{h}</div>)}
        </div>

        {mLoading && radarRows.length === 0 ? (
          <div className="p-4 space-y-2">{Array.from({ length: 6 }).map((_, i) => <div key={i} className="shimmer h-9" />)}</div>
        ) : mRows.length === 0 ? (
          <div className="p-8 text-center text-ink-dim text-[12.5px] font-mono">
            {mLoading ? "扫描中…（冷缓存约需 5-10s 拉全市场日线）"
              : mode === "ignition" ? "暂无启动前候选：市场普涨/普跌时底部吸筹信号少，属正常（宁缺毋滥）"
                : "暂无起飞中妖币（当前无显著暴涨币，或接口不可用）"}
          </div>
        ) : (
          mRows.map((m) => {
            const side = SIDE_META[m.side];
            const isIgn = mode === "ignition";
            return (
              <div key={m.symbol} onClick={() => onTrade?.(m.symbol)}
                className="grid items-center px-4 py-2.5 border-b border-line/60 last:border-0 hover:bg-elevated/40 transition-colors cursor-pointer group"
                style={{ gridTemplateColumns: RADAR_COLS[mode].tpl }}>
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-semibold text-ink group-hover:text-gold">{baseName(m.symbol)}</span>
                    <span className="font-mono text-[9px] text-ink-mute">/USDT</span>
                    <span className={`pill ${m.side === "LONG" ? "pill-green" : m.side === "WATCH_SHORT" ? "pill-red" : "pill-dim"} text-[9px]`} title={m.tag}>{m.tag}</span>
                    {isIgn && m.floor_rising && <span className="pill pill-dim text-[9px]" title="近5日低点高于前期平台">底抬</span>}
                  </div>
                  <div className="font-mono text-[9.5px] text-ink-mute truncate mt-0.5" title={m.note}>{m.note}</div>
                </div>
                <div className="font-mono tabular text-ink text-[12px]">{fmtPrice(m.price)}</div>

                {isIgn ? (
                  <>
                    <div className={`font-mono tabular text-[12px] ${(m.change3d_pct ?? 0) >= 0 ? "up" : "down"}`}>{m.change3d_pct! >= 0 ? "+" : ""}{m.change3d_pct!.toFixed(1)}%</div>
                    <div className={`font-mono tabular text-[12px] ${(m.change30d_pct ?? 0) >= 0 ? "up" : "down"}`}>{m.change30d_pct! >= 0 ? "+" : ""}{m.change30d_pct!.toFixed(0)}%</div>
                    <div className="font-mono tabular text-ink-dim text-[11.5px]">
                      分位 {m.position_pct?.toFixed(0)}%{m.position_pct! <= 30 ? " · 贴底" : m.position_pct! <= 45 ? " · 低位" : " · 中位"}
                    </div>
                  </>
                ) : (
                  <>
                    <div className={`font-mono tabular text-[12px] ${(m.change7d_pct ?? 0) >= 0 ? "up" : "down"}`}>{m.change7d_pct! >= 0 ? "+" : ""}{m.change7d_pct!.toFixed(1)}%</div>
                    <div className={`font-mono tabular text-[12px] ${(m.change30d_pct ?? 0) >= 0 ? "up" : "down"}`}>{m.change30d_pct! >= 0 ? "+" : ""}{m.change30d_pct!.toFixed(0)}%</div>
                    <div className={`font-mono tabular text-[12px] ${(m.drawdown_pct ?? 0) < 0 ? "down" : "text-ink-dim"}`}>{m.drawdown_pct!.toFixed(1)}%</div>
                    <div className="font-mono tabular text-ink-dim text-[11.5px]">{fmtVol(m.quote_volume)}</div>
                  </>
                )}

                <div className="font-mono tabular text-[12px] text-ink">{m.vol_ratio >= 1 ? "+" : ""}{m.vol_ratio.toFixed(1)}x</div>
                <div><span className={`pill ${side.cls} text-[10px]`}>{side.label}</span></div>
                <div className="flex items-center gap-1.5">
                  {m.side === "LONG" && (
                    <>
                      <button onClick={(e) => { e.stopPropagation(); onOrder?.(m.symbol, "spot-long"); }}
                        className="btn-ghost text-[10.5px] py-1 border-green/40 text-green hover:border-green"><I.Check size={10} /> 现货买入</button>
                      <button onClick={(e) => { e.stopPropagation(); onOrder?.(m.symbol, "futures-long"); }}
                        className="btn-ghost text-[10.5px] py-1"><I.Bolt size={10} /> 合约做多</button>
                    </>
                  )}
                  {m.side === "WATCH_SHORT" && (
                    <button onClick={(e) => { e.stopPropagation(); onOrder?.(m.symbol, "futures-short"); }}
                      className="btn-ghost text-[10.5px] py-1 border-red/40 text-red hover:border-red"><I.Bolt size={10} /> 合约做空</button>
                  )}
                  {m.side === "WATCH" && (
                    <button onClick={(e) => { e.stopPropagation(); onTrade?.(m.symbol); }}
                      className="btn-ghost text-[10.5px] py-1"><I.Search size={10} /> 查看</button>
                  )}
                </div>
              </div>
            );
          })
        )}
        {radarRows.length > mLimit && (
          <div className="px-4 py-2 border-t border-line text-center">
            <button onClick={() => setMLimit((n) => n + 24)} className="text-gold font-mono text-[11px] hover:underline">
              加载更多 ({radarRows.length - mLimit} 隐藏)
            </button>
          </div>
        )}
      </div>

      {/* 智能异动信号（全市场扫描） */}
      {data && data.signals && data.signals.length > 0 && (
        <div className="glass overflow-hidden" style={{ borderRadius: 12 }}>
          <div className="flex items-center gap-2 px-4 pt-3 pb-1">
            <I.Bolt size={13} className="text-gold" />
            <span className="font-mono text-[12px] tracking-wider text-ink">智能异动扫描</span>
            <span className="pill pill-dim text-[10px]">全市场 · 成交额前 150 · 波动/资金费率异常</span>
            <span className="pill pill-gold ml-auto text-[10px]">{data.signals.length} 条</span>
          </div>
          <div className="grid items-center px-4 py-2 mt-1.5 border-b border-line text-[10px] font-mono tracking-[0.08em] text-ink-dim"
            style={{ gridTemplateColumns: "1.4fr 1fr 1fr 1.1fr 1fr 0.9fr 1.9fr 0.8fr" }}>
            <div>标的</div><div>现价</div><div>24h 涨跌</div><div>成交额</div><div>资金费率</div><div>方向</div><div>触发依据</div><div>强度</div>
          </div>
          {data.signals.map((r, i) => (
            <div key={r.symbol + i} onClick={() => onTrade?.(r.symbol)}
              className="grid items-center px-4 py-2.5 border-b border-line/60 last:border-0 hover:bg-elevated/40 transition-colors cursor-pointer group"
              style={{ gridTemplateColumns: "1.4fr 1fr 1fr 1.1fr 1fr 0.9fr 1.9fr 0.8fr" }}>
              <div className="flex items-center gap-2 min-w-0">
                <span className="font-mono font-semibold text-ink group-hover:text-gold">{baseName(r.symbol)}</span>
                <span className="font-mono text-[10px] text-ink-mute">/USDT</span>
              </div>
              <div className="font-mono tabular text-ink text-[12.5px]">{fmtPrice(r.price)}</div>
              <div className={`font-mono tabular text-[12.5px] ${up(r.change_pct) ? "up" : "down"}`}>
                {up(r.change_pct) ? "+" : ""}{r.change_pct.toFixed(2)}%
              </div>
              <div className="font-mono tabular text-ink-dim text-[11.5px]">{fmtVol(r.volume)}</div>
              <div className={`font-mono tabular text-[11.5px] ${(r.funding_rate ?? 0) >= 0 ? "text-green" : "text-red"}`}>{fmtRate(r.funding_rate)}</div>
              <div><span className={`pill ${up(r.change_pct) ? "pill-green" : "pill-red"}`}>{r.direction ?? (up(r.change_pct) ? "做多" : "做空")}</span></div>
              <div className="font-mono text-[11px] text-ink-dim truncate" title={r.reason}>{r.reason ?? "—"}</div>
              <div className="font-mono tabular text-gold text-[12.5px]">{(r.score ?? 0).toFixed(1)}</div>
            </div>
          ))}
        </div>
      )}

      {/* 全市场交易对表 */}
      <div className="glass overflow-hidden" style={{ borderRadius: 12 }}>
        {/* Toolbar */}
        <div className="flex items-center gap-3 flex-wrap px-4 py-2.5 border-b border-line">
          <div className="flex items-center gap-1.5">
            {([["all", `全部 (${data?.total ?? 0})`], ["gainers", "涨幅榜"], ["losers", "跌幅榜"]] as const).map(([t, label]) => (
              <button key={t} onClick={() => switchTab(t)}
                className={`px-3 py-1.5 rounded-md font-mono text-[11.5px] tracking-wider transition-colors border
                  ${tab === t ? "bg-elevated text-gold border-line" : "border-transparent text-ink-dim hover:text-ink"}`}>
                {label}
              </button>
            ))}
          </div>
          <div className="ml-auto relative">
            <I.Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-mute" />
            <input value={q} onChange={(e) => { setQ(e.target.value); setLimit(PAGE); }} placeholder="搜任意交易对：BTC / PEPE / CAT…"
              className="field pl-8 w-64 py-1.5" />
          </div>
        </div>

        {/* Table head */}
        <div className="grid items-center px-4 py-2.5 border-b border-line text-[10px] font-mono tracking-[0.08em] text-ink-dim select-none"
          style={{ gridTemplateColumns: "1.7fr 1fr 1.1fr 1.2fr 1fr 1fr" }}>
          <div>标的</div>
          <button onClick={() => toggleSort("price")} className="text-left hover:text-gold">现价 <Arrow on={sort.key === "price"} /></button>
          <button onClick={() => toggleSort("change_pct")} className="text-left hover:text-gold">24h 涨跌 <Arrow on={sort.key === "change_pct"} /></button>
          <button onClick={() => toggleSort("quote_volume")} className="text-left hover:text-gold">24h 成交额 <Arrow on={sort.key === "quote_volume"} /></button>
          <div>24h 最高</div><div>24h 最低</div>
        </div>

        {loading && rows.length === 0 ? (
          <div className="p-4 space-y-2">{Array.from({ length: 8 }).map((_, i) => <div key={i} className="shimmer h-9" />)}</div>
        ) : rows.length === 0 ? (
          <div className="p-10 text-center text-ink-dim text-[13px]">
            {q ? `在 ${total} 个交易对里没搜到「${q}」。试试 BTC / ETH / 币名全称。` : "暂无行情数据（接口可能不可用）。"}
          </div>
        ) : (
          visible.map((r) => (
            <div key={r.symbol} onClick={() => onTrade?.(r.symbol)}
              className="grid items-center px-4 py-2.5 border-b border-line/60 last:border-0 hover:bg-elevated/40 transition-colors cursor-pointer group"
              style={{ gridTemplateColumns: "1.7fr 1fr 1.1fr 1.2fr 1fr 1fr" }}>
              <div className="flex items-center gap-2 min-w-0">
                <span className="font-mono font-semibold text-ink group-hover:text-gold transition-colors">{baseName(r.symbol)}</span>
                <span className="font-mono text-[10px] text-ink-mute">/USDT</span>
              </div>
              <div className="font-mono tabular text-ink text-[12.5px]">{fmtPrice(r.price)}</div>
              <div className={`font-mono tabular text-[12.5px] ${up(r.change_pct) ? "up" : "down"}`}>
                {up(r.change_pct) ? "+" : ""}{r.change_pct.toFixed(2)}%
              </div>
              <div className="font-mono tabular text-ink-dim text-[11.5px]">{fmtVol(r.quote_volume)}</div>
              <div className="font-mono tabular text-ink-dim text-[11.5px]">{r.high > 0 ? fmtPrice(r.high) : "—"}</div>
              <div className="font-mono tabular text-ink-dim text-[11.5px]">{r.low > 0 ? fmtPrice(r.low) : "—"}</div>
            </div>
          ))
        )}

        <div className="px-4 py-2.5 flex items-center justify-between font-mono text-[10.5px] text-ink-mute">
          <span>按 {SORT_META[sort.key]} {sort.dir === -1 ? "降序" : "升序"} · 点击列头排序</span>
          <div className="flex items-center gap-3">
            <span>展示 {visible.length} / {rows.length}{rows.length < total ? `（共 ${total} 个 ${data?.quote ?? "USDT"} 对，按成交额取前 400）` : ""}</span>
            {visible.length < rows.length && (
              <button onClick={() => setLimit((n) => n + PAGE)} className="text-gold hover:underline">加载更多 +{PAGE}</button>
            )}
          </div>
        </div>
      </div>

      {/* 24H 领涨 / 领跌 */}
      {data?.movers && (data.movers.gainers.length > 0 || data.movers.losers.length > 0) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="glass p-4" style={{ borderRadius: 12 }}>
            <div className="flex items-center gap-2 mb-3">
              <I.Arrow size={12} className="text-gold" />
              <span className="font-mono text-[12px] tracking-wider text-ink">24H 领涨</span>
              <span className="prefix ml-auto">全市场 · 流动性过滤</span>
            </div>
            <div className="space-y-1.5">
              {data.movers.gainers.map((m, i) => (
                <button key={i} onClick={() => onTrade?.(m.symbol)}
                  className="w-full flex items-center gap-2 rounded-lg border border-line bg-card/40 px-3 py-2 hover:border-gold/40 transition-colors">
                  <span className="font-mono text-[10px] text-ink-mute w-4">#{i + 1}</span>
                  <span className="font-mono text-[12.5px] text-ink flex-1 text-left">{baseName(m.symbol)}<span className="text-ink-mute text-[10px]">/USDT</span></span>
                  <span className="font-mono tabular text-[11.5px] text-ink-dim">{fmtPrice(m.price)}</span>
                  <span className="font-mono tabular text-[12.5px] up w-20 text-right">+{m.change_pct.toFixed(2)}%</span>
                </button>
              ))}
            </div>
          </div>
          <div className="glass p-4" style={{ borderRadius: 12 }}>
            <div className="flex items-center gap-2 mb-3">
              <I.Arrow size={12} className="rotate-180 text-gold" />
              <span className="font-mono text-[12px] tracking-wider text-ink">24H 领跌</span>
              <span className="prefix ml-auto">全市场 · 流动性过滤</span>
            </div>
            <div className="space-y-1.5">
              {data.movers.losers.map((m, i) => (
                <button key={i} onClick={() => onTrade?.(m.symbol)}
                  className="w-full flex items-center gap-2 rounded-lg border border-line bg-card/40 px-3 py-2 hover:border-gold/40 transition-colors">
                  <span className="font-mono text-[10px] text-ink-mute w-4">#{i + 1}</span>
                  <span className="font-mono text-[12.5px] text-ink flex-1 text-left">{baseName(m.symbol)}<span className="text-ink-mute text-[10px]">/USDT</span></span>
                  <span className="font-mono tabular text-[11.5px] text-ink-dim">{fmtPrice(m.price)}</span>
                  <span className="font-mono tabular text-[12.5px] down w-20 text-right">{m.change_pct.toFixed(2)}%</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Data freshness hint */}
      <div className="font-mono text-[10.5px] text-ink-mute flex items-center justify-between px-1">
        <span>数据源: Binance 公开 API 全量 24h ticker · 点击任意交易对跳转 Binance CEX 下单 · 公开行情无需 Key</span>
        <span className="tabular">updated_at: {updated}</span>
      </div>
    </div>
  );
}