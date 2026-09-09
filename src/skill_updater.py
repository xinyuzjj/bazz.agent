"""v1.3.9：币安技能 + baw CLI 自动更新。

两部分：
  1) baw CLI（npm @binance/agentic-wallet，内置 runtime）—— 查 registry.npmjs.org latest，
     落后则用内置 node + npm 原地升级。注意：runtime/ 无 package.json，npm 会把「不在本次
     安装清单里」的包全部剪掉 —— baw 必须与 undici@6 同一条命令安装（fetch 代理补丁依赖）。
  2) Skills Hub 技能包（.agents/skills/<name>，npx skills add 装的）—— 对每个已装官方技能
     重新执行安装命令（覆盖 = 更新到最新版）。

自动策略：后端启动 45s 后首次检查；有更新自动应用；之后每 6h 循环一次。
状态持久化 workspace/.skill_update.json（上次检查/更新结果）；前端 /api/skills/updates 读
同一状态机，手动「检查更新 / 立即更新」与自动更新幂等复用（更新中重复触发直接忽略）。
代理池启用时环境变量已注入 —— npm 与版本检查自动走代理。
"""
import json
import os
import subprocess
import threading
import time

import requests

import skills_client
import wallet_runtime
import workspace

STATE_PATH = os.path.join(workspace.WORKSPACE, ".skill_update.json")
NPM_LATEST_URL = "https://registry.npmjs.org/@binance/agentic-wallet/latest"
CHECK_INTERVAL = 6 * 3600      # 自动检查周期
FIRST_DELAY = 45               # 启动后首次检查延迟（避开启动高峰，也让界面先就绪）
FRESH_TTL = 3600               # latest 缓存 1h 内直接复用（前端频繁轮询不打 npm）

_lock = threading.Lock()
_state = {
    "phase": "idle",           # idle | checking | updating | done | error
    "message": "",
    "baw": {"installed": "", "latest": "", "available": False},
    "skills": {"installed": [], "updated": [], "failed": [], "total": 0},
    "last_check": 0,
    "last_updated": 0,
}


def _persist():
    try:
        data = {k: _state[k] for k in ("baw", "skills", "last_check", "last_updated")}
        data["ts"] = time.time()
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError:
        pass


def _restore():
    """启动时恢复上次检查时间（避免每次启动都打 npm）。"""
    try:
        data = json.load(open(STATE_PATH, encoding="utf-8"))
        _state["last_check"] = float(data.get("last_check") or 0)
    except Exception:
        pass


def _baw_version() -> str:
    try:
        return (wallet_runtime.wallet_runtime_status() or {}).get("version", "") or ""
    except Exception:
        return ""


def _npm_latest() -> str:
    try:
        r = requests.get(NPM_LATEST_URL, timeout=8,
                         headers={"User-Agent": "BAZZ.AGENT-skill-updater"})
        if r.status_code == 200:
            return (r.json() or {}).get("version", "") or ""
    except Exception:
        pass
    return ""


def snapshot() -> dict:
    with _lock:
        return json.loads(json.dumps(_state))  # 深拷贝快照


def check(force: bool = False) -> dict:
    """检查更新（不安装）。force=True 绕过 1h 缓存强制打 npm。"""
    with _lock:
        if _state["phase"] in ("checking", "updating"):
            return json.loads(json.dumps(_state))
        _state["phase"] = "checking"
        _state["message"] = "正在检查最新版本…"

    installed = _baw_version()
    latest = _npm_latest() if (force or time.time() - _state["last_check"] > FRESH_TTL) \
        else _state["baw"].get("latest", "")
    with _lock:
        _state["baw"] = {"installed": installed, "latest": latest,
                         "available": bool(latest and installed and latest != installed)}
        _state["last_check"] = time.time()
        _state["phase"] = "idle"
        _state["message"] = ("发现新版本 baw v" + latest) if _state["baw"]["available"] \
            else ("baw 已是最新" if latest else "检查完成（npm 暂不可达，稍后自动重试）")
        _persist()
        return json.loads(json.dumps(_state))


def start_update(scope: str = "all") -> dict:
    """手动触发更新（scope: baw | skills | all）。更新中重复调用幂等忽略。"""
    with _lock:
        if _state["phase"] in ("checking", "updating"):
            return json.loads(json.dumps(_state))
        _state["phase"] = "updating"
        _state["message"] = "正在更新…"
        _state["skills"] = {"installed": [], "updated": [], "failed": [], "total": 0}
    threading.Thread(target=_run, args=(scope,), daemon=True).start()
    return snapshot()


def _run(scope: str):
    msg = []
    if scope in ("baw", "all"):
        ok, detail = _update_baw()
        msg.append(("baw 更新成功" if ok else "baw 更新失败") + (f"：{detail}" if detail else ""))
    if scope in ("skills", "all"):
        ok_s, n, fail = _update_skills()
        msg.append(f"技能包更新 {n} 个" + (f"，失败 {len(fail)} 个" if fail else ""))
    with _lock:
        _state["phase"] = "done" if all((ok, ok_s) if scope == "all" else (ok if scope == "baw" else ok_s)) else "error"
        _state["message"] = "；".join(msg) or "完成"
        _state["last_updated"] = time.time()
        if scope in ("baw", "all"):
            _state["baw"]["installed"] = _baw_version()
            _state["baw"]["available"] = bool(_state["baw"]["latest"] and
                                              _state["baw"]["installed"] != _state["baw"]["latest"])
        _persist()


def _update_baw() -> tuple:
    """baw 原地升级：runtime node + npm-cli.js。baw@latest 与 undici@6 必须同一条命令
    （runtime/ 无 package.json，npm 分两次装会互相剪包 —— v1.3.8 实测 74 包被剪）。"""
    node = workspace.NODE_EXE
    npmcli = os.path.join(workspace.RUNTIME_DIR, "node", "node_modules", "npm", "bin", "npm-cli.js")
    if not (os.path.isfile(node) and os.path.isfile(npmcli)):
        return False, "内置 runtime 缺 node/npm"
    cmd = [node, npmcli, "install", "--no-audit", "--no-fund", "--no-save",
           "--prefix", workspace.RUNTIME_DIR, "--loglevel=error",
           "@binance/agentic-wallet@latest", "undici@6"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if p.returncode == 0:
            return True, "v" + (_baw_version() or "?")
        return False, (p.stderr or p.stdout or "")[-200:]
    except Exception as e:
        return False, str(e)[:200]


def _update_skills() -> tuple:
    """已装技能逐个重装（= 更新）。install_skill 走 catalog 的 GitHub URL 覆盖安装。"""
    installed = skills_client.list_installed()
    official = [k for k in skills_client.SKILL_CATALOG if k in installed]
    updated, failed = [], []
    for k in official:
        r = skills_client.install_skill(k)
        (updated if r.get("status") == "ok" else failed).append(
            k if r.get("status") == "ok" else f"{k}({(r.get('stderr') or r.get('detail') or '')[-80:]})")
        with _lock:
            _state["skills"] = {"installed": official, "updated": list(updated),
                                "failed": list(failed), "total": len(official)}
    return (not failed), len(updated), failed


def ensure_background():
    """启动后台守护线程：首次延迟 45s 检查 → 有更新自动应用 → 每 6h 循环。"""
    _restore()

    def _loop():
        time.sleep(FIRST_DELAY)
        while True:
            try:
                snap = check()
                if snap["baw"]["available"]:
                    start_update("all")
            except Exception:
                pass
            time.sleep(CHECK_INTERVAL)

    threading.Thread(target=_loop, daemon=True).start()
