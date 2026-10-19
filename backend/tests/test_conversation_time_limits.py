"""专家阶段时限和有限补跑次数。"""
import pytest


def test_default_limits_leave_synthesis_time_after_ten_minute_specialist_phase():
    from db.config import get_config_int
    assert get_config_int('agent.specialist_timeout') == 600
    assert get_config_int('conversation.abort_at_minutes') * 60 == 900


@pytest.mark.parametrize('configured, expected', [(-5, 0), (0, 0), (1, 1), (1000, 1)])
def test_self_reflection_has_at_most_one_retry(monkeypatch, configured, expected):
    from agent.infra.self_reflection import get_max_retry
    import db.config
    monkeypatch.setattr(db.config, 'get_config_int', lambda *a: configured)
    assert get_max_retry() == expected
