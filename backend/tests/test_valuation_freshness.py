"""估值来源日期优先与过期降级，使用临时库并隔离在线请求。"""
import ast
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from db import valuations as v


def day(age=0):
    return (date.today() - timedelta(days=age)).isoformat()


def local(age=12, metric='市盈率', value=12, code='000300', name='沪深300'):
    v.save_valuation({'index_code': code, 'index_name': name, 'metric_type': metric,
                      'current_value': value, 'percentile': 20}, snapshot_date=day(age))


def image(source='螺丝钉', age=1, code='000300', name='沪深300', **metrics):
    save = v.save_dd_valuation if source == '螺丝钉' else v.save_liuyi_valuation
    return save({'ok': True, 'update_date': day(age), 'data': [
        {'index_code': code, 'index_name': name, **(metrics or {'pe': 15, 'pe_percentile': 25})}
    ]}, f'{source}-{age}-{name}.png')


@pytest.fixture(autouse=True)
def isolate_online(monkeypatch):
    monkeypatch.setattr(v, '_online_cache', {})
    monkeypatch.setattr(v, '_ONLINE_FAILURES', {})
    monkeypatch.setattr(v, '_run_with_safe_timeout', lambda *a, **kw: None)


def test_dd_exact_code_is_queryable():
    image()
    result = v.get_latest_dd_valuation_for_index('000300.CSI')
    assert result and result['current_value'] == 15


def test_dd_resolves_unique_explicit_alias_without_code():
    v.save_index_code_mapping('000300', '沪深300', ['沪深三百'])
    image(code=None, name='沪深三百')
    result = v.get_latest_dd_valuation_for_index('000300')
    assert result and result['current_value'] == 15


def test_ambiguous_image_name_is_not_assigned_to_an_index():
    local(code='000300', name='同名指数')
    local(code='000301', name='同名指数')
    image(code=None, name='同名指数')
    assert v.get_latest_dd_valuation_for_index('000300') is None


@pytest.mark.parametrize('source', ['螺丝钉', '六亿'])
def test_newer_image_source_wins_even_when_main_table_is_recent(source):
    local(age=2)
    image(source=source, age=1)
    result = v.get_best_valuation('000300', enable_online=False)
    assert result['current_value'] == 15
    assert result['snapshot_date'] == day(1)
    assert result['data_source'] == source


def test_stale_local_is_refreshed_before_return(monkeypatch):
    local(age=140)
    online = Mock(return_value={'index_code': '000300', 'metric_type': '市盈率',
        'current_value': 18, 'percentile': 35, 'snapshot_date': day(1), 'source': 'akshare'})
    monkeypatch.setattr(v, '_online_fallback', online)
    result = v.get_best_valuation('000300')
    online.assert_called_once()
    assert result['current_value'] == 18
    assert result['is_expired'] is False


@pytest.mark.parametrize('online_date', [None, 'invalid', day(-1), day(20)])
def test_online_cannot_replace_newer_valid_local_date(monkeypatch, online_date):
    local(age=12)
    monkeypatch.setattr(v, '_online_fallback', lambda *a, **kw: {
        'metric_type': '市盈率', 'current_value': 99, 'snapshot_date': online_date})
    result = v.get_best_valuation('000300')
    assert result['current_value'] == 12
    assert result['is_expired'] is True
    assert result['freshness'] == 'stale'


def test_future_local_does_not_hide_actual_latest_snapshot():
    local(age=-3, value=99)
    local(age=1, value=14)
    result = v.get_best_valuation('000300', enable_online=False)
    assert result['current_value'] == 14


def test_stale_source_does_not_short_circuit_newer_provider(monkeypatch):
    calls = []
    def fetch(fn, *args, **kw):
        calls.append(fn.__name__)
        return {'metric_type': '市盈率', 'current_value': 12 if len(calls) == 1 else 16,
                'snapshot_date': day(20) if len(calls) == 1 else day(1)}
    monkeypatch.setattr(v, '_run_with_safe_timeout', fetch)
    result = v.fetch_online_valuation('000300')
    assert len(calls) == 2
    assert result['current_value'] == 16
    assert result['source'] == 'ttfund_online'


def test_stale_persistent_cache_does_not_block_provider_refresh(monkeypatch):
    v.save_online_valuation('000300', '市盈率', {'current_value': 12, 'snapshot_date': day(20)}, 'akshare')
    fetch = Mock(return_value={'metric_type': '市盈率', 'current_value': 16, 'snapshot_date': day(1)})
    monkeypatch.setattr(v, '_run_with_safe_timeout', fetch)
    result = v._online_fallback('000300', '市盈率', datetime.now(), 'test')
    assert fetch.called
    assert result['current_value'] == 16


@pytest.mark.parametrize('provider_date', [None, day(20)])
def test_recent_memory_cache_with_old_source_date_is_refreshed(monkeypatch, provider_date):
    monkeypatch.setattr(v, '_online_cache', {'000300:市盈率': {
        'current_value': 12, 'snapshot_date': provider_date, '_cached_at': datetime.now()}})
    fetch = Mock(return_value={'metric_type': '市盈率', 'current_value': 16, 'snapshot_date': day(1)})
    monkeypatch.setattr(v, '_run_with_safe_timeout', fetch)
    result = v.fetch_online_valuation('000300')
    assert fetch.called
    assert result['current_value'] == 16


def test_query_tool_keeps_local_date_when_online_is_older(monkeypatch):
    import tools
    local(age=12)
    monkeypatch.setattr(v, '_online_fallback', lambda *a, **kw: {
        'metric_type': '市盈率', 'current_value': 99, 'snapshot_date': day(20)})
    result = json.loads(tools._query_valuation({'index_name': '沪深300'}))[0]['metrics'][0]
    assert result['current_value'] == 12
    assert result['freshness'] == 'stale'
    assert 'level' not in result
    assert 'online_latest' not in result
    assert '历史参考' in result['data_freshness_hint']


def test_query_tool_discovers_image_only_index():
    import tools
    image(source='六亿')
    result = json.loads(tools._query_valuation({'index_name': '沪深300'}))[0]['metrics'][0]
    assert result['current_value'] == 15
    assert result['source'] == '六亿'


def test_overview_cannot_recommend_expired_cheap_snapshot():
    import tools
    local(age=100)
    cheap = json.loads(tools._get_valuation_list({'filter': 'undervalued'}))
    assert cheap['count'] == 0
    all_data = json.loads(tools._get_valuation_list({'filter': 'all'}))['indexes'][0]
    assert all_data['freshness'] == 'stale'
    assert all_data.get('level') != '低估'


def test_online_tool_checks_each_metric_date_and_preserves_provider(monkeypatch):
    import tools
    local(age=12)
    def fetch(code, metric_type, **kw):
        return {'index_code': code, 'metric_type': metric_type, 'current_value': 12, 'percentile': 20,
                'source': 'ttfund_online', 'snapshot_date': day(20) if metric_type == '市盈率' else day(1)}
    monkeypatch.setattr(v, 'fetch_online_valuation', fetch)
    result = json.loads(tools._query_online_valuation_impl({'index_name': '沪深300'}))[0]
    metrics = {row['metric_type']: row for row in result['metrics']}
    assert metrics['市盈率']['date'] == day(12)
    assert metrics['市盈率']['is_expired'] is True
    assert 'level' not in metrics['市盈率']
    assert metrics['市净率']['date'] == day(1)
    assert metrics['市净率']['source'] == 'ttfund_online'
    assert result.get('source') != 'akshare'


def test_summary_dates_are_per_metric_and_old_pe_has_no_current_label():
    from services.portfolio.portfolio_context import build_valuation_summary
    local(age=140)
    local(age=1, metric='市净率', value=1.2)
    text = build_valuation_summary()
    assert day(140) in text
    assert day(1) in text
    assert '历史参考' in text


def test_legacy_chat_context_uses_fresh_image_source():
    # 只加载问答上下文函数，避免导入 app 时启动重型依赖与后台任务。
    import db
    source = Path(__file__).resolve().parents[1] / 'app.py'
    tree = ast.parse(source.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_build_valuation_context')
    namespace = vars(db).copy()
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
    local(age=140)
    image(age=1, pe=15, pe_percentile=25)
    text = namespace['_build_valuation_context']('沪深300现在估值如何')
    assert day(1) in text
    assert '螺丝钉' in text


def test_index_analysis_uses_fresh_image_source(monkeypatch):
    from routers.analysis import index_analysis as route
    from db.config import update_config
    update_config('tool.online_valuation_query_enabled', 'false')
    local(age=140)
    image(age=1)
    result = route._collect_valuation_evidence(SimpleNamespace(index_code='000300', metric_type='市盈率'))
    assert result['valuation_asof'] == day(1)
    assert result['freshness'] == 'fresh'
    assert result['latest']['current_value'] == 15
