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
    return f"{v:,.5f}".rstrip("0").rstrip(".") if v else "0"


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
    d["c24"] = klines_closes(sym, "1h", 25, market)
    if market == "futures" and (_flat(d["k90"]) or _flat({"closes": d["c24"]})):
        # 合约盘占位脏数据（如 RAYUSDT SETTLING）：回退现货真实行情，衍生品维度自动省略
        d["k90"] = klines_ohlcv(sym, "1d", 90, "spot")
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

def _judgment(stat: dict) -> list:
    """规则化「判断 · 推测」——逐条基于上方事实，明确标注推测，绝不编项目背景。"""
    k = stat.get("k90") or {}
    c = k.get("closes") or []
    out = []
    if len(c) >= 10:
        price, lo, hi = c[-1], min(k["lows"]), max(k["highs"])
        pos = (price - lo) / (hi - lo) * 100 if hi > lo else 50
        if pos >= 80:
            out.append(f"现价处于 90 日区间上沿（{pos:.0f}% 分位），短线追高性价比一般，等回踩确认再评估。")
        elif pos >= 50:
            out.append(f"现价位于 90 日区间中上位（{pos:.0f}% 分位），趋势偏强但上方空间需成交量配合。")
        else:
            out.append(f"现价位于 90 日区间中下位（{pos:.0f}% 分位），安全边际相对好一些，但需确认止跌信号。")
    fr = stat.get("funding_rate")
    if fr is not None:
        frc = float(fr) * 100
        if frc >= 0.05:
            out.append(f"资金费率 {frc:+.4f}% 偏高，多头拥挤，警惕插针回调。")
        elif frc <= -0.05:
            out.append(f"资金费率 {frc:+.4f}% 为负，空头偏拥挤，反弹时易发生轧空。")
        else:
            out.append(f"资金费率 {frc:+.4f}% 处于正常区间，杠杆情绪中性。")
    if stat.get("divergence"):
        out.append("大户持仓比与散户账户比出现背离，注意大户方向通常更具参考性。")
    if not out:
        out.append("数据维度有限，方向判断需结合更多上下文。")
    return out


def _article(stat: dict) -> tuple:
    """(title, body)。事实分区 + 推测分栏 + $cashtag/#hashtag。"""
    sym = stat["symbol"]
    base = sym[:-4] if sym.endswith("USDT") else sym
    k = stat.get("k90") or {}
    c = k.get("closes") or []
    price = c[-1] if c else None
    chg24 = stat.get("change_pct")
    if chg24 is None and stat.get("c24") and len(stat["c24"]) > 1:
        chg24 = _pct(stat["c24"][-1], stat["c24"][0])
    head = f"${base} 现价 {_fmt(price)} USDT" + (f"，24h {chg24:+.2f}%。" if chg24 is not None else "。")

    lines = [head, ""]
    if stat.get("fallback"):
        lines += ["（注：该币永续合约处于结算/下架流程，本文图为现货数据）", ""]
    if len(c) >= 10:
        lo, hi = min(k["lows"]), max(k["highs"])
        pos = (price - lo) / (hi - lo) * 100 if hi > lo else 50
        dist_hi = (hi - price) / hi * 100
        dist_lo = (price - lo) / lo * 100 if lo else 0
        lines += [
            "【90日结构 · 币安公开行情】",
            f"· 90日区间 {_fmt(lo)} ~ {_fmt(hi)}，现价位于区间 {pos:.0f}% 分位",
            f"· 距 90日高点还有 {dist_hi:.1f}%，距低点已涨 {dist_lo:.1f}%",
            f"· 90日收盘价分位：{sum(1 for x in c if x <= price) / len(c) * 100:.0f}%",
            "",
        ]
    if stat["market"] == "futures":
        lines.append("【衍生品 · U本位永续】")
        fr = stat.get("funding_rate")
        if fr is not None:
            lines.append(f"· 资金费率 {float(fr)*100:+.4f}%")
        if stat.get("oi"):
            lines.append(f"· 持仓量 OI ≈ {_fmt_usd(stat['oi'])} USDT")
        if stat.get("top_ratio") is not None:
            dv = "（与散户背离）" if stat.get("divergence") else ""
            g = f"｜全球 {stat['global_ratio']}" if stat.get("global_ratio") is not None else ""
            lines.append(f"· 大户多空比 {stat['top_ratio']}{g}{dv}")
        if not any(x.startswith("·") for x in lines[-4:]):
            lines.append("· 衍生品数据暂缺")
        lines.append("")
    fng = stat.get("fng") or {}
    if fng.get("value") is not None:
        lines += ["【市场情绪】", f"· 恐惧贪婪指数 {fng['value']}（{fng.get('classification') or '-'}）", ""]
    lines.append("【判断 · 推测】（非事实，仅供参考）")
    lines += [f"· {j}" for j in _judgment(stat)]
    lines += ["", "【风险】加密资产波动剧烈，以上不构成投资建议，DYOR。",
              "", "—— 由 BAZZ.AGENT 自动生成｜数据源：币安公开行情",
              f"封面图为 90 日 K 线+成交量，${base}"]

    tags = ["#行情分析", "#币安广场"]
    ch = _CHAIN_TAG.get(base)
    if ch:
        tags.insert(0, f"#{ch}")
    title = f"{base} 90日全维度拆解" + (f"：24h {chg24:+.1f}%" if chg24 is not None else "")
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
