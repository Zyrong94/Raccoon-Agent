# 定时任务

从创建定时任务开始，跟踪到期检查和触发执行，比较单次任务与周期任务的下一次触发时间。

## 源码入口

- [定时任务存储](../raccoon/modules/scheduling/store.py)：`ScheduleStore.create()` 保存任务及其调度参数。
- [到期检查](../raccoon/modules/scheduling/service.py)：`SchedulingService.tick()` 检查并分派已到期的任务。
- [时间计算](../raccoon/modules/scheduling/schedules.py)：`next_time()` 和 `advance()` 计算触发时间。

[返回模块指南](../README.md#模块指南)
