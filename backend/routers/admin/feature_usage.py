"""功能使用埋点路由 — /api/feature-usage/*

前端上报页面访问/功能点击行为，后端记录并聚合统计。
"""
import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from db.config import get_config
from db.feature_usage import (
    track_feature_usage, batch_track_feature_usage, get_feature_usage_stats,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feature-usage", tags=["feature-usage"])


class TrackEvent(BaseModel):
    """单条埋点事件。"""
    page_key: str
    feature_key: str = None
    action_type: str
    duration_ms: int = None
    referrer_page: str = None


class BatchTrack(BaseModel):
    """批量埋点事件。"""
    events: list[TrackEvent]


def _is_enabled() -> bool:
    """检查埋点开关是否开启。"""
    return get_config("tracking.feature_usage_enabled", "true") == "true"


@router.post("/track")
async def track(req: Request, event: TrackEvent):
    """单条上报。"""
    if not _is_enabled():
        return {"ok": False, "message": "埋点已关闭"}
    # 从 header 获取 session_id，缺失则标记为匿名
    session_id = req.headers.get("X-Session-Id") or "anonymous"
    try:
        track_feature_usage(
            session_id, event.page_key, event.action_type,
            event.feature_key, event.duration_ms, event.referrer_page,
        )
    except Exception as e:
        logger.warning(f"埋点记录失败: {e}")
        return {"ok": False, "message": str(e)}
    return {"ok": True}


@router.post("/track-batch")
async def track_batch(req: Request, body: BatchTrack):
    """批量上报。"""
    if not _is_enabled():
        return {"ok": False, "message": "埋点已关闭"}
    session_id = req.headers.get("X-Session-Id") or "anonymous"
    events = [{**e.model_dump(), "session_id": session_id} for e in body.events]
    try:
        count = batch_track_feature_usage(events)
    except Exception as e:
        logger.warning(f"批量埋点记录失败: {e}")
        return {"ok": False, "message": str(e)}
    return {"ok": True, "count": count}


@router.get("/stats")
async def stats(days: int = 30):
    """聚合统计。"""
    return get_feature_usage_stats(days)
