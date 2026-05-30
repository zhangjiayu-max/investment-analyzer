"""评测自动评分 — LLM-as-Judge 对分析结果进行质量评估。"""

import json
import logging

from llm_service import _call_llm, MODEL

logger = logging.getLogger(__name__)

# 评分 prompt 模板
_SCORE_PROMPT = """你是投资分析质量评审专家。请对以下分析结果进行严格打分。

## 评分标准（1-5分）

| 分数 | 含义 | 标准 |
|------|------|------|
| 5 | 优秀 | 专业、准确、可操作、覆盖全面、逻辑清晰 |
| 4 | 良好 | 较好，有小瑕疵但不影响核心判断 |
| 3 | 合格 | 基本可用，但有明显不足或遗漏 |
| 2 | 较差 | 存在事实错误、逻辑漏洞或严重偏题 |
| 1 | 不可用 | 严重错误、数据捏造或完全无价值 |

## 分析类型
{analysis_type}

## 期望质量标准
{expected_quality}

## 实际输出（前3000字）
{actual_result}

## 要求
1. 严格按 1-5 整数打分，不要给中间值
2. 评语控制在一句话，指出最关键的优点或问题
3. 必须输出严格 JSON 格式"""

_SCORE_USER_TEMPLATE = """请对上述分析结果打分。期望质量标准：{expected_quality}

输出严格JSON格式（不要包含其他文字）：
{{"score": 整数1-5, "reason": "一句话评语"}}"""


def _parse_score_response(text: str) -> tuple[float, str]:
    """解析 LLM 返回的评分 JSON。"""
    text = text.strip()
    # 尝试直接解析
    try:
        data = json.loads(text)
        score = float(data.get("score", 3))
        reason = str(data.get("reason", ""))
        # 合法性校验
        score = max(1.0, min(5.0, round(score)))
        return score, reason
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # 尝试从文本中提取 JSON
    import re
    match = re.search(r'\{[^}]*"score"\s*:\s*\d[^}]*\}', text)
    if match:
        try:
            data = json.loads(match.group())
            score = float(data.get("score", 3))
            reason = str(data.get("reason", ""))
            score = max(1.0, min(5.0, round(score)))
            return score, reason
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # 兜底：尝试从文本中提取分数
    match = re.search(r'[：:]\s*([1-5])\s*[分]', text)
    if match:
        return float(match.group(1)), text[:100]

    logger.warning(f"评分解析失败，使用默认3分。原始返回: {text[:200]}")
    return 3.0, "评分解析失败，使用默认分数"


async def score_eval_result(expected_quality: str, actual_result: str,
                            analysis_type: str = "") -> tuple[float, str]:
    """对 eval 运行结果进行自动评分。

    Args:
        expected_quality: 期望的质量标准描述
        actual_result: 实际分析输出（截断到3000字）
        analysis_type: 分析类型（如 panorama, deep_dive 等）

    Returns:
        (score, reason) — 1-5分 + 一句话评语
    """
    if not expected_quality or not expected_quality.strip():
        # 没有质量标准，跳过评分
        return 0.0, "未设置期望质量，跳过自动评分"

    if not actual_result or len(actual_result.strip()) < 20:
        return 1.0, "输出内容过短或为空"

    prompt = _SCORE_PROMPT.format(
        analysis_type=analysis_type or "未指定",
        expected_quality=expected_quality[:500],
        actual_result=actual_result[:3000],
    )

    try:
        resp = _call_llm(
            caller="eval_scorer",
            model=MODEL,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": _SCORE_USER_TEMPLATE.format(
                    expected_quality=expected_quality[:300]
                )},
            ],
            temperature=0.1,
            max_tokens=200,
        )
        text = resp.choices[0].message.content or ""
        score, reason = _parse_score_response(text)
        logger.info(f"评分结果: {score}分 — {reason}")
        return score, reason
    except Exception as e:
        logger.error(f"评分调用失败: {e}")
        return 0.0, f"评分失败: {str(e)[:100]}"
