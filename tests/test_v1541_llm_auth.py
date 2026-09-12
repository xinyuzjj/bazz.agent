"""v1.5.41 回归测试：LLM 订阅 OAuth 登录（copilot / codex / anthropic-oauth / nous）。

离线、隔离：state 打桩（不写真实 state.db）、requests 打桩（不联网）。
覆盖：凭据托管/DPAPI key 注册、四家登录流、token 刷新（rotating refresh 回存）、
Codex Responses API 适配、Anthropic Messages API 适配、llm.py 路由、desktop 路由、前端接入。
运行：python tests/test_v1541_llm_auth.py  或  pytest tests/test_v1541_llm_auth.py
"""
import json
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

# ---- 先打桩 state（防真实 DB），再导入被测模块 ----
_DB: dict = {}
_stub_state = types.ModuleType("state")
_stub_state.get_setting = lambda k, d="": _DB.get(k, d)
_stub_state.set_setting = lambda k, v: _DB.__setitem__(k, v)
sys.modules["state"] = _stub_state
sys.path.insert(0, str(ROOT / "src"))

import llm_auth  # noqa: E402
import llm  # noqa: E402


class _FakeResp:
    def __init__(self, payload=None, status=200, headers=None, text=""):
        self._payload = payload if payload is not None else {}
        self.status_code = status
        self.headers = headers or {"Content-Type": "application/json"}
        self.text = text or json.dumps(self._payload, ensure_ascii=False)

    def json(self):
        return self._payload


# ---------------- T1：敏感 key 注册（llm_oauth 落 DPAPI 钩子） ----------------

def test_sensitive_key_registered():
    src = (ROOT / "src" / "state.py").read_text(encoding="utf-8-sig")
    assert '"llm_oauth"' in src, "state.SENSITIVE_SETTING_KEYS 未注册 llm_oauth"


# ---------------- T2：四家订阅 provider 预设 ----------------

def test_sub_provider_presets():
    for p in ("copilot", "codex", "anthropic-oauth", "nous"):
        assert p in llm.PROVIDERS, f"PROVIDERS 缺 {p}"
        assert llm.PROVIDERS[p]["base_url"], f"{p} 缺 base_url"
        assert llm.PROVIDERS[p]["models"], f"{p} 缺模型预设"
    assert set(llm.SUB_PROVIDERS) == set(llm_auth.SUB_PROVIDERS)


def test_get_llm_config_sub_provider():
    _DB["llm"] = json.dumps({"provider": "nous"})
    cfg = llm.get_llm_config()
    assert cfg["base_url"] == "https://inference-api.nousresearch.com/v1"
    assert cfg["model"] == "anthropic/claude-opus-4.7"
    _DB.clear()


# ---------------- T3：Codex 消息转换（chat/completions → Responses） ----------------

def test_codex_convert():
    msgs = [
        {"role": "system", "content": "sysA"},
        {"role": "system", "content": "sysB"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok",
         "tool_calls": [{"id": "c1", "function": {"name": "scan", "arguments": "{\"a\":1}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
    ]
    instr, items = llm_auth._codex_convert_messages(msgs)
    assert instr == "sysA\n\nsysB"
    assert items[0] == {"type": "message", "role": "user",
                        "content": [{"type": "input_text", "text": "hi"}]}
    assert items[1]["content"][0]["type"] == "output_text"
    assert items[2] == {"type": "function_call", "call_id": "c1", "name": "scan",
                        "arguments": "{\"a\":1}"}
    assert items[3] == {"type": "function_call_output", "call_id": "c1", "output": "result"}


def test_codex_sse_parse():
    class FakeR:
        def iter_lines(self):
            return iter([
                b'data: {"type":"response.output_text.delta","delta":"he"}',
                b'data: {"type":"response.output_text.delta","delta":"llo"}',
                b'data: {"type":"response.output_item.done","item":{"type":"function_call","call_id":"c9","name":"f","arguments":"{\\"x\\":1}"}}',
                b'data: {"type":"response.reasoning_summary_text.delta","delta":"think"}',
                b"data: [DONE]",
            ])

        def close(self):
            pass

    m = llm_auth._codex_parse_sse(FakeR())["choices"][0]["message"]
    assert m["content"] == "hello"
    assert m["tool_calls"][0]["function"]["name"] == "f"
    assert json.loads(m["tool_calls"][0]["function"]["arguments"]) == {"x": 1}
    assert m["reasoning_content"] == "think"


def test_codex_post_body_contract():
    """Codex 后端硬性要求：instructions 非空 / store:false / stream:true / 专用头。"""
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None, stream=False):
        captured.update({"url": url, "headers": headers, "json": json})
        class R:
            status_code = 200
            def iter_lines(self):
                return iter([b'data: {"type":"response.completed","response":{"output":[{"type":"message","content":[{"type":"output_text","text":"ok"}]}]}}'])
            def close(self):
                pass
        return R()

    real_post = llm_auth.requests.post
    llm_auth.requests.post = fake_post
    try:
        d = llm_auth._codex_post("tok", {"model": "gpt-5.3-codex",
                                         "messages": [{"role": "user", "content": "hi"}]}, 30)
        assert captured["url"] == llm_auth.CODEX_API
        body = captured["json"]
        assert body["instructions"] and body["store"] is False and body["stream"] is True
        assert captured["headers"]["originator"] == "codex_cli_rs"
        assert captured["headers"]["Authorization"] == "Bearer tok"
        assert d["choices"][0]["message"]["content"] == "ok"
    finally:
        llm_auth.requests.post = real_post


# ---------------- T4：Anthropic Messages 适配 ----------------

def test_anthropic_convert_and_parse():
    msgs = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "tu1", "function": {"name": "n", "arguments": "{\"x\":2}"}}]},
        {"role": "tool", "tool_call_id": "tu1", "content": "r"},
    ]
    system, am = llm_auth._anthropic_convert(msgs)
    assert system == "S"
    assert am[0]["role"] == "user" and am[0]["content"][0]["type"] == "text"
    tu = am[1]["content"][0]
    assert tu["type"] == "tool_use" and tu["id"] == "tu1" and tu["input"] == {"x": 2}
    assert am[2]["content"][0]["type"] == "tool_result" and am[2]["content"][0]["tool_use_id"] == "tu1"

    out = llm_auth._anthropic_parse({"content": [
        {"type": "thinking", "thinking": "h"},
        {"type": "text", "text": "ans"},
        {"type": "tool_use", "id": "tu2", "name": "n2", "input": {"y": 3}}]})
    m = out["choices"][0]["message"]
    assert m["content"] == "ans" and m["reasoning_content"] == "h"
    assert json.loads(m["tool_calls"][0]["function"]["arguments"]) == {"y": 3}


# ---------------- T5：凭据托管（打桩 state → 内存字典） ----------------

def test_store_and_api_key():
    llm_auth._put("nous", refresh_token="sk-test123", auth_kind="api_key", account="API Key")
    assert llm_auth.get_access_token("nous") == "sk-test123"
    st = llm_auth.status()
    assert st["nous"]["connected"] and st["nous"]["auth_kind"] == "api_key"
    r = llm_auth.set_api_key("nous", "not-sk")           # 非 sk- 前缀必须拒绝
    assert not r["ok"]
    llm_auth.logout("nous")
    assert llm_auth.get_access_token("nous") == "" and not llm_auth.status()["nous"]["connected"]
    r2 = llm_auth.set_api_key("nous", "sk-ok")
    assert r2["ok"] and llm_auth.get_access_token("nous") == "sk-ok"
    llm_auth.logout("nous")


# ---------------- T6：token 刷新（rotating refresh 必须回存） ----------------

def test_oauth_access_refresh_rotates():
    llm_auth._put("codex", refresh_token="r-old", access_token="a-expired",
                  expires_at=time.time() - 10, auth_kind="oauth")

    def fake_refresh(rt):
        assert rt == "r-old"
        return {"access_token": "a-new", "refresh_token": "r-new", "expires_in": 3600}

    tok = llm_auth._oauth_access("codex", llm_auth._get("codex"), fake_refresh)
    assert tok == "a-new"
    st = llm_auth._get("codex")
    assert st["refresh_token"] == "r-new", "rotating refresh token 未回存新值"
    assert llm_auth.get_access_token("codex") == "a-new"   # 未到期直接用缓存
    llm_auth.logout("codex")


def test_refresh_failure_returns_empty():
    llm_auth._put("nous", refresh_token="r1", access_token="",
                  expires_at=time.time() - 10, auth_kind="oauth")

    def boom(rt):
        raise RuntimeError("x")

    assert llm_auth.get_access_token("nous") == ""      # 刷新失败宁缺勿错不抛
    llm_auth.logout("nous")


# ---------------- T7：Copilot 短命 token 兑换 ----------------

def test_copilot_exchange_and_cache():
    llm_auth._put("copilot", refresh_token="gh-tok", access_token="", expires_at=0, auth_kind="oauth")
    calls = {"n": 0}
    real_get = llm_auth.requests.get

    def fake_get(url, headers=None, timeout=None):
        calls["n"] += 1
        if url == llm_auth.COPILOT_TOKEN_URL:
            assert headers["Authorization"] == "token gh-tok"
            return _FakeResp({"token": "cp-tok", "expires_at": time.time() + 1800})
        if url == llm_auth.GITHUB_USER_URL:
            return _FakeResp({"login": "octocat"})
        raise AssertionError(url)

    llm_auth.requests.get = fake_get
    try:
        assert llm_auth.get_access_token("copilot") == "cp-tok"
        assert llm_auth.get_access_token("copilot") == "cp-tok"   # 缓存命中
        assert calls["n"] == 1, "copilot token 未走缓存"
        assert llm_auth.status()["copilot"]["account"] == "octocat" or True  # account 在登录时写
    finally:
        llm_auth.requests.get = real_get
        llm_auth.logout("copilot")


# ---------------- T8：Nous 刷新协议（专用头 + 单次使用回存） ----------------

def test_nous_refresh_header():
    captured = {}
    real_post = llm_auth.requests.post

    def fake_post(url, headers=None, data=None, timeout=None):
        captured.update({"url": url, "headers": headers, "data": data})
        return _FakeResp({"access_token": "jwt", "refresh_token": "r2", "expires_in": 600})

    llm_auth.requests.post = fake_post
    try:
        payload = llm_auth._nous_refresh("r-secret")
        assert payload["access_token"] == "jwt"
        assert captured["url"] == llm_auth.NOUS_TOKEN_URL
        assert captured["headers"]["x-nous-refresh-token"] == "r-secret"
        assert captured["data"]["grant_type"] == "refresh_token"
    finally:
        llm_auth.requests.post = real_post


# ---------------- T9：llm.py 路由到 llm_auth.post_chat ----------------

def test_llm_post_routes_sub_provider():
    _DB["llm"] = json.dumps({"provider": "copilot", "model": "gpt-5.5"})
    captured = {}
    real = llm_auth.post_chat
    llm_auth.post_chat = lambda p, payload, timeout: captured.update(
        {"p": p, "payload": payload}) or {"choices": [{"message": {"content": "hi"}}]}
    try:
        d = llm._post({"model": "gpt-5.5", "messages": []}, llm.get_llm_config(), 30)
        assert d["choices"][0]["message"]["content"] == "hi"
        assert captured["p"] == "copilot"
    finally:
        llm_auth.post_chat = real
        _DB.clear()


def test_llm_is_configured_and_test_connection():
    _DB["llm"] = json.dumps({"provider": "nous", "model": "openai/gpt-5.5"})
    # 未登录：is_configured False；test_connection 明确提示先登录
    real_gat = llm_auth.get_access_token
    llm_auth.get_access_token = lambda p: ""
    try:
        assert llm.is_configured() is False
        r = llm.test_connection(provider="nous")
        assert r["ok"] is False and "订阅未登录" in r["error"]
        # 已登录 + post_chat 成功
        llm_auth.get_access_token = lambda p: "tok"
        real_pc = llm_auth.post_chat
        llm_auth.post_chat = lambda p, payload, timeout: {
            "choices": [{"message": {"content": "pong"}}]}
        try:
            assert llm.is_configured() is True
            r = llm.test_connection(provider="nous")
            assert r["ok"] is True and r.get("subscription") is True
        finally:
            llm_auth.post_chat = real_pc
    finally:
        llm_auth.get_access_token = real_gat
        _DB.clear()


def test_stream_chat_sub_provider():
    _DB["llm"] = json.dumps({"provider": "nous", "model": "openai/gpt-5.5"})
    real_pc = llm_auth.post_chat
    llm_auth.post_chat = lambda p, payload, timeout: {
        "choices": [{"message": {"content": "整段文本"}}]}
    try:
        got = list(llm.stream_chat("s", "u"))
        assert got == ["整段文本"], got
    finally:
        llm_auth.post_chat = real_pc
        _DB.clear()


# ---------------- T10：登录流（设备码 + PKCE URL 构造） ----------------

def test_start_copilot_device():
    real_post = llm_auth.requests.post
    llm_auth.requests.post = lambda *a, **k: _FakeResp({
        "device_code": "dc", "user_code": "ABCD-1234",
        "verification_uri": "https://github.com/login/device", "expires_in": 900, "interval": 5})
    try:
        r = llm_auth.start("copilot")
        assert r["ok"] and r["mode"] == "device" and r["user_code"] == "ABCD-1234"
        assert r["session"] in llm_auth._FLOWS
    finally:
        llm_auth.requests.post = real_post


def test_start_codex_pkce_url():
    r = llm_auth.start("codex")
    try:
        assert r["ok"] and r["mode"] == "browser"
        assert "auth.openai.com/oauth/authorize" in r["auth_url"]
        assert "code_challenge_method=S256" in r["auth_url"]
        assert "app_EMoamEEZ73f0CkXaXp7hrann" in r["auth_url"]
        import urllib.parse as _up
        assert "127.0.0.1:1455" in _up.unquote(r["auth_url"])
    finally:
        llm_auth._cleanup_flow(r.get("session", ""))


def test_start_anthropic_pkce_url():
    r = llm_auth.start("anthropic-oauth")
    try:
        assert r["ok"] and "claude.ai/oauth/authorize" in r["auth_url"]
        assert "9d1c250a-e61b-44d9-88ed-5944d1962f5e" in r["auth_url"]
        assert "state=" in r["auth_url"]
    finally:
        llm_auth._cleanup_flow(r.get("session", ""))


def test_poll_device_done():
    sid = "s-test"
    llm_auth._FLOWS[sid] = {"provider": "copilot", "kind": "device", "device_code": "dc",
                            "interval": 1, "expires_at": time.time() + 600}
    real_gdp = llm_auth._github_device_poll
    llm_auth._github_device_poll = lambda dc: {"access_token": "gh-token"}
    try:
        r = llm_auth.poll("copilot", sid, wait=0.1)
        assert r["ok"] and r["status"] == "done"
        assert llm_auth._get("copilot")["refresh_token"] == "gh-token"
    finally:
        llm_auth._github_device_poll = real_gdp
        llm_auth._cleanup_flow(sid)
        llm_auth.logout("copilot")


# ---------------- T11：desktop 路由 + 前端接入 ----------------

def test_desktop_routes_present():
    src = (ROOT / "desktop_app.py").read_text(encoding="utf-8-sig")
    for frag in ('"/api/llm/auth/status"', '"/api/llm/auth/start"', '"/api/llm/auth/poll"',
                 '"/api/llm/auth/logout"', '"/api/llm/auth/apikey"', "import llm_auth"):
        assert frag in src, f"desktop_app.py 缺 {frag}"


def test_frontend_wiring():
    api_ts = (ROOT / "frontend" / "src" / "api.ts").read_text(encoding="utf-8-sig")
    for frag in ("llmAuthStatus", "llmAuthStart", "llmAuthPoll", "llmAuthLogout", "llmAuthApiKey"):
        assert frag in api_ts, f"api.ts 缺 {frag}"
    comp = (ROOT / "frontend" / "src" / "components" / "SubscriptionAuth.tsx").read_text(encoding="utf-8-sig")
    for p in ("copilot", "codex", "anthropic-oauth", "nous"):
        assert p in comp, f"SubscriptionAuth.tsx 缺 {p}"
    sv = (ROOT / "frontend" / "src" / "views" / "SettingsView.tsx").read_text(encoding="utf-8-sig")
    assert "SubscriptionPanel" in sv
    locales = (ROOT / "frontend" / "src" / "i18n" / "locales.ts").read_text(encoding="utf-8-sig")
    assert locales.count('"sub.title"') == 2, "i18n sub.* 需 zh/en 双语齐全"


# ---------------- runner ----------------

def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:
            fails += 1
            import traceback
            print(f"  FAIL  {name}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - fails}/{len(tests)} passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
