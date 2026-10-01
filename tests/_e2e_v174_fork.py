# -*- coding: utf-8 -*-
"""v1.7.4 端到端验证：会话分支（**隔离工作区，不碰真实库**）。

分叉的关键不是「复制一份」，而是：新会话带着**该点之前的上下文**、
记住**血统**（从哪来、从第几条分叉），且**不污染父会话**。

运行：.venv/Scripts/python.exe tests/_e2e_v174_fork.py
"""
import os
import sys
import tempfile

WS = tempfile.mkdtemp(prefix="bazz_fork_")
os.environ["BAZZ_WORKSPACE"] = WS
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

import state  # noqa: E402

FAILS = []


def ck(name, cond, extra=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({extra})" if (extra and not cond) else ""))
    if not cond:
        FAILS.append(name)


print("隔离工作区:", state.DB_PATH)

c1 = state.new_conversation("原始分析")
state.add_message(c1, "user", "问题一")
state.add_message(c1, "assistant", "回答一")
state.add_message(c1, "user", "问题二")

# ① 从中途分叉（保留前 2 条）
r = state.fork_conversation(c1, from_index=2)
ck("① 分叉成功", r.get("ok") is True, str(r))
new_id = r.get("id")
ck("① 复制了 2 条", r.get("copied") == 2, str(r.get("copied")))

# ② 血统记录
conv = state.get_conversation(new_id) or {}
ck("② 记住父会话", conv.get("parent_id") == c1, str(conv.get("parent_id")))
ck("② 记住分叉点", int(conv.get("fork_from") or 0) == 2, str(conv.get("fork_from")))

# ③ 上下文被带过来
msgs = state.get_messages(new_id)
ck("③ 新会话带上了之前的上下文", len(msgs) == 2, str(len(msgs)))
ck("③ 内容一致", msgs[0]["content"] == "问题一" and msgs[1]["content"] == "回答一",
   str([m["content"] for m in msgs]))

# ④ 父会话不受影响
ck("④ 父会话消息数不变", len(state.get_messages(c1)) == 3, str(len(state.get_messages(c1))))

# ⑤ 新会话出现在列表里且带血统字段
lst = state.list_conversations()
row = next((x for x in lst if x["id"] == new_id), None)
ck("⑤ 新会话在列表里", row is not None)
ck("⑤ 列表也带 parent_id", (row or {}).get("parent_id") == c1, str((row or {}).get("parent_id")))

# ⑥ from_index=0 → 整条复制
r2 = state.fork_conversation(c1, from_index=0)
ck("⑥ from_index=0 复制全部", r2.get("copied") == 3, str(r2.get("copied")))

# ⑦ 源会话不存在时优雅失败
r3 = state.fork_conversation("no-such-cid")
ck("⑦ 源不存在返回 ok=False", r3.get("ok") is False, str(r3))

print("\n" + "=" * 52)
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS}")
    sys.exit(1)
print("全部通过 ✅")
