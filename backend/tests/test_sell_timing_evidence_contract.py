"""卖出守卫按逐标的理由核查，不以浮亏强制持有；不访问真实数据库。"""
import pytest


@pytest.fixture(autouse=True)
def tmp_db(monkeypatch):
    import db.config
    monkeypatch.setattr(db.config, 'get_config_bool', lambda key, default: True)


@pytest.mark.parametrize('reason', [
    '当前占总资产15.44%，超过用户15%的单只上限，应减持以消除集中度超限。',
    '当前风险敞口20%，超过已确认的15%风险预算，应减持以回到预算。',
    '用户已确认下月需要还款10000元，当前现金不足，应赎回以满足流动性需求。',
])
def test_loss_does_not_block_specific_portfolio_reason(reason):
    from agent.safety.sell_timing_guard import enforce_sell_timing_guard
    answer = '## 操作建议\n基金014846浮亏10%，建议减仓。' + reason
    checked, warnings = enforce_sell_timing_guard(answer, arbitration_summary={'final_stance': 'hold'})
    assert checked == answer
    assert warnings == []


def test_loss_alone_warns_without_forcing_hold_or_dca():
    from agent.safety.sell_timing_guard import enforce_sell_timing_guard
    answer = '## 操作建议\n基金014846浮亏10%，建议减仓，因为已经亏损。'
    checked, warnings = enforce_sell_timing_guard(answer)
    assert warnings
    assert '014846' in checked[len(answer):]
    assert all(phrase not in checked for phrase in ('止盈不止损', '建议改为', '坚持定投', '禁止底部割肉'))
    assert '逐标的' in checked


def test_other_funds_profit_and_valuation_cannot_authorize_sale():
    from agent.safety.sell_timing_guard import enforce_sell_timing_guard
    answer = ('## 操作建议\n基金014846亏损10%，建议减仓。\n'
              '基金161725维持持有，PE分位80%，盈利20%。')
    checked, warnings = enforce_sell_timing_guard(answer)
    assert warnings
    assert 'sell_without_percentile' in warnings
    assert '014846' in checked[len(answer):]


def test_other_sale_with_good_basis_does_not_authorize_second_sale():
    from agent.safety.sell_timing_guard import enforce_sell_timing_guard
    answer = ('## 操作建议\n建议卖出161725，PE分位80%，盈利20%，估值偏高。\n'
              '建议减仓014846。')
    checked, warnings = enforce_sell_timing_guard(answer)
    assert warnings
    assert '014846' in checked[len(answer):]
    assert '161725' not in checked[len(answer):]


def test_target_matched_evidence_before_recommendation_is_usable():
    from agent.safety.sell_timing_guard import enforce_sell_timing_guard
    answer = ('## 持仓数据\n014846占总资产15.44%，超过用户单只上限15%，集中度超限。\n'
              '161725盈利20%，PE分位80%。\n'
              '## 操作建议\n建议减仓014846，浮亏10%。')
    checked, warnings = enforce_sell_timing_guard(answer)
    assert checked == answer
    assert warnings == []


def test_table_reduce_action_is_checked_even_when_overall_hold():
    from agent.safety.sell_timing_guard import enforce_sell_timing_guard
    answer = '## 操作建议\n|基金|动作|\n|014846|减持5000元|'
    checked, warnings = enforce_sell_timing_guard(answer, arbitration_summary={'final_stance': 'hold'})
    assert warnings
    assert '014846' in checked[len(answer):]


@pytest.mark.parametrize('text', ['暂不卖出014846。', '不建议减仓014846。', '禁止赎回014846。'])
def test_negated_sell_is_not_an_action(text):
    from agent.safety.sell_timing_guard import enforce_sell_timing_guard
    answer = '## 操作建议\n' + text
    checked, warnings = enforce_sell_timing_guard(answer)
    assert checked == answer
    assert warnings == []


def test_disabled_guard_preserves_response(monkeypatch):
    import db.config
    from agent.safety.sell_timing_guard import enforce_sell_timing_guard
    monkeypatch.setattr(db.config, 'get_config_bool', lambda key, default: False)
    answer = '## 操作建议\n建议减仓014846。'
    assert enforce_sell_timing_guard(answer) == (answer, [])


@pytest.mark.parametrize('reason', ['没有基本面恶化证据', '并非高估，PE分位80%，盈利20%'])
def test_negated_reason_does_not_authorize_sale(reason):
    from agent.safety.sell_timing_guard import enforce_sell_timing_guard
    answer = '## 操作建议\n建议减仓014846，' + reason + '。'
    checked, warnings = enforce_sell_timing_guard(answer)
    assert warnings


def test_named_fund_cannot_use_other_named_funds_valuation():
    from agent.safety.sell_timing_guard import enforce_sell_timing_guard
    answer = '## 操作建议\n建议减仓白酒指数。\n恒生科技PE分位80%，盈利20%，继续持有。'
    checked, warnings = enforce_sell_timing_guard(answer)
    assert warnings
    assert '白酒指数' in checked[len(answer):]


@pytest.mark.parametrize("profit", [20, 80])
def test_malformed_percentile_warns_instead_of_crashing(profit):
    from agent.safety.sell_timing_guard import enforce_sell_timing_guard
    answer = f'## 操作建议\n建议减仓014846，PE分位 ...%，盈利{profit}%。'
    _, warnings = enforce_sell_timing_guard(answer)
    assert warnings
