"""组合智能引擎 — 第三阶段单元测试。

覆盖：
- 组合风险度量（波动率/VaR/CVaR/最大回撤/夏普/Sortino/Effective N）
- 7维体检组合聚合（按权重加权）
- 大师矩阵组合版（6位大师组合视角）
- 数据降级（空持仓/净值不足）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import numpy as np


# ── 风险度量算法测试 ────────────────────────────────────────

def test_calc_daily_returns():
    """日收益率计算。"""
    from services.portfolio_intelligence import _calc_daily_returns
    navs = [1.0, 1.05, 1.02, 1.08]
    rets = _calc_daily_returns(navs)
    assert len(rets) == 3
    assert abs(rets[0] - 0.05) < 0.001
    assert abs(rets[1] - (-0.0286)) < 0.001


def test_calc_max_drawdown():
    """最大回撤计算。"""
    from services.portfolio_intelligence import _calc_max_drawdown
    # 先涨后跌再涨
    navs = [1.0, 1.2, 0.9, 1.3]
    max_dd, recovery = _calc_max_drawdown(navs)
    # 峰值1.2，谷底0.9，回撤25%
    assert abs(max_dd - 0.25) < 0.01
    assert recovery > 0  # 已恢复


def test_calc_max_drawdown_no_recovery():
    """未恢复的回撤。"""
    from services.portfolio_intelligence import _calc_max_drawdown
    navs = [1.0, 1.2, 0.8, 0.9]  # 峰值1.2，谷底0.8，未恢复到1.2
    max_dd, recovery = _calc_max_drawdown(navs)
    assert abs(max_dd - 0.333) < 0.01
    assert recovery == -1  # 未恢复


def test_align_nav_series_intersection():
    """净值序列对齐 — 数据不足30天返回空（正确降级行为）。"""
    from services.portfolio_intelligence import _align_nav_series
    series1 = {"fund_code": "A", "dates": ["2024-01-01", "2024-01-02", "2024-01-03"], "navs": [1.0, 1.01, 1.02]}
    series2 = {"fund_code": "B", "dates": ["2024-01-02", "2024-01-03", "2024-01-04"], "navs": [2.0, 2.02, 2.04]}
    result = _align_nav_series([series1, series2])
    # 数据不足30天，正确降级为空
    assert result["dates"] == []
    assert result["fund_codes"] == []


def test_default_risk_metrics():
    """降级默认值。"""
    from services.portfolio_intelligence import _default_risk_metrics
    result = _default_risk_metrics("测试原因")
    assert result["data_status"] == "degraded"
    assert result["degraded_reason"] == "测试原因"
    assert result["portfolio_volatility"] == 0
    assert result["effective_n"] == 1.0


# ── 组合风险度量（模拟数据）─────────────────────────────────

def test_portfolio_risk_metrics_with_mock(monkeypatch):
    """用mock数据测试组合风险度量。"""
    from services import portfolio_intelligence

    # Mock持仓数据
    monkeypatch.setattr(portfolio_intelligence, "_get_portfolio_weights", lambda user_id: [
        {"fund_code": "161725", "fund_name": "白酒基金", "weight": 0.6, "current_value": 6000, "shares": 1000, "fund_category": "index", "index_code": "", "index_name": ""},
        {"fund_code": "005827", "fund_name": "新能源基金", "weight": 0.4, "current_value": 4000, "shares": 800, "fund_category": "index", "index_code": "", "index_name": ""},
    ])

    # Mock净值序列（100天，有波动）
    np.random.seed(42)
    n_days = 100
    nav_a = np.cumprod(1 + np.random.normal(0.001, 0.02, n_days)).tolist()
    nav_b = np.cumprod(1 + np.random.normal(0.0005, 0.015, n_days)).tolist()
    dates = [f"2024-01-{i+1:02d}" for i in range(n_days)]

    monkeypatch.setattr(portfolio_intelligence, "_fetch_nav_series", lambda code, days=365: {
        "fund_code": code, "dates": dates, "navs": nav_a if code == "161725" else nav_b,
    })

    result = portfolio_intelligence.calculate_portfolio_risk_metrics("test")

    assert result["data_status"] == "ok"
    assert result["fund_count"] == 2
    assert result["portfolio_volatility"] > 0
    assert result["var_95_daily"] < 0  # 5%分位数应为负
    assert result["max_drawdown"] >= 0
    assert result["sharpe_ratio"] != 0 or result["annual_return"] != 0
    assert len(result["risk_contributions"]) <= 10


def test_portfolio_risk_metrics_empty(monkeypatch):
    """空持仓降级。"""
    from services import portfolio_intelligence
    monkeypatch.setattr(portfolio_intelligence, "_get_portfolio_weights", lambda user_id: [])
    result = portfolio_intelligence.calculate_portfolio_risk_metrics("test")
    assert result["data_status"] == "degraded"


# ── 7维聚合测试 ─────────────────────────────────────────────

def test_portfolio_decision_low_effective_n():
    """Effective N过低 → reduce。"""
    from services.portfolio_intelligence import _build_portfolio_decision
    report = {"quality": {"score": 60}, "valuation": {"score": 50}, "trend": {"score": 50}, "fundamental": {"score": 50}}
    risk = {"effective_n": 1.2, "max_drawdown": 0.2, "sharpe_ratio": 0.5}
    decision = _build_portfolio_decision(report, risk)
    assert decision["action"] == "reduce"
    assert "分散" in decision["reason"]


def test_portfolio_decision_high_drawdown():
    """最大回撤过大 → reduce。"""
    from services.portfolio_intelligence import _build_portfolio_decision
    report = {"quality": {"score": 60}, "valuation": {"score": 50}, "fundamental": {"score": 50}}
    risk = {"effective_n": 2.5, "max_drawdown": 0.45, "sharpe_ratio": -0.3}
    decision = _build_portfolio_decision(report, risk)
    assert decision["action"] == "reduce"
    assert "回撤" in decision["reason"]


def test_portfolio_decision_low_valuation_strong_buy():
    """低估+质量好+分散充分 → strong_buy。"""
    from services.portfolio_intelligence import _build_portfolio_decision
    report = {"quality": {"score": 65}, "valuation": {"score": 80}, "fundamental": {"score": 60}}
    risk = {"effective_n": 2.5, "max_drawdown": 0.15, "sharpe_ratio": 1.0}
    decision = _build_portfolio_decision(report, risk)
    assert decision["action"] == "strong_buy"


def test_portfolio_decision_hold():
    """正常情况 → hold。"""
    from services.portfolio_intelligence import _build_portfolio_decision
    report = {"quality": {"score": 60}, "valuation": {"score": 55}, "fundamental": {"score": 55}}
    risk = {"effective_n": 2.0, "max_drawdown": 0.15, "sharpe_ratio": 0.5}
    decision = _build_portfolio_decision(report, risk)
    assert decision["action"] == "hold"


# ── 大师矩阵组合版测试 ──────────────────────────────────────

def test_portfolio_master_matrix_basic():
    """大师矩阵组合版基本功能。"""
    from services.portfolio_intelligence import build_portfolio_master_matrix
    portfolio_report = {
        "quality": {"score": 65},
        "drawdown": {"score": 60},
        "trend": {"score": 55},
        "capital": {"score": 50},
        "sentiment": {"score": 55},
        "valuation": {"score": 60},
        "fundamental": {"score": 60},
    }
    risk_metrics = {"effective_n": 2.5, "avg_correlation": 0.5, "max_drawdown": 0.2, "sharpe_ratio": 0.8}
    result = build_portfolio_master_matrix(portfolio_report, risk_metrics, [])

    assert len(result["masters"]) == 6
    assert "consensus" in result
    # 所有大师都有评分
    for m in result["masters"]:
        assert m["score"] is not None
        assert m["action"] in ("strong_buy", "dca", "hold", "reduce", "wait")


def test_portfolio_master_matrix_dalio_over_concentrated():
    """达利欧：分散不足 → reduce。"""
    from services.portfolio_intelligence import build_portfolio_master_matrix
    portfolio_report = {
        "quality": {"score": 60}, "drawdown": {"score": 50}, "trend": {"score": 50},
        "capital": {"score": 50}, "sentiment": {"score": 50}, "valuation": {"score": 50},
        "fundamental": {"score": 50},
    }
    risk_metrics = {"effective_n": 1.2, "avg_correlation": 0.8, "max_drawdown": 0.1, "sharpe_ratio": 0.5}
    result = build_portfolio_master_matrix(portfolio_report, risk_metrics, [])
    dalio = next(m for m in result["masters"] if m["master_key"] == "dalio")
    assert dalio["action"] == "reduce"


def test_portfolio_master_matrix_marks_bottom():
    """马克斯：底部+低估 → strong_buy。"""
    from services.portfolio_intelligence import build_portfolio_master_matrix
    portfolio_report = {
        "quality": {"score": 60}, "drawdown": {"score": 30}, "trend": {"score": 30},
        "capital": {"score": 50}, "sentiment": {"score": 30}, "valuation": {"score": 80},
        "fundamental": {"score": 50},
    }
    risk_metrics = {"effective_n": 2.0, "avg_correlation": 0.4, "max_drawdown": 0.35, "sharpe_ratio": -0.5}
    result = build_portfolio_master_matrix(portfolio_report, risk_metrics, [])
    marks = next(m for m in result["masters"] if m["master_key"] == "marks")
    assert marks["action"] == "strong_buy"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
