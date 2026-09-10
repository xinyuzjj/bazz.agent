"""妖币追踪（v1.5.2）：启动前发现 → 后续暴涨/暴跌结局验证。

- 登记源：scanner.get_radar_v2 扫描完成后调用 record_from_radar(payload)，
  只登记 ignition 组（吸筹/点火 = 启动前窗口）的币，同币在跟踪中自动幂等。
- 跟踪：daemon 线程 60s 轮询 pending 记录；价格取 market_ws.price("spot") 内存快照
  （WS 未覆盖时回退 scanner.get_snapshot 60s 缓存），不新增外网请求路径。
- 结局：自发现价最大涨幅 ≥ +25% → moon；最大跌幅 ≤ -20% → dump；7 天未触发 → expired。
  关单即终态，market_ws.publish_event("alert", kind="radar_outcome", ...) 推前端 toast + 系统通知。
"""
import threading
import time

import state
import market_ws
import scanner

GAIN_HIT = 25.0    # 自发现价最大涨幅 % → moon（暴涨兑现）
DROP_HIT = 20.0    # 自发现价最大跌幅 % → dump（暴跌兑现）
TTL_SEC = 7 * 86400  # 跟踪期限：超时未触发 → expired（未兑现）
POLL_SEC = 60.0

_started = False


def record_from_radar(payload: dict) -> None:
    """雷达扫描完成后登记启动前（吸筹/点火）发现。任何异常静默，不拖垮雷达同步响应。
    方向取雷达行的 side：WATCH_SHORT → SHORT（做空观察），其余（LONG/WATCH）→ LONG。"""
    try:
        rows = (payload or {}).get("ignition") or []
        for r in rows:
            sym = str(r.get("symbol") or "").upper()
            px = float(r.get("price") or 0)
            if not sym or px <= 0:
                continue
            direction = "SHORT" if str(r.get("side") or "").upper() == "WATCH_SHORT" else "LONG"
            state.radar_track_add(sym, str(r.get("stage") or "IGNITION"), px,
                                  int(r.get("score") or 0), r.get("reasons") or [],
                                  direction=direction)
    except Exception:
        pass


def _current_price(sym: str, snap: dict):
    px = market_ws.price("spot", sym)
    if px is None:
        px = snap.get(sym)
    return float(px) if px else None


def _judge_outcome(ctx: dict, now: float, direction: str = "LONG") -> str:
    """ctx 来自 radar_track_progress：累计 max_gain/max_drop + found_at。
    方向感知（v1.5.7）：
      LONG（做多）：涨 ≥ +25% → moon（暴涨兑现）；跌 ≥ -20% → dump（做多失败）。
      SHORT（做空）：跌 ≥ 20% → moon（做空兑现/暴跌盈利）；涨 ≥ +25% → dump（做空失败）。
    判定规则不变，仅语义按方向翻转；7 天未触发 → expired。"""
    gain = ctx.get("max_gain") or 0
    drop = ctx.get("max_drop") or 0
    if direction == "SHORT":
        if drop >= DROP_HIT:
            return "moon"
        if gain >= GAIN_HIT:
            return "dump"
    else:
        if gain >= GAIN_HIT:
            return "moon"
        if drop >= DROP_HIT:
            return "dump"
    if now - (ctx.get("found_at") or 0) > TTL_SEC:
        return "expired"
    return ""


def _tick() -> None:
    tracks = state.radar_tracks_list("pending")
    if not tracks:
        return
    try:
        snap = {r["symbol"]: float(r.get("price") or 0) for r in scanner.get_snapshot()}
    except Exception:
        snap = {}
    now = time.time()
    for t in tracks:
        try:
            sym = t["symbol"]
            px = _current_price(sym, snap)
            if not px:
                continue
            ctx = state.radar_track_progress(t["id"], px)
            if not ctx:
                continue
            outcome = _judge_outcome(ctx, now, t.get("direction") or "LONG")
            if outcome and state.radar_track_close(t["id"], outcome, px):
                market_ws.publish_event(
                    "alert", kind="radar_outcome", outcome=outcome, symbol=sym,
                    stage=t.get("stage") or "", found_price=ctx["found"], price=px,
                    max_gain_pct=round(ctx["max_gain"], 2),
                    max_drop_pct=round(ctx["max_drop"], 2))
        except Exception:
            continue


def ensure_started() -> None:
    """启动跟踪 daemon 线程（幂等）。"""
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

    threading.Thread(target=_loop, daemon=True, name="bazz-radar-tracker").start()


def tracks_view() -> dict:
    """GET /api/market/radar/tracks 数据：进行中 + 历史（按关单时间降序 50 条）+ 战绩统计。"""
    history = [h for h in state.radar_tracks_list("closed")]
    history.sort(key=lambda h: h.get("closed_at") or 0, reverse=True)
    return {
        "pending": state.radar_tracks_list("pending"),
        "history": history[:50],
        "stats": state.radar_tracks_stats(),
        "ts": time.time(),
    }
