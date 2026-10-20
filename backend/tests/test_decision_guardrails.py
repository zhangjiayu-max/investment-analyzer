"""同一快照下，新增投资必须满足现金、用途及投后风险约束。"""
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 10, 20, 10, tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    from services.portfolio import decision_guardrails
    monkeypatch.setattr(decision_guardrails, '_now', lambda: NOW)


@pytest.fixture
def inputs():
    funds = [
        {"fund_code": "E", "fund_name": "权益", "fund_category": "index", "index_code": "IDX",
         "shares": 10000, "current_price": 1, "current_value": 10000, "price_updated_at": "2026-10-20"},
        {"fund_code": "B", "fund_name": "纯债", "fund_category": "bond_pure",
         "shares": 20000, "current_price": 1, "current_value": 20000, "price_updated_at": "2026-10-20"},
    ]
    return {
        "snapshot": {"user_id": "default", "funds": funds, "holdings": funds, "records": funds,
                     "cash_balance": 70000, "frozen_cash": 4000, "receivable_cash": 5000,
                     "cash_accounts": [{"account": "花无缺", "balance": 70000}],
                     "valuation_complete": True},
        "cashflow": {"data_available": True, "protected_cash": 10000, "investable_surplus": 3000,
                     "monthly_expense": 1000, "emergency_target": 6000, "emergency_available": 70000,
                     "emergency_gap": 0, "cash_runway_months": 70},
        "policy": {"version": 2, "confirmed": True, "source": "user_confirmed",
                   "max_single_position_pct": 25, "max_equity_pct": 60, "max_same_index_pct": 40,
                   "max_stress_loss_pct": 20, "max_stress_loss_amount": 20000,
                   "nav_max_age_business_days": 3, "valuation_max_age_days": 14,
                   "future_cash_days": 90, "estimated_fee_rate": 0},
        "buckets": [], "valuation_facts": {"IDX": {"percentile": 20, "snapshot_date": "2026-10-19"}},
        "now": NOW,
    }


def context(inputs):
    from services.portfolio.decision_guardrails import build_decision_snapshot
    return build_decision_snapshot(**inputs)


def test_current_cash_excludes_frozen_protected_and_receivable(inputs):
    ctx = context(inputs)
    assert ctx["cash"]["available_now"] == 56000
    assert ctx["cash"]["future_monthly_surplus"] == 3000
    assert ctx["total_assets"] == 105000


def test_future_income_cannot_fund_execution(inputs):
    from services.portfolio.decision_guardrails import evaluate_add_decision
    inputs["snapshot"]["cash_balance"] = 0
    inputs["snapshot"]["frozen_cash"] = 0
    ctx = context(inputs)
    result = evaluate_add_decision("E", 1000, context=ctx)
    assert result["executable_amount"] == 0
    assert result["status"] == "blocked"
    assert any("现金" in reason for reason in result["reasons"])


def test_policy_not_confirmed_waits_for_user_data(inputs):
    from services.portfolio.decision_guardrails import evaluate_add_decision
    inputs["policy"]["confirmed"] = False
    result = evaluate_add_decision("E", 1000, context=context(inputs))
    assert result["status"] == "wait_for_data"
    assert result["executable_amount"] == 0


@pytest.mark.parametrize("field", ["current_value", "current_price"])
def test_missing_price_blocks_complete_risk_judgment(inputs, field):
    from services.portfolio.decision_guardrails import evaluate_add_decision
    inputs["snapshot"]["funds"][1][field] = None
    result = evaluate_add_decision("E", 1000, context=context(inputs))
    assert result["status"] == "wait_for_data"
    assert result["executable_amount"] == 0


def test_stale_nav_blocks_execution_with_visible_reason(inputs):
    from services.portfolio.decision_guardrails import evaluate_add_decision
    inputs["snapshot"]["funds"][0]["price_updated_at"] = "2026-09-01"
    result = evaluate_add_decision("E", 1000, context=context(inputs))
    assert result["status"] == "wait_for_data"
    assert any("净值" in r for r in result["reasons"])


def test_stale_valuation_blocks_equity_but_bond_not_forced_to_have_pe(inputs):
    from services.portfolio.decision_guardrails import evaluate_add_decision
    inputs["valuation_facts"]["IDX"]["snapshot_date"] = "2026-08-01"
    ctx = context(inputs)
    assert evaluate_add_decision("E", 1000, context=ctx)["executable_amount"] == 0
    assert evaluate_add_decision("B", 1000, context=ctx)["executable_amount"] == 1000


def test_single_fund_limit_uses_household_total_assets(inputs):
    from services.portfolio.decision_guardrails import evaluate_add_decision
    result = evaluate_add_decision("E", 30000, context=context(inputs))
    assert result["status"] == "allowed"
    assert result["executable_amount"] == 16250
    assert any("单基金" in r for r in result["reasons"])
    assert result["after"]["single_position_pct"] <= 25


def test_same_index_exposure_cannot_be_disguised_by_another_fund(inputs):
    from services.portfolio.decision_guardrails import evaluate_add_decision
    inputs["snapshot"]["funds"].append({**inputs["snapshot"]["funds"][0], "fund_code": "E2", "current_value": 30000})
    inputs["snapshot"]["holdings"] = inputs["snapshot"]["funds"]
    inputs["policy"]["max_same_index_pct"] = 30
    inputs["policy"]["max_single_position_pct"] = 100
    result = evaluate_add_decision("E", 10000, context=context(inputs))
    assert result["executable_amount"] == 500
    assert result["after"]["same_index_pct"] <= 30


def test_equity_ceiling_caps_new_risk(inputs):
    from services.portfolio.decision_guardrails import evaluate_add_decision
    inputs["policy"]["max_single_position_pct"] = 100
    inputs["policy"]["max_equity_pct"] = 15
    result = evaluate_add_decision("E", 10000, context=context(inputs))
    assert result["executable_amount"] == 5750
    assert any("权益" in r for r in result["reasons"])


def test_stress_loss_budget_caps_incremental_loss(inputs):
    from services.portfolio.decision_guardrails import evaluate_add_decision
    inputs["policy"]["max_stress_loss_amount"] = 5000
    result = evaluate_add_decision("E", 10000, context=context(inputs))
    assert result["executable_amount"] < 10000
    assert result["after"]["worst_loss_amount"] <= 5000
    assert any("压力" in r for r in result["reasons"])


def test_near_term_purpose_cash_is_reserved_not_added_to_assets(inputs):
    inputs["buckets"] = [{"bucket_type": "stable", "name": "学费", "current_amount": 20000, "liquidity_days": 30}]
    ctx = context(inputs)
    assert ctx["cash"]["available_now"] == 36000
    assert ctx["total_assets"] == 105000


def test_incomplete_cashflow_is_unknown_not_free_cash(inputs):
    from services.portfolio.decision_guardrails import evaluate_add_decision
    inputs["cashflow"]["data_available"] = False
    result = evaluate_add_decision("E", 1000, context=context(inputs))
    assert result["status"] == "wait_for_data"
    assert result["executable_amount"] == 0


def test_snapshot_identity_changes_only_when_facts_or_policy_change(inputs):
    a = context(inputs)
    b = context(deepcopy(inputs))
    assert a["snapshot_id"] == b["snapshot_id"]
    inputs["policy"]["version"] += 1
    assert context(inputs)["snapshot_id"] != a["snapshot_id"]


def test_comparisons_include_no_add_and_small_add_with_shared_budget(inputs):
    from services.portfolio.decision_guardrails import evaluate_add_decision
    result = evaluate_add_decision("E", 1000, context=context(inputs))
    assert [s["key"] for s in result["comparisons"]] == ["no_add", "small_add", "planned_add"]
    assert result["comparisons"][0]["amount"] == 0
    assert result["comparisons"][1]["amount"] == 500
    assert result["snapshot_id"]
    assert result["expires_at"]


@pytest.mark.parametrize("amount", [float("nan"), float("inf"), -1])
def test_invalid_amount_never_becomes_action(inputs, amount):
    from services.portfolio.decision_guardrails import evaluate_add_decision
    with pytest.raises(ValueError):
        evaluate_add_decision("E", amount, context=context(inputs))


def test_policy_save_is_versioned_and_rejects_stale_updates():
    from db.decision_risk import get_risk_policy, save_risk_policy
    p = get_risk_policy()
    assert p["confirmed"] is False
    saved = save_risk_policy({"max_single_position_pct": 20, "max_equity_pct": 50,
                              "max_stress_loss_amount": 10000}, expected_version=p["version"])
    assert saved["confirmed"] is True
    assert saved["version"] == p["version"] + 1
    with pytest.raises(ValueError):
        save_risk_policy({"max_equity_pct": 80}, expected_version=p["version"])


def test_reading_decision_context_does_not_create_account_or_saved_plan():
    from db._conn import _get_conn
    from services.portfolio.decision_guardrails import build_decision_snapshot
    conn = _get_conn()
    before = conn.total_changes
    assert conn.execute("SELECT COUNT(*) FROM portfolio_cash").fetchone()[0] == 0
    conn.close()
    build_decision_snapshot()
    conn = _get_conn()
    assert conn.execute("SELECT COUNT(*) FROM portfolio_cash").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM portfolio_risk_policy").fetchone()[0] == 0
    conn.close()


def test_reserved_other_purchase_counts_towards_shared_index_and_cash(inputs):
    from services.portfolio.decision_guardrails import evaluate_add_decision
    inputs['snapshot']['funds'].append({**inputs['snapshot']['funds'][0], 'fund_code':'E2','current_value':1000})
    inputs['policy']['max_single_position_pct']=100
    inputs['policy']['max_same_index_pct']=20
    ctx=context(inputs)
    first=evaluate_add_decision('E',6000,context=ctx)
    assert first['executable_amount']==6000
    ctx['proposed_additions']={'E':6000}
    second=evaluate_add_decision('E2',6000,context=ctx)
    assert second['executable_amount']==4200
    assert second['after']['same_index_pct']<=20
    assert second['after']['available_cash']==45800


def test_other_fund_danger_does_not_block_unrelated_bond(inputs):
    from services.portfolio.decision_guardrails import evaluate_add_decision
    ctx=context(inputs)
    ctx['active_risks']=[{'severity':'danger','related_fund_code':'E','alert_type':'daily_drop','risk_status':'in_progress'}]
    assert evaluate_add_decision('E',1000,context=ctx)['status']=='blocked'
    assert evaluate_add_decision('B',1000,context=ctx)['status']=='allowed'


def test_fee_cannot_breach_cash_or_exposure_after_purchase(inputs):
    from services.portfolio.decision_guardrails import evaluate_add_decision
    inputs['policy']['estimated_fee_rate']=.01
    ctx=context(inputs)
    result=evaluate_add_decision('E',30000,context=ctx)
    assert result['after']['single_position_pct']<=25
    assert result['after']['total_assets']<ctx['total_assets']
    assert result['after']['available_cash']>=0


def test_equivalent_index_suffixes_count_as_one_exposure(inputs):
    from services.portfolio.decision_guardrails import evaluate_add_decision
    inputs['snapshot']['funds'][0]['index_code']='IDX.SH'
    inputs['snapshot']['funds'].append({**inputs['snapshot']['funds'][0],'fund_code':'E2','index_code':'IDX','current_value':30000})
    inputs['policy'].update(max_same_index_pct=30,max_single_position_pct=100)
    result=evaluate_add_decision('E',10000,context=context(inputs))
    assert result['executable_amount']==500


def test_existing_other_fund_limit_breach_blocks_further_risk(inputs):
    from services.portfolio.decision_guardrails import evaluate_add_decision
    inputs['policy']['max_single_position_pct']=15
    result=evaluate_add_decision('E',1000,context=context(inputs))
    assert result['status']=='blocked'
    assert result['executable_amount']==0
    assert any('单基金' in r for r in result['reasons'])


def test_expired_context_cannot_be_reused_even_if_its_facts_look_fresh(inputs):
    from datetime import timedelta
    from services.portfolio.decision_guardrails import evaluate_add_decision
    result=evaluate_add_decision('E',1000,context=context(inputs),now=NOW+timedelta(hours=2))
    assert result['status']=='wait_for_data'
    assert result['executable_amount']==0
    assert any('过期' in r for r in result['reasons'])
