"""估值数据利用监测 API — 暴露数据源命中、上下文注入、LLM 引用情况。

提供工具查询侧、上下文注入侧、LLM 消费侧三段可观测能力。
"""
from fastapi import APIRouter, Query

from services.valuation.valuation_monitor import (
    get_valuation_usage_report,
    get_conversation_valuation_usage,
)

router = APIRouter()


@router.get("/api/admin/valuation-usage/stats")
def valuation_usage_stats(days: int = Query(7, ge=1, le=90)):
    """获取估值数据利用整体统计。

    - 工具查询次数、数据源命中占比、缓存命中、失败次数
    - 上下文注入次数、平均覆盖指数、过期数据次数
    - LLM 引用率、幻觉风险数
    - 高风险列表 Top 20
    """
    return get_valuation_usage_report(days=days)


@router.get("/api/admin/valuation-usage/conversations")
def valuation_usage_conversations(
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(50, ge=1, le=200),
):
    """按对话聚合估值利用情况。"""
    from datetime import datetime, timedelta
    from db._conn import _get_conn

    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT
                conv_id,
                COUNT(*) as query_count,
                SUM(CASE WHEN cache_hit = 1 THEN 1 ELSE 0 END) as cache_hits,
                COUNT(DISTINCT final_source) as source_count,
                GROUP_CONCAT(DISTINCT final_source) as sources,
                MAX(created_at) as last_query_at
            FROM valuation_query_logs
            WHERE created_at >= ? AND conv_id IS NOT NULL
            GROUP BY conv_id
            ORDER BY last_query_at DESC
            LIMIT ?
        """, (since, limit)).fetchall()
        return {
            "days": days,
            "count": len(rows),
            "conversations": [dict(r) for r in rows],
        }
    finally:
        conn.close()


@router.get("/api/admin/valuation-usage/risks")
def valuation_usage_risks(
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(20, ge=1, le=100),
):
    """获取估值引用幻觉风险列表。"""
    from datetime import datetime, timedelta
    from db._conn import _get_conn

    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT
                trace_id,
                conv_id,
                message_id,
                agent_name,
                analysis_type,
                refs_found,
                has_hallucination_risk,
                confidence,
                sample_text,
                created_at
            FROM valuation_reference_check
            WHERE created_at >= ? AND has_hallucination_risk = 1
            ORDER BY created_at DESC
            LIMIT ?
        """, (since, limit)).fetchall()
        return {
            "days": days,
            "count": len(rows),
            "risks": [dict(r) for r in rows],
        }
    finally:
        conn.close()


@router.get("/api/admin/valuation-usage/conversations/{conv_id}")
def valuation_usage_conversation_detail(conv_id: int, limit: int = Query(100, ge=1, le=500)):
    """获取单个对话的估值利用详情。"""
    return get_conversation_valuation_usage(conv_id=conv_id, limit=limit)
