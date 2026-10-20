"""对话 205：行情归因应保留市场角色，且不强制生成交易计划。"""

import pytest

from agent.core.router import SmartRouter, _classify_question_type
from agent.core.task_planner import classify_question_type
from agent.core.plan_executor import generate_plan


QUERY = "是有什么在做空港股吗， 为什么港股科技 和互联网 一直在大跌呀"


@pytest.mark.parametrize("query", [QUERY, "为什么恒生科技一直在下跌", "外资卖出是港股下跌的原因吗", "港股大跌是做空造成的吗"])
def test_attribution_intent_precedes_comparison_and_market_selling(query):
    assert _classify_question_type(query) == "attribution"
    assert classify_question_type(query) == "attribution"


@pytest.mark.parametrize("query", ["为什么港股大跌，现在能买入吗", "港股科技加仓2000元合适吗", "白酒能买吗"])
def test_explicit_trading_request_keeps_action_intent(query):
    assert _classify_question_type(query) == "action"
    assert classify_question_type(query) == "action"


def test_attribution_router_keeps_market_lead_before_truncation():
    result = SmartRouter()._rule_route(QUERY, "基金占比40%，现金40%")
    assert result["question_type"] == "attribution"
    assert result["primary_specialist"] == "market_analyst"
    assert result["specialists"][0] == "market_analyst"
    assert "risk_assessor" in result["specialists"][:3]


def test_rewritten_query_cannot_invent_a_user_trade_request():
    result = SmartRouter()._rule_route("港股下跌原因及持仓配置建议", original_query=QUERY)
    assert result["question_type"] == "attribution"
    assert result["primary_specialist"] == "market_analyst"


def test_plan_preserves_core_roles_and_distinct_executable_subqueries():
    keys = ["valuation_expert", "allocation_advisor", "fund_analyst", "market_analyst", "risk_assessor", "macro_strategist"]
    available = [{"agent_key": k, "name": k, "tools": ["query_policy_news"]} for k in keys]
    plan = generate_plan(QUERY, QUERY, "complex", available, "regression", routed_specialists=keys)
    assert plan.question_type == "attribution"
    assert plan.steps[0].agent_key == "market_analyst"
    assert "risk_assessor" in [s.agent_key for s in plan.steps]
    assert len(plan.steps) <= 3
    assert len({s.sub_query for s in plan.steps}) == len(plan.steps)
    for step in plan.steps:
        assert step.sub_query and step.sub_query != QUERY
        assert step.sub_query in step.query
        assert QUERY in step.query
        assert step.allowed_tools == ["query_policy_news"]
    assert "portfolio" not in plan.shared_evidence_keys


def test_attribution_plan_without_router_uses_evidence_roles_without_llm(monkeypatch):
    import services.llm_service as llm
    def unexpected(**kwargs):
        pytest.fail("归因职责已由配置确定，无需调用规划模型")
    monkeypatch.setattr(llm, "_call_llm", unexpected)
    available = [{"agent_key": k, "name": k, "tools": []} for k in
                 ["valuation_expert", "allocation_advisor", "fund_analyst", "market_analyst", "risk_assessor", "macro_strategist"]]
    plan = generate_plan(QUERY, QUERY, "complex", available, "no-router")
    assert [s.agent_key for s in plan.steps] == ["market_analyst", "risk_assessor", "macro_strategist"]
    assert all(s.sub_query and s.sub_query in s.query for s in plan.steps)


def test_answer_policy_uses_editable_analysis_agents_and_keeps_defaults_idempotent():
    from agent.core.answer_policy import build_answer_policy, get_attribution_subquery
    from db.analysis import _init_analysis_tables, get_analysis_agent_by_name, update_analysis_agent
    policy = build_answer_policy(QUERY)
    for term in ["已确认事实", "待验证机制", "证据缺口", "事件日期", "旧文章", "不同时间窗", "情景损失", "主题上限"]:
        assert term in policy
    assert "不强制" in policy and "金额" in policy
    agent = get_analysis_agent_by_name("行情归因回答约束")
    assert agent is not None
    update_analysis_agent(agent["id"], system_prompt="自定义归因边界")
    _init_analysis_tables()
    assert "自定义归因边界" in build_answer_policy(QUERY)
    assert "自定义归因边界" not in build_answer_policy("现在加仓2000元可以吗")
    assert get_attribution_subquery("market_analyst", QUERY)


@pytest.mark.parametrize("entry", ["initial", "cross_review"])
def test_expert_entrypoints_do_not_force_trade_or_portfolio_for_attribution(monkeypatch, entry):
    from agent.core import multi_agent

    class PromptCaptured(Exception):
        pass

    captured = []
    def capture(**kwargs):
        captured.append(kwargs["messages"][0]["content"])
        raise PromptCaptured("captured")

    monkeypatch.setattr(multi_agent, "_call_llm", capture)
    monkeypatch.setattr(multi_agent, "_inject_kyc_profile", lambda text, agent: text)
    with pytest.raises(PromptCaptured):
        if entry == "initial":
            multi_agent.run_specialist("market_analyst", QUERY, prebuilt_context="市场证据", from_pipeline=True)
        else:
            multi_agent.run_specialist_with_context("market_analyst", QUERY, {}, prebuilt_context="市场证据")
    prompt = captured[0]
    assert "你的分析结尾必须包含「具体操作建议」" not in prompt
    assert "你的分析结尾必须包含「对当前持仓的影响」" not in prompt
    assert "待验证机制" in prompt


def test_explicit_trade_still_receives_action_template(monkeypatch):
    from agent.core import multi_agent
    captured = []
    class PromptCaptured(Exception):
        pass
    def capture(**kwargs):
        captured.append(kwargs["messages"][0]["content"])
        raise PromptCaptured("captured")
    monkeypatch.setattr(multi_agent, "_call_llm", capture)
    with pytest.raises(PromptCaptured):
        multi_agent.run_specialist("allocation_advisor", "现在加仓2000元可以吗", prebuilt_context="真实持仓", from_pipeline=True)
    assert "具体操作建议" in captured[0]
    assert "对当前持仓的影响" in captured[0]


def test_context_layer0_does_not_force_action_for_every_task():
    from agent.core.context_builder import build_layer0_system
    assert "结尾包含具体操作建议" not in build_layer0_system({"system_prompt": "已配置角色"})


@pytest.mark.parametrize("query, expected_profile", [(QUERY, False), ("港股加仓2000元合适吗", True)])
def test_layered_context_omits_private_profile_and_portfolio_for_attribution(monkeypatch, query, expected_profile):
    from agent.core import context_builder
    calls = []
    monkeypatch.setattr(context_builder, "build_layer1_user_profile", lambda *args: calls.append(True) or "私人KYC")
    monkeypatch.setattr(context_builder, "build_layer2_conversation_history", lambda *args, **kw: "")
    context = context_builder.build_specialist_context(
        {"system_prompt": "已配置角色"}, [{"role": "user", "content": query}], 1,
        portfolio_summary="私人持仓", market_context="当期市场数据",
    )
    assert bool(calls) is expected_profile
    assert ("私人KYC" in context) is expected_profile
    assert ("私人持仓" in context) is expected_profile
    assert "当期市场数据" in context
