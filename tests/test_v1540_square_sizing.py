"""v1.5.40 回归测试：广场文章「仓位算法」不许自相矛盾、口径要能被读者复算。

用户现场（2026-09-12，承接 v1.5.39 那篇 ETH 文章）
--------------------------------------------------
用户贴回「仓位算法」那一行并指出「这个是错误的」：

```
· 仓位算法：止损位 2,495.06（距离 1.6%）。100U 本金、单笔亏 5U →
  做多名义 ≈ 310U，10x 占保证金 ≈ 34U（10x 保证金要占 31U，太重，建议降到 9x 左右）
```

三层问题：

  ① **同一句里两个「10x 保证金」**：前脚说「10x 占保证金 ≈ 34U」，后脚说「10x 保证金要占 31U」；
  ② **建议把杠杆降到 9x，方向是反的**：旧口径下保证金 = 名义 ÷ 杠杆，
     降杠杆只会让保证金**变大**（310/9 ≈ 34U > 31U）；
  ③ **口径本身让读者算不通**：名义由「单笔只亏 5U ÷ 止损%」反推，
     与用户心里的「100U 本金 = 100U 保证金、10x → 开单 1000U」完全对不上。

用户明确给出的口径：**100U 本金 = 100U 保证金，10x 杠杆 → 开单名义 1000U**，
即 `名义 = 本金 × 杠杆`。

修法（`_size_line`）：按上面口径直写，名义恒为 本金 × 杠杆；
风险照样点明（打止损亏多少、占本金多少），偏重时给的降杠杆建议是
**按「把单笔亏损压到本金 10% 左右」反推**的杠杆 —— 在这个口径下降杠杆会同步缩小名义，
方向是对的，不会再出现「止损 8% 却只建议降到 5x、照做仍亏 40%」那种没用的提示。

离线运行：纯函数测试，不联网、不写盘。
运行：python tests/test_v1540_square_sizing.py
"""
import ast
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SRC_PATH = ROOT / "src" / "square_rich.py"

CAPITAL = 100          # 举例本金
LEVERAGE = 10          # 举例杠杆
NOTIONAL = CAPITAL * LEVERAGE      # 1000U

# 用户贴出来的那行原文（错的）。留着当「判据必须能拒绝它」的标尺。
LEGACY_SIZING_LINE = (
    "· 仓位算法：止损位 2,495.06（距离 1.6%）。100U 本金、单笔亏 5U → "
    "做多名义 ≈ 310U，10x 占保证金 ≈ 34U（10x 保证金要占 31U，太重，建议降到 9x 左右）"
)

_MOD = None


def _mod():
    global _MOD
    if _MOD is None:
        import square_rich  # noqa: F401
        _MOD = square_rich
    return _MOD


def _nums(text: str):
    return [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*\.?\d*", text)]


def _size_src() -> str:
    """取 `_size_line` 的函数源码（AST 定位，避开同名字符串）。"""
    tree = ast.parse(SRC_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_size_line":
            return ast.unparse(node)
    raise AssertionError("没找到 _size_line()")


# ---------------- 判据：交给它一行「仓位算法」，不合格就抛 ----------------

def _assert_sizing_consistent(line: str):
    """一行仓位算法必须满足的口径，任何一条不满足就抛 AssertionError。

    抽成独立助手的目的：让 test_guard_rejects_the_legacy_line 能把**旧的那行原文**
    喂进来，证明这组判据真的抓得住 bug，而不是「改完就都过」。
    """
    assert "仓位算法" in line, f"这行不是仓位算法：{line}"

    # ① 口径：本金 = 保证金，名义 = 本金 × 杠杆
    m1 = re.search(r"(\d+)U 本金 = (\d+)U 保证金", line)
    assert m1, f"没有「A本金 = B保证金」的结构：{line}"
    assert m1.group(1) == m1.group(2) == str(CAPITAL), f"本金/保证金不是 {CAPITAL}U：{line}"
    assert f"{LEVERAGE}x 杠杆" in line, f"没点明举例杠杆：{line}"
    assert f"名义 ≈ {NOTIONAL}U" in line, f"开单名义不是 本金×杠杆 = {NOTIONAL}U：{line}"

    # ② 不许再出现「按风险反推名义」的旧口径
    assert "单笔亏 5U" not in line, f"仍在用「单笔亏 5U」旧口径：{line}"
    assert "单笔风险" not in line, f"仍在用「单笔风险」旧口径：{line}"
    assert "10x 占保证金" not in line, f"仍在用「10x 占保证金」旧措辞：{line}"

    # ③ 「保证金」只该出现一次 —— 旧文案一句里出现两处（34U / 31U）才自相矛盾
    assert line.count("保证金") == 1, f"「保证金」出现多次，容易自相矛盾：{line}"

    # ④ 给了降杠杆建议时：杠杆必须变小、且按它算的亏损要压到本金 10% 附近
    m2 = re.search(r"把杠杆降到 (\d+)x，名义 ≈ ([\d,]+)U、亏 ≈ ([\d.]+)U", line)
    if m2:
        lv, n2, loss2 = int(m2.group(1)), _nums(m2.group(2) + "U")[0], float(m2.group(3))
        assert 1 <= lv < LEVERAGE, f"建议杠杆没变小（{lv}x）：{line}"
        assert abs(n2 - CAPITAL * lv) < 1, f"建议名义不等于 本金×{lv}：{line}"
        assert loss2 <= CAPITAL * 0.1 + 0.5, f"按建议杠杆仍亏 {loss2}U，没压到本金 10% 内：{line}"


# ---------------- 1. 核心口径 ----------------

def test_notional_is_capital_times_leverage():
    line = _mod()._size_line(2535.94, 2495.06, "long")
    assert f"名义 ≈ {NOTIONAL}U" in line, line
    _assert_sizing_consistent(line)


def test_distance_and_loss_are_consistent():
    price, stop = 100.0, 96.9
    line = _mod()._size_line(price, stop, "long")
    dist = abs(price - stop) / price * 100
    assert f"距离 {dist:.1f}%" in line, line
    loss = NOTIONAL * dist / 100
    assert f"打止损亏 ≈ {loss:.1f}U" in line, line


def test_direction_word_follows_bias():
    assert "做多开单名义" in _mod()._size_line(100.0, 97.0, "long")
    assert "做空开单名义" in _mod()._size_line(100.0, 103.0, "short")


# ---------------- 2. 不再自相矛盾 ----------------

def test_only_one_margin_number_in_a_row():
    """旧文案一句里出现两个「保证金」数字（34U / 31U），必须只剩口径里的那一个。"""
    line = _mod()._size_line(2535.94, 2495.06, "long")
    assert "10x 占保证金" not in line, line
    # 「34U」这种「降杠杆后保证金反而变大」的数字不许再出现
    assert "34U" not in line, line


def test_suggested_leverage_really_reduces_risk():
    """降杠杆在本口径下会同步缩小名义，所以建议杠杆算出的亏损必须更小。"""
    line = _mod()._size_line(100.0, 92.0, "long")          # 止损 8%
    m = re.search(r"打止损亏 ≈ ([\d.]+)U（占本金 \d+%，偏重；想稳一点把杠杆降到 (\d+)x，"
                  r"名义 ≈ ([\d,]+)U、亏 ≈ ([\d.]+)U）", line)
    assert m, f"宽止损没有给出降杠杆建议：{line}"
    base_loss, lv, n2, loss2 = float(m.group(1)), int(m.group(2)), _nums(m.group(3))[0], float(m.group(4))
    assert loss2 < base_loss, f"建议杠杆没有降低亏损：{line}"
    assert loss2 <= CAPITAL * 0.1 + 0.5, f"建议后仍亏 {loss2}U：{line}"
    assert abs(n2 - CAPITAL * lv) < 1, line
    assert lv >= 1, line


def test_tight_stop_has_no_heavy_warning():
    """止损很小（0.5%）时单笔亏损本来就在 10U 内，不该再啰嗦「偏重」。"""
    line = _mod()._size_line(100.0, 99.5, "long")
    assert "偏重" not in line, f"小止损不该出现「偏重」提示：{line}"
    _assert_sizing_consistent(line)


def test_normal_stop_keeps_warning():
    line = _mod()._size_line(2535.94, 2495.06, "long")
    assert "偏重" in line and "想稳一点" in line, f"1.6% 止损应给出降杠杆建议：{line}"
    _assert_sizing_consistent(line)


# ---------------- 3. 判据必须能拒绝旧文案 ----------------

def test_guard_rejects_the_legacy_line():
    """把用户贴出来的旧原文喂给判据，必须判为不合格 —— 否则这组断言就是摆设。"""
    try:
        _assert_sizing_consistent(LEGACY_SIZING_LINE)
    except AssertionError:
        return
    raise AssertionError("判据居然接受了旧文案，说明它抓不住这个 bug")


def test_legacy_line_contains_the_wrong_numbers():
    """确认留作标尺的那行确实带着旧 bug 的特征值。"""
    assert "单笔亏 5U" in LEGACY_SIZING_LINE
    assert "310U" in LEGACY_SIZING_LINE and "34U" in LEGACY_SIZING_LINE
    assert "降到 9x" in LEGACY_SIZING_LINE


# ---------------- 4. 源码护栏 ----------------

def test_source_no_longer_uses_risk_first_notional():
    src = _size_src()
    assert "notional = _CAPITAL_U * _LEVERAGE" in src, "名义没有按 本金×杠杆 计算"
    assert not re.search(r"notional\s*=\s*5\s*/", src), "还在用「5U ÷ 止损%」反推名义"
    assert "_RISK_TARGET_U" in src, "降杠杆建议没有按目标亏损反推"


def test_source_has_no_legacy_margin_tip():
    src = _size_src()
    # 旧的「10x 保证金要占 X U，太重，建议降到 Yx」那段提示整块应已删除
    assert "太重，建议降到" not in src, "旧的「太重，建议降到 Nx」提示还在"


# ---------------- 5. 端到端 ----------------

def _k(closes, lows, highs, n=120):
    """把短列表铺成 n 根（现场是锯齿状行情，直接用中轴-现价两值交替）。"""
    return {"closes": (closes * (n // len(closes) + 1))[:n],
            "lows": (lows * (n // len(lows) + 1))[:n],
            "highs": (highs * (n // len(highs) + 1))[:n]}


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
    stop_txt = re.search(r"止损位 ([\d,]+\.\d+)", sizing[0]).group(1)
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
