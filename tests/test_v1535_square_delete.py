"""v1.5.35 回归测试：广场台账删除（单条 / 批量）+ 端到端 API 验证。

v1.5.35 新增：
- 后端 src/square_store.delete_records（按 id 列表删除，仅本地台账）
- 后端 POST /api/square/posts/delete
- 前端 SquarePostView 单条删除按钮 + 顶部「清空所有失败」批量按钮
- i18n：square.delPost / delPostTip / clearFailed / clearFailedTip / delFail × 2 语言

测试分三层：
1. 单元：delete_records 在隔离 DATA_FILE 下的行为（空、已存在、不存在、混合）
2. AST：前端 SquarePostView 的删除接入（按钮、确认、onDelete、关键 i18n）
3. 端到端：FastAPI TestClient 打端点（避开 Popen 跨进程不确定性），覆盖鉴权 / 单删 / 批删 / 错误体 / 落盘

运行：python tests/test_v1535_square_delete.py
"""
import json
import os
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))          # 让 import desktop_app 能找到根模块
sys.path.insert(0, str(ROOT / "src"))  # 让 import square_store（裸名）能工作


# ========== 1. 单元：delete_records ==========

def _seed_square_store(tmp_dir, posts):
    """把测试数据写到隔离的 DATA_FILE，重置 square_store 模块常量指向它。"""
    import square_store
    backup = (square_store.DATA_FILE, square_store.DATA_DIR)
    square_store.DATA_FILE = str(tmp_dir / "square_posts.json")
    square_store.DATA_DIR = str(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    pathlib.Path(square_store.DATA_FILE).write_text(
        json.dumps(posts, ensure_ascii=False), encoding="utf-8")
    return backup


def _restore_square_store(backup):
    import square_store
    square_store.DATA_FILE, square_store.DATA_DIR = backup


def test_delete_records_basic():
    """基本删除：3 条 → 删 1 → 剩 2。"""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="sq_unit_"))
    posts = [
        {"id": "sq_a", "ts": 1, "status": "failed", "title": "A"},
        {"id": "sq_b", "ts": 2, "status": "posted", "title": "B"},
        {"id": "sq_c", "ts": 3, "status": "failed", "title": "C"},
    ]
    backup = _seed_square_store(tmp, posts)
    try:
        import square_store
        r = square_store.delete_records(["sq_a"])
        assert r == {"deleted": 1, "missing": []}, f"got {r}"
        remaining = square_store._read()
        ids = [p["id"] for p in remaining]
        assert ids == ["sq_b", "sq_c"], f"remaining={ids}"
    finally:
        _restore_square_store(backup); shutil.rmtree(tmp, ignore_errors=True)


def test_delete_records_missing_ids_counted():
    """不存在的 id 不报错，计入 missing。"""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="sq_unit_"))
    posts = [{"id": "sq_x", "ts": 1, "status": "failed"}]
    backup = _seed_square_store(tmp, posts)
    try:
        import square_store
        r = square_store.delete_records(["sq_x", "sq_y"])
        assert r["deleted"] == 1, f"got {r}"
        assert "sq_y" in r["missing"], f"missing={r['missing']}"
        assert square_store._read() == [], "数据未清空"
    finally:
        _restore_square_store(backup); shutil.rmtree(tmp, ignore_errors=True)


def test_delete_records_empty_and_none():
    """空 list / None 都返回 0 deleted，且不动数据。"""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="sq_unit_"))
    backup = _seed_square_store(tmp, [{"id": "sq_1", "ts": 1}])
    try:
        import square_store
        assert square_store.delete_records([]) == {"deleted": 0, "missing": []}
        assert square_store.delete_records(None) == {"deleted": 0, "missing": []}
        assert len(square_store._read()) == 1
    finally:
        _restore_square_store(backup); shutil.rmtree(tmp, ignore_errors=True)


def test_delete_records_filters_invalid_inputs():
    """非字符串 id 静默忽略（None / 数字 / 空串）。"""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="sq_unit_"))
    posts = [{"id": "sq_ok", "ts": 1}]
    backup = _seed_square_store(tmp, posts)
    try:
        import square_store
        r = square_store.delete_records([None, 123, "", "sq_ok"])
        assert r["deleted"] == 1, f"got {r}"
        assert square_store._read() == [], "未清空"
    finally:
        _restore_square_store(backup); shutil.rmtree(tmp, ignore_errors=True)


def test_delete_records_persistence():
    """删除后落盘：磁盘文件确实被改了。"""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="sq_unit_"))
    posts = [{"id": "sq_a", "ts": 1}, {"id": "sq_b", "ts": 2}]
    backup = _seed_square_store(tmp, posts)
    try:
        import square_store
        square_store.delete_records(["sq_a"])
        on_disk = json.loads(pathlib.Path(square_store.DATA_FILE).read_text(encoding="utf-8"))
        assert [p["id"] for p in on_disk] == ["sq_b"], f"disk={on_disk}"
    finally:
        _restore_square_store(backup); shutil.rmtree(tmp, ignore_errors=True)


# ========== 2. AST：前端接入 ==========

def _read_chatview() -> str:
    return (ROOT / "frontend/src/views/SquarePostView.tsx").read_text(encoding="utf-8-sig")


def _slice_arrow_body(text: str, i: int) -> str:
    """从 `const X = useCallback(` 的起始下标 i 起，用大括号配对切出函数体。

    不能靠「下一个顶层语句」当结束标记——语句顺序一变断言就假失败（v1.5.35
    把两个 useCallback 挪到 useT() 之后时就踩过）。这里跳过字符串字面量，
    避免模板串 / JSX 文本里的括号干扰配对。
    """
    k = text.index("=>", i)
    k = text.index("{", k)
    n, p, depth = len(text), k, 0
    while p < n:
        c = text[p]
        if c in "\"'`":
            p += 1
            while p < n:
                if text[p] == "\\":
                    p += 2
                    continue
                if text[p] == c:
                    break
                p += 1
            p += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[k:p + 1]
        p += 1
    return text[k:]


def test_postcard_accepts_on_delete_prop():
    """PostCard 必须接收 onDelete prop。"""
    text = _read_chatview()
    assert "function PostCard({ p, onDelete }" in text, "PostCard 未接收 onDelete"


def test_failed_card_renders_delete_button():
    """失败状态卡片有删除按钮（图标 + onClick）。"""
    text = _read_chatview()
    i = text.index("function PostCard(")
    j = text.find("function ", i + 20)
    if j == -1:
        j = len(text)
    body = text[i:j]
    assert "onDelete" in body, "PostCard 函数体未引用 onDelete"
    assert "{!ok && onDelete" in body or "!ok && onDelete" in body, \
        "失败卡片未挂条件渲染"
    assert "I.Trash" in body, "删除按钮未用 I.Trash 图标"
    assert "onDelete(p.id)" in body, "删除按钮未调 onDelete"


def test_del_post_handler_exists():
    """单条删除：确认弹窗 + 调 API + 失败提示 + 刷新。"""
    text = _read_chatview()
    assert "const delPost" in text, "缺少单条删除函数"
    body = _slice_arrow_body(text, text.index("const delPost"))
    assert "confirmDialog" in body, "delPost 未走确认"
    assert "squarePostsDelete" in body, "delPost 未调 API"
    assert "square.delPostTip" in body, "delPost 未用确认文案"
    assert 'r?.ok === false' in body, "delPost 未判断后端返回 ok=false"
    assert "load(true)" in body, "delPost 删除后未刷新列表"


def test_clear_all_failed_handler_exists():
    """批量清空：确认弹窗 + 只筛 failed + 调 API + 刷新。"""
    text = _read_chatview()
    assert "clearAllFailed" in text, "缺少批量清除函数"
    body = _slice_arrow_body(text, text.index("const clearAllFailed"))
    assert "confirmDialog" in body, "clearAllFailed 未走确认"
    assert 'p.status === "failed"' in body, "clearAllFailed 未筛选 failed"
    assert "squarePostsDelete" in body, "clearAllFailed 未调 API"
    assert "load(true)" in body, "clearAllFailed 删除后未刷新列表"


def test_filter_bar_shows_clear_button():
    """失败筛选时显示「清空所有失败」按钮。"""
    text = _read_chatview()
    assert 'filter === "failed"' in text, "未在失败筛选时显示按钮"
    assert "clearAllFailed" in text


def test_i18n_keys_complete():
    """5 个广场删除键 × 2 语言都要存在（zh + en 各 1 次，共 2 处定义）。"""
    text = (ROOT / "frontend/src/i18n/locales.ts").read_text(encoding="utf-8-sig")
    for k in ("delPost", "delPostTip", "clearFailed", "clearFailedTip", "delFail"):
        cnt = text.count(f'"square.{k}":')
        assert cnt == 2, f"square.{k} 应有 2 处定义（zh + en），实际 {cnt}"


# ========== 3. 端到端：FastAPI TestClient（避开 Popen 跨进程不确定性） ==========

def test_endpoint_delete_e2e():
    """直接 import desktop_app + TestClient 打端点。
    覆盖：单删/批删/不存在 id 计入 missing/错误体/空数组 + 实际落盘 + 鉴权。

    注意：鉴权中间件在 `if AUTH_TOKEN:` 块里用装饰器注册，所以 import 前必须把
    BAZZ_AUTH_TOKEN 写进 os.environ，确保 module-level `AUTH_TOKEN` 非空。
    """
    # 必须在 import desktop_app 之前设置 —— 模块级装饰器 @app.middleware("http") 只在
    # AUTH_TOKEN 非空时注册，否则整个鉴权链都不存在。
    os.environ["BAZZ_AUTH_TOKEN"] = "sq-test-token"
    import desktop_app  # 首次 import，顶层 AUTH_TOKEN = "sq-test-token"
    from fastapi.testclient import TestClient
    client = TestClient(desktop_app.app)

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="sq_tc_"))
    import square_store
    backup = (square_store.DATA_FILE, square_store.DATA_DIR)
    square_store.DATA_FILE = str(tmp / "square_posts.json")
    square_store.DATA_DIR = str(tmp)
    tmp.mkdir(parents=True, exist_ok=True)

    seed = [
        {"id": "sq_e1", "ts": 1, "kind": "text", "title": "E1",
         "text": "t", "tags": [], "post_id": "", "share_url": "",
         "status": "failed", "error": "err1", "via": "agent"},
        {"id": "sq_e2", "ts": 2, "kind": "text", "title": "E2",
         "text": "t", "tags": [], "post_id": "", "share_url": "",
         "status": "failed", "error": "err2", "via": "agent"},
        {"id": "sq_e3", "ts": 3, "kind": "text", "title": "E3",
         "text": "t", "tags": [], "post_id": "", "share_url": "",
         "status": "posted", "error": "", "via": "agent"},
    ]
    pathlib.Path(square_store.DATA_FILE).write_text(
        json.dumps(seed, ensure_ascii=False), encoding="utf-8")

    H = {"X-BAZZ-Token": "sq-test-token"}

    try:
        # 1. 单删一条
        r = client.post("/api/square/posts/delete", json={"ids": ["sq_e1"]}, headers=H)
        assert r.status_code == 200, f"status={r.status_code} body={r.text}"
        body = r.json()
        assert body == {"ok": True, "deleted": 1, "missing": []}, f"body={body}"
        on_disk = json.loads(pathlib.Path(square_store.DATA_FILE).read_text(encoding="utf-8"))
        assert [p["id"] for p in on_disk] == ["sq_e2", "sq_e3"], f"磁盘未落盘: {on_disk}"

        # 2. 批删 + 不存在 id 混入
        r = client.post("/api/square/posts/delete",
                        json={"ids": ["sq_e1", "sq_e2", "sq_e3"]}, headers=H)
        body = r.json()
        assert body["deleted"] == 2, f"deleted={body}"
        assert body["missing"] == ["sq_e1"], f"missing={body['missing']}"

        # 3. 全部删完
        assert json.loads(pathlib.Path(square_store.DATA_FILE).read_text(encoding="utf-8")) == [], \
            "应为 0 条"

        # 4. 错误体：ids 不是数组
        r = client.post("/api/square/posts/delete", json={"ids": "sq_e1"}, headers=H)
        body = r.json()
        assert body.get("ok") is False, f"non-array ids should fail: {body}"

        # 5. 空数组 OK
        r = client.post("/api/square/posts/delete", json={"ids": []}, headers=H)
        assert r.json() == {"ok": True, "deleted": 0, "missing": []}

        # 6. 无 token → 401
        r = client.post("/api/square/posts/delete", json={"ids": ["x"]})
        assert r.status_code == 401, f"no token status={r.status_code}"

        # 7. 错 token → 401
        r = client.post("/api/square/posts/delete",
                        json={"ids": ["x"]}, headers={"X-BAZZ-Token": "wrong"})
        assert r.status_code == 401
    finally:
        square_store.DATA_FILE, square_store.DATA_DIR = backup
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    fails = []
    cases = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for name, fn in cases:
        try:
            fn()
            print(f"  PASS {name}")
        except Exception as e:
            fails.append((name, e))
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
    print(f"\n{len(cases) - len(fails)}/{len(cases)} passed")
    if fails:
        raise SystemExit(1)


if __name__ == "__main__":
    main()