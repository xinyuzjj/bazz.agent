"""BAZZ.AGENT 自动更新模块 —— 检查 GitHub Releases / 后台下载 / 整目录替换重启（v1.2.13）。

版本源：
  · 本地：<APP_DIR>/BAZZ_VERSION.txt（打包时 build-desktop.js 写入；冻结态 = ScoutBackend 目录）
          dev 回退到 <APP_DIR>/package.json 的 version 字段
  · 远端：GitHub Releases latest（xinyuzjj/bazz.agent，资产含 win32-x64-portable.zip）

v1.2.13 设计要点（吸取 v1.2.10「完全无效」教训）：
  1. 修复替换路径：旧代码把 root（应用目录本身）当成父目录，`root/BAZZ.AGENT-win32-x64`
     根本不存在 → 整目录替换空转。正确语义：替换目标是 app_root() 自身，
     新 zip 解到其父目录，替换后目录名恒为 BAZZ.AGENT-win32-x64。
  2. 保护用户数据：WORKSPACE（state.db / square_posts / attachments 等）在冻结态位于
     <app>/resources/scout-bundle/ScoutBackend/workspace —— 直接在应用目录内。
     整目录替换前先把 WORKSPACE 原子改名挪到父目录备份，替换成功后再挪回新应用，
     任何一步失败都会把备份挪回原位并中止，绝不丢数据。
  3. 下载带重试 + 落盘后校验 zip 顶层结构；apply 前再次确认文件存在。
  4. UI 侧每步失败都有「打开下载页」浏览器兜底，不会把人卡死在半自动状态。

应用流程（桌面便携版专用，dev 拒绝）：
  check → download（后台线程，前端轮询进度）→ apply：
    路径内嵌进 PowerShell 脚本（UTF-8 BOM，规避命令行传参编码坑）→ DETACHED 启动 →
    等 Electron 主进程退出 → 杀残留后端 → 备份 WORKSPACE → 整目录替换 → 还原 WORKSPACE →
    删除 zip → 重启新 exe。
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
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
FOLDER_NAME = "BAZZ.AGENT-win32-x64"      # zip 顶层目录 + 替换后新应用目录名（build-desktop.js 契约）
EXE_NAME = "BAZZ.AGENT.exe"
ASSET_HINT = "win32-x64-portable.zip"
CHECKSUM_ASSET = "SHA256SUMS"
VERSION_FILE = "BAZZ_VERSION.txt"
DL_ATTEMPTS = 3                           # 下载重试次数（网络抖动/被掐断时有用）

# —— 下载进度（模块级共享，前端轮询） —— #
_dl_lock = threading.Lock()
_dl_state = {"active": False, "total": 0, "done": 0, "path": "", "error": "", "ready": False, "started_at": 0}


def _ver_tuple(v: str):
    m = re.match(r"v?(\d+)\.(\d+)(?:\.(\d+))?", str(v or "").strip())
    if not m:
        return (0, 0, 0)
    return tuple(int(x) for x in m.groups(default="0"))


def _local_version() -> str:
    """本地当前版本：BAZZ_VERSION env > 各处 BAZZ_VERSION.txt > package.json。"""
    v = os.environ.get("BAZZ_VERSION", "").strip()
    if v:
        return v.lstrip("v")
    cands = [os.path.join(workspace.APP_DIR, VERSION_FILE),
             os.path.join(os.path.dirname(workspace.APP_DIR), VERSION_FILE)]
    try:
        cands.append(os.path.join(app_root(), VERSION_FILE))
    except Exception:
        pass
    for cand in cands:
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
    """便携版应用目录（含 BAZZ.AGENT.exe 与 resources/ 的目录，即替换目标）。dev 返回 APP_DIR。"""
    cand = workspace.APP_DIR
    for _ in range(5):
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
    """会话：默认直连（trust_env=False，绕开 Windows 系统代理把 GitHub 打成 403）；
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


def _fetch_checksums(rel: dict) -> dict:
    """解析 release 的 SHA256SUMS 资产 → {文件名: sha256小写}。拿不到返回 {}。"""
    out = {}
    for a in rel.get("assets", []) or []:
        if a.get("name", "").lower() != CHECKSUM_ASSET:
            continue
        url = a.get("browser_download_url", "")
        try:
            r = _session().get(url, timeout=(10, 30))
            if r.status_code == 200:
                for line in r.text.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        out[parts[-1].lstrip("*")] = parts[0].lower()
        except Exception:
            pass
        break
    return out


def _sha256_of(path: str) -> str:
    """流式计算文件 SHA256（大 zip 防内存爆）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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


def _zip_layout_ok(path: str) -> bool:
    """轻量校验 zip 顶层结构：须含 <FOLDER_NAME>/<EXE_NAME>。不做全量 CRC（大文件耗时）。"""
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
        prefix = FOLDER_NAME + "/"
        top_ok = any(n.startswith(prefix) for n in names)
        exe_ok = any(n == prefix + EXE_NAME for n in names)
        return top_ok and exe_ok
    except Exception:
        return False


def _download_once(url: str, dest: str, tmp: str, read_timeout: float = 60.0):
    """单次流式下载到 .part；抛错由外层重试。返回最终字节数。"""
    done = 0
    total = 0
    with _session().get(url, stream=True, timeout=(10, read_timeout)) as r:
        if r.status_code != 200:
            raise RuntimeError(f"下载失败：HTTP {r.status_code}")
        total = int(r.headers.get("content-length") or 0)
        with _dl_lock:
            _dl_state["total"] = total
            _dl_state["done"] = 0
            _dl_state["error"] = ""
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                with _dl_lock:
                    _dl_state["done"] = done
    if total and done != total:
        raise RuntimeError(f"下载不完整（{done}/{total} 字节），已自动重试。")
    return done


def _download_worker(url: str, dest: str):
    tmp = dest + ".part"
    last_err = ""
    for attempt in range(1, DL_ATTEMPTS + 1):
        try:
            if requests is None:
                raise RuntimeError("缺少 requests 依赖，无法下载。")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            _download_once(url, dest, tmp)
            if not _zip_layout_ok(tmp):
                raise RuntimeError("下载的压缩包结构异常（未找到 BAZZ.AGENT-win32-x64 顶层目录）。")
            os.replace(tmp, dest)
            with _dl_lock:
                _dl_state["done"] = _dl_state["total"] or _dl_state["done"]
                _dl_state["ready"] = True
                _dl_state["active"] = False
                _dl_state["error"] = ""
            return
        except Exception as e:
            last_err = str(e)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            if attempt < DL_ATTEMPTS:
                with _dl_lock:
                    _dl_state["error"] = f"{last_err}（第 {attempt}/{DL_ATTEMPTS} 次失败，重试中…）"
                time.sleep(2 * attempt)
    with _dl_lock:
        _dl_state["error"] = last_err or "下载失败"
        _dl_state["active"] = False
        _dl_state["ready"] = False


def start_download(url: str) -> dict:
    """后台线程下载 zip 到 update-cache/<asset 文件名>。返回立即。"""
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


def _log_path() -> str:
    return os.path.join(tempfile.gettempdir(), "bazz-updater.log")


def _write_updater_script(zip_path: str, wait_pid: int, backend_pid: int) -> str:
    """生成 PowerShell 更新脚本（路径内嵌防编码坑；UTF-8 带 BOM 防 PS5.1 按 ANSI 误读）。

    核心逻辑：
      等 Electron 退出 → 杀残留 → 备份 WORKSPACE 到父目录（同卷原子改名）→
      删旧应用目录 → 解压 zip 到父目录 → 校验新 exe → 把 WORKSPACE 挪回新应用 →
      删 zip → 重启。任一步失败：能还原就还原 WORKSPACE，绝不丢用户数据。
    """
    root = app_root()                              # 旧应用目录（替换目标）
    parent = os.path.dirname(root)
    new_root = os.path.join(parent, FOLDER_NAME)   # 新应用目录（zip 顶层目录名，契约固定）
    new_exe = os.path.join(new_root, EXE_NAME)
    ws = workspace.WORKSPACE
    # workspace 是否仍在应用目录内（旧设计）。自 v1.2.17 起 Electron 已把 workspace 外置到
    # %APPDATA%\BAZZ.AGENT —— 更新变成「纯程序替换」，绝不挪动用户数据，故不再需要备份/还原。
    ws_inside_root = _path_inside(ws, root)
    if ws_inside_root:
        ws_bak = os.path.join(parent, ".bazz-ws-backup")
        new_ws = os.path.join(new_root, os.path.relpath(ws, root)) if _path_inside(ws, root) else ws
        zip_bak = os.path.join(ws_bak, os.path.relpath(zip_path, ws)) if _path_inside(zip_path, ws) else zip_path
        ps_ws = ws                       # 让 PS 走「备份 WORKSPACE → 替换 → 还原」逻辑
    else:
        ws_bak = ""
        new_ws = ws                      # 外置 workspace 在删除应用目录时不受影响，保持不变
        zip_bak = zip_path               # workspace 不被挪走，zip 也不会随其迁移
        ps_ws = ""                       # 外置时 PS 的 workspace 备份/还原块为空 → 自然跳过
    # zip 备份后位置：workspace 若在应用目录内，会被整目录挪到 ws_bak —— 解压前需切到新路径
    log = _log_path()

    log_cmd = ("Add-Content -Path '{log}' -Value ('[' + (Get-Date -Format o) + '] ' + $msg) "
               "-Encoding UTF8").replace("{log}", _ps1_quote(log))

    script = """$ErrorActionPreference = 'Continue'
function L($msg) {{ {L_BODY} }}
$oldRoot = '{root}'
$parent  = '{parent}'
$newRoot = '{newRoot}'
$newExe  = '{newExe}'
$ws      = '{ws}'
$wsBak   = '{wsBak}'
$newWs   = '{newWs}'
$zip     = '{zip}'
$zipBak  = '{zipBak}'
$waitPid = {wait}
$backendPid = {backend}
L '--- updater start ---'
# 1) 等 Electron 主进程退出（最长 ~3 分钟；用户点更新后前端已主动关窗）
if ($waitPid -gt 0) {{
  $gone = $false
  for ($i = 0; $i -lt 90; $i++) {{
    if (-not (Get-Process -Id $waitPid -ErrorAction SilentlyContinue)) {{ $gone = $true; break }}
    Start-Sleep -Seconds 2
  }}
  if (-not $gone) {{ L 'ERR wait-electron-timeout'; exit 3 }}
}}
# 2) 杀残留后端 / 双开实例（释放文件锁，避免删不掉旧目录）
if ($backendPid -gt 0) {{ Stop-Process -Id $backendPid -Force -ErrorAction SilentlyContinue }}
Get-Process -Name 'BAZZ.AGENT','ScoutBackend' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
# 3) 前置校验（zip 可能随后因 workspace 备份被挪到 $zipBak）
if (-not (Test-Path -LiteralPath $zip) -and -not (Test-Path -LiteralPath $zipBak)) {{ L 'ERR zip-missing'; exit 4 }}
# 4) 备份 WORKSPACE（同卷原子改名；失败不中止，但会记录 —— 用户数据优先）
$wsMoved = $false
if ($ws -and (Test-Path -LiteralPath $ws)) {{
  try {{
    if (Test-Path -LiteralPath $wsBak) {{ Remove-Item -LiteralPath $wsBak -Recurse -Force -ErrorAction Stop }}
    Move-Item -LiteralPath $ws -Destination $wsBak -ErrorAction Stop
    $wsMoved = $true
    L ('workspace backed up -> ' + $wsBak)
  }} catch {{ L ('ERR ws-backup: ' + $_.Exception.Message) }}
}}
# 备份把 zip 一起挪走了 → 解压/清理切到备份后的真实路径
if (-not (Test-Path -LiteralPath $zip) -and (Test-Path -LiteralPath $zipBak)) {{ $zip = $zipBak; L 'zip relocated with workspace backup' }}
# 5) 整目录替换（旧目录可能名 != BAZZ.AGENT-win32-x64，先清同名残留再删旧）
function Restore-Ws {{
  if ($wsMoved -and (Test-Path -LiteralPath $wsBak) -and -not (Test-Path -LiteralPath $ws)) {{
    try {{ Move-Item -LiteralPath $wsBak -Destination $ws -Force -ErrorAction Stop; L 'workspace restored (rollback)' }}
    catch {{ L ('ERR ws-restore-rollback: ' + $_.Exception.Message) }}
  }}
}}
try {{
  if ($newRoot -ne $oldRoot) {{
    if (Test-Path -LiteralPath $newRoot) {{ Remove-Item -LiteralPath $newRoot -Recurse -Force -ErrorAction Stop }}
  }}
  Remove-Item -LiteralPath $oldRoot -Recurse -Force -ErrorAction Stop
}} catch {{
  L ('ERR remove-old: ' + $_.Exception.Message)
  Restore-Ws
  exit 6
}}
try {{
  Expand-Archive -LiteralPath $zip -DestinationPath $parent -Force -ErrorAction Stop
  if (-not (Test-Path -LiteralPath $newExe)) {{ throw 'new exe missing after expand' }}
}} catch {{
  L ('ERR expand: ' + $_.Exception.Message)
  Restore-Ws
  exit 7
}}
L 'swap ok'
# 6) 还原 WORKSPACE 到新应用
if ($wsMoved -and (Test-Path -LiteralPath $wsBak)) {{
  try {{
    $dst = Split-Path -Parent $newWs
    if (-not (Test-Path -LiteralPath $dst)) {{ New-Item -ItemType Directory -Path $dst -Force | Out-Null }}
    if (Test-Path -LiteralPath $newWs) {{ Remove-Item -LiteralPath $newWs -Recurse -Force -ErrorAction SilentlyContinue }}
    Move-Item -LiteralPath $wsBak -Destination $newWs -ErrorAction Stop
    L ('workspace restored -> ' + $newWs)
  }} catch {{ L ('ERR ws-restore (backup kept at ' + $wsBak + '): ' + $_.Exception.Message) }}
}}
# 7) 清理更新包（zip 随 workspace 备份/还原换过位置：按新位置递归清，原路径兜底再删一次）
try {{
  $uc = Join-Path $newWs 'update-cache'
  if (Test-Path -LiteralPath $uc) {{
    Get-ChildItem -Path $uc -Filter *.zip -Recurse -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
  }}
  Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
  L 'zip removed'
}} catch {{}}
# 8) 重启新版本
try {{
  Start-Process -FilePath $newExe -WorkingDirectory (Split-Path -Parent $newExe)
  L 'relaunch ok'
}} catch {{ L ('ERR relaunch: ' + $_.Exception.Message); exit 8 }}
""".format(L_BODY=log_cmd,
           root=_ps1_quote(root), parent=_ps1_quote(parent),
           newRoot=_ps1_quote(new_root), newExe=_ps1_quote(new_exe),
           ws=_ps1_quote(ps_ws), wsBak=_ps1_quote(ws_bak), newWs=_ps1_quote(new_ws),
           zip=_ps1_quote(zip_path), zipBak=_ps1_quote(zip_bak),
           wait=int(wait_pid), backend=int(backend_pid))
    ps1 = os.path.join(update_cache_dir(), "apply-update.ps1")
    with open(ps1, "w", encoding="utf-8-sig") as f:
        f.write(script)
    return ps1


def _path_inside(child: str, parent: str) -> bool:
    """child 是否位于 parent 之下（含边界比较，Windows 大小写不敏感）。"""
    try:
        c = os.path.normcase(os.path.abspath(child))
        p = os.path.normcase(os.path.abspath(parent))
        return c == p or c.startswith(p.rstrip("\\/") + os.sep)
    except Exception:
        return False


def apply(zip_path: str, wait_pid: int = 0) -> dict:
    """生成脚本并以 DETACHED 进程启动。仅打包态允许。"""
    if not is_packaged():
        return {"ok": False,
                "error": "自动更新仅适用于桌面便携版（打包态）。开发模式请手动拉取最新代码/重新发布。",
                "log": _log_path()}
    if not zip_path or not os.path.exists(zip_path):
        return {"ok": False, "error": "更新包不存在，请先完成下载。", "log": _log_path()}
    root = app_root()
    log = _log_path()
    try:
        # 应用前再确认一次包结构，避免解压到一半才发现包坏
        if not _zip_layout_ok(zip_path):
            return {"ok": False, "error": "更新包结构异常（未找到 BAZZ.AGENT-win32-x64 顶层目录），请重新下载。",
                    "log": log}
        # SHA256 校验（防下载包被篡改/损坏）：能从官 release 拿到校验和就强校验，不符即中止。
        # 拿不到（网络抖动 / 旧 release 未挂 SHA256SUMS）则仅忽略，不阻塞正常更新。
        try:
            sums = _fetch_checksums(fetch_latest(timeout=15))
            expected = sums.get(os.path.basename(zip_path), "")
            if expected:
                actual = _sha256_of(zip_path)
                if actual != expected:
                    return {"ok": False,
                            "error": "更新包校验和不一致（可能被篡改或下载损坏）。已中止替换以保证安全，请重新下载。",
                            "log": log}
                print(f"[updater] SHA256 校验通过：{zip_path}")
        except Exception as e:
            print(f"[updater] 校验和获取失败，跳过校验：{e}")
        ps1 = _write_updater_script(zip_path, int(wait_pid or 0), os.getpid())
    except Exception as e:
        return {"ok": False, "error": f"生成更新脚本失败：{e}", "log": log}
    flags = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP|CREATE_NO_WINDOW
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-WindowStyle", "Hidden", "-File", ps1],
            creationflags=flags if os.name == "nt" else 0,
            close_fds=True,
        )
    except Exception as e:
        return {"ok": False, "error": f"启动更新进程失败：{e}", "log": log}
    return {"ok": True, "started": True, "log": log,
            "tip": "更新脚本已在后台运行：应用会自动退出，完成后自动替换并重启，期间请勿关机。"}
