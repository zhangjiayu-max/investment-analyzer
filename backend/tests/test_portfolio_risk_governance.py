"""风险数值必须使用真实协议、同日期和明确的未知状态。"""
from datetime import date, timedelta

import pytest


def _series(code, start=0, count=60):
    return {
        "fund_code": code,
        "dates": [(date.today() - timedelta(days=count - i - start)).isoformat() for i in range(count)],
        "navs": [1 + i * .001 + (i % 3) * .002 for i in range(count)],
    }


def test_risk_consumes_actual_list_history_contract(monkeypatch):
    from services.portfolio import portfolio_intelligence as risk
    from services import fund_data_service
    series = _series("A")
    rows = [{"nav_date": d, "nav": n} for d, n in zip(series["dates"], series["navs"])]
    monkeypatch.setattr(fund_data_service, "get_or_refresh_fund_nav_history", lambda *a, **k: rows)
    assert risk._fetch_nav_series("A")["navs"] == series["navs"]


def test_inadequate_date_overlap_is_unknown_instead_of_different_dates():
    from services.portfolio.portfolio_intelligence import _align_nav_series
    result = _align_nav_series([_series("A", count=40), _series("B", start=29, count=31)])
    assert result["dates"] == []


def test_max_drawdown_recovers_to_original_peak():
    from services.portfolio.portfolio_intelligence import _calc_max_drawdown
    assert _calc_max_drawdown([100, 80, 100, 120]) == (.2, 1)


def test_missing_risk_is_null_not_zero():
    from services.portfolio.portfolio_intelligence import _default_risk_metrics
    result = _default_risk_metrics("历史净值不足")
    assert result["portfolio_volatility"] is None
    assert result["max_drawdown"] is None
    assert result["coverage_ratio"] == 0


def test_degraded_risk_cannot_produce_buy_or_reduce_advice():
    from services.portfolio.portfolio_intelligence import _build_portfolio_decision, _default_risk_metrics
    report = {key: {"score": 90} for key in ("quality", "valuation", "trend", "fundamental")}
    assert _build_portfolio_decision(report, _default_risk_metrics("缺净值"))["action"] == "wait"


def test_partial_history_exposes_coverage_without_complete_portfolio_claim(monkeypatch):
    from services.portfolio import portfolio_intelligence as risk
    monkeypatch.setattr(risk, "_get_portfolio_weights", lambda *a: [
        {"fund_code": "A", "fund_name": "甲", "weight": .1, "current_value": 1000},
        {"fund_code": "B", "fund_name": "乙", "weight": .9, "current_value": 9000},
    ])
    monkeypatch.setattr(risk, "_fetch_nav_series", lambda code, **kw: _series(code) if code == "A" else None)
    result = risk.calculate_portfolio_risk_metrics()
    assert result["coverage_ratio"] == .1
    assert result["data_status"] == "partial"
    assert result["excluded_fund_codes"] == ["B"]
    assert result["portfolio_volatility"] is None
    assert result["calculation_basis"] == "current_allocation_historical_simulation"


def test_bond_subcategories_share_bond_scenario_shocks():
    from services.portfolio.stress_test import scenario_shock
    assert scenario_shock("bond_pure", "market_drop_20") == -.03
    assert scenario_shock("bond_short", "market_drop_20") == -.03
    assert scenario_shock("bond_hybrid", "market_drop_20") == -.12
    assert scenario_shock("bond_convertible", "market_drop_20") == -.12


def test_unknown_asset_category_has_explicit_assumption():
    from services.portfolio.stress_test import category_risk_assumption
    result = category_risk_assumption("unclassified")
    assert result["assumed"] is True
    assert result["risk_category"] == "equity"


def test_same_index_is_known_duplicate_exposure_without_history(monkeypatch):
    from services.advisor import portfolio_risk as risk
    monkeypatch.setattr(risk, "_fetch_closes", lambda *a: [])
    result = risk.assess_add_position_risk("IDX", ["IDX"])
    assert result["max_correlation"] == 1
    assert result["corr_downweight"] < 1
    assert result["same_index_exposure"] is True


def test_dated_index_returns_use_common_dates():
    from services.advisor.portfolio_risk import align_dated_returns
    a = [{"trade_date": str(i), "close": 100 + i} for i in range(1, 40)]
    b = [{"trade_date": str(i), "close": 200 + i} for i in range(20, 60)]
    result = align_dated_returns({"A": a, "B": b}, min_observations=5)
    assert len(result["dates"]) == 19
    assert len(result["returns"]["A"]) == len(result["returns"]["B"]) == 19
    assert result["dates"][0] == "21"


def test_health_report_requires_sufficient_asset_and_risk_coverage(monkeypatch):
    from services.portfolio import portfolio_intelligence as risk
    from services import fund_analysis
    monkeypatch.setattr(risk, "_get_portfolio_weights", lambda *a: [
        {"fund_code": "A", "fund_name": "甲", "weight": .1, "current_value": 1000},
        {"fund_code": "B", "fund_name": "乙", "weight": .9, "current_value": 9000},
    ])
    def health(code, **kwargs):
        if code == "B":
            raise ValueError("净值缺失")
        return {"report": {"quality": {"score": 90}, "valuation": {"score": 90}}}
    monkeypatch.setattr(fund_analysis, "calculate_fund_health_report", health)
    monkeypatch.setattr(risk, "calculate_portfolio_risk_metrics", lambda *a: risk._default_risk_metrics("缺失"))
    result = risk.calculate_portfolio_health_report()
    assert result["data_status"] != "ok"
    assert result["coverage_ratio"] == .1
    assert result["portfolio_decision"]["action"] == "wait"
