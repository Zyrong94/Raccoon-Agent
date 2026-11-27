"""Display projections invalidate on durable sources and preserve event ordering."""

from types import SimpleNamespace as NS

import pytest

from raccoon.interfaces.web.chat_display import ChatDisplay


@pytest.fixture
def presentation(tmp_path, monkeypatch):
    reads = {'view': 0, 'artifacts': 0}
    log = tmp_path / 'session.jsonl'
    log.write_text('initial', encoding='utf-8')
    artifact = tmp_path / 'report.txt'
    artifact.write_text('report', encoding='utf-8')
    class View(NS):
        @property
        def messages(self):
            reads['view'] += 1
            return [NS(id='message', role='assistant', metadata={},
                       parts=[NS(kind='text', content=log.read_text(encoding='utf-8'))])]

    session = NS(session_id='session', mode='ask', store=NS(sessions_dir=tmp_path), data=View(metadata={}))

    runner = NS(provider=NS(capabilities=NS(supports_vision=True, supports_pdf_input=False)),
                reasoning_effort='high',
                background_manager=None)
    runtime = NS(session=session, chat_runner=runner, model_ref='fake',
                 model_catalog=NS(models={'fake': NS(ref='fake', label='Fake',
                                                  reasoning_effort='high',
                                                  supported_reasoning_efforts=('high',), vision=True, pdf_input=False)}))

    def artifacts(runtime_arg, session_arg):
        assert runtime_arg is runtime and session_arg is session
        reads['artifacts'] += 1
        if not artifact.exists():
            return {}, None
        return {'report': {'path': artifact, 'content': artifact.read_text(encoding='utf-8')}}, 'report'

    def scoped(items, project_id, session_id):
        assert (project_id, session_id) == ('project', 'session')
        return [{'id': key, 'content': item['content']} for key, item in items.items()]

    runtime.__dict__.update(vars(runner))
    runtime.project_path = tmp_path
    result = ChatDisplay.__new__(ChatDisplay)
    result.runtime = runtime
    result.web = NS(default_model=lambda _saved: 'fake',
                    message_model=lambda _model: {'images': True, 'pdf': False})
    result.attachment_store = lambda: None
    result.project_artifacts = artifacts
    result.messages, result.artifacts, result.activity = [], {}, {}
    result.canvas = result._projection_version = result._projected_artifacts = None
    monkeypatch.setattr('raccoon.interfaces.web.chat_display.workspace_id', lambda path: 'project')
    monkeypatch.setattr('raccoon.interfaces.web.chat_display.scoped_artifacts', scoped)
    return result, reads, log, artifact


def snapshot_inputs():
    return {
        'execution': {'busy': True, 'status': 'running', 'cancellable': True,
                      'pending_messages': [], 'queue_paused': False,
                      'pending': {'id': 'question', 'kind': 'ask_user', 'question': 'Continue?'},
                      'error': None, 'run_phase': 'tool', 'parallel_replies': []},
        'navigation': {'execution_ready': True, 'event_seq': 4, 'event_epoch': 'epoch',
                       'navigation_id': 'navigation', 'project': {'id': 'project'},
                       'workspaces': [{'id': 'project'}], 'sidebar': {},
                       'session': {'id': 'session', 'title': 'Chat'}, 'sessions': []},
        'revisions': {'versions': {'items': []}},
    }


def test_refresh_reuses_versions_and_invalidates_log_artifact_and_deleted_file(presentation):
    display, reads, log, artifact = presentation
    display.refresh_history()
    display.refresh_history()
    assert reads == {'view': 1, 'artifacts': 1}
    first_version = display.log_version()
    first_artifacts = display.artifact_version()

    artifact.write_text('externally replaced report', encoding='utf-8')
    assert display.artifact_version() != first_artifacts
    display.refresh_history()
    assert reads['view'] == 1 and reads['artifacts'] == 2
    assert display.artifact_state() == {
        'artifacts': [{'id': 'report', 'content': 'externally replaced report'}], 'canvas': 'report'}

    log.write_text('new external message', encoding='utf-8')
    assert display.log_version() != first_version
    display.refresh_history()
    assert reads['view'] == 2 and reads['artifacts'] == 3
    assert display.messages[0]['content'] == 'new external message'

    artifact.unlink()
    display.refresh_history()
    display.refresh_history()
    assert reads['view'] == 2 and reads['artifacts'] == 4
    assert display.artifact_state() == {'artifacts': [], 'canvas': None}


def test_snapshot_keeps_event_order_and_prior_display_values_without_refreshing(presentation):
    display, reads, log, _ = presentation
    display.refresh_history()
    started = display.tool_event({'id': 'tool', 'kind': 'started', 'arguments': {'path': 'x'}})
    assert started['order'] == 0
    inputs = snapshot_inputs()
    before = display.build_snapshot(**inputs)

    log.write_text('must wait for explicit refresh', encoding='utf-8')
    assert display.tool_event({'id': 'tool', 'kind': 'completed', 'ok': True})['order'] == 0
    after = display.build_snapshot(**inputs)
    assert reads == {'view': 1, 'artifacts': 1}
    assert before['activity'][0]['kind'] == 'started'
    assert after['activity'][0]['kind'] == 'completed'
    assert 'surfaces' not in after and 'weather' not in after
    assert after['messages'] == display.messages
    assert after['pending']['question'] == 'Continue?'
    assert 'pending_user' not in after and 'command_running' not in after
    assert after['input_capabilities'] == {'images': True, 'pdf': False}
    assert after['effort'] == 'high' and after['model'] == 'fake'
    assert after['models'] == [{'ref': 'fake', 'label': 'Fake', 'effort': 'high',
                                'supported_reasoning_efforts': ['high'],
                                'input_capabilities': {'images': True, 'pdf': False}}]
    assert after['versions'] == inputs['revisions']['versions']

    inputs['execution']['busy'] = False
    assert display.build_snapshot(**inputs)['messages'] == display.messages
    display.activity.clear()
    assert display.build_snapshot(**inputs)['live'] == []
    assert display.build_snapshot(**inputs)['activity'] == []
    assert len(before['activity']) == 1

