from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
import asyncio
import json
from typing import Optional

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

subscriptions = []

async def notify_subscribers(message):
    for queue in subscriptions[:]:
        try:
            await queue.put(message)
        except:
            subscriptions.remove(queue)

@router.get("/stream")
async def stream_notifications():
    queue = asyncio.Queue()
    subscriptions.append(queue)

    async def event_generator():
        try:
            while True:
                message = await queue.get()
                yield f"data: {json.dumps(message)}\n\n"
        except asyncio.CancelledError:
            subscriptions.remove(queue)
            raise

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@router.post("/push")
async def push_notification(title: str, message: str, type: str = 'info', data: Optional[dict] = None):
    notification = {
        'title': title,
        'message': message,
        'type': type,
        'data': data or {},
        'timestamp': asyncio.get_event_loop().time()
    }
    await notify_subscribers(notification)
    return {"ok": True, "notification": notification}

@router.get("/subscribers")
async def get_subscriber_count():
    return {"count": len(subscriptions)}
