// 订单跟踪卡（v1.4.0）：确认下单后自动登记，展示状态流转（待成交/部分成交/已成交/已撤销）
// + 实时价、相对入场盈亏、止损/止盈距离；exchange 订单状态由后端监控线程自动同步。
import React, { memo, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { I } from "./icons";
import { useT } from "../i18n/i18n";
import { useLiveTick, subscribeAlerts } from "../lib/live";
import { baseName, fmtPrice } from "./MarketRows";

type TrackedOrder = {
  id: string; order_id: string; symbol: string; route: string; direction: string;
  quantity: string; entry: number; stop_loss: number; take_profit: number;
  status: string; active: boolean; note: string; created_at: number; updated_at: number;
};

const STATUS_META: Record<string, { key: string; cls: string }> = {
  NEW: { key: "track.stNew", cls: "pill-dim" },
  PARTIALLY_FILLED: { key: "track.stPart", cls: "pill-gold" },
  FILLED: { key: "track.stFilled", cls: "pill-green" },
  DONE: { key: "track.stFilled", cls: "pill-green" },
  CANCELED: { key: "track.stCanceled", cls: "pill-red" },
  REJECTED: { key: "track.stRejected", cls: "pill-red" },
  EXPIRED: { key: "track.stExpired", cls: "pill-red" },
  FAILED: { key: "track.stRejected", cls: "pill-red" },
};

const TrackedLine = memo(function TrackedLine({ o, onRemove }: {
  o: TrackedOrder;
  onRemove: (id: string) => void;
}) {
  const t = useT();
  const fut = useLiveTick("futures", o.symbol);
  const spot = useLiveTick("spot", o.symbol);
  const tick = fut ?? spot;
  const px = tick ? tick.p : 0;
  const entry = o.entry || 0;
  const long = ["BULLISH", "做多", "LONG", "BUY"].includes(String(o.direction).toUpperCase());
  const pnl = px && entry ? ((px - entry) / entry) * 100 * (long ? 1 : -1) : null;
  const sm = STATUS_META[o.status] ?? { key: "track.stNew", cls: "pill-dim" };
  const slDist = px && o.stop_loss ? ((px - o.stop_loss) / px) * 100 * (long ? 1 : -1) : null;
  const tpDist = px && o.take_profit ? ((o.take_profit - px) / px) * 100 * (long ? 1 : -1) : null;
  return (
    <div className="grid items-center px-4 py-2.5 border-b border-line/60 last:border-0 hover:bg-elevated/40 transition-colors font-mono text-[12px]"
      style={{ gridTemplateColumns: "1.5fr 0.8fr 1.2fr 1.1fr 1fr 1fr 1.2fr 0.6fr" }}>
      <div className="flex items-center gap-1.5 min-w-0">
        <span className="font-semibold text-ink truncate">{baseName(o.symbol)}</span>
        <span className="text-[9.5px] text-ink-mute">/USDT</span>
        <span className={`pill ${long ? "pill-green" : "pill-red"} text-[9px]`}>{long ? t("markets.long") : t("markets.short")}</span>
      </div>
      <div>
        <span className={`pill ${sm.cls} text-[9.5px]`} title={o.order_id}>{t(sm.key)}</span>
      </div>
      <div className="text-ink-dim text-[11px] truncate" title={`${t("track.qty")} ${o.quantity} @ ${fmtPrice(entry)}`}>
        {o.quantity} @ {entry ? fmtPrice(entry) : "—"}
      </div>
      <div className="text-ink tabular">{px ? fmtPrice(px) : "—"}</div>
      <div className={`tabular ${pnl == null ? "text-ink-mute" : pnl >= 0 ? "up" : "down"}`}>
        {pnl == null ? "—" : `${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}%`}
      </div>
      <div className="text-[11px] text-ink-dim tabular">
        {o.stop_loss ? <span title={t("track.sl")}>SL {fmtPrice(o.stop_loss)}{slDist != null ? <span className="text-ink-mute"> ({slDist.toFixed(1)}%)</span> : null}</span> : "—"}
      </div>
      <div className="text-[11px] text-ink-dim tabular">
        {o.take_profit ? <span title={t("track.tp")}>TP {fmtPrice(o.take_profit)}{tpDist != null ? <span className="text-ink-mute"> ({tpDist.toFixed(1)}%)</span> : null}</span> : "—"}
      </div>
      <div className="text-right">
        <button onClick={() => onRemove(o.id)} className="btn-ghost px-1.5 py-0.5 text-[10px] text-ink-mute hover:text-red" title={t("track.remove")}>
          <I.X size={10} />
        </button>
      </div>
    </div>
  );
});

export function OrderTracking() {
  const t = useT();
  const [orders, setOrders] = useState<TrackedOrder[] | null>(null);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      const r: any = await api.ordersTrack();
      setErr(r?.status === "ok" ? "" : (r?.message || ""));
      setOrders(r?.orders ?? []);
    } catch (e: any) { setErr(e?.message || String(e)); }
  }, []);

  useEffect(() => {
    load();
    const iv = setInterval(load, 5000);
    // 订单状态事件 → 立即刷新
    const off = subscribeAlerts((e: any) => { if (e?.type === "order") load(); });
    return () => { clearInterval(iv); off(); };
  }, [load]);

  const remove = async (id: string) => {
    setOrders((cur) => (cur ?? []).filter((o) => o.id !== id));
    try { await api.orderUntrack(id); } catch { /* 下一轮轮询会恢复显示 */ }
  };

  return (
    <div className="glass overflow-hidden" style={{ borderRadius: 12 }}>
      <div className="px-4 py-3 flex items-center gap-3 border-b border-line flex-wrap">
        <span className="font-mono text-[13px] text-ink"><span className="text-gold">▣</span> {t("track.title")}</span>
        <span className="pill pill-gold">{orders?.length ?? 0}</span>
        <span className="prefix hidden md:inline">{t("track.hint")}</span>
        <button onClick={load} className="btn-ghost ml-auto"><I.Refresh size={12} /> {t("markets.refresh")}</button>
      </div>
      {err && <div className="px-4 py-2 text-[11px] text-red font-mono border-b border-line/60">{err}</div>}
      {orders === null ? (
        <div className="px-4 py-5 text-center font-mono text-[12px] text-ink-mute">{t("exch.loading")}</div>
      ) : orders.length === 0 ? (
        <div className="px-4 py-5 text-center font-mono text-[12px] text-ink-mute">{t("track.empty")}</div>
      ) : (
        <>
          <div className="grid items-center px-4 py-2 border-b border-line text-[10px] font-mono tracking-[0.08em] text-ink-dim"
            style={{ gridTemplateColumns: "1.5fr 0.8fr 1.2fr 1.1fr 1fr 1fr 1.2fr 0.6fr" }}>
            <div>{t("markets.h.symbol")}</div><div>{t("track.status")}</div><div>{t("track.qtyEntry")}</div>
            <div>{t("track.live")}</div><div>{t("track.pnl")}</div><div>{t("track.sl")}</div><div>{t("track.tp")}</div><div />
          </div>
          {orders.map((o) => <TrackedLine key={o.id} o={o} onRemove={remove} />)}
        </>
      )}
    </div>
  );
}
