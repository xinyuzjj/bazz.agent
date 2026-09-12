"""mihomo（Clash Meta）内核管理（v1.3.7）—— 给连不上的用户兜底。

- 一键下载 mihomo windows-amd64 内核（GitHub 直连失败自动走镜像），落 <安装根>/.system/kernel/；
- 订阅原始内容作为 proxy-provider 落盘，生成 config 后拉起内核；
- 内核在本地起混合端口（HTTP+SOCKS），应用流量走 127.0.0.1:<mixed> 转发，
  hysteria2/vmess/trojan/ss 等协议全部可用；
- 节点切换/测速走 mihomo 外部控制 API（external-controller）。
- 本地能直连 Binance 的用户完全不需要内核 —— 直连型 http/socks 节点由 requests 直接走。
"""
import atexit
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import zipfile

import requests

import workspace

KERNEL_DIR = os.path.join(workspace.SYSTEM_DIR, "kernel")
PROVIDERS_DIR = os.path.join(KERNEL_DIR, "providers")
MIHOMO_EXE = os.path.join(KERNEL_DIR, "mihomo.exe")
CONFIG_PATH = os.path.join(KERNEL_DIR, "config.yaml")
LOG_PATH = os.path.join(KERNEL_DIR, "kernel.log")
# v1.5.32：内核状态落盘（pid + 实际端口）。见 _recover() 说明。
STATE_PATH = os.path.join(KERNEL_DIR, "state.json")

# v1.3.8：安装包内置内核的位置（build-desktop.js 由 CI 下载后打进 <安装根>/.system/kernel/）
BUNDLED_KERNEL_EXE = os.path.join(workspace.app_root(), ".system", "kernel", "mihomo.exe")

GROUP_NAME = "BAZZ"
TEST_URL = "https://api.binance.com/api/v3/ping"

_LOCK = threading.RLock()
# 注意：以下三项都是**进程内**状态。mihomo 是独立进程，可能由上一个后端实例
# （或同机的第二个实例）拉起，因此任何依赖它们的判断都必须先走 _recover() 兜底，
# 否则「内核客观在跑」也会被判成没跑（v1.5.32 修复的正是这个 bug）。
_proc = None
_mixed_port = 0
_ctrl_port = 0
# v1.5.33：端口是「落盘恢复来的」还是「本进程 start() 刚写的」。_recover() 只清前者 ——
# 否则并发请求（前端轮询 /api/proxies）会在 start() 的 _write_config() → Popen() 之间
# 触发一次探活失败，把刚写好的端口清零，导致等待循环一直打 :0/version、最终报「内核启动超时」，
# 并把 mixed_port:0 写进 state.json。
_recovered = False

# 下载进度（前端轮询）
_dl = {"active": False, "total": 0, "done": 0, "error": "", "ready": False, "version": ""}

_MIRRORS = [
    "",  # 直连优先
    "https://gh-proxy.com/",
    "https://ghfast.top/",
    "https://ghproxy.net/",
]


# ---------------- 基础状态 ----------------

_bundled_adopted = False


def _adopt_bundled_kernel():
    """v1.3.8：把安装包内置内核接化为当前内核（仅首次尝试一次）。

    正常情况下内置内核就落在 <安装根>/.system/kernel/ = SYSTEM_DIR/kernel（同一位置，
    无需任何动作）。只有安装目录只读、SYSTEM_DIR 回退到 workspace 时，内置内核才
    「看得见摸不着」，这里把它拷贝到激活的 KERNEL_DIR。"""
    global _bundled_adopted
    if _bundled_adopted:
        return
    _bundled_adopted = True
    if os.path.isfile(MIHOMO_EXE) or not os.path.isfile(BUNDLED_KERNEL_EXE):
        return
    try:
        os.makedirs(KERNEL_DIR, exist_ok=True)
        shutil.copyfile(BUNDLED_KERNEL_EXE, MIHOMO_EXE)
    except OSError:
        pass


def is_installed():
    _adopt_bundled_kernel()
    return os.path.isfile(MIHOMO_EXE)


def _free_port(prefer):
    def _ok(p):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))
            return True
        except OSError:
            return False
        finally:
            s.close()
    if _ok(prefer):
        return prefer
    for p in range(prefer + 1, prefer + 20):
        if _ok(p):
            return p
    return prefer


def mixed_port():
    if not _mixed_port:
        _recover()          # v1.5.32：端口可能由上一个进程分配，先尝试恢复
    return _mixed_port


def _ctrl():
    return f"http://127.0.0.1:{_ctrl_port}"


def _probe_ctrl(port):
    """控制面探活：/version 返回 200 才算内核真的在跑。"""
    if not port:
        return False
    try:
        return requests.get(f"http://127.0.0.1:{port}/version", timeout=2).status_code == 200
    except Exception:
        return False


def _ports_from_config():
    """从落盘的 config.yaml 兜底解析端口（state.json 缺失/损坏时用）。"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            txt = f.read()
    except OSError:
        return 0, 0
    mp = re.search(r"^mixed-port:\s*(\d+)", txt, re.M)
    ec = re.search(r"^external-controller:\s*[\d.]*:(\d+)", txt, re.M)
    return (int(mp.group(1)) if mp else 0), (int(ec.group(1)) if ec else 0)


def _write_state():
    """把 pid + 实际端口落盘，供**其它进程**识别这个内核。"""
    try:
        os.makedirs(KERNEL_DIR, exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"pid": _proc.pid if _proc is not None else 0,
                       "mixed_port": _mixed_port, "ctrl_port": _ctrl_port}, f)
        os.replace(tmp, STATE_PATH)
    except OSError:
        pass


def _read_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        return int(d.get("pid") or 0), int(d.get("mixed_port") or 0), int(d.get("ctrl_port") or 0)
    except (OSError, ValueError, TypeError):
        return 0, 0, 0


def _clear_state():
    try:
        os.remove(STATE_PATH)
    except OSError:
        pass


def _recover():
    """v1.5.32：本进程没亲手拉起内核时，从落盘状态恢复端口并探活。

    背景（真实事故）：代理池里启用的是 vless 等**内核型**节点，端口由 mihomo 的
    _write_config() 动态分配并只存在进程内存里。后端一旦重启（或同机存在第二个实例、
    内核被上一实例拉成孤儿进程），新进程 _proc 恒为 None → is_running() 返回 False、
    mixed_port() 返回 0 → proxy_pool._apply_env() 走 else 分支把 HTTP(S)_PROXY
    **清空** → 所有技能子进程直连外网 → Node/undici 报 UND_ERR_CONNECT_TIMEOUT。
    用户侧表现：「代理池里节点明明是启用的、延迟也测得出，但广场发文/取数一律超时」。

    这里按 state.json → config.yaml 的顺序恢复端口，再用控制面探活确认内核真在跑。
    """
    global _mixed_port, _ctrl_port, _recovered
    if not _ctrl_port:
        _, mp, cp = _read_state()
        if not cp:
            mp, cp = _ports_from_config()
        _mixed_port, _ctrl_port = mp, cp
        _recovered = True
    if _probe_ctrl(_ctrl_port):
        return True
    # 探不通：清掉「恢复来的」缓存，下次重新解析（内核可能换过端口）。
    # ⚠️ 绝不能清 start() 刚写好的端口 —— 那会让本次启动必然超时（v1.5.33 修复的竞态）。
    if _recovered:
        _mixed_port = 0
        _ctrl_port = 0
        _recovered = False
    return False


def is_running():
    global _proc
    if _proc is not None:
        if _proc.poll() is not None:
            _proc = None        # 本进程拉起的内核已退出 → 落到 _recover() 看别处有没有
        else:
            return _probe_ctrl(_ctrl_port)
    return _recover()


def version():
    if not _ctrl_port:
        _recover()          # v1.5.32：端口可能由上一个进程分配
    try:
        r = requests.get(_ctrl() + "/version", timeout=2)
        if r.status_code == 200:
            return r.json().get("version", "") or ""
    except Exception:
        pass
    # 未运行时从 exe 取
    if is_installed():
        try:
            out = subprocess.run([MIHOMO_EXE, "-v"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace",
                                 timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            m = re.search(r"mihomo\s+([0-9.]+)", (out.stdout or "") + (out.stderr or ""))
            return m.group(1) if m else "已安装"
        except Exception:
            return "已安装"
    return ""


def status():
    running = is_running()      # 会顺带恢复端口，因此下面读 _mixed_port 是安全的
    return {"installed": is_installed(), "running": running,
            "version": version(), "mixed_port": _mixed_port if running else 0,
            "download": dict(_dl)}


# ---------------- 订阅 provider ----------------

def _provider_id(sub_url):
    return "sub_" + hashlib.md5(sub_url.encode("utf-8")).hexdigest()[:10]


def save_provider(sub_url, raw):
    """把订阅原始内容落盘给内核做 file provider。仅 Clash YAML 格式可直接用。"""
    os.makedirs(PROVIDERS_DIR, exist_ok=True)
    if "proxies:" not in raw:
        return  # base64/URI 列表格式内核 file provider 不直接吃，跳过
    path = os.path.join(PROVIDERS_DIR, _provider_id(sub_url) + ".yaml")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(raw)
    os.replace(tmp, path)


def _provider_files():
    if not os.path.isdir(PROVIDERS_DIR):
        return []
    return [f for f in os.listdir(PROVIDERS_DIR) if f.endswith(".yaml")]


# ---------------- 配置生成 / 启停 ----------------

def _write_config():
    global _mixed_port, _ctrl_port, _recovered
    _mixed_port = _free_port(7899)
    _ctrl_port = _free_port(9099)
    _recovered = False      # 端口由本进程权威写入，不再是「恢复值」
    providers = _provider_files()
    lines = [
        f"mixed-port: {_mixed_port}",
        "allow-lan: false",
        "mode: rule",
        "log-level: warning",
        f"external-controller: 127.0.0.1:{_ctrl_port}",
    ]
    if providers:
        lines.append("proxy-providers:")
        for f in providers:
            pid = f[:-5]
            lines += [
                f"  {pid}:",
                "    type: file",
                f"    path: providers/{f}",
                "    health-check:",
                "      enable: true",
                f"      url: {TEST_URL}",
                "      interval: 300",
            ]
    lines += [
        "proxy-groups:",
        f"  - name: {GROUP_NAME}",
        "    type: select",
    ]
    use_list = [f[:-5] for f in providers]
    if use_list:
        lines.append("    use:")
        for u in use_list:
            lines.append(f"      - {u}")
    lines.append("    proxies:")
    lines.append("      - DIRECT")
    lines += [
        "rules:",
        f"  - MATCH,{GROUP_NAME}",
    ]
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _kill_proc():
    global _proc
    if _proc is None:
        return
    try:
        # Windows：taskkill /T 连孙进程一起收，避免 mihomo 孤儿化
        subprocess.run(["taskkill", "/PID", str(_proc.pid), "/T", "/F"],
                       capture_output=True, timeout=10,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        try:
            _proc.kill()
        except Exception:
            pass
    _proc = None


def start():
    """拉起内核。已在跑则直接返回。"""
    global _proc
    with _LOCK:
        if is_running():
            return {"ok": True, "running": True, "port": _mixed_port}
        if not is_installed():
            return {"ok": False, "error": "内核未下载，请先点「下载内核」。"}
        os.makedirs(KERNEL_DIR, exist_ok=True)
        _write_config()
        logf = open(LOG_PATH, "a", encoding="utf-8")
        try:
            _proc = subprocess.Popen(
                [MIHOMO_EXE, "-d", KERNEL_DIR, "-f", CONFIG_PATH],
                cwd=KERNEL_DIR, stdout=logf, stderr=logf,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            return {"ok": False, "error": f"内核启动失败：{e}"}
        _write_state()      # v1.5.32：落盘 pid + 端口，供后续进程（重启/第二实例）识别
        # 等控制面就绪（最多 20s）
        for _ in range(40):
            try:
                r = requests.get(_ctrl() + "/version", timeout=1)
                if r.status_code == 200:
                    return {"ok": True, "running": True, "port": _mixed_port}
            except Exception:
                pass
            if _proc.poll() is not None:
                _proc = None
                _clear_state()
                return {"ok": False, "error": "内核进程启动后退出，详见 kernel.log。"}
            time.sleep(0.5)
        return {"ok": False, "error": "内核启动超时（控制面无响应），详见 kernel.log。"}


def _pid_image(pid):
    """取某 PID 的镜像名（小写）；取不到返回空串。"""
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lines = [ln for ln in (out.stdout or "").splitlines() if ln.strip()]
        if not lines:
            return ""
        return lines[0].split(",")[0].strip().strip('"').lower()
    except Exception:
        return ""


def _kill_recovered():
    """v1.5.32：内核由**上一个进程**拉起时本进程 _proc 为空，从 state.json 取 pid 收掉。

    先核对镜像名确为 mihomo.exe —— PID 会被系统复用，不核对可能误杀无关进程。"""
    if _proc is not None:
        return
    pid, _, _ = _read_state()
    if pid <= 0 or _pid_image(pid) != "mihomo.exe":
        return
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                       timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass


def stop():
    global _mixed_port, _ctrl_port, _recovered
    with _LOCK:
        _kill_proc()
        _kill_recovered()
        _clear_state()
        # v1.5.33：端口随内核一起失效，必须清掉，否则 mixed_port() 会返回过期端口
        _mixed_port = 0
        _ctrl_port = 0
        _recovered = False
    return {"ok": True}


def ensure_running():
    if not is_running():
        r = start()
        if not r.get("ok"):
            raise RuntimeError(r.get("error", "内核启动失败"))


def select(node_name):
    """切换 BAZZ 组当前节点。"""
    r = requests.put(_ctrl() + f"/proxies/{GROUP_NAME}",
                     json={"name": node_name}, timeout=5)
    return r.status_code in (200, 204)


def _provider_proxies():
    """{provider_name: [ {name, history}, ... ]}，只取订阅型 provider（跳过 default）。"""
    out = {}
    try:
        data = requests.get(_ctrl() + "/providers/proxies", timeout=5).json()
        for pname, p in (data.get("providers") or {}).items():
            if pname == "default":
                continue
            nodes = p.get("proxies") or []
            if nodes:
                out[pname] = [{"name": n.get("name"),
                               "history": n.get("history") or []} for n in nodes]
    except Exception:
        pass
    return out


def _find_provider_of(node_name):
    """返回节点所属 provider 名；找不到返回 None。"""
    for pname, nodes in _provider_proxies().items():
        if any(n["name"] == node_name for n in nodes):
            return pname
    return None


def _history_delay(nodes, node_name):
    for n in nodes:
        if n["name"] == node_name and n["history"]:
            d = n["history"][-1].get("delay")
            if isinstance(d, (int, float)) and d > 0:
                return int(d)
    return None


def _healthcheck(provider_name, timeout_ms):
    """触发某个 provider 全节点健康检查（204 = 已在同步执行完）。"""
    try:
        r = requests.get(_ctrl() + f"/providers/proxies/{requests.utils.quote(provider_name)}/healthcheck",
                         params={"url": TEST_URL, "timeout": timeout_ms},
                         timeout=timeout_ms / 1000 + 6)
        return r.status_code in (200, 204)
    except Exception:
        return False


def node_delay(node_name, timeout_ms=8000):
    """单节点延迟（ms）。新版 mihomo 中订阅 provider 节点不在 /proxies/<name>/delay 暴露，
    走 provider healthcheck + 读取节点 history。失败返回 None。"""
    try:
        pname = _find_provider_of(node_name)
        if not pname:
            return None
        _healthcheck(pname, timeout_ms)
        return _history_delay(_provider_proxies().get(pname, []), node_name)
    except Exception:
        return None


def group_delays(timeout_ms=8000):
    """全部订阅节点测速：{节点名: 延迟ms}。对每个 provider 触发 healthcheck 后汇总 history。"""
    result = {}
    try:
        providers = _provider_proxies()
        for pname in providers:
            _healthcheck(pname, timeout_ms)
        for nodes in _provider_proxies().values():
            for n in nodes:
                d = _history_delay([n], n["name"])
                if d:
                    result[n["name"]] = d
    except Exception:
        pass
    return result


# ---------------- 下载内核 ----------------

def _find_asset():
    r = requests.get("https://api.github.com/repos/MetaCubeX/mihomo/releases/latest",
                     timeout=15, headers={"User-Agent": "BAZZ.AGENT"})
    r.raise_for_status()
    rel = r.json()
    tag = rel.get("tag_name", "")
    for a in rel.get("assets", []):
        n = a.get("name", "")
        # 标准 windows-amd64 包（排除 -compatible- / arm 等变体）
        if re.match(r"^mihomo-windows-amd64-v[\d.]+\.zip$", n):
            return tag, a["browser_download_url"], a.get("size", 0)
    raise RuntimeError("未在 mihomo 最新 Release 中找到 windows-amd64 包。")


def _download_one(url, dest, total_hint):
    r = requests.get(url, timeout=30, stream=True,
                     headers={"User-Agent": "BAZZ.AGENT"})
    r.raise_for_status()
    total = int(r.headers.get("content-length", 0) or total_hint or 0)
    done = 0
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=256 * 1024):
            if not chunk:
                continue
            f.write(chunk)
            done += len(chunk)
            with _LOCK:
                _dl["done"] = done
                _dl["total"] = total
    return done


def _download_worker():
    zip_path = os.path.join(KERNEL_DIR, "mihomo.zip")
    os.makedirs(KERNEL_DIR, exist_ok=True)
    try:
        tag, gh_url, size = _find_asset()
        last_err = ""
        for prefix in _MIRRORS:
            url = prefix + gh_url if prefix else gh_url
            try:
                with _LOCK:
                    _dl["error"] = "" if not prefix else f"直连失败，正在尝试镜像 {prefix} …"
                _download_one(url, zip_path, size)
                last_err = ""
                break
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                continue
        if last_err:
            raise RuntimeError(f"全部下载源均失败（{last_err}）。可稍后重试，或手动下载 mihomo.exe 放入 {KERNEL_DIR}")
        # 解压找内核 exe（包内名为 mihomo-windows-amd64.exe 等变体）
        with zipfile.ZipFile(zip_path) as zf:
            exe_member = next((n for n in zf.namelist()
                               if re.search(r"mihomo[\w\-]*\.exe$", n.replace("\\", "/").lower())), None)
            if not exe_member:
                raise RuntimeError("压缩包内未找到 mihomo.exe。")
            with zf.open(exe_member) as src, open(MIHOMO_EXE, "wb") as dst:
                while True:
                    b = src.read(1024 * 1024)
                    if not b:
                        break
                    dst.write(b)
        try:
            os.remove(zip_path)
        except OSError:
            pass
        ver = version()
        with _LOCK:
            _dl.update({"active": False, "ready": True, "error": "", "version": ver or tag})
    except Exception as e:
        with _LOCK:
            _dl.update({"active": False, "ready": False, "error": str(e)[:200]})


def start_download():
    """后台下载内核（幂等）。"""
    with _LOCK:
        if is_installed():
            return {"ok": True, "ready": True, "version": version()}
        if _dl["active"]:
            return {"ok": True, "active": True}
        _dl.update({"active": True, "ready": False, "error": "", "done": 0, "total": 0})
    threading.Thread(target=_download_worker, daemon=True).start()
    return {"ok": True, "active": True}


def download_status():
    with _LOCK:
        return dict(_dl)


def _shutdown():
    """退出时收掉内核并清掉落盘状态（v1.5.32）。"""
    with _LOCK:
        _kill_proc()
        _clear_state()


atexit.register(_shutdown)
