# 会话历史

跟踪一条用户消息从写入记录到重新加载的过程，再对照助手回复和工具结果的写入入口。

## 源码入口

- [会话记录](../raccoon/modules/sessions/events.py)：`SessionEvent` 表示持久化的一条会话事件。
- [数据结构](../raccoon/modules/sessions/models.py)：`SessionData` 保存会话数据，`AgentMessage` 和 `MessagePart` 表示消息及其组成部分。
- [存取与回放](../raccoon/modules/sessions/store.py)：`SessionStore.append_event()` 追加事件，`load()` 和 `apply_event()` 从记录还原会话。

[返回模块指南](../README.md#模块指南)
