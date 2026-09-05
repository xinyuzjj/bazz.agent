"""Binance Agent Native — 真实 MCP 客户端（Streamable HTTP / JSON-RPC 2.0）

严格按 Binance 官方 Agent Native 规格实现：
- 端点：https://agent.binance.com/mcp/agentic
- 传输：MCP over Streamable HTTP（POST JSON-RPC；响应可为 application/json 或 text/event-stream）
- 协议版本：2025-06-18
- 鉴权：OAuth 2.0（RFC 9728 资源元数据挑战）。未鉴权请求返回
  401 + www-authenticate: Bearer resource_metadata="https://agent.binance.com/.well-known/oauth-protected-resource/gateway-mcp"
  → 解析资源元数据 → 发现授权服务器 → 浏览器 PKCE 流拿 access token → Bearer 调用。
- 公开行情（ticker / klines / orderbook / fundingRate）免鉴权，initialize 后即可 tools/call。
- 账户/交易/划转类工具需 Agentic 子账户 OAuth 授权 + 对应 scope。

工具在运行时通过 tools/list 发现，无需硬编码工具名。
"""

import os
import json
import time
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

try:
    from state import get_setting, set_setting
except Exception:  # 独立运行兜底
    def get_setting(k, d=""):
        return d
    def set_setting(k, v):
        pass

APP_NAME = "agent-os-alpha-scout"
PROTOCOL_VERSION = "2025-06-18"
DEFAULT_BINANCE = {
    "name": "binance",
    "url": "https://agent.binance.com/mcp/agentic",
    "enabled": True,
    "auth": "oauth",            # oauth | token | none
    "description": "Binance Agentic MCP Server（Agent Native 核心连接层）",
}
_REDIRECT_HOST = "127.0.0.1"
_REDIRECT_PORT = 8787
_REDIRECT_PATH = "/oauth/callback"

_session = requests.Session()
_session.headers.update({"User-Agent": APP_NAME})

# 运行时鉴权流（PKCE）状态：state -> {verifier, flow_done, token, error}
_oauth_flows = {}
_oauth_server = None
_oauth_lock = threading.Lock()


# ---------------- server 配置持久化 ----------------

def ensure_default():
    servers = _read_servers()
    if not any(s["name"] == "binance" for s in servers):
        servers.insert(0, dict(DEFAULT_BINANCE))
        _write_servers(servers)
    return servers


def _read_servers():
    raw = get_setting("mcp_servers", "")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return [dict(DEFAULT_BINANCE)]


def _write_servers(servers):
    set_setting("mcp_servers", json.dumps(servers, ensure_ascii=False))


def list_servers():
    return _read_servers()


def add_server(name, url, auth="oauth", enabled=True, description=""):
    servers = _read_servers()
    servers = [s for s in servers if s["name"] != name]
    servers.append({"name": name, "url": url, "enabled": enabled,
                    "auth": auth, "description": description})
    _write_servers(servers)
    return servers


def remove_server(name):
    servers = [s for s in _read_servers() if s["name"] != name]
    _write_servers(servers)
    return servers


def set_enabled(name, enabled):
    servers = _read_servers()
    for s in servers:
        if s["name"] == name:
            s["enabled"] = enabled
    _write_servers(servers)
    return servers


def get_server(name):
    return next((s for s in _read_servers() if s["name"] == name), None)


# ---------------- token 持久化 ----------------

def _token_key(name):
    return f"mcp_token:{name}"


def get_token(name):
    return get_setting(_token_key(name), "")


def set_token(name, token):
    set_setting(_token_key(name), token or "")


# ---------------- 底层 RPC（JSON-RPC over Streamable HTTP）----------------

def _parse_sse(text):
    """把 text/event-stream 文本解析为按 id 索引的 JSON-RPC 消息列表。"""
    msgs = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("data:"):
            payload = line[5:].strip()
        elif line.startswith("event:"):
            continue
        else:
            continue
        if not payload:
            continue
        try:
            msgs.append(json.loads(payload))
        except Exception:
            continue
    return msgs


def _rpc(server, method, params=None, idn=None, token=None, session_id=None, timeout=20):
    """发送一条 JSON-RPC 请求，返回 {status_code, session_id, data, ok, error}。"""
    body = {"jsonrpc": "2.0", "method": method}
    if idn is not None:
        body["id"] = idn
    if params is not None:
        body["params"] = params
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        h["Mcp-Session-Id"] = session_id
    if token:
        h["Authorization"] = f"Bearer {token}"
    try:
        r = _session.post(server["url"], headers=h, json=body, timeout=timeout)
        sid = r.headers.get("Mcp-Session-Id", session_id)
        ctype = r.headers.get("Content-Type", "")
        text = r.text
        data = None
        if "text/event-stream" in ctype or "data:" in text:
            msgs = _parse_sse(text)
            # 取与目标 id 匹配的消息；无 id 的通知取最后一条
            for m in msgs:
                if idn is None or m.get("id") == idn:
                    data = m
            if data is None and msgs:
                data = msgs[-1]
        else:
            try:
                data = json.loads(text) if text.strip() else None
            except Exception:
                data = None
        return {"status_code": r.status_code, "session_id": sid, "data": data,
                "ok": r.status_code < 400, "headers": dict(r.headers)}
    except Exception as e:
        return {"status_code": 0, "session_id": session_id, "data": None,
                "ok": False, "error": str(e)}


def _oauth_challenge(resp):
    """从 401 响应的 www-authenticate 头解析 RFC 9728 资源元数据 URL。"""
    wa = resp.headers.get("www-authenticate", "") if hasattr(resp, "headers") else ""
    if not wa:
        return None
    # 形如: Bearer resource_metadata="https://.../oauth-protected-resource/gateway-mcp"
    import re
    m = re.search(r'resource_metadata="([^"]+)"', wa)
    return m.group(1) if m else None


# ---------------- OAuth 2.0 发现 ----------------

def discover_oauth(server_url):
    """对未鉴权端点做一次探测，解析 401 挑战并发现授权服务器元数据。"""
    probe = _session.post(
        server_url,
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                         "clientInfo": {"name": APP_NAME, "version": "1.0"}}},
        timeout=20,
    )
    if probe.status_code != 401:
        # 可能无需鉴权（公开数据），或已带 token
        return {"needs_auth": False, "status_code": probe.status_code}
    meta_url = _oauth_challenge(probe)
    if not meta_url:
        return {"needs_auth": True, "error": "无 resource_metadata 挑战", "status_code": 401}
    try:
        res_meta = _session.get(meta_url, timeout=20).json()
    except Exception as e:
        return {"needs_auth": True, "error": f"资源元数据获取失败: {e}", "status_code": 401}
    auth_servers = res_meta.get("authorization_servers", [])
    if not auth_servers:
        return {"needs_auth": True, "error": "无 authorization_servers", "status_code": 401}
    authz_base = auth_servers[0].rstrip("/")
    # 授权服务器元数据
    asm = None
    for path in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
        try:
            asm = _session.get(authz_base + path, timeout=20).json()
            break
        except Exception:
            asm = None
    if not asm:
        asm = {"authorization_endpoint": authz_base + "/oauth/authorize",
               "token_endpoint": authz_base + "/oauth/token"}
    return {
        "needs_auth": True,
        "status_code": 401,
        "resource": res_meta.get("resource", server_url),
        "authorization_endpoint": asm.get("authorization_endpoint"),
        "token_endpoint": asm.get("token_endpoint"),
        "registration_endpoint": asm.get("registration_endpoint"),
        "scopes_supported": asm.get("scopes_supported", []),
    }


# ---------------- 连接 / 握手 ----------------

def initialize(server, token=None):
    """返回 (session_id, server_info)。无 token 且需鉴权时抛 NeedsOAuth。"""
    init = _rpc(server, "initialize", {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": APP_NAME, "version": "1.0"},
    }, idn=1, token=token)
    if init["status_code"] == 401:
        disc = discover_oauth(server["url"])
        raise NeedsOAuth(disc, server["name"])
    if not init["ok"] or not init.get("data"):
        raise RuntimeError(f"initialize 失败: {init['status_code']} {init.get('error')}")
    # 发送 notifications/initialized（无响应）
    _rpc(server, "notifications/initialized", session_id=init["session_id"], token=token)
    info = (init["data"] or {}).get("result", {})
    return init["session_id"], info


class NeedsOAuth(Exception):
    def __init__(self, discovery, server_name):
        self.discovery = discovery
        self.server_name = server_name
        super().__init__("需要 OAuth 授权")


def server_status(name):
    server = get_server(name)
    if not server:
        return {"name": name, "configured": False, "reachable": False, "detail": "未找到 server"}
    token = get_token(name) if server.get("auth") in ("oauth", "token") else ""
    try:
        sid, info = initialize(server, token=token)
        return {"name": name, "configured": True, "reachable": True,
                "authed": bool(token), "server_info": info.get("serverInfo"),
                "detail": "已连接" + ("（已授权）" if token else "（公开数据）")}
    except NeedsOAuth as e:
        return {"name": name, "configured": True, "reachable": False,
                "needs_auth": True, "discovery": e.discovery,
                "detail": "需要 OAuth 授权"}
    except Exception as e:
        return {"name": name, "configured": True, "reachable": False, "detail": str(e)}


# ---------------- 工具发现 / 调用 ----------------

def list_tools_for(server, token=None, use_cache=True):
    cache_key = f"mcp_tools:{server['name']}"
    if use_cache:
        cached = get_setting(cache_key, "")
        if cached:
            try:
                return {"status": "ok", "tools": json.loads(cached), "cached": True}
            except Exception:
                pass
    try:
        sid, _ = initialize(server, token=token)
        res = _rpc(server, "tools/list", {}, session_id=sid, idn=2, token=token)
        if not res["ok"] or not res.get("data"):
            return {"status": "error", "detail": f"tools/list 失败: {res['status_code']}", "tools": []}
        tools = (res["data"] or {}).get("result", {}).get("tools", [])
        out = [{"name": t.get("name"),
                "description": t.get("description", ""),
                "inputSchema": t.get("inputSchema", {})} for t in tools]
        set_setting(cache_key, json.dumps(out, ensure_ascii=False))
        return {"status": "ok", "tools": out, "cached": False}
    except NeedsOAuth as e:
        return {"status": "needs_auth", "detail": "需要 OAuth 授权", "discovery": e.discovery, "tools": []}
    except Exception as e:
        return {"status": "error", "detail": str(e), "tools": []}


def call_tool(server_name, tool, arguments=None, token=None):
    arguments = arguments or {}
    server = get_server(server_name)
    if not server:
        return {"status": "error", "detail": f"未找到 server: {server_name}"}
    if token is None:
        token = get_token(server_name) if server.get("auth") in ("oauth", "token") else ""
    try:
        sid, _ = initialize(server, token=token)
        res = _rpc(server, "tools/call", {"name": tool, "arguments": arguments},
                   session_id=sid, idn=3, token=token)
        if not res["ok"] or not res.get("data"):
            return {"status": "error", "detail": f"tools/call 失败: {res['status_code']}", "raw": res.get("data")}
        return {"status": "ok", "data": (res["data"] or {}).get("result")}
    except NeedsOAuth as e:
        return {"status": "needs_auth", "detail": "需要 OAuth 授权", "discovery": e.discovery}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


# ---------------- OAuth PKCE 浏览器流（桌面原生回调）----------------

def _start_callback_server():
    global _oauth_server
    with _oauth_lock:
        if _oauth_server:
            return
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != _REDIRECT_PATH:
                self._html(404, "Not found")
                return
            q = urllib.parse.parse_qs(parsed.query)
            state = q.get("state", [""])[0]
            code = q.get("code", [""])[0]
            error = q.get("error", [""])[0]
            flow = _oauth_flows.get(state)
            if error:
                if flow:
                    flow["error"] = error
                    flow["done"] = True
                self._html(400, f"授权失败: {error}")
                return
            if not flow:
                self._html(400, "未知的授权状态")
                return
            flow["code"] = code
            flow["done"] = True
            self._html(200, "✅ Binance 授权成功，可返回 BAZZ Agent 继续。")
        def _html(self, code, msg):
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<html><body style='font-family:sans-serif;padding:40px'><h3>{msg}</h3></body></html>".encode("utf-8"))
        def log_message(self, *a):
            pass
    srv = HTTPServer((_REDIRECT_HOST, _REDIRECT_PORT), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    with _oauth_lock:
        _oauth_server = srv


def oauth_start(name):
    """开始 OAuth PKCE 流，返回授权 URL（前端打开）与本地 state。"""
    server = get_server(name)
    if not server:
        return {"ok": False, "detail": "未找到 server"}
    disc = discover_oauth(server["url"])
    if not disc.get("needs_auth") or not disc.get("authorization_endpoint"):
        return {"ok": False, "detail": disc.get("error", "无需授权或无法发现授权端点")}
    verifier = secrets.token_urlsafe(64)
    # S256 challenge
    import hashlib, base64
    chal = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    _oauth_flows[state] = {"verifier": verifier, "done": False, "code": "", "token": "", "error": ""}
    _start_callback_server()
    redirect_uri = f"http://{_REDIRECT_HOST}:{_REDIRECT_PORT}{_REDIRECT_PATH}"
    params = {
        "response_type": "code",
        "client_id": os.getenv("BINANCE_OAUTH_CLIENT_ID", APP_NAME),
        "redirect_uri": redirect_uri,
        "code_challenge": chal,
        "code_challenge_method": "S256",
        "state": state,
        "resource": disc.get("resource", server["url"]),
    }
    scopes = disc.get("scopes_supported") or ["openid", "profile"]
    params["scope"] = " ".join(scopes)
    auth_url = disc["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)
    return {"ok": True, "auth_url": auth_url, "state": state,
            "token_endpoint": disc.get("token_endpoint"),
            "redirect_uri": redirect_uri, "detail": "在浏览器打开 auth_url 完成 Binance 登录"}


def oauth_poll(name, state, timeout=120):
    """轮询等待本地回调拿到 code 并换 token。"""
    flow = _oauth_flows.get(state)
    if not flow:
        return {"ok": False, "detail": "未知 state"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        if flow.get("done"):
            break
        time.sleep(1)
    if flow.get("error"):
        return {"ok": False, "detail": flow["error"]}
    if not flow.get("code"):
        return {"ok": False, "detail": "等待授权超时"}
    server = get_server(name)
    disc = discover_oauth(server["url"])
    token_endpoint = disc.get("token_endpoint") or flow.get("token_endpoint")
    if not token_endpoint:
        return {"ok": False, "detail": "无 token_endpoint"}
    # 用本地回调服务器拿到的 code 换 token
    try:
        resp = _session.post(token_endpoint, data={
            "grant_type": "authorization_code",
            "code": flow["code"],
            "code_verifier": flow["verifier"],
            "client_id": os.getenv("BINANCE_OAUTH_CLIENT_ID", APP_NAME),
            "redirect_uri": f"http://{_REDIRECT_HOST}:{_REDIRECT_PORT}{_REDIRECT_PATH}",
        }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=20)
        tok = resp.json()
    except Exception as e:
        return {"ok": False, "detail": f"换 token 失败: {e}"}
    if "access_token" not in tok:
        return {"ok": False, "detail": f"换 token 失败: {tok}"}
    set_token(name, tok["access_token"])
    # 清缓存工具，强制下次重新发现（权限可能变化）
    set_setting(f"mcp_tools:{name}", "")
    return {"ok": True, "token_type": tok.get("token_type"),
            "scope": tok.get("scope"), "detail": "授权成功，已保存 token"}


def oauth_status(name):
    return {"configured": bool(get_token(name))}


# ---------------- 兼容旧接口（agent_core / desktop_app 使用）----------------

def mcp_status():
    ensure_default()
    out = []
    for s in _read_servers():
        out.append(server_status(s["name"]))
    return {"servers": out, "reachable": any(s.get("reachable") for s in out) if out else False}


def mcp_list_tools():
    ensure_default()
    tools = []
    for s in _read_servers():
        if not s.get("enabled"):
            continue
        lt = list_tools_for(s)
        for t in lt.get("tools", []):
            t = dict(t)
            t["server"] = s["name"]
            tools.append(t)
    return {"status": "ok" if tools else "empty", "tools": tools}


if __name__ == "__main__":
    import pprint
    ensure_default()
    pprint.pprint(mcp_status())
    pprint.pprint(mcp_list_tools())
