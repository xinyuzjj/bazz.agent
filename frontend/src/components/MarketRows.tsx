// 行情表格行组件（v1.4.0 拆分自 MarketsView）
// 每行 React.memo + 内部 useLiveTick 订阅自身实时价：WS tick 只让价格变化的行重渲，
// 父组件整表不再随每秒时间戳 tick 重渲。
import React, { memo, useEffect, useRef, useState } from "react";
import { I } from "./icons";
import { useT } from "../i18n/i18n";
import { useLiveTick } from "../lib/live";

/* ---------------- 共享类型（与后端 /api/market* 对齐） ---------------- */

export type Ticker = { symbol: string; price: number; change_pct: number; volume: number; quote_volume: number; high: number; low: number };
export type MinTicker = { symbol: string; price: number; change_pct: number };
export type Signal = {
  symbol: string; price: number; change_pct: number; volume: number;
  funding_rate?: number; direction: string; emoji?: string; reason?: string; score?: number;
};
export type FutureRow = { symbol: string; price: number; change_pct: number; quote_volume: number; high: number; low: number; funding_rate: number };
export type EquityRow = FutureRow & { name?: string; leverage?: string };
export type RadarRow = {
  symbol: string; price: number; quote_volume: number; vol_ratio: number;
  tag: string; side: "LONG" | "WATCH_SHORT" | "WATCH"; note: string; score: number;
  change7d_pct?: number; change30d_pct?: number; drawdown_pct?: number;
  change3d_pct?: number; position_pct?: number; floor_rising?: boolean;
  // v1.5.0 雷达 v2：语义层阶段 + 确认层因子
  stage?: string; stage_label?: string;
  change1h_pct?: number; change24_pct?: number; rvol15?: number; amp24?: number;
  cooldown?: boolean; listed_days?: number | null;
  factors?: {
    flow?: number; jump?: number; speed5m?: number; rvol15?: number; amp24?: number;
    funding?: number; funding_peak?: number | null;
    oi_chg24?: number; oi_pulse15?: number;
    top_ratio?: number | null; global_ratio?: number | null; taker_ratio?: number | null;
    liq_5m?: number; liq_side?: string;
    btc_beta?: number | null; btc_residual?: number | null;
  };
  reasons?: string[];
};
export type OrderMode = "spot-long" | "futures-long" | "futures-short";

// v1.5.0 语义层六阶段 → pill 样式（吸筹/点火=绿，垂直拉升=金，派发顶/崩跌=红，沉寂/异动=灰）
export const STAGE_META: Record<string, { cls: string }> = {
  ACCUMULATION: { cls: "pill-green" },
  IGNITION:     { cls: "pill-green" },
  VERTICAL:     { cls: "pill-gold" },
  DISTRIBUTION: { cls: "pill-red" },
  CRASH:        { cls: "pill-red" },
  DORMANT:      { cls: "pill-dim" },
  ACTIVE:       { cls: "pill-dim" },
};

export const SIDE_META: Record<RadarRow["side"], { label: string; cls: string }> = {
  LONG: { label: "markets.sideLong", cls: "pill-green" },
  WATCH_SHORT: { label: "markets.sideShort", cls: "pill-red" },
  WATCH: { label: "markets.sideWatch", cls: "pill-dim" },
};
export const RADAR_COLS = {
  ignition: { tpl: "2.2fr 0.9fr 0.9fr 0.9fr 1fr 0.9fr 1fr 2.3fr", head: ["markets.col.symbol", "markets.h.price", "3D", "30D", "markets.col.pos90", "markets.col.volratio", "markets.col.verdict", "markets.col.action"] },
  takeoff:  { tpl: "2fr 0.9fr 0.9fr 0.9fr 0.9fr 1.1fr 0.8fr 1fr 2.2fr", head: ["markets.col.symbol", "markets.h.price", "7D", "30D", "markets.col.fromhigh", "markets.h.quotevol", "markets.col.surge", "markets.col.verdict", "markets.col.action"] },
} as const;

// v1.5.2 妖币追踪：启动前发现 → 后续暴涨/暴跌结局验证
export type TrackRow = {
  id: string; symbol: string; stage: string;
  found_price: number; found_score: number; reasons?: string[];
  status: "pending" | "closed";
  outcome: "" | "moon" | "dump" | "expired";
  max_gain_pct: number; max_drop_pct: number;
  peak_price: number; trough_price: number;
  last_price: number; outcome_price: number;
  found_at: number; closed_at: number | null; updated_at: number;
};
export type TracksData = { pending: TrackRow[]; history: TrackRow[]; stats?: { total?: number; pending?: number; moon?: number; dump?: number; expired?: number }; ts?: number; error?: string };
export const OUTCOME_META: Record<string, { label: string; cls: string }> = {
  moon:    { label: "markets.outcomeMoon", cls: "pill-green" },
  dump:    { label: "markets.outcomeDump", cls: "pill-red" },
  expired: { label: "markets.outcomeExpired", cls: "pill-dim" },
};
export const TRACK_COLS = {
  pending: { tpl: "2.2fr 0.9fr 0.9fr 0.9fr 0.9fr 1fr", head: ["markets.col.symbol", "markets.trackFoundPrice", "markets.trackNowPrice", "markets.trackMaxGain", "markets.trackMaxDrop", "markets.trackFoundAt"] },
  history: { tpl: "2.2fr 0.9fr 0.9fr 0.9fr 0.9fr 1fr", head: ["markets.col.symbol", "markets.trackFoundPrice", "markets.trackOutcomePrice", "markets.trackMaxGain", "markets.trackMaxDrop", "markets.trackDuration"] },
} as const;
// 相对时间：60s→"xm"、1h→"x.xh"、更长→"x.xd"（列头文案区分"发现于/持续"）
export const fmtAgo = (ts: number, now: number = Date.now() / 1000) => {
  const s = Math.max(0, now - ts);
  if (s < 3600) return `${Math.max(1, Math.floor(s / 60))}m`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h`;
  return `${(s / 86400).toFixed(1)}d`;
};

/* ---------------- 格式化 ---------------- */

export const fmtPrice = (n: number) => {
  if (!n && n !== 0) return "—";
  if (n >= 1000) return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (n >= 1) return n.toFixed(4);
  return n.toPrecision(4);
};
export const fmtVol = (v: number) => {
  if (!v && v !== 0) return "—";
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
};
export const fmtRate = (r?: number) => r === undefined ? "—" : `${r > 0 ? "+" : ""}${(r * 100).toFixed(4)}%`;
export const baseName = (s: string) => s.replace(/USDT$/, "");

/* ---------------- v1.5.3 可视化：价格闪烁 / 24h 区间条 / 侧栏 bar 行 ---------------- */

// 价格变化闪烁：对比上次值，变化时返回一次性 flash-up / flash-down class
export function useFlash(value: number): string {
  const prev = useRef(value);
  const [cls, setCls] = useState("");
  useEffect(() => {
    if (value > prev.current) setCls("flash-up");
    else if (value < prev.current) setCls("flash-down");
    prev.current = value;
    const t = setTimeout(() => setCls(""), 650);
    return () => clearTimeout(t);
  }, [value]);
  return cls;
}

// 24h 区间位置条：当前价在 [low, high] 中的位置（CoinMarketCap 风格渐变条 + 位置点）
export const PosBar = memo(function PosBar({ low, high, price }: { low: number; high: number; price: number }) {
  const t = useT();
  if (!(low > 0 && high > low)) return null;
  const pos = Math.min(1, Math.max(0, (price - low) / (high - low)));
  return (
    <div className="posbar" title={t("markets.posbarTitle")}>
      <div className="posbar-fill" style={{ width: "100%" }} />
      <div className="posbar-dot" style={{ left: `${pos * 100}%` }} />
    </div>
  );
});

// 侧栏：24h 成交额热度行（底部横向 bar，宽度 = 相对最大成交额）
export const VolHeatRow = memo(function VolHeatRow({ v, rank, maxVol, onTrade, onAnalyze }: {
  v: { symbol: string; price: number; change_pct: number; quote_volume: number };
  rank: number; maxVol: number;
  onTrade?: (symbol: string) => void;
  onAnalyze?: (symbol: string) => void;
}) {
  const t = useT();
  const up = v.change_pct >= 0;
  const w = maxVol > 0 ? Math.max(2, (v.quote_volume / maxVol) * 100) : 0;
  return (
    <button onClick={() => onTrade?.(v.symbol)}
      className="w-full rounded-lg border border-line bg-card/40 px-2.5 py-1.5 hover:border-gold/40 transition-colors group">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[9.5px] text-ink-mute w-4">#{rank + 1}</span>
        <span className="font-mono text-[12px] text-ink flex-1 text-left truncate">{baseName(v.symbol)}</span>
        <span className={`font-mono tabular text-[11.5px] ${up ? "up" : "down"}`}>{up ? "+" : ""}{v.change_pct.toFixed(1)}%</span>
        <span className="font-mono tabular text-[11px] text-ink-dim">{fmtVol(v.quote_volume)}</span>
        <span onClick={(e) => { e.stopPropagation(); onAnalyze?.(v.symbol); }}
          className="btn-ghost px-1.5 py-0.5 text-[9.5px] opacity-0 group-hover:opacity-100"><I.Search size={9} /> {t("markets.analyze")}</span>
      </div>
      <div className="hbar mt-1.5"><div style={{ width: `${w}%`, background: up ? "var(--green)" : "var(--red)" }} /></div>
    </button>
  );
});

// 侧栏：多空比行（大户多空比双段比例条，1:1 = 中线）
export const LsLine = memo(function LsLine({ r, onTrade }: {
  r: { symbol: string; top_ratio: number | null; global_ratio: number | null; divergence: boolean };
  onTrade?: (symbol: string) => void;
}) {
  const t = useT();
  const ratio = r.top_ratio ?? 0;
  const longPct = ratio > 0 ? Math.min(0.95, Math.max(0.05, ratio / (1 + ratio))) : 0.5;
  return (
    <button onClick={() => onTrade?.(r.symbol)}
      className="w-full rounded-md px-1.5 py-1 hover:bg-elevated/40 transition-colors">
      <div className="flex items-center gap-2 font-mono text-[10.5px]">
        <span className="text-ink flex-1 text-left truncate">{baseName(r.symbol)}</span>
        {r.divergence && <span className="pill pill-gold text-[8.5px]">{t("markets.lsDiverge")}</span>}
        <span className="text-ink-dim tabular" title={t("markets.lsTop")}>
          {t("markets.lsTopShort")} {r.top_ratio != null ? r.top_ratio.toFixed(2) : "—"}
        </span>
        <span className="text-ink-mute tabular" title={t("markets.lsGlobal")}>
          {t("markets.lsGlobalShort")} {r.global_ratio != null ? r.global_ratio.toFixed(2) : "—"}
        </span>
      </div>
      <div className="flex items-center gap-1.5 mt-1">
        <span className="font-mono text-[8.5px] text-green">{t("markets.long")}</span>
        <div className="hbar flex-1 flex">
          <div style={{ width: `${longPct * 100}%`, background: "var(--green)" }} />
          <div style={{ width: `${(1 - longPct) * 100}%`, background: "var(--red)" }} />
        </div>
        <span className="font-mono text-[8.5px] text-red">{t("markets.short")}</span>
      </div>
    </button>
  );
});

/* ---------------- 更新时间角标（自带 1s tick，隔离重渲范围） ---------------- */

export const UpdatedAgo = memo(function UpdatedAgo({ updatedAt }: { updatedAt?: number }) {
  const t = useT();
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const iv = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(iv); }, []);
  if (!updatedAt) return <span>—</span>;
  const secs = Math.max(0, Math.floor((now / 1000) - updatedAt));
  if (secs < 1) return <span>{t("markets.updatedNow")}</span>;
  if (secs < 60) return <span>{t("markets.updatedAgo", { n: secs })}</span>;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return <span>{t("markets.updatedMinAgo", { n: mins })}</span>;
  return <span>{new Date(updatedAt * 1000).toLocaleTimeString()}</span>;
});

/* ---------------- 现货全市场行 ---------------- */

export const SpotRow = memo(function SpotRow({ r, onTrade, onAnalyze }: {
  r: Ticker;
  onTrade?: (symbol: string) => void;
  onAnalyze?: (symbol: string) => void;
}) {
  const t = useT();
  const tick = useLiveTick("spot", r.symbol);
  const price = tick ? tick.p : r.price;
  const chg = tick ? tick.c : r.change_pct;
  const qv = tick && tick.q ? tick.q : r.quote_volume;
  const low = tick && tick.l ? tick.l : r.low;
  const high = tick && tick.h ? tick.h : r.high;
  const flash = useFlash(price);
  const up = chg >= 0;
  return (
    <div key={r.symbol} onClick={() => onTrade?.(r.symbol)}
      className="grid items-center px-4 py-2.5 border-b border-line/60 last:border-0 hover:bg-elevated/40 transition-colors cursor-pointer group"
      style={{ gridTemplateColumns: "1.7fr 1fr 1.1fr 1.2fr 1.4fr" }}>
      <div className="flex items-center gap-2 min-w-0">
        <span className="font-mono font-semibold text-ink group-hover:text-gold transition-colors">{baseName(r.symbol)}</span>
        <span className="font-mono text-[10px] text-ink-mute">/USDT</span>
        <button onClick={(e) => { e.stopPropagation(); onAnalyze?.(r.symbol); }}
          className="btn-ghost px-1.5 py-0.5 text-[9.5px] opacity-0 group-hover:opacity-100" title={t("markets.analyze")}><I.Search size={9} /> {t("markets.analyze")}</button>
      </div>
      <div className={`font-mono tabular text-ink text-[12.5px] px-1 -mx-1 ${flash}`}>{fmtPrice(price)}</div>
      <div className={`font-mono tabular text-[12.5px] font-medium ${up ? "up" : "down"}`}>
        {up ? "+" : ""}{chg.toFixed(2)}%
      </div>
      <div className="font-mono tabular text-ink-dim text-[11.5px]">{fmtVol(qv)}</div>
      <div className="pr-1"><PosBar low={low} high={high} price={price} /></div>
    </div>
  );
});

/* ---------------- 合约（永续）行 ---------------- */

export const FutRow = memo(function FutRow({ r, onTrade, onAnalyze }: {
  r: FutureRow;
  onTrade?: (symbol: string) => void;
  onAnalyze?: (symbol: string) => void;
}) {
  const t = useT();
  const tick = useLiveTick("futures", r.symbol);
  const price = tick ? tick.p : r.price;
  const chg = tick ? tick.c : r.change_pct;
  const qv = tick && tick.q ? tick.q : r.quote_volume;
  const low = tick && tick.l ? tick.l : r.low;
  const high = tick && tick.h ? tick.h : r.high;
  const flash = useFlash(price);
  const up = chg >= 0;
  return (
    <div key={r.symbol} onClick={() => onTrade?.(r.symbol)}
      className="grid items-center px-4 py-2.5 border-b border-line/60 last:border-0 hover:bg-elevated/40 transition-colors cursor-pointer group"
      style={{ gridTemplateColumns: "1.6fr 1fr 1fr 1.2fr 1fr 1.3fr" }}>
      <div className="flex items-center gap-2 min-w-0">
        <span className="font-mono font-semibold text-ink group-hover:text-gold">{baseName(r.symbol)}</span>
        <span className="font-mono text-[10px] text-ink-mute">/USDT</span>
        <button onClick={(e) => { e.stopPropagation(); onAnalyze?.(r.symbol); }}
          className="btn-ghost px-1.5 py-0.5 text-[9.5px] opacity-0 group-hover:opacity-100" title={t("markets.analyze")}><I.Search size={9} /> {t("markets.analyze")}</button>
      </div>
      <div className={`font-mono tabular text-ink text-[12.5px] px-1 -mx-1 ${flash}`}>{fmtPrice(price)}</div>
      <div className={`font-mono tabular text-[12.5px] font-medium ${up ? "up" : "down"}`}>{up ? "+" : ""}{chg.toFixed(2)}%</div>
      <div className="font-mono tabular text-ink-dim text-[11.5px]">{fmtVol(qv)}</div>
      <div className={`font-mono tabular text-[11.5px] ${Math.abs(r.funding_rate) >= 0.001 ? "text-gold font-semibold" : r.funding_rate >= 0 ? "text-green" : "text-red"}`}>{fmtRate(r.funding_rate)}</div>
      <div className="pr-1"><PosBar low={low} high={high} price={price} /></div>
    </div>
  );
});

/* ---------------- 股票化代币合约卡片 ---------------- */

export const EquityCard = memo(function EquityCard({ e, onTrade }: {
  e: EquityRow;
  onTrade?: (symbol: string) => void;
}) {
  const t = useT();
  const tick = useLiveTick("futures", e.symbol);
  const price = tick ? tick.p : e.price;
  const chg = tick ? tick.c : e.change_pct;
  const qv = tick && tick.q ? tick.q : e.quote_volume;
  const cfg = e.leverage ? { label: `≤${e.leverage}x`, cls: "pill-gold" } : { label: "永续", cls: "pill-dim" };
  const up = chg >= 0;
  return (
    <button onClick={() => onTrade?.(e.symbol)}
      className="group flex flex-col gap-1.5 rounded-xl border border-line bg-card/40 p-3 text-left hover:border-gold/50 hover:shadow-sm transition-all">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="font-mono font-semibold text-[15px] text-ink group-hover:text-gold">{baseName(e.symbol)}</span>
            <span className="pill pill-dim text-[9px]">/USDT</span>
            <span className={`pill ${cfg.cls} text-[9px]`}>{cfg.label}</span>
          </div>
          <div className="font-mono text-[10px] text-ink-mute truncate mt-0.5" title={e.name}>{e.name ?? "—"}</div>
        </div>
        <span className={`font-mono tabular text-[14px] font-semibold ${up ? "up" : "down"}`}>
          {up ? "+" : ""}{chg.toFixed(2)}%
        </span>
      </div>
      <div className="flex items-center justify-between font-mono tabular text-[12px]">
        <span className="text-ink">{fmtPrice(price)}</span>
        <span className={`text-[11px] ${up ? "up" : "down"}`}>24h</span>
      </div>
      <div className="flex items-center justify-between font-mono text-[10.5px] text-ink-dim">
        <span>{fmtVol(qv)}</span>
        <span className={Math.abs(e.funding_rate) >= 0.001 ? "text-gold" : "text-ink-mute"}>
          {t("markets.funding")} {fmtRate(e.funding_rate)}
        </span>
      </div>
    </button>
  );
});

/* ---------------- 妖币雷达行 ---------------- */

export const RadarLine = memo(function RadarLine({ m, mode, onTrade, onOrder, onAnalyze }: {
  m: RadarRow;
  mode: "ignition" | "takeoff";
  onTrade?: (symbol: string) => void;
  onOrder?: (symbol: string, mode: OrderMode) => void;
  onAnalyze?: (symbol: string) => void;
}) {
  const t = useT();
  const side = SIDE_META[m.side];
  const isIgn = mode === "ignition";
  // v1.5.0 确认因子摘要（悬浮 title / 次行展示）
  const f = m.factors ?? {};
  const bits: string[] = [];
  if (f.flow != null) bits.push(`flow ${f.flow}`);
  if (f.jump != null && f.jump > 0) bits.push(`J${f.jump.toFixed(1)}`);
  if (f.speed5m != null && Math.abs(f.speed5m) >= 0.3) bits.push(`5m ${f.speed5m > 0 ? "+" : ""}${f.speed5m.toFixed(1)}%`);
  if (f.rvol15 != null && f.rvol15 >= 1.2) bits.push(`RVOL ${f.rvol15.toFixed(1)}x`);
  if (f.funding != null && Math.abs(f.funding) >= 0.0015) bits.push(`费率${f.funding > 0 ? "+" : ""}${(f.funding * 100).toFixed(3)}%`);
  if (f.oi_chg24 != null && Math.abs(f.oi_chg24) >= 5) bits.push(`OI ${f.oi_chg24 > 0 ? "+" : ""}${f.oi_chg24.toFixed(0)}%`);
  if (f.top_ratio != null) bits.push(`大户 ${f.top_ratio.toFixed(2)}`);
  if (f.taker_ratio != null && f.taker_ratio >= 1.5) bits.push(`taker ${f.taker_ratio.toFixed(2)}`);
  if (f.liq_5m != null && f.liq_5m >= 3e5) bits.push(`爆 $${(f.liq_5m / 1e6).toFixed(1)}M${f.liq_side === "long" ? "多" : f.liq_side === "short" ? "空" : ""}`);
  const stageCls = m.stage ? STAGE_META[m.stage]?.cls ?? "pill-dim" : "pill-dim";
  return (
    <div onClick={() => onTrade?.(m.symbol)}
      className="grid items-center px-4 py-2.5 border-b border-line/60 last:border-0 hover:bg-elevated/40 transition-colors cursor-pointer group"
      style={{ gridTemplateColumns: RADAR_COLS[mode].tpl }}>
      <div className="min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-mono font-semibold text-ink group-hover:text-gold">{baseName(m.symbol)}</span>
          <span className="font-mono text-[9px] text-ink-mute">/USDT</span>
          {m.stage_label && <span className={`pill ${stageCls} text-[9px]`} title={m.tag}>{m.stage_label}</span>}
          {!m.stage_label && <span className={`pill ${m.side === "LONG" ? "pill-green" : m.side === "WATCH_SHORT" ? "pill-red" : "pill-dim"} text-[9px]`} title={m.tag}>{m.tag}</span>}
          <span className="pill pill-dim text-[9px]" title={t("markets.scoreTitle")}>{t("markets.scorePrefix")}{m.score}</span>
          {m.cooldown && <span className="pill pill-dim text-[9px]" title={t("markets.cooldownTitle")}>{t("markets.cooldown")}</span>}
          {isIgn && m.floor_rising && <span className="pill pill-dim text-[9px]" title={t("markets.floorRisingTitle")}>{t("markets.floorRising")}</span>}
        </div>
        <div className="font-mono text-[9.5px] text-ink-mute truncate mt-0.5" title={bits.length ? bits.join(" · ") : m.note}>
          {bits.length ? bits.join(" · ") : m.note}
        </div>
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
});

/* ---------------- 妖币追踪行（v1.5.2：启动前发现 → 结局验证） ---------------- */

export const TrackLine = memo(function TrackLine({ r, variant, onTrade }: {
  r: TrackRow;
  variant: "pending" | "history";
  onTrade?: (symbol: string) => void;
}) {
  const t = useT();
  const isP = variant === "pending";
  const stageCls = STAGE_META[r.stage]?.cls ?? "pill-dim";
  const oc = OUTCOME_META[r.outcome] ?? null;
  const gain = r.max_gain_pct ?? 0;
  const drop = r.max_drop_pct ?? 0;
  return (
    <div onClick={() => onTrade?.(r.symbol)}
      className="grid items-center px-4 py-2.5 border-b border-line/60 last:border-0 hover:bg-elevated/40 transition-colors cursor-pointer group"
      style={{ gridTemplateColumns: TRACK_COLS[variant].tpl }}>
      <div className="min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-mono font-semibold text-ink group-hover:text-gold">{baseName(r.symbol)}</span>
          <span className="font-mono text-[9px] text-ink-mute">/USDT</span>
          <span className={`pill ${stageCls} text-[9px]`}>{t(`markets.stage.${r.stage}`)}</span>
          {!isP && oc && <span className={`pill ${oc.cls} text-[9px]`}>{t(oc.label)}</span>}
          {!isP && r.outcome === "moon" && r.outcome_price > 0 && (
            <span className="font-mono text-[9.5px] text-green">+{(((r.outcome_price / (r.found_price || 1)) - 1) * 100).toFixed(0)}%</span>
          )}
          {!isP && r.outcome === "dump" && r.outcome_price > 0 && (
            <span className="font-mono text-[9.5px] text-red">{(((r.outcome_price / (r.found_price || 1)) - 1) * 100).toFixed(0)}%</span>
          )}
        </div>
        {(r.reasons?.length ?? 0) > 0 && (
          <div className="font-mono text-[9.5px] text-ink-mute truncate mt-0.5">{r.reasons!.slice(0, 3).join(" · ")}</div>
        )}
      </div>
      <div className="font-mono tabular text-ink text-[12px]">{fmtPrice(r.found_price)}</div>
      {isP ? (
        <div className="font-mono tabular text-ink text-[12px]">{r.last_price ? fmtPrice(r.last_price) : "—"}</div>
      ) : (
        <div className="font-mono tabular text-ink text-[12px]">{fmtPrice(r.outcome_price || r.last_price)}</div>
      )}
      <div className={`font-mono tabular text-[12px] ${gain > 0 ? "up" : "text-ink-mute"}`}>+{gain.toFixed(1)}%</div>
      <div className={`font-mono tabular text-[12px] ${drop > 0 ? "down" : "text-ink-mute"}`}>-{drop.toFixed(1)}%</div>
      <div className="font-mono tabular text-ink-dim text-[11px]">
        {isP ? fmtAgo(r.found_at) : fmtAgo(r.closed_at || r.updated_at, r.found_at)}
      </div>
    </div>
  );
});

/* ---------------- 智能异动信号行 ---------------- */

export const SignalRow = memo(function SignalRow({ r, onTrade, onAnalyze }: {
  r: Signal;
  onTrade?: (symbol: string) => void;
  onAnalyze?: (symbol: string) => void;
}) {
  const t = useT();
  const up = r.change_pct >= 0;
  return (
    <div onClick={() => onTrade?.(r.symbol)}
      className="grid items-center px-4 py-2.5 border-b border-line/60 last:border-0 hover:bg-elevated/40 transition-colors cursor-pointer group"
      style={{ gridTemplateColumns: "1.4fr 1fr 1fr 1.1fr 1fr 0.9fr 1.9fr 0.8fr" }}>
      <div className="flex items-center gap-2 min-w-0">
        <span className="font-mono font-semibold text-ink group-hover:text-gold">{baseName(r.symbol)}</span>
        <span className="font-mono text-[10px] text-ink-mute">/USDT</span>
        <button onClick={(e) => { e.stopPropagation(); onAnalyze?.(r.symbol); }}
          className="btn-ghost px-1.5 py-0.5 text-[9.5px]" title={t("markets.analyze")}><I.Search size={9} /> {t("markets.analyze")}</button>
      </div>
      <div className="font-mono tabular text-ink text-[12.5px]">{fmtPrice(r.price)}</div>
      <div className={`font-mono tabular text-[12.5px] ${up ? "up" : "down"}`}>
        {up ? "+" : ""}{r.change_pct.toFixed(2)}%
      </div>
      <div className="font-mono tabular text-ink-dim text-[11.5px]">{fmtVol(r.volume)}</div>
      <div className={`font-mono tabular text-[11.5px] ${(r.funding_rate ?? 0) >= 0 ? "text-green" : "text-red"}`}>{fmtRate(r.funding_rate)}</div>
      <div><span className={`pill ${up ? "pill-green" : "pill-red"}`}>{r.direction ?? (up ? t("markets.long") : t("markets.short"))}</span></div>
      <div className="font-mono text-[11px] text-ink-dim truncate" title={r.reason}>{r.reason ?? "—"}</div>
      <div className="font-mono tabular text-gold text-[12.5px]">{(r.score ?? 0).toFixed(1)}</div>
    </div>
  );
});
