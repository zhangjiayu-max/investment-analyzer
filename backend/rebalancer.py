"""智能调仓分析 — 结合持仓、估值、市场分析，给出稳健调仓建议。"""

import logging

logger = logging.getLogger(__name__)

# 资产类型基础配比（默认均衡型）
DEFAULT_BASE_ALLOCATION = {
    "equity": 0.40,   # 股票型
    "bond": 0.30,     # 债券型
    "money": 0.05,    # 货币型
    "hybrid": 0.10,   # 混合型
    "index": 0.10,    # 指数型
    "qdii": 0.05,     # QDII
}

# 估值调整系数
VALUATION_ADJUSTMENT = {
    "极度低估": 1.4,   # 低估 → 多配
    "低估": 1.2,
    "合理": 1.0,
    "偏高": 0.8,
    "极度高估": 0.6,   # 高估 → 少配
}


def _get_valuation_level(percentile: float) -> str:
    """根据百分位返回估值水平标签。"""
    if percentile <= 20:
        return "极度低估"
    elif percentile <= 30:
        return "低估"
    elif percentile <= 70:
        return "合理"
    elif percentile <= 80:
        return "偏高"
    else:
        return "极度高估"


def _get_market_overall_level(valuations: list) -> tuple[str, float]:
    """计算市场整体估值水平和平均百分位。

    返回: (水平标签, 平均百分位)
    """
    if not valuations:
        return "合理", 50.0

    percentiles = []
    for v in valuations:
        p = v.get("percentile")
        if p is not None:
            percentiles.append(p)

    if not percentiles:
        return "合理", 50.0

    avg_pct = sum(percentiles) / len(percentiles)
    return _get_valuation_level(avg_pct), avg_pct


def analyze_rebalancing_need(user_id: str = "default") -> dict:
    """分析是否需要调仓，返回偏离度和建议。

    返回结构:
    {
        "total_assets": float,
        "cash_balance": float,
        "current_allocation": {category: ratio},
        "target_allocation": {category: ratio},
        "drift": {category: delta},
        "max_drift": float,
        "drift_level": "balanced" | "slight" | "significant",
        "suggestions": [
            {"action": "buy"|"sell"|"hold_cash", "category": str, "reason": str,
             "fund_code": str, "fund_name": str, "amount_range": str}
        ],
        "valuation_basis": {...},
        "market_level": str,
    }
    """
    try:
        from db import list_holdings, get_cash_balance, list_valuation_indexes

        # 1. 获取持仓数据
        holdings = list_holdings(user_id)
        active = [h for h in holdings if (h.get("shares") or 0) > 0]

        cash_info = get_cash_balance(user_id)
        cash_balance = cash_info.get("balance", 0) if cash_info else 0

        total_value = sum(h.get("current_value", 0) or 0 for h in active)
        total_assets = total_value + cash_balance

        if total_assets <= 0:
            return {"error": "无持仓数据"}

        # 2. 获取估值数据
        valuations = list_valuation_indexes()
        # 按指数聚合，取 PE 百分位
        index_valuation = {}
        for v in valuations:
            code = v.get("index_code", "")
            metric = v.get("metric_type", "")
            percentile = v.get("percentile")
            if code and percentile is not None:
                if code not in index_valuation:
                    index_valuation[code] = {
                        "name": v.get("index_name", code),
                        "percentile": percentile,
                        "metric": metric,
                    }
                elif metric == "市盈率":  # PE 优先
                    index_valuation[code]["percentile"] = percentile
                    index_valuation[code]["metric"] = metric

        # 3. 计算当前资产分布
        category_value = {}
        for h in active:
            cat = h.get("fund_category") or "equity"
            category_value.setdefault(cat, 0)
            category_value[cat] += h.get("current_value", 0) or 0

        # 加入现金
        category_value["cash"] = cash_balance

        current_allocation = {}
        for cat, val in category_value.items():
            current_allocation[cat] = val / total_assets if total_assets > 0 else 0

        # 4. 计算目标配比（根据市场整体估值调整）
        market_level, avg_pct = _get_market_overall_level(
            [v for v in index_valuation.values()]
        )
        adjustment = VALUATION_ADJUSTMENT.get(market_level, 1.0)

        # 现金目标：根据市场估值调整
        if avg_pct <= 30:
            cash_target = 0.05  # 低估时少持现金
        elif avg_pct <= 70:
            cash_target = 0.10  # 合理时适度持现
        else:
            cash_target = 0.15  # 高估时多持现金

        # 计算各类资产目标配比
        target_allocation = {}
        remaining = 1.0 - cash_target
        for cat, base_ratio in DEFAULT_BASE_ALLOCATION.items():
            # 根据市场估值微调（股票/指数类更敏感）
            if cat in ("equity", "index", "hybrid"):
                adjusted = base_ratio * adjustment
            else:
                adjusted = base_ratio
            target_allocation[cat] = adjusted

        # 归一化（排除现金后）
        total_adjusted = sum(target_allocation.values())
        if total_adjusted > 0:
            for cat in target_allocation:
                target_allocation[cat] = (target_allocation[cat] / total_adjusted) * remaining

        target_allocation["cash"] = cash_target

        # 5. 计算偏离度
        drift = {}
        all_cats = set(list(current_allocation.keys()) + list(target_allocation.keys()))
        for cat in all_cats:
            current = current_allocation.get(cat, 0)
            target = target_allocation.get(cat, 0)
            drift[cat] = current - target

        max_drift = max(abs(d) for d in drift.values()) if drift else 0

        if max_drift < 0.03:
            drift_level = "balanced"
        elif max_drift < 0.08:
            drift_level = "slight"
        else:
            drift_level = "significant"

        # 6. 生成调仓建议
        suggestions = []
        cash_ratio = cash_balance / total_assets if total_assets > 0 else 0

        # 现金建议
        if cash_ratio > cash_target + 0.05:
            excess = cash_balance - cash_target * total_assets
            suggestions.append({
                "action": "deploy_cash",
                "category": "cash",
                "reason": f"现金占比{cash_ratio:.0%}超过目标{cash_target:.0%}，建议配置",
                "amount_range": f"¥{excess * 0.5:,.0f} - ¥{excess:,.0f}",
            })
        elif cash_ratio < cash_target - 0.02:
            shortage = cash_target * total_assets - cash_balance
            suggestions.append({
                "action": "reserve_cash",
                "category": "cash",
                "reason": f"现金占比{cash_ratio:.0%}低于目标{cash_target:.0%}，建议保留流动性",
                "amount_range": f"¥{shortage * 0.5:,.0f} - ¥{shortage:,.0f}",
            })

        # 各类资产偏离建议
        category_labels = {
            "equity": "股票型", "bond": "债券型", "money": "货币型",
            "hybrid": "混合型", "index": "指数型", "qdii": "QDII",
        }
        for cat, delta in sorted(drift.items(), key=lambda x: abs(x[1]), reverse=True):
            if cat == "cash" or abs(delta) < 0.05:
                continue
            label = category_labels.get(cat, cat)
            if delta > 0:
                # 超配 → 建议减仓
                excess_val = delta * total_assets
                # 找该类别下最大的持仓
                cat_holdings = [h for h in active if (h.get("fund_category") or "equity") == cat]
                if cat_holdings:
                    top = max(cat_holdings, key=lambda h: h.get("current_value", 0) or 0)
                    suggestions.append({
                        "action": "sell",
                        "category": cat,
                        "fund_code": top.get("fund_code", ""),
                        "fund_name": top.get("fund_name", ""),
                        "reason": f"{label}超配{delta:+.0%}，建议减仓",
                        "amount_range": f"¥{excess_val * 0.3:,.0f} - ¥{excess_val * 0.7:,.0f}",
                    })
            else:
                # 欠配 → 建议加仓
                shortage_val = abs(delta) * total_assets
                suggestions.append({
                    "action": "buy",
                    "category": cat,
                    "reason": f"{label}欠配{delta:+.0%}，建议加仓",
                    "amount_range": f"¥{shortage_val * 0.3:,.0f} - ¥{shortage_val * 0.7:,.0f}",
                })

        # 低估指数加仓建议（如果有现金空间）
        if cash_ratio > 0.05:
            undervalued = [
                (code, info) for code, info in index_valuation.items()
                if info["percentile"] <= 30
            ]
            for code, info in undervalued[:2]:
                suggestions.append({
                    "action": "buy_index",
                    "category": "index",
                    "fund_code": code,
                    "fund_name": info["name"],
                    "reason": f"{info['name']}估值{info['percentile']:.0f}%百分位（低估），可考虑定投",
                    "amount_range": "¥1,000 - ¥3,000",
                })

        return {
            "total_assets": round(total_assets, 2),
            "cash_balance": round(cash_balance, 2),
            "current_allocation": {k: round(v, 4) for k, v in current_allocation.items()},
            "target_allocation": {k: round(v, 4) for k, v in target_allocation.items()},
            "drift": {k: round(v, 4) for k, v in drift.items()},
            "max_drift": round(max_drift, 4),
            "drift_level": drift_level,
            "suggestions": suggestions,
            "market_level": market_level,
            "market_avg_percentile": round(avg_pct, 1),
            "cash_target": round(cash_target, 4),
        }

    except Exception as e:
        logger.error(f"调仓分析失败: {e}")
        return {"error": str(e)}
