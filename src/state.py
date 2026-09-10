"""BAZZ Agent - 本地状态层（SQLite）

Hermes 风格：
- 多会话持久化（conversations / messages）
- 长期记忆（memory 表：跨会话用户画像 / 学到的偏好）
- 设置（Key 仅存本机 SQLite；明确告知用户不跨设备同步）

数据库文件位于运行时工作区 <WORKSPACE>/state.db（首启自动从旧 .scout.db 迁移）。
"""
import os
import json
import sqlite3
import threading
import uuid
import time

from workspace import DB_PATH  # 统一落盘到 workspace（见 src/workspace.py）

# v1.3.6 并发保护：FastAPI 线程池 + scheduler + 钱包预热线程共用这一条连接，
# 不串行化时多线程写入会交错（execute…execute…commit 互相穿插）导致事务混乱。
_db_lock = threading.RLock()  # 可重入：同线程嵌套调用（ensure_group_member_session → new_conversation）安全

_conn = None


def _conn_get():
    global _conn
    with _db_lock:
        if _conn is None:
            c = sqlite3.connect(os.path.abspath(DB_PATH), check_same_thread=False)
            c.row_factory = sqlite3.Row
            # WAL：读写互不阻塞；busy_timeout：遇锁等待 5s 而非立即报 database is locked
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=5000")
            c.execute("PRAGMA synchronous=NORMAL")
            _conn = c
            _init()
        return _conn


def _serialized(fn):
    """装饰器：写函数全程持锁，防止并发写交错。（仅内存锁，WAL 兜底跨进程场景）"""
    def wrapper(*args, **kwargs):
        with _db_lock:
            return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _init():
    c = _conn_get()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        persona TEXT DEFAULT '',
        kind TEXT DEFAULT 'dm',
        members_json TEXT DEFAULT '',
        provider_snapshot TEXT DEFAULT '{}',
        ctx_summary TEXT DEFAULT '',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        conv_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        tools_json TEXT,
        data_json TEXT,
        reasoning TEXT DEFAULT '',
        model TEXT DEFAULT '',
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS memory (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        kind TEXT DEFAULT 'fact',
        source TEXT DEFAULT 'auto',
        hits INTEGER DEFAULT 0,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS agents (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        title TEXT DEFAULT '',
        description TEXT DEFAULT '',
        avatar TEXT DEFAULT '🤖',
        color TEXT DEFAULT '#F0B90B',
        config TEXT DEFAULT '{}',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS todos (
        id TEXT PRIMARY KEY,
        text TEXT NOT NULL,
        done INTEGER DEFAULT 0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS tracked_orders (
        id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        route TEXT DEFAULT 'exchange',
        direction TEXT DEFAULT 'BULLISH',
        quantity TEXT DEFAULT '0',
        entry REAL DEFAULT 0,
        stop_loss REAL DEFAULT 0,
        take_profit REAL DEFAULT 0,
        status TEXT DEFAULT 'NEW',
        active INTEGER DEFAULT 1,
        note TEXT DEFAULT '',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS radar_tracks (
        id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        stage TEXT NOT NULL,
        direction TEXT DEFAULT 'LONG',
        found_price REAL NOT NULL,
        found_score INTEGER DEFAULT 0,
        reasons_json TEXT DEFAULT '[]',
        status TEXT DEFAULT 'pending',
        outcome TEXT DEFAULT '',
        max_gain_pct REAL DEFAULT 0.0,
        max_drop_pct REAL DEFAULT 0.0,
        peak_price REAL DEFAULT 0,
        trough_price REAL DEFAULT 0,
        last_price REAL DEFAULT 0,
        outcome_price REAL DEFAULT 0,
        review TEXT DEFAULT '',
        holding INTEGER DEFAULT 0,
        hold_ext REAL DEFAULT 0,
        found_at REAL NOT NULL,
        closed_at REAL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conv_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_mem ON memory(key);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_radar_tracks_pending ON radar_tracks(symbol) WHERE status='pending';
    CREATE INDEX IF NOT EXISTS idx_radar_tracks_hist ON radar_tracks(status, found_at);
    """)
    # 兼容早期库：agents 缺 config 列时补上
    cols = {r[1] for r in c.execute("PRAGMA table_info(agents)").fetchall()}
    if cols and "config" not in cols:
        c.execute("ALTER TABLE agents ADD COLUMN config TEXT DEFAULT '{}'")
        c.commit()
    # v1.5.7：radar_tracks 缺 direction 列时补上（旧记录默认 LONG）
    rtcols = {r[1] for r in c.execute("PRAGMA table_info(radar_tracks)").fetchall()}
    if rtcols and "direction" not in rtcols:
        c.execute("ALTER TABLE radar_tracks ADD COLUMN direction TEXT DEFAULT 'LONG'")
        c.commit()
    # v1.5.8：radar_tracks 缺 review 列时补上（失败复盘记录）
    if rtcols and "review" not in rtcols:
        c.execute("ALTER TABLE radar_tracks ADD COLUMN review TEXT DEFAULT ''")
        c.commit()
    # v1.5.8：radar_tracks 缺 holding/hold_ext 列时补上（达标继续持有 + 移动止盈）
    if rtcols and "holding" not in rtcols:
        c.execute("ALTER TABLE radar_tracks ADD COLUMN holding INTEGER DEFAULT 0")
        c.commit()
    if rtcols and "hold_ext" not in rtcols:
        c.execute("ALTER TABLE radar_tracks ADD COLUMN hold_ext REAL DEFAULT 0")
        c.commit()
    ccols = {r[1] for r in c.execute("PRAGMA table_info(conversations)").fetchall()}
    if ccols and "persona" not in ccols:
        c.execute("ALTER TABLE conversations ADD COLUMN persona TEXT DEFAULT ''")
        c.commit()
    if ccols and "provider_snapshot" not in ccols:
        c.execute("ALTER TABLE conversations ADD COLUMN provider_snapshot TEXT DEFAULT '{}'")
        c.commit()
    if ccols and "kind" not in ccols:
        c.execute("ALTER TABLE conversations ADD COLUMN kind TEXT DEFAULT 'dm'")
        c.commit()
    if ccols and "members_json" not in ccols:
        c.execute("ALTER TABLE conversations ADD COLUMN members_json TEXT DEFAULT ''")
        c.commit()
    if ccols and "archived" not in ccols:
        c.execute("ALTER TABLE conversations ADD COLUMN archived INTEGER DEFAULT 0")
        c.commit()
    if ccols and "ctx_summary" not in ccols:
        # v1.4.2 持久化滚动摘要：长对话旧历史压缩产物落库，跨轮复用不重烧
        c.execute("ALTER TABLE conversations ADD COLUMN ctx_summary TEXT DEFAULT ''")
        c.commit()
    mcols = {r[1] for r in c.execute("PRAGMA table_info(messages)").fetchall()}
    if mcols and "reasoning" not in mcols:
        c.execute("ALTER TABLE messages ADD COLUMN reasoning TEXT DEFAULT ''")
        c.commit()
    if mcols and "model" not in mcols:
        c.execute("ALTER TABLE messages ADD COLUMN model TEXT DEFAULT ''")
        c.commit()
    # 记忆分类升级（v1.4.1）：旧库补 kind/source/hits 列，旧记忆默认 fact/auto
    ycols = {r[1] for r in c.execute("PRAGMA table_info(memory)").fetchall()}
    if ycols and "kind" not in ycols:
        c.execute("ALTER TABLE memory ADD COLUMN kind TEXT DEFAULT 'fact'")
        c.commit()
    if ycols and "source" not in ycols:
        c.execute("ALTER TABLE memory ADD COLUMN source TEXT DEFAULT 'auto'")
        c.commit()
    if ycols and "hits" not in ycols:
        c.execute("ALTER TABLE memory ADD COLUMN hits INTEGER DEFAULT 0")
        c.commit()
    c.commit()


def _uid():
    return uuid.uuid4().hex


# ---------------- 会话 ----------------

@_serialized
def new_conversation(title="新对话", persona="", provider_snapshot=None, kind="dm", members=None):
    cid = _uid()
    now = time.time()
    snap = json.dumps(provider_snapshot or {}, ensure_ascii=False)
    mjs = json.dumps(members or [], ensure_ascii=False)
    _conn_get().execute(
        "INSERT INTO conversations (id,title,persona,kind,members_json,provider_snapshot,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (cid, title, persona, kind, mjs, snap, now, now))
    _conn_get().commit()
    return cid


def _snap_of(r):
    try:
        return json.loads(r["provider_snapshot"] or "{}")
    except Exception:
        return {}


def _members_of(r):
    try:
        m = json.loads(r["members_json"] or "[]")
        return m if isinstance(m, list) else []
    except Exception:
        return []


def _conv_out(r):
    d = dict(r)
    d["kind"] = r["kind"] or "dm"
    d["members"] = _members_of(r)
    d["provider_snapshot"] = _snap_of(r)
    try:
        d["archived"] = bool(r["archived"])
    except (KeyError, IndexError):
        d["archived"] = False
    return d
    return d


def list_conversations(include_archived: bool = False):
    sql = ("SELECT id,title,persona,kind,members_json,provider_snapshot,archived,created_at,updated_at "
           "FROM conversations")
    if not include_archived:
        sql += " WHERE COALESCE(archived,0)=0"
    sql += " ORDER BY updated_at DESC LIMIT 80"
    rows = _conn_get().execute(sql).fetchall()
    out = []
    for r in rows:
        last = _conn_get().execute(
            "SELECT content FROM messages WHERE conv_id=? ORDER BY created_at DESC LIMIT 1", (r["id"],)).fetchone()
        d = _conv_out(r)
        d["preview"] = (last["content"][:60] if last else "")
        out.append(d)
    return out


def get_conversation(cid):
    r = _conn_get().execute(
        "SELECT id,title,persona,kind,members_json,provider_snapshot,created_at,updated_at FROM conversations WHERE id=?", (cid,)).fetchone()
    return _conv_out(r) if r else None


def _msg_snippet(content, q, width=72):
    """在消息正文里定位关键词首次出现处，返回带省略号的上下文片段（无命中返回开头截断）。"""
    text = " ".join(str(content or "").split())
    if not text:
        return ""
    i = text.lower().find(str(q).lower())
    if i < 0:
        return text[:width] + ("…" if len(text) > width else "")
    start = max(0, i - 20)
    seg = text[start:start + width]
    return ("…" if start > 0 else "") + seg + ("…" if start + width < len(text) else "")


def search_conversations(q, limit=30):
    """会话搜索（v1.4.3，学习 Hermes session_search）：LIKE 匹配标题 + 全部消息正文，
    返回会话卡片（含命中片段 hits 片段与命中条数）。room 房间不参与（另有入口管理）。"""
    q = str(q or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    c = _conn_get()
    hits = {}  # cid -> {"snippet": 最近一条命中片段, "n": 命中消息条数}
    try:
        mrows = c.execute(
            "SELECT conv_id, content FROM messages WHERE content LIKE ? "
            "ORDER BY created_at DESC LIMIT 500", (like,)).fetchall()
        trows = c.execute(
            "SELECT id FROM conversations WHERE title LIKE ? LIMIT 50", (like,)).fetchall()
    except Exception:
        return []
    for r in mrows:
        h = hits.setdefault(r["conv_id"], {"snippet": "", "n": 0})
        h["n"] += 1
        if not h["snippet"]:
            h["snippet"] = _msg_snippet(r["content"], q)
    for r in trows:
        hits.setdefault(r["id"], {"snippet": "", "n": 0})
    out = []
    for cid, h in hits.items():
        r = c.execute(
            "SELECT id,title,persona,kind,members_json,provider_snapshot,archived,created_at,updated_at "
            "FROM conversations WHERE id=?", (cid,)).fetchone()
        if not r or (r["kind"] or "dm") == "room":
            continue
        d = _conv_out(r)
        d["preview"] = h["snippet"]
        d["hits"] = h["n"]
        if not d["preview"]:
            last = c.execute(
                "SELECT content FROM messages WHERE conv_id=? ORDER BY created_at DESC LIMIT 1",
                (cid,)).fetchone()
            d["preview"] = (last["content"][:60] if last else "")
        out.append(d)
    out.sort(key=lambda d: d.get("updated_at", 0), reverse=True)
    return out[:limit]


def list_rooms():
    """群聊房间（Hermes: shared ordered room log, one conversation kind='room'）。"""
    rows = _conn_get().execute(
        "SELECT id,title,persona,kind,members_json,provider_snapshot,created_at,updated_at "
        "FROM conversations WHERE kind='room' ORDER BY updated_at DESC LIMIT 50").fetchall()
    out = []
    for r in rows:
        last = _conn_get().execute(
            "SELECT content FROM messages WHERE conv_id=? ORDER BY created_at DESC LIMIT 1", (r["id"],)).fetchone()
        d = _conv_out(r)
        d["preview"] = (last["content"][:60] if last else "")
        out.append(d)
    return out


def room_exists(name):
    r = _conn_get().execute("SELECT id FROM conversations WHERE kind='room' AND title=?", (name,)).fetchone()
    return r["id"] if r else None


def get_conv_summary(cid):
    """会话的持久化滚动摘要（v1.4.2 上下文压缩；无则空串）。"""
    r = _conn_get().execute("SELECT ctx_summary FROM conversations WHERE id=?", (cid,)).fetchone()
    return (r["ctx_summary"] or "") if r else ""


@_serialized
def set_conv_summary(cid, summary):
    _conn_get().execute("UPDATE conversations SET ctx_summary=? WHERE id=?",
                        (str(summary or "")[:2000], cid))
    _conn_get().commit()


@_serialized
def update_room_members(rid, members):
    _conn_get().execute("UPDATE conversations SET members_json=? WHERE id=?",
                        (json.dumps(members, ensure_ascii=False), rid))
    _conn_get().commit()


def ensure_group_member_session(bot_name, room_title):
    """每个 bot 在群聊里拥有自己的独立会话（Hermes: per-member session, title 'Group: X'）。
    该会话只以该 bot 视角记录：自己发言=assistant，其它成员/用户=user 且带 [名字] 前缀。
    这样各 bot 的记录互不混写。
    """
    title = f"Group: {room_title}"
    r = _conn_get().execute(
        "SELECT id FROM conversations WHERE kind='group' AND persona=? AND title=? LIMIT 1",
        (bot_name, title)).fetchone()
    if r:
        return r["id"]
    return new_conversation(title=title, persona=bot_name, kind="group")


def last_persona_conv(persona: str, kind=None):
    """persona 最近更新的会话 id（无则 None）。kind='group' 用于群聊成员会话检索。"""
    if kind:
        r = _conn_get().execute(
            "SELECT id FROM conversations WHERE persona=? AND kind=? ORDER BY updated_at DESC LIMIT 1",
            (persona, kind)).fetchone()
        return r["id"] if r else None
    r = _conn_get().execute(
        "SELECT id FROM conversations WHERE persona=? AND kind NOT IN ('group','room') "
        "ORDER BY updated_at DESC LIMIT 1", (persona,)).fetchone()
    return r["id"] if r else None


@_serialized
def set_conversation_provider_snapshot(cid, snap):
    """只在未固定快照时写入一次（首次消息即固定 provider/model，防漂移）。"""
    if not snap:
        return
    _conn_get().execute(
        "UPDATE conversations SET provider_snapshot=? WHERE id=? AND (provider_snapshot IS NULL OR provider_snapshot='{}')",
        (json.dumps(snap, ensure_ascii=False), cid))
    _conn_get().commit()


@_serialized
def set_conversation_persona(cid, persona):
    if not persona:
        return
    _conn_get().execute("UPDATE conversations SET persona=? WHERE id=? AND (persona IS NULL OR persona='')",
                        (persona, cid))
    _conn_get().commit()


@_serialized
def touch_conversation(cid, title=None):
    now = time.time()
    if title:
        _conn_get().execute("UPDATE conversations SET updated_at=?, title=? WHERE id=?", (now, title, cid))
    else:
        _conn_get().execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, cid))
    _conn_get().commit()


@_serialized
def delete_conversation(cid):
    c = _conn_get()
    c.execute("DELETE FROM messages WHERE conv_id=?", (cid,))
    c.execute("DELETE FROM conversations WHERE id=?", (cid,))
    c.commit()


@_serialized
def set_conversation_archived(cid, archived: bool):
    c = _conn_get()
    c.execute("UPDATE conversations SET archived=? WHERE id=?", (1 if archived else 0, cid))
    c.commit()


@_serialized
def add_message(conv_id, role, content, tools=None, data=None, reasoning="", model=""):
    mid = _uid()
    _conn_get().execute(
        "INSERT INTO messages (id,conv_id,role,content,tools_json,data_json,reasoning,model,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (mid, conv_id, role, content, json.dumps(tools or [], ensure_ascii=False),
         json.dumps(data or {}, ensure_ascii=False), reasoning or "", model or "", time.time()))
    _conn_get().commit()
    return mid


def get_messages(cid):
    rows = _conn_get().execute(
        "SELECT role,content,tools_json,data_json,reasoning,model FROM messages WHERE conv_id=? ORDER BY created_at ASC", (cid,)).fetchall()
    out = []
    for r in rows:
        try:
            data = json.loads(r["data_json"] or "{}")
        except Exception:
            data = {}
        out.append({"role": r["role"], "content": r["content"],
                    "tools": json.loads(r["tools_json"] or "[]"),
                    "persona": data.get("persona") or "",
                    "reasoning": r["reasoning"] or "",
                    "model": r["model"] or ""})
    return out


# ---------------- Regenerate / Edit（回滚一段，供前端“重新生成/编辑重发”） ----------------

@_serialized
def rollback_last_turn(cid, new_user_text=None, upto_user_index=None):
    """回滚会话到某条用户消息为止（含）：删除其后全部消息，可选改写该条用户消息文本
    （编辑重发）。upto_user_index 为空 = 回滚到最后一条 user 及其尾随 assistant。
    返回被保留的 user 文本（供重新生成）。消息按 created_at ASC 排序，索引与前端 1:1。"""
    c = _conn_get()
    rows = c.execute(
        "SELECT id, role, content FROM messages WHERE conv_id=? ORDER BY created_at ASC", (cid,)).fetchall()
    if not rows:
        return ""
    # 定位“目标用户消息”
    target_idx = None
    if upto_user_index is not None:
        if 0 <= upto_user_index < len(rows) and rows[upto_user_index]["role"] == "user":
            target_idx = upto_user_index
    if target_idx is None:
        for i in range(len(rows) - 1, -1, -1):
            if rows[i]["role"] == "user":
                target_idx = i
                break
    if target_idx is None:
        return ""
    user_text = rows[target_idx]["content"]
    if new_user_text is not None:
        c.execute("UPDATE messages SET content=? WHERE id=?", (new_user_text, rows[target_idx]["id"]))
        user_text = new_user_text
    # 删除目标之后的所有消息
    for r in rows[target_idx + 1:]:
        c.execute("DELETE FROM messages WHERE id=?", (r["id"],))
    c.commit()
    return user_text


# ---------------- 设置（本地持久化，仅本机） ----------------

@_serialized
def set_setting(key, value):
    _conn_get().execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value))
    _conn_get().commit()


def get_setting(key, default=""):
    r = _conn_get().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def get_all_settings():
    rows = _conn_get().execute("SELECT key,value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


# ---------------- Agents / Bots 档案（Hermes: one chat per agent） ----------------
# v2: 档案已迁移为「文件式 Bot 包」（.agents/bots/<slug>/bot.md），
#     本组函数薄委托 bot_host，保持旧签名与 shape，下游（room/desktop_app）零改动。
#     sqlite agents 表仅保留兼容（不再写入）。

def _agent_out(r):
    d = dict(r)
    try:
        cfg = json.loads(d.get("config") or "{}")
    except Exception:
        cfg = {}
    d["config"] = cfg
    return d


def list_agents():
    from bot_host import list_bots
    return list_bots()


def get_agent(aid):
    from bot_host import get_bot
    return get_bot(aid)


def get_agent_by_name(name):
    from bot_host import get_bot_by_name
    return get_bot_by_name(name)


def upsert_agent(aid=None, name="", title="", description="", avatar="🤖", color="#F0B90B",
                 config=None):
    from bot_host import upsert_bot
    return upsert_bot(aid=aid, name=name, title=title, description=description,
                      avatar=avatar, color=color, config=config)


def delete_agent(aid):
    from bot_host import delete_bot
    return delete_bot(aid)


def seed_default_agents():
    from bot_host import seed_default_bots
    seed_default_bots()


def bot_activity() -> list:
    """每个 Agent persona 专属会话的最后活动（assistant 消息时间戳），供前端算未读徽标。"""
    rows = _conn_get().execute(
        "SELECT c.persona AS persona, MAX(m.created_at) AS last_ts FROM messages m "
        "JOIN conversations c ON m.conv_id=c.id "
        "WHERE c.persona<>'' AND c.persona<>'__group__' AND m.role='assistant' "
        "GROUP BY c.persona").fetchall()
    return [{"persona": r["persona"], "last_ts": r["last_ts"]} for r in rows]


def last_persona_conv(persona: str):
    """persona 最近更新的会话 id（无则 None）。"""
    r = _conn_get().execute(
        "SELECT id FROM conversations WHERE persona=? ORDER BY updated_at DESC LIMIT 1", (persona,)).fetchone()
    return r["id"] if r else None


# ---------------- 长期记忆（跨会话） ----------------
# v1.4.1 对齐 Hermes「精选式记忆」：kind 分类（pref/fact/event）+ source 来源
# （auto/agent/manual）+ hits 注入命中数（活跃度）；value 截断 500 字符防 prompt 膨胀。

_MEM_VALUE_MAX = 500


@_serialized
def set_memory(key, value, kind="fact", source="auto"):
    v = str(value or "")[:_MEM_VALUE_MAX]
    k = str(kind or "fact").lower()
    if k not in ("pref", "fact", "event"):
        k = "fact"
    _conn_get().execute(
        "INSERT INTO memory(key,value,kind,source,hits,updated_at) VALUES(?,?,?,?,0,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, kind=excluded.kind, "
        "source=excluded.source, updated_at=excluded.updated_at",
        (key, v, k, str(source or "auto"), time.time()))
    _conn_get().commit()


def get_memory(key, default=""):
    r = _conn_get().execute("SELECT value FROM memory WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def list_memory():
    rows = _conn_get().execute(
        "SELECT key,value,kind,source,hits,updated_at FROM memory ORDER BY updated_at DESC").fetchall()
    return [{"key": r["key"], "value": r["value"], "kind": r["kind"] or "fact",
             "source": r["source"] or "auto", "hits": r["hits"] or 0,
             "updated_at": r["updated_at"]} for r in rows]


def search_memory(query, limit=10):
    """简易相关性检索：LIKE 全词匹配 key/value，按 hits 与更新时间排序（无向量库，够用）。"""
    q = str(query or "").strip()
    if not q:
        return []
    rows = _conn_get().execute(
        "SELECT key,value,kind,source,hits,updated_at FROM memory "
        "WHERE value LIKE ? OR key LIKE ? "
        "ORDER BY hits DESC, updated_at DESC LIMIT ?",
        (f"%{q}%", f"%{q}%", int(limit))).fetchall()
    return [{"key": r["key"], "value": r["value"], "kind": r["kind"] or "fact",
             "source": r["source"] or "auto", "hits": r["hits"] or 0,
             "updated_at": r["updated_at"]} for r in rows]


@_serialized
def bump_memory_hits(keys):
    """注入命中 +1（活跃度追踪）。keys 为空跳过；未知 key 忽略。"""
    ks = [str(k) for k in (keys or []) if k]
    if not ks:
        return
    c = _conn_get()
    c.executemany("UPDATE memory SET hits = COALESCE(hits,0) + 1 WHERE key=?", [(k,) for k in ks])
    c.commit()


@_serialized
def delete_memory(key):
    _conn_get().execute("DELETE FROM memory WHERE key=?", (key,))
    _conn_get().commit()


# ---------------- Agent 任务清单（v1.4.4，学习 Hermes todo_tool） ----------------

def _todo_out(r):
    d = dict(r)
    d["done"] = bool(r["done"] or 0)
    return d


def list_todos(open_only=False):
    """任务清单（open_only=True 仅未完成，按创建顺序）。"""
    sql = "SELECT id,text,done,created_at,updated_at FROM todos"
    if open_only:
        sql += " WHERE COALESCE(done,0)=0"
    sql += " ORDER BY created_at ASC LIMIT 50"
    rows = _conn_get().execute(sql).fetchall()
    return [_todo_out(r) for r in rows]


@_serialized
def add_todo(text):
    """新增任务（去重：与现有未完成任务同文本则原样返回）。"""
    text = str(text or "").strip()[:200]
    if not text:
        return ""
    r = _conn_get().execute(
        "SELECT id FROM todos WHERE COALESCE(done,0)=0 AND text=?", (text,)).fetchone()
    if r:
        return r["id"]
    tid = _uid()
    now = time.time()
    _conn_get().execute(
        "INSERT INTO todos (id,text,done,created_at,updated_at) VALUES (?,?,0,?,?)",
        (tid, text, now, now))
    _conn_get().commit()
    return tid


@_serialized
def todo_toggle(tid, done=None):
    """勾/取消勾（done=None 时翻转）。"""
    if done is None:
        _conn_get().execute("UPDATE todos SET done=1-COALESCE(done,0), updated_at=? WHERE id=?",
                            (time.time(), tid))
    else:
        _conn_get().execute("UPDATE todos SET done=?, updated_at=? WHERE id=?",
                            (1 if done else 0, time.time(), tid))
    _conn_get().commit()


@_serialized
def todo_toggle_by_text(text, done=True):
    """按文本勾选（供 Agent 不带 id 的快捷路径，命中最新一条未完成）。"""
    text = str(text or "").strip()[:200]
    if not text:
        return False
    cur = _conn_get().execute(
        "SELECT id FROM todos WHERE COALESCE(done,0)=0 AND text=? ORDER BY created_at DESC LIMIT 1",
        (text,)).fetchone()
    if not cur:
        return False
    _conn_get().execute("UPDATE todos SET done=?, updated_at=? WHERE id=?",
                        (1 if done else 0, time.time(), cur["id"]))
    _conn_get().commit()
    return True


@_serialized
def remove_todo(tid):
    _conn_get().execute("DELETE FROM todos WHERE id=?", (tid,))
    _conn_get().commit()


@_serialized
def clear_done_todos():
    n = _conn_get().execute("DELETE FROM todos WHERE COALESCE(done,0)=1").rowcount
    _conn_get().commit()
    return n


# ---------------- 订单跟踪（v1.4.0：下单后状态跟踪 + SL/TP 提醒） ----------------

def _track_out(r):
    d = dict(r)
    d["active"] = bool(r["active"] or 0)
    return d


@_serialized
def track_add(order_id: str, symbol: str, route: str = "exchange", direction: str = "BULLISH",
              quantity: str = "0", entry: float = 0, stop_loss: float = 0,
              take_profit: float = 0, status: str = "NEW", note: str = "") -> str:
    tid = _uid()
    now = time.time()
    _conn_get().execute(
        "INSERT INTO tracked_orders (id,order_id,symbol,route,direction,quantity,entry,stop_loss,"
        "take_profit,status,active,note,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?,?)",
        (tid, str(order_id), str(symbol).upper(), route or "exchange", direction or "BULLISH",
         str(quantity or "0"), float(entry or 0), float(stop_loss or 0), float(take_profit or 0),
         status or "NEW", note or "", now, now))
    _conn_get().commit()
    return tid


def track_list() -> list:
    rows = _conn_get().execute(
        "SELECT * FROM tracked_orders ORDER BY created_at DESC LIMIT 100").fetchall()
    return [_track_out(r) for r in rows]


@_serialized
def track_set_status(tid: str, status: str) -> None:
    _conn_get().execute("UPDATE tracked_orders SET status=?, updated_at=? WHERE id=?",
                        (status, time.time(), tid))
    _conn_get().commit()


@_serialized
def track_set_active(tid: str, active: bool) -> None:
    _conn_get().execute("UPDATE tracked_orders SET active=?, updated_at=? WHERE id=?",
                        (1 if active else 0, time.time(), tid))
    _conn_get().commit()


@_serialized
def track_remove(tid: str) -> None:
    _conn_get().execute("DELETE FROM tracked_orders WHERE id=?", (tid,))
    _conn_get().commit()


# ---------------- 妖币追踪（v1.5.2：启动前发现 → 后续暴涨/暴跌结局验证） ----------------

def _radar_track_out(r):
    d = dict(r)
    d["reasons"] = json.loads(d.get("reasons_json") or "[]")
    d.pop("reasons_json", None)
    return d


@_serialized
def radar_track_add(symbol: str, stage: str, found_price: float, found_score: int = 0,
                    reasons=None, found_at: float = 0, direction: str = "LONG") -> str:
    """登记一条启动前发现记录；同币已在跟踪中（pending）则幂等返回已有 id。
    direction: LONG=做多 / SHORT=做空（决定结局语义，见 radar_tracker._judge_outcome）。"""
    now = time.time()
    exist = _conn_get().execute(
        "SELECT id FROM radar_tracks WHERE symbol=? AND status='pending'",
        (str(symbol).upper(),)).fetchone()
    if exist:
        return exist["id"]
    tid = _uid()
    _conn_get().execute(
        "INSERT INTO radar_tracks (id,symbol,stage,direction,found_price,found_score,reasons_json,"
        "status,outcome,max_gain_pct,max_drop_pct,peak_price,trough_price,last_price,"
        "outcome_price,found_at,closed_at,updated_at) VALUES (?,?,?,?,?,?,?, 'pending','',0,0,0,0,0,0,?,NULL,?)",
        (tid, str(symbol).upper(), stage or "IGNITION",
         "SHORT" if str(direction).upper() == "SHORT" else "LONG",
         float(found_price or 0),
         int(found_score or 0), json.dumps(reasons or [], ensure_ascii=False),
         float(found_at or now), now))
    _conn_get().commit()
    return tid


@_serialized
def radar_track_progress(tid: str, price: float):
    """跟踪线程每轮更新：last/peak/trough 与自发现价最大涨跌幅（正数 %）。
    v1.5.8 持有模式：holding=1 时同步维护 hold_ext（做多=持有期最高价 / 做空=最低价）。返回本轮判定上下文。"""
    now = time.time()
    row = _conn_get().execute(
        "SELECT direction, holding, hold_ext, found_price, found_at, max_gain_pct, max_drop_pct, peak_price, trough_price "
        "FROM radar_tracks WHERE id=? AND status='pending'", (tid,)).fetchone()
    if not row or price <= 0:
        return None
    found = row["found_price"] or 0
    if found <= 0:
        return None
    gain = (price / found - 1.0) * 100.0
    drop = (1.0 - price / found) * 100.0
    mg = max(float(row["max_gain_pct"] or 0), gain)
    md = max(float(row["max_drop_pct"] or 0), drop)
    peak = max(float(row["peak_price"] or 0), price)
    trough = price if float(row["trough_price"] or 0) <= 0 else min(float(row["trough_price"]), price)
    holding = int(row["holding"] or 0)
    hold_ext = float(row["hold_ext"] or 0)
    if holding:
        if (row["direction"] or "LONG") == "SHORT":
            hold_ext = price if hold_ext <= 0 else min(hold_ext, price)
        else:
            hold_ext = max(hold_ext, price)
        _conn_get().execute(
            "UPDATE radar_tracks SET last_price=?, peak_price=?, trough_price=?, "
            "max_gain_pct=?, max_drop_pct=?, hold_ext=?, updated_at=? WHERE id=?",
            (price, peak, trough, mg, md, hold_ext, now, tid))
    else:
        _conn_get().execute(
            "UPDATE radar_tracks SET last_price=?, peak_price=?, trough_price=?, "
            "max_gain_pct=?, max_drop_pct=?, updated_at=? WHERE id=?",
            (price, peak, trough, mg, md, now, tid))
    _conn_get().commit()
    return {"gain": gain, "drop": drop, "max_gain": mg, "max_drop": md,
            "found": found, "found_at": row["found_at"],
            "holding": holding, "hold_ext": hold_ext}


@_serialized
def radar_track_hold_start(tid: str, price: float) -> None:
    """v1.5.8：达标后判定继续持有 → 进入移动止盈模式（hold_ext 记持有期极值）。"""
    _conn_get().execute(
        "UPDATE radar_tracks SET holding=1, hold_ext=?, updated_at=? WHERE id=? AND status='pending'",
        (float(price or 0), time.time(), tid))
    _conn_get().commit()


@_serialized
def radar_track_close(tid: str, outcome: str, price: float, review: str = "") -> bool:
    """终态关单：moon|dump|expired。关单后不再被跟踪。review=失败复盘（v1.5.8，dump 时写入）。"""
    now = time.time()
    cur = _conn_get().execute(
        "UPDATE radar_tracks SET status='closed', outcome=?, outcome_price=?, review=?, closed_at=?, updated_at=? "
        "WHERE id=? AND status='pending'",
        (outcome, float(price or 0), str(review or ""), now, now, tid))
    _conn_get().commit()
    return cur.rowcount > 0


@_serialized
def radar_track_set_review(tid: str, review: str) -> None:
    """v1.5.8：写入/更新失败复盘文本（dump 关单后调用）。"""
    _conn_get().execute("UPDATE radar_tracks SET review=? WHERE id=?",
                        (str(review or ""), tid))
    _conn_get().commit()


def radar_tracks_list(status: str = "") -> list:
    q = "SELECT * FROM radar_tracks"
    args = ()
    if status in ("pending", "closed"):
        q += " WHERE status=?"
        args = (status,)
    q += " ORDER BY found_at DESC LIMIT 200"
    return [_radar_track_out(r) for r in _conn_get().execute(q, args).fetchall()]


def radar_tracks_stats() -> dict:
    rows = _conn_get().execute(
        "SELECT status, outcome, COUNT(*) AS n FROM radar_tracks GROUP BY status, outcome").fetchall()
    st = {"total": 0, "pending": 0, "moon": 0, "dump": 0, "expired": 0}
    for r in rows:
        st["total"] += r["n"]
        if r["status"] == "pending":
            st["pending"] += r["n"]
        elif r["outcome"] in ("moon", "dump", "expired"):
            st[r["outcome"]] += r["n"]
    return st


if __name__ == "__main__":
    print("conversations:", len(list_conversations()))
    print("memory keys:", len(list_memory()))
