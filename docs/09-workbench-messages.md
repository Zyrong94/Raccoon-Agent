# 工作台消息收发

跟随一条带附件的工作台消息，分别追踪用户输入的提交路径和进度、回复的展示路径。

## 源码入口

- [消息提交](../raccoon/interfaces/web/conversation_actions.py)：`send_chat_message()` 读取 `WebAttachments` 中的附件引用，构造消息事件并提交给会话。
- [服务端推送](../raccoon/interfaces/web/event_stream.py)：`WebEventStream.publish()` 发布更新，`replay_after()` 按游标补发事件。
- [工作台订阅](../web-next/lib/workbench/client.ts)：浏览器通过 `EventSource` 订阅 `/api/events`。

[返回模块指南](../README.md#模块指南)
