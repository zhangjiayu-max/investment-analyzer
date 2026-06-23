"""统一行动提取器 — 从分析结果中提取可执行行动。"""
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# 行动类型常量
ACTION_WATCH = "watch"       # 加入关注
ACTION_BUY = "buy"           # 买入
ACTION_SELL = "sell"         # 卖出
ACTION_REDUCE = "reduce"     # 减仓
ACTION_REPLACE = "replace"   # 替换（费率优化）
ACTION_REBALANCE = "rebalance"  # 再平衡
ACTION_REVIEW = "review"     # 复盘

PRIORITY_HIGH = "high"
PRIORITY_MEDIUM = "medium"
PRIORITY_LOW = "low"


def extract_actions_from_hotspots(result: dict, holdings: list) -> list[dict]:
    """从热点分析结果中提取行动。"""
    actions = []
    recs = result.get("recommendations", [])
    for rec in recs:
        direction = rec.get("direction", "watch")
        code = rec.get("index_code", "")
        name = rec.get("index_name", "")
        score = rec.get("opportunity_score", 0)
        reason = rec.get("reason", "")

        # 检查是否已持有
        already_held = any(h.get("index_code") == code or h.get("fund_name") == name for h in holdings)

        if direction == "up":
            if score >= 70:
                action_type = ACTION_BUY if not already_held else ACTION_WATCH
                priority = PRIORITY_HIGH if score >= 80 else PRIORITY_MEDIUM
            else:
                action_type = ACTION_WATCH
                priority = PRIORITY_LOW
        elif direction == "down":
            action_type = ACTION_REDUCE if already_held else ACTION_WATCH
            priority = PRIORITY_MEDIUM
        else:
            action_type = ACTION_WATCH
            priority = PRIORITY_LOW

        actions.append({
            "action_type": action_type,
            "target_name": name,
            "target_code": code,
            "reason": reason[:100] if reason else f"热点分析推荐 {direction}",
            "priority": priority,
            "source": "hotspots",
            "score": score,
        })
    return actions


def extract_actions_from_health_score(result: dict) -> list[dict]:
    """从健康分结果中提取改进行动。"""
    actions = []
    dimensions = result.get("dimensions", {})
    dimension_names = {
        "quality": "持仓质量",
        "diversification": "分散度",
        "valuation": "估值合理性",
        "behavior": "交易行为",
        "risk": "风险控制",
    }

    for dim_key, dim_name in dimension_names.items():
        dim_data = dimensions.get(dim_key, {})
        score = dim_data.get("score", 100)
        if score < 60:
            detail = dim_data.get("detail", {})
            reason = _generate_health_improvement(dim_key, score, detail)
            actions.append({
                "action_type": ACTION_REBALANCE,
                "target_name": f"{dim_name}改进",
                "target_code": "",
                "reason": reason,
                "priority": PRIORITY_HIGH if score < 40 else PRIORITY_MEDIUM,
                "source": "health_score",
                "score": score,
            })
    return actions


def _generate_health_improvement(dim_key: str, score: int, detail: dict) -> str:
    """为低分维度生成改进建议。"""
    if dim_key == "quality":
        loss_count = detail.get("loss_count", 0)
        return f"你有{loss_count}只基金亏损，建议评估是否止损或定投摊薄成本"
    elif dim_key == "diversification":
        max_sector_pct = detail.get("max_sector_pct", 0)
        return f"持仓集中度{max_sector_pct}%，建议加入不同行业/资产类别基金"
    elif dim_key == "valuation":
        high_count = detail.get("high_valuation_count", 0)
        return f"{high_count}只基金处于高估区间，建议减仓或止盈"
    elif dim_key == "behavior":
        trade_count = detail.get("trade_count_30d", 0)
        return f"近30天交易{trade_count}次，频繁交易增加费率损耗"
    elif dim_key == "risk":
        max_single_pct = detail.get("max_single_pct", 0)
        return f"单只基金占比{max_single_pct}%，超过30%警戒线，建议分散"
    return f"该维度得分{score}，建议关注改进"


def extract_actions_from_fee(result: dict) -> list[dict]:
    """从费率分析中提取替换建议。"""
    actions = []
    funds = result.get("funds", [])
    for fund in funds:
        fee_rate = fund.get("total_fee_rate", 0)
        category_avg = fund.get("category_avg_fee", 0)
        if fee_rate > 0 and category_avg > 0 and fee_rate > category_avg * 1.5:
            savings = (fee_rate - category_avg) * fund.get("current_value", 0)
            actions.append({
                "action_type": ACTION_REPLACE,
                "target_name": fund.get("fund_name", ""),
                "target_code": fund.get("fund_code", ""),
                "reason": f"费率{fee_rate*100:.2f}%高于同类均值{category_avg*100:.2f}%，年多付¥{savings:.0f}",
                "priority": PRIORITY_MEDIUM,
                "source": "fee_analysis",
                "estimated_savings": round(savings, 2),
            })
    return actions


def extract_actions_from_correlation(result: dict) -> list[dict]:
    """从相关性分析中提取配置建议。"""
    actions = []
    pairs = result.get("high_correlation_pairs", [])
    for pair in pairs:
        corr = pair.get("correlation", 0)
        # 兼容 fund_a/fund_b 为字符串或字典的情况
        fund_a = pair.get("fund_a", "")
        fund_b = pair.get("fund_b", "")
        name_a = fund_a.get("name", fund_a) if isinstance(fund_a, dict) else str(fund_a)
        name_b = fund_b.get("name", fund_b) if isinstance(fund_b, dict) else str(fund_b)
        if corr > 0.8:
            actions.append({
                "action_type": ACTION_REBALANCE,
                "target_name": f"{name_a} ↔ {name_b}",
                "target_code": "",
                "reason": f"相关性{corr:.2f}过高，建议替换其中一只为低相关资产",
                "priority": PRIORITY_MEDIUM if corr > 0.9 else PRIORITY_LOW,
                "source": "correlation",
            })
    return actions


def extract_actions_from_four_pots(result: dict) -> list[dict]:
    """从四笔钱分析中提取再平衡建议。"""
    actions = []
    pots = result.get("pots", {})

    # 兼容中英文键名
    cash_data = pots.get("cash", pots.get("活钱管理", {}))
    long_data = pots.get("long_term", pots.get("长期投资", {}))
    total = sum(p.get("total_value", p.get("total", 0)) for p in pots.values()) or 1

    # 活钱太多（>30%）
    cash_value = cash_data.get("total_value", cash_data.get("total", 0))
    if cash_value / total > 0.3:
        actions.append({
            "action_type": ACTION_REBALANCE,
            "target_name": "活钱管理",
            "target_code": "",
            "reason": f"活钱占比{cash_value/total*100:.0f}%过高，建议转入稳健理财或长期投资",
            "priority": PRIORITY_MEDIUM,
            "source": "four_pots",
        })

    # 长期投资太少（<30%）
    long_value = long_data.get("total_value", long_data.get("total", 0))
    if long_value / total < 0.3 and total > 10000:
        actions.append({
            "action_type": ACTION_REBALANCE,
            "target_name": "长期投资",
            "target_code": "",
            "reason": f"长期投资占比{long_value/total*100:.0f}%偏低，建议增加权益类配置",
            "priority": PRIORITY_MEDIUM,
            "source": "four_pots",
        })
    return actions


def extract_actions_from_rolling(result: dict) -> list[dict]:
    """从滚动收益分析中提取复盘建议。"""
    actions = []
    stats = result.get("stats", {})
    win_rate = stats.get("win_rate", 50)
    if win_rate < 50:
        actions.append({
            "action_type": ACTION_REVIEW,
            "target_name": "滚动收益复盘",
            "target_code": "",
            "reason": f"持有胜率仅{win_rate:.0f}%，建议复盘买入时机和持有策略",
            "priority": PRIORITY_MEDIUM,
            "source": "rolling_return",
        })
    return actions


def extract_actions(analysis_type: str, result: dict, holdings: list = None) -> list[dict]:
    """统一入口：从任意分析结果中提取行动。"""
    holdings = holdings or []
    extractors = {
        "hotspots": lambda: extract_actions_from_hotspots(result, holdings),
        "health_score": lambda: extract_actions_from_health_score(result),
        "fee": lambda: extract_actions_from_fee(result),
        "correlation": lambda: extract_actions_from_correlation(result),
        "four_pots": lambda: extract_actions_from_four_pots(result),
        "rolling": lambda: extract_actions_from_rolling(result),
    }
    extractor = extractors.get(analysis_type)
    if not extractor:
        return []
    try:
        return extractor()
    except Exception as e:
        logger.warning(f"行动提取失败 [{analysis_type}]: {e}")
        return []


def format_actions_for_response(actions: list[dict]) -> list[dict]:
    """格式化行动列表，用于 API 响应。"""
    return [
        {
            "action_type": a.get("action_type", "watch"),
            "target_name": a.get("target_name", ""),
            "target_code": a.get("target_code", ""),
            "reason": a.get("reason", ""),
            "priority": a.get("priority", "low"),
            "source": a.get("source", ""),
            "score": a.get("score"),
            "estimated_savings": a.get("estimated_savings"),
        }
        for a in actions
    ]
