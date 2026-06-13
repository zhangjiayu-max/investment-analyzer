"""统一的 Agent 分析执行器。

将分散在各路由中的 LLM 分析逻辑抽象为统一入口，自动完成：
  加载 agent prompt → RAG 检索 → 拼装上下文 → LLM 调用 → 保存记录 → 记录 RAG 日志

用法:
    from agent.analysis_runner import run_agent_analysis

    result = await run_agent_analysis(
        agent_id=3,
        caller="portfolio_panorama",
        context_parts={"持仓明细": holdings_text, "估值数据": val_text},
        user_question="请诊断我的持仓健康度",
        rag_query="投资组合 资产配置 风险分析",
    )
    # result = {"text": "...", "tokens": 1234, "record_id": 5}
"""

import asyncio
import json
import logging
import time

from llm_service import _call_llm, MODEL

logger = logging.getLogger(__name__)


async def run_agent_analysis(
    agent_id: int,
    caller: str,
    context_parts: dict[str, str],
    user_question: str,
    rag_query: str = None,
    conversation_id: int = 0,
    message_id: int = 0,
    system_prompt_override: str = None,
    temperature: float = 0.3,
    max_tokens: int = 8192,
    timeout: int = 120,
    record_type: str = None,
    record_summary: str = None,
    record_input: dict = None,
) -> dict:
    """统一的 Agent 分析执行器。

    Args:
        agent_id: analysis_agents 表 ID（用于加载 prompt 和保存记录）
        caller: 调用标识（传给 _call_llm，用于 token 统计）
        context_parts: 上下文片段 dict，key 为标题，value 为内容
                       会自动拼装为 Markdown 格式注入 user message
        user_question: 用户问题/指令
        rag_query: RAG 检索词。None 则跳过 RAG；空字符串也跳过
        conversation_id: 关联对话 ID（RAG 日志用）
        message_id: 关联消息 ID（RAG 日志用）
        system_prompt_override: 覆盖 agent 的 system_prompt（如需要自定义）
        temperature: LLM 温度
        max_tokens: 最大 token 数
        timeout: 超时秒数
        record_type: analysis_history 的类型标识（如 "panorama"）
        record_summary: analysis_history 的摘要
        record_input: analysis_history 的输入数据（JSON 序列化）

    Returns:
        {"text": str, "tokens": int, "record_id": int|None, "rag_context": str}
    """
    from db import get_analysis_agent, create_analysis_history

    # 1. 加载 agent prompt
    agent = get_analysis_agent(agent_id)
    if not agent:
        raise ValueError(f"Agent ID {agent_id} 不存在")

    system_prompt = system_prompt_override or agent.get("system_prompt", "")
    if not system_prompt:
        raise ValueError(f"Agent ID {agent_id} 没有配置 system_prompt")

    # 2. RAG 知识库检索
    rag_context = ""
    if rag_query:
        try:
            from rag import build_rag_context_with_details, log_rag_search
            rag_result = build_rag_context_with_details(query=rag_query, limit=5)
            rag_context = rag_result.get("context", "")
            # 记录 RAG 日志
            log_rag_search(
                conversation_id=conversation_id,
                message_id=message_id,
                query=rag_query,
                keywords=rag_result.get("keywords", []),
                results=rag_result.get("results", []),
                fts_count=rag_result.get("fts_count", 0),
                chroma_count=rag_result.get("chroma_count", 0),
                freshness_filtered=rag_result.get("freshness_filtered", 0),
            )
        except Exception as e:
            logger.warning(f"RAG 检索失败 [{caller}]: {e}")

    # 3. 拼装 user message
    sections = []
    for title, content in context_parts.items():
        if content:
            sections.append(f"## {title}\n{content}")
    if rag_context:
        sections.append(f"## 知识库参考（历史分析/文章）\n{rag_context[:1500]}")
    sections.append(f"## 用户问题\n{user_question}")

    user_message = "\n\n".join(sections)

    # 4. 调用 LLM
    start_time = time.time()
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(lambda: _call_llm(
                caller=caller,
                model=MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )),
            timeout=timeout,
        )
        result_text = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0
        duration_ms = int((time.time() - start_time) * 1000)
    except asyncio.TimeoutError:
        logger.error(f"LLM 调用超时 [{caller}]: {timeout}s")
        raise TimeoutError(f"AI 分析超时（{timeout}秒），请重试")
    except Exception as e:
        logger.error(f"LLM 调用失败 [{caller}]: {e}")
        raise

    # 5. 保存 analysis_history 记录
    record_id = None
    if record_type:
        try:
            record_id = create_analysis_history(
                agent_id=agent_id,
                agent_name=agent.get("name", ""),
                prompt_used=system_prompt[:500],
                news_context=list(context_parts.values())[0][:500] if context_parts else "",
                result=result_text[:2000],
                token_usage=tokens,
            )
        except Exception as e:
            logger.warning(f"保存分析记录失败 [{caller}]: {e}")

    # 6. 记录 agent_runs
    try:
        from db.agents import create_agent_run
        create_agent_run(
            conversation_id=conversation_id,
            message_id=message_id,
            agent_key=caller,
            agent_name=agent.get("name", ""),
            query=user_question[:500],
            result=result_text[:500],
            duration_ms=duration_ms,
            status="success",
        )
    except Exception as e:
        logger.warning(f"记录 agent_run 失败 [{caller}]: {e}")

    return {
        "text": result_text,
        "tokens": tokens,
        "record_id": record_id,
        "rag_context": rag_context,
    }
