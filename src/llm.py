"""多模型 LLM 层（OpenAI-compatible，Hermes 风格 provider 接入）

设计参照 Hermes：
- 配置存于本地 state settings("llm") = JSON {"provider","base_url","api_key","model"}。
- 走标准 OpenAI Chat Completions 协议（/chat/completions），天然兼容
  OpenAI / DeepSeek / Anthropic / Google / xAI / Moonshot / 硅基流动 / Qwen / 智谱 /
  Mistral / OpenRouter / 本地 Ollama 等。
- 支持 function calling（tools + tool_calls 循环）：这是「真 LLM 接上」的核心——
  Agent 让 LLM 决定调用哪个工具（扫描/风险/下单方案/支付/Skills…），再合成最终回答。
- 无 key 时 chat()/stream_chat()/chat_with_tools() 返回 None，调用方降级到规则路由。

新增能力：
- TOOLS：Agent 能力的标准函数声明（供 LLM 调用）。
- chat_with_tools：单轮 function-calling（返回 content + tool_calls），供 agent 循环使用。
- list_models：拉取模型目录（供设置面板下拉）。
- test_connection：验证 key + base_url 是否可用。
"""
import os
import json
import re
import requests
from typing import Optional, Iterator, Dict, Any, List

try:
    from state import get_setting
except Exception:
    def get_setting(k, d=""):
        return d


# provider 预设（base_url 去掉尾部 /v1 之外的路径统一由模型端点拼接）
# 模型名按 2026-09 各厂商官方 API 现役目录核实（只列可用、不列已退役），
# 与前端 SettingsView.tsx 的 LLM_PROVIDERS presets 完全对齐。
# 备注：
# - DeepSeek 官方 API 现用名 deepseek-v4-pro / deepseek-v4-flash（+vision-exp）；
#   旧的 deepseek-chat / deepseek-reasoner 已于 2026-07-24 下线，遇历史配置由 _LEGACY_MODEL_MAP 自动归一到 v4 系列。
#   第三方网关（如硅基流动）仍以「deepseek-ai/DeepSeek-V4-*」组织前缀托管同名模型，详见 siliconflow 预设。
# - Anthropic / Google 官方均提供 OpenAI 兼容端点（/chat/completions），可直接接入。
# - 无 key 或拉目录失败时，设置面板会回退到下列 presets 作为可选手动模型。
PROVIDERS = {
    # OpenAI：GPT-5.6 世代(2026-09-03 发布) + 5.5/5.4 + codex/o3；gpt-4o/4.1/o3-mini/o1 等已退役
    "openai":   {"base_url": "https://api.openai.com/v1", "models": [
        "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5",
        "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.3-codex", "o3"]},
    # Anthropic：Claude Opus 5 / Sonnet 5 / Haiku 4.5（Opus 4、Sonnet 4 已于 2026-06 退役）
    "anthropic": {"base_url": "https://api.anthropic.com/v1", "models": [
        "claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5"]},
    # Google Gemini：OpenAI 兼容端点；3.1 Pro 为旗舰，Flash 线已迭代到 3.6/3.5
    "google":   {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "models": [
        "gemini-3.1-pro", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3-flash", "gemini-3.1-flash-lite"]},
    # xAI Grok：OpenAI 兼容端点（2026-08 起品牌 SpaceXAI，API 域名不变）
    "xai":      {"base_url": "https://api.x.ai/v1", "models": [
        "grok-4.6", "grok-4.5", "grok-4.3"]},
    # DeepSeek 官方 API 现用名（2026-09 现役，1M ctx，Thinking/Non-Thinking 切换）：
    # - deepseek-v4-pro (V4-Pro-0813, GA)
    # - deepseek-v4-flash (V4-Flash-0731, public beta)
    # - deepseek-v4-flash-vision-exp (实验性多模态)
    # 旧别名 deepseek-chat / deepseek-reasoner 已于 2026-07-24 15:59 UTC 下线，
    # 在官方端点上继续发请求会 404；详见 _LEGACY_MODEL_MAP 自动迁移。
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "models": [
        "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"]},
    # Moonshot：moonshot-v1 全系与 kimi-k2.5 已于 2026-08-31 下线，现役 kimi-k3 家族
    "moonshot": {"base_url": "https://api.moonshot.cn/v1", "models": [
        "kimi-k3", "kimi-k2.7-code", "kimi-k2.7-code-highspeed", "kimi-k2.6"]},
    # 阿里云百炼：qwen-max/plus/turbo 为自动指向最新版的长期别名
    "qwen":     {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "models": [
        "qwen-max", "qwen-plus", "qwen-turbo"]},
    # 智谱 BigModel：GLM-5.3 旗舰 / 5.3-Flash 原生多模态（glm-4-plus/air/flash 已退役）
    "zhipu":    {"base_url": "https://open.bigmodel.cn/api/paas/v4", "models": [
        "glm-5.3", "glm-5.3-flash", "glm-5.2", "glm-5", "glm-4.7"]},
    "mistral":  {"base_url": "https://api.mistral.ai/v1", "models": [
        "mistral-large-latest", "mistral-medium-latest", "mistral-small-latest", "codestral-latest"]},
    # 硅基流动：第三方托管目录用「组织/模型」全名；前缀以 /models 实际返回为准（可用“拉取模型”刷新）
    "siliconflow": {"base_url": "https://api.siliconflow.cn/v1", "models": [
        "deepseek-ai/DeepSeek-V4-Pro", "deepseek-ai/DeepSeek-V4-Flash",
        "Qwen/Qwen3.6-27B", "Qwen/Qwen3.6-35B-A3B",
        "Pro/moonshotai/Kimi-K2.6", "Pro/zai-org/GLM-5.2"]},
    # OpenRouter：聚合网关，前缀「厂商/模型」；完整目录请用“拉取模型”
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "models": [
        "openai/gpt-5.6-sol", "anthropic/claude-sonnet-5", "google/gemini-3.1-pro", "x-ai/grok-4.6"]},
    "ollama":   {"base_url": "http://localhost:11434/v1", "models": [
        "qwen3", "llama3.3", "deepseek-r1", "gemma3"]},
    "custom":   {"base_url": "", "models": []},
}


_LEGACY_MODEL_MAP = {
    # 兜底：把官方 DeepSeek 端点上已下线（2026-07-24 后 404）的别名归一到现役 v4 系列。
    # 第三方网关（如硅基流动）自有命名 deepseek-ai/DeepSeek-V4-* 不走此映射，避免误伤。
    "deepseek-chat": "deepseek-v4-flash",
    "deepseek-reasoner": "deepseek-v4-pro",
}


def _is_official_deepseek(cfg: dict) -> bool:
    """是否官方 DeepSeek 端点（api.deepseek.com）。仅官方端才做退役别名迁移，
    避免误伤第三方网关托管的 deepseek-ai/DeepSeek-V4-*。"""
    try:
        return (str(cfg.get("provider", "")).lower() == "deepseek") or \
               ("api.deepseek.com" in str(cfg.get("base_url", "") or ""))
    except Exception:
        return False


def _map_legacy_model(model: str, cfg: dict = None) -> str:
    """把已退役/过期的官方模型别名映射到现名（仅官方 DeepSeek 端点）。
    第三方网关托管的 deepseek-ai/DeepSeek-V4-* 不受影响。"""
    if not model:
        return model
    if not cfg or not _is_official_deepseek(cfg):
        return model
    return _LEGACY_MODEL_MAP.get(model.strip(), model)


# Hermes auxiliary 细分任务模型槽：reasoning(深度思考) / vision(多模态) / summarize(压缩/摘要)
AUX_SLOTS = ("reasoning", "vision", "summarize")


def get_llm_config() -> dict:
    raw = get_setting("llm", "")
    try:
        cfg = json.loads(raw) if raw else {}
    except Exception:
        cfg = {}
    cfg.setdefault("provider", "openai")
    preset = PROVIDERS.get(cfg["provider"], {})
    cfg.setdefault("base_url", preset.get("base_url", ""))
    cfg.setdefault("model", (preset.get("models") or ["gpt-5.4-mini"])[0])
    cfg.setdefault("api_key", os.getenv("LLM_API_KEY", ""))   # 兼容旧的 LLM_API_KEY 直给
    cfg.setdefault("key_env", "")                             # key_env：api_key 留空时从该环境变量读
    if not isinstance(cfg.get("backup_models"), list):
        cfg["backup_models"] = []
    aux = cfg.setdefault("aux", {})
    for slot in AUX_SLOTS:
        aux.setdefault(slot, "")
    _migrate_persisted_models(cfg)
    return cfg


def _migrate_persisted_models(cfg: dict):
    """把 DB 里已保存的官方 DeepSeek 退役别名就地改写为现名并回存（一次性迁移）。
    主模型 / 备用 / reasoning aux 槽都覆盖；非官方端点不动。"""
    if not _is_official_deepseek(cfg):
        return
    changed = False
    model = cfg.get("model") or ""
    new_m = _LEGACY_MODEL_MAP.get(model.strip(), model)
    if new_m != model:
        cfg["model"] = new_m
        changed = True
    new_backup = [_LEGACY_MODEL_MAP.get(str(m).strip(), str(m)) for m in cfg.get("backup_models") or []]
    if new_backup != (cfg.get("backup_models") or []):
        cfg["backup_models"] = new_backup
        changed = True
    aux = cfg.get("aux") or {}
    rslot = str(aux.get("reasoning") or "").strip()
    new_r = _LEGACY_MODEL_MAP.get(rslot, rslot)
    if new_r != rslot:
        aux["reasoning"] = new_r
        changed = True
    if changed:
        try:
            set_setting("llm", json.dumps({k: v for k, v in cfg.items() if k != "api_key"},
                                          ensure_ascii=False))
        except Exception:
            pass


def resolve_key(cfg: dict) -> str:
    """统一取 key：api_key 优先，否则按 key_env 从环境变量注入（Hermes key_env 机制）。"""
    k = (cfg.get("api_key") or "").strip()
    if not k:
        envn = (cfg.get("key_env") or "").strip()
        if envn:
            k = (os.getenv(envn) or "").strip()
    return k


def _materialize(cfg: dict = None) -> dict:
    """把配置补成可直接请求的形态（api_key 解析 key_env 后填入）。

    以全局配置为底，只覆盖调用方传入的“非空”字段——修复部分配置（如仅
    {deep_thinking: True}）传入时丢失 provider/base_url/model/api_key 的坑。
    """
    base = get_llm_config()
    if not cfg:
        return base
    merged = {**base, **{k: v for k, v in cfg.items() if v not in (None, "")}}
    if not merged.get("api_key"):
        merged["api_key"] = resolve_key(merged)
    return merged


def is_configured(cfg: dict = None) -> bool:
    cfg = cfg or get_llm_config()
    return bool(resolve_key(cfg))


def snapshot(cfg: dict = None) -> dict:
    """Hermes provider_snapshot：生成不落明文的会话 provider 快照（防漂移用）。
    快照含 provider/base_url/model/backup/aux/key_env 名，不含明文 key。
    """
    cfg = cfg or get_llm_config()
    return {
        "provider": cfg.get("provider", ""),
        "base_url": cfg.get("base_url", ""),
        "model": cfg.get("model", ""),
        "backup_models": list(cfg.get("backup_models") or []),
        "aux": {s: (cfg.get("aux") or {}).get(s, "") for s in AUX_SLOTS},
        "key_env": cfg.get("key_env", ""),
        "key_set": bool(resolve_key(cfg)),
        "at": int(__import__("time").time()),
    }


def cfg_from_snapshot(snap: dict = None) -> dict:
    """把会话级快照叠加到当前配置：延续旧会话时保持当时的 provider/model 选择，
    密钥始终取当前（快照不含明文 key）→ 满足防漂移又不丢失凭据。
    """
    cfg = get_llm_config()
    if not snap:
        return cfg
    if snap.get("provider"):
        cfg["provider"] = snap["provider"]
    if snap.get("base_url"):
        cfg["base_url"] = snap["base_url"]
    if snap.get("model"):
        cfg["model"] = snap["model"]
    if isinstance(snap.get("backup_models"), list):
        cfg["backup_models"] = [m for m in snap["backup_models"] if m]
    aux = snap.get("aux")
    if isinstance(aux, dict):
        cfg["aux"] = {**cfg.get("aux", {}), **aux}
    if snap.get("key_env"):
        cfg["key_env"] = snap["key_env"]
    return cfg


def _model_chain(cfg: dict) -> List[str]:
    """主模型 + 备用模型链（Hermes fallback：429/5xx/超时自动切换）。
    官方 DeepSeek 端点上自动把已下线别名（deepseek-chat/deepseek-reasoner）归一到 v4 现名（deepseek-v4-flash/v4-pro）。"""
    chain = [cfg.get("model") or ""]
    for m in cfg.get("backup_models") or []:
        if m and m not in chain:
            chain.append(m)
    # 先映射再按映射后名字去重（旧主模型+旧备用可能都落到同一个 v4 名）
    mapped = [_map_legacy_model(m, cfg) for m in chain if m]
    out: List[str] = []
    for m in mapped:
        if m not in out:
            out.append(m)
    return out
    return chain or ["gpt-5.4-mini"]


def _chain_for(cfg: dict, task: str = None) -> List[str]:
    """按任务选模型链：aux 槽位命中时把该槽模型放最前，其余仍走 fallback 链。"""
    chain = _model_chain(cfg)
    if task and task in AUX_SLOTS:
        m = _map_legacy_model(((cfg.get("aux") or {}).get(task) or "").strip(), cfg)
        if m and m not in chain:
            return [m] + chain
    return chain


def plugin_tools() -> List[Dict[str, Any]]:
    """Hermes 插件 SDK：把已装插件的 commands 动态转成 LLM function schema（name=<pid>.<cmd>）。"""
    try:
        from plugin_host import list_command_schemas
        return list_command_schemas()
    except Exception:
        return []


def _sanitize_tool_name(name: str) -> str:
    """把含点号的插件工具名（<pid>.<cmd>）净化成 OpenAI 合法名 ^[a-zA-Z0-9_-]+$。"""
    if not name:
        return ""
    clean = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    return clean


def _extract_reasoning(msg: dict) -> str:
    """从 message 提取思考链（OpenAI 兼容: reasoning_content / reasoning / content[].type=thinking）。"""
    rc = msg.get("reasoning_content")
    if isinstance(rc, str) and rc.strip():
        return rc.strip()
    r2 = msg.get("reasoning")
    if isinstance(r2, str):
        return r2.strip()
    if isinstance(r2, list):  # 分块格式
        parts = []
        for b in r2:
            if isinstance(b, dict) and b.get("type") in ("reasoning", "thinking") and isinstance(b.get("text"), str):
                parts.append(b["text"])
            elif isinstance(b, str):
                parts.append(b)
        return "".join(parts).strip()
    return ""


THINKING_PROTOCOL = (
    "【深度思考协议 · 必须遵守】请按以下结构回复，否则视为不合格：\n"
    "<thinking>\n"
    "先把你的真实思考过程写在这里——这部分用户能在「深度思考」折叠块里看到。"
    "应包含：① 拆解用户问题的目标/约束/未知（必要时查长期记忆）；"
    "② 至少列 2 个候选假设，注明各自成立条件；"
    "③ 用工具拿到真实数据来支持或推翻每个假设（不能凭印象编数字）；"
    "④ 在核验后做因果推理，标注关键前提与不确定性；"
    "⑤ 自检：是否把相关性当因果？有没有忽略相反证据？结论是否越过用户风险边界？"
    "⑥ 收敛要点（最终正文要回应的 2-3 条）。\n"
    "思考要详细、真实、像 WorkBuddy 助手那样——不要敷衍套话，"
    "也不要复述上面的指令字眼。\n"
    "</thinking>\n\n"
    "然后再写最终正文（只放结论、关键依据、风险提示），不要再重复思考内容。"
)


def _thinking_sys_msg() -> Dict[str, str]:
    return {"role": "system", "content": THINKING_PROTOCOL}


def _extract_thinking_block(text: str) -> tuple:
    """从 LLM 正文里抽 <thinking>...</thinking> 块，返回 (思考, 清洁正文)。

    v1.5.12 修复泄漏：
    - 模型可能输出**多个**思考块（先推演再复核）→ 全部抽出合并；
    - 第二个块可能**未闭合**（max_tokens 截断/模型忘写闭合标签）→ 尾部未闭合的
      <thinking>... 整段视为思考，绝不让半截推理出现在正文里。
    没有 thinking 块时返回 ("", 原文本)。"""
    if not text:
        return "", text
    import re as _re
    blocks = _re.findall(r"<thinking>(.*?)</thinking>", text, flags=_re.S | _re.I)
    if blocks:
        cleaned = _re.sub(r"<thinking>.*?</thinking>", "", text, flags=_re.S | _re.I)
        # 移除闭合块后，尾部可能还残留未闭合的 <thinking>...（模型连续多个思考块且最后一个被截断）
        m = _re.search(r"<thinking>(.*)$", cleaned, flags=_re.S | _re.I)
        if m:
            blocks.append(m.group(1))
            cleaned = cleaned[:m.start()]
        return "\n\n".join(b.strip() for b in blocks if b.strip()), cleaned.strip()
    # 无闭合块：处理未闭合尾巴（<thinking> 后没有 </thinking>）
    m = _re.search(r"<thinking>(.*)$", text, flags=_re.S | _re.I)
    if m:
        return m.group(1).strip(), text[:m.start()].strip()
    return "", text


def _apply_thinking_protocol(messages: List[Dict[str, Any]], deep_thinking: bool) -> List[Dict[str, Any]]:
    """deep_thinking=True 且当前没有思考协议 system 消息时，把 THINKING_PROTOCOL 插在 messages 最前。"""
    if not deep_thinking:
        return messages
    if not messages:
        return [_thinking_sys_msg()]
    # 已经在首位塞过（同一进程内同一请求）就不重复
    for m in messages[:2]:
        if isinstance(m, dict) and m.get("role") == "system" and THINKING_PROTOCOL in (m.get("content") or ""):
            return messages
    return [_thinking_sys_msg()] + list(messages)


def _is_reasoning_model(model: str) -> bool:
    """通过模型名粗判是否原生支持推理/扩展思考。
    命中则不在 prompt 里塞 thinking 协议（它本来就返回 reasoning_content）；
    否则强制塞协议让模型自己产出 <thinking>。"""
    if not model:
        return False
    n = model.lower()
    return any(k in n for k in ("reasoner", "-thinking", "thinking", "deepseek-r1",
                                "deepseek-v4-pro",
                                "o1-", "o1.", "o3-", "o3.", "o4-", "gpt-5", "qwq"))


def _deep_thinking_budget_tokens(default_max: int) -> int:
    """深度思考开启时，给足 token 让思考块 + 正文都装得下。"""
    return max(default_max, 4000)


def _post(payload: dict, cfg: dict, timeout: int):
    """发一次请求并返回 json；HTTP 4xx/5xx/非 JSON/网络错全部抛出（带真实响应摘要）。"""
    key = resolve_key(cfg)
    if not key:
        raise RuntimeError("no key")
    r = requests.post(_base_url(cfg) + "/chat/completions",
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      json=payload, timeout=timeout)
    # 状态码错：直接抛，requests.HTTPError 包含状态码
    if r.status_code >= 400:
        snippet = r.text[:200].replace("\n", " ").strip()
        raise RuntimeError(f"HTTP {r.status_code} · {r.headers.get('Content-Type','?').split(';')[0]} · {snippet[:160]}")
    # 内容类型：非 JSON（如 Cloudflare challenge HTML）抛错，含真实摘要
    ctype = (r.headers.get("Content-Type") or "").lower()
    if "json" not in ctype:
        snippet = r.text[:200].replace("\n", " ").strip()
        raise RuntimeError(f"非 JSON 响应（Content-Type={ctype.split(';')[0]}）；疑似被网关/Cloudflare 拦截 · {snippet[:160]}")
    try:
        return r.json()
    except Exception as e:
        snippet = r.text[:200].replace("\n", " ").strip()
        raise RuntimeError(f"JSON 解析失败：{type(e).__name__} · {snippet[:160]}")


def _base_url(cfg: dict) -> str:
    return (cfg.get("base_url") or PROVIDERS.get(cfg.get("provider"), {}).get("base_url", "")).rstrip("/")


# ---------------- Agent 工具声明（function calling） ----------------
# 每个工具对应 agent_core 里的一个能力处理器。LLM 决定何时调用、传什么参数。
TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "scan_market",
            "description": "扫描 Binance 全市场行情：在按成交额排序的前 300 个 USDT 现货交易对上动态发现 24h 波动/资金费率异常币种（非固定 20 币），并给出领涨/领跌概况。用户问‘行情/市场/扫描/异常/有什么异动/分析大盘’等时调用，能覆盖任意市值币种。\n**同时覆盖 USDT 永续合约维度**：每个币附带其永续资金费率，用户搜『合约/永续/资金费率/哪个合约费率异常/合约代币/查合约行情』也用本工具（输出含每币资金费率）。\n⚠️ 本工具走公开行情接口，**免费、无需任何授权**——不要为此走 mcp_call。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_risk",
            "description": "基于 Academy 风险教育材料，对当前最强信号生成风险提示。用户问‘风险/风控’时调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_trade",
            "description": "生成带止损/止盈的下单方案（仅方案，不会真实下单）。返回 needs_approval=True，需用户点击确认才会真实提交。用户表达买入/卖出/做多/做空某标的时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "交易对或基础资产，如 BTCUSDT 或 BTC"},
                    "direction": {"type": "string", "enum": ["BULLISH", "BEARISH"], "description": "BULLISH=买入/做多，BEARISH=卖出/做空"},
                    "margin_usdt": {"type": "number", "description": "本金（USDT，默认 50）。用户说了金额（如『用100U买』）时传入"},
                    "leverage": {"type": "integer", "description": "杠杆倍数（默认 1=现货）。仅当用户明确要求杠杆时传入，如 10"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "market_quote",
            "description": "查询单个交易对（USDT 现货或 USDT 永续均可，symbol 如 BTCUSDT / 1000PEPEUSDT）的实时价格、24h 涨跌与永续资金费率。**公开行情接口，免费免授权——不要走 mcp_call**；查不到该交易对时如实说明，可换 scan_market 扫全市场。",
            "parameters": {
                "type": "object",
                "properties": {"symbol": {"type": "string", "description": "交易对，如 BTCUSDT"}},
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_x402",
            "description": "解释 x402 / B402 机器对机器支付流（Binance Agentic Wallet）。用户问‘支付/402/x402’时调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": ("列出已安装的 Binance Skills Hub 技能（含已装的 binance-web3 12 个官方技能：binance-agentic-wallet / query-token-info / query-token-audit / query-address-info / crypto-market-rank / meme-rush / trading-signal / binance-trading-signal / binance-wallet-tracker / binance-leaderboard / binance-tokenized-securities-info / binance-sports-ai-analyzer）。"
                            "用户问‘skills/技能/已装技能/能跑什么’时调用。"),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_skills",
            "description": "发现/搜索技能：按关键词检索本地已装技能与 Binance 官方技能目录，并联网在 GitHub 搜索可安装的 agent skills（含安装状态/星标/描述）。用户说‘发现技能/找找有没有XX技能/搜技能’或想扩展能力时调用。结果里有用户想装的，就用『安装 <名称或URL>』引导用户确认后再装。",
            "parameters": {
                "type": "object",
                "properties": {"keyword": {"type": "string", "description": "搜索关键词，如 memory、context、行情、wallet、支付"}},
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "onchain_ops",
            "description": "介绍链上自动化（Agentic Wallet + Skills）。用户问‘钱包/链上/defi’时调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "meme_watch",
            "description": ("妖币雷达 / 启动前埋伏：**直接调取行情模块 Monster Radar 同源数据**"
                            "(scanner.get_ignition_coins + get_monster_coins，与「行情→妖币雷达」"
                            "页面展示 100% 一致)。\n"
                            "**mode 必须按用户语义传**（不传则默认 both）：\n"
                            "- ignition：用户说『启动前/埋伏/蓄势/点火前/吸筹/二买点』→ 仅返回 ignition(埋伏候选)\n"
                            "- takeoff：用户说『起飞中/追涨/已爆发/拉升中/暴涨中/加速/起飞』→ 仅返回 takeoff(已爆发跟踪)\n"
                            "- both：『妖币/meme/百倍币/十倍币/妖币雷达』等无明确阶段 → 两组都给\n"
                            "**不要**自己拿全市场数据二次筛，也**不要**给 scan_market 的 24h 涨跌幅榜"
                            "（那是已爆发币）。拿到列表后可继续用 market_quote 查某币实时行情、"
                            "propose_trade 给带止损/止盈的下单方案（需用户确认才真实下单）。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["ignition", "takeoff", "both"],
                             "description": "按用户语义选 ignition/takeoff/both；无明确阶段默认 both"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_write",
            "description": "长期记忆管理（跨会话生效）。actions：add=新记（用户说‘记住…’或透露稳定偏好时）；replace=更新已有记忆（需 key）；remove=删除（需 key 或 text 相似匹配；用户手动创建的不可删）；read=查看/检索记忆（text 作关键词过滤，返回 key 列表）。含 API Key/私钥/密码的内容会被拒绝。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "replace", "remove", "read"],
                               "description": "默认 add"},
                    "text": {"type": "string", "description": "要记住的内容（add/replace），或检索关键词（read/remove 匹配用）"},
                    "key": {"type": "string", "description": "replace/remove 时目标记忆的 key"},
                    "kind": {"type": "string", "enum": ["pref", "fact", "event"],
                             "description": "pref=偏好/规则约束，fact=事实背景，event=事件；默认自动判断"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_history",
            "description": "搜索本地历史会话（跨全部对话的标题与消息正文，按最近排序返回会话卡片+命中片段）。用户说‘我之前说过/上次聊到/历史里有没有/之前查过的XX/翻翻旧对话’时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词（支持标题与正文模糊匹配）"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": "Agent 任务清单（跨轮持久化，未完成任务每轮自动注入提醒）。多步任务先拆解成 add 逐条登记，每完成一条立即 toggle 勾选；actions：add=新增（text）、toggle=勾/取消（todo_id 或 text）、remove=删除（todo_id）、list=查看全部、clear_done=清理已完成。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "toggle", "remove", "list", "clear_done"],
                               "description": "默认 add"},
                    "text": {"type": "string", "description": "任务内容（add）或匹配文本（toggle）"},
                    "todo_id": {"type": "string", "description": "任务 id（toggle/remove 精确操作时用，list 可查）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "基础研究技能：抓取一个网页/URL 并返回纯文本内容（用户要求查资料/看某网页/研究某话题时先用它）。",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "完整 URL，含 https://"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_help",
            "description": "列出 Agent 的全部能力。用户问‘帮助/功能/怎么用’时调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gateway_status",
            "description": "查询 Binance 网关连接状态（MCP Agentic / x402 / Agentic Wallet / Skills Hub）：连通、OAuth/登录态、可用工具数。用户问‘网关/连接/授权/状态/为什么连不上’时调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_call",
            "description": "调用 Binance MCP 网关暴露的**私有鉴权**工具（余额/持仓/账户信息/真实下单/划转/OTC 等，需先完成 OAuth 授权）。\n⚠️ **查询公开行情（价格/24h 波动/资金费率/市场扫描）一律用 scan_market / market_quote**——那是免费公开接口，无需 MCP/OAuth；仅在确实需要账户级私有数据时才调用本工具。参数 server=网关名（默认 binance），tool=工具名，arguments=工具参数对象。",
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "MCP 服务器名，默认 binance"},
                    "tool": {"type": "string", "description": "要调用的工具名（如 ticker / account_info 等，先 gateway_status/list 或 tools/list 查）"},
                    "arguments": {"type": "object", "description": "传给该工具的参数对象"},
                },
                "required": ["tool"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "受限本地读取：读取项目目录内文本文件（黑名单目录除外），输出自动截断。用户要求‘读文件/看代码/查某文件/读报告’时调用。参数 path 为项目内相对或绝对路径。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "项目内文件路径，如 src/agent_core.py 或 workspace/策略.md"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "受限本地写入：把内容写到工作区白名单目录（workspace/ 或 .workbuddy/generated/，后者映射到工作区 generated/ 子目录），自动建目录。写文件需用户确认。参数 path 须以 workspace/ 或 .workbuddy/generated/ 开头（相对工作区根，与进程 cwd 无关），或直接用之前工具返回的绝对路径。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "写入路径（必须以 workspace/ 或 .workbuddy/generated/ 开头）"},
                    "content": {"type": "string", "description": "要写入的完整文本内容"},
                    "append": {"type": "boolean", "description": "是否追加（默认 false 覆盖）"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": ("受限本地执行：cwd 锁工作区根（打包态 = 应用安装目录下的 workspace），默认 15s 超时（可传 timeout 调到 120s）、输出截断。运行需用户确认。"
                            "支持的命令：\n"
                            "- 真二进制白名单：python / node / npx / npm / git\n"
                            "- 内置 wrapper（POSIX 跨平台）：ls, cat, head, tail, wc, grep, find, pwd, mkdir, touch, cp, mv, rm, echo\n"
                            "- ls 支持 flag：-l -a -la -al -h -F -R -r -t -S；多路径\n"
                            "- find 支持：-name -iname -path -type f|d -maxdepth N -mindepth N -not\n"
                            "- 支持顶层链式：cmd1 && cmd2；cmd1 || cmd2；cmd1 ; cmd2\n"
                            "- 不支持：单根管道 |、重定向 >、变量替换 $、通配符 *（抛错）。要看前 N 行请用 head -n N <file>，要前 N 个匹配用 grep 然后限制或 head -n N，不要用 | head。\n"
                            "- 拒绝 rm -rf / del /S / format 等危险片段。\n"
                            "可读路径例外（允许读取）：.agents/skills/<name>/SKILL.md（已装技能用法）。\n"
                            "用户要求‘跑一下/执行脚本/算一下/验证/测试/运行命令/看下目录结构’时调用。"
                            "⚠️ 不要用本工具跑已装技能的 cli.mjs——统一走 run_skill。"),
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "命令，如 'python scripts/x.py' 或 'ls -la && find src -maxdepth 2 -type f -name \"*.py\"' "},
                               "timeout": {"type": "integer", "description": "可选，超时秒数（5–120），默认 15s；慢命令（git clone / npm install / 大脚本）建议 60–120"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_task",
            "description": ("管理后台定时任务（cron / interval）。任务会被桌面后端守护线程到点自动执行——**不需要 Agent 开着**。"
                            "action：list 列出全部任务；create 新建（必填 name + time）；update 修改已有任务（job_id + 要改的字段 name/time/task/prompt）；"
                            "delete / toggle / run 用 job_id（toggle 可传 enabled）。\n"
                            "task 三种：daily_scan_report=全市场 Top 行情扫描；meme_scan_report=妖币雷达；"
                            "**custom_prompt=自定义任务（必填 prompt=到点要执行的完整指令，Agent 会带着全部工具无头真实执行）**。\n"
                            "time 支持：`09:00` / `9 点` / `0 9 * * *`（5 字段 cron）/ `interval:30m` 或 `interval:1h`。\n"
                            "用户说『帮我做一个定时任务』『每天早上 9 点分析妖币』『每隔 30 分钟扫一次』『加个日报』"
                            "或给出任何**自定义的周期性指令**（如『每天 9 点总结 BTC 行情并给出关键位』『每小时检查一次资金费率异常』"
                            "『每天早上告诉我昨天发生了什么』）时必须调本工具——自定义要求一律 task=custom_prompt 并把用户的完整要求写进 prompt，"
                            "不要给一句手动话术，也不要用 mcp_call 写系统级 cron。\n"
                            "任务被触发后：内置类型报告写入『BAZZ Agent 日报』会话；custom_prompt 结果写入专属会话「定时任务 · <name>」（不需要用户在场）。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "create", "update", "delete", "toggle", "run"],
                               "description": "list=列任务；create=新建（必填 name+time）；update=修改（job_id+字段）；delete/toggle/run 用 job_id"},
                    "name": {"type": "string", "description": "任务名（create 时必填；update 可选）"},
                    "time": {"type": "string", "description": "时间规格（create 必填；update 可选），如 09:00 / 0 9 * * * / interval:30m"},
                    "task": {"type": "string", "enum": ["daily_scan_report", "meme_scan_report", "custom_prompt"],
                             "description": "daily_scan_report=全市场扫描；meme_scan_report=妖币雷达；custom_prompt=自定义指令任务（配 prompt 使用）"},
                    "prompt": {"type": "string", "description": "task=custom_prompt 时必填：到点执行的完整指令，写清要做什么、关注什么、输出什么格式"},
                    "job_id": {"type": "string", "description": "已有任务 ID（update/delete/toggle/run 必填）"},
                    "enabled": {"type": "boolean", "description": "toggle 时是否启用"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_skill",
            "description": ("执行已安装的 Binance Skills Hub 技能（位于 .agents/skills/<name>/）。\n"
                            "- 带本地脚本的（scripts/cli.mjs）或 binance-agentic-wallet：真实运行（需用户确认）\n"
                            "- 纯指引类（HTTP/扩展型）：直接返回 SKILL.md 说明\n"
                            "用法：skill_name=技能名，args='<子命令> <JSON参数>'，例如 search '{\"keyword\":\"BNB\"}' 或 meme-rush '{\"chainId\":\"CT_501\",\"rankType\":10}'。\n"
                            "**本机内置技能速查（分析代币必用，数据走本机行情网关，稳定可达）：**\n"
                            "- coin-report：args='report <SYMBOL>'（如 report KATUSDT）——一键全维度研报：90日K线/动量/区间分位/费率/OI/大户多空比/恐惧贪婪/战绩，Markdown 落盘\n"
                            "- market-data：args='<子命令> <JSON>'，子命令 klines{\"symbol\":\"BTCUSDT\",\"interval\":\"1d\",\"limit\":90,\"market\":\"futures\"} / funding{\"symbol\":...} / oi{...} / longshort{\"symbol\":...} / fng / overview / liquidations\n"
                            "- track-monitor：args='check 15'——妖币追踪复查\n"
                            "需要先看用法说明时，先 cat .agents/skills/<name>/SKILL.md（已被允许）。\n"
                            "用户要求『跑技能/用技能/执行XX skill/查meme/查聪明钱/扫链上数据/审计代币/查地址持仓/扫市场榜/发币安广场』时调用。\n"
                            "**分析/研究某币（走势/K线/费率/OI/研报）时必须优先本工具调 coin-report 或 market-data——禁止用 run_command 跑 python/node 直连币安 API（受限地区必失败），禁止 fetch_url 抓 api.binance.com**。\n"
                            "**发币安广场（square-post）：** args 直接拼 `node scripts/cli.mjs <子命令> <JSON>`，常用子命令 text(短文)、article(长文 + 标题)、image(图文,<=4 张)、video(视频)。前置 BINANCE_SQUARE_OPENAPI_KEY，缺时去创作者中心 https://www.binance.com/square/creator-center/home 生成。\n"
                            "⚠️ 不要用 run_command 直接调 .agents/skills/X/scripts/cli.mjs——这是已装技能的入口，应当走本 run_skill 工具（它会代你处理 token 化/确认/路径/超时）。\n"
                            "⚠️ **不要把发广场映射成 mcp_call**：MCP binance 网关不含发广场端点；发广场只走本 run_skill。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "已安装技能名（先 list_skills 查）"},
                    "args": {"type": "string", "description": "传给技能的参数串，如 search '{\"keyword\":\"BNB\"}' 或 meme-rush '{\"chainId\":\"CT_501\"}'"},
                },
                "required": ["skill_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clarify",
            "description": ("结构化追问：当用户的需求存在**关键分叉**（目标币种/周期/方向/预算/风险偏好不明，"
                            "不同选择会导向完全不同的操作），而靠猜大概率做错时，把它拆成 1-3 个选择题让用户点选。"
                            "questions 为数组，每项 {question, choices[], recommended?}：question 一句话；"
                            "choices 每题最多 4 个选项（字符串或 {label, description}）；recommended=推荐选项的原文。\n"
                            "**只用于关键决策**——能用合理默认值继续的就不要问（频繁追问很烦）；"
                            "问题会以选项卡片呈现，用户点选后答案作为下一条消息回流；120 秒未选按 recommended/最佳判断继续。\n"
                            "反例：用户说『帮我看看 BTC』→ 不要 clarify，直接看；"
                            "正例：用户说『帮我定个策略』但没说周期和风险承受 → clarify 一次问清。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "description": "1-3 个问题，每项 {question, choices[], recommended?}",
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {"type": "string", "description": "一句话问题"},
                                "choices": {"type": "array", "items": {},
                                            "description": "最多 4 个选项；字符串或 {label, description}"},
                                "recommended": {"type": "string", "description": "推荐选项（须与某 choice 原文一致）"},
                            },
                            "required": ["question"],
                        },
                    },
                },
                "required": ["questions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate",
            "description": ("并行子代理委派：把**相互独立**的子任务拆成 tasks=[{name, prompt}]（最多 4 个），"
                            "每个子任务由一个全新 Agent 并行真实执行（带全部工具，行情/技能/文件/命令都可用，但不能再次委派），"
                            "返回各子任务的最终摘要。\n"
                            "**prompt 必须自包含**——子代理看不到当前对话，把背景、目标、关注点、输出格式写全。\n"
                            "适合：多标的独立研究（BTC/ETH/SOL 各查各的）、多路径同时排查、批量重复性子任务；"
                            "用户说『并行/同时/分开查/每个币都查一遍』或任务天然可拆时调用。\n"
                            "不适合：子任务间有依赖（后一步要用前一步结果）→ 自己顺序做；"
                            "单一简单查询 → 直接用对应工具，别为委派而委派。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "description": "1-4 个子任务，每项 {name, prompt}",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "子任务短名（如『BTC 研究』）"},
                                "prompt": {"type": "string", "description": "完整自包含的任务指令（子代理看不到父对话）"},
                            },
                            "required": ["prompt"],
                        },
                    },
                },
                "required": ["tasks"],
            },
        },
    },
]


def list_models(base_url: str = "", api_key: str = "", provider: str = "") -> List[str]:
    """拉取模型目录；失败回退到 provider 预设。api_key 缺省时按当前配置解析 key_env。"""
    if not api_key:
        api_key = resolve_key(get_llm_config())
    url = (base_url or PROVIDERS.get(provider, {}).get("base_url", "")).rstrip("/") + "/models"
    if not url or url.endswith("/models") is False and not url:
        return PROVIDERS.get(provider, {}).get("models", [])
    try:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        items = data if isinstance(data, list) else data.get("data", [])
        ids = [m["id"] for m in items if isinstance(m, dict) and m.get("id")]
        if ids:
            return ids
    except Exception:
        pass
    return PROVIDERS.get(provider, {}).get("models", [])


def test_connection(base_url: str = "", api_key: str = "", model: str = "", provider: str = "") -> Dict[str, Any]:
    """验证 key 与 base_url 是否可用：先试 /models，失败再试一次极小 chat 补全。
    返回 {ok, model_tried, model_hint?, error/detail}。所有错误都把真实响应摘要带回 UI。
    """
    cfg = _materialize()
    base_url = base_url or cfg.get("base_url") or PROVIDERS.get(provider or cfg.get("provider", ""), {}).get("base_url", "")
    api_key = api_key or cfg.get("api_key", "")
    eff_provider = (provider or cfg.get("provider", "") or "").lower()
    default_model = "deepseek-v4-flash" if eff_provider == "deepseek" else "gpt-5.4-mini"
    model = model or cfg.get("model", "") or default_model
    # 官方 DeepSeek 端点：把 UI/配置里残留的退役别名迁移到现名再测
    if _is_official_deepseek({"provider": provider or cfg.get("provider", ""), "base_url": base_url}):
        model = _map_legacy_model(model, {"provider": "deepseek", "base_url": base_url})
    if not base_url or not api_key:
        return {"ok": False, "model_tried": model,
                "error": "缺少 base_url 或 api_key（可用 key_env 指定环境变量名注入）"}
    base = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    ids: list = []
    # 1) 试模型目录
    try:
        r = requests.get(base + "/models", headers=headers, timeout=10)
        ctype = (r.headers.get("Content-Type") or "").lower()
        if r.ok and "json" in ctype:
            data = r.json()
            items = data if isinstance(data, list) else data.get("data", [])
            ids = [m["id"] for m in items if isinstance(m, dict) and m.get("id")]
            if ids and model in ids:
                # 在目录中 → 直接可用
                return {"ok": True, "model_tried": model, "model_hint": model, "count": len(ids)}
        # 非 JSON / 非 200 / 不在目录：落到第二阶段验证，但先记录第一阶段响应
        first = (f"阶段1 /models {r.status_code} {ctype.split(';')[0]} · " + (r.text[:200].replace('\n', ' ').strip() or "no body"))[:240]
    except Exception as e:
        first = f"阶段1 /models 异常：{type(e).__name__}: {str(e)[:200]}"
    # 2) 试一次极小补全（部分 provider 无 /models，或 model 是目录外的可用别名，
    #    例如 DeepSeek 的 deepseek-v4-flash：/models 不再列出但仍可用 → 以 chat 实测为准）
    try:
        r = requests.post(base + "/chat/completions",
            headers=headers,
            json={"model": model, "messages": [{"role": "user", "content": "ping"}],
                  "max_tokens": 1, "temperature": 0},
            timeout=20)
        ctype = (r.headers.get("Content-Type") or "").lower()
        if r.ok and "json" in ctype:
            body = r.json()
            # 有的网关 200 但无 choices（拦截/鉴权/空响应）→ 仍判失败并给目录首项建议
            if isinstance(body, dict) and (body.get("choices") or body.get("data")):
                return {"ok": True, "model_tried": model, "model_hint": model}
            if isinstance(body, list) and body:
                return {"ok": True, "model_tried": model, "model_hint": model}
            hint = model if model in ids else (ids[0] if ids else model)
            return {"ok": False, "model_tried": model,
                    "detail": f"HTTP {r.status_code} · 200 但空 choices（网关可能拦截该模型）",
                    "model_hint": hint, "error": first}
        snippet = r.text[:240].replace("\n", " ").strip()
        return {"ok": False, "model_tried": model,
                "detail": f"HTTP {r.status_code} {ctype.split(';')[0]} · {snippet}",
                "error": f"{first} ｜ 阶段2 /chat → {r.status_code}"}
    except Exception as e:
        return {"ok": False, "model_tried": model,
                "detail": f"{type(e).__name__}: {str(e)[:240]}", "error": first}


def _summarize_error(err) -> str:
    """把异常 / HTML 响应摘要成一句可读的诊断，便于透传 UI（避免把整页 HTML 塞日志）。"""
    s = str(err or "").strip()
    if not s:
        return ""
    head = s.split("\n", 1)[0]
    kws = ["Cloudflare", "Attention Required", "Just a moment", "cf-chl", "cf_chl",
           "HTTP ", "JSON 解析", "非 JSON", "Forbidden", "SSL", "Connect", "timeout",
           "Connection", "Cloudflare"]
    hits = sorted({k for k in kws if k in head or k in s})
    snip = head[:160]
    return f"{snip}（{', '.join(hits)}）" if hits else snip


def _unwrap_choice(d: dict, model: str) -> dict:
    """取 choices[0].message；网关异常回包（HTTP 200 但 choices 为 null/[] 或带 error）
    抛出带摘要的 RuntimeError —— 否则 d["choices"][0] 直接 NoneType/IndexError，
    且 chat_with_tools 里被裸 except 吞掉，上层完全看不到失败原因。"""
    if not isinstance(d, dict) or not d.get("choices"):
        err = d.get("error") if isinstance(d, dict) else None
        detail = ""
        if isinstance(err, dict):
            detail = str(err.get("message") or err.get("code") or err)[:200]
        elif err:
            detail = str(err)[:200]
        if not detail:
            try:
                detail = json.dumps(d, ensure_ascii=False)[:200]
            except Exception:
                detail = str(d)[:200]
        raise RuntimeError(f"模型 {model} 回包异常（choices 为空）· {detail}")
    msg = d["choices"][0].get("message")
    if not isinstance(msg, dict):
        raise RuntimeError(f"模型 {model} 回包异常（message 缺失）· {json.dumps(d, ensure_ascii=False)[:200]}")
    return msg


def chat(system: str, user: str, temperature: float = 0.6, max_tokens: int = 900,
         llm_cfg: dict = None, task: str = None) -> Optional[str]:
    """一次对话文本（返回 str）；多模型 fallback 链，全部失败返回 None。
    llm_cfg: 会话级 provider 快照覆盖（防漂移）；task: aux 细分槽（reasoning/vision/summarize）。
    llm_cfg.deep_thinking=True 时：
      - messages 前插一份 THINKING_PROTOCOL 让模型在正文前先写 <thinking>...</thinking>
      - token 预算拉到至少 4000，给思考块留足空间
      - 抽出 <thinking> 块返回清洁正文（chat() 返回单字符串即可，
        chat_with_tools() 会同时把 thinking 放 reasoning 字段给前端展示）
    失败时把最近一次具体异常写入模块级 _last_error，供上层透传给用户。
    """
    global _last_error
    _last_error = None
    cfg = _materialize(llm_cfg)
    deep = bool(cfg.get("deep_thinking"))
    msgs = _apply_thinking_protocol(
        [{"role": "system", "content": system}, {"role": "user", "content": user}], deep)
    eff_max = _deep_thinking_budget_tokens(max_tokens) if deep else max_tokens
    for model in _chain_for(cfg, task):
        payload = {"model": model, "messages": msgs,
                   "temperature": temperature, "max_tokens": eff_max}
        try:
            d = _post(payload, cfg, timeout=45)
            msg = _unwrap_choice(d, model)
            text = (msg.get("content") or "").strip()
            rc = _extract_reasoning(msg)
            if rc:
                _last_error = None
                return rc if not text.strip() else text
            if deep and text:
                thinking, clean = _extract_thinking_block(text)
                if thinking:
                    _last_error = None
                    return clean or thinking
            if text:
                _last_error = None
                return text
            _last_error = f"模型 {model} 返回空 content（网关可能拦截或鉴权失败）"
        except Exception as e:
            _last_error = _summarize_error(f"模型 {model} 调用失败：{type(e).__name__}: {str(e).strip()}")
            continue
    return None


def stream_chat(system: str, user: str, temperature: float = 0.6, llm_cfg: dict = None):
    """yield 文本增量；不可用先 yield None 一次。"""
    cfg = _materialize(llm_cfg)
    key = cfg.get("api_key")
    if not key:
        yield None
        return
    try:
        r = requests.post(_base_url(cfg) + "/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": cfg["model"],
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                  "temperature": temperature, "stream": True},
            timeout=60, stream=True)
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            s = line.decode("utf-8", "ignore")
            if not s.startswith("data:"):
                continue
            p = s[5:].strip()
            if p == "[DONE]":
                break
            try:
                d = json.loads(p)
                delta = d["choices"][0]["delta"].get("content")
                if delta:
                    yield delta
            except Exception:
                continue
    except Exception:
        yield None


def chat_with_tools(messages: List[Dict[str, Any]], tools: List[Dict[str, Any]] = None,
                    temperature: float = 0.4, max_tokens: int = 1200,
                    llm_cfg: dict = None, task: str = None) -> Optional[Dict[str, Any]]:
    """单轮 function-calling（多模型 fallback 链）。
    返回 {"content": str, "tool_calls": [...], "reasoning": str, "model": str} 或 None。
    llm_cfg: 会话 provider 快照覆盖（防漂移）；task: aux 槽位模型优先。
    llm_cfg.deep_thinking=True 时：messages 前插 THINKING_PROTOCOL 并放足 token，
    回包时若没拿到原生 reasoning_content，就把 <thinking>...</thinking> 从 content 抽出来
    放到 out["reasoning"]，并把 content 清洗掉该块（保证正文不带思考内容）。
    """
    cfg = _materialize(llm_cfg)
    deep = bool(cfg.get("deep_thinking"))
    eff_max = _deep_thinking_budget_tokens(max_tokens) if deep else max_tokens
    base_msgs = _apply_thinking_protocol(messages, deep)
    for model in _chain_for(cfg, task):
        payload = {"model": model,
                   "messages": json.loads(json.dumps(base_msgs)) if tools else base_msgs,
                   "temperature": temperature, "max_tokens": eff_max}
        name_map: Dict[str, str] = {}
        if tools:
            # OpenAI 工具名只允许 [a-zA-Z0-9_-]：插件 schema 用 "<pid>.<cmd>"（如
            # scout-signals.hot）会触发 400，这里净化成合法名并在回包时还原。
            clean_tools: List[Dict[str, Any]] = []
            for t in tools:
                fn = (t or {}).get("function", {})
                orig = (fn or {}).get("name", "")
                clean = _sanitize_tool_name(orig)
                if clean != orig:
                    name_map[clean] = orig
                if clean:
                    clone = json.loads(json.dumps(t))
                    clone["function"]["name"] = clean
                    clean_tools.append(clone)
            payload["tools"] = clean_tools or tools
            payload["tool_choice"] = "auto"
            # 历史消息里上一轮的 assistant tool_calls 也带点号 → 一并净化（否则同样 400）
            for mm in payload["messages"]:
                if isinstance(mm, dict) and mm.get("role") == "assistant":
                    tcs = mm.get("tool_calls")
                    if tcs:
                        for tc in tcs:
                            fn = (tc or {}).get("function", {})
                            orig = (fn or {}).get("name", "")
                            clean = _sanitize_tool_name(orig)
                            if clean != orig:
                                name_map[clean] = orig
                                fn["name"] = clean
        try:
            d = _post(payload, cfg, timeout=120)
            msg = d["choices"][0]["message"]
            content = msg.get("content") or ""
            tcs = msg.get("tool_calls") or []
            tool_calls = []
            for tc in tcs:
                fn = tc.get("function", {})
                raw = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw) if isinstance(raw, str) else raw
                except Exception:
                    args = {}
                raw_name = fn.get("name") or ""
                tool_calls.append({"id": tc.get("id"),
                                   "name": name_map.get(raw_name, raw_name),
                                   "args": args})
            out: Dict[str, Any] = {"content": content, "tool_calls": tool_calls, "model": model}
            rc = _extract_reasoning(msg)
            if rc:
                out["reasoning"] = rc
                # 兼容 v4/推理模型「只回 reasoning_content、content 为空」的形态：
                # 无工具调用时把思考链兜成正文，避免用户看到空回复
                if not content.strip() and not tool_calls:
                    out["content"] = rc
            if deep and content:
                # v1.5.14 修复泄漏：原为 elif —— 推理模型（deepseek-v4-flash 等）既回原生
                # reasoning_content 又在 content 里写 <thinking> 块时，elif 短路导致
                # 思考块留在正文被当回答流式输出。改为独立判断，两种思考合并进 reasoning。
                thinking, clean = _extract_thinking_block(content)
                if thinking:
                    out["reasoning"] = ((out.get("reasoning", "") + "\n\n" + thinking).strip())
                    out["content"] = clean
            _last_error = None
            return out
        except Exception as e:
            # 之前这里静默 continue —— 网关异常回包/工具名 400 等全部无迹可查
            _last_error = _summarize_error(f"模型 {model} 工具调用失败：{type(e).__name__}: {str(e).strip()}")
            continue
    return None
