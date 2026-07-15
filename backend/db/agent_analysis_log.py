"""分析 Agent 统一记录体系 — agent_analysis_log 表。

所有分析路由执行时写入一条统一格式的记录，作为分析执行的统一索引，
可按 agent / 时间 / 类型快速定位分析数据与结果。
"""
import json
import logging

from db._conn import _get_conn

logger = logging.getLogger(__name__)


def init_table():
    """建表（由 init_db 调用）。"""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_analysis_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT UNIQUE NOT NULL,
            agent_id INTEGER,
            agent_name TEXT,
            analysis_type TEXT NOT NULL,
            source_table TEXT NOT NULL,
            source_id INTEGER,
            query TEXT,
            input_summary TEXT,
            status TEXT DEFAULT 'running',
            duration_ms INTEGER,
            token_usage INTEGER,
            has_eval INTEGER DEFAULT 0,
            eval_score REAL,
            error_msg TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            completed_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_aal_agent ON agent_analysis_log(agent_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_aal_type ON agent_analysis_log(analysis_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_aal_created ON agent_analysis_log(created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_aal_trace ON agent_analysis_log(trace_id)")
    conn.commit()
    conn.close()


def create_analysis_log(trace_id: str, analysis_type: str, source_table: str,
                        agent_id: int = None, agent_name: str = "",
                        source_id: int = None, query: str = "",
                        input_summary: str = "", status: str = "running") -> int:
    """创建分析执行记录（status=running），返回 log_id。"""
    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO agent_analysis_log
            (trace_id, agent_id, agent_name, analysis_type, source_table,
             source_id, query, input_summary, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (trace_id, agent_id, agent_name, analysis_type, source_table,
          source_id, query[:500] if query else "", input_summary, status))
    log_id = cur.lastrowid
    conn.commit()
    conn.close()
    return log_id


def complete_analysis_log(trace_id: str, status: str, duration_ms: int = None,
                          token_usage: int = None, error_msg: str = None) -> bool:
    """完成分析记录（status=done/error），更新耗时、token、错误信息。"""
    conn = _get_conn()
    cur = conn.execute("""
        UPDATE agent_analysis_log SET
            status = ?, duration_ms = ?, token_usage = ?,
            error_msg = ?, completed_at = datetime('now','localtime')
        WHERE trace_id = ?
    """, (status, duration_ms, token_usage, error_msg, trace_id))
    updated = cur.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def update_analysis_log_source(trace_id: str, source_id: int) -> bool:
    """回补 source_id（running 阶段创建时 source_id=None，业务记录创建后再更新）。

    用于避免同一 trace_id 重复 INSERT 触发 UNIQUE 冲突。
    """
    conn = _get_conn()
    cur = conn.execute("""
        UPDATE agent_analysis_log SET source_id = ? WHERE trace_id = ?
    """, (source_id, trace_id))
    updated = cur.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def get_analysis_log(log_id: int) -> dict | None:
    """获取单条分析记录。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM agent_analysis_log WHERE id = ?", (log_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_analysis_log_by_trace(trace_id: str) -> dict | None:
    """按 trace_id 获取分析记录。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM agent_analysis_log WHERE trace_id = ?", (trace_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_analysis_logs(agent_id: int = None, analysis_type: str = None,
                       status: str = None, date_from: str = None, date_to: str = None,
                       limit: int = 50, offset: int = 0) -> list[dict]:
    """查询分析记录列表，支持多维度过滤。"""
    sql = "SELECT * FROM agent_analysis_log WHERE 1=1"
    params = []
    if agent_id is not None:
        sql += " AND agent_id = ?"
        params.append(agent_id)
    if analysis_type:
        sql += " AND analysis_type = ?"
        params.append(analysis_type)
    if status:
        sql += " AND status = ?"
        params.append(status)
    if date_from:
        sql += " AND created_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND created_at <= ?"
        params.append(date_to)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    conn = _get_conn()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_analysis_logs(agent_id: int = None, analysis_type: str = None,
                        status: str = None, date_from: str = None,
                        date_to: str = None) -> int:
    """统计分析记录总数（配合 list 分页）。"""
    sql = "SELECT COUNT(*) FROM agent_analysis_log WHERE 1=1"
    params = []
    if agent_id is not None:
        sql += " AND agent_id = ?"
        params.append(agent_id)
    if analysis_type:
        sql += " AND analysis_type = ?"
        params.append(analysis_type)
    if status:
        sql += " AND status = ?"
        params.append(status)
    if date_from:
        sql += " AND created_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND created_at <= ?"
        params.append(date_to)
    conn = _get_conn()
    count = conn.execute(sql, params).fetchone()[0]
    conn.close()
    return count


def get_analysis_stats() -> dict:
    """今日统计：总数 / 平均耗时 / 平均 token / 已评估数。"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT
            COUNT(*) AS today_total,
            AVG(CASE WHEN duration_ms IS NOT NULL AND status='done' THEN duration_ms END) AS avg_duration,
            AVG(CASE WHEN token_usage IS NOT NULL AND token_usage > 0 THEN token_usage END) AS avg_token,
            SUM(CASE WHEN has_eval = 1 THEN 1 ELSE 0 END) AS eval_count
        FROM agent_analysis_log
        WHERE date(created_at) = date('now','localtime')
    """).fetchone()
    conn.close()
    return {
        "today_total": row[0] or 0,
        "avg_duration": int(row[1] or 0),
        "avg_token": int(row[2] or 0),
        "eval_count": row[3] or 0,
    }


def update_eval_result(log_id: int, eval_score: float) -> bool:
    """回写质量评估结果。"""
    conn = _get_conn()
    cur = conn.execute("""
        UPDATE agent_analysis_log SET has_eval = 1, eval_score = ? WHERE id = ?
    """, (eval_score, log_id))
    updated = cur.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def fetch_source_result(source_table: str, source_id: int) -> str:
    """按 source_table + source_id 查回原始记录的完整 result。"""
    conn = _get_conn()
    try:
        if source_table == "analysis_history":
            row = conn.execute("SELECT result FROM analysis_history WHERE id = ?", (source_id,)).fetchone()
        elif source_table == "portfolio_analysis_records":
            row = conn.execute("SELECT result_data FROM portfolio_analysis_records WHERE id = ?", (source_id,)).fetchone()
        elif source_table == "health_scores":
            row = conn.execute("SELECT detail_json FROM health_scores WHERE id = ?", (source_id,)).fetchone()
        else:
            conn.close()
            return ""
        conn.close()
        if row:
            return row[0] or ""
        return ""
    except Exception as e:
        conn.close()
        logger.warning(f"查回原始记录失败 {source_table}:{source_id}: {e}")
        return ""
