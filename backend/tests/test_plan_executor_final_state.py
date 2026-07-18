"""Plan Executor 终态行为测试。"""

import pytest

from agent.core.plan_executor import generate_plan


@pytest.fixture(autouse=True)
def tmp_db():
    yield


def test_generate_plan_includes_task_metadata():
    available_specialists = [
        {"agent_key": "valuation_expert", "name": "估值分析师", "description": "", "tools": []},
        {"agent_key": "risk_assessor", "name": "风险管理师", "description": "", "tools": []},
        {"agent_key": "allocation_advisor", "name": "资产配置师", "description": "", "tools": []},
        {"agent_key": "market_analyst", "name": "市场分析师", "description": "", "tools": []},
    ]

    plan = generate_plan(
        user_query="白酒能买吗",
        refined_query="白酒能买吗",
        complexity="complex",
        available_specialists=available_specialists,
        trace_id="test-trace",
        routed_specialists=["valuation_expert", "risk_assessor", "allocation_advisor"],
        portfolio_summary="用户持仓摘要",
    )

    assert plan.question_type == "action"
    assert plan.arbitration_mode in {"always", "if_conflict", "if_complex"}
    assert "portfolio" in plan.shared_evidence_keys
    assert "valuation" in plan.shared_evidence_keys
    assert "risk" in plan.shared_evidence_keys
    payload = plan.to_dict()
    assert payload["question_type"] == "action"
    assert payload["shared_evidence_keys"] == plan.shared_evidence_keys
