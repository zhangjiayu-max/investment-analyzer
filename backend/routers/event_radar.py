"""前瞻性事件雷达 — API 端点。

- POST /api/alerts/event-radar/scan：手动触发扫描
- GET /api/alerts/event-radar/events：事件列表（可按 status/relevance 过滤）
- GET /api/alerts/event-radar/events/{event_id}：事件详情
- POST /api/alerts/event-radar/verify：手动触发落地验证
- GET /api/alerts/event-radar/accuracy：准确率统计
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

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
