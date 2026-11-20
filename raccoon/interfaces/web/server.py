"""Same-origin local HTTP API for the Raccoon Next.js workbench."""

from __future__ import annotations

from raccoon.interfaces.web import conversation_actions as actions


from raccoon.modules.permissions.types import PermissionMode

import asyncio
import json
import secrets
import sqlite3
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from raccoon.modules.sessions.errors import SessionError
from raccoon.application.delivery.artifacts import MEDIA_MIME_TYPES, OFFICE_MIME_TYPES, session_artifacts, validate_artifact_path
from raccoon.interfaces.web.canvas import CANVAS_ASSET_TYPES, canvas_asset_path
from raccoon.application.workspaces.store import WorkspaceConflict as WorkbenchConflict
from raccoon.interfaces.web.attachments import MAX_FILE_BYTES, WebAttachments, validate_upload, download_disposition
from raccoon.application.conversations.message_revisions import MessageRevisionConflict, switch_message_version
from raccoon.interfaces.web.directory_picker import install_directory_picker_routes
from raccoon.interfaces.web.project_archives import install_project_archive_routes
from raccoon.interfaces.web.configuration import install_configuration_routes
from raccoon.interfaces.web.subagents import install_subagent_routes
from raccoon.application.conversations.main_host import MainHostConflict, MainHostUnavailable
from raccoon.interfaces.web.group_sessions import list_group_sessions, group_session_snapshot


from raccoon.interfaces.web.project_requests import ProjectWriteBody, submit_once


class MessageBody(ProjectWriteBody):
    mode: Literal['steer', 'follow_up', 'auto'] = 'steer'
    event_id: str | None = Field(default=None, min_length=1, max_length=200)
    message: str = Field(max_length=100000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=16)
    model_ref: str | None = Field(default=None, min_length=1, max_length=200)
    permission_mode: Literal['ask', 'auto', 'bypass'] | None = None


class InputBody(ProjectWriteBody):
    request_id: str = Field(min_length=1, max_length=200)
    answer: str = Field(min_length=1, max_length=100000)


class MainIdentityBody(BaseModel):
    session_id: str = Field(min_length=1, max_length=200)
    generation: str = Field(min_length=1, max_length=200)


class MessageRevisionIdentity(ProjectWriteBody):
    history_revision: str = Field(min_length=1, max_length=200)


class MessageRegenerateBody(MessageRevisionIdentity):
    message_id: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=1, max_length=200)
    model_ref: str | None = Field(default=None, min_length=1, max_length=200)
    permission_mode: Literal['ask', 'auto', 'bypass'] | None = None


class MessageEditBody(MessageRegenerateBody):
    message: str = Field(max_length=100000)


class MessageVersionBody(MessageRevisionIdentity):
    target_session_id: str = Field(min_length=1, max_length=200)


class MainMessageBody(MainIdentityBody):
    mode: Literal['steer', 'follow_up', 'auto'] = 'steer'
    message: str = Field(max_length=100000)
    message_id: str = Field(min_length=1, max_length=200)
    attachment_ids: list[str] = Field(default_factory=list, max_length=16)


class MainInputBody(MainIdentityBody):
    request_id: str = Field(min_length=1, max_length=200)
    answer: str = Field(min_length=1, max_length=100000)


class MainModelBody(MainIdentityBody):
    model: str = Field(min_length=1, max_length=200)


class MainEffortBody(MainIdentityBody):
    effort: str | None = Field(default=None, max_length=80)


class MainModeBody(MainIdentityBody):
    mode: PermissionMode


class ModelBody(ProjectWriteBody):
    model: str = Field(min_length=1, max_length=200)


class PermissionModeBody(ProjectWriteBody):
    mode: PermissionMode


class EffortBody(ProjectWriteBody):
    effort: str | None = Field(default=None, max_length=80)


class SessionUpdateBody(ProjectWriteBody):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    pinned: bool | None = None
    archived: bool | None = None
    deleted: bool | None = None


class NavigationBody(BaseModel):
    navigation_id: str | None = Field(default=None, min_length=1, max_length=128)


class WorkspaceBody(NavigationBody):
    path: str = Field(min_length=1, max_length=4096)
    new_chat: bool = False


class SidebarNewBody(NavigationBody):
    project_id: str | None = Field(default=None, min_length=1, max_length=64)
    draft_only: bool = False


class SidebarUpdateBody(BaseModel):
    project_id: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    pinned: bool | None = None
    archived: bool | None = None


class DirectoryBody(BaseModel):
    path: str = Field(default="", max_length=4096)


def create_web_app(main_data_root: Path, *, workbench, main_bridge, scheduling, initial_projectless=False) -> FastAPI:
    from raccoon.interfaces.web.scheduling import install_routes
    token = secrets.token_urlsafe(32)


    @asynccontextmanager
    async def lifespan(app):
        workbench.loop = asyncio.get_running_loop()
        await workbench.start()
        if initial_projectless and workbench.sidebar.is_new():
            await workbench.navigate('sidebar_new')
        await scheduling.start()
        try:
            yield
        finally:
            await app.state.directory_picker.close()
            await scheduling.stop_intake()
            try:
                await main_bridge.close()
                await workbench.close()
            finally:
                await scheduling.close()

    app = FastAPI(title="Raccoon", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.project_receipts = {}
    app.state.workbench = workbench
    app.state.main_bridge = main_bridge
    app.state.scheduling = scheduling
    install_routes(app, workbench, scheduling)
    install_directory_picker_routes(app, workbench)
    install_project_archive_routes(app, workbench)
    install_configuration_routes(app, workbench)
    install_subagent_routes(app, workbench, main_bridge)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.url.hostname
        origin = request.headers.get("origin")
        allowed = host in {"127.0.0.1", "localhost", "::1"}
        artifact_resource = bool(re.match(r'^/api/projects/[a-f0-9]{16}/sessions/[A-Za-z0-9_-]+/artifacts/', request.url.path))
        canvas_resource = request.method in {"GET", "HEAD"} and artifact_resource and "/canvas/" in request.url.path
        if origin and not (origin == "null" and canvas_resource):
            parsed = urlsplit(origin)
            allowed = allowed and parsed.scheme == request.url.scheme and parsed.netloc == request.url.netloc
        if not allowed:
            return JSONResponse({"detail": "仅允许本机同源访问"}, status_code=403)
        if request.method not in {"GET", "HEAD"} and not secrets.compare_digest(request.headers.get("x-raccoon-token", ""), token):
            return JSONResponse({"detail": "无效工作台令牌，请刷新页面"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not artifact_resource:
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        # API responses and the backend entry must not outlive the current process.
        if request.url.path.startswith("/api/") or request.url.path in {"/", "/index.html"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(WorkbenchConflict)
    @app.exception_handler(MessageRevisionConflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(SessionError)
    async def session_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/api/state")
    async def state(request: Request):
        workbench.loop = asyncio.get_running_loop()
        return {**workbench.selected_snapshot(), "token": token}


    @app.get('/api/session-view')
    async def session_view(project_id: str, session_id: str | None = None, conversation_id: str | None = None):
        if session_id is None and conversation_id is None:
            raise HTTPException(422, '需要会话或聊天标识')
        return workbench.session_view(project_id, session_id=session_id, conversation_id=conversation_id)

    @app.post('/api/execution/prepare')
    async def prepare(request: Request, body: ProjectWriteBody):
        async def execute():
            owner = await workbench.prepare_session(body.project_id, body.session_id)
            return owner.snapshot()

        return await submit_once(request, body, execute)

    @app.get('/api/group-sessions')
    async def group_sessions():
        try:
            return list_group_sessions(main_data_root)
        except (OSError,ValueError,TypeError,sqlite3.Error):
            raise HTTPException(503,'群聊列表暂时无法读取，请稍后重试') from None

    @app.get('/api/group-sessions/{group_key}')
    async def group_session(group_key: str):
        try:
            return group_session_snapshot(main_data_root,group_key)
        except KeyError:
            raise HTTPException(404,'群聊不存在') from None
        except (OSError,ValueError,TypeError,sqlite3.Error):
            raise HTTPException(503,'群聊记录暂时无法读取，请稍后重试') from None

    @app.get('/api/main-session/state')
    async def main_state():
        try:
            return await main_bridge.call('snapshot')
        except (OSError, ValueError, KeyError, TypeError, MainHostUnavailable, RuntimeError):
            raise HTTPException(503, '主会话宿主暂不可用，请检查本机配置或中断执行记录后重新连接。') from None

    async def main_mutation(operation, body):
        try:
            return await main_bridge.call(operation, body.model_dump())
        except MainHostConflict as error:
            raise HTTPException(409, str(error)) from None
        except (MainHostUnavailable, OSError, RuntimeError):
            raise HTTPException(503, '主会话宿主暂不可用；请核对历史后重新连接，不要重复提交结果未知的操作。') from None

    @app.post('/api/main-session/messages', status_code=202)
    async def main_message(body: MainMessageBody):
        return await main_mutation('message', body)

    @app.post('/api/main-session/input', status_code=202)
    async def main_input(body: MainInputBody):
        return await main_mutation('input', body)

    @app.post('/api/main-session/cancel')
    async def main_cancel(body: MainIdentityBody):
        return await main_mutation('cancel', body)

    @app.post('/api/main-session/model')
    async def main_model(body: MainModelBody):
        return await main_mutation('model', body)

    @app.post('/api/main-session/effort')
    async def main_effort(body: MainEffortBody):
        return await main_mutation('effort', body)

    @app.post('/api/main-session/mode')
    async def main_mode(body: MainModeBody):
        return await main_mutation('mode', body)


    @app.post('/api/main-session/attachments', status_code=201)
    async def main_upload(request: Request, session_id: str, generation: str, name: str):
        body = MainIdentityBody(session_id=session_id, generation=generation)
        await main_mutation('attachment_guard', body)
        name, media_type = validate_upload(name, request.headers.get('content-type', 'application/octet-stream'))
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_FILE_BYTES:
                raise HTTPException(413, '单个附件不能超过 20 MiB')
            raw.extend(chunk)
        await main_mutation('attachment_guard', body)
        return WebAttachments(main_data_root, 'personal-main', session_id, personal=True).stage(bytes(raw), name, media_type)

    async def main_attachment_response(identity, session_id, *, download):
        state = await main_state()
        if state['session_id'] != session_id:
            raise HTTPException(409, '主会话已更换，请重新同步')
        try:
            raw, descriptor = WebAttachments(main_data_root, 'personal-main', session_id, personal=True).read(identity)
        except (ValueError, OSError):
            raise HTTPException(404, '附件不存在或不可读取') from None
        inline = descriptor['kind'] == 'image' and not download
        headers = {'Content-Security-Policy': "sandbox; default-src 'none'"}
        if not inline:
            headers['Content-Disposition'] = download_disposition(descriptor['name'])
        return Response(raw, media_type=descriptor['content_type'] if inline else 'application/octet-stream', headers=headers)

    @app.get('/api/main-session/attachments/{identity}/content')
    async def main_attachment_content(identity: str, session_id: str):
        return await main_attachment_response(identity, session_id, download=False)

    @app.get('/api/main-session/attachments/{identity}/download')
    async def main_attachment_download(identity: str, session_id: str):
        return await main_attachment_response(identity, session_id, download=True)


    @app.post("/api/messages", status_code=202)
    async def message(request: Request, body: MessageBody):
        async def execute():
            text = body.message.strip()
            if not text and not body.attachment_ids:
                raise HTTPException(422, "消息不能为空")

            actions.send_chat_message(workbench, text, attachment_ids=body.attachment_ids, project_id=body.project_id,
                       session_id=body.session_id, mode=body.mode, event_id=body.event_id,
                       model_ref=body.model_ref, permission_mode=body.permission_mode)
            return {"accepted": True}

        return await submit_once(request, body, execute)

    @app.post('/api/messages/edit', status_code=202)
    async def edit_message(request: Request, body: MessageEditBody):
        async def execute():
            owner = await workbench.prepare_session(body.project_id, body.session_id)
            return await owner.message_revisions.submit('edit', body.model_dump(exclude={'submission_id'}))

        return await submit_once(request, body, execute)

    @app.post('/api/messages/regenerate', status_code=202)
    async def regenerate_message(request: Request, body: MessageRegenerateBody):
        async def execute():
            owner = await workbench.prepare_session(body.project_id, body.session_id)
            return await owner.message_revisions.submit('regenerate', body.model_dump(exclude={'submission_id'}))

        return await submit_once(request, body, execute)

    @app.post('/api/messages/version')
    async def message_version(request: Request, body: MessageVersionBody):
        async def execute():
            return switch_message_version(workbench, body.project_id, body.session_id,
                                          body.history_revision, body.target_session_id)

        return await submit_once(request, body, execute)

    @app.post("/api/attachments", status_code=201)
    async def upload_attachment(request: Request, project_id: str, session_id: str, name: str):
        workbench.resolve_session(project_id, session_id=session_id)
        workbench.require_active_project(project_id)
        root = workbench.workspace.catalog(project_id).root
        owner = WebAttachments(root, project_id, session_id)
        name, media_type = validate_upload(name, request.headers.get("content-type", "application/octet-stream"))
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_FILE_BYTES:
                raise HTTPException(413, "单个附件不能超过 20 MiB")
            raw.extend(chunk)
        # The request may yield while another browser switches workspaces/sessions.
        workbench.resolve_session(project_id, session_id=session_id)
        workbench.require_active_project(project_id)
        if workbench.workspace.catalog(project_id).root != owner.root:
            raise WorkbenchConflict("会话已切换，请重新选择附件")
        return owner.stage(bytes(raw), name, media_type)

    def attachment_response(identity, project_id, session_id, *, download):
        store = workbench.read_store(project_id, session_id)
        owner = WebAttachments(store.root, project_id, session_id)
        try:
            raw, descriptor = owner.read(identity)
        except (ValueError, OSError):
            raise HTTPException(404, "附件不存在或不可读取") from None
        inline = descriptor["kind"] == "image" and not download
        headers = {"Content-Security-Policy": "sandbox; default-src 'none'"}
        if not inline:
            headers["Content-Disposition"] = download_disposition(descriptor["name"])
        return Response(raw, media_type=descriptor["content_type"] if inline else "application/octet-stream", headers=headers)

    @app.get("/api/attachments/{identity}/content")
    async def attachment_content(identity: str, project_id: str, session_id: str):
        return attachment_response(identity, project_id, session_id, download=False)

    @app.get("/api/attachments/{identity}/download")
    async def attachment_download(identity: str, project_id: str, session_id: str):
        return attachment_response(identity, project_id, session_id, download=True)


    @app.post("/api/input", status_code=202)
    async def answer(request: Request, body: InputBody):
        async def execute():
            workspace = workbench
            workspace.resolve_session(body.project_id, session_id=body.session_id)
            workspace.require_active_project(body.project_id)
            owner = workspace.displays.get((body.project_id, body.session_id))
            if owner is None:
                raise HTTPException(409, '待回答请求已失效')
            owner._ui_session(body.project_id, body.session_id)
            actions.answer_question(owner, body.request_id, body.answer)
            return {"accepted": True}

        return await submit_once(request, body, execute)

    @app.post("/api/cancel")
    async def cancel(request: Request, body: ProjectWriteBody):
        async def execute():
            workspace = workbench
            workspace.resolve_session(body.project_id, session_id=body.session_id)
            workspace.require_active_project(body.project_id)
            if not workspace.conversations.busy(workspace.session_key(body.project_id, body.session_id)):
                raise HTTPException(409, '当前会话没有运行中的任务')
            actions.cancel_session(workspace, body.project_id, body.session_id)
            return {"accepted": True}

        return await submit_once(request, body, execute)

    @app.post("/api/sessions/{session_id}/activate")
    async def activate(session_id: str, body: NavigationBody | None = None):
        state = await workbench.navigate('activate', session_id=session_id,
                                         navigation_id=body.navigation_id if body else None)
        return state if state is not None else workbench.selected_snapshot()

    @app.patch("/api/sessions/{session_id}")
    async def update_session(request: Request, session_id: str, body: SessionUpdateBody):
        async def execute():
            if session_id != body.session_id:
                raise HTTPException(409, '请求与目标会话不一致')
            changes = body.model_dump(exclude_none=True, exclude={'project_id', 'session_id', 'submission_id'})
            return workbench.sidebar_update(body.project_id, session_id, changes)

        return await submit_once(request, body, execute)

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(request: Request, session_id: str, body: ProjectWriteBody):
        async def execute():
            if session_id != body.session_id:
                raise HTTPException(409, '请求与目标会话不一致')
            return await workbench.delete_session(body.project_id, session_id)

        return await submit_once(request, body, execute)

    @app.post("/api/model")
    async def model(request: Request, body: ModelBody):
        async def execute():
            owner = await workbench.prepare_session(body.project_id, body.session_id)
            return actions.set_model(owner, body.model)

        return await submit_once(request, body, execute)

    @app.post('/api/mode')
    async def permission_mode(request: Request, body: PermissionModeBody):
        async def execute():
            owner = await workbench.prepare_session(body.project_id, body.session_id)
            return actions.set_permission_mode(owner, body.mode)

        return await submit_once(request, body, execute)

    @app.post("/api/effort")
    async def effort(request: Request, body: EffortBody):
        async def execute():
            owner = await workbench.prepare_session(body.project_id, body.session_id)
            actions.set_effort(owner, body.effort)
            return owner.snapshot()

        return await submit_once(request, body, execute)

    @app.post("/api/workspaces")
    async def add_workspace(body: WorkspaceBody):
        state = await workbench.navigate('add_workspace', **body.model_dump(exclude={'path'}), directory=body.path)
        return state if state is not None else workbench.selected_snapshot()

    @app.post('/api/sidebar/new')
    async def sidebar_new(body: SidebarNewBody):
        state = await workbench.navigate('sidebar_new', **body.model_dump())
        return state if state is not None else workbench.selected_snapshot()

    @app.patch('/api/sidebar/conversations/{conversation_id}')
    async def sidebar_update(conversation_id: str, body: SidebarUpdateBody):
        changes = body.model_dump(exclude_none=True, exclude={'project_id'})
        return workbench.sidebar_update(body.project_id, conversation_id, changes)

    @app.post("/api/directories")
    def directories(body: DirectoryBody):
        path = Path(body.path).expanduser() if body.path else Path(workbench.workspace.projects[workbench.selected_session[0]]['path'])
        if not path.is_absolute():
            raise ValueError("请输入绝对目录路径")
        path = path.resolve()
        try:
            children = sorted((p for p in path.iterdir() if not p.name.startswith(".") and p.is_dir()), key=lambda p: p.name.casefold())
        except OSError as exc:
            raise ValueError("目录不存在或没有读取权限") from exc
        return {"path": str(path), "parent": str(path.parent) if path.parent != path else None,
                "directories": [{"name": p.name, "path": str(p)} for p in children]}

    @app.post("/api/workspaces/{identity}/activate")
    async def activate_workspace(identity: str, body: NavigationBody | None = None):
        state = await workbench.navigate('activate_workspace', identity=identity,
                                         navigation_id=body.navigation_id if body else None)
        return state if state is not None else workbench.selected_snapshot()

    @app.get("/api/events")
    async def events(request: Request):
        async def stream():
            queue = workbench.events.subscribe()
            try:
                headers = getattr(request, 'headers', {})
                query = getattr(request, 'query_params', {})
                cursor = query.get('cursor') or headers.get('last-event-id')
                try:
                    replay = workbench.events.replay_after(int(cursor)) if cursor is not None else None
                except (TypeError, ValueError):
                    replay = None
                if replay is None:
                    if cursor is not None:
                        yield 'data: ' + json.dumps({'kind': 'resync', 'seq': workbench.events.event_seq,
                            'epoch': workbench.events.event_epoch, 'reset_cursor': True}) + '\n\n'
                    state = workbench.selected_snapshot()
                    initial = {'kind': 'snapshot', 'state': state, 'project_id': state['project']['id'],
                               'session_id': state['session']['id'], 'seq': workbench.events.event_seq,
                               'epoch': workbench.events.event_epoch}
                    yield "data: " + json.dumps(initial, ensure_ascii=False) + "\n\n"
                else:
                    for event in replay:
                        yield 'id: ' + str(event['seq']) + '\ndata: ' + json.dumps(event, ensure_ascii=False) + '\n\n'
                    yield 'data: ' + json.dumps({'kind': 'ready', 'epoch': workbench.events.event_epoch}) + '\n\n'
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                        yield 'id: ' + str(event['seq']) + '\ndata: ' + json.dumps(event, ensure_ascii=False) + '\n\n'
                    except TimeoutError:
                        yield ": heartbeat\n\n"
            finally:
                workbench.events.unsubscribe(queue)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    def artifact_scope(project_id, session_id):
        store = workbench.read_store(project_id, session_id)
        project = Path(workbench.workspace.projects[project_id]['path'])
        artifacts = session_artifacts(store.list_events(session_id), session_id=session_id, project=project, reports=store.root / 'reports')
        return project, store.root, artifacts

    def artifact_path(identity, project_id, session_id):
        project, root, artifacts = artifact_scope(project_id, session_id)
        entry = artifacts.get(identity)
        if entry is None:
            raise HTTPException(404, "当前会话没有这个产物")
        try:
            path = validate_artifact_path(entry["path"], project=project, reports=root / "reports")
        except (ValueError, OSError):
            raise HTTPException(404, "产物不存在或不可读取")
        return entry, path

    @app.get('/api/projects/{project_id}/sessions/{session_id}/artifacts/{identity}/content')
    async def preview(project_id: str, session_id: str, identity: str):
        entry, path = artifact_path(identity, project_id, session_id)
        headers = {"Content-Security-Policy": "sandbox allow-scripts; default-src 'self' https: data: blob: 'unsafe-inline'; connect-src https:; frame-ancestors 'self'"}
        suffix = path.suffix.lower()
        if suffix in OFFICE_MIME_TYPES:
            raise HTTPException(415, "暂不支持预览此文件，请下载后查看")
        if suffix in MEDIA_MIME_TYPES or suffix in {".html", ".htm", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            return FileResponse(path, media_type=entry["mime"], headers=headers)
        if path.stat().st_size > 2 * 1024 * 1024:
            raise HTTPException(413, "文件较大，请下载后查看")
        return Response(path.read_text(encoding="utf-8", errors="replace"), media_type="text/plain", headers=headers)

    @app.get('/api/projects/{project_id}/sessions/{session_id}/artifacts/{identity}/download')
    async def download(project_id: str, session_id: str, identity: str):
        _, path = artifact_path(identity, project_id, session_id)
        return FileResponse(path, media_type="application/octet-stream", filename=path.name,
                            headers={"Content-Security-Policy": "sandbox"})

    @app.get('/api/projects/{project_id}/sessions/{session_id}/artifacts/{identity}/canvas/{asset_path:path}')
    async def canvas_asset(project_id: str, session_id: str, identity: str, asset_path: str):
        _, entry = artifact_path(identity, project_id, session_id)
        if entry.suffix.lower() not in {".html", ".htm"}:
            raise HTTPException(404, "产物不是 HTML 页面")
        try:
            project, root, _ = artifact_scope(project_id, session_id)
            path = canvas_asset_path(entry, asset_path, project=project, reports=root / "reports")
        except (OSError, ValueError):
            raise HTTPException(404, "Canvas 资源不存在或不可读取")
        return FileResponse(path, media_type=CANVAS_ASSET_TYPES[path.suffix.lower()], headers={
            "Content-Security-Policy": "sandbox allow-scripts; default-src 'self' https: data: blob: 'unsafe-inline'; "
                                       "connect-src 'self' https:; object-src 'none'; base-uri 'none'; "
                                       "frame-src 'none'; frame-ancestors 'self'",
            # Opaque sandbox origins need anonymous CORS for ES modules, fonts and fetches of bundle data.
            "Access-Control-Allow-Origin": "*",
        })

    @app.get('/api/projects/{project_id}/sessions/{session_id}/artifacts/{identity}/pdf-page/{number}')
    def pdf_page(project_id: str, session_id: str, identity: str, number: int):
        from raccoon.interfaces.web.pdf import render_pdf_page
        import pypdfium2 as pdfium

        _, path = artifact_path(identity, project_id, session_id)
        if path.suffix.lower() != ".pdf":
            raise HTTPException(404, "文件不是 PDF")
        if path.stat().st_size > 50 * 1024 * 1024:
            raise HTTPException(413, "PDF 较大，请下载后查看")
        try:
            content, count = render_pdf_page(path, number)
        except IndexError:
            raise HTTPException(404, "PDF 页码不存在")
        except (pdfium.PdfiumError, ValueError):
            raise HTTPException(422, "PDF 无法预览，可能已加密或文件损坏，请下载后查看")
        return Response(content, media_type="image/png", headers={"X-PDF-Pages": str(count)})

    @app.get("/")
    async def backend_info():
        return {"name": "Raccoon", "detail": "这是 Python API 服务；请从 web-next 启动网页工作台。"}
    return app
