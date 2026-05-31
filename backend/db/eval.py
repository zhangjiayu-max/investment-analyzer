"""评测集 (Eval Suite) CRUD。"""

from datetime import datetime

from db._conn import _get_conn
from db._utils import _add_column_if_not_exists


def init_eval_tables(conn):
    """初始化评测集相关表。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS eval_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            analysis_type TEXT NOT NULL,
            input_params TEXT NOT NULL DEFAULT '{}',
            expected_quality TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS eval_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER REFERENCES eval_cases(id),
            analysis_type TEXT NOT NULL,
            result_summary TEXT,
            score REAL,
            result_data TEXT,
            duration_ms INTEGER DEFAULT 0,
            token_usage INTEGER DEFAULT 0,
            error_msg TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_runs_case ON eval_runs(case_id)")
    # 评分原因字段（LLM-as-Judge 评语）
    _add_column_if_not_exists(conn, "eval_runs", "score_reason", "TEXT DEFAULT ''")
    # Agent 关联（用于回归测试）
    _add_column_if_not_exists(conn, "eval_cases", "agent_id", "INTEGER")
    _add_column_if_not_exists(conn, "eval_cases", "agent_type", "TEXT DEFAULT ''")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_id TEXT,
            index_name TEXT NOT NULL,
            index_code TEXT,
            direction TEXT NOT NULL,
            reason TEXT,
            confidence TEXT,
            status TEXT DEFAULT 'pending',
            baseline_value REAL,
            baseline_date TEXT,
            current_value REAL,
            current_date TEXT,
            change_pct REAL,
            verified_at TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_status ON recommendations(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_created ON recommendations(created_at)")
    _add_column_if_not_exists(conn, "recommendations", "verify_after_date", "TEXT")
    _add_column_if_not_exists(conn, "recommendations", "benchmark_change_pct", "REAL")
    _add_column_if_not_exists(conn, "recommendations", "verify_window_days", "INTEGER DEFAULT 5")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS recommendation_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_id INTEGER REFERENCES recommendations(id),
            rating TEXT NOT NULL DEFAULT 'neutral',
            tags TEXT DEFAULT '',
            comment TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_feedback_rec ON recommendation_feedback(recommendation_id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            caller TEXT NOT NULL,
            input_summary TEXT DEFAULT '',
            output_summary TEXT DEFAULT '',
            rating TEXT NOT NULL DEFAULT 'neutral',
            tags TEXT DEFAULT '',
            comment TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_feedback_caller ON llm_feedback(caller)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_feedback_rating ON llm_feedback(rating)")
    _add_column_if_not_exists(conn, "llm_feedback", "reason_tag", "TEXT DEFAULT ''")
    # 多维度评分
    _add_column_if_not_exists(conn, "llm_feedback", "score_data_accuracy", "INTEGER")
    _add_column_if_not_exists(conn, "llm_feedback", "score_logic", "INTEGER")
    _add_column_if_not_exists(conn, "llm_feedback", "score_actionability", "INTEGER")
    _add_column_if_not_exists(conn, "llm_feedback", "overall_score", "REAL")
    _add_column_if_not_exists(conn, "llm_feedback", "target_type", "TEXT DEFAULT ''")
    _add_column_if_not_exists(conn, "llm_feedback", "target_id", "INTEGER")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS analysis_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_key TEXT UNIQUE NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default' UNIQUE,
            preferences_json TEXT DEFAULT '{}',
            feedback_summary TEXT DEFAULT '',
            positive_patterns TEXT DEFAULT '[]',
            negative_patterns TEXT DEFAULT '[]',
            total_feedback_count INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_profiles_uid ON user_profiles(user_id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversation_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            up_to_message_id INTEGER NOT NULL,
            summary TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_summaries_cid ON conversation_summaries(conversation_id)")


def create_eval_case(name: str, analysis_type: str, input_params: str = "{}",
                     description: str = "", expected_quality: str = "") -> int:
    """创建评测用例。"""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO eval_cases (name, description, analysis_type, input_params, expected_quality) VALUES (?, ?, ?, ?, ?)",
        (name, description, analysis_type, input_params, expected_quality)
    )
    case_id = cur.lastrowid
    conn.commit()
    conn.close()
    return case_id


def list_eval_cases(analysis_type: str = None, active_only: bool = True) -> list[dict]:
    """列出评测用例。"""
    conn = _get_conn()
    conditions = []
    params = []
    if active_only:
        conditions.append("is_active = 1")
    if analysis_type:
        conditions.append("analysis_type = ?")
        params.append(analysis_type)
    where = " AND ".join(conditions) if conditions else "1=1"
    rows = conn.execute(f"""
        SELECT ec.*, COUNT(er.id) as run_count,
               AVG(er.score) as avg_score
        FROM eval_cases ec
        LEFT JOIN eval_runs er ON ec.id = er.case_id
        WHERE {where}
        GROUP BY ec.id
        ORDER BY ec.id DESC
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_eval_case(case_id: int) -> dict | None:
    """获取单个评测用例。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM eval_cases WHERE id = ?", (case_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_eval_case(case_id: int, **fields) -> bool:
    """更新评测用例。"""
    if not fields:
        return False
    fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [case_id]
    conn = _get_conn()
    conn.execute(f"UPDATE eval_cases SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return True


def delete_eval_case(case_id: int) -> bool:
    """删除评测用例（同时删除关联的运行记录）。"""
    conn = _get_conn()
    conn.execute("DELETE FROM eval_runs WHERE case_id = ?", (case_id,))
    cur = conn.execute("DELETE FROM eval_cases WHERE id = ?", (case_id,))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def create_eval_run(case_id: int, analysis_type: str, result_summary: str,
                    result_data: str = "", score: float = None,
                    duration_ms: int = 0, token_usage: int = 0,
                    error_msg: str = "") -> int:
    """创建评测运行记录。"""
    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO eval_runs (case_id, analysis_type, result_summary, result_data,
                               score, duration_ms, token_usage, error_msg)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (case_id, analysis_type, result_summary, result_data,
          score, duration_ms, token_usage, error_msg))
    run_id = cur.lastrowid
    conn.commit()
    conn.close()
    return run_id


def list_eval_runs(case_id: int = None, limit: int = 50) -> list[dict]:
    """列出评测运行记录。"""
    conn = _get_conn()
    if case_id:
        rows = conn.execute("""
            SELECT er.*, ec.name as case_name, ec.analysis_type
            FROM eval_runs er
            LEFT JOIN eval_cases ec ON er.case_id = ec.id
            WHERE er.case_id = ?
            ORDER BY er.id DESC LIMIT ?
        """, (case_id, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT er.*, ec.name as case_name, ec.analysis_type
            FROM eval_runs er
            LEFT JOIN eval_cases ec ON er.case_id = ec.id
            ORDER BY er.id DESC LIMIT ?
        """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_eval_stats() -> dict:
    """获取评测统计概览。"""
    conn = _get_conn()
    stats = conn.execute("""
        SELECT
            COUNT(*) as total_cases,
            COALESCE(SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END), 0) as active_cases,
            (SELECT COUNT(*) FROM eval_runs) as total_runs,
            (SELECT AVG(score) FROM eval_runs WHERE score IS NOT NULL) as avg_score
        FROM eval_cases
    """).fetchone()
    conn.close()
    return dict(stats)


def get_eval_run_detail(run_id: int) -> dict | None:
    """获取单条运行记录详情。"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT er.*, ec.name as case_name, ec.description, ec.input_params, ec.expected_quality
        FROM eval_runs er
        LEFT JOIN eval_cases ec ON er.case_id = ec.id
        WHERE er.id = ?
    """, (run_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_eval_run(run_id: int, **fields) -> bool:
    """更新评测运行记录（用于写入评分结果）。"""
    if not fields:
        return False
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [run_id]
    conn = _get_conn()
    conn.execute(f"UPDATE eval_runs SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return True


def list_eval_cases_by_agent(agent_id: int, agent_type: str = "") -> list[dict]:
    """列出关联到指定 Agent 的评测用例（用于回归测试）。"""
    conn = _get_conn()
    if agent_type:
        rows = conn.execute("""
            SELECT ec.*, COUNT(er.id) as run_count,
                   AVG(er.score) as avg_score
            FROM eval_cases ec
            LEFT JOIN eval_runs er ON ec.id = er.case_id
            WHERE ec.agent_id = ? AND ec.agent_type = ? AND ec.is_active = 1
            GROUP BY ec.id
            ORDER BY ec.id DESC
        """, (agent_id, agent_type)).fetchall()
    else:
        rows = conn.execute("""
            SELECT ec.*, COUNT(er.id) as run_count,
                   AVG(er.score) as avg_score
            FROM eval_cases ec
            LEFT JOIN eval_runs er ON ec.id = er.case_id
            WHERE ec.agent_id = ? AND ec.is_active = 1
            GROUP BY ec.id
            ORDER BY ec.id DESC
        """, (agent_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_eval_case_avg_score(case_id: int, exclude_run_id: int = 0) -> float | None:
    """获取评测用例的平均分（可排除指定 run）。"""
    conn = _get_conn()
    if exclude_run_id:
        row = conn.execute(
            "SELECT AVG(score) as avg FROM eval_runs WHERE case_id = ? AND score IS NOT NULL AND score > 0 AND id != ?",
            (case_id, exclude_run_id)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT AVG(score) as avg FROM eval_runs WHERE case_id = ? AND score IS NOT NULL AND score > 0",
            (case_id,)
        ).fetchone()
    conn.close()
    return row["avg"] if row and row["avg"] else None
