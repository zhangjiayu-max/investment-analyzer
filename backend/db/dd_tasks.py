"""螺丝钉图片解析任务 CRUD。"""

import json
from datetime import datetime

from db._conn import _get_conn, _row_to_dict


def create_dd_parse_task(image_path: str, image_name: str = "", parse_type: str = "dd") -> int:
    try:
        """创建解析任务，返回 task_id。"""
        conn = _get_conn()
        cur = conn.execute(
            "INSERT INTO dd_parse_tasks (image_path, image_name, parse_type) VALUES (?, ?, ?)",
            (image_path, image_name, parse_type)
        )
        task_id = cur.lastrowid
        conn.commit()
        conn.close()
        return task_id
    finally:
        conn.close()


def update_dd_parse_task(task_id: int, **fields):
    try:
        """更新任务字段。"""
        if not fields:
            return
        fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if "result_json" in fields and isinstance(fields["result_json"], (dict, list)):
            fields["result_json"] = json.dumps(fields["result_json"], ensure_ascii=False)

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [task_id]
        conn = _get_conn()
        conn.execute(f"UPDATE dd_parse_tasks SET {set_clause} WHERE id = ?", values)
        conn.commit()
        conn.close()
    finally:
        conn.close()


def get_dd_parse_task(task_id: int) -> dict | None:
    try:
        """获取任务详情。"""
        conn = _get_conn()
        row = conn.execute("SELECT * FROM dd_parse_tasks WHERE id = ?", (task_id,)).fetchone()
        conn.close()
        if not row:
            return None
        d = _row_to_dict(row)
        # 反序列化 result_json
        if d.get("result_json") and isinstance(d["result_json"], str):
            try:
                d["result_json"] = json.loads(d["result_json"])
            except (json.JSONDecodeError, TypeError):
                pass
        return d
    finally:
        conn.close()


def find_running_task(image_path: str) -> dict | None:
    try:
        """查找同一图片正在执行或最近完成的任务（去重用）。"""
        conn = _get_conn()
        row = conn.execute("""
            SELECT * FROM dd_parse_tasks
            WHERE image_path = ? AND status IN ('pending', 'parsing')
            ORDER BY id DESC LIMIT 1
        """, (image_path,)).fetchone()
        conn.close()
        if not row:
            return None
        return _row_to_dict(row)
    finally:
        conn.close()


def list_dd_parse_tasks(status: str = None, limit: int = 50) -> list[dict]:
    try:
        """任务列表。"""
        conn = _get_conn()
        if status:
            rows = conn.execute(
                "SELECT * FROM dd_parse_tasks WHERE status = ? ORDER BY id DESC LIMIT ?",
                (status, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM dd_parse_tasks ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        conn.close()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()

