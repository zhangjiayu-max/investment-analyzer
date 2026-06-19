"""对话 + 消息 + 摘要 CRUD。"""

from datetime import datetime

from db._conn import _get_conn


# ── Conversation CRUD ──────────────────────────────────────

def list_conversations() -> list[dict]:
    """列出所有对话，按更新时间倒序。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT c.*, a.name as agent_name, a.icon as agent_icon,
               (SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) as message_count
        FROM conversations c
        LEFT JOIN agents a ON c.agent_id = a.id
        ORDER BY c.updated_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_conversation(conv_id: int) -> dict | None:
    """获取单个对话。"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT c.*, a.name as agent_name, a.icon as agent_icon
        FROM conversations c
        LEFT JOIN agents a ON c.agent_id = a.id
        WHERE c.id = ?
    """, (conv_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_conversation(title: str = "新对话", agent_id: int = None,
                        context_data: str = None) -> int:
    """创建对话。"""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO conversations (title, agent_id, context_data) VALUES (?, ?, ?)",
        (title, agent_id, context_data),
    )
    conv_id = cur.lastrowid
    conn.commit()
    conn.close()
    return conv_id


def update_conversation(conv_id: int, **fields):
    """更新对话字段。"""
    if not fields:
        return
    fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [conv_id]
    conn.execute(f"UPDATE conversations SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def delete_conversation(conv_id: int):
    """删除对话及其所有消息。"""
    conn = _get_conn()
    conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
    conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    conn.commit()
    conn.close()


# ── Message CRUD ──────────────────────────────────────

def get_messages(conv_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
    """获取对话的消息历史（最近 N 条，支持分页）。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT * FROM messages
        WHERE conversation_id = ?
        ORDER BY id DESC
        LIMIT ? OFFSET ?
    """, (conv_id, limit, offset)).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


def create_message(conv_id: int, role: str, content: str, metadata: str = None) -> int:
    """创建消息。"""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO messages (conversation_id, role, content, metadata) VALUES (?, ?, ?, ?)",
        (conv_id, role, content, metadata),
    )
    msg_id = cur.lastrowid
    conn.execute("UPDATE conversations SET updated_at = datetime('now','localtime') WHERE id = ?", (conv_id,))
    conn.commit()
    conn.close()
    return msg_id


def update_message_metadata(msg_id: int, metadata_dict: dict):
    """更新消息的 metadata（增量保存执行进度）。"""
    import json as _json
    conn = _get_conn()
    conn.execute("UPDATE messages SET metadata = ? WHERE id = ?",
                 (_json.dumps(metadata_dict, ensure_ascii=False), msg_id))
    conn.commit()
    conn.close()


def update_message_content_and_metadata(msg_id: int, content: str, metadata_dict: dict):
    """更新消息的 content 和 metadata（最终保存）。"""
    import json as _json
    conn = _get_conn()
    conn.execute("UPDATE messages SET content = ?, metadata = ? WHERE id = ?",
                 (content, _json.dumps(metadata_dict, ensure_ascii=False), msg_id))
    conn.commit()
    conn.close()


# ── 对话摘要缓存 ──────────────────────────────────

def get_conversation_summary(conversation_id: int) -> dict | None:
    """获取对话的最新摘要。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM conversation_summaries WHERE conversation_id = ? ORDER BY id DESC LIMIT 1",
        (conversation_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_conversation_summary(conversation_id: int, up_to_message_id: int, summary: str) -> int:
    """保存对话摘要。"""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO conversation_summaries (conversation_id, up_to_message_id, summary) VALUES (?, ?, ?)",
        (conversation_id, up_to_message_id, summary)
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


# ── 任务状态查询 ──────────────────────────────────

def get_running_conversations() -> list[dict]:
    """获取所有正在执行的对话（状态为 streaming 的消息）。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT DISTINCT
            m.conversation_id,
            c.title as conversation_title,
            m.id as message_id,
            m.created_at as started_at,
            m.metadata
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE m.role = 'assistant'
          AND json_extract(m.metadata, '$.execution_status') = 'streaming'
        ORDER BY m.created_at DESC
    """).fetchall()
    conn.close()

    results = []
    for row in rows:
        metadata = {}
        try:
            import json
            metadata = json.loads(row['metadata']) if row['metadata'] else {}
        except:
            pass

        results.append({
            "conversation_id": row['conversation_id'],
            "conversation_title": row['conversation_title'],
            "message_id": row['message_id'],
            "started_at": row['started_at'],
            "complexity": metadata.get('complexity', 'unknown'),
            "specialist_results": metadata.get('specialist_results', {}),
        })

    return results


def get_conversation_progress(conv_id: int) -> dict:
    """获取对话执行进度。"""
    conn = _get_conn()

    # 获取最新的 assistant 消息
    msg = conn.execute("""
        SELECT id, metadata FROM messages
        WHERE conversation_id = ? AND role = 'assistant'
        ORDER BY id DESC LIMIT 1
    """, (conv_id,)).fetchone()

    if not msg:
        conn.close()
        return {"status": "no_message", "progress": 0}

    metadata = {}
    try:
        import json
        metadata = json.loads(msg['metadata']) if msg['metadata'] else {}
    except:
        pass

    status = metadata.get('execution_status', 'unknown')

    # 从 agent_runs 获取已完成的专家数量
    completed_runs = conn.execute("""
        SELECT COUNT(*) as count FROM agent_runs
        WHERE conversation_id = ? AND message_id = ? AND status = 'completed'
    """, (conv_id, msg['id'])).fetchone()['count']

    total_runs = conn.execute("""
        SELECT COUNT(*) as count FROM agent_runs
        WHERE conversation_id = ? AND message_id = ?
    """, (conv_id, msg['id'])).fetchone()['count']

    conn.close()

    # 计算进度百分比
    progress = 0
    if total_runs > 0:
        progress = int((completed_runs / total_runs) * 100)

    return {
        "status": status,
        "message_id": msg['id'],
        "completed_specialists": completed_runs,
        "total_specialists": total_runs,
        "progress": progress,
        "complexity": metadata.get('complexity', 'unknown'),
    }
