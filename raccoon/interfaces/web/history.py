"""Web adapters for cold history projections without execution runtimes."""
from raccoon.application.delivery.history import ReadOnlySessionStore, read_store
from raccoon.application.delivery.history import session_snapshot as project_session_snapshot
from raccoon.interfaces.web.artifacts import scoped_artifacts
from raccoon.interfaces.web.attachments import WebAttachments
from raccoon.application.delivery.canvas import session_canvas


def session_snapshot(workbench, project_id, record, records, *, event_context=None):
    if event_context is None:
        event_context = {'event_seq': workbench.events.event_seq, 'event_epoch': workbench.events.event_epoch}
    return project_session_snapshot(workbench, project_id, record, records,
        scoped_artifacts=scoped_artifacts,
        session_canvas=session_canvas, attachments_factory=WebAttachments,
        event_context=event_context)


__all__ = ['ReadOnlySessionStore', 'read_store', 'session_snapshot']
