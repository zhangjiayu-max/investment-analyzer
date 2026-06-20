"""家庭财务统一仪表盘路由 — /api/finance-dashboard

聚合用户画像、目标桶、持仓、配置偏离、压力测试，提供单一 API 返回完整家庭财务视图。
"""

from fastapi import APIRouter

from db import (
    get_cash_balance,
    get_portfolio_summary,
    get_user_profile,
    list_goal_buckets,
    get_goal_bucket_summary,
)
from allocation_dashboard import build_allocation_dashboard
from stress_test import run_portfolio_stress_test

router = APIRouter(tags=["finance-dashboard"])


@router.get("/api/finance-dashboard")
async def get_finance_dashboard(user_id: str = "default"):
    """返回家庭财务统一仪表盘数据。

    六大模块：
    1. 净值总览：总资产、现金、持仓市值、总成本、浮盈亏、收益率
    2. 现金流：月收入、月支出、月结余、结余率、备用金覆盖月数
    3. 负债：负债摘要、月供压力、负债收入比
    4. 目标进度：资金桶列表+进度
    5. 投资状态：当前 vs 目标配置缩略、最大偏离
    6. 风险视图：压力测试摘要、备用金缓冲
    """
    # ── 1. 净值总览 ──
    summary = get_portfolio_summary(user_id=user_id)
    cash = get_cash_balance(user_id)
    cash_balance = cash.get("balance", 0) if cash else 0
    total_assets = summary.get("total_assets", 0)
    total_cost = summary.get("total_cost", 0)
    holding_value = summary.get("total_market_value", 0)
    float_pnl = summary.get("total_float_pnl", 0)
    total_return = summary.get("total_return_pct", 0)

    net_worth = {
        "total_assets": round(total_assets, 2),
        "cash_balance": round(cash_balance, 2),
        "holding_value": round(holding_value, 2),
        "total_cost": round(total_cost, 2),
        "float_pnl": round(float_pnl, 2),
        "total_return_pct": round(total_return, 4),
    }

    # ── 2. 现金流 ──
    profile = get_user_profile(user_id) or {}
    monthly_income = profile.get("monthly_income") or 0
    monthly_expense = profile.get("monthly_expense") or 0
    monthly_surplus = profile.get("monthly_surplus") or (monthly_income - monthly_expense)
    surplus_rate = round(monthly_surplus / monthly_income, 4) if monthly_income > 0 else 0
    emergency_fund_months = profile.get("emergency_fund_months") or 0

    cash_flow = {
        "monthly_income": round(monthly_income, 2),
        "monthly_expense": round(monthly_expense, 2),
        "monthly_surplus": round(monthly_surplus, 2),
        "surplus_rate": surplus_rate,
        "emergency_fund_months": round(emergency_fund_months, 1),
    }

    # ── 3. 负债 ──
    debt_summary = profile.get("debt_summary") or ""
    monthly_debt_payment = profile.get("monthly_debt_payment") or 0
    debt_to_income = round(monthly_debt_payment / monthly_income, 4) if monthly_income > 0 else 0

    debt = {
        "debt_summary": debt_summary,
        "monthly_debt_payment": round(monthly_debt_payment, 2),
        "debt_to_income": debt_to_income,
    }

    # ── 4. 目标进度 ──
    buckets = list_goal_buckets(user_id=user_id, status="active")
    bucket_summary = get_goal_bucket_summary(user_id)
    goals = {
        "buckets": [
            {
                "id": b["id"],
                "name": b["name"],
                "target_amount": b.get("target_amount") or 0,
                "current_amount": b.get("current_amount") or 0,
                "progress_pct": round(
                    (b.get("current_amount", 0) / b["target_amount"] * 100), 1
                ) if b.get("target_amount") else 0,
                "priority": b.get("priority") or "medium",
                "bucket_type": b.get("bucket_type") or "general",
            }
            for b in buckets
        ],
        "total_target": sum(b.get("target_amount") or 0 for b in buckets),
        "total_current": sum(b.get("current_amount") or 0 for b in buckets),
        "emergency_bucket": bucket_summary.get("emergency_bucket"),
    }

    # ── 5. 投资状态（配置偏离缩略） ──
    try:
        alloc = build_allocation_dashboard(user_id)
        allocation = {
            "max_drift": alloc.get("max_drift", 0),
            "top_drift": alloc.get("top_drift"),
            "market_level": alloc.get("market_level", ""),
            "guardrails_count": len(alloc.get("guardrails", [])),
        }
    except Exception:
        allocation = {"max_drift": 0, "top_drift": None, "market_level": "", "guardrails_count": 0}

    # ── 6. 风险视图（取 market_drop_20 作为默认压力测试） ──
    try:
        stress = run_portfolio_stress_test("market_drop_20", user_id)
        risk = {
            "stress_loss_amount": stress.get("loss_amount", 0),
            "stress_loss_ratio": stress.get("loss_ratio", 0),
            "risk_level": stress.get("risk_level", "low"),
            "emergency_bucket": stress.get("emergency_bucket"),
            "warnings": stress.get("warnings", []),
        }
    except Exception:
        risk = {
            "stress_loss_amount": 0,
            "stress_loss_ratio": 0,
            "risk_level": "low",
            "emergency_bucket": None,
            "warnings": ["暂无持仓数据，无法计算压力测试"],
        }

    # ── 健康状态提示 ──
    health_warnings = []
    emergency_bucket = bucket_summary.get("emergency_bucket")
    if emergency_bucket and (emergency_bucket.get("progress_pct") or 0) < 100:
        health_warnings.append(
            f"备用金不足：{emergency_bucket.get('name', '备用金')} "
            f"当前进度 {emergency_bucket.get('progress_pct', 0):g}%"
        )
    elif not emergency_bucket:
        health_warnings.append("未设置备用金桶，建议优先建立应急储备")
    if monthly_surplus <= 0:
        health_warnings.append("月结余为负或为零，不建议新增风险资产投资")
    if allocation.get("max_drift", 0) > 0.1:
        health_warnings.append(
            f"组合最大偏离 {allocation['max_drift']:.1%}，建议考虑再平衡"
        )
    if risk.get("stress_loss_ratio", 0) >= 0.15:
        health_warnings.append(
            f"压力测试下组合回撤约 {risk['stress_loss_ratio']:.1%}，"
            "请确认风险承受度"
        )

    return {
        "net_worth": net_worth,
        "cash_flow": cash_flow,
        "debt": debt,
        "goals": goals,
        "allocation": allocation,
        "risk": risk,
        "health_warnings": health_warnings,
    }
