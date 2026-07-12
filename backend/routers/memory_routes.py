"""记忆 API 路由。"""

from fastapi import APIRouter, HTTPException, Body

from db import list_user_memory, get_user_memory, delete_user_memory
from services.user_memory_service import (
    build_context_for_conversation, save_user_memory,
    update_conversation_context, get_user_context_summary,
)

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/user")
async def get_user_memory_list(memory_type: str = None):
    memories = list_user_memory(memory_type=memory_type)
    return {"code": 0, "message": "ok", "data": memories}


@router.post("/user")
async def create_or_update_memory(
    memory_type: str = Body(...),
    content: dict = Body(...),
):
    memory_id = save_user_memory(memory_type, content)
    return {"ok": True, "memory_id": memory_id}


@router.get("/user/{memory_id}")
async def get_single_memory(memory_id: int):
    memory = get_user_memory(memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"code": 0, "message": "ok", "data": memory}


@router.delete("/user/{memory_id}")
async def remove_memory(memory_id: int):
    success = delete_user_memory(memory_id)
    if not success:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"ok": True}


@router.get("/context/{conversation_id}")
async def get_conversation_context_data(conversation_id: int):
    context = build_context_for_conversation(conversation_id)
    return {"code": 0, "message": "ok", "data": context}


@router.post("/context/{conversation_id}")
async def update_context_data(
    conversation_id: int,
    updates: dict = Body(...),
):
    success = update_conversation_context(conversation_id, updates)
    if not success:
        raise HTTPException(status_code=400, detail="更新失败")
    return {"ok": True}


@router.get("/context-summary")
async def get_summary():
    summary = get_user_context_summary()
    return {"code": 0, "message": "ok", "data": summary}
