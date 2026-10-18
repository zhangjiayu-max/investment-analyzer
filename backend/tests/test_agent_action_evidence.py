"""动作与持久化证据不得从否定句或被截断字符串中凭空构造。"""

import json

from agent.infra.blackboard import _extract_action_signals, extract_entry_from_result


def test_negated_history_and_conditional_are_not_current_actions():
    text = "基金014846禁止加仓。历史上基金161725已经买入。若下跌则建议买入基金000300。"
    assert _extract_action_signals(text, "risk_assessor") == []


def test_actions_bind_each_sentence_target_without_first_code_leak():
    actions = _extract_action_signals("基金014846占比30%。建议减仓基金161725。建议持有基金006962。", "risk")
    assert [(a["type"], a["target"]) for a in actions] == [("SELL", "161725"), ("HOLD", "006962")]


def test_structured_actions_take_precedence_and_missing_impact_is_unknown():
    result = {"analysis": "基金014846禁止加仓，亏损30.4%，目前占比25%。",
              "asset_actions": [{"target": "014846", "action": "SELL", "reason": "集中度过高"}]}
    entry = extract_entry_from_result("risk_assessor", "风险", result)
    assert entry.action_signals[0]["type"] == "SELL"
    assert entry.portfolio_impact["current_position_pct"] is None
    assert entry.portfolio_impact["post_change_pct"] is None
    assert entry.portfolio_impact["suggested_change"] == ""


def test_arbitration_keeps_asset_actions_even_when_portfolio_stance_hold():
    from agent.core.arbitration import arbitrate_results
    results = [{"agent_key": "risk", "verdict": "hold", "asset_actions": [
        {"target": "014846", "action": "SELL", "reason": "集中度"},
        {"target": "006962", "action": "HOLD"}]}]
    result = arbitrate_results("组合风险", results)
    assert result["portfolio_stance"] == "hold"
    assert {(a["target"], a["type"]) for a in result["asset_actions"]} == {("014846", "SELL"), ("006962", "HOLD")}


def test_agent_run_large_tools_stay_valid_and_recover_evidence():
    from db.agents import create_agent_run, deserialize_agent_run_payload, get_agent_runs
    from db.conversations import create_conversation, create_message
    conv = create_conversation("证据")
    msg = create_message(conv, "assistant", "分析")
    tools = [{"name": "query_portfolio", "arguments": {"scope": "household"}, "result_preview": "中" * 6000}]
    create_agent_run(conv, msg, "risk", "风险", "风险", tool_calls=tools,
                     evidence={"verdict": "hold", "confidence": .65}, run_phase="cross_review")
    run = get_agent_runs(conv)[0]
    json.loads(run["tool_calls"])
    recovered_tools, evidence = deserialize_agent_run_payload(run["tool_calls"])
    assert recovered_tools[0]["name"] == "query_portfolio"
    assert evidence["verdict"] == "hold"
    assert evidence["confidence"] == .65
    assert run["run_phase"] == "cross_review"


def test_damaged_legacy_tools_mark_missing_evidence():
    from db.agents import deserialize_agent_run_payload
    tools, evidence = deserialize_agent_run_payload("[{'name': 'query_portfolio', 'result': 'trunc")
    assert tools == []
    assert evidence["status"] == "missing"
