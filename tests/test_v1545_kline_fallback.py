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


# ---------------- v1.5.55：日线封面不标 SMC，4h 结构图单独标记 ----------------

def test_daily_cover_defaults_no_smc():
    """用户指定：日线图不需要标记 SMC。draw_cover 默认参数必须无标记。"""
    import inspect
    sig = inspect.signature(square_rich.draw_cover)
    assert sig.parameters["smc_zones"].default is False, "日线封面默认不得标 SMC"
    assert sig.parameters["kkey"].default == "k90", "日线封面默认取 k90"


def test_draw_cover_4h_renders_with_zones():
    """draw_cover_4h：k4h 可用 → 出图成功。"""
    import os, tempfile
    stat = {"symbol": "TESTUSDT", "market": "futures",
            "k90": _bars(90), "k4h": _bars(120), "c24": _bars(30)["closes"],
            "k90_label": "90日"}
    out = tempfile.mkdtemp(prefix="bazz_t_")
    p = square_rich.draw_cover_4h(stat, os.path.join(out, "c4h.png"))
    assert os.path.isfile(p) and os.path.getsize(p) > 10_000, "4h 结构图未生成"


def test_draw_cover_4h_requires_k4h():
    """k4h 不可用 → 明确报错（compose 侧已 try/except 兜底跳过该图）。"""
    import os, tempfile
    stat = {"symbol": "TESTUSDT", "market": "futures", "k90": _bars(90),
            "c24": _bars(30)["closes"], "k90_label": "90日"}
    try:
        square_rich.draw_cover_4h(stat, os.path.join(tempfile.mkdtemp(), "x.png"))
        raise AssertionError("无 k4h 未拒绝出图")
    except RuntimeError:
        pass


def test_4h_chart_htf_trend_first():
    """用户定版（v1.5.57→v1.5.58）：先判断大趋势（日线「趋势延续优先」口径），4h 图
    只画顺大趋势的结构；框从结构 K 起向右延伸，不横跨整屏、也不止框一根 K。
    反向 CHoCH OB 只在 4h 自身趋势已实体翻向（跌破结构最低点／升破最高点）时才画。
    源码护栏。"""
    src = (ROOT / "src" / "square_rich.py").read_text(encoding="utf-8-sig")
    blk = src[src.index("def draw_cover("):src.index("def draw_cover_4h(")]
    assert "大趋势优先" in blk, "SMC 标记缺「大趋势优先」逻辑"
    assert "_htf_trend(stat)" in blk, "大趋势未走 _htf_trend（趋势延续优先口径）"
    assert 'bx1=x1' in blk, "框未从结构 K 向右延伸"
    assert "CHoCH" in blk, "顺趋势时缺小结构 CHoCH 反向 OB/OTE 标记逻辑"
    assert "实体收盘跌破最低点" in blk and "broke4" in blk, \
        "反向 OB 未按实体破结构最低/最高点区分态（用户定版：没实体破最低点不成立）"
    assert "跌破后" in blk and "_dashed_rect" in blk, \
        "未成立的反向 OB 缺失线框「跌破后的空头ob位」（用户指定：作跌破后做空点虚线标出）"


# ---------------- v1.5.57：日线大趋势「趋势延续优先」（用户定版：实体破位才翻转） ----------------

def _daily(bars):
    return {"opens": [b[0] for b in bars], "highs": [b[1] for b in bars],
            "lows": [b[2] for b in bars], "closes": [b[3] for b in bars],
            "vols": [1.0] * len(bars), "times": list(range(len(bars)))}


def _uptrend_bars():
    """上涨→深回调出显著低点（最低价 90）→止跌回升：影线 94.5 捅破但未实体破锚。
    尾巴用单调上升段，避免平段制造假摆动点。"""
    bars = [(100, 102, 99, 101)] * 15
    bars += [(101, 110, 100.5, 108)] * 5              # 拉升（摆动高点 110）
    bars += [(108, 109, 90, 95)]                      # 深回调：摆动低点，最低价 90
    bars += [(95, 96, 94.5, 95.5)]                    # 影线 94.5 < 锚 90？否——收盘 95.5 未破
    bars += [(102 + i, 104 + i, 101 + i, 103 + i) for i in range(10)]
    return bars


def test_htf_trend_bullish_anchor_unbroken():
    """用户口径（v1.5.58）：无实体收盘跌破最近显著低点的最低价 → 大趋势仍是多头，
    影线捅破不算、高点走低只算调整；锚 = 摆动低点最低价（BTC 实例 76,165=wick）。"""
    ht = square_rich._htf_trend({"k90": _daily(_uptrend_bars())})
    assert ht["trend"] == "bullish", ht
    assert abs(ht["anchor"] - 90.0) < 1e-9, f"锚不是摆动低点最低价：{ht}"
    assert "回调" in ht["txt"], ht


def test_htf_trend_flips_on_body_close():
    """实体 K 收盘跌破锚（最低价 90）→ 上涨趋势失效，判空头。"""
    bars = _uptrend_bars()[:-2] + [(103, 104, 89, 89.5), (89.5, 90, 88, 88.5)]
    ht = square_rich._htf_trend({"k90": _daily(bars)})
    assert ht["trend"] == "bearish", ht
    assert "空头" in ht["txt"], ht


def test_htf_trend_recent_high_does_not_flip():
    """用户纠正（BTC 2026-09 实例）：上涨回调出新低点后再反弹出更高高点，最近显著
    事件虽是摆动高点，但只要锚（最近显著低点最低价）未被实体收盘跌破，大趋势仍是
    多头——旧版按「最近显著事件」定方向会误判空头。"""
    bars = [(100, 102, 99, 101)] * 15
    bars += [(101, 110, 100.5, 108)] * 5              # 拉升，高点 110
    bars += [(108, 109, 90, 95)]                      # 深回调：摆动低点，最低价 90
    bars += [(95, 96, 94.5, 95.5)]                    # 止跌
    bars += [(96, 112, 95, 111)]                      # 强反弹：摆动高点（最近显著事件=高点）
    bars += [(110 + i * 0.5, 111.5 + i * 0.5, 100 + i * 0.5, 101 + i * 0.5)
             for i in range(7)]                       # 回调企稳缓升：实体全在 112 下方，锚 90 未破
    ht = square_rich._htf_trend({"k90": _daily(bars)})
    assert ht["trend"] == "bullish", ht
    assert abs(ht["anchor"] - 90.0) < 1e-9, f"锚不是最近显著低点最低价：{ht}"


def test_htf_trend_wick_through_anchor_not_flip():
    """影线捅破锚（最低价 90）但实体收盘在锚上方 → 仍是多头（影线不算）。"""
    bars = _uptrend_bars()[:-1] + [(101, 102, 89, 100)]
    ht = square_rich._htf_trend({"k90": _daily(bars)})
    assert ht["trend"] == "bullish", ht


def test_htf_trend_bearish_mirror():
    """镜像（用户口径 v1.5.58）：下跌→反弹出显著高点（最高价 110）→阴跌，但锚未被
    实体收盘升破 → 大趋势仍是空头；影线捅破不算；锚 = 摆动高点最高价。"""
    bars = [(100, 98, 101, 99)] * 15                  # 平段（摆动低 99 / 高 101）
    bars += [(99, 90, 99.5, 92)] * 5                  # 下跌（摆动低点 90）
    bars += [(92, 110, 91, 105)]                      # 强反弹：摆动高点，最高价 110
    bars += [(105, 105.5, 104, 104.5)]                # 影线 105.5 < 锚 110，实体未破
    bars += [(98 - i, 96 - i, 99 - i, 97 - i) for i in range(10)]   # 阴跌，未实体升破 110
    ht = square_rich._htf_trend({"k90": _daily(bars)})
    assert ht["trend"] == "bearish", ht
    assert abs(ht["anchor"] - 110.0) < 1e-9, f"锚不是摆动高点最高价：{ht}"
    assert "反弹" in ht["txt"], ht


def test_htf_trend_bearish_flips_on_body_close():
    """镜像翻转：实体 K 收盘升破锚（最高价 110）→ 下跌趋势失效，判多头。"""
    bars = [(100, 98, 101, 99)] * 15
    bars += [(99, 90, 99.5, 92)] * 5
    bars += [(92, 110, 91, 105)]
    bars += [(105, 105.5, 104, 104.5)]
    bars += [(98 - i, 96 - i, 99 - i, 97 - i) for i in range(7)]
    bars += [(96, 111, 95, 110.5), (110.5, 112, 110, 111.5)]        # 实体收盘升破 110
    ht = square_rich._htf_trend({"k90": _daily(bars)})
    assert ht["trend"] == "bullish", ht
    assert "多头" in ht["txt"], ht


# ---------------- v1.5.57：OTE 窗口数学（下降腿窗口曾倒置且位置全错） ----------------

def _leg_k(down: bool):
    """构造一条明确的摆动腿：down=True 高点100@20→低点78@50（下降腿），False 镜像上升腿。"""
    n = 60
    h, l, c = [], [], []
    for i in range(n):
        if down:
            v = (90 + 10 * i / 20 if i <= 20 else
                 100 - 22 * (i - 20) / 30 if i <= 50 else
                 78 + 4 * (i - 50) / 9)
        else:
            v = (90 - 12 * i / 20 if i <= 20 else
                 78 + 22 * (i - 20) / 30 if i <= 50 else
                 100 - 4 * (i - 50) / 9)
        h.append(v)
        l.append(v - 1)
        c.append(v)
    if down:
        l[50] = 78.0          # 摆动低点值（腿终点）
    else:
        l[20] = 78.0          # 摆动低点值（腿起点）
    return {"opens": c[:], "highs": h, "lows": l, "closes": c}


def test_ote_down_leg_window_upside_from_low():
    """下降腿 100→78：窗口必须从终点低点向上返 0.62~0.79×22 = 91.64~95.38（用户定版 v1.5.59），
    且 ote_lo < ote_hi（旧版从起点高点往下算 → 窗口倒置位置全错）；中间值 0.702×22=93.44。"""
    smc = square_rich._smc({"k4h": _leg_k(down=True)})
    assert smc.get("ote_dir") == "down", smc.get("ote_dir")
    lo, hi = smc["ote_lo"], smc["ote_hi"]
    assert abs(lo - (78 + 22 * 0.62)) < 0.3, f"ote_lo={lo}"
    assert abs(hi - (78 + 22 * 0.79)) < 0.3, f"ote_hi={hi}"
    assert lo < hi, f"窗口倒置：{lo} > {hi}"
    assert abs(smc.get("ote_entry") - (78 + 22 * 0.702)) < 0.3, f"ote_entry={smc.get('ote_entry')}"


def test_ote_up_leg_window_below_swing_high():
    """上升腿 78→100：窗口 = 终点高点向下回撤 0.62~0.79×腿长（高点为分形取点，非整数，
    容差 0.8）；中间值 0.702 落在窗口中部。"""
    smc = square_rich._smc({"k4h": _leg_k(down=False)})
    assert smc.get("ote_dir") == "up", smc.get("ote_dir")
    lo, hi = smc["ote_lo"], smc["ote_hi"]
    assert abs(lo - (100 - 22 * 0.79)) < 0.8, f"ote_lo={lo}"
    assert abs(hi - (100 - 22 * 0.62)) < 0.8, f"ote_hi={hi}"
    assert lo < hi, f"窗口倒置：{lo} > {hi}"
    assert abs(smc.get("ote_entry") - (100 - 22 * 0.702)) < 0.8, f"ote_entry={smc.get('ote_entry')}"
    assert lo < smc["ote_entry"] < hi, "中间值 0.702 必须落在 OTE 窗口内"


# ---------------- v1.5.60：入场结构位止损距离护栏（LSK/牛来实测翻车） ----------------

def _rally_k():
    """前 110 根横盘 55~60，末 10 根 60→100 暴涨：现价距近10根低点也 40%+（r10 兜底同样超限）。"""
    bars = [(56, 59, 55, 58)] * 110
    seq = [60, 66, 73, 80, 86, 91, 95, 98, 99, 100]
    bars += [(seq[i - 1] if i else 58, seq[i] + 1, seq[i] - 2, seq[i]) for i in range(10)]
    return {"opens": [b[0] for b in bars], "highs": [b[1] for b in bars],
            "lows": [b[2] for b in bars], "closes": [b[3] for b in bars],
            "vols": [1.0] * len(bars), "times": list(range(len(bars)))}


def _crash_k():
    """镜像：前 110 根横盘 140~145，末 10 根 140→100 暴跌（空头 r10 兜底同样超限）。"""
    bars = [(142, 146, 141, 144)] * 110
    seq = [136, 128, 120, 113, 108, 105, 103, 101, 100, 100]
    bars += [(seq[i - 1] if i else 144, seq[i] + 2, seq[i] - 1, seq[i]) for i in range(10)]
    return {"opens": [b[0] for b in bars], "highs": [b[1] for b in bars],
            "lows": [b[2] for b in bars], "closes": [b[3] for b in bars],
            "vols": [1.0] * len(bars), "times": list(range(len(bars)))}


def test_stop_ok_guard():
    assert square_rich._stop_ok(100, 93) is True        # 7% 可承受
    assert square_rich._stop_ok(100, 91) is False       # 9% 超 8% 上限
    assert square_rich._stop_ok(None, 95) is False
    assert square_rich._stop_ok(100, None) is False


def test_long_far_ob_falls_back_to_ote():
    """LSK 翻车场景：OB 距现价 -65%（止损距离 14.7%）→ 拒绝，降级到 OTE 入场。"""
    stat = {"k4h": _rally_k()}
    smc = {"ob": {"low": 30.0, "high": 35.0, "dir": "bull"},
           "ote_dir": "up", "ote_lo": 91.0, "ote_hi": 95.0, "ote_entry": 92.3}
    lv = square_rich._plan_levels(stat, "long", smc)
    assert "OTE" in lv["entry"] and "0.702" in lv["entry"], lv["entry"]
    assert abs(lv["entry_px"] - 92.3) < 1e-9, lv
    assert lv.get("rr") is not None, "OTE 入场应继续算盈亏比"


def test_long_all_structures_too_far_noplay():
    """OB/OTE/近10根低点的止损距离全部超 8% → 观望劝退，不摆仓位算法（10x 活不到止损）。"""
    stat = {"k4h": _rally_k()}
    smc = {"ob": {"low": 30.0, "high": 35.0, "dir": "bull"},
           "ote_dir": "up", "ote_lo": 20.0, "ote_hi": 24.0, "ote_entry": 22.5}
    lv = square_rich._plan_levels(stat, "long", smc)
    assert lv.get("noplay") is True, lv
    assert "观望" in lv["entry"] and "8%" in lv["entry"], lv["entry"]
    assert lv.get("stop") is None and "rr" not in lv, "noplay 不应给出止损/盈亏比"
    plan = square_rich._plan(stat, "long", smc)
    assert len(plan) == 1 and "观望" in plan[0], "noplay 只给劝退行，不带仓位算法"


def test_short_wide_ob_falls_back_to_ote():
    """牛来翻车场景：空头 OB 区宽 58%（止损距离 91%）→ 拒绝，降级到空头 OTE。"""
    stat = {"k4h": _crash_k()}
    smc = {"ob": {"low": 140.0, "high": 190.0, "dir": "bear"},
           "ote_dir": "down", "ote_lo": 104.0, "ote_hi": 108.0, "ote_entry": 106.6}
    lv = square_rich._plan_levels(stat, "short", smc)
    assert "空头 OTE" in lv["entry"] and "0.702" in lv["entry"], lv["entry"]
    assert abs(lv["stop"] - 108 * 1.01) < 1e-9, lv


def test_short_all_structures_too_far_noplay():
    """空头镜像：OB 区宽超大、OTE 窗口高悬、近10根高点远在天上 → 观望。"""
    stat = {"k4h": _crash_k()}
    smc = {"ob": {"low": 140.0, "high": 190.0, "dir": "bear"},
           "ote_dir": "down", "ote_lo": 270.0, "ote_hi": 300.0, "ote_entry": 280.0}
    lv = square_rich._plan_levels(stat, "short", smc)
    assert lv.get("noplay") is True, lv
    assert "观望" in lv["entry"], lv["entry"]
    assert lv.get("stop") is None and "rr" not in lv


def test_view_tail_explains_divergence():
    """牛来翻车场景：4h 结构多头 + 最终偏空（日线权重 3:1 压过）→ 结论必须说破分歧，
    不能上文「多头结构没坏」下一句就「几条对得上，倾向偏空」。"""
    p = square_rich._view_story({"k4h": _rally_k()}, "short", {"structure": "bullish"})
    txt = "".join(p)
    assert "分量更重" in txt, txt[-200:]
    assert "几条对得上" not in txt, txt[-200:]
    p2 = square_rich._view_story({"k4h": _crash_k()}, "long", {"structure": "bearish"})
    txt2 = "".join(p2)
    assert "分量更重" in txt2, txt2[-200:]
    p3 = square_rich._view_story({"k4h": _rally_k()}, "long", {"structure": "bullish"})
    assert "几条对得上" in "".join(p3), "同向时保留原结论"


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
