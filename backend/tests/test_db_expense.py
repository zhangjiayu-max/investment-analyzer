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
