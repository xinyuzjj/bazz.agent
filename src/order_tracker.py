"""订单跟踪 + 止损止盈提醒（v1.4.0）

下单成功后（executor.confirm_and_place）自动登记 tracked_orders（state.db 持久化，重启续跟）：
- exchange 通道：每 5s 签名查 /api/v3/order 同步状态（NEW / PARTIALLY_FILLED / FILLED /
  CANCELED / REJECTED / EXPIRED），状态变化 → market_ws.publish_event 推前端
- wallet 通道：baw swap 即时成交 → 直接记 FILLED
- 止损/止盈监控：对 active 订单用 market_ws 实时价判「接近」（距触发价 0.5% 内）与
  「触发」（穿越），触发后该方向 6h 静默，避免重复轰炸

事件格式（/api/ws → 前端 toast + 系统通知）：
  {"type":"order", "event":"status", "id","order_id","symbol","status","prev"}
  {"type":"alert", "kind":"sl_near|sl_hit|tp_near|tp_hit", "symbol","price","entry","level"}
"""
import threading
import time

import state

try:
    import market_ws
except Exception:
    market_ws = None

POLL_SEC = 5.0
NEAR_RATIO = 0.005     # 距触发价 0.5% 内 → 接近
NEAR_COOLDOWN = 600.0  # 同一订单同一提醒 10 分钟静默
HIT_COOLDOWN = 6 * 3600.0

_started = False
_cooldown: dict = {}   # (tid, kind) -> last_ts

# 查单终态（不再轮询交易所同步状态；FILLED/DONE 说明订单生命周期已结束）
_CLOSED = {"CANCELED", "REJECTED", "EXPIRED", "FILLED", "DONE", "FAILED"}
# v1.5.28（F04 修复）：提醒监控的终态。FILLED 只是订单成交——持仓还活着，
# 止损/止盈必须继续监控。此前 FILLED 混在 _CLOSED 里导致「未成交时有提醒，
# 真正成交后反而静默」。注意：提醒只是提示，不是止损执行，触发后靠冷却限频。
_CLOSED_FOR_ALERTS = {"CANCELED", "REJECTED", "EXPIRED", "FAILED", "DONE"}


def _warn_price_scope(symbol: str) -> str:
    """优先合约价（股票化代币/永续），否则现货价。"""
    if market_ws and market_ws.has_scope_symbol("futures", symbol):
        return "futures"
    return "spot"


def track_from_result(res: dict, signal: dict) -> None:
    """executor 下单成功后登记跟踪（异常静默，不影响下单结果返回）。"""
    try:
        if not res or res.get("error") or not state:
            return
        symbol = str(res.get("symbol") or signal.get("symbol") or "").upper()
        if not symbol:
            return
        route = "wallet" if str(res.get("orderId", "")) == "WALLET" else \
            str(signal.get("route") or "exchange")
        status = str(res.get("status") or "NEW").upper()
        if route == "wallet":
            status = "FILLED"  # baw swap 即时成交
        state.track_add(
            order_id=str(res.get("orderId") or "N/A"),
            symbol=symbol,
            route=route,
            direction=str(signal.get("direction") or "BULLISH"),
            quantity=str(signal.get("quantity") or res.get("executedQty") or "0"),
            entry=float(signal.get("price") or 0),
            stop_loss=float(signal.get("stop_loss") or 0),
            take_profit=float(signal.get("take_profit") or 0),
            status=status,
            note=str(res.get("command") or "")[:200],
        )
        if market_ws:
            market_ws.publish_event("order", event="tracked", symbol=symbol, status=status,
                                    order_id=str(res.get("orderId") or ""))
    except Exception:
        pass


def untrack(tid: str) -> None:
    state.track_remove(tid)
    if market_ws:
        market_ws.publish_event("order", event="removed", id=tid)


def _cool(tid: str, kind: str) -> bool:
    """冷却期内返回 True（不发提醒）。"""
    key = (tid, kind)
    now = time.time()
    last = _cooldown.get(key, 0.0)
    limit = HIT_COOLDOWN if kind.endswith("_hit") else NEAR_COOLDOWN
    if now - last < limit:
        return True
    _cooldown[key] = now
    return False


def _check_sl_tp(o: dict) -> None:
    """对 active 订单做止损/止盈接近与触发检测（多头：SL<entry<TP；空头镜像）。"""
    if not market_ws:
        return
    if not o.get("active") or o.get("status") in _CLOSED_FOR_ALERTS:
        return
    sl, tp = float(o.get("stop_loss") or 0), float(o.get("take_profit") or 0)
    if sl <= 0 and tp <= 0:
        return
    sym = o["symbol"]
    px = market_ws.price(_warn_price_scope(sym), sym)
    if not px or px <= 0:
        return
    long = str(o.get("direction") or "BULLISH").upper() in ("BULLISH", "做多", "LONG", "BUY")
    tid = o["id"]
    if sl > 0:
        near = (px - sl) / sl if long else (sl - px) / sl
        hit = px <= sl if long else px >= sl
        if hit:
            if not _cool(tid, "sl_hit"):
                market_ws.publish_event("alert", kind="sl_hit", symbol=sym, price=px,
                                        entry=float(o.get("entry") or 0), level=sl)
        elif 0 <= near <= NEAR_RATIO:
            if not _cool(tid, "sl_near"):
                market_ws.publish_event("alert", kind="sl_near", symbol=sym, price=px,
                                        entry=float(o.get("entry") or 0), level=sl)
    if tp > 0:
        near = (tp - px) / tp if long else (px - tp) / tp
        hit = px >= tp if long else px <= tp
        if hit:
            if not _cool(tid, "tp_hit"):
                market_ws.publish_event("alert", kind="tp_hit", symbol=sym, price=px,
                                        entry=float(o.get("entry") or 0), level=tp)
        elif 0 <= near <= NEAR_RATIO:
            if not _cool(tid, "tp_near"):
                market_ws.publish_event("alert", kind="tp_near", symbol=sym, price=px,
                                        entry=float(o.get("entry") or 0), level=tp)


def _refresh_exchange_status(o: dict) -> None:
    """exchange 通道未终态订单：签名查最新状态，变化则落库 + 推事件。"""
    if market_ws is None:
        return
    if o.get("route") != "exchange" or o.get("status") in _CLOSED:
        return
    try:
        import cex_wallet
        keys = cex_wallet.get_keypair()
        if not keys or not keys[0] or not keys[1]:
            return
        res = cex_wallet._get_signed("/api/v3/order",
                                     {"symbol": o["symbol"], "orderId": o["order_id"]},
                                     keys[0], keys[1])
        new_status = str(res.get("status") or "").upper()
        if new_status and new_status != str(o.get("status") or "").upper():
            state.track_set_status(o["id"], new_status)
            market_ws.publish_event("order", event="status", id=o["id"],
                                    order_id=o["order_id"], symbol=o["symbol"],
                                    status=new_status, prev=o.get("status"))
    except Exception:
        pass  # 网络/权限问题静默，下轮再试


def _tick() -> None:
    try:
        orders = state.track_list()
    except Exception:
        return
    for o in orders:
        try:
            _refresh_exchange_status(o)
            _check_sl_tp(o)
        except Exception:
            continue


def ensure_started() -> None:
    """启动监控 daemon 线程（幂等）。"""
    global _started
    if _started:
        return
    _started = True

    def _loop():
        while True:
            time.sleep(POLL_SEC)
            try:
                _tick()
            except Exception:
                pass

    threading.Thread(target=_loop, daemon=True, name="bazz-order-tracker").start()
