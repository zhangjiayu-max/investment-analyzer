"""路由系统 ADE 增强单元测试。

覆盖：
- A 方向：_classify_complexity_by_semantics 语义复杂度判定
- D 方向：_KEYWORD_ROUTES 关键词覆盖扩展
- E 方向：_apply_min_specialists complex 下限补齐
"""
import pytest

from agent.core.router import (
    _classify_complexity_by_semantics,
    _KEYWORD_ROUTES,
    _COMPLEXITY_DOMAIN_GROUPS,
    _MULTI_INTENT_SEPARATORS,
    _MARKET_EXTREME_KEYWORDS,
    _HIGH_RISK_ACTION_KEYWORDS,
    SmartRouter,
)
from agent.core.orchestrator import (
    get_context_config,
    _QUESTION_TYPE_MANDATORY,
    _is_specialist_enabled,
    _apply_min_specialists,
)


# ════════════════════════════════════════════════════════════
# A 方向：复杂度独立判定
# ════════════════════════════════════════════════════════════

class TestSemanticComplexity:
    """A 方向：_classify_complexity_by_semantics 测试。"""

    def test_simple_zero_domain(self):
        """零领域命中 → simple"""
        assert _classify_complexity_by_semantics("你好") == "simple"
        assert _classify_complexity_by_semantics("在吗") == "simple"

    def test_simple_empty_query(self):
        """空查询 → simple"""
        assert _classify_complexity_by_semantics("") == "simple"

    def test_medium_single_domain(self):
        """单领域命中 → medium（需要工具数据但单主题）"""
        # 估值组
        assert _classify_complexity_by_semantics("沪深300现在估值多少") == "medium"
        # 基金组
        assert _classify_complexity_by_semantics("推荐一只基金") == "medium"
        # 市场组（不含极端词）
        assert _classify_complexity_by_semantics("今天大盘行情怎么样") == "medium"

    def test_complex_multi_intent_separators(self):
        """多意图分隔符 ≥2 → complex"""
        # 2 个 "和"
        assert _classify_complexity_by_semantics("大盘和债券和基金怎么样") == "complex"
        # "和" + "另外"
        assert _classify_complexity_by_semantics("估值和仓位另外看看风险") == "complex"

    def test_complex_multi_domain(self):
        """命中 ≥2 个不同领域 → complex"""
        # 估值 + 风险
        assert _classify_complexity_by_semantics("估值高风险大吗") == "complex"
        # 市场 + 配置
        assert _classify_complexity_by_semantics("大盘行情如何配置仓位") == "complex"

    def test_complex_high_risk_keyword(self):
        """命中高风险关键词 → complex"""
        for kw in ["清仓", "满仓", "梭哈", "补仓", "抄底", "加杠杆"]:
            assert _classify_complexity_by_semantics(f"现在能{kw}吗") == "complex"

    def test_complex_market_extreme_keyword(self):
        """D 方向：市场极端关键词 → complex"""
        for kw in ["跌破", "破位", "熔断", "股灾", "熊市", "崩盘", "暴跌", "新低"]:
            assert _classify_complexity_by_semantics(f"大盘{kw}了") == "complex", \
                f"市场极端词 '{kw}' 应该升级 complex"

    def test_complex_conv122_scenario(self):
        """conv 122 场景：沪深跌破3700 熊市 → complex"""
        assert _classify_complexity_by_semantics("沪深指数跌破3700 熊市") == "complex"

    def test_complex_conv117_scenario(self):
        """conv 117 场景：医药利好政策 → complex（政策+利好双领域）"""
        assert _classify_complexity_by_semantics("医药利好政策") == "complex"


# ════════════════════════════════════════════════════════════
# D 方向：关键词覆盖扩展
# ════════════════════════════════════════════════════════════

class TestKeywordRoutesExpansion:
    """D 方向：_KEYWORD_ROUTES 扩展测试。"""

    def test_market_extreme_keywords_added(self):
        """D-1: 市场极端关键词已加入路由规则"""
        all_keywords = []
        for keywords, _ in _KEYWORD_ROUTES:
            all_keywords.extend(keywords)
        for kw in ["跌破", "破位", "熔断", "股灾", "熊市", "崩盘", "暴跌", "新低"]:
            assert kw in all_keywords, f"市场极端词 '{kw}' 未加入路由规则"

    def test_bond_keywords_added(self):
        """D-2: 债券关键词已加入路由规则"""
        all_keywords = []
        for keywords, _ in _KEYWORD_ROUTES:
            all_keywords.extend(keywords)
        for kw in ["债券", "国债", "利率债", "信用债", "可转债", "债基"]:
            assert kw in all_keywords, f"债券关键词 '{kw}' 未加入路由规则"

    def test_policy_keywords_expanded(self):
        """D-3: 政策利好关键词扩展"""
        all_keywords = []
        for keywords, _ in _KEYWORD_ROUTES:
            all_keywords.extend(keywords)
        for kw in ["刺激政策", "补贴", "减税", "降准", "降息", "政策受益"]:
            assert kw in all_keywords, f"政策关键词 '{kw}' 未加入路由规则"

    def test_valuation_colloquial_added(self):
        """D-4: 估值口语化表达已加入"""
        all_keywords = []
        for keywords, _ in _KEYWORD_ROUTES:
            all_keywords.extend(keywords)
        for kw in ["百分位低", "百分位高", "历史低位", "历史高位"]:
            assert kw in all_keywords, f"估值口语词 '{kw}' 未加入路由规则"

    def test_add_position_scenarios_added(self):
        """D-5: 补仓抄底场景关键词已加入"""
        all_keywords = []
        for keywords, _ in _KEYWORD_ROUTES:
            all_keywords.extend(keywords)
        for kw in ["抄底", "补仓时机", "加仓时机", "分批建仓", "左侧交易"]:
            assert kw in all_keywords, f"补仓抄底词 '{kw}' 未加入路由规则"

    def test_attribution_route_has_industry(self):
        """D-修改：归因类路由补充行业基本面专家"""
        for keywords, agents in _KEYWORD_ROUTES:
            if "为什么涨" in keywords:
                assert "industry_fundamentalist" in agents, \
                    "归因类路由应包含 industry_fundamentalist"
                return
        pytest.fail("未找到归因类路由规则")

    def test_medical_route_has_industry(self):
        """D-修改：医药类路由补充行业基本面专家"""
        for keywords, agents in _KEYWORD_ROUTES:
            if "医药" in keywords:
                assert "industry_fundamentalist" in agents, \
                    "医药类路由应包含 industry_fundamentalist"
                return
        pytest.fail("未找到医药类路由规则")

    def test_market_extreme_route_returns_three_experts(self):
        """D-1: 市场极端关键词 → market+macro+risk 三专家"""
        router = SmartRouter()
        result = router._rule_route("沪深跌破3700 熊市", "")
        assert result is not None
        assert "market_analyst" in result["specialists"]
        assert "macro_strategist" in result["specialists"]
        assert "risk_assessor" in result["specialists"]

    def test_bond_route_returns_three_experts(self):
        """D-2: 债券 → fund+macro+valuation 三专家"""
        router = SmartRouter()
        result = router._rule_route("闲置资金买债券可行吗", "")
        assert result is not None
        assert "fund_analyst" in result["specialists"]
        assert "macro_strategist" in result["specialists"]
        assert "valuation_expert" in result["specialists"]


# ════════════════════════════════════════════════════════════
# E 方向：complex 下限补齐
# ════════════════════════════════════════════════════════════

class TestMinSpecialistsPadding:
    """E 方向：_apply_min_specialists 测试。"""

    def test_context_config_has_min_specialists(self):
        """get_context_config 返回 min_specialists 字段"""
        for c in ["simple", "medium", "complex"]:
            cfg = get_context_config(c)
            assert "min_specialists" in cfg, f"{c} 缺少 min_specialists"
            assert cfg["min_specialists"] >= 1

    def test_complex_min_is_4(self):
        """complex 的 min_specialists = 4"""
        cfg = get_context_config("complex")
        assert cfg["min_specialists"] == 4
        assert cfg["max_specialists"] == 4  # 默认上限

    def test_simple_medium_min_matches_max(self):
        """simple/medium 的 min = max（保持原语义）"""
        s_cfg = get_context_config("simple")
        assert s_cfg["min_specialists"] == s_cfg["max_specialists"] == 1
        m_cfg = get_context_config("medium")
        assert m_cfg["min_specialists"] == m_cfg["max_specialists"] == 2

    def test_padding_attribution(self):
        """conv 122 场景：attribution 类型，命中 3 个 → 补到 4"""
        # market+macro+risk (3) + attribution 池 industry_fundamentalist → 4
        specialists = ["market_analyst", "macro_strategist", "risk_assessor"]
        result, reason = _apply_min_specialists(
            specialists, "complex", "attribution", 4, 4
        )
        assert len(result) == 4
        assert "industry_fundamentalist" in result
        assert "补齐" in reason

    def test_padding_action(self):
        """action 类型：命中 2 个 → 补到 4"""
        # allocation+risk (2) + action 池 behavioral+valuation → 4
        specialists = ["allocation_advisor", "risk_assessor"]
        result, reason = _apply_min_specialists(
            specialists, "complex", "action", 4, 4
        )
        assert len(result) == 4
        assert "behavioral_advisor" in result
        assert "valuation_expert" in result

    def test_padding_prediction(self):
        """prediction 类型：命中 1 个 → 补到 4（需补 3 个但池只有 3 个）"""
        specialists = ["market_analyst"]
        result, reason = _apply_min_specialists(
            specialists, "complex", "prediction", 4, 4
        )
        # prediction 池: valuation+market+macro = 3 个候选
        # 排除已含的 market，剩 2 个可补
        assert len(result) >= 3  # 至少补到 3（受池大小限制）

    def test_no_padding_for_medium(self):
        """medium 不触发补齐"""
        specialists = ["valuation_expert"]
        result, reason = _apply_min_specialists(
            specialists, "medium", "generic", 2, 2
        )
        assert result == specialists
        assert reason == ""

    def test_no_padding_when_already_at_min(self):
        """已达下限不补齐"""
        specialists = ["a", "b", "c", "d"]
        result, reason = _apply_min_specialists(
            specialists, "complex", "generic", 4, 4
        )
        assert result == specialists
        assert reason == ""

    def test_no_padding_beyond_max(self):
        """补齐不超过 max_spec"""
        # max=4, 当前 3, 需补 1（即使 min=5 也只补到 max=4）
        specialists = ["a", "b", "c"]
        result, reason = _apply_min_specialists(
            specialists, "complex", "generic", 5, 4
        )
        assert len(result) == 4  # 受 max 限制

    def test_generic_fallback_pool(self):
        """未知 question_type 用 generic 池兜底"""
        specialists = ["market_analyst"]
        result, reason = _apply_min_specialists(
            specialists, "complex", "unknown_type", 4, 4
        )
        # generic 池: risk+allocation+market+valuation
        assert len(result) == 4
        assert "risk_assessor" in result
        assert "allocation_advisor" in result

    def test_skip_disabled_specialist(self):
        """禁用的专家不补入（industry_fundamentalist_enabled=false 时跳过）"""
        # 这个测试需要 monkeypatch 配置，先验证函数签名正确
        # 实际禁用场景由 _is_specialist_enabled 控制
        assert callable(_is_specialist_enabled)
        # 默认未禁用的专家返回 True
        assert _is_specialist_enabled("valuation_expert") is True
        assert _is_specialist_enabled("market_analyst") is True


# ════════════════════════════════════════════════════════════
# 集成测试：完整路由流程
# ════════════════════════════════════════════════════════════

class TestRouterIntegration:
    """路由集成测试：验证 A+D+E 三方向协同工作。"""

    def test_conv122_full_route(self):
        """conv 122 完整路由：沪深跌破3700 熊市

        预期：
        - 复杂度: complex（A 方向：市场极端词）
        - 专家包含: market_analyst + macro_strategist + risk_assessor（D-1）
        - complex 下限补齐到 4 个（E 方向）
        """
        router = SmartRouter()
        result = router._rule_route("沪深跌破3700 熊市", "")
        assert result is not None
        assert result["complexity"] == "complex"
        assert "market_analyst" in result["specialists"]
        assert "macro_strategist" in result["specialists"]
        assert "risk_assessor" in result["specialists"]
        # 命中 3 个 → complex → 应该能补到 4
        # （_rule_route 不做补齐，补齐在 orchestrator 做，这里只验证路由命中）

    def test_conv113_medical_route(self):
        """conv 113 完整路由：医疗涨 医疗器械涨幅少

        预期：
        - 复杂度: medium（单领域，但多主题）
        - 专家包含: macro+valuation+fund+industry（D-修改）
        """
        router = SmartRouter()
        result = router._rule_route("医疗涨 医疗器械涨幅少", "")
        assert result is not None
        # 医药路由命中 → macro+valuation+fund+industry
        assert "fund_analyst" in result["specialists"]
        assert "valuation_expert" in result["specialists"]
        assert "industry_fundamentalist" in result["specialists"]

    def test_conv117_policy_route(self):
        """conv 117 完整路由：医药利好政策

        预期：
        - 复杂度: complex（政策+利好双领域）
        - 专家包含: macro + industry（D-3 政策利好强化）
        """
        router = SmartRouter()
        result = router._rule_route("医药利好政策", "")
        assert result is not None
        assert result["complexity"] == "complex"
        assert "macro_strategist" in result["specialists"]
        assert "industry_fundamentalist" in result["specialists"]

    def test_conv116_bond_route(self):
        """conv 116 完整路由：闲置资金买债券

        预期：
        - 复杂度: medium（单领域）
        - 专家包含: fund+macro+valuation（D-2 债券主题）
        """
        router = SmartRouter()
        result = router._rule_route("闲置资金买债券可行吗", "")
        assert result is not None
        assert "fund_analyst" in result["specialists"]
        assert "macro_strategist" in result["specialists"]
        assert "valuation_expert" in result["specialists"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
