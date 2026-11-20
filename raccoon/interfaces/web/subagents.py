"""HTTP Subagent identity validation and routes."""
from typing import Literal
from fastapi import HTTPException, Request
from raccoon.interfaces.web.project_requests import ProjectWriteBody, submit_once
from pydantic import BaseModel, Field
from raccoon.application.conversations.subagents import control_subagent
from raccoon.application.delivery.subagents import stored_subagent_snapshot

class MainIdentity(BaseModel):
    session_id: str = Field(min_length=1, max_length=200)
    generation: str = Field(min_length=1, max_length=200)


class MessageFields(BaseModel):
    message: str = Field(min_length=1, max_length=100000)
    mode: Literal['follow_up', 'steer'] = 'follow_up'
    event_id: str = Field(min_length=1, max_length=200)


class ProjectMessage(ProjectWriteBody, MessageFields):
    pass


class MainMessage(MainIdentity, MessageFields):
    pass


def install_subagent_routes(app, workbench, main_bridge):
    from raccoon.application.conversations.main_host import MainHostConflict, MainHostUnavailable

    async def main_call(operation, body):
        try:
            return await main_bridge.call(operation, body)
        except MainHostConflict as error:
            raise HTTPException(409, str(error)) from None
        except (MainHostUnavailable, OSError, RuntimeError):
            raise HTTPException(503, '主会话宿主暂不可用；请重新同步，勿重复提交结果未知的操作。') from None

    @app.get('/api/subagents')
    async def subagents(project_id: str, session_id: str):
        store = workbench.read_store(project_id, session_id)
        return stored_subagent_snapshot(store, session_id, conversations=workbench.conversations)

    @app.get('/api/subagents/{subagent_id}')
    async def detail(subagent_id: str, project_id: str, session_id: str):
        store = workbench.read_store(project_id, session_id)
        return stored_subagent_snapshot(store, session_id, conversations=workbench.conversations, subagent_id=subagent_id)

    @app.post('/api/subagents/{subagent_id}/messages', status_code=202)
    async def send(request: Request, subagent_id: str, body: ProjectMessage):
        async def execute():
            owner = await workbench.prepare_session(body.project_id, body.session_id)
            runner = owner.runtime
            return await control_subagent(runner, subagent_id, **body.model_dump(exclude={'project_id', 'session_id', 'submission_id'}))

        return await submit_once(request, body, execute)

    @app.post('/api/subagents/{subagent_id}/stop')
    async def stop(request: Request, subagent_id: str, body: ProjectWriteBody):
        async def execute():
            owner = await workbench.prepare_session(body.project_id, body.session_id)
            return await control_subagent(owner.runtime, subagent_id, stop=True)

        return await submit_once(request, body, execute)

    @app.get('/api/main-session/subagents')
    async def main_subagents(session_id: str, generation: str):
        return await main_call('subagents_list', MainIdentity(session_id=session_id, generation=generation).model_dump())

    @app.get('/api/main-session/subagents/{subagent_id}')
    async def main_detail(subagent_id: str, session_id: str, generation: str):
        return await main_call('subagents_detail', {**MainIdentity(session_id=session_id, generation=generation).model_dump(), 'subagent_id': subagent_id})

    @app.post('/api/main-session/subagents/{subagent_id}/messages', status_code=202)
    async def main_send(subagent_id: str, body: MainMessage):
        return await main_call('subagents_send', {**body.model_dump(), 'subagent_id': subagent_id})

    @app.post('/api/main-session/subagents/{subagent_id}/stop')
    async def main_stop(subagent_id: str, body: MainIdentity):
        return await main_call('subagents_stop', {**body.model_dump(), 'subagent_id': subagent_id})
