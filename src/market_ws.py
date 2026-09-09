"""行情 WebSocket 实时流（v1.4.0）

订阅币安全市场 ticker 免费流（无需 API Key）：
- 现货: wss://stream.binance.com:9443/ws/!miniTicker@arr （约 1s 推一次全市场快照）
  ※ 2026-09 实测：`!ticker@arr` 全市场数组流已被币安下线（SUBSCRIBE 仅回 ACK 不推数据），
    改用 `!miniTicker@arr`（字段缺 24h 涨跌幅，用 (c-o)/o 自算）。
- 合约: fstream.binance.com 实测连接后不推流（地域限制）→ 合约维度维持 REST 轮询，
  WS 缓存仅现货；前端 tick() 为空时自动回退显示 REST 值，无需特殊处理。

设计要点：
- websockets.sync 客户端跑在 daemon 线程；proxy=True（默认）→ 读 HTTP(S)_PROXY/ALL_PROXY
  环境变量，与代理池（proxy_pool.bootstrap 注入 env）天然打通；socks 代理需 python-socks。
- 断线自动重连（退避 2s→60s 封顶）；60s 无消息视为 stale 主动断开重连。
- 内存缓存最近一次全量快照（SPOT / FUT 两个 dict），供 /api/ws 推送、REST 兜底与
  止损止盈监控读取。
- 事件总线 publish/drain：订单状态变更、SL/TP 提醒等后端事件经 /api/ws 推给前端
  （应用内 toast + 系统通知）。
"""
import json
import threading
import time
from typing import Optional

try:
    from websockets.sync.client import connect as ws_connect
except Exception:  # websockets 未安装（理论上 requirements 已带）→ 功能整体禁用但不崩
    ws_connect = None

STREAMS = {
    "spot": "wss://stream.binance.com:9443/ws/!miniTicker@arr",
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
}

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
    """兼容三种帧：纯数组 / {data:[...]} / 单对象。"""
    m = json.loads(raw)
    if isinstance(m, list):
        return m
    if isinstance(m, dict):
        d = m.get("data")
        if isinstance(d, list):
            return d
        if m.get("s"):
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
