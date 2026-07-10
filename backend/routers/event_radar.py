"""前瞻性事件雷达 — API 端点。

- POST /api/alerts/event-radar/scan：手动触发扫描
- GET /api/alerts/event-radar/events：事件列表（可按 status/relevance 过滤）
- GET /api/alerts/event-radar/events/{event_id}：事件详情
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
    return ApiResponse.success(data={"events": events, "total": len(events)})


@router.get("/api/alerts/event-radar/events/{event_id}")
async def get_event(event_id: str):
    """查询事件详情。"""
    event = get_market_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="事件不存在")
    return ApiResponse.success(data=event)
