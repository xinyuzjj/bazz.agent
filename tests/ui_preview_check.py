"""Browser-only visual smoke test for the isolated BAZZ UI preview.

Never launches the real backend. Run while outputs/ui-preview is served on 127.0.0.1:5186.

Windows note: the CLI boots a persistent daemon that inherits our stdout/stderr handles.
Capturing with pipes therefore deadlocks on the first call (communicate() waits for EOF that
never comes). All output is redirected to files instead, and every call has a hard timeout.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "ui-preview"
SESSION = "bazz-ui-review"
CLI = Path.home() / ".workbuddy-ai/binaries/node/workspace/node_modules/agent-browser/bin/agent-browser-win32-x64.exe"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
ENV["PATH"] = str(Path.home() / ".workbuddy-ai/binaries/node/versions/22.22.2-2") + os.pathsep + ENV["PATH"]

checks = []
failures = []
_logdir = Path(tempfile.mkdtemp(prefix="bazz-ui-preview-"))


def run(*args, timeout=90):
    """Run one CLI command. Returns stripped stdout; raises on timeout or non-zero exit."""
    out = _logdir / "out.txt"
    err = _logdir / "err.txt"
    with out.open("wb") as o, err.open("wb") as e:
        proc = subprocess.Popen([str(CLI), "--session", SESSION, *args],
                                stdout=o, stderr=e, stdin=subprocess.DEVNULL, env=ENV)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)
            raise AssertionError(f"timeout after {timeout}s: agent-browser {' '.join(args)}")
    stdout = out.read_text("utf-8", "replace").strip()
    stderr = err.read_text("utf-8", "replace").strip()
    if code:
        raise AssertionError(f"agent-browser {' '.join(args)} -> rc={code}\n{stderr or stdout}")
    return stdout


def evaluate(code):
    return run("eval", code)


def check(name, condition):
    """Record one assertion. Never aborts the suite, so every view still gets captured."""
    try:
        result = evaluate(f"Promise.resolve({condition}).then(Boolean)")
    except AssertionError as exc:
        result = f"error: {exc}"
    ok = result == "true"
    checks.append({"name": name, "passed": ok, "result": result})
    if not ok:
        failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'} {name}", flush=True)
    return ok


def nav(label):
    run("find", "role", "button", "click", "--name", label, "--exact")
    evaluate("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")


def shot(name):
    evaluate("document.fonts.ready.then(() => true)")
    run("screenshot", str(OUT / name))


def confirm_dialog():
    """Click the danger button of the in-app dialog (AppDialogHost renders it as .btn-halt)."""
    evaluate("(document.querySelector('.glass-bright .btn-halt') "
             "|| [...document.querySelectorAll('.glass-bright button')].at(-1)).click()")


def cleanup_session():
    """Release a session left over from an earlier interrupted run."""
    try:
        run("close", timeout=20)
    except BaseException:
        # 没有在跑的会话时 close 会报错；沙箱的批量删除保护也会抛 SystemExit，一并吞掉
        pass


VIEWS = [("对话", "chat"), ("行情", "markets"), ("Agent 钱包", "wallet"), ("Binance CEX", "cex"),
         ("广场", "council"), ("技能库", "skills"), ("记忆", "memory"), ("设置", "settings")]

# 顶栏若被钉死高度、而内容（品牌行 + 导航行）比它高，导航会溢出去压住正文 —— 肉眼很容易漏掉
BAR_CLEARANCE = ("(() => {const b=document.querySelector('.app-topbar').getBoundingClientRect();"
                 "const c=document.querySelector('.workspace-content').getBoundingClientRect();"
                 "return b.bottom <= c.top + 1})()")

try:
    cleanup_session()
    run("open", "http://127.0.0.1:5186/preview.html")
    run("set", "viewport", "1440", "980")
    run("wait", "--fn", "document.querySelectorAll('.topbar-nav-item').length === 8")
    check("preview banner present", "!!document.querySelector('.ui-preview-banner')")

    for index, (label, view) in enumerate(VIEWS, 1):
        try:
            nav(label)
            check(f"{view}: mounted without render errors",
                  f"document.querySelector('.view-{view}') && !(window.__boot_err__ || []).length")
            check(f"{view}: document width", "document.documentElement.scrollWidth <= innerWidth + 1")
            check(f"{view}: topbar does not overlap content", BAR_CLEARANCE)
        except AssertionError as exc:
            checks.append({"name": f"{view}: navigation", "passed": False, "result": str(exc)[:300]})
            failures.append(f"{view}: navigation")
            print(f"FAIL {view}: navigation -> {exc}", flush=True)
        shot(f"{index:02d}-{view}-light.png")

    nav("广场")
    evaluate("[...document.querySelectorAll('button')].find(b => b.textContent.trim().startsWith('失败')).click()")
    check("failed cards count", "document.querySelectorAll('.square-post').length === 2")
    evaluate("document.querySelector('.square-post button[title=\"删除\"]').click()")
    run("wait", "--text", "确定删除")
    shot("09-delete-confirm.png")
    evaluate("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === '取消').click()")
    check("cancel keeps failed posts", "document.querySelectorAll('.square-post').length === 2")
    evaluate("new Promise(r => setTimeout(r, 260))")
    evaluate("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === '清空所有失败').click()")
    run("wait", "--text", "确定清空")
    confirm_dialog()
    run("wait", "--fn", "document.querySelectorAll('.square-post').length === 0")
    check("clear failed preserves successful posts",
          "(async () => {const r=await (await fetch('/api/square/posts')).json();"
          "return r.posts.length===2 && r.posts.every(p=>p.status==='posted')})()")

    nav("对话")
    # NOTE: this CLI's `find role textbox` locator never resolves (returns "Element not found"
    # for a plain <input> that does expose aria-label) — verified against 4 argument forms.
    # `fill <css> <text>` works, and the selector doubles as the accessible-name assertion.
    check("chat composer is labelled", "!!document.querySelector('input[aria-label=\"向 Agent 发送消息\"]')")
    run("fill", "input[aria-label='向 Agent 发送消息']", "测试预览对话")
    # Icon-only buttons carry no text, and this CLI does not resolve `--name` from
    # aria-label/title — target them by attribute selector instead (see note above).
    run("click", ".chat-composer button[title='发送']")
    run("wait", "--text", "固定 fixture")
    check("chat streamed demo reply", "document.querySelector('.chat-messages').textContent.includes('固定 fixture')")
    nav("行情")
    nav("对话")
    check("chat persists across navigation", "document.querySelector('.chat-messages').textContent.includes('固定 fixture')")

    run("click", ".topbar-tools button[aria-label='切换为深色']")
    check("dark theme", "document.documentElement.dataset.theme === 'dark'")
    shot("10-chat-dark.png")
    for label, view in [("行情", "markets"), ("技能库", "skills"), ("设置", "settings")]:
        nav(label)
        check(f"{view}: dark mode renders", "!(window.__boot_err__ || []).length")
        shot(f"11-{view}-dark.png")
    run("click", ".topbar-tools button[aria-label='切换为浅色']")

    for width in [1024, 760, 390]:
        run("set", "viewport", str(width), "900")
        for label, view in [("对话", "chat"), ("行情", "markets"), ("技能库", "skills"), ("设置", "settings")]:
            try:
                nav(label)
                # 断言导航真的切过去了：窄屏下顶栏导航要横向滚动，只按名字点会静默点空
                check(f"{width}px {view}: view mounted", f"document.querySelector('.view-{view}')")
                check(f"{width}px {view}: document width", "document.documentElement.scrollWidth <= innerWidth + 1")
                check(f"{width}px {view}: topbar does not overlap content", BAR_CLEARANCE)
                check(f"{width}px {view}: no render errors", "!(window.__boot_err__ || []).length")
            except AssertionError as exc:
                checks.append({"name": f"{width}px {view}: navigation", "passed": False, "result": str(exc)[:300]})
                failures.append(f"{width}px {view}: navigation")
                print(f"FAIL {width}px {view}: navigation -> {exc}", flush=True)
            shot(f"12-{view}-{width}.png")

    check("all network requests are local static assets",
          "performance.getEntriesByType('resource').every(r=>r.name.startsWith(location.origin) && !r.name.includes('/api/'))")
except BaseException as exc:  # structural failure (含沙箱守卫抛的 SystemExit): 仍要落报告
    checks.append({"name": "suite aborted", "passed": False, "result": repr(exc)[:500]})
    failures.append("suite aborted")
    print(f"ABORTED {exc!r}", flush=True)
finally:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "verification.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        run("close", timeout=25)
    except Exception:
        pass
    shutil.rmtree(_logdir, ignore_errors=True)
    total = len(checks)
    print(f"Completed {total} checks · {total - len(failures)} passed · {len(failures)} failed", flush=True)
    if failures:
        print("Failed: " + "; ".join(failures), flush=True)
    sys.exit(1 if failures else 0)
