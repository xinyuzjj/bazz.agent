"""v1.5.39 回归测试：广场文章「反向剧本」不许给出与这笔单无关的作废线。

用户现场（2026-09-12，文章 `ETHUSDT_20260912_190153`）
--------------------------------------------------------
文中的计划是这样的：

```
· 入场：优先挂 2,507.60 ~ 2,523.74 的多头 OB 区回踩接（限价），现价 2,533.98 …
· 仓位算法：止损位 2,495.06（距离 1.5%）…
· 反向剧本：4h 收盘跌破 1,503.32（90 日低点下方）且收不回来，上面这些多头逻辑全部作废
```

**止损 2,495.06，作废线却是 1,503.32 —— 低了 40%。** 三个后果：

  ① 同一段计划自相矛盾：止损 2,495 早就先打到了，1,503 那句永远不会触发；
  ② 把「这笔单作废」和「大趋势作废」混为一谈，读者照它扛单要亏 40%；
  ③ 紧挨着的「仓位算法」已经给了 2,495.06，两个数字打架，读者不知道信哪个。

根因（`src/square_rich.py`）：`_plan()` 内部算出的 stop 只在局部，反向剧本另起炉灶
直接拿 90 日低点 `lo*0.995` 当多头的作废线。数据本身没错（ETH 90 日低点确实是 1,510.87，
发生在 06-26），错在**拿一个与本笔单无关的价位去描述这笔单的作废条件**。

修法：把「入场/止损/止盈」抽成 `_plan_levels()`，`_plan()` 与 `_invalid_line()` **共用同一个
stop**；再补一个离止损最近的更深结构位；90 日低点只在离现价足够近时才附带提及并明确标注
「跟这笔单的止损不是一回事」。

顺带修掉一个连带 bug：旧代码 `if bias != "short"` 把 neutral（观望）也当成多头，
观望场景下照样输出「上面这些多头逻辑全部作废」，可那段计划里根本没有多头单。

离线运行：纯函数测试，不联网、不写盘。
运行：python tests/test_v1539_square_invalidation.py
"""
import ast
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SRC_PATH = ROOT / "src" / "square_rich.py"

PRICE = 2533.98
OB_LO, OB_HI = 2507.60, 2523.74
OTE_LO, OTE_HI = 2446.83, 2473.88
LOW_90D = 1510.87          # ETH 90 日低点，离现价 -40%
STOP = OB_LO * 0.995       # 2,495.06 —— _plan_levels() 会算出的同一个值


def _k(closes, lows, highs, n=90):
    """把有限的点位列表铺成 n 根，最后一根 = 现价现场。"""
    return {"closes": (closes * (n // len(closes) + 1))[:n],
            "lows": (lows * (n // len(lows) + 1))[:n],
            "highs": (highs * (n // len(highs) + 1))[:n]}


def _stat(low_90d=LOW_90D):
    # 注意：k90 的 lows 里要保证 min() == low_90d，否则 _macro_level 取到的是别的值
    return {"symbol": "ETHUSDT",
            "k90": _k([2400.0, 2450.0, PRICE], [low_90d, low_90d * 1.1, 2450.0], [2666.0, 2600.0, PRICE]),
            "k4h": _k([2500.0, PRICE], [2446.83, 2480.0], [PRICE, 2666.0]),
            "change_pct": 3.01, "c24": [2460.0, PRICE],
            "funding_rate": 0.000045, "oi": 5902000000, "fng": {"value": 63},
            "global_ratio": 1.8, "top_ratio": 1.6, "market": "spot"}


def _smc():
    """复刻现场那份 4h SMC：多头 OB、OTE 窗口、摆动高低点。"""
    return {"ob": {"low": OB_LO, "high": OB_HI},
            "ote_lo": OTE_LO, "ote_hi": OTE_HI, "ote_dir": "up",
            "sh_v": [2666.00], "sl_v": [OTE_LO]}


def _nums(text: str):
    return [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*\.?\d*", text)]


# 现场那行原文（用户贴出来的、错的）。留着当「判据必须能拒绝它」的标尺。
LEGACY_LINE = ("· 反向剧本：4h 收盘跌破 1,503.32（90 日低点下方）且收不回来，"
               "上面这些多头逻辑全部作废，砍仓别犹豫。")


def _assert_invalidation_close_to_stop(line: str, stop: float, price: float):
    """反向剧本的数值判据：作废线必须贴近本单止损，不能飘到几十个百分点外。

    这两条就是这次 bug 的核心判据。单独抽出来，是为了能拿**现场那行错文**去反向验证它
    （见 test_guard_rejects_the_legacy_line）—— 不验证判据本身，断言就是摆设。
    """
    levels = [v for v in _nums(line) if v > 100]      # 跳过 "4h" 里的 4 这类噪声
    assert levels, f"反向剧本里没有任何价位：{line}"
    deepest = min(levels)
    assert deepest <= stop * 1.001, (
        f"作废线 {deepest} 比本单止损 {stop:.2f} 还高 —— 它先于止损触发，说明写错了方向：{line}")
    assert deepest >= price * 0.85, (
        f"作废线 {deepest} 离现价 {price:.2f} 太远（跌幅 {(1-deepest/price)*100:.0f}%）—— "
        f"止损早就打到了，这句永远不会触发：{line}")


def _mod():
    import square_rich
    return square_rich


# ---------------- 0. 判据本身必须能拒绝现场那行错文 ----------------

def test_guard_rejects_the_legacy_line():
    """把用户贴的那行原文喂进判据 —— 必须被拒。

    否则下面所有「作废线贴近止损」的断言都是空转：它们能通过，可能只是判据太松。
    这条件的现实依据：那行写的是 1,503.32，而本单止损 2,495.06、现价 2,533.98。
    """
    try:
        _assert_invalidation_close_to_stop(LEGACY_LINE, STOP, PRICE)
    except AssertionError:
        return                      # 正确：判据拒绝了它
    raise AssertionError("判据没有拒绝现场那行错文（1,503.32 vs 止损 2,495.06）—— 断言太松，形同虚设")


def test_legacy_line_contains_the_wrong_number():
    """留档：确认我们讨论的就是用户贴的那一行（数字没记错）。"""
    assert "1,503.32" in LEGACY_LINE
    assert f"{LOW_90D * 0.995:,.2f}" == "1,503.32", "90 日低点 × 0.995 应当正好是 1,503.32"


# ---------------- 1. 作废线必须与这笔单的止损一致 ----------------

def test_long_invalidation_uses_the_plan_stop():
    sr = _mod()
    stat, smc = _stat(), _smc()
    lv = sr._plan_levels(stat, "long", smc)
    assert abs(lv["stop"] - STOP) < 0.01, f"止损算错了：{lv['stop']} 应为 {STOP}"

    line = sr._invalid_line(stat, "long", smc)
    stop_txt = f"{STOP:,.2f}"
    assert stop_txt in line, f"反向剧本没引用本单止损 {stop_txt}：{line}"

    # 仓位算法那行给的止损，必须和反向剧本里的作废线是同一个数
    size_line = [l for l in sr._plan(stat, "long", smc) if "仓位算法" in l][0]
    assert stop_txt in size_line, f"仓位算法里的止损也不是 {stop_txt}：{size_line}"


# ---------------- 2. 离得远的 90 日低点不许当作废线 ----------------

def test_remote_90d_low_not_used_as_invalidation():
    sr = _mod()
    stat, smc = _stat(), _smc()
    line = sr._invalid_line(stat, "long", smc)

    old_bug = LOW_90D * 0.995          # 1,503.32 —— 旧实现写出来的那个数
    assert f"{old_bug:,.2f}" not in line, f"又拿 90 日低点当多头的作废线了：{line}"
    assert f"{LOW_90D:,.2f}" not in line, f"不该在反向剧本里引用 40% 以外的 90 日低点：{line}"

    # 出现的所有价位都必须贴近止损（现价下方 15% 以内）
    _assert_invalidation_close_to_stop(line, STOP, PRICE)


def test_invalidation_is_close_to_stop():
    """作废线和止损的距离要小到「能被触发」，这是这次 bug 的核心判据。"""
    sr = _mod()
    stat, smc = _stat(), _smc()
    _assert_invalidation_close_to_stop(sr._invalid_line(stat, "long", smc), STOP, PRICE)


# ---------------- 3. 90 日低点足够近时，可以提，但要标明它是「大趋势的事」 ----------------

def test_nearby_macro_level_is_labelled_separately():
    sr = _mod()
    smc = _smc()
    near = PRICE * 0.90                # 离现价 -10%，在 _MACRO_MAX_DROP(20%) 以内
    line = sr._invalid_line(_stat(low_90d=near), "long", smc)
    assert f"{near:,.2f}" in line, f"90 日低点离得近时应当提及：{line}"
    assert "不是一回事" in line, f"提及日线级别大位时必须说明它跟止损不是一回事：{line}"
    assert "本单止损位" in line, "反向剧本没有明确「本单止损位」这个口径"


# ---------------- 4. neutral 不许再输出多头剧本 ----------------

def test_neutral_gets_its_own_line():
    sr = _mod()
    stat, smc = _stat(), _smc()
    line = sr._invalid_line(stat, "neutral", smc)
    assert "多头逻辑全部作废" not in line, f"观望场景不该说多头逻辑作废：{line}"
    assert "没仓位" in line, f"观望场景应当说明当前没有仓位：{line}"
    # 同时给出向上/向下两种触发
    assert "偏多" in line and "偏空" in line, f"观望的剧本应给双向条件：{line}"


def test_short_invalidation_uses_the_plan_stop():
    sr = _mod()
    stat, smc = _stat(), _smc()
    lv = sr._plan_levels(stat, "short", smc)
    line = sr._invalid_line(stat, "short", smc)
    assert f"{lv['stop']:,.2f}" in line, f"空头反向剧本没引用本单止损：{line}（stop={lv['stop']}）"


# ---------------- 5. 源码护栏：别再退回「另起炉灶取 90 日低点」 ----------------

def test_source_no_longer_hardcodes_90d_low_in_invalidation():
    src = SRC_PATH.read_text(encoding="utf-8-sig")
    tree = ast.parse(src)

    # _article 必须调用 _invalid_line()
    art = next(n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "_article")
    calls = set()
    for sub in ast.walk(art):
        if isinstance(sub, ast.Call):
            f = sub.func
            calls.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    assert "_invalid_line" in calls, "_article 没有走 _invalid_line()，反向剧本又散在正文里了"
    assert "_plan_levels" in calls or "_plan" in calls, "_article 没有算点位"

    # 旧写法 `lo*0.995` 不许再出现在 _article 里。
    # ⚠️ 一律用 AST 判定 —— `ast.get_source_segment()` 会把**注释**一起带出来，
    #    而这个仓库的注释里正好写着「旧实现 `bias != "short"` …」「`lo*0.995` …」，
    #    纯文本断言必然误伤（v1.5.37 / v1.5.38 / 本版已连踩三次）。
    for sub in ast.walk(art):
        if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Mult):
            if isinstance(sub.left, ast.Name) and sub.left.id == "lo":
                raise AssertionError("_article 里又出现 `lo * …`（90 日低点当多头的作废线）")
        if isinstance(sub, ast.Compare) and isinstance(sub.left, ast.Name) and sub.left.id == "bias":
            if any(isinstance(op, ast.NotEq) for op in sub.ops):
                vals = [c.value for c in sub.comparators if isinstance(c, ast.Constant)]
                if "short" in vals:
                    raise AssertionError(
                        '检测到 `bias != "short"` 判定 —— 它会把 neutral(观望) 归到多头剧本里')

    # _invalid_line 必须复用 _plan_levels 的 stop
    inv = next(n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "_invalid_line")
    inv_calls = set()
    for sub in ast.walk(inv):
        if isinstance(sub, ast.Call):
            f = sub.func
            inv_calls.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    assert "_plan_levels" in inv_calls, "_invalid_line 没有复用 _plan_levels()，止损又会对不上"


# ---------------- 6. 端到端：整篇不许崩，作废线要落在合理区间 ----------------

def test_article_end_to_end_invalidation_is_sane():
    sr = _mod()
    title, body, tags = sr._article(_stat())
    assert title and body and tags, "文章生成返回了空值"
    inv_lines = [l for l in body.splitlines() if l.startswith("· 反向剧本：")]
    assert len(inv_lines) == 1, f"反向剧本应恰好一行，实际 {len(inv_lines)} 行"
    inv = inv_lines[0]
    assert "1,503" not in inv, f"端到端又出现 1,503：{inv}"
    for v in _nums(inv):
        if v > 100:
            assert PRICE * 0.85 <= v <= PRICE * 1.15, f"端到端作废线离现价太远：{inv}"


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
