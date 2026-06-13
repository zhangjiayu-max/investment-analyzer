"""RAG 检索质量评估套件。

预定义测试用例，覆盖典型检索场景。
可 CLI 运行，也可被 API 调用。

用法:
    cd backend && python3 scripts/rag_eval_suite.py
    cd backend && python3 scripts/rag_eval_suite.py --case 0  # 只跑第 1 个
"""

import asyncio
import json
import sys
import os
import time
import argparse

# 添加 backend 到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 评估用例 ──────────────────────────────────────────

RAG_EVAL_CASES = [
    # ── 估值查询 ──
    {
        "name": "沪深300估值查询",
        "query": "沪深300 估值 分析 投资",
        "expected_topics": ["沪深300", "市盈率", "百分位"],
        "category": "valuation",
    },
    {
        "name": "中证500能不能买",
        "query": "中证500 现在能买吗 估值高不高",
        "expected_topics": ["中证500", "估值"],
        "category": "valuation",
    },
    {
        "name": "创业板估值历史",
        "query": "创业板 历史估值 百分位",
        "expected_topics": ["创业板", "估值", "百分位"],
        "category": "valuation",
    },

    # ── 知识查询 ──
    {
        "name": "杜邦分析法",
        "query": "什么是杜邦分析法 怎么用",
        "expected_topics": ["杜邦", "ROE"],
        "category": "knowledge",
    },
    {
        "name": "指数基金vs主动基金",
        "query": "指数基金和主动基金有什么区别",
        "expected_topics": ["指数基金", "主动基金"],
        "category": "knowledge",
    },
    {
        "name": "PE和PB含义",
        "query": "PE PB 估值指标怎么看",
        "expected_topics": ["市盈率", "市净率"],
        "category": "knowledge",
    },

    # ── 策略查询 ──
    {
        "name": "定投策略",
        "query": "定投策略怎么选 定期定额",
        "expected_topics": ["定投", "策略"],
        "category": "strategy",
    },
    {
        "name": "债券基金买入时机",
        "query": "债券基金什么时候买 利率",
        "expected_topics": ["债券", "利率"],
        "category": "strategy",
    },
    {
        "name": "资产配置比例",
        "query": "股债怎么配比 资产配置",
        "expected_topics": ["配置", "股债"],
        "category": "strategy",
    },

    # ── 组合查询 ──
    {
        "name": "持仓风险分析",
        "query": "我的持仓风险大吗 集中度",
        "expected_topics": ["持仓", "风险"],
        "category": "portfolio",
    },
    {
        "name": "医药基金前景",
        "query": "医药基金还能买吗 创新药",
        "expected_topics": ["医药", "创新药"],
        "category": "portfolio",
    },

    # ── 行业/板块查询 ──
    {
        "name": "半导体行业",
        "query": "半导体 芯片 行业分析",
        "expected_topics": ["半导体", "芯片"],
        "category": "sector",
    },
    {
        "name": "新能源投资",
        "query": "新能源 光伏 锂电 投资机会",
        "expected_topics": ["新能源"],
        "category": "sector",
    },

    # ── 边界情况 ──
    {
        "name": "极短查询",
        "query": "估值",
        "expected_topics": ["估值"],
        "category": "edge",
    },
    {
        "name": "英文查询",
        "query": "PE ratio valuation",
        "expected_topics": ["PE", "估值"],
        "category": "edge",
    },
    {
        "name": "领域外查询",
        "query": "今天天气怎么样",
        "expected_topics": [],
        "category": "out_of_domain",
    },
]


async def run_eval_suite(cases: list[dict] = None, verbose: bool = True) -> dict:
    """运行评估套件。

    Args:
        cases: 测试用例列表，None 则用默认用例
        verbose: 是否输出详细信息

    Returns:
        {"cases": [...], "summary": {...}}
    """
    from agent.rag_evaluator import evaluate_rag_retrieval

    if cases is None:
        cases = RAG_EVAL_CASES

    results = []
    start_time = time.time()

    for i, case in enumerate(cases):
        if verbose:
            print(f"\n[{i+1}/{len(cases)}] {case['name']} — {case['query']}")

        try:
            eval_result = await evaluate_rag_retrieval(
                query=case["query"],
                expected_topics=case.get("expected_topics"),
            )
            eval_result["case_name"] = case["name"]
            eval_result["category"] = case.get("category", "unknown")
            results.append(eval_result)

            if verbose:
                p = eval_result["precision"]
                m = eval_result["mrr"]
                n = eval_result["ndcg"]
                emoji = "✅" if p >= 0.6 else ("⚠️" if p >= 0.4 else "❌")
                print(f"  {emoji} Precision={p:.2f} MRR={m:.2f} NDCG={n:.2f}")
                for d in eval_result.get("details", []):
                    rel_emoji = {"relevant": "✅", "partial": "⚠️", "irrelevant": "❌"}.get(d["relevance"], "❓")
                    print(f"    {rel_emoji} [{d['content_type']}] {d['title'][:30]} (score={d['score']:.4f})")

        except Exception as e:
            if verbose:
                print(f"  ❌ 评估失败: {e}")
            results.append({
                "case_name": case["name"],
                "category": case.get("category", "unknown"),
                "query": case["query"],
                "error": str(e),
            })

    elapsed = time.time() - start_time

    # 汇总统计
    valid_results = [r for r in results if "error" not in r]
    summary = {
        "total_cases": len(cases),
        "success_cases": len(valid_results),
        "failed_cases": len(cases) - len(valid_results),
        "avg_precision": sum(r["precision"] for r in valid_results) / len(valid_results) if valid_results else 0,
        "avg_mrr": sum(r["mrr"] for r in valid_results) / len(valid_results) if valid_results else 0,
        "avg_ndcg": sum(r["ndcg"] for r in valid_results) / len(valid_results) if valid_results else 0,
        "elapsed_seconds": round(elapsed, 1),
    }

    # 按类别汇总
    categories = {}
    for r in valid_results:
        cat = r.get("category", "unknown")
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(r)

    summary["by_category"] = {}
    for cat, cat_results in categories.items():
        summary["by_category"][cat] = {
            "count": len(cat_results),
            "avg_precision": sum(r["precision"] for r in cat_results) / len(cat_results),
            "avg_mrr": sum(r["mrr"] for r in cat_results) / len(cat_results),
        }

    if verbose:
        print(f"\n{'='*50}")
        print(f"评估完成: {summary['success_cases']}/{summary['total_cases']} 成功")
        print(f"平均 Precision: {summary['avg_precision']:.3f}")
        print(f"平均 MRR: {summary['avg_mrr']:.3f}")
        print(f"平均 NDCG: {summary['avg_ndcg']:.3f}")
        print(f"耗时: {summary['elapsed_seconds']}s")
        print(f"\n按类别:")
        for cat, stats in summary["by_category"].items():
            print(f"  {cat}: P={stats['avg_precision']:.2f} MRR={stats['avg_mrr']:.2f} ({stats['count']}条)")

    return {"cases": results, "summary": summary}


def save_results(output: dict, path: str = "data/rag_eval_results.json"):
    """保存评估结果到文件。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存到: {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG 检索质量评估套件")
    parser.add_argument("--case", type=int, help="只运行指定编号的用例（从 0 开始）")
    parser.add_argument("--output", default="data/rag_eval_results.json", help="输出文件路径")
    parser.add_argument("--quiet", action="store_true", help="静默模式")
    args = parser.parse_args()

    if args.case is not None:
        if 0 <= args.case < len(RAG_EVAL_CASES):
            cases = [RAG_EVAL_CASES[args.case]]
        else:
            print(f"用例编号 {args.case} 超出范围（0-{len(RAG_EVAL_CASES)-1}）")
            sys.exit(1)
    else:
        cases = RAG_EVAL_CASES

    result = asyncio.run(run_eval_suite(cases, verbose=not args.quiet))
    save_results(result, args.output)
