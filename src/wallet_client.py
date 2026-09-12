"""Agentic Wallet 客户端 - 封装 baw CLI（Binance Agentic Wallet）

官方文档: https://developers.binance.com/en/docs/products/agentic-wallet/welcome
安装: npm i -g @binance/agentic-wallet   (要求 Node >= 18；当前配套 SKILL v1.11.0 / CLI v1.9.0)
特性: MPC 无密钥钱包、QR 扫码登录（Binance App）、多链（BSC / Ethereum / Base / Solana / Polygon / Arbitrum / Robinhood Chain）。

本模块只做状态探测与命令转发，绝不接触任何私钥；命令执行被限制为仅 baw 前缀。
以下是官方真实的命令树（v1.11.0，见 .agents/skills/binance-agentic-wallet/SKILL.md）：
  baw auth signin / signout / verify --qrCodeId <id>   扫码登录 / 登出 / 轮询校验
  baw wallet status / address / balance / chains        状态 / 多链地址 / 余额 / 链
  baw wallet settings / left-quota / tx-lock            钱包设置 / 每日额度 / 交易锁
  baw wallet send                                       向地址转账代币
  baw wallet tx-history --type pending|confirmed        交易历史（含 pending 筛选）
  baw wallet speed-up <txHash> / cancel <txHash>        加速 / 取消 pending 交易
  baw market-order quote / swap / list                  市价兑换报价 / 执行 / 列表
  baw limit-order buy / sell / list / cancel            限价单（条件单，如 "跌到 $Y 买"）
  baw approvals list / detail / revoke                  代币授权管理（风控）
  baw prediction category list / market list|detail|search|order-book|last-trade-price
  baw prediction position list|token|settled-history|pnl|portfolio    预测市场持仓/PnL
  baw prediction order history / trade quote|place-order|cancel|redeem 预测下单/兑奖
  baw defi protocol-list|info / investment-list|info / position        DeFi 协议/机会/持仓
  baw defi deposit / redeem / lp-add / lp-remove / claim / preview     DeFi 存/取/LP/领奖
  baw contract-call preview / execute                  外部合约调用（两步，devMode）
  baw sign-message preview / execute / result / history EIP-712 消息签名（两步）
  baw x402-payment preview / sign                       x402/B402 HTTP 402 机器支付
  （Campaign 限时活动 bStock 大赛见 references/campaign.md）
（所有命令均支持 --json 输出机器可读 JSON）
每日限额（Binance 设定）: 兑换 $50,000 / DeFi $100,000 / x402 $20。
"""
import json
import os
import shutil
import subprocess
import threading
import time

from skills_client import list_installed
import wallet_runtime

# 钱包状态缓存：baw CLI 冷启动极慢（单次 ~11s），缓存后秒回，避免前端假死。
_STATE_CACHE = {"ts": 0.0, "data": None}
_STATE_LOCK = threading.Lock()

# 官方真实 baw 命令树 v1.11.0（用于 UI 快捷按钮 / 文档；--json 为全局参数略去）
COMMAND_TREE = [
    {"group": "鉴权 Auth", "cmd": "baw auth signin", "desc": "扫 Binance App 二维码登录"},
    {"group": "鉴权 Auth", "cmd": "baw auth signout", "desc": "登出并清除会话"},
    {"group": "鉴权 Auth", "cmd": "baw auth verify --qrCodeId <id>", "desc": "校验扫码状态"},
    {"group": "钱包 Wallet", "cmd": "baw wallet status", "desc": "查看鉴权与钱包状态"},
    {"group": "钱包 Wallet", "cmd": "baw wallet address", "desc": "获取多链钱包地址"},
    {"group": "钱包 Wallet", "cmd": "baw wallet balance", "desc": "查询代币余额（含 USD 估值）"},
    {"group": "钱包 Wallet", "cmd": "baw wallet chains", "desc": "列出可用链"},
    {"group": "钱包 Wallet", "cmd": "baw wallet send", "desc": "向地址转账代币（需确认）"},
    {"group": "钱包 Wallet", "cmd": "baw wallet tx-history --type pending", "desc": "待确认/待上链交易"},
    {"group": "钱包 Wallet", "cmd": "baw wallet tx-history", "desc": "查询全部交易历史"},
    {"group": "钱包 Wallet", "cmd": "baw wallet speed-up <txHash>", "desc": "加速 pending 交易"},
    {"group": "钱包 Wallet", "cmd": "baw wallet cancel <txHash>", "desc": "取消 pending 交易"},
    {"group": "钱包 Wallet", "cmd": "baw wallet tx-lock", "desc": "查询交易锁（需二次确认）"},
    {"group": "钱包 Wallet", "cmd": "baw wallet settings", "desc": "查看钱包设置（devMode 等）"},
    {"group": "钱包 Wallet", "cmd": "baw wallet left-quota", "desc": "查看每日额度剩余"},
    {"group": "市价单 Market", "cmd": "baw market-order quote", "desc": "获取兑换报价（不执行）"},
    {"group": "市价单 Market", "cmd": "baw market-order swap", "desc": "执行市价兑换"},
    {"group": "市价单 Market", "cmd": "baw market-order list", "desc": "列出市价单"},
    {"group": "限价单 Limit", "cmd": "baw limit-order buy", "desc": "限价买入（条件单）"},
    {"group": "限价单 Limit", "cmd": "baw limit-order sell", "desc": "限价卖出（条件单）"},
    {"group": "限价单 Limit", "cmd": "baw limit-order list", "desc": "列出限价单"},
    {"group": "限价单 Limit", "cmd": "baw limit-order cancel", "desc": "取消限价单"},
    {"group": "授权 Approvals", "cmd": "baw approvals list", "desc": "列出代币授权（风控）"},
    {"group": "授权 Approvals", "cmd": "baw approvals detail", "desc": "查看授权详情与操作记录"},
    {"group": "授权 Approvals", "cmd": "baw approvals revoke", "desc": "撤销代币授权（需确认）"},
    {"group": "预测市场 Prediction", "cmd": "baw prediction category list", "desc": "预测市场分类"},
    {"group": "预测市场 Prediction", "cmd": "baw prediction market list", "desc": "浏览预测市场（加密/体育等）"},
    {"group": "预测市场 Prediction", "cmd": "baw prediction market order-book", "desc": "预测市场深度"},
    {"group": "预测市场 Prediction", "cmd": "baw prediction position list", "desc": "我的预测持仓"},
    {"group": "预测市场 Prediction", "cmd": "baw prediction position pnl", "desc": "预测 PnL 记录"},
    {"group": "预测市场 Prediction", "cmd": "baw prediction trade quote", "desc": "预测下单报价"},
    {"group": "预测市场 Prediction", "cmd": "baw prediction trade place-order", "desc": "下预测单（需确认）"},
    {"group": "预测市场 Prediction", "cmd": "baw prediction trade redeem", "desc": "兑付中奖预测"},
    {"group": "DeFi", "cmd": "baw defi protocol-list", "desc": "DeFi 协议（TVL/APY 排行）"},
    {"group": "DeFi", "cmd": "baw defi investment-list --investType Earn", "desc": "DeFi 机会（Earn/LP）"},
    {"group": "DeFi", "cmd": "baw defi position", "desc": "我的 DeFi 持仓（健康因子/LP/质押）"},
    {"group": "DeFi", "cmd": "baw defi deposit", "desc": "存入/质押/供应（需确认）"},
    {"group": "DeFi", "cmd": "baw defi redeem", "desc": "赎回/解押/提取（需确认）"},
    {"group": "DeFi", "cmd": "baw defi lp-add", "desc": "添加 LP 流动性（需确认）"},
    {"group": "DeFi", "cmd": "baw defi lp-remove", "desc": "移除 LP 流动性（需确认）"},
    {"group": "DeFi", "cmd": "baw defi claim", "desc": "领取 LP 费/奖励（需确认）"},
    {"group": "DeFi", "cmd": "baw defi preview", "desc": "DeFi 交易预览（不上链）"},
    {"group": "外部签名 External", "cmd": "baw contract-call preview", "desc": "合约调用预览（devMode 两步）"},
    {"group": "外部签名 External", "cmd": "baw contract-call execute --requestId <id>", "desc": "执行合约调用（需确认）"},
    {"group": "外部签名 External", "cmd": "baw sign-message preview", "desc": "EIP-712 消息签名预览"},
    {"group": "外部签名 External", "cmd": "baw sign-message execute --requestId <id>", "desc": "执行消息签名（需确认）"},
    {"group": "外部签名 External", "cmd": "baw sign-message result", "desc": "取回已确认签名"},
    {"group": "x402 支付", "cmd": "baw x402-payment preview", "desc": "解析 HTTP 402 付款方案"},
    {"group": "x402 支付", "cmd": "baw x402-payment sign", "desc": "签名选中的 402 付款（需确认）"},
    {"group": "活动 Campaign", "cmd": "baw wallet status", "desc": "Campaign/bStock 大赛：规则见 references/campaign.md（活动期内优先）"},
]

DAILY_CAPS = {
    "swap": "$50,000",
    "defi": "$100,000",
    "x402": "$20",
}


def cli_installed() -> bool:
    """CLI 是否可用：内置 runtime 优先，其次 PATH 里的 baw。"""
    return wallet_runtime.baw_invocation()[1] != "missing"


def npm_installed() -> bool:
    return shutil.which("npm") is not None


INSTALL_CMD = "npm i -g @binance/agentic-wallet"


def get_version() -> dict:
    if not cli_installed():
        return {"installed": False, "version": None,
                "detail": "未检测到 baw，请先 npm i -g @binance/agentic-wallet"}
    argv, mode = wallet_runtime.baw_invocation(["--version"])
    try:
        p = subprocess.run(["cmd", "/c"] + argv,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=5)
        return {"installed": True, "version": (p.stdout or p.stderr).strip()[:120],
                "bundled": mode == "bundled",
                "detail": None if p.returncode == 0 else (p.stderr or p.stdout).strip()[:200]}
    except subprocess.TimeoutExpired:
        return {"installed": True, "version": None, "bundled": mode == "bundled",
                "detail": "baw --version 超时（5s），CLI 疑似卡死，请检查安装或重装。"}
    except Exception as e:
        return {"installed": True, "version": None, "bundled": mode == "bundled",
                "detail": str(e)[:200]}


def get_status() -> dict:
    """读取钱包登录状态：一次 baw wallet status，从输出文本判断 Logged in / Not logged in。"""
    if not cli_installed():
        return {"connected": False, "detail": "baw 未安装"}
    argv, mode = wallet_runtime.baw_invocation(["wallet", "status"])
    try:
        p = subprocess.run(["cmd", "/c"] + argv,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=5)
        out = (p.stdout or p.stderr or "").strip()
        low = out.lower()
        not_logged = "not logged in" in low or "not logged" in low
        logged = ("logged in" in low or "已登录" in out) and not not_logged
        return {"connected": logged, "command": " ".join(argv),
                "output": out[-2000:],
                "bundled": mode == "bundled",
                "detail": None if logged else "已安装但未登录，请运行 baw auth signin 用 Binance App 扫码登录。"}
    except subprocess.TimeoutExpired:
        return {"connected": False, "bundled": mode == "bundled",
                "detail": "baw wallet status 超时（5s），CLI 疑似卡死。"}
    except Exception as e:
        return {"connected": False, "bundled": mode == "bundled", "detail": str(e)[:200]}


def run_command(cmd: str) -> dict:
    """仅允许以 baw 开头的命令，避免任意代码执行。"""
    cmd = (cmd or "").strip()
    if not cmd:
        return {"status": "error", "code": "empty_cmd", "detail": "空命令"}
    if not cmd.startswith("baw "):
        return {"status": "error", "code": "forbidden_prefix", "detail": "仅允许 baw 命令"}
    # 预检：未安装就给出明确中文提示，避免泄露底层 WinError 2 / FileNotFoundError 等。
    if not cli_installed():
        return {"status": "error", "code": "baw_not_installed",
                "detail": "未检测到 baw CLI。请先运行「npm i -g @binance/agentic-wallet」安装（要求 Node ≥ 18），再点击刷新。",
                "install_cmd": INSTALL_CMD}
    try:
        parts = cmd.split()
        argv, mode = wallet_runtime.baw_invocation(parts[1:])
        # Windows 下 .CMD 必须走 cmd /c（shell=True 会引入用户输入注入风险）
        p = subprocess.run(["cmd", "/c"] + argv, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        return {"status": "ok" if p.returncode == 0 else "error",
                "code": "ok" if p.returncode == 0 else "non_zero_exit",
                "returncode": p.returncode,
                "bundled": mode == "bundled",
                "stdout": p.stdout[-12000:], "stderr": p.stderr[-4000:]}
    except Exception as e:
        return {"status": "error", "code": "exec_exception", "detail": str(e)[:300]}


def install_cli() -> dict:
    """一键安装 baw：npm i -g @binance/agentic-wallet（要求本机有 npm + Node ≥ 18）。
    若 APP 已内置 runtime，直接返回已就绪，不再重复安装。"""
    _, mode = wallet_runtime.baw_invocation()
    if mode == "bundled":
        return {"status": "ok", "code": "already_bundled",
                "detail": "v1.2.11+ 已内置 Node 20 + baw CLI，无需安装。请直接点「扫码登录 Agent 钱包」。",
                "install_cmd": INSTALL_CMD}
    if not npm_installed():
        return {"status": "error", "code": "npm_not_installed",
                "detail": "未检测到 npm（请先安装 Node.js ≥ 18）。"}
    if cli_installed():
        return {"status": "ok", "code": "already_installed",
                "detail": "baw 已安装，无需重复安装。", "install_cmd": INSTALL_CMD}
    try:
        # Windows 下 npm 是 .cmd 脚本，直接传 argv 经常报 WinError 2；用 shell=True 走 cmd 解析
        p = subprocess.run(INSTALL_CMD, capture_output=True, text=True, timeout=300, shell=True,
                           encoding="utf-8", errors="replace")
        if p.returncode == 0:
            return {"status": "ok", "code": "installed",
                    "detail": "安装成功。", "install_cmd": INSTALL_CMD,
                    "stdout": (p.stdout or "")[-1500:]}
        return {"status": "error", "code": "npm_install_failed",
                "detail": "npm 安装失败（通常网络或权限问题）。",
                "stderr": (p.stderr or p.stdout or "")[-1500:]}
    except Exception as e:
        return {"status": "error", "code": "exec_exception", "detail": str(e)[:300]}


# ---------------- Agent 钱包扫码登录（baw auth 真实流程） ----------------
# 流程：signin → 前端展示 urlForWeb 二维码 + pairingCode → Binance App 扫码确认
#      → 前端轮询 verify(qrCodeId) 直到 SUCCESS → wallet status 变 connected。
# baw 使用本地会话文件，并发调用会互相踩，因此全部串行（_BAW_LOCK）。

_BAW_LOCK = threading.Lock()


def _baw_json(parts: list, timeout: int) -> dict:
    """执行一条 baw 命令（含 --json），统一返回结构：
    {status:'ok', json} | {status:'error', code, message, ...}
    parts 以 'baw' 开头；实际 argv 通过 wallet_runtime.baw_invocation 解析（内置 runtime 优先）。"""
    try:
        argv, mode = wallet_runtime.baw_invocation(parts[1:])
        p = subprocess.run(["cmd", "/c"] + argv, capture_output=True,
                           text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"status": "error", "code": "timeout",
                "message": f"baw {' '.join(parts[2:])} 超时（>{timeout}s）。"}
    except Exception as e:
        return {"status": "error", "code": "exec_exception", "message": str(e)[:300]}

    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()

    if p.returncode != 0:
        # 官方错误以 JSON 形式出现在 stdout（如 NETWORK_ERROR / AUTH_REJECTED）
        for blob in (out, err):
            if not blob:
                continue
            try:
                j = json.loads(blob)
                e = j.get("error") or {}
                return {"status": "error",
                        "code": str(e.get("code") or e.get("name") or "non_zero"),
                        "name": e.get("name"),
                        "message": str(e.get("message") or blob[:400])}
            except Exception:
                continue
        return {"status": "error", "code": "non_zero_exit",
                "returncode": p.returncode, "message": (err or out)[-600:]}
    try:
        return {"status": "ok", "json": json.loads(out)}
    except Exception:
        return {"status": "error", "code": "bad_json",
                "message": f"无法解析 baw 输出：{out[:400]}"}


def signin() -> dict:
    """发起 Binance App 扫码登录：baw auth signin --json。

    返回 {status:'ok', connected:True, already:True}（已登录）
    或 {status:'ok', connected:False, urlForWeb, qrCodeId, pairingCode, expireAt}
    或 {status:'error', code, name, message, install_cmd?}
    """
    if not cli_installed():
        return {"status": "error", "code": "baw_not_installed",
                "message": "未检测到 baw CLI。请先安装 @binance/agentic-wallet。",
                "install_cmd": INSTALL_CMD}
    with _BAW_LOCK:
        r = _baw_json(["baw", "auth", "signin", "--json"], 60)
    if r["status"] != "ok":
        return r
    d = r["json"].get("data") or {}
    st = (d.get("status") or "").upper()
    if st == "ALREADY_CONNECTED":
        return {"status": "ok", "connected": True, "already": True,
                "message": "已在登录状态，无需重复扫码。"}
    url = d.get("urlForWeb") or ""
    qid = d.get("qrCodeId") or ""
    pc = d.get("pairingCode") or ""
    if not url or not qid:
        return {"status": "error", "code": "missing_fields",
                "message": f"signin 响应缺少 urlForWeb/qrCodeId：{json.dumps(d, ensure_ascii=False)[:300]}"}
    return {"status": "ok", "connected": False, "urlForWeb": url,
            "qrCodeId": qid, "pairingCode": pc, "expireAt": d.get("expireAt")}


def verify(qr_code_id: str, wait: int = 10) -> dict:
    """校验扫码状态：baw auth verify --qrCodeId <id> --json。

    该命令会阻塞到 App 确认或超时；每次轮询给 wait 秒预算，
    超时返回 {status:'ok', pending:True}，由前端继续轮询。
    """
    if not cli_installed():
        return {"status": "error", "code": "baw_not_installed",
                "message": "未检测到 baw CLI。", "install_cmd": INSTALL_CMD}
    qid = (qr_code_id or "").strip()
    if not qid:
        return {"status": "error", "code": "empty_qrcode", "message": "缺少 qrCodeId。"}
    with _BAW_LOCK:
        r = _baw_json(["baw", "auth", "verify", "--qrCodeId", qid, "--json"],
                      max(3, min(int(wait), 30)))
    if r["status"] == "error":
        if r.get("code") == "timeout":
            return {"status": "ok", "pending": True,
                    "message": "等待 Binance App 确认…"}
        return r
    d = r["json"].get("data") or {}
    st = (d.get("status") or "").upper()
    if st == "SUCCESS":
        return {"status": "ok", "pending": False, "success": True,
                "message": "扫码登录成功，钱包已就绪。"}
    return {"status": "ok", "pending": True,
            "message": f"当前状态：{st or '等待确认…'}"}


def signout() -> dict:
    """退出登录并清除本地会话。"""
    if not cli_installed():
        return {"status": "error", "code": "baw_not_installed",
                "message": "未检测到 baw CLI。", "install_cmd": INSTALL_CMD}
    with _BAW_LOCK:
        r = _baw_json(["baw", "auth", "signout", "--json"], 30)
    if r["status"] != "ok":
        return r
    d = r["json"].get("data") or {}
    return {"status": "ok",
            "message": "已退出登录。" if (d.get("status") or "").upper() == "LOGGED_OUT"
            else f"退出指令已执行（{d.get('status') or 'ok'}）。"}


def qr_svg(text: str, box: int = 6) -> dict:
    """把一段文本渲染成二维码 SVG（供前端 <img> 直接展示登录二维码）。"""
    if not (text or "").strip():
        return {"status": "error", "code": "empty_text", "message": "缺少二维码内容。"}
    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage
    except ImportError:
        return {"status": "error", "code": "no_qrcode",
                "message": "qrcode 库未安装（pip install qrcode）。"}
    try:
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                           box_size=int(box) if box else 6, border=2)
        qr.add_data(text)
        qr.make(fit=True)
        svg = qr.make_image(image_factory=SvgPathImage).to_string().decode("utf-8")
        return {"status": "ok", "svg": svg}
    except Exception as e:
        return {"status": "error", "code": "qr_fail", "message": str(e)[:200]}


def _compute_wallet_state() -> dict:
    """并行探测版本/登录/npm，避免串行等待 baw 冷启动造成前端假死。"""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as ex:
        fv = ex.submit(get_version)
        fs = ex.submit(get_status)
        fn = ex.submit(npm_installed)
        try:
            v = fv.result(timeout=7)
        except Exception as e:
            v = {"installed": False, "detail": f"get_version 异常: {e}"}
        try:
            s = fs.result(timeout=7)
        except Exception as e:
            s = {"connected": False, "detail": f"get_status 异常: {e}"}
        try:
            npm_ok = fn.result(timeout=3)
        except Exception:
            npm_ok = False
    return {
        "cli": v,
        "status": s,
        "commands": COMMAND_TREE,
        "daily_caps": DAILY_CAPS,
        "install_cmd": INSTALL_CMD,
        "npm_available": npm_ok,
        "agentic_skill_installed": "binance-agentic-wallet" in list_installed(),
    }


def get_wallet_state(force: bool = False) -> dict:
    """带 30s TTL 的缓存；force=True 时强制重算（用于「刷新状态」）。"""
    now = time.time()
    cached = _STATE_CACHE["data"]
    if not force and cached is not None and (now - _STATE_CACHE["ts"]) < 30:
        return cached
    data = _compute_wallet_state()
    with _STATE_LOCK:
        _STATE_CACHE["ts"] = now
        _STATE_CACHE["data"] = data
    return data


def warm_wallet_cache() -> None:
    """后台预热：进程启动即触发一次探测，用户打开钱包页时已命中缓存（秒回）。"""
    try:
        get_wallet_state(force=True)
    except Exception:
        pass
