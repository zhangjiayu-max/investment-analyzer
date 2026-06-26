"""任务管理路由 — /api/tasks/*, /api/analyze, /api/analyze-image"""

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import OUTPUT_DIR
from db import create_task, update_task, get_task, list_tasks, delete_task
from article_reader import fetch_article, download_images, extract_stock_codes
from market_data import get_stock_info
from valuation import analyze_stock
from llm_service import analyze_article, analyze_images_batch
from image_parser import ImageParser

router = APIRouter(tags=["tasks"])


class CreateTaskRequest(BaseModel):
    url: str


async def _run_task(task_id: int, url: str):
    """后台异步执行：抓取 → 下载图片 → 提取代码 → 分析。"""
    try:
        update_task(task_id, status="fetching")
        article = await fetch_article(url)
        update_task(task_id,
            title=article["title"],
            author=article["author"],
            publish_time=article["publish_time"],
            content_text=article["content_text"],
        )

        images_dir = str(OUTPUT_DIR / str(task_id) / "images")
        local_images = await download_images(article["images"], images_dir)
        update_task(task_id, images_dir=images_dir, local_images=local_images)

        update_task(task_id, status="analyzing")
        codes = extract_stock_codes(article["content_text"])
        market_summary = {}
        for code in codes[:5]:
            try:
                info = get_stock_info(code)
                analysis = analyze_stock(code)
                market_summary[code] = {
                    "name": info.get("name", ""),
                    "pe": info.get("pe"),
                    "pb": info.get("pb"),
                    "recommendation": analysis.get("recommendation", ""),
                }
            except Exception as e:
                market_summary[code] = {"error": str(e)}

        update_task(task_id, codes_found=codes, market_data=market_summary)

        llm_result = analyze_article(
            title=article["title"],
            content=article["content_text"],
            market_data=json.dumps(market_summary, ensure_ascii=False, indent=2) if market_summary else None,
        )
        update_task(task_id, llm_analysis=llm_result, status="done")
    except Exception as e:
        update_task(task_id, status="error", error_msg=str(e))


@router.post("/api/tasks")
async def create_task_api(req: CreateTaskRequest):
    """创建任务，后台异步执行抓取+分析。"""
    task_id = create_task(req.url)
    asyncio.create_task(_run_task(task_id, req.url))
    return {"task_id": task_id, "status": "pending"}


@router.get("/api/tasks")
async def list_tasks_api(limit: int = 50):
    """任务列表。"""
    return {"tasks": list_tasks(limit)}


@router.get("/api/tasks/{task_id}")
async def get_task_api(task_id: int):
    """任务详情。"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@router.delete("/api/tasks/{task_id}")
async def delete_task_api(task_id: int):
    """删除任务。"""
    if not delete_task(task_id):
        raise HTTPException(404, "任务不存在")
    return {"ok": True}


@router.get("/api/tasks/{task_id}/images")
async def get_task_images(task_id: int):
    """获取任务图片列表（本地路径 + URL）。"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    local_images = task.get("local_images") or []
    images = []
    for path in local_images:
        filename = Path(path).name
        images.append({
            "local_path": path,
            "url": f"/static/tasks/{task_id}/images/{filename}",
        })
    return {"images": images}


@router.post("/api/analyze")
async def analyze_compat(req: CreateTaskRequest):
    """兼容旧接口，创建任务并等待完成返回结果。"""
    task_id = create_task(req.url)
    await _run_task(task_id, req.url)
    return get_task(task_id)


@router.post("/api/tasks/{task_id}/analyze-images")
async def analyze_task_images(task_id: int):
    """分析任务中的所有图片，提取结构化数据。"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    local_images = task.get("local_images") or []
    if not local_images:
        raise HTTPException(400, "该任务没有图片")
    results = analyze_images_batch(local_images)
    return {"results": results}


@router.post("/api/analyze-image")
async def analyze_single_image(body: dict):
    """分析单张图片（传本地路径）。"""
    path = body.get("path")
    if not path or not Path(path).exists():
        raise HTTPException(400, "图片路径无效")
    parser = ImageParser()
    result = parser.parse(path)
    return result
