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
    # 统一评分尺度：4 维度评分（0-10）+ 归一化总分（0-100）
    _add_column_if_not_exists(conn, "eval_runs", "score_data_accuracy", "REAL")
    _add_column_if_not_exists(conn, "eval_runs", "score_logic", "REAL")
    _add_column_if_not_exists(conn, "eval_runs", "score_actionability", "REAL")
    _add_column_if_not_exists(conn, "eval_runs", "score_risk_awareness", "REAL")
    _add_column_if_not_exists(conn, "eval_runs", "score_normalized", "REAL")
    # 一次性回填旧数据：原 score 为 0-24 分制，乘 4.17 归一化到 0-100
    conn.execute(
        "UPDATE eval_runs SET score_normalized = score * 4.17 "
        "WHERE score_normalized IS NULL AND score IS NOT NULL"
    )

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
    # P0-A 决策闭环：用户采纳标记（与 status 验证状态分离）
    # adopted: 0=未标记, 1=已采纳, -1=未采纳
    _add_column_if_not_exists(conn, "recommendations", "adopted", "INTEGER DEFAULT 0")
    _add_column_if_not_exists(conn, "recommendations", "adopted_at", "TEXT")
    # Phase D: 归因字段 — 记录建议是否引用了书籍（JSON 数组，如 ["聪明的投资者","周期"]）
    _add_column_if_not_exists(conn, "recommendations", "referenced_books", "TEXT")
    # P2 执行落地：建议关联基金代码 + 建议金额（用于"去执行"跳转）
    _add_column_if_not_exists(conn, "recommendations", "target_fund_code", "TEXT")
    _add_column_if_not_exists(conn, "recommendations", "target_fund_name", "TEXT")
    _add_column_if_not_exists(conn, "recommendations", "suggested_amount", "REAL")
    # 2026-07-13 决策模型升级：区分 value_dip（低估机会）/ momentum_breakout（趋势机会）
    _add_column_if_not_exists(conn, "recommendations", "signal_type", "TEXT DEFAULT 'value_dip'")

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
    # Bad Case 根因分析
    _add_column_if_not_exists(conn, "llm_feedback", "root_cause", "TEXT DEFAULT ''")
    _add_column_if_not_exists(conn, "llm_feedback", "root_cause_detail", "TEXT DEFAULT ''")

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

    # ── KYC 理财画像维度（风险偏好/投资期限/资金体量/投资经验/亏损承受度/关注品种）──
    _add_column_if_not_exists(conn, "user_profiles", "risk_tolerance", "TEXT DEFAULT ''")        # 保守|稳健|平衡|进取|激进
    _add_column_if_not_exists(conn, "user_profiles", "investment_horizon", "TEXT DEFAULT ''")    # short(<1y)|medium(1-3y)|long(>3y)
    _add_column_if_not_exists(conn, "user_profiles", "capital_scale", "TEXT DEFAULT ''")         # small(<10w)|medium(10-100w)|large(>100w)
    _add_column_if_not_exists(conn, "user_profiles", "investment_experience", "TEXT DEFAULT ''") # novice|intermediate|advanced|professional
    _add_column_if_not_exists(conn, "user_profiles", "loss_tolerance", "TEXT DEFAULT ''")        # low(<5%)|medium(5-15%)|high(>15%)
    _add_column_if_not_exists(conn, "user_profiles", "focus_assets", "TEXT DEFAULT '[]'")        # JSON: ["index","fund","bond","stock","gold"]
    _add_column_if_not_exists(conn, "user_profiles", "kyc_completed", "INTEGER DEFAULT 0")
    _add_column_if_not_exists(conn, "user_profiles", "kyc_completed_at", "TEXT DEFAULT ''")
    _add_column_if_not_exists(conn, "user_profiles", "kyc_version", "INTEGER DEFAULT 0")
    _add_column_if_not_exists(conn, "user_profiles", "kyc_source", "TEXT DEFAULT ''")            # questionnaire|conversation|feedback|eval_hint
    # ── 个人财务画像 2.0（目标、现金流、约束、行为偏差）──────────────────
    _add_column_if_not_exists(conn, "user_profiles", "monthly_income", "REAL")
    _add_column_if_not_exists(conn, "user_profiles", "monthly_expense", "REAL")
    _add_column_if_not_exists(conn, "user_profiles", "monthly_surplus", "REAL")
    _add_column_if_not_exists(conn, "user_profiles", "emergency_fund_months", "REAL")
    _add_column_if_not_exists(conn, "user_profiles", "target_equity_ratio", "REAL")
    _add_column_if_not_exists(conn, "user_profiles", "max_single_position_pct", "REAL")
    _add_column_if_not_exists(conn, "user_profiles", "primary_goal", "TEXT DEFAULT ''")
    _add_column_if_not_exists(conn, "user_profiles", "fund_usage", "TEXT DEFAULT ''")
    _add_column_if_not_exists(conn, "user_profiles", "liquidity_needs", "TEXT DEFAULT ''")
    _add_column_if_not_exists(conn, "user_profiles", "liabilities_summary", "TEXT DEFAULT ''")
    _add_column_if_not_exists(conn, "user_profiles", "behavior_biases", "TEXT DEFAULT '[]'")

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

    # ── P1 投资目标表 ──────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS investment_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            goal_type TEXT NOT NULL,
            target_amount REAL,
            target_date TEXT,
            monthly_contribution REAL,
            current_progress REAL DEFAULT 0,
            priority INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_investment_goals_uid ON investment_goals(user_id)")

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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rag_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            knowledge_id INTEGER,
            content_type TEXT DEFAULT '',
            query TEXT DEFAULT '',
            rating INTEGER NOT NULL,
            reasons TEXT DEFAULT '[]',
            user_id TEXT DEFAULT 'default',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_feedback_user ON rag_feedback(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_feedback_kid ON rag_feedback(knowledge_id)")

    # 新增表
    _init_prompt_versions_table(conn)
    _init_eval_daily_reports_table(conn)
    _init_eval_suites_tables(conn)
    _init_improvement_tasks_table(conn)
    _init_prompt_regression_results_table(conn)


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


def list_eval_cases(analysis_type: str = None, active_only: bool = True,
                    page: int = None, page_size: int = 20) -> list[dict] | dict:
    """列出评测用例。

    传入 page 时返回分页结构 {items, total, page, page_size}，否则返回列表（向后兼容）。
    """
    conn = _get_conn()
    conditions = []
    params = []
    if active_only:
        conditions.append("ec.is_active = 1")
    if analysis_type:
        conditions.append("ec.analysis_type = ?")
        params.append(analysis_type)
    where = " AND ".join(conditions) if conditions else "1=1"

    if page is not None:
        total_row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM eval_cases ec WHERE {where}", params
        ).fetchone()
        total = total_row["cnt"] if total_row else 0
        offset = max(0, (page - 1) * page_size)
        rows = conn.execute(f"""
            SELECT ec.*, COUNT(er.id) as run_count,
                   AVG(er.score) as avg_score
            FROM eval_cases ec
            LEFT JOIN eval_runs er ON ec.id = er.case_id
            WHERE {where}
            GROUP BY ec.id
            ORDER BY ec.id DESC
            LIMIT ? OFFSET ?
        """, params + [page_size, offset]).fetchall()
        conn.close()
        return {"items": [dict(r) for r in rows], "total": total,
                "page": page, "page_size": page_size}

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
                    error_msg: str = "",
                    score_data_accuracy: float = None,
                    score_logic: float = None,
                    score_actionability: float = None,
                    score_risk_awareness: float = None,
                    score_normalized: float = None) -> int:
    """创建评测运行记录。

    支持多维度评分（0-10）：data_accuracy / logic / actionability / risk_awareness，
    以及归一化总分 score_normalized（0-100）。
    若传入 4 维度但未传 score_normalized，则自动计算 (a+b+c+d)/4*10。
    """
    # 自动归一化：4 维度均值 * 10 → 0-100
    if (score_normalized is None and None not in (
            score_data_accuracy, score_logic, score_actionability, score_risk_awareness)):
        try:
            score_normalized = (
                float(score_data_accuracy) + float(score_logic)
                + float(score_actionability) + float(score_risk_awareness)
            ) / 4 * 10
        except (TypeError, ValueError):
            pass

    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO eval_runs (case_id, analysis_type, result_summary, result_data,
                               score, duration_ms, token_usage, error_msg,
                               score_data_accuracy, score_logic,
                               score_actionability, score_risk_awareness,
                               score_normalized)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (case_id, analysis_type, result_summary, result_data,
          score, duration_ms, token_usage, error_msg,
          score_data_accuracy, score_logic,
          score_actionability, score_risk_awareness,
          score_normalized))
    run_id = cur.lastrowid
    conn.commit()
    conn.close()
    return run_id


def list_eval_runs(case_id: int = None, limit: int = 50,
                   page: int = None, page_size: int = 20) -> list[dict] | dict:
    """列出评测运行记录。

    传入 page 时返回分页结构 {items, total, page, page_size}，否则返回列表（向后兼容）。
    """
    conn = _get_conn()
    if page is not None:
        if case_id:
            total_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM eval_runs WHERE case_id = ?", (case_id,)
            ).fetchone()
        else:
            total_row = conn.execute("SELECT COUNT(*) as cnt FROM eval_runs").fetchone()
        total = total_row["cnt"] if total_row else 0
        offset = max(0, (page - 1) * page_size)
        if case_id:
            rows = conn.execute("""
                SELECT er.*, ec.name as case_name, ec.analysis_type
                FROM eval_runs er
                LEFT JOIN eval_cases ec ON er.case_id = ec.id
                WHERE er.case_id = ?
                ORDER BY er.id DESC LIMIT ? OFFSET ?
            """, (case_id, page_size, offset)).fetchall()
        else:
            rows = conn.execute("""
                SELECT er.*, ec.name as case_name, ec.analysis_type
                FROM eval_runs er
                LEFT JOIN eval_cases ec ON er.case_id = ec.id
                ORDER BY er.id DESC LIMIT ? OFFSET ?
            """, (page_size, offset)).fetchall()
        conn.close()
        return {"items": [dict(r) for r in rows], "total": total,
                "page": page, "page_size": page_size}

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


def list_conversation_evaluations(limit: int = 50, min_score: float = None,
                                  page: int = None, page_size: int = 20) -> list[dict] | dict:
    """列出对话评估记录。

    传入 page 时返回分页结构 {items, total, page, page_size}，否则返回列表（向后兼容）。
    """
    conn = _get_conn()

    conditions = []
    params = []

    if min_score is not None:
        conditions.append("auto_score >= ?")
        params.append(min_score)

    where = " AND ".join(conditions) if conditions else "1=1"

    if page is not None:
        total_row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM conversation_evaluations WHERE {where}", params
        ).fetchone()
        total = total_row["cnt"] if total_row else 0
        offset = max(0, (page - 1) * page_size)
        rows = conn.execute(f"""
            SELECT ce.*, c.title as conversation_title
            FROM conversation_evaluations ce
            LEFT JOIN conversations c ON ce.conversation_id = c.id
            WHERE {where}
            ORDER BY ce.id DESC
            LIMIT ? OFFSET ?
        """, params + [page_size, offset]).fetchall()
        conn.close()
        return {"items": [dict(r) for r in rows], "total": total,
                "page": page, "page_size": page_size}

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


def get_agent_eval_scores(days: int = 7) -> dict:
    """获取各专家最近 N 天的评估平均分（供路由降权使用）。

    通过 agent_runs JOIN conversation_evaluations 按 message_id 关联。
    仅返回 eval_count >= 3 的专家（样本不足不参与降权）。
    返回: {agent_key: {"avg_score": float, "eval_count": int}}
    """
    conn = _get_conn()
    try:
        rows = conn.execute(f"""
            SELECT ar.agent_key,
                   AVG(ce.auto_score) as avg_score,
                   COUNT(ce.auto_score) as eval_count
            FROM agent_runs ar
            JOIN conversation_evaluations ce ON ar.message_id = ce.message_id
            WHERE ar.status = 'completed'
              AND ce.auto_score IS NOT NULL
              AND ce.created_at >= datetime('now', '-{days} days')
            GROUP BY ar.agent_key
            HAVING eval_count >= 3
        """).fetchall()
        return {r["agent_key"]: {"avg_score": r["avg_score"], "eval_count": r["eval_count"]}
                for r in rows}
    except Exception:
        return {}
    finally:
        conn.close()


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


# ── RAG 反馈 CRUD ──

def save_rag_feedback(
    knowledge_id: int = None,
    content_type: str = "",
    query: str = "",
    rating: int = 1,
    reasons: list = None,
    user_id: str = "default",
) -> int:
    """保存 RAG 检索结果反馈。rating: 1=赞, -1=踩。"""
    import json
    conn = _get_conn()
    cur = conn.execute(
        """
        INSERT INTO rag_feedback (knowledge_id, content_type, query, rating, reasons, user_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (knowledge_id, content_type, query, rating, json.dumps(reasons or [], ensure_ascii=False), user_id),
    )
    conn.commit()
    feedback_id = cur.lastrowid
    conn.close()
    return feedback_id


def get_rag_feedback_stats(user_id: str = "default", days: int = 30) -> dict:
    """获取 RAG 反馈统计，用于个性化排序权重回流。

    返回：
        {
            "positive_types": {"knowledge": 5, "book": 3, ...},  -- 赞过的内容类型计数
            "negative_types": {"author_article": 2, ...},        -- 踩过的内容类型计数
            "positive_ids": [101, 205, ...],                     -- 赞过的知识 ID
            "negative_ids": [302, ...],                          -- 踩过的知识 ID
            "total_feedback": 20,
        }
    """
    from datetime import datetime, timedelta
    conn = _get_conn()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        """
        SELECT knowledge_id, content_type, rating
        FROM rag_feedback
        WHERE user_id = ? AND created_at >= ?
        """,
        (user_id, cutoff),
    ).fetchall()
    conn.close()

    positive_types: dict[str, int] = {}
    negative_types: dict[str, int] = {}
    positive_ids = []
    negative_ids = []

    for row in rows:
        ct = row["content_type"] or ""
        kid = row["knowledge_id"]
        if row["rating"] > 0:
            if ct:
                positive_types[ct] = positive_types.get(ct, 0) + 1
            if kid:
                positive_ids.append(kid)
        elif row["rating"] < 0:
            if ct:
                negative_types[ct] = negative_types.get(ct, 0) + 1
            if kid:
                negative_ids.append(kid)

    return {
        "positive_types": positive_types,
        "negative_types": negative_types,
        "positive_ids": positive_ids,
        "negative_ids": negative_ids,
        "total_feedback": len(rows),
    }


# ── 提示词版本管理 ──────────────────────────────

def _init_prompt_versions_table(conn):
    """提示词版本表。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prompt_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_type TEXT NOT NULL,
            version TEXT NOT NULL,
            prompt_content TEXT NOT NULL,
            changelog TEXT DEFAULT '',
            is_active INTEGER DEFAULT 0,
            avg_score REAL DEFAULT 0,
            eval_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(agent_type, version)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pv_type ON prompt_versions(agent_type)")


def _init_eval_daily_reports_table(conn):
    """每日评测日报表。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS eval_daily_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT NOT NULL UNIQUE,
            total_cases INTEGER DEFAULT 0,
            avg_score REAL DEFAULT 0,
            scores_by_type TEXT DEFAULT '{}',
            score_trend TEXT DEFAULT 'stable',
            alerts TEXT DEFAULT '[]',
            recommendations TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_daily_date ON eval_daily_reports(report_date)")


def _init_eval_suites_tables(conn):
    """测试套件表：eval_suites + eval_suite_cases。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS eval_suites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            suite_type TEXT DEFAULT 'regression',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS eval_suite_cases (
            suite_id INTEGER REFERENCES eval_suites(id) ON DELETE CASCADE,
            case_id INTEGER REFERENCES eval_cases(id) ON DELETE CASCADE,
            sort_order INTEGER DEFAULT 0,
            PRIMARY KEY (suite_id, case_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_suite_cases_suite ON eval_suite_cases(suite_id)")


def _init_improvement_tasks_table(conn):
    """改进任务表：根因分析结果 → 可应用的改进项。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS improvement_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_id INTEGER,
            agent_type TEXT,
            root_cause TEXT,
            suggestion TEXT,
            prompt_diff TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            applied_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_improvement_tasks_status ON improvement_tasks(status)")


def _init_prompt_regression_results_table(conn):
    """Prompt 变更回归测试结果持久化表。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prompt_regression_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            agent_type TEXT DEFAULT 'conversation',
            status TEXT NOT NULL,
            total_cases INTEGER DEFAULT 0,
            improved INTEGER DEFAULT 0,
            degraded INTEGER DEFAULT 0,
            unchanged INTEGER DEFAULT 0,
            cases_detail TEXT DEFAULT '[]',
            error TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_prr_agent ON prompt_regression_results(agent_id, agent_type)")


def save_regression_result(agent_id: int, agent_type: str, result: dict) -> int:
    """持久化回归测试结果，返回记录 id。"""
    conn = _get_conn()
    summary = result.get("summary", {})
    cur = conn.execute("""
        INSERT INTO prompt_regression_results
            (agent_id, agent_type, status, total_cases, improved, degraded, unchanged, cases_detail, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        agent_id, agent_type,
        result.get("status", "completed"),
        summary.get("total", 0),
        summary.get("improved", 0),
        summary.get("degraded", 0),
        summary.get("unchanged", 0),
        json.dumps(result.get("cases", []), ensure_ascii=False),
        result.get("error", ""),
    ))
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid


def get_latest_regression_result(agent_id: int, agent_type: str) -> dict | None:
    """从 DB 取最近一条回归测试结果。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM prompt_regression_results WHERE agent_id=? AND agent_type=? "
        "ORDER BY id DESC LIMIT 1",
        (agent_id, agent_type)
    ).fetchone()
    conn.close()
    if not row:
        return None
    r = dict(row)
    try:
        r["cases"] = json.loads(r.get("cases_detail", "[]"))
    except Exception:
        r["cases"] = []
    return r


def create_prompt_version(agent_type: str, version: str, prompt_content: str,
                          changelog: str = "") -> int:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO prompt_versions (agent_type, version, prompt_content, changelog) VALUES (?,?,?,?)",
        (agent_type, version, prompt_content, changelog)
    )
    vid = cur.lastrowid
    conn.commit()
    conn.close()
    return vid


def list_prompt_versions(agent_type: str = None) -> list[dict]:
    conn = _get_conn()
    if agent_type:
        rows = conn.execute(
            "SELECT * FROM prompt_versions WHERE agent_type = ? ORDER BY id DESC", (agent_type,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM prompt_versions ORDER BY agent_type, id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_active_prompt(agent_type: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM prompt_versions WHERE agent_type = ? AND is_active = 1", (agent_type,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def activate_prompt_version(version_id: int, agent_type: str) -> bool:
    conn = _get_conn()
    conn.execute("UPDATE prompt_versions SET is_active = 0 WHERE agent_type = ?", (agent_type,))
    conn.execute("UPDATE prompt_versions SET is_active = 1 WHERE id = ?", (version_id,))
    conn.commit()
    conn.close()
    return True


def update_prompt_scores(version_id: int, avg_score: float, eval_count: int) -> bool:
    conn = _get_conn()
    conn.execute(
        "UPDATE prompt_versions SET avg_score = ?, eval_count = ? WHERE id = ?",
        (avg_score, eval_count, version_id)
    )
    conn.commit()
    conn.close()
    return True


def save_eval_daily_report(report_date: str, total_cases: int, avg_score: float,
                           scores_by_type: dict, score_trend: str = "stable",
                           alerts: list = None, recommendations: list = None) -> int:
    import json
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO eval_daily_reports (report_date, total_cases, avg_score, scores_by_type, score_trend, alerts, recommendations) VALUES (?,?,?,?,?,?,?)",
        (report_date, total_cases, avg_score,
         json.dumps(scores_by_type, ensure_ascii=False),
         score_trend,
         json.dumps(alerts or [], ensure_ascii=False),
         json.dumps(recommendations or [], ensure_ascii=False))
    )
    conn.commit()
    conn.close()
    return 0


def get_eval_daily_report(report_date: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM eval_daily_reports WHERE report_date = ?", (report_date,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_eval_daily_reports(limit: int = 30) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM eval_daily_reports ORDER BY report_date DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_eval_trends(days: int = 30) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM eval_daily_reports ORDER BY report_date DESC LIMIT ?", (days,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_random_active_cases(count: int = 5) -> list[dict]:
    """随机抽取活跃评测用例。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM eval_cases WHERE is_active = 1 ORDER BY RANDOM() LIMIT ?", (count,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 升级三：工具调用质量评估 ──────────────────────────────


def _ensure_tool_eval_tables():
    """建表（幂等）。"""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tool_eval_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT,
            total_calls INTEGER DEFAULT 0,
            unique_tools INTEGER DEFAULT 0,
            unique_agents INTEGER DEFAULT 0,
            redundant_calls INTEGER DEFAULT 0,
            empty_result_calls INTEGER DEFAULT 0,
            efficiency_score REAL DEFAULT 1.0,
            tool_distribution TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS tool_bad_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT,
            query TEXT,
            metrics TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
    """)
    conn.commit()
    conn.close()


def save_tool_eval_metrics(metrics: dict) -> int:
    """保存工具调用评估指标，返回 id。"""
    import json as _json
    _ensure_tool_eval_tables()
    conn = _get_conn()
    cur = conn.execute(
        """INSERT INTO tool_eval_metrics
           (query, total_calls, unique_tools, unique_agents, redundant_calls,
            empty_result_calls, efficiency_score, tool_distribution)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            (metrics.get("query") or "")[:200],
            metrics.get("total_calls", 0),
            metrics.get("unique_tools", 0),
            metrics.get("unique_agents", 0),
            metrics.get("redundant_calls", 0),
            metrics.get("empty_result_calls", 0),
            metrics.get("efficiency_score", 1.0),
            _json.dumps(metrics.get("tool_distribution", {}), ensure_ascii=False),
        ),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def add_bad_case(case: dict) -> int:
    """记录 bad case（工具效率低/其他问题）。"""
    import json as _json
    _ensure_tool_eval_tables()
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO tool_bad_cases (type, query, metrics) VALUES (?, ?, ?)",
        (
            case.get("type", "unknown"),
            (case.get("query") or "")[:200],
            _json.dumps(case.get("metrics", {}), ensure_ascii=False),
        ),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def list_tool_eval_metrics(limit: int = 50) -> list[dict]:
    """查询最近的工具调用评估记录。"""
    _ensure_tool_eval_tables()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM tool_eval_metrics ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 测试套件 (Eval Suite) CRUD ──────────────────────────────


def create_eval_suite(name: str, description: str = "", suite_type: str = "regression",
                      is_active: bool = True) -> int:
    """创建测试套件。"""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO eval_suites (name, description, suite_type, is_active) VALUES (?, ?, ?, ?)",
        (name, description, suite_type, 1 if is_active else 0),
    )
    suite_id = cur.lastrowid
    conn.commit()
    conn.close()
    return suite_id


def list_eval_suites(suite_type: str = None, active_only: bool = False) -> list[dict]:
    """列出测试套件（含用例数）。"""
    conn = _get_conn()
    conditions = []
    params = []
    if active_only:
        conditions.append("s.is_active = 1")
    if suite_type:
        conditions.append("s.suite_type = ?")
        params.append(suite_type)
    where = " AND ".join(conditions) if conditions else "1=1"
    rows = conn.execute(f"""
        SELECT s.*, COUNT(sc.case_id) as case_count
        FROM eval_suites s
        LEFT JOIN eval_suite_cases sc ON s.id = sc.suite_id
        WHERE {where}
        GROUP BY s.id
        ORDER BY s.id DESC
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_eval_suite(suite_id: int) -> dict | None:
    """获取单个测试套件（含用例列表）。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM eval_suites WHERE id = ?", (suite_id,)
    ).fetchone()
    if not row:
        conn.close()
        return None
    suite = dict(row)
    cases = conn.execute("""
        SELECT sc.case_id, sc.sort_order, ec.name, ec.analysis_type, ec.is_active
        FROM eval_suite_cases sc
        JOIN eval_cases ec ON sc.case_id = ec.id
        WHERE sc.suite_id = ?
        ORDER BY sc.sort_order, sc.case_id
    """, (suite_id,)).fetchall()
    suite["cases"] = [dict(c) for c in cases]
    conn.close()
    return suite


def update_eval_suite(suite_id: int, **fields) -> bool:
    """更新测试套件。"""
    if not fields:
        return False
    fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [suite_id]
    conn = _get_conn()
    conn.execute(f"UPDATE eval_suites SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return True


def delete_eval_suite(suite_id: int) -> bool:
    """删除测试套件（关联表通过 ON DELETE CASCADE 自动清理）。"""
    conn = _get_conn()
    # SQLite 默认未开启外键级联，显式清理关联表
    conn.execute("DELETE FROM eval_suite_cases WHERE suite_id = ?", (suite_id,))
    cur = conn.execute("DELETE FROM eval_suites WHERE id = ?", (suite_id,))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def add_case_to_suite(suite_id: int, case_id: int, sort_order: int = 0) -> bool:
    """添加用例到套件（已存在则更新 sort_order）。"""
    conn = _get_conn()
    conn.execute("""
        INSERT INTO eval_suite_cases (suite_id, case_id, sort_order)
        VALUES (?, ?, ?)
        ON CONFLICT(suite_id, case_id) DO UPDATE SET sort_order = excluded.sort_order
    """, (suite_id, case_id, sort_order))
    conn.commit()
    conn.close()
    return True


def remove_case_from_suite(suite_id: int, case_id: int) -> bool:
    """从套件移除用例。"""
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM eval_suite_cases WHERE suite_id = ? AND case_id = ?",
        (suite_id, case_id),
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def list_suite_cases(suite_id: int) -> list[dict]:
    """列出套件内的用例（含完整用例信息）。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT sc.sort_order, ec.*
        FROM eval_suite_cases sc
        JOIN eval_cases ec ON sc.case_id = ec.id
        WHERE sc.suite_id = ?
        ORDER BY sc.sort_order, sc.case_id
    """, (suite_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 改进任务 (Improvement Task) CRUD ──────────────────────────────


def create_improvement_task(source_type: str, source_id: int = None,
                            agent_type: str = "", root_cause: str = "",
                            suggestion: str = "", prompt_diff: str = "",
                            status: str = "pending") -> int:
    """创建改进任务（根因分析结果 → 可应用的改进项）。"""
    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO improvement_tasks
            (source_type, source_id, agent_type, root_cause, suggestion, prompt_diff, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (source_type, source_id, agent_type, root_cause, suggestion, prompt_diff, status))
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return task_id


def list_improvement_tasks(status: str = None, limit: int = 100) -> list[dict]:
    """列出改进任务（支持 status 过滤）。"""
    conn = _get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM improvement_tasks WHERE status = ? ORDER BY id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM improvement_tasks ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_improvement_task_status(task_id: int, status: str,
                                   prompt_diff: str = None) -> bool:
    """更新改进任务状态（applied / rejected）。

    status=applied 时写入 applied_at 时间戳。
    """
    conn = _get_conn()
    if status == "applied":
        if prompt_diff is not None:
            conn.execute(
                "UPDATE improvement_tasks SET status = ?, prompt_diff = ?, "
                "applied_at = datetime('now','localtime') WHERE id = ?",
                (status, prompt_diff, task_id),
            )
        else:
            conn.execute(
                "UPDATE improvement_tasks SET status = ?, "
                "applied_at = datetime('now','localtime') WHERE id = ?",
                (status, task_id),
            )
    else:
        conn.execute(
            "UPDATE improvement_tasks SET status = ? WHERE id = ?",
            (status, task_id),
        )
    conn.commit()
    conn.close()
    return True
