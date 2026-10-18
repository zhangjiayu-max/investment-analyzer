"""异步分析任务 CRUD — 统一管理所有 Agent 分析的异步执行状态。"""

import json
import hashlib
from datetime import datetime

from db._conn import _get_conn, _row_to_dict

STALE_TASK_MINUTES = 30


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
    # 添加 progress 字段（存储 JSON 格式的进度信息）
    try:
        conn.execute("ALTER TABLE async_tasks ADD COLUMN progress TEXT DEFAULT ''")
    except Exception:
        pass  # 列已存在
    columns = {r[1] for r in conn.execute('PRAGMA table_info(async_tasks)')}
    for name, definition in [('request_key', "TEXT DEFAULT ''"),
                             ('source_table', "TEXT DEFAULT ''"), ('source_id', 'INTEGER')]:
        if name not in columns:
            conn.execute(f'ALTER TABLE async_tasks ADD COLUMN {name} {definition}')
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_async_task_request_running "
                 "ON async_tasks(task_type, request_key) WHERE status='running' AND request_key != ''")


def start_analysis_task(task_type: str, caller: str, request_data: dict,
                        source_table: str, source_fields: dict) -> dict:
    """同一目标原子创建任务和分析记录，复用时始终返回对应记录 ID。"""
    allowed = {
        'analysis_history': {'index_code', 'index_name', 'agent_id', 'agent_name', 'prompt_used',
                             'news_context', 'valuation_context', 'result', 'token_usage'},
        'portfolio_analysis_records': {'user_id', 'analysis_type', 'summary', 'input_data',
                                      'result_data', 'token_usage', 'agent_id'},
    }
    if source_table not in allowed or not source_fields or set(source_fields) - allowed[source_table]:
        raise ValueError('无效的分析记录字段')
    from services.quality.eval_execution import evaluation_request_signature
    evaluation_signature = evaluation_request_signature()
    if evaluation_signature:
        request_data = {**request_data, '_evaluation_signature': evaluation_signature}
    request_key = hashlib.sha256(json.dumps(request_data, sort_keys=True, ensure_ascii=False,
                                           separators=(',', ':')).encode()).hexdigest()
    conn = _get_conn()
    try:
        cleanup_stale_async_tasks(_conn=conn)
        conn.execute('BEGIN IMMEDIATE')
        existing = conn.execute(
            "SELECT id,source_id FROM async_tasks WHERE task_type=? AND request_key=? AND status='running'",
            (task_type, request_key)).fetchone()
        if existing:
            task_id, record_id, reused = existing['id'], existing['source_id'], True
        else:
            fields = dict(source_fields, status='running')
            columns = ','.join(fields)
            placeholders = ','.join('?' for _ in fields)
            record_id = conn.execute(f'INSERT INTO {source_table} ({columns}) VALUES ({placeholders})',
                                     list(fields.values())).lastrowid
            task_id = conn.execute(
                "INSERT INTO async_tasks (task_type,caller,request_key,source_table,source_id,status) "
                "VALUES (?,?,?,?,?,'running')", (task_type, caller, request_key, source_table, record_id)).lastrowid
            reused = False
        conn.commit()
        return {'ok': True, 'id': record_id, 'record_id': record_id, 'task_id': task_id,
                'status': 'running', 'reused': reused}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
    # result / progress 字段如果是 dict/list 自动 JSON 序列化
    for key in ("result", "progress"):
        if key in fields and isinstance(fields[key], (dict, list)):
            fields[key] = json.dumps(fields[key], ensure_ascii=False)

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [task_id]
    conn = _get_conn()
    guard = ''
    if fields.get('status') in ('done', 'error', 'running'):
        guard = " AND (status = 'running' OR status = ?)"
        values.append(fields['status'])
    cur = conn.execute(f"UPDATE async_tasks SET {set_clause} WHERE id = ?{guard}", values)
    if fields.get('status') == 'error':
        _cleanup_analysis_records(conn, STALE_TASK_MINUTES)
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def get_async_task(task_id: int) -> dict | None:
    """获取异步任务详情。"""
    conn = _get_conn()
    cleanup_stale_async_tasks(_conn=conn)
    row = conn.execute("SELECT * FROM async_tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_dict(row)


def list_async_tasks(task_type: str = None, status: str = None, limit: int = 50) -> list[dict]:
    """列出异步任务，可按类型和状态过滤。"""
    conn = _get_conn()
    cleanup_stale_async_tasks(_conn=conn)
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
    cleanup_stale_async_tasks(_conn=conn)
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


def get_running_async_task(task_type: str) -> dict | None:
    """获取指定类型最近一条 running 状态的任务(幂等保护用)。

    用于:触发新任务前先查是否已有同类型任务在执行,避免重复触发。
    返回 None 表示无运行中任务,可以触发新任务。
    """
    conn = _get_conn()
    cleanup_stale_async_tasks(_conn=conn)
    row = conn.execute(
        "SELECT * FROM async_tasks WHERE task_type = ? AND status = 'running' ORDER BY id DESC LIMIT 1",
        (task_type,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_dict(row)


def cleanup_stale_async_tasks(max_age_minutes: int = STALE_TASK_MINUTES, _conn=None) -> int:
    """将长时间无更新的 running 任务标记为 error，避免重启后永久卡住。"""
    own_conn = _conn is None
    conn = _conn or _get_conn()
    try:
        cur = conn.execute(
            """UPDATE async_tasks
               SET status = 'error',
                   error_msg = ?,
                   updated_at = datetime('now','localtime')
             WHERE status = 'running'
               AND updated_at < datetime('now','localtime', ?)""",
            (f"任务超过 {max_age_minutes} 分钟未更新，已自动标记失败", f"-{max_age_minutes} minutes"),
        )
        count = cur.rowcount
        _cleanup_analysis_records(conn, max_age_minutes)
        conn.commit()
        return count
    finally:
        if own_conn:
            conn.close()


def _cleanup_analysis_records(conn, max_age_minutes: int):
    """同步任务终态，并清理没有活跃任务关联的历史悬挂记录。"""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    error = f'分析超过 {max_age_minutes} 分钟未更新，执行已中断，请重试'
    for table in ('portfolio_analysis_records', 'analysis_history'):
        if table not in tables:
            continue
        columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        if not {'status', 'error_msg', 'created_at'} <= columns:
            continue
        conn.execute(f"""UPDATE {table} SET status='error', error_msg=COALESCE(
                (SELECT NULLIF(t.error_msg,'') FROM async_tasks t WHERE t.source_table=?
                 AND t.source_id={table}.id AND t.status='error' ORDER BY t.id DESC LIMIT 1), ?)
            WHERE status='running' AND (
                EXISTS (SELECT 1 FROM async_tasks t WHERE t.source_table=? AND t.source_id={table}.id
                        AND t.status='error')
                OR (created_at < datetime('now','localtime', ?) AND NOT EXISTS (
                    SELECT 1 FROM async_tasks t WHERE t.source_table=? AND t.source_id={table}.id
                    AND t.status='running')))
            """, (table, error, table, f'-{max_age_minutes} minutes', table))
    if 'agent_analysis_log' in tables:
        conditions = []
        for table in ('portfolio_analysis_records', 'analysis_history'):
            if table in tables:
                columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
                if 'status' in columns:
                    conditions.append(f"(source_table='{table}' AND source_id IN (SELECT id FROM {table} WHERE status='error'))")
        conditions.append("""(created_at < datetime('now','localtime', ?) AND NOT EXISTS (
            SELECT 1 FROM async_tasks t WHERE t.source_table=agent_analysis_log.source_table
            AND t.source_id=agent_analysis_log.source_id AND t.status='running'))""")
        conn.execute("""UPDATE agent_analysis_log SET status='error',error_msg=?,
            completed_at=datetime('now','localtime') WHERE status='running' AND ("""
            + ' OR '.join(conditions) + ')', (error, f'-{max_age_minutes} minutes'))
