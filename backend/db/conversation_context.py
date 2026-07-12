"""对话上下文数据层 — CRUD 操作。"""

import json
from datetime import datetime

from db._conn import _get_conn, _row_to_dict


def set_conversation_context(conversation_id, context_key, context_value):
    conn = _get_conn()
    value_json = json.dumps(context_value, ensure_ascii=False)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    existing = conn.execute(
        "SELECT id FROM conversation_context WHERE conversation_id = ? AND context_key = ?",
        (conversation_id, context_key)
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE conversation_context SET context_value = ?, updated_at = ? WHERE id = ?",
            (value_json, now, existing[0])
        )
    else:
        conn.execute(
            "INSERT INTO conversation_context (conversation_id, context_key, context_value, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (conversation_id, context_key, value_json, now, now)
        )

    conn.commit()
    conn.close()
    return True


def get_conversation_context(conversation_id, context_key=None):
    conn = _get_conn()
    if context_key:
        row = conn.execute(
            "SELECT * FROM conversation_context WHERE conversation_id = ? AND context_key = ?",
            (conversation_id, context_key)
        ).fetchone()
        conn.close()
        if row:
            row_dict = _row_to_dict(row)
            row_dict['context_value'] = json.loads(row_dict['context_value'])
            return row_dict
        return None
    else:
        rows = conn.execute(
            "SELECT * FROM conversation_context WHERE conversation_id = ?",
            (conversation_id,)
        ).fetchall()
        conn.close()
        result = {}
        for row in rows:
            row_dict = _row_to_dict(row)
            result[row_dict['context_key']] = json.loads(row_dict['context_value'])
        return result


def delete_conversation_context(conversation_id, context_key=None):
    conn = _get_conn()
    if context_key:
        conn.execute(
            "DELETE FROM conversation_context WHERE conversation_id = ? AND context_key = ?",
            (conversation_id, context_key)
        )
    else:
        conn.execute(
            "DELETE FROM conversation_context WHERE conversation_id = ?",
            (conversation_id,)
        )
    conn.commit()
    conn.close()
    return True
