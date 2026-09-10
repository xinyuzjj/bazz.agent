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
    """Agent 钱包通道：走 baw 链上 DEX 兑换（非 MCP/OAuth）。
    v1.5.12 修复：原 `baw token swap` 是不存在的命令（baw CLI 无 token 组 →
    `unknown command 'token'`）；正确语法是 `baw market-order swap --fromTokenQty ...
    --fromToken <合约地址> --toToken <合约地址> --binanceChainId 56 --json`。
    且 DEX 链上只有 BNB/USDT 有已知合约地址——其他 CEX 币种（如 TRUMPUSDT）没有
    对应链上资产，给出明确指引让用户连 CEX，而不是发一条必败命令。"""
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
    if base not in ("BNB", "USDT"):
        return {"error": (f"Agent 钱包走链上 DEX 兑换，仅支持 BNB/USDT；要交易 {symbol}，"
                          "请在「设置 → 币安 CEX」绑定 API Key 后重试（Agent 会自动改走交易所通道）。")}
    bull = str(signal.get("direction") or "").upper() in ("BULLISH", "做多")
    usdt_bsc = "0x55d398326f99059fF775485246999027B3197955"
    bnb_bsc = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"
    if bull:
        amt = round(qty * price, 4)  # 买入：USDT→BNB，按 USDT 数量
        cmd = (f"baw market-order swap --fromTokenQty {amt} --fromToken {usdt_bsc} "
               f"--toToken {bnb_bsc} --binanceChainId 56 --json")
    else:
        cmd = (f"baw market-order swap --fromTokenQty {qty} --fromToken {bnb_bsc} "
               f"--toToken {usdt_bsc} --binanceChainId 56 --json")
    try:
        res = baw_run(cmd)
    except Exception as e:
        return {"error": str(e)[:300]}
    if res.get("status") == "ok":
        out = (res.get("stdout") or res.get("stderr") or "")
        # v1.5.12：orderId 只是「已提交」不是「已成交」——按 SKILL.md 轮询到终态再报告，
        # 避免把链上 FAILED（滑点/流动性）当成功。最多 3 次 × 8s，超时按 PENDING 如实报告。
        import json as _json, re as _re, time as _time
        oid = ""
        try:
            oid = str(((_json.loads(out).get("data") or {}).get("orderId"))) if out.strip().startswith("{") else ""
        except Exception:
            oid = ""
        if not oid:
            m = _re.search(r'"orderId"\s*:\s*"?(\w+)', out)
            oid = m.group(1) if m else ""
        final, status = out[-800:], "PENDING"
        if oid:
            for _ in range(3):
                _time.sleep(8)
                try:
                    chk = baw_run(f"baw market-order list --orderId {oid} --json")
                    body = chk.get("stdout") or ""
                    if chk.get("status") == "ok" and body:
                        final = body[-800:]
                        if '"FAILED"' in body or "'FAILED'" in body:
                            return {"error": f"链上兑换失败（orderId={oid}）：{body[:240]}"}
                        if '"FINISHED"' in body or "'FINISHED'" in body:
                            status = "FINISHED"
                            break
                except Exception:
                    break
        return {"orderId": oid or "WALLET", "symbol": symbol, "status": f"TRADE_{status}",
                "command": cmd, "output": final}
    err = (res.get("stderr") or res.get("detail") or res.get("stdout") or "钱包下单失败").strip()[:300]
    return {"error": err}


def _tracked(res: dict, signal: dict) -> dict:
    """v1.4.0：下单成功后登记订单跟踪（状态同步 + SL/TP 提醒），失败不影响下单结果。"""
    try:
        if not res.get("error"):
            from order_tracker import track_from_result
            track_from_result(res, signal)
    except Exception:
        pass
    return res


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
        "margin_usdt": signal.get("margin_usdt"),
        "leverage": signal.get("leverage"),
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
        return _tracked(place_report(res), signal)
    # 交易所密钥通道：用界面绑定的密钥（settings 优先 / .env 兜底），不依赖 MCP/OAuth
    from cex_wallet import place_order as cex_place
    side = "BUY" if summary["direction"] in ("BULLISH", "做多") else "SELL"
    return _tracked(place_report(cex_place(
        symbol=summary["symbol"],
        side=side,
        quantity=str(signal.get("quantity", "0.001")),
        price=str(summary["entry_price"]),
    )), signal)


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
