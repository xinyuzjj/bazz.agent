"""链上钱包（CEX 账户，API Key + Secret 签名）——只读资产模块

与 Agentic Wallet（baw MPC 扫码）是两套独立体系：
  - Agentic Wallet：Binance App 扫码登录（MPC 无密钥），见 wallet_client.py
  - 本模块：用户自持 Binance API Key + Secret，HMAC 签名调用 CEX REST，
    仅做账户 / 资产估值等**只读**查询，不承载下单（下单走 executor + 人工确认）。

密钥存储：本地 sqlite settings（state.set_setting），与 desktop /api/settings 一致，
绝不落日志、绝不返回明文给前端（只回掩码）。
"""
import os
import time
import hmac
import hashlib
import threading
from urllib.parse import urlencode, quote
from typing import Optional, Tuple

import requests
import state
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.binance.com"

# 估值缓存（ticker 全量拉取 ~200KB，8s TTL 够用且不刷屏）
_SUM_CACHE = {"ts": 0.0, "data": None}
_SUM_LOCK = threading.Lock()


# ---------------- 密钥存取 ----------------

def _stored() -> Tuple[str, str]:
    """读取已保存密钥：settings 优先，回退 .env（BINANCE_API_KEY / BINANCE_SECRET_KEY）。"""
    k = (state.get_setting("BINANCE_API_KEY", "") or "").strip()
    s = (state.get_setting("BINANCE_API_SECRET", "") or "").strip()
    if not k or not s:
        k = k or (os.getenv("BINANCE_API_KEY", "") or "").strip()
        s = s or (os.getenv("BINANCE_SECRET_KEY", "") or os.getenv("BINANCE_API_SECRET", "") or "").strip()
    return k, s


def configured() -> bool:
    k, s = _stored()
    return bool(k and s)


def masked_key() -> str:
    k, _ = _stored()
    if not k:
        return ""
    return f"{k[:6]}…{k[-4:]}"


def save_keys(api_key: str, secret: str) -> None:
    state.set_setting("BINANCE_API_KEY", (api_key or "").strip())
    state.set_setting("BINANCE_API_SECRET", (secret or "").strip())


def clear_keys() -> None:
    state.set_setting("BINANCE_API_KEY", "")
    state.set_setting("BINANCE_API_SECRET", "")


# ---------------- 签名请求 ----------------

def _sign(params: dict, secret: str) -> str:
    """构造签名查询串（直接可拼在 URL 后发送）。

    Binance Spot CHANGELOG（2025-12-17 公告，2026-01-15 起强制）：
    调用需签名的端点时，必须【先对 payload 做百分号编码，再计算签名】，
    否则返回 -1022 INVALID_SIGNATURE。因此这里用 RFC3986 编码（空格→%20，
    而非 urlencode 默认的 +）生成 qs，并对同一串做 HMAC；返回的字符串
    与最终发送内容逐字节一致，避免二次编码导致签名失配。
    """
    params["timestamp"] = int(time.time() * 1000)
    qs = urlencode(params, quote_via=quote)  # 先编码
    sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()  # 后签名
    return f"{qs}&signature={sig}"


def _get_signed(path: str, params: dict, api_key: str, secret: str) -> dict:
    qs = _sign(params, secret)  # 直接发送签名用的同一查询串，不再让 requests 二次编码
    req = requests.get(f"{BASE_URL}{path}?{qs}",
                       headers={"X-MBX-APIKEY": api_key}, timeout=15)
    if req.status_code != 200:
        # Binance 返回的错误通常带 {"code":-2014,"msg":"API-key format invalid."}
        body_msg = ""
        body_code = None
        try:
            e = req.json()
            body_msg = str(e.get("msg") or e.get("message") or "")
            body_code = e.get("code")
        except Exception:
            body_msg = req.text[:200]
        msg = body_msg or f"HTTP {req.status_code}"
        # 错误归因（区分三种场景，给前端可操作提示）：
        #   -2014 / "API-key format invalid" → Key 不是 HMAC 格式（可能 Ed25519/BX- 或抄错）
        #   -2015 / "Invalid API-key, IP, or permissions" → Key 无效 / IP 不在白名单 / 权限不足
        #   其它签名/时间戳错 → -1021/-1022 等
        if "API-key format invalid" in msg or body_code == -2014:
            raise ValueError("KEY_FORMAT::API-key format invalid（-2014）")
        if body_code == -2015 or "invalid api-key, ip, or permissions" in msg.lower():
            raise ValueError(f"IP_PERM::{msg}（-2015）")
        if body_code in (-1022, -1021, -1003) or "signature" in msg.lower() or "timestamp" in msg.lower():
            raise ValueError(f"签名 / 时间戳被拒（{body_code}）：{msg}（请检查 Secret Key 是否对应、机器时钟是否同步）")
        raise PermissionError(msg) if req.status_code in (401, 403) else ConnectionError(msg)
    return req.json()


def _all_prices() -> dict:
    """一次拉全量币价（公开接口，无需签名）：{SYMBOL: float}。

    同时拉 USDT 对与 BTC 兑 USDT 价格，用于对没有直接 USDT 对的币种做交叉估值。
    """
    r = requests.get(BASE_URL + "/api/v3/ticker/price", timeout=20)
    r.raise_for_status()
    prices: dict = {i["symbol"]: float(i["price"]) for i in r.json() if i.get("symbol")}
    btc_usdt = prices.get("BTCUSDT")
    return {"pairs": prices, "btc_usdt": btc_usdt}


def account_summary(keys: Optional[Tuple[str, str]] = None, force: bool = False) -> dict:
    """账户资产快照（只读）：非零余额 + 按 USDT 现价折算估值。

    估值规则（按优先级）：
      1. {ASSET}USDT 直接对；
      2. 否则 {ASSET}BTC × BTCUSDT 交叉估值；
      3. 都没有的币标记 `no_usdt_pair=True`，仍列入持仓但 `usdt=0`，从 `total_usdt` 中排除。
    """
    if keys is None:
        keys = _stored()
        if not keys[0] or not keys[1]:
            return {"status": "error", "code": "not_configured",
                    "message": "尚未配置 Binance API Key / Secret。",
                    "masked_key": masked_key()}
    api_key, secret = keys

    now = time.time()
    cached = _SUM_CACHE["data"]
    if not force and cached is not None and (now - _SUM_CACHE["ts"]) < 8:
        return cached

    try:
        acc = _get_signed("/api/v3/account", {}, api_key, secret)
        balances = [b for b in acc.get("balances", [])
                    if float(b.get("free", 0)) > 0 or float(b.get("locked", 0)) > 0]
        px = _all_prices()
        pairs: dict = px["pairs"]
        btc_usdt = px.get("btc_usdt") or 0

        def to_usdt(b: dict) -> tuple[float, bool]:
            asset = b["asset"]
            if asset == "USDT":
                return float(b["free"]) + float(b["locked"]), False
            direct = pairs.get(f"{asset}USDT")
            if direct is not None:
                return (float(b["free"]) + float(b["locked"])) * direct, False
            if btc_usdt:
                x = pairs.get(f"{asset}BTC")
                if x is not None:
                    return (float(b["free"]) + float(b["locked"])) * x * btc_usdt, False
            return 0.0, True  # 没有可估值的交易对

        rows = []
        for b in balances:
            usdt, no_pair = to_usdt(b)
            rows.append({"asset": b["asset"], "free": float(b["free"]), "locked": float(b["locked"]),
                         "usdt": round(usdt, 2), "no_usdt_pair": no_pair})
        rows.sort(key=lambda x: x["usdt"], reverse=True)
        total = round(sum(r["usdt"] for r in rows), 2)
        data = {"status": "ok",
                "account": {
                    "total_usdt": total,
                    "coin_count": len(rows),
                    "assets": rows,
                    "can_trade": bool(acc.get("canTrade")),
                    "can_withdraw": bool(acc.get("canWithdraw")),
                    "can_deposit": bool(acc.get("canDeposit")),
                },
                "masked_key": f"{api_key[:6]}…{api_key[-4:]}" if api_key else ""}
        with _SUM_LOCK:
            _SUM_CACHE["ts"] = now
            _SUM_CACHE["data"] = data
        return data
    except requests.exceptions.ConnectionError as e:
        return {"status": "error", "code": "network",
                "message": f"无法连接 Binance（{e.__class__.__name__}），请检查网络。",
                "masked_key": masked_key()}
    except requests.exceptions.Timeout:
        return {"status": "error", "code": "timeout",
                "message": "Binance API 请求超时（15s）。", "masked_key": masked_key()}
    except ValueError as e:
        msg = str(e)
        if msg.startswith("KEY_FORMAT::"):
            return {"status": "error", "code": "wrong_key_type",
                    "message": "Key 不是 CEX 现货 HMAC API Key 的格式（币安返回 -2014）。"
                               "若 Key 以 BX-/Ed25519 开头（Wallet API）或长度不是 64 字符，都无法走本 HMAC 通道。",
                    "hint": "本面板走的是 CEX 现货 HMAC 签名（api.binance.com）。"
                            "请到 binance.com → API 管理 创建 **HMAC 类型的 API Key**（64 字符），"
                            "不要使用 Wallet API（BX-/Ed25519）那一组；粘贴时核对 Key 是否 64 字符、无遗漏。",
                    "masked_key": masked_key()}
        if msg.startswith("IP_PERM::"):
            return {"status": "error", "code": "ip_or_permission",
                    "message": "币安拒绝了这把 Key（-2015 Invalid API-key, IP, or permissions）。"
                               "通常不是密钥格式问题，而是下面三者之一未满足：",
                    "hint": "① IP 白名单：在 API 管理里把出口 IP 117.170.200.178 加入该 Key 白名单（或临时关闭 IP 限制）；"
                            "② API Key 是否复制完整（应 64 字符）且与 Secret 配对；"
                            "③ 权限是否开启：现货读取/交易、合约等要按需勾选（提现保持关闭）。",
                    "masked_key": masked_key()}
        if "签名" in msg or "signature" in msg.lower() or "timestamp" in msg.lower():
            return {"status": "error", "code": "signature_invalid",
                    "message": msg,
                    "hint": "Secret Key 是否与 API Key 配对？机器时钟是否与网络时间一致？",
                    "masked_key": masked_key()}
        return {"status": "error", "code": "key_invalid",
                "message": msg, "masked_key": masked_key()}
    except PermissionError as e:
        return {"status": "error", "code": "key_invalid",
                "message": f"API Key 鉴权失败：{e}", "masked_key": masked_key()}
    except Exception as e:
        return {"status": "error", "code": "api_error",
                "message": str(e)[:300], "masked_key": masked_key()}


if __name__ == "__main__":
    print(configured(), masked_key())
    import json
    print(json.dumps(account_summary(force=True), ensure_ascii=False, indent=2)[:1200])
