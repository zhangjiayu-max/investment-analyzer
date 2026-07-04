"""统一对话上下文构建测试。"""

import json

from db import (
    add_cash,
    create_conversation,
    create_decision,
    create_holding,
    create_message,
    save_conversation_summary,
)


def test_build_context_includes_portfolio_summary_decisions_and_recent_messages(tmp_db):
    from services.conversation_context import build_conversation_context

    conv_id = create_conversation(title="测试对话")
    create_message(conv_id, "user", "之前我问过医疗基金要不要加仓")
    assistant_msg_id = create_message(conv_id, "assistant", "当时建议先观察估值和仓位")
    create_message(conv_id, "user", "现在还能继续加吗？")
    save_conversation_summary(conv_id, assistant_msg_id, "用户关注医疗基金加仓，但要求控制单次投入。")

    create_holding(
        "000001",
        "测试医疗基金",
        shares=1000,
        cost_price=1.2,
        current_price=1.0,
        index_code="399989",
        index_name="中证医疗",
    )
    add_cash("default", 5000)
    create_decision(
        source_type="chat",
        source_id=2,
        decision_type="add",
        target_type="fund",
        target_code="000001",
        target_name="测试医疗基金",
        summary="测试医疗基金分批加仓草案",
        rationale="估值较低但需要控制仓位",
        evidence={"data_points": [{"name": "估值", "value": "20%"}]},
        actions=[
            {
                "action_type": "pre_trade_check",
                "title": "检查现金和仓位",
                "params": {"amount": 1000, "fund_code": "000001"},
            }
        ],
    )

    bundle = build_conversation_context(
        conversation_id=conv_id,
        current_user_message="现在还能继续加吗？",
        scenario_type="buy_decision",
        rag_context="医疗估值处于历史较低区间。",
        token_budget=4000,
    )

    assert bundle["scenario_type"] == "buy_decision"
    assert bundle["sections"]["conversation_summary"].startswith("用户关注医疗基金")
    assert "测试医疗基金" in bundle["sections"]["portfolio_context"]
    assert "现金" in bundle["sections"]["portfolio_context"]
    assert "测试医疗基金分批加仓草案" in bundle["sections"]["decision_context"]
    assert "现在还能继续加吗" in bundle["sections"]["recent_messages"]
    assert "医疗估值处于历史较低区间" in bundle["sections"]["rag_context"]
    assert "当前问题" in bundle["prompt_context"]


def test_build_context_marks_missing_context_for_trade_decision(tmp_db):
    from services.conversation_context import build_conversation_context

    conv_id = create_conversation(title="测试对话")
    create_message(conv_id, "user", "可以买这个基金吗？")

    bundle = build_conversation_context(
        conversation_id=conv_id,
        current_user_message="可以买这个基金吗？",
        scenario_type="buy_decision",
        token_budget=1200,
    )

    missing = bundle["sections"]["missing_context"]
    assert "资金用途" in missing
    assert "目标仓位" in missing
    assert "当前问题" in bundle["prompt_context"]
    assert len(bundle["prompt_context"]) < 5000
