"""会话205—207的原始错误模式；仅使用隔离数据库与结构化证据。"""
import copy

import pytest


def _snapshot():
    return {"as_of": "2026-10-21T11:30:00", "user_id": "default",
            "invested_value": 25000, "cash_balance": 75000,
            "target_equity_ratio": .15,
            "funds": [{"fund_code": "020256", "fund_name": "机器人", "fund_category": "equity",
                       "current_value": 25000, "shares": 1000}]}


def test_portfolio_context_excludes_four_hypothetical_buys_and_system_rows():
    from db._conn import _get_conn
    from services.portfolio.portfolio_context import build_portfolio_context
    conn = _get_conn()
    rows = [(680, "020256", "假设机器人", 4385.71, "confirmed", 0, 1),
            (681, "013309", "假设恒生科技", 8381.31, "confirmed", 0, 1),
            (682, "012725", "假设畜牧养殖", 4970.28, "confirmed", 0, 1),
            (683, "008929", "假设消费红利", 4248.77, "confirmed", 0, 1),
            (684, "000001", "系统迁移记录", 999, "confirmed", 1, 0),
            (685, "000002", "真实已结算", 100, "settled", 0, 0),
            (686, "000003", "真实待确认", 200, "pending", 0, 0),
            (687, "000004", "真实已确认", 300, "confirmed", 0, 0)]
    conn.executemany("""INSERT INTO portfolio_transactions
        (id,user_id,fund_code,fund_name,transaction_type,amount,transaction_date,status,is_system,is_hypothetical)
        VALUES (?, 'default', ?, ?, 'buy', ?, '2026-10-20', ?, ?, ?)""", rows)
    conn.commit()
    conn.close()
    text = build_portfolio_context(snapshot=_snapshot())
    for row in rows[:5]:
        assert row[2] not in text
    assert "### 最近真实成交" in text
    assert "真实已结算" in text and "真实已确认" in text
    assert "### 待确认交易（不计入成交或冷却期）" in text
    completed, pending = text.split("### 待确认交易（不计入成交或冷却期）")
    assert "真实待确认" not in completed
    assert "真实待确认" in pending


@pytest.mark.parametrize("text", [
    "除非先降低权益目标，否则不能加仓。",
    "先降低目标权益比例，就可以继续买入。",
])
def test_lower_equity_target_cannot_create_buy_headroom_when_already_overweight(text):
    from services.conversation.factual_checks import check_final_facts
    assert "equity_target_direction_error" in check_final_facts(text, _snapshot(), [])


@pytest.mark.parametrize("text", [
    "降低权益目标不能为加仓腾出空间，应降低实际权益敞口。",
    "如果权益已超目标，应先降低实际权益敞口，再评估是否加仓。",
    "不应通过降低权益目标来支持加仓。",
    "旧报告称除非先降低权益目标，否则不能加仓，这个说法有误。",
    "如果有人声称除非降低权益目标，否则不能加仓，需要先核验逻辑。",
])
def test_equity_direction_guard_respects_negation_and_actual_exposure(text):
    from services.conversation.factual_checks import check_final_facts
    assert check_final_facts(text, _snapshot(), []) == []


def _quotes():
    return [{"name": "eastmoney_finance_data", "result": {
        "market_observations": [{"index_name": "机器人", "change_pct": 2.98,
                                 "current_point": 1000, "as_of": "2026-10-20 15:00"}]}}]


@pytest.mark.parametrize("text", [
    "机器人今日反弹2.98%，所以你上午说的大跌不成立。",
    "机器人上一交易日上涨2.98%，因此今天上午并没有大跌。",
])
def test_yesterday_quote_cannot_be_used_as_today_or_disprove_intraday_observation(text):
    from services.conversation.factual_checks import check_final_facts
    assert "stale_quote_as_current" in check_final_facts(text, _snapshot(), _quotes())


@pytest.mark.parametrize("text", [
    "机器人上一交易日上涨2.98%，不能反驳今天上午的大跌，盘中行情待核实。",
    "如果机器人今日反弹2.98%，仍需核实盘中行情。",
    "机器人昨日上涨2.98%；今日行情尚未取得。",
])
def test_old_quote_date_or_hypothesis_is_not_an_error(text):
    from services.conversation.factual_checks import check_final_facts
    assert check_final_facts(text, _snapshot(), _quotes()) == []


def _hypothetical():
    return [{"name": "query_transaction_history", "result": {"transactions": [
        {"id": 680, "fund_code": "020256", "fund_name": "机器人", "amount": 4385.71,
         "transaction_date": "2026-10-20", "transaction_type": "buy", "status": "confirmed",
         "is_hypothetical": 1}]}}]


@pytest.mark.parametrize("text", [
    "2026-10-20你已确认买入机器人4385.71元，因此触发冷却期。",
    "交易680已真实执行，近期应遵守冷却期。",
])
def test_identified_hypothetical_transaction_is_not_real_or_a_cooldown_trigger(text):
    from services.conversation.factual_checks import check_final_facts
    calls = _hypothetical()
    before = copy.deepcopy(calls)
    assert "nonreal_trade_as_executed" in check_final_facts(text, _snapshot(), calls)
    assert calls == before


@pytest.mark.parametrize("text", [
    "机器人4385.71元是假设买入，不计入真实成交或冷却期。",
    "假如机器人已确认买入4385.71元，则应检查冷却期。",
    "2026-10-20机器人4385.71元的方案未执行。",
])
def test_nonreal_trade_guard_does_not_reject_clear_hypothetical_or_negation(text):
    from services.conversation.factual_checks import check_final_facts
    assert check_final_facts(text, _snapshot(), _hypothetical()) == []


def test_service_layer_checks_unrequested_plan_but_not_denied_or_requested_plan():
    from services.conversation.factual_checks import check_final_facts
    query = "为什么一直跌，有人在做空吗"
    assert "unrequested_trade_plan" in check_final_facts("建议每期买入2000元。", original_query=query)
    assert check_final_facts("不要每期买入2000元，应先核实原因。", original_query=query) == []
    assert check_final_facts("建议每月定投1000元。", original_query="每月应该定投多少") == []


def test_absent_evidence_is_unverified_not_factual_contradiction():
    from services.conversation.factual_checks import check_final_fact_status
    result = check_final_fact_status("下跌主要是资金撤离与情绪踩踏。", _snapshot(), [])
    assert result["issues"] == []
    assert "market_causation_unverified" in result["unverified"]


@pytest.mark.parametrize("text", [
    "没有资金流和卖空数据。下跌主要是资金撤离与情绪踩踏。",
    "资金流数据未取得。已经可以确定是做空导致下跌。",
])
def test_explicit_missing_data_cannot_support_certain_market_cause(text):
    from services.conversation.factual_checks import check_final_facts
    assert "unsupported_market_causation" in check_final_facts(text, _snapshot(), [])


@pytest.mark.parametrize("text", [
    "没有资金流和卖空数据，不能确定是做空导致下跌。",
    "没有资金流数据。资金撤离与情绪踩踏只是假设，仍需验证。",
    "不能因没有资金流数据而确定是情绪踩踏。",
])
def test_market_cause_hypotheses_and_denials_are_not_errors(text):
    from services.conversation.factual_checks import check_final_facts
    assert check_final_facts(text, _snapshot(), []) == []


def test_available_quote_date_is_relative_to_analysis_snapshot_not_wall_clock():
    from services.conversation.factual_checks import check_final_facts
    calls = [{"name": "eastmoney_finance_data", "result": {
        "index_name": "恒生科技", "current_point": 4045.91, "as_of": "2026-10-20 16:08"}}]
    assert "market_quote_falsely_missing" in check_final_facts(
        "恒生科技实时点位本轮未取得。", {"as_of": "2026-10-20T17:00:00"}, calls)


def _mixed_transactions():
    from datetime import date
    from db._conn import _get_conn
    conn = _get_conn()
    # 同标的的pending及系统记录不能成为已成交次数或最近成交价。
    rows = [(1, "default", "A", "confirmed", 0, 0, 10),
            (2, "default", "A", "settled", 0, 0, 11),
            (3, "default", "A", "pending", 0, 0, 12),
            (4, "default", "A", "submitted", 0, 0, 13),
            (5, "default", "A", "confirmed", 1, 0, 14),
            (6, "default", "A", "confirmed", 0, 1, 15),
            (7, "default", "B", "pending", 0, 1, 16),
            (8, "other", "C", "pending", 0, 0, 17),
            (9, "default", "D", "pending", 1, 0, 18)]
    conn.executemany("""INSERT INTO portfolio_transactions
        (id,user_id,fund_code,status,is_system,is_hypothetical,price,amount,transaction_type,transaction_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, 100, 'buy', ?)""", [(*r, date.today().isoformat()) for r in rows])
    conn.commit()
    conn.close()


def test_daily_advisor_recent_transactions_and_pending_are_real_user_rows():
    from services.advisor.daily_position_advisor import _get_recent_transactions, _get_pending_decisions, _get_last_buy_price
    _mixed_transactions()
    recent = _get_recent_transactions("default")
    assert {r["id"] for r in recent} == {1, 2, 3, 4}
    assert _get_pending_decisions("default")["pending_tx_funds"] == {"A"}
    assert _get_last_buy_price("default", "A", recent) == 11


def test_cooldown_only_counts_real_confirmed_or_settled_and_last_price_is_real():
    from services.advisor.smart_add_planner import _check_cooldown, _get_last_buy_price
    _mixed_transactions()
    allowed, count, amount, _ = _check_cooldown("A", {"user_id": "default", "max_buys_in_cooldown": 3})
    assert allowed is True
    assert (count, amount) == (2, 200)
    assert _get_last_buy_price("A") == 11


def test_portfolio_pending_actions_are_scoped_to_user_and_real_records():
    from services.portfolio.portfolio_fact_layer import _build_recent_decisions
    _mixed_transactions()
    actions = _build_recent_decisions(snapshot={"user_id": "default"})["pending_actions"]
    assert len(actions) == 2
    assert {a["fund_code"] for a in actions} == {"A"}


def test_daily_cooldown_does_not_count_pending_transactions():
    from services.advisor.daily_position_advisor import _get_recent_transactions, _count_recent_buys
    _mixed_transactions()
    assert _count_recent_buys("A", _get_recent_transactions("default"), 10) == 2


def test_behavior_diagnosis_only_receives_real_completed_transactions(monkeypatch):
    from services.portfolio import behavior_diagnosis as module
    _mixed_transactions()
    captured = []
    def disposition(rows, holdings):
        captured.extend(rows)
        return {"score": 0}
    monkeypatch.setattr(module, "_diagnose_disposition", disposition)
    monkeypatch.setattr(module, "_diagnose_anchoring", lambda *a: {"score": 0})
    monkeypatch.setattr(module, "_diagnose_herding", lambda *a: {"score": 0})
    monkeypatch.setattr(module, "_diagnose_overtrading", lambda *a: {"score": 0})
    monkeypatch.setattr(module, "_build_suggestions", lambda *a: [])
    module.diagnose_behavior()
    assert {r["id"] for r in captured} == {1, 2}


def test_monthly_budget_keeps_real_pending_reservations_but_excludes_system_and_hypothetical():
    from services.advisor.smart_add_planner import _get_current_month_buy_amount
    _mixed_transactions()
    assert _get_current_month_buy_amount("default") == 400


def test_nonreal_guard_does_not_disprove_an_ambiguous_same_day_real_buy():
    from services.conversation.factual_checks import check_final_facts
    calls = _hypothetical()
    real = {**calls[0]["result"]["transactions"][0], "id": 690, "is_hypothetical": 0}
    calls[0]["result"]["transactions"].append(real)
    assert check_final_facts("2026-10-20你已确认买入机器人4385.71元。", _snapshot(), calls) == []


def test_stale_quote_guard_respects_explicit_retraction():
    from services.conversation.factual_checks import check_final_facts
    assert check_final_facts("旧报告说机器人今日上涨2.98%，这个说法有误。", _snapshot(), _quotes()) == []


def test_incomplete_trade_evidence_is_unverified_instead_of_disproved():
    from services.conversation.factual_checks import check_final_fact_status
    result = check_final_fact_status("交易680已真实执行，触发冷却期。", _snapshot(), [
        {"name": "query_transaction_history", "result": '{"transactions":['}])
    assert result["issues"] == []
    assert "trade_execution_unverified" in result["unverified"]


def test_identified_real_execution_does_not_need_additional_verification():
    from services.conversation.factual_checks import check_final_fact_status
    calls = _hypothetical()
    calls[0]["result"]["transactions"][0]["is_hypothetical"] = 0
    result = check_final_fact_status("交易680已真实执行。", _snapshot(), calls)
    assert result == {"issues": [], "unverified": []}


def test_current_fund_flow_evidence_does_not_require_missing_data_verification():
    from services.conversation.factual_checks import check_final_fact_status
    calls = [{"name": "eastmoney_finance_data", "result": {
        "market_observations": [{"index_name": "恒生科技", "net_outflow": 1000000,
                                 "as_of": "2026-10-21 11:00", "unit": "CNY"}]}}]
    result = check_final_fact_status("恒生科技下跌主要由资金撤离导致。", _snapshot(), calls)
    assert result == {"issues": [], "unverified": []}


@pytest.mark.parametrize("flow", [
    {"index_name": "恒生科技", "net_outflow": 1000000, "as_of": "2026-10-20 11:00"},
    {"index_name": "上证指数", "net_outflow": 1000000, "as_of": "2026-10-21 11:00"},
    {"index_name": "恒生科技", "net_outflow": None, "as_of": "2026-10-21 11:00"},
    {"index_name": "恒生科技", "net_outflow": 0, "as_of": "2026-10-21 11:00"},
])
def test_stale_other_subject_or_empty_flow_does_not_verify_current_cause(flow):
    from services.conversation.factual_checks import check_final_fact_status
    result = check_final_fact_status("恒生科技下跌主要由资金撤离导致。", _snapshot(), [
        {"name": "eastmoney_finance_data", "result": {"market_observations": [flow]}}])
    assert "market_causation_unverified" in result["unverified"]
