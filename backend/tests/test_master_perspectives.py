"""大师理念矩阵 — 第二阶段单元测试。

覆盖：
- 6位大师评分函数（巴菲特/林奇/博格/马克斯/达利欧/段永平）
- 共识检测算法
- 价值陷阱场景
- 债基降级（无基本面数据）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


# ── 测试数据构造 ─────────────────────────────────────────────

def _mock_report(score_overrides: dict = None) -> dict:
    """构造7维评分报告。"""
    report = {
        "quality": {"score": 65, "rating": "good", "label": "基金质量"},
        "drawdown": {"score": 60, "rating": "good", "label": "回撤恢复"},
        "trend": {"score": 55, "rating": "fair", "label": "趋势均线"},
        "capital": {"score": 50, "rating": "fair", "label": "资金流向"},
        "sentiment": {"score": 55, "rating": "fair", "label": "情绪温度"},
        "valuation": {"score": 60, "rating": "fair", "label": "估值水位"},
    }
    if score_overrides:
        for k, v in score_overrides.items():
            if k in report:
                report[k]["score"] = v
    return report


def _mock_details(has_fundamental: bool = True, fundamental_rating: str = "good") -> dict:
    """构造详细维度数据。"""
    details = {
        "quality": {"detail": {"fee_score": 75, "tracking_error_score": 80}},
        "drawdown": {"detail": {"drawdown_percentile": 0.4, "is_bottoming": False, "is_new_high": False}},
        "trend": {"detail": {"arrangement": "tangled"}},
        "capital": {"detail": {}},
        "sentiment": {"fear_greed_index": 50},
        "valuation": {"pe_percentile": 50, "score": 60},
        "holding_changes": {"has_history": False, "changes": []},
    }
    if has_fundamental:
        details["fundamental"] = {
            "fund_code": "161725",
            "fundamental_score": 65.0,
            "rating": fundamental_rating,
            "top10_coverage": 65.0,
            "stock_scores": [
                {
                    "stock_code": "600519",
                    "stock_name": "贵州茅台",
                    "pct_nav": 14.0,
                    "profitability": {"score": 85, "reason": "ROE高"},
                    "growth": {"score": 70, "reason": "增速稳健"},
                    "solvency": {"score": 80, "reason": "负债率低"},
                    "stability": {"score": 75, "reason": "ROE稳定"},
                    "valuation": {"score": 40, "reason": "PE偏高"},
                    "total": 72.0,
                    "rating": "good",
                },
            ],
            "advice": "测试",
        }
    else:
        details["fundamental"] = None
    return details


# ── 巴菲特视角测试 ──────────────────────────────────────────

def test_buffett_has_moat_strong_buy():
    """有护城河+ROE稳定+有安全边际+适合长期持有 → strong_buy。"""
    from services.master_perspectives import _buffett_perspective
    report = _mock_report({"quality": 75, "valuation": 80})  # 估值分高=低估
    details = _mock_details(fundamental_rating="good")
    # 提高盈利能力和稳定性分数
    details["fundamental"]["stock_scores"][0]["profitability"] = {"score": 88}
    details["fundamental"]["stock_scores"][0]["stability"] = {"score": 80}
    result = _buffett_perspective(report, details)
    assert result["action"] == "strong_buy"
    assert result["key_metrics"]["has_moat"] is True
    assert result["key_metrics"]["roe_consistent"] is True


def test_buffett_no_moat_wait():
    """无护城河 → wait。"""
    from services.master_perspectives import _buffett_perspective
    report = _mock_report()
    details = _mock_details()
    details["fundamental"]["stock_scores"][0]["profitability"] = {"score": 50}  # 低盈利
    result = _buffett_perspective(report, details)
    assert result["action"] == "wait"
    assert "护城河" in result["reason"]


def test_buffett_high_valuation_reduce():
    """有护城河但估值高 → reduce。"""
    from services.master_perspectives import _buffett_perspective
    report = _mock_report({"valuation": 30})  # 估值分低=高估
    details = _mock_details(fundamental_rating="good")
    details["fundamental"]["stock_scores"][0]["profitability"] = {"score": 88}
    details["fundamental"]["stock_scores"][0]["stability"] = {"score": 80}
    result = _buffett_perspective(report, details)
    assert result["action"] == "reduce"
    assert "估值" in result["reason"]


# ── 林奇视角测试 ────────────────────────────────────────────

def test_lynch_low_peg_fast_grower_strong_buy():
    """PEG<1+快速增长 → strong_buy。"""
    from services.master_perspectives import _lynch_perspective
    report = _mock_report({"valuation": 85, "sentiment": 60})
    details = _mock_details()
    details["fundamental"]["stock_scores"][0]["growth"] = {"score": 90}
    result = _lynch_perspective(report, details)
    assert result["action"] == "strong_buy"
    assert result["key_metrics"]["peg_estimate"] < 1.0


def test_lynch_high_peg_wait():
    """PEG>2 → wait。"""
    from services.master_perspectives import _lynch_perspective
    report = _mock_report({"valuation": 30})  # 估值分低=PE高
    details = _mock_details()
    details["fundamental"]["stock_scores"][0]["growth"] = {"score": 30}  # 低增长
    result = _lynch_perspective(report, details)
    assert result["action"] == "wait"
    assert result["key_metrics"]["peg_estimate"] > 1.5


# ── 博格视角测试 ────────────────────────────────────────────

def test_bogle_high_fee_wait():
    """高费率 → wait。"""
    from services.master_perspectives import _bogle_perspective
    report = _mock_report()
    details = _mock_details()
    details["quality"]["detail"]["fee_score"] = 40  # 高费率
    result = _bogle_perspective(report, details)
    assert result["action"] == "wait"
    assert "费率" in result["reason"]


def test_bogle_low_cost_low_valuation_strong_buy():
    """低成本+指数化好+估值低 → strong_buy。"""
    from services.master_perspectives import _bogle_perspective
    report = _mock_report({"valuation": 80})
    details = _mock_details()
    details["quality"]["detail"]["fee_score"] = 85
    details["quality"]["detail"]["tracking_error_score"] = 85
    result = _bogle_perspective(report, details)
    assert result["action"] == "strong_buy"


def test_bogle_high_valuation_reduce():
    """估值高 → reduce（均值回归风险）。"""
    from services.master_perspectives import _bogle_perspective
    report = _mock_report({"valuation": 30})
    details = _mock_details()
    details["quality"]["detail"]["fee_score"] = 80
    result = _bogle_perspective(report, details)
    assert result["action"] == "reduce"
    assert "均值回归" in result["reason"]


# ── 马克斯视角测试 ──────────────────────────────────────────

def test_marks_cycle_bottom_fear_strong_buy():
    """周期底部+情绪恐惧+趋势企稳 → strong_buy。"""
    from services.master_perspectives import _marks_perspective
    report = _mock_report({"sentiment": 80})  # 情绪分高=恐贪低
    details = _mock_details()
    details["drawdown"]["detail"]["drawdown_percentile"] = 0.8  # 回撤高位=周期底部
    details["drawdown"]["detail"]["is_bottoming"] = True
    details["trend"]["detail"]["arrangement"] = "weak_bull"
    details["sentiment"]["fear_greed_index"] = 25  # 恐惧
    result = _marks_perspective(report, details)
    assert result["action"] == "strong_buy"
    assert result["key_metrics"]["cycle_position"] == "底部"


def test_marks_cycle_high_reduce():
    """周期高位+估值高 → reduce。"""
    from services.master_perspectives import _marks_perspective
    report = _mock_report({"valuation": 30, "sentiment": 40})
    details = _mock_details()
    details["drawdown"]["detail"]["drawdown_percentile"] = 0.2  # 回撤低位=周期高位
    result = _marks_perspective(report, details)
    assert result["action"] == "reduce"


# ── 达利欧视角测试 ──────────────────────────────────────────

def test_dalio_over_concentrated_reduce():
    """集中度过高 → reduce。"""
    from services.master_perspectives import _dalio_perspective
    report = _mock_report()
    details = _mock_details()
    details["fundamental"]["top10_coverage"] = 75.0  # 高集中度
    result = _dalio_perspective(report, details)
    assert result["action"] == "reduce"
    assert "集中度" in result["reason"]


def test_dalio_well_diversified_hold():
    """分散良好+风险平衡 → hold。"""
    from services.master_perspectives import _dalio_perspective
    report = _mock_report({"capital": 70})
    details = _mock_details()
    details["fundamental"]["top10_coverage"] = 40.0  # 低集中度=分散良好
    result = _dalio_perspective(report, details)
    assert result["action"] == "hold"


# ── 段永平视角测试 ──────────────────────────────────────────

def test_duanyongping_value_trap_wait():
    """价值陷阱：估值低+基本面差 → wait。"""
    from services.master_perspectives import _duanyongping_perspective
    report = _mock_report({"valuation": 85, "quality": 70})
    details = _mock_details(fundamental_rating="poor")
    result = _duanyongping_perspective(report, details)
    assert result["action"] == "wait"
    assert "价值陷阱" in result["reason"]


def test_duanyongping_perfect_strong_buy():
    """好生意+好公司+好价格 → strong_buy。"""
    from services.master_perspectives import _duanyongping_perspective
    report = _mock_report({"quality": 75, "valuation": 80})
    details = _mock_details(fundamental_rating="excellent")
    result = _duanyongping_perspective(report, details)
    assert result["action"] == "strong_buy"
    assert "好生意" in result["view_text"]


# ── 共识检测测试 ────────────────────────────────────────────

def test_consensus_high_agreement():
    """5/6一致 → 高度共识。"""
    from services.master_perspectives import _detect_consensus
    masters = [
        {"action": "hold", "master_name": "A"},
        {"action": "hold", "master_name": "B"},
        {"action": "hold", "master_name": "C"},
        {"action": "hold", "master_name": "D"},
        {"action": "hold", "master_name": "E"},
        {"action": "dca", "master_name": "F"},
    ]
    result = _detect_consensus(masters)
    assert result["consensus_action"] == "hold"
    assert result["agreement"] >= 0.83
    assert result["agreement_label"] == "高度共识"


def test_consensus_conflict_detection():
    """同时出现strong_buy和reduce → 识别冲突。"""
    from services.master_perspectives import _detect_consensus
    masters = [
        {"action": "strong_buy", "master_name": "巴菲特"},
        {"action": "reduce", "master_name": "马克斯"},
        {"action": "hold", "master_name": "C"},
        {"action": "hold", "master_name": "D"},
        {"action": "hold", "master_name": "E"},
        {"action": "hold", "master_name": "F"},
    ]
    result = _detect_consensus(masters)
    assert len(result["conflicts"]) > 0
    assert any("加仓" in c and "减仓" in c for c in result["conflicts"])


def test_consensus_low_agreement_split():
    """意见分歧（3/3对半分）。"""
    from services.master_perspectives import _detect_consensus
    masters = [
        {"action": "hold", "master_name": "A"},
        {"action": "hold", "master_name": "B"},
        {"action": "hold", "master_name": "C"},
        {"action": "reduce", "master_name": "D"},
        {"action": "reduce", "master_name": "E"},
        {"action": "reduce", "master_name": "F"},
    ]
    result = _detect_consensus(masters)
    assert result["agreement"] == 0.5
    assert result["agreement_label"] == "温和共识"


# ── 大师矩阵聚合测试 ────────────────────────────────────────

def test_build_matrix_with_fundamental():
    """有基本面数据时，6位大师全部评分。"""
    from services.master_perspectives import build_master_perspectives_matrix
    report = _mock_report()
    details = _mock_details(has_fundamental=True)
    result = build_master_perspectives_matrix(report, details)
    assert len(result["masters"]) == 6
    # 所有大师都有评分
    scored = [m for m in result["masters"] if m.get("score") is not None]
    assert len(scored) == 6
    # 有共识结果
    assert "consensus_action" in result["consensus"]


def test_build_matrix_without_fundamental_bond_fund():
    """债基（无基本面）时，巴菲特和林奇降级，其余4位正常评分。"""
    from services.master_perspectives import build_master_perspectives_matrix
    report = _mock_report()
    details = _mock_details(has_fundamental=False)
    result = build_master_perspectives_matrix(report, details)
    assert len(result["masters"]) == 6
    # 巴菲特和林奇无评分
    buffett = next(m for m in result["masters"] if m["master_key"] == "buffett")
    lynch = next(m for m in result["masters"] if m["master_key"] == "lynch")
    assert buffett["score"] is None
    assert lynch["score"] is None
    assert "不适用" in buffett["reason"]
    # 其余4位有评分
    scored = [m for m in result["masters"] if m.get("score") is not None]
    assert len(scored) == 4


def test_build_matrix_consensus_only_scored():
    """共识检测只统计有评分的大师。"""
    from services.master_perspectives import build_master_perspectives_matrix
    report = _mock_report()
    details = _mock_details(has_fundamental=False)
    result = build_master_perspectives_matrix(report, details)
    # 只有4位大师有评分，共识应基于4位
    consensus = result["consensus"]
    # agreement_count 应该是 X/4 格式
    assert "/4" in consensus["agreement_count"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
