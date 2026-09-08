"""Guarded Execution - 带人工确认的下单执行器"""
import os
import time
import hmac
import hashlib
from urllib.parse import urlencode, quote
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.binance.com"
API_KEY = os.getenv("BINANCE_API_KEY", "")
SECRET = os.getenv("BINANCE_SECRET_KEY", "")


def _sign(params: dict) -> str:
    """构造签名查询串。Binance 2026-01-15 起强制：先百分号编码 payload 再计算签名
    （RFC3986，空格→%20），否则 -1022。返回串与发送内容逐字节一致。"""
    params["timestamp"] = int(time.time() * 1000)
    qs = urlencode(params, quote_via=quote)
    sig = hmac.new(SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return f"{qs}&signature={sig}"


def get_account() -> dict:
    """获取账户信息（需要API Key）"""
    if not API_KEY or not SECRET:
        return {"error": "缺少 API Key，请在 .env 中配置 BINANCE_API_KEY 和 BINANCE_SECRET_KEY"}
    params = _sign({})
    r = requests.get(f"{BASE_URL}/api/v3/account?{params}",
                     headers={"X-MBX-APIKEY": API_KEY}, timeout=10)
    r.raise_for_status()
    return r.json()


def get_balances() -> list[dict]:
    """获取非零余额"""
    acc = get_account()
    if "error" in acc:
        return []
    return [b for b in acc.get("balances", []) if float(b.get("free", 0)) > 0]


def place_limit_order(symbol: str, side: str, quantity: str,
                      price: str, time_in_force: str = "GTC") -> dict:
    """
    下限价单（Spot）
    side: BUY / SELL
    """
    if not API_KEY or not SECRET:
        return {"error": "缺少 API Key"}
    params = _sign({
        "symbol": symbol,
        "side": side,
        "type": "LIMIT",
        "timeInForce": time_in_force,
        "quantity": quantity,
        "price": price,
    })
    r = requests.post(f"{BASE_URL}/api/v3/order?{params}",
                      headers={"X-MBX-APIKEY": API_KEY}, timeout=10)
    if r.status_code == 200:
        return r.json()
    return {"error": r.text, "status_code": r.status_code}


def place_oco_order(symbol: str, side: str, quantity: str,
                    price: str, stop_price: str, limit_price: str) -> dict:
    """
    放置 OCO 订单（Take Profit + Stop Loss 合一单）
    """
    if not API_KEY or not SECRET:
        return {"error": "缺少 API Key"}
    params = _sign({
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "listClientOrderId": f"oco_{int(time.time())}",
        "legs": [
            {"price": limit_price, "quantity": quantity, "side": side, "type": "LIMIT_MAKER"},
            {"stopPrice": stop_price, "quantity": quantity, "side": "SELL" if side == "BUY" else "BUY",
             "type": "STOP_LOSS_LIMIT", "timeInForce": "GTC", "price": stop_price},
        ],
    })
    r = requests.post(f"{BASE_URL}/api/v3/order/oco?{params}",
                      headers={"X-MBX-APIKEY": API_KEY}, timeout=10)
    if r.status_code == 200:
        return r.json()
    return {"error": r.text, "status_code": r.status_code}


def _wallet_place(signal: dict) -> dict:
    """Agent 钱包通道：走 baw（沿用现有 send/swap 链路，非 MCP/OAuth）。
    买入 = USDT→BASE 兑换；卖出 = BASE→USDT。失败返回含 error。"""
    try:
        from wallet_client import run_command as baw_run
    except Exception as e:
        return {"error": f"钱包执行器不可用：{e}"}
    symbol = str(signal.get("symbol") or "").upper()
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    if not base:
        return {"error": f"无法解析标的 {symbol}"}
    qty = float(signal.get("quantity", 0) or 0)
    price = float(signal.get("price", 0) or 0)
    if qty <= 0 or price <= 0:
        return {"error": "数量或价格无效，无法在钱包执行。"}
    bull = str(signal.get("direction") or "").upper() in ("BULLISH", "做多")
    if bull:
        amt = round(qty * price, 4)  # 买入：按 USDT 计输入额
        cmd = f"baw token swap USDT {base} {amt}"
    else:
        cmd = f"baw token swap {base} USDT {qty}"
    try:
        res = baw_run(cmd)
    except Exception as e:
        return {"error": str(e)[:300]}
    if res.get("status") == "ok":
        return {"orderId": "WALLET", "symbol": symbol, "status": "TRADE_SUBMITTED",
                "command": cmd, "output": (res.get("stdout") or res.get("stderr") or "")[-1200:]}
    err = (res.get("stderr") or res.get("detail") or res.get("stdout") or "钱包下单失败").strip()[:300]
    return {"error": err}


def confirm_and_place(signal: dict, confirm: bool = False) -> dict:
    """
    确认后下单：confirm=True 时才真实下单。
    通道由 Agent 决策：route=="wallet" 走 Agent 钱包(baw)；否则走交易所 API 密钥(cex_wallet)。
    """
    summary = {
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "entry_price": signal["price"],
        "stop_loss": signal.get("stop_loss"),
        "take_profit": signal.get("take_profit"),
        "max_loss_usdt": signal.get("max_loss_usdt"),
        "route": signal.get("route", "exchange"),
    }
    if not confirm:
        return {"status": "pending_confirm", "summary": summary,
                "message": "请人工确认：是否同意按上述参数下单？"}
    # 钱包通道：走 Agent 钱包（baw）
    if signal.get("route") == "wallet":
        res = _wallet_place(signal)
        if "error" in res:
            return res
        return place_report(res)
    # 交易所密钥通道：用界面绑定的密钥（settings 优先 / .env 兜底），不依赖 MCP/OAuth
    from cex_wallet import place_order as cex_place
    side = "BUY" if summary["direction"] in ("BULLISH", "做多") else "SELL"
    return place_report(cex_place(
        symbol=summary["symbol"],
        side=side,
        quantity=str(signal.get("quantity", "0.001")),
        price=str(summary["entry_price"]),
    ))


def place_report(res: dict) -> dict:
    """把 cex_wallet.place_order / 钱包下单结果归一化成下单单所需字段（orderId/symbol/status/error）。"""
    if res.get("status") == "ok":
        d = res.get("order", {})
        return {"orderId": d.get("orderId", "N/A"),
                "symbol": d.get("symbol", ""),
                "status": d.get("status", "NEW"),
                "executedQty": d.get("executedQty"),
                "fills_cnt": len(d.get("fills", []) or [])}
    return {"error": res.get("message") or res.get("detail") or res.get("output") or "下单失败"}


if __name__ == "__main__":
    print("=== Guarded Execution 测试模式 ===")
    test_signal = {
        "symbol": "BTCUSDT",
        "direction": "BULLISH",
        "price": 104500.0,
        "stop_loss": 102500.0,
        "take_profit": 108500.0,
        "quantity": "0.0005",
        "max_loss_usdt": 10.0,
    }
    result = confirm_and_place(test_signal, confirm=False)
    print(result["message"])
    print("summary:", result["summary"])
