"""多智能体对话降本增效优化模块测试。"""

import pytest


def test_exact_cache_hit():
    from agent.cache import ExpertCache
    cache = ExpertCache(ttl_seconds=60, max_size=10)
    value = {"analysis": "test"}
    cache.put("query", "valuation", value)
    assert cache.get("query", "valuation") == value


def test_semantic_cache_hit():
    from agent.cache import ExpertCache
    cache = ExpertCache(ttl_seconds=60, max_size=10, semantic_threshold=0.92)
    value = {"analysis": "test"}
    cache.put("今天白酒估值怎么样", "valuation", value)
    # 语义相近但表述不同
    result = cache.get("白酒今天估值如何", "valuation")
    assert result is not None
    assert result["analysis"] == "test"


def test_semantic_cache_miss_different_agent():
    from agent.cache import ExpertCache
    cache = ExpertCache(ttl_seconds=60, max_size=10, semantic_threshold=0.92)
    cache.put("query", "valuation", {"analysis": "test"})
    assert cache.get("query", "risk") is None


def test_context_hash_affects_cache():
    from agent.cache import ExpertCache
    cache = ExpertCache(ttl_seconds=60, max_size=10)
    cache.put("query", "valuation", {"analysis": "test"}, context_hash="ctx1")
    assert cache.get("query", "valuation", context_hash="ctx2") is None


def test_detect_conflicts_rating_opposite():
    from agent.orchestrator import detect_conflicts
    results = [
        {"agent": "估值专家", "analysis": "建议买入"},
        {"agent": "风险专家", "analysis": "建议卖出"},
    ]
    conflicts = detect_conflicts(results)
    assert conflicts["detected"] is True


def test_detect_conflicts_no_conflict():
    from agent.orchestrator import detect_conflicts
    results = [
        {"agent": "估值专家", "analysis": "当前估值合理，建议持有"},
        {"agent": "配置专家", "analysis": "仓位适中，建议持有"},
    ]
    conflicts = detect_conflicts(results)
    assert conflicts["detected"] is False


def test_detect_conflicts_action_opposite():
    from agent.orchestrator import detect_conflicts
    results = [
        {"agent": "A", "analysis": "建议加仓 中证500(510500) 10%"},
        {"agent": "B", "analysis": "建议减仓 中证500(510500) 全部卖出"},
    ]
    conflicts = detect_conflicts(results)
    assert conflicts["detected"] is True


def test_router_rule_match_valuation():
    from agent.router import SmartRouter
    router = SmartRouter()
    result = router.route("白酒估值怎么样？")
    assert "valuation_expert" in result["specialists"]
    assert result["route_by"] == "rule"


def test_router_rule_match_portfolio():
    from agent.router import SmartRouter
    router = SmartRouter()
    result = router.route("我的持仓分散吗？")
    assert "allocation_advisor" in result["specialists"] or "wealth_advisor" in result["specialists"]


def test_router_high_risk_action_keeps_behavior_and_counter_agents():
    from agent.router import SmartRouter
    router = SmartRouter()

    result = router.route("今天大跌我很慌，要不要清仓卖出")

    assert "risk_assessor" in result["specialists"]
    assert "behavior_coach" in result["specialists"]
    assert "counter_argument" in result["specialists"]


def test_router_mention_override():
    from agent.router import SmartRouter
    router = SmartRouter()
    result = router.route("随便问", target_specialists=["valuation_expert"])
    assert result["specialists"] == ["valuation_expert"]
    assert result["route_by"] == "mention"


def test_router_cache_does_not_duplicate():
    from agent.router import SmartRouter
    router = SmartRouter()
    r1 = router.route("今天大盘怎么看")
    r2 = router.route("今天大盘怎么看")
    assert r1 == r2


@pytest.fixture
def validator_no_llm(monkeypatch):
    """禁用 LLM 质检，避免测试受外部模型影响。"""
    from db.config import update_config
    update_config("validator.llm_check_enabled", "false")


def test_validator_passes_executable_answer(validator_no_llm):
    from agent.validator import LightValidator
    v = LightValidator()
    result = v.validate(
        query="白酒还能买吗？",
        final_answer="建议持有 招商白酒(161725) ，触发条件为 PE 分位低于 30% 时加仓 10%。",
        specialist_results=[{"agent": "估值专家", "analysis": "当前估值合理"}],
        context="持仓：招商白酒 1000 元",
    )
    assert result["passed"] is True


def test_validator_catches_missing_action(validator_no_llm):
    from agent.validator import LightValidator
    v = LightValidator()
    result = v.validate(
        query="白酒还能买吗？",
        final_answer="白酒目前估值处于历史中位数附近，投资者可根据自身情况决定。",
        specialist_results=[{"agent": "估值专家", "analysis": "估值合理"}],
        context="",
    )
    assert result["passed"] is False
    assert any("可执行" in issue for issue in result["issues"])


def test_validator_catches_hallucinated_fund(validator_no_llm):
    from agent.validator import LightValidator
    v = LightValidator()
    result = v.validate(
        query="白酒还能买吗？",
        final_answer="建议买入 招商白酒(161725) 。",
        specialist_results=[],
        context="持仓：易方达蓝筹 2000 元",
    )
    assert result["passed"] is False


def test_dynamic_spawn_uses_existing_behavior_coach_key():
    from agent.orchestrator import _check_dynamic_spawn

    suggestions = _check_dynamic_spawn({"analysis": "", "confidence": 0.4}, already_called=set())

    assert any(s["agent_key"] == "behavior_coach" for s in suggestions)
    assert all(s["agent_key"] != "behavioral_coach" for s in suggestions)


def test_append_specialist_context_includes_dynamic_spawn_results():
    from agent.orchestrator import _append_specialist_synthesis_context

    llm_messages = []
    specialist_results = [
        {
            "agent_key": "valuation_expert",
            "agent": "估值分析师",
            "analysis": "估值处于低位，可以小额分批。",
        },
        {
            "agent_key": "counter_argument",
            "agent": "反方观点审查员",
            "analysis": "低估不等于马上反转，需要警惕继续下跌。",
            "spawned_by": "dynamic_spawn",
        },
    ]

    _append_specialist_synthesis_context(llm_messages, specialist_results)

    assert len(llm_messages) == 1
    assert llm_messages[0]["role"] == "user"
    assert "估值分析师" in llm_messages[0]["content"]
    assert "反方观点审查员" in llm_messages[0]["content"]
    assert "低估不等于马上反转" in llm_messages[0]["content"]


def test_sop_templates_use_existing_behavior_coach_key():
    from agent.orchestrator import SOP_TEMPLATES

    for template in SOP_TEMPLATES.values():
        agents = [step["agent"] for step in template.get("steps", [])]
        assert "behavioral_coach" not in agents
        assert template.get("final_agent") != "behavioral_coach"
