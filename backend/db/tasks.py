"""任务 CRUD。"""

import json
from datetime import datetime

from db._conn import _get_conn, _row_to_dict


def create_task(url: str) -> int:
    try:
        """创建任务，返回 task_id。"""
        conn = _get_conn()
        cur = conn.execute(
            "INSERT INTO tasks (url, status) VALUES (?, 'pending')",
            (url,)
        )
        task_id = cur.lastrowid
        conn.commit()
        conn.close()
        return task_id
    finally:
        conn.close()


def update_task(task_id: int, **fields):
    try:
        """更新任务字段。"""
        if not fields:
            return
        fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # dict 字段自动 json 序列化
        for k in ("codes_found", "market_data", "local_images"):
            if k in fields and isinstance(fields[k], (list, dict)):
                fields[k] = json.dumps(fields[k], ensure_ascii=False)

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [task_id]
        conn = _get_conn()
        conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
        conn.commit()
        conn.close()
    finally:
        conn.close()


def get_task(task_id: int) -> dict | None:
    try:
        """获取任务详情。"""
        conn = _get_conn()
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        conn.close()
        if not row:
            return None
        return _row_to_dict(row)
    finally:
        conn.close()


def list_tasks(limit: int = 50) -> list[dict]:
    try:
        """任务列表，按创建时间倒序。"""
        conn = _get_conn()
        rows = conn.execute(
            "SELECT id, url, title, status, created_at, updated_at FROM tasks ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def delete_task(task_id: int) -> bool:
    try:
        """删除任务。"""
        conn = _get_conn()
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
        deleted = cur.rowcount > 0
        conn.close()
        return deleted
    finally:
        conn.close()

