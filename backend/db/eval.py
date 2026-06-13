"""评测集 (Eval Suite) CRUD。"""

import json
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

    # ── 对话质量评估表 ──────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversation_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            message_id INTEGER,
            auto_score REAL,
            auto_score_breakdown TEXT DEFAULT '{}',
            user_score REAL,
            user_score_breakdown TEXT DEFAULT '{}',
            user_comment TEXT DEFAULT '',
            complexity TEXT DEFAULT 'medium',
            specialist_count INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            has_cross_review INTEGER DEFAULT 0,
            has_arbitration INTEGER DEFAULT 0,
            duplicate_calls INTEGER DEFAULT 0,
            suggestions TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_eval_cid ON conversation_evaluations(conversation_id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversation_eval_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            eval_id INTEGER REFERENCES conversation_evaluations(id),
            dimension TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL,
            detail TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_eval_detail_eval ON conversation_eval_details(eval_id)")

    # ── 评估建议表（高分对话转化为 Eval 用例的建议）──────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS eval_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            analysis_type TEXT NOT NULL,
            input_params TEXT NOT NULL DEFAULT '{}',
            expected_quality TEXT,
            auto_score REAL,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_suggestions_status ON eval_suggestions(status)")

    # ── 专家表现告警表 ──────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS expert_performance_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            agent_key TEXT NOT NULL,
            agent_name TEXT,
            success_rate REAL,
            avg_duration_ms INTEGER,
            alert_type TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_expert_alerts_agent ON expert_performance_alerts(agent_key)")

    # ── LLM 评估记录表 ──────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_type TEXT NOT NULL,
            target_id INTEGER,
            message_id INTEGER,
            total_score REAL,
            dimensions_json TEXT DEFAULT '{}',
            strengths_json TEXT DEFAULT '[]',
            weaknesses_json TEXT DEFAULT '[]',
            suggestions_json TEXT DEFAULT '[]',
            user_preference_hints TEXT DEFAULT '[]',
            evaluator_version TEXT DEFAULT 'v1',
            duration_ms INTEGER,
            token_usage INTEGER,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_eval_target ON llm_evaluations(target_type, target_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_eval_score ON llm_evaluations(total_score)")

    # ── 用户偏好学习表 ──────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_preference_learnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            preference_key TEXT NOT NULL,
            preference_value TEXT,
            source TEXT,
            confidence REAL DEFAULT 0.5,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_pref_uid ON user_preference_learnings(user_id)")

    # ── 失败模式表 ──────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS failure_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern TEXT NOT NULL,
            frequency INTEGER DEFAULT 1,
            last_seen TEXT DEFAULT (datetime('now','localtime')),
            suggestion TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

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


# ── 对话质量评估 CRUD ──────────────────────────────

def create_conversation_evaluation(
    conversation_id: int,
    message_id: int = None,
    auto_score: float = None,
    auto_score_breakdown: str = "{}",
    complexity: str = "medium",
    specialist_count: int = 0,
    duration_ms: int = 0,
    has_cross_review: bool = False,
    has_arbitration: bool = False,
    duplicate_calls: int = 0,
    suggestions: str = "[]",
) -> int:
    """创建对话评估记录"""
    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO conversation_evaluations (
            conversation_id, message_id, auto_score, auto_score_breakdown,
            complexity, specialist_count, duration_ms,
            has_cross_review, has_arbitration, duplicate_calls, suggestions
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        conversation_id, message_id, auto_score, auto_score_breakdown,
        complexity, specialist_count, duration_ms,
        1 if has_cross_review else 0, 1 if has_arbitration else 0,
        duplicate_calls, suggestions,
    ))
    eval_id = cur.lastrowid
    conn.commit()
    conn.close()
    return eval_id


def update_conversation_evaluation_user_score(
    eval_id: int,
    user_score: float,
    user_score_breakdown: str = "{}",
    user_comment: str = "",
) -> bool:
    """更新对话评估的用户评分"""
    conn = _get_conn()
    conn.execute("""
        UPDATE conversation_evaluations
        SET user_score = ?, user_score_breakdown = ?, user_comment = ?,
            updated_at = datetime('now','localtime')
        WHERE id = ?
    """, (user_score, user_score_breakdown, user_comment, eval_id))
    conn.commit()
    conn.close()
    return True


def get_conversation_evaluation(conversation_id: int, message_id: int = None) -> dict | None:
    """获取对话的评估结果

    参数:
        conversation_id: 对话 ID
        message_id: 消息 ID（可选，如果指定则返回该消息的评估）
    """
    conn = _get_conn()

    if message_id:
        # 按 message_id 查询
        row = conn.execute("""
            SELECT * FROM conversation_evaluations
            WHERE conversation_id = ? AND message_id = ?
            ORDER BY id DESC LIMIT 1
        """, (conversation_id, message_id)).fetchone()
    else:
        # 查询最新的评估
        row = conn.execute("""
            SELECT * FROM conversation_evaluations
            WHERE conversation_id = ?
            ORDER BY id DESC LIMIT 1
        """, (conversation_id,)).fetchone()

    if not row:
        conn.close()
        return None

    result = dict(row)

    # 获取评估详情
    details = conn.execute("""
        SELECT * FROM conversation_eval_details
        WHERE eval_id = ?
        ORDER BY dimension, metric
    """, (result["id"],)).fetchall()
    result["details"] = [dict(d) for d in details]

    conn.close()
    return result


def list_conversation_evaluations(limit: int = 50, min_score: float = None) -> list[dict]:
    """列出对话评估记录"""
    conn = _get_conn()

    conditions = []
    params = []

    if min_score is not None:
        conditions.append("auto_score >= ?")
        params.append(min_score)

    where = " AND ".join(conditions) if conditions else "1=1"

    rows = conn.execute(f"""
        SELECT ce.*, c.title as conversation_title
        FROM conversation_evaluations ce
        LEFT JOIN conversations c ON ce.conversation_id = c.id
        WHERE {where}
        ORDER BY ce.id DESC
        LIMIT ?
    """, params + [limit]).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def get_conversation_eval_stats() -> dict:
    """获取对话评估统计"""
    conn = _get_conn()

    stats = conn.execute("""
        SELECT
            COUNT(*) as total_evals,
            AVG(auto_score) as avg_auto_score,
            AVG(user_score) as avg_user_score,
            SUM(CASE WHEN auto_score >= 80 THEN 1 ELSE 0 END) as high_score_count,
            SUM(CASE WHEN auto_score < 60 THEN 1 ELSE 0 END) as low_score_count,
            SUM(CASE WHEN has_cross_review = 1 THEN 1 ELSE 0 END) as cross_review_count,
            SUM(CASE WHEN has_arbitration = 1 THEN 1 ELSE 0 END) as arbitration_count
        FROM conversation_evaluations
    """).fetchone()

    result = dict(stats) if stats else {}

    # 按复杂度统计
    complexity_stats = conn.execute("""
        SELECT
            complexity,
            COUNT(*) as count,
            AVG(auto_score) as avg_score
        FROM conversation_evaluations
        GROUP BY complexity
    """).fetchall()

    result["by_complexity"] = [dict(r) for r in complexity_stats]

    # 最近7天的趋势
    trend = conn.execute("""
        SELECT
            DATE(created_at) as date,
            COUNT(*) as count,
            AVG(auto_score) as avg_score
        FROM conversation_evaluations
        WHERE created_at >= datetime('now', '-7 days')
        GROUP BY DATE(created_at)
        ORDER BY date
    """).fetchall()

    result["trend"] = [dict(r) for r in trend]

    conn.close()
    return result


def save_eval_details(eval_id: int, details: list[dict]):
    """保存评估详情"""
    conn = _get_conn()
    for detail in details:
        conn.execute("""
            INSERT INTO conversation_eval_details (eval_id, dimension, metric, value, detail)
            VALUES (?, ?, ?, ?, ?)
        """, (
            eval_id,
            detail.get("dimension", ""),
            detail.get("metric", ""),
            detail.get("value", 0),
            detail.get("detail", ""),
        ))
    conn.commit()
    conn.close()


# ── LLM 评估 CRUD ──────────────────────────────

def save_llm_evaluation(
    target_type: str,
    target_id: int = None,
    message_id: int = None,
    total_score: float = 0,
    dimensions: dict = None,
    strengths: list = None,
    weaknesses: list = None,
    suggestions: list = None,
    user_preference_hints: list = None,
    duration_ms: int = 0,
    token_usage: int = 0,
) -> int:
    """保存 LLM 评估结果"""
    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO llm_evaluations (
            target_type, target_id, message_id, total_score,
            dimensions_json, strengths_json, weaknesses_json,
            suggestions_json, user_preference_hints,
            duration_ms, token_usage
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        target_type, target_id, message_id, total_score,
        json.dumps(dimensions or {}, ensure_ascii=False),
        json.dumps(strengths or [], ensure_ascii=False),
        json.dumps(weaknesses or [], ensure_ascii=False),
        json.dumps(suggestions or [], ensure_ascii=False),
        json.dumps(user_preference_hints or [], ensure_ascii=False),
        duration_ms, token_usage,
    ))
    eval_id = cur.lastrowid
    conn.commit()
    conn.close()
    return eval_id


def get_llm_evaluation(target_type: str, target_id: int, message_id: int = None) -> dict | None:
    """获取 LLM 评估结果"""
    conn = _get_conn()

    if message_id:
        row = conn.execute("""
            SELECT * FROM llm_evaluations
            WHERE target_type = ? AND target_id = ? AND message_id = ?
            ORDER BY id DESC LIMIT 1
        """, (target_type, target_id, message_id)).fetchone()
    else:
        row = conn.execute("""
            SELECT * FROM llm_evaluations
            WHERE target_type = ? AND target_id = ?
            ORDER BY id DESC LIMIT 1
        """, (target_type, target_id)).fetchone()

    conn.close()
    return dict(row) if row else None


def list_llm_evaluations(target_type: str = None, limit: int = 50, min_score: float = None) -> list:
    """列出 LLM 评估记录"""
    conn = _get_conn()

    conditions = []
    params = []

    if target_type:
        conditions.append("target_type = ?")
        params.append(target_type)

    if min_score is not None:
        conditions.append("total_score >= ?")
        params.append(min_score)

    where = " AND ".join(conditions) if conditions else "1=1"

    rows = conn.execute(f"""
        SELECT * FROM llm_evaluations
        WHERE {where}
        ORDER BY id DESC
        LIMIT ?
    """, params + [limit]).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def get_llm_eval_stats(days: int = 30) -> dict:
    """获取 LLM 评估统计"""
    conn = _get_conn()

    stats = conn.execute("""
        SELECT
            COUNT(*) as total_evals,
            AVG(total_score) as avg_score,
            SUM(CASE WHEN total_score >= 80 THEN 1 ELSE 0 END) as high_score_count,
            SUM(CASE WHEN total_score < 60 THEN 1 ELSE 0 END) as low_score_count
        FROM llm_evaluations
        WHERE created_at >= datetime('now', ?)
    """, (f"-{days} days",)).fetchone()

    result = dict(stats) if stats else {}

    # 按类型统计
    by_type = conn.execute("""
        SELECT
            target_type,
            COUNT(*) as count,
            AVG(total_score) as avg_score
        FROM llm_evaluations
        WHERE created_at >= datetime('now', ?)
        GROUP BY target_type
    """, (f"-{days} days",)).fetchall()

    result["by_type"] = [dict(r) for r in by_type]

    # 趋势
    trend = conn.execute("""
        SELECT
            DATE(created_at) as date,
            AVG(total_score) as avg_score,
            COUNT(*) as count
        FROM llm_evaluations
        WHERE created_at >= datetime('now', ?)
        GROUP BY DATE(created_at)
        ORDER BY date
    """, (f"-{days} days",)).fetchall()

    result["trend"] = [dict(r) for r in trend]

    conn.close()
    return result


def save_user_preference(user_id: str, key: str, value: str, source: str = "", confidence: float = 0.5):
    """保存用户偏好学习"""
    conn = _get_conn()

    # 检查是否已存在
    existing = conn.execute("""
        SELECT id FROM user_preference_learnings
        WHERE user_id = ? AND preference_key = ?
    """, (user_id, key)).fetchone()

    if existing:
        # 更新
        conn.execute("""
            UPDATE user_preference_learnings
            SET preference_value = ?, source = ?, confidence = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ?
        """, (value, source, confidence, existing["id"]))
    else:
        # 新增
        conn.execute("""
            INSERT INTO user_preference_learnings (user_id, preference_key, preference_value, source, confidence)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, key, value, source, confidence))

    conn.commit()
    conn.close()


def get_user_preferences(user_id: str = "default") -> list:
    """获取用户偏好"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT * FROM user_preference_learnings
        WHERE user_id = ?
        ORDER BY confidence DESC, updated_at DESC
    """, (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_failure_pattern(pattern: str, suggestion: str = ""):
    """保存失败模式"""
    conn = _get_conn()

    # 检查是否已存在
    existing = conn.execute("""
        SELECT id, frequency FROM failure_patterns WHERE pattern = ?
    """, (pattern,)).fetchone()

    if existing:
        # 更新频率
        conn.execute("""
            UPDATE failure_patterns
            SET frequency = ?, last_seen = datetime('now','localtime')
            WHERE id = ?
        """, (existing["frequency"] + 1, existing["id"]))
    else:
        # 新增
        conn.execute("""
            INSERT INTO failure_patterns (pattern, suggestion)
            VALUES (?, ?)
        """, (pattern, suggestion))

    conn.commit()
    conn.close()


def get_failure_patterns(limit: int = 10) -> list:
    """获取失败模式（按频率排序）"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT * FROM failure_patterns
        ORDER BY frequency DESC, last_seen DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
