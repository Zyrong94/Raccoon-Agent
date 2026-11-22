# 上下文组装与压缩

从已保存的会话历史出发，确定本轮发送给模型的内容，以及超出预算时怎样选取和压缩较早的消息。

## 源码入口

- [消息组装](../raccoon/modules/context/context_builder.py)：`ContextBuilder.build_provider_messages()` 把会话数据转换为模型输入消息。
- [预算估算](../raccoon/modules/context/token_budget.py)：`build_context_budget()` 估算本轮输入的 Token 占用。
- [历史压缩](../raccoon/modules/context/compaction.py)：`prepare_compaction()` 选取压缩范围，`generate_compaction()` 生成摘要。

[返回模块指南](../README.md#模块指南)
