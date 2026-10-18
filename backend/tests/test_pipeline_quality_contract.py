"""流水线审查与完成状态的回归。"""
from types import SimpleNamespace
from unittest.mock import Mock


def test_action_review_cannot_be_skipped_by_simple_complexity(monkeypatch):
    from agent.core import orchestrator
    monkeypatch.setattr(orchestrator, 'get_orchestration_config', lambda key, default='': default)
    assert orchestrator.should_run_cross_review(
        [{'agent_key':'risk', 'analysis':'建议减仓基金014846'}, {'agent_key':'allocation', 'analysis':'持有'}],
        'simple', {}, min_specialists=1)


def test_confidence_does_not_rise_just_from_expert_count():
    from agent.core.pipeline import _compute_confidence
    assert _compute_confidence(None, 3) <= _compute_confidence(None, 1)


def test_effective_complexity_includes_plan_and_multiple_experts():
    from agent.core.pipeline import _effective_complexity
    assert _effective_complexity('simple', {'plan':{'complexity':'complex'}}, []) == 'complex'
    assert _effective_complexity('simple', {}, [{'agent_key':'a'},{'agent_key':'b'}]) == 'medium'
