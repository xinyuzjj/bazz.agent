# -*- coding: utf-8 -*-
"""跨会话召回 —— 每轮自动从历史会话里捞出相关片段，注入当前上下文。

**为什么**（v1.7.4，机制借鉴 Hermes 的 per-turn prefetch）：
改造前只有「LLM 想起来才会调 search_history」这一条路，而模型**不知道自己不知道什么**：
用户说「上次那个币」，模型不会想到去翻历史，除非它恰好记得有这个工具。
Hermes 的做法是**每轮自动 prefetch**：拿当前这条消息去检索历史，把命中的片段
连同一条「这是召回、不是当前对话」的指示一起塞进上下文。

**实现取舍（与 Hermes 不同，且是有意的）**：
Hermes 用 FTS5 虚表 + 可加载的 CJK-bigram 扩展。本项目实测：
  · 本机 sqlite 3.53.1 **支持** fts5 与 trigram，但 **trigram 只索引 ≥3 字符** ——
    中文两字词（「雷达」「广场」）**查不出来**（实测 `MATCH '广场'` 返回空）；
  · 中文要可用就得自己把正文 bigram 展开再入库（Hermes 靠可加载扩展，而 Python 内置
    sqlite3 **不允许加载扩展**）→ 存储翻倍 + 一份变形逻辑要长期维护；
  · 本项目的数据量下，LIKE 子串扫全表只要几十毫秒，且对中文子串**100% 准确**。

所以这里不引进 FTS5，而是 **LIKE 检索 + 相关度排序 + 每轮自动注入**。
真正缺的从来不是索引速度，是「自动想起来」这件事。
"""
import re

MAX_HITS = 3              # 每轮最多注入几条召回
MAX_SNIPPET = 220         # 单条片段上限（字符）
LOOKBACK_ROWS = 300       # 单次检索最多看多少行（防大库拖慢）
MIN_TERM_LEN = 2

_EN_STOP = {"the", "and", "for", "with", "that", "this", "what", "how", "why", "are", "was",
            "you", "can", "not", "but", "get", "has", "have", "its", "about"}
_CN_STOP = {"什么", "怎么", "为什", "这个", "那个", "一下", "可以", "没有", "还是", "就是",
            "帮我", "我想", "请问", "现在", "已经", "我们", "他们", "自己"}


def probe_terms(q: str):
    """从当前消息里抽出**可检索的片段**。

    顺序即优先级：先字母数字串（币种符号 / 参数，辨识度最高），
    再长中文片段，最后 2 字窗口（滑动切分，用来兜「上次」「刚才」这类语境词）。
    """
    q = str(q or "")
    terms = []
    terms += re.findall(r"[A-Za-z]{2,}[A-Za-z0-9]*", q)
    terms += re.findall(r"[\u4e00-\u9fa5]{%d,}" % MIN_TERM_LEN, q)
    seen, out = set(), []
    for t in terms:
        tl = t.lower()
        if len(tl) < MIN_TERM_LEN or tl in _EN_STOP or t in _CN_STOP or tl in seen:
            continue
        seen.add(tl)
        out.append(t)
    return out[:6]


def _snippet(content: str, term: str) -> str:
    """截一段围绕命中词的上下文（别把整条长消息塞进去）。"""
    s = " ".join(str(content or "").split())
    i = s.lower().find(str(term).lower())
    if i < 0:
        return s[:MAX_SNIPPET]
    lo = max(0, i - 60)
    seg = s[lo:lo + MAX_SNIPPET]
    return ("…" if lo else "") + seg + ("…" if lo + MAX_SNIPPET < len(s) else "")


def recall(query: str, exclude_cid: str = "", limit: int = MAX_HITS) -> list:
    """按当前消息跨会话检索历史片段。任何异常都返回空表（召回失败不该影响主流程）。"""
    q = str(query or "").strip()
    if len(q) < MIN_TERM_LEN:
        return []
    terms = probe_terms(q)
    if not terms:
        return []
    try:
        import state as _state
        conn = _state._conn_get()
    except Exception:
        return []

    # cid -> {"score", "snippet", "title", "ts", "term"}
    hits = {}
    for term in terms:
        try:
            rows = conn.execute(
                "SELECT m.conv_id AS cid, m.content AS content, m.created_at AS ts, "
                "       c.title AS title, c.updated_at AS uts "
                "FROM messages m JOIN conversations c ON c.id = m.conv_id "
                "WHERE m.content LIKE ? AND m.role IN ('user','assistant') "
                "  AND m.conv_id <> ? "
                "ORDER BY m.created_at DESC LIMIT ?",
                (f"%{term}%", exclude_cid or "", LOOKBACK_ROWS)).fetchall()
        except Exception:
            continue
        for r in rows:
            cid = r["cid"]
            h = hits.get(cid)
            if h is None:
                h = hits[cid] = {"cid": cid, "title": r["title"] or "", "ts": r["ts"] or 0,
                                 "score": 0.0, "snippet": ""}
            # 命中次数给基础分；币种/参数类片段（纯 ASCII）辨识度高，加权
            h["score"] += 2.0 + (1.0 if term.isascii() else 0.0)
            if not h["snippet"]:
                h["snippet"] = _snippet(r["content"], term)
            # 标题也命中 → 这个会话大概率就是在讲这件事
            if term.lower() in (r["title"] or "").lower():
                h["score"] += 3.0

    out = sorted(hits.values(), key=lambda h: (-h["score"], -h["ts"]))[:max(1, int(limit))]
    return out


def usd_sign_symbol(text: str) -> str:
    """兜底占位（保留给将来做符号归一化）。"""
    return text


def format_block(hits: list, current_cid: str = "") -> str:
    """把召回结果变成可注入的文本块。无命中返回空串。

    ⚠️ 必须显式标注「这是历史召回」—— 否则模型会把它当成当前对话的一部分，
    出现「你刚才说的是…」这种张冠李戴。
    """
    if not hits:
        return ""
    lines = ["【历史会话召回 — 以下是**过去**对话里检索到的相关片段，不是本轮内容，仅作参考】"]
    for h in hits:
        title = (h.get("title") or "未命名").strip()[:40]
        snip = (h.get("snippet") or "").strip()
        if not snip:
            continue
        lines.append(f"- 〔{title}〕{snip}")
    if len(lines) == 1:
        return ""
    lines.append("（若无助于当前问题，忽略即可；不要据此编造用户没说过的话。）")
    return "\n".join(lines)


def build(query: str, exclude_cid: str = "", limit: int = MAX_HITS) -> str:
    """一步到位：检索 + 格式化。任何异常都返回空串。"""
    try:
        return format_block(recall(query, exclude_cid=exclude_cid, limit=limit),
                            current_cid=exclude_cid)
    except Exception:
        return ""
