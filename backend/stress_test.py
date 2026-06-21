"""组合压力测试：确定性情景冲击估算。"""

from __future__ import annotations


SCENARIOS = {
    "market_drop_20": {
        "label": "市场下跌 20%",
        "shocks": {
            "cash": 0.0,
            "money": -0.002,
            "money_market": -0.002,
            "bond": -0.03,
            "bond_index": -0.04,
            "convertible_bond": -0.12,
            "hybrid": -0.12,
            "equity": -0.20,
            "index": -0.20,
            "qdii": -0.18,
        },
    },
    "rate_up": {
        "label": "利率上行",
        "shocks": {
            "cash": 0.0,
            "money": 0.0,
            "money_market": 0.0,
            "bond": -0.06,
            "bond_index": -0.08,
            "convertible_bond": -0.08,
            "hybrid": -0.06,
            "equity": -0.05,
            "index": -0.05,
            "qdii": -0.04,
        },
    },
    "liquidity_crunch": {
        "label": "流动性冲击",
        "shocks": {
            "cash": 0.0,
            "money": -0.005,
            "money_market": -0.005,
            "bond": -0.05,
            "bond_index": -0.06,
            "convertible_bond": -0.15,
            "hybrid": -0.15,
            "equity": -0.25,
            "index": -0.25,
            "qdii": -0.20,
        },
    },
}


def _risk_level(loss_ratio: float) -> str:
    if loss_ratio >= 0.15:
        return "high"
    if loss_ratio >= 0.08:
        return "medium"
    return "low"


def run_portfolio_stress_test(scenario: str = "market_drop_20", user_id: str = "default",
                              custom_shocks: dict = None) -> dict:
    """按资产类别冲击估算组合压力测试结果。

    Args:
        scenario: 场景名称，或 "custom" 使用自定义冲击
        user_id: 用户 ID
        custom_shocks: 自定义冲击系数 {"cash": 0, "equity": -0.3, ...}
    """
    from db import get_cash_balance, list_holdings
    from db.goal_buckets import get_goal_bucket_summary

    if scenario == "custom" and custom_shocks:
        cfg = {"label": "自定义场景", "shocks": custom_shocks}
    else:
        cfg = SCENARIOS.get(scenario)
    if not cfg:
        raise ValueError(f"不支持的压力测试场景: {scenario}")

    holdings = [h for h in list_holdings(user_id) if (h.get("shares") or 0) > 0]
    cash_info = get_cash_balance(user_id)
    cash_balance = cash_info.get("balance", 0) if cash_info else 0
    total_holding_value = sum(h.get("current_value") or 0 for h in holdings)
    total_assets = round(total_holding_value + cash_balance, 2)
    if total_assets <= 0:
        return {"status": "empty", "scenario": scenario, "asset_impacts": [], "warnings": ["暂无持仓或现金数据"]}

    grouped: dict[str, float] = {"cash": cash_balance}
    for h in holdings:
        category = h.get("fund_category") or "equity"
        grouped[category] = grouped.get(category, 0) + (h.get("current_value") or 0)

    impacts = []
    projected_total = 0.0
    shocks = cfg["shocks"]
    for category, amount in sorted(grouped.items(), key=lambda item: item[1], reverse=True):
        shock = shocks.get(category, shocks.get("equity", -0.2))
        projected = round(amount * (1 + shock), 2)
        loss = round(amount - projected, 2)
        projected_total += projected
        impacts.append({
            "category": category,
            "current_amount": round(amount, 2),
            "shock_pct": round(shock, 4),
            "projected_amount": projected,
            "loss_amount": loss,
        })

    projected_total = round(projected_total, 2)
    loss_amount = round(total_assets - projected_total, 2)
    loss_ratio = round(loss_amount / total_assets, 4) if total_assets else 0

    goal_summary = get_goal_bucket_summary(user_id)
    emergency = goal_summary.get("emergency_bucket")
    warnings = []
    if emergency and (emergency.get("progress_pct") or 0) < 100:
        warnings.append(f"备用金不足：{emergency.get('name')} 当前进度 {emergency.get('progress_pct', 0):g}%")
    elif not emergency:
        warnings.append("未设置备用金桶，无法判断压力情景下的现金缓冲")
    if loss_ratio >= 0.08:
        warnings.append(f"压力情景下组合回撤约 {loss_ratio:.1%}，执行新增风险资产前需确认承受度")

    return {
        "status": "ok",
        "scenario": scenario,
        "label": cfg["label"],
        "total_assets": total_assets,
        "projected_total_assets": projected_total,
        "loss_amount": loss_amount,
        "loss_ratio": loss_ratio,
        "risk_level": _risk_level(loss_ratio),
        "asset_impacts": impacts,
        "emergency_bucket": emergency,
        "warnings": warnings,
    }
