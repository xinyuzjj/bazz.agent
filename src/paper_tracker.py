"""广场发文模拟挂单跟踪（v1.5.61）。

发文成功后按文章 SMC 计划建纸单（100U 保证金 × 10x），本模块后台每 60s 复查一次
价格，驱动状态机：pending（挂单中）→ open（已入场）→ win（已获利）/ loss（已亏损），
7 天观察期到期强制结算（未入场 → noentry）。

_eval() 为纯函数（输入 K 线高低收，输出状态变更 dict），便于离线单测。
不接真实下单，不碰 exchange/wallet 路由。
"""
import threading
import time

import state

POLL_SEC = 60
WINDOW_DAYS = 7          # 观察期：到期强制结算（用户定版）
_WIN = "win"
_LOSS = "loss"


def _pnl_pct(order: dict, close: float) -> float:
    """按杠杆折算的保证金收益率（%）。多头 (close/entry-1)*lev；空头 (entry/close-1)*lev。"""
    entry = float(order.get("entry") or 0)
    lev = float(order.get("leverage") or 10)
    if not entry or not close:
        return 0.0
    if order.get("direction") == "short":
        return round((entry / close - 1) * lev * 100, 2)
    return round((close / entry - 1) * lev * 100, 2)


def _eval(order: dict, high: float, low: float, close: float, now: float = None) -> dict:
    """状态机核心（纯函数）。order 需含 status/direction/entry/zone_lo/zone_hi/stop/tp/
    created_at/filled_ts/leverage。返回 paper_update 可用的字段 dict；无变化返回 {}。
    同一根 K 同时触及止盈与止损 → 保守判止损（无法知道先后，按坏情况记账）。"""
    now = now or time.time()
    st = order.get("status")
    d = order.get("direction")
    entry = float(order.get("entry") or 0)
    stop = float(order.get("stop") or 0)
    tp = float(order.get("tp") or 0)
    zlo = float(order.get("zone_lo") or 0)
    zhi = float(order.get("zone_hi") or 0)
    if not entry or not (high and low and close):
        return {}

    out: dict = {"last_price": close}

    if st == "pending":
        # 到期未入场 → noentry（文章观点 7 天内没触发）
        if now - float(order.get("created_at") or now) > WINDOW_DAYS * 86400:
            out.update({"status": "noentry", "closed_ts": now, "close_price": close,
                        "pnl_pct": 0.0})
            return out
        # 触及入场区（多头：K 线低点探到区上沿即第一触点成交；空头镜像）
        touched = (low <= zhi) if d == "long" else (high >= zlo) \
            if (zlo and zhi) else (low <= entry if d == "long" else high >= entry)
        if touched:
            out.update({"status": "open", "filled_ts": now})
        return out

    if st == "open":
        filled_ts = float(order.get("filled_ts") or order.get("created_at") or now)
        if d == "long":
            if stop and low <= stop:
                out.update({"status": _LOSS, "closed_ts": now, "close_price": stop,
                            "pnl_pct": _pnl_pct(order, stop)})
                return out
            if tp and high >= tp:
                out.update({"status": _WIN, "closed_ts": now, "close_price": tp,
                            "pnl_pct": _pnl_pct(order, tp)})
                return out
        else:
            if stop and high >= stop:
                out.update({"status": _LOSS, "closed_ts": now, "close_price": stop,
                            "pnl_pct": _pnl_pct(order, stop)})
                return out
            if tp and low <= tp:
                out.update({"status": _WIN, "closed_ts": now, "close_price": tp,
                            "pnl_pct": _pnl_pct(order, tp)})
                return out
        # 观察期到期：按现价强制结算
        if now - filled_ts > WINDOW_DAYS * 86400:
            p = _pnl_pct(order, close)
            out.update({"status": _WIN if p > 0 else _LOSS, "closed_ts": now,
                        "close_price": close, "pnl_pct": p})
        return out

    # win/loss/noentry 终态：不再变化
    return {}


def _fetch_bar(symbol: str, market: str):
    """取最近两根 5m K 线的 (high, low, close)，覆盖触发判断。"""
    from scanner import klines_ohlcv
    k = klines_ohlcv(symbol, interval="5m", limit=2, market=market or "futures") or {}
    highs, lows, closes = k.get("highs") or [], k.get("lows") or [], k.get("closes") or []
    if not highs or not closes:
        return None
    return max(highs), min(lows), closes[-1]


def _tick() -> int:
    """一轮复查，返回发生状态迁移的单数。"""
    try:
        orders = [o for o in state.paper_list()
                  if o.get("status") in ("pending", "open")]
    except Exception:
        return 0
    moved = 0
    for o in orders:
        try:
            bar = _fetch_bar(o.get("symbol"), o.get("market"))
            if not bar:
                continue
            updates = _eval(o, *bar)
            if not updates:
                continue
            if updates.get("status") and updates["status"] != o.get("status"):
                moved += 1
                try:
                    import market_ws
                    market_ws.publish_event("paper_order", event="status", id=o["id"],
                                            symbol=o.get("symbol"),
                                            status=updates["status"], prev=o.get("status"),
                                            pnl_pct=updates.get("pnl_pct"))
                except Exception:
                    pass
            state.paper_update(o["id"], **updates)
        except Exception:
            continue
    return moved


def ensure_started() -> None:
    """启动模拟挂单复查 daemon 线程（幂等）。"""
    global _started
    if _started:
        return
    _started = True

    def _loop():
        while True:
            try:
                _tick()
            except Exception:
                pass
            time.sleep(POLL_SEC)   # 先跑一轮再 sleep（与 order_tracker 同节奏）

    threading.Thread(target=_loop, daemon=True, name="bazz-paper-tracker").start()


_started = False
