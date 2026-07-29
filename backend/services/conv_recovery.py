"""中断对话恢复 — 启动时检测并修复因重启中断的对话。

场景：后端重启时，正在执行的 SSE 流被中断，导致：
- 专家分析已成功（agent_runs.status='success'）
- 但 synthesis 阶段未执行 → assistant message 仍是占位符

恢复策略（2026-07-30 增强）：
1. 扫描所有 content 为占位符的 assistant message
2. 检查该 message 是否有 status='success' 的 agent_runs（按 message_id 过滤）
3. 若有专家结果：
   a. 优先尝试"只跑综合阶段"（resume_synthesis_only）—— 复用已落库的专家结果，
      调用 _phase_synthesis 生成完整综合报告（含仲裁+5段结构），不重新分析专家
   b. 若综合阶段失败，降级为简单拼接（_merge_runs_to_answer）
4. 若无专家结果，将占位符替换为中断提示

收益（conv 192 案例）：
- article_expert 已花 ~69s + ~20k token 分析完，恢复时只需跑综合阶段（~5s + ~5k token）
- 节省 75%+ 时间和 token，且产出完整综合报告而非简单拼接

注意：必须按 message_id 过滤 agent_runs，不能用 conversation_id。
否则同一对话多轮中，会把其他轮次的专家结果错误合并到中断轮次。

触发时机：
- 启动时全量扫描（recover_interrupted_conversations）
- 心跳超时单条恢复（recover_message），避免依赖后端重启
- 启动时自动重试 process restart 中断（auto_retry_process_restart_interrupted）
"""
import json
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# 占位符前缀（匹配所有 ⏳ 开头的临时状态文本）
_PLACEHOLDER_PREFIX = "⏳"

# 专家已完成提示文本（有专家结果，仅综合报告未生成）
# 修复 conv#140：专家全部成功时不应标记为"中断失败"，应标为"已完成（专家分析）"
_EXPERTS_DONE_NOTICE = (
    "✅ 专家分析已完成（综合报告因服务重启未生成）。"
    "如需综合报告，可点击「重新生成」。\n\n"
    "---\n\n以下为已完成的专家分析：\n\n"
)

# 中断提示文本（专家未执行时）
_NO_RESULT_NOTICE = "⚠️ 本次分析因服务中断未完成（专家未执行）。请重新发送问题以获得完整分析。"

# P0-4 修复 conv_189：专家已执行但归属其他消息时，提示更准确
_EXPERTS_MISPLACED_NOTICE = (
    "⚠️ 本次分析因服务中断未完成。专家分析可能已在上一轮执行，"
    "但因服务重启未能生成综合报告。建议点击「重新生成」以复用已有专家结果。"
)

# 自动重试中提示文本
_RETRYING_NOTICE = "⏳ 检测到服务重启导致中断，正在自动重试..."


# ── "只跑综合阶段"恢复（2026-07-30）──────────────────────────
# 复用已落库的 agent_runs 结果，调用 _phase_synthesis 生成完整综合报告
# 避免重新分析专家，节省 75%+ 时间和 token


def _rebuild_specialists_from_runs(runs: list) -> list:
    """从 agent_runs 重建 specialists 列表（供 _phase_synthesis 使用）。

    每个 specialist 需包含：agent_key, agent, analysis, icon, tool_calls, duration_ms
    """
    # SPECIALIST_AGENTS 已迁移到 DB，用 load_specialist_agents() 加载
    from db.agents import load_specialist_agents

    specialists_db = load_specialist_agents()  # {agent_key: {name, icon, ...}}

    specialists = []
    seen_keys = set()
    for r in runs:
        agent_key = r.get("agent_key") or ""
        agent_name = r.get("agent_name") or "专家分析"
        result = r.get("result") or ""

        # 跳过交叉审阅结果（内容以"审阅"或"综合审阅"开头）
        if result.startswith("审阅") or result.startswith("综合审阅"):
            continue

        # 按 agent_key 去重，保留首次（首轮 primary 分析）
        if agent_key and agent_key in seen_keys:
            continue
        if agent_key:
            seen_keys.add(agent_key)

        # icon 优先从 DB 取，DB 无则用默认
        icon = "🤖"
        if agent_key and agent_key in specialists_db:
            icon = specialists_db[agent_key].get("icon", "🤖")

        # 解析 tool_calls（可能是 JSON 字符串）
        tool_calls = []
        tc_raw = r.get("tool_calls")
        if tc_raw:
            try:
                tool_calls = json.loads(tc_raw) if isinstance(tc_raw, str) else tc_raw
            except (json.JSONDecodeError, TypeError):
                tool_calls = []

        specialists.append({
            "agent_key": agent_key,
            "agent": agent_name,
            "icon": icon,
            "analysis": result,
            "tool_calls": tool_calls if isinstance(tool_calls, list) else [],
            "duration_ms": r.get("duration_ms") or 0,
        })

    return specialists


def _find_original_query(conn, conv_id: int, msg_id: int) -> str:
    """找到 assistant message 对应的原始 user query。"""
    rows = conn.execute("""
        SELECT role, content FROM messages
        WHERE conversation_id = ? AND id < ?
        ORDER BY id DESC LIMIT 10
    """, (conv_id, msg_id)).fetchall()
    for r in rows:
        if r["role"] == "user":
            return r["content"] or ""
    return ""


def _rebuild_blackboard_from_specialists(specialists: list) -> "Blackboard":
    """从已落库的 specialists 产物重建黑板。

    agent_runs 落库了完整的 result(analysis) + tool_calls，
    足以通过 extract_entry_from_result 重建与原执行等价的 BlackboardEntry：
    - conclusion（结论）
    - action_signals（BUY/SELL/HOLD 信号 → 冲突检测）
    - key_data（PE/PB/分位等关键数据点 → 综合阶段关键数据汇总）
    - risk_veto（风险评估师的否决 → 强制降级）
    - portfolio_impact（持仓影响 → 组合层面决策）

    这样恢复后的综合阶段能获得与原执行等价的上下文，
    而非空黑板（空黑板会导致冲突检测失效、关键数据汇总缺失）。
    """
    from agent.infra.blackboard import Blackboard, extract_entry_from_result

    blackboard = Blackboard()
    rebuilt = 0
    for s in specialists:
        try:
            entry = extract_entry_from_result(
                agent_key=s.get("agent_key", ""),
                agent_name=s.get("agent", "") or s.get("agent_name", "") or "专家",
                result={
                    "analysis": s.get("analysis", ""),
                    "tool_calls": s.get("tool_calls", []),
                    "duration_ms": s.get("duration_ms", 0),
                },
                duration_ms=s.get("duration_ms", 0),
            )
            if entry.conclusion or entry.action_signals or entry.key_data:
                blackboard.write(entry)
                rebuilt += 1
        except Exception as e:
            logger.debug(f"[resume_synthesis] 重建黑板条目失败 ({s.get('agent_key','')}): {e}")

    logger.info(
        f"[resume_synthesis] 黑板重建完成: {rebuilt}/{len(specialists)} 条目, "
        f"冲突={len(blackboard.find_conflicts())}, "
        f"否决={len(blackboard.get_vetoes())}, "
        f"持仓影响={len(blackboard.get_portfolio_impacts())}"
    )
    return blackboard


def resume_synthesis_only(msg_id: int, conv_id: int = None) -> dict:
    """只跑综合阶段：复用已落库的 agent_runs，生成完整综合报告。

    适用场景：专家已成功（agent_runs.status='success'）但综合阶段未执行。

    流程：
    1. 从 agent_runs 重建 specialists 列表
    2. 创建空 blackboard（恢复场景无实时黑板数据）
    3. 调用 _phase_synthesis（单专家直接返回，多专家调 LLM 综合）
    4. 写回 message

    Returns:
        {"success": bool, "answer": str, "specialist_count": int, "reason": str}
    """
    from db._conn import _get_conn

    conn = _get_conn()
    try:
        # 1. 查 agent_runs
        if conv_id is None:
            row = conn.execute(
                "SELECT conversation_id FROM messages WHERE id = ?", (msg_id,)
            ).fetchone()
            if not row:
                return {"success": False, "answer": "", "specialist_count": 0, "reason": "message_not_found"}
            conv_id = row["conversation_id"]

        runs = conn.execute("""
            SELECT agent_key, agent_name, result, tool_calls, duration_ms, run_phase
            FROM agent_runs
            WHERE message_id = ? AND status = 'success'
            ORDER BY id
        """, (msg_id,)).fetchall()

        if not runs:
            return {"success": False, "answer": "", "specialist_count": 0, "reason": "no_success_runs"}

        # 2. 重建 specialists
        specialists = _rebuild_specialists_from_runs([dict(r) for r in runs])
        if not specialists:
            return {"success": False, "answer": "", "specialist_count": 0, "reason": "rebuild_failed"}

        # 3. 找原始 query
        query = _find_original_query(conn, conv_id, msg_id)
        if not query:
            query = "（恢复场景：复用已完成的专家分析生成综合报告）"

        logger.info(
            f"[resume_synthesis] msg {msg_id} (conv {conv_id}) "
            f"复用 {len(specialists)} 个专家结果，只跑综合阶段"
        )

    finally:
        conn.close()

    # 4. 调用 _phase_synthesis（在线程中执行，避免阻塞）
    try:
        from agent.core.pipeline import _phase_synthesis
        from agent.state.pipeline_state import PipelineState

        # 从 agent_runs 完整产物重建黑板（关键数据/冲突/风险否决/持仓影响）
        # agent_runs 落库了完整的 result + tool_calls，足以重建与原执行等价的黑板
        blackboard = _rebuild_blackboard_from_specialists(specialists)

        # 构建 execution_result
        all_tool_calls = []
        for s in specialists:
            all_tool_calls.extend(s.get("tool_calls", []))

        execution_result = {
            "specialists": specialists,
            "tool_calls": all_tool_calls,
        }

        # 创建临时 state（_phase_synthesis 需要）
        trace_id = f"resume-{msg_id}"
        state = PipelineState(
            conversation_id=conv_id,
            message_id=msg_id,
            trace_id=trace_id,
        )

        # 调用综合阶段
        synthesis_result = _phase_synthesis(
            state=state,
            query=query,
            execution_result=execution_result,
            blackboard=blackboard,
            trace_id=trace_id,
        )

        answer = synthesis_result.get("answer", "")
        if not answer:
            return {
                "success": False,
                "answer": "",
                "specialist_count": len(specialists),
                "reason": "empty_synthesis",
            }

        logger.info(
            f"[resume_synthesis] msg {msg_id} 综合报告生成成功 "
            f"(len={len(answer)}, specialists={len(specialists)}, "
            f"黑板={blackboard.entry_count}条目, "
            f"冲突={len(blackboard.find_conflicts())}, "
            f"否决={len(blackboard.get_vetoes())})"
        )

        return {
            "success": True,
            "answer": answer,
            "specialist_count": len(specialists),
            "blackboard_entries": blackboard.entry_count,
            "synthesis_result": synthesis_result,
            "reason": "ok",
        }

    except Exception as e:
        logger.warning(f"[resume_synthesis] msg {msg_id} 综合阶段失败，降级拼接: {e}")
        return {
            "success": False,
            "answer": "",
            "specialist_count": len(specialists) if 'specialists' in dir() else 0,
            "reason": f"synthesis_error: {e}",
        }


def _merge_runs_to_answer(runs) -> str | None:
    """把 agent_runs 合并成恢复后的 content。无有效结果返回 None。

    修复 conv#140：
    1. 只合并 primary 阶段的专家结果（cross_review 是中间步骤，不应作为最终答案）。
       因 run_phase 字段有时未正确区分，按 agent_name 去重保留首次出现的结果
       （交叉审阅是同一专家的第二次调用，去重后自然只保留首轮分析）。
    2. 用"专家分析已完成"文案替代误导性的"服务重启中断"。
    """
    # 按 agent_name 去重，保留首次出现（首轮 primary 分析）
    seen_agents = set()
    primary_runs = []
    for r in runs:
        name = r["agent_name"] or "专家分析"
        if name in seen_agents:
            continue
        # 跳过明显的交叉审阅结果（内容以"审阅"或"综合审阅"开头）
        text = r["result"] or ""
        if text.startswith("审阅") or text.startswith("综合审阅"):
            continue
        seen_agents.add(name)
        primary_runs.append(r)

    parts = []
    for r in primary_runs:
        text = r["result"] or ""
        # 去掉 JSON 块，从第一个 markdown 标题开始取
        idx = text.find("\n## ")
        body = text[idx + 1:] if idx >= 0 else text
        agent_name = r["agent_name"] or "专家分析"
        parts.append(f"\n## {agent_name}\n")
        parts.append(body.strip())
    if not parts:
        return None
    return _EXPERTS_DONE_NOTICE + "\n".join(parts)


def _apply_recovery(conn, msg_id: int, content: str, has_expert_results: bool = False) -> None:
    """统一恢复写回：同时更新 content 和 metadata.execution_status。

    修复 conv#140：专家已成功时 execution_status 设为 'completed'（专家分析可用），
    仅无专家结果时设为 'failed'。避免把"专家都跑完了"误标为"失败"。
    """
    import json as _json
    row = conn.execute("SELECT metadata FROM messages WHERE id = ?", (msg_id,)).fetchone()
    meta = {}
    if row and row["metadata"]:
        try:
            meta = _json.loads(row["metadata"])
        except Exception:
            meta = {}
    # 专家有结果时标 completed（前端不显示"失败"，用户能看到专家分析）
    meta["execution_status"] = "completed" if has_expert_results else "failed"
    meta["recovered"] = True  # 标记为恢复产生，便于排查
    meta["recovery_type"] = "experts_done" if has_expert_results else "no_expert"
    conn.execute(
        "UPDATE messages SET content = ?, metadata = ? WHERE id = ?",
        (content, _json.dumps(meta, ensure_ascii=False), msg_id),
    )


def recover_message(message_id: int) -> str:
    """恢复单条中断消息。

    在心跳超时标记 channel 为 aborted 后立即调用，避免占位符长期悬挂。
    幂等：非占位符消息不会被处理。

    Returns:
        恢复后的 content；若消息不存在或非占位符，返回原内容或空串。
    """
    from db._conn import _get_conn

    conn = _get_conn()
    try:
        msg = conn.execute(
            "SELECT id, conversation_id, content FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()
        if not msg:
            return ""
        content = msg["content"] or ""
        # 非占位符消息不处理（避免覆盖已恢复或正常完成的消息）
        if not content.startswith(_PLACEHOLDER_PREFIX):
            return content

        msg_id = msg["id"]
        conv_id = msg["conversation_id"]

        # 按 message_id 过滤（关键修复）
        runs = conn.execute("""
            SELECT agent_name, result, run_phase, trace_id
            FROM agent_runs
            WHERE message_id = ? AND status = 'success'
            ORDER BY id
        """, (msg_id,)).fetchall()

        # P0-3/P0-4 修复 conv_189：message_id 查不到时，按 conversation_id + 近期 trace_id fallback
        # 原因：澄清续答流程的 checkpoint 未更新 message_id，导致专家结果归属到旧消息
        misplaced_runs = []
        if not runs:
            # 查同对话最近 30 分钟内、其他 message_id 的 success agent_runs
            recent_runs = conn.execute("""
                SELECT agent_name, result, run_phase, trace_id, message_id
                FROM agent_runs
                WHERE conversation_id = ? AND status = 'success'
                  AND created_at >= datetime('now', 'localtime', '-30 minutes')
                ORDER BY id DESC
            """, (conv_id,)).fetchall()
            if recent_runs:
                # 按 trace_id 分组，取最新 trace 的结果
                latest_trace = recent_runs[0]["trace_id"] if recent_runs else None
                if latest_trace:
                    misplaced_runs = [r for r in recent_runs if r["trace_id"] == latest_trace]
                    logger.info(
                        f"[conv_recovery] msg {msg_id} (conv {conv_id}) message_id 无结果，"
                        f"fallback 到同对话 trace_id={latest_trace} 的 {len(misplaced_runs)} 个专家结果"
                    )

        # 使用 fallback 结果（如果有）
        effective_runs = runs if runs else misplaced_runs
        if effective_runs:
            # 优先尝试"只跑综合阶段"（复用专家结果，生成完整报告）
            resume_result = resume_synthesis_only(msg_id, conv_id)
            if resume_result.get("success"):
                full_answer = resume_result["answer"]
                _apply_recovery(conn, msg_id, full_answer, has_expert_results=True)
                conn.commit()
                logger.info(
                    f"[conv_recovery] msg {msg_id} (conv {conv_id}) 心跳超时恢复"
                    f"（resume_synthesis，{resume_result['specialist_count']} 个专家）"
                )
                return full_answer

            # 降级：简单拼接
            full_answer = _merge_runs_to_answer(effective_runs)
            if full_answer:
                _apply_recovery(conn, msg_id, full_answer, has_expert_results=True)
                conn.commit()
                source = "message_id" if runs else "trace_id fallback"
                logger.info(
                    f"[conv_recovery] msg {msg_id} (conv {conv_id}) 心跳超时恢复"
                    f"（降级拼接 {source}，{len(effective_runs)} 个专家，原因: {resume_result.get('reason', '')}）"
                )
                return full_answer

        # 无专家结果 → 标记为中断
        # P0-4：如果同对话有近期的 success agent_runs（但 trace 不同），用更准确的提示
        has_nearby_experts = bool(misplaced_runs) or bool(
            conn.execute("""
                SELECT 1 FROM agent_runs
                WHERE conversation_id = ? AND status = 'success'
                  AND created_at >= datetime('now', 'localtime', '-30 minutes')
                LIMIT 1
            """, (conv_id,)).fetchone()
        )
        notice = _EXPERTS_MISPLACED_NOTICE if has_nearby_experts else _NO_RESULT_NOTICE
        _apply_recovery(conn, msg_id, notice, has_expert_results=False)
        conn.commit()
        logger.info(f"[conv_recovery] msg {msg_id} (conv {conv_id}) 心跳超时，无专家结果，标记为中断")
        return notice
    except Exception as e:
        logger.warning(f"[conv_recovery] recover_message({message_id}) 失败: {e}")
        return ""
    finally:
        conn.close()


def recover_interrupted_conversations() -> dict:
    """启动时恢复中断的对话。

    Returns:
        {"recovered": int, "marked_interrupted": int, "skipped": int}
    """
    from db._conn import _get_conn

    stats = {"recovered": 0, "marked_interrupted": 0, "skipped": 0}

    conn = _get_conn()
    try:
        # 找所有占位符 assistant message
        placeholder_msgs = conn.execute("""
            SELECT id, conversation_id, content
            FROM messages
            WHERE role = 'assistant' AND content LIKE ?
        """, (_PLACEHOLDER_PREFIX + "%",)).fetchall()

        if not placeholder_msgs:
            logger.info("[conv_recovery] 无中断对话需恢复")
            return stats

        logger.info(f"[conv_recovery] 发现 {len(placeholder_msgs)} 条占位符消息，开始恢复")

        for msg in placeholder_msgs:
            msg_id = msg["id"]
            conv_id = msg["conversation_id"]

            # 关键修复：按 message_id 过滤，而非 conversation_id
            # 同一对话多轮中，其他轮次的专家结果不应合并到中断轮次
            runs = conn.execute("""
                SELECT agent_name, result, run_phase
                FROM agent_runs
                WHERE message_id = ? AND status = 'success'
                ORDER BY id
            """, (msg_id,)).fetchall()

            if runs:
                # 有专家结果 → 优先尝试"只跑综合阶段"（复用专家结果，生成完整报告）
                # 失败则降级为简单拼接
                resume_result = resume_synthesis_only(msg_id, conv_id)
                if resume_result.get("success"):
                    full_answer = resume_result["answer"]
                    _apply_recovery(conn, msg_id, full_answer, has_expert_results=True)
                    stats["recovered"] += 1
                    logger.info(
                        f"[conv_recovery] msg {msg_id} (conv {conv_id}) 已恢复"
                        f"（resume_synthesis，{resume_result['specialist_count']} 个专家）"
                    )
                else:
                    # 降级：简单拼接
                    full_answer = _merge_runs_to_answer(runs)
                    if full_answer:
                        _apply_recovery(conn, msg_id, full_answer, has_expert_results=True)
                        stats["recovered"] += 1
                        logger.info(
                            f"[conv_recovery] msg {msg_id} (conv {conv_id}) 已恢复"
                            f"（降级拼接，{len(runs)} 个专家，原因: {resume_result.get('reason', '')}）"
                        )
                    else:
                        stats["skipped"] += 1
            else:
                # 无专家结果 → 标记为中断
                _apply_recovery(conn, msg_id, _NO_RESULT_NOTICE, has_expert_results=False)
                stats["marked_interrupted"] += 1
                logger.info(f"[conv_recovery] msg {msg_id} (conv {conv_id}) 无专家结果，标记为中断")

        conn.commit()
        logger.info(
            f"[conv_recovery] 恢复完成: {stats['recovered']} 条合并恢复, "
            f"{stats['marked_interrupted']} 条标记中断, {stats['skipped']} 条跳过"
        )
    except Exception as e:
        logger.warning(f"[conv_recovery] 恢复失败（不影响启动）: {e}")
    finally:
        conn.close()

    return stats


# ══════════════════════════════════════════════════════
# 自动重试：进程重启导致的中断（专家未执行）
# ══════════════════════════════════════════════════════

def _find_process_restart_interrupted_messages() -> list[dict]:
    """查找 process restart 中断且专家未执行的占位符消息。

    条件：
    - content 以 ⏳ 开头（占位符，init_db 清理后 content 不变，只改 metadata）
    - 关联的 stream_channels.abort_reason = 'process restart'
    - 该 message_id 无 success 的 agent_runs
    - metadata.execution_status = 'failed'（init_db 清理后 streaming → failed）
    - metadata 无 auto_retried 标记（避免重复重试）
    """
    from db._conn import _get_conn

    conn = _get_conn()
    try:
        # 注意：init_db() 会把 streaming 状态清理为 failed，所以这里查 failed
        # 而非 streaming。content 仍是占位符（init_db 不改 content）
        rows = conn.execute("""
            SELECT m.id as msg_id, m.conversation_id as conv_id,
                   c.user_message_id, c.trace_id, c.complexity
            FROM messages m
            INNER JOIN stream_channels c ON c.message_id = m.id
            LEFT JOIN agent_runs ar ON ar.message_id = m.id AND ar.status = 'success'
            WHERE m.role = 'assistant'
              AND m.content LIKE ?
              AND c.abort_reason = 'process restart'
              AND ar.id IS NULL
              AND m.metadata LIKE '%"execution_status": "failed"%'
              AND m.metadata NOT LIKE '%auto_retried%'
            GROUP BY m.id
        """, (_PLACEHOLDER_PREFIX + "%",)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _auto_retry_one(item: dict) -> bool:
    """后台自动重试单条中断消息。返回是否成功触发。

    流程：
    1. 更新占位符为"自动重试中"
    2. 提取原始 user 消息
    3. RAG 检索
    4. 调用 orchestrate() 非流式执行
    5. 写回结果到 message（success 或 fallback）
    """
    import json as _json
    from db._conn import _get_conn
    from db.conversations import get_messages
    from services.rag import build_rag_context_with_details

    msg_id = item["msg_id"]
    conv_id = item["conv_id"]
    trace_id = item.get("trace_id") or f"retry-{msg_id}"

    # 1. 找原始 user 消息
    msgs = get_messages(conv_id, limit=200)
    target_idx = next((i for i, m in enumerate(msgs) if m["id"] == msg_id), -1)
    if target_idx < 0:
        logger.warning(f"[auto_retry] msg {msg_id} 找不到，跳过")
        return False
    original_query = ""
    for m in reversed(msgs[:target_idx]):
        if m["role"] == "user":
            original_query = m["content"] or ""
            break
    if not original_query:
        logger.warning(f"[auto_retry] msg {msg_id} 找不到原始 user 消息，跳过")
        return False

    # 2. 更新占位符为"自动重试中"（避免并发重复触发）
    conn = _get_conn()
    try:
        _apply_recovery(conn, msg_id, _RETRYING_NOTICE)
        conn.commit()
    finally:
        conn.close()

    logger.info(f"[auto_retry] msg {msg_id} (conv {conv_id}) 开始自动重试: {original_query[:50]}...")

    # 3. 后台执行 pipeline（非流式，结果写到 message）
    def _run_in_thread():
        try:
            # RAG 检索
            try:
                rag_result = build_rag_context_with_details(original_query)
                rag_context = rag_result.get("context", "")
            except Exception as e:
                logger.warning(f"[auto_retry] RAG 检索失败: {e}")
                rag_context = ""

            # 构建历史（不含占位符消息本身）
            history = [{"role": m["role"], "content": m["content"]}
                       for m in msgs[:target_idx] if m["role"] in ("user", "assistant")]
            history = history[-20:]

            # 调用 orchestrate（generator，消费到 type=answer 事件）
            from agent.orchestrator import orchestrate
            answer = ""
            specialist_results = []
            try:
                for event in orchestrate(
                    query=original_query,
                    history=history,
                    rag_context=rag_context,
                    conversation_id=conv_id,
                    message_id=msg_id,
                    trace_id=trace_id,
                ):
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") == "answer":
                        answer = event.get("content") or ""
                        specialist_results = event.get("specialist_results") or []
                        break  # 拿到最终答案即可
            except Exception as orch_err:
                logger.warning(f"[auto_retry] orchestrate 执行异常: {orch_err}")

            conn = _get_conn()
            try:
                if answer:
                    # 成功：写回结果，标记 completed
                    import json as _json2
                    row = conn.execute("SELECT metadata FROM messages WHERE id = ?", (msg_id,)).fetchone()
                    meta = {}
                    if row and row["metadata"]:
                        try:
                            meta = _json2.loads(row["metadata"])
                        except Exception:
                            meta = {}
                    meta["execution_status"] = "completed"
                    meta["auto_retried"] = True
                    conn.execute(
                        "UPDATE messages SET content = ?, metadata = ? WHERE id = ?",
                        (answer, _json2.dumps(meta, ensure_ascii=False), msg_id),
                    )
                    conn.commit()
                    logger.info(f"[auto_retry] msg {msg_id} 自动重试成功 (answer len={len(answer)})")
                else:
                    # 失败：标记中断提示重发
                    _apply_recovery(conn, msg_id, _NO_RESULT_NOTICE)
                    conn.commit()
                    logger.warning(f"[auto_retry] msg {msg_id} 自动重试未产出答案，标记中断")
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"[auto_retry] msg {msg_id} 自动重试异常: {e}")
            try:
                conn = _get_conn()
                _apply_recovery(conn, msg_id, _NO_RESULT_NOTICE)
                conn.commit()
                conn.close()
            except Exception:
                pass

    # 后台线程执行（避免阻塞启动）
    t = threading.Thread(target=_run_in_thread, name=f"auto-retry-{msg_id}", daemon=True)
    t.start()
    return True


def auto_retry_process_restart_interrupted() -> dict:
    """启动时自动重试 process restart 中断的消息。

    仅对 abort_reason='process restart' 且专家未执行（run_cnt=0）的消息重试。
    心跳超时（专家卡死）不自动重试，避免再次卡死。
    每条消息最多重试 1 次（通过 metadata.auto_retried 标记防重复）。

    Returns:
        {"triggered": int, "skipped": int}
    """
    from db.config import get_config_bool

    # 开关控制（默认开启）
    if not get_config_bool("auto_retry.process_restart_enabled", True):
        logger.info("[auto_retry] 开关关闭，跳过自动重试")
        return {"triggered": 0, "skipped": 0}

    stats = {"triggered": 0, "skipped": 0}
    try:
        targets = _find_process_restart_interrupted_messages()
        if not targets:
            logger.info("[auto_retry] 无 process restart 中断需重试")
            return stats

        logger.info(f"[auto_retry] 发现 {len(targets)} 条 process restart 中断，开始自动重试")
        for item in targets:
            msg_id = item["msg_id"]
            # 防重复已在 SQL 查询里过滤（metadata NOT LIKE '%auto_retried%'）
            triggered = _auto_retry_one(item)
            if triggered:
                stats["triggered"] += 1
            else:
                stats["skipped"] += 1

        logger.info(f"[auto_retry] 完成: {stats['triggered']} 条触发重试, {stats['skipped']} 条跳过")
    except Exception as e:
        logger.warning(f"[auto_retry] 自动重试失败（不影响启动）: {e}")
    return stats
