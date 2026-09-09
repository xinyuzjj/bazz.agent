// 行情表格行组件（v1.4.0 拆分自 MarketsView）
// 每行 React.memo + 内部 useLiveTick 订阅自身实时价：WS tick 只让价格变化的行重渲，
// 父组件整表不再随每秒时间戳 tick 重渲。
import React, { memo, useEffect, useState } from "react";
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
};
export type OrderMode = "spot-long" | "futures-long" | "futures-short";

export const SIDE_META: Record<RadarRow["side"], { label: string; cls: string }> = {
  LONG: { label: "markets.sideLong", cls: "pill-green" },
  WATCH_SHORT: { label: "markets.sideShort", cls: "pill-red" },
  WATCH: { label: "markets.sideWatch", cls: "pill-dim" },
};
export const RADAR_COLS = {
  ignition: { tpl: "2.2fr 0.9fr 0.9fr 0.9fr 1fr 0.9fr 1fr 2.3fr", head: ["markets.col.symbol", "markets.h.price", "3D", "30D", "markets.col.pos90", "markets.col.volratio", "markets.col.verdict", "markets.col.action"] },
  takeoff:  { tpl: "2fr 0.9fr 0.9fr 0.9fr 0.9fr 1.1fr 0.8fr 1fr 2.2fr", head: ["markets.col.symbol", "markets.h.price", "7D", "30D", "markets.col.fromhigh", "markets.h.quotevol", "markets.col.surge", "markets.col.verdict", "markets.col.action"] },
} as const;

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
  const up = chg >= 0;
  return (
    <div key={r.symbol} onClick={() => onTrade?.(r.symbol)}
      className="grid items-center px-4 py-2.5 border-b border-line/60 last:border-0 hover:bg-elevated/40 transition-colors cursor-pointer group"
      style={{ gridTemplateColumns: "1.7fr 1fr 1.1fr 1.2fr 1fr 1fr" }}>
      <div className="flex items-center gap-2 min-w-0">
        <span className="font-mono font-semibold text-ink group-hover:text-gold transition-colors">{baseName(r.symbol)}</span>
        <span className="font-mono text-[10px] text-ink-mute">/USDT</span>
        <button onClick={(e) => { e.stopPropagation(); onAnalyze?.(r.symbol); }}
          className="btn-ghost px-1.5 py-0.5 text-[9.5px]" title={t("markets.analyze")}><I.Search size={9} /> {t("markets.analyze")}</button>
      </div>
      <div className="font-mono tabular text-ink text-[12.5px]">{fmtPrice(price)}</div>
      <div className={`font-mono tabular text-[12.5px] ${up ? "up" : "down"}`}>
        {up ? "+" : ""}{chg.toFixed(2)}%
      </div>
      <div className="font-mono tabular text-ink-dim text-[11.5px]">{fmtVol(qv)}</div>
      <div className="font-mono tabular text-ink-dim text-[11.5px]">{r.high > 0 ? fmtPrice(r.high) : "—"}</div>
      <div className="font-mono tabular text-ink-dim text-[11.5px]">{r.low > 0 ? fmtPrice(r.low) : "—"}</div>
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
  const up = chg >= 0;
  return (
    <div key={r.symbol} onClick={() => onTrade?.(r.symbol)}
      className="grid items-center px-4 py-2.5 border-b border-line/60 last:border-0 hover:bg-elevated/40 transition-colors cursor-pointer group"
      style={{ gridTemplateColumns: "1.6fr 1fr 1fr 1.2fr 1fr" }}>
      <div className="flex items-center gap-2 min-w-0">
        <span className="font-mono font-semibold text-ink group-hover:text-gold">{baseName(r.symbol)}</span>
        <span className="font-mono text-[10px] text-ink-mute">/USDT</span>
        <button onClick={(e) => { e.stopPropagation(); onAnalyze?.(r.symbol); }}
          className="btn-ghost px-1.5 py-0.5 text-[9.5px]" title={t("markets.analyze")}><I.Search size={9} /> {t("markets.analyze")}</button>
      </div>
      <div className="font-mono tabular text-ink text-[12.5px]">{fmtPrice(price)}</div>
      <div className={`font-mono tabular text-[12.5px] ${up ? "up" : "down"}`}>{up ? "+" : ""}{chg.toFixed(2)}%</div>
      <div className="font-mono tabular text-ink-dim text-[11.5px]">{fmtVol(qv)}</div>
      <div className={`font-mono tabular text-[11.5px] ${Math.abs(r.funding_rate) >= 0.001 ? "text-gold font-semibold" : r.funding_rate >= 0 ? "text-green" : "text-red"}`}>{fmtRate(r.funding_rate)}</div>
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
  return (
    <div onClick={() => onTrade?.(m.symbol)}
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
