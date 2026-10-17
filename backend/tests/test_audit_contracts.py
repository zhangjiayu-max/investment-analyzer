"""审计回归：启动与跨模块数据契约（外部数据均替换为本地样本）。"""
import os
from pathlib import Path
import subprocess
import sys
import asyncio
from datetime import datetime

import pytest


def test_fresh_database_import_does_not_read_configuration(tmp_path):
    env = dict(os.environ, DB_PATH=str(tmp_path / "fresh.db"), MIMO_API_KEY="", OPENAI_API_KEY="")
    result = subprocess.run(
        [sys.executable, "-c", "import db; from infra.cost_tracker import budget_controller"],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "fresh.db").exists(), "导入模块不能打开尚未初始化的数据库"


def test_decision_exports_keep_both_ledgers_independent():
    import db
    from db import decisions, investment_decisions
    assert db.create_decision is decisions.create_decision
    assert db.get_decision is decisions.get_decision
    assert db.list_decisions is decisions.list_decisions
    assert db.update_decision_status is decisions.update_decision_status
    assert db.create_investment_decision is investment_decisions.create_decision


def test_ledger_endpoint_reads_investment_ledger():
    from db.investment_decisions import create_decision
    from routers.analysis.decision import list_ledger, get_ledger_item
    decision_id = create_decision("watch", "audit", "000001")
    response = asyncio.run(list_ledger(limit=10))
    assert response["code"] == 0
    assert response["data"][0]["id"] == decision_id
    response = asyncio.run(get_ledger_item(decision_id))
    assert response["data"]["source_module"] == "audit"


def test_family_dashboard_uses_portfolio_totals(monkeypatch):
    from routers.dashboard import finance_dashboard as m
    monkeypatch.setattr(m, "get_portfolio_summary", lambda **kw: {
        "total_assets": 1300, "total_cost": 1000, "total_value": 1200,
        "total_profit": 200, "profit_rate": 0.2,
    })
    monkeypatch.setattr(m, "get_cash_balance", lambda *a: {"balance": 100})
    monkeypatch.setattr(m, "build_allocation_dashboard", lambda *a: {})
    monkeypatch.setattr(m, "run_portfolio_stress_test", lambda *a: {})
    nw = asyncio.run(m.get_finance_dashboard())["net_worth"]
    assert nw["holding_value"] == 1200
    assert nw["float_pnl"] == 200
    assert nw["total_return_pct"] == 0.2  # 前端按比率乘100展示


def test_finance_routes_use_api_prefix():
    from routers.dashboard.finance_routes import router
    assert {route.path for route in router.routes} == {
        "/api/finance/cash-flow-forecast", "/api/finance/allocation-suggestion",
        "/api/finance/stress-test", "/api/finance/goals",
    }


def _planner_data(monkeypatch, module):
    monkeypatch.setattr(module, "get_portfolio_summary", lambda: {
        "total_value": 10000, "holdings": [
            {"id": 1, "fund_category": "equity", "current_value": 10000},
        ],
    })
    monkeypatch.setattr(module, "get_cash_balance", lambda: {"balance": 6000})
    monkeypatch.setattr(module, "list_goal_buckets", lambda: [
        {"id": 1, "name": "长期", "bucket_type": "long_term", "target_amount": 36000,
         "current_amount": 0, "linked_holdings": "[1]"},
    ])


def test_cash_forecast_conserves_transfers_and_calendar_months(monkeypatch):
    from services.finance import finance_planner as m
    _planner_data(monkeypatch, m)
    # 支出1000中已包含300债务偿还，不能重复扣除。
    monkeypatch.setattr(m, "get_cashflow", lambda: {
        "monthly_income": 2000, "monthly_expense": 1000,
        "monthly_surplus": 1000, "debt_payment": 300, "expense_source": "records",
    }, raising=False)
    class MonthEnd(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 1, 31)
    monkeypatch.setattr(m, "datetime", MonthEnd)
    rows = m.forecast_cash_flow(3)
    assert [r["month"] for r in rows] == ["2026-01", "2026-02", "2026-03"]
    assert rows[0]["cash_balance"] == 6000
    assert rows[0]["portfolio_value"] == 11050
    assert rows[0]["total_assets"] == 17050


def test_planner_accepts_holdings_list_and_linked_goals(monkeypatch):
    from services.finance import finance_planner as m
    _planner_data(monkeypatch, m)
    assert m.generate_allocation_suggestion()["current_allocation"]["equity"] == 1
    assert m.stress_test("moderate")["impact"]["stressed_total"] == 7500
    assert m.get_goals_progress()["goals"][0]["current_amount"] == 10000


def _save_nav_samples():
    from services.fund.fund_data_service import _save_nav_history
    _save_nav_history([
        {"fund_code": "000001", "nav_date": day, "nav": nav}
        for day, nav in [("2026-01-01", 1), ("2026-01-20", 1.1), ("2026-02-01", 2)]
    ])


def test_watchlist_review_persists_historical_nav(monkeypatch):
    from db.watchlist import create_signal_backtest, list_pending_signal_backtests
    from db._conn import _get_conn
    from services.advisor import watchlist_backtest as m
    _save_nav_samples()
    create_signal_backtest({"watchlist_id": 1, "fund_code": "000001", "signal_date": "2026-01-01",
                            "signal_status": "green", "entry_nav": 1, "review_date": "2026-01-21"})
    import db.portfolio
    monkeypatch.setattr(db.portfolio, "fetch_fund_nav", lambda *a: pytest.fail("历史复盘不应请求实时价"))
    assert m.review_watchlist_signal_backtests() == {"reviewed": 1, "hit": 1, "miss": 0, "skipped": 0}
    assert list_pending_signal_backtests() == []
    with _get_conn() as conn:
        assert conn.execute("SELECT review_nav FROM watchlist_signal_backtests").fetchone()[0] == 1.1


def test_watchlist_missing_history_never_fetches_live_nav(monkeypatch):
    from services.advisor.watchlist_backtest import _get_fund_nav_at_or_before
    import db.portfolio
    calls = []
    monkeypatch.setattr(db.portfolio, "fetch_fund_nav", lambda *a: calls.append(a) or {"nav": 3, "date": "2026-10-17"})
    assert _get_fund_nav_at_or_before("000001", "2026-01-01") is None
    assert calls == []


@pytest.mark.parametrize("windows", [
    [({}, 3), ({"end_date": "2026-01-20"}, 2)],
    [({"start_date": "2026-02-01"}, 1), ({}, 3)],
    [({"end_date": "2026-01-20", "days": 1}, 1), ({}, 3)],
])
def test_nav_cache_preserves_full_series_and_filters_every_window(monkeypatch, windows):
    from services.fund import fund_data_service as m
    _save_nav_samples()
    monkeypatch.setattr(m, "_MEMORY_CACHE", {})
    monkeypatch.setattr(m, "refresh_fund_nav_history", lambda code: None)
    for options, count in windows:
        records = m.get_or_refresh_fund_nav_history("000001", min_local_days=1, **options)
        assert len(records) == count
        if options.get("end_date"):
            assert all(r["nav_date"] <= options["end_date"] for r in records)


def test_dip_unknown_valuation_cannot_trigger(monkeypatch):
    from services.dip_4pct_strategy import DipStrategy4Pct
    from db.dip_investment_plans import create_dip_plan
    create_dip_plan("000001", "测试", 10000)
    strategy = DipStrategy4Pct()
    monkeypatch.setattr(strategy, "is_enabled", lambda: True)
    monkeypatch.setattr(strategy, "_fetch_valuation_percentile", lambda *a: None)
    result = strategy.check_trigger("000001", current_price=1)
    assert not result["triggered"]
    assert "估值" in result["reason"]


def test_dip_last_buy_uses_confirmed_transaction_price(monkeypatch):
    from services.dip_4pct_strategy import DipStrategy4Pct
    from db.dip_investment_plans import create_dip_plan
    from db._conn import _get_conn
    create_dip_plan("000001", "测试", 10000)
    with _get_conn() as conn:
        conn.execute("INSERT INTO portfolio_transactions (fund_code, transaction_type, amount, status, price, transaction_date) VALUES ('000001', 'buy', 100, 'confirmed', 1.01, '2026-01-01')")
        conn.execute("INSERT INTO portfolio_transactions (fund_code, transaction_type, amount, status, price, transaction_date) VALUES ('000001', 'buy', 100, 'pending', 2, '2026-01-02')")
    strategy = DipStrategy4Pct()
    monkeypatch.setattr(strategy, "is_enabled", lambda: True)
    assert strategy._get_last_buy_price("000001") == 1.01
    assert not strategy.check_trigger("000001", current_price=1.05, valuation_percentile=10)["triggered"]


def test_dip_valuation_reads_actual_holdings_table(monkeypatch):
    from services.dip_4pct_strategy import DipStrategy4Pct
    from db import create_holding
    import types
    create_holding("000001", "测试", shares=1, cost_price=1, index_name="沪深300")
    monkeypatch.setitem(sys.modules, "tools", types.SimpleNamespace(_query_valuation=lambda args: '[{"metrics":[{"metric_type":"市盈率","percentile":0}]}]'))
    assert DipStrategy4Pct()._fetch_valuation_percentile("000001") == 0


def test_dip_buy_anchor_respects_user_and_account():
    from services.dip_4pct_strategy import DipStrategy4Pct
    from db import create_holding
    from db._conn import _get_conn
    first = create_holding("000001", "甲", account="账户甲", user_id="alice")
    second = create_holding("000001", "乙", account="账户乙", user_id="alice")
    with _get_conn() as conn:
        for holding_id, price, day in [(first, 1, "2026-01-01"), (second, 2, "2026-01-02")]:
            conn.execute("INSERT INTO portfolio_transactions (user_id, holding_id, fund_code, transaction_type, amount, status, price, transaction_date) VALUES ('alice', ?, '000001', 'buy', 100, 'confirmed', ?, ?)", (holding_id, price, day))
    strategy = DipStrategy4Pct()
    assert strategy._get_last_buy_price("000001", user_id="alice", account="账户甲") == 1
    assert strategy._get_last_buy_price("000001", user_id="alice", account="账户乙") == 2
    assert strategy._get_last_buy_price("000001") is None


def test_dip_trigger_returns_same_record_until_executed(monkeypatch):
    from services.dip_4pct_strategy import DipStrategy4Pct
    from db.dip_investment_plans import create_dip_plan, list_dip_triggers
    create_dip_plan("000001", "测试", 10000)
    strategy = DipStrategy4Pct()
    monkeypatch.setattr(strategy, "is_enabled", lambda: True)
    first = strategy.check_trigger("000001", current_price=1, valuation_percentile=10)
    again = strategy.check_trigger("000001", current_price=0.98, valuation_percentile=10)
    assert first["trigger_id"] == again["trigger_id"]
    assert again["current_price"] == 1  # 待执行建议保留原始快照
    assert len(list_dip_triggers(fund_code="000001")) == 1


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_dip_invalid_valuation_never_triggers(monkeypatch, value):
    from services.dip_4pct_strategy import DipStrategy4Pct
    from db.dip_investment_plans import create_dip_plan
    create_dip_plan("000001", "测试", 10000)
    strategy = DipStrategy4Pct()
    monkeypatch.setattr(strategy, "is_enabled", lambda: True)
    assert not strategy.check_trigger("000001", current_price=1, valuation_percentile=value)["triggered"]


def test_stress_test_does_not_apply_bond_drop_to_money_funds(monkeypatch):
    from services.finance import finance_planner as m
    monkeypatch.setattr(m, "get_portfolio_summary", lambda: {"total_value": 160, "holdings": [
        {"fund_category": "equity", "current_value": 100},
        {"fund_category": "bond", "current_value": 40},
        {"fund_category": "money", "current_value": 20},
    ]})
    assert m.stress_test("mild")["impact"]["stressed_total"] == 149.2


def test_nav_refresh_does_not_store_a_window_subset(monkeypatch):
    from services.fund import fund_data_service as m
    monkeypatch.setattr(m, "_MEMORY_CACHE", {})
    def refresh(code):
        _save_nav_samples()
        return m.get_fund_nav_history_from_cache(code)
    monkeypatch.setattr(m, "refresh_fund_nav_history", refresh)
    assert len(m.get_or_refresh_fund_nav_history("000001", end_date="2026-01-20", days=1)) == 1
    monkeypatch.setattr(m, "refresh_fund_nav_history", lambda code: None)
    assert len(m.get_or_refresh_fund_nav_history("000001")) == 3


def test_cash_forecast_deficit_does_not_fund_investments(monkeypatch):
    from services.finance import finance_planner as m
    _planner_data(monkeypatch, m)
    monkeypatch.setattr(m, "get_cash_balance", lambda: {"balance": 100})
    monkeypatch.setattr(m, "get_cashflow", lambda: {"monthly_income": 500, "monthly_expense": 1000})
    row = m.forecast_cash_flow(1)[0]
    assert row["cash_balance"] == -400
    assert row["monthly_contribution"] == 0
    assert row["total_assets"] == 9650


def test_dip_executed_plan_with_missing_buy_anchor_cannot_restart(monkeypatch):
    from services.dip_4pct_strategy import DipStrategy4Pct
    from db.dip_investment_plans import create_dip_plan, mark_trigger_executed
    create_dip_plan("000001", "测试", 10000)
    strategy = DipStrategy4Pct()
    monkeypatch.setattr(strategy, "is_enabled", lambda: True)
    first = strategy.check_trigger("000001", current_price=1, valuation_percentile=10)
    mark_trigger_executed(first["trigger_id"])
    result = strategy.check_trigger("000001", current_price=0.96, valuation_percentile=10)
    assert not result["triggered"]
    assert "买入" in result["reason"]


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf")])
def test_dip_invalid_current_price_never_triggers(monkeypatch, price):
    from services.dip_4pct_strategy import DipStrategy4Pct
    from db.dip_investment_plans import create_dip_plan
    create_dip_plan("000001", "测试", 10000)
    strategy = DipStrategy4Pct()
    monkeypatch.setattr(strategy, "is_enabled", lambda: True)
    assert not strategy.check_trigger("000001", current_price=price, valuation_percentile=10)["triggered"]


def test_finance_trend_does_not_count_cash_as_investment_profit(monkeypatch):
    from routers.dashboard import finance_dashboard as m
    monkeypatch.setattr(m, "get_portfolio_summary", lambda **kw: {
        "total_assets": 1300, "total_value": 1200, "total_cost": 1000, "profit_rate": 0.2,
    })
    result = asyncio.run(m.get_finance_trend())
    assert result["current_value"] == 1200
    assert result["total_return"] == 0.2


@pytest.mark.parametrize("batch", [False, True])
def test_direct_nav_refresh_keeps_older_database_history_in_warm_cache(monkeypatch, batch):
    from services.fund import fund_data_service as m
    _save_nav_samples()
    monkeypatch.setattr(m, "_MEMORY_CACHE", {})
    # 供应商仅返回最新一条，本地仍有三条。
    monkeypatch.setattr(m, "_fetch_nav_history_from_akshare", lambda code: [
        {"fund_code": code, "nav_date": "2026-02-01", "nav": 2},
    ])
    if batch:
        refreshed = m.batch_refresh_fund_nav_history(["000001"])["000001"]
    else:
        refreshed = m.refresh_fund_nav_history("000001")
    assert len(refreshed) == 3
    monkeypatch.setattr(m, "refresh_fund_nav_history", lambda code: None)
    warm = m.get_or_refresh_fund_nav_history("000001", end_date="2026-01-20")
    m._MEMORY_CACHE.clear()
    cold = m.get_or_refresh_fund_nav_history("000001", end_date="2026-01-20")
    assert warm == cold
    assert len(warm) == 2


def test_concurrent_dip_triggers_return_the_same_persisted_snapshot(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from db import dip_investment_plans as plans
    from services.dip_4pct_strategy import DipStrategy4Pct
    plans.create_dip_plan("000001", "测试", 10000)
    strategy = DipStrategy4Pct()
    monkeypatch.setattr(strategy, "is_enabled", lambda: True)
    create = plans.create_dip_trigger
    barrier = Barrier(2)
    def concurrent_create(**fields):
        barrier.wait(timeout=5)
        return create(**fields)
    monkeypatch.setattr(plans, "create_dip_trigger", concurrent_create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(strategy.check_trigger, "000001", current_price=1, valuation_percentile=10)
        second = pool.submit(strategy.check_trigger, "000001", current_price=0.98, valuation_percentile=12)
        results = [first.result(timeout=10), second.result(timeout=10)]
    saved = plans.list_dip_triggers(fund_code="000001")
    assert len(saved) == 1
    assert results[0] == results[1]
    assert results[0]["trigger_id"] == saved[0]["id"]
    for field in ("current_price", "valuation_percentile", "prev_buy_price", "suggested_amount"):
        assert results[0][field] == saved[0][field]


@pytest.mark.parametrize("operation", ["allocation", "stress", "goals"])
def test_finance_planner_handles_real_unpriced_holdings(operation):
    from db import create_holding, create_goal_bucket, update_goal_bucket
    from services.finance import finance_planner as m
    holding = create_holding("000001", "未定价基金", shares=100, cost_price=1)
    bucket = create_goal_bucket("长期目标", "long_term", target_amount=1000)
    update_goal_bucket(bucket, linked_holdings=f"[{holding}]")
    if operation == "allocation":
        assert m.generate_allocation_suggestion()["current_allocation"]["total_value"] == 0
    elif operation == "stress":
        assert m.stress_test()["impact"]["stressed_total"] == 0
    else:
        assert m.get_goals_progress()["goals"][0]["current_amount"] == 0


def test_decision_precheck_never_queries_absent_pattern_table_or_leaks_connections(monkeypatch):
    from db import decisions as m
    decision_id = m.create_decision(source_type="manual", decision_type="add",
                                    target_type="fund", summary="预检查连接清理")
    get_conn = m._get_conn
    opened = []
    queries = []
    class TrackedConnection:
        def __init__(self):
            self.connection = get_conn()
            self.closed = False
            opened.append(self)
        def execute(self, sql, *args):
            queries.append(sql)
            return self.connection.execute(sql, *args)
        def close(self):
            self.connection.close()
            self.closed = True
    monkeypatch.setattr(m, "_get_conn", TrackedConnection)
    try:
        assert m.build_decision_precheck(decision_id)["exists"] is True
        assert all(conn.closed for conn in opened), "所有预检查连接必须关闭，包括SQL异常分支"
        assert not any("FROM trade_patterns" in sql for sql in queries)
    finally:
        for conn in opened:
            conn.close()
