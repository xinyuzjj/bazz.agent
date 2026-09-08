"""BAZZ.AGENT 更新检查模块（v1.2.11 起：只检查版本，不下载/不替换）。

设计变更：v1.2.10 及之前的「自动下载 zip → 整目录替换 → 重启」方案在中国网络环境
下多次失败（GitHub API 403 / 代理 / PS 脚本杀进程 / 解压权限等），被整体废弃。

新版只做一件事：拿 GitHub Releases latest 的版本号与 release page URL，
前端展示「已是最新 / 有新版本 / 检查失败」三态，**让用户自己点按钮去浏览器下载页**。

版本源：
  · 本地：<APP_DIR>/BAZZ_VERSION.txt（打包时 build-desktop.js 写入；冻结态 = exe 所在目录）
          dev 回退到 <APP_DIR>/package.json 的 version 字段
  · 远端：GitHub Releases latest（xinyuzjj/bazz.agent）
"""
import json
import os
import re

try:
    import requests
except Exception:  # 极端情况缺失时仅在调用处报错
    requests = None

import workspace

GITHUB_REPO = "xinyuzjj/bazz.agent"
GITHUB_API = os.environ.get("BAZZ_GH_API") or "https://api.github.com"
RELEASE_URL = f"{GITHUB_API}/repos/{GITHUB_REPO}/releases/latest"
VERSION_FILE = "BAZZ_VERSION.txt"


def _ver_tuple(v: str):
    m = re.match(r"v?(\d+)\.(\d+)(?:\.(\d+))?", str(v or "").strip())
    if not m:
        return (0, 0, 0)
    return tuple(int(x) for x in m.groups(default="0"))


def _local_version() -> str:
    """本地当前版本：BAZZ_VERSION env > BAZZ_VERSION.txt > package.json。"""
    v = os.environ.get("BAZZ_VERSION", "").strip()
    if v:
        return v.lstrip("v")
    for cand in (os.path.join(workspace.APP_DIR, VERSION_FILE),
                 os.path.join(os.path.dirname(workspace.APP_DIR), VERSION_FILE)):
        try:
            if os.path.isfile(cand):
                s = open(cand, encoding="utf-8").read().strip()
                if s:
                    return s.lstrip("v")
        except OSError:
            pass
    try:
        pkg = os.path.join(workspace.APP_DIR, "package.json")
        if os.path.isfile(pkg):
            return str(json.load(open(pkg, encoding="utf-8")).get("version", "0.0.0")).lstrip("v")
    except OSError:
        pass
    return "0.0.0-dev"


def _session():
    """会话：默认直连（trust_env=False，绕开 Windows 系统代理把 GitHub API 打成 403）；
    设了 BAZZ_GH_PROXY 时走显式代理。"""
    s = requests.Session()
    s.trust_env = False
    proxy = os.environ.get("BAZZ_GH_PROXY", "").strip()
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    s.headers.update({"Accept": "application/vnd.github+json", "User-Agent": "BAZZ.AGENT-updater"})
    return s


def fetch_latest(timeout: float = 12.0) -> dict:
    """GET GitHub Releases latest；失败抛 RuntimeError（含中文原因）。"""
    if requests is None:
        raise RuntimeError("缺少 requests 依赖，无法检查更新。")
    try:
        r = _session().get(RELEASE_URL, timeout=timeout)
    except Exception as e:
        raise RuntimeError(f"无法连接更新服务器（{e.__class__.__name__}）。请检查网络后重试。")
    if r.status_code == 404:
        raise RuntimeError("更新服务器未找到 Release（仓库或 tag 异常）。")
    if r.status_code != 200:
        raise RuntimeError(f"更新服务器返回 HTTP {r.status_code}。")
    return r.json()


def check(timeout: float = 12.0) -> dict:
    """对比本地/远端版本，返回给 /api/update/check。永不抛 —— 网络失败给 error 字段。"""
    cur = _local_version()
    try:
        rel = fetch_latest(timeout)
        tag = str(rel.get("tag_name", "")).lstrip("v")
        latest = _ver_tuple(tag)
        mine = _ver_tuple(cur)
        body = rel.get("body") or ""
        return {
            "ok": True,
            "current": cur,
            "latest": tag or rel.get("name", ""),
            "available": bool(tag) and latest > mine,
            "html_url": rel.get("html_url", "") or f"https://github.com/{GITHUB_REPO}/releases/latest",
            "published_at": rel.get("published_at", ""),
            "notes": body.strip()[:2000],
        }
    except RuntimeError as e:
        return {"ok": False, "current": cur, "available": False, "error": str(e),
                "html_url": f"https://github.com/{GITHUB_REPO}/releases/latest"}
    except Exception as e:
        return {"ok": False, "current": cur, "available": False, "error": f"检查失败：{e}",
                "html_url": f"https://github.com/{GITHUB_REPO}/releases/latest"}
