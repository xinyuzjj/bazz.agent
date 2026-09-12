"""v1.5.30 回归测试：技能「本地后端不可达」根因修复。

现象：开启代理后 coin-report / market-data 全部报
      {"error":"本地后端不可达(8080/8081): fetch failed"}
      + "[auto-proxy] 首次网络失败，已自动启用代理 … 重试（仍失败）"
      用户据此以为代理没配好，反复折腾代理池仍无解。

根因（实测确认）：
  1) 主因 —— v1.5.29 F06 的沙箱环境清洗按子串（TOKEN/SECRET/AUTH/...）剥离凭据，
     把应用自有的本机回环令牌 BAZZ_AUTH_TOKEN 一并删掉 → 技能子进程访问
     127.0.0.1:8080/api/* 一律 HTTP 401。
     证据：直连探测 8080 返回 HTTP 401（不是连不上）；8081 未监听；
           git log -S 确认 _ENV_DENY_SUBSTR 由 v1.5.29(e32d762) 引入，
           而 v1.5.28 的 subprocess.run 不传 env、继承完整环境 → 属新引入的回归。
  2) 帮凶 —— CLI 的端口回退只保留最后一条错误（lastErr），把 8080 的 401
     覆盖成 8081 的连接失败，于是真因被伪装成「不可达」。

  ⚠️ 曾一度把 NO_PROXY 判为主因（据此改了 proxy_pool / proxy-preload.cjs）。
     复测证明该判断有误：用户机器上 NO_PROXY 在 User/Machine 级均为空，
     setdefault 本该生效，代理并未劫持本机地址。相关改动保留为加固，但不是根因。

本文件离线运行：AST 抽函数 + 桩环境，不联网、不启进程、不写 state.db。
运行：python tests/test_v1530_local_backend.py
"""
import ast
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]


def tree(file: str):
    return ast.parse((ROOT / file).read_text(encoding="utf-8-sig"), filename=file)


def function(file: str, name: str, env: dict | None = None):
    """从模块中抽取单个函数定义，在给定桩环境中 exec（不触发模块级副作用）。"""
    node = next(n for n in tree(file).body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    ns = {"Dict": Dict, "Any": Any}
    if env:
        ns.update(env)
    exec(compile(ast.Module(body=[node], type_ignores=[]), file, "exec"), ns)
    return ns[name]


def assignment(file: str, name: str):
    for n in tree(file).body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name
                                            for t in n.targets):
            return ast.literal_eval(n.value)
    raise KeyError(name)


def unparse(file: str, name: str) -> str:
    node = next(n for n in tree(file).body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    return ast.unparse(node)


def regex_const(file: str, name: str):
    """抽取模块级正则常量（re.compile(...) 不是字面量，literal_eval 取不到）。"""
    import re as _re
    node = next(n for n in tree(file).body
                if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name for t in n.targets))
    ns = {"re": _re}
    exec(compile(ast.Module(body=[node], type_ignores=[]), file, "exec"), ns)
    return ns[name]


def src(file: str) -> str:
    return (ROOT / file).read_text(encoding="utf-8-sig")


# ---------------- 1. _ensure_no_proxy 必须是「并集」，不是「缺省填充」 ----------------

def _run_ensure_no_proxy(initial: dict) -> dict:
    """在桩 os.environ 上跑 _ensure_no_proxy，返回最终环境。"""
    bypass = assignment("src/proxy_pool.py", "_LOCAL_BYPASS")
    fake_os = SimpleNamespace(environ=dict(initial))
    fn = function("src/proxy_pool.py", "_ensure_no_proxy",
                  {"os": fake_os, "_LOCAL_BYPASS": bypass})
    fn()
    return fake_os.environ


def test_no_proxy_added_when_absent():
    env = _run_ensure_no_proxy({})
    assert "127.0.0.1" in env["NO_PROXY"], env
    assert "localhost" in env["NO_PROXY"], env
    assert "::1" in env["NO_PROXY"], env


def test_no_proxy_fixed_when_empty_string():
    """空串是最阴的病因：setdefault 认为键已存在，直接放行。"""
    env = _run_ensure_no_proxy({"NO_PROXY": ""})
    assert "127.0.0.1" in env["NO_PROXY"], env
    assert "localhost" in env["NO_PROXY"], env


def test_no_proxy_merged_when_present_without_local():
    env = _run_ensure_no_proxy({"NO_PROXY": "example.com,foo.bar"})
    assert "example.com" in env["NO_PROXY"], "原有条目不得丢失"
    assert "foo.bar" in env["NO_PROXY"], "原有条目不得丢失"
    assert "127.0.0.1" in env["NO_PROXY"], env
    assert "localhost" in env["NO_PROXY"], env


def test_no_proxy_no_duplicate_when_already_present():
    env = _run_ensure_no_proxy({"NO_PROXY": "127.0.0.1,localhost,::1"})
    parts = [p for p in env["NO_PROXY"].split(",") if p]
    assert len(parts) == len(set(parts)), f"出现重复项: {env['NO_PROXY']}"


def test_no_proxy_lowercase_key_also_written():
    """同时写小写键，兼容只读 no_proxy 的实现。"""
    env = _run_ensure_no_proxy({})
    assert env.get("no_proxy") == env.get("NO_PROXY"), env


def test_apply_env_no_longer_uses_setdefault():
    body = unparse("src/proxy_pool.py", "_apply_env")
    assert "setdefault" not in body, "不得再用 setdefault 设置 NO_PROXY（会被已有值挡住）"
    assert "_ensure_no_proxy" in body, "应调用 _ensure_no_proxy 做强制并集"


# ---------------- 2. proxy-preload.cjs 必须显式传 noProxy ----------------

def test_preload_passes_explicit_noproxy():
    s = src("scripts/proxy-preload.cjs")
    assert "noProxy" in s, "proxy-preload.cjs 必须显式传 noProxy（不能只依赖环境变量）"
    assert "127.0.0.1" in s, "noProxy 必须包含 127.0.0.1"
    assert "localhost" in s, "noProxy 必须包含 localhost"


# ---------------- 3. 本地故障必须与网络故障区分开 ----------------

def _looks(name: str, text: str) -> bool:
    """在注入了两个模块级正则常量的桩环境里跑 looks_* 判定。"""
    import re as _re
    env = {"re": _re,
           "_LOCAL_BACKEND_ERR_RE": regex_const("src/skills_client.py", "_LOCAL_BACKEND_ERR_RE"),
           "_NET_ERR_RE": regex_const("src/skills_client.py", "_NET_ERR_RE")}
    return function("src/skills_client.py", name, env)(text)


def test_real_error_text_is_detected_as_local_backend():
    real = '{"error":"本地后端不可达(8080/8081): fetch failed"}'
    assert _looks("looks_local_backend_error", real) is True


def test_same_text_also_matches_network_regex():
    """正因两者都命中，才必须先判本地 —— 断言这一点防止有人删掉前置分支。"""
    real = '{"error":"本地后端不可达(8080/8081): fetch failed"}'
    assert _looks("looks_network_error", real) is True


def test_plain_network_error_is_not_local():
    assert _looks("looks_local_backend_error", "fetch failed: ECONNRESET") is False


def test_hint_mentions_no_proxy_and_localhost():
    fn = function("src/skills_client.py", "local_backend_hint")
    h = fn()
    assert "127.0.0.1" in h and "NO_PROXY" in h, h
    assert "代理" in h, "必须说清与外网/代理无关"


# ---------------- 4. 判定顺序：本地分支必须先于代理兜底 ----------------

def test_sandbox_checks_local_before_proxy_retry():
    body = unparse("src/exec_sandbox.py", "run_skill_cmd")
    i_local = body.find("looks_local_backend_error")
    i_net = body.find("looks_network_error")
    assert i_local != -1, "run_skill_cmd 缺少本机后端前置判定"
    assert i_local < i_net, "本机后端判定必须在代理重试之前（否则会白白重试一次并误导用户）"


def test_run_skill_checks_local_before_proxy_retry():
    body = unparse("src/skills_client.py", "run_skill")
    i_local = body.find("looks_local_backend_error")
    i_net = body.find("looks_network_error")
    assert i_local != -1 and i_local < i_net, "run_skill 的判定顺序不对"


def test_agent_core_hint_applies_to_all_skills():
    """本机后端提示必须对任意技能生效，不能只在钱包/广场分支里。

    注意：`_is_wallet_skill(name)` 在 _tool_run_skill 里出现两次（"未安装"分支在前），
    所以这里锚定只在失败提示块里出现的 `_wallet_fail_hint(out)`。
    """
    body = unparse("src/agent_core.py", "_tool_run_skill")
    i_local = body.find("looks_local_backend_error")
    i_wallet_hint = body.find("_wallet_fail_hint(out)")
    assert i_local != -1, "_tool_run_skill 缺少本机后端提示"
    assert i_wallet_hint != -1, "未找到钱包失败提示分支，测试锚点失效"
    assert i_local < i_wallet_hint, "本机后端提示必须排在钱包/广场分支之前（对所有技能生效）"


def test_square_hint_local_branch_precedes_network_branch():
    body = unparse("src/agent_core.py", "_square_post_fail_hint")
    i_local = body.find("本地后端不可达")
    i_net = body.find("network|timeout")
    assert i_local != -1, "_square_post_fail_hint 缺少本机后端分支"
    assert i_local < i_net, "本机后端分支必须在 network 分支之前"


# ---------------- 5. 真正的根因：BAZZ_AUTH_TOKEN 被沙箱误删 ----------------

def _run_sandbox_env(initial: dict) -> dict:
    deny = assignment("src/exec_sandbox.py", "_ENV_DENY_SUBSTR")
    allow = assignment("src/exec_sandbox.py", "_ENV_ALLOW_EXACT")
    fake_os = SimpleNamespace(environ=dict(initial))
    fn = function("src/exec_sandbox.py", "_sandbox_env",
                  {"os": fake_os, "_ENV_DENY_SUBSTR": deny, "_ENV_ALLOW_EXACT": allow})
    return fn()


def test_auth_token_survives_sandbox_env():
    """v1.5.29 的回归点：令牌被剥离 → 技能访问本机后端一律 401。"""
    env = _run_sandbox_env({"BAZZ_AUTH_TOKEN": "tok-abc", "PATH": "/usr/bin"})
    assert env.get("BAZZ_AUTH_TOKEN") == "tok-abc", \
        "BAZZ_AUTH_TOKEN 被剥离 —— 技能将无法访问本机后端（全部 401）"


def test_third_party_credentials_still_stripped():
    env = _run_sandbox_env({
        "BAZZ_AUTH_TOKEN": "tok",
        "BINANCE_API_SECRET": "s", "MY_API_KEY": "k",
        "FOO_SECRET": "x", "DB_PASSWORD": "p",
    })
    for k in ("BINANCE_API_SECRET", "MY_API_KEY", "FOO_SECRET", "DB_PASSWORD"):
        assert k not in env, f"{k} 属于第三方凭据，必须剥离"
    assert env.get("BAZZ_AUTH_TOKEN") == "tok", "应用自有令牌不得被连带剥离"


def test_runtime_env_vars_survive():
    env = _run_sandbox_env({
        "BAZZ_AUTH_TOKEN": "tok", "BAZZ_PORT": "8080",
        "BAZZ_WORKSPACE": r"F:\1\BAZZ.AGENT\workspace",
        "HTTP_PROXY": "http://127.0.0.1:7899",
        "NO_PROXY": "127.0.0.1,localhost",
        "NODE_OPTIONS": "--require=x",
    })
    for k in ("BAZZ_PORT", "BAZZ_WORKSPACE", "HTTP_PROXY", "NO_PROXY", "NODE_OPTIONS"):
        assert env.get(k), f"{k} 是运行必需项，不应被剥离"


# ---------------- 6. 技能 CLI 不得掩盖真实错误 ----------------

SKILL_CLIS = ["coin-report", "market-data", "portfolio-review", "risk-guard", "track-monitor"]


def test_skill_clis_keep_per_port_errors():
    """旧实现只留最后一个端口的错误，把 :8080 的 HTTP 401 掩盖成「fetch failed」。"""
    for name in SKILL_CLIS:
        rel = f".agents/skills/{name}/scripts/cli.mjs"
        s = src(rel)
        assert "errs.push" in s, f"{name}: 未聚合逐端口错误"
        assert "lastErr" not in s, f"{name}: 仍有 lastErr 残留（会掩盖真实错误）"
        assert "本地后端不可达" in s, f"{name}: 错误文案被破坏，Python 侧判定会失效"


def test_skill_clis_use_auth_token_header():
    """技能必须读取 BAZZ_AUTH_TOKEN 并放进 X-BAZZ-Token —— 与根因修复配套。"""
    for name in SKILL_CLIS:
        s = src(f".agents/skills/{name}/scripts/cli.mjs")
        assert "BAZZ_AUTH_TOKEN" in s, f"{name}: 未读取 BAZZ_AUTH_TOKEN"
        assert "X-BAZZ-Token" in s, f"{name}: 未设置 X-BAZZ-Token 头"


# ---------------- 主入口 ----------------

def main():
    fails = []
    cases = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for name, fn in cases:
        try:
            fn()
            print(f"  PASS {name}")
        except Exception as e:
            fails.append((name, e))
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(cases) - len(fails)}/{len(cases)} passed")
    if fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
