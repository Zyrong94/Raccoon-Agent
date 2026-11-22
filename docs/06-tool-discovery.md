# 工具注册与发现

沿一个工具从注册、声明到可调用的过程，区分已注册、可发现和已加载的工具。

## 源码入口

- [工具对象](../raccoon/modules/tools/types.py)：`Tool` 保存工具的执行入口和参数信息。
- [注册与声明](../raccoon/modules/tools/registry.py)：`ToolRegistry.register()` 注册工具，`definitions()` 列出对应的 `ToolDefinition`。
- [可见范围](../raccoon/modules/agent/run.py)：`_discoverable_tool_definitions()` 筛选本次运行可发现的工具，`_validate_tool_call()` 检查延迟加载的工具是否已经加载。

[返回模块指南](../README.md#模块指南)
