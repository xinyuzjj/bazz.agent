"""Binance x402 / B402 机器支付客户端
官方文档: https://developers.binance.info/docs/products/onchainpay-x402/introduction
B402 Facilitator: /papi/v2/b402/{supported,verify,settle}（BNB Smart Chain）
说明：真实签名需要钱包私钥（web3）。本客户端在本地无密钥时提供「仿真」端到端流程，
     但会真实尝试访问 /supported 端点以展示线上配置；也可在配置 B402 生产权限后真实结算。
"""
import os
import time
import json
import uuid
import requests

# B402 Facilitator base（生产需申请；当前 BSC Testnet 开放）
B402_BASE = os.getenv("B402_BASE_URL", "https://api.binance.com")
B402_PATH = "/papi/v2/b402"
NETWORK = "BNB Smart Chain (BSC)"

# 支持的支付资产（BSC Mainnet 合约，来自官方文档）
SUPPORTED_ASSETS = {
    "USDT": "0x55d398326f99059fF775485246999027B3197955",
    "USDC": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
    "U":    "0xcE24439F2D9C6a2289F741120FE202248B666666",
    "USD1": "0x8d0D000Ee44948FC98c9B98A4FA4921476f08B0d",
}

_session = requests.Session()
_session.headers.update({"User-Agent": "AgentOS-BAZZ/1.0", "Content-Type": "application/json"})


def get_supported_configs() -> dict:
    """查询 B402 Facilitator 支持的支付配置（x402 v2 /supported）。"""
    try:
        r = _session.post(f"{B402_BASE}{B402_PATH}/supported", json={}, timeout=10)
        if r.status_code == 200:
            return {"status": "ok", "data": r.json()}
        return {"status": "error", "code": r.status_code, "detail": r.text[:300]}
    except Exception as e:
        return {"status": "unreachable", "detail": str(e)}


def build_payment_payload(asset: str = "USDT", amount: str = "0.10",
                          merchant: str = "0x9A2f3b7C4d1E8f0A6b5C3d2E1f0A9b8C7d6E5f40") -> dict:
    """构造 x402 v2 PaymentPayload（Permit2 形态的 EIP-712 离线授权）。
    注意：signature 为占位；真实场景由 Agentic Wallet 离线 EIP-712 签名。
    """
    asset = asset.upper()
    token = SUPPORTED_ASSETS.get(asset, SUPPORTED_ASSETS["USDT"])
    now = int(time.time())
    return {
        "x402Version": 2,
        "scheme": "exact",
        "network": "eip155:56",
        "payload": {
            "signature": "0x__SIMULATED_SIGNATURE__",
            "authorization": {
                "from": "0xAgentWalletDemo0000000000000000000000000000",
                "to": merchant,
                "value": "0",
                "validAfter": 0,
                "validBefore": now + 3600,
                "nonce": "0x" + uuid.uuid4().hex,
            },
            "permit2": {
                "token": token,
                "spender": "0xB402FacilitatorSpender0000000000000000000000",
                "amount": amount,
                "expiration": now + 3600,
                "sigDeadline": now + 3600,
            },
        },
    }


def verify_payment(payload: dict) -> dict:
    try:
        r = _session.post(f"{B402_BASE}{B402_PATH}/verify", json=payload, timeout=10)
        return {"status": "ok" if r.status_code == 200 else "error",
                "code": r.status_code, "detail": r.text[:300]}
    except Exception as e:
        return {"status": "unreachable", "detail": str(e)}


def settle_payment(payload: dict) -> dict:
    try:
        r = _session.post(f"{B402_BASE}{B402_PATH}/settle", json=payload, timeout=10)
        return {"status": "ok" if r.status_code == 200 else "error",
                "code": r.status_code, "detail": r.text[:300]}
    except Exception as e:
        return {"status": "unreachable", "detail": str(e)}


def demo_x402_flow(asset: str = "USDT", amount: str = "0.10") -> dict:
    """端到端演示 x402 的 402 流程（买家离线签名 + Facilitator 验证/结算）。"""
    merchant = "0x9A2f3b7C4d1E8f0A6b5C3d2E1f0A9b8C7d6E5f40"
    steps = []

    supported = get_supported_configs()
    steps.append({
        "step": 1,
        "title": "卖家返回 HTTP 402 支付要求",
        "detail": f"Merchant 要求 {amount} {asset}（{NETWORK}），通过 B402 Facilitator 结算。",
        "supported": supported,
    })

    payload = build_payment_payload(asset=asset, amount=amount, merchant=merchant)
    steps.append({
        "step": 2,
        "title": "买家离线签署 EIP-712 授权（无需 gas）",
        "detail": "Agent 本地构造 Permit2 授权并离线签名；真实场景由 Agentic Wallet 完成。",
        "payment_payload": payload,
    })

    verify = verify_payment(payload)
    steps.append({
        "step": 3,
        "title": "B402 Facilitator 验证签名",
        "detail": "Facilitator 离线校验 EIP-712 签名、余额与授权参数。",
        "verify_result": verify,
    })

    settle = settle_payment(payload)
    steps.append({
        "step": 4,
        "title": "链上结算（B402 代付 gas）",
        "detail": "Facilitator 将 stablecoin 从买家钱包直接转给卖家，点对点结算。",
        "settle_result": settle,
    })

    return {
        "flow": "simulated" if verify.get("status") != "ok" else "live",
        "network": NETWORK,
        "asset": asset,
        "amount": amount,
        "steps": steps,
        "payment_payload": payload,
        "verify_result": verify,
        "settle_result": settle,
    }


if __name__ == "__main__":
    import pprint
    pprint.pprint(demo_x402_flow())
