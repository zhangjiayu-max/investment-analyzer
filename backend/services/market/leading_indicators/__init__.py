"""领先指标接入层 — 统一抽象 + 多 Provider 聚合。

对外暴露：
- LeadingSignal: 统一信号数据结构
- LeadingIndicatorProvider: Provider 抽象接口
- collect_leading_signals(): 聚合入口
- call_akshare_with_timeout(): akshare 超时保护工具
"""
from services.market.leading_indicators.base import (
    LeadingSignal,
    LeadingIndicatorProvider,
)
from services.market.leading_indicators.akshare_utils import call_akshare_with_timeout
from services.market.leading_indicators.aggregator import (
    collect_leading_signals,
    register_provider,
)

__all__ = [
    "LeadingSignal",
    "LeadingIndicatorProvider",
    "collect_leading_signals",
    "register_provider",
    "call_akshare_with_timeout",
]
