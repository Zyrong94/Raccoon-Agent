"""QQ media downloads use fake HTTP and real image decoding/storage."""

import asyncio
from dataclasses import replace
from io import BytesIO

import httpx
from PIL import Image
import pytest

from raccoon.interfaces.qq.attachments import QQImageInput
from raccoon.modules.sessions.user_attachments import resolve_queued_attachments
from raccoon.interfaces.qq.state import QQState
from raccoon.bootstrap.group_chat import QQGroups
from tests.qq_fixtures import RecordingRuntime, channel, group_packet, packet
from tests.scripted_providers import FakeProvider, response



class VisionProvider(FakeProvider):
    @property
    def capabilities(self):
        return replace(super().capabilities, supports_vision=True)


def sent_images(request):
    images = []
    for message in request.messages:
        if message.role == "user":
            images.extend(part for part in message.content_parts or [] if part.type == "image")
    return images


def png(color='red'):
    out = BytesIO()
    Image.new('RGB', (3, 3), color).save(out, format='PNG')
    return out.getvalue()


def descriptor(url='https://multimedia.nt.qq.com.cn/download?fake=1'):
    return {'content_type': 'image/png', 'url': url, 'filename': 'photo.png'}


def test_multiple_images_are_persistent_session_refs_without_credentials(tmp_path):
    async def scenario():
        requests = []
        def handler(req):
            requests.append(req)
            return httpx.Response(200, content=png(), headers={'Content-Type': 'image/png'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            loader = QQImageInput(client, store_root=tmp_path, session_id='main')
            refs = await loader.prepare([descriptor(), descriptor('//gchat.qpic.cn/a')])
        items = resolve_queued_attachments(refs, store_root=tmp_path, session_id='main')
        assert len(items) == 2
        assert all(item.path.read_bytes() == png() for item in items)
        assert len(requests) == 2
        assert all('Authorization' not in r.headers for r in requests)
        assert not list((tmp_path / 'attachments/main').glob('*.tmp'))
    asyncio.run(scenario())


@pytest.mark.parametrize('url', [
    'http://multimedia.nt.qq.com.cn/a', 'https://127.0.0.1/a', 'https://evil.example/a',
    'https://multimedia.nt.qq.com.cn.evil.example/a', 'https://user:secret@gchat.qpic.cn/a',
    'https://gchat.qpic.cn:8443/a', 'file:///etc/passwd', 'https://gchat.qpic.cn/a#fragment',
])
def test_untrusted_urls_are_rejected_without_network(tmp_path, url):
    async def scenario():
        requests = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: requests.append(r))) as client:
            with pytest.raises(ValueError):
                await QQImageInput(client, store_root=tmp_path, session_id='main').prepare([descriptor(url)])
        assert requests == []
    asyncio.run(scenario())


@pytest.mark.parametrize(('status', 'headers', 'body'), [
    (302, {'Location': 'https://127.0.0.1/secret'}, b''),
    (403, {}, b'expired'), (200, {'Content-Length': str(21 * 1024 * 1024)}, b''),
    (200, {}, b'not an image'), (200, {'Content-Type': 'image/png'}, b'\x89PNG\r\n\x1a\n'),
])
def test_download_failures_never_return_partial_success_or_leak_url(tmp_path, status, headers, body):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(status, headers=headers, content=body),
        )) as client:
            with pytest.raises(ValueError) as error:
                await QQImageInput(client, store_root=tmp_path, session_id='main').prepare([descriptor()])
            assert 'fake=1' not in str(error.value)
    asyncio.run(scenario())


def test_streamed_download_is_bounded_even_without_content_length(tmp_path, monkeypatch):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'a' * 20
            yield b'b' * 20
    async def scenario():
        import raccoon.interfaces.qq.attachments as media
        monkeypatch.setattr(media, 'MAX_IMAGE_BYTES', 30)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Stream()))) as client:
            with pytest.raises(ValueError, match='大小|大|limit'):
                await QQImageInput(client, store_root=tmp_path, session_id='main').prepare([descriptor()])
    asyncio.run(scenario())


def test_group_pure_image_and_multimage_text_reach_vision_and_stay_in_their_group(tmp_path):
    async def scenario():
        state = QQState(tmp_path / 'channels/qq.sqlite3')
        state.set('owner', 'private-owner')
        def factory(**kwargs):
            from raccoon.bootstrap.runtime import create_runtime
            from tests.runtime_fixtures import FakeMcpManager
            bundle = create_runtime(**kwargs, provider=VisionProvider([response('seen'), response('seen again')]),
                                    tools=[], mcp_manager_factory=lambda configs: FakeMcpManager())
            return bundle
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=png()))) as client:
            groups = QQGroups(state, project=tmp_path, data_root=tmp_path, runtime_factory=factory,
                              members='all', mode='bypass', media_client=client)
            try:
                for group in ['a', 'b']:
                    await groups.receive(group_packet('/pair ' + groups.issue_code('private-owner'), group=group, message_id='pair-' + group)['d'])
                    data = group_packet('' if group == 'a' else '看这两张', group=group, message_id='image-' + group)['d']
                    data['attachments'] = [descriptor()] * (1 if group == 'a' else 2)
                    await groups.receive(data)
                contexts = sorted(groups.contexts.values(), key=lambda c: c.group_id)
                for ctx in contexts:
                    key = ctx.runtime.key_for_session(ctx.session_id)
                    await asyncio.wait_for(ctx.runtime.wait(ctx.runtime.history(key)[0]), timeout=5)
                assert len(sent_images(contexts[0].bundle.provider.requests[0])) == 1
                assert len(sent_images(contexts[1].bundle.provider.requests[0])) == 2
                a, b = contexts
                key = a.runtime.key_for_session(a.session_id)
                ref = a.runtime.history(key)[0].event.payload['content']['attachments'][0]
                assert (a.root / ref['relative_path']).exists()
                assert not (b.root / ref['relative_path']).exists()
                assert a.state.pending_outbound()[-1]['openid'] == 'a'
                assert b.state.pending_outbound()[-1]['openid'] == 'b'
            finally:
                await groups.close()
                state.close()
    asyncio.run(scenario())


def test_unauthorized_group_member_never_downloads_images(tmp_path):
    async def scenario():
        state = QQState(tmp_path / 'channels/qq.sqlite3')
        state.set('owner', 'private-owner')
        requests = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: requests.append(r))) as client:
            groups = QQGroups(state, project=tmp_path, data_root=tmp_path, media_client=client, members='owner')
            try:
                await groups.receive(group_packet('/pair ' + groups.issue_code('private-owner'))['d'])
                data = group_packet('look', member='teammate')['d']
                data['attachments'] = [descriptor()]
                await groups.receive(data)
                assert not requests and not groups.contexts
            finally:
                await groups.close()
                state.close()
    asyncio.run(scenario())





def test_private_image_is_rejected_before_download_when_model_has_no_vision(tmp_path):
    async def scenario():
        def unexpected_download(request):
            pytest.fail(f"unexpected download {request.method}")

        transport = httpx.MockTransport(unexpected_download)
        async with httpx.AsyncClient(transport=transport) as client:
            runtime = RecordingRuntime()
            runtime.vision = False
            image_input = QQImageInput(client, store_root=tmp_path, session_id="main")
            bot, state, _ = channel(tmp_path, runtime=runtime, image_input=image_input)
            try:
                state.set("owner", "owner")
                incoming = packet("Look at the image")
                incoming["d"]["attachments"] = [descriptor()]
                await bot.receive(incoming)
                assert not runtime.events
                assert "视觉" in state.pending_outbound()[-1]["content"]
            finally:
                state.close()

    asyncio.run(scenario())
