"""Stateless Web auto-routing rules shared by conversation hosts."""

from raccoon.modules.events.models import Event
from raccoon.app.conversations import SessionSnapshot
from raccoon.modules.events.semantic_routing import RoutingMode


def accepts_web_auto_event(event: Event, *, session_id: str) -> bool:
    if event.type != 'user.message' or event.source != 'web' or event.session_id != session_id:
        return False
    if event.payload.get('mode') != 'auto':
        return False
    origin = event.payload.get('origin')
    return isinstance(origin, dict) and origin.get('kind') == 'user'


async def classify_web_auto_event(event: Event, snapshot: SessionSnapshot, *, runner) -> RoutingMode:
    """Classify an eligible event after its host has checked pending input."""
    from raccoon.modules.events.semantic_routing import build_event_router

    router = build_event_router(runner)
    if router is None:
        return 'queue'
    mode = await router.classify(event, snapshot)
    if mode == 'parallel' and event.payload['content'].get('attachments'):
        # Read-only branches currently accept only text.
        return 'queue'
    return mode
