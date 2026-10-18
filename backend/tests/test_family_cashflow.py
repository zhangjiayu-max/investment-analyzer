"""家庭现金流：不完整记账、固定项身份及可投资资金回归。"""
import pytest

from db.dashboard import get_user_profile, update_user_profile
from db.expense import create_expense, copy_fixed_expenses, get_cashflow, list_expenses


def test_partial_expense_keeps_budget_and_profile(tmp_db):
    update_user_profile(monthly_income=10000, monthly_expense=6000)
    create_expense('2026-10', '车贷', 3000, is_fixed=True)
    cf = get_cashflow(month='2026-10')
    assert cf['monthly_expense'] == 6000
    assert cf['recorded_expense'] == 3000
    assert cf['monthly_surplus'] == 4000
    assert cf['expense_complete'] is False
    assert get_user_profile()['monthly_expense'] == 6000


def test_fixed_copy_preserves_equal_premiums_and_repeat_is_idempotent(tmp_db):
    for note in ['家人A', '家人B', '家人B']:
        create_expense('2026-09', '保险', 500, is_fixed=True, note=note)
    assert copy_fixed_expenses('2026-09', '2026-10') == 3
    assert copy_fixed_expenses('2026-09', '2026-10') == 0
    assert len(list_expenses('2026-10')) == 3


def test_complete_month_uses_actual_and_can_reopen(tmp_db):
    from db.expense import set_expense_month_complete
    update_user_profile(monthly_income=10000, monthly_expense=6000)
    create_expense('2026-10', '餐饮', 1800)
    set_expense_month_complete('2026-10', True)
    assert get_cashflow(month='2026-10')['monthly_expense'] == 1800
    assert get_cashflow(month='2026-10')['expense_source'] == 'records'
    set_expense_month_complete('2026-10', False)
    # 明确的预算不应因一次结账被重写。
    assert get_user_profile()['monthly_expense'] == 6000
    assert get_cashflow(month='2026-10')['monthly_expense'] == 6000


@pytest.mark.parametrize('amount', [float('nan'), float('inf'), -1, 0])
def test_expense_rejects_nonfinite_or_nonpositive_amount(tmp_db, amount):
    with pytest.raises(ValueError):
        create_expense('2026-10', '餐饮', amount)


@pytest.mark.parametrize('month', ['2026-1', '2026-13', '2026-10-extra'])
def test_expense_month_is_canonical(tmp_db, month):
    with pytest.raises(ValueError):
        create_expense(month, '餐饮', 100)


def test_zero_or_negative_cashflow_never_falls_back_to_positive_global_budget(tmp_db, monkeypatch):
    from db.expense import _current_month
    from services.advisor import position_sizing as sizing
    update_user_profile(monthly_income=1000, monthly_expense=2000)
    monkeypatch.setattr(sizing, 'get_config_bool', lambda *_: True)
    monkeypatch.setattr(sizing, 'get_config_float', lambda *_: 9000)
    monkeypatch.setattr(sizing, 'get_cash_balance', lambda *_: {'balance': 0})
    cf = get_cashflow(month=_current_month())
    assert cf['monthly_surplus'] == -1000
    result = sizing.calc_cash_constraint('default', {}, [], 100000)
    assert result['monthly_inflow'] == 0
    assert result['total_available_3m'] == 0


def test_legacy_cashflow_switch_cannot_bypass_household_budget(tmp_db, monkeypatch):
    from services.advisor import position_sizing as sizing
    update_user_profile(monthly_income=1000, monthly_expense=2000)
    monkeypatch.setattr(sizing, 'get_config_bool', lambda *_: False)
    monkeypatch.setattr(sizing, 'get_config_float', lambda *_: 9000)
    monkeypatch.setattr(sizing, 'get_cash_balance', lambda *_: {'balance': 0})
    assert sizing.calc_cash_constraint('default', {}, [], 100000)['monthly_inflow'] == 0


def test_recurring_payment_moves_pending_to_actual_without_double_count(tmp_db):
    from db.recurring_expenses import create_recurring_template, pay_recurring_bill
    from db.expense import delete_expense
    update_user_profile(monthly_income=10000, monthly_expense=6000)
    template = create_recurring_template('车贷', '车贷', 3000, 'monthly', '2026-10')
    before = get_cashflow(month='2026-10')
    assert before['pending_bills'] == before['debt_payment'] == 3000
    expense = pay_recurring_bill(template, '2026-10')
    after = get_cashflow(month='2026-10')
    assert after['pending_bills'] == 0
    assert after['recorded_expense'] == after['debt_payment'] == 3000
    assert before['monthly_expense'] == after['monthly_expense'] == 6000
    assert copy_fixed_expenses('2026-10', '2026-11') == 0
    delete_expense(expense)
    assert get_cashflow(month='2026-10')['pending_bills'] == 3000


def test_yearly_insurance_reserves_only_outside_due_month(tmp_db):
    from db.recurring_expenses import create_recurring_template, pay_recurring_bill
    update_user_profile(monthly_income=20000, monthly_expense=6000)
    template = create_recurring_template('年度保费', '保险', 12000, 'yearly', '2026-01')
    january = get_cashflow(month='2026-01')
    assert january['monthly_expense'] == 12000
    assert january['future_provision'] == 0
    pay_recurring_bill(template, '2026-01')
    assert get_cashflow(month='2026-01')['monthly_expense'] == 12000
    february = get_cashflow(month='2026-02')
    assert february['monthly_provision'] == february['future_provision'] == 1000
    assert february['pending_bills'] == 0


def test_emergency_gap_reduces_investable_surplus(tmp_db):
    from db.portfolio import set_cash_balance
    update_user_profile(monthly_income=10000, monthly_expense=2000)
    set_cash_balance('default', 10000)
    cf = get_cashflow(month='2026-10')
    assert cf['emergency_target'] == 12000
    assert cf['emergency_gap'] == cf['bucket_reserve'] == 2000
    assert cf['investable_surplus'] == 6000


def test_financial_diagnosis_uses_household_expense_and_never_infers_insurance_cover(tmp_db):
    from services.finance.household_finance import build_household_diagnosis
    update_user_profile(monthly_income=10000, monthly_expense=6000)
    create_expense('2026-10', '车贷', 4500, is_fixed=True)
    result = build_household_diagnosis(cashflow=get_cashflow(month='2026-10'))
    assert result['debt_to_income'] == .45
    assert result['status'] == 'attention'
    assert result['insurance_status'] == 'unknown'
    assert result['liabilities_status'] == 'unknown'
    assert result['warnings']


def test_cash_deploy_action_respects_protected_household_cash(tmp_db, monkeypatch):
    from services.health import health_v2_service as health
    update_user_profile(monthly_income=10000, monthly_expense=6000)
    monkeypatch.setattr('db.expense.get_cashflow', lambda *_: {
        'data_available': True, 'protected_cash': 50000, 'investable_surplus': 0,
    })
    actions = health._generate_actions_from_cash('default', {'cash_ratio': .5, 'cash_balance': 30000})
    assert actions == []


def test_unknown_liabilities_do_not_become_zero_or_fake_net_worth(tmp_db, monkeypatch):
    from services.health import health_v2_service as health
    monkeypatch.setattr(health, 'get_portfolio_summary', lambda **_: {'total_value': 5000, 'cash_balance': 1000})
    monkeypatch.setattr(health, 'get_total_cash_balance', lambda: 1000)
    overview = health.build_asset_overview()
    assert overview['total_assets'] == 6000
    assert overview['total_liabilities'] is None
    assert overview['net_worth'] is None


def test_expense_cashflow_endpoint_honors_requested_month(tmp_db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.expense import router
    app = FastAPI()
    app.include_router(router)
    create_expense('2026-09', '餐饮', 123)
    response = TestClient(app).get('/api/expense/cashflow?month=2026-09')
    assert response.status_code == 200
    assert response.json()['month'] == '2026-09'
    assert response.json()['recorded_expense'] == 123


def test_old_expense_schema_upgrades_idempotently_without_losing_records(tmp_db):
    from db.expense import init_expense_tables
    from db._conn import _get_conn
    conn = _get_conn()
    conn.execute('DROP TABLE expense_records')
    conn.execute('''CREATE TABLE expense_records (
        id INTEGER PRIMARY KEY, user_id TEXT DEFAULT 'default', month TEXT,
        category TEXT, sub_category TEXT, amount REAL, is_fixed INTEGER DEFAULT 0,
        note TEXT, created_at TEXT, updated_at TEXT)''')
    conn.execute("INSERT INTO expense_records(id,month,category,amount,is_fixed) VALUES(42,'2026-09','保险',500,1)")
    init_expense_tables(conn)
    init_expense_tables(conn)
    conn.commit()
    conn.close()
    assert copy_fixed_expenses('2026-09', '2026-10') == 1
    assert copy_fixed_expenses('2026-09', '2026-10') == 0
    assert list_expenses('2026-09')[0]['id'] == 42


@pytest.mark.parametrize('payload', [
    {'monthly_income': -1}, {'monthly_expense': -1},
    {'monthly_income': 'NaN'}, {'monthly_expense': 'Infinity'},
])
def test_profile_rejects_invalid_financial_budget(tmp_db, payload):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.profile import router
    app = FastAPI()
    app.include_router(router)
    assert TestClient(app).put('/api/profile', json=payload).status_code == 422


def test_recurring_paid_record_cannot_be_edited_directly(tmp_db):
    from db.recurring_expenses import create_recurring_template, pay_recurring_bill
    from db.expense import update_expense
    identifier = create_recurring_template('车贷', '车贷', 3000, 'monthly', '2026-01')
    actual = pay_recurring_bill(identifier, '2026-10')
    with pytest.raises(ValueError, match='已支付'):
        update_expense(actual, amount=100)


def test_copy_back_to_original_month_does_not_duplicate_source(tmp_db):
    create_expense('2026-09', '保险', 500, is_fixed=True)
    assert copy_fixed_expenses('2026-09', '2026-10') == 1
    assert copy_fixed_expenses('2026-10', '2026-09') == 0
    assert len(list_expenses('2026-09')) == 1


def test_missing_household_budget_never_shows_monthly_purchase_amount(tmp_db):
    from services.advisor import position_sizing as sizing
    holding = {'fund_code': 'test', 'fund_name': '测试', 'current_value': 1000,
               'total_cost': 1000, 'shares': 1000, 'current_price': 1}
    result = sizing.generate_position_sizing_plan(
        holding=holding, cfg={}, total_assets=100000, all_holdings=[holding],
        kelly={'limit_pct': 25}, valuation={'percentile': 10},
        fund_type='equity', type_strategy={'hard_cap_pct': 25},
    )
    assert result['cash_constraint']['data_available'] is False
    assert result['target_driven_monthly'] == 0
    assert result['shares'] == 0
    assert not result['first_position']['needed']
    assert '收支' in result['summary']


@pytest.mark.parametrize('final_amount,can_add,expected', [(700, True, 700), (700, False, 0), (0, True, 0)])
def test_diagnosis_buy_action_uses_final_budgeted_amount(tmp_db, monkeypatch, final_amount, can_add, expected):
    from services.advisor import smart_add_planner as planner
    from services.health import health_v2_service as health
    monkeypatch.setattr(planner, 'generate_smart_add_plan', lambda **_: {'plans': [{
        'fund_code': 'test', 'fund_name': '测试', 'profit_rate': -.1,
        'pyramid': {'triggered_tiers': [1], 'suggested_amount': 2500},
        'final_suggested_amount': final_amount, 'safety': {'can_add': can_add},
    }]})
    actions = health._generate_actions_from_smart_add('default')
    assert (actions[0]['amount'] if actions else 0) == expected


def test_cashflow_read_is_consistent_when_payment_commits_between_aggregates(tmp_db, monkeypatch):
    import db.expense as expenses
    from db.recurring_expenses import create_recurring_template, pay_recurring_bill
    update_user_profile(monthly_income=20000, monthly_expense=6000)
    identifier = create_recurring_template('年缴保费', '保险', 12000, 'yearly', '2026-01')
    original = expenses.get_expense_summary
    monkeypatch.setattr(expenses, '_auto_fill_profile', lambda *_: None)
    def pay_after_actual_snapshot(*args, **kwargs):
        result = original(*args, **kwargs)
        pay_recurring_bill(identifier, '2026-01')
        return result
    monkeypatch.setattr(expenses, 'get_expense_summary', pay_after_actual_snapshot)
    cf = expenses.get_cashflow(month='2026-01')
    assert cf['monthly_expense'] == 12000
    assert cf['recorded_expense'] + cf['pending_bills'] == 12000
