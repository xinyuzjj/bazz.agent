// 行情实时流（v1.4.0）：单例 WebSocket（/api/ws）+ 订阅管理 + 微渲染订阅。
// 设计：tick 存 Map，行组件用 useSyncExternalStore 各自订阅自己的 symbol ——
// 只有价格变化的行重渲，父组件（整表）不随 1s tick 重渲。
import { useSyncExternalStore } from "react";

export type Scope = "spot" | "futures";
export type Tick = { p: number; c: number; q: number; h: number; l: number; t: number };

const tickMaps: Record<Scope, Map<string, Tick>> = { spot: new Map(), futures: new Map() };
const wanted: Record<Scope, Set<string>> = { spot: new Set(), futures: new Set() };

export type WsStatus = "init" | "on" | "off";
let status: WsStatus = "init";

const tickSubs = new Set<() => void>();
const statusSubs = new Set<() => void>();
const alertSubs = new Set<(e: any) => void>();

let ws: WebSocket | null = null;
let attempts = 0;
let timer: any = null;
let lastMsgAt = 0;
let sentSig = "";

function notify(subs: Set<() => void>) { subs.forEach((f) => { try { f(); } catch { /* noop */ } }); }

function subSig(): string {
  const s = (sc: Scope) => [...wanted[sc]].sort().join(",");
  return `spot:${s("spot")}|fut:${s("futures")}`;
}

function flushSubs(force = false) {
  const sig = subSig();
  if (!force && sig === sentSig) return;
  sentSig = sig;
  if (ws && ws.readyState === WebSocket.OPEN) {
    try { ws.send(JSON.stringify({ type: "sub", spot: [...wanted.spot], futures: [...wanted.futures] })); } catch { /* noop */ }
  }
}

function connect() {
  if (timer) { clearTimeout(timer); timer = null; }
  try {
    const token = String((window as any)?.bazzAuth?.token || "");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/api/ws${token ? `?token=${encodeURIComponent(token)}` : ""}`);
  } catch {
    scheduleReconnect();
    return;
  }
  ws.onopen = () => {
    attempts = 0;
    status = "on";
    lastMsgAt = Date.now();
    notify(statusSubs);
    sentSig = "";
    flushSubs(true);
  };
  ws.onmessage = (ev) => {
    lastMsgAt = Date.now();
    let m: any;
    try { m = JSON.parse(ev.data); } catch { return; }
    if (m?.type === "frame") {
      let dirty = false;
      for (const scope of ["spot", "futures"] as Scope[]) {
        const map = tickMaps[scope];
        const incoming = m.ticks?.[scope] ?? {};
        for (const [sym, t] of Object.entries(incoming as Record<string, Tick>)) {
          const prev = map.get(sym);
          // diff 合并：渲染相关字段（价/涨跌幅/量/高低）全部未变 → 保留旧对象引用，
          // useSyncExternalStore 的 Object.is 比较命中，下游组件不重渲（t 为时间戳，无展示方，可随旧引用保留）
          if (prev && prev.p === t.p && prev.c === t.c && prev.q === t.q && prev.h === t.h && prev.l === t.l) {
            continue;
          }
          map.set(sym, t);
          dirty = true;
        }
      }
      if (dirty) notify(tickSubs);
    }
    if (Array.isArray(m?.events)) {
      m.events.forEach((e: any) => alertSubs.forEach((f) => { try { f(e); } catch { /* noop */ } }));
    }
    // conn 状态变化才广播
    const on = !!(m?.conn?.spot?.connected || m?.conn?.futures?.connected);
    const next: WsStatus = on ? "on" : status === "init" ? "init" : "off";
    if (next !== status) { status = next; notify(statusSubs); }
  };
  ws.onclose = () => {
    ws = null;
    if (status !== "off") { status = "off"; notify(statusSubs); }
    scheduleReconnect();
  };
  ws.onerror = () => { try { ws?.close(); } catch { /* noop */ } };
}

function scheduleReconnect() {
  if (timer) return;
  const delay = Math.min(30000, 1000 * Math.pow(2, Math.min(attempts, 5)));
  attempts += 1;
  timer = setTimeout(() => { timer = null; connect(); }, delay);
}

// 心跳：20s ping；90s 无消息强制重连
setInterval(() => {
  if (ws && ws.readyState === WebSocket.OPEN) {
    try { ws.send(JSON.stringify({ type: "ping" })); } catch { /* noop */ }
    if (Date.now() - lastMsgAt > 90_000) { try { ws.close(); } catch { /* noop */ } }
  }
}, 20_000);

// 懒连接：首个订阅者出现时才建 WS（App 全局 toast 也会订阅，因此实际等于启动即连）
function ensureConnected() {
  if (!ws && !timer) connect();
}

// ---------------- 订阅 API ----------------

// 引用计数：多个视图可订阅同一 symbol，全部释放才真正退订
const refcnt = new Map<string, number>();
const keyOf = (scope: Scope, s: string) => `${scope}:${s.toUpperCase()}`;

export function subscribeTicks(scope: Scope, symbols: string[]): () => void {
  ensureConnected();
  const added: string[] = [];
  const keys: string[] = [];
  for (const raw of symbols) {
    const s = String(raw || "").toUpperCase();
    if (!s) continue;
    const key = keyOf(scope, s);
    const n = (refcnt.get(key) || 0) + 1;
    refcnt.set(key, n);
    keys.push(key);
    if (n === 1 && !wanted[scope].has(s)) { wanted[scope].add(s); added.push(s); }
  }
  if (added.length) flushSubs();
  let released = false;
  return () => {
    if (released) return;
    released = true;
    const removed: string[] = [];
    for (const key of keys) {
      const n = (refcnt.get(key) || 0) - 1;
      if (n <= 0) { refcnt.delete(key); removed.push(key.split(":")[1]); }
      else refcnt.set(key, n);
    }
    if (removed.length) {
      removed.forEach((s) => wanted[scope].delete(s));
      flushSubs();
    }
  };
}

export function useLiveTick(scope: Scope, symbol: string): Tick | undefined {
  return useSyncExternalStore(
    (cb) => { tickSubs.add(cb); return () => tickSubs.delete(cb); },
    () => tickMaps[scope].get(String(symbol).toUpperCase()),
    () => undefined,
  );
}

export function useWsStatus(): WsStatus {
  return useSyncExternalStore(
    (cb) => { statusSubs.add(cb); return () => statusSubs.delete(cb); },
    () => status,
    () => "init" as WsStatus,
  );
}

export function subscribeAlerts(cb: (e: any) => void): () => void {
  ensureConnected();
  alertSubs.add(cb);
  return () => alertSubs.delete(cb);
}
