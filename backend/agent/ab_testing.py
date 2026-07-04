"""A/B 测试框架 — 对比不同 prompt/策略的效果"""

import json
import time
import logging
from db.config import get_config_int

logger = logging.getLogger(__name__)


async def run_ab_test(
    test_name: str,
    queries: list[str],
    variant_a_fn,
    variant_b_fn,
    judge_fn=None,
) -> dict:
    """运行 A/B 测试。

    Args:
        test_name: 测试名称
        queries: 测试问题列表
        variant_a_fn: async (query) -> str
        variant_b_fn: async (query) -> str
        judge_fn: async (query, result_a, result_b) -> {a_score, b_score, reason}

    Returns:
        测试结果汇总
    """
    results = []
    a_wins = b_wins = ties = 0
    a_scores = []
    b_scores = []

    for query in queries:
        start_a = time.time()
        result_a = await variant_a_fn(query)
        time_a = time.time() - start_a

        start_b = time.time()
        result_b = await variant_b_fn(query)
        time_b = time.time() - start_b

        if judge_fn:
            judgment = await judge_fn(query, result_a, result_b)
        else:
            judgment = await _default_judge(query, result_a, result_b)

        a_score = judgment.get("a_score", 5)
        b_score = judgment.get("b_score", 5)
        a_scores.append(a_score)
        b_scores.append(b_score)

        if a_score > b_score + 0.5:
            a_wins += 1
        elif b_score > a_score + 0.5:
            b_wins += 1
        else:
            ties += 1

        results.append({
            "query": query,
            "a_score": a_score,
            "b_score": b_score,
            "a_time_ms": int(time_a * 1000),
            "b_time_ms": int(time_b * 1000),
            "reason": judgment.get("reason", ""),
        })

    return {
        "test_name": test_name,
        "total_queries": len(queries),
        "a_wins": a_wins,
        "b_wins": b_wins,
        "ties": ties,
        "a_avg_score": round(sum(a_scores) / len(a_scores), 2) if a_scores else 0,
        "b_avg_score": round(sum(b_scores) / len(b_scores), 2) if b_scores else 0,
        "details": results,
    }


async def _default_judge(query: str, result_a: str, result_b: str) -> dict:
    """默认 LLM-as-Judge 评分。"""
    from services.llm_service import _call_llm

    prompt = f"""对比以下两个投资分析结果的质量。

用户问题: {query}

--- 方案A ---
{result_a[:1500]}

--- 方案B ---
{result_b[:1500]}

评分标准: 准确性、完整性、可操作性、数据支撑
输出JSON: {{"a_score": 1-10, "b_score": 1-10, "reason": "简短理由"}}
只输出JSON。"""

    response = _call_llm(prompt, max_tokens=get_config_int('llm.max_tokens_eval_score', 200))
    try:
        data = json.loads(response.strip().strip("```json").strip("```"))
        return data
    except Exception:
        return {"a_score": 5, "b_score": 5, "reason": "评分解析失败"}
