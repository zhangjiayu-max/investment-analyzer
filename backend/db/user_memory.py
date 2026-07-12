"""用户记忆数据层 — CRUD 操作。"""

from datetime import datetime

from db._conn import _get_conn, _row_to_dict


def create_user_memory(content, memory_type='preference', last_accessed_at=None):
    conn = _get_conn()
    now = last_accessed_at or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur = conn.execute("""
        INSERT INTO user_memory (content, memory_type, relevance_score, last_accessed_at, created_at, updated_at)
        VALUES (?, ?, NULL, ?, ?, ?)
    """, (content, memory_type, now, now, now))
    conn.commit()
    conn.close()
    return cur.lastrowid


def get_user_memory(memory_id):
    conn = _get_conn()
    row = conn.execute("SELECT * FROM user_memory WHERE id = ?", (memory_id,)).fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def list_user_memory(memory_type=None):
    conn = _get_conn()
    query = "SELECT * FROM user_memory"
    params = []
    if memory_type:
        query += " WHERE memory_type = ?"
        params.append(memory_type)
    query += " ORDER BY last_accessed_at DESC NULLS LAST, created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def update_user_memory(memory_id, **fields):
    conn = _get_conn()
    fields['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    set_clause = ', '.join(f"{k} = ?" for k in fields.keys())
    params = list(fields.values()) + [memory_id]
    conn.execute(f"UPDATE user_memory SET {set_clause} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return True


def delete_user_memory(memory_id):
    conn = _get_conn()
    cur = conn.execute("DELETE FROM user_memory WHERE id = ?", (memory_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0
