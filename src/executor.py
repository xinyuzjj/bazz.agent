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


def confirm_and_place(signal: dict, confirm: bool = False) -> dict:
    """
    确认后下单流程
    confirm=True 时才真实下单
    """
    summary = {
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "entry_price": signal["price"],
        "stop_loss": signal.get("stop_loss"),
        "take_profit": signal.get("take_profit"),
        "max_loss_usdt": signal.get("max_loss_usdt"),
    }
    if not confirm:
        return {"status": "pending_confirm", "summary": summary,
                "message": "请人工确认：是否同意按上述参数下单？"}
    return place_limit_order(
        symbol=summary["symbol"],
        side="BUY" if summary["direction"] in ("BULLISH", "做多") else "SELL",
        quantity=str(signal.get("quantity", "0.001")),
        price=str(summary["entry_price"]),
    )


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
