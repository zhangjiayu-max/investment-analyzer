"""中断对话恢复 — 启动时检测并修复因重启中断的对话。

场景：后端重启时，正在执行的 SSE 流被中断，导致：
- 专家分析已成功（agent_runs.status='success'）
- 但 synthesis 阶段未执行 → assistant message 仍是占位符

恢复策略：
1. 扫描所有 content 为占位符的 assistant message
2. 检查该 conversation 是否有 status='success' 的 agent_runs
3. 若有，合并专家结果写回 message
4. 若无（专家也没完成），将占位符替换为中断提示
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# 占位符前缀（匹配所有 ⏳ 开头的临时状态文本）
_PLACEHOLDER_PREFIX = "⏳"

# 中断提示文本
_INTERRUPTED_NOTICE = (
    "⚠️ 本次分析因服务重启中断。"
    "如需完整分析，请点击「重新生成」。\n\n"
    "---\n\n以下为中断前已完成的专家分析片段：\n\n"
)


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

            # 查该对话是否有成功的专家结果
            runs = conn.execute("""
                SELECT agent_name, result, run_phase
                FROM agent_runs
                WHERE conversation_id = ? AND status = 'success'
                ORDER BY id
            """, (conv_id,)).fetchall()

            if runs:
                # 有专家结果 → 合并写回
                parts = []
                for r in runs:
                    text = r["result"] or ""
                    # 去掉 JSON 块，从第一个 markdown 标题开始取
                    idx = text.find("\n## ")
                    body = text[idx + 1:] if idx >= 0 else text
                    agent_name = r["agent_name"] or "专家分析"
                    parts.append(f"\n## {agent_name}\n")
                    parts.append(body.strip())

                if parts:
                    full_answer = _INTERRUPTED_NOTICE + "\n".join(parts)
                    conn.execute(
                        "UPDATE messages SET content = ? WHERE id = ?",
                        (full_answer, msg_id),
                    )
                    stats["recovered"] += 1
                    logger.info(f"[conv_recovery] conv {conv_id} 已恢复（合并 {len(runs)} 个专家结果）")
                else:
                    stats["skipped"] += 1
            else:
                # 无专家结果 → 标记为中断
                conn.execute(
                    "UPDATE messages SET content = ? WHERE id = ?",
                    ("⚠️ 本次分析因服务重启中断，未产生有效结果。请重新发送问题。", msg_id),
                )
                stats["marked_interrupted"] += 1
                logger.info(f"[conv_recovery] conv {conv_id} 无专家结果，标记为中断")

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
