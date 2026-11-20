
from raccoon.modules.tools.execution import execute_tool
from urllib.error import HTTPError

import pytest

from raccoon.modules.tools import create_builtin_registry


def feed(entries, total=None):
    body = ''.join(f'''<entry><id>http://arxiv.org/abs/{identifier}</id>
        <title>Title {identifier}</title><summary>Full abstract</summary>
        <updated>{updated}</updated><published>2025-01-01T12:00:00Z</published>
        <author><name>Alice</name></author></entry>''' for identifier, updated in entries)
    return f'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:o="http://a9.com/-/spec/opensearch/1.1/">
        <o:totalResults>{len(entries) if total is None else total}</o:totalResults>{body}</feed>'''.encode()


def execute(tmp_path, **kwargs):
    return execute_tool(create_builtin_registry(tmp_path, include_network_tools=True), 
        'search_papers', {'query': 'all:"sparse autoencoder"', 'date_from': '2026-03-01',
                          'date_to': '2026-03-31', **kwargs})


def transport(monkeypatch, pages):
    import raccoon.modules.tools.arxiv as arxiv
    calls = []
    def download(query, offset, size):
        calls.append(offset)
        item = pages[offset]
        if isinstance(item, Exception):
            raise item
        return item
    monkeypatch.setattr(arxiv, '_download', download)
    return calls


def test_latest_submission_eastern_dates_and_dedup(monkeypatch, tmp_path):
    transport(monkeypatch, {0: feed([
        ('2603.00001v3', '2026-04-01T03:00:00Z'),
        ('2603.00001v2', '2026-03-15T12:00:00Z'),
        ('2501.00002v2', '2026-03-04T12:00:00Z'),
        ('2602.00003v1', '2026-03-01T03:00:00Z')])})
    result = execute(tmp_path)
    assert result.ok
    assert [p['id'] for p in result.data['papers']] == ['2603.00001', '2501.00002']
    assert result.data['papers'][0]['date'] == '2026-03-31'
    assert result.data['papers'][0]['version'] == 'v3'
    assert result.data['papers'][1]['abstract'] == 'Full abstract'
    assert result.data['next_offset'] is None


def test_limit_continuation_does_not_skip_unreturned_entries(monkeypatch, tmp_path):
    pages = {0: feed([('2603.00001v1','2026-03-30T12:00:00Z'), ('2603.00002v1','2026-03-29T12:00:00Z')]),
             1: feed([('2603.00002v1','2026-03-29T12:00:00Z')], total=2)}
    calls = transport(monkeypatch, pages)
    first = execute(tmp_path, limit=1)
    assert first.data['next_offset'] == 1
    second = execute(tmp_path, limit=1, offset=first.data['next_offset'])
    assert second.data['papers'][0]['id'] == '2603.00002'
    assert second.data['next_offset'] is None
    assert calls == [0,1]


def test_scan_budget_returns_resume_position(monkeypatch, tmp_path):
    import raccoon.modules.tools.arxiv as arxiv
    monkeypatch.setattr(arxiv, 'PAGE_SIZE', 1)
    calls = transport(monkeypatch, {n: feed([(f'2610.0000{n}v1','2026-10-01T12:00:00Z')],total=100) for n in range(3)})
    result = execute(tmp_path)
    assert result.ok and not result.data['papers']
    assert result.data['next_offset'] == 3
    assert result.data['stop_reason'] == 'scan_budget'
    assert calls == [0,1,2]


@pytest.mark.parametrize('body', [b'<html>blocked</html>', b'not xml', feed([('bad','invalid')])])
def test_invalid_feed_is_failure_not_empty_success(monkeypatch, tmp_path, body):
    transport(monkeypatch, {0: body})
    result = execute(tmp_path)
    assert not result.ok
    assert result.data['next_offset'] == 0


def test_rate_limit_preserves_partial_results_and_retry_offset(monkeypatch, tmp_path):
    import raccoon.modules.tools.arxiv as arxiv
    monkeypatch.setattr(arxiv, 'PAGE_SIZE', 1)
    calls = transport(monkeypatch, {0: feed([('2603.00001v1','2026-03-30T12:00:00Z')],total=5),
        1: HTTPError('https://export.arxiv.org',429,'rate limited',{},None)})
    result = execute(tmp_path)
    assert not result.ok
    assert len(result.data['papers']) == 1
    assert result.data['next_offset'] == 1
    assert result.data['stop_reason'] == 'rate_limited'
    assert calls == [0,1]


def test_empty_official_response_is_success(monkeypatch, tmp_path):
    transport(monkeypatch, {0: feed([])})
    result = execute(tmp_path)
    assert result.ok and result.data['papers'] == []
    assert result.data['stop_reason'] == 'exhausted'


@pytest.mark.parametrize('args', [{'source':'ieee'}, {'date_type':'published'}, {'limit':0},
    {'limit':21}, {'offset':-1}, {'date_from':'2026-04-01'}, {'date_to':'wrong'}, {'query':' '}])
def test_invalid_arguments_do_not_request_network(monkeypatch, tmp_path, args):
    calls = transport(monkeypatch,{})
    assert not execute(tmp_path, **args).ok
    assert calls == []


def test_discovery_disabled_with_network_or_search_disabled(tmp_path):
    assert 'search_papers' not in create_builtin_registry(tmp_path).names()
    assert 'search_papers' not in create_builtin_registry(tmp_path, include_network_tools=True, include_web_search=False).names()


def test_permission_and_encoded_official_transport(monkeypatch, tmp_path):
    from urllib.parse import parse_qs, urlsplit
    import raccoon.modules.tools.arxiv as arxiv
    from raccoon.modules.permissions.types import PermissionAction
    calls = []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, size):
            return feed([])
    class Opener:
        def open(self, req, timeout):
            calls.append(req.full_url)
            return Response()
    monkeypatch.setattr(arxiv, '_resolves_to_private_ip', lambda *a, **kw: False)
    monkeypatch.setattr(arxiv.request, 'build_opener', lambda *a: Opener())
    monkeypatch.setattr(arxiv, '_last_request', 0)
    tool = create_builtin_registry(tmp_path, include_network_tools=True).get('search_papers')
    assert tool.permission.action == PermissionAction.NETWORK_REQUEST
    assert tool.permission.target_value == arxiv.ENDPOINT
    assert execute(tmp_path, query='all:"3D reconstruction" AND all:pose').ok
    url = urlsplit(calls[0])
    assert url.hostname == 'export.arxiv.org'
    params = parse_qs(url.query)
    assert params['search_query'] == ['(all:"3D reconstruction" AND all:pose) AND submittedDate:[199101010000 TO 202604010400]']
    assert params['sortBy'] == ['lastUpdatedDate']
    assert params['sortOrder'] == ['descending']
    # The original-date filter has only an upper bound: old papers revised in
    # March must remain candidates. Current-version filtering happens locally.


def test_blocked_dns_is_reported_without_network(monkeypatch, tmp_path):
    import raccoon.modules.tools.arxiv as arxiv
    monkeypatch.setattr(arxiv, '_resolves_to_private_ip', lambda *a, **kw: True)
    result = execute(tmp_path)
    assert not result.ok
    assert result.data['next_offset'] == 0


def test_full_abstract_is_not_xml_truncated(monkeypatch, tmp_path):
    body = feed([('2603.00001v1', '2026-03-10T12:00:00Z')]).replace(b'Full abstract', b'Long abstract ' * 2000)
    transport(monkeypatch, {0: body})
    result = execute(tmp_path)
    assert result.ok
    assert len(result.data['papers'][0]['abstract']) > 20000
