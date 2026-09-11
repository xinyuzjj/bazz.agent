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
        raise RuntimeError("90 日 K 线数据不足，无法出图")
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

    # 90d 高/低虚线标注
    imax, imin = h.index(max(h)), l.index(min(l))
    for idx, val, col, lab in ((imax, h[imax], UP, f"90d 高 {_fmt(h[imax])}"),
                               (imin, l[imin], DOWN, f"90d 低 {_fmt(l[imin])}")):
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
    chips.append(("90日区间位置", f"{pos:.0f}%", ACCENT if pos > 80 else TXT))
    chips.append(("90日区间", f"{_fmt(lo)} ~ {_fmt(hi)}", TXT))
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


def _plan(stat: dict, bias: str, smc: dict = None) -> list:
    """仓位/点位方案（举例口径：100U 本金、单笔风险 5U）。点位基于 4h SMC：OB/OTE/摆动点。"""
    smc = smc or {}
    k = stat.get("k4h") or stat.get("k90") or {}
    c = k.get("closes") or []
    if len(c) < 20 or not k.get("lows"):
        return ["· 数据不足，给不出靠谱点位，宁可错过不做没把握的。"]
    price = c[-1]
    r10_lo = min(k["lows"][-10:])
    r10_hi = max(k["highs"][-10:])
    ob = smc.get("ob") or {}
    out = []

    def _size_line(stop, direction):
        dist = abs(price - stop) / price * 100
        if dist < 0.5:
            dist = 0.5
        notional = 5 / (dist / 100)          # 单笔想亏 5U → 名义 = 5 / 止损%
        margin10 = notional / 10
        tip = ""
        if margin10 > 30:
            lv = max(2, int(10 * 30 / margin10))
            tip = f"（10x 保证金要占 {margin10:.0f}U，太重，建议降到 {lv}x 左右）"
            margin10 = notional / lv
        side = "做多" if direction == "long" else "做空"
        return (f"· 仓位算法：止损位 {_fmt(stop)}（距离 {dist:.1f}%）。100U 本金、单笔亏 5U → "
                f"{side}名义 ≈ {notional:.0f}U，10x 占保证金 ≈ {margin10:.0f}U{tip}")

    if bias == "long":
        ob_lo, ob_hi = ob.get("low"), ob.get("high")
        ote_lo, ote_hi = smc.get("ote_lo"), smc.get("ote_hi")
        if ob_lo and ob_hi and ob_lo < price:
            entry = f"· 入场：优先挂 {_fmt(ob_lo)} ~ {_fmt(ob_hi)} 的多头 OB 区回踩接（限价），现价 {_fmt(price)} 直接追的盈亏比一般"
            stop = ob_lo * 0.995
        elif smc.get("ote_dir") == "up" and ote_lo is not None and ote_lo < price:
            entry = f"· 入场：等回踩 OTE 窗口 {_fmt(ote_lo)} ~ {_fmt(ote_hi)}（斐波那契 0.618-0.705）分批接"
            stop = ote_lo * 0.99
        else:
            entry = f"· 入场：现价 {_fmt(price)} 附近轻仓试，或等 4h 回踩 {_fmt(r10_lo*0.995)}（近 10 根低点下方）确认支撑"
            stop = r10_lo * 0.99
        tp1 = smc["sh_v"][-1] if smc.get("sh_v") else max(k["highs"][-30:])
        out += [entry,
                f"· 止盈：第一目标 {_fmt(tp1)}（4h 前高/摆动高点）先减半，破位续持有看日线级别空间",
                ]
        out.append(_size_line(stop, "long"))
    elif bias == "short":
        ob_lo, ob_hi = ob.get("low"), ob.get("high")
        if ob_hi and ob_hi > price:
            entry = f"· 入场：优先挂 {_fmt(ob_lo)} ~ {_fmt(ob_hi)} 的空头 OB 区反弹接（限价），不追空"
            stop = ob_hi * 1.005
        else:
            entry = f"· 入场：反弹到 {_fmt(r10_hi*1.005)}（近 10 根高点上方）再空，不追空"
            stop = r10_hi * 1.01
        tp1 = smc["sl_v"][-1] if smc.get("sl_v") else min(k["lows"][-30:])
        out += [entry,
                f"· 止盈：第一目标 {_fmt(tp1)}（4h 前低/摆动低点）先减半，破位续持有",
                ]
        out.append(_size_line(stop, "short"))
    else:
        out += [
            "· 观望为主：多空信号打架时，不进场就是最好的仓位。",
            f"· 若非要动：向上突破 {_fmt(r10_hi*1.01)} 小仓跟多 / 跌破 {_fmt(r10_lo*0.99)} 小仓跟空，"
            "严格止损，仓位按「单笔亏 5U ÷ 止损距离%」反推名义。",
        ]
    return out


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
    if chg90 <= -15:
        p.append(f"{_fmt_lo_hi(hi_t)}冲到 {_fmt(hi)} 之后就没像样地反攻过，90 日下来 {chg90:.0f}%，低点 {_fmt(lo)} 落在 {lo_t or '区间后段'}。")
    elif chg90 >= 30:
        p.append(f"这 90 日整体是往上走的，涨了 {chg90:.0f}%，{_fmt_lo_hi(hi_t)}触到 {_fmt(hi)}，低点 {_fmt(lo)} 是 {lo_t or '早段'}的事。")
    else:
        p.append(f"这 90 日基本就是 {_fmt(lo)} 到 {_fmt(hi)} 之间来回，目前 {chg90:+.0f}%，谈不上单边。")
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


def _market_story(stat: dict) -> str:
    """衍生品 + 情绪，融成一小段叙述。"""
    s = []
    if stat["market"] == "futures":
        fr = stat.get("funding_rate")
        if fr is not None:
            frc = float(fr) * 100
            mood = ("多头不拥挤" if frc < 0.05 else
                    "多头有点拥挤，随时可能插针" if frc < 0.15 else "费率很热，杠杆情绪已经极端")
            s.append(f"合约这边，资金费率 {frc:+.4f}%，{mood}")
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
    if not s:
        return ""
    head = "合约和情绪面放在一块说：" if stat["market"] == "futures" else "情绪面上："
    return head + "；".join(s) + "。"


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


def _article(stat: dict) -> tuple:
    """(title, body, tags)。叙述式人话文风：SMC 定方向，点位仓位给方案，事实与推测分栏。"""
    sym = stat["symbol"]
    base = sym[:-4] if sym.endswith("USDT") else sym
    k = stat.get("k90") or {}
    c = k.get("closes") or []
    price = c[-1] if c else None
    chg24 = stat.get("change_pct")
    if chg24 is None and stat.get("c24") and len(stat["c24"]) > 1:
        chg24 = _pct(stat["c24"][-1], stat["c24"][0])
    bias, why, smc = _bias(stat)

    title = {"long": f"{base}：4 小时结构在转多，说说我的打算",
             "short": f"{base}：反弹一个比一个矮，空头还没放手",
             "neutral": f"{base}：多空信号在打架，先别急着下场"}[bias]
    if smc.get("sweep") == "bullish":
        title = f"{base}：4 小时前低被扫又收回，这个细节值得注意"
    elif smc.get("sweep") == "bearish":
        title = f"{base}：4 小时假突破之后一地鸡毛，先别接"
    elif smc.get("ote") == "inside" and bias == "long":
        title = f"{base}：4 小时回踩进 OTE 窗口，我盯上了"

    lo, hi = (min(k["lows"]), max(k["highs"])) if k.get("lows") else (None, None)
    lines = [
        f"${base} 现价 {_fmt(price)} USDT" + (f"，24h {chg24:+.2f}%。" if chg24 is not None else "。"),
        "",
        _human_story(stat, smc),
        "",
    ]
    mk = _market_story(stat)
    if mk:
        lines += [mk, ""]
    lines.append("我的看法（4 小时 SMC 视角，技术面推测不构成建议）：")
    for w in _view_story(stat, bias, smc):
        lines.append(f"· {w.rstrip('。')}。")
    lines.append("")
    lines.append("真要动手的话，我是这么安排的（举例 100U 本金，仅演示算法）：")
    lines += _plan(stat, bias, smc)
    if bias != "short":
        lines.append(f"· 反向剧本：4h 收盘跌破 {_fmt(lo*0.995) if lo else '关键支撑'}（90 日低点下方）且收不回来，上面这些多头逻辑全部作废，砍仓别犹豫。")
    else:
        lines.append("· 反向剧本：哪天放量收复 MA20 并站稳，空头逻辑作废，及时认错不丢人。")
    lines.append("")
    lines += ["仓位比观点重要，活着比赚钱重要。以上全是个人思路，不构成投资建议，DYOR。",
              "",
              "—— BAZZ.AGENT 自动生成｜数据源：币安公开行情（4h SMC 结构 + 90d 日线/费率/OI/恐惧贪婪）",
              "项目开源：https://github.com/xinyuzjj/bazz.agent （觉得有用去点个 Star）",
              f"封面图是 90 日 K线加成交量，文中 4h 点位可在币安 App 切 4 小时图对照，${base}"]

    tags = ["#行情分析", "#币安广场"]
    ch = _CHAIN_TAG.get(base)
    if ch:
        tags.insert(0, f"#{ch}")
    return title, "\n".join(lines), tags


# ---------------- 入口 ----------------

def compose(symbol: str, market: str = "futures") -> dict:
    """合成富媒体发文素材，返回 {ok, dir, title_file, text_file, cover, extra_chart, stats, tags}。"""
    if Image is None:
        return {"ok": False, "error": "PIL 不可用，无法生成图表"}
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return {"ok": False, "error": "缺少 SYMBOL"}
    stat = _collect(symbol, market)
    if not (stat.get("k90") or {}).get("closes"):
        return {"ok": False, "error": f"{symbol} 行情数据不可用（90d K 线为空）"}

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(OUT_ROOT, f"{symbol}_{ts}")
    os.makedirs(out_dir, exist_ok=True)

    cover = draw_cover(stat, os.path.join(out_dir, "cover.png"))
    try:
        extra = draw_24h(stat, os.path.join(out_dir, "chart_24h.png"))
    except Exception:
        extra = ""

    title, body, tags = _article(stat)
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
                   "stats": stats, "tags": tags, "title": title}, f, ensure_ascii=False, indent=1)

    return {"ok": True, "dir": out_dir, "title_file": title_f, "text_file": text_f,
            "cover": cover, "extra_chart": extra, "tags": tags, "stats": stats}
