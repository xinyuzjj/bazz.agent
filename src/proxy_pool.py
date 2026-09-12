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
# v1.5.37：新增 last_active_id —— 「用户上次显式选过的节点」。
# active_id 只代表「本次会话是否启用」，启动时一律清空；用户的选择记在 last_active_id 里，
# 由界面上的一键「恢复」按钮取回，而不是开机偷偷替他决定。
_state = {"active_id": "", "last_active_id": "", "entries": []}


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
            _state["last_active_id"] = data.get("last_active_id", "") or ""
            _state["entries"] = data.get("entries", []) or []
        except (OSError, json.JSONDecodeError):
            _state["active_id"] = ""
            _state["last_active_id"] = ""
            _state["entries"] = []


def _save():
    tmp = POOL_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"active_id": _state["active_id"],
                   "last_active_id": _state["last_active_id"],
                   "entries": _state["entries"]},
                  f, ensure_ascii=False, indent=1)
    os.replace(tmp, POOL_PATH)


def bootstrap():
    """后端启动时调用：加载配置，并**确保本次会话从直连开始**。

    v1.5.37（真实事故「一进 APP 就默认连上代理池」）：旧实现是 `_load()` 之后立刻
    `_apply_env()` + `_revive_kernel_async()`。而 `_load()` 会把上次的 `active_id`
    （现场就是一个 vless 内核型节点）恢复成「已启用」，于是每次启动都会在几秒后静默
    拉起 mihomo、把整个 APP 的流量切进代理池 —— 用户从没同意过，而且界面上那句
    「直连 · 使用中」还是假的（那时 /api/proxies 还没回来）。

    现在：**启动一律直连**。上次的选择不丢，记在 `last_active_id`，由用户在代理池面板
    上显式点「恢复」才生效。
    """
    _load()
    with _LOCK:
        _state["last_active_id"] = _state["active_id"] or _state["last_active_id"]
        _state["active_id"] = ""
        _save()
    _apply_env()



# v1.5.37：`_revive_kernel_async()` 已删除。
# 它当年（v1.5.32）是为了修「重启 APP 后代理静默失效」——那时把 `active_id` 当成
# 用户意图恢复，于是每次启动都后台拉起内核。代价是 v1.5.37 用户报的
# 「一进 APP 就默认连上代理池」。两者只能选一个：**启动不碰网络**。
# 而 v1.5.32 的原始症状不会回来 —— 内核型节点现在只有走 `set_active()` 才会变成 active，
# 那条路径里已经 `ensure_running()` + `select()` 过了，不存在「显示已启用但内核没跑」。

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
    # 原始订阅落盘（内核 proxy-provider 用）。v1.5.37：save_provider 现在只写 proxies 段，
    # 并把结果**返回**而不是静默吞掉 —— provider 是空的（内核一个节点都拿不到）必须让用户看见。
    provider = {"ok": False, "error": "未尝试"}
    try:
        import proxy_kernel
        provider = proxy_kernel.save_provider(url, raw)
    except Exception as e:
        provider = {"ok": False, "error": str(e)[:200]}
    entries = _parse_subscription(raw, group=group, source=f"{host} 订阅", sub_url=url)
    with _LOCK:
        added, updated = _merge(entries)
        _save()
    return {"added": added, "updated": updated, "parsed": len(entries),
            "group": host, "provider": provider}


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
    """把 active 代理写入进程环境变量（requests 与子进程继承生效）；直连则清除干净。"""
    e = active_entry()
    purl = proxy_url(e)
    if e and purl:
        # socks5h 用于 ALL_PROXY（DNS 也走代理）
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            os.environ[k] = purl
        for k in ("ALL_PROXY", "all_proxy"):
            os.environ[k] = purl
        _ensure_no_proxy()
        _ensure_node_preload()
    else:
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                  "http_proxy", "https_proxy", "all_proxy"):
            os.environ.pop(k, None)
        # v1.5.37：直连必须连预加载一起摘。只清 env 是不够的 ——
        # 下面 _ensure_node_preload() 会往 NODE_OPTIONS 里塞 --require=proxy-preload.cjs，
        # 而它此前**只加不摘**。已经起来的 Node 子进程手里那个 EnvHttpProxyAgent
        # 会继续把 fetch 送进本地代理端口；内核一停，这些进程就彻底连不上任何东西。
        _drop_node_preload()


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


# 匹配 `--require="…proxy-preload.cjs"` / `--require='…'` / 裸路径写法。
# 路径里可能带空格（安装根如 `F:\某 目录\BAZZ.AGENT`），所以不能按空白切分。
_PRELOAD_TOKEN_RE = re.compile(
    r'\s*--require=(?:"[^"]*proxy-preload\.cjs"|\'[^\']*proxy-preload\.cjs\'|\S*proxy-preload\.cjs)')


def _drop_node_preload():
    """v1.5.37：`_ensure_node_preload()` 的反操作 —— 从 NODE_OPTIONS 里摘掉预加载。

    切回直连时若只清 HTTP(S)_PROXY 而留着预加载，长期存活的 Node 子进程仍会走旧代理；
    反过来，直连时留着它本身没有意义（脚本只在有代理 env 时才接管）。"""
    cur = os.environ.get("NODE_OPTIONS") or ""
    if "proxy-preload.cjs" not in cur:
        return
    left = _PRELOAD_TOKEN_RE.sub("", cur).strip()
    if left:
        os.environ["NODE_OPTIONS"] = left
    else:
        os.environ.pop("NODE_OPTIONS", None)


def teardown_proxy():
    """v1.5.37：真正的「取消连接」—— 把代理链路整体拆掉，而不是只切一下 selector。

    现场（用户报「取消连接后整个应用都没有网了」）：旧实现直连分支只调
    `k.select("DIRECT")`，**内核照跑**，NODE_OPTIONS 里的预加载也照挂着。于是
    「取消连接」并没有真的回到直连：本机仍有一个 mihomo 在 7899 上监听着，
    任何在代理启用期间启动的 Node 子进程仍把 fetch 交给它；而它自己的出口如果因为
    selector 切换出现竞态（或端口随后被 stop 掉），这些进程就谁也连不上。
    这里按「停内核 → 摘预加载 → 清 env」的顺序彻底还原，并返回清理结果供界面显示。
    """
    detail = {}
    try:
        detail["kernel"] = _kernel().stop()
    except Exception as e:
        detail["kernel"] = {"ok": False, "error": str(e)[:200]}
    _drop_node_preload()
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
              "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(k, None)
    detail["env"] = env_snapshot()
    return detail



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


def start_kernel_async():
    """v1.5.37：非阻塞启动内核 + 就绪后注入 env。

    旧路径是 `proxy_kernel.start()` 同步等最多 20 秒（且当时还在持锁），HTTP 请求、
    代理池轮询、界面全部陪着一起等 —— 用户点一下就是「卡死」。
    现在线程里启动，接口立刻返回；界面靠 `status().starting` 显示「启动中…」。"""
    def _work():
        try:
            r = _kernel().start()
            if r.get("ok") and not r.get("starting"):
                _apply_env()          # 内核真起来了才注入 env
        except Exception:
            pass
    threading.Thread(target=_work, daemon=True).start()
    return {"ok": True, "starting": True}



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
        if entry_id:
            _state["last_active_id"] = entry_id      # v1.5.37：记住用户的选择，供下次一键恢复
        _save()

    if target is None:
        # v1.5.37：直连 = 彻底拆除代理链路（停内核 + 摘预加载 + 清 env）。
        # 旧实现只切 selector，内核照跑、预加载照挂 —— 这就是「取消连接后整个应用没网」。
        teardown_proxy()
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
            # v1.5.37：「上次显式选过的节点」——启动不再自动启用，但选择不丢，
            # 界面据此显示「恢复上次」入口，而不是开机替用户做决定。
            "last_active_id": _state.get("last_active_id", "") or "",
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
