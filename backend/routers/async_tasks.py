"""异步任务状态查询路由 — /api/async-tasks/*"""

import json
import logging

from fastapi import APIRouter, HTTPException

from db import get_async_task, list_async_tasks

logger = logging.getLogger(__name__)

router = APIRouter(tags=["async-tasks"])


@router.get("/api/async-tasks/{task_id}/status")
async def get_async_task_status(task_id: int):
    """查询异步任务状态。"""
    task = get_async_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    result = None
    if task.get("result"):
        try:
            result = json.loads(task["result"])
        except Exception:
            result = task["result"]
    return {
        "task_id": task_id,
        "status": task.get("status", "running"),
        "result": result,
        "error": task.get("error_msg", ""),
        "token_usage": task.get("token_usage", 0),
        "created_at": task.get("created_at", ""),
    }


@router.get("/api/async-tasks")
async def list_async_tasks_api(task_type: str = "", status: str = "", limit: int = 50):
    """列出异步任务。"""
    tasks = list_async_tasks(
        task_type=task_type or None,
        status=status or None,
        limit=limit,
    )
    # 反序列化 result
    for t in tasks:
        if t.get("result"):
            try:
                t["result"] = json.loads(t["result"])
            except Exception:
                pass
    return {"tasks": tasks}
