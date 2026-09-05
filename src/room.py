"""群聊房间引擎（对齐 Hermes Bot Mode 的房间模型）

模型：
- 一个房间 = 一条 kind='room' 的会话（共享有序房间日志），成员名单存 members_json。
- 每个成员各自拥有 kind='group' 的独立会话（title='Group: <房间>'，persona=成员名），
  里面只以该成员的视角记录：自己的发言=assistant；用户提问=user；其它成员的发言=user 但文本
  带 "[成员名]: " 前缀 —— 保证各 bot 的记录互不混写。
- 每轮谁说话由 @ 解析决定（@成员/@everyone，默认全员）；各成员串行（never parallel）；
  回复 "(pass)" 或空/失败 = 沉默；某轮全员沉默 → 会话收敛结束；最多 GROUP_MAX_ROUNDS 轮。
"""
import json
import re
import time

import state
import llm
from agent_core import _build_system

GROUP_MAX_ROUNDS = 3
GROUP_MAX_SPOKES = 10
HANDOFF_MAX = 4          # 单次请求内允许的 bot→bot 转交次数（防无限互踢皮球）
MAX_TEXT = 1600

_PASS_RE = re.compile(r"^\(?\s*pass\s*\)?\.?\s*$", re.I)
_PASS_ZH_RE = re.compile(r"^(\(|（)?\s*(跳过|过|无话可说|沉默)\s*(\)|）)?\.?\s*$")
_ALL_RE = re.compile(r"@(everyone|all|\u5168\u4f53)", re.I)
_MENTION_RE = re.compile(r"@([^\s@,，。.!?！？、]+)")


def parse_responders(text: str, members: list, agents_by_name: dict) -> list:
    """Hermes 确定性 @ 解析：@everyone/@all/无 @成员 → 全员；@成员 → 仅被 @ 者。
    members/agents_by_name 的 key 均为成员名。
    """
    if _ALL_RE.search(text or ""):
        return list(members)
    mentioned = []
    for raw in _MENTION_RE.findall(text or ""):
        want = raw.strip().lstrip("\"“").rstrip("\"”").lower()
        for m in members:
            if want == m.lower():
                mentioned.append(m)
                break
            ag = agents_by_name.get(m) or {}
            if want and (want == (ag.get("title") or "").lower() or want == m.lower().replace(" ", "")):
                if m not in mentioned:
                    mentioned.append(m)
                    break
    if mentioned:
        return mentioned
    return list(members)


_CLAUSE_LAST2 = ("另外", "同时", "然后", "麻烦", "请你", "请帮", "顺便", "以及", "还有")
_CLAUSE_PUNC = " \t\n\r,，;；:：。！？!?、"
_TASK_VERBS = ("帮我", "帮忙", "把", "帮", "查", "看", "算", "分析", "计算", "评估", "检查", "检测",
               "执行", "运行", "跑", "试试", "试", "读", "写", "读取", "写入", "生成", "创建",
               "整理", "汇总", "总结", "对比", "解读", "通知", "转告", "回复", "告诉我", "给我",
               "请", "麻烦", "确认", "验证", "回测", "扫描", "看看", "评估下", "看下", "查下",
               "算下", "写下", "读下", "总结下", "翻译", "计算下")


def _is_task_payload(text: str, at_idx: int, name_len: int, seg: str) -> bool:
    """判断 @成员 之后的内容是否为「转交任务」，而不是单纯观点/打招呼：
    1) 位置在句首或分句边界（前为标点/空格/另外/同时…）；
    2) 内容以祈使动词开头（帮我/查/分析/执行…）。
    两者都满足才算任务转交。"""
    seg0 = (seg or "").lstrip()
    if len(seg0) < 5:
        return False
    prefix = (text or "")[:at_idx].rstrip()
    boundary = (not prefix) or (prefix[-1] in _CLAUSE_PUNC) or (prefix[-2:] in _CLAUSE_LAST2)
    if not boundary:
        return False
    head = seg0[:6]
    return any(head.startswith(v) or seg0.startswith(v) for v in _TASK_VERBS)


def find_handoffs(text: str, members: list, agents_by_name: dict) -> list:
    """Bot 回复里的「任务转交」扫描：@成员 + 句首/分句边界 + 祈使任务 → (target, payload)。
    纯 @观点/打招呼 不构成转交（目标成员会通过投影在后续轮自然回复）。
    """
    text = text or ""
    out = []
    for raw in _MENTION_RE.findall(text):
        want = raw.strip().lstrip("\"“").rstrip("\"”").lower()
        tgt = None
        for m in members:
            ag = agents_by_name.get(m) or {}
            if want == m.lower() or want == m.lower().replace(" ", "") or \
               want == (ag.get("title") or "").lower():
                tgt = m
                break
        if not tgt:
            continue
        idx = text.find("@" + raw)
        if idx == -1:
            continue
        seg = text[idx + 1 + len(raw):]
        nxt = seg.find("@")
        if nxt != -1:
            seg = seg[:nxt]
        seg = seg.strip(" \t\n\r:：,，。.!！?？\"“”'‘’")
        if _is_task_payload(text, idx, len(raw), seg):
            out.append((tgt, seg))
    return out


def _is_pass(text) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if _PASS_RE.match(t) or _PASS_ZH_RE.match(t):
        return True
    return False


def run_room(conv_id: str, message: str, llm_cfg: dict = None):
    """生成房间轮次事件流（NDJSON dict 生成器），并完成全部落库。"""
    conv = state.get_conversation(conv_id)
    if not conv or conv.get("kind") != "room":
        yield {"type": "error", "detail": "该会话不是群聊房间"}
        return
    members = [m for m in conv.get("members") or [] if isinstance(m, str)]
    room_title = conv.get("title") or "房间"
    agents_by_name = {}
    agent_list = []
    for m in members:
        ag = state.get_agent_by_name(m)
        if ag:
            agents_by_name[m] = ag
            agent_list.append(ag)

    if len(agent_list) < 2:
        yield {"type": "error", "detail": "房间至少需要 2 个 Agent 成员"}
        return

    # 用户消息写入房间日志 + 每个成员的独立会话（Hermes：每成员看到自己视角的日志）
    state.add_message(conv_id, "user", message)
    member_conv = {}
    for ag in agent_list:
        cid = state.ensure_group_member_session(ag["name"], room_title)
        member_conv[ag["name"]] = cid
        state.add_message(cid, "user", message)
        state.touch_conversation(cid)
    state.touch_conversation(conv_id)

    if not llm.is_configured(llm_cfg):
        yield {"type": "text", "delta": "（未配置 LLM：群聊需要接入模型后各 Agent 才能发言。可在 Settings 配置。）"}
        yield {"type": "done", "conversation_id": conv_id, "intent": "room"}
        return

    yield {"type": "room", "room": room_title, "members": members, "rounds": GROUP_MAX_ROUNDS}

    from collections import deque

    names_all = [ag["name"] for ag in agent_list]
    system_base = (
        f"\n\n你正参与群聊房间「{room_title}」，成员：{', '.join('@' + n for n in names_all)}。"
        f"\n规则：像真人在群里自然发言（一般 2-4 句，可反驳/补充他人）；"
        f"观点已被说完就只回复 (pass)。不要假装自己是别的成员。"
        f"\n跨成员协作：需要别人帮忙/数据/接力时，直接 @对方成员 并写明要它做的事（例如『@流动性猎手 帮我查下费率』）；"
        f"收到「@你」+ 明确任务的转交时，优先处理并回复，不要推回给转交人；"
        f"确实超出职责时才再 @合适成员转交（最多一次，避免互踢皮球）。")

    queue = deque()
    spoke_total = 0
    handoff_total = 0
    rounds_used = 0
    last_round_spoke = 0

    def seed_round(rnd: int):
        if rnd == 1:
            for n in parse_responders(message, names_all, agents_by_name):
                queue.append((n, None))
        else:
            for n in names_all:
                queue.append((n, None))

    seed_round(1)
    rounds_used = 1
    current_round = 1

    while queue and spoke_total < GROUP_MAX_SPOKES:
        name, handoff = queue.popleft()
        ag = agents_by_name.get(name)
        if not ag:
            continue
        persona = {"name": ag["name"], "title": ag["title"], "description": ag["description"],
                   "avatar": ag["avatar"], "color": ag["color"], "config": ag.get("config") or {}}
        system = _build_system(persona) + system_base

        hist = state.get_messages(member_conv[name])  # 该成员视角：assistant=自己，user=用户/他人(带前缀)
        lines = []
        for h in hist[-20:]:
            c = (h["content"] or "").strip()
            if not c:
                continue
            if h["role"] == "assistant":
                lines.append(f"你：{c}")
            else:
                lines.append(c)  # 用户原文 / "[成员名]: 发言" / "[成员名 转交] ..."（已带说话人前缀）
        prompt = "（群聊房间的最近记录，按先后顺序；[名字]: 表示他人发言）\n" + "\n".join(lines) + \
                 "\n\n现在轮到你发言。你的身份与风格见 system。直接输出你要说的话（2-4 句，自然口语，可回应上面观点）；" \
                 "如果没有新观点或该说的已被说完，只输出 (pass)。"
        if handoff:
            src, payload = handoff
            prompt += (f"\n\n【转交任务】@{src} 转交给你：{payload}。"
                       f"请把它当作必须处理的任务/请求，直接回答或给出结论；除非确实不归你管，"
                       f"否则不要把它原样退回给 @{src}。")
        resp = None
        try:
            resp = llm.chat(system, prompt, temperature=0.7, max_tokens=520, llm_cfg=llm_cfg)
        except Exception:
            resp = None
        text = (resp or "").strip() if isinstance(resp, str) else ""

        if resp is None or _is_pass(text):
            reason = ""
            if resp is None:
                err = getattr(llm, "_last_error", None)
                reason = (str(err).strip() if err else "") or "LLM 上游无响应（可能 base_url/Key/网络问题）"
            yield {"type": "round", "room": room_title, "round": current_round, "name": ag["name"],
                   "status": "pass", "reason": reason or "沉默 (pass)"}
            # 转交的接收者沉默 → 不重试，直接继续（防卡死）
            if not queue:
                if last_round_spoke == 0:
                    break
                current_round += 1
                rounds_used = current_round
                if current_round > GROUP_MAX_ROUNDS:
                    break
                last_round_spoke = 0
                seed_round(current_round)
            continue

        text = text[:MAX_TEXT]
        spoke_total += 1
        last_round_spoke += 1
        # 自己的发言：进自己的会话(assistant) + 房间日志(persona=自己)；并投影到其它成员会话(user+前缀)
        state.add_message(member_conv[name], "assistant", text,
                          data={"persona": ag["name"], "intent": "room"})
        state.add_message(conv_id, "assistant", text,
                          data={"persona": ag["name"], "intent": "room"})
        for other in member_conv:
            if other != name:
                state.add_message(member_conv[other], "user", f"[{ag['name']}]: {text}")
        state.touch_conversation(member_conv[name])
        state.touch_conversation(conv_id)
        yield {"type": "bot", "name": ag["name"], "avatar": ag["avatar"],
               "color": ag["color"], "text": text, "round": current_round}

        # 发言里对其它成员的任务转交 → 目标成员插队下一条作答（Hermes bot handoff）
        for tgt, payload in find_handoffs(text, names_all, agents_by_name):
            if tgt == name or handoff_total >= HANDOFF_MAX:
                continue
            handoff_total += 1
            state.add_message(member_conv[tgt], "user", f"[{ag['name']} 转交] @{tgt} {payload}")
            state.touch_conversation(member_conv[tgt])
            yield {"type": "transfer", "room": room_title, "from": ag["name"], "to": tgt,
                   "text": payload, "round": current_round}
            queue.appendleft((tgt, (ag["name"], payload)))

        if not queue:
            if last_round_spoke == 0:
                break
            current_round += 1
            rounds_used = current_round
            if current_round > GROUP_MAX_ROUNDS:
                break
            last_round_spoke = 0
            seed_round(current_round)

    if spoke_total == 0:
        err = getattr(llm, "_last_error", None)
        reason = (str(err).strip() if err else "") or "LLM 已配置但全员沉默，请检查 base_url/Key/网络"
        yield {"type": "warn", "level": "error",
               "detail": f"房间 0/{len(agent_list)} 个成员发言：{reason}"}

    yield {"type": "done", "conversation_id": conv_id, "intent": "room",
           "rounds": rounds_used, "spoke_total": spoke_total, "handoff_total": handoff_total}
