"""Binance 行情扫描器 - 基于公开 REST API（无需 API Key）
- 单次拉全市场 24h 快照（一次性 /api/v3/ticker/24hr），本地 TTL 缓存
- 异动信号 / 涨跌幅榜在"成交额前 N"的广阔交易对池上计算（不再固定 20 币）
- 资金费率一次性批量拉取（premiumIndex），避免 N+1 请求
- 「大盘币」按当前 24h 成交额**动态识别**（get_top_liquid_symbols），不再写死列表
"""
import os
import json
import time
import urllib.parse
import requests

SPOT = "https://api.binance.com"
FAPI = "https://fapi.binance.com"


_session = requests.Session()
_session.headers.update({"User-Agent": "AgentOS-BAZZ/1.0"})

# ---- 全市场 24h 快照（TTL 缓存） ----
SNAPSHOT_TTL = 8.0  # 秒；前端 30s 轮询 + 手动刷新，缓存避免击穿 Binance
_snap_cache = {"ts": 0.0, "rows": []}


def get_top_liquid_symbols(n: int = 20, limit_pool: int = 300) -> list:
    """按当前 24h USDT 成交额**动态**识别前 n 个大盘币（每次按真实市场取）。

    用于「妖币 / 启动前」筛选时的排除集合：动态而非固定，避免漏掉新晋大盘币、
    也避免错误排除曾是大盘但已被边缘化的老币。无网络/快照空时返回空列表。
    """
    try:
        rows = get_snapshot(quote="USDT", limit=limit_pool, use_cache=True)
    except Exception:
        return []
    if not rows:
        return []
    rows_sorted = sorted(rows, key=lambda x: x.get("quote_volume", 0) or 0, reverse=True)
    return [r["symbol"] for r in rows_sorted[: max(1, int(n))]]

# 兼容旧名（部分历史代码可能引用）：保留为动态计算入口
TOP_SYMBOLS = get_top_liquid_symbols  # type: ignore[assignment]


def get_snapshot(quote: str = "USDT", limit: int = 0, use_cache: bool = True) -> list:
    """全市场 24h ticker 快照（按 quote 过滤、按成交额降序）。
    limit=0 返回全部（通常 ~450 个 USDT 现货对）。失败时回退旧缓存。
    """
    now = time.time()
    q = (quote or "USDT").upper()
    if use_cache and _snap_cache["rows"] and now - _snap_cache["ts"] < SNAPSHOT_TTL:
        rows = _snap_cache["rows"]
    else:
        try:
            r = _session.get(f"{SPOT}/api/v3/ticker/24hr", timeout=15)
            r.raise_for_status()
            payload = r.json()
        except Exception:
            rows = _snap_cache["rows"]
        else:
            out = []
            for d in payload:
                sym = str(d.get("symbol", ""))
                # 只取 quote 结尾的现货对（如 USDT），排除 USDTUSDT 类自引用
                if not sym.endswith(q) or len(sym) == len(q):
                    continue
                try:
                    price = float(d.get("lastPrice") or 0)
                    if price <= 0:
                        continue
                    out.append({
                        "symbol": sym,
                        "price": price,
                        "change_pct": float(d.get("priceChangePercent") or 0),
                        "volume": float(d.get("volume") or 0),          # 基础币成交量
                        "quote_volume": float(d.get("quoteVolume") or 0),  # USDT 成交额
                        "high": float(d.get("highPrice") or 0),
                        "low": float(d.get("lowPrice") or 0),
                        # v1.5.0：成交笔数（妖币 v2 刷量过滤：成交额/笔数=单笔均额）
                        "count": int(float(d.get("count") or 0)),
                    })
                except (TypeError, ValueError):
                    continue
            out.sort(key=lambda x: x["quote_volume"], reverse=True)
            _snap_cache["rows"] = out
            _snap_cache["ts"] = now
            rows = out
    return rows[:limit] if limit and limit > 0 else list(rows)


FUNDING_TTL = 30.0  # 秒；资金费率批量拉取较稳，加短 TTL 缓存避免每个 30s 轮询都全量打 premiumIndex
_funding_cache = {"ts": 0.0, "rates": {}}


def get_funding_rates() -> dict:
    """一次性拉取所有交易对的资金费率（避免逐币请求）。~30s TTL 缓存。失败回退单请求。"""
    now = time.time()
    if _funding_cache["rates"] and now - _funding_cache["ts"] < FUNDING_TTL:
        return _funding_cache["rates"]
    try:
        r = _session.get(f"{FAPI}/fapi/v1/premiumIndex", timeout=10)
        r.raise_for_status()
        rates = {d["symbol"]: float(d.get("lastFundingRate", 0) or 0) for d in r.json()}
    except Exception:
        rates = {}
        for sym in TOP_SYMBOLS():  # 动态识别的大盘币（按当前成交额排序）
            try:
                r = _session.get(f"{FAPI}/fapi/v1/fundingRate", params={"symbol": sym}, timeout=5)
                rates[sym] = float(r.json().get("lastFundingRate", 0) or 0)
            except Exception:
                rates[sym] = 0.0
    _funding_cache["ts"] = now
    _funding_cache["rates"] = rates
    return rates


def _fmt_signal(it: dict, change: float, funding: float,
                min_change_pct: float, min_funding: float) -> dict | None:
    """把快照行整理成异动信号行（带方向/依据/评分）。"""
    reasons = []
    if abs(change) >= min_change_pct:
        reasons.append(f"24h 波动 {change:+.2f}%")
    if abs(funding) >= min_funding:
        reasons.append(f"资金费率 {funding * 100:+.4f}%")
    if not reasons:
        return None
    return {
        "symbol": it["symbol"],
        "price": it["price"],
        "change_pct": change,
        "volume": it["quote_volume"],          # 沿用 USD 口径，前端 fmtVol 直接可用
        "funding_rate": funding,
        "direction": "做多" if change > 0 else "做空",
        "emoji": "🟢" if change > 0 else "🔴",
        "reason": " · ".join(reasons),
        "score": abs(change) + abs(funding) * 100,
    }


def scan_universe(min_change_pct: float = 0.5, min_funding: float = 0.01,
                  universe_size: int = 150, max_signals: int = 16,
                  min_qv: float = 2e6) -> list:
    """在成交额前 universe_size 的全市场交易对上扫描异动（替代固定 20 币）。

    min_qv：流动性下限（USDT 成交额），把低流动性灰尘盘的"假异动"从扫描池剔除，
    避免小盘庄股一根针就触发信号。默认 2M USDT/24h。
    """
    rows = get_snapshot(limit=universe_size)
    if not rows:
        return []
    if min_qv and min_qv > 0:
        rows = [r for r in rows if r["quote_volume"] >= min_qv]
    rates = get_funding_rates()
    results = []
    for it in rows:
        change = it["change_pct"]
        funding = rates.get(it["symbol"], 0.0)
        sig = _fmt_signal(it, change, funding, min_change_pct, min_funding)
        if sig:
            results.append(sig)
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:max_signals]


def get_24h(symbols) -> dict:
    """批量获取 24h 行情（价格 / 涨跌幅 / 成交量），一次请求。
    注意：Binance 的 symbols 参数需用预编码 URL 拼接（params 字典方式会被代理拒绝）。
    """
    try:
        url = f"{SPOT}/api/v3/ticker/24hr?symbols=" + urllib.parse.quote(json.dumps(symbols, separators=(",", ":")))
        r = _session.get(url, timeout=10)
        r.raise_for_status()
        return {d["symbol"]: d for d in r.json()}
    except Exception:
        return {}


def get_futures_ticker(symbol: str) -> dict | None:
    """单币 U 本位永续合约 24h 行情（公开免鉴权）。
    用于现货端点查不到某币（如仅永续上市 SNDKUSDT 类）时的兜底。
    返回值与 get_24h 同结构（symbol/lastPrice/priceChangePercent/quoteVolume…）。
    """
    try:
        url = f"{FAPI}/fapi/v1/ticker/24hr?symbol=" + urllib.parse.quote(symbol)
        r = _session.get(url, timeout=5)
        r.raise_for_status()
        d = r.json()
        if isinstance(d, list):
            d = d[0] if d else None
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def get_funding_rate(symbol: str) -> float:
    """单币永续资金费率（fapi premiumIndex 单参）；失败返回 0。"""
    try:
        r = _session.get(f"{FAPI}/fapi/v1/premiumIndex", params={"symbol": symbol}, timeout=5)
        r.raise_for_status()
        d = r.json()
        if isinstance(d, list):
            d = d[0] if d else {}
        return float((d or {}).get("lastFundingRate", 0) or 0)
    except Exception:
        return 0.0


def scan_top20(min_change_pct: float = 5.0, min_funding: float = 0.01, top_n: int = 20) -> list:
    """(兼容旧名) 全市场异动扫描 —— 不再固定 TOP_SYMBOLS 那 20 个币！

    改为在『按成交额排序的前 max(top_n*10, 200) 个交易对』这个动态广阔池上扫描，
    返回按强度排序的前 top_n 个信号。新代码请直接用 scan_universe。
    """
    pool = max(top_n * 10, 200)
    return scan_universe(min_change_pct=min_change_pct, min_funding=min_funding,
                         universe_size=pool, max_signals=top_n)


def top_movers(limit: int = 3) -> list:
    """全市场 |24h 涨跌| 最大的几个交易对（基于整市场快照，非固定列表）。"""
    rows = get_snapshot(limit=0)
    rows.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    return [{"symbol": r["symbol"], "price": r["price"], "change_pct": r["change_pct"]}
            for r in rows[:limit]]


def market_movers(quote: str = "USDT", liquid_qv: float = 5e6, top: int = 5) -> dict:
    """全市场涨/跌幅 TOP（只统计成交额 >= liquid_qv 的流动性交易对，滤掉灰尘盘）。"""
    rows = get_snapshot(quote=quote)
    liq = [x for x in rows if x["quote_volume"] >= liquid_qv]
    gainers = sorted(liq, key=lambda x: x["change_pct"], reverse=True)[:top]
    losers = sorted(liq, key=lambda x: x["change_pct"])[:top]
    pick = lambda r: {"symbol": r["symbol"], "price": r["price"], "change_pct": r["change_pct"]}
    return {"gainers": [pick(x) for x in gainers],
            "losers": [pick(x) for x in losers]}


def market_breadth(quote: str = "USDT") -> dict:
    """市场宽度/突发：全市场涨/跌家数、上涨占比、平均 |涨跌|、极端动量标的数（突发预警）。
    基于 get_snapshot 快照，零额外请求。"""
    rows = get_snapshot(quote=quote)
    if not rows:
        return {"advancers": 0, "decliners": 0, "unchanged": 0, "up_ratio": 0.0,
                "avg_abs_chg": 0.0, "extreme_count": 0, "total": 0}
    up = sum(1 for r in rows if r["change_pct"] > 0)
    dn = sum(1 for r in rows if r["change_pct"] < 0)
    uh = len(rows) - up - dn
    avg_abs = sum(abs(r["change_pct"]) for r in rows) / len(rows)
    extreme = sum(1 for r in rows if abs(r["change_pct"]) >= 20)  # 20%+ 暴涨暴跌视为突发
    return {"advancers": up, "decliners": dn, "unchanged": uh,
            "up_ratio": round(up / len(rows), 4) if rows else 0.0,
            "avg_abs_chg": round(avg_abs, 2),
            "extreme_count": extreme, "total": len(rows)}


_FLIP_TTL = 300.0
_flip_cache = {"ts": 0.0, "data": {}}
_fund_alert_ts: dict = {}   # (kind, sym) -> last ts


def _funding_flips(symbols: list) -> list:
    """翻转检测：相邻两期「已结算」费率变号（8h 一期）。5 分钟缓存。"""
    now = time.time()
    cached = _flip_cache["data"]
    if _flip_cache["ts"] and now - _flip_cache["ts"] < _FLIP_TTL:
        return [v for v in cached.values() if v]
    out = {}
    for sym in symbols or []:
        try:
            hist = _funding_hist(sym, limit=2)
        except Exception:
            hist = []
        if len(hist) >= 2 and hist[-1] * hist[-2] < 0:
            out[sym] = {"symbol": sym, "prev": hist[-2], "last": hist[-1],
                        "to": "多" if hist[-1] > 0 else "空"}
        else:
            out[sym] = None
    _flip_cache["data"] = out
    _flip_cache["ts"] = now
    return [v for v in out.values() if v]


def _funding_alerts(items: list, flips: list) -> None:
    """funding 极值/翻转 → 后端事件（toast + 系统通知），同币同类型 6h 冷却。"""
    try:
        import market_ws
    except Exception:
        return
    now = time.time()
    for it in items or []:
        if not it.get("extreme"):
            continue
        key = ("funding_extreme", it["symbol"])
        if now - _fund_alert_ts.get(key, 0.0) < 21600.0:
            continue
        _fund_alert_ts[key] = now
        market_ws.publish_event("alert", kind="funding_extreme", symbol=it["symbol"],
                                rate=it["funding_rate"], side="long" if it["funding_rate"] > 0 else "short")
    for fl in flips or []:
        key = ("funding_flip", fl["symbol"])
        if now - _fund_alert_ts.get(key, 0.0) < 21600.0:
            continue
        _fund_alert_ts[key] = now
        market_ws.publish_event("alert", kind="funding_flip", symbol=fl["symbol"],
                                prev=fl["prev"], last=fl["last"], to=fl["to"])


def funding_board(top: int = 10, min_qv: float = 2e6) -> dict:
    """资金费率板块 v2（v1.5.0）：正/负费率排序 + 极值标记（|rate|≥0.30%）
    + 翻转检测（相邻两期结算变号）。只统计流动性交易对。"""
    rates = get_funding_rates()
    rows = [r for r in get_snapshot(quote="USDT") if r["quote_volume"] >= min_qv]
    items = []
    for r in rows:
        fr = rates.get(r["symbol"], 0.0)
        if abs(fr) < 0.00001:
            continue
        items.append({"symbol": r["symbol"], "price": r["price"],
                      "change_pct": r["change_pct"],
                      "funding_rate": fr})
    items.sort(key=lambda x: x["funding_rate"], reverse=True)
    for it in items:
        it["extreme"] = abs(it["funding_rate"]) >= 0.003      # |费率| ≥ 0.30% 极值
    _pick = lambda i: {**i, "direction": "多" if i["funding_rate"] > 0 else "空",
                       "crowded": abs(i["funding_rate"]) >= 0.001}  # >=0.1% 视为拥挤
    extremes = sorted([x for x in items if x["extreme"]],
                      key=lambda x: abs(x["funding_rate"]), reverse=True)
    flips = _funding_flips([x["symbol"] for x in extremes[:8]])
    try:
        _funding_alerts(extremes[:5], flips)
    except Exception:
        pass
    return {"long_crowded": [_pick(x) for x in items[:top]],
            "short_crowded": [_pick(x) for x in items[::-1][:top if len(items) >= top else len(items)]],
            "extremes": [_pick(x) for x in extremes[:top]],
            "flips": flips}


# ---------------- 多空比 / 大户持仓面板（v1.5.0） ----------------
_LS_TTL = 60.0
_ls_cache = {"ts": 0.0, "data": None}


def get_longshort_board(top_n: int = 12, min_qv: float = 5e6) -> dict:
    """多空比/大户持仓面板：大户持仓比（topLongShortPositionRatio）× 散户账户比
    （globalLongShortAccountRatio）双比值 + 背离标记。合约维度免费端点，60s TTL。"""
    now = time.time()
    if _ls_cache["data"] and now - _ls_cache["ts"] < _LS_TTL:
        return _ls_cache["data"]
    pool = [x for x in futures_snapshot() if x["quote_volume"] >= min_qv][:top_n]

    def _one(x: dict) -> dict:
        row = {**x, "top_ratio": None, "top_prev": None, "global_ratio": None,
               "divergence": False}
        top = _fut_hist(x["symbol"], "topLongShortPositionRatio", period="1h", limit=2)
        glob = _fut_hist(x["symbol"], "globalLongShortAccountRatio", period="1h", limit=2)
        try:
            if top:
                row["top_ratio"] = float(top[-1].get("longShortRatio") or 0) or None
                row["top_prev"] = float(top[0].get("longShortRatio") or 0) or None
            if glob:
                row["global_ratio"] = float(glob[-1].get("longShortRatio") or 0) or None
        except (TypeError, ValueError, IndexError):
            pass
        t, g = row["top_ratio"], row["global_ratio"]
        if t and g:
            row["divergence"] = (t - 1.0) * (g - 1.0) < 0    # 大户与散户方向相反 = 主力反向
        return row

    rows = []
    with _cf.ThreadPoolExecutor(max_workers=6) as ex:
        for r in ex.map(_one, pool):
            if r["top_ratio"] or r["global_ratio"]:
                rows.append(r)
    rows.sort(key=lambda x: abs((x["top_ratio"] or 1.0) - 1.0), reverse=True)
    payload = {"rows": rows, "updated_at": int(now), "ttl": _LS_TTL}
    _ls_cache["data"] = payload
    _ls_cache["ts"] = now
    return payload


def volume_heat(top: int = 15, quote: str = "USDT") -> list:
    """24h 成交额/热度榜：按成交额降序的流动性交易对。"""
    rows = get_snapshot(quote=quote, limit=top)
    return [{"symbol": r["symbol"], "price": r["price"],
             "change_pct": r["change_pct"], "quote_volume": r["quote_volume"]}
            for r in rows[:top]]


# ================= 合约（USDT-M 永续） + 股票化代币合约 =================
FUTURES_TTL = 20.0  # 秒
_futures_cache = {"ts": 0.0, "rows": []}


def futures_snapshot(use_cache: bool = True) -> list:
    """U 本位永续合约全市场 24h 快照（/fapi/v1/ticker/24hr，无鉴权）。
    只保留成交额>0 的 USDT 永续对，按成交额降序，并附资金费率与标记价格。
    失败回退旧缓存。TTL ~20s，前端内嵌 30s 轮询不击穿接口。
    """
    now = time.time()
    if use_cache and _futures_cache["rows"] and now - _futures_cache["ts"] < FUTURES_TTL:
        return list(_futures_cache["rows"])
    try:
        r = _session.get(f"{FAPI}/fapi/v1/ticker/24hr", timeout=15)
        r.raise_for_status()
        payload = r.json()
    except Exception:
        return list(_futures_cache["rows"])
    rates = get_funding_rates()
    out = []
    for d in payload:
        sym = str(d.get("symbol", ""))
        if not sym.endswith("USDT") or len(sym) <= len("USDT"):
            continue
        try:
            price = float(d.get("lastPrice") or 0)
            if price <= 0:
                continue
            out.append({
                "symbol": sym,
                "price": price,
                "change_pct": float(d.get("priceChangePercent") or 0),
                "quote_volume": float(d.get("quoteVolume") or 0),
                "high": float(d.get("highPrice") or 0),
                "low": float(d.get("lowPrice") or 0),
                "funding_rate": float(rates.get(sym, 0) or 0),
                "side": "future",
                "kind": "futures",
            })
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["quote_volume"], reverse=True)
    _futures_cache["ts"] = now
    _futures_cache["rows"] = out
    return list(out)


# 币安 U 本位「股票化代币/传统资产(TradFi)」永续合约 —— 标的为美股/ETF。
# 逐品种白名单（symbol → 展示名/公司），与实时 fapi ticker 交叉匹配，只展现在线合约。
EQUITY_PERPS: dict = {
    # 大盘科技/金融等（近年陆续上线）
    "TSLAUSDT": "Tesla 特斯拉 · 纳斯达克:TSLA",
    "AAPLUSDT": "Apple 苹果 · 纳斯达克:AAPL",
    "MSFTUSDT": "Microsoft 微软 · 纳斯达克:MSFT",
    "NVDAUSDT": "NVIDIA 英伟达 · 纳斯达克:NVDA",
    "AMZNUSDT": "Amazon 亚马逊 · 纳斯达克:AMZN",
    "GOOGLUSDT": "Alphabet 谷歌 · 纳斯达克:GOOGL",
    "METAUSDT": "Meta · 纳斯达克:META",
    "NFLXUSDT": "Netflix 奈飞 · 纳斯达克:NFLX",
    "COINUSDT": "Coinbase · 纳斯达克:COIN",
    "AMDUSDT": "AMD · 纳斯达克:AMD",
    "INTCUSDT": "Intel 英特尔 · 纳斯达克:INTC",
    "QCOMUSDT": "Qualcomm 高通 · 纳斯达克:QCOM",
    "MRVLUSDT": "Marvell 迈威尔 · 纳斯达克:MRVL",
    "WMTUSDT": "Walmart 沃尔玛 · 纽交所:WMT",
    "JPMUSDT": "JPMorgan 摩根大通 · 纽交所:JPM",
    "VUSDT": "Visa · 纽交所:V",
    "BRKBUSDT": "Berkshire Hathaway B · 纽交所:BRK.B",
    "MRNAUSDT": "Moderna · 纳斯达克:MRNA",
    "DJTUSDT": "Trump Media · 纳斯达克:DJT",
    # 杠杆 ETF / 标的（不同国家的标的，名字尽量保留交易所原码）
    "SOXLUSDT": "Direxion 半导体 3X ETF",
    "INTWUSDT": "GraniteShares 2X Long INTC ETF",
    "SNXXUSDT": "Tradr 2X Long SNDK ETF",
    "SKUUUSDT": "GraniteShares 2X Long SK Hynix ETF",
    "SKDDUSDT": "GraniteShares 2X Short SK Hynix ETF",
    "RAMUSDT": "Roundhill 2X Long DRAM ETF",
    "XBIUSDT": "SPDR S&P 生物科技 ETF",
    # 其余个股/小型股
    "BOTUSDT": "RoboStrategy · 机器人",
    "WENUSDT": "Wendy's 温蒂 · 纳斯达克:WEN",
    "BNCUSDT": "CEA Industries",
    "FWDIUSDT": "Forward Industries",
    "CRWVUSDT": "CoreWeave · 云/AI 计算",
}
EQUITY_LEVERAGE = {  # 已知杠杆上限（凑齐展示信息用；缺失默认 --）
    "TSLAUSDT": 5, "AAPLUSDT": 5, "NVDAUSDT": 5, "MSFTUSDT": 5,
    "AMZNUSDT": 5, "GOOGLUSDT": 5, "COINUSDT": 5, "BOTUSDT": 25,
    "WENUSDT": 25, "XBIUSDT": 25, "INTWUSDT": 25, "SNXXUSDT": 25,
    "BNCUSDT": 10, "FWDIUSDT": 10,
}


def equity_board() -> list:
    """股票化代币合约行情板：从合约快照里过滤出 EQUITY_PERPS 白名单，附展示名与杠杆上限。"""
    live = {x["symbol"]: x for x in futures_snapshot()}
    rows = []
    for sym, name in EQUITY_PERPS.items():
        d = live.get(sym)
        if not d:
            continue
        rows.append({**d, "name": name,
                     "leverage": EQUITY_LEVERAGE.get(sym, ""),
                     "side": "equity", "kind": "equity"})
    rows.sort(key=lambda x: x["quote_volume"], reverse=True)
    return rows


# ================= 妖币雷达（Monster Radar） =================
# 两种模式（避免"涨完才提示"的追高陷阱）：
#   1) 启动前·埋伏窗口 (ignition)：量在价先 —— 低位横盘/阴跌后温和放量、
#      价格仍被压制(吸筹脚印)、底部抬升不破新低 → 疑似主力吸筹，点火前
#   2) 起飞中·追涨高风险 (takeoff)：已启动的暴涨币，仅跟踪，明示追高风险
# 说明：公开现货 REST 无链上持币分布/资金流，用价量结构近似；链上因子（
# holder 集中度、CEX 净流出、聪明钱）需接链上/Web3 API，留待后续增强。
import concurrent.futures as _cf

MONSTER_TTL = 300.0  # 秒；kline 拉取较贵，5 分钟一刷
_monster_cache = {"ts": 0.0, "data": None}
_ignition_cache = {"ts": 0.0, "data": None}
_bars_cache = {"ts": 0.0, "bars": {}}  # sym -> (closes, vols) 110 根日线，两模式共用

_STABLE_BASES = {
    "USDC", "FDUSD", "TUSD", "USDP", "USDD", "DAI", "EUR", "AEUR", "BUSD",
    "PAXG", "EURT", "XUSD", "USDE", "PYUSD", "GUSD", "USD1", "USDS", "EURI",
    "SUSD", "USDX", "RUSD", "FUSD", "EURI", "TRY", "BRL", "GBP", "JPY",
}
_LEV_HINTS = ("3L", "3S", "5L", "5S", "UP", "DOWN", "BULL", "BEAR", "BULLS", "BEARS")


def _base_sym(sym: str) -> str:
    return sym[:-4] if sym.endswith("USDT") else sym


def _is_eligible(sym: str) -> bool:
    b = _base_sym(sym)
    if not b or b in _STABLE_BASES:
        return False
    for h in _LEV_HINTS:
        if h in b:
            return False
    return True


def _kline_daily(sym: str, limit: int = 110):
    """拉 sym 的日线（返回 closes/vols），失败返回 None。"""
    try:
        r = _session.get(f"{SPOT}/api/v3/klines",
                         params={"symbol": sym, "interval": "1d", "limit": limit}, timeout=8)
        r.raise_for_status()
        arr = r.json()
    except Exception:
        return None
    if not arr:
        return None
    closes, vols = [], []
    for x in arr:
        if len(x) < 6:
            continue
        try:
            closes.append(float(x[4])); vols.append(float(x[5]))
        except (TypeError, ValueError):
            continue
    return (closes, vols) if len(closes) >= 60 else None


def get_candidate_bars(top_n: int = 120, min_qv: float = 2e6,
                       force: bool = False, workers: int = 10) -> list:
    """一次性并发拉全市场候选日线（110 根），monster(36窗口) 与 ignition(90窗口) 共用。"""
    now = time.time()
    if not force and _bars_cache["bars"] and now - _bars_cache["ts"] < MONSTER_TTL:
        return _bars_cache["bars"]
    rows = [r for r in get_snapshot() if r["quote_volume"] >= min_qv and _is_eligible(r["symbol"])]
    cand = rows[:top_n]
    out = {}
    with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_kline_daily, r["symbol"]): r for r in cand}
        for fut in _cf.as_completed(futs):
            kv = fut.result()
            if kv:
                out[futs[fut]["symbol"]] = kv
    _bars_cache["bars"] = out
    _bars_cache["ts"] = now
    return out


def _analyze_takeoff(sym: str, ticker: dict, closes: list, vols: list):
    """起飞中·追涨高风险：已启动暴涨币（30/35 日窗口），仅跟踪、明示追高风险。"""
    c = closes[-36:]
    v = vols[-36:]
    n = len(c)
    if n < 15:
        return None
    last = c[-1]
    if last <= 0:
        return None
    d30 = last / c[0] - 1.0                     # ~35 日累计
    d7 = last / c[-8] - 1.0                     # 近 7 日
    hi90 = max(closes[-90:]) if len(closes) >= 90 else max(c)
    dd = last / hi90 - 1.0                      # 距 90d 高点回撤
    lo = min(c); from_low = last / lo - 1.0
    prev7 = v[-8:-1]
    avg = sum(prev7) / len(prev7) if prev7 else 0.0
    vol_ratio = (v[-1] / avg) if avg > 0 else 0.0

    if d30 < 0.20 and d7 < 0.15:
        return None

    # 追高风险提示（side 语义与旧版一致）
    if d30 >= 1.0 and dd > -0.30:
        tag, side, note = "起飞 · 主升", "LONG", "已暴涨数倍，追高风险大；只适合已持仓者沿趋势，止损要快"
    elif d7 >= 0.25 and d30 < 1.6 and dd > -0.20:
        tag, side, note = "起飞 · 加速", "LONG", "正在加速拉升（已启动），非启动前埋伏；小仓+硬止损"
    elif d30 >= 1.0 and dd <= -0.30 and dd > -0.60:
        tag, side, note = "起飞 · 回落", "WATCH_SHORT", "暴涨后回落，现货只可减/清持仓；做空需合约并防反抽"
    elif d30 >= 1.0 and dd <= -0.60:
        tag, side, note = "起飞 · 深跌", "WATCH", "从高点深跌 60%+，或有反弹但接刀风险大，观察企稳"
    else:
        tag, side, note = "起飞 · 异动", ("LONG" if d7 >= 0 else "WATCH_SHORT"), "短期活跃，量价配合再介入"

    score = round(max(1.0, min(99.0,
        (1 + max(d30, 0.0)) * (1 + max(d7, 0.0)) * 8
        + (12 if vol_ratio >= 2 else 0)
        + (8 if dd > -0.15 else 0))))
    return {
        "symbol": sym,
        "price": ticker["price"],
        "change7d_pct": round(d7 * 100, 2),
        "change30d_pct": round(d30 * 100, 2),
        "drawdown_pct": round(dd * 100, 2),
        "from_low_pct": round(from_low * 100, 2),
        "quote_volume": ticker["quote_volume"],
        "vol_ratio": round(vol_ratio, 2),
        "tag": tag, "side": side, "note": note,
        "score": score,
    }


def _analyze_ignition(sym: str, ticker: dict, closes: list, vols: list):
    """启动前·埋伏窗口：量在价先 —— 低位放量吸筹、价格被压制、底部抬升不破新低。

    依据（行业共识，见调研）：
      1) 位置低  现价处于 90 日区间 [low,high] 的中低分位，不是追高位；
      2) 温和放量  近 1/3 日均量 >= 前 30 日均量 1.6 倍（"量在价先"，吸筹脚印）；
      3) 价格被压制  近 3 日涨跌幅收窄（-10%~+20%），未提前起飞；
      4) 底部企稳  近 5 日低点高于前期平台、未创 90 日新低。
    提示：公开现货无链上筹码数据，此为价量近似；接链上（holder 集中度/资金流）可再增强。
    """
    if len(closes) < 90 or len(vols) < 90:
        return None
    c = closes[-90:]
    v = vols[-90:]
    last = c[-1]
    if last <= 0:
        return None
    hi90 = max(c); lo90 = min(c)
    pos = (last - lo90) / (hi90 - lo90) if hi90 > lo90 else 1.0   # 0=最低 1=最高
    avg30 = sum(v[-31:-1]) / 30.0 if len(v) >= 31 else 0.0
    if avg30 <= 0:
        return None
    vr1 = v[-1] / avg30
    vr3 = (sum(v[-3:]) / 3.0) / avg30
    vr = max(vr1, vr3)
    ch3 = last / c[-4] - 1.0
    ch7 = last / c[-8] - 1.0
    ch30 = last / c[-31] - 1.0 if len(c) >= 31 else last / c[0] - 1.0
    floor5 = min(c[-5:])
    floor_prev = min(c[-26:-5]) if len(c) >= 26 else lo90
    floor_rising = floor5 >= floor_prev * 1.002          # 底部平台小幅抬升
    near_new_low = last <= lo90 * 1.03                    # 仍贴着 90 日低点 = 未企稳

    # 硬门槛：中低位 + 放量 + 价格未大涨（被压制/回踩）+ 未创新低
    if not (0.0 <= pos < 0.60 and vr >= 1.5 and -0.12 <= ch3 <= 0.10
            and not near_new_low and ch30 >= -0.40):
        return None

    score = 18.0
    score += max(0.0, (0.60 - pos) / 0.60) * 24          # 越低分位越高分（最多 24）
    score += min(26.0, max(0.0, (vr - 1.5) / 4.5) * 26.0)  # 放量强度（1.5x→6.0x 封顶 26）
    score += 8.0 if -0.04 <= ch3 <= 0.06 else (2.0 if ch3 > 0.06 else 0.0)  # 越"价稳"越像吸筹
    score += 8.0 if floor_rising else 0.0
    if vr > 15.0:
        # 异常放量多为刷量 / 大额一次性事件（利好落地、解锁砸盘），真吸筹通常 2~6 倍温和放量
        tag, side, note = "放量异常 · 防刷量", "WATCH", "量比异常放大（刷量/单次大额事件概率高），真吸筹通常 2~6 倍温和放量；先辨真伪再决定"
        score -= 35.0
    elif pos <= 0.35 and vr >= 2.0 and ch3 <= 0.10 and floor_rising:
        tag, side, note = "吸筹放量 · 疑似点火前", "LONG", "底部放量但价被压住 + 平台抬升 = 典型吸筹脚印；点火信号出现前小仓埋伏、破位即走"
    elif vr >= 1.8 and floor_rising and ch3 >= -0.08 and ch30 > -0.15:
        tag, side, note = "突破回踩 · 二买点", "LONG", "放量突破后缩量回踩不破位，常见二次启动点；站回前高即确认"
    elif vr >= 2.0 and pos <= 0.45:
        tag, side, note = "底部温和放量 · 观察", "LONG", "低位量能放大 2 倍+，疑似主力分批吸筹；等放量突破确认再加仓"
    else:
        tag, side, note = "低位试盘 · 待确认", "LONG", "首次放量试探，真假吸筹未定；观察 2-3 日能否站稳不再回落"

    return {
        "symbol": sym,
        "price": ticker["price"],
        "change3d_pct": round(ch3 * 100, 2),
        "change7d_pct": round(ch7 * 100, 2),
        "change30d_pct": round(ch30 * 100, 2),
        "position_pct": round(pos * 100, 1),      # 90 日区间分位（低=越接近底部）
        "drawdown_pct": round((last / hi90 - 1) * 100, 2),
        "quote_volume": ticker["quote_volume"],
        "vol_ratio": round(vr, 2),
        "floor_rising": bool(floor_rising),
        "tag": tag, "side": side, "note": note,
        "score": round(max(1.0, min(99.0, score))),
    }


def _env_regime(bars: dict) -> dict:
    """大盘环境标签：BTC 状态（来自同池日线），提示资金外溢窗口。"""
    kv = bars.get("BTCUSDT")
    if not kv or len(kv[0]) < 60:
        return {"btc_30d": None, "btc_range": None, "regime": "unknown"}
    c = kv[0][-60:]
    last = c[-1]
    d30 = last / c[-30] - 1.0 if len(c) >= 30 else last / c[0] - 1.0
    rng = (max(c[-30:]) - min(c[-30:])) / min(c[-30:]) * 100 if min(c[-30:]) > 0 else 0
    if -0.05 <= d30 <= 0.05 and rng <= 12:
        regime = "BTC 横盘 · 山寨资金外溢窗口（利于小币启动）"
    elif d30 > 0.10:
        regime = "BTC 强势上行 · 资金偏好大盘"
    elif d30 < -0.10:
        regime = "BTC 回调 · 逆势妖币风险高"
    else:
        regime = "BTC 温和 · 结构性行情"
    return {"btc_30d": round(d30 * 100, 2), "btc_range": round(rng, 2), "regime": regime}


# ================= 妖币雷达 v2 · 四层模型（v1.5.0） =================
# 调研固化（PROJECT_STATUS §四 v1.5.0）：妖币 = 暴涨暴跌的极端波动币种（≠ meme 币）。
# 架构：触发层（短时窗价格暴力）→ 确认层（合约杠杆/资金驱动因子）→
#       语义层（生命周期六阶段）→ 过滤层（流动性/新币/刷量/β 残差/冷却）。
# v1（日K 35/90 日窗口）保留为网络异常兜底；get_monster_coins / get_ignition_coins
# 对外接口不变（agent_core meme_watch 与前端行情页共用）。
import math

RADAR2_TTL = 300.0          # 5 分钟一扫
RADAR2_FLOOR = 5e6          # 过滤层：24h 成交额 ≥ $5M 流动性地板
NEW_COIN_DAYS = 30          # 过滤层：上市 < 30 天排除
RADAR2_COOLDOWN = 1800.0    # 过滤层：同币同阶段 30 分钟冷却（防重复报警刷屏）

_radar2_cache = {"ts": 0.0, "data": None}
_daily2_cache = {"ts": 0.0, "bars": {}}   # sym -> (o,h,l,c,v,first_ms)，30 分钟缓存
_radar_prev: dict = {}                    # sym -> (cycle_ts, stage)


def _klines_raw(sym: str, interval: str = "15m", limit: int = 288) -> list:
    """拉 sym 的 K 线原始数组（旧→新，含当前未收 bar），失败返回 []。"""
    try:
        r = _session.get(f"{SPOT}/api/v3/klines",
                         params={"symbol": sym, "interval": interval, "limit": limit}, timeout=10)
        r.raise_for_status()
        return r.json() or []
    except Exception:
        return []


def _ohlcv(arr: list):
    """klines 原始数组 → (opens, highs, lows, closes, vols)。"""
    o, h, l, c, v = [], [], [], [], []
    for x in arr:
        try:
            if len(x) < 6:
                continue
            o.append(float(x[1])); h.append(float(x[2])); l.append(float(x[3]))
            c.append(float(x[4])); v.append(float(x[5]))
        except (TypeError, ValueError):
            continue
    return (o, h, l, c, v)


# ---------------- 触发层（纯函数，可单测） ----------------

def _ewma_vol(rets, lam: float = 0.94) -> float:
    """EWMA 波动率（RiskMetrics λ=0.94；短窗收益以 0 为均值）。"""
    if not rets:
        return 0.0
    var = rets[0] * rets[0]
    for r in rets[1:]:
        var = lam * var + (1 - lam) * r * r
    return math.sqrt(var)


def _jump_L(closes) -> float:
    """跳跃检验 L = r/σ̂（σ̂ 为近 40 根对数收益 EWMA 波动率）。|L|>3 触发。"""
    n = len(closes)
    if n < 40:
        return 0.0
    rets = []
    for i in range(n - 40, n):
        if closes[i - 1] > 0 and closes[i] > 0:
            rets.append(math.log(closes[i] / closes[i - 1]))
    if len(rets) < 10:
        return 0.0
    sigma = _ewma_vol(rets[:-1])
    if sigma <= 0:
        return 0.0
    return rets[-1] / sigma


def _flow_price(o, h, l, c, v) -> float:
    """泵度 flow_price（edsonschlei/freqtrade-stuff PumpDetector 移植）：
    flow = 量×振幅×阴阳方向；归一化 ÷(全窗均量×全窗均振幅)；近 k 根累计取
    k∈{1,2,4,8,16,32} 最大值。>1000 = 近期被泵（15m 窗口 288 根 ≈ 72h）。"""
    n = len(c)
    if n < 50:
        return 0.0
    flows = []
    amps = []
    for i in range(n):
        amp = max(0.0, h[i] - l[i])
        amps.append(amp)
        direction = 1.0 if c[i] >= o[i] else -1.0
        flows.append(v[i] * amp * direction)
    mv = sum(v) / n
    ma = sum(amps) / n
    if mv <= 0 or ma <= 0:
        return 0.0
    best = 0.0
    for k in (1, 2, 4, 8, 16, 32):
        if k > n:
            break
        val = sum(flows[-k:]) / (mv * ma)
        if val > best:
            best = val
    return best


def _velocity(c5) -> tuple:
    """价格速度：(近 5 分钟涨跌%, 加速度>0?)（加速度 = 近 15min 速度 > 前 15min 速度）。"""
    n = len(c5)
    if n < 8 or c5[-6] <= 0:
        return (0.0, False)
    speed = (c5[-1] / c5[-6] - 1.0) * 100.0
    v1 = (c5[-1] / c5[-4] - 1.0) if c5[-4] > 0 else 0.0     # 近 15min
    v0 = (c5[-4] / c5[-7] - 1.0) if c5[-7] > 0 else 0.0     # 前 15min
    return (speed, v1 > v0)


def _rvol(v, window: int = 20) -> float:
    """量比 RVOL = 最新一根量 / 前 window 根均量。"""
    if len(v) < window + 1:
        return 0.0
    base = sum(v[-(window + 1):-1]) / window
    return (v[-1] / base) if base > 0 else 0.0


def _strongvol60(v5) -> bool:
    """近 60min 均量 ≥ 3× 前 60min 均量（强触发）。"""
    if len(v5) < 25:
        return False
    a = sum(v5[-12:]) / 12.0
    b = sum(v5[-24:-12]) / 12.0
    return b > 0 and a >= 3.0 * b


def _consec_streak(o, c, v) -> int:
    """连续同向放量形态：返回最后一串「同向且逐根放量」的迁移数（2 = 3 根连续）。"""
    cnt = 0
    for i in range(1, len(c)):
        same = (c[i] >= o[i]) == (c[i - 1] >= o[i - 1])
        rising = v[i] >= v[i - 1]
        cnt = cnt + 1 if (same and rising) else 0
    return cnt


def _short_sweep(o, c, v) -> dict:
    """短时暴涨暴跌：5m ±3% 带量 / 1h ±5%。"""
    res = {"pump5": False, "dump5": False, "pump1h": False, "dump1h": False}
    n = len(c)
    if n < 13:
        return res
    chg5 = (c[-1] / c[-2] - 1.0) * 100 if c[-2] > 0 else 0.0
    if chg5 >= 3.0 and _rvol(v) >= 2.0:
        res["pump5"] = True
    if chg5 <= -3.0 and _rvol(v) >= 2.0:
        res["dump5"] = True
    if c[-13] > 0:
        chg1h = (c[-1] / c[-13] - 1.0) * 100
        res["pump1h"] = chg1h >= 5.0
        res["dump1h"] = chg1h <= -5.0
    return res


def _trigger_hit(t: dict, row: dict, range_ratio: float = 0.0) -> tuple:
    """触发层：任一命中进扫描池。返回 (命中?, 触发依据列表)。"""
    rs = []
    L = t.get("jump_L", 0.0)
    if abs(L) > 3.0:
        rs.append(f"跳跃检验 |L|={abs(L):.1f}")
    if t.get("flow", 0.0) > 1000:
        rs.append(f"泵度 flow={t['flow']:.0f}")
    if abs(t.get("speed5m", 0.0)) >= 0.5 and t.get("accel"):
        rs.append(f"速度 {t['speed5m']:+.2f}%/5m 加速")
    if abs(t.get("chg24", 0.0)) >= 10.0:
        rs.append(f"24h {t['chg24']:+.1f}%")
    if t.get("pump5") or t.get("dump5"):
        rs.append("5m ±3% 带量")
    if t.get("pump1h") or t.get("dump1h"):
        rs.append("1h ±5%")
    if t.get("streak", 0) >= 2:
        rs.append(f"连续 {t['streak'] + 1} 根同向放量")
    if t.get("rvol15", 0.0) >= 2.0:
        rs.append(f"RVOL={t['rvol15']:.1f}x")
    if t.get("strongvol"):
        rs.append("60min 量能 3x")
    if t.get("amp24", 0.0) >= 15.0:
        rs.append(f"24h 振幅 {t['amp24']:.0f}%")
    if range_ratio >= 4.0:
        rs.append(f"振幅比 {range_ratio:.1f}x")
    return (len(rs) > 0, rs)


# ---------------- 日线特征（过滤层 + 语义层底料） ----------------

def _daily2_fetch(sym: str):
    """日线 → (o,h,l,c,v,first_ms)；不足 25 根返回 None。"""
    arr = _klines_raw(sym, "1d", 110)
    if len(arr) < 25:
        return None
    o, h, l, c, v = _ohlcv(arr)
    if len(c) < 25:
        return None
    try:
        first_ms = int(arr[0][0])
    except (TypeError, ValueError, IndexError):
        first_ms = 0
    return (o, h, l, c, v, first_ms)


def _daily2_bars(top_n: int, min_qv: float, force: bool = False, workers: int = 10) -> dict:
    """并发拉扫描池日线（30 分钟缓存；妖币 v2 专用，与 v1 _bars_cache 分离）。"""
    now = time.time()
    if not force and _daily2_cache["bars"] and now - _daily2_cache["ts"] < 1800:
        return _daily2_cache["bars"]
    rows = [r for r in get_snapshot() if r["quote_volume"] >= min_qv and _is_eligible(r["symbol"])][:top_n]
    out = {}
    with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_daily2_fetch, r["symbol"]): r["symbol"] for r in rows}
        for fut in _cf.as_completed(futs):
            try:
                kv = fut.result()
            except Exception:
                kv = None
            if kv:
                out[futs[fut]] = kv
    _daily2_cache["bars"] = out
    _daily2_cache["ts"] = now
    return out


def _daily_feats(bars: tuple, now_ts: float) -> dict:
    """日线特征：位置分位 / 3-7-30 日涨跌 / 回撤 / 量比 / 形态 / 上市天数 / 刷量嫌疑。"""
    o, h, l, c, v, first_ms = bars
    n = len(c)
    last = c[-1]
    if last <= 0:
        return {}
    w = c[-90:] if n >= 90 else c
    hi, lo = max(w), min(w)
    pos = (last - lo) / (hi - lo) if hi > lo else 1.0
    chg3 = last / c[-4] - 1.0 if n >= 4 and c[-4] > 0 else 0.0
    chg7 = last / c[-8] - 1.0 if n >= 8 and c[-8] > 0 else 0.0
    chg30 = last / c[-31] - 1.0 if n >= 31 and c[-31] > 0 else last / c[0] - 1.0
    dd = last / hi - 1.0
    from_low = last / lo - 1.0
    # 量比（以最近「已收」日线为准，最后一根为今日未收）
    def _vr(i: int) -> float:
        base = v[max(0, i - 31):i - 1]
        b = sum(base) / len(base) if base else 0.0
        return (v[i - 1] / b) if b > 0 else 0.0
    rvol_d = _vr(n - 1)          # 昨日量 / 前 30 日均量
    vr2 = _vr(n - 2)
    vr3 = _vr(n - 3)
    base30 = v[max(0, n - 32):n - 2]
    b30 = sum(base30) / len(base30) if base30 else 0.0
    vol_climax = b30 > 0 and v[n - 2] >= 8.0 * b30          # 量高潮
    # 长上影（昨日收线）：上影 > 实体×2
    body = abs(c[n - 2] - o[n - 2])
    upper = h[n - 2] - max(o[n - 2], c[n - 2])
    upper_shadow = body > 0 and upper > 2.0 * body
    breakout20 = n >= 22 and last > max(c[-21:-1])          # 破 20 日高
    floor5 = min(l[-5:])
    floor_prev = min(l[-26:-5]) if n >= 26 else lo
    floor_rising = floor5 >= floor_prev * 1.002
    # 刷量嫌疑：连续 3 个已收日量比 > 50
    wash = min(rvol_d, vr2, vr3) > 50.0
    listed_days = (now_ts - first_ms / 1000.0) / 86400.0 if first_ms else 999.0
    amp7 = sum((h[i] - l[i]) / l[i] * 100 for i in range(max(1, n - 8), n - 1)
               if l[i] > 0) / max(1, (n - 1) - max(1, n - 8))
    return {"pos": pos, "chg3d": chg3 * 100, "chg7d": chg7 * 100, "chg30d": chg30 * 100,
            "dd": dd * 100, "from_low": from_low * 100, "rvol_d": rvol_d,
            "near_low": last <= lo * 1.03,
            "vol_climax_d": vol_climax, "upper_shadow_d": upper_shadow,
            "breakout20": breakout20, "floor_rising": floor_rising,
            "wash": wash, "listed_days": listed_days, "amp7d": amp7}


def _btc_beta(sym_rets, btc_rets, chg24: float, btc24: float) -> dict:
    """BTC β 残差：普涨普跌（残差小）打六折，剔除大盘 beta 的水分子。"""
    out = {"beta": None, "residual": None, "damped": False}
    m = min(len(sym_rets), len(btc_rets))
    if m < 30:
        return out
    xs = btc_rets[-m:]
    ys = sym_rets[-m:]
    sxx = sum(x * x for x in xs)
    if sxx <= 0:
        return out
    sxy = sum(x * y for x, y in zip(xs, ys))
    beta = sxy / sxx
    resid = (chg24 / 100.0) - beta * (btc24 / 100.0)
    out["beta"] = round(beta, 2)
    out["residual"] = round(resid * 100, 2)
    out["damped"] = abs(btc24) >= 2.0 and abs(resid) * 100 < 0.3 * abs(chg24)
    return out


# ---------------- 确认层（/futures/data/* + premiumIndex，全部免费） ----------------

def _fut_hist(sym: str, path: str, period: str = "15m", limit: int = 100) -> list:
    """/futures/data/* 免费端点。失败返回 []。"""
    try:
        r = _session.get(f"{FAPI}/futures/data/{path}",
                         params={"symbol": sym, "period": period, "limit": limit}, timeout=8)
        r.raise_for_status()
        return r.json() or []
    except Exception:
        return []


def _funding_hist(sym: str, limit: int = 21) -> list:
    """已结算资金费率历史（8h 一期，21 期 ≈ 7 天）。失败返回 []。"""
    try:
        r = _session.get(f"{FAPI}/fapi/v1/fundingRate",
                         params={"symbol": sym, "limit": limit}, timeout=8)
        r.raise_for_status()
        return [float(d.get("lastFundingRate") or 0) for d in (r.json() or [])]
    except Exception:
        return []


def _confirm_factors(sym: str, funding_now: float) -> dict:
    """确认层因子：OI 四象限/脉冲、funding 极值、大户/散户多空比、taker 买卖比、爆仓流。
    任一接口失败安全降级（0/None），绝不因确认层挂掉丢触发信号。"""
    f = {"oi_chg24": 0.0, "oi_chg48": 0.0, "oi_pulse15": 0.0, "funding": funding_now,
         "funding_peak": funding_now, "funding_prev": None, "top_ratio": None,
         "top_mean48": None, "global_ratio": None, "taker_ratio": None,
         "taker_drop": False, "liq_5m": 0.0, "liq_side": "", "liq_n5m": 0}
    oi = _fut_hist(sym, "openInterestHist", period="15m", limit=197)
    if len(oi) >= 13:
        try:
            vals = [float(x.get("sumOpenInterest") or 0) for x in oi]
            last = vals[-1]
            if last > 0:
                if len(vals) >= 97 and vals[-97] > 0:
                    f["oi_chg24"] = (last / vals[-97] - 1.0) * 100
                if vals[0] > 0:
                    f["oi_chg48"] = (last / vals[0] - 1.0) * 100
                if vals[-2] > 0:
                    f["oi_pulse15"] = (last / vals[-2] - 1.0) * 100
        except (TypeError, ValueError, IndexError):
            pass
    top = _fut_hist(sym, "topLongShortPositionRatio", period="1h", limit=49)
    if top:
        try:
            f["top_ratio"] = float(top[-1].get("longShortRatio") or 0) or None
            seg = top[-48:]
            f["top_mean48"] = sum(float(x.get("longShortRatio") or 0) for x in seg) / len(seg)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    glob = _fut_hist(sym, "globalLongShortAccountRatio", period="1h", limit=49)
    if glob:
        try:
            f["global_ratio"] = float(glob[-1].get("longShortRatio") or 0) or None
        except (TypeError, ValueError):
            pass
    tk = _fut_hist(sym, "takerlongshortRatio", period="15m", limit=97)
    if tk:
        try:
            f["taker_ratio"] = float(tk[-1].get("buySellRatio") or 0) or None
            prev = [float(x.get("buySellRatio") or 0) for x in tk[-13:-1]]
            pm = sum(prev) / len(prev) if prev else 0.0
            f["taker_drop"] = pm > 0 and (f["taker_ratio"] or 0) < 0.6 * pm
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    fh = _funding_hist(sym)
    if fh:
        try:
            f["funding_peak"] = max(fh + [funding_now])
            f["funding_prev"] = fh[-1] if len(fh) >= 1 else None
        except Exception:
            pass
    try:
        import market_ws
        ls = market_ws.liq_symbol_stats(sym, window=300)
        f["liq_5m"] = float(ls.get("quote") or 0)
        f["liq_side"] = str(ls.get("side") or "")
        f["liq_n5m"] = int(ls.get("count") or 0)
    except Exception:
        pass
    return f


# ---------------- 语义层（生命周期六阶段） ----------------

def _stage_of(d: dict) -> tuple:
    """语义层：吸筹→点火→垂直拉升→派发顶部→崩跌→沉寂（先到先得）。
    返回 (stage, stage_label, tag, side, note)。"""
    chg24 = d.get("chg24", 0.0) or 0.0
    chg1h = d.get("chg1h", 0.0) or 0.0
    chg3 = d.get("chg3d", 0.0) or 0.0
    chg30 = d.get("chg30d", 0.0) or 0.0
    pos = d.get("pos", 1.0)
    oi24 = d.get("oi_chg24", 0.0) or 0.0
    oi48 = d.get("oi_chg48", 0.0) or 0.0
    oi15 = d.get("oi_pulse15", 0.0) or 0.0
    fund = d.get("funding", 0.0) or 0.0
    fund_pk = max(d.get("funding_peak") or fund, fund)
    tr = d.get("taker_ratio") or 0.0
    tdrop = bool(d.get("taker_drop"))
    top = d.get("top_ratio") or 0.0
    liq5 = d.get("liq_5m", 0.0) or 0.0
    liqside = d.get("liq_side", "") or ""

    # 1) 崩跌：连环多头清算 / OI 价齐跌
    if (liqside == "long" and liq5 >= 1e6) or chg1h <= -5.0 or (chg24 <= -12.0 and oi24 <= -5.0):
        return ("CRASH", "崩跌", "崩跌 · 清算潮", "WATCH_SHORT",
                "价跌伴随爆仓/OI 同落 = 多杀多；接刀风险大，等清算出清、量能枯竭再谈企稳")
    # 2) 派发顶部：量高潮+长上影 / funding 极值回落 / OI 顶背离 / 爆仓潮后 OI 骤降
    dist = ((pos >= 0.80 and d.get("vol_climax_d") and d.get("upper_shadow_d"))
            or (fund_pk >= 0.003 and fund <= 0.4 * fund_pk and chg24 < 5.0)
            or (chg24 >= 10.0 and oi24 <= -5.0)
            or (liqside == "long" and liq5 >= 5e5 and oi15 <= -2.0)
            or (top > 2.0 and chg24 >= 20.0 and tdrop))
    if dist:
        return ("DISTRIBUTION", "派发顶部", "派发 · 顶部预警", "WATCH_SHORT",
                "高位量价背离（量高潮长上影 / 费率极值回落 / OI 顶背离 / 爆仓潮）= 派发嫌疑；只减不追")
    # 3) 垂直拉升：量 10-50x / funding 0.1-0.5% 过热
    if chg24 >= 25.0 or (chg1h >= 5.0 and (d.get("rvol15", 0) or 0) >= 3.0) \
            or ((d.get("flow", 0) or 0) >= 1000 and chg24 >= 10.0):
        return ("VERTICAL", "垂直拉升", "起飞 · 垂直拉升", "LONG",
                "短时暴力拉升 + 放量（留意 funding 过热与点差扩大）；追高风险大，仅持仓者带移动止损")
    # 4) 点火：破位放量 / OI 脉冲 / taker 买比飙升
    if (d.get("breakout20") and (d.get("rvol_d", 0) or 0) >= 2.0) \
            or (oi15 >= 5.0 and chg24 >= 3.0) \
            or (tr >= 1.85 and (d.get("rvol15", 0) or 0) >= 2.0 and chg24 >= 2.0):
        return ("IGNITION", "点火", "点火 · 破位启动", "LONG",
                "放量突破平台 + 买盘主导（taker 买比 / OI 加速）；启动初期，小仓试错、破位即走")
    # 5) 吸筹：价平量升 / funding 转正 / OI 蓄力
    accum = (pos <= 0.55 and abs(chg30) <= 20.0 and (d.get("rvol_d", 0) or 0) >= 1.5
             and -12.0 <= chg3 <= 10.0)
    accum_sig = (oi48 >= 20.0 and abs(chg24) <= 3.0) \
        or (fund > 0 and (d.get("funding_prev") is not None and (d["funding_prev"] or 0) <= 0))
    if accum and (accum_sig or (d.get("rvol_d", 0) or 0) >= 2.0):
        tail = "（OI 48h 蓄力 / funding 转正佐证）" if accum_sig else ""
        return ("ACCUMULATION", "吸筹", "吸筹 · 启动前", "LONG",
                f"低位放量但价被压住{tail}；点火信号出现前小仓埋伏、破位即走")
    # 6) 沉寂：量枯回吐
    if (d.get("rvol_d", 1.0) or 1.0) <= 0.6 and (d.get("amp24", 0.0) or 0.0) <= 5.0 \
            and abs(chg24) <= 3.0:
        return ("DORMANT", "沉寂", "沉寂 · 泵后回吐" if chg30 >= 30.0 else "沉寂 · 无波动",
                "WATCH", "量能枯竭、波动收敛；仅有大起大落史者保持观察")
    return (None, "", "", "", "")


def _radar_score(t: dict, f: dict, d: dict, cooldown: bool) -> int:
    """合成 0-99 妖币度：flow_price 归一化为核心权重 + 触发层各因子 + 确认层加减分。"""
    s = 0.0
    s += min(28.0, (t.get("flow", 0.0) or 0.0) / 1000.0 * 28.0)
    s += min(12.0, abs(t.get("jump_L", 0.0) or 0.0) / 6.0 * 12.0)
    s += min(8.0, abs(t.get("speed5m", 0.0) or 0.0))
    s += min(10.0, abs(d.get("chg24", 0.0) or 0.0) / 25.0 * 10.0)
    vr = max((t.get("rvol15", 0.0) or 0.0) - 1.0, (d.get("rvol_d", 0.0) or 0.0) - 1.0)
    s += min(12.0, max(0.0, vr) / 4.0 * 12.0)
    s += min(5.0, (d.get("amp24", 0.0) or 0.0) / 30.0 * 5.0)
    # 确认层加减
    chg24 = d.get("chg24", 0.0) or 0.0
    oi24 = f.get("oi_chg24", 0.0) or 0.0
    if chg24 >= 3.0 and oi24 >= 5.0:
        s += 6.0                                   # 价↑OI↑ 新钱趋势
    elif chg24 >= 3.0 and oi24 <= -5.0:
        s += 3.0                                   # 价↑OI↓ 轧空虚涨（顶前兆）
    if (f.get("oi_pulse15", 0.0) or 0.0) >= 5.0:
        s += 4.0
    af = abs(f.get("funding", 0.0) or 0.0)
    if af >= 0.003:
        s += 4.0
    elif af >= 0.0015:
        s += 2.0
    tr_ = f.get("top_ratio") or 0.0
    if tr_ >= 1.5 or 0 < tr_ <= 0.7:
        s += 3.0
    if (f.get("taker_ratio") or 0.0) >= 1.85:
        s += 3.0
    lq = f.get("liq_5m", 0.0) or 0.0
    if lq >= 1e6:
        s += 5.0
    elif lq >= 3e5:
        s += 2.0
    if d.get("beta_damped"):
        s *= 0.6                                   # BTC β 残差：普涨普跌打折
    if cooldown:
        s = min(s, 59.0)                           # 冷却期压分，让新面孔排前面
    return int(round(max(1.0, min(99.0, s))))


# ---------------- v2 主流程 ----------------

def get_radar_v2(force: bool = False, top_n: int = 110, min_qv: float = RADAR2_FLOOR) -> dict:
    """妖币雷达 v2 全量扫描（5 分钟缓存）。coins 含 stage/score/factors，按妖币度降序。"""
    now = time.time()
    if not force and _radar2_cache["data"] and now - _radar2_cache["ts"] < RADAR2_TTL:
        return _radar2_cache["data"]
    cycle_ts = now
    snap_all = get_snapshot()
    snap = {r["symbol"]: r for r in snap_all}
    floor = max(min_qv or 0, RADAR2_FLOOR)
    pool_syms = [r["symbol"] for r in snap_all
                 if r["quote_volume"] >= floor and _is_eligible(r["symbol"])][:top_n]
    if not pool_syms:
        raise RuntimeError("empty pool (snapshot unavailable)")
    d2 = _daily2_bars(top_n, floor, force=force)
    rates = get_funding_rates()

    # —— 短时窗 K 线并发（15m 结构 + 5m 速度） ——
    k15, k5 = {}, {}
    with _cf.ThreadPoolExecutor(max_workers=10) as ex:
        f15 = {ex.submit(_klines_raw, s, "15m", 288): s for s in pool_syms}
        f5 = {ex.submit(_klines_raw, s, "5m", 96): s for s in pool_syms}
        for fut in _cf.as_completed(f15):
            arr = fut.result()
            if arr:
                k15[f15[fut]] = arr
        for fut in _cf.as_completed(f5):
            arr = fut.result()
            if arr:
                k5[f5[fut]] = arr
    if not k15:
        raise RuntimeError("15m klines unavailable")
    btc15 = _ohlcv(k15.get("BTCUSDT", []))[3]

    # —— 逐币特征合成 + 触发判定 ——
    feats: dict = {}
    n_new = n_wash = 0
    for sym in pool_syms:
        r = snap.get(sym)
        if not r:
            continue
        o15, h15, l15, c15, v15 = _ohlcv(k15.get(sym, []))
        o5, h5, l5, c5, v5 = _ohlcv(k5.get(sym, []))
        if len(c15) < 30:
            continue
        d = {"chg24": r["change_pct"],
             "amp24": (r["high"] - r["low"]) / r["low"] * 100 if r["low"] > 0 else 0.0,
             "price": r["price"], "quote_volume": r["quote_volume"],
             "funding": rates.get(sym, 0.0)}
        # 过滤层：新币 / 刷量 / 假量（单笔均额过低）
        b = d2.get(sym)
        rr = 0.0
        if b:
            df = _daily_feats(b, now)
            d.update(df)
            if df.get("amp7d"):
                rr = d["amp24"] / df["amp7d"] if df["amp7d"] > 0 else 0.0
            if (df.get("listed_days") or 999) < NEW_COIN_DAYS:
                n_new += 1
                continue
            if df.get("wash"):
                n_wash += 1
                continue
            cnt = r.get("count") or 0
            if cnt > 0 and r["quote_volume"] > 1e6 and r["quote_volume"] / cnt < 5.0:
                n_wash += 1
                continue
        if len(c15) >= 2 and c15[-2] > 0:
            d["chg1h"] = (c15[-1] / c15[-5] - 1.0) * 100 if len(c15) >= 5 and c15[-5] > 0 else 0.0
        else:
            d["chg1h"] = 0.0
        t = {"jump_L": _jump_L(c15),
             "flow": _flow_price(o15, h15, l15, c15, v15),
             "rvol15": _rvol(v15),
             "streak": _consec_streak(o15, c15, v15),
             "chg24": r["change_pct"], "amp24": d["amp24"]}
        t.update(_short_sweep(o15, c15, v15))
        if len(c5) >= 8:
            sp, acc = _velocity(c5)
            t["speed5m"] = sp
            t["accel"] = acc
            t["strongvol"] = _strongvol60(v5)
        else:
            t["speed5m"] = 0.0
            t["accel"] = False
            t["strongvol"] = False
        d["flow"] = t["flow"]
        d["jump_L"] = t["jump_L"]
        d["rvol15"] = t["rvol15"]
        d["speed5m"] = t["speed5m"]
        # BTC β 残差
        if btc15 and len(c15) >= 30 and len(btc15) >= 30:
            s0 = max(1, len(c15) - 96)
            b0 = max(1, len(btc15) - 96)
            sym_rets = [math.log(c15[i] / c15[i - 1]) for i in range(s0, len(c15))
                        if c15[i - 1] > 0 and c15[i] > 0]
            btc_rets = [math.log(btc15[i] / btc15[i - 1]) for i in range(b0, len(btc15))
                        if btc15[i - 1] > 0 and btc15[i] > 0]
            bb = _btc_beta(sym_rets, btc_rets, r["change_pct"],
                           _btc_chg24(btc15, r["change_pct"]))
            d["btc_beta"] = bb["beta"]
            d["btc_residual"] = bb["residual"]
            d["beta_damped"] = bb["damped"]
        hit, reasons = _trigger_hit(t, r, rr)
        # 无 5m 数据也允许日线/15m 触发（速度触发缺数据自动跳过）
        feats[sym] = {"d": d, "t": t, "hit": hit, "reasons": reasons, "range_ratio": rr}

    # —— 确认层候选：触发命中 ∪ 日线吸筹证据，按 |24h 涨跌| 优先，封顶 24 币 ——
    def _accum_evid(f: dict) -> bool:
        d = f["d"]
        return (d.get("pos", 1.0) <= 0.55 and (d.get("rvol_d", 0) or 0) >= 1.5
                and -12.0 <= (d.get("chg3d", 0) or 0) <= 10.0
                and not d.get("near_low", False)
                and (d.get("chg30d", 0) or 0) >= -40.0)

    cands = {s for s, f in feats.items() if f["hit"]} | {s for s, f in feats.items() if _accum_evid(f)}
    if len(cands) > 24:
        cands = set(sorted(cands, key=lambda s: abs(feats[s]["d"]["chg24"]), reverse=True)[:24])
    confirms: dict = {}
    if cands:
        with _cf.ThreadPoolExecutor(max_workers=10) as ex:
            futs = {ex.submit(_confirm_factors, s, feats[s]["d"].get("funding", 0.0)): s
                    for s in cands}
            for fut in _cf.as_completed(futs):
                try:
                    confirms[futs[fut]] = fut.result()
                except Exception:
                    pass

    # —— 语义层 + 评分 + 冷却 + 出行 ——
    rows = []
    stage_counts: dict = {}
    for sym, f in feats.items():
        d = f["d"]
        cf = confirms.get(sym) or {}
        d.update({k: cf[k] for k in ("oi_chg24", "oi_chg48", "oi_pulse15", "top_ratio",
                                     "top_mean48", "global_ratio", "taker_ratio", "taker_drop",
                                     "liq_5m", "liq_side", "funding_peak", "funding_prev")
                  if k in cf})
        prev = _radar_prev.get(sym)
        stage, slabel, tag, side, note = _stage_of(d)
        if not f["hit"] and not stage:
            continue                                  # 未触发且无语义 → 不出行
        if stage:
            cooldown = bool(prev and prev[1] == stage and cycle_ts - prev[0] < RADAR2_COOLDOWN)
        else:
            stage, slabel = "ACTIVE", "异动"
            tag = "异动 · 待确认"
            side = "LONG" if (d.get("chg24") or 0) >= 0 else "WATCH_SHORT"
            note = "短时窗触发但阶段特征未收敛；结合确认因子观察量价配合"
            cooldown = bool(prev and prev[1] == stage and cycle_ts - prev[0] < RADAR_COOLDOWN_V1)
        _radar_prev[sym] = (cycle_ts, stage)
        score = _radar_score(f["t"], cf, d, cooldown)
        # 确认因子补充触发依据
        reasons = list(f["reasons"])
        if cf:
            if abs(cf.get("oi_chg24", 0) or 0) >= 5:
                reasons.append(f"OI 24h {cf['oi_chg24']:+.1f}%")
            if abs(cf.get("funding", 0) or 0) >= 0.0015:
                reasons.append(f"费率 {cf['funding'] * 100:+.3f}%")
            if (cf.get("taker_ratio") or 0) >= 1.85:
                reasons.append(f"taker {cf['taker_ratio']:.2f}")
            if (cf.get("top_ratio") or 0) >= 1.5 or 0 < (cf.get("top_ratio") or 1) <= 0.7:
                reasons.append(f"大户比 {cf['top_ratio']:.2f}")
            if (cf.get("liq_5m", 0) or 0) >= 3e5:
                reasons.append(f"爆仓 ${cf['liq_5m'] / 1e6:.1f}M/5m")
        rows.append({
            "symbol": sym, "price": d["price"],
            "change24_pct": round(d.get("chg24", 0.0), 2),
            "change1h_pct": round(d.get("chg1h", 0.0) or 0.0, 2),
            "change3d_pct": round(d.get("chg3d", 0.0) or 0.0, 2),
            "change7d_pct": round(d.get("chg7d", 0.0) or 0.0, 2),
            "change30d_pct": round(d.get("chg30d", 0.0) or 0.0, 2),
            "drawdown_pct": round(d.get("dd", 0.0) or 0.0, 2),
            "from_low_pct": round(d.get("from_low", 0.0) or 0.0, 2),
            "position_pct": round((d.get("pos") or 0.0) * 100, 1),
            "quote_volume": d["quote_volume"],
            "vol_ratio": round(d.get("rvol_d", 0.0) or 0.0, 2),
            "rvol15": round(d.get("rvol15", 0.0) or 0.0, 2),
            "amp24": round(d.get("amp24", 0.0) or 0.0, 2),
            "stage": stage, "stage_label": slabel, "tag": tag, "side": side,
            "note": note, "score": score,
            "floor_rising": bool(d.get("floor_rising")),
            "factors": {
                "flow": round(d.get("flow", 0.0) or 0.0, 1),
                "jump": round(d.get("jump_L", 0.0) or 0.0, 2),
                "speed5m": round(d.get("speed5m", 0.0) or 0.0, 2),
                "rvol15": round(d.get("rvol15", 0.0) or 0.0, 2),
                "amp24": round(d.get("amp24", 0.0) or 0.0, 2),
                "funding": cf.get("funding", d.get("funding", 0.0)),
                "funding_peak": cf.get("funding_peak"),
                "oi_chg24": round(cf.get("oi_chg24", 0.0) or 0.0, 2),
                "oi_pulse15": round(cf.get("oi_pulse15", 0.0) or 0.0, 2),
                "top_ratio": cf.get("top_ratio"),
                "global_ratio": cf.get("global_ratio"),
                "taker_ratio": cf.get("taker_ratio"),
                "liq_5m": round(cf.get("liq_5m", 0.0) or 0.0, 0),
                "liq_side": cf.get("liq_side", ""),
                "btc_beta": d.get("btc_beta"),
                "btc_residual": d.get("btc_residual"),
            },
            "reasons": reasons[:6],
            "listed_days": round(d.get("listed_days", 0) or 0, 1) if d.get("listed_days") else None,
            "cooldown": cooldown,
        })
        stage_counts[stage] = stage_counts.get(stage, 0) + 1

    rows.sort(key=lambda x: x["score"], reverse=True)
    rows_ign = [r for r in rows if r["stage"] in ("ACCUMULATION", "IGNITION")]
    rows_tk = [r for r in rows if r["stage"] in ("VERTICAL", "DISTRIBUTION", "CRASH")]
    rows_tk += [r for r in rows if r["stage"] == "DORMANT"
                and (r.get("change30d_pct") or 0) >= 30.0]
    rows_tk.sort(key=lambda x: x["score"], reverse=True)
    rows_ign.sort(key=lambda x: x["score"], reverse=True)

    bars_env = {s: (b[3], b[4]) for s, b in d2.items()}
    payload = {
        "coins": rows, "takeoff": rows_tk, "ignition": rows_ign,
        "scanned": len(feats), "candidates": len(pool_syms),
        "triggered": sum(1 for f in feats.values() if f["hit"]),
        "confirmed": len(confirms),
        "excluded_new": n_new, "excluded_wash": n_wash,
        "min_qv": floor, "env": _env_regime(bars_env),
        "stage_counts": stage_counts, "engine": "v2",
        "updated_at": int(cycle_ts), "ttl": RADAR2_TTL,
    }
    _radar2_cache["data"] = payload
    _radar2_cache["ts"] = cycle_ts
    # 妖币追踪（v1.5.2）：启动前发现的币登记进跟踪表（延迟 import 防循环，异常不影响雷达）
    try:
        import radar_tracker
        radar_tracker.record_from_radar(payload)
    except Exception:
        pass
    # 清理 24h 前的冷却记录
    for k in [k for k, v in _radar_prev.items() if cycle_ts - v[0] > 86400]:
        _radar_prev.pop(k, None)
    return payload


def _btc_chg24(btc_closes, fallback: float) -> float:
    """BTC 24h 涨跌（15m 收盘，96 根前）；数据不足回退 0（禁用 damping）。"""
    if len(btc_closes) >= 97 and btc_closes[-97] > 0:
        return (btc_closes[-1] / btc_closes[-97] - 1.0) * 100
    return 0.0


RADAR_COOLDOWN_V1 = RADAR2_COOLDOWN  # 兼容命名


def _get_monster_v1(force: bool = False, top_n: int = 120, min_qv: float = 2e6) -> dict:
    """v1 兜底：日K 35 日窗口起飞逻辑（v2 引擎异常时保供）。"""
    now = time.time()
    if not force and _monster_cache["data"] and now - _monster_cache["ts"] < MONSTER_TTL:
        return _monster_cache["data"]
    bars = get_candidate_bars(top_n=top_n, min_qv=min_qv, force=force)
    rows = {r["symbol"]: r for r in get_snapshot() if r["quote_volume"] >= min_qv}
    analyzed = []
    for sym, (closes, vols) in bars.items():
        t = rows.get(sym)
        if not t:
            continue
        out = _analyze_takeoff(sym, t, closes, vols)
        if out:
            analyzed.append(out)
    analyzed.sort(key=lambda x: x["score"], reverse=True)
    payload = {
        "mode": "takeoff",
        "coins": analyzed,
        "scanned": len(analyzed),
        "candidates": len(bars),
        "min_qv": min_qv,
        "env": _env_regime(bars),
        "updated_at": int(now),
        "ttl": MONSTER_TTL,
        "engine": "v1",
    }
    _monster_cache["data"] = payload
    _monster_cache["ts"] = now
    return payload


def _get_ignition_v1(force: bool = False, top_n: int = 120, min_qv: float = 2e6) -> dict:
    """v1 兜底：日K 90 日窗口吸筹逻辑（v2 引擎异常时保供）。"""
    now = time.time()
    if not force and _ignition_cache["data"] and now - _ignition_cache["ts"] < MONSTER_TTL:
        return _ignition_cache["data"]
    bars = get_candidate_bars(top_n=top_n, min_qv=min_qv, force=force)
    rows = {r["symbol"]: r for r in get_snapshot() if r["quote_volume"] >= min_qv}
    analyzed = []
    for sym, (closes, vols) in bars.items():
        t = rows.get(sym)
        if not t:
            continue
        out = _analyze_ignition(sym, t, closes, vols)
        if out:
            analyzed.append(out)
    analyzed.sort(key=lambda x: x["score"], reverse=True)
    payload = {
        "mode": "ignition",
        "coins": analyzed,
        "scanned": len(analyzed),
        "candidates": len(bars),
        "min_qv": min_qv,
        "env": _env_regime(bars),
        "updated_at": int(now),
        "ttl": MONSTER_TTL,
        "engine": "v1",
    }
    _ignition_cache["data"] = payload
    _ignition_cache["ts"] = now
    return payload


def get_monster_coins(force: bool = False, top_n: int = 110, min_qv: float = 2e6) -> dict:
    """起飞中·追涨高风险（妖币雷达 v2 四层模型；异常回退 v1 日K 兜底）。
    v2 阶段映射：垂直拉升/派发顶部/崩跌/沉寂(泵后) → takeoff 列表。"""
    try:
        v2 = get_radar_v2(force=force, top_n=top_n if top_n and top_n > 0 else 110)
        return {"mode": "takeoff",
                "coins": v2.get("takeoff", []),
                "scanned": v2.get("scanned", 0),
                "candidates": v2.get("candidates", 0),
                "triggered": v2.get("triggered", 0),
                "min_qv": v2.get("min_qv", 0),
                "env": v2.get("env"),
                "stage_counts": v2.get("stage_counts", {}),
                "engine": v2.get("engine", "v2"),
                "updated_at": v2.get("updated_at", int(time.time())),
                "ttl": v2.get("ttl", RADAR2_TTL)}
    except Exception:
        return _get_monster_v1(force=force, top_n=top_n, min_qv=min_qv)


def get_ignition_coins(force: bool = False, top_n: int = 110, min_qv: float = 2e6) -> dict:
    """启动前·埋伏窗口（妖币雷达 v2 四层模型；异常回退 v1 日K 兜底）。
    v2 阶段映射：吸筹/点火 → ignition 列表。"""
    try:
        v2 = get_radar_v2(force=force, top_n=top_n if top_n and top_n > 0 else 110)
        return {"mode": "ignition",
                "coins": v2.get("ignition", []),
                "scanned": v2.get("scanned", 0),
                "candidates": v2.get("candidates", 0),
                "triggered": v2.get("triggered", 0),
                "min_qv": v2.get("min_qv", 0),
                "env": v2.get("env"),
                "stage_counts": v2.get("stage_counts", {}),
                "engine": v2.get("engine", "v2"),
                "updated_at": v2.get("updated_at", int(time.time())),
                "ttl": v2.get("ttl", RADAR2_TTL)}
    except Exception:
        return _get_ignition_v1(force=force, top_n=top_n, min_qv=min_qv)


def scan_symbols(symbols: list, min_change_pct: float = 3.0, min_funding: float = 0.01,
                 force: bool = False) -> list:
    """对任意符号列表扫描异常（用于并行子代理分板块扫描）。
    force=True 时（指定交易对精确查价）跳过波动/资金费率过滤，只要有价格即返回。
    兼容仅永续上市币种：SPOT ticker 缺失时自动回退 fapi 公开端点取价。"""
    rates = get_funding_rates()
    tickers = get_24h(symbols)
    results = []
    for sym in symbols:
        t = tickers.get(sym)
        # 现货端查不到（如仅 U 本位永续上市）→ 回退 fapi 单币 ticker
        if not t:
            t = get_futures_ticker(sym)
        if not t:
            continue
        try:
            price = float(t["lastPrice"])
            change = float(t["priceChangePercent"])
        except (KeyError, ValueError):
            continue
        if price <= 0:
            continue
        funding = rates.get(sym)
        if funding is None:
            funding = get_funding_rate(sym)  # 全量 funding 没拿到时按需补单币
        if not force and not ((abs(change) >= min_change_pct) or (abs(funding) >= min_funding)):
            continue
        results.append({
            "symbol": sym, "price": price, "change_pct": change,
            "volume": float(t.get("quoteVolume", 0) or 0),
            "funding_rate": funding,
            "direction": "做多" if change > 0 else "做空",
            "emoji": "🟢" if change > 0 else "🔴",
            "score": abs(change) + abs(funding) * 100,
        })
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# 板块分组（并行子代理用）
SECTORS = {
    "大盘": ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"],
    "公链/L2": ["ADAUSDT", "AVAXUSDT", "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "TONUSDT", "SUIUSDT"],
    "Meme": ["DOGEUSDT", "PEPEUSDT"],
    "老牌": ["DOTUSDT", "MATICUSDT", "LTCUSDT", "TRXUSDT", "LINKUSDT"],
}



if __name__ == "__main__":
    import pprint
    pprint.pprint(scan_top20()[:5])
