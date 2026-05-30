"""任务路由 — /api/task/* (规范化版本)

路径规范：
  - /api/task/list                    - 任务列表
  - /api/task/create                  - 创建任务
  - /api/task/{task_id}               - 任务操作（获取/删除）
  - /api/task/{task_id}/images        - 任务图片
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import (
    create_task, get_task, list_tasks, delete_task,
)

router = APIRouter(prefix="/api/task", tags=["task"])


class CreateTaskRequest(BaseModel):
    url: str


@router.post("/create")
async def create_task_api(req: CreateTaskRequest):
    """创建任务（提交链接）。"""
    task_id = create_task(req.url)
    return {"ok": True, "id": task_id}


@router.get("/list")
async def list_tasks_api(limit: int = 50):
    """任务列表。"""
    return {"tasks": list_tasks(limit)}


@router.get("/{task_id}")
async def get_task_api(task_id: int):
    """获取任务详情。"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@router.delete("/{task_id}")
async def delete_task_api(task_id: int):
    """删除任务。"""
    delete_task(task_id)
    return {"ok": True}


@router.get("/{task_id}/images")
async def get_task_images_api(task_id: int):
    """获取任务图片。"""
    images = get_task_images(task_id)
    return {"images": images}
