"""前瞻性事件雷达 — API 端点。

- POST /api/alerts/event-radar/scan：手动触发扫描
- GET /api/alerts/event-radar/events：事件列表（可按 status/relevance 过滤）
- GET /api/alerts/event-radar/events/{event_id}：事件详情
- POST /api/alerts/event-radar/verify：手动触发落地验证
- GET /api/alerts/event-radar/accuracy：准确率统计
- POST /api/alerts/event-radar/analyze-article：抓取文章并提取投资趋势
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Body

from db.market_events import (
    list_market_events, get_market_event,
)
from api.response import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["event-radar"])


@router.post("/api/alerts/event-radar/scan")
async def manual_scan():
    """手动触发前瞻事件雷达扫描。"""
    try:
        from services.event_radar import scan_forward_events
        result = scan_forward_events()
        return ApiResponse.success(data=result)
    except Exception as e:
        logger.error(f"手动触发事件雷达扫描失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"扫描失败: {e}")


@router.get("/api/alerts/event-radar/events")
async def list_events(
    status: Optional[str] = Query(None, description="按状态过滤：upcoming/imminent/materialized/expired"),
    relevance: Optional[str] = Query(None, description="按分级过滤：holding_impact/opportunity/market_watch"),
    limit: int = Query(50, ge=1, le=200),
):
    """查询事件列表。"""
    events = list_market_events(status=status, relevance=relevance, limit=limit)

    # Batch2 增强点 3：附加 effective_confidence 字段（开关控制）
    try:
        from services.event_radar import attach_effective_confidence
        attach_effective_confidence(events)
    except Exception as e:
        logger.warning(f"附加 effective_confidence 失败: {e}")

    conn = None
    last_scan_time = None
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        row = conn.execute(
            "SELECT MAX(detected_date) as last_scan FROM market_events"
        ).fetchone()
        if row and row["last_scan"]:
            last_scan_time = row["last_scan"]
    except Exception as e:
        logger.warning(f"获取上次扫描时间失败: {e}")
    finally:
        if conn:
            conn.close()

    return ApiResponse.success(data={"events": events, "total": len(events), "last_scan_time": last_scan_time})


@router.get("/api/alerts/event-radar/events/{event_id}")
async def get_event(event_id: str):
    """查询事件详情。"""
    event = get_market_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="事件不存在")

    # Batch2 增强点 3：附加 effective_confidence 字段（开关控制）
    try:
        from services.event_radar import attach_effective_confidence
        attach_effective_confidence(event)
    except Exception as e:
        logger.warning(f"附加 effective_confidence 失败: {e}")

    return ApiResponse.success(data=event)


@router.post("/api/alerts/event-radar/verify")
async def manual_verify():
    """手动触发事件落地验证（扫描已落地超过 T+N 的事件）。"""
    try:
        from services.event_radar import verify_materialized_events
        result = verify_materialized_events()
        return ApiResponse.success(data=result)
    except Exception as e:
        logger.error(f"手动触发事件验证失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"验证失败: {e}")


@router.get("/api/alerts/event-radar/accuracy")
async def accuracy_stats():
    """获取事件验证准确率统计（总体 + 分板块）。"""
    try:
        from services.event_radar import get_sector_accuracy_stats
        stats = get_sector_accuracy_stats()
        return ApiResponse.success(data=stats)
    except Exception as e:
        logger.error(f"获取准确率统计失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取失败: {e}")


@router.post("/api/alerts/event-radar/analyze-impact")
async def analyze_event_impact_api(event_id: str = Body(..., embed=True)):
    """LLM 深度解读事件影响（结合用户持仓）。

    - 开关：alerts.event_impact_analysis_enabled（默认 false）
    - 缓存：alerts.event_impact_analysis_cache_days（默认 7 天）
    - 失败时返回 error 字段，HTTP 仍 200，前端按 data.error 判断
    """
    if not event_id:
        raise HTTPException(status_code=400, detail="event_id 不能为空")
    try:
        from services.event_radar import analyze_event_impact
        result = analyze_event_impact(event_id)
        return ApiResponse.success(data=result)
    except Exception as e:
        logger.error(f"事件影响分析失败 event_id={event_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"分析失败: {e}")


@router.get("/api/alerts/event-radar/events/{event_id}/impact-amount")
async def estimate_impact_amount_api(event_id: str):
    """Batch2 增强点 2：实时估算事件对用户持仓的金额影响（纯计算，无 LLM）。

    - 开关：alerts.event_impact_amount_enabled（默认 false）
    - 公式：影响金额 = expected_impact_pct × holding_value / 100
    - 不缓存：每次调用实时计算（持仓会变化）
    - 失败时返回 reason 字段说明原因
    """
    try:
        from db.config import get_config_bool
        if not get_config_bool("alerts.event_impact_amount_enabled", False):
            return ApiResponse.success(data={
                "event_id": event_id,
                "total_impact_amount": 0.0,
                "affected_holdings": [],
                "reason": "事件影响金额估算开关未开启",
            })
    except Exception as e:
        logger.warning(f"检查 event_impact_amount 开关失败: {e}")

    event = get_market_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="事件不存在")

    try:
        from services.event_radar import estimate_event_impact_amount
        result = estimate_event_impact_amount(event)
        return ApiResponse.success(data=result)
    except Exception as e:
        logger.error(f"事件影响金额估算失败 event_id={event_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"估算失败: {e}")


@router.post("/api/alerts/event-radar/analyze-article")
async def analyze_article_trends(url: str = Body(..., embed=True)):
    """抓取文章并提取投资趋势。

    流程：
    1. 调用 services/article_reader.py 的 fetch_generic_article 抓取文章
    2. 调用 services/event_radar.py 的 _extract_trends_from_articles 提取趋势
    3. 将趋势写入 market_events 表（带 time_frame/evidence 字段）
    4. 返回提取的趋势列表
    """
    if not url or not url.strip().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="请提供合法的文章 URL（http/https）")
    try:
        from services.article_reader import fetch_generic_article
        from services.event_radar import _extract_trends_from_articles
        from db.market_events import create_market_event, get_market_event, _gen_event_id

        # 1. 抓取文章
        article = await fetch_generic_article(url)
        content = (article or {}).get("content_text", "") or ""
        title = (article or {}).get("title", "") or ""

        if len(content) < 500:
            raise HTTPException(
                status_code=400,
                detail=f"文章内容过短或抓取失败（{len(content)} 字符），无法提取趋势。标题：{title or '未知'}",
            )

        # 2. 提取趋势
        trends = _extract_trends_from_articles(content, title)

        # 3. 写入 market_events 表（幂等）
        saved_new = 0
        for trend in trends:
            try:
                eid = _gen_event_id(trend.get("title", ""), "")
                existing = get_market_event(eid)
                create_market_event(
                    title=trend.get("title", ""),
                    summary=trend.get("summary", ""),
                    event_type=trend.get("event_type", "theme"),
                    direction=trend.get("direction", "neutral"),
                    expected_date="",
                    affected_sectors=trend.get("affected_sectors", []),
                    affected_themes=trend.get("affected_themes", []),
                    confidence=float(trend.get("confidence", 0.5)),
                    sources=[{"title": title, "url": url}],
                    time_frame=trend.get("time_frame", ""),
                    evidence=trend.get("evidence", ""),
                )
                if not existing:
                    saved_new += 1
            except Exception as e:
                logger.warning(f"写入趋势事件失败 '{trend.get('title', '')}': {e}")

        logger.info(
            f"[event_radar] 文章趋势分析完成: url={url}, title={title}, "
            f"提取 {len(trends)} 个趋势，新增 {saved_new} 个"
        )

        return ApiResponse.success(data={
            "trends": trends,
            "total": len(trends),
            "new": saved_new,
            "article_title": title,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"分析文章趋势失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"分析失败: {e}")
