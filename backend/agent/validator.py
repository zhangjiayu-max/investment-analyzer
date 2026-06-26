"""轻量反思：最终答案输出前的低成本质检。"""

import json
import logging
import re
from typing import Optional

from db.config import get_config, get_config_float
from llm_service import _call_llm, MODEL

logger = logging.getLogger(__name__)


class LightValidator:
    """轻量验证器：规则检查为主，LLM 检查为辅。"""

    _VAGUE_PHRASES = [
        "根据自身情况",
        "仅供参考",
        "可以考虑",
        "适当调整",
        "自行判断",
    ]

    _ACTION_KEYWORDS = ["买入", "卖出", "持有", "定投", "加仓", "减仓", "止盈", "止损", "清仓"]

    def __init__(self):
        pass

    def _rule_checks(self, query: str, final_answer: str, context: str) -> list[str]:
        issues = []

        # 1. 可执行性检查
        has_action = any(kw in final_answer for kw in self._ACTION_KEYWORDS)
        has_fund_code = bool(re.search(r"\b(\d{6}|\d{5,6})\b", final_answer))
        has_amount = bool(re.search(r"\d+元|\d+%|百分之\d+|仓位\d+", final_answer))
        has_trigger = any(kw in final_answer for kw in ["当", "如果", "跌至", "涨到", "分位", "阈值"])

        if not has_action:
            issues.append("可执行性：缺少明确的操作（买/卖/持有/定投等）")
        if has_action and not has_fund_code:
            issues.append("可执行性：操作建议缺少具体基金代码")
        if has_action and not has_amount:
            issues.append("可执行性：缺少金额或比例")
        if has_action and not has_trigger:
            issues.append("可执行性：缺少触发条件")

        # 2. 模糊表述检查
        for phrase in self._VAGUE_PHRASES:
            if phrase in final_answer:
                issues.append(f"模糊表述：包含'{phrase}'")

        # 3. 数据真实性/幻觉检查：答案中的基金代码是否在上下文中
        codes_in_answer = set(re.findall(r"\b(\d{6}|\d{5,6})\b", final_answer))
        codes_in_context = set(re.findall(r"\b(\d{6}|\d{5,6})\b", context))
        # 如果答案有基金代码，但上下文也有基金代码且完全不重叠，可能幻觉
        if codes_in_answer and codes_in_context and not codes_in_answer.intersection(codes_in_context):
            issues.append(f"数据真实性：答案中的基金代码 {codes_in_answer} 未在上下文中出现")

        return issues

    def _llm_check(self, query: str, final_answer: str, specialist_results: list, context: str) -> list[str]:
        # 默认关闭 LLM 质检，需在配置中显式开启
        if get_config("validator.llm_check_enabled", "false") != "true":
            return []

        expert_summary = "\n".join(
            f"- {sr.get('agent', sr.get('agent_key', '专家'))}: {sr.get('analysis', '')[:200]}"
            for sr in specialist_results
        )

        prompt = f"""你是答案质检员。检查最终答案是否存在问题，只返回 JSON。

原始问题：{query}
专家结论摘要：
{expert_summary}
上下文：{context[:800]}
最终答案：{final_answer}

请检查：
1. 最终答案是否与多数专家观点矛盾
2. 是否有数据无法从上下文中找到来源
3. 是否有明显逻辑错误

返回严格 JSON：{{"issues": ["问题1", "问题2"]}}，无问题返回 {{"issues": []}}"""

        try:
            response = _call_llm(
                caller="light_validator",
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=get_config_float("llm.temperature_tool", 0.2),
                max_tokens=400,
            )
            content = response.choices[0].message.content or ""
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                return data.get("issues", [])
        except Exception as e:
            logger.warning(f"LLM 质检失败: {e}")

        return []

    def validate(self, query: str, final_answer: str, specialist_results: list, context: str = "") -> dict:
        """验证最终答案。

        返回：
            {"passed": bool, "issues": list[str], "severity": "low|medium|high"}
        """
        issues = self._rule_checks(query, final_answer, context)
        issues.extend(self._llm_check(query, final_answer, specialist_results, context))

        # 去重
        issues = list(dict.fromkeys(issues))

        # 定级
        if any("可执行性" in i or "矛盾" in i for i in issues):
            severity = "high"
        elif any("数据真实性" in i or "幻觉" in i for i in issues):
            severity = "high"
        elif len(issues) >= 2:
            severity = "medium"
        elif issues:
            severity = "low"
        else:
            severity = "none"

        return {
            "passed": len(issues) == 0,
            "issues": issues,
            "severity": severity,
        }
