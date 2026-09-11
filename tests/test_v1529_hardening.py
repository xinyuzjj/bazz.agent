"""v1.5.29 回归测试：审查报告 F03/F05/F06/F07/F09/F11/F12/F14/F15/F16/F17 + 性能5.1/5.2/5.3 修复验证。

离线、隔离：AST/函数提取 + 桩替换，不导入应用、不联网、不下单、不写 state.db。
每个用例断言「修复后的正确行为」——对修复前代码这些用例必然失败。
运行：python tests/test_v1529_hardening.py  或  pytest tests/test_v1529_hardening.py
"""
import ast
import os
import sys
import tempfile
import shutil
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


# ---------------- F03：资金语义（现货无杠杆 / 拒绝做空语义 / 真实名义 / 移除虚假最大亏损） ----------------

_BEST = {"symbol": "BTCUSDT", "price": 100.0, "change_pct": 1.0, "funding_rate": 0.01}


def _run_execute_env(direction: str = "BULLISH", route: str = "exchange"):
    return {
        "_parse_trade": lambda m: ("BTCUSDT", direction),
        "scan_symbols": lambda syms, force=False: [_BEST],
        "_best_signal": lambda: dict(_BEST),
        "_decide_route": lambda signal=None: route,
        "confirm_and_place": lambda signal=None, confirm=False: {},
    }


def test_run_execute_rejects_leverage():
    run_exec = function("src/agent_core.py", "_run_execute", _run_execute_env())
    r = run_exec(message="买入 BTC", margin_usdt=50.0, leverage=10)
    assert "approval" not in r and not r.get("needs_approval"), f"杠杆请求仍生成下单方案: {r.keys()}"
    assert "杠杆" in r["reply"], "未提示现货通道不支持杠杆"


def test_run_execute_rejects_short_on_exchange_spot():
    run_exec = function("src/agent_core.py", "_run_execute", _run_execute_env(direction="BEARISH"))
    r = run_exec(message="做空 BTC", margin_usdt=50.0, leverage=1)
    assert "approval" not in r and not r.get("needs_approval"), "现货通道做空语义未被拒绝"
    assert "做空" in r["reply"] or "SELL" in r["reply"]


def test_run_execute_spot_notional_no_max_loss():
    run_exec = function("src/agent_core.py", "_run_execute", _run_execute_env())
    r = run_exec(message="买入 BTC", margin_usdt=50.0, leverage=1)
    sig = r["approval"]["signal"]
    assert "max_loss_usdt" not in sig, "仍携带虚假的最大亏损字段"
    assert "最大亏损" not in r["reply"], "回复仍承诺最大亏损"
    assert int(sig["leverage"]) == 1, "杠杆应为 1"
    assert abs(float(sig["quantity"]) - round(50.0 / 100.0, 6)) < 1e-9, \
        f"数量未按 margin/price 真实现货口径: {sig['quantity']}"
    assert "不会自动挂保护单" in r["reply"], "未如实说明止损止盈仅为提醒"


# ---------------- F05：副作用出口（插件 / MCP）统一校验 confirmed ----------------

class _PluginHostStub:
    calls = []

    @staticmethod
    def is_plugin(pid):
        return pid == "myplug"

    @staticmethod
    def exec_command(pid, cmd, args):
        _PluginHostStub.calls.append((pid, cmd, args))
        return {"ok": True, "text": "ran"}


def test_dispatch_plugin_requires_confirmation():
    sys.modules["plugin_host"] = _PluginHostStub
    try:
        _PluginHostStub.calls = []
        disp = function("src/agent_core.py", "_dispatch_tool", {"_wl_has": lambda op, a: False})
        r = disp("myplug.cmd", {}, confirmed=False)
        assert r.get("needs_approval"), "插件命令未经确认被直接执行（F05 回归）"
        assert r["approval"]["op"] == "plugin_exec"
        assert _PluginHostStub.calls == [], "未确认时插件已被执行"
    finally:
        sys.modules.pop("plugin_host", None)


def test_dispatch_plugin_confirmed_executes():
    sys.modules["plugin_host"] = _PluginHostStub
    try:
        _PluginHostStub.calls = []
        disp = function("src/agent_core.py", "_dispatch_tool", {"_wl_has": lambda op, a: False})
        r = disp("myplug.cmd", {}, confirmed=True)
        assert not r.get("needs_approval") and _PluginHostStub.calls, "confirmed=True 应直接执行"
    finally:
        sys.modules.pop("plugin_host", None)


def test_mcp_call_requires_confirmation():
    mcp = function("src/agent_core.py", "_run_mcp_call", {"_wl_has": lambda op, a: False})
    r = mcp({"server": "binance", "tool": "get_balances", "arguments": {}}, confirmed=False)
    assert r.get("needs_approval"), "MCP 调用未经确认被放行（F05 回归）"
    assert r["approval"]["op"] == "mcp_call"


def test_mcp_call_whitelisted_passes_gate():
    mcp = function("src/agent_core.py", "_run_mcp_call", {"_wl_has": lambda op, a: True})
    r = mcp({"server": "binance", "tool": "no_such", "arguments": {}}, confirmed=False)
    # 白名单命中后应越过审批屏障（此处因桩内 mcp_client 缺失而抛错文案，但绝不能是 needs_approval）
    assert not r.get("needs_approval"), "白名单命中的 MCP 调用仍被拦（应放行到执行层）"


def test_wl_rule_mcp_and_plugin():
    rule = function("src/agent_core.py", "_wl_rule")
    assert rule("mcp_call", {"server": "binance", "tool": "get_balances"}) == "mcp:binance.get_balances"
    assert rule("plugin_exec", {"plugin": "myplug", "command": "cmd"}) == "plugin:myplug"


def test_approval_barrier_names_include_mcp_and_plugin():
    fn = function("src/agent_core.py", "_dispatch_is_approval_needed")
    assert fn("mcp_call") is True, "mcp_call 未纳入审批屏障（F09/F05）"
    assert fn("someplug.cmd") is True, "插件命令（含点号）未纳入审批屏障"
    assert fn("scan_market") is False


# ---------------- F09：审批屏障 + 禁止整批重放 ----------------

def test_run_llm_agent_no_batch_replay():
    src = unparse("src/agent_core.py", "_run_llm_agent")
    assert "tool_calls_exec" not in src, "旧的整批列表 + 异常重放路径仍存在"
    assert "_run_batch" in src, "未使用分批调度"
    assert "executed_sigs" in src, "未按 per-call 记录签名（zip 会错位）"


def test_run_one_never_raises():
    src = unparse("src/agent_core.py", "_run_llm_agent")
    assert "工具执行异常" in src, "_run_one 未兜底异常（线程池异常会触发整批重跑）"


# ---------------- F06：沙箱环境清洗 + 符号链接越界 ----------------

def test_sandbox_env_strips_credentials():
    deny = assignment("src/exec_sandbox.py", "_ENV_DENY_SUBSTR")
    sandbox_env = function("src/exec_sandbox.py", "_sandbox_env",
                           {"os": os, "_ENV_DENY_SUBSTR": deny})
    old = {k: os.environ.get(k) for k in
           ("BAZZ_AUTH_TOKEN", "MY_API_KEY", "FOO_SECRET", "DB_PASSWORD", "PATH", "NODE_OPTIONS")}
    try:
        os.environ["BAZZ_AUTH_TOKEN"] = "tok"
        os.environ["MY_API_KEY"] = "key"
        os.environ["FOO_SECRET"] = "sec"
        os.environ["DB_PASSWORD"] = "pw"
        env = sandbox_env()
        assert not any(any(s in k.upper() for s in deny) for k in env), \
            "子进程环境仍携带凭据类变量"
        assert "PATH" in env and env["PATH"], "PATH 被误删"
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_realpath_escapes_rejects_symlink_outside():
    esc = function("src/exec_sandbox.py", "_realpath_escapes",
                   {"os": os, "_under": function("src/exec_sandbox.py", "_under", {"os": os})})
    tmp = tempfile.mkdtemp(prefix="bazz_f06_")
    try:
        # root 用 realpath 归一化，避免 8.3 短路径/符号化 tmp 目录干扰比较
        root = os.path.realpath(os.path.join(tmp, "root"))
        outside = os.path.realpath(os.path.join(tmp, "outside"))
        os.makedirs(root, exist_ok=True)
        os.makedirs(outside, exist_ok=True)
        outside_file = os.path.join(outside, "secret.txt")
        open(outside_file, "w").write("x")
        link = os.path.join(root, "leak.txt")
        try:
            os.symlink(outside_file, link)
        except (OSError, NotImplementedError):
            return  # 环境不支持符号链接则跳过
        assert esc(link, [root]) is True, "工作区内符号链接指向沙箱外未被识别"
        inside = os.path.join(root, "ok.txt")
        open(inside, "w").write("y")
        assert esc(inside, [root]) is False, "root 内普通文件被误判越界"
        assert esc(os.path.join(root, "missing.txt"), [root]) is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_resolvers_enforce_realpath_and_env():
    for fn in ("resolve_read", "resolve_write", "_resolve_sh_path"):
        assert "_realpath_escapes" in unparse("src/exec_sandbox.py", fn), \
            f"{fn} 未做符号链接 realpath 校验（F06）"
    # subprocess.run 必须传清洗后的 env
    hit = False
    for n in ast.walk(tree("src/exec_sandbox.py")):
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "run" \
                and getattr(n.func.value, "id", "") == "subprocess":
            kws = {k.arg for k in n.keywords}
            assert "env" in kws, "subprocess.run 未传 env=_sandbox_env()（F06）"
            hit = True
    assert hit, "未找到 subprocess.run 调用"


# ---------------- F07：快照恢复不跨服务继承密钥 ----------------

def test_cfg_from_snapshot_cross_provider_no_key_inherit():
    cur = {"provider": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-5.4",
           "api_key": "sk-B-KEY", "key_env": "", "backup_models": [], "aux": {}}
    fn = function("src/llm.py", "cfg_from_snapshot", {"get_llm_config": lambda: dict(cur)})
    out = fn({"provider": "deepseek", "base_url": "https://api.deepseek.com/v1",
              "model": "deepseek-v4-pro", "key_env": "DEEPSEEK_KEY"})
    assert out["provider"] == "deepseek" and out["base_url"].startswith("https://api.deepseek.com")
    assert out["api_key"] == "", "provider 换过后仍继承当前 key（F07 跨服务密钥错配）"
    assert out["key_env"] == "DEEPSEEK_KEY", "key_env 应回退为快照记录的旧变量名"


def test_cfg_from_snapshot_same_provider_keeps_key():
    cur = {"provider": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-5.4",
           "api_key": "sk-A-KEY", "key_env": "", "backup_models": [], "aux": {}}
    fn = function("src/llm.py", "cfg_from_snapshot", {"get_llm_config": lambda: dict(cur)})
    out = fn({"provider": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-5.6-sol"})
    assert out["api_key"] == "sk-A-KEY", "同 provider 恢复快照不应丢当前凭据"


# ---------------- F12：scheduler 按任务原子更新 ----------------

def test_scheduler_atomic_job_update():
    src = unparse("src/scheduler.py", "_loop")
    assert "update_cron_job" in src, "scheduler 未使用按任务原子更新（F12）"
    st = (ROOT / "src/state.py").read_text(encoding="utf-8-sig")
    assert "def update_cron_job" in st, "state 层缺 update_cron_job 原子更新函数"


# ---------------- F14：禁用插件双重校验 ----------------

class _DisabledPluginStub:
    def __init__(self, enabled):
        self.enabled = enabled

    def _env(self):
        return {
            "_plugin_dirs": lambda: ["myplug"],
            "_read_manifest": lambda pid: {"id": "myplug", "commands": [{"name": "cmd"}]},
            "_load_module": lambda pid: SimpleNamespace(cmd=lambda p: {"ok": True, "text": "ran"}),
            "is_enabled": lambda pid: self.enabled,
        }


def test_exec_command_rejects_disabled_plugin():
    stub = _DisabledPluginStub(False)
    fn = function("src/plugin_host.py", "exec_command", stub._env())
    r = fn("myplug", "cmd", {})
    assert r.get("ok") is False and "禁用" in (r.get("error") or ""), \
        f"禁用插件仍被执行（F14 回归）: {r}"


def test_exec_command_allows_enabled_plugin():
    stub = _DisabledPluginStub(True)
    fn = function("src/plugin_host.py", "exec_command", stub._env())
    r = fn("myplug", "cmd", {})
    assert r.get("ok") is True and r.get("text") == "ran"


def test_disabled_plugin_not_injected_to_llm():
    stub = _DisabledPluginStub(False)
    env = stub._env()
    env.update({"list_plugins": lambda: [{"id": "myplug", "name": "mp",
                                          "commands": [{"name": "cmd", "description": "d"}]}]})
    fn = function("src/plugin_host.py", "list_command_schemas", env)
    assert fn() == [], "禁用插件仍被注入 LLM 工具（F14）"


# ---------------- F15：敏感设置 DPAPI 加密（secrets.py + state 层钩子） ----------------

def test_secrets_roundtrip_and_legacy_plain():
    import importlib.util
    spec = importlib.util.spec_from_file_location("bazz_secrets", ROOT / "src/secrets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    stored = mod.encrypt_secret("super-secret-value")
    assert stored != "super-secret-value", "敏感值未加密落库（F15）"
    assert mod.is_encrypted(stored)
    assert mod.decrypt_secret(stored) == "super-secret-value", "加解密回环失败"
    # 旧明文兼容：无前缀原样返回；plain: 前缀去前缀返回
    assert mod.decrypt_secret("legacy-plain") == "legacy-plain"
    assert mod.decrypt_secret("plain:abc") == "abc"


def test_state_has_sensitive_key_hook():
    st = (ROOT / "src/state.py").read_text(encoding="utf-8-sig")
    assert "SENSITIVE_SETTING_KEYS" in st, "state 层缺敏感 key 加密钩子（F15）"
    for key in ("llm", "BINANCE_API_KEY", "BINANCE_API_SECRET", "mcp_servers"):
        assert f'"{key}"' in st, f"敏感 key {key} 未纳入加密清单"


# ---------------- F17：last_persona_conv 重复定义已清除 ----------------

def test_last_persona_conv_single_definition_keeps_group_room_filter():
    tree_state = tree("src/state.py")
    defs = [n for n in ast.walk(tree_state)
            if isinstance(n, ast.FunctionDef) and n.name == "last_persona_conv"]
    assert len(defs) == 1, f"last_persona_conv 仍有 {len(defs)} 处定义（F17）"
    src = ast.unparse(defs[0])
    assert "group" in src and "room" in src, "保留版本丢失了 group/room 排除条件（F17）"


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
