"""币安广场发文台账（本地记账）—— 广场 OpenAPI 官方只发不读，
广场页要展示「Agent 发过的帖子」，唯一真实可靠的来源是本机每次发布的记录。

数据落在运行时工作区 <WORKSPACE>/square_posts.json（首启自动从旧 data/ 迁移），由两个执行入口埋点：
  - agent_core._tool_run_skill    （聊天 / 定时任务里的 Agent 自动发布）→ via=agent
  - skills_client.run_skill       （前端「运行」按钮手动发布）        → via=manual

对外只读 API：GET /api/square/posts 聚合本模块 list_posts / stats / square_key_status。
"""
import json
import os
import random
import re
import shlex
import time

from workspace import SQUARE_POSTS as DATA_FILE  # 统一落盘到 workspace

DATA_DIR = os.path.dirname(DATA_FILE)  # v1.5.12 修复：_write 引用的 DATA_DIR 从未定义，台账写入一直静默失败

SQUARE_SKILL = "square-post"
RICH_SKILL = "square-rich-post"        # 富媒体发文技能（合成 + 转发 square-post）
DAILY_LIMIT = 100                      # Square OpenAPI 每 key 每日发帖上限
KEY_ENV = "BINANCE_SQUARE_OPENAPI_KEY"
KEY_FILE = os.path.join(os.path.expanduser("~"), ".config", "binance-square", "openapi-key")
POST_URL_PREFIX = "https://www.binance.com/square/post/"

_TEXT_MAX = 2000
_LIST_MAX = 500


# ---------------- 底层读写 ----------------

def _read() -> list:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            arr = json.load(f)
        return arr if isinstance(arr, list) else []
    except Exception:
        return []


def _write(arr: list) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(arr, f, ensure_ascii=False, indent=1)


def square_key_status() -> dict:
    """Square OpenAPI key 是否存在（只回掩码，绝不明文）。"""
    raw = ""
    try:
        raw = (os.environ.get(KEY_ENV) or "").strip()
    except Exception:
        raw = ""
    if not raw and os.path.isfile(KEY_FILE):
        try:
            raw = open(KEY_FILE, "r", encoding="utf-8").read().strip()
        except Exception:
            raw = ""
    if not raw:
        return {"present": False, "masked": "", "source": ""}
    src = "env" if os.environ.get(KEY_ENV) else "~/.config/binance-square/openapi-key"
    masked = raw if len(raw) <= 9 else f"{raw[:5]}…{raw[-4:]}"
    return {"present": True, "masked": masked, "source": src}


def save_key(api_key: str) -> dict:
    """保存 Square OpenAPI Key 到本地（0600，与官方 save-key.mjs 一致）。

    优先写入 ~/.config/binance-square/openapi-key；若 BINANCE_SQUARE_OPENAPI_KEY 已存在
    于环境变量，则仅返回状态不覆盖（避免误导用户）。

    安全策略：写入前若已存在 key 且与新 key 不同，先备份为 openapi-key.bak.<ts>
    （仅保留最近 5 份），覆盖事故可立即回滚。
    """
    key = (api_key or "").strip()
    if not key:
        raise ValueError("Square OpenAPI Key 不能为空")
    if os.environ.get(KEY_ENV):
        return {**square_key_status(), "note": "env 已配置 BINANCE_SQUARE_OPENAPI_KEY，本地保存未启用（重启会失效）"}
    try:
        os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True, mode=0o700)
    except Exception as e:
        raise RuntimeError(f"无法创建配置目录: {e}")

    # 已存在 key 且与新 key 不一致 → 先备份再覆盖（覆盖事故可回滚）
    backed_up_to = ""
    try:
        if os.path.isfile(KEY_FILE):
            old = open(KEY_FILE, "r", encoding="utf-8").read().strip()
            if old and old != key:
                bak = _unique_backup_path(KEY_FILE)
                try:
                    os.replace(KEY_FILE, bak)
                    backed_up_to = bak
                    _prune_old_backups(KEY_FILE, keep=5)
                except Exception:
                    # 备份失败则中止保存，绝不让新 key 覆盖旧 key 而无回滚路径
                    raise RuntimeError("备份旧 Key 失败，已拒绝覆盖以保护原凭证")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"读取/备份旧 Key 失败: {e}")

    try:
        with open(KEY_FILE, "w", encoding="utf-8") as f:
            f.write(key + "\n")
        try:
            os.chmod(KEY_FILE, 0o600)
        except Exception:
            pass  # Windows 上 chmod 限制语义不同，但文件本身仍可工作
    except Exception as e:
        raise RuntimeError(f"写入密钥失败: {e}")
    st = square_key_status()
    if backed_up_to:
        st["backed_up_to"] = backed_up_to
    return st


def _prune_old_backups(path: str, keep: int = 5) -> None:
    """只保留最近 keep 份 .bak.<ts> 备份。"""
    try:
        d = os.path.dirname(path)
        base = os.path.basename(path)
        baks = []
        for fn in os.listdir(d):
            if fn.startswith(base + ".bak."):
                full = os.path.join(d, fn)
                try:
                    baks.append((os.path.getmtime(full), full))
                except Exception:
                    continue
        baks.sort(reverse=True)
        for _, p in baks[keep:]:
            try:
                os.remove(p)
            except Exception:
                pass
    except Exception:
        pass


def _unique_backup_path(path: str) -> str:
    """生成不冲突的备份路径：<path>.bak.<ms>_<rand>。"""
    ts_ms = int(time.time() * 1000)
    for _ in range(8):
        cand = f"{path}.bak.{ts_ms}_{random.randint(1000, 9999)}"
        if not os.path.exists(cand):
            return cand
        ts_ms += 1
    return f"{path}.bak.{ts_ms}_{random.randint(10000, 99999)}"


def clear_key() -> dict:
    """断开本地保存的 Square OpenAPI Key（仅删文件，不动环境变量）。

    删除前自动备份到 .bak.<ms>_<rand>，避免误删后无法挽回。
    """
    if not os.path.isfile(KEY_FILE):
        return {**square_key_status(), "note": "本地未保存密钥，无需清理"}
    backed_up_to = ""
    try:
        bak = _unique_backup_path(KEY_FILE)
        os.replace(KEY_FILE, bak)
        backed_up_to = bak
        _prune_old_backups(KEY_FILE, keep=5)
    except Exception as e:
        raise RuntimeError(f"删除密钥失败: {e}")
    st = square_key_status()
    if backed_up_to:
        st["backed_up_to"] = backed_up_to
    return st


# ---------------- 记录模型 ----------------

def _extract_meta(arg_s: str) -> dict:
    """从 cli.mjs 参数串解析 {kind, text, title, media}。解析失败不抛错。"""
    meta = {"kind": "text", "text": "", "title": "", "media": ""}
    try:
        toks = shlex.split(arg_s or "", posix=True)
    except Exception:
        toks = []

    # --text-file/--title-file：正文/标题走文件（长文本规避命令行解析），读回内容供台账记录
    def _read_flag_file(name):
        i = next((i for i, t in enumerate(toks) if t == f"--{name}-file"), -1)
        if i != -1 and i + 1 < len(toks):
            try:
                return open(toks[i + 1], "r", encoding="utf-8").read().strip()
            except Exception:
                return None
        return None

    ftext = _read_flag_file("text")
    if ftext is None:
        ftext = ""
    ftitle = _read_flag_file("title")
    if ftitle is None:
        ftitle = ""

    def flag(name):
        i = next((i for i, t in enumerate(toks) if t == f"--{name}"), -1)
        if i != -1 and i + 1 < len(toks):
            return toks[i + 1]
        for t in toks:
            if t.startswith(f"--{name}="):
                return t.split("=", 1)[1]
        return None

    first = (toks[0] or "").lower() if toks else ""
    if first in ("text", "article", "image", "video"):
        meta["kind"] = "article" if first == "article" else first
    elif first not in ("",) and not first.startswith("--"):
        meta["text"] = arg_s.strip()  # 纯正文快捷方式
        return meta

    text = flag("text")
    if text is None and meta["kind"] == "text" and not first.startswith("--") and len(toks) > 1:
        text = " ".join(toks[1:]).strip()  # 兼容没打引号的多词正文
    meta["text"] = (ftext or text or "").strip()
    meta["title"] = (ftitle or flag("title") or "").strip()
    media = flag("images") or flag("cover") or flag("video") or ""
    meta["media"] = media.strip()
    return meta


def _extract_tags(text: str, title: str) -> list:
    tags = []
    for m in re.finditer(r"#([\w\u4e00-\u9fff][\w\u4e00-\u9fff-]{0,24})", (title or "") + " " + (text or "")):
        t = m.group(1).strip()
        if t and t not in tags:
            tags.append(t)
    return tags[:10]


def append_record(kind: str, text: str, title: str, tags: list, post_id: str,
                  share_url: str, status: str, error: str = "", via: str = "agent",
                  ts: int = None) -> dict:
    """写一条台账。post_id 去重（同一帖子不重复记）。返回该条记录。"""
    arr = _read()
    ts = ts or int(time.time() * 1000)
    post_id = (post_id or "").strip()
    if post_id:
        for exist in arr:
            if exist.get("post_id") == post_id:
                return exist
    rec = {
        "id": f"sq_{int(time.time() * 1000)}{len(arr)}",
        "ts": ts,
        "kind": kind,
        "title": (title or "")[:200],
        "text": (text or "")[:_TEXT_MAX],
        "tags": (tags or [])[:10],
        "media": "",
        "post_id": post_id,
        "share_url": share_url or (f"{POST_URL_PREFIX}{post_id}" if post_id and post_id != "unavailable" else ""),
        "status": status if status in ("posted", "failed") else "posted",
        "error": (error or "")[:400],
        "via": via if via in ("agent", "manual") else "agent",
    }
    arr.insert(0, rec)
    _write(arr[:2000])
    return rec


def delete_records(ids: list[str]) -> dict:
    """按 id 删除台账条目（v1.5.35）。仅当条目存在才会被删，缺失的 id 计入 missing。

    返回：{ deleted: int, missing: list[str] }
    注：失败/成功的删除都允许 —— 接口层不限制，调用方决定语义。
    """
    ids = [i for i in (ids or []) if isinstance(i, str) and i]
    if not ids:
        return {"deleted": 0, "missing": []}
    arr = _read()
    target = set(ids)
    keep = [r for r in arr if r.get("id") not in target]
    deleted = len(arr) - len(keep)
    if deleted:
        _write(keep)
    present = {r.get("id") for r in arr}
    missing = [i for i in ids if i not in present]
    return {"deleted": deleted, "missing": missing}


def record_from_run(skill_name: str, arg_s: str, output: str, exit_code: int,
                    via: str = "agent") -> dict | None:
    """skill 执行结果 → 台账。仅 square-post / square-rich-post 生效；任何异常都静默。"""
    if (skill_name or "") not in (SQUARE_SKILL, RICH_SKILL):
        return None
    try:
        out = output or ""
        failed = exit_code != 0 or "Failed:" in out or "failed:" in out.lower()
        err = ""
        if failed:
            for line in out.splitlines():
                line = line.strip()
                if line.lower().startswith("failed") or line.startswith("Error"):
                    err = line[:300]
                    break
            if not err:
                err = (out or "").strip()[-300:] or f"exit={exit_code}"

        if skill_name == RICH_SKILL:
            # rich 技能：从输出解析产物目录，读回 title/article；发布结果同样取 ID/Link
            m_dir = re.search(r"已合成 → (\S+)", out)
            kind, text, title = "article", "", ""
            if m_dir and os.path.isdir(m_dir.group(1)):
                d = m_dir.group(1)
                try:
                    title = open(os.path.join(d, "title.txt"), encoding="utf-8").read().strip()
                except Exception:
                    title = ""
                try:
                    text = open(os.path.join(d, "article.txt"), encoding="utf-8").read().strip()
                except Exception:
                    text = ""
            else:
                meta = _extract_meta(arg_s)
                kind, text, title = meta["kind"], meta["text"], meta["title"]
        else:
            meta = _extract_meta(arg_s)
            kind, text, title = meta["kind"], meta["text"], meta["title"]

        m_id = re.search(r"\bID:\s*(\S+)", out)
        m_link = re.search(r"\bLink:\s*(\S+)", out)
        post_id = (m_id.group(1).strip() if m_id else "").strip()
        if post_id and post_id.lower() in ("unavailable", "null", "none"):
            post_id = ""
        share_url = (m_link.group(1).strip() if m_link else "").strip()
        if post_id and not share_url:
            share_url = f"{POST_URL_PREFIX}{post_id}"

        return append_record(
            kind=kind, text=text, title=title,
            tags=_extract_tags(text, title),
            post_id=post_id, share_url=share_url,
            status="failed" if failed else "posted",
            error=err if failed else "", via=via,
        )
    except Exception:
        return None


# ---------------- 只读查询 ----------------

def list_posts(limit: int = _LIST_MAX) -> list:
    arr = _read()
    arr.sort(key=lambda r: r.get("ts", 0), reverse=True)
    return arr[:limit]


def stats() -> dict:
    arr = _read()
    now = time.time()
    week = sum(1 for r in arr if r.get("ts", 0) >= now - 7 * 86400 * 1000)
    posted = sum(1 for r in arr if r.get("status") == "posted")
    failed = len(arr) - posted
    # 今日按自然日（本地时区）
    lt = time.localtime()
    day_start = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))) * 1000
    today_local = sum(1 for r in arr if r.get("ts", 0) >= day_start)
    return {
        "total": len(arr),
        "posted": posted,
        "failed": failed,
        "today": today_local,
        "week": week,
        "limit_per_day": DAILY_LIMIT,
    }


def payload() -> dict:
    """GET /api/square/posts 的聚合体。"""
    return {
        "ok": True,
        "posts": list_posts(),
        "stats": stats(),
        "key": square_key_status(),
    }
