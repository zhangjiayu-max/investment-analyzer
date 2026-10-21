"""审查额度真实记账并限制后续调用；专家输出与总输入预算分离。"""
import importlib
from types import SimpleNamespace

import pytest

from agent.state.pipeline_state import PipelineState
from agent.infra.termination import get_token_budget_for_complexity


@pytest.mark.parametrize('complexity', ['simple', 'medium', 'complex'])
def test_default_budgets_have_explicit_review_reserve(complexity):
    budget = get_token_budget_for_complexity(complexity)
    assert 0 < budget['review'] < budget['max_total'] - budget['synthesis']


def response(tokens=200):
    return SimpleNamespace(usage=SimpleNamespace(total_tokens=tokens), choices=[SimpleNamespace(
        finish_reason='stop', message=SimpleNamespace(content='{"quality_issues":[],"missing_perspectives":[],"conflict_resolutions":[],"confidence_adjustment":0}'))])


def test_review_call_accounts_reported_usage_and_caps_output():
    p = importlib.import_module('agent.core.pipeline')
    state = PipelineState(token_budget={'review': 300, 'synthesis': 500, 'max_total': 3000})
    captured = []
    def call(**kwargs):
        captured.append(kwargs)
        return response(200)
    p._call_review_model(state, 'reflection', call, messages=[{'role':'user','content':'review'}], max_tokens=2400)
    from services.llm.context_budget import estimate_request_tokens
    assert captured[0]['max_tokens'] == 300 - estimate_request_tokens(captured[0]['messages'])
    assert state.tokens_used_by_phase['reflection'] == 200
    assert state.tokens_used == 200
    with pytest.raises(p._ReviewBudgetExhausted):
        p._call_review_model(state, 'debate', call, messages=[], max_tokens=1000)
    assert captured[1]['max_tokens'] == 100 - estimate_request_tokens([])
    assert state.tokens_used_by_phase['debate'] == 200
    with pytest.raises(p._ReviewBudgetExhausted):
        p._call_review_model(state, 'reflection', call, messages=[], max_tokens=1000)
    assert len(captured) == 2


def test_reflection_retries_cannot_spend_unrecorded_tokens(monkeypatch):
    p = importlib.import_module('agent.core.pipeline')
    llm = importlib.import_module('services.llm_service')
    state = PipelineState(token_budget={'review': 30000, 'synthesis': 5000, 'max_total': 60000})
    calls = []
    def call(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(usage=SimpleNamespace(total_tokens=35000), choices=[SimpleNamespace(
            finish_reason='length', message=SimpleNamespace(content='{'))])
    monkeypatch.setattr(llm, '_call_llm', call)
    result = p._phase_reflection(state, 'q', {'specialists':[{'analysis':'text'}]}, None, 't')
    assert len(calls) == 1
    assert result['status'] == 'budget_exhausted'
    assert state.tokens_used_by_phase['reflection'] == 35000


def test_model_review_cannot_spend_reserved_synthesis_tokens():
    p = importlib.import_module('agent.core.pipeline')
    state = PipelineState(token_budget={'review': 1500, 'synthesis': 2500, 'max_total': 10000},
                          tokens_used=7600, tokens_used_by_phase={'execution':7600})
    with pytest.raises(p._ReviewBudgetExhausted):
        p._call_review_model(state, 'reflection', lambda **kw: pytest.fail('budget exhausted'), messages=[])


def test_missing_provider_usage_has_conservative_nonzero_accounting():
    p = importlib.import_module('agent.core.pipeline')
    state = PipelineState(token_budget={'review': 1500, 'synthesis': 500, 'max_total': 3000})
    p._call_review_model(state, 'reflection', lambda **kw: SimpleNamespace(usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content='质量检查结论'))]), messages=[{'role':'user','content':'检查已返回事实'}])
    assert state.tokens_used_by_phase['reflection'] > 0


def test_specialist_runner_caps_output_without_default_strict_input_budget():
    p = importlib.import_module('agent.core.pipeline')
    state = PipelineState(token_budget={'execution':1200,'review':400,'synthesis':500,'max_total':3000})
    captured = []
    def specialist(**kwargs):
        captured.append(kwargs)
        return {'tokens_used':800,'analysis':'短答'}
    result = p._run_pipeline_specialist(specialist, None, budget_state=state, agent_key='market')
    assert captured[0]['max_output_tokens'] == 1200
    assert 'token_budget' not in captured[0]
    assert 'budget_state' not in captured[0]
    assert result['tokens_used'] == 800


def test_exhausted_execution_stops_additional_specialist():
    p = importlib.import_module('agent.core.pipeline')
    state = PipelineState(token_budget={'execution':1200,'review':400,'synthesis':500,'max_total':3000},
                          tokens_used=1500,tokens_used_by_phase={'execution':1500})
    result = p._run_pipeline_specialist(lambda **kwargs:pytest.fail('overbudget specialist'), None,
                                       budget_state=state,agent_key='market')
    assert result['status'] == 'budget_exhausted'
    assert result['completion_status'] == 'degraded'
    assert result['asset_actions'] == []


def test_parallel_specialists_share_output_allocation():
    p = importlib.import_module('agent.core.pipeline')
    state = PipelineState(token_budget={'execution':4000,'review':1500,'synthesis':2500,'max_total':10000})
    assert p._specialist_output_limit(state, 3) == 1333
