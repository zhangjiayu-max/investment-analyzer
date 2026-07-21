"""卖出操作时机守卫。

conv#131 修复：综合报告建议卖出/减仓/清仓/止损/止盈时，必须显式包含
估值分位和盈亏状态信息，避免"无时机判断的卖出"导致底部割肉。

设计原则：
- 不修改 LLM 输出，只追加警告区块（保持输出完整性）
- 纯 Python 正则实现，不引入新 LLM 调用
- 默认关闭，符合项目规范"新 LLM 相关开关默认 false"
- 与 arbitration_guard 互补：仲裁守卫管方向冲突，时机守卫管时机依据
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ── 卖出操作关键词（触发时机守卫） ──────────────────────
# 注意：关键词需要足够具体，避免误判"风险提示"段中的描述性文字
_SELL_KEYWORDS = [
    "建议卖出", "应该卖出", "可以卖出", "考虑卖出",
    "建议减仓", "应该减仓", "可以减仓", "考虑减仓",
    "建议止损", "应该止损", "可以止损", "考虑止损",
    "建议止盈", "应该止盈", "可以止盈", "考虑止盈",
    "立即卖出", "立即减仓", "立即止损", "立即止盈",
    "全部卖出", "全部清仓", "清仓", "赎回",
    "卖出止盈", "卖出止损", "减仓止盈",
]

# ── 估值分位识别正则 ──────────────────────────────────
# 匹配"PE 分位 15.2%"、"估值百分位 0.45%"、"PB 历史分位 30%"等
_PERCENTILE_PATTERNS = [
    re.compile(
        r'(?:百分位|分位|历史分位|估值分位|PE\s*分位|PB\s*分位|市盈率\s*分位|市净率\s*分位)'
        r'[^。\n]{0,30}?'
        r'(\d+(?:\.\d+)?\s*%)',
        re.IGNORECASE
    ),
    re.compile(
        r'(?:PE|PB|市盈率|市净率|pe_ttm|pb_ttm)'
        r'[^。\n]{0,15}?'
        r'(\d+(?:\.\d+)?)',
        re.IGNORECASE
    ),
]

# ── 盈亏状态识别正则 ──────────────────────────────────
# 匹配"盈亏率 -19.5%"、"亏损 15%"、"盈利 8.2%"、"profit_rate: -10%"等
_PROFIT_PATTERNS = [
    re.compile(
        r'(?:盈亏率|盈亏|亏损|盈利|浮亏|浮盈|收益率|profit_rate|profit_pct)'
        r'[^。\n]{0,30}?'
        r'(-?\d+(?:\.\d+)?\s*%)',
        re.IGNORECASE
    ),
    re.compile(
        r'(-?\d+(?:\.\d+)?)\s*%'
        r'[^。\n]{0,15}?'
        r'(?:盈亏|亏损|盈利|浮亏|浮盈)',
        re.IGNORECASE
    ),
]


def _detect_sell_actions(answer: str) -> list[str]:
    """检测 answer 中出现的卖出操作关键词。

    Returns:
        匹配到的关键词列表（去重）
    """
    if not answer:
        return []
    # 只检测"操作建议"段，避免误报"风险提示"段
    op_section = answer
    section_patterns = [
        r'(?:^|\n)#{1,6}\s*(?:第\s*4\s*段[:：]?|操作建议|具体操作建议).*$',
        r'(?:^|\n)#{1,6}\s*操作建议.*?(?=\n#{1,6}|\Z)',
    ]
    for pat in section_patterns:
        m = re.search(pat, answer, re.MULTILINE | re.DOTALL)
        if m:
            op_section = m.group(0)
            next_header = re.search(r'\n#{1,6}\s', op_section[1:])
            if next_header:
                op_section = op_section[:next_header.start() + 1]
            break

    # 如果没找到"操作建议"段，使用全文但排除"风险提示"段
    risk_pat = re.search(
        r'(?:^|\n)#{1,6}\s*(?:第\s*5\s*段[:：]?|风险提示|风险与盲点|风险提示与盲点).*$',
        answer, re.MULTILINE
    )
    if risk_pat and op_section is answer:
        op_section = answer[:risk_pat.start()]

    matched = []
    for kw in _SELL_KEYWORDS:
        if kw in op_section:
            matched.append(kw)
    return matched


def _has_percentile_info(answer: str) -> bool:
    """检测 answer 是否包含估值分位/PE/PB 信息。"""
    if not answer:
        return False
    for pat in _PERCENTILE_PATTERNS:
        if pat.search(answer):
            return True
    return False


def _has_profit_info(answer: str) -> bool:
    """检测 answer 是否包含盈亏状态信息。"""
    if not answer:
        return False
    for pat in _PROFIT_PATTERNS:
        if pat.search(answer):
            return True
    return False


def enforce_sell_timing_guard(
    answer: str,
    trace_id: str = "",
) -> tuple[str, list[str]]:
    """卖出操作时机守卫。

    检测综合报告中卖出/减仓/清仓等操作建议时，强制要求 answer 包含
    估值分位和盈亏状态信息。缺失时在 answer 末尾追加警告区块。

    Args:
        answer: 综合报告 LLM 生成的回答
        trace_id: 追踪 ID，用于日志

    Returns:
        (修正后的 answer, warnings 列表)
        - 若无卖出操作或开关关闭，answer 原样返回
        - 若有卖出操作但缺少时机依据，answer 末尾追加警告
        - warnings 为字符串列表，便于日志记录

    开关：
        agent.sell_timing_guard_enabled（默认 false）
    """
    warnings: list[str] = []

    # 开关检查
    try:
        from db.config import get_config_bool
        if not get_config_bool("agent.sell_timing_guard_enabled", False):
            return answer, warnings
    except Exception:
        # 配置读取失败时降级为关闭（保守策略）
        return answer, warnings

    if not answer:
        return answer, warnings

    # 检测卖出操作
    sell_kws = _detect_sell_actions(answer)
    if not sell_kws:
        return answer, warnings

    # 检测估值分位和盈亏状态
    has_percentile = _has_percentile_info(answer)
    has_profit = _has_profit_info(answer)

    if has_percentile and has_profit:
        logger.debug(
            f"[trace:{trace_id}] [sell_timing_guard] 卖出操作已包含时机依据: "
            f"keywords={sell_kws[:3]}"
        )
        return answer, warnings

    # 构建警告区块
    missing_parts = []
    if not has_percentile:
        missing_parts.append("估值分位（PE/PB 历史分位）")
        warnings.append("sell_without_percentile")
    if not has_profit:
        missing_parts.append("盈亏状态（当前盈亏率）")
        warnings.append("sell_without_profit")

    warning_block = (
        "\n\n---\n\n"
        "⚠️ **卖出时机守卫提示**\n\n"
        f"本回答包含卖出/减仓操作建议（{', '.join(sell_kws[:3])}），"
        f"但未提供以下时机依据：{', '.join(missing_parts)}。\n\n"
        "**建议**：\n"
        "- 先调用 `query_valuation` 查询标的当前估值分位，判断是否处于低位\n"
        "- 先调用 `query_portfolio` 确认标的当前盈亏率，区分止盈/止损\n"
        "- 若标的为亏损且估值低位，应遵循「止盈不止损」原则，禁止底部割肉\n"
        "- 若标的为盈利且估值高位，止盈操作可执行\n\n"
        "> 未提供时机依据的卖出建议可能导致「高买低卖」或「底部割肉」，请审慎参考。\n"
    )

    answer = answer.rstrip() + warning_block

    logger.warning(
        f"[trace:{trace_id}] [sell_timing_guard] 卖出操作缺少时机依据: "
        f"keywords={sell_kws[:3]}, missing={missing_parts}"
    )

    return answer, warnings
