"""仲裁-综合一致性硬校验。

conv#131 修复：仲裁裁决为"持有/观望"时，综合报告却出现"立即卖出"等操作建议，
导致仲裁被架空。本模块在综合报告生成后做代码层硬校验，检测方向冲突并追加警告。

设计原则：
- 不修改 LLM 输出，只追加警告区块（保持输出完整性）
- 只检测"操作建议"段，不检测"风险提示"段（避免误报）
- 所有新开关默认关闭，符合项目规范
- 纯 Python 关键词/正则实现，不引入新 LLM 调用
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ── 仲裁 verdict → stance 映射 ──────────────────────────────
# 与 arbitration.py:_stance_verdict_map 反向映射
_VERDICT_TO_STANCE = {
    "建议买入/加仓": "buy",
    "建议卖出/减仓": "sell",
    "建议持有/观望": "hold",
    "建议观望等待": "watch",
    "建议观望": "watch",
}

# ── answer 操作建议关键词 ──────────────────────────────────
# 注意：关键词需要足够具体，避免误判"风险提示"段的描述
_STANCE_KEYWORDS = {
    "sell": [
        "卖出", "减仓", "清仓", "止损", "退出", "赎回",
        "全部卖", "立即卖", "建议卖", "应该卖", "清空",
        "卖出止盈", "卖出止损",
    ],
    "buy": [
        "买入", "加仓", "建仓", "上车", "增持", "补仓", "定投",
        "建议买", "应该买", "立即买", "分批买",
    ],
    "hold": [
        "持有", "观望", "保持", "暂不", "等待",
    ],
}


def _extract_arbitration_stance(arbitration_summary: dict | None) -> tuple[str, str]:
    """从仲裁摘要提取 stance 和原始 verdict 文本。

    Returns:
        (stance, verdict_text) — stance 为 "buy"/"sell"/"hold"/"watch"/"unknown"
    """
    if not arbitration_summary:
        return "unknown", ""

    # 优先使用 verdict 字段（兼容字段，已在 conv#125 修复中添加）
    verdict = (arbitration_summary.get("verdict") or "").strip()
    if verdict:
        stance = _VERDICT_TO_STANCE.get(verdict)
        if stance:
            return stance, verdict

    # 兜底：从 final_stance 字段提取
    final_stance = (arbitration_summary.get("final_stance") or "").strip()
    if final_stance in ("buy", "sell", "hold", "watch"):
        return final_stance, verdict

    return "unknown", verdict


def _detect_answer_actions(answer: str) -> dict[str, list[str]]:
    """检测 answer 中出现的操作建议关键词。

    只检测"操作建议"段（第 4 段），避免误报"风险提示"段。

    Returns:
        {"sell": [匹配的关键词], "buy": [匹配的关键词], "hold": [匹配的关键词]}
    """
    if not answer:
        return {"sell": [], "buy": [], "hold": []}

    # 尝试提取"操作建议"段（第 4 段）
    # 综合报告 5 段结构：核心结论→推理链条→分歧反驳→操作建议→风险提示
    # 常见标题：## 操作建议、### 操作建议、## 第 4 段：操作建议
    op_section = answer
    section_patterns = [
        r'(?:^|\n)#{1,6}\s*(?:第\s*4\s*段[:：]?|操作建议|具体操作建议).*$',
        r'(?:^|\n)#{1,6}\s*操作建议.*?(?=\n#{1,6}|\Z)',
    ]
    for pat in section_patterns:
        m = re.search(pat, answer, re.MULTILINE | re.DOTALL)
        if m:
            op_section = m.group(0)
            # 截取到下一个标题
            next_header = re.search(r'\n#{1,6}\s', op_section[1:])
            if next_header:
                op_section = op_section[:next_header.start() + 1]
            break

    # 如果没找到"操作建议"段，使用全文但排除"风险提示"段
    risk_pat = re.search(
        r'(?:^|\n)#{1,6}\s*(?:第\s*5\s*段[:：]?|风险提示|风险与盲点|风险提示与盲点).*$',
        answer, re.MULTILINE
    )
    if risk_pat:
        # 如果找到了"操作建议"段，使用 op_section；否则使用风险段之前的内容
        if op_section is answer:
            op_section = answer[:risk_pat.start()]

    result = {"sell": [], "buy": [], "hold": []}
    for stance, keywords in _STANCE_KEYWORDS.items():
        for kw in keywords:
            if kw in op_section:
                result[stance].append(kw)
    return result


def _detect_conflict(
    arb_stance: str, answer_actions: dict[str, list[str]]
) -> tuple[bool, str, str]:
    """检测仲裁 stance 与 answer 操作建议是否冲突。

    Returns:
        (is_conflict, conflict_type, description)
    """
    if arb_stance == "unknown":
        return False, "", ""

    sell_kws = answer_actions.get("sell", [])
    buy_kws = answer_actions.get("buy", [])

    # 冲突规则：
    # - 仲裁 hold/watch 但 answer 有 sell/buy → 冲突
    # - 仲裁 buy 但 answer 有 sell → 冲突
    # - 仲裁 sell 但 answer 有 buy → 冲突
    if arb_stance in ("hold", "watch"):
        if sell_kws:
            return True, "hold_vs_sell", (
                f"仲裁裁决为持有/观望，但综合报告建议卖出操作（{', '.join(sell_kws[:3])}）"
            )
        if buy_kws:
            return True, "hold_vs_buy", (
                f"仲裁裁决为持有/观望，但综合报告建议买入操作（{', '.join(buy_kws[:3])}）"
            )
    elif arb_stance == "buy":
        if sell_kws:
            return True, "buy_vs_sell", (
                f"仲裁裁决为买入/加仓，但综合报告建议卖出操作（{', '.join(sell_kws[:3])}）"
            )
    elif arb_stance == "sell":
        if buy_kws:
            return True, "sell_vs_buy", (
                f"仲裁裁决为卖出/减仓，但综合报告建议买入操作（{', '.join(buy_kws[:3])}）"
            )

    return False, "", ""


def enforce_arbitration_consistency(
    answer: str,
    arbitration_summary: dict | None,
    trace_id: str = "",
) -> tuple[str, list[str]]:
    """仲裁-综合一致性硬校验。

    检测综合报告操作建议与仲裁裁决方向是否冲突，冲突时在 answer 末尾追加警告区块。

    Args:
        answer: 综合报告 LLM 生成的回答
        arbitration_summary: 仲裁模块输出的结构化裁决（含 verdict/reasoning）
        trace_id: 追踪 ID，用于日志

    Returns:
        (修正后的 answer, warnings 列表)
        - 若无冲突或开关关闭，answer 原样返回
        - 若有冲突，answer 末尾追加警告区块
        - warnings 为字符串列表，便于日志记录

    开关：
        agent.arbitration_consistency_guard_enabled（默认 false）
    """
    warnings: list[str] = []

    # 开关检查
    try:
        from db.config import get_config_bool
        if not get_config_bool("agent.arbitration_consistency_guard_enabled", False):
            return answer, warnings
    except Exception:
        # 配置读取失败时降级为关闭（保守策略）
        return answer, warnings

    # 仲裁摘要为空时跳过
    if not arbitration_summary:
        return answer, warnings

    # 提取仲裁 stance
    arb_stance, verdict_text = _extract_arbitration_stance(arbitration_summary)
    if arb_stance == "unknown":
        logger.debug(
            f"[trace:{trace_id}] [arbitration_guard] 无法识别仲裁 stance，跳过校验"
        )
        return answer, warnings

    # 检测 answer 操作建议
    answer_actions = _detect_answer_actions(answer)

    # 检测冲突
    is_conflict, conflict_type, description = _detect_conflict(arb_stance, answer_actions)
    if not is_conflict:
        logger.debug(
            f"[trace:{trace_id}] [arbitration_guard] 无冲突（arb_stance={arb_stance}）"
        )
        return answer, warnings

    # 冲突时追加警告区块
    reasoning = (arbitration_summary.get("reasoning") or "").strip()
    if len(reasoning) > 500:
        reasoning = reasoning[:497] + "..."

    warning_block = (
        "\n\n---\n\n"
        "⚠️ **仲裁一致性校验提示**\n\n"
        f"本回答的操作建议与仲裁裁决方向不一致，请审慎参考。\n\n"
        f"- **仲裁裁决**：{verdict_text or arb_stance}\n"
        f"- **冲突类型**：{conflict_type}\n"
        f"- **冲突描述**：{description}\n"
    )
    if reasoning:
        warning_block += f"- **仲裁推理依据**：\n\n> {reasoning}\n"
    warning_block += (
        "\n> 建议优先遵循仲裁裁决方向，或重新提交问题以获得更一致的分析。\n"
    )

    answer = answer.rstrip() + warning_block
    warnings.append(f"arbitration_conflict:{conflict_type}")

    logger.warning(
        f"[trace:{trace_id}] [arbitration_guard] 检测到仲裁-综合方向冲突: "
        f"type={conflict_type}, arb_stance={arb_stance}, "
        f"description={description[:100]}"
    )

    return answer, warnings
