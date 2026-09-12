"""v1.5.31 回归测试：两个「提示词侧」缺陷。

缺陷一 —— market-data 宣传了不存在的子命令
  SKILL.md 的 description 把「资金费率 / funding」列为触发词，但 CLI 的 CMDS 里
  只有 klines/fng/oi/longshort/liq/overview/bundle。模型据此调用
  `run_skill market-data "funding BTCUSDT"` 只会得到 {"error":"未知命令 \"funding\""}。
  资金费率当时仅作为 bundle 的一个字段存在 —— 为拿单币费率要跑完整分析包，代价过高。
  → 新增真正的 `funding [SYM[,SYM...]]` 子命令，并让未知命令回一句 hint。

缺陷二 —— 模型把 HTTP 状态码当文件路径传进 run_command
  技能报错文本里含 `:8080 HTTP 401: {"error":"unauthorized"}`，模型随后执行
  `grep <pattern> 401`，把状态码 401 当成路径 → 只得到干巴巴的「路径不存在: 401」，
  无从纠正，容易反复重试同一个错。
  → 新增 _not_a_path_hint()，在 4 个路径报错点对「明显不是路径」的实参补提示；
    并在 run_command 的工具描述里写明该约束。

本文件离线运行：AST 抽函数 + 源码文本断言；端到端那一条只起本机 stub 后端
（127.0.0.1 随机端口），不访问外网、不写 state.db、不碰用户数据。
运行：python tests/test_v1531_prompt_fixes.py
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]


class Skip(Exception):
    """环境不满足（如缺 node）——记为跳过，不计失败。"""


# ---------------- AST / 文本助手 ----------------

def tree(file: str):
    return ast.parse((ROOT / file).read_text(encoding="utf-8-sig"), filename=file)


def function(file: str, name: str, env: dict | None = None):
    node = next(n for n in tree(file).body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    ns = {"Dict": Dict, "Any": Any}
    if env:
        ns.update(env)
    exec(compile(ast.Module(body=[node], type_ignores=[]), file, "exec"), ns)
    return ns[name]


def src(file: str) -> str:
    return (ROOT / file).read_text(encoding="utf-8-sig")


def hint_fn():
    return function("src/exec_sandbox.py", "_not_a_path_hint", {"re": re})


# ---------------- 1. _not_a_path_hint 行为 ----------------

def test_hint_flags_http_status():
    """真实案例：401 被当成路径。"""
    h = hint_fn()("401")
    assert h, "401 未被识别为「不是路径」"
    assert "状态码" in h, h
    assert "401" in h, h


def test_hint_flags_url():
    assert "URL" in hint_fn()("https://example.com/data.json")
    assert "URL" in hint_fn()("http://127.0.0.1:8080/api/market/futures")


def test_hint_flags_leading_dash():
    assert "选项" in hint_fn()("-l")


def test_hint_silent_on_normal_paths():
    """正常路径不得被加噪音 —— 否则每次报错都多一坨无关文字。"""
    for p in ("workspace/report.md", "src", ".agents/skills/market-data/SKILL.md",
              "workspace/a/b/c.txt", ""):
        assert hint_fn()(p) == "", f"{p!r} 被误判为非路径"


def test_hint_tolerates_quotes_and_spaces():
    """shell 里常带引号/空白，判断前必须剥掉。"""
    assert "状态码" in hint_fn()(' "401" ')
    assert "状态码" in hint_fn()("'404'")


# ---------------- 2. 提示已接到全部路径报错点 ----------------

def test_hint_wired_into_all_path_error_sites():
    s = src("src/exec_sandbox.py")
    frags = [
        '文件不存在: {path}{_not_a_path_hint(path)}',
        '目录不存在: {p}{_not_a_path_hint(p)}',
        '路径不存在: {toks[2]}{_not_a_path_hint(toks[2])}',
        '目录不存在: {target}{_not_a_path_hint(target)}',
    ]
    for f in frags:
        assert f in s, f"报错点未接入提示: {f}"


def test_run_command_description_warns_about_path_args():
    """工具描述要正面写清：不要把状态码/URL 当路径传。"""
    s = src("src/llm.py")
    assert "不要把数字、HTTP 状态码" in s, "run_command 描述缺少路径实参约束"


# ---------------- 3. market-data 新增 funding 子命令 ----------------

CLI = ".agents/skills/market-data/scripts/cli.mjs"


def test_funding_registered_in_cmds():
    assert re.search(r"^\s{2}async funding\(rest\)", src(CLI), re.M), "CMDS 里没有 funding"


def test_funding_hits_futures_endpoint():
    """funding 必须走 /market/futures（该端点已附资金费率），不要另起炉灶。"""
    m = re.search(r"async funding\(rest\) \{(.*?)\n  \},", src(CLI), re.S)
    assert m, "找不到 funding 函数体"
    body = m.group(1)
    assert "/market/futures" in body, "funding 未调用 /market/futures"
    assert "funding_pct_8h" in body and "annualized_pct" in body, "缺少费率换算字段"


def test_unknown_command_replies_with_hint():
    s = src(CLI)
    assert "hint:" in s, "未知命令未回 hint，模型无从纠正"
    assert "未知命令" in s, "未知命令文案被破坏"


def test_skill_md_documents_funding():
    md = src(".agents/skills/market-data/SKILL.md")
    assert "`funding" in md, "SKILL.md 命令表未登记 funding"
    assert "funding WLDUSDT" in md, "SKILL.md 缺少 funding 用法示例"


def test_skill_md_commands_all_dispatchable():
    """防止再出现「文档宣传了 CLI 没有的命令」—— 本次缺陷的通用护栏。"""
    for name in ("market-data", "news-sentiment", "portfolio-review"):
        cli = src(f".agents/skills/{name}/scripts/cli.mjs")
        m = re.search(r"const CMDS = \{(.*?)\n\};", cli, re.S)
        assert m, f"{name}: 找不到 CMDS"
        keys = set(re.findall(r"^\s{2}async (\w+)\(", m.group(1), re.M))
        doc = set(re.findall(r"^\|\s*`([a-z][\w-]*)", src(f".agents/skills/{name}/SKILL.md"), re.M))
        missing = doc - keys
        assert not missing, f"{name}: SKILL.md 宣传了 CLI 不存在的命令 {sorted(missing)}"


def test_single_command_skills_declare_accurate_available():
    """coin-report / risk-guard / track-monitor 用 available 数组自述，须与文档一致。"""
    expect = {"coin-report": ["report"], "risk-guard": ["plan", "check"], "track-monitor": ["check"]}
    for name, cmds in expect.items():
        s = src(f".agents/skills/{name}/scripts/cli.mjs")
        assert "available" in s, f"{name}: 未知命令未回 available"
        for c in cmds:
            assert f'"{c}"' in s, f"{name}: CLI 里找不到命令 {c}"
        md = src(f".agents/skills/{name}/SKILL.md")
        for c in cmds:
            assert re.search(rf"^\|\s*`{re.escape(c)}\b", md, re.M), f"{name}: SKILL.md 未登记 {c}"


# ---------------- 4. 端到端：真跑 funding ----------------

FAKE_FUTURES = {
    "futures": [
        {"symbol": "WLDUSDT", "price": 2.345, "change_pct": -4.5,
         "quote_volume": 8.8e8, "funding_rate": 0.00085},
        {"symbol": "RAYUSDT", "price": 4.567, "change_pct": 6.7,
         "quote_volume": 1.1e8, "funding_rate": -0.00092},
    ]
}


class _StubHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/market/futures"):
            body = json.dumps(FAKE_FUTURES).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, *a):  # 静音
        pass


def _run_cli(args, port):
    node = shutil.which("node")
    if not node:
        raise Skip("未找到 node 可执行文件")
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")}
    env["BAZZ_PORT"] = str(port)
    env["BAZZ_AUTH_TOKEN"] = "tok-test"
    r = subprocess.run([node, str(ROOT / CLI), *args],
                       capture_output=True, text=True, timeout=60, env=env)
    return r


def test_funding_end_to_end():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = _run_cli(["funding", "WLDUSDT"], port)
        assert r.returncode == 0, f"exit={r.returncode} out={r.stdout} err={r.stderr}"
        it = json.loads(r.stdout.strip())["items"][0]
        assert it["symbol"] == "WLDUSDT", it
        assert abs(it["funding_pct_8h"] - 0.085) < 1e-9, it
        assert abs(it["annualized_pct"] - 93.07) < 0.02, it

        # 多标的 + 缺号提示
        r2 = _run_cli(["funding", "WLDUSDT,RAYUSDT,NOPEUSDT"], port)
        o2 = json.loads(r2.stdout.strip())
        assert [x["symbol"] for x in o2["items"]] == ["WLDUSDT", "RAYUSDT"], o2
        assert o2.get("missing") == ["NOPEUSDT"], o2

        # 无参数 → 两端极值
        r3 = _run_cli(["funding"], port)
        o3 = json.loads(r3.stdout.strip())
        assert o3["long_crowded"][0]["symbol"] == "WLDUSDT", o3
        assert o3["short_crowded"][0]["symbol"] == "RAYUSDT", o3

        # 拼错 → 未知命令 + hint
        r4 = _run_cli(["fundng", "WLDUSDT"], port)
        assert r4.returncode == 1, f"未知命令应非零退出，实际 {r4.returncode}"
        o4 = json.loads(r4.stdout.strip())
        assert "funding" in o4.get("hint", ""), o4
    finally:
        srv.shutdown()
        srv.server_close()


# ---------------- 主入口 ----------------

def main():
    fails, skips = [], []
    cases = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for name, fn in cases:
        try:
            fn()
            print(f"  PASS {name}")
        except Skip as e:
            skips.append(name)
            print(f"  SKIP {name}: {e}")
        except Exception as e:
            fails.append((name, e))
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
    ran = len(cases) - len(skips)
    tail = f"（跳过 {len(skips)}）" if skips else ""
    print(f"\n{ran - len(fails)}/{ran} passed{tail}")
    if fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
