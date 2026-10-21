"""205–207 审计回归：无效专家、证据与交付质量分别验收。"""
import json

import pytest

from agent.core.arbitration import arbitrate_results
from agent.eval.conversation_evaluator import ConversationQualityEvaluator


def specialist(key='risk', **fields):
    return {'agent_key': key, 'analysis': '当前证据只支持谨慎观察。' * 8, **fields}


@pytest.mark.parametrize('results', [[], [specialist(verdict='unknown', analysis='建议买入，但该片段不能形成裁决。')],
    [specialist(verdict='buy', completion_status='truncated')],
    [specialist(verdict='buy', finish_reason='length')], [specialist(verdict='sell', analysis='')]])
def test_zero_valid_votes_is_insufficient_evidence(results):
    result = arbitrate_results('是否加仓', results)
    assert result['final_stance'] == 'watch'
    assert result['arbitration_mode'] == 'insufficient_evidence'
    assert result['supporting_agents'] == []
    assert result['confidence'] == 'low'
    assert '证据不足' in result['reasoning']
    assert '立场一致' not in result['reasoning']
    assert result['voting_specialist_count'] == 0


def test_invalid_and_duplicate_experts_do_not_vote_or_create_conflicts():
    valid = specialist('a', verdict='hold')
    result = arbitrate_results('如何操作', [valid, valid, specialist('b', verdict='sell', completion_status='truncated'),
                                         specialist('arb', verdict='buy', is_arbitration=True)])
    assert result['valid_specialist_count'] == result['voting_specialist_count'] == 1
    assert result['supporting_agents'] == ['a']
    assert result['key_conflicts'] == []


def test_arbitration_mode_cannot_claim_consensus_with_opposing_votes():
    result = arbitrate_results('如何操作', [specialist('a', verdict='buy'), specialist('b', verdict='buy'), specialist('c', verdict='sell')])
    assert result['arbitration_mode'] == 'conflict'
    assert len(result['supporting_agents']) == 2


def test_watch_and_avoid_votes_are_supported_instead_of_disappearing():
    result = arbitrate_results('如何操作', [specialist('a', verdict='avoid'), specialist('b', verdict='avoid')])
    assert result['final_stance'] == 'avoid'
    assert result['supporting_agents'] == ['a', 'b']


def test_collaboration_only_counts_valid_original_specialists():
    metadata = {'complexity': 'complex', 'specialist_results': [specialist('a', completion_status='truncated'),
                specialist('b', output_quality={'valid': False}), specialist('arb', is_arbitration=True)],
                'arbitration': {'arbitration_mode': 'insufficient_evidence', 'supporting_agents': []},
                'cross_review_results': [{'status': 'budget_exhausted'}]}
    dim = ConversationQualityEvaluator()._evaluate_collaboration(metadata, [])
    assert dim.metrics['actual_specialists'] == 0
    assert dim.metrics['has_arbitration'] is False
    assert dim.metrics['has_cross_review'] is False
    assert dim.score == 0


@pytest.mark.parametrize('result', [{'error': 'upstream failed'}, '{"error":"upstream failed"}',
    {'data_points': 0, 'metric_status': 'unknown'}, {'data': {'metric_status': 'unknown', 'value': None}}])
def test_empty_or_error_tool_result_is_not_usable_data(result):
    dim = ConversationQualityEvaluator()._evaluate_data_utilization({'tool_calls': [{'name': 'valuation', 'result': result}]}, [])
    assert dim.metrics['successful_tools'] == 0
    assert dim.metrics['tool_success_rate'] == 0


def test_review_budget_exhaustion_is_unverified_not_contradicted():
    dim = ConversationQualityEvaluator()._evaluate_accuracy({'content': '根据来源，暂时观望。'}, {
        'quality_issues': ['cross_review_budget_exhausted'], 'cross_review_results': [{'status': 'budget_exhausted'}],
        'factual_validation': {'status': 'degraded', 'issues': ['cross_review_incomplete']}})
    assert dim.metrics['verification_status'] == 'unverified'
    assert dim.metrics['has_data_contradiction'] is False
    assert dim.metrics['factual_issues'] == []
    assert dim.metrics['review_issues']


def test_recovered_experts_without_synthesis_reduce_response_and_completeness():
    evaluator = ConversationQualityEvaluator()
    content = '# 原因\n根据来源，PE10，分位20%，市场波动存在风险。建议持有等待，调整配置，置信度有限。\n|指标|值|\n|PE|10|\n- **注意风险**\n' * 20
    message = {'content': content}
    metadata = {'original_query': '为什么下跌，是否有人做空', 'recovery_type': 'specialist_merge',
                'completion_status': 'failed', 'specialist_results': [specialist(completion_status='truncated')]}
    response = evaluator._evaluate_response_quality(message, metadata)
    completeness = evaluator._evaluate_completeness(message, metadata)
    assert response.score <= 50
    assert completeness.score <= 40
    assert completeness.metrics['has_final_synthesis'] is False
    assert completeness.metrics['invalid_specialists'] == 1


def test_completeness_matches_actual_attribution_question_instead_of_generic_headings():
    evaluator = ConversationQualityEvaluator()
    metadata = {'original_query': '为什么下跌，是否有人做空'}
    irrelevant = evaluator._evaluate_completeness({'content': '# 估值风险市场配置建议\nPE10，仓位20%，建议持有。' * 50}, metadata)
    relevant = evaluator._evaluate_completeness({'content': '下跌原因尚不能确定。缺少资金流与卖空数据，无法判断是否有人做空，情绪变化只能作为待验证假设。'}, metadata)
    assert relevant.score > irrelevant.score
    assert irrelevant.score < 50


def test_timeliness_requires_current_market_evidence_for_intraday_question():
    evaluator = ConversationQualityEvaluator()
    metadata = {'original_query': '今天上午大跌，现在能加仓吗', 'portfolio_snapshot': {'as_of': '2026-10-21'},
                'tool_calls': [{'name': 'eastmoney_finance_data', 'result': {'name': '机器人', 'date': '2026-10-20', 'last_price': 100, 'change_pct': 2.98}}]}
    stale = evaluator._evaluate_timeliness({'content': '截至2026-10-21，今天机器人反弹2.98%。'}, metadata)
    honest = evaluator._evaluate_timeliness({'content': '只有2026-10-20收盘行情，今天盘中行情未获取，无法判断上午跌幅。'}, metadata)
    assert stale.score < honest.score < 100
    assert stale.metrics['market_data_current'] is False
    assert stale.metrics['requires_intraday'] is True


def test_unknown_tools_cannot_prove_accuracy_even_with_verified_label():
    checks = {key: 'verified' for key in ('source_type', 'metric_type', 'unit', 'date', 'causal_evidence', 'intent')}
    dim = ConversationQualityEvaluator()._evaluate_accuracy({'content': '根据来源，PE10。'}, {
        'factual_validation': {'status': 'verified', 'checks': checks},
        'tool_calls': [{'name': 'valuation', 'result': {'data_points': 0, 'metric_status': 'unknown'}}]})
    assert dim.metrics['verification_status'] == 'unverified'


def test_evaluation_total_and_metadata_use_only_valid_experts(tmp_db):
    from db.conversations import create_conversation, create_message
    conv = create_conversation('审查未完成')
    create_message(conv, 'user', '是否加仓')
    msg = create_message(conv, 'assistant', '根据来源，等待数据核验。', json.dumps({
        'completion_status': 'degraded', 'quality_issues': ['cross_review_budget_exhausted'],
        'specialist_results': [specialist(completion_status='truncated'), specialist('arb', is_arbitration=True)]}))
    result = ConversationQualityEvaluator().evaluate(conv, msg, trigger_evolution=False)
    assert result.metadata['specialist_count'] == 0
    assert result.metadata['score_cap'] == 59
    assert result.metadata['verification_status'] == 'unverified'


def test_short_nonbusiness_output_does_not_vote_even_with_verdict():
    result = arbitrate_results('如何操作', [specialist(verdict='buy', analysis='ok')])
    assert result['arbitration_mode'] == 'insufficient_evidence'


def test_unresolved_expert_disagreement_does_not_prove_fact_contradiction():
    dim = ConversationQualityEvaluator()._evaluate_accuracy({'content': '根据来源，尚需综合专家意见。'}, {
        'unresolved_disagreements': True, 'recovery_type': 'experts_only'})
    assert dim.metrics['verification_status'] == 'unverified'
    assert dim.metrics['has_data_contradiction'] is False


def test_legacy_failed_validation_with_only_review_budget_issue_is_unverified():
    dim = ConversationQualityEvaluator()._evaluate_accuracy({'content': '根据来源，暂时观望。'}, {
        'factual_validation': {'status': 'failed', 'issues': ['cross_review_budget_exhausted']}})
    assert dim.metrics['verification_status'] == 'unverified'
    assert dim.metrics['has_data_contradiction'] is False


def test_explicit_review_budget_status_cannot_be_overridden_by_verified_label():
    checks = {key: 'verified' for key in ('source_type', 'metric_type', 'unit', 'date', 'causal_evidence', 'intent')}
    dim = ConversationQualityEvaluator()._evaluate_accuracy({'content': '根据来源，完整分析。'}, {
        'factual_validation': {'status': 'verified', 'checks': checks},
        'cross_review_status': {'status': 'budget_exhausted'}})
    assert dim.metrics['verification_status'] == 'unverified'


def test_fact_checker_nonreal_trade_failure_lowers_evaluator_accuracy():
    calls = [{'name': 'query_transaction_history', 'result': {'transactions': [
        {'id': 680, 'fund_code': '020256', 'fund_name': '机器人', 'amount': 4385.71,
         'transaction_date': '2026-10-20', 'transaction_type': 'buy', 'status': 'confirmed', 'is_hypothetical': 1}]}}]
    dim = ConversationQualityEvaluator()._evaluate_accuracy({'content': '2026-10-20你已确认买入机器人4385.71元，因此触发冷却期。'}, {
        'portfolio_snapshot': {'as_of': '2026-10-21'}, 'tool_calls': calls})
    assert dim.metrics['verification_status'] == 'failed'
    assert 'nonreal_trade_as_executed' in dim.metrics['factual_issues']
    assert dim.metrics['has_data_contradiction'] is True


def test_metadata_reports_completed_collaboration_instead_of_card_presence(tmp_db):
    from db.conversations import create_conversation, create_message
    conv = create_conversation('无效协作卡片')
    msg = create_message(conv, 'assistant', '证据不足，保守等待。', json.dumps({
        'cross_review_results': [{'status': 'budget_exhausted'}],
        'arbitration': {'arbitration_mode': 'insufficient_evidence'},
        'specialist_results': [specialist(completion_status='truncated')]}))
    result = ConversationQualityEvaluator().evaluate(conv, msg, trigger_evolution=False)
    assert result.metadata['has_cross_review'] is False
    assert result.metadata['has_arbitration'] is False


@pytest.mark.parametrize('completion,expected_cap', [('failed', 49), ('degraded', 59), ('completed', 69)])
def test_total_score_cap_distinguishes_delivery_failure_degraded_and_unverified(completion, expected_cap):
    from db.conversations import create_conversation, create_message
    conv = create_conversation('评分门槛')
    msg = create_message(conv, 'assistant', '根据来源，证据尚需核验。', json.dumps({'completion_status': completion}))
    result = ConversationQualityEvaluator().evaluate(conv, msg, trigger_evolution=False)
    assert result.metadata['score_cap'] == expected_cap


@pytest.mark.parametrize('payload', [{'error': 'unavailable'}, {'data_points': 0, 'metric_status': 'unknown'}])
def test_sell_valuation_guard_does_not_accept_failed_tool_by_name(monkeypatch, payload):
    import db.config
    monkeypatch.setattr(db.config, 'get_config_bool', lambda key, default: True)
    result = arbitrate_results('卖出吗', [specialist('valuation_expert', verdict='sell',
        analysis='PE分位数据暂缺，建议卖出以控制风险。' * 5,
        tool_calls=[{'name': 'query_valuation', 'result': payload}])])
    assert result['valuation_gap'] is True
    assert result['final_stance'] == 'hold'
    assert result['supporting_agents'] == []


def test_legacy_arbitration_key_without_flag_is_not_counted_as_expert():
    dim = ConversationQualityEvaluator()._evaluate_collaboration({
        'specialist_results': [specialist('arbitrator')], 'complexity': 'complex'}, [])
    assert dim.metrics['actual_specialists'] == 0


def test_sell_valuation_guard_accepts_complete_known_metric(monkeypatch):
    import db.config
    monkeypatch.setattr(db.config, 'get_config_bool', lambda key, default: True)
    result = arbitrate_results('卖出吗', [specialist('valuation_expert', verdict='sell',
        tool_calls=[{'name': 'query_valuation', 'result': {'current_value': 15.2, 'percentile': 80, 'metric_status': 'known'}}])])
    assert result['valuation_gap'] is False
    assert result['final_stance'] == 'sell'
    assert result['supporting_agents'] == ['valuation_expert']
