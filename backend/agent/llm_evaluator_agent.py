"""LLM 评估 Agent — 通用的 LLM 输出质量评估

支持评估场景：
- 对话评估（conversation）
- 日报评估（daily_report）
- 热点评估（hot_topics）
- 持仓分析评估（portfolio）
"""

import json
import logging
import re
from typing import Optional

from llm_service import _call_llm, MODEL

logger = logging.getLogger(__name__)


# ── 评估 Prompt 模板 ──────────────────────────────────────

EVALUATOR_SYSTEM_PROMPT = """你是专业的 LLM 输出质量评估专家。你的任务是评估 AI 生成的投资分析内容的质量。

## 评估维度

### 1. 数据准确性 (25%)
- 数据来源是否清晰？
- 数字是否准确、是否有单位？
- 是否引用了具体的估值指标（PE/PB/百分位）？

### 2. 逻辑一致性 (25%)
- 分析逻辑是否自洽？
- 结论是否与论据匹配？
- 是否存在矛盾或跳跃？

### 3. 可执行性 (25%)
- 建议是否具体？
- 是否有明确的操作方向（买入/卖出/持有/等待）？
- 是否有时间/比例/仓位建议？

### 4. 用户理解 (25%)
- 是否回答了用户的核心问题？
- 是否考虑了用户的持仓情况？
- 是否考虑了用户的风险偏好？

## 输出格式

严格输出 JSON 格式，不要其他文字：
{
    "total_score": 0-100,
    "dimensions": {
        "data_accuracy": {"score": 0-100, "evidence": "具体证据", "issues": ["问题1"]},
        "logic_consistency": {"score": 0-100, "evidence": "具体证据", "issues": ["问题1"]},
        "actionability": {"score": 0-100, "evidence": "具体证据", "issues": ["问题1"]},
        "user_understanding": {"score": 0-100, "evidence": "具体证据", "issues": ["问题1"]}
    },
    "strengths": ["优点1", "优点2"],
    "weaknesses": ["问题1", "问题2"],
    "suggestions": ["建议1", "建议2"],
    "user_preference_hints": ["用户可能偏好...", "用户可能不喜欢..."]
}"""


# 场景特定的评估提示
SCENARIO_PROMPTS = {
    "conversation": """
## 评估重点
- 是否准确回答了用户的问题？
- 是否结合了用户的持仓情况？
- 建议是否具体可操作？
- 风险提示是否充分？
""",
    "daily_report": """
## 评估重点
- 市场数据是否准确？
- 分析逻辑是否清晰？
- 是否有明确的观点和建议？
- 是否关注了用户持仓的相关板块？
""",
    "hot_topics": """
## 评估重点
- 热点来源是否可靠？
- 与用户持仓的关联性如何？
- 投资建议是否具体？
- 时效性如何？
""",
    "portfolio": """
## 评估重点
- 持仓数据是否准确？
- 风险评估是否客观？
- 建议是否个性化？
- 是否考虑了用户的风险偏好？
""",
}


class LlmEvaluatorAgent:
    """LLM 评估 Agent"""

    def __init__(self):
        self.name = "LLM 评估专家"
        self.icon = "🔍"

    def evaluate(self, target_type: str, content: str, user_query: str = "",
                 user_context: dict = None, **kwargs) -> dict:
        """评估 LLM 输出质量

        参数:
            target_type: 评估场景（conversation/daily_report/hot_topics/portfolio）
            content: 要评估的内容
            user_query: 用户问题（对话场景）
            user_context: 用户上下文（持仓、偏好等）

        返回:
            {
                "total_score": 85,
                "dimensions": {...},
                "strengths": [...],
                "weaknesses": [...],
                "suggestions": [...],
                "user_preference_hints": [...]
            }
        """
        # 构建评估 prompt
        scenario_prompt = SCENARIO_PROMPTS.get(target_type, SCENARIO_PROMPTS["conversation"])

        # 构建用户上下文
        context_str = ""
        if user_context:
            if user_context.get("portfolio"):
                context_str += f"\n用户持仓：{json.dumps(user_context['portfolio'], ensure_ascii=False)[:500]}"
            if user_context.get("preferences"):
                context_str += f"\n用户偏好：{json.dumps(user_context['preferences'], ensure_ascii=False)[:200]}"

        # 构建完整 prompt
        user_prompt = f"""请评估以下 {target_type} 类型的 LLM 输出质量。

{scenario_prompt}

{f'用户问题：{user_query[:300]}' if user_query else ''}
{context_str}

## 待评估内容

{content[:3000]}

请严格按照 JSON 格式输出评估结果。"""

        try:
            response = _call_llm(
                caller="llm_evaluator",
                model=MODEL,
                messages=[
                    {"role": "system", "content": EVALUATOR_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=2000,
            )

            raw = response.choices[0].message.content
            if not raw:
                return self._default_result("LLM 返回空内容")

            raw = raw.strip()
            logger.info(f"LLM 评估返回: {raw[:300]}")

            # 解析 JSON
            result = self._parse_json(raw)

            # 验证和补全
            result = self._validate_result(result)

            return result

        except Exception as e:
            logger.error(f"LLM 评估失败: {e}")
            return self._default_result(str(e))

    def _parse_json(self, raw: str) -> dict:
        """解析 JSON，支持多种容错"""
        # 去除 markdown 代码块
        if "```" in raw:
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else parts[0]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        # 尝试直接解析
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # 提取 JSON 对象（支持嵌套）
        match = re.search(r'\{[\s\S]*\}', raw)
        if match:
            json_str = match.group()
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                # 尝试修复截断的 JSON
                return self._fix_truncated_json(json_str)

        raise ValueError("无法解析 JSON")

    def _fix_truncated_json(self, json_str: str) -> dict:
        """修复截断的 JSON"""
        # 尝试逐步截断，找到可解析的部分
        for i in range(len(json_str) - 1, 0, -1):
            if json_str[i] in ('}', ']', '"', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9'):
                try:
                    # 尝试补全括号
                    test_str = json_str[:i+1]
                    # 计算未闭合的括号
                    open_braces = test_str.count('{') - test_str.count('}')
                    open_brackets = test_str.count('[') - test_str.count(']')
                    test_str += ']' * open_brackets + '}' * open_braces
                    return json.loads(test_str)
                except json.JSONDecodeError:
                    continue

        # 如果无法修复，返回基本结构
        return {
            "total_score": 50,
            "dimensions": {
                "data_accuracy": {"score": 50, "evidence": "JSON 解析失败", "issues": []},
                "logic_consistency": {"score": 50, "evidence": "", "issues": []},
                "actionability": {"score": 50, "evidence": "", "issues": []},
                "user_understanding": {"score": 50, "evidence": "", "issues": []},
            },
            "strengths": [],
            "weaknesses": ["JSON 解析失败"],
            "suggestions": ["请重试评估"],
            "user_preference_hints": [],
        }

    def _validate_result(self, result: dict) -> dict:
        """验证和补全评估结果"""
        # 确保必要字段存在
        if "total_score" not in result:
            dims = result.get("dimensions", {})
            if dims:
                scores = [d.get("score", 0) for d in dims.values() if isinstance(d, dict)]
                result["total_score"] = sum(scores) / len(scores) if scores else 0
            else:
                result["total_score"] = 0

        # 确保 dimensions 结构正确
        default_dim = {"score": 0, "evidence": "", "issues": []}
        result.setdefault("dimensions", {})
        for key in ["data_accuracy", "logic_consistency", "actionability", "user_understanding"]:
            result["dimensions"].setdefault(key, default_dim.copy())

        # 确保列表字段
        result.setdefault("strengths", [])
        result.setdefault("weaknesses", [])
        result.setdefault("suggestions", [])
        result.setdefault("user_preference_hints", [])

        return result

    def _default_result(self, error: str) -> dict:
        """返回默认结果（评估失败时）"""
        return {
            "total_score": 0,
            "dimensions": {
                "data_accuracy": {"score": 0, "evidence": "", "issues": [error]},
                "logic_consistency": {"score": 0, "evidence": "", "issues": []},
                "actionability": {"score": 0, "evidence": "", "issues": []},
                "user_understanding": {"score": 0, "evidence": "", "issues": []},
            },
            "strengths": [],
            "weaknesses": [error],
            "suggestions": ["评估失败，请重试"],
            "user_preference_hints": [],
            "error": error,
        }


# ── 单例 ──────────────────────────────────────────────

_evaluator = None


def get_llm_evaluator() -> LlmEvaluatorAgent:
    """获取 LLM 评估 Agent 单例"""
    global _evaluator
    if _evaluator is None:
        _evaluator = LlmEvaluatorAgent()
    return _evaluator


# ── 便捷函数 ──────────────────────────────────────────────

def evaluate_conversation_output(conversation_id: int, message_id: int = None) -> dict:
    """评估对话输出"""
    from db.conversations import get_messages

    messages = get_messages(conversation_id)
    if not messages:
        return {"error": "无消息"}

    # 提取用户问题
    user_query = ""
    for msg in messages:
        if msg.get("role") == "user":
            user_query = msg.get("content", "")[:300]
            break

    # 提取目标消息
    target_msg = None
    if message_id:
        for msg in messages:
            if msg.get("id") == message_id:
                target_msg = msg
                break
    else:
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                target_msg = msg
                break

    if not target_msg:
        return {"error": "未找到目标消息"}

    content = target_msg.get("content", "")

    # 获取用户上下文
    user_context = _get_user_context()

    # 调用评估
    evaluator = get_llm_evaluator()
    return evaluator.evaluate(
        target_type="conversation",
        content=content,
        user_query=user_query,
        user_context=user_context,
    )


def evaluate_daily_report_output(report_content: str, date: str = None) -> dict:
    """评估日报输出"""
    user_context = _get_user_context()
    evaluator = get_llm_evaluator()
    return evaluator.evaluate(
        target_type="daily_report",
        content=report_content,
        user_query=f"今日市场日报（{date or '今天'}）",
        user_context=user_context,
    )


def evaluate_hot_topics_output(topics_content: str) -> dict:
    """评估热点分析输出"""
    user_context = _get_user_context()
    evaluator = get_llm_evaluator()
    return evaluator.evaluate(
        target_type="hot_topics",
        content=topics_content,
        user_query="今日热点投资机会分析",
        user_context=user_context,
    )


def evaluate_portfolio_output(analysis_content: str, question: str = "") -> dict:
    """评估持仓分析输出"""
    user_context = _get_user_context()
    evaluator = get_llm_evaluator()
    return evaluator.evaluate(
        target_type="portfolio",
        content=analysis_content,
        user_query=question or "持仓分析",
        user_context=user_context,
    )


def _get_user_context() -> dict:
    """获取用户上下文"""
    context = {}

    # 获取持仓信息
    try:
        from portfolio_context import build_portfolio_summary_line
        context["portfolio"] = build_portfolio_summary_line()
    except Exception:
        pass

    # 获取用户偏好
    try:
        from agent.feedback_learner import get_preference_context
        pref = get_preference_context("default")
        if pref:
            context["preferences"] = pref
    except Exception:
        pass

    return context
