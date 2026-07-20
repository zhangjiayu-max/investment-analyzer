"""Agent 对话系统路由 — /api/conversations/*, /api/chat/feedback, /api/rag-logs, /api/rag-stats"""

import asyncio
import json
import logging
import queue
import threading
import time
import uuid
from collections import Counter

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from db import (
    list_conversations, get_conversation, create_conversation, update_conversation, delete_conversation,
    get_messages, create_message, update_message_metadata, update_message_content_and_metadata,
    get_latest_recoverable_assistant, mark_message_execution_status, retry_assistant_message,
    get_agent, create_agent_run,
    list_holdings, create_alert, save_llm_feedback,
    get_conversation_summary, save_conversation_summary,
    _get_conn,
    create_stream_channel, append_stream_event, update_stream_heartbeat,
    complete_stream_channel, fail_stream_channel, cancel_stream_channel,
    get_latest_channel_for_message, mark_stream_channel_aborted,
    is_stream_heartbeat_stale, list_stream_events, get_stream_channel,
)
from db.config import get_config, get_config_bool, get_config_float, get_config_int
from infra.state import running_agents as _running_agents
from agent.orchestrator import orchestrate, orchestrate_stream, clarify_requirement, CancelledError
from services.portfolio_fact_layer import build_portfolio_facts
from agent.multi_agent import run_specialist
from services.rag import build_rag_context_with_details, log_rag_search
from services.llm_service import _call_llm, MODEL, ORCHESTRATOR_PROMPT
from infra.output_reviewer import review_output

logger = logging.getLogger(__name__)


async def _async_verify_and_log(conv_id: int, msg_id: int, answer: str):
    """异步基金代码幻觉校验（不阻塞主回复流）。"""
    try:
        from agent.hallucination_guard import verify_fund_codes_in_response
        result = await verify_fund_codes_in_response(answer, conv_id, msg_id)
        if result.get("hallucinations"):
            logger.warning(
                f"幻觉检测 msg={msg_id}: {len(result['hallucinations'])}个代码名称不匹配 "
                + "; ".join(
                    f"{h['code']}={h['claimed']} vs {h['actual']}"
                    for h in result["hallucinations"][:5]
                )
            )
    except Exception as e:
        logger.debug(f"异步幻觉校验失败: {e}")


def _build_effective_query(content: str, images: list[dict]) -> str:
    """如果有图片且 parse_result 无 error，将图片上下文拼入查询。"""
    if not images:
        return content
    image_context_parts = []
    for img in images:
        parse_result = img.get("parse_result") or {}
        if not parse_result.get("error"):
            img_type = parse_result.get("type", "未知")
            img_summary = parse_result.get("summary", parse_result.get("description", ""))
            if isinstance(img_summary, (dict, list)):
                img_summary = json.dumps(img_summary, ensure_ascii=False)
            image_context_parts.append(f"[用户上传图片 {img.get('url', '')}] 类型: {img_type}, 摘要: {img_summary}")
    if image_context_parts:
        return content + "\n\n[图片上下文]\n" + "\n".join(image_context_parts)
    return content

router = APIRouter(tags=["conversations"])


def _build_unified_context_safe(
    conv_id: int,
    query: str,
    scenario_type: str = "general_analysis",
    agent_id: int | None = None,
    rag_context: str = "",
    token_budget: int = 6000,
) -> str:
    """构建统一上下文，失败时返回空字符串，避免阻断对话主流程。"""
    try:
        from services.conversation_context import build_conversation_context
        user_id = "default"
        try:
            conv = get_conversation(conv_id)
            if conv and conv.get("user_id"):
                user_id = conv.get("user_id") or "default"
        except Exception:
            pass

        bundle = build_conversation_context(
            conversation_id=conv_id,
            current_user_message=query,
            scenario_type=scenario_type,
            agent_id=agent_id,
            rag_context=rag_context,
            token_budget=token_budget,
            user_id=user_id,
        )
        return bundle.get("prompt_context", "")
    except Exception as e:
        logger.warning(f"统一对话上下文构建失败 conv_id={conv_id}: {e}")
        return ""


def _build_arbitrator_specialist(arbitration: dict) -> dict | None:
    """把仲裁结果构造成一个独立的 specialist 项，追加到 specialist_results 末尾。

    让前端 ChatMessage.vue 的专家列表能直接展示「⚖️ 仲裁 Agent」卡片，
    而不是只把仲裁埋在 metadata.arbitration 字段里看不到。

    修复 conv 127 用户反馈："仲裁 agent 为什么不是以 agent 总结的形式出现呢"
    """
    if not arbitration or not isinstance(arbitration, dict):
        return None
    arb_verdict = arbitration.get("verdict", "未裁决")
    arb_confidence = arbitration.get("confidence", "")
    arb_conflicts = arbitration.get("key_conflicts", []) or []
    arb_reasoning = (arbitration.get("reasoning", "") or "")[:1500]

    lines = [
        "### 仲裁裁决",
        "",
        f"- **裁决**: {arb_verdict}",
        f"- **置信度**: {arb_confidence}",
        "",
    ]
    if arb_conflicts:
        lines.append("#### 关键分歧")
        for c in arb_conflicts:
            if isinstance(c, dict):
                note = c.get("note", "")
                buy = c.get("buy_side", [])
                opp = c.get("opposing_side", [])
                lines.append(f"- {note}：看多 {buy} vs 看空/持有 {opp}")
        lines.append("")
    if arb_reasoning:
        lines.append("#### 推理依据")
        lines.append("")
        for piece in arb_reasoning.split(" | "):
            if piece.strip():
                lines.append(f"- {piece.strip()[:300]}")
    return {
        "agent_key": "arbitrator",
        "agent": "仲裁 Agent",
        "icon": "⚖️",
        "analysis": "\n".join(lines),
    }


# ── 请求模型 ─────────────────────────────────────────────


class CreateConversationRequest(BaseModel):
    title: str = "新对话"
    agent_id: int = None
    context_data: str = None


class SendMessageRequest(BaseModel):
    content: str
    target_specialists: list[str] = []  # @mention 指定的 agent_key 列表
    images: list[dict] = []  # 上传的图片信息 [{image_id, url, parse_status, parse_result}]


class ChatFeedbackRequest(BaseModel):
    message_id: int = None
    feedback: str  # "helpful" or "unhelpful"
    note: str = ""
    input_summary: str = ""
    output_summary: str = ""


# ── 对话 CRUD ─────────────────────────────────────────────


@router.get("/api/conversations")
async def list_conversations_api(page: int = 1, page_size: int = 50):
    """对话列表。"""
    all_convs = list_conversations()
    if page and page_size:
        start = (page - 1) * page_size
        return {"conversations": all_convs[start:start+page_size]}
    return {"conversations": all_convs}


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
async def get_messages_api(conv_id: int, limit: int = 50, offset: int = 0):
    """获取对话消息历史（支持分页）。"""
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")
    msgs = get_messages(conv_id, limit, offset)
    # 解析 metadata JSON 字符串为 dict
    for msg in msgs:
        if msg.get("metadata") and isinstance(msg["metadata"], str):
            try:
                msg["metadata"] = json.loads(msg["metadata"])
            except Exception:
                pass
    return {"conversation": conv, "messages": msgs}


@router.post("/api/conversations/{conv_id}/cancel")
async def cancel_conversation_execution(conv_id: int):
    """客户端通知取消执行：
    1. 设置 cancel_event 让后台生产者线程在下一个检查点真正停止（_check_cancel 抛 CancelledError）
    2. 同时把 streaming 状态的消息标记为 cancelled（前端可见）
    """
    # 1. 通过 cancel_event 真正中断后台任务（之前的版本只标记 DB 状态，后台任务仍会跑完）
    agent_info = _running_agents.get(f"prod_{conv_id}")
    if agent_info and agent_info.get("cancel_event"):
        agent_info["cancel_event"].set()
        logger.info(f"取消对话 {conv_id}：已设置 cancel_event，后台任务将在下一个检查点停止")

    # 1.5 持久化取消标记（刷新页面后仍能识别，防止自动 resume）
    try:
        from db.conversations import mark_conversation_cancelled
        mark_conversation_cancelled(conv_id)
        logger.info(f"取消对话 {conv_id}：已持久化 cancel_requested 标记")
    except Exception as e:
        logger.warning(f"持久化取消标记失败: {e}")

    # 2. 标记消息 DB 状态为 cancelled
    msgs = get_messages(conv_id, limit=5)
    updated = 0
    for msg in reversed(msgs):
        meta = msg.get("metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                continue
        if isinstance(meta, dict) and meta.get("execution_status") == "streaming":
            meta["execution_status"] = "cancelled"
            try:
                update_message_metadata(msg["id"], meta)
                updated += 1
            except Exception as e:
                logger.warning(f"标记取消状态失败: {e}")

    # 3. 标记 agent_runs 表中 running/pending 的记录为 cancelled
    #    （之前漏了这步，导致取消后前端仍显示"运行中"的 agent）
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        cur = conn.execute('''
            UPDATE agent_runs
            SET status = 'cancelled', completed_at = datetime('now','localtime')
            WHERE conversation_id = ? AND status IN ('running', 'pending')
        ''', (conv_id,))
        agent_runs_updated = cur.rowcount
        conn.commit()
        conn.close()
        if agent_runs_updated:
            logger.info(f"取消对话 {conv_id}：已标记 {agent_runs_updated} 条 agent_runs 为 cancelled")
    except Exception as e:
        logger.warning(f"标记 agent_runs 取消状态失败: {e}")

    return {"ok": True, "updated": updated}


@router.post("/api/conversations/{conv_id}/clear-cancel")
async def clear_cancel_flag(conv_id: int):
    """清除取消标记（用户点击重试/继续分析时调用）。

    resume 接口对 cancel_requested=true 的对话返回 409，用户主动重试时需先清除标记。
    """
    from db.conversations import clear_conversation_cancel_flag
    clear_conversation_cancel_flag(conv_id)
    logger.info(f"清除对话 {conv_id} 的取消标记（用户主动重试）")
    return {"ok": True}


@router.post("/api/conversations/{conv_id}/resume")
async def resume_conversation(conv_id: int, request: Request):
    """[DEPRECATED] 恢复中断的对话执行，跳过已完成的专家。

    已被 GET /api/conversations/{conv_id}/replay 替代。
    保留一段时间兼容旧前端缓存，新前端不应调用此接口。
    """
    from db.agents import get_completed_agents_for_message

    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")

    # 守卫1：已取消的对话不自动恢复（用户主动取消后刷新页面，应看到取消状态而非自动重试）
    from db.conversations import get_conversation_cancel_status
    if get_conversation_cancel_status(conv_id):
        logger.info(f"恢复对话 {conv_id}：检测到 cancel_requested=true，拒绝自动恢复")
        raise HTTPException(409, "对话已取消，不会自动恢复。如需重试请点击重试按钮。")

    # 守卫2：失败状态的对话不自动恢复（需要用户显式点击重试）
    # 检查最后一条 assistant 消息是否为 failed 状态
    msgs_check = get_messages(conv_id, limit=5)
    for msg in reversed(msgs_check):
        if msg["role"] == "assistant":
            meta = msg.get("metadata")
            if isinstance(meta, str):
                try:
                    import json as _json
                    meta = _json.loads(meta)
                except Exception:
                    meta = {}
            if isinstance(meta, dict) and meta.get("execution_status") == "failed":
                logger.info(f"恢复对话 {conv_id}：最后一条 assistant 消息为 failed，拒绝自动恢复")
                raise HTTPException(409, "对话上次执行失败，不会自动恢复。如需重试请点击重试按钮。")
            break  # 只检查最后一条 assistant 消息

    # 获取消息
    msgs = get_messages(conv_id)
    if not msgs:
        raise HTTPException(404, "对话无消息")

    # 找到最后一条 assistant 消息（中断的）
    last_assistant = None
    original_query = None
    for msg in reversed(msgs):
        if msg["role"] == "assistant" and not last_assistant:
            meta = msg.get("metadata")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            if isinstance(meta, dict) and meta.get("execution_status") in ("streaming", "cancelled", "failed"):
                last_assistant = msg
                last_assistant["_parsed_metadata"] = meta
        elif msg["role"] == "user" and last_assistant:
            original_query = msg["content"]
            break

    if not last_assistant or not original_query:
        # 没有 assistant 回复 → 检查最后一条是否是用户消息，从零重新执行
        last_msg = msgs[-1] if msgs else None
        if last_msg and last_msg["role"] == "user" and not original_query:
            original_query = last_msg["content"]
            logger.info(f"恢复对话 {conv_id}：检测到用户消息无回复（消息 {last_msg['id']}），从零重新执行")
            # 创建 assistant 占位消息，假装是"中断的"
            stream_msg_id = create_message(conv_id, "assistant", "⏳ 分析进行中...",
                metadata=json.dumps({"execution_status": "pending"}, ensure_ascii=False))
            last_assistant = {"id": stream_msg_id, "role": "assistant", "_parsed_metadata": {"execution_status": "pending"}}
            completed_runs = []
        else:
            raise HTTPException(400, "没有可恢复的中断对话")

    # 从 agent_runs 表查询已完成的专家
    message_id = last_assistant["id"]
    completed_runs = get_completed_agents_for_message(message_id, run_phase='primary')

    # ★ 如果是失败消息（failed）且本消息没有 agent_runs，
    # 回退到查询该消息的 retry_of_message_id 的已完成专家
    if not completed_runs:
        meta = last_assistant.get("_parsed_metadata", {})
        retry_of = meta.get("retry_of_message_id")
        if retry_of:
            completed_runs = get_completed_agents_for_message(retry_of, run_phase='primary')
            if completed_runs:
                logger.info(f"恢复对话 {conv_id}：从 retry_of_message_id={retry_of} 找到 {len(completed_runs)} 个已完成专家")
    elif last_assistant.get("_parsed_metadata", {}).get("execution_status") == "failed":
        logger.info(f"恢复对话 {conv_id}：消息 {message_id} 为失败状态，找到 {len(completed_runs)} 个已完成专家")
    else:
        logger.info(f"恢复对话 {conv_id}：找到 {len(completed_runs)} 个已完成的专家")

    logger.info(f"恢复对话 {conv_id}：找到 {len(completed_runs)} 个已完成的专家")

    # 检查是否已有正在运行的任务 —— 如果有，启动监控模式，不重新执行
    running_trace_ids = [k for k, v in _running_agents.items() if v.get("conv_id") == conv_id]
    if running_trace_ids:
        logger.info(f"恢复对话 {conv_id}：检测到正在运行的任务 {running_trace_ids}，启动监控模式")

        async def _monitor_stream():
            stream_msg_id = last_assistant["id"]
            metadata = last_assistant.get("_parsed_metadata", {})
            last_spec_count = len(completed_runs)
            last_cross_count = len(metadata.get("cross_review_results", []))

            yield _sse_event("status", {"message": f"检测到后台执行中的任务（{last_spec_count} 个专家已完成），正在恢复连接..."})

            # 推送已有的专家结果（让前端恢复状态）
            for run in completed_runs:
                yield _sse_event("specialist_done", {
                    "agent_key": run.get("agent_key", ""),
                    "agent": run.get("agent_name", ""),
                    "icon": "🤖",
                    "analysis": run.get("result", ""),
                    "duration_ms": run.get("duration_ms", 0),
                })

            # 轮询监控数据库进度
            max_polls = 150  # 最多等待 5 分钟
            for _ in range(max_polls):
                await asyncio.sleep(2)

                msgs = get_messages(conv_id, limit=1)
                if not msgs or msgs[0]["id"] != stream_msg_id:
                    yield _sse_event("error", {"message": "消息状态异常"})
                    return

                meta = msgs[0].get("metadata")
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except Exception:
                        meta = {}

                status = meta.get("execution_status", "unknown")

                # 推送新增的专家结果
                spec_results = meta.get("specialist_results", [])
                if len(spec_results) > last_spec_count:
                    for i in range(last_spec_count, len(spec_results)):
                        s = spec_results[i]
                        yield _sse_event("specialist_done", {
                            "agent_key": s.get("agent_key", ""),
                            "agent": s.get("agent", ""),
                            "icon": s.get("icon", "🤖"),
                            "analysis": s.get("analysis", ""),
                            "duration_ms": s.get("duration_ms", 0),
                        })
                    last_spec_count = len(spec_results)

                # 推送新增的交叉审阅结果
                cross_results = meta.get("cross_review_results", [])
                if len(cross_results) > last_cross_count:
                    for i in range(last_cross_count, len(cross_results)):
                        s = cross_results[i]
                        yield _sse_event("cross_review_done", {
                            "agent_key": s.get("agent_key", ""),
                            "agent": s.get("agent", ""),
                            "icon": s.get("icon", "🤖"),
                            "analysis": s.get("analysis", ""),
                            "duration_ms": s.get("duration_ms", 0),
                        })
                    last_cross_count = len(cross_results)

                # 推送进度
                total_expected = 3 if meta.get("complexity") == "simple" else (4 if meta.get("complexity") == "medium" else 6)
                if total_expected > 0 and last_spec_count > 0:
                    pct = min(35 + (last_spec_count * 10), 95)
                    yield _sse_event("progress", {
                        "phase": "specialists",
                        "phase_index": 4,
                        "total_phases": total_expected,
                        "phase_label": "专家分析",
                        "substep": f"已完成 {last_spec_count} 个专家",
                        "pct": pct,
                    })

                # 任务结束
                if status == "completed":
                    yield _sse_event("answer", {
                        "content": msgs[0].get("content", ""),
                        "specialist_results": spec_results,
                    })
                    total_ms = meta.get("phase_timings", {}).get("total_ms", 0)
                    yield _sse_event("done", {
                        "message_id": stream_msg_id,
                        "duration_ms": total_ms,
                        "phase_timings": meta.get("phase_timings", {}),
                    })
                    return
                elif status == "failed":
                    yield _sse_event("error", {"message": meta.get("error_message", "执行失败")})
                    return
                elif status == "cancelled":
                    yield _sse_event("cancelled", {"message": "执行已取消"})
                    return

            yield _sse_event("error", {"message": "等待任务完成超时，请刷新页面查看最新结果"})

        return StreamingResponse(_monitor_stream(), media_type="text/event-stream")

    # 如果没有已完成的专家，将消息状态重置为待执行
    if not completed_runs:
        update_message_metadata(message_id, {"execution_status": "pending"})
        logger.info(f"恢复对话 {conv_id}：无已完成专家，重新执行整个任务")

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
        import threading

        cancel_event = threading.Event()
        request_start = time.time()
        phase_timings = {}
        trace_id = str(uuid.uuid4())[:12]
        logger.info(f"[trace:{trace_id}] 恢复对话 {conv_id}: {original_query[:50]}...")

        # 更新状态为 resuming
        metadata = last_assistant.get("_parsed_metadata", {})
        metadata["execution_status"] = "resuming"
        update_message_metadata(last_assistant["id"], metadata)
        stream_msg_id = last_assistant["id"]

        yield _sse_event("status", {"message": f"正在恢复执行（{len(completed_runs)} 个专家已完成）..."})

        # RAG 检索（不检查断开连接，让后端任务继续执行）
        def _run_rag():
            try:
                return build_rag_context_with_details(original_query, content_types=rag_types if rag_types else None)
            except Exception as e:
                logger.warning(f"[trace:{trace_id}] RAG 检索失败，跳过: {e}")
                return {"context": "", "results": [], "keywords": [], "query": original_query, "fts_count": 0, "chroma_count": 0, "freshness_filtered": 0}

        rag_result = await asyncio.to_thread(_run_rag)
        rag_context = rag_result["context"]

        # RAG 空结果兜底（同上）
        if not rag_context:
            _rag_trigger_keywords = ["政策", "原因", "为什么", "分析", "新闻", "暴跌", "暴涨",
                                      "利好", "利空", "行情", "走势", "趋势", "预测", "影响"]
            if any(kw in original_query for kw in _rag_trigger_keywords):
                def _run_rag2():
                    try:
                        return build_rag_context_with_details(original_query, content_types=None, limit=5)
                    except Exception:
                        return {"context": ""}
                rag_result2 = await asyncio.to_thread(_run_rag2)
                if rag_result2.get("context"):
                    rag_context = rag_result2["context"]
                    rag_result = rag_result2

        # 获取历史
        history = get_messages(conv_id, limit=20)
        msg_list = [{"role": m["role"], "content": m["content"]} for m in history if m["id"] != stream_msg_id]

        # 恢复执行，传递 message_id
        resume_data = {
            "message_id": message_id,
            "original_query": original_query,
        }

        def _run_resume_stream():
            try:
                for event in orchestrate_stream(original_query, msg_list, rag_context, cancel_event, resume_from=resume_data, conversation_id=conv_id, message_id=message_id, trace_id=trace_id):
                    _running_agents[trace_id] = {"conv_id": conv_id, "cancel": cancel_event}
                    yield event
            except CancelledError:
                yield {"type": "cancelled", "message": "执行已取消"}
            except Exception as e:
                logger.error(f"[trace:{trace_id}] 恢复执行异常: {e}", exc_info=True)
                yield {"type": "error", "message": str(e)}
            finally:
                _running_agents.pop(trace_id, None)

        # 消费事件流
        specialist_results_so_far = []
        tool_calls_so_far = []
        final_content = ""
        final_complexity = metadata.get("complexity", "medium")

        client_disconnected = False
        for event in _run_resume_stream():
            # 不检查断开连接，让后端任务继续执行
            if not client_disconnected and await request.is_disconnected():
                client_disconnected = True
                # 不设置 cancel_event，让任务继续执行

            event_type = event.get("type")

            if event_type == "status":
                if not client_disconnected:
                    yield _sse_event("status", event)
            elif event_type == "plan":
                if not client_disconnected:
                    yield _sse_event("plan", event)
            elif event_type == "specialist_start":
                if not client_disconnected:
                    yield _sse_event("specialist_start", event)
            elif event_type == "specialist_done":
                agent_key = event.get("agent_key")
                # 更新或添加结果
                existing_idx = next(
                    (i for i, sr in enumerate(specialist_results_so_far) if sr.get("agent_key") == agent_key),
                    None
                )
                result_item = {
                    "agent_key": agent_key,
                    "agent": event.get("agent", ""),
                    "icon": event.get("icon", "🤖"),
                    "analysis": event.get("analysis", ""),
                    "duration_ms": event.get("duration_ms", 0),
                }
                if existing_idx is not None:
                    specialist_results_so_far[existing_idx] = result_item
                else:
                    specialist_results_so_far.append(result_item)

                update_message_metadata(stream_msg_id, {
                    "execution_status": "streaming",
                    "complexity": final_complexity,
                    "specialist_results": specialist_results_so_far,
                    "tool_calls": tool_calls_so_far,
                    "trace_id": trace_id,
                })
                if not client_disconnected:
                    yield _sse_event("specialist_done", event)
            elif event_type == "answer_chunk":
                final_content += event.get("content", "")
                if not client_disconnected:
                    yield _sse_event("answer_chunk", event)
            elif event_type == "answer":
                final_content = event.get("content", final_content)
                specialist_results_so_far = event.get("specialist_results", specialist_results_so_far)
                tool_calls_so_far = event.get("tool_calls", tool_calls_so_far)
                final_complexity = event.get("complexity", final_complexity)

                duration_ms = int((time.time() - request_start) * 1000)
                update_message_content_and_metadata(stream_msg_id, final_content, {
                    "execution_status": "completed",
                    "complexity": final_complexity,
                    "specialist_results": [
                        {"agent_key": s.get("agent_key", ""), "agent": s.get("agent", ""), "icon": s.get("icon", ""), "analysis": s.get("analysis", "")[:3000]}
                        for s in specialist_results_so_far
                    ],
                    "tool_calls": tool_calls_so_far,
                    "phase_timings": phase_timings,
                    "trace_id": trace_id,
                })

                if not client_disconnected:
                    yield _sse_event("answer", {
                        "content": final_content,
                        "specialist_results": specialist_results_so_far,
                        "complexity": final_complexity,
                        "duration_ms": duration_ms,
                    })
            elif event_type == "cancelled":
                update_message_metadata(stream_msg_id, {
                    "execution_status": "cancelled",
                    "complexity": final_complexity,
                    "specialist_results": specialist_results_so_far,
                    "tool_calls": tool_calls_so_far,
                    "trace_id": trace_id,
                })
                if not client_disconnected:
                    yield _sse_event("cancelled", event)
            elif event_type == "error":
                update_message_metadata(stream_msg_id, {
                    "execution_status": "failed",
                    "error_message": event.get("message", ""),
                    "specialist_results": specialist_results_so_far,
                    "trace_id": trace_id,
                })
                yield _sse_event("error", event)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/api/conversations/{conv_id}/execution-state")
async def get_conversation_execution_state_api(conv_id: int):
    """获取当前对话最近可恢复的 assistant 执行状态 + 关联 channel 信息。"""
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")
    item = get_latest_recoverable_assistant(conv_id)
    # 扩展：关联 channel 信息（支持前端 replay 续接）
    if item:
        channel = get_latest_channel_for_message(item["id"])
        if channel:
            item["channel_id"] = channel["channel_id"]
            item["channel_status"] = channel["status"]
            item["last_seq"] = channel["last_seq"]
        else:
            item["channel_id"] = None
            item["channel_status"] = None
            item["last_seq"] = 0
    return {"item": item, "has_recoverable": bool(item)}


@router.get("/api/conversations/{conv_id}/replay")
async def replay_conversation(conv_id: int, channel_id: str, last_seq: int = 0, request: Request = None):
    """回放续接 SSE 流——切回页面时续接 channel 事件流。

    - channel 已结束（completed/failed/aborted/cancelled）→ 回放 last_seq 之后事件 + replay_end，关闭流
    - channel running 且心跳超时 → 标记 aborted + 推 error，关闭流
    - channel running 且心跳正常 → 回放 last_seq 之后事件 → 长连接订阅新事件
    """
    channel = get_stream_channel(channel_id)
    if not channel or channel["conversation_id"] != conv_id:
        raise HTTPException(404, "channel 不存在")

    async def event_stream():
        # 1. 回放历史事件（seq > last_seq）
        events = list_stream_events(channel_id, after_seq=last_seq)
        for ev in events:
            yield _sse_event_with_seq(ev["event_type"], ev["data"], ev["seq"])

        # 2. 重新查 channel 状态
        channel = get_stream_channel(channel_id)
        if not channel:
            yield _sse_event("error", {"message": "channel 不存在"})
            return

        status = channel["status"]

        # 3. 已结束 → 推 replay_end 关闭
        if status != "running":
            yield _sse_event("replay_end", {"status": status})
            return

        # 4. running 但心跳超时 → 标记 aborted + 同步恢复 message
        if is_stream_heartbeat_stale(channel_id, threshold_sec=15):
            mark_stream_channel_aborted(channel_id, "heartbeat timeout")
            # 立即恢复占位符 message，避免长期悬挂（不依赖后端重启）
            try:
                from services.conv_recovery import recover_message
                if channel.get("message_id"):
                    recover_message(channel["message_id"])
            except Exception as e:
                logger.warning(f"[replay] recover_message 失败: {e}")
            yield _sse_event("error", {"message": "任务中断，请点击重试"})
            yield _sse_event("replay_end", {"status": "aborted"})
            return

        # 5. running 且心跳正常 → 长连接订阅新事件
        current_seq = events[-1]["seq"] if events else last_seq
        max_polls = 1500  # 最多等待 50 分钟（2s × 1500）
        for _ in range(max_polls):
            await asyncio.sleep(2)
            # 检测客户端断开
            if await request.is_disconnected():
                return
            # 拉新事件
            new_events = list_stream_events(channel_id, after_seq=current_seq)
            for ev in new_events:
                yield _sse_event_with_seq(ev["event_type"], ev["data"], ev["seq"])
                current_seq = ev["seq"]
            # 检查 channel 状态
            channel = get_stream_channel(channel_id)
            if not channel or channel["status"] != "running":
                status = channel["status"] if channel else "unknown"
                yield _sse_event("replay_end", {"status": status})
                return
            # 心跳超时检测
            if is_stream_heartbeat_stale(channel_id, threshold_sec=15):
                mark_stream_channel_aborted(channel_id, "heartbeat timeout")
                # 立即恢复占位符 message
                try:
                    from services.conv_recovery import recover_message
                    if channel.get("message_id"):
                        recover_message(channel["message_id"])
                except Exception as e:
                    logger.warning(f"[replay] recover_message 失败: {e}")
                yield _sse_event("error", {"message": "任务中断，请点击重试"})
                yield _sse_event("replay_end", {"status": "aborted"})
                return

        yield _sse_event("error", {"message": "等待任务完成超时"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _sse_event_with_seq(event_type: str, data: dict, seq: int) -> str:
    """格式化带 seq 的 SSE 事件（replay 专用）。"""
    return f"data: {json.dumps({'type': event_type, 'data': data, 'seq': seq}, ensure_ascii=False)}\n\n"


@router.post("/api/conversations/{conv_id}/continue")
async def continue_conversation_api(conv_id: int, request: Request):
    """继续分析：复用现有 assistant 消息并跳过已完成专家。"""
    item = get_latest_recoverable_assistant(conv_id)
    if not item:
        raise HTTPException(400, "没有可继续的对话任务")
    mark_message_execution_status(item["id"], "resuming")
    return await resume_conversation(conv_id, request)


@router.post("/api/conversations/{conv_id}/retry-message/{message_id}")
async def retry_conversation_message_api(conv_id: int, message_id: int):
    """重新生成：创建新的 assistant 占位消息，保留原失败/取消记录。"""
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")
    messages = get_messages(conv_id, limit=200)
    target = next((m for m in messages if m["id"] == message_id and m["role"] == "assistant"), None)
    if not target:
        raise HTTPException(404, "助手消息不存在")
    retry_id = retry_assistant_message(message_id)
    if not retry_id:
        raise HTTPException(400, "无法创建重试消息")

    original_query = ""
    target_index = next((i for i, m in enumerate(messages) if m["id"] == message_id), -1)
    for msg in reversed(messages[:target_index]):
        if msg.get("role") == "user":
            original_query = msg.get("content") or ""
            break
    if not original_query:
        raise HTTPException(400, "找不到原始用户消息")
    return {"ok": True, "message_id": retry_id, "original_query": original_query}


@router.post("/api/conversations/{conv_id}/messages")
async def send_message_api(conv_id: int, req: SendMessageRequest):
    """发送消息并获取 AI 回复（多 Agent 协作模式）。"""
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")

    # 生成 trace_id（提前生成，便于 RAG/标题更新等早期阶段也能关联日志）
    trace_id = str(uuid.uuid4())[:12]

    # 1. 存储用户消息（去重：中断重试时不重复保存）
    existing = get_messages(conv_id, limit=1)
    if not existing or existing[-1]["role"] != "user" or existing[-1]["content"] != req.content:
        user_metadata = json.dumps({"images": req.images}, ensure_ascii=False) if req.images else None
        create_message(conv_id, "user", req.content, metadata=user_metadata)

    # 构造 effective_query（包含图片上下文）
    effective_query = _build_effective_query(req.content, req.images)

    # 1.1 首次对话时立即更新标题（异步，不阻塞）
    if conv.get("title") == "新对话":
        history_count = len(get_messages(conv_id, limit=2))
        if history_count <= 1:
            short_title = req.content[:30] + ("..." if len(req.content) > 30 else "")
            try:
                update_conversation(conv_id, title=short_title)
            except Exception as e:
                logger.warning(f"更新对话标题失败: {e}")

    # 2. RAG 检索
    agent = get_agent(conv["agent_id"]) if conv.get("agent_id") else None
    rag_types = []
    if agent and agent.get("knowledge_scope"):
        try:
            scope = json.loads(agent["knowledge_scope"]) if isinstance(agent["knowledge_scope"], str) else agent["knowledge_scope"]
            rag_types = scope.get("rag_types", [])
        except (json.JSONDecodeError, TypeError):
            pass

    try:
        rag_result = build_rag_context_with_details(effective_query, content_types=rag_types if rag_types else None)
    except Exception as e:
        logger.warning(f"[trace:{trace_id}] RAG 检索失败，跳过: {e}")
        rag_result = {"context": "", "results": [], "keywords": [], "query": effective_query, "fts_count": 0, "chroma_count": 0, "freshness_filtered": 0}
    rag_context = rag_result["context"]

    # RAG 空结果兜底：政策/原因/新闻/分析类问题强制二次检索（全类型）
    if not rag_context:
        _rag_trigger_keywords = ["政策", "原因", "为什么", "分析", "新闻", "暴跌", "暴涨",
                                  "利好", "利空", "行情", "走势", "趋势", "预测", "影响",
                                  "金价", "降息", "加息", "设备更新", "新能源", "AI"]
        if any(kw in effective_query for kw in _rag_trigger_keywords):
            try:
                logger.info(f"RAG 空结果兜底：查询含政策/新闻关键词，全类型二次检索")
                rag_result2 = build_rag_context_with_details(effective_query, content_types=None, limit=5)
                if rag_result2.get("context"):
                    rag_context = rag_result2["context"]
                    rag_result = rag_result2
                    logger.info(f"RAG 兜底命中：{rag_result2.get('fts_count',0)} FTS + {rag_result2.get('chroma_count',0)} Chroma")
            except Exception as e:
                logger.warning(f"RAG 兜底检索失败: {e}")

    # 3. 获取对话历史
    history = get_messages(conv_id, limit=20)
    msg_list = [{"role": m["role"], "content": m["content"]} for m in history]

    # 3.5 注入组合约束（Portfolio Fact Layer）
    try:
        from services.portfolio_fact_layer import build_portfolio_facts as _bpf
        _pf = _bpf()
        _cs = _pf.get("constraints", {})
        _sn = _pf.get("snapshot", {})
        _ras = _pf.get("recent_analyses", [])
        _constraint_block = f"""## ⚠️ 组合约束（多智能体协同统一规则，最高优先级）

1. **集中度红线**：以下基金已超25%集中度，禁止建议加仓：
{chr(10).join(f'   - {t}' for t in _cs.get('no_add_targets', []))}

2. **债市温度**：{_cs.get('bond_temperature', '?')}°，{'偏贵，不建议加仓债基' if _cs.get('bond_expensive') else '正常'}

3. **组合概况**：总市值 {_sn.get('total_value', '?')}元，债基占比 {_sn.get('bond_pct', '?')}%

4. **最近分析**：
{chr(10).join(f'   - [{a.get("type","")}] {a.get("conclusion","")[:200]}...' for a in _ras)}

---
"""
        rag_context = _constraint_block + "\n" + (rag_context or "")
    except Exception as _e:
        logger.warning(f"注入组合约束失败: {_e}")

    # Bridge A: 注入24h分析结论上下文
    try:
        from agent.orchestrator import _inject_analysis_context
        _, _analysis_ctx = _inject_analysis_context(effective_query)
        if _analysis_ctx:
            rag_context = _analysis_ctx + "\n" + (rag_context or "")
    except Exception as _e:
        logger.warning(f"注入分析结论上下文失败: {_e}")

    # 4. 调用 Orchestrator（多 Agent 协作）
    logger.info(f"[trace:{trace_id}] 非流式对话 {conv_id}: {effective_query[:50]}...")
    try:
        llm_result = orchestrate(effective_query, msg_list, rag_context, conversation_id=conv_id, trace_id=trace_id)
        answer = llm_result["answer"]
    except Exception as e:
        answer = f"AI 回复失败: {str(e)}"
        llm_result = {"answer": answer, "specialist_results": [], "tool_calls": [], "turns": 0}

    # 5. 存储 AI 回复
    specialist_results = llm_result.get("specialist_results", [])
    metadata_dict = {
        "specialist_results": [
            {"agent_key": s.get("agent_key", ""), "agent": s.get("agent", ""), "icon": s.get("icon", ""), "analysis": s.get("analysis", "")[:3000]}
            for s in specialist_results
        ],
        "tool_calls": llm_result.get("tool_calls", []),
        "reasoning_trail": llm_result.get("reasoning_trail"),
    }
    metadata = json.dumps(metadata_dict, ensure_ascii=False) if specialist_results else None
    msg_id = create_message(conv_id, "assistant", answer, metadata=metadata)

    # 5.5. 基金代码幻觉验证（事后异步校验，不阻塞回复）
    try:
        from agent.hallucination_guard import quick_check_fund_codes
        codes = quick_check_fund_codes(answer)
        if codes:
            logger.info(f"回复含 {len(codes)} 个基金代码，触发幻觉校验: {codes[:10]}")
            asyncio.create_task(
                _async_verify_and_log(conv_id, msg_id, answer)
            )
    except Exception as e:
        logger.debug(f"幻觉校验钩子失败（不影响主流程）: {e}")

    # 6. 记录 RAG 日志
    log_rag_search(
        conversation_id=conv_id,
        message_id=msg_id,
        query=effective_query,
        keywords=rag_result.get("keywords", []),
        results=rag_result.get("results", []),
        content_types=rag_types if rag_types else None,
        fts_count=rag_result.get("fts_count", 0),
        chroma_count=rag_result.get("chroma_count", 0),
        freshness_filtered=rag_result.get("freshness_filtered", 0),
    )

    # 7. 自动更新对话标题（如果还没有更新过）
    # 注意：标题更新已提前到消息处理开始时执行

    return {
        "answer": answer,
        "specialist_results": [
            {"agent_key": s.get("agent_key", ""), "agent": s.get("agent", ""), "icon": s.get("icon", ""), "analysis": s.get("analysis", "")}
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

    # 防重复发送：检查是否有进行中的 agent 任务
    from db.agents import get_running_agent_count
    running_count = get_running_agent_count(conv_id)
    if running_count > 0:
        logger.warning(f"对话 {conv_id} 有 {running_count} 个进行中的任务，拒绝重复请求")
        raise HTTPException(409, f"该对话有 {running_count} 个进行中的任务，请等待完成后再试")

    # 防重复编排：如果最后一条 user 消息内容相同且已有 completed agent_runs，说明编排已跑过
    existing_msgs = get_messages(conv_id, limit=10)
    if existing_msgs:
        # 找最后一条 user 消息
        last_user_msg = None
        for m in reversed(existing_msgs):
            if m["role"] == "user":
                last_user_msg = m
                break
        if last_user_msg and last_user_msg["content"] == req.content:
            # 查该 user 消息之后是否有 assistant 消息带 completed agent_runs
            from db.agents import get_completed_agent_count_for_message
            for m in existing_msgs:
                if m["role"] == "assistant" and m["id"] > last_user_msg["id"]:
                    completed = get_completed_agent_count_for_message(conv_id, m["id"])
                    if completed > 0:
                        # 例外：如果 assistant 回答是错误占位文本，允许重发
                        # （Pipeline 失败但未正确降级时可能出现）
                        _error_placeholders = {"无法生成分析", "分析失败", "", None}
                        if m.get("content") in _error_placeholders:
                            logger.info(f"对话 {conv_id} assistant msg={m['id']} 内容为错误占位，允许重发")
                        else:
                            logger.info(f"对话 {conv_id} user消息 {last_user_msg['id']} 已有 {completed} 个完成的 agent_runs（assistant msg={m['id']}），跳过重复编排")
                            raise HTTPException(409, "该问题已处理完成，请勿重复发送。如需重新分析，请点击「重新生成」按钮")

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
        import threading

        cancel_event = threading.Event()
        request_start = time.time()
        phase_timings = {}
        error_category = "none"
        client_disconnected = False  # 标记客户端是否断开

        # 生成 trace_id，关联本次对话的所有事件
        trace_id = str(uuid.uuid4())[:12]

        # 构造 effective_query（包含图片上下文）
        effective_query = _build_effective_query(req.content, req.images)

        logger.info(f"[trace:{trace_id}] 对话 {conv_id} 开始: {effective_query[:50]}...")

        # 执行 before_prompt hooks
        try:
            from agent.hooks import run_hooks
            from agent.session_signals import detect_signals, get_adaptive_behavior
            hook_context = {
                "conversation_id": conv_id,
                "query": effective_query,
                "user_id": "default",
            }
            hook_context = run_hooks("before_prompt", hook_context)

            # 检测会话信号
            detect_signals(conv_id, "user_message", {"content": effective_query})
            adaptive = get_adaptive_behavior(conv_id)
            if adaptive.get("should_inject_context"):
                hook_context["system_prompt"] = (hook_context.get("system_prompt", "") +
                    f"\n\n<adaptive_context>{adaptive['should_inject_context']}</adaptive_context>")
        except Exception as e:
            logger.warning(f"Hooks 执行失败: {e}")

        # 1. 存储用户消息（去重：中断重试时不重复保存）
        existing = get_messages(conv_id, limit=1)
        if not existing or existing[-1]["role"] != "user" or existing[-1]["content"] != req.content:
            user_metadata = json.dumps({"images": req.images}, ensure_ascii=False) if req.images else None
            user_msg_id = create_message(conv_id, "user", req.content, metadata=user_metadata)
        else:
            user_msg_id = existing[-1]["id"]
        yield _sse_event("user_message", {"content": req.content})

        # KYC 画像对话中持续学习（异步后台，不阻塞主流程；仅关键词命中时触发）
        try:
            from agent.kyc_learner import should_extract, learn_from_message
            if should_extract(effective_query):
                asyncio.create_task(asyncio.to_thread(learn_from_message, effective_query, "default"))
        except Exception as e:
            logger.debug(f"KYC 学习触发跳过: {e}")

        # 1.1 首次对话时立即更新标题
        if conv.get("title") == "新对话":
            history_count = len(get_messages(conv_id, limit=2))
            if history_count <= 1:
                short_title = req.content[:30] + ("..." if len(req.content) > 30 else "")
                try:
                    update_conversation(conv_id, title=short_title)
                    yield _sse_event("title_updated", {"title": short_title})
                except Exception as e:
                    logger.warning(f"更新对话标题失败: {e}")

        # 2. 并行执行：需求澄清 + RAG 检索（节省 0.5-2s）
        # 不检查断开连接，让后端任务继续执行
        yield _sse_event("status", {"message": "正在理解问题并检索知识库..."})

        def _run_clarification():
            return clarify_requirement(effective_query)

        def _run_rag():
            try:
                return build_rag_context_with_details(effective_query, content_types=rag_types if rag_types else None)
            except Exception as e:
                logger.warning(f"[trace:{trace_id}] RAG 检索失败，跳过: {e}")
                return {"context": "", "results": [], "keywords": [], "query": effective_query, "fts_count": 0, "chroma_count": 0, "freshness_filtered": 0}

        t0 = time.time()
        clarification_task = asyncio.to_thread(_run_clarification)
        rag_task = asyncio.to_thread(_run_rag)

        # 等待两者都完成
        clarification, rag_result = await asyncio.gather(clarification_task, rag_task)
        phase_timings["clarification_rag_ms"] = int((time.time() - t0) * 1000)
        complexity = clarification["complexity"]
        rag_context = rag_result["context"]

        # 升级五：多跳检索增强（检测到多跳意图时，追加多跳上下文）
        try:
            from agent.multi_hop_rag import multi_hop_search
            from db.portfolio import list_holdings
            from services.rag import search_knowledge

            def _mh_rag_fn(q, limit):
                return search_knowledge(q, limit=limit)

            def _mh_portfolio_fn():
                return list_holdings()

            mh = await asyncio.to_thread(multi_hop_search, effective_query, _mh_rag_fn, _mh_portfolio_fn)
            if mh and mh.get("context"):
                rag_context = (rag_context + "\n\n" + mh["context"]) if rag_context else mh["context"]
                logger.info(f"[multi_hop] 增强上下文 {len(mh['hops'])} 跳，模板={mh['template']}")
        except Exception as _mh_e:
            logger.debug(f"多跳检索跳过: {_mh_e}")

        scenario_type = clarification.get("scenario_type", "general_analysis")
        # 用 asyncio.to_thread + wait_for 包装，避免 _build_unified_context_safe 内部
        # 同步阻塞调用（如 ak.stock_margin_sse 无 timeout）卡死整个事件循环。
        # 案例：conv 122 trace=7b36d072 因 akshare 卡住导致 11 分钟无任何日志，
        # 连 alert_scanner（asyncio.create_task）都无法运行。
        try:
            unified_context = await asyncio.wait_for(
                asyncio.to_thread(
                    _build_unified_context_safe,
                    conv_id=conv_id,
                    query=effective_query,
                    scenario_type=scenario_type,
                    agent_id=conv.get("agent_id"),
                    rag_context=rag_context,
                    token_budget=get_config_int("llm.max_tokens_orchestrator", 6000),
                ),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[trace:{trace_id}] _build_unified_context_safe 超时 30s（可能 akshare/外部接口卡住），跳过统一上下文")
            unified_context = ""

        yield _sse_event("status", {"message": f"问题类型: {complexity} ({clarification.get('reason', '')})"})

        # 进度：澄清 + RAG 完成
        total_phases = 3 if complexity == "simple" else (4 if complexity == "medium" else 6)
        yield _sse_event("progress", {
            "phase": "clarification",
            "phase_index": 1,
            "total_phases": total_phases,
            "phase_label": "理解问题",
            "substep": None,
            "pct": 15,
        })

        # 发送 RAG 来源
        if rag_result.get("results"):
            sources = [{"type": r.get("label", r.get("content_type")), "title": r.get("title")} for r in rag_result["results"][:3]]
            yield _sse_event("rag_sources", {"sources": sources})
            # 进度：RAG 检索完成
            yield _sse_event("progress", {
                "phase": "rag",
                "phase_index": 2,
                "total_phases": total_phases,
                "phase_label": "知识检索",
                "substep": f"找到 {len(rag_result['results'])} 条相关知识",
                "pct": 25,
            })

        # 3. 普通聊天：直接调用 LLM 回答，不走专家流程
        if complexity == "chat" and not clarification.get("specialists"):
            # chat 路径也走 channel 事件流持久化（支持断线续接）
            stream_msg_id = create_message(conv_id, "assistant", "⏳ 分析进行中...", metadata=json.dumps({"execution_status": "streaming"}, ensure_ascii=False))
            channel_id = create_stream_channel(
                conversation_id=conv_id, message_id=stream_msg_id,
                user_message_id=user_msg_id, trace_id=trace_id, complexity="chat",
            )
            logger.info(f"[trace:{trace_id}] chat 路径创建 channel {channel_id} for msg={stream_msg_id}")
            if not client_disconnected:
                yield _sse_event("channel_started", {"channel_id": channel_id, "message_id": stream_msg_id})

            append_stream_event(channel_id, "status", {"message": "思考中..."})
            yield _sse_event("status", {"message": "思考中..."})
            # 获取对话历史（chat 类型需要）
            history = get_messages(conv_id, limit=20)
            msg_list = [{"role": m["role"], "content": m["content"]} for m in history]
            _progress_data = {
                "phase": "chat",
                "phase_index": 2,
                "total_phases": 3,
                "phase_label": "回答",
                "substep": None,
                "pct": 50,
            }
            append_stream_event(channel_id, "progress", _progress_data)
            yield _sse_event("progress", _progress_data)
            # 构建上下文
            chat_messages = [{"role": "system", "content": ORCHESTRATOR_PROMPT}]
            if unified_context:
                chat_messages.append({"role": "system", "content": f"统一个人理财上下文：\n{unified_context}"})
            for m in msg_list[-10:]:
                chat_messages.append({"role": m["role"], "content": m["content"][:500]})
            if rag_context:
                chat_messages.append({"role": "system", "content": f"相关知识库参考：\n{rag_context[:1000]}"})
            chat_messages.append({"role": "user", "content": effective_query})
            try:
                resp = await asyncio.to_thread(lambda: _call_llm(
                    caller="chat", model=MODEL, messages=chat_messages,
                    temperature=get_config_float("llm.temperature_default", 0.3),
                    max_tokens=get_config_int("llm.max_tokens_report", 8000),
                ))
                answer = resp.choices[0].message.content or "抱歉，我无法回答这个问题。"
                # 清理 reasoning_content
                if hasattr(resp.choices[0].message, 'reasoning_content') and resp.choices[0].message.reasoning_content:
                    answer = resp.choices[0].message.content or answer
            except Exception as e:
                logger.warning(f"[trace:{trace_id}] Chat LLM 调用失败: {e}")
                answer = "抱歉，处理您的问题时出现了错误，请重试。"

            # 输出结果
            _answer_data = {"content": answer, "specialist_results": []}
            append_stream_event(channel_id, "answer", _answer_data)
            yield _sse_event("answer", _answer_data)
            _done_data = {"duration_ms": int((time.time() - request_start) * 1000), "complexity": "chat"}
            append_stream_event(channel_id, "done", _done_data)
            yield _sse_event("done", _done_data)
            # 标记 channel 完成
            try:
                complete_stream_channel(channel_id)
            except Exception as _e:
                logger.warning(f"[trace:{trace_id}] 标记 chat channel 完成失败: {_e}")

            # 保存消息（更新占位消息内容）
            metadata = {"complexity": "chat", "execution_status": "completed"}
            update_message_content_and_metadata(stream_msg_id, answer, metadata)

            # 基金代码幻觉验证（异步）
            try:
                from agent.hallucination_guard import quick_check_fund_codes
                codes = quick_check_fund_codes(answer)
                if codes:
                    asyncio.create_task(_async_verify_and_log(conv_id, stream_msg_id, answer))
            except Exception:
                pass

            # 记录 RAG 日志
            if rag_result and rag_result.get("results"):
                try:
                    log_rag_search(
                        conversation_id=conv_id, message_id=user_msg_id, query=effective_query,
                        keywords=rag_result.get("keywords", []),
                        results=rag_result.get("results", []),
                        content_types=rag_types if rag_types else None,
                        fts_count=rag_result.get("fts_count", 0),
                        chroma_count=rag_result.get("chroma_count", 0),
                        freshness_filtered=rag_result.get("freshness_filtered", 0),
                    )
                except Exception as e:
                    logger.warning(f"chat RAG 日志记录失败: {e}")
            return

        # 4. 简单任务：直接路由到专家，跳过 Orchestrator
        if complexity == "simple" and len(clarification.get("specialists", [])) == 1:
            # 只需要1个专家，直接调用
            agent_key = clarification["specialists"][0]
            if agent_key:
                # simple 路径也走 channel 事件流持久化（支持断线续接）
                stream_msg_id = create_message(conv_id, "assistant", "⏳ 分析进行中...", metadata=json.dumps({"execution_status": "streaming"}, ensure_ascii=False))
                channel_id = create_stream_channel(
                    conversation_id=conv_id, message_id=stream_msg_id,
                    user_message_id=user_msg_id, trace_id=trace_id, complexity="simple",
                )
                logger.info(f"[trace:{trace_id}] simple 路径创建 channel {channel_id} for msg={stream_msg_id}")
                if not client_disconnected:
                    yield _sse_event("channel_started", {"channel_id": channel_id, "message_id": stream_msg_id})

                _status_data = {"message": f"正在咨询{_get_specialist_name(agent_key)}..."}
                append_stream_event(channel_id, "status", _status_data)
                yield _sse_event("status", _status_data)
                # 进度：开始专家分析
                _progress_data = {
                    "phase": "specialist",
                    "phase_index": 2,
                    "total_phases": 3,
                    "phase_label": "专家分析",
                    "substep": f"{_get_specialist_name(agent_key)} 分析中",
                    "pct": 50,
                }
                append_stream_event(channel_id, "progress", _progress_data)
                yield _sse_event("progress", _progress_data)

                # 直接运行专家（传递轻量级 RAG 上下文）
                def _run_expert():
                    try:
                        expert_query = clarification.get("refined_query") or effective_query
                        prebuilt = ""
                        if unified_context:
                            prebuilt += f"## 统一个人理财上下文\n{unified_context[:3500]}\n\n"
                        elif rag_context:
                            prebuilt += f"## 知识库参考（书籍/文章）\n{rag_context[:800]}\n\n"
                        if not unified_context:
                            try:
                                from services.portfolio_context import build_portfolio_context
                                prebuilt += f"## 用户当前持仓\n{build_portfolio_context()}\n\n"
                            except Exception:
                                pass
                        return run_specialist(agent_key, expert_query, prebuilt_context=prebuilt, trace_id=trace_id)
                    except Exception as e:
                        return {"error": str(e)}

                result = await asyncio.to_thread(_run_expert)

                if "error" not in result:
                    # 发送专家完成事件
                    _spec_done_data = {
                        "agent_key": result.get("agent_key", agent_key),
                        "agent": result.get("agent", ""),
                        "icon": result.get("icon", ""),
                        "analysis": result.get("analysis", ""),
                        "duration_ms": result.get("duration_ms", 0),
                    }
                    append_stream_event(channel_id, "specialist_done", _spec_done_data)
                    yield _sse_event("specialist_done", _spec_done_data)

                    # 记录专家执行到 agent_runs
                    create_agent_run(
                        conversation_id=conv_id, message_id=0,
                        agent_key=result.get("agent_key", agent_key),
                        agent_name=result.get("agent", ""),
                        query=effective_query[:500],
                        result=(result.get("analysis", "") or "")[:3000],
                        duration_ms=result.get("duration_ms", 0),
                        trace_id=trace_id,
                        status="success",
                    )

                    # 构建专家结果
                    answer = result.get("analysis", "")
                    specialist_results = [{
                        "agent_key": result.get("agent_key", agent_key),
                        "agent": result.get("agent", ""),
                        "icon": result.get("icon", ""),
                        "analysis": answer,
                        "duration_ms": result.get("duration_ms", 0),
                    }]
                    # 输出审核
                    review = review_output(answer, specialist_results)
                    if review["warnings"]:
                        logger.warning(f"[trace:{trace_id}] 输出审核警告: {review['warnings']}")
                    answer = review["content"]
                    specialist_results[0]["analysis"] = answer

                    _answer_data = {
                        "content": answer,
                        "specialist_results": specialist_results,
                    }
                    append_stream_event(channel_id, "answer", _answer_data)
                    yield _sse_event("answer", _answer_data)

                    # 存储回复（更新占位消息）
                    metadata_dict = {
                        "specialist_results": [
                            {"agent_key": s.get("agent_key", ""), "agent": s.get("agent", ""), "icon": s.get("icon", ""), "analysis": s.get("analysis", "")[:3000]}
                            for s in specialist_results
                        ],
                        "complexity": complexity,
                        "execution_status": "completed",
                        "refined_query": clarification.get("refined_query", effective_query),
                        "reasoning_trail": result.get("reasoning_trail"),
                    }
                    update_message_content_and_metadata(stream_msg_id, answer, metadata_dict)
                    msg_id = stream_msg_id

                    # 基金代码幻觉验证（异步）
                    try:
                        from agent.hallucination_guard import quick_check_fund_codes
                        codes = quick_check_fund_codes(answer)
                        if codes:
                            asyncio.create_task(_async_verify_and_log(conv_id, msg_id, answer))
                    except Exception:
                        pass

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
                            query=effective_query[:500], result=answer[:3000],
                            tool_calls=json.dumps(phase_timings, ensure_ascii=False),
                            duration_ms=total_ms,
                            trace_id=trace_id,
                            status="success",
                        )
                    except Exception as _e:
                        logger.warning(f"记录 agent_runs 失败: {_e}")

                    # 写入 execution_traces
                    _save_execution_trace(trace_id, conv_id, effective_query, "simple", "completed",
                                          total_ms, phase_timings, error_category)

                    _done_data = {
                        "message_id": msg_id,
                        "duration_ms": total_ms,
                        "phase_timings": phase_timings,
                    }
                    append_stream_event(channel_id, "done", _done_data)
                    yield _sse_event("done", _done_data)
                    # 标记 channel 完成
                    try:
                        complete_stream_channel(channel_id)
                    except Exception as _e:
                        logger.warning(f"[trace:{trace_id}] 标记 simple channel 完成失败: {_e}")
                    return
                else:
                    # 专家执行失败，回退到 Orchestrator
                    _fail_status = {"message": "专家执行失败，切换到完整分析模式..."}
                    append_stream_event(channel_id, "status", _fail_status)
                    yield _sse_event("status", _fail_status)
                    # 标记当前 channel 失败，后续走完整路径会创建新 channel
                    try:
                        fail_stream_channel(channel_id, result.get("error", "专家执行失败"))
                    except Exception:
                        pass

        # 4. 获取对话历史
        history = get_messages(conv_id, limit=20)
        msg_list = [{"role": m["role"], "content": m["content"]} for m in history]
        # 构建组合约束上下文（Portfolio Fact Layer）
        # ⚠️ 必须用 asyncio.to_thread + wait_for 包装：build_portfolio_facts() 内部调用
        # _get_bond_temperature() 会发起 HTTP 请求到 youzhiyouxing.cn（timeout=15s），
        # 同步调用会阻塞整个事件循环，导致 SSE 事件无法中继、orchestrate_stream 无法启动。
        # 案例：conv 128 第一次执行因 DNS 解析挂起卡死 6+ 分钟。
        portfolio_facts = None
        facts_text = None
        try:
            portfolio_facts = await asyncio.wait_for(
                asyncio.to_thread(build_portfolio_facts),
                timeout=30.0,
            )
            if portfolio_facts:
                facts_text = json.dumps(portfolio_facts, ensure_ascii=False, indent=2, default=str)
        except asyncio.TimeoutError:
            logger.warning(f"[trace:{trace_id}] build_portfolio_facts 超时 30s（可能 youzhiyouxing.cn 卡住），跳过组合约束")
            portfolio_facts = None
            facts_text = None
        except Exception as e:
            logger.warning(f"构建组合约束失败: {e}")
            facts_text = None

        orchestrator_context = rag_context
        if unified_context:
            orchestrator_context = f"{unified_context}\n\n## 原始 RAG 检索上下文\n{rag_context}" if rag_context else unified_context

        # 注入组合约束到多智能体协作上下文
        if facts_text and portfolio_facts:
            constraints = portfolio_facts.get("constraints", {})
            constraint_summary = f"""## ⚠️ 组合约束（多智能体协同统一规则，最高优先级）

请所有参与协作的专家注意以下硬约束：

1. **集中度红线**：以下基金已超过25%持仓集中度阈值，任何专家**禁止建议加仓或转入**这些基金：
{chr(10).join(f'   - {t}' for t in constraints.get('no_add_targets', []))}

2. **债市温度**：当前债市温度 {constraints.get('bond_temperature', '?')}°，{'偏贵，不建议加仓债券型基金' if constraints.get('bond_expensive') else '正常'}

3. **组合概况**：总市值 {portfolio_facts.get('snapshot', {}).get('total_value', '?')}元，债基占比 {portfolio_facts.get('snapshot', {}).get('bond_pct', '?')}%

4. **最近分析结论**：请参考以下24h内其他分析的结论，不要给出矛盾建议：
{chr(10).join(f'   - [{a.get("type","")}] {a.get("conclusion","")[:200]}...' for a in portfolio_facts.get('recent_analyses', []))}

---
"""
            orchestrator_context = constraint_summary + "\n" + (orchestrator_context or "")

        # Bridge A: 注入24h分析结论上下文
        try:
            from agent.orchestrator import _inject_analysis_context
            _, _analysis_ctx = _inject_analysis_context(effective_query)
            if _analysis_ctx:
                orchestrator_context = _analysis_ctx + "\n" + (orchestrator_context or "")
        except Exception as _e:
            logger.warning(f"注入分析结论上下文失败: {_e}")

        # 5. 调用 Orchestrator（多 Agent 协作）
        # 不检查断开连接，让后端任务继续执行
        yield _sse_event("status", {"message": "正在分析问题，决定需要咨询哪些专家..."})
        # 进度：开始编排
        yield _sse_event("progress", {
            "phase": "orchestrator",
            "phase_index": 3,
            "total_phases": total_phases,
            "phase_label": "专家协作",
            "substep": "正在协调专家团队",
            "pct": 35,
        })

        def _run_orchestrator_stream():
            """在线程中运行 orchestrator + 自动持久化（不受 SSE 断开影响）。"""
            import queue
            q = queue.Queue()
            _prod_spec_results = []
            _prod_start = time.time()

            def _save_progress(status="streaming"):
                try:
                    p1 = [{"agent_key": s["agent_key"], "agent": s.get("agent", ""), "icon": s.get("icon", ""), "analysis": s.get("analysis", "")[:3000]} for s in _prod_spec_results if not s.get("is_cross_review")]
                    p2 = [{"agent_key": s["agent_key"], "agent": s.get("agent", ""), "icon": s.get("icon", ""), "analysis": s.get("analysis", "")[:3000]} for s in _prod_spec_results if s.get("is_cross_review")]
                    update_message_metadata(stream_msg_id, {
                        "execution_status": status, "complexity": complexity,
                        "specialist_results": p1, "cross_review_results": p2,
                        "trace_id": trace_id,
                    })
                except Exception as e:
                    logger.warning(f"增量保存进度失败: {e}")

            def _save_final(content, spec_results, tool_calls, orch_ms, arbitration=None):
                total_ms = int((time.time() - request_start) * 1000)
                pt = {"orchestrator_ms": orch_ms, "total_ms": total_ms}
                # 输出审核
                try:
                    review = review_output(content, spec_results)
                    if review["warnings"]:
                        logger.warning(f"[trace:{trace_id}] 输出审核: {review['warnings']}")
                    content = review["content"]
                except Exception:
                    pass
                p1 = [{"agent_key": s["agent_key"], "agent": s.get("agent", ""), "icon": s.get("icon", ""), "analysis": s.get("analysis", "")[:3000]} for s in _prod_spec_results if not s.get("is_cross_review")]
                p2 = [{"agent_key": s["agent_key"], "agent": s.get("agent", ""), "icon": s.get("icon", ""), "analysis": s.get("analysis", "")[:3000]} for s in _prod_spec_results if s.get("is_cross_review")]
                if stream_msg_id > 0:
                    # 构建 metadata（含仲裁结果，让前端可见对话链路完整四阶段）
                    _meta = {
                        "execution_status": "completed", "complexity": complexity,
                        "specialist_results": p1, "cross_review_results": p2,
                        "tool_calls": tool_calls, "phase_timings": pt, "trace_id": trace_id,
                    }
                    if arbitration:
                        # 截断仲裁摘要避免 metadata 过大
                        _meta["arbitration"] = {
                            "verdict": arbitration.get("verdict", "")[:500],
                            "confidence": arbitration.get("confidence", ""),
                            "key_conflicts": arbitration.get("key_conflicts", [])[:5],
                            "reasoning": arbitration.get("reasoning", "")[:800],
                        }
                        # 仲裁以独立 specialist 卡片形式追加到列表末尾
                        # 修复 conv 127：原仲裁只写 metadata，前端 specialist_results 列表无仲裁 Agent
                        arb_specialist = _build_arbitrator_specialist(arbitration)
                        if arb_specialist:
                            _meta["specialist_results"] = p1 + [arb_specialist]
                    update_message_content_and_metadata(stream_msg_id, content, _meta)
                try:
                    conn = _get_conn()
                    conn.execute("UPDATE agent_runs SET message_id = ? WHERE conversation_id = ? AND message_id = 0", (stream_msg_id, conv_id))
                    conn.commit()
                    conn.close()
                    create_agent_run(conversation_id=conv_id, message_id=stream_msg_id,
                        agent_key="chat_turn", agent_name="对话整体",
                        query=effective_query[:500], result=content[:3000],
                        tool_calls=json.dumps(pt, ensure_ascii=False), duration_ms=total_ms,
                        trace_id=trace_id, status="success")
                except Exception as e:
                    logger.warning(f"记录 agent_runs 失败: {e}")
                log_rag_search(conversation_id=conv_id, message_id=stream_msg_id, query=effective_query,
                    keywords=rag_result.get("keywords", []), results=rag_result.get("results", []),
                    content_types=rag_types if rag_types else None,
                    fts_count=rag_result.get("fts_count", 0), chroma_count=rag_result.get("chroma_count", 0),
                    freshness_filtered=rag_result.get("freshness_filtered", 0), trace_id=trace_id)
                qm = _calculate_quality_metrics(_prod_spec_results, rag_result, tool_calls)
                _save_execution_trace(trace_id, conv_id, effective_query, complexity, "completed", total_ms, pt, "none", qm)

                # 自动检测可执行建议 → 创建决策候选
                try:
                    _auto_create_decision_candidate(content, conv_id, stream_msg_id)
                except Exception:
                    pass

                return content

            def _auto_create_decision_candidate(content: str, conversation_id: int, message_id: int):
                """自动检测 AI 回复中的可执行建议，创建决策候选。"""
                actionable_keywords = ["买入", "加仓", "卖出", "减仓", "清仓", "止盈", "止损", "定投", "调仓", "再平衡", "转换"]
                if not any(kw in content for kw in actionable_keywords):
                    return
                from db.decisions import create_candidate_from_structured_recommendation
                import re
                action_type = "watch"
                if any(kw in content for kw in ["买入", "加仓", "建仓", "定投"]):
                    action_type = "buy"
                elif any(kw in content for kw in ["卖出", "减仓", "清仓", "止盈"]):
                    action_type = "sell"
                elif any(kw in content for kw in ["调仓", "再平衡", "转换"]):
                    action_type = "convert"
                fund_code = ""
                code_match = re.search(r"\d{6}(?:\.\w+)?", content)
                if code_match:
                    fund_code = code_match.group(0)
                candidate_id = create_candidate_from_structured_recommendation({
                    "source_type": "ai_chat",
                    "source_id": str(conversation_id),
                    "scenario_type": "auto_detect",
                    "action_type": action_type,
                    "target_type": "fund" if fund_code else "portfolio",
                    "target_code": fund_code,
                    "summary": content[:200],
                    "rationale": content,
                    "confidence": "low",
                    "evidence": {"auto_detected": True, "conversation_id": conversation_id, "message_id": message_id},
                    "priority": 4,
                    "status": "new",
                })
                logger.info(f"自动创建决策候选: {candidate_id} (action={action_type}, fund={fund_code})")

            def _save_failed(err_msg):
                if stream_msg_id <= 0:
                    return
                p1 = [{"agent_key": s["agent_key"], "agent": s.get("agent", ""), "icon": s.get("icon", ""), "analysis": s.get("analysis", "")[:3000]} for s in _prod_spec_results if not s.get("is_cross_review")]
                p2 = [{"agent_key": s["agent_key"], "agent": s.get("agent", ""), "icon": s.get("icon", ""), "analysis": s.get("analysis", "")[:3000]} for s in _prod_spec_results if s.get("is_cross_review")]
                update_message_content_and_metadata(stream_msg_id, f"❌ 执行失败: {err_msg}", {
                    "execution_status": "failed", "complexity": complexity,
                    "specialist_results": p1, "cross_review_results": p2, "trace_id": trace_id,
                })

            def _producer():
                try:
                    # 将 cancel_event 存入 _running_agents，供 /cancel 端点真正中断后台任务
                    _running_agents[f"prod_{conv_id}"] = {"conv_id": conv_id, "started_at": time.time(), "trace_id": trace_id, "cancel_event": cancel_event}
                    # P2-1：长对话超时保护（5min 警告 / 8min 硬收尾）
                    producer_started = time.time()
                    warn_at_sec = get_config_int("conversation.warn_at_minutes", 5) * 60
                    abort_at_sec = get_config_int("conversation.abort_at_minutes", 8) * 60
                    timeout_warned = False
                    for event in orchestrate_stream(effective_query, msg_list, orchestrator_context, cancel_event=cancel_event, conversation_id=conv_id, message_id=stream_msg_id, trace_id=trace_id, target_specialists=req.target_specialists):
                        # P2-1：在每个事件前检查总耗时
                        elapsed = time.time() - producer_started
                        if elapsed >= abort_at_sec:
                            logger.warning(
                                f"[P2-1] 对话 #{conv_id} 已运行 {elapsed:.0f}s 超过 {abort_at_sec}s 阈值，强制收尾"
                            )
                            q.put({"type": "status", "subtype": "timeout_abort",
                                   "message": f"分析已超过 {abort_at_sec // 60} 分钟，正在强制收尾。已完成的专家分析将保留。"})
                            # 设置 cancel_event 让 orchestrator 内部循环也尽快退出
                            try:
                                cancel_event.set()
                            except Exception:
                                pass
                            break
                        elif elapsed >= warn_at_sec and not timeout_warned:
                            timeout_warned = True
                            logger.info(
                                f"[P2-1] 对话 #{conv_id} 已运行 {elapsed:.0f}s 达 {warn_at_sec}s 警告阈值"
                            )
                            q.put({"type": "status", "subtype": "timeout_warn",
                                   "message": f"分析已进行 {warn_at_sec // 60} 分钟，正在收尾中，请稍候。"})

                        et = event.get("type")
                        # === 持久化事件到 stream_events（支持断线续接）===
                        # 跳过增量块（answer_chunk/reasoning_chunk），最终全文在 answer 事件里
                        if et and et not in ("answer_chunk", "reasoning_chunk"):
                            try:
                                event_seq = append_stream_event(channel_id, et, event)
                                event["seq"] = event_seq
                            except Exception as _e:
                                logger.warning(f"[trace:{trace_id}] 持久化事件失败: {_e}")
                        # === 在线程中持久化（独立于 SSE 连接）===
                        if et == "specialist_done":
                            _prod_spec_results.append({"agent_key": event["agent_key"], "agent": event["agent"], "icon": event.get("icon", "🤖"), "analysis": event.get("analysis", ""), "duration_ms": event.get("duration_ms", 0)})
                            _save_progress("streaming")
                            # 注：orchestrator.py 已创建并更新 agent_run 记录，此处不再重复创建
                        elif et == "cross_review_done":
                            _prod_spec_results.append({"agent_key": event.get("agent_key"), "agent": event.get("agent"), "icon": event.get("icon"), "analysis": event.get("analysis"), "duration_ms": event.get("duration_ms"), "is_cross_review": True})
                            _save_progress("streaming")
                        elif et == "answer":
                            reviewed = _save_final(event.get("content", ""), event.get("specialist_results", []), event.get("tool_calls", []), int((time.time() - _prod_start) * 1000), arbitration=event.get("arbitration"))
                            event = dict(event)
                            event["content"] = reviewed
                            # 标记 channel 完成
                            try:
                                complete_stream_channel(channel_id)
                            except Exception as _e:
                                logger.warning(f"[trace:{trace_id}] 标记 channel 完成失败: {_e}")
                        elif et == "clarification":
                            # 交互式澄清：保存澄清问题到占位消息，存储 checkpoint 供续答恢复
                            # 前端 ChatMessage.vue 期望 msg.clarification = {reason, options, question}
                            # 修复 conv 127：原只存 clarification_options/clarification_checkpoint，
                            # 字段名与前端不匹配，导致澄清内容不展示
                            try:
                                clarify_question = event.get("question", "请补充更多信息")
                                clarify_reason = event.get("reason", "")
                                clarify_options = event.get("options", [])
                                update_message_content_and_metadata(stream_msg_id, clarify_question, {
                                    "execution_status": "clarification",
                                    # 兼容旧字段
                                    "clarification_options": clarify_options,
                                    "clarification_checkpoint": event.get("checkpoint"),
                                    "trace_id": trace_id,
                                    # 新增：与前端 ChatMessage.vue L680 期望的字段一致
                                    "clarification": {
                                        "question": clarify_question,
                                        "reason": clarify_reason,
                                        "options": clarify_options,
                                    },
                                })
                                complete_stream_channel(channel_id)
                            except Exception as _e:
                                logger.warning(f"[trace:{trace_id}] 保存澄清状态失败: {_e}")
                        elif et == "cancelled":
                            _save_progress("cancelled")
                            try:
                                cancel_stream_channel(channel_id)
                            except Exception as _e:
                                logger.warning(f"[trace:{trace_id}] 标记 channel 取消失败: {_e}")
                        elif et == "error":
                            _save_failed(event.get("message", "未知错误"))
                            try:
                                fail_stream_channel(channel_id, event.get("message", "未知错误"))
                            except Exception as _e:
                                logger.warning(f"[trace:{trace_id}] 标记 channel 失败: {_e}")
                        q.put(event)
                except CancelledError:
                    q.put({"type": "cancelled", "message": "用户取消了执行"})
                    _save_progress("cancelled")
                    try:
                        cancel_stream_channel(channel_id)
                    except Exception:
                        pass
                except TimeoutError as e:
                    err = f"执行超时: {e}"
                    _save_failed(err)
                    try:
                        fail_stream_channel(channel_id, err)
                    except Exception:
                        pass
                    q.put({"type": "error", "message": err})
                except Exception as e:
                    logger.error(f"[trace:{trace_id}] 后台执行异常: {e}", exc_info=True)
                    err = str(e)
                    _save_failed(err)
                    try:
                        fail_stream_channel(channel_id, err)
                    except Exception:
                        pass
                    q.put({"type": "error", "message": err})
                finally:
                    _running_agents.pop(f"prod_{conv_id}", None)
                    q.put(None)

            t = threading.Thread(target=_producer, daemon=True)
            t.start()
            return q

        # 提前创建 assistant 消息，获取 message_id 传给 orchestrator
        stream_msg_id = create_message(conv_id, "assistant", "⏳ 分析进行中...", metadata=json.dumps({"execution_status": "streaming"}, ensure_ascii=False))

        # 创建 stream channel（事件流持久化，支持断线续接）
        channel_id = create_stream_channel(
            conversation_id=conv_id,
            message_id=stream_msg_id,
            user_message_id=user_msg_id,
            trace_id=trace_id,
            complexity=complexity,
        )
        logger.info(f"[trace:{trace_id}] 创建 channel {channel_id} for msg={stream_msg_id}")

        q = await asyncio.to_thread(_run_orchestrator_stream)

        client_disconnected = False
        done_data = {}
        _spec_done_count = 0

        # 首事件：通知前端 channel_id（前端缓存用于切回时 replay）
        if not client_disconnected:
            yield _sse_event("channel_started", {"channel_id": channel_id, "message_id": stream_msg_id})

        # 简化的事件中继循环 — 持久化已在生产者线程完成
        while True:
            try:
                event = await asyncio.to_thread(lambda: q.get(timeout=0.5))
            except queue.Empty:
                if not client_disconnected and await request.is_disconnected():
                    logger.info("客户端断开连接，后台任务将继续自动保存结果")
                    client_disconnected = True
                continue

            if event is None:
                break

            event_type = event.get("type")

            if event_type == "cancelled":
                if not client_disconnected:
                    yield _sse_event("cancelled", {"message": event.get("message", "执行已取消")})

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
                _running_agents[f"{conv_id}_{event.get('agent_key', '')}_{int(time.time())}"] = {
                    "agent": event.get("agent", ""),
                    "task": f"对话 #{conv_id}",
                    "started_at": time.time(),
                }
                if not client_disconnected:
                    yield _sse_event("specialist_start", {
                        "agent_key": event.get("agent_key"),
                        "agent": event.get("agent"),
                        "icon": event.get("icon"),
                    })

            elif event_type == "specialist_done":
                _spec_done_count += 1
                to_remove = [k for k in _running_agents if k.startswith(f"{conv_id}_{event.get('agent_key', '')}_")]
                for k in to_remove:
                    _running_agents.pop(k, None)
                if not client_disconnected:
                    yield _sse_event("specialist_done", {
                        "agent_key": event.get("agent_key"),
                        "agent": event.get("agent"),
                        "icon": event.get("icon"),
                        "analysis": event.get("analysis"),
                        "duration_ms": event.get("duration_ms"),
                    })
                    # 进度
                    yield _sse_event("progress", {
                        "phase": "specialists",
                        "phase_index": 4,
                        "total_phases": total_phases,
                        "phase_label": "专家分析",
                        "substep": f"{event.get('agent', '')} 完成 ({_spec_done_count} 个已完成)",
                        "pct": min(35 + (_spec_done_count * 15), 70),
                    })

            elif event_type == "cross_review_start":
                if not client_disconnected:
                    yield _sse_event("cross_review_start", {
                        "agent_key": event.get("agent_key"),
                        "agent": event.get("agent"),
                        "icon": event.get("icon"),
                    })
                    yield _sse_event("progress", {
                        "phase": "cross_review",
                        "phase_index": 5,
                        "total_phases": total_phases,
                        "phase_label": "交叉审阅",
                        "substep": f"{event.get('agent', '')} 交叉审阅中",
                        "pct": 80,
                    })

            elif event_type == "cross_review_done":
                if not client_disconnected:
                    yield _sse_event("cross_review_done", {
                        "agent_key": event.get("agent_key"),
                        "agent": event.get("agent"),
                        "icon": event.get("icon"),
                        "analysis": event.get("analysis"),
                        "duration_ms": event.get("duration_ms"),
                    })

            elif event_type == "reasoning_chunk":
                # 思考过程增量（仅展示，不落库）
                if not client_disconnected:
                    yield _sse_event("reasoning_chunk", {
                        "content": event.get("content", ""),
                        "agent": event.get("agent", ""),
                    })

            elif event_type == "answer_chunk":
                # 答案增量（仅展示，不落库；全文仍由最终 answer 事件持久化）
                if not client_disconnected:
                    yield _sse_event("answer_chunk", {
                        "content": event.get("content", ""),
                    })

            elif event_type == "answer":
                done_data = event
                if not client_disconnected:
                    yield _sse_event("answer", {
                        "content": event.get("content", ""),
                        "specialist_results": event.get("specialist_results", []),
                    })

            elif event_type == "query_refined":
                # P0-2: 持久化查询改写结果到消息元数据（只更新 metadata，content 保持 placeholder）
                update_message_metadata(stream_msg_id, {
                    "original_query": event.get("original_query"),
                    "refined_query": event.get("refined_query"),
                    "rewrite_reason": event.get("rewrite_reason", ""),
                })

            elif event_type == "clarification":
                # 交互式澄清：转发给前端展示选项（checkpoint 已在 producer 存入消息元数据）
                if not client_disconnected:
                    yield _sse_event("clarification", {
                        "question": event.get("question", ""),
                        "reason": event.get("reason", ""),
                        "options": event.get("options", []),
                        "message_id": stream_msg_id,
                    })

            elif event_type == "error":
                if not client_disconnected:
                    yield _sse_event("error", {"message": event.get("message", "未知错误")})
                    return

        # 生产者已完成 — 发送完成事件
        if not client_disconnected:
            total_ms = int((time.time() - request_start) * 1000)
            yield _sse_event("done", {
                "message_id": stream_msg_id,
                "duration_ms": total_ms,
                "phase_timings": {"total_ms": total_ms},
            })

        # 首次对话更新标题
        if len(history) <= 1 and conv.get("title") == "新对话":
            short_title = req.content[:30] + ("..." if len(req.content) > 30 else "")
            update_conversation(conv_id, title=short_title)

        # 主动预警检查
        content = done_data.get("content", "")
        if content:
            asyncio.create_task(_proactive_alert_check(effective_query, content, done_data.get("specialist_results", [])))

        # after_response hooks（后台异步）
        try:
            from agent.hooks import run_hooks
            from agent.memory_governance import SessionSteward
            run_hooks("after_response", {"conversation_id": conv_id, "response": (content or "")[:2000], "analysis_type": complexity, "user_id": "default"})
            SessionSteward.extract_and_save_candidates(conv_id, "default")
        except Exception as e:
            logger.warning(f"after_response hooks 失败: {e}")

        # 对话摘要自动生成（LLM 调用，默认关闭）
        if get_config("llm_cost.auto_conversation_summary", "false") == "true":
            try:
                from agent.memory import _generate_summary
                msgs = get_messages(conv_id, limit=100)
                if len(msgs) >= 8 and not get_conversation_summary(conv_id):
                    recent = msgs[-12:]
                    text = "\n".join(f"[{m.get('role','')}] {(m.get('content','') or '')[:200]}" for m in recent)
                    summary_text = _generate_summary(text, context="对话摘要")
                    if summary_text and "生成失败" not in summary_text:
                        save_conversation_summary(conv_id, msgs[-1]["id"], summary_text)
                        logger.info(f"对话摘要已生成: conv={conv_id} msgs={len(msgs)}")
            except Exception as e:
                logger.warning(f"对话摘要生成失败: {e}")

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/api/conversations/{conv_id}/clarify-answer")
async def clarify_answer_stream(conv_id: int, request: Request):
    """交互式澄清续答 — 从 checkpoint 恢复 Pipeline 执行。

    流程：
    1. 从澄清消息元数据读取 checkpoint
    2. 存储用户回答消息
    3. 用回答改写 query，从 Phase 1（信息收集）开始执行
    4. SSE 流式返回专家分析 + 最终回答
    """
    # 开关检查
    try:
        if not get_config_bool("pipeline.clarification_interactive_enabled", True):
            raise HTTPException(400, "交互式澄清未启用，请直接重新提问并补充细节")
    except HTTPException:
        raise
    except Exception:
        pass

    body = await request.json()
    answer = (body.get("answer") or "").strip()
    message_id = body.get("message_id")
    if not answer:
        raise HTTPException(400, "回答内容不能为空")

    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")

    # 从澄清消息元数据读取 checkpoint
    checkpoint = None
    if message_id:
        try:
            conn = _get_conn()
            row = conn.execute(
                "SELECT metadata FROM messages WHERE id = ? AND conversation_id = ?",
                (message_id, conv_id),
            ).fetchone()
            conn.close()
            if row and row["metadata"]:
                meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
                checkpoint = meta.get("clarification_checkpoint")
        except Exception as e:
            logger.warning(f"[clarify-answer] 读取 checkpoint 失败: {e}")

    if not checkpoint:
        raise HTTPException(400, "澄清状态已过期，请重新提问并补充细节")

    # 防重复：检查是否有进行中的任务
    from db.agents import get_running_agent_count
    if get_running_agent_count(conv_id) > 0:
        raise HTTPException(409, "该对话有进行中的任务，请等待完成后再试")

    trace_id = str(uuid.uuid4())[:12]
    logger.info(f"[trace:{trace_id}] 澄清续答 conv={conv_id} answer='{answer[:50]}'")

    # 存储用户回答
    create_message(conv_id, "user", answer)
    history = get_messages(conv_id, limit=20)
    msg_list = [{"role": m["role"], "content": m["content"]} for m in history]

    async def event_stream():
        import threading
        import queue as queue_module

        cancel_event = threading.Event()
        stream_msg_id = create_message(
            conv_id, "assistant", "⏳ 根据您的补充分析中...",
            metadata=json.dumps({"execution_status": "streaming"}, ensure_ascii=False),
        )
        request_start = time.time()
        q = queue_module.Queue()

        def _producer():
            try:
                _running_agents[f"clarify_{conv_id}"] = {
                    "conv_id": conv_id, "started_at": time.time(),
                    "trace_id": trace_id, "cancel_event": cancel_event,
                }
                from agent.pipeline import run_pipeline_from_checkpoint
                for event in run_pipeline_from_checkpoint(
                    checkpoint, answer, msg_list, trace_id, cancel_event,
                ):
                    et = event.get("type")
                    # 持久化关键事件到消息
                    if et == "answer":
                        content = event.get("content", "")
                        spec_results = event.get("specialist_results", [])
                        arbitration = event.get("arbitration")
                        try:
                            p1 = [{"agent_key": s.get("agent_key", ""), "agent": s.get("agent", ""),
                                   "icon": s.get("icon", ""), "analysis": (s.get("analysis", "") or "")[:3000]}
                                  for s in spec_results if not s.get("is_cross_review")]
                            _meta = {
                                "execution_status": "completed",
                                "specialist_results": p1,
                                "trace_id": trace_id,
                            }
                            if arbitration:
                                _meta["arbitration"] = {
                                    "verdict": arbitration.get("verdict", "")[:500],
                                    "confidence": arbitration.get("confidence", ""),
                                    "key_conflicts": arbitration.get("key_conflicts", [])[:5],
                                    "reasoning": arbitration.get("reasoning", "")[:800],
                                }
                                # 续答路径同样把仲裁追加为独立 specialist 卡片
                                arb_specialist = _build_arbitrator_specialist(arbitration)
                                if arb_specialist:
                                    _meta["specialist_results"] = p1 + [arb_specialist]
                            update_message_content_and_metadata(stream_msg_id, content, _meta)
                        except Exception as _e:
                            logger.warning(f"[trace:{trace_id}] 保存续答回答失败: {_e}")
                    elif et == "degrade":
                        try:
                            update_message_content_and_metadata(
                                stream_msg_id, "分析遇到问题，请重新提问", {
                                    "execution_status": "failed", "trace_id": trace_id,
                                })
                        except Exception:
                            pass
                    elif et == "error":
                        try:
                            update_message_content_and_metadata(
                                stream_msg_id, f"❌ 续答失败: {event.get('message', '')}", {
                                    "execution_status": "failed", "trace_id": trace_id,
                                })
                        except Exception:
                            pass
                    q.put(event)
            except Exception as e:
                logger.exception(f"[trace:{trace_id}] 澄清续答 producer 异常: {e}")
                q.put({"type": "error", "message": str(e)})
            finally:
                _running_agents.pop(f"clarify_{conv_id}", None)
                q.put(None)

        t = threading.Thread(target=_producer, daemon=True)
        t.start()

        if not await request.is_disconnected():
            yield _sse_event("status", {"message": "正在根据您的回答检索分析..."})

        spec_count = 0
        while True:
            try:
                event = await asyncio.to_thread(lambda: q.get(timeout=0.5))
            except queue_module.Empty:
                if await request.is_disconnected():
                    break
                continue
            if event is None:
                break

            et = event.get("type")
            if et == "phase_start":
                if not await request.is_disconnected():
                    yield _sse_event("phase", {"message": event.get("phase", "")})
            elif et == "plan_generated":
                plan = event.get("plan", {})
                if not await request.is_disconnected():
                    yield _sse_event("plan", {
                        "complexity": plan.get("complexity", "medium"),
                        "reason": "澄清续答",
                        "refined_query": plan.get("refined_query", ""),
                    })
            elif et == "specialist_start":
                if not await request.is_disconnected():
                    yield _sse_event("specialist_start", {
                        "agent_key": event.get("agent_key", ""),
                        "agent": event.get("agent", ""),
                        "icon": event.get("icon", ""),
                    })
            elif et == "specialist_done":
                spec_count += 1
                if not await request.is_disconnected():
                    yield _sse_event("specialist_done", {
                        "agent_key": event.get("agent_key", ""),
                        "agent": event.get("agent", ""),
                        "icon": event.get("icon", ""),
                        "analysis": event.get("analysis", ""),
                        "duration_ms": event.get("duration_ms", 0),
                    })
                    yield _sse_event("progress", {
                        "phase": "specialists", "phase_index": 2,
                        "total_phases": 3, "phase_label": "专家分析",
                        "substep": f"{event.get('agent', '')} 完成 ({spec_count} 个)",
                        "pct": min(30 + spec_count * 20, 80),
                    })
            elif et == "answer":
                if not await request.is_disconnected():
                    yield _sse_event("answer", {
                        "content": event.get("content", ""),
                        "specialist_results": event.get("specialist_results", []),
                    })
            elif et == "degrade":
                if not await request.is_disconnected():
                    yield _sse_event("status", {"message": "切换到标准模式..."})
            elif et == "error":
                if not await request.is_disconnected():
                    yield _sse_event("error", {"message": event.get("message", "未知错误")})
                return

        total_ms = int((time.time() - request_start) * 1000)
        if not await request.is_disconnected():
            yield _sse_event("done", {
                "message_id": stream_msg_id,
                "duration_ms": total_ms,
                "phase_timings": {"total_ms": total_ms},
            })

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── 辅助函数 ─────────────────────────────────────────────


def _sse_event(event_type: str, data: dict) -> str:
    """格式化 SSE 事件。"""
    return f"data: {json.dumps({'type': event_type, 'data': data}, ensure_ascii=False)}\n\n"


async def _proactive_alert_check(query: str, answer: str, specialist_results: list):
    """对话结束后，主动检测是否需要对持仓生成预警。

    仅在对话中**明确建议**对某只持仓基金进行加减仓、或明确提示未来风险/涨幅时才触发。
    普通的持仓查看、市场分析、估值讨论等不会触发预警。
    """
    try:
        # ── 第一关：必须包含明确的动作/建议类关键词 ──
        # 仅当 AI 回复中出现明确的买卖建议、风险提示时才继续
        action_keywords = [
            "建议加仓", "建议买入", "建议减仓", "建议卖出", "建议赎回", "建议清仓",
            "可以加仓", "可以买入", "可以减仓", "可以卖出", "可以赎回",
            "考虑加仓", "考虑买入", "考虑减仓", "考虑卖出",
            "应该买入", "应该卖出", "应该加仓", "应该减仓",
            "适当加仓", "适当减仓", "适当买入", "适当卖出",
            "分批买入", "分批卖出", "分批加仓", "分批减仓",
            "止盈", "止损", "预警风险", "重大风险", "需要注意",
            "及时调整", "及时减仓", "及时止盈",
        ]
        combined_text = query + " " + answer
        for sr in specialist_results:
            analysis = sr.get("analysis", "")
            if analysis:
                combined_text += " " + analysis[:2000]

        has_action = any(kw in combined_text for kw in action_keywords)
        if not has_action:
            return

        # ── 第二关：必须关联到具体的持仓基金 ──
        holdings = list_holdings()
        if not holdings:
            return

        alert_holdings = []
        for h in holdings:
            fname = h.get("fund_name", "")
            iname = h.get("index_name", "")
            if (fname and fname in combined_text) or (iname and iname in combined_text):
                alert_holdings.append(h)

        if not alert_holdings:
            return

        # ── 第三关：为匹配的持仓生成预警 ──
        for h in alert_holdings[:3]:
            # 从对话内容中截取相关片段作为预警依据
            fname = h.get("fund_name", "")
            reason = _extract_alert_reason(combined_text, fname)
            create_alert(
                alert_type="news_impact",
                title=f"对话中提及 {fname} 的操作建议",
                content=reason or f"对话中涉及对 {fname}（{h.get('fund_code', '')}）的操作建议，请留意。",
                severity="info",
                related_fund_code=h.get("fund_code"),
                related_fund_name=fname,
                source="ai_analysis",
            )
    except Exception as e:
        logger.warning(f"[proactive_alert] 生成预警异常: {e}")


def _extract_alert_reason(text: str, fund_name: str) -> str:
    """从对话文本中提取与基金相关的建议片段（最多 200 字）。"""
    if not fund_name:
        return ""
    idx = text.find(fund_name)
    if idx < 0:
        return ""
    # 取基金名前后各 100 字符作为上下文
    start = max(0, idx - 100)
    end = min(len(text), idx + len(fund_name) + 100)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    return snippet


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
    """获取专家名称（从数据库加载）。"""
    from db.agents import load_specialist_agents
    specialists = load_specialist_agents()
    agent = specialists.get(agent_key)
    return agent["name"] if agent else "专家"


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
        except Exception:
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
        except Exception:
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


# ── Trace 全链路追踪辅助函数 ──────────────────────────────────────


def _save_execution_trace(trace_id: str, conversation_id: int, query: str,
                          complexity: str, status: str, total_ms: int,
                          phase_timings: dict, error_category: str = "none",
                          quality_metrics: dict = None):
    """写入 execution_traces 表，记录一次对话的完整执行链路。"""
    try:
        conn = _get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO execution_traces
            (trace_id, conversation_id, query, complexity, status,
             finished_at, total_ms, phase_timings, quality_metrics, error_category)
            VALUES (?, ?, ?, ?, ?, datetime('now','localtime'), ?, ?, ?, ?)
        """, (
            trace_id, conversation_id, query[:500], complexity, status,
            total_ms, json.dumps(phase_timings, ensure_ascii=False),
            json.dumps(quality_metrics or {}, ensure_ascii=False),
            error_category,
        ))
        conn.commit()
        conn.close()
        logger.info(f"[trace:{trace_id}] 执行链路已保存: status={status}, total_ms={total_ms}")
    except Exception as e:
        logger.warning(f"保存 execution_trace 失败: {e}")


def _calculate_quality_metrics(specialist_results: list, rag_result: dict,
                               tool_calls: list) -> dict:
    """计算本次执行的质量指标。"""
    # RAG 覆盖率：是否找到相关信息
    rag_coverage = 1.0 if (rag_result and rag_result.get("results")) else 0.0

    # 工具成功率：检查是否有实际错误（不是结果中包含"error"字样）
    if tool_calls:
        success_count = 0
        for tc in tool_calls:
            # 有 result_preview 且不是错误消息就算成功
            has_result = bool(tc.get("result_preview"))
            is_error = tc.get("error") or (tc.get("result_preview", "").startswith("错误") or tc.get("result_preview", "").startswith("Error"))
            if has_result and not is_error:
                success_count += 1
        tool_success_rate = success_count / len(tool_calls)
    else:
        tool_success_rate = 1.0

    # 专家完成度：status=success 且非兜底文本 且 ≥200 字才算完成
    _FALLBACK_BLACKLIST = {"分析过程遇到问题，请重试。", "交叉审阅完成，请参考其他专家分析。"}
    if specialist_results:
        completed = len([s for s in specialist_results
                        if s.get("analysis")
                        and s.get("status", "success") == "success"
                        and s["analysis"].strip() not in _FALLBACK_BLACKLIST
                        and len(s["analysis"].strip()) >= 200])
        specialist_completion = completed / len(specialist_results)
    else:
        specialist_completion = 0.0

    return {
        "rag_coverage": round(rag_coverage, 2),
        "tool_success_rate": round(tool_success_rate, 2),
        "specialist_completion": round(specialist_completion, 2),
        "specialist_count": len(specialist_results or []),
        "tool_count": len(tool_calls or []),
    }


# ── Trace 查询 API ──────────────────────────────────────


@router.get("/api/conversations/{conv_id}/trace/{trace_id}")
async def get_trace_api(conv_id: int, trace_id: str):
    """获取一次对话的完整执行链路。"""
    conn = _get_conn()

    # 获取 trace 元数据
    trace = conn.execute(
        "SELECT * FROM execution_traces WHERE trace_id = ? AND conversation_id = ?",
        (trace_id, conv_id)
    ).fetchone()
    if not trace:
        conn.close()
        raise HTTPException(404, "执行链路不存在")

    # 获取关联的 agent_runs
    runs = conn.execute(
        "SELECT * FROM agent_runs WHERE trace_id = ? ORDER BY id",
        (trace_id,)
    ).fetchall()

    # 获取关联的 rag_logs
    rag_logs = conn.execute(
        "SELECT * FROM rag_logs WHERE trace_id = ? ORDER BY id",
        (trace_id,)
    ).fetchall()

    # 获取关联的工具审计日志
    tool_logs = conn.execute(
        "SELECT * FROM tool_audit_logs WHERE trace_id = ? ORDER BY id",
        (trace_id,)
    ).fetchall()

    conn.close()

    return {
        "trace": dict(trace),
        "agent_runs": [dict(r) for r in runs],
        "rag_logs": [dict(r) for r in rag_logs],
        "tool_audit_logs": [dict(r) for r in tool_logs],
    }


@router.get("/api/conversations/{conv_id}/traces")
async def list_traces_api(conv_id: int, limit: int = 20):
    """获取对话的执行链路列表。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT trace_id, complexity, status, total_ms, error_category,
               quality_metrics, started_at, finished_at
        FROM execution_traces
        WHERE conversation_id = ?
        ORDER BY id DESC
        LIMIT ?
    """, (conv_id, limit)).fetchall()
    conn.close()
    return {"traces": [dict(r) for r in rows]}


# ── 任务状态查询 API ──────────────────────────────────────────


@router.get("/api/conversations/tasks/running")
async def get_running_tasks():
    """获取所有正在运行的任务。"""
    from db.conversations import get_running_conversations
    tasks = get_running_conversations()
    return {"tasks": tasks}


@router.get("/api/conversations/tasks/{conv_id}/status")
async def get_task_status(conv_id: int):
    """获取指定对话的任务状态。"""
    from db.conversations import get_conversation_progress
    progress = get_conversation_progress(conv_id)
    return progress


@router.get("/api/conversations/{conv_id}/export")
async def export_conversation(conv_id: int, format: str = "markdown"):
    """导出对话为 Markdown 文件。"""
    from fastapi.responses import Response
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")
    messages = get_messages(conv_id)
    if not messages:
        raise HTTPException(400, "对话无消息")

    lines = [f"# {conv.get('title', '投资分析对话')}\n"]
    lines.append(f"导出时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append("---\n")
    for msg in messages:
        role = "🧑 用户" if msg.get("role") == "user" else "🤖 助手"
        lines.append(f"## {role}\n")
        content = msg.get("content", "")
        lines.append(f"{content}\n")
        # 附带专家结果
        meta = msg.get("metadata")
        if isinstance(meta, str):
            try:
                import json
                meta = json.loads(meta)
            except Exception:
                meta = None
        if meta and meta.get("specialist_results"):
            lines.append("### 专家分析\n")
            for s in meta["specialist_results"]:
                lines.append(f"**{s.get('agent', '')}**:\n{s.get('analysis', '')[:2000]}\n")
        if msg.get("created_at"):
            lines.append(f"_{msg['created_at']}_\n")
        lines.append("---\n")

    content = "\n".join(lines)
    filename = f"conversation_{conv_id}_{__import__('datetime').datetime.now().strftime('%Y%m%d')}.md"
    return Response(
        content=content.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── 增强5: 人在回路 — 确认端点 ──────────────────────────────

@router.post("/api/conversations/{conv_id}/confirm")
async def handle_confirm(conv_id: int, request: Request):
    """处理用户在 Human-in-the-Loop 确认节点的选择。

    请求体：{"confirm_id": "confirm_xxx", "choice": "continue"}
    """
    from agent.orchestrator import resolve_confirm
    body = await request.json()
    confirm_id = body.get("confirm_id", "")
    choice = body.get("choice", "continue")
    if not confirm_id:
        raise HTTPException(400, "缺少 confirm_id")
    resolve_confirm(confirm_id, choice)
    return {"ok": True, "confirm_id": confirm_id, "choice": choice}
