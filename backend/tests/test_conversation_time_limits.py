"""专家阶段时限和有限补跑次数。"""
import pytest


def test_default_limits_leave_synthesis_time_after_specialist_phase():
    """专家阶段共享时限必须明显小于 abort 总时限，否则审阅与综合拿不到预算。

    实测单专家耗时 P99=153s、峰值 274s（857 次样本），300s 足以覆盖；
    旧默认 600s 会占掉 900s 总时限的 2/3。
    """
    from db.config import get_config_int
    specialist = get_config_int('agent.specialist_timeout')
    abort_seconds = get_config_int('conversation.abort_at_minutes') * 60
    assert abort_seconds == 900
    assert specialist <= abort_seconds / 2, "专家阶段不应占掉总时限的一半以上"
    assert abort_seconds - specialist >= 300, "至少留 300s 给审阅与综合"


@pytest.mark.parametrize('configured, expected', [(-5, 0), (0, 0), (1, 1), (1000, 1)])
def test_self_reflection_has_at_most_one_retry(monkeypatch, configured, expected):
    from agent.infra.self_reflection import get_max_retry
    import db.config
    monkeypatch.setattr(db.config, 'get_config_int', lambda *a: configured)
    assert get_max_retry() == expected
