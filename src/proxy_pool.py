"""代理池（v1.3.7）—— 导入代理 / 订阅解析 / 延迟测速 / 一键启用 / 可选 mihomo 内核。

设计：
- 持久化在 workspace/proxies.json；启用代理后向 os.environ 注入 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY，
  requests（trust_env 默认开）立即生效；baw 等子进程继承环境变量。
- 两类节点：
  · 直连型：http / https / socks4 / socks5（socks 需 PySocks），requests 直接走；
  · 内核型：hysteria2 / vmess / trojan / ss / vless 等 —— 需 mihomo（Clash Meta）内核。
    用户一键「下载内核」后，内核在本地起混合端口（HTTP/SOCKS），应用流量走本地端口转发。
    本地能直连 Binance 的用户不需要内核，池子里只用直连型节点即可。
- 订阅（Clash YAML / base64 列表）自动解析分流；原始订阅内容落盘给内核做 provider。
- 测速：直连型真实转发 GET https://api.binance.com/api/v3/ping；内核型走 mihomo 外部控制 API。
"""
import base64
import hashlib
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

# 显式导入让 PyInstaller 静态分析收集 PySocks（urllib3 是惰性 import socks，会漏收集）
try:
    import socks  # noqa: F401
    _HAS_SOCKS = True
except Exception:
    _HAS_SOCKS = False

import workspace

POOL_PATH = os.path.join(workspace.WORKSPACE, "proxies.json")
TEST_URL = "https://api.binance.com/api/v3/ping"
TEST_TIMEOUT = 8.0
DEAD_THRESHOLD = 3          # 连续失败 3 次 → dead
_DIRECT_PROTO = "direct"    # 直连（不走代理）

# requests 可直接使用的代理协议；其余（hysteria2/vmess/...）需 mihomo 内核
_DIRECT_PROTOS = {"http", "https", "socks4", "socks5", "socks"}
_LOCK = threading.RLock()
_state = {"active_id": "", "entries": []}

# 节点来源标签
SRC_MANUAL = "手动导入"
SRC_SUB = "订阅"


# ---------------- 持久化 ----------------

def _load():
    with _LOCK:
        try:
            with open(POOL_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            _state["active_id"] = data.get("active_id", "") or ""
            _state["entries"] = data.get("entries", []) or []
        except (OSError, json.JSONDecodeError):
            _state["active_id"] = ""
            _state["entries"] = []


def _save():
    tmp = POOL_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"active_id": _state["active_id"], "entries": _state["entries"]},
                  f, ensure_ascii=False, indent=1)
    os.replace(tmp, POOL_PATH)


def bootstrap():
    """后端启动时调用：加载配置并把已启用代理注入环境变量。"""
    _load()
    _apply_env()
    _revive_kernel_async()


def _revive_kernel_async():
    """v1.5.32：active 是内核型节点且内核已安装 —— 后台确保内核在跑并选中该节点。

    为什么必须做：内核型节点（vless / hysteria2 / vmess / trojan …）的代理入口是 mihomo
    的本地混合端口，内核不在跑时 proxy_url() 返回 None，_apply_env() 会把 HTTP(S)_PROXY
    **清空**。于是「重启 APP 后代理池仍显示节点已启用、延迟也测得出，但技能实际全部直连
    超时（UND_ERR_CONNECT_TIMEOUT）」。
    放后台线程是为了不让 mihomo 启动等待（最多 20s）阻塞后端启动。"""
    try:
        e = active_entry()
        if not e or e.get("direct", True):
            return
        if not _kernel().is_installed():
            return
    except Exception:
        return

    def _work():
        try:
            k = _kernel()
            k.ensure_running()          # 已在跑则立即返回（含被上一进程拉起的孤儿内核）
            k.select(e.get("kernel_name") or e["name"])   # 内核重启后 selector 会回到默认，需重选
            _apply_env()
        except Exception:
            pass

    threading.Thread(target=_work, daemon=True).start()


# ---------------- 解析 ----------------

def _new_entry(proto, server, port, username="", password="", name="",
               group="", source=SRC_MANUAL, sub_url="", direct=None):
    """direct=True → requests 直连型；False → 需 mihomo 内核型。"""
    if direct is None:
        direct = proto in _DIRECT_PROTOS
    return {
        "id": uuid.uuid4().hex[:12],
        "name": name or (f"{proto}://{server}:{port}" if direct else f"{proto} {server}:{port}"),
        "group": group or "-",
        "source": source,
        "sub_url": sub_url,
        "kernel_name": name if not direct else "",   # 内核 selector 用的 Clash 节点名
        "proto": proto,
        "server": server,
        "port": int(port) if str(port).isdigit() else port,
        "username": username,
        "password": password,
        "direct": direct,                            # True=requests 直连；False=走内核
        "note": "" if direct else "需内核（一键下载 mihomo 内核后可用）",
        "latency_ms": None,
        "status": "untested",          # untested / ok / dead
        "fails": 0,
        "last_tested": 0,
        "error": "",
    }


def _parse_uri(line, group="", source=SRC_MANUAL, sub_url=""):
    """解析单行代理。返回 entry 或 None。"""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    low = line.lower()
    # scheme:// 形式
    m = re.match(r"^([a-z0-9]+)://(.+)$", line)
    if m:
        proto, rest = m.group(1).lower(), m.group(2)
        if proto in _DIRECT_PROTOS:
            try:
                u = urlparse(line)
                return _new_entry("socks5" if proto == "socks" else proto,
                                  u.hostname or "", u.port or 0,
                                  u.username or "", u.password or "",
                                  group=group, source=source, sub_url=sub_url)
            except Exception:
                return None
        # 内核协议（vmess/ss/trojan/hysteria2/hy2/vless...）：尽力提取 server/port 展示，
        # 实际转发由内核按订阅原始内容处理（URI 形式未提供内核配置，仅展示）
        server, port = "", ""
        mm = re.match(r"^([^@/\s]+@)?([a-zA-Z0-9.\-]+):(\d+)", rest)
        if mm:
            server, port = mm.group(2), mm.group(3)
        nm = line.split("://", 1)[0].upper()
        return _new_entry(proto, server, port, name=nm, group=group, source=source,
                          sub_url=sub_url, direct=False)
    # host:port 形式（默认 http 代理）；可带 user:pass@ 前缀
    m2 = re.match(r"^(?:([^:@\s]+):([^@\s]+)@)?([a-zA-Z0-9.\-]+):(\d+)$", line)
    if m2:
        user, pw, host, port = m2.group(1), m2.group(2), m2.group(3), m2.group(4)
        return _new_entry("http", host, port, user or "", pw or "",
                          group=group, source=source, sub_url=sub_url)
    return None


def _yaml_scalar(v):
    """去掉 YAML 标量引号/空白。"""
    v = v.strip()
    if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
        return v[1:-1]
    return v


def _parse_flow_map(s):
    """解析 `k: v, k: "v"` 流映射（值内含逗号/引号的简化处理）。"""
    kv = {}
    for km in re.finditer(r'([a-zA-Z0-9_\-]+)\s*:\s*("([^"]*)"|\'([^\']*)\'|([^,]+))\s*(?:,|$)', s):
        val = km.group(3) if km.group(3) is not None else (km.group(4) if km.group(4) is not None else km.group(5))
        kv[km.group(1)] = _yaml_scalar(val or "")
    return kv


def _entry_from_kv(kv, group, source, sub_url):
    ptype = (kv.get("type") or "").lower()
    server = kv.get("server") or ""
    port = kv.get("port") or ""
    if not server or not port:
        return None
    name = kv.get("name") or ""
    user = kv.get("username") or ""
    pw = kv.get("password") or ""
    if ptype in ("http", "https"):
        return _new_entry(ptype, server, port, user, pw, name=name,
                          group=group, source=source, sub_url=sub_url)
    if ptype in ("socks5", "socks"):
        return _new_entry("socks5", server, port, user, pw, name=name,
                          group=group, source=source, sub_url=sub_url)
    if ptype == "socks4":
        return _new_entry("socks4", server, port, user, pw, name=name,
                          group=group, source=source, sub_url=sub_url)
    return _new_entry(ptype or "unknown", server, port, name=name,
                      group=group, source=source, sub_url=sub_url, direct=False)


def _parse_clash_yaml(text, group, source, sub_url):
    """极简 Clash YAML 解析：吃 `proxies:` 段，兼容流映射（- {k: v}）与块式（- name: x 换行 k: v）。
    不依赖 PyYAML；只提取节点需要的字段。"""
    lines = text.splitlines()
    start = None
    base_indent = 0
    for i, raw in enumerate(lines):
        m = re.match(r"^(\s*)proxies\s*:\s*$", raw.rstrip())
        if m:
            start = i + 1
            base_indent = len(m.group(1))
            break
    if start is None:
        return []

    entries = []
    chunk = None      # 块式节点行缓冲
    flow_buf = None   # 跨行流映射缓冲

    def _flush_chunk():
        nonlocal chunk, flow_buf
        kv = None
        if flow_buf is not None:
            kv = _parse_flow_map(flow_buf.strip("{} "))
        elif chunk:
            kv = {}
            for cl in chunk:
                km = re.match(r"^\s*([a-zA-Z0-9_\-]+)\s*:\s*(.*)$", cl)
                if km:
                    kv[km.group(1)] = _yaml_scalar(km.group(2))
        if kv:
            e = _entry_from_kv(kv, group, source, sub_url)
            if e:
                entries.append(e)
        chunk = None
        flow_buf = None

    for raw in lines[start:]:
        line = raw.rstrip()
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        # 段结束：同级或更小缩进的非列表键
        if stripped and not stripped.startswith("-") and indent <= base_indent \
                and re.match(r"^[a-zA-Z_][\w\-]*\s*:", stripped):
            break
        # 新节点（"- " 缩进须大于段键缩进）
        hm = re.match(r"^(\s*)-\s+(.*)$", line)
        if hm and len(hm.group(1)) > base_indent:
            _flush_chunk()
            rest = hm.group(2).strip()
            if rest.startswith("{"):
                if rest.count("{") > rest.count("}"):
                    flow_buf = rest
                    chunk = []
                else:
                    e = _entry_from_kv(_parse_flow_map(rest.strip("{} ")), group, source, sub_url)
                    if e:
                        entries.append(e)
                    chunk = []  # 占位，表示段内已开始
            else:
                chunk = [rest]
            continue
        if flow_buf is not None:
            flow_buf += " " + stripped
            if flow_buf.count("{") <= flow_buf.count("}"):
                e = _entry_from_kv(_parse_flow_map(flow_buf.strip("{} ")), group, source, sub_url)
                if e:
                    entries.append(e)
                flow_buf = None
        elif chunk is not None and stripped:
            chunk.append(stripped)
    _flush_chunk()
    return entries


def _parse_subscription(content, group, source, sub_url):
    """订阅内容：base64 列表 或 Clash YAML。"""
    txt = content.strip()
    # base64（整段无空格、可解码出含 :// 的行）
    if "proxies:" not in txt and re.match(r"^[A-Za-z0-9+/=\r\n]+$", txt):
        try:
            decoded = base64.b64decode(txt + "===").decode("utf-8", "ignore")
            if "://" in decoded:
                txt = decoded
        except Exception:
            pass
    if "proxies:" in txt:
        return _parse_clash_yaml(txt, group, source, sub_url)
    # 一行一个 URI
    out = []
    for line in re.split(r"[\r\n]+", txt):
        e = _parse_uri(line, group=group, source=source, sub_url=sub_url)
        if e:
            out.append(e)
    return out


# ---------------- 导入 ----------------

def _dedup_key(e):
    # 内核节点按「内核节点名 + 订阅」去重（同订阅刷新）；直连节点按 proto+server+port+user
    if not e.get("direct", True):
        return ("k", e.get("kernel_name") or e["name"], e.get("sub_url", ""))
    return (e["proto"], e["server"], str(e["port"]), e.get("username", ""))


def _merge(new_entries):
    """合并进池：同节点保留已有测速结果。"""
    added, updated = 0, 0
    existing = {_dedup_key(e): e for e in _state["entries"]}
    for e in new_entries:
        k = _dedup_key(e)
        if k in existing:
            old = existing[k]
            # 订阅刷新：更新名称/分组/来源/内核名，保留测速
            old["name"] = e["name"] or old["name"]
            old["group"] = e["group"]
            old["source"] = e["source"]
            old["sub_url"] = e["sub_url"] or old.get("sub_url", "")
            old["kernel_name"] = e.get("kernel_name") or old.get("kernel_name", "")
            old["direct"] = e.get("direct", old.get("direct", True))
            old["note"] = e["note"]
            updated += 1
        else:
            _state["entries"].append(e)
            existing[k] = e
            added += 1
    return added, updated


def import_text(text, group=""):
    """批量粘贴导入。返回 (added, updated, entries)。"""
    with _LOCK:
        parsed = []
        for line in re.split(r"[\r\n]+", text or ""):
            e = _parse_uri(line, group=group or "-", source=SRC_MANUAL)
            if e:
                parsed.append(e)
        added, updated = _merge(parsed)
        _save()
        return added, updated, parsed


def import_subscription(url):
    """拉订阅 URL 导入。直连拉取（订阅站本身不需要代理）；原始内容落盘给内核做 provider。"""
    if not url:
        raise RuntimeError("订阅地址为空。")
    host = urlparse(url).hostname or url
    group = host
    r = requests.get(url, timeout=15, headers={"User-Agent": "clash.meta"})
    r.raise_for_status()
    raw = r.text
    # 原始订阅落盘（内核 proxy-provider 用），失败不影响池导入
    try:
        import proxy_kernel
        proxy_kernel.save_provider(url, raw)
    except Exception:
        pass
    entries = _parse_subscription(raw, group=group, source=f"{host} 订阅", sub_url=url)
    with _LOCK:
        added, updated = _merge(entries)
        _save()
    return {"added": added, "updated": updated, "parsed": len(entries), "group": host}


def refresh_subscriptions():
    """刷新全部已存订阅 URL。"""
    urls = sorted({e.get("sub_url") for e in _state["entries"] if e.get("sub_url")})
    results = []
    for u in urls:
        try:
            results.append({"url": u, **import_subscription(u)})
        except Exception as e:
            results.append({"url": u, "error": str(e)[:120]})
    return results


# ---------------- 测速 ----------------

def _kernel():
    import proxy_kernel
    return proxy_kernel


def _direct_url(e):
    """直连型节点的 requests URL。"""
    proto = e["proto"]
    scheme = "socks5h" if proto == "socks5" else ("socks4" if proto == "socks4" else "http")
    auth = ""
    if e.get("username"):
        auth = e["username"] + (":" + e["password"] if e.get("password") else "") + "@"
    return f"{scheme}://{auth}{e['server']}:{e['port']}"


def proxy_url(e):
    """返回 requests 可用的代理 URL：直连型 → 节点本身；内核型 → 内核本地混合端口；否则 None。"""
    if not e:
        return None
    if e.get("direct", True):
        return _direct_url(e)
    # 内核型：内核运行中 → 走本地混合端口
    try:
        k = _kernel()
        if k.is_running():
            return f"http://127.0.0.1:{k.mixed_port()}"
    except Exception:
        pass
    return None


def _mark(e, ok, latency=None, err=""):
    if ok:
        e["latency_ms"] = latency
        e["status"] = "ok"
        e["fails"] = 0
        e["error"] = ""
    else:
        e["latency_ms"] = None
        e["fails"] = e.get("fails", 0) + 1
        e["status"] = "dead" if e["fails"] >= DEAD_THRESHOLD else "untested"
        e["error"] = err[:120]
    e["last_tested"] = int(time.time())


def test_entry(e):
    """测速：直连型经代理 GET Binance ping；内核型走 mihomo 外部控制 API。"""
    if e.get("direct", True):
        if e["proto"].startswith("socks") and not _HAS_SOCKS:
            _mark(e, False, err="缺少 PySocks 依赖，无法走 SOCKS 代理")
            return e
        purl = _direct_url(e)
        t0 = time.time()
        try:
            r = requests.get(TEST_URL, timeout=TEST_TIMEOUT,
                             proxies={"http": purl, "https": purl})
            if r.status_code == 200:
                _mark(e, True, latency=int((time.time() - t0) * 1000))
            else:
                _mark(e, False, err=f"HTTP {r.status_code}")
        except Exception as ex:
            _mark(e, False, err=str(ex))
        return e
    # 内核型
    try:
        k = _kernel()
        if not k.is_running():
            _mark(e, False, err="内核未运行，请先下载并启动内核")
            return e
        ms = k.node_delay(e.get("kernel_name") or e["name"])
        if ms is not None:
            _mark(e, True, latency=int(ms))
        else:
            _mark(e, False, err="内核测速超时或节点不可达")
    except Exception as ex:
        _mark(e, False, err=str(ex))
    return e


def test_all(ids=None):
    """并发测速：直连型走 requests，内核型批量走内核控制 API。"""
    with _LOCK:
        targets = [dict(e) for e in _state["entries"]
                   if (ids is None or e["id"] in ids)]
    direct_nodes = [e for e in targets if e.get("direct", True)]
    kernel_nodes = [e for e in targets if not e.get("direct", True)]

    results = []
    # 直连型并发
    def _run(e):
        test_entry(e)
        return e
    with ThreadPoolExecutor(max_workers=10) as ex:
        for e in ex.map(_run, direct_nodes):
            results.append({"id": e["id"], "status": e["status"], "latency_ms": e.get("latency_ms")})
    # 内核型：一次 group delay 批量拿
    if kernel_nodes:
        delay_map = {}
        try:
            k = _kernel()
            if k.is_running():
                delay_map = k.group_delays() or {}
        except Exception:
            delay_map = {}
        for e in kernel_nodes:
            ms = delay_map.get(e.get("kernel_name") or e["name"])
            if isinstance(ms, int) and ms > 0:
                _mark(e, True, latency=ms)
            else:
                _mark(e, False, err="内核测速超时或节点不可达" if k.is_running() else "内核未运行")
            results.append({"id": e["id"], "status": e["status"], "latency_ms": e.get("latency_ms")})
    # 把测速结果写回 state（targets 是副本）
    with _LOCK:
        by_id = {t["id"]: t for t in targets}
        for e in _state["entries"]:
            t = by_id.get(e["id"])
            if t:
                e["latency_ms"] = t["latency_ms"]
                e["status"] = t["status"]
                e["fails"] = t["fails"]
                e["error"] = t["error"]
                e["last_tested"] = t["last_tested"]
        _save()
    return results


# ---------------- 启用 / 环境注入 ----------------

# v1.5.30：必须绕过代理的本机地址。
# 技能（coin-report / market-data 等）的唯一取数入口就是本机后端 127.0.0.1:8080/8081，
# 一旦这些地址被送进代理，技能会全部报「本地后端不可达」，而且换多快的节点都救不回来。
_LOCAL_BYPASS = ("127.0.0.1", "localhost", "::1")


def _ensure_no_proxy():
    """把本机地址强制并入 NO_PROXY（大小写两个键都写）。

    背景：proxy-preload.cjs 装的是 undici 的 EnvHttpProxyAgent，它只认 NO_PROXY 环境变量。
    实测（死代理探针）：无 NO_PROXY 时对 127.0.0.1 的 fetch 会被送进代理并失败；
    设了 NO_PROXY=127.0.0.1,localhost 才正常绕过。
    旧实现用 os.environ.setdefault —— 用户机器上常已有 NO_PROXY（代理客户端/系统注入，
    且可能是空字符串），setdefault 不会覆盖，于是本机后端照样被代理劫持。
    因此这里必须做「并集」，而不是「缺省填充」。
    """
    cur = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    parts = [p.strip() for p in cur.split(",") if p.strip()]
    lowered = {p.lower() for p in parts}
    for host in _LOCAL_BYPASS:
        if host.lower() not in lowered:
            parts.append(host)
    val = ",".join(parts)
    os.environ["NO_PROXY"] = val
    os.environ["no_proxy"] = val


def _apply_env():
    """把 active 代理写入进程环境变量（requests 与子进程继承生效）；直连则清除。"""
    e = active_entry()
    purl = proxy_url(e)
    if e and purl:
        # socks5h 用于 ALL_PROXY（DNS 也走代理）
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            os.environ[k] = purl
        for k in ("ALL_PROXY", "all_proxy"):
            os.environ[k] = purl
        _ensure_no_proxy()
    else:
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                  "http_proxy", "https_proxy", "all_proxy"):
            os.environ.pop(k, None)
    _ensure_node_preload()


def _ensure_node_preload():
    """v1.3.8：给 Node 子进程（baw CLI 等）挂 fetch 代理补丁。

    Node 20 的全局 fetch（内置 undici）不读 HTTP(S)_PROXY env —— 代理池启用后
    env 注入对 baw 无效（仍直连，二维码生成失败），只有系统级 TUN/全局才拦得到。
    通过 NODE_OPTIONS 预加载 runtime/proxy-preload.cjs（undici EnvHttpProxyAgent），
    子进程启动时若存在代理 env 即接管全部 fetch 流量。无代理 env 时脚本自动跳过，
    因此常挂不影响直连场景。"""
    preload = os.path.join(workspace.RUNTIME_DIR, "proxy-preload.cjs")
    if not os.path.isfile(preload):
        return  # dev / runtime 缺失：跳过（baw 走系统 PATH，代理由用户自行解决）
    flag = '--require="' + preload.replace("\\", "/") + '"'
    cur = (os.environ.get("NODE_OPTIONS") or "").strip()
    if "proxy-preload.cjs" in cur:
        return  # 已挂过
    os.environ["NODE_OPTIONS"] = (cur + " " + flag) if cur else flag


def active_entry():
    with _LOCK:
        aid = _state["active_id"]
        if not aid:
            return None
        for e in _state["entries"]:
            if e["id"] == aid:
                return e
        return None


def active_url():
    """供 updater 等显式会话使用：返回代理 URL 或空串（直连）。"""
    return proxy_url(active_entry())


def apply_env():
    """v1.5.32：公开入口 —— 内核启停后由 desktop_app 调用，重新同步进程代理环境变量。

    此前 /api/proxies/kernel/start 只拉起内核却不注入 env，用户点「启动内核」后
    HTTP(S)_PROXY 仍是空的，技能照样直连超时。"""
    _apply_env()
    return active_url()


_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "NODE_OPTIONS")


def env_snapshot():
    """v1.5.32：诊断用 —— 返回当前**实际生效**的代理相关环境变量。

    排障时最关键的一句话是「代理到底注入进程了没有」，此前只能靠翻日志猜。
    NO_PROXY 必须包含 127.0.0.1，否则本机后端自身会被代理劫持。"""
    return {k: (os.environ.get(k) or "") for k in _ENV_KEYS}


_NET_TEST_TIMEOUT = 6.0

# v1.5.23：发文全链路端点——此前只 ping api.binance.com，节点 ping 通但
# www.binance.com / S3 图片域超时时检测照样放行（RUNE 发文 4 连败实测踩坑）
_REACH_URLS = ("https://www.binance.com/", "https://public.bnbstatic.com/")


def _url_alive(url, timeout=_NET_TEST_TIMEOUT):
    """实测某代理 URL 能否连通 Binance 发文全链路（socks 无 PySocks 直接判不可用）。

    api ping 必须返回 200；_REACH_URLS 里任何 HTTP 响应（含 404/307）都算连通——
    关键是不经代理转发就到不了对端。三端点全过才算该节点可用。"""
    if not url:
        return False
    proto = (urlparse(url).scheme or "").lower()
    if proto.startswith("socks") and not _HAS_SOCKS:
        return False
    proxies = {"http": url, "https": url}
    try:
        r = requests.get(TEST_URL, proxies=proxies, timeout=timeout)
        if r.status_code != 200:
            return False
    except Exception:
        return False
    for u in _REACH_URLS:
        try:
            requests.get(u, proxies=proxies, timeout=timeout)  # 有响应即连通，不看状态码
        except Exception:
            return False
    return True


def ensure_working_proxy(max_candidates: int = 6):
    """网络失败兜底（v1.5.19）：确保存在一个「实测能连 Binance」的代理节点并激活它。

    顺序：active 节点实测 → 按缓存延迟升序逐个 set_active + 实测（内核型自动拉起内核
    并切 selector，同时注入 env + NODE_OPTIONS preload）。全部失败则恢复原 active 并
    返回 ""（池子为空 / 全挂时调用方维持原状，把直连错误如实抛给用户）。
    返回值：可用代理 URL，或 ""（无可用代理）。"""
    with _LOCK:
        original_id = _state["active_id"]
        entries = [dict(e) for e in _state["entries"]]
    if not entries:
        return ""
    # 1) 当前 active 先实测（最常见的快路径）
    cur = active_url()
    if cur and _url_alive(cur):
        _apply_env()        # v1.5.32：确认可用后务必把 env 同步上（此前直接 return，env 可能仍是空的）
        return cur
    # 1.5) v1.5.32：active 是内核型节点且内核已装 —— 先把内核拉起/选中该节点再试一次。
    #      此前这里直接跳到「别的候选」，等于用户自己选的节点永远不会被救活；
    #      而候选测试又会顺手启动内核却不切回用户节点，最终 env 被清空、技能全线直连。
    act = active_entry()
    if act and not act.get("direct", True):
        try:
            k = _kernel()
            if k.is_installed():
                k.ensure_running()
                k.select(act.get("kernel_name") or act["name"])
                _apply_env()
                cur = active_url()
                if cur and _url_alive(cur):
                    return cur
        except Exception:
            pass
    # 2) 候选排序：延迟已知且小的优先，未测过的次之，dead 靠后；跳过原 active
    def _key(e):
        lat = e.get("latency_ms")
        dead = e.get("status") == "dead"
        return (dead, 1 if not isinstance(lat, (int, float)) or lat <= 0 else 0, lat or 0)
    candidates = [e for e in sorted(entries, key=_key) if e["id"] != original_id][:max_candidates]
    for e in candidates:
        try:
            set_active(e["id"])
        except Exception:
            continue
        url = active_url()
        if url and _url_alive(url):
            return url
    # 3) 全部失败：恢复原状态
    try:
        set_active(original_id)
    except Exception:
        pass
    return ""


def set_active(entry_id):
    """启用某节点（entry_id 空串 = 直连）。内核型节点会自动拉起内核并切换 selector。"""
    with _LOCK:
        target = None
        if entry_id:
            for e in _state["entries"]:
                if e["id"] == entry_id:
                    target = e
                    break
            if target is None:
                raise RuntimeError("代理节点不存在。")
        _state["active_id"] = entry_id or ""
        _save()

    if target is None:
        # 直连：内核若在跑，selector 切回 DIRECT
        try:
            k = _kernel()
            if k.is_running():
                k.select("DIRECT")
        except Exception:
            pass
    elif not target.get("direct", True):
        k = _kernel()
        if not k.is_installed():
            raise RuntimeError("该节点需要内核。请先点「下载内核」，完成后再启用。")
        k.ensure_running()
        k.select(target.get("kernel_name") or target["name"])
    _apply_env()
    return _state["active_id"]


# ---------------- CRUD ----------------

def list_pool():
    kernel_status = {"installed": False, "running": False, "version": "", "mixed_port": 0}
    try:
        k = _kernel()
        kernel_status = k.status()
    except Exception:
        pass
    with _LOCK:
        entries = json.loads(json.dumps(_state["entries"], ensure_ascii=False))
    # 动态可用标记：直连型恒可用；内核型看内核是否已安装
    for e in entries:
        e["usable"] = bool(e.get("direct", True)) or kernel_status["installed"]
    return {"active_id": _state["active_id"],
            "active_url": active_url(),
            "has_socks": _HAS_SOCKS,
            "kernel": kernel_status,
            "env": env_snapshot(),
            "entries": entries}


def delete(entry_id):
    with _LOCK:
        before = len(_state["entries"])
        _state["entries"] = [e for e in _state["entries"] if e["id"] != entry_id]
        if _state["active_id"] == entry_id:
            _state["active_id"] = ""
            _apply_env()
        _save()
        return before - len(_state["entries"])


def update_entry(entry_id, **fields):
    with _LOCK:
        for e in _state["entries"]:
            if e["id"] == entry_id:
                for k in ("name", "group"):
                    if fields.get(k) is not None:
                        e[k] = str(fields[k])[:60]
                _save()
                return e
    raise RuntimeError("代理节点不存在。")
