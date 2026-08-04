"""智能补仓 P0 治理的关键回归测试。"""

from db import valuations
from services.advisor import position_sizing, smart_add_planner


def test_percentile_contract_and_best_valuation_output(monkeypatch):
    assert valuations._normalize_percentile(0.4274) == 42.74
    assert valuations._normalize_percentile(0) == 0
    assert valuations._normalize_percentile(1) == 1
    assert valuations._normalize_percentile("84.71%") == 84.71

    monkeypatch.setattr(
        valuations,
        "get_latest_valuation",
        lambda *args, **kwargs: {
            "index_code": "TEST",
            "index_name": "测试指数",
            "current_value": 10,
            "percentile": 0.4274,
            "snapshot_date": "2026-08-04",
        },
    )
    monkeypatch.setattr(valuations, "_log_valuation_query", lambda *args, **kwargs: None)

    result = valuations.get_best_valuation("TEST", enable_online=False)

    assert result["percentile"] == 42.74

    from db._conn import _get_conn
    from scripts.migrate_percentile_scale import migrate_percentile_scale

    conn = _get_conn()
    conn.execute(
        """INSERT INTO index_valuations
           (index_code, index_name, snapshot_date, metric_type, percentile)
           VALUES ('RATIO', '比例指数', '2026-08-04', '市盈率', 0.25)"""
    )
    conn.commit()
    dry_run = migrate_percentile_scale(conn, apply=False)
    assert dry_run["index_valuations"]["candidates"] == 1
    applied = migrate_percentile_scale(conn, apply=True)
    value = conn.execute(
        "SELECT percentile FROM index_valuations WHERE index_code = 'RATIO'"
    ).fetchone()["percentile"]
    conn.close()
    assert applied["index_valuations"]["updated"] == 1
    assert value == 25


def test_known_zero_cash_blocks_target_position_growth():
    result = position_sizing.calc_target_position(
        kelly={"limit_pct": 25},
        valuation={"percentile": 20},
        fund_type="broad",
        type_strategy={"hard_cap_pct": 40},
        exposure_warning={"room_pct": 40},
        cash_constraint={"position_room_pct": 0, "data_available": True},
    )

    assert result["target_pct"] == 0


def test_portfolio_budget_caps_total_add_amount():
    plans = [
        {"fund_code": "A", "final_suggested_amount": 6000, "safety": {"can_add": True}},
        {"fund_code": "B", "final_suggested_amount": 4000, "safety": {"can_add": True}},
    ]

    result = smart_add_planner._allocate_portfolio_budget(plans, monthly_budget=5000)

    assert [p["final_suggested_amount"] for p in plans] == [3000, 2000]
    assert result["total_raw_demand"] == 10000
    assert result["allocation_scale"] == 0.5
    assert sum(p["final_suggested_amount"] for p in plans) <= 5000


def test_portfolio_budget_applies_single_asset_and_blocking_limits():
    plans = [
        {
            "fund_code": "CAP",
            "effective_base": 24900,
            "final_suggested_amount": 5000,
            "safety": {"can_add": True, "max_position_pct": 25},
            "position_sizing": {"target_position": {"target_pct": 25}},
        },
        {
            "fund_code": "EXIT",
            "effective_base": 10000,
            "final_suggested_amount": 1000,
            "safety": {"can_add": True, "max_position_pct": 25},
            "exit_signals": [{"triggered": True}],
        },
        {
            "fund_code": "RISK",
            "effective_base": 10000,
            "final_suggested_amount": 1000,
            "safety": {"can_add": True, "max_position_pct": 25},
            "fund_health": {"healthy": False},
        },
    ]

    smart_add_planner._allocate_portfolio_budget(
        plans, monthly_budget=10000, total_assets=100000, max_add_mult=1
    )

    assert [p["final_suggested_amount"] for p in plans] == [100, 0, 0]


def test_monthly_budget_tightens_on_missing_data_and_deducts_used_amount():
    assert smart_add_planner._resolve_monthly_budget(
        {"data_available": False}, pool_total=10000, period_used=0
    )["monthly_budget"] == 0
    result = smart_add_planner._resolve_monthly_budget(
        {"data_available": True, "usable_cash": 9000, "monthly_inflow": 1000},
        pool_total=8000,
        period_used=3000,
    )
    assert result["monthly_budget"] == 5000


def test_executable_amount_uses_budgeted_final_value():
    plan = {
        "final_suggested_amount": 1200,
        "pyramid": {"released_amount": 5000},
        "safety": {"can_add": True},
    }

    assert smart_add_planner._get_executable_add_amount(plan) == 1200


def test_candidate_requires_triggered_buy_signal(monkeypatch):
    from db import decisions

    created = []
    monkeypatch.setattr(
        decisions,
        "create_candidate_from_structured_recommendation",
        lambda payload, user_id="default": created.append((user_id, payload)),
    )
    plan = {
        "fund_code": "A",
        "fund_name": "测试基金",
        "engine1": {"monthly_dca": 1000},
        "final_suggested_amount": 1000,
        "safety": {"can_add": True},
        "triggered_signals": [],
    }
    smart_add_planner._plans_to_candidates([plan], user_id="investor")

    assert created == []
    plan["triggered_signals"] = [{"triggered": True, "amount": 1000}]
    smart_add_planner._plans_to_candidates([plan], user_id="investor")
    assert created[0][0] == "investor"
