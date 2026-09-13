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
    d["k4h"] = klines_ohlcv(sym, "4h", 120, market)   # SMC 主分析周期：4 小时
    d["c24"] = klines_closes(sym, "1h", 25, market)
    if market == "futures" and (_flat(d["k90"]) or _flat({"closes": d["c24"]})):
        # 合约盘占位脏数据（如 RAYUSDT SETTLING）：回退现货真实行情，衍生品维度自动省略
        d["k90"] = klines_ohlcv(sym, "1d", 90, "spot")
        d["k4h"] = klines_ohlcv(sym, "4h", 120, "spot")
        d["c24"] = klines_closes(sym, "1h", 25, "spot")
        d["market"] = "spot"
        d["fallback"] = "futures_flat→spot"
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


def _draw_header(dr, W, sym, market, price, chg, sub_right=""):
    dr.rectangle([0, 0, W, 84], fill=PANEL)
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
    """底部信息条：[(label, value, color), ...] 均分排布。"""
    y = H - 76
    dr.rectangle([0, y - 12, W, H], fill=PANEL)
    n = len(chips)
    seg = W / max(n, 1)
    for i, (lab, val, color) in enumerate(chips):
        x = seg * i + 18
        dr.text((x, y + 2), lab, font=_font(13), fill=SUB)
        dr.text((x, y + 26), val, font=_font(17, True), fill=color or TXT)


def draw_cover(stat: dict, path: str) -> str:
    """封面主图 1280x720：90d 蜡烛 + 成交量 + 关键位标注 + 底部信息条。"""
    W, H = 1280, 720
    k = stat.get("k90") or {}
    o, h, l, c, v, t = (k.get("opens") or [], k.get("highs") or [], k.get("lows") or [],
                        k.get("closes") or [], k.get("vols") or [], k.get("times") or [])
    if len(c) < 5:
        raise RuntimeError("K 线数据不足（1d/4h/1h 均无可用数据），无法出图")
    # v1.5.45：降级周期时图上「90d 高/低」「90日区间」等文案跟随实际周期
    lbl = stat.get("k90_label") or "90日"
    img = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(img)

    price = c[-1]
    chg = stat.get("change_pct")
    if chg is None and len(c) > 1:
        chg = _pct(price, c[-2])
    _draw_header(dr, W, stat["symbol"], stat["market"], price, chg,
                 sub_right=time.strftime("%Y-%m-%d %H:%M %Z", time.localtime()).replace(" +0800", " UTC+8"))

    # —— 蜡烛区 —— #
    x0, x1 = 16, W - 96          # 右侧留价格轴
    y0, y1 = 104, H - 320
    lo, hi = min(l), max(h)
    pad = (hi - lo) * 0.06 or hi * 0.01
    lo_p, hi_p = lo - pad, hi + pad
    n = len(c)
    step = (x1 - x0) / n
    bw = max(2, int(step * 0.62))

    def py(p):
        return y1 - (p - lo_p) / (hi_p - lo_p) * (y1 - y0)

    # 横向网格 + 价格轴
    for i in range(5):
        gy = y0 + (y1 - y0) * i / 4
        dr.line([x0, gy, x1, gy], fill=GRID, width=1)
        gp = hi_p - (hi_p - lo_p) * i / 4
        dr.text((x1 + 10, gy - 8), _fmt(gp), font=_font(13), fill=SUB)

    # 区间高/低虚线标注（降级周期时标注实际覆盖范围）
    imax, imin = h.index(max(h)), l.index(min(l))
    for idx, val, col, lab in ((imax, h[imax], UP, f"{lbl} 高 {_fmt(h[imax])}"),
                               (imin, l[imin], DOWN, f"{lbl} 低 {_fmt(l[imin])}")):
        cx = x0 + step * idx + step / 2
        dr.line([x0, py(val), x1, py(val)], fill=col, width=1)
        lw = dr.textlength(lab, font=_font(13))
        lx = min(max(cx - lw / 2, x0), x1 - lw - 8)
        dr.text((lx, py(val) - 20 if idx == imax else py(val) + 6), lab, font=_font(13), fill=col)

    for i in range(n):
        cx = x0 + step * i + step / 2
        up = c[i] >= o[i]
        col = UP if up else DOWN
        dr.line([cx, py(h[i]), cx, py(l[i])], fill=col, width=1)
        top, bot = py(max(o[i], c[i])), py(min(o[i], c[i]))
        if bot - top < 1.2:
            bot = top + 1.2
        dr.rectangle([cx - bw / 2, top, cx + bw / 2, bot], fill=col)

    # 现价标记（右侧轴）
    dr.line([x0, py(price), x1 + 4, py(price)], fill=ACCENT, width=1)
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

    # —— 成交量区 —— #
    vy0, vy1 = y1 + 28, H - 100
    vmax = max(v) or 1
    for i in range(n):
        cx = x0 + step * i + step / 2
        vh = (v[i] / vmax) * (vy1 - vy0)
        col = UP if c[i] >= o[i] else DOWN
        dr.rectangle([cx - bw / 2, vy1 - vh, cx + bw / 2, vy1], fill=col)
    dr.text((x1 - 64, vy0 + 2), "成交量", font=_font(12), fill=SUB)

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


def draw_24h(stat: dict, path: str) -> str:
    """24h 分时图 1280x640：小时收盘折线 + 面积 + 高低点标注。"""
    W, H = 1280, 640
    cl = stat.get("c24") or []
    if len(cl) < 5:
        raise RuntimeError("24h 数据不足，无法出图")
    img = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(img)
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
        dr.line([x0, gy, x1, gy], fill=GRID, width=1)
        gp = hi_p - (hi_p - lo_p) * i / 4
        dr.text((x1 + 10, gy - 8), _fmt(gp), font=_font(13), fill=SUB)

    col = UP if cl[-1] >= cl[0] else DOWN
    pts = [(px(i), py(cl[i])) for i in range(n)]
    dr.polygon([(x0, y1)] + pts + [(x1, y1)], fill=(col[0] // 7 + 8, col[1] // 7 + 8, col[2] // 7 + 8))
    dr.line(pts, fill=col, width=3, joint="curve")

    imax, imin = cl.index(hi), cl.index(lo)
    for idx, val, lab, dy in ((imax, hi, f"高 {_fmt(hi)}", -22), (imin, lo, f"低 {_fmt(lo)}", 8)):
        cx = px(idx)
        dr.ellipse([cx - 4, py(val) - 4, cx + 4, py(val) + 4], fill=col)
        lw = dr.textlength(lab, font=_font(13))
        dr.text((min(max(cx - lw / 2, x0), x1 - lw), py(val) + dy), lab, font=_font(13), fill=col)

    dr.line([x0, py(price), x1 + 4, py(price)], fill=ACCENT, width=1)
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
    摆动结构 / BOS・CHoCH / 流动性扫荡 / 折价溢价区 / OTE（斐波那契 0.62-0.705 最优入场区）。
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

    # OTE（Optimal Trade Entry）：最近一段摆动腿的斐波那契 0.618-0.705 回撤窗口；
    # 无可用腿时退化为全区间口径
    leg_dir, leg_a, leg_b = "up", lo, hi
    if sh and sl and sh[-1] != sl[-1]:
        if sl[-1] < sh[-1]:          # 低点在前、高点在后 → 上升腿
            leg_dir, leg_a, leg_b = "up", sl_v[-1], sh_v[-1]
        else:                        # 高点在前、低点在后 → 下降腿
            leg_dir, leg_a, leg_b = "down", sh_v[-1], sl_v[-1]
    rng_leg = leg_b - leg_a
    if rng_leg > 0:
        if leg_dir == "up":
            ote_lo, ote_hi = leg_b - rng_leg * 0.705, leg_b - rng_leg * 0.618
            out["ote_dir"] = "up"
            if ote_lo <= price <= ote_hi:
                out["ote"] = "inside"
                out["ote_txt"] = f"价恰好在 OTE 多头窗口（{_fmt(ote_lo)}~{_fmt(ote_hi)}，{_fmt(leg_a)}→{_fmt(leg_b)} 这段腿的 0.618-0.705 回撤），盈亏比最优"
            elif price < ote_lo:
                out["ote"] = "below"
                out["ote_txt"] = f"价已跌穿 OTE 下沿 {_fmt(ote_lo)}，这段腿的多头窗口失守"
            else:
                out["ote"] = "above"
                out["ote_txt"] = f"价还在 OTE 上沿 {_fmt(ote_hi)} 上方，等回踩到 {_fmt(ote_lo)}~{_fmt(ote_hi)} 再接更划算"
        else:
            ote_lo, ote_hi = leg_a + rng_leg * 0.618, leg_a + rng_leg * 0.705
            out["ote_dir"] = "down"
            if ote_lo <= price <= ote_hi:
                out["ote"] = "inside"
                out["ote_txt"] = f"价正回抽到空头 OTE 窗口（{_fmt(ote_lo)}~{_fmt(ote_hi)}，{_fmt(leg_b)}→{_fmt(leg_a)} 这段腿的 0.618-0.705 反抽位），做空的盈亏比最优"
            elif price > ote_hi:
                out["ote"] = "above"
                out["ote_txt"] = f"价已升破空头 OTE 上沿 {_fmt(ote_hi)}，这段腿的空头窗口失守"
            else:
                out["ote"] = "below"
                out["ote_txt"] = f"价还在空头 OTE 下沿 {_fmt(ote_lo)} 下方，等反抽到 {_fmt(ote_lo)}~{_fmt(ote_hi)} 再空更划算"
        out["ote_lo"], out["ote_hi"] = ote_lo, ote_hi
    out["fvg"] = _find_fvg(k)
    return out


def _find_ob(k: dict, direction: str) -> dict:
    """找最近的订单块（OB）：结构方向上的最后一根 opposing K 线（被后续同向走势确认）。
    bullish：最后一根「前阴后阳」的阳线（需求区）；bearish：最后一根「前阳后阴」的阴线（供给区）。"""
    h, l, c, o = k.get("highs") or [], k.get("lows") or [], k.get("closes") or [], k.get("opens") or []
    if len(c) < 5 or len(o) != len(c):
        return {}
    n = len(c)
    for i in range(n - 2, max(n - 41, 0), -1):   # 往前扫 40 根
        try:
            if direction == "bullish":
                if c[i] > o[i] and c[i - 1] < o[i - 1] and c[-1] > c[i]:
                    return {"dir": "bull", "low": l[i], "high": h[i],
                            "txt": f"多头订单块（OB）在 {_fmt(l[i])}~{_fmt(h[i])}，回踩这里是需求接力位"}
            else:
                if c[i] < o[i] and c[i - 1] > o[i - 1] and c[-1] < c[i]:
                    return {"dir": "bear", "low": l[i], "high": h[i],
                            "txt": f"空头订单块（OB）在 {_fmt(l[i])}~{_fmt(h[i])}，反弹到这里是供给压制位"}
        except IndexError:
            break
    return {}


def _find_fvg(k: dict) -> list:
    """找未回补的 FVG（Fair Value Gap 公允价值缺口）：三根 K 中 1/3 根影线不重叠的跳区。
    回补判定：价格越过缺口中点才算填掉（影线探进去只是部分回补，仍算有效）。
    各留一个最近的：现价下方未回补多头 FVG（支撑/回补目标）、上方未回补空头 FVG（阻力/回补目标）。"""
    h, l = k.get("highs") or [], k.get("lows") or []
    if len(h) < 8 or len(l) != len(h):
        return []
    n = len(h)
    bull = bear = None
    for i in range(n - 1, max(n - 42, 1), -1):
        if bull is None and l[i] > h[i - 2]:
            lo_, hi_ = h[i - 2], l[i]
            if i < n - 1 and min(l[i + 1:]) <= (lo_ + hi_) / 2:
                pass                      # 已回补过半：跳过这条，继续往旧找
            else:
                bull = {"lo": lo_, "hi": hi_}
        if bear is None and h[i] < l[i - 2]:
            lo_, hi_ = h[i], l[i - 2]
            if i < n - 1 and max(h[i + 1:]) >= (lo_ + hi_) / 2:
                pass
            else:
                bear = {"lo": lo_, "hi": hi_}
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
    st = smc.get("structure")
    if st == "bullish":
        score += 2
        why.append(smc["structure_txt"])
    elif st == "bearish":
        score -= 2
        why.append(smc["structure_txt"])
    else:
        why.append(smc.get("structure_txt", "结构方向不明"))
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
          # entry_px：计算止损距离/盈亏比用的**入场参考价**（挂区间限价时取区中位），
          # v1.5.46 起 _size_line 从入场价算距离，不再用现价
          "entry_px": None,
          # anchor：止损的 SMC 结构锚位说明（v1.5.47）——止损只看结构失效位
          "anchor": ""}

    if bias == "long":
        if ob_lo and ob_hi and ob_lo < price:
            lv["entry"] = (f"· 入场：优先挂 {_fmt(ob_lo)} ~ {_fmt(ob_hi)} 的多头 OB 区回踩接（限价），"
                           f"现价 {_fmt(price)} 直接追的盈亏比一般")
            lv["stop"] = ob_lo * 0.995
            lv["entry_px"] = (ob_lo + ob_hi) / 2
            lv["anchor"] = f"多头 OB 下沿 {_fmt(ob_lo)} 下方 0.5% 缓冲，结构失效即离场"
        elif smc.get("ote_dir") == "up" and ote_lo is not None and ote_lo < price:
            lv["entry"] = f"· 入场：等回踩 OTE 窗口 {_fmt(ote_lo)} ~ {_fmt(ote_hi)}（斐波那契 0.618-0.705）分批接"
            lv["stop"] = ote_lo * 0.99
            lv["entry_px"] = (ote_lo + ote_hi) / 2
            lv["anchor"] = f"OTE 窗口下沿 {_fmt(ote_lo)} 下方 1% 缓冲，结构失效即离场"
        else:
            lv["entry"] = (f"· 入场：现价 {_fmt(price)} 附近轻仓试，"
                           f"或等 4h 回踩 {_fmt(r10_lo*0.995)}（近 10 根低点下方）确认支撑")
            lv["stop"] = r10_lo * 0.99
            lv["entry_px"] = price
            lv["anchor"] = "近 10 根摆动低点下方 1% 缓冲，摆动结构失效即离场"
        lv["tp1"] = smc["sh_v"][-1] if smc.get("sh_v") else max(k["highs"][-30:])
        lv["tp_txt"] = "4h 前高/摆动高点"
    elif bias == "short":
        if ob_hi and ob_hi > price:
            lv["entry"] = f"· 入场：优先挂 {_fmt(ob_lo)} ~ {_fmt(ob_hi)} 的空头 OB 区反弹接（限价），不追空"
            lv["stop"] = ob_hi * 1.005
            lv["entry_px"] = (ob_lo + ob_hi) / 2
            lv["anchor"] = f"空头 OB 上沿 {_fmt(ob_hi)} 上方 0.5% 缓冲，结构失效即离场"
        else:
            lv["entry"] = f"· 入场：反弹到 {_fmt(r10_hi*1.005)}（近 10 根高点上方）再空，不追空"
            lv["stop"] = r10_hi * 1.01
            lv["entry_px"] = price
            lv["anchor"] = "近 10 根摆动高点上方 1% 缓冲，摆动结构失效即离场"
        lv["tp1"] = smc["sl_v"][-1] if smc.get("sl_v") else min(k["lows"][-30:])
        lv["tp_txt"] = "4h 前低/摆动低点"
    return lv


def _plan(stat: dict, bias: str, smc: dict = None) -> list:
    """仓位/点位方案（v1.5.47：止损只由 SMC 结构失效位决定；仓位口径
    本金 = 保证金、10x → 名义 1000U，打止损亏损如实报数）。点位基于 4h SMC：OB/OTE/摆动点。"""
    lv = _plan_levels(stat, bias, smc)
    if not lv:
        return ["· 数据不足，给不出靠谱点位，宁可错过不做没把握的。"]
    if bias in ("long", "short"):
        # 盈亏比从入场参考价算（挂区间限价取区中位）；算不出就不写，不硬凑
        rr = ""
        if lv.get("entry_px") and lv.get("tp1"):
            risk = abs(lv["entry_px"] - lv["stop"])
            rew = abs(lv["tp1"] - lv["entry_px"])
            if risk > 0:
                rr = f"，盈亏比 ≈ {rew / risk:.1f}"
        return [lv["entry"],
                f"· 止盈：第一目标 {_fmt(lv['tp1'])}（{lv['tp_txt']}{rr}）先减半，破位续持有看日线级别空间",
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
        rel = "现价就踩在这个区里" if ob.get("low") <= price <= ob.get("high") else "回踩这个区看承接反应"
        p.append(f"下方最近的多头 OB（需求区）在 {_fmt(ob['low'])}~{_fmt(ob['high'])}，{rel}")
    elif ob.get("dir") == "bear" and price:
        rel = "现价就顶在这个区里" if ob.get("low") <= price <= ob.get("high") else "反弹到这个区看压制反应"
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


def _style_review(f: dict, stat: dict) -> tuple:
    """冷静复盘体：事实 / 情绪面 / 观点 / 计划 分栏陈述（原版风格）。"""
    base, bias, smc = f["base"], f["bias"], f["smc"]
    title = {"long": f"{base}：4 小时结构在转多，说说我的打算",
             "short": f"{base}：反弹一个比一个矮，空头还没放手",
             "neutral": f"{base}：多空信号在打架，先别急着下场"}[bias]
    if smc.get("sweep") == "bullish":
        title = f"{base}：4 小时前低被扫又收回，这个细节值得注意"
    elif smc.get("sweep") == "bearish":
        title = f"{base}：4 小时假突破之后一地鸡毛，先别接"
    elif smc.get("ote") == "inside" and bias == "long":
        title = f"{base}：4 小时回踩进 OTE 窗口，我盯上了"

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
    title = {"long": f"{base} 我盯了大半天，还是想等那个位置",
             "short": f"{base} 今天这波我没接，理由写在这",
             "neutral": f"{base} 今天纯看戏，没等到想下手的点"}[bias]
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
    title = {"long": f"{base} 现在还能追吗？我把该问的问了一遍",
             "short": f"{base} 还能空吗？几个关键问题拆开说",
             "neutral": f"{base} 该不该等？我把犹豫的点列出来"}[bias]
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
    title = {"long": f"{base}：偏多，但别追现价",
             "short": f"{base}：偏空，等反弹再挂",
             "neutral": f"{base}：没方向，先空着等"}[bias]
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
    with open(meta_f, "w", encoding="utf-8") as f:
        json.dump({"symbol": symbol, "market": market, "ts": int(time.time()),
                   "stats": stats, "tags": tags, "title": title,
                   "style": art["style"], "style_label": art["style_label"],
                   "news_count": (stat.get("news") or {}).get("count", 0)},
                  f, ensure_ascii=False, indent=1)

    return {"ok": True, "dir": out_dir, "title_file": title_f, "text_file": text_f,
            "cover": cover, "extra_chart": extra, "tags": tags, "stats": stats,
            "style": art["style"], "style_label": art["style_label"]}
