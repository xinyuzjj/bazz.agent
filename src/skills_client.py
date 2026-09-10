"""Binance Skills Hub 客户端 - 列出 / 安装 / 运行本地 Skills
Skills Hub: https://www.binance.com/en/skills
安装: npx skills add <github 目录 URL>
运行真实机制（重要）：
  - binance-agentic-wallet skill 驱动 baw CLI（npm @binance/agentic-wallet）
  - 多数 binance-web3 数据类技能自带 node <skill-dir>/scripts/cli.mjs <cmd> '<json_params>'
  - 少数为纯 HTTP / 依赖 baw 扩展（无本地脚本）→ 只能返回 SKILL.md 使用指引
  不存在 "npx skills run"（skills CLI 无 run 子命令）！
"""
import os
import re
import shlex
import shutil
import subprocess

import workspace

AGENTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".agents", "skills")


def _npx_cmd():
    """npx 解析：内置 runtime 的 npx.cmd 优先 —— 打包版用户机器没有 PATH node/npx，
    裸 `cmd /c npx` 在桌面版必失败（v1.3.9 修复）。回退 PATH 供 dev 使用。"""
    rt = os.path.join(workspace.RUNTIME_DIR, "node", "npx.cmd")
    if os.path.isfile(rt):
        return ["cmd", "/c", rt]
    return ["cmd", "/c", "npx"]


def _node_cmd():
    """node 解析：内置 runtime 的 node.exe 优先（打包版无 PATH node），回退 PATH。"""
    if os.path.isfile(workspace.NODE_EXE):
        return ["cmd", "/c", workspace.NODE_EXE]
    return ["cmd", "/c", "node"]

# Wallet Skills（官方 7 个，docs/products/wallet-skills/supported-skills）：
# 类别统一为 Read，不需要钱包连接；连接链上钱包（BX-）只是让 query-address-info 等直接用本机地址。
WALLET_SKILLS = {
    "query-token-info",                # 代币详情
    "query-token-audit",               # 代币安全审计
    "query-address-info",              # 地址持仓
    "crypto-market-rank",              # 市场排行榜
    "meme-rush",                       # Meme 发射台
    "trading-signal",                  # 逐笔聪明钱信号
    "binance-tokenized-securities-info",  # 代币化美股 RWA
}

# 完整 19 个官方 Skill（binance-skills-hub）
SKILL_CATALOG = {
    "academy-skill": {
        "group": "binance", "name": "academy-skill", "title": "币安学院风险教育",
        "desc": "检索学院官方教育内容（词汇/课程/Learn & Earn），仅教育不荐股",
        "url": "https://github.com/binance/binance-skills-hub/tree/main/skills/binance/academy-skill",
    },
    "binance": {
        "group": "binance", "name": "binance", "title": "币安交易 CLI",
        "desc": "现货 / U 本位合约 / 闪兑（需 auth）",
        "url": "https://github.com/binance/binance-skills-hub/tree/main/skills/binance/binance",
    },
    "fiat": {
        "group": "binance", "name": "fiat", "title": "法币支付能力",
        "desc": "查询国家 / 货币 / 支付方式 / 限额",
        "url": "https://github.com/binance/binance-skills-hub/tree/main/skills/binance/fiat",
    },
    "onchain-pay": {
        "group": "binance", "name": "onchain-pay", "title": "链上支付 Onchain Pay",
        "desc": "法币买币或发币至外部地址",
        "url": "https://github.com/binance/binance-skills-hub/tree/main/skills/binance/onchain-pay",
    },
    "p2p": {
        "group": "binance", "name": "p2p", "title": "P2P/C2C 助手",
        "desc": "广告查询 / 订单历史 / 发布管理",
        "url": "https://github.com/binance/binance-skills-hub/tree/main/skills/binance/p2p",
    },
    "payment": {
        "group": "binance", "name": "payment", "title": "payment-assistant · Binance Pay",
        "desc": "发送 / 接收加密支付（含 PIX）",
        "url": "https://github.com/binance/binance-skills-hub/tree/main/skills/binance/payment",
    },
    "square-post": {
        "group": "binance", "name": "square-post", "title": "币安广场发帖",
        "desc": "发布短文 / 图片 / 文章 / 视频",
        "url": "https://github.com/binance/binance-skills-hub/tree/main/skills/binance/square-post",
    },
    "binance-agentic-wallet": {
        "group": "binance-web3", "name": "binance-agentic-wallet", "title": "Agentic 钱包",
        "desc": "v1.11.0：扫码登录 / 余额 / 转账 / swap / 限价单 / approvals 授权风控 / prediction 预测市场 / DeFi 存提与 LP / contract-call / sign-message / x402（baw CLI）",
        "url": "https://github.com/binance/binance-skills-hub/tree/main/skills/binance-web3/binance-agentic-wallet",
    },
    "binance-leaderboard": {
        "group": "binance-web3", "name": "binance-leaderboard", "title": "链上排行榜",
        "desc": "Gem Hunter 分析与 PnL 排行",
        "url": "https://github.com/binance/binance-skills-hub/tree/main/skills/binance-web3/binance-leaderboard",
    },
    "binance-sports-ai-analyzer": {
        "group": "binance-web3", "name": "binance-sports-ai-analyzer", "title": "体育 AI 预测",
        "desc": "世界杯 AI 预测与新闻",
        "url": "https://github.com/binance/binance-skills-hub/tree/main/skills/binance-web3/binance-sports-ai-analyzer",
    },
    "binance-tokenized-securities-info": {
        "group": "binance-web3", "name": "binance-tokenized-securities-info", "title": "代币化美股",
        "desc": "Ondo 代币化美股 RWA 数据",
        "url": "https://github.com/binance/binance-skills-hub/tree/main/skills/binance-web3/binance-tokenized-securities-info",
    },
    "binance-trading-signal": {
        "group": "binance-web3", "name": "binance-trading-signal", "title": "链上交易信号",
        "desc": "聪明钱信号与自定义策略",
        "url": "https://github.com/binance/binance-skills-hub/tree/main/skills/binance-web3/binance-trading-signal",
    },
    "binance-wallet-tracker": {
        "group": "binance-web3", "name": "binance-wallet-tracker", "title": "钱包追踪",
        "desc": "聪明钱 / KOL 实时追踪",
        "url": "https://github.com/binance/binance-skills-hub/tree/main/skills/binance-web3/binance-wallet-tracker",
    },
    "crypto-market-rank": {
        "group": "binance-web3", "name": "crypto-market-rank", "title": "市场排行榜",
        "desc": "社交热度 / 趋势 / 聪明钱净流入",
        "url": "https://github.com/binance/binance-skills-hub/tree/main/skills/binance-web3/crypto-market-rank",
    },
    "meme-rush": {
        "group": "binance-web3", "name": "meme-rush", "title": "Meme 发射台",
        "desc": "Meme 实时 feed 与热点",
        "url": "https://github.com/binance/binance-skills-hub/tree/main/skills/binance-web3/meme-rush",
    },
    "query-address-info": {
        "group": "binance-web3", "name": "query-address-info", "title": "地址持仓",
        "desc": "单地址多链代币快照",
        "url": "https://github.com/binance/binance-skills-hub/tree/main/skills/binance-web3/query-address-info",
    },
    "query-token-audit": {
        "group": "binance-web3", "name": "query-token-audit", "title": "代币安全审计",
        "desc": "防 scam / honeypot 审计",
        "url": "https://github.com/binance/binance-skills-hub/tree/main/skills/binance-web3/query-token-audit",
    },
    "query-token-info": {
        "group": "binance-web3", "name": "query-token-info", "title": "代币详情",
        "desc": "搜索 / 元数据 / 行情 / K 线",
        "url": "https://github.com/binance/binance-skills-hub/tree/main/skills/binance-web3/query-token-info",
    },
    "trading-signal": {
        "group": "binance-web3", "name": "trading-signal", "title": "逐笔聪明钱信号",
        "desc": "BSC / Solana 买卖事件",
        "url": "https://github.com/binance/binance-skills-hub/tree/main/skills/binance-web3/trading-signal",
    },
}


def list_installed() -> list:
    if not os.path.isdir(AGENTS_DIR):
        return []
    return sorted([d for d in os.listdir(AGENTS_DIR) if os.path.isdir(os.path.join(AGENTS_DIR, d))])


# ---------------- v1.5.6：本地内置技能（非官方 catalog）+ 安装残留清理 ----------------

def _md_meta(skill_dir: str) -> tuple:
    """从 SKILL.md frontmatter 提取 (title, desc)：title 用 name 字段或目录名，desc 用 description 首段。"""
    name = os.path.basename(skill_dir.rstrip("\\/"))
    desc = ""
    md = os.path.join(skill_dir, "SKILL.md")
    try:
        txt = open(md, encoding="utf-8", errors="replace").read(4000)
        mt = re.search(r"^name:\s*(.+?)\s*$", txt, re.M)
        if mt:
            name = mt.group(1).strip() or name
        mdesc = re.search(r"^description:\s*\|?\s*\n((?:[ \t]+.+\n?)+)", txt, re.M)
        if mdesc:
            desc = " ".join(ln.strip() for ln in mdesc.group(1).splitlines())
            desc = re.sub(r"\s+", " ", desc)[:180]
    except OSError:
        pass
    return name, desc


# v1.5.6 本地内置技能（随应用打包分发，禁止通过 API 移除）
BUILTIN_SKILLS = {
    "market-data", "coin-report", "track-monitor",
    "risk-guard", "portfolio-review", "news-sentiment",
}


def builtin_skills() -> dict:
    """本地内置技能：.agents/skills 下有 SKILL.md 但不在官方 SKILL_CATALOG 的目录（随应用打包分发）。"""
    out = {}
    for d in list_installed():
        if d in SKILL_CATALOG or not os.path.isfile(os.path.join(AGENTS_DIR, d, "SKILL.md")):
            continue
        title, desc = _md_meta(os.path.join(AGENTS_DIR, d))
        out[d] = {
            "group": "builtin", "name": d, "title": title, "desc": desc or f"内置技能 {d}",
            "url": "", "kind": "builtin",
        }
    return out


def _cleanup_install_junk() -> None:
    """清理 skills CLI 安装/更新异常残留（v1.5.6）：
    1) .agents/skills 下的悬空 junction（更新时 skills CLI 把旧目录挪进 .agents/.agents/skills/
       再建 junction 指过去，下载失败（无代理）→ 商店为空、junction 悬空，git /打包读不到）
    2) 指向 .agents/.agents 嵌套商店的有效 junction（路径拼接 bug 产物）
    3) .agents/.agents 嵌套商店本体
    好目录 / 指向有效目标的普通 junction 一律不动。
    注意：junction 检测用 os.readlink（3.8+ 支持 junction，且目标缺失也能读出），
    不用 os.path.isjunction（3.12 才有，Python 3.11 打包版会静默失效）。"""
    def _islink(p: str) -> bool:
        try:
            os.readlink(p)
            return True
        except OSError:
            return False

    def _link_target(p: str) -> str:
        try:
            t = os.readlink(p)
        except OSError:
            return ""
        # junction 目标可能是相对路径（skills CLI 产物）→ 锚定父目录解析
        return t if os.path.isabs(t) else os.path.normpath(os.path.join(os.path.dirname(p), t))

    nested = os.path.join(os.path.dirname(AGENTS_DIR), ".agents")
    try:
        if os.path.isdir(nested):
            shutil.rmtree(nested, ignore_errors=True)   # 嵌套坏商店先清掉 → 指向它的 junction 全部悬空
        if not os.path.isdir(AGENTS_DIR):
            return
        for name in os.listdir(AGENTS_DIR):
            p = os.path.join(AGENTS_DIR, name)
            try:
                if not _islink(p):
                    continue                             # 正常目录/文件不动
                tgt = _link_target(p)
                if not tgt or not os.path.exists(tgt):
                    os.rmdir(p)                          # 悬空 junction → 只摘联接本身
            except OSError:
                pass
    except OSError:
        pass


_cleanup_install_junk()   # 应用每次启动（import）顺带清扫一次


def get_skill_info(skill_name: str) -> dict:
    info = dict(SKILL_CATALOG.get(skill_name, {}))
    info["installed"] = skill_name in list_installed()
    return info


# 本地 Hermes Agent 技能目录（环境变量 HERMES_AGENTS_DIR 可覆盖）—— 只读参考
HERMES_AGENTS_CANDIDATES = [
    os.path.expanduser("~/.hermes/skills"),
    os.path.expanduser("~/.hermes/agents"),
]


def _hermes_agent_root() -> str:
    for p in HERMES_AGENTS_CANDIDATES:
        if p and os.path.isdir(p):
            return p
    return os.environ.get("HERMES_AGENTS_DIR", "")


def get_bots() -> dict:
    """Bots/插件市场数据：Binance 官方技能（可装/移除）+ 用户 Hermes 本地 bots（只读参考）。"""
    installed = set(list_installed())
    market = []
    for k, v in SKILL_CATALOG.items():
        market.append({
            "id": k,
            "name": k,
            "title": v.get("title", k),
            "desc": v.get("desc", ""),
            "group": v.get("group", "binance"),
            "url": v.get("url", ""),
            "installed": k in installed,
            "wallet_skill": k in WALLET_SKILLS,
            "kind": "binance",
        })
    hermes = []
    root = _hermes_agent_root()
    if root:
        try:
            for n in sorted(os.listdir(root)):
                p = os.path.join(root, n)
                if not os.path.isdir(p) or n.startswith(".") or n.startswith("@"):
                    continue
                subs = [s for s in sorted(os.listdir(p))
                        if os.path.isdir(os.path.join(p, s)) and not s.startswith(".")]
                desc = ""
                md = os.path.join(p, "DESCRIPTION.md")
                if os.path.isfile(md):
                    try:
                        desc = "".join(open(md, encoding="utf-8").readlines()[:3]).strip()[:120]
                    except Exception:
                        desc = ""
                hermes.append({
                    "id": "hermes:" + n,
                    "name": n,
                    "title": n,
                    "desc": desc or f"{len(subs)} 个子技能",
                    "group": "hermes",
                    "bots": len(subs),
                    "path": p,
                    "installed": False,
                    "kind": "hermes-ref",
                })
        except Exception:
            hermes = []
    return {"market": market, "hermes": hermes, "hermes_root": root}


def get_catalog() -> dict:
    installed = set(list_installed())
    catalog = {}
    for k, v in SKILL_CATALOG.items():
        entry = dict(v)
        entry["installed"] = k in installed
        entry["wallet_skill"] = k in WALLET_SKILLS
        catalog[k] = entry
    # v1.5.6：本地内置技能（market-data / coin-report / track-monitor / risk-guard / portfolio-review / news-sentiment …）
    for k, v in builtin_skills().items():
        entry = dict(v)
        entry["installed"] = True
        entry["wallet_skill"] = False
        catalog[k] = entry
    return catalog


def install_skill(skill_key: str) -> dict:
    """通过 npx skills add 安装一个 Skill（官方 catalog key 或 GitHub URL 均可）。返回执行结果。
    注：Windows 下 npx/npm 是 .cmd 脚本，必须走 cmd /c（与 wallet_client.install_cli 同处理），否则 WinError 2。"""
    url = skill_key.strip()
    if not url.lower().startswith(("http://", "https://")):
        entry = SKILL_CATALOG.get(skill_key)
        if not entry:
            return {"status": "error", "detail": f"未找到 skill: {skill_key}"}
        url = entry["url"]
    try:
        # cwd 锚定 .agents 的父目录：打包态 AGENTS_DIR 在 _internal/.agents，若跟随进程
        # cwd（ScoutBackend/）会把技能装到后端读不到的位置（v1.3.9 修复）
        _cleanup_install_junk()   # 先清残留，避免旧 junction 干扰安装
        proc = subprocess.run(
            _npx_cmd() + ["skills", "add", url, "-y"],
            capture_output=True, text=True, timeout=240,
            cwd=os.path.dirname(os.path.abspath(AGENTS_DIR)),
        )
        r = {
            "status": "ok" if proc.returncode == 0 else "error",
            "skill": skill_key,
            "stdout": proc.stdout[-1500:],
            "stderr": proc.stderr[-1500:],
        }
        _cleanup_install_junk()   # 更新流程先删旧目录再下载，失败会留悬空 junction / 嵌套商店
        # v1.5.6：命令成功 ≠ 真实落地 —— 校验 SKILL.md 存在，否则报错引导检查网络/代理
        target = os.path.join(AGENTS_DIR, skill_key, "SKILL.md")
        if r["status"] == "ok" and not os.path.isfile(target):
            r = {"status": "error", "skill": skill_key,
                 "detail": "安装命令成功但技能目录未落地（常见原因：下载被网络拦截 / 更新中断）。"
                           "请检查代理池后重试；技能库会在下次启动时自动清理残留。"}
        return r
    except Exception as e:
        return {"status": "error", "detail": str(e)}


def _read_skill_guide(skill_name: str, limit: int = 60) -> str:
    """读取技能 SKILL.md 前几行作为使用指引（用于说明型/无本地脚本的技能）。"""
    md = os.path.join(AGENTS_DIR, skill_name, "SKILL.md")
    if not os.path.isfile(md):
        return f"(本地无 {skill_name}/SKILL.md)"
    try:
        with open(md, encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        return f"(读取 SKILL.md 失败: {e})"
    # 跳过 frontmatter 的 description 块，正文更有用
    start = 0
    for i, ln in enumerate(lines):
        if ln.strip() == "---" and i > 0:
            start = i + 1
            break
    return "".join(lines[start: start + limit]).strip()


# baw 扩展技能：leaderboard / wallet-tracker 是 baw CLI 内置子命令（v1.9.0+）
_BAW_SKILLS = {"binance-agentic-wallet", "binance-leaderboard", "binance-wallet-tracker"}


def _cli_uses_meta_url_dispatch(cli: str) -> bool:
    """官方数据类 cli.mjs 用 `import.meta.url === 'file://' + process.argv[1]` 判定直跑，
    Windows 下反斜杠路径永不相等 → 空输出 exit 0。检测到该模式就走通用 launcher。"""
    try:
        src = open(cli, "r", encoding="utf-8", errors="replace").read()
        return "import.meta.url" in src and "process.argv[1]" in src and "realpathSync" not in src
    except Exception:
        return False


def run_skill(skill_name: str, args: str = "") -> dict:
    """按真实机制运行已安装 skill。

    - baw 扩展技能（binance-agentic-wallet / binance-leaderboard / binance-wallet-tracker）
      → cmd /c baw <args>（leaderboard query / tracker token 等均为 baw 子命令）
    - 带 scripts/cli.mjs 的数据类技能 → cmd /c node <cli> <args>；
      若 cli.mjs 用 import.meta.url 判定直跑（Windows 下必失败）→ 改走 src/skill_launcher.mjs
      动态 import COMMANDS+call，绕开 dispatch 缺陷
    - 其余（纯 HTTP / 依赖 baw 扩展）→ 不本地执行，返回 SKILL.md 使用指引
    Windows 下 npx / baw / node 均需 cmd /c 包裹。
    """
    sdir = os.path.join(AGENTS_DIR, skill_name)
    cli = os.path.join(sdir, "scripts", "cli.mjs")
    arg_s = (args or "").strip()

    try:
        if skill_name in _BAW_SKILLS:
            # v1.2.11：baw 调用统一走 wallet_runtime.baw_invocation() 解析（内置 runtime 优先，回退 PATH）
            import wallet_runtime
            toks = shlex.split(arg_s, posix=True) if arg_s else ["wallet", "status"]
            base, mode = wallet_runtime.baw_invocation(toks)
            if mode == "missing":
                guide = _read_skill_guide(skill_name, limit=50)
                return {"status": "error", "skill": skill_name,
                        "stdout": "", "stderr": "baw 不可用：APP 内置 runtime 未就绪且 PATH 也未找到 baw。",
                        "returncode": 127, "note": "runtime_missing", "guide": guide}
        elif os.path.isfile(cli):
            if _cli_uses_meta_url_dispatch(cli):
                launcher = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skill_launcher.mjs")
                base = _node_cmd() + [launcher]
                toks = [sdir] + (shlex.split(arg_s, posix=True) if arg_s else [])
            else:
                base = _node_cmd() + [cli]
                toks = shlex.split(arg_s, posix=True) if arg_s else []
            if not toks:
                guide = _read_skill_guide(skill_name, limit=30)
                return {"status": "ok", "skill": skill_name,
                        "stdout": f"(技能 {skill_name} 需要子命令 + JSON 参数，用法见 SKILL.md 指引)\n\n{guide}",
                        "note": "usage_hint"}
        else:
            guide = _read_skill_guide(skill_name, limit=70)
            return {"status": "ok", "skill": skill_name,
                    "stdout": f"(技能 {skill_name} 为 HTTP/扩展型，无本地 cli.mjs — 以下为官方 SKILL.md 使用指引，请在有网真机按指引调用)\n\n{guide}",
                    "note": "api_reference"}
        proc = subprocess.run(["cmd", "/c"] + base + toks,
                              capture_output=True, text=True, timeout=120,
                              encoding="utf-8", errors="replace")  # node 输出 UTF-8，按 GBK 读会乱码
        # 广场发帖记账：手动「运行」发布的真实结果也落本地台账
        if skill_name == "square-post":
            try:
                import square_store
                square_store.record_from_run(skill_name, arg_s, proc.stdout or "", proc.returncode, via="manual")
            except Exception:
                pass
        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "skill": skill_name,
            "stdout": proc.stdout[-3000:],
            "stderr": proc.stderr[-1500:],
            "returncode": proc.returncode,
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}


def remove_skill(skill_key: str) -> dict:
    """移除一个本地已安装的 skill（删除 .agents/skills/<key> 目录）。"""
    key = (skill_key or "").strip()
    if not key or "/" in key or ".." in key or "\\" in key:
        return {"status": "error", "detail": "非法的 skill 名称"}
    if key in BUILTIN_SKILLS:
        return {"status": "error", "detail": f"{key} 是随应用分发的内置技能，不可移除"}
    target = os.path.join(AGENTS_DIR, key)
    if not os.path.isdir(target):
        return {"status": "error", "detail": f"未安装：{key}"}
    try:
        shutil.rmtree(target)
        return {"status": "ok", "skill": key}
    except Exception as e:
        return {"status": "error", "detail": str(e)[:300]}


if __name__ == "__main__":
    import pprint
    pprint.pprint({"installed": list_installed(), "catalog_count": len(SKILL_CATALOG)})
