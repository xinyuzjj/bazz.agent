"""v1.5.37 回归测试：代理池「开机自动接管 / 取消连接后断网 / 内核形同虚设 / 点一下卡死」。

现场（F:\\1\\BAZZ.AGENT，v1.5.36）用户描述：
  1. 「一进去是默认使用连接代理池的，这不对」
  2. 「当我取消连接，整个应用都没有网了」
  3. 「并且显示需要安装插件」（钱包页拿到失败探测 → 误报未安装）
  4. 「当我点安装直接卡死」

四个缺陷的根因（都有现场物证）：

A. 开机静默接管流量
   proxies.json 里 active_id 是一个 vless 内核型节点；bootstrap() → _load() 把它恢复成
   「已启用」，再 _apply_env() + _revive_kernel_async() 自动拉起 mihomo 并把全部流量切进代理池。
   现场 kernel.log 在 18:20:58 / 18:23:15 两次自动启动可证。
   → 启动一律直连；选择存 last_active_id，用户显式点「恢复」才生效。

B. 取消连接 ≠ 回到直连
   直连分支只调 k.select("DIRECT")，**内核照跑**，且 _ensure_node_preload() 只往 NODE_OPTIONS
   里加 --require=proxy-preload.cjs、**从来不摘**。代理启用期间起来的 Node 子进程继续拿着
   EnvHttpProxyAgent 把 fetch 送进那个本地端口。
   → teardown_proxy()：停内核 + 摘预加载 + 清 env。

C. provider 形同虚设
   save_provider() 只要原文「包含 proxies:」就把**整份 clash 配置**（mixed-port / dns /
   proxy-groups / rules）原样落盘。现场 F:\\1\\BAZZ.AGENT\\.system\\kernel\\providers\\sub_*.yaml
   就是 566 行的完整配置 —— 而 mihomo 的 proxy-providers:{type: file} 只认 proxies: 段。
   → 只抽 proxies: 段落盘；抽不到 / 0 个节点就拒绝写入。

D. 点一下卡死
   proxy_kernel.start() 在 `with _LOCK:` **内部**做完 40×0.5s 的控制面等待，也就是持锁最长 20 秒。
   同一个 _LOCK 还被 list_pool()（/api/proxies）、start_download()、stop() 依赖 ——
   前端 15s / 1.5s 两套轮询全部堵死。
   → 等待循环移到锁外；启动走非阻塞 start_kernel_async()，用 status().starting 反馈进度。

离线运行：不访问外网、不动用户数据（落盘路径全部重定向到临时目录）。
运行：python tests/test_v1537_proxy_identity.py
"""
import ast
import json
import os
import re
import sys
import tempfile
import traceback
from pathlib import Path


def _calls_in_function(src: str, func_name: str) -> set:
    """返回某个顶层函数体内**真实调用**到的名字。

    只走 AST，因此文档字符串 / 注释 / 普通字符串里出现函数名不会误判 ——
    v1.5.37 的 bootstrap() 文档里专门解释了「为什么不再调用 _revive_kernel_async()」，
    用纯文本切片断言会把这段说明当成调用。
    """
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            names = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    if isinstance(f, ast.Name):
                        names.add(f.id)
                    elif isinstance(f, ast.Attribute):
                        names.add(f.attr)
            return names
    raise AssertionError(f"源码里找不到函数 {func_name}()")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

ROOT_SRC = ROOT / "src"
UI = ROOT / "frontend" / "src"
LOCALES = UI / "i18n" / "locales.ts"
PROXY_VIEW = UI / "views" / "ProxyPoolView.tsx"

# 真实现场里那份订阅文件（566 行完整 clash 配置）。逐字保留结构用于回归。
FULL_CONFIG_SUB = """\
mixed-port: 7890
allow-lan: false
bind-address: '*'
mode: rule
log-level: info
external-controller: '127.0.0.1:9090'
unified-delay: true
tcp-concurrent: true
dns:
    enable: true
    ipv6: false
    enhanced-mode: fake-ip
    fake-ip-range: 198.18.0.1/16
proxies:
    - { name: '节点A', server: 1.2.3.4, port: 443, type: hysteria2, password: pw-a }
    - { name: '节点B', server: 5.6.7.8, port: 443, type: vless, uuid: u-b }
    - { name: '节点C', server: 9.9.9.9, port: 35000, type: hysteria2, password: pw-c }
proxy-groups:
    - name: PROXY
      type: select
      proxies: [节点A, 节点B]
rules:
    - MATCH,PROXY
"""


class Skip(Exception):
    pass


# ---------------- 环境隔离 ----------------

def _mods(tmp: Path):
    """导入被测模块，落盘路径全部重定向到临时目录（绝不碰用户真实数据）。"""
    import proxy_kernel
    import proxy_pool
    kdir = tmp / "kernel"
    kdir.mkdir(parents=True, exist_ok=True)
    proxy_kernel.KERNEL_DIR = str(kdir)
    proxy_kernel.PROVIDERS_DIR = str(kdir / "providers")
    proxy_kernel.STATE_PATH = str(kdir / "state.json")
    proxy_kernel.CONFIG_PATH = str(kdir / "config.yaml")
    proxy_kernel.MIHOMO_EXE = str(kdir / "mihomo.exe")
    proxy_pool.POOL_PATH = str(tmp / "proxies.json")
    return proxy_kernel, proxy_pool


_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy",
             "all_proxy", "NO_PROXY", "no_proxy", "NODE_OPTIONS")


def _env_snapshot():
    return {k: os.environ.get(k) for k in _ENV_KEYS}


def _env_restore(snap):
    for k, v in snap.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _seed_pool(pool, tmp: Path, *, active_id="", entries=None, last=""):
    """把池子写成落盘状态，模拟「上一次会话选过节点」。"""
    data = {"active_id": active_id, "last_active_id": last, "entries": entries or []}
    Path(pool.POOL_PATH).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    pool._state["active_id"] = active_id
    pool._state["last_active_id"] = last
    pool._state["entries"] = list(entries or [])


def _kernel_node(nid="k1", name="🇸🇬新加坡"):
    return {"id": nid, "name": name, "group": "g", "source": "s", "sub_url": "https://x/sub",
            "kernel_name": name, "proto": "vless", "server": "138.2.84.141", "port": 443,
            "username": "", "password": "", "direct": False, "note": "", "latency_ms": 407,
            "status": "ok", "fails": 0, "error": "", "last_tested": 0}


# ---------------- C. provider 只写 proxies 段 ----------------

def test_provider_keeps_only_proxies_section():
    """整份 clash 配置必须被削成只含 proxies 段的 provider 文件。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pk, _ = _mods(tmp)
        r = pk.save_provider("https://dash.example/api/sub?token=x", FULL_CONFIG_SUB)
        assert r.get("ok"), f"保存 provider 失败：{r}"
        assert r.get("count") == 3, f"节点数应为 3，实际 {r.get('count')}"
        txt = Path(r["path"]).read_text(encoding="utf-8")
        assert txt.startswith("proxies:\n"), "provider 文件必须以 proxies: 开头"
        for banned in ("mixed-port:", "external-controller:", "dns:", "proxy-groups:", "rules:"):
            assert banned not in txt, f"provider 文件里仍有顶层键 {banned} —— 内核加载会失败"
        for nm in ("节点A", "节点B", "节点C"):
            assert nm in txt, f"provider 丢了节点 {nm}"


def test_provider_refuses_empty_proxies():
    """抽不到 proxies / 0 个节点时必须拒绝写入，不能把已有可用 provider 覆盖成空气。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pk, _ = _mods(tmp)
        url = "https://dash.example/api/sub?token=y"
        good = pk.save_provider(url, FULL_CONFIG_SUB)
        assert good.get("ok") and good.get("count") == 3
        before = Path(good["path"]).read_text(encoding="utf-8")

        r1 = pk.save_provider(url, "dns:\n  enable: true\nrules:\n  - MATCH,DIRECT\n")
        assert r1.get("ok") is False, "没有 proxies 段却报告成功"
        r2 = pk.save_provider(url, "proxies:\n\nrules:\n  - MATCH,DIRECT\n")
        assert r2.get("ok") is False, "proxies 段为空却报告成功"
        assert Path(good["path"]).read_text(encoding="utf-8") == before, "空订阅把可用 provider 覆盖掉了"


def test_provider_accepts_flow_and_block_styles():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pk, _ = _mods(tmp)
        block = "proxies:\n  - { name: A, type: ss }\n  - { name: B, type: ss }\n"
        flow = "proxies: [{ name: A, type: ss }, { name: B, type: ss }]\n"
        rb = pk.save_provider("https://a/x", block)
        rf = pk.save_provider("https://b/y", flow)
        assert rb.get("count") == 2 and rf.get("count") == 2, f"两种写法都应识别 2 个节点：{rb} {rf}"


def test_provider_summary_flags_zero_node_files():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pk, _ = _mods(tmp)
        pk.save_provider("https://a/x", FULL_CONFIG_SUB)
        pdir = Path(pk.PROVIDERS_DIR)
        (pdir / "broken.yaml").write_text("mixed-port: 1\nrules: []\n", encoding="utf-8")
        summary = {s["file"]: s["count"] for s in pk.provider_summary()}
        assert sorted(summary.values()) == [0, 3], f"诊断应报出 0 节点文件：{summary}"


# ---------------- A. 启动不再自动接管 ----------------

def test_bootstrap_starts_direct_and_remembers_last_choice():
    """启动必须直连：active_id 清空、env 干净、选择记进 last_active_id。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _, pool = _mods(tmp)
        node = _kernel_node()
        _seed_pool(pool, tmp, active_id=node["id"], entries=[node])
        snap = _env_snapshot()
        try:
            os.environ["HTTP_PROXY"] = "http://127.0.0.1:7899"   # 模拟上次会话残留在环境里
            pool.bootstrap()
            assert pool._state["active_id"] == "", "启动后 active_id 仍带着上次的节点 —— 会静默接管流量"
            assert pool._state["last_active_id"] == node["id"], "上次的选择被丢了，用户没法一键恢复"
            env = pool.env_snapshot()
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
                assert not env.get(k), f"启动后 {k} 仍被注入：{env}"
            saved = json.loads(Path(pool.POOL_PATH).read_text(encoding="utf-8"))
            assert saved["active_id"] == "" and saved["last_active_id"] == node["id"], f"落盘状态不对：{saved}"
        finally:
            _env_restore(snap)


def test_bootstrap_never_calls_kernel_autostart():
    src = (ROOT_SRC / "proxy_pool.py").read_text(encoding="utf-8-sig")
    calls = _calls_in_function(src, "bootstrap")
    assert "_revive_kernel_async" not in calls, f"bootstrap 仍会自动拉起内核：{sorted(calls)}"
    assert "start_kernel" not in {c for c in calls}, f"bootstrap 不应启动内核：{sorted(calls)}"
    assert "_apply_env" in calls, "bootstrap 必须调用 _apply_env() 以保证启动即直连"
    assert "def _revive_kernel_async" not in src, "_revive_kernel_async 应已删除"


def test_list_pool_exposes_last_active_id():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _, pool = _mods(tmp)
        node = _kernel_node()
        _seed_pool(pool, tmp, active_id="", entries=[node], last=node["id"])
        d = pool.list_pool()
        assert d.get("last_active_id") == node["id"], "接口没有把 last_active_id 暴露给界面"


# ---------------- B. 取消连接 = 彻底还原 ----------------

def test_direct_switch_tears_down_kernel_and_preload():
    """切直连必须停内核 + 摘掉 NODE_OPTIONS 预加载 + 清 env，而不是只切 selector。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pk, pool = _mods(tmp)
        node = _kernel_node()
        _seed_pool(pool, tmp, active_id=node["id"], entries=[node])
        snap = _env_snapshot()
        calls = {"stop": 0}
        real_stop = pk.stop
        try:
            pk.stop = lambda: (calls.__setitem__("stop", calls["stop"] + 1) or {"ok": True})
            os.environ["HTTP_PROXY"] = "http://127.0.0.1:7899"
            os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7899"
            os.environ["NODE_OPTIONS"] = '--max-old-space-size=4096 --require="F:/1/BAZZ.AGENT/runtime/proxy-preload.cjs"'
            pool.set_active("")
            assert calls["stop"] == 1, "取消连接没有真正停掉内核 —— 本机仍在监听那个混合端口"
            env = pool.env_snapshot()
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
                assert not env.get(k), f"取消连接后 {k} 仍在：{env}"
            assert "proxy-preload.cjs" not in os.environ.get("NODE_OPTIONS", ""), \
                "NODE_OPTIONS 里的预加载没摘 —— 已起的 Node 子进程会继续走死代理"
            assert "--max-old-space-size=4096" in os.environ.get("NODE_OPTIONS", ""), \
                "摘预加载时把用户自己的 NODE_OPTIONS 一起清掉了"
        finally:
            pk.stop = real_stop
            _env_restore(snap)


def test_drop_preload_handles_spaces_in_path():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _, pool = _mods(tmp)
        snap = _env_snapshot()
        try:
            os.environ["NODE_OPTIONS"] = '--require="F:/某 个 目录/BAZZ.AGENT/runtime/proxy-preload.cjs" --trace-warnings'
            pool._drop_node_preload()
            left = os.environ.get("NODE_OPTIONS", "")
            assert "proxy-preload.cjs" not in left, f"带空格的路径没摘掉：{left!r}"
            assert "--trace-warnings" in left, f"其他参数被误删：{left!r}"
            os.environ["NODE_OPTIONS"] = '--require="X/proxy-preload.cjs"'
            pool._drop_node_preload()
            assert "NODE_OPTIONS" not in os.environ, "只剩预加载时应把 NODE_OPTIONS 整个删掉"
        finally:
            _env_restore(snap)


def test_kernel_stop_resets_kernel_type_active():
    """停内核后必须把内核型节点的 active 回落直连，否则界面显示「使用中」却没代理。"""
    src = (ROOT / "desktop_app.py").read_text(encoding="utf-8-sig")
    blk = src[src.index('@app.post("/api/proxies/kernel/stop")'):]
    blk = blk[:blk.index('@app.', 10)]
    assert 'set_active("")' in blk, "停内核后没有回落直连 —— 会留下「显示已启用但内核没跑」的假状态"


# ---------------- D. 不再卡死 ----------------

def test_kernel_start_waits_outside_lock():
    """等待循环必须在 _LOCK 之外 —— 否则持锁 20s，前端所有轮询一起堵死。"""
    src = (ROOT_SRC / "proxy_kernel.py").read_text(encoding="utf-8-sig")
    body = src[src.index("def start():"):src.index("def _pid_image(")]
    wait = body.index("for _ in range(40):")
    # 等待循环之后不应该再出现「进入 with _LOCK」包裹它的写法：
    # 判定方式是「等待循环之前，最后一个 with _LOCK 块已经结束」——
    # 直接检查等待循环与上一次 with _LOCK 的缩进关系。
    lines = body.split("\n")
    wait_indent = None
    lock_indent = None
    for i, l in enumerate(lines):
        if "for _ in range(40):" in l:
            wait_indent = len(l) - len(l.lstrip())
        if "with _LOCK:" in l:
            lock_indent = len(l) - len(l.lstrip())
    assert wait_indent is not None and lock_indent is not None
    assert wait_indent <= lock_indent, "等待循环仍缩进在 with _LOCK 内部 —— 持锁等 20 秒"
    assert "# ---- 等待就绪：必须在 _LOCK 之外 ----" in body, "缺少「等待移到锁外」的显式说明"
    assert "_starting = True" in body and "finally:" in body, "缺少 _starting 标志 / 复位"


def test_status_reports_starting():
    src = (ROOT_SRC / "proxy_kernel.py").read_text(encoding="utf-8-sig")
    body = src[src.index("def status():"):src.index("def _provider_id(")]
    assert '"starting": starting' in body, "status() 没有把「启动中」暴露给界面 —— 用户只能干等"
    assert "provider_summary()" in body, "status() 没有暴露 provider 节点数，空 provider 无法被发现"


def test_kernel_start_endpoint_is_non_blocking():
    src = (ROOT / "desktop_app.py").read_text(encoding="utf-8-sig")
    blk = src[src.index('@app.post("/api/proxies/kernel/start")'):]
    blk = blk[:blk.index('@app.', 10)]
    assert "start_kernel_async()" in blk, "内核启动端点仍是同步阻塞 —— 点一下界面就等着"
    pool_src = (ROOT_SRC / "proxy_pool.py").read_text(encoding="utf-8-sig")
    body = pool_src[pool_src.index("def start_kernel_async():"):]
    assert "threading.Thread" in body, "start_kernel_async 没有放后台线程"
    assert "_apply_env()" in body, "内核起来后没有注入 env"


def test_concurrent_start_does_not_double_spawn():
    src = (ROOT_SRC / "proxy_kernel.py").read_text(encoding="utf-8-sig")
    body = src[src.index("def start():"):src.index("def _pid_image(")]
    assert "if _starting:" in body, "重复点「启动内核」会重复 Popen —— 需要 _starting 幂等保护"


# ---------------- UI：不许拿默认值假装正常 ----------------

def test_ui_pool_view_has_no_lying_defaults():
    src = PROXY_VIEW.read_text(encoding="utf-8-sig")
    assert "useState<any>(null)" in src, "pool 初值仍是那个假状态（entries:[] / installed:false）"
    assert "loadErr" in src and "setLoadErr" in src, "load() 仍在吞掉错误"
    load_body = src[src.index("const load = useCallback("):]
    load_body = load_body[:load_body.index("useEffect")]
    assert "catch (e: any)" in load_body and "setLoadErr" in load_body, \
        "load() 失败时没有把错误 surface 出来 —— 又变成「静默显示空面板」"
    assert '"proxy.loadFail"' in src and '"proxy.retry"' in src, "缺少加载失败的界面"


def test_ui_latency_and_status_are_separate_columns():
    """延迟列与状态列曾连写两遍同一个 statusCell，渲染出完全一样的内容。"""
    src = PROXY_VIEW.read_text(encoding="utf-8-sig")
    assert "const latencyCell" in src, "缺少独立的延迟单元格"
    assert "{latencyCell(e)}</td>" in src, "延迟列没有用 latencyCell"
    row = src[src.index("const active = pool.active_id === e.id;"):]
    row = row[:row.index("</tr>")]
    assert "{latencyCell(e)}</td>" in row and "{statusCell(e)}</td>" in row, "两列应各渲染各的"
    assert row.count("{statusCell(e)}") == 1, "statusCell 仍被渲染了两次（延迟列重复）"


def test_ui_empty_hint_matches_button_position():
    src = LOCALES.read_text(encoding="utf-8-sig")
    assert "点右上角" not in src, "空状态提示仍写「右上角」，而「导入代理」按钮在左上角"


def test_ui_i18n_new_keys_both_locales():
    src = LOCALES.read_text(encoding="utf-8-sig")
    keys = ["proxy.alive", "proxy.kStarting", "proxy.kStartingShort", "proxy.restoreLast",
            "proxy.restoreTip", "proxy.staleWarn", "proxy.providerEmpty", "proxy.loadFail",
            "proxy.retry", "proxy.importProviderWarn"]
    for k in keys:
        n = len(re.findall(r'"' + re.escape(k) + r'":', src))
        assert n == 2, f"{k} 应在中英两套里各出现一次，实际 {n} 次"


def test_ui_proto_badge_uses_theme_tokens():
    """direct 徽章曾用 bg-black/30 + text-ink-dim，浅色主题实测 3.44:1（低于 AA 4.5）。"""
    src = PROXY_VIEW.read_text(encoding="utf-8-sig")
    body = src[src.index("const protoBadge"):]
    body = body[:body.index(");")]
    assert "text-sky-300" not in body and "text-violet-300" not in body, \
        "徽章仍用 300 级亮色 —— 浅色主题下不可读"


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
