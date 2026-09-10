// 全局通知（v1.4.0）：订阅 /api/ws 事件（订单状态 / SL·TP 提醒）→
// 应用内 toast（右下角，自动消失）+ 系统通知（Electron 渲染层 Web Notification API）。
import React, { useEffect, useRef, useState } from "react";
import { useT } from "../i18n/i18n";
import { subscribeAlerts } from "../lib/live";
import { baseName, fmtPrice } from "./MarketRows";

type Toast = { id: number; title: string; body: string; kind: "info" | "warn" | "ok" | "bad" };

const KIND_CLS: Record<Toast["kind"], string> = {
  info: "border-line bg-elevated",
  ok: "border-green/40 bg-green/5",
  warn: "border-gold/50 bg-gold/5",
  bad: "border-red/40 bg-red/5",
};
const KIND_DOT: Record<Toast["kind"], string> = {
  info: "bg-ink-mute", ok: "bg-green", warn: "bg-gold", bad: "bg-red",
};

let seq = 1;
const listeners = new Set<(t: Toast) => void>();

export function pushToast(title: string, body: string, kind: Toast["kind"] = "info") {
  const t: Toast = { id: seq++, title, body, kind };
  listeners.forEach((f) => { try { f(t); } catch { /* noop */ } });
}

function systemNotify(title: string, body: string) {
  try {
    if (typeof Notification === "undefined") return;
    if (Notification.permission === "granted") new Notification(title, { body });
    else if (Notification.permission !== "denied") {
      Notification.requestPermission().then((p) => { if (p === "granted") new Notification(title, { body }); }).catch(() => {});
    }
  } catch { /* 非 Electron 环境静默 */ }
}

export default function Toasts() {
  const t = useT();
  const [items, setItems] = useState<Toast[]>([]);
  const timers = useRef<Map<number, any>>(new Map());

  useEffect(() => {
    const add = (t: Toast) => {
      setItems((cur) => [...cur.slice(-4), t]);  // 最多同时 5 条
      const timer = setTimeout(() => {
        setItems((cur) => cur.filter((x) => x.id !== t.id));
        timers.current.delete(t.id);
      }, 8000);
      timers.current.set(t.id, timer);
    };
    listeners.add(add);
    return () => { listeners.delete(add); timers.current.forEach((x) => clearTimeout(x)); };
  }, []);

  // 订阅后端事件：SL/TP 提醒 + 订单状态变更（应用内 + 系统通知）
  useEffect(() => {
    const off = subscribeAlerts((e: any) => {
      if (!e) return;
      if (e.type === "alert") {
        const sym = baseName(String(e.symbol ?? ""));
        const px = e.price != null ? fmtPrice(Number(e.price)) : "—";
        const kind = String(e.kind ?? "");
        // v1.5.0：爆仓潮 / funding 极值与翻转提醒
        if (kind === "liq_burst") {
          const isLong = String(e.side ?? "") === "long";
          const top: any[] = Array.isArray(e.top) ? e.top : [];
          const title = t(isLong ? "alert.liqBurstLong" : "alert.liqBurstShort");
          const body = `$${(Number(e.quote ?? 0) / 1e6).toFixed(2)}M · ${t("alert.liqCount", { n: Number(e.count ?? 0) })}`
            + (top.length ? ` · ${top.map((x) => `${baseName(String(x.symbol))} $${(Number(x.quote) / 1e3).toFixed(0)}K`).join(" / ")}` : "");
          pushToast(title, body, isLong ? "bad" : "ok");
          systemNotify(title, body);
          return;
        }
        if (kind === "funding_extreme") {
          const isLong = String(e.side ?? "") === "long";
          const title = t("alert.fundExtreme");
          const body = `${sym} · ${t(isLong ? "alert.fundLongSide" : "alert.fundShortSide")} ${((Number(e.rate ?? 0)) * 100).toFixed(3)}%`;
          pushToast(title, body, isLong ? "bad" : "ok");
          systemNotify(title, body);
          return;
        }
        if (kind === "funding_flip") {
          const title = t("alert.fundFlip");
          const body = `${sym} · ${Number(e.prev ?? 0) > 0 ? "+" : ""}${(Number(e.prev ?? 0) * 100).toFixed(3)}% → ${Number(e.last ?? 0) > 0 ? "+" : ""}${(Number(e.last ?? 0) * 100).toFixed(3)}%`;
          pushToast(title, body, "warn");
          systemNotify(title, body);
          return;
        }
        // v1.5.2：妖币追踪结局（启动前发现 → 暴涨/暴跌兑现 / 到期）
        if (kind === "radar_outcome") {
          const oc = String(e.outcome ?? "");
          const title = t(oc === "moon" ? "alert.radarMoon" : oc === "dump" ? "alert.radarDump" : "alert.radarExpired");
          const body = t("alert.radarBody", {
            found: e.found_price != null ? fmtPrice(Number(e.found_price)) : "—",
            price: px,
            gain: Number(e.max_gain_pct ?? 0).toFixed(1),
            drop: Number(e.max_drop_pct ?? 0).toFixed(1),
          });
          pushToast(title, `${sym} · ${body}`, oc === "moon" ? "ok" : oc === "dump" ? "bad" : "info");
          systemNotify(title, `${sym} · ${body}`);
          return;
        }
        const isHit = kind.endsWith("_hit");
        const isSl = kind.startsWith("sl");
        const title = t(isSl ? (isHit ? "alert.slHit" : "alert.slNear") : (isHit ? "alert.tpHit" : "alert.tpNear"));
        const body = `${sym} · ${t("alert.priceNow")} ${px} · ${isSl ? t("alert.levelSl") : t("alert.levelTp")} ${e.level != null ? fmtPrice(Number(e.level)) : "—"}`;
        pushToast(title, body, isHit ? (isSl ? "bad" : "ok") : "warn");
        systemNotify(title, body);
      } else if (e.type === "order" && e.event === "status") {
        const sym = baseName(String(e.symbol ?? ""));
        const title = t("alert.orderStatus");
        const body = `${sym} · ${e.prev ?? "?"} → ${e.status ?? "?"}`;
        pushToast(title, body, e.status === "FILLED" ? "ok" : e.status === "CANCELED" || e.status === "REJECTED" || e.status === "EXPIRED" ? "bad" : "info");
        systemNotify(title, body);
      }
    });
    return off;
  }, [t]);

  const dismiss = (id: number) => {
    setItems((cur) => cur.filter((x) => x.id !== id));
    const timer = timers.current.get(id);
    if (timer) { clearTimeout(timer); timers.current.delete(id); }
  };

  if (items.length === 0) return null;
  return (
    <div className="fixed bottom-4 right-4 z-[9999] flex flex-col gap-2 w-[320px]">
      {items.map((it) => (
        <button key={it.id} onClick={() => dismiss(it.id)}
          className={`text-left rounded-lg border px-3 py-2.5 shadow-lg backdrop-blur transition-all ${KIND_CLS[it.kind]}`}>
          <div className="flex items-center gap-2">
            <span className={`w-1.5 h-1.5 rounded-full ${KIND_DOT[it.kind]}`} />
            <span className="font-mono text-[12px] font-semibold text-ink flex-1">{it.title}</span>
            <span className="font-mono text-[10px] text-ink-mute">✕</span>
          </div>
          <div className="font-mono text-[11px] text-ink-dim mt-1 leading-relaxed break-all">{it.body}</div>
        </button>
      ))}
    </div>
  );
}
