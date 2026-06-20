"""组合目标配置 / 偏离度驾驶舱。"""

from __future__ import annotations


CATEGORY_LABELS = {
    "cash": "现金",
    "equity": "股票型",
    "bond": "债券型",
    "money": "货币型",
    "money_market": "货币型",
    "hybrid": "混合型",
    "index": "指数型",
    "qdii": "QDII",
    "bond_index": "债券指数",
    "convertible_bond": "可转债",
}


def _amount_from_ratio(total_assets: float, ratio: float) -> float:
    return round((total_assets or 0) * (ratio or 0), 2)


def _drift_level(drift_abs: float) -> str:
    if drift_abs >= 0.10:
        return "significant"
    if drift_abs >= 0.05:
        return "slight"
    return "balanced"


def _build_goal_constraints() -> dict:
    try:
        from db.goal_buckets import get_goal_bucket_summary
        return get_goal_bucket_summary()
    except Exception:
        return {"count": 0, "emergency_bucket": None}


def _build_guardrails(goal_constraints: dict) -> list[str]:
    guardrails = []
    emergency = goal_constraints.get("emergency_bucket")
    if not emergency:
        guardrails.append("尚未设置备用金桶，调仓前建议先确认 3-6 个月生活费来源")
    elif (emergency.get("progress_pct") or 0) < 100:
        guardrails.append(
            f"备用金桶未达标：{emergency.get('name')} 当前进度 {emergency.get('progress_pct', 0):g}%，"
            "新增风险资产前应优先补足"
        )
    return guardrails


def _build_allocation_rows(raw: dict) -> list[dict]:
    total_assets = raw.get("total_assets") or 0
    current = raw.get("current_allocation") or {}
    target = raw.get("target_allocation") or {}
    drift = raw.get("drift") or {}
    categories = sorted(
        set(current) | set(target) | set(drift),
        key=lambda cat: abs(drift.get(cat, 0)),
        reverse=True,
    )
    rows = []
    for cat in categories:
        current_ratio = current.get(cat, 0)
        target_ratio = target.get(cat, 0)
        drift_value = drift.get(cat, current_ratio - target_ratio)
        rows.append({
            "category": cat,
            "label": CATEGORY_LABELS.get(cat, cat),
            "current_ratio": round(current_ratio, 4),
            "target_ratio": round(target_ratio, 4),
            "drift": round(drift_value, 4),
            "drift_abs": round(abs(drift_value), 4),
            "current_amount": _amount_from_ratio(total_assets, current_ratio),
            "target_amount": _amount_from_ratio(total_assets, target_ratio),
            "drift_amount": _amount_from_ratio(total_assets, drift_value),
            "level": _drift_level(abs(drift_value)),
        })
    return rows


def _prioritize_suggestions(suggestions: list[dict], guardrails: list[str]) -> list[dict]:
    result = []
    for idx, item in enumerate(suggestions or [], start=1):
        suggestion = dict(item)
        suggestion["priority"] = idx
        suggestion["guardrail_note"] = guardrails[0] if guardrails and suggestion.get("action") in {
            "buy",
            "buy_index",
            "deploy_cash",
        } else ""
        result.append(suggestion)
    return result


def build_allocation_dashboard(user_id: str = "default") -> dict:
    """构建组合偏离驾驶舱数据。"""
    from rebalancer import analyze_rebalancing_need

    raw = analyze_rebalancing_need(user_id=user_id)
    if raw.get("error"):
        return {"status": "empty", "error": raw["error"], "allocation_rows": [], "suggestions": []}

    goal_constraints = _build_goal_constraints()
    guardrails = _build_guardrails(goal_constraints)
    rows = _build_allocation_rows(raw)
    suggestions = _prioritize_suggestions(raw.get("suggestions") or [], guardrails)
    top_drift = rows[0] if rows else None

    return {
        "status": "ok",
        "total_assets": raw.get("total_assets", 0),
        "cash_balance": raw.get("cash_balance", 0),
        "market_level": raw.get("market_level", ""),
        "market_avg_percentile": raw.get("market_avg_percentile"),
        "cash_target": raw.get("cash_target"),
        "drift_level": raw.get("drift_level", "balanced"),
        "max_drift": raw.get("max_drift", 0),
        "top_drift": top_drift,
        "allocation_rows": rows,
        "suggestions": suggestions,
        "goal_constraints": goal_constraints,
        "guardrails": guardrails,
        "raw": raw,
    }
