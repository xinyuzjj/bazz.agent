"""广场富媒体发文合成引擎（square-rich-post 技能的后端部分）。

流程：进程内调 scanner 全维度取数（90d OHLCV / 24h 收盘 / 费率 / OI / 多空比 / 恐惧贪婪）
→ Pillow 画深色封面图（90d K线+成交量+关键位标注）与 24h 分时图（零新增依赖：
PyInstaller 已打包 PIL，不引 matplotlib，包体积零增量）
→ 组稿（标题 + 分区正文 + $cashtag/#hashtag，事实与推测分栏）
→ 落盘 workspace/square_rich/<SYM>_<ts>/（title.txt / article.txt / cover.png / chart_24h.png / meta.json）。

发文本身交给 square-post 技能（--cover --title-file --text-file），本模块只合成不发布。
"""
import json
import os
import random
import re
import time

import workspace

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # 极端缺失时不拖死后端启动
    Image = ImageDraw = ImageFont = None

OUT_ROOT = os.path.join(workspace.WORKSPACE, "square_rich")

# —— 币安深色主题 —— #
BG = (11, 14, 17)          # #0b0e11
PANEL = (16, 20, 25)
GRID = (38, 44, 52)
TXT = (233, 237, 241)
SUB = (150, 160, 176)
UP = (14, 203, 129)        # 涨绿（币安 #0ECB81，与前端 Sparkline 涨绿一致）
DOWN = (246, 70, 93)       # 跌红 #F6465D
ACCENT = (240, 185, 11)    # 币安黄

# 分类 hashtag（命中才加，未命中不加，绝不硬编项目背景）
_CHAIN_TAG = {
    "RAY": "Solana", "SOL": "Solana", "JUP": "Solana", "WIF": "Solana", "BONK": "Solana",
    "BNB": "BNB", "CAKE": "BNB", "PEPE": "Meme", "DOGE": "Meme", "SHIB": "Meme",
    "WLD": "AI", "FET": "AI", "RENDER": "AI", "TAO": "AI",
}

_FONT_CACHE = {}


def _font(size: int, bold: bool = False):
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    f = None
    win = os.environ.get("WINDIR", r"C:\Windows")
    for name in (("msyhbd.ttc", "msyh.ttc", "simhei.ttf") if bold
                 else ("msyh.ttc", "msyhbd.ttc", "simhei.ttf")):
        p = os.path.join(win, "Fonts", name)
        if os.path.isfile(p):
            try:
                f = ImageFont.truetype(p, size)
                break
            except Exception:
                continue
    if f is None:
        f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def _fmt(v, nd=4):
    """价格格式：小数位随量级自适应（>100→2位，>1→3-4位，<1→5-6位）。"""
    if v is None:
        return "-"
    v = float(v)
    if v >= 100:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:,.3f}"
    if v >= 0.01:
        return f"{v:.4f}".rstrip("0").rstrip(".") or "0"
    return f"{v:.10f}".rstrip("0") or "0"


def _fmt_usd(v):
    if not v:
        return "-"
    v = float(v)
    for div, suf in ((1e9, "亿"), (1e8, "千万"), (1e6, "百万"), (1e3, "K")):
        if v >= div:
            # 中文习惯：亿/万
            if v >= 1e8:
                return f"{v/1e8:.2f} 亿"
            if v >= 1e4:
                return f"{v/1e4:.1f} 万"
    return f"{v:,.0f}"


def _pct(a, b):
    if not a or not b:
        return None
    return (float(a) - float(b)) / float(b) * 100.0


# ---------------- 取数 ----------------

def _flat(k: dict) -> bool:
    """合约被 SETTLING/下架等状态时，上游会返回全等价格、0 量的占位平线——90/24 根全等在真实市场不可能。"""
    c = (k or {}).get("closes") or []
    return len(c) >= 2 and max(c) == min(c)


def _k_usable(k: dict) -> bool:
    """出图最低要求：≥5 根且非占位平线。"""
    c = (k or {}).get("closes") or []
    return len(c) >= 5 and not _flat(k)


def _collect(sym: str, market: str) -> dict:
    from scanner import klines_ohlcv, klines_closes, fear_greed_index, futures_open_interest
    d = {"symbol": sym, "market": market}
    d["k90"] = klines_ohlcv(sym, "1d", 90, market)
    d["k4h"] = klines_ohlcv(sym, "4h", 84, market)   # SMC 主分析周期：4 小时
    d["c24"] = klines_closes(sym, "1h", 25, market)
    if market == "futures" and (_flat(d["k90"]) or _flat({"closes": d["c24"]})):
        # 合约盘占位脏数据（如 RAYUSDT SETTLING）：回退现货真实行情，衍生品维度自动省略
        d["k90"] = klines_ohlcv(sym, "1d", 90, "spot")
        d["k4h"] = klines_ohlcv(sym, "4h", 84, "spot")
        d["c24"] = klines_closes(sym, "1h", 25, "spot")
        d["market"] = "spot"
        d["fallback"] = "futures_flat→spot"
    # v1.5.48（用户实测：SNDKUSDT 传 spot 全链路 400 → 报「无 K 线数据」）：
    # 代币化股票等标的不在现货市场（spot /api/v3/klines 返回 400），但 fapi 有完整永续
    # K 线。请求市场 1d 与 4h 都不可用、而另一市场 1d 可用时 → 整体切换市场再继续走
    # 周期降级链；反向（futures 平线→spot）已由上面分支处理。
    other = "spot" if d["market"] == "futures" else "futures"
    if not _k_usable(d["k90"]) and not _k_usable(d["k4h"]):
        k90o = klines_ohlcv(sym, "1d", 90, other)
        if _k_usable(k90o):
            d["k90"] = k90o
            d["k4h"] = klines_ohlcv(sym, "4h", 84, other)
            d["c24"] = klines_closes(sym, "1h", 25, other)
            d["market"] = other
            d["fallback"] = (d.get("fallback") + "+" if d.get("fallback") else "") + f"market→{other}"
    # v1.5.45（用户实测：90 日 K 线数据不足出稿失败）：1d 不可用（新上市不足 90d /
    # 上游失败 / 平线占位）→ 4h×540（≈90 日）→ 1h×720（≈30 日），保证出稿；
    # 降级后 k90_label 标注实际周期，图上的「90d 高/低」「90日区间」等文案跟着换。
    d["k90_label"] = "90日"
    if not _k_usable(d["k90"]):
        for itv, n, label in (("4h", 540, "近90日·4h"), ("1h", 720, "近30日·1h")):
            kk = klines_ohlcv(sym, itv, n, d["market"])
            if _k_usable(kk):
                d["k90"] = kk
                d["k90_label"] = label
                d.setdefault("fallback", "")
                d["fallback"] = (d["fallback"] + "+" if d.get("fallback") else "") + f"k90={itv}×{n}"
                break
    try:
        fng = fear_greed_index()
        d["fng"] = {"value": fng.get("value"), "classification": fng.get("classification")}
    except Exception:
        d["fng"] = {"value": None, "classification": None}
    if d["market"] == "futures":
        try:
            from scanner import futures_snapshot, get_longshort_board
            row = next((r for r in futures_snapshot() if r.get("symbol") == sym), None)
            if row:
                d["funding_rate"] = row.get("funding_rate")
                d["quote_volume"] = row.get("quote_volume")
                d["change_pct"] = row.get("change_pct")
        except Exception:
            pass
        try:
            l = next((r for r in get_longshort_board().get("rows", []) if r.get("symbol") == sym), None)
            if l:
                d["top_ratio"] = l.get("top_ratio")
                d["global_ratio"] = l.get("global_ratio")
                d["divergence"] = l.get("divergence")
        except Exception:
            pass
        try:
            it = futures_open_interest([sym])
            if it:
                d["oi"] = it[0].get("notional")
                d["oi_base"] = it[0].get("oi")
        except Exception:
            pass
    return d


# ---------------- 图表（Pillow） ----------------

def _xaxis_label(ts_ms: int) -> str:
    try:
        return time.strftime("%m-%d", time.localtime(ts_ms / 1000))
    except Exception:
        return ""


def _blend(c1, c2, t):
    """颜色插值：t=0 → c1，t=1 → c2。"""
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(c1, c2))


def _vgrad(dr, x0, y0, x1, y1, top, bottom):
    """纵向渐变填充（逐行画，零依赖）。"""
    h = max(int(y1 - y0), 1)
    for i in range(h):
        dr.line([x0, y0 + i, x1, y0 + i], fill=_blend(top, bottom, i / h))


def _dashed(dr, x0, y, x1, fill, width=1, dash=7, gap=6):
    """水平虚线。"""
    x = x0
    while x < x1:
        dr.line([x, y, min(x + dash, x1), y], fill=fill, width=width)
        x += dash + gap


def _dashed_rect(dr, x0, y0, x1, y1, fill, width=1, dash=6, gap=5):
    """虚线矩形框（四条边都点线，用于「潜在/条件成立」的结构区）。"""
    # 上下水平边
    _dashed(dr, x0, y0, x1, fill, width, dash, gap)
    _dashed(dr, x0, y1, x1, fill, width, dash, gap)
    # 左右垂直边（竖直方向逐段点线）
    def _v(x):
        yy = y0
        while yy < y1:
            dr.line([x, yy, x, min(yy + dash, y1)], fill=fill, width=width)
            yy += dash + gap
    _v(x0)
    _v(x1)


def _draw_header(dr, W, sym, market, price, chg, sub_right=""):
    dr.rectangle([0, 0, W, 84], fill=PANEL)
    dr.rectangle([0, 0, W, 3], fill=ACCENT)   # 顶部金色描边
    dr.text((20, 14), f"{sym}", font=_font(30, True), fill=TXT)
    tag = "币安U本位永续" if market == "futures" else "币安现货"
    bw = dr.textlength(tag, font=_font(15))
    dr.rounded_rectangle([20 + dr.textlength(sym, font=_font(30, True)) + 14, 22,
                          20 + dr.textlength(sym, font=_font(30, True)) + 14 + bw + 18, 50],
                         radius=6, outline=SUB, width=1)
    dr.text((20 + dr.textlength(sym, font=_font(30, True)) + 23, 27), tag, font=_font(15), fill=SUB)
    if chg is not None and price is not None:
        color = UP if chg >= 0 else DOWN
        pt = f"{_fmt(price)} USDT"
        ct = f"{chg:+.2f}%"
        pw = dr.textlength(pt, font=_font(26, True))
        dr.text((W - 24 - dr.textlength(ct, font=_font(20, True)) - pw - 10, 18), pt,
                font=_font(26, True), fill=TXT)
        dr.text((W - 24 - dr.textlength(ct, font=_font(20, True)), 24), ct, font=_font(20, True), fill=color)
    if sub_right:
        dr.text((W - 24 - dr.textlength(sub_right, font=_font(13)), 62), sub_right,
                font=_font(13), fill=SUB)


def _draw_footer(dr, W, H, chips):
    """底部信息卡：[(label, value, color), ...] 圆角卡片均分排布。"""
    y = H - 92
    dr.rectangle([0, y - 16, W, H], fill=PANEL)
    n = len(chips)
    seg = W / max(n, 1)
    for i, (lab, val, color) in enumerate(chips):
        x = seg * i + 12
        dr.rounded_rectangle([x, y - 2, x + seg - 24, y + 58], radius=10,
                             fill=(23, 28, 35), outline=GRID, width=1)
        dr.text((x + 14, y + 8), lab, font=_font(13), fill=SUB)
        dr.text((x + 14, y + 31), val, font=_font(17, True), fill=color or TXT)


def draw_cover(stat: dict, path: str, kkey: str = "k90", smc_zones: bool = False) -> str:
    """主图 1280x720：蜡烛 + 成交量 + 关键位标注 + 底部信息条。

    kkey="k90"（默认）= 日线封面；kkey="k4h" + smc_zones=True = 4h 结构图
    （叠加 OB/FVG/OTE 矩形区间框）。日线封面不标 SMC（用户指定：日线不标，
    另出一张 4h 图标记）。"""
    W, H = 1280, 720
    k = stat.get(kkey) or {}
    o, h, l, c, v, t = (k.get("opens") or [], k.get("highs") or [], k.get("lows") or [],
                        k.get("closes") or [], k.get("vols") or [], k.get("times") or [])
    if len(c) < 5:
        raise RuntimeError("K 线数据不足（1d/4h/1h 均无可用数据），无法出图")
    # v1.5.45：降级周期时图上「90d 高/低」「90日区间」等文案跟随实际周期
    lbl = (stat.get("k90_label") or "90日") if kkey == "k90" \
        else f"近{max(1, round(len(c) * 4 / 24))}日·4h"   # 4h 图标签按实际根数动态算
    img = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(img)
    _vgrad(dr, 0, 0, W, H, (15, 20, 27), BG)   # 主区微渐变，避免死黑底

    price = c[-1]
    chg = stat.get("change_pct")
    if chg is None and len(c) > 1:
        chg = _pct(price, c[-2])
    _draw_header(dr, W, stat["symbol"], stat["market"], price, chg,
                 sub_right=time.strftime("%Y-%m-%d %H:%M %Z", time.localtime()).replace(" +0800", " UTC+8"))

    # —— 蜡烛区（主图放大） —— #
    x0, x1 = 16, W - 96          # 右侧留价格轴
    y0, y1 = 104, H - 236
    lo, hi = min(l), max(h)
    pad = (hi - lo) * 0.06 or hi * 0.01
    lo_p, hi_p = lo - pad, hi + pad
    n = len(c)
    step = (x1 - x0) / n
    bw = max(2, int(step * 0.62))

    def py(p):
        return y1 - (p - lo_p) / (hi_p - lo_p) * (y1 - y0)

    # 横向虚线网格 + 价格轴；日期锚点处补竖向淡网格
    date_idx = [i for i in (0, n // 3, 2 * n // 3, n - 1) if 0 <= i < len(t)]
    for i in range(5):
        gy = y0 + (y1 - y0) * i / 4
        _dashed(dr, x0, gy, x1, GRID)
        gp = hi_p - (hi_p - lo_p) * i / 4
        dr.text((x1 + 10, gy - 8), _fmt(gp), font=_font(13), fill=SUB)
    for idx in date_idx:
        vx = x0 + step * idx + step / 2
        dr.line([vx, y0, vx, y1], fill=_blend(GRID, BG, 0.45), width=1)

    # —— SMC 区域标记（OB / FVG / OTE 矩形框，仅 4h 结构图，画在蜡烛下层） —— #
    # 框只画在结构出现的那段 K 上，不横跨整屏（用户指定）
    def _band(blo, bhi, col, lab, alpha=0.10, bx0=None, bx1=None, dashed=False):
        by0, by1 = py(bhi), py(blo)
        if by1 - by0 < 10:          # 最小可视高度：窄区间也要能看出是「框」
            by1 = by0 + 10
        by0, by1 = max(by0, y0), min(by1, y1)
        if by1 <= by0:
            return
        bx0 = x0 if bx0 is None else max(bx0, x0)
        bx1 = x1 if bx1 is None else min(bx1, x1)
        if bx1 - bx0 < 3:
            return
        if dashed:                  # 潜在区：虚线框，底填充更淡
            _dashed_rect(dr, bx0, by0, bx1, by1, _blend(col, TXT, 0.45), width=1)
        else:
            dr.rectangle([bx0, by0, bx1, by1], fill=_blend(BG, col, alpha),
                         outline=_blend(col, TXT, 0.2), width=1)
        # 文字统一放右侧边栏（用户定版：不和 K 线混在一起），标签跟区间 y 居中
        fnt = _font(11, True)
        tw = dr.textlength(lab, font=fnt)
        ty = min(max((by0 + by1) / 2 - 8, y0), y1 - 16)
        # 避让右侧现价金胶囊（胶囊占 py(price)±10）：向「远离现价」的方向挪，
        # 挪完重新夹紧（旧写法朝现价方向挪，标签正好被胶囊盖住）
        pty = py(price)
        if abs(ty + 8 - pty) < 22:
            ty = pty + 14 if ty + 8 >= pty else pty - 30
            ty = min(max(ty, y0), y1 - 16)
        dr.rounded_rectangle([x1 + 6, ty, x1 + 8 + tw, ty + 16], radius=3,
                             fill=_blend(BG, col, 0.18),
                             outline=_blend(col, TXT, 0.35), width=1)
        dr.text((x1 + 7, ty + 2), lab, font=fnt, fill=_blend(col, TXT, 0.5))

    k4 = stat.get("k4h") or {}
    if smc_zones and _k_usable(k4):
        def _kx(i):                  # 第 i 根 K 的 x 范围
            cx = x0 + step * i + step / 2
            return max(x0, cx - step / 2 - 1), min(x1, cx + step / 2 + 1)

        # 大趋势优先（用户指定：先判断大趋势，再看趋势内部的情况）：
        # 用日线结构定方向，主画「顺大趋势」的 OB / FVG / OTE；日线方向不明才两者都画。
        # 反向 CHoCH OB 成立前提（用户定版 v1.5.58）：只有当价格实体收盘跌破了
        # 结构最低点（空头OB）／实体收盘升破结构最高点（多头OB）才成立——
        # 回调途中逐个跌破小低点形成的反向 OB 不算，不画。
        trend = _htf_trend(stat).get("trend") or "mixed"
        smc_ov = _smc(stat)
        opp = {"bullish": "bearish", "bearish": "bullish"}.get(trend)
        # 标签带级别（用户定版：分清大趋势/小趋势）——顺日线大趋势的标「顺势」，
        # 反向小结构标「CHoCH」；日线方向不明时两侧都只是 4h 级别结构，标「4h」
        ob_dirs = {"bullish": [("bullish", UP, "顺势 多头OB")],
                   "bearish": [("bearish", DOWN, "顺势 空头OB")]}.get(
            trend, [("bullish", UP, "4h 多头OB"), ("bearish", DOWN, "4h 空头OB")])
        closes4 = k4.get("closes") or []
        # 反向 OB 分两态（用户定版 v1.5.58）：
        #   · 已实体破结构最低/最高点 → 成立，实线框标「CHoCH 空头OB / CHoCH 多头OB」；
        #   · 尚未破结构最低/最高点 → 潜在做空/做多点，虚线框标「跌破后的空头ob位 / 升破后的多头ob位」，
        #     供价格若跌破后参考（用户指定：也要显示，作跌破后的做空点）
        rev_info = None
        if opp and _find_ob(k4, opp):
            if opp == "bearish":
                ext4 = min(k4.get("lows") or [0])          # 4h 结构最低点
                broke4 = any(cl < ext4 for cl in closes4)  # 实体收盘跌破最低点
            else:
                ext4 = max(k4.get("highs") or [0])         # 4h 结构最高点
                broke4 = any(cl > ext4 for cl in closes4)  # 实体收盘升破最高点
            if broke4:
                ob_dirs.append((opp, DOWN if opp == "bearish" else UP,
                                f"CHoCH {'空头' if opp == 'bearish' else '多头'}OB"))
            else:
                rev_info = (opp, DOWN if opp == "bearish" else UP,
                            f"{'跌破后' if opp == 'bearish' else '升破后'}的"
                            f"{'空头' if opp == 'bearish' else '多头'}ob位", True)
        zones = []
        for d, col, lab in ob_dirs:
            ob = _find_ob(k4, d)
            if ob and ob.get("i") is not None:
                zones.append([ob["low"], ob["high"], col, lab, _kx(ob["i"])[0], False])
        # 反向未成立的潜在 OB：也画（虚线框），且不与同向 OB 重叠（保住顺趋势主区）
        if rev_info:
            ob = _find_ob(k4, rev_info[0])
            if ob and ob.get("i") is not None:
                zones.append([ob["low"], ob["high"], rev_info[1], rev_info[2],
                              _kx(ob["i"])[0], True])
        # 同向 OB 价格区间相邻/重叠时只留更贴近现价的一个（异向并存不强去重）
        if len(zones) == 2 and zones[0][2] == zones[1][2]:
            (a_lo, a_hi, _, _, _, _), (b_lo, b_hi, _, _, _, _) = zones
            tol = price * 0.005
            if a_lo - tol <= b_hi and b_lo - tol <= a_hi:
                zones = [min(zones, key=lambda z: abs((z[0] + z[1]) / 2 - price))]
        for z_lo, z_hi, col, lab, zx0, dashed in zones:
            _band(z_lo, z_hi, col, lab, alpha=0.12, bx0=zx0, bx1=x1, dashed=dashed)
        # FVG：只画顺大趋势方向的缺口（多头趋势=现价下方需求缺口，空头镜像），
        # 从缺口形成那组 K 起向右延伸
        gaps = sorted((_find_fvg(k4) or []),
                      key=lambda g: abs((g["lo"] + g["hi"]) / 2 - price))
        drawn = 0
        for g in gaps:
            if drawn >= 3:
                break
            below = (g["lo"] + g["hi"]) / 2 < price
            if trend == "bullish" and not below:
                continue
            if trend == "bearish" and below:
                continue
            col = UP if below else DOWN
            fx0 = _kx(max(0, g["i"] - 2))[0] if g.get("i") is not None else None
            if fx0 is None:
                continue
            _band(g["lo"], g["hi"], col, "4h FVG", alpha=0.09, bx0=fx0, bx1=x1)
            drawn += 1
        # OTE：画最近一段腿的回撤窗口（窗口方向=最后一腿方向——回调打破小结构时，
        # 它就是用户要的「改变出来的」反向 OTE；主升是最后一腿时则是顺趋势 OTE）
        if smc_ov.get("ote_lo") is not None and smc_ov.get("ote_i0") is not None \
                and smc_ov.get("ote_dir"):
            ox0 = _kx(min(smc_ov["ote_i0"], smc_ov["ote_i1"]))[0]
            _band(smc_ov["ote_lo"], smc_ov["ote_hi"], ACCENT, "4h OTE", alpha=0.09,
                  bx0=ox0, bx1=x1)
        # 大趋势标签（用户定版：看图先看级别）——左上角标明日线大趋势方向
        tname = {"bullish": "多头", "bearish": "空头"}.get(trend, "方向不明")
        tcol = {"bullish": UP, "bearish": DOWN}.get(trend, SUB)
        tlab = f"大趋势·日线 {tname}"
        tw2 = dr.textlength(tlab, font=_font(12, True))
        dr.rounded_rectangle([x0 + 8, y0 + 8, x0 + 22 + tw2, y0 + 28], radius=5,
                             fill=(20, 25, 31), outline=_blend(tcol, BG, 0.35), width=1)
        dr.text((x0 + 15, y0 + 12), tlab, font=_font(12, True), fill=_blend(tcol, TXT, 0.25))

    # 区间高/低虚线标注（降级周期时标注实际覆盖范围），胶囊标签保证可读
    imax, imin = h.index(max(h)), l.index(min(l))
    for idx, val, col, lab in ((imax, h[imax], UP, f"{lbl} 高 {_fmt(h[imax])}"),
                               (imin, l[imin], DOWN, f"{lbl} 低 {_fmt(l[imin])}")):
        cx = x0 + step * idx + step / 2
        _dashed(dr, x0, py(val), x1, _blend(col, BG, 0.35))
        lw = dr.textlength(lab, font=_font(13))
        lx = min(max(cx - lw / 2, x0), x1 - lw - 8)
        ly = py(val) - 26 if idx == imax else py(val) + 6
        dr.rounded_rectangle([lx - 7, ly - 4, lx + lw + 7, ly + 20], radius=6,
                             fill=(20, 25, 31), outline=_blend(col, BG, 0.2), width=1)
        dr.text((lx, ly), lab, font=_font(13), fill=col)

    for i in range(n):
        cx = x0 + step * i + step / 2
        up = c[i] >= o[i]
        col = UP if up else DOWN
        dr.line([cx, py(h[i]), cx, py(l[i])], fill=_blend(col, BG, 0.3), width=1)  # 影线收暗一层，实体更立体
        top, bot = py(max(o[i], c[i])), py(min(o[i], c[i]))
        if bot - top < 1.2:
            bot = top + 1.2
        dr.rectangle([cx - bw / 2, top, cx + bw / 2, bot], fill=col)

    # 现价标记（右侧轴，虚线更轻）
    _dashed(dr, x0, py(price), x1, ACCENT)
    tag = _fmt(price)
    tw = dr.textlength(tag, font=_font(13, True)) + 12
    dr.rounded_rectangle([x1 + 6, py(price) - 10, x1 + 6 + tw, py(price) + 10], radius=4, fill=ACCENT)
    dr.text((x1 + 12, py(price) - 8), tag, font=_font(13, True), fill=(20, 20, 20))

    # x 轴日期
    for idx in (0, n // 3, 2 * n // 3, n - 1):
        if 0 <= idx < len(t):
            cx = x0 + step * idx + step / 2
            lab = _xaxis_label(t[idx])
            dr.text((min(cx, x1 - 40), y1 + 6), lab, font=_font(12), fill=SUB)

    # —— 成交量区（矮排，衬托主图） —— #
    vy0, vy1 = y1 + 30, H - 150
    vmax = max(v) or 1
    for i in range(n):
        cx = x0 + step * i + step / 2
        vh = (v[i] / vmax) * (vy1 - vy0)
        col = _blend(UP if c[i] >= o[i] else DOWN, BG, 0.45)   # 量柱收暗一档，衬托价格区
        dr.rectangle([cx - bw / 2, vy1 - vh, cx + bw / 2, vy1], fill=col)
    dr.text((x0 + 4, vy0 + 2), "成交量", font=_font(12), fill=SUB)

    # —— 底部信息条 —— #
    chips = []
    pos = (price - lo) / (hi - lo) * 100 if hi > lo else 50
    chips.append((f"{lbl}区间位置", f"{pos:.0f}%", ACCENT if pos > 80 else TXT))
    chips.append((f"{lbl}区间", f"{_fmt(lo)} ~ {_fmt(hi)}", TXT))
    fr = stat.get("funding_rate")
    if fr is not None:
        frc = float(fr) * 100
        chips.append(("资金费率", f"{frc:+.4f}%", UP if frc >= 0 else DOWN))
    if stat.get("oi"):
        chips.append(("合约 OI", f"{_fmt_usd(stat['oi'])} USDT", TXT))
    if stat.get("top_ratio") is not None:
        chips.append(("大户多空比", f"{stat['top_ratio']}", TXT))
    if stat.get("fng", {}).get("value") is not None:
        chips.append(("恐惧贪婪", f"{stat['fng']['value']} {stat['fng'].get('classification') or ''}", TXT))
    _draw_footer(dr, W, H, chips[:6])

    img.save(path, "PNG")
    return path


def draw_cover_4h(stat: dict, path: str) -> str:
    """4h 结构图：4h 蜡烛 + SMC 矩形区间框（多头/空头 OB、FVG、OTE）。

    与日线封面同版式；SMC 标记只出现在这张图上（用户指定）。"""
    return draw_cover(stat, path, kkey="k4h", smc_zones=True)


def draw_24h(stat: dict, path: str) -> str:
    """24h 分时图 1280x640：小时收盘折线 + 面积 + 高低点标注。"""
    W, H = 1280, 640
    cl = stat.get("c24") or []
    if len(cl) < 5:
        raise RuntimeError("24h 数据不足，无法出图")
    img = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(img)
    _vgrad(dr, 0, 0, W, H, (15, 20, 27), BG)
    price = cl[-1]
    chg = _pct(price, cl[0])
    _draw_header(dr, W, stat["symbol"], stat["market"], price, chg, sub_right="近 24 小时 · 1h 收盘")

    x0, x1 = 16, W - 96
    y0, y1 = 110, H - 110
    lo, hi = min(cl), max(cl)
    pad = (hi - lo) * 0.08 or hi * 0.01
    lo_p, hi_p = lo - pad, hi + pad
    n = len(cl)

    def px(i):
        return x0 + (x1 - x0) * i / (n - 1)

    def py(p):
        return y1 - (p - lo_p) / (hi_p - lo_p) * (y1 - y0)

    for i in range(5):
        gy = y0 + (y1 - y0) * i / 4
        _dashed(dr, x0, gy, x1, GRID)
        gp = hi_p - (hi_p - lo_p) * i / 4
        dr.text((x1 + 10, gy - 8), _fmt(gp), font=_font(13), fill=SUB)

    col = UP if cl[-1] >= cl[0] else DOWN
    pts = [(px(i), py(cl[i])) for i in range(n)]
    # 面积填充：纵向渐变渐隐（贴线最亮 → 底部没入背景），mask 贴多边形，零依赖
    gh = max(int(y1 - y0), 1)
    grad = Image.new("RGB", (W, gh))
    gd = ImageDraw.Draw(grad)
    for i in range(gh):
        gd.line([0, i, W, i], fill=_blend(BG, col, 0.32 * (1 - i / gh)))
    mask = Image.new("L", (W, gh), 0)
    ImageDraw.Draw(mask).polygon(
        [(px(i), py(cl[i]) - y0) for i in range(n)] + [(x1, gh), (x0, gh)], fill=255)
    img.paste(grad, (0, int(y0)), mask)
    # 折线三层描边：外圈辉光 → 中层过渡 → 亮芯
    dr.line(pts, fill=_blend(col, BG, 0.55), width=9, joint="curve")
    dr.line(pts, fill=_blend(col, BG, 0.25), width=5, joint="curve")
    dr.line(pts, fill=col, width=2, joint="curve")

    imax, imin = cl.index(hi), cl.index(lo)
    for idx, val, lab, dy in ((imax, hi, f"高 {_fmt(hi)}", -26), (imin, lo, f"低 {_fmt(lo)}", 10)):
        cx = px(idx)
        dr.ellipse([cx - 8, py(val) - 8, cx + 8, py(val) + 8], fill=_blend(col, BG, 0.55))
        dr.ellipse([cx - 4, py(val) - 4, cx + 4, py(val) + 4], fill=col)
        lw = dr.textlength(lab, font=_font(13))
        lx = min(max(cx - lw / 2, x0), x1 - lw)
        dr.rounded_rectangle([lx - 7, py(val) + dy - 4, lx + lw + 7, py(val) + dy + 20], radius=6,
                             fill=(20, 25, 31), outline=_blend(col, BG, 0.2), width=1)
        dr.text((lx, py(val) + dy), lab, font=_font(13), fill=col)

    _dashed(dr, x0, py(price), x1, ACCENT)
    tag = _fmt(price)
    tw = dr.textlength(tag, font=_font(13, True)) + 12
    dr.rounded_rectangle([x1 + 6, py(price) - 10, x1 + 6 + tw, py(price) + 10], radius=4, fill=ACCENT)
    dr.text((x1 + 12, py(price) - 8), tag, font=_font(13, True), fill=(20, 20, 20))

    for idx in (0, n // 2, n - 1):
        dr.text((max(px(idx) - 16, x0), y1 + 8), f"-{24 - idx}h" if idx < n - 1 else "现在",
                font=_font(12), fill=SUB)
    _draw_footer(dr, W, H, [("现价", _fmt(price), TXT),
                            ("24h 涨跌", f"{chg:+.2f}%", col),
                            ("24h 区间", f"{_fmt(lo)} ~ {_fmt(hi)}", TXT)])
    img.save(path, "PNG")
    return path


# ---------------- 组稿 ----------------

def _ma(closes: list, n: int):
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def _smc(stat: dict) -> dict:
    """轻量 SMC（Smart Money Concepts）分析 — 主周期 4 小时。
    摆动结构 / BOS・CHoCH / 流动性扫荡 / 折价溢价区 / OTE（斐波那契 0.62-0.79 最优入场区，中间值 0.702）。
    纯规则判定，输出供人话叙述。"""
    k = stat.get("k4h") or stat.get("k90") or {}
    h, l, c = k.get("highs") or [], k.get("lows") or [], k.get("closes") or []
    out = {}
    if len(c) < 30 or len(h) != len(c) or len(l) != len(c):
        return out
    K = 3  # 分形摆动宽度
    sh = [i for i in range(K, len(h) - K) if h[i] == max(h[i - K:i + K + 1])]
    sl = [i for i in range(K, len(l) - K) if l[i] == min(l[i - K:i + K + 1])]
    # 相邻聚类去重（保留更极值的那个）
    def _dedup(idxs, vals, keep_max):
        res = []
        for i in idxs:
            if res and i - res[-1] <= 2:
                if (vals[i] > vals[res[-1]]) == keep_max:
                    res[-1] = i
            else:
                res.append(i)
        return res
    sh, sl = _dedup(sh, h, True), _dedup(sl, l, False)
    out["sh"], out["sl"] = sh, sl
    out["sh_v"] = [h[i] for i in sh]
    out["sl_v"] = [l[i] for i in sl]

    # 结构：最近两组摆动高/低点
    hh = len(sh_v := out["sh_v"]) >= 2 and sh_v[-1] > sh_v[-2]
    hl = len(sl_v := out["sl_v"]) >= 2 and sl_v[-1] > sl_v[-2]
    lh = len(sh_v) >= 2 and sh_v[-1] < sh_v[-2]
    ll = len(sl_v) >= 2 and sl_v[-1] < sl_v[-2]
    out["structure"] = ("bullish" if hh and hl else "bearish" if lh and ll else "mixed")
    out["structure_txt"] = {
        "bullish": "高点高点抬、低点低点抬，多头结构",
        "bearish": "高点越压越低、低点越来越低，空头结构",
        "mixed": "高点和低点还没走出方向，结构在打架",
    }[out["structure"]]

    price = c[-1]
    # BOS / CHoCH：收盘越过最近摆动点
    if sh_v:
        last_h = sh_v[-1]
        if price > last_h:
            out["bos"] = "bullish"
            out["bos_txt"] = ("向上 CHoCH（空头结构被破坏，可能转多）" if out["structure"] != "bullish"
                              else "向上 BOS（多头结构延续）")
    if sl_v and "bos" not in out:
        last_l = sl_v[-1]
        if price < last_l:
            out["bos"] = "bearish"
            out["bos_txt"] = ("向下 CHoCH（多头结构被破坏，可能转空）" if out["structure"] != "bearish"
                              else "向下 BOS（空头结构延续）")

    # 流动性扫荡：近 10 根捅破前摆动点又收回
    if len(c) >= 12 and sl_v:
        last_l = sl_v[-1]
        if min(l[-10:]) < last_l and price > last_l:
            out["sweep"] = "bullish"
            out["sweep_txt"] = "前低被捅破后快速收回——下方的止损流动性被拿走，这种假跌破经常是转暖前兆"
    if "sweep" not in out and len(c) >= 12 and sh_v:
        last_h = sh_v[-1]
        if max(h[-10:]) > last_h and price < last_h:
            out["sweep"] = "bearish"
            out["sweep_txt"] = "前高被假突破后跌回来——上方的追多单被套住，大概率还要下来找流动性"

    # 折价/溢价区（区间 50% 分界）
    lo, hi = min(l), max(h)
    rng = hi - lo
    pos = (price - lo) / rng * 100 if rng > 0 else 50
    out["zone"] = "discount" if pos < 45 else "premium" if pos > 60 else "equilibrium"
    out["zone_txt"] = {"discount": "处在区间折价区（便宜的一侧）",
                       "premium": "处在区间溢价区（贵的一侧）",
                       "equilibrium": "正好卡在区间中轴附近"}[out["zone"]]
    out["pos"] = pos

    # OTE（Optimal Trade Entry）：最近一段摆动腿的斐波那契 0.62-0.79 回撤窗口；
    # 一般用中间值 0.702 作为入场参考位（用户定版 v1.5.59）。无可用腿时退化为全区间口径。
    OTE_LO, OTE_HI, OTE_ENTRY = 0.62, 0.79, 0.702
    leg_dir, leg_a, leg_b = "up", lo, hi
    leg_i = (0, len(c) - 1)          # 腿的 K 线索引锚点（OTE 框画在这段腿上）
    if sh and sl and sh[-1] != sl[-1]:
        if sl[-1] < sh[-1]:          # 低点在前、高点在后 → 上升腿
            leg_dir, leg_a, leg_b = "up", sl_v[-1], sh_v[-1]
            leg_i = (sl[-1], sh[-1])
        else:                        # 高点在前、低点在后 → 下降腿
            leg_dir, leg_a, leg_b = "down", sh_v[-1], sl_v[-1]
            leg_i = (sh[-1], sl[-1])
    rng_leg = leg_b - leg_a
    seg = f"这段腿的 {OTE_LO}-{OTE_HI}（中间值 {OTE_ENTRY}）"
    if rng_leg:                     # 下跌腿 rng<0 同样有效（v1.5.57 修复：>0 会漏掉全部空头 OTE）
        if leg_dir == "up":
            ote_lo, ote_hi = leg_b - rng_leg * OTE_HI, leg_b - rng_leg * OTE_LO
            ote_entry = leg_b - rng_leg * OTE_ENTRY
            out["ote_dir"] = "up"
            if ote_lo <= price <= ote_hi:
                out["ote"] = "inside"
                out["ote_txt"] = f"价恰好在 OTE 多头窗口（{_fmt(ote_lo)}~{_fmt(ote_hi)}，中间值 {_fmt(ote_entry)}；{_fmt(leg_a)}→{_fmt(leg_b)} {seg}回撤），盈亏比最优"
            elif price < ote_lo:
                out["ote"] = "below"
                out["ote_txt"] = f"价已跌穿 OTE 下沿 {_fmt(ote_lo)}，这段腿的多头窗口失守"
            else:
                out["ote"] = "above"
                out["ote_txt"] = f"价还在 OTE 上沿 {_fmt(ote_hi)} 上方，等回踩到 {_fmt(ote_lo)}~{_fmt(ote_hi)}（中间值 {_fmt(ote_entry)}）再接更划算"
        else:
            # 下降腿回撤：从终点低点 leg_b 向上返 0.62~0.79 的腿长
            #（v1.5.57 修复：旧式 leg_a + rng*… 是从起点高点往下算，rng<0 时窗口倒置且位置全错）
            ote_lo, ote_hi = leg_b - rng_leg * OTE_LO, leg_b - rng_leg * OTE_HI
            ote_entry = leg_b - rng_leg * OTE_ENTRY
            out["ote_dir"] = "down"
            if ote_lo <= price <= ote_hi:
                out["ote"] = "inside"
                out["ote_txt"] = f"价正回抽到空头 OTE 窗口（{_fmt(ote_lo)}~{_fmt(ote_hi)}，中间值 {_fmt(ote_entry)}；{_fmt(leg_b)}→{_fmt(leg_a)} {seg}反抽位），做空的盈亏比最优"
            elif price > ote_hi:
                out["ote"] = "above"
                out["ote_txt"] = f"价已升破空头 OTE 上沿 {_fmt(ote_hi)}，这段腿的空头窗口失守"
            else:
                out["ote"] = "below"
                out["ote_txt"] = f"价还在空头 OTE 下沿 {_fmt(ote_lo)} 下方，等反抽到 {_fmt(ote_lo)}~{_fmt(ote_hi)}（中间值 {_fmt(ote_entry)}）再空更划算"
        out["ote_lo"], out["ote_hi"], out["ote_entry"] = ote_lo, ote_hi, ote_entry
        out["ote_i0"], out["ote_i1"] = leg_i
    out["fvg"] = _find_fvg(k)
    return out


def _htf_trend(stat: dict) -> dict:
    """日线大趋势判定（用户定版 v1.5.58：趋势延续优先，锚=最近显著摆动点，实体破锚才翻转）。
    用户口径（BTC 2026-09 实例 + 手绘图定版）：只要没有实体 K 线收盘跌破最近显著低点
    （如 76,165）就还在上涨趋势中——
      · 影线捅破不算（09-11 插到 76,000 收在 77,191，仍是多头）；
      · 中途形成的摆动高点不翻转趋势（09-03 高点 82,282 未收破也不改多头）；
      · 被新锚取代的旧低点破位无效（09-10 实体跌破已过时的旧低 76,853，但最新锚是
        09-02 的 76,152，未破 → 仍多头）；
      · 镜像同理：空头锚 = 最近显著高点，实体升破才翻多。
    实现：单遍状态机（摆动点确认滞后 K=3 根，与 _smc 分形一致）——
      多头状态锚跟随最近已确认摆动低点，实体收盘破当前锚 → 翻空头（锚改挂最近摆动
      高点）；空头镜像；初始方向由第一次原始 BOS（实体收盘越过最近已确认摆动点）确定。
    返回 {"trend", "anchor", "txt"}；无数据返回 {}。"""
    k = stat.get("k90") or {}
    o, h, l, c = (k.get(x) or [] for x in ("opens", "highs", "lows", "closes"))
    if len(c) < 30 or len(o) != len(c) or len(h) != len(c) or len(l) != len(c):
        return {}
    smc = _smc({"k4h": k})
    sl, sh = smc.get("sl") or [], smc.get("sh") or []
    if not sl and not sh:
        return {"trend": smc.get("structure") or "mixed", "anchor": None,
                "txt": smc.get("structure_txt") or ""}
    n, K = len(c), 3
    state, anchor, aj = None, None, -1          # aj = 锚所在 K（形成时刻）
    for t in range(n):
        if state is None:
            # 初始方向：第一次原始 BOS（实体收盘越过最近已确认摆动点）
            lj = max((j for j in sl if j + K <= t), default=None)
            hj = max((j for j in sh if j + K <= t), default=None)
            if hj is not None and c[t] > h[hj]:
                state = 'bull'
            elif lj is not None and c[t] < l[lj]:
                state = 'bear'
            continue
        # 锚跟随最近「已确认」的反向显著摆动点（多头挂低点、空头挂高点）——
        # 新锚成形即取代旧锚，旧锚此后被破不算破位（用户手绘图定版）
        pool = [j for j in (sl if state == 'bull' else sh) if j + K <= t]
        j = max(pool, default=None)
        if j is not None:
            anchor, aj = (l[j] if state == 'bull' else h[j]), j
        # 实体收盘破当前锚（影线不算）→ 趋势翻转，锚改挂反向摆动点
        if anchor is not None and t > aj:
            if state == 'bull' and c[t] < anchor:
                state, anchor, aj = 'bear', None, -1
            elif state == 'bear' and c[t] > anchor:
                state, anchor, aj = 'bull', None, -1
    if state is None:
        return {"trend": smc.get("structure") or "mixed", "anchor": None,
                "txt": smc.get("structure_txt") or ""}
    if state == 'bull':
        txt = (f"大趋势是多头，锚在 {_fmt(anchor)}——实体收盘跌破锚之前，"
               "回调只算上涨中的调整（影线捅破不算）") if anchor is not None \
            else "大趋势偏多头（尚无已确认摆动低点作锚）"
        return {"trend": "bullish", "anchor": anchor, "txt": txt}
    txt = (f"大趋势是空头，锚在 {_fmt(anchor)}——实体收盘升破锚之前，"
           "反弹只算下跌中的调整（影线捅破不算）") if anchor is not None \
        else "大趋势偏空头（尚无已确认摆动高点作锚）"
    return {"trend": "bearish", "anchor": anchor, "txt": txt}


def _find_ob(k: dict, direction: str) -> dict:
    """找最近一个「有结构意义」的订单块（OB）——v1.5.57 重写（用户反馈：整体 OB 不对）。

    旧版只看「前阴后阳 + 现价在上方」，震荡区随手命中杂毛 K，画出的块没意义。
    新版三重条件，宁缺毋滥（找不到返回 {}，不画噪音区）：
      ① 破构确认：OB 必须是某段突破摆动点（BOS）推动浪的起点——
         bullish：某摆动高点被后续收盘升破，从破位 K 往回找这段上涨前的最后一根阴线；
         bearish 镜像（摆动低点被收盘跌破，往回找最后一根阳线）；
         打破一律以实体收盘为准，影线捅破不算（用户定版）；
      ② 未失效：形成之后没有被收盘价完全回吃（bullish：收盘跌破块下沿即失效；bearish 镜像）；
      ③ 只认真破构（用户定版：没有打破结构不算 OB）：OB 必须直接发动破构浪
         （OB 到破位 K ≤ 8 根），被破的摆动点用 K=4 显著分形（小抖动不算结构）；
         按 BOS 从新到旧逐个验证，最多回看 4 次破构。
    返回 {"dir", "low", "high", "i", "txt"}。"""
    h, l, c, o = k.get("highs") or [], k.get("lows") or [], k.get("closes") or [], k.get("opens") or []
    if len(c) < 15 or len(o) != len(c) or len(h) != len(c) or len(l) != len(c):
        return {}
    n = len(c)
    K = 4                                     # 分形强度：摆动点要显著，小抖动不算结构
    scan_from = max(K, n - 150)               # 结构只在近段找（150 根 ≈ 25 日/4h）
    if direction == "bullish":
        sw = [i for i in range(scan_from, n - K) if h[i] == max(h[i - K:i + K + 1])]
    else:
        sw = [i for i in range(scan_from, n - K) if l[i] == min(l[i - K:i + K + 1])]
    breaks, seen_m = [], set()                # (破位K m, 摆动点 j)——没有破构事件就没有 OB
    for j in sw:
        rng = range(j + 1, n)
        m = next((t for t in rng if (c[t] > h[j] if direction == "bullish" else c[t] < l[j])), None)
        if m is not None and m not in seen_m:
            seen_m.add(m)
            breaks.append((m, j))
    for m, j in reversed(breaks[-4:]):        # 最近的破构优先
        # 浪的真正起点（用户定版：OB 锚在起点极值，不是跌势中段的小反抽）：
        # bearish 取 j..m 段最高点，bullish 取最低点；OB = 起点极值处/前最后一根反向 K
        seg = range(j, m + 1)
        io = (max(seg, key=lambda t: h[t]) if direction == "bearish"
              else min(seg, key=lambda t: l[t]))
        rng2 = range(io, max(j - 1, scan_from - 1), -1)
        i = next((t for t in rng2 if (c[t] < o[t] if direction == "bullish" else c[t] > o[t])), None)
        if i is None or io - i > 3:           # OB 必须紧贴浪起点
            continue
        z_lo, z_hi = l[i], h[i]
        tail = range(m + 1, n)
        bad = any(c[t] < z_lo for t in tail) if direction == "bullish" \
            else any(c[t] > z_hi for t in tail)
        if bad:
            continue
        if direction == "bullish":
            txt = f"多头订单块（OB）在 {_fmt(z_lo)}~{_fmt(z_hi)}，{m - j + 1} 根 K 的上推动浪从这里起步（破构确认、未失效），回踩接需求"
        else:
            txt = f"空头订单块（OB）在 {_fmt(z_lo)}~{_fmt(z_hi)}，{m - j + 1} 根 K 的下破浪从这里起步（破构确认、未失效），反抽接供给"
        return {"dir": "bull" if direction == "bullish" else "bear",
                "low": z_lo, "high": z_hi, "i": i, "txt": txt}
    return {}


def _find_fvg(k: dict) -> list:
    """找未回补的 FVG（Fair Value Gap 公允价值缺口）：三根 K 中 1/3 根影线不重叠的跳区。
    宽度过滤（v1.5.57 修复「FVG 画错」）：缺口高度 < 现价 0.2% 的视为噪声跳空，不画不报——
    否则一条十几点的发丝缝被最小可视高度撑成大框，真正的深缺口反被挤掉。
    回补判定（用户定版）：价格触及缺口即算回补——影线碰到也算（bull：后续最低价 ≤ 缺口
    上沿；bear：后续最高价 ≥ 缺口下沿），缺口被碰过就销掉，不再画。
    各留一个最近的：现价下方多头 FVG（支撑）、上方空头 FVG（阻力）。"""
    h, l, c = k.get("highs") or [], k.get("lows") or [], k.get("closes") or []
    if len(h) < 8 or len(l) != len(h) or len(c) != len(h):
        return []
    n = len(h)
    min_h = (c[-1] or 0) * 0.002          # 噪声跳空过滤：宽度 ≥ 现价 0.2%
    bull = bear = None
    for i in range(n - 1, max(n - 80, 1), -1):
        if bull is None and l[i] > h[i - 2]:
            lo_, hi_ = h[i - 2], l[i]
            if hi_ - lo_ < min_h:
                pass                      # 发丝缝：噪声，不画
            elif i < n - 1 and min(l[i + 1:]) <= hi_:
                pass                      # 价格触及缺口（影线也算）：即算回补，往旧找
            else:
                bull = {"lo": lo_, "hi": hi_, "i": i}
        if bear is None and h[i] < l[i - 2]:
            lo_, hi_ = h[i], l[i - 2]
            if hi_ - lo_ < min_h:
                pass
            elif i < n - 1 and max(h[i + 1:]) >= lo_:
                pass
            else:
                bear = {"lo": lo_, "hi": hi_, "i": i}
        if bull is not None and bear is not None:
            break
    return [g for g in (bull, bear) if g]


def _bias(stat: dict) -> tuple:
    """方向打分（SMC 为主，传统指标为辅）→ (bias, reasons, smc)。"""
    k = stat.get("k4h") or stat.get("k90") or {}
    c = k.get("closes") or []
    if len(c) < 30:
        return "neutral", ["数据长度不足，方向判断保持观望。"], {}
    smc = _smc(stat)
    score, why = 0, []
    # 大小趋势分级（用户定版：先分清哪些是大趋势、哪些是小趋势）——
    # 日线大趋势用「趋势延续优先」口径（_htf_trend），权重 3；4h 小结构权重 1。
    ht = _htf_trend(stat)
    hst = ht.get("trend")
    if hst == "bullish":
        score += 3
        why.append("日线大趋势：" + ht.get("txt", "多头结构"))
    elif hst == "bearish":
        score -= 3
        why.append("日线大趋势：" + ht.get("txt", "空头结构"))
    elif hst:
        why.append("日线大趋势：" + (ht.get("txt") or "方向不明") + "，只算上下都有限的震荡")
    st = smc.get("structure")
    if st == "bullish":
        score += 1
        why.append("4h 小趋势：" + smc["structure_txt"])
    elif st == "bearish":
        score -= 1
        why.append("4h 小趋势：" + smc["structure_txt"])
    else:
        why.append("4h 小趋势：结构方向不明")
    if smc.get("bos") == "bullish":
        score += 1
        why.append(smc["bos_txt"])
    elif smc.get("bos") == "bearish":
        score -= 1
        why.append(smc["bos_txt"])
    if smc.get("sweep") == "bullish":
        score += 1
        why.append(smc["sweep_txt"])
    elif smc.get("sweep") == "bearish":
        score -= 1
        why.append(smc["sweep_txt"])
    if smc.get("zone") == "discount":
        score += 1
        why.append("价格" + smc["zone_txt"] + "，做多的赔率更好")
    elif smc.get("zone") == "premium":
        score -= 1
        why.append("价格" + smc["zone_txt"] + "，追多的赔率偏差")
    if smc.get("ote") == "inside":
        if smc.get("ote_dir") == "up":
            score += 1
        else:
            score -= 1
        why.append(smc["ote_txt"])
    elif smc.get("ote") == "below" and smc.get("ote_dir") == "up":
        score -= 1
        why.append(smc["ote_txt"])
    elif smc.get("ote") == "above" and smc.get("ote_dir") == "down":
        score += 1
        why.append(smc["ote_txt"])
    price = c[-1]
    ma20 = _ma(c, 20)
    if ma20:
        if price > ma20:
            score += 1
            why.append("4h 站在 MA20 上方")
        else:
            score -= 1
            why.append("4h 还在 MA20 下方")
    fr = stat.get("funding_rate")
    if fr is not None:
        frc = float(fr) * 100
        if frc >= 0.05:
            score -= 1
            why.append(f"费率 {frc:+.4f}% 偏高，多头拥挤")
        elif frc <= -0.05:
            score += 1
            why.append(f"费率 {frc:+.4f}% 为负，空头拥挤，易轧空")
    bias = "long" if score >= 2 else ("short" if score <= -2 else "neutral")
    # OB（订单块）：沿结构/倾向方向找最近的需求/供给区
    d = bias if bias != "neutral" else (st or "bullish")
    d = "bullish" if d in ("long", "bull", "bullish") else "bearish"
    ob = _find_ob(k, d)
    smc["ob"] = ob
    if ob.get("txt"):
        why.append(ob["txt"])
    return bias, why, smc


# 仓位举例口径（v1.5.47 回归用户口径）：本金 = 保证金，10x 杠杆 → 名义 1000U。
# **止损只由 SMC 结构决定**（OB 下沿/OTE 下沿/摆动低点下方缓冲 = 结构失效位），
# 不用风险预算去反推止损或仓位；打止损亏多少按结构止损**如实报数**，
# 偏重（> 本金 10%）时附一句「把名义降到多少」的稳妥建议，仅供参考。
_CAPITAL_U = 100          # 举例本金（U）= 保证金
_LEVERAGE = 10            # 举例杠杆
_RISK_TARGET_U = 10       # 本金的 10%：只用来判断「偏重」，不参与定止损/定仓位


def _size_line(entry: float, stop: float, direction: str, anchor: str = "") -> str:
    """仓位算法（v1.5.47 回归用户口径 + SMC 结构止损）。

    修复记录：
      · 2026-09-12（v1.5.40）：从「单笔只亏 5U」反推名义再除 10 得保证金，数字互相打架；
      · 2026-09-13（v1.5.46）：距离改从入场价算（正确，保留），但把口径改成了
        「按单笔风险 10U 反推名义」→ 止损被风险预算绑架、永远「恒亏 ≈10U」——
        用户纠正：**止损必须按 SMC 结构失效位设置，风险预算不能反推止损**；
      · v1.5.47：止损只看结构（锚位文案由 _plan_levels 传入），仓位回归
        「本金 = 保证金，10x → 名义 1000U」演示口径；打止损亏损如实报数，
        超过本金 10% 时附「降名义」建议（可选参考，不改变止损位）。
    """
    dist = abs(entry - stop) / entry * 100
    if dist < 0.5:
        dist = 0.5
    d = dist / 100
    side = "做多" if direction == "long" else "做空"
    notional = _CAPITAL_U * _LEVERAGE                    # 开单名义 = 本金 × 杠杆
    loss = notional * d                                  # 打到结构止损的亏损（如实）
    anchor_txt = f"{anchor}，距入场 {dist:.1f}%）" if anchor else f"距入场 {dist:.1f}%）"
    txt = (f"· 仓位算法：{_CAPITAL_U}U 本金 = {_CAPITAL_U}U 保证金，{_LEVERAGE}x 杠杆 → "
           f"{side}开单名义 ≈ {notional:.0f}U；止损 {_fmt(stop)}（{anchor_txt}，"
           f"打止损亏 ≈ {loss:.1f}U")
    if loss > _RISK_TARGET_U:                            # 超过本金 10% → 给降名义建议（可选）
        n2 = _RISK_TARGET_U / d
        txt += (f"（占本金 {loss / _CAPITAL_U * 100:.0f}%，偏重；想稳一点把名义降到 ≈{n2:.0f}U，"
                f"单笔亏损回到本金 10% 左右，止损位不动）")
    return txt + "。"


def _pick_tp(stat: dict, smc: dict, entry_px: float, stop: float, direction: str):
    """止盈目标（v1.5.49 定版，用户指示：瞄准 SMC 流动性区域——摆动点/FVG/OB 块）：
    ① 首选 SMC 摆动点（前高/前低 = 裸露流动性池）；
    ② 太近（RR<1.5）→ 换其他流动性目标：FVG 缺口（价格对缺口有回补引力，取缺口中位）、
       对侧 OB 块边缘（若在止盈方向上）、近 30 根极值 → 90日极值，取第一个 RR≥1.5 的，
       标签如实标注；
    ③ 全不达标取最远目标，RR 如实回报，由 _plan 决定「小仓/劝退」话术。
    返回 (价, 标签, rr)；数据不足返回 None。"""
    k4 = stat.get("k4h") or {}
    k90 = stat.get("k90") or {}
    liq: list = []
    for g in (smc.get("fvg") or []):
        try:
            mid = (g["lo"] + g["hi"]) / 2
        except (KeyError, TypeError):
            continue
        if direction == "long" and mid > entry_px:
            liq.append((mid, "FVG 缺口回补"))
        elif direction == "short" and mid < entry_px:
            liq.append((mid, "FVG 缺口回补"))
    ob = smc.get("ob") or {}
    if direction == "long" and ob.get("low") and ob["low"] > entry_px:
        liq.append((ob["low"], "上方 OB 块"))
    elif direction == "short" and ob.get("high") and ob["high"] < entry_px:
        liq.append((ob["high"], "下方 OB 块"))
    if direction == "long":
        cands = ([(v, "4h 前高/摆动高点") for v in reversed((smc or {}).get("sh_v") or [])]
                 + liq
                 + ([(max(k4["highs"][-30:]), "近 30 根 4h 高点") if k4.get("highs") else None])
                 + ([(max(k90["highs"]), "90日高点") if k90.get("highs") else None]))
        cands = [cd for cd in cands if cd]      # k90/k4h 缺失时上面的条件项是裸 None（v1.5.60 修）
        ok = [(v, t) for v, t in cands if v and v > entry_px]
        risk = entry_px - stop
    else:
        cands = ([(v, "4h 前低/摆动低点") for v in reversed((smc or {}).get("sl_v") or [])]
                 + liq
                 + ([(min(k4["lows"]), "近 30 根 4h 低点") if k4.get("lows") else None])
                 + ([(min(k90["lows"]), "90日低点") if k90.get("lows") else None]))
        cands = [cd for cd in cands if cd]
        ok = [(v, t) for v, t in cands if v and v < entry_px]
        risk = stop - entry_px
    if risk <= 0 or not ok:
        return None
    seen, uniq = set(), []
    for v, t in ok:
        if v not in seen:            # 去重保序（近 → 远）
            seen.add(v)
            uniq.append((v, t))
    for v, t in uniq:
        if abs(v - entry_px) / risk >= 1.5:
            return v, t, abs(v - entry_px) / risk
    v, t = max(uniq, key=lambda vt: abs(vt[0] - entry_px))  # 全不达标：取最远目标，RR 如实上报
    return v, t, abs(v - entry_px) / risk


# 10x 杠杆下反向 ~9-10% 就强平，止损距离必须 ≤8% 才能活着被打到止损位
# （v1.5.60 护栏，用户实测两篇翻车：LSK 旧 OB 距现价 -85% 还挂单、牛来空头 OB 区宽 58%，
#   止损距离 59%——都是「打止损前先强平」的不可执行结构）
_STOP_MAX_PCT = 0.08


def _stop_ok(entry_px, stop):
    """入场→止损的距离是否在 10x 杠杆可承受范围内（≤8%）。"""
    return bool(entry_px and stop and 0 < abs(entry_px - stop) / entry_px <= _STOP_MAX_PCT)


def _plan_levels(stat: dict, bias: str, smc: dict = None) -> dict:
    """算出「入场 / 止损 / 止盈」三级点位，**供 _plan() 与反向剧本共用**。

    为什么必须共用（2026-09-12 修）：反向剧本原来自己另取 90 日低点当多头的作废线，
    与这里真正给出的止损差了 40% 量级 —— 那篇 ETH 文章入场 2,507.60、止损 2,495.06，
    作废线却是 1,503.32。同一段计划自相矛盾，读者照它扛单要亏 40%。
    作废线必须锚在同一笔单的止损上，所以先把点位算出来、两边都引用它。

    返回 {} 表示数据不足；bias == "neutral" 时 entry/stop/tp1 为 None。
    """
    smc = smc or {}
    k = stat.get("k4h") or stat.get("k90") or {}
    c = k.get("closes") or []
    if len(c) < 20 or not k.get("lows") or not k.get("highs"):
        return {}
    price = c[-1]
    r10_lo = min(k["lows"][-10:])
    r10_hi = max(k["highs"][-10:])
    ob = smc.get("ob") or {}
    ob_lo, ob_hi = ob.get("low"), ob.get("high")
    ote_lo, ote_hi = smc.get("ote_lo"), smc.get("ote_hi")
    lv = {"price": price, "k": k, "r10_lo": r10_lo, "r10_hi": r10_hi,
          "entry": None, "stop": None, "tp1": None, "tp_txt": "",
          # zone_lo/zone_hi：入场参考区（模拟挂单建单时判断现价是否已入场用，v1.5.61）
          "zone_lo": None, "zone_hi": None,
          # entry_px：计算止损距离/盈亏比用的**入场参考价**。v1.5.47c（用户定版）：
          # 多头买在 OB 上沿、空头卖在 OB 下沿（第一触点，保证成交），入场文案标明进价；
          # 止损锚对侧失效位（多头 OB 下沿下方 / 空头 OB 上沿上方）——距离 = 区宽 + 缓冲，如实计算
          "entry_px": None,
          # anchor：止损的 SMC 结构锚位说明（v1.5.47）——止损只看结构失效位
          "anchor": ""}

    if bias == "long":
        # 结构位降级链（v1.5.60）：OB → OTE → 近10根低点 → 观望。
        # 每档都要过 _stop_ok 护栏（止损距离 ≤8%，10x 杠杆活着到止损的前提），
        # 拒绝「挂单距离/止损距离」超限的旧结构（暴涨币的 OB 可能还在 -85% 的老底）。
        if ob_lo and ob_hi and ob_lo < price and _stop_ok(ob_hi, ob_lo * 0.995):
            lv["entry"] = (f"· 入场：优先挂 {_fmt(ob_lo)} ~ {_fmt(ob_hi)} 的多头 OB 区回踩接"
                           f"（限价，进价 {_fmt(ob_hi)}——上沿第一触点保证成交），现价 {_fmt(price)} 直接追的盈亏比一般")
            lv["stop"] = ob_lo * 0.995
            lv["entry_px"] = ob_hi
            lv["zone_lo"], lv["zone_hi"] = ob_lo, ob_hi
            lv["anchor"] = f"多头 OB 下沿 {_fmt(ob_lo)} 下方 0.5% 缓冲，结构失效即离场"
        elif smc.get("ote_dir") == "up" and ote_lo is not None and ote_lo < price \
                and _stop_ok(smc.get("ote_entry") or ote_hi, ote_lo * 0.99):
            ote_e = smc.get("ote_entry") or ote_hi
            lv["entry"] = (f"· 入场：等回踩 OTE 窗口 {_fmt(ote_lo)} ~ {_fmt(ote_hi)}（斐波那契 0.62-0.79）分批接"
                           f"（参考中间值进价 {_fmt(ote_e)}——取 0.702 位）")
            lv["stop"] = ote_lo * 0.99
            lv["entry_px"] = ote_e
            lv["zone_lo"], lv["zone_hi"] = ote_lo, ote_hi
            lv["anchor"] = f"OTE 窗口下沿 {_fmt(ote_lo)} 下方 1% 缓冲，结构失效即离场"
        elif _stop_ok(price, r10_lo * 0.99):
            lv["entry"] = (f"· 入场：现价 {_fmt(price)} 附近轻仓试，"
                           f"或等 4h 回踩 {_fmt(r10_lo*0.995)}（近 10 根低点下方）确认支撑")
            lv["stop"] = r10_lo * 0.99
            lv["entry_px"] = price
            lv["zone_lo"], lv["zone_hi"] = price * 0.995, price * 1.005
            lv["anchor"] = "近 10 根摆动低点下方 1% 缓冲，摆动结构失效即离场"
        else:
            lv["noplay"] = True
            lv["entry"] = (f"· 入场：观望——现价 {_fmt(price)} 距离所有可用结构位（OB/OTE/近10根低点）"
                           f"的止损距离都超出 10x 杠杆可承受的 8%（强平线 ~9%），挂哪都可能在打止损前先被强平；"
                           "降杠杆（如 3x）或等价格回撤出更近的结构再排计划")
        if lv.get("entry_px") and lv.get("stop"):
            got = _pick_tp(stat, smc, lv["entry_px"], lv["stop"], "long")
            if got:
                lv["tp1"], lv["tp_txt"], lv["rr"] = got
    elif bias == "short":
        # 结构位降级链（v1.5.60）：OB → 空头 OTE → 近10根高点 → 观望（护栏同多头）
        if ob_lo and ob_hi and ob_hi > price and _stop_ok(ob_lo, ob_hi * 1.005):
            lv["entry"] = (f"· 入场：优先挂 {_fmt(ob_lo)} ~ {_fmt(ob_hi)} 的空头 OB 区反弹接"
                           f"（限价，进价 {_fmt(ob_lo)}——下沿第一触点保证成交），不追空")
            lv["stop"] = ob_hi * 1.005
            lv["entry_px"] = ob_lo
            lv["zone_lo"], lv["zone_hi"] = ob_lo, ob_hi
            lv["anchor"] = f"空头 OB 上沿 {_fmt(ob_hi)} 上方 0.5% 缓冲，结构失效即离场"
        elif smc.get("ote_dir") == "down" and ote_hi is not None and ote_hi > price \
                and _stop_ok(smc.get("ote_entry") or ote_lo, ote_hi * 1.01):
            ote_e = smc.get("ote_entry") or ote_lo
            lv["entry"] = (f"· 入场：等反抽空头 OTE 窗口 {_fmt(ote_lo)} ~ {_fmt(ote_hi)}（斐波那契 0.62-0.79）分批空"
                           f"（参考中间值进价 {_fmt(ote_e)}——取 0.702 位）")
            lv["stop"] = ote_hi * 1.01
            lv["entry_px"] = ote_e
            lv["zone_lo"], lv["zone_hi"] = ote_lo, ote_hi
            lv["anchor"] = f"空头 OTE 窗口上沿 {_fmt(ote_hi)} 上方 1% 缓冲，结构失效即离场"
        elif _stop_ok(price, r10_hi * 1.01):
            lv["entry"] = f"· 入场：反弹到 {_fmt(r10_hi*1.005)}（近 10 根高点上方）再空，不追空"
            lv["stop"] = r10_hi * 1.01
            lv["entry_px"] = price
            lv["zone_lo"], lv["zone_hi"] = price * 0.995, price * 1.005
            lv["anchor"] = "近 10 根摆动高点上方 1% 缓冲，摆动结构失效即离场"
        else:
            lv["noplay"] = True
            lv["entry"] = (f"· 入场：观望——现价 {_fmt(price)} 距离所有可用结构位（OB/OTE/近10根高点）"
                           f"的止损距离都超出 10x 杠杆可承受的 8%（强平线 ~9%），挂哪都可能在打止损前先被强平；"
                           "降杠杆（如 3x）或等价格反弹出更近的结构再排计划")
        if lv.get("entry_px") and lv.get("stop"):
            got = _pick_tp(stat, smc, lv["entry_px"], lv["stop"], "short")
            if got:
                lv["tp1"], lv["tp_txt"], lv["rr"] = got
    return lv


def _plan(stat: dict, bias: str, smc: dict = None) -> list:
    """仓位/点位方案（v1.5.47：止损只由 SMC 结构失效位决定；仓位口径
    本金 = 保证金、10x → 名义 1000U，打止损亏损如实报数）。点位基于 4h SMC：OB/OTE/摆动点。"""
    lv = _plan_levels(stat, bias, smc)
    if not lv:
        return ["· 数据不足，给不出靠谱点位，宁可错过不做没把握的。"]
    if lv.get("noplay"):
        return [lv["entry"]]     # 结构位止损距离全部超限 → 只给观望劝退，不摆仓位算法
    if bias in ("long", "short"):
        # 止盈（v1.5.49 定版）：首选 SMC 摆动点，太近（RR<1.5）自动换更远的流动性目标
        # （近 30 根极值 → 90日极值）；仍不达标则如实标注小仓/劝退（用户质问「盈亏比
        # 0.3 是认真的吗」）—— 绝不摆出一副能做的样子
        rr = lv.get("rr")
        if rr is not None and rr < 1.0:
            return [lv["entry"],
                    f"· 止盈：最近的结构目标 {_fmt(lv['tp1'])}（{lv['tp_txt']}）离入场太近，"
                    f"盈亏比 ≈ {rr:.1f} —— **这笔结构质量不够，放弃**；等价格离目标位更远、"
                    "或入场更贴近止损再排计划"]
        rr_txt = f"，盈亏比 ≈ {rr:.1f}" if rr is not None else ""
        note = "（盈亏比一般，只试小仓）" if rr is not None and rr < 1.5 else ""
        # 4h OTE 参考（用户定版 v1.5.59）：无论入场走 OB 还是 OTE，都要把 4h OTE 介绍清楚，
        # 范围 0.62~0.79（斐波那契最优入场），一般用中间值 0.702 作入场位
        ote_ref = ""
        ol, oh, oe, od = ((smc or {}).get(x) for x in ("ote_lo", "ote_hi", "ote_entry", "ote_dir"))
        if ol is not None and oh is not None:
            side = "多头" if od == "up" else "空头"
            ote_ref = (f"· 4h OTE 参考：最优入场区间取 0.62~0.79 斐波那契回撤（中间值 0.702 作入场），"
                       f"当前{side} OTE 窗口 {_fmt(ol)}~{_fmt(oh)}（中间值 {_fmt(oe)}）——"
                       f"{'回踩' if od == 'up' else '反抽'}到这附近再待命，盈亏比更优")
        head = [ote_ref] if ote_ref else []
        return head + [lv["entry"],
                f"· 止盈：第一目标 {_fmt(lv['tp1'])}（{lv['tp_txt']}{rr_txt}）先减半{note}，破位续持有看日线级别空间",
                _size_line(lv["entry_px"] or lv["price"], lv["stop"], bias, lv.get("anchor", ""))]
    return ["· 观望为主：多空信号打架时，不进场就是最好的仓位。",
            f"· 若非要动：向上突破 {_fmt(lv['r10_hi']*1.01)} 小仓跟多 / 跌破 {_fmt(lv['r10_lo']*0.99)} 小仓跟空，"
            "严格止损；仓位照「本金 = 保证金、开单名义 = 本金 × 杠杆」算，方向没走出来之前别上满。"]


# 作废线允许离现价多远。超过就该换一种说法，而不是硬写一个读者按它扛单会亏 40% 的价位。
_INVALID_MAX_DROP = 0.15     # 4h 结构位：现价下方 15% 以内
_MACRO_MAX_DROP = 0.20       # 日线级别大位：现价下方 20% 以内才值得附带一提


def _nearest_struct_below(price, stop, k, smc):
    """止损下方最近的那个结构位（越贴近止损越有参考价值）。取不到返回 (None, "")。"""
    cands = []
    ote_lo = (smc or {}).get("ote_lo")
    if ote_lo:
        cands.append((ote_lo, "OTE 下沿"))
    for v in ((smc or {}).get("sl_v") or [])[-3:]:
        if v:
            cands.append((v, "4h 摆动低点"))
    lows = (k or {}).get("lows") or []
    if lows:
        cands.append((min(lows[-20:]), "近 20 根 4h 低点"))
    # 必须在止损下方、且离现价不能太远，否则不是「这笔单」的结构位
    ok = [(v, lb) for v, lb in cands
          if v and v < stop and v > price * (1 - _INVALID_MAX_DROP)]
    if not ok:
        return None, ""
    return max(ok, key=lambda t: t[0])


def _macro_level(price, stat):
    """90 日低点这类「日线级别大位」—— 离现价太远就不提，免得跟这笔单的止损混淆。"""
    lows = ((stat or {}).get("k90") or {}).get("lows") or []
    if not lows or not price:
        return None
    lo = min(lows)
    if 0 < (price - lo) / price <= _MACRO_MAX_DROP:
        return lo
    return None


def _invalid_line(stat: dict, bias: str, smc: dict = None) -> str:
    """反向剧本（作废条件）。核心约束：**作废线必须锚在这笔单自己的止损上**。

    修复记录（2026-09-12，用户实测）：旧实现 `if bias != "short"` 时直接取 90 日低点当
    多头的作废线，那篇 ETH 文章里入场 2,507.60、止损 2,495.06，作废线却写成
    「跌破 1,503.32（90 日低点下方）」—— 比止损还低 40%。三个后果：
      ① 同一段计划自相矛盾：止损 2,495 早就先打到了，1,503 那句永远不会触发；
      ② 把「这笔单作废」和「大趋势作废」混为一谈，读者照它扛单要亏 40%；
      ③ 旁边的「仓位算法」已经给了 2,495.06，两句数字打架，读者不知道信哪个。

    现在改为：
      - 作废线 = `_plan_levels()` 算出的同一个 stop（同一笔单的口径）；
      - 再补一个「离止损最近的更深结构位」（4h 摆动低点 / OTE 下沿 / 近 20 根低点，
        且必须在现价下方 _INVALID_MAX_DROP 以内）作为「结构坏掉」的确认；
      - 90 日低点只在离现价 ≤ _MACRO_MAX_DROP 时才附带提及，并明确标注它跟止损不是一回事。
    另外修掉一个连带 bug：旧代码 `bias != "short"` 把 neutral 也当成多头，观望场景下
    照样输出「上面这些多头逻辑全部作废」，可那段计划里根本没有多头单。
    """
    lv = _plan_levels(stat, bias, smc)
    if not lv:
        return "· 反向剧本：数据不足，先不参与，等结构清楚了再定。"

    if bias == "short":
        stop = lv.get("stop")
        if stop:
            return (f"· 反向剧本：4h 收盘站回 {_fmt(stop)} 上方（本单止损位）就先认错出场；"
                    "若进一步放量收复 MA20 并站稳，空头逻辑才算真的作废。")
        return "· 反向剧本：哪天放量收复 MA20 并站稳，空头逻辑作废，及时认错不丢人。"

    if bias == "neutral":
        # 没有仓位就没有「这笔单作废」，只给两种「方向自己走出来」的触发条件
        return (f"· 反向剧本：现在没仓位，等方向自己走出来 —— 站稳 {_fmt(lv['r10_hi']*1.01)} 转偏多、"
                f"跌破 {_fmt(lv['r10_lo']*0.99)} 转偏空，两边都不给就继续空仓等。")

    price = lv["price"]
    stop = lv.get("stop")
    if not stop:
        return "· 反向剧本：4h 收盘重新跌回当前区间下沿并收不回来，多头逻辑就先放一放，别硬扛。"

    txt = f"· 反向剧本：4h 收盘跌回 {_fmt(stop)} 下方（本单止损位）就别恋战，按计划砍仓"
    struct, label = _nearest_struct_below(price, stop, lv["k"], smc)
    if struct:
        txt += f"；若连 {_fmt(struct)}（{label}）都收不回来，多头结构才算真的走坏"
    txt += "。"
    macro = _macro_level(price, stat)
    if macro:
        txt += f" 日线级别的大位在 {_fmt(macro)} 附近，那是大趋势的事，跟这笔单的止损不是一回事。"
    return txt


def _fmt_lo_hi(t_label: str) -> str:
    return f"{t_label} 那波" if t_label else "前面那波"


def _human_story(stat: dict, smc: dict) -> str:
    """把 90 日走势 + SMC 结构讲成一段人话。"""
    k = stat.get("k90") or {}
    c = k.get("closes") or []
    lo, hi = min(k["lows"]), max(k["highs"])
    price = c[-1]
    chg90 = _pct(price, c[0]) if len(c) > 1 else 0
    lo_t = hi_t = ""
    if k.get("times") and len(k["times"]) == len(k["lows"]) == len(k["highs"]):
        lo_t = _xaxis_label(k["times"][k["lows"].index(lo)])
        hi_t = _xaxis_label(k["times"][k["highs"].index(hi)])
    p = []
    span = stat.get("k90_label") or "90 日"   # 降级周期时如实说「近90日·4h」「近30日·1h」
    if chg90 <= -15:
        p.append(f"{_fmt_lo_hi(hi_t)}冲到 {_fmt(hi)} 之后就没像样地反攻过，{span}下来 {chg90:.0f}%，低点 {_fmt(lo)} 落在 {lo_t or '区间后段'}。")
    elif chg90 >= 30:
        p.append(f"这{span}整体是往上走的，涨了 {chg90:.0f}%，{_fmt_lo_hi(hi_t)}触到 {_fmt(hi)}，低点 {_fmt(lo)} 是 {lo_t or '早段'}的事。")
    else:
        p.append(f"这{span}基本就是 {_fmt(lo)} 到 {_fmt(hi)} 之间来回，目前 {chg90:+.0f}%，谈不上单边。")
    if smc:
        # 大小趋势分级（用户定版）：先讲日线大趋势（趋势延续优先口径），再讲 4h 小趋势
        ht = _htf_trend(stat)
        hst = ht.get("trend")
        if hst in ("bullish", "bearish") and ht.get("txt"):
            p.append(f"先把级别分清楚：日线{ht['txt']}。")
        elif hst:
            p.append("先把级别分清楚：日线大趋势暂时没有方向，高点和低点在打架，属于区间市。")
        if smc.get("structure") == "bearish":
            p.append("拉 4 小时结构看，它一直处在「反弹一个比一个矮、下探一个比一个深」的节奏里，筹码在往下换手。")
        elif smc.get("structure") == "bullish":
            p.append("拉 4 小时结构看，回撤的低点一个比一个高，突破的高点也在抬，筹码在往上换手，这是多头控盘的样子。")
        else:
            p.append("拉 4 小时结构看，最近两个高点和低点互有胜负，多空暂时谁也没说服谁。")
        if smc.get("sweep"):
            p.append(f"比较有意思的是最近这出戏：{smc['sweep_txt']}。")
        if smc.get("bos"):
            p.append(f"另外，{smc['bos_txt']}，这是结构层面最近的信号。")
        p.append(f"位置上它现在{smc.get('zone_txt', '在区间中段')}。")
    else:
        r7 = c[-7:]
        if len(r7) >= 2 and max(r7) > price * 1.03 >= price:
            p.append("最近一周有点止跌回升的意思，但还没走出明确趋势。")
        elif len(r7) >= 2 and price <= min(r7) * 1.01:
            p.append("最近一周还在阴跌，别急着接。")
    return " ".join(p)


def _market_bits(stat: dict) -> list:
    """衍生品 + 情绪拆成短句列表 —— 各风格换自己的话头来拼，不再共用一句固定开场白。"""
    s = []
    if stat["market"] == "futures":
        fr = stat.get("funding_rate")
        if fr is not None:
            frc = float(fr) * 100
            mood = ("多头不拥挤" if frc < 0.05 else
                    "多头有点拥挤，随时可能插针" if frc < 0.15 else "费率很热，杠杆情绪已经极端")
            s.append(f"资金费率 {frc:+.4f}%，{mood}")
        if stat.get("oi"):
            hot = "关注度不低" if float(stat["oi"]) >= 3e8 else "不温不火"
            s.append(f"OI 大概 {_fmt_usd(stat['oi'])} USDT，{hot}")
    fng = stat.get("fng") or {}
    if fng.get("value") is not None:
        v = fng["value"]
        mood = ("偏贪婪但没到过热" if 50 <= v < 75 else
                "已经过热，要留个心眼" if v >= 75 else
                "偏恐惧，机会往往是跌出来的" if v >= 25 else "极度恐惧，情绪冰点")
        s.append(f"恐惧贪婪指数 {v}，{mood}")
    return s


def _market_story(stat: dict) -> str:
    """兼容旧调用：默认话头。"""
    s = _market_bits(stat)
    if not s:
        return ""
    head = "合约和情绪面放在一块说：" if stat["market"] == "futures" else "情绪面上："
    return head + "；".join(s) + "。"


def _news_picks(stat: dict) -> dict:
    """消息面事实（来自 scanner.coin_news）。取不到就返回 {}，整段自然省略。"""
    nd = stat.get("news") or {}
    items = [x for x in (nd.get("items") or []) if x.get("title")]
    if not items:
        return {}
    bull = sum(1 for x in items if x.get("sentiment") == "bull")
    bear = sum(1 for x in items if x.get("sentiment") == "bear")
    # 挑两条最值得引的：先要有情绪倾向，再优先中文源（读者好读），最后要新
    order = {"bull": 0, "bear": 1, "neutral": 2}
    picks = sorted(items, key=lambda x: (order.get(x.get("sentiment"), 3),
                                         0 if x.get("source") == "PANews" else 1,
                                         -(x.get("ts") or 0)))[:2]
    return {"count": len(items), "bull": bull, "bear": bear,
            "neutral": len(items) - bull - bear, "picks": picks,
            "failed": nd.get("failed") or []}


def _news_time(x: dict) -> str:
    ts = x.get("ts") or 0
    if not ts:
        return ""
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(ts / 1000))
    except Exception:
        return ""


def _news_mood(p: dict) -> str:
    b, r = p["bull"], p["bear"]
    if b >= 2 and b > r * 1.5:
        return "偏暖"
    if r >= 2 and r > b * 1.5:
        return "偏冷"
    return "不一边倒"


def _news_sentence(p: dict) -> str:
    """消息面事实句（不含风格化的话头，各风格自己加）。

    v1.5.40 按用户要求精简：不输出「某源没抓到」这类实现细节，
    也不加「消息只是背景板…」的说教尾巴 —— 消息面只陈述事实。
    """
    if not p:
        return ""
    picked = []
    for x in p["picks"]:
        tm = _news_time(x)
        picked.append(f"{x.get('source', '')}{(' ' + tm) if tm else ''}「{x['title']}」")
    s = (f"跟它相关的标题扫到 {p['count']} 条，利多 {p['bull']} / 利空 {p['bear']} / "
         f"中性 {p['neutral']} 条，整体{_news_mood(p)}")
    if picked:
        s += f"；比如：{'；'.join(picked)}"
    return s + "。"



def _view_story(stat: dict, bias: str, smc: dict) -> list:
    """「我的看法」：SMC 推理链，逐条返回（结构 → OTE → OB → FVG → 动能 → 结论）。"""
    k = stat.get("k4h") or stat.get("k90") or {}
    c = k.get("closes") or []
    price = c[-1] if c else None
    ma20 = _ma(c, 20)
    p = []
    st = smc.get("structure")
    if st == "bullish":
        p.append("4 小时上，4h 低点还在抬高、高点也在抬，大的多头结构暂时没坏")
    elif st == "bearish":
        p.append("4 小时上，4h 高点一个比一个矮、低点也在下移，空头结构还在")
    else:
        p.append("4 小时上，4h 高低点互有胜负，结构本身没给方向")

    ote, ote_dir = smc.get("ote"), smc.get("ote_dir")
    olo, ohi = smc.get("ote_lo"), smc.get("ote_hi")
    # 4h OTE 概念介绍（用户定版 v1.5.59）：先讲清楚 OTE 是什么、范围与入场取位
    if olo is not None and ohi is not None:
        oe = smc.get("ote_entry") or ohi
        p.append(f"再看 4 小时的 OTE：Optimal Trade Entry（最优入场区），取这段腿斐波那契 0.62~0.79 的回撤窗口为黄金区间，一般用中间值 0.702 作入场位（当前窗口 {_fmt(olo)}~{_fmt(ohi)}，中间值约 {_fmt(oe)}）")
    if ote == "inside" and ote_dir == "up":
        p.append(f"价格正落在最近上涨腿的 OTE 窗口（{_fmt(olo)}~{_fmt(ohi)}）里，这是顺势做多的黄金回撤区")
    elif ote == "above" and ote_dir == "up":
        p.append(f"最近这段上涨还没回踩到位，OTE 窗口在 {_fmt(olo)}~{_fmt(ohi)}，追在现价等于放弃了盈亏比")
    elif ote == "below" and ote_dir == "up":
        p.append(f"但这波回调已经跌穿 OTE 下沿 {_fmt(olo) if olo else '-'}，回撤比正常回踩深，短期动能转弱，多头要等重新收回窗口再算数")
    elif ote == "inside" and ote_dir == "down":
        p.append(f"价格正反抽到最近下跌腿的空头 OTE 窗口（{_fmt(olo)}~{_fmt(ohi)}），这是顺势做空的黄金反抽区")
    elif ote == "above" and ote_dir == "down":
        p.append(f"空头 OTE 窗口上沿 {_fmt(ohi) if ohi else '-'} 已被涨破，这段下跌腿的空头逻辑失效")
    elif ote == "below" and ote_dir == "down":
        p.append(f"价格还压在空头 OTE 下沿 {_fmt(olo) if olo else '-'} 下方，离最佳做空位太远，追空不划算")

    ob = smc.get("ob") or {}
    if ob.get("dir") == "bull" and price:
        if ob.get("low") <= price <= ob.get("high"):
            rel = "现价就踩在这个区里"
        elif price / ob["high"] > 1.25:      # 老底远在天下（暴涨币）：如实标注参考意义有限
            rel = f"距现价 -{(1 - ob['high'] / price) * 100:.0f}%，是很远的老底，短期承接参考意义有限"
        else:
            rel = "回踩这个区看承接反应"
        p.append(f"下方最近的多头 OB（需求区）在 {_fmt(ob['low'])}~{_fmt(ob['high'])}，{rel}")
    elif ob.get("dir") == "bear" and price:
        if ob.get("low") <= price <= ob.get("high"):
            rel = "现价就顶在这个区里"
        elif ob["low"] / price > 1.25:
            rel = f"距现价 +{(ob['low'] / price - 1) * 100:.0f}%，是很远的老顶，短期压制参考意义有限"
        else:
            rel = "反弹到这个区看压制反应"
        p.append(f"上方最近的空头 OB（供给区）在 {_fmt(ob['low'])}~{_fmt(ob['high'])}，{rel}")

    for g in smc.get("fvg") or []:
        gl, gh = g["lo"], g["hi"]
        if gh < price:
            p.append(f"下方 {_fmt(gl)}~{_fmt(gh)} 还留着个没回补的多头 FVG（公允价值缺口），价格常会回头填缺，跟 OB 重合的话接力概率更高")
        elif gl > price:
            p.append(f"上方 {_fmt(gl)}~{_fmt(gh)} 留着个没回补的空头 FVG（公允价值缺口），反弹填缺时容易遇阻，是减仓位不是追仓位")

    if ma20 and price:
        if price > ma20:
            p.append("4h 站在 MA20 上方，短线动能是配合的")
        else:
            p.append(f"4h 价格还在 MA20（{_fmt(ma20)}）下方，短线动能暂时跟不上结构")

    # 倾向结论要与上文自洽（v1.5.60 修，牛来翻车：上文「4h 多头结构没坏」下一句直接
    # 「几条对得上，倾向偏空」——日线 -3 压过 4h +1 的分歧必须说破，不能装作几条对得上）
    if bias == "short" and st == "bullish":
        tail = "4h 结构虽然还偏多，但日线大趋势那边的分量更重（权重 3:1），方向上我站偏空：按下面计划挂单等，不追空"
    elif bias == "long" and st == "bearish":
        tail = "4h 结构虽然还偏空，但日线大趋势那边的分量更重（权重 3:1），方向上我站偏多：按下面计划分批做，不追价"
    elif bias == "neutral" and st == "bullish":
        tail = "4h 结构虽然偏多，但日线那边没跟上、其他信号也互相打架，方向上我倾向观望：宁可错过，不做看不懂的"
    elif bias == "neutral" and st == "bearish":
        tail = "4h 结构虽然偏空，但日线那边没跟上、其他信号也互相打架，方向上我倾向观望：宁可错过，不做看不懂的"
    else:
        tail = {"long": "几条对得上，我的倾向是偏多：按下面计划分批做，不追价",
                "short": "几条对得上，我的倾向是偏空：按下面计划挂单等，不追空",
                "neutral": "多空信号拧在一起，谁也说服不了谁，我的倾向就是观望：宁可错过，不做看不懂的"}[bias]
    p.append(tail)
    return p


# ---------------- 风格库：同一份事实，四种说法 ----------------
# 用户 2026-09-12 要求「每次的风格换一换试试」。原则：**只换叙述，不换数字** ——
# 方向、点位、仓位、反向剧本全部出自 _facts() 的同一份结果，四套风格只是换说法，
# 避免「换个写法把止损也换了」这种事故。
STYLES = ("review", "diary", "qa", "blunt")
STYLE_LABELS = {"review": "冷静复盘体", "diary": "交易员日记体",
                "qa": "自问自答体", "blunt": "直给结论体"}


def _facts(stat: dict) -> dict:
    """把所有事实算一遍，四套风格共用 —— 换风格绝不换数字。"""
    sym = stat["symbol"]
    base = sym[:-4] if sym.endswith("USDT") else sym
    k = stat.get("k90") or {}
    c = k.get("closes") or []
    price = c[-1] if c else None
    chg24 = stat.get("change_pct")
    if chg24 is None and stat.get("c24") and len(stat["c24"]) > 1:
        chg24 = _pct(stat["c24"][-1], stat["c24"][0])
    chg90 = _pct(c[-1], c[0]) if len(c) > 1 else None
    lo = min(k["lows"]) if k.get("lows") else None
    hi = max(k["highs"]) if k.get("highs") else None
    bias, why, smc = _bias(stat)
    return {"sym": sym, "base": base, "price": price, "chg24": chg24, "chg90": chg90,
            "lo90": lo, "hi90": hi, "bias": bias, "reasons": why, "smc": smc,
            "human": _human_story(stat, smc), "market_bits": _market_bits(stat),
            "view": _view_story(stat, bias, smc), "plan": _plan(stat, bias, smc),
            "invalid": _invalid_line(stat, bias, smc), "news": _news_picks(stat)}


def _pick_style(style: str = None) -> str:
    """显式指定就用指定值，否则每次随机抽一个（「换一换」）。"""
    if style in STYLES:
        return style
    return random.choice(STYLES)


def _quote(f: dict) -> str:
    s = f"${f['base']} 现价 {_fmt(f['price'])} USDT"
    if f["chg24"] is not None:
        s += f"，24h {f['chg24']:+.2f}%"
    return s + "。"


def _footer(base: str) -> list:
    # v1.5.40：按用户要求去掉「封面图是 90 日 K线加成交量…」那行说明 —— 多余。
    # v1.5.45：K 线周期可能降级（1d→4h→1h），页脚不再写死「90d 日线」。
    return ["",
            "—— BAZZ.AGENT 自动生成｜数据源：币安公开行情（4h SMC 结构 + K线/费率/OI/恐惧贪婪/公开新闻）",
            "项目开源：https://github.com/xinyuzjj/bazz.agent （觉得有用去点个 Star）"]


def _tags(base: str) -> list:
    tags = ["#行情分析", "#币安广场"]
    ch = _CHAIN_TAG.get(base)
    if ch:
        tags.insert(0, f"#{ch}")
    return tags


def _sentences(text: str) -> list:
    """按句号拆开，给日记体用 —— 一句一行，节奏立刻就不一样。"""
    if not text:
        return []
    return [s.strip() for s in re.split(r"(?<=[。！？])", text) if s.strip()]


def _time_of_day() -> str:
    h = time.localtime().tm_hour
    if 5 <= h < 11:
        return "早上"
    if 11 <= h < 13:
        return "中午"
    if 13 <= h < 18:
        return "下午"
    return "晚上"


def _split_view(view: list) -> dict:
    """把「我的看法」按内容归类：结构 / 位置(OTE·OB·FVG) / 动能(MA20) / 结论。

    自问自答体按这个分组来提问，直给结论体按它挑「理由」。
    """
    out = {"struct": [], "pos": [], "mom": [], "tail": ""}
    if not view:
        return out
    body, out["tail"] = view[:-1], view[-1]
    for x in body:
        if "MA20" in x:
            out["mom"].append(x)
        elif "OB" in x or "OTE" in x or "FVG" in x:
            out["pos"].append(x)
        else:
            out["struct"].append(x)
    return out


def _pick_title(f: dict, smc: dict, bias: str) -> str:
    """标题池（v1.5.47e，按爆款标题公式重写：数字具体化/痛点拷问/悬念留白/
    对比反差/身份共鸣/犀利引语/反常识，参考 CoinTelegraph 被点名的
    「数字+亲历教训」价值式；适度悬念 > 直白 > 夸张，且不承诺收益保合规）。

    选择顺序：结构事件标题（前低扫荡 / 假突破 / OTE / OB 压顶·托底）命中时以 60% 概率
    优先选用 —— 事件标题带具体细节，天然比泛标题抓人；其余从方向大池随机抽，
    保证同一币种连发多篇不重样。所有模板都自带 {base}，不会张冠李戴。
    """
    base = f["base"]

    events: list = []
    sweep = smc.get("sweep")
    if sweep == "bullish":
        events += [f"{base}：一根下影线扫掉所有止损，然后呢",
                   f"{base} 那次「崩盘」是假的，看懂的人已经在计划里",
                   f"{base}：前低被扫又收回，这个细节值得注意"]
    if sweep == "bearish":
        events += [f"{base}：冲破前高那几分钟，套住了多少人",
                   f"{base} 的假突破刚收完门票，下一幕才是重点",
                   f"{base}：假突破之后一地鸡毛，先别接"]
    if smc.get("ote") == "inside" and bias == "long":
        events += [f"{base}：OTE 0.702 中间值到了，这单我这么排",
                   f"{base} 踩进黄金坑？先看完这 3 个条件再说"]
    ob = smc.get("ob")
    if ob and ob.get("low") is not None and ob.get("high") is not None:
        if bias == "long" and ob["low"] < f["price"]:
            events += [f"{base} 下方那个多头 OB，是我等的上车点",
                       f"{base}：头顶下方留了个多头 OB，我把单挂那儿了"]
        elif bias == "short" and ob["high"] > f["price"]:
            events += [f"{base} 头顶那块空头 OB：位置、止损、计划一次说清",
                       f"{base}：上方压着个空头 OB，反弹我就动"]

    pool = {
        "long": [
            # 数字+结果
            f"{base}：3 个理由让我只做多这一边",
            f"在 {base} 上亏过 3 次之后，我终于学会了等",
            # 痛点拷问
            f"为什么你总在 {base} 上追高？差的就是这一步",
            # 反常识
            f"{base} 跌了，但我比上周更想买",
            # 悬念留白
            f"{base} 我挂了一个单，位置可能和你想的不一样",
            # 对比反差
            f"同样买 {base}，有人追涨有人等回踩，差别在哪",
            # 身份共鸣
            f"写给在 {base} 上站过岗的人：这次把计划做对",
            # 时间紧迫
            f"{base}：4 小时内我要盯的两个位置",
            # 犀利引语
            f"「现价直接追 {base}？」我劝你先看完这篇",
            # 保留原池里数据反馈好的直给型
            f"{base}：偏多，但别追现价",
            f"{base}：折价区里我只想做一件事——挂单等",
            f"{base} 我盯了大半天，还是想等那个位置",
            f"{base}：多头剧本我已经写好，就差行情配合",
            f"{base} 跌下来我反而来精神，原因在这",
        ],
        "short": [
            # 数字+结果
            f"{base}：反弹越猛我越冷静，3 个理由在这",
            f"上一波 {base} 的顶我错过了，这次不想再用感觉",
            # 痛点拷问
            f"追多 {base} 的人，你的止损打算放哪？",
            f"为什么你不敢空 {base}？大概率是这 2 个误区",
            # 反常识
            f"{base} 涨了，但我今天只考虑一件事",
            # 悬念留白
            f"{base}：我等的位置快到了，单子已经挂好",
            # 对比反差
            f"别人追 {base} 的时候，我在数它头顶的压力位",
            # 犀利引语
            f"「{base} 永远涨」？4 小时图刚说了句实话",
            # 时间/历史
            f"{base} 假突破之后通常发生什么？答案在历史里",
            # 保留原池直给型
            f"{base}：偏空，等反弹再挂",
            f"{base}：冲高别追，那是给空单送流动性",
            f"{base} 今天这波我没接，理由写在这",
            f"{base}：与其抄在半山腰，不如等结构说话",
            f"{base}：现在喊多的人不少，我偏要泼盆冷水",
        ],
        "neutral": [
            # 数字+干货
            f"{base} 看不懂的时候，我最常做的 3 件事",
            f"{base}：方向没出来前，先做好这 1 件小事",
            # 痛点拷问
            f"为什么老手在 {base} 上反而赚得少？",
            f"{base} 多空都在喊，你该听谁的？",
            # 身份共鸣
            f"在 {base} 上亏钱的人，多半败在等不了",
            # 反常识
            f"{base}：今天不交易，也是交易计划的一部分",
            # 悬念留白
            f"{base} 现在的位置很微妙，我说说我在等什么",
            # 保留原池直给型
            f"{base}：多空信号在打架，先别急着下场",
            f"{base} 今天纯看戏，没等到想下手的点",
            f"{base}：没方向，先空着等",
            f"{base}：方向不明的时候，纪律最值钱",
            f"{base}：不动手的日子，把计划先写好",
            f"{base} 在区间里晃悠，两头的价都记一下",
        ],
    }[bias]
    if events and random.random() < 0.6:
        return random.choice(events)
    return random.choice(pool)


def _style_review(f: dict, stat: dict) -> tuple:
    """冷静复盘体：事实 / 情绪面 / 观点 / 计划 分栏陈述（原版风格）。"""
    base, bias, smc = f["base"], f["bias"], f["smc"]
    title = _pick_title(f, smc, bias)

    L = [_quote(f), "", f["human"], ""]
    mb = "；".join(f["market_bits"])
    if mb:
        head = "合约和情绪面放在一块说：" if stat.get("market") == "futures" else "情绪面上："
        L += [head + mb + "。", ""]
    if f["news"]:
        L += [_news_sentence(f["news"]), ""]
    L.append("我的看法（4 小时 SMC 视角，技术面推测不构成建议）：")
    L += [f"· {w.rstrip('。')}。" for w in f["view"]]
    L += ["", "真要动手的话，我是这么安排的（100U 本金、10x 杠杆举例；止损只看 SMC 结构失效位，仅演示算法）："]
    L += f["plan"]
    L.append(f["invalid"])
    L += ["", "仓位比观点重要，活着比赚钱重要。以上全是个人思路，不构成投资建议，DYOR。"]
    return title, L + _footer(base)


def _style_diary(f: dict, stat: dict) -> tuple:
    """交易员日记体：第一人称、一句一行、有盯盘的时间感。"""
    base, bias = f["base"], f["bias"]
    title = _pick_title(f, f["smc"], bias)
    head = f"今天{_time_of_day()}一直在看 ${base}。现价 {_fmt(f['price'])} USDT"
    if f["chg24"] is not None:
        head += f"，24h {f['chg24']:+.2f}%"
    L = [head + "。", "", "盘面是这样："]
    L += _sentences(f["human"])
    mb = "；".join(f["market_bits"])
    if mb:
        L += ["", "合约和情绪那边，我扫了一眼：", mb + "。"]
    if f["news"]:
        L += ["", "刷新闻的时候也留意了一下：", _news_sentence(f["news"])]
    L += ["", "我自己是这么打算的："]
    L += f["plan"]
    L.append(f["invalid"])
    L += ["", "以上是我自己的盘感，不是喊单。真金白银的事，自己拿主意。"]
    return title, L + _footer(base)


def _style_qa(f: dict, stat: dict) -> tuple:
    """自问自答体：把读者会问的问题一个个摆出来回答。"""
    base, bias = f["base"], f["bias"]
    title = _pick_title(f, f["smc"], bias)
    v = _split_view(f["view"])
    L = [_quote(f), ""]

    def qa(q: str, a: str):
        L.append(q)
        L.append(a)
        L.append("")

    struct = " ".join(v["struct"]).strip()
    qa("方向到底偏哪边？",
       ((struct + " " if struct else "") + v["tail"] + "。") if v["tail"] else "结构没给出方向，接着看。")
    qa("现在这个位置，追进去划算吗？",
       ("；".join(v["pos"]) + "。") if v["pos"] else "位置不上不下，没有特别好的进场点，等。")
    if v["mom"]:
        qa("短线动能配合吗？", "；".join(v["mom"]) + "。")
    mb = "；".join(f["market_bits"])
    qa("合约和情绪面呢？", (mb + "。") if mb else "这块今天没什么可说的，略过。")
    if f["news"]:
        qa("消息面在说什么？", _news_sentence(f["news"]))
    qa("那具体怎么下手？", "（100U 本金、10x 杠杆举例；止损只看 SMC 结构失效位，仅演示算法）\n" + "\n".join(f["plan"]))
    qa("什么情况算你看错了？", f["invalid"])
    L += ["问完了。不构成投资建议，DYOR。"]
    return title, L + _footer(base)


def _style_blunt(f: dict, stat: dict) -> tuple:
    """直给结论体：开头三句给判断，再补理由；短句、零铺垫。"""
    base, bias = f["base"], f["bias"]
    concl = {"long": "偏多，但不追现价。", "short": "偏空，等反弹挂单，不追空。",
             "neutral": "没方向，空着等。"}[bias]
    title = _pick_title(f, f["smc"], bias)
    L = [f"${base} {_fmt(f['price'])}"
         + (f"（24h {f['chg24']:+.2f}%）" if f["chg24"] is not None else "") + "。",
         "",
         f"结论：{concl}", ""]
    if f["chg90"] is not None and f["lo90"] is not None:
        L += [f"背景：90 日 {f['chg90']:+.0f}%，区间 {_fmt(f['lo90'])} ~ {_fmt(f['hi90'])}。", ""]
    v = _split_view(f["view"])
    reasons = list(v["struct"]) + v["pos"][:1] + v["mom"][:1]
    if reasons:
        L.append("理由：")
        for i, r in enumerate(reasons[:3], 1):
            L.append(f"{i}. {r.rstrip('。')}。")
    mb = "；".join(f["market_bits"])
    if mb:
        L += ["", f"盘外：{mb}。"]
    if f["news"]:
        L += ["", "消息：" + _news_sentence(f["news"])]
    L += ["", "方案（100U 本金、10x 杠杆举例；止损只看 SMC 结构失效位，仅演示算法）："]
    L += f["plan"]
    L.append(f["invalid"])
    L += ["", "不构成投资建议。"]
    return title, L + _footer(base)


_RENDER = {"review": _style_review, "diary": _style_diary,
           "qa": _style_qa, "blunt": _style_blunt}


def _article_full(stat: dict, style: str = None) -> dict:
    """完整发文素材：title/body/tags + 命中的风格（compose 记进 meta 便于追溯）。"""
    f = _facts(stat)
    st = _pick_style(style)
    title, lines = _RENDER[st](f, stat)
    return {"title": title, "body": "\n".join(lines), "tags": _tags(f["base"]),
            "style": st, "style_label": STYLE_LABELS[st]}


def _article(stat: dict, style: str = None) -> tuple:
    """(title, body, tags)。SMC 定方向，点位仓位给方案；风格可指定，不指定则随机轮换。

    事实与数字由 _facts() 统一产出，四种风格只是换说法 —— v1.5.39 那条
    「反向剧本必须复用 _plan_levels() 的同一个止损」的约束在所有风格下都成立。
    """
    a = _article_full(stat, style)
    return a["title"], a["body"], a["tags"]


# ---------------- 入口 ----------------

def compose(symbol: str, market: str = "futures", style: str = None) -> dict:
    """合成富媒体发文素材，返回 {ok, dir, title_file, text_file, cover, extra_chart, stats, tags, style}。

    style 不指定则四套风格随机抽一个；显式传 STYLES 里的值可锁定（回归测试用）。
    """
    if Image is None:
        return {"ok": False, "error": "PIL 不可用，无法生成图表"}
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return {"ok": False, "error": "缺少 SYMBOL"}
    stat = _collect(symbol, market)
    if not (stat.get("k90") or {}).get("closes"):
        return {"ok": False, "error": f"{symbol} 行情数据不可用（1d/4h/1h K 线均无数据，该币可能刚上线或已下架）"}

    # 消息面：取不到就整段省略，绝不因为它让文章生成失败
    try:
        from scanner import coin_news
        stat["news"] = coin_news(symbol, n=10)
    except Exception:
        stat["news"] = {}

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(OUT_ROOT, f"{symbol}_{ts}")
    os.makedirs(out_dir, exist_ok=True)

    cover = draw_cover(stat, os.path.join(out_dir, "cover.png"))
    try:
        extra = draw_24h(stat, os.path.join(out_dir, "chart_24h.png"))
    except Exception:
        extra = ""
    # v1.5.55：4h 结构图（SMC 矩形区间框只标在这张，日线封面不标）
    try:
        chart_4h = draw_cover_4h(stat, os.path.join(out_dir, "chart_4h.png")) \
            if _k_usable(stat.get("k4h")) else ""
    except Exception:
        chart_4h = ""

    art = _article_full(stat, style)
    title, body, tags = art["title"], art["body"], art["tags"]
    title_f = os.path.join(out_dir, "title.txt")
    text_f = os.path.join(out_dir, "article.txt")
    meta_f = os.path.join(out_dir, "meta.json")
    with open(title_f, "w", encoding="utf-8") as f:
        f.write(title + "\n")
    with open(text_f, "w", encoding="utf-8") as f:
        f.write(body + "\n")

    k = stat.get("k90") or {}
    c = k.get("closes") or []
    stats = {
        "symbol": symbol, "market": stat.get("market", market), "price": c[-1] if c else None,
        "chg90": _pct(c[-1], c[0]) if len(c) > 1 else None,
        "chg24": stat.get("change_pct"), "fng": stat.get("fng"),
        "funding_rate": stat.get("funding_rate"), "oi": stat.get("oi"),
        "top_ratio": stat.get("top_ratio"), "global_ratio": stat.get("global_ratio"),
    }
    # v1.5.61 模拟挂单：把文章的 SMC 计划写进 meta.json 的 plan 节（发布成功后建单钩子读它）。
    # 观望（neutral/noplay/无点位）→ plan=null，绝不硬造方向
    plan = None
    try:
        bias, _why, smc = _bias(stat)
        lv = _plan_levels(stat, bias, smc)
        if bias in ("long", "short") and lv and not lv.get("noplay") \
                and lv.get("entry_px") and lv.get("stop"):
            plan = {"direction": bias, "entry": lv["entry_px"],
                    "zone_lo": lv.get("zone_lo"), "zone_hi": lv.get("zone_hi"),
                    "stop": lv["stop"], "tp": lv.get("tp1"), "price": lv["price"]}
    except Exception:
        plan = None
    with open(meta_f, "w", encoding="utf-8") as f:
        json.dump({"symbol": symbol, "market": market, "ts": int(time.time()),
                   "stats": stats, "tags": tags, "title": title,
                   "style": art["style"], "style_label": art["style_label"],
                   "news_count": (stat.get("news") or {}).get("count", 0),
                   "plan": plan},
                  f, ensure_ascii=False, indent=1)

    return {"ok": True, "dir": out_dir, "title_file": title_f, "text_file": text_f,
            "cover": cover, "extra_chart": extra, "chart_4h": chart_4h,
            "tags": tags, "stats": stats, "plan": plan,
            "style": art["style"], "style_label": art["style_label"]}
