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

# 敏感设置加解密（F15）：flat 布局下 src/secrets.py 遮蔽 stdlib secrets，
# 包布局（src.state）时兜底走 src.secrets。
try:
    from secrets import encrypt_secret, decrypt_secret
except Exception:
    from src.secrets import encrypt_secret, decrypt_secret

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
    CREATE TABLE IF NOT EXISTS paper_orders (
        id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        market TEXT DEFAULT 'futures',
        direction TEXT DEFAULT 'long',
        entry REAL NOT NULL,
        zone_lo REAL DEFAULT 0,
        zone_hi REAL DEFAULT 0,
        stop REAL DEFAULT 0,
        tp REAL DEFAULT 0,
        leverage INTEGER DEFAULT 10,
        margin REAL DEFAULT 100,
        status TEXT DEFAULT 'pending',
        last_price REAL DEFAULT 0,
        filled_ts REAL,
        closed_ts REAL,
        close_price REAL DEFAULT 0,
        pnl_pct REAL DEFAULT 0,
        post_id TEXT DEFAULT '',
        title TEXT DEFAULT '',
        run_dir TEXT DEFAULT '',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_run ON paper_orders(run_dir) WHERE run_dir <> '';
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
        snap_chg24 REAL,
        snap_oi24 REAL,
        snap_amp24 REAL,
        snap_funding REAL,
        snap_rvol15 REAL,
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
    # v1.6.4：radar_tracks 补**登记时刻数值快照**（snap_*）。
    # 此前回放只能解析 reasons_json 文本反推，口径会漂（同一条规则算出「剩 11 笔」与
    # 「剩 7 笔」两个答案）→ 改任何规则都无法做 A/B 回放。老库这 5 列为 NULL，
    # 回放脚本必须兼容「快照缺失」并回退文本口径。
    for _scol in ("snap_chg24", "snap_oi24", "snap_amp24", "snap_funding", "snap_rvol15"):
        if rtcols and _scol not in rtcols:
            c.execute(f"ALTER TABLE radar_tracks ADD COLUMN {_scol} REAL")
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

# F15：含密钥的敏感设置 key —— 落库前整体 encrypt_secret，读出后先 decrypt_secret。
# 旧明文数据读入时 decrypt_secret 原样返回 = 自动兼容，下次保存自动迁移为密文。
# （key 名以各模块实际 set_setting 调用为准：src/llm.py、src/cex_wallet.py、
#   src/web3_wallet.py、src/mcp_client.py）
SENSITIVE_SETTING_KEYS = {
    "llm",                  # LLM 配置 JSON（含 api_key）—— src/llm.py
    "llm_oauth",            # LLM 订阅 OAuth 凭据（refresh/access token、sk- key）—— src/llm_auth.py
    "BINANCE_API_KEY",      # 币安 CEX API Key / Secret —— src/cex_wallet.py
    "BINANCE_API_SECRET",
    "W3_API_KEY",           # Web3 钱包服务 API Key / Secret —— src/web3_wallet.py
    "W3_API_SECRET",
    "mcp_servers",          # MCP server 配置 JSON —— src/mcp_client.py（token 另存 mcp_token:<name>）
}


def _is_sensitive_setting(key) -> bool:
    k = str(key or "")
    if k in SENSITIVE_SETTING_KEYS:
        return True
    return k.startswith("mcp_token:")  # 每个 MCP server 的 OAuth token —— src/mcp_client.py _token_key()


@_serialized
def set_setting(key, value):
    if _is_sensitive_setting(key) and isinstance(value, str) and value:
        value = encrypt_secret(value)  # 内部自带降级/异常兜底，不抛
    _conn_get().execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value))
    _conn_get().commit()


def get_setting(key, default=""):
    r = _conn_get().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    v = r["value"] if r else default
    if _is_sensitive_setting(key) and isinstance(v, str) and v:
        v = decrypt_secret(v)  # 内部自带降级/异常兜底，不抛
    return v


def get_all_settings():
    rows = _conn_get().execute("SELECT key,value FROM settings").fetchall()
    out = {}
    for r in rows:
        v = r["value"]
        if _is_sensitive_setting(r["key"]) and isinstance(v, str) and v:
            v = decrypt_secret(v)
        out[r["key"]] = v
    return out


@_serialized
def update_cron_job(job_id, patch):
    """F12：按 id 原子更新 settings("cron_jobs") 中单个任务的字段。
    读-改-写全程持锁（RLock），供调度线程逐任务回写 last_run/next_run，
    替代「读全量 -> 执行 -> 整份覆盖」的丢并发修改写法。
    patch 为字段字典；id 不存在或 patch 为空返回 False。"""
    if not job_id or not isinstance(patch, dict) or not patch:
        return False
    try:
        jobs = json.loads(get_setting("cron_jobs", "") or "[]")
    except Exception:
        return False
    if not isinstance(jobs, list):
        return False
    hit = False
    for j in jobs:
        if isinstance(j, dict) and j.get("id") == job_id:
            j.update(patch)
            hit = True
    if hit:
        set_setting("cron_jobs", json.dumps(jobs, ensure_ascii=False))
    return hit


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


# ---------------- 广场发文模拟挂单（v1.5.61：发文成功按文章 SMC 计划建纸单，7 天结算） ----------------

def _paper_out(r) -> dict:
    return dict(r)


@_serialized
def paper_add(symbol: str, direction: str, entry: float, stop: float, tp: float,
              zone_lo: float = 0, zone_hi: float = 0, market: str = "futures",
              leverage: int = 10, margin: float = 100.0, price: float = 0,
              status: str = "pending", post_id: str = "", title: str = "",
              run_dir: str = "") -> str:
    """创建模拟挂单。同 run_dir 已存在则幂等返回（防重复建单）。
    status: pending=挂单中 / open=已入场（建单时现价已在入场区内）。"""
    if run_dir:
        exist = _conn_get().execute(
            "SELECT id FROM paper_orders WHERE run_dir=?", (run_dir,)).fetchone()
        if exist:
            return exist["id"]
    pid = _uid()
    now = time.time()
    filled_ts = now if status == "open" else None
    _conn_get().execute(
        "INSERT INTO paper_orders (id,symbol,market,direction,entry,zone_lo,zone_hi,stop,tp,"
        "leverage,margin,status,last_price,filled_ts,closed_ts,close_price,pnl_pct,"
        "post_id,title,run_dir,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,? ,NULL,0,0,?,?,?, ?,?)",
        (pid, str(symbol).upper(), market or "futures",
         "short" if str(direction).lower() == "short" else "long",
         float(entry), float(zone_lo or 0), float(zone_hi or 0),
         float(stop or 0), float(tp or 0), int(leverage or 10), float(margin or 100),
         status or "pending", float(price or 0), filled_ts,
         post_id or "", title or "", run_dir or "", now, now))
    _conn_get().commit()
    return pid


def paper_list(limit: int = 200) -> list:
    rows = _conn_get().execute(
        "SELECT * FROM paper_orders ORDER BY created_at DESC LIMIT ?", (int(limit),)).fetchall()
    return [_paper_out(r) for r in rows]


def paper_stats() -> dict:
    rows = _conn_get().execute("SELECT status, COUNT(*) n FROM paper_orders GROUP BY status").fetchall()
    by = {r["status"]: r["n"] for r in rows}
    total = sum(by.values())
    settled_wl = by.get("win", 0) + by.get("loss", 0)
    return {"total": total, "pending": by.get("pending", 0), "open": by.get("open", 0),
            "win": by.get("win", 0), "loss": by.get("loss", 0), "noentry": by.get("noentry", 0),
            "win_rate": round(by.get("win", 0) / settled_wl * 100, 1) if settled_wl else None}


@_serialized
def paper_update(pid: str, **fields) -> None:
    """允许字段：status/last_price/filled_ts/closed_ts/close_price/pnl_pct。"""
    allowed = {"status", "last_price", "filled_ts", "closed_ts", "close_price", "pnl_pct"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return
    sets.append("updated_at=?")
    vals.append(time.time())
    vals.append(pid)
    _conn_get().execute(f"UPDATE paper_orders SET {', '.join(sets)} WHERE id=?", vals)
    _conn_get().commit()


@_serialized
def paper_remove(pid: str) -> None:
    _conn_get().execute("DELETE FROM paper_orders WHERE id=?", (pid,))
    _conn_get().commit()


# ---------------- 妖币追踪（v1.5.2：启动前发现 → 后续暴涨/暴跌结局验证） ----------------

# v1.6.2 同币冷却期：关单后 24h 内不再登记同一标的。
# 旧实现只挡 pending —— 关单即可立刻重登，实测 MTLUSDT 被登记 4 次、4 次全 dump、
# 每次都是一次完整爆仓，单一标的的失败被重复放大。
RADAR_REENTRY_COOLDOWN = 24 * 3600


def _radar_track_out(r):
    d = dict(r)
    d["reasons"] = json.loads(d.get("reasons_json") or "[]")
    d.pop("reasons_json", None)
    return d


def _snap_val(snap: dict, key: str):
    """登记快照取数（v1.6.4）：**缺值写 NULL，不写 0**。
    0 的含义是「真的是 0」，None 的含义是「没测到」—— 回放时两者结论完全不同，不能混。
    纯函数，不加 `@_serialized`（它不碰连接）。"""
    if not snap:
        return None
    v = snap.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@_serialized
def radar_track_add(symbol: str, stage: str, found_price: float, found_score: int = 0,
                    reasons=None, found_at: float = 0, direction: str = "LONG",
                    snap: dict = None) -> str:
    """登记一条启动前发现记录；同币已在跟踪中（pending）则幂等返回已有 id；
    同币刚关单不满 RADAR_REENTRY_COOLDOWN 则**拒绝登记**（返回空串，调用方据此跳过）。
    direction: LONG=做多 / SHORT=做空（决定结局语义，见 radar_tracker._judge_outcome）。
    snap: v1.6.4 登记时刻数值快照（chg24/oi24/amp24/funding/rvol15）—— 落库后回放不再依赖
    `reasons_json` 文本反推；缺键写 NULL。"""
    now = time.time()
    sym_u = str(symbol).upper()
    exist = _conn_get().execute(
        "SELECT id FROM radar_tracks WHERE symbol=? AND status='pending'",
        (sym_u,)).fetchone()
    if exist:
        return exist["id"]
    last_closed = _conn_get().execute(
        "SELECT closed_at FROM radar_tracks WHERE symbol=? AND status='closed' "
        "AND closed_at IS NOT NULL ORDER BY closed_at DESC LIMIT 1", (sym_u,)).fetchone()
    if last_closed and now - float(last_closed["closed_at"] or 0) < RADAR_REENTRY_COOLDOWN:
        return ""
    tid = _uid()
    _conn_get().execute(
        "INSERT INTO radar_tracks (id,symbol,stage,direction,found_price,found_score,reasons_json,"
        "status,outcome,max_gain_pct,max_drop_pct,peak_price,trough_price,last_price,"
        "outcome_price,snap_chg24,snap_oi24,snap_amp24,snap_funding,snap_rvol15,"
        "found_at,closed_at,updated_at) VALUES (?,?,?,?,?,?,?, 'pending','',0,0,0,0,0,0,?,?,?,?,?,?,NULL,?)",
        (tid, str(symbol).upper(), stage or "IGNITION",
         "SHORT" if str(direction).upper() == "SHORT" else "LONG",
         float(found_price or 0),
         int(found_score or 0), json.dumps(reasons or [], ensure_ascii=False),
         _snap_val(snap, "chg24"), _snap_val(snap, "oi24"), _snap_val(snap, "amp24"),
         _snap_val(snap, "funding"), _snap_val(snap, "rvol15"),
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
    """战绩统计（+ v1.6.5 OPT-07 的 by_stage 分组）。

    为什么要按 stage 分组：安装版已关单里 `IGNITION` 是 3 moon / 13 dump（18.8%），
    而 `ACCUMULATION` 是 **0 moon / 4 dump**，新规则却仍把 ACCUMULATION 当 ignition 登记。
    4 笔样本可能纯属偶然，所以本轮**不删、不降权**，只把两组的胜率分开摆出来，
    攒到 10 笔再决定（这正是 OPT-07 的原文要求）。
    """
    rows = _conn_get().execute(
        "SELECT status, outcome, stage, COUNT(*) AS n FROM radar_tracks "
        "GROUP BY status, outcome, stage").fetchall()
    st = {"total": 0, "pending": 0, "moon": 0, "dump": 0, "expired": 0,
          "by_stage": {}, "stages": []}
    for r in rows:
        st["total"] += r["n"]
        g = st["by_stage"].setdefault(
            r["stage"] or "—",
            {"pending": 0, "moon": 0, "dump": 0, "expired": 0, "closed": 0, "win_rate": 0.0})
        if r["status"] == "pending":
            st["pending"] += r["n"]
            g["pending"] += r["n"]
        elif r["outcome"] in ("moon", "dump", "expired"):
            st[r["outcome"]] += r["n"]
            g[r["outcome"]] += r["n"]
            g["closed"] += r["n"]
    for name, g in st["by_stage"].items():
        # 胜率分母只用**已关单**样本：pending 既不算赢也不算输，混进来会把胜率稀释成噪声。
        g["win_rate"] = round(g["moon"] / g["closed"] * 100, 1) if g["closed"] else 0.0
    # stages 按「已关单样本数」降序，样本多的组排前面（前端直接按序渲染，不再自己排）
    st["stages"] = sorted(st["by_stage"].items(), key=lambda kv: -kv[1]["closed"])
    return st


# ---------------- OPT-08 解除阻塞：登记快照 × 结局交叉统计 ----------------
# OPT-08（触发层从「发现波动」重建为「发现沉寂」）**不能现在动**：它是重建不是调参，
# 而现有 11 条规则之所以无效，是因为它们都在测同一件事（波动率，moon/dump 分布几乎完全重叠）。
# 要证明「低振幅横盘 + RVOL 放大 + OI 未动」更好，必须拿**登记时刻的数值快照**去分桶算胜率 ——
# 而 snap_* 列是 v1.6.4 才加的，安装版库里全是 NULL（0 条样本）。
# 所以本轮交付的不是改触发规则，而是**解除这个阻塞的工具**：下面的分桶器 + 门禁。
# 样本够了（>= RADAR_SNAP_MIN_N）再动规则，否则又是拍脑袋。
RADAR_SNAP_MIN_N = 20        # 门禁：已关单且带快照的样本少于它，不出任何结论
_RADAR_SNAP_BUCKETS = {
    # 轴的边界取法与判据本身同源（chg24 的 3/10 就是 _TRIG_UP_CHG24 / _TRIG_LATE_CHG24，
    # oi24 的 5 就是 _TRIG_MAX_OI24），只写数值不 import scanner，避免循环依赖。
    "snap_chg24":  [(-1e9, 0.0, "<0%"), (0.0, 3.0, "0~3%"),
                    (3.0, 10.0, "3~10%"), (10.0, 1e9, ">=10%")],
    "snap_oi24":   [(-1e9, -5.0, "<-5%"), (-5.0, 0.0, "-5~0%"),
                    (0.0, 5.0, "0~5%"), (5.0, 1e9, ">=5%")],
    "snap_amp24":  [(0.0, 10.0, "<10%"), (10.0, 20.0, "10~20%"),
                    (20.0, 35.0, "20~35%"), (35.0, 1e9, ">=35%")],
    "snap_funding": [(-1e9, -0.0015, "<=-0.15%"), (-0.0015, 0.0, "-0.15~0%"),
                     (0.0, 0.0005, "0~0.05%"), (0.0005, 1e9, ">=0.05%")],
    "snap_rvol15": [(0.0, 1.0, "<1x"), (1.0, 2.0, "1~2x"),
                    (2.0, 4.0, "2~4x"), (4.0, 1e9, ">=4x")],
}
SNAP_AXES = tuple(_RADAR_SNAP_BUCKETS.keys())


def snapshot_axis_labels(key: str) -> list:
    """某轴的分桶标签（纯函数，供前端/测试直接读，不用连库）。"""
    return [b[2] for b in _RADAR_SNAP_BUCKETS.get(key, [])]


def snap_axis_bucket(key: str, v) -> str:
    """把快照值映射到分桶标签。**None（未测到）返回 None**，不是「落在第一桶」。

    这条是硬要求：把「没测到」并进「<0%」会让某一桶凭空多出一堆假样本，
    正是 v1.6.4 落库时坚持写 NULL 不写 0 的同一个理由。
    """
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    for lo, hi, label in _RADAR_SNAP_BUCKETS.get(key, []):
        if lo <= v < hi:
            return label
    return None


def radar_snapshot_crosstab(direction: str = "LONG") -> dict:
    """OPT-08 的敲门砖：快照每个轴 × 结局的交叉表（默认只看 LONG —— 做空已整条砍掉）。

    返回 {n, ready, min_n, axes:{key:[{label,n,moon,dump,expired,win_rate}]}, unknown:{key:int}}。
    `ready=False` 时**不要**用它去改规则：样本不足，任何阈值都是拍脑袋。
    """
    rows = _conn_get().execute(
        "SELECT outcome, direction, snap_chg24, snap_oi24, snap_amp24, snap_funding, snap_rvol15 "
        "FROM radar_tracks WHERE status='closed'").fetchall()
    want = str(direction or "LONG").upper()
    closed = [r for r in rows if (r["direction"] or "LONG") == want
              and r["outcome"] in ("moon", "dump", "expired")]
    out = {"n": 0, "ready": False, "min_n": RADAR_SNAP_MIN_N,
           "direction": want, "axes": {}, "unknown": {}}
    for key in SNAP_AXES:
        out["axes"][key] = [{"label": lb, "n": 0, "moon": 0, "dump": 0, "expired": 0,
                             "win_rate": 0.0} for lb in snapshot_axis_labels(key)]
        out["unknown"][key] = 0
    for r in closed:
        # 计数口径：at least one axis 有值才算「带快照的样本」（用 chg24 当锚，与 v1.6.4 落库一致）
        if r["snap_chg24"] is not None:
            out["n"] += 1
        for key in SNAP_AXES:
            lb = snap_axis_bucket(key, r[key])
            if lb is None:
                out["unknown"][key] += 1
                continue
            node = next((x for x in out["axes"][key] if x["label"] == lb), None)
            if node is None:
                continue
            node["n"] += 1
            node[r["outcome"]] += 1
    for key, nodes in out["axes"].items():
        for nd in nodes:
            nd["win_rate"] = round(nd["moon"] / nd["n"] * 100, 1) if nd["n"] else 0.0
    out["ready"] = out["n"] >= RADAR_SNAP_MIN_N
    return out


# ---------------- v1.6.6：两个挂起决策的自动复核 ----------------
# 决策 2（设不设最低分门槛）与决策 3（挡不挡「登记时已下跌」）当时的结论都是「等样本再来」。
# **「挂着等」是最糟糕的形态** —— 没人记得为什么等、等到多少笔算够、什么时候该回头看。
# 所以把它仪表化：每次调用重算一遍证据，样本不够就如实说「还差几笔」，够了就直接给结论。
DECISION_BUCKET_MIN_N = 5      # 单个分数桶至少这么多笔，才有资格看它的胜率
DECISION_DOWN_MIN_N = 5        # 「登记时已下跌」至少这么多笔，才敢下「挡/不挡」
# 与 scanner 同源（只写数值不 import，避免循环依赖）：OPT-04 的价格硬挡线 / OI 堆积线。
# 决策 2 的关键在于**先剔除 OPT-04 会挡掉的那批样本再看 score 还有没有判别力** ——
# 不剔除的话 score 只是 chg24 的影子（高分样本恰好都是「已涨」的 dump），重复计数。
_OPT04_LATE_CHG24 = 10.0
_OPT04_PILED_OI24 = 5.0

_SCORE_BUCKETS = (("0-9", 0, 9), ("10-14", 10, 14), ("15-19", 15, 19),
                  ("20-24", 20, 24), ("25+", 25, 10 ** 9))
_SCORE_BUCKET_LABELS = tuple(b[0] for b in _SCORE_BUCKETS)


def _score_bucket(score) -> str:
    if score is None:
        return ""
    try:
        s = int(score)
    except (TypeError, ValueError):
        return ""
    for lab, lo, hi in _SCORE_BUCKETS:
        if lo <= s <= hi:
            return lab
    return ""


def _score_bucket_table(sample: list) -> list:
    acc = {lab: {"label": lab, "n": 0, "moon": 0, "dump": 0, "expired": 0, "win_rate": 0.0}
           for lab in _SCORE_BUCKET_LABELS}
    for r in sample:
        lab = _score_bucket(r["found_score"])
        if not lab:
            continue
        nd = acc[lab]
        nd["n"] += 1
        if r["outcome"] in nd:
            nd[r["outcome"]] += 1
    for nd in acc.values():
        nd["win_rate"] = round(nd["moon"] / nd["n"] * 100, 1) if nd["n"] else 0.0
    return [acc[lab] for lab in _SCORE_BUCKET_LABELS]


def radar_recheck_decisions(direction: str = "LONG") -> dict:
    """重算两个挂起决策的证据，明确回答「还差几笔」或「结论是什么」。

    这是给「待拍板」那两件事收尾用的：当初写的是「等 OPT-04 落地后的新样本」和
    「等 XLMUSDT 这类攒到 5 笔」，但**没有人会记得回来复查**。本函数把这个复查变成
    随时可调的一次计算 —— 挂在 /api/radar/decisions 上，也是它唯一的正确用法。

    与 `radar_tracks_stats` / `radar_snapshot_crosstab` 的分工：
      · stats 答「战绩如何」
      · crosstab 答「哪个轴有判别力」（OPT-08 的敲门砖，门禁 n≥20）
      · **本函数答「那两个决策能不能拍了」**（门禁更低：桶内 n≥5，因为它只问两件事）

    返回 {ok, decision2:{...}, decision3:{...}, direction, note}。
    `status="ready"` 才表示可以拍板；`waiting` 时 `gap` 是还差的笔数。
    """
    try:
        rows = _conn_get().execute(
            "SELECT outcome, direction, found_score, snap_chg24, snap_oi24 "
            "FROM radar_tracks WHERE status='closed'").fetchall()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    want = str(direction or "LONG").upper()
    closed = [r for r in rows if (r["direction"] or "LONG") == want
              and r["outcome"] in ("moon", "dump", "expired")]

    def _still_registers(r) -> bool:
        """OPT-04 之后这笔还会不会被登记？（价格硬挡线 / OI 堆积线）"""
        c, o = r["snap_chg24"], r["snap_oi24"]
        if c is not None and float(c) >= _OPT04_LATE_CHG24:
            return False
        if o is not None and float(o) >= _OPT04_PILED_OI24:
            return False
        return True

    after = [r for r in closed if _still_registers(r)]
    no_snap = [r for r in closed if r["snap_chg24"] is None and r["snap_oi24"] is None]
    dropped = len(closed) - len(after)
    b_all = _score_bucket_table(closed)
    b_after = _score_bucket_table(after)
    usable = [b for b in b_after if b["n"] >= DECISION_BUCKET_MIN_N]

    # ---- 决策 2：设不设最低分门槛 ----
    if len(usable) >= 2:
        d2_status = "ready"
        usable_sorted = sorted(usable, key=lambda b: b["win_rate"], reverse=True)
        best, worst = usable_sorted[0], usable_sorted[-1]
        if best["win_rate"] - worst["win_rate"] >= 20.0:
            d2_reco = (f"可以设了：剔除 OPT-04 会挡掉的那批之后，{best['label']} 分桶仍有判别力"
                       f"（{best['moon']}/{best['n']}，胜率 {best['win_rate']}%），"
                       f"而 {worst['label']} 桶只有 {worst['win_rate']}%（{worst['moon']}/{worst['n']}）。"
                       f"门槛可设在 {best['label']} 的下沿。")
        else:
            d2_reco = (f"仍然**不建议**设：各桶胜率差距只有 {best['win_rate'] - worst['win_rate']:.1f} 个百分点"
                       f"（最高 {best['label']} {best['win_rate']}%，最低 {worst['label']} {worst['win_rate']}%），"
                       f"不构成判别力。score 目前更像 chg24 的影子，不是独立信号。")
        d2_gap = 0
    else:
        d2_status = "waiting"
        d2_gap = max(0, DECISION_BUCKET_MIN_N * 2 - len(after))
        d2_reco = (f"还不能设。OPT-04 之后只剩 {len(after)} 笔可分析"
                   f"（被 OPT-04 挡掉的 {dropped} 笔已剔除），"
                   f"要形成「至少两个桶各有 {DECISION_BUCKET_MIN_N} 笔」的判断还差 ≈ {d2_gap} 笔。")
    d2_note = (f"另有 {len(no_snap)} 笔老样本没落登记快照（OPT-02 之前登记的），"
               f"只能按 reasons 文本口径估算，回放严格性略低。") if no_snap else ""

    # ---- 决策 3：挡不挡「登记时已下跌」 ----
    down = [r for r in closed if r["snap_chg24"] is not None and float(r["snap_chg24"]) < 0]
    down_moon = sum(1 for r in down if r["outcome"] == "moon")
    try:
        pen = _conn_get().execute(
            "SELECT COUNT(*) AS n FROM radar_tracks WHERE status='pending' "
            "AND direction=? AND snap_chg24 IS NOT NULL AND snap_chg24 < 0", (want,)).fetchone()
        pend_down = int(pen["n"]) if pen else 0
    except Exception:
        pend_down = 0
    if len(down) >= DECISION_DOWN_MIN_N:
        d3_status = "ready"
        wr = round(down_moon / len(down) * 100, 1)
        if wr <= 20.0:
            d3_reco = (f"该挡：登记时已下跌的 {len(down)} 笔里只有 {down_moon} 笔走出 moon"
                       f"（胜率 {wr}%）—— 反例攒够了，可以加这道闸门。")
        else:
            d3_reco = (f"不该挡：登记时已下跌的 {len(down)} 笔里有 {down_moon} 笔走出 moon"
                       f"（胜率 {wr}%）—— 「跌到位了」的直觉站得住，加闸门会误杀。")
        d3_gap = 0
    else:
        d3_status = "waiting"
        d3_gap = DECISION_DOWN_MIN_N - len(down)
        d3_reco = (f"还不能挡。已关单的「登记时已下跌」样本只有 {len(down)} 笔"
                   f"（另有 {pend_down} 笔还在 pending），"
                   f"再攒 {d3_gap} 笔才有反例可依据 —— 0 样本下加闸门就是纯拍脑袋。")

    return {
        "ok": True, "direction": want,
        "n_closed": len(closed), "n_after_opt04": len(after), "n_dropped_by_opt04": dropped,
        "n_no_snapshot": len(no_snap),
        "decision2": {
            "question": "登记要不要设最低分门槛？",
            "status": d2_status, "gap": d2_gap,
            "buckets_all": b_all, "buckets_after_opt04": b_after,
            "min_bucket_n": DECISION_BUCKET_MIN_N,
            "recommend": d2_reco, "note": d2_note,
        },
        "decision3": {
            "question": "登记后「已经跌下去」的币，挡不挡？",
            "status": d3_status, "gap": d3_gap,
            "n_down_closed": len(down), "n_down_moon": down_moon,
            "n_down_pending": pend_down, "min_n": DECISION_DOWN_MIN_N,
            "recommend": d3_reco,
        },
        "note": "本函数只给证据与建议，**不改任何规则**。加闸门是产品决策，要用户拍板。",
    }


if __name__ == "__main__":
    print("conversations:", len(list_conversations()))
    print("memory keys:", len(list_memory()))
