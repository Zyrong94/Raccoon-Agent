"""Wire the scheduled execution service to the shared runtime factory."""
from raccoon.modules.scheduling.service import SchedulingService as SchedulingServiceCore
import time


class SchedulingService(SchedulingServiceCore):
    def __init__(self, data_root, *, store=None, runtime_factory=None, now=time.time, main_bridge=None, workbench=None):
        if runtime_factory is None:
            from raccoon.bootstrap.runtime import create_runtime
            runtime_factory = create_runtime
        from raccoon.interfaces.local.main_host import LocalMainEndpoint, call_main_host
        from uuid import uuid4
        bridges = {}
        async def main_call(task, operation, body):
            bridge = main_bridge
            if bridge is None:
                from raccoon.bootstrap.main_host import MainSessionBridge
                key = (task['project'], task['data_root'])
                bridge = bridges.get(key)
                if bridge is None:
                    bridge = bridges[key] = MainSessionBridge(task['project'], task['data_root'], runtime_factory=runtime_factory)
            return await bridge.call(operation, body)
        self._owned_main_bridges = bridges
        self.workbench = workbench
        self.generation = uuid4().hex
        self.endpoint = LocalMainEndpoint(data_root, descriptor_name='project_timers.json') if workbench is not None else None
        async def project_call(operation, body):
            if self.workbench is not None:
                return await self.dispatch(operation, body)
            return await call_main_host(data_root, operation, body, descriptor_name='project_timers.json')
        super().__init__(
            data_root, store=store, project_call=project_call, now=now,
            main_call=main_call,
        )

    async def start(self, *, background=True):
        if self.endpoint is not None:
            await self.endpoint.start(self)
        await super().start(background=background)

    async def dispatch(self, operation, body):
        from pathlib import Path
        from raccoon.application.conversations.scheduled_main import ScheduledTurn
        from raccoon.modules.scheduling.store import require_supported_execution
        if operation not in {'scheduled', 'scheduled_answer'}:
            raise ValueError('不支持的定时操作')
        run = self.store.get_run(body['run_id'])
        task = self.store.get(run['task_id'])
        require_supported_execution(task)
        if self.is_main_task(task) or task['channel'] != 'web':
            raise ValueError('定时任务不属于项目宿主')
        web = self.workbench
        project = Path(task['project']).resolve()
        identity = web._workspace_id(project)
        web.require_active_project(identity)
        root = web.sidebar.catalog(identity).root.resolve()
        if root != Path(task['data_root']).resolve():
            raise ValueError('定时任务的数据目录与目标项目不一致')
        owner = await web.prepare_session(identity, run['session_id'])
        turn = ScheduledTurn(root, run['session_id'], web.conversations, owner.runtime, store=self.store, now=self.now)
        result = await turn.dispatch(operation, body)
        owner.publish({'kind': 'snapshot', 'state': owner.snapshot()})
        return result

    async def stop_intake(self):
        if self.endpoint is not None:
            await self.endpoint.stop()
            self.endpoint.cleanup(generation=self.generation, handoff=False)
            self.endpoint = None
        await super().stop_intake()

    async def close(self):
        await super().close()
        for bridge in self._owned_main_bridges.values():
            await bridge.close()
        self._owned_main_bridges.clear()
