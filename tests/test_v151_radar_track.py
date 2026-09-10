"""v1.5.2 妖币追踪单元测试 —— 零网络、隔离临时库。

覆盖：
1. record_from_radar：只登记 ignition 组合法行 + 重复调用幂等
2. _tick 结局判定：moon（+25%）/ dump（-20%）/ expired（7 天超时）
3. radar_track_progress：max_gain/max_drop 累计
4. tracks_view / radar_tracks_stats 对账
5. 关单后不再跟踪（终态幂等）

运行：.venv/Scripts/python.exe tests/test_v151_radar_track.py
"""
import os
import sys
import tempfile
import time

# 必须在 import 任何 src 模块之前：workspace.py import 时读该 env 决定库位置
os.environ["BAZZ_WORKSPACE"] = tempfile.mkdtemp(prefix="bazz_test_v151_")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import market_ws      # noqa: E402
import radar_tracker # noqa: E402
import scanner       # noqa: E402
import state         # noqa: E402

FAILS = []


def check(name: str, cond: bool, extra: str = "") -> None:
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f"  ({extra})" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


# ---------------- 1) record_from_radar 登记与幂等 ----------------

def test_record():
    payload = {
        "ignition": [
            {"symbol": "AAAUSDT", "stage": "ACCUMULATION", "price": 1.0, "score": 40, "reasons": ["rvol"]},
            {"symbol": "BBBUSDT", "stage": "IGNITION", "price": 2.0, "score": 33, "reasons": []},
            {"symbol": "", "stage": "IGNITION", "price": 3.0},          # 坏行：无 symbol
            {"symbol": "CCCUSDT", "stage": "IGNITION", "price": 0},     # 坏行：price<=0
        ],
        "takeoff": [
            {"symbol": "DDDUSDT", "stage": "VERTICAL", "price": 4.0},   # 起飞中不登记
        ],
    }
    radar_tracker.record_from_radar(payload)
    pending = state.radar_tracks_list("pending")
    syms = {r["symbol"] for r in pending}
    check("record: 只登记 ignition 组合法行", syms == {"AAAUSDT", "BBBUSDT"}, str(syms))
    check("record: 发现价/分数/理由落库",
          any(r["symbol"] == "AAAUSDT" and r["found_price"] == 1.0 and r["found_score"] == 40
              and r["reasons"] == ["rvol"] and r["stage"] == "ACCUMULATION" for r in pending))
    radar_tracker.record_from_radar(payload)  # 重复调用
    check("record: 重复调用幂等", len(state.radar_tracks_list("pending")) == 2)
    add_again = state.radar_track_add("AAAUSDT", "IGNITION", 9.9)
    check("record: 同币 pending 唯一（返回已有 id）",
          add_again == next(r["id"] for r in state.radar_tracks_list("pending")
                            if r["symbol"] == "AAAUSDT"))


# ---------------- 2) 结局判定 ----------------

_orig_price = market_ws.price
_orig_snap = scanner.get_snapshot
_orig_publish = market_ws.publish_event
_events = []


def _patch_env(prices: dict):
    market_ws.price = lambda scope, symbol: prices.get(symbol)
    scanner.get_snapshot = lambda *a, **k: []          # 兜底快照置空（零网络）
    market_ws.publish_event = lambda *a, **k: _events.append(k)


def _restore_env():
    market_ws.price = _orig_price
    scanner.get_snapshot = _orig_snap
    market_ws.publish_event = _orig_publish


def test_moon():
    tid = state.radar_track_add("MOONUSDT", "IGNITION", 1.0)
    _patch_env({"MOONUSDT": 1.3})                       # +30% ≥ 25%
    _events.clear()
    try:
        radar_tracker._tick()
    finally:
        _restore_env()
    row = next(r for r in state.radar_tracks_list("closed") if r["symbol"] == "MOONUSDT")
    check("moon: 结局=moon", row["outcome"] == "moon")
    check("moon: 关单价/最大涨幅落库", row["outcome_price"] == 1.3 and row["max_gain_pct"] >= 25.0)
    check("moon: radar_outcome 事件恰好 1 条", len(_events) == 1 and _events[0].get("kind") == "radar_outcome",
          str(_events))
    check("moon: 事件字段齐", _events and _events[0].get("symbol") == "MOONUSDT"
          and _events[0].get("found_price") == 1.0 and _events[0].get("outcome") == "moon")


def test_dump():
    state.radar_track_add("DUMPUSDT", "ACCUMULATION", 1.0)
    _patch_env({"DUMPUSDT": 0.75})                      # -25% ≥ 20%
    try:
        radar_tracker._tick()
    finally:
        _restore_env()
    row = next(r for r in state.radar_tracks_list("closed") if r["symbol"] == "DUMPUSDT")
    check("dump: 结局=dump", row["outcome"] == "dump" and row["max_drop_pct"] >= 20.0)


def test_progress_max_accumulate():
    tid = state.radar_track_add("MAXUSDT", "IGNITION", 1.0)
    state.radar_track_progress(tid, 1.10)               # +10%
    state.radar_track_progress(tid, 0.90)               # -10%
    row = next(r for r in state.radar_tracks_list("pending") if r["symbol"] == "MAXUSDT")
    check("progress: max_gain/max_drop 累计不回落",
          abs(row["max_gain_pct"] - 10.0) < 1e-6 and abs(row["max_drop_pct"] - 10.0) < 1e-6)
    check("progress: peak/trough 记录", row["peak_price"] == 1.10 and row["trough_price"] == 0.90)
    state.radar_track_close(tid, "expired", 0.90)       # 收尾，不污染后续用例


def test_expired():
    tid = state.radar_track_add("OLDUSDT", "IGNITION", 1.0,
                                found_at=time.time() - 8 * 86400)  # 回拨 8 天
    _patch_env({"OLDUSDT": 1.0})                        # 价格持平
    try:
        radar_tracker._tick()
    finally:
        _restore_env()
    row = next(r for r in state.radar_tracks_list("closed") if r["symbol"] == "OLDUSDT")
    check("expired: 超时未触发 → expired", row["outcome"] == "expired")


def test_closed_not_tracked():
    row = next(r for r in state.radar_tracks_list("closed") if r["symbol"] == "MOONUSDT")
    _patch_env({"MOONUSDT": 5.0})                       # 关单后再暴涨也不应再产出事件
    _events.clear()
    try:
        radar_tracker._tick()
    finally:
        _restore_env()
    check("closed: 终态不再跟踪", len(_events) == 0, str(_events))
    check("closed: close 幂等（二次 close 返回 False）",
          state.radar_track_close(row["id"], "moon", 5.0) is False)


def test_view_and_stats():
    v = radar_tracker.tracks_view()
    st = state.radar_tracks_stats()
    check("stats: 对账 total=各态之和",
          st["total"] == st["pending"] + st["moon"] + st["dump"] + st["expired"], str(st))
    check("stats: moon/dump/expired 各 ≥1", st["moon"] >= 1 and st["dump"] >= 1 and st["expired"] >= 1)
    check("view: 结构完整", set(v) == {"pending", "history", "stats", "ts"}
          and len(v["history"]) == 4 and len(v["pending"]) == 2, str(st))
    check("view: history 按 closed_at 降序",
          all(v["history"][i]["closed_at"] >= v["history"][i + 1]["closed_at"]
              for i in range(len(v["history"]) - 1)))


if __name__ == "__main__":
    test_record()
    test_moon()
    test_dump()
    test_progress_max_accumulate()
    test_expired()
    test_closed_not_tracked()
    test_view_and_stats()
    print()
    if FAILS:
        print(f"✗ {len(FAILS)} 项失败：{FAILS}")
        sys.exit(1)
    print("✓ 全部通过")
