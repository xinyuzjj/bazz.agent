# -*- coding: utf-8 -*-
"""v1.7.4 护栏：agent 体验层（借鉴 pi / Hermes Agent 的机制）。

按清单分批落地，每批对应一项，断言跟着加：
  4) 响应体字节上限 —— 错误路径别把巨型 body 全量读进内存
运行：.venv/Scripts/python.exe tests/test_v174_agent_ux.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

import net_guard  # noqa: E402
import error_digest  # noqa: E402
import recall  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({extra})" if (extra and not cond) else ""))
    if not cond:
        FAILS.append(name)


def _src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class _FakeResp:
    """最小 requests.Response 替身：只带 bounded_text 关心的三个属性。"""

    def __init__(self, body: bytes, encoding: str = "utf-8", consumed: bool = True):
        self.content = body
        self.encoding = encoding
        self._content_consumed = consumed


# ---------------- ④ 响应体字节上限 ----------------

def test_bounded_text_truncates_big_body():
    big = b"x" * (net_guard.MAX_BODY_BYTES + 5000)
    out = net_guard.bounded_text(_FakeResp(big))
    check("④ 超大响应体被截断到上限附近", len(out) <= net_guard.MAX_BODY_BYTES + 40, str(len(out)))
    check("④ 截断时带「已截断」标记", "已截断" in out)


def test_bounded_text_keeps_small_body_intact():
    small = "币安返回的小错误体".encode("utf-8")
    out = net_guard.bounded_text(_FakeResp(small))
    check("④ 小响应体原样返回", out == "币安返回的小错误体", out)
    check("④ 小响应体不加截断标记", "已截断" not in out)


def test_bounded_text_never_raises():
    """诊断路径上二次抛错最伤 —— 传垃圾也必须安静返回。"""
    check("④ None 不抛", net_guard.bounded_text(None) == "")
    check("④ 空对象不抛", net_guard.bounded_text(object()) == "")

    class _Boom:
        @property
        def content(self):
            raise RuntimeError("boom")

        @property
        def encoding(self):
            raise RuntimeError("boom")

    check("④ 取属性就炸也不抛", net_guard.bounded_text(_Boom()) == "")


def test_bounded_text_survives_bad_encoding():
    r = _FakeResp("中文".encode("utf-8"), encoding="not-a-real-codec")
    out = net_guard.bounded_text(r)
    check("④ 编码名非法时回退 utf-8 而不是抛", "中文" in out, out)


def test_snippet_is_one_line_and_bounded():
    body = ("第一行\n第二行\n" + "很长" * 400).encode("utf-8")
    out = net_guard.snippet(_FakeResp(body))
    check("④ snippet 压掉换行", "\n" not in out)
    check("④ snippet 限长", len(out) <= net_guard.MAX_SNIPPET_CHARS, str(len(out)))


def test_no_raw_text_slice_in_error_paths():
    """源码守卫：错误路径不得再出现裸 `.text[:200]`（那会先全量读入再截断）。"""
    for rel in ("src/llm.py", "src/cex_wallet.py", "src/executor.py"):
        src = _src(rel)
        check(f"④ {rel} 不再裸读 .text[:200]", ".text[:200]" not in src)
    check("④ llm.py 改用了 net_guard", "net_guard" in _src("src/llm.py"))
    check("④ cex_wallet.py 改用了 net_guard", "net_guard" in _src("src/cex_wallet.py"))
    check("④ executor.py 改用了 net_guard", "net_guard" in _src("src/executor.py"))


# ---------------- ② 错误 → 人话 + 行动建议 + 脱敏 ----------------

def test_error_digest_classifies():
    d = error_digest.digest
    cases = [
        (Exception("HTTPSConnectionPool: Max retries exceeded ... schannel"), "network"),
        (Exception("Read timed out. (read timeout=10)"), "timeout"),
        (Exception('HTTP 429 {"code":4029,"message":"too many requests"}'), "ratelimit"),
        (Exception("HTTP 401 Unauthorized: invalid api key"), "auth"),
        (Exception("本地后端不可达: fetch failed"), "localbackend"),
        (Exception("某段谁也没见过的报错"), "unknown"),
    ]
    for exc, want in cases:
        got = d(exc)["kind"]
        check(f"② 分类 {want}", got == want, f"got={got}")


def test_sandbox_not_confused_with_network():
    """**最关键的一条**：沙盒规则拦下 ≠ 网络不通。

    混在一起会把人引去折腾代理池 —— 这正是改造前真实发生过的误诊。
    """
    a = error_digest.digest(Exception("命令不在白名单（沙盒）"))["kind"]
    b = error_digest.digest(Exception("Max retries exceeded with url: /x"))["kind"]
    check("② 沙盒类不被判成网络", a == "sandbox", a)
    check("② 网络类不被判成沙盒", b == "network", b)


def test_error_digest_walks_exception_chain():
    """分类必须沿 __cause__ 回溯 —— 包装过的异常里，真因常在链尾。"""
    inner = Exception("Max retries exceeded")
    outer = RuntimeError("技能执行失败")
    outer.__cause__ = inner
    check("② 沿异常链找到真因", error_digest.digest(outer)["kind"] == "network")


def test_redact_masks_credentials():
    s = error_digest.redact(
        "api_key=sk-abcdefgh12345678 Authorization: Bearer abcdefghijklmnop "
        + "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6")
    check("② sk- 被掩", "sk-abcdefgh" not in s, s)
    check("② Bearer 被掩", "abcdefghijklmnop" not in s, s)
    check("② 长 hex 被掩", "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6" not in s, s)
    check("② 脱敏不抛（传 None）", error_digest.redact(None) == "")


def test_humanize_carries_action():
    out = error_digest.humanize(Exception("Read timed out"))
    check("② 人话里带「该怎么办」", "→" in out, out)
    check("② 人话里有可读结论", len(out) > 20, out)
    check("② humanize 遇垃圾不抛", isinstance(error_digest.humanize(None), str))


def test_agent_core_error_sites_upgraded():
    src = _src("src/agent_core.py")
    check("② agent_core 已接入 error_digest", "error_digest" in src)
    check("② 不再拼「异常名 + 截断原文」的裸报错",
          "{type(e).__name__}: {str(e)[:200]}" not in src)
    check("② 沙箱拦截走统一人话", 'f"⛔ 沙箱拦截：{e}"' not in src)


# ---------------- ⑤ 工具注册表 ----------------

def test_registry_matches_llm_schemas():
    """注册表与 `llm.py` 的 schema 必须**双向一致**。

    改造前这两份清单各写各的：加了工具只改一边，模型就看不到（或调不动）。
    这条断言就是那道对齐闸门。
    """
    import agent_core  # noqa: F401  导入即触发注册
    import llm
    import tool_registry

    names_r = set(tool_registry.REGISTRY.names())
    names_s = {t["function"]["name"] for t in llm.TOOLS}
    check("⑤ 注册表工具数 ≥ 24（脚本没失效）", len(names_r) >= 24, str(len(names_r)))
    check("⑤ 注册表有 / schema 没有 → 必须为空", not (names_r - names_s), str(sorted(names_r - names_s)))
    check("⑤ schema 有 / 注册表没有 → 必须为空", not (names_s - names_r), str(sorted(names_s - names_r)))


def test_dispatch_no_longer_if_chain():
    src = _src("src/agent_core.py")
    check("⑤ 旧 if name == \"scan_market\" 链已移除", 'if name == "scan_market"' not in src)
    check("⑤ dispatch 改为查注册表", "tool_registry.REGISTRY.get(name)" in src)
    check("⑤ 注册块存在且被调用", "_register_tools()" in src)


def test_approval_list_has_single_source():
    """审批清单**不能两处各写一份** —— 注册表的 approval 标记与外层屏障必须逐项一致。

    不一致的后果很实际：注册表说不用确认、屏障说用（或反过来），
    要么白弹确认框，要么把该拦的操作放过去。
    """
    import agent_core
    import tool_registry

    reg = set(tool_registry.REGISTRY.approvals())
    outer = {n for n in tool_registry.REGISTRY.names()
             if agent_core._dispatch_is_approval_needed(n)}
    check("⑤ 注册表审批集 == 外层屏障（同名单）", reg == outer,
          f"reg={sorted(reg)} outer={sorted(outer)}")


def test_every_registered_tool_is_callable():
    import agent_core  # noqa: F401
    import tool_registry

    bad = [r["name"] for r in tool_registry.REGISTRY.rows() if not callable(r["handler"])]
    check("⑤ 每个注册项都有可调用的 handler", not bad, str(bad))
    check("⑤ 分组与图标都声明了",
          all(r["toolset"] for r in tool_registry.REGISTRY.rows()), "有工具缺 toolset")


def test_alignment_guard_is_real():
    """护栏自检：**人为制造差集**，确认上面那条对齐断言真的会红。

    否则它就只是一条永远绿的假护栏 —— 项目里踩过这个坑（v1.7.1 变异验证抓出 5 组假护栏）。
    """
    import llm
    import tool_registry

    names_r = set(tool_registry.REGISTRY.names())
    names_s = {t["function"]["name"] for t in llm.TOOLS}
    check("⑤ 自检：凭空多一个工具 → 差集必须非空", bool((names_r | {"__ghost_tool__"}) - names_s))
    check("⑤ 自检：凭空少一个工具 → 差集必须非空", bool(names_s - (names_r - {"meme_watch"})))


# ---------------- ③ 工具用法进 schema ----------------

def test_tool_usage_moved_to_schema():
    """逐工具用法必须在各工具的 `schema.description` 里，**不能在系统提示词里再抄一遍**。

    抄两遍的下场：改一处忘一处，模型看到的两份说明就对不上了。
    只存在于提示词的 7 个技能是最先要搬的 —— 不搬就删不掉提示词里那张路由表。
    """
    ac = _src("src/agent_core.py")
    llm_src = _src("src/llm.py")
    for skill in ("news-sentiment", "portfolio-review", "query-token-audit",
                  "query-address-info", "binance-tokenized-securities-info",
                  "binance-trading-signal", "binance-sports-ai-analyzer"):
        check(f"③ {skill} 已进 run_skill 的 schema", skill in llm_src)
        check(f"③ 提示词不再逐条列 {skill}",
              f"run_skill('{skill}'" not in ac and f"run_skill({skill}" not in ac)
    check("③ 发文形态规则在 schema 里", "发文形态" in llm_src)
    check("③ 币种识别（严禁猜交易对）在 schema 里", "严禁猜交易对" in llm_src)
    check("③ square-monster-post 用法在 schema 里", "square-monster-post" in llm_src)


# ---------------- ① 提示词分模块 ----------------

def test_prompt_assembled_from_blocks():
    import agent_core

    for blk in ("_IDENTITY", "_TOOL_DOCTRINE", "_BEHAVIOR_RULES"):
        v = getattr(agent_core, blk, None)
        check(f"① 提示词分块存在且非空：{blk}", isinstance(v, str) and len(v) > 40)
    s = agent_core._system_prompt("zh")
    check("① 组装结果含身份与两类纪律",
          all(x in s for x in ("BAZZ Agent", "工具纪律", "行为纪律")))
    check("① 提示词已瘦身（< 4000 字）", len(s) < 4000, str(len(s)))


def test_cross_tool_doctrine_survived():
    """跨工具纪律**搬不走**（不属于任何单个工具），重构后必须还在。"""
    import agent_core

    s = agent_core._system_prompt("zh")
    for k in ("绝对禁止用 mcp_call 查行情",
              "禁止用 run_command 跑 python/node 直连币安 API",
              "发币安广场只走 run_skill",
              "schedule_task 真实注册",
              "绝不可互相替代",
              "deep-thinking",
              "客套开场白"):
        check(f"① 跨工具纪律仍在：{k[:16]}…", k in s)


# ---------------- ⑥ 跨会话召回 ----------------

def test_recall_probe_terms():
    t = recall.probe_terms("帮我看下 WLDUSDT 之前那个分析")
    check("⑥ 抽出币种符号", "WLDUSDT" in t, str(t))
    check("⑥ 过滤中文语境词（那个/一下）", "那个" not in t and "一下" not in t, str(t))
    check("⑥ 过滤英文停用词", "the" not in [x.lower() for x in recall.probe_terms("the BTC price")])
    check("⑥ 空输入返回空表", recall.probe_terms("") == [])
    check("⑥ 抽词遇垃圾不抛", isinstance(recall.probe_terms(None), list))


def test_recall_block_is_labelled():
    """注入块**必须自带「这是历史召回」的标注**。

    少了这句，模型会把召回内容当成当前对话的一部分，说出
    「你刚才说的是…」这种张冠李戴的话。
    """
    hits = [{"cid": "c1", "title": "妖币雷达", "snippet": "WLDUSDT 在已拉升阶段",
             "score": 5, "ts": 1}]
    b = recall.format_block(hits)
    check("⑥ 标注这是历史召回", "历史会话召回" in b and "不是本轮" in b, b[:60])
    check("⑥ 带防编造提示", "编造" in b)
    check("⑥ 无命中返回空串", recall.format_block([]) == "")
    check("⑥ 片段全空时不硬凑空块", recall.format_block([{"title": "x", "snippet": ""}]) == "")


def test_recall_never_raises():
    check("⑥ 空查询返回空串", recall.build("") == "")
    check("⑥ 垃圾输入不抛", isinstance(recall.build(None), str))


def test_recall_wired_into_agent_loop():
    src = _src("src/agent_core.py")
    check("⑥ 已接入每轮自动召回", "recall.build(message" in src)
    check("⑥ 召回排除当前会话", "exclude_cid=cid" in src)
    check("⑥ 有开关（recall_enabled）", "recall_enabled" in src)


# ---------------- ⑦ 流式工具输出 ----------------

def test_tool_execution_streams():
    """工具必须**边完成边回传**，不能阻塞到整批跑完（改前用 pool.map 就是这个毛病）。"""
    ac = _src("src/agent_core.py")
    check("⑦ 工具执行不再用阻塞的 pool.map", "pool.map(_run_one" not in ac)
    check("⑦ 改用 as_completed 边完成边回传", "as_completed(futs)" in ac)
    check("⑦ 发出 tool_progress 事件（运行中提示）", '"type": "tool_progress"' in ac)
    check("⑦ 结果事件在执行阶段发（不是事后统一发）", "slots[idx] = pair" in ac)
    check("⑦ 审批类工具也推运行中", 'phase": "start", "names": [tc["name"]]' in ac)


def test_no_duplicate_tool_events():
    """工具结果**不能发两次** —— 重复发的后果不只是卡片翻倍，
    还让前端「按索引与 done.tools 合并」整条链错位。"""
    ac = _src("src/agent_core.py")
    # 旧写法：事后循环里再统一发一遍
    check("⑦ 事后循环不再重复发 tool 事件",
          "for t in res.get(\"tools\", []):\n                accumulated_tools.append" not in ac)
    check("⑦ 注释写明了为什么不能重复发", "索引对齐" in ac or "按索引" in ac)


def test_frontend_handles_tool_progress():
    fe = _src("frontend/src/views/ChatView.tsx")
    check("⑦ 前端处理 tool_progress", 'ev.type === "tool_progress"' in fe)
    check("⑦ 运行中列表是独立字段（不混进 tools）", "runningTools" in fe)
    check("⑦ 有渲染「正在执行」", "chat.toolExecuting" in fe)
    loc = _src("frontend/src/i18n/locales.ts")
    check("⑦ i18n 键 zh/en 各一处", loc.count('"chat.toolExecuting"') == 2,
          str(loc.count('"chat.toolExecuting"')))


# ---------------- ⑧ 会话分支 ----------------

def test_fork_backend():
    st = _src("src/state.py")
    check("⑧ 有 fork_conversation", "def fork_conversation(" in st)
    check("⑧ 建表带 parent_id", "parent_id TEXT DEFAULT ''" in st)
    check("⑧ 老库迁移补列（parent_id）", 'ADD COLUMN parent_id' in st)
    check("⑧ 老库迁移补列（fork_from）", 'ADD COLUMN fork_from' in st)
    # 三处会话查询都要带出血统，漏一处就会出现「有的接口有、有的没有」
    check("⑧ 会话查询都带出了血统字段",
          st.count("parent_id,fork_from") >= 3, str(st.count("parent_id,fork_from")))
    app = _src("desktop_app.py")
    check("⑧ 有 fork 路由", '"/api/conversations/{cid}/fork"' in app)


def test_fork_frontend():
    api = _src("frontend/src/api.ts")
    fe = _src("frontend/src/views/ChatView.tsx")
    check("⑧ api.ts 有 forkConversation", "forkConversation:" in api)
    check("⑧ 聊天页有分叉入口", "forkHere(" in fe and "⑂" in fe)
    check("⑧ 分叉后切到新会话", "setConvId(r.id)" in fe)
    loc = _src("frontend/src/i18n/locales.ts")
    for k in ("chat.forkHere", "chat.forked", "chat.forkedHint"):
        check(f"⑧ i18n 键 {k} zh/en 各一处", loc.count(f'"{k}"') == 2, str(loc.count(f'"{k}"')))


# ---------------- ⑨ 技能自建 / 自改进 ----------------

def test_skill_learning_is_safe_by_default():
    """这条的关键不是「能不能生成技能」，而是**边界**：默认关 + 草案不直接生效。

    自动创建技能 = 悄悄改变 agent 的能力边界；交易场景下必须先让用户知道。
    """
    sl = _src("src/skill_learning.py")
    check("⑨ 默认关闭（缺省 0）", '"skill_learning_enabled", "0"' in sl)
    check("⑨ 草案落在 _drafts（不写正式目录）", '_drafts' in sl)
    check("⑨ 有显式采纳动作", "def adopt_draft(" in sl)
    check("⑨ 有丢弃动作", "def discard_draft(" in sl)
    check("⑨ 技能名校验（防路径穿越）", "_NAME_RE" in sl and ".." not in sl.split("_NAME_RE")[0][-200:])
    check("⑨ 同名技能拒绝覆盖", "同名技能已存在" in sl)
    check("⑨ 后台线程跑，不给主对话加延迟", "threading.Thread" in sl and "daemon=True" in sl)


def test_skill_learning_wired():
    ac = _src("src/agent_core.py")
    check("⑨ agent 收尾时触发 review", "skill_learning.maybe_review_async(history" in ac)
    check("⑨ 触发被 try 包住（失败不影响对话）",
          "skill_learning.maybe_review_async" in ac and "except Exception" in ac)
    app = _src("desktop_app.py")
    check("⑨ 有草案列表路由", '"/api/skill-drafts"' in app)
    check("⑨ 有采纳路由", "/adopt" in app and "adopt_skill_draft" in app)
    check("⑨ 有丢弃路由", "/discard" in app)


# ---------------- ⑩ 中断改向 ----------------

def test_interrupt_redirect():
    """中断改向：打断后**已产出的内容要落库并带上标记**。

    只落库不标记，模型下一轮看不出「上次是讲到一半被喊停」，会把半截内容
    当成完整结论接着往下讲 —— 这是比「内容丢失」更隐蔽的一种错。
    """
    app = _src("desktop_app.py")
    check("⑩ _persist 支持 interrupted 参数", "def _persist(interrupted: bool = False)" in app)
    check("⑩ 断连时按「被打断」落库", "_persist(interrupted=True)" in app)
    check("⑩ 标记写进正文（模型看得到）", "用户中断" in app)
    check("⑩ 保留 v1.3.6 的断连兜底落库", "except GeneratorExit:" in app)
    check("⑩ 不再有重复的 /note 路由（自动落库已覆盖）", "/note" not in app)

    fe = _src("frontend/src/views/ChatView.tsx")
    check("⑩ 生成中发新消息不再被直接挡回",
          "if (streaming || uploading) return;" not in fe)
    check("⑩ 生成中发消息会先中断", "if (streaming) {" in fe and "改向" in fe)
    check("⑩ Stop 仍然可用", "abortRef.current?.abort()" in fe)


# ---------------- ⑪ 工具状态与旁白呈现（用户实测反馈） ----------------

def test_tool_status_is_three_state():
    """工具卡片必须能表达「失败」。

    改前 `_ok_status` 用**黑名单**判定，而技能失败时后端只给 `status="warn"` ——
    warn 不在黑名单里 → 判成成功 → **卡片显示绿色 OK，可 detail 里明明写着 exit=1**。
    """
    ac = _src("src/agent_core.py")
    check("⑪ _ok_status 改为白名单（未知不再默认成功）",
          'return st in ("ok", "success", "done", "completed", "allow")' in ac)
    check("⑪ 进行中/待确认 → None（前端渲染金色运行中）",
          '"info", "pending", "running", "wait", "waiting"' in ac)
    check("⑪ 技能结果显式给出 ok 字段", '"name": f"技能 {name}", "ok": ok' in ac)


def test_narration_detects_real_case():
    """拿用户实测那句话做样本：必须命中，且不能误伤正常旁白。"""
    import agent_core

    f = agent_core._looks_like_narration
    check("⑪ 命中实测那句内心独白",
          f("15 分钟级别 K 线数据接口不支持（market-data 仅提供 1h/4h/1d），"
            "让我换用 1h 级别补档，再结合 coin-report 现有数据做分析。"))
    check("⑪ 不误伤结论型过渡句", not f("先看行情：ETH 现价 2699，24h +1.1%"))
    check("⑪ 空输入不抛", f("") is False and f(None) is False)


def test_narration_is_folded_not_dumped():
    """模型的内心独白不该直接铺在正文里（用户实测反馈）。"""
    ac = _src("src/agent_core.py")
    check("⑪ 有叙述判定函数", "def _looks_like_narration(" in ac)
    check("⑪ 叙述型走 narration 事件", '"type": "narration"' in ac)
    check("⑪ 普通旁白仍当正文铺开", '"type": "text", "delta": chunk}' in ac)
    fe = _src("frontend/src/views/ChatView.tsx")
    check("⑪ 前端处理 narration", 'ev.type === "narration"' in fe)
    check("⑪ 渲染成可折叠块（不删只折）", "m.narration?.length" in fe and "<details" in fe)
    loc = _src("frontend/src/i18n/locales.ts")
    check("⑪ i18n 键 chat.narration zh/en 各一处", loc.count('"chat.narration"') == 2,
          str(loc.count('"chat.narration"')))


# ---------------- ⑫ 工具卡片「劣质感」治理（用户实测反馈） ----------------

_SKILL_FAIL_SAMPLE = (
    '{"error":"klines: {\\"SYMBOL\\":\\"ETHUSDT\\",\\"INTERVAL\\":\\"15M\\",\\"LIMIT\\":96,'
    '\\"MARKET\\":\\"SPOT\\"} 无数据（interval=1h market=spot）。检查符号拼写、interval 合法值'
    '（1h/4h/1d 等）与 market（spot/futures）；合约下架币试试 spot"} '
    '[stderr] (node:19176) [UNDICI-EHPA] Warning: EnvHttpProxyAgent is experimental, '
    'expect them to change at any time.'
)
_SKILL_OK_SAMPLE = (
    '{"report":"F:\\\\1\\\\BAZZ.AGENT\\\\workspace\\\\妖币\\\\ETHUSDT_研报_202610010443.md",'
    '"summary":{"price":2696.54,"chg_24h":1.04}} '
    '(node:6636) [UNDICI-EHPA] Warning: EnvHttpProxyAgent is experimental.'
)


def test_skill_detail_line_hides_json_and_noise():
    """技能卡片 detail 必须是一行人话，不能倒 JSON / Node 警告。

    用户实测原文里那 4 张失败卡片的 detail 是「`exit=1 · 0.41s` + 一坨原始 JSON + UNDICI 警告」，
    反馈原话「一股劣质的味道」。这里用**原样样本**跑摘要器。
    """
    import agent_core

    f = agent_core._skill_detail_line
    bad = f({"elapsed": 0.41}, _SKILL_FAIL_SAMPLE)
    check("⑫ 失败行给出人话原因", "失败：" in bad and "无数据" in bad, bad)
    check("⑫ 失败行不含内嵌 JSON", "{" not in bad and "SYMBOL" not in bad, bad)
    check("⑫ 失败行不含 Node / UNDICI 噪声",
          "UNDICI" not in bad and "node:" not in bad and "EnvHttpProxy" not in bad, bad)
    check("⑫ 失败行是一行（无换行）", "\n" not in bad, bad)

    ok = f({"elapsed": 2.22}, _SKILL_OK_SAMPLE)
    check("⑫ 成功行取结果要点（生成文件名）", "已生成" in ok and "ETHUSDT_研报" in ok, ok)
    check("⑫ 成功行不含反斜杠路径", "\\" not in ok and "F:" not in ok, ok)

    # 半截 JSON（无闭合引号）也不能原样倒出
    half = f({"elapsed": 0.3}, '{"error":"funding: 未找到 {\\"SYMBOL\\":\\"ETHUSDT\\"} 的 USDT 永续合约')
    check("⑫ 半截 JSON 也能剥壳", not half.rstrip().endswith("合约\"") and "{" not in half, half)


def test_skill_card_moves_raw_out_of_detail():
    """原始输出必须挪到 raw 字段折叠承载，而不是塞进 detail。"""
    ac = _src("src/agent_core.py")
    check("⑫ 有摘要器", "def _skill_detail_line(" in ac)
    check("⑫ 有噪声正则", "_SKILL_NOISE_RE" in ac)
    check("⑫ detail 不再切 out[:240]", "out[:240]" not in ac)
    check("⑫ raw 字段承载原始输出（两处卡片）", ac.count('"raw": out[:2000]') >= 2,
          str(ac.count('"raw": out[:2000]')))
    check("⑫ _norm_tool 会透传 raw", "nt = dict(t)" in ac)
    fe = _src("frontend/src/views/ChatView.tsx")
    check("⑫ 前端 tools 类型带 raw", "raw?: string" in fe)
    check("⑫ 前端把 raw 折进 details", "m.narration?.length" in fe and "tl.raw" in fe)
    loc = _src("frontend/src/i18n/locales.ts")
    check("⑫ i18n 键 chat.rawOutput zh/en 各一处", loc.count('"chat.rawOutput"') == 2,
          str(loc.count('"chat.rawOutput"')))


def test_memory_gate_not_over_strict():
    """记忆门控不能是「必须命中偏好词表」—— 那是「记不住」的直接原因。

    改前：`if not any(h in um for h in _AUTO_MEM_PREF_HINTS): return 跳过`。
    词表再全也覆盖不了自然语言（「以后别给我推合约了」未必命中），
    结果该记的全被挡在门外。现在只挡明显太短的闲聊。
    """
    ac = _src("src/agent_core.py")
    check("⑫ 不再无条件要求命中词表",
          "if not any(h in um for h in _AUTO_MEM_PREF_HINTS):" not in ac)
    check("⑫ 改为「太短且无信号」才跳过",
          'len(um) < 12 and not any(h in um for h in _AUTO_MEM_PREF_HINTS)' in ac)
    check("⑫ 冷却仍在（成本可控）", "auto_mem_last_ts" in ac and "_AUTO_MEM_COOLDOWN" in ac)


# ---------------- ⑬ 技能参数形状（「不会自己解决问题」的直接病灶） ----------------

def test_skill_args_normalized_to_positional():
    """位置参数族技能收到 JSON 对象要自动摊平。

    实测事故：market-data 是位置参数 CLI，模型却传 JSON → 整坨被当成 SYMBOL
    → 报「无数据」→ 换 4 组数值继续撞。这是工具说明写错引发的一连串空转。
    """
    import exec_sandbox as es

    f = es.normalize_skill_args
    check("⑬ JSON → 位置参数（全字段）",
          f("market-data", 'klines {"symbol":"ETHUSDT","interval":"1d","limit":90,"market":"futures"}')
          == "klines ETHUSDT 1d 90 futures")
    check("⑬ 补默认值不串槽（只给 limit）",
          f("market-data", '{"symbol":"BTCUSDT","limit":90}') == "klines BTCUSDT 1h 90 spot")
    check("⑬ 纯位置参数不动",
          f("market-data", "klines ETHUSDT 1h 24 spot") == "klines ETHUSDT 1h 24 spot")
    check("⑬ 无参子命令不动", f("market-data", "fng") == "fng")
    check("⑬ coin-report JSON → 位置参数",
          f("coin-report", '{"symbol":"ETHUSDT"}') == "report ETHUSDT futures")
    check("⑬ JSON 参数族不受影响（meme-rush 原样）",
          f("meme-rush", '{"chainId":"CT_501","rankType":10}') == '{"chainId":"CT_501","rankType":10}')
    check("⑬ 传错形状给可读错误（不是静默）",
          "SandboxError" in _src("src/exec_sandbox.py") and "_POSITIONAL_SPECS" in _src("src/exec_sandbox.py"))


def test_skill_interval_validated_at_boundary():
    """非法 interval 必须在**边界**就被拦下并给出可用值。

    改前是丢给 CLI 报「无数据（interval=15m market=spot）」—— 那句只字不提
    「15m 根本不支持」，模型只能靠猜，实测来回猜了 4 次。
    """
    import exec_sandbox as es

    try:
        es.normalize_skill_args("market-data", '{"SYMBOL":"ETHUSDT","INTERVAL":"15M"}')
        check("⑬ 非法 interval 应报错", False, "未抛异常")
    except es.SandboxError as e:
        msg = str(e)
        check("⑬ 非法 interval 报错并点明可选值", "1h/4h/1d" in msg, msg)
        check("⑬ 报错给出可直接照抄的改法", "market-data klines ETHUSDT 1h" in msg, msg)
    check("⑬ 大写 interval 自动转小写（合法值）",
          "_INTERVAL_OK" in _src("src/exec_sandbox.py")
          and es.normalize_skill_args("market-data", '{"symbol":"ETH","interval":"1D"}') == "klines ETH 1d 24 spot")


def test_run_skill_doc_is_positional():
    """工具说明必须写对参数形状 —— 这是事故的源头，改错了它会再犯一次。"""
    ll = _src("src/llm.py")
    check("⑬ market-data 说明改为位置参数", "klines <SYMBOL> [interval] [limit] [market]" in ll)
    check("⑬ 明说不要传 JSON", "不要传 JSON" in ll)
    check("⑬ 点明没有 15m/30m", "没有 15m/30m" in ll)
    check("⑬ 通用用法行区分两种形状", "位置参数" in ll and "参数形状" in _src("src/agent_core.py"))


def main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        try:
            fn()
        except AssertionError as e:
            print(f"[FAIL] {fn.__name__}: {e}")
            FAILS.append(fn.__name__)
        except Exception as e:  # noqa: BLE001
            print(f"[ERROR] {fn.__name__}: {type(e).__name__}: {e}")
            FAILS.append(fn.__name__)
    print("\n" + "=" * 56)
    if FAILS:
        print(f"失败 {len(FAILS)} 组：{FAILS}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
