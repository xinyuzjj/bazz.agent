import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { I } from "../components/icons";
import { useT } from "../i18n/i18n";
import { subscribeTicks, useWsStatus, useLiveTick } from "../lib/live";
import {
  type Ticker, type Signal, type FutureRow, type EquityRow, type RadarRow, type OrderMode,
  type TracksData,
  SIDE_META, RADAR_COLS, STAGE_META, TRACK_COLS, fmtPrice, fmtVol, fmtRate, fmtAgo, baseName, UpdatedAgo,
  PosBar, useFlash, LsLine,
  FutRow, EquityCard, RadarLine, TrackLine, SignalRow,
} from "../components/MarketRows";
import { Sparkline } from "../components/Sparkline";
import { CoinDetail } from "../components/CoinDetail";

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
        <span className="font-mono text-[13.5px] font-semibold text-ink">{baseName(base.symbol)}</span>
        <span className={`font-mono tabular text-[13px] font-semibold px-1.5 py-0.5 rounded ${up ? "text-green bg-green/10" : "text-red bg-red/10"}`}>
          {up ? "+" : ""}{chg.toFixed(2)}%
        </span>
      </div>
      <div className={`font-mono tabular text-[20px] font-semibold text-ink leading-none px-1 -mx-1 ${flash}`}>{fmtPrice(price)}</div>
      <Sparkline symbol={base.symbol} market="spot" className="h-7" />
      <PosBar low={low} high={high} price={price} />
      <div className="flex items-center justify-between font-mono text-[11px] text-ink-mute">
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
  const [dim, setDim] = useState<"spot" | "futures" | "equity" | "radar">("spot");
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
  // —— v1.5.4 增强：详情浮层 / 合约 OI / 恐惧贪婪 ——
  const [detail, setDetail] = useState<{ symbol: string; market: "spot" | "futures"; base?: any } | null>(null);
  const openDetail = (market: "spot" | "futures") => (symbol: string, base?: any) => setDetail({ symbol, market, base });
  const [oiMap, setOiMap] = useState<Record<string, { oi: number | null; notional: number | null }>>({});
  const [fng, setFng] = useState<any>(null);
  const heroBase = useMemo(
    () => HERO_COINS.map((s) => (data?.all ?? []).find((x) => x.symbol === s)).filter(Boolean) as Ticker[],
    [data],
  );
  // 雷达完整说明（收进 tooltip）
  const radarDesc = mode === "ignition"
    ? `${t("markets.ignDesc1")}${t("markets.buySellSpot")}${t("markets.ignDesc2")}`
    : `${t("markets.takeoffDesc1")}${t("markets.buySellSpot")}${t("markets.takeoffDesc2")}`;

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

  // —— v1.5.0 多空比面板（爆仓流已按需求移除 v1.5.7）——
  type LsRow = { symbol: string; price: number; change_pct: number; quote_volume: number; top_ratio: number | null; top_prev: number | null; global_ratio: number | null; divergence: boolean };
  const [ls, setLs] = useState<LsRow[]>([]);
  const loadLs = async () => {
    try { const d: any = await api.marketLongshort(); if (!d?.error) setLs(d?.rows ?? []); } catch { /* 网络失败保持旧数据 */ }
  };
  useEffect(() => {
    loadLs();
    const t = setInterval(loadLs, 30_000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    loadTracks();
    const t = setInterval(() => loadTracks(), 60_000);
    return () => clearInterval(t);
  }, []);
  // v1.5.4：恐惧贪婪指数（10min 后端缓存，前端 5min 轮询）
  useEffect(() => {
    const pull = async () => { try { setFng(await api.marketFNG()); } catch { /* noop */ } };
    pull();
    const t = setInterval(pull, 300_000);
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
  // v1.5.4：合约维度可见行 OI 批量拉取（后端 5min 缓存，60s 刷新；futuresRows 定义于此之后才可用）
  const oiKey = useMemo(() => (dim === "futures" ? futuresRows.map((r) => r.symbol).join(",") : ""), [dim, futuresRows]);
  useEffect(() => {
    if (!oiKey) return;
    let alive = true;
    const syms = oiKey.split(",");
    const pull = async () => {
      try {
        const d: any = await api.marketOI(syms);
        if (alive && d?.items?.length) {
          const m: Record<string, { oi: number | null; notional: number | null }> = {};
          for (const it of d.items) m[it.symbol] = { oi: it.oi, notional: it.notional };
          setOiMap((prev) => ({ ...prev, ...m }));
        }
      } catch { /* noop */ }
    };
    pull();
    const t = setInterval(pull, 60_000);
    return () => { alive = false; clearInterval(t); };
  }, [oiKey]);
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
                : dim === "radar"
                  ? t("markets.radarCounts", { ign: ign.length, tk: tk.length })
                  : `${data?.quote ?? "USDT"} ${t("markets.spotPairs", { total })}`}
          </span>
        </div>
        <span className="prefix ml-auto">{t("markets.autoRefreshPrefix")} <span className="text-ink-dim tabular"><UpdatedAgo updatedAt={data?.updated_at} /></span></span>
        <button onClick={() => { load(); loadOverview(); loadFutures(); }} className="btn-ghost py-1 px-2.5 text-[13px]"><I.Refresh size={11} /> {t("markets.refresh")}</button>
      </div>

      {/* 维度切换：现货 / 合约 / 股票化代币合约（sticky：滚动时固定） */}
      <div className="sticky top-0 z-20 -mx-5 px-5 py-2 bg-canvas/85 backdrop-blur-md border-b border-line/40">
        <div className="flex items-center gap-1.5 rounded-lg bg-elevated/50 border border-line p-1 w-fit">
          {([
            ["spot", t("markets.dimSpot"), I.Market],
            ["futures", t("markets.dimFutures"), I.Bolt],
            ["equity", t("markets.dimEquity"), I.Star],
            ["radar", t("markets.dimRadar"), I.Flame],
          ] as const).map(([key, label, Icon]) => (
            <button key={key} onClick={() => setDim(key)}
              className={`px-4 py-1.5 rounded-md font-mono text-[13px] tracking-wide transition-colors border flex items-center gap-1.5
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
              <span className="font-mono text-[14px] tracking-wider text-ink">{t("markets.equityTitle")}</span>
              <span className="pill pill-dim text-[11.5px]">{fd?.total_equity ?? 0} · {t("markets.equityTag")}</span>
            </div>
            <p className="text-[12.5px] text-ink-dim font-mono leading-relaxed">{t("markets.equityHint")}</p>
            {fErr && <div className="rounded-md border border-red/40 bg-red/5 px-3 py-1.5 text-[13px] text-red font-mono mt-2">{fErr}</div>}
            <div className="mt-3 grid grid-cols-2 sm:grid-cols-3 gap-3">
              {equityRows.length === 0 && (
                <div className="col-span-full p-6 text-center text-ink-dim text-[13.5px] font-mono">{t("markets.noData")}</div>
              )}
              {equityRows.map((e) => (
                <EquityCard key={e.symbol} e={e} onDetail={openDetail("futures")} />
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
                    <span className={`font-mono text-[11.5px] tracking-wider px-2 py-1 rounded-md ${s === "gainers" ? "text-green bg-green/10" : "text-red bg-red/10"}`}>
                      {s === "gainers" ? t("markets.dimEquityGain") : t("markets.dimEquityLoss")}
                    </span>
                    <div className="flex-1 flex gap-3 overflow-hidden">
                      {list.map((e, i) => (
                        <button key={e.symbol} onClick={() => onTrade?.(e.symbol)} className="flex items-center gap-1 font-mono text-[13px] hover:text-gold">
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
              <span className="font-mono text-[11.5px] tracking-wider text-ink-mute">{t("markets.futTotal")}</span>
              <span className="font-mono text-[22px] text-ink font-semibold mt-0.5">{futuresBreadth.total}</span>
            </div>
            <div className="glass p-3 flex flex-col justify-center" style={{ borderRadius: 12 }}>
              <span className="font-mono text-[11.5px] tracking-wider text-ink-mute">{t("markets.futUp")}</span>
              <span className="font-mono text-[22px] up font-semibold mt-0.5">{futuresBreadth.up}</span>
            </div>
            <div className="glass p-3 flex flex-col justify-center" style={{ borderRadius: 12 }}>
              <span className="font-mono text-[11.5px] tracking-wider text-ink-mute">{t("markets.futDown")}</span>
              <span className="font-mono text-[22px] down font-semibold mt-0.5">{futuresBreadth.down}</span>
            </div>
            <div className="glass p-3 flex flex-col justify-center" style={{ borderRadius: 12 }}>
              <span className="font-mono text-[11.5px] tracking-wider text-ink-mute">{t("markets.futCrowded")}</span>
              <span className="font-mono text-[22px] text-gold font-semibold mt-0.5">{futuresBreadth.crowded}</span>
            </div>
          </div>

          <div className="glass overflow-hidden" style={{ borderRadius: 12 }}>
            <div className="flex items-center gap-2 px-4 pt-3 pb-1 flex-wrap">
              <I.Bolt size={13} className="text-gold" />
              <span className="font-mono text-[13px] tracking-wider text-ink">{t("markets.futuresBoard")}</span>
              <span className="pill pill-dim text-[11.5px]">USDT-M · {t("markets.futPerp")}</span>
              {fErr && <span className="pill pill-red text-[11.5px]">{fErr}</span>}
              <div className="ml-auto flex items-center gap-1.5">
                {([["all", t("markets.futAll")], ["gain", t("markets.futGain")], ["loser", t("markets.futLoser")], ["funding", t("markets.futFunding")]] as const).map(([k, label]) => (
                  <button key={k} onClick={() => { setFMode(k); setFLimit(60); }}
                    className={`px-2.5 py-1 rounded-md font-mono text-[12px] tracking-wider transition-colors border
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
            <div className="grid items-center px-4 py-2 mt-1.5 border-b border-line text-[11.5px] font-mono tracking-[0.08em] text-ink-dim"
              style={{ gridTemplateColumns: "1.5fr 1fr 1fr 1.1fr 0.9fr 1.1fr 1.2fr" }}>
              <div>{t("markets.h.symbol")}</div><div>{t("markets.h.price")}</div><div>{t("markets.h.change")}</div><div>{t("markets.h.quotevol")}</div><div>{t("markets.h.funding")}</div><div>{t("markets.futH.oi")}</div><div>{t("markets.h.range")}</div>
            </div>
            {futuresRows.length === 0 ? (
              <div className="p-8 text-center text-ink-dim text-[13.5px] font-mono">{t("markets.noData")}</div>
            ) : futuresRows.map((r) => (
              <FutRow key={r.symbol} r={r} oi={oiMap[r.symbol]} onDetail={openDetail("futures")} onAnalyze={onAnalyze} />
            ))}
            {(fd?.futures?.length ?? 0) > fLimit && (
              <div className="px-4 py-2 border-t border-line text-center">
                <button onClick={() => setFLimit((n) => n + 60)} className="text-gold font-mono text-[12.5px] hover:underline">
                  {t("markets.loadMore", { n: 60 })}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* 妖币雷达独立维度页（v1.5.8：顶部单独 Tab，全宽展示，不再挤在现货双栏里） */}
      {dim === "radar" && (
      <div className="space-y-4">
      <div className="glass overflow-hidden" style={{ borderRadius: 12 }}>
        <div className="px-4 pt-3.5 pb-2.5 space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <I.Flame className="text-gold" size={16} />
            <span className="font-mono text-[14px] font-semibold tracking-wider text-ink">{t("markets.radarTitle")}</span>
            {env && <span className="pill pill-gold text-[11.5px]" title={t("markets.envTitle")}>{t("markets.env", { env })}</span>}
            {engine && <span className="pill pill-dim text-[10.5px]">engine v{engine.replace(/^v/, "")}</span>}
            {/* 语义层阶段计数 */}
            {(["ACCUMULATION", "IGNITION", "VERTICAL", "DISTRIBUTION", "CRASH", "DORMANT", "ACTIVE"] as const).map((st) => {
              const n = stageCounts[st] ?? 0;
              if (!n) return null;
              return (
                <span key={st} className={`pill ${STAGE_META[st]?.cls ?? "pill-dim"} text-[11px]`}>
                  {t(`markets.stage.${st}`)} ×{n}
                </span>
              );
            })}
            <div className="ml-auto flex items-center gap-2">
              <span className="pill pill-dim text-[11.5px]">{t("markets.radarCounts", { ign: ign.length, tk: tk.length })}</span>
              <button onClick={() => fetchRadar(true)} disabled={mLoading} className="btn-ghost py-1 px-2.5 text-[12.5px]">
                <I.Refresh size={11} className={mLoading ? "animate-spin" : ""} /> {mLoading ? t("markets.scanning") : t("markets.forceRescan")}
              </button>
            </div>
          </div>
          {/* 模式切换 + 方向过滤（完整说明收进 tooltip） */}
          <div className="flex items-center gap-1.5 flex-wrap">
            <button onClick={() => { setMode("ignition"); setMSide("ALL"); setMLimit(24); }}
              className={`px-3.5 py-1.5 rounded-md font-mono text-[13px] tracking-wide border transition-colors
                ${mode === "ignition" ? "bg-gold text-canvas border-gold font-semibold" : "border-transparent text-ink-dim hover:text-ink"}`}>
              {t("markets.modeIgnition")} <span className="ml-0.5 opacity-70">({ign.length})</span>
            </button>
            <button onClick={() => { setMode("takeoff"); setMSide("ALL"); setMLimit(24); }}
              className={`px-3.5 py-1.5 rounded-md font-mono text-[13px] tracking-wide border transition-colors
                ${mode === "takeoff" ? "bg-gold text-canvas border-gold font-semibold" : "border-transparent text-ink-dim hover:text-ink"}`}>
              {t("markets.modeTakeoff")} <span className="ml-0.5 opacity-70">({tk.length})</span>
            </button>
            <span className="w-px h-4 bg-line mx-0.5" />
            {(["ALL", "LONG", "WATCH_SHORT", "WATCH"] as const)
              .filter((k) => k === "ALL" || radarRows.some((m) => m.side === k))
              .map((k) => (
                <button key={k} onClick={() => { setMSide(k); setMLimit(24); }}
                  className={`px-2.5 py-1 rounded-md font-mono text-[12.5px] tracking-wider transition-colors border
                    ${mSide === k ? "bg-elevated text-gold border-line" : "border-transparent text-ink-dim hover:text-ink"}`}>
                  {k === "ALL" ? t("markets.all", { n: radarRows.length }) : t(SIDE_META[k].label)}
                </button>
              ))}
            <span className="prefix ml-auto hidden lg:inline" title={radarDesc}>
              {mode === "ignition" ? t("markets.ignHint") : t("markets.takeoffHint")}
            </span>
          </div>
          {mErr && <div className="rounded-md border border-red/40 bg-red/5 px-3 py-1.5 text-[13px] text-red font-mono">{mErr}</div>}
        </div>

        {/* 表头 */}
        <div className="grid items-center px-4 py-2 border-t border-b border-line text-[11.5px] font-mono tracking-[0.08em] text-ink-dim"
          style={{ gridTemplateColumns: RADAR_COLS[mode].tpl }}>
          {RADAR_COLS[mode].head.map((h) => <div key={h}>{t(h)}</div>)}
        </div>

        {mLoading && radarRows.length === 0 ? (
          <div className="p-4 space-y-2">{Array.from({ length: 6 }).map((_, i) => <div key={i} className="shimmer h-9" />)}</div>
        ) : mRows.length === 0 ? (
          <div className="p-8 text-center text-ink-dim text-[13.5px] font-mono">
            {mLoading ? t("markets.scanningCold")
              : mode === "ignition" ? t("markets.emptyIgn")
                : t("markets.emptyTakeoff")}
          </div>
        ) : (
          mRows.map((m) => (
            <RadarLine key={m.symbol} m={m} mode={mode} onTrade={onTrade} onOrder={onOrder} onAnalyze={onAnalyze} onDetail={openDetail("spot")} />
          ))
        )}
        {radarRows.length > mLimit && (
          <div className="px-4 py-2 border-t border-line text-center">
            <button onClick={() => setMLimit((n) => n + 24)} className="text-gold font-mono text-[12.5px] hover:underline">
              {t("markets.loadMoreHidden", { n: radarRows.length - mLimit })}
            </button>
          </div>
        )}
      </div>

      {/* v1.5.2 妖币追踪：随雷达一起移入独立 Tab（启动前发现 → 后续暴涨/暴跌结局验证） */}
      {tracks && ((tracks.pending?.length ?? 0) + (tracks.history?.length ?? 0) > 0) && (
        <div className="glass overflow-hidden" style={{ borderRadius: 12 }}>
          <div className="px-4 pt-3.5 pb-1">
            <div className="flex items-center gap-2 flex-wrap">
              <I.Target className="text-gold" size={15} />
              <span className="font-mono text-[14px] tracking-wider text-ink">{t("markets.trackTitle")}</span>
              {tracks.stats && (
                <span className="pill pill-dim text-[11.5px]">
                  {t("markets.trackStats", {
                    p: tracks.stats.pending ?? 0, moon: tracks.stats.moon ?? 0,
                    dump: tracks.stats.dump ?? 0, expired: tracks.stats.expired ?? 0,
                  })}
                </span>
              )}
            </div>
            <p className="text-[12.5px] text-ink-dim mt-1.5 leading-relaxed font-mono">{t("markets.trackSub")}</p>
          </div>

          {/* v1.5.4 战绩可视化：胜率 + 结局占比堆叠条 */}
          {(() => {
            const st = tracks.stats ?? {};
            const moon = st.moon ?? 0, dump = st.dump ?? 0, expired = st.expired ?? 0;
            const closed = moon + dump + expired;
            if (closed <= 0) return null;
            const win = (moon / closed) * 100;
            return (
              <div className="px-4 pb-3 pt-1 flex items-center gap-3 flex-wrap">
                <div className="flex items-baseline gap-1.5 shrink-0">
                  <span className={`font-mono tabular text-[20px] font-semibold leading-none ${win >= 50 ? "text-green" : "text-ink"}`}>{win.toFixed(0)}%</span>
                  <span className="font-mono text-[11px] text-ink-mute">{t("markets.trackWin")}</span>
                </div>
                <div className="flex-1 min-w-[140px] flex h-2 rounded overflow-hidden bg-line"
                  title={`${t("markets.outcomeMoon")} ${moon} · ${t("markets.outcomeDump")} ${dump} · ${t("markets.outcomeExpired")} ${expired}`}>
                  <div className="bg-green" style={{ flex: Math.max(0.0001, moon) }} />
                  <div className="bg-red/80" style={{ flex: Math.max(0.0001, dump) }} />
                  <div className="bg-ink-mute/40" style={{ flex: Math.max(0.0001, expired) }} />
                </div>
                <div className="flex items-center gap-2 font-mono text-[11px] shrink-0">
                  <span className="text-green">● {t("markets.outcomeMoon")} {moon}</span>
                  <span className="text-red">● {t("markets.outcomeDump")} {dump}</span>
                  <span className="text-ink-mute">● {t("markets.outcomeExpired")} {expired}</span>
                </div>
              </div>
            );
          })()}

          {(tracks.pending?.length ?? 0) > 0 && (
            <>
              <div className="grid items-center px-4 py-2 border-t border-b border-line text-[11.5px] font-mono tracking-[0.08em] text-ink-dim"
                style={{ gridTemplateColumns: TRACK_COLS.pending.tpl }}>
                {TRACK_COLS.pending.head.map((h) => <div key={h}>{t(h)}</div>)}
              </div>
              {tracks.pending.map((r) => (
                <TrackLine key={r.id} r={r} variant="pending" onDetail={openDetail("spot")} />
              ))}
            </>
          )}

          {(tracks.history?.length ?? 0) > 0 && (
            <>
              <div className="px-4 pt-3 pb-1 text-[11.5px] font-mono tracking-[0.08em] text-ink-dim">{t("markets.trackHistory")}</div>
              <div className="grid items-center px-4 py-2 border-t border-b border-line text-[11.5px] font-mono tracking-[0.08em] text-ink-dim"
                style={{ gridTemplateColumns: TRACK_COLS.history.tpl }}>
                {TRACK_COLS.history.head.map((h) => <div key={h}>{t(h)}</div>)}
              </div>
              {tracks.history.slice(0, 8).map((r) => (
                <TrackLine key={r.id} r={r} variant="history" onDetail={openDetail("spot")} />
              ))}
              {tracks.history.length > 8 && (
                <div className="px-4 py-2 border-t border-line text-center text-ink-dim font-mono text-[12px]">
                  {t("markets.trackMore", { n: tracks.history.length - 8 })}
                </div>
              )}
            </>
          )}
        </div>
      )}
      </div>
      )}

      {/* v1.5.3 现货行情：Hero 速览 + 双栏终端布局 */}
      {dim === "spot" && (
      <div className="space-y-4">
      {/* Hero 大盘速览条：4 大币实时卡（含迷你走势）+ 全市场宽度 + 恐惧贪婪 */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        {heroBase.map((b) => <HeroCoin key={b.symbol} base={b} />)}
        <div className="glass p-3 flex flex-col gap-2 min-w-0" style={{ borderRadius: 12 }}>
          <div className="flex items-center justify-between gap-1">
            <span className="font-mono text-[11.5px] tracking-wider text-ink-mute">{t("markets.hero.breadth")}</span>
            <span className="pill pill-dim text-[10.5px]">{t("markets.breadthTotal", { n: ov?.breadth?.total ?? 0 })}</span>
          </div>
          {ov?.breadth ? (
            <>
              <div className="flex items-baseline gap-1.5">
                <span className="font-mono tabular text-[24px] font-semibold text-ink leading-none">{(ov.breadth.up_ratio * 100).toFixed(0)}%</span>
                <span className="font-mono text-[11.5px] text-ink-mute">{t("markets.breadthUpRatio")}</span>
              </div>
              <div className="flex h-2 rounded overflow-hidden bg-line">
                <div className="bg-green" style={{ flex: Math.max(0.0001, ov.breadth.up_ratio) }} />
                <div className="bg-red/70" style={{ flex: Math.max(0.0001, 1 - ov.breadth.up_ratio) }} />
              </div>
              <div className="flex items-center justify-between font-mono text-[11px]">
                <span className="text-green">{ov.breadth.advancers} {t("markets.breadthUp")}</span>
                <span className={ov.breadth.extreme_count > 0 ? "text-gold font-semibold" : "text-ink-mute"}
                  title={t("markets.breadthExtremeTitle")}>{t("markets.breadthExtreme")} ×{ov.breadth.extreme_count}</span>
                <span className="text-red">{ov.breadth.decliners} {t("markets.breadthDown")}</span>
              </div>
            </>
          ) : <div className="text-[12.5px] text-ink-dim font-mono">{t("markets.noData")}</div>}
        </div>
        {/* v1.5.4 恐惧贪婪指数卡（alternative.me，10min 后端缓存） */}
        <div className="glass p-3 flex flex-col gap-2 min-w-0" style={{ borderRadius: 12 }}>
          <div className="flex items-center justify-between gap-1">
            <span className="font-mono text-[11.5px] tracking-wider text-ink-mute">{t("markets.fng.title")}</span>
            <span className="pill pill-dim text-[10.5px]">alt.me</span>
          </div>
          {fng?.value != null ? (() => {
            const v = Number(fng.value);
            const txt = v <= 24 ? "text-red" : v <= 44 ? "text-gold" : v <= 55 ? "text-ink-dim" : "text-green";
            const label = t(v <= 24 ? "markets.fng.z1" : v <= 44 ? "markets.fng.z2" : v <= 55 ? "markets.fng.z3" : v <= 75 ? "markets.fng.z4" : "markets.fng.z5");
            return (
              <>
                <div className="flex items-baseline gap-2">
                  <span className={`font-mono tabular text-[24px] font-semibold leading-none ${txt}`}>{v}</span>
                  <span className={`font-mono text-[12px] ${txt}`}>{label}</span>
                </div>
                <div className="flex items-end gap-0.5 h-6" title={(fng.history ?? []).map((p: any) => p.value).join(" · ")}>
                  {(fng.history ?? []).slice(-8).map((p: any, i: number) => (
                    <div key={i} className={`flex-1 rounded-sm ${p.value <= 24 ? "bg-red/60" : p.value <= 44 ? "bg-gold/50" : p.value <= 55 ? "bg-ink-mute/40" : "bg-green/60"}`}
                      style={{ height: `${Math.max(10, p.value)}%` }} />
                  ))}
                </div>
              </>
            );
          })() : <div className="text-[12.5px] text-ink-dim font-mono">—</div>}
        </div>
      </div>

      {/* v1.5.3 双栏终端布局：主区（异动/全市场） + 侧栏（费率/爆仓/多空/热度）；雷达/追踪已独立成「妖币雷达」Tab */}
      <div className="flex flex-col xl:flex-row gap-4 items-start w-full">
        <div className="flex-1 min-w-0 space-y-4 w-full">
      {/* 智能异动信号（全市场扫描） */}
      {data && data.signals && data.signals.length > 0 && (
        <div className="glass overflow-hidden" style={{ borderRadius: 12 }}>
          <div className="flex items-center gap-2 px-4 pt-3 pb-1">
            <I.Bolt size={13} className="text-gold" />
            <span className="font-mono text-[13px] tracking-wider text-ink">{t("markets.smartScan")}</span>
            <span className="pill pill-dim text-[11.5px]">{t("markets.scanPill")}</span>
            <span className="pill pill-gold ml-auto text-[11.5px]">{t("markets.signalCount", { n: data.signals.length })}</span>
          </div>
          <div className="grid items-center px-4 py-2 mt-1.5 border-b border-line text-[11.5px] font-mono tracking-[0.08em] text-ink-dim"
            style={{ gridTemplateColumns: "1.4fr 1fr 1fr 1.1fr 1fr 0.9fr 1.9fr 0.8fr" }}>
            <div>{t("markets.h.symbol")}</div><div>{t("markets.h.price")}</div><div>{t("markets.h.change")}</div><div>{t("markets.h.quotevol")}</div><div>{t("markets.h.funding")}</div><div>{t("markets.h.direction")}</div><div>{t("markets.h.reason")}</div><div>{t("markets.h.strength")}</div>
          </div>
          {data.signals.map((r) => (
            <SignalRow key={r.symbol} r={r} onDetail={openDetail("spot")} onAnalyze={onAnalyze} />
          ))}
        </div>
      )}

      {/* v1.5.8 全市场交易对表已按需求移除（数据由雷达/异动/合约页承载） */}

        </div>
        {/* —— 主区结束 / 侧栏信息流 —— */}

        <div className="w-full xl:w-[340px] shrink-0 space-y-4 xl:sticky xl:top-14">
          {/* 资金费率：极值榜 | 多空拥挤（合并原综述卡 + 极值榜） */}
          <div className="glass p-4" style={{ borderRadius: 12 }}>
            <div className="flex items-center gap-2 mb-2.5">
              <I.Bolt size={14} className="text-gold" />
              <span className="font-mono text-[13px] font-semibold tracking-wider text-ink">{t("markets.funding")}</span>
              <div className="ml-auto flex items-center gap-1">
                {([["ext", t("markets.fundTab.ext")], ["crowd", t("markets.fundTab.crowd")]] as const).map(([k, label]) => (
                  <button key={k} onClick={() => setFundTab(k)}
                    className={`px-2 py-0.5 rounded font-mono text-[11.5px] tracking-wider border transition-colors
                      ${fundTab === k ? "bg-elevated text-gold border-line" : "border-transparent text-ink-dim hover:text-ink"}`}>
                    {label}
                  </button>
                ))}
              </div>
            </div>
            {(ov?.funding?.flips ?? []).length > 0 && (
              <div className="flex items-center gap-1.5 mb-2 flex-wrap" title={t("markets.fundFlipTitle")}>
                {ov!.funding!.flips!.slice(0, 4).map((fl) => (
                  <span key={fl.symbol} className="pill pill-gold text-[10.5px]" title={`${fmtRate(fl.prev)} → ${fmtRate(fl.last)}`}>
                    {baseName(fl.symbol)} {t("markets.fundFlipTo", { to: fl.to })}
                  </span>
                ))}
              </div>
            )}
            {fundTab === "ext" ? (
              <div className="space-y-1">
                {(ov?.funding?.extremes ?? []).slice(0, 12).map((f, i) => (
                  <button key={i} onClick={() => onTrade?.(f.symbol)}
                    className="w-full flex items-center gap-2 font-mono text-[12px] hover:bg-elevated/40 rounded px-1 py-0.5 transition-colors">
                    <span className="text-ink flex-1 text-left truncate">{baseName(f.symbol)}</span>
                    <span className={`tabular ${f.funding_rate > 0 ? "text-red" : "text-green"}`}>
                      {f.funding_rate > 0 ? "+" : ""}{(f.funding_rate * 100).toFixed(3)}%
                    </span>
                    <span className={`text-[11px] ${f.funding_rate > 0 ? "text-red" : "text-green"}`}>
                      {t(f.funding_rate > 0 ? "markets.fundLongSide" : "markets.fundShortSide")}
                    </span>
                  </button>
                ))}
                {(ov?.funding?.extremes ?? []).length === 0 && (
                  <div className="text-[12px] text-ink-mute font-mono py-2">{t("markets.noData")}</div>
                )}
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <div className="font-mono text-[11.5px] tracking-wider text-green mb-1.5">{t("markets.longCrowded")}</div>
                  <div className="space-y-1">
                    {(ov?.funding?.long_crowded ?? []).map((f, i) => (
                      <button key={i} onClick={() => onTrade?.(f.symbol)}
                        className="w-full flex items-center gap-1.5 text-[12.5px] font-mono hover:bg-elevated/40 rounded px-1 py-0.5 transition-colors">
                        <span className="flex-1 text-ink truncate text-left">{baseName(f.symbol)}</span>
                        <span className={`tabular ${f.crowded ? "text-gold font-semibold" : "text-green"}`}>+{(f.funding_rate * 100).toFixed(3)}%</span>
                      </button>
                    ))}
                    {(ov?.funding?.long_crowded ?? []).length === 0 && <div className="text-[11.5px] text-ink-mute font-mono">{t("markets.noData")}</div>}
                  </div>
                </div>
                <div>
                  <div className="font-mono text-[11.5px] tracking-wider text-red mb-1.5">{t("markets.shortCrowded")}</div>
                  <div className="space-y-1">
                    {(ov?.funding?.short_crowded ?? []).map((f, i) => (
                      <button key={i} onClick={() => onTrade?.(f.symbol)}
                        className="w-full flex items-center gap-1.5 text-[12.5px] font-mono hover:bg-elevated/40 rounded px-1 py-0.5 transition-colors">
                        <span className="flex-1 text-ink truncate text-left">{baseName(f.symbol)}</span>
                        <span className={`tabular ${f.crowded ? "text-gold font-semibold" : "text-red"}`}>{((f.funding_rate ?? 0) * 100).toFixed(3)}%</span>
                      </button>
                    ))}
                    {(ov?.funding?.short_crowded ?? []).length === 0 && <div className="text-[11.5px] text-ink-mute font-mono">{t("markets.noData")}</div>}
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* 爆仓流已按需求移除（v1.5.7）；后端 /api/market/liquidations 保留供 market-data 技能使用 */}

          {/* 多空比（大户比例条可视化） */}
          <div className="glass p-4" style={{ borderRadius: 12 }}>
            <div className="flex items-center gap-2 mb-2">
              <I.Bolt size={14} className="text-gold" />
              <span className="font-mono text-[13px] font-semibold tracking-wider text-ink">{t("markets.lsTitle")}</span>
              <span className="pill pill-dim ml-auto text-[11px]">1h</span>
            </div>
            <div className="space-y-0.5">
              {ls.slice(0, 12).map((r) => (
                <LsLine key={r.symbol} r={r} onTrade={onTrade} />
              ))}
              {ls.length === 0 && <div className="text-[12px] text-ink-mute font-mono py-2">{t("markets.noData")}</div>}
            </div>
          </div>

          {/* 24h 成交额热度榜已按需求移除（v1.5.8） */}
        </div>
      </div>
      </div>
      )}

      {/* Data freshness hint */}
      <div className="font-mono text-[12px] text-ink-mute flex items-center justify-between px-1">
        <span>{t("markets.dataSource")}</span>
        <span className="tabular">updated_at: <UpdatedAgo updatedAt={data?.updated_at} /></span>
      </div>

      {/* v1.5.4 币种详情浮层（Esc / 点击遮罩关闭） */}
      {detail && (
        <CoinDetail symbol={detail.symbol} market={detail.market} base={detail.base}
          onClose={() => setDetail(null)}
          onTrade={onTrade} onOrder={onOrder} onAnalyze={onAnalyze} />
      )}
    </div>
  );
}
