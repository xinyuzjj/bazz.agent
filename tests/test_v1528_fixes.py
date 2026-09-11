"""v1.5.28 回归测试：审查报告 F01/F02/F04/F08/F10/F13 修复验证。

离线、隔离：AST/函数提取 + 桩替换，不导入应用、不联网、不下单、不写 state.db。
每个用例断言「修复后的正确行为」——对旧代码（944ca16）这些用例必然失败。
运行：python tests/test_v1528_fixes.py  或  pytest tests/test_v1528_fixes.py
"""
import ast
import json
import os
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def tree(file: str):
    return ast.parse((ROOT / file).read_text(encoding="utf-8-sig"), filename=file)


def function(file: str, name: str, env: dict | None = None):
    """从模块中抽取单个函数定义，在给定桩环境中 exec（不触发模块级副作用）。"""
    node = next(n for n in tree(file).body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    ns = {} if env is None else dict(env)
    exec(compile(ast.Module(body=[node], type_ignores=[]), file, "exec"), ns)
    return ns[name]


def assignment(file: str, name: str):
    for n in tree(file).body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name
                                             for t in n.targets):
            return ast.literal_eval(n.value)
    raise KeyError(name)


# ---------------- F02：钱包 TRADE_FINISHED / TRADE_PENDING 不再误报失败 ----------------

def test_wallet_finished_not_error():
    place_report = function("src/executor.py", "place_report")
    r = place_report({"orderId": "oid-1", "symbol": "BNBUSDT",
                      "status": "TRADE_FINISHED", "output": "done", "command": "baw ..."})
    assert "error" not in r, f"已成交仍被报错: {r}"
    assert r["status"] == "FILLED" and r["orderId"] == "oid-1"
    assert r["symbol"] == "BNBUSDT"


def test_wallet_pending_pending_not_error():
    place_report = function("src/executor.py", "place_report")
    r = place_report({"orderId": "oid-2", "symbol": "BNBUSDT",
                      "status": "TRADE_PENDING", "output": "submitted"})
    assert "error" not in r, f"待确认被报错: {r}"
    assert r["status"] == "PENDING"        # pending → 待查证，不自动重试


def test_cex_ok_unchanged():
    place_report = function("src/executor.py", "place_report")
    r = place_report({"status": "ok", "order": {"orderId": "cex-1", "symbol": "BTCUSDT",
                                                "status": "NEW", "executedQty": "0",
                                                "fills": []}})
    assert r["orderId"] == "cex-1" and r["status"] == "NEW"


def test_cex_error_still_error():
    place_report = function("src/executor.py", "place_report")
    r = place_report({"status": "error", "code": "lot_size", "message": "数量不足"})
    assert "error" in r and "数量不足" in r["error"]


# ---------------- F04：FILLED 成交后止损止盈提醒继续 ----------------

def _check_sl_tp_with(status: str):
    events = []
    ws = SimpleNamespace(price=lambda *a: 90.0,
                         publish_event=lambda *a, **k: events.append(k))
    check = function("src/order_tracker.py", "_check_sl_tp", {
        "market_ws": ws,
        "_CLOSED_FOR_ALERTS": assignment("src/order_tracker.py", "_CLOSED_FOR_ALERTS"),
        "_warn_price_scope": lambda s: "spot",
        "_cool": lambda *a: False,
        "NEAR_RATIO": 0.005,
    })
    check({"id": "t1", "symbol": "TESTUSDT", "active": True, "status": status,
           "direction": "BULLISH", "entry": 100, "stop_loss": 97, "take_profit": 108})
    return events


def test_filled_still_alerts_sl_hit():
    events = _check_sl_tp_with("FILLED")
    assert any(e.get("kind") == "sl_hit" for e in events), \
        f"FILLED 成交后止损触发无提醒（F04 回归）: {events}"


def test_canceled_no_alerts():
    events = _check_sl_tp_with("CANCELED")
    assert events == [], "已撤单订单不应再提醒"


def test_alert_closed_set_excludes_filled():
    closed_alerts = assignment("src/order_tracker.py", "_CLOSED_FOR_ALERTS")
    assert "FILLED" not in closed_alerts
    assert {"CANCELED", "REJECTED", "EXPIRED"} <= closed_alerts
    # 轮询终态仍包含 FILLED（成交后不再查单）
    assert "FILLED" in assignment("src/order_tracker.py", "_CLOSED")


# ---------------- F01：更新校验资产名大小写 + 校验不可缺失 ----------------

class _FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        return SimpleNamespace(status_code=200, text="abc  sample-setup.exe",
                               json=lambda: {"files": {"sample": {"h": "abc"}}})


def _fetch_env():
    s = _FakeSession()
    env = {"_session": lambda: s,
           "CHECKSUM_ASSET": assignment("src/updater.py", "CHECKSUM_ASSET"),
           "MANIFEST_ASSET": assignment("src/updater.py", "MANIFEST_ASSET")}
    return s, env


def test_checksums_asset_case_insensitive():
    s, env = _fetch_env()
    fetch = function("src/updater.py", "_fetch_checksums", env)
    # 故意用小写资产名（GitHub release 资产名大小写曾被错误假设）
    rel = {"assets": [{"name": "sha256sums", "browser_download_url": "offline://sums"}]}
    out = fetch(rel)
    assert out, f"SHA256SUMS（小写资产名）仍匹配失败（F01 回归）: {out}"
    assert out.get("sample-setup.exe") == "abc"
    assert len(s.calls) == 1, "匹配到资产却未发起下载"


def test_manifest_asset_case_insensitive():
    s, env = _fetch_env()
    fetch = function("src/updater.py", "_fetch_manifest", env)
    rel = {"assets": [{"name": "MANIFEST.json", "browser_download_url": "offline://man"}]}
    out = fetch(rel)
    assert out.get("files", {}).get("sample", {}).get("h") == "abc"


def test_apply_fail_closed_on_missing_checksums():
    """apply() 官方包路径：校验和缺失/获取失败必须拒绝安装（fail-closed）。"""
    src = (ROOT / "src" / "updater.py").read_text(encoding="utf-8-sig")
    apply_node = next(n for n in tree("src/updater.py").body
                      if isinstance(n, ast.FunctionDef) and n.name == "apply")
    body_src = ast.get_source_segment(src, apply_node) or ""
    assert 'return {"ok": False' in body_src
    assert "无法获取官方 SHA256 校验和" in body_src, "获取校验和异常时应拒绝安装而非跳过"
    assert "未提供本更新包的 SHA256 校验值" in body_src, "校验和缺失时应拒绝安装而非跳过"


# ---------------- F08：强制兜底工具名不得布尔化 ----------------

def test_forced_tool_name_stays_string():
    ag = tree("src/agent_core.py")
    node = next(n for n in ast.walk(ag)
                if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "forced" for t in n.targets)
                and isinstance(n.value, (ast.BoolOp, ast.IfExp)))
    val = eval(compile(ast.Expression(node.value), "forced", "eval"),
               {"forced_cand": "scan_market", "allowed": None})
    assert not isinstance(val, bool), f"forced 仍是布尔（F08 回归）: {val!r}"
    assert val == "scan_market"
    # 不在允许列表 → 置空（falsy），不得抛错
    val2 = eval(compile(ast.Expression(node.value), "forced", "eval"),
                {"forced_cand": "run_skill", "allowed": {"scan_market"}})
    assert isinstance(val2, str) and not val2


# ---------------- F10：MCP 成功结果不再判为失败 ----------------

def _mcp_ok_expr():
    fn = next(n for n in tree("src/agent_core.py").body
              if isinstance(n, ast.FunctionDef) and n.name == "_run_mcp_call")
    return next(n.value for n in ast.walk(fn)
                if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "ok" for t in n.targets))


def test_mcp_status_ok_counts_as_success():
    ok = eval(compile(ast.Expression(_mcp_ok_expr()), "ok", "eval"),
              {"res": {"status": "ok", "data": {"result": {"x": 1}}}})
    assert ok is True, "status==ok 的 MCP 成功结果被判失败（F10 回归）"


def test_mcp_error_still_failure():
    ok = eval(compile(ast.Expression(_mcp_ok_expr()), "ok", "eval"),
              {"res": {"status": "error", "detail": "boom"}})
    assert ok is False


# ---------------- F13：日报调用签名与返回值契约 ----------------

def test_scheduler_ignition_call_signature():
    params = [a.arg for a in next(n for n in tree("src/scanner.py").body
                                  if isinstance(n, ast.FunctionDef)
                                  and n.name == "get_ignition_coins").args.args]
    calls = [n for n in ast.walk(tree("src/scheduler.py"))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id in ("get_ignition_coins", "get_monster_coins")]
    assert calls, "scheduler 未调用雷达函数"
    for c in calls:
        for kw in c.keywords:
            assert kw.arg in ("force", "top_n", "min_qv"), \
                f"非法实参 {kw.arg}=（F13 回归，实参集为 force/top_n/min_qv）"


def test_scheduler_reads_coins_from_dict():
    src = (ROOT / "src" / "scheduler.py").read_text(encoding="utf-8-sig")
    seg_start = src.find("def _run_meme_scan")
    seg = src[seg_start:src.find("\ndef ", seg_start + 1)]
    assert '.get("coins")' in seg, "_run_meme_scan 未显式读取 dict 的 coins 字段（F13 回归）"


if __name__ == "__main__":
    fails = []
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            fails.append(name)
            print(f"FAIL  {name}: {e}")
        except Exception as e:
            fails.append(name)
            print(f"ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - len(fails)}/{len(tests)} passed")
    raise SystemExit(1 if fails else 0)
