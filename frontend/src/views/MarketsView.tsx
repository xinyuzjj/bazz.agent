import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { I } from "../components/icons";
import { useT } from "../i18n/i18n";
import { subscribeTicks, useWsStatus, useLiveTick } from "../lib/live";
import {
  type Ticker, type Signal, type FutureRow, type EquityRow, type RadarRow, type OrderMode,
  type TracksData,
  SIDE_META, RADAR_COLS, STAGE_META, TRACK_COLS, fmtPrice, fmtVol, fmtRate, fmtAgo, baseName, UpdatedAgo,
  PosBar, useFlash, VolHeatRow, LsLine,
  SpotRow, FutRow, EquityCard, RadarLine, TrackLine, SignalRow,
} from "../components/MarketRows";

/* 行情 · MARKETS —— 全市场交易对浏览（不再固定 20 币）
 * 数据源 /api/market：
 *   signals  智能异动扫描（成交额前 150 交易对内，波动 / 资金费率异常）
 *   movers   全市场涨 / 跌幅 TOP（流动性过滤）
 *   all      全 USDT 现货对 24h 快照（最多 400，前端搜索 / 排序 / 加载更多）
 *   total    全 USDT 现货对总数
 * v1.4.0：价格实时化 —— 后端订阅币安 WS，本页行组件各自订阅自身标的的实时价，
 * 30s REST 轮询只负责聚合数据（信号/综述/雷达）。
 */

const PAGE = 120; // 每批展示行数（"加载更多"）
type SortKey = "price" | "change_pct" | "quote_volume";
const SORT_META: Record<SortKey, string> = { price: "markets.h.price", change_pct: "markets.h.change", quote_volume: "markets.h.quotevol" };

type MinTicker = { symbol: string; price: number; change_pct: number };
type MarketData = {
  signals: Signal[];
  movers?: { gainers: MinTicker[]; losers: MinTicker[] };
  all: Ticker[];
  total?: number;
  quote?: string;
  updated_at?: number;
};

/* ---------------- v1.5.3 Hero 大盘速览卡（实时 WS 价 + 24h 区间条） ---------------- */

const HERO_COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"] as const;

function HeroCoin({ base }: { base: Ticker }) {
  const t = useT();
  const tick = useLiveTick("spot", base.symbol);
  const price = tick ? tick.p : base.price;
  const chg = tick ? tick.c : base.change_pct;
  const qv = tick && tick.q ? tick.q : base.quote_volume;
  const low = tick && tick.l ? tick.l : base.low;
  const high = tick && tick.h ? tick.h : base.high;
  const flash = useFlash(price);
  const up = chg >= 0;
  return (
    <div className="glass p-3 flex flex-col gap-2 min-w-0" style={{ borderRadius: 12 }}>
      <div className="flex items-center justify-between gap-1">
        <span className="font-mono text-[12.5px] font-semibold text-ink">{baseName(base.symbol)}</span>
        <span className={`font-mono tabular text-[11.5px] font-semibold px-1.5 py-0.5 rounded ${up ? "text-green bg-green/10" : "text-red bg-red/10"}`}>
          {up ? "+" : ""}{chg.toFixed(2)}%
        </span>
      </div>
      <div className={`font-mono tabular text-[18px] font-semibold text-ink leading-none px-1 -mx-1 ${flash}`}>{fmtPrice(price)}</div>
      <PosBar low={low} high={high} price={price} />
      <div className="flex items-center justify-between font-mono text-[9.5px] text-ink-mute">
        <span>{t("markets.hero.vol24")} {fmtVol(qv)}</span>
        <span className="hidden md:inline tabular">{fmtPrice(low)} — {fmtPrice(high)}</span>
      </div>
    </div>
  );
}

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
  type FundingItem = { symbol: string; price: number; change_pct: number; funding_rate: number; direction: string; crowded: boolean; extreme?: boolean };
  type VolItem = { symbol: string; price: number; change_pct: number; quote_volume: number };
  type OverviewData = {
    breadth?: { advancers: number; decliners: number; unchanged: number; up_ratio: number; avg_abs_chg: number; extreme_count: number; total: number };
    funding?: { long_crowded: FundingItem[]; short_crowded: FundingItem[]; extremes?: FundingItem[]; flips?: { symbol: string; prev: number; last: number; to: string }[] };
    volume_top?: VolItem[];
    error?: string;
  };
  const [ov, setOv] = useState<OverviewData | null>(null);
  const loadOverview = async () => {
    try { setOv(await api.marketOverview()); } catch { /* 网络失败保持旧数据 */ }
  };

  // —— 市场维度：现货(spot) / 合约(futures) / 股票化代币合约(equity) ——
  type FutureRow = { symbol: string; price: number; change_pct: number; quote_volume: number; high: number; low: number; funding_rate: number };
  type EquityRow = FutureRow & { name?: string; leverage?: string };
  type FuturesData = { futures: FutureRow[]; equity: EquityRow[]; total_futures?: number; total_equity?: number; updated_at?: number; error?: string };
  const [dim, setDim] = useState<"spot" | "futures" | "equity">("spot");
  const [fd, setFd] = useState<FuturesData | null>(null);
  const [fErr, setFErr] = useState("");
  const [fq, setFq] = useState("");
  const [fLimit, setFLimit] = useState(60);
  const [fMode, setFMode] = useState<"all" | "gain" | "loser" | "funding">("all");
  const loadFutures = async () => {
    try { const d = await api.marketFutures(); if (d?.error) setFErr(d.error); else setFErr(""); setFd(d ?? null); }
    catch (e: any) { setFErr(e?.message ?? String(e)); }
  };

  // —— 妖币雷达 v2：四层模型全量扫描（一次 /api/market/radar，前端拆 takeoff/ignition） ——
  const [mode, setMode] = useState<"ignition" | "takeoff">("ignition");
  const [ign, setIgn] = useState<RadarRow[]>([]);
  const [tk, setTk] = useState<RadarRow[]>([]);
  const [stageCounts, setStageCounts] = useState<Record<string, number>>({});
  const [engine, setEngine] = useState("");
  const [env, setEnv] = useState("");
  const [mLoading, setMLoading] = useState(false);
  const [mErr, setMErr] = useState("");
  const [mSide, setMSide] = useState<"ALL" | RadarRow["side"]>("ALL");
  const [mLimit, setMLimit] = useState(24);

  // —— v1.5.3 重设计：Hero 大盘卡基准数据 + 侧栏资金费率面板 Tab ——
  const [fundTab, setFundTab] = useState<"ext" | "crowd">("ext");
  const heroBase = useMemo(
    () => HERO_COINS.map((s) => (data?.all ?? []).find((x) => x.symbol === s)).filter(Boolean) as Ticker[],
    [data],
  );
  // 雷达完整说明（收进 tooltip）
  const radarDesc = mode === "ignition"
    ? `${t("markets.ignDesc1")}${t("markets.buySellSpot")}${t("markets.ignDesc2")}`
    : `${t("markets.takeoffDesc1")}${t("markets.buySellSpot")}${t("markets.takeoffDesc2")}`;
  // 成交额热度：用于 bar 归一化
  const volMax = useMemo(
    () => (ov?.volume_top ?? []).reduce((m, v) => Math.max(m, v.quote_volume), 0),
    [ov],
  );

  const load = async () => {
    try { const d = await api.market(); setData(d); } finally { setLoading(false); }
  };
  const fetchRadar = async (force = false) => {
    setMLoading(true); setMErr("");
    try {
      const d: any = await api.marketRadar(force);
      setIgn(d?.ignition ?? []);
      setTk(d?.takeoff ?? []);
      setStageCounts(d?.stage_counts ?? {});
      setEngine(String(d?.engine ?? ""));
      if (d?.env?.regime) setEnv(d.env.regime);
      if (d?.error) setMErr(d.error);
    } catch (e: any) { setMErr(e?.message ?? String(e)); }
    finally { setMLoading(false); }
  };

  // —— v1.5.2 妖币追踪：启动前发现 → 结局验证（进行中 + 历史 + 战绩） ——
  const [tracks, setTracks] = useState<TracksData | null>(null);
  const loadTracks = async () => {
    try { setTracks(await api.marketRadarTracks()); } catch { /* 网络失败保持旧数据 */ }
  };

  // —— v1.5.0 爆仓流面板 + 多空比面板 ——
  type LiqRec = { ts: number; symbol: string; side: "SELL" | "BUY"; kind: "long" | "short"; price: number; qty: number; quote: number };
  type LiqData = { recent?: LiqRec[]; stats?: { long_count?: number; short_count?: number; long_quote?: number; short_quote?: number; total_quote?: number; window?: number }; ws?: { connected?: boolean }; error?: string };
  const [liq, setLiq] = useState<LiqData | null>(null);
  const loadLiq = async () => {
    try { setLiq(await api.marketLiquidations(60, 300)); } catch { /* 网络失败保持旧数据 */ }
  };
  type LsRow = { symbol: string; price: number; change_pct: number; quote_volume: number; top_ratio: number | null; top_prev: number | null; global_ratio: number | null; divergence: boolean };
  const [ls, setLs] = useState<LsRow[]>([]);
  const loadLs = async () => {
    try { const d: any = await api.marketLongshort(); if (!d?.error) setLs(d?.rows ?? []); } catch { /* 网络失败保持旧数据 */ }
  };
  useEffect(() => {
    loadLiq(); loadLs();
    const t = setInterval(() => { loadLiq(); loadLs(); }, 30_000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    loadTracks();
    const t = setInterval(() => loadTracks(), 60_000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    load(); loadOverview(); loadFutures();
    const t = setInterval(() => { load(); loadOverview(); loadFutures(); }, 30_000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    fetchRadar();
    const t = setInterval(() => fetchRadar(), 180_000);
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

  // —— 合约 / 股票化代币派生的展示数据 ——
  const futuresRows: FutureRow[] = useMemo(() => {
    const base = fd?.futures ?? [];
    let r = base;
    if (fMode === "gain") r = r.filter((x) => x.change_pct > 0);
    else if (fMode === "loser") r = r.filter((x) => x.change_pct < 0);
    else if (fMode === "funding") r = r.filter((x) => Math.abs(x.funding_rate) >= 0.0007);
    if (fq.trim()) {
      const s = fq.trim().toLowerCase();
      r = r.filter((x) => x.symbol.toLowerCase().includes(s) || x.symbol.replace(/USDT$/, "").toLowerCase().includes(s));
    }
    return r.slice(0, fLimit);
  }, [fd, fMode, fq, fLimit]);
  const futuresBreadth = useMemo(() => {
    const fs = fd?.futures ?? [];
    const up = fs.filter((x) => x.change_pct > 0).length;
    const down = fs.filter((x) => x.change_pct < 0).length;
    const crowded = fs.filter((x) => Math.abs(x.funding_rate) >= 0.001).length;
    const liq = fs.slice().sort((a, b) => b.quote_volume - a.quote_volume).slice(0, 3);
    return { up, down, crowded, total: fs.length, liq };
  }, [fd]);
  const equityRows: EquityRow[] = useMemo(() => fd?.equity ?? [], [fd]);
  const equityMovers = useMemo(() => {
    const e = fd?.equity ?? [];
    const sorted = [...e].sort((a, b) => b.change_pct - a.change_pct);
    return {
      gainers: sorted.filter((x) => x.change_pct > 0).slice(0, 3),
      losers: [...sorted].reverse().filter((x) => x.change_pct < 0).slice(0, 3),
    };
  }, [fd]);

  // —— v1.4.0 实时订阅：可见标的注册到 WS（行内 useLiveTick 自取实时价，行级微渲染） ——
  const wsStatus = useWsStatus();
  const spotSubKey = useMemo(() => visible.map((r) => r.symbol).join(","), [visible]);
  useEffect(() => {
    if (!spotSubKey) return;
    return subscribeTicks("spot", spotSubKey.split(","));
  }, [spotSubKey]);
  const futSubKey = useMemo(() => futuresRows.map((r) => r.symbol).join(","), [futuresRows]);
  useEffect(() => {
    if (!futSubKey) return;
    return subscribeTicks("futures", futSubKey.split(","));
  }, [futSubKey]);
  const eqSubKey = useMemo(() => equityRows.map((r) => r.symbol).join(","), [equityRows]);
  useEffect(() => {
    if (!eqSubKey) return;
    return subscribeTicks("futures", eqSubKey.split(","));
  }, [eqSubKey]);

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
          <span className={`pill ${wsStatus === "on" ? "pill-green" : "pill-dim"}`} title={t("markets.wsTitle")}>
            {wsStatus === "on" && <span className="dot dot-green live" />} {wsStatus === "on" ? t("markets.wsLive") : t("markets.wsOff")}
          </span>
          <span className="pill pill-dim">
            {dim === "equity"
              ? `${fd?.total_equity ?? 0} ${t("markets.dimEquityTag")}`
              : dim === "futures"
                ? `${fd?.total_futures ?? 0} ${t("markets.futPerp")}`
                : `${data?.quote ?? "USDT"} ${t("markets.spotPairs", { total })}`}
          </span>
        </div>
        <span className="prefix ml-auto">{t("markets.autoRefreshPrefix")} <span className="text-ink-dim tabular"><UpdatedAgo updatedAt={data?.updated_at} /></span></span>
        <button onClick={() => { load(); loadOverview(); loadFutures(); }} className="btn-ghost py-1 px-2.5 text-[12px]"><I.Refresh size={11} /> {t("markets.refresh")}</button>
      </div>

      {/* 维度切换：现货 / 合约 / 股票化代币合约（sticky：滚动时固定） */}
      <div className="sticky top-0 z-20 -mx-5 px-5 py-2 bg-canvas/85 backdrop-blur-md border-b border-line/40">
        <div className="flex items-center gap-1.5 rounded-lg bg-elevated/50 border border-line p-1 w-fit">
          {([
            ["spot", t("markets.dimSpot"), I.Market],
            ["futures", t("markets.dimFutures"), I.Bolt],
            ["equity", t("markets.dimEquity"), I.Star],
          ] as const).map(([key, label, Icon]) => (
            <button key={key} onClick={() => setDim(key)}
              className={`px-4 py-1.5 rounded-md font-mono text-[12px] tracking-wide transition-colors border flex items-center gap-1.5
                ${dim === key ? "bg-gold text-canvas border-gold font-semibold shadow-sm" : "border-transparent text-ink-dim hover:text-ink hover:bg-line/30"}`}>
              <Icon size={13} /> {label}
            </button>
          ))}
        </div>
      </div>

      {/* 股票化代币合约板块（商品化/传统资产永续） */}
      {dim === "equity" && (
        <div className="space-y-4">
          <div className="glass p-4" style={{ borderRadius: 12 }}>
            <div className="flex items-center gap-2 mb-1">
              <I.Star size={14} className="text-gold" />
              <span className="font-mono text-[13px] tracking-wider text-ink">{t("markets.equityTitle")}</span>
              <span className="pill pill-dim text-[10px]">{fd?.total_equity ?? 0} · {t("markets.equityTag")}</span>
            </div>
            <p className="text-[11px] text-ink-dim font-mono leading-relaxed">{t("markets.equityHint")}</p>
            {fErr && <div className="rounded-md border border-red/40 bg-red/5 px-3 py-1.5 text-[11.5px] text-red font-mono mt-2">{fErr}</div>}
            <div className="mt-3 grid grid-cols-2 sm:grid-cols-3 gap-3">
              {equityRows.length === 0 && (
                <div className="col-span-full p-6 text-center text-ink-dim text-[12.5px] font-mono">{t("markets.noData")}</div>
              )}
              {equityRows.map((e) => (
                <EquityCard key={e.symbol} e={e} onTrade={onTrade} />
              ))}
            </div>
          </div>

          {/* 股票领涨/领跌快览 */}
          {(equityMovers.gainers.length > 0 || equityMovers.losers.length > 0) && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {(["gainers", "losers"] as const).map((s) => {
                const list = equityMovers[s];
                if (!list.length) return null;
                return (
                  <div key={s} className="glass p-3 flex gap-2 items-center">
                    <span className={`font-mono text-[10px] tracking-wider px-2 py-1 rounded-md ${s === "gainers" ? "text-green bg-green/10" : "text-red bg-red/10"}`}>
                      {s === "gainers" ? t("markets.dimEquityGain") : t("markets.dimEquityLoss")}
                    </span>
                    <div className="flex-1 flex gap-3 overflow-hidden">
                      {list.map((e, i) => (
                        <button key={e.symbol} onClick={() => onTrade?.(e.symbol)} className="flex items-center gap-1 font-mono text-[11.5px] hover:text-gold">
                          <span className="text-ink">{baseName(e.symbol)}</span>
                          <span className={s === "gainers" ? "up" : "down"}>{e.change_pct >= 0 ? "+" : ""}{e.change_pct.toFixed(1)}%</span>
                        </button>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* 合约（USDT-M 永续）板块 */}
      {dim === "futures" && (
        <div className="space-y-4">
          {/* 合约大盘速览 */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="glass p-3 flex flex-col justify-center" style={{ borderRadius: 12 }}>
              <span className="font-mono text-[10px] tracking-wider text-ink-mute">{t("markets.futTotal")}</span>
              <span className="font-mono text-[22px] text-ink font-semibold mt-0.5">{futuresBreadth.total}</span>
            </div>
            <div className="glass p-3 flex flex-col justify-center" style={{ borderRadius: 12 }}>
              <span className="font-mono text-[10px] tracking-wider text-ink-mute">{t("markets.futUp")}</span>
              <span className="font-mono text-[22px] up font-semibold mt-0.5">{futuresBreadth.up}</span>
            </div>
            <div className="glass p-3 flex flex-col justify-center" style={{ borderRadius: 12 }}>
              <span className="font-mono text-[10px] tracking-wider text-ink-mute">{t("markets.futDown")}</span>
              <span className="font-mono text-[22px] down font-semibold mt-0.5">{futuresBreadth.down}</span>
            </div>
            <div className="glass p-3 flex flex-col justify-center" style={{ borderRadius: 12 }}>
              <span className="font-mono text-[10px] tracking-wider text-ink-mute">{t("markets.futCrowded")}</span>
              <span className="font-mono text-[22px] text-gold font-semibold mt-0.5">{futuresBreadth.crowded}</span>
            </div>
          </div>

          <div className="glass overflow-hidden" style={{ borderRadius: 12 }}>
            <div className="flex items-center gap-2 px-4 pt-3 pb-1 flex-wrap">
              <I.Bolt size={13} className="text-gold" />
              <span className="font-mono text-[12px] tracking-wider text-ink">{t("markets.futuresBoard")}</span>
              <span className="pill pill-dim text-[10px]">USDT-M · {t("markets.futPerp")}</span>
              {fErr && <span className="pill pill-red text-[10px]">{fErr}</span>}
              <div className="ml-auto flex items-center gap-1.5">
                {([["all", t("markets.futAll")], ["gain", t("markets.futGain")], ["loser", t("markets.futLoser")], ["funding", t("markets.futFunding")]] as const).map(([k, label]) => (
                  <button key={k} onClick={() => { setFMode(k); setFLimit(60); }}
                    className={`px-2.5 py-1 rounded-md font-mono text-[10.5px] tracking-wider transition-colors border
                      ${fMode === k ? "bg-elevated text-gold border-line" : "border-transparent text-ink-dim hover:text-ink"}`}>
                    {label}
                  </button>
                ))}
                <div className="relative ml-1">
                  <I.Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-ink-mute" />
                  <input value={fq} onChange={(e) => { setFq(e.target.value); setFLimit(60); }} placeholder={t("markets.searchPlaceholder")}
                    className="field pl-7 w-40 py-1" />
                </div>
              </div>
            </div>
            {/* 表头 */}
            <div className="grid items-center px-4 py-2 mt-1.5 border-b border-line text-[10px] font-mono tracking-[0.08em] text-ink-dim"
              style={{ gridTemplateColumns: "1.6fr 1fr 1fr 1.2fr 1fr 1.3fr" }}>
              <div>{t("markets.h.symbol")}</div><div>{t("markets.h.price")}</div><div>{t("markets.h.change")}</div><div>{t("markets.h.quotevol")}</div><div>{t("markets.h.funding")}</div><div>{t("markets.h.range")}</div>
            </div>
            {futuresRows.length === 0 ? (
              <div className="p-8 text-center text-ink-dim text-[12.5px] font-mono">{t("markets.noData")}</div>
            ) : futuresRows.map((r) => (
              <FutRow key={r.symbol} r={r} onTrade={onTrade} onAnalyze={onAnalyze} />
            ))}
            {(fd?.futures?.length ?? 0) > fLimit && (
              <div className="px-4 py-2 border-t border-line text-center">
                <button onClick={() => setFLimit((n) => n + 60)} className="text-gold font-mono text-[11px] hover:underline">
                  {t("markets.loadMore", { n: 60 })}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* v1.5.3 现货行情：Hero 速览 + 双栏终端布局 */}
      {dim === "spot" && (
      <div className="space-y-4">
      {/* Hero 大盘速览条：4 大币实时卡 + 全市场宽度 */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3">
        {heroBase.map((b) => <HeroCoin key={b.symbol} base={b} />)}
        <div className="glass p-3 flex flex-col gap-2 min-w-0" style={{ borderRadius: 12 }}>
          <div className="flex items-center justify-between gap-1">
            <span className="font-mono text-[10px] tracking-wider text-ink-mute">{t("markets.hero.breadth")}</span>
            <span className="pill pill-dim text-[9px]">{t("markets.breadthTotal", { n: ov?.breadth?.total ?? 0 })}</span>
          </div>
          {ov?.breadth ? (
            <>
              <div className="flex items-baseline gap-1.5">
                <span className="font-mono tabular text-[22px] font-semibold text-ink leading-none">{(ov.breadth.up_ratio * 100).toFixed(0)}%</span>
                <span className="font-mono text-[10px] text-ink-mute">{t("markets.breadthUpRatio")}</span>
              </div>
              <div className="flex h-2 rounded overflow-hidden bg-line">
                <div className="bg-green" style={{ flex: Math.max(0.0001, ov.breadth.up_ratio) }} />
                <div className="bg-red/70" style={{ flex: Math.max(0.0001, 1 - ov.breadth.up_ratio) }} />
              </div>
              <div className="flex items-center justify-between font-mono text-[9.5px]">
                <span className="text-green">{ov.breadth.advancers} {t("markets.breadthUp")}</span>
                <span className={ov.breadth.extreme_count > 0 ? "text-gold font-semibold" : "text-ink-mute"}
                  title={t("markets.breadthExtremeTitle")}>{t("markets.breadthExtreme")} ×{ov.breadth.extreme_count}</span>
                <span className="text-red">{ov.breadth.decliners} {t("markets.breadthDown")}</span>
              </div>
            </>
          ) : <div className="text-[11px] text-ink-dim font-mono">{t("markets.noData")}</div>}
        </div>
      </div>

      {/* v1.5.3 双栏终端布局：主区（雷达/追踪/异动/全市场） + 侧栏（费率/爆仓/多空/热度） */}
      <div className="flex flex-col xl:flex-row gap-4 items-start w-full">
        <div className="flex-1 min-w-0 space-y-4 w-full">
      <div className="glass overflow-hidden" style={{ borderRadius: 12 }}>
        <div className="px-4 pt-3.5 pb-2.5 space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <I.Flame className="text-gold" size={16} />
            <span className="font-mono text-[13px] font-semibold tracking-wider text-ink">{t("markets.radarTitle")}</span>
            {env && <span className="pill pill-gold text-[10px]" title={t("markets.envTitle")}>{t("markets.env", { env })}</span>}
            {engine && <span className="pill pill-dim text-[9px]">engine v{engine.replace(/^v/, "")}</span>}
            {/* 语义层阶段计数 */}
            {(["ACCUMULATION", "IGNITION", "VERTICAL", "DISTRIBUTION", "CRASH", "DORMANT", "ACTIVE"] as const).map((st) => {
              const n = stageCounts[st] ?? 0;
              if (!n) return null;
              return (
                <span key={st} className={`pill ${STAGE_META[st]?.cls ?? "pill-dim"} text-[9.5px]`}>
                  {t(`markets.stage.${st}`)} ×{n}
                </span>
              );
            })}
            <div className="ml-auto flex items-center gap-2">
              <span className="pill pill-dim text-[10px]">{t("markets.radarCounts", { ign: ign.length, tk: tk.length })}</span>
              <button onClick={() => fetchRadar(true)} disabled={mLoading} className="btn-ghost py-1 px-2.5 text-[11px]">
                <I.Refresh size={11} className={mLoading ? "animate-spin" : ""} /> {mLoading ? t("markets.scanning") : t("markets.forceRescan")}
              </button>
            </div>
          </div>
          {/* 模式切换 + 方向过滤（完整说明收进 tooltip） */}
          <div className="flex items-center gap-1.5 flex-wrap">
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
            <span className="w-px h-4 bg-line mx-0.5" />
            {(["ALL", "LONG", "WATCH_SHORT", "WATCH"] as const)
              .filter((k) => k === "ALL" || radarRows.some((m) => m.side === k))
              .map((k) => (
                <button key={k} onClick={() => { setMSide(k); setMLimit(24); }}
                  className={`px-2.5 py-1 rounded-md font-mono text-[11px] tracking-wider transition-colors border
                    ${mSide === k ? "bg-elevated text-gold border-line" : "border-transparent text-ink-dim hover:text-ink"}`}>
                  {k === "ALL" ? t("markets.all", { n: radarRows.length }) : t(SIDE_META[k].label)}
                </button>
              ))}
            <span className="prefix ml-auto hidden lg:inline" title={radarDesc}>
              {mode === "ignition" ? t("markets.ignHint") : t("markets.takeoffHint")}
            </span>
          </div>
          {mErr && <div className="rounded-md border border-red/40 bg-red/5 px-3 py-1.5 text-[11.5px] text-red font-mono">{mErr}</div>}
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
          mRows.map((m) => (
            <RadarLine key={m.symbol} m={m} mode={mode} onTrade={onTrade} onOrder={onOrder} onAnalyze={onAnalyze} />
          ))
        )}
        {radarRows.length > mLimit && (
          <div className="px-4 py-2 border-t border-line text-center">
            <button onClick={() => setMLimit((n) => n + 24)} className="text-gold font-mono text-[11px] hover:underline">
              {t("markets.loadMoreHidden", { n: radarRows.length - mLimit })}
            </button>
          </div>
        )}
      </div>

      {/* v1.5.2 妖币追踪：启动前发现 → 后续暴涨/暴跌结局验证 */}
      {tracks && ((tracks.pending?.length ?? 0) + (tracks.history?.length ?? 0) > 0) && (
        <div className="glass overflow-hidden" style={{ borderRadius: 12 }}>
          <div className="px-4 pt-3.5 pb-1">
            <div className="flex items-center gap-2 flex-wrap">
              <I.Target className="text-gold" size={15} />
              <span className="font-mono text-[13px] tracking-wider text-ink">{t("markets.trackTitle")}</span>
              {tracks.stats && (
                <span className="pill pill-dim text-[10px]">
                  {t("markets.trackStats", {
                    p: tracks.stats.pending ?? 0, moon: tracks.stats.moon ?? 0,
                    dump: tracks.stats.dump ?? 0, expired: tracks.stats.expired ?? 0,
                  })}
                </span>
              )}
            </div>
            <p className="text-[11px] text-ink-dim mt-1.5 leading-relaxed font-mono">{t("markets.trackSub")}</p>
          </div>

          {(tracks.pending?.length ?? 0) > 0 && (
            <>
              <div className="grid items-center px-4 py-2 border-t border-b border-line text-[10px] font-mono tracking-[0.08em] text-ink-dim"
                style={{ gridTemplateColumns: TRACK_COLS.pending.tpl }}>
                {TRACK_COLS.pending.head.map((h) => <div key={h}>{t(h)}</div>)}
              </div>
              {tracks.pending.map((r) => (
                <TrackLine key={r.id} r={r} variant="pending" onTrade={onTrade} />
              ))}
            </>
          )}

          {(tracks.history?.length ?? 0) > 0 && (
            <>
              <div className="px-4 pt-3 pb-1 text-[10px] font-mono tracking-[0.08em] text-ink-dim">{t("markets.trackHistory")}</div>
              <div className="grid items-center px-4 py-2 border-t border-b border-line text-[10px] font-mono tracking-[0.08em] text-ink-dim"
                style={{ gridTemplateColumns: TRACK_COLS.history.tpl }}>
                {TRACK_COLS.history.head.map((h) => <div key={h}>{t(h)}</div>)}
              </div>
              {tracks.history.slice(0, 8).map((r) => (
                <TrackLine key={r.id} r={r} variant="history" onTrade={onTrade} />
              ))}
              {tracks.history.length > 8 && (
                <div className="px-4 py-2 border-t border-line text-center text-ink-dim font-mono text-[10.5px]">
                  {t("markets.trackMore", { n: tracks.history.length - 8 })}
                </div>
              )}
            </>
          )}
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
          {data.signals.map((r) => (
            <SignalRow key={r.symbol} r={r} onTrade={onTrade} onAnalyze={onAnalyze} />
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
          style={{ gridTemplateColumns: "1.7fr 1fr 1.1fr 1.2fr 1.4fr" }}>
          <div>{t("markets.h.symbol")}</div>
          <button onClick={() => toggleSort("price")} className="text-left hover:text-gold">{t("markets.h.price")} <Arrow on={sort.key === "price"} /></button>
          <button onClick={() => toggleSort("change_pct")} className="text-left hover:text-gold">{t("markets.h.change")} <Arrow on={sort.key === "change_pct"} /></button>
          <button onClick={() => toggleSort("quote_volume")} className="text-left hover:text-gold">{t("markets.h.quotevol")} <Arrow on={sort.key === "quote_volume"} /></button>
          <div>{t("markets.h.range")}</div>
        </div>

        {loading && rows.length === 0 ? (
          <div className="p-4 space-y-2">{Array.from({ length: 8 }).map((_, i) => <div key={i} className="shimmer h-9" />)}</div>
        ) : rows.length === 0 ? (
          <div className="p-10 text-center text-ink-dim text-[13px]">
            {q ? t("markets.searchEmpty", { total, q }) : t("markets.noData")}
          </div>
        ) : (
          visible.map((r) => (
            <SpotRow key={r.symbol} r={r} onTrade={onTrade} onAnalyze={onAnalyze} />
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

        </div>
        {/* —— 主区结束 / 侧栏信息流 —— */}

        <div className="w-full xl:w-[340px] shrink-0 space-y-4 xl:sticky xl:top-14">
          {/* 资金费率：极值榜 | 多空拥挤（合并原综述卡 + 极值榜） */}
          <div className="glass p-4" style={{ borderRadius: 12 }}>
            <div className="flex items-center gap-2 mb-2.5">
              <I.Bolt size={14} className="text-gold" />
              <span className="font-mono text-[12px] font-semibold tracking-wider text-ink">{t("markets.funding")}</span>
              <div className="ml-auto flex items-center gap-1">
                {([["ext", t("markets.fundTab.ext")], ["crowd", t("markets.fundTab.crowd")]] as const).map(([k, label]) => (
                  <button key={k} onClick={() => setFundTab(k)}
                    className={`px-2 py-0.5 rounded font-mono text-[10px] tracking-wider border transition-colors
                      ${fundTab === k ? "bg-elevated text-gold border-line" : "border-transparent text-ink-dim hover:text-ink"}`}>
                    {label}
                  </button>
                ))}
              </div>
            </div>
            {(ov?.funding?.flips ?? []).length > 0 && (
              <div className="flex items-center gap-1.5 mb-2 flex-wrap" title={t("markets.fundFlipTitle")}>
                {ov!.funding!.flips!.slice(0, 4).map((fl) => (
                  <span key={fl.symbol} className="pill pill-gold text-[9px]" title={`${fmtRate(fl.prev)} → ${fmtRate(fl.last)}`}>
                    {baseName(fl.symbol)} {t("markets.fundFlipTo", { to: fl.to })}
                  </span>
                ))}
              </div>
            )}
            {fundTab === "ext" ? (
              <div className="space-y-1">
                {(ov?.funding?.extremes ?? []).slice(0, 12).map((f, i) => (
                  <button key={i} onClick={() => onTrade?.(f.symbol)}
                    className="w-full flex items-center gap-2 font-mono text-[10.5px] hover:bg-elevated/40 rounded px-1 py-0.5 transition-colors">
                    <span className="text-ink flex-1 text-left truncate">{baseName(f.symbol)}</span>
                    <span className={`tabular ${f.funding_rate > 0 ? "text-red" : "text-green"}`}>
                      {f.funding_rate > 0 ? "+" : ""}{(f.funding_rate * 100).toFixed(3)}%
                    </span>
                    <span className={`text-[9.5px] ${f.funding_rate > 0 ? "text-red" : "text-green"}`}>
                      {t(f.funding_rate > 0 ? "markets.fundLongSide" : "markets.fundShortSide")}
                    </span>
                  </button>
                ))}
                {(ov?.funding?.extremes ?? []).length === 0 && (
                  <div className="text-[10.5px] text-ink-mute font-mono py-2">{t("markets.noData")}</div>
                )}
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <div className="font-mono text-[10px] tracking-wider text-green mb-1.5">{t("markets.longCrowded")}</div>
                  <div className="space-y-1">
                    {(ov?.funding?.long_crowded ?? []).map((f, i) => (
                      <button key={i} onClick={() => onTrade?.(f.symbol)}
                        className="w-full flex items-center gap-1.5 text-[11px] font-mono hover:bg-elevated/40 rounded px-1 py-0.5 transition-colors">
                        <span className="flex-1 text-ink truncate text-left">{baseName(f.symbol)}</span>
                        <span className={`tabular ${f.crowded ? "text-gold font-semibold" : "text-green"}`}>+{(f.funding_rate * 100).toFixed(3)}%</span>
                      </button>
                    ))}
                    {(ov?.funding?.long_crowded ?? []).length === 0 && <div className="text-[10px] text-ink-mute font-mono">{t("markets.noData")}</div>}
                  </div>
                </div>
                <div>
                  <div className="font-mono text-[10px] tracking-wider text-red mb-1.5">{t("markets.shortCrowded")}</div>
                  <div className="space-y-1">
                    {(ov?.funding?.short_crowded ?? []).map((f, i) => (
                      <button key={i} onClick={() => onTrade?.(f.symbol)}
                        className="w-full flex items-center gap-1.5 text-[11px] font-mono hover:bg-elevated/40 rounded px-1 py-0.5 transition-colors">
                        <span className="flex-1 text-ink truncate text-left">{baseName(f.symbol)}</span>
                        <span className={`tabular ${f.crowded ? "text-gold font-semibold" : "text-red"}`}>{((f.funding_rate ?? 0) * 100).toFixed(3)}%</span>
                      </button>
                    ))}
                    {(ov?.funding?.short_crowded ?? []).length === 0 && <div className="text-[10px] text-ink-mute font-mono">{t("markets.noData")}</div>}
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* 爆仓流 */}
          <div className="glass p-4" style={{ borderRadius: 12 }}>
            <div className="flex items-center gap-2 mb-2.5">
              <I.Zap size={14} className="text-gold" />
              <span className="font-mono text-[12px] font-semibold tracking-wider text-ink">{t("markets.liqTitle")}</span>
              <span className={`pill ml-auto text-[9.5px] ${liq?.ws?.connected ? "pill-green" : "pill-dim"}`}>
                {liq?.ws?.connected ? t("markets.liqOn") : t("markets.liqWait")}
              </span>
            </div>
            {liq?.stats && (
              <div className="grid grid-cols-2 gap-2 mb-2.5">
                <div className="rounded-md border border-red/40 bg-red/5 px-2 py-1.5 text-center">
                  <div className="font-mono tabular text-[13px] text-red font-semibold">${((liq.stats.long_quote ?? 0) / 1e6).toFixed(2)}M</div>
                  <div className="font-mono text-[9px] text-ink-mute">{t("markets.liqLong5m", { n: liq.stats.long_count ?? 0 })}</div>
                </div>
                <div className="rounded-md border border-green/40 bg-green/5 px-2 py-1.5 text-center">
                  <div className="font-mono tabular text-[13px] text-green font-semibold">${((liq.stats.short_quote ?? 0) / 1e6).toFixed(2)}M</div>
                  <div className="font-mono text-[9px] text-ink-mute">{t("markets.liqShort5m", { n: liq.stats.short_count ?? 0 })}</div>
                </div>
              </div>
            )}
            <div className="space-y-0.5">
              {(liq?.recent ?? []).slice(0, 14).map((r, i) => (
                <button key={i} onClick={() => onTrade?.(r.symbol)}
                  className="w-full flex items-center gap-2 font-mono text-[10.5px] hover:bg-elevated/40 rounded px-1 py-0.5 transition-colors">
                  <span className={`w-9 text-center rounded ${r.kind === "long" ? "text-red bg-red/10" : "text-green bg-green/10"}`}>
                    {r.kind === "long" ? t("markets.liqLong") : t("markets.liqShort")}
                  </span>
                  <span className="text-ink flex-1 text-left truncate">{baseName(r.symbol)}</span>
                  <span className="text-ink-dim tabular">{fmtPrice(r.price)}</span>
                  <span className="text-gold tabular">${r.quote >= 1e6 ? `${(r.quote / 1e6).toFixed(2)}M` : r.quote >= 1e3 ? `${(r.quote / 1e3).toFixed(0)}K` : r.quote.toFixed(0)}</span>
                </button>
              ))}
              {(liq?.recent ?? []).length === 0 && (
                <div className="text-[10.5px] text-ink-mute font-mono py-2">{t("markets.liqNone")}</div>
              )}
            </div>
          </div>

          {/* 多空比（大户比例条可视化） */}
          <div className="glass p-4" style={{ borderRadius: 12 }}>
            <div className="flex items-center gap-2 mb-2">
              <I.Bolt size={14} className="text-gold" />
              <span className="font-mono text-[12px] font-semibold tracking-wider text-ink">{t("markets.lsTitle")}</span>
              <span className="pill pill-dim ml-auto text-[9.5px]">1h</span>
            </div>
            <div className="space-y-0.5">
              {ls.slice(0, 12).map((r) => (
                <LsLine key={r.symbol} r={r} onTrade={onTrade} />
              ))}
              {ls.length === 0 && <div className="text-[10.5px] text-ink-mute font-mono py-2">{t("markets.noData")}</div>}
            </div>
          </div>

          {/* 24h 成交额热度（bar 可视化） */}
          <div className="glass p-4" style={{ borderRadius: 12 }}>
            <div className="flex items-center gap-2 mb-2">
              <I.Arrow size={14} className="text-gold" />
              <span className="font-mono text-[12px] font-semibold tracking-wider text-ink">{t("markets.volumeTitle")}</span>
              <span className="pill pill-dim ml-auto text-[10px]">24h</span>
            </div>
            <div className="space-y-1.5">
              {(ov?.volume_top ?? []).map((v, i) => (
                <VolHeatRow key={v.symbol} v={v} rank={i} maxVol={volMax} onTrade={onTrade} onAnalyze={onAnalyze} />
              ))}
              {(ov?.volume_top ?? []).length === 0 && <div className="text-[11px] text-ink-dim font-mono">{t("markets.noData")}</div>}
            </div>
          </div>
        </div>
      </div>
      </div>
      )}

      {/* Data freshness hint */}
      <div className="font-mono text-[10.5px] text-ink-mute flex items-center justify-between px-1">
        <span>{t("markets.dataSource")}</span>
        <span className="tabular">updated_at: <UpdatedAgo updatedAt={data?.updated_at} /></span>
      </div>
    </div>
  );
}
