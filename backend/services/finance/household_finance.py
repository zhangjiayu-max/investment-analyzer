"""家庭财务诊断事实：共享现金流，不从保费或月供推断保额、负债本金。"""

from db.expense import get_cashflow


def build_household_diagnosis(user_id: str = "default", cashflow: dict | None = None) -> dict:
    cf = cashflow if cashflow is not None else get_cashflow(user_id)
    income = cf["monthly_income"]
    debt_ratio = round(cf["debt_payment"] / income, 4) if income > 0 else None
    warnings = []
    if not cf["data_available"]:
        warnings.append("请先填写月收入和月支出预算，或明确确认本月支出已记录完整")
    if cf["monthly_surplus"] <= 0:
        warnings.append("预计月结余不足，优先保障必要支出")
    if cf["emergency_gap"] > 0:
        warnings.append(f"应急储备缺口 ¥{cf['emergency_gap']:,.2f}，优先补足备用金")
    if debt_ratio is not None and debt_ratio >= .4:
        warnings.append(f"本月还贷占收入 {debt_ratio:.1%}，需关注还款压力")
    if cf["overdue_bills"]:
        warnings.append(f"本月有 {cf['overdue_bills']} 笔账单待确认支付且已过扣款日")

    actions = []
    missing = list(cf.get("data_gaps") or [])
    if missing:
        actions.append({
            "action_id": "household:data-gap",
            "category": "data_gap",
            "priority": 100,
            "title": "补充家庭收入和预算",
            "reason": "缺少月收入或全月支出预算，无法可靠计算结余率和可投资结余。",
            "evidence": {"missing": missing},
            "cta": "补充收支资料",
            "navigate_to": "family-finance",
        })
    pending = float(cf.get("pending_bills") or 0)
    if pending > 0:
        actions.append({
            "action_id": "household:pending-bills",
            "category": "fixed_bills",
            "priority": 90,
            "title": "确认本月固定账单",
            "reason": f"仍有 ¥{pending:,.2f} 固定账单待支付。",
            "evidence": {"pending_bills": round(pending, 2), "bills": cf.get("recurring_bills", [])},
            "cta": "查看固定账单",
            "navigate_to": "family-finance",
        })
    shortfalls = [item for item in (cf.get("forecast_3m") or []) if item.get("status") == "shortfall"]
    if shortfalls:
        first = shortfalls[0]
        actions.append({
            "action_id": "household:cash-safety",
            "category": "cash_safety",
            "priority": 85,
            "title": "未来现金安全线不足",
            "reason": f"预计 {first['month']} 支付必要支出后现金缺口 ¥{abs(first['cash_balance']):,.2f}。",
            "evidence": {"forecast_3m": cf.get("forecast_3m", [])},
            "cta": "查看现金预测",
            "navigate_to": "family-finance",
        })
    if not cf.get("expense_complete") and "monthly_expense_or_month_complete" not in missing:
        actions.append({
            "action_id": "household:complete-expense",
            "category": "expense_tracking",
            "priority": 70,
            "title": "确认本月支出是否已记全",
            "reason": "当前预测仍可能使用月度预算兜底，实际结余会随补记支出变化。",
            "evidence": {"recorded_expense": cf.get("recorded_expense", 0), "budget_expense": cf.get("budget_expense", 0)},
            "cta": "补记本月支出",
            "navigate_to": "family-finance",
        })
    if cf.get("emergency_gap", 0) > 0:
        actions.append({
            "action_id": "household:emergency-gap",
            "category": "emergency_fund",
            "priority": 65,
            "title": "补足家庭备用金",
            "reason": f"备用金还差 ¥{cf['emergency_gap']:,.2f} 才达到目标。",
            "evidence": {"emergency_gap": cf.get("emergency_gap"), "emergency_months": cf.get("emergency_months")},
            "cta": "查看资金桶",
            "navigate_to": "goal-buckets",
        })
    if len(actions) < 5:
        actions.append({
            "action_id": "household:liabilities",
            "category": "liabilities_gap",
            "priority": 50,
            "title": "补充负债余额",
            "reason": "目前只有月供记录，无法判断完整家庭净资产和负债率。",
            "evidence": {"liabilities_status": "unknown", "monthly_debt_payment": cf.get("debt_payment", 0)},
            "cta": "补充负债资料",
            "navigate_to": "family-finance",
        })
    actions.sort(key=lambda item: (-item["priority"], item["action_id"]))
    return {
        "status": "insufficient" if not cf["data_available"] else "attention" if warnings else "adequate",
        "debt_to_income": debt_ratio,
        "monthly_debt_payment": cf["debt_payment"],
        "emergency_months": cf["emergency_months"],
        "emergency_gap": cf["emergency_gap"],
        "investable_surplus": cf["investable_surplus"],
        "expense_complete": cf["expense_complete"],
        "liabilities_status": "unknown",
        "insurance_status": "unknown",
        "scope_note": "本诊断使用已登记收支；保费不代表保额，月供不代表贷款余额。投资健康分单独评估基金持仓。",
        "warnings": warnings,
        "actions": actions[:5],
    }
