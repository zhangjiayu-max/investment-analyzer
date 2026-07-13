"""大师理念矩阵 — 6位投资大师的多视角决策引擎。

每位大师基于7维体检数据的不同子集做规则映射，输出独立的评分和action建议。
最终聚合为大师矩阵，识别共识与冲突。

大师列表：
- 巴菲特（Warren Buffett）：护城河+ROE持续性+安全边际
- 林奇（Peter Lynch）：PEG+六类公司分类+快速增长
- 博格（John Bogle）：成本最小化+指数化+均值回归
- 马克斯（Howard Marks）：周期位置+逆向投资+风险控制
- 达利欧（Ray Dalio）：全天候配置+风险平价+分散化
- 段永平：好生意+好公司+好价格+能力圈
"""
import logging
from collections import Counter
from typing import Optional

logger = logging.getLogger(__name__)


# ── 大师元数据 ──────────────────────────────────────────────

MASTER_META = {
    "buffett": {
        "name": "巴菲特",
        "icon": "🏰",
        "philosophy": "护城河+ROE持续性+安全边际+长期持有",
    },
    "lynch": {
        "name": "林奇",
        "icon": "📈",
        "philosophy": "PEG+六类公司分类+快速增长",
    },
    "bogle": {
        "name": "博格",
        "icon": "💰",
        "philosophy": "成本最小化+指数化+均值回归",
    },
    "marks": {
        "name": "马克斯",
        "icon": "🔄",
        "philosophy": "周期位置+逆向投资+第二层次思维",
    },
    "dalio": {
        "name": "达利欧",
        "icon": "⚖️",
        "philosophy": "全天候配置+风险平价+分散化",
    },
    "duanyongping": {
        "name": "段永平",
        "icon": "🎯",
        "philosophy": "好生意+好公司+好价格+能力圈",
    },
}

ACTION_LABELS = {
    "strong_buy": "强烈加仓",
    "dca": "定投加仓",
    "hold": "持有",
    "reduce": "减仓",
    "wait": "等待",
}


# ── 辅助函数 ────────────────────────────────────────────────

def _safe_float(v, default: float = 0.0) -> float:
    """安全转float，处理NaN/None。"""
    try:
        f = float(v)
        if f != f:  # NaN check
            return default
        return f
    except (ValueError, TypeError):
        return default


def _score_to_rating(score: float) -> str:
    """分数转评级。"""
    if score >= 80:
        return "excellent"
    elif score >= 60:
        return "good"
    elif score >= 40:
        return "fair"
    else:
        return "poor"


def _avg_subscore(stock_scores: list, sub_key: str) -> float:
    """计算重仓股某子维度的平均分。"""
    if not stock_scores:
        return 50.0
    scores = []
    for s in stock_scores:
        sub = s.get(sub_key, {})
        if isinstance(sub, dict):
            scores.append(_safe_float(sub.get("score", 50)))
        else:
            scores.append(50.0)
    return sum(scores) / len(scores) if scores else 50.0


def _get_fundamental_detail(details: dict) -> dict:
    """安全获取基本面详情。"""
    fund = details.get("fundamental") or {}
    if not isinstance(fund, dict):
        return {}
    return fund


# ── 6位大师评分函数 ─────────────────────────────────────────

def _buffett_perspective(report: dict, details: dict) -> dict:
    """巴菲特视角：护城河+ROE持续性+安全边际+长期持有。

    数据来源：
    - 护城河：基本面.盈利能力（ROE+毛利率，高盈利=有护城河）
    - ROE持续性：基本面.稳定性（4季度标准差小=稳定）
    - 安全边际：估值水位（低估=有安全边际）
    - 长期持有：质量.经理稳定性
    """
    fundamental = _get_fundamental_detail(details)
    stock_scores = fundamental.get("stock_scores", [])

    # 护城河判定（基于重仓股盈利能力）
    profitability_avg = _avg_subscore(stock_scores, "profitability")
    has_moat = profitability_avg >= 75

    # ROE持续性（基于稳定性子指标）
    stability_avg = _avg_subscore(stock_scores, "stability")
    roe_consistent = stability_avg >= 65

    # 安全边际（估值低=有安全边际）
    valuation_score = report.get("valuation", {}).get("score", 50)
    valuation_level = "low" if valuation_score >= 75 else ("high" if valuation_score < 40 else "mid")
    has_margin_of_safety = valuation_level == "low"

    # 长期持有适合度（质量分高=适合长期持有）
    quality_score = report.get("quality", {}).get("score", 50)
    suitable_long_term = quality_score >= 60

    # 评分（0-100）
    score = (
        profitability_avg * 0.35  # 护城河权重最高
        + stability_avg * 0.25     # ROE持续性
        + valuation_score * 0.20   # 安全边际
        + quality_score * 0.20     # 长期持有适合度
    )

    # 决策逻辑
    if not has_moat:
        action = "wait"
        reason = f"无护城河（盈利能力{int(profitability_avg)}分），巴菲特不会买入无护城河的公司"
    elif not roe_consistent:
        action = "wait"
        reason = f"ROE不稳定（稳定性{int(stability_avg)}分），巴菲特偏好持续性强的标的"
    elif has_margin_of_safety and suitable_long_term:
        action = "strong_buy"
        reason = "有护城河+ROE稳定+有安全边际+适合长期持有，巴菲特式完美标的"
    elif has_margin_of_safety:
        action = "dca"
        reason = "有护城河+有安全边际，适合分批建仓"
    elif valuation_level == "high":
        action = "reduce"
        reason = f"好公司但估值偏高（{int(valuation_score)}分），巴菲特不追高"
    else:
        action = "hold"
        reason = "有护城河+ROE稳定，但估值不低，持有等待安全边际"

    return {
        "master_key": "buffett",
        "master_name": "巴菲特",
        "master_icon": "🏰",
        "core_philosophy": "护城河+ROE持续性+安全边际+长期持有",
        "score": round(score, 1),
        "rating": _score_to_rating(score),
        "action": action,
        "action_label": ACTION_LABELS.get(action, "持有"),
        "reason": reason,
        "key_metrics": {
            "has_moat": has_moat,
            "roe_consistent": roe_consistent,
            "margin_of_safety": has_margin_of_safety,
            "suitable_long_term": suitable_long_term,
            "profitability_avg": round(profitability_avg, 1),
            "stability_avg": round(stability_avg, 1),
        },
    }


def _lynch_perspective(report: dict, details: dict) -> dict:
    """林奇视角：PEG+六类公司分类+快速增长。

    数据来源：
    - PEG：PE / 净利润增速（PEG<1为低估）
    - 快速增长：基本面.成长性
    - 资产富裕：基本面.偿债能力
    - 估值合理性：估值水位
    """
    fundamental = _get_fundamental_detail(details)
    stock_scores = fundamental.get("stock_scores", [])

    # 成长性
    growth_avg = _avg_subscore(stock_scores, "growth")
    is_fast_grower = growth_avg >= 70

    # 偿债能力（资产富裕类）
    solvency_avg = _avg_subscore(stock_scores, "solvency")
    is_asset_rich = solvency_avg >= 75

    # 估值
    valuation_score = report.get("valuation", {}).get("score", 50)
    valuation_level = "low" if valuation_score >= 75 else ("high" if valuation_score < 40 else "mid")

    # PEG估算（简化：用估值分和成长性推算）
    # valuation_score高=PE低，growth_avg高=增速高 → PEG低
    # PEG ≈ (100 - valuation_score) / growth_avg  （简化模型）
    if growth_avg > 0:
        peg_estimate = max(0.3, min(3.0, (100 - valuation_score) / max(growth_avg, 1)))
    else:
        peg_estimate = 2.0

    # 公司分类（林奇六类公司简化为四类）
    if is_fast_grower:
        company_type = "快速增长型"
    elif is_asset_rich and not is_fast_grower:
        company_type = "资产富裕型"
    elif growth_avg < 40:
        company_type = "缓慢增长型"
    else:
        company_type = "稳健增长型"

    # 评分
    score = (
        growth_avg * 0.35       # 成长性权重最高
        + valuation_score * 0.25  # 估值合理性
        + solvency_avg * 0.20    # 资产质量
        + (100 - peg_estimate * 30) * 0.20  # PEG（PEG越低分越高）
    )
    score = max(0, min(100, score))

    # 决策逻辑
    if peg_estimate < 1.0 and is_fast_grower:
        action = "strong_buy"
        reason = f"PEG≈{peg_estimate:.1f}（<1）+ 快速增长型，林奇式完美标的"
    elif peg_estimate < 1.5 and is_fast_grower:
        action = "dca"
        reason = f"PEG≈{peg_estimate:.1f} + 快速增长型，适合定投"
    elif peg_estimate > 2.0:
        action = "wait"
        reason = f"PEG≈{peg_estimate:.1f}（>2），林奇不买贵的高增长"
    elif company_type == "缓慢增长型" and valuation_level == "low":
        action = "hold"
        reason = "缓慢增长型+估值低，林奇会持有但不加仓"
    elif valuation_level == "high":
        action = "reduce"
        reason = f"估值偏高，林奇会获利了结"
    else:
        action = "hold"
        reason = f"{company_type}，PEG≈{peg_estimate:.1f}，持有观察"

    return {
        "master_key": "lynch",
        "master_name": "林奇",
        "master_icon": "📈",
        "core_philosophy": "PEG+六类公司分类+快速增长",
        "score": round(score, 1),
        "rating": _score_to_rating(score),
        "action": action,
        "action_label": ACTION_LABELS.get(action, "持有"),
        "reason": reason,
        "key_metrics": {
            "peg_estimate": round(peg_estimate, 2),
            "company_type": company_type,
            "is_fast_grower": is_fast_grower,
            "is_asset_rich": is_asset_rich,
            "growth_avg": round(growth_avg, 1),
        },
    }


def _bogle_perspective(report: dict, details: dict) -> dict:
    """博格视角：成本最小化+指数化+均值回归+长期持有。

    数据来源：
    - 成本优势：质量.费率竞争力（从quality detail获取）
    - 指数化程度：质量.跟踪误差
    - 均值回归：趋势+回撤（回撤高位+趋势企稳=均值回归机会）
    - 估值合理性：估值水位
    """
    quality_detail = details.get("quality", {}) or {}
    quality_score = report.get("quality", {}).get("score", 50)

    # 成本优势（从质量详情获取fee_score）
    fee_score = 50.0
    if isinstance(quality_detail, dict):
        detail_data = quality_detail.get("detail", {}) or {}
        fee_score = _safe_float(detail_data.get("fee_score", 50))
    is_low_cost = fee_score >= 70

    # 指数化程度（跟踪误差小=指数化好）
    tracking_error_score = 50.0
    if isinstance(quality_detail, dict):
        detail_data = quality_detail.get("detail", {}) or {}
        tracking_error_score = _safe_float(detail_data.get("tracking_error_score", 50))
    is_indexed = tracking_error_score >= 70

    # 均值回归机会（回撤高位+趋势企稳）
    drawdown_detail = details.get("drawdown", {}) or {}
    drawdown_percentile = 0.3
    is_bottoming = False
    if isinstance(drawdown_detail, dict):
        detail_data = drawdown_detail.get("detail", {}) or {}
        drawdown_percentile = _safe_float(detail_data.get("drawdown_percentile", 0.3))
        is_bottoming = bool(detail_data.get("is_bottoming", False))

    trend_detail = details.get("trend", {}) or {}
    trend_arrangement = "tangled"
    if isinstance(trend_detail, dict):
        detail_data = trend_detail.get("detail", {}) or {}
        trend_arrangement = detail_data.get("arrangement", "tangled")

    mean_reversion_opportunity = drawdown_percentile >= 0.6 and (is_bottoming or trend_arrangement in ("weak_bull", "tangled"))

    # 估值
    valuation_score = report.get("valuation", {}).get("score", 50)
    valuation_level = "low" if valuation_score >= 75 else ("high" if valuation_score < 40 else "mid")

    # 评分
    score = (
        fee_score * 0.30            # 成本优势权重最高
        + tracking_error_score * 0.20  # 指数化程度
        + valuation_score * 0.25       # 估值合理性
        + quality_score * 0.25         # 整体质量
    )

    # 决策逻辑
    if not is_low_cost:
        action = "wait"
        reason = f"费率偏高（{int(fee_score)}分），博格反对高成本基金"
    elif valuation_level == "high":
        action = "reduce"
        reason = f"估值偏高（{int(valuation_score)}分），博格警惕均值回归风险"
    elif is_low_cost and is_indexed and valuation_level == "low":
        action = "strong_buy"
        reason = "低成本+指数化好+估值低，博格式理想标的"
    elif is_low_cost and valuation_level == "mid":
        action = "dca"
        reason = "低成本+估值中位，适合定投"
    elif mean_reversion_opportunity:
        action = "dca"
        reason = "回撤高位+趋势企稳，博格认为存在均值回归机会"
    else:
        action = "hold"
        reason = "低成本+估值合理，长期持有"

    return {
        "master_key": "bogle",
        "master_name": "博格",
        "master_icon": "💰",
        "core_philosophy": "成本最小化+指数化+均值回归+长期持有",
        "score": round(score, 1),
        "rating": _score_to_rating(score),
        "action": action,
        "action_label": ACTION_LABELS.get(action, "持有"),
        "reason": reason,
        "key_metrics": {
            "is_low_cost": is_low_cost,
            "is_indexed": is_indexed,
            "mean_reversion_opportunity": mean_reversion_opportunity,
            "fee_score": round(fee_score, 1),
            "tracking_error_score": round(tracking_error_score, 1),
        },
    }


def _marks_perspective(report: dict, details: dict) -> dict:
    """马克斯视角：周期位置+逆向投资+第二层次思维+风险控制。

    数据来源：
    - 周期位置：回撤.回撤分位（高位=周期底部）
    - 逆向信号：情绪.恐贪指数（恐惧时贪婪）
    - 风险评估：回撤.最大回撤（大=风险已释放）
    - 趋势确认：趋势.均线排列
    """
    drawdown_detail = details.get("drawdown", {}) or {}
    drawdown_percentile = 0.3
    is_bottoming = False
    if isinstance(drawdown_detail, dict):
        detail_data = drawdown_detail.get("detail", {}) or {}
        drawdown_percentile = _safe_float(detail_data.get("drawdown_percentile", 0.3))
        is_bottoming = bool(detail_data.get("is_bottoming", False))

    # 周期位置判定
    if drawdown_percentile >= 0.7:
        cycle_position = "底部"
    elif drawdown_percentile >= 0.4:
        cycle_position = "中位"
    else:
        cycle_position = "高位"

    # 逆向信号（情绪恐惧时贪婪）
    sentiment_detail = details.get("sentiment", {}) or {}
    fear_greed = 50
    if isinstance(sentiment_detail, dict):
        fear_greed = _safe_float(sentiment_detail.get("fear_greed_index", 50))
    is_fearful = fear_greed <= 35

    # 趋势确认
    trend_detail = details.get("trend", {}) or {}
    trend_arrangement = "tangled"
    if isinstance(trend_detail, dict):
        detail_data = trend_detail.get("detail", {}) or {}
        trend_arrangement = detail_data.get("arrangement", "tangled")
    trend_stabilizing = trend_arrangement in ("weak_bull", "tangled") and is_bottoming

    # 估值
    valuation_score = report.get("valuation", {}).get("score", 50)
    valuation_level = "low" if valuation_score >= 75 else ("high" if valuation_score < 40 else "mid")

    # 情绪分
    sentiment_score = report.get("sentiment", {}).get("score", 50)

    # 评分（马克斯强调逆向和周期）
    cycle_score = 90 if cycle_position == "底部" else (60 if cycle_position == "中位" else 30)
    score = (
        cycle_score * 0.35          # 周期位置权重最高
        + (100 - fear_greed) * 0.25  # 逆向信号（恐惧=高分）
        + valuation_score * 0.20     # 估值
        + sentiment_score * 0.20     # 情绪
    )

    # 决策逻辑
    if cycle_position == "底部" and is_fearful and trend_stabilizing:
        action = "strong_buy"
        reason = "周期底部+情绪恐惧+趋势企稳，马克斯式逆向加仓"
    elif cycle_position == "底部" and is_fearful:
        action = "dca"
        reason = "周期底部+情绪恐惧，分批逆向建仓"
    elif cycle_position == "高位" and valuation_level == "high":
        action = "reduce"
        reason = "周期高位+估值高，马克斯会减仓规避风险"
    elif trend_arrangement in ("strong_bear", "weak_bear") and not is_bottoming:
        action = "wait"
        reason = "趋势下行+未企稳，马克斯强调不在趋势不明时行动"
    else:
        action = "hold"
        reason = f"周期{cycle_position}，持有观察"

    return {
        "master_key": "marks",
        "master_name": "马克斯",
        "master_icon": "🔄",
        "core_philosophy": "周期位置+逆向投资+第二层次思维+风险控制",
        "score": round(score, 1),
        "rating": _score_to_rating(score),
        "action": action,
        "action_label": ACTION_LABELS.get(action, "持有"),
        "reason": reason,
        "key_metrics": {
            "cycle_position": cycle_position,
            "is_fearful": is_fearful,
            "trend_stabilizing": trend_stabilizing,
            "drawdown_percentile": round(drawdown_percentile, 2),
            "fear_greed": int(fear_greed),
        },
    }


def _dalio_perspective(report: dict, details: dict) -> dict:
    """达利欧视角：全天候配置+风险平价+债务周期+分散化。

    数据来源：
    - 分散化程度：持仓集中度（Top10覆盖率）
    - 风险平价：资金流向+波动率
    - 债务周期：宏观资金流向
    - 配置建议：估值+趋势综合
    """
    fundamental = _get_fundamental_detail(details)
    top10_coverage = _safe_float(fundamental.get("top10_coverage", 50))

    # 分散化判定
    is_well_diversified = top10_coverage < 50
    is_over_concentrated = top10_coverage >= 70

    # 资金流向（风险平价代理）
    capital_score = report.get("capital", {}).get("score", 50)
    is_risk_balanced = capital_score >= 60

    # 估值+趋势综合
    valuation_score = report.get("valuation", {}).get("score", 50)
    trend_score = report.get("trend", {}).get("score", 50)
    valuation_level = "low" if valuation_score >= 75 else ("high" if valuation_score < 40 else "mid")

    # 评分
    diversification_score = 90 if is_well_diversified else (60 if not is_over_concentrated else 30)
    score = (
        diversification_score * 0.35  # 分散化权重最高
        + capital_score * 0.25         # 风险平价
        + valuation_score * 0.20       # 估值
        + trend_score * 0.20           # 趋势
    )

    # 决策逻辑
    if is_over_concentrated:
        action = "reduce"
        reason = f"集中度过高（Top10={int(top10_coverage)}%），达利欧反对过度集中"
    elif valuation_level == "high" and is_over_concentrated:
        action = "reduce"
        reason = "估值高+集中度高，达利欧会减仓平衡风险"
    elif is_well_diversified and valuation_level == "low":
        action = "dca"
        reason = "分散良好+估值低，适合定投加仓"
    elif is_well_diversified and is_risk_balanced:
        action = "hold"
        reason = "分散良好+风险平衡，全天候配置持有"
    elif valuation_level == "high":
        action = "reduce"
        reason = "估值偏高，达利欧会再平衡降低风险"
    else:
        action = "hold"
        reason = "配置均衡，持有观察"

    return {
        "master_key": "dalio",
        "master_name": "达利欧",
        "master_icon": "⚖️",
        "core_philosophy": "全天候配置+风险平价+分散化",
        "score": round(score, 1),
        "rating": _score_to_rating(score),
        "action": action,
        "action_label": ACTION_LABELS.get(action, "持有"),
        "reason": reason,
        "key_metrics": {
            "is_well_diversified": is_well_diversified,
            "is_over_concentrated": is_over_concentrated,
            "is_risk_balanced": is_risk_balanced,
            "top10_coverage": round(top10_coverage, 1),
        },
    }


def _duanyongping_perspective(report: dict, details: dict) -> dict:
    """段永平视角：好生意+好公司+好价格+能力圈（结构化扩展）。

    数据来源：
    - 好生意：质量分
    - 好公司：基本面评级
    - 好价格：估值水位
    - 趋势：趋势均线排列
    """
    quality_score = report.get("quality", {}).get("score", 50)
    valuation_score = report.get("valuation", {}).get("score", 50)
    valuation_level = "low" if valuation_score >= 75 else ("high" if valuation_score < 40 else "mid")

    fundamental = _get_fundamental_detail(details)
    fundamental_rating = fundamental.get("rating", "fair")

    trend_detail = details.get("trend", {}) or {}
    trend_arrangement = "tangled"
    if isinstance(trend_detail, dict):
        detail_data = trend_detail.get("detail", {}) or {}
        trend_arrangement = detail_data.get("arrangement", "tangled")

    # 好生意判定
    if quality_score >= 70:
        business = "好生意"
        business_score = 90
    elif quality_score >= 50:
        business = "尚可的生意"
        business_score = 60
    else:
        business = "一般的生意"
        business_score = 30

    # 好公司判定
    company_map = {
        "excellent": ("好公司", 95),
        "good": ("质地良好的公司", 75),
        "fair": ("质地一般的公司", 50),
        "poor": ("质地差的公司", 25),
    }
    company, company_score = company_map.get(fundamental_rating, ("质地未知", 50))

    # 好价格判定
    if valuation_level == "low":
        price = "好价格(低估)"
        price_score = 90
    elif valuation_level == "high":
        price = "价格偏高(高估)"
        price_score = 30
    else:
        price = "价格合理(中估)"
        price_score = 60

    # 趋势
    if trend_arrangement in ("strong_bull", "weak_bull"):
        trend_text = "趋势向上"
        trend_score = 80
    elif trend_arrangement in ("strong_bear", "weak_bear"):
        trend_text = "趋势待反转"
        trend_score = 30
    else:
        trend_text = "趋势待确立"
        trend_score = 50

    # 评分（段永平：好生意+好公司+好价格三要素）
    score = (
        business_score * 0.35
        + company_score * 0.30
        + price_score * 0.20
        + trend_score * 0.15
    )

    # 决策逻辑
    if fundamental_rating == "poor" and valuation_level == "low":
        action = "wait"
        reason = "⚠️价值陷阱：估值低但公司质地差，段永平不抄底差公司"
    elif business_score >= 60 and company_score >= 75 and price_score >= 75:
        action = "strong_buy"
        reason = f"{business}+{company}+{price}，段永平式完美标的"
    elif fundamental_rating == "poor":
        action = "reduce"
        reason = f"{company}，段永平会减仓规避"
    elif valuation_level == "high":
        action = "reduce"
        reason = f"{price}，段永平不追高"
    elif business_score >= 60 and company_score >= 50:
        action = "hold"
        reason = f"{business}+{company}+{price}，持有"
    else:
        action = "wait"
        reason = f"{business}，段永平会在能力圈内等待"

    view_text = f"{business}+{company}+{price}+{trend_text}"

    return {
        "master_key": "duanyongping",
        "master_name": "段永平",
        "master_icon": "🎯",
        "core_philosophy": "好生意+好公司+好价格+能力圈",
        "score": round(score, 1),
        "rating": _score_to_rating(score),
        "action": action,
        "action_label": ACTION_LABELS.get(action, "持有"),
        "reason": reason,
        "view_text": view_text,
        "key_metrics": {
            "business": business,
            "company": company,
            "price": price,
            "trend": trend_text,
            "fundamental_rating": fundamental_rating,
        },
    }


# ── 大师矩阵聚合 + 共识检测 ─────────────────────────────────

def _detect_consensus(master_results: list[dict]) -> dict:
    """检测6位大师的共识与冲突。"""
    if not master_results:
        return {
            "consensus_action": "hold",
            "agreement": 0.0,
            "agreement_label": "无数据",
            "conflicts": [],
            "action_distribution": {},
        }

    actions = [m["action"] for m in master_results]
    action_counts = Counter(actions)
    majority_action, majority_count = action_counts.most_common(1)[0]
    agreement = majority_count / len(actions)

    # 识别冲突
    conflicts = []
    strong_buy_masters = [m["master_name"] for m in master_results if m["action"] == "strong_buy"]
    reduce_masters = [m["master_name"] for m in master_results if m["action"] == "reduce"]
    wait_masters = [m["master_name"] for m in master_results if m["action"] == "wait"]

    if strong_buy_masters and reduce_masters:
        conflicts.append(
            f"意见分歧：{'+'.join(strong_buy_masters)}建议加仓，{'+'.join(reduce_masters)}建议减仓"
        )
    if strong_buy_masters and wait_masters:
        conflicts.append(
            f"买卖信号冲突：{'+'.join(strong_buy_masters)}建议加仓，{'+'.join(wait_masters)}建议等待"
        )

    # 共识强度标签
    if agreement >= 0.83:
        agreement_label = "高度共识"
    elif agreement >= 0.67:
        agreement_label = "多数共识"
    elif agreement >= 0.50:
        agreement_label = "温和共识"
    else:
        agreement_label = "意见分歧"

    return {
        "consensus_action": majority_action,
        "consensus_action_label": ACTION_LABELS.get(majority_action, "持有"),
        "agreement": round(agreement, 2),
        "agreement_label": agreement_label,
        "agreement_count": f"{majority_count}/{len(actions)}",
        "conflicts": conflicts,
        "action_distribution": dict(action_counts),
    }


def build_master_perspectives_matrix(report: dict, details: dict) -> dict:
    """构建大师理念矩阵。

    Args:
        report: 7维评分报告 {quality, drawdown, trend, capital, sentiment, valuation, fundamental?}
        details: 详细维度数据 {quality, drawdown, trend, capital, sentiment, valuation, fundamental?, holding_changes?}

    Returns:
        {
            "masters": [6位大师评分结果],
            "consensus": {共识检测},
        }
    """
    # 判断是否有基本面数据（债基降级）
    fundamental = _get_fundamental_detail(details)
    has_fundamental = bool(fundamental.get("stock_scores"))

    masters = []

    # 巴菲特和林奇依赖基本面数据，无基本面时降级
    if has_fundamental:
        masters.append(_buffett_perspective(report, details))
        masters.append(_lynch_perspective(report, details))
    else:
        # 债基/无持仓：巴菲特和林奇跳过，给出说明
        for key, name, icon, philosophy in [
            ("buffett", "巴菲特", "🏰", "护城河+ROE持续性+安全边际"),
            ("lynch", "林奇", "📈", "PEG+六类公司分类+快速增长"),
        ]:
            masters.append({
                "master_key": key,
                "master_name": name,
                "master_icon": icon,
                "core_philosophy": philosophy,
                "score": None,
                "rating": None,
                "action": "hold",
                "action_label": "持有",
                "reason": f"无股票持仓数据，{name}视角不适用（适用于股票型基金）",
                "key_metrics": {"applicable": False},
            })

    # 博格/马克斯/达利欧/段永平不依赖基本面，正常评分
    masters.append(_bogle_perspective(report, details))
    masters.append(_marks_perspective(report, details))
    masters.append(_dalio_perspective(report, details))
    masters.append(_duanyongping_perspective(report, details))

    # 共识检测（只统计有评分的大师）
    scored_masters = [m for m in masters if m.get("score") is not None]
    consensus = _detect_consensus(scored_masters)

    return {
        "masters": masters,
        "consensus": consensus,
    }
