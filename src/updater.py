"""BAZZ.AGENT 自动更新模块 —— 检查 GitHub Releases / 后台下载 / 整目录替换重启（v1.2.13）。

版本源：
  · 本地：<APP_DIR>/BAZZ_VERSION.txt（打包时 build-desktop.js 写入；冻结态 = ScoutBackend 目录）
          dev 回退到 <APP_DIR>/package.json 的 version 字段
  · 远端：GitHub Releases latest（xinyuzjj/bazz.agent，资产含 v<版本>-setup.exe 全量包 + delta 差分包）

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
import shutil
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
FOLDER_NAME = "BAZZ.AGENT-win32-x64"      # delta 重建 zip 的顶层目录名（build-desktop.js 契约）
EXE_NAME = "BAZZ.AGENT.exe"
# v1.5.11：Release 全量包只发 setup.exe（portable.zip 退役）。更新器全量回退 = 静默安装：
# 下载 setup.exe → SHA256 校验 → /VERYSILENT /DIR=<当前安装根> 原地覆盖（workspace 不在安装器
# 内容清单里，[InstallDelete] 不存在 → 用户数据天然保留）→ 重启。
ASSET_SUFFIX = "-setup.exe"
CHECKSUM_ASSET = "SHA256SUMS"
MANIFEST_ASSET = "MANIFEST.json"        # 全量文件清单（path -> {s, h}），更新完整性校验的真相源
DELTA_ASSET_PREFIX = "delta-"           # 差分包资产前缀：delta-<ver>.zip（相对上一版的变化文件）
VERSION_FILE = "BAZZ_VERSION.txt"
DL_ATTEMPTS = 3                           # 下载重试次数（网络抖动/被掐断时有用）

# —— 下载进度（模块级共享，前端轮询） —— #
_dl_lock = threading.Lock()
_dl_state = {"active": False, "total": 0, "done": 0, "path": "", "error": "", "ready": False,
             "started_at": 0, "stage": "idle", "ver": ""}  # ver=ready 包所属版本（跨版本复位用）


def _tag_from_url(url: str) -> str:
    m = re.search(r"v\d+\.\d+\.\d+", url or "")
    return m.group(0) if m else ""


class _DiffFallback(Exception):
    """增量差分路径内部失败标记：让调用者回退到整包下载。非网络/环境错误，无需上层处理。"""
    pass


# 本地由「增量差分」构建出来的目标 zip 路径集合。apply() 对这类包跳过「整包 SHA256 比对」
# （整包 SHA 只能比对官方原始 zip 的哈希，本地重建的 zip blob 与之必然不同），
# 因为构建时已对清单里每个文件做过 sha256 逐文件校验，安全性不降低。
_PATCH_BUILT = set()


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
    """便携版应用目录（含 BAZZ.AGENT.exe 与 resources/ 的目录，即替换目标）。dev 返回 APP_DIR。
    v1.3.6：实现合并进 workspace.app_root()（此前三处重复，防改漏），这里只做语义别名。"""
    return workspace.app_root()


def is_packaged() -> bool:
    """是否运行在打包态：APP_DIR 必须嵌套在 app_root 内（resources/scout-bundle/ScoutBackend…）。"""
    root = app_root()
    return root != workspace.APP_DIR and os.path.isdir(os.path.join(root, "resources"))


def update_cache_dir() -> str:
    """更新包缓存目录：优先 <安装根>/update-cache（workspace.UPDATE_CACHE_DIR），
    安装目录只读时 workspace 层已兜底回工作区。这里只消费，不再自定位置。"""
    d = workspace.UPDATE_CACHE_DIR
    os.makedirs(d, exist_ok=True)
    return d


def _session():
    """会话：默认不读系统代理（trust_env=False，绕开 Windows 系统代理把 GitHub 打成 403）；
    代理来源优先级：BAZZ_GH_PROXY 环境变量 → v1.3.7 代理池启用的节点。"""
    s = requests.Session()
    s.trust_env = False
    proxy = os.environ.get("BAZZ_GH_PROXY", "").strip()
    if not proxy:
        try:
            import proxy_pool
            proxy = proxy_pool.active_url()
        except Exception:
            proxy = ""
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
    """全量包资产：v1.5.11 起只认 setup.exe（portable.zip 已退役）。"""
    for a in rel.get("assets", []) or []:
        name = (a.get("name", "") or "").lower()
        if name.endswith(ASSET_SUFFIX):
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


def _sha_stream(path: str) -> str:
    """与 _sha256_of 等价的文件流式哈希（用于构建 zip 时逐成员校验）。"""
    return _sha256_of(path)


def _safe_rel(r: str) -> str:
    """清洗清单里的相对路径：去开头 /、去 .. / . /空段，防 zip-slip 逃逸。"""
    r = (r or "").replace("\\", "/")
    while r.startswith("/"):
        r = r[1:]
    parts = [p for p in r.split("/") if p not in ("", " ", ".", "..")]
    return "/".join(parts)


def _fetch_manifest(rel: dict) -> dict:
    """从 release 资产里拉 MANIFEST.json。拿不到 / 结构不对返回 {}。"""
    for a in rel.get("assets", []) or []:
        if a.get("name", "").lower() != MANIFEST_ASSET:
            continue
        url = a.get("browser_download_url", "")
        try:
            r = _session().get(url, timeout=(10, 30))
            if r.status_code == 200:
                j = r.json()
                if isinstance(j, dict) and isinstance(j.get("files"), dict):
                    return j
        except Exception:
            pass
        break
    return {}


def _pick_delta_asset(rel: dict) -> str:
    """取差分包资产 URL；没有返回 ""。"""
    for a in rel.get("assets", []) or []:
        n = a.get("name", "") or ""
        if n.startswith(DELTA_ASSET_PREFIX) and n.lower().endswith(".zip"):
            return a.get("browser_download_url", "") or ""
    return ""


# —— 防 drive-by：更新资产只信任本仓库的 GitHub 官方发布链 ——
# 之前 /api/update/download 收任意 url、/api/update/apply 收任意 zip 路径，
# 配合宽松 CORS，恶意网页可诱导下载攻击者的 zip 并整目录替换重启（RCE 链）。
_TRUSTED_CDN_HOSTS = (
    "https://objects.githubusercontent.com/",
    "https://release-assets.githubusercontent.com/",
)


def is_trusted_asset_url(url: str) -> bool:
    """仅放行：本仓库的 github.com/releases/download 直链、本仓库 api.github.com 链接、
    GitHub release 资产 CDN（签名跳转域）。其余一律拒绝。"""
    u = (url or "").strip()
    if not u.startswith("https://"):
        return False
    u = u.lower()
    repo = GITHUB_REPO.lower()
    if u.startswith("https://github.com/"):
        return f"/{repo}/releases/download/" in u
    if u.startswith("https://api.github.com/repos/"):
        return f"/repos/{repo}/" in u
    return any(u.startswith(h) for h in _TRUSTED_CDN_HOSTS)


def _download_entry(dest: str, url: str):
    """入口线程：先尝试增量差分；无清单/无差分/校验不符则回退整包下载。"""
    rel = {}
    try:
        rel = fetch_latest(timeout=15)
    except Exception:
        rel = {}
    try:
        _diff_worker(rel, dest, url)
    except Exception as e:  # 兜底：任何未捕获异常都回退整包，绝不卡死更新
        print(f"[updater] 增量差分异常，回退整包下载：{e}")
        try:
            _PATCH_BUILT.discard(dest)
            if os.path.exists(dest + ".part"):
                os.remove(dest + ".part")
        except OSError:
            pass
        try:
            _download_worker(url, dest)
        except Exception:
            pass


def _diff_worker(rel: dict, dest: str, url: str):
    """增量差分构建目标全量 zip（ZIP_STORED，本地直写）。

    pipeline：
      1. 拉 MANIFEST.json（当前版全量清单）→ 没有 → 回退整包。
      2. 下载 delta-<ver>.zip（相对上一版的变化文件，通常只有几 MB）→ 解压到临时目录。
      3. 按清单流式重建完整新树：成员来源优先取 delta（有就用、并校验），否则取本地
         app_root 未变文件——边写边算 sha256 与清单比对，任一不符即失败。
      4. 结构校验通过 → 替换为最终 zip，标记 ready 并记入 _PATCH_BUILT。
      任一环节失败 → 抛 _DiffFallback / 对应元数据缺失 → 由调用方回退整包。
    v1.5.11：全量资产已改为 setup.exe（dest 以 .exe 结尾），差分重建产物仍必须是
    zip（apply 按扩展名分流）—— 产物名从 <name>.exe 改写为 <name>.zip。
    """
    if dest.lower().endswith(".exe"):
        dest = dest[:-4] + ".zip"
    tmp = dest + ".part"
    try:
        _PATCH_BUILT.discard(dest)
    except Exception:
        pass

    man = _fetch_manifest(rel)
    files = man.get("files") if man else None
    if not files:
        raise _DiffFallback()
    delta_url = _pick_delta_asset(rel)
    if not delta_url:
        raise _DiffFallback()
    local = app_root()

    # 1) 下载差分包（小；进度 = 差分字节）
    patch_zip = os.path.join(update_cache_dir(), "delta-" + (os.path.basename(dest) or "bazz.zip"))
    try:
        if os.path.exists(patch_zip + ".part"):
            os.remove(patch_zip + ".part")
        _download_once(delta_url, patch_zip, patch_zip + ".part")
        os.replace(patch_zip + ".part", patch_zip)   # 落盘（.part → 最终）
    except Exception:
        raise _DiffFallback()

    # 2) 解压差分包到临时目录（顶层目录 = FOLDER_NAME）
    patch_dir = os.path.join(update_cache_dir(), "patch-" + str(int(time.time() * 1000)))
    try:
        shutil.rmtree(patch_dir, ignore_errors=True)
    except Exception:
        pass
    os.makedirs(patch_dir, exist_ok=True)
    try:
        with zipfile.ZipFile(patch_zip) as zf:
            zf.extractall(patch_dir)
    except Exception:
        raise _DiffFallback()
    patch_root = os.path.join(patch_dir, FOLDER_NAME)

    # 3) 流式重建完整新树并逐文件校验
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except OSError:
        pass
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
            for raw, meta in files.items():
                r = _safe_rel(raw)
                if not r:
                    raise _DiffFallback()
                src = os.path.join(patch_root, r)
                if not _path_inside(src, patch_root) or not os.path.isfile(src):
                    src = os.path.normpath(os.path.join(local, *r.split("/")))
                    if not _path_inside(src, local) or not os.path.isfile(src):
                        raise _DiffFallback()
                if _sha_stream(src) != str(meta.get("h") or "").lower():
                    raise _DiffFallback()
                zf.write(src, FOLDER_NAME + "/" + r)
        if not _zip_layout_ok(tmp):
            raise _DiffFallback()
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise

    # 4) 收尾：替换为最终 zip + 清理临时目录
    os.replace(tmp, dest)
    try:
        shutil.rmtree(patch_dir, ignore_errors=True)
    except Exception:
        pass
    with _dl_lock:
        _dl_state.update({"ready": True, "active": False, "error": "",
                          "done": _dl_state.get("total") or 0, "path": dest, "stage": "ready",
                          "ver": _tag_from_url(url)})
    _PATCH_BUILT.add(dest)
    print(f"[updater] 增量差分完成（清单 {len(files)} 项）：{dest}")


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
        available = bool(tag) and latest > mine
        if available and asset.get("browser_download_url"):
            # 后台预下载：检测到新版即开始（增量差分优先），用户打开「立即更新」时大概率已就绪。
            # 幂等：已就绪/进行中/目标包已存在时都不重复。
            try:
                with _dl_lock:
                    ready = _dl_state.get("ready")
                    active = _dl_state.get("active")
                    # v1.3.6：ready 的包属于旧版本时复位 —— 否则应用内停留跨过两个版本时，
                    # 新版预下载永不触发，「立即更新」拿到的还是旧包。
                    if ready and _dl_state.get("ver") != tag:
                        _dl_state.update({"ready": False, "active": False, "path": "",
                                          "done": 0, "total": 0, "ver": ""})
                        ready = False
                target = os.path.join(update_cache_dir(),
                                      os.path.basename(asset["browser_download_url"]).split("?", 1)[0])
                if not ready and not active and not os.path.isfile(target):
                    start_download(asset["browser_download_url"])
            except Exception:
                pass  # 预下载非关键路径，失败不影响 check 结果
        return {
            "ok": True,
            "current": cur,
            "latest": tag or rel.get("name", ""),
            "available": available,
            "pre_downloading": available,
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


# —— 并行分段下载 —— #
_DL_SEGMENTS = 4          # 分段线程数（越大越吃连接数，GitHub 单连接限速时收益明显）
_DL_MIN_PARALLEL = 8 << 20  # 小于 8MB 直接单流，分段反而有启动开销


def _parallel_download(url: str, total: int, tmp: str) -> int:
    """HTTP Range 并行分段下载。服务器不支持 Range 或任一段失败 → 抛异常回退单流。

    全程写瞬态分段，拼接校验通过后再原子替换到 tmp —— 失败绝不破坏已有 tmp，
    从而与单流「断点续传」安全共存。
    """
    segs = _DL_SEGMENTS
    part = max(1, int(total / segs)) if total >= segs else total
    # 探针：确认真支持 Range（206），否则返回 200 整包，分段无意义
    with _session().get(url, stream=True, timeout=(10, 30),
                        headers={"Range": "bytes=0-0"}) as r:
        if r.status_code != 206:
            r.close()
            raise RuntimeError("服务器不支持断点分段")
    seg_paths = [tmp + f".s{i}" for i in range(segs)]
    cat = tmp + ".cat"
    for p in seg_paths + [cat]:
        try:
            os.remove(p)
        except OSError:
            pass
    done = [0] * segs
    errors = []

    def worker(i: int):
        start = i * part
        end = (total - 1) if i == segs - 1 else min((i + 1) * part - 1, total - 1)
        try:
            with _session().get(url, stream=True, timeout=(20, 120),
                                headers={"Range": f"bytes={start}-{end}"}) as r:
                if r.status_code != 206:
                    raise RuntimeError(f"HTTP {r.status_code}")
                with open(seg_paths[i], "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 18):
                        if not chunk:
                            continue
                        f.write(chunk)
                        done[i] += len(chunk)
                        with _dl_lock:
                            _dl_state["done"] = sum(done)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(segs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]
    # 按顺序拼接 + 校验大小 → 原子替换到 tmp
    with open(cat, "wb") as out:
        for p in seg_paths:
            with open(p, "rb") as f:
                shutil.copyfileobj(f, out, 1 << 20)
    size = os.path.getsize(cat)
    if size != total:
        raise RuntimeError(f"下载不完整（{size}/{total} 字节）")
    os.replace(cat, tmp)
    return size


def _download_once(url: str, dest: str, tmp: str, read_timeout: float = 60.0):
    """下载到 .part；优先并行分段加速，失败回退单流（含断点续传）。返回最终字节数。"""
    # 先拿文件总大小（不解体整个响应）
    with _session().get(url, stream=True, timeout=(10, read_timeout)) as r:
        if r.status_code != 200:
            raise RuntimeError(f"下载失败：HTTP {r.status_code}")
        total = int(r.headers.get("content-length") or 0)
        r.close()
    with _dl_lock:
        _dl_state["total"] = total
        _dl_state["done"] = 0
        _dl_state["error"] = ""
    # 大文件优先并行分段；服务器不支持 / 中途失败则清理临时分段、回退单流
    if total >= _DL_MIN_PARALLEL:
        try:
            return _parallel_download(url, total, tmp)
        except Exception:
            try:
                for p in (tmp + f".s{i}" for i in range(_DL_SEGMENTS)):
                    if os.path.exists(p):
                        os.remove(p)
                if os.path.exists(tmp + ".cat"):
                    os.remove(tmp + ".cat")
            except OSError:
                pass
    # 单流（断点续传：已存在 .part 说明上次下到哪，续着下，避免重头）
    done = 0
    if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
        done = os.path.getsize(tmp)
        if done >= total:
            done = 0
            try:
                os.remove(tmp)
            except OSError:
                pass
    headers = {"Range": f"bytes={done}-"} if done > 0 else None
    with _session().get(url, stream=True, timeout=(10, read_timeout), headers=headers) as r:
        if r.status_code not in (200, 206):
            raise RuntimeError(f"下载失败：HTTP {r.status_code}")
        mode = "ab" if done > 0 else "wb"
        with open(tmp, mode) as f:
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
            _download_once(url, dest, tmp)
            # v1.5.11：全量包可能是 setup.exe（无 zip 顶层结构，大小校验已在 _download_once 做）；
            # zip 包才做顶层结构校验。exe 的完整性由 apply 阶段 SHA256 强校验兜底。
            if dest.lower().endswith(".zip") and not _zip_layout_ok(tmp):
                # 完整但结构坏：不能续传，清掉让下一轮重头下
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise RuntimeError("下载的压缩包结构异常（未找到 BAZZ.AGENT-win32-x64 顶层目录）。")
            os.replace(tmp, dest)
            with _dl_lock:
                _dl_state["done"] = _dl_state["total"] or _dl_state["done"]
                _dl_state["ready"] = True
                _dl_state["active"] = False
                _dl_state["error"] = ""
                _dl_state["ver"] = _tag_from_url(url)
            return
        except Exception as e:
            last_err = str(e)
            if attempt < DL_ATTEMPTS:
                with _dl_lock:
                    _dl_state["error"] = f"{last_err}（第 {attempt}/{DL_ATTEMPTS} 次失败，重试中…）"
                time.sleep(2 * attempt)
    with _dl_lock:
        _dl_state["error"] = last_err or "下载失败"
        _dl_state["active"] = False
        _dl_state["ready"] = False


def start_download(url: str) -> dict:
    """后台预下载更新包到 update-cache/<asset 文件名>。返回立即。

    幂等：· 已 ready → 直接回报已是就绪，不重启线程；
          · 进行中   → 回报 started:true（让前端轮询接管，不再视为重复任务错误）；
          · 否则     → 起新线程走「增量差分 →（回退）整包」管线。
    """
    name = url.rsplit("/", 1)[-1].split("?", 1)[0] or "bazz-agent.zip"
    dest = os.path.join(update_cache_dir(), name)
    with _dl_lock:
        # v1.3.6：ready 的包若属于别的版本（旧 tag），不复用 —— 视同未就绪重新下载，
        # 防止「立即更新」安装到跨版本前遗留的旧包。
        if _dl_state["ready"] and _dl_state.get("ver") == _tag_from_url(url):
            return {"started": True, "ready": True, "path": _dl_state["path"] or dest}
        if _dl_state["active"]:
            return {"started": True, "path": _dl_state["path"] or dest}
        _dl_state.update({"active": True, "ready": False, "error": "", "done": 0,
                          "total": 0, "path": dest, "started_at": time.time(), "stage": "delta",
                          "ver": _tag_from_url(url)})
    threading.Thread(target=_download_entry, args=(dest, url), daemon=True).start()
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
    # workspace 是否在应用目录内。v1.3.3 起工作区默认就在安装根下（<root>/workspace），
    # 即 ws_inside_root 是常态 —— 更新走「备份 WORKSPACE → 整目录替换 → 还原」，用户数据绝不随旧目录被删。
    # 若用户自定义外置（%APPDATA% 等），则纯程序替换，备份/还原自然跳过。
    ws_inside_root = _path_inside(ws, root)
    if ws_inside_root:
        ws_bak = os.path.join(parent, ".bazz-ws-backup")
        new_ws = os.path.join(new_root, os.path.relpath(ws, root)) if _path_inside(ws, root) else ws
        ps_ws = ws                       # 让 PS 走「备份 WORKSPACE → 替换 → 还原」逻辑
    else:
        ws_bak = ""
        new_ws = ws                      # 外置 workspace 在删除应用目录时不受影响，保持不变
        ps_ws = ""                       # 外置时 PS 的 workspace 备份/还原块为空 → 自然跳过
    # zip 落点保护：apply 前 zip 在 update-cache（现位于安装根 = 旧应用目录内），
    # 删旧目录前必须先挪出去，否则解压时 zip 已被删 → 更新必挂。
    if ws_inside_root and _path_inside(zip_path, ws):
        zip_bak = os.path.join(ws_bak, os.path.relpath(zip_path, ws))   # 随 workspace 备份一起挪走
    elif _path_inside(zip_path, root):
        zip_bak = os.path.join(parent, ".bazz-zip-bak" + os.path.splitext(zip_path)[1])  # 单独挪到父目录
    else:
        zip_bak = zip_path               # zip 在旧应用目录外，不受替换影响
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
# 4.5) zip 在旧应用目录内但不在 workspace 里（update-cache 在安装根）→ 删目录前单独挪到父目录
if ((Test-Path -LiteralPath $zip) -and ($zipBak -ne $zip)) {{
  try {{
    Move-Item -LiteralPath $zip -Destination $zipBak -ErrorAction Stop
    $zip = $zipBak
    L 'zip relocated out of old root'
  }} catch {{ L ('ERR zip-relocate: ' + $_.Exception.Message) }}
}}
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
# 7) 清理更新包（v1.3.6：update-cache 在安装根 $newRoot 下而非 workspace 内；zip 已在 $zipBak）
try {{
  $uc = Join-Path $newRoot 'update-cache'
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


def _write_installer_script(setup_path: str, wait_pid: int, backend_pid: int) -> str:
    """生成 setup.exe 静默安装更新脚本（v1.5.11，portable.zip 退役后的全量回退路径）。

    逻辑：等 Electron 退出 → 杀残留后端 → setup.exe 挪出安装根（防边读边写）→
    /VERYSILENT /DIR=<当前安装根> 原地覆盖（installer.iss 无 [InstallDelete]、workspace
    不在 [Files] 清单 → 用户数据不动）→ 校验新 exe → 清理 → 重启。
    """
    root = app_root()
    parent = os.path.dirname(root)
    exe = os.path.join(root, EXE_NAME)
    # 安装器边读 setup.exe 边往安装根写文件，setup 在 update-cache（安装根内）时先挪到父目录
    setup_bak = setup_path if not _path_inside(setup_path, root) else \
        os.path.join(parent, ".bazz-setup-tmp.exe")
    log = _log_path()
    log_cmd = ("Add-Content -Path '{log}' -Value ('[' + (Get-Date -Format o) + '] ' + $msg) "
               "-Encoding UTF8").replace("{log}", _ps1_quote(log))

    script = """$ErrorActionPreference = 'Continue'
function L($msg) {{ {L_BODY} }}
$oldRoot = '{root}'
$newExe  = '{newExe}'
$setup   = '{setup}'
$setupBak = '{setupBak}'
$waitPid = {wait}
$backendPid = {backend}
L '--- installer updater start ---'
# 1) 等 Electron 主进程退出（安装器 CloseApplications 也会兜底关闭）
if ($waitPid -gt 0) {{
  $gone = $false
  for ($i = 0; $i -lt 90; $i++) {{
    if (-not (Get-Process -Id $waitPid -ErrorAction SilentlyContinue)) {{ $gone = $true; break }}
    Start-Sleep -Seconds 2
  }}
  if (-not $gone) {{ L 'ERR wait-electron-timeout'; exit 3 }}
}}
# 2) 杀残留后端 / 双开实例（释放文件锁）
if ($backendPid -gt 0) {{ Stop-Process -Id $backendPid -Force -ErrorAction SilentlyContinue }}
Get-Process -Name 'BAZZ.AGENT','ScoutBackend' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
# 3) 前置校验 + 挪出安装根
if (-not (Test-Path -LiteralPath $setup)) {{ L 'ERR setup-missing'; exit 4 }}
if ($setupBak -ne $setup) {{
  try {{ Move-Item -LiteralPath $setup -Destination $setupBak -Force -ErrorAction Stop; $setup = $setupBak; L 'setup relocated out of root' }}
  catch {{ L ('ERR setup-relocate: ' + $_.Exception.Message) }}
}}
# 4) 静默安装：原地覆盖（/DIR 固定当前安装根，防便携解压目录无注册表记录时跑到默认路径）
L 'silent install start'
$dirArg = '/DIR="' + $oldRoot + '"'
$p = Start-Process -FilePath $setup -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/NOCANCEL',$dirArg -Wait -PassThru
L ('installer exit code: ' + $p.ExitCode)
if ($p.ExitCode -ne 0) {{ L 'ERR install-failed'; exit 6 }}
if (-not (Test-Path -LiteralPath $newExe)) {{ L 'ERR new-exe-missing'; exit 7 }}
L 'install ok'
# 5) 清理安装包与更新缓存
try {{
  Remove-Item -LiteralPath $setup -Force -ErrorAction SilentlyContinue
  Get-ChildItem -Path (Join-Path $oldRoot 'update-cache') -Include *.zip,*.exe -Recurse -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
}} catch {{}}
# 6) 重启新版本
try {{
  Start-Process -FilePath $newExe -WorkingDirectory $oldRoot
  L 'relaunch ok'
}} catch {{ L ('ERR relaunch: ' + $_.Exception.Message); exit 8 }}
""".format(L_BODY=log_cmd,
           root=_ps1_quote(root), newExe=_ps1_quote(exe),
           setup=_ps1_quote(setup_path), setupBak=_ps1_quote(setup_bak),
           wait=int(wait_pid), backend=int(backend_pid))
    ps1 = os.path.join(update_cache_dir(), "apply-update-setup.ps1")
    with open(ps1, "w", encoding="utf-8-sig") as f:
        f.write(script)
    return ps1


def apply(zip_path: str, wait_pid: int = 0) -> dict:
    """生成脚本并以 DETACHED 进程启动。仅打包态允许。"""
    if not is_packaged():
        return {"ok": False,
                "error": "自动更新仅适用于桌面便携版（打包态）。开发模式请手动拉取最新代码/重新发布。",
                "log": _log_path()}
    # v1.3.6 防线：zip 只能来自本机 update-cache（此前 apply 收任意路径，配合宽松 CORS
    # 可被诱导替换任意目录 —— 路由层已加校验，这里双保险）
    if not _path_inside(os.path.abspath(zip_path), update_cache_dir()):
        return {"ok": False, "error": "更新包路径不受信任（仅允许 update-cache 内的包）。",
                "log": _log_path()}
    if not zip_path or not os.path.exists(zip_path):
        return {"ok": False, "error": "更新包不存在，请先完成下载。", "log": _log_path()}
    root = app_root()
    log = _log_path()
    is_setup = zip_path.lower().endswith(".exe")   # v1.5.11：全量回退包 = setup.exe 静默安装
    try:
        # 应用前再确认一次包结构（仅 zip；exe 的完整性由下方 SHA256 强校验兜底），
        # 避免解压到一半才发现包坏
        if not is_setup and not _zip_layout_ok(zip_path):
            return {"ok": False, "error": "更新包结构异常（未找到 BAZZ.AGENT-win32-x64 顶层目录），请重新下载。",
                    "log": log}
        # SHA256 校验（防下载包被篡改/损坏）：
        #   · 本地「增量差分」构建的 zip → 构建时已逐文件校验清单 sha，整包 blob 与官方不同，
        #     直接跳过整包比对。
        #   · 官方整包（zip / setup.exe）→ 能从官 release 拿到校验和就强校验，不符即中止。
        #   拿不到（网络抖动 / 旧 release 未挂 SHA256SUMS）则仅忽略，不阻塞正常更新。
        if os.path.abspath(zip_path) in _PATCH_BUILT:
            print(f"[updater] 本地增量构建包，跳过整包 SHA256（逐文件校验已通过）：{zip_path}")
        else:
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
        ps1 = (_write_installer_script(zip_path, int(wait_pid or 0), os.getpid()) if is_setup
               else _write_updater_script(zip_path, int(wait_pid or 0), os.getpid()))
    except Exception as e:
        return {"ok": False, "error": f"生成更新脚本失败：{e}", "log": log}
    # v1.5.16 修复（实测复现）：DETACHED_PROCESS(0x8) 会让 powershell.exe 立即静默退出
    # （exit 0 且不执行 -File 脚本）—— 历次「点更新没反应、安装器从不运行」的真正根因。
    # 只保留 CREATE_NEW_PROCESS_GROUP|CREATE_NO_WINDOW：同样无窗口闪烁，脚本正常执行。
    flags = 0x00000200 | 0x08000000   # CREATE_NEW_PROCESS_GROUP|CREATE_NO_WINDOW
    # v1.5.13 修复：System32\WindowsPowerShell\v1.0 不在 CreateProcess 的固有搜索目录里，
    # 只能靠 PATH；Electron 拉起的后端若 PATH 被裁剪，裸 "powershell" 解析失败 → 应用退了安装器却没跑。
    # 改用 SystemRoot 绝对路径，找不到再退回 PATH。
    ps_exe = "powershell"
    if os.name == "nt":
        ps_exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                              "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        if not os.path.exists(ps_exe):
            ps_exe = "powershell"
    try:
        # 新一轮更新流程开始 → 清掉上次恢复安装的尝试计数
        try:
            os.remove(os.path.join(update_cache_dir(), "apply-attempts.txt"))
        except OSError:
            pass
        subprocess.Popen(
            [ps_exe, "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-WindowStyle", "Hidden", "-File", ps1],
            creationflags=flags if os.name == "nt" else 0,
            close_fds=True,
        )
    except Exception as e:
        return {"ok": False, "error": f"启动更新进程失败：{e}", "log": log}
    return {"ok": True, "started": True, "log": log,
            "tip": "更新脚本已在后台运行：应用会自动退出，完成后自动替换并重启，期间请勿关机。"}


def resume_pending_update() -> dict:
    """v1.5.13 兜底：上次会话 spawn PowerShell 静默失败（应用已退出但安装器没跑）时，
    开机检测 update-cache 遗留的 *-setup.exe + apply-update-setup.ps1 → 补跑安装。
    v1.5.17 版本守卫：遗留包版本 ≤ 本地版本时绝不安装（否则新版本装完开机又被旧包降级，
    v1.5.16 实测踩坑），直接清掉遗留包与陈旧脚本/分段残片。
    尝试计数防死循环：连续 3 次仍未成功则停止自动重试，保留现场供排查。
    安装成功后 ps1 会清掉 setup.exe；新一轮 apply() 会清掉计数文件。"""
    if not is_packaged():
        return {"resumed": False, "reason": "not-packaged"}
    cache = update_cache_dir()
    ps1 = os.path.join(cache, "apply-update-setup.ps1")
    try:
        setups = [os.path.join(cache, f) for f in os.listdir(cache)
                  if f.lower().endswith("-setup.exe")]
    except OSError:
        return {"resumed": False, "reason": "cache-unreadable"}
    if not (os.path.exists(ps1) and setups):
        return {"resumed": False}
    setup = setups[0]
    # 版本守卫：从文件名解析 BAZZ.AGENT-v<ver>-setup.exe；解析不出或 ≤ 本地版本 → 清理不安装
    m = re.search(r"v(\d+\.\d+\.\d+)", os.path.basename(setup))
    if m and _ver_tuple(m.group(1)) <= _ver_tuple(_local_version()):
        print(f"[updater] 遗留更新包 {os.path.basename(setup)} 不比当前版本新，清理跳过恢复安装")
        for junk in ([setup, ps1] +
                     [os.path.join(cache, f) for f in os.listdir(cache)
                      if f.endswith(".part") or re.search(r"\.part\.s\d+$", f)]):
            try:
                os.remove(junk)
            except OSError:
                pass
        return {"resumed": False, "reason": "stale-package"}
    cnt_file = os.path.join(cache, "apply-attempts.txt")
    try:
        with open(cnt_file, "r", encoding="utf-8") as f:
            n = int((f.read() or "0").strip() or "0")
    except Exception:
        n = 0
    if n >= 3:
        print("[updater] 连续 3 次恢复安装未成功，停止自动重试。"
              "可手动运行 update-cache\\apply-update-setup.ps1 并把 Temp\\bazz-updater.log 反馈排查")
        return {"resumed": False, "reason": "max-attempts"}
    try:
        with open(cnt_file, "w", encoding="utf-8") as f:
            f.write(str(n + 1))
    except Exception:
        pass
    r = apply(setup, wait_pid=0)
    print(f"[updater] 开机恢复安装（第 {n + 1} 次尝试）：{r}")
    return {"resumed": True, "attempt": n + 1, "result": r}
