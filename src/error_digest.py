# -*- coding: utf-8 -*-
"""错误 → 人话 + 行动建议（+ 脱敏）。

**为什么需要**：此前出错时用户看到的是「异常名 + str(e)[:200]」这种裸报错，
既不知道**哪一类**问题，也不知道**下一步该干什么**；更糟的是不同类别的错误长得一模一样 ——
「沙盒规则拦了」和「网络真不通」在界面上都显示成一句 fetch failed，用户被误导去折腾代理池。

**做法**（对齐 Hermes `agent/api_error_summary.py` 的思路）：沿 `__cause__` / `__context__`
链回溯，用 marker 元组判定类别，每类给「出了什么事 + 为什么 + 该怎么办」三段式；
最后统一脱敏，防止把 API Key 写进对话框或日志。

对外只暴露两个函数：
    digest(exc)  -> dict(kind / headline / detail / action)
    humanize(exc) -> str   一句话 + 换行建议（直接可贴进 reply）
"""
import re

# ---------------- 分类 marker ----------------
# 顺序即优先级：越靠前越具体。命中即停。
_NET_MARKERS = (
    "getaddrinfo", "name or service not known", "failed to resolve", "nodename nor servname",
    "max retries exceeded", "connection refused", "connection aborted", "connection reset",
    "remotedisconnected", "ssl", "schannel", "connect tunnel failed", "proxyerror",
    "temporary failure in name resolution", "network is unreachable", "no route to host",
)
_TIMEOUT_MARKERS = ("timed out", "timeout", "readtimeout", "connecttimeout")
_RATE_MARKERS = ("429", "too many requests", "rate limit", "4029", "限流")
_AUTH_MARKERS = ("401", "403", "unauthorized", "forbidden", "invalid api", "no key",
                 "signature", "apikey", "api key")
_MODEL_MARKERS = ("model not found", "does not exist", "no such model", "unsupported model",
                  "context length", "maximum context")
# 「本机后端不可达」与「真网络不通」必须分开：前者是本机服务没起（或 NO_PROXY 没配被代理劫持）
_LOCALBACKEND_MARKERS = ("本地后端不可达", "local backend", "127.0.0.1:8", "127.0.0.1:9")
# 沙盒拦截是**本地规则**拦的，绝不是网络问题 —— 这条不分开就会把人往代理池上引
_SANDBOX_MARKERS = ("沙箱拦截", "sandbox", "不在白名单", "forbidden pattern", "路径不在允许范围")

_KINDS = {
    "sandbox":      ("被本地沙盒规则拦下了", "这是**本机安全规则**拦的，不是网络问题"),
    "localbackend": ("本机后端服务不可达", "后端进程没起来，或本机流量被代理劫持了"),
    "network":      ("连不上目标服务", "网络出口不通（或目标在受限地区）"),
    "timeout":      ("请求超时", "对方没在时限内响应"),
    "ratelimit":    ("被数据源限流", "这类免费源按**窗口内次数**限流，慢下来就能绕开"),
    "auth":         ("鉴权失败", "凭据无效、过期，或这个账号没有该权限"),
    "model":        ("模型不可用", "模型名 / 端点 / 账号权限不匹配"),
    "unknown":      ("出错了", "未能归类到已知类型"),
}

_ACTIONS = {
    "sandbox": "换一条合规命令（相对路径须带允许前缀；`rm` 不支持递归），或先把要删的列出来再逐个删。",
    "localbackend": "确认后端在跑；若在跑仍报这个，检查 `NO_PROXY` 是否含 `127.0.0.1`（否则本机流量被代理接管）。",
    "network": "检查网络 / 代理（本机 Clash 混合端口 7897），或改用本机行情网关取数。",
    "timeout": "重试一次；持续超时就把时间窗调大，别在错误路径上反复打。",
    "ratelimit": "隔几十秒再试（例如 GoPlus 实测统一 5.5s 间隔即可 14/14 全成功）。",
    "auth": "到设置里重新填一次 Key，并确认该 Key 有这项权限；订阅类账号注意权限范围。",
    "model": "换一个模型，或在设置里核对 `base_url` / 模型名是否与该 provider 匹配。",
    "unknown": "把下面这行原文一起贴出来，便于定位。",
}

# ---------------- 脱敏 ----------------
_REDACT = (
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "sk-***"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"), r"\1***"),
    (re.compile(r"""(?i)((?:"?(?:api[_-]?key|secret|token|password|passwd)"?\s*[:=]\s*"?))([A-Za-z0-9_\-]{6,})"""),
     r"\1***"),
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "***"),
)


def redact(text: str) -> str:
    """掩掉凭据类内容。任何异常都吞掉返回原文（脱敏不该成为新的故障点）。"""
    try:
        out = str(text or "")
        for pat, rep in _REDACT:
            out = pat.sub(rep, out)
        return out
    except Exception:
        return str(text or "")


def _chain_text(exc) -> str:
    """把异常链上的类型名 + 消息拼成一段小写文本用于匹配。"""
    parts, seen = [], set()
    cur = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        parts.append(f"{type(cur).__name__}: {cur}")
        # requests 的 HTTPError 把状态码放在 response 上
        resp = getattr(cur, "response", None)
        code = getattr(resp, "status_code", None)
        if code:
            parts.append(f"status_code={code}")
        cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
    return " | ".join(parts).lower()


def _classify(blob: str) -> str:
    if any(m in blob for m in _SANDBOX_MARKERS):
        return "sandbox"
    if any(m in blob for m in _LOCALBACKEND_MARKERS):
        return "localbackend"
    if any(m in blob for m in _RATE_MARKERS):
        return "ratelimit"
    if any(m in blob for m in _AUTH_MARKERS):
        return "auth"
    if any(m in blob for m in _MODEL_MARKERS):
        return "model"
    if any(m in blob for m in _TIMEOUT_MARKERS):
        return "timeout"
    if any(m in blob for m in _NET_MARKERS):
        return "network"
    return "unknown"


def digest(exc) -> dict:
    """把异常拆成 {kind, headline, why, action, raw}。永不抛异常。"""
    try:
        raw = redact(f"{type(exc).__name__}: {exc}")[:600]
        blob = _chain_text(exc)
        kind = _classify(blob)
        headline, why = _KINDS[kind]
        return {"kind": kind, "headline": headline, "why": why,
                "action": _ACTIONS[kind], "raw": raw}
    except Exception as e:  # noqa: BLE001
        return {"kind": "unknown", "headline": _KINDS["unknown"][0],
                "why": _KINDS["unknown"][1], "action": _ACTIONS["unknown"],
                "raw": redact(str(e))[:600]}


def humanize(exc, *, with_raw: bool = True, prefix: str = "⚠️") -> str:
    """一句话结论 + 该怎么办（+ 可选的脱敏原文）。直接可贴进 reply。"""
    d = digest(exc)
    out = f"{prefix} {d['headline']} —— {d['why']}。\n→ {d['action']}"
    if with_raw and d["raw"]:
        out += f"\n\n原文：{d['raw']}"
    return out
