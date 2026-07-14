"""
多跳检索（Multi-hop RAG）— 升级五。

预定义多跳模板：根据查询关键词识别多跳意图，逐跳执行检索并将结果累积为上下文。

例如：
  - 持仓 → 基金 → 基金经理 → 经理其他基金
  - 持仓 → 基金 → 行业配置 → 行业风险
  - 指数 → 成分股 → 个股估值

每跳的输出作为下一跳的输入或上下文，最终汇总为完整的多跳上下文。
"""

import logging
import re
from typing import Callable

logger = logging.getLogger(__name__)

# 单跳最大结果数
HOP_LIMIT = 3
# 总跳数上限
MAX_HOPS = 4


def _match(patterns: list[str], text: str) -> bool:
    """任一正则命中即返回 True。"""
    return any(re.search(p, text) for p in patterns)


# ── 多跳模板 ────────────────────────────────────────────────
# 每个模板：name, match_fn, hops = [hop_name, hop_runner]
# hop_runner(ctx) -> str  返回该跳的检索文本，更新 ctx


def _template_holding_to_manager(query: str) -> bool:
    return _match([
        r"基金经理|基金管理人|经理.*基金|谁管.*基金",
        r"持仓.*经理|经理.*持仓",
    ], query)


def _template_holding_to_industry(query: str) -> bool:
    return _match([
        r"行业配置|行业分布|行业.*持仓|持仓.*行业",
        r"行业.*风险|集中度",
    ], query)


def _template_index_to_stock_valuation(query: str) -> bool:
    return _match([
        r"指数.*成分|成分股.*估值|指数.*估值",
        r"沪深300|中证500|创业板.*估值",
    ], query)


MULTI_HOP_TEMPLATES = [
    {
        "name": "holding→fund→manager",
        "match": _template_holding_to_manager,
        "description": "持仓 → 基金 → 基金经理",
    },
    {
        "name": "holding→fund→industry",
        "match": _template_holding_to_industry,
        "description": "持仓 → 基金 → 行业配置",
    },
    {
        "name": "index→stock→valuation",
        "match": _template_index_to_stock_valuation,
        "description": "指数 → 成分股 → 个股估值",
    },
]


def detect_multi_hop(query: str) -> dict | None:
    """检测查询是否需要多跳检索，返回模板信息或 None。"""
    for tpl in MULTI_HOP_TEMPLATES:
        try:
            if tpl["match"](query):
                return tpl
        except Exception as e:
            logger.debug(f"模板匹配失败 {tpl['name']}: {e}")
    return None


def run_multi_hop(
    query: str,
    template: dict,
    rag_search_fn: Callable[[str, int], tuple[list[dict], int]],
    portfolio_fn: Callable[[], list[dict]] | None = None,
) -> dict:
    """
    执行多跳检索。

    Args:
        query: 原始查询
        template: 检测到的多跳模板
        rag_search_fn: RAG 检索函数 (query, limit) -> (results, count)
        portfolio_fn: 获取持仓列表的函数（可选，用于持仓→基金的跳转）

    Returns:
        {
            "hops": [{"name": str, "results": list, "count": int}, ...],
            "context": str,  # 累积的多跳上下文
            "template": str,
        }
    """
    hops_result: list[dict] = []
    context_parts: list[str] = []
    accumulated_query = query

    tpl_name = template.get("name", "")

    # ── 跳1：从持仓提取基金代码 ──────────────────────
    if tpl_name.startswith("holding→") and portfolio_fn:
        try:
            holdings = portfolio_fn() or []
            fund_codes = [h.get("fund_code") for h in holdings if h.get("fund_code")]
            if fund_codes:
                hop_ctx = f"用户持仓基金：{', '.join(fund_codes[:10])}"
                context_parts.append(f"[Hop1-持仓] {hop_ctx}")
                hops_result.append({"name": "持仓→基金", "results": [], "count": len(fund_codes), "context": hop_ctx})
                # 后续跳以基金代码作为查询
                accumulated_query = " ".join(fund_codes[:5])
            else:
                hops_result.append({"name": "持仓→基金", "results": [], "count": 0, "context": "无持仓"})
        except Exception as e:
            logger.warning(f"多跳 Hop1 持仓查询失败: {e}")
            hops_result.append({"name": "持仓→基金", "results": [], "count": 0, "context": f"查询失败: {e}"})

    # ── 中间跳：RAG 检索 ──────────────────────
    try:
        results, count = rag_search_fn(accumulated_query, HOP_LIMIT)
        hop_ctx = "\n".join(
            f"- {r.get('title', '')}: {(r.get('content') or '')[:150]}"
            for r in results[:HOP_LIMIT]
        )
        context_parts.append(f"[Hop2-知识检索] {hop_ctx}")
        hops_result.append({"name": "知识检索", "results": results[:HOP_LIMIT], "count": count, "context": hop_ctx})
    except Exception as e:
        logger.warning(f"多跳 RAG 检索失败: {e}")
        hops_result.append({"name": "知识检索", "results": [], "count": 0, "context": f"检索失败: {e}"})

    # ── 末跳：基于累积上下文再检索一次原查询 ──────────────────────
    if len(hops_result) < MAX_HOPS:
        try:
            # 用原查询 + 累积上下文关键词再检索
            final_query = f"{query} {accumulated_query}"
            results, count = rag_search_fn(final_query, HOP_LIMIT)
            hop_ctx = "\n".join(
                f"- {r.get('title', '')}: {(r.get('content') or '')[:150]}"
                for r in results[:HOP_LIMIT]
            )
            context_parts.append(f"[Hop3-综合检索] {hop_ctx}")
            hops_result.append({"name": "综合检索", "results": results[:HOP_LIMIT], "count": count, "context": hop_ctx})
        except Exception as e:
            logger.warning(f"多跳末跳检索失败: {e}")

    return {
        "hops": hops_result,
        "context": "\n\n".join(context_parts),
        "template": tpl_name,
    }


def multi_hop_search(query: str, rag_search_fn, portfolio_fn=None) -> dict | None:
    """
    入口：检测多跳意图并执行。非多跳查询返回 None。

    rag_search_fn: (query, limit) -> (results, count)
    portfolio_fn: () -> list[dict]  持仓列表
    """
    tpl = detect_multi_hop(query)
    if not tpl:
        return None

    logger.info(f"[multi_hop] 检测到多跳模板：{tpl['name']}")
    try:
        return run_multi_hop(query, tpl, rag_search_fn, portfolio_fn)
    except Exception as e:
        logger.warning(f"多跳检索执行失败（回退到单跳）: {e}")
        return None
