"""评测自动评分 — LLM-as-Judge 对分析结果进行质量评估。

评估 Agent 从数据库加载（agents 表 id=3116），可通过 Agent 管理页面编辑 prompt。
"""

import json
import logging

from services.llm_service import _call_llm, MODEL
from db.config import get_config_int, get_config_float

logger = logging.getLogger(__name__)

# 评估 Agent ID（数据库中的质量评估师）
EVAL_AGENT_ID = 3116


def _get_eval_agent() -> dict | None:
    """从数据库加载评估 Agent。"""
    try:
        from db import get_agent
        return get_agent(EVAL_AGENT_ID)
    except Exception as e:
        logger.warning(f"加载评估 Agent 失败: {e}")
        return None


def _build_eval_prompt(analysis_type: str, expected_quality: str, actual_result: str,
                       agent_prompt: str = "") -> str:
    """构建评估 system prompt。"""
    return f"""评估以下投资分析的质量。

标准：{expected_quality[:300]}

分析内容：
{actual_result[:2000]}

评分：1-10分，5分起步，优点加分缺点扣分。
输出JSON：{{"score":分数,"reason":"优点和缺点"}}

只输出JSON，不要其他文字。"""


def _parse_score_response(text: str) -> tuple[float, str]:
    """解析 LLM 返回的评分 JSON（10分制）。"""
    import re
    text = text.strip()

    # 尝试直接解析
    try:
        data = json.loads(text)
        score = float(data.get("score", data.get("overall_score", 5)))
        reason = str(data.get("reason", data.get("overall_reason", "")))
        score = max(1.0, min(10.0, round(score)))
        return score, reason
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # 尝试从文本中提取 JSON（支持嵌套大括号和截断）
    json_patterns = [
        r'\{[^{}]*"score"\s*:\s*\d+[^{}]*\}',
        r'\{.*?"score"\s*:\s*\d+.*?"reason"\s*:.*?\}',
        r'\{.*?"overall_score"\s*:\s*\d+.*?\}',
    ]
    for pattern in json_patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                candidate = match.group()
                # 修复截断的 JSON
                if not candidate.endswith('}'):
                    # 截断在 reason 中间 → 补全引号和括号
                    if '"reason":' in candidate:
                        # 找到最后一个完整的值
                        last_brace = candidate.rfind('}')
                        if last_brace == -1:
                            candidate = candidate.rstrip() + '"}'  # 补全 reason 的引号和括号
                        else:
                            candidate = candidate[:last_brace + 1]
                    else:
                        candidate = candidate[:candidate.rfind('}') + 1]
                data = json.loads(candidate)
                score = float(data.get("score", data.get("overall_score", 5)))
                reason = str(data.get("reason", data.get("overall_reason", "")))
                score = max(1.0, min(10.0, round(score)))
                return score, reason
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

    # 尝试从思考过程中推断分数
    # 注意：排除"满分X分"、"X-10分"等描述性文字
    score_patterns = [
        r'(?:最终|最后|综合)[：：\s]*(?:给|评|打)?[：：\s]*(\d{1,2}(?:\.\d)?)\s*分',
        r'(?:score|rating)[：：\s]*(\d{1,2}(?:\.\d)?)',
        r'(\d{1,2}(?:\.\d)?)\s*分[（(].*?[）)]',
    ]
    for pattern in score_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                score = float(match.group(1))
                if 1 <= score <= 10:
                    # 排除"满分10分"等描述性文字
                    context_start = max(0, match.start() - 20)
                    context = text[context_start:match.end()]
                    if '满分' in context or '不允许' in context:
                        continue
                    start = max(0, match.start() - 50)
                    end = min(len(text), match.end() + 100)
                    reason = text[start:end].strip()
                    reason = re.sub(r'\s+', ' ', reason)[:150]
                    return score, reason
            except (ValueError, IndexError):
                pass

    # 兜底1：直接从文本找 "score": N
    score_match = re.search(r'"score"\s*:\s*(\d{1,2})', text)
    if score_match:
        score = float(score_match.group(1))
        if 1 <= score <= 10:
            # 提取 reason 部分
            reason_match = re.search(r'"reason"\s*:\s*"([^"]*)', text)
            reason = reason_match.group(1) if reason_match else "评分已提取"
            reason = reason[:150]
            return score, reason

    # 兜底2：从文本找 "X分" 但排除描述性文字
    for match in re.finditer(r'(\d{1,2})\s*分', text):
        score = float(match.group(1))
        if 1 <= score <= 10:
            context_start = max(0, match.start() - 30)
            context_end = min(len(text), match.end() + 20)
            context = text[context_start:context_end]
            if any(kw in context for kw in ['满分', '不允许', '评分标准', '1-5', '1-10', '到10分']):
                continue
            start = max(0, match.start() - 30)
            end = min(len(text), match.end() + 80)
            reason = text[start:end].strip()
            reason = re.sub(r'\s+', ' ', reason)[:150]
            return score, reason

    logger.warning(f"评分解析失败，使用默认5分。原始返回: {text[:300]}")
    return 5.0, "评分解析失败，使用默认分数"


def _parse_multi_dim_response(text: str) -> dict:
    """解析多维度评分 JSON。"""
    text = text.strip()

    # 尝试直接解析
    try:
        data = json.loads(text)
        return _normalize_scores(data)
    except (json.JSONDecodeError, ValueError):
        pass

    # 尝试从文本中提取 JSON
    import re
    match = re.search(r'\{[^{}]*"data_accuracy"[^{}]*\}', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            return _normalize_scores(data)
        except (json.JSONDecodeError, ValueError):
            pass

    # 兜底
    logger.warning(f"多维度评分解析失败: {text[:200]}")
    return {
        "data_accuracy": {"score": 5, "reason": "评分解析失败"},
        "logic": {"score": 5, "reason": "评分解析失败"},
        "actionability": {"score": 5, "reason": "评分解析失败"},
        "overall_score": 5,
        "overall_reason": "评分解析失败",
    }


def _normalize_scores(data: dict) -> dict:
    """标准化评分数据（10分制）。"""
    result = {}
    for key in ("data_accuracy", "logic", "actionability"):
        item = data.get(key, {})
        if isinstance(item, dict):
            score = max(1, min(10, int(item.get("score", 5))))
            reason = str(item.get("reason", ""))
        elif isinstance(item, (int, float)):
            score = max(1, min(10, int(item)))
            reason = ""
        else:
            score = 5
            reason = ""
        result[key] = {"score": score, "reason": reason}
    # overall
    overall = data.get("overall_score", 0)
    if isinstance(overall, (int, float)) and overall > 0:
        result["overall_score"] = max(1, min(10, int(overall)))
    else:
        # 计算平均分
        scores = [result[k]["score"] for k in ("data_accuracy", "logic", "actionability")]
        result["overall_score"] = round(sum(scores) / len(scores))
    result["overall_reason"] = str(data.get("overall_reason", ""))
    return result


async def score_eval_result(expected_quality: str, actual_result: str,
                            analysis_type: str = "") -> tuple[float, str]:
    """对 eval 运行结果进行自动评分。

    Args:
        expected_quality: 期望的质量标准描述
        actual_result: 实际分析输出（截断到3000字）
        analysis_type: 分析类型（如 panorama, deep_dive 等）

    Returns:
        (score, reason) — 1-10分 + 评语
    """
    if not expected_quality or not expected_quality.strip():
        return 0.0, "未设置期望质量，跳过自动评分"

    if not actual_result or len(actual_result.strip()) < 20:
        return 1.0, "输出内容过短或为空"

    # 从数据库加载评估 Agent
    agent = _get_eval_agent()
    agent_prompt = agent["system_prompt"] if agent else ""

    prompt = _build_eval_prompt(
        analysis_type=analysis_type or "未指定",
        expected_quality=expected_quality[:500],
        actual_result=actual_result[:3000],
        agent_prompt=agent_prompt,
    )

    try:
        # 关闭 thinking mode，强制模型直接输出 JSON
        resp = _call_llm(
            caller="eval_scorer",
            model=MODEL,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": "输出评分JSON："},
            ],
            temperature=get_config_float('llm.temperature_eval', 0.1),
            max_tokens=get_config_int('llm.max_tokens_eval_score', 800),
            extra_body={"thinking": {"type": "disabled"}},
        )
        msg = resp.choices[0].message
        text = msg.content or ""
        # MIMO thinking mode: content 可能在 reasoning_content 中
        reasoning = ""
        if hasattr(msg, "model_extra") and msg.model_extra:
            reasoning = msg.model_extra.get("reasoning_content") or ""
        if not reasoning:
            reasoning = getattr(msg, "reasoning_content", None) or ""
        logger.info(f"评分原始返回: content='{text[:200]}', reasoning='{reasoning[:200]}'")

        # 如果 content 为空但有 reasoning，从 reasoning 中提取结论
        if not text.strip() and reasoning.strip():
            logger.info("content 为空，尝试从 reasoning 提取结论")
            import re
            # 1. 先找 JSON
            json_match = re.search(r'\{[^{}]*"score"\s*:\s*\d+[^{}]*\}', reasoning)
            if json_match:
                text = json_match.group()
                logger.info(f"从 reasoning 提取到 JSON: {text[:100]}")
            else:
                # 2. 找最终结论性文字（在 reasoning 末尾）
                # 搜索 "综合"、"最终"、"给分" 等关键词附近的内容
                lines = reasoning.split('\n')
                for line in reversed(lines[-10:]):
                    line = line.strip()
                    if not line:
                        continue
                    # 找包含数字和"分"的行
                    if re.search(r'\d+\s*分', line):
                        text = line
                        logger.info(f"从 reasoning 末尾提取: {text[:100]}")
                        break

        score, reason = _parse_score_response(text)
        logger.info(f"评分结果: {score}分 — {reason}")
        return score, reason
    except Exception as e:
        logger.error(f"评分调用失败: {e}")
        return 0.0, f"评分失败: {str(e)[:100]}"


async def evaluate_llm_output(query: str, output: str, context: str = "",
                               target_type: str = "", target_id: int = None) -> dict:
    """对 LLM 产出进行多维度质量评估。

    Args:
        query: 用户原始问题
        output: LLM 产出文本
        context: 上下文信息（数据来源等）
        target_type: 目标类型（analysis/daily_report/chat）
        target_id: 关联目标 ID

    Returns:
        {
            "data_accuracy": {"score": int, "reason": str},
            "logic": {"score": int, "reason": str},
            "actionability": {"score": int, "reason": str},
            "overall_score": int,
            "overall_reason": str,
        }
    """
    if not output or len(output.strip()) < 50:
        return {
            "data_accuracy": {"score": 1, "reason": "输出过短"},
            "logic": {"score": 1, "reason": "输出过短"},
            "actionability": {"score": 1, "reason": "输出过短"},
            "overall_score": 1,
            "overall_reason": "输出过短",
        }

    # 从数据库加载评估 Agent
    agent = _get_eval_agent()
    agent_prompt = agent["system_prompt"] if agent else ""

    prompt = f"""{agent_prompt}

## 待评估内容

### 用户问题
{query[:500]}

### LLM 产出（前3000字）
{output[:3000]}

### 上下文信息
{context[:1000] if context else "无"}

## 重要：输出要求
你必须直接输出一个 JSON 对象，不要输出任何其他文字、思考过程或解释。

输出格式：
{{"data_accuracy": {{"score": 整数, "reason": "【优点】xxx【扣分】xxx"}}, "logic": {{"score": 整数, "reason": "【优点】xxx【扣分】xxx"}}, "actionability": {{"score": 整数, "reason": "【优点】xxx【扣分】xxx"}}, "overall_score": 整数, "overall_reason": "综合评语"}}

注意：
- score 必须是 1-10 的整数
- 直接输出 JSON，不要有其他任何内容
- 不要输出 ```json 代码块标记"""

    try:
        # 关闭 thinking mode，强制模型直接输出 JSON
        resp = _call_llm(
            caller="quality_evaluator",
            model=MODEL,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": "请对上述产出进行多维度评估。\n\n直接输出JSON，不要其他文字："},
            ],
            temperature=get_config_float('llm.temperature_eval', 0.1),
            max_tokens=get_config_int('llm.max_tokens_eval_score', 500),
            extra_body={"thinking": {"type": "disabled"}},
        )
        msg = resp.choices[0].message
        text = msg.content or ""
        # MIMO thinking mode
        if not text.strip() and hasattr(msg, "model_extra") and msg.model_extra:
            text = msg.model_extra.get("reasoning_content") or ""
        if not text.strip():
            text = getattr(msg, "reasoning_content", None) or ""
        scores = _parse_multi_dim_response(text)

        logger.info(f"质量评估完成: 综合{scores['overall_score']}分 "
                    f"(数据{scores['data_accuracy']['score']}, "
                    f"逻辑{scores['logic']['score']}, "
                    f"可执行{scores['actionability']['score']})")

        # 异步保存到 llm_feedback
        try:
            from db import save_llm_feedback
            save_llm_feedback(
                caller="quality_evaluator",
                input_summary=query[:200],
                output_summary=output[:200],
                rating="neutral",
                score_data_accuracy=scores["data_accuracy"]["score"],
                score_logic=scores["logic"]["score"],
                score_actionability=scores["actionability"]["score"],
                target_type=target_type,
                target_id=target_id,
            )
        except Exception as e:
            logger.warning(f"保存质量评估结果失败: {e}")

        return scores

    except Exception as e:
        logger.error(f"质量评估调用失败: {e}")
        return {
            "data_accuracy": {"score": 0, "reason": f"评估失败: {str(e)[:50]}"},
            "logic": {"score": 0, "reason": f"评估失败: {str(e)[:50]}"},
            "actionability": {"score": 0, "reason": f"评估失败: {str(e)[:50]}"},
            "overall_score": 0,
            "overall_reason": f"评估失败: {str(e)[:50]}",
        }
