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
    _add_column_if_not_exists(conn, 'conversation_summaries', 'cursor_kind', "TEXT DEFAULT 'legacy_count'")
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
    _init_governance_evaluation(conn)


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
                   AVG(CASE WHEN er.score_status='scored' THEN er.score END) as avg_score
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
               AVG(CASE WHEN er.score_status='scored' THEN er.score END) as avg_score
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
    conn.execute("DELETE FROM eval_case_sources WHERE case_id = ?", (case_id,))
    conn.execute("DELETE FROM eval_suite_cases WHERE case_id = ?", (case_id,))
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
                    score_normalized: float = None, score_max: float = 10,
                    rubric_version: str = "judge-10-v1", score_status: str = None,
                    case_snapshot: dict = None, prompt_version: str = "") -> int:
    """创建评测运行记录。

    支持多维度评分（0-10）：data_accuracy / logic / actionability / risk_awareness，
    以及归一化总分 score_normalized（0-100）。
    若传入 4 维度但未传 score_normalized，则自动计算 (a+b+c+d)/4*10。
    """
    raw_score = score
    if score_max <= 0:
        raise ValueError("评分满分必须大于0")
    if score is not None:
        if not 0 <= float(score) <= score_max:
            raise ValueError("评分超出量程")
        score = round(float(score) / score_max * 10, 4)
        score_normalized = score * 10
    score_status = score_status or ("error" if error_msg else "scored" if score and score > 0 else "skipped" if score == 0 else "pending")
    if score_status not in ('pending','scored','skipped','error'):
        raise ValueError('无效评分状态')
    if score_status == 'scored' and (score is None or score <= 0):
        raise ValueError('有效评分必须大于0')
    snapshot = case_snapshot or get_eval_case(case_id) or {}
    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO eval_runs (case_id, analysis_type, result_summary, result_data,
                               score, duration_ms, token_usage, error_msg,
                               score_data_accuracy, score_logic,
                               score_actionability, score_risk_awareness,
                               score_normalized, raw_score, score_max, rubric_version, score_status,
                               case_snapshot, prompt_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (case_id, analysis_type, result_summary, result_data,
          score, duration_ms, token_usage, error_msg,
          score_data_accuracy, score_logic,
          score_actionability, score_risk_awareness,
          score_normalized, raw_score, score_max, rubric_version, score_status,
          json.dumps(snapshot, ensure_ascii=False), prompt_version))
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
                SELECT er.*, COALESCE(json_extract(er.case_snapshot,'$.name'),ec.name) as case_name
                FROM eval_runs er
                LEFT JOIN eval_cases ec ON er.case_id = ec.id
                WHERE er.case_id = ?
                ORDER BY er.id DESC LIMIT ? OFFSET ?
            """, (case_id, page_size, offset)).fetchall()
        else:
            rows = conn.execute("""
                SELECT er.*, COALESCE(json_extract(er.case_snapshot,'$.name'),ec.name) as case_name
                FROM eval_runs er
                LEFT JOIN eval_cases ec ON er.case_id = ec.id
                ORDER BY er.id DESC LIMIT ? OFFSET ?
            """, (page_size, offset)).fetchall()
        conn.close()
        return {"items": [dict(r) for r in rows], "total": total,
                "page": page, "page_size": page_size}

    if case_id:
        rows = conn.execute("""
            SELECT er.*, COALESCE(json_extract(er.case_snapshot,'$.name'),ec.name) as case_name
            FROM eval_runs er
            LEFT JOIN eval_cases ec ON er.case_id = ec.id
            WHERE er.case_id = ?
            ORDER BY er.id DESC LIMIT ?
        """, (case_id, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT er.*, COALESCE(json_extract(er.case_snapshot,'$.name'),ec.name) as case_name
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
            (SELECT AVG(score) FROM eval_runs WHERE score_status='scored') as avg_score
        FROM eval_cases
    """).fetchone()
    conn.close()
    return dict(stats)


def get_eval_run_detail(run_id: int) -> dict | None:
    """历史详情使用运行时快照；旧记录明确标记为迁移快照。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    if not row:
        return None
    result = dict(row)
    snapshot = json.loads(result.get("case_snapshot") or "{}")
    result.update(case_name=snapshot.get("name", ""), description=snapshot.get("description", ""),
                  input_params=snapshot.get("input_params", "{}"), expected_quality=snapshot.get("expected_quality", ""))
    return result


def update_eval_run(run_id: int, **fields) -> bool:
    """更新评测运行记录（用于写入评分结果）。"""
    if not fields:
        return False
    if 'score_status' in fields and fields['score_status'] not in ('pending','scored','skipped','error'):
        raise ValueError('无效评分状态')
    if "score" in fields:
        score = fields["score"]
        if score is not None and not 0 <= float(score) <= 10:
            raise ValueError('更新评分必须为0-10')
        fields.setdefault("raw_score", score)
        fields.setdefault("score_max", 10)
        fields.setdefault("score_normalized", score * 10 if score is not None else None)
        fields.setdefault("score_status", "scored" if score and score > 0 else "skipped")
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
                   AVG(CASE WHEN er.score_status='scored' THEN er.score END) as avg_score
            FROM eval_cases ec
            LEFT JOIN eval_runs er ON ec.id = er.case_id
            WHERE ec.agent_id = ? AND ec.agent_type = ? AND ec.is_active = 1
            GROUP BY ec.id
            ORDER BY ec.id DESC
        """, (agent_id, agent_type)).fetchall()
    else:
        rows = conn.execute("""
            SELECT ec.*, COUNT(er.id) as run_count,
                   AVG(CASE WHEN er.score_status='scored' THEN er.score END) as avg_score
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


def save_conversation_evaluation(result) -> int:
    """持久化对话评估结果到 conversation_evaluations 表。

    参数:
        result: ConversationEvaluation dataclass（或含同名字段的 dict）
    返回:
        新建记录 id
    """
    # 兼容 dataclass 与 dict
    if isinstance(result, dict):
        _get = lambda f, d=None: result.get(f, d)
    else:
        _get = lambda f, d=None: getattr(result, f, d)

    metadata = _get("metadata", {}) or {}

    # duplicate_calls 不在 metadata 中，从 execution 维度的 metrics 提取
    duplicate_calls = 0
    for dim in (_get("dimensions", []) or []):
        dim_dict = dim if isinstance(dim, dict) else getattr(dim, "__dict__", {})
        if dim_dict.get("name") == "execution":
            duplicate_calls = (dim_dict.get("metrics", {}) or {}).get("duplicate_calls", 0)
            break

    breakdown = _get("auto_score_breakdown", {}) or {}
    suggestions = _get("suggestions", []) or []

    return create_conversation_evaluation(
        conversation_id=_get("conversation_id"),
        message_id=_get("message_id"),
        auto_score=_get("auto_score"),
        auto_score_breakdown=json.dumps(breakdown, ensure_ascii=False),
        complexity=metadata.get("complexity", "medium"),
        specialist_count=metadata.get("specialist_count", 0),
        duration_ms=metadata.get("duration_ms", 0),
        has_cross_review=metadata.get("has_cross_review", False),
        has_arbitration=metadata.get("has_arbitration", False),
        duplicate_calls=duplicate_calls,
        suggestions=json.dumps(suggestions, ensure_ascii=False),
    )


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


def get_conversation_eval_stats(days: int = 30) -> dict:
    """获取对话评估统计"""
    days = max(1, min(int(days), 3650))
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
        WHERE created_at >= datetime('now','localtime', ?)
    """, (f"-{days} days",)).fetchone()

    result = dict(stats) if stats else {}

    # 按复杂度统计
    complexity_stats = conn.execute("""
        SELECT
            complexity,
            COUNT(*) as count,
            AVG(auto_score) as avg_score
        FROM conversation_evaluations
        WHERE created_at >= datetime('now','localtime', ?)
        GROUP BY complexity
    """, (f"-{days} days",)).fetchall()

    result["by_complexity"] = [dict(r) for r in complexity_stats]

    # 最近7天的趋势
    trend = conn.execute("""
        SELECT
            DATE(created_at) as date,
            COUNT(*) as count,
            AVG(auto_score) as avg_score
        FROM conversation_evaluations
        WHERE created_at >= datetime('now','localtime', ?)
        GROUP BY DATE(created_at)
        ORDER BY date
    """, (f"-{days} days",)).fetchall()

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
    counts = {key:sum(case.get('status') == key for case in r['cases'])
              for key in ('improved','degraded','unchanged','new','skipped','error')}
    r['summary'] = {'total':len(r['cases']),'scored':sum(counts[k] for k in ('improved','degraded','unchanged','new')),**counts}
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
    """校验归属后原子发布到真实 Agent 配置；无效目标不改变任何状态。"""
    from services.quality.eval_execution import ANALYSIS_NAMES
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        version = conn.execute("SELECT * FROM prompt_versions WHERE id=? AND agent_type=?", (version_id, agent_type)).fetchone()
        if not version:
            return False
        content = version["prompt_content"]
        if agent_type in ANALYSIS_NAMES:
            changed = conn.execute("UPDATE analysis_agents SET system_prompt=?,updated_at=datetime('now','localtime') WHERE name=? AND is_active=1",
                                   (content, ANALYSIS_NAMES[agent_type])).rowcount
        elif agent_type in ("orchestrator", "ai"):
            key = "agent.orchestrator_prompt" if agent_type == "orchestrator" else "agent.portfolio_ai_prompt"
            conn.execute("INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, content))
            changed = 1
        else:
            changed = conn.execute("UPDATE agents SET system_prompt=? WHERE agent_key=? AND is_specialist=1", (content, agent_type)).rowcount
        if not changed:
            return False
        conn.execute("UPDATE prompt_versions SET is_active=0 WHERE agent_type=?", (agent_type,))
        conn.execute("UPDATE prompt_versions SET is_active=1 WHERE id=?", (version_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    from db.agents import clear_specialist_cache
    clear_specialist_cache()
    from db.config import _CONFIG_CACHE
    _CONFIG_CACHE.clear()
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


# ── 治理评测：量纲、不可变快照、来源幂等和原子任务提交 ──
def _init_governance_evaluation(conn):
    for name, definition in (
        ('raw_score', 'REAL'), ('score_max', 'REAL'), ('rubric_version', "TEXT DEFAULT ''"),
        ('score_status', "TEXT DEFAULT 'pending'"), ('case_snapshot', "TEXT DEFAULT '{}'"),
        ('prompt_version', "TEXT DEFAULT ''"), ('snapshot_origin', "TEXT DEFAULT 'runtime'"),
    ):
        _add_column_if_not_exists(conn, 'eval_runs', name, definition)
    conn.execute('''CREATE TABLE IF NOT EXISTS eval_case_sources (
        source_type TEXT NOT NULL, source_id INTEGER NOT NULL,
        case_id INTEGER NOT NULL REFERENCES eval_cases(id),
        PRIMARY KEY(source_type, source_id))''')
    # 只依据记录内的评分结构判断旧量纲，不再次缩放已经迁移的记录。
    rows = conn.execute("SELECT id,case_id,score,result_data,error_msg FROM eval_runs WHERE score_max IS NULL").fetchall()
    for row in rows:
        rid, cid, score, data, error = row
        try:
            payload = json.loads(data or '{}')
        except (ValueError, TypeError):
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        is_25 = isinstance(payload.get('scores'), dict) and 'score_total' in payload['scores']
        maximum = 25 if is_25 or (score is not None and score > 10) else 10
        normalized = round(score / maximum * 10, 4) if score is not None else None
        status = 'error' if error else 'scored' if score and score > 0 else 'skipped' if score == 0 else 'pending'
        case = conn.execute('SELECT * FROM eval_cases WHERE id=?', (cid,)).fetchone()
        snap = dict(case) if case else {}
        conn.execute('''UPDATE eval_runs SET raw_score=?,score=?,score_normalized=?,score_max=?,
            rubric_version=?,score_status=?,case_snapshot=?,snapshot_origin='legacy_current_case' WHERE id=?''',
            (score, normalized, normalized * 10 if normalized is not None else None, maximum,
             'legacy-25' if maximum == 25 else 'legacy-10', status, json.dumps(snap, ensure_ascii=False), rid))
    # 旧说明文本仅作一次来源回填，重复旧用例保留而不继续产生。
    import re
    for row in conn.execute('SELECT id,description FROM eval_cases ORDER BY id'):
        match = re.search(r'来源: (analysis|chat) \| 原始ID: (\d+)', row[1] or '')
        if match:
            conn.execute('INSERT OR IGNORE INTO eval_case_sources VALUES(?,?,?)',
                         ('bad_case:' + match[1], int(match[2]), row[0]))
    conn.execute('''UPDATE prompt_versions SET is_active=0 WHERE is_active=1 AND id NOT IN
        (SELECT MAX(id) FROM prompt_versions WHERE is_active=1 GROUP BY agent_type)''')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_one_active ON prompt_versions(agent_type) WHERE is_active=1')


def create_eval_case_from_source(source_type: str, source_id: int, **case) -> dict:
    """同一来源最多创建一个用例；并发提交使用同一事务排他检查。"""
    conn = _get_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT s.case_id,c.name FROM eval_case_sources s JOIN eval_cases c ON s.case_id=c.id WHERE source_type=? AND source_id=?', (source_type, source_id)).fetchone()
        if row:
            return {'id':row['case_id'], 'name':row['name'], 'created':False}
        cid = conn.execute('INSERT INTO eval_cases(name,analysis_type,input_params,description,expected_quality) VALUES(?,?,?,?,?)',
                           tuple(case.get(k, '' if k != 'input_params' else '{}') for k in ('name','analysis_type','input_params','description','expected_quality'))).lastrowid
        conn.execute('INSERT OR REPLACE INTO eval_case_sources VALUES(?,?,?)', (source_type,source_id,cid))
        conn.commit()
        return {'id':cid, 'name':case['name'], 'created':True}
    finally:
        conn.close()


def accept_governance_suggestion(suggestion_id: int) -> dict:
    """接受建议及创建用例在同一事务内，终态不可反向覆盖。"""
    conn = _get_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        item = conn.execute('SELECT * FROM eval_suggestions WHERE id=?', (suggestion_id,)).fetchone()
        if not item:
            raise LookupError('建议不存在')
        previous = conn.execute("SELECT case_id FROM eval_case_sources WHERE source_type='suggestion' AND source_id=?", (suggestion_id,)).fetchone()
        if item['status'] == 'accepted' and previous:
            return {'ok':True,'case_id':previous[0],'reused':True}
        if item['status'] != 'pending':
            raise ValueError('建议已处理')
        cid = conn.execute('INSERT INTO eval_cases(name,analysis_type,input_params,description,expected_quality) VALUES(?,?,?,?,?)',
                           (item['name'],item['analysis_type'],item['input_params'],f"从对话 {item['conversation_id']} 转化",item['expected_quality'] or '')).lastrowid
        conn.execute("INSERT INTO eval_case_sources VALUES('suggestion',?,?)", (suggestion_id,cid))
        conn.execute("UPDATE eval_suggestions SET status='accepted' WHERE id=? AND status='pending'", (suggestion_id,))
        conn.commit()
        return {'ok':True,'case_id':cid,'reused':False}
    finally:
        conn.close()


def reject_governance_suggestion(suggestion_id: int) -> bool:
    conn = _get_conn()
    try:
        changed = conn.execute("UPDATE eval_suggestions SET status='rejected' WHERE id=? AND status='pending'", (suggestion_id,)).rowcount
        conn.commit()
        return bool(changed)
    finally:
        conn.close()


def claim_governance_task(task_type: str, caller: str, request: dict) -> dict:
    """使用已存在的 request_key 唯一索引，原子去重同目标在途任务。"""
    import hashlib
    key = hashlib.sha256(json.dumps(request,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    conn = _get_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute("SELECT id FROM async_tasks WHERE task_type=? AND request_key=? AND status='running'", (task_type,key)).fetchone()
        if row:
            return {'task_id':row[0],'status':'running','reused':True}
        tid = conn.execute("INSERT INTO async_tasks(task_type,caller,request_key,status) VALUES(?,?,?,'running')", (task_type,caller,key)).lastrowid
        conn.commit()
        return {'task_id':tid,'status':'running','reused':False}
    finally:
        conn.close()


def get_governance_quality(days: int = 30, limit: int = 20) -> dict:
    """概览、趋势、Agent和低分使用相同量纲、窗口及有效样本。"""
    days = max(1,min(int(days),3650))
    conn = _get_conn()
    try:
        records = [dict(r) for r in conn.execute("""SELECT id,'eval_run' AS source,analysis_type AS caller,
            score AS overall_score,score_data_accuracy,score_logic,score_actionability,
            result_summary AS comment,result_summary AS input_summary,created_at
            FROM eval_runs WHERE score_status='scored' AND created_at>=datetime('now','localtime',?)
            UNION ALL SELECT id,'feedback',caller,overall_score,score_data_accuracy,score_logic,
            score_actionability,comment,input_summary,created_at FROM llm_feedback
            WHERE overall_score IS NOT NULL AND overall_score>0 AND overall_score<=10
            AND created_at>=datetime('now','localtime',?)""", (f'-{days} days',f'-{days} days'))]
        total = conn.execute("SELECT (SELECT COUNT(*) FROM eval_runs WHERE created_at>=datetime('now','localtime',?)) + (SELECT COUNT(*) FROM llm_feedback WHERE created_at>=datetime('now','localtime',?))", (f'-{days} days',f'-{days} days')).fetchone()[0]
    finally:
        conn.close()
    def avg(rows,key):
        values = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(values)/len(values),3) if values else 0
    low = sorted((r for r in records if r['overall_score']<5),key=lambda r:r['created_at'],reverse=True)
    mean = avg(records,'overall_score')
    summary = dict(total_feedback=total,scored_count=len(records),avg_overall=mean,eval_avg_score=mean,
                   eval_good_count=sum(r['overall_score']>=7 for r in records),eval_bad_count=len(low),low_quality_count=len(low))
    groups, agents = {},{}
    for row in records:
        groups.setdefault(row['created_at'][:10],[]).append(row)
        agents.setdefault(row['caller'],[]).append(row)
    trend = [dict(day=day,avg_score=avg(items,'overall_score'),feedback_count=len(items),
                  avg_accuracy=avg(items,'score_data_accuracy'),avg_logic=avg(items,'score_logic'),
                  avg_actionability=avg(items,'score_actionability')) for day,items in sorted(groups.items())]
    by_agent = [dict(analysis_type=key,agent_name=key,avg_score=avg(items,'overall_score'),
                     run_count=len(items),case_count=len(items),good_count=sum(r['overall_score']>=7 for r in items),bad_count=sum(r['overall_score']<5 for r in items)) for key,items in agents.items()]
    return {'summary':summary,'trend':trend,'agents':by_agent,'low_quality':low[:max(1,min(limit,500))]}


def get_eval_source(source_type: str, source_id: int) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute('SELECT c.id,c.name FROM eval_case_sources s JOIN eval_cases c ON s.case_id=c.id WHERE source_type=? AND source_id=?', (source_type,source_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_governance_bad_case(source: str, case_id: int) -> dict | None:
    conn = _get_conn()
    try:
        if source == 'analysis':
            row = conn.execute("SELECT id,analysis_type as type,input_data as input,result_data as output,feedback_note as note,created_at FROM portfolio_analysis_records WHERE id=? AND feedback='unhelpful'", (case_id,)).fetchone()
        elif source == 'chat':
            row = conn.execute("SELECT id,caller as type,input_summary as input,output_summary as output,comment as note,created_at FROM llm_feedback WHERE id=? AND rating='unhelpful'", (case_id,)).fetchone()
        else:
            raise ValueError('来源必须为analysis或chat')
        return {'source':source,**dict(row)} if row else None
    finally:
        conn.close()
