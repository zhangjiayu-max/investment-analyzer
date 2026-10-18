"""逐标的动作从模型 JSON 到黑板、仲裁及候选的往返契约。"""
import copy
import json

import pytest

from agent.infra.blackboard import Blackboard, _normalize_asset_actions, extract_entry_from_result
from agent.infra.message_protocol import (
    agent_outputs_to_a2a_context, get_a2a_output_instruction,
    normalize_specialist_result, parse_agent_output,
)


def action(**changes):
    return {"target_code": "014846", "target": "目标债券基金", "action": "REDUCE",
            "confidence": .78, "evidence_refs": ["portfolio:current"],
            "reason": "按总资产口径恢复用户单标的上限", "status": "current", "amount": 4526.70,
            **changes}


def parse(actions=None, **changes):
    payload = {"conclusion": "整体持有，降低超限单只仓位。", "verdict": "hold", "portfolio_stance": "hold",
               "asset_actions": [action()] if actions is None else actions, "confidence": .64,
               "data_references": [{"source": "query_portfolio", "evidence_id": "portfolio:current"}], **changes}
    return parse_agent_output("```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```\n建议减持基金014846。",
                              "asset_allocator", "资产配置师", "analyst", "protocol-test", [], 12)


def test_agent_output_dictionary_retains_full_asset_action_and_portfolio_stance():
    output = parse()
    stored = json.loads(json.dumps(output.to_dict()))
    assert stored["asset_actions"] == [action()]
    assert stored["portfolio_stance"] == "hold"
    assert stored["verdict"] == "hold"


def test_a2a_message_payload_preserves_full_action_constraints():
    conditional = action(status="conditional", conditions=["核对赎回费率后"], amount=None)
    output = parse([conditional])
    payload = output.to_a2a_message()["payload"]
    assert payload["asset_actions"] == [conditional]
    assert payload["portfolio_stance"] == "hold"
    assert payload["verdict"] == "hold"


def test_a2a_peer_context_keeps_codes_evidence_and_conditions():
    output = parse([action(status="conditional", conditions=["必须先核对赎回费率"])])
    context = agent_outputs_to_a2a_context([output], "risk_assessor")
    assert "014846" in context
    assert "portfolio:current" in context
    assert "必须先核对赎回费率" in context
    assert "conditional" in context


def test_blackboard_normalizes_code_and_reduce_without_losing_candidate_fields():
    raw = action()
    before = copy.deepcopy(raw)
    result = _normalize_asset_actions([raw])
    assert len(result) == 1
    assert result[0]["target"] == "014846"
    assert result[0]["type"] == "SELL"
    for field in ("target_code", "confidence", "evidence_refs", "reason", "status", "amount"):
        assert result[0][field] == raw[field]
    assert raw == before


def test_blackboard_reads_nested_agent_output_and_roundtrips_actions():
    output = parse()
    wrapped = {"analysis": "建议减持基金014846。", "structured": {"structured": output.to_dict()}}
    entry = extract_entry_from_result("asset_allocator", "资产配置师", wrapped)
    assert entry.portfolio_stance == "hold"
    assert entry.asset_actions[0]["target_code"] == "014846"
    assert entry.asset_actions[0]["type"] == "SELL"
    bb = Blackboard()
    bb.write(entry)
    recovered = Blackboard.from_dict(bb.to_dict()).entries[0]
    assert recovered.asset_actions == entry.asset_actions
    assert recovered.portfolio_stance == "hold"


@pytest.mark.parametrize("changes", [
    {"status": "conditional"}, {"status": "historical"}, {"status": "rejected"},
    {"status": "negated"}, {"negated": True}, {"historical": True},
    {"condition": "先核对费用"}, {"conditions": ["穿透核验后再决定"]},
])
def test_rejected_structured_actions_cannot_be_revived_by_text_fallback(changes):
    forbidden = action(**changes)
    entry = extract_entry_from_result("asset_allocator", "资产配置师", {
        "analysis": "建议减仓基金014846。", "asset_actions": [forbidden],
    })
    assert entry.asset_actions == []
    assert entry.action_signals == []
    assert entry.portfolio_impact is None


def test_explicit_empty_asset_actions_override_legacy_buy_signals():
    entry = extract_entry_from_result("risk_assessor", "风险管理师", {
        "analysis": "建议加仓基金014846。", "asset_actions": [],
        "action_signals": [{"target": "014846", "type": "BUY"}],
    })
    assert entry.action_signals == []


def test_legacy_signal_without_asset_action_field_survives_normalization():
    legacy = {"analysis": "建议减仓基金014846。", "action_signals": [
        {"target": "014846", "type": "REDUCE", "confidence": .7}],
    }
    normalized = normalize_specialist_result(legacy)
    entry = extract_entry_from_result("asset_allocator", "资产配置师", normalized)
    assert entry.asset_actions[0]["type"] == "SELL"
    assert entry.asset_actions[0]["target"] == "014846"


def test_legacy_text_confidence_is_not_promoted_by_missing_structured_default():
    from agent.infra.blackboard import _estimate_confidence
    raw = {"analysis": "持仓分析。" * 60, "data_references": ["portfolio:current"]}
    entry = extract_entry_from_result("asset_allocator", "资产配置师", raw)
    assert entry.confidence == _estimate_confidence(raw["analysis"], [], result=raw)


def test_only_target_code_is_sufficient_for_reduce_alias():
    raw = action()
    raw.pop("target")
    assert _normalize_asset_actions([raw])[0]["type"] == "SELL"


def test_roundtrip_reaches_validated_candidate_without_direction_reversal():
    from services.conversation.quality_contract import build_chat_candidates
    normalized = normalize_specialist_result({"analysis": "建议减持基金014846。", "structured": {"structured": parse().to_dict()}})
    entry = extract_entry_from_result("asset_allocator", "资产配置师", normalized)
    candidates = build_chat_candidates(
        "建议减持基金014846。", entry.asset_actions,
        [{"name": "query_portfolio", "evidence_id": "portfolio:current", "result": {"holdings": [{"fund_code": "014846"}]}}],
    )
    assert len(candidates) == 1
    assert candidates[0]["action_type"] == "sell"
    assert candidates[0]["target_code"] == "014846"
    assert candidates[0]["suggested_amount"] == pytest.approx(4526.70)
    assert candidates[0]["evidence"]["evidence_refs"] == ["portfolio:current"]


def test_output_format_declares_fields_required_for_safe_candidate_creation():
    instruction = get_a2a_output_instruction()
    schema = instruction.split('"asset_actions": [', 1)[1].split('"portfolio_stance"', 1)[0]
    for field in ("target_code", "confidence", "evidence_refs", "reason", "status", "amount", "conditions"):
        assert f'"{field}"' in schema
