"""v1.5.34 回归测试：文件查看器/文件列表的图片预览从未接线。

现象：在 Files 面板点 `chart_24h.png`，查看器只显示「二进制文件，不可文本预览（共 33.8K）」。

根因（典型的「做了半截」）：
  后端 v1.5.24 就加了 `/api/workspace/raw`（图片原文端点），`api.ts` 也加了
  `workspaceRawBlob()` —— 但**全仓没有任何调用点**，ChatView 的 `fileModal` 类型里
  声明了 `img_url?: string` 却**从来没有赋值**，渲染分支 `fileModal.img_url ? <img ...>`
  因此永远走不到，图片一律落到 `!is_text` 的「二进制不可预览」兜底。
  也就是说：能力齐全，只是最后一根线没接。

修复：
  - `openFile()` 识别图片扩展名 → `api.workspaceRawBlob()` → objectURL → 写入 `img_url`
    （图片必须走 /raw：/read 是文本通道，1.5MB 上限，且「含 NUL 字节即判二进制」，
     PNG 头部就带 NUL，必然被挡）
  - objectURL 在关闭/切换/卸载时 revoke
  - 文件列表加图片缩略图（同样受 THUMB_LIMIT 限制，避免一次打太多请求）

本文件离线运行：源码文本/AST 断言 + 前后端扩展名白名单交叉校验，不联网、不起服务。
运行：python tests/test_v1534_image_preview.py
"""
import ast
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHATVIEW = ROOT / "frontend/src/views/ChatView.tsx"
APITS = ROOT / "frontend/src/api.ts"
DESKTOP = ROOT / "desktop_app.py"


def src(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig")


def _backend_img_exts() -> set:
    """AST 取 desktop_app._IMG_EXT_MEDIA 的键集合。"""
    tree = ast.parse(src(DESKTOP), filename=str(DESKTOP))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_IMG_EXT_MEDIA":
                    return set(ast.literal_eval(node.value).keys())
    raise AssertionError("desktop_app.py 里找不到 _IMG_EXT_MEDIA")


def _frontend_re():
    """取 ChatView 的 IMG_EXT_RE 正则体并编译（i 标志）。"""
    m = re.search(r"IMG_EXT_RE\s*=\s*/(.+?)/i", src(CHATVIEW))
    assert m, "ChatView.tsx 里找不到 IMG_EXT_RE"
    return re.compile(m.group(1), re.I)


def _frontend_img_exts() -> set:
    """从 ChatView 的 IMG_EXT_RE 正则里解出扩展名集合。"""
    body = _frontend_re().pattern
    # 剥掉正则的 \.( ... )$ 外壳，只留交替分支
    body = re.sub(r"^\\\.\(", "", body)
    body = re.sub(r"\)\$$", "", body)
    out = set()
    for alt in body.split("|"):
        alt = alt.strip()
        if not alt:
            continue
        # jpe?g → jpeg（保留可选字符）/ jpg（去掉可选字符）
        if "?" in alt:
            pre, post = alt.split("?", 1)
            out.add("." + pre + post)
            if len(pre) >= 2:
                out.add("." + pre[:-1] + post)
        else:
            out.add("." + alt)
    return out


# ---------------- 1. 前后端白名单必须一致 ----------------

def test_image_ext_whitelists_match():
    """前端认的图片扩展名必须与后端 /api/workspace/raw 的白名单**完全一致**。

    不一致的后果是静默的：前端认、后端不认 → 415；后端认、前端不认 → 永远走文本通道。"""
    be = _backend_img_exts()
    fe = _frontend_img_exts()
    assert fe == be, f"前端 {sorted(fe)} != 后端 {sorted(be)}"
    # 行为验证：拿真实正则去匹配后端白名单（含大写），比字符串解析更接近运行时
    rx = _frontend_re()
    for e in sorted(be):
        assert rx.search("chart_24h" + e), f"前端正则匹配不到 {e}"
        assert rx.search("CHART_24H" + e.upper()), f"前端正则大小写不敏感失效: {e}"
    assert not rx.search("notes.txt"), "前端正则会误判非图片扩展名"
    assert not rx.search("archive.png.bak"), "前端正则没有锚定结尾"


# ---------------- 2. 接线本身（防「声明了却没赋值」重演） ----------------

def test_workspace_raw_blob_has_a_caller():
    """这是本次事故的核心断言：能力存在但没人调用 = 功能不存在。"""
    callers = [p for p in (ROOT / "frontend/src").rglob("*.ts*")
               if p.name != "api.ts" and "workspaceRawBlob" in src(p)]
    assert callers, ("workspaceRawBlob 在 api.ts 之外没有任何调用点 —— "
                     "图片端点等于没接上界面（v1.5.24 起的实际状态）")


def test_open_file_sets_img_url():
    """openFile 必须真的给 img_url 赋值，否则渲染分支永远走不到。"""
    text = src(CHATVIEW)
    i = text.index("const openFile = async")
    j = text.index("const closeFileModal", i)
    body = text[i:j]
    assert "isImageName" in body, "openFile 没有按扩展名分流图片"
    assert "workspaceRawBlob" in body, "openFile 没有走图片原文端点"
    assert "img_url: url" in body, "openFile 没有给 img_url 赋值（本次事故的原样）"
    assert "createObjectURL" in body, "openFile 没有把 blob 转成可展示的 objectURL"
    # 图片分支必须在文本通道之前，否则 PNG 会先撞 /read 的 NUL 判定
    assert body.index("isImageName") < body.index("workspaceRead"), \
        "图片分流必须在 workspaceRead 之前"


def test_render_uses_img_url_before_binary_fallback():
    """渲染顺序：img_url 分支必须排在 !is_text 兜底之前。"""
    text = src(CHATVIEW)
    assert text.index("fileModal.img_url ? (") < text.index(") : !fileModal.is_text ? ("), \
        "图片分支被排在「二进制不可预览」兜底之后 → 图片永远看不到"


# ---------------- 3. objectURL 生命周期 ----------------

def test_object_url_is_revoked():
    text = src(CHATVIEW)
    assert "revokeObjectURL" in text, "没有任何 revoke → objectURL 泄漏"
    # releaseImg 同时被 closeFileModal 与卸载清理调用
    m = re.search(r"const closeFileModal = \(\) => \{([^}]*)\}", text)
    assert m and "releaseImg" in m.group(1), "关闭查看器时没有释放 objectURL"
    assert re.search(r"useEffect\(\(\) => \(\) => releaseImg\(\), \[\]\)", text), \
        "缺少卸载时的 objectURL 兜底释放"


# ---------------- 4. 列表缩略图 ----------------

def test_file_list_thumb_is_capped():
    text = src(CHATVIEW)
    assert "function FileThumb" in text, "没有缩略图组件"
    assert "THUMB_LIMIT" in text, "缩略图没有数量上限"
    m = re.search(r"THUMB_LIMIT\s*=\s*(\d+)", text)
    assert m and 0 < int(m.group(1)) <= 60, f"THUMB_LIMIT 取值不合理: {m and m.group(1)}"
    assert "thumbNames.has(f.name)" in text, "文件列表没有用缩略图"
    # 缩略图组件必须自己回收 objectURL
    k = text.index("function FileThumb")
    seg = text[k:text.index("type ChatMsg", k)]
    assert "revokeObjectURL" in seg, "缩略图组件没有回收 objectURL"


# ---------------- 5. 错误文案可读（顺带修的） ----------------

def test_error_body_is_unwrapped():
    """jget 把整个 JSON body 塞进 Error.message；查看器要解出 error 字段再展示。"""
    text = src(CHATVIEW)
    assert "const errText = " in text, "缺少错误体解包"
    k = text.index("const errText = ")
    seg = text[k:k + 400]
    assert "JSON.parse" in seg and ".error" in seg, "errText 没有解出 error 字段"


# ---------------- 主入口 ----------------

def main():
    fails, skips = [], []
    cases = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for name, fn in cases:
        try:
            fn()
            print(f"  PASS {name}")
        except Exception as e:
            fails.append((name, e))
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
    ran = len(cases) - len(skips)
    print(f"\n{ran - len(fails)}/{ran} passed")
    if fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
