"""v1.6.6 测试 —— 妖币广场引擎（剧本三轴）+ 两个挂起决策的自动复核。零网络、隔离临时库。

覆盖：
  ① 引擎分离：square_monster 只用「底层原语」复用 square_rich，**不得 import 任何 SMC 分析函数**
  ② 出场四段与 radar_tracker / scanner 的常量**逐一相等**（不许漂移）
  ③ 位阶轴：只有 吸筹/点火 且不晚 → 允许进场；已拉升/垂直/派发/崩跌/做空埋伏 → 全部不许
  ④ 合议优先级：做空埋伏 → 不参与；派发/崩跌 → 离场（无论另两轴多好）
  ⑤ **不给点位也是结论**：非 AMBUSH 时 plan 必须为 None（绝不硬造方向）
  ⑥ 只做多：plan.direction 恒为 long；4 套风格与代币引擎的 4 套**不重名**
  ⑦ 落盘产物与代币引擎同构（square-post 发布链路无需改动）
  ⑧ 符号不在妖币雷达视野内 → 明确报错并提示改用 rich 引擎（不硬编一篇）
  ⑨ 决策自动复核：0 样本 → waiting 且 gap 如实；样本够了 → ready 且给结论
  ⑩ 接线：agent 路由提示词、llm 工具 schema 的 audit 模式、技能可被发现

运行：.venv/Scripts/python.exe tests/test_v166_monster_engine.py
"""
import inspect
import math
import os
import sys
import tempfile

# 必须在 import 任何 src 模块之前：workspace.py import 时读该 env 决定库位置
os.environ["BAZZ_WORKSPACE"] = tempfile.mkdtemp(prefix="bazz_test_v166_")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

import radar_tracker   # noqa: E402
import scanner         # noqa: E402
import square_rich     # noqa: E402   （只为对照，monster 引擎不得 import 它的 SMC 函数）
import state           # noqa: E402

FAILS = []


def check(name: str, cond: bool, extra: str = "") -> None:
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f"  ({extra})" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def _src(name: str) -> str:
    return open(os.path.join(SRC, name), encoding="utf-8").read()


def _mkrow(**kw) -> dict:
    """构造一条雷达行（字段名与 scanner.get_radar_v2 的 row 完全一致）。"""
    base = {
        "symbol": kw.pop("symbol", "TESTUSDT"), "price": kw.pop("price", 1.0),
        "change24_pct": 0.0, "oi_chg24": None, "change1h_pct": 0.5, "change3d_pct": 2.0,
        "change30d_pct": 15.0, "position_pct": 30.0, "stage": "IGNITION",
        "stage_label": "点火", "score": 20, "manip": [], "manip_note": "", "reasons": [],
        "vol_ratio": 2.0, "quote_volume": 5e7,
        "factors": {"funding": 0.0, "funding_peak": None, "oi_chg24": None,
                    "oi_pulse15": None, "top_ratio": 1.5, "global_ratio": None,
                    "taker_ratio": None, "rvol15": None, "liq_5m": 0.0, "liq_side": ""},
    }
    factors = kw.pop("factors", {}) or {}
    base.update(kw)
    base["factors"] = {**base["factors"], **factors}
    return base


# ---------------- ① 引擎分离：不得复用 SMC 分析层 ----------------

def test_engine_separation():
    """monster 引擎必须与 rich 的 SMC 分析层**完全分开**。

    这条是整套设计的立足点：如果 monster 悄悄 import 了 _smc/_bias/_plan_levels，
    那它就只是 SMC 换了层皮，「两套技巧」变成一句空话 —— 而且不会有任何报错提醒你。
    """
    print("\n=== ① 引擎分离（不得复用 SMC 分析层）===")
    import square_monster as sm

    forbidden = ("_smc", "_htf_trend", "_find_ob", "_find_fvg", "_bias",
                 "_plan_levels", "_pick_tp", "_invalid_line", "_macro_level")
    leaked = [n for n in forbidden if hasattr(sm, n)]
    check("monster 未 import 任何 SMC 分析函数", not leaked, f"泄漏了 {leaked}")

    # 复用是**显式白名单**：源码里必须只有一处 from square_rich import
    src = _src("square_monster.py")
    check("from square_rich 只有一处（显式白名单）",
          src.count("from square_rich import") == 1)
    check("白名单块里有禁止项注释（提醒后来者）", "禁止：_smc" in src or "禁止：_smc /" in src)
    # 自己实现了三轴，不依赖 rich 的分析结果
    for fn in ("_axis_stage", "_axis_control", "_axis_fuel", "_verdict", "_plan",
               "_exit_segments"):
        check(f"monster 自带 {fn}()", callable(getattr(sm, fn, None)))

    # 风格名必须与代币引擎不重名（名字不同是为了让 agent 不能顺手互换）
    check("风格集与 rich 不重名",
          not (set(sm.STYLES) & set(square_rich.STYLES)),
          f"{sm.STYLES} vs {square_rich.STYLES}")
    check("rich 仍是 4 套 SMC 风格", set(square_rich.STYLES) == {"review", "diary", "qa", "blunt"})


# ---------------- ② 常量镜像不许漂移 ----------------

def test_constant_mirrors():
    """出场四段与闸门线全部镜像既有模块，一处不等就是 bug。"""
    print("\n=== ② 常量镜像（与 radar_tracker / scanner 逐一相等）===")
    import square_monster as sm

    check(f"STOP_PCT {sm.STOP_PCT} == radar_tracker.FAIL_HIT {radar_tracker.FAIL_HIT}",
          sm.STOP_PCT == radar_tracker.FAIL_HIT)
    check(f"HIT_PCT {sm.HIT_PCT} == radar_tracker.GAIN_HIT {radar_tracker.GAIN_HIT}",
          sm.HIT_PCT == radar_tracker.GAIN_HIT)
    check(f"TRAIL_PCT {sm.TRAIL_PCT} == radar_tracker.TRAIL_PCT {radar_tracker.TRAIL_PCT}",
          sm.TRAIL_PCT == radar_tracker.TRAIL_PCT)
    check(f"POS_USDT {sm.POS_USDT} == radar_tracker.POS_USDT {radar_tracker.POS_USDT}",
          sm.POS_USDT == radar_tracker.POS_USDT)
    check(f"LATE_CHG24 {sm.LATE_CHG24} == scanner._TRIG_LATE_CHG24 {scanner._TRIG_LATE_CHG24}",
          sm.LATE_CHG24 == scanner._TRIG_LATE_CHG24)
    check(f"MAX_OI24 {sm.MAX_OI24} == scanner._TRIG_MAX_OI24 {scanner._TRIG_MAX_OI24}",
          sm.MAX_OI24 == scanner._TRIG_MAX_OI24)
    check(f"MAX_CHG24 {sm.MAX_CHG24} == scanner._TRIG_MAX_CHG24 {scanner._TRIG_MAX_CHG24}",
          sm.MAX_CHG24 == scanner._TRIG_MAX_CHG24)
    check(f"WARM_CHG24 {sm.WARM_CHG24} == scanner._TRIG_WARM_CHG24 {scanner._TRIG_WARM_CHG24}",
          sm.WARM_CHG24 == scanner._TRIG_WARM_CHG24)
    check("出场四段恰好 4 条", len(sm._exit_segments()) == 4)
    txt = " ".join(f"{a} {b}" for a, b, _c in sm._exit_segments())
    for want in ("逆向 10%", "顺向 25%", "自极值回撤 12%", "反转因子命中"):
        check(f"出场段含「{want}」", want in txt, txt)


# ---------------- ③④⑤ 三轴 / 合议 / 计划门禁 ----------------

def test_stage_axis_and_verdict():
    """位阶是最硬的一轴：一票否决。"""
    print("\n=== ③ 位阶轴与合议优先级 ===")
    import square_monster as sm

    def verdict_of(**kw):
        r = _mkrow(**kw)
        sa, ca, fa = sm._axis_stage(r), sm._axis_control(r), sm._axis_fuel(r)
        vk, _line, _c = sm._verdict(sa, ca, fa)
        return sa, vk, sm._plan(sa, ca, fa, r)

    # --- 允许进场：吸筹 / 点火 且不晚 ---
    for stage in ("ACCUMULATION", "IGNITION"):
        sa, vk, pl = verdict_of(stage=stage, change24_pct=0.5)
        check(f"{stage} 不晚 → 可埋伏", vk == "AMBUSH", vk)
        check(f"{stage} 不晚 → 给点位", pl is not None)
        check(f"{stage} 止损 = 入场 ×(1−10%)",
              pl and abs(pl["stop"] - pl["entry"] * 0.9) < 1e-9)

    # --- 暖启动 3~10%：降分不挡（OPT-04 用户拍板口径）---
    sa, vk, pl = verdict_of(stage="IGNITION", change24_pct=6.0)
    check("暖启动 3~10% 仍可埋伏（不硬挡）", vk == "AMBUSH", vk)
    check("暖启动被标 warm", sa["warm"] is True)

    # --- 晚：价格硬挡线 ---
    sa, vk, pl = verdict_of(stage="IGNITION", change24_pct=sm.LATE_CHG24)
    check(f"chg24 = {sm.LATE_CHG24} → 窗口已过", vk == "LATE", vk)
    check("晚 → 不给点位", pl is None)
    check("晚的理由写明价格", any("价格已涨" in w for w in sa["late_why"]))

    # --- 晚：OI 堆积（与价格无关的第二维度）---
    sa, vk, pl = verdict_of(stage="IGNITION", change24_pct=0.5,
                            oi_chg24=sm.MAX_OI24)
    check("OI 堆积 → 窗口已过", vk == "LATE", vk)
    check("晚的理由写明 OI", any("持仓已堆积" in w for w in sa["late_why"]))

    # --- 已拉升 / 垂直 / 派发 / 崩跌 / 做空埋伏：全都不许进 ---
    cases = [("EXTENDED", "LATE"), ("VERTICAL", "RIDE"), ("DISTRIBUTION", "EXIT"),
             ("CRASH", "EXIT"), ("SHORT_AMBUSH", "NO_SHORT")]
    for stage, want in cases:
        sa, vk, pl = verdict_of(stage=stage, change24_pct=20.0)
        check(f"{stage} → {want}", vk == want, vk)
        check(f"{stage} 不给点位", pl is None)
        check(f"{stage} allow=NO", sa["allow"] == "NO")

    # --- 合议优先级：派发时即使控盘满分 + 燃料满油，也必须离场 ---
    r = _mkrow(stage="DISTRIBUTION", change24_pct=12.0, oi_chg24=-6.0,
               manip=["无现货", "合约独大", "换手畸高"],
               factors={"funding": -0.005, "oi_pulse15": 9.0, "liq_side": "short",
                        "liq_5m": 2e6, "taker_ratio": 2.5, "rvol15": 5.0})
    sa, ca, fa = sm._axis_stage(r), sm._axis_control(r), sm._axis_fuel(r)
    vk, _l, _c = sm._verdict(sa, ca, fa)
    check("重度控盘 + 满油 但位阶在派发 → 仍离场（位阶一票否决）", vk == "EXIT", vk)
    check("该行控盘确实被判重度", ca["grade"] == "重度控盘", ca["grade"])

    # --- 做空埋伏优先级最高：无论另外两轴多好都不参与 ---
    r = _mkrow(stage="SHORT_AMBUSH", change24_pct=4.0,
               manip=["无现货"], factors={"funding": -0.004, "taker_ratio": 2.2})
    sa, ca, fa = sm._axis_stage(r), sm._axis_control(r), sm._axis_fuel(r)
    vk, _l, _c = sm._verdict(sa, ca, fa)
    check("做空埋伏 → 不参与（优先级高于控盘/燃料）", vk == "NO_SHORT", vk)


def test_only_long_never_short():
    """只做多，绝不做空 —— 这是安装版真实战绩写死的规则。"""
    print("\n=== ④ 只做多 ===")
    import square_monster as sm

    r = _mkrow(stage="IGNITION", change24_pct=1.0)
    sa, ca, fa = sm._axis_stage(r), sm._axis_control(r), sm._axis_fuel(r)
    pl = sm._plan(sa, ca, fa, r)
    check("plan.direction 恒为 long", pl and pl["direction"] == "long")
    check("TP 目标 = 入场 ×(1+25%)",
          pl and abs(pl["entry"] * 1.25 - r["price"] * 1.25) < 1e-9)
    check("仓位口径 100U × 10x = 1000U 名义",
          pl and pl["notional"] == 1000.0 and "1000U" in pl["size_line"])
    check("如实报打止损亏损（10% → 100U）", pl and abs(pl["loss_u"] - 100.0) < 0.6)
    # 10x + 10% 止损 = 强平线：必须把这个算术写进稿子，不能让人以为「只亏一点」
    check("size_line 不重复前导 ·（嵌进 bullet 列表会变成 · ·）",
          pl and not pl["size_line"].startswith("·"))
    check("plan 带 leverage_note 说明打到止损即本金归零",
          pl and "本金归零" in pl["leverage_note"] and "强平线" in pl["leverage_note"])
    seg1 = sm._exit_segments()[0]
    check("认输线写明「就是强平线」", "就是强平线" in seg1[2], seg1[2])
    # 计划块不能出现双 bullet
    f = sm._facts(_an(sm, r))
    block = "\n".join(sm._plan_block(f))
    check("操作计划块无 · · 连续符号", "· ·" not in block)
    check("计划块含杠杆算术提醒", "本金归零" in block)

    # 源码里不得存在给空头出计划的路径
    src = _src("square_monster.py")
    check("_plan 里写死 direction='long'", '"direction": "long"' in src)
    check("没有 direction='short' 的出场计划", '"direction": "short"' not in src.replace(
        '"direction": "long"', ""))


def test_render_styles():
    """4 套风格都能出稿，结论段在所有风格下一致（同源数据不因风格变结论）。"""
    print("\n=== ⑤ 组稿四风格 ===")
    import square_monster as sm

    an = _an(sm, _mkrow(stage="IGNITION", change24_pct=2.0, oi_chg24=1.0,
                        manip=["合约独大"], factors={"funding": -0.003, "oi_pulse15": 6.0}))
    seen = set()
    for st in sm.STYLES:
        art = sm._article_full(an, st)
        check(f"风格 {st} 出稿", bool(art["title"]) and len(art["body"]) > 300)
        check(f"风格 {st} 带 $cashtag + #话题",
              any(t.startswith("$") for t in art["tags"]) and any(t.startswith("#") for t in art["tags"]))
        # 结论一致性：同一份数据不许因风格不同给出不同动作
        check(f"风格 {st} 正文含统一结论「{an['verdict_label']}」",
              an["verdict_label"] in art["body"])
        seen.add(art["title"])
    check("四套风格标题互不相同", len(seen) == len(sm.STYLES))

    # 不进场时稿中必须明说「不给点位」
    an2 = _an(sm, _mkrow(stage="EXTENDED", change24_pct=20.0))
    for st in sm.STYLES:
        body = sm._article_full(an2, st)["body"]
        check(f"风格 {st} 在不进场时明说不给点位",
              ("不给点位" in body or "没有点位" in body or "不进" in body), st)


def test_wrap_clamp_ellipsis():
    """超行必须加省略号 —— 截半句话会让人以为稿子坏了。"""
    print("\n=== ⑥ 折行截断带省略号 ===")
    import square_monster as sm
    from PIL import Image, ImageDraw

    dr = ImageDraw.Draw(Image.new("RGB", (400, 200), (0, 0, 0)))
    font = sm._font(12)
    long_text = "这是一段故意写得很长的说明文字，用来验证超出行数时会不会老实加省略号而不是把话截断"
    out = sm._wrap_clamp(dr, long_text, font, 200, 2)
    check("限制为 2 行", len(out) == 2)
    check("末行以省略号结尾", out[-1].endswith("…"), out[-1])
    short = sm._wrap_clamp(dr, "短句", font, 200, 3)
    check("不超行时不动它", short == ["短句"])


# ---------------- ⑦⑧ 落盘产物 / 符号不在视野内 ----------------

def _an(sm, row: dict) -> dict:
    sa, ca, fa = sm._axis_stage(row), sm._axis_control(row), sm._axis_fuel(row)
    vk, vline, vc = sm._verdict(sa, ca, fa)
    return {"ok": True, "symbol": row.get("symbol", "TESTUSDT"), "market": "futures",
            "row": row, "stage_axis": sa, "control_axis": ca, "fuel_axis": fa,
            "verdict": vk, "verdict_line": vline, "verdict_color": vc,
            "verdict_label": sm.VERDICTS[vk][0], "plan": sm._plan(sa, ca, fa, row),
            "exit_segments": sm._exit_segments(), "missing": [], "score": row.get("score"),
            "reasons": [], "radar_meta": {}}


def _synth_klines(n=90, base=1.0) -> dict:
    o, h, l, c, v, t = [], [], [], [], [], []
    p = base
    for i in range(n):
        p = p * (1 + 0.01 + 0.02 * math.sin(i / 3.1))
        o.append(p * 0.995)
        h.append(p * 1.012)
        l.append(p * 0.988)
        c.append(p)
        v.append(1e6)
        t.append(1750000000000 + i * 86400000)
    return {"opens": o, "highs": h, "lows": l, "closes": c, "vols": v, "times": t}


def test_compose_products():
    """compose 产物结构与代币引擎同构 —— 发布链路（square-post --cover/--title-file）
    不需要为妖币改一行；plan 节结构也要能被 paper_tracker 直接读。"""
    print("\n=== ⑦ compose 产物 ===")
    import json
    import square_monster as sm

    row = _mkrow(stage="IGNITION", change24_pct=2.5, oi_chg24=1.5,
                 manip=["合约独大"], factors={"funding": -0.003, "oi_pulse15": 6.0,
                                              "taker_ratio": 2.0, "rvol15": 3.2})
    orig_row_of, orig_collect = sm._row_of, sm._collect
    sm._row_of = lambda sym: dict(row)
    sm._collect = lambda sym, mkt: {"market": "futures", "k90": _synth_klines()}
    try:
        out = sm.compose("TESTUSDT", "futures", "playbook")
    finally:
        sm._row_of, sm._collect = orig_row_of, orig_collect

    check("compose ok", out.get("ok") is True, str(out)[:200])
    for key in ("dir", "title_file", "text_file", "cover", "tags", "stats", "plan"):
        check(f"产物含 {key}", key in out)
    check("engine 标记为 monster", out.get("engine") == "monster")
    check("封面图落盘且非空", os.path.getsize(out["cover"]) > 8000)
    check("竖版剧本卡落盘且非空", os.path.getsize(out["extra_chart"]) > 8000)

    meta = json.load(open(os.path.join(out["dir"], "meta.json"), encoding="utf-8"))
    # paper_tracker._paper_order_from_run 读这些键
    for key in ("symbol", "market", "plan"):
        check(f"meta.json 含 paper_tracker 需要的 {key}", key in meta)
    check("plan 结构含 entry/stop/tp/price/zone_lo/zone_hi",
          all(k in (meta["plan"] or {}) for k in
              ("direction", "entry", "stop", "tp", "price", "zone_lo", "zone_hi")))
    check("meta 记录三轴读数", meta.get("axes", {}).get("stage") == "IGNITION")
    check("meta 记录出场四段", len(meta.get("exit_segments") or []) == 4)
    check("tags 在 meta 里", bool(meta.get("tags")))

    # 不发点位时不建单（plan=null）—— 这是正确行为而非漏单
    row2 = _mkrow(stage="EXTENDED", change24_pct=22.0)
    sm._row_of = lambda sym: dict(row2)
    sm._collect = lambda sym, mkt: {"market": "futures", "k90": _synth_klines()}
    try:
        out2 = sm.compose("TEST2USDT", "futures", "plain")
    finally:
        sm._row_of, sm._collect = orig_row_of, orig_collect
    check("不进场时 plan=null", out2.get("plan") is None)
    meta2 = json.load(open(os.path.join(out2["dir"], "meta.json"), encoding="utf-8"))
    check("不进场时 meta.plan=null", meta2.get("plan") is None)


def test_symbol_out_of_radar():
    """不在妖币雷达视野内 → 明确报错 + 提示改用 SMC 引擎，绝不硬编一篇。"""
    print("\n=== ⑧ 符号不在视野内 ===")
    import square_monster as sm

    orig = sm._row_of
    sm._row_of = lambda sym: {}
    try:
        out = sm.analyze("BTCUSDT")
    finally:
        sm._row_of = orig
    check("analyze 返回失败", out.get("ok") is False)
    check("错误里说明「不在妖币雷达视野内」", "不在妖币雷达视野内" in out.get("error", ""))
    check("错误里指引改用 square-rich-post（SMC）",
          "square-rich-post" in out.get("error", "") and "SMC" in out.get("error", ""))
    check("空 SYMBOL 也失败", sm.analyze("").get("ok") is False)


# ---------------- ⑨ 决策自动复核 ----------------

def test_radar_recheck_decisions():
    """决策 2/3 的复核：0 样本必须 waiting 且 gap 如实；样本够了必须给结论。"""
    print("\n=== ⑨ 决策自动复核 ===")

    d = state.radar_recheck_decisions("LONG")
    check("空库返回 ok", d.get("ok") is True)
    check("0 样本时 decision2 = waiting", d["decision2"]["status"] == "waiting")
    check("0 样本时 decision3 = waiting", d["decision3"]["status"] == "waiting")
    check("decision2 gap 如实 = 10（两个桶各 5 笔）", d["decision2"]["gap"] == 10,
          str(d["decision2"]["gap"]))
    check("decision3 gap 如实 = 5", d["decision3"]["gap"] == 5, str(d["decision3"]["gap"]))
    check("明确说不改规则", "不改任何规则" in d["note"])
    check("waiting 时建议里出现「还差」", "还差" in d["decision2"]["recommend"])

    # —— 造样本：OPT-04 之后仍留下两组、且分数高的那组赢 —— #
    # 低分组（score 8）× 5：全 dump；高分组（score 30）× 5：全 moon
    for i in range(5):
        tid = state.radar_track_add(f"LOW{i}USDT", "IGNITION", 1.0, found_score=8,
                                    direction="LONG", snap={"chg24": 1.0, "oi24": 0.5})
        state.radar_track_close(tid, "dump", 0.9)
    for i in range(5):
        tid = state.radar_track_add(f"HIGH{i}USDT", "IGNITION", 1.0, found_score=30,
                                    direction="LONG", snap={"chg24": 1.0, "oi24": 0.5})
        state.radar_track_close(tid, "moon", 1.3)
    # 3 笔「登记时已下跌」：2 moon / 1 dump → 反例不足（<5），仍 waiting
    for i, oc in enumerate(("moon", "moon", "dump")):
        tid = state.radar_track_add(f"DOWN{i}USDT", "ACCUMULATION", 1.0, found_score=12,
                                    direction="LONG", snap={"chg24": -8.0, "oi24": -1.0})
        state.radar_track_close(tid, oc, 1.2 if oc == "moon" else 0.9)

    d2 = state.radar_recheck_decisions("LONG")
    check("样本够了 decision2 = ready", d2["decision2"]["status"] == "ready",
          str(d2["decision2"]["status"]))
    check("decision2 识别出高分桶胜率更高",
          "高" in d2["decision2"]["recommend"] or "可以设了" in d2["decision2"]["recommend"],
          d2["decision2"]["recommend"])
    check("OPT-04 之后样本数 = 13（无 ≥10% 涨幅样本被剔）",
          d2["n_after_opt04"] == 13, str(d2["n_after_opt04"]))
    check("decision3 仍 waiting（只 3 笔下跌样本）",
          d2["decision3"]["status"] == "waiting", str(d2["decision3"]["status"]))
    check("decision3 gap = 2", d2["decision3"]["gap"] == 2, str(d2["decision3"]["gap"]))
    check("decision3 统计到 3 笔已关单 + 2 笔 moon",
          d2["decision3"]["n_down_closed"] == 3 and d2["decision3"]["n_down_moon"] == 2)

    # —— OPT-04 剔除口径：把 high 那 5 笔改成「登记时已涨 12%」→ 应被剔除 —— #
    state._conn_get().execute(
        "UPDATE radar_tracks SET snap_chg24=12.0 WHERE symbol LIKE 'HIGH%'")
    state._conn_get().commit()
    d3 = state.radar_recheck_decisions("LONG")
    check("OPT-04 剔除生效：dropped = 5", d3["n_dropped_by_opt04"] == 5,
          str(d3["n_dropped_by_opt04"]))
    check("剔除后剩余 8 笔 → decision2 回落为 waiting",
          d3["decision2"]["status"] == "waiting", str(d3["decision2"]["status"]))
    # 剩余 8 笔 = 5 笔低分(score 8 → 0-9 桶) + 3 笔「登记时已下跌」(score 12 → 10-14 桶)。
    # 注意 10-14 桶里是**下跌样本**而不是高分组 —— 这正是「按分数看胜率」会被混杂变量污染的现场。
    check("剔除后分桶 = [5, 3, 0, 0, 0]",
          [b["n"] for b in d3["decision2"]["buckets_after_opt04"]] == [5, 3, 0, 0, 0],
          str([b["n"] for b in d3["decision2"]["buckets_after_opt04"]]))

    # —— 补齐下跌样本到 5 笔且多为 dump → decision3 ready 且判「该挡」 —— #
    for i in (3, 4):
        tid = state.radar_track_add(f"MORE{i}USDT", "ACCUMULATION", 1.0, found_score=11,
                                    direction="LONG", snap={"chg24": -6.0, "oi24": -1.0})
        state.radar_track_close(tid, "dump", 0.9)
    d4 = state.radar_recheck_decisions("LONG")
    check("下跌样本到 5 笔 → decision3 ready", d4["decision3"]["status"] == "ready",
          str(d4["decision3"]["status"]))
    check("2 moon / 5 笔 = 40% > 20% → 判「不该挡」",
          "不该挡" in d4["decision3"]["recommend"], d4["decision3"]["recommend"])

    # 做空组不串味
    d5 = state.radar_recheck_decisions("SHORT")
    check("SHORT 组独立统计（不与 LONG 混）", d5["n_closed"] == 0, str(d5["n_closed"]))


# ---------------- ⑩ 接线 ----------------

def test_wiring():
    """路由提示词 / llm 工具 schema / 技能发现 / 前端文案 —— 一处漏了就等于没接上。"""
    print("\n=== ⑩ 接线 ===")
    ac = _src("agent_core.py")
    check("agent 提示词提到 square-monster-post", "square-monster-post" in ac)
    check("agent 提示词要求先按标的分流（代币 vs 妖币）", "代币 vs 妖币必须先分流" in ac)
    check("agent 提示词写清「绝不可互相替代」",
          "绝不可互相替代" in ac or "两者绝不可互相替代" in ac)
    check("agent 提示词有 audit 模式说明", "mode='audit'" in ac)
    check("agent 提示词里有 _run_radar_audit 实现", "_run_radar_audit" in ac)

    llm = _src("llm.py")
    check("llm meme_watch enum 含 audit", '"audit"' in llm)
    check("llm 描述里说明 audit 只给证据不改规则", "只给证据不改规则" in llm)

    dr = open(os.path.join(ROOT, "desktop_app.py"), encoding="utf-8").read()
    check("后端有 /api/square/monster/compose", "/api/square/monster/compose" in dr)
    check("后端有 /api/square/monster/analyze", "/api/square/monster/analyze" in dr)
    check("后端注释写明两套引擎不是同一套的参数",
          "两套引擎" in dr or "**与 /api/square/rich/compose 是两套引擎" in dr)

    # 技能可被 agent 发现（builtin_skills 扫 .agents/skills）
    import skills_client as sc
    b = sc.builtin_skills()
    check("square-monster-post 被识别为内置技能", "square-monster-post" in b)
    check("技能 description 提取到了（agent 靠它决定何时触发）",
          bool(b.get("square-monster-post", {}).get("desc")))
    check("description 里没有残留 markdown **",
          "**" not in (b.get("square-monster-post", {}).get("desc") or ""))
    check("square-rich-post 描述也标了「代币专用」",
          "代币专用" in (b.get("square-rich-post", {}).get("desc") or ""))
    cli = os.path.join(ROOT, ".agents", "skills", "square-monster-post", "scripts", "cli.mjs")
    check("技能 cli.mjs 存在", os.path.isfile(cli))
    check("cli.mjs 打的是 monster 端点", "/square/monster/compose" in open(
        cli, encoding="utf-8").read())

    loc = open(os.path.join(ROOT, "frontend", "src", "i18n", "locales.ts"),
               encoding="utf-8").read()
    check("前端模拟单提示提到两套引擎", "square-monster-post" in loc)


def main():
    for fn in (test_engine_separation, test_constant_mirrors, test_stage_axis_and_verdict,
               test_only_long_never_short, test_render_styles, test_wrap_clamp_ellipsis,
               test_compose_products, test_symbol_out_of_radar,
               test_radar_recheck_decisions, test_wiring):
        fn()
    print("\n" + "=" * 60)
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
