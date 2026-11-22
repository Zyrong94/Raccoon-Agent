# 工具执行与结果回写

从模型提出的一批工具调用开始，追踪每项操作的结果如何与原调用对应并写回会话。

## 源码入口

- [批量分派](../raccoon/modules/agent/tool_execution.py)：`execute_tool_batch()` 分派本轮工具调用，`_record_result()` 处理返回结果。
- [单个工具](../raccoon/modules/tools/execution.py)：`execute_tool()` 执行指定工具并返回 `ToolResult`。
- [结果写回](../raccoon/modules/agent/session.py)：`AgentSession.settle_tool_result()` 把工具结果与原来的 `ToolCall` 对应。

[返回模块指南](../README.md#模块指南)
