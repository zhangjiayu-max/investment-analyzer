"""RAG 检索质量评估器。

基于 RAGAS 思路，用 LLM-as-Judge 评估检索质量。

核心指标：
- Context Precision: 检索结果中有多少是相关的
- Context Recall: 期望主题是否都被检索到了
- MRR: 第一个相关结果排第几
- NDCG@K: 考虑位置权重的排序质量

用法:
    from agent.rag_evaluator import evaluate_rag_retrieval
    result = await evaluate_rag_retrieval("沪深300 估值 分析")
"""

import json
import logging
import math

from rag import build_rag_context_with_details
from llm_service import _call_llm, MODEL

logger = logging.getLogger(__name__)


async def evaluate_rag_retrieval(
    query: str,
    expected_topics: list[str] = None,
    limit: int = 5,
) -> dict:
    """评估单次 RAG 检索质量。

    Args:
        query: 检索查询
        expected_topics: 期望检索到的主题关键词（用于计算 recall）
        limit: 检索结果数量

    Returns:
        {
            "query": str,
            "precision": float,      # 0-1，相关结果占比
            "recall": float,         # 0-1，期望主题覆盖度
            "mrr": float,            # 0-1，首个相关结果排名倒数
            "ndcg": float,           # 0-1，排序质量
            "score_gap": float,      # 相关 vs 不相关的分数差距
            "details": list[dict],   # 每条结果的相关性判断
            "suggestions": list[str] # 优化建议
        }
    """
    # 1. 执行 RAG 检索
    rag_result = build_rag_context_with_details(query, limit=limit)
    results = rag_result.get("results", [])

    if not results:
        return {
            "query": query,
            "precision": 0.0,
            "recall": 0.0,
            "mrr": 0.0,
            "ndcg": 0.0,
            "score_gap": 0.0,
            "details": [],
            "suggestions": ["检索无结果，需要检查索引是否正常或查询词是否过于冷门"],
        }

    # 2. 用 LLM 判断每条结果的相关性
    details = await _judge_relevance(query, results)

    # 3. 计算指标
    precision = _calc_precision(details)
    mrr = _calc_mrr(details)
    ndcg = _calc_ndcg(details)
    score_gap = _calc_score_gap(details)

    # 4. 计算 topic recall（如果提供了期望主题）
    recall = _calc_topic_recall(details, expected_topics) if expected_topics else precision

    # 5. 生成优化建议
    suggestions = _generate_suggestions(details, rag_result, precision, mrr, ndcg)

    return {
        "query": query,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "mrr": round(mrr, 3),
        "ndcg": round(ndcg, 3),
        "score_gap": round(score_gap, 3),
        "details": details,
        "suggestions": suggestions,
    }


async def _judge_relevance(query: str, results: list[dict]) -> list[dict]:
    """用 LLM 判断每条检索结果与查询的相关性。"""
    # 构建评估 prompt
    items_text = []
    for i, r in enumerate(results):
        title = r.get("title", "")
        body = r.get("body", "")[:300]
        ct = r.get("content_type", "")
        items_text.append(f"[{i+1}] 类型={ct}, 标题={title}\n内容: {body}")

    items_str = "\n\n".join(items_text)

    prompt = f"""你是一个 RAG 检索质量评估专家。请判断以下检索结果与用户查询的相关性。

【用户查询】
{query}

【检索结果】
{items_str}

请对每条结果判断相关性，返回 JSON 数组：
[
  {{"id": 1, "relevance": "relevant", "reason": "..."}},
  {{"id": 2, "relevance": "partial", "reason": "..."}},
  ...
]

relevance 取值：
- "relevant": 直接相关，能回答用户问题
- "partial": 部分相关，有一定参考价值但不直接
- "irrelevant": 不相关，对回答无帮助

只返回 JSON 数组，不要其他内容。"""

    try:
        response = _call_llm(
            caller="rag_evaluator",
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2000,
        )
        content = response.choices[0].message.content or "[]"
        # 提取 JSON
        import re
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match:
            judgments = json.loads(json_match.group())
        else:
            judgments = json.loads(content)

        # 合并到结果中
        details = []
        for i, r in enumerate(results):
            judgment = judgments[i] if i < len(judgments) else {}
            details.append({
                "index": i + 1,
                "content_type": r.get("content_type", ""),
                "title": r.get("title", ""),
                "score": r.get("_score", 0),
                "source": r.get("source", ""),
                "relevance": judgment.get("relevance", "unknown"),
                "reason": judgment.get("reason", ""),
            })
        return details

    except Exception as e:
        logger.warning(f"LLM 相关性判断失败: {e}")
        # fallback: 基于分数简单判断
        details = []
        for i, r in enumerate(results):
            score = r.get("_score", 0)
            relevance = "relevant" if score >= 0.06 else ("partial" if score >= 0.03 else "irrelevant")
            details.append({
                "index": i + 1,
                "content_type": r.get("content_type", ""),
                "title": r.get("title", ""),
                "score": score,
                "source": r.get("source", ""),
                "relevance": relevance,
                "reason": f"基于分数 {score:.4f} 的自动判断",
            })
        return details


def _calc_precision(details: list[dict]) -> float:
    """Context Precision: 相关结果占比。"""
    if not details:
        return 0.0
    relevant = sum(1 for d in details if d.get("relevance") in ("relevant", "partial"))
    return relevant / len(details)


def _calc_mrr(details: list[dict]) -> float:
    """MRR (Mean Reciprocal Rank): 第一个相关结果的排名倒数。"""
    for i, d in enumerate(details):
        if d.get("relevance") == "relevant":
            return 1.0 / (i + 1)
    # 如果没有 "relevant"，找 "partial"
    for i, d in enumerate(details):
        if d.get("relevance") == "partial":
            return 0.5 / (i + 1)
    return 0.0


def _calc_ndcg(details: list[dict], k: int = 5) -> float:
    """NDCG@K: 考虑位置权重的排序质量。"""
    relevance_map = {"relevant": 3, "partial": 1, "irrelevant": 0, "unknown": 0}

    # DCG
    dcg = 0.0
    for i, d in enumerate(details[:k]):
        rel = relevance_map.get(d.get("relevance", "unknown"), 0)
        dcg += rel / math.log2(i + 2)  # i+2 because log2(1) = 0

    # Ideal DCG (按相关性降序排列)
    ideal_rels = sorted([relevance_map.get(d.get("relevance", "unknown"), 0) for d in details], reverse=True)
    idcg = 0.0
    for i, rel in enumerate(ideal_rels[:k]):
        idcg += rel / math.log2(i + 2)

    return dcg / idcg if idcg > 0 else 0.0


def _calc_score_gap(details: list[dict]) -> float:
    """相关 vs 不相关的分数差距。差距越大说明排序区分度越好。"""
    relevant_scores = [d["score"] for d in details if d.get("relevance") in ("relevant", "partial")]
    irrelevant_scores = [d["score"] for d in details if d.get("relevance") == "irrelevant"]

    if not relevant_scores or not irrelevant_scores:
        return 0.0

    avg_relevant = sum(relevant_scores) / len(relevant_scores)
    avg_irrelevant = sum(irrelevant_scores) / len(irrelevant_scores)

    return avg_relevant - avg_irrelevant


def _calc_topic_recall(details: list[dict], expected_topics: list[str]) -> float:
    """Topic Recall: 期望主题的覆盖度。"""
    if not expected_topics:
        return 1.0

    # 把所有结果的标题和内容拼起来
    all_text = " ".join(d.get("title", "") + " " + d.get("reason", "") for d in details)

    covered = 0
    for topic in expected_topics:
        if topic in all_text:
            covered += 1

    return covered / len(expected_topics)


def _generate_suggestions(
    details: list[dict],
    rag_result: dict,
    precision: float,
    mrr: float,
    ndcg: float,
) -> list[str]:
    """根据评估结果生成优化建议。"""
    suggestions = []

    # 精确率低
    if precision < 0.6:
        suggestions.append("精确率偏低（{:.0%}），建议优化查询词提取或提高分数阈值".format(precision))

    # MRR 低（第一个相关结果不在第 1 位）
    if mrr < 0.5:
        first_relevant = next((d for d in details if d.get("relevance") == "relevant"), None)
        if first_relevant:
            suggestions.append(f"首个相关结果排在第 {first_relevant['index']} 位，建议优化 RRF 排序权重")

    # NDCG 低（排序质量差）
    if ndcg < 0.7:
        suggestions.append("排序质量偏低（NDCG={:.2f}），建议调整 RRF k 值或增加标题匹配权重".format(ndcg))

    # 结果来源单一
    sources = set(d.get("source", "") for d in details)
    if len(sources) == 1 and len(details) > 1:
        suggestions.append("结果全部来自单一来源（{}），建议增加跨来源融合".format(sources.pop()))

    # 结果类型单一
    types = set(d.get("content_type", "") for d in details)
    if len(types) == 1 and len(details) > 1:
        suggestions.append("结果全部为同一类型（{}），建议增加知识库内容多样性".format(types.pop()))

    # 有不相关结果
    irrelevant = [d for d in details if d.get("relevance") == "irrelevant"]
    if irrelevant:
        titles = [d.get("title", "")[:20] for d in irrelevant[:2]]
        suggestions.append(f"有 {len(irrelevant)} 条不相关结果混入（如: {', '.join(titles)}），建议提高分数阈值或增加语义过滤")

    # 分数分布不合理
    relevant_scores = [d["score"] for d in details if d.get("relevance") == "relevant"]
    irrelevant_scores = [d["score"] for d in details if d.get("relevance") == "irrelevant"]
    if relevant_scores and irrelevant_scores:
        min_relevant = min(relevant_scores)
        max_irrelevant = max(irrelevant_scores)
        if min_relevant < max_irrelevant:
            suggestions.append(f"存在不相关结果分数（{max_irrelevant:.4f}）高于相关结果（{min_relevant:.4f}）的情况，建议调整融合权重")

    if not suggestions:
        suggestions.append("检索质量良好，各项指标正常")

    return suggestions
