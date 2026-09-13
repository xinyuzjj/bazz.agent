"""v1.5.46 回归测试：仓位算法按 SMC 止盈止损设计（风险预算反推仓位）。

用户现场（2026-09-13，承接 v1.5.40 口径）
--------------------------------------------------
用户贴回新一版「仓位算法」并指出「止损不对，没有按照 SMC 的止盈止损来设计」：

```
· 入场：优先挂 0.1155 ~ 0.128 的多头 OB 区回踩接（限价），现价 0.1332 直接追的盈亏比一般
· 仓位算法：100U 本金 = 100U 保证金，10x 杠杆 → 做多开单名义 ≈ 1000U；
  止损位 0.1149（距离 13.7%），打止损亏 ≈ 137.2U（占本金 137%，偏重；
  想稳一点把杠杆降到 1x，名义 ≈ 100U、亏 ≈ 13.7U）
```

两层硬伤：

  ① **止损距离用现价算**：计划明明是挂 OB 区限价接（0.1155~0.128），
     却按现价 0.1332 算出 13.7% 距离——真按计划进场距离只有约 5%；
  ② **名义写死 本金×10x**，与止损距离无关 → 亏 137% 再建议「降到 1x」，
     把整单推翻，等于没按 SMC 设计。

SMC 正确顺序：**先定结构位（入场 = OB 区中位、止损 = OB 下沿下方缓冲），
再按单笔风险预算（本金 10% = 10U）反推仓位**：
距离从入场价算；名义 = 10U ÷ 距离；杠杆 = 名义 ÷ 本金，封顶 10x；
止损贴结构（≈1%）时自然贴近「10x / 1000U」演示口径，止损宽时名义自动缩小，
打止损恒亏 ≈10U；距离宽到连 1x 都装不下（>10%）→ 如实建议放弃这笔。
止盈行给出盈亏比（也是从入场价算）。

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
LEVERAGE = 10          # 杠杆上限
RISK_U = 10            # 单笔风险预算 = 本金 10%

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

def _assert_sizing_consistent(line: str):
    """SMC 风险口径判据：读者拿行内数字必须能复算，且亏损被压在风险预算内。"""
    assert "仓位算法" in line, f"这行不是仓位算法：{line}"
    m = re.search(r"入场 ([\d,.]+)、止损 ([\d,.]+)（距离 ([\d.]+)%）", line)
    assert m, f"缺「入场/止损/距离」结构位三元组：{line}"
    entry, stop, dist = _num(m.group(1)), _num(m.group(2)), float(m.group(3))
    # ① 距离必须从入场价算（不是现价）——复算对得上
    assert abs(abs(entry - stop) / entry * 100 - dist) < 0.15, \
        f"距离不是从入场价算的：{line}"

    if "放弃" in line:
        # 止损宽到连 1x 都装不下风险预算 → 不给仓位，且必须说明原因
        assert dist > 10, f"未超 10% 却建议放弃：{line}"
        assert "名义 ≈" not in line, f"放弃的仓位不该再给名义：{line}"
        return

    m2 = re.search(r"名义 ≈ ([\d,.]+)U（保证金 (\d+)U，约 ([\d.]+)x），打止损亏 ≈ ([\d.]+)U", line)
    assert m2, f"缺「名义/保证金/杠杆/亏损」四元组：{line}"
    notional, margin, lev, loss = _num(m2.group(1)), _num(m2.group(2)), float(m2.group(3)), float(m2.group(4))
    # ② 风险预算反推：名义 ≈ 10U ÷ 距离（距离用入场/止损价复算，封顶 1000U）
    dist_recalc = abs(entry - stop) / entry * 100
    expect = min(RISK_U / (dist_recalc / 100), CAPITAL * LEVERAGE)
    assert abs(notional - expect) < 1.5, f"名义不是 风险预算÷距离（期望 ≈{expect:.0f}U）：{line}"
    assert margin == CAPITAL, f"保证金不是本金 {CAPITAL}U：{line}"
    assert lev <= LEVERAGE + 1e-9, f"杠杆超过上限 {LEVERAGE}x：{line}"
    assert abs(lev - notional / CAPITAL) < 0.11, f"杠杆 ≠ 名义÷本金：{line}"
    # ③ 打止损亏损被压在本金 10% 内（封顶时更小）
    assert loss <= RISK_U + 0.6, f"打止损亏 {loss}U，没压在风险预算内：{line}"
    assert abs(loss - notional * dist / 100) < 0.8, f"亏损与 名义×距离 对不上：{line}"
    # ④ 文案卫生：旧口径的措辞不许回来
    assert line.count("保证金") == 1, f"「保证金」出现多次：{line}"
    for bad in ("单笔亏 5U", "10x 占保证金", "偏重", "把杠杆降到", "本金 = 100U 保证金，10x"):
        assert bad not in line, f"旧口径措辞「{bad}」回归：{line}"


# ---------------- 1. 核心口径：止损贴结构 → 10x/1000U；止损宽 → 自动缩仓 ----------------

def test_tight_stop_reaches_demo_leverage():
    """止损 0.5%：10U ÷ 0.5% = 2000U 超上限 → 封顶 10x/1000U（用户演示口径）。"""
    line = _mod()._size_line(100.0, 99.5, "long")
    assert "名义 ≈ 1000U" in line and "约 10.0x" in line, line
    assert "打止损亏 ≈ 5.0U" in line, line
    _assert_sizing_consistent(line)


def test_normal_stop_sizes_from_risk_budget():
    """止损 1.6%（入场 2535.94 / 止损 2495.06）：名义 = 10 ÷ 1.6% ≈ 625U，亏 ≈ 10U。"""
    line = _mod()._size_line(2535.94, 2495.06, "long")
    _assert_sizing_consistent(line)
    assert "打止损亏 ≈ 10.0U" in line, line


def test_wide_stop_shrinks_notional():
    """止损 8%：名义 = 10 ÷ 8% = 125U（1.2x），打止损仍只亏 ≈10U —— 不再出现亏 137%。"""
    line = _mod()._size_line(100.0, 92.0, "long")
    _assert_sizing_consistent(line)
    assert "125" in line, line


def test_too_wide_stop_says_skip():
    """用户实测那个场景（0.1332 现价挂 OB、止损 0.1149）：距离 >10% → 如实建议放弃。"""
    line = _mod()._size_line(0.1332, 0.1149, "long")
    _assert_sizing_consistent(line)
    assert "放弃" in line, line


def test_direction_word_follows_bias():
    assert "做多开单名义" in _mod()._size_line(100.0, 97.0, "long")
    assert "做空开单名义" in _mod()._size_line(100.0, 103.0, "short")


# ---------------- 2. SMC 结构位：距离从入场价算，不从现价算 ----------------

def _k(closes, lows, highs, n=120):
    return {"closes": (closes * (n // len(closes) + 1))[:n],
            "lows": (lows * (n // len(lows) + 1))[:n],
            "highs": (highs * (n // len(highs) + 1))[:n]}


def test_ob_entry_distance_uses_entry_not_price():
    """OB 限价接：入场参考价 = OB 区中位，止损 = OB 下沿下方；距离必须明显小于「现价→止损」。"""
    sr = _mod()
    stat = {"symbol": "TESTUSDT",
            "k90": _k([0.12, 0.125], [0.10, 0.11], [0.14, 0.135], 90),
            "k4h": _k([0.125, 0.1332], [0.1155, 0.12], [0.1332, 0.14]),
            "c24": [0.13, 0.1332], "change_pct": 1.0, "market": "spot",
            "funding_rate": 0.00004}
    smc = {"ob": {"low": 0.1155, "high": 0.128}, "sh_v": [0.162]}
    lv = sr._plan_levels(stat, "long", smc)
    assert lv, "plan_levels 不应返回空"
    assert lv["entry_px"] is not None and 0.1155 <= lv["entry_px"] <= 0.128, \
        f"入场参考价不在 OB 区内：{lv.get('entry_px')}"
    dist_entry = abs(lv["entry_px"] - lv["stop"]) / lv["entry_px"] * 100
    dist_price = abs(lv["price"] - lv["stop"]) / lv["price"] * 100
    assert dist_entry < dist_price, f"入场价距离({dist_entry:.1f}%)应小于现价距离({dist_price:.1f}%)"
    plan = sr._plan(stat, "long", smc)
    sizing = [l for l in plan if l.startswith("· 仓位算法：")][0]
    _assert_sizing_consistent(sizing)
    # 止盈行带盈亏比（从入场价算）
    tp = [l for l in plan if l.startswith("· 止盈：")][0]
    assert re.search(r"盈亏比 ≈ [\d.]+", tp), f"止盈行缺盈亏比：{tp}"


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

def test_source_sizes_from_risk_budget():
    src = _size_src()
    assert "notional = _RISK_TARGET_U / d" in src, "名义没有按 风险预算÷距离 反推"
    assert "_CAPITAL_U * _LEVERAGE" not in src, "名义仍在写死 本金×10x"
    assert "偏重" not in src and "把杠杆降到" not in src, "旧的降杠杆建议还在"


def test_plan_uses_entry_px_for_sizing():
    src = (SRC_PATH.read_text(encoding="utf-8"))
    plan_src = src[src.index("def _plan("):src.index("def ", src.index("def _plan(") + 10)]
    assert 'lv["entry_px"] or lv["price"]' in plan_src, "_plan 没把入场参考价传给 _size_line"
    lv_src = src[src.index("def _plan_levels"):src.index("def _plan(")]
    assert 'lv["entry_px"] = (ob_lo + ob_hi) / 2' in lv_src, "OB 入场没取区中位当入场参考价"


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
    _assert_sizing_consistent(sizing[0])
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
