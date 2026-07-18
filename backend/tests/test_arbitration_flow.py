"""仲裁 Agent 行为测试。"""

import pytest

from agent.core.arbitration import arbitrate_results


@pytest.fixture(autouse=True)
def tmp_db():
    yield


def test_arbitration_prefers_risk_guardrails():
    specialist_results = [
        {
            "agent_key": "valuation_expert",
            "agent": "估值分析师",
            "analysis": "估值低，建议分批买入。",
        },
        {
            "agent_key": "risk_assessor",
            "agent": "风险管理师",
            "analysis": "波动偏大，不建议追高，应先控制仓位。",
        },
        {
            "agent_key": "allocation_advisor",
            "agent": "资产配置师",
            "analysis": "从仓位看可以小仓试探。",
        },
    ]

    result = arbitrate_results("白酒能买吗", specialist_results)

    assert result["final_stance"] == "hold"
    assert result["arbitration_mode"] == "conflict"
    assert any("风险" in reason for reason in result["reasons"])
