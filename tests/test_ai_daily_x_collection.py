import importlib.util
import json
from datetime import datetime
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / '.agents/skills/ai-news-daily/scripts/collect_official_x.py'
spec = importlib.util.spec_from_file_location('ai_daily_x_collection', SCRIPT)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)
START = '2026-11-06T14:47:11Z'
END = '2026-11-07T14:47:11Z'
AFTER = datetime.fromisoformat('2026-11-07T15:00:00+00:00')


def target(identity, handle='OpenAI', *, verified=True):
    return {'id': identity, 'required': True, 'identity_status': 'verified',
            'x_identity_status': 'verified' if verified else 'unresolved',
            'official_x': [{'handle': handle}] if verified else [],
            'official_sources': [{'url': 'https://openai.com/news/'}]}


def post(identity='1', created=START, author='OpenAI', **extras):
    return {'id': identity, 'author': author, 'created_at': created, 'is_retweet': False,
            'text': 'Public release', 'url': f'https://x.com/{author}/status/{identity}', **extras}


def result(posts):
    return SimpleNamespace(returncode=0, stdout=json.dumps(posts), stderr='')


def run(tmp_path, targets, runner, **kwargs):
    request = {'window_start': START, 'window_end': END, 'output_dir': 'reports/sample'}
    catalog = {'schema_version': 1, 'mandatory_channels': ['official_x', 'hackernews', 'v2ex'], 'targets': targets}
    summary = collector.collect(request, catalog, project=tmp_path, call_runner=runner,
                                now=kwargs.pop('now', lambda: AFTER), pause=kwargs.pop('pause', lambda _: None), **kwargs)
    root = tmp_path / 'reports/sample'
    return (summary, json.loads((root / 'x-posts.json').read_text(encoding='utf-8')),
            json.loads((root / 'coverage-draft.json').read_text(encoding='utf-8')))


def test_shared_accounts_are_read_once_and_stop_at_window_start(tmp_path):
    calls = []
    def runner(command, *, timeout):
        calls.append(command)
        assert timeout <= 60
        return result([post('2', '2026-11-07T02:11:24Z'), post('1', '2026-11-06T14:00:00Z')])
    summary, public, draft = run(tmp_path, [target('openai'), target('codex', '@openai')], runner)
    assert len(calls) == 1
    assert calls[0][1:] == ['--profile', 'raccoon', 'twitter', 'tweets', 'OpenAI', '--limit', '50', '-f', 'json']
    assert summary['unique_accounts'] == 1
    assert [p['target_id'] for p in draft['products']] == ['openai', 'codex']
    assert draft['products'][0]['x_accounts'] == draft['products'][1]['x_accounts']
    assert public['accounts'][0]['scan']['complete'] is True
    assert len(public['accounts'][0]['window_posts']) == 1
    assert all(p['outcome'] == 'blocked' and p['official_checks'] == [] for p in draft['products'])
    assert draft['declared_complete'] is False


def test_limits_grow_but_short_or_empty_results_do_not_prove_exhaustion(tmp_path):
    limits = []
    def runner(command, *, timeout):
        limits.append(int(command[command.index('--limit') + 1]))
        return result([post('1', '2026-11-07T02:00:00Z')])
    _, public, _ = run(tmp_path, [target('openai')], runner)
    assert limits == [50, 100, 200]
    assert public['accounts'][0]['scan'] == {'method': 'tweets', 'complete': False,
        'stop_reason': 'truncated', 'oldest_seen_at': '2026-11-07T02:00:00+00:00', 'batch_reads': 3}
    empty_root = tmp_path / 'empty'
    _, public, _ = run(empty_root, [target('openai')], lambda command, timeout: result([]))
    assert public['accounts'][0]['scan']['complete'] is False
    assert public['accounts'][0]['scan']['batch_reads'] == 1


def test_uses_real_timestamps_not_query_or_number_of_filtered_candidates(tmp_path):
    def runner(command, *, timeout):
        return result([post('start', START), post('end', END), post('in', 'Sat Nov 07 02:11:24 +0000 2026'),
                       post('old', '2026-10-13T10:00:00Z'),
                       post('retweet', '2026-11-07T03:00:00Z', is_retweet=True)])
    _, public, _ = run(tmp_path, [target('openai')], runner)
    account = public['accounts'][0]
    assert {p['id'] for p in account['window_posts']} == {'start', 'in'}
    assert account['scan']['complete'] is True
    assert account['scan']['oldest_seen_at'] == '2026-10-13T10:00:00+00:00'


@pytest.mark.parametrize('bad', [post('other', '2026-10-13T10:00:00Z', author='SomeoneElse'),
                                  post('time', 'not a date'),
                                  post('flag', '2026-10-13T10:00:00Z', is_retweet=None)])
def test_wrong_author_or_unusable_post_data_never_proves_complete(tmp_path, bad):
    _, public, _ = run(tmp_path, [target('openai')], lambda command, timeout: result([bad]))
    assert public['accounts'][0]['scan']['complete'] is False
    assert public['accounts'][0]['window_posts'] == []


def test_unresolved_target_is_recorded_without_guessing_a_handle(tmp_path):
    def no_command(*args, **kwargs):
        raise AssertionError('Unverified accounts must not be queried')
    _, public, draft = run(tmp_path, [target('muse', verified=False)], no_command)
    assert public['accounts'] == []
    assert draft['products'][0]['outcome'] == 'identity_unresolved'
    assert draft['products'][0]['x_accounts'] == []


def test_old_retweet_does_not_prove_the_account_reached_window_start(tmp_path):
    posts = [post('recent', '2026-11-07T02:00:00Z'),
             post('retweet', '2020-01-01T00:00:00Z', is_retweet=True)]
    _, public, _ = run(tmp_path, [target('openai')], lambda command, timeout: result(posts))
    assert public['accounts'][0]['scan']['complete'] is False
    assert public['accounts'][0]['scan']['oldest_seen_at'] == '2026-11-07T02:00:00+00:00'


def test_other_authors_retweet_does_not_block_coverage_proven_by_own_posts(tmp_path):
    posts = [post('recent', '2026-11-07T02:00:00Z'),
             post('retweet', '2026-11-07T01:00:00Z', author='OtherOfficial', is_retweet=True),
             post('old', '2026-10-13T10:00:00Z')]
    _, public, _ = run(tmp_path, [target('openai')], lambda command, timeout: result(posts))
    account = public['accounts'][0]
    assert account['scan']['complete'] is True
    assert account['scan']['batch_reads'] == 1
    assert {p['id'] for p in account['window_posts']} == {'recent'}
    assert any(p['id'] == 'retweet' for p in account['attempts'][0]['posts'])
    assert 'author_mismatch' not in account['notes']


def test_global_connection_failure_stops_other_accounts_and_never_means_no_news(tmp_path):
    calls = []
    def runner(command, *, timeout):
        calls.append(command)
        return SimpleNamespace(returncode=69, stdout='ok: false\ncode: BROWSER_CONNECT',
                               stderr='Login information MUST_NOT_BE_SAVED')
    _, public, draft = run(tmp_path, [target('openai'), target('claude', 'claudeai')], runner)
    assert len(calls) == 1
    assert [a['status'] for a in public['accounts']] == ['blocked', 'not_checked']
    assert all(p['outcome'] == 'blocked' for p in draft['products'])
    assert 'MUST_NOT_BE_SAVED' not in json.dumps(public)
    assert 'MUST_NOT_BE_SAVED' not in json.dumps(draft)


def test_transient_failures_have_one_retry_and_timeout_is_bounded(tmp_path):
    calls = []
    def runner(command, *, timeout):
        calls.append(command)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(command, timeout)
        return result([post('old', '2026-10-13T10:00:00Z')])
    _, public, _ = run(tmp_path, [target('openai')], runner)
    assert len(calls) == 2
    assert public['accounts'][0]['scan']['complete'] is True
    assert public['accounts'][0]['attempts'][0]['error_code'] == 'COMMAND_TIMEOUT'


@pytest.mark.parametrize('task_request', [
    {'window_start': START, 'window_end': END, 'output_dir': '../outside'},
    {'window_start': START, 'window_end': END, 'output_dir': 'reports/ok', 'profile': 'daily'},
    {'window_start': '2026-11-06T14:47:11', 'window_end': END, 'output_dir': 'reports/ok'},
    {'window_start': START, 'window_end': '2026-11-08T14:47:11Z', 'output_dir': 'reports/ok'},
])
def test_rejects_escaping_paths_extra_options_and_invalid_windows(tmp_path, task_request):
    with pytest.raises(ValueError):
        collector.collect(task_request, {'schema_version': 1, 'targets': []}, project=tmp_path,
                          call_runner=lambda *a, **k: pytest.fail('must reject before execution'))


def test_request_and_output_symlinks_cannot_escape_reports(tmp_path):
    reports = tmp_path / 'reports'
    reports.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    alias = reports / 'escape'
    try:
        alias.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('Creating symlinks requires Windows privileges')
    with pytest.raises(ValueError):
        collector.report_path(alias / 'x-posts.json', tmp_path)
    (outside / 'request.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError):
        collector.load_request(alias / 'request.json', tmp_path)


def test_prepare_and_final_collection_keep_the_same_window_and_do_not_claim_completion(tmp_path):
    runner = lambda command, timeout: result([post('old', '2026-10-13T10:00:00Z')])
    before = datetime.fromisoformat('2026-11-07T14:30:00+00:00')
    early, public, draft = run(tmp_path / 'prepare', [target('openai')], runner, now=lambda: before)
    assert early['preparation_only'] is True
    assert early['x_coverage_status'] == 'partial'
    assert draft['window_end'] == END
    assert '提前准备' in draft['products'][0]['x_accounts'][0]['notes']
    final, public, draft = run(tmp_path / 'final', [target('openai')], runner)
    assert final['preparation_only'] is False
    assert final['x_coverage_status'] == 'complete'
    assert draft['window_end'] == END
    assert draft['declared_complete'] is False


def test_no_time_budget_records_remaining_accounts_without_running(tmp_path):
    _, public, draft = run(tmp_path, [target('openai'), target('claude', 'claudeai')],
                           lambda *a, **k: pytest.fail('budget is exhausted'), budget_seconds=0)
    assert all(a['status'] == 'not_checked' for a in public['accounts'])
    assert len(draft['products']) == 2


def test_collector_draft_is_honest_partial_under_shared_coverage_validator(tmp_path):
    checker_path = SCRIPT.with_name('check_coverage.py')
    checker_spec = importlib.util.spec_from_file_location('ai_daily_shared_checker', checker_path)
    checker = importlib.util.module_from_spec(checker_spec)
    checker_spec.loader.exec_module(checker)
    targets = [target('openai')]
    _, _, draft = run(tmp_path, targets,
                       lambda command, timeout: result([post('old', '2026-10-13T10:00:00Z')]))
    catalog = {'schema_version': 1, 'mandatory_channels': ['official_x', 'hackernews', 'v2ex'], 'targets': targets}
    verdict = checker.validate(catalog, draft)
    assert verdict['status'] == 'partial'
    assert verdict['errors'] == []
