"""对话205曾出现的明确逻辑越界不能通过最终门禁。"""
import pytest


@pytest.mark.parametrize("text,issue", [
    ("本轮没有资金流向数据。低估值下持续下跌更符合资金撤离与情绪踩踏，而非基本面恶化。", "unsupported_market_causation"),
    ("一年跌31.64%，最近一月跌6.86%，所以跌幅明显收窄甚至企稳。", "incomparable_return_windows"),
    ("假设再跌30%，最大损失11076元。", "scenario_is_not_maximum_loss"),
])
def test_known_attribution_reasoning_errors_are_flagged(text, issue):
    from services.conversation.factual_checks import check_final_facts
    assert issue in check_final_facts(text, {}, [])


@pytest.mark.parametrize("text", [
    "没有资金流向数据，资金撤离只是待验证假设，不能排除基本面恶化。",
    "一年跌31.64%，最近一月跌6.86%，不能直接据此判断企稳。",
    "假设再跌30%，该情景损失11076元；不代表最大可能损失。",
    "低估值不代表基本面没有恶化。",
])
def test_hypothesis_and_appropriate_limits_do_not_fail_gate(text):
    from services.conversation.factual_checks import check_final_facts
    assert check_final_facts(text, {}, []) == []


def test_available_market_quote_must_not_be_claimed_missing():
    from services.conversation.factual_checks import check_final_facts
    calls = [{"name": "eastmoney_finance_data", "arguments": {"code": "HSTECH"},
              "result": {"data": {"index_name": "恒生科技", "current_point": 4045.91, "as_of": "2026-10-20 16:08"}}}]
    assert "market_quote_falsely_missing" in check_final_facts("恒生科技实时点位本轮未取得。", {"as_of": "2026-10-20"}, calls)
    assert check_final_facts("本轮未取得恒生互联网实时点位。", {}, calls) == []


def test_final_gate_rejects_unrequested_trade_plan():
    from agent.core.pipeline import _validate_final_facts
    from agent.state.pipeline_state import PipelineState
    state = PipelineState(original_query="为什么港股一直在跌", refined_query="为什么港股一直在跌")
    answer, status, confidence, issues = _validate_final_facts(state, "建议每月定投1000元。", "completed", .8, [], [])
    assert status == "degraded"
    assert "unrequested_trade_plan" in issues


def test_conflicting_current_actions_cannot_pass_action_gate():
    from services.conversation.quality_contract import action_constraint_issues
    actions = [{"type": "BUY", "target_code": "013128", "amount": 1000},
               {"type": "SELL", "target_code": "013128", "amount": 1000}]
    assert "conflicting_asset_actions" in action_constraint_issues(actions, [])


def test_valid_low_percentile_is_not_mistaken_for_fractional_ratio():
    from tools import _validate_tool_result
    import json
    assert _validate_tool_result("query_valuation", json.dumps({"percentile": 1.3, "percentile_scale": 100}))[0]


@pytest.mark.parametrize("value", [620, -3, float("nan")])
def test_real_nested_valuation_shape_cannot_hide_invalid_percentile(value):
    from tools import _validate_tool_result
    import json
    raw = [{"index_name": "恒生科技", "metrics": [{"metric_type": "市销率", "percentile": value}]}]
    assert _validate_tool_result("query_valuation", json.dumps(raw))[0] is False


def test_eastmoney_named_columns_are_exposed_as_quote_evidence(monkeypatch):
    import json
    import tools
    from services.conversation.factual_checks import check_final_facts
    original = {"success": True, "data": {"data": {"searchDataResultDTO": {"dataTableDTOList": [{
        "code": "HSTECH.HI", "entityName": "恒生科技指数(HSTECH.HI)(指数)",
        "table": {"ZXJ_f2_3": ["4045.91"], "headName": ["2026-10-20 16:08"]},
        "nameMap": {"ZXJ_f2_3": "最新价"}}]}}}}
    monkeypatch.setattr(tools, "_eastmoney_finance_data", lambda args: json.dumps(original))
    result = json.loads(tools._execute_tool_impl("eastmoney_finance_data", {"query": "恒生科技最新点位"}))
    observation = result["market_observations"][0]
    assert observation["current_point"] == 4045.91
    assert observation["as_of"] == "2026-10-20 16:08"
    calls = [{"name": "eastmoney_finance_data", "result": result}]
    assert "market_quote_falsely_missing" in check_final_facts("恒生科技实时点位本轮未取得。", {"as_of": "2026-10-20"}, calls)
