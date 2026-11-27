"""Personal host delivery projection, reading the existing execution owner."""
from raccoon.application.delivery.messages import display_messages, running_tool_call_ids
from raccoon.application.delivery.permissions import pending_snapshot

def main_host_snapshot(self, events):
    active = self.application.busy(self.key)
    pending = self.application.pending_inputs.get(self.session_id)
    last = events[-1] if events else None
    error = last.error if last is not None and last.status == 'failed' else None
    session = self.bundle.session
    view = session.data
    capabilities = getattr(self.bundle.provider, 'capabilities', None)
    catalog = getattr(self.bundle, 'model_catalog', None)
    provider = self.bundle.provider
    try:
        parallel_replies = self.parallel.replies()
    except Exception:
        parallel_replies = []
    return {'available': True, 'session': {'id': self.session_id, 'title': '主会话'},
            'session_id': self.session_id, 'generation': self.generation,
            'messages': display_messages(view, attachments=self.attachment_store(),
                ui_event_ids=set()),
            'live': self.live, 'activity': list(self.activity.values()),
            'run_phase': self.run_phase,
            'running_tool_call_ids': running_tool_call_ids(self.bundle, self.session_id),
            'busy': bool(active), 'cancellable': bool(active) and self.status != 'stopping', 'pending': pending_snapshot(pending),
            'pending_messages': self.application.pending_messages(self.key),
            'input_capabilities': {'images': bool(getattr(capabilities, 'supports_vision', False)),
                                   'pdf': bool(getattr(capabilities, 'supports_pdf_input', False))},
            'model': getattr(self.bundle, 'model_ref', f'{provider.name}/{provider.model}'),
            'models': [{'ref': profile.ref, 'label': profile.label, 'effort': profile.reasoning_effort, 'supported_reasoning_efforts': list(profile.supported_reasoning_efforts)} for profile in catalog.models.values()] if catalog else [],
            'effort': self.bundle.reasoning_effort,
            'permission_mode': self.bundle.session.mode,
            'status': 'stopping' if self.status == 'stopping' else 'error' if error else 'waiting' if pending else 'running' if active else self.status,
            'error': error,
            'parallel_replies': parallel_replies}
