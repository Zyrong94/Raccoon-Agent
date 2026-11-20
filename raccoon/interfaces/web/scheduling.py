"""Local same-origin schedule API; project and delivery bindings come from the host."""

from typing import Literal
from pathlib import Path

from fastapi import HTTPException, Request
from raccoon.interfaces.web.project_requests import ProjectWriteBody, submit_once
from pydantic import BaseModel, ConfigDict, Field

from raccoon.modules.scheduling.store import ScheduleConflict
from raccoon.application.workspaces.catalog import main_session_id


class CreateTask(ProjectWriteBody):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=20000)
    kind: Literal['once','interval','cron']
    spec: dict
    timezone: str = Field(min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    permission_mode: Literal['ask','auto','bypass'] | None = None
    request_id: str | None = Field(default=None, max_length=200)


class Revision(BaseModel):
    revision: int = Field(ge=1, strict=True)


class UpdateTask(Revision):
    model_config = ConfigDict(extra='forbid')
    title: str | None = Field(default=None, min_length=1, max_length=160)
    prompt: str | None = Field(default=None, min_length=1, max_length=20000)
    kind: Literal['once','interval','cron'] | None = None
    spec: dict | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    permission_mode: Literal['ask','auto','bypass'] | None = None


class Answer(BaseModel):
    token: str = Field(min_length=1, max_length=100)
    request_id: str = Field(min_length=1, max_length=200)
    answer: str = Field(min_length=1, max_length=20000)


def install_routes(app, workbench, service):
    store = service.store

    @app.exception_handler(ScheduleConflict)
    async def conflict(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail': str(exc)}, status_code=409)

    def task_for(identity):
        try:
            task = store.get(identity)
            if task['status'] == 'deleted':
                raise KeyError(identity)
            return task
        except KeyError:
            raise HTTPException(404, '此本机宿主没有该定时任务') from None

    def project_source(task):
        identity = workbench._workspace_id(Path(task['project']))
        item = workbench.workspace.projects.get(identity)
        if item and not item.get('removed') and Path(task['data_root']).resolve() == workbench.sidebar.catalog(identity).root.resolve():
            return {'kind': 'project', 'project_id': identity, 'label': item['name']}
        return None

    def task_view(task):
        main = service.is_main_task(task) and Path(task['data_root']).resolve() == service.root.resolve()
        project = project_source(task)
        source = {'kind': 'main', 'label': '主会话（QQ）' if task['channel'] == 'qq' else '主会话（Web）'} if main or task['channel'] == 'qq' else project
        return {**task, 'source': source or {'kind': 'project', 'label': Path(task['project']).name},
                'execution': 'main' if main else 'project' if project and task['channel'] == 'web' else 'isolated'}

    def run_view(task, run):
        try:
            current_main = main_session_id(service.root)
        except (OSError, ValueError, TypeError):
            current_main = None
        main = (service.is_main_task(task) and run['session_id'] == task['origin_session']
                and Path(task['data_root']).resolve() == service.root.resolve()
                and Path(run.get('execution_data_root') or task['data_root']).resolve() == Path(task['data_root']).resolve())
        project = project_source(task)
        execution = 'main' if main else 'project' if project and task['channel'] == 'web' else 'isolated'
        return {**run, 'execution': execution,
                'can_open_session': current_main == task['origin_session'] if main else execution == 'project'}

    def run_for(identity, run_id):
        task_for(identity)
        row = store._connection.execute('SELECT task_id FROM schedule_runs WHERE id=?', (run_id,)).fetchone()
        if row is None or row['task_id'] != identity:
            raise HTTPException(404, '执行不存在')

    @app.get('/api/scheduled-tasks')
    async def tasks():
        items = store.list()
        return {'tasks': [{**task_view(t), 'latest': next((run_view(t, run) for run in store.runs(t['id'], limit=1)), None)} for t in items],
                'host': {'owner': service.is_owner, 'error': service.error,
                         'message': '定时宿主运行中' if service.is_owner else '由其他本地宿主调度；此页面可继续管理'}}

    @app.post('/api/scheduled-tasks', status_code=201)
    async def create(request: Request, body: CreateTask):
        async def execute():
            owner = await workbench.prepare_session(body.project_id, body.session_id)
            runtime = owner.runtime
            if body.model is not None and body.model not in runtime.model_catalog.models:
                raise HTTPException(400, '模型不在可选目录中')
            task = store.create(**body.model_dump(exclude={'project_id','session_id','submission_id'}), project=str(runtime.project_path.resolve()),
                data_root=runtime.catalog.root, origin_session=runtime.session.session_id, channel='web', target='')
            return task_view(task)

        return await submit_once(request, body, execute)

    @app.patch('/api/scheduled-tasks/{identity}')
    async def update(identity: str, body: UpdateTask):
        task_for(identity)
        if body.model is not None and body.model not in workbench.model_catalog.models:
            raise HTTPException(400, '模型不在可选目录中')
        return task_view(store.update(identity, body.model_dump(exclude_unset=True, exclude={'revision'}), revision=body.revision))

    @app.post('/api/scheduled-tasks/{identity}/run', status_code=202)
    async def run(identity: str):
        task_for(identity)
        return store.trigger(identity)

    @app.post('/api/scheduled-tasks/{identity}/{action}')
    async def change(identity: str, action: Literal['pause','resume','delete'], body: Revision):
        task_for(identity)
        return task_view(store.change(identity, action, revision=body.revision))

    @app.get('/api/scheduled-tasks/{identity}/runs')
    async def runs(identity: str, limit: int = 50):
        task = task_for(identity)
        return [run_view(task, run) for run in store.runs(identity, limit=limit)]

    @app.post('/api/scheduled-tasks/{identity}/runs/{run_id}/answer')
    async def answer(identity: str, run_id: str, body: Answer):
        run_for(identity, run_id)
        await service.answer(run_id, **body.model_dump())
        return {'accepted': True}
