"""206/207 预算与续写接缝的离线回归。"""
import copy
import importlib
import json
import pytest
from types import SimpleNamespace

from agent.infra.termination import get_token_budget_for_complexity
from agent.state.pipeline_state import PipelineState


def test_default_cost_and_output_budgets_are_independent():
    pipeline = importlib.import_module('agent.core.pipeline')
    state = PipelineState(token_budget=get_token_budget_for_complexity('medium'))
    assert pipeline._specialist_output_limit(state, 3) >= 6000
    # 真实首轮的输入成本仍然记账，但不会吃掉所有审查额度。
    state.record_phase_tokens('execution', 30000)
    assert pipeline._review_tokens_remaining(state) > 0


def test_effective_complexity_updates_budget_without_resetting_usage():
    pipeline = importlib.import_module('agent.core.pipeline')
    state = PipelineState(token_budget=get_token_budget_for_complexity('simple'),
                          tokens_used=500, tokens_used_by_phase={'preprocess': 500})
    pipeline._sync_complexity_budget(state, 'complex')
    assert state.token_budget == get_token_budget_for_complexity('complex')
    assert state.tokens_used == 500
    assert state.tokens_used_by_phase == {'preprocess': 500}


def test_pipeline_specialist_has_shared_cost_allowance(monkeypatch):
    pipeline = importlib.import_module('agent.core.pipeline')
    state = PipelineState(token_budget=get_token_budget_for_complexity('medium'))
    sent = []
    pipeline._run_pipeline_specialist(lambda **kwargs: sent.append(kwargs) or {'tokens_used': 1},
        None, budget_state=state, agent_key='risk')
    assert sent[0]['token_budget'] > sent[0]['max_output_tokens']
    assert sent[0]['token_budget'] <= state.token_budget['execution']


def test_synthesis_uses_configured_output_and_bounded_evidence(monkeypatch):
    pipeline = importlib.import_module('agent.core.pipeline')
    llm = importlib.import_module('services.llm_service')
    calls = []
    def call(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        return SimpleNamespace(usage=SimpleNamespace(total_tokens=100), choices=[SimpleNamespace(
            finish_reason='stop', message=SimpleNamespace(content='综合报告已经完成。'))])
    monkeypatch.setattr(llm, '_call_llm', call)
    monkeypatch.setattr(pipeline, 'get_config_int', lambda key, default: 8192 if key == 'llm.max_tokens_report' else default)
    tools = [{'name': 'query_portfolio', 'result': {'funds': [
        {'fund_code': f'{i:06}', 'current_value': i, 'records': [{'padding': '重复内容' * 1000}]}
        for i in range(30)], 'total_assets': 1029500.44}}]
    original = copy.deepcopy(tools)
    pipeline._synthesize_multiple_specialists('为什么下跌',
        [{'agent_key': 'risk', 'analysis': '没有当期资金数据。'}], None, 'trace', tool_calls=tools,
        state=PipelineState(token_budget=get_token_budget_for_complexity('medium')))
    assert calls[0]['max_tokens'] == 8192
    assert len(json.dumps(calls[0]['messages'], ensure_ascii=False)) < 40000
    assert '1029500.44' in calls[0]['messages'][0]['content']
    assert tools == original


def test_continuation_deduplicates_heading_seam_and_preserves_reasoning():
    from services.llm.text_completion import complete_text_analysis_sync
    responses = iter([
        SimpleNamespace(usage={'total_tokens': 10}, choices=[SimpleNamespace(finish_reason='length',
            message=SimpleNamespace(content='第一段正文。\n\n**分歧点 2：仲裁专家解析全部', model_extra={'reasoning_content': '继续核验'}))]),
        SimpleNamespace(usage={'total_tokens': 20}, choices=[SimpleNamespace(finish_reason='stop',
            message=SimpleNamespace(content='**分歧点 2：仲裁专家解析全部落入 unknown。', model_extra={}))]),
    ])
    sent = []
    def call(**kwargs):
        sent.append(copy.deepcopy(kwargs))
        return next(responses)
    text, tokens, status = complete_text_analysis_sync(call, messages=[{'role': 'user', 'content': '原始问题'}])
    assert text.count('分歧点 2：仲裁专家解析全部') == 1
    assert text.endswith('unknown。')
    assert sent[1]['messages'][-2]['reasoning_content'] == '继续核验'
    assert (tokens, status) == (30, 'completed')


def test_synthesis_continuations_stop_when_cost_reserve_is_exhausted(monkeypatch):
    pipeline = importlib.import_module('agent.core.pipeline')
    llm = importlib.import_module('services.llm_service')
    state = PipelineState(token_budget={'synthesis': 20000, 'max_total': 30000})
    calls = []
    def call(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(usage=SimpleNamespace(total_tokens=20050), choices=[SimpleNamespace(
            finish_reason='length', message=SimpleNamespace(content='已取得的综合正文。', reasoning_content='推理'))])
    monkeypatch.setattr(llm, '_call_llm', call)
    answer, _, completion = pipeline._synthesize_multiple_specialists('为什么跌', [], None, 't', state=state)
    assert len(calls) == 1
    assert answer.startswith('已取得的综合正文。')
    assert completion == 'truncated'
    assert state.tokens_used_by_phase['synthesis'] == 20050


def test_synthesis_does_not_resend_input_when_only_output_allowance_remains(monkeypatch):
    pipeline = importlib.import_module('agent.core.pipeline')
    llm = importlib.import_module('services.llm_service')
    state = PipelineState(token_budget={'synthesis': 20000, 'max_total': 30000})
    calls = []
    def call(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(usage=SimpleNamespace(total_tokens=19999), choices=[SimpleNamespace(
            finish_reason='length' if len(calls) == 1 else 'stop',
            message=SimpleNamespace(content='已完成的报告片段。'))])
    monkeypatch.setattr(llm, '_call_llm', call)
    answer, _, completion = pipeline._synthesize_multiple_specialists('为什么跌', [], None, 't', state=state)
    assert len(calls) == 1
    assert completion == 'truncated'
    assert answer == '已完成的报告片段。'


def test_failed_expert_and_review_reasons_reach_final_quality_issues():
    pipeline = importlib.import_module('agent.core.pipeline')
    state = PipelineState(original_query='现在可以加仓吗')
    state.set_phase_result('execution', {'specialists': [{'agent_key': 'risk', 'analysis': '截断正文',
                                                        'completion_status': 'truncated'}]})
    state.set_phase_result('reflection', {'status': 'budget_exhausted'})
    _, status, _, issues = pipeline._validate_final_facts(state, '正文已经生成。', 'completed', .8, [])
    assert status == 'degraded'
    assert 'specialist_truncated' in issues
    assert 'reflection_budget_exhausted' in issues


def test_unsupported_causation_degrades_as_unverified_not_disproved():
    pipeline = importlib.import_module('agent.core.pipeline')
    state = PipelineState(original_query='为什么港股跌')
    _, status, _, issues = pipeline._validate_final_facts(state,
        '本轮下跌更符合资金撤离加情绪踩踏特征，而非典型做空袭击。', 'completed', .8, [])
    assert status == 'degraded'
    assert 'market_causation_unverified' in issues
    assert 'unsupported_market_causation' not in issues


def test_continuation_budget_failure_retains_all_completed_fragments():
    from services.llm.text_completion import complete_text_analysis_sync, CompletionBudgetExhausted
    calls = []
    def call(**kwargs):
        calls.append(kwargs)
        if len(calls) == 3:
            raise CompletionBudgetExhausted('剩余成本不足以重发输入')
        return SimpleNamespace(usage=SimpleNamespace(total_tokens=100), choices=[SimpleNamespace(
            finish_reason='length', message=SimpleNamespace(content=f'已生成第{len(calls)}段。'))])
    text, tokens, status = complete_text_analysis_sync(call, messages=[])
    assert text == '已生成第1段。已生成第2段。'
    assert (tokens, status) == (200, 'truncated')


def test_model_view_compresses_large_json_without_mutating_evidence():
    from services.llm.context_budget import compact_model_messages
    source = {'role': 'tool', 'tool_call_id': 'abc', 'content': json.dumps({
        'fund_code': '020256', 'total_assets': 1029500.44, 'is_hypothetical': 1,
        'records': [{'padding': 'x' * 60000}], 'date': '2026-10-20'})}
    original = copy.deepcopy(source)
    view = compact_model_messages([source])[0]
    assert len(view['content']) < 6000
    result = json.loads(view['content'])
    assert result['is_hypothetical'] == 1
    assert result['total_assets'] == 1029500.44
    assert result['date'] == '2026-10-20'
    assert view['tool_call_id'] == 'abc'
    assert source == original


def test_answer_policy_loads_transaction_time_and_target_guards_from_config(monkeypatch):
    from agent.core import answer_policy
    from db import analysis
    monkeypatch.setattr(analysis, 'get_analysis_agent_by_name', lambda name: None)
    policy = answer_policy.build_answer_policy('今天上午跌了，可以加仓吗')
    assert 'is_hypothetical' in policy
    assert '上一交易日' in policy
    assert '配置目标' in policy and '硬上限' in policy
    assert '降低目标' in policy


def test_failed_empty_answer_cannot_become_readable_confidence_footer():
    from services.conversation.quality_contract import finalize_answer_event
    result = finalize_answer_event({'content': '', 'execution_status': 'failed',
                                   'completion_status': 'failed', 'confidence': .8})
    assert result['content'] == ''
    assert result['execution_status'] == 'failed'


def test_connection_error_does_not_gain_report_footer():
    from services.conversation.quality_contract import finalize_answer_event
    result = finalize_answer_event({'content': 'Connection error.', 'execution_status': 'failed',
                                   'completion_status': 'failed', 'confidence': .8})
    assert result['content'] == 'Connection error.'
    assert result['execution_status'] == 'failed'


def test_empty_synthesis_stays_failed_through_fact_guard_and_delivery():
    from services.conversation.quality_contract import finalize_answer_event
    pipeline = importlib.import_module('agent.core.pipeline')
    specialists = [{'agent_key': 'risk', 'analysis': '', 'status': 'failed'}]
    state = PipelineState(original_query='风险如何')
    state.set_phase_result('execution', {'specialists': specialists})
    text, completion, quality, confidence = pipeline._validate_synthesis('', 'failed', specialists, None, .7)
    text, completion, confidence, issues = pipeline._validate_final_facts(state, text, completion, confidence, [])
    result = finalize_answer_event({'content': text, 'completion_status': completion,
        'quality_status': quality, 'quality_issues': issues, 'confidence': confidence})
    assert result['content'] == ''
    assert result['execution_status'] == 'failed'


def test_tool_context_has_aggregate_budget_and_references_identical_evidence():
    from services.llm.context_budget import compact_model_messages
    messages = [{'role': 'tool', 'tool_call_id': str(i), 'content': json.dumps({
        'fund_code': f'{i:06}', 'date': '2026-10-20', 'content': '长篇数据' * 6000})} for i in range(12)]
    messages.append({**messages[0], 'tool_call_id': 'duplicate'})
    original = copy.deepcopy(messages)
    view = compact_model_messages(messages, total_tool_chars=18000)
    assert sum(len(m['content']) for m in view) <= 18000
    reference = json.loads(view[-1]['content'])
    first = json.loads(view[0]['content'])
    assert reference['_evidence_ref'] == first['_evidence_id']
    assert reference['_same_as_tool_call_id'] == '0'
    assert [m['tool_call_id'] for m in view] == [m['tool_call_id'] for m in messages]
    assert messages == original


def test_review_preflight_preserves_synthesis_reserve_without_sending_large_prompt():
    pipeline = importlib.import_module('agent.core.pipeline')
    state = PipelineState(token_budget=get_token_budget_for_complexity('medium'))
    with pytest.raises(pipeline._ReviewBudgetExhausted):
        pipeline._call_review_model(state, 'reflection', lambda **kw: pytest.fail('input exceeds review budget'),
            messages=[{'role': 'user', 'content': '输入' * 100000}], max_tokens=1000)
    assert state.tokens_used == 0


def test_synthesis_key_data_cannot_reintroduce_unbounded_snapshot(monkeypatch):
    pipeline = importlib.import_module('agent.core.pipeline')
    llm = importlib.import_module('services.llm_service')
    calls = []
    board = SimpleNamespace(entry_count=1, get_key_data=lambda: {'snapshot': {'records': ['噪声' * 100000], 'total_assets': 12345}},
                            find_conflicts=lambda: [], get_vetoes=lambda: [], get_portfolio_impacts=lambda: [])
    monkeypatch.setattr(llm, '_call_llm', lambda **kw: calls.append(kw) or SimpleNamespace(usage=None,
        choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='综合分析完成。'))]))
    pipeline._synthesize_multiple_specialists('为什么跌', [], board, 't')
    assert len(calls[0]['messages'][0]['content']) < 15000
    assert '12345' in calls[0]['messages'][0]['content']


def test_specialist_cost_preflight_includes_reasoning_and_tool_schema(monkeypatch):
    from agent.core import multi_agent
    monkeypatch.setattr(multi_agent, '_call_llm', lambda **kw: pytest.fail('prompt cannot fit remaining budget'))
    usage = multi_agent._SpecialistUsage(token_budget=100)
    from services.llm.text_completion import CompletionBudgetExhausted
    with pytest.raises(CompletionBudgetExhausted):
        usage.call(messages=[{'role': 'assistant', 'content': '完成', 'reasoning_content': '思考' * 5000}],
                   tools=[{'type': 'function', 'function': {'name': 'query', 'description': '定义' * 5000}}], max_tokens=80)


def test_specialist_context_metrics_record_sent_view_and_zero_usage_falls_back(monkeypatch):
    from agent.core import multi_agent
    monkeypatch.setattr(multi_agent, '_call_llm', lambda **kw: SimpleNamespace(usage={'total_tokens': 0},
        choices=[SimpleNamespace(message=SimpleNamespace(content='核实后可读正文', tool_calls=[]))]))
    usage = multi_agent._SpecialistUsage()
    usage.call(messages=[{'role': 'tool', 'tool_call_id': 'a', 'content': json.dumps({'records': ['噪声' * 20000], 'total_assets': 12345})}])
    fields = usage.fields()
    assert fields['tokens_used'] > 0
    assert fields['tokens_source'] == 'estimated'
    assert fields['context_usage']['calls'] == 1
    assert fields['context_usage']['model_message_chars'] < fields['context_usage']['raw_message_chars'] / 10


def test_provider_cost_overrun_cannot_claim_synthesis_completed(monkeypatch):
    pipeline = importlib.import_module('agent.core.pipeline')
    llm = importlib.import_module('services.llm_service')
    state = PipelineState(token_budget={'synthesis': 20000, 'max_total': 30000})
    monkeypatch.setattr(llm, '_call_llm', lambda **kw: SimpleNamespace(usage={'total_tokens': 21000},
        choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='完成报告正文。'))]))
    answer, _, completion = pipeline._synthesize_multiple_specialists('为什么跌', [], None, 't', state=state)
    assert answer == '完成报告正文。'
    assert completion == 'degraded'


def test_trace_reconciliation_counts_auxiliary_calls_once_and_isolates_other_traces():
    pipeline = importlib.import_module('agent.core.pipeline')
    from db._conn import _get_conn
    with _get_conn() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS token_usage (trace_id TEXT, total_tokens INTEGER)')
        conn.executemany('INSERT INTO token_usage(trace_id,total_tokens) VALUES (?,?)',
                         [('current', 1000), ('current', 2000), ('other', 500000)])
    state = PipelineState(trace_id='current', tokens_used=2000, tokens_used_by_phase={'execution': 2000})
    pipeline._reconcile_trace_usage(state)
    pipeline._reconcile_trace_usage(state)
    assert state.tokens_used == 3000
    assert state.tokens_used_by_phase == {'execution': 2000, 'auxiliary': 1000}
