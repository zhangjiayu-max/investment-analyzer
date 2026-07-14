"""专属理财顾问 — 主动关怀预警

借鉴私人银行客户经理的主动关怀职责：扫描用户持仓与关注品种，
当出现风险信号（回撤超亏损承受度、估值进入极端区间）时生成主动关怀预警。

预警由 wealth_advisor 编排专家或 /api/profile/alerts 接口触发，供前端展示。
"""

import logging

logger = logging.getLogger(__name__)


# 亏损承受度 → 触发阈值（%）
_LOSS_TOLERANCE_PCT = {"low": 5, "medium": 15, "high": 30}
# 估值百分位极端区间
_LOW_PCT = 0.2
_HIGH_PCT = 0.8


def generate_proactive_alerts(user_id: str = "default") -> list[dict]:
    """生成主动关怀预警。

    返回: [{"type", "level", "title", "detail", "suggestion"}]
    level: "warning"（需关注）/ "info"（提示）
    """
    alerts = []

    # 1. 持仓回撤 vs 亏损承受度（核心预警）
    alerts.extend(_check_drawdown_alerts(user_id))

    # 2. 持仓关联指数估值极端区间（可扩展）
    alerts.extend(_check_valuation_alerts(user_id))

    return alerts


def _check_drawdown_alerts(user_id: str) -> list[dict]:
    """检查持仓浮亏是否超过用户亏损承受度。"""
    alerts = []
    try:
        from agent.kyc.kyc import get_kyc_profile
        from db import list_holdings

        profile = get_kyc_profile(user_id)
        loss_tol = profile.get("loss_tolerance", "")
        loss_pct = _LOSS_TOLERANCE_PCT.get(loss_tol, 15)  # 默认 15%

        holdings = list_holdings() or []
        for h in holdings:
            cost = h.get("cost_price") or h.get("total_cost", 0)
            cur = h.get("current_price") or h.get("current_value", 0)
            shares = h.get("shares", 0) or 1
            # 用单价或总值计算浮亏
            if cost > 0 and cur > 0:
                # 如果 cost/current 是总值就直接比，是单价也一致
                drawdown = (cur - cost) / cost * 100 if cost else 0
                if drawdown < -loss_pct:
                    name = h.get("fund_name") or h.get("index_name") or "持仓"
                    level = "warning" if drawdown < -loss_pct * 1.5 else "info"
                    alerts.append({
                        "type": "drawdown",
                        "level": level,
                        "title": f"{name} 浮亏 {abs(drawdown):.1f}%",
                        "detail": f"已超过您的亏损承受度（约 {loss_pct}%）。成本 {cost:.2f}，现价 {cur:.2f}",
                        "suggestion": "建议评估基本面是否变化，考虑止损或分批调仓，避免情绪化决策",
                    })
    except Exception as e:
        logger.warning(f"持仓回撤预警失败: {e}")
    return alerts


def _check_valuation_alerts(user_id: str) -> list[dict]:
    """检查持仓关联指数的估值是否进入极端区间（低估/高估）。"""
    alerts = []
    try:
        from db import list_holdings, get_latest_valuation

        holdings = list_holdings() or []
        for h in holdings:
            idx_code = h.get("index_code")
            if not idx_code:
                continue
            val = get_latest_valuation(idx_code)
            if not val:
                continue
            pe_pct = val.get("pe_percentile")
            if pe_pct is None:
                continue
            name = h.get("index_name") or h.get("fund_name") or idx_code
            if pe_pct < _LOW_PCT:
                alerts.append({
                    "type": "valuation_low",
                    "level": "info",
                    "title": f"{name} 估值处于低估区（PE 百分位 {pe_pct:.0%}）",
                    "detail": "历史分位较低，可能是定投/加仓的窗口期",
                    "suggestion": "可考虑分批建仓或定投，但需结合基本面判断",
                })
            elif pe_pct > _HIGH_PCT:
                alerts.append({
                    "type": "valuation_high",
                    "level": "warning",
                    "title": f"{name} 估值处于高估区（PE 百分位 {pe_pct:.0%}）",
                    "detail": "历史分位较高，回调风险增加",
                    "suggestion": "可考虑止盈或减仓，保留部分仓位",
                })
    except Exception as e:
        logger.warning(f"估值预警失败: {e}")
    return alerts


def format_alerts_for_chat(alerts: list[dict]) -> str:
    """把预警格式化为对话可用的开场白文本（供 wealth_advisor 使用）。"""
    if not alerts:
        return ""
    lines = ["📊 主动关怀提醒："]
    for a in alerts:
        icon = "⚠️" if a.get("level") == "warning" else "💡"
        lines.append(f"{icon} {a['title']}")
        if a.get("detail"):
            lines.append(f"   {a['detail']}")
        if a.get("suggestion"):
            lines.append(f"   建议：{a['suggestion']}")
    return "\n".join(lines)
