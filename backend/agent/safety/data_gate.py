"""数据质量门禁 — 在专家 Agent 执行前校验数据完整性。

借鉴企业级 RAG 检索质量门禁：
1. 必备数据完整性（如估值分析需有 PE/PB 数据）
2. 数据时效性（行情数据需 < 30 分钟）
3. 缺失时拒绝执行 + 提示用户补充
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


# ── 数据质量规则 ────────────────────────────────────────────

class DataQualityRule:
    """单条数据质量规则。"""

    def __init__(self, name: str, required_fields: list[str],
                 max_age_minutes: int = 60,
                 description: str = ""):
        self.name = name
        self.required_fields = required_fields
        self.max_age_minutes = max_age_minutes
        self.description = description

    def check(self, data: dict) -> tuple[bool, str]:
        """检查数据是否通过门禁。

        Returns:
            (passed, error_msg)
        """
        if not data:
            return False, f"数据为空（{self.description}）"

        # 必备字段检查
        missing = [f for f in self.required_fields if f not in data or data[f] is None]
        if missing:
            return False, f"缺少必要字段: {', '.join(missing)}"

        # 时效性检查（有 data_timestamp 字段时）
        ts = data.get("data_timestamp") or data.get("timestamp") or data.get("updated_at")
        if ts and self.max_age_minutes > 0:
            try:
                # 兼容多种格式
                ts_str = str(ts)
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                    try:
                        ts_dt = datetime.strptime(ts_str[:len(fmt)], fmt)
                        age = (datetime.now() - ts_dt).total_seconds() / 60
                        if age > self.max_age_minutes:
                            return False, f"数据已过期（{age:.0f} 分钟前，阈值 {self.max_age_minutes} 分钟）"
                        break
                    except ValueError:
                        continue
            except Exception:
                pass  # 时间解析失败不阻塞

        return True, ""


# ── 预设规则 ────────────────────────────────────────────────

RULES = {
    "valuation_analysis": DataQualityRule(
        name="估值分析",
        required_fields=["fund_code", "pe_percentile", "pb_percentile"],
        max_age_minutes=1440,  # 估值数据 1 天内有效
        description="估值分析需要 PE/PB 百分位数据"
    ),
    "market_timing": DataQualityRule(
        name="择时分析",
        required_fields=["fund_code", "current_price", "ma_20", "ma_60"],
        max_age_minutes=30,
        description="择时分析需要实时价格和均线数据"
    ),
    "portfolio_review": DataQualityRule(
        name="持仓体检",
        required_fields=["holdings", "total_market_value"],
        max_age_minutes=60,
        description="持仓体检需要完整持仓列表"
    ),
    "buy_decision": DataQualityRule(
        name="买入决策",
        required_fields=["fund_code", "valuation", "market_trend"],
        max_age_minutes=60,
        description="买入决策需要估值和趋势数据"
    ),
    "sell_decision": DataQualityRule(
        name="卖出决策",
        required_fields=["holding_id", "fund_code", "cost_price", "current_price"],
        max_age_minutes=30,
        description="卖出决策需要成本价和当前价"
    ),
}


# ── 门禁检查器 ──────────────────────────────────────────────

def check_data_quality(analysis_type: str, data: dict) -> dict:
    """检查数据质量门禁。

    Returns:
        {
            "passed": bool,
            "rule": str,
            "missing_fields": list[str],
            "error": str,
        }
    """
    rule = RULES.get(analysis_type)
    if not rule:
        # 没有预设规则的分析类型默认放行
        return {"passed": True, "rule": None, "missing_fields": [], "error": ""}

    passed, error_msg = rule.check(data)
    return {
        "passed": passed,
        "rule": rule.name,
        "missing_fields": [f for f in rule.required_fields if not data or f not in data or data[f] is None],
        "error": error_msg,
    }


def get_safe_fallback_message(analysis_type: str, error: str) -> str:
    """数据不足时的安全回退消息（不编造数据）。"""
    rule = RULES.get(analysis_type)
    if rule:
        return (
            f"⚠️ {rule.name}所需数据不完整，无法生成可靠分析。\n\n"
            f"原因：{error}\n\n"
            f"需要的数据：{', '.join(rule.required_fields)}\n"
            f"建议：稍后重试，或检查数据源是否正常。"
        )
    return f"⚠️ 数据不完整：{error}"
