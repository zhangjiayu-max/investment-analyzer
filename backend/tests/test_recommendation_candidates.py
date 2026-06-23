"""理财决策闭环 Phase 2：推荐候选池测试。"""

from db import create_holding
from db.decisions import (
    create_decision_from_candidate,
    create_recommendation_candidate,
    extract_recommendation_candidates_from_analysis,
    get_recommendation_candidate,
    list_recommendation_candidates,
    update_recommendation_candidate_status,
)


def test_create_and_list_recommendation_candidate(tmp_db):
    candidate_id = create_recommendation_candidate(
        source_type="analysis",
        source_id=1,
        scenario_type="panorama",
        action_type="add",
        target_type="fund",
        target_code="000001",
        target_name="测试基金",
        summary="测试基金可分批加仓",
        rationale="低估且仓位不足",
        suggested_amount=1200,
        confidence="medium",
        evidence={"snippet": "建议分批加仓测试基金"},
        risk={"notes": ["短期波动"]},
    )

    item = get_recommendation_candidate(candidate_id)
    assert item["summary"] == "测试基金可分批加仓"
    assert item["suggested_amount"] == 1200
    assert item["evidence_json"]["snippet"] == "建议分批加仓测试基金"
    items = list_recommendation_candidates(status="new")
    assert [i["id"] for i in items] == [candidate_id]


def test_update_recommendation_candidate_status(tmp_db):
    candidate_id = create_recommendation_candidate(
        source_type="analysis",
        source_id=2,
        scenario_type="deep_dive",
        action_type="watch",
        target_type="fund",
        target_code="000002",
        target_name="观察基金",
        summary="观察基金等待回撤",
    )

    assert update_recommendation_candidate_status(candidate_id, "ignored") is True
    assert get_recommendation_candidate(candidate_id)["status"] == "ignored"


def test_extract_candidates_from_analysis_matches_holdings_and_deduplicates(tmp_db):
    create_holding("000001", "测试基金", shares=100, cost_price=1, current_price=1)
    text = "建议对测试基金分批加仓，单次不超过1000元。风险是短期波动可能扩大。建议对测试基金分批加仓。"

    created = extract_recommendation_candidates_from_analysis(
        record_id=10,
        analysis_type="panorama",
        result_text=text,
    )

    assert created == 1
    item = list_recommendation_candidates(status="new")[0]
    assert item["target_code"] == "000001"
    assert item["action_type"] == "add"
    assert "分批加仓" in item["summary"]
    assert item["evidence_json"]["source"]["record_id"] == 10


def test_create_decision_from_candidate_marks_candidate_saved(tmp_db):
    candidate_id = create_recommendation_candidate(
        source_type="analysis",
        source_id=3,
        scenario_type="trade_review",
        action_type="reduce",
        target_type="fund",
        target_code="000003",
        target_name="减仓基金",
        summary="减仓基金仓位偏高，可减仓",
        rationale="集中度偏高",
    )

    result = create_decision_from_candidate(candidate_id, review_days=45)

    assert result["ok"] is True
    assert result["decision"]["source_type"] == "recommendation_candidate"
    assert result["decision"]["decision_type"] == "reduce"
    assert get_recommendation_candidate(candidate_id)["status"] == "saved"
