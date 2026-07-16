"""Agent 管理路由 — /api/agents/*, /api/analysis-agents/*/versions, /api/analysis-agents/*/rollback"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from db import (
    list_agents, get_agent, create_agent as db_create_agent, update_agent, delete_agent,
    save_prompt_version, list_prompt_versions, get_prompt_version,
    get_analysis_agent, update_analysis_agent,
    get_config_int,
)
from models.agents import CreateAgentRequest, UpdateAgentRequest, GeneratePromptRequest

router = APIRouter(tags=["agents"])

# 可用工具列表（供 AI 生成提示词时参考）
AVAILABLE_TOOLS_DESC = """
- query_valuation: 查询指定指数的估值数据（PE、PB、百分位、z-score）
- search_knowledge: 从知识库检索相关文章、分析记录
- get_bond_temperature: 获取当前债市温度
- get_valuation_list: 获取所有指数估值概览，可筛选低估/高估
- calculate_metrics: 计算投资指标（定投收益率、年化收益、最大回撤、风险等级）
- web_search: 搜索最新财经新闻和市场资讯
- query_portfolio: 查询用户持仓信息
- query_fund_info: 查询基金详细信息
"""

PROMPT_GENERATOR_META = """你是一位 AI 提示词工程专家，专门为投资分析领域的 Agent 编写系统提示词。

## 编写规范
一个高质量的 Agent 提示词必须包含以下部分：

1. **人设**：清晰的角色定义（经验年限、专注领域、专业背景）
2. **分析框架**：具体的方法论，带数值阈值（如"百分位 <20% 为深度低估"）
3. **输出规范**：明确的格式要求（结论先行、数据支撑、风险提示等）
4. **思维链**：分步推理流程（理解诉求→检索数据→分析→结论→标注置信度）
5. **知识边界**：能力范围声明（擅长什么、不擅长什么、超出范围怎么处理）
6. **Few-shot 示例**：一个好的回答样例（让模型知道期望的输出质量）
7. **负面约束**：明确列出不要做的事（如"不要给出具体买卖时点"、"不要编造数据"）

## 注意事项
- 使用 Markdown 标题层级（## / ###），结构清晰
- 数值判断标准必须具体（如百分位区间、z-score 阈值）
- 语气专业但不晦涩，面向普通投资者
- 篇幅控制在 300-600 字，不要太长
"""


@router.get("/api/agents")
async def list_agents_api():
    """列出所有 Agent。"""
    return {"agents": list_agents()}


@router.post("/api/agents")
async def create_agent_api(req: CreateAgentRequest):
    """创建自定义 Agent。"""
    agent_id = db_create_agent(
        name=req.name, system_prompt=req.system_prompt,
        description=req.description, knowledge_scope=req.knowledge_scope, icon=req.icon,
    )
    return {"ok": True, "agent_id": agent_id}


@router.get("/api/agents/{agent_id}")
async def get_agent_api(agent_id: int):
    """获取单个 Agent 详情。"""
    agent = get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent 不存在")
    return agent


@router.put("/api/agents/{agent_id}")
async def update_agent_api(agent_id: int, req: UpdateAgentRequest):
    """更新 Agent 信息。修改提示词时自动保存版本历史，并触发回归测试。"""
    agent = get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent 不存在")
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    prompt_changed = False
    if 'system_prompt' in fields and fields['system_prompt'] != agent.get('system_prompt'):
        save_prompt_version(agent_id, 'conversation', agent['system_prompt'])
        prompt_changed = True
    if fields:
        update_agent(agent_id, **fields)
    # 如果更新的是编排专家，清除缓存使新 prompt 立即生效
    if agent.get("is_specialist"):
        from db.agents import clear_specialist_cache
        clear_specialist_cache()
    # prompt 变更后触发回归测试
    regression_warning = None
    if prompt_changed:
        try:
            from agent.regression import run_regression_tests
            agent_type = "specialist" if agent.get("is_specialist") else "conversation"
            asyncio.create_task(run_regression_tests(agent_id, agent_type))
            regression_warning = "regression_running"
        except Exception as e:
            logging.warning(f"触发回归测试失败: {e}")
            regression_warning = f"regression_trigger_failed: {e}"

        # 检查上一轮回归结果（如果有），退步超阈值则创建 improvement_task
        try:
            from agent.regression import get_regression_result
            from db.eval import create_improvement_task
            prev = get_regression_result(agent_id, agent_type)
            if prev and prev.get("status") == "completed":
                summary = prev.get("summary", {})
                total = summary.get("total", 0)
                degraded = summary.get("degraded", 0)
                if total > 0 and degraded / total > 0.3:
                    create_improvement_task(
                        source_type="regression_degraded",
                        source_id=agent_id,
                        agent_type=agent_type,
                        root_cause=f"Prompt 变更后回归测试退步: {degraded}/{total} cases degraded",
                        suggestion="请人工确认 prompt 变更，必要时回滚到上一版本",
                    )
                    regression_warning = f"regression_degraded: {degraded}/{total}"
        except Exception as e:
            logging.debug(f"回归历史检查失败: {e}")

    return {"ok": True, "regression_warning": regression_warning}


@router.delete("/api/agents/{agent_id}")
async def delete_agent_api(agent_id: int):
    """删除自定义 Agent（预设 Agent 不可删除）。"""
    agent = get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent 不存在")
    if agent.get("is_preset"):
        raise HTTPException(400, "预设 Agent 不可删除")
    delete_agent(agent_id)
    return {"ok": True}


@router.post("/api/agents/generate-prompt")
async def generate_prompt_api(req: GeneratePromptRequest):
    """AI 辅助生成或优化 Agent 系统提示词。"""
    if req.mode == "optimize" and not req.current_prompt:
        raise HTTPException(400, "优化模式需要提供 current_prompt")

    if req.mode == "optimize":
        user_content = f"""请优化以下 Agent 系统提示词。保持原有角色定位不变，但按规范补充缺失的部分（如 Few-shot 示例、负面约束等），让提示词更专业、更完整。

## 当前 Agent 信息
- 名称：{req.name}
- 描述：{req.description}

## 当前提示词
{req.current_prompt}

## 可用工具
{AVAILABLE_TOOLS_DESC}

请直接输出优化后的完整提示词，不要加任何解释说明。"""
    else:
        user_content = f"""请根据以下信息从零生成一个 Agent 系统提示词。

## Agent 信息
- 名称：{req.name}
- 描述：{req.description}

## 可用工具
{AVAILABLE_TOOLS_DESC}

请直接输出完整的提示词，不要加任何解释说明。"""

    try:
        from services.llm_service import _call_llm, call_llm_async, MODEL
        resp = await call_llm_async(
            caller="agent_generator",
            messages=[
                {"role": "system", "content": PROMPT_GENERATOR_META},
                {"role": "user", "content": user_content},
            ],
            model=MODEL,
            max_tokens=get_config_int('llm.max_tokens_analysis', 8000),
        )
        result = resp.choices[0].message.content if resp and resp.choices else ""
        if not result:
            raise HTTPException(500, "AI 生成失败，返回为空")
        return {"ok": True, "prompt": result.strip()}
    except HTTPException:
        raise
    except Exception as e:
        logging.warning(f"AI 生成提示词失败: {e}")
        raise HTTPException(500, f"AI 生成失败: {str(e)}")


@router.get("/api/agents/{agent_id}/versions")
async def list_agent_versions_api(agent_id: int):
    """列出某 Agent 的提示词版本历史。"""
    versions = list_prompt_versions(agent_id, 'conversation')
    return {"versions": versions}


@router.post("/api/agents/{agent_id}/rollback/{version_id}")
async def rollback_agent_prompt_api(agent_id: int, version_id: int):
    """回滚到指定版本的提示词。"""
    version = get_prompt_version(version_id)
    if not version:
        raise HTTPException(404, "版本不存在")
    agent = get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent 不存在")
    save_prompt_version(agent_id, 'conversation', agent['system_prompt'])
    update_agent(agent_id, system_prompt=version['system_prompt'])
    return {"ok": True, "system_prompt": version['system_prompt']}


@router.get("/api/analysis-agents/{agent_id}/versions")
async def list_analysis_agent_versions_api(agent_id: int):
    """列出某分析 Agent 的提示词版本历史。"""
    versions = list_prompt_versions(agent_id, 'analysis')
    return {"versions": versions}


@router.get("/api/agents/{agent_id}/regression")
async def get_regression_result_api(agent_id: int, agent_type: str = "conversation"):
    """获取 Agent 的最近回归测试结果。"""
    from agent.regression import get_regression_result
    result = get_regression_result(agent_id, agent_type)
    if not result:
        return {"status": "none", "message": "暂无回归测试记录"}
    return result


@router.post("/api/analysis-agents/{agent_id}/rollback/{version_id}")
async def rollback_analysis_agent_prompt_api(agent_id: int, version_id: int):
    """回滚分析 Agent 到指定版本的提示词。"""
    version = get_prompt_version(version_id)
    if not version:
        raise HTTPException(404, "版本不存在")
    current = get_analysis_agent(agent_id)
    if not current:
        raise HTTPException(404, "Agent 不存在")
    save_prompt_version(agent_id, 'analysis', current['system_prompt'])
    update_analysis_agent(agent_id, system_prompt=version['system_prompt'])
    return {"ok": True, "system_prompt": version['system_prompt']}


# ── 升级六：ReAct 循环推理 ──────────────────────────────

@router.post("/api/agents/react")
async def react_reasoning_api(body: dict):
    """
    ReAct 循环推理接口（思考→行动→观察→再思考）。

    用于复杂多步问题的深度推理，带死循环检测。

    Body: {"query": str, "max_iterations"?: int}
    """
    from agent.react_loop import run_react_loop, MAX_ITERATIONS
    from services.llm_service import call_llm_async
    from tools import execute_tool

    query = (body.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "query 不能为空")

    max_iter = body.get("max_iterations") or MAX_ITERATIONS
    trace_id = f"react-{id(query) & 0xffff:x}"

    def _llm_call(messages):
        # 同步包装：react_loop 为同步设计，这里用 asyncio 跑
        import asyncio as _aio
        from services.llm_service import MODEL
        try:
            loop = _aio.get_event_loop()
        except RuntimeError:
            loop = _aio.new_event_loop()
        resp = loop.run_until_complete(
            call_llm_async(caller="react_loop", model=MODEL, messages=messages)
        )
        return resp.choices[0].message.content or ""

    def _tool_call(name, arguments, tid):
        return execute_tool(name, arguments or {}, trace_id=tid)

    result = run_react_loop(
        query=query,
        llm_call_fn=_llm_call,
        execute_tool_fn=_tool_call,
        max_iterations=max_iter,
        trace_id=trace_id,
    )
    return result
