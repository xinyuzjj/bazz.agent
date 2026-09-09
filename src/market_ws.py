"""行情 WebSocket 实时流（v1.4.0；v1.5.0 增加强平流）

订阅币安全市场 ticker 免费流（无需 API Key）：
- 现货: wss://stream.binance.com:9443/ws/!miniTicker@arr （约 1s 推一次全市场快照）
  ※ 2026-09 实测：`!ticker@arr` 全市场数组流已被币安下线（SUBSCRIBE 仅回 ACK 不推数据），
    改用 `!miniTicker@arr`（字段缺 24h 涨跌幅，用 (c-o)/o 自算）。
- 合约: fstream.binance.com 实测连接后不推流（地域限制）→ 合约维度维持 REST 轮询，
  WS 缓存仅现货；前端 tick() 为空时自动回退显示 REST 值，无需特殊处理。
- 强平流(v1.5.0): wss://fstream.binance.com/ws/!forceOrder@arr 全市场强平单实时推送，
  内存缓冲最近 800 条 + 分钟桶统计；单边密集爆发（1 分钟单边清算 ≥ $1.5M 且
  ≥3× 20 分钟基线、笔数 ≥5）→ 事件总线提醒（toast + 系统通知，10 分钟冷却）。
  ※ 与合约 ticker 同域（fstream），受限地区可能不推流 → 面板显示重连中即可，无副作用。

设计要点：
- websockets.sync 客户端跑在 daemon 线程；proxy=True（默认）→ 读 HTTP(S)_PROXY/ALL_PROXY
  环境变量，与代理池（proxy_pool.bootstrap 注入 env）天然打通；socks 代理需 python-socks。
- 断线自动重连（退避 2s→60s 封顶）；60s 无消息视为 stale 主动断开重连。
- 内存缓存最近一次全量快照（SPOT / FUT 两个 dict），供 /api/ws 推送、REST 兜底与
  止损止盈监控读取。
- 事件总线 publish/drain：订单状态变更、SL/TP 提醒、爆仓潮等后端事件经 /api/ws 推给前端
  （应用内 toast + 系统通知）。
"""
import json
import threading
import time
from collections import deque
from typing import Optional

try:
    from websockets.sync.client import connect as ws_connect
except Exception:  # websockets 未安装（理论上 requirements 已带）→ 功能整体禁用但不崩
    ws_connect = None

STREAMS = {
    "spot": "wss://stream.binance.com:9443/ws/!miniTicker@arr",
    "liq":  "wss://fstream.binance.com/ws/!forceOrder@arr",   # v1.5.0 强平流
}
RECV_TIMEOUT = 60.0     # 超过该时长无任何消息 → 主动断开重连
BACKOFF_MAX = 60.0

_stop = threading.Event()
_threads: list = []

# —— 实时快照缓存 ——（每条为完整 dict，GIL 保证单键读写原子；足够本场景用）
_lock = threading.Lock()
_store: dict = {"spot": {}, "futures": {}}
_stats: dict = {
    "spot":    {"connected": False, "last_msg": 0.0, "msgs": 0, "reconnects": 0, "error": ""},
    "futures": {"connected": False, "last_msg": 0.0, "msgs": 0, "reconnects": 0, "error": ""},
    "liq":     {"connected": False, "last_msg": 0.0, "msgs": 0, "reconnects": 0, "error": ""},
}

# —— 强平流缓冲（v1.5.0） ——
# _liq_buf：最近 800 条强平单；_liq_min：分钟桶（每桶 {m, lq, sq, ln, sn}，
# lq/sq=多头/空头被强平的 USDT 金额，ln/sn=笔数）。side=SELL → 多头被强平(多爆)。
_liq_buf: deque = deque(maxlen=800)
_liq_min: deque = deque(maxlen=130)
_liq_burst_ts: dict = {}          # side -> last alert ts
LIQ_BURST_QUOTE = 1.5e6           # 1 分钟单边清算额门槛
LIQ_BURST_COUNT = 5               # 1 分钟单边笔数门槛
LIQ_BURST_XBASE = 3.0             # ≥ 3× 20 分钟基线
LIQ_BURST_COOLDOWN = 600.0        # 同方向 10 分钟冷却

# —— 后端事件总线（订单状态 / SL·TP 提醒 → 前端 toast + 系统通知） ——
# 带自增 seq：多客户端各自持游标增量拉取，互不抢占
_events: list = []
_ev_lock = threading.Lock()
_ev_seq = 0


def publish_event(type_: str, **fields) -> None:
    """推一条后端事件给所有已连接前端（type=alert / order …）。"""
    global _ev_seq
    ev = {"seq": 0, "type": type_, "ts": round(time.time(), 3)}
    ev.update(fields)
    with _ev_lock:
        _ev_seq += 1
        ev["seq"] = _ev_seq
        _events.append(ev)
        if len(_events) > 500:
            del _events[:len(_events) - 500]


def events_after(seq: int) -> list:
    """取 seq 之后的事件（各连接持自己的游标）。"""
    with _ev_lock:
        return [e for e in _events if e["seq"] > seq]


# ---------------- 快照读取 ----------------

def price(scope: str, symbol: str) -> Optional[float]:
    """某标的最新价（无数据返回 None）。scope: spot | futures"""
    t = _store.get(scope, {}).get(str(symbol).upper())
    return float(t["p"]) if t else None


def tick(scope: str, symbol: str) -> Optional[dict]:
    return _store.get(scope, {}).get(str(symbol).upper())


def tickers(scope: str, symbols: Optional[list] = None) -> dict:
    """快照：{SYMBOL: {p,c,q,h,l,t}}；symbols 为空给全量。"""
    src = _store.get(scope, {})
    if symbols:
        want = {str(s).upper() for s in symbols}
        return {k: v for k, v in src.items() if k in want}
    return dict(src)


def info() -> dict:
    """连接状态（前端 LIVE 徽标 / 调试）。"""
    with _lock:
        out = {}
        for k, v in _stats.items():
            d = dict(v)
            d["age"] = round(time.time() - v["last_msg"], 1) if v["last_msg"] else None
            out[k] = d
        return out


def has_scope_symbol(scope: str, symbol: str) -> bool:
    return str(symbol).upper() in _store.get(scope, {})


# ---------------- 强平流（v1.5.0 !forceOrder@arr） ----------------

def _apply_liq(rows: list) -> int:
    """把一帧强平单写入缓冲与分钟桶。返回入库条数。
    帧结构：{"e":"forceOrder","E":ms,"o":{"s":sym,"S":"SELL|BUY","q":qty,"ap":均价,"T":成交ms,...}}
    side=SELL → 多头被强平（多爆）；side=BUY → 空头被强平（空爆）。"""
    now = time.time()
    n = 0
    for r in rows:
        try:
            if not isinstance(r, dict):
                continue
            o = r.get("o")
            if not isinstance(o, dict):
                continue
            sym = str(o.get("s") or "").upper()
            side = str(o.get("S") or "").upper()
            if not sym or side not in ("SELL", "BUY"):
                continue
            price = float(o.get("ap") or o.get("p") or 0)
            qty = float(o.get("q") or 0)
            if price <= 0 or qty <= 0:
                continue
            ts = float(o.get("T") or r.get("E") or 0) / 1000.0 or now
            rec = {"ts": round(ts, 3), "symbol": sym, "side": side,
                   "kind": "long" if side == "SELL" else "short",
                   "price": price, "qty": qty, "quote": round(price * qty, 2)}
            _liq_buf.append(rec)
            m = int(ts // 60)
            if _liq_min and _liq_min[-1]["m"] == m:
                b = _liq_min[-1]
            else:
                b = {"m": m, "lq": 0.0, "sq": 0.0, "ln": 0, "sn": 0}
                _liq_min.append(b)
            if side == "SELL":
                b["lq"] += rec["quote"]; b["ln"] += 1
            else:
                b["sq"] += rec["quote"]; b["sn"] += 1
            n += 1
        except Exception:
            continue
    if n:
        with _lock:
            _stats["liq"]["msgs"] += 1
            _stats["liq"]["last_msg"] = now
        _liq_burst_check()
    return n


def _liq_burst_check() -> None:
    """单边密集爆发检测：当前分钟单边清算额 ≥ 门槛 且 ≥3× 前 20 分钟基线 且笔数 ≥5
    → publish_event（toast + 系统通知），同方向冷却 10 分钟。"""
    if len(_liq_min) < 4:
        return
    cur = _liq_min[-1]
    base = list(_liq_min)[:-1][-20:]
    now = time.time()
    for qk, nk, side in (("lq", "ln", "long"), ("sq", "sn", "short")):
        cur_q = cur[qk]
        if cur_q < LIQ_BURST_QUOTE or cur[nk] < LIQ_BURST_COUNT:
            continue
        vals = [b[qk] for b in base]
        avg = sum(vals) / len(vals) if vals else 0.0
        if avg > 0 and cur_q < LIQ_BURST_XBASE * avg and cur_q < avg + LIQ_BURST_QUOTE:
            continue
        if now - _liq_burst_ts.get(side, 0.0) < LIQ_BURST_COOLDOWN:
            continue
        _liq_burst_ts[side] = now
        # 本分钟按币聚合 top3
        agg: dict = {}
        floor_ts = cur["m"] * 60
        for rec in _liq_buf:
            if rec["ts"] >= floor_ts and rec["kind"] == side:
                agg[rec["symbol"]] = agg.get(rec["symbol"], 0.0) + rec["quote"]
        top = sorted(agg.items(), key=lambda x: x[1], reverse=True)[:3]
        publish_event("alert", kind="liq_burst", side=side,
                      quote=round(cur_q, 0), count=cur[nk],
                      top=[{"symbol": s, "quote": round(q, 0)} for s, q in top])


def liq_recent(limit: int = 80) -> list:
    """最近强平单（新→旧）。"""
    if limit <= 0:
        return []
    return list(_liq_buf)[-limit:][::-1]


def liq_stats(window: int = 300) -> dict:
    """窗口内强平统计（默认 5 分钟）：多/空爆笔数与金额。"""
    now = time.time()
    out = {"long_count": 0, "short_count": 0, "long_quote": 0.0, "short_quote": 0.0,
           "total_quote": 0.0, "window": window}
    for rec in reversed(_liq_buf):
        if now - rec["ts"] > window:
            break
        if rec["kind"] == "long":
            out["long_count"] += 1
            out["long_quote"] += rec["quote"]
        else:
            out["short_count"] += 1
            out["short_quote"] += rec["quote"]
    out["long_quote"] = round(out["long_quote"], 0)
    out["short_quote"] = round(out["short_quote"], 0)
    out["total_quote"] = round(out["long_quote"] + out["short_quote"], 0)
    return out


def liq_symbol_stats(symbol: str, window: int = 300) -> dict:
    """单币窗口内强平统计（scanner v2 确认层消费）：count/quote/side（主导方向）。"""
    sym = str(symbol).upper()
    now = time.time()
    count = 0
    lq = sq = 0.0
    for rec in reversed(_liq_buf):
        if now - rec["ts"] > window:
            break
        if rec["symbol"] != sym:
            continue
        count += 1
        if rec["kind"] == "long":
            lq += rec["quote"]
        else:
            sq += rec["quote"]
    side = ""
    if lq > sq and lq > 0:
        side = "long"
    elif sq > lq and sq > 0:
        side = "short"
    return {"count": count, "quote": round(lq + sq, 0), "side": side}


# ---------------- 解析 ----------------

def _apply(scope: str, rows: list) -> int:
    """把一帧 ticker 数组写入缓存。返回更新条数。"""
    out = {}
    now = time.time()
    for r in rows:
        try:
            sym = str(r.get("s") or "").upper()
            if not sym:
                continue
            c = float(r.get("c") or 0)            # 最新价
            # miniTicker 无 24h 涨跌幅字段 → 用 (c-o)/o 自算；老 ticker@arr 帧直接读 P
            chg = float(r["P"]) if r.get("P") is not None else (
                (c - float(r.get("o") or 0)) / float(r["o"]) * 100.0
                if r.get("o") and float(r.get("o") or 0) > 0 else 0.0)
            out[sym] = {
                "p": c,                               # 最新价
                "c": chg,                             # 24h 涨跌幅 %
                "q": float(r.get("q") or 0),          # 24h 成交额（quote）
                "h": float(r.get("h") or 0),
                "l": float(r.get("l") or 0),
                "t": round(now, 3),
            }
        except Exception:
            continue
    if not out:
        return 0
    with _lock:
        _store[scope].update(out)
        st = _stats[scope]
        st["last_msg"] = now
        st["msgs"] += 1
    return len(out)


def _parse_frame(raw) -> list:
    """兼容四种帧：纯数组 / {data:[...]} / 单对象（ticker 或 forceOrder）。"""
    m = json.loads(raw)
    if isinstance(m, list):
        return m
    if isinstance(m, dict):
        d = m.get("data")
        if isinstance(d, list):
            return d
        if m.get("s") or m.get("o") or m.get("e"):   # forceOrder 事件无顶层 s，有 o/e
            return [m]
    return []


# ---------------- 连接线程 ----------------

def _feed_loop(scope: str) -> None:
    url = STREAMS[scope]
    backoff = 2.0
    while not _stop.is_set():
        try:
            with _lock:
                _stats[scope]["error"] = ""
            # proxy=True（默认）：读 HTTP(S)_PROXY/ALL_PROXY env，代理池启用时自动走池
            ws = ws_connect(url, open_timeout=10, ping_interval=20, ping_timeout=15,
                            close_timeout=5, max_size=8 * 1024 * 1024)
            backoff = 2.0
            with _lock:
                _stats[scope]["connected"] = True
            try:
                while not _stop.is_set():
                    raw = ws.recv(timeout=RECV_TIMEOUT)
                    rows = _parse_frame(raw)
                    if rows:
                        if scope == "liq":
                            _apply_liq(rows)
                        else:
                            _apply(scope, rows)
            finally:
                try:
                    ws.close()
                except Exception:
                    pass
        except Exception as e:
            if _stop.is_set():
                break
            with _lock:
                st = _stats[scope]
                st["connected"] = False
                st["reconnects"] += 1
                st["error"] = str(e)[:200]
            _stop.wait(backoff)
            backoff = min(BACKOFF_MAX, backoff * 2)
        else:
            # recv 超时（TimeoutError 在 except 分支已处理）；此处处理正常退出循环
            with _lock:
                _stats[scope]["connected"] = False
    with _lock:
        _stats[scope]["connected"] = False


def start() -> None:
    """启动实时流行程（幂等；websockets 缺失时静默禁用）。"""
    if ws_connect is None:
        return
    if _threads and any(t.is_alive() for t in _threads):
        return
    _stop.clear()
    for scope in STREAMS:
        t = threading.Thread(target=_feed_loop, args=(scope,), daemon=True,
                             name=f"bazz-mktws-{scope}")
        t.start()
        _threads.append(t)
