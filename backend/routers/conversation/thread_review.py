"""对话复盘路由 — /api/thread-review/*"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import asyncio
import json
import logging

from db import get_running_async_task, create_async_task, update_async_task
from db.thread_summaries import create_thread_summary, list_thread_summaries

logger = logging.getLogger(__name__)
router = APIRouter()


class ThreadReviewRequest(BaseModel):
    user_id: str = "default"
    thread_id: str
    messages: list  # conversation messages for context


def _thread_review_worker(req: ThreadReviewRequest) -> dict:
    """同步：构建 prompt + LLM 调用 + 落库。"""
    from services.llm_service import _call_llm, MODEL_AUX
    from db.config import get_config_float, get_config_int

    # Build prompt from messages
    context = "\n".join([f"{m.get('role','user')}: {m.get('content','')}" for m in req.messages[-20:]])

    prompt = f"""请分析以下投资对话，提取：
1. 关键决策（做了什么决定）
2. 讨论的持仓/标的
3. 未解决的问题

对话内容：
{context}

请用JSON格式返回：
{{"summary": "...", "key_decisions": ["决策1", ...], "positions_discussed": ["标的1", ...], "unresolved_questions": ["问题1", ...]}}
"""

    response = _call_llm(
        caller="thread_review",
        model=MODEL_AUX,
        messages=[
            {"role": "system", "content": "你是一个投资对话分析助手，擅长提炼关键信息。"},
            {"role": "user", "content": prompt},
        ],
        temperature=get_config_float("llm.temperature_analysis", 0.3),
        max_tokens=get_config_int("llm.max_tokens_analysis", 4096),
    )
    result = response.choices[0].message.content or ""

    # Try to parse JSON from result
    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(result[start:end])
        else:
            parsed = {"summary": result, "key_decisions": [], "positions_discussed": [], "unresolved_questions": []}
    except json.JSONDecodeError:
        parsed = {"summary": result, "key_decisions": [], "positions_discussed": [], "unresolved_questions": []}

    summary_id = create_thread_summary(
        user_id=req.user_id,
        thread_id=req.thread_id,
        summary=parsed.get("summary", ""),
        key_decisions=parsed.get("key_decisions", []),
        positions_discussed=parsed.get("positions_discussed", []),
        unresolved_questions=parsed.get("unresolved_questions", []),
        summary_type="auto"
    )
    return {"id": summary_id, **parsed}


@router.post("/api/thread-review/generate")
async def generate_thread_review(req: ThreadReviewRequest):
    """Generate AI summary of a conversation thread（异步执行）。"""
    # 幂等保护：已有 running 任务时直接返回该 task_id
    existing = get_running_async_task("thread_review")
    if existing:
        return {"task_id": existing["id"], "status": "running", "reused": True}

    task_id = create_async_task("thread_review", caller="thread_review")

    async def _run():
        try:
            result = await asyncio.to_thread(_thread_review_worker, req)
            update_async_task(task_id, status="done", result=result)
        except Exception as e:
            logger.error(f"thread review 失败: {e}", exc_info=True)
            update_async_task(task_id, status="error", error_msg=str(e))

    asyncio.create_task(_run())
    return {"task_id": task_id, "status": "running"}


@router.get("/api/thread-review/list")
async def get_thread_reviews(user_id: str = "default", limit: int = 20):
    return list_thread_summaries(user_id, limit)
