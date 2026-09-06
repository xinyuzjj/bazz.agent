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
import uuid
import time

from workspace import DB_PATH  # 统一落盘到 workspace（见 src/workspace.py）

_conn = None


def _conn_get():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(os.path.abspath(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _init()
    return _conn


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
    CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conv_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_mem ON memory(key);
    """)
    # 兼容早期库：agents 缺 config 列时补上
    cols = {r[1] for r in c.execute("PRAGMA table_info(agents)").fetchall()}
    if cols and "config" not in cols:
        c.execute("ALTER TABLE agents ADD COLUMN config TEXT DEFAULT '{}'")
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
    mcols = {r[1] for r in c.execute("PRAGMA table_info(messages)").fetchall()}
    if mcols and "reasoning" not in mcols:
        c.execute("ALTER TABLE messages ADD COLUMN reasoning TEXT DEFAULT ''")
        c.commit()
    if mcols and "model" not in mcols:
        c.execute("ALTER TABLE messages ADD COLUMN model TEXT DEFAULT ''")
        c.commit()
    c.commit()


def _uid():
    return uuid.uuid4().hex


# ---------------- 会话 ----------------

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


def set_conversation_provider_snapshot(cid, snap):
    """只在未固定快照时写入一次（首次消息即固定 provider/model，防漂移）。"""
    if not snap:
        return
    _conn_get().execute(
        "UPDATE conversations SET provider_snapshot=? WHERE id=? AND (provider_snapshot IS NULL OR provider_snapshot='{}')",
        (json.dumps(snap, ensure_ascii=False), cid))
    _conn_get().commit()


def set_conversation_persona(cid, persona):
    if not persona:
        return
    _conn_get().execute("UPDATE conversations SET persona=? WHERE id=? AND (persona IS NULL OR persona='')",
                        (persona, cid))
    _conn_get().commit()


def touch_conversation(cid, title=None):
    now = time.time()
    if title:
        _conn_get().execute("UPDATE conversations SET updated_at=?, title=? WHERE id=?", (now, title, cid))
    else:
        _conn_get().execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, cid))
    _conn_get().commit()


def delete_conversation(cid):
    c = _conn_get()
    c.execute("DELETE FROM messages WHERE conv_id=?", (cid,))
    c.execute("DELETE FROM conversations WHERE id=?", (cid,))
    c.commit()


def set_conversation_archived(cid, archived: bool):
    c = _conn_get()
    c.execute("UPDATE conversations SET archived=? WHERE id=?", (1 if archived else 0, cid))
    c.commit()


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


def set_memory(key, value):
    _conn_get().execute(
        "INSERT INTO memory(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, time.time()))
    _conn_get().commit()


def get_memory(key, default=""):
    r = _conn_get().execute("SELECT value FROM memory WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def list_memory():
    rows = _conn_get().execute("SELECT key,value,updated_at FROM memory ORDER BY updated_at DESC").fetchall()
    return [{"key": r["key"], "value": r["value"], "updated_at": r["updated_at"]} for r in rows]


def delete_memory(key):
    _conn_get().execute("DELETE FROM memory WHERE key=?", (key,))
    _conn_get().commit()


if __name__ == "__main__":
    print("conversations:", len(list_conversations()))
    print("memory keys:", len(list_memory()))
