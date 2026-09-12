"""v1.5.38 回归测试：代理池「取消连接彻底拆栈」这一条修复不许被删。

背景（用户现场 `F:\\1\\BAZZ.AGENT`，v1.5.36 → 装了 v1.5.37）
--------------------------------------------------------------
用户先报：「一进去是默认使用连接代理池的这不对；当我取消连接，整个应用都没有网了；
并且显示需要安装插件；当我点安装直接卡死」。

v1.5.37 把四条一起修了。但 v1.5.37 同时改了**行为**（启动一律直连、非阻塞启动、面板骨架屏），
用户随后反馈这三处让体验变差（「代理池加载慢、连接很久才会好、直连下行情都出不来」），
于是 **v1.5.38 把代理池整体回退到 v1.5.35 的行为**：

  - bootstrap() 恢复成「_load() → _apply_env() → _revive_kernel_async()」
    ⇒ 每次启动自动连上上次的节点，行情立即可用
  - `/api/proxies/kernel/start` 恢复同步等待；面板恢复即时渲染
  - 供应商文件恢复「原样落盘」

**只有一条修复保留**，就是本文件守的东西 ——
「取消连接」必须真的把代理链路拆掉，而不是只切一下 selector：

  1. set_active("") → teardown_proxy()（停内核）
  2. teardown_proxy() → _drop_node_preload()（从 NODE_OPTIONS 摘掉 --require=proxy-preload.cjs）
  3. teardown_proxy() → 清空 HTTP(S)_PROXY / ALL_PROXY 六个大小写变体
  4. _apply_env() 走直连分支时同样要 _drop_node_preload()

第 2 条是最容易被忽略的那一条：旧实现只在 `_ensure_node_preload()` 里**加**，从来不**摘**，
于是代理启用期间启动的 Node 子进程会一直攥着 undici 的 EnvHttpProxyAgent，
把 fetch 送往「已经被取消」的本地端口 —— 用户看到的就是「一取消连接，整个应用都没网」。

离线运行：不访问外网、不动用户数据（落盘路径全部重定向到临时目录）。
运行：python tests/test_v1538_proxy_teardown.py
"""
import ast
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PROXY_POOL_SRC = ROOT / "src" / "proxy_pool.py"

_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "NODE_OPTIONS",
             "http_proxy", "https_proxy", "all_proxy", "no_proxy")

_KERNEL_ENTRY = {
    "id": "k1", "name": "🇸🇬新加坡", "proto": "vless", "server": "1.2.3.4", "port": 443,
    "direct": False, "kernel_name": "🇸🇬新加坡", "status": "ok", "latency_ms": 407,
}


# ---------------- 环境隔离 ----------------

def _mods(tmp: Path):
    """导入被测模块并把落盘路径重定向到临时目录（绝不碰用户真实数据）。"""
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


def _env_snapshot():
    return {k: os.environ.get(k) for k in _ENV_KEYS}


def _env_restore(snap):
    for k, v in snap.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class _StubKernel:
    """替身内核：只记录有没有被 stop / select，绝不真的去 taskkill 用户进程。"""

    def __init__(self):
        self.stopped = 0
        self.selected = []

    def stop(self):
        self.stopped += 1
        return {"ok": True, "stopped": True}

    def select(self, name):
        self.selected.append(name)
        return True

    def is_running(self):
        return True

    def is_installed(self):
        return True


# ---------------- 1. 回退意图：bootstrap 必须恢复自动连代理 ----------------

def test_bootstrap_auto_revives_last_node():
    """v1.5.38 是**有意**回退：bootstrap 必须重新自动拉起内核，行情才开箱可用。"""
    src = PROXY_POOL_SRC.read_text(encoding="utf-8-sig")
    calls = set()
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == "bootstrap":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    calls.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    assert "_revive_kernel_async" in calls, (
        "bootstrap 不再自动连上上次的节点 —— v1.5.38 是特意回退到 v1.5.35 这个行为的，别改回去")
    assert "_apply_env" in calls, "bootstrap 没有注入代理环境变量"
    assert "def _revive_kernel_async" in src, "_revive_kernel_async 被删了，回退不完整"


# ---------------- 2. 取消连接 = 停内核 ----------------

def test_set_active_empty_calls_teardown():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _, pool = _mods(tmp)
        pool._state["active_id"] = _KERNEL_ENTRY["id"]
        pool._state["entries"] = [dict(_KERNEL_ENTRY)]
        stub = _StubKernel()
        real, pool._kernel = pool._kernel, (lambda: stub)
        snap = _env_snapshot()
        try:
            os.environ["HTTP_PROXY"] = "http://127.0.0.1:7899"
            os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7899"
            os.environ["ALL_PROXY"] = "http://127.0.0.1:7899"
            os.environ["NODE_OPTIONS"] = '--require="C:/x/proxy-preload.cjs"'
            pool.set_active("")
            assert pool._state["active_id"] == "", "取消连接后 active_id 没清空"
            assert stub.stopped >= 1, "取消连接没有停掉内核 —— 本地端口还活着，等于没取消"
            env = _env_snapshot()
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
                assert not os.environ.get(k), f"取消连接后 {k} 仍是 {os.environ.get(k)}"
            assert "proxy-preload.cjs" not in (os.environ.get("NODE_OPTIONS") or ""), (
                "取消连接后 NODE_OPTIONS 里仍挂着预加载 —— "
                "代理期间起来的 Node 子进程会继续把请求送往已取消的端口")
        finally:
            pool._kernel = real
            _env_restore(snap)


# ---------------- 3. 预加载必须能摘掉（含带空格路径）----------------

def test_drop_node_preload_handles_quoted_path_with_spaces():
    with tempfile.TemporaryDirectory() as td:
        _, pool = _mods(Path(td))
        snap = _env_snapshot()
        try:
            # 与 _ensure_node_preload() 写进去的形态一致：路径用双引号包住，可能含空格
            os.environ["NODE_OPTIONS"] = '--max-old-space-size=4096 --require="C:/Program Files/BAZZ/runtime/proxy-preload.cjs"'
            pool._drop_node_preload()
            left = os.environ.get("NODE_OPTIONS") or ""
            assert "proxy-preload.cjs" not in left, f"预加载没摘干净：{left!r}"
            assert "--max-old-space-size=4096" in left, f"误删了无关的 NODE_OPTIONS 标志：{left!r}"
            # 摘完不该留下双空格（否则会污染下一次拼接）
            assert "  " not in left, f"摘除后留下多余空白：{left!r}"
            # 只有一个 token 时应整体移除该变量
            os.environ["NODE_OPTIONS"] = '--require="/a b/proxy-preload.cjs"'
            pool._drop_node_preload()
            assert os.environ.get("NODE_OPTIONS") is None, "只剩预加载时应删掉整个 NODE_OPTIONS"
            # 单引号 / 无引号两种历史形态也要能摘
            for form in ('--require=\'/a/proxy-preload.cjs\'', '--require=/a/proxy-preload.cjs'):
                os.environ["NODE_OPTIONS"] = form
                pool._drop_node_preload()
                assert not os.environ.get("NODE_OPTIONS"), f"未能摘除形态 {form!r}"
        finally:
            _env_restore(snap)


# ---------------- 4. _apply_env 直连分支也要摘预加载 ----------------

def test_apply_env_direct_branch_drops_preload():
    with tempfile.TemporaryDirectory() as td:
        _, pool = _mods(Path(td))
        snap = _env_snapshot()
        try:
            pool._state["active_id"] = ""
            pool._state["entries"] = []
            os.environ["HTTP_PROXY"] = "http://127.0.0.1:7899"
            os.environ["NODE_OPTIONS"] = '--require="/a/proxy-preload.cjs"'
            pool._apply_env()
            assert not os.environ.get("HTTP_PROXY"), "_apply_env 直连分支没清代理变量"
            assert "proxy-preload.cjs" not in (os.environ.get("NODE_OPTIONS") or ""), (
                "_apply_env 直连分支没有摘掉预加载：直连之后 Node 子进程还在走代理")
        finally:
            _env_restore(snap)


# ---------------- 5. 源码护栏：别再退回「只切 selector」----------------

def test_teardown_is_reachable_and_complete():
    src = PROXY_POOL_SRC.read_text(encoding="utf-8-sig")
    assert "def teardown_proxy()" in src, "teardown_proxy 被删了"
    tree = ast.parse(src)
    body = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "teardown_proxy":
            body = node
    assert body is not None
    names = set()
    for sub in ast.walk(body):
        if isinstance(sub, ast.Call):
            f = sub.func
            names.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    assert "stop" in names, "teardown_proxy 没停内核"
    assert "_drop_node_preload" in names, "teardown_proxy 没摘预加载"
    # 直连分支不能再出现「只 select('DIRECT')」的老写法。
    # ⚠️ 必须走 AST —— 上面那段说明注释里就写着 `k.select("DIRECT")`，
    #    用文本断言会把注释当成代码（这个仓库已经踩过两次）。
    sa = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "set_active":
            sa = node
    assert sa is not None
    direct_branch = None
    for sub in ast.walk(sa):
        if isinstance(sub, ast.If) and isinstance(sub.test, ast.Compare):
            left = sub.test.left
            if isinstance(left, ast.Name) and left.id == "target":
                direct_branch = sub
                break
    assert direct_branch is not None, "set_active 里找不到 `if target is None:` 直连分支"
    branch_calls = set()
    for stmt in direct_branch.body:          # ⚠️ 只看直连分支本体，orelse 是 elif 内核分支（那里本该有 select）
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Call):
                f = sub.func
                branch_calls.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    assert "select" not in branch_calls, (
        "set_active 的直连分支又在调 select() 了 —— 只切 selector 不清栈，"
        "就是「取消连接后整个应用没网」的根因")
    assert "teardown_proxy" in branch_calls, "set_active 的直连分支没有调用 teardown_proxy"


def main():
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
            passed += 1
        except Exception:
            print(f"  FAIL {name}")
            traceback.print_exc()
            failed.append(name)
    print(f"\n{passed}/{len(tests)} passed")
    if failed:
        print("失败：" + ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
