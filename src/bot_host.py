"""文件式 Bot 包（Hermes 风格：一个 Agent = 一个目录 + bot.md）

目录约定（项目根/.agents/bots/<slug>/）：
  bot.md
    ---frontmatter---
    name: 趋势猎手            # 显示名（persona 以此为准，room/会话关联都靠它）
    title: Trend Hunter
    description: ...
    avatar: 🧭
    color: "#F0B90B"
    model: ""                # 可选偏好模型
    skills: []               # 可选：技能白名单
    tone: ""                 # 语气风格
    enabled: true
    ---
    （markdown 正文 = 追加到 system prompt 的专属设定）

设计：
- 文件即唯一真相；sqlite 里的 agents 表函数由 state.py 薄委托到本模块，下游零改动。
- to_agent() 输出 shape 与旧 sqlite 完全一致：{id,name,title,description,avatar,color,config{model,skills,tone},created_at,updated_at}
- slug 是稳定目录名（改显示名不动 slug，会话 persona 关联不受影响）。
"""
import os
import re
import json
import time
import shutil

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOTS_DIR = os.path.join(PROJECT_DIR, ".agents", "bots")

DEFAULTS = [
    {
        "slug": "trend-hunter", "name": "趋势猎手", "title": "Trend Hunter",
        "description": "专攻 4H 趋势与突破动量，EMA20 + 量价共振，主做多。",
        "avatar": "🧭", "color": "#F0B90B", "tone": "果断、直接，善用表格给信号强度",
        "tools": ["scan_market", "market_quote", "propose_trade", "read_file", "run_command", "write_file"],
    },
    {
        "slug": "risk-sentinel", "name": "风控官", "title": "Risk Sentinel",
        "description": "对任何开仓做风险评估，硬止损 2%，冷血执行纪律。",
        "avatar": "🛡️", "color": "#F6465D", "tone": "冷静、审慎，先讲风险再讲机会",
        "tools": ["check_risk", "scan_market", "market_quote", "read_file"],
    },
    {
        "slug": "onchain-fox", "name": "链上侦察", "title": "Onchain Fox",
        "description": "解读聪明钱、巨鲸转账与 DEX 资金流，找 alpha 前先看链上。",
        "avatar": "🦊", "color": "#0ECB81", "tone": "神秘、精炼，爱用链上指标佐证",
        "tools": ["onchain_ops", "explain_x402", "scan_market", "fetch_url", "read_file"],
    },
    {
        "slug": "liquidity-hunter", "name": "流动性猎手", "title": "Liquidity",
        "description": "分析盘口深度、资金费率与 x402 支付流，擅长套利与兑换。",
        "avatar": "💰", "color": "#8B5CF6", "tone": "敏锐，量化视角，关注流动性与费差",
        "tools": ["market_quote", "scan_market", "list_skills", "run_skill", "explain_x402", "run_command", "read_file"],
    },
    {
        "slug": "default-assistant", "name": "默认助手", "title": "Default",
        "description": "通用交易助手：行情、风险、技能、记忆都可问。",
        "avatar": "🤖", "color": "#5E6673", "tone": "",
    },
]


def _ensure_dir():
    os.makedirs(BOTS_DIR, exist_ok=True)


def _slug_path(slug: str):
    return os.path.join(BOTS_DIR, slug, "bot.md")


def _valid_slug(slug: str) -> bool:
    # 允许 字母/数字/中划线/下划线/中文；禁止路径穿越与空白
    return bool(slug) and bool(re.fullmatch(r"[A-Za-z0-9_\-\u4e00-\u9fff]{1,64}", slug or "")) \
        and not slug.startswith(".")


def _dump_front(fields: dict) -> str:
    lines = []
    lines.append("---")
    for k in ("slug", "name", "title", "description", "avatar", "color", "model", "enabled"):
        if k not in fields:
            continue
        v = fields[k]
        if isinstance(v, bool):
            lines.append(f"{k}: {str(v).lower()}")
        elif isinstance(v, str):
            s = v.replace("\\", "\\\\").replace("\n", "\\n")
            if ":" in s or s != s.strip() or s == "" or re.search(r"[#\"']", s):
                lines.append(f"{k}: {json.dumps(s, ensure_ascii=False)}")
            else:
                lines.append(f"{k}: {s}")
        else:
            lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    if fields.get("skills"):
        lines.append(f"skills: {json.dumps(fields['skills'], ensure_ascii=False)}")
    if fields.get("tools"):
        lines.append(f"tools: {json.dumps(fields['tools'], ensure_ascii=False)}")
    if fields.get("tone"):
        tone = fields["tone"].replace("\n", "\\n")
        lines.append(f"tone: {json.dumps(tone, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _parse_md(text: str):
    """解析 bot.md → (front:dict, body:str)。"""
    front: dict = {}
    body = text
    stripped = text.lstrip("\ufeff")
    if stripped.startswith("---"):
        end = stripped.find("\n---", 3)
        if end != -1:
            head = stripped[3:end]
            raw = stripped[end + 4:]
            body = raw.lstrip("\n")
            for line in head.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" not in line:
                    continue
                k, _, v = line.partition(":")
                k = k.strip()
                v = v.strip()
                if not k:
                    continue
                # 引号包裹 → json 解
                if (v.startswith('"') and v.endswith('"')) or (v.startswith("[") and v.endswith("]")):
                    try:
                        v = json.loads(v)
                    except Exception:
                        pass
                elif v.lower() in ("true", "false"):
                    v = v.lower() == "true"
                front[k] = v
    return front, body


def _slug_from_name(name: str, seed: str = "") -> str:
    base = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "-", (name or "").strip()).strip("-").lower()
    base = re.sub(r"-{2,}", "-", base)
    if not base:
        base = "bot"
    cand = base
    i = 1
    while _valid_slug(cand) and os.path.isdir(os.path.join(BOTS_DIR, cand)) and seed and cand != seed:
        i += 1
        cand = f"{base}-{i}"
    if len(cand) > 56:
        cand = cand[:56]
    return cand or "bot"


def _read_agent(slug: str):
    path = _slug_path(slug)
    if not _valid_slug(slug) or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            front, body = _parse_md(f.read())
    except Exception:
        return None
    front.setdefault("slug", slug)
    front.setdefault("name", front.get("slug") or slug)
    front.setdefault("title", "")
    front.setdefault("description", "")
    front.setdefault("avatar", "🤖")
    front.setdefault("color", "#5E6673")
    front.setdefault("enabled", True)
    mtime = os.path.getmtime(path)
    return _to_agent(front, body, mtime)


def _to_agent(front: dict, body: str, mtime: float = None) -> dict:
    config = {
        "model": (front.get("model") or ""),
        "skills": (front.get("skills") or []),
        "tools": (front.get("tools") or []),
        "tone": (front.get("tone") or ""),
        "prompt": body or "",
    }
    out = {
        "id": front.get("slug") or front.get("name"),
        "name": front.get("name") or front.get("slug"),
        "title": front.get("title") or "",
        "description": front.get("description") or "",
        "avatar": front.get("avatar") or "🤖",
        "color": front.get("color") or "#5E6673",
        "config": config,
        "enabled": bool(front.get("enabled", True)),
        "updated_at": mtime or time.time(),
        "created_at": mtime or time.time(),
    }
    return out


def _write_agent(slug: str, name: str, title: str = "", description: str = "",
                 avatar: str = "🤖", color: str = "#5E6673", config: dict = None,
                 enabled: bool = True) -> str:
    _ensure_dir()
    cfg = config or {}
    slug = _valid_slug(slug) and slug or _slug_from_name(name)
    d = os.path.join(BOTS_DIR, slug)
    os.makedirs(d, exist_ok=True)
    front = {
        "slug": slug,
        "name": name or slug,
        "title": title,
        "description": description,
        "avatar": avatar or "🤖",
        "color": color or "#5E6673",
        "model": (cfg.get("model") or ""),
        "enabled": enabled,
    }
    skills = cfg.get("skills") or []
    tools = cfg.get("tools") or []
    tone = cfg.get("tone") or ""
    prompt = cfg.get("prompt") or ""
    if skills:
        front["skills"] = skills
    if tools:
        front["tools"] = tools
    if tone:
        front["tone"] = tone
    content = _dump_front(front)
    if prompt.strip():
        content += "\n" + prompt.strip() + "\n"
    with open(_slug_path(slug), "w", encoding="utf-8") as f:
        f.write(content)
    return slug


# ---------------- 公共 API（与旧 sqlite agents 函数签名一致） ----------------

def list_bots():
    _ensure_dir()
    out = []
    try:
        names = sorted(os.listdir(BOTS_DIR))
    except Exception:
        return out
    for n in names:
        if not os.path.isfile(os.path.join(BOTS_DIR, n, "bot.md")):
            continue
        ag = _read_agent(n)
        if ag:
            out.append(ag)
    # 默认稳定顺序：置顶启用，其余按名
    out.sort(key=lambda a: (not a["enabled"], a["name"]))
    return out


def get_bot(aid: str):
    if not aid:
        return None
    if os.path.isfile(_slug_path(aid)):
        return _read_agent(aid)
    # aid 不是 slug 时按 name 反查
    for ag in list_bots():
        if ag["id"] == aid or ag["name"] == aid:
            return ag
    return None


def get_bot_by_name(name: str):
    if not name:
        return None
    for ag in list_bots():
        if ag["name"] == name:
            return ag
    return None


def upsert_bot(name="", title="", description="", avatar="🤖", color="#5E6673",
               config=None, aid=None, enabled=True):
    """新建或更新。给 aid → 一律按 aid 作为 slug 目录写入（目录不存在也重建，幂等还原，
    不会从中文名另派生第二个同名片）；不给 aid → 按 name 生成 slug 新建。"""
    cfg = config or {}
    if aid:
        slug = aid if _valid_slug(aid) else _slug_from_name(name)
        _write_agent(slug, name=name or aid, title=title, description=description,
                     avatar=avatar, color=color, config=cfg, enabled=enabled)
        return get_bot(slug)["id"]
    slug = _slug_from_name(name)
    _write_agent(slug, name=name or slug, title=title, description=description,
                 avatar=avatar, color=color, config=cfg, enabled=enabled)
    return slug


def delete_bot(aid: str):
    ag = get_bot(aid)
    if not ag:
        return False
    slug = ag["id"] if _valid_slug(ag["id"]) else _slug_from_name(ag["name"])
    d = os.path.join(BOTS_DIR, slug)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
        return True
    return False


def export_bot(aid: str) -> str:
    ag = get_bot(aid)
    if not ag:
        return ""
    slug = ag["id"]
    with open(_slug_path(slug), encoding="utf-8") as f:
        return f.read()


def import_bot(md_text: str) -> dict:
    """导入一个 bot.md 文本 → 写盘。返回 agent dict。"""
    front, body = _parse_md(md_text)
    slug = front.get("slug") or _slug_from_name(front.get("name") or "")
    if not _valid_slug(slug):
        slug = _slug_from_name(front.get("name") or "bot")
    name = front.get("name") or slug
    existing = get_bot_by_name(name)
    if existing and existing["id"] != slug:
        slug = existing["id"]  # 同名人 → 归并到其包，避免重复
    # 目标 slug 目录已存在：
    cur = get_bot(slug) if _valid_slug(slug) else None
    if cur and os.path.isfile(_slug_path(slug)):
        if cur["name"] != name:
            # 名字不一致却复用旧 slug → 是想导入一份“副本”：生成 slug-2/-3 避免覆盖原 bot
            base = slug[:48]
            i = 2
            while _valid_slug(f"{base}-{i}") and os.path.isfile(_slug_path(f"{base}-{i}")):
                i += 1
            slug = f"{base}-{i}"
    _write_agent(slug, name=name, title=front.get("title") or "",
                 description=front.get("description") or "",
                 avatar=front.get("avatar") or "🤖", color=front.get("color") or "#5E6673",
                 config={"model": front.get("model") or "",
                         "skills": front.get("skills") or [],
                         "tone": front.get("tone") or "",
                         "prompt": body},
                 enabled=bool(front.get("enabled", True)))
    return get_bot(slug)


def seed_default_bots():
    """首次运行：.agents/bots 为空时写入默认档案。"""
    _ensure_dir()
    if list_bots():
        return
    for d in DEFAULTS:
        _write_agent(d["slug"], name=d["name"], title=d["title"],
                     description=d["description"], avatar=d["avatar"], color=d["color"],
                     config={"model": "", "skills": [], "tools": d.get("tools") or [],
                             "tone": d.get("tone", ""), "prompt": ""},
                     enabled=True)
