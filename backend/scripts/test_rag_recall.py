#!/usr/bin/env python3
"""RAG 召回率与检索质量测试脚本。

覆盖场景：
1. 精确匹配 — 查询词直接出现在知识点中
2. 语义匹配 — 用不同表述问同一个概念
3. 跨书检索 — 一个查询应命中多本书
4. 全文 vs 向量 — 对比两种检索路径的效果
5. Query Rewrite — 测试改写是否提升召回
6. 边界场景 — 短查询、长查询、无结果查询

用法：
    cd backend && python3 scripts/test_rag_recall.py
"""

import sys
import time
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rag import init_fts, init_chroma, build_rag_context_with_details
from rag_enhanced import expand_query


# ══════════════════════════════════════════════════════════════
# 测试用例定义
# ══════════════════════════════════════════════════════════════

TEST_CASES = [
    # ── 1. 精确匹配：查询词直接出现在知识点中 ──
    {
        "id": "exact_01",
        "query": "格雷厄姆安全边际",
        "content_types": ["book"],
        "expected_any": ["聪明的投资者"],
        "description": "精确匹配：格雷厄姆 + 安全边际",
        "category": "精确匹配",
    },
    {
        "id": "exact_02",
        "query": "彼得林奇六种分类",
        "content_types": ["book"],
        "expected_any": ["彼得·林奇的成功投资"],
        "description": "精确匹配：彼得林奇的股票分类法",
        "category": "精确匹配",
    },
    {
        "id": "exact_03",
        "query": "霍华德马克斯周期",
        "content_types": ["book"],
        "expected_any": ["周期"],
        "description": "精确匹配：霍华德·马克斯的周期理论",
        "category": "精确匹配",
    },
    {
        "id": "exact_04",
        "query": "久期 久期缺口",
        "content_types": ["book"],
        "expected_any": ["债券投资实战"],
        "description": "精确匹配：债券久期概念",
        "category": "精确匹配",
    },
    {
        "id": "exact_05",
        "query": "定投 红利再投资",
        "content_types": ["book"],
        "expected_any": ["定投十年财务自由", "指数基金投资指南"],
        "description": "精确匹配：定投策略",
        "category": "精确匹配",
    },

    # ── 2. 语义匹配：不同表述，相同含义 ──
    {
        "id": "semantic_01",
        "query": "买股票什么时候卖",
        "content_types": ["book"],
        "expected_any": ["彼得·林奇的成功投资", "投资最重要的事"],
        "description": "语义匹配：卖出时机（不用专业术语）",
        "category": "语义匹配",
    },
    {
        "id": "semantic_02",
        "query": "如何分散风险",
        "content_types": ["book"],
        "expected_any": ["资产配置的艺术", "漫步华尔街", "聪明的投资者"],
        "description": "语义匹配：分散投资（口语化表达）",
        "category": "语义匹配",
    },
    {
        "id": "semantic_03",
        "query": "市场情绪恐慌怎么办",
        "content_types": ["book"],
        "expected_any": ["投资最重要的事", "思考，快与慢"],
        "description": "语义匹配：市场恐慌时的操作",
        "category": "语义匹配",
    },
    {
        "id": "semantic_04",
        "query": "基金费率太高怎么选",
        "content_types": ["book"],
        "expected_any": ["共同基金常识"],
        "description": "语义匹配：基金费率选择",
        "category": "语义匹配",
    },
    {
        "id": "semantic_05",
        "query": "牛市熊市怎么判断",
        "content_types": ["book"],
        "expected_any": ["周期", "投资中最简单的事"],
        "description": "语义匹配：牛熊判断",
        "category": "语义匹配",
    },

    # ── 3. 跨书检索：一个查询应命中多本书 ──
    {
        "id": "cross_01",
        "query": "资产配置比例",
        "content_types": ["book"],
        "expected_any": ["资产配置的艺术", "共同基金常识", "投资要义"],
        "min_results": 3,
        "description": "跨书检索：资产配置应命中多本",
        "category": "跨书检索",
    },
    {
        "id": "cross_02",
        "query": "指数基金 估值 买入",
        "content_types": ["book"],
        "expected_any": ["指数基金投资指南", "投资要义", "定投十年财务自由"],
        "min_results": 2,
        "description": "跨书检索：指数基金估值买入",
        "category": "跨书检索",
    },
    {
        "id": "cross_03",
        "query": "止损 止盈 策略",
        "content_types": ["book"],
        "expected_any": ["彼得·林奇的成功投资", "投资最重要的事"],
        "min_results": 2,
        "description": "跨书检索：止损止盈策略",
        "category": "跨书检索",
    },

    # ── 4. 多类型检索：book + author_article 混合 ──
    {
        "id": "multi_01",
        "query": "沪深300 定投",
        "content_types": None,
        "min_results": 2,
        "description": "多类型：沪深300定投（应命中书籍+文章）",
        "category": "多类型",
    },
    {
        "id": "multi_02",
        "query": "债券基金 收益率",
        "content_types": None,
        "min_results": 2,
        "description": "多类型：债券基金收益率",
        "category": "多类型",
    },

    # ── 5. 边界场景 ──
    {
        "id": "edge_01",
        "query": "PE",
        "content_types": ["book"],
        "description": "边界：极短查询（英文缩写）",
        "category": "边界场景",
    },
    {
        "id": "edge_02",
        "query": "在当前市场环境下，考虑到美联储加息周期接近尾声，国内经济复苏预期增强，应该如何调整我的基金投资组合配置比例",
        "content_types": ["book"],
        "description": "边界：超长查询",
        "category": "边界场景",
    },
    {
        "id": "edge_03",
        "query": "量子力学 投资",
        "content_types": ["book"],
        "expect_empty": True,
        "description": "边界：无关查询（应无结果）",
        "category": "边界场景",
    },

    # ── 6. Query Rewrite 对比 ──
    {
        "id": "rewrite_01",
        "query": "鸡尾酒会理论",
        "content_types": ["book"],
        "expected_any": ["彼得·林奇的成功投资"],
        "description": "Query Rewrite：彼得林奇鸡尾酒会理论",
        "category": "Query Rewrite",
        "test_rewrite": True,
    },
]


# ══════════════════════════════════════════════════════════════
# 测试执行
# ══════════════════════════════════════════════════════════════

def run_single_test(tc: dict, use_rewrite: bool = False) -> dict:
    """执行单个测试用例，返回结果。"""
    query = tc["query"]
    if use_rewrite:
        query = expand_query(query)

    t0 = time.time()
    result = build_rag_context_with_details(
        query=query,
        content_types=tc.get("content_types"),
        limit=10,
    )
    elapsed_ms = round((time.time() - t0) * 1000)

    results = result.get("results", [])
    fts_count = result.get("fts_count", 0)
    chroma_count = result.get("chroma_count", 0)

    # 检查是否命中预期来源（标题有 jieba 空格，需去空格匹配）
    hit_sources = set()
    for r in results:
        title = r.get("title", "")
        title_compact = title.replace(" ", "")  # 去掉分词空格
        for expected in tc.get("expected_any", []):
            if expected in title_compact:
                hit_sources.add(expected)

    expected = set(tc.get("expected_any", []))
    missed = expected - hit_sources

    # 判断通过条件
    passed = True
    fail_reason = ""

    if tc.get("expect_empty"):
        if len(results) > 0:
            passed = False
            fail_reason = f"期望无结果，但命中 {len(results)} 条"
    else:
        if expected and missed:
            passed = False
            fail_reason = f"未命中: {', '.join(missed)}"
        min_results = tc.get("min_results", 1)
        if len(results) < min_results:
            passed = False
            fail_reason = f"结果不足: {len(results)} < {min_results}"

    # 来源统计
    source_breakdown = {"fts_only": 0, "chroma_only": 0, "both": 0}
    for r in results:
        src = r.get("source", "")
        if src == "both":
            source_breakdown["both"] += 1
        elif src == "fts":
            source_breakdown["fts_only"] += 1
        elif src == "chroma":
            source_breakdown["chroma_only"] += 1

    return {
        "id": tc["id"],
        "query": tc["query"],
        "rewritten_query": query if use_rewrite else None,
        "description": tc["description"],
        "category": tc["category"],
        "passed": passed,
        "fail_reason": fail_reason,
        "elapsed_ms": elapsed_ms,
        "result_count": len(results),
        "fts_count": fts_count,
        "chroma_count": chroma_count,
        "source_breakdown": source_breakdown,
        "hit_sources": list(hit_sources),
        "missed_sources": list(missed),
        "top_results": [
            {"title": r.get("title", "").replace(" ", "")[:50], "source": r.get("source", ""), "score": round(r.get("_score", 0), 4)}
            for r in results[:5]
        ],
    }


def run_all_tests():
    """运行所有测试用例。"""
    init_fts()
    init_chroma()

    print("=" * 80)
    print("RAG 召回率与检索质量测试")
    print("=" * 80)

    all_results = []
    categories = {}

    for tc in TEST_CASES:
        cat = tc["category"]
        if cat not in categories:
            categories[cat] = []

        # 基础测试
        result = run_single_test(tc, use_rewrite=False)
        all_results.append(result)
        categories[cat].append(result)

        # 如果标记了 test_rewrite，额外跑一次 rewrite 对比
        if tc.get("test_rewrite"):
            result_rw = run_single_test(tc, use_rewrite=True)
            result_rw["id"] = tc["id"] + "_rewrite"
            result_rw["description"] = tc["description"] + " (Rewrite)"
            all_results.append(result_rw)
            categories[cat].append(result_rw)

    # ── 输出逐条结果 ──
    for cat, items in categories.items():
        print(f"\n{'─' * 80}")
        print(f"  {cat}")
        print(f"{'─' * 80}")

        for r in items:
            status = "✅" if r["passed"] else "❌"
            print(f"\n  {status} [{r['id']}] {r['description']}")
            print(f"     查询: \"{r['query']}\"", end="")
            if r.get("rewritten_query"):
                print(f" → \"{r['rewritten_query']}\"", end="")
            print()
            print(f"     耗时: {r['elapsed_ms']}ms | FTS5: {r['fts_count']} | 向量: {r['chroma_count']} | 合并: {r['result_count']}")
            print(f"     来源: 双路={r['source_breakdown']['both']}, 全文={r['source_breakdown']['fts_only']}, 向量={r['source_breakdown']['chroma_only']}")

            if r["hit_sources"]:
                print(f"     命中: {', '.join(r['hit_sources'])}")
            if r["missed_sources"]:
                print(f"     未命中: {', '.join(r['missed_sources'])}")
            if r["fail_reason"]:
                print(f"     原因: {r['fail_reason']}")

            if r["top_results"]:
                print(f"     Top5:")
                for i, tr in enumerate(r["top_results"]):
                    print(f"       [{i+1}] [{tr['source']}] {tr['title']} (score={tr['score']})")

    # ── 汇总统计 ──
    total = len(all_results)
    passed = sum(1 for r in all_results if r["passed"])
    failed = total - passed
    avg_time = sum(r["elapsed_ms"] for r in all_results) / total if total else 0
    avg_results = sum(r["result_count"] for r in all_results) / total if total else 0

    # 来源分布总统计
    total_both = sum(r["source_breakdown"]["both"] for r in all_results)
    total_fts = sum(r["source_breakdown"]["fts_only"] for r in all_results)
    total_chroma = sum(r["source_breakdown"]["chroma_only"] for r in all_results)

    print(f"\n{'=' * 80}")
    print(f"  汇总统计")
    print(f"{'=' * 80}")
    print(f"  总用例: {total} | 通过: {passed} ✅ | 失败: {failed} ❌ | 通过率: {passed/total*100:.1f}%")
    print(f"  平均耗时: {avg_time:.0f}ms | 平均命中: {avg_results:.1f} 条")
    print(f"  来源分布: 双路={total_both}, 仅全文={total_fts}, 仅向量={total_chroma}")

    # 分类统计
    print(f"\n  分类通过率:")
    for cat, items in categories.items():
        cat_total = len(items)
        cat_passed = sum(1 for r in items if r["passed"])
        cat_time = sum(r["elapsed_ms"] for r in items) / cat_total if cat_total else 0
        print(f"    {cat}: {cat_passed}/{cat_total} ({cat_passed/cat_total*100:.0f}%) | 平均 {cat_time:.0f}ms")

    # ── 失败用例分析 ──
    failed_cases = [r for r in all_results if not r["passed"]]
    if failed_cases:
        print(f"\n{'=' * 80}")
        print(f"  失败用例分析")
        print(f"{'=' * 80}")
        for r in failed_cases:
            print(f"\n  ❌ [{r['id']}] {r['description']}")
            print(f"     查询: \"{r['query']}\"")
            print(f"     原因: {r['fail_reason']}")
            if r["top_results"]:
                print(f"     实际 Top3:")
                for i, tr in enumerate(r["top_results"][:3]):
                    print(f"       [{i+1}] {tr['title']}")

    # ── 优化建议 ──
    print(f"\n{'=' * 80}")
    print(f"  优化建议")
    print(f"{'=' * 80}")

    suggestions = []

    # 检查语义匹配通过率
    semantic_results = [r for r in all_results if r["category"] == "语义匹配"]
    semantic_passed = sum(1 for r in semantic_results if r["passed"])
    if semantic_results and semantic_passed / len(semantic_results) < 0.8:
        suggestions.append("语义匹配通过率偏低，建议：\n    - 扩充同义词词典（rag_enhanced.py）\n    - 开启 Cross-Encoder Reranker（RERANK_ENABLED=true）\n    - 增大 ChromaDB 检索的 limit")

    # 检查来源分布
    if total_chroma > total_both * 2:
        suggestions.append("仅向量命中的结果较多，说明 FTS5 全文检索覆盖不足：\n    - 检查 jieba 分词是否覆盖投资专业术语\n    - 考虑给 knowledge_fts 表补充同义词字段")

    if total_fts > total_both * 2:
        suggestions.append("仅全文命中的结果较多，说明向量检索语义理解不足：\n    - 考虑换用更大的 embedding 模型\n    - 检查 chunk 切分是否破坏语义完整性")

    # 检查耗时
    slow_cases = [r for r in all_results if r["elapsed_ms"] > 1000]
    if slow_cases:
        suggestions.append(f"有 {len(slow_cases)} 个用例耗时超过 1s，建议：\n    - 检查 ChromaDB 索引是否需要重建\n    - 考虑对 embedding 模型做量化加速")

    # 检查跨书检索
    cross_results = [r for r in all_results if r["category"] == "跨书检索"]
    cross_passed = sum(1 for r in cross_results if r["passed"])
    if cross_results and cross_passed / len(cross_results) < 0.8:
        suggestions.append("跨书检索通过率偏低，建议：\n    - 增大 limit 参数（当前 10）\n    - 检查 RRF 融合是否偏向某一路")

    if not suggestions:
        suggestions.append("当前检索质量良好，无明显优化空间。")

    for i, s in enumerate(suggestions, 1):
        print(f"  {i}. {s}")

    # 保存结果到 JSON
    output_file = Path(__file__).parent.parent.parent / "data" / "rag_test_results.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({
            "summary": {
                "total": total,
                "passed": passed,
                "failed": failed,
                "pass_rate": round(passed / total * 100, 1),
                "avg_time_ms": round(avg_time),
                "avg_results": round(avg_results, 1),
                "source_breakdown": {
                    "both": total_both,
                    "fts_only": total_fts,
                    "chroma_only": total_chroma,
                },
            },
            "results": all_results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n  详细结果已保存: {output_file}")


if __name__ == "__main__":
    run_all_tests()
