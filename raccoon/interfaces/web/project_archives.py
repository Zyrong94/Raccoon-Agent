"""Project archive HTTP models and routes."""
from pydantic import BaseModel, Field


class ArchiveProjectBody(BaseModel):
    archived: bool = Field(strict=True)


class RestoreArchiveBody(BaseModel):
    storage_project_id: str = Field(min_length=1, max_length=64)
    conversation_id: str = Field(min_length=1, max_length=200)


def install_project_archive_routes(app, workbench):
    workspace = workbench

    @app.patch('/api/workspaces/{identity}')
    async def update_project(identity: str, body: ArchiveProjectBody):
        workspace.update_workspace(identity, archived=body.archived)
        return workspace.selected_snapshot()

    @app.delete('/api/workspaces/{identity}')
    async def remove_project(identity: str):
        await workspace.remove_workspace(identity)
        return workspace.selected_snapshot()

    @app.get('/api/archive')
    async def archives():
        return workspace.project_archives.snapshot()

    @app.post('/api/archive/restore')
    async def restore_chat(body: RestoreArchiveBody):
        return workspace.restore_archived_chat(body.storage_project_id, body.conversation_id)
