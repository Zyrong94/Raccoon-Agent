from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / '.agents/skills/ai-news-daily/scripts/check_coverage.py'
spec = importlib.util.spec_from_file_location('ai_daily_coverage', SCRIPT)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)

START = '2026-11-06T22:47:11+08:00'
END = '2026-11-07T22:47:11+08:00'
CHECKED = '2026-11-07T23:00:00+08:00'


def receipt(url='https://x.com/Official/status/123'):
    return {'url': url, 'tool': 'opencli twitter tweets', 'read_at': CHECKED}


def checked(url='https://official.example.com/news'):
    return {'status': 'checked', 'checked_at': CHECKED, 'receipts': [receipt(url)]}


@pytest.fixture
def ledgers():
    catalog = {'schema_version': 1, 'mandatory_channels': ['official_x', 'hackernews', 'v2ex'],
        'targets': [{'id': 'product', 'name': 'Product', 'required': True, 'identity_status': 'verified',
                     'x_identity_status': 'verified',
                     'official_x': [{'handle': 'Official', 'url': 'https://x.com/Official',
                                     'verification_urls': ['https://official.example.com']}],
                     'official_sources': [{'url': 'https://official.example.com/news', 'kind': 'changelog'}]}]}
    account = {'handle': '@oFFICIAL', **checked(), 'query': 'twitter tweets Official --limit 100',
        'scan': {'method': 'tweets', 'complete': True, 'stop_reason': 'reached_window_start',
                 'oldest_seen_at': START, 'batch_reads': 1}, 'notes': ''}
    coverage = {'schema_version': 1, 'window_start': START, 'window_end': END, 'checked_at': CHECKED,
        'products': [{'target_id': 'product', 'x_accounts': [account],
                      'official_checks': [{'url': 'https://official.example.com/news', **checked()}],
                      'outcome': 'no_qualifying_update', 'notes': ''}],
        'channels': [{'id': name, **checked()} for name in ['official_x', 'hackernews', 'v2ex']],
        'items': [], 'declared_complete': True}
    return catalog, coverage


def test_complete_zero_news_is_valid_and_checker_discloses_its_limit(ledgers):
    result = checker.validate(*ledgers)
    assert result['status'] == 'complete' and result['counts']['items'] == 0
    assert not result['gaps'] and not result['errors']
    assert 'does not verify online facts' in result['limitation']


def test_exhausted_empty_official_timeline_can_be_complete(ledgers):
    catalog, coverage = ledgers
    account = coverage['products'][0]['x_accounts'][0]
    account['scan'].update(stop_reason='exhausted_results', oldest_seen_at=None)
    account['notes'] = 'Read result explicitly reported no next cursor; no posts exist.'
    assert checker.validate(catalog, coverage)['status'] == 'complete'


@pytest.mark.parametrize('missing', ['target', 'second_account', 'official_source', 'channel'])
def test_missing_required_coverage_is_honest_partial_or_false_complete_error(ledgers, missing):
    catalog, coverage = ledgers
    coverage['declared_complete'] = False
    coverage['products'][0]['outcome'] = 'blocked'
    if missing == 'target':
        coverage['products'] = []
    elif missing == 'second_account':
        catalog['targets'][0]['official_x'].append({'handle': 'SecondOfficial', 'url': 'https://x.com/SecondOfficial'})
    elif missing == 'official_source':
        coverage['products'][0]['official_checks'] = []
    else:
        coverage['channels'].pop()
    result = checker.validate(catalog, coverage)
    assert result['status'] == 'partial' and result['gaps']
    coverage['declared_complete'] = True
    assert checker.validate(catalog, coverage)['status'] == 'error'


def test_blocked_account_cannot_be_relabelled_as_no_news(ledgers):
    catalog, coverage = ledgers
    coverage['declared_complete'] = False
    account = coverage['products'][0]['x_accounts'][0]
    account.update(status='blocked', receipts=[], notes='Login wall')
    account['scan'].update(complete=False, stop_reason='blocked', oldest_seen_at=None, batch_reads=0)
    assert checker.validate(catalog, coverage)['status'] == 'error'
    coverage['products'][0]['outcome'] = 'blocked'
    assert checker.validate(catalog, coverage)['status'] == 'partial'


def test_truncated_limit_does_not_count_as_complete_scan(ledgers):
    catalog, coverage = ledgers
    account = coverage['products'][0]['x_accounts'][0]
    account['query'] = 'twitter search from:Official --limit 15'
    account['scan'].update(method='search', complete=False, stop_reason='truncated', oldest_seen_at=END)
    coverage['products'][0]['outcome'] = 'blocked'
    coverage['declared_complete'] = False
    assert checker.validate(catalog, coverage)['status'] == 'partial'
    account['scan']['complete'] = True
    assert checker.validate(catalog, coverage)['status'] == 'error'


def test_scan_cannot_claim_window_start_before_reaching_it(ledgers):
    catalog, coverage = ledgers
    coverage['products'][0]['x_accounts'][0]['scan']['oldest_seen_at'] = '2026-11-07T10:00:00+08:00'
    assert checker.validate(catalog, coverage)['status'] == 'error'


def test_combined_or_query_does_not_replace_individual_official_account_read(ledgers):
    catalog, coverage = ledgers
    account = coverage['products'][0]['x_accounts'][0]
    account['scan']['method'] = 'search'
    account['query'] = '(from:Official OR from:SomebodyElse) since:2026-11-06'
    assert checker.validate(catalog, coverage)['status'] == 'error'
    account['query'] = 'from:Official since:2026-11-06'
    assert checker.validate(catalog, coverage)['status'] == 'complete'


def test_preparation_before_window_end_is_partial_even_with_later_ledger_time(ledgers):
    catalog, coverage = ledgers
    coverage['declared_complete'] = False
    coverage['products'][0]['outcome'] = 'blocked'
    account = coverage['products'][0]['x_accounts'][0]
    earlier = '2026-11-07T22:40:00+08:00'
    account['checked_at'] = earlier
    account['receipts'][0]['read_at'] = earlier
    result = checker.validate(catalog, coverage)
    assert result['status'] == 'partial'
    assert any('checked_before_window_end' in gap for gap in result['gaps'])
    account['checked_at'] = CHECKED
    result = checker.validate(catalog, coverage)
    assert result['status'] == 'partial'
    assert any('receipts_before_window_end' in gap for gap in result['gaps'])
    coverage['declared_complete'] = True
    assert checker.validate(catalog, coverage)['status'] == 'error'


def test_news_with_exact_time_or_contained_bounds_needs_no_minimum_count(ledgers):
    catalog, coverage = ledgers
    coverage['products'][0]['outcome'] = 'news'
    coverage['items'] = [{'id': 'release', 'target_ids': ['product'], 'title': 'Released feature',
        'published_at': START, 'official_urls': ['https://official.example.com/release'],
        'evidence_urls': ['https://x.com/Official/status/123']}]
    assert checker.validate(catalog, coverage)['status'] == 'complete'
    coverage['items'][0].update(published_at=None,
        time_bounds={'earliest': START, 'latest': '2026-11-07T22:47:10+08:00'})
    assert checker.validate(catalog, coverage)['status'] == 'complete'
    coverage['items'][0]['time_bounds']['latest'] = END
    assert checker.validate(catalog, coverage)['status'] == 'error'


@pytest.mark.parametrize('value', ['2026-11-07', '2026-11-07T10:00:00', '2026-11-07T22:47:11+08:00'])
def test_date_only_naive_or_end_boundary_news_is_rejected(ledgers, value):
    catalog, coverage = ledgers
    coverage['products'][0]['outcome'] = 'news'
    coverage['items'] = [{'id': 'release', 'target_ids': ['product'], 'title': 'Released feature',
        'published_at': value, 'official_urls': ['https://official.example.com/release'],
        'evidence_urls': ['https://x.com/Official/status/123']}]
    assert checker.validate(catalog, coverage)['status'] == 'error'


@pytest.mark.parametrize('collection', ['products', 'account', 'items'])
def test_duplicate_records_are_rejected(ledgers, collection):
    catalog, coverage = ledgers
    if collection == 'products':
        coverage['products'].append(deepcopy(coverage['products'][0]))
    elif collection == 'account':
        coverage['products'][0]['x_accounts'].append(deepcopy(coverage['products'][0]['x_accounts'][0]))
    else:
        item = {'id': 'duplicate', 'target_ids': ['product'], 'title': 'Release', 'published_at': START,
                'official_urls': ['https://official.example.com/release'], 'evidence_urls': ['https://official.example.com/release']}
        coverage['products'][0]['outcome'] = 'news'
        coverage['items'] = [item, deepcopy(item)]
    assert checker.validate(catalog, coverage)['status'] == 'error'


def test_unverified_x_identity_is_partial_even_when_website_identity_is_known(ledgers):
    catalog, coverage = ledgers
    catalog['targets'][0].update(x_identity_status='unresolved', official_x=[])
    coverage['products'][0].update(x_accounts=[], outcome='identity_unresolved')
    coverage['declared_complete'] = False
    assert checker.validate(catalog, coverage)['status'] == 'partial'


@pytest.mark.parametrize('fault', ['product', 'item_reference'])
def test_unknown_targets_cannot_replace_required_product(ledgers, fault):
    catalog, coverage = ledgers
    if fault == 'product':
        coverage['products'][0]['target_id'] = 'unknown'
    else:
        coverage['items'] = [{'id': 'release', 'target_ids': ['unknown'], 'title': 'Release',
            'published_at': START, 'official_urls': ['https://official.example.com/release'],
            'evidence_urls': ['https://official.example.com/release']}]
    assert checker.validate(catalog, coverage)['status'] == 'error'


@pytest.mark.parametrize('fault', ['unknown_account', 'missing_query', 'missing_receipt', 'private_receipt', 'future_receipt'])
def test_bulk_query_or_unread_receipts_cannot_satisfy_account_coverage(ledgers, fault):
    catalog, coverage = ledgers
    account = coverage['products'][0]['x_accounts'][0]
    if fault == 'unknown_account':
        account['handle'] = 'RandomUser'
    elif fault == 'missing_query':
        account['query'] = ''
    elif fault == 'missing_receipt':
        account['receipts'] = []
    elif fault == 'private_receipt':
        account['receipts'][0]['url'] = 'http://127.0.0.1/secrets'
    else:
        account['receipts'][0]['read_at'] = '2026-11-08T01:00:00+08:00'
    assert checker.validate(catalog, coverage)['status'] == 'error'


def test_cli_only_reads_reports_and_rejects_extra_arguments(ledgers, tmp_path, monkeypatch, capsys):
    catalog, coverage = ledgers
    monkeypatch.setattr(checker, 'PROJECT', tmp_path)
    catalog_path = tmp_path / 'sources.json'
    catalog_path.write_text(json.dumps(catalog), encoding='utf-8')
    monkeypatch.setattr(checker, 'CATALOG', catalog_path)
    report = tmp_path / 'reports/coverage.json'
    report.parent.mkdir()
    report.write_text(json.dumps(coverage), encoding='utf-8')
    assert checker.main(['--', str(report)]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'complete'
    secret = tmp_path / '.raccoon/permissions.json'
    secret.parent.mkdir()
    secret.write_text('secret-placeholder', encoding='utf-8')
    for args in [[str(secret)], [str(report), 'extra'], ['--', str(report), '--help']]:
        assert checker.main(args) == 2
        output = capsys.readouterr().out
        assert 'secret-placeholder' not in output


def test_cli_rejects_report_symlink_escape(ledgers, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(checker, 'PROJECT', tmp_path)
    reports = tmp_path / 'reports'
    reports.mkdir()
    secret = tmp_path / 'secret.json'
    secret.write_text('private-payload', encoding='utf-8')
    alias = reports / 'coverage.json'
    try:
        alias.symlink_to(secret)
    except OSError:
        pytest.skip('Windows symlink permission is unavailable')
    assert checker.main([str(alias)]) == 2
    assert 'private-payload' not in capsys.readouterr().out
