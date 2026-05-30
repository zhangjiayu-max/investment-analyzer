"""对话路由 — /api/conversation/* (规范化版本)

路径规范：
  - /api/conversation/list                    - 对话列表
  - /api/conversation/create                  - 创建对话
  - /api/conversation/{conv_id}               - 对话操作（删除）
  - /api/conversation/{conv_id}/messages      - 消息管理
  - /api/conversation/{conv_id}/cancel        - 取消对话
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from db import (
    create_conversation, list_conversations, get_conversation, delete_conversation,
    create_message, get_messages,
)

router = APIRouter(prefix="/api/conversation", tags=["conversation"])


class CreateConversationRequest(BaseModel):
    title: Optional[str] = None
    agent_id: Optional[int] = None


class CreateMessageRequest(BaseModel):
    content: str


@router.get("/list")
async def list_conversations_api(limit: int = 50):
    """列出对话。"""
    return {"conversations": list_conversations()}


@router.post("/create")
async def create_conversation_api(req: CreateConversationRequest):
    """创建对话。"""
    conv_id = create_conversation(
        title=req.title,
        agent_id=req.agent_id,
    )
    return {"ok": True, "id": conv_id}


@router.delete("/{conv_id}")
async def delete_conversation_api(conv_id: int):
    """删除对话。"""
    delete_conversation(conv_id)
    return {"ok": True}


@router.get("/{conv_id}/messages")
async def list_messages_api(conv_id: int, limit: int = 100):
    """列出对话消息。"""
    messages = get_messages(conv_id, limit)
    return {"messages": messages}


@router.post("/{conv_id}/messages")
async def create_message_api(conv_id: int, req: CreateMessageRequest):
    """创建消息（同步回复）。"""
    # 保存用户消息
    user_msg_id = create_message(conv_id, "user", req.content)

    # 获取对话信息
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")

    # 运行 Agent
    from agent.orchestrator import orchestrate
    agent_id = conv.get("agent_id")

    try:
        result = await orchestrate(
            query=req.content,
            conversation_id=conv_id,
            agent_id=agent_id,
        )

        # 保存助手消息
        assistant_msg_id = create_message(
            conv_id,
            "assistant",
            result.get("response", ""),
        )

        return {
            "ok": True,
            "user_message_id": user_msg_id,
            "assistant_message_id": assistant_msg_id,
            "response": result.get("response", ""),
        }
    except Exception as e:
        logging.error(f"Agent 运行失败: {e}")
        error_msg_id = create_message(conv_id, "assistant", f"抱歉，处理您的问题时出现错误：{str(e)}")
        raise HTTPException(500, f"Agent 运行失败: {str(e)}")


@router.post("/{conv_id}/cancel")
async def cancel_conversation_api(conv_id: int):
    """取消对话（停止正在运行的 Agent）。"""
    return {"ok": True}
