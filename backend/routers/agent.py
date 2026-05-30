"""Agent 管理路由 — /api/agent/* (规范化版本)

路径规范：
  - /api/agent/list                    - Agent 列表
  - /api/agent/create                  - 创建 Agent
  - /api/agent/{agent_id}              - Agent 操作（获取/更新/删除）
  - /api/agent/{agent_id}/versions     - 版本历史
  - /api/agent/generate-prompt         - 生成 Prompt
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from db import (
    list_agents, get_agent, create_agent as db_create_agent, update_agent, delete_agent,
    save_prompt_version, list_prompt_versions, get_prompt_version,
)

router = APIRouter(prefix="/api/agent", tags=["agent"])


class CreateAgentRequest(BaseModel):
    name: str
    system_prompt: str
    description: str = ""
    knowledge_scope: str = ""
    icon: str = "robot"


class UpdateAgentRequest(BaseModel):
    name: Optional[str] = None
    system_prompt: Optional[str] = None
    description: Optional[str] = None
    knowledge_scope: Optional[str] = None
    icon: Optional[str] = None


class GeneratePromptRequest(BaseModel):
    name: str
    description: str


@router.get("/list")
async def list_agents_api():
    """Agent 列表。"""
    return {"agents": list_agents()}


@router.post("/create")
async def create_agent_api(req: CreateAgentRequest):
    """创建 Agent。"""
    agent_id = db_create_agent(
        name=req.name,
        system_prompt=req.system_prompt,
        description=req.description,
        knowledge_scope=req.knowledge_scope,
        icon=req.icon,
    )
    return {"ok": True, "id": agent_id}


@router.get("/{agent_id}")
async def get_agent_api(agent_id: int):
    """获取单个 Agent。"""
    agent = get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent 不存在")
    return agent


@router.put("/{agent_id}")
async def update_agent_api(agent_id: int, req: UpdateAgentRequest):
    """更新 Agent。"""
    update_agent(agent_id, **req.dict(exclude_unset=True))
    return {"ok": True}


@router.delete("/{agent_id}")
async def delete_agent_api(agent_id: int):
    """删除 Agent。"""
    delete_agent(agent_id)
    return {"ok": True}


@router.get("/{agent_id}/versions")
async def list_versions_api(agent_id: int, agent_type: str = "conversation"):
    """列出 Agent 版本历史。"""
    versions = list_prompt_versions(agent_id, agent_type)
    return {"versions": versions}


@router.post("/{agent_id}/rollback/{version_id}")
async def rollback_version_api(agent_id: int, version_id: int, agent_type: str = "conversation"):
    """回滚到指定版本。"""
    version = get_prompt_version(version_id)
    if not version:
        raise HTTPException(404, "版本不存在")

    update_agent(agent_id, system_prompt=version["system_prompt"])
    return {"ok": True}


@router.post("/generate-prompt")
async def generate_prompt_api(req: GeneratePromptRequest):
    """AI 生成 Agent 提示词。"""
    from llm_service import _call_llm

    prompt = f"""你是一位 AI 提示词工程专家，专门为投资分析领域的 Agent 编写系统提示词。

## 任务
请为以下 Agent 生成系统提示词：

**Agent 名称**：{req.name}
**功能描述**：{req.description}

## 编写规范
一个高质量的 Agent 提示词必须包含以下部分：

1. **人设**：清晰的角色定义（经验年限、专注领域、专业背景）
2. **分析框架**：具体的方法论，带数值阈值
3. **输出规范**：明确的格式要求（结论先行、数据支撑、风险提示等）
4. **思维链**：分步推理流程
5. **知识边界**：能力范围声明

请直接输出完整的系统提示词，不要添加额外说明。"""

    try:
        result = _call_llm(prompt, temperature=0.7, max_tokens=2000)
        return {"ok": True, "prompt": result}
    except Exception as e:
        logging.error(f"生成提示词失败: {e}")
        raise HTTPException(500, f"生成失败: {str(e)}")
