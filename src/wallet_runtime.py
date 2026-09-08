"""v1.2.11：钱包 CLI（baw）调用路径解析。

解析策略：
  1) 优先用 APP 内置 runtime（resources/runtime/node/node.exe + @binance/agentic-wallet）
     —— 用户机器不需要任何 Node 环境，baw 开箱即用
  2) 内置 runtime 不存在（dev / 用户手动清空）→ 回退 PATH 里的 `baw` / `npx baw`
"""
import json
import os
import shutil

import workspace


def _resolve_baw_entry() -> str:
    """从 @binance/agentic-wallet/package.json 读 bin 字段，找到真正的 JS 入口。"""
    pkg = workspace.BAW_PACKAGE
    if not os.path.isfile(pkg):
        return ""
    try:
        data = json.load(open(pkg, encoding="utf-8"))
    except Exception:
        return ""
    pkg_dir = os.path.dirname(pkg)
    bin_field = data.get("bin", "")
    if isinstance(bin_field, str):
        cand = os.path.join(pkg_dir, bin_field)
        return cand if os.path.isfile(cand) else ""
    if isinstance(bin_field, dict):
        for name, rel in bin_field.items():
            if name == "baw" or "baw" in name.lower():
                cand = os.path.join(pkg_dir, rel)
                if os.path.isfile(cand):
                    return cand
    main = data.get("main", "")
    if main:
        cand = os.path.join(pkg_dir, main)
        if os.path.isfile(cand):
            return cand
    # 兜底：bin/baw.js（npm 包装最常见写法）
    cand = os.path.join(pkg_dir, "bin", "baw.js")
    return cand if os.path.isfile(cand) else ""


def baw_invocation(extra_args=None):
    """返回 (argv 前缀, 模式)。

    模式：
      "bundled" — 用内置 node + 内置 baw.js（最稳，开箱即用）
      "path"    — 用系统 PATH 的 baw（用户自己装了 npm i -g）
      "missing" — 两个都没有（前端应提示「未安装」）

    调用方拼命令时把 extra_args 接到后面即可：
      prefix, mode = baw_invocation()
      if mode == "missing": return {"ok": False, "error": "未安装..."}
      cmd = prefix + list(extra_args or [])
    """
    extra_args = list(extra_args or [])
    if workspace.runtime_available():
        entry = _resolve_baw_entry()
        if entry and os.path.isfile(entry) and os.path.isfile(workspace.NODE_EXE):
            return [workspace.NODE_EXE, entry] + extra_args, "bundled"
    # 回退 PATH
    if shutil.which("baw"):
        return ["baw"] + extra_args, "path"
    if shutil.which("npx") and workspace.runtime_available():
        # 极少情况：runtime 在但 entry 找不到，尝试 npx
        return ["npx", "--no-install", "baw"] + extra_args, "path"
    return [], "missing"


def wallet_runtime_status() -> dict:
    """给前端用的 runtime 状态报告。"""
    node_ok = os.path.isfile(workspace.NODE_EXE)
    baw_entry = _resolve_baw_entry() if node_ok else ""
    baw_ok = bool(baw_entry) and os.path.isfile(baw_entry)
    if node_ok and baw_ok:
        try:
            version = json.load(open(workspace.BAW_PACKAGE, encoding="utf-8")).get("version", "")
        except Exception:
            version = ""
        return {
            "bundled": True,
            "node": True,
            "baw": True,
            "version": version,
            "mode": "bundled",
        }
    # 回退 PATH
    return {
        "bundled": False,
        "node": shutil.which("node") is not None,
        "baw": shutil.which("baw") is not None,
        "version": "",
        "mode": "path" if shutil.which("baw") else "missing",
    }
