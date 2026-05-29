"""Agent 对话系统路由 — /api/conversations/*, /api/chat/feedback, /api/rag-logs, /api/rag-stats"""

import asyncio
import json
import logging
import queue
import threading
import time
from collections import Counter

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from db import (
    list_conversations, get_conversation, create_conversation, update_conversation, delete_conversation,
    get_messages, create_message, update_message_metadata, update_message_content_and_metadata,
    get_agent, create_agent_run,
    list_holdings, create_alert, save_llm_feedback,
    _get_conn,
)
from state import running_agents as _running_agents
from agent.orchestrator import orchestrate, orchestrate_stream, clarify_requirement, CancelledError
from agent.multi_agent import run_specialist
from rag import build_rag_context_with_details, log_rag_search
from llm_service import _call_llm, MODEL, ORCHESTRATOR_PROMPT

logger = logging.getLogger(__name__)

router = APIRouter(tags=["conversations"])


# ── 请求模型 ─────────────────────────────────────────────


class CreateConversationRequest(BaseModel):
    title: str = "新对话"
    agent_id: int = None
    context_data: str = None


class SendMessageRequest(BaseModel):
    content: str


class ChatFeedbackRequest(BaseModel):
    message_id: int = None
    feedback: str  # "helpful" or "unhelpful"
    note: str = ""
    input_summary: str = ""
    output_summary: str = ""


# ── 对话 CRUD ─────────────────────────────────────────────


@router.get("/api/conversations")
async def list_conversations_api():
    """对话列表。"""
    return {"conversations": list_conversations()}


@router.post("/api/conversations")
async def create_conversation_api(req: CreateConversationRequest):
    """创建对话。"""
    conv_id = create_conversation(title=req.title, agent_id=req.agent_id, context_data=req.context_data)
    return {"ok": True, "conversation_id": conv_id}


@router.delete("/api/conversations/{conv_id}")
async def delete_conversation_api(conv_id: int):
    """删除对话。"""
    delete_conversation(conv_id)
    return {"ok": True}


@router.get("/api/conversations/{conv_id}/messages")
async def get_messages_api(conv_id: int, limit: int = 50):
    """获取对话消息历史。"""
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")
    msgs = get_messages(conv_id, limit)
    # 解析 metadata JSON 字符串为 dict
    for msg in msgs:
        if msg.get("metadata") and isinstance(msg["metadata"], str):
            try:
                msg["metadata"] = json.loads(msg["metadata"])
            except Exception:
                pass
    return {"conversation": conv, "messages": msgs}


@router.post("/api/conversations/{conv_id}/messages")
async def send_message_api(conv_id: int, req: SendMessageRequest):
    """发送消息并获取 AI 回复（多 Agent 协作模式）。"""
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")

    # 1. 存储用户消息（去重：中断重试时不重复保存）
    existing = get_messages(conv_id, limit=1)
    if not existing or existing[-1]["role"] != "user" or existing[-1]["content"] != req.content:
        create_message(conv_id, "user", req.content)

    # 2. RAG 检索
    agent = get_agent(conv["agent_id"]) if conv.get("agent_id") else None
    rag_types = []
    if agent and agent.get("knowledge_scope"):
        try:
            scope = json.loads(agent["knowledge_scope"]) if isinstance(agent["knowledge_scope"], str) else agent["knowledge_scope"]
            rag_types = scope.get("rag_types", [])
        except (json.JSONDecodeError, TypeError):
            pass

    rag_result = build_rag_context_with_details(req.content, content_types=rag_types if rag_types else None)
    rag_context = rag_result["context"]

    # 3. 获取对话历史
    history = get_messages(conv_id, limit=20)
    msg_list = [{"role": m["role"], "content": m["content"]} for m in history]

    # 4. 调用 Orchestrator（多 Agent 协作）
    try:
        llm_result = orchestrate(req.content, msg_list, rag_context)
        answer = llm_result["answer"]
    except Exception as e:
        answer = f"AI 回复失败: {str(e)}"
        llm_result = {"answer": answer, "specialist_results": [], "tool_calls": [], "turns": 0}

    # 5. 存储 AI 回复
    specialist_results = llm_result.get("specialist_results", [])
    metadata_dict = {
        "specialist_results": [
            {"agent_key": s.get("agent_key", ""), "agent": s["agent"], "icon": s["icon"], "analysis": s.get("analysis", "")[:500]}
            for s in specialist_results
        ],
        "tool_calls": llm_result.get("tool_calls", []),
    }
    metadata = json.dumps(metadata_dict, ensure_ascii=False) if specialist_results else None
    msg_id = create_message(conv_id, "assistant", answer, metadata=metadata)

    # 6. 记录 RAG 日志
    log_rag_search(
        conversation_id=conv_id,
        message_id=msg_id,
        query=req.content,
        keywords=rag_result.get("keywords", []),
        results=rag_result.get("results", []),
        content_types=rag_types if rag_types else None,
    )

    # 7. 自动更新对话标题
    if len(history) <= 1 and conv.get("title") == "新对话":
        short_title = req.content[:30] + ("..." if len(req.content) > 30 else "")
        update_conversation(conv_id, title=short_title)

    return {
        "answer": answer,
        "specialist_results": [
            {"agent_key": s.get("agent_key", ""), "agent": s["agent"], "icon": s["icon"], "analysis": s.get("analysis", "")}
            for s in specialist_results
        ],
        "rag": {
            "keywords": rag_result.get("keywords", []),
            "sources": [{"type": r.get("label", r.get("content_type")), "title": r.get("title")} for r in rag_result.get("results", [])[:3]],
            "results_count": len(rag_result.get("results", [])),
        },
        "tool_calls": llm_result.get("tool_calls", []),
        "turns": llm_result.get("turns", 1),
    }


@router.post("/api/conversations/{conv_id}/messages/stream")
async def send_message_stream(conv_id: int, req: SendMessageRequest, request: Request):
    """SSE 流式对话，支持多 Agent 专家分析实时展示。"""
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")

    # 解析 knowledge_scope
    rag_types = []
    agent = get_agent(conv["agent_id"]) if conv.get("agent_id") else None
    if agent and agent.get("knowledge_scope"):
        try:
            scope = json.loads(agent["knowledge_scope"]) if isinstance(agent["knowledge_scope"], str) else agent["knowledge_scope"]
            rag_types = scope.get("rag_types", [])
        except (json.JSONDecodeError, TypeError):
            pass

    async def event_stream():
        import asyncio
        import threading

        cancel_event = threading.Event()
        request_start = time.time()
        phase_timings = {}

        # 1. 存储用户消息（去重：中断重试时不重复保存）
        existing = get_messages(conv_id, limit=1)
        if not existing or existing[-1]["role"] != "user" or existing[-1]["content"] != req.content:
            create_message(conv_id, "user", req.content)
        yield _sse_event("user_message", {"content": req.content})

        # 2. 需求澄清（使用 LLM 分析问题）
        if await request.is_disconnected():
            cancel_event.set()
            return
        yield _sse_event("status", {"message": "正在理解您的问题..."})

        def _run_clarification():
            return clarify_requirement(req.content)
        t0 = time.time()
        clarification = await asyncio.to_thread(_run_clarification)
        phase_timings["clarification_ms"] = int((time.time() - t0) * 1000)
        complexity = clarification["complexity"]
        yield _sse_event("status", {"message": f"问题类型: {complexity} ({clarification.get('reason', '')})"})

        # 3. 简单任务：直接路由到专家，跳过 Orchestrator
        if complexity == "simple" and len(clarification.get("specialists", [])) == 1:
            # 只需要1个专家，直接调用
            agent_key = clarification["specialists"][0]
            if agent_key:
                if await request.is_disconnected():
                    cancel_event.set()
                    return
                yield _sse_event("status", {"message": f"正在咨询{_get_specialist_name(agent_key)}..."})

                # 直接运行专家
                def _run_expert():
                    try:
                        return run_specialist(agent_key, req.content)
                    except Exception as e:
                        return {"error": str(e)}

                result = await asyncio.to_thread(_run_expert)

                if "error" not in result:
                    # 发送专家完成事件
                    yield _sse_event("specialist_done", {
                        "agent_key": result.get("agent_key", agent_key),
                        "agent": result.get("agent", ""),
                        "icon": result.get("icon", ""),
                        "analysis": result.get("analysis", ""),
                        "duration_ms": result.get("duration_ms", 0),
                    })

                    # 记录专家执行到 agent_runs
                    create_agent_run(
                        conversation_id=conv_id, message_id=0,
                        agent_key=result.get("agent_key", agent_key),
                        agent_name=result.get("agent", ""),
                        query=req.content[:500],
                        result=(result.get("analysis", "") or "")[:500],
                        duration_ms=result.get("duration_ms", 0),
                    )

                    # 发送最终回答
                    answer = result.get("analysis", "")
                    specialist_results = [{
                        "agent_key": result.get("agent_key", agent_key),
                        "agent": result.get("agent", ""),
                        "icon": result.get("icon", ""),
                        "analysis": answer,
                        "duration_ms": result.get("duration_ms", 0),
                    }]

                    yield _sse_event("answer", {
                        "content": answer,
                        "specialist_results": specialist_results,
                    })

                    # 存储回复
                    metadata_dict = {
                        "specialist_results": [
                            {"agent_key": s.get("agent_key", ""), "agent": s["agent"], "icon": s["icon"], "analysis": s.get("analysis", "")[:500]}
                            for s in specialist_results
                        ],
                        "complexity": complexity,
                    }
                    metadata = json.dumps(metadata_dict, ensure_ascii=False)
                    msg_id = create_message(conv_id, "assistant", answer, metadata=metadata)

                    total_ms = int((time.time() - request_start) * 1000)
                    phase_timings["specialist_ms"] = result.get("duration_ms", 0)
                    phase_timings["total_ms"] = total_ms

                    # 更新 agent_runs 的 message_id + 记录整体执行
                    try:
                        _c = _get_conn()
                        _c.execute(
                            "UPDATE agent_runs SET message_id = ? WHERE conversation_id = ? AND message_id = 0",
                            (msg_id, conv_id)
                        )
                        _c.commit()
                        _c.close()
                        create_agent_run(
                            conversation_id=conv_id, message_id=msg_id,
                            agent_key="chat_turn", agent_name="对话整体",
                            query=req.content[:500], result=answer[:500],
                            tool_calls=json.dumps(phase_timings, ensure_ascii=False),
                            duration_ms=total_ms,
                        )
                    except Exception as _e:
                        logger.warning(f"记录 agent_runs 失败: {_e}")

                    yield _sse_event("done", {
                        "message_id": msg_id,
                        "duration_ms": total_ms,
                        "phase_timings": phase_timings,
                    })
                    return
                else:
                    # 专家执行失败，回退到 Orchestrator
                    yield _sse_event("status", {"message": "专家执行失败，切换到完整分析模式..."})

        # 4. RAG 检索（中等和复杂任务）
        if await request.is_disconnected():
            cancel_event.set()
            return
        yield _sse_event("status", {"message": "正在检索知识库..."})
        t0 = time.time()
        rag_result = build_rag_context_with_details(req.content, content_types=rag_types if rag_types else None)
        phase_timings["rag_ms"] = int((time.time() - t0) * 1000)
        rag_context = rag_result["context"]

        if rag_result.get("results"):
            sources = [{"type": r.get("label", r.get("content_type")), "title": r.get("title")} for r in rag_result["results"][:3]]
            yield _sse_event("rag_sources", {"sources": sources})

        # 5. 获取对话历史
        history = get_messages(conv_id, limit=20)
        msg_list = [{"role": m["role"], "content": m["content"]} for m in history]

        # 6. 调用 Orchestrator（多 Agent 协作）
        if await request.is_disconnected():
            cancel_event.set()
            return
        yield _sse_event("status", {"message": "正在分析问题，决定需要咨询哪些专家..."})

        def _run_orchestrator_stream():
            """在线程中运行 orchestrator 流式生成器，通过队列传递事件。"""
            import queue
            q = queue.Queue()

            def _producer():
                try:
                    for event in orchestrate_stream(req.content, msg_list, rag_context, cancel_event=cancel_event):
                        q.put(event)
                except CancelledError:
                    q.put({"type": "cancelled", "message": "用户取消了执行"})
                except TimeoutError as e:
                    q.put({"type": "error", "message": f"执行超时: {e}"})
                except Exception as e:
                    q.put({"type": "error", "message": str(e)})
                finally:
                    q.put(None)  # 结束信号

            t = threading.Thread(target=_producer, daemon=True)
            t.start()
            return q

        t_orch_start = time.time()
        q = await asyncio.to_thread(_run_orchestrator_stream)

        specialist_results = []
        all_tool_calls = []
        final_answer = ""
        client_disconnected = False
        stream_msg_id = 0  # 追踪正在构建中的 assistant 消息 ID

        while True:
            try:
                event = await asyncio.to_thread(q.get, timeout=0.5)
            except queue.Empty:
                # 队列超时 — 检查客户端是否断开
                if not client_disconnected and await request.is_disconnected():
                    logger.info("客户端断开连接，设置取消标志，等待后台任务完成后保存结果")
                    client_disconnected = True
                    cancel_event.set()
                    # 清理该对话的 running agents
                    to_remove = [k for k in _running_agents if k.startswith(f"{conv_id}_")]
                    for k in to_remove:
                        _running_agents.pop(k, None)
                    # 不 return，继续消费队列直到后台线程完成
                continue

            if event is None:
                break

            event_type = event.get("type")

            if event_type == "cancelled":
                logger.info("执行已被用户取消")
                # 清理该对话的 running agents
                to_remove = [k for k in _running_agents if k.startswith(f"{conv_id}_")]
                for k in to_remove:
                    _running_agents.pop(k, None)
                # 标记取消状态
                if stream_msg_id > 0:
                    try:
                        update_message_metadata(stream_msg_id, {
                            "execution_status": "cancelled",
                            "complexity": complexity,
                            "specialist_results": [
                                {"agent_key": s.get("agent_key", ""), "agent": s["agent"], "icon": s["icon"], "analysis": s.get("analysis", "")[:500]}
                                for s in specialist_results if not s.get("is_cross_review")
                            ],
                            "cross_review_results": [
                                {"agent_key": s.get("agent_key", ""), "agent": s["agent"], "icon": s["icon"], "analysis": s.get("analysis", "")[:500]}
                                for s in specialist_results if s.get("is_cross_review")
                            ],
                            "tool_calls": all_tool_calls,
                        })
                    except Exception as _e:
                        logger.warning(f"标记取消状态失败: {_e}")
                # 不 return，继续消费队列直到后台线程完成并保存结果

            elif event_type == "status":
                if not client_disconnected:
                    yield _sse_event("status", event)

            elif event_type == "plan":
                if not client_disconnected:
                    yield _sse_event("plan", {
                        "complexity": event.get("complexity", ""),
                        "reason": event.get("reason", ""),
                        "refined_query": event.get("refined_query", ""),
                    })

            elif event_type == "specialist_start":
                agent_key = event.get("agent_key", "")
                agent_name = event.get("agent", "")
                uid = f"{conv_id}_{agent_key}_{int(time.time())}"
                _running_agents[uid] = {
                    "agent": agent_name,
                    "task": f"对话 #{conv_id}",
                    "started_at": time.time(),
                }
                if not client_disconnected:
                    yield _sse_event("specialist_start", {
                        "agent_key": agent_key,
                        "agent": agent_name,
                        "icon": event.get("icon"),
                    })

            elif event_type == "specialist_done":
                # 清除对应的 running agent
                agent_key = event.get("agent_key", "")
                to_remove = [k for k in _running_agents if k.startswith(f"{conv_id}_{agent_key}_")]
                for k in to_remove:
                    _running_agents.pop(k, None)
                specialist_results.append({
                    "agent_key": event.get("agent_key"),
                    "agent": event.get("agent"),
                    "icon": event.get("icon"),
                    "analysis": event.get("analysis"),
                    "duration_ms": event.get("duration_ms"),
                })
                # 记录到 agent_runs
                create_agent_run(
                    conversation_id=conv_id, message_id=0,
                    agent_key=event.get("agent_key", ""),
                    agent_name=event.get("agent", ""),
                    query=req.content[:500],
                    result=(event.get("analysis", "") or "")[:500],
                    duration_ms=event.get("duration_ms", 0),
                )
                # 增量保存执行进度到 metadata
                try:
                    _progress_metadata = {
                        "execution_status": "streaming",
                        "complexity": complexity,
                        "specialist_results": [
                            {"agent_key": s.get("agent_key", ""), "agent": s["agent"], "icon": s["icon"], "analysis": s.get("analysis", "")[:500]}
                            for s in specialist_results
                        ],
                        "tool_calls": all_tool_calls,
                    }
                    if stream_msg_id == 0:
                        stream_msg_id = create_message(conv_id, "assistant", "⏳ 分析进行中...", metadata=json.dumps(_progress_metadata, ensure_ascii=False))
                    else:
                        update_message_metadata(stream_msg_id, _progress_metadata)
                except Exception as _e:
                    logger.warning(f"增量保存执行进度失败: {_e}")
                if not client_disconnected:
                    yield _sse_event("specialist_done", {
                        "agent_key": event.get("agent_key"),
                        "agent": event.get("agent"),
                        "icon": event.get("icon"),
                        "analysis": event.get("analysis"),
                        "duration_ms": event.get("duration_ms"),
                    })

            elif event_type == "cross_review_start":
                if not client_disconnected:
                    yield _sse_event("cross_review_start", {
                        "agent_key": event.get("agent_key"),
                        "agent": event.get("agent"),
                        "icon": event.get("icon"),
                    })

            elif event_type == "cross_review_done":
                specialist_results.append({
                    "agent_key": event.get("agent_key"),
                    "agent": event.get("agent"),
                    "icon": event.get("icon"),
                    "analysis": event.get("analysis"),
                    "duration_ms": event.get("duration_ms"),
                    "is_cross_review": True,
                })
                # 增量保存交叉审阅进度
                try:
                    _cr_metadata = {
                        "execution_status": "streaming",
                        "complexity": complexity,
                        "specialist_results": [
                            {"agent_key": s.get("agent_key", ""), "agent": s["agent"], "icon": s["icon"], "analysis": s.get("analysis", "")[:500]}
                            for s in specialist_results if not s.get("is_cross_review")
                        ],
                        "cross_review_results": [
                            {"agent_key": s.get("agent_key", ""), "agent": s["agent"], "icon": s["icon"], "analysis": s.get("analysis", "")[:500]}
                            for s in specialist_results if s.get("is_cross_review")
                        ],
                        "tool_calls": all_tool_calls,
                    }
                    if stream_msg_id > 0:
                        update_message_metadata(stream_msg_id, _cr_metadata)
                except Exception as _e:
                    logger.warning(f"增量保存交叉审阅进度失败: {_e}")
                if not client_disconnected:
                    yield _sse_event("cross_review_done", {
                        "agent_key": event.get("agent_key"),
                        "agent": event.get("agent"),
                        "icon": event.get("icon"),
                        "analysis": event.get("analysis"),
                        "duration_ms": event.get("duration_ms"),
                    })

            elif event_type == "answer":
                final_answer = event.get("content", "")
                all_tool_calls = event.get("tool_calls", [])
                if not specialist_results:
                    specialist_results = event.get("specialist_results", [])

            elif event_type == "error":
                # 标记失败状态
                if stream_msg_id > 0:
                    try:
                        update_message_metadata(stream_msg_id, {
                            "execution_status": "failed",
                            "error_message": event.get("message", "未知错误"),
                            "complexity": complexity,
                            "specialist_results": [
                                {"agent_key": s.get("agent_key", ""), "agent": s["agent"], "icon": s["icon"], "analysis": s.get("analysis", "")[:500]}
                                for s in specialist_results if not s.get("is_cross_review")
                            ],
                            "cross_review_results": [
                                {"agent_key": s.get("agent_key", ""), "agent": s["agent"], "icon": s["icon"], "analysis": s.get("analysis", "")[:500]}
                                for s in specialist_results if s.get("is_cross_review")
                            ],
                            "tool_calls": all_tool_calls,
                        })
                    except Exception as _e:
                        logger.warning(f"标记失败状态失败: {_e}")
                if not client_disconnected:
                    yield _sse_event("error", {"message": event.get("message", "未知错误")})
                    return
                # 客户端已断开，记录错误但继续保存结果
                logger.warning(f"后台执行出错（客户端已断开）: {event.get('message', '')}")

        phase_timings["orchestrator_ms"] = int((time.time() - t_orch_start) * 1000)
        answer = final_answer

        # 5. 发送最终回答（仅客户端在线时）
        if not client_disconnected:
            yield _sse_event("answer", {
                "content": answer,
                "specialist_results": specialist_results,
            })

        # 5.1 主动分析是否产生预警（后台异步执行）
        if answer:
            asyncio.create_task(_proactive_alert_check(req.content, answer, specialist_results))

        # 6. 存储 AI 回复（始终保存，即使客户端已断开）
        if not answer and specialist_results:
            # 没有最终回答但有专家结果，拼接摘要
            answer = "\n\n".join(
                f"**{s.get('agent', '')}**: {s.get('analysis', '')[:300]}"
                for s in specialist_results
                if s.get('analysis')
            ) or "（执行未完成，以下为部分分析结果）"

        if answer:
            final_metadata = {
                "execution_status": "completed",
                "complexity": complexity,
                "specialist_results": [
                    {"agent_key": s.get("agent_key", ""), "agent": s["agent"], "icon": s["icon"], "analysis": s.get("analysis", "")[:500]}
                    for s in specialist_results if not s.get("is_cross_review")
                ],
                "cross_review_results": [
                    {"agent_key": s.get("agent_key", ""), "agent": s["agent"], "icon": s["icon"], "analysis": s.get("analysis", "")[:500]}
                    for s in specialist_results if s.get("is_cross_review")
                ],
                "tool_calls": all_tool_calls,
                "phase_timings": phase_timings,
            }
            if stream_msg_id > 0:
                # 已有占位消息，更新 content + metadata
                update_message_content_and_metadata(stream_msg_id, answer, final_metadata)
                msg_id = stream_msg_id
            else:
                # 简单任务路径，直接创建
                metadata = json.dumps(final_metadata, ensure_ascii=False) if specialist_results or all_tool_calls else None
                msg_id = create_message(conv_id, "assistant", answer, metadata=metadata)
        else:
            msg_id = stream_msg_id if stream_msg_id > 0 else 0
            # 有占位消息但无最终回答，标记为失败
            if stream_msg_id > 0:
                try:
                    update_message_content_and_metadata(stream_msg_id, "❌ 执行未完成（超时或异常中断）", {
                        "execution_status": "failed",
                        "complexity": complexity,
                        "specialist_results": [
                            {"agent_key": s.get("agent_key", ""), "agent": s["agent"], "icon": s["icon"], "analysis": s.get("analysis", "")[:500]}
                            for s in specialist_results if not s.get("is_cross_review")
                        ],
                        "cross_review_results": [
                            {"agent_key": s.get("agent_key", ""), "agent": s["agent"], "icon": s["icon"], "analysis": s.get("analysis", "")[:500]}
                            for s in specialist_results if s.get("is_cross_review")
                        ],
                        "tool_calls": all_tool_calls,
                        "phase_timings": phase_timings,
                    })
                except Exception as _e:
                    logger.warning(f"标记失败状态失败: {_e}")

        # 7. 记录 RAG 日志
        log_rag_search(
            conversation_id=conv_id,
            message_id=msg_id,
            query=req.content,
            keywords=rag_result.get("keywords", []),
            results=rag_result.get("results", []),
            content_types=rag_types if rag_types else None,
        )

        # 8. 更新 agent_runs 的 message_id + 记录整体执行
        total_ms = int((time.time() - request_start) * 1000)
        phase_timings["total_ms"] = total_ms
        try:
            _c = _get_conn()
            _c.execute(
                "UPDATE agent_runs SET message_id = ? WHERE conversation_id = ? AND message_id = 0",
                (msg_id, conv_id)
            )
            _c.commit()
            _c.close()
            create_agent_run(
                conversation_id=conv_id, message_id=msg_id,
                agent_key="chat_turn", agent_name="对话整体",
                query=req.content[:500], result=answer[:500],
                tool_calls=json.dumps(phase_timings, ensure_ascii=False),
                duration_ms=total_ms,
            )
        except Exception as _e:
            logger.warning(f"记录 agent_runs 失败: {_e}")

        # 9. 自动更新对话标题
        if len(history) <= 1 and conv.get("title") == "新对话":
            short_title = req.content[:30] + ("..." if len(req.content) > 30 else "")
            update_conversation(conv_id, title=short_title)

        if not client_disconnected:
            yield _sse_event("done", {
                "message_id": msg_id,
                "duration_ms": total_ms,
                "phase_timings": phase_timings,
            })
        else:
            logger.info(f"对话 {conv_id} 结果已保存（客户端已断开），answer 长度: {len(answer)}")

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── 辅助函数 ─────────────────────────────────────────────


def _sse_event(event_type: str, data: dict) -> str:
    """格式化 SSE 事件。"""
    return f"data: {json.dumps({'type': event_type, 'data': data}, ensure_ascii=False)}\n\n"


async def _proactive_alert_check(query: str, answer: str, specialist_results: list):
    """对话结束后，主动检测是否需要对持仓生成预警。"""
    try:
        # 检测是否涉及政策/新闻/估值变化等可能影响持仓的内容
        alert_keywords = ["政策", "新闻", "利好", "利空", "上涨", "下跌", "大涨", "大跌",
                          "风险", "泡沫", "危机", "加息", "降息", "降准", "监管",
                          "高估", "低估", "加仓", "减仓", "卖出", "买入", "注意"]
        if not any(kw in query for kw in alert_keywords) and not any(kw in answer for kw in alert_keywords):
            return

        # 获取持仓数据
        holdings = list_holdings()
        if not holdings:
            return

        # 检查各专家分析中是否有关联持仓的内容
        fund_names = {h.get("fund_name", "") for h in holdings if h.get("fund_name")}
        index_names = {h.get("index_name", "") for h in holdings if h.get("index_name")}

        # 构建预警内容
        alert_holdings = []
        combined_text = query + " " + answer

        for sr in specialist_results:
            analysis = sr.get("analysis", "")
            if not analysis:
                continue
            combined_text += " " + analysis[:2000]

        for h in holdings:
            fname = h.get("fund_name", "")
            iname = h.get("index_name", "")
            if (fname and fname in combined_text) or (iname and iname in combined_text):
                alert_holdings.append(h)

        if not alert_holdings and any(kw in combined_text for kw in ["政策", "新闻", "利好", "利空", "市场"]):
            # 虽然没直接提到某只基金，但涉及政策/新闻，对全部持仓生成轻度预警
            for h in holdings[:3]:  # 最多3只
                create_alert(
                    alert_type="news_impact",
                    title=f"市场动态可能影响 {h.get('fund_name', '')}",
                    content=f"当前对话涉及市场变化，可能影响您的持仓 {h.get('fund_name', '')}（{h.get('fund_code', '')}）。建议关注后续走势。",
                    severity="info",
                    related_fund_code=h.get("fund_code"),
                    related_fund_name=h.get("fund_name"),
                    source="ai_analysis",
                )
        elif alert_holdings:
            for h in alert_holdings[:5]:
                create_alert(
                    alert_type="news_impact",
                    title=f"对话涉及 {h.get('fund_name', '')}，建议关注",
                    content=f"当前对话内容涉及您的持仓 {h.get('fund_name', '')}（{h.get('fund_code', '')}），可能影响该持仓。",
                    severity="info",
                    related_fund_code=h.get("fund_code"),
                    related_fund_name=h.get("fund_name"),
                    source="ai_analysis",
                )
    except Exception as e:
        logger.warning(f"[proactive_alert] 生成预警异常: {e}")


def _route_to_specialist(query: str) -> str | None:
    """根据问题关键词路由到最合适的专家。返回 agent_key 或 None。"""
    query = query.strip()

    # 估值相关关键词 → 估值专家
    valuation_keywords = ["估值", "PE", "PB", "百分位", "z-score", "高估", "低估", "贵不贵", "便宜"]
    if any(kw in query for kw in valuation_keywords):
        return "valuation_expert"

    # 新闻/市场动态关键词 → 择时分析师
    news_keywords = ["新闻", "最新", "动态", "政策", "消息", "市场", "今天", "昨天"]
    if any(kw in query for kw in news_keywords):
        return "market_analyst"

    # 风险相关关键词 → 风险评估师
    risk_keywords = ["风险", "回撤", "波动", "最大回撤"]
    if any(kw in query for kw in risk_keywords):
        return "risk_assessor"

    # 配置相关关键词 → 资产配置师
    allocation_keywords = ["配置", "配比", "定投", "股债", "组合"]
    if any(kw in query for kw in allocation_keywords):
        return "allocation_advisor"

    # 默认返回估值专家（最常见的查询）
    return "valuation_expert"


def _get_specialist_name(agent_key: str) -> str:
    """获取专家名称。"""
    names = {
        "valuation_expert": "估值专家",
        "market_analyst": "择时分析师",
        "risk_assessor": "风险评估师",
        "allocation_advisor": "资产配置师",
    }
    return names.get(agent_key, "专家")


# ── RAG 日志 API ─────────────────────────────────────────


@router.get("/api/conversations/{conv_id}/rag-logs")
async def get_rag_logs_api(conv_id: int, limit: int = 50):
    """获取对话的 RAG 检索日志。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT * FROM rag_logs
        WHERE conversation_id = ?
        ORDER BY id DESC
        LIMIT ?
    """, (conv_id, limit)).fetchall()
    conn.close()
    logs = []
    for row in rows:
        log = dict(row)
        log["keywords"] = json.loads(log.get("keywords") or "[]")
        log["results"] = json.loads(log.get("results") or "[]")
        log["content_types"] = json.loads(log.get("content_types") or "[]")
        logs.append(log)
    return {"logs": logs}


@router.get("/api/rag-logs")
async def get_all_rag_logs_api(limit: int = 100):
    """获取所有 RAG 检索日志。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT * FROM rag_logs
        ORDER BY id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    logs = []
    for row in rows:
        log = dict(row)
        log["keywords"] = json.loads(log.get("keywords") or "[]")
        log["results"] = json.loads(log.get("results") or "[]")
        log["content_types"] = json.loads(log.get("content_types") or "[]")
        logs.append(log)
    return {"logs": logs}


# ── 反馈 API ─────────────────────────────────────────────


@router.post("/api/chat/feedback")
async def submit_chat_feedback_api(req: ChatFeedbackRequest):
    """提交对 AI 对话回复的反馈。"""
    if req.feedback not in ("helpful", "unhelpful"):
        raise HTTPException(400, "feedback 必须为 helpful 或 unhelpful")
    save_llm_feedback(
        caller="chat",
        input_summary=req.input_summary[:200],
        output_summary=req.output_summary[:200],
        rating=req.feedback,
        comment=req.note,
    )
    # 触发反馈学习
    try:
        from agent.feedback_learner import update_user_profile_from_feedback
        update_user_profile_from_feedback("default", req.feedback, req.note, req.input_summary)
    except Exception as e:
        logger.warning(f"反馈学习更新失败: {e}")
    return {"ok": True}


# ── RAG 统计 API ─────────────────────────────────────────


@router.get("/api/rag-stats")
async def get_rag_stats_api(days: int = 7):
    """获取 RAG 检索统计。"""
    conn = _get_conn()

    # 总检索次数
    total = conn.execute("SELECT COUNT(*) FROM rag_logs").fetchone()[0]

    # 按天统计
    daily = conn.execute("""
        SELECT date(created_at) as day, COUNT(*) as count
        FROM rag_logs
        WHERE created_at >= datetime('now', ?)
        GROUP BY date(created_at)
        ORDER BY day DESC
    """, (f"-{days} days",)).fetchall()

    # 热门关键词
    keywords_raw = conn.execute("""
        SELECT keywords FROM rag_logs
        WHERE created_at >= datetime('now', ?)
        ORDER BY id DESC LIMIT 100
    """, (f"-{days} days",)).fetchall()

    keyword_counter = Counter()
    for row in keywords_raw:
        try:
            kws = json.loads(row[0] or "[]")
            for kw in kws:
                keyword_counter[kw] += 1
        except:
            pass
    top_keywords = [{"keyword": k, "count": c} for k, c in keyword_counter.most_common(20)]

    # 知识类型命中统计
    type_stats = conn.execute("""
        SELECT content_types, COUNT(*) as count
        FROM rag_logs
        WHERE created_at >= datetime('now', ?)
        GROUP BY content_types
    """, (f"-{days} days",)).fetchall()

    type_counter = Counter()
    for row in type_stats:
        try:
            types = json.loads(row[0] or "[]")
            for t in types:
                type_counter[t] += 1
        except:
            pass
    type_distribution = [{"type": t, "count": c} for t, c in type_counter.most_common()]

    # 平均命中结果数
    avg_results = conn.execute("""
        SELECT AVG(results_count) FROM rag_logs
        WHERE created_at >= datetime('now', ?)
    """, (f"-{days} days",)).fetchone()[0] or 0

    conn.close()

    return {
        "total": total,
        "daily": [dict(r) for r in daily],
        "top_keywords": top_keywords,
        "type_distribution": type_distribution,
        "avg_results": round(avg_results, 1),
    }
