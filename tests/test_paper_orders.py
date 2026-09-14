"""v1.5.61 回归测试：广场发文模拟挂单。

链路：square_rich.compose 把 SMC 计划写进 meta.json 的 plan 节 →
square_store.record_from_run（rich 发布成功）读 plan 建纸单（state.paper_add）→
paper_tracker._tick / _eval 按 5m K 线驱动状态机 →
GET /api/square/paper 给广场页「模拟挂单」标签。

离线、隔离：_eval 纯函数直测，不联网、不写 state.db；源码护栏检查钩子与路由。
运行：python tests/test_paper_orders.py  或  pytest tests/test_paper_orders.py
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import paper_tracker  # noqa: E402  （顶层只依赖 state 的导入，不触库）


# ---------------- _eval 状态机（纯函数） ----------------

def _order(**kw):
    base = {"status": "pending", "direction": "long", "entry": 100.0,
            "zone_lo": 98.0, "zone_hi": 100.0, "stop": 95.0, "tp": 110.0,
            "leverage": 10, "created_at": None, "filled_ts": None}
    base.update(kw)
    if base["created_at"] is None:
        base["created_at"] = NOW - 60      # 默认建单于 NOW 前 60s（未到期）
    return base


NOW = 1_800_000_000.0


def test_pending_touches_zone_then_open():
    """多头：K 线低点探到区上沿 → 第一触点成交，转 open。"""
    u = paper_tracker._eval(_order(), high=101, low=99.5, close=100.5, now=NOW)
    assert u["status"] == "open" and u["filled_ts"] == NOW, u
    assert u["last_price"] == 100.5


def test_pending_short_mirror():
    """空头：K 线高点顶到区下沿 → 触发。"""
    o = _order(direction="short", entry=100.0, zone_lo=100.0, zone_hi=102.0)
    u = paper_tracker._eval(o, high=100.5, low=99.0, close=100.2, now=NOW)
    assert u["status"] == "open", u


def test_pending_no_touch_stays():
    """未触及入场区 → 只刷新现价，状态不变。"""
    u = paper_tracker._eval(_order(), high=100.5, low=100.2, close=100.4, now=NOW)
    assert "status" not in u and u["last_price"] == 100.4, u


def test_pending_7d_expires_noentry():
    """7 天没触发 → noentry（文章观点未兑现），pnl=0。"""
    o = _order(created_at=NOW - 8 * 86400)
    u = paper_tracker._eval(o, high=100.5, low=100.2, close=100.4, now=NOW)
    assert u["status"] == "noentry" and u["pnl_pct"] == 0.0 and u["closed_ts"] == NOW, u


def test_open_long_tp_win():
    """持仓多头打到止盈 → win，pnl 按止盈价×10x 杠杆。"""
    o = _order(status="open", filled_ts=NOW - 3600)
    u = paper_tracker._eval(o, high=110.5, low=99.8, close=109.0, now=NOW)
    assert u["status"] == "win" and u["close_price"] == 110.0, u
    assert abs(u["pnl_pct"] - 100.0) < 1e-9, u      # (110/100-1)*10 = +100%


def test_open_long_sl_loss():
    """持仓多头打到止损 → loss，pnl 为负。"""
    o = _order(status="open", filled_ts=NOW - 3600)
    u = paper_tracker._eval(o, high=101, low=94.5, close=95.5, now=NOW)
    assert u["status"] == "loss" and u["close_price"] == 95.0, u
    assert abs(u["pnl_pct"] - (-50.0)) < 1e-9, u    # (95/100-1)*10 = -50%


def test_open_tp_sl_same_bar_conservative_loss():
    """同一根 K 同时触及止盈与止损 → 保守判止损（按坏情况记账）。"""
    o = _order(status="open", filled_ts=NOW - 3600)
    u = paper_tracker._eval(o, high=111, low=94, close=105, now=NOW)
    assert u["status"] == "loss", u


def test_open_short_mirror_win_loss():
    """空头镜像：打止盈 win（(entry/close-1)*lev），打止损 loss。"""
    o = _order(status="open", direction="short", entry=100.0, zone_lo=100.0,
               zone_hi=102.0, stop=103.0, tp=90.0, filled_ts=NOW - 3600)
    u = paper_tracker._eval(o, high=101, low=89.5, close=90.5, now=NOW)
    assert u["status"] == "win" and u["close_price"] == 90.0, u
    assert abs(u["pnl_pct"] - (100 / 90 - 1) * 1000) < 0.01, u   # ≈+111.11%（杠杆折算，保留2位）
    u2 = paper_tracker._eval(o, high=103.5, low=100.5, close=103.0, now=NOW)
    assert u2["status"] == "loss" and abs(u2["pnl_pct"] - (100 / 103 - 1) * 1000) < 0.01, u2  # ≈-29.13%


def test_open_7d_settles_at_last_price():
    """持仓满 7 天没打 TP/SL → 按现价强制结算，pnl>0 记 win。"""
    o = _order(status="open", filled_ts=NOW - 8 * 86400)
    u = paper_tracker._eval(o, high=104, low=103, close=103.5, now=NOW)
    assert u["status"] == "win" and u["close_price"] == 103.5, u
    assert abs(u["pnl_pct"] - 35.0) < 1e-9, u       # (103.5/100-1)*10
    # 浮亏到期 → loss
    u2 = paper_tracker._eval(o, high=99, low=98, close=98.5, now=NOW)
    assert u2["status"] == "loss", u2


def test_terminal_states_frozen():
    """终态（win/loss/noentry）不再变化。"""
    for st in ("win", "loss", "noentry"):
        assert paper_tracker._eval(_order(status=st), high=111, low=94, close=105, now=NOW) == {}


def test_eval_bad_data_no_crash():
    assert paper_tracker._eval(_order(entry=0), high=1, low=1, close=1, now=NOW) == {}
    assert paper_tracker._eval(_order(), high=0, low=0, close=0, now=NOW) == {}


# ---------------- 建单钩子 + 路由 + 前端（源码护栏，离线不触库） ----------------

def test_compose_writes_plan_to_meta():
    """compose 必须把 SMC 计划写进 meta.json 的 plan 节；观望（neutral/noplay）→ plan=null。"""
    src = (ROOT / "src" / "square_rich.py").read_text(encoding="utf-8-sig")
    blk = src[src.index('with open(meta_f, "w"'):]
    blk = blk[:blk.index("return {\"ok\": True")]
    assert '"plan": plan' in blk, "compose meta.json 缺 plan 节"
    pre = src[src.index("    plan = None"):src.index('with open(meta_f, "w"')]
    assert "noplay" in pre and '"long", "short"' in pre, "plan 节未过滤观望/方向"
    assert '"zone_lo": lv.get("zone_lo")' in src, "plan 未带入场区（建单判断现价是否已入场）"


def test_plan_levels_sets_zone():
    """_plan_levels 各入场分支必须产出 zone_lo/zone_hi（模拟挂单建单依据）。"""
    src = (ROOT / "src" / "square_rich.py").read_text(encoding="utf-8-sig")
    start = src.index("def _plan_levels")
    body = src[start:src.index("\ndef ", start + 10)]      # 到 _plan_levels 的下一个函数为止
    assert body.count('lv["zone_lo"], lv["zone_hi"] = ') == 6, \
        "6 个入场分支（多/空 × OB/OTE/r10）都应设置入场区"


def test_publish_hook_creates_paper_order():
    """发布成功入账处（record_from_run）必须挂钩建单，且只对 rich + 发布成功生效。"""
    src = (ROOT / "src" / "square_store.py").read_text(encoding="utf-8-sig")
    assert "_paper_order_from_run" in src, "record_from_run 缺建单钩子"
    hook = src[src.index("if not failed and skill_name == RICH_SKILL"):]
    assert "not failed" in src, "发布失败不应建单"
    assert 'run_dir=run_dir' in src, "建单必须带 run_dir（幂等去重）"


def test_paper_routes_and_start():
    """desktop_app：/api/square/paper 路由 + 启动钩子存在。"""
    src = (ROOT / "desktop_app.py").read_text(encoding="utf-8-sig")
    assert "@app.get(\"/api/square/paper\")" in src, "缺模拟挂单查询路由"
    assert "@app.post(\"/api/square/paper/delete\")" in src, "缺模拟挂单删除路由"
    assert "paper_tracker.ensure_started()" in src, "启动缺 paper_tracker 后台线程"
    assert "paper_stats" in src, "路由未返回战绩统计"


def test_paper_tracker_window_is_7d():
    """观察期 7 天（用户定版），与妖币追踪口径一致。"""
    assert paper_tracker.WINDOW_DAYS == 7
    assert paper_tracker.POLL_SEC == 60


def test_paper_tracker_uses_pure_eval():
    """_tick 必须走纯函数 _eval（可离线单测），状态更新经 state.paper_update。"""
    src = (ROOT / "src" / "paper_tracker.py").read_text(encoding="utf-8-sig")
    tick = src[src.index("def _tick"):src.index("def ensure_started")]
    assert "_eval(o, *bar)" in tick and "paper_update" in tick
    assert 'in ("pending", "open")' in tick, "终态单不应重复复查"


# ---------------- runner ----------------

def main() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    return 0 if passed == len(fns) else 1


if __name__ == "__main__":
    sys.exit(main())
