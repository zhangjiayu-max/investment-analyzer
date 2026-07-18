"""任务规划辅助函数。

为 Plan & Execute 编排器提供更稳定的任务语义：
- 问题类型分类
- 共享证据选择
- 仲裁模式选择
"""

from __future__ import annotations


_ACTION_KEYWORDS = [
    "买", "卖", "加仓", "减仓", "补仓", "建仓", "清仓", "止盈", "止损", "上车", "下车",
]
_ATTRIBUTION_KEYWORDS = [
    "为什么涨", "为什么跌", "为何", "怎么回事", "原因", "驱动", "归因",
]
_COMPARISON_KEYWORDS = [
    "vs", "VS", "对比", "比较", "哪个好", "区别", "差异", "相比",
]
_PREDICTION_KEYWORDS = [
    "会涨吗", "会跌吗", "还能涨", "还能跌", "见底", "到顶", "未来走势", "接下来", "还会",
]


def classify_question_type(query: str) -> str:
    """将用户问题粗分成 5 类。"""
    if not query:
        return "generic"

    for kw in _ACTION_KEYWORDS:
        if kw in query:
            return "action"
    for kw in _ATTRIBUTION_KEYWORDS:
        if kw in query:
            return "attribution"
    for kw in _COMPARISON_KEYWORDS:
        if kw in query:
            return "comparison"
    for kw in _PREDICTION_KEYWORDS:
        if kw in query:
            return "prediction"
    return "generic"


def build_shared_evidence_keys(question_type: str, complexity: str) -> list[str]:
    """为不同问题类型选择共享证据池。"""
    question_type = question_type or "generic"
    complexity = complexity or "medium"

    if question_type == "action":
        return ["portfolio", "valuation", "risk", "market_signal", "knowledge", "regression", "memory"]
    if question_type == "attribution":
        return ["news", "market", "macro", "knowledge", "regression", "memory"]
    if question_type == "prediction":
        return ["valuation", "news", "market", "knowledge", "regression", "memory"]
    if question_type == "comparison":
        return ["valuation", "portfolio", "knowledge", "memory"]
    if complexity == "complex":
        return ["portfolio", "valuation", "risk", "news", "market_signal", "knowledge", "regression", "memory"]
    return ["portfolio", "memory", "news", "knowledge"]


def build_arbitration_mode(question_type: str, complexity: str, step_count: int = 0) -> str:
    """选择仲裁模式。"""
    if question_type == "action":
        return "always"
    if complexity == "complex" or step_count >= 3:
        return "if_conflict"
    return "if_complex"


def build_task_focus(question_type: str) -> str:
    """给任务规划器的简短聚焦标签。"""
    if question_type == "action":
        return "先看估值，再看风险，最后给出仓位建议"
    if question_type == "attribution":
        return "先找驱动因素，再做宏观与市场验证"
    if question_type == "prediction":
        return "先看趋势和估值，再判断未来方向"
    if question_type == "comparison":
        return "先比较差异，再给出选择建议"
    return "围绕用户问题拆解所需证据与结论"
