"""输出审核层 — 检测 LLM 输出中的不合规内容，自动附加风险提示。

功能：
- 检测绝对化用语（"保证收益"、"稳赚不赔"等）
- 检测是否缺少风险提示
- 自动附加风险提示模板
"""

import re
import logging

logger = logging.getLogger(__name__)

# 绝对化用语（金融合规）
ABSOLUTE_PATTERNS = [
    (r"保证[收盈赚]", "保证收益"),
    (r"稳赚", "稳赚"),
    (r"包赚", "包赚"),
    (r"零风险", "零风险"),
    (r"无风险", "无风险"),
    (r"一定[会涨能赚]", "一定涨/赚"),
    (r"必定[涨赚]", "必定涨/赚"),
    (r"肯定[涨赚]", "肯定涨/赚"),
    (r"100%[收回]", "100%收益"),
    (r"翻倍", "翻倍"),
    (r"暴涨", "暴涨"),
    (r"只涨不跌", "只涨不跌"),
]

# 风险提示模板
RISK_DISCLAIMER = """

---
⚠️ **风险提示**：以上分析仅供参考，不构成投资建议。投资有风险，入市需谨慎。过往业绩不代表未来表现，请根据自身风险承受能力做出决策。"""

# 风险提示关键词（检测是否已有）
DISCLAIMER_KEYWORDS = ["风险提示", "风险提醒", "不构成投资建议", "投资有风险", "入市需谨慎"]

# 投资建议关键词（判断是否需要风险提示）
ADVICE_KEYWORDS = ["建议", "推荐", "买入", "卖出", "加仓", "减仓", "配置", "定投", "持有", "观望"]


def review_output(content: str, specialist_results: list = None) -> dict:
    """审核 LLM 输出，返回审核结果。

    Args:
        content: LLM 输出内容
        specialist_results: 专家分析结果列表（可选）

    Returns:
        {
            "approved": bool,       # 是否通过审核（无警告）
            "warnings": list[str],  # 警告列表
            "content": str,         # 审核后的内容（可能附加了风险提示）
            "has_disclaimer": bool, # 是否已包含风险提示
        }
    """
    if not content:
        return {"approved": True, "warnings": [], "content": content, "has_disclaimer": False}

    warnings = []

    # 1. 检测绝对化用语
    for pattern, label in ABSOLUTE_PATTERNS:
        matches = re.findall(pattern, content)
        if matches:
            warnings.append(f"检测到绝对化用语：{label}")

    # 2. 检测是否已有风险提示
    has_disclaimer = any(kw in content for kw in DISCLAIMER_KEYWORDS)

    # 3. 判断是否涉及投资建议
    is_advice = any(kw in content for kw in ADVICE_KEYWORDS)

    # 4. 也检查专家结果中是否有投资建议
    if specialist_results and not is_advice:
        for sr in (specialist_results or []):
            analysis = sr.get("analysis", "")
            if any(kw in analysis for kw in ADVICE_KEYWORDS):
                is_advice = True
                break

    # 5. 自动附加风险提示（如果涉及投资建议且没有）
    if is_advice and not has_disclaimer:
        content = content.rstrip() + RISK_DISCLAIMER
        has_disclaimer = True

    return {
        "approved": len(warnings) == 0,
        "warnings": warnings,
        "content": content,
        "has_disclaimer": has_disclaimer,
    }
