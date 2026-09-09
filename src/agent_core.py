"""BAZZ Agent - 统一 Agent 核心（Hermes 风格）

能力：
- 规则意图路由 + 可选 LLM 润色/闲聊（多模型，见 llm.py）
- 人格注入（SOUL.md）
- 长期记忆读写（state.memory，跨会话）
- 并行子代理（分板块扫描，线程池）
- 安全审批 gate（真实下单 / 安装 / 写操作需二次确认）
- 流式事件 run_stream() -> NDJSON（meta/tool/text/data/approval/done）
所有交易默认「待确认」。
"""
import os
import re
import time
import json
import hashlib
import threading
from typing import Optional, Iterator, Dict, Any
from concurrent.futures import ThreadPoolExecutor

# ---------- tool dict normalization ----------
def _ok_status(t: dict) -> bool:
    """把各家模块写的 status 字段统一成 ok: bool（前端就靠这个判断卡片态）。"""
    if not isinstance(t, dict):
        return False
    if "ok" in t:
        return bool(t["ok"])
    st = str(t.get("status", "")).lower()
    return st not in ("", "error", "fail", "failed", "failed:", "bad")


_HEAL_MAX = 3  # 同一请求内工具失败的最大自愈轮次（永续/小币类查询需要更长窗口）
# 单次生成的 agent 轮次上限：每轮 = 一次模型决策 + 一批工具执行。
# 14 轮足够容纳长链研究（市场扫描→多币逐一 quote→资金费率/量能核对→风险→成稿），
# 同时避免模型因上限过低而在数据没拿全时被强断（用户反馈过『轮次太少自动结束』）。
MAX_AGENT_TURNS = 14


def _tool_fail_desc(res) -> str:
    """工具结果里含失败卡片/错误标记时返回失败描述；成功返回空串。"""
    if not isinstance(res, dict):
        return ""
    if res.get("ok") is False:
        return str(res.get("error") or res.get("reply") or "执行失败")[:300]
    for t in res.get("tools") or []:
        if isinstance(t, dict) and not _ok_status(t):
            return str(t.get("detail") or t.get("name") or "执行失败")[:300]
    return ""


def _heal_hint(fail: str, remain: int) -> str:
    """自愈提示词：remain>0 引导模型修复重试；remain==0 强制停止并如实上报。"""
    if remain > 0:
        return (f"\n\n⚠️ 刚才的工具执行失败了：{fail}\n"
                f"【自愈指令】分析失败原因（参数？格式？网络？权限？）。\n"
                f"- 若 args 与上一次完全相同——**立即停止重试本调用**，改为：换工具/换路径（如发币安广场走 run_skill 而非 mcp_call）、"
                f"向用户索要必要凭据、或把失败原文与建议直接呈现。\n"
                f"- 修复点不明确时，也优先停下如实告知用户，不要盲目循环同 args。"
                f"（剩余自愈次数 {remain}。）")
    return (f"\n\n⛔ 工具执行失败：{fail}\n"
            f"【自愈次数已用尽】立即停止调用工具，不要再尝试。直接向用户如实说明："
            f"发生了什么、已尝试了哪些方案、建议下一步怎么做。")


def _norm_tool(t: dict) -> dict:
    """补齐 ok / name / detail，保证前端渲染稳定。"""
    if not isinstance(t, dict):
        return {"name": str(t), "ok": False}
    nt = dict(t)
    nt["ok"] = _ok_status(t)
    nt["name"] = nt.get("name") or nt.get("tool") or nt.get("command") or "tool"
    if "detail" not in nt and "result" in nt:
        nt["detail"] = str(nt["result"])[:300]
    return nt

def _norm_tools(items):
    return [_norm_tool(t) for t in (items or [])]


# ---------- 工具输出落盘（Hermes tool_output_limits 语义） ----------
# 超过阈值（20KB）的工具输出不硬塞给模型：完整内容落盘 workspace/spill/，回传截断文本+句柄路径，
# 模型需要完整内容时可用 read_file 读句柄（spill/ 在工作区内，read_file 可达）。
TOOL_SPILL_THRESHOLD = 20000


# ---------- delegate 并行子代理（Hermes delegate_tool 语义，v1.4.6） ----------
# 子代理 = 全新会话（无父对话历史）+ 工具集减去 delegate（防递归）；
# 父级只看每个子任务的最终摘要（中间工具过程不回流），摘要按预算截断。
DELEGATE_MAX_TASKS = 4          # 单次委派子任务上限（Hermes max_concurrent_children 收敛）
DELEGATE_MAX_CONCURRENT = 3     # 并行线程数
DELEGATE_SUMMARY_BUDGET = 2500  # 每个子任务回传父级的摘要预算（字符）
DELEGATE_TIMEOUT_TOTAL = 420    # 全部子任务总超时（秒）；超时任务标记失败，线程自然结束不强杀
DELEGATE_BLOCKED_TOOLS = {"delegate"}  # 子代理禁用工具（防递归；后续可按需扩充）


def _filter_tool_schemas(tools: list, blocked: set = None) -> list:
    """按名字过滤工具 schema（子代理禁用集）。"""
    if not blocked:
        return tools
    return [t for t in tools if isinstance(t, dict) and t.get("function", {}).get("name") not in blocked]


def _run_delegate(args: dict) -> Dict[str, Any]:
    """并行子代理委派：tasks=[{name, prompt}]，每个子任务一个全新 Agent 循环（带工具、禁 delegate）。
    prompt 必须自包含——子代理看不到父对话历史。返回各子任务最终摘要。"""
    tasks_raw = args.get("tasks") or []
    if isinstance(tasks_raw, dict):
        tasks_raw = [tasks_raw]
    if isinstance(tasks_raw, str):
        tasks_raw = [{"prompt": tasks_raw}]
    tasks = []
    for i, item in enumerate(tasks_raw[:DELEGATE_MAX_TASKS]):
        if isinstance(item, str):
            item = {"prompt": item}
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt") or item.get("goal") or "").strip()
        if not prompt:
            continue
        name = str(item.get("name") or item.get("title") or f"子任务{i + 1}")[:40]
        tasks.append({"name": name, "prompt": prompt})
    if not tasks:
        return {"reply": "delegate 需要至少一个子任务：tasks=[{name, prompt}]。prompt 必须自包含"
                         "（子代理看不到当前对话，把背景/目标/输出要求写全）。",
                "tools": [{"icon": "🤖", "name": "委派子代理", "status": "error", "detail": "缺 tasks"}]}
    results: list = [None] * len(tasks)

    def _worker(idx: int, t: dict):
        acc = {"text": "", "reply": ""}
        try:
            for ev in _run_llm_agent(t["prompt"], auto_exec=False, history=None,
                                     locale="zh", cid="", blocked_tools=DELEGATE_BLOCKED_TOOLS):
                et = ev.get("type")
                if et == "text":
                    acc["text"] += ev.get("delta") or ""
                elif et == "done":
                    if ev.get("reply"):
                        acc["reply"] = ev["reply"]
                    if ev.get("needs_approval"):
                        acc["reply"] += "\n（子任务停在待确认操作——父任务无法代批，请人工处理。）"
                        break
                    if ev.get("needs_clarify"):
                        acc["reply"] += "\n（子任务发起追问但无人应答，已终止。）"
                        break
        except Exception as e:
            acc["reply"] = f"子任务异常：{type(e).__name__}: {str(e)[:200]}"
        summary = (acc["reply"] or acc["text"] or "（无输出）").strip()[:DELEGATE_SUMMARY_BUDGET]
        results[idx] = {"name": t["name"], "summary": summary,
                        "ok": not summary.startswith("子任务异常")}

    import concurrent.futures as _cf
    pool = _cf.ThreadPoolExecutor(max_workers=min(len(tasks), DELEGATE_MAX_CONCURRENT),
                                  thread_name_prefix="delegate")
    try:
        futs = [pool.submit(_worker, i, t) for i, t in enumerate(tasks)]
        try:
            for _f in _cf.as_completed(futs, timeout=DELEGATE_TIMEOUT_TOTAL):
                pass
        except _cf.TimeoutError:
            pass  # 超时：未完成子任务下面标记，线程不强杀（自然结束）
    finally:
        pool.shutdown(wait=False)
    out = []
    for i, t in enumerate(tasks):
        if results[i] is None:
            out.append({"name": t["name"], "summary": "子任务超时未完成（可能仍在后台运行）", "ok": False})
        else:
            out.append(results[i])
    ok_n = sum(1 for r in out if r["ok"])
    lines = [f"**并行子代理完成（{ok_n}/{len(out)} 成功）**", ""]
    for r in out:
        mark = "✅" if r["ok"] else "⚠️"
        lines.append(f"- {mark} **{r['name']}**：{r['summary']}")
    return {"reply": "\n".join(lines),
            "tools": [{"icon": "🤖", "name": f"委派 · {r['name']}",
                       "status": "success" if r["ok"] else "error",
                       "detail": r["summary"][:80]} for r in out],
            "intent": "delegate", "data": {"delegated": out}}


def _spill_tool_output(tool_name: str, content: str):
    """把超长工具输出写入 <WORKSPACE>/spill/<ts>_<tool>.txt，返回句柄路径；失败返回 None。"""
    try:
        from workspace import WORKSPACE
        d = os.path.join(WORKSPACE, "spill")
        os.makedirs(d, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_\-]", "_", str(tool_name))[:40] or "tool"
        path = os.path.join(d, f"{time.strftime('%Y%m%d_%H%M%S')}_{safe}.txt")
        with open(path, "w", encoding="utf-8", errors="replace") as f:
            f.write(content)
        return path
    except Exception:
        return None


def _tool_msg_from_res(name: str, res: dict) -> str:
    """把工具结果组装成回传给 LLM 的消息：优先原始 data JSON，超限落盘截断（v1.4.5）。"""
    raw_data = res.get("data")
    if raw_data not in (None, {}):
        try:
            full = f"[{name} 返回的原始数据]\n" + json.dumps(raw_data, ensure_ascii=False)
        except Exception:
            full = res.get("reply") or ""
    else:
        full = res.get("reply") or ""
    if len(full) > TOOL_SPILL_THRESHOLD:
        spilled = _spill_tool_output(name, full)
        head = full[:2200]
        if spilled:
            return (f"{head}\n\n…（输出过长，共 {len(full)} 字符，已截断。"
                    f"完整输出已存盘：{spilled}，可用 read_file 读取）")
        return head
    return full[:2200]

from scanner import scan_universe, scan_symbols, market_movers, SECTORS, get_snapshot, get_funding_rates, get_top_liquid_symbols
from risk_guard import check_risks, format_risk_report
from executor import confirm_and_place
from reporter import format_report
from x402_client import demo_x402_flow
from skills_client import get_catalog, install_skill, list_installed, AGENTS_DIR
from mcp_client import mcp_status, mcp_list_tools, list_servers, call_tool
import llm

# ---- 行情口径：Agent 分析面向"全市场动态池"，不再固定 Top 20 ----
SCAN_UNIVERSE_SIZE = 300   # 覆盖按成交额排序的前 300 交易对（≈全部流动性市场）
SCAN_MIN_CHANGE = 2.0      # 进入异动候选的 24h 波动门槛（%）
SCAN_MAX_SIGNALS = 12      # 单次返回的信号上限


def _market_scan() -> list:
    """全市场异动扫描：在成交额前 SCAN_UNIVERSE_SIZE 的交易对上动态找信号，
    返回按强度排序的列表（可能为空 = 市场平静/网络异常）。"""
    try:
        return scan_universe(min_change_pct=SCAN_MIN_CHANGE, min_funding=0.01,
                             universe_size=SCAN_UNIVERSE_SIZE, max_signals=SCAN_MAX_SIGNALS)
    except Exception:
        return []


def _best_signal() -> dict:
    """全市场最强信号；无信号时回退到成交额第一（BTCUSDT 等）的真实实时行情，
    保证下游永远拿到一个有价格的分析对象。"""
    sigs = _market_scan()
    if sigs:
        return sigs[0]
    try:
        rows = scan_symbols(["BTCUSDT"], force=True)
        if rows:
            return rows[0]
    except Exception:
        pass
    return {"symbol": "BTCUSDT", "price": 0, "change_pct": 0, "funding_rate": 0}

try:
    from state import set_memory, get_memory, list_memory, bump_memory_hits, search_memory
except Exception:
    def set_memory(k, v, kind="fact", source="auto"): pass
    def get_memory(k, d=""): return d
    def list_memory(): return []
    def bump_memory_hits(keys): pass
    def search_memory(q, limit=10): return []

# 人格
try:
    _soul_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "SOUL.md")
    with open(_soul_path, "r", encoding="utf-8") as _f:
        SOUL = _f.read()
except Exception:
    SOUL = "你是 BAZZ Agent，一个 Binance 专属 AI 交易助手。"

PERSONA_NAME = "BAZZ Agent"

# 高危指令拦截（安全）
DANGEROUS = ["清空账户", "全仓", "all in", "梭哈", "借款", "杠杆 100", "liquidate", "transfer all", "提现全部"]


# 记忆注入总字符预算（v1.4.1 对齐 Hermes char limits：防记忆膨胀撑爆 system prompt）
_MEMORY_BUDGET = 3500
# 提示注入清洗：记忆文本里混入的「指令性」内容一律剥离后再入 prompt
_MEM_INJECT_PAT = re.compile(
    r"(忽略(以上|之前|上面)[^\n。；;]*|ignore\s+(all\s+)?(previous|above|prior)[^\n。;]*"
    r"|disregard[^\n。;]*|system\s*prompt[^\n。;]*|你(现在)?是[^\n。；;]{0,20}(系统|管理员|开发)"
    r"|</?[a-z_]{1,20}>|<\|[^>]{0,20}\|>)",
    re.IGNORECASE)


def _sanitize_mem(text: str) -> str:
    """记忆注入前清洗：剥离疑似提示注入的片段、压成单行、去首尾空白。"""
    t = str(text or "")
    t = _MEM_INJECT_PAT.sub("□", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:500]


def memory_context() -> str:
    """把长期记忆注入 system prompt（v1.4.1 精选式）。

    - 按 kind 字段分组（写入时已定类型，不再靠关键词猜）：
      pref → 【对用户的了解·请遵守】；fact/event → 【长期记忆·背景】；
    - 组内按 updated_at 新者优先，总字符预算 _MEMORY_BUDGET 封顶；
    - 每条注入前 _sanitize_mem 清洗提示注入；命中的 key 异步 bump_memory_hits。
    """
    mem = list_memory()
    if not mem:
        return ""
    rank = {"pref": 0, "fact": 1, "event": 2}
    for m in mem:
        m["_san"] = _sanitize_mem(m.get("value"))
    mem = [m for m in mem if m["_san"]]
    mem.sort(key=lambda m: (rank.get(m.get("kind"), 1), -(m.get("updated_at") or 0)))
    prefs, facts, used = [], [], 0
    for m in mem:
        if used >= _MEMORY_BUDGET:
            break
        line = f"- {m['_san']}"
        if used + len(line) > _MEMORY_BUDGET:
            break
        (prefs if m.get("kind") == "pref" else facts).append(line)
        used += len(line)
        m["_hit"] = True
    if not prefs and not facts:
        return ""
    blocks = []
    if prefs:
        blocks.append("【对用户的了解（记忆驱动行为 · 请遵守）】\n" + "\n".join(prefs[:12]))
    if facts:
        blocks.append("【长期记忆·背景】\n" + "\n".join(facts[:16]))
    hit_keys = [m["key"] for m in mem if m.get("_hit")]
    if hit_keys:
        threading.Thread(target=bump_memory_hits, args=(hit_keys,), daemon=True,
                         name="bazz-mem-hits").start()
    return "\n".join(blocks) + "\n" if blocks else ""


def todo_context() -> str:
    """把未完成任务注入 system prompt（v1.4.4，学习 Hermes todo_tool：长任务跨轮不烂尾）。"""
    try:
        from state import list_todos
        todos = [t for t in list_todos(open_only=True) if _sanitize_mem(t.get("text"))]
    except Exception:
        return ""
    if not todos:
        return ""
    lines = [f"- [{t['id'][:8]}] {_sanitize_mem(t['text'])[:100]}" for t in todos[:12]]
    return ("【当前任务清单·进行中（用户托付的任务，完成后用 todo_write 勾选；"
            "别在未完成时装作已做完）】\n" + "\n".join(lines) + "\n")


_EN_LANG_BLOCK = ("[LANGUAGE & OUTPUT RULES - HIGHEST PRIORITY]\n"
  "- The product UI is in English. Write your ENTIRE reply - conclusions, tables, bullets and follow-ups - in English.\n"
  "- Keep tickers and technical terms as-is (BTC/USDT, funding rate, APY, RWA...).\n"
  "- If the user writes in another language (e.g. Chinese), still reply in English unless they explicitly ask otherwise.\n"
  "- Every number must come from tool results; never invent prices, APYs or statistics.\n"
  "- Tools return raw data, not final copy: rewrite it into a natural English answer - tables only when they help, keep sentences tight.\n"
  "- Never open with filler like \"I'll...\" / \"Let me...\"; call the tool or state the conclusion directly.\n"
  "- The routing rules below (Chinese examples like 启动前/起飞中) still apply when the user describes those situations in any wording.\n\n")


def _lang(locale: str = None) -> str:
    """'en' when locale starts with en, else 'zh'."""
    return "en" if locale and str(locale).lower().startswith("en") else "zh"


def _system_prompt(locale: str = "zh") -> str:
    s = (SOUL + "\n\n你是 Binance Agent OS 的中文交易助手(BAZZ Agent)。\n"
            "你拥有一组工具（function calling），用它们完成用户的真实请求。\n"
            "【强制规则 — 必须遵守】\n"
            "1) 涉及行情/异常 → 立即调用 scan_market；问某币价格/合约行情/资金费率 → market_quote；\n"
            "   **公开行情（现货价量 + USDT 永续资金费率）一律走 scan_market / market_quote——免费免授权，"
            "绝对禁止用 mcp_call 查行情**（mcp_call 仅用于账户级私有数据：余额/持仓/真实下单等）；\n"
            "   风险/风控 → check_risk；买卖/多空 → propose_trade（仅出方案，下单需确认）；\n"
            "   支付/x402/402 → explain_x402；skills/技能 → list_skills；\n"
            "   链上/钱包/defi → onchain_ops；『记住…』→ memory_write；能力介绍 → get_help；\n"
            "   多步任务拆解/『加个任务/任务完成/清单』→ todo_write（建/勾/删任务，完成一条立刻勾一条）；\n"
            "   **妖币 / 启动前 / 埋伏 / 蓄势 / meme / 百倍币 → 立即用 meme_watch（**直接调取行情模块 Monster Radar 同源数据**——scanner.get_ignition_coins/get_monster_coins，与「行情→妖币雷达」展示内容 100% 一致），"
            "**不要**自己用价量/费率二次筛；拿到候选后可用 market_quote 查某币实时行情、propose_trade 给方案。\n"
            "   **mode 必须按用户语义传**：用户说『启动前/埋伏/蓄势/点火前/吸筹/二买点』→ `mode='ignition'`；说『起飞中/追涨/已爆发/拉升中/暴涨中/加速/起飞』→ `mode='takeoff'`；说『妖币/meme/百倍币/十倍币』等无明确阶段 → `mode='both'`。\n"
            "   **发币安广场 / Square 发文 / 发推 / 发图文 / 『把这篇分析发出去』→ 必须用 run_skill(\n"
            "       skill_name='square-post', args='<text|article|image|video 子命令 + JSON 参数>'\n"
            "     ),不要走 mcp_call —— MCP binance 网关只有公开行情/账户/交易端点，没有发广场的能力，OAuth 授权也帮不上**。\n"
            "   **『帮我做个定时任务 / 每天 9 点分析妖币 / 每天早上定时扫描 / 每隔 30 分钟扫一次 / 加个日报 / 加个定时提醒 / cron / 自动定时』→ 立即用 schedule_task(action='create', name=…, time=…, task=…) 在后台真实注册 cron / interval 任务（不是给一句手动话术，也不要走 mcp_call 写系统级 cron）**。time 支持 `09:00`/`9 点`/`0 9 * * *`/`interval:30m`；task 默认 daily_scan_report，做妖币雷达传 meme_scan_report；**用户给出自定义周期指令（如『每天 9 点总结 BTC 行情并给关键位』）→ task='custom_prompt' 且把完整指令写进 prompt 参数（Agent 到点带全部工具无头真实执行）**。内置任务结果写『BAZZ Agent 日报』会话，custom_prompt 写专属会话「定时任务 · <name>」（都不需要用户在场）。**\n"
            "   **需求存在关键分叉（币种/周期/方向/预算不明且猜错代价高）→ 用 clarify 工具发结构化选择题让用户点选；能用合理默认值继续就不要问**。\n"
            "   **多个相互独立的子任务（多标的各查各的/多路径排查）→ 用 delegate 并行委派子代理（tasks=[{name, prompt}]，prompt 必须自包含）；子任务间有依赖就自己做**。\n"
            "2) **绝不要先用文字叙述『我先调用 xxx』或『正在调用 xxx』！**\n"
            "   直接在 reply 之外、以 tool_calls 形式调用；用户必须看到真实数据。\n"
            "   调完拿到数据后再用中文总结结论；不要在 text 里编造数字。\n"
            "3) 涉及交易必提示风险，不替用户做决策；propose_trade 只生成方案+needs_approval，不直接下单。\n"
            "4) 多轮迭代：如果工具返回不充分，可以再调一次别的工具补足信息。\n"
            "5) 同一个工具不要连续重复调用——拿到结果后直接总结，除非用户明确要求刷新。\n"
            "6) **绝不使用『我先…』『让我…』『我来…』『好的，让我…』『先看一下…』等固定客套开场白**。\n"
            "   直接进入结论或直接调用工具；开场要多样化，可以用『当前 ETH 在…/ 直接看：…/ 行情来了：…/ 拉一下：…/ 现在读：…』等不同起手，\n"
            "   或干脆不要 preamble——tool_call 已经能展示动作，用户不需要预告。\n"
            "7) **工具返回给你的是原始数据（JSON/要点），不是成稿。请基于真实数字自己组织语言回答**，\n"
            "   像朋友给你讲行情一样自然：需要表格才给表格、一句能说清就别堆砌列表、有明显信号才说『值得注意』。\n"
            "   每次回答的句式、详略、先后随问题与数据而变化——同一个问题隔一阵再问，因为数据变了，话也会不一样。\n"
            "   回答风格不限，可以偶尔轻松一点，但行情数字必须来自工具结果，绝不编造。\n"
            "8) **工具执行失败时不要放弃、也不要假装成功：主动自我修复**——分析报错原因"
            "（参数/格式/网络/权限），修正参数后重试，或改换更合适的工具/路径完成同一目标；\n"
            "   同一意图最多自动修复 3 轮。若工具结果里出现『自愈次数已用尽』的提示，"
            "**别直接摆烂**：轮次耗尽兜底时，把可换路径一次性列给用户（`fapi 公开端点 ticker/24hr` 直查合约、`run_skill` 调官方 `binance-agentic-wallet`/`binance-leaderboard`/`trading-signal`、`fetch_url` 抓公开 Binance 页、`run_command` 让我本地 Python 直请求 fapi），并自行尝试其中至少一条再总结——尤其当目标是『某个只在永续上的币』时，先把 `fapi /fapi/v1/ticker/24hr?symbol=XXX` 与 `/fapi/v1/premiumIndex` 这两条公开免授权接口试一遍再说。\n"
            "9) **深度思考（deep-thinking 协议）**：行情归因/是否交易/策略对比/风险判断等分析类请求，"
            "先在思考链里按『拆解→列假设→用工具核验→推理→自检→收敛』走一遍再回答：\n"
            "   - 至少列 2 个假设并用真实工具数据支持/推翻，不凭印象编数字；"
            "自检时反问『有无相反证据/是否把相关性当因果/有没有越过用户风险边界（查记忆）』；\n"
            "   - 支持思考链的模型把推理过程放进 reasoning 字段（前端会折叠展示），最终回答只放结论+依据+风险；\n"
            "   - 拿不准就直说『无法确定』，绝不硬编。简单查询（单个价格/是否存在）可跳过，直接答。\n"
            "10) **收尾自查（防止答一半就结束）**：当用户要的是分析/研究/对比时，第一轮拿到数据不要急着收尾——\n"
            "   先对照需求自查：价格有了，那资金费率？24h 量能？相对大盘强弱？历史高低点？风险与止损参考？\n"
            "   缺哪补哪（market_quote 逐项补 / scan_market 看大盘背景 / run_skill 查链上与官方榜单），补齐后再给结构化结论；\n"
            "   用户只要『报个价/一句话快答』、或所需数据已齐全时则立即收尾——不要为凑轮数空转。\n"
            + memory_context() + todo_context())
    if _lang(locale) == "en":
        s = _EN_LANG_BLOCK + s
    return s


def _detect(message: str) -> str:
    t = message.lower()
    # 发现技能（先于 记忆/技能/onchain 等关键词，避免被内部词抢先）
    if re.search(r"(发现|搜索|查找|找找|找一个|推荐|找一下|推荐一个).{0,12}(技能|skill|插件|能力)", message, re.I): return "discover"
    if re.search(r"(有没有|有什么|有什么好用的).{0,10}(技能|skill|插件)", message): return "discover"
    if re.search(r"记住|笔记|记录|记一下|存一下", message): return "memory_write"
    if re.search(r"你?记得|记忆|我之前|我的偏好|我告诉过你", message): return "memory_read"
    if any(k in t for k in ["并行", "全面扫描", "所有板块", "板块", "parallel"]): return "parallel"
    if (re.search(r"定时|cron|每隔|日报|schedule|自动定时|自动.{0,4}扫描|帮我做.*定时|每天.{0,5}点|定时提醒", t)
            or re.search(r"每天.{0,8}(分析|扫|检查|看|提醒|推送)", message)): return "cron"
    if any(k in t for k in ["scan", "扫描", "数据", "分析", "异常", "看看",
                            "行情", "市场", "大盘", "涨跌", "涨幅", "异动", "波动", "走势",
                            "合约行情", "合约价格", "合约代币", "永续合约", "资金费率", "funding", "perp"]): return "scan"
    if any(k in t for k in ["risk", "风险", "风控", "check"]): return "risk"
    # 链上 / 钱包 交易类（含交易动作词，如「用 agent 钱包买入」）→ 优先归 onchain，
    # 避免被下方 CEX execute（买入/市价…）抢走路由到交易所下单。
    if any(k in t for k in ["钱包", "wallet", "chain", "链上", "defi", "质押", "baw", "agentic", "swap"]) \
            and any(k in t for k in ["买", "卖", "buy", "sell", "swap", "兑换", "market", "limit",
                                     "order", "交易", "下单", "定投", "建仓"]):
        return "onchain"
    if any(k in t for k in ["确认下单", "execute", "下单", "buy", "sell", "执行", "交易", "建仓",
                              "买入", "卖出", "市价", "限价", "做多", "做空", "开仓", "平仓",
                              "market", "limit", "long", "short", "order"]): return "execute"
    if any(k in t for k in ["report", "报告", "总结"]): return "report"
    if any(k in t for k in ["pay", "支付", "x402", "b402", "402"]): return "payment"
    if any(k in t for k in ["install", "安装", "add skill"]): return "install"
    if any(k in t for k in ["chain", "链上", "defi", "质押", "wallet", "钱包"]): return "onchain"
    if re.search(r"妖币|meme|启动前|埋伏|蓄势|将爆发|蓄势待发|潜力币|百倍币|十倍币", t): return "meme"
    if any(k in t for k in ["skill", "skills", "技能"]): return "skills"
    if any(k in t for k in ["tool", "tools", "mcp", "工具", "接口"]): return "tools"
    if any(k in t for k in ["help", "帮助", "功能", "怎么用"]): return "help"
    return "chat"


# 常见基础资产（用于从自然语言解析交易对）
_TRADE_BASE = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT",
               "MATIC", "TRX", "LTC", "NEAR", "TON", "ARB", "OP", "APT", "SUI", "PEPE",
               "WLD", "FIL", "ETC", "BCH", "XLM", "ATOM", "INJ", "TIA", "SEI", "RNDR", "ENA"]
_QUOTES = ["USDT", "USDC", "FDUSD", "TUSD", "TRY", "BRL", "EUR"]


def _parse_trade(message: str):
    """从自然语言提取 (symbol, direction)。支持「买入 BTC」「卖出 ETH 市价」「BTCUSDT 做多」等。"""
    direction = "BULLISH"
    if re.search(r"卖出|sell|做空|short|看空|空", message, re.I):
        direction = "BEARISH"
    # 显式 BASEQUOTE 形式（如 BTCUSDT）
    m = re.search(r"\b([A-Z]{2,10})(USDT|USDC|FDUSD|TUSD)\b", message.upper())
    if m:
        return f"{m.group(1)}{m.group(2)}", direction
    # 仅给基础资产（如 BTC）→ 默认 USDT 交易对
    for b in _TRADE_BASE:
        if re.search(rf"\b{b}\b", message.upper()):
            return f"{b}USDT", direction
    return None, direction


def _refuse_dangerous() -> dict:
    return {"reply": "⛔ 出于安全考虑，我拒绝执行高危操作（如全仓、清空账户、超高杠杆）。"
                      "请使用受控的下单方案（带止损/止盈、需确认），并自担风险。",
            "tools": [{"icon": "🛡️", "name": "安全拦截", "status": "warn", "detail": "高危指令已阻止"}]}


# ---------------- 各意图处理（返回 dict） ----------------

def _run_scan(message: str = ""):
    signals = _market_scan()
    movers = {}
    try:
        movers = market_movers(quote="USDT", liquid_qv=5e6, top=3)
    except Exception:
        movers = {}
    detail = f"全市场成交额前 {SCAN_UNIVERSE_SIZE} 交易对扫描，命中 {len(signals)} 个异动"
    tools = [{"icon": "📊", "name": "Binance 全市场行情扫描", "status": "success", "detail": detail}]

    # 用户点名了具体币（如「ETH 行情」「看看 SHIB」）→ 先给该币精确行情
    focus_lines = []
    focus_sym, _ = _parse_trade(message or "")
    if focus_sym:
        try:
            fr = scan_symbols([focus_sym], force=True)
            if fr:
                f = fr[0]
                focus_lines.append(f"**{f['symbol']}** 实时行情：{f['price']} ｜ 24h {f['change_pct']:+.2f}%"
                                   f" ｜ 资金费率 {float(f.get('funding_rate') or 0) * 100:+.4f}%")
        except Exception:
            pass

    if not signals:
        parts = ["当前市场无明显异动（已扫描全市场成交额前 300 交易对，波动 < 2% 且资金费率正常）。"]
        if movers:
            g = [f"{x['symbol']} {x['change_pct']:+.2f}%" for x in movers.get("gainers", [])]
            l = [f"{x['symbol']} {x['change_pct']:+.2f}%" for x in movers.get("losers", [])]
            parts.append(f"\n- 领涨：{' · '.join(g) if g else '—'}\n- 领跌：{' · '.join(l) if l else '—'}")
        return {"reply": "\n".join(focus_lines + [""] + parts if focus_lines else parts), "tools": tools}
    lines = [f"全市场扫描完成（覆盖成交额前 **{SCAN_UNIVERSE_SIZE}** 个交易对），发现 **{len(signals)}** 个异动：\n"]
    if focus_lines:
        lines = focus_lines + [""] + lines
    for s in signals[:6]:
        lines.append(f"{s['emoji']} **{s['symbol']}** · {s['direction']} · {s['change_pct']:+.2f}% · 资金费率 {s['funding_rate'] * 100:+.4f}%")
    lines.append(f"\n最强信号：**{signals[0]['symbol']}**。说「执行交易」生成带止损/止盈的下单方案（需确认才真实下单）。")
    return {"reply": "\n".join(lines), "tools": tools, "data": {"signals": signals[:6], "movers": movers}}


def _run_risk():
    best = _best_signal()
    tips = check_risks(best, 50)
    tools = [{"icon": "🛡️", "name": "Academy 风险教育", "status": "success",
              "detail": f"对全市场最强信号 {best.get('symbol')} 生成 {len(tips)} 条风险提示"}]
    return {"reply": format_risk_report(best.get("symbol", "?"), tips), "tools": tools}


def _run_meme_watch(mode: str = "both"):
    """「妖币 / 启动前 / 埋伏 / meme」：直接调行情模块 Monster Radar 同源数据
    (scanner.get_ignition_coins + get_monster_coins)，与「行情→妖币雷达」展示
    内容 100% 一致。

    mode:
      - "ignition" → 仅启动前·埋伏（用户说"启动前/埋伏/蓄势/点火前/吸筹"）
      - "takeoff"  → 仅起飞中·追涨（用户说"起飞中/追涨/已爆发/拉升中/暴涨中/加速"）
      - "both"     → 两组都给（默认）
    """
    from scanner import get_radar_v2

    # v1.5.0：直接取雷达 v2 全量（四层模型），一次调用拆 ignition/takeoff；
    # 异常回退旧接口（内部仍走 v2，v2 不可用时 v1 日K 兜底）。
    try:
        v2 = get_radar_v2(force=False, top_n=120) or {}
        ig_full = {"coins": v2.get("ignition", []), "env": v2.get("env"),
                   "updated_at": v2.get("updated_at"), "stage_counts": v2.get("stage_counts", {})}
        tk_full = {"coins": v2.get("takeoff", []), "env": v2.get("env"),
                   "updated_at": v2.get("updated_at"), "engine": v2.get("engine")}
    except Exception as e:
        from scanner import get_ignition_coins, get_monster_coins
        try:
            ig_full = get_ignition_coins(force=False, top_n=120, min_qv=2e6) or {}
        except Exception as e2:
            return {"reply": (f"⚠️ 拉取 Monster Radar「启动前」数据失败：{e2}\n\n"
                              "请确认后端服务正常，或直接打开「行情」页看 Monster Radar。"),
                    "tools": [{"icon": "🪙", "name": "妖币雷达", "status": "error", "detail": str(e2)[:140]}]}
        try:
            tk_full = get_monster_coins(force=False, top_n=120, min_qv=2e6) or {}
        except Exception:
            tk_full = {"coins": [], "env": "n/a"}

    env = ig_full.get("env") or tk_full.get("env") or "n/a"
    updated = ig_full.get("updated_at") or tk_full.get("updated_at") or 0

    m = (mode or "both").lower()
    if m not in ("ignition", "takeoff", "both"):
        m = "both"

    if m == "ignition":
        ig_coins = ig_full.get("coins", []) or []
        tk_coins = []
        head_ig = ""
        head_tk = None
    elif m == "takeoff":
        ig_coins = []
        tk_coins = tk_full.get("coins", []) or []
        head_ig = None
        head_tk = ""
    else:
        ig_coins = ig_full.get("coins", []) or []
        tk_coins = tk_full.get("coins", []) or []
        head_ig = ""
        head_tk = ""

    def _lines_for(coins, head, note_takeoff=False):
        out = [head]
        if not coins:
            out.append("（暂无符合的标的）")
            return out
        for c in coins[:8]:
            sym = c.get("symbol", "?")
            price = c.get("price")
            d7 = c.get("change7d_pct") or 0.0
            d30 = c.get("change30d_pct") or 0.0
            dd = c.get("drawdown_pct") or 0.0
            fl = c.get("from_low_pct") or 0.0
            qv = c.get("quote_volume") or 0
            vr = c.get("vol_ratio") or 0.0
            score = c.get("score") or 0.0
            tag = c.get("tag", "")
            side = c.get("side", "")
            note = c.get("note", "")
            qv_m = qv / 1e6
            price_str = f"`{price}`" if price is not None else "n/a"
            # v1.5.0：阶段标签 + 确认因子上下文
            stage = c.get("stage_label") or ""
            f = c.get("factors") or {}
            fbits = []
            if f.get("flow") is not None:
                fbits.append(f"flow {f['flow']}")
            if (f.get("rvol15") or 0) >= 1.2:
                fbits.append(f"RVOL {f['rvol15']:.1f}x")
            if f.get("funding") is not None and abs(f["funding"]) >= 0.0015:
                fbits.append(f"费率 {f['funding'] * 100:+.3f}%")
            if abs(f.get("oi_chg24") or 0) >= 5:
                fbits.append(f"OI 24h {f['oi_chg24']:+.1f}%")
            if f.get("top_ratio") is not None:
                fbits.append(f"大户比 {f['top_ratio']:.2f}")
            if (f.get("taker_ratio") or 0) >= 1.5:
                fbits.append(f"taker {f['taker_ratio']:.2f}")
            if (f.get("liq_5m") or 0) >= 3e5:
                fbits.append(f"5m爆仓 ${f['liq_5m'] / 1e6:.1f}M")
            reasons = "、".join(c.get("reasons") or []) or note
            stage_str = f" · 阶段「{stage}」" if stage else ""
            factor_str = f" · 因子: {', '.join(fbits)}" if fbits else ""
            out.append(
                f"- **{sym}** · 现价 {price_str} · 7d {d7:+.2f}% · 30d {d30:+.2f}% · "
                f"回撤 {dd:+.2f}% · 距低位 {fl:+.2f}% · 24h 量 {qv_m:.2f}M · "
                f"量比 {vr:.2f} · 妖币度 **{score:.1f}**{stage_str} · {tag} ({side}){factor_str} — {reasons}"
            )
        return out

    stage_counts = ig_full.get("stage_counts") or {}
    stage_line = ""
    if stage_counts:
        stage_line = f"· 阶段分布：{' · '.join(f'{k}×{v}' for k, v in stage_counts.items() if v)}"

    lines = [
        f"**妖币雷达 · Monster Radar**（与「行情→妖币雷达」页面同源数据 · mode=`{m}`）",
        "",
        f"· 扫描池：成交额前 120 个 USDT 现货 · 大盘币按当前 24h 成交额**动态**识别（非固定）",
        f"· 大盘状态：`{env}` · 缓存更新时间戳：{updated}",
    ]
    if stage_line:
        lines.append(stage_line)
    lines.append("")
    if head_ig is not None:
        lines.append("## 1) 启动前·埋伏（主推 / 点火前）")
        lines += _lines_for(ig_coins, head_ig)
        lines.append("")
    if head_tk is not None:
        lines.append("## 2) 起飞中·追涨（高风险 / 已爆发）")
        lines += _lines_for(tk_coins, head_tk, note_takeoff=True)
        lines.append("")
    lines += [
        "**下一步建议**：让我对感兴趣的某个币做——",
        "- `market_quote(symbol='XXX')` 查实时行情；",
        "- `propose_trade(symbol='XXX', direction='BULLISH'|'BEARISH')` 生成带止损/止盈的下单方案（需你确认才会真实下单）；",
        "- 或继续读其他维度的辅助：run_skill('meme-rush') 拉官方 meme 榜单；"
        " run_skill('crypto-market-rank') 拉市场排行；",
        "- 也可直接点行情页 Monster Radar 里任意币的「现货买入 / 合约做事」跳到对话。",
    ]

    tools = [
        {"icon": "🪙", "name": "妖币雷达·启动前·埋伏", "status": "success" if ig_coins else "warn",
         "detail": f"{len(ig_coins)} 个埋伏候选（与行情页同源）"},
        {"icon": "🚀", "name": "妖币雷达·起飞中·追涨", "status": "success" if tk_coins else "warn",
         "detail": f"{len(tk_coins)} 个已爆发跟踪"},
    ]
    return {
        "reply": "\n".join(lines),
        "tools": tools,
        "data": {"ignition": ig_coins[:12], "takeoff": tk_coins[:12],
                 "env": env, "updated_at": updated},
    }


def _decide_route(*, signal: str = None) -> str:
    """Agent 决策执行通道：优先交易所（API 密钥现货限价单，精确可控）；
    无密钥时退回 Agent 钱包（baw swap）。这由 Agent 自动判断，不让用户手选。"""
    try:
        from cex_wallet import configured as cex_configured
        if cex_configured():
            return "exchange"
    except Exception:
        pass
    try:
        import wallet_client
        if wallet_client.cli_installed():
            return "wallet"
    except Exception:
        pass
    return "wallet"


def _run_execute(confirm: bool = False, signal: dict = None, message: str = ""):
    if confirm and signal:
        res = confirm_and_place(signal, confirm=True)
        ok = "error" not in res
        tools = [{"icon": "📈", "name": "Binance 真实下单", "status": "success" if ok else "error",
                  "detail": res.get("orderId", res.get("error", ""))}]
        if ok:
            reply = ("✅ **已提交真实下单**\n\n"
                     f"- 订单号：`{res.get('orderId', 'N/A')}`\n- 标的：{res.get('symbol')}\n"
                     f"- 状态：{res.get('status', 'UNKNOWN')}\n\n请到 Binance 账户核对。")
        else:
            # 按错误类型分类：CEX 下单报错仅与「币安交易所」相关，不要混入 Agentic Wallet 概念。
            err = (res.get('error') or '').strip()
            err_l = err.lower()
            if 'api key' in err_l or 'api_key' in err_l or '缺少 api' in err_l:
                reply = (f"⚠️ 下单失败：{err}\n\n"
                         "**币安交易所未连接。** 请到「设置 → 币安 CEX」面板填写交易所 API Key 后再试。")
            elif 'permission' in err_l or '权限' in err or 'signature' in err_l or '签名' in err or 'invalid' in err_l:
                reply = (f"⚠️ 下单失败：{err}\n\n"
                         "**币安交易所 API 权限或签名问题。** 请到「设置 → 币安 CEX」面板检查 API Key 权限、IP 白名单与签名配置。")
            else:
                reply = (f"⚠️ 下单失败：{err}\n\n"
                         "请检查币安交易所连接状态、网络或稍后重试。")
        return {"reply": reply, "tools": tools, "data": res}
    # 解析用户真正想交易的标的（如「买入 BTC」「卖出 ETH 市价」）
    symbol, direction = _parse_trade(message)
    note = ""
    if symbol:
        rows = scan_symbols([symbol], force=True)
        if rows:
            best = rows[0]
        else:
            note = f"（未找到 {symbol} 的实时行情，已改用全市场最强信号）\n"
            best = _best_signal()
    else:
        best = _best_signal()
    price = float(best["price"]) if best.get("price") else 0.0
    if price <= 0:
        return {"reply": "⚠️ 当前无可执行信号，请先「扫描」。", "tools": [
            {"icon": "📈", "name": "交易预检", "status": "warn", "detail": "无信号"}]}
    # Agent 决策执行通道：有交易所 API 密钥 → 走交易所（现货限价单，更精准）；否则走 Agent 钱包（baw swap）。
    route = _decide_route(signal=symbol)
    signal = {**best, "direction": direction, "price": price,
              "stop_loss": round(price * 0.97, 4), "take_profit": round(price * 1.08, 4),
              "quantity": str(round(50 / price, 6)), "max_loss_usdt": 5.0, "route": route}
    route_txt = "币安交易所（API 密钥）" if route == "exchange" else "Agent 钱包（baw）"
    reply = (f"已生成下单方案（**未真实下单，需你确认**）：\n\n"
             f"- 标的：**{signal['symbol']}** · {signal['direction']}\n- 入场：{price}\n"
             f"- 止损：{signal['stop_loss']} · 止盈：{signal['take_profit']}\n"
             f"- 数量：{signal['quantity']} · 最大亏损：{signal['max_loss_usdt']} USDT\n"
             f"- 执行通道：**{route_txt}**（由 Agent 自动判断）\n\n"
             "点击下方「确认下单」才会真实提交（交易所通道需配置 Binance API Key）。")
    if note:
        reply = note + reply
    return {"reply": reply, "needs_approval": True,
            "approval": {"action": "execute_order", "signal": signal,
                         "title": f"确认下单 {signal['symbol']}（{signal['direction']} · 走{route_txt}）",
                         "label": "✅ 确认下单"},
            "tools": [{"icon": "📈", "name": "Binance 下单（待确认）", "status": "info",
                       "detail": f"{signal['symbol']} {signal['direction']} @ {price}"}],
            "data": {"signal": signal}}


def _run_report():
    best = _best_signal()
    tips = check_risks(best, 50)
    report = format_report(best, {"status": "agent_demo", "orderId": "DEMO"}, tips)
    return {"reply": report, "tools": [{"icon": "📝", "name": "交易报告生成", "status": "success", "detail": f"全市场最强 {best.get('symbol', '?')}"}]}


def _run_payment():
    flow = demo_x402_flow()
    tools = [{"icon": "💰", "name": "x402 / B402 支付流", "status": "info" if flow["flow"] == "simulated" else "success",
              "detail": f"{flow['network']} · {flow['amount']} {flow['asset']} · {flow['flow']}"}]
    lines = [f"**x402 / B402 机器支付演示**（{flow['network']}）\n",
             f"状态：**{flow['flow']}**（真实结算需 Agentic Wallet / B402 生产权限）\n"]
    for s in flow["steps"]:
        lines.append(f"**{s['step']}. {s['title']}**\n> {s['detail']}")
    lines.append("\n买家离线 EIP-712 签名 → Facilitator 验证 → 链上结算（gas 由 B402 代付）。")
    return {"reply": "\n".join(lines), "tools": tools, "data": flow}


# ---------- clarify 结构化追问（Hermes clarify_tool 语义） ----------
CLARIFY_MAX_QUESTIONS = 3
CLARIFY_MAX_CHOICES = 4
CLARIFY_CHOICE_KEYS = ("label", "description", "text", "title")  # dict 型 choice 的展平优先级


def _clarify_choice_label(c) -> str:
    """choice 可能是字符串或 dict（Hermes 兼容）：dict 按 label>description>text>title 展平。"""
    if isinstance(c, dict):
        for k in CLARIFY_CHOICE_KEYS:
            v = c.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return str(c)[:80]
    return str(c).strip()


def _run_clarify(args: dict) -> Dict[str, Any]:
    """把模糊需求转成结构化选择题，流出 clarify 事件给前端渲染，暂停等用户点选；
    超时（前端 120s）用户未选 → 以『最佳判断继续』作为答案回流。"""
    raw = args.get("questions") or args.get("q") or []
    if isinstance(raw, dict):
        raw = [raw]
    if isinstance(raw, str):
        raw = [{"question": raw, "choices": []}]
    questions = []
    for item in raw[:CLARIFY_MAX_QUESTIONS]:
        if isinstance(item, str):
            item = {"question": item, "choices": []}
        if not isinstance(item, dict):
            continue
        q = str(item.get("question") or item.get("q") or item.get("text") or "").strip()
        if not q:
            continue
        choices_raw = item.get("choices") or item.get("options") or []
        if isinstance(choices_raw, str):
            choices_raw = [choices_raw]
        choices = [_clarify_choice_label(c) for c in choices_raw if _clarify_choice_label(c)]
        rec = str(item.get("recommended") or "")
        if not rec and isinstance(item.get("recommended_index"), int):
            ri = item["recommended_index"]
            if 0 <= ri < len(choices):
                rec = choices[ri]
        questions.append({"q": q, "choices": choices[:CLARIFY_MAX_CHOICES],
                          "recommended": _clarify_choice_label(rec) if rec else ""})
    if not questions:
        return {"reply": "clarify 需要至少一个 question（可带 choices 选项）。若只是想确认细节，直接在回复里向用户提问即可。",
                "tools": [{"icon": "❓", "name": "结构化追问", "status": "error", "detail": "缺 question"}]}
    lines = ["**需要你确认几个问题**（点选选项即可；不选我会在 120 秒后按最佳判断继续）："]
    for i, qa in enumerate(questions, 1):
        lines.append(f"\n**{i}. {qa['q']}**")
        for j, ch in enumerate(qa["choices"], 1):
            rec = "（推荐）" if qa.get("recommended") and ch == qa["recommended"] else ""
            lines.append(f"  {j}) {ch}{rec}")
    return {"reply": "\n".join(lines),
            "tools": [{"icon": "❓", "name": "结构化追问", "status": "info",
                       "detail": f"{len(questions)} 个问题"}],
            "needs_clarify": True,
            "clarify": {"questions": questions}}


def _run_schedule_tool(action: str = "list", name: str = "", time_spec: str = "", task: str = "daily_scan_report",
                      job_id: str = "", enabled: bool = True, prompt: str = ""):
    """调度任务工具：list/create/update/delete/toggle/run。后端守护线程到点自动执行。
    内置类型报告写入『BAZZ Agent 日报』会话；custom_prompt 自定义任务写入专属会话「定时任务 · <name>」。"""
    try:
        from scheduler import (add_job, remove_job, set_job_enabled, get_job, run_job,
                              parse_time_to_spec, get_jobs, update_job)
    except Exception as e:
        return {"reply": f"调度器不可用：{e}", "tools": [{"icon": "📅", "name": "定时任务", "status": "error",
                                                          "detail": str(e)}]}
    action = (action or "list").strip()
    if action == "list":
        jobs = get_jobs()
        lines = ["**当前定时任务**", ""]
        for j in jobs:
            st = "🟢" if j.get("enabled") else "⚪"
            nxt = j.get("next_run") or 0
            nxt_s = time.strftime("%Y-%m-%d %H:%M", time.localtime(nxt)) if nxt else "—"
            pline = ""
            if j.get("task") == "custom_prompt":
                pline = f" · prompt「{(j.get('prompt') or '')[:60]}」"
            lines.append(f"- {st} `{j.get('id','?')}` **{j.get('name','?')}** · "
                         f"`{j.get('schedule','?')}` · {j.get('task','?')}{pline} · next={nxt_s}")
        return {"reply": "\n".join(lines), "tools": [{"icon": "📅", "name": "定时任务", "status": "info",
                                                       "detail": f"{len(jobs)} 个任务"}], "intent": "cron",
                "data": {"jobs": jobs}}
    if action == "create":
        task = (task or "daily_scan_report").strip()
        if not name or not time_spec:
            return {"reply": "缺少 name 或 time 参数。", "tools": [{"icon": "📅", "name": "定时任务", "status": "error",
                                                                   "detail": "name+time 必填"}]}
        spec = parse_time_to_spec(time_spec)
        if not spec:
            return {"reply": f"无法解析时间规格：`{time_spec}`（支持 `09:00` / `0 9 * * *` / `interval:30m`）",
                    "tools": [{"icon": "📅", "name": "定时任务", "status": "error", "detail": "time 格式无效"}]}
        if task == "custom_prompt" and not (prompt or "").strip():
            return {"reply": "task=custom_prompt 时必须传 prompt（到点要执行的完整指令）。", "tools": [
                {"icon": "📅", "name": "定时任务", "status": "error", "detail": "prompt 必填"}]}
        try:
            jid = add_job(name=name, schedule=spec,
                          task=task if task in ("daily_scan_report", "meme_scan_report", "custom_prompt") else "daily_scan_report",
                          enabled=bool(enabled), persona="",
                          prompt=(prompt or "").strip(), failure_deliver=True)
        except Exception as e:
            return {"reply": f"创建失败：{e}", "tools": [{"icon": "📅", "name": "定时任务", "status": "error",
                                                          "detail": str(e)}]}
        nxt = (get_job(jid) or {}).get("next_run") or 0
        nxt_s = time.strftime("%Y-%m-%d %H:%M", time.localtime(nxt)) if nxt else "—"
        dest = "「定时任务 · " + name + "」会话" if task == "custom_prompt" else "『BAZZ Agent 日报』会话"
        return {"reply": (f"已创建定时任务：**{name}**\n"
                          f"规格：`{spec}` · 任务类型：`{task}`"
                          + (f" · 执行指令：「{(prompt or '').strip()[:80]}」" if task == "custom_prompt" else "") + "\n"
                          f"下次执行：{nxt_s}（到点自动跑，结果写入{dest}，不需要你在线）"),
                "tools": [{"icon": "📅", "name": "定时任务", "status": "success", "detail": f"{spec} · {task}"}],
                "intent": "cron", "data": {"job_id": jid, "schedule": spec, "task": task, "prompt": prompt}}
    if action == "update":
        if not job_id:
            return {"reply": "缺少 job_id。", "tools": [{"icon": "📅", "name": "定时任务", "status": "error", "detail": "job_id 必填"}]}
        if not get_job(job_id):
            return {"reply": f"找不到任务 `{job_id}`。", "tools": [{"icon": "📅", "name": "定时任务", "status": "error", "detail": "not found"}]}
        ok = update_job(job_id, name=name or None, schedule=time_spec or None,
                        task=task if task in ("daily_scan_report", "meme_scan_report", "custom_prompt") else None,
                        prompt=(prompt.strip() if isinstance(prompt, str) and prompt.strip() else None),
                        enabled=None)
        if not ok:
            return {"reply": "没有可更新的字段（需提供 name / time / task / prompt 之一）。", "tools": [
                {"icon": "📅", "name": "定时任务", "status": "warn", "detail": "no fields"}]}
        j = get_job(job_id) or {}
        nxt = j.get("next_run") or 0
        nxt_s = time.strftime("%Y-%m-%d %H:%M", time.localtime(nxt)) if nxt else "—"
        return {"reply": (f"已更新定时任务 `{job_id}`：**{j.get('name')}** · `{j.get('schedule')}` · "
                          f"{j.get('task')} · 下次 {nxt_s}"),
                "tools": [{"icon": "📅", "name": "定时任务", "status": "success", "detail": job_id}],
                "intent": "cron", "data": {"job": j}}
    if action == "delete":
        if not job_id:
            return {"reply": "缺少 job_id。", "tools": [{"icon": "📅", "name": "定时任务", "status": "error", "detail": "job_id 必填"}]}
        remove_job(job_id)
        return {"reply": f"已删除定时任务：`{job_id}`", "tools": [{"icon": "📅", "name": "定时任务", "status": "success",
                                                                  "detail": job_id}], "intent": "cron"}
    if action == "toggle":
        if not job_id:
            return {"reply": "缺少 job_id。", "tools": [{"icon": "📅", "name": "定时任务", "status": "error", "detail": "job_id 必填"}]}
        set_job_enabled(job_id, bool(enabled))
        return {"reply": f"已{'启用' if enabled else '暂停'}定时任务：`{job_id}`",
                "tools": [{"icon": "📅", "name": "定时任务", "status": "success", "detail": f"{job_id} -> {enabled}"}],
                "intent": "cron"}
    if action == "run":
        if not job_id:
            return {"reply": "缺少 job_id。", "tools": [{"icon": "📅", "name": "定时任务", "status": "error", "detail": "job_id 必填"}]}
        job = get_job(job_id)
        if not job:
            return {"reply": f"找不到任务 `{job_id}`。", "tools": [{"icon": "📅", "name": "定时任务", "status": "error", "detail": "not found"}]}
        try:
            summary = run_job(job)
        except Exception as e:
            return {"reply": f"执行失败：{e}", "tools": [{"icon": "📅", "name": "定时任务", "status": "error", "detail": str(e)}]}
        return {"reply": f"已立即执行：`{job.get('name')}`\n\n{summary[:1000]}",
                "tools": [{"icon": "📅", "name": "定时任务", "status": "success", "detail": job_id}],
                "intent": "cron", "data": {"ran": job_id, "summary": summary[:2000]}}
    return {"reply": f"未知 action：{action}", "tools": [{"icon": "📅", "name": "定时任务", "status": "error", "detail": action}]}


def _run_onchain():
    tools = [{"icon": "⛓️", "name": "Agentic Wallet", "status": "info",
              "detail": "baw CLI · 多链 BSC/Eth/Base/Solana"}]
    reply = ("**链上自动化流程** — Binance Agentic Wallet + Skills Hub\n\n"
             "`npm i -g @binance/agentic-wallet` 支持自然语言操作（skill v1.11.0 / CLI v1.9.0）：\n"
             "- 钱包/交易：`baw wallet status/balance/send` · `market-order quote/swap` · `limit-order buy/sell`\n"
             "- 交易增强：`wallet speed-up/cancel` 处理 pending · `approvals list/revoke` 授权风控\n"
             "- 预测市场：`baw prediction market list` · `position list/pnl` · `trade place-order/redeem`\n"
             "- DeFi：`baw defi protocol-list` · `investment-list` · `position` · `deposit/redeem/lp-add`\n"
             "- 外部调用：`contract-call preview/execute`（devMode）· `sign-message` · `x402-payment`\n\n"
             "需 Binance 账号 + App 扫码登录（MPC 无密钥）。已装链上 Skills：`binance-agentic-wallet` 等。\n\n"
             "**想真实执行**（如「用 agent 钱包买入 100 USDT 的 BTC」）直接说即可：我会先请你在对话里确认，"
             "再经沙箱调用 `baw` 执行；若钱包未连接 / 未登录、余额或限额不足、命令语法错误，都会给出对应的钱包侧提醒。")
    return {"reply": reply, "tools": tools}


def _run_skills():
    cat = get_catalog()
    installed = [k for k, v in cat.items() if v["installed"]]
    tools = [{"icon": "🔧", "name": "Skills Hub", "status": "success",
              "detail": f"已安装 {len(installed)} / 共 {len(cat)} 个"}]
    lines = [f"**Binance Skills Hub** — 已安装 **{len(installed)}** 个（官方共 {len(cat)} 个）\n"]
    for k in installed:
        lines.append(f"- ✅ `{k}` — {cat[k]['title']}")
    lines.append("\n输入「安装 skill」查看可安装列表，或「安装 <名称>」触发 `npx skills add`。")
    return {"reply": "\n".join(lines), "tools": tools, "data": {"catalog": cat}}


def _skill_md_desc(name: str) -> str:
    """读已装社区技能的 SKILL.md frontmatter description（前 200 字）。"""
    md = os.path.join(AGENTS_DIR, name, "SKILL.md")
    if not os.path.isfile(md):
        return ""
    try:
        for ln in open(md, encoding="utf-8").read().splitlines()[:12]:
            s = ln.strip()
            if s.lower().startswith("description"):
                return s.split(":", 1)[-1].strip()[:200]
    except Exception:
        pass
    return ""


def _run_find_skills(arg: str = ""):
    """发现技能：本地检索（官方 catalog + 已装社区技能）+ GitHub 联网搜索可装 agent skills。

    返回带安装状态的结果清单；官方 key 或 GitHub URL 都能走「安装 xxx」确认流。
    """
    kw = re.sub(r"(发现|查找|搜一下|搜索|找|帮我|有没有|什么|技能|skills?|一个|给我|看看)",
                "", (arg or ""), flags=re.I).strip()
    cat = get_catalog()
    installed = set(list_installed())
    # A) 官方 catalog 命中
    local = []
    for k, v in cat.items():
        hay = f"{k} {v.get('title','')} {v.get('desc','')}".lower()
        if not kw or kw.lower() in hay:
            local.append({"id": k, "title": v.get("title", k), "desc": v.get("desc", ""),
                          "installed": k in installed, "url": v.get("url", "")})
    # B) 本地已装但不在官方 catalog 里的社区技能（context-compression / memory-systems 等）
    custom = []
    for name in sorted(installed - set(cat)):
        desc = _skill_md_desc(name)
        hay = f"{name} {desc}".lower()
        if not kw or kw.lower() in hay:
            custom.append({"id": name, "title": name, "desc": desc,
                           "installed": True, "url": ""})
    # C) GitHub 联网发现（关键词必填时才搜，网络失败静默降级）
    remote = []
    remote_err = ""
    if kw:
        try:
            import requests as _req
            r = _req.get("https://api.github.com/search/repositories",
                         params={"q": f"{kw} claude skill",
                                 "sort": "stars", "order": "desc", "per_page": 5},
                         headers={"Accept": "application/vnd.github+json", "User-Agent": "BAZZ"},
                         timeout=8)
            if r.status_code == 200:
                for it in (r.json().get("items") or [])[:5]:
                    remote.append({"id": it.get("full_name", ""), "title": it.get("full_name", ""),
                                   "desc": (it.get("description") or "")[:140],
                                   "installed": False, "url": it.get("html_url", ""),
                                   "stars": it.get("stargazers_count", 0)})
            else:
                remote_err = f"GitHub 返回 {r.status_code}"
        except Exception as e:
            remote_err = f"网络不可用（{type(e).__name__}）"
    # 汇总回复
    head = f"**技能发现** — 关键词「{kw or '（全部）'}」\n"
    lines = [head]
    if not kw and not local and not custom:
        return {"reply": "输入想找的方向，例如「发现记忆技能 / 搜索 context 技能 / 找行情技能」。", "tools": [
            {"icon": "🧭", "name": "技能发现", "status": "info", "detail": "等待关键词"}]}
    if local:
        lines.append(f"\n**官方 Binance Skills Hub**（命中 {len(local)}）：")
        for it in local[:10]:
            mark = "✅已装" if it["installed"] else "⬜可装"
            lines.append(f"- {mark} `{it['id']}` — {it['title']}：{it['desc'][:60]}")
    if custom:
        lines.append(f"\n**本地已装技能**（命中 {len(custom)}）：")
        for it in custom[:10]:
            lines.append(f"- ✅ `{it['id']}` — {it['desc'][:80] or '社区安装'}")
    if remote:
        lines.append(f"\n**GitHub 发现**（可安装，需确认）：")
        for it in remote:
            lines.append(f"- ⭐{it['stars']} `{it['id']}` — {it['desc'][:80]}")
    elif kw and remote_err:
        lines.append(f"\n（GitHub 联网发现不可用：{remote_err}——本地结果不受影响）")
    lines.append("\n→ 装官方技能说「安装 <名称>」；装 GitHub 上的说「安装 <URL>」，确认后自动执行。")
    tools = [{"icon": "🧭", "name": "技能发现", "status": "success",
              "detail": f"官方 {len(local)} · 本地 {len(custom)} · GitHub {len(remote)}"}]
    return {"reply": "\n".join(lines), "tools": tools,
            "data": {"keyword": kw, "local": local, "custom": custom, "remote": remote}}


def _perform_install(key: str) -> dict:
    """真正执行安装（已由用户确认，不再二次询问）。"""
    res = install_skill(key)
    ok = res["status"] == "ok"
    return {"reply": f"安装 `{key}`：{res['status']}\n```\n{(res.get('stdout') or res.get('detail') or '')[:1200]}\n```",
            "tools": [{"icon": "🔧", "name": f"安装 {key}", "status": "success" if ok else "error",
                       "detail": (res.get("stdout") or res.get("detail") or "")[:200]}]}


def _run_install(arg: str = ""):
    cat = get_catalog()
    # GitHub URL 直装（find_skills 发现的结果说「安装 <url>」时走这里）
    m_url = re.search(r"https?://[^\s，。；]+", arg or "")
    if m_url:
        url = m_url.group(0).strip().rstrip(".,;)，】")
        return {"reply": f"即将安装 **{url}**（`npx skills add`，从 GitHub 拉取）。确认后执行。",
                "needs_approval": True,
                "approval": {"action": "install_skill", "skill": url},
                "tools": [{"icon": "🔧", "name": "安装技能", "status": "info", "detail": url[:90]}]}
    key = next((k for k in cat if k in arg), None)
    if not key:
        lines = ["**可安装的官方 Skills（npx skills add）**\n"]
        for k, v in cat.items():
            mark = "✅已装" if v["installed"] else "⬜"
            lines.append(f"- {mark} `{k}` — {v['title']}：{v['desc']}")
        lines.append("\n输入「安装 binance-agentic-wallet」等即可触发安装。")
        lines.append("想找官方目录以外的新技能？试试「发现 记忆 技能」（联网搜 GitHub）。")
        return {"reply": "\n".join(lines), "tools": [
            {"icon": "🔧", "name": "Skills 目录", "status": "info", "detail": f"{len(cat)} 个可用"}]}
    return {"reply": f"即将安装 **`{key}`**（`npx skills add {key}`）。确认后执行。",
            "needs_approval": True,
            "approval": {"action": "install_skill", "skill": key},
            "tools": [{"icon": "🔧", "name": f"安装 {key}", "status": "info", "detail": "待确认"}]}


def _run_tools():
    ms = mcp_status()
    lt = mcp_list_tools()
    tools = [{"icon": "🛠️", "name": "MCP 集成", "status": "success" if ms.get("reachable") else "warn",
              "detail": f"{len(ms.get('servers', []))} 个 server · {len(lt.get('tools', []))} 个工具"}]
    if lt.get("tools"):
        lines = ["**已连接的 MCP 工具**：\n"]
        for t in lt["tools"][:14]:
            lines.append(f"- `{t['name']}` _({t.get('server', '')})_ — {t.get('description', '')[:80]}")
        reply = "\n".join(lines)
    else:
        reply = ("**MCP 集成**\n\n当前未连接任何工具 server。\n"
                 "默认预置 Binance Agentic MCP（`agent.binance.com/mcp/agentic`）。\n"
                 "在「设置 / MCP」中配置 API Key（Agentic 子账户授权）后即可读取行情/账户并下单；也可添加任意 MCP Server。")
    return {"reply": reply, "tools": tools, "data": lt}


def _run_help():
    tools = [{"icon": "🤖", "name": "Agent 就绪", "status": "success", "detail": "Binance Agent OS"}]
    reply = ("**🤖 BAZZ Agent** — Binance 专属桌面 Agent\n\n"
             "| 能力 | 指令 |\n|------|------|\n"
             "| 📊 行情扫描 | 扫描 |\n| 🛡️ 风险教育 | 风险 |\n"
             "| 📈 交易方案（需确认）| 执行交易 |\n| 💰 机器支付 | 支付 |\n"
             "| ⛓️ 链上自动化 | 链上 |\n| 🔧 Skills | 安装 skill |\n"
             "| 🛠️ MCP 工具 | 工具 |\n| 🧠 并行板块扫描 | 并行扫描 |\n"
             "| ⏰ 定时日报 | 定时扫描 |\n| 📓 长期记忆 | 记住… / 你记得… |\n\n"
             "可调项：模型切换、MCP Server、插件、安全审批，均在左侧面板。")
    return {"reply": reply, "tools": tools}


# ---------------- 自动生成记忆（对话后自动沉淀偏好/事实，无需用户说「记住」） ----------------
_AUTO_MEM_PREF_HINTS = ("我只做", "我不做", "不要", "别用", "偏好", "习惯", "只用", "现货就好",
                        "不做合约", "不碰", "我关注", "我主要", "尽量", "风险偏好", "仓位", "止盈",
                        "止损", "喜欢", "不喜欢", "记住", "以后", "每次都", "默认")
_AUTO_MEM_COOLDOWN = 180  # 同一会话 3 分钟内至多沉淀一次，防每轮烧模型

# 敏感信息模式：API key / secret / 私钥 / 助记词等绝不入记忆（Hermes 式写入验证）
_SECRET_PAT = re.compile(
    r"(api[_\s-]?(key|secret)|私钥|助记词|mnemonic|seed\s*phrase|password|密码|token\s*[:=]"
    r"|[A-Za-z0-9]{48,}|0x[a-fA-F0-9]{40,})", re.IGNORECASE)


def _has_secret(text: str) -> bool:
    return bool(_SECRET_PAT.search(str(text or "")))


def _mem_similarity(a: str, b: str) -> float:
    """字符 bigram 重合度（0-1）：轻量相似度，用于合并式去重（对齐 Hermes replace 语义）。"""
    a, b = str(a or ""), str(b or "")
    if len(a) < 2 or len(b) < 2:
        return 0.0
    ga = {a[i:i + 2] for i in range(len(a) - 1)}
    gb = {b[i:i + 2] for i in range(len(b) - 1)}
    return len(ga & gb) / max(1, len(ga | gb))


def auto_memorize(user_msg: str, reply: str, llm_cfg: dict = None) -> dict:
    """对话结束后调用：从「用户说了什么 + 我答了什么」提炼值得跨会话记住的信息。

    门控：
    - 用户消息含明显偏好信号词才触发（避免把闲聊也沉淀）；
    - 冷却时间内（_AUTO_MEM_COOLDOWN 秒）跳过；
    - 敏感信息（API key/助记词/密码等）一律拒绝入库；
    - 合并式去重：与现有记忆高相似（bigram ≥0.6）→ replace 更新原条目，不新增；
    - 提炼失败/无可记内容静默返回，绝不影响主流程。
    返回 {"stored": int, "keys": [...], "skipped": str}。
    """
    try:
        from state import get_setting, set_setting, set_memory, list_memory
    except Exception:
        return {"stored": 0, "keys": [], "skipped": "state 不可用"}
    um = (user_msg or "").strip()
    if not um or len(um) < 4:
        return {"stored": 0, "keys": [], "skipped": "消息太短"}
    if not any(h in um for h in _AUTO_MEM_PREF_HINTS):
        return {"stored": 0, "keys": [], "skipped": "无偏好信号"}
    try:
        last = float(get_setting("auto_mem_last_ts", "0") or 0)
        if time.time() - last < _AUTO_MEM_COOLDOWN:
            return {"stored": 0, "keys": [], "skipped": "冷却中"}
    except Exception:
        pass
    existing = list_memory()
    sysp = (
        "你是一个记忆提炼器。用户在与 BAZZ Agent（币安交易助手）对话中透露了偏好/习惯/规则/重要背景。"
        "从下面的『用户消息』与『助手回复』中，提炼 1-3 条值得长期记住的内容（跨会话有用）。\n"
        "规则：只记稳定的偏好/习惯/约束/重要背景，不记一次性行情数字；每条一句话 ≤50 字；"
        "kind 取值：pref=偏好/规则约束，fact=事实背景，event=重要事件。\n"
        + ("已有记忆（内容相似的不要再出，除非是明确更新——此时在 replace_key 里填已有记忆的 key）：\n"
           + "\n".join(f"- [{m['key']}] {m['value'][:60]}" for m in existing[:20]) + "\n" if existing else "")
        + '只输出 JSON：{"items": [{"text": "…", "kind": "pref|fact|event", "replace_key": ""}], "无可记内容时 items 为空}。')
    prompt = f"用户消息：{um[:600]}\n\n助手回复：{(reply or '')[:600]}"
    try:
        out = llm.chat(sysp, prompt, temperature=0.1, max_tokens=400, llm_cfg=llm_cfg, task="summarize")
        if not out:
            return {"stored": 0, "keys": [], "skipped": "模型无输出"}
        parsed = json.loads(out)
        items = parsed.get("items", []) if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else [])
    except Exception:
        return {"stored": 0, "keys": [], "skipped": "提炼失败"}
    keys = []
    try:
        set_setting("auto_mem_last_ts", str(time.time()))
        for it in items[:3]:
            if not isinstance(it, dict):
                it = {"text": str(it)}
            text = str(it.get("text") or "").strip()
            if len(text) < 6 or len(text) > 200:
                continue
            if _has_secret(text):
                continue  # 敏感信息绝不入库
            kind = str(it.get("kind") or "pref").lower()
            if kind not in ("pref", "fact", "event"):
                kind = "pref" if any(w in text for w in ("只", "不要", "偏好", "习惯", "默认")) else "fact"
            # 合并式去重：LLM 指定 replace_key，或与现有记忆高相似 → 原地更新
            target_key = str(it.get("replace_key") or "").strip()
            if not (target_key and any(m["key"] == target_key for m in existing)):
                target_key = ""
                for m in existing:
                    if _mem_similarity(text, m["value"]) >= 0.6:
                        target_key = m["key"]
                        break
            if target_key:
                set_memory(target_key, text, kind=kind, source="auto")
                keys.append(target_key)
                continue
            key = f"{kind}:{int(time.time() * 1000) % 100000000}-{len(existing) + len(keys) + 1}"
            set_memory(key, text, kind=kind, source="auto")
            keys.append(key)
    except Exception:
        pass
    return {"stored": len(keys), "keys": keys, "skipped": ""}


# ---------------- 会话标题自动生成（v1.4.3，学习 Hermes title_generator） ----------------

def _clean_title(raw: str) -> str:
    """清洗模型产出的标题：剥围栏/JSON/引号/前缀，拒绝答案形状的超长输出（不截断）。"""
    if not raw:
        return ""
    t = str(raw).strip()
    if t.startswith("```"):
        t = t.strip("`").removeprefix("json").strip()
    try:
        j = json.loads(t)
        if isinstance(j, dict) and isinstance(j.get("title"), str):
            t = j["title"].strip()
    except Exception:
        pass
    if t.lower().startswith("title:"):
        t = t[6:].strip()
    t = t.strip("\"'「」『』“” 　").rstrip(".。，,！!？?；;：:…")
    t = (t.splitlines() or [""])[0].strip()
    if len(t) > 30:  # 答案形状输出（模型在回答而非命名）→ 整条拒绝
        return ""
    return t


def auto_title(cid: str, llm_cfg: dict = None):
    """会话标题自动升级：标题仍是默认截断（首条用户消息前 28 字 / 「新对话」/ @档案 前缀）时，
    用 summarize 槽位模型生成 4-16 字标题落库。后台线程调用，失败静默；
    用户手动改过或已有自动标题的不覆盖（写库前二次校验防改名竞态）。"""
    if not cid:
        return {"skipped": "no cid"}
    try:
        import state as _state
        conv = _state.get_conversation(cid)
        if not conv:
            return {"skipped": "no conv"}
        first_user = ""
        for m in _state.get_messages(cid):
            if m.get("role") == "user" and (m.get("content") or "").strip():
                first_user = m["content"].strip()
                break
        if not first_user:
            return {"skipped": "no user msg"}
        persona_name = conv.get("persona") or ""
        cur = (conv.get("title") or "").strip()
        base = first_user[:28]
        defaults = {d for d in ("新对话", base, (f"@{persona_name} · {base}" if persona_name else "")) if d}
        if cur not in defaults:
            return {"skipped": "already titled"}
        sysp = ("你是对话命名器。根据用户的第一条消息，给这段对话起一个便于在会话列表中再次找到它的标题。\n"
                "规则：4-16 个字；说清用户想做什么事，不写问句；保留关键术语/币种/数字；"
                "去掉「帮我/请/我想」等填充词；结尾不带标点；只命名，不要回答消息内容。\n"
                '只输出 JSON：{"title": "..."}')
        out = llm.chat(sysp, first_user[:500], temperature=0.3, max_tokens=64,
                       llm_cfg=llm_cfg, task="summarize")
        title = _clean_title(out)
        if not title:
            return {"skipped": "model no title"}
        if persona_name:
            title = f"@{persona_name} · {title}"
        conv2 = _state.get_conversation(cid) or {}
        if (conv2.get("title") or "").strip() != cur:
            return {"skipped": "renamed during generation"}
        _state.touch_conversation(cid, title=title[:60])
        return {"ok": True, "title": title}
    except Exception as e:
        return {"skipped": str(e)[:120]}


def _run_search_history(query: str = "") -> Dict[str, Any]:
    """搜索本地历史会话（v1.4.3）：标题 + 消息正文 LIKE 匹配，返回会话卡片与命中片段。"""
    query = str(query or "").strip()
    if not query:
        return {"reply": "请告诉我要在历史会话中搜索的关键词。", "tools": [
            {"icon": "🔍", "name": "历史搜索", "status": "warn", "detail": "关键词为空"}]}
    try:
        import state as _state
        results = _state.search_conversations(query, limit=8)
    except Exception as e:
        return {"reply": f"历史搜索失败：{e}", "tools": [
            {"icon": "🔍", "name": "历史搜索", "status": "error", "detail": str(e)[:120]}]}
    if not results:
        return {"reply": f"历史会话中没有找到与「{query}」相关的内容。",
                "tools": [{"icon": "🔍", "name": "历史搜索", "status": "success", "detail": "0 条命中"}]}
    lines = []
    for r in results:
        when = time.strftime("%m-%d %H:%M", time.localtime(r.get("updated_at") or 0))
        title = r.get("title") or str(r.get("id", ""))[:8]
        lines.append(f"- 「{title}」（{when}，命中 {r.get('hits', 0)} 条消息）：{(r.get('preview') or '')[:120]}")
    reply = (f"在历史会话中找到 {len(results)} 个相关对话（按最近排序）：\n" + "\n".join(lines) +
             "\n\n可据此结合用户问题作答；如需完整上下文，请用户在会话列表搜索框中打开对应会话。")
    return {"reply": reply, "tools": [{"icon": "🔍", "name": "历史搜索", "status": "success",
                                       "detail": f"{len(results)} 个会话命中"}]}


def _todo_list_reply() -> str:
    try:
        from state import list_todos
        todos = list_todos()
    except Exception:
        return "（任务清单读取失败）"
    if not todos:
        return "（任务清单为空）"
    lines = [f"{'☑' if t['done'] else '☐'} [{t['id'][:8]}] {t['text']}" for t in todos]
    return "当前任务清单：\n" + "\n".join(lines)


def _run_todo_tool(action: str = "add", text: str = "", todo_id: str = "") -> Dict[str, Any]:
    """Agent 任务清单（v1.4.4，学习 Hermes todo_tool）：add / toggle / remove / list / clear_done。
    未完成任务每轮注入 system prompt，长任务跨轮不烂尾。"""
    from state import (add_todo, list_todos, todo_toggle, todo_toggle_by_text,
                       remove_todo, clear_done_todos)
    action = (action or "add").lower()
    try:
        if action == "add":
            t = str(text or "").strip()
            if not t:
                return {"reply": "请提供任务内容（text）。", "tools": [
                    {"icon": "📋", "name": "任务清单", "status": "warn", "detail": "内容为空"}]}
            add_todo(t)
            reply = f"已加入任务：{t[:80]}\n" + _todo_list_reply()
        elif action == "toggle":
            ok = False
            if todo_id:
                todo_toggle(todo_id)
                ok = True
            elif str(text or "").strip():
                ok = todo_toggle_by_text(text)
            if not ok and not todo_id:
                return {"reply": "没找到要勾选的任务（可先 list 查看任务与 id）。\n" + _todo_list_reply(),
                        "tools": [{"icon": "📋", "name": "任务清单", "status": "warn", "detail": "未命中"}]}
            reply = "已更新任务状态。\n" + _todo_list_reply()
        elif action == "remove":
            if not todo_id:
                return {"reply": "remove 需要 todo_id（先 list 查看）。\n" + _todo_list_reply(),
                        "tools": [{"icon": "📋", "name": "任务清单", "status": "warn", "detail": "缺 id"}]}
            remove_todo(todo_id)
            reply = "已删除任务。\n" + _todo_list_reply()
        elif action == "clear_done":
            n = clear_done_todos()
            reply = f"已清理 {n} 条已完成任务。\n" + _todo_list_reply()
        else:  # list
            reply = _todo_list_reply()
        return {"reply": reply, "tools": [{"icon": "📋", "name": "任务清单", "status": "success",
                                           "detail": action}]}
    except Exception as e:
        return {"reply": f"任务清单操作失败：{e}", "tools": [
            {"icon": "📋", "name": "任务清单", "status": "error", "detail": str(e)[:120]}]}


def _run_memory_write(message: str = "", action: str = "add", key: str = "",
                      text: str = "", kind: str = "", agent_call: bool = False):
    """记忆统一入口（v1.4.1 对齐 Hermes 动作模型）：add / replace / remove / read。

    - Agent memory_write 工具与中文「记住：…」指令都走这里；
    - 敏感信息（API key/助记词/密码）一律拒绝入库；
    - add 自动合并式去重（与现有记忆相似 ≥0.6 → 原地更新不新增）；
    - agent_call=True（工具路径）时 remove 仅限非 manual 来源的记忆。
    """
    from state import delete_memory  # 局部导入避免顶部 fallback 缺失
    action = (action or "add").lower()
    if message and not text:
        m = re.search(r"(?:记住|笔记|记录|记一下|存一下)[：:：]?\s*(.+)", message)
        text = m.group(1).strip() if m else message
    text = str(text or "").strip()[:500]

    if action == "read":
        rows = search_memory(text) if text else list_memory()
        if not rows:
            return {"reply": "我目前还没有记住任何长期信息。试试「记住：你只做现货」。", "tools": [
                {"icon": "📓", "name": "读取记忆", "status": "info", "detail": "空"}]}
        tag = {"pref": "偏好", "fact": "事实", "event": "事件"}
        lines = ["**我的长期记忆**：\n"] if not text else [f"**记忆检索「{text[:40]}」**：\n"]
        for m in rows[:12]:
            lines.append(f"- 📌 [{tag.get(m.get('kind'), '事实')}] {m['value']}"
                         + (f"（key: {m['key']}）" if not text else ""))
        return {"reply": "\n".join(lines), "tools": [
            {"icon": "📓", "name": "读取记忆", "status": "success", "detail": f"{len(rows)} 条"}],
            "data": {"memory": rows}}

    if action == "remove":
        mem = list_memory()
        target = ""
        if key:
            target = key if any(m["key"] == key for m in mem) else ""
        if not target and text:
            best = max(mem, key=lambda m: _mem_similarity(text, m["value"]), default=None)
            if best and _mem_similarity(text, best["value"]) >= 0.5:
                target = best["key"]
        if not target:
            return {"reply": "没找到要删除的记忆。用 memory_write read 先查看现有记忆与 key。",
                    "tools": [{"icon": "📓", "name": "删除记忆", "status": "warn", "detail": "未匹配"}]}
        mrow = next((m for m in mem if m["key"] == target), None)
        if agent_call and mrow and mrow.get("source") == "manual":
            return {"reply": "该记忆由用户手动创建，Agent 不能删除。", "tools": [
                {"icon": "📓", "name": "删除记忆", "status": "error", "detail": "用户手动记忆"}]}
        delete_memory(target)
        return {"reply": f"🗑️ 已删除记忆：{target}", "tools": [
            {"icon": "📓", "name": "删除记忆", "status": "success", "detail": target}],
            "data": {"memory": list_memory()[:10]}}

    # add / replace
    if not text:
        return {"reply": "你想让我记住什么？例如「记住：我只做现货，不做合约」。", "tools": [
            {"icon": "📓", "name": "记忆", "status": "warn", "detail": "空内容"}]}
    if _has_secret(text):
        return {"reply": "⚠️ 内容包含 API Key / 私钥 / 密码等敏感信息，出于安全考虑不会写入长期记忆。",
                "tools": [{"icon": "🛡️", "name": "记忆安全拦截", "status": "warn", "detail": "含敏感信息"}]}
    if kind not in ("pref", "fact", "event"):
        kind = "pref" if (action == "add" and any(
            w in text for w in ("只", "不要", "别", "偏好", "习惯", "默认", "每次"))) else "fact"
    mem = list_memory()
    source = "agent" if agent_call else "manual"
    if action == "replace":
        target = key if key and any(m["key"] == key for m in mem) else ""
        if not target:
            return {"reply": "replace 需要提供要更新的记忆 key（先用 read 查看）。", "tools": [
                {"icon": "📓", "name": "更新记忆", "status": "warn", "detail": "缺 key"}]}
    else:  # add：合并式去重
        target = ""
        for m in mem:
            if _mem_similarity(text, m["value"]) >= 0.6:
                target = m["key"]
                break
    set_memory(target or f"{kind}:{int(time.time() * 1000) % 100000000}-{len(mem) + 1}",
               text, kind=kind, source=source)
    verb = "已更新" if target else "已记住"
    return {"reply": f"✅ {verb}：**{text}**（将用于后续所有会话）。", "tools": [
        {"icon": "📓", "name": "写入长期记忆", "status": "success", "detail": target or kind}],
        "data": {"memory": list_memory()[:10]}}


def _run_memory_read():
    return _run_memory_write(action="read")


def _run_parallel():
    """并行子代理：板块细分 + 全市场动态池（不再只扫固定 SECTORS 列表）。"""
    sub_cards = []
    results = {}

    def worker(sector, symbols):
        sigs = scan_symbols(symbols, min_change_pct=3.0)
        return sector, sigs

    # 先跑静态板块（大盘/公链/Meme…），再补全市场成交额前 N 的动态池
    tasks = list(SECTORS.items())
    pool_sigs = []
    try:
        pool_sigs = _market_scan()
    except Exception:
        pool_sigs = []
    results["全市场动态池"] = pool_sigs

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(worker, s, syms): s for s, syms in tasks}
        for f in futures:
            sector, sigs = f.result()
            results[sector] = sigs
            top = sigs[0] if sigs else None
            sub_cards.append({
                "icon": "🤖", "name": f"子代理 · {sector}", "status": "success" if top else "info",
                "detail": (f"{top['symbol']} {top['direction']} {top['change_pct']:+.2f}%" if top else "无异常"),
            })
    pool_top = pool_sigs[0] if pool_sigs else None
    sub_cards.append({
        "icon": "🌐", "name": "全市场动态池", "status": "success" if pool_top else "info",
        "detail": (f"{len(pool_sigs)} 个异动 · 最强 {pool_top['symbol']}" if pool_top else "无异常"),
    })
    all_top = []
    for sector, sigs in results.items():
        for s in sigs:
            all_top.append({**s, "sector": sector})
    all_top.sort(key=lambda x: x["score"], reverse=True)
    lines = ["**并行扫描完成**（板块子代理 + 全市场动态池覆盖成交额前 300 交易对）：\n"]
    for sector, sigs in results.items():
        if sector == "全市场动态池":
            continue
        if sigs:
            top = sigs[0]
            lines.append(f"- **{sector}**：{top['symbol']} {top['direction']} {top['change_pct']:+.2f}%")
        else:
            lines.append(f"- {sector}：无异常")
    if pool_top:
        lines.append(f"- **全市场**：{len(pool_sigs)} 个异动，最强 {pool_top['symbol']} {pool_top['change_pct']:+.2f}%")
    if all_top:
        lines.append(f"\n全局最强信号：**{all_top[0]['symbol']}**（{all_top[0]['sector']}）。")
    return {"reply": "\n".join(lines), "tools": sub_cards,
            "data": {"parallel": {k: [x["symbol"] for x in v] for k, v in results.items()}}}


def _run_cron(message: str):
    from scheduler import get_jobs, add_job, _next
    if "添加" in message or "新建" in message or "增加" in message:
        add_job("定时市场扫描", "0 9 * * *")
        jobs = get_jobs()
    else:
        jobs = get_jobs()
    lines = [f"**定时任务（{len(jobs)} 个）**：\n"]
    now = time.time()
    for j in jobs:
        nxt = j.get("next_run", 0)
        nxt_s = time.strftime("%Y-%m-%d %H:%M", time.localtime(nxt)) if nxt else "未启用"
        lines.append(f"- {'🟢' if j.get('enabled') else '⚪'} `{j['name']}` · {j['schedule']} · 下次 {nxt_s}")
    lines.append("\n在「定时任务」面板可增删/启停。任务到点会自动扫描并生成日报存入「BAZZ Agent 日报」会话。")
    return {"reply": "\n".join(lines), "tools": [
        {"icon": "⏰", "name": "定时调度", "status": "success", "detail": f"{len(jobs)} 个任务"}],
        "data": {"jobs": jobs}}


def _dispatch_tool(name: str, args: dict, confirmed: bool = False) -> Dict[str, Any]:
    """把 LLM 选择的工具映射到现有能力处理器，返回统一结果 dict。

    confirmed=True 时沙箱类工具（write_file/run_command/run_skill）跳过审批直接执行；
    仅在「全能模式」auto_exec 开启时由上层传入。涉及资金/交易的 propose_trade 不受影响。
    """
    args = args or {}
    # Hermes 插件 SDK：插件命令以 <pluginId>.<command> 形式暴露给 LLM
    if "." in name:
        pid, cmd = name.split(".", 1)
        try:
            from plugin_host import exec_command, is_plugin
        except Exception:
            is_plugin = None
        if is_plugin and is_plugin(pid):
            try:
                out = exec_command(pid, cmd, args)
            except Exception as e:
                out = {"ok": False, "error": str(e)}
            ok = bool(out.get("ok"))
            txt = (out.get("text") or out.get("error") or "（无输出）")[:1500]
            return {"reply": txt,
                    "tools": [{"icon": "🧩", "name": f"插件 {pid}.{cmd}", "status": "success" if ok else "error",
                               "detail": (out.get("error") or out.get("plugin") or pid)}],
                    "intent": "tool", "data": {"plugin": pid, "command": cmd, "output": out}}
    if name == "scan_market":
        return _run_scan()
    if name == "check_risk":
        return _run_risk()
    if name == "propose_trade":
        return _tool_propose_trade(args.get("symbol", ""), args.get("direction", "BULLISH"))
    if name == "market_quote":
        symbol = args.get("symbol", "BTCUSDT")
        rows = scan_symbols([symbol], force=True)
        if rows:
            r = rows[0]
            return {"reply": f"**{r['symbol']}** 实时行情：\n- 价格：{r['price']}\n"
                             f"- 24h 涨跌：{r.get('change_pct', 0):+.2f}%\n"
                             f"- 资金费率：{r.get('funding_rate', 0) * 100:+.4f}%",
                    "tools": [{"icon": "📈", "name": "行情查询", "status": "success", "detail": r['symbol']}],
                    "data": {"quote": r}}
        return {"reply": f"未找到 {symbol} 的行情。", "tools": [
            {"icon": "📈", "name": "行情查询", "status": "warn", "detail": "未找到"}]}
    if name == "explain_x402":
        return _run_payment()
    if name == "list_skills":
        return _run_skills()
    if name == "find_skills":
        return _run_find_skills(args.get("keyword") or "")
    if name == "onchain_ops":
        return _run_onchain()
    if name == "meme_watch":
        return _run_meme_watch(mode=(args.get("mode") or "both"))
    if name == "clarify":
        return _run_clarify(args)
    if name == "delegate":
        return _run_delegate(args)
    if name == "schedule_task":
        return _run_schedule_tool(
            action=args.get("action") or "list",
            name=args.get("name") or "",
            time_spec=args.get("time") or "",
            task=args.get("task") or "",  # 透传原始值：create 缺省 daily_scan_report，update 缺省=不改动
            job_id=args.get("job_id") or "",
            enabled=args.get("enabled", True),
            prompt=args.get("prompt") or "",
        )
    if name == "memory_write":
        return _run_memory_write(action=args.get("action", "add"), key=args.get("key", ""),
                                 text=args.get("text", ""), kind=args.get("kind", ""),
                                 agent_call=True)
    if name == "search_history":
        return _run_search_history(args.get("query") or "")
    if name == "todo_write":
        return _run_todo_tool(action=args.get("action", "add"), text=args.get("text", ""),
                              todo_id=args.get("todo_id", ""))
    if name == "fetch_url":
        return _run_fetch_url(args.get("url", ""))
    if name == "get_help":
        return _run_help()
    if name == "gateway_status":
        return _run_gateway_status()
    if name == "mcp_call":
        return _run_mcp_call(args)
    if name == "read_file":
        return _run_sandbox_file(args, op="read")
    if name == "write_file":
        return _run_sandbox_file(args, op="write", confirmed=confirmed)
    if name == "run_command":
        return _run_sandbox_cmd(args, confirmed=confirmed)
    if name == "run_skill":
        return _tool_run_skill(args, confirmed=confirmed)
    return {"reply": f"未知工具 {name}。", "tools": [
        {"icon": "⚠️", "name": "工具错误", "status": "error", "detail": name}]}


def _run_gateway_status() -> Dict[str, Any]:
    """聚合 Binance 各网关连接状态（Hermes 网关健康卡）。"""
    gates = []
    try:
        from mcp_client import mcp_status, mcp_list_tools
        ms = mcp_status()
        mt = mcp_list_tools()
        for s in ms.get("servers", []):
            gates.append({"name": f"MCP · {s.get('name', '')}",
                          "connected": bool(s.get("reachable")), "type": "mcp",
                          "detail": (s.get("detail") or s.get("error") or "")[:160],
                          "auth": bool(s.get("token") or s.get("authed"))})
        mcp_tool_count = len(mt.get("tools", []))
    except Exception as e:
        mcp_tool_count = 0
        gates.append({"name": "MCP", "connected": False, "type": "mcp", "detail": str(e)[:120]})
    try:
        from wallet_client import get_status
        w = get_status()
        gates.append({"name": "Agentic Wallet", "connected": bool(w.get("connected")), "type": "wallet",
                      "detail": (w.get("detail") or "")[:160]})
    except Exception:
        pass
    try:
        from x402_client import get_supported_configs
        x = get_supported_configs()
        gates.append({"name": "x402 Facilitator", "connected": x.get("status") == "ok", "type": "x402",
                      "detail": (x.get("detail") or (f"{len(x.get('data', []))} 资产配置可用" if x.get('data') else ""))[:160]})
    except Exception:
        pass
    try:
        from skills_client import list_installed
        sk = list_installed()
        gates.append({"name": "Skills Hub", "connected": True, "type": "skills",
                      "detail": f"{len(sk)} 个已装技能"})
    except Exception:
        pass
    on = sum(1 for g in gates if g.get("connected"))
    lines = [f"- {'✅' if g['connected'] else '❌'} **{g['name']}**" +
             (f" · {g['detail']}" if g.get("detail") else "") for g in gates]
    reply = (f"网关状态：{on}/{len(gates)} 在线\n" + "\n".join(lines) +
             (f"\n\nMCP 运行时可用工具：{mcp_tool_count} 个（登录后 tools/list 发现）。" if mcp_tool_count else ""))
    return {"reply": reply,
            "tools": [{"icon": "🛰️", "name": "网关状态", "status": "success",
                       "detail": f"{on}/{len(gates)} online"}],
            "data": {"gateways": gates, "mcp_tools": mcp_tool_count}}


def _run_mcp_call(args: dict) -> Dict[str, Any]:
    """调用 MCP 网关真实工具。缺 token 时给出引导（OAuth），不裸抛底层异常。"""
    server = (args.get("server") or "binance").strip()
    tool = (args.get("tool") or "").strip()
    arguments = args.get("arguments") or {}
    if not tool:
        return {"reply": "缺少 tool 参数。可先 gateway_status 查看可用网关，或用 tools/list 发现工具。",
                "tools": [{"icon": "🛰️", "name": "MCP 调用", "status": "error", "detail": "缺 tool"}]}
    try:
        from mcp_client import call_tool, server_status, oauth_start
        st = server_status(server)
        if not st.get("token") and not st.get("reachable"):
            hint = ""
            try:
                oauth_start(server)
                hint = "已发起浏览器授权（本地回调 127.0.0.1:8787），完成后重试。"
            except Exception as oe:
                hint = f"OAuth 启动失败：{oe}"
            return {"reply": f"网关「{server}」未授权，无法调用 {tool}。{hint}",
                    "tools": [{"icon": "🔐", "name": f"MCP {server}", "status": "error",
                               "detail": "需 OAuth 授权"}],
                    "data": {"needs_oauth": True, "server": server, "tool": tool}}
        res = call_tool(server, tool, arguments)
        ok = bool(res.get("ok"))
        txt = (res.get("text") or res.get("error") or res.get("result") or "")[:1500]
        import json as _json
        try:
            extra = _json.dumps(res.get("data", {}), ensure_ascii=False)[:800]
        except Exception:
            extra = ""
        return {"reply": (f"MCP {server}.{tool} 调用成功：\n{txt}" + (f"\n\n数据：{extra}" if extra else "")) if ok
                          else f"MCP {server}.{tool} 失败：{txt}",
                "tools": [{"icon": "🛰️", "name": f"MCP {server}.{tool}", "status": "success" if ok else "error",
                           "detail": (res.get("error") or res.get("detail") or tool)[:120]}],
                "data": res.get("data", {})}
    except Exception as e:
        return {"reply": f"MCP 调用异常：{type(e).__name__}: {str(e)[:200]}",
                "tools": [{"icon": "🛰️", "name": f"MCP {server}.{tool}", "status": "error", "detail": str(e)[:120]}]}


def _run_fetch_url(url: str):
    """基础技能：抓取 URL 并转纯文本（Hermes 网络情报 Level 1；真机可用）。"""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"reply": "请提供合法的 http(s) URL。", "tools": [
            {"icon": "🌐", "name": "网页抓取", "status": "warn", "detail": "URL 非法"}]}
    try:
        import re as _re
        import requests as _req
        r = _req.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (Alpha-Scout-Agent/1.0)"})
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "json" in ctype:
            txt = r.text[:4000]
        elif "html" in ctype or "text" in ctype:
            txt = _re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", r.text)
            txt = _re.sub(r"<[^>]+>", " ", txt)
            txt = _re.sub(r"\s+", " ", txt).strip()[:4000]
        else:
            txt = r.text[:2000]
        return {"reply": f"抓取成功（{len(r.content)} 字节）：\n\n{txt}",
                "tools": [{"icon": "🌐", "name": "网页抓取", "status": "success", "detail": url[:60]}],
                "data": {"url": url, "bytes": len(r.content)}}
    except Exception as e:
        return {"reply": f"抓取失败：{e}",
                "tools": [{"icon": "🌐", "name": "网页抓取", "status": "error", "detail": str(e)[:120]}]}


def _tool_propose_trade(symbol: str, direction: str = "BULLISH"):
    if not symbol:
        return _run_scan()
    msg = f"{'买入' if str(direction).upper() == 'BULLISH' else '卖出'} {symbol}"
    return _run_execute(message=msg)


def _emit_llm_unavailable(message: str, locale: str = "zh"):
    """已接入 LLM 但模型调用失败时：给出明确提示，绝不回退规则引擎的固定话术。"""
    tip = ("⚠️ 已接入 LLM，但本次模型调用失败（请检查 API Key / 网络 / 模型名是否可用）。\n"
           "未接入 LLM 时才会自动使用内置回答；当前已接入，故不回退固定话术。")
    if _lang(locale) == "en":
        tip = ("⚠️ LLM is configured but this call failed (check API Key / network / model name).\n"
               "Built-in replies only kick in when no LLM is configured; since it is, we do not fall back to canned text.")
        tname = "LLM call failed"
    yield {"type": "text", "delta": tip}
    yield {"type": "done", "intent": "llm", "reply": tip,
           "tools": [{"icon": "⚠️", "name": tname if _lang(locale) == "en" else "LLM 调用失败", "ok": False,
                      "detail": llm.get_llm_config().get("model")}]}


def _emit_dispatch(out: Dict[str, Any]):
    """把 dispatch 返回的结果 dict 展开成 NDJSON 事件流。"""
    for tool in out.get("tools", []):
        yield {"type": "tool", "tool": _norm_tool(tool)}
        time.sleep(0.04)
    for chunk in _chunk_text(out.get("reply", "")):
        yield {"type": "text", "delta": chunk}
        time.sleep(0.012)
    if out.get("data") is not None:
        yield {"type": "data", "kind": out.get("intent", "chat"), "payload": out["data"]}
    yield {"type": "done", "intent": out.get("intent", "chat"), "reply": out.get("reply", ""),
           "tools": _norm_tools(out.get("tools", [])), "needs_approval": out.get("needs_approval", False),
           "approval": out.get("approval")}


def _intent_to_tool(intent: str) -> Optional[str]:
    """把 _detect() 命中的语义意图映射到要强制调用的工具名（防止 LLM 漏调）。"""
    m = {
        "scan": "scan_market",
        "risk": "check_risk",
        "report": "scan_market",
        "execute": "propose_trade",
        "payment": "explain_x402",
        "onchain": "onchain_ops",
        "meme": "meme_watch",
        "skills": "list_skills",
        "memory_write": "memory_write",
        "memory_read": "memory_write",
        "cron": "schedule_task",
    }
    return m.get(intent)


def _build_system(persona: dict = None, locale: str = "zh") -> str:
    base = _system_prompt(locale)
    if persona and persona.get("name"):
        cfg = persona.get("config") or {}
        tone = (cfg.get("tone") or "").strip()
        p = f"\n\n【当前对话的 Agent 档案（Bots）】\n" \
            f"- 你是档案「{persona.get('name','')}」{persona.get('title','') or ''}\n" \
            f"- 档案描述: {persona.get('description','') or '通用交易助手'}"
        if tone:
            p += f"\n- 语气与风格要求: {tone}"
        model = (cfg.get("model") or "").strip()
        if model:
            p += f"\n- 偏好模型: {model}（仅在可用时采用）"
        prompt = (cfg.get("prompt") or "").strip()
        if prompt:
            p += f"\n\n【本档案专属设定（bot.md 正文）】\n{prompt}"
        p += f"\n- 请以该档案的身份与口吻回答，同时仍遵守上面的风控与真实工具规则。"
        return base + p
    return base


# ---------------- 受限本地执行（文件读写 / 跑命令 / 跑技能，全部走沙箱白名单） ----------------

# ---------------- 审批白名单（用户勾选「信任并执行」后持久化，命中自动放行） ----------------

def _wl_load() -> list:
    """读取审批白名单规则列表（settings 表持久化）。"""
    try:
        from state import get_setting
        raw = get_setting("approval_whitelist", "[]")
        items = json.loads(raw or "[]")
        return [str(x) for x in items] if isinstance(items, list) else []
    except Exception:
        return []


def _wl_save(items: list):
    try:
        from state import set_setting
        set_setting("approval_whitelist", json.dumps(items, ensure_ascii=False))
    except Exception:
        pass


def _wl_rule(op: str, args: dict) -> str:
    """把一次审批请求归约为规则 key（cmd:/write:/skill:）。"""
    args = args or {}
    if op == "run_command":
        c = str(args.get("command") or "").strip()
        return f"cmd:{c}" if c else ""
    if op == "write_file":
        p = str(args.get("path") or "").strip()
        return f"write:{p}" if p else ""
    if op == "run_skill":
        n = str(args.get("skill_name") or "").strip()
        return f"skill:{n}" if n else ""
    return ""


def _wl_has(op: str, args: dict) -> bool:
    rule = _wl_rule(op, args)
    return bool(rule) and rule in _wl_load()


def _wl_add(op: str, args: dict) -> bool:
    rule = _wl_rule(op, args)
    if not rule:
        return False
    cur = _wl_load()
    if rule in cur:
        return True
    cur.append(rule)
    _wl_save(cur)
    return True


def _run_sandbox_file(args: dict, op: str = "read", confirmed: bool = False) -> Dict[str, Any]:
    """read_file：直接读（沙箱内）；write_file：未确认时流 needs_approval，确认后真正写入。"""
    from exec_sandbox import SandboxError, read_text, write_text
    path = (args.get("path") or "").strip()
    if not path:
        return {"reply": "缺少 path 参数。", "tools": [
            {"icon": "💾", "name": "文件工具", "status": "error", "detail": "缺 path"}]}
    if op == "write":
        # 白名单命中（用户此前「信任并执行」过同路径）→ 自动放行
        if not confirmed and _wl_has("write_file", args):
            confirmed = True
        if not confirmed:
            return {"reply": f"准备写入文件 **{path}**（沙箱白名单目录）。确认后执行。",
                    "needs_approval": True,
                    "approval": {"action": "local_exec", "op": "write_file", "args": args,
                                 "title": f"写入文件 {path}", "label": "✅ 确认写入"},
                    "tools": [{"icon": "💾", "name": "写入文件（待确认）", "status": "info", "detail": path}]}
        content = args.get("content") or ""
        append = bool(args.get("append"))
        try:
            r = write_text(path, content, append=append)
        except SandboxError as e:
            return {"reply": f"⛔ 沙箱拦截：{e}", "tools": [
                {"icon": "💾", "name": "写入文件", "status": "error", "detail": str(e)[:140]}]}
        rel = os.path.relpath(r["path"], os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return {"reply": f"✅ 已{'追加' if append else '写入'} **{rel}**（{r['bytes']} 字节，{'新建' if r['created'] else '已存在'}）。",
                "intent": "tool", "data": r,
                "tools": [{"icon": "💾", "name": "写入文件", "status": "success", "detail": f"{rel} · {r['bytes']}B"}]}
    # read：自动执行（只读安全）
    try:
        r = read_text(path)
    except SandboxError as e:
        return {"reply": f"⛔ 沙箱拦截：{e}", "tools": [
            {"icon": "📄", "name": "读取文件", "status": "error", "detail": str(e)[:140]}]}
    rel = os.path.relpath(r["path"], os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    head = r["content"][:600]
    tip = f"\n\n（文件共 {r['bytes']} 字节，以下为前 {len(r['content'])} 字" + ("，已截断" if r["truncated"] else "）")
    return {"reply": f"📄 **{rel}**\n\n```\n{head}\n```" + tip,
            "intent": "tool", "data": r,
            "tools": [{"icon": "📄", "name": "读取文件", "status": "success", "detail": rel}]}


def _run_sandbox_cmd(args: dict, confirmed: bool = False) -> Dict[str, Any]:
    """run_command：未确认时流 needs_approval，确认后真正执行（沙箱白名单）。"""
    from exec_sandbox import SandboxError, run_command as _run
    cmd = (args.get("command") or "").strip()
    if not cmd:
        return {"reply": "缺少 command 参数。", "tools": [
            {"icon": "⚡", "name": "命令执行", "status": "error", "detail": "缺 command"}]}
    # 白名单命中（用户此前「信任并执行」过同命令）→ 自动放行
    if not confirmed and _wl_has("run_command", args):
        confirmed = True
    if not confirmed:
        return {"reply": f"准备在项目目录运行命令 **`{cmd[:80]}`**（白名单 python/node/npx/npm/git，15s 超时）。确认后执行。",
                "needs_approval": True,
                "approval": {"action": "local_exec", "op": "run_command", "args": args,
                             "title": f"运行命令 {cmd[:48]}", "label": "▶ 确认运行"},
                "tools": [{"icon": "⚡", "name": "运行命令（待确认）", "status": "info", "detail": cmd[:80]}]}
    try:
        r = _run(cmd)
    except SandboxError as e:
        return {"reply": f"⛔ 沙箱拦截：{e}", "tools": [
            {"icon": "⚡", "name": "运行命令", "status": "error", "detail": str(e)[:140]}]}
    out = r["output"]
    ok = r["exit_code"] == 0
    snippet = out[:1200]
    tail = f"\n\n（输出 {len(out)} 字符，已截断）" if r["truncated"] else ""
    detail = f"exit={r['exit_code']} · {r['elapsed']}s" + (("\n" + out[:240].rstrip()) if out else "")
    return {"reply": f"{'✅' if ok else '⚠️'} 退出码 {r['exit_code']}（{r['elapsed']}s）`{r['cmd'][:60]}`\n\n```\n{snippet}\n```{tail}",
            "intent": "tool", "data": r,
            "tools": [{"icon": "⚡", "name": "运行命令", "status": "success" if ok else "warn", "detail": detail}]}


def _is_wallet_skill(name: str) -> bool:
    """是否 Agentic Wallet 相关技能（baw / binance-agentic-wallet 等）。"""
    n = (name or "").lower()
    return any(k in n for k in ["wallet", "agentic", "baw", "onchain", "chain"])


def _wallet_fail_hint(out: str) -> str:
    """baw / Agentic Wallet 技能失败 → 针对钱包侧的分诊文案（避免混成交易所/通用错误）。"""
    o = (out or "").lower()
    if re.search(r"not logged|not connected|login|qr ?code|扫码|expired|unauthorized|forbidden|401|403|session|请登录|未连接|未登录|会话", o):
        return ("**Agentic Wallet 未登录 / 未连接。** 请到「钱包」面板点连接、用手机 App 重新扫码登录 baw"
                "（MPC 无密钥），并确认会话未过期。")
    if re.search(r"command not found|no such command|unknown command|not a command|invalid .{0,20}(option|command|arg)|usage:", o):
        return ("**baw 命令语法或版本问题。** 可先跑 `baw wallet status` 自检；参考 `baw market-order quote/swap`、"
                "`baw limit-order buy/sell`。")
    if re.search(r"slippage|price impact|滑点|价格影响", o):
        return ("**滑点 / 价格影响超限。** 可提高滑点容忍度，或改用限价单（`baw limit-order`）。")
    if re.search(r"insufficient|balance|余额|资金不足", o):
        return ("**链上余额不足。** 请确认所选链（BSC / ETH / Base / SOL）上有足够本币与 gas 后再试。")
    if re.search(r"daily limit|limit (reached|exceeded)|cap|上限|限额|quota", o):
        return ("**触发币安官方每日限额**（兑换 $50k / DeFi $100k / x402 $20）。超出部分请改日再试，或改走 CEX 下单。")
    if re.search(r"approve|allowance|授权", o):
        return ("**需要先授权代币。** 可先让 Agent 执行 `baw approvals` 授权后再交易。")
    if re.search(r"network|timeout|connect|rpc|网络|超时|econnreset", o):
        return ("**网络 / RPC 问题。** 请检查网络连接后重试。")
    return ""


def _is_square_post_skill(name: str) -> bool:
    n = (name or "").lower()
    return "square" in n or "广场" in n or "square-post" in n


def _square_post_fail_hint(out: str) -> str:
    """square-post 技能失败 → 按 Square OpenAPI 错误码 / 常见环境问题分诊。"""
    o = (out or "")
    ol = o.lower()
    # 错误码优先
    if re.search(r"220003|api ?key ?(not found|missing|未)", o):
        return ("**Square OpenAPI Key 未配置。** 请到创作者中心 https://www.binance.com/square/creator-center/home 生成，"
                "再设环境变量 `BINANCE_SQUARE_OPENAPI_KEY=<key>` 或存到 `~/.config/binance-square/openapi-key` 后再试。")
    if re.search(r"220004|key ?(expired|invalid)", ol) and re.search(r"key|key", ol):
        return ("**Square OpenAPI Key 已过期 / 无效。** 请到创作者中心重生成并更新 `BINANCE_SQUARE_OPENAPI_KEY`。")
    if re.search(r"220009|daily post limit|每日.*(帖|发布)", ol):
        return ("**触发每日发帖上限**（OpenAPI 100 帖 / 日）。请改日再试或减少同主题连发。")
    if re.search(r"220014|daily upload limit|每日.*上传", ol):
        return ("**触发每日媒体上传上限**（400 次 / 日）。请改日再试或减少上传次数。")
    if re.search(r"20002|20022|sensitive|敏感词", o):
        return ("**内容含敏感词。** 请去掉或替换触发词后重试。")
    if re.search(r"20013|content length|内容过长|too long", ol):
        return ("**内容超过单帖长度限制。** 长文改用 article 子命令 + 标题，或拆成多帖。")
    if re.search(r"20020|220011|empty|内容为空", o):
        return ("**内容为空。** 给 run_skill 传入的 `--text` 文本不可为空。")
    if re.search(r"30008|2000001|2000002|account|device|账号|设备", o):
        return ("**账号或设备发帖受限。** 请到币安 App 端广场检查账号状态或解除限制后再试。")
    if re.search(r"ffmpeg|ffprobe", ol):
        return ("**缺少 ffmpeg / ffprobe。** 视频发帖需要从视频抽帧作封面，请先 `winget install ffmpeg` 或装好后确保 PATH 里有 `ffmpeg` 与 `ffprobe`。")
    if re.search(r"command not found|no such file|enoent|cannot find module", ol):
        return ("**Node 环境或脚本路径问题。** 请确认 Node ≥18、`node scripts/cli.mjs` 在 square-post 目录下可执行；"
                "或直接走 run_skill 工具重试。")
    if re.search(r"network|timeout|econnreset|fetch failed|网络|超时", ol):
        return ("**网络问题。** 检查网络后重试；若持续失败，多为币安广场 OpenAPI 网关临时不可用。")
    return ""


def _tool_run_skill(args: dict, confirmed: bool = False) -> Dict[str, Any]:
    """run_skill：本地可执行类技能（baw / cli.mjs）需确认后经沙箱执行；纯指引类直接返回说明。"""
    from exec_sandbox import SandboxError, run_skill_cmd, skill_is_executable
    import skills_client
    name = (args.get("skill_name") or "").strip()
    arg_s = args.get("args") or ""
    installed = set(skills_client.list_installed())
    if name not in installed:
        if _is_wallet_skill(name):
            return {"reply": (f"技能 `{name}` 未安装。\n\n"
                              "**Agentic Wallet 技能缺失：** 请到「技能 / Skills」面板安装官方 `binance-agentic-wallet`"
                              "（或先 `npm i -g @binance/agentic-wallet` 装 baw CLI），装好后再执行钱包操作。"),
                    "tools": [{"icon": "🧰", "name": "执行技能", "status": "error", "detail": "未安装（钱包技能）"}]}
        if _is_square_post_skill(name):
            return {"reply": (f"技能 `{name}` 未安装。\n\n"
                              "**广场发文技能缺失：** 请到「技能 / Skills」面板安装官方 `square-post`（无需 MCP / OAuth 授权），"
                              "装好后再发币安广场。"),
                    "tools": [{"icon": "🧰", "name": "执行技能", "status": "error", "detail": "未安装（广场技能）"}]}
        return {"reply": f"技能 `{name}` 未安装。可用：{', '.join(sorted(installed)[:8]) or '（无）'}",
                "tools": [{"icon": "🧰", "name": "执行技能", "status": "error", "detail": "未安装"}]}
    if not skill_is_executable(name):
        info = skills_client.get_skill_info(name) or {}
        return {"reply": f"技能 **{name}** 为指引类（无本地脚本）。要点：\n{(info.get('summary') or info.get('description') or info.get('guide') or '见 SKILL.md')[:800]}",
                "intent": "tool",
                "tools": [{"icon": "🧰", "name": "执行技能", "status": "info", "detail": "指引类 · 无需执行"}]}
    # 白名单命中（用户此前「信任并执行」过同技能）→ 自动放行
    if not confirmed and _wl_has("run_skill", args):
        confirmed = True
    if not confirmed:
        return {"reply": f"准备执行技能 **{name}**（参数：{arg_s or '—'}）。确认后运行。",
                "needs_approval": True,
                "approval": {"action": "local_exec", "op": "run_skill", "args": args,
                             "title": f"执行技能 {name}", "label": "▶ 确认执行"},
                "tools": [{"icon": "🧰", "name": f"技能 {name}（待确认）", "status": "info", "detail": arg_s[:60] or "执行"}]}
    try:
        r = run_skill_cmd(name, arg_s)
    except SandboxError as e:
        hint = ""
        if _is_wallet_skill(name):
            hint = _wallet_fail_hint(str(e))
        elif _is_square_post_skill(name):
            hint = _square_post_fail_hint(str(e))
        return {"reply": f"⛔ 技能执行拦截：{e}" + (f"\n\n{hint}" if hint else ""), "tools": [
            {"icon": "🧰", "name": f"技能 {name}", "status": "error", "detail": str(e)[:140]}]}
    ok = r["exit_code"] == 0
    out = r.get("output", "")
    detail = f"exit={r['exit_code']} · {r['elapsed']}s" + (("\n" + out[:240].rstrip()) if out else "")
    # 广场发帖记账：真实发布成功 / 失败都落本地台账（广场页展示），失败不打断主流程
    if name == "square-post":
        try:
            import square_store
            square_store.record_from_run(name, arg_s, out, r["exit_code"], via="agent")
        except Exception:
            pass
    reply = f"{'✅' if ok else '⚠️'} 技能 **{name}** 退出码 {r['exit_code']}（{r['elapsed']}s）\n\n```\n{r['output'][:1200]}\n```"
    if not ok:
        hint = ""
        if _is_wallet_skill(name):
            hint = _wallet_fail_hint(out)
        elif _is_square_post_skill(name):
            hint = _square_post_fail_hint(out)
        if hint:
            reply += "\n\n" + hint
    return {"reply": reply,
            "intent": "tool", "data": r,
            "tools": [{"icon": "🧰", "name": f"技能 {name}", "status": "success" if ok else "warn", "detail": detail}]}


def _dispatch_is_approval_needed(name: str) -> bool:
    """这些工具执行路径可能流 needs_approval（下单类/装 skill/写文件/跑命令/执行技能）→ 不与其它工具并行。"""
    base = name.split(".")[0] if "." in name else name
    return base in ("propose_trade", "execute", "execute_order", "install_skill", "memory_write",
                    "write_file", "run_command", "run_skill")


# Hermes bots：persona 可声明 config.tools 工具子集白名单（留空/缺省 = 全部工具）
_TOOL_UNIVERSAL = {"get_help", "memory_write", "memory_read", "fetch_url", "gateway_status",
                   "search_history", "todo_write", "clarify", "delegate"}


def _persona_allowed_tools(persona: dict) -> set:
    """返回该档案允许的工具名集合；未声明 tools 子集 → None 表示不限制。"""
    if not persona or not persona.get("name"):
        return None
    cfg = persona.get("config") or {}
    allow = cfg.get("tools")
    if not allow:
        return None
    return set(allow) | _TOOL_UNIVERSAL


def _persona_filter_tools(tools: list, persona: dict) -> list:
    allow = _persona_allowed_tools(persona)
    if allow is None:
        return tools
    return [t for t in tools if (t.get("function", {}).get("name") or "") in allow]


# ---------------- 图片输入：视觉直读 / OCR 兜底路由 ----------------
_VISION_HINTS = ("gpt-4o", "gpt-4.1", "gpt-4-vision", "vision", "qwen-vl", "qwen2-vl", "qwen2.5-vl",
                 "glm-4v", "glm-4.1v", "gemini", "gemma3", "internvl", "minicpm-v", "moondream",
                 "llava", "claude-3", "claude-3.5", "claude-3.7", "claude-sonnet-4", "claude-opus-4",
                 "yi-vl", "step-1v", "phi-3-vision", "pixtral")


def _model_is_vision(model: str) -> bool:
    m = (model or "").lower()
    return any(h in m for h in _VISION_HINTS)


def _vision_available(llm_cfg: dict) -> bool:
    """当前会话链路里是否存在能看图的模型（主模型 or aux.vision 槽）。"""
    names: list = []
    if isinstance(llm_cfg, dict):
        if llm_cfg.get("model"):
            names.append(str(llm_cfg["model"]))
        aux = llm_cfg.get("aux") or {}
        if isinstance(aux, dict) and aux.get("vision"):
            names.append(str(aux["vision"]))
    if not names:
        try:
            cfg = llm.get_llm_config() or {}
            names = [str(cfg.get("model", ""))]
        except Exception:
            names = []
    return any(_model_is_vision(n) for n in names)


def _data_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("data:"):
        return url
    return "data:image/png;base64," + url  # 裸 base64 按 png 包裹


def _build_user_message(message: str, images: list = None, llm_cfg: dict = None):
    """把用户消息 + 图片列表转成 LLM 的 user content。

    返回 (content, notes)：
    - 视觉模型 → OpenAI content parts 数组（image_url data URI）
    - 非视觉模型 → OCR 图片文字后拼进消息正文
    notes 是给前端/日志的中文说明。
    """
    imgs = [u for u in (images or []) if u and isinstance(u, str)]
    if not imgs:
        return message, []
    notes = []
    try:
        if _vision_available(llm_cfg):
            parts = [{"type": "text", "text": message or ""}]
            for i, url in enumerate(imgs, 1):
                parts.append({"type": "image_url", "image_url": {"url": _data_url(url)}})
            notes.append(f"已附 {len(imgs)} 张图片 → 走视觉模型直读")
            return parts, notes
    except Exception as e:
        notes.append(f"视觉判断异常，降级 OCR：{e}")
    # OCR 兜底
    try:
        from ocr_engine import ocr_data_url, OCRError
        blocks = []
        for i, url in enumerate(imgs, 1):
            try:
                txt = ocr_data_url(url).strip()
                blocks.append(f"图片{i} 文字：\n{txt}" if txt else f"图片{i}：未识别到文字")
            except OCRError as e:
                blocks.append(f"图片{i}：OCR 失败（{e}）")
        ocr_block = "\n\n".join(blocks)
        notes.append(f"已附 {len(imgs)} 张图片 → OCR 提取文字（当前模型不支持看图）")
        return f"{message or ''}\n\n[用户附带的图片内容]\n{ocr_block}", notes
    except Exception as e:
        notes.append(f"OCR 不可用：{e}")
        return f"{message or ''}\n\n[用户附带了 {len(imgs)} 张图片，但当前环境无法识别图片内容]", notes


# ---------------- 记住上下文：历史注入 + 超长压缩 ----------------
_CTX_MAX_HIST = 12          # 最多注入多少条历史（user/assistant 计为一条）
_CTX_MAX_CHARS = 9000       # 历史累计超过该长度 → 把最旧一半压成摘要
_CTX_MAX_MSG = 1600         # 单条历史正文截断


def _summarize_old(hist_lines: list, llm_cfg: dict, old_summary: str = "") -> str:
    """增量压缩：把被裁历史并入旧摘要，产出 ≤600 字滚动摘要（summarize 槽位模型；失败硬截断兜底）。

    v1.4.2 对齐 Hermes：摘要跨轮持久化复用，不再每次重烧；被裁内容不再蒸发。
    """
    text = "\n".join(hist_lines)
    if not text.strip():
        return old_summary or ""
    try:
        sysp = ("你是会话摘要器。把「已有摘要」与「新对话片段」合并成一段 ≤260 字中文滚动摘要："
                "保留用户偏好、已查过的标的与关键结论、已定的关键参数（如止损止盈价位）、"
                "进行中的任务与未完成事项。已有摘要仍然有效的内容直接保留，不要丢失。只输出摘要正文。")
        prompt = (("已有摘要：\n" + old_summary + "\n\n") if old_summary else "") + "新对话片段：\n" + text
        summ = llm.chat(sysp, prompt, temperature=0.2, max_tokens=400, llm_cfg=llm_cfg, task="summarize")
        if summ and summ.strip():
            return summ.strip()[:600]
    except Exception:
        pass
    if old_summary:
        # 已有摘要时保底：旧摘要 + 新片段硬截断拼接
        return (old_summary[:300] + "\n" + text[-300:])[:600]
    return text[:600]


def _history_blocks(history: list, llm_cfg: dict = None, cid: str = ""):
    """把 (role, text) 历史列表整理成注入用的 blocks 文本。

    - 只保留 user/assistant 两种角色文本（tool/系统噪音不带入）；
    - 数量上限 _CTX_MAX_HIST，**被裁掉的历史不蒸发**：与旧摘要增量合并成滚动摘要；
    - 有 cid 时摘要持久化到 conversations.ctx_summary + 游标 `ctxcur:{cid}`（settings 表，
      记录已摘要覆盖到第几条——重复请求不重烧摘要模型）；无 cid 退化为一次性临时压缩；
    - 若保留部分仍超长，最旧一半并入摘要。
    """
    import state as _state  # 局部导入避免循环依赖
    hist_all = [h for h in (history or []) if isinstance(h, dict) and h.get("role") in ("user", "assistant")]
    if not hist_all:
        return []
    old_summary = ""
    covered = 0
    try:
        if cid:
            old_summary = _state.get_conv_summary(cid)
            covered = int(_state.get_setting(f"ctxcur:{cid}", "0") or 0)
    except Exception:
        old_summary, covered = "", 0
    covered = max(0, min(covered, len(hist_all)))
    evicted, overflow, hist = [], [], hist_all
    if len(hist_all) > _CTX_MAX_HIST:
        cut = len(hist_all) - _CTX_MAX_HIST
        hist = hist_all[cut:]
        evicted = hist_all[covered:cut]  # 只压缩「新」被裁片段（游标增量）
        if not cid:
            evicted = hist_all[:cut]     # 临时会话无游标：全量被裁片段一次性压缩
    # 数值化：每条文本允许截断
    pairs = []
    total = 0
    for h in hist:
        t = (h.get("content") or "").strip()
        if not t:
            continue
        t = t[:_CTX_MAX_MSG]
        pairs.append((h["role"], t))
        total += len(t)
    if total > _CTX_MAX_CHARS:
        mid = max(1, len(pairs) // 2)
        overflow = pairs[:mid]
        pairs = pairs[mid:]
    # 被裁内容（数量裁剪 + 超长裁剪）→ 增量滚动摘要
    if evicted or overflow:
        ev_lines = [f"{'用户' if h.get('role') == 'user' else '助手'}：{(h.get('content') or '')[:400]}"
                    for h in evicted]
        ov_lines = [f"{'用户' if r == 'user' else '助手'}：{t}" for r, t in overflow]
        new_summ = _summarize_old(ev_lines + ov_lines, llm_cfg, old_summary)
        if new_summ and new_summ != old_summary:
            old_summary = new_summ
            if cid:
                try:
                    _state.set_conv_summary(cid, old_summary)
                    _state.set_setting(f"ctxcur:{cid}", str(max(covered, len(hist_all) - _CTX_MAX_HIST
                                                                if len(hist_all) > _CTX_MAX_HIST else covered)))
                except Exception:
                    pass
    blocks = []
    if old_summary:
        blocks.append(("ctx", "[此前对话摘要·持续更新] " + old_summary))
    blocks.extend(pairs)
    return blocks


def _run_llm_agent(message: str, confirm: bool = False, signal: dict = None, approval: dict = None,
                   persona: dict = None, llm_cfg: dict = None, images: list = None,
                   auto_exec: bool = False, history: list = None, locale: str = "zh",
                   cid: str = "", blocked_tools: set = None):
    """真 LLM 接上（Hermes 风格 function-calling 循环）。

    1) LLM 看 system + 历史上下文(history) + 用户消息 + TOOLS(含插件命令)，决定调用哪些工具；
    2) 我们执行工具（复用现有 _run_* 能力 + plugin_host），把结果回传给 LLM；
    3) LLM 合成最终中文回答；需要审批的动作（propose_trade）流出 approval 事件；
    4) 任何失败都降级到规则引擎 dispatch，保证不崩。
    llm_cfg: 会话级 provider 快照（防漂移）。images: 用户附图（dataURL），视觉直读或 OCR。
    history: 同会话前序消息（记住上下文），超长自动压缩。
    """
    # 把 deep_thinking 设置并入 llm_cfg（默认开，让模型像 WorkBuddy 那样真思考）
    llm_cfg = dict(llm_cfg or {})
    if "deep_thinking" not in llm_cfg:
        try:
            from state import get_setting as _gs
            llm_cfg["deep_thinking"] = _gs("deep_thinking", "1") not in ("0", "false", "False", "")
        except Exception:
            llm_cfg["deep_thinking"] = True
    yield {"type": "meta", "intent": "llm", "persona": (persona or {}).get("name") or PERSONA_NAME}
    system = _build_system(persona, locale)
    user_content, img_notes = _build_user_message(message, images, llm_cfg)
    if img_notes:
        yield {"type": "reasoning", "text": img_notes[0]}
    messages = [{"role": "system", "content": system}]
    # 记住上下文：历史块注入（被裁部分增量并入持久化滚动摘要）
    ctx_blocks = _history_blocks(history, llm_cfg, cid=cid)
    for role, text in ctx_blocks:
        if role == "ctx":
            messages.append({"role": "system", "content": text})  # 摘要以 system 注入（不带对话轮次属性）
        else:
            messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": user_content})
    accumulated_tools = []
    model_used = ""
    forced_once = False  # _detect 兜底只在首轮强制一次，防多轮重复派发卡死
    tools = _filter_tool_schemas(_persona_filter_tools(llm.TOOLS + llm.plugin_tools(), persona),
                                 blocked_tools)
    turns = 0
    heal_left = _HEAL_MAX  # 工具失败自愈预算（同一请求内共享）
    ever_failed = False    # 出现过工具失败（用于轮次用尽时区分提示语）
    # v1.4.2 循环健壮性（Hermes repetition_guard / empty_response_guard / iteration 预警）
    recent_calls: list = []      # 已执行的工具签名（name+args 摘要），防同参重复调用
    empty_retry_left = 1         # 空回复重试预算（用尽不再空转）
    warned_wrap_up = False       # 轮次将尽预警只注入一次

    def _tc_sig(tc) -> str:
        try:
            digest = hashlib.md5(json.dumps(tc.get("args", {}), sort_keys=True,
                                            ensure_ascii=False).encode("utf-8")).hexdigest()[:10]
        except Exception:
            digest = str(tc.get("args", {}))[:40]
        return f"{tc.get('name', '?')}:{digest}"

    while turns < MAX_AGENT_TURNS:  # 长链研究（行情/合约多币对比/多步分析）轮次充足；见常量说明
        turns += 1
        # 轮次将尽预警：让 LLM 在硬截止前汇总已有信息作答，而不是被掐断
        if (not warned_wrap_up) and accumulated_tools and turns >= MAX_AGENT_TURNS - 2:
            warned_wrap_up = True
            messages.append({"role": "user", "content":
                             "（系统提示）工具调用轮次即将用尽。请立即基于已获得的工具结果"
                             "汇总出最终回答，不要再发起新的工具调用。"})
        try:
            resp = llm.chat_with_tools(messages, tools, llm_cfg=llm_cfg)
        except Exception:
            resp = None
        if not resp:
            # 模型调用失败：先尝试一次纯对话兜底（仍是 LLM 自己回答），
            # 仍失败才给出明确提示——绝不回退规则引擎的固定话术。
            try:
                plain = llm.chat(_build_system(persona, locale), message, llm_cfg=llm_cfg)
            except Exception:
                plain = None
            if plain:
                model_used = model_used or (llm_cfg or {}).get("model") or llm.get_llm_config().get("model", "")
                for chunk in _chunk_text(_strip_preamble(plain)):
                    yield {"type": "text", "delta": chunk}
                yield {"type": "done", "intent": "llm", "reply": plain, "model": model_used,
                       "tools": [{"icon": "💬", "name": "LLM 对话", "status": "success",
                                  "detail": model_used}]}
                return
            yield from _emit_llm_unavailable(message, locale)
            return
        content = resp.get("content") or ""
        tool_calls = resp.get("tool_calls") or []
        model_used = resp.get("model") or model_used
        # 思考链（Hermes 渲染范式第一块：可折叠灰色思考区）
        reasoning = resp.get("reasoning") or ""
        if reasoning:
            yield {"type": "reasoning", "text": reasoning, "model": resp.get("model") or model_used}
        if not tool_calls:
            # LLM 没主动调工具 → 用 _detect 兜底强制派发用户意图对应的工具。
            # 仅当本轮尚未执行过任何工具时强推一次（避免真实调用后第 2 轮
            # 的最终回答被再次强推，造成重复扫描/卡死）。
            forced_cand = (not forced_once) and (not accumulated_tools) and _intent_to_tool(_detect(message))
            allowed = _persona_allowed_tools(persona)
            forced = forced_cand and (allowed is None or forced_cand in allowed)
            if forced:
                forced_once = True
                res = _dispatch_tool(forced, {}, confirmed=auto_exec)
                if res.get("tools"):
                    for t in res["tools"]:
                        nt = _norm_tool(t)
                        accumulated_tools.append(nt)
                        yield {"type": "tool", "tool": nt}
                if res.get("data") is not None:
                    yield {"type": "data", "kind": res.get("intent", "tool"), "payload": res["data"]}
                if res.get("needs_approval"):
                    yield {"type": "approval", "approval": res.get("approval")}
                    yield {"type": "done", "intent": "llm", "reply": res.get("reply", ""),
                           "tools": accumulated_tools, "model": model_used,
                           "needs_approval": True, "approval": res.get("approval")}
                    return
                raw_data = res.get("data")
                if raw_data not in (None, {}):
                    tmsg = _tool_msg_from_res(forced, res)
                else:
                    tmsg = (res.get("reply") or "")[:1600]
                fail = _tool_fail_desc(res)
                if fail:
                    ever_failed = True
                    if heal_left > 0:
                        heal_left -= 1
                    tmsg += _heal_hint(fail, heal_left)
                messages.append({"role": "assistant", "content": content or "",
                                  "tool_calls": [{"id": "forced-" + forced, "type": "function",
                                                  "function": {"name": forced, "arguments": "{}"}}]})
                messages.append({"role": "tool", "tool_call_id": "forced-" + forced,
                                 "content": tmsg})
                continue  # 进下一轮让 LLM 看到 tool 结果再合成最终回答
            # 已是最终回答轮（或意图不匹配）：把 LLM 正文流式输出（剥掉「我先…让我…」客套）
            if not content.strip() and not accumulated_tools:
                # 模型既没调工具又没输出正文（偶发空返回）→ 用有限预算追加一次明确指令再生成，
                # 预算用尽直接以空回复收尾，不再空转烧轮次（Hermes empty_response_guard）。
                if empty_retry_left > 0 and turns < MAX_AGENT_TURNS:
                    empty_retry_left -= 1
                    messages.append({"role": "user", "content": "（你刚才没有输出任何内容）请直接回答用户的问题。"})
                    continue
            if content.strip():
                for chunk in _chunk_text(_strip_preamble(content)):
                    yield {"type": "text", "delta": chunk}
            yield {"type": "done", "intent": "llm", "reply": content, "model": model_used,
                   "tools": _norm_tools(accumulated_tools) or [{"icon": "💬", "name": "LLM 对话", "ok": True,
                                              "detail": model_used}]}
            return
        # 有工具调用：先展示 LLM 的旁白（如有，剥掉客套）
        if content.strip():
            for chunk in _chunk_text(_strip_preamble(content)):
                yield {"type": "text", "delta": chunk}
        # 记录 assistant 消息（含 tool_calls）以便回传
        assistant_tc = [{"id": tc["id"], "type": "function",
                         "function": {"name": tc["name"],
                                      "arguments": json.dumps(tc.get("args", {}), ensure_ascii=False)}}
                        for tc in tool_calls]
        messages.append({"role": "assistant", "content": content, "tool_calls": assistant_tc})
        # 并行执行多个 tool_calls（Hermes：同一轮互不依赖的工具同时跑，结果按原序回传）
        # 需要审批（needs_approval）的动作（如 propose_trade/execute）不并行——审批要立刻停住等用户。
        def _run_one(tc):
            return tc, _dispatch_tool(tc["name"], tc.get("args", {}), confirmed=auto_exec)

        # 重复调用防护（Hermes repetition_guard）：同签名已真实执行 ≥2 次 → 不再执行，
        # 直接回传提示让 LLM 基于已有结果作答（省 API 调用，防同参死循环烧到轮次上限）。
        # 审批类工具不拦（交易参数不变的重试是合理用户行为）。
        pending = []
        for tc in tool_calls:
            sig = _tc_sig(tc)
            if (not _dispatch_is_approval_needed(tc["name"])) and recent_calls.count(sig) >= 2:
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": f"[{tc['name']}] 相同参数已执行过，结果不会变化。"
                                            "请直接基于已有工具结果汇总作答，不要重复调用。"})
                continue
            pending.append((tc, sig))
        if not pending:
            continue  # 本轮全部被拦截 → 直接进下一轮让 LLM 看到提示后收尾
        tool_calls_exec = [tc for tc, _sig in pending]
        if len(tool_calls_exec) <= 1 or any(_dispatch_is_approval_needed(tc["name"]) for tc in tool_calls_exec):
            results = [_run_one(tc) for tc in tool_calls_exec]
        else:
            try:
                with ThreadPoolExecutor(max_workers=min(len(tool_calls_exec), 4)) as pool:
                    results = list(pool.map(_run_one, tool_calls_exec))
            except Exception:
                results = [_run_one(tc) for tc in tool_calls_exec]
        for sig, (tc, res) in zip([s for _tc, s in pending], results):
            recent_calls.append(sig)
            if len(recent_calls) > 8:
                del recent_calls[:len(recent_calls) - 8]
        for tc, res in results:
            for t in res.get("tools", []):
                accumulated_tools.append(_norm_tool(t))
                yield {"type": "tool", "tool": _norm_tool(t)}
            if res.get("data") is not None:
                yield {"type": "data", "kind": res.get("intent", "tool"), "payload": res["data"]}
            # 需要审批：流出 approval，等待用户确认后由 dispatch 精确处理
            if res.get("needs_approval"):
                yield {"type": "approval", "approval": res.get("approval")}
                yield {"type": "done", "intent": "llm", "reply": res.get("reply", ""),
                       "tools": accumulated_tools, "model": model_used,
                       "needs_approval": True, "approval": res.get("approval")}
                return
            # 结构化追问（v1.4.5 clarify）：流出选择题卡，暂停等用户点选；
            # 超时回退由前端负责（120s 未选自动以『最佳判断继续』作为下一条消息）。
            if res.get("needs_clarify"):
                yield {"type": "clarify", "clarify": res.get("clarify")}
                yield {"type": "done", "intent": "llm", "reply": res.get("reply", ""),
                       "tools": accumulated_tools, "model": model_used,
                       "needs_clarify": True, "clarify": res.get("clarify")}
                return
            # 工具结果回传给 LLM：优先给原始 data JSON（LLM 自己组织语言，避免照抄 reply 的成品模板）；
            # 无 data 或异常时才退回 reply 文本。超长输出落盘截断（_tool_msg_from_res）。
            tool_msg = _tool_msg_from_res(tc["name"], res)
            fail = _tool_fail_desc(res)
            if fail:
                ever_failed = True
                if heal_left > 0:
                    heal_left -= 1
                tool_msg += _heal_hint(fail, heal_left)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": tool_msg})
    # 超过轮次保护：明确提示，并给具体可换路径清单，不让用户陷入死胡同
    if ever_failed:
        if _lang(locale) == "en":
            tip = ("I tried multiple rounds but still couldn't get a complete result (tools kept failing or the heal budget ran out).\n\n"
                   "**Next steps** (pick one and I'll retry, or try it yourself and tell me):\n"
                   "1) The coin may not be on SPOT and only on USDT-margined PERPETUALS - query it via the public `fapi /fapi/v1/ticker/24hr?symbol=XXX` endpoint (plus `/fapi/v1/premiumIndex` for funding), no auth needed. Say \"try the fapi public endpoint again\";\n"
                   "2) Use `run_skill` with an installed skill such as `binance-agentic-wallet` / `binance-leaderboard` / `trading-signal` for its contract / on-chain data;\n"
                   "3) Use `fetch_url` to hit `fapi.binance.com/fapi/v1/ticker/24hr?symbol=XXX` or the exchange announcement page to verify it exists;\n"
                   "4) Use `run_command` to let my local Python call the public fapi endpoint directly (no API Key), bypassing the tool chain.\n"
                   "Tell me which path, or rephrase the question so I can try a different route.")
            tools_tip = [{"icon": "⚠️", "name": "Multiple attempts failed", "ok": False,
                          "detail": "Max turns reached · see suggestions"}]
        else:
            tip = ("我已经连续尝试多轮仍未拿到完整结果（工具持续报错或自愈次数已用尽）。\n\n"
               "**下一步建议**（你可直接选一条让我重试，或你自己操作后告诉我）：\n"
               "1) 该币可能在 SPOT 没上、只上了 U 本位永续 —— 我会用 `fapi /fapi/v1/ticker/24hr?symbol=XXX` 这条公开免授权接口直接拿合约价量+资金费率（前面已尝试过的话可以再说一次『用 fapi 公开端点再试一次』）；\n"
               "2) 用 `run_skill` 调官方 `binance-agentic-wallet` / `binance-leaderboard` / `trading-signal` 等已装技能查它的合约/链上数据；\n"
               "3) 用 `fetch_url` 直接抓 `fapi.binance.com/fapi/v1/ticker/24hr?symbol=XXX` 或交易所公告页验证存在性；\n"
               "4) 用 `run_command` 让我本地 Python 直接请求 fapi 公开接口（无需 API Key），绕过工具链路。\n"
               "告诉我用哪条，或换种问法让我换条路再试。")
            tools_tip = [{"icon": "⚠️", "name": "多次尝试失败", "ok": False,
                          "detail": "已达最大轮次 · 见建议清单"}]
        yield {"type": "text", "delta": tip}
        yield {"type": "done", "intent": "llm", "reply": tip, "model": model_used,
               "tools": tools_tip}
        return
    yield from _emit_llm_unavailable(message, locale)


def dispatch(message: str, confirm: bool = False, signal: dict = None,
             approval: dict = None, auto_exec: bool = False, locale: str = "zh") -> Dict[str, Any]:
    """规则引擎入口。auto_exec=True 时沙箱类工具跳过审批直接执行。"""
    # 审批回调：用户确认了某个待审批动作
    if approval:
        act = approval.get("action")
        if act == "execute_order" and approval.get("signal"):
            return _run_execute(confirm=True, signal=approval["signal"])
        if act == "install_skill" and approval.get("skill"):
            return _perform_install(approval["skill"])
        if act == "local_exec":
            # 用户确认受限本地操作：按 op 重新派发（沙箱白名单再次校验后才真正执行）
            aargs = approval.get("args") or {}
            op = approval.get("op")
            # 用户勾选「信任并执行」：先把规则写进持久化白名单，本次自动放行
            if approval.get("whitelist"):
                _wl_add(op, aargs)
            if op == "write_file":
                return _run_sandbox_file(aargs, op="write", confirmed=True)
            if op == "run_command":
                return _run_sandbox_cmd(aargs, confirmed=True)
            if op == "run_skill":
                return _tool_run_skill(aargs, confirmed=True)
            return {"reply": "未知的本地执行审批类型。", "tools": [
                {"icon": "⚠️", "name": "审批", "status": "error", "detail": op}]}

    intent = _detect(message)

    # 安全拦截
    if any(d in message.lower() for d in DANGEROUS):
        return _refuse_dangerous()

    if intent == "memory_write": return _run_memory_write(message)
    if intent == "memory_read": return _run_memory_read()
    if intent == "parallel": return _run_parallel()
    if intent == "cron": return _run_cron(message)
    if intent == "scan": return _run_scan(message)
    if intent == "risk": return _run_risk()
    if intent == "execute": return _run_execute(confirm=confirm, signal=signal, message=message)
    if intent == "report": return _run_report()
    if intent == "payment": return _run_payment()
    if intent == "onchain": return _run_onchain()
    if intent == "skills": return _run_skills()
    if intent == "discover": return _run_find_skills(message)
    if intent == "install": return _run_install(message)
    if intent == "tools": return _run_tools()
    if intent == "help": return _run_help()

    # 闲聊：有 LLM 走 LLM，否则规则兜底
    if llm.is_configured():
        narr = llm.chat(_system_prompt(locale), message)
        if narr:
            return {"intent": "chat", "reply": narr,
                    "tools": [{"icon": "💬", "name": "LLM 对话", "status": "success",
                               "detail": llm.get_llm_config().get("model")}]}
    if _lang(locale) == "en":
        intro = "I'm BAZZ Agent, connected to Binance Agent OS. Type \"help\" to see all skills, or try \"scan / parallel scan / scheduled scan\"."
        tname, tdet = "Chat", "Rule engine"
    else:
        intro = "我是 BAZZ Agent，已接入 Binance Agent OS。输入「帮助」查看全部能力，或试试「扫描 / 并行扫描 / 定时扫描」。"
        tname, tdet = "对话", "规则引擎"
    return {"intent": "chat",
            "reply": intro,
            "tools": [{"icon": "💬", "name": tname, "status": "info", "detail": tdet}]}


def handle(message: str, locale: str = "zh") -> dict:
    return dispatch(message, locale=locale)


def _chunk_text(text: str, size: int = 6) -> Iterator[str]:
    """激进流式：中文/英文标点立刻吐；连续字符每 size 个一吐；末尾兜底。"""
    if not text:
        return
    breaks = set("，。！？；：、\n,.!?;:)]）")
    buf = ""
    for ch in text:
        buf += ch
        if ch in breaks or len(buf) >= size:
            yield buf
            buf = ""
    if buf:
        yield buf


_PREAMBLE_PAT = re.compile(r"^[我让先好嗯OoKk]")


def _strip_preamble(text: str) -> str:
    """删除 LLM 输出的「我先…让我…我来…好的…」等客套开场白。
    规则：找到第一个句末标点（。！？），若该句以「我/让/先/好/ok/嗯」开头且包含「先/来/做」
    且不含数字且 ≤ 35 字 → 视为客套，整段删掉。"""
    if not text:
        return text
    m = re.search(r"[。！？]", text)
    if not m:
        return text
    first = text[:m.end()]
    rest = text[m.end():].lstrip()
    if not rest:
        return text  # 删完就空了，保留原文避免丢内容
    looks_formulaic = (
        bool(_PREAMBLE_PAT.match(first))
        and ("先" in first or "来" in first or "做" in first or "试试" in first or "调用" in first)
        and not re.search(r"\d", first)
        and len(first) <= 35
    )
    return rest if looks_formulaic else text


def run_stream(message: str, confirm: bool = False, signal: dict = None,
               approval: dict = None, persona: dict = None, llm_cfg: dict = None,
               images: list = None, auto_exec: bool = False,
               history: list = None, locale: str = "zh", cid: str = "") -> Iterator[Dict[str, Any]]:
    """产出 NDJSON 事件流。persona 可选：来自 Bots 页的 Agent 档案（身份/口吻）。
    llm_cfg 可选：会话级 provider 快照（防漂移）。images 可选：用户附图 dataURL 列表。
    auto_exec：全能模式（首页默认），沙箱工具跳过审批直接执行；交易类仍需确认。
    history：同会话前序消息（记住上下文）。cid：会话 id（滚动摘要持久化键）。"""
    # 审批回调（用户确认了某个待审批动作）始终走规则引擎精确处理
    if approval:
        yield from _emit_dispatch(dispatch(message, confirm=confirm, signal=signal, approval=approval,
                                           auto_exec=auto_exec, locale=locale))
        return

    # 真 LLM 已配置 → 走 function-calling agent 循环（Hermes 风格）
    if llm.is_configured(llm_cfg):
        yield from _run_llm_agent(message, confirm=confirm, signal=signal, approval=approval,
                                  persona=persona, llm_cfg=llm_cfg, images=images,
                                  auto_exec=auto_exec, history=history, locale=locale, cid=cid)
        return

    # 未配置 LLM → 规则引擎
    msg_for_rule, _notes = _build_user_message(message, images, None)
    msg_for_rule = msg_for_rule if isinstance(msg_for_rule, str) else message
    out = dispatch(msg_for_rule, confirm=confirm, signal=signal, approval=approval,
                   auto_exec=auto_exec, locale=locale)
    yield from _emit_dispatch(out)


if __name__ == "__main__":
    for ev in run_stream("并行扫描"):
        print(ev["type"], ev.get("delta") or ev.get("tool", {}).get("name") or ev.get("intent"))
