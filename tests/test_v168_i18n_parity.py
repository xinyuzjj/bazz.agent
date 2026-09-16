"""v1.6.8 测试 —— 前端/后端「契约一致性」三件套（i18n 键 / API 端点 / mock 谎报）。

背景：用户问「还有哪里有问题」时，扫出四个**真实但长期没被发现**的缺陷，
它们有一个共同点 —— **都不是逻辑错误，而是「两侧契约漂移」**，靠跑一遍看不出来：

  ① `AdminPanels.tsx` 的 4 通道面板 raw fetch `/api/wallet/status`，
     而后端只有 `/api/wallet`（返回 `{cli, status, commands, ...}`）→ 真实 app 里
     该卡片**恒显示「未连接」**。为什么几个版本没人发现：`preview/mock.ts` 里
     **恰好 mock 了** `/api/wallet/status` → 隔离预览一切正常，**mock 把真 bug 盖住了**。
  ② 22 个 `track.*` 键**只有 zh 有** → 英文界面下「订单跟踪」面板整块显示裸 key。
  ③ `common.saved` 只有 en 有、`common.cancel` **两本都没有**、`markets.trackWin`
     只有 en 有 —— 中文是**默认语言**，所以这几处中文界面直接显示裸 key。
  ④ 前端 1164 个 `t("...")` 键里，任何一个漏进字典都会静默降级成 key 本身
     （`i18n.tsx`：`dict[key] ?? key`，**没有 zh 兜底**）。

本文件就是给这类漂移上机器守。它不测业务逻辑，只测「两侧是否还对得上」：

  一组 i18n 键集合：zh 与 en 必须**完全相等**，且各自**无重复键**
  二组 使用侧覆盖：前端所有 `t("...")` 用到的键必须两本字典都有
  三组 API 端点：`api.ts` 里的每个端点都要能在 `desktop_app.py` 找到路由
  四组 裸 fetch：非 api.ts 的 `fetch("/api/...")` 同样要能找到路由
  五组 mock 反查：预览 mock 不得伪造后端不存在的端点（否则又会盖住真 bug）

⚠️ 后端路由一律走 **AST** 提取，不做子串匹配 —— 本文件对应的那次修复，
   `desktop_app.py` 的**函数 docstring 里就写着** `/api/wallet/status`（解释它为什么存在），
   子串匹配会让断言**永远通过**，正好把这个 bug 放过去。

运行：.venv/Scripts/python.exe tests/test_v168_i18n_parity.py
"""
import ast
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
FE = os.path.join(ROOT, "frontend", "src")

FAILS = []


def check(name: str, cond: bool, extra: str = "") -> None:
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f"  ({extra})" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


# ============================================================
# 工具
# ============================================================
def _read(path: str) -> str:
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _strip_ts_comments(s: str) -> str:
    """去掉 // 行注释与 /* */ 块注释。

    否则「文档里提到 t("foo.bar")」会让「使用侧覆盖」断言误判成真。
    （注意：本仓库的 locales.ts 里就有解释性注释在正文提到键名。）
    """
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    out = []
    for line in s.splitlines():
        # 只截掉不在字符串里的 //（简化处理：取第一个 // 之前）
        i = line.find("//")
        out.append(line if i < 0 else line[:i])
    return "\n".join(out)


def _backend_routes() -> set:
    """AST 提取 `@app.<method>("path")` / `@router.<method>("path")` 的路径。

    必须走 AST：`desktop_app.py` 里存在**只在 docstring / 注释里出现**的端点名
    （v1.5.67 新增的 `/api/wallet/status` 路由，其 docstring 解释了自己为什么存在）。
    子串匹配会把注释也算命中 —— 那正是这个 bug 能藏住几个版本的原因。
    """
    tree = ast.parse(_read(os.path.join(ROOT, "desktop_app.py")))
    paths = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            f = dec.func
            if not isinstance(f, ast.Attribute):
                continue
            owner = getattr(f.value, "id", "")
            if owner not in ("app", "router"):
                continue
            if f.attr not in ("get", "post", "put", "delete", "patch"):
                continue
            if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                paths.add(_norm_be(dec.args[0].value))
    return paths


def _norm_be(p: str) -> str:
    p = re.sub(r"\{[^}]*\}", "*", p)
    return p.rstrip("/") or "/"


def _norm_call(p: str) -> str:
    """把调用侧的路径归一：去 query、模板插值 -> *、末尾斜杠归一。"""
    p = p.split("?")[0]
    p = re.sub(r"\$\{[^}]*\}", "*", p)
    p = re.sub(r"\{[^}]*\}", "*", p)
    return p.rstrip("/") or "/"


def _fe_files(include_mock: bool = True):
    for base, _dirs, files in os.walk(FE):
        for fn in files:
            if not fn.endswith((".ts", ".tsx")):
                continue
            rel = os.path.relpath(os.path.join(base, fn), FE)
            if not include_mock and rel.replace("\\", "/").startswith("preview/"):
                continue
            yield rel, os.path.join(base, fn)


def _locale_blocks():
    """切出 zh / en 两个字典块的源码行。"""
    lines = _read(os.path.join(FE, "i18n", "locales.ts")).splitlines()
    zs = next(i for i, l in enumerate(lines) if re.match(r"export const zh", l))
    es = next(i for i, l in enumerate(lines) if re.match(r"export const en", l))
    return lines, zs, es


def _keys(lines, a, b):
    """按行取 `"key":` 形式的键；返回 (去重集合, 全部出现列表)。"""
    found = [m.group(1) for m in
             (re.match(r'\s*"([A-Za-z0-9_.\-]+)"\s*:', l) for l in lines[a:b]) if m]
    return set(found), found


# ============================================================
# 一组：zh / en 键集合必须完全相等
# ============================================================
def test_locale_key_sets_are_identical():
    lines, zs, es = _locale_blocks()
    zh, zh_all = _keys(lines, zs, es)
    en, en_all = _keys(lines, es, len(lines))
    check("zh 字典非空（解析没歪）", len(zh) > 800, f"zh={len(zh)}")
    check("en 字典非空（解析没歪）", len(en) > 800, f"en={len(en)}")
    diff = sorted(zh ^ en)
    check("zh 与 en 键集合完全相等", not diff,
          f"差集 {len(diff)} 个：{diff[:12]}")
    check("只有 zh 有的键为空", not (zh - en), f"{sorted(zh - en)[:12]}")
    check("只有 en 有的键为空", not (en - zh), f"{sorted(en - zh)[:12]}")


def test_no_duplicate_keys_within_dict():
    """同一字典内重复键会**静默覆盖**前一个 —— 补键时最容易踩。"""
    lines, zs, es = _locale_blocks()
    _zh, zh_all = _keys(lines, zs, es)
    _en, en_all = _keys(lines, es, len(lines))
    for label, allk in (("zh", zh_all), ("en", en_all)):
        seen, dups = set(), []
        for k in allk:
            if k in seen:
                dups.append(k)
            seen.add(k)
        check(f"{label} 字典内无重复键", not dups, f"重复：{sorted(set(dups))[:12]}")


def test_i18n_has_no_zh_fallback_documented():
    """把「没有 zh 兜底」这个前提钉住。

    若哪天有人给 i18n 加了兜底，本文件的严重性会下降、但断言仍应成立 ——
    所以只断言**当前实现**是 `?? key`（不兜底），并解释为什么。
    """
    s = _read(os.path.join(FE, "i18n", "i18n.tsx"))
    check("i18n 未命中时回落为 key 自身（因此缺键 = 界面显示裸 key）",
          re.search(r"dict\[key\]\s*\?\?\s*key", s) is not None)


# ============================================================
# 二组：前端用到的每个键都必须两本字典都有
# ============================================================
def test_all_used_keys_are_defined():
    lines, zs, es = _locale_blocks()
    zh, _ = _keys(lines, zs, es)
    en, _ = _keys(lines, es, len(lines))
    used = {}
    for rel, path in _fe_files():
        if rel.replace("\\", "/").endswith("i18n/locales.ts"):
            continue
        src = _strip_ts_comments(_read(path))
        for m in re.finditer(r'\bt\(\s*"([A-Za-z0-9_.\-]+)"', src):
            used.setdefault(m.group(1), set()).add(rel)
    check("扫到足量使用点（脚本没失效）", len(used) > 500, f"used={len(used)}")
    miss_both = sorted(k for k in used if k not in zh and k not in en)
    miss_zh = sorted(k for k in used if k not in zh)
    miss_en = sorted(k for k in used if k not in en)
    check("没有「两本字典都没有」的使用键", not miss_both,
          f"{miss_both[:12]}  ← 出现在 {sorted(used.get(miss_both[0], []))[:2] if miss_both else []}")
    check("没有「zh 缺」的使用键（中文是默认语言）", not miss_zh, f"{miss_zh[:12]}")
    check("没有「en 缺」的使用键", not miss_en, f"{miss_en[:12]}")
    # 反向：字典里应当有键被用到 —— 只是提醒，不判失败（存在合法的动态拼接键）
    unused = sorted(zh - set(used))
    print(f"      （提示）字典里未被静态引用的键 {len(unused)} 个 —— 可能是动态拼接，不判失败")


# ============================================================
# 三组：api.ts 的端点必须都有后端路由
# ============================================================
def test_api_ts_endpoints_exist():
    routes = _backend_routes()
    check("解析到足量后端路由（AST 没歪）", len(routes) > 80, f"routes={len(routes)}")
    api = _strip_ts_comments(_read(os.path.join(FE, "api.ts")))
    calls = set()
    for m in re.finditer(r'\b(?:jget|jpost|jput|jdel|jdelete)\(\s*"([^"]+)"', api):
        calls.add(m.group(1))
    for m in re.finditer(r'BASE\s*\+\s*"([^"]+)"', api):
        calls.add(m.group(1))
    check("解析到足量 api.ts 端点（脚本没失效）", len(calls) > 60, f"calls={len(calls)}")
    missing = []
    for c in sorted(calls):
        p = "/api" + _norm_call(c)
        if p == "/api":
            continue
        if p not in routes:
            missing.append((c, p))
    check("api.ts 的端点都能在后端找到路由", not missing,
          "; ".join(f"{c} -> {p}" for c, p in missing[:8]))


# ============================================================
# 四组：非 api.ts 的裸 fetch("/api/...") 也要有路由
# ============================================================
def test_raw_fetch_paths_exist():
    routes = _backend_routes()
    bad = []
    for rel, path in _fe_files(include_mock=False):
        if rel.replace("\\", "/").endswith("api.ts"):
            continue
        src = _strip_ts_comments(_read(path))
        for m in re.finditer(r'fetch\(\s*([^,\)]{0,120})', src):
            lit = m.group(1)
            # 只认字面量路径（含 /api/）；变量拼接的跳过
            lm = re.search(r'"(/api/[^"]+)"', lit)
            if not lm:
                continue
            p = _norm_call(lm.group(1))
            if p not in routes:
                bad.append((rel, lm.group(1)))
    check("非 api.ts 的裸 fetch 路径都能在后端找到路由", not bad,
          "; ".join(f"{r}: {p}" for r, p in bad[:8]))


# ============================================================
# 五组：预览 mock 不得伪造后端不存在的端点
#   这正是 `/api/wallet/status` 能藏几个版本的机制 —— mock 先把它「实现」了。
# ============================================================
# 预览用的**聚合占位**，前端无任何调用方，保留是历史原因（不是 bug）。
# 新增任何一条都必须在这里说明理由，否则测试红。
KNOWN_DEAD_MOCK = {
    "/api/admin": "预览聚合占位 {cron,mcp,plugins,gateways}，前端改为一端点一请求后已无调用方",
    "/api/stats": "预览聚合计数占位 {conversations,memory,bots,...}，同上",
}


def test_mock_does_not_fake_endpoints():
    routes = _backend_routes()
    mock_path = os.path.join(FE, "preview", "mock.ts")
    src = _strip_ts_comments(_read(mock_path))
    cases = set(m.group(1) for m in re.finditer(r'case\s+"(/api/[^"]+)"', src))
    check("解析到足量 mock case（脚本没失效）", len(cases) > 30, f"cases={len(cases)}")
    phantom = sorted(c for c in cases if _norm_call(c) not in routes)
    unexpected = [c for c in phantom if c not in KNOWN_DEAD_MOCK]
    check("mock 没有伪造后端不存在的端点（否则会盖住真实 404）", not unexpected,
          f"未登记的 phantom：{unexpected}")
    # 反向提醒：登记的已知占位若哪天变成真端点，就该从名单里划掉
    now_real = [c for c in KNOWN_DEAD_MOCK if _norm_call(c) in routes]
    check("KNOWN_DEAD_MOCK 里没有已变成真实端点的条目", not now_real,
          f"请从名单移除：{now_real}")


# ============================================================
def main() -> int:
    groups = [
        ("locale 键集合", [test_locale_key_sets_are_identical, test_no_duplicate_keys_within_dict,
                        test_i18n_has_no_zh_fallback_documented]),
        ("使用侧覆盖", [test_all_used_keys_are_defined]),
        ("API 端点", [test_api_ts_endpoints_exist, test_raw_fetch_paths_exist]),
        ("mock 反查", [test_mock_does_not_fake_endpoints]),
    ]
    for title, fns in groups:
        print(f"\n—— {title} ——")
        for fn in fns:
            fn()
    print("\n" + "=" * 60)
    if FAILS:
        print(f"{len(FAILS)} 项失败：")
        for f in FAILS:
            print("  -", f)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
