// v1.5.4 迷你走势图：/api/market/klines（后端 5min 缓存）→ 前端再共享 5min 内存缓存，
// Hero 卡 / 详情浮层 / 任意行内复用同一份收盘价序列。
import React, { useEffect, useId, useState } from "react";
import { api } from "../api";

const _cache = new Map<string, { ts: number; closes: number[] }>();
const _fetching = new Set<string>();
const _sub = new Map<string, Set<(c: number[]) => void>>();
const TTL = 300_000;

async function _ensure(key: string, symbol: string, interval: string, limit: number, market: string) {
  const hit = _cache.get(key);
  if (hit && Date.now() - hit.ts < TTL) return;
  if (_fetching.has(key)) return;
  _fetching.add(key);
  try {
    const d: any = await api.marketKlines(symbol, interval, limit, market);
    const closes: number[] = Array.isArray(d?.closes) ? d.closes : [];
    _cache.set(key, { ts: Date.now(), closes });
    _sub.get(key)?.forEach((f) => { try { f(closes); } catch { /* noop */ } });
  } catch { /* 网络失败保持旧数据 */ }
  finally { _fetching.delete(key); }
}

export function Sparkline({ symbol, market = "spot", interval = "1h", limit = 24, className = "h-8" }: {
  symbol: string;
  market?: "spot" | "futures";
  interval?: string;
  limit?: number;
  className?: string; // 控制高度（宽度随容器 100%）
}) {
  const key = `${market}:${symbol}:${interval}:${limit}`;
  const gid = useId().replace(/[:]/g, "");
  const [closes, setCloses] = useState<number[]>(() => _cache.get(key)?.closes ?? []);

  useEffect(() => {
    setCloses(_cache.get(key)?.closes ?? []);
    const set = _sub.get(key) ?? new Set<(c: number[]) => void>();
    set.add(setCloses);
    _sub.set(key, set);
    _ensure(key, symbol, interval, limit, market);
    return () => { set.delete(setCloses); };
  }, [key]);

  if (closes.length < 2) {
    return <div className={`w-full rounded bg-line/30 shimmer ${className}`} />;
  }

  const H = 30;
  const first = closes[0];
  const last = closes[closes.length - 1];
  const up = last >= first;
  const mn = Math.min(...closes);
  const mx = Math.max(...closes);
  const span = mx - mn || 1;
  const pts = closes.map((c, i) => {
    const x = (i / (closes.length - 1)) * 100;
    const y = H - 2 - ((c - mn) / span) * (H - 4);
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });
  const stroke = up ? "var(--green)" : "var(--red)";

  return (
    <svg viewBox={`0 0 100 ${H}`} preserveAspectRatio="none"
      className={`w-full ${className}`} style={{ display: "block" }}>
      <defs>
        <linearGradient id={`sg${gid}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.22" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon points={`0,${H} ${pts.join(" ")} 100,${H}`} fill={`url(#sg${gid})`} />
      <polyline points={pts.join(" ")} fill="none" stroke={stroke} strokeWidth="1.6"
        vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx="100" cy={pts[pts.length - 1].split(",")[1]} r="1.6" fill={stroke}
        vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
