"""v1.5.32 回归测试：内核型代理「静默失效」+ 子进程 GBK 解码崩溃。

缺陷一 —— 内核型代理的跨进程状态丢失（广场发文 UND_ERR_CONNECT_TIMEOUT 的真根因）
  真实事故：代理池里启用的是 vless（内核型）节点，mihomo 的混合端口由 _write_config()
  动态分配，且只存在**进程内存**里（_mixed_port / _ctrl_port / _proc 三个模块级变量）。
  后端一旦重启（或同机有第二个实例、或上一实例把 mihomo 留成了孤儿进程），新进程里
  _proc 恒为 None → is_running() 直接 return False → mixed_port() 返回 0 →
  proxy_pool.proxy_url() 返回 None → _apply_env() 走 else 分支把 HTTP(S)_PROXY
  **清空** → 所有技能子进程直连外网 → Node/undici 报 UND_ERR_CONNECT_TIMEOUT。
  用户侧表现：「代理池里节点明明是启用的、延迟也测得出，但广场发文/取数一律超时」。

  → proxy_kernel 新增 state.json 落盘 + config.yaml 兜底 + 控制面探活恢复端口（_recover）；
    /api/proxies/kernel/start|stop 补调 apply_env()；
    ensure_working_proxy() 先尝试救活「用户选中的那个内核节点」再去找别的候选。

缺陷二 —— subprocess 文本模式未指定编码，GBK 严格解码崩掉读取线程
  zh-CN Windows 上 subprocess.run(text=True) 默认按 locale(GBK) 严格解码；baw/npx 输出
  含非 ASCII 时 _readerthread 抛 UnicodeDecodeError 直接死掉，proc.stdout 变空 ——
  技能「明明跑了但没有任何输出」，排障时极具误导性。

缺陷三（v1.5.33，修缺陷一时引入的竞态）—— _recover() 会清掉 start() 刚写好的端口
  start() 里 _write_config() 已写好端口、Popen() 还没返回时，若前端轮询 /api/proxies
  触发 status() → is_running() → _recover()，探活必然失败（内核还没起），
  于是把刚写好的端口清零 → 等待循环一直打 http://127.0.0.1:0/version → 误报
  「内核启动超时」，并把 mixed_port:0 写进 state.json。表现与「代理没启用」一模一样。
  → 引入 _recovered 标记，_recover() 只清「落盘恢复来的」端口；stop() 同时归零端口。

本文件离线运行：只做模块级行为测试 + 源码 AST 断言，不访问外网、不动用户数据
（所有落盘路径都重定向到临时目录）。运行：python tests/test_v1532_proxy_kernel_state.py
"""
import ast
import json
import os
import re
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class Skip(Exception):
    """环境不满足 —— 记为跳过，不计失败。"""


# ---------------- 环境隔离 ----------------

def _mods(tmp: Path):
    """导入被测模块，并把所有落盘路径重定向到临时目录（绝不碰用户真实数据）。"""
    import proxy_kernel
    import proxy_pool
    kdir = tmp / "kernel"
    kdir.mkdir(parents=True, exist_ok=True)
    proxy_kernel.KERNEL_DIR = str(kdir)
    proxy_kernel.PROVIDERS_DIR = str(kdir / "providers")
    proxy_kernel.STATE_PATH = str(kdir / "state.json")
    proxy_kernel.CONFIG_PATH = str(kdir / "config.yaml")
    proxy_pool.POOL_PATH = str(tmp / "proxies.json")
    return proxy_kernel, proxy_pool


def _reset_kernel_state(pk, tmp: Path, *, state=None, config=None):
    """把内核模块恢复成「刚启动的全新进程」：没有任何进程内记忆。"""
    pk._proc = None
    pk._mixed_port = 0
    pk._ctrl_port = 0
    pk._recovered = False
    sp = Path(pk.STATE_PATH)
    cp = Path(pk.CONFIG_PATH)
    sp.unlink(missing_ok=True)
    cp.unlink(missing_ok=True)
    if state is not None:
        sp.write_text(json.dumps(state), encoding="utf-8")
    if config is not None:
        cp.write_text(config, encoding="utf-8")


CONFIG_YAML = "mixed-port: 7899\nallow-lan: false\nexternal-controller: 127.0.0.1:9099\n"

_KERNEL_ENTRY = {
    "id": "k1", "name": "🇸🇬新加坡", "proto": "vless", "server": "1.2.3.4", "port": 443,
    "direct": False, "kernel_name": "🇸🇬新加坡", "status": "ok", "latency_ms": 407,
}


def _set_active(pp, entry):
    pp._state["active_id"] = entry["id"]
    pp._state["entries"] = [dict(entry)]


# ---------------- 1. 跨进程恢复 ----------------

def test_recover_from_state_file():
    """核心回归：state.json 在、控制面活着 → 新进程必须认出内核在跑且端口正确。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pk, _ = _mods(tmp)
        _reset_kernel_state(pk, tmp,
                            state={"pid": 4321, "mixed_port": 7899, "ctrl_port": 9099})
        pk._probe_ctrl = lambda p: p == 9099          # 只认控制面端口

        assert pk.is_running() is True, "新进程未认出「内核客观在跑」（这正是事故根因）"
        assert pk.mixed_port() == 7899, f"混合端口未恢复：{pk.mixed_port()}"


def test_recover_falls_back_to_config_yaml():
    """state.json 丢失（旧版本升级上来 / 被清理）时，从 config.yaml 兜底解析端口。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pk, _ = _mods(tmp)
        _reset_kernel_state(pk, tmp, config=CONFIG_YAML)
        pk._probe_ctrl = lambda p: p == 9099

        assert pk.is_running() is True
        assert pk.mixed_port() == 7899


def test_recover_reports_dead_when_probe_fails():
    """端口在但控制面不通 → 必须如实报「没在跑」，且不残留假端口。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pk, _ = _mods(tmp)
        _reset_kernel_state(pk, tmp,
                            state={"pid": 0, "mixed_port": 7899, "ctrl_port": 9099},
                            config=CONFIG_YAML)
        pk._probe_ctrl = lambda p: False

        assert pk.is_running() is False
        assert pk.mixed_port() == 0, "探活失败后仍返回端口 → 会写出指向空端口的代理 URL"


def test_recover_does_not_clobber_inprocess_ports():
    """v1.5.33 竞态回归：并发 is_running() 不得清掉 start() 刚写好的端口。

    触发路径：start() 内 _write_config() 已写好端口、Popen() 还没返回时，前端轮询
    /api/proxies → status() → is_running() → _recover()。若此时探活失败就无条件清零，
    等待循环会一直打 http://127.0.0.1:0/version，最终误报「内核启动超时」，
    并把 mixed_port:0 写进 state.json —— 表现与「代理没启用」完全一样。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pk, _ = _mods(tmp)
        _reset_kernel_state(pk, tmp, config=CONFIG_YAML)
        # 模拟 start() 已写入端口（_recovered=False），内核尚未就绪
        pk._mixed_port = 7899
        pk._ctrl_port = 9099
        pk._probe_ctrl = lambda p: False

        assert pk.is_running() is False
        assert pk._ctrl_port == 9099, "并发 is_running() 清掉了 start() 刚写好的控制端口 → 启动必然超时"
        assert pk._mixed_port == 7899, "并发 is_running() 清掉了 start() 刚写好的混合端口"


def test_stop_resets_ports():
    """停内核后端口必须归零，否则 mixed_port() 会返回过期端口。

    断言看的是模块内端口缓存（不是 mixed_port()）—— 后者在 stop 后会走 _recover()
    重新解析 config.yaml，那是正确行为，不代表残留。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pk, _ = _mods(tmp)
        _reset_kernel_state(pk, tmp, config=CONFIG_YAML)
        pk._pid_image = lambda pid: ""      # 不触发 taskkill
        pk._mixed_port = 7899
        pk._ctrl_port = 9099
        pk.stop()
        assert pk._mixed_port == 0, f"stop() 后 _mixed_port 仍为 {pk._mixed_port}"
        assert pk._ctrl_port == 0, f"stop() 后 _ctrl_port 仍为 {pk._ctrl_port}"
        assert pk._recovered is False


def test_proxy_url_uses_recovered_port():
    """恢复出来的端口要能直接变成可用的代理 URL。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pk, pp = _mods(tmp)
        _reset_kernel_state(pk, tmp,
                            state={"pid": 4321, "mixed_port": 7899, "ctrl_port": 9099})
        pk._probe_ctrl = lambda p: p == 9099

        url = pp.proxy_url(dict(_KERNEL_ENTRY))
        assert url == "http://127.0.0.1:7899", f"内核型节点未解析出代理 URL：{url!r}"


def test_apply_env_injects_recovered_proxy():
    """端到端回归：新进程 + 内核客观在跑 → _apply_env() 必须**注入**而不是清空 env。

    修复前：is_running()=False → proxy_url()=None → else 分支把 HTTP(S)_PROXY 全删掉，
    技能子进程直连超时。这一条断言的就是那个 else 分支不再被误触发。"""
    keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
            "http_proxy", "https_proxy", "all_proxy", "NO_PROXY", "no_proxy", "NODE_OPTIONS")
    saved = {k: os.environ.get(k) for k in keys}
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pk, pp = _mods(tmp)
        _reset_kernel_state(pk, tmp,
                            state={"pid": 4321, "mixed_port": 7899, "ctrl_port": 9099})
        pk._probe_ctrl = lambda p: p == 9099
        _set_active(pp, _KERNEL_ENTRY)
        try:
            for k in keys:
                os.environ.pop(k, None)
            pp._apply_env()
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
                assert os.environ.get(k) == "http://127.0.0.1:7899", \
                    f"{k}={os.environ.get(k)!r} —— 代理 env 没注入（技能会直连超时）"
            np = os.environ.get("NO_PROXY", "")
            assert "127.0.0.1" in np, f"NO_PROXY 未含本机地址：{np!r}"
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def test_apply_env_public_entry_exists():
    """desktop_app 要用的公开入口。"""
    with tempfile.TemporaryDirectory() as td:
        _, pp = _mods(Path(td))
        assert callable(getattr(pp, "apply_env", None)), "proxy_pool.apply_env 未提供"
        snap = pp.env_snapshot()
        assert set(snap) >= {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"}, snap


# ---------------- 2. 救活用户选中的内核节点 ----------------

class _FakeKernel:
    def __init__(self):
        self.running = False
        self.selected = None

    def is_installed(self):
        return True

    def is_running(self):
        return self.running

    def mixed_port(self):
        return 7899

    def ensure_running(self):
        self.running = True

    def select(self, name):
        self.selected = name
        return True


def test_ensure_working_proxy_revives_active_kernel_node():
    """active 是内核型节点时，兜底逻辑要先把它救活，而不是绕过去找别的候选。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pk, pp = _mods(tmp)
        _reset_kernel_state(pk, tmp)
        fake = _FakeKernel()
        pp._kernel = lambda: fake
        pp._url_alive = lambda url, timeout=None: bool(url)
        _set_active(pp, _KERNEL_ENTRY)

        got = pp.ensure_working_proxy()
        assert fake.running, "没有拉起内核 —— 用户选中的内核型节点永远救不活"
        assert fake.selected == "🇸🇬新加坡", f"没切到用户节点：{fake.selected!r}"
        assert got == "http://127.0.0.1:7899", f"兜底未返回可用代理 URL：{got!r}"


# ---------------- 3. 内核启停端点必须同步 env ----------------

def _handler_block(src: str, path: str) -> str:
    """截取某个 @app.post 处理函数体（到下一个装饰器为止）。"""
    i = src.index(f'@app.post("{path}")')
    j = src.find("\n@app.", i + 10)
    return src[i:j if j > 0 else len(src)]


def test_kernel_endpoints_apply_env():
    src = (ROOT / "desktop_app.py").read_text(encoding="utf-8-sig")
    st = _handler_block(src, "/api/proxies/kernel/start")
    assert "apply_env()" in st, "内核启动后没有注入代理 env —— 点「启动内核」等于没启用代理"
    sp = _handler_block(src, "/api/proxies/kernel/stop")
    assert "apply_env()" in sp, "内核停止后没有同步 env —— 会留下指向死端口的代理"


def test_bootstrap_revives_kernel():
    src = (ROOT / "src/proxy_pool.py").read_text(encoding="utf-8-sig")
    body = src[src.index("def bootstrap():"):src.index("def _revive_kernel_async():")]
    assert "_revive_kernel_async()" in body, "bootstrap 未尝试恢复内核 —— 重启 APP 后代理静默失效"


def test_stop_clears_state_file():
    """停内核必须清掉 state.json，否则下次启动会拿着过期端口去探活。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pk, _ = _mods(tmp)
        _reset_kernel_state(pk, tmp,
                            state={"pid": 4321, "mixed_port": 7899, "ctrl_port": 9099})
        pk._pid_image = lambda pid: ""      # 不匹配 mihomo.exe → 不触发 taskkill
        assert Path(pk.STATE_PATH).exists()
        pk.stop()
        assert not Path(pk.STATE_PATH).exists(), "stop() 后 state.json 仍在"


# ---------------- 4. GBK 解码护栏 ----------------

_OWN_FILES = [ROOT / "desktop_app.py", ROOT / "launcher.py"] + \
             sorted((ROOT / "src").glob("*.py"))

_TEXT_KW = {"text", "universal_newlines"}
_SUBPROC = {"run", "Popen", "call", "check_call", "check_output"}


def test_no_text_mode_subprocess_without_encoding():
    """所有 text=True 的子进程调用都必须显式指定 encoding ——
    否则 zh-CN Windows 上按 GBK 严格解码，非 ASCII 输出会崩掉 _readerthread 并静默丢输出。"""
    bad = []
    for f in _OWN_FILES:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8-sig"), filename=str(f))
        except SyntaxError as e:
            bad.append(f"{f.name}: 语法错误 {e}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr in _SUBPROC
                    and isinstance(fn.value, ast.Name) and fn.value.id == "subprocess"):
                continue
            kws = {k.arg for k in node.keywords}
            if not (_TEXT_KW & kws):
                continue
            if "encoding" not in kws:
                bad.append(f"{f.relative_to(ROOT)}:{node.lineno} "
                           f"subprocess.{fn.attr}(text=...) 缺少 encoding=")
    assert not bad, "以下调用在中文 Windows 上会 GBK 解码崩溃：\n  " + "\n  ".join(bad)


def test_exec_sandbox_decode_falls_back_to_gbk():
    """沙箱那侧本来就是 utf-8→gbk→latin-1 兜底，确认没被改坏（排除它是本次崩溃的来源）。"""
    src = (ROOT / "src/exec_sandbox.py").read_text(encoding="utf-8-sig")
    assert 'for enc in ("utf-8", "gbk")' in src, "_decode_text 的多编码兜底被改动了"


# ---------------- 主入口 ----------------

def main():
    fails, skips = [], []
    cases = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
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
