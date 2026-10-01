# -*- coding: utf-8 -*-
"""v1.7.4 端到端验证：技能自建（**全程在临时目录，绝不碰项目技能目录**）。

这条最容易出事的地方是「自动改正式技能」—— 所以验证的重点不是「能不能生成」，
而是**边界**：默认关、草案只在 `_drafts/`、采纳才转正、非法名拒绝。

运行：.venv/Scripts/python.exe tests/_e2e_v174_skill.py
"""
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
TD = tempfile.mkdtemp(prefix="bazz_skill_root_")
WS = tempfile.mkdtemp(prefix="bazz_skill_ws_")
os.chdir(TD)                       # save_draft / adopt 以 cwd 为根 → 必须换到临时目录
os.environ["BAZZ_WORKSPACE"] = WS
sys.path.insert(0, os.path.join(ROOT, "src"))

import state          # noqa: E402
import skill_learning  # noqa: E402

FAILS = []


def ck(name, cond, extra=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({extra})" if (extra and not cond) else ""))
    if not cond:
        FAILS.append(name)


print("临时技能根:", TD)

# ① 默认必须是关闭的
ck("① 默认关闭（不擅自改变能力边界）", skill_learning.enabled() is False)

# ② 打开后生效
state.set_setting("skill_learning_enabled", "1")
ck("② 打开后 enabled", skill_learning.enabled() is True)
ck("③ 触发节奏（每 8 轮）", skill_learning.due(8) and not skill_learning.due(7))

# ④ 草案只落 _drafts
p = skill_learning.save_draft({"name": "test-draft", "description": "测试用",
                               "body": "## 步骤\n1. …"})
ck("④ 草案落进 _drafts", os.sep + "_drafts" + os.sep in p, p)
ck("④ SKILL.md 已写出", os.path.isfile(os.path.join(p, "SKILL.md")))
ck("④ 带 frontmatter", "agent-authored" in open(os.path.join(p, "SKILL.md"), encoding="utf-8").read())

# ⑤ 关键：正式技能目录**不能**被动
formal = os.path.join(TD, ".agents", "skills", "test-draft")
ck("⑤ 正式目录未被写入（草案 ≠ 生效）", not os.path.exists(formal))

# ⑥ 能列举
lst = skill_learning.list_drafts()
ck("⑥ 能列出草案", len(lst) == 1 and lst[0]["name"] == "test-draft", str(lst))

# ⑦ 采纳才转正
r = skill_learning.adopt_draft("test-draft")
ck("⑦ 采纳后转到正式目录", r.get("ok") is True and os.path.isfile(os.path.join(formal, "SKILL.md")),
   str(r))

# ⑧ 安全：拒绝路径穿越 / 同名覆盖
ck("⑧ 拒绝路径穿越名", skill_learning.adopt_draft("../../evil").get("ok") is False)
ck("⑧ 拒绝大写/空格名", skill_learning.adopt_draft("Bad Name").get("ok") is False)
ck("⑨ 同名技能已存在时拒绝覆盖", skill_learning.adopt_draft("test-draft").get("ok") is False)

# ⑩ 丢弃
skill_learning.save_draft({"name": "tmp-draft", "description": "x", "body": "y"})
ck("⑩ 丢弃草案成功", skill_learning.discard_draft("tmp-draft").get("ok") is True)

print("\n" + "=" * 52)
if FAILS:
    print(f"失败 {len(FAILS)} 项：{FAILS}")
    sys.exit(1)
print("全部通过 ✅")
