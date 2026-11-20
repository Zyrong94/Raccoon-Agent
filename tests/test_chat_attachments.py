"""Session storage and injected delivery contracts, without a model or HTTP server."""

from dataclasses import asdict, replace
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from raccoon.application.delivery.attachments import ChatAttachments
from raccoon.modules.sessions.models import MessagePart
from raccoon.modules.sessions.attachments import SessionAttachmentStore
from raccoon.app.payloads import AttachmentRef


def test_store_reads_legacy_manifest_and_returns_verified_references_without_urls(tmp_path):
    identity, raw = 'a' * 32, b'previously saved attachment'
    digest = sha256(raw).hexdigest()
    directory = tmp_path / 'attachments' / 'session'
    directory.mkdir(parents=True)
    path = directory / f'{digest[:16]}-legacy.txt'
    path.write_bytes(raw)
    ref = AttachmentRef('file', path.relative_to(tmp_path).as_posix(), 'legacy.txt',
                           'text/plain', len(raw), digest, 'web:' + identity)
    (directory / 'web-manifest.json').write_text(json.dumps({identity: asdict(ref)}), encoding='utf-8')

    store = SessionAttachmentStore(tmp_path, 'session')
    assert store.read(identity) == (raw, ref)
    assert store.staged_refs([identity]) == (ref,)
    new_identity, new_ref = store.stage(b'new upload', 'new.txt', 'text/plain')
    assert isinstance(new_ref, AttachmentRef)
    assert new_ref.source == 'web:' + new_identity
    assert (directory / 'web-manifest.lock').is_file()
    reopened = SessionAttachmentStore(tmp_path, 'session')
    assert reopened.read(identity) == (raw, ref)
    assert reopened.read(new_identity) == (b'new upload', new_ref)
    saved = json.loads((directory / 'web-manifest.json').read_text(encoding='utf-8'))
    assert saved == {identity: asdict(ref), new_identity: asdict(new_ref)}
    assert not hasattr(store, 'project_id') and not hasattr(store, 'personal')


def test_chat_attachments_uses_injected_urls_for_staged_queued_and_historical_files(tmp_path):
    store = SessionAttachmentStore(tmp_path, 'session')
    identities = []
    def urls(identity):
        identities.append(identity)
        return {'content_url': 'custom-content:' + identity, 'download_url': 'custom-download:' + identity}
    attachments = ChatAttachments(store, urls=urls)
    item = attachments.stage(b'note', 'note.txt', 'text/plain')
    identity = item['id']
    assert item == {'id': identity, 'kind': 'file', 'name': 'note.txt', 'content_type': 'text/plain', 'size': 4,
                    'content_url': 'custom-content:' + identity, 'download_url': 'custom-download:' + identity}
    ref, = store.staged_refs([identity])
    part = MessagePart('part', 'message', 'file', '', metadata={
        'path': ref.relative_path, 'sha256': ref.sha256, 'filename': ref.filename, 'source': ref.source})
    assert attachments.descriptor(identity, ref) == item
    assert attachments.read(identity) == (b'note', item)
    assert attachments.for_parts([part]) == [item]
    assert attachments.for_queued((ref,)) == [item]
    assert identities == [identity] * 5
    foreign = replace(ref, source='web:' + 'f' * 32)
    part.metadata['source'] = foreign.source
    assert attachments.for_parts([part]) == []
    assert attachments.for_queued((foreign,)) == []
    assert identities == [identity] * 5


@pytest.mark.parametrize('foreign', ['root', 'session'])
def test_personal_host_rejects_foreign_attachment_store_before_queue_side_effects(tmp_path, foreign):
    from raccoon.bootstrap.main_host import create_personal_chat_host as PersonalChatHost

    store = SessionAttachmentStore(tmp_path / 'other' if foreign == 'root' else tmp_path,
                                   'other' if foreign == 'session' else 'session')
    attachments = ChatAttachments(store, urls=lambda identity: {})
    def forbidden(session_id):
        pytest.fail('a rejected attachment owner must not mutate the queue')
    queue = SimpleNamespace(cancel_abandoned_web=forbidden)
    with pytest.raises(ValueError, match='附件存储不属于当前主会话'):
        PersonalChatHost(tmp_path, 'session', queue, None, chat_attachments=attachments)
