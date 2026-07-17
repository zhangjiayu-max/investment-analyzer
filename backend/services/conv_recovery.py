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
"""
import logging
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
                conn.execute(
                    "UPDATE messages SET content = ? WHERE id = ?",
                    (full_answer, msg_id),
                )
                conn.commit()
                logger.info(f"[conv_recovery] msg {msg_id} (conv {conv_id}) 心跳超时恢复（合并 {len(runs)} 个专家结果）")
                return full_answer
        # 无专家结果 → 标记为中断
        conn.execute(
            "UPDATE messages SET content = ? WHERE id = ?",
            (_NO_RESULT_NOTICE, msg_id),
        )
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
                    conn.execute(
                        "UPDATE messages SET content = ? WHERE id = ?",
                        (full_answer, msg_id),
                    )
                    stats["recovered"] += 1
                    logger.info(f"[conv_recovery] msg {msg_id} (conv {conv_id}) 已恢复（合并 {len(runs)} 个专家结果）")
                else:
                    stats["skipped"] += 1
            else:
                # 无专家结果 → 标记为中断
                conn.execute(
                    "UPDATE messages SET content = ? WHERE id = ?",
                    (_NO_RESULT_NOTICE, msg_id),
                )
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
