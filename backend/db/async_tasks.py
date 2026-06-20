"""异步分析任务 CRUD — 统一管理所有 Agent 分析的异步执行状态。"""

import json
from datetime import datetime

from db._conn import _get_conn, _row_to_dict


def init_async_tasks_table(conn):
    """建表，启动时由 init_db 调用。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS async_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT NOT NULL,
            caller TEXT DEFAULT '',
            status TEXT DEFAULT 'running',
            result TEXT DEFAULT '',
            error_msg TEXT DEFAULT '',
            token_usage INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_async_tasks_type ON async_tasks(task_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_async_tasks_status ON async_tasks(status)")


def create_async_task(task_type: str, caller: str = "") -> int:
    """创建异步任务记录，返回 task_id。"""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO async_tasks (task_type, caller, status) VALUES (?, ?, 'running')",
        (task_type, caller)
    )
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return task_id


def update_async_task(task_id: int, **fields) -> bool:
    """更新异步任务字段。"""
    if not fields:
        return False
    fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # result 字段如果是 dict/list 自动 JSON 序列化
    if "result" in fields and isinstance(fields["result"], (dict, list)):
        fields["result"] = json.dumps(fields["result"], ensure_ascii=False)

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [task_id]
    conn = _get_conn()
    cur = conn.execute(f"UPDATE async_tasks SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def get_async_task(task_id: int) -> dict | None:
    """获取异步任务详情。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM async_tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_dict(row)


def list_async_tasks(task_type: str = None, status: str = None, limit: int = 50) -> list[dict]:
    """列出异步任务，可按类型和状态过滤。"""
    conn = _get_conn()
    sql = "SELECT * FROM async_tasks"
    params = []
    conditions = []
    if task_type:
        conditions.append("task_type = ?")
        params.append(task_type)
    if status:
        conditions.append("status = ?")
        params.append(status)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_latest_async_task(task_type: str) -> dict | None:
    """获取指定类型最近一条任务（不限状态）。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM async_tasks WHERE task_type = ? ORDER BY id DESC LIMIT 1",
        (task_type,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_dict(row)


def get_latest_done_task(task_type: str) -> dict | None:
    """获取指定类型最近一条已完成的任务。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM async_tasks WHERE task_type = ? AND status = 'done' ORDER BY id DESC LIMIT 1",
        (task_type,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_dict(row)
