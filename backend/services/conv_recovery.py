"""中断对话恢复 — 启动时检测并修复因重启中断的对话。

场景：后端重启时，正在执行的 SSE 流被中断，导致：
- 专家分析已成功（agent_runs.status='success'）
- 但 synthesis 阶段未执行 → assistant message 仍是占位符

恢复策略：
1. 扫描所有 content 为占位符的 assistant message
2. 检查该 message 是否有 status='success' 的 agent_runs（按 message_id 过滤）
3. 若有，合并专家结果写回 message
4. 若无（专家也没完成），将占位符替换为中断提示

注意：必须按 message_id 过滤 agent_runs，不能用 conversation_id。
否则同一对话多轮中，会把其他轮次的专家结果错误合并到中断轮次。

触发时机：
- 启动时全量扫描（recover_interrupted_conversations）
- 心跳超时单条恢复（recover_message），避免依赖后端重启
- 启动时自动重试 process restart 中断（auto_retry_process_restart_interrupted）
"""
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# 占位符前缀（匹配所有 ⏳ 开头的临时状态文本）
_PLACEHOLDER_PREFIX = "⏳"

# 中断提示文本（有部分专家结果时）
_INTERRUPTED_NOTICE = (
    "⚠️ 本次分析因服务重启中断。"
    "如需完整分析，请点击「重新生成」。\n\n"
    "---\n\n以下为中断前已完成的专家分析片段：\n\n"
)

# 中断提示文本（专家未执行时）
_NO_RESULT_NOTICE = "⚠️ 本次分析因服务中断未完成（专家未执行）。请重新发送问题以获得完整分析。"

# 自动重试中提示文本
_RETRYING_NOTICE = "⏳ 检测到服务重启导致中断，正在自动重试..."


def _merge_runs_to_answer(runs) -> str | None:
    """把 agent_runs 合并成中断恢复后的 content。无有效结果返回 None。"""
    parts = []
    for r in runs:
        text = r["result"] or ""
        # 去掉 JSON 块，从第一个 markdown 标题开始取
        idx = text.find("\n## ")
        body = text[idx + 1:] if idx >= 0 else text
        agent_name = r["agent_name"] or "专家分析"
        parts.append(f"\n## {agent_name}\n")
        parts.append(body.strip())
    if not parts:
        return None
    return _INTERRUPTED_NOTICE + "\n".join(parts)


def _apply_recovery(conn, msg_id: int, content: str) -> None:
    """统一恢复写回：同时更新 content 和 metadata.execution_status='failed'。

    关键：必须更新 metadata.execution_status，否则前端看到 streaming 状态
    会显示"后台执行中"+"恢复连接"按钮，但任务已死，点击无效。
    改为 failed 后前端显示"执行失败"+"重新生成"按钮（走 retry-message 接口，有效）。
    """
    import json as _json
    row = conn.execute("SELECT metadata FROM messages WHERE id = ?", (msg_id,)).fetchone()
    meta = {}
    if row and row["metadata"]:
        try:
            meta = _json.loads(row["metadata"])
        except Exception:
            meta = {}
    meta["execution_status"] = "failed"
    meta["recovered"] = True  # 标记为恢复产生，便于排查
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
            SELECT agent_name, result, run_phase
            FROM agent_runs
            WHERE message_id = ? AND status = 'success'
            ORDER BY id
        """, (msg_id,)).fetchall()

        if runs:
            full_answer = _merge_runs_to_answer(runs)
            if full_answer:
                _apply_recovery(conn, msg_id, full_answer)
                conn.commit()
                logger.info(f"[conv_recovery] msg {msg_id} (conv {conv_id}) 心跳超时恢复（合并 {len(runs)} 个专家结果）")
                return full_answer
        # 无专家结果 → 标记为中断
        _apply_recovery(conn, msg_id, _NO_RESULT_NOTICE)
        conn.commit()
        logger.info(f"[conv_recovery] msg {msg_id} (conv {conv_id}) 心跳超时，无专家结果，标记为中断")
        return _NO_RESULT_NOTICE
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
                # 有专家结果 → 合并写回
                full_answer = _merge_runs_to_answer(runs)
                if full_answer:
                    _apply_recovery(conn, msg_id, full_answer)
                    stats["recovered"] += 1
                    logger.info(f"[conv_recovery] msg {msg_id} (conv {conv_id}) 已恢复（合并 {len(runs)} 个专家结果）")
                else:
                    stats["skipped"] += 1
            else:
                # 无专家结果 → 标记为中断
                _apply_recovery(conn, msg_id, _NO_RESULT_NOTICE)
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
