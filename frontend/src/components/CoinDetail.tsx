// v1.5.4 币种详情浮层：点击任意行情行弹出 ——
// 实时价 + 迷你走势图（24H/7D 切换）+ 关键指标（区间/成交额/资金费率/OI/大户多空比）+ 交易/分析入口。
import React, { useEffect, useState } from "react";
import { api } from "../api";
import { useT } from "../i18n/i18n";
import { useLiveTick } from "../lib/live";
import { I } from "./icons";
import { Sparkline } from "./Sparkline";
import { baseName, fmtPrice, fmtVol, fmtRate, type OrderMode } from "./MarketRows";

export function CoinDetail({ symbol, market, base, onClose, onTrade, onOrder, onAnalyze }: {
  symbol: string;
  market: "spot" | "futures";
  base?: any;               // 行内已有的静态快照（价格/涨跌/成交额/高低/费率/名称）
  onClose: () => void;
  onTrade?: (symbol: string) => void;
  onOrder?: (symbol: string, mode: OrderMode) => void;
  onAnalyze?: (symbol: string) => void;
}) {
  const t = useT();
  const tick = useLiveTick(market, symbol);
  const price = tick ? tick.p : (base?.price ?? 0);
  const chg = tick ? tick.c : (base?.change_pct ?? 0);
  const qv = tick && tick.q ? tick.q : (base?.quote_volume ?? 0);
  const low = tick && tick.l ? tick.l : base?.low;
  const high = tick && tick.h ? tick.h : base?.high;
  const funding = base?.funding_rate;
  const up = chg >= 0;

  const [range, setRange] = useState<"24h" | "7d">("24h");
  const [oi, setOi] = useState<{ oi: number | null; notional: number | null } | null>(null);
  const [lsRatio, setLsRatio] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      if (market !== "futures") return;
      try {
        const d: any = await api.marketOI([symbol]);
        const it = d?.items?.[0];
        if (alive && it) setOi({ oi: it.oi, notional: it.notional });
      } catch { /* ignore */ }
      try {
        const d: any = await api.marketLongshort();
        const row = (d?.rows ?? []).find((x: any) => x.symbol === symbol);
        if (alive && row) setLsRatio(row.top_ratio ?? null);
      } catch { /* ignore */ }
    })();
    return () => { alive = false; };
  }, [symbol, market]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const Stat = ({ label, children }: { label: string; children: React.ReactNode }) => (
    <div className="rounded-lg border border-line bg-card/30 px-2.5 py-2 min-w-0">
      <div className="font-mono text-[10.5px] tracking-wider text-ink-mute mb-1">{label}</div>
      <div className="font-mono tabular text-[13px] text-ink">{children}</div>
    </div>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-6" onClick={onClose}>
      <div className="absolute inset-0 bg-canvas/70 backdrop-blur-sm" />
      <div className="relative glass w-full max-w-lg p-5 space-y-4 border border-line shadow-xl"
        style={{ borderRadius: 14 }} onClick={(e) => e.stopPropagation()}>
        {/* 头部：币种 + 实时价 + 涨跌 */}
        <div className="flex items-start gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-mono text-[16px] font-bold text-ink">{baseName(symbol)}</span>
              <span className="font-mono text-[11.5px] text-ink-mute">/USDT</span>
              <span className={`pill ${market === "futures" ? "pill-gold" : "pill-dim"} text-[10.5px]`}>
                {market === "futures" ? t("markets.dimFutures") : t("markets.dimSpot")}
              </span>
            </div>
            <div className="flex items-baseline gap-2 mt-1">
              <span className="font-mono tabular text-[24px] font-semibold text-ink leading-none">{fmtPrice(price)}</span>
              <span className={`font-mono tabular text-[14px] font-semibold ${up ? "up" : "down"}`}>
                {up ? "+" : ""}{chg.toFixed(2)}%
              </span>
            </div>
          </div>
          <button onClick={onClose} className="btn-ghost ml-auto shrink-0" title="Esc"><I.X size={14} /></button>
        </div>

        {/* 走势：24H / 7D */}
        <div>
          <div className="flex items-center gap-1.5 mb-1.5">
            <span className="font-mono text-[11.5px] tracking-wider text-ink-mute">{t("markets.detail.trend")}</span>
            <div className="ml-auto flex items-center gap-1">
              {([["24h", "24H"], ["7d", "7D"]] as const).map(([k, label]) => (
                <button key={k} onClick={() => setRange(k)}
                  className={`px-2 py-0.5 rounded font-mono text-[11.5px] border transition-colors
                    ${range === k ? "bg-elevated text-gold border-line" : "border-transparent text-ink-dim hover:text-ink"}`}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          <Sparkline symbol={symbol} market={market} interval={range === "24h" ? "1h" : "4h"}
            limit={range === "24h" ? 24 : 42} className="h-20" />
        </div>

        {/* 指标网格 */}
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
          <Stat label={t("markets.detail.range24")}>
            {low != null && high != null ? `${fmtPrice(low)} — ${fmtPrice(high)}` : "—"}
          </Stat>
          <Stat label={t("markets.h.quotevol")}>{qv ? fmtVol(qv) : "—"}</Stat>
          {market === "futures" ? (
            <>
              <Stat label={t("markets.funding")}>
                {funding != null ? fmtRate(funding) : "—"}
              </Stat>
              <Stat label={t("markets.detail.oi")}>
                {oi?.notional != null ? `$${fmtVol(oi.notional)}` : "—"}
              </Stat>
              <Stat label={t("markets.detail.ls")}>
                {lsRatio != null ? lsRatio.toFixed(2) : "—"}
              </Stat>
            </>
          ) : (
            <Stat label={t("markets.detail.ls")}>—</Stat>
          )}
        </div>

        {/* 操作区 */}
        <div className="flex items-center gap-2 flex-wrap pt-1 border-t border-line">
          {market === "spot" && (
            <button onClick={() => { onClose(); onOrder?.(symbol, "spot-long"); }}
              className="btn-ghost text-[13px] py-1.5 border-green/40 text-green hover:border-green">
              <I.Check size={12} /> {t("markets.spotBuy")}
            </button>
          )}
          <button onClick={() => { onClose(); onOrder?.(symbol, "futures-long"); }}
            className="btn-ghost text-[13px] py-1.5"><I.Bolt size={12} /> {t("markets.futuresLong")}</button>
          <button onClick={() => { onClose(); onOrder?.(symbol, "futures-short"); }}
            className="btn-ghost text-[13px] py-1.5 border-red/40 text-red hover:border-red"><I.Bolt size={12} /> {t("markets.futuresShort")}</button>
          <button onClick={() => { onClose(); onAnalyze?.(symbol); }}
            className="btn-ghost text-[13px] py-1.5 ml-auto"><I.Search size={12} /> {t("markets.analyze")}</button>
          <button onClick={() => { onClose(); onTrade?.(symbol); }}
            className="btn-ghost text-[13px] py-1.5 text-gold border-gold/40 hover:border-gold">{t("markets.detail.trade")}</button>
        </div>
      </div>
    </div>
  );
}
