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
const SORT_META: Record<SortKey, string> = { price: "markets.h.price", change_pct: "markets.h.change", quote_volume: "markets.h.quotevol" };

type OrderMode = "spot-long" | "futures-long" | "futures-short";
/* 妖币雷达行：ignition(启动前) 与 takeoff(起飞中) 共用，可选字段按模式出现 */
type RadarRow = {
  symbol: string; price: number; quote_volume: number; vol_ratio: number;
  tag: string; side: "LONG" | "WATCH_SHORT" | "WATCH"; note: string; score: number;
  change7d_pct?: number; change30d_pct?: number; drawdown_pct?: number;
  change3d_pct?: number; position_pct?: number; floor_rising?: boolean;
};
const SIDE_META: Record<RadarRow["side"], { label: string; cls: string }> = {
  LONG: { label: "markets.sideLong", cls: "pill-green" },
  WATCH_SHORT: { label: "markets.sideShort", cls: "pill-red" },
  WATCH: { label: "markets.sideWatch", cls: "pill-dim" },
};
const RADAR_COLS = {
  ignition: { tpl: "2.2fr 0.9fr 0.9fr 0.9fr 1fr 0.9fr 1fr 2.3fr", head: ["markets.col.symbol", "markets.h.price", "3D", "30D", "markets.col.pos90", "markets.col.volratio", "markets.col.verdict", "markets.col.action"] },
  takeoff:  { tpl: "2fr 0.9fr 0.9fr 0.9fr 0.9fr 1.1fr 0.8fr 1fr 2.2fr", head: ["markets.col.symbol", "markets.h.price", "7D", "30D", "markets.col.fromhigh", "markets.h.quotevol", "markets.col.surge", "markets.col.verdict", "markets.col.action"] },
} as const;

export function MarketsView({ onTrade, onOrder, onAnalyze }: {
  onTrade?: (symbol: string) => void;
  onOrder?: (symbol: string, mode: OrderMode) => void;
  onAnalyze?: (symbol: string) => void;
}) {
  const t = useT();
  const [data, setData] = useState<MarketData | null>(null);
  const [tab, setTab] = useState<"all" | "gainers" | "losers">("all");
  const [q, setQ] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "quote_volume", dir: -1 });
  const [loading, setLoading] = useState(true);

  // —— 行情综述：市场宽度 / 资金费率拥挤 / 24h 成交额热度 ——
  type FundingItem = { symbol: string; price: number; change_pct: number; funding_rate: number; direction: string; crowded: boolean };
  type VolItem = { symbol: string; price: number; change_pct: number; quote_volume: number };
  type OverviewData = {
    breadth?: { advancers: number; decliners: number; unchanged: number; up_ratio: number; avg_abs_chg: number; extreme_count: number; total: number };
    funding?: { long_crowded: FundingItem[]; short_crowded: FundingItem[] };
    volume_top?: VolItem[];
    error?: string;
  };
  const [ov, setOv] = useState<OverviewData | null>(null);
  const loadOverview = async () => {
    try { setOv(await api.marketOverview()); } catch { /* 网络失败保持旧数据 */ }
  };

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
  useEffect(() => {
    load(); loadOverview();
    const t = setInterval(() => { load(); loadOverview(); }, 30_000);
    return () => clearInterval(t);
  }, []);
  // 每秒 tick，驱动「N 秒前」实时刷新角标
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  useEffect(() => {
    fetchRadar("ignition"); fetchRadar("takeoff");
    const t = setInterval(() => { fetchRadar("ignition"); fetchRadar("takeoff"); }, 180_000);
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
  const updated = data?.updated_at ? (() => {
    const secs = Math.max(0, Math.floor((now / 1000) - data.updated_at));
    if (secs < 1) return t("markets.updatedNow");
    if (secs < 60) return t("markets.updatedAgo", { n: secs });
    const mins = Math.floor(secs / 60);
    if (mins < 60) return t("markets.updatedMinAgo", { n: mins });
    return new Date(data.updated_at * 1000).toLocaleTimeString();
  })() : "—";
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
          <span className="pill pill-dim">{data?.quote ?? "USDT"} {t("markets.spotPairs", { total })}</span>
        </div>
        <span className="prefix ml-auto">{t("markets.autoRefreshPrefix")} <span className="text-ink-dim tabular">{updated}</span></span>
        <button onClick={() => { load(); loadOverview(); }} className="btn-ghost py-1 px-2.5 text-[12px]"><I.Refresh size={11} /> {t("markets.refresh")}</button>
      </div>

      {/* 妖币雷达：启动前·埋伏（量在价先） / 起飞中·追涨高风险 */}
      <div className="glass overflow-hidden" style={{ borderRadius: 12 }}>
        <div className="px-4 pt-3.5 pb-1">
          <div className="flex items-center gap-2 flex-wrap">
            <I.Flame className="text-gold" size={16} />
            <span className="font-mono text-[13px] tracking-wider text-ink">{t("markets.radarTitle")}</span>
            {env && <span className="pill pill-gold text-[10px]" title={t("markets.envTitle")}>{t("markets.env", { env })}</span>}
            <div className="ml-auto flex items-center gap-2">
              <span className="pill pill-dim text-[10px]">{t("markets.radarCounts", { ign: ign.length, tk: tk.length })}</span>
              <button onClick={() => fetchRadar(mode, true)} disabled={mLoading} className="btn-ghost py-1 px-2.5 text-[11px]">
                <I.Refresh size={11} className={mLoading ? "animate-spin" : ""} /> {mLoading ? t("markets.scanning") : t("markets.forceRescan")}
              </button>
            </div>
          </div>
          {/* 模式切换 */}
          <div className="flex items-center gap-1.5 mt-2">
            <button onClick={() => { setMode("ignition"); setMSide("ALL"); setMLimit(24); }}
              className={`px-3.5 py-1.5 rounded-md font-mono text-[12px] tracking-wide border transition-colors
                ${mode === "ignition" ? "bg-gold text-canvas border-gold font-semibold" : "border-transparent text-ink-dim hover:text-ink"}`}>
              {t("markets.modeIgnition")} <span className="ml-0.5 opacity-70">({ign.length})</span>
            </button>
            <button onClick={() => { setMode("takeoff"); setMSide("ALL"); setMLimit(24); }}
              className={`px-3.5 py-1.5 rounded-md font-mono text-[12px] tracking-wide border transition-colors
                ${mode === "takeoff" ? "bg-gold text-canvas border-gold font-semibold" : "border-transparent text-ink-dim hover:text-ink"}`}>
              {t("markets.modeTakeoff")} <span className="ml-0.5 opacity-70">({tk.length})</span>
            </button>
            <span className="prefix ml-auto hidden md:inline">
              {mode === "ignition" ? t("markets.ignHint") : t("markets.takeoffHint")}
            </span>
          </div>
          <p className="text-[11px] text-ink-dim mt-1.5 leading-relaxed font-mono">
            {mode === "ignition" ? (
              <>{t("markets.ignDesc1")}<span className="text-green">{t("markets.buySellSpot")}</span>{t("markets.ignDesc2")}</>
            ) : (
              <>{t("markets.takeoffDesc1")}<span className="text-green">{t("markets.buySellSpot")}</span>{t("markets.takeoffDesc2")}</>
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
                {k === "ALL" ? t("markets.all", { n: radarRows.length }) : t(SIDE_META[k].label)}
              </button>
            ))}
        </div>

        {/* 表头 */}
        <div className="grid items-center px-4 py-2 border-t border-b border-line text-[10px] font-mono tracking-[0.08em] text-ink-dim"
          style={{ gridTemplateColumns: RADAR_COLS[mode].tpl }}>
          {RADAR_COLS[mode].head.map((h) => <div key={h}>{t(h)}</div>)}
        </div>

        {mLoading && radarRows.length === 0 ? (
          <div className="p-4 space-y-2">{Array.from({ length: 6 }).map((_, i) => <div key={i} className="shimmer h-9" />)}</div>
        ) : mRows.length === 0 ? (
          <div className="p-8 text-center text-ink-dim text-[12.5px] font-mono">
            {mLoading ? t("markets.scanningCold")
              : mode === "ignition" ? t("markets.emptyIgn")
                : t("markets.emptyTakeoff")}
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
                    {isIgn && m.floor_rising && <span className="pill pill-dim text-[9px]" title={t("markets.floorRisingTitle")}>{t("markets.floorRising")}</span>}
                  </div>
                  <div className="font-mono text-[9.5px] text-ink-mute truncate mt-0.5" title={m.note}>{m.note}</div>
                </div>
                <div className="font-mono tabular text-ink text-[12px]">{fmtPrice(m.price)}</div>

                {isIgn ? (
                  <>
                    <div className={`font-mono tabular text-[12px] ${(m.change3d_pct ?? 0) >= 0 ? "up" : "down"}`}>{m.change3d_pct! >= 0 ? "+" : ""}{m.change3d_pct!.toFixed(1)}%</div>
                    <div className={`font-mono tabular text-[12px] ${(m.change30d_pct ?? 0) >= 0 ? "up" : "down"}`}>{m.change30d_pct! >= 0 ? "+" : ""}{m.change30d_pct!.toFixed(0)}%</div>
                    <div className="font-mono tabular text-ink-dim text-[11.5px]">
                      {t("markets.positionPrefix")}{m.position_pct?.toFixed(0)}%{m.position_pct! <= 30 ? t("markets.posLow") : m.position_pct! <= 45 ? t("markets.posMidLow") : t("markets.posMid")}
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
                <div><span className={`pill ${side.cls} text-[10px]`}>{t(side.label)}</span></div>
                <div className="flex items-center gap-1.5">
                  <button onClick={(e) => { e.stopPropagation(); onAnalyze?.(m.symbol); }}
                    className="btn-ghost text-[10.5px] py-1" title={t("markets.analyze")}><I.Search size={10} /> {t("markets.analyze")}</button>
                  {m.side === "LONG" && (
                    <>
                      <button onClick={(e) => { e.stopPropagation(); onOrder?.(m.symbol, "spot-long"); }}
                        className="btn-ghost text-[10.5px] py-1 border-green/40 text-green hover:border-green"><I.Check size={10} /> {t("markets.spotBuy")}</button>
                      <button onClick={(e) => { e.stopPropagation(); onOrder?.(m.symbol, "futures-long"); }}
                        className="btn-ghost text-[10.5px] py-1"><I.Bolt size={10} /> {t("markets.futuresLong")}</button>
                    </>
                  )}
                  {m.side === "WATCH_SHORT" && (
                    <button onClick={(e) => { e.stopPropagation(); onOrder?.(m.symbol, "futures-short"); }}
                      className="btn-ghost text-[10.5px] py-1 border-red/40 text-red hover:border-red"><I.Bolt size={10} /> {t("markets.futuresShort")}</button>
                  )}
                  {m.side === "WATCH" && (
                    <button onClick={(e) => { e.stopPropagation(); onTrade?.(m.symbol); }}
                      className="btn-ghost text-[10.5px] py-1"><I.Search size={10} /> {t("markets.view")}</button>
                  )}
                </div>
              </div>
            );
          })
        )}
        {radarRows.length > mLimit && (
          <div className="px-4 py-2 border-t border-line text-center">
            <button onClick={() => setMLimit((n) => n + 24)} className="text-gold font-mono text-[11px] hover:underline">
              {t("markets.loadMoreHidden", { n: radarRows.length - mLimit })}
            </button>
          </div>
        )}
      </div>

      {/* 行情综述：市场宽度 / 资金费率拥挤 / 24h 成交额热度 */}
      {ov && (ov.breadth || ov.funding || (ov.volume_top ?? []).length > 0) && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* 市场宽度 / 突发 */}
          <div className="glass p-4" style={{ borderRadius: 12 }}>
            <div className="flex items-center gap-2 mb-3">
              <I.Zap size={14} className="text-gold" />
              <span className="font-mono text-[12px] tracking-wider text-ink">{t("markets.breadthTitle")}</span>
              <span className="pill pill-dim ml-auto text-[10px]">{t("markets.breadthTotal", { n: ov.breadth?.total ?? 0 })}</span>
            </div>
            {ov.breadth ? (
              <>
                <div className="flex items-center gap-3 font-mono tabular text-[12px] flex-wrap">
                  <span className="text-green">{ov.breadth.advancers} <span className="text-ink-mute text-[9.5px]">{t("markets.breadthUp")}</span></span>
                  <span className="text-red">{ov.breadth.decliners} <span className="text-ink-mute text-[9.5px]">{t("markets.breadthDown")}</span></span>
                  <span className="text-ink-dim">{ov.breadth.unchanged} <span className="text-ink-mute text-[9.5px]">{t("markets.breadthFlat")}</span></span>
                </div>
                <div className="flex h-2 rounded overflow-hidden mt-2.5 bg-line">
                  <div className="bg-green" style={{ flex: Math.max(0.0001, ov.breadth.up_ratio) }} />
                  <div className="bg-red/70" style={{ flex: Math.max(0.0001, 1 - ov.breadth.up_ratio) }} />
                </div>
                <div className="grid grid-cols-3 gap-2 mt-3">
                  <div className="rounded-md border border-red/40 bg-red/5 px-2 py-1.5 text-center" title={t("markets.breadthExtremeTitle")}>
                    <div className="font-mono tabular text-[14px] text-red font-semibold">{ov.breadth.extreme_count}</div>
                    <div className="font-mono text-[9px] text-ink-mute">{t("markets.breadthExtreme")}</div>
                  </div>
                  <div className="rounded-md border border-line bg-card/40 px-2 py-1.5 text-center">
                    <div className="font-mono tabular text-[14px] text-ink">{(ov.breadth.up_ratio * 100).toFixed(0)}%</div>
                    <div className="font-mono text-[9px] text-ink-mute">{t("markets.breadthUpRatio")}</div>
                  </div>
                  <div className="rounded-md border border-line bg-card/40 px-2 py-1.5 text-center">
                    <div className="font-mono tabular text-[14px] text-ink">{ov.breadth.avg_abs_chg.toFixed(1)}%</div>
                    <div className="font-mono text-[9px] text-ink-mute">{t("markets.breadthAvgAbs")}</div>
                  </div>
                </div>
              </>
            ) : <div className="text-[11px] text-ink-dim font-mono">{t("markets.noData")}</div>}
          </div>

          {/* 资金费率拥挤度 */}
          <div className="glass p-4" style={{ borderRadius: 12 }}>
            <div className="flex items-center gap-2 mb-3">
              <I.Bolt size={14} className="text-gold" />
              <span className="font-mono text-[12px] tracking-wider text-ink">{t("markets.fundingTitle")}</span>
            </div>
            {ov.funding ? (
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <div className="font-mono text-[10px] tracking-wider text-green mb-1.5">{t("markets.longCrowded")}</div>
                  <div className="space-y-1">
                    {(ov.funding.long_crowded ?? []).map((f, i) => (
                      <button key={i} onClick={() => onTrade?.(f.symbol)}
                        className="w-full flex items-center gap-1.5 text-[11px] font-mono hover:bg-elevated/40 rounded px-1 py-0.5 transition-colors">
                        <span className="flex-1 text-ink truncate text-left">{baseName(f.symbol)}</span>
                        <span className={`tabular ${f.crowded ? "text-gold font-semibold" : "text-green"}`}>+{(f.funding_rate * 100).toFixed(3)}%</span>
                      </button>
                    ))}
                    {(ov.funding.long_crowded ?? []).length === 0 && <div className="text-[10.5px] text-ink-mute font-mono">{t("markets.noData")}</div>}
                  </div>
                </div>
                <div>
                  <div className="font-mono text-[10px] tracking-wider text-red mb-1.5">{t("markets.shortCrowded")}</div>
                  <div className="space-y-1">
                    {(ov.funding.short_crowded ?? []).map((f, i) => (
                      <button key={i} onClick={() => onTrade?.(f.symbol)}
                        className="w-full flex items-center gap-1.5 text-[11px] font-mono hover:bg-elevated/40 rounded px-1 py-0.5 transition-colors">
                        <span className="flex-1 text-ink truncate text-left">{baseName(f.symbol)}</span>
                        <span className={`tabular ${f.crowded ? "text-gold font-semibold" : "text-red"}`}>{((f.funding_rate ?? 0) * 100).toFixed(3)}%</span>
                      </button>
                    ))}
                    {(ov.funding.short_crowded ?? []).length === 0 && <div className="text-[10.5px] text-ink-mute font-mono">{t("markets.noData")}</div>}
                  </div>
                </div>
              </div>
            ) : <div className="text-[11px] text-ink-dim font-mono">{t("markets.noData")}</div>}
          </div>

          {/* 24h 成交额热度榜 */}
          <div className="glass p-4" style={{ borderRadius: 12 }}>
            <div className="flex items-center gap-2 mb-2">
              <I.Arrow size={14} className="text-gold" />
              <span className="font-mono text-[12px] tracking-wider text-ink">{t("markets.volumeTitle")}</span>
              <span className="pill pill-dim ml-auto text-[10px]">24h</span>
            </div>
            <div className="space-y-1">
              {(ov.volume_top ?? []).map((v, i) => (
                <button key={i} onClick={() => onTrade?.(v.symbol)}
                  className="w-full flex items-center gap-2 rounded-lg border border-line bg-card/40 px-2.5 py-1.5 hover:border-gold/40 transition-colors group">
                  <span className="font-mono text-[9.5px] text-ink-mute w-4">#{i + 1}</span>
                  <span className="font-mono text-[12px] text-ink flex-1 text-left truncate">{baseName(v.symbol)}</span>
                  <span className={`font-mono tabular text-[11.5px] ${v.change_pct >= 0 ? "up" : "down"}`}>{v.change_pct >= 0 ? "+" : ""}{v.change_pct.toFixed(1)}%</span>
                  <span className="font-mono tabular text-[11px] text-ink-dim hidden sm:inline">{fmtVol(v.quote_volume)}</span>
                  <span onClick={(e) => { e.stopPropagation(); onAnalyze?.(v.symbol); }}
                    className="btn-ghost px-1.5 py-0.5 text-[9.5px] opacity-0 group-hover:opacity-100"><I.Search size={9} /> {t("markets.analyze")}</span>
                </button>
              ))}
              {(ov.volume_top ?? []).length === 0 && <div className="text-[11px] text-ink-dim font-mono">{t("markets.noData")}</div>}
            </div>
          </div>
        </div>
      )}

      {/* 智能异动信号（全市场扫描） */}
      {data && data.signals && data.signals.length > 0 && (
        <div className="glass overflow-hidden" style={{ borderRadius: 12 }}>
          <div className="flex items-center gap-2 px-4 pt-3 pb-1">
            <I.Bolt size={13} className="text-gold" />
            <span className="font-mono text-[12px] tracking-wider text-ink">{t("markets.smartScan")}</span>
            <span className="pill pill-dim text-[10px]">{t("markets.scanPill")}</span>
            <span className="pill pill-gold ml-auto text-[10px]">{t("markets.signalCount", { n: data.signals.length })}</span>
          </div>
          <div className="grid items-center px-4 py-2 mt-1.5 border-b border-line text-[10px] font-mono tracking-[0.08em] text-ink-dim"
            style={{ gridTemplateColumns: "1.4fr 1fr 1fr 1.1fr 1fr 0.9fr 1.9fr 0.8fr" }}>
            <div>{t("markets.h.symbol")}</div><div>{t("markets.h.price")}</div><div>{t("markets.h.change")}</div><div>{t("markets.h.quotevol")}</div><div>{t("markets.h.funding")}</div><div>{t("markets.h.direction")}</div><div>{t("markets.h.reason")}</div><div>{t("markets.h.strength")}</div>
          </div>
          {data.signals.map((r, i) => (
            <div key={r.symbol + i} onClick={() => onTrade?.(r.symbol)}
              className="grid items-center px-4 py-2.5 border-b border-line/60 last:border-0 hover:bg-elevated/40 transition-colors cursor-pointer group"
              style={{ gridTemplateColumns: "1.4fr 1fr 1fr 1.1fr 1fr 0.9fr 1.9fr 0.8fr" }}>
              <div className="flex items-center gap-2 min-w-0">
                <span className="font-mono font-semibold text-ink group-hover:text-gold">{baseName(r.symbol)}</span>
                <span className="font-mono text-[10px] text-ink-mute">/USDT</span>
                <button onClick={(e) => { e.stopPropagation(); onAnalyze?.(r.symbol); }}
                  className="btn-ghost px-1.5 py-0.5 text-[9.5px]" title={t("markets.analyze")}><I.Search size={9} /> {t("markets.analyze")}</button>
              </div>
              <div className="font-mono tabular text-ink text-[12.5px]">{fmtPrice(r.price)}</div>
              <div className={`font-mono tabular text-[12.5px] ${up(r.change_pct) ? "up" : "down"}`}>
                {up(r.change_pct) ? "+" : ""}{r.change_pct.toFixed(2)}%
              </div>
              <div className="font-mono tabular text-ink-dim text-[11.5px]">{fmtVol(r.volume)}</div>
              <div className={`font-mono tabular text-[11.5px] ${(r.funding_rate ?? 0) >= 0 ? "text-green" : "text-red"}`}>{fmtRate(r.funding_rate)}</div>
              <div><span className={`pill ${up(r.change_pct) ? "pill-green" : "pill-red"}`}>{r.direction ?? (up(r.change_pct) ? t("markets.long") : t("markets.short"))}</span></div>
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
            {([["all", t("markets.all", { n: data?.total ?? 0 })], ["gainers", t("markets.gainers")], ["losers", t("markets.losers")]] as const).map(([key, label]) => (
              <button key={key} onClick={() => switchTab(key)}
                className={`px-3 py-1.5 rounded-md font-mono text-[11.5px] tracking-wider transition-colors border
                  ${tab === key ? "bg-elevated text-gold border-line" : "border-transparent text-ink-dim hover:text-ink"}`}>
                {label}
              </button>
            ))}
          </div>
          <div className="ml-auto relative">
            <I.Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-mute" />
            <input value={q} onChange={(e) => { setQ(e.target.value); setLimit(PAGE); }} placeholder={t("markets.searchPlaceholder")}
              className="field pl-8 w-64 py-1.5" />
          </div>
        </div>

        {/* Table head */}
        <div className="grid items-center px-4 py-2.5 border-b border-line text-[10px] font-mono tracking-[0.08em] text-ink-dim select-none"
          style={{ gridTemplateColumns: "1.7fr 1fr 1.1fr 1.2fr 1fr 1fr" }}>
          <div>{t("markets.h.symbol")}</div>
          <button onClick={() => toggleSort("price")} className="text-left hover:text-gold">{t("markets.h.price")} <Arrow on={sort.key === "price"} /></button>
          <button onClick={() => toggleSort("change_pct")} className="text-left hover:text-gold">{t("markets.h.change")} <Arrow on={sort.key === "change_pct"} /></button>
          <button onClick={() => toggleSort("quote_volume")} className="text-left hover:text-gold">{t("markets.h.quotevol")} <Arrow on={sort.key === "quote_volume"} /></button>
          <div>{t("markets.h.high")}</div><div>{t("markets.h.low")}</div>
        </div>

        {loading && rows.length === 0 ? (
          <div className="p-4 space-y-2">{Array.from({ length: 8 }).map((_, i) => <div key={i} className="shimmer h-9" />)}</div>
        ) : rows.length === 0 ? (
          <div className="p-10 text-center text-ink-dim text-[13px]">
            {q ? t("markets.searchEmpty", { total, q }) : t("markets.noData")}
          </div>
        ) : (
          visible.map((r) => (
            <div key={r.symbol} onClick={() => onTrade?.(r.symbol)}
              className="grid items-center px-4 py-2.5 border-b border-line/60 last:border-0 hover:bg-elevated/40 transition-colors cursor-pointer group"
              style={{ gridTemplateColumns: "1.7fr 1fr 1.1fr 1.2fr 1fr 1fr" }}>
              <div className="flex items-center gap-2 min-w-0">
                <span className="font-mono font-semibold text-ink group-hover:text-gold transition-colors">{baseName(r.symbol)}</span>
                <span className="font-mono text-[10px] text-ink-mute">/USDT</span>
                <button onClick={(e) => { e.stopPropagation(); onAnalyze?.(r.symbol); }}
                  className="btn-ghost px-1.5 py-0.5 text-[9.5px]" title={t("markets.analyze")}><I.Search size={9} /> {t("markets.analyze")}</button>
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
          <span>{t("markets.sortPrefix")} {t(SORT_META[sort.key])} {sort.dir === -1 ? t("markets.sort.desc") : t("markets.sort.asc")} · {t("markets.sort.hint")}</span>
          <div className="flex items-center gap-3">
            <span>{t("markets.showing", { v: visible.length, r: rows.length, total, quote: data?.quote ?? "USDT" })}</span>
            {visible.length < rows.length && (
              <button onClick={() => setLimit((n) => n + PAGE)} className="text-gold hover:underline">{t("markets.loadMore", { n: PAGE })}</button>
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
              <span className="font-mono text-[12px] tracking-wider text-ink">{t("markets.topGainers")}</span>
              <span className="prefix ml-auto">{t("markets.liqFilter")}</span>
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
              <span className="font-mono text-[12px] tracking-wider text-ink">{t("markets.topLosers")}</span>
              <span className="prefix ml-auto">{t("markets.liqFilter")}</span>
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
        <span>{t("markets.dataSource")}</span>
        <span className="tabular">updated_at: {updated}</span>
      </div>
    </div>
  );
}
