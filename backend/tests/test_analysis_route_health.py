"""独立分析入口的配置与失败契约；所有外部数据和持久化均隔离。"""
import asyncio
import importlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock

import pytest
from fastapi import HTTPException


@pytest.fixture(autouse=True)
def tmp_db(monkeypatch, tmp_path):
    # 覆盖全局 fixture，禁止这些单元测试触碰真实数据库或初始化 schema。
    import db._conn
    monkeypatch.setattr(db._conn, 'DB_PATH', tmp_path / 'analysis-health-isolated.db')


@pytest.fixture
def routes(monkeypatch):
    mods = {key: importlib.import_module(path) for key, path in {
        'what_if': 'routers.analysis.what_if', 'bond': 'routers.analysis.bond_recommend',
        'hotspots': 'routers.analysis.hotspots', 'market': 'routers.analysis.market_intel',
        'enhanced': 'routers.market.valuation',
        **{key: f'routers.analysis.{key}' for key in ('index_analysis', 'panorama', 'diversification',
                                                    'deep_dive', 'fund_analysis', 'trade_review')},
    }.items()}
    for mod in mods.values():
        for name in ('create_analysis_log', 'complete_analysis_log', 'track_agent', 'untrack_agent',
                     '_track_agent', '_untrack_agent', 'save_analysis_conclusion', 'create_agent_run',
                     'save_analysis_cache', 'save_recommendations', 'update_async_task',
                     'create_portfolio_analysis_record', 'update_analysis_record', 'update_analysis_history'):
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, Mock(return_value=77))
        for name, result in [('list_holdings', []), ('list_valuation_indexes', []),
                             ('get_portfolio_diversification', {}), ('get_total_cash_balance', 0),
                             ('build_portfolio_facts_with_check', '{}')]:
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, Mock(return_value=result))
        if hasattr(mod, 'get_analysis_agent'):
            monkeypatch.setattr(mod, 'get_analysis_agent', lambda agent_id: {
                'id': agent_id, 'name': '分散度分析师', 'is_active': 1, 'system_prompt': 'configured analysis prompt'})
        if hasattr(mod, 'get_analysis_agent_by_name'):
            monkeypatch.setattr(mod, 'get_analysis_agent_by_name', lambda name: {
                'id': 99, 'name': name, 'is_active': 1, 'system_prompt': 'configured analysis prompt'})
        if hasattr(mod, 'get_config_float'):
            monkeypatch.setattr(mod, 'get_config_float', lambda key, default: default)
        if hasattr(mod, 'get_config_int'):
            monkeypatch.setattr(mod, 'get_config_int', lambda key, default: default)
    import agent.multi_agent as multi
    monkeypatch.setattr(multi, 'run_specialist', Mock(return_value={'status': 'success', 'analysis': '有效分析', 'tokens_used': 37}))
    for mod in mods.values():
        if hasattr(mod, '_get_conn'):
            monkeypatch.setattr(mod, '_get_conn', Mock(side_effect=AssertionError('unexpected database access')))
        if hasattr(mod, '_call_llm'):
            monkeypatch.setattr(mod, '_call_llm', Mock(side_effect=RuntimeError('LLM must be mocked')))
    import db
    import db.agents
    import db.agent_analysis_log
    monkeypatch.setattr(db, 'get_analysis_agent_by_name', lambda name: {
        'id': 99, 'name': name, 'is_active': 1, 'system_prompt': 'configured analysis prompt'})
    monkeypatch.setattr(db.agents, 'create_agent_run', Mock())
    monkeypatch.setattr(db.agent_analysis_log, 'update_analysis_log_source', Mock())
    monkeypatch.setattr(db.agent_analysis_log, 'create_analysis_log', Mock())
    monkeypatch.setattr(db.agent_analysis_log, 'complete_analysis_log', Mock())
    import routers.dashboard.dashboard as dashboard
    monkeypatch.setattr(dashboard, 'get_hot_topics', AsyncMock(return_value={'news': []}))
    import tools
    monkeypatch.setattr(tools, '_get_bond_temperature', lambda: '{}')
    monkeypatch.setattr(tools, '_get_bond_yield_curve', lambda *a: '{}')
    monkeypatch.setattr(tools, '_get_macro_policy_data', lambda: '{}')
    monkeypatch.setattr(mods['bond'], '_fetch_bond_data', lambda: [])
    monkeypatch.setitem(sys.modules, 'akshare', SimpleNamespace())
    market = mods['market']
    for name, result in [('_fetch_news_multi', []), ('_fetch_hot_topics', []),
                         ('_fetch_cctv_news', []), ('_fetch_market_hotspots', [])]:
        monkeypatch.setattr(market, name, AsyncMock(return_value=result))
    for name, result in [('_fetch_macro_data', {}), ('get_market_overview', {}),
                         ('_build_event_radar_brief', {}), ('_build_watchlist_signal_brief', {}),
                         ('_build_index_catalog', ''), ('_build_holdings_text', ''),
                         ('inject_rag_context', ''), ('build_unified_evidence', {})]:
        monkeypatch.setattr(market, name, Mock(return_value=result))
    monkeypatch.setattr(market, '_fuzzy_match_sectors_to_data', lambda s: s)
    return mods


def response(content, tokens=37):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                           usage=SimpleNamespace(total_tokens=tokens))


def invoke(key, routes):
    if key == 'what_if':
        from models.portfolio import WhatIfRequest
        routes[key].list_holdings.return_value = [{'current_value': 100}]
        routes[key]._get_valuation_context = lambda: ''
        return routes[key].what_if_analysis_api(WhatIfRequest(scenario='market_drop'))
    return {'bond': routes['bond']._do_bond_recommend,
            'hotspots': routes['hotspots']._do_hotspots_analysis,
            'market': routes['market']._do_market_intelligence,
            'enhanced': routes['enhanced'].get_enhanced_strategy}[key]()


@pytest.mark.parametrize('key', ['what_if', 'bond', 'hotspots', 'market', 'enhanced'])
@pytest.mark.parametrize('agent', [None, {'id': 99, 'is_active': 0, 'system_prompt': 'disabled'},
                                   {'id': 99, 'is_active': 1, 'system_prompt': '  '}])
def test_routes_require_enabled_agent_with_prompt(routes, monkeypatch, key, agent):
    import db
    monkeypatch.setattr(db, 'get_analysis_agent_by_name', lambda name: agent)
    if hasattr(routes[key], 'get_analysis_agent_by_name'):
        monkeypatch.setattr(routes[key], 'get_analysis_agent_by_name', lambda name: agent)
    with pytest.raises(HTTPException) as error:
        asyncio.run(invoke(key, routes))
    assert error.value.status_code == 400
    assert 'Agent' in error.value.detail


@pytest.mark.parametrize('key', ['what_if', 'bond'])
@pytest.mark.parametrize('result', [ {'status': 'failed', 'analysis': '失败占位文本'},
                                     {'status': 'success', 'analysis': '  '}])
def test_specialist_failure_never_creates_success_record(routes, monkeypatch, key, result):
    import agent.multi_agent as multi
    monkeypatch.setattr(multi, 'run_specialist', Mock(return_value=result))
    with pytest.raises(Exception):
        asyncio.run(invoke(key, routes))
    routes[key].create_portfolio_analysis_record.assert_not_called()
    assert routes[key].complete_analysis_log.call_args.kwargs['status'] == 'error'


@pytest.mark.parametrize('key', ['what_if', 'bond'])
def test_specialist_receives_analysis_agent_prompt(routes, monkeypatch, key):
    import agent.multi_agent as multi
    specialist = Mock(return_value={'status': 'success', 'analysis': '有效债券配置分析', 'tokens_used': 37})
    monkeypatch.setattr(multi, 'run_specialist', specialist)
    asyncio.run(invoke(key, routes))
    assert specialist.call_args.kwargs['system_prompt_override'] == 'configured analysis prompt'
    assert routes[key].create_portfolio_analysis_record.call_args.kwargs['token_usage'] == 37


@pytest.mark.parametrize('key', ['hotspots', 'market'])
@pytest.mark.parametrize('content', [None, '', '{}'])
def test_llm_failure_marks_task_error_without_caching(routes, monkeypatch, key, content):
    mod = routes[key]
    llm = Mock(side_effect=RuntimeError('upstream unavailable')) if content is None else Mock(return_value=response(content))
    monkeypatch.setattr(mod, '_call_llm', llm)
    worker = mod._run_hotspots_analysis_async if key == 'hotspots' else mod._run_market_intelligence_async
    asyncio.run(worker(42))
    assert mod.update_async_task.call_args.kwargs['status'] == 'error'
    mod.save_analysis_cache.assert_not_called()
    assert mod.complete_analysis_log.call_args.kwargs['status'] == 'error'
    assert 'configured analysis prompt' in llm.call_args.kwargs['messages'][0]['content']


def test_hotspots_history_uses_configured_id_and_actual_tokens(routes, monkeypatch):
    mod = routes['hotspots']
    monkeypatch.setattr(mod, '_call_llm', Mock(return_value=response(json.dumps({
        'summary': '市场观察', 'recommendations': [{'index_code': '000300', 'index_name': '沪深300'}]}))))
    monkeypatch.setattr(mod, 'get_index_current_price', lambda code: {})
    mod.save_recommendations.return_value = [12]
    conn = Mock()
    conn.execute.return_value.lastrowid = 15
    monkeypatch.setattr(mod, '_get_conn', lambda: conn)
    asyncio.run(mod._do_hotspots_analysis())
    args = conn.execute.call_args.args[1]
    assert args[0] == 99
    assert args[-1] == 37


def test_bond_runs_blocking_specialist_off_event_loop(routes, monkeypatch):
    import agent.multi_agent as multi
    def specialist(*args, **kwargs):
        with pytest.raises(RuntimeError, match='no running event loop'):
            asyncio.get_running_loop()
        return {'status': 'success', 'analysis': '有效债券配置分析', 'tokens_used': 37}
    monkeypatch.setattr(multi, 'run_specialist', specialist)
    asyncio.run(routes['bond']._do_bond_recommend())


def setup_enhanced_data(routes, monkeypatch):
    import db
    import db.config
    import db._conn
    import services.valuation as valuation_service
    import services.llm_service as llm
    conn = Mock()
    conn.execute.return_value.fetchall.return_value = [{
        'index_code': '000300', 'index_name': '沪深300', 'snapshot_date': '2026-10-17',
        'percentile': 10, 'current_value': 10, 'zscore': -1, 'source_image': 'dd_test',
    }] * 3
    conn.execute.return_value.lastrowid = 88
    monkeypatch.setattr(routes['enhanced'], '_get_conn', lambda: conn)
    monkeypatch.setattr(db._conn, '_get_conn', lambda: conn)
    monkeypatch.setattr(db, 'get_config_float', lambda k, d: d)
    monkeypatch.setattr(db, 'get_config_int', lambda k, d: d)
    monkeypatch.setattr(valuation_service, 'check_pe_pb_divergence', lambda c: None)
    monkeypatch.setattr(valuation_service, 'get_index_history_years', lambda c: 10)
    monkeypatch.setattr(valuation_service, 'get_history_years_warning', lambda y: None)
    import mcp.yingmi_client as yingmi
    monkeypatch.setattr(yingmi, 'get_yingmi_client', lambda: SimpleNamespace(call_tool=lambda *a: {}))
    import db.knowledge
    monkeypatch.setattr(db.knowledge, 'search_knowledge', lambda *a, **kw: [])
    return llm, conn


@pytest.mark.parametrize('content', [None, '', '{}'])
def test_enhanced_llm_failure_is_http_error(routes, monkeypatch, content):
    import db.agent_analysis_log
    llm, conn = setup_enhanced_data(routes, monkeypatch)
    call = Mock(side_effect=RuntimeError('upstream unavailable')) if content is None else Mock(return_value=response(content))
    monkeypatch.setattr(llm, '_call_llm', call)
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes['enhanced'].get_enhanced_strategy())
    assert error.value.status_code == 500
    assert db.agent_analysis_log.complete_analysis_log.call_args.kwargs['status'] == 'error'
    assert 'configured analysis prompt' in call.call_args.kwargs['messages'][0]['content']


@pytest.mark.parametrize('key,trigger_name', [
    ('bond', 'bond_ai_recommend'), ('hotspots', 'trigger_hotspots_analysis'),
    ('market', 'trigger_market_intelligence'),
])
def test_disabled_trigger_rejects_before_scheduling_task(routes, monkeypatch, key, trigger_name):
    mod = routes[key]
    monkeypatch.setattr(mod, 'get_analysis_agent_by_name', lambda name: None)
    create_task = Mock()
    monkeypatch.setattr(mod, 'create_async_task', create_task)
    with pytest.raises(HTTPException, match='已禁用'):
        asyncio.run(getattr(mod, trigger_name)())
    create_task.assert_not_called()


@pytest.mark.parametrize('failure', [None, 'macro', 'risk'])
def test_policy_scenario_checks_both_specialists(routes, monkeypatch, failure):
    import agent.multi_agent as multi
    from models.portfolio import WhatIfRequest
    mod = routes['what_if']
    mod.list_holdings.return_value = [{'current_value': 100}]
    monkeypatch.setattr(mod, '_get_valuation_context', lambda: '')
    macro = {'status': 'failed' if failure == 'macro' else 'success', 'analysis': '宏观传导分析', 'tokens_used': 20}
    risk = {'status': 'failed' if failure == 'risk' else 'success', 'analysis': '风险冲击分析', 'tokens_used': 17}
    specialist = Mock(side_effect=[macro, risk])
    monkeypatch.setattr(multi, 'run_specialist', specialist)
    invocation = mod.what_if_analysis_api(WhatIfRequest(scenario='rate_cut'))
    if failure:
        with pytest.raises(HTTPException) as error:
            asyncio.run(invocation)
        assert error.value.status_code == 500
        mod.create_portfolio_analysis_record.assert_not_called()
        assert mod.complete_analysis_log.call_args.kwargs['status'] == 'error'
    else:
        result = asyncio.run(invocation)
        assert result['token_usage'] == 37
        assert specialist.call_count == 2
        assert '宏观传导分析' in specialist.call_args.args[1]
    for call in specialist.call_args_list:
        assert call.kwargs['system_prompt_override'] == 'configured analysis prompt'


def test_bond_llm_exception_marks_task_error(routes, monkeypatch):
    import agent.multi_agent as multi
    mod = routes['bond']
    monkeypatch.setattr(multi, 'run_specialist', Mock(side_effect=RuntimeError('upstream unavailable')))
    asyncio.run(mod._run_bond_recommend_async(42))
    assert mod.update_async_task.call_args.kwargs['status'] == 'error'
    assert mod.complete_analysis_log.call_args.kwargs['status'] == 'error'
    mod.create_portfolio_analysis_record.assert_not_called()
    mod.save_analysis_conclusion.assert_not_called()


@pytest.mark.parametrize('key', ['index_analysis', 'panorama', 'diversification',
                                 'deep_dive', 'fund_analysis', 'trade_review'])
@pytest.mark.parametrize('agent', [None, {'id': 99, 'is_active': 0, 'system_prompt': 'disabled'},
                                   {'id': 99, 'is_active': 1, 'system_prompt': '  '}])
def test_async_route_validates_agent_before_starting_task(routes, monkeypatch, key, agent):
    mod = routes[key]
    monkeypatch.setattr(mod, 'get_analysis_agent_by_name', lambda name: agent, raising=False)
    if hasattr(mod, 'get_analysis_agent'):
        monkeypatch.setattr(mod, 'get_analysis_agent', lambda agent_id: agent)
    if agent:
        agent = {**agent, 'name': '测试分析师'}
    # 除配置校验外，不允许执行前置 IO 或创建任务。
    import db.async_tasks
    start = Mock(side_effect=AssertionError('must validate before creating task'))
    monkeypatch.setattr(db.async_tasks, 'start_analysis_task', start)
    if hasattr(mod, 'list_holdings'):
        mod.list_holdings.return_value = [{'current_value': 100, 'fund_code': '000001'}]
    if hasattr(mod, 'get_holding'):
        monkeypatch.setattr(mod, 'get_holding', lambda hid: {'fund_code': '000001'})
    if hasattr(mod, 'list_transactions'):
        monkeypatch.setattr(mod, 'list_transactions', lambda **kwargs: [{'id': 1, 'transaction_date': '2026-10-17', 'transaction_type': 'buy'}])
    for name, value in [('lookup_fund_info', {}), ('fetch_fund_nav', {}),
                        ('_get_valuation_context', ''), ('_get_valuation_context_for_fund', ''),
                        ('get_transaction_tags', [])]:
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, lambda *a, _value=value, **kw: _value)
    from models.portfolio import PanoramaAnalysisRequest, DeepDiveRequest, TradeReviewRequest
    from models.analysis import AnalysisRunRequest
    factories = {
        'index_analysis': lambda: mod.run_analysis(AnalysisRunRequest(index_code='000300')),
        'panorama': lambda: mod.panorama_analysis_api(PanoramaAnalysisRequest()),
        'diversification': lambda: mod.portfolio_diversification_ai_summary(),
        'deep_dive': lambda: mod.fund_deep_dive_api(1, DeepDiveRequest()),
        'fund_analysis': lambda: mod.fund_analysis_api({'fund_code': '000001'}),
        'trade_review': lambda: mod.trade_review_api(TradeReviewRequest()),
    }
    with pytest.raises(HTTPException) as error:
        asyncio.run(factories[key]())
    assert error.value.status_code == 400
    assert 'Agent' in error.value.detail
    start.assert_not_called()


def test_diversification_default_agent_is_resolved_by_name(routes, monkeypatch):
    mod = routes['diversification']
    lookup = Mock(return_value={'id': 42, 'name': '分散度分析师', 'is_active': 1,
                               'system_prompt': 'configured analysis prompt'})
    monkeypatch.setattr(mod, 'get_analysis_agent_by_name', lookup, raising=False)
    monkeypatch.setattr(mod, 'get_analysis_agent', Mock(side_effect=AssertionError('default must not use fixed id')))
    mod.list_holdings.return_value = [{'fund_code': '000001', 'current_value': 100}]
    import db.async_tasks
    start = Mock(return_value={'reused': True, 'id': 22, 'task_id': 23})
    monkeypatch.setattr(db.async_tasks, 'start_analysis_task', start)
    result = asyncio.run(mod.portfolio_diversification_ai_summary())
    assert lookup.call_args.args == ('分散度分析师',)
    assert start.call_args.args[2]['agent_id'] == 42
    assert start.call_args.args[4]['agent_id'] == 42
    assert result['task_id'] == 23


def setup_async_data(routes, monkeypatch):
    import services.llm_service as llm
    import tools
    import mcp.yingmi_client as yingmi
    monkeypatch.setattr(tools, 'execute_tool', lambda *a, **kw: '')
    monkeypatch.setattr(yingmi, 'get_yingmi_client', lambda: SimpleNamespace(call_tool=lambda *a: {}))
    for key in ('index_analysis', 'diversification'):
        mod = routes[key]
        monkeypatch.setattr(mod, 'inject_rag_context', lambda **kw: '')
        monkeypatch.setattr(mod, 'build_portfolio_facts_with_check', lambda: '{}')
        monkeypatch.setattr(mod, 'list_holdings', lambda: [])
        monkeypatch.setattr(mod, 'get_config_float', lambda k, d: d)
        monkeypatch.setattr(mod, 'get_config_int', lambda k, d: d)
    monkeypatch.setattr(routes['diversification'], 'list_portfolio_analysis_records', lambda **kw: [])
    monkeypatch.setattr(routes['diversification'], '_track_agent', Mock())
    monkeypatch.setattr(routes['diversification'], '_untrack_agent', Mock())
    monkeypatch.setattr(routes['index_analysis'], 'get_latest_valuation', lambda code: None)
    monkeypatch.setattr(routes['index_analysis'], 'get_valuation_history', lambda *a, **kw: [])
    monkeypatch.setattr(routes['index_analysis'], '_fetch_online_valuation_block', lambda *a: '')
    return llm


@pytest.mark.parametrize('key', ['index_analysis', 'diversification'])
def test_index_and_diversification_empty_llm_marks_source_and_task_error(routes, monkeypatch, key):
    llm = setup_async_data(routes, monkeypatch)
    import db
    monkeypatch.setattr(db, 'get_portfolio_analysis_record', lambda record_id: {'status': 'running'})
    call = Mock(return_value=response('   '))
    monkeypatch.setattr(llm, '_call_llm', call)
    mod = routes[key]
    if key == 'index_analysis':
        monkeypatch.setattr(mod, '_call_llm', call)
        agent = {'id': 99, 'name': '指数深度分析师', 'is_active': 1, 'system_prompt': 'configured prompt'}
        asyncio.run(mod._run_index_analysis_async(11, {'index_code': '000300'}, agent, 12))
        update = mod.update_analysis_history
    else:
        asyncio.run(mod._run_diversification_ai_async(12, 11, 99))
        update = mod.update_analysis_record
    assert update.call_args.kwargs['status'] == 'error'
    assert mod.update_async_task.call_args.kwargs['status'] == 'error'
    assert mod.complete_analysis_log.call_args.kwargs['status'] == 'error'


@pytest.mark.parametrize('failure_point', ['get_portfolio_diversification', 'inject_rag_context', 'create_analysis_log'])
def test_diversification_pre_llm_failure_closes_both_records(routes, monkeypatch, failure_point):
    setup_async_data(routes, monkeypatch)
    mod = routes['diversification']
    monkeypatch.setattr(mod, failure_point, Mock(side_effect=RuntimeError(failure_point + ' failed')))
    asyncio.run(mod._run_diversification_ai_async(12, 11, 99))
    assert mod.update_analysis_record.call_args.kwargs['status'] == 'error'
    assert mod.update_async_task.call_args.kwargs['status'] == 'error'


@pytest.mark.parametrize('failure_point', ['build_portfolio_facts_with_check', 'update_analysis_history'])
def test_index_worker_unexpected_failure_closes_task(routes, monkeypatch, failure_point):
    llm = setup_async_data(routes, monkeypatch)
    mod = routes['index_analysis']
    monkeypatch.setattr(mod, '_call_llm', lambda **kw: response('有效指数分析'))
    if failure_point == 'update_analysis_history':
        # 成功结果保存异常仍必须尝试标记 source error，并独立结束 task。
        mod.update_analysis_history.side_effect = [RuntimeError('write failed'), None]
    else:
        monkeypatch.setattr(mod, failure_point, Mock(side_effect=RuntimeError('facts failed')))
    agent = {'id': 99, 'name': '指数深度分析师', 'is_active': 1, 'system_prompt': 'configured prompt'}
    asyncio.run(mod._run_index_analysis_async(11, {'index_code': '000300'}, agent, 12))
    assert mod.update_analysis_history.call_args.kwargs['status'] == 'error'
    assert mod.update_async_task.call_args.kwargs['status'] == 'error'


@pytest.mark.parametrize('key', ['index_analysis', 'diversification'])
def test_cancelled_analysis_closes_source_and_task_before_propagating(routes, monkeypatch, key):
    llm = setup_async_data(routes, monkeypatch)
    mod = routes[key]
    call = Mock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(llm, '_call_llm', call)
    if key == 'index_analysis':
        monkeypatch.setattr(mod, '_call_llm', call)
        agent = {'id': 99, 'name': '指数深度分析师', 'is_active': 1, 'system_prompt': 'configured prompt'}
        worker = mod._run_index_analysis_async(11, {'index_code': '000300'}, agent, 12)
        update = mod.update_analysis_history
    else:
        worker = mod._run_diversification_ai_async(12, 11, 99)
        update = mod.update_analysis_record
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker)
    assert update.call_args.kwargs['status'] == 'error'
    assert mod.update_async_task.call_args.kwargs['status'] == 'error'


@pytest.mark.parametrize('key', ['index_analysis', 'diversification'])
def test_source_error_write_failure_does_not_prevent_task_error(routes, monkeypatch, key):
    llm = setup_async_data(routes, monkeypatch)
    mod = routes[key]
    call = Mock(side_effect=RuntimeError('model unavailable'))
    monkeypatch.setattr(llm, '_call_llm', call)
    if key == 'index_analysis':
        monkeypatch.setattr(mod, '_call_llm', call)
        mod.update_analysis_history.side_effect = RuntimeError('source unavailable')
        agent = {'id': 99, 'name': '指数深度分析师', 'is_active': 1, 'system_prompt': 'configured prompt'}
        asyncio.run(mod._run_index_analysis_async(11, {'index_code': '000300'}, agent, 12))
    else:
        mod.update_analysis_record.side_effect = RuntimeError('source unavailable')
        asyncio.run(mod._run_diversification_ai_async(12, 11, 99))
    assert mod.update_async_task.call_args.kwargs['status'] == 'error'


def test_diversification_success_keeps_source_and_task_done(routes, monkeypatch):
    llm = setup_async_data(routes, monkeypatch)
    mod = routes['diversification']
    import db
    monkeypatch.setattr(db, 'get_portfolio_analysis_record', lambda record_id: {'status': 'done'})
    monkeypatch.setattr(llm, '_call_llm', lambda **kw: response('有效分散度分析'))
    asyncio.run(mod._run_diversification_ai_async(12, 11, 99))
    assert mod.update_analysis_record.call_args.kwargs['status'] == 'done'
    assert mod.update_async_task.call_args.kwargs['status'] == 'done'


@pytest.mark.parametrize('key', ['what_if', 'bond', 'hotspots', 'market', 'enhanced'])
def test_primary_result_save_failure_is_error(routes, monkeypatch, key):
    mod = routes[key]
    if key in ('what_if', 'bond'):
        mod.create_portfolio_analysis_record.side_effect = RuntimeError('result save failed')
        if key == 'bond':
            asyncio.run(mod._run_bond_recommend_async(42))
            assert mod.update_async_task.call_args.kwargs['status'] == 'error'
        else:
            with pytest.raises(HTTPException) as error:
                asyncio.run(invoke(key, routes))
            assert error.value.status_code == 500
        assert mod.complete_analysis_log.call_args.kwargs['status'] == 'error'
    elif key in ('hotspots', 'market'):
        monkeypatch.setattr(mod, '_call_llm', lambda **kw: response(json.dumps({'summary': '有效分析'})))
        monkeypatch.setattr(mod, '_get_conn', Mock(side_effect=RuntimeError('result save failed')))
        worker = mod._run_hotspots_analysis_async if key == 'hotspots' else mod._run_market_intelligence_async
        asyncio.run(worker(42))
        assert mod.update_async_task.call_args.kwargs['status'] == 'error'
        assert mod.complete_analysis_log.call_args.kwargs['status'] == 'error'
        mod.save_analysis_cache.assert_not_called()
    else:
        import db.agent_analysis_log
        llm, conn = setup_enhanced_data(routes, monkeypatch)
        monkeypatch.setattr(llm, '_call_llm', lambda **kw: response(json.dumps({'overall_summary': '有效分析'})))
        original_cursor = conn.execute.return_value
        def execute(sql, *args):
            if sql.startswith('INSERT'):
                raise RuntimeError('result save failed')
            return original_cursor
        conn.execute.side_effect = execute
        with pytest.raises(HTTPException) as error:
            asyncio.run(mod.get_enhanced_strategy())
        assert error.value.status_code == 500
        assert db.agent_analysis_log.complete_analysis_log.call_args.kwargs['status'] == 'error'


def test_bond_success_links_log_and_returns_record_id(routes):
    import db.agent_analysis_log
    mod = routes['bond']
    result = asyncio.run(mod._do_bond_recommend())
    assert result['record_id'] == 77
    source = db.agent_analysis_log.update_analysis_log_source.call_args
    assert source.kwargs.get('source_id', source.args[-1] if source.args else None) == 77


def test_hotspots_recommendation_save_failure_is_error(routes, monkeypatch):
    mod = routes['hotspots']
    monkeypatch.setattr(mod, '_call_llm', lambda **kw: response(json.dumps({
        'summary': '有效分析', 'recommendations': [{'index_code': '000300'}]})))
    monkeypatch.setattr(mod, 'get_index_current_price', lambda code: {})
    mod.save_recommendations.side_effect = RuntimeError('recommendations save failed')
    asyncio.run(mod._run_hotspots_analysis_async(42))
    assert mod.update_async_task.call_args.kwargs['status'] == 'error'
    assert mod.complete_analysis_log.call_args.kwargs['status'] == 'error'
    mod.save_analysis_cache.assert_not_called()


@pytest.mark.parametrize('key', ['what_if', 'bond', 'hotspots', 'market', 'enhanced'])
def test_completed_result_survives_optional_log_completion_error(routes, monkeypatch, key):
    mod = routes[key]
    import db.agent_analysis_log
    if key == 'enhanced':
        llm, conn = setup_enhanced_data(routes, monkeypatch)
        monkeypatch.setattr(llm, '_call_llm', lambda **kw: response(json.dumps({'overall_summary': '有效分析'})))
        monkeypatch.setattr(db.agent_analysis_log, 'complete_analysis_log', Mock(side_effect=RuntimeError('log unavailable')))
    else:
        monkeypatch.setattr(mod, 'complete_analysis_log', Mock(side_effect=RuntimeError('log unavailable')))
        if key in ('hotspots', 'market'):
            monkeypatch.setattr(mod, '_call_llm', lambda **kw: response(json.dumps({'summary': '有效分析'})))
            conn = Mock()
            conn.execute.return_value.lastrowid = 77
            monkeypatch.setattr(mod, '_get_conn', lambda: conn)
    result = asyncio.run(invoke(key, routes))
    assert result
