# 后台任务

从工具转入后台执行开始，跟踪作业完成后，其结果如何以新事件回到所属会话。

## 源码入口

- [后台作业](../raccoon/runtime/background.py)：`BackgroundJobManager.start()` 创建 `BackgroundJob`，`_finish()` 记录结果并触发完成通知。
- [会话订阅](../raccoon/app/conversations.py)：`ConversationService._subscribe_background()` 接收 `BackgroundNotification`，将其转换为事件并送回会话。

[返回模块指南](../README.md#模块指南)
