# 工具权限

以一次工具操作为例，跟踪从权限请求到放行、拒绝或等待确认的判断。

## 源码入口

- [检查入口](../raccoon/modules/agent/tool_execution.py)：`check_tool_permission()` 接收工具调用，推进权限检查。
- [权限决定](../raccoon/modules/permissions/manager.py)：`PermissionManager.preflight()` 返回初步决定，`resolve_confirmation()` 处理用户选择。
- [默认策略](../raccoon/modules/permissions/policy.py)：`DefaultPermissionPolicy.decide()` 根据操作内容和权限模式判断请求。
- [自动审核](../raccoon/modules/agent/permission_review.py)：`review_tool_operation()` 负责需要模型审核的操作。

[返回模块指南](../README.md#模块指南)
