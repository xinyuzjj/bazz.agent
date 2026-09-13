"""v1.5.47 回归测试：止损只按 SMC 结构失效位设置，仓位回归用户演示口径。

用户现场（2026-09-13，两轮反馈叠加）
--------------------------------------------------
① v1.5.46 之前：挂 OB 限价接却按现价算止损距离 13.7%、名义写死 1000U → 亏 137%；
② v1.5.46 改成了「按单笔风险 10U 反推名义」→ 止损被风险预算绑架、
   变成「打止损恒亏 ≈10U」。用户明确纠正：**止损不要恒亏 10U，
   一定要按 SMC 的止损来设置**（结构失效位定止损，风险预算不许反推）。

v1.5.47 最终口径：
  · 止损 = SMC 结构失效位（多头 OB 下沿 / OTE 下沿 / 摆动低点下方缓冲；空头镜像），
    锚位说明（anchor）随行输出，风险预算不参与定止损；
  · 止损距离从**入场参考价**算（OB/OTE 区限价取区中位）——v1.5.46 的正确修复保留；
  · 仓位 = 「100U 本金 = 100U 保证金，10x 杠杆 → 名义 1000U」演示口径；
  · 打止损亏损按结构止损**如实报数**（1000U × 距离），超过本金 10% 时附
    「把名义降到 ≈NU」的稳妥建议（仅供参考，止损位不动）。

离线运行：纯函数测试，不联网、不写盘。
运行：python tests/test_v1540_square_sizing.py
"""
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SRC_PATH = ROOT / "src" / "square_rich.py"

CAPITAL = 100          # 举例本金 = 保证金
LEVERAGE = 10          # 举例杠杆
RISK_U = 10            # 本金 10%：只用于「偏重」判断与降名义建议

# 用户贴出来的那行原文（错的）。留着当「判据必须能拒绝它」的标尺。
LEGACY_SIZING_LINE = (
    "· 仓位算法：100U 本金 = 100U 保证金，10x 杠杆 → 做多开单名义 ≈ 1000U；"
    "止损位 0.1149（距离 13.7%），打止损亏 ≈ 137.2U（占本金 137%，偏重；"
    "想稳一点把杠杆降到 1x，名义 ≈ 100U、亏 ≈ 13.7U）"
)

_MOD = None


def _mod():
    global _MOD
    if _MOD is None:
        import square_rich  # noqa: F401
        _MOD = square_rich
    return _MOD


def _num(text: str) -> float:
    return float(text.replace(",", ""))


def _size_src() -> str:
    """取 `_size_line` 的函数源码（AST 定位，避开同名字符串）。"""
    import ast
    tree = ast.parse(SRC_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_size_line":
            return ast.unparse(node)
    raise AssertionError("没找到 _size_line()")


# ---------------- 判据：交给它一行「仓位算法」，不合格就抛 ----------------

def _assert_sizing_consistent(line: str, require_anchor: bool = False):
    """v1.5.47 判据：演示口径 + 结构止损 + 如实报数 + 可选降名义建议。"""
    assert "仓位算法" in line, f"这行不是仓位算法：{line}"
    # ① 演示口径：本金 = 保证金，10x → 名义 1000U（不许被风险预算绑架）
    assert f"{CAPITAL}U 本金 = {CAPITAL}U 保证金，{LEVERAGE}x 杠杆" in line, f"演示口径丢失：{line}"
    m_notional = re.search(r"开单名义 ≈ ([\d,.]+)U；", line)
    assert m_notional, f"缺开单名义：{line}"
    assert _num(m_notional.group(1)) == CAPITAL * LEVERAGE, f"名义 ≠ 本金×杠杆：{line}"
    # ② 结构止损 + 距离：止损价后必须带说明，距离如实（贪婪匹配最后一个「，距入场」，
    #    兼容锚位说明本身含逗号的情况；无锚位时兜底「（距入场 N%）」）
    m = re.search(r"止损 ([\d,.]+)（(.*)，距入场 ([\d.]+)%）", line)
    if m:
        stop, anchor, dist = _num(m.group(1)), m.group(2).strip(), float(m.group(3))
    else:
        m = re.search(r"止损 ([\d,.]+)（距入场 ([\d.]+)%）", line)
        assert m, f"缺「止损（锚位说明，距入场 N%）」结构：{line}"
        stop, anchor, dist = _num(m.group(1)), "", float(m.group(2))
    if require_anchor:
        assert anchor and ("失效" in anchor or "摆动" in anchor), \
            f"止损缺 SMC 结构锚位说明：{anchor!r}"
    # ③ 亏损如实：打止损亏 = 名义 × 距离（结构止损是多少就报多少，不凑数）
    m_loss = re.search(r"打止损亏 ≈ ([\d,.]+)U", line)
    assert m_loss, f"缺打止损亏损：{line}"
    loss = _num(m_loss.group(1))
    assert abs(loss - CAPITAL * LEVERAGE * dist / 100) < 0.8, \
        f"亏损与 名义×距离 对不上：{line}"
    # ④ 偏重时：降名义建议必须让亏损回到本金 10% 左右，且止损位不动
    if "偏重" in line:
        m2 = re.search(r"名义降到 ≈\s*([\d,.]+)U", line)
        assert m2, f"偏重但没给降名义建议：{line}"
        n2 = _num(m2.group(1))
        # 期望值从「亏损 ÷ 名义」反推真实距离（避开行内百分数的显示舍入）
        real_d = loss / (CAPITAL * LEVERAGE)
        expect_n2 = RISK_U / real_d
        assert abs(n2 - expect_n2) < 2, f"建议名义不是 10U÷距离（期望 ≈{expect_n2:.0f}U）：{line}"
        assert abs(n2 * real_d - RISK_U) < 1.2, f"建议名义打止损仍不 ≈10U：{line}"
        assert "止损位不动" in line, f"降名义建议不得动摇结构止损：{line}"
    else:
        assert loss <= RISK_U + 0.1, f"亏损 {loss}U 超本金 10% 却没标「偏重」：{line}"
    # ⑤ 文案卫生：旧口径措辞不许回来
    assert line.count("保证金") == 1, f"「保证金」出现多次：{line}"
    for bad in ("单笔亏 5U", "10x 占保证金", "把杠杆降到", "按单笔风险", "恒亏", "反推", "放弃"):
        assert bad not in line, f"旧口径措辞「{bad}」回归：{line}"


# ---------------- 1. 演示口径：名义恒为 本金×10x，止损只影响报数 ----------------

def test_demo_headline_and_light_loss():
    """止损 0.5%：名义 1000U，打止损亏 5U ≤ 10U → 无「偏重」建议。"""
    line = _mod()._size_line(100.0, 99.5, "long")
    assert "开单名义 ≈ 1000U" in line, line
    assert "打止损亏 ≈ 5.0U" in line, line
    assert "偏重" not in line, line
    _assert_sizing_consistent(line)


def test_structural_stop_reports_loss_honestly():
    """用户定版（v1.5.47c）：空头卖在 OB 下沿（第一触点保证成交）。
    BTC 素材：OB 77,867.5 ~ 78,542，进价 77,867.5，止损 78,934.71（上沿上方 0.5%）
    → 距离 1.4%，10x 亏 ≈13.7U → 如实报数 + 降名义建议，止损位不动。"""
    anchor = "空头 OB 上沿 78,542.00 上方 0.5% 缓冲，结构失效即离场"
    line = _mod()._size_line(77867.5, 78934.71, "short", anchor)
    assert anchor in line, f"结构锚位说明丢失：{line}"
    assert "距入场 1.4%" in line, line
    assert "打止损亏 ≈ 13.7U" in line, line
    assert "偏重" in line and "止损位不动" in line, line
    _assert_sizing_consistent(line, require_anchor=True)


def test_wide_entry_reports_honestly_and_advises():
    """宽止损场景：如实报数 + 降名义建议，止损位不动。"""
    line = _mod()._size_line(0.12175, 0.1149, "long", "近 10 根摆动低点下方 1% 缓冲，摆动结构失效即离场")
    assert "偏重" in line and "止损位不动" in line, line
    _assert_sizing_consistent(line, require_anchor=True)


def test_direction_word_follows_bias():
    assert "做多开单名义" in _mod()._size_line(100.0, 97.0, "long")
    assert "做空开单名义" in _mod()._size_line(100.0, 103.0, "short")


# ---------------- 2. SMC 结构位：止损锚结构、距离从入场价算 ----------------

def _k(closes, lows, highs, n=120):
    return {"closes": (closes * (n // len(closes) + 1))[:n],
            "lows": (lows * (n // len(lows) + 1))[:n],
            "highs": (highs * (n // len(highs) + 1))[:n]}


def test_ob_stop_anchored_to_structure_and_distance_from_entry():
    """OB 限价接（用户定版）：多头买在 OB 上沿（第一触点保证成交），止损锚下沿下方。"""
    sr = _mod()
    stat = {"symbol": "TESTUSDT",
            "k90": _k([0.12, 0.125], [0.10, 0.11], [0.14, 0.135], 90),
            "k4h": _k([0.125, 0.1332], [0.1155, 0.12], [0.1332, 0.14]),
            "c24": [0.13, 0.1332], "change_pct": 1.0, "market": "spot",
            "funding_rate": 0.00004}
    smc = {"ob": {"low": 0.1155, "high": 0.128}, "sh_v": [0.162]}
    lv = sr._plan_levels(stat, "long", smc)
    assert lv, "plan_levels 不应返回空"
    assert abs(lv["stop"] - 0.1155 * 0.995) < 1e-9, f"止损没锚在 OB 下沿下方：{lv['stop']}"
    assert lv["anchor"] and "OB 下沿" in lv["anchor"], f"缺结构锚位说明：{lv.get('anchor')}"
    assert abs(lv["entry_px"] - 0.128) < 1e-9, \
        f"多头入场参考价应是 OB 上沿（第一触点保证成交），实际 {lv.get('entry_px')}"
    assert "进价 0.128——" in lv["entry"], f"入场文案必须标明进入价格：{lv['entry']}"
    dist_entry = abs(lv["entry_px"] - lv["stop"]) / lv["entry_px"] * 100
    dist_price = abs(lv["price"] - lv["stop"]) / lv["price"] * 100
    assert dist_entry < dist_price, f"入场价距离({dist_entry:.1f}%)应小于现价距离({dist_price:.1f}%)"
    plan = sr._plan(stat, "long", smc)
    sizing = [l for l in plan if l.startswith("· 仓位算法：")][0]
    _assert_sizing_consistent(sizing, require_anchor=True)
    # 距离必须按入场参考价算：行内数字 = |entry_px - stop| / entry_px
    inline_dist = float(re.search(r"距入场 ([\d.]+)%", sizing).group(1))
    assert abs(inline_dist - dist_entry) < 0.15, f"距离不是从入场价算的：{sizing}"
    # 止盈行带盈亏比（从入场价算）
    tp = [l for l in plan if l.startswith("· 止盈：")][0]
    assert re.search(r"盈亏比 ≈ [\d.]+", tp), f"止盈行缺盈亏比：{tp}"


def test_short_ob_entry_uses_lower_edge():
    """空头 OB：卖在 OB 下沿（第一触点保证成交），止损锚上沿上方，入场文案标明进价。"""
    sr = _mod()
    stat = {"symbol": "TESTUSDT",
            "k90": _k([0.12, 0.125], [0.10, 0.11], [0.14, 0.135], 90),
            "k4h": _k([0.13, 0.125], [0.125, 0.12], [0.128, 0.1332]),
            "c24": [0.127, 0.125], "change_pct": -1.0, "market": "spot",
            "funding_rate": 0.00004}
    smc = {"ob": {"low": 0.128, "high": 0.1332}, "sl_v": [0.10]}
    lv = sr._plan_levels(stat, "short", smc)
    assert abs(lv["entry_px"] - 0.128) < 1e-9, \
        f"空头入场参考价应是 OB 下沿（第一触点保证成交），实际 {lv.get('entry_px')}"
    assert abs(lv["stop"] - 0.1332 * 1.005) < 1e-9, f"止损没锚在 OB 上沿上方：{lv['stop']}"
    assert "OB 上沿" in (lv["anchor"] or ""), f"缺结构锚位说明：{lv.get('anchor')}"
    assert "进价 0.128——" in lv["entry"], f"入场文案必须标明进入价格：{lv['entry']}"


# ---------------- 3. 判据必须能拒绝旧文案 ----------------

def test_guard_rejects_the_legacy_line():
    """把用户贴出来的旧原文喂给判据，必须判为不合格 —— 否则这组断言就是摆设。"""
    try:
        _assert_sizing_consistent(LEGACY_SIZING_LINE)
    except AssertionError:
        return
    raise AssertionError("判据居然接受了旧文案，说明它抓不住这个 bug")


def test_legacy_line_contains_the_wrong_numbers():
    assert "137.2U" in LEGACY_SIZING_LINE and "占本金 137%" in LEGACY_SIZING_LINE
    assert "把杠杆降到 1x" in LEGACY_SIZING_LINE


# ---------------- 4. 源码护栏 ----------------

def test_source_keeps_demo_notional_and_structural_stop():
    src = _size_src()
    assert "notional = _CAPITAL_U * _LEVERAGE" in src, "名义不再按 本金×杠杆 演示口径"
    assert "notional = _RISK_TARGET_U / d" not in src, "名义仍被风险预算反推（恒亏 10U 口径回归）"
    assert "anchor" in src, "止损没有结构锚位说明参数"
    assert "这笔放弃" not in src, "v1.5.46 的「放弃」分支还在"
    # _plan 必须把 anchor 传给 _size_line
    src_all = SRC_PATH.read_text(encoding="utf-8")
    plan_src = src_all[src_all.index("def _plan("):src_all.index("def ", src_all.index("def _plan(") + 10)]
    assert 'lv.get("anchor", "")' in plan_src, "_plan 没把结构锚位说明传给 _size_line"
    lv_src = src_all[src_all.index("def _plan_levels"):src_all.index("def _plan(")]
    assert 'lv["entry_px"] = ob_lo' in lv_src and 'lv["entry_px"] = ob_hi' in lv_src, \
        "OB 入场参考价没有取贴止损那条沿（多=下沿 / 空=上沿）"


def test_plan_levels_anchor_covers_all_branches():
    """每个入场分支都必须给出 SMC 结构锚位说明（止损不能没有依据）。"""
    src_all = SRC_PATH.read_text(encoding="utf-8")
    lv_src = src_all[src_all.index("def _plan_levels"):src_all.index("def _plan(")]
    anchors = lv_src.count('lv["anchor"] = ')
    assert anchors == 5, f"入场分支锚位说明应 5 处（OB多/OTE多/摆动多/OB空/摆动空），实际 {anchors}"


# ---------------- 5. 端到端 ----------------

def test_article_end_to_end_sizing_line_is_sane():
    sr = _mod()
    stat = {"symbol": "ETHUSDT",
            "k90": _k([2400.0, 2450.0, 2533.98], [1510.87, 1662.0, 2450.0],
                      [2666.0, 2600.0, 2533.98], 90),
            "k4h": _k([2500.0, 2533.98], [2446.83, 2480.0], [2533.98, 2666.0]),
            "c24": [2460.0, 2533.98], "change_pct": 3.01, "market": "spot",
            "funding_rate": 0.000045}
    bias, _, _ = sr._bias(stat)
    assert bias == "long", f"合成行情应判为多头，实际 {bias}（测试素材需要更新）"
    _, body, _ = sr._article(stat)
    sizing = [l for l in body.splitlines() if l.startswith("· 仓位算法：")]
    assert len(sizing) == 1, f"仓位算法应恰好一行，实际 {len(sizing)} 行"
    _assert_sizing_consistent(sizing[0], require_anchor=True)
    # 仓位算法里的止损价必须和反向剧本一致（v1.5.39 的约束不能回退）
    inv = [l for l in body.splitlines() if l.startswith("· 反向剧本：")][0]
    stop_txt = re.search(r"止损 ([\d,]+\.\d+)", sizing[0]).group(1)
    assert stop_txt in inv, f"反向剧本里的止损与仓位算法不一致：{inv}"


def main():
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
            passed += 1
        except Exception:
            print(f"  FAIL {name}")
            traceback.print_exc()
            failed.append(name)
    print(f"\n{passed}/{len(tests)} passed")
    if failed:
        print("失败：" + ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
