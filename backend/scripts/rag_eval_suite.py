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


# ── 扩展评测用例（四大类，每类 15 个，共 60+ 用例）─────────────────────

EVAL_SUITE_CASES = {
    "valuation_analysis": [
        # 估值分析类
        {"query": "沪深300现在估值怎么样？", "expected_topics": ["沪深300", "PE", "百分位", "估值水平"]},
        {"query": "中证白酒估值高吗？", "expected_topics": ["中证白酒", "PE", "百分位"]},
        {"query": "医药50现在能买吗？", "expected_topics": ["医药50", "估值", "风险"]},
        {"query": "消费红利指数低估了吗？", "expected_topics": ["消费红利", "PE", "百分位"]},
        {"query": "红利指数和沪深300哪个更便宜？", "expected_topics": ["红利", "沪深300", "PE对比"]},
        {"query": "创业板估值处于什么水平？", "expected_topics": ["创业板", "PE", "历史分位"]},
        {"query": "现在A股整体估值贵不贵？", "expected_topics": ["A股", "整体估值", "市场温度"]},
        {"query": "恒生指数估值如何？", "expected_topics": ["恒生指数", "PE", "PB"]},
        {"query": "纳斯达克估值偏高吗？", "expected_topics": ["纳斯达克", "PE", "估值"]},
        {"query": "中证500和中证1000哪个更值得配置？", "expected_topics": ["中证500", "中证1000", "估值对比"]},
        {"query": "新能源板块估值回落到位了吗？", "expected_topics": ["新能源", "估值", "回调"]},
        {"query": "半导体指数现在估值多少？", "expected_topics": ["半导体", "PE", "估值"]},
        {"query": "银行股估值为什么这么低？", "expected_topics": ["银行", "PB", "低估值"]},
        {"query": "煤炭指数估值处于历史什么位置？", "expected_topics": ["煤炭", "估值", "历史分位"]},
        {"query": "军工板块估值偏高还是偏低？", "expected_topics": ["军工", "PE", "估值水平"]},
    ],
    "portfolio_diagnosis": [
        # 持仓诊断类
        {"query": "我的持仓风险大吗？", "expected_topics": ["持仓", "风险评估", "集中度"]},
        {"query": "帮我看看持仓有没有问题", "expected_topics": ["持仓分析", "偏离度", "建议"]},
        {"query": "持仓太集中了怎么办？", "expected_topics": ["集中度", "分散", "调仓"]},
        {"query": "我的基金亏了20%要不要卖？", "expected_topics": ["亏损", "止损", "基金分析"]},
        {"query": "现在仓位太重了要不要减仓？", "expected_topics": ["仓位", "减仓", "风险"]},
        {"query": "帮我做个持仓健康检查", "expected_topics": ["持仓健康", "估值", "行业分布"]},
        {"query": "我的组合跑赢大盘了吗？", "expected_topics": ["组合收益", "基准对比", "超额收益"]},
        {"query": "哪些基金该卖了？", "expected_topics": ["止盈", "估值偏高", "基金筛选"]},
        {"query": "持仓里有没有暴雷风险？", "expected_topics": ["风险排查", "基金经理", "规模"]},
        {"query": "定投的基金该不该继续？", "expected_topics": ["定投", "估值", "策略"]},
        {"query": "帮我优化一下持仓结构", "expected_topics": ["持仓优化", "行业配置", "风险收益"]},
        {"query": "我的持仓适合当前市场环境吗？", "expected_topics": ["持仓", "市场环境", "适配性"]},
        {"query": "固收+基金还能持有吗？", "expected_topics": ["固收+", "债市", "风险"]},
        {"query": "指数基金和主动基金怎么选？", "expected_topics": ["指数基金", "主动基金", "对比"]},
        {"query": "我的FOF持仓合理吗？", "expected_topics": ["FOF", "持仓", "配置"]},
    ],
    "market_interpretation": [
        # 市场解读类
        {"query": "最近市场为什么跌？", "expected_topics": ["市场下跌", "原因分析", "影响因素"]},
        {"query": "今天大盘怎么回事？", "expected_topics": ["大盘", "行情", "分析"]},
        {"query": "美联储加息对A股影响大吗？", "expected_topics": ["美联储", "加息", "A股影响"]},
        {"query": "消费板块最近为什么涨？", "expected_topics": ["消费板块", "上涨", "原因"]},
        {"query": "北向资金流出意味着什么？", "expected_topics": ["北向资金", "流出", "信号"]},
        {"query": "降准对市场有什么影响？", "expected_topics": ["降准", "货币政策", "市场影响"]},
        {"query": "最近有什么值得关注的政策？", "expected_topics": ["政策", "消费政策", "影响"]},
        {"query": "中美关系对投资有什么影响？", "expected_topics": ["中美关系", "贸易", "投资影响"]},
        {"query": "房地产政策放松了该买房还是买地产股？", "expected_topics": ["房地产", "政策", "投资选择"]},
        {"query": "最近债市怎么样？", "expected_topics": ["债市", "利率", "债券收益"]},
        {"query": "黄金还能买吗？", "expected_topics": ["黄金", "避险", "估值"]},
        {"query": "新能源板块还有机会吗？", "expected_topics": ["新能源", "行业分析", "前景"]},
        {"query": "AI概念股泡沫大吗？", "expected_topics": ["AI", "估值", "泡沫"]},
        {"query": "港股现在值得配置吗？", "expected_topics": ["港股", "估值", "配置价值"]},
        {"query": "可转债市场现在什么情况？", "expected_topics": ["可转债", "市场", "估值"]},
    ],
    "strategy_advice": [
        # 策略建议类
        {"query": "现在该怎么配置资产？", "expected_topics": ["资产配置", "策略", "建议"]},
        {"query": "定投策略该怎么调整？", "expected_topics": ["定投", "估值加权", "策略优化"]},
        {"query": "低估多投的策略靠谱吗？", "expected_topics": ["低估多投", "估值策略", "回测"]},
        {"query": "止盈点该设多少？", "expected_topics": ["止盈", "估值阈值", "策略"]},
        {"query": "要不要做再平衡？", "expected_topics": ["再平衡", "偏离度", "触发条件"]},
        {"query": "新手该怎么开始投资？", "expected_topics": ["新手", "入门", "定投"]},
        {"query": "10万块钱怎么配置？", "expected_topics": ["资金配置", "比例", "品种"]},
        {"query": "保守型投资者该怎么配？", "expected_topics": ["保守", "低风险", "债券为主"]},
        {"query": "退休后投资策略怎么调整？", "expected_topics": ["退休", "低风险", "现金流"]},
        {"query": "牛市和熊市策略有什么区别？", "expected_topics": ["牛市", "熊市", "策略差异"]},
        {"query": "用估值做择时靠谱吗？", "expected_topics": ["估值择时", "历史回测", "胜率"]},
        {"query": "股债比例该怎么定？", "expected_topics": ["股债比例", "风险偏好", "年龄"]},
        {"query": "行业轮动策略有效吗？", "expected_topics": ["行业轮动", "策略", "回测"]},
        {"query": "如何做仓位管理？", "expected_topics": ["仓位管理", "凯利公式", "风险控制"]},
        {"query": "抄底和追涨哪个更好？", "expected_topics": ["抄底", "追涨", "策略对比"]},
    ],
}


async def run_eval_suite_by_category(
    cases: dict = None, limit: int = None, verbose: bool = True
) -> dict:
    """运行分类评测套件，返回各维度评分。

    Args:
        cases: 分类用例字典，None 则用 EVAL_SUITE_CASES
        limit: 每个类别最多运行的用例数，None 则全部运行
        verbose: 是否输出详细信息

    Returns:
        {"category_name": {"cases": [...], "avg_precision": ..., ...}, ...}
    """
    from agent.rag_evaluator import evaluate_rag_retrieval

    if cases is None:
        cases = EVAL_SUITE_CASES

    results = {}
    start_time = time.time()

    for category, case_list in cases.items():
        category_results = []
        run_list = case_list[:limit] if limit else case_list

        if verbose:
            print(f"\n{'='*50}")
            print(f"类别: {category} ({len(run_list)} 个用例)")
            print(f"{'='*50}")

        for i, case in enumerate(run_list):
            query = case["query"]
            if verbose:
                print(f"  [{i+1}/{len(run_list)}] {query}")

            try:
                eval_result = await evaluate_rag_retrieval(
                    query=query,
                    expected_topics=case.get("expected_topics", []),
                )
                category_results.append({
                    "query": query,
                    "precision": eval_result["precision"],
                    "recall": eval_result["recall"],
                    "mrr": eval_result["mrr"],
                    "ndcg": eval_result["ndcg"],
                })

                if verbose:
                    p = eval_result["precision"]
                    m = eval_result["mrr"]
                    n = eval_result["ndcg"]
                    emoji = "✅" if p >= 0.6 else ("⚠️" if p >= 0.4 else "❌")
                    print(f"    {emoji} P={p:.2f} MRR={m:.2f} NDCG={n:.2f}")

            except Exception as e:
                if verbose:
                    print(f"    ❌ 评估失败: {e}")
                category_results.append({
                    "query": query,
                    "precision": 0.0,
                    "recall": 0.0,
                    "mrr": 0.0,
                    "ndcg": 0.0,
                    "error": str(e),
                })

        if category_results:
            n = len(category_results)
            results[category] = {
                "cases": category_results,
                "count": n,
                "avg_precision": sum(r["precision"] for r in category_results) / n,
                "avg_recall": sum(r["recall"] for r in category_results) / n,
                "avg_mrr": sum(r["mrr"] for r in category_results) / n,
                "avg_ndcg": sum(r["ndcg"] for r in category_results) / n,
            }

            if verbose:
                s = results[category]
                print(f"  ── 汇总: P={s['avg_precision']:.3f} R={s['avg_recall']:.3f} "
                      f"MRR={s['avg_mrr']:.3f} NDCG={s['avg_ndcg']:.3f}")

    elapsed = time.time() - start_time

    # 全局汇总
    all_cases = []
    for cat_data in results.values():
        all_cases.extend(cat_data["cases"])

    total = len(all_cases)
    summary = {
        "total_cases": total,
        "elapsed_seconds": round(elapsed, 1),
    }
    if total > 0:
        summary["global_avg_precision"] = sum(r["precision"] for r in all_cases) / total
        summary["global_avg_recall"] = sum(r["recall"] for r in all_cases) / total
        summary["global_avg_mrr"] = sum(r["mrr"] for r in all_cases) / total
        summary["global_avg_ndcg"] = sum(r["ndcg"] for r in all_cases) / total

    results["_summary"] = summary

    if verbose:
        print(f"\n{'='*50}")
        print(f"全局汇总: {total} 个用例, 耗时 {summary['elapsed_seconds']}s")
        if "global_avg_precision" in summary:
            print(f"  Precision={summary['global_avg_precision']:.3f} "
                  f"Recall={summary['global_avg_recall']:.3f} "
                  f"MRR={summary['global_avg_mrr']:.3f} "
                  f"NDCG={summary['global_avg_ndcg']:.3f}")

    return results


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
    parser.add_argument("--suite", action="store_true",
                        help="运行完整分类评测套件（60+ 用例）")
    parser.add_argument("--limit", type=int, default=None,
                        help="--suite 模式下每个类别最多运行的用例数")
    parser.add_argument("--category", type=str, default=None,
                        help="--suite 模式下只运行指定类别")
    args = parser.parse_args()

    if args.suite:
        cases_dict = EVAL_SUITE_CASES
        if args.category:
            if args.category not in EVAL_SUITE_CASES:
                print(f"未知类别: {args.category}")
                print(f"可选类别: {', '.join(EVAL_SUITE_CASES.keys())}")
                sys.exit(1)
            cases_dict = {args.category: EVAL_SUITE_CASES[args.category]}

        result = asyncio.run(run_eval_suite_by_category(
            cases=cases_dict, limit=args.limit, verbose=not args.quiet
        ))
        save_results(result, args.output)
    else:
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
