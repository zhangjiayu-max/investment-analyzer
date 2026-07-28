"""SSE 通知端点（P1-7: 通知携带数据）。

通知 payload 结构：
    {
        "title": str,
        "message": str,
        "type": "alert"|"news"|"valuation"|"decision"|"system",
        "category": str,            # 可选，子分类（如 watchlist_signal_change）
        "data": {                   # 可选，携带详情，前端可直接展示无需二次拉取
            "alert_id": int,
            "alert_type": "danger"|"warning"|"info",
            "fund_code": str,
            "fund_name": str,
            "metric": str,
            "current_value": float,
            "threshold": float,
            "snapshot": dict,
            "news_summary": str,    # 自动截断到 NEWS_SUMMARY_MAX 字
            "action_url": str,      # 前端跳转页 key
        },
        "timestamp": str            # ISO 8601
    }

约束：
    - data 字段可选，向后兼容（旧客户端忽略 data）
    - 整个 payload 序列化后不超过 MAX_PAYLOAD_SIZE（4KB），超限按优先级精简 data
"""
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import asyncio
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

# 通知 payload 序列化后最大字节数（避免 SSE 帧过大）
MAX_PAYLOAD_SIZE = 4096
# news_summary 最大字符数
NEWS_SUMMARY_MAX = 200

# 通知大类（type 字段取值）
VALID_NOTIFY_TYPES = {"alert", "news", "valuation", "decision", "system"}

subscriptions: list[asyncio.Queue] = []


async def notify_subscribers(message: dict) -> None:
    """将消息推送到所有订阅者队列（低层函数，message 应已标准化）。"""
    for queue in subscriptions[:]:
        try:
            await queue.put(message)
        except Exception:
            try:
                subscriptions.remove(queue)
            except ValueError:
                pass


def _truncate_news_summary(data: dict) -> None:
    """就地截断 data['news_summary'] 到 NEWS_SUMMARY_MAX 字。"""
    summary = data.get("news_summary")
    if isinstance(summary, str) and len(summary) > NEWS_SUMMARY_MAX:
        data["news_summary"] = summary[:NEWS_SUMMARY_MAX] + "…"


def _enforce_payload_size(payload: dict) -> dict:
    """payload 序列化超限時，按优先级精简 data 字段。"""
    try:
        if len(json.dumps(payload, ensure_ascii=False)) <= MAX_PAYLOAD_SIZE:
            return payload
    except (TypeError, ValueError):
        payload["data"] = {"_truncated": True}
        return payload

    data = dict(payload.get("data") or {})
    # 按优先级从低到高移除大字段
    for key in ("snapshot", "related_news", "news_summary", "signal_reason", "metric"):
        if key in data:
            data.pop(key)
            payload["data"] = data
            try:
                if len(json.dumps(payload, ensure_ascii=False)) <= MAX_PAYLOAD_SIZE:
                    return payload
            except (TypeError, ValueError):
                break
    # 仍然超限：仅保留标识字段
    minimal = {
        k: data[k] for k in ("alert_id", "fund_code", "fund_name", "alert_type", "action_url")
        if k in data
    }
    minimal["_truncated"] = True
    payload["data"] = minimal
    return payload


def build_notification(title: str, message: str,
                       notify_type: str = "system",
                       category: Optional[str] = None,
                       data: Optional[dict] = None) -> dict:
    """构造标准化通知 payload（P1-7: 携带数据）。

    Args:
        title: 通知标题
        message: 通知正文
        notify_type: 通知大类 alert|news|valuation|decision|system
        category: 可选子分类（如 watchlist_signal_change）
        data: 可选详情字典；news_summary 会被截断到 200 字

    Returns:
        标准化 payload dict，timestamp 为 ISO 8601 字符串
    """
    if notify_type not in VALID_NOTIFY_TYPES:
        notify_type = "system"
    payload_data = dict(data or {})
    _truncate_news_summary(payload_data)
    payload = {
        "title": str(title),
        "message": str(message),
        "type": notify_type,
        "data": payload_data,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    if category:
        payload["category"] = category
    return _enforce_payload_size(payload)


async def broadcast_notification(title: str, message: str,
                                 notify_type: str = "system",
                                 category: Optional[str] = None,
                                 data: Optional[dict] = None) -> dict:
    """构造并广播通知到所有订阅者（P1-7: 携带数据）。

    供后端服务直接调用（无需经过 HTTP 端点）。
    """
    payload = build_notification(title, message, notify_type, category, data)
    await notify_subscribers(payload)
    return payload


class PushRequest(BaseModel):
    """HTTP /push 请求体（与前端 api.post JSON body 对齐）。"""
    title: str
    message: str
    type: str = "system"
    category: Optional[str] = None
    data: Optional[dict] = None


@router.get("/stream")
async def stream_notifications():
    """SSE 推送端点：实时下发标准化通知 payload（含 data 字段）。"""
    queue: asyncio.Queue = asyncio.Queue()
    subscriptions.append(queue)

    async def event_generator():
        try:
            while True:
                message = await queue.get()
                yield f"data: {json.dumps(message, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            try:
                subscriptions.remove(queue)
            except ValueError:
                pass
            raise

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/push")
async def push_notification(req: PushRequest):
    """HTTP 推送端点（手动测试/外部调用）：构造标准化通知并广播。"""
    payload = build_notification(req.title, req.message, req.type, req.category, req.data)
    await notify_subscribers(payload)
    return {"ok": True, "notification": payload}


@router.get("/subscribers")
async def get_subscriber_count():
    return {"count": len(subscriptions)}
