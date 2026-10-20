"""指数分析主指标、证据和异步执行契约；仅临时 SQLite 与外部调用替身。"""
import asyncio
import json
import threading
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def route(monkeypatch):
    from routers.analysis import index_analysis as route
    from db.config import update_config
    import agent.eval_scorer as scorer
    async def noop(**kwargs):
        return None
    monkeypatch.setattr(scorer, 'evaluate_llm_output', noop)
    update_config('tool.online_valuation_query_enabled', 'false')
    monkeypatch.setattr(route, 'create_analysis_log', lambda **kw: None)
    monkeypatch.setattr(route, 'complete_analysis_log', lambda **kw: None)
    return route


def _insert(metric, value, when=None):
    from db._conn import _get_conn
    with _get_conn() as conn:
        conn.execute('''INSERT INTO index_valuations
            (index_code,index_name,snapshot_date,metric_type,current_value,percentile,source_image,source_url)
            VALUES (?,?,?,?,?,?,?,?)''',
            ('000300', '沪深300', when or date.today().isoformat(), metric, value, 10,
             'dd_evidence.png', 'https://example.test/valuation'))


def _response(content='基于提供证据的历史分析'):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                           usage=SimpleNamespace(total_tokens=20))


def test_request_metric_default_and_validation():
    from models.analysis import AnalysisRunRequest
    assert AnalysisRunRequest(index_code='000300').metric_type == '市净率'
    assert AnalysisRunRequest(metric_type='股息率').metric_type == '股息率'
    with pytest.raises(ValueError):
        AnalysisRunRequest(metric_type='unsupported')


def test_tasks_deduplicate_only_same_index_and_metric(route, monkeypatch):
    from models.analysis import AnalysisRunRequest
    from db.async_tasks import get_async_task
    async def waiting(*args):
        await asyncio.Event().wait()
    monkeypatch.setattr(route, '_run_index_analysis_async', waiting)
    async def run():
        pe = await route.run_analysis(AnalysisRunRequest(index_code='000300', metric_type='市盈率'))
        pb = await route.run_analysis(AnalysisRunRequest(index_code='000300', metric_type='市净率'))
        same = await route.run_analysis(AnalysisRunRequest(index_code='000300', metric_type='市盈率'))
        assert pe['task_id'] != pb['task_id']
        assert same['reused'] and same['task_id'] == pe['task_id']
        assert json.loads(get_async_task(pe['task_id'])['result'])['metric_type'] == '市盈率'
        item = await route.get_analysis_history_detail_api(pe['id'])
        assert item['metric_type'] == '市盈率'
    asyncio.run(run())


def test_valuation_evidence_is_selected_metric_and_observations_not_days(route):
    from models.analysis import AnalysisRunRequest
    _insert('市盈率', 12)
    _insert('市净率', 1.5)
    _insert('市盈率', 11, (date.today() - timedelta(days=12)).isoformat())
    evidence = route._collect_valuation_evidence(AnalysisRunRequest(index_code='000300', metric_type='市盈率'))
    assert evidence['primary_metric'] == '市盈率'
    assert evidence['latest']['current_value'] == 12
    assert {row['metric_type'] for group in evidence['history_groups'] for row in group['observations']} == {'市盈率'}
    assert evidence['history_observation_count'] == 2
    assert evidence['freshness'] == 'fresh'
    assert evidence['sources'][0]['source_url'] == 'https://example.test/valuation'
    assert '近2天' not in json.dumps(evidence, ensure_ascii=False)


def test_online_does_not_substitute_unsupported_metric_or_invent_date(route, monkeypatch):
    from models.analysis import AnalysisRunRequest
    import db.valuations as vals
    from db.config import update_config
    update_config('tool.online_valuation_query_enabled', 'true')
    fetch = Mock(return_value={'metric_type': '市盈率', 'current_value': 12, 'percentile': 10, 'source': 'online'})
    monkeypatch.setattr(vals, 'fetch_online_valuation', fetch)
    ps = route._collect_valuation_evidence(AnalysisRunRequest(index_code='000300', metric_type='市销率'))
    fetch.assert_not_called()
    assert ps['freshness'] == 'missing' and ps['warnings']
    pe = route._collect_valuation_evidence(AnalysisRunRequest(index_code='000300', metric_type='市盈率'))
    assert fetch.call_args.kwargs['metric_type'] == '市盈率'
    assert pe['valuation_asof'] is None
    assert pe['freshness'] == 'unknown'
    assert pe['warnings']


def test_news_retains_url_and_published_date(route, monkeypatch):
    from models.analysis import AnalysisRunRequest
    import tools
    import mcp.yingmi_client as yingmi
    monkeypatch.setattr(tools, 'execute_tool', lambda *a, **kw: json.dumps({'results': [
        {'title': '政策', 'url': 'https://example.test/web', 'time': '2026-10-17', 'source': 'web'},
    ]}))
    monkeypatch.setattr(yingmi, 'get_yingmi_client', lambda: SimpleNamespace(call_tool=lambda *a: {'content': [
        {'type': 'text', 'text': json.dumps({'success': True, 'data': {'items': [
            {'title': '产业新闻', 'url': 'https://example.test/mcp', 'publishTime': '2026-10-16', 'sources': 'MCP'},
        ]}})},
    ]}))
    news = route._collect_index_news(AnalysisRunRequest(index_code='000300', index_name='沪深300'))
    assert {(n['url'], n['published_at']) for n in news['items']} == {
        ('https://example.test/web', '2026-10-17'), ('https://example.test/mcp', '2026-10-16'),
    }
    assert 'https://example.test/mcp' in news['context']


def test_worker_offloads_data_calls_and_persists_downgrade_evidence(route, monkeypatch):
    from db.analysis import create_analysis_history
    from db.async_tasks import create_async_task, get_async_task
    main_thread = threading.get_ident()
    called = []
    def offloop(value):
        def call(*args, **kwargs):
            assert threading.get_ident() != main_thread
            called.append(value)
            return value
        return call
    monkeypatch.setattr(route, 'inject_rag_context', offloop(''))
    monkeypatch.setattr(route, 'build_portfolio_facts_with_check', offloop('{}'))
    monkeypatch.setattr(route, 'list_holdings', offloop([]))
    monkeypatch.setattr(route, '_fetch_online_valuation_block', offloop(''))
    monkeypatch.setattr(route, '_collect_index_news', offloop({'context': '', 'items': [], 'warnings': ['新闻缺失']}))
    model = Mock(return_value=_response())
    monkeypatch.setattr(route, '_call_llm', model)
    import agent.eval_scorer as scorer
    async def noop(**kwargs):
        return None
    monkeypatch.setattr(scorer, 'evaluate_llm_output', noop)
    history_id = create_analysis_history('000300', '沪深300', 1, '指数深度分析师', '', '', '', status='running')
    task_id = create_async_task('index_analysis')
    agent = {'id': 1, 'name': '指数深度分析师', 'system_prompt': '数据库配置提示词'}
    asyncio.run(route._run_index_analysis_async(history_id, {'index_code': '000300', 'index_name': '沪深300', 'metric_type': '市盈率'}, agent, task_id))
    item = asyncio.run(route.get_analysis_history_detail_api(history_id))
    assert item['status'] == 'done'
    assert item['metric_type'] == '市盈率'
    assert item['evidence']['freshness'] == 'missing'
    assert '数据不足' in item['result'] and '市盈率' in item['result']
    assert '禁止' in model.call_args.kwargs['messages'][0]['content']
    result = json.loads(get_async_task(task_id)['result'])
    assert result['evidence'] == item['evidence']
    status = asyncio.run(route.get_analysis_history_status_api(history_id))
    assert status['metric_type'] == '市盈率' and status['evidence'] == item['evidence']
    assert len(called) == 5


def test_bounded_data_timeout_does_not_stall_event_loop(route):
    import time
    async def run():
        progress = []
        async def heartbeat():
            await asyncio.sleep(0.005)
            progress.append('responsive')
        slow = asyncio.create_task(route._bounded_data_call('测试数据', lambda: time.sleep(0.08), timeout=0.02))
        await heartbeat()
        value, warning = await slow
        assert progress == ['responsive']
        assert value is None and '超时' in warning
    asyncio.run(run())


def test_history_groups_real_provider_across_article_images_and_marks_unknown(route):
    from models.analysis import AnalysisRunRequest
    from db._conn import _get_conn
    _insert('市盈率', 12)
    _insert('市盈率', 11, (date.today() - timedelta(days=1)).isoformat())
    with _get_conn() as conn:
        conn.execute("UPDATE index_valuations SET raw_json=?, source_image=snapshot_date || '.png', source_url='https://example.test/' || snapshot_date",
                     (json.dumps({'provider': '真实机构'}),))
    evidence = route._collect_valuation_evidence(AnalysisRunRequest(index_code='000300', metric_type='市盈率'))
    assert len(evidence['history_groups']) == 1
    group = evidence['history_groups'][0]
    assert group['source'] == '真实机构' and group['comparable'] is True
    assert len({row['source_image'] for row in group['observations']}) == 2
    with _get_conn() as conn:
        conn.execute('UPDATE index_valuations SET raw_json=NULL')
    unknown = route._collect_valuation_evidence(AnalysisRunRequest(index_code='000300', metric_type='市盈率'))
    assert unknown['history_groups'][0]['comparable'] is False
    assert any('来源未标注' in warning for warning in unknown['warnings'])


@pytest.mark.parametrize('never_finishes', [False, True])
def test_worker_continues_truncated_output_and_preserves_reasoning(route, monkeypatch, never_finishes):
    from db.analysis import create_analysis_history
    from db.async_tasks import create_async_task, get_async_task
    monkeypatch.setattr(route, 'inject_rag_context', lambda **kw: '')
    monkeypatch.setattr(route, 'build_portfolio_facts_with_check', lambda: '{}')
    monkeypatch.setattr(route, 'list_holdings', lambda: [])
    monkeypatch.setattr(route, '_collect_index_news', lambda *a: {'context': '', 'items': [], 'warnings': []})
    calls = []
    def model(**kwargs):
        calls.append(kwargs)
        response = _response('前半段' if len(calls) == 1 else '后半段')
        response.choices[0].finish_reason = 'length' if len(calls) == 1 or never_finishes else 'stop'
        response.choices[0].message.reasoning_content = '保留推理上下文'
        return response
    monkeypatch.setattr(route, '_call_llm', model)
    history_id = create_analysis_history('000300', '', 1, '指数深度分析师', '', '', '', status='running')
    task_id = create_async_task('index_analysis')
    asyncio.run(route._run_index_analysis_async(history_id, {'index_code': '000300'},
               {'id': 1, 'name': '指数深度分析师', 'system_prompt': '数据库配置提示词'}, task_id))
    task = get_async_task(task_id)
    if never_finishes:
        assert task['status'] == 'error'
        assert '截断' in task['error_msg']
    else:
        assert len(calls) == 2
        assert calls[1]['messages'][-2]['reasoning_content'] == '保留推理上下文'
        assert task['status'] == 'done'
        result = json.loads(task['result'])
        assert '前半段后半段' in result['result'] and result['token_usage'] == 40


def test_request_accepts_existing_ps_ttm_metric():
    from models.analysis import AnalysisRunRequest
    assert AnalysisRunRequest(metric_type='市销率TTM').metric_type == '市销率TTM'


@pytest.mark.parametrize(('field', 'value'), [
    ('current_value', float('nan')), ('current_value', float('inf')), ('current_value', -1),
    ('percentile', float('nan')), ('percentile', float('inf')), ('percentile', -1), ('percentile', 101),
])
def test_invalid_primary_numbers_are_missing_evidence(route, monkeypatch, field, value):
    from models.analysis import AnalysisRunRequest
    row = {'metric_type': '市盈率', 'current_value': 12, 'percentile': 10,
           'snapshot_date': date.today().isoformat(), field: value}
    monkeypatch.setattr(route, 'get_best_valuation', lambda *a, **kw: row)
    monkeypatch.setattr(route, 'get_valuation_history', lambda *a, **kw: [row])
    evidence = route._collect_valuation_evidence(AnalysisRunRequest(index_code='000300', metric_type='市盈率'))
    assert evidence['data_quality'] == 'insufficient'
    assert evidence['latest'][field] is None
    assert evidence['history_observation_count'] == 0
    assert evidence['warnings']
    json.dumps(evidence, allow_nan=False)


def test_zero_percentile_is_valid_primary_evidence(route, monkeypatch):
    from models.analysis import AnalysisRunRequest
    row = {'metric_type': '市盈率', 'current_value': 12, 'percentile': 0,
           'snapshot_date': date.today().isoformat()}
    monkeypatch.setattr(route, 'get_best_valuation', lambda *a, **kw: row)
    monkeypatch.setattr(route, 'get_valuation_history', lambda *a, **kw: [row])
    evidence = route._collect_valuation_evidence(AnalysisRunRequest(index_code='000300', metric_type='市盈率'))
    assert evidence['data_quality'] == 'sufficient'
    assert evidence['latest']['percentile'] == 0


def test_model_failure_keeps_collected_evidence_for_history(route, monkeypatch):
    from db.analysis import create_analysis_history
    from db.async_tasks import create_async_task
    monkeypatch.setattr(route, 'inject_rag_context', lambda **kw: '')
    monkeypatch.setattr(route, 'build_portfolio_facts_with_check', lambda: '{}')
    monkeypatch.setattr(route, 'list_holdings', lambda: [])
    monkeypatch.setattr(route, '_collect_index_news', lambda *a: {'context': '', 'items': [], 'warnings': []})
    monkeypatch.setattr(route, '_call_llm', Mock(side_effect=RuntimeError('模型不可用')))
    _insert('市盈率', 12)
    history_id = create_analysis_history('000300', '', 1, '指数深度分析师', '', '', '', status='running')
    task_id = create_async_task('index_analysis')
    asyncio.run(route._run_index_analysis_async(history_id, {'index_code': '000300', 'metric_type': '市盈率'},
               {'id': 1, 'name': '指数深度分析师', 'system_prompt': '数据库配置提示词'}, task_id))
    item = asyncio.run(route.get_analysis_history_detail_api(history_id))
    assert item['status'] == 'error'
    assert item['metric_type'] == '市盈率'
    assert item['evidence']['latest']['current_value'] == 12
