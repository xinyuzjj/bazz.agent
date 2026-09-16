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
    """保护单：止盈 + 止损 OCO（二选一成交，另一腿自动撤销）。

    v1.6.5（OPT-09）**修 bug + 改走统一下单链路**。原实现有两个问题：
      1. `legs=[{...},{...}]` 不是 Binance 的入参格式 —— `/api/v3/order/oco` 收的是扁平参数
         （price / stopPrice / stopLimitPrice / stopLimitTimeInForce），传 legs 会因缺必填参数被拒；
      2. 它自建了一套 BASE_URL + .env 密钥，与界面绑定的密钥（cex_wallet）是两条路，
         用户没在 .env 里配 Key 时它永远返回「缺少 API Key」。
    现在委托给 `cex_wallet.place_oco`：一套密钥、一套交易规则取整、一套错误归因。
    旧签名保持兼容（price=止盈价 / stop_price=止损触发价 / limit_price=止损腿限价）。
    """
    from cex_wallet import place_oco as _oco
    return _oco(symbol=symbol, side=side, quantity=quantity, take_profit=price,
                stop_price=stop_price, stop_limit_price=limit_price)


def cancel_order(symbol: str, order_id: str) -> dict:
    """撤单（v1.6.5 OPT-09）：委托 cex_wallet（界面绑定的密钥）。
    注意与「停止本地跟踪」是两件事 —— 撤单才会真正把挂单从交易所摘掉。"""
    from cex_wallet import cancel_order as _cancel
    return _cancel(str(symbol).upper(), str(order_id))


def cancel_open_orders(symbol: str) -> dict:
    """撤掉某标的全部挂单（v1.6.5 OPT-09）。"""
    from cex_wallet import cancel_open_orders as _cancel_all
    return _cancel_all(str(symbol).upper())


# 保护单止损距离：与仓位模拟口径**一致**（radar_tracker.FAIL_HIT = 10.0，10x 近强平线）。
# 硬约束（用户明确要求）：止损 10% 与 10x 杠杆都不动，所以这里刻意不暴露成可调参数。
PROTECT_STOP_PCT = 10.0


def place_protective(signal: dict, fill_price: float = 0.0) -> dict:
    """下单即挂保护单（v1.6.5 OPT-09）：现货 OCO，止损 −10%（与模拟口径一致），
    止盈取 signal.take_profit（缺省按顺向 +25%，与雷达 GAIN_HIT 同源）。

    只在**人工确认过**的入场之后调用（由 confirm_and_place 触发），且要求 signal 显式带
    `protect=True` —— 默认不开，因为这是一张真金白银挂在交易所的委托单。
    失败一律返回 error 字典、绝不抛异常：保护单挂不上不该把已成交的入场判成失败。
    """
    sym = str(signal.get("symbol") or "").upper()
    try:
        qty = str(signal.get("quantity") or signal.get("executed_qty") or "")
        entry = float(fill_price or signal.get("price") or 0)
        if not sym or not qty or float(qty) <= 0 or entry <= 0:
            return {"error": f"保护单参数不完整（symbol={sym or '—'} qty={qty or '—'} entry={entry or '—'}）",
                    "orderId": "", "symbol": sym, "status": "PROTECT_FAILED"}
        bull = str(signal.get("direction") or "").upper() in ("BULLISH", "做多", "LONG", "BUY")
        tp = float(signal.get("take_profit") or 0)
        if tp <= 0:
            tp = entry * (1 + 0.25) if bull else entry * (1 - 0.25)
        if bull:
            stop = entry * (1 - PROTECT_STOP_PCT / 100.0)
            stop_lim = stop * 0.995          # 止损腿限价再让 0.5%，避免触发后因滑点挂不上
        else:
            stop = entry * (1 + PROTECT_STOP_PCT / 100.0)
            stop_lim = stop * 1.005
        res = place_oco_order(symbol=sym, side="SELL" if bull else "BUY", quantity=qty,
                              price=str(round(tp, 10)),
                              stop_price=str(round(stop, 10)),
                              limit_price=str(round(stop_lim, 10)))
        if res.get("error"):
            return {"error": res["error"], "orderId": "", "symbol": sym,
                    "status": "PROTECT_FAILED"}
        d = res.get("order") or {}
        return {"orderId": str(d.get("orderListId") or d.get("orderId") or "N/A"),
                "symbol": sym, "status": "PROTECTED",
                "stop_price": round(stop, 10), "take_profit": round(tp, 10),
                "side": "SELL" if bull else "BUY", "raw": d}
    except Exception as e:
        return {"error": f"保护单异常：{e.__class__.__name__}", "orderId": "", "symbol": sym,
                "status": "PROTECT_FAILED"}


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
        # v1.6.5（OPT-09）：确认框里就要看见「会不会顺手挂保护单」，否则用户不知道
        # 点确认之后还有第二张单会发出去。
        "protect": bool(signal.get("protect")),
        "protect_stop_pct": PROTECT_STOP_PCT if signal.get("protect") else None,
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
    res = _tracked(place_report(cex_place(
        symbol=summary["symbol"],
        side=side,
        quantity=str(signal.get("quantity", "0.001")),
        price=str(summary["entry_price"]),
    )), signal)
    # v1.6.5（OPT-09）：下单即挂保护单（止损 −10%，与模拟口径一致）。
    # 仅当 signal 显式带 protect=True —— 默认不开，因为这是真挂在交易所的委托单。
    # 失败只附注不改变下单结果：入场已成交，保护单挂不上不该被判成下单失败。
    if signal.get("protect") and not res.get("error"):
        prot = place_protective(signal)
        res["protective"] = prot
        if prot.get("error"):
            res["protect_warning"] = f"保护单未挂上：{prot['error']}（请手动挂止损）"
    return res


def place_report(res: dict) -> dict:
    """把 cex_wallet.place_order / 钱包下单结果归一化成下单单所需字段（orderId/symbol/status/error）。
    v1.5.28（F02 修复）：此前只认 cex 通道的 status=="ok"，钱包通道返回的
    TRADE_FINISHED（已成交）/ TRADE_PENDING（待确认）全部误报成「下单失败」，
    诱导用户/Agent 重复下单。现按 orderId 有无 + 状态映射归一：
    TRADE_FINISHED→FILLED；TRADE_PENDING→PENDING（待查证，不自动重试）。"""
    st = str(res.get("status") or "").upper()
    if st == "OK":
        d = res.get("order", {})
        return {"orderId": d.get("orderId", "N/A"),
                "symbol": d.get("symbol", ""),
                "status": d.get("status", "NEW"),
                "executedQty": d.get("executedQty"),
                "fills_cnt": len(d.get("fills", []) or [])}
    if res.get("orderId") or st in ("TRADE_FINISHED", "TRADE_PENDING"):
        mapped = "FILLED" if st in ("TRADE_FINISHED", "FINISHED") else "PENDING"
        return {"orderId": str(res.get("orderId") or "N/A"),
                "symbol": str(res.get("symbol") or ""),
                "status": mapped,
                "raw_status": st or "UNKNOWN",       # 保留原始状态便于审计/排障
                "executedQty": res.get("executedQty"),
                "fills_cnt": 0,
                "command": res.get("command"),
                "output": res.get("output")}
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
