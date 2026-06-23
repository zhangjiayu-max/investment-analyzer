"""理财决策闭环 Phase 3：复盘统计测试。"""

from db.decisions import create_decision, get_decision_stats, record_decision_review


def test_decision_stats_counts_statuses_and_reviews(tmp_db):
    helpful_id = create_decision(
        source_type="manual",
        decision_type="add",
        target_type="fund",
        target_code="000001",
        target_name="测试基金",
        summary="加仓测试基金",
        status="executed",
    )
    neutral_id = create_decision(
        source_type="manual",
        decision_type="watch",
        target_type="fund",
        target_code="000002",
        target_name="观察基金",
        summary="观察测试基金",
        status="executed",
    )
    create_decision(
        source_type="manual",
        decision_type="sell",
        target_type="fund",
        target_code="000003",
        target_name="卖出基金",
        summary="卖出测试基金",
        status="proposed",
    )
    record_decision_review(helpful_id, "helpful", profit_change=300, lesson="分批执行有效")
    record_decision_review(neutral_id, "neutral", profit_change=-100, lesson="观察条件要更清晰")

    stats = get_decision_stats()

    assert stats["total"] == 3
    assert stats["by_status"]["reviewed"] == 2
    assert stats["by_status"]["proposed"] == 1
    assert stats["reviewed"] == 2
    assert stats["helpful_reviews"] == 1
    assert stats["unhelpful_reviews"] == 0
    assert stats["review_helpful_rate"] == 50
    assert stats["total_profit_change"] == 200
    assert stats["avg_profit_change"] == 100
    assert stats["by_decision_type"]["add"] == 1
    assert "分批执行有效" in stats["recent_lessons"][0]
