# Agent 主循环与结束条件

从会话发起一次运行开始，跟踪模型响应、工具调用和下一轮推理，区分正常结束、用户中断、达到上限和异常退出。

## 源码入口

- [会话状态](../raccoon/modules/agent/session.py)：同一段会话的多次运行共享一个 `AgentSession`，每次运行使用自己的 `AgentRun`。
- [运行入口](../raccoon/app/session_runtime.py)：`SessionRuntime.run_turn()` 创建一次 `AgentRun`。
- [主循环](../raccoon/modules/agent/run.py)：`run_agent()` 调用 `request_model_response()`。模型没有请求工具时，还要检查待收取的异步结果和未完成的计划。正常回复发布前还会调用 `try_close_input()`。追加列表非空时再请求一轮模型，入口关闭后才由 `finish_turn()` 保存最终响应。
- [运行边界](../raccoon/modules/agent/loop_limits.py)：`AgentLoopLimits` 和 `AgentLoopStopReason` 描述工具轮数、模型调用次数和单次运行耗时的上限。

[返回模块指南](../README.md#模块指南)
