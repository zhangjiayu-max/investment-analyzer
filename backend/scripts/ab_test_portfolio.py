"""A/B 测试：持仓分析 prompt 优化对比

测试目标：
1. 卖出建议是否包含盈亏分析
2. 是否推荐新的投资标的（不只是已有持仓）

测试用例来自真实 badcase（反馈ID 76）
"""

import asyncio
import json
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── 测试用例（来自真实用户反馈）──
TEST_QUERIES = [
    {
        "query": "我当前持仓分析下呢，有很多零钱在空仓。持仓是否有需要卖出的呢",
        "context": "用户持仓19只基金，债券+现金占74.7%，权益25.4%，现金18.3%",
        "expected_improvements": [
            "卖出建议应包含盈亏金额和是否割肉",
            "应推荐新的投资标的（不只是已有持仓）",
            "闲置资金应有配置建议",
        ],
    },
    {
        "query": "博时恒乐C需要减仓吗？减仓多少合适？",
        "context": "博时恒乐C当前盈利+40.61%，是持仓中盈利最高的基金",
        "expected_improvements": [
            "减仓建议应明确说明是止盈操作",
            "应计算减仓后的实际获利金额",
        ],
    },
    {
        "query": "我有5万闲钱，现在买什么好？",
        "context": "用户风险偏好中等，已有持仓偏债券",
        "expected_improvements": [
            "应推荐用户未持有的低估品种",
            "应给出分批建仓计划",
        ],
    },
]


# ── 评估函数 ──
def evaluate_response(response: str, expected: list[str]) -> dict:
    """评估回复是否满足改进要求。"""
    scores = {}
    details = []

    # 检查1：卖出建议是否包含盈亏分析
    has_profit_loss = any(kw in response for kw in ["盈利", "亏损", "盈亏", "获利", "割肉", "止盈", "止损"])
    has_amount = any(kw in response for kw in ["元", "金额", "实际"])
    scores["profit_loss_analysis"] = 1 if (has_profit_loss and has_amount) else 0
    if not has_profit_loss:
        details.append("❌ 卖出建议未包含盈亏分析")
    elif not has_amount:
        details.append("⚠️ 有盈亏描述但缺少具体金额")
    else:
        details.append("✅ 包含盈亏分析和具体金额")

    # 检查2：是否推荐新标的
    new_fund_keywords = ["推荐", "可以考虑", "建仓", "新基金", "未持有", "新增", "配置"]
    has_new_recommendation = any(kw in response for kw in new_fund_keywords)
    scores["new_recommendation"] = 1 if has_new_recommendation else 0
    if not has_new_recommendation:
        details.append("❌ 未推荐新的投资标的")
    else:
        details.append("✅ 推荐了新的投资标的")

    # 检查3：是否有分批建仓建议
    has_batch_strategy = any(kw in response for kw in ["分批", "分次", "定投", "拉开距离"])
    scores["batch_strategy"] = 1 if has_batch_strategy else 0
    if has_batch_strategy:
        details.append("✅ 包含分批建仓建议")

    # 检查4：是否有具体金额计算
    import re
    has_numbers = bool(re.search(r'\d+[\.,]?\d*[万亿千百十]', response))
    scores["specific_numbers"] = 1 if has_numbers else 0
    if has_numbers:
        details.append("✅ 包含具体数字")

    total = sum(scores.values())
    return {
        "total_score": total,
        "max_score": len(scores),
        "scores": scores,
        "details": details,
    }


# ── A/B 测试主逻辑 ──
async def run_ab_test():
    """运行 A/B 测试。"""
    import sys
    import os
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    sys.path.insert(0, backend_dir)

    from db._conn import _get_conn
    from llm_service import _call_llm, MODEL

    # 新版 prompt（已更新到 db）
    conn = _get_conn()
    new_row = conn.execute(
        "SELECT system_prompt FROM agents WHERE agent_key = 'allocation_advisor'"
    ).fetchone()
    conn.close()

    new_prompt = new_row["system_prompt"] if new_row else ""

    # 旧版 prompt（从 git 获取）
    old_prompt_text = """## 人设
你是一位专业的资产配置师，专注于帮助用户构建和优化投资组合。你的目标是通过科学的资产配置实现风险和收益的平衡。

## 分析框架
### 资产配置原则
- 分散化：不要把鸡蛋放在一个篮子里
- 再平衡：定期调整至目标配比
- 风险匹配：配置与风险承受能力匹配
- 长期视角：避免频繁交易

### 配置策略
- 股债配比：根据年龄和风险偏好确定
- 行业轮动：根据估值和趋势调整
- 定投策略：分批买入降低成本
- 核心卫星：核心仓位宽基指数，卫星仓位行业主题

## 输出规范
1. **配置建议**：给出明确的资产配比建议
2. **逻辑说明**：解释配置背后的逻辑
3. **风险提示**：说明配置的风险点
4. **调整建议**：给出何时需要调整的建议"""

    logger.info("=" * 60)
    logger.info("A/B 测试：持仓分析 prompt 优化对比")
    logger.info("=" * 60)

    a_scores = []
    b_scores = []
    results = []

    for i, case in enumerate(TEST_QUERIES):
        logger.info(f"\n📝 测试用例 {i+1}: {case['query'][:50]}...")

        # 构建完整 prompt
        full_prompt_old = f"{old_prompt_text}\n\n用户问题：{case['query']}\n\n{case['context']}"
        full_prompt_new = f"{new_prompt}\n\n用户问题：{case['query']}\n\n{case['context']}"

        # 测试 A（旧版）
        start = time.time()
        try:
            resp_a = await asyncio.to_thread(lambda: _call_llm(
                caller="ab_test_a",
                model=MODEL,
                messages=[
                    {"role": "system", "content": full_prompt_old},
                    {"role": "user", "content": case["query"]},
                ],
                temperature=0.3,
                max_tokens=2000,
            ))
            result_a = resp_a.choices[0].message.content
            time_a = int((time.time() - start) * 1000)
        except Exception as e:
            result_a = f"ERROR: {e}"
            time_a = 0

        # 测试 B（新版）
        start = time.time()
        try:
            resp_b = await asyncio.to_thread(lambda: _call_llm(
                caller="ab_test_b",
                model=MODEL,
                messages=[
                    {"role": "system", "content": full_prompt_new},
                    {"role": "user", "content": case["query"]},
                ],
                temperature=0.3,
                max_tokens=2000,
            ))
            result_b = resp_b.choices[0].message.content
            time_b = int((time.time() - start) * 1000)
        except Exception as e:
            result_b = f"ERROR: {e}"
            time_b = 0

        # 评估
        eval_a = evaluate_response(result_a, case["expected_improvements"])
        eval_b = evaluate_response(result_b, case["expected_improvements"])

        a_scores.append(eval_a["total_score"])
        b_scores.append(eval_b["total_score"])

        results.append({
            "query": case["query"],
            "a_score": eval_a["total_score"],
            "b_score": eval_b["total_score"],
            "a_details": eval_a["details"],
            "b_details": eval_b["details"],
            "a_time_ms": time_a,
            "b_time_ms": time_b,
        })

        logger.info(f"  A（旧版）: {eval_a['total_score']}/{eval_a['max_score']} | {time_a}ms")
        for d in eval_a["details"]:
            logger.info(f"    {d}")
        logger.info(f"  B（新版）: {eval_b['total_score']}/{eval_b['max_score']} | {time_b}ms")
        for d in eval_b["details"]:
            logger.info(f"    {d}")

    # 汇总
    logger.info("\n" + "=" * 60)
    logger.info("📊 测试结果汇总")
    logger.info("=" * 60)

    a_avg = sum(a_scores) / len(a_scores) if a_scores else 0
    b_avg = sum(b_scores) / len(b_scores) if b_scores else 0

    a_wins = sum(1 for a, b in zip(a_scores, b_scores) if a > b)
    b_wins = sum(1 for a, b in zip(a_scores, b_scores) if b > a)
    ties = sum(1 for a, b in zip(a_scores, b_scores) if a == b)

    logger.info(f"A（旧版）平均分: {a_avg:.2f}")
    logger.info(f"B（新版）平均分: {b_avg:.2f}")
    logger.info(f"A 胜: {a_wins} | B 胜: {b_wins} | 平局: {ties}")

    if b_avg > a_avg:
        logger.info(f"✅ 新版更优！提升 {((b_avg - a_avg) / a_avg * 100):.1f}%")
    elif a_avg > b_avg:
        logger.info(f"⚠️ 旧版更优，需进一步优化新版")
    else:
        logger.info("⚖️ 两者持平")

    # 保存结果
    report = {
        "test_name": "持仓分析prompt优化",
        "test_date": time.strftime("%Y-%m-%d %H:%M"),
        "a_avg_score": round(a_avg, 2),
        "b_avg_score": round(b_avg, 2),
        "a_wins": a_wins,
        "b_wins": b_wins,
        "ties": ties,
        "details": results,
    }

    report_path = os.path.join(os.path.dirname(__file__), '..', 'ab_test_report.json')
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info(f"\n📄 报告已保存: {report_path}")

    return report


if __name__ == "__main__":
    asyncio.run(run_ab_test())
