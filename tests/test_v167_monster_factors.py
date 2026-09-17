"""v1.6.7 测试 —— 妖币引擎的「两轴数据饥饿」修复。零网络、全 monkeypatch。

背景（用户实测反馈）：「控盘度和燃料为什么每一个都是显示的一样？」
排查后确认这是**三个真实缺陷叠加**，不是一个：

  ① **数据饥饿**：雷达 v2 的确认层只覆盖**前 24 个候选**
     （`get_radar_v2` 里 `cands = ...[:24]`，为控 REST 调用量），而它最终输出 40+ 行。
     未被覆盖的行 `top_ratio`/`taker_ratio` 是 None，`oi_chg24`/`oi_pulse15`/`liq_5m`
     被 `cf.get(k, 0.0)` 兜底写成**字面 0.0** —— 消费方分不清「没变化」和「没测」。
     于是燃料轴在这些行上只能看到资金费率，**永远输出「中性」**。
  ② **文案相同**：`_axis_*` 的 `reading` 是每个档位一句**死文案**，
     37 只「中性」的币共用同一个字符串 —— 视觉上当然「每只币都一样」。
     而且卡片只显示 grade + reading，**看不到到底命中了什么**。
  ③ **死规则**：`taker_ratio >= 1.85` 从来没触发过（实测 40 个合约 max 1.79、命中 0）。

本文件钉住的正是这三条，防止回归：
  ① 单币按需补确认层（`_ensure_factors` + `scanner.confirm_factors` / `manip_flags`）
  ② 「没测」与「中性」必须分开说；reading 必须带**实测读数/命中项**
  ③ 死阈值修正（`_TAKER_BUY_DOMINANT` 镜像 + 源码里不得再出现 1.85）

运行：.venv/Scripts/python.exe tests/test_v167_monster_factors.py
"""
import ast
import io
import os
import sys
import tempfile
import tokenize

# 必须在 import 任何 src 模块之前：workspace.py import 时读该 env 决定库位置
os.environ["BAZZ_WORKSPACE"] = tempfile.mkdtemp(prefix="bazz_test_v167_")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

import scanner         # noqa: E402
import square_monster as M   # noqa: E402

FAILS = []


def check(name: str, cond: bool, extra: str = "") -> None:
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f"  ({extra})" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def _src(name: str) -> str:
    with open(os.path.join(SRC, name), encoding="utf-8") as fh:
        return fh.read()


# ============================================================
# ③ 死阈值：1.85 必须彻底消失，改成有数据依据的 1.30
# ============================================================
def test_taker_threshold_mirror():
    check("scanner 定义了 _TAKER_BUY_DOMINANT", hasattr(scanner, "_TAKER_BUY_DOMINANT"))
    check("monster 镜像了 TAKER_BUY_DOMINANT", hasattr(M, "TAKER_BUY_DOMINANT"))
    check("两处取值相等（不许漂移）",
          getattr(scanner, "_TAKER_BUY_DOMINANT", None) == getattr(M, "TAKER_BUY_DOMINANT", None),
          f"{getattr(scanner, '_TAKER_BUY_DOMINANT', None)} vs {getattr(M, 'TAKER_BUY_DOMINANT', None)}")
    # 1.85 是条从未触发的死规则：实测 40 个最高成交额合约 max=1.79、命中 0。
    check("阈值已离开不可达的 1.85", float(M.TAKER_BUY_DOMINANT) < 1.6,
          f"当前 {M.TAKER_BUY_DOMINANT}")
    check("阈值没有低到把日常波动当信号（≥1.2）", float(M.TAKER_BUY_DOMINANT) >= 1.2,
          f"当前 {M.TAKER_BUY_DOMINANT}")


def _docstring_lines(src: str) -> set:
    """模块/类/函数级 docstring 覆盖的行号集合。"""
    lines = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return lines
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                v = body[0].value
                lines.update(range(v.lineno, (v.end_lineno or v.lineno) + 1))
    return lines


def _dead_185_hits(src: str) -> list:
    """找出**仍在用** 1.85 的位置（注释与 docstring 属文档，允许保留历史说明）。

    判据基于 token 而非行文本，避免把文档里的历史说明误判为「还在用」；
    同时比按行切 `#` 更严：字符串里的配置/默认值（如 `"1.85"`）也算命中。
      · NUMBER 字面量 == 1.85          → 命中（真的当阈值在用）
      · 非 docstring 的字符串含 1.85   → 命中（配置串里在用）
      · COMMENT / docstring            → 放过（只是解释「原来为什么是 1.85」）
    """
    doc_lines = _docstring_lines(src)
    hits = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.NUMBER and tok.string.strip() == "1.85":
            hits.append(f"{tok.start[0]}: {tok.line.strip()[:90]}")
        elif (tok.type == tokenize.STRING and "1.85" in tok.string
                and tok.start[0] not in doc_lines):
            hits.append(f"{tok.start[0]}: {tok.line.strip()[:90]}")
    return hits


def test_no_dead_185_left():
    # 护栏自检：先证明这把尺子既抓得住、又不误伤（防止「为了让测试过而放水」）
    check("护栏自检：代码里的 1.85 字面量会被抓到",
          len(_dead_185_hits("def f():\n    return 1.85\n")) == 1)
    check("护栏自检：配置串里的 \"1.85\" 会被抓到",
          len(_dead_185_hits('X = float("1.85")\n')) == 1)
    check("护栏自检：docstring 里的 1.85 被放过（属文档）",
          _dead_185_hits('def f():\n    """原阈值 1.85 是拍出来的"""\n    return 1.0\n') == [])
    check("护栏自检：注释里的 1.85 被放过（属文档）",
          _dead_185_hits("# 1.85 是拍出来的\nX = 1.0\n") == [])

    bad = _dead_185_hits(_src("scanner.py"))
    check("scanner 代码里不再出现在用的 1.85 阈值", not bad, " | ".join(bad))
    bad_m = _dead_185_hits(_src("square_monster.py"))
    check("monster 代码里不再出现在用的 1.85 阈值", not bad_m, " | ".join(bad_m))


# ============================================================
# ① 按需补确认层：入口、判定、缓存、降级
# ============================================================
def test_public_entries_exist():
    check("scanner.confirm_factors 是公开可调用的", callable(getattr(scanner, "confirm_factors", None)))
    check("scanner.manip_flags 是公开可调用的", callable(getattr(scanner, "manip_flags", None)))
    # 公开别名必须真的指向同一个实现（不能各写一份）
    d = {"price": 1.0, "fut_qv": 0.0, "spot_qv": 0.0, "oi_usd": 0.0, "funding": 0.0,
         "chg24": 0.0, "liq_5m": 0.0, "liq_n5m": 0, "no_spot": False}
    check("manip_flags 与 _manip_flags 同源", scanner.manip_flags(d) == scanner._manip_flags(d))


def test_radar_row_carries_manip_inputs():
    """控盘指纹的原始底料必须出行，否则补完 oi_last 也算不出「换手畸高」。"""
    sc = _src("scanner.py")
    for k in ("fut_qv", "spot_qv", "no_spot", "oi_usd"):
        check(f"雷达输出行带 {k}", f'"{k}":' in sc)


def _fresh_row(**kw):
    row = {"symbol": "TESTUSDT", "price": 1.0, "factors": {"funding": 0.0},
           "manip": [], "manip_note": "", "change24_pct": 0.0}
    row.update(kw)
    return row


def test_ensure_factors_radar_hit():
    """雷达已覆盖（taker/大户比非空）→ 不补取，直接标 radar。"""
    M._confirm_cache.clear()
    called = []
    orig = scanner.confirm_factors
    scanner.confirm_factors = lambda s, f=None: called.append(s) or {}
    try:
        row = _fresh_row(factors={"funding": 0.0, "taker_ratio": 1.1})
        src = M._ensure_factors(row)
    finally:
        scanner.confirm_factors = orig
    check("雷达已覆盖时返回 'radar'", src == "radar", src)
    check("雷达已覆盖时**不发起**补取", not called, str(called))


def test_ensure_factors_fetches_and_recomputes_manip():
    """雷达未覆盖 → 补取一次，并用补到的 oi_last **重算控盘指纹**。"""
    M._confirm_cache.clear()
    row = _fresh_row(fut_qv=1e9, spot_qv=0.0, no_spot=False)
    row["factors"] = {"funding": -0.002}
    orig = scanner.confirm_factors
    scanner.confirm_factors = lambda s, f=None: {
        "taker_ratio": 1.42, "top_ratio": 1.8, "oi_chg24": 7.5, "oi_pulse15": 6.2,
        "oi_last": 1e6, "liq_5m": 0.0, "liq_side": "", "liq_n5m": 0,
        "funding_peak": -0.002,
    }
    try:
        src = M._ensure_factors(row)
    finally:
        scanner.confirm_factors = orig
    check("未覆盖时返回 'fetched'", src == "fetched", src)
    check("taker/大户比 已写入 factors",
          row["factors"].get("taker_ratio") == 1.42 and row["factors"].get("top_ratio") == 1.8)
    check("顶层 oi_chg24 已补齐（位阶「晚不晚」要用）", row.get("oi_chg24") == 7.5,
          str(row.get("oi_chg24")))
    # oi_usd = oi_last × price = 1e6 × 1.0 = 1e6；fut_qv 1e9 / 1e6 = 1000x > 10x → 换手畸高
    check("oi_usd 已按 oi_last×price 重算", row.get("oi_usd") == 1e6, str(row.get("oi_usd")))
    check("控盘指纹已重算（换手畸高命中）", "换手畸高" in (row.get("manip") or []),
          str(row.get("manip")))
    check("重算留痕 _manip_refreshed", row.get("_manip_refreshed") is True)


def test_ensure_factors_cache_and_degrade():
    M._confirm_cache.clear()
    calls = []
    orig = scanner.confirm_factors
    scanner.confirm_factors = lambda s, f=None: calls.append(s) or {
        "taker_ratio": 1.5, "top_ratio": 1.2, "oi_last": 5e5}
    try:
        r1 = _fresh_row()
        r2 = _fresh_row()
        a = M._ensure_factors(r1)
        b = M._ensure_factors(r2)          # 同一只币、TTL 内 → 走缓存
    finally:
        scanner.confirm_factors = orig
    check("首次补取成功", a == "fetched", a)
    check("TTL 内第二次走缓存（不重复请求）", b == "fetched" and len(calls) == 1, f"calls={calls}")

    # 补取抛异常 → 必须降级为 unavailable，绝不把异常抛给上层
    M._confirm_cache.clear()

    def _boom(s, f=None):
        raise RuntimeError("network down")
    orig = scanner.confirm_factors
    scanner.confirm_factors = _boom
    try:
        row = _fresh_row()
        src = M._ensure_factors(row)
    finally:
        scanner.confirm_factors = orig
    check("补取失败降级为 'unavailable'（不抛异常）", src == "unavailable", src)

    # 补取返回全默认（没真取到）→ 不能当成成功
    M._confirm_cache.clear()
    scanner.confirm_factors = lambda s, f=None: {"taker_ratio": None, "top_ratio": None,
                                                 "oi_last": 0.0, "oi_chg24": 0.0}
    try:
        src2 = M._ensure_factors(_fresh_row())
    finally:
        scanner.confirm_factors = orig
    check("补取返回空壳 → 'unavailable'", src2 == "unavailable", src2)


# ============================================================
# ② 「没测」≠「中性」；reading 必须带证据
# ============================================================
def test_fuel_unmeasured_is_not_neutral():
    row = _fresh_row(factors={"funding": 0.0})
    row["_factors_source"] = "unavailable"
    row["oi_chg24"] = None
    fa = M._axis_fuel(row)
    check("未测时不得报「中性」", fa["grade"] != "中性", fa["grade"])
    check("未测时档位是「未能判定」", fa["grade"] == "未能判定", fa["grade"])
    check("未测时文案写明是「没测」", "没测" in fa["reading"], fa["reading"])


def test_fuel_masked_zero_is_treated_as_missing():
    """`factors.oi_chg24 = 0.0` 但顶层为 None（雷达兜底写死）→ 必须当「没测」，
    否则 OI 类燃料项会静默失效，还会把「没测」显示成「OI 0.0%」。"""
    row = _fresh_row(factors={"funding": 0.0, "oi_chg24": 0.0, "oi_pulse15": 0.0})
    row["oi_chg24"] = None            # 顶层是诚实的 None
    row["_factors_source"] = "radar"
    fa = M._axis_fuel(row)
    labels = [i[0] for i in fa["items"]]
    check("被兜底成 0.0 的 OI 不参与判定", not any("OI" in x for x in labels), str(labels))
    check("masked 0.0 不进实测读数（不能显示成 OI 0.0%）",
          "OI15" not in fa["reading"] and "OI24" not in fa["reading"], fa["reading"])


def test_fuel_reading_carries_evidence():
    # 同样判「中性」，但实测读数不同的两只币，文案必须**不同**
    r1 = _fresh_row(factors={"funding": 0.00005, "taker_ratio": 1.04, "rvol15": 0.13,
                             "oi_chg24": -2.63, "oi_pulse15": -0.13, "top_ratio": 2.71})
    r1["oi_chg24"] = -2.63
    r1["_factors_source"] = "radar"
    r2 = _fresh_row(factors={"funding": -0.00089756, "taker_ratio": 1.08, "rvol15": 0.73,
                             "oi_chg24": 14.86, "oi_pulse15": -0.8, "top_ratio": 1.66})
    r2["oi_chg24"] = 14.86
    r2["_factors_source"] = "radar"
    f1, f2 = M._axis_fuel(r1), M._axis_fuel(r2)
    check("两只中性币的档位相同（对照组成立）", f1["grade"] == f2["grade"] == "中性",
          f"{f1['grade']}/{f2['grade']}")
    check("**同档位的两只币文案不同**（这是用户抱怨的解药）", f1["reading"] != f2["reading"])
    check("文案里带实测费率", "费率" in f1["reading"] and "+0.005%" in f1["reading"], f1["reading"])
    check("文案里带实测大户比", "大户比 2.71" in f1["reading"], f1["reading"])
    check("命中时列出命中项与读数", "命中" in M._axis_fuel(_row_with_fuel())["reading"])


def _row_with_fuel():
    row = _fresh_row(factors={"funding": -0.0079, "taker_ratio": 1.03, "rvol15": 4.19,
                              "oi_chg24": -1.23, "oi_pulse15": 7.27, "top_ratio": 1.02})
    row["oi_chg24"] = -1.23
    row["_factors_source"] = "radar"
    return row


def test_control_reading_carries_evidence():
    # 没命中：必须**列出查过哪五项**，而不是一句「没有算出控盘痕迹」
    row = _fresh_row(factors={"funding": 0.0, "top_ratio": 1.0}, oi_usd=1e6)
    row["_factors_source"] = "radar"
    ca = M._axis_control(row)
    check("未见控盘指纹档位不变", ca["grade"] == "未见控盘指纹", ca["grade"])
    check("文案列出「未命中」查过的指纹", "未命中" in ca["reading"], ca["reading"])
    for kw in ("无现货", "合约独大", "换手畸高", "空头付钱", "拉升无爆仓"):
        check(f"文案里有 {kw}", kw in ca["reading"])

    # 命中：要写清**为什么**算控盘
    row2 = _fresh_row(factors={"funding": 0.0, "top_ratio": 1.0}, manip=["合约独大"], oi_usd=1e6)
    ca2 = M._axis_control(row2)
    check("命中时档位为明确控盘", ca2["grade"] == "明确控盘", ca2["grade"])
    check("命中时文案含「命中」与指纹名", "命中" in ca2["reading"] and "合约独大" in ca2["reading"])
    check("命中时给出该指纹的人话解释", "现货没深度" in ca2["reading"], ca2["reading"])

    # 未测：不得报「未见控盘指纹」（那是「测了没有」，不是「没测」）
    row3 = _fresh_row(factors={"funding": 0.0, "top_ratio": None})
    row3["_factors_source"] = "unavailable"
    row3["oi_chg24"] = None
    ca3 = M._axis_control(row3)
    check("未测时不得报「未见控盘指纹」", ca3["grade"] != "未见控盘指纹", ca3["grade"])
    check("未测时档位为「未能判定」", ca3["grade"] == "未能判定", ca3["grade"])
    check("未测文案写明「没测」", "没测" in ca3["reading"], ca3["reading"])
    # 缺持仓额必须如实标注（换手畸高算不出来）
    check("缺持仓额时标注换手畸高未判定", "换手畸高" in " ".join(ca3["missing"]),
          str(ca3["missing"]))


def test_spot_only_coin_is_not_reported_as_unmeasured():
    """只有现货、没有合约市场的币：OI/大户比/费率**本就不存在**。

    说成「没测到」会让读者以为数据源坏了 —— 必须区分开。
    """
    # 量能与买盘在现货口径下依然可测 —— 但这里给一个「什么都没命中」的行，
    # 才能验证「无数据时的措辞」这条分支（有命中量能时本就该给「有油」）。
    row = _fresh_row(factors={"rvol15": 1.0}, oi_usd=0, price=146.59)
    row["_factors_source"] = "unavailable"
    row["_no_futures"] = True
    row["oi_chg24"] = None
    ca, fa = M._axis_control(row), M._axis_fuel(row)
    check("现货币控盘档位为未能判定", ca["grade"] == "未能判定", ca["grade"])
    check("现货币控盘文案说明「本就不存在」", "本就不存在" in ca["reading"], ca["reading"])
    # 不能复用「这是「没测」」那套措辞 —— 现货盘不是「没测到」，是「没有这个概念」
    check("现货币控盘不走「没测」措辞", "这是「没测」" not in ca["reading"], ca["reading"])
    check("现货币控盘 evidence 指明合约口径不适用",
          "无合约市场" in ca["evidence"], ca["evidence"])
    check("现货币 missing 说明合约口径不适用",
          any("不适用" in m for m in ca["missing"]), str(ca["missing"]))
    check("现货币燃料档位为未能判定", fa["grade"] == "未能判定", fa["grade"])
    check("现货币燃料文案说明「本就不存在」", "本就不存在" in fa["reading"], fa["reading"])
    check("现货币 fuel evidence 指明合约口径不适用",
          "无合约市场" in fa["evidence"], fa["evidence"])
    # 但量能这类**现货也存在**的读数必须照常给出，不能被「不适用」一笔抹掉
    row_rv = _fresh_row(factors={"rvol15": 10.1}, oi_usd=0, price=146.59)
    row_rv["_factors_source"] = "unavailable"
    row_rv["_no_futures"] = True
    fa_rv = M._axis_fuel(row_rv)
    check("现货币仍有量能燃料时照常给「有油」", fa_rv["grade"] == "有油", fa_rv["grade"])
    check("现货币量能读数照常保留", "RVOL15 10.1x" in fa_rv["reading"], fa_rv["reading"])

    # 对照：合约币补取失败 → 才说「没测」
    row2 = _fresh_row(factors={"funding": 0.0})
    row2["_factors_source"] = "unavailable"
    row2["_no_futures"] = False
    row2["oi_chg24"] = None
    ca2 = M._axis_control(row2)
    check("合约币补取失败才说「没测」", "这是「没测」" in ca2["reading"], ca2["reading"])
    check("合约币补取失败提示可能只是没测到", "没控盘" in ca2["reading"], ca2["reading"])


# ============================================================
# 接线：analyze 必须真的调用补取，并把来源与缺口如实暴露
# ============================================================
def test_analyze_wires_ensure_factors():
    src = _src("square_monster.py")
    check("analyze 调用了 _ensure_factors", "factors_source = _ensure_factors(row)" in src)
    check("analyze 返回 factors_source", '"factors_source": factors_source' in src)
    check("补取失败会写进 missing（诚实标注）",
          "确认层（OI / 大户比 / taker）补取失败" in src)
    check("产物 meta.json 记录 factors_source", '"factors_source": an.get("factors_source")' in src)
    check("_recompute_manip 在补取路径上被调用", "_recompute_manip(row)" in src)


def test_no_network_imports_added():
    """本修复不得引入新的网络依赖（只用 scanner 现成的取数）。"""
    src = _src("square_monster.py")
    for bad in ("import requests", "import httpx", "import aiohttp", "urlopen"):
        check(f"monster 未引入 {bad}", bad not in src)


# ============================================================
def main():
    print("=" * 60)
    print("v1.6.7 妖币引擎「两轴数据饥饿」修复回归")
    print("=" * 60)
    groups = [
        ("③ 死阈值 1.85 → 1.30", [test_taker_threshold_mirror, test_no_dead_185_left]),
        ("① 按需补确认层", [test_public_entries_exist, test_radar_row_carries_manip_inputs,
                            test_ensure_factors_radar_hit, test_ensure_factors_fetches_and_recomputes_manip,
                            test_ensure_factors_cache_and_degrade]),
        ("② 没测≠中性 + 文案带证据", [test_fuel_unmeasured_is_not_neutral,
                                     test_fuel_masked_zero_is_treated_as_missing,
                                     test_fuel_reading_carries_evidence,
                                     test_control_reading_carries_evidence,
                                     test_spot_only_coin_is_not_reported_as_unmeasured]),
        ("接线与边界", [test_analyze_wires_ensure_factors, test_no_network_imports_added]),
    ]
    for title, fns in groups:
        print(f"\n—— {title} ——")
        for fn in fns:
            fn()
    print("\n" + "=" * 60)
    if FAILS:
        print(f"{len(FAILS)} 项失败：")
        for f in FAILS:
            print("  -", f)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
