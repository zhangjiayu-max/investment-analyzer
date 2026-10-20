"""组合压力测试：确定性情景冲击估算。"""

from __future__ import annotations

import math


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

# 子类型与历史分类共用映射；未知类别保留保守假设并向用户披露。
RISK_CATEGORY_MAP = {
    "bond_pure": "bond", "bond_short": "bond", "bond_long": "bond",
    "bond_hybrid": "hybrid", "bond_convertible": "convertible_bond",
    "money_market": "money", "stock": "equity", "stock_index": "index",
}


def category_risk_assumption(category: str) -> dict:
    """返回资产类别的情景映射及是否使用未知类别假设。"""
    mapped = RISK_CATEGORY_MAP.get(category, category)
    known = mapped in SCENARIOS["market_drop_20"]["shocks"]
    return {"category": category, "risk_category": mapped if known else "equity", "assumed": not known}


def scenario_shock(category: str, scenario: str = "market_drop_20", custom_shocks: dict | None = None) -> float:
    """情景冲击为比例，禁止非有限值或损失超过本金。"""
    if scenario == "custom":
        if not custom_shocks:
            raise ValueError("自定义场景需要冲击系数")
        shocks = custom_shocks
    elif scenario in SCENARIOS:
        shocks = SCENARIOS[scenario]["shocks"]
    else:
        raise ValueError(f"不支持的压力测试场景: {scenario}")
    for value in shocks.values():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not -1 <= value <= 1:
            raise ValueError("冲击系数必须是-1至1之间的有限比例")
    mapping = category_risk_assumption(category)
    return float(shocks.get(category, shocks.get(mapping["risk_category"], shocks.get("equity", -.2))))


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
    from db.portfolio import get_portfolio_snapshot
    from db.goal_buckets import get_goal_bucket_summary

    if scenario == "custom" and custom_shocks:
        cfg = {"label": "自定义场景", "shocks": custom_shocks}
    else:
        cfg = SCENARIOS.get(scenario)
    if not cfg:
        raise ValueError(f"不支持的压力测试场景: {scenario}")

    snapshot = get_portfolio_snapshot(user_id)
    holdings = snapshot["holdings"]
    cash_balance = snapshot["cash_balance"] + snapshot.get("receivable_cash", 0)
    missing = [h.get("fund_code") for h in holdings if h.get("current_value") is None]
    if missing:
        return {"status": "insufficient_data", "scenario": scenario, "asset_impacts": [],
                "loss_amount": None, "loss_ratio": None, "risk_level": "unknown",
                "warnings": ["部分持仓缺少有效净值，无法估算完整压力损失"], "missing_fund_codes": missing}
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
        assumption = category_risk_assumption(category)
        shock = scenario_shock(category, scenario, custom_shocks)
        projected = round(amount * (1 + shock), 2)
        loss = round(amount - projected, 2)
        projected_total += projected
        impacts.append({
            "category": category,
            "current_amount": round(amount, 2),
            "shock_pct": round(shock, 4),
            "projected_amount": projected,
            "loss_amount": loss,
            "risk_category": assumption["risk_category"],
            "assumed": assumption["assumed"],
        })

    projected_total = round(projected_total, 2)
    loss_amount = round(total_assets - projected_total, 2)
    loss_ratio = round(loss_amount / total_assets, 4) if total_assets else 0

    goal_summary = get_goal_bucket_summary(user_id)
    emergency = goal_summary.get("emergency_bucket")
    warnings = []
    assumed_categories = [item["category"] for item in impacts if item["assumed"]]
    if assumed_categories:
        warnings.append("未知资产类别按权益冲击估算：" + "、".join(assumed_categories))
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
        "calculation_basis": "deterministic_scenario_not_forecast",
        "snapshot_as_of": snapshot.get("as_of"),
    }
