# -*- coding: utf-8 -*-
"""v1.7.5 后续：LLM 真流式护栏。

用户实测反馈：正文「直接蹦出来，而不是缓慢的显示出来」。
根因：agent 每轮走 chat_with_tools（**非流式**），整轮（35s 思考 + 2000 字正文）
憋到最后一次性吐出，前端一两帧画完。

修复：chat_with_tools_stream（SSE 增量 + 工具调用按 index 组装 + <thinking> 吞吐机）
+ agent_core._StreamEmitter（思考按行攒批 / 正文分类门：旁白扣住、结论直通）。

运行：.venv/Scripts/python.exe tests/test_v175_streaming.py
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

import llm  # noqa: E402
import agent_core  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({extra})" if (extra and not cond) else ""))
    if not cond:
        FAILS.append(name)


def _src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


# ---------------- ① <thinking> 吞吐机 ----------------

def test_gate_swallows_split_tags():
    """<thinking> 标签被拆到多个 chunk 也必须截住，绝不能闪现在正文里。"""
    g = llm._ThinkingGate(active=True)
    cp, tp = [], []
    for s in ["<thi", "nking>先查雷达名单\n再查行情</thin", "king>XRP 现价 1.50",
              "37，90 日 88.2% 分位。</thinking>正文接上"]:
        a, b = g.feed(s)
        cp += a
        tp += b
    clean, think = g.finish()
    check("① 思考不进正文", "<thinking" not in clean and "先查雷达" not in clean, clean)
    check("① 正文完整保留", "XRP 现价 1.5037" in clean and "正文接上" in clean, clean)
    check("① 思考边吞边外发（用户能看到思考过程）",
          "先查雷达名单" in "".join(tp) and "再查行情" in "".join(tp), str(tp)[:80])
    check("① finish 汇总思考全文", "先查雷达名单" in think and "再查行情" in think, think)


def test_gate_unclosed_tail_and_inactive():
    """未闭合的思考尾整段算思考（与非流式 _extract_thinking_block 同语义）；关闭时直通。"""
    g = llm._ThinkingGate(active=True)
    cp = []
    for s in ["结论先行：看多。", "<thinking>后半段推理被 max_tokens 截断"]:
        a, _ = g.feed(s)
        cp += a
    clean, think = g.finish()
    check("① 未闭合尾不进正文", "截断" not in clean, clean)
    check("① 未闭合尾进思考", "截断" in think, think)
    check("① 闭合前的正文保留", "结论先行" in clean, clean)

    g2 = llm._ThinkingGate(active=False)
    a, b = g2.feed("带<thinking>标签的原文")
    check("① deep 关闭时直通（与非流式行为一致）",
          "".join(a) == "带<thinking>标签的原文" and not b)


# ---------------- ② _StreamEmitter ----------------

def test_emitter_reasoning_batches_by_line():
    """思考必须按完整行攒批 —— 逐 delta 发会让前端碎出几百条假步骤。"""
    e = agent_core._StreamEmitter()
    out = []
    for delta in ["第一", "步：核对区间位置\n第二", "步：检查资金费率\n", "第三步无换行尾"]:
        out += e.feed_reasoning(delta)
    out += e.finish_reasoning()
    check("② 按行成批（不是逐 delta）", len(out) == 3, str(len(out)))
    check("② 行内容完整", out[0] == "第一步：核对区间位置" and out[1] == "第二步：检查资金费率", str(out[:2]))
    check("② 尾批不丢", out[2] == "第三步无换行尾", str(out[2]))
    check("② 记账 reasoning_emitted", e.reasoning_emitted)


def test_emitter_content_gate_and_narration():
    """正文：结论型过 64 字门后直通；「让我…」旁白扣住（等调用方定性）。"""
    e = agent_core._StreamEmitter()
    # 结论型：先攒后直通
    pieces = []
    pieces += e.feed_content("XRP 现价 1.5037（24h +0.37%），90 日区间 88.2% 分位，")
    pieces += e.feed_content("距 90 日高点仅 -4.36%。")
    pieces += e.feed_content("后续增量。")
    pieces += e.finish_content()
    joined = "".join(pieces)
    check("② 结论型直通且过门后才发", "XRP 现价" in joined and "后续增量" in joined, joined)
    check("② 结论型不定性为旁白", not e.narration)

    # 旁白型（用户实测原话）
    e2 = agent_core._StreamEmitter()
    got = []
    got += e2.feed_content("15 分钟级别 K 线数据接口不支持（market-data 仅提供 1h/4h/1d），")
    got += e2.feed_content("让我换用 1h 级别补档，再结合 coin-report 现有数据做分析。")
    got += e2.finish_content()
    check("② 实测旁白句被扣住（不铺正文）", got == [], str(got))
    check("② 定性为旁白", e2.narration)

    # 短旁白（<64 字，流结束时才分类）
    e3 = agent_core._StreamEmitter()
    got3 = e3.feed_content("让我查一下。")
    got3 += e3.finish_content()
    check("② 短旁白同样扣住", got3 == [] and e3.narration, str(got3))


# ---------------- ③ chat_with_tools_stream 假服务端端到端 ----------------

def test_stream_e2e_with_fake_sse_server():
    """本地假 SSE 服务端 → 全链路：思考外发 / thinking 吞吐 / 工具调用按 index 组装。"""
    sse_lines = [
        {"delta": {"reasoning_content": "第一步：核对区间位置\n"}},
        {"delta": {"reasoning_content": "第二步：检查资金费率\n"}},
        {"delta": {"content": "<thi"}},
        {"delta": {"content": "nking>雷达名单没有 XRPU\n按普通币处理</thinking>"}},
        {"delta": {"content": "XRP 现价 1.5037（24h +0.37%），"}},
        {"delta": {"content": "90 日区间 88.2% 分位。"}},
        {"delta": {"tool_calls": [{"index": 0, "id": "call_1",
                                   "function": {"name": "run_skill", "arguments": ""}}]}},
        {"delta": {"tool_calls": [{"index": 0,
                                   "function": {"arguments": '{"skill_name":"market-data","args":'}}]}},
        {"delta": {"tool_calls": [{"index": 0,
                                   "function": {"arguments": '"klines XRPUSDT 1d 90 futures"}'}}]}},
        {"delta": {}, "finish_reason": "tool_calls"},
    ]

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = "".join(f"data: {json.dumps({'model': 'fake-model', 'choices': [ch]}, ensure_ascii=False)}\n\n"
                           for ch in sse_lines) + "data: [DONE]\n\n"
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        real_materialize, real_chain = llm._materialize, llm._chain_for
        llm._materialize = lambda cfg=None: {
            "provider": "custom", "base_url": f"http://127.0.0.1:{port}/v1",
            "model": "fake-model", "api_key": "sk-test", "deep_thinking": True}
        llm._chain_for = lambda cfg, task=None: ["fake-model"]
        try:
            evs = list(llm.chat_with_tools_stream([{"role": "user", "content": "看下 XRP"}], tools=[]))
        finally:
            llm._materialize, llm._chain_for = real_materialize, real_chain
    finally:
        srv.shutdown()

    kinds = [e[0] for e in evs]
    check("③ 产出 done 且唯一", kinds.count("done") == 1, str(kinds))
    done = next(e for e in evs if e[0] == "done")[1]
    # 思考链：原生 2 行 + 吞吐机外发的思考块，都必须在 done 之前到达（真流式的核心）
    res = [e for e in evs if e[0] == "reasoning"]
    all_reasoning = "".join(e[1] for e in res)
    check("③ 原生思考在 done 前到达",
          "第一步：核对区间位置" in all_reasoning and "第二步：检查资金费率" in all_reasoning,
          all_reasoning[:80])
    check("③ thinking 块也作为思考外发（deep 开启）", "雷达名单没有 XRPU" in all_reasoning,
          all_reasoning[:120])
    first_reasoning_idx = kinds.index("reasoning")
    check("③ 思考先于 done（不是最后一次性蹦出）", first_reasoning_idx < kinds.index("done"))
    # 正文：thinking 已剥离
    body = "".join(e[1] for e in evs if e[0] == "content")
    check("③ 正文不含思考块", "雷达名单" not in body and "thinking" not in body, body)
    check("③ 正文完整", "XRP 现价 1.5037" in body and "88.2% 分位" in body, body)
    check("③ 正文在 done 前开始到达", kinds.index("content") < kinds.index("done"))
    # 工具调用：分片 arguments 按 index 组装还原
    tcs = done.get("tool_calls") or []
    check("③ 工具调用组装出 1 个", len(tcs) == 1, str(tcs))
    if tcs:
        check("③ 工具名正确", tcs[0]["name"] == "run_skill", str(tcs[0]))
        check("③ 分片 arguments 合并且可解析",
              tcs[0]["args"] == {"skill_name": "market-data", "args": "klines XRPUSDT 1d 90 futures"},
              str(tcs[0]["args"]))
    check("③ model 记录", done.get("model") == "fake-model", str(done.get("model")))
    check("③ thinking 合并进 out.reasoning（落库/降级用）",
          "雷达名单没有 XRPU" in (done.get("reasoning") or ""), str(done.get("reasoning"))[:80])


# ---------------- ④ agent 循环接线（源码断言） ----------------

def test_agent_loop_wired_to_stream():
    ac = _src("src/agent_core.py")
    check("④ 循环改用流式接口", "llm.chat_with_tools_stream(messages, tools, llm_cfg=llm_cfg)" in ac)
    check("④ 非流式调用已从循环移除",
          "resp = llm.chat_with_tools(messages, tools, llm_cfg=llm_cfg)" not in ac)
    check("④ 思考链不重复发（流式已发过则跳过）",
          "if reasoning and not emitter.reasoning_emitted:" in ac)
    check("④ 最终回答也可能以「让我」开头 → 扣住内容当正文补放",
          "not emitter.saw_content or emitter.narration" in ac)
    check("④ 旁白只有出现 tool_calls 才折叠",
          "if content.strip() and emitter.narration:" in ac)
    check("④ 流式消费的异常被逐段兜住（半路崩了不影响已吐内容）",
          "batches = emitter.feed_reasoning(ev[1])" in ac and "pieces = emitter.feed_content(ev[1])" in ac)
    ll_src = _src("src/llm.py")
    check("④ 流式接口存在且产出 done", 'yield ("done", out)' in ll_src)
    check("④ 非流式接口保留（订阅直连/其他调用方仍用）", "def chat_with_tools(" in ll_src)
    check("④ payload 构造两版共用（name 净化不漂移）",
          "def _tools_payload(" in ll_src and ll_src.count("_tools_payload(model, base_msgs, tools, temperature, eff_max)") == 2)


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
