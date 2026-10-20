"""家庭财务与持仓管理必须使用同一账户现金口径。"""

import asyncio
import pytest

from db.portfolio import create_holding, get_portfolio_summary, set_cash_balance
from db.expense import get_cashflow


@pytest.fixture(autouse=True)
def no_daily_interest(monkeypatch):
    monkeypatch.setattr('db.portfolio.accrue_cash_interest', lambda *_: {})


def household():
    create_holding('asset-a', 'A', shares=100, cost_price=1, current_price=2, account='小鱼儿')
    create_holding('asset-b', 'B', shares=50, cost_price=1, current_price=3, account='花无缺')
    set_cash_balance('小鱼儿', 400)
    set_cash_balance('花无缺', 100)
    set_cash_balance('default', 9000)  # 历史余额，不应与分账户余额重复或混用
    set_cash_balance('another-user', 80000)


def test_household_summary_uses_account_cash_instead_of_legacy_default(tmp_db):
    household()
    summary = get_portfolio_summary()
    assert summary['cash_balance'] == 500
    assert summary['total_assets'] == 850
    assert summary['cash_scope'] == 'household_accounts'
    assert {r['account']: r['balance'] for r in summary['cash_accounts']} == {'小鱼儿': 400, '花无缺': 100}


@pytest.mark.parametrize('account,value,cash', [('小鱼儿', 200, 400), ('花无缺', 150, 100)])
def test_selected_account_cash_matches_selected_holdings(tmp_db, account, value, cash):
    household()
    summary = get_portfolio_summary(account=account)
    assert summary['total_value'] == value
    assert summary['cash_balance'] == cash
    assert summary['total_assets'] == value + cash


def test_zero_account_cash_does_not_restore_old_default_balance(tmp_db):
    household()
    set_cash_balance('小鱼儿', 0)
    set_cash_balance('花无缺', 0)
    assert get_portfolio_summary()['cash_balance'] == 0


def test_legacy_cash_only_database_keeps_existing_balance(tmp_db):
    set_cash_balance('default', 1234)
    assert get_portfolio_summary()['cash_balance'] == 1234


def test_account_without_cash_never_borrows_legacy_balance(tmp_db):
    set_cash_balance('default', 1234)
    create_holding('asset-a', 'A', shares=100, current_price=2, account='小鱼儿')
    assert get_portfolio_summary(account='小鱼儿')['cash_balance'] == 0


def test_other_user_does_not_aggregate_household_accounts(tmp_db):
    household()
    assert get_portfolio_summary(user_id='another-user')['cash_balance'] == 80000
    assert get_cashflow(user_id='another-user')['cash_balance'] == 80000


def test_editable_holding_account_cannot_read_another_users_cash(tmp_db):
    set_cash_balance('another-user', 80000)
    create_holding('asset-a', 'A', shares=1, current_price=2, account='another-user')
    assert get_portfolio_summary(account='another-user')['cash_balance'] == 0


def test_household_cashflow_uses_same_cash_without_writing_while_reading(tmp_db):
    household()
    from db._conn import _get_conn
    conn = _get_conn()
    before = [tuple(r) for r in conn.execute('SELECT * FROM portfolio_cash ORDER BY user_id')]
    conn.close()
    assert get_cashflow()['cash_balance'] == 500
    conn = _get_conn()
    assert [tuple(r) for r in conn.execute('SELECT * FROM portfolio_cash ORDER BY user_id')] == before
    conn.close()


def test_family_dashboard_and_health_reuse_portfolio_asset_breakdown(tmp_db, monkeypatch):
    household()
    from routers.dashboard import finance_dashboard as route
    from services.health.health_v2_service import build_asset_overview
    monkeypatch.setattr(route, 'build_allocation_dashboard', lambda *_: {})
    monkeypatch.setattr(route, 'run_portfolio_stress_test', lambda *_: {})
    nw = asyncio.run(route.get_finance_dashboard())['net_worth']
    assert nw['cash_balance'] == 500
    assert nw['total_assets'] == nw['holding_value'] + nw['cash_balance'] == 850
    assert {r['account'] for r in nw['cash_accounts']} == {'小鱼儿', '花无缺'}
    assert build_asset_overview()['total_assets'] == 850


def test_risk_and_allocation_use_same_household_total(tmp_db):
    household()
    from services.portfolio.stress_test import run_portfolio_stress_test
    from services.portfolio.allocation_dashboard import build_allocation_dashboard
    stress = run_portfolio_stress_test()
    assert stress['total_assets'] == 850
    assert next(r for r in stress['asset_impacts'] if r['category'] == 'cash')['current_amount'] == 500
    allocation = build_allocation_dashboard()
    assert allocation['total_assets'] == 850
    assert allocation['cash_balance'] == 500


def test_bucket_sync_receives_numeric_canonical_cash(tmp_db, monkeypatch):
    household()
    from routers import profile
    monkeypatch.setattr(profile, 'sync_bucket_from_portfolio', lambda user, total, cash: {'total': total, 'cash': cash})
    assert profile.api_sync_buckets() == {'total': 850, 'cash': 500}


def test_forecast_starts_from_same_household_cash(tmp_db, monkeypatch):
    household()
    from services.finance import finance_planner
    monkeypatch.setattr(finance_planner, 'get_cashflow', lambda: {'monthly_income': 0, 'monthly_expense': 0})
    monkeypatch.setattr(finance_planner, 'list_goal_buckets', lambda: [])
    assert finance_planner.forecast_cash_flow(months=1)[0]['cash_balance'] == 500
