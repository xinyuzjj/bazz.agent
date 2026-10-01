# -*- coding: utf-8 -*-
"""v1.7.4 端到端验证：跨会话召回（**隔离工作区，不碰真实库**）。

单元断言（tests/test_v174_agent_ux.py ⑥ 组）只测不依赖 DB 的部分（抽词、注入块格式）。
真正涉及数据库的行为在这里跑：跨会话命中、相关度排序、排除当前会话、无关查询不注入。

运行：.venv/Scripts/python.exe tests/_e2e_v174_recall.py
"""
import os
import sys
import tempfile

WS = tempfile.mkdtemp(prefix="bazz_recall_")
os.environ["BAZZ_WORKSPACE"] = WS
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

import state   # noqa: E402
import recall  # noqa: E402

FAILS = []


def ck(name, cond, extra=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({extra})" if (extra and not cond) else ""))
    if not cond:
        FAILS.append(name)


print("隔离工作区:", state.DB_PATH)

# 造历史：两个会话
c1 = state.new_conversation("妖币雷达讨论")
state.add_message(c1, "user", "帮我看下 WLDUSDT 的妖币雷达位阶")
state.add_message(c1, "assistant", "WLDUSDT 目前在已拉升阶段，窗口已过")

c2 = state.new_conversation("广场发文")
state.add_message(c2, "user", "把 WLDUSDT 发到广场，记得先查名单")
state.add_message(c2, "assistant", "已按流程发帖")

c3 = state.new_conversation("无关会话")
state.add_message(c3, "user", "随便聊聊别的东西")

# ① 跨会话命中
hits = recall.recall("WLD 之前那个分析怎么说")
titles = [h["title"] for h in hits]
ck("① 跨会话能召回", len(hits) >= 2, str(titles))
ck("① 不召回无关会话", "无关会话" not in titles, str(titles))
ck("① 按相关度降序", all(hits[i]["score"] >= hits[i + 1]["score"] for i in range(len(hits) - 1)),
   str([round(h["score"], 1) for h in hits]))

# ② 排除当前会话
ex = [h["title"] for h in recall.recall("WLD 之前那个分析怎么说", exclude_cid=c1)]
ck("② 排除当前会话生效", "妖币雷达讨论" not in ex and "广场发文" in ex, str(ex))

# ③ 注入块
block = recall.build("WLD 之前那个分析怎么说")
ck("③ 注入块带召回标注", "历史会话召回" in block)
ck("③ 注入块含命中会话标题", "广场发文" in block or "妖币雷达讨论" in block)

# ④ 无关 / 空查询不注入
ck("④ 无关查询不注入", recall.build("今天天气怎么样qqqzzz") == "")
ck("④ 空查询不注入", recall.build("") == "")

# ⑤ 不抛异常
ck("⑤ 垃圾输入不抛", isinstance(recall.build(None), str))

print("\n" + "=" * 52)
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS}")
    sys.exit(1)
print("全部通过 ✅")
