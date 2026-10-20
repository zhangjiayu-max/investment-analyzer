"""螺丝钉图片解析任务 CRUD。"""

import json
from datetime import datetime

from db._conn import _get_conn, _row_to_dict

STALE_PARSE_TASK_MINUTES = 10


def _expire_stale_tasks(conn):
    """查询和提交都清理失联任务，避免页面永久轮询 parsing。"""
    conn.execute(
        """UPDATE dd_parse_tasks
        SET status = 'error',
            error_msg = CASE WHEN error_msg IS NULL OR error_msg = ''
                             THEN '解析任务超时或服务已重启，请重新提交'
                             ELSE error_msg END,
            updated_at = datetime('now', 'localtime')
        WHERE status IN ('pending', 'parsing')
          AND updated_at < datetime('now', 'localtime', ?)""",
        (f'-{STALE_PARSE_TASK_MINUTES} minutes',),
    )
    conn.commit()


def create_dd_parse_task(image_path: str, image_name: str = "", parse_type: str = "dd") -> int:
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


def update_dd_parse_task(task_id: int, **fields):
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


def get_dd_parse_task(task_id: int) -> dict | None:
    """获取任务详情。"""
    conn = _get_conn()
    _expire_stale_tasks(conn)
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


def find_running_task(image_path: str, parse_type: str | None = None) -> dict | None:
    """查找同一图片正在执行的任务（去重用）。

    解析任务由进程内后台协程执行；服务重载或进程崩溃后，数据库里的
    ``pending/parsing`` 状态可能没有机会收尾。超过视觉模型最大重试时长
    的任务视为陈旧并标记失败，允许用户重新提交。
    """
    conn = _get_conn()
    stale_cutoff = f"-{STALE_PARSE_TASK_MINUTES} minutes"
    _expire_stale_tasks(conn)
    type_filter = " AND parse_type = ?" if parse_type else ""
    row = conn.execute(f"""
        SELECT * FROM dd_parse_tasks
        WHERE image_path = ? AND status IN ('pending', 'parsing')
          AND updated_at >= datetime('now', 'localtime', ?){type_filter}
        ORDER BY id DESC LIMIT 1
    """, (image_path, stale_cutoff, parse_type) if parse_type else (image_path, stale_cutoff)).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_dict(row)


def list_dd_parse_tasks(status: str = None, limit: int = 50) -> list[dict]:
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
