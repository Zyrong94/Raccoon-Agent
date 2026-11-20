"""Explicit parent-scoped Subagent controls for Web and local channels."""
from raccoon.modules.collaboration.coordination import send_subagent, stop_subagent


async def control_subagent(runtime, subagent_id, *, stop=False, message=None, mode='follow_up', event_id=None):
    if stop:
        return await stop_subagent(runtime, subagent_id)
    if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 200:
        raise ValueError('投递 ID 无效')
    return await send_subagent(runtime, subagent_id, message, mode=mode, event_id=event_id, wait_for_completion=False)
