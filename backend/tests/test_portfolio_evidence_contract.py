"""组合分析必须绑定当前事实，金额与权重由程序而非模型计算。"""
import copy

import pytest


@pytest.fixture
def snapshot():
    return {
        "user_id": "default", "as_of": "2026-10-18T20:00:00",
        "invested_value": 521756.13, "cash_balance": 505898.39,
        "total_assets": 1027654.52, "target_equity_ratio": 15,
        "concentration_policy": {"max_single_position_pct": 15, "denominator": "total_assets"},
        "funds": [
            {"fund_code": "014846", "fund_name": "债券基金", "fund_category": "bond", "current_value": 158674.88, "shares": 120000},
            {"fund_code": "INDEX", "fund_name": "股票指数", "fund_category": "index", "current_value": 206800, "shares": 100000},
            {"fund_code": "MIX", "fund_name": "混合基金", "fund_category": "hybrid", "current_value": 16300, "shares": 10000},
            {"fund_code": "GOLD", "fund_name": "黄金ETF联接", "fund_category": "index", "current_value": 2000, "shares": 1000},
            {"fund_code": "BOND", "fund_name": "其余债券", "fund_category": "bond", "current_value": 137981.25, "shares": 100000},
        ],
    }


def test_fingerprint_ignores_read_times_and_order_but_changes_with_economic_facts(snapshot):
    from services.portfolio.analysis_contract import portfolio_fingerprint
    changed = copy.deepcopy(snapshot)
    changed["as_of"] = "2026-10-19T12:00:00"
    changed["funds"].reverse()
    changed["funds"][0]["updated_at"] = "anything"
    assert portfolio_fingerprint(changed) == portfolio_fingerprint(snapshot)
    changed["funds"][0]["current_value"] += 1
    assert portfolio_fingerprint(changed) != portfolio_fingerprint(snapshot)


def test_history_requires_verified_matching_portfolio_basis(snapshot):
    from services.portfolio.analysis_contract import portfolio_analysis_basis, filter_portfolio_conclusions
    changed = copy.deepcopy(snapshot)
    changed["cash_balance"] -= 200000
    rows = [
        {"id": 1, "summary": "旧组合两只合计51%", "data_basis": None},
        {"id": 2, "data_basis": [portfolio_analysis_basis(changed, "validated")]},
        {"id": 3, "data_basis": [portfolio_analysis_basis(snapshot)]},
        {"id": 4, "data_basis": [portfolio_analysis_basis(snapshot, "validated")]},
    ]
    assert [r["id"] for r in filter_portfolio_conclusions(rows, snapshot)] == [4]


def test_retained_cash_sale_recalculates_invested_denominator(snapshot):
    from services.portfolio.analysis_contract import sale_to_target, simulate_sale
    assert sale_to_target(snapshot, "014846", 20, "invested_value") == 67904.57
    assert sale_to_target(snapshot, "014846", 15, "total_assets") == 4526.70
    assert simulate_sale(snapshot, "014846", 45000)["invested_pct"] == pytest.approx(23.8434, abs=.001)
    assert simulate_sale(snapshot, "014846", 55000)["invested_pct"] == pytest.approx(22.21179, abs=.001)
    assert simulate_sale(snapshot, "014846", 55000)["total_assets"] == snapshot["total_assets"]
    assert sale_to_target(snapshot, "014846", 20, "invested_value", proceeds="reinvest") == 54323.65


def test_allocation_excludes_gold_and_preserves_user_equity_target(snapshot):
    from services.portfolio.analysis_contract import build_portfolio_calculations
    result = build_portfolio_calculations(snapshot)
    assert result["category_values"]["gold"] == 2000
    assert result["equity_proxy_value"] == 206800
    assert result["target_equity_ratio"] == 15
    assert result["equity_cash_scenarios"][120000]["equity_proxy_pct"] == pytest.approx(31.80, abs=.01)
    assert result["equity_cash_scenarios"][250000]["equity_proxy_pct"] == pytest.approx(44.45, abs=.01)
    assert result["single_position_limits"]["014846"]["sale_to_kyc_limit"] == 4526.70


def test_context_injects_actual_constraints_and_reproducible_calculations(snapshot):
    from services.portfolio.portfolio_context import build_portfolio_context
    text = build_portfolio_context(snapshot=snapshot)
    assert "4,526.70" in text
    assert "67,904.57" in text
    assert "目标权益比例: 15%" in text
    assert "黄金" in text and "不等于底层股票" in text


def test_fact_layer_does_not_need_connection_for_provided_snapshot(snapshot, monkeypatch):
    from services.portfolio import portfolio_fact_layer as facts
    monkeypatch.setattr(facts, "_get_conn_once", lambda *a: (None, False))
    result = facts._build_snapshot(snapshot=snapshot)
    assert result["total_assets"] == 1027654.52
    assert result["calculations"]["single_position_limits"]["014846"]["sale_to_kyc_limit"] == 4526.70


def test_historical_injection_rejects_legacy_unverified_conclusions(snapshot, monkeypatch):
    # 提取入口函数以避免导入完整 Agent 栈触发模型/可选依赖。
    import ast
    import logging
    from pathlib import Path
    import db
    from db import analysis_conclusions as conclusions
    source = Path(__file__).parents[1] / "agent/core/orchestrator.py"
    tree = ast.parse(source.read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_inject_analysis_context")
    namespace = {"logger": logging.getLogger(__name__)}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
    monkeypatch.setattr(db, "get_portfolio_snapshot", lambda *a, **kw: snapshot)
    monkeypatch.setattr(conclusions, "get_latest_analysis_conclusions", lambda **kw: [
        {"target_subject": "整体组合", "summary": "旧组合51%", "confidence": .9},
    ])
    assert namespace["_inject_analysis_context"]("风险分析") == ("风险分析", "")


def test_kyc_constraints_survive_market_tool_failure(snapshot, monkeypatch):
    from services.portfolio import portfolio_fact_layer as facts
    import tools
    def unavailable():
        raise RuntimeError("market unavailable")
    monkeypatch.setattr(tools, "_get_bond_temperature", unavailable)
    monkeypatch.setattr(facts, "_get_conn_once", lambda *a: (None, False))
    result = facts._build_constraints(snapshot)
    assert any("014846" in item for item in result["no_add_targets"])
    assert result["max_single_position_pct"] == 15
    assert result["target_equity_ratio"] == 15


def test_kyc_concentration_compares_unrounded_values(snapshot, monkeypatch):
    from services.portfolio import portfolio_fact_layer as facts
    import tools
    import json
    snapshot["funds"][0]["current_value"] = snapshot["total_assets"] * .15001
    monkeypatch.setattr(tools, "_get_bond_temperature", lambda: json.dumps({"temperature": 93, "date": "2026-10-16"}))
    result = facts._build_constraints(snapshot)
    assert any("014846" in item for item in result["no_add_targets"])
    assert "债券型基金" not in result["no_add_targets"]
    assert result["bond_temperature_date"] == "2026-10-16"


def test_fact_layer_never_substitutes_invested_for_kyc_total_assets(snapshot):
    from services.portfolio.portfolio_fact_layer import _build_snapshot
    del snapshot["total_assets"]
    result = _build_snapshot(snapshot=snapshot)
    assert result["total_assets"] == 1027654.52
    assert all(r["denominator"] == "total_assets" for r in result["overconcentrated"])
    assert not any(r["fund_code"] == "BOND" for r in result["overconcentrated"])


def test_context_uses_existing_cash_purpose_and_expenses(snapshot):
    from services.portfolio.portfolio_context import build_portfolio_context
    snapshot["kyc_context"] = {"monthly_expense": 5000, "fund_usage": "闲置资金", "liquidity_needs": "1年内不动"}
    context = build_portfolio_context(snapshot=snapshot)
    assert "月支出 ¥5,000.00" in context
    assert "闲置资金" in context and "1年内不动" in context
