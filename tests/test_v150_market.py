"""v1.5.0 行情大更新单元测试 —— 零网络、零副作用（不触 WS 连接、不落库）。

覆盖：
1. market_ws 爆仓流：_apply_liq 解析入库 / liq_recent / liq_stats / liq_symbol_stats
2. market_ws 单边密集爆发检测（_liq_burst_check）与同方向冷却
3. scanner 语义层六阶段判定（_stage_of）

运行：.venv/Scripts/python.exe tests/test_v150_market.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import market_ws  # noqa: E402
import scanner    # noqa: E402

FAILS = []


def check(name: str, cond: bool, extra: str = "") -> None:
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f"  ({extra})" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


# ---------------- 1) _apply_liq 解析 / 统计 ----------------

def test_apply_liq():
    market_ws._liq_buf.clear()
    market_ws._liq_min.clear()
    now_ms = int(time.time() * 1000)
    frames = [
        {"e": "forceOrder", "E": now_ms, "o": {"s": "BTCUSDT", "S": "SELL", "q": "10", "ap": "50000", "T": now_ms}},
        {"e": "forceOrder", "E": now_ms, "o": {"s": "ETHUSDT", "S": "BUY", "q": "5", "ap": "3000", "T": now_ms}},
        {"e": "forceOrder", "E": now_ms, "o": {"s": "", "S": "SELL", "q": "1", "ap": "1", "T": now_ms}},      # 坏行：无 symbol
        {"e": "forceOrder", "E": now_ms, "o": {"s": "XRPUSDT", "S": "HOLD", "q": "1", "ap": "2", "T": now_ms}},  # 坏行：side 非法
        "not-a-dict",                                                                                        # 坏行：非 dict
    ]
    n = market_ws._apply_liq(frames)
    check("apply_liq: 只入库 2 条合法记录", n == 2, f"n={n}")
    recs = market_ws.liq_recent(10)              # 新→旧：[0]=ETH（空爆），[-1]=BTC（多爆）
    check("apply_liq: side=SELL → kind=long（多爆）", recs[-1]["kind"] == "long" and recs[-1]["symbol"] == "BTCUSDT")
    check("apply_liq: quote = 价×量", abs(recs[-1]["quote"] - 500000.0) < 1.0, f"quote={recs[-1]['quote']}")
    st = market_ws.liq_stats(300)
    check("liq_stats: 多爆金额 50 万", abs(st["long_quote"] - 500000.0) < 1.0)
    check("liq_stats: 空爆金额 1.5 万", abs(st["short_quote"] - 15000.0) < 1.0)
    check("liq_stats: 笔数 1+1", st["long_count"] == 1 and st["short_count"] == 1)
    ss = market_ws.liq_symbol_stats("ETHUSDT", 300)
    check("liq_symbol_stats: ETH 单币 1 笔 / 空头主导", ss["count"] == 1 and ss["side"] == "short")
    check("liq_symbol_stats: BTC 多头主导", market_ws.liq_symbol_stats("BTCUSDT")["side"] == "long")
    check("liq_symbol_stats: 无记录币返回空", market_ws.liq_symbol_stats("FAKEUSDT")["count"] == 0)


# ---------------- 2) 爆发检测 + 冷却 ----------------

def test_liq_burst():
    events = []
    orig = market_ws.publish_event
    market_ws.publish_event = lambda type_, **fields: events.append({"type": type_, **fields})
    try:
        market_ws._liq_min.clear()
        market_ws._liq_burst_ts.clear()
        market_ws._liq_buf.clear()
        now = time.time()
        cur_m = int(now // 60)
        # 前 20 分钟基线：每分钟多头爆 10 万（低于门槛）
        for i in range(1, 21):
            market_ws._liq_min.append({"m": cur_m - i, "lq": 100_000.0, "sq": 0.0, "ln": 1, "sn": 0})
        # 当前分钟：多头爆 200 万、6 笔（≥$1.5M、≥5 笔、≥3× 基线）
        market_ws._liq_min.append({"m": cur_m, "lq": 2_000_000.0, "sq": 0.0, "ln": 6, "sn": 0})
        market_ws._liq_buf.append({"ts": cur_m * 60 + 1, "symbol": "BTCUSDT", "side": "SELL",
                                   "kind": "long", "price": 50000.0, "qty": 20.0, "quote": 1_000_000.0})
        market_ws._liq_burst_check()
        check("burst: 触发一次 liq_burst 事件", len(events) == 1 and events[0].get("kind") == "liq_burst",
              f"events={events}")
        check("burst: 方向=long · 金额=2M · 6 笔",
              events and events[0].get("side") == "long" and events[0].get("quote") == 2_000_000.0
              and events[0].get("count") == 6)
        check("burst: top 聚合含 BTCUSDT",
              events and any(t["symbol"] == "BTCUSDT" for t in events[0].get("top", [])))
        # 冷却：同方向 10 分钟内第二次不再发
        market_ws._liq_min.pop()  # 重建当前分钟桶再触发
        market_ws._liq_min.append({"m": cur_m, "lq": 3_000_000.0, "sq": 0.0, "ln": 8, "sn": 0})
        market_ws._liq_burst_check()
        check("burst: 同方向冷却期内不重复发", len(events) == 1, f"events={len(events)}")
        # 反例：金额不足门槛不触发
        market_ws._liq_min.clear()
        market_ws._liq_burst_ts.clear()
        for i in range(1, 5):
            market_ws._liq_min.append({"m": cur_m - i, "lq": 100_000.0, "sq": 0.0, "ln": 1, "sn": 0})
        market_ws._liq_min.append({"m": cur_m, "lq": 200_000.0, "sq": 0.0, "ln": 2, "sn": 0})
        market_ws._liq_burst_check()
        check("burst: 低于门槛不触发", len(events) == 1, f"events={len(events)}")
    finally:
        market_ws.publish_event = orig
        market_ws._liq_min.clear()
        market_ws._liq_buf.clear()
        market_ws._liq_burst_ts.clear()


# ---------------- 3) 语义层六阶段 ----------------

def test_stage_of():
    base = {"chg24": 0.0, "chg1h": 0.0, "chg3d": 0.0, "chg30d": 0.0, "pos": 0.5,
            "rvol_d": 1.0, "amp24": 6.0}
    d = dict(base, chg1h=-6.0)
    check("stage: 1h -6% → CRASH 崩跌", scanner._stage_of(d)[0] == "CRASH")
    d = dict(base, chg24=12.0, oi_chg24=-8.0)
    check("stage: 涨 12% + OI -8% → DISTRIBUTION 顶背离", scanner._stage_of(d)[0] == "DISTRIBUTION")
    d = dict(base, chg24=30.0)
    check("stage: 24h +30% → VERTICAL 垂直拉升", scanner._stage_of(d)[0] == "VERTICAL")
    d = dict(base, breakout20=True, rvol_d=2.5, chg24=2.0)
    check("stage: 破位 + 放量 → IGNITION 点火", scanner._stage_of(d)[0] == "IGNITION")
    d = dict(base, pos=0.40, chg30d=5.0, chg3d=0.0, rvol_d=2.0)
    check("stage: 低位放量价平 → ACCUMULATION 吸筹", scanner._stage_of(d)[0] == "ACCUMULATION")
    d = dict(base, rvol_d=0.4, amp24=3.0, chg24=1.0)
    check("stage: 量枯幅缩 → DORMANT 沉寂", scanner._stage_of(d)[0] == "DORMANT")


if __name__ == "__main__":
    print("== v1.5.0 行情单测（隔离运行）==")
    test_apply_liq()
    test_liq_burst()
    test_stage_of()
    if FAILS:
        print(f"\n✗ {len(FAILS)} 项失败：{FAILS}")
        sys.exit(1)
    print("\n✓ 全部通过")
