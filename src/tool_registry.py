# -*- coding: utf-8 -*-
"""工具注册表 —— 「有哪些工具」只在一个地方声明。

**为什么**（v1.7.4，做法借鉴 Hermes `tools/registry.py`）：

改造前，工具清单散在两处、各写各的：
  · `agent_core._dispatch_tool` 里一条 110 行的 `if name == "..."` 链 —— 决定**怎么执行**
  · `llm.py` 的 `TOOLS` 里 24 份 schema —— 决定**怎么告诉模型**
两边没有任何机制保证一致：加了工具只改一边，模型就看不到（或调不动）；
也没法回答「现在一共几个工具」「哪些工具需要审批」这类问题。

**做法**：每个工具在自己的注册点声明一次（name / handler / toolset / emoji / approval），
dispatcher 只做一次查表。schema 暂时留在 `llm.py`，由护栏比对**名字集合**是否一致；
下一批（第 3 项）会把 schema 也搬进这里。

**注意**：注册发生在 `agent_core` 模块加载时，因此注册块必须写在所有 `_run_*`
函数定义**之后**（文件末尾），否则 handler 还没绑定。
"""
from typing import Any, Callable, Dict, List, Optional


class ToolRegistry:
    """极简注册表：注册即声明，查表即分发。刻意不做插件/动态发现 —— 那是 Extension 层的事。"""

    def __init__(self) -> None:
        self._tools: Dict[str, dict] = {}

    def register(self, name: str, handler: Callable, *, toolset: str = "",
                 emoji: str = "", approval: bool = False, summary: str = "") -> Callable:
        if not name:
            raise ValueError("工具必须有名字")
        if name in self._tools:
            raise ValueError(f"工具重复注册：{name}")
        self._tools[name] = {
            "name": name, "handler": handler, "toolset": toolset,
            "emoji": emoji, "approval": bool(approval), "summary": summary,
        }
        return handler

    def get(self, name: str) -> Optional[dict]:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> List[str]:
        return sorted(self._tools)

    def rows(self) -> List[dict]:
        return [self._tools[n] for n in self.names()]

    def approvals(self) -> List[str]:
        """需要人工确认的工具名（审批屏障的单一来源）。"""
        return [n for n in self.names() if self._tools[n]["approval"]]

    def __len__(self) -> int:
        return len(self._tools)


REGISTRY = ToolRegistry()
register = REGISTRY.register
