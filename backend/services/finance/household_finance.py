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
    }
