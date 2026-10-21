"""ReAct 专家缓存只可复用同一请求和相同运行配置的结果。"""
import json

import pytest

from agent.infra.cache import ExpertCache
from agent.core import orchestrator


@pytest.fixture
def cached_expert(monkeypatch):
    cache = ExpertCache()
    monkeypatch.setattr(cache, '_embed', lambda _: None)
    monkeypatch.setattr(orchestrator, 'expert_cache', cache)
    monkeypatch.setattr(orchestrator, 'load_specialist_agents', lambda: {
        'risk_assessor': {'system_prompt': '审视风险', 'tools': ['query_portfolio']}})
    calls = []

    def execute(name, query, **kwargs):
        calls.append(kwargs)
        return json.dumps({'analysis': f'私有结果-{len(calls)}',
                           'tool_calls': [{'name': 'query_portfolio', 'result': kwargs['trace_id']}]})

    monkeypatch.setattr(orchestrator, '_execute_specialist', execute)

    def invoke(**kwargs):
        options = {'trace_id': 'trace-A', 'prebuilt_context': '## 持仓\n相同持仓'}
        options.update(kwargs)
        return json.loads(orchestrator._execute_specialist_cached(
            'consult_risk_assessor', '请检查风险', **options))

    return invoke, calls, cache


def test_same_trace_and_context_reuses_result(cached_expert):
    invoke, calls, _ = cached_expert
    first = invoke()
    second = invoke()
    assert first['analysis'] == second['analysis']
    assert second['_cached'] is True
    assert len(calls) == 1


def test_different_traces_do_not_share_private_tool_results(cached_expert):
    invoke, calls, _ = cached_expert
    invoke()
    second = invoke(trace_id='trace-B')
    assert len(calls) == 2
    assert second['tool_calls'][0]['result'] == 'trace-B'


def test_full_context_is_part_of_key_before_role_filtering(cached_expert):
    invoke, calls, _ = cached_expert
    invoke(prebuilt_context='## 持仓\n相同持仓\n## 用户风险画像\nA的约束')
    invoke(prebuilt_context='## 持仓\n相同持仓\n## 用户风险画像\nB的不同约束')
    assert len(calls) == 2


def test_database_paths_do_not_share_cached_expert(cached_expert, monkeypatch, tmp_path):
    from db import _conn
    invoke, calls, _ = cached_expert
    invoke()
    monkeypatch.setattr(_conn, 'DB_PATH', tmp_path / 'other.db')
    invoke()
    assert len(calls) == 2


def test_missing_trace_bypasses_cache(cached_expert):
    invoke, calls, cache = cached_expert
    invoke(trace_id='')
    invoke(trace_id='')
    assert len(calls) == 2
    assert cache.stats['size'] == 0


@pytest.mark.parametrize('setting', ['budget', 'model', 'routing', 'configuration', 'agent'])
def test_runtime_changes_do_not_reuse_previous_result(cached_expert, monkeypatch, setting):
    from db.config import update_config
    invoke, calls, _ = cached_expert
    monkeypatch.setattr(orchestrator, '_is_cost_routing_enabled', lambda: True)
    monkeypatch.setattr(orchestrator, '_get_model_for_agent', lambda *args: 'mimo-original')
    invoke()
    options = {}
    if setting == 'budget':
        options['budget_mode'] = 'conservative'
    elif setting == 'model':
        monkeypatch.setattr(orchestrator, '_get_model_for_agent', lambda *args: 'mimo-new')
    elif setting == 'routing':
        monkeypatch.setattr(orchestrator, '_is_cost_routing_enabled', lambda: False)
    elif setting == 'agent':
        monkeypatch.setattr(orchestrator, 'load_specialist_agents', lambda: {
            'risk_assessor': {'system_prompt': '变更后的风险策略', 'tools': ['query_portfolio']}})
    else:
        update_config('llm.temperature_agent', '0.1')
    invoke(**options)
    assert len(calls) == 2
