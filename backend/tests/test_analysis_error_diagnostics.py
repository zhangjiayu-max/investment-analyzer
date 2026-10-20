"""连接异常必须保留底层原因及请求上下文，旧记录不能猜测根因。"""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError


def connection_error(url='https://example.test/v1/chat/completions'):
    try:
        try:
            raise OSError(61, 'Connection refused')
        except OSError as cause:
            raise httpx.ConnectError('Connection refused') from cause
    except httpx.ConnectError as cause:
        error = APIConnectionError(request=httpx.Request('POST', url))
        error.__cause__ = cause
        return error


def test_connection_diagnostics_expose_real_cause_without_credentials():
    from infra.analysis_errors import build_error_details
    details = build_error_details(connection_error('https://user:password@example.test/v1?api_key=secret'), stage='llm_request',
        endpoint='https://user:password@example.test/v1?api_key=secret', model='mimo')
    assert details['category'] == 'connection_refused'
    assert '拒绝' in details['summary']
    assert details['causes'][-1]['type'] == 'ConnectionRefusedError'
    assert '61' in details['causes'][-1]['message']
    assert details['endpoint'] == 'https://example.test/v1'
    assert 'secret' not in json.dumps(details) and 'password' not in json.dumps(details)


def test_exception_text_redacts_credentials_and_handles_cycles():
    from infra.analysis_errors import build_error_details
    error = RuntimeError('Authorization: Bearer secret-value api_key="hidden-key" https://u:p@host/path?token=hidden-query '
                         '{"token": "hidden-json", "api_key": "json-key", "password": "json-password"} '
                         'Authorization: Basic dXNlcjpwYXNz')
    error.__cause__ = error
    details = build_error_details(error)
    raw = json.dumps(details)
    assert len(details['causes']) == 1
    for secret in ('secret-value', 'hidden-key', 'hidden-query', 'hidden-json', 'json-key',
                   'json-password', 'dXNlcjpwYXNz', 'u:p@'):
        assert secret not in raw


def test_httpx_error_without_request_preserves_original_cause():
    from infra.analysis_errors import build_error_details
    details = build_error_details(httpx.ConnectError('Connection refused'))
    assert details['category'] == 'connection_refused'
    assert details['endpoint'] == ''
    assert details['causes'][0]['type'] == 'ConnectError'


def test_diagnostic_failure_never_replaces_model_error(monkeypatch):
    from services.llm import llm_service as m
    import infra.analysis_errors as diagnostics
    monkeypatch.setattr(m, '_enforce_token_budget', lambda *a: None)
    monkeypatch.setattr(m, '_fallback_client', None)
    monkeypatch.setattr(m, '_llm_retry', lambda f: f)
    original = connection_error()
    def fail(**kw):
        raise original
    monkeypatch.setattr(m, 'client', SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fail))))
    def broken_diagnostics(*a, **kw):
        raise RuntimeError('diagnostic unavailable')
    monkeypatch.setattr(diagnostics, 'build_error_details', broken_diagnostics)
    with pytest.raises(APIConnectionError) as caught:
        m._call_llm(model=m.MODEL, messages=[])
    assert caught.value is original


def test_cancellation_does_not_wait_for_diagnostic_database_write(monkeypatch):
    from services.llm import llm_service as m
    from infra.execution_budget import ExecutionCancelled
    import db.agent_analysis_log as logs
    monkeypatch.setattr(m, '_enforce_token_budget', lambda *a: None)
    monkeypatch.setattr(m, '_fallback_client', None)
    def fail(**kw):
        raise ExecutionCancelled('cancelled')
    monkeypatch.setattr(m, 'client', SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fail))))
    monkeypatch.setattr(logs, 'save_analysis_error_details', lambda *a: pytest.fail('cancelled request attempted DB write'))
    with pytest.raises(ExecutionCancelled):
        m._call_llm(trace_id='cancelled-log', model=m.MODEL, messages=[])


def test_diagnostic_write_has_short_lock_wait():
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    from db._conn import _get_conn
    from db.agent_analysis_log import create_analysis_log, save_analysis_error_details
    create_analysis_log('locked-log', 'index_analysis', 'analysis_history')
    lock = _get_conn()
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        lock.execute('BEGIN IMMEDIATE')
        future = pool.submit(save_analysis_error_details, 'locked-log', {'summary': 'network failure'})
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            future.result(timeout=.3)
    finally:
        lock.rollback()
        lock.close()
        pool.shutdown(wait=True)


def test_fallback_diagnostics_count_each_request_and_actual_model(monkeypatch):
    from services.llm import llm_service as m
    from tenacity import retry, stop_after_attempt, wait_none, retry_if_exception
    def fail(**kwargs):
        raise connection_error()
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fail)))
    monkeypatch.setattr(m, '_enforce_token_budget', lambda *a: None)
    monkeypatch.setattr(m, 'client', fake)
    monkeypatch.setattr(m, '_fallback_client', fake)
    monkeypatch.setattr(m, '_fallback_model', 'configured-fallback')
    monkeypatch.setattr(m, '_llm_retry', retry(stop=stop_after_attempt(2), wait=wait_none(),
        retry=retry_if_exception(m._is_retryable), reraise=True))
    with pytest.raises(APIConnectionError) as caught:
        m._call_llm(model=m.MODEL, messages=[])
    details = caught.value.analysis_error_details
    assert details['attempt_count'] == 4
    assert [a['model'] for a in details['attempts']] == [m.MODEL, 'configured-fallback'] * 2
    assert details['model'] == 'configured-fallback'


def test_llm_failure_records_attempts_and_survives_route_completion(monkeypatch):
    from services.llm import llm_service as m
    from db.agent_analysis_log import create_analysis_log, complete_analysis_log, get_analysis_log
    from tenacity import retry, stop_after_attempt, wait_none, retry_if_exception
    log_id = create_analysis_log('diagnostic-test', 'index_analysis', 'analysis_history')
    calls = []
    def fail(**kwargs):
        calls.append(kwargs)
        raise connection_error()
    monkeypatch.setattr(m, '_enforce_token_budget', lambda *a: None)
    monkeypatch.setattr(m, '_fallback_client', None)
    monkeypatch.setattr(m, 'client', SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fail))))
    monkeypatch.setattr(m, '_llm_retry', retry(stop=stop_after_attempt(2), wait=wait_none(),
        retry=retry_if_exception(m._is_retryable), reraise=True))
    with pytest.raises(APIConnectionError):
        m._call_llm(caller='index_deep_analysis', trace_id='diagnostic-test', model=m.MODEL,
                    messages=[{'role': 'user', 'content': 'PRIVATE PROMPT MUST NOT BE STORED'}])
    complete_analysis_log('diagnostic-test', 'error', error_msg='Connection error.')
    log = get_analysis_log(log_id)
    details = log['error_details']
    assert len(calls) == 2
    assert details['attempt_count'] == 2
    assert [a['attempt'] for a in details['attempts']] == [1, 2]
    assert details['caller'] == 'index_deep_analysis'
    assert details['model'] == m.MODEL
    assert details['elapsed_ms'] >= 0
    assert details['category'] == 'connection_refused'
    assert 'PRIVATE PROMPT' not in json.dumps(details)
    response = asyncio.run(__import__('routers.analysis.analysis_log', fromlist=['get_analysis_log_detail_api']).get_analysis_log_detail_api(log_id))
    assert response['log']['error_details'] == details


@pytest.mark.parametrize('error, category', [(TimeoutError(), 'timeout'), (ValueError('invalid response'), 'invalid_response')])
def test_non_connection_failure_is_not_mislabelled(error, category):
    from infra.analysis_errors import build_error_details
    assert build_error_details(error, stage='model_analysis')['category'] == category


def test_old_failure_explicitly_reports_missing_diagnostics():
    from db.agent_analysis_log import create_analysis_log, complete_analysis_log
    from routers.analysis.analysis_log import get_analysis_log_detail_api
    log_id = create_analysis_log('old-failure', 'index_analysis', 'analysis_history')
    complete_analysis_log('old-failure', 'error', error_msg='Connection error.')
    response = asyncio.run(get_analysis_log_detail_api(log_id))
    assert response['log']['error_details'] == {}
    assert '未保存' in response['diagnostic_note']


def test_index_background_failure_persists_exception_chain(monkeypatch):
    from routers.analysis import index_analysis as route
    from db.agent_analysis_log import create_analysis_log, get_analysis_log_by_trace
    from db.analysis import create_analysis_history
    history_id = create_analysis_history('930707', '中证畜牧', 9, '指数深度分析师', '', '', '', status='running')
    traces = []
    async def fail(history_id, req, agent, task, trace_id, started):
        traces.append(trace_id)
        create_analysis_log(trace_id, 'index_analysis', 'analysis_history', source_id=history_id)
        raise connection_error()
    monkeypatch.setattr(route, '_execute_index_analysis', fail)
    asyncio.run(route._run_index_analysis_async(history_id, {'index_code': '930707'}, {'id': 9}))
    details = get_analysis_log_by_trace(traces[0])['error_details']
    assert details['category'] == 'connection_refused'
    assert details['stage'] == 'background_analysis'
