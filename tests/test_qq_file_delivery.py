import asyncio
import hashlib
import json

import httpx
import pytest

from raccoon.interfaces.qq.api import QQApi, QQApiError


CONTENT = '<!doctype html><meta charset="utf-8"><h1>每日 AI 简报</h1>'.encode('utf-8')
UPLOAD_URL = 'https://bucket.cos.ap-tokyo.myqcloud.com/report?sign=private-signature'


def file_at(tmp_path):
    path = tmp_path / 'AI日报.html'
    path.write_bytes(CONTENT)
    return path


def responder(request):
    if request.url.host == 'bots.qq.com':
        return httpx.Response(200, json={'access_token': 'fake-token', 'expires_in': 7200})
    if request.url.path.endswith('/upload_prepare'):
        return httpx.Response(200, json={'upload_id': 'upload-one', 'block_size': '30', 'parts': [
            {'index': 1, 'presigned_url': UPLOAD_URL + '0', 'block_size': '30'},
            {'index': 2, 'presigned_url': UPLOAD_URL + '1', 'block_size': str(len(CONTENT) - 30)}]})
    if request.url.path.endswith('/files'):
        return httpx.Response(200, json={'file_info': 'opaque-file-reference', 'ttl': 300})
    return httpx.Response(200, json={})


def send(path, handler, openid='owner'):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await QQApi('app', 'fake-secret', client).send_proactive_file(openid, path)
    return asyncio.run(scenario())


@pytest.mark.parametrize('first_index', [0, 1])
@pytest.mark.parametrize('part_count', [1, 2])
def test_proactive_file_preserves_html_name_bytes_and_target_without_reply_id(tmp_path, first_index, part_count):
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith('/upload_prepare'):
            blocks = [CONTENT] if part_count == 1 else [CONTENT[:30], CONTENT[30:]]
            return httpx.Response(200, json={'upload_id': 'upload-one', 'block_size': '5242880',
                'parts': [{'index': first_index + index, 'presigned_url': UPLOAD_URL + str(index),
                           'block_size': str(len(block))} for index, block in enumerate(blocks)],
                'upload_config': {'concurrency': 1, 'retry_timeout': 300, 'retry_delay': 1}})
        return responder(request)

    path = file_at(tmp_path)
    send(path, handler, openid='owner/other?')
    api_requests = [r for r in seen if r.url.host == 'api.sgroup.qq.com']
    assert [r.url.raw_path for r in api_requests] == [
        b'/v2/users/owner%2Fother%3F/' + endpoint for endpoint in
        [b'upload_prepare', *([b'upload_part_finish'] * part_count), b'files', b'messages']]
    assert all(r.headers['authorization'] == 'QQBot fake-token' for r in api_requests)
    assert all(r.headers['x-union-appid'] == 'app' for r in api_requests)
    prepare = json.loads(api_requests[0].content)
    assert prepare == {
        'file_type': 4, 'file_size': str(len(CONTENT)), 'file_name': path.name,
        'md5': hashlib.md5(CONTENT).hexdigest(), 'sha1': hashlib.sha1(CONTENT).hexdigest(),
        'md5_10m': hashlib.md5(CONTENT).hexdigest()}
    puts = [r for r in seen if r.method == 'PUT']
    assert b''.join(r.content for r in puts) == CONTENT
    assert all('authorization' not in r.headers and 'x-union-appid' not in r.headers for r in puts)
    for index, request in enumerate(api_requests[1:1 + part_count]):
        block = puts[index].content
        assert json.loads(request.content) == {
            'upload_id': 'upload-one', 'part_index': first_index + index, 'block_size': str(len(block)),
            'md5': hashlib.md5(block).hexdigest()}
    assert json.loads(api_requests[-2].content) == {
        'upload_id': 'upload-one', 'file_type': 4, 'file_name': path.name, 'srv_send_msg': False}
    assert json.loads(api_requests[-1].content) == {
        'msg_type': 7, 'media': {'file_info': 'opaque-file-reference'}}
    assert all('msg_id' not in json.loads(r.content) and 'event_id' not in json.loads(r.content)
               for r in api_requests)


@pytest.mark.parametrize('stage', ['upload_prepare', 'put', 'upload_part_finish', 'files', 'messages'])
def test_failure_stops_delivery_and_redacts_vendor_details(tmp_path, stage):
    seen = []

    def handler(request):
        seen.append(request)
        if (stage == 'put' and request.method == 'PUT') or request.url.path.endswith('/' + stage):
            return httpx.Response(400, json={'code': 850031, 'message': 'private-body fake-secret fake-token'})
        return responder(request)

    with pytest.raises(QQApiError) as caught:
        send(file_at(tmp_path), handler)
    assert caught.value.status == 400
    assert str(caught.value) == 'QQ API HTTP 400, code=' + ('None' if stage == 'put' else '850031')
    if stage != 'messages':
        assert not any(r.url.path.endswith('/messages') for r in seen)


@pytest.mark.parametrize('change', [
    {'upload_id': ''}, {'parts': []},
    {'parts': [{'index': 0, 'presigned_url': 'https://attacker.example/upload', 'block_size': str(len(CONTENT))}]},
    {'parts': [{'index': 0, 'presigned_url': UPLOAD_URL, 'block_size': '0'}]},
    {'parts': [{'index': 2, 'presigned_url': UPLOAD_URL, 'block_size': str(len(CONTENT))}]},
    {'parts': [{'index': True, 'presigned_url': UPLOAD_URL, 'block_size': str(len(CONTENT))}]},
    {'parts': [{'index': 0, 'presigned_url': UPLOAD_URL, 'block_size': '1'}]},
])
def test_invalid_upload_plan_never_sends_file_bytes(tmp_path, change):
    seen = []

    def handler(request):
        seen.append(request)
        response = responder(request)
        if request.url.path.endswith('/upload_prepare'):
            return httpx.Response(200, json={**response.json(), **change})
        return response

    with pytest.raises(QQApiError):
        send(file_at(tmp_path), handler)
    assert not any(r.method == 'PUT' or r.url.path.endswith('/messages') for r in seen)


@pytest.mark.parametrize('indexes', [[1, 1], [0, 0], [1, 3], [0, 2], [1, 0], [0, True]])
def test_duplicate_gapped_or_noninteger_part_indexes_reject_before_upload(tmp_path, indexes):
    seen = []

    def handler(request):
        seen.append(request)
        response = responder(request)
        if request.url.path.endswith('/upload_prepare'):
            payload = response.json()
            for part, index in zip(payload['parts'], indexes):
                part['index'] = index
            return httpx.Response(200, json=payload)
        return response

    with pytest.raises(QQApiError):
        send(file_at(tmp_path), handler)
    assert not any(r.method == 'PUT' or r.url.path.endswith('/messages') for r in seen)


@pytest.mark.parametrize('file_info', [None, '', 123])
def test_missing_file_reference_never_sends_message(tmp_path, file_info):
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith('/files'):
            return httpx.Response(200, json={'file_info': file_info})
        return responder(request)

    with pytest.raises(QQApiError):
        send(file_at(tmp_path), handler)
    assert not any(r.url.path.endswith('/messages') for r in seen)


def test_upload_transport_error_does_not_expose_signed_url(tmp_path):
    def handler(request):
        if request.method == 'PUT':
            raise httpx.ConnectError('private-signature fake-secret', request=request)
        return responder(request)

    with pytest.raises(QQApiError) as caught:
        send(file_at(tmp_path), handler)
    assert str(caught.value) == 'QQ API HTTP 0, code=None'


@pytest.mark.parametrize('size', [0, 5 * 1024 * 1024 + 1])
def test_empty_and_oversized_files_are_rejected_before_network(tmp_path, size):
    path = tmp_path / 'report.html'
    with path.open('wb') as stream:
        stream.truncate(size)

    def handler(request):
        pytest.fail('Invalid file must not trigger network requests')

    with pytest.raises(ValueError, match='nonempty'):
        send(path, handler)


def test_overlong_utf8_filename_is_rejected_before_network(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(Path, 'name', property(lambda path: '日' * 86 + '.html'))
    with pytest.raises(ValueError, match='file name'):
        send(tmp_path / 'report.html', lambda request: pytest.fail('Must not access network'))
