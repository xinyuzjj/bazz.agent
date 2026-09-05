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


_HEAL_MAX = 2  # 同一请求内工具失败的最大自愈轮次


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
                f"【自愈指令】不要放弃。分析失败原因（参数？格式？网络？权限？），自己修正后重试，"
                f"或改换更合适的工具/路径完成同一目标（剩余自愈次数 {remain}）。成功后再向用户汇报。")
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

from scanner import scan_universe, scan_symbols, market_movers, SECTORS
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
    from state import set_memory, get_memory, list_memory
except Exception:
    def set_memory(k, v): pass
    def get_memory(k, d=""): return d
    def list_memory(): return []

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


def memory_context() -> str:
    """把长期记忆注入 system prompt。

    - pref: 开头（或含「只做/不要/偏好」等约束字样）→ 提炼为【对用户的了解·请遵守】，作为行为规则；
    - 其余 → 归入【长期记忆·背景】。
    记忆越攒越多 → 模型自动按记忆调整风格与建议（记忆驱动的自我提升）。
    """
    mem = list_memory()
    if not mem:
        return ""
    prefs, facts = [], []
    for m in mem[:24]:
        v = (m.get("value") or "").strip()
        k = (m.get("key") or "")
        if not v:
            continue
        if k.startswith("pref:") or any(x in v for x in ("我只做", "不要", "别", "偏好", "只用", "习惯",
                                                          "不做", "不碰", "风险", "仓位", "止损", "止盈",
                                                          "现货", "合约", "默认", "喜欢", "每次")):
            prefs.append(v)
        else:
            facts.append(f"- {k}: {v}")
    blocks = []
    if prefs:
        uniq = list(dict.fromkeys(prefs))[:8]
        blocks.append("【对用户的了解（记忆驱动行为 · 请遵守）】\n" + "\n".join(f"- {p}" for p in uniq))
    if facts:
        blocks.append("【长期记忆·背景】\n" + "\n".join(facts[:12]))
    return "\n".join(blocks) + "\n" if blocks else ""


def _system_prompt() -> str:
    return (SOUL + "\n\n你是 Binance Agent OS 的中文交易助手(BAZZ Agent)。\n"
            "你拥有一组工具（function calling），用它们完成用户的真实请求。\n"
            "【强制规则 — 必须遵守】\n"
            "1) 涉及行情/异常 → 立即调用 scan_market；问某币价格 → market_quote；\n"
            "   风险/风控 → check_risk；买卖/多空 → propose_trade（仅出方案，下单需确认）；\n"
            "   支付/x402/402 → explain_x402；skills/技能 → list_skills；\n"
            "   链上/钱包/defi → onchain_ops；『记住…』→ memory_write；能力介绍 → get_help。\n"
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
            "   同一意图最多自动修复 2 轮。若工具结果里出现『自愈次数已用尽』的提示，"
            "立即停止调用工具，把失败原因、已尝试的方案与建议如实告诉用户，绝不编造成功结果。\n"
            "9) **深度思考（deep-thinking 协议）**：行情归因/是否交易/策略对比/风险判断等分析类请求，"
            "先在思考链里按『拆解→列假设→用工具核验→推理→自检→收敛』走一遍再回答：\n"
            "   - 至少列 2 个假设并用真实工具数据支持/推翻，不凭印象编数字；"
            "自检时反问『有无相反证据/是否把相关性当因果/有没有越过用户风险边界（查记忆）』；\n"
            "   - 支持思考链的模型把推理过程放进 reasoning 字段（前端会折叠展示），最终回答只放结论+依据+风险；\n"
            "   - 拿不准就直说『无法确定』，绝不硬编。简单查询（单个价格/是否存在）可跳过，直接答。\n"
            + memory_context())


def _detect(message: str) -> str:
    t = message.lower()
    # 发现技能（先于 记忆/技能/onchain 等关键词，避免被内部词抢先）
    if re.search(r"(发现|搜索|查找|找找|找一个|推荐|找一下|推荐一个).{0,12}(技能|skill|插件|能力)", message, re.I): return "discover"
    if re.search(r"(有没有|有什么|有什么好用的).{0,10}(技能|skill|插件)", message): return "discover"
    if re.search(r"记住|笔记|记录|记一下|存一下", message): return "memory_write"
    if re.search(r"你?记得|记忆|我之前|我的偏好|我告诉过你", message): return "memory_read"
    if any(k in t for k in ["并行", "全面扫描", "所有板块", "板块", "parallel"]): return "parallel"
    if any(k in t for k in ["定时", "cron", "日报", "schedule", "自动扫描"]): return "cron"
    if any(k in t for k in ["scan", "扫描", "数据", "分析", "异常", "看看",
                            "行情", "市场", "大盘", "涨跌", "涨幅", "异动", "波动", "走势"]): return "scan"
    if any(k in t for k in ["risk", "风险", "风控", "check"]): return "risk"
    if any(k in t for k in ["确认下单", "execute", "下单", "buy", "sell", "执行", "交易", "建仓",
                              "买入", "卖出", "市价", "限价", "做多", "做空", "开仓", "平仓",
                              "market", "limit", "long", "short", "order"]): return "execute"
    if any(k in t for k in ["report", "报告", "总结"]): return "report"
    if any(k in t for k in ["pay", "支付", "x402", "b402", "402"]): return "payment"
    if any(k in t for k in ["install", "安装", "add skill"]): return "install"
    if any(k in t for k in ["chain", "链上", "defi", "质押", "wallet", "钱包"]): return "onchain"
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


def _run_execute(confirm: bool = False, signal: dict = None, message: str = ""):
    if confirm and signal:
        res = confirm_and_place(signal, confirm=True)
        ok = "error" not in res
        tools = [{"icon": "📈", "name": "Binance 真实下单", "status": "success" if ok else "error",
                  "detail": res.get("orderId", res.get("error", ""))}]
        reply = ("✅ **已提交真实下单**\n\n"
                 f"- 订单号：`{res.get('orderId', 'N/A')}`\n- 标的：{res.get('symbol')}\n"
                 f"- 状态：{res.get('status', 'UNKNOWN')}\n\n请到 Binance 账户核对。") if ok else (
                 f"⚠️ 下单失败：{res.get('error')}\n\n请检查 API Key、Agentic 子账户权限与网络。")
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
    signal = {**best, "direction": direction, "price": price,
              "stop_loss": round(price * 0.97, 4), "take_profit": round(price * 1.08, 4),
              "quantity": str(round(50 / price, 6)), "max_loss_usdt": 5.0}
    reply = (f"已生成下单方案（**未真实下单，需你确认**）：\n\n"
             f"- 标的：**{signal['symbol']}** · {signal['direction']}\n- 入场：{price}\n"
             f"- 止损：{signal['stop_loss']} · 止盈：{signal['take_profit']}\n"
             f"- 数量：{signal['quantity']} · 最大亏损：{signal['max_loss_usdt']} USDT\n\n"
             "点击下方「确认下单」才会真实提交（需配置 Binance API Key 并开启 Agentic 子账户）。")
    if note:
        reply = note + reply
    return {"reply": reply, "needs_approval": True,
            "approval": {"action": "execute_order", "signal": signal,
                         "title": f"确认下单 {signal['symbol']}（{signal['direction']}）",
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
             "需 Binance 账号 + App 扫码登录（MPC 无密钥）。已装链上 Skills：`binance-agentic-wallet` 等。")
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


def auto_memorize(user_msg: str, reply: str, llm_cfg: dict = None) -> dict:
    """对话结束后调用：从「用户说了什么 + 我答了什么」提炼值得跨会话记住的信息。

    门控：
    - 用户消息含明显偏好信号词才触发（避免把闲聊也沉淀）；
    - 冷却时间内（_AUTO_MEM_COOLDOWN 秒）跳过；
    - 提炼失败/无可记内容静默返回，绝不影响主流程。
    返回 {"stored": int, "keys": [...], "skipped": str}。
    """
    try:
        from state import get_setting, set_setting, set_memory
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
    sysp = (
        "你是一个记忆提炼器。用户在与 BAZZ Agent（币安交易助手）对话中透露了偏好/习惯/规则/重要背景。"
        "从下面的『用户消息』与『助手回复』中，提炼 1-3 条值得长期记住的内容（跨会话有用）。\n"
        "规则：只记稳定的偏好/习惯/约束/重要背景，不记一次性行情数字；每条一句话 ≤50 字；避免重复。\n"
        "只输出 JSON：{\"items\": [\"…\", \"…\"]}，无可记内容时输出 {\"items\": []}。")
    prompt = f"用户消息：{um[:600]}\n\n助手回复：{(reply or '')[:600]}"
    try:
        out = llm.chat(sysp, prompt, temperature=0.1, max_tokens=300, llm_cfg=llm_cfg, task="summarize")
        if not out:
            return {"stored": 0, "keys": [], "skipped": "模型无输出"}
        items = json.loads(out)
        items = items.get("items", []) if isinstance(items, dict) else (items if isinstance(items, list) else [])
    except Exception:
        return {"stored": 0, "keys": [], "skipped": "提炼失败"}
    items = [str(x).strip()[:80] for x in items if str(x).strip() and len(str(x).strip()) >= 6][:3]
    if not items:
        return {"stored": 0, "keys": [], "skipped": "无值得记忆"}
    keys = []
    try:
        set_setting("auto_mem_last_ts", str(time.time()))
        for it in items:
            # 去重：已存在完全相同的 value 则不重复写
            dup = any(m.get("value") == it for m in list_memory())
            if dup:
                continue
            key = f"pref:{it[:20]}"
            set_memory(key, it)
            keys.append(key)
    except Exception:
        pass
    return {"stored": len(keys), "keys": keys, "skipped": ""}


def _run_memory_write(message: str):
    m = re.search(r"(?:记住|笔记|记录|记一下|存一下)[：:：]?\s*(.+)", message)
    text = m.group(1).strip() if m else message
    if not text:
        return {"reply": "你想让我记住什么？例如「记住：我只做现货，不做合约」。", "tools": [
            {"icon": "📓", "name": "记忆", "status": "warn", "detail": "空内容"}]}
    key = text[:24]
    set_memory(key, text)
    tools = [{"icon": "📓", "name": "写入长期记忆", "status": "success", "detail": key}]
    return {"reply": f"✅ 已记住：**{text}**（将用于后续所有会话）。", "tools": tools,
            "data": {"memory": list_memory()[:10]}}


def _run_memory_read():
    mem = list_memory()
    if not mem:
        return {"reply": "我目前还没有记住任何长期信息。试试「记住：你只做现货」。", "tools": [
            {"icon": "📓", "name": "读取记忆", "status": "info", "detail": "空"}]}
    lines = ["**我的长期记忆**：\n"]
    for m in mem[:12]:
        lines.append(f"- 📌 {m['key']}: {m['value']}")
    return {"reply": "\n".join(lines), "tools": [
        {"icon": "📓", "name": "读取记忆", "status": "success", "detail": f"{len(mem)} 条"}],
        "data": {"memory": mem}}


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
    if name == "memory_write":
        return _run_memory_write("记住：" + (args.get("text", "") or ""))
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


def _emit_llm_unavailable(message: str):
    """已接入 LLM 但模型调用失败时：给出明确提示，绝不回退规则引擎的固定话术。"""
    tip = ("⚠️ 已接入 LLM，但本次模型调用失败（请检查 API Key / 网络 / 模型名是否可用）。\n"
           "未接入 LLM 时才会自动使用内置回答；当前已接入，故不回退固定话术。")
    yield {"type": "text", "delta": tip}
    yield {"type": "done", "intent": "llm", "reply": tip,
           "tools": [{"icon": "⚠️", "name": "LLM 调用失败", "ok": False,
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
        "skills": "list_skills",
        "memory_write": "memory_write",
        "memory_read": "memory_write",
        "cron": "list_skills",
    }
    return m.get(intent)


def _build_system(persona: dict = None) -> str:
    base = _system_prompt()
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


def _tool_run_skill(args: dict, confirmed: bool = False) -> Dict[str, Any]:
    """run_skill：本地可执行类技能（baw / cli.mjs）需确认后经沙箱执行；纯指引类直接返回说明。"""
    from exec_sandbox import SandboxError, run_skill_cmd, skill_is_executable
    import skills_client
    name = (args.get("skill_name") or "").strip()
    arg_s = args.get("args") or ""
    installed = set(skills_client.list_installed())
    if name not in installed:
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
        return {"reply": f"⛔ 技能执行拦截：{e}", "tools": [
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
    return {"reply": f"{'✅' if ok else '⚠️'} 技能 **{name}** 退出码 {r['exit_code']}（{r['elapsed']}s）\n\n```\n{r['output'][:1200]}\n```",
            "intent": "tool", "data": r,
            "tools": [{"icon": "🧰", "name": f"技能 {name}", "status": "success" if ok else "warn", "detail": detail}]}


def _dispatch_is_approval_needed(name: str) -> bool:
    """这些工具执行路径可能流 needs_approval（下单类/装 skill/写文件/跑命令/执行技能）→ 不与其它工具并行。"""
    base = name.split(".")[0] if "." in name else name
    return base in ("propose_trade", "execute", "execute_order", "install_skill", "memory_write",
                    "write_file", "run_command", "run_skill")


# Hermes bots：persona 可声明 config.tools 工具子集白名单（留空/缺省 = 全部工具）
_TOOL_UNIVERSAL = {"get_help", "memory_write", "memory_read", "fetch_url", "gateway_status"}


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


def _summarize_old(hist_lines: list, llm_cfg: dict) -> str:
    """把早期历史压成一段「此前对话摘要」（用 summarize 槽位模型；失败则硬截断）。"""
    text = "\n".join(hist_lines)
    if len(text) <= _CTX_MAX_CHARS:
        return ""
    try:
        sysp = ("你是会话摘要器。把下面的对话要点压缩成 ≤260 字中文摘要：保留用户偏好、"
                "已查过的标的与关键结论、进行中的任务、未完成事项。只输出摘要正文。")
        summ = llm.chat(sysp, text, temperature=0.2, max_tokens=400, llm_cfg=llm_cfg, task="summarize")
        if summ and summ.strip():
            return "\n[此前对话摘要] " + summ.strip()[:600]
    except Exception:
        pass
    # 硬截断兜底：保留开头与结尾意图
    return ("\n[此前对话（截断）] " + text[:_CTX_MAX_CHARS // 2] + "\n……\n" + text[-800:])


def _history_blocks(history: list, llm_cfg: dict = None):
    """把 (role, text) 历史列表整理成注入用的 blocks 文本。

    - 只保留 user/assistant 两种角色文本（tool/系统噪音不带入）；
    - 数量上限 _CTX_MAX_HIST（超出的最旧部分先丢弃）；
    - 若剩余累计超长，最旧一半送入摘要器压缩成一段。
    """
    hist = [h for h in (history or []) if isinstance(h, dict) and h.get("role") in ("user", "assistant")]
    if not hist:
        return []
    hist = hist[:_CTX_MAX_HIST]
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
    if total <= _CTX_MAX_CHARS:
        return pairs
    # 超长：最旧一半尝试压缩为摘要块
    mid = max(1, len(pairs) // 2)
    old_lines = [f"{'用户' if r=='user' else '助手'}：{t}" for r, t in pairs[:mid]]
    summ = _summarize_old(old_lines, llm_cfg)
    keep = pairs[mid:]
    blocks = []
    if summ:
        blocks.append(("ctx", summ.strip()))
    blocks.extend(keep)
    return blocks


def _run_llm_agent(message: str, confirm: bool = False, signal: dict = None, approval: dict = None,
                   persona: dict = None, llm_cfg: dict = None, images: list = None,
                   auto_exec: bool = False, history: list = None):
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
    system = _build_system(persona)
    user_content, img_notes = _build_user_message(message, images, llm_cfg)
    if img_notes:
        yield {"type": "reasoning", "text": img_notes[0]}
    messages = [{"role": "system", "content": system}]
    # 记住上下文：历史块注入（早期超长部分压缩成摘要）
    ctx_blocks = _history_blocks(history, llm_cfg)
    for role, text in ctx_blocks:
        if role == "ctx":
            messages.append({"role": "system", "content": text})  # 摘要以 system 注入（不带对话轮次属性）
        else:
            messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": user_content})
    accumulated_tools = []
    model_used = ""
    forced_once = False  # _detect 兜底只在首轮强制一次，防多轮重复派发卡死
    tools = _persona_filter_tools(llm.TOOLS + llm.plugin_tools(), persona)
    turns = 0
    heal_left = _HEAL_MAX  # 工具失败自愈预算（同一请求内共享）
    ever_failed = False    # 出现过工具失败（用于轮次用尽时区分提示语）
    while turns < 6:  # 比默认多 2 轮：容纳「失败→自愈→再失败→最终回答」
        turns += 1
        try:
            resp = llm.chat_with_tools(messages, tools, llm_cfg=llm_cfg)
        except Exception:
            resp = None
        if not resp:
            # 模型调用失败：先尝试一次纯对话兜底（仍是 LLM 自己回答），
            # 仍失败才给出明确提示——绝不回退规则引擎的固定话术。
            try:
                plain = llm.chat(_build_system(persona), message, llm_cfg=llm_cfg)
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
            yield from _emit_llm_unavailable(message)
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
                    try:
                        jd = json.dumps(raw_data, ensure_ascii=False)
                        tmsg = f"[{forced} 返回的原始数据]\n{jd[:2200]}"
                    except Exception:
                        tmsg = (res.get("reply") or "")[:1600]
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

        if len(tool_calls) <= 1 or any(_dispatch_is_approval_needed(tc["name"]) for tc in tool_calls):
            results = [_run_one(tc) for tc in tool_calls]
        else:
            try:
                with ThreadPoolExecutor(max_workers=min(len(tool_calls), 4)) as pool:
                    results = list(pool.map(_run_one, tool_calls))
            except Exception:
                results = [_run_one(tc) for tc in tool_calls]
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
            # 工具结果回传给 LLM：优先给原始 data JSON（LLM 自己组织语言，避免照抄 reply 的成品模板）；
            # 无 data 或异常时才退回 reply 文本。
            summary = res.get("reply", "")
            raw_data = res.get("data")
            if raw_data not in (None, {}):
                try:
                    jd = json.dumps(raw_data, ensure_ascii=False)
                    tool_msg = f"[{tc['name']} 返回的原始数据]\n{jd[:2200]}"
                except Exception:
                    tool_msg = summary[:1600]
            else:
                tool_msg = summary[:1600]
            fail = _tool_fail_desc(res)
            if fail:
                ever_failed = True
                if heal_left > 0:
                    heal_left -= 1
                tool_msg += _heal_hint(fail, heal_left)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": tool_msg})
    # 超过轮次保护：明确提示，不走规则引擎固定话术
    if ever_failed:
        tip = ("我已经连续尝试多轮仍未成功（工具持续报错或自愈次数已用尽）。\n"
               "请看上面的错误卡片：通常是网络 / API Key / 参数问题。检查后重试，"
               "或换一种问法，让我换条路再试。")
        yield {"type": "text", "delta": tip}
        yield {"type": "done", "intent": "llm", "reply": tip, "model": model_used,
               "tools": [{"icon": "⚠️", "name": "多次尝试失败", "ok": False, "detail": "已达最大轮次"}]}
        return
    yield from _emit_llm_unavailable(message)


def dispatch(message: str, confirm: bool = False, signal: dict = None,
             approval: dict = None, auto_exec: bool = False) -> Dict[str, Any]:
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
        narr = llm.chat(_system_prompt(), message)
        if narr:
            return {"intent": "chat", "reply": narr,
                    "tools": [{"icon": "💬", "name": "LLM 对话", "status": "success",
                               "detail": llm.get_llm_config().get("model")}]}
    return {"intent": "chat",
            "reply": "我是 BAZZ Agent，已接入 Binance Agent OS。输入「帮助」查看全部能力，或试试「扫描 / 并行扫描 / 定时扫描」。",
            "tools": [{"icon": "💬", "name": "对话", "status": "info", "detail": "规则引擎"}]}


def handle(message: str) -> dict:
    return dispatch(message)


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
               history: list = None) -> Iterator[Dict[str, Any]]:
    """产出 NDJSON 事件流。persona 可选：来自 Bots 页的 Agent 档案（身份/口吻）。
    llm_cfg 可选：会话级 provider 快照（防漂移）。images 可选：用户附图 dataURL 列表。
    auto_exec：全能模式（首页默认），沙箱工具跳过审批直接执行；交易类仍需确认。
    history：同会话前序消息（记住上下文）。"""
    # 审批回调（用户确认了某个待审批动作）始终走规则引擎精确处理
    if approval:
        yield from _emit_dispatch(dispatch(message, confirm=confirm, signal=signal, approval=approval, auto_exec=auto_exec))
        return

    # 真 LLM 已配置 → 走 function-calling agent 循环（Hermes 风格）
    if llm.is_configured(llm_cfg):
        yield from _run_llm_agent(message, confirm=confirm, signal=signal, approval=approval,
                                  persona=persona, llm_cfg=llm_cfg, images=images,
                                  auto_exec=auto_exec, history=history)
        return

    # 未配置 LLM → 规则引擎
    msg_for_rule, _notes = _build_user_message(message, images, None)
    msg_for_rule = msg_for_rule if isinstance(msg_for_rule, str) else message
    out = dispatch(msg_for_rule, confirm=confirm, signal=signal, approval=approval, auto_exec=auto_exec)
    yield from _emit_dispatch(out)


if __name__ == "__main__":
    for ev in run_stream("并行扫描"):
        print(ev["type"], ev.get("delta") or ev.get("tool", {}).get("name") or ev.get("intent"))
