"""广场发文 · 妖币剧本引擎（square-monster-post 技能的后端部分）。

—— 为什么妖币不能跟代币用同一套分析 ——
`square_rich.py` 是**代币**引擎，用的是 SMC：结构（HH/HL）、BOS・CHoCH、扫流动性、
OTE 0.618-0.705、OB 订单块、FVG 公允价值缺口。这套东西成立的前提是
**「价格由众多参与者的共同行为堆出来」** —— 结构位、订单块之所以有用，是因为那里
真的有人挂单、真的有共识。代币满足这个前提。

妖币不满足。控盘盘的 K 线是**画**出来的：你看到的 OB 就是诱多区，你看到的「扫流动性」
就是庄家专门去点你的止损。**用 SMC 分析妖币，等于拿散户的地图去找庄家的门。**

—— 妖币该看什么 ——
看**控盘意图**。本模块用「剧本三轴」：

  ① 位阶 STAGE     —— 庄家剧本演到第几格（吸筹 → 点火 → 已拉升 → 垂直拉升 → 派发 → 崩跌 → 沉寂）
  ② 控盘度 CONTROL —— 这个盘是不是被攥在手里（无现货 / 合约独大 / 换手畸高）
  ③ 燃料 FUEL      —— 往上推的油从哪来（空头付钱 / OI 蓄力 / 大户拥挤 / 爆仓结构）

三轴合议才出动作，且**只做多、绝不做空**：安装版 3 笔做空全部被轧空击穿 10% 强平线，
最大有利分别仅 0.0% / 0.0% / 1.4% —— 妖币的顶部是二次拉升前的换手，不是做空窗口。

—— 出场用的是应用自己的真实机制，不是本模块发明的 ——
止损 10%（逆向近强平线）→ 达标 25% → 自持有期极值回撤 12% 移动止盈 → 反转因子提前落袋。
四个数**与 `radar_tracker` 逐一对应**，`tests/test_v166_monster_engine.py` 有断言钉住不许漂移。
妖币没有可信的结构位可以挂止损，所以用固定 10% —— 它同时也是 10x 的强平线口径，
这不是巧合，是同一件事：**你能承受的逆向幅度，就是你能活多久**。

—— 与 square_rich.py 的关系 ——
画图原语 / 取数 / 100U×10x 仓位口径直接复用（避免两套视觉、两套算法各自漂移），
但**分析层与组稿层完全另起** —— 共用 SMC 就等于没换技巧。见文件末尾的 import 白名单注释。
"""
import json
import os
import random
import time

import workspace

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # 极端缺失时不拖死后端启动
    Image = ImageDraw = ImageFont = None

# —— 只复用「底层原语」，绝不 import 任何 SMC 分析函数 —— #
# 允许：颜色 / 字体 / 格式化 / 画图原语 / 取数 / 仓位口径。
# 禁止：_smc / _htf_trend / _find_ob / _find_fvg / _bias / _plan_levels / _pick_tp /
#       _invalid_line / _macro_level —— 一旦 import 它们，这套框架就会悄悄退化成 SMC 的皮。
from square_rich import (  # noqa: E402
    BG, PANEL, GRID, TXT, SUB, UP, DOWN, ACCENT,
    _font, _fmt, _fmt_usd, _pct, _flat, _k_usable,
    _blend, _vgrad, _dashed, _dashed_rect, _draw_header, _draw_footer, _xaxis_label,
    _collect, _CAPITAL_U, _LEVERAGE, _RISK_TARGET_U, _size_line,
)

OUT_ROOT = os.path.join(workspace.WORKSPACE, "square_monster")

# ---------------- 出场四段（镜像 radar_tracker.py，测试钉住） ---------------- #
STOP_PCT = 10.0     # == radar_tracker.FAIL_HIT：逆向 ≥10% ≈ 10x 强平线 → 认输离场
HIT_PCT = 25.0      # == radar_tracker.GAIN_HIT：顺向 ≥25% → 达标（不直接落袋，转持有）
TRAIL_PCT = 12.0    # == radar_tracker.TRAIL_PCT：自持有期极值回撤 → 落袋
POS_USDT = 100.0    # == radar_tracker.POS_USDT：模拟本金

# ---------------- 雷达闸门线（镜像 scanner.py，测试钉住） ---------------- #
LATE_CHG24 = 10.0   # == scanner._TRIG_LATE_CHG24：涨幅硬挡线，越过 = 「你来晚了」
MAX_OI24 = 5.0      # == scanner._TRIG_MAX_OI24：OI 24h 堆积线，越过 = 「你来晚了」
MAX_CHG24 = 12.0    # == scanner._TRIG_MAX_CHG24：追高线，越过 → 剧本进 EXTENDED
WARM_CHG24 = 3.0    # == scanner._TRIG_WARM_CHG24：暖启动段下沿 [3, 10)

# ---------------- 剧本位阶表 ---------------- #
# 主剧本七格，顺序即剧本顺序；SHORT_AMBUSH / ACTIVE 是旁支，不占格。
STAGE_SEQ = ("ACCUMULATION", "IGNITION", "EXTENDED", "VERTICAL",
             "DISTRIBUTION", "CRASH", "DORMANT")

# stage → (短名, 色, 这一格在演什么, 下一格)
# 颜色是**温度刻度**（冷 → 热），刻意避开涨绿跌红 —— 那是行情语义，这里是「剧本烧到几度」。
STAGE_BOOK = {
    "ACCUMULATION": ("吸筹", (72, 124, 255),
                     "低位的货在被慢慢收走，价格故意压在区间里不让涨",
                     "点火：放量突破平台，剧本正式开演"),
    "IGNITION": ("点火", (240, 185, 11),
                 "第一次放量突破，量价同时在说话",
                 "继续拉升，或者假突破打回区间 —— 只有这一格值得盯盘"),
    "EXTENDED": ("已拉升", (247, 147, 30),
                 "涨幅已越过追高线，车开走了",
                 "垂直拉升，或者直接派发 —— 在这里进场等于接力末端"),
    "VERTICAL": ("垂直拉升", (217, 70, 239),
                 "暴力拉升，账面最爽、离场最难的一格",
                 "派发顶部：货开始往外走"),
    "DISTRIBUTION": ("派发顶部", (234, 88, 12),
                     "高位量价背离，庄家在出货",
                     "崩跌 —— 对持仓者，这一格本身就是离场信号"),
    "CRASH": ("崩跌", (100, 116, 139),
              "多杀多，接刀的人比出货的人多",
              "沉寂：量能枯竭，等下一轮吸筹"),
    "DORMANT": ("沉寂", (71, 85, 105),
                "泵后回吐，波动收敛",
                "下一次吸筹（只看有前科的大起大落币）"),
    # —— 旁支 —— #
    "SHORT_AMBUSH": ("做空埋伏", (148, 163, 184),
                     "拉升衰竭迹象（但妖币的顶常是二次拉升前的换手）",
                     "本框架：不参与 —— 做空妖币是站到庄家对面"),
    "ACTIVE": ("异动", (148, 163, 184),
               "短时窗触发了，但阶段特征还没收敛",
               "等量价配合确认，别抢着给它定剧本"),
}

# 合议动作 → (短标签, 一句话人话, 色)
VERDICTS = {
    "AMBUSH":  ("可埋伏", "剧本还在前半段，位置不算晚，小仓试错合理", (14, 203, 129)),
    "LATE":    ("窗口已过", "位置已经晚了，不是不能涨，是不该在这里上车", (247, 147, 30)),
    "RIDE":    ("持仓者减", "已在拉升中段，持仓者可留、空仓者勿追", (217, 70, 239)),
    "EXIT":    ("离场/不接", "剧本已进入出货段，对持仓者是离场信号", (246, 70, 93)),
    "NO_SHORT": ("不参与", "这是做空陷阱，不是做空机会", (246, 70, 93)),
    "WATCH":   ("只观察", "剧本特征未收敛，等确认", (148, 163, 184)),
}

# ---------------- 基础工具 ---------------- #

def _wrap(dr, text: str, font, maxw: float) -> list:
    """按像素宽度折行（中文无空格，逐字累加）。"""
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if cur and dr.textlength(cur + ch, font=font) > maxw:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def _wrap_clamp(dr, text: str, font, maxw: float, maxlines: int) -> list:
    """折行并限制行数。超出时在末行加省略号 —— **绝不把话截一半就丢掉**
    （读稿的人看到「这是唯一允」这种断句会以为稿子坏了）。"""
    lines = _wrap(dr, text, font, maxw)
    if len(lines) <= maxlines:
        return lines
    out = lines[:maxlines]
    last = out[-1]
    while last and dr.textlength(last + "…", font=font) > maxw:
        last = last[:-1]
    out[-1] = last + "…"
    return out


def _chip_label(dr, x, y, text, color, size=13, bold=False, pad=5):
    """带深色底衬的小标签。

    为什么需要：高/低/现价标签的位置是数据决定的，价格线正好压在文字上时（真实行情里
    很常见）就完全读不出来。加一层底衬让它无论如何都能看清，而不是赌数据配合。
    """
    f = _font(size, bold)
    w = dr.textlength(text, font=f)
    h = size + 6
    dr.rounded_rectangle([x - pad, y - pad, x + w + pad, y + h - pad + 2], radius=5,
                         fill=(13, 17, 22))
    dr.text((x, y), text, font=f, fill=color)


def _spct(v, nd=1) -> str:
    """带符号百分比。None → 「未测到」（绝不写 0，0 会被读成「涨跌为零」）。"""
    if v is None:
        return "未测到"
    try:
        return f"{float(v):+.{nd}f}%"
    except (TypeError, ValueError):
        return "未测到"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------- 取数：从雷达全量行里摘出这只币 ---------------- #

def _row_of(symbol: str) -> dict:
    """从 scanner.get_radar_v2 全量行里取 symbol 的行。

    查找顺序 coins（全量）→ takeoff → ignition，取不到返回 {}。
    **绝不自己用价量二次筛**：与「行情 → 妖币雷达」UI 展示的内容必须同源，
    否则稿子里说的位阶会和用户在 app 里看到的对不上。
    """
    import scanner
    v2 = scanner.get_radar_v2()
    sym = symbol.upper()
    for key in ("coins", "takeoff", "ignition"):
        for r in (v2.get(key) or []):
            if (r.get("symbol") or "").upper() == sym:
                r = dict(r)
                r["_radar_key"] = key
                r["_radar_meta"] = {
                    "scanned": v2.get("scanned"), "candidates": v2.get("candidates"),
                    "triggered": v2.get("triggered"), "confirmed": v2.get("confirmed"),
                    "env": v2.get("env"), "updated_at": v2.get("updated_at"),
                    "stage_counts": v2.get("stage_counts"),
                }
                return r
    return {}


# ---------------- 三轴 ---------------- #

def _axis_stage(row: dict) -> dict:
    """① 位阶轴：剧本演到第几格 + 这一格允不允许进场。

    这是三轴里**唯一能一票否决**的轴：位阶不对，控盘再重、燃料再满也不该进场
    （EXTENDED 之后的重控盘 + 满燃料，恰恰是最典型的出货前夜）。
    """
    st = row.get("stage") or "ACTIVE"
    seq = STAGE_SEQ.index(st) if st in STAGE_SEQ else None
    label, color, playing, nxt = STAGE_BOOK.get(
        st, (row.get("stage_label") or "未知", (148, 163, 184), "该阶段不在主剧本序列内", "—"))

    chg24 = _num(row.get("change24_pct"))
    oi24 = _num(row.get("oi_chg24"))
    # 晚不晚 = 雷达自己的两条闸门（价格已启动 / 持仓已堆积），**不另发明口径**
    price_late = chg24 is not None and chg24 >= LATE_CHG24
    oi_late = oi24 is not None and oi24 >= MAX_OI24
    late = price_late or oi_late
    late_why = []
    if price_late:
        late_why.append(f"价格已涨 {_spct(chg24)}（硬挡线 {LATE_CHG24:.0f}%）")
    if oi_late:
        late_why.append(f"持仓已堆积 OI 24h {_spct(oi24)}（堆积线 {MAX_OI24:.0f}%）")
    warm = chg24 is not None and WARM_CHG24 <= chg24 < LATE_CHG24

    if seq is None:                     # 旁支
        allow = "NO"
        why = ("做空埋伏是旁支：妖币的顶常是二次拉升前的换手，本框架不参与做空"
               if st == "SHORT_AMBUSH" else "阶段未收敛，无法判定进不进场")
    elif seq <= 1 and not late:         # 吸筹 / 点火 且不晚
        allow = "YES"
        why = "剧本还在前半段（吸筹/点火），且未越过晚点闸门 —— 这是唯一允许进场的窗口"
    elif seq <= 1 and late:
        allow = "NO"
        why = "阶段名义上还在前半段，但" + "、".join(late_why) + " —— 位置已经晚了"
    elif seq == 2:
        allow = "NO"
        why = f"已拉升：涨幅 {_spct(chg24)} 越过追高线 {MAX_CHG24:.0f}%，启动窗口已过"
    else:
        allow = "NO"
        why = f"{label}不是进场位 —— 这一格对持仓者是**离场**信号，对空仓者无意义"

    return {
        "stage": st, "label": label, "color": color, "seq": seq,
        "seq_len": len(STAGE_SEQ), "playing": playing, "next": nxt,
        "late": late, "late_why": late_why, "warm": warm,
        "allow": allow, "why": why,
        "pos_pct": _num(row.get("position_pct")),
    }


def _axis_control(row: dict) -> dict:
    """② 控盘度轴：这个盘是不是被攥在手里。

    **控盘度高 ≠ 坏消息。** 它说明「剧本是真的有人在演」，这恰恰是妖币会涨的原因。
    真正的含义是：**离场必须果断** —— 控盘盘没有自然买盘接你的货，你以为的支撑是画出来的。
    """
    flags = list(row.get("manip") or [])
    note = row.get("manip_note") or ""
    STRUCT = ("无现货", "合约独大", "换手畸高")     # 结构性控盘指纹（盘本身被攥住）
    POSITION = ("空头付钱", "拉升无爆仓")           # 持仓结构指纹（对手盘被收割中）
    hit_struct = [f for f in flags if f in STRUCT]
    hit_pos = [f for f in flags if f in POSITION]

    if len(hit_struct) >= 2:
        grade, color = "重度控盘", (246, 70, 93)
        reading = "盘本身被攥住了，价格是想画成什么就画成什么"
    elif hit_struct:
        grade, color = "明确控盘", (247, 147, 30)
        reading = f"有结构性控盘指纹：{'、'.join(hit_struct)}"
    elif hit_pos:
        grade, color = "持仓侧控盘", (240, 185, 11)
        reading = f"盘面结构正常，但对手盘在挨打：{'、'.join(hit_pos)}"
    else:
        grade, color = "未见控盘指纹", (148, 163, 184)
        reading = "没有算出控盘痕迹 —— 可能是正常异动，也可能是代理指标看不到的控法"

    # 数据缺失要如实说：缺了现货口径就算不出「现货无深度」，不能默认「没控盘」
    missing = []
    if row.get("factors", {}).get("top_ratio") is None:
        missing.append("大户多空比")
    if not flags and (row.get("oi_chg24") is None):
        missing.append("OI（确认因子未取到）")

    return {
        "flags": flags, "note": note, "grade": grade, "color": color,
        "reading": reading, "struct": hit_struct, "pos": hit_pos,
        "exit_discipline": "重度控盘/明确控盘" if hit_struct else "常规",
        "missing": missing,
    }


def _axis_fuel(row: dict) -> dict:
    """③ 燃料轴：往上推的油从哪来。

    妖币的燃料跟基本面无关，全是**对手盘的痛**：
      · 空头付钱（负费率）= 空头在给多头交钱，这是最猛的燃料（挤空收割）
      · OI 蓄力 = 有人在低位悄悄建仓，还没发力
      · 大户拥挤 = 大户/散户同侧，一旦反向就是连环爆
      · 爆仓结构 = 多头被清（对已持仓者是利空，对空仓者可能是坑口）
    """
    f = row.get("factors") or {}
    fund = _num(f.get("funding"))
    fund_pk = _num(f.get("funding_peak"))
    oi24 = _num(f.get("oi_chg24"))
    oi48 = None
    oi15 = _num(f.get("oi_pulse15"))
    top = _num(f.get("top_ratio"))
    glob = _num(f.get("global_ratio"))
    taker = _num(f.get("taker_ratio"))
    liq5 = _num(f.get("liq_5m")) or 0.0
    liqside = f.get("liq_side") or ""
    rvol15 = _num(f.get("rvol15"))

    items = []      # (标签, 读数, 是加油还是放油, 人话)
    # 空头付钱：最猛的燃料，同时是「绝不可做空」的铁证
    if fund is not None and fund <= -0.0015:
        items.append(("空头付钱", f"费率 {fund * 100:+.3f}%", +2,
                      "空头在给多头交钱 —— 拉升靠挤空，不靠买盘；此刻做空等于替庄家付账"))
    elif fund is not None and fund >= 0.003:
        items.append(("费率过热", f"费率 {fund * 100:+.3f}%", -1,
                      "多头开始给空头付钱，追多成本已经很高"))
    elif fund is not None:
        items.append(("费率中性", f"费率 {fund * 100:+.3f}%", 0, "资金成本正常"))

    # 费率自极值回落 = 真实的反转因子（与 radar_tracker._reversal_now 同口径）
    if fund_pk and fund is not None and fund_pk >= 0.003 and fund <= 0.4 * fund_pk:
        items.append(("费率极值回落", f"峰值 {fund_pk * 100:+.3f}% → {fund * 100:+.3f}%", -2,
                      "多头拥挤正在退潮 —— 这是 radar_tracker 认可的**提前落袋**信号"))

    if oi15 is not None and oi15 >= 5.0:
        items.append(("OI 脉冲", f"15m {_spct(oi15)}", +1, "有资金在短时窗里进场，点火瞬间特征"))
    if oi15 is not None and oi15 <= -5.0:
        items.append(("OI 反向脉冲", f"15m {_spct(oi15)}", -2, "持仓在快速离场，与价格背离时是顶部信号"))
    if oi24 is not None and oi24 >= 20.0 and abs(_num(row.get("change24_pct")) or 0) <= 3.0:
        items.append(("OI 蓄力", f"OI 24h {_spct(oi24)} 而价格几乎没动", +1,
                      "低位悄悄建仓、价格被压住 —— 这恰恰是「启动前」的样子"))

    if top is not None and (top >= 1.5 or (0 < top <= 0.7)):
        side = "大户偏多" if top >= 1.5 else "大户偏空"
        items.append(("大户拥挤", f"大户比 {top:.2f}（{side}）", 0,
                      "拥挤方向一旦反向就是连环爆，别跟大户站同一侧"))

    if taker is not None and taker >= 1.85:
        items.append(("买盘主导", f"taker {taker:.2f}", +1, "主动买占优，突破有人真金白银在推"))
    if rvol15 is not None and rvol15 >= 3.0:
        items.append(("量能放大", f"RVOL15 {rvol15:.1f}x", +1, "短时窗量能显著放大"))

    if liqside == "long" and liq5 >= 5e5:
        items.append(("多头爆仓潮", f"5m 爆仓 ${liq5 / 1e6:.1f}M（多头）", -2,
                      "多头在被清 —— 与 OI 同降时是「离场」，与 OI 同升时是「换手」"))
    if liqside == "short" and liq5 >= 5e5:
        items.append(("空头爆仓潮", f"5m 爆仓 ${liq5 / 1e6:.1f}M（空头）", +2,
                      "空头在被清 —— 轧空的典型画面"))

    score = sum(x[2] for x in items)
    if score >= 3:
        grade, color, gread = "满油", (14, 203, 129), "往上推的油是足的，且来源偏「对手盘在挨打」"
    elif score >= 1:
        grade, color, gread = "有油", (240, 185, 11), "有几处燃料，但还没到碾轧的程度"
    elif score <= -3:
        grade, color, gread = "油尽（在放油）", (246, 70, 93), "燃料侧在反向，这是离场读数而非进场读数"
    elif score <= -1:
        grade, color, gread = "在放油", (234, 88, 12), "出现反向读数，仓位要更谨慎"
    else:
        grade, color, gread = "中性", (148, 163, 184), "看不出明确的燃料结构"

    return {"items": items, "score": score, "grade": grade, "color": color,
            "reading": gread,
            "raw": {"funding": fund, "funding_peak": fund_pk, "oi_chg24": oi24,
                    "oi_pulse15": oi15, "top_ratio": top, "global_ratio": glob,
                    "taker_ratio": taker, "rvol15": rvol15,
                    "liq_5m": liq5, "liq_side": liqside}}


# ---------------- 合议 + 计划 ---------------- #

def _verdict(sa: dict, ca: dict, fa: dict) -> tuple:
    """三轴合议 → (动作, 一句话, 色)。

    规则很硬，且有优先级 —— 顺序本身就是这套框架的观点：
      1. 旁支（做空埋伏）→ 不参与，无论另外两轴多好看
      2. 已进入出货段（派发/崩跌）→ 离场，无论另外两轴多好看
      3. 位阶在拉升中段（垂直拉升）→ 持仓者减
      4. 位阶晚（已拉升 / 点火但已晚）→ 窗口已过
      5. 位阶在前半段且不晚 → 可埋伏
      6. 其余 → 只观察
    """
    st = sa["stage"]
    if st == "SHORT_AMBUSH":
        return ("NO_SHORT", VERDICTS["NO_SHORT"][1], VERDICTS["NO_SHORT"][2])
    if st in ("DISTRIBUTION", "CRASH"):
        return ("EXIT", VERDICTS["EXIT"][1], VERDICTS["EXIT"][2])
    if st == "VERTICAL":
        return ("RIDE", VERDICTS["RIDE"][1], VERDICTS["RIDE"][2])
    if st == "EXTENDED" or (sa["allow"] == "NO" and st in ("ACCUMULATION", "IGNITION")):
        return ("LATE", VERDICTS["LATE"][1], VERDICTS["LATE"][2])
    if sa["allow"] == "YES":
        return ("AMBUSH", VERDICTS["AMBUSH"][1], VERDICTS["AMBUSH"][2])
    return ("WATCH", VERDICTS["WATCH"][1], VERDICTS["WATCH"][2])


def _exit_segments() -> list:
    """出场四段 —— 全是 radar_tracker 的真实机制，本模块不发明。"""
    return [
        ("认输线", f"逆向 {STOP_PCT:.0f}%",
         f"在 {_LEVERAGE}x 下这条线**就是强平线** —— 打到它本金归零，没有「亏一点」这回事。"
         "这不是软件不够聪明，是杠杆的算术：100U 本金 ×10x = 1000U 名义，"
         f"逆向 {STOP_PCT:.0f}% 就是 100U。所以这条线的意义不是「保护你」，是**逼你认输**；"
         "真正能控的只有仓位，不是止损位。"),
        ("达标线", f"顺向 {HIT_PCT:.0f}%",
         "达到就先减一半。注意达标**不等于**落袋 —— 真妖币不会只走到 25%，剩下的交给移动止盈。"),
        ("移动止盈", f"自极值回撤 {TRAIL_PCT:.0f}%",
         f"走过 {HIT_PCT:.0f}% 之后，只要从持有期最高点回撤 {TRAIL_PCT:.0f}% 就落袋。"
         "妖币的主要收益在尾部，固定止盈会把最大那段切掉，所以用回撤止盈而不是目标价。"),
        ("提前落袋", "反转因子命中",
         "费率自极值回落 / OI 脉冲反向 ≤ -5% / 多头爆仓潮 —— 命中任一直接走，不等回撤 12%。"
         "这四个因子与 radar_tracker._reversal_now 完全同口径。"),
    ]


def _plan(sa: dict, ca: dict, fa: dict, row: dict) -> dict:
    """进场方案。**只在动作是 AMBUSH 时给点位** —— 其他情况给 null，绝不硬造方向。

    与代币引擎最大的不同：代币用 SMC 结构位定止损，妖币用固定 10%。
    原因写在 docstring 里了 —— 控盘盘的结构位不可信。
    """
    if sa["allow"] != "YES":
        return None
    price = _num(row.get("price"))
    if not price or price <= 0:
        return None
    entry = price
    stop = round(entry * (1.0 - STOP_PCT / 100.0), 10)
    if stop <= 0:
        return None
    dist = STOP_PCT
    notional = _CAPITAL_U * _LEVERAGE
    loss = notional * dist / 100.0
    # anchor 里不要再嵌套括号 —— _size_line 会把它整体再包一层，出现「（…（…）…）」
    size = _size_line(entry, stop, "long", f"固定 {STOP_PCT:.0f}% 认输线 · 妖币不挂结构止损")
    # _size_line 自带前导「· 」，这里要嵌进已带 bullet 的列表里 → 去掉重复的符号
    size = size.lstrip("· ").strip()
    return {
        "direction": "long",
        "entry": entry,
        "stop": stop,
        "stop_pct": STOP_PCT,
        "hit_pct": HIT_PCT,
        "trail_pct": TRAIL_PCT,
        "notional": notional,
        "loss_u": round(loss, 1),
        "loss_pct_of_capital": round(loss / _CAPITAL_U * 100.0, 1),
        "size_line": size,
        # 这条必须写进稿子：10x + 10% 止损 = 打到就本金归零，没有中间地带。
        "leverage_note": (f"{_LEVERAGE}x 下 {STOP_PCT:.0f}% 止损就是强平线 —— 打到止损等于本金归零，"
                          f"不存在「小亏一点」。这不是 bug 是杠杆的算术；能调的是仓位，不是这条线。"),
        "stage_gate": sa["label"],
        "control_gate": ca["grade"],
        "fuel_gate": fa["grade"],
    }


# ---------------- 主分析 ---------------- #

def analyze(symbol: str, market: str = "futures") -> dict:
    """妖币剧本分析主入口。返回 {ok, ...} 或 {ok: False, error}。"""
    sym = (symbol or "").strip().upper()
    if not sym:
        return {"ok": False, "error": "缺少 SYMBOL"}
    try:
        row = _row_of(sym)
    except Exception as e:
        return {"ok": False, "error": f"雷达数据不可用：{e}"}
    if not row:
        return {"ok": False, "error": (
            f"{sym} 不在妖币雷达视野内（未触发任何妖币特征、或被流动性/新币/刷量/冷却过滤层剔除）。"
            "本引擎只分析**真的异动币**；它是普通代币请改用 square-rich-post（SMC 引擎）。")}

    sa = _axis_stage(row)
    ca = _axis_control(row)
    fa = _axis_fuel(row)
    vkey, vline, vcolor = _verdict(sa, ca, fa)
    plan = _plan(sa, ca, fa, row)

    # 诚实标注：哪些轴的数据没取到
    missing = []
    if row.get("oi_chg24") is None:
        missing.append("OI 24h（确认因子未取到 → 位阶的「晚不晚」只按价格判）")
    if not (row.get("factors") or {}).get("funding"):
        missing.append("资金费率")
    missing += ca["missing"]

    return {
        "ok": True, "symbol": sym, "market": market, "engine": "monster-v1",
        "row": row,
        "stage_axis": sa, "control_axis": ca, "fuel_axis": fa,
        "verdict": vkey, "verdict_line": vline, "verdict_color": vcolor,
        "verdict_label": VERDICTS[vkey][0],
        "plan": plan,
        "exit_segments": _exit_segments(),
        "missing": missing,
        "score": row.get("score"),
        "reasons": row.get("reasons") or [],
        "radar_meta": row.get("_radar_meta") or {},
    }


# ---------------- 封面：剧本进度带 ---------------- #

def draw_monster_cover(an: dict, stat: dict, path: str) -> str:
    """封面（1280×720）：价格走势 + **剧本进度带** + 三轴卡 + 底部读数条。

    与代币封面（SMC 在 K 线上画 OB/FVG 矩形）刻意不同：这里的视觉主角是**剧本进度带**
    —— 一眼看出这只币演到第几格，比任何指标都直观。
    """
    if Image is None:
        raise RuntimeError("PIL 不可用")
    W, H = 1280, 720
    img = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(img)
    M = 24
    row = an["row"]
    sa, ca, fa = an["stage_axis"], an["control_axis"], an["fuel_axis"]
    sym = an["symbol"]
    price = _num(row.get("price"))
    chg = _num(row.get("change24_pct"))
    _draw_header(dr, W, sym, stat.get("market", "futures"), price, chg,
                 sub_right="妖币剧本引擎 · 位阶/控盘/燃料")

    # —— 价格走势（复用代币引擎的取数结果，画法另写）——
    k = stat.get("k90") or {}
    closes = k.get("closes") or []
    x0, x1, y0, y1 = M, W - M, 96, 366
    if _k_usable(k):
        lo, hi = min(closes), max(closes)
        rng = (hi - lo) or 1.0
        n = len(closes)
        pts = [(x0 + (x1 - x0) * i / max(n - 1, 1), y1 - (y1 - y0) * (c - lo) / rng)
               for i, c in enumerate(closes)]
        # 面积渐变：**先铺底再填曲线下区域**。反过来的话渐变会把面积填色整片盖掉，
        # 结果是一条线悬在空矩形上（v1.6.6 视觉验收发现）。
        _vgrad(dr, x0, y0, x1, y1, (15, 22, 31), (11, 14, 17))
        poly = pts + [(x1, y1), (x0, y1)]
        dr.polygon(poly, fill=_blend(BG, ACCENT, 0.10))
        dr.line(pts, fill=ACCENT, width=3, joint="curve")
        # 高低标注（带底衬：价格线常正好压在这两个位置）
        _chip_label(dr, x0 + 6, y0 + 4, f"90d 高 {_fmt(hi)}", SUB, 13)
        _chip_label(dr, x0 + 6, y1 - 22, f"90d 低 {_fmt(lo)}", SUB, 13)
        # 现价线
        if price:
            py = y1 - (y1 - y0) * (price - lo) / rng
            py = max(y0, min(y1, py))
            _dashed(dr, x0, py, x1, UP, width=1)
            _chip_label(dr, x1 - 148, py - 20, f"现价 {_fmt(price)}", UP, 13, bold=True)
    else:
        dr.rectangle([x0, y0, x1, y1], outline=GRID)
        dr.text((x0 + 16, y0 + 16), "K 线数据不可用（该币可能刚上线或已下架）", font=_font(15), fill=SUB)

    # —— 剧本进度带 —— #
    _draw_stage_band(dr, x0, 400, x1, 470, sa)

    # —— 三轴卡 —— #
    _draw_tri_axis(dr, x0, 478, x1, 600, sa, ca, fa)

    # —— 底部读数条 —— #
    f = row.get("factors") or {}
    chips = [
        ("位阶", sa["label"] + (f" · 第 {sa['seq'] + 1}/{sa['seq_len']} 格" if sa["seq"] is not None else " · 旁支"),
         sa["color"]),
        ("控盘", ca["grade"], ca["color"]),
        ("燃料", fa["grade"], fa["color"]),
        ("费率", (f"{_num(f.get('funding')) * 100:+.3f}%" if _num(f.get("funding")) is not None else "未测到"),
         DOWN if (_num(f.get("funding")) or 0) <= -0.0015 else TXT),
        ("OI 24h", _spct(row.get("oi_chg24"), 1), TXT),
    ]
    _draw_footer(dr, W, H, chips)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    img.save(path, "PNG")
    return path


def _draw_stage_band(dr, x0, y0, x1, y1, sa: dict):
    """剧本进度带：主剧本七格横排，当前格高亮 + 三角指针。"""
    dr.text((x0, y0 - 22), "妖币剧本 · 演到第几格", font=_font(15, True), fill=TXT)
    cur = sa["seq"]
    n = len(STAGE_SEQ)
    gap = 6
    cw = (x1 - x0 - gap * (n - 1)) / n
    for i, st in enumerate(STAGE_SEQ):
        label, color, _p, _nx = STAGE_BOOK[st]
        cx0 = x0 + i * (cw + gap)
        cx1 = cx0 + cw
        if cur is None:
            dr.rounded_rectangle([cx0, y0, cx1, y1], radius=8, fill=(20, 25, 32), outline=GRID, width=1)
            dr.text((cx0 + 10, y0 + 12), f"{i + 1}", font=_font(12), fill=SUB)
            dr.text((cx0 + 10, y0 + 34), label, font=_font(15, True), fill=SUB)
        elif i < cur:
            dr.rounded_rectangle([cx0, y0, cx1, y1], radius=8,
                                 fill=_blend(PANEL, color, 0.16), outline=_blend(GRID, color, 0.35), width=1)
            dr.text((cx0 + 10, y0 + 12), "已演过", font=_font(11), fill=SUB)
            dr.text((cx0 + 10, y0 + 34), label, font=_font(15, True), fill=SUB)
        elif i == cur:
            dr.rounded_rectangle([cx0, y0, cx1, y1], radius=8, fill=_blend(PANEL, color, 0.34),
                                 outline=color, width=2)
            # 指针
            mx = (cx0 + cx1) / 2
            dr.polygon([(mx - 9, y0 - 12), (mx + 9, y0 - 12), (mx, y0 - 1)], fill=color)
            dr.text((cx0 + 10, y0 + 12), "当前", font=_font(11, True), fill=color)
            dr.text((cx0 + 10, y0 + 34), label, font=_font(16, True), fill=TXT)
        else:
            dr.rounded_rectangle([cx0, y0, cx1, y1], radius=8, outline=GRID, width=1)
            dr.text((cx0 + 10, y0 + 12), f"{i + 1}", font=_font(11), fill=SUB)
            dr.text((cx0 + 10, y0 + 34), label, font=_font(15), fill=SUB)

    # 旁支说明
    tag = "主剧本七格"
    if cur is None:
        tag = f"旁支「{sa['label']}」不在主剧本序列内 —— 不进场的旁支"
    dr.text((x1 - dr.textlength(tag, font=_font(12)), y0 - 20), tag, font=_font(12), fill=SUB)


def _draw_tri_axis(dr, x0, y0, x1, y1, sa, ca, fa):
    """三轴卡：位阶 / 控盘 / 燃料，各一张，横向均分。"""
    gap = 12
    cw = (x1 - x0 - gap * 2) / 3
    cards = [
        ("① 位阶 STAGE", sa["label"], sa["color"], sa["why"]),
        ("② 控盘度 CONTROL", ca["grade"], ca["color"], ca["reading"]),
        ("③ 燃料 FUEL", fa["grade"], fa["color"], fa["reading"]),
    ]
    for i, (head, val, color, desc) in enumerate(cards):
        cx0 = x0 + i * (cw + gap)
        cx1 = cx0 + cw
        dr.rounded_rectangle([cx0, y0, cx1, y1], radius=10, fill=(20, 25, 32), outline=GRID, width=1)
        dr.rectangle([cx0, y0, cx0 + 4, y1], fill=color)
        dr.text((cx0 + 14, y0 + 10), head, font=_font(12), fill=SUB)
        dr.text((cx0 + 14, y0 + 28), val, font=_font(19, True), fill=color)
        for j, ln in enumerate(_wrap_clamp(dr, desc, _font(12), cw - 30, 3)):
            dr.text((cx0 + 14, y0 + 56 + j * 17), ln, font=_font(12), fill=SUB)


def draw_playbook_card(an: dict, stat: dict, path: str) -> str:
    """竖版剧本卡（1080×1080）——手机上看封面字太小，这张专门给移动端。

    内容：剧本进度带（放大）+ 三轴 + 出场四段。刻意不含 K 线 —— 讲剧本的时候
    给你看价格，注意力会被价格带走。
    """
    if Image is None:
        raise RuntimeError("PIL 不可用")
    W = H = 1080
    img = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(img)
    M = 48
    row = an["row"]
    sa, ca, fa = an["stage_axis"], an["control_axis"], an["fuel_axis"]
    sym = an["symbol"]
    price = _num(row.get("price"))
    chg = _num(row.get("change24_pct"))

    # 顶栏
    dr.text((M, 44), sym, font=_font(46, True), fill=TXT)
    tag = "币安U本位永续" if stat.get("market", "futures") == "futures" else "币安现货"
    dr.text((M, 100), f"{tag} · 妖币剧本引擎", font=_font(17), fill=SUB)
    if price and chg is not None:
        pt = f"{_fmt(price)}  {chg:+.2f}%"
        col = UP if chg >= 0 else DOWN
        dr.text((W - M - dr.textlength(pt, font=_font(30, True)), 52), pt, font=_font(30, True), fill=col)
    dr.line([M, 136, W - M, 136], fill=GRID, width=1)

    # 结论条
    dr.rounded_rectangle([M, 162, W - M, 250], radius=14,
                         fill=_blend(PANEL, an["verdict_color"], 0.18),
                         outline=an["verdict_color"], width=2)
    dr.text((M + 22, 178), f"合议结论 · {an['verdict_label']}", font=_font(26, True),
            fill=an["verdict_color"])
    for j, ln in enumerate(_wrap_clamp(dr, an["verdict_line"], _font(16), W - M * 2 - 44, 2)):
        dr.text((M + 22, 212 + j * 22), ln, font=_font(16), fill=TXT)

    # 剧本进度带
    _draw_stage_band(dr, M, 322, W - M, 420, sa)
    # 这一格在演什么 / 下一格
    yy = 448
    dr.text((M, yy), "这一格在演什么", font=_font(14), fill=SUB)
    for j, ln in enumerate(_wrap_clamp(dr, sa["playing"], _font(17), W - M * 2, 2)):
        dr.text((M, yy + 22 + j * 24), ln, font=_font(17), fill=TXT)
    yy += 88
    dr.text((M, yy), "下一格", font=_font(14), fill=SUB)
    for j, ln in enumerate(_wrap_clamp(dr, sa["next"], _font(17), W - M * 2, 2)):
        dr.text((M, yy + 22 + j * 24), ln, font=_font(17), fill=TXT)

    # 三轴（位阶已在进度带里给过，这里补控盘与燃料两张）
    yy = 630
    dr.text((M, yy - 26), "控盘度 与 燃料", font=_font(15, True), fill=TXT)
    for j, (head, val, color, desc) in enumerate([
        ("② 控盘度", ca["grade"], ca["color"], ca["reading"]),
        ("③ 燃料", fa["grade"], fa["color"], fa["reading"]),
    ]):
        cy = yy + j * 104
        dr.rounded_rectangle([M, cy, W - M, cy + 92], radius=10, fill=(20, 25, 32),
                             outline=GRID, width=1)
        dr.rectangle([M, cy, M + 4, cy + 92], fill=color)
        dr.text((M + 18, cy + 14), head, font=_font(13), fill=SUB)
        dr.text((M + 160, cy + 8), val, font=_font(23, True), fill=color)
        for k, ln in enumerate(_wrap_clamp(dr, desc, _font(14), W - M * 2 - 180, 2)):
            dr.text((M + 18, cy + 46 + k * 20), ln, font=_font(14), fill=SUB)

    # 出场四段：四列卡片（名称 + 数值）。挤成一行 dot-separated 读起来像免责声明，
    # 而这四条是**真正要照着执行**的东西，值得单独给格。
    yy = 866
    dr.text((M, yy - 26), "出场四段（与 radar_tracker 同口径）", font=_font(15, True), fill=TXT)
    segs = an["exit_segments"]
    gap = 12
    cw = (W - M * 2 - gap * 3) / 4
    for i, (name, val, _desc) in enumerate(segs[:4]):
        cx0 = M + i * (cw + gap)
        dr.rounded_rectangle([cx0, yy, cx0 + cw, yy + 112], radius=10,
                             fill=(20, 25, 32), outline=GRID, width=1)
        dr.rectangle([cx0, yy, cx0 + cw, yy + 3], fill=ACCENT)
        dr.text((cx0 + 12, yy + 16), name, font=_font(13), fill=SUB)
        for k, ln in enumerate(_wrap_clamp(dr, val, _font(16, True), cw - 24, 3)):
            dr.text((cx0 + 12, yy + 42 + k * 21), ln, font=_font(16, True), fill=TXT)

    dr.text((M, H - 42), "剧本位阶与控盘指纹由 Monster Radar 同源数据生成 · 非投资建议",
            font=_font(12), fill=SUB)
    img.save(path, "PNG")
    return path


# ---------------- 组稿四风格（与代币引擎的 review/diary/qa/blunt 不重名） ---------------- #

STYLES = ("playbook", "hunt", "warn", "plain")
STYLE_LABELS = {
    "playbook": "剧本拆解体",
    "hunt": "埋伏笔记体",
    "warn": "别接盘体",
    "plain": "说人话体",
}


def _facts(an: dict) -> dict:
    row = an["row"]
    sa, ca, fa = an["stage_axis"], an["control_axis"], an["fuel_axis"]
    base = an["symbol"].replace("USDT", "") or an["symbol"]
    return {
        "sym": an["symbol"], "base": base,
        "price": _num(row.get("price")),
        "chg24": _num(row.get("change24_pct")),
        "chg1h": _num(row.get("change1h_pct")),
        "chg3d": _num(row.get("change3d_pct")),
        "chg30d": _num(row.get("change30d_pct")),
        "pos": sa.get("pos_pct"),
        "vol_ratio": _num(row.get("vol_ratio")),
        "qv": _num(row.get("quote_volume")),
        "score": row.get("score"),
        "stage": sa["stage"], "stage_label": sa["label"], "seq": sa["seq"],
        "seq_len": sa["seq_len"], "playing": sa["playing"], "next": sa["next"],
        "allow": sa["allow"], "late": sa["late"], "late_why": sa["late_why"],
        "warm": sa["warm"],
        "ctrl_grade": ca["grade"], "ctrl_flags": ca["flags"], "ctrl_reading": ca["reading"],
        "fuel_grade": fa["grade"], "fuel_items": fa["items"], "fuel_reading": fa["reading"],
        "verdict": an["verdict"], "verdict_label": an["verdict_label"],
        "verdict_line": an["verdict_line"],
        "plan": an["plan"], "exit": an["exit_segments"], "reasons": an["reasons"],
        "missing": an["missing"],
    }


def _tags(base: str) -> list:
    """$cashtag + 固定话题。话题只用与「妖币/合约」相关的通用词，**不硬编项目背景**。"""
    return [f"${base}", "#妖币雷达", "#合约交易", "#风险管理"]


def _footer_lines() -> list:
    return [
        "",
        "—",
        "本内容由 BAZZ.AGENT 的妖币剧本引擎生成（位阶/控盘/燃料三轴），数据取自行情接口，非投资建议。",
        "项目开源：github.com/xinyuzjj/bazz.agent",
        "妖币是控盘盘，剧本随时可能反手；任何位置都不值得重仓。",
    ]


def _stage_line(f: dict) -> str:
    L = f["stage_label"]
    if f["seq"] is not None:
        return f"{L}（主剧本第 {f['seq'] + 1}/{f['seq_len']} 格）"
    return f"{L}（旁支，不在主剧本序列内）"


def _verdict_block(f: dict) -> list:
    """结论段：动作 + 理由。所有风格共用，保证同一份数据在不同风格下结论一致。"""
    out = [f"**结论：{f['verdict_label']}** —— {f['verdict_line']}。"]
    if f["allow"] != "YES":
        for w in (f["late_why"] or []):
            out.append(f"· {w}")
    return out


def _plan_block(f: dict) -> list:
    """操作计划段。plan 为 None 时**不给点位**（绝不硬造方向）。"""
    if not f["plan"]:
        return ["", "**操作计划：本次不给点位。**",
                "位阶未落在允许进场的窗口里 —— 没有计划比给一个勉强的计划更负责。"]
    p = f["plan"]
    return ["", "**操作计划（若要参与）**",
            "· 方向：只做多（本框架绝不做空）",
            f"· 入场：{_fmt(p['entry'])}（{f['stage_label']}阶段现价，不追高）",
            f"· 止损：{_fmt(p['stop'])}（固定 −{p['stop_pct']:.0f}%）",
            f"· {p['size_line']}",
            f"· ⚠️ {p['leverage_note']}",
            f"· 达标减半：+{p['hit_pct']:.0f}% → 减一半；其余交给自极值回撤 {p['trail_pct']:.0f}% 的移动止盈。"]


def _fuel_block(f: dict) -> list:
    if not f["fuel_items"]:
        return ["燃料侧没有算出明确读数。"]
    return [f"· {lab}：{val} —— {desc}" for lab, val, _sc, desc in f["fuel_items"]]


def _axis_block(f: dict) -> list:
    out = []
    out.append(f"**① 位阶**：{_stage_line(f)}")
    out.append(f"这一格在演：{f['playing']}")
    out.append(f"下一格：{f['next']}")
    out.append("")
    out.append(f"**② 控盘度**：{f['ctrl_grade']}")
    if f["ctrl_flags"]:
        out.append(f"命中的控盘指纹：{'、'.join(f['ctrl_flags'])}")
    out.append(f["ctrl_reading"])
    out.append("")
    out.append(f"**③ 燃料**：{f['fuel_grade']}")
    out += _fuel_block(f)
    return out


def _style_playbook(f: dict) -> tuple:
    """剧本拆解体：像拆一集剧一样讲清楚庄家在干什么。"""
    sym, base = f["sym"], f["base"]
    title = f"{base} 现在演到剧本第几格：{f['stage_label']}"
    if f["seq"] is not None:
        title = f"{base} 演到「{f['stage_label']}」——主剧本第 {f['seq'] + 1}/{f['seq_len']} 格"
    lines = [
        f"{sym} 现价 {_fmt(f['price'])}，24h {_spct(f['chg24'])}。",
        "",
        "妖币不能当普通币看。普通币的价格是很多人一起买出来的，所以看结构、看订单块有用；"
        "妖币的价格是**一个人（或一伙人）按剧本演出来的**，你看到的每一个「支撑」都可能是画给你看的。"
        "所以这篇不聊指标，只聊三件事：演到第几格、盘在谁手里、往上推的油从哪来。",
        "",
    ]
    lines += _axis_block(f)
    lines += [""] + _verdict_block(f)
    lines += _plan_block(f)
    lines += ["", "**出场四条线（别问理由，到线就走）**"]
    for name, val, desc in f["exit"]:
        lines.append(f"· {name} {val}：{desc}")
    lines += _footer_lines()
    return title, lines


def _style_hunt(f: dict) -> tuple:
    """埋伏笔记体：第一人称，像猎人记等待日志。"""
    sym, base = f["sym"], f["base"]
    title = f"{base} 埋伏笔记：剧本在「{f['stage_label']}」，我等不等"
    if f["allow"] != "YES":
        title = f"{base} 埋伏笔记：这一格我不进（{f['stage_label']}）"
    lines = [
        f"记一笔 {sym}，现价 {_fmt(f['price'])}，24h {_spct(f['chg24'])}。",
        "",
    ]
    if f["allow"] == "YES":
        lines.append("这只在我等的那一格里。")
        if f["warm"]:
            lines.append("注意它已经温起来了（涨幅进了 3~10% 区间）—— 温了不挡，但分数会被降，"
                         "意思是我排到冷启动后面去等，而不是抢。")
    else:
        lines.append("这只不在我等的那一格里。写下来不是为了参与，是为了记住它当时的形态 —— "
                     "妖币的形态是会重演的。")
    lines += [""] + _axis_block(f)
    lines += [""] + _verdict_block(f)
    lines += _plan_block(f)
    lines += [
        "",
        "**如果我进去了，我打算怎么出来**",
        "妖币最爽和最难受的是同一件事：它涨起来的时候你舍不得走，"
        "等你想走的时候没有买盘。所以这四条是我进之前就写好的，不是等亏了再想：",
    ]
    for name, val, desc in f["exit"]:
        lines.append(f"· {name} {val}：{desc}")
    lines += _footer_lines()
    return title, lines


def _style_warn(f: dict) -> tuple:
    """别接盘体：站在「你可能正要去追」的位置上劝。"""
    sym, base = f["sym"], f["base"]
    title = f"{base} 涨到这里，先看清是谁在推"
    lines = [
        f"{sym} 现价 {_fmt(f['price'])}，24h {_spct(f['chg24'])}，30d {_spct(f['chg30d'])}。",
        "",
        "看到这种涨幅，第一反应不该是「还能不能追」，而是「我在这个剧本里是什么角色」。"
        "妖币的收益从来不是分给散户的 —— 它是从对手盘身上收上来的。",
        "",
        f"**你先看它在哪一格**：{_stage_line(f)}。",
        f"这一格在演：{f['playing']}",
        f"再往后是：{f['next']}",
        "",
        f"**再看这个盘攥在谁手里**：{f['ctrl_grade']}。{f['ctrl_reading']}。",
    ]
    if f["ctrl_flags"]:
        lines.append(f"命中的指纹：{'、'.join(f['ctrl_flags'])}。")
    lines += ["", f"**最后看油从哪来**：{f['fuel_grade']}。{f['fuel_reading']}。"]
    lines += _fuel_block(f)
    lines += [""] + _verdict_block(f)
    lines += [
        "",
        f"**最容易亏的一种做法**：看到 {_spct(f['chg24'])} 觉得「涨这么多了该回调了吧」，然后去做空。",
        "安装版的真实战绩：3 笔做空全部被轧空击穿 10% 强平线，最大有利分别是 0.0% / 0.0% / 1.4% —— "
        "**从一开始就没跌过**。妖币的顶经常是二次拉升前的换手，做空你以为在赌回调，"
        "其实是在给庄家当接盘的人。",
    ]
    lines += _plan_block(f)
    lines += ["", "**不管参不参与，这四条线先记下来**"]
    for name, val, desc in f["exit"]:
        lines.append(f"· {name} {val}：{desc}")
    lines += _footer_lines()
    return title, lines


def _style_plain(f: dict) -> tuple:
    """说人话体：极简，给只看结论的人。"""
    sym, base = f["sym"], f["base"]
    title = f"{base}：{f['verdict_label']}"
    lines = [
        f"现价 {_fmt(f['price'])}，24h {_spct(f['chg24'])}。",
        "",
        f"结论：{f['verdict_label']}。{f['verdict_line']}。",
        "",
        f"它在剧本的「{f['stage_label']}」这一格"
        + (f"（第 {f['seq'] + 1}/{f['seq_len']} 格）" if f["seq"] is not None else "（旁支）")
        + f"，盘面控盘度 {f['ctrl_grade']}，燃料 {f['fuel_grade']}。",
        "",
        f"这一格的意思是：{f['playing']}。",
        f"再往下：{f['next']}。",
    ]
    if f["allow"] == "YES":
        lines += ["", "要参与的话（只做多）："]
        lines += _plan_block(f)[1:]
    else:
        lines += ["", "本次不给点位 —— 不在这只币上勉强找机会。"]
    lines += ["", "出场："
              + " / ".join(f"{a} {b}" for a, b, _c in f["exit"]) + "。"]
    lines += _footer_lines()
    return title, lines


_RENDER = {
    "playbook": _style_playbook,
    "hunt": _style_hunt,
    "warn": _style_warn,
    "plain": _style_plain,
}


def _pick_style(style: str = None) -> str:
    if style in STYLES:
        return style
    return random.choice(STYLES)


def _article_full(an: dict, style: str = None) -> dict:
    f = _facts(an)
    st = _pick_style(style)
    title, lines = _RENDER[st](f)
    return {"title": title, "body": "\n".join(lines), "tags": _tags(f["base"]),
            "style": st, "style_label": STYLE_LABELS[st]}


# ---------------- 入口 ---------------- #

def compose(symbol: str, market: str = "futures", style: str = None) -> dict:
    """合成妖币广场发文素材。返回结构与 square_rich.compose 保持一致，
    这样 square-post 的发布链路（--cover/--title-file/--text-file）不用改一行。
    """
    if Image is None:
        return {"ok": False, "error": "PIL 不可用，无法生成图表"}
    an = analyze(symbol, market)
    if not an.get("ok"):
        return an
    sym = an["symbol"]
    stat = _collect(sym, market)
    if not _k_usable(stat.get("k90")):
        return {"ok": False, "error": f"{sym} 行情 K 线不可用（该币可能刚上线或已下架）"}

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(OUT_ROOT, f"{sym}_{ts}")
    os.makedirs(out_dir, exist_ok=True)

    cover = draw_monster_cover(an, stat, os.path.join(out_dir, "cover.png"))
    try:
        playbook_card = draw_playbook_card(an, stat, os.path.join(out_dir, "chart_playbook.png"))
    except Exception:
        playbook_card = ""

    art = _article_full(an, style)
    title_f = os.path.join(out_dir, "title.txt")
    text_f = os.path.join(out_dir, "article.txt")
    meta_f = os.path.join(out_dir, "meta.json")
    with open(title_f, "w", encoding="utf-8") as fh:
        fh.write(art["title"] + "\n")
    with open(text_f, "w", encoding="utf-8") as fh:
        fh.write(art["body"] + "\n")

    sa, ca, fa = an["stage_axis"], an["control_axis"], an["fuel_axis"]
    stats = {
        "symbol": sym, "market": stat.get("market", market),
        "price": _num(an["row"].get("price")), "chg24": _num(an["row"].get("change24_pct")),
        "stage": sa["stage"], "stage_label": sa["label"], "stage_seq": sa["seq"],
        "verdict": an["verdict"], "verdict_label": an["verdict_label"],
        "control": ca["grade"], "control_flags": ca["flags"],
        "fuel": fa["grade"], "fuel_score": fa["score"],
        "late": sa["late"], "warm": sa["warm"], "score": an["score"],
        "missing": an["missing"],
    }
    # 模拟挂单：妖币引擎给出的 plan 与代币引擎同结构，发布成功后的建单钩子可以直接读。
    # 不给点位时写 null（绝不硬造方向）。
    plan = None
    if an["plan"]:
        p = an["plan"]
        plan = {"direction": "long", "entry": p["entry"], "zone_lo": None, "zone_hi": None,
                "stop": p["stop"], "tp": round(p["entry"] * (1 + p["hit_pct"] / 100.0), 10),
                "price": p["entry"], "engine": "monster", "stop_pct": p["stop_pct"]}
    with open(meta_f, "w", encoding="utf-8") as fh:
        json.dump({"symbol": sym, "market": market, "ts": int(time.time()),
                   "engine": "monster", "stats": stats, "tags": art["tags"],
                   "title": art["title"], "style": art["style"],
                   "style_label": art["style_label"], "plan": plan,
                   "axes": {"stage": sa["stage"], "stage_label": sa["label"],
                            "control": ca["grade"], "control_flags": ca["flags"],
                            "fuel": fa["grade"], "fuel_score": fa["score"]},
                   "verdict": an["verdict"], "verdict_label": an["verdict_label"],
                   "exit_segments": [{"name": a, "value": b, "why": c}
                                     for a, b, c in an["exit_segments"]],
                   "missing": an["missing"]},
                  fh, ensure_ascii=False, indent=1)

    return {"ok": True, "dir": out_dir, "title_file": title_f, "text_file": text_f,
            "cover": cover, "extra_chart": playbook_card, "chart_4h": "",
            "tags": art["tags"], "stats": stats, "plan": plan,
            "style": art["style"], "style_label": art["style_label"],
            "engine": "monster",
            "verdict": an["verdict"], "verdict_label": an["verdict_label"]}
