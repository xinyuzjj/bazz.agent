"""Binance 行情扫描器 - 基于公开 REST API（无需 API Key）
- 单次拉全市场 24h 快照（一次性 /api/v3/ticker/24hr），本地 TTL 缓存
- 异动信号 / 涨跌幅榜在"成交额前 N"的广阔交易对池上计算（不再固定 20 币）
- 资金费率一次性批量拉取（premiumIndex），避免 N+1 请求
- 「大盘币」按当前 24h 成交额**动态识别**（get_top_liquid_symbols），不再写死列表
"""
import os
import json
import re
import time
import threading
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
                        # v1.6.6（档一「已付费未取用」）：现货 ticker **带 bid/ask/bidQty/askQty**，
                        # 此前整体丢弃。买卖价差是最早的流动性恶化信号（盘口先变薄，价格与成交量后动），
                        # 且与「现货无深度」这个控盘指纹同源，是它的**实时版本**。
                        # 注：合约 ticker **没有** bid/ask（2026-09 实测字段全集里不含），
                        # 所以纯合约币拿不到价差 —— 那类币只能标「未测到」，不能拿别的数凑。
                        "bid": float(d.get("bidPrice") or 0),
                        "ask": float(d.get("askPrice") or 0),
                        "bid_qty": float(d.get("bidQty") or 0),
                        "ask_qty": float(d.get("askQty") or 0),
                        "weighted_avg_price": float(d.get("weightedAvgPrice") or 0),
                    })
                except (TypeError, ValueError):
                    continue
            out.sort(key=lambda x: x["quote_volume"], reverse=True)
            _snap_cache["rows"] = out
            _snap_cache["ts"] = now
            rows = out
    return rows[:limit] if limit and limit > 0 else list(rows)


FUNDING_TTL = 30.0  # 秒；资金费率批量拉取较稳，加短 TTL 缓存避免每个 30s 轮询都全量打 premiumIndex
# v1.7.1（#10）：币安**默认**资金费率结算周期（小时）。与 `radar_tracker.FUNDING_CYCLE_H`
# 同义，两侧都只用在「周期未测到时的回退」上 —— 有护栏断言两者相等，防止以后只改一边。
FUNDING_CYCLE_DEFAULT_H = 8.0
# v1.6.6（档一「已付费未取用」）：`premiumIndex` 的响应本来就同时返回
# `markPrice` / `indexPrice` / `nextFundingTime` / `interestRate` / `estimatedSettlePrice`，
# 此前只取了 `lastFundingRate`，其余全丢。缓存结构因此扩成 `meta`（逐币全字段）。
# `markPrice − indexPrice` 的背离是外部文献里「跨所标记价格操纵」的直接观测量：
# 标记价被单向拉离指数价，意味着有人在用标记价推动强平（而不是真实成交）。
_funding_cache = {"ts": 0.0, "rates": {}, "meta": {}}


def get_funding_meta() -> dict:
    """逐币 `premiumIndex` 全字段（v1.6.6）。

    返回 `{symbol: {"funding", "mark_price", "index_price", "next_funding_ts",
    "interest_rate", "est_settle", "basis_bps"}}`。
    `basis_bps` = 标记价对指数价的偏离（基点），**符号有意义**：正 = 标记价高于指数价。
    与 `get_funding_rates` 共用同一份缓存，不产生额外请求。
    """
    get_funding_rates()          # 负责拉取 + 填充缓存（含 meta）
    return _funding_cache["meta"] or {}


# v1.7.1（#10 成本模型修正）：**资金费率结算周期并非全市场统一 8 小时**。
# 实测 `/fapi/v1/fundingInfo`（782 条，覆盖成交额前 110 全部）分布为：
#     8h = 312 个 ／ **4h = 467 个** ／ **1h = 3 个**
# 即 4 小时周期才是多数；成交额前 110 里有 **40 个**是 4h（ENA/HYPE/ONDO/TRUMP/PENGU/
# TAO/PUMP/ZEC/XAU…）。而成本模型此前把周期**硬编码成 8h** ——
# 对这 40 个币的资金费率成本**低估 2 倍**，对 1h 周期的低估 8 倍。
# 这是「数字看起来没问题、方向却系统性偏乐观」的典型：不报错、不飘红，只是偏。
#
# `fundingInfo` 变化极慢（调整结算频率是公告级事件），给长 TTL 6 小时。
# 拿不到 → `measured=False`，调用方必须回退默认 8h 并把「未测到」如实标出，
# **不得**把「没测到周期」悄悄当成某个具体周期（与全项目 None/0 语义纪律一致）。
FUNDING_INFO_TTL = 21600.0   # 6 小时
_funding_info_cache = {"ts": 0.0, "intervals": {}, "measured": False}


# v1.7.1（#10 成本模型闭环）：**合约盘口价差**。
# 成本模型里 `SLIP_PCT = 0.15%`（15bps/边）是**保守常数、非实测**，报告里写明
# 「等 C6 时序库攒够「价差 + 成交额」样本再回来标定」。但那个闭环**当时是断的**：
# `ts_series.spread_bps` 存的是**现货** ticker 的 bid/ask，而**纯合约币恒为 None** ——
# 偏偏妖币里「只上合约、无现货深度」是经典形态，于是最该标定的那批币一个样本都不会有。
#
# 修法不是逐币打 `/fapi/v1/depth`（110 个币 = 110 次请求，且合约 ticker 不返回盘口），
# 而是 `/fapi/v1/ticker/bookTicker` **不带 symbol**：实测一次返回**全市场 766 个**合约的
# `bidPrice`/`askPrice`（1.64s，1 次请求）。于是全市场价差一次到位，零扇出。
# 实测（v1.7.1）中位 3.97bps / p90 9.81bps / max 29.11bps —— 与假设的 15bps/边 同量级
# 且更小，说明常数方向**偏保守**（高估成本，不会把结论说得好听）。
BOOK_TTL = 30.0   # 秒；与 FUNDING_TTL 同量级（盘口变化快，但成本标定不需要秒级精度）
_book_cache = {"ts": 0.0, "spreads": {}, "measured": False}


def get_book_spreads() -> tuple:
    """全市场**合约**盘口价差（基点）。返回 `(spreads, measured)`（v1.7.1）。

    `spreads` = `{symbol: bps}`，`bps = (ask − bid) / mid × 1e4`（mid = (ask+bid)/2）。
    `measured` 语义与 `get_funding_intervals` 一致：True = 真读到了，False = 失败兜底。

    只收 `bid>0 且 ask>0 且 ask>=bid` 的行：缺字段/零价/交叉盘一律**不入表**（宁可留空
    = 未测到，也不写 0 —— 0 会被读成「零价差」即「完美流动性」，那是相反的结论）。
    30s TTL；失败返回上次成功内容（可能为空表），`measured` 保持上次的值。
    """
    now = time.time()
    if _book_cache["measured"] and now - _book_cache["ts"] < BOOK_TTL:
        return _book_cache["spreads"], True
    try:
        r = _session.get(f"{FAPI}/fapi/v1/ticker/bookTicker", timeout=10)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list) or not data:
            raise ValueError("bookTicker empty/not a list")
        out: dict = {}
        for d in data:
            sym = str(d.get("symbol") or "")
            if not sym:
                continue
            try:
                b = float(d.get("bidPrice") or 0)
                a = float(d.get("askPrice") or 0)
            except (TypeError, ValueError):
                continue
            if b > 0 and a >= b:
                mid = (a + b) / 2.0
                if mid > 0:
                    out[sym] = round((a - b) / mid * 1e4, 2)
        if not out:
            raise ValueError("bookTicker yielded no usable spreads")
        _book_cache.update({"ts": now, "spreads": out, "measured": True})
        return out, True
    except Exception:
        return _book_cache["spreads"], bool(_book_cache["measured"])


def get_funding_intervals() -> tuple:
    """逐币资金费率**结算周期**（小时）。返回 `(intervals, measured)`（v1.7.1）。

    `intervals` = `{symbol: 4.0 / 8.0 / 1.0}`，只取 `fundingIntervalHours`
    （响应里的 `adjustedFundingRateCap/Floor` 是费率上下限，本轮不消费 —— 留着不动，
    免得再落一个「已付费未取用」）。

    `measured` = 本次是否**真的从接口读到了**（True）/ 走的是失败兜底（False）。
    这个布尔是必须的：`intervals` 里查不到某个币，既可能是「该币用默认 8h」，
    也可能是「接口压根没通」—— 两者对成本的结论完全不同，不能合并成同一个 `{}`。
    TTL 6 小时；失败时返回**上一次成功的内容**（若有），否则空表。
    """
    now = time.time()
    if _funding_info_cache["measured"] and now - _funding_info_cache["ts"] < FUNDING_INFO_TTL:
        return _funding_info_cache["intervals"], True
    try:
        r = _session.get(f"{FAPI}/fapi/v1/fundingInfo", timeout=10)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list) or not data:
            raise ValueError("fundingInfo empty/not a list")
        iv: dict = {}
        for d in data:
            sym = str(d.get("symbol") or "")
            if not sym:
                continue
            try:
                h = float(d.get("fundingIntervalHours") or 0)
            except (TypeError, ValueError):
                continue
            # 只收正数：0 / 负数 / 非数值一律不收（宁可回退默认 8h，也不接受一个
            # 会让 `hours / interval` 除零或翻符号的周期）。
            if h > 0:
                iv[sym] = h
        if not iv:
            raise ValueError("fundingInfo yielded no usable intervals")
        _funding_info_cache.update({"ts": now, "intervals": iv, "measured": True})
        return iv, True
    except Exception:
        # 失败：退回上次成功的内容（可能是空表）。`measured` 保持上次的值 ——
        # 若从未成功过，仍是 False，调用方据此知道「这是默认值不是实测值」。
        return _funding_info_cache["intervals"], bool(_funding_info_cache["measured"])


def get_funding_rates() -> dict:
    """一次性拉取所有交易对的资金费率（避免逐币请求）。~30s TTL 缓存。失败回退单请求。

    v1.6.6：同一响应里顺手取走 `markPrice` / `indexPrice` / `nextFundingTime` /
    `interestRate`（见 `_funding_cache["meta"]`）—— 零额外请求。
    """
    now = time.time()
    if _funding_cache["rates"] and now - _funding_cache["ts"] < FUNDING_TTL:
        return _funding_cache["rates"]
    rates: dict = {}
    meta: dict = {}
    try:
        r = _session.get(f"{FAPI}/fapi/v1/premiumIndex", timeout=10)
        r.raise_for_status()
        for d in r.json():
            sym = str(d.get("symbol") or "")
            if not sym:
                continue
            rates[sym] = float(d.get("lastFundingRate", 0) or 0)
            try:
                mp = float(d.get("markPrice") or 0)
            except (TypeError, ValueError):
                mp = 0.0
            try:
                ip = float(d.get("indexPrice") or 0)
            except (TypeError, ValueError):
                ip = 0.0
            # 基差只在两价都有效时才算 —— 缺失写 None（未测到），绝不写 0
            # （0 会被读成「标记价与指数价完全一致」，那是另一个意思）。
            basis_bps = round((mp - ip) / ip * 1e4, 2) if (mp > 0 and ip > 0) else None
            try:
                nft = int(d.get("nextFundingTime") or 0)
            except (TypeError, ValueError):
                nft = 0
            meta[sym] = {
                "funding": rates[sym],
                "mark_price": mp or None,
                "index_price": ip or None,
                "next_funding_ts": (nft // 1000) if nft else None,   # 毫秒 → 秒
                "interest_rate": d.get("interestRate"),
                "est_settle": d.get("estimatedSettlePrice"),
                "basis_bps": basis_bps,
            }
    except Exception:
        rates = {}
        meta = {}
        for sym in TOP_SYMBOLS():  # 动态识别的大盘币（按当前成交额排序）
            try:
                r = _session.get(f"{FAPI}/fapi/v1/fundingRate", params={"symbol": sym}, timeout=5)
                rates[sym] = float(r.json().get("lastFundingRate", 0) or 0)
            except Exception:
                rates[sym] = 0.0
    _funding_cache["ts"] = now
    _funding_cache["rates"] = rates
    _funding_cache["meta"] = meta
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
# v1.6.3：合约「标的是加密币」白名单（exchangeInfo 的 underlyingType == "COIN"），6 小时缓存
# v1.6.6（档一「已付费未取用」）：同一个响应里顺手取走 `onboardDate`（权威合约上线时间）
# 与 `status`。此前只取了 underlyingType + symbol —— 响应里另外 6 个字段全被丢弃，
# 而 onboardDate 恰好是 C1（新币过滤失效）的现成解药，零额外请求。
FUT_CRYPTO_TTL = 6 * 3600.0
_fut_crypto_cache = {"ts": 0.0, "syms": set(), "onboard": {}, "status": {}}


def _fetch_exchange_meta() -> None:
    """拉一次 exchangeInfo，刷新 `_fut_crypto_cache`（syms / onboard / status 一次取齐）。

    只在这一个地方发请求，保证三个派生视图永远同源同批 —— 否则 syms 与 onboard
    可能来自不同时刻的两次拉取，出现「币在白名单里但币龄查不到」的错配。
    失败时**保持旧缓存不动**（不写空值），与扩池前的安全退化行为一致。
    """
    now = time.time()
    try:
        r = _session.get(f"{FAPI}/fapi/v1/exchangeInfo", timeout=20)
        r.raise_for_status()
        payload = r.json()
    except Exception:
        return
    syms, onboard, status = set(), {}, {}
    for c in payload.get("symbols", []) or []:
        if not isinstance(c, dict):
            continue
        s = str(c.get("symbol") or "")
        if not s:
            continue
        # onboardDate 是**毫秒**时间戳。缺失/非法写 None —— 消费方必须按「未知」处理，
        # 不能当成 0（=1970 年，会被读成「上市 2 万年」）也不能当成「刚上市」。
        ts = c.get("onboardDate")
        try:
            onboard[s] = float(ts) / 1000.0 if ts else None
        except (TypeError, ValueError):
            onboard[s] = None
        status[s] = str(c.get("status") or "")
        if str(c.get("underlyingType") or "") == "COIN":
            syms.add(s)
    if syms:
        _fut_crypto_cache["syms"] = syms
        _fut_crypto_cache["onboard"] = onboard
        _fut_crypto_cache["status"] = status
        _fut_crypto_cache["ts"] = now


def futures_crypto_syms(use_cache: bool = True) -> set:
    """U 本位合约中**标的是加密币**的 symbol 集合（`underlyingType == "COIN"`）。

    v1.6.3 扩池后才需要这层过滤：`/fapi/v1/ticker/24hr` 全市场里混着近 200 个
    **代币化股票/大宗**（`contractType = TRADIFI_PERPETUAL`；NVDA / TSLA / XAU / XAG /
    BZ / QQQ / SPY / SKHYNIX … 分属 underlyingType = EQUITY / COMMODITY / INDEX /
    KR_EQUITY / HK_EQUITY / CN_EQUITY / PREMARKET）。它们是**跟踪真实标的**的合约，
    不是庄家控盘的加密妖币，进池只会挤占 top_n 名额 —— 实测 110 个池位里 57 个是纯合约，
    其中一大半是 TradFi，真正的加密纯合约币反而被挤出去。

    用 `underlyingType == "COIN"` 而不是自己的 `EQUITY_PERPS` 白名单（只有 31 条，
    实测只能捞到 11 个 TradFi），接口字段是**权威且随上线自动更新**的。
    注意 **Alpha 币必须保留**（LAB 就是 Alpha 币，subType 带 `Alpha`，`underlyingType` 仍是 COIN），
    meme / 中文盘（如 `龙虾USDT`）同理 —— 它们恰恰是最像妖币的一类。

    拉取失败沿用旧缓存；没有缓存则返回空集 → 纯合约币全部不入池（安全退化为扩池前的行为）。
    """
    now = time.time()
    if use_cache and _fut_crypto_cache["syms"] and now - _fut_crypto_cache["ts"] < FUT_CRYPTO_TTL:
        return set(_fut_crypto_cache["syms"])
    _fetch_exchange_meta()
    return set(_fut_crypto_cache["syms"])


def futures_listed_days(sym: str) -> float | None:
    """合约上市天数（**权威口径**，来自 exchangeInfo 的 `onboardDate`）。

    v1.6.6（问题清单 C1）：此前币龄靠**日线首根 K 线**反推（`_daily_feats` 里的
    `listed_days = (now - first_ms)/86400`），有两个洞：
      ① 上市不足 25 根的币拿不到日线（`_daily2_fetch` 直接返回 None）→ 根本走不到
         币龄判断，整段过滤被跳过（这正是 C1 的静默豁免）；
      ② 现货与合约上市日不同，日线首根只反映**所选那个市场**的历史长度。
    改用 `onboardDate` 后，**任何有合约的币都能判币龄**，且是交易所权威值。

    返回 `None` = **未知**（接口失败或字段缺失）。消费方必须把「未知」与
    「已确认很新」分开处理 —— 把 None 当成 0 会让全市场都被判成新币。
    """
    now = time.time()
    if not _fut_crypto_cache["onboard"] or now - _fut_crypto_cache["ts"] >= FUT_CRYPTO_TTL:
        _fetch_exchange_meta()
    ts = (_fut_crypto_cache["onboard"] or {}).get(sym)
    if not ts:
        return None
    return max(0.0, (now - float(ts)) / 86400.0)


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
            # v1.6.6（档一「已付费未取用」）：ticker 响应里本来就有 `count`（成交笔数）与
            # `weightedAvgPrice`，此前整体丢弃。
            # ⚠️ 2026-09 实测：**合约 ticker 没有 bidPrice/askPrice**（字段全集只有 16 个，
            # 不含盘口）。所以这里的 bid/ask 恒为 0、`spread_bps` 恒为 None —— 这是
            # **诚实的「未测到」**，不是「价差为零」。合约侧的价差只能靠 `depth` 流
            # 或 REST `/fapi/v1/depth` 拿，那是另一条路（见 §14.2）。
            bid = float(d.get("bidPrice") or 0)
            ask = float(d.get("askPrice") or 0)
            mid = (ask + bid) / 2.0
            spread_bps = round((ask - bid) / mid * 1e4, 2) if (bid > 0 and ask >= bid and mid > 0) else None
            out.append({
                "symbol": sym,
                "price": price,
                "change_pct": float(d.get("priceChangePercent") or 0),
                "quote_volume": float(d.get("quoteVolume") or 0),
                "high": float(d.get("highPrice") or 0),
                "low": float(d.get("lowPrice") or 0),
                # v1.6.6（问题清单 C2）：合约 ticker **确实带 `count`（成交笔数）** ——
                # `_radar_pool` 此前把它无条件置 0，注释写「合约 ticker 无成交笔数」，
                # 那是个**错误假设**：它让「单笔均额过低 = 假量」这条防线对池子里
                # 57/110 的纯合约币整体失效（`if cnt > 0` 永不成立），而纯合约 + 无现货
                # 深度恰恰是最容易刷量的形态。缺失时写 0（= 没测到），消费方以 `cnt > 0`
                # 为前提，语义不变，不会把「没测到」误判成「没有成交」。
                "count": int(float(d.get("count") or 0)),
                "bid": bid,
                "ask": ask,
                "spread_bps": spread_bps,
                "weighted_avg_price": float(d.get("weightedAvgPrice") or 0),
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

RADAR2_TTL = 300.0          # 5 分钟一扫（**默认值**；运行时可用 env BAZZ_RADAR2_TTL 覆盖，见 radar2_ttl）
RADAR2_FLOOR = 5e6          # 过滤层：24h 成交额 ≥ $5M 流动性地板
NEW_COIN_DAYS = 30          # 过滤层：上市 < 30 天排除
RADAR2_COOLDOWN = 1800.0    # 过滤层：同币同阶段 30 分钟冷却（防重复报警刷屏）

_radar2_cache = {"ts": 0.0, "data": None}
# v1.6.6（问题清单 #9）：扫描级互斥锁 + 等待上限。见 get_radar_v2 的 docstring。
RADAR2_LOCK_WAIT = 20.0
_radar2_lock = threading.Lock()
_daily2_cache = {"ts": 0.0, "bars": {}, "key": None}   # sym -> (o,h,l,c,v,first_ms)，30 分钟缓存
_radar_prev: dict = {}                    # sym -> (cycle_ts, stage)


# ---------------- 5.0 扫描节流（C3 端到端延迟治理的**后端一半**） ----------------
# 问题清单 C3 记的端到端延迟 3~8 分钟，链路上有三个固定项：
#   ① 触发层 15m K 线  ② 后端 RADAR2_TTL 缓存  ③ 前端 180s 轮询
# 实测（v1.7.1）澄清 ① **不是瓶颈**：`/fapi/v1/klines` 返回的最后一根是**在途未收盘**
# K 线（实测其 closeTime 比当前时间晚 713s），而触发层特征直接读 `c15[-1]`/`c5[-1]`，
# 所以每轮扫描用的都是**当下最新价**，不存在「等 15 分钟收盘」的盲区。
# 真正的固定延迟是 ② + ③。两者都做成可调/可见，而不是继续当常数写死：
#   · ② 本函数：`BAZZ_RADAR2_TTL` 覆盖（运维可在「请求量」与「延迟」之间取舍，
#     不必改代码）；默认仍是 300s，行为不变。
#   · ③ 前端轮询间隔改 60s，并把 `age_sec`（数据已陈旧多少秒）显示出来 ——
#     延迟从「静默发生」变成「看得见」，见 payload 的 `age_sec` / `stale`。
def radar2_ttl() -> float:
    """雷达 v2 缓存 TTL（秒）。env `BAZZ_RADAR2_TTL` 可覆盖；非法/非正/非有限值一律回退默认，不抛。

    `inf` / `nan` 必须一起挡掉：`float("inf") > 0` 为真，放过去会让缓存**永不失效**
    （表现为「雷达再也不更新」），而这是个只在有人手滑写错 env 时才出现的故障。
    """
    raw = (os.environ.get("BAZZ_RADAR2_TTL") or "").strip()
    if raw:
        try:
            v = float(raw)
            if v > 0 and math.isfinite(v):
                return v
        except Exception:
            pass
    return RADAR2_TTL


# ---------------- 5.1 在途请求合并（同缓存键并发去重） ----------------
# 雷达三段扇出（_daily2_bars 日线池 / get_radar_v2 的 15m+5m K 线 / _confirm_factors
# 确认层）在线程池里按缓存键发 REST；两轮扫描并发（缓存过期瞬间/force=True）会对相同
# (sym, interval, limit) 重复打接口（冷启动扇出约 450 次 REST）。_INFLIGHT 把同时进行的
# 重复请求合并成一次：首个调用者注册 Future 并执行 fetch_fn，其余等待其结果。
# 已有 TTL 缓存逻辑不变 —— 合并只针对「同时在途」的重复请求，不改变缓存行为。
_INFLIGHT: dict = {}                       # cache_key -> concurrent.futures.Future
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT_WAIT_SEC = 30.0                  # 等待在途结果的上限（超时自取兜底，防持有者卡死拖死等待者）


def _dedupe_fetch(cache_key, fetch_fn):
    """相同 cache_key 的并发请求只发一次、共享同一结果（线程安全）。
    未命中：注册自己的 Future 再执行 fetch_fn，完成后先移除注册再回填结果；
    命中：限时等待在途 Future（超时循环），超时后自己发请求兜底；
    异常：先移除注册再 set_exception，原样传播给等待者。"""
    with _INFLIGHT_LOCK:
        fut = _INFLIGHT.get(cache_key)
        if fut is None:
            fut = _cf.Future()
            _INFLIGHT[cache_key] = fut
            owner = True
        else:
            owner = False
    if not owner:
        deadline = time.time() + _INFLIGHT_WAIT_SEC
        while True:
            try:
                return fut.result(timeout=max(0.5, deadline - time.time()))
            except _cf.TimeoutError:
                if time.time() >= deadline:
                    break                  # 持有者迟迟未返回 → 自取兜底（不等死锁）
            except BaseException:
                raise                      # 持有者失败：异常原样传播（下轮重试会重新发请求）
        return fetch_fn()
    try:
        res = fetch_fn()
    except BaseException as e:
        with _INFLIGHT_LOCK:
            _INFLIGHT.pop(cache_key, None)
        fut.set_exception(e)
        raise
    with _INFLIGHT_LOCK:
        _INFLIGHT.pop(cache_key, None)
    fut.set_result(res)
    return res


def _klines_raw(sym: str, interval: str = "15m", limit: int = 288,
                fut_fallback: bool = False) -> list:
    """拉 sym 的 K 线原始数组（旧→新，含当前未收 bar），失败返回 []。
    5.1：经 _dedupe_fetch 按 (sym, interval, limit, fut_fallback) 在途合并 —— 覆盖雷达日线池
    （_daily2_fetch）、15m/5m 扫描扇出与 klines_closes/klines_ohlcv 的 spot 路径，
    并发重复请求只发一次 REST。

    v1.6.3：`fut_fallback=True` 时，现货查不到该对（纯合约妖币 RAVE / LAB 只上合约不上现货）
    回退 U 本位合约 K 线。雷达池已扩到合约全市场（见 `_radar_pool`），没有这层回退
    这些币会在这两处被静默丢弃：`_daily2_fetch` 的「不足 25 根」与逐币的 `len(c15) < 30`。
    """
    def _fetch():
        try:
            r = _session.get(f"{SPOT}/api/v3/klines",
                             params={"symbol": sym, "interval": interval, "limit": limit}, timeout=10)
            r.raise_for_status()
            arr = r.json() or []
        except Exception:
            arr = []
        if not arr and fut_fallback:
            try:
                r2 = _session.get(f"{FAPI}/fapi/v1/klines",
                                  params={"symbol": sym, "interval": interval, "limit": limit},
                                  timeout=10)
                r2.raise_for_status()
                arr = r2.json() or []
            except Exception:
                arr = []
        return arr
    return _dedupe_fetch(("klines", sym, interval, limit, fut_fallback), _fetch)


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


# ---------------- 触发层（v1.6.2 方向化） ----------------
# 旧实现 11 条触发规则里 7 条方向无关（abs() 或涨跌双向），实测命中率最高的是
# 「24h 振幅 ≥15%」（22 单里 19 单命中 = 86%）—— 雷达实际是个**波动率探测器**：
# 谁当天暴动得最厉害就登记谁，而暴动之后的币正是均值回归概率最高的。
# 新实现分三条通道：机会型（up，必须顺向）/ 风险型（down，只产崩跌·做空语义）/
# 波动率通道（方向无关，仅用于取出确认因子与风险提示，**不构成做多机会**）。
_TRIG_UP_CHG24 = 3.0       # 机会型顺向涨幅门槛（旧：|chg24| >= 10.0 双向）
# v1.6.5（OPT-04）登记涨幅门槛分段。此前「登记门槛」与「触发门槛」共用 3.0，
# 于是 3% 这个**没有样本支撑**的数字被当成硬挡线（注释里写的「连 3%~12% 也晚了」数据不支持）。
# 实测（安装版 20 笔 LONG 已关单，reasons 原文级统计）：
#   · 9 笔「登记时已涨」样本的 chg24 最小是 **11.3%**（ETHFI），其余 13.9%~21.9%，**9 笔全 dump**；
#   · **3%~11.3% 之间一笔样本都没有** → 有证据的只是「≥11.3% 该挡」。
# 所以把硬挡线外推到 10.0（仍在有据区间下沿，且不越过 11.3 的观测下限），
# 3%~10% 这段改为**降分不挡**：它是「刚点着火」的位置，靠 OI 判据（_piled）二次筛，
# 而不是靠一个拍出来的 3%。这是本轮唯一的「放宽」方向（用户拍板）。
_TRIG_LATE_CHG24 = 10.0    # 硬挡线：chg24 ≥ 它 → 认定「你来晚了」，不进 ignition 组
_TRIG_WARM_CHG24 = _TRIG_UP_CHG24   # 暖启动段下沿（= 机会型触发线）
_TRIG_WARM_PENALTY = 6.0   # 暖启动段 [3, 10) 的降分幅度（只排序 + 记录，不构成闸门）
# v1.6.4 持仓堆积线（价格之外的**第二个「启动」维度**）。
# 证据（安装版 20 笔 LONG 已关单，依据原文级统计，只取前缀 fullmatch 避免误读 "OI 24h"）：
#   · `OI 24h` 为**正**的样本 **9 笔 —— 9 笔全是 dump**（+7.4% ~ +106.3%）；
#     3 笔 moon 里**从来没有出现过正值 OI**。
#   · `OI 24h` 为负的样本只有 2 笔（SEI −6.2 dump / VTHO −14.5 moon）→ 负值无判别力。
# 逻辑：OI 涨 = 已经有人建仓 = 你进去就是接盘；妖币启动前持仓是平的、甚至在减。
# 阈值取 5.0 是**故意的零外推**：依据生成处本身就是 `abs(oi_chg24) >= 5` 才写进 reasons，
# 所以 5.0 正好等于「我实际观测到的那个集合」。更严的 `oi_chg24 <= 0` 在 (0, 5) 区间
# 没有任何样本，属于外推，暂不采用。
# 叠加回放（chg24>=3 或 oi24>=5 即不登记）：20 笔 → 存活 9 笔（3 moon / 6 dump），
# 胜率 15.0% → **33.3%**，且**零误杀**（3 笔 moon 全部留存）。
_TRIG_MAX_OI24 = 5.0       # 持仓堆积线：OI 24h ≥ 它 → 已不算「启动前」
_TRIG_MAX_CHG24 = 12.0     # 追高线：涨幅越过它不再算「启动机会」，交 _stage_of 判 EXTENDED
_TRIG_DOWN_CHG24 = -12.0   # 下跌侧风险触发
_TRIG_AMP_OK = 20.0        # 振幅「活口线」：以内算有活口（加分），超过算接力末端（扣分）
_TRIG_AMP_RISK = _TRIG_AMP_OK   # 波动率通道门槛（≥ 此值才取确认因子，但不进机会池）

# ---- v1.6.3 妖币控盘代理（全链下、零新增接口） ----
# 妖币的定义性特征是「现货控盘 96%+ / 只上合约不上现货 / 零基本面」，
# 但 Binance 公开 Web3 接口**不提供前 N 大持币地址占比**（query-token-audit 只有蜜罐/税率，
# query-token-info.dynamic 只有持币**人数**，query-address-info 只列单钱包持仓）。
# 所以控盘率这个一票否决项拿不到，改用三个现成数据能算出来的代理信号：
_MANIP_SPOT_SHARE_MIN = 0.05   # 现货成交额占比 < 5% → 现货无深度（庄家可少量资金撬动）
_MANIP_CHURN_MAX = 10.0        # 合约24h成交额 / 持仓额 > 10x → 换手畸高（量能做出来的）
_MANIP_NEG_FUNDING = -0.0015   # 费率 ≤ −0.15% → 空头在给多头付钱（挤空收割特征）

# ---- v1.5.66 「买盘主导」阈值：修一条从上线起就没触发过的死规则 ----
# 原阈值 **1.85** 是拍出来的，实际取不到。2026-09-16 实测：拉 40 个最高成交额合约的
# `takerlongshortRatio` 最新值 → min 0.439 / 中位 1.110 / p90 1.426 / **max 1.790**，
# `>= 1.85` 命中 **0 个**。而这条信号同时挂在四个地方（`_stage_of` 的点火判据、
# `_radar_score` 的 +3、雷达依据文案、妖币引擎的「买盘主导」燃料项）——
# 也就是说它**自上线起一次都没生效过**。这不是「市场没出现」，是阈值压根够不着。
#
# 改 **1.30**（主动买 ≈ 主动卖的 1.3 倍，实测命中 10/40 = top 25%）：
# 既表示「确实有明显买盘在推」，又不会把日常波动误当信号。
# ⚠️ 这是一处**会产生行为变化**的修正（点火判定更易命中、评分 +3 可能开始加分），
# 不是纯显示修复 —— 之所以必须一起改，是因为同一个数在四个地方必须同义，
# 只改一处等于制造口径漂移。
_TAKER_BUY_DOMINANT = 1.30


# ---------------- 阈值命中率监控（v1.6.6 · 档一 §16） ----------------
# **为什么需要它**：上面那条 `_TAKER_BUY_DOMINANT = 1.85` 自上线起命中 **0 个**，却在
# 四处被使用 —— 这类「死规则」**不报错、不抛异常、也不会让任何测试变红**，只能靠
# **命中率**暴露。§16 已指出：支撑闸门线的样本本就很薄（20 笔、9 笔、甚至 0 笔），
# 一旦某条线再也够不着，系统会静默地少一个维度，而没有任何地方会说出来。
#
# 设计取舍：
#   · **逐行计数、进程级累计**（不落库）—— 零新增依赖、零额外请求；冷启动后 1~2 轮扫描即有意义。
#   · 分母是**走到语义层的候选行**（`d` 已合并确认层因子、已过过滤层）。为什么不把分母放到
#     更前面：过滤层那几条（新币 / 刷量 / 假量）在命中时就已经 `continue` 掉了，
#     若混进来会永远读到 0 命中而被误判「死规则」—— 那几条的可观测性已经由 payload 里的
#     `excluded_new` / `excluded_wash` / `excluded_no_bars` 承担，不必在此重复。
#   · 判「死」需要样本下限 `_THR_DEAD_MIN_ROWS`，避免冷启动（rows 很小）时误报。
#   · 计数发生在 `_radar2_lock` 内（`_radar_v2_scan` 全程持锁），无需再加锁。
_THR_HITS: dict = {"scans": 0, "rows": 0, "hits": {}, "since": 0.0}
_THR_DEAD_MIN_ROWS = 200      # 少于此样本量不下「死规则」结论
_THR_RULES: tuple = (
    # (规则名, 说明) —— 说明会随 payload 下发，前端/技能可直接展示
    ("trig_up_chg24", "触发：顺向涨幅 ≥ 3%"),
    ("trig_late_chg24", "硬挡：涨幅 ≥ 10%（判定「你来晚了」）"),
    ("trig_max_chg24", "追高：涨幅 > 12%（判 EXTENDED）"),
    ("trig_max_oi24", "硬挡：OI 24h ≥ 5%（持仓已堆积）"),
    ("trig_amp_ok", "振幅 ≥ 20%（活口线）"),
    ("taker_buy_dominant", "买盘主导：taker 买比 ≥ 1.30"),
    ("manip_no_spot", "控盘：现货成交占比 < 5%"),
    ("manip_churn", "控盘：换手畸高 > 10x"),
    ("manip_neg_funding", "控盘：空头付钱 ≤ −0.15%"),
)
_THR_NAMES = {k: v for k, v in _THR_RULES}


def _thr_rules(d: dict, cf: dict) -> dict:
    """一行命中了哪些阈值规则（纯函数，可单测）。键 = 规则名，值 = 是否命中。

    ⚠️ 这里**只做判定、不做过滤** —— 与生产判据用同一个常量对象，保证「监控的就是在用的」。
    凡「未测到」的字段一律判 False（fail-open），不得把缺失当成命中。
    """
    chg24 = float(d.get("chg24") or 0.0)
    oi24 = d.get("oi_chg24")
    amp24 = float(d.get("amp24") or 0.0)
    tr = (cf or {}).get("taker_ratio")
    fut_qv = float(d.get("fut_qv") or 0.0)
    spot_qv = float(d.get("spot_qv") or 0.0)
    oi_usd = float(d.get("oi_usd") or 0.0)
    fund = d.get("funding")
    return {
        "trig_up_chg24": chg24 >= _TRIG_UP_CHG24,
        "trig_late_chg24": chg24 >= _TRIG_LATE_CHG24,
        "trig_max_chg24": chg24 > _TRIG_MAX_CHG24,
        "trig_max_oi24": oi24 is not None and float(oi24) >= _TRIG_MAX_OI24,
        "trig_amp_ok": amp24 >= _TRIG_AMP_OK,
        "taker_buy_dominant": tr is not None and float(tr) >= _TAKER_BUY_DOMINANT,
        # 控盘三项：与 `_manip_flags` 同口径（分母为 0 时不成立 —— 这是「没测到」，不是「命中」）
        "manip_no_spot": bool(fut_qv > 0 and spot_qv / (fut_qv + spot_qv) < _MANIP_SPOT_SHARE_MIN),
        "manip_churn": bool(oi_usd > 0 and fut_qv / oi_usd > _MANIP_CHURN_MAX),
        "manip_neg_funding": fund is not None and float(fund) <= _MANIP_NEG_FUNDING,
    }


def _thr_tally(flags: dict) -> None:
    """累计一次扫描的行命中（在 `_radar2_lock` 内调用）。"""
    hits = _THR_HITS["hits"]
    for k, v in flags.items():
        if v:
            hits[k] = hits.get(k, 0) + 1


def _thr_new_scan() -> None:
    """一轮扫描开始时调用（在 `_radar2_lock` 内）。"""
    _THR_HITS["scans"] += 1
    if not _THR_HITS["since"]:
        _THR_HITS["since"] = time.time()


def threshold_hitrate() -> dict:
    """阈值命中率快照（供 payload / 技能 / 人工体检）。

    返回 `rules[name] = {hits, rows, rate, dead}` + `dead_rules` 清单。
    `dead=True` 的语义：**样本已够（≥ `_THR_DEAD_MIN_ROWS` 行）但一次都没命中** ——
    这正是 `_TAKER_BUY_DOMINANT = 1.85` 当年的形态，必须被看见。
    """
    n = int(_THR_HITS["rows"] or 0)
    rules = {}
    for name, desc in _THR_RULES:
        h = int(_THR_HITS["hits"].get(name, 0) or 0)
        rules[name] = {
            "desc": desc, "hits": h, "rows": n,
            "rate": (round(h / n, 4) if n else 0.0),
            "dead": bool(n >= _THR_DEAD_MIN_ROWS and h == 0),
        }
    return {
        "scans": int(_THR_HITS["scans"] or 0),
        "rows": n,
        "since": int(_THR_HITS["since"] or 0),
        "min_rows": _THR_DEAD_MIN_ROWS,
        "dead_rules": sorted([k for k, v in rules.items() if v["dead"]]),
        "rules": rules,
    }


def threshold_hitrate_reset() -> None:
    """清零（仅测试与人工体检用；不在生产路径调用）。"""
    _THR_HITS["scans"] = 0
    _THR_HITS["rows"] = 0
    _THR_HITS["hits"] = {}
    _THR_HITS["since"] = 0.0


# ---------------- 本地时序库落盘（v1.6.6 · 档二 C6） ----------------
# 表与读写实现在 `state.py`（`ts_series` / `ts_append` / `ts_range` / `ts_stats`）。
# 这里只负责「扫描时把行交出去」，并且**必须做成旁路**：
#   ① 落盘失败绝不能让雷达报错（行情是主路径，历史数据是附属产物）；
#   ② 用**惰性 import**，让 `scanner` 在「没有 state / 没有工作区」的上下文里
#      （单文件脚本、离线复算、部分测试）仍能正常被导入 —— 模块顶层 import 会连坐。
# 观测点：`_TS_LAST` 记录最近一次真正写入的行数，随 payload 的 `ts_store` 下发。
# 没有这个数，C6 很容易退化成「代码写了、库里是空的」而无人察觉（同 §16 的动机）。
_TS_LAST: dict = {"written": 0, "ts": 0.0}
_TS_STATS_CACHE: dict = {"ts": 0.0, "data": None}
_TS_STATS_TTL = 60.0        # `ts_stats()` 要 COUNT(*) 全表，payload 每次构建都算太浪费


def _ts_persist(rows: list) -> int:
    """把时序行交 `state` 落库；返回写入行数（失败返回 0，不抛）。"""
    if not rows:
        _TS_LAST["written"] = 0
        return 0
    try:
        import state
        n = int(state.ts_append(rows) or 0)
    except Exception:
        n = 0
    _TS_LAST["written"] = n
    _TS_LAST["ts"] = time.time()
    return n


def ts_store_view(force: bool = False) -> dict:
    """时序库体检快照（供 payload / 技能 / 人工体检）。结果缓存 `_TS_STATS_TTL` 秒。"""
    now = time.time()
    cached = _TS_STATS_CACHE.get("data")
    if cached is not None and not force and now - float(_TS_STATS_CACHE.get("ts") or 0) < _TS_STATS_TTL:
        st = dict(cached)
    else:
        try:
            import state
            st = dict(state.ts_stats())
        except Exception:
            st = {"rows": 0, "symbols": 0, "oldest": 0, "newest": 0, "keep_days": 0}
        _TS_STATS_CACHE["ts"] = now
        _TS_STATS_CACHE["data"] = dict(st)
    st["last_written"] = int(_TS_LAST.get("written") or 0)
    st["last_ts"] = int(_TS_LAST.get("ts") or 0)
    return st


def _trigger_hit(t: dict, row: dict, range_ratio: float = 0.0) -> tuple:
    """触发层（v1.6.2）：返回 (命中?, 依据列表)。
    机会型命中必须在**顺向**上成立；振幅 / 量能等方向无关项只写进依据文本，不再单独触发。"""
    rs, up, down = [], [], []
    L = t.get("jump_L", 0.0) or 0.0
    sp = t.get("speed5m", 0.0) or 0.0
    chg24 = t.get("chg24", 0.0) or 0.0
    accel = bool(t.get("accel"))
    amp24 = t.get("amp24", 0.0) or 0.0

    # —— 机会型（顺向向上）——
    if L > 3.0:
        up.append(f"跳跃检验 L={L:.1f}")
    if (t.get("flow", 0.0) or 0.0) > 1000 and chg24 > 0:
        up.append(f"泵度 flow={t['flow']:.0f}")
    if sp >= 0.5 and accel:
        up.append(f"速度 {sp:+.2f}%/5m 加速")
    if chg24 >= _TRIG_UP_CHG24:
        up.append(f"24h {chg24:+.1f}%")
    if t.get("pump5") or t.get("pump1h"):
        up.append("5m/1h 向上异动")
    if (t.get("streak", 0) or 0) >= 2 and chg24 > 0:
        up.append(f"连续 {t['streak'] + 1} 根同向放量")
    if (t.get("rvol15", 0.0) or 0.0) >= 2.0 and chg24 > 0:
        up.append(f"RVOL={t['rvol15']:.1f}x")

    # —— 风险型（顺向向下 → 只产崩跌/做空语义，不产做多建议）——
    if L < -3.0:
        down.append(f"跳跃检验 L={L:.1f}")
    if sp <= -0.5 and accel:
        down.append(f"速度 {sp:+.2f}%/5m 加速")
    if chg24 <= _TRIG_DOWN_CHG24:
        down.append(f"24h {chg24:+.1f}%")
    if t.get("dump5") or t.get("dump1h"):
        down.append("5m/1h 向下异动")

    # —— 佐证（方向无关，只作正文依据）——
    if amp24 >= 15.0:
        rs.append(f"24h 振幅 {amp24:.0f}%")
    if range_ratio >= 4.0:
        rs.append(f"振幅比 {range_ratio:.1f}x")
    if t.get("strongvol"):
        rs.append("60min 量能 3x")

    hit = bool(up or down or amp24 >= _TRIG_AMP_RISK)
    return (hit, up + down + rs)


# ---------------- 日线特征（过滤层 + 语义层底料） ----------------

def _daily2_fetch(sym: str):
    """日线 → (o,h,l,c,v,first_ms)；不足 25 根返回 None。
    v1.6.3：带合约回退 —— 纯合约妖币（无现货）改取 U 本位日线。"""
    arr = _klines_raw(sym, "1d", 110, fut_fallback=True)
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


def _spread_bps(bid, ask):
    """买卖价差（基点）。任一腿缺失/为 0/交叉 → **None（未测到）**，绝不写 0。

    v1.6.6（档一）：现货 ticker 带 bid/ask，合约 ticker 不带（实测），
    所以纯合约币这里恒为 None —— 那是诚实的「拿不到」，不是「流动性完美」。
    """
    try:
        b = float(bid or 0)
        a = float(ask or 0)
    except (TypeError, ValueError):
        return None
    if b <= 0 or a <= 0 or a < b:
        return None
    mid = (a + b) / 2.0
    if mid <= 0:
        return None
    return round((a - b) / mid * 1e4, 2)


def _radar_pool(top_n: int, min_qv: float) -> list:
    """v1.6.3 妖币候选池 = **现货 ∪ 合约**（按 `rank_qv` 降序，取前 top_n）。

    用户拍板把池子扩到合约全市场：此前池子只取自现货快照，而 **RAVE / LAB 都是只上合约、
    不上现货** —— 纯合约妖币根本进不了池子，排序规则再准也看不见。

    每行带三个派生字段：

    - `rank_qv`：排序/门槛用 `max(现货成交额, 合约成交额)`。用 max 而不是现货额，是为了救
      「现货极浅但合约火热」的币 —— 只看现货额它们会被 top_n 切掉，而那正是妖币的样子。
    - `spot_qv`：**现货成交额独立口径**（纯合约币为 0）。控盘代理必须读它，
      不能读 `quote_volume` —— 纯合约币的 `quote_volume` 是合约额，拿来当现货额会算出
      「现货占比 50%」，恰好把最该报警的币放过。
    - `no_spot`：该币无现货市场（RAVE / LAB 的真实画像）。
    """
    merged: dict = {}
    for r in get_snapshot():
        m = dict(r)
        qv = float(m.get("quote_volume") or 0.0)
        m["rank_qv"] = qv
        m["spot_qv"] = qv
        m["no_spot"] = False
        # v1.6.6（档一）：现货行能算价差；纯合约行在下面显式写 None。
        m["spread_bps"] = _spread_bps(m.get("bid"), m.get("ask"))
        merged[m["symbol"]] = m
    try:
        fut_rows = futures_snapshot()
    except Exception:
        fut_rows = []
    crypto_fut = None                     # 懒加载：只有在真的遇到「合约独有」的币时才拉白名单
    for r in fut_rows:
        sym = r["symbol"]
        fq = float(r.get("quote_volume") or 0.0)
        if sym in merged:
            merged[sym]["rank_qv"] = max(merged[sym]["rank_qv"], fq)
            continue                      # 现货已覆盖：保持现货口径（含成交笔数）
        if crypto_fut is None:
            try:
                crypto_fut = futures_crypto_syms()
            except Exception:
                crypto_fut = set()
        if sym not in crypto_fut:
            continue                      # 代币化股票/大宗（TradFi）不是妖币，不进池
        m = dict(r)
        m["rank_qv"] = fq
        m["spot_qv"] = 0.0
        m["no_spot"] = True
        # 纯合约行：合约 ticker 无 bid/ask → 价差拿不到，写 None（未测到）。
        m["spread_bps"] = _spread_bps(m.get("bid"), m.get("ask"))
        # v1.6.6（问题清单 C2）：此前无条件写 0，注释称「合约 ticker 无成交笔数」。
        # 该假设是错的 —— `/fapi/v1/ticker/24hr` 的响应带 `count`，已在
        # `futures_snapshot` 中取用。置 0 的后果是「单笔均额过低 = 假量」这条
        # 防线对**池子里 57/110 的纯合约币**整体失效（`if cnt > 0` 永不成立），
        # 而纯合约 + 无现货深度恰恰是最容易刷量的形态 —— 防线在最需要它的地方关着。
        m["count"] = int(r.get("count") or 0)
        merged[sym] = m
    rows = [r for r in merged.values()
            if (r.get("rank_qv") or 0) >= min_qv and _is_eligible(r["symbol"])]
    rows.sort(key=lambda x: x["rank_qv"], reverse=True)
    return rows[:top_n]


def _daily2_bars(top_n: int, min_qv: float, force: bool = False, workers: int = 10) -> dict:
    """并发拉扫描池日线（30 分钟缓存；妖币 v2 专用，与 v1 _bars_cache 分离）。

    v1.6.6（问题清单 #10）：缓存键纳入 `(top_n, min_qv)`。此前命中条件只看
    「非空 + 未过期」，**不比较这两个参数** —— 传 110 与传 120 会拿到同一份池子。
    实际影响面不大（`get_radar_v2` 默认 110、`agent_core` 传 120），
    但属于隐性耦合：一旦有人按不同 top_n 做对比实验，拿到的会是同一批数据而不自知。
    """
    now = time.time()
    key = (int(top_n), float(min_qv))
    if not force and _daily2_cache["bars"] and _daily2_cache.get("key") == key \
            and now - _daily2_cache["ts"] < 1800:
        return _daily2_cache["bars"]
    rows = _radar_pool(top_n, min_qv)
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
    _daily2_cache["key"] = key
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
    """/futures/data/* 免费端点。失败返回 []。
    5.1：确认层按 (sym, path, period, limit) 在途合并（_confirm_factors 扇出），
    并发扫描轮对相同键的请求只发一次 REST。"""
    def _fetch():
        try:
            r = _session.get(f"{FAPI}/futures/data/{path}",
                             params={"symbol": sym, "period": period, "limit": limit}, timeout=8)
            r.raise_for_status()
            return r.json() or []
        except Exception:
            return []
    return _dedupe_fetch(("futhist", sym, path, period, limit), _fetch)


def _funding_hist(sym: str, limit: int = 21) -> list:
    """已结算资金费率历史（8h 一期，21 期 ≈ 7 天）。失败返回 []。5.1：同键在途合并。"""
    def _fetch():
        try:
            r = _session.get(f"{FAPI}/fapi/v1/fundingRate",
                             params={"symbol": sym, "limit": limit}, timeout=8)
            r.raise_for_status()
            return [float(d.get("lastFundingRate") or 0) for d in (r.json() or [])]
        except Exception:
            return []
    return _dedupe_fetch(("fundhist", sym, limit), _fetch)


def _liq_source_available() -> bool:
    """强平数据源当前是否真的在供数（v1.6.6，问题清单 D1）。

    包一层是为了「雷达没有强平数据」这件事在 payload 层面可见，
    同时让 market_ws 未启动（测试、无 WS 依赖）时安全返回 False 而不抛异常。
    """
    try:
        import market_ws
        return bool(market_ws.liq_available())
    except Exception:
        return False


def _confirm_factors(sym: str, funding_now: float) -> dict:
    """确认层因子：OI 四象限/脉冲、funding 极值、大户/散户多空比、taker 买卖比、爆仓流。
    任一接口失败安全降级（0/None），绝不因确认层挂掉丢触发信号。

    v1.6.6（问题清单 D1）：`liq_5m` / `liq_n5m` 的默认值由 `0.0` / `0` 改为 **`None`**。
    原因见下方强平流分支注释 —— 该流在部署地区不推数据，而恒写 0 会与
    「真的没有爆仓」完全无法区分，属于静默失效。
    """
    f = {"oi_chg24": 0.0, "oi_chg48": 0.0, "oi_pulse15": 0.0, "oi_last": 0.0,
         "funding": funding_now,
         "funding_peak": funding_now, "funding_prev": None, "top_ratio": None,
         "top_mean48": None, "global_ratio": None, "taker_ratio": None,
         "taker_drop": False, "liq_5m": None, "liq_side": "", "liq_n5m": None,
         "liq_available": False}
    oi = _fut_hist(sym, "openInterestHist", period="15m", limit=197)
    if len(oi) >= 13:
        try:
            vals = [float(x.get("sumOpenInterest") or 0) for x in oi]
            last = vals[-1]
            if last > 0:
                f["oi_last"] = last          # v1.6.3：持仓量原值（币本位），×price 得 OI 美元额
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
    # 强平流（v1.6.6，问题清单 D1）：WS `!forceOrder@arr` 在部署地区**连得上但不推流**
    # —— 2026-09 实测观察 109 秒 **0 帧**（同域 `btcusdt@depth@100ms` 10 秒 5 帧，对照组正常），
    # 且**没有公开 REST 兜底**（`/fapi/v1/allForceOrders` 已 404，`/fapi/v1/forceOrders` 需
    # API key 且只返回本账户强平单）。即这个数据源在当前环境下**整体不可用**。
    #
    # 此前无论可用与否都写 `liq_5m = 0.0`，于是「数据源没推流」与「真的没有爆仓」
    # 在数值上完全一样。它挂在三处：reasons 的「爆仓 $X M/5m」、`_radar_score` 的加分、
    # `_reversal_now` 的反转因子 —— 三处**静默空转**，与 `_TAKER_BUY_DOMINANT = 1.85`
    # 那条「自上线起命中 0 个」的死规则同类，但更难发现（因为 0 看起来是个合法读数）。
    #
    # 现在按「未测到 → None」写入。全部展示侧消费方本来就写的是 `if f.get("liq_5m"):`
    # 或 `(x or 0) >= 3e5`，拿到 None 会自然跳过，不会再谎报「爆仓 $0.0M」。
    try:
        import market_ws
        ls = market_ws.liq_symbol_stats(sym, window=300)
        if ls.get("available"):
            f["liq_available"] = True
            f["liq_5m"] = float(ls.get("quote") or 0.0)
            f["liq_side"] = str(ls.get("side") or "")
            f["liq_n5m"] = int(ls.get("count") or 0)
    except Exception:
        pass
    return f


def confirm_factors(sym: str, funding_now: float = None) -> dict:
    """公开入口：给**单个币**补一次确认层因子（OI 四象限 / 大户比 / taker / 爆仓流）。

    为什么需要这个入口 —— 雷达 v2 的确认层有 **24 币封顶**
    （见 `get_radar_v2` 里 `cands = ...[:24]`，那是为了控制 REST 调用量）。
    但雷达最终会输出 40+ 行，于是**过半的行根本没有 oi_chg24 / top_ratio / taker_ratio**，
    而 `get_radar_v2` 又用 `cf.get(k, 0.0)` 兜底把它们写成了字面 **0.0** ——
    消费方无法区分「真的没变化」和「压根没测」。

    妖币引擎分析的是**单只币**，按需补这一次的代价只有 3~4 个 REST 调用，
    却能让控盘度/燃料两轴从「大面积读成中性」变成有真实依据。
    """
    sym = (sym or "").strip().upper()
    if not sym:
        return {}
    if funding_now is None:
        try:
            funding_now = float((get_funding_rates() or {}).get(sym) or 0.0)
        except Exception:
            funding_now = 0.0
    return _confirm_factors(sym, funding_now)


# ---------------- v1.6.3 妖币控盘代理层 ----------------

def _manip_flags(d: dict) -> tuple:
    """妖币控盘代理（v1.6.3，全链下、零新增接口）。返回 (flags, note, penalty)。

    为什么是代理而不是控盘率：公开接口拿不到持币集中度（见 `_MANIP_*` 常量注释）。
    但妖币的收割机制在**盘面**上留了三个可算的指纹：

    ① 「现货无深度」（`无现货` / `合约独大`）：现货成交额占现货+合约总额 < 5%；
       **连现货市场都没有的（`no_spot`，RAVE / LAB 就是）单列 `无现货`**。
       妖币的原话是「现货缺乏流动性意味着无抛压、无深度监管」——现货簿薄，
       少量资金就能把价格推起来，再从合约端兑现。
    ② 「换手畸高」：合约 24h 成交额 ÷ 持仓额 > 10x。
       RAVE 合约 24h 成交 69 亿 vs 持仓 3 亿（≈23x），且拉 10 倍一个大额爆仓单都没有
       —— 量能是两边对冲做出来的，不是真金白银的方向性押注。
    ③ 「空头付钱」：费率 ≤ −0.15%。**负费率的含义是空头给多头付钱**，
       这正是庄家挤空收割空头的标志（RAVE 年化 −1000%~−4000%）。
       此刻的拉升是挤空，不是趋势 —— 绝不可在此做空，长仓也应视为随时反手。

    任一命中即扣分（v1.6.4：「空头付钱」权重由 10 降到 3，理由见该分支注释）。
    **注意：只扣分、不改 side、不构成登记门槛** —— `_stage_of` 里也明确写了「只做标注，
    避免重蹈『过滤过紧误杀赢家』」。风控语义靠 `notes`（风险提示）表达，不靠降级 side。
    各项权重：无现货 / 合约独大 / 换手畸高 各 6，空头付钱 3，拉升无爆仓 4。"""
    flags, notes = [], []
    penalty = 0.0
    price = d.get("price", 0.0) or 0.0
    fut_qv = d.get("fut_qv", 0.0) or 0.0
    # v1.6.3：现货成交额必须读**独立口径** `spot_qv`（纯合约币为 0），
    # 不能读 `quote_volume` —— 池子扩到合约全市场后，纯合约币的 `quote_volume` 是合约口径，
    # 拿它当现货成交额会算出「现货占比 50%」，恰好把最该报警的币放过。
    spot_qv = d.get("spot_qv", 0.0) or 0.0
    oi_usd = d.get("oi_usd", 0.0) or 0.0
    fu = d.get("funding", 0.0) or 0.0
    chg24 = d.get("chg24", 0.0) or 0.0
    liq5 = d.get("liq_5m", 0.0) or 0.0
    liq_n = d.get("liq_n5m", 0) or 0

    # ① 现货无深度：最极端的一档就是**压根没有现货市场**（RAVE / LAB 的真实画像）
    if d.get("no_spot"):
        flags.append("无现货")
        notes.append(f"只上合约、无现货市场（合约 {fut_qv / 1e6:.0f}M，无现货抛压与价格锚）")
        penalty += 6.0
    else:
        tot = spot_qv + fut_qv
        if fut_qv >= 5e6 and tot > 0:
            share = spot_qv / tot
            if share < _MANIP_SPOT_SHARE_MIN:
                flags.append("合约独大")
                notes.append(f"现货成交仅占 {share * 100:.1f}%（合约 {fut_qv / 1e6:.0f}M）")
                penalty += 6.0

    # ② 换手畸高
    if oi_usd > 0 and fut_qv > 0:
        churn = fut_qv / oi_usd
        if churn > _MANIP_CHURN_MAX:
            flags.append("换手畸高")
            notes.append(f"合约成交/持仓 {churn:.0f}x（OI {oi_usd / 1e6:.1f}M）")
            penalty += 6.0

    # ③ 空头付钱（挤空收割特征）
    # v1.6.4 降权 10 → 3：这条规则**自带反例** —— VTHOUSDT 登记时费率 −0.776%，
    # 结局却是 +31.3% moon。全样本负费率只有 1 moon / 2 dump，对**做多结局**判别力很弱，
    # 而它原本是最大单项扣分（10 分），足以把真妖币压出登记组。
    # 语义保留（对做空确实是硬约束、且是重要风险提示），只降低它对做多评分的权重。
    if fu <= _MANIP_NEG_FUNDING:
        flags.append("空头付钱")
        notes.append(f"费率 {fu * 100:+.3f}% = 空头在给多头付钱（挤空特征，严禁做空）")
        penalty += 3.0

    # ④ 拉升无爆仓（数据可疑 / 假突破诱多）
    if chg24 >= 8.0 and liq_n > 0 and liq5 < 5e4:
        flags.append("拉升无爆仓")
        notes.append(f"当日 {chg24:+.0f}% 但 5m 爆仓仅 ${liq5 / 1e3:.0f}K（拉升未见对手盘出清）")
        penalty += 4.0

    return (flags, "；".join(notes), penalty)


def manip_flags(d: dict) -> tuple:
    """公开别名：`_manip_flags`。

    供妖币引擎在**按需补完确认层因子之后重算控盘指纹**。为什么需要重算 ——
    雷达确认层有 24 币封顶，未被覆盖的行 `oi_usd` 为 0，于是
    「换手畸高」（合约成交额 ÷ 持仓额 > 10x）与「拉升无爆仓」**永远算不出来**，
    控盘轴就只能落到「未见控盘指纹」这一档，看起来像「每只币都一样」。
    """
    return _manip_flags(d)


# ---------------- 语义层（生命周期阶段） ----------------

def _stage_of_raw(d: dict) -> tuple:
    """语义层：吸筹→点火→**已拉升**→垂直拉升→派发顶部→崩跌→沉寂（先到先得）。
    v1.6.2 新增 EXTENDED（已拉升）：当日涨幅越过 `_TRIG_MAX_CHG24` 但未到垂直拉升 ——
    旧实现把 3%~25% 全归「点火（启动前）」，实测 dump 组登记时 24h 涨幅中位 18.1% 全落在这段。
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
    # 3.5) 做空埋伏：高位滞涨 + 拥挤过热（费率/大户） + 买盘衰竭 → 崩跌前预警（吸筹的镜像）
    # v1.6.2：**必须已有破位迹象**才成立。旧实现只要求「滞涨」，实测 3 单全被轧空
    # （CAKE +10.0% / THETA +10.3% / SAGA +11.0%）—— 高位滞涨在妖币上常是二次拉升前的换手，
    # 等破位确认再空，而不是在强势币上左侧逆势。
    sam = (pos >= 0.75 and (chg30 >= 30.0 or chg3 >= 15.0) and chg24 <= 5.0 and chg1h <= 1.0)
    sam_sig = (fund_pk >= 0.003 and tr <= 1.10) or tdrop \
        or (top >= 2.0 and tr <= 1.20) or (oi24 <= -3.0 and chg24 >= 0.0)
    sam_break = chg1h <= -2.0 or chg3 <= -5.0
    # v1.6.3：「空头付钱」时禁止做空。负费率 = 空头给多头送钱，而做空正是妖币剧本里被挤的位置
    # （RAVE 空头爆仓占全部爆仓 82%、多头仅 18%）。宁可不做，也不去当那个对手盘。
    sam_ok = fund > _MANIP_NEG_FUNDING
    if sam and sam_sig and sam_break and sam_ok:
        return ("SHORT_AMBUSH", "做空埋伏", "做空 · 破位确认", "WATCH_SHORT",
                "高位滞涨 + 费率/大户拥挤过热 + **已出现破位**（1h 跌幅 / 3 日转跌）= 拉升衰竭确认；"
                "做空仅小仓试错、创新高即走")
    # 3.8) 已拉升（v1.6.2 新增）：越过追高线但未到垂直拉升 —— 启动窗口已过
    # 旧实现把 3%~25% 全归「点火（启动前）」，实测 dump 组登记时 24h 涨幅中位 18.1%
    # 全部落在这段里。这段不是「启动前」，是「已经拉升」，进场等于接力末端。
    if chg24 > _TRIG_MAX_CHG24:
        return ("EXTENDED", "已拉升", "已拉升 · 追高区", "WATCH",
                f"当日已涨 {chg24:.0f}%，越过 {_TRIG_MAX_CHG24:.0f}% 追高线：启动窗口已过，"
                "此处进场即接力末端；等回踩平台不破再谈，不追")
    # 4) 点火：破位放量 / OI 脉冲 / taker 买比飙升
    if (d.get("breakout20") and (d.get("rvol_d", 0) or 0) >= 2.0) \
            or (oi15 >= 5.0 and chg24 >= 3.0) \
            or (tr >= _TAKER_BUY_DOMINANT and (d.get("rvol15", 0) or 0) >= 2.0 and chg24 >= 2.0):
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
    #
    # v1.6.6（问题清单 #4）：此前写作 `(d.get("rvol_d", 1.0) or 1.0) <= 0.6`，有**双重吞值**：
    #   ① `_daily_feats._vr()` 在基准均量为 0 时返回 `0.0`，而 `0.0 or 1.0` 在 Python 里
    #      求值为 `1.0` → `1.0 <= 0.6` 恒为 False。也就是**「真·零成交量」这个最该判沉寂的
    #      极端情形永远进不了沉寂档**，方向与直觉完全相反。
    #   ② `d.get("rvol_d", 1.0)` 的默认值 1.0 与后面的 `or 1.0` 是重复防御，两次都指向
    #      「不触发」，让这个 bug 更难被发现（看起来像刻意保守）。
    # 现改为显式判空：**缺失**（键不存在 / None，即无日线）不参与判定 —— fail-open，
    # 与全系统「拿不到就不判」的约定一致；但**测得为 0** 必须真正参与判定，
    # 因为那正是「量能枯竭」这个信号本身。
    rv_d = d.get("rvol_d")
    if rv_d is not None and rv_d <= 0.6 and (d.get("amp24", 0.0) or 0.0) <= 5.0 \
            and abs(chg24) <= 3.0:
        return ("DORMANT", "沉寂", "沉寂 · 泵后回吐" if chg30 >= 30.0 else "沉寂 · 无波动",
                "WATCH", "量能枯竭、波动收敛；仅有大起大落史者保持观察")
    return (None, "", "", "", "")


def _stage_of(d: dict) -> tuple:
    """语义层 + 控盘代理注释（v1.6.3 包装 `_stage_of_raw`，调用方签名不变）。

    定位（用户确认）＝「顺剧本骑①②」：承认妖币是庄家剧本，只做控盘后的首次拉升，
    **绝不做空**；顶部/派发特征出现时对持仓者是离场信号。

    **为什么控盘标记只注释、不硬降级（v1.6.3 实测修正）**：
    初版把「空头付钱」（费率 ≤ −0.15%）当作硬性离场条件，结果拿安装版 23 笔已关单回放，
    反例立刻出现 —— VTHOUSDT 登记时费率 **−0.776%**，却是 +31.3% 的 moon。
    原因：币安永续上**空头拥挤本身就会把费率压成负值**，低位负费率反而是轧空燃料。
    所以负费率是「风险标记」而非「离场信号」，硬拦会误杀赢家。
    真正的离场信号在 `DISTRIBUTION` 与 `radar_tracker._reversal_now`（费率自极值回落 /
    OI 顶背离 / 爆仓潮后 OI 骤降），那套是拿 float 极值相对变化判断的，不受此影响。

    硬性规则只保留一条：**SHORT_AMBUSH 不再进入可下注/登记组**（见 `get_radar_v2` 分组）。
    """
    stage, slabel, tag, side, note = _stage_of_raw(d)
    if not stage:
        return (stage, slabel, tag, side, note)
    flags, mnote, _pen = _manip_flags(d)
    if not flags:
        return (stage, slabel, tag, side, note)
    # 只做标注（分数由 `_radar_score` 扣），不改 side —— 避免重蹈「过滤过紧误杀赢家」
    return (stage, slabel, tag, side, f"{note}；控盘代理（{'/'.join(flags)}）：{mnote}")


def _radar_score(t: dict, f: dict, d: dict, cooldown: bool) -> int:
    """合成 0-99 妖币度（v1.6.2 方向化）。
    旧实现用 abs() 打所有动量项 —— 崩跌与拉升得分完全相同，且振幅越大分越高。
    实测 dump 组 24h 涨幅中位 18.1%、日振幅中位 27.5%，正是被这套打分挑出来的。
    新实现：动量项只认顺向；涨幅越过追高线、振幅越过活口线，一律改为**扣分**。"""
    s = 0.0
    chg24 = d.get("chg24", 0.0) or 0.0
    s += min(28.0, (t.get("flow", 0.0) or 0.0) / 1000.0 * 28.0)
    s += min(12.0, max(0.0, t.get("jump_L", 0.0) or 0.0) / 6.0 * 12.0)   # 只认向上跳跃
    s += min(8.0, max(0.0, t.get("speed5m", 0.0) or 0.0))                # 只认向上加速
    if chg24 > 0:
        s += min(10.0, chg24 / _TRIG_MAX_CHG24 * 10.0)                   # 启动窗口内线性加分
        if chg24 > _TRIG_MAX_CHG24:
            s -= min(18.0, (chg24 - _TRIG_MAX_CHG24) / 13.0 * 18.0)      # 12%→25% 扣满 18
    else:
        s -= min(10.0, abs(chg24) / 25.0 * 10.0)                         # 当日下跌倒扣
    vr = max((t.get("rvol15", 0.0) or 0.0) - 1.0, (d.get("rvol_d", 0.0) or 0.0) - 1.0)
    s += min(12.0, max(0.0, vr) / 4.0 * 12.0)
    amp24 = d.get("amp24", 0.0) or 0.0
    if amp24 <= _TRIG_AMP_OK:
        s += min(5.0, amp24 / _TRIG_AMP_OK * 5.0)                        # 20% 内算「有活口」
    else:
        s -= min(10.0, (amp24 - _TRIG_AMP_OK) / 20.0 * 10.0)             # 20%→40% 扣满 10
    # 确认层加减
    oi24 = f.get("oi_chg24", 0.0) or 0.0
    if chg24 >= 3.0 and oi24 >= 5.0:
        s += 6.0                                   # 价↑OI↑ 新钱趋势
    elif chg24 >= 3.0 and oi24 <= -5.0:
        s += 3.0                                   # 价↑OI↓ 轧空虚涨（顶前兆）
    if (f.get("oi_pulse15", 0.0) or 0.0) >= 5.0:
        s += 4.0
    # v1.6.3 费率**方向化**：旧实现 `abs(funding)` 把「负费率」也当拥挤度加分 ——
    # 但负费率的含义是**空头在给多头付钱**，这是妖币挤空收割空头的标志（RAVE 年化 −1000%~−4000%）。
    # 负费率下的拉升是挤空而非趋势，不该加分。只有**正**费率（多头拥挤）才算热度；
    # 负费率的扣分统一由 `_manip_flags` 的「空头付钱」负责，避免同一件事扣两次。
    fu = f.get("funding", 0.0) or 0.0
    if fu >= 0.003:
        s += 4.0
    elif fu >= 0.0015:
        s += 2.0
    tr_ = f.get("top_ratio") or 0.0
    if tr_ >= 1.5 or 0 < tr_ <= 0.7:
        s += 3.0
    if (f.get("taker_ratio") or 0.0) >= _TAKER_BUY_DOMINANT:
        s += 3.0
    lq = f.get("liq_5m", 0.0) or 0.0
    if lq >= 1e6:
        s += 5.0
    elif lq >= 3e5:
        s += 2.0
    # v1.6.3 控盘代理扣分（见 `_manip_flags`）：命中即说明这是庄家剧本盘，不是趋势盘
    _mflags, _mnote, mpen = _manip_flags(d)
    s -= mpen
    if d.get("beta_damped"):
        s *= 0.6                                   # BTC β 残差：普涨普跌打折
    if cooldown:
        s = min(s, 59.0)                           # 冷却期压分，让新面孔排前面
    return int(round(max(1.0, min(99.0, s))))


# ---------------- v2 主流程 ----------------

def _with_age(data: dict, now: float) -> dict:
    """补写 `age_sec` / `stale` 后返回（浅拷贝，不动缓存里的原对象）。

    v1.7.1（C3 延迟治理）：`updated_at` 只说「这轮什么时候算的」，不说「你现在看到的是多久前的」。
    前端过去只能自己拿本地时钟去减，于是：① 各页面各算各的；② 拿不到后端认为的 TTL，
    无法判断「这个数字是不是已经过期」；③ 本地时钟与服务端有偏差时直接算错，且错的方向不固定。
    现在后端把两个判断一次下发：
      · `age_sec` = 读的时刻 − 本轮扫描时刻（0 = 刚算完；走缓存则 > 0）
      · `stale`   = `age_sec` 是否已超过**本轮生效的** TTL（payload 里的 ttl，可被 env 覆盖）
    浅拷贝的理由：`coins` 是上百行的列表，深拷贝会白花 CPU，而这里只改两个顶层键。
    """
    out = dict(data or {})
    try:
        ts = float(out.get("updated_at") or 0.0)
    except (TypeError, ValueError):
        ts = 0.0
    if not math.isfinite(ts):
        ts = 0.0
    age = max(0.0, float(now) - ts) if ts > 0 else 0.0
    out["age_sec"] = int(age)
    try:
        ttl = float(out.get("ttl") or 0.0)
    except (TypeError, ValueError):
        ttl = 0.0
    if not (ttl > 0) or not math.isfinite(ttl):
        ttl = radar2_ttl()
    out["stale"] = bool(age > ttl)
    return out


def get_radar_v2(force: bool = False, top_n: int = 110, min_qv: float = RADAR2_FLOOR) -> dict:
    """妖币雷达 v2 全量扫描（5 分钟缓存）。coins 含 stage/score/factors，按妖币度降序。

    v1.6.6（问题清单 #9）：**扫描级互斥**。此前 `_radar2_cache` 是无锁 dict，
    `get_radar_v2` 也没有扫描级互斥，而触发源至少四个（前端「强制重扫」按钮、
    定时任务、对话 `meme_watch`、妖币引擎 `_row_of`）。`_dedupe_fetch` 只合并
    「**同缓存键且同时在途**」的请求，挡不住两个并发 `force=True` 各自跑完整流水线
    （日线池 + 15m/5m 扇出 + 确认层，冷启动扇出约 450 次 REST）→ 请求量翻倍、
    有被限频的实际风险，且缓存写入「最后写赢」会让 `updated_at` 与内容短暂错配。

    现在：拿不到锁的调用**等锁**（最多 `RADAR2_LOCK_WAIT` 秒），拿到后先做双重检查 ——
    若等锁期间已有别的线程写好缓存，直接复用而不重复扇出。

    v1.7.1（C3 延迟治理）：**所有返回路径都经 `_with_age()` 补写新鲜度**
    （`age_sec` / `stale`），上层不必再自己拿本地时钟去减 —— 见 `_with_age` 的说明。
    """
    now = time.time()
    if not force and _radar2_cache["data"] and now - _radar2_cache["ts"] < radar2_ttl():
        return _with_age(_radar2_cache["data"], now)
    if not _radar2_lock.acquire(timeout=RADAR2_LOCK_WAIT):
        # 等不到锁：另一路正在扫。有缓存就给缓存，没有则抛出让上层走 v1 兜底
        # （不硬等 —— 扫描可能要几十秒，调用方不该被无限阻塞）。
        if _radar2_cache["data"]:
            return _with_age(_radar2_cache["data"], now)
        raise RuntimeError("radar v2 scan busy")
    try:
        # 双重检查：等锁期间可能已有别的线程跑完并写好缓存
        now2 = time.time()
        if _radar2_cache["data"] and now2 - _radar2_cache["ts"] < radar2_ttl():
            return _with_age(_radar2_cache["data"], now2)
        return _with_age(_radar_v2_scan(force=force, top_n=top_n, min_qv=min_qv), now2)
    finally:
        _radar2_lock.release()


def _radar_v2_scan(force: bool = False, top_n: int = 110, min_qv: float = RADAR2_FLOOR) -> dict:
    """v2 扫描主体（**只在持有 `_radar2_lock` 时调用**）。"""
    now = time.time()
    if not force and _radar2_cache["data"] and now - _radar2_cache["ts"] < radar2_ttl():
        return _radar2_cache["data"]
    cycle_ts = now
    # v1.6.6（档一 §16）：阈值命中率监控 —— 一轮扫描计一次
    _thr_new_scan()
    # v1.6.3：候选池扩到 **现货 ∪ 合约全市场**（纯合约妖币 RAVE / LAB 此前不可见，见 `_radar_pool`）。
    floor = max(min_qv or 0, RADAR2_FLOOR)
    pool_rows = _radar_pool(top_n, floor)
    snap = {r["symbol"]: r for r in pool_rows}
    pool_syms = [r["symbol"] for r in pool_rows]
    # 合约成交额（算「现货无深度」与「换手畸高」两个控盘代理，一次全市场请求，有 20s 缓存）
    try:
        futq = {r["symbol"]: float(r.get("quote_volume") or 0.0) for r in futures_snapshot()}
    except Exception:
        futq = {}
    if not pool_syms:
        raise RuntimeError("empty pool (snapshot unavailable)")
    d2 = _daily2_bars(top_n, floor, force=force)
    rates = get_funding_rates()
    # v1.6.6（档一）：premiumIndex 的 markPrice/indexPrice 与费率共用同一份缓存，
    # 这里取来算**基差**（标记价对指数价的偏离）—— 零额外请求。
    fund_meta = get_funding_meta()
    # v1.7.1（#10 成本模型修正）：逐币**资金费率结算周期**（4h/8h/1h）。与上面同源动机 ——
    # 确认层要用它算含成本的 PnL，而周期不是全市场统一的 8h（实测 4h 才是多数）。
    # `_fi_ok=False` 表示接口没通：此时**不下发**任何周期（写 None = 未测到），
    # 由消费方回退默认 8h 并自知那是假设，而不是把假设伪装成实测。
    fund_iv, _fi_ok = get_funding_intervals()
    # v1.7.1（#10 成本模型闭环）：全市场**合约**盘口价差（1 次请求拿 766 个币，零扇出）。
    # 现货口径的 `spread_bps`（下面行里的 `r.get("spread_bps")`）保持原样不动 ——
    # 那是**另一个口径**，且纯合约币为 None；合约价差单列 `fut_spread_bps`，不覆盖。
    book, _bk_ok = get_book_spreads()

    # —— 短时窗 K 线并发（15m 结构 + 5m 速度） ——
    # v1.6.3：带合约回退 —— 池里已含纯合约妖币，必须走 fut_fallback 才拿得到 K 线。
    k15, k5 = {}, {}
    with _cf.ThreadPoolExecutor(max_workers=10) as ex:
        f15 = {ex.submit(_klines_raw, s, "15m", 288, True): s for s in pool_syms}
        f5 = {ex.submit(_klines_raw, s, "5m", 96, True): s for s in pool_syms}
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
    n_new = n_wash = n_no_bars = 0
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
             # v1.6.3：现货成交额读 `_radar_pool` 标好的**独立口径** spot_qv（纯合约币为 0），
             # 不能拿 r["quote_volume"] 冒充 —— 纯合约币那里是合约口径。
             "spot_qv": float(r.get("spot_qv") or 0.0),
             "no_spot": bool(r.get("no_spot")),
             # v1.6.6（档一）：价差随行带入（现货行有值、纯合约行为 None）
             "spread_bps": r.get("spread_bps"),
             # v1.7.1（#10）：合约盘口价差（基点）。与上面的现货口径**并列**，不覆盖 ——
             # 两个口径的差别正是「纯合约币的滑点从哪来」这个问题的答案。
             # 未测到 → None（不是 0）：0 会被读成「零价差」，那是相反的结论。
             "fut_spread_bps": (book.get(sym) if _bk_ok else None),
             "funding": rates.get(sym, 0.0)}
        # 过滤层：新币 / 刷量 / 假量（单笔均额过低）
        #
        # v1.6.6（问题清单 C1）：此前整段过滤被 `if b:` 包住 —— 一旦拿不到日线就
        # **把三道防线整体跳过**。而 `_daily2_fetch` 在 `len(arr) < 25` 时返回 None，
        # 即「上市不足 25 天」的币恰好全部绕过过滤；「刚上市 + 只上合约 + 无现货深度」
        # 正是外部文献描述的**经典妖币形态** —— 防线在最该生效的地方关着。
        # 更糟的是它是**静默**的：`n_new` / `n_wash` 都在 `if b:` 内部自增，
        # 审计计数里也看不见这批币，所以「被丢掉的到底是谁、为什么」无从回答。
        #
        # 现拆成两条显式路径：有日线走原逻辑；无日线改用**权威的合约 onboardDate**
        # 判币龄（`futures_listed_days`），并单独计入 `n_no_bars` 让它可见。
        # 注意 fail-open 原则不变：币龄**未知**（接口失败/字段缺失）时放行，不误杀。
        b = d2.get(sym)
        df: dict = {}
        rr = 0.0
        if b:
            df = _daily_feats(b, now)
            d.update(df)
            if df.get("amp7d"):
                rr = d["amp24"] / df["amp7d"] if df["amp7d"] > 0 else 0.0
            listed = df.get("listed_days")
        else:
            n_no_bars += 1
            listed = futures_listed_days(sym)
        # 新币线（两条路径共用）：None = 未知 → 放行，绝不把未知当成「刚上市」
        if listed is not None and listed < NEW_COIN_DAYS:
            n_new += 1
            continue
        # 刷量线：连续 3 个已收日量比 > 50（需要日线，无日线时无法判定 → 放行）
        if df.get("wash"):
            n_wash += 1
            continue
        # 假量线：单笔均额过低。`count` 为 0 表示**没测到**（合约 ticker 此前被强制置 0），
        # 不是「没有成交」—— 见 C2 修复后此处对纯合约币同样生效。
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
        # v1.6.2：旧实现 `key=abs(chg24)` 取前 24 —— 把「当日涨跌幅度最大」的币优先送进确认层，
        # 等于把追高写死在排序里。改为按「启动窗口优先级」：顺向未过热 > 低位吸筹 > 已拉升 > 其余。
        def _cand_rank(sym_: str) -> int:
            c = feats[sym_]["d"].get("chg24", 0.0) or 0.0
            if 0 < c <= _TRIG_MAX_CHG24:
                return 0
            if _accum_evid(feats[sym_]):
                return 1
            if c > _TRIG_MAX_CHG24:
                return 2
            return 3
        cands = set(sorted(cands, key=lambda s: (
            _cand_rank(s), -float(feats[s]["d"].get("quote_volume", 0.0) or 0.0)))[:24])
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
    _ts_rows: list = []          # v1.6.6（档二 C6）：待落本地时序库的行，循环后一次事务写入
    stage_counts: dict = {}
    for sym, f in feats.items():
        d = f["d"]
        cf = confirms.get(sym) or {}
        d.update({k: cf[k] for k in ("oi_chg24", "oi_chg48", "oi_pulse15", "top_ratio",
                                     "top_mean48", "global_ratio", "taker_ratio", "taker_drop",
                                     "liq_5m", "liq_side", "liq_n5m", "oi_last",
                                     "funding_peak", "funding_prev")
                  if k in cf})
        # v1.6.3 控盘代理底料：合约成交额 + 持仓美元额（`_manip_flags` 直接读 d）
        d["fut_qv"] = futq.get(sym, 0.0)
        d["oi_usd"] = (d.get("oi_last") or 0.0) * (d.get("price") or 0.0)
        # v1.6.6（档一 §16）：阈值命中率监控 —— 此处 `d` 已合并确认层因子且已过过滤层，
        # 是「闸门线到底还够不够得着」最干净的分母。逐行累计，供 `threshold_hitrate()` 体检。
        _thr_tally(_thr_rules(d, cf))
        _THR_HITS["rows"] = int(_THR_HITS["rows"] or 0) + 1
        # v1.6.6（档二 C6）：把这一行的因子读数落进**本地时序库**。
        # 落点与 §16 的 tally 完全相同（`d` 已合并确认层因子、已过过滤层），是因子最全的一刻。
        # 刻意**不等到「出行」之后再存** —— C6 要回答的是「某币被**登记之前**长什么样」，
        # 只存登记过的币等于没解决那个问题。未测到一律 None（不写 0），与 payload 同口径。
        _fm = fund_meta.get(sym) or {}
        _ts_rows.append({
            "symbol": sym, "ts": cycle_ts,
            "price": d.get("price"), "chg24": d.get("chg24"),
            "oi_usd": d.get("oi_usd"), "oi_chg24": d.get("oi_chg24"),
            "funding": d.get("funding"),
            "mark_price": _fm.get("mark_price"), "index_price": _fm.get("index_price"),
            "basis_bps": _fm.get("basis_bps"), "spread_bps": d.get("spread_bps"),
            "taker_ratio": cf.get("taker_ratio"),
            "liq_5m": d.get("liq_5m"), "liq_side": cf.get("liq_side", ""),
            "fut_qv": d.get("fut_qv"),
            # v1.7.1（#10 成本模型闭环）：合约盘口价差落库 —— 攒的是「滑点常数该是多少」
            # 的标定样本，尤其是纯合约币（它们在上面那列 `spread_bps` 里永远是空的）。
            "fut_spread_bps": d.get("fut_spread_bps"),
        })
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
        # v1.6.5（OPT-04）暖启动降分：登记的那一刻已经涨了 3%~10% 的币，
        # **不硬挡**（样本不足以证明该挡，见 _TRIG_LATE_CHG24 注释），但降 6 分让它排到冷启动之后。
        # ⚠️ 登记链路本身是**不看 score 的**（radar_tracker.record_from_radar 遍历整个 ignition 数组），
        # 所以这个降分的作用是「排序 + 把暖启动这件事写进依据/快照，便于日后按数值分桶回放」，
        # 真正的闸门仍是 `_late`（价格硬挡线 + OI 堆积）。别把它当过滤用。
        chg24_now = float(d.get("chg24") or 0.0)
        warm = _TRIG_WARM_CHG24 <= chg24_now < _TRIG_LATE_CHG24
        if warm:
            score = round(max(1.0, score - _TRIG_WARM_PENALTY), 1)
        # 确认因子补充触发依据
        reasons = list(f["reasons"])
        if warm:
            # 插到最前而不是追加：reasons 会被 [:6] 截断，追加等于把这条最该看见的信息丢掉。
            reasons.insert(0, f"涨幅已温 {chg24_now:+.1f}%（3~10% 段降分不挡）")
        if cf:
            if abs(cf.get("oi_chg24", 0) or 0) >= 5:
                reasons.append(f"OI 24h {cf['oi_chg24']:+.1f}%")
            if abs(cf.get("funding", 0) or 0) >= 0.0015:
                reasons.append(f"费率 {cf['funding'] * 100:+.3f}%")
            if (cf.get("taker_ratio") or 0) >= _TAKER_BUY_DOMINANT:
                reasons.append(f"taker {cf['taker_ratio']:.2f}")
            if (cf.get("top_ratio") or 0) >= 1.5 or 0 < (cf.get("top_ratio") or 1) <= 0.7:
                reasons.append(f"大户比 {cf['top_ratio']:.2f}")
            if (cf.get("liq_5m", 0) or 0) >= 3e5:
                reasons.append(f"爆仓 ${cf['liq_5m'] / 1e6:.1f}M/5m")
        # v1.6.3 控盘代理：命中即写进依据，让「为什么被降级」可解释
        mflags, mnote, _mpen = _manip_flags(d)
        if mflags:
            reasons.append("控盘异常 " + "/".join(mflags))
        rows.append({
            "symbol": sym, "price": d["price"],
            "manip": mflags, "manip_note": mnote,
            # v1.5.66：控盘代理的**原始底料**也出行。雷达确认层有 24 币封顶，未被覆盖的行
            # `oi_usd` 恒为 0，于是「换手畸高 / 拉升无爆仓」两个指纹永远算不出来。
            # 妖币引擎在按需补完确认层因子（oi_last）后，需要拿这几个原始值**重算**指纹；
            # 不带上它们，那一半行就只能停在「未见控盘指纹」——这正是「每只币都一样」的来源。
            "fut_qv": round(futq.get(sym, 0.0) or 0.0, 0),
            "spot_qv": round(d.get("spot_qv", 0.0) or 0.0, 0),
            "no_spot": bool(d.get("no_spot")),
            "oi_usd": round(d.get("oi_usd", 0.0) or 0.0, 0),
            "change24_pct": round(d.get("chg24", 0.0), 2),
            # v1.6.4：OI 24h 提到行顶层（原来只在 factors 里），供登记判据与快照落库直接读。
            # 确认为空时写 **None（未测到）而不是 0** —— 0 会被判据误读成「持仓没动」。
            "oi_chg24": (round(d["oi_chg24"], 2) if d.get("oi_chg24") is not None else None),
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
            "warm": warm,   # v1.6.5（OPT-04）：登记时已涨 3%~10%（降分不挡），供回放分桶
            "floor_rising": bool(d.get("floor_rising")),
            "factors": {
                "flow": round(d.get("flow", 0.0) or 0.0, 1),
                "jump": round(d.get("jump_L", 0.0) or 0.0, 2),
                "speed5m": round(d.get("speed5m", 0.0) or 0.0, 2),
                "rvol15": round(d.get("rvol15", 0.0) or 0.0, 2),
                "amp24": round(d.get("amp24", 0.0) or 0.0, 2),
                "funding": cf.get("funding", d.get("funding", 0.0)),
                "funding_peak": cf.get("funding_peak"),
                # v1.7.1（#10 成本模型修正）：该币资金费率的**结算周期**（小时）。
                # 三态，不能合并：
                #   · 具体小时数（4.0 / 8.0 / 1.0）= 实测到了周期
                #   · 8.0 = 接口通了、但该币不在 fundingInfo 里 → 币安**默认周期**，
                #     这是有依据的默认值，不是假设
                #   · None = 接口没通 → **未测到**，消费方必须自己回退默认值并自知那是假设
                # 合并掉后两态的后果：成本模型会拿「假设的 8h」冒充「实测的 8h」，
                # 而这正是本轮要修的那个缺陷的同一形状。
                "funding_interval_h": (float(fund_iv.get(sym) or FUNDING_CYCLE_DEFAULT_H)
                                       if _fi_ok else None),
                # v1.6.6（问题清单 #7）：`factors` 层此前用 `cf.get(k, 0.0) or 0.0` 兜底，
                # 于是**同一个概念在同一个 dict 的两层里语义相反** —— 顶层「缺值写 None
                # （未测到）」，factors 层「缺值写 0.0（没变化）」。而 OI 是 OI 堆积线、
                # 燃料轴、控盘轴三处的关键输入，任何新消费方直接读 factors 层就会把
                # 「压根没测」读成「持仓没动」。代码里已有两处专门规避（`radar_tracker._pick`
                # 按键存在性取值、`square_monster._axis_fuel` 显式回查顶层），正是这个陷阱的证据。
                # 现在两层统一走**同一个表达式**，从结构上保证不可能再出现分歧。
                "oi_chg24": (round(d["oi_chg24"], 2) if d.get("oi_chg24") is not None else None),
                "oi_pulse15": (round(d["oi_pulse15"], 2) if d.get("oi_pulse15") is not None else None),
                "top_ratio": cf.get("top_ratio"),
                "global_ratio": cf.get("global_ratio"),
                "taker_ratio": cf.get("taker_ratio"),
                # v1.6.6（档一）：`markPrice − indexPrice` 的偏离（基点）。外部文献里这是
                # 「跨所标记价格操纵」的直接观测量 —— 标记价被单向拉离指数价，意味着
                # 有人在用标记价推动强平而不是真实成交。None = 未测到。
                "basis_bps": (fund_meta.get(sym) or {}).get("basis_bps"),
                # v1.6.6（档一）：买卖价差（基点）。现货行有、纯合约行恒为 None。
                "spread_bps": d.get("spread_bps"),
                # v1.7.1（#10 成本模型闭环）：**合约**盘口价差（基点）。纯合约币在这里
                # 终于有值了 —— 上面那列对它们恒为 None，于是「滑点常数该标定成多少」
                # 这个闭环此前对最该标定的那批币是断的。None = 未测到。
                "fut_spread_bps": d.get("fut_spread_bps"),
                "top_mean48": cf.get("top_mean48"),
                # 强平流：未测到时写 None（不是 0）—— 见 _confirm_factors 的 D1 注释。
                # 前端 `if (f.liq_5m != null && ...)` 与 `radar_tracker` 的 `if f.get("liq_5m")`
                # 都天然把 None 当「不展示」，所以这里保持 None 即可让三处静默空转变为显式缺失。
                "liq_5m": (round(d["liq_5m"], 0) if d.get("liq_5m") is not None else None),
                "liq_side": cf.get("liq_side", ""),
                "btc_beta": d.get("btc_beta"),
                "btc_residual": d.get("btc_residual"),
            },
            "reasons": reasons[:6],
            "listed_days": round(d.get("listed_days", 0) or 0, 1) if d.get("listed_days") else None,
            # v1.6.6（问题清单 D1）：强平数据源可用性随行下发，供 UI 区分
            # 「没有爆仓」与「爆仓流不可用」。不给出这个标志时，两者在界面上完全一样。
            "liq_available": bool(cf.get("liq_available")) if cf else None,
            "cooldown": cooldown,
        })
        stage_counts[stage] = stage_counts.get(stage, 0) + 1

    # v1.6.6（档二 C6）：循环结束后**一次事务**写入本地时序库（旁路，失败不影响返回）。
    # 放在这里而不是循环内：SQLite 每行一次 commit 会让扫描尾部多出上百次 fsync。
    _ts_persist(_ts_rows)

    rows.sort(key=lambda x: x["score"], reverse=True)
    # v1.6.3 起：**只有「尚未启动」的币才可登记**（登记即入场，不设候选态）。
    # 实测（安装版 20 笔 LONG 已关单）：3 笔 moon 的登记依据里**全都没有「24h +X%」**，
    # 即登记时 chg24 < _TRIG_UP_CHG24；而 9 笔「登记时已涨」的**全是 dump**。
    # 回放：现状 20 笔 −1086.5U / 胜率 15.0% → 只留未启动 7 笔 **+213.5U / 胜率 42.9%**。
    # 已启动的行不删除，划归 takeoff（仅展示），让「雷达看见但太晚」这件事仍可解释。
    #
    # v1.6.5（OPT-04，用户拍板）硬挡线 3.0 → 10.0，3%~10% 段降分不挡。证据边界见
    # `_TRIG_LATE_CHG24` 注释：9 笔已启动样本 chg24 最小 11.3%，3%~11.3% 无样本，
    # 有据的只是「≥11.3% 该挡」；原 3% 是外推。这段改降分后**是「放宽」方向**，
    # 唯一依据是逻辑（3%~10% 恰是「刚点着火」）而非样本 —— 属用户确认的风险偏好。
    #
    # v1.6.4：判据从「价格已启动」扩为「**价格已启动 或 持仓已堆积**」——
    # 两者是「你来晚了」的同一件事的两个维度（证据见 _TRIG_MAX_OI24 注释，方向一致性 10/10）。
    def _piled(r) -> bool:
        """持仓是否已堆积。**确认因子缺失时返回 False（fail-open）**：
        接口失败时必须与扩池前行为一致，不能因为拿不到 OI 就少登记。"""
        try:
            v = r.get("oi_chg24")
            return v is not None and float(v) >= _TRIG_MAX_OI24
        except (TypeError, ValueError):
            return False

    def _late(r) -> bool:
        """v1.6.5（OPT-04）：价格硬挡线由 3.0 抬到 10.0（3%~10% 只降分不挡，见 `_warm`）。
        OI 判据不变 —— 持仓堆积是「你来晚了」的第二个维度，与价格无关。"""
        return (r.get("change24_pct") or 0.0) >= _TRIG_LATE_CHG24 or _piled(r)

    rows_ign = [r for r in rows if r["stage"] in ("ACCUMULATION", "IGNITION") and not _late(r)]
    # v1.6.3：**SHORT_AMBUSH 移出 ignition（登记）组，改入 takeoff（仅展示）组**。
    # 实测证据（安装版已关单 3 笔做空全亏）：CAKE +10.0% / THETA +10.3% / SAGA +11.0%
    # 全部逆向轧空击穿 10% 强平线，而**最大有利仅 0.0% / 0.0% / 1.4%** —— 从一开始就没跌过，
    # 复盘原文都是「直接轧空上行（未给回踩）」。RAVE 里空头爆仓占全部爆仓 82%、LAB 更是
    # 「只需维持横盘，做空者账户便会因费率清零」。用户定位：**绝不做空**。
    # 顶部风险仍需可见（派发/做空意图对持仓者是离场信号），所以只降级、不删除。
    rows_tk = [r for r in rows if r["stage"] in ("VERTICAL", "DISTRIBUTION", "CRASH", "EXTENDED", "SHORT_AMBUSH")]
    rows_tk += [r for r in rows if r["stage"] in ("ACCUMULATION", "IGNITION") and _late(r)]
    rows_tk += [r for r in rows if r["stage"] == "DORMANT"
                and (r.get("change30d_pct") or 0) >= 30.0]

    # v1.6.6（问题清单 #5）：**展示层与分组对齐**。
    #
    # 病根是「阶段标签断层」：`_late` 用 `_TRIG_LATE_CHG24`(10.0)，而语义层判 `EXTENDED`
    # 要 `chg24 > _TRIG_MAX_CHG24`(12.0)。于是 `chg24 ∈ (10, 12]` 的行判不出 EXTENDED，
    # stage 仍是 IGNITION（tag「点火 · 破位启动」、side LONG、绿底），却被 `_late`
    # 划进 takeoff（追高高风险）组 —— takeoff 页里出现「绿底 + LONG + 写着『点火 = 可埋伏』」
    # 的行，而它实际已被判定「你来晚了」。`_piled`（OI 24h ≥ 5%）为真时同样如此，与涨幅无关。
    #
    # 修法：**不动 `stage`**（它是语义层的真实输出，且被 `stage_counts`、回放分桶、
    # `square_monster` 依赖），只改写**展示层**的 tag / stage_label / side，
    # 并加一个显式的 `late` 标志给前端换 pill 颜色。这样「分组」与「标签」不再互相矛盾，
    # 且「语义层判它是什么」与「现在能不能上车」两件事都如实表达。
    for r in rows_tk:
        if r["stage"] in ("ACCUMULATION", "IGNITION") and _late(r):
            _orig = r.get("stage_label") or r["stage"]
            r["late"] = True
            r["tag"] = "已启动 · 追高区"
            r["stage_label"] = "已启动"
            r["side"] = "WATCH"
            r["note"] = (f"语义层仍判「{_orig}」，但已越过启动窗口"
                         f"（24h 涨幅 ≥ {_TRIG_LATE_CHG24:.0f}% 或 OI 24h ≥ {_TRIG_MAX_OI24:.0f}%）；"
                         "此处进场即接力末端，仅作观察")

    rows_tk.sort(key=lambda x: x["score"], reverse=True)
    rows_ign.sort(key=lambda x: x["score"], reverse=True)

    bars_env = {s: (b[3], b[4]) for s, b in d2.items()}
    payload = {
        "coins": rows, "takeoff": rows_tk, "ignition": rows_ign,
        "scanned": len(feats), "candidates": len(pool_syms),
        "triggered": sum(1 for f in feats.values() if f["hit"]),
        "confirmed": len(confirms),
        "excluded_new": n_new, "excluded_wash": n_wash,
        "excluded_no_bars": n_no_bars,
        "min_qv": floor, "env": _env_regime(bars_env),
        "stage_counts": stage_counts, "engine": "v2",
        # v1.6.6（问题清单 D1）：强平流（`!forceOrder@arr`）在部署地区实测不推数据，
        # 且无公开 REST 兜底 → 该信号整体不可用。把它作为**顶层标志**下发，
        # 让前端能显式提示「爆仓流不可用」，而不是让用户把「没有爆仓」读成一种结论。
        "liq_available": _liq_source_available(),
        # v1.6.6（问题清单 #2）：**把判据阈值随 payload 下发**，消除前后端双份硬编码。
        # 病根：后端 v1.5.66 把 `_TAKER_BUY_DOMINANT` 从 1.85 修到 1.30，前端因子摘要行
        # 仍硬编码 1.5 —— 同一个数在两处不同义。后果是 taker ∈ [1.30, 1.50) 的币
        # 「买盘主导」信号在 UI 上完全不可见（后端 reasons 里写了，但前端既不显示摘要 bit，
        # 又因为 `bits.length ? bits.join() : note` 的短路，连含该信息的 note 也不显示），
        # 于是「分数为什么高」在界面上解释不通。阈值由后端单一来源下发后不会再漂。
        "thresholds": {
            "taker_buy_dominant": _TAKER_BUY_DOMINANT,
            "trig_up_chg24": _TRIG_UP_CHG24,
            "trig_late_chg24": _TRIG_LATE_CHG24,
            "trig_warm_chg24": _TRIG_WARM_CHG24,
            "trig_max_chg24": _TRIG_MAX_CHG24,
            "trig_max_oi24": _TRIG_MAX_OI24,
            "trig_amp_ok": _TRIG_AMP_OK,
            "trig_amp_risk": _TRIG_AMP_RISK,
            "manip_spot_share_min": _MANIP_SPOT_SHARE_MIN,
            "manip_churn_max": _MANIP_CHURN_MAX,
            "manip_neg_funding": _MANIP_NEG_FUNDING,
            "new_coin_days": NEW_COIN_DAYS,
        },
        # v1.6.6（档一 §16）：阈值命中率 —— 让「够不着的死规则」在界面上可见。
        # 缘起：`_TAKER_BUY_DOMINANT = 1.85` 自上线起命中 0 个却在四处被使用，
        # 这类规则不报错、不让测试变红，只能靠命中率暴露。`dead_rules` 非空 = 该看一看了。
        "threshold_hits": threshold_hitrate(),
        # v1.6.6（档二 C6）：本地时序库体检 —— 让「到底存没存下来」可见。
        # 与 §16 同源动机：只写不看的落盘等于没有落盘，出问题时没人会发现。
        "ts_store": ts_store_view(),
        "updated_at": int(cycle_ts), "ttl": radar2_ttl(),
        # v1.7.1（C3 延迟治理 · 可见化）：`age_sec` / `stale` **不在这里定稿** ——
        # 它们的定义是「**读的时刻** − 扫描时刻」，而这里只是扫描时刻，读的时刻还不知道。
        # 所以由 `_with_age()` 在所有返回路径上补写（见该函数）。此处占位 0/False，
        # 保证直接读 `_radar2_cache["data"]` 的旧消费方（如 radar_tracker）拿到的结构不变。
        "age_sec": 0,
        "stale": False,
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
    v2 阶段映射：垂直拉升/已拉升(EXTENDED)/派发顶部/崩跌/沉寂(泵后) → takeoff 列表。"""
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
                "ttl": v2.get("ttl", radar2_ttl())}
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
                "ttl": v2.get("ttl", radar2_ttl())}
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

# ---------------- v1.5.4 行情增强：K 线走势 / 合约 OI / 恐惧贪婪指数 ----------------

_klines_close_cache: dict = {}   # (market, sym, interval, limit) -> (ts, closes)


def klines_closes(sym: str, interval: str = "1h", limit: int = 24, market: str = "spot") -> list:
    """最近 N 根 K 线收盘价（旧→新，含当前未收 bar）。spot→现货 API；futures→U 本位合约 API。
    5 分钟内存缓存（Hero 卡 / 详情浮层共用）；拉新失败回退旧缓存；完全失败返回 []。"""
    key = (market, sym, interval, int(limit))
    now = time.time()
    hit = _klines_close_cache.get(key)
    if hit and now - hit[0] < 300:
        return hit[1]
    closes: list = []
    try:
        if market == "futures":
            r = _session.get(f"{FAPI}/fapi/v1/klines",
                             params={"symbol": sym, "interval": interval, "limit": int(limit)}, timeout=10)
            r.raise_for_status()
            closes = _ohlcv(r.json() or [])[3]
        else:
            closes = _ohlcv(_klines_raw(sym, interval, int(limit)))[3]
    except Exception:
        closes = []
    if closes:
        _klines_close_cache[key] = (now, closes)
    elif hit:
        closes = hit[1]          # 上游临时失败：沿用旧缓存（并顺延有效期防打爆上游）
        _klines_close_cache[key] = (now, closes)
    return closes


_klines_ohlcv_cache: dict = {}   # (market, sym, interval, limit) -> (ts, dict)


def klines_ohlcv(sym: str, interval: str = "1d", limit: int = 90, market: str = "futures") -> dict:
    """最近 N 根 K 线完整 OHLCV + 开盘时间(ms)（旧→新，含当前未收 bar）。
    square-rich-post 图表引擎用；5 分钟缓存，拉新失败回退旧缓存，完全失败返回空表。"""
    key = (market, sym, interval, int(limit))
    now = time.time()
    hit = _klines_ohlcv_cache.get(key)
    if hit and now - hit[0] < 300:
        return hit[1]
    out = {"opens": [], "highs": [], "lows": [], "closes": [], "vols": [], "times": []}
    try:
        if market == "futures":
            r = _session.get(f"{FAPI}/fapi/v1/klines",
                             params={"symbol": sym, "interval": interval, "limit": int(limit)}, timeout=10)
            r.raise_for_status()
            raw = r.json() or []
        else:
            raw = _klines_raw(sym, interval, int(limit))
        for x in raw:
            try:
                out["times"].append(int(x[0]))
                out["opens"].append(float(x[1]))
                out["highs"].append(float(x[2]))
                out["lows"].append(float(x[3]))
                out["closes"].append(float(x[4]))
                out["vols"].append(float(x[5]))
            except (TypeError, ValueError, IndexError):
                continue
    except Exception:
        pass
    if out["closes"]:
        _klines_ohlcv_cache[key] = (now, out)
    elif hit:
        out = hit[1]
        _klines_ohlcv_cache[key] = (now, out)
    return out


_oi_cache: dict = {}             # sym -> (ts, oi_base, price)


def futures_open_interest(symbols: list, workers: int = 8) -> list:
    """批量查合约持仓量（U 本位永续）：OI（币本位数量）× 最新价 → USD 名义持仓。
    5 分钟缓存；失败项 oi/price/notional 为 null。返回 [{symbol, oi, price, notional}]（与入参同序）。"""
    syms = []
    for s in (symbols or []):
        s = str(s).strip().upper()
        if s and s not in syms:
            syms.append(s)
    syms = syms[:80]
    if not syms:
        return []
    now = time.time()
    out: dict = {}
    need: list = []
    for s in syms:
        hit = _oi_cache.get(s)
        if hit and now - hit[0] < 300:
            oi, px = hit[1], hit[2]
            out[s] = {"symbol": s, "oi": oi, "price": px,
                      "notional": (oi * px) if (oi is not None and px) else None}
        else:
            need.append(s)

    def _one(sym: str):
        try:
            r = _session.get(f"{FAPI}/fapi/v1/openInterest", params={"symbol": sym}, timeout=6)
            r.raise_for_status()
            oi = float(r.json().get("openInterest", 0) or 0)
            rp = _session.get(f"{FAPI}/fapi/v1/ticker/price", params={"symbol": sym}, timeout=6)
            rp.raise_for_status()
            px = float(rp.json().get("price", 0) or 0)
            return sym, (oi if oi > 0 else None), (px if px > 0 else None)
        except Exception:
            return sym, None, None

    if need:
        with _cf.ThreadPoolExecutor(max_workers=max(2, min(workers, len(need)))) as ex:
            for sym, oi, px in ex.map(_one, need):
                if oi is not None and px is not None:
                    _oi_cache[sym] = (now, oi, px)
                out[sym] = {"symbol": sym, "oi": oi, "price": px,
                            "notional": (oi * px) if (oi is not None and px) else None}
    return [out[s] for s in syms if s in out]


_fng_cache: dict = {"ts": 0.0, "data": None}


def fear_greed_index() -> dict:
    """恐惧贪婪指数（alternative.me，免费无 Key，8 天历史）。10 分钟缓存；失败不缓存并返回 error。"""
    now = time.time()
    if _fng_cache["data"] and now - _fng_cache["ts"] < 600:
        return _fng_cache["data"]
    try:
        r = _session.get("https://api.alternative.me/fng/", params={"limit": 8}, timeout=8)
        r.raise_for_status()
        data = r.json().get("data") or []
        hist = []
        for x in data:
            try:
                hist.append({"ts": int(x["timestamp"]), "value": int(x["value"])})
            except (KeyError, TypeError, ValueError):
                continue
        hist.reverse()   # 旧→新
        cur = hist[-1] if hist else None
        payload = {"value": cur["value"] if cur else None,
                   "classification": (data[0].get("value_classification") if data else None),
                   "history": hist, "updated_at": int(now)}
        _fng_cache["ts"] = now
        _fng_cache["data"] = payload
        return payload
    except Exception as e:
        return {"value": None, "classification": None, "history": [], "error": str(e)}



# ---------------- 币种消息面（零 Key，中文快讯 + 英文头条） ----------------
# 与 news-sentiment 技能同一批公开源，但用 Python 直接抓：这样广场文章合成不必依赖
# Node 运行时，也不会走 run_skill() 里那条「网络失败自动切代理重试」的路径
# （用户明确不喜欢应用偷偷连代理）。
_NEWS_TTL = 300.0            # 5 分钟内存缓存，避免同一轮生成里重复打三个源
_news_cache = {"ts": 0.0, "items": [], "failed": []}

_NEWS_SOURCES = (
    ("PANews", "https://api.panewslab.com/newsweb/v1/flashlist?language=cn"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
)

# 币 → 常见项目名/别名。中文按子串、英文按词边界匹配（不区分大小写）。
COIN_ALIASES = {
    "BTC": ["bitcoin", "比特币"], "ETH": ["ethereum", "以太坊", "以太"], "SOL": ["solana"],
    "BNB": ["binance coin", "币安币"], "XRP": ["ripple", "瑞波"],
    "DOGE": ["dogecoin", "狗狗币", "狗狗"], "ADA": ["cardano"], "LINK": ["chainlink"],
    "TON": ["toncoin", "ton 链"], "TRX": ["tron", "波场"], "WLD": ["worldcoin"],
    "RAY": ["raydium"], "AVAX": ["avalanche"], "DOT": ["polkadot", "波卡"],
    "SUI": ["sui network", "sui 链"], "PEPE": ["pepe"], "ARB": ["arbitrum"],
    "OP": ["optimism"], "APT": ["aptos"], "NEAR": ["near protocol"],
    "ENA": ["ethena"], "HYPE": ["hyperliquid"], "TAO": ["bittensor"],
    "JUP": ["jupiter"], "WIF": ["dogwifhat"], "BONK": ["bonk"], "SEI": ["sei network"],
    "INJ": ["injective"], "FIL": ["filecoin"], "ATOM": ["cosmos"], "LTC": ["litecoin", "莱特币"],
}

_NEWS_BULL = ["涨", "突破", "利好", "看多", "买入", "增持", "上涨", "新高", "反弹", "飙升",
              "流入", "获批", "上线", "合作", "回购", "销毁", "解锁增持",
              "bullish", "surge", "rally", "gain", "soar", "jump", "record", "inflow",
              "adopt", "approve", "upgrade", "breakout", "all-time", "partnership", "buyback"]
_NEWS_BEAR = ["跌", "暴跌", "崩", "利空", "看空", "卖出", "抛售", "清算", "爆仓", "下跌",
              "新低", "流出", "被查", "盗", "诉讼", "下架", "黑客", "漏洞", "抛压",
              "bearish", "plunge", "crash", "dump", "liquidat", "hack", "exploit",
              "lawsuit", "ban", "sell-off", "selloff", "outflow", "decline", "fraud", "delist"]


def _news_hit(text: str, token: str) -> bool:
    """中文按子串，英文按词首边界（避免 op 命中 operation 这类误伤由调用方处理）。"""
    if not token:
        return False
    if any("\u4e00" <= ch <= "\u9fff" for ch in token):
        return token in text
    return re.search(r"\b" + re.escape(token.lower()), text.lower()) is not None


def _news_match(title: str, sym: str) -> bool:
    """标题是否在说这个币。"""
    for alias in COIN_ALIASES.get(sym, []):
        if _news_hit(title, alias):
            return True
    if len(sym) >= 3:
        return _news_hit(title, sym)
    # 1~2 个字母的 ticker（OP/SUI 之外还有 AR 等）只认原文里独立的大写出现，
    # 否则「op」会命中 operation/option 等一大片无关标题。
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(sym) + r"(?![A-Za-z0-9])", title) is not None


def _news_sentiment(title: str) -> str:
    b = sum(1 for w in _NEWS_BULL if _news_hit(title, w))
    r = sum(1 for w in _NEWS_BEAR if _news_hit(title, w))
    return "bull" if b > r else "bear" if r > b else "neutral"


def _news_ms(v) -> int:
    """时间戳归一化到毫秒（源有 ms / s / ISO / RFC822 四种写法）。"""
    try:
        n = float(v)
        if n <= 0:
            return 0
        return int(n * 1000) if n < 1e11 else int(n)
    except (TypeError, ValueError):
        s = str(v or "").strip()
        if not s:
            return 0
        try:
            from email.utils import parsedate_to_datetime
            return int(parsedate_to_datetime(s).timestamp() * 1000)
        except Exception:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                return int(time.mktime(time.strptime(s[:19], fmt)) * 1000)
            except Exception:
                continue
        return 0


def _news_panews() -> list:
    r = _session.get(_NEWS_SOURCES[0][1], timeout=12)
    r.raise_for_status()
    d = r.json() or {}
    data = d.get("data")
    lst = data.get("list") or data.get("List") if isinstance(data, dict) else (data if isinstance(data, list) else [])
    out = []
    for x in (lst or [])[:60]:
        if not isinstance(x, dict):
            continue
        t = re.sub(r"<[^>]+>", "", str(x.get("title") or x.get("Content") or x.get("content") or "")).strip()
        if t:
            out.append({"title": t[:160], "source": "PANews",
                        "ts": _news_ms(x.get("time") or x.get("PublishTime") or x.get("publish_time"))})
    return out


def _news_rss(url: str, source: str) -> list:
    r = _session.get(url, timeout=12)
    r.raise_for_status()
    out = []
    for m in re.finditer(r"<item>([\s\S]*?)</item>", r.text, re.I):
        body = m.group(1)

        def pick(tag: str) -> str:
            mm = re.search(r"<" + tag + r"[^>]*>([\s\S]*?)</" + tag + r">", body, re.I)
            return mm.group(1).strip() if mm else ""

        t = re.sub(r"<[^>]+>", "", pick("title").replace("<![CDATA[", "").replace("]]>", "")).strip()
        if t:
            out.append({"title": t[:160], "source": source,
                        "ts": _news_ms(pick("pubDate") or pick("published"))})
    return out


def _news_fetch_all() -> dict:
    """三源混合去重（新→旧）。某源失败只记 failed，不当致命错误。"""
    items, failed = [], []
    for name, url in _NEWS_SOURCES:
        try:
            items += _news_panews() if name == "PANews" else _news_rss(url, name)
        except Exception:
            failed.append(name)
    seen, uniq = set(), []
    for x in sorted(items, key=lambda v: v.get("ts") or 0, reverse=True):
        k = x["title"].lower()
        if k in seen:
            continue
        seen.add(k)
        x["sentiment"] = _news_sentiment(x["title"])
        uniq.append(x)
    return {"items": uniq[:80], "failed": failed}


def coin_news(symbol: str, n: int = 10, force: bool = False) -> dict:
    """某币相关新闻（标题/来源/时间/情绪）。全源失败返回空 items + failed，不抛异常。

    返回 {symbol, count, items:[{title,source,ts,sentiment}], failed:[源名]}。
    情绪为关键词启发式，只做消息面参考 —— 与 K 线/费率冲突时以后者为准。
    """
    sym = (symbol or "").strip().upper()
    if sym.endswith("USDT"):
        sym = sym[:-4]
    if not sym:
        return {"symbol": "", "count": 0, "items": [], "failed": []}
    now = time.time()
    if force or not _news_cache["items"] or now - _news_cache["ts"] > _NEWS_TTL:
        got = _news_fetch_all()
        _news_cache.update({"ts": now, "items": got["items"], "failed": got["failed"]})
    hit = [x for x in _news_cache["items"] if _news_match(x["title"], sym)]
    return {"symbol": sym, "count": len(hit), "items": hit[:max(1, int(n))],
            "failed": list(_news_cache["failed"])}


if __name__ == "__main__":
    import pprint
    pprint.pprint(scan_top20()[:5])
