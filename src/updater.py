"""BAZZ.AGENT 自动更新模块 —— 检查 GitHub Releases / 下载 / 整目录替换重启。

版本源：
  · 本地：<APP_DIR>/BAZZ_VERSION.txt（打包时 build-desktop.js 写入；冻结态 = ScoutBackend 目录）
          dev 回退到 <APP_DIR>/package.json 的 version 字段
  · 远端：GitHub Releases latest（xinyuzjj/bazz.agent，资产含 win32-x64-portable.zip）

应用流程（桌面便携版专用，dev 拒绝）：
  check → download（后台线程，前端轮询进度）→ apply：
    把路径内嵌进 PowerShell 脚本（避免命令行传参的编码坑）→ 以 DETACHED 进程启动 →
    等待 Electron 主进程退出 → 杀残留后端 → 整目录替换（旧 BAZZ.AGENT-win32-x64 → 新 zip）→ 重启 exe。
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import zipfile

try:
    import requests
except Exception:  # 极端情况缺失时仅在调用处报错
    requests = None

import workspace

GITHUB_REPO = "xinyuzjj/bazz.agent"
GITHUB_API = os.environ.get("BAZZ_GH_API") or "https://api.github.com"
RELEASE_URL = f"{GITHUB_API}/repos/{GITHUB_REPO}/releases/latest"
FOLDER_NAME = "BAZZ.AGENT-win32-x64"      # zip 顶层目录（build-desktop.js 固定）
EXE_NAME = "BAZZ.AGENT.exe"
ASSET_HINT = "win32-x64-portable.zip"
VERSION_FILE = "BAZZ_VERSION.txt"

# —— 下载进度（模块级共享，前端轮询） —— #
_dl_lock = threading.Lock()
_dl_state = {"active": False, "total": 0, "done": 0, "path": "", "error": "", "ready": False, "started_at": 0}


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


def app_root() -> str:
    """便携版根目录（含 BAZZ.AGENT.exe 与 resources/）。dev 态返回 APP_DIR。"""
    cand = workspace.APP_DIR
    for _ in range(4):
        if (os.path.isfile(os.path.join(cand, EXE_NAME))
                or os.path.isdir(os.path.join(cand, "resources"))):
            return cand
        parent = os.path.dirname(cand)
        if parent == cand:
            break
        cand = parent
    return workspace.APP_DIR


def is_packaged() -> bool:
    """是否运行在打包态：APP_DIR 必须嵌套在 app_root 内（resources/scout-bundle/ScoutBackend…）。"""
    root = app_root()
    return root != workspace.APP_DIR and os.path.isdir(os.path.join(root, "resources"))


def update_cache_dir() -> str:
    d = os.path.join(workspace.WORKSPACE, "update-cache")
    os.makedirs(d, exist_ok=True)
    return d


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


def _pick_asset(rel: dict) -> dict:
    for a in rel.get("assets", []) or []:
        name = a.get("name", "")
        if ASSET_HINT in name and name.lower().endswith(".zip"):
            return a
    return {}


def check(timeout: float = 12.0) -> dict:
    """对比本地/远端版本，返回给 /api/update/check。永不抛 —— 网络失败给 error 字段。"""
    cur = _local_version()
    try:
        rel = fetch_latest(timeout)
        tag = str(rel.get("tag_name", "")).lstrip("v")
        latest = _ver_tuple(tag)
        mine = _ver_tuple(cur)
        asset = _pick_asset(rel)
        body = rel.get("body") or ""
        return {
            "ok": True,
            "current": cur,
            "latest": tag or rel.get("name", ""),
            "available": bool(tag) and latest > mine,
            "asset": {"name": asset.get("name", ""), "size": asset.get("size", 0),
                      "url": asset.get("browser_download_url", "")},
            "html_url": rel.get("html_url", ""),
            "published_at": rel.get("published_at", ""),
            "notes": body.strip()[:2000],
        }
    except RuntimeError as e:
        return {"ok": False, "current": cur, "available": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "current": cur, "available": False, "error": f"检查失败：{e}"}


def _download_worker(url: str, dest: str):
    global _dl_state
    try:
        if requests is None:
            raise RuntimeError("缺少 requests 依赖，无法下载。")
        tmp = dest + ".part"
        with _session().get(url, stream=True, timeout=(6, 30)) as r:
            if r.status_code != 200:
                raise RuntimeError(f"下载失败：HTTP {r.status_code}")
            total = int(r.headers.get("content-length") or 0)
            with _dl_lock:
                _dl_state["total"] = total
                _dl_state["done"] = 0
                _dl_state["error"] = ""
                _dl_state["path"] = dest
                _dl_state["ready"] = False
            done = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    with _dl_lock:
                        _dl_state["done"] = done
        os.replace(tmp, dest)
        with _dl_lock:
            _dl_state["done"] = total or done
            _dl_state["ready"] = True
            _dl_state["active"] = False
    except Exception as e:
        try:
            if os.path.exists(tmp := dest + ".part"):
                os.remove(tmp)
        except OSError:
            pass
        with _dl_lock:
            _dl_state["error"] = str(e)
            _dl_state["active"] = False
            _dl_state["ready"] = False


def start_download(url: str) -> dict:
    """后台线程下载到 update-cache/<asset 文件名>。返回立即。"""
    name = url.rsplit("/", 1)[-1].split("?", 1)[0] or "bazz-agent.zip"
    dest = os.path.join(update_cache_dir(), name)
    with _dl_lock:
        if _dl_state["active"]:
            return {"started": False, "error": "已有下载任务进行中。", "path": _dl_state["path"]}
        _dl_state.update({"active": True, "ready": False, "error": "", "done": 0,
                          "total": 0, "path": dest, "started_at": time.time()})
    threading.Thread(target=_download_worker, args=(url, dest), daemon=True).start()
    return {"started": True, "path": dest}


def download_status() -> dict:
    with _dl_lock:
        return dict(_dl_state)


def _ps1_quote(p: str) -> str:
    """PowerShell 单引号字符串转义（路径内嵌脚本，规避命令行传参编码坑）。"""
    return p.replace("'", "''")


def write_updater_script(zip_path: str, wait_pid: int, backend_pid: int, root: str, log_path: str) -> str:
    """生成 PowerShell 更新脚本（路径内嵌防编码坑；UTF-8 带 BOM 防 PS5.1 按 ANSI 误读）。"""
    old = os.path.join(root, FOLDER_NAME)
    exe = os.path.join(root, FOLDER_NAME, EXE_NAME)
    log_cmd = ("Add-Content -Path '{log}' -Value ('[' + (Get-Date -Format o) + '] ' + $msg) "
               "-Encoding UTF8").replace("{log}", _ps1_quote(log_path))
    script = """$ErrorActionPreference = 'Continue'
function L($msg) {{ {L_BODY} }}
$old = '{old}'
$exe = '{exe}'
$root = '{root}'
$zip = '{zip}'
$waitPid = {wait}
$backendPid = {backend}
L 'updater start'
# 1) 等待 Electron 主进程退出（最长 ~5 分钟）
if ($waitPid -gt 0) {{
  $gone = $false
  for ($i = 0; $i -lt 150; $i++) {{
    $p = Get-Process -Id $waitPid -ErrorAction SilentlyContinue
    if (-not $p) {{ $gone = $true; break }}
    Start-Sleep -Seconds 2
  }}
  if (-not $gone) {{ L 'timeout waiting main process'; exit 3 }}
}}
# 2) 清掉可能残留的后端 / 双开实例
if ($backendPid -gt 0) {{ Stop-Process -Id $backendPid -Force -ErrorAction SilentlyContinue }}
Get-Process -Name 'BAZZ.AGENT' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
# 3) 整目录替换
try {{
  if (Test-Path $old) {{ Remove-Item -Recurse -Force $old -ErrorAction Stop }}
  if (-not (Test-Path $zip)) {{ L 'zip missing'; exit 4 }}
  Expand-Archive -Path $zip -DestinationPath $root -Force -ErrorAction Stop
  if (-not (Test-Path $exe)) {{ L 'new exe missing'; exit 5 }}
  Remove-Item $zip -Force -ErrorAction SilentlyContinue
  L 'swap ok'
}} catch {{
  L ('swap failed: ' + $_.Exception.Message)
  exit 6
}}
# 4) 重启新版本
Start-Process -FilePath $exe -WorkingDirectory $root
L 'relaunch ok'
""".format(L_BODY=log_cmd,
           old=_ps1_quote(old), exe=_ps1_quote(exe), root=_ps1_quote(root), zip=_ps1_quote(zip_path),
           wait=int(wait_pid), backend=int(backend_pid))
    ps1 = os.path.join(update_cache_dir(), "apply-update.ps1")
    with open(ps1, "w", encoding="utf-8-sig") as f:
        f.write(script)
    return ps1


def apply(zip_path: str, wait_pid: int = 0) -> dict:
    """生成脚本并以 DETACHED 进程启动。仅打包态允许。"""
    if not is_packaged():
        return {"ok": False,
                "error": "自动更新仅适用于桌面便携版（打包态）。开发模式请手动拉取最新代码/重新发布。"}
    root = app_root()
    log_path = os.path.join(update_cache_dir(), "updater.log")
    try:
        ps1 = write_updater_script(zip_path, wait_pid, os.getpid(), root, log_path)
    except Exception as e:
        return {"ok": False, "error": f"生成更新脚本失败：{e}"}
    flags = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP|CREATE_NO_WINDOW
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-WindowStyle", "Hidden", "-File", ps1],
            creationflags=flags if os.name == "nt" else 0,
            close_fds=True,
        )
    except Exception as e:
        return {"ok": False, "error": f"启动更新进程失败：{e}"}
    return {"ok": True, "started": True, "log": log_path,
            "tip": "更新脚本已在后台运行：等待应用退出后自动替换并重启。"}
