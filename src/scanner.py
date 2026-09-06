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
                    })
                except (TypeError, ValueError):
                    continue
            out.sort(key=lambda x: x["quote_volume"], reverse=True)
            _snap_cache["rows"] = out
            _snap_cache["ts"] = now
            rows = out
    return rows[:limit] if limit and limit > 0 else list(rows)


def get_funding_rates() -> dict:
    """一次性拉取所有交易对的资金费率（避免逐币请求）。失败回退单请求。"""
    try:
        r = _session.get(f"{FAPI}/fapi/v1/premiumIndex", timeout=10)
        r.raise_for_status()
        return {d["symbol"]: float(d.get("lastFundingRate", 0) or 0) for d in r.json()}
    except Exception:
        rates = {}
        for sym in TOP_SYMBOLS():  # 动态识别的大盘币（按当前成交额排序）
            try:
                r = _session.get(f"{FAPI}/fapi/v1/fundingRate", params={"symbol": sym}, timeout=5)
                rates[sym] = float(r.json().get("lastFundingRate", 0) or 0)
            except Exception:
                rates[sym] = 0.0
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
                  universe_size: int = 150, max_signals: int = 16) -> list:
    """在成交额前 universe_size 的全市场交易对上扫描异动（替代固定 20 币）。"""
    rows = get_snapshot(limit=universe_size)
    if not rows:
        return []
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


def get_monster_coins(force: bool = False, top_n: int = 120, min_qv: float = 2e6) -> dict:
    """起飞中·追涨高风险列表（原妖币逻辑，明示已启动追高风险）。"""
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
    }
    _monster_cache["data"] = payload
    _monster_cache["ts"] = now
    return payload


def get_ignition_coins(force: bool = False, top_n: int = 120, min_qv: float = 2e6) -> dict:
    """启动前·埋伏窗口：低位放量吸筹（点火前），主推模式。"""
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
    }
    _ignition_cache["data"] = payload
    _ignition_cache["ts"] = now
    return payload


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
