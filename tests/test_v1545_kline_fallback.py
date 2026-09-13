"""v1.5.45 回归测试：90 日 K 线不足时的降级出稿链（用户实测出稿失败）。

用户要求：90d K 线数据不足就换成 4h K 线，再不足换 1h K 线，保证出稿。
_collect 降级链 1d×90 → 4h×540（≈90日）→ 1h×720（≈30日）；
k90_label 标注实际周期，封面图/文章文案跟随（不再写死「90日」）。

离线、隔离：桩替换 scanner，不导入应用、不联网、不写 state.db。
运行：python tests/test_v1545_kline_fallback.py  或  pytest tests/test_v1545_kline_fallback.py
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import square_rich  # noqa: E402  （顶层无副作用，PIL 缺失也只是 Image=None）


def _bars(n, price=100.0):
    c = [price * (1 + 0.01 * (i % 7)) for i in range(n)]
    return {"opens": c[:], "highs": [x * 1.01 for x in c], "lows": [x * 0.99 for x in c],
            "closes": c, "vols": [1.0] * n, "times": list(range(n))}


def _install_scanner(data: dict, calls: list):
    """data: interval（或 (market, interval)）-> klines 表；缺失返回空表。"""
    fake = types.ModuleType("scanner")

    def klines_ohlcv(sym, interval="1d", limit=90, market="futures"):
        calls.append((market, interval))
        k = data.get((market, interval)) or data.get(interval) \
            or {"opens": [], "highs": [], "lows": [], "closes": [], "vols": [], "times": []}
        return k

    fake.klines_ohlcv = klines_ohlcv
    fake.klines_closes = lambda sym, interval="1h", limit=24, market="futures": \
        (data.get((market, interval)) or data.get(interval) or {}).get("closes", [])
    fake.fear_greed_index = lambda: {"value": 50, "classification": "Neutral"}
    fake.futures_open_interest = lambda syms, workers=8: []
    sys.modules["scanner"] = fake


def _collect_with(data: dict):
    calls: list = []
    _install_scanner(data, calls)
    d = square_rich._collect("TESTUSDT", "spot")
    return d, calls


def test_90d_ok_no_fallback():
    d, calls = _collect_with({"1d": _bars(90), "4h": _bars(120), "1h": _bars(25)})
    assert d["k90_label"] == "90日", "90d 数据充足时不应降级"
    assert calls[:2] == [("spot", "1d"), ("spot", "4h")], f"取数顺序异常: {calls}"
    assert d["market"] == "spot", "数据可用时不应切换市场"


def test_fallback_to_4h():
    """1d 不足 5 根（如新上市/上游失败）→ 4h×540 兜底，label 标注实际周期。"""
    d, _ = _collect_with({"1d": _bars(3), "4h": _bars(540), "1h": _bars(25)})
    assert len(d["k90"]["closes"]) == 540, "未切换到 4h 数据"
    assert d["k90_label"] == "近90日·4h", f"label 未跟随降级: {d['k90_label']}"


def test_fallback_to_1h():
    """4h 也拿不到 → 1h×720 兜底，保证出稿。"""
    d, calls = _collect_with({"1d": _bars(2), "4h": _bars(0), "1h": _bars(720)})
    assert len(d["k90"]["closes"]) == 720, "未切换到 1h 数据"
    assert d["k90_label"] == "近30日·1h", f"label 未跟随降级: {d['k90_label']}"
    tried = {(m, i) for m, i in calls}
    assert {("spot", "1d"), ("spot", "4h"), ("spot", "1h")} <= tried, \
        f"降级链未按 1d→4h→1h 顺序尝试: {calls}"


# ---------------- v1.5.48：市场自动纠偏（SNDKUSDT 代币化股票 spot 400 → fapi 可用） ----------------

def test_market_autoswitch_spot_to_futures():
    """请求 spot 全空、futures 1d 可用 → 整体切到 futures（SNDKUSDT 场景）。"""
    d, _ = _collect_with({("futures", "1d"): _bars(90), ("futures", "4h"): _bars(120),
                          ("futures", "1h"): _bars(25)})
    assert d["market"] == "futures", "另一市场可用时未切换市场"
    assert len(d["k90"]["closes"]) == 90, "切换后未取到 futures 1d 数据"
    assert "market→futures" in (d.get("fallback") or ""), f"fallback 未记录切换: {d.get('fallback')}"


def test_market_autoswitch_requires_other_usable():
    """另一市场也是平线占位 → 不切换（RAY 场景：futures 平线先回退 spot，spot 也空则保持空，不回切）。"""
    flat = _bars(90)
    for key in ("opens", "highs", "lows", "closes"):
        flat[key] = [100.0] * 90
    d, _ = _collect_with({"1d": flat, ("futures", "1d"): flat, ("futures", "4h"): _bars(120)})
    assert d["market"] == "spot", "futures 平线应先走既有回退到 spot"
    assert not d["k90"]["closes"] or not square_rich._k_usable(d["k90"]), "不应回切到平线 futures"


def test_market_autoswitch_futures_to_spot():
    """请求 futures 全空、spot 可用 → 切到 spot。"""
    _install_scanner({("spot", "1d"): _bars(90), ("spot", "4h"): _bars(120),
                      ("spot", "1h"): _bars(25)}, [])
    d = square_rich._collect("TESTUSDT", "futures")
    assert d["market"] == "spot", "futures 全空时应切到 spot"
    assert len(d["k90"]["closes"]) == 90
    assert "market→spot" in (d.get("fallback") or "")


def test_flat_1d_falls_back():
    """1d 是占位平线（全等价格）同样视为不可用 → 降级。"""
    flat = _bars(90)
    flat["closes"] = [100.0] * 90
    flat["opens"] = flat["highs"] = flat["lows"] = [100.0] * 90
    d, _ = _collect_with({"1d": flat, "4h": _bars(540), "1h": _bars(720)})
    assert d["k90_label"] == "近90日·4h", "平线 1d 未触发降级"


def test_all_empty_stays_empty():
    """三档全空：不伪造数据，k90 保持空（compose 返回带指引的错误而非异常崩溃）。"""
    d, _ = _collect_with({})
    assert not d["k90"]["closes"], "全空时不应有残留数据"
    assert d["k90_label"] == "90日"


def test_k_usable_threshold():
    assert square_rich._k_usable(_bars(5)) is True
    assert square_rich._k_usable(_bars(4)) is False, "≥5 根才可出图"
    flat = _bars(50)
    for key in ("opens", "highs", "lows", "closes"):
        flat[key] = [100.0] * 50
    assert square_rich._k_usable(flat) is False, "占位平线不可用"


def test_prose_and_cover_follow_label():
    """封面与文章文案必须读 k90_label，不得写死「90日」（源码护栏）。"""
    src = (ROOT / "src" / "square_rich.py").read_text(encoding="utf-8-sig")
    cover = src[src.index("def draw_cover"):src.index("def draw_24h")]
    assert "k90_label" in cover, "封面未跟随实际周期标注"
    story = src[src.index("def _human_story"):src.index("def ", src.index("def _human_story") + 10)]
    assert "k90_label" in story, "文章走势段未跟随实际周期"
    assert src.count('f"{lbl}区间位置"') == 1 and 'f"{lbl} 高 {' in src, \
        "封面 chips/标注未参数化"
    assert "90d 日线/费率" not in src, "页脚仍写死「90d 日线」"
    assert "90d K 线为空" not in src, "compose 错误文案仍写死 90d"


def test_draw_cover_rejects_empty_with_clear_error():
    try:
        square_rich.draw_cover({"symbol": "X", "market": "spot",
                                "k90": {"opens": [], "highs": [], "lows": [],
                                        "closes": [], "vols": [], "times": []}}, "t.png")
        raise AssertionError("空数据未拒绝出图")
    except RuntimeError as e:
        assert "1d/4h/1h" in str(e), f"错误文案未说明降级链: {e}"


# ---------------- runner ----------------

def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:
            fails += 1
            import traceback
            print(f"  FAIL  {name}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - fails}/{len(tests)} passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
