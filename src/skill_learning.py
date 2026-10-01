# -*- coding: utf-8 -*-
"""技能自建 / 自改进 —— 从真实对话里识别「值得固化的技巧」，生成技能草案。

**为什么**（v1.7.4，机制借鉴 Hermes 的 background review）：
Hermes 每 N 轮分叉一个后台 Agent，判断本轮有没有产生「下次还该这么做」的经验，
若有就创建或改进 skill。BAZZ 此前完全没有这个回路 —— 用户在对话里纠正过一次的做法，
换个会话又会被重犯，纠正本身白白流失。

**与 Hermes 的两处有意差异（都是安全考虑，不是偷懒）**：

1. **默认关闭**（`settings.skill_learning_enabled`，缺省 `"0"`）。
   自动创建技能 = 悄悄改变 agent 的能力边界。Hermes 面向开发者本机，可以默认开；
   这里涉及下单 / 发帖这类真实动作，必须让用户先知道这件事存在。

2. **不直接落进正式技能目录**：草案先写 `.agents/skills/_drafts/<name>/SKILL.md`，
   **用户采纳后才转正**。Hermes 是直接写的；而这里写坏一个技能会直接影响真实交易动作，
   所以把「生成」和「生效」拆成两步。

两者都可以改（把 setting 打开即可用），但默认值刻意保守。
"""
import io
import json
import os
import re
import threading
import time

REVIEW_EVERY_TURNS = 8         # 每多少轮触发一次 review（Hermes 是 10）
DRAFT_ROOT = os.path.join(".agents", "skills", "_drafts")
MAX_DRAFT_CHARS = 6000         # 草案正文上限（防止把整段对话灌进去）
MAX_NAME_CHARS = 40

# 只允许安全字符，避免把技能名写成路径穿越
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,%d}$" % (MAX_NAME_CHARS - 2))

_REVIEW_PROMPT = """你在帮一个交易助手做「经验固化」判断。

下面是一段刚结束的对话（含用户纠正、工具使用过程）。请判断：**这里面有没有产生
「下次还应该这么做」的可复用技巧？**

只有满足全部三条才算：
1. 是**做法/流程**层面的（例如「查妖币前先 run 雷达名单」「发帖先读 SKILL.md 再跑 CLI」），
   不是一次性的数据结论；
2. 用户明确纠正过、或者明显踩了坑又绕过去了；
3. 换个会话、换只币，这个做法仍然成立。

**没有就老实说没有。** 不要为了交差编一个。

输出**严格 JSON**（不要 markdown 代码块）：
{"worth": true/false, "name": "kebab-case-短名", "description": "一句话说明什么时候用它",
 "body": "Markdown 正文：做法步骤 + 为什么 + 反例（≤1500 字）"}

worth=false 时其余字段留空字符串。"""


def _draft_dir() -> str:
    root = os.path.join(os.getcwd(), DRAFT_ROOT)
    os.makedirs(root, exist_ok=True)
    return root


def enabled() -> bool:
    """是否开启（默认关）。读不到就当作关 —— 宁可少做，不可擅自开。"""
    try:
        import state
        return str(state.get_setting("skill_learning_enabled", "0")).lower() not in (
            "0", "false", "none", "")
    except Exception:
        return False


def due(turns: int) -> bool:
    """到达触发节奏且已开启。"""
    return enabled() and int(turns or 0) > 0 and int(turns) % REVIEW_EVERY_TURNS == 0


def _history_text(history: list, limit: int = 14) -> str:
    """把最近若干轮压成 review 可读的文本（太长就截）。"""
    rows = [h for h in (history or []) if isinstance(h, dict) and h.get("role") in ("user", "assistant")]
    rows = rows[-limit:]
    out = []
    for h in rows:
        who = "用户" if h.get("role") == "user" else "助手"
        txt = " ".join(str(h.get("content") or "").split())[:600]
        out.append(f"{who}：{txt}")
    return "\n".join(out)[:5000]


def review(history: list, llm_cfg: dict = None) -> dict:
    """让 LLM 判断有无值得固化的技巧。返回 {"worth": bool, ...}；任何失败返回 worth=False。"""
    text = _history_text(history)
    if len(text) < 80:
        return {"worth": False}
    try:
        import llm
        raw = llm.chat(_REVIEW_PROMPT, f"对话如下：\n\n{text}", llm_cfg=llm_cfg or {}) or ""
    except Exception:
        return {"worth": False}
    raw = str(raw).strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return {"worth": False}
    try:
        d = json.loads(m.group(0))
    except Exception:
        return {"worth": False}
    if not d.get("worth"):
        return {"worth": False}
    name = str(d.get("name") or "").strip().lower()
    if not _NAME_RE.match(name):
        return {"worth": False}
    return {"worth": True, "name": name,
            "description": str(d.get("description") or "").strip()[:200],
            "body": str(d.get("body") or "").strip()[:MAX_DRAFT_CHARS],
            "created_at": time.time()}


def save_draft(d: dict) -> str:
    """把草案落进 `_drafts/`（**不碰正式技能目录**）。返回草案目录路径。"""
    name = str(d.get("name") or "").strip().lower()
    if not _NAME_RE.match(name):
        raise ValueError(f"技能名不合规：{name!r}")
    root = _draft_dir()
    ddir = os.path.join(root, name)
    os.makedirs(ddir, exist_ok=True)
    fm = (f"---\nname: {name}\ndescription: {d.get('description') or ''}\n"
          f"source: agent-authored\ncreated_at: {int(d.get('created_at') or time.time())}\n---\n\n")
    with io.open(os.path.join(ddir, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write(fm + (d.get("body") or ""))
    return ddir


def list_drafts() -> list:
    """列出待采纳草案。"""
    try:
        root = _draft_dir()
    except Exception:
        return []
    out = []
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name, "SKILL.md")
        if not os.path.isfile(p):
            continue
        try:
            txt = io.open(p, encoding="utf-8").read()
        except Exception:
            continue
        desc = ""
        m = re.search(r"^description:\s*(.+)$", txt, re.M)
        if m:
            desc = m.group(1).strip()
        out.append({"name": name, "description": desc, "path": p,
                    "chars": len(txt)})
    return out


def adopt_draft(name: str) -> dict:
    """把草案**转正**：移到 `.agents/skills/<name>/`（正式技能目录）。

    转正是显式动作 —— 只有用户点了采纳才会走到这里。
    """
    name = str(name or "").strip().lower()
    if not _NAME_RE.match(name):
        return {"ok": False, "error": "技能名不合规"}
    src = os.path.join(_draft_dir(), name, "SKILL.md")
    if not os.path.isfile(src):
        return {"ok": False, "error": "草案不存在"}
    dst_dir = os.path.join(os.getcwd(), ".agents", "skills", name)
    if os.path.exists(os.path.join(dst_dir, "SKILL.md")):
        return {"ok": False, "error": "同名技能已存在，请先改名或删除"}
    os.makedirs(dst_dir, exist_ok=True)
    try:
        with io.open(src, encoding="utf-8") as fh:
            body = fh.read()
        with io.open(os.path.join(dst_dir, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write(body)
    except Exception as e:
        return {"ok": False, "error": f"写入失败：{e}"}
    return {"ok": True, "name": name, "path": os.path.join(dst_dir, "SKILL.md")}


def discard_draft(name: str) -> dict:
    """丢弃草案（不采纳）。"""
    name = str(name or "").strip().lower()
    if not _NAME_RE.match(name):
        return {"ok": False, "error": "技能名不合规"}
    p = os.path.join(_draft_dir(), name, "SKILL.md")
    try:
        os.remove(p)
        os.rmdir(os.path.dirname(p))
    except Exception:
        return {"ok": False, "error": "草案不存在或已被清理"}
    return {"ok": True, "name": name}


def maybe_review_async(history: list, llm_cfg: dict = None, turns: int = 0) -> None:
    """到达节奏就**后台线程**跑一次 review —— 绝不给主对话加延迟。

    没开启 / 不到点 / 已跑满并发，都直接返回。
    """
    if not due(turns):
        return
    if getattr(maybe_review_async, "_running", False):
        return
    maybe_review_async._running = True

    def _work():
        try:
            d = review(history, llm_cfg)
            if d.get("worth"):
                save_draft(d)
        except Exception:
            pass
        finally:
            maybe_review_async._running = False

    try:
        threading.Thread(target=_work, name="skill-review", daemon=True).start()
    except Exception:
        maybe_review_async._running = False
