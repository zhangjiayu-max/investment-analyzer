"""固定账单：周期投影、付款幂等、历史快照和输入边界。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

import db
from db._conn import _get_conn


def api(name):
    function = getattr(db, name, None)
    assert callable(function), f"周期账单 API 尚未实现: {name}"
    return function


def template(**overrides):
    fields = dict(name="家庭保险", category="保险", amount=1200,
                  frequency="yearly", start_month="2026-01", due_day=31)
    fields.update(overrides)
    return api("create_recurring_template")(**fields)


def test_monthly_due_day_clamps_for_leap_year_and_cross_year():
    template(frequency="monthly", start_month="2023-12")
    bills = api("list_recurring_bills")
    assert bills("2023-12")[0]["due_date"] == "2023-12-31"
    assert bills("2024-02")[0]["due_date"] == "2024-02-29"
    assert bills("2025-02")[0]["due_date"] == "2025-02-28"
    assert bills("2023-11") == []


def test_quarterly_anchor_crosses_year_and_end_is_inclusive():
    template(frequency="quarterly", start_month="2025-11", end_month="2026-05")
    bills = api("list_recurring_bills")
    assert len(bills("2026-02")) == 1
    assert len(bills("2026-05")) == 1
    assert bills("2026-01") == []
    assert bills("2026-08") == []


def test_summary_reserves_annual_non_due_month_without_double_count():
    template(amount=1200)
    template(name="房贷", category="房贷", amount=3000, frequency="monthly")
    summary = api("get_recurring_summary")("2026-02")
    assert summary["monthly_provision"] == 3100
    assert summary["future_provision"] == 100
    assert summary["pending_total"] == summary["debt_payment"] == 3000
    assert api("get_recurring_summary")("2026-01")["future_provision"] == 0


def test_pay_is_atomic_idempotent_and_equal_premiums_stay_distinct():
    first = template()
    second = template(name="另一份家庭保险")
    pay = api("pay_recurring_bill")
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: pay(first, "2026-01"), range(8)))
    assert len(set(ids)) == 1
    assert pay(second, "2026-01") != ids[0]
    conn = _get_conn()
    assert conn.execute("SELECT COUNT(*) FROM expense_records").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM recurring_expense_payments").fetchone()[0] == 2
    conn.close()
    summary = api("get_recurring_summary")("2026-01")
    assert summary["paid_total"] == 2400
    assert summary["pending_total"] == 0


def test_paid_snapshot_survives_template_edit_and_deactivation():
    identifier = template()
    expense_id = api("pay_recurring_bill")(identifier, "2026-01")
    assert api("update_recurring_template")(
        identifier, name="新名称", amount=2400, start_month="2026-02", due_day=3)
    assert api("delete_recurring_template")(identifier)
    bill = api("list_recurring_bills")("2026-01")[0]
    assert bill == dict(template_id=identifier, month="2026-01", name="家庭保险",
                        category="保险", amount=1200, due_date="2026-01-31",
                        status="paid", expense_id=expense_id)
    assert api("pay_recurring_bill")(identifier, "2026-01") == expense_id
    assert api("list_recurring_bills")("2027-02") == []
    assert api("get_recurring_template")(identifier)["active"] is False


def test_paid_snapshot_excludes_reserve_after_template_schedule_changes():
    identifier = template()
    api("pay_recurring_bill")(identifier, "2026-01")
    api("update_recurring_template")(identifier, start_month="2025-12")
    summary = api("get_recurring_summary")("2026-01")
    assert summary["paid_total"] == 1200
    assert summary["monthly_provision"] == 100
    assert summary["future_provision"] == 0


def test_user_scope_prevents_cross_user_read_write_and_pay():
    identifier = template(user_id="alice")
    assert api("get_recurring_template")(identifier) is None
    assert api("list_recurring_templates")() == []
    assert api("list_recurring_bills")("2026-01") == []
    assert api("update_recurring_template")(identifier, amount=300) is False
    assert api("delete_recurring_template")(identifier) is False
    with pytest.raises(ValueError):
        api("pay_recurring_bill")(identifier, "2026-01")
    assert api("pay_recurring_bill")(identifier, "2026-01", user_id="alice") > 0


@pytest.mark.parametrize("changes", [
    {"amount": 0}, {"amount": -1}, {"amount": float("inf")},
    {"amount": float("nan")}, {"amount": True}, {"name": " "},
    {"name": "x" * 201}, {"note": "x" * 2001}, {"category": "餐饮"},
    {"frequency": "weekly"}, {"start_month": "2026-1"},
    {"start_month": "2026-13"}, {"start_month": "0000-01"},
    {"end_month": "2025-12"}, {"due_day": 0}, {"due_day": 32},
    {"due_day": 1.5}, {"due_day": True}, {"active": "false"},
])
def test_rejects_invalid_template_fields(changes):
    with pytest.raises(ValueError):
        template(**changes)


def test_update_validates_complete_date_range_and_explicit_end_clear():
    identifier = template(end_month="2026-12")
    with pytest.raises(ValueError):
        api("update_recurring_template")(identifier, start_month="2027-01")
    assert api("update_recurring_template")(identifier, end_month=None)
    assert api("get_recurring_template")(identifier)["end_month"] is None
    assert len(api("list_recurring_bills")("2027-01")) == 1


def test_payment_failure_rolls_back_actual_expense():
    identifier = template()
    conn = _get_conn()
    conn.execute("CREATE TRIGGER fail_payment BEFORE INSERT ON recurring_expense_payments "
                 "BEGIN SELECT RAISE(ABORT, 'payment failed'); END")
    conn.commit()
    conn.close()
    with pytest.raises(Exception, match="payment failed"):
        api("pay_recurring_bill")(identifier, "2026-01")
    conn = _get_conn()
    assert conn.execute("SELECT COUNT(*) FROM expense_records").fetchone()[0] == 0
    conn.close()


def test_rejects_non_due_payment_and_counts_due_dates(monkeypatch):
    identifier = template()
    with pytest.raises(ValueError):
        api("pay_recurring_bill")(identifier, "2026-02")
    import db.recurring_expenses as recurring
    monkeypatch.setattr(recurring, "_today", lambda: date(2026, 1, 15))
    template(name="房贷", category="房贷", amount=3000, frequency="monthly", due_day=2)
    summary = api("get_recurring_summary")("2026-01")
    assert summary["overdue_count"] == 1
    assert summary["upcoming_count"] == 1


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.expense import router
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def test_template_and_bill_routes_complete_round_trip(client):
    payload = dict(name="保险", category="保险", amount=1200, frequency="yearly",
                   start_month="2026-01", end_month="2026-12", due_day=31)
    response = client.post("/api/expense/templates", json=payload)
    assert response.status_code == 200
    identifier = response.json()["id"]
    assert len(client.get("/api/expense/templates").json()["items"]) == 1
    assert client.put(f"/api/expense/templates/{identifier}", json={"end_month": None}).status_code == 200
    response = client.get("/api/expense/bills", params={"month": "2027-01"})
    assert response.status_code == 200
    assert response.json()["summary"]["pending_total"] == 1200
    pay = client.post(f"/api/expense/templates/{identifier}/pay", json={"month": "2027-01"})
    assert pay.status_code == 200
    assert pay.json()["expense_id"] > 0
    assert client.delete(f"/api/expense/templates/{identifier}").status_code == 200
    assert client.get("/api/expense/bills", params={"month": "2027-01"}).json()["items"][0]["status"] == "paid"


def test_recurring_routes_reject_invalid_and_missing_records(client):
    response = client.post("/api/expense/templates", json=dict(
        name="保险", category="保险", amount=1200, frequency="yearly",
        start_month="2026-1", due_day=31))
    assert response.status_code in {400, 422}
    assert client.get("/api/expense/bills", params={"month": "2026-1"}).status_code == 400
    assert client.post("/api/expense/templates/999/pay", json={"month": "2026-01"}).status_code == 404
    assert client.delete("/api/expense/templates/999").status_code == 404
    identifier = template()
    assert client.put(f"/api/expense/templates/{identifier}", json={"active": "false"}).status_code == 422
    assert client.put(f"/api/expense/templates/{identifier}", json={"due_day": 1.5}).status_code == 422


def test_month_status_route_precedes_generic_expense_path(client):
    response = client.put("/api/expense/month-status", json={"month": "2026-01", "complete": True})
    assert response.status_code == 200
    assert response.json()["expense_complete"] is True
