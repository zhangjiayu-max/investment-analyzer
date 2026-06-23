"""理财决策闭环全量补完测试。"""

from datetime import datetime, timedelta

from db import create_decision, get_decision, record_decision_review, update_user_profile
from db.decisions import (
    build_decision_precheck,
    create_candidate_from_structured_recommendation,
    create_decision_from_candidate,
    create_recommendation_candidate,
    defer_recommendation_candidate,
    expire_recommendation_candidates,
    get_recommendation_candidate,
    list_recommendation_candidates,
)


def test_candidate_full_schema_dedupe_defer_and_expire(tmp_db):
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    first_id = create_recommendation_candidate(
        source_type="tool",
        source_id=7,
        scenario_type="dca",
        action_type="dca",
        target_type="fund",
        target_code="000001",
        target_name="测试基金",
        summary="测试基金定投优化",
        suggested_amount=800,
        suggested_ratio=0.2,
        review_at=tomorrow,
        expires_at=yesterday,
        dedupe_key="dca:000001",
        priority=8,
        source_snapshot={"fear_greed": 25},
    )
    second_id = create_recommendation_candidate(
        source_type="tool",
        source_id=7,
        scenario_type="dca",
        action_type="dca",
        target_type="fund",
        target_code="000001",
        target_name="测试基金",
        summary="重复建议",
        dedupe_key="dca:000001",
    )

    assert second_id == first_id
    item = get_recommendation_candidate(first_id)
    assert item["suggested_ratio"] == 0.2
    assert item["review_at"] == tomorrow
    assert item["priority"] == 8
    assert item["source_snapshot_json"]["fear_greed"] == 25

    assert defer_recommendation_candidate(first_id, tomorrow) is True
    assert get_recommendation_candidate(first_id)["status"] == "deferred"

    expired = expire_recommendation_candidates()
    assert expired == 1
    assert get_recommendation_candidate(first_id)["status"] == "expired"


def test_structured_tool_candidate_can_be_saved_as_dca_decision(tmp_db):
    candidate_id = create_candidate_from_structured_recommendation({
        "source_type": "tool",
        "source_id": 12,
        "scenario_type": "dca_optimization",
        "action_type": "dca",
        "target_type": "fund",
        "target_code": "000002",
        "target_name": "定投基金",
        "summary": "估值偏低，提升月定投",
        "reason": "估值和情绪均支持",
        "suggested_amount": 1500,
        "confidence": "high",
        "evidence": {"pe_percentile": "20%"},
        "risks": {"notes": ["继续下跌"]},
        "dedupe_key": "dca_optimization:000002",
    })

    result = create_decision_from_candidate(candidate_id, review_days=60)

    assert result["ok"] is True
    decision = result["decision"]
    assert decision["decision_type"] == "dca"
    assert decision["suitability_json"]["suggested_amount"] == 1500
    assert get_recommendation_candidate(candidate_id)["status"] == "saved"


def test_precheck_warns_for_missing_counterarguments_stale_evidence_and_unreviewed_duplicate(tmp_db):
    old_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
    first_id = create_decision(
        source_type="manual",
        decision_type="add",
        target_type="fund",
        target_code="000003",
        target_name="重复基金",
        summary="第一次加仓",
        status="accepted",
        evidence={"data_points": [{"name": "估值", "as_of": old_date}]},
        risk={"notes": []},
    )
    second_id = create_decision(
        source_type="manual",
        decision_type="add",
        target_type="fund",
        target_code="000003",
        target_name="重复基金",
        summary="第二次加仓",
        evidence={"data_points": [{"name": "估值", "as_of": old_date}]},
        risk={"notes": []},
    )

    result = build_decision_precheck(second_id)

    joined = "；".join(result["warnings"] + result["blockers"])
    assert "反方观点" in joined
    assert "数据" in joined and ("过期" in joined or "较旧" in joined)
    assert f"#{first_id}" in joined


def test_review_updates_user_profile_patterns_and_biases(tmp_db):
    update_user_profile("default", positive_patterns="", negative_patterns="", behavior_biases=[])
    decision_id = create_decision(
        source_type="manual",
        decision_type="sell",
        target_type="fund",
        target_code="000004",
        target_name="复盘基金",
        summary="恐慌卖出复盘基金",
        status="executed",
    )

    record_decision_review(
        decision_id,
        "unhelpful",
        result_note="卖出后很快反弹，属于情绪化止损",
        profit_change=-500,
        lesson="下跌时先看估值和仓位，不要情绪化止损",
    )

    from db import get_user_profile
    profile = get_user_profile("default")
    assert "情绪化止损" in (profile.get("negative_patterns") or "")
    assert "panic_sell" in (profile.get("behavior_biases") or "")
