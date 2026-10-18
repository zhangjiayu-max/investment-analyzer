"""支出记录与现金流数据层回归测试。"""

from datetime import date

from db.dashboard import update_user_profile
from db.expense import create_expense, get_cashflow, set_expense_month_complete
from services.advisor import position_sizing


def test_cashflow_uses_records_for_requested_month(tmp_db):
    update_user_profile(
        "default",
        monthly_income=10000,
        monthly_expense=2500,
        monthly_surplus=7500,
    )
    create_expense("2026-09", "餐饮", 1800)

    set_expense_month_complete("2026-09", True)
    cashflow = get_cashflow(month="2026-09")

    assert cashflow["month"] == "2026-09"
    assert cashflow["monthly_expense"] == 1800
    assert cashflow["monthly_surplus"] == 8200
    assert cashflow["expense_source"] == "records"


def test_cashflow_exposes_recurring_detail_and_known_expense(tmp_db):
    from db.recurring_expenses import create_recurring_template, pay_recurring_bill
    update_user_profile("default", monthly_income=10000, monthly_expense=6000)
    loan = create_recurring_template("车贷", "车贷", 6364.87, "monthly", "2026-10")
    insurance = create_recurring_template("保险", "保险", 377.27, "monthly", "2026-10")
    pay_recurring_bill(loan, "2026-10")
    pay_recurring_bill(insurance, "2026-10")
    cf = get_cashflow(month="2026-10")
    assert cf["income_known"] is True
    assert cf["expense_estimate_complete"] is True
    assert cf["recorded_debt_payment"] == 6364.87
    assert cf["pending_debt_payment"] == 0
    assert cf["recurring_paid"] == 6742.14
    assert cf["recurring_due_total"] == 6742.14
    assert cf["recurring_count"] == 2
    assert {row["category"]: row["total"] for row in cf["recurring_by_category"]} == {"车贷": 6364.87, "保险": 377.27}


def test_goal_aware_cash_constraint_uses_current_cashflow(tmp_db, monkeypatch):
    current_month = date.today().strftime("%Y-%m")
    update_user_profile(
        "default",
        monthly_income=10000,
        monthly_expense=2500,
        monthly_surplus=7500,
    )
    create_expense(current_month, "餐饮", 1800)
    monkeypatch.setattr(position_sizing, "get_config_bool", lambda *_: True)
    monkeypatch.setattr(position_sizing, "get_cash_balance", lambda *_: {"balance": 0})

    constraint = position_sizing.calc_cash_constraint("default", {}, [], 100000)

    cashflow = get_cashflow(month=current_month)
    assert constraint["monthly_inflow"] == cashflow["investable_surplus"] == 0
    assert cashflow["monthly_surplus"] == 7500
    assert constraint["monthly_inflow_source"] == "household_cashflow"
