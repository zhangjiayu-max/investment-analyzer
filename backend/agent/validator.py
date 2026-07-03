"""轻量反思：最终答案输出前的低成本质检。

增强：金融数据校验 + Self-Consistency 验证（借鉴企业级防幻觉分层防御）。
"""

import json
import logging
import re
from typing import Optional

from db.config import get_config, get_config_float
from llm_service import _call_llm, MODEL
from agent.prompt_defense import validate_financial_data

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

        # 判断用户问题是否期待操作建议（持仓、买卖、操作类）
        action_intent_keywords = ["买", "卖", "持有", "操作", "定投", "加仓", "减仓", "止盈", "止损", "清仓", "我的", "持仓"]
        expects_action = any(kw in query for kw in action_intent_keywords)

        # 1. 可执行性检查（仅对明显需要操作建议的提问做严格检查）
        has_action = any(kw in final_answer for kw in self._ACTION_KEYWORDS)
        has_fund_code = bool(re.search(r"\b(\d{6}|\d{5,6})\b", final_answer))
        has_amount = bool(re.search(r"\d+元|\d+%|百分之\d+|仓位\d+", final_answer))
        has_trigger = any(kw in final_answer for kw in ["当", "如果", "跌至", "涨到", "分位", "阈值"])

        if expects_action and not has_action:
            issues.append("可执行性：缺少明确的操作（买/卖/持有/定投等）")
        if expects_action and has_action and not has_fund_code:
            issues.append("可执行性：操作建议缺少具体基金代码")
        if expects_action and has_action and not has_amount:
            issues.append("可执行性：缺少金额或比例")
        # 触发条件仅对买入/卖出/加仓/减仓等主动调仓动作强制要求；"持有"不要求
        active_action_keywords = ["买入", "卖出", "加仓", "减仓", "定投", "止盈", "止损", "清仓"]
        has_active_action = any(kw in final_answer for kw in active_action_keywords)
        if expects_action and has_active_action and not has_trigger:
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

    # ── 金融数据校验（纯规则，零 LLM 成本）──────────────────────

    def validate_financial(self, final_answer: str) -> dict:
        """检查答案中的金融数据是否合理（收益率/费率/净值范围）。"""
        return validate_financial_data(final_answer)

    # ── Self-Consistency 验证（仅关键决策场景）──────────────────

    # 触发 Self-Consistency 的分析类型（关键决策才跑，省 token）
    _KEY_DECISION_TYPES = {"portfolio_trade", "asset_allocation", "deep_dive", "buy_decision", "sell_decision"}

    def verify_self_consistency(self, output: str, analysis_type: str = "",
                                 n_samples: int = 3) -> dict:
        """Self-Consistency 检查：对关键结论多次采样验证一致性。

        仅对关键决策场景（买卖/调仓/深度分析）触发，日常简报跳过。
        """
        if analysis_type not in self._KEY_DECISION_TYPES:
            return {"consistent": True, "score": 1.0, "skipped": True}

        if get_config("validator.self_consistency_enabled", "false") != "true":
            return {"consistent": True, "score": 1.0, "skipped": True}

        # 提取关键结论
        key_claims = self._extract_key_claims(output)
        if not key_claims:
            return {"consistent": True, "score": 1.0, "skipped": True}

        agreement_scores: list[float] = []
        for claim in key_claims[:3]:  # 最多检查 3 个关键结论
            verify_prompt = (
                f"判断以下投资陈述是否准确合理（0-1 打分，只输出数字）：\n"
                f"陈述：{claim['text']}\n上下文：{claim.get('context', '')[:200]}"
            )
            scores: list[float] = []
            for _ in range(n_samples):
                try:
                    resp = _call_llm(
                        caller="self_consistency",
                        model=MODEL,
                        messages=[{"role": "user", "content": verify_prompt}],
                        temperature=0.5,
                        max_tokens=10,
                    )
                    val = float((resp.choices[0].message.content or "").strip())
                    scores.append(max(0.0, min(1.0, val)))
                except (ValueError, TypeError):
                    pass
                except Exception as e:
                    logger.warning(f"Self-Consistency 采样失败: {e}")

            if scores:
                agreement_scores.append(sum(scores) / len(scores))

        if not agreement_scores:
            return {"consistent": True, "score": 1.0, "skipped": True}

        final_score = sum(agreement_scores) / len(agreement_scores)
        return {
            "consistent": final_score > 0.7,
            "score": round(final_score, 3),
            "checked_claims": len(key_claims[:3]),
            "samples": n_samples,
        }

    @staticmethod
    def _extract_key_claims(output: str) -> list[dict]:
        """提取输出中的关键事实性结论（含操作建议的句子）。"""
        claims: list[dict] = []
        sentences = re.split(r"[。！；]", output)
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            if any(kw in sent for kw in ["建议", "推荐", "应该", "不推荐", "买入", "卖出", "增配", "减配", "止盈", "止损"]):
                claims.append({"text": sent, "context": output[:100]})
        return claims
