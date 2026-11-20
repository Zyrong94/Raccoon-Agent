"""Target and in-process receipts for writes to a project session."""
import asyncio
import json

from fastapi import HTTPException
from pydantic import BaseModel, Field


class ProjectWriteBody(BaseModel):
    project_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=200)
    submission_id: str = Field(min_length=1, max_length=200)


async def submit_once(request, body, execute):
    """Share one write result across concurrent submissions with the same ID."""
    key = (body.project_id, body.session_id, body.submission_id)
    fingerprint = (request.method, request.url.path, json.dumps(body.model_dump(), sort_keys=True))
    receipts = request.app.state.project_receipts
    if key in receipts:
        previous, task = receipts[key]
        if previous != fingerprint:
            raise HTTPException(409, '此请求标识已用于另一项操作')
    else:
        if len(receipts) >= 256:
            for identity, (_, previous_task) in receipts.items():
                if previous_task.done():
                    del receipts[identity]
                    break
            else:
                raise HTTPException(429, '正在处理的请求过多，请稍后核对状态')
        task = asyncio.create_task(execute())
        receipts[key] = (fingerprint, task)
        # The mutation keeps running if the HTTP client disconnects.
        task.add_done_callback(consume_exception)
    return await asyncio.shield(task)


def consume_exception(task):
    if not task.cancelled():
        task.exception()
