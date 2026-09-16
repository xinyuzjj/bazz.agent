"""v1.6.3 妖币「控盘代理层」测试 —— 零网络、隔离临时库。

背景（用户指令："你对于妖币的理解还不够，你去搜索一下什么是妖币，不然 rave 等代币"）：
查证后推翻了原设计的隐含假设 —— 妖币不是「涨得猛波动的币」，而是
**庄家控盘 96%+ 的收割装置**：
  · 现货控盘率 96% 以上（链上多签钱包囤 90%+ 流通量，top10 钱包 >98%）
  · 只上合约不上现货（现货无深度 → 少量资金能撬动）
  · 庄家靠「引发多空清算 + 吃对手方费率」获利，最后现货出货
RAVE 实证：9 天 $0.25 → $27.9（+10,800%），24h 内 −90%；
  空头爆仓 $2399 万 = 全部爆仓 82%；费率年化 −1000% ~ −4000%。

因此 v1.6.2 的「方向化」修复**必要但不够**：它只解决「别在下跌途中登记」，
没解决「找到的这一批本身就是庄家的收割工具」。

v1.6.3 的定位（用户确认）＝「顺剧本骑①②」：
只做控盘后的首次拉升；负费率 / 派发特征一出现即强制离场；**绝不做空埋伏**。

控制代理（链下、零新增接口）：公开接口拿不到持币集中度，
  改用 ① 现货成交额占比 ② 合约成交/持仓比 ③ 费率正负 三个可算指纹。

覆盖：
1. _manip_flags 四个信号各自成立 / 不成立
2. 「拉升无爆仓」必须带数据可用性守卫（无 ws 数据时不得假阳性）
3. _radar_score 费率方向化：正费率加分、负费率不再加分（旧 abs() 的 bug）
4. _radar_score 控盘扣分生效
5. _stage_of：负费率**禁止** SHORT_AMBUSH（RAVE 剧本里做空=被割的那一方）
6. **实测修正**：负费率只标注、不硬降级做多（反例 VTHOUSDT 费率 −0.776% 却是 +31.3% moon）
7. **绝不做空**：SHORT_AMBUSH 移出 ignition 登记组、只留 takeoff 展示组
8. 源码护栏：不得再出现 abs(funding) 这类方向无关写法
9. 链路护栏：contract 独大 / 换手畸高 所需的 fut_qv、oi_usd 已接进主流程

安装版真实关单回放（23 笔 = 20 LONG（3 moon / 17 dump）+ 3 SHORT）得到的结论：
  · 3 笔做空 3/3 全亏（轧空 +10.0/+10.3/+11.0%，最大有利仅 0.0/0.0/1.4%）→ 支持「绝不做空」
  · 负费率既出现在 dump（REZ −0.373%、MTL −0.237%）也出现在 moon（VTHO −0.776%）
    → **负费率不是可靠判别器**，故第 6 项把硬降级降为标注（v1.6.4 进一步把扣分 10 → 3）
  · dump 组「最大涨」几乎全 ≤6.7%（大量 0.0~3.3%），moon 组全是 30%+
    → 真正的下一刀**不是入场确认**（用户已否决候选观察期，定版「登记即入场、不设候选态」），
      而是**登记时刻的快照过滤**：只登记「价格未启动（chg24<3%）且持仓未堆积（OI24<5%）」的币。

运行：.venv/Scripts/python.exe tests/test_v163_manip_guard.py
"""
import ast
import os
import sys
import tempfile

# 必须在 import 任何 src 模块之前：workspace.py import 时读该 env 决定库位置
os.environ["BAZZ_WORKSPACE"] = tempfile.mkdtemp(prefix="bazz_test_v163_")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

import scanner  # noqa: E402

FAILS = []


def check(name: str, cond: bool, extra: str = "") -> None:
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f"  ({extra})" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def _src() -> str:
    return open(os.path.join(SRC, "scanner.py"), encoding="utf-8").read()


def _clean_d() -> dict:
    """干净底料：不触发任何控盘代理。
    注意 `spot_qv`（现货成交额）与 `quote_volume` 是**两个口径** —— 池子扩到合约全市场后，
    纯合约币的 quote_volume 是合约额，控盘层只认 spot_qv。"""
    return {"price": 1.0, "quote_volume": 1e7, "spot_qv": 1e7, "fut_qv": 0.0, "oi_usd": 0.0,
            "chg24": 0.0, "funding": 0.0, "liq_5m": 0.0, "liq_n5m": 0}


# ---------------- 1) 控盘代理四个信号 ----------------

def test_manip_flags_unit():
    M = scanner._manip_flags

    # ① 现货无深度：现货占比 < 5% 且合约成交额够大
    d = _clean_d()
    d.update({"spot_qv": 2e5, "fut_qv": 2e7})
    flags, note, pen = M(d)
    check("控盘①：现货占比 1% → 命中「合约独大」", "合约独大" in flags, str(flags))
    check("控盘①：命中即扣分", pen > 0, str(pen))

    # 现货占比正常（30%）不应命中
    d2 = _clean_d()
    d2.update({"spot_qv": 6e6, "fut_qv": 1.4e7})
    flags2, _, _ = M(d2)
    check("控盘①：现货占比 30% 不命中", "合约独大" not in flags2, str(flags2))

    # ①b 压根没有现货市场（RAVE / LAB）→ 单列「无现货」，不得被当成「占比 50%」
    d0 = _clean_d()
    d0.update({"no_spot": True, "spot_qv": 0.0, "quote_volume": 2e7, "fut_qv": 2e7})
    flags0, note0, pen0 = M(d0)
    check("控盘①b：无现货市场 → 命中「无现货」", "无现货" in flags0, str(flags0))
    check("控盘①b：不被误判为「合约独大」", "合约独大" not in flags0, str(flags0))

    # ② 换手畸高：合约 24h 成交 / 持仓 > 10x（RAVE ≈23x）
    d3 = _clean_d()
    d3.update({"fut_qv": 6.9e9, "oi_usd": 3e8})
    flags3, _, _ = M(d3)
    check("控盘②：成交/持仓 23x → 命中「换手畸高」", "换手畸高" in flags3, str(flags3))

    d4 = _clean_d()
    d4.update({"fut_qv": 1e7, "oi_usd": 5e6})       # 2x，正常水位
    flags4, _, _ = M(d4)
    check("控盘②：成交/持仓 2x 不命中", "换手畸高" not in flags4, str(flags4))

    # ③ 空头付钱：费率 ≤ −0.15%（RAVE 剧本：负费率 = 空头给多头送钱）
    d5 = _clean_d()
    d5.update({"funding": -0.002})
    flags5, _, pen5 = M(d5)
    check("控盘③：费率 −0.20% → 命中「空头付钱」", "空头付钱" in flags5, str(flags5))
    # v1.6.4 降权 10 → 3：VTHOUSDT 登记时费率 −0.776% 却是 +31.3% moon，
    # 全样本负费率只有 1 moon / 2 dump —— 对做多结局判别力弱，不该压掉真妖币。
    check("控盘③：扣分已降权到 3（不再高于其它信号）", 3.0 <= pen5 < 6.0, str(pen5))

    d6 = _clean_d()
    d6.update({"funding": 0.002})                   # 正费率＝多头拥挤，是热度不是收割
    flags6, _, _ = M(d6)
    check("控盘③：正费率 +0.20% 不命中", "空头付钱" not in flags6, str(flags6))

    # ④ 拉升无爆仓（数据可疑 / 假突破诱多）
    d7 = _clean_d()
    d7.update({"chg24": 12.0, "liq_5m": 1e4, "liq_n5m": 3})
    flags7, _, _ = M(d7)
    check("控盘④：涨 12% 但 5m 爆仓仅 1 万 → 命中「拉升无爆仓」",
          "拉升无爆仓" in flags7, str(flags7))

    # 干净底料
    flags8, note8, pen8 = M(_clean_d())
    check("控盘：干净数据零命中、零扣分",
          flags8 == [] and pen8 == 0.0 and note8 == "", f"{flags8} {pen8}")


def test_manip_liq_data_guard():
    """无 ws 爆仓数据（liq_n5m=0）时不得假阳性 —— 否则涨了的币全被判「拉升无爆仓」。"""
    M = scanner._manip_flags
    d = _clean_d()
    d.update({"chg24": 15.0, "liq_5m": 0.0, "liq_n5m": 0})
    flags, _, _ = M(d)
    check("控盘④：无爆仓数据源时不命中（数据可用性守卫）",
          "拉升无爆仓" not in flags, str(flags))

    d2 = _clean_d()
    d2.update({"chg24": 15.0, "liq_5m": 0.0, "liq_n5m": 0})
    d2["liq_n5m"] = 5                                # 有数据且确实没爆仓
    flags2, _, _ = M(d2)
    check("控盘④：有数据源且确实无爆仓 → 命中",
          "拉升无爆仓" in flags2, str(flags2))


# ---------------- 2) 评分层费率方向化 ----------------

def test_score_funding_directional():
    """旧 bug：`abs(funding)` 让负费率也当成「拥挤度」加分 ——
    而 RAVE 费率 −2%/4h 远超 0.3% 阈值，会被**加满 4 分**排到最前面。"""
    S = scanner._radar_score
    t = {"flow": 2000.0, "jump_L": 0.0, "speed5m": 0.0, "rvol15": 0.0}
    d = _clean_d()

    s_zero = S(t, {"funding": 0.0}, d, False)
    s_pos = S(t, {"funding": 0.003}, d, False)
    s_neg = S(t, {"funding": -0.002}, d, False)

    check("评分：正费率 +0.30% 加分", s_pos - s_zero == 4, f"{s_zero}→{s_pos}")
    check("评分：负费率**不再**加分（旧 abs 写法会给 +4）",
          s_neg == s_zero, f"{s_zero}→{s_neg}")

    d_neg = _clean_d()
    d_neg["funding"] = -0.002
    s_neg2 = S(t, {"funding": -0.002}, d_neg, False)
    check("评分：负费率经控盘层扣 3 分（v1.6.4 由 10 降权）",
          s_zero - s_neg2 == 3, f"{s_zero}→{s_neg2}")


def test_score_manip_penalty():
    S = scanner._radar_score
    t = {"flow": 2000.0, "jump_L": 0.0, "speed5m": 0.0, "rvol15": 0.0}
    base = S(t, {"funding": 0.0}, _clean_d(), False)

    d = _clean_d()
    d.update({"spot_qv": 2e5, "fut_qv": 2e7})   # 合约独大
    hit = S(t, {"funding": 0.0}, d, False)
    check("评分：控盘信号命中后总分下降", hit < base, f"{base}→{hit}")


# ---------------- 3) 语义层：绝不做空 + 特征一现即离场 ----------------

def _short_ambush_d(funding: float) -> dict:
    """构造一个「高位滞涨 + 大户拥挤 + 已破位」的盘面（旧实现的 SHORT_AMBUSH 场景）。"""
    d = _clean_d()
    d.update({
        "pos": 0.85, "chg30d": 40.0, "chg3d": 2.0, "chg24": 3.0, "chg1h": -2.5,
        "funding": funding, "funding_peak": funding,
        "top_ratio": 2.5, "taker_ratio": 1.0,
        "oi_chg24": 0.0, "oi_chg48": 0.0, "oi_pulse15": 0.0,
        "liq_5m": 0.0, "liq_side": "", "amp24": 8.0, "rvol_d": 1.0, "rvol15": 0.0,
        "flow": 0.0, "breakout20": False,
    })
    return d


def test_no_short_when_negative_funding():
    """负费率下禁止做空：做空正是妖币剧本里被挤爆的位置（RAVE 空头爆仓占 82%）。"""
    stage_pos, _, _, side_pos, _ = scanner._stage_of(_short_ambush_d(0.002))
    check("做空：正费率下「做空埋伏」仍可成立（其它条件已满足）",
          stage_pos == "SHORT_AMBUSH", str(stage_pos))

    stage_neg, _, _, side_neg, note_neg = scanner._stage_of(_short_ambush_d(-0.002))
    check("做空：负费率下**禁止**给做空信号", stage_neg != "SHORT_AMBUSH", str(stage_neg))
    check("做空：负费率下不产生 WATCH_SHORT 侧", side_neg != "WATCH_SHORT", str(side_neg))


def test_negative_funding_marks_not_blocks():
    """v1.6.3 实测修正：负费率只**标注**、不硬降级。

    反例来自安装版真实数据：VTHOUSDT 登记时费率 **−0.776%**，结果却是 +31.3% 的 moon。
    币安永续上「空头拥挤」本身就会把费率压成负值，低位负费率反而是轧空燃料 ——
    硬拦会误杀赢家，正是 v1.6.2「过滤过紧」教训的翻版。
    """
    d = _clean_d()
    d.update({
        "pos": 0.4, "chg24": 4.0, "chg1h": 0.5, "chg3d": 1.0, "chg30d": 5.0,
        "breakout20": True, "rvol_d": 2.5, "amp24": 8.0,
        "funding": 0.002, "funding_peak": 0.002,
        "oi_chg24": 0.0, "oi_chg48": 0.0, "oi_pulse15": 0.0,
    })
    stage, _, _, side, _ = scanner._stage_of(d)
    check("标注：正费率下点火阶段给 LONG",
          stage == "IGNITION" and side == "LONG", f"{stage}/{side}")

    d2 = dict(d)
    d2.update({"funding": -0.002, "funding_peak": -0.002})
    stage2, _, tag2, side2, note2 = scanner._stage_of(d2)
    check("标注：负费率**不**改变方向（避免误杀 VTHO 式赢家）",
          side2 == "LONG", f"{stage2}/{side2}")
    check("标注：说明里点明是空头付钱", "空头付钱" in note2, note2)


def test_only_unstarted_registered():
    """「登记即入场」的前提：只登记**尚未启动**的币（登记时 chg24 < 追高线）。

    实测（安装版 20 笔 LONG 已关单）：
      · 3 笔 moon 的登记依据里**全都没有「24h +X%」** → 登记时尚未启动
      · 9 笔「登记时已涨 7.4%~24.8%」的**全是 dump**
    回放：现状 20 笔 −1086.5U / 胜率 15.0% → 只留未启动 7 笔 **+213.5U / 胜率 42.9%**。
    """
    src = _src()
    line = next((l for l in src.splitlines() if l.strip().startswith("rows_ign = [")), "")
    # v1.6.4：判定函数由 `_started`（只看价格）改为 `_late`（价格已启动 **或** 持仓已堆积）。
    # 阈值写在 `_late` 的函数体里（不是 def 那一行），所以要整段取。
    late_body = src[src.index("def _late("):src.index("rows_ign = [")]
    check("登记：rows_ign 带「未启动」前置条件", "not _late(r)" in line, line[:130])
    # v1.6.5（OPT-04）：硬挡线由 _TRIG_UP_CHG24(3.0，触发线) 换成 _TRIG_LATE_CHG24(10.0，有据线)。
    # 触发线仍是 3.0（"24h +X%" 依据生成用），但**它不再是登记闸门** —— 两者必须分开，
    # 否则「依据生成阈值」和「登记门槛」会再次被动地绑成一个数。
    check("登记：未启动判定的硬挡线是 _TRIG_LATE_CHG24（10.0，与 3.0 触发线解耦）",
          "_TRIG_LATE_CHG24" in late_body, late_body[:220])
    check("登记：硬挡线不再误用触发线 _TRIG_UP_CHG24",
          "_TRIG_UP_CHG24" not in late_body, late_body[:220])
    check("登记：已启动的行不丢弃，划归 takeoff 仍可见",
          "and _late(r)" in src, "")


def test_short_ambush_not_tradeable():
    """绝不做空：SHORT_AMBUSH 必须离开 ignition（登记）组，只在 takeoff 组展示。

    实测：安装版已关单 3 笔做空 **3/3 全亏**（CAKE +10.0% / THETA +10.3% / SAGA +11.0% 轧空），
    最大有利仅 0.0% / 0.0% / 1.4% —— 从一开始就没跌过，复盘原文均「直接轧空上行（未给回踩）」。
    """
    src = _src()
    line_ign = next((l for l in src.splitlines() if l.strip().startswith("rows_ign = [")), "")
    line_tk = next((l for l in src.splitlines() if l.strip().startswith("rows_tk = [")), "")
    check("分组：SHORT_AMBUSH 不进 ignition（=不登记进跟踪表）",
          "SHORT_AMBUSH" not in line_ign, line_ign[:110])
    check("分组：SHORT_AMBUSH 进 takeoff（顶部风险仍可见）",
          "SHORT_AMBUSH" in line_tk, line_tk[:110])
    check("分组：ignition 只剩两个可下注阶段",
          all(k in line_ign for k in ("ACCUMULATION", "IGNITION")), line_ign[:110])


def test_stage_of_minimal_input_safe():
    """包装层不得因缺字段抛异常（真实调用里很多字段可能缺失）。"""
    try:
        res = scanner._stage_of({})
        ok = isinstance(res, tuple) and len(res) == 5
    except Exception as e:                                    # pragma: no cover
        ok = False
        res = repr(e)
    check("语义层：空 dict 安全返回五元组", ok, str(res))


# ---------------- 4) 源码护栏 ----------------

def test_no_abs_funding_in_score():
    """方向无关写法护栏：`_radar_score` 里不得对 funding 取绝对值。"""
    tree = ast.parse(_src())
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "_radar_score":
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                    and sub.func.id == "abs" and sub.args):
                arg = ast.unparse(sub.args[0])
                if "funding" in arg:
                    bad.append(arg)
    check("护栏：_radar_score 不再出现 abs(funding)", not bad, str(bad))


def test_radar_pool_unions_futures_only():
    """候选池必须含「只在合约」的**加密**币 —— 否则 RAVE / LAB 这类纯合约妖币永远不可见；
    但 **代币化股票/大宗（TradFi）必须挡在池外**，它们不是庄家控盘的妖币，
    进池只会挤占 top_n（实测 110 个池位里 57 个纯合约、一大半是 TradFi）。"""
    orig_s, orig_f = scanner.get_snapshot, scanner.futures_snapshot
    orig_c = scanner.futures_crypto_syms
    scanner.get_snapshot = lambda *a, **k: [
        {"symbol": "AAAUSDT", "quote_volume": 5e7, "price": 1.0, "change_pct": 1.0,
         "high": 1.1, "low": 0.9, "count": 1000},
    ]
    scanner.futures_snapshot = lambda *a, **k: [
        {"symbol": "AAAUSDT", "quote_volume": 9e7},
        {"symbol": "RAVEUSDT", "quote_volume": 8e7},
        {"symbol": "LABUSDT", "quote_volume": 6e7},
        {"symbol": "NVDAUSDT", "quote_volume": 9.9e9},      # 代币化股票，应被挡
        {"symbol": "XAUUSDT", "quote_volume": 9.8e9},       # 黄金，应被挡
    ]
    scanner.futures_crypto_syms = lambda *a, **k: {"AAAUSDT", "RAVEUSDT", "LABUSDT"}
    try:
        rows = scanner._radar_pool(10, 1e6)
    finally:
        scanner.get_snapshot, scanner.futures_snapshot = orig_s, orig_f
        scanner.futures_crypto_syms = orig_c
    syms = [r["symbol"] for r in rows]
    by = {r["symbol"]: r for r in rows}
    check("扩池：纯合约币（RAVE）进候选池", "RAVEUSDT" in syms, str(syms))
    check("扩池：LAB 进候选池", "LABUSDT" in syms, str(syms))
    check("扩池：代币化股票（NVDA）被挡在池外", "NVDAUSDT" not in syms, str(syms))
    check("扩池：大宗（XAU 黄金）被挡在池外", "XAUUSDT" not in syms, str(syms))
    check("扩池：无重复行（现货∪合约不是简单拼接）",
          len(syms) == len(set(syms)), str(syms))
    rave = by.get("RAVEUSDT") or {}
    check("扩池：合约独有的币带 no_spot 且 spot_qv=0",
          rave.get("no_spot") is True and rave.get("spot_qv") == 0.0, str(rave))
    aaa = by.get("AAAUSDT") or {}
    check("扩池：现货覆盖的币 no_spot=False、spot_qv=现货额",
          aaa.get("no_spot") is False and aaa.get("spot_qv") == 5e7, str(aaa))
    check("扩池：排序用 max(现货, 合约) 且只对入池币生效（AAA 9e7 居首）",
          syms[0] == "AAAUSDT", str(syms))
    check("扩池：门槛按 rank_qv（合约额够大即可入池）",
          all((r.get("rank_qv") or 0) >= 1e6 for r in rows), str(syms))

    # 合约快照挂掉时不得炸掉整池（回退纯现货）
    scanner.get_snapshot = lambda *a, **k: [
        {"symbol": "AAAUSDT", "quote_volume": 5e7, "price": 1.0, "change_pct": 1.0,
         "high": 1.1, "low": 0.9, "count": 1000},
    ]

    def _boom(*a, **k):
        raise RuntimeError("fapi down")

    scanner.futures_snapshot = _boom
    try:
        rows2 = scanner._radar_pool(10, 1e6)
    finally:
        scanner.get_snapshot, scanner.futures_snapshot = orig_s, orig_f
    check("扩池：合约快照失败时回退纯现货、不抛异常",
          [r["symbol"] for r in rows2] == ["AAAUSDT"], str([r["symbol"] for r in rows2]))

    # 白名单拉不到 → 纯合约币全部不入池（安全退化为扩池前行为），现货池照常
    scanner.get_snapshot = lambda *a, **k: [
        {"symbol": "AAAUSDT", "quote_volume": 5e7, "price": 1.0, "change_pct": 1.0,
         "high": 1.1, "low": 0.9, "count": 1000},
    ]
    scanner.futures_snapshot = lambda *a, **k: [
        {"symbol": "AAAUSDT", "quote_volume": 9e7},
        {"symbol": "RAVEUSDT", "quote_volume": 8e7},
    ]
    scanner.futures_crypto_syms = lambda *a, **k: set()
    try:
        rows3 = scanner._radar_pool(10, 1e6)
    finally:
        scanner.get_snapshot, scanner.futures_snapshot = orig_s, orig_f
        scanner.futures_crypto_syms = orig_c
    check("扩池：加密白名单为空时安全退化（只留现货，不误收 TradFi）",
          [r["symbol"] for r in rows3] == ["AAAUSDT"], str([r["symbol"] for r in rows3]))


def test_wiring():
    """控盘代理所需的底料必须真的接进主流程，否则信号永远不触发。"""
    src = _src()
    check("链路：get_radar_v2 调用 futures_snapshot()",
          "futures_snapshot()" in src)
    check("链路：注入 d['fut_qv']（合约成交额）", 'd["fut_qv"]' in src)
    check("链路：注入 d['oi_usd']（持仓美元额）", 'd["oi_usd"]' in src)
    check("链路：确认层提供 oi_last 原值", '"oi_last": 0.0' in src)
    check("链路：liq_n5m 合并进 d（爆仓数据守卫需要）", '"liq_n5m"' in src)
    check("链路：行出参带 manip 字段（前端可解释降级原因）", '"manip": mflags' in src)
    # v1.6.3 扩池：池子必须走 _radar_pool（现货 ∪ 合约），K 线必须带合约回退
    check("链路：get_radar_v2 用 _radar_pool 建池（不再只取现货快照）",
          "_radar_pool(top_n, floor)" in src)
    check("链路：_daily2_bars 与主流程共用同一个池",
          "rows = _radar_pool(top_n, min_qv)" in src)
    check("链路：15m/5m 扇出带 fut_fallback（否则纯合约币被静默丢弃）",
          '_klines_raw, s, "15m", 288, True' in src)
    check("链路：日线取数带 fut_fallback",
          '_klines_raw(sym, "1d", 110, fut_fallback=True)' in src)
    check("链路：d 注入 spot_qv / no_spot（控盘层读独立现货口径）",
          '"spot_qv": float(r.get("spot_qv") or 0.0)' in src)
    check("链路：扩池时用加密白名单挡掉代币化股票/大宗（TradFi）",
          "futures_crypto_syms()" in src and "代币化股票/大宗（TradFi）不是妖币" in src)


def main():
    for fn in (test_manip_flags_unit, test_manip_liq_data_guard,
               test_score_funding_directional, test_score_manip_penalty,
               test_no_short_when_negative_funding,
               test_negative_funding_marks_not_blocks,
               test_short_ambush_not_tradeable,
               test_only_unstarted_registered,
               test_radar_pool_unions_futures_only,
               test_stage_of_minimal_input_safe,
               test_no_abs_funding_in_score, test_wiring):
        fn()
    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：")
        for n in FAILS:
            print("   -", n)
        sys.exit(1)
    print("✅ 全部通过")


if __name__ == "__main__":
    main()
