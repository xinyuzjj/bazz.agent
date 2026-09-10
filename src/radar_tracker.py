"""妖币追踪（v1.5.2）：启动前发现 → 后续暴涨/暴跌结局验证。

- 登记源：scanner.get_radar_v2 扫描完成后调用 record_from_radar(payload)，
  只登记 ignition 组（吸筹/点火/做空埋伏 = 启动前窗口）的币，同币在跟踪中自动幂等。
- 跟踪：daemon 线程 60s 轮询 pending 记录；价格取 market_ws.price("spot") 内存快照
  （WS 未覆盖时回退 scanner.get_snapshot 60s 缓存），不新增外网请求路径。
- 结局（v1.5.8，按 10x 合约杠杆口径，仓位模拟 100U×10x）：逆向 ≥10%（近强平线）→ dump（失败）；
  顺向 ≥25% 达标不直接关单——判反转因子（费率极值回落/OI 脉冲/爆仓潮）：现反转 → 落袋 moon，
  否则转持有模式移动止盈（自持有期极值回撤/反弹 ≥12% → moon）；7 天未触发 → expired。
  关单即终态，market_ws.publish_event("alert", kind="radar_outcome", ...) 推前端 toast + 系统通知。
"""
import json
import os
import threading
import time

import state
import market_ws
import scanner
import workspace

GAIN_HIT = 25.0    # 顺向最大波动 % → 达标（不再直接关单：判定反转则落袋，否则持有移动止盈）
FAIL_HIT = 10.0    # 逆向最大波动 % → dump（失败）：按 10x 合约杠杆计，逆向 ~10% 即近强平线
TRAIL_PCT = 12.0   # 持有期移动止盈：自持有期极值回撤/反弹 ≥12% → moon 落袋
POS_USDT = 100.0   # 仓位模拟：每只妖币默认 100U 本金
LEVERAGE = 10      # 仓位模拟：10x 合约
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


def _sim_pnl(direction: str, found: float, px: float) -> tuple:
    """仓位模拟（100U 本金 × 10x 合约，爆仓封底 -100U）。返回 (pnl_usdt, roi_pct)。"""
    if not found or not px:
        return 0.0, 0.0
    chg = (float(px) / float(found) - 1.0) * 100.0
    roi = (-chg if direction == "SHORT" else chg) * LEVERAGE
    return round(max(-POS_USDT, POS_USDT * roi / 100.0), 2), round(roi, 1)


def _reversal_now(sym: str, direction: str) -> bool:
    """达标瞬间是否出现反转因子（取最近一次雷达缓存，零外呼）：是→直接落袋 moon；否→持有。
    无因子数据（缓存缺失）默认继续持有——真妖币不会只到 25% 就结束。"""
    f = (_last_scan_row(sym).get("factors") or {})
    if not f:
        return False
    fund, fund_pk = f.get("funding"), f.get("funding_peak")
    oi15, liq5, liqside = f.get("oi_pulse15"), f.get("liq_5m") or 0, f.get("liq_side")
    if direction == "SHORT":
        return bool((fund is not None and fund <= 0)
                    or (oi15 is not None and oi15 >= 5.0)
                    or (liqside == "short" and liq5 >= 5e5))
    return bool((fund_pk and fund is not None and fund_pk >= 0.003 and fund <= 0.4 * fund_pk)
                or (oi15 is not None and oi15 <= -5.0)
                or (liqside == "long" and liq5 >= 5e5))


def _judge_outcome(ctx: dict, now: float, direction: str = "LONG") -> str:
    """ctx 来自 radar_track_progress：累计 max_gain/max_drop + 当前 gain/drop + holding/hold_ext + found_at。
    方向感知（v1.5.8，按 10x 合约杠杆口径 + 持有模式）：
      未持有：仅判 dump（逆向 ≥10% 近强平）与 expired；顺向达标（≥25%）由 _tick 决定落袋/持有。
      持有中：自持有期极值回撤（做多）/反弹（做空）≥12% → moon 移动止盈落袋；逆向 ≥10% 仍 → dump（爆仓优先）。
    7 天未触发 → expired。"""
    gain = ctx.get("max_gain") or 0
    drop = ctx.get("max_drop") or 0
    found = float(ctx.get("found") or 0)
    holding = bool(ctx.get("holding"))
    if direction == "SHORT":
        if gain >= FAIL_HIT:
            return "dump"
        if holding:
            ext = float(ctx.get("hold_ext") or 0)
            px = found * (1.0 - (ctx.get("drop") or 0) / 100.0)
            if ext > 0 and px > 0 and (px - ext) / ext * 100.0 >= TRAIL_PCT:
                return "moon"
    else:
        if drop >= FAIL_HIT:
            return "dump"
        if holding:
            ext = float(ctx.get("hold_ext") or 0)
            px = found * (1.0 + (ctx.get("gain") or 0) / 100.0)
            if ext > 0 and px > 0 and (ext - px) / ext * 100.0 >= TRAIL_PCT:
                return "moon"
    if now - (ctx.get("found_at") or 0) > TTL_SEC:
        return "expired"
    return ""


def _fmt_price(p: float) -> str:
    p = float(p or 0)
    if p <= 0:
        return "—"
    return f"{p:.8f}".rstrip("0").rstrip(".") if p < 1 else f"{p:,.4f}"


def _last_scan_row(sym: str) -> dict:
    """取最近一次雷达扫描中该币的行（含 factors/env），零外呼；无缓存返回 {}。"""
    try:
        data = (getattr(scanner, "_radar2_cache", {}) or {}).get("data") or {}
        for r in (data.get("coins") or []):
            if r.get("symbol") == sym:
                r = dict(r)
                r["_env"] = data.get("env")
                return r
    except Exception:
        pass
    return {}


def _build_failure_review(t: dict, ctx: dict, px: float, now: float) -> str:
    """dump 关单时生成失败复盘（启发式，零外呼）：失败路径 + 关单时因子 + 大盘环境 + 教训。"""
    direction = t.get("direction") or "LONG"
    stage = t.get("stage") or ""
    found = float(ctx.get("found") or 0)
    gain = float(ctx.get("max_gain") or 0)
    drop = float(ctx.get("max_drop") or 0)
    dur_m = max(0, int((now - float(ctx.get("found_at") or now)) / 60))
    dur = f"{dur_m // 60}h{dur_m % 60:02d}m" if dur_m >= 60 else f"{dur_m}m"

    if direction == "SHORT":
        path = "直接轧空上行（未给回踩）" if gain >= FAIL_HIT * 0.9 and drop <= 2.0 \
            else f"先探低 -{drop:.1f}% 后反转拉升（止损/反弹打掉）"
        lessons = {
            "SHORT_AMBUSH": "高位过热 ≠ 立即回落，主力常借拥挤度逼空二次拉升；左侧埋伏只小仓，加仓需等趋势破位确认",
        }.get(stage, "做空需等趋势破位确认再介入，左侧逆势单严设 10x 口径止损（+10% 即离场）")
        head = (f"做空失败：自发现价 {_fmt_price(found)} 逆向上涨 +{gain:.1f}%（≥{FAIL_HIT:.0f}% ≈ 10x 强平线），"
                f"历时 {dur}。失败路径：{path}。")
    else:
        if gain >= 3.0:
            path = f"先冲高 +{gain:.1f}% 后回落破位（假突破/冲高未离场）"
        else:
            path = "登记后直接破位下行（未启动即失败）"
        lessons = {
            "ACCUMULATION": "吸筹证伪：低位放量并非吸筹而是派发出货——破位即应止损，勿与『主力成本区』叙事共情",
            "IGNITION": f"点火失败（{'假突破' if gain >= 3.0 else '点火即灭'}）：突破无承接量回落——放量突破需回踩平台不破才算有效，否则小仓试错快速认错",
        }.get(stage, "逆向跌破发现价 10% 即认错离场，10x 口径下已近强平")
        head = (f"做多失败：自发现价 {_fmt_price(found)} 逆向下跌 -{drop:.1f}%（≥{FAIL_HIT:.0f}% ≈ 10x 强平线），"
                f"历时 {dur}。失败路径：{path}。")

    seg = [head]
    row = _last_scan_row(t.get("symbol") or "")
    f = row.get("factors") or {}
    bits = []
    if f.get("funding") is not None:
        bits.append(f"费率 {f['funding'] * 100:+.3f}%")
    if f.get("oi_chg24") is not None:
        bits.append(f"OI 24h {f['oi_chg24']:+.1f}%")
    if f.get("top_ratio") is not None:
        bits.append(f"大户比 {f['top_ratio']:.2f}")
    if f.get("taker_ratio") is not None:
        bits.append(f"taker {f['taker_ratio']:.2f}")
    if f.get("liq_5m"):
        bits.append(f"5m爆仓 ${f['liq_5m'] / 1e6:.1f}M")
    if bits:
        seg.append("关单时因子：" + " · ".join(bits))
    env = row.get("_env")
    if env:
        seg.append(f"大盘环境：{env}")
    seg.append(f"教训：{lessons}")
    seg.append("登记依据：" + ("；".join(t.get("reasons") or []) or "（无）"))
    return "\n".join(seg)


def _append_review_md(t: dict, outcome: str, px: float, review: str) -> None:
    """失败复盘落盘 workspace/复盘/妖币追踪复盘_YYYYMMDD.md（静默失败不影响主流程）。"""
    try:
        d = os.path.join(workspace.WORKSPACE, "复盘")
        os.makedirs(d, exist_ok=True)
        fpath = os.path.join(d, time.strftime("妖币追踪复盘_%Y%m%d.md"))
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        found = float(t.get("found_price") or 0)
        chg = (float(px or 0) / found - 1.0) * 100 if found > 0 else 0
        pnl, roi = _sim_pnl(t.get("direction") or "LONG", found, px)
        L = [f"\n## {t.get('symbol')} · {outcome.upper()} · {ts}", "",
             f"- 方向：{'做空' if t.get('direction') == 'SHORT' else '做多'}　阶段：{t.get('stage') or '—'}　"
             f"发现价：{_fmt_price(found)}　结局价：{_fmt_price(px)}（{chg:+.1f}%）",
             f"- 最大涨幅：+{float(t.get('max_gain_pct') or 0):.1f}%　最大跌幅：-{float(t.get('max_drop_pct') or 0):.1f}%",
             f"- 仓位模拟（100U × 10x，爆仓封底）：盈亏 {pnl:+.2f} USDT（ROI {roi:+.1f}%）",
             "", "```", review, "```", ""]
        with open(fpath, "a", encoding="utf-8") as fh:
            fh.write("\n".join(L))
    except Exception:
        pass


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
            direction = t.get("direction") or "LONG"
            outcome = _judge_outcome(ctx, now, direction)
            # v1.5.8：顺向达标（≥25%）不再直接关单——判反转因子：现反转 → 落袋 moon；否则持有移动止盈
            if not outcome and not ctx.get("holding"):
                hit = ctx["max_gain"] >= GAIN_HIT if direction == "LONG" else ctx["max_drop"] >= GAIN_HIT
                if hit:
                    if _reversal_now(sym, direction):
                        outcome = "moon"                       # 动能已反转 → 达标即落袋
                    else:
                        state.radar_track_hold_start(t["id"], px)
                        ctx = {**ctx, "holding": 1, "hold_ext": px}
                        pnl, roi = _sim_pnl(direction, ctx["found"], px)
                        market_ws.publish_event(
                            "alert", kind="radar_outcome", outcome="hold", symbol=sym,
                            stage=t.get("stage") or "", found_price=ctx["found"], price=px,
                            max_gain_pct=round(ctx["max_gain"], 2),
                            max_drop_pct=round(ctx["max_drop"], 2),
                            pnl_usdt=pnl, roi_pct=roi)
            if outcome and state.radar_track_close(t["id"], outcome, px):
                # v1.5.8：失败关单生成复盘（入库 + 落盘 Markdown），任何异常不影响关单
                if outcome == "dump":
                    try:
                        review = _build_failure_review(t, ctx, px, now)
                        state.radar_track_set_review(t["id"], review)
                        _append_review_md(t, outcome, px, review)
                    except Exception:
                        pass
                pnl, roi = _sim_pnl(direction, ctx["found"], px)
                market_ws.publish_event(
                    "alert", kind="radar_outcome", outcome=outcome, symbol=sym,
                    stage=t.get("stage") or "", found_price=ctx["found"], price=px,
                    max_gain_pct=round(ctx["max_gain"], 2),
                    max_drop_pct=round(ctx["max_drop"], 2),
                    pnl_usdt=pnl, roi_pct=roi)
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
