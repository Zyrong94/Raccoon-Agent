"""工具目录；执行与权限检查不属于注册表。"""

from __future__ import annotations

from threading import RLock

from jsonschema import Draft202012Validator

from raccoon.runtime.model_types import ToolDefinition
from raccoon.modules.tools.types import Tool


class ToolRegistry:
    """保存工具定义和执行函数，维护目录来源。"""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._lock = RLock()
        self._tools: dict[str, Tool] = {}
        self._supplied_names: set[str] = set()
        for tool in tools or []:
            self.register(tool, supplied=True)

    def register(self, tool: Tool, *, supplied: bool = False) -> None:
        """注册一个工具。

        同名工具会让 tool calling 变得不可预测，所以这里直接拒绝重复注册。
        """

        with self._lock:
            if tool.exposure not in {"direct", "deferred"}:
                raise ValueError(f"未知工具曝光方式：{tool.exposure}")
            if not callable(tool.executor):
                raise ValueError(f"工具缺少执行入口：{tool.name}")
            Draft202012Validator.check_schema(tool.definition.parameters or {"type": "object"})
            if tool.name in self._tools:
                raise ValueError(f"工具已存在：{tool.name}")
            self._tools[tool.name] = tool
            if supplied:
                self._supplied_names.add(tool.name)

    def replace_supplied_tools(self, tools: list[Tool]) -> None:
        """Refresh provider-supplied tools while keeping session-bound tools."""
        with self._lock:
            names = {tool.name for tool in tools}
            if len(names) != len(tools):
                raise ValueError("工具目录中有重复名称")
            for name in self._supplied_names - names:
                self.unregister(name)
            for tool in tools:
                if self._tools.get(tool.name) is tool and tool.name in self._supplied_names:
                    continue
                self.unregister(tool.name)
                self.register(tool, supplied=True)

    def definitions(self) -> list[ToolDefinition]:
        """返回所有模型可见的工具 schema。"""

        # 注意这里返回的是“给模型看的声明”，不是 executor 本身。provider 会把这些
        # ToolDefinition 转成 OpenAI/Anthropic 等具体协议里的 tool schema。
        with self._lock:
            return [tool.definition for tool in self._tools.values()]

    def unregister(self, name: str) -> None:
        with self._lock:
            self._tools.pop(name, None)
            self._supplied_names.discard(name)

    def names(self) -> list[str]:
        """返回当前已注册工具名称。"""

        with self._lock:
            return list(self._tools.keys())

    def tools(self, *, supplied_only: bool = False) -> list[Tool]:
        """返回当前已注册工具对象。"""

        with self._lock:
            return [tool for name, tool in self._tools.items() if not supplied_only or name in self._supplied_names]

    def get(self, name: str) -> Tool | None:
        """按名称返回工具对象。"""

        with self._lock:
            return self._tools.get(name)
