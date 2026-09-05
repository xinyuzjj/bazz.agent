"""币安交易 CLI（binance-cli）——真实下单执行通道的本地管理模块

与 cex_wallet（只读 GET /api/v3/account）共用同一把交易所 HMAC Key：
在「币安 CEX」面板填入 Key/Secret 且校验通过后，同步生成本地 binance-cli
profile（--env prod，--select 置为当前激活），使 Agent / 命令行可真实执行
现货 / USDT 本位合约 / 闪兑等交易。

安全约定：
  - 交易一律 confirm-before-execute（真实下单前必须让用户输入 CONFIRM）；
  - 本模块绝不打印 / 落盘明文密钥（密钥仅存在于 binance-cli 自己的 profile 存储）；
  - Windows 上 binance-cli 是 npm 生成的 .cmd 包装器，subprocess 必须经 cmd /c 启动，
    否则 WinError 2（与 npx 同理）。
"""
import os
import shutil
import subprocess
from typing import Optional, Tuple

PROFILE = "main"
ENV = "prod"
_BIN = "binance-cli"
_IS_WIN = os.name == "nt"


def _found() -> bool:
    return shutil.which(_BIN) is not None


def _run(args: list, timeout: int = 120) -> Tuple[int, str, str]:
    """运行 binance-cli 子命令，返回 (returncode, stdout, stderr)。"""
    if not _found():
        return 127, "", f"{_BIN} not found in PATH"
    cmd = ["cmd", "/c", _BIN, *args] if _IS_WIN else [_BIN, *args]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except FileNotFoundError:
        return 127, "", f"{_BIN} not found in PATH"
    except subprocess.TimeoutExpired:
        return 124, "", f"{_BIN} 超时（>{timeout}s）"


def version() -> str:
    code, out, err = _run(["--version"], timeout=30)
    return (out or err).strip().splitlines()[0][:40] if code == 0 else ""


def available() -> dict:
    """binance-cli 是否已安装 + 版本。"""
    if not _found():
        return {"installed": False, "version": "", "error": "未检测到 binance-cli（npm i -g @binance/binance-cli）"}
    v = version()
    return {"installed": True, "version": v, "error": ""}


def profile_status() -> dict:
    """当前 profile 是否就绪（本地存在 main/prod profile）。"""
    base = available()
    if not base["installed"]:
        return {**base, "profile": "", "env": ""}
    code, out, err = _run(["profile", "list"], timeout=30)
    listed = (out or "") + (err or "")
    exists = PROFILE in listed
    return {**base, "profile": PROFILE if exists else "", "env": ENV if exists else ""}


def sync_profile(api_key: str, secret: str, env: str = ENV) -> dict:
    """用同一把交易所 HMAC Key 创建 / 覆盖本地交易 profile（main · env）。

    注意：secret 只作为 CLI 参数传入由其自行落盘，本函数不打印、不保存明文。
    """
    if not api_key or not secret:
        return {"ok": False, "error": "缺少 API Key / Secret"}
    if not _found():
        return {"ok": False, "error": "binance-cli 未安装，无法同步交易 profile"}
    # --force 覆盖同名旧 profile；--select 置为当前激活 profile
    code, out, err = _run([
        "profile", "create",
        "--name", PROFILE,
        "--env", env,
        "--api-key", api_key,
        "--api-secret", secret,
        "--force",
        "--select",
    ], timeout=90)
    if code != 0:
        msg = (err or out).strip().splitlines()
        return {"ok": False, "error": (msg[-1][:200] if msg else f"exit {code}")}
    return {"ok": True, "profile": PROFILE, "env": env}


def remove_profile() -> dict:
    if not _found():
        return {"ok": True}
    code, out, err = _run(["profile", "delete", "--names", PROFILE], timeout=60)
    return {"ok": code == 0, "error": (err or out).strip()[:200] if code else ""}


if __name__ == "__main__":
    import json
    print(json.dumps({"available": available(), "profile": profile_status()}, ensure_ascii=False, indent=2))
