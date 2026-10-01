"""网络边界防护 —— 读 HTTP 响应体前先卡字节上限。

**为什么需要**：错误响应体往往不是小 JSON，而是网关 / Cloudflare 的巨型 HTML 挑战页。
原写法 `r.text[:200]` 有个隐蔽前提 —— `r.text` 会把**整个 body 先读进内存并解码**，
之后才轮到 `[:200]` 生效。body 一大，内存和耗时都白付；更糟的是这发生在「本来就已经
出错」的路径上，会把一次可诊断的失败拖成一次卡顿。

**做法**（对齐 Hermes `agent/bounded_response.py` 的思路，但保留本项目的中文错误文案风格）：
原始字节先截断、再解码；对超限的响应体显式标注「已截断」，让读的人知道**后面还有内容**，
而不是以为信息就这么多。诊断路径**绝不抛异常**。
"""
MAX_BODY_BYTES = 64 * 1024      # 响应体读取上限（对齐 Hermes 的 64 KB）
MAX_SNIPPET_CHARS = 200         # 错误摘要用的短文本上限


def bounded_text(resp, limit: int = MAX_BODY_BYTES, *, marker: bool = True) -> str:
    """安全取响应体文本：**先按字节截断、再解码**。

    - 非流式响应：用已读入的 `resp.content` 截断
    - 流式响应且尚未消费：用 `iter_content` **有界**读取，避免触发全量下载
    - 任何异常一律吞掉返回空串 —— 这本身就在错误路径上，不能二次抛错
    """
    try:
        consumed = getattr(resp, "_content_consumed", True)
        if consumed is False:
            chunks, total = [], 0
            try:
                for ch in resp.iter_content(chunk_size=8192):
                    if not ch:
                        continue
                    chunks.append(ch)
                    total += len(ch)
                    if total >= limit:
                        break
            except Exception:
                pass
            raw = b"".join(chunks)[:limit]
        else:
            raw = getattr(resp, "content", None)
            if raw is None:
                raw = (getattr(resp, "text", "") or "").encode("utf-8", "replace")
            raw = bytes(raw)
            truncated = len(raw) > limit
            raw = raw[:limit]
            enc = getattr(resp, "encoding", None) or "utf-8"
            try:
                txt = raw.decode(enc, errors="replace")
            except (LookupError, TypeError):
                txt = raw.decode("utf-8", errors="replace")
            return txt + (" …（响应体超限已截断）" if (truncated and marker) else "")
        # 流式分支：截断与否这里判断不了长度，统一按需标注
        enc = getattr(resp, "encoding", None) or "utf-8"
        return raw.decode(enc, errors="replace")
    except Exception:
        return ""


def snippet(resp, chars: int = MAX_SNIPPET_CHARS) -> str:
    """错误摘要用的一行短文本（压掉换行与多余空白）。"""
    return " ".join(bounded_text(resp, marker=False).split())[:chars]
