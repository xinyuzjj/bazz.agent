"""链上钱包（Binance Web3 Wallet API）—— 官方连接器桥封装

使用场景：用户持有 Binance Web3 Wallet API 凭据（API Key 形如 BX-… + Secret），
用于读 Binance 链上钱包/任意地址的跨链代币持仓。

实现方式：不自行实现 X-OC-* 签名（时间戳+方法+/build路径+body → HMAC-SHA256 → base64），
而是调用官方 @binance-web3/wallet（wallet_bridge/bridge.mjs），与官方客户端逐字节一致。
密钥仅存本地 sqlite settings，绝不落日志 / 不回明文（只回掩码）。
"""
import json
import os
import subprocess

import state

_BRIDGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "wallet_bridge", "bridge.mjs")

# ---------------- 密钥存取 ----------------

def _stored():
    k = (state.get_setting("W3_API_KEY", "") or "").strip()
    s = (state.get_setting("W3_API_SECRET", "") or "").strip()
    return k, s


def configured() -> bool:
    k, s = _stored()
    return bool(k and s)


def masked_key() -> str:
    k, _ = _stored()
    return f"{k[:6]}…{k[-4:]}" if k else ""


def save_keys(api_key: str, secret: str) -> None:
    state.set_setting("W3_API_KEY", (api_key or "").strip())
    state.set_setting("W3_API_SECRET", (secret or "").strip())


def clear_keys() -> None:
    state.set_setting("W3_API_KEY", "")
    state.set_setting("W3_API_SECRET", "")


# ---------------- 桥调用 ----------------

def _run_bridge(req: dict) -> dict:
    """执行 bridge.mjs，返回统一 dict。绝不把 secret 透出。"""
    try:
        p = subprocess.run(["node", _BRIDGE, json.dumps(req)],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=40)
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": "timeout", "message": "Web3 Wallet API 请求超时（40s）。"}
    except FileNotFoundError:
        return {"ok": False, "code": "no_node", "message": "未找到 node，无法调用官方连接器（需 Node ≥ 22）。"}
    except Exception as e:
        return {"ok": False, "code": "exec_exception", "message": str(e)[:300]}

    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    # 最后一行是结果 JSON（console 噪音忽略）
    lines = [ln for ln in out.splitlines() if ln.strip().startswith("{") and ln.strip().endswith("}")]
    if not lines:
        return {"ok": False, "code": "bridge_no_output",
                "message": f"bridge 无输出（{err[:200] or 'node 可能缺少依赖，请在 wallet_bridge 下 npm install'}）"}
    try:
        return json.loads(lines[-1])
    except Exception:
        return {"ok": False, "code": "bad_bridge_json", "message": out[-400:]}


def _signed_req(op: str, extra: dict) -> dict:
    k, s = _stored()
    if not k or not s:
        return {"ok": False, "code": "not_configured", "message": "尚未连接 Web3 Wallet API（BX- Key）。"}
    return _run_bridge({"op": op, "apiKey": k, "apiSecret": s, **extra})


# ---------------- 对外 API ----------------

def status() -> dict:
    return {"configured": configured(), "masked_key": masked_key()}


def connect(api_key: str, secret: str) -> dict:
    """连接并验证：用该凭据请求一次余额接口；网关鉴权不过会回 40101 等错误码。"""
    k = (api_key or "").strip()
    s = (secret or "").strip()
    if not k or not s:
        return {"status": "error", "code": "missing_keys", "message": "API Key（BX-…）与 Secret 均不能为空。"}
    if not k.startswith("BX-"):
        # 也给个软提示，但允许（官方网关按 key 内容校验）
        pass
    probe = _run_bridge({
        "op": "balance", "apiKey": k, "apiSecret": s,
        "address": "0x0000000000000000000000000000000000000001",
        "chains": ["56"], "page": 1, "pageSize": 1,
    })
    if not probe.get("ok"):
        return {"status": "error", "code": probe.get("code", "gateway"),
                "gateway": probe.get("gateway"),
                "message": _gateway_message(probe)}
    save_keys(k, s)
    return {"status": "ok", "configured": True, "masked_key": f"{k[:6]}…{k[-4:]}",
            "message": "Web3 Wallet API 已连接（网关鉴权通过，密钥仅存本机）。",
            "probe": probe.get("data")}


def balance(address: str, chains: list, page: int = 1, page_size: int = 50) -> dict:
    """查询指定链上地址持仓（可多链）。返回原始 OC data + 归一化摘要。"""
    if not (address or "").strip():
        return {"status": "error", "code": "no_address", "message": "需要链上地址（0x… / Solana 地址）。"}
    chains = chains or ["56"]
    r = _signed_req("balance", {
        "address": (address or "").strip(),
        "chains": [str(c) for c in chains],
        "page": int(page or 1), "pageSize": int(page_size or 50),
    })
    if not r.get("ok"):
        return {"status": "error", "code": r.get("code", "gateway"), "gateway": r.get("gateway"),
                "message": _gateway_message(r)}
    return {"status": "ok", "chains": [str(c) for c in chains], "data": r.get("data")}


def agent_addresses() -> dict:
    """从 Agentic Wallet（baw）取当前 MPC 钱包多链地址，供链上钱包直接填入。"""
    try:
        import wallet_client
    except Exception:
        wallet_client = None
    if wallet_client is None:
        return {"status": "error", "code": "no_wallet_client", "message": "wallet_client 不可用"}
    r = wallet_client.run_command("baw wallet address --json")
    if r.get("status") != "ok":
        return {"status": "error", "code": r.get("code", "baw_failed"),
                "message": r.get("detail") or r.get("stderr") or r.get("stdout") or "读取 Agent 钱包地址失败（需先扫码登录）。"}
    raw = (r.get("stdout") or "").strip()
    # baw wallet address --json 结构不固定，做宽松解析：找 data 列表/映射
    out = []
    try:
        j = json.loads(raw)
    except Exception:
        return {"status": "error", "code": "bad_json", "message": raw[:300]}
    # 兼容 {success:true,data:{chainId:address}} 与 {data:[{chainId,address}]}
    def walk(x):
        if isinstance(x, dict):
            if isinstance(x.get("address"), str):
                out.append({"chain": str(x.get("chain") or x.get("chainId") or "?"), "address": x["address"]})
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(j)
    # 去重
    seen, uniq = set(), []
    for row in out:
        if row["address"] not in seen:
            seen.add(row["address"]); uniq.append(row)
    if not uniq:
        return {"status": "error", "code": "no_address_found",
                "message": "Agent 钱包暂未返回地址（请先扫码登录）。原始输出：" + raw[:200]}
    return {"status": "ok", "addresses": uniq}


def _gateway_message(r: dict) -> str:
    code = r.get("code")
    msg = r.get("message") or ""
    if str(code) == "40101":
        return f"网关鉴权失败（40101 Invalid API Key）：Key 与 Secret 不匹配或已失效。"
    if str(code) == "40102":
        return f"签名无效（40102）：请核对 Secret（HMAC 签名用）是否与 Key 配对。"
    if str(code) == "40100" or str(code).startswith("401"):
        return f"网关拒绝（{code} {msg}）。"
    if r.get("gateway"):
        return f"Binance Web3 网关错误（{code}）：{msg}"
    return msg or f"请求失败（{code}）。"


if __name__ == "__main__":
    import json as _j
    print(_j.dumps(status(), ensure_ascii=False))
