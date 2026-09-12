"""LLM 订阅 OAuth 登录层（v1.5.41，Hermes 对齐）

支持四家「订阅直连」LLM 提供方（不需要任何 API Key，浏览器登录即用）：
- copilot          GitHub Copilot 设备码流（github.com/login/device/code），
                   短命 Copilot token 经 api.github.com/copilot_internal/v2/token 兑换，
                   对话走 https://api.githubcopilot.com（OpenAI 兼容 /chat/completions）。
- codex            ChatGPT/Codex 订阅（PKCE 本地回环，auth.openai.com），
                   对话走 https://chatgpt.com/backend-api/codex/responses（Responses API，SSE-only）。
- anthropic-oauth  Claude Pro/Max 订阅（PKCE 本地回环，claude.ai/oauth/authorize，
                   token 端点 console.anthropic.com/v1/oauth/token，JSON body），
                   对话走 https://api.anthropic.com/v1/messages（原生 Messages API）。
- nous             Nous Portal 订阅（设备码流 portal.nousresearch.com/api/oauth/device/code，
                   client_id=hermes-cli，scope=inference:invoke），
                   对话走 https://inference-api.nousresearch.com/v1（OpenAI 兼容）。
                   另支持手动录入 Portal 生成的 sk- API Key 兜底（Portal 设备审批 UI 有已知缺陷）。

凭据托管：全部写入 settings("llm_oauth")（state.py SENSITIVE_SETTING_KEYS 自动 DPAPI 加密）。
- Nous/Codex refresh token 为**单次使用**（rotating）：刷新成功必须立即回存新 refresh_token。
- Copilot gh token 长命，Copilot API token 短命（~30min），按需兑换并缓存。
对外主接口：
- status()            四家连接状态（供前端卡片）
- start(provider)     发起登录（返回设备码信息或浏览器授权 URL）
- poll(provider, s)   轮询/完成登录
- logout(provider)    断开
- get_access_token(p) 取可用 access token（自动刷新；供 llm.py 发请求）
- set_api_key(p, key) 手动录入 API Key（nous 等）
"""
import os
import json
import time
import base64
import hashlib
import secrets as _std_secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional, Dict, Any

import requests

try:
    from state import get_setting, set_setting
except Exception:
    def get_setting(k, d=""):
        return d

    def set_setting(k, v):
        pass


STORE_KEY = "llm_oauth"

# ---------------- 各家端点常量（全部按官方/hermes-agent 源码核实，2026-09） ----------------

# GitHub Copilot（设备码流；client_id 用 VS Code 公开 ID）
GITHUB_CLIENT_ID = "Iv1.b507a08c87ecfe98"
GITHUB_DEVICE_URL = "https://github.com/login/device/code"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
COPILOT_BASE = "https://api.githubcopilot.com"

# OpenAI Codex（ChatGPT 订阅 PKCE）
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_CALLBACK_PATH = "/auth/callback"
CODEX_REDIRECT_PORT = 1455          # 与 Codex CLI 一致的回环端口
CODEX_API = "https://chatgpt.com/backend-api/codex/responses"

# Anthropic（Claude 订阅 PKCE；state=verifier 是其非标准要求）
ANTHROPIC_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
ANTHROPIC_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
ANTHROPIC_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
ANTHROPIC_CALLBACK_PATH = "/bazz-auth/callback"
ANTHROPIC_REDIRECT_PORT = 8790
ANTHROPIC_API = "https://api.anthropic.com/v1"

# Nous Portal（设备码流）
NOUS_PORTAL = "https://portal.nousresearch.com"
NOUS_CLIENT_ID = "hermes-cli"
NOUS_SCOPE = "inference:invoke"
NOUS_DEVICE_URL = NOUS_PORTAL + "/api/oauth/device/code"
NOUS_TOKEN_URL = NOUS_PORTAL + "/api/oauth/token"
NOUS_API = "https://inference-api.nousresearch.com/v1"
NOUS_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
SUB_PROVIDERS = {"copilot", "codex", "anthropic-oauth", "nous"}
DEVICE_PROVIDERS = {"copilot", "nous"}      # 设备码流（前端轮询）
REFRESH_SKEW = 120                          # 到期前 2 分钟即刷新

_SUB_LOCK = threading.RLock()
_FLOWS: Dict[str, dict] = {}                # session_id -> flow 状态（跨 poll 保持）


# ---------------- 存取（DPAPI 加密由 state.py 钩子自动完成） ----------------

def _load() -> dict:
    raw = get_setting(STORE_KEY, "")
    try:
        d = json.loads(raw) if raw else {}
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(store: dict):
    set_setting(STORE_KEY, json.dumps(store, ensure_ascii=False))


def _get(p: str) -> dict:
    v = _load().get(p)
    return v if isinstance(v, dict) else {}


def _put(p: str, **kv):
    with _SUB_LOCK:
        store = _load()
        cur = store.get(p) if isinstance(store.get(p), dict) else {}
        cur.update(kv)
        cur["updated_at"] = int(time.time())
        store[p] = cur
        _save(store)


def _drop(p: str):
    with _SUB_LOCK:
        store = _load()
        if p in store:
            del store[p]
            _save(store)


# ---------------- 小工具 ----------------

def _jwt_claims(token: str) -> dict:
    """解析 JWT payload（不验签，仅取 claims）。"""
    try:
        if not token or token.count(".") != 2:
            return {}
        seg = token.split(".")[1]
        seg += "=" * ((4 - len(seg) % 4) % 4)
        claims = json.loads(base64.urlsafe_b64decode(seg.encode()).decode("utf-8"))
        return claims if isinstance(claims, dict) else {}
    except Exception:
        return {}


def _pkce_pair() -> tuple:
    verifier = _std_secrets.token_urlsafe(64)
    chal = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, chal


def _redirect_uri(provider: str) -> str:
    if provider == "codex":
        return f"http://127.0.0.1:{CODEX_REDIRECT_PORT}{CODEX_CALLBACK_PATH}"
    return f"http://127.0.0.1:{ANTHROPIC_REDIRECT_PORT}{ANTHROPIC_CALLBACK_PATH}"


def _expires_in(payload: dict, fallback: int = 3600) -> int:
    for k in ("expires_in", "expired_in"):
        v = payload.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    return fallback


def _err(msg: str) -> dict:
    return {"ok": False, "detail": str(msg)[:300]}


# ---------------- 本地回环回调服务器（PKCE：codex / anthropic-oauth） ----------------

class _CallbackServer:
    """一次性本地回调服务器：捕获 ?code=&state= 后自动关闭。每个登录流程各起一个。"""

    def __init__(self, port: int, path: str):
        self.code = ""
        self.state = ""
        self.error = ""
        self._done = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != path:
                    self._html(404, "Not found")
                    return
                q = urllib.parse.parse_qs(parsed.query)
                if q.get("error"):
                    outer.error = q.get("error", [""])[0] + ": " + q.get("error_description", [""])[0]
                    self._html(400, f"授权失败：{outer.error}")
                else:
                    outer.code = q.get("code", [""])[0]
                    outer.state = q.get("state", [""])[0]
                    self._html(200, "✅ 授权成功，可返回 BAZZ Agent 继续。")
                outer._done.set()

            def _html(self, code, msg):
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"<html><body style='font-family:sans-serif;padding:40px'><h3>{msg}</h3></body></html>".encode("utf-8"))

            def log_message(self, *a):
                pass

        self._srv = HTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def wait(self, timeout: float) -> bool:
        return self._done.wait(timeout)

    def close(self):
        try:
            self._srv.shutdown()
            self._srv.server_close()
        except Exception:
            pass


# ---------------- 登录发起 ----------------

def start(provider: str) -> dict:
    """发起登录。设备码流返回 {mode:'device', user_code, verification_uri, session}；
    PKCE 返回 {mode:'browser', auth_url, session}（同时起本地回调服务器）。"""
    provider = (provider or "").lower()
    if provider == "copilot":
        return _start_device_generic(
            provider,
            url=GITHUB_DEVICE_URL,
            data={"client_id": GITHUB_CLIENT_ID, "scope": "read:user"},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            verify=lambda payload: "device_code" in payload)
    if provider == "nous":
        return _start_device_generic(
            provider,
            url=NOUS_DEVICE_URL,
            data={"client_id": NOUS_CLIENT_ID, "scope": NOUS_SCOPE},
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            verify=lambda payload: "device_code" in payload)
    if provider == "codex":
        verifier, chal = _pkce_pair()
        params = {
            "response_type": "code", "client_id": CODEX_CLIENT_ID,
            "redirect_uri": _redirect_uri(provider),
            "scope": "openai profile email offline_access",
            "code_challenge": chal, "code_challenge_method": "S256",
            "state": verifier,                      # codex：state 即 verifier（与 anthropic 同套路防 CSRF）
            "prompt": "login", "codex_cli_simplified_flow": "true",
        }
        return _start_browser(provider, CODEX_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params),
                              verifier, CODEX_REDIRECT_PORT, CODEX_CALLBACK_PATH)
    if provider == "anthropic-oauth":
        verifier, chal = _pkce_pair()
        params = {
            "code": "true", "response_type": "code", "client_id": ANTHROPIC_CLIENT_ID,
            "redirect_uri": _redirect_uri(provider),
            "scope": "org:create_api_key user:profile user:inference",
            "code_challenge": chal, "code_challenge_method": "S256",
            "state": verifier,                      # anthropic：state=verifier（非标准要求）
        }
        return _start_browser(provider, ANTHROPIC_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params),
                              verifier, ANTHROPIC_REDIRECT_PORT, ANTHROPIC_CALLBACK_PATH)
    return _err(f"不支持的订阅提供方：{provider}")


def _start_device_generic(provider: str, *, url: str, data: dict,
                          headers: dict, verify) -> dict:
    try:
        r = requests.post(url, json=data if "application/json" in headers.get("Content-Type", "")
                          else None, data=None if "application/json" in headers.get("Content-Type", "")
                          else data, headers=headers, timeout=20)
        payload = r.json() if "json" in (r.headers.get("Content-Type") or "").lower() else {}
        if r.status_code >= 400 or not verify(payload):
            return _err(f"设备码申请失败 HTTP {r.status_code} · {str(payload)[:200]}")
        sid = _std_secrets.token_urlsafe(16)
        interval = payload.get("interval") or (5 if provider == "copilot" else 1)
        _FLOWS[sid] = {"provider": provider, "kind": "device",
                       "device_code": payload.get("device_code", ""),
                       "interval": max(1, int(interval)),
                       "expires_at": time.time() + int(payload.get("expires_in") or 900)}
        return {"ok": True, "mode": "device", "session": sid,
                "user_code": payload.get("user_code", ""),
                "verification_uri": payload.get("verification_uri") or
                                    (f"{NOUS_PORTAL}/manage-subscription?user_code={payload.get('user_code', '')}"
                                     if provider == "nous" else "https://github.com/login/device"),
                "verification_uri_complete": payload.get("verification_uri_complete", ""),
                "interval": _FLOWS[sid]["interval"],
                "detail": "在浏览器打开验证页面并输入代码，完成后自动连接"}
    except Exception as e:
        return _err(f"设备码申请异常：{type(e).__name__}: {e}")


def _start_browser(provider: str, auth_url: str, verifier: str, port: int, path: str) -> dict:
    try:
        srv = _CallbackServer(port, path)
    except OSError as e:
        return _err(f"本地回调端口 {port} 被占用：{e}（可关闭占用该端口的程序后重试）")
    sid = _std_secrets.token_urlsafe(16)
    _FLOWS[sid] = {"provider": provider, "kind": "browser", "verifier": verifier,
                   "server": srv, "expires_at": time.time() + 600}
    return {"ok": True, "mode": "browser", "session": sid, "auth_url": auth_url,
            "detail": "浏览器完成登录后会自动回连本地回调端口"}


# ---------------- 登录轮询 ----------------

def poll(provider: str, session: str, wait: float = 8.0) -> dict:
    """轮询登录进度。前端每次 HTTP 调一次：
    - {ok, status:'pending'}：还没好，按返回的 interval 继续轮
    - {ok, status:'done'}：完成，token 已入库
    - {ok:false}：失败（含超时/被拒）"""
    provider = (provider or "").lower()
    flow = _FLOWS.get(session)
    if not flow or flow.get("provider") != provider:
        return _err("未知或已过期的登录会话，请重新发起")
    if time.time() > flow.get("expires_at", 0):
        _cleanup_flow(session)
        return _err("登录会话已超时，请重新发起")
    if flow["kind"] == "device":
        return _poll_device(provider, flow, session, wait)
    return _poll_browser(provider, flow, session, wait)


def _cleanup_flow(session: str):
    flow = _FLOWS.pop(session, None)
    if flow and flow.get("server"):
        flow["server"].close()


def _poll_device(provider: str, flow: dict, session: str, wait: float) -> dict:
    """设备码流：一次（或少量）token 端点轮询。copilot=GitHub，nous=Portal。"""
    deadline = time.time() + max(0.0, min(wait, 20.0))
    interval = flow.get("interval", 5)
    while True:
        if provider == "copilot":
            payload = _github_device_poll(flow["device_code"])
        else:
            payload = _nous_device_poll(flow["device_code"])
        err_code = str((payload or {}).get("error") or "")
        if payload and "access_token" in payload:
            saved = _store_tokens(provider, payload)
            _cleanup_flow(session)
            return {"ok": True, "status": "done", **saved}
        if err_code == "slow_down":
            interval = min(interval + 1, 30)
        elif err_code and err_code != "authorization_pending":
            _cleanup_flow(session)
            desc = payload.get("error_description") or payload.get("detail") or err_code
            return _err(f"授权被拒：{desc}")
        if time.time() >= deadline:
            return {"ok": True, "status": "pending", "interval": interval}
        time.sleep(min(interval, max(1, deadline - time.time())))


def _github_device_poll(device_code: str) -> dict:
    try:
        r = requests.post(GITHUB_TOKEN_URL, headers={"Accept": "application/json"}, json={
            "client_id": GITHUB_CLIENT_ID, "device_code": device_code,
            "grant_type": DEVICE_CODE_GRANT}, timeout=20)
        return r.json()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _nous_device_poll(device_code: str) -> dict:
    try:
        r = requests.post(NOUS_TOKEN_URL, headers={"Accept": "application/json"}, data={
            "grant_type": NOUS_DEVICE_GRANT, "client_id": NOUS_CLIENT_ID,
            "device_code": device_code}, timeout=20)
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "json" not in ctype:
            # Portal 设备审批 UI 已知缺陷（Vercel WAF / 无审批界面）→ 引导走 sk- Key 兜底
            return {"error": "authorization_backend_error",
                    "detail": f"Portal 返回非 JSON（HTTP {r.status_code}）。设备审批可能不可用，"
                              f"建议改用「手动录入 Portal API Key（sk- 开头）」。"}
        return r.json()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _poll_browser(provider: str, flow: dict, session: str, wait: float) -> dict:
    srv = flow["server"]
    if not srv.wait(max(0.0, min(wait, 20.0))):
        return {"ok": True, "status": "pending", "interval": 2}
    try:
        if srv.error:
            return _err(f"授权失败：{srv.error}")
        code = (srv.code or "").strip()
        if not code:
            return _err("回调未携带授权码")
        payload = _exchange_code(provider, code, flow["verifier"])
        if not isinstance(payload, dict) or "access_token" not in payload:
            detail = payload.get("error_description") or payload.get("error") or str(payload)[:200] \
                if isinstance(payload, dict) else str(payload)[:200]
            return _err(f"换 token 失败：{detail}")
        saved = _store_tokens(provider, payload)
        return {"ok": True, "status": "done", **saved}
    finally:
        _cleanup_flow(session)


def _exchange_code(provider: str, code: str, verifier: str) -> dict:
    if provider == "codex":
        r = requests.post(CODEX_TOKEN_URL, headers={
            "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
            "User-Agent": "BAZZ-Agent/1.0"}, data={
            "grant_type": "authorization_code", "code": code,
            "code_verifier": verifier, "client_id": CODEX_CLIENT_ID,
            "redirect_uri": _redirect_uri(provider)}, timeout=20)
    elif provider == "anthropic-oauth":
        # 注意：anthropic token 端点要 JSON body（与标准表单不同）
        r = requests.post(ANTHROPIC_TOKEN_URL, json={
            "grant_type": "authorization_code", "code": code, "state": verifier,
            "client_id": ANTHROPIC_CLIENT_ID, "redirect_uri": _redirect_uri(provider),
            "code_verifier": verifier}, timeout=20)
    else:
        return {"error": f"provider {provider} 不支持 PKCE"}
    ctype = (r.headers.get("Content-Type") or "").lower()
    if "json" not in ctype:
        return {"error": f"HTTP {r.status_code} 非 JSON：{r.text[:160]}"}
    return r.json()


def _store_tokens(provider: str, payload: dict) -> dict:
    """登录成功入库。返回展示信息（account/expiry）。"""
    access = str(payload.get("access_token") or "")
    refresh = str(payload.get("refresh_token") or "")
    expires_in = _expires_in(payload)
    account = ""
    if provider == "copilot":
        # gh token 长命；account 取 GitHub 用户名
        gh_tok = access or refresh
        try:
            u = requests.get(GITHUB_USER_URL, headers={
                "Authorization": f"token {gh_tok}", "Accept": "application/json",
                "User-Agent": "BAZZ-Agent"}, timeout=15).json()
            account = str(u.get("login") or "")
        except Exception:
            pass
        _put(provider, refresh_token=gh_tok, access_token="", expires_at=0, account=account,
             auth_kind="oauth")
        # 立即兑换一次 Copilot token，验证连通
        tok = _copilot_exchange(gh_tok)
        return {"account": account, "expires_in": tok[1] if tok else 0}
    if provider == "codex":
        claims = _jwt_claims(access)
        auth_cl = claims.get("https://api.openai.com/auth") or {}
        account = str(auth_cl.get("chatgpt_account_id") or claims.get("email") or "")
    elif provider == "anthropic-oauth":
        claims = _jwt_claims(access)
        account = str(claims.get("email") or claims.get("sub") or "")[:48]
    elif provider == "nous":
        claims = _jwt_claims(access)
        account = str(claims.get("sub") or claims.get("email") or "")[:48]
    _put(provider, refresh_token=refresh, access_token=access,
         expires_at=time.time() + expires_in, account=account, auth_kind="oauth")
    return {"account": account, "expires_in": expires_in}


# ---------------- access token 获取（含自动刷新） ----------------

def get_access_token(provider: str) -> str:
    """取可用 Bearer token；未登录/刷新失败返回 ""（宁缺勿错，不抛异常）。"""
    provider = (provider or "").lower()
    if provider not in SUB_PROVIDERS:
        return ""
    st = _get(provider)
    if not st:
        return ""
    if st.get("auth_kind") == "api_key":
        return str(st.get("refresh_token") or "")   # 手动录入的 sk- key 存这里
    try:
        if provider == "copilot":
            return _copilot_access(st)
        if provider == "nous":
            return _oauth_access(provider, st, _nous_refresh)
        if provider == "codex":
            return _oauth_access(provider, st, _codex_refresh)
        if provider == "anthropic-oauth":
            return _oauth_access(provider, st, _anthropic_refresh)
    except Exception:
        return ""
    return ""


def _copilot_access(st: dict) -> str:
    gh_tok = str(st.get("refresh_token") or "")
    if not gh_tok:
        return ""
    cached = str(st.get("access_token") or "")
    exp = float(st.get("expires_at") or 0)
    if cached and time.time() < exp - REFRESH_SKEW:
        return cached
    tok = _copilot_exchange(gh_tok)
    if not tok:
        return ""
    token, ttl = tok
    _put("copilot", access_token=token, expires_at=time.time() + ttl)
    return token


def _copilot_exchange(gh_token: str):
    """gh token → 短命 Copilot API token；失败返回 None。"""
    try:
        r = requests.get(COPILOT_TOKEN_URL, headers={
            "Authorization": f"token {gh_token}", "Accept": "application/json",
            "Editor-Version": "vscode/1.99.0", "Editor-Plugin-Version": "copilot-chat/0.26.7",
            "User-Agent": "GitHubCopilotChat/0.26.7"}, timeout=20)
        if r.status_code != 200:
            return None
        d = r.json()
        token = str(d.get("token") or "")
        if not token:
            return None
        exp = d.get("expires_at")
        ttl = int(exp - time.time()) if isinstance(exp, (int, float)) else 1500
        return token, max(60, ttl)
    except Exception:
        return None


def _oauth_access(provider: str, st: dict, refresher) -> str:
    access = str(st.get("access_token") or "")
    exp = float(st.get("expires_at") or 0)
    if access and time.time() < exp - REFRESH_SKEW:
        return access
    refresh = str(st.get("refresh_token") or "")
    if not refresh:
        return ""
    payload = refresher(refresh)
    if not payload or not payload.get("access_token"):
        return ""
    new_refresh = str(payload.get("refresh_token") or refresh)   # rotating refresh：必须回存新值
    _put(provider, access_token=str(payload["access_token"]), refresh_token=new_refresh,
         expires_at=time.time() + _expires_in(payload))
    return str(payload["access_token"])


def _codex_refresh(refresh_token: str) -> dict:
    r = requests.post(CODEX_TOKEN_URL, headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
        "User-Agent": "BAZZ-Agent/1.0"}, data={
        "grant_type": "refresh_token", "refresh_token": refresh_token,
        "client_id": CODEX_CLIENT_ID}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"codex refresh HTTP {r.status_code}: {r.text[:160]}")
    return r.json()


def _anthropic_refresh(refresh_token: str) -> dict:
    r = requests.post(ANTHROPIC_TOKEN_URL, json={
        "grant_type": "refresh_token", "refresh_token": refresh_token,
        "client_id": ANTHROPIC_CLIENT_ID}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"anthropic refresh HTTP {r.status_code}: {r.text[:160]}")
    return r.json()


def _nous_refresh(refresh_token: str) -> dict:
    # Nous：refresh_token 放专用头（非标准），且为单次使用，成功后调用方必须回存新值
    r = requests.post(NOUS_TOKEN_URL, headers={
        "x-nous-refresh-token": refresh_token, "Accept": "application/json"},
        data={"grant_type": "refresh_token", "client_id": NOUS_CLIENT_ID}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"nous refresh HTTP {r.status_code}: {r.text[:160]}")
    return r.json()


# ---------------- 手动 API Key 兜底 / 状态 / 断开 ----------------

def set_api_key(provider: str, key: str) -> dict:
    """手动录入 API Key（Nous Portal 的 sk- key 等）。key 存 refresh_token 槽，DPAPI 加密。"""
    provider = (provider or "").lower()
    key = (key or "").strip()
    if provider not in SUB_PROVIDERS:
        return _err("不支持的提供方")
    if not key:
        return _err("key 不能为空")
    if provider == "nous" and not key.startswith("sk-"):
        return _err("Nous Portal 的 API Key 以 sk- 开头（portal.nousresearch.com → API Keys 生成）")
    _put(provider, refresh_token=key, access_token="", expires_at=0,
         account="API Key", auth_kind="api_key")
    return {"ok": True, "account": "API Key"}


def status() -> dict:
    """四家订阅连接状态（供前端提供方卡片）。"""
    out = {}
    for p in sorted(SUB_PROVIDERS):
        st = _get(p)
        connected = bool(get_access_token(p))
        out[p] = {
            "connected": connected,
            "account": str(st.get("account") or "") if st else "",
            "auth_kind": str(st.get("auth_kind") or "") if st else "",
            "expires_at": int(st.get("expires_at") or 0) if st else 0,
        }
    return out


def logout(provider: str) -> dict:
    provider = (provider or "").lower()
    if provider not in SUB_PROVIDERS:
        return _err("不支持的提供方")
    _drop(provider)
    return {"ok": True}


# ============================================================================
# 对话适配层：把 OpenAI /chat/completions 载荷适配到各家原生协议
# llm.py 的 _post() 在 provider ∈ SUB_PROVIDERS 时转调 post_chat()。
# 返回统一形态 {"choices":[{"message":{"content","tool_calls","reasoning_content"}}]}。
# ============================================================================

def post_chat(provider: str, payload: dict, timeout: int) -> dict:
    """统一发对话请求。订阅未登录时抛 RuntimeError（llm.py fallback 链会接住）。"""
    provider = (provider or "").lower()
    key = get_access_token(provider)
    if not key:
        raise RuntimeError("订阅未登录或 token 已失效（请在设置→订阅登录里重新授权）")
    if provider == "codex":
        return _codex_post(key, payload, timeout)
    if provider == "anthropic-oauth":
        return _anthropic_post(key, payload, timeout)
    # copilot / nous：OpenAI 兼容端点直连
    base = COPILOT_BASE if provider == "copilot" else NOUS_API
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if provider == "copilot":
        headers.update({"Editor-Version": "vscode/1.99.0",
                        "Editor-Plugin-Version": "copilot-chat/0.26.7",
                        "User-Agent": "GitHubCopilotChat/0.26.7"})
    r = requests.post(base + "/chat/completions", headers=headers, json=payload, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} · {r.text[:200]}")
    return r.json()


# ---------------- Codex：chat/completions → Responses API（SSE-only） ----------------

def _codex_convert_messages(messages: list) -> tuple:
    """OpenAI messages → (instructions, input_items)。工具调用历史转 function_call/function_call_output。"""
    instructions = ""
    items = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role") or "user"
        content = m.get("content")
        text = content if isinstance(content, str) else \
            "".join(p.get("text", "") for p in content or [] if isinstance(p, dict))
        if role == "system" or role == "developer":
            instructions = (instructions + "\n\n" + text).strip() if instructions else text
            continue
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": str(m.get("tool_call_id") or ""),
                          "output": text})
            continue
        item_role = "assistant" if role == "assistant" else "user"
        part_type = "output_text" if item_role == "assistant" else "input_text"
        if text:
            items.append({"type": "message", "role": item_role,
                          "content": [{"type": part_type, "text": text}]})
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments")
            items.append({"type": "function_call", "call_id": str(tc.get("id") or ""),
                          "name": str(fn.get("name") or ""),
                          "arguments": args if isinstance(args, str) else json.dumps(args or {}, ensure_ascii=False)})
    return instructions, items


def _codex_post(key: str, payload: dict, timeout: int) -> dict:
    instructions, items = _codex_convert_messages(payload.get("messages") or [])
    body: dict = {
        "model": payload.get("model") or "gpt-5.3-codex",
        # Codex 后端要求 instructions 非空、store:false、stream:true
        "instructions": instructions or "You are a helpful assistant.",
        "input": items or [{"type": "message", "role": "user",
                            "content": [{"type": "input_text", "text": "ping"}]}],
        "store": False, "stream": True,
    }
    mt = payload.get("max_tokens")
    if mt:
        body["max_output_tokens"] = int(mt)
    tools = payload.get("tools") or []
    if tools:
        body["tools"] = [{"type": "function",
                          "name": t.get("function", {}).get("name", ""),
                          "description": t.get("function", {}).get("description", ""),
                          "parameters": t.get("function", {}).get("parameters", {})}
                         for t in tools if isinstance(t, dict)]
        body["tool_choice"] = "auto"
    claims = _jwt_claims(key).get("https://api.openai.com/auth") or {}
    account_id = str(claims.get("chatgpt_account_id") or "")
    headers = {
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "originator": "codex_cli_rs", "User-Agent": "codex_cli_rs/0.0.1 (BAZZ Agent)",
        "OpenAI-Beta": "responses=experimental", "Accept": "text/event-stream",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    r = requests.post(CODEX_API, headers=headers, json=body, timeout=timeout, stream=True)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} · {r.text[:240]}")
    return _codex_parse_sse(r)


def _codex_parse_sse(r) -> dict:
    """解析 Codex Responses SSE → chat/completions 回包形态。"""
    text_parts, tool_calls, reasoning_parts = [], [], []
    try:
        for line in r.iter_lines():
            if not line:
                continue
            s = line.decode("utf-8", "ignore")
            if not s.startswith("data:"):
                continue
            p = s[5:].strip()
            if not p or p == "[DONE]":
                if p == "[DONE]":
                    break
                continue
            try:
                ev = json.loads(p)
            except Exception:
                continue
            etype = ev.get("type") or ""
            if etype == "response.output_text.delta":
                if ev.get("delta"):
                    text_parts.append(str(ev["delta"]))
            elif etype == "response.reasoning_summary_text.delta":
                if ev.get("delta"):
                    reasoning_parts.append(str(ev["delta"]))
            elif etype == "response.output_item.done":
                item = ev.get("item") or {}
                if item.get("type") == "function_call":
                    tool_calls.append({"id": item.get("call_id") or item.get("id") or "",
                                       "name": item.get("name") or "",
                                       "args_raw": item.get("arguments") or "{}"})
            elif etype == "response.completed":
                resp = ev.get("response") or {}
                for item in resp.get("output") or []:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "function_call":
                        tc = {"id": item.get("call_id") or item.get("id") or "",
                              "name": item.get("name") or "",
                              "args_raw": item.get("arguments") or "{}"}
                        if tc not in tool_calls:
                            tool_calls.append(tc)
                    elif item.get("type") == "message":
                        # completed 终态 message：兜底提取正文（delta 缺失时仍拿得到全文）
                        for part in item.get("content") or []:
                            if isinstance(part, dict) and part.get("type") in ("output_text", "text") \
                                    and part.get("text"):
                                text_parts.append(str(part["text"]))
                    elif item.get("type") == "reasoning":
                        for sm in item.get("summary") or []:
                            if isinstance(sm, dict) and sm.get("text"):
                                reasoning_parts.append(str(sm["text"]))
    finally:
        try:
            r.close()
        except Exception:
            pass
    content = "".join(text_parts).strip()
    msg: dict = {"content": content}
    if reasoning_parts:
        msg["reasoning_content"] = "".join(reasoning_parts).strip()
    if tool_calls:
        msg["tool_calls"] = [{"id": tc["id"] or f"call_{i}", "type": "function",
                              "function": {"name": tc["name"], "arguments": tc["args_raw"]}}
                             for i, tc in enumerate(tool_calls)]
    return {"choices": [{"message": msg}]}


# ---------------- Anthropic OAuth：chat/completions → Messages API ----------------

def _anthropic_convert(messages: list) -> tuple:
    """OpenAI messages → (system, anthropic_messages)。"""
    system_parts, out = [], []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role") or "user"
        content = m.get("content")
        text = content if isinstance(content, str) else \
            "".join(p.get("text", "") for p in content or [] if isinstance(p, dict))
        if role in ("system", "developer"):
            system_parts.append(text)
            continue
        if role == "tool":
            out.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": str(m.get("tool_call_id") or ""),
                 "content": [{"type": "text", "text": text}]}]})
            continue
        blocks = []
        if text:
            blocks.append({"type": "text", "text": text})
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}") if isinstance(fn.get("arguments"), str) \
                    else (fn.get("arguments") or {})
            except Exception:
                args = {}
            blocks.append({"type": "tool_use", "id": str(tc.get("id") or ""),
                           "name": str(fn.get("name") or ""), "input": args})
        if blocks:
            out.append({"role": "assistant" if role == "assistant" else "user", "content": blocks})
    return "\n\n".join(p for p in system_parts if p), out


def _anthropic_post(key: str, payload: dict, timeout: int) -> dict:
    system, msgs = _anthropic_convert(payload.get("messages") or [])
    body: dict = {
        "model": payload.get("model") or "claude-sonnet-5",
        "max_tokens": max(64, int(payload.get("max_tokens") or 1200)),
        "messages": msgs or [{"role": "user", "content": [{"type": "text", "text": "ping"}]}],
    }
    if system:
        body["system"] = system
    temp = payload.get("temperature")
    if isinstance(temp, (int, float)):
        body["temperature"] = max(0.0, min(1.0, temp))
    tools = payload.get("tools") or []
    if tools:
        body["tools"] = [{"name": t.get("function", {}).get("name", ""),
                          "description": t.get("function", {}).get("description", ""),
                          "input_schema": t.get("function", {}).get("parameters",
                                              {"type": "object", "properties": {}})}
                         for t in tools if isinstance(t, dict)]
        body["tool_choice"] = {"type": "auto"}
    r = requests.post(ANTHROPIC_API + "/messages", headers={
        "Authorization": f"Bearer {key}", "anthropic-version": "2023-06-01",
        "anthropic-beta": "oauth-2025-04-20", "x-app": "cli",
        "User-Agent": "claude-cli/1.0.0 (external, cli)",
        "Content-Type": "application/json"}, json=body, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} · {r.text[:240]}")
    return _anthropic_parse(r.json())


def _anthropic_parse(d: dict) -> dict:
    """Messages API 回包 → chat/completions 形态。"""
    if not isinstance(d, dict) or d.get("type") == "error":
        err = (d or {}).get("error") or {}
        raise RuntimeError(f"anthropic 回包异常 · {str(err.get('message') or d)[:200]}")
    content, tool_calls, reasoning_parts = [], [], []
    for b in d.get("content") or []:
        if not isinstance(b, dict):
            continue
        btype = b.get("type")
        if btype == "text":
            content.append(str(b.get("text") or ""))
        elif btype == "thinking":
            reasoning_parts.append(str(b.get("thinking") or ""))
        elif btype == "tool_use":
            tool_calls.append({"id": b.get("id") or "", "name": b.get("name") or "",
                               "args": b.get("input") or {}})
    msg: dict = {"content": "".join(content).strip()}
    if reasoning_parts:
        msg["reasoning_content"] = "\n\n".join(reasoning_parts).strip()
    if tool_calls:
        msg["tool_calls"] = [{"id": tc["id"] or f"call_{i}", "type": "function",
                              "function": {"name": tc["name"],
                                           "arguments": json.dumps(tc["args"], ensure_ascii=False)}}
                             for i, tc in enumerate(tool_calls)]
    return {"choices": [{"message": msg}]}
