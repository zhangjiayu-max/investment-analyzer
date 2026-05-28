"""SQLite 数据层 — 任务持久化"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "valuations.db"


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """建表，启动时调用。"""
    conn = _get_conn()

    # 任务表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            title TEXT,
            author TEXT,
            publish_time TEXT,
            content_text TEXT,
            codes_found TEXT,
            market_data TEXT,
            llm_analysis TEXT,
            images_dir TEXT,
            local_images TEXT,
            status TEXT DEFAULT 'pending',
            error_msg TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # 文章管理表（从 articles.json 同步）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seq INTEGER,
            url TEXT NOT NULL UNIQUE,
            title TEXT,
            publish_time TEXT,
            status TEXT DEFAULT 'pending',
            images_dir TEXT,
            manifest_path TEXT,
            image_count INTEGER DEFAULT 0,
            error_msg TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # 图片分析记录表（每张图片的分析状态）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analysis_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER REFERENCES articles(id),
            image_index INTEGER,
            image_path TEXT UNIQUE,
            image_url TEXT,
            index_code TEXT,
            index_name TEXT,
            metric_type TEXT,
            status TEXT DEFAULT 'pending',
            error_msg TEXT,
            raw_response TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # 指数估值时间序列表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_valuations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_code TEXT NOT NULL,
            index_name TEXT,
            snapshot_date TEXT NOT NULL,
            current_point REAL,
            change_pct REAL,
            metric_type TEXT NOT NULL DEFAULT '市盈率',
            current_value REAL,
            percentile REAL,
            danger_value REAL,
            median REAL,
            opportunity_value REAL,
            max_value REAL,
            min_value REAL,
            avg_value REAL,
            zscore REAL,
            source_image TEXT,
            source_url TEXT,
            raw_json TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(index_code, snapshot_date, metric_type)
        )
    """)

    # 迁移旧表数据（pe_ttm/pb 分列 → metric_type 统一字段）
    try:
        conn.execute("SELECT pe_ttm FROM index_valuations LIMIT 1")
        # 旧表存在，迁移
        _migrate_old_schema(conn)
    except sqlite3.OperationalError:
        pass  # 已经是新表

    # 修复 UNIQUE 约束：确保包含 metric_type（处理已存在但约束不对的表）
    _fix_unique_constraint(conn)

    # ── Agent 系统表 ──────────────────────────────────────

    # Agent 定义表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            system_prompt TEXT NOT NULL,
            knowledge_scope TEXT,
            icon TEXT DEFAULT 'robot',
            is_preset INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # 对话表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT '新对话',
            agent_id INTEGER REFERENCES agents(id),
            context_data TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # 消息表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id)")

    # 作者文章表（研究员雷牛牛等外部作者文章）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS author_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            title TEXT,
            author TEXT DEFAULT '研究员雷牛牛',
            publish_time TEXT,
            summary TEXT,
            content_text TEXT,
            content_html TEXT,
            article_type TEXT,
            tags TEXT,
            read_count INTEGER,
            like_count INTEGER,
            images TEXT,
            status TEXT DEFAULT 'pending',
            error_msg TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    _add_column_if_not_exists(conn, "author_articles", "images", "TEXT")
    _add_column_if_not_exists(conn, "author_articles", "content_html", "TEXT")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS linked_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT,
            title TEXT DEFAULT '',
            file_path TEXT,
            file_size INTEGER,
            file_type TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    _add_column_if_not_exists(conn, "linked_articles", "file_path", "TEXT")
    _add_column_if_not_exists(conn, "linked_articles", "file_size", "INTEGER")
    _add_column_if_not_exists(conn, "linked_articles", "file_type", "TEXT")
    _add_column_if_not_exists(conn, "linked_articles", "embed_status", "TEXT DEFAULT 'pending'")
    _add_column_if_not_exists(conn, "linked_articles", "chunks_count", "INTEGER DEFAULT 0")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS document_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            char_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (article_id) REFERENCES linked_articles(id) ON DELETE CASCADE
        )
    """)

    # 专家 Agent 调用记录表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            message_id INTEGER,
            agent_key TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            query TEXT,
            result TEXT,
            tool_calls TEXT,
            duration_ms INTEGER,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_conv ON agent_runs(conversation_id)")

    # ── 持仓管理表 ──────────────────────────────────────

    # 持仓表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            fund_code TEXT NOT NULL,
            fund_name TEXT NOT NULL,
            index_code TEXT,
            index_name TEXT,
            shares REAL DEFAULT 0,
            cost_price REAL DEFAULT 0,
            current_price REAL,
            total_cost REAL DEFAULT 0,
            current_value REAL,
            profit_loss REAL,
            profit_rate REAL,
            buy_date TEXT,
            last_update TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(user_id, fund_code)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_holdings_user ON portfolio_holdings(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_holdings_index ON portfolio_holdings(index_code)")
    _add_column_if_not_exists(conn, "portfolio_holdings", "price_updated_at", "TEXT")
    _add_column_if_not_exists(conn, "portfolio_holdings", "account", "TEXT DEFAULT '默认账户'")
    _add_column_if_not_exists(conn, "portfolio_holdings", "today_change_pct", "REAL DEFAULT 0")
    _add_column_if_not_exists(conn, "portfolio_holdings", "today_profit", "REAL DEFAULT 0")
    _add_column_if_not_exists(conn, "portfolio_holdings", "fund_category", "TEXT DEFAULT ''")
    _add_column_if_not_exists(conn, "portfolio_holdings", "has_base_position", "INTEGER DEFAULT 0")
    # 回填已有持仓的 fund_category
    rows = conn.execute(
        "SELECT id, fund_name FROM portfolio_holdings WHERE fund_category IS NULL OR fund_category = ''"
    ).fetchall()
    for r in rows:
        cat = classify_fund_category(r["fund_name"])
        if cat != "equity":  # 默认就是 equity 类型，只回填非默认的
            conn.execute("UPDATE portfolio_holdings SET fund_category = ? WHERE id = ?", (cat, r["id"]))
    _fix_holdings_unique_constraint(conn)

    # 交易记录表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            holding_id INTEGER REFERENCES portfolio_holdings(id) ON DELETE CASCADE,
            user_id TEXT DEFAULT 'default',
            fund_code TEXT NOT NULL,
            transaction_type TEXT NOT NULL,
            amount REAL NOT NULL,
            shares REAL,
            price REAL,
            transaction_date TEXT NOT NULL,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_holding ON portfolio_transactions(holding_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_fund ON portfolio_transactions(fund_code)")
    # 交易状态字段迁移（pending/confirmed/settled）
    _add_column_if_not_exists(conn, "portfolio_transactions", "status", "TEXT DEFAULT 'confirmed'")
    _add_column_if_not_exists(conn, "portfolio_transactions", "submitted_shares", "REAL")
    _add_column_if_not_exists(conn, "portfolio_transactions", "submitted_amount", "REAL")
    _add_column_if_not_exists(conn, "portfolio_transactions", "confirmed_at", "TEXT")
    _add_column_if_not_exists(conn, "portfolio_transactions", "settled_at", "TEXT")
    _add_column_if_not_exists(conn, "portfolio_transactions", "transaction_time", "TEXT")
    _add_column_if_not_exists(conn, "portfolio_transactions", "expected_confirm_date", "TEXT")
    _add_column_if_not_exists(conn, "portfolio_transactions", "is_system", "INTEGER DEFAULT 0")

    # 零钱每日收益字段
    _add_column_if_not_exists(conn, "portfolio_cash", "last_interest_date", "TEXT")
    _add_column_if_not_exists(conn, "portfolio_cash", "today_interest", "REAL DEFAULT 0")

    # 风险预警表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            alert_type TEXT NOT NULL,
            severity TEXT DEFAULT 'info',
            title TEXT NOT NULL,
            content TEXT,
            related_fund_code TEXT,
            related_fund_name TEXT,
            source TEXT,
            is_read INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_user ON portfolio_alerts(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_read ON portfolio_alerts(is_read)")

    # 零钱账户表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_cash (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default' UNIQUE,
            balance REAL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # 交易标签表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transaction_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER REFERENCES portfolio_transactions(id) ON DELETE CASCADE,
            tag TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_tags_tx ON transaction_tags(transaction_id)")

    # 持仓分析记录表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_analysis_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            analysis_type TEXT NOT NULL,
            summary TEXT,
            input_data TEXT,
            result_data TEXT,
            token_usage INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_par_user ON portfolio_analysis_records(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_par_type ON portfolio_analysis_records(analysis_type)")
    try:
        conn.execute("ALTER TABLE portfolio_analysis_records ADD COLUMN agent_id INTEGER DEFAULT NULL")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE portfolio_analysis_records ADD COLUMN feedback TEXT DEFAULT NULL")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE portfolio_analysis_records ADD COLUMN feedback_note TEXT DEFAULT NULL")
    except Exception:
        pass

    # 指数信息缓存表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_code TEXT NOT NULL UNIQUE,
            index_name TEXT,
            info TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # Agent 提示词版本历史表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_prompt_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            agent_type TEXT NOT NULL DEFAULT 'conversation',
            system_prompt TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_apv_agent ON agent_prompt_versions(agent_id, agent_type)")

    # ── Token 用量统计表 ──────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model TEXT,
            caller TEXT DEFAULT '',
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    # 兼容已有表：加 caller 列（早期版本没有）
    _add_column_if_not_exists(conn, "token_usage", "caller", "TEXT DEFAULT ''")

    # 初始化预设 Agent
    _init_preset_agents(conn)

    # 初始化评测集表
    init_eval_tables(conn)

    # Skill 文档表（供 RAG 索引的知识文档）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skill_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_type TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    # 债券知识库表（债券市场数据快照）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bond_market_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_type TEXT NOT NULL,
            content TEXT NOT NULL,
            snapshot_date TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    conn.commit()
    conn.close()

    # 初始化 AI 分析表
    _init_analysis_tables()


def create_task(url: str) -> int:
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


def update_task(task_id: int, **fields):
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


def get_task(task_id: int) -> dict | None:
    """获取任务详情。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_dict(row)


def list_tasks(limit: int = 50) -> list[dict]:
    """任务列表，按创建时间倒序。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, url, title, status, created_at, updated_at FROM tasks ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def delete_task(task_id: int) -> bool:
    """删除任务。"""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def _add_column_if_not_exists(conn: sqlite3.Connection, table: str, column: str, col_type: str):
    """安全新增列，已存在则忽略。"""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    except sqlite3.OperationalError:
        pass


def _fix_unique_constraint(conn: sqlite3.Connection):
    """修复 index_valuations 的 UNIQUE 约束，确保包含 metric_type。"""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='index_valuations'"
    ).fetchone()
    if not row:
        return
    create_sql = row[0]
    # 约束已包含 metric_type，无需修复
    if "UNIQUE(index_code, snapshot_date, metric_type)" in create_sql:
        return
    # 没有 UNIQUE 约束或约束不含 metric_type，需要修复
    if "UNIQUE" not in create_sql:
        return  # 无约束，不处理

    print("[db] 修复 UNIQUE 约束: 添加 metric_type...")
    # 备份并重建
    conn.execute("ALTER TABLE index_valuations RENAME TO index_valuations_backup")
    conn.execute("""
        CREATE TABLE index_valuations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_code TEXT NOT NULL,
            index_name TEXT,
            snapshot_date TEXT NOT NULL,
            current_point REAL,
            change_pct REAL,
            metric_type TEXT NOT NULL DEFAULT '市盈率',
            current_value REAL,
            percentile REAL,
            danger_value REAL,
            median REAL,
            opportunity_value REAL,
            max_value REAL,
            min_value REAL,
            avg_value REAL,
            zscore REAL,
            source_image TEXT,
            source_url TEXT,
            raw_json TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(index_code, snapshot_date, metric_type)
        )
    """)
    conn.execute("""
        INSERT INTO index_valuations (
            id, index_code, index_name, snapshot_date,
            current_point, change_pct, metric_type,
            current_value, percentile, danger_value, median,
            opportunity_value, max_value, min_value, avg_value, zscore,
            source_image, source_url, raw_json, created_at
        )
        SELECT id, index_code, index_name, snapshot_date,
            current_point, change_pct, metric_type,
            current_value, percentile, danger_value, median,
            opportunity_value, max_value, min_value, avg_value, zscore,
            source_image, source_url, raw_json, created_at
        FROM index_valuations_backup
    """)
    conn.execute("DROP TABLE index_valuations_backup")
    print("[db] UNIQUE 约束修复完成")


def _fix_holdings_unique_constraint(conn: sqlite3.Connection):
    """修复 portfolio_holdings 的 UNIQUE 约束：(user_id, fund_code) → (user_id, account, fund_code)。"""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='portfolio_holdings'"
    ).fetchone()
    if not row:
        return
    create_sql = row[0]
    # 已包含 account，无需修复
    if "UNIQUE(user_id,account,fund_code)" in create_sql.replace(" ", "").replace("\n", ""):
        return

    print("[db] 修复 portfolio_holdings UNIQUE 约束: 添加 account...")
    # 备份并重建
    conn.execute("ALTER TABLE portfolio_holdings RENAME TO portfolio_holdings_backup")
    conn.execute("""
        CREATE TABLE portfolio_holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            fund_code TEXT NOT NULL,
            fund_name TEXT NOT NULL,
            account TEXT DEFAULT '默认账户',
            index_code TEXT,
            index_name TEXT,
            shares REAL DEFAULT 0,
            cost_price REAL DEFAULT 0,
            current_price REAL,
            total_cost REAL DEFAULT 0,
            current_value REAL,
            profit_loss REAL,
            profit_rate REAL,
            buy_date TEXT,
            last_update TEXT,
            notes TEXT,
            price_updated_at TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(user_id, account, fund_code)
        )
    """)
    conn.execute("""
        INSERT INTO portfolio_holdings (
            id, user_id, fund_code, fund_name, index_code, index_name,
            shares, cost_price, current_price, total_cost, current_value,
            profit_loss, profit_rate, buy_date, last_update, notes,
            price_updated_at, created_at, updated_at
        )
        SELECT id, user_id, fund_code, fund_name, index_code, index_name,
            shares, cost_price, current_price, total_cost, current_value,
            profit_loss, profit_rate, buy_date, last_update, notes,
            price_updated_at, created_at, updated_at
        FROM portfolio_holdings_backup
    """)
    conn.execute("DROP TABLE portfolio_holdings_backup")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_holdings_user ON portfolio_holdings(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_holdings_index ON portfolio_holdings(index_code)")
    print("[db] portfolio_holdings UNIQUE 约束修复完成")


def _migrate_old_schema(conn: sqlite3.Connection):
    """将 old schema（pe_ttm/pb 分列）迁移到新 schema（metric_type 统一字段）。"""
    cur = conn.execute("PRAGMA table_info(index_valuations)")
    cols = {r[1] for r in cur.fetchall()}
    # 判断是否已是新表（有 metric_type 列）
    if "metric_type" in cols:
        return

    print("[db] 迁移旧表 index_valuations → 统一 metric_type 字段...")

    # 备份旧表
    conn.execute("ALTER TABLE index_valuations RENAME TO index_valuations_old")

    # 建新表
    conn.execute("""
        CREATE TABLE index_valuations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_code TEXT NOT NULL,
            index_name TEXT,
            snapshot_date TEXT NOT NULL,
            current_point REAL,
            change_pct REAL,
            metric_type TEXT NOT NULL DEFAULT '市盈率',
            current_value REAL,
            percentile REAL,
            danger_value REAL,
            median REAL,
            opportunity_value REAL,
            max_value REAL,
            min_value REAL,
            avg_value REAL,
            zscore REAL,
            source_image TEXT,
            source_url TEXT,
            raw_json TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(index_code, snapshot_date, metric_type)
        )
    """)

    # 查出旧数据
    rows = conn.execute("SELECT * FROM index_valuations_old").fetchall()
    migrated = 0
    for r in rows:
        d = dict(r)
        # PE 行
        pe_fields = [
            ("current_value", d.get("pe_ttm")),
            ("percentile", d.get("pe_percentile")),
            ("danger_value", d.get("pe_danger")),
            ("median", d.get("pe_median")),
            ("opportunity_value", d.get("pe_opportunity")),
            ("max_value", d.get("pe_max")),
            ("min_value", d.get("pe_min")),
            ("avg_value", d.get("pe_avg")),
            ("zscore", d.get("pe_zscore")),
        ]
        if any(v is not None for _, v in pe_fields):
            conn.execute("""
                INSERT INTO index_valuations (
                    index_code, index_name, snapshot_date,
                    current_point, change_pct, metric_type,
                    current_value, percentile, danger_value, median,
                    opportunity_value, max_value, min_value, avg_value, zscore,
                    source_image, source_url, raw_json
                ) VALUES (?, ?, ?, ?, ?, '市盈率', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d.get("index_code"), d.get("index_name"), d.get("snapshot_date"),
                d.get("current_point"), d.get("change_pct"),
                d.get("pe_ttm"), d.get("pe_percentile"), d.get("pe_danger"),
                d.get("pe_median"), d.get("pe_opportunity"),
                d.get("pe_max"), d.get("pe_min"), d.get("pe_avg"), d.get("pe_zscore"),
                d.get("source_image"), d.get("source_url"), d.get("raw_json"),
            ))
            migrated += 1

        # PB 行
        pb_fields = [
            ("current_value", d.get("pb")),
            ("percentile", d.get("pb_percentile")),
            ("danger_value", d.get("pb_danger")),
            ("median", d.get("pb_median")),
            ("opportunity_value", d.get("pb_opportunity")),
            ("max_value", d.get("pb_max")),
            ("min_value", d.get("pb_min")),
            ("avg_value", d.get("pb_avg")),
        ]
        if any(v is not None for _, v in pb_fields):
            conn.execute("""
                INSERT INTO index_valuations (
                    index_code, index_name, snapshot_date,
                    current_point, change_pct, metric_type,
                    current_value, percentile, danger_value, median,
                    opportunity_value, max_value, min_value, avg_value, zscore,
                    source_image, source_url, raw_json
                ) VALUES (?, ?, ?, ?, ?, '市净率', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d.get("index_code"), d.get("index_name"), d.get("snapshot_date"),
                d.get("current_point"), d.get("change_pct"),
                d.get("pb"), d.get("pb_percentile"), d.get("pb_danger"),
                d.get("pb_median"), d.get("pb_opportunity"),
                d.get("pb_max"), d.get("pb_min"), d.get("pb_avg"), None,
                d.get("source_image"), d.get("source_url"), d.get("raw_json"),
            ))
            migrated += 1

    conn.execute("DROP TABLE index_valuations_old")
    print(f"[db] 迁移完成: {migrated} 条")


def _row_to_dict(row) -> dict:
    d = dict(row)
    for k in ("codes_found", "market_data", "local_images"):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


# ── 估值数据 CRUD ──────────────────────────────────────


def save_valuation(data: dict, source_image: str = None, source_url: str = None, snapshot_date: str = None) -> int:
    """
    保存估值数据，返回 id。同指数同日期同类型会更新。

    参数:
        data: 标准化后的数据，需包含 metric_type
        source_image: 来源图片路径
        source_url: 来源文章链接
        snapshot_date: 数据日期，默认今天
    """
    from datetime import date
    if not snapshot_date:
        snapshot_date = date.today().isoformat()

    # 确保必填字段有值
    if not data.get("index_code"):
        data["index_code"] = "UNKNOWN"
    if not data.get("index_name"):
        data["index_name"] = "未知指数"
    metric_type = data.get("metric_type", "市盈率")

    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO index_valuations (
            index_code, index_name, snapshot_date,
            current_point, change_pct, metric_type,
            current_value, percentile, danger_value, median,
            opportunity_value, max_value, min_value, avg_value, zscore,
            source_image, source_url, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(index_code, snapshot_date, metric_type) DO UPDATE SET
            index_name=excluded.index_name,
            current_point=excluded.current_point,
            change_pct=excluded.change_pct,
            current_value=excluded.current_value,
            percentile=excluded.percentile,
            danger_value=excluded.danger_value,
            median=excluded.median,
            opportunity_value=excluded.opportunity_value,
            max_value=excluded.max_value,
            min_value=excluded.min_value,
            avg_value=excluded.avg_value,
            zscore=excluded.zscore,
            source_image=excluded.source_image,
            source_url=excluded.source_url,
            raw_json=excluded.raw_json,
            created_at=datetime('now','localtime')
    """, (
        data.get("index_code"), data.get("index_name"), snapshot_date,
        data.get("current_point"), data.get("change_pct"), metric_type,
        data.get("current_value"), data.get("percentile"),
        data.get("danger_value"), data.get("median"),
        data.get("opportunity_value"), data.get("max_value"),
        data.get("min_value"), data.get("avg_value"), data.get("zscore"),
        source_image, source_url, data.get("raw_json"),
    ))
    # lastrowid 在 ON CONFLICT UPDATE 时为 0，改用 RETURNING 查真实 id
    if cur.lastrowid:
        valuation_id = cur.lastrowid
    else:
        valuation_id = conn.execute(
            "SELECT id FROM index_valuations WHERE index_code=? AND snapshot_date=?",
            (data.get("index_code"), snapshot_date)
        ).fetchone()[0]
    conn.commit()
    conn.close()
    return valuation_id


def get_valuation_history(index_code: str, days: int = 30, metric_type: str = None) -> list[dict]:
    """查询某指数最近 N 天的估值历史。"""
    conn = _get_conn()
    if metric_type:
        rows = conn.execute("""
            SELECT * FROM index_valuations
            WHERE index_code = ? AND metric_type = ?
            ORDER BY snapshot_date DESC
            LIMIT ?
        """, (index_code, metric_type, days)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM index_valuations
            WHERE index_code = ?
            ORDER BY snapshot_date DESC
            LIMIT ?
        """, (index_code, days)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_valuation(index_code: str, metric_type: str = None) -> dict | None:
    """获取某指数最新一条估值。"""
    conn = _get_conn()
    if metric_type:
        row = conn.execute("""
            SELECT * FROM index_valuations
            WHERE index_code = ? AND metric_type = ?
            ORDER BY snapshot_date DESC
            LIMIT 1
        """, (index_code, metric_type)).fetchone()
    else:
        row = conn.execute("""
            SELECT * FROM index_valuations
            WHERE index_code = ?
            ORDER BY snapshot_date DESC
            LIMIT 1
        """, (index_code,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_valuation_indexes() -> list[dict]:
    """列出所有有估值数据的指数，按 metric_type 分别显示，含最新值和记录数。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT iv.index_code, iv.index_name, iv.metric_type,
               iv.current_value, iv.percentile,
               iv.snapshot_date as latest_date,
               cnt.record_count
        FROM index_valuations iv
        INNER JOIN (
            SELECT index_code, metric_type, MAX(snapshot_date) as max_date
            FROM index_valuations
            GROUP BY index_code, metric_type
        ) latest ON iv.index_code = latest.index_code
                  AND iv.metric_type = latest.metric_type
                  AND iv.snapshot_date = latest.max_date
        INNER JOIN (
            SELECT index_code, metric_type, COUNT(*) as record_count
            FROM index_valuations
            GROUP BY index_code, metric_type
        ) cnt ON iv.index_code = cnt.index_code AND iv.metric_type = cnt.metric_type
        ORDER BY iv.index_code, iv.metric_type
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_index_freshness() -> list[dict]:
    """列出所有指数的最新数据日期和距今天数。"""
    conn = _get_conn()
    from datetime import date
    today = date.today().isoformat()
    rows = conn.execute("""
        SELECT index_code, index_name, MAX(snapshot_date) as latest_date,
               (julianday(?) - julianday(MAX(snapshot_date))) as stale_days
        FROM index_valuations
        GROUP BY index_code, index_name
        ORDER BY stale_days DESC
    """, (today,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_indexes_by_keyword(keyword: str) -> list[dict]:
    """按关键词模糊匹配指数名称或代码，返回去重后的指数列表。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT DISTINCT index_code, index_name
        FROM index_valuations
        WHERE index_name LIKE ? OR index_code LIKE ?
        ORDER BY index_code
    """, (f"%{keyword}%", f"%{keyword}%")).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 指数信息 CRUD ──────────────────────────────────────


def get_index_info(index_code: str) -> dict | None:
    """查询指数信息缓存。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM index_info WHERE index_code = ?", (index_code,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_index_info(index_code: str, index_name: str, info: str) -> int:
    """保存指数信息（UPSERT）。"""
    conn = _get_conn()
    conn.execute("""
        INSERT INTO index_info (index_code, index_name, info)
        VALUES (?, ?, ?)
        ON CONFLICT(index_code) DO UPDATE SET
            index_name = excluded.index_name,
            info = excluded.info,
            created_at = datetime('now','localtime')
    """, (index_code, index_name, info))
    conn.commit()
    row = conn.execute(
        "SELECT id FROM index_info WHERE index_code = ?", (index_code,)
    ).fetchone()
    conn.close()
    return row["id"] if row else 0


# ── Agent 提示词版本 CRUD ──────────────────────────────


def save_prompt_version(agent_id: int, agent_type: str, system_prompt: str):
    """保存当前提示词到版本历史（在更新 agent 前调用）。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT MAX(version) as max_ver FROM agent_prompt_versions WHERE agent_id = ? AND agent_type = ?",
        (agent_id, agent_type)
    ).fetchone()
    next_ver = (row["max_ver"] or 0) + 1
    conn.execute(
        "INSERT INTO agent_prompt_versions (agent_id, agent_type, system_prompt, version) VALUES (?, ?, ?, ?)",
        (agent_id, agent_type, system_prompt, next_ver)
    )
    conn.commit()
    conn.close()


def list_prompt_versions(agent_id: int, agent_type: str = 'conversation') -> list[dict]:
    """列出某 Agent 的所有提示词版本，最新在前。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM agent_prompt_versions WHERE agent_id = ? AND agent_type = ? ORDER BY version DESC",
        (agent_id, agent_type)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_prompt_version(version_id: int) -> dict | None:
    """获取单个版本详情。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM agent_prompt_versions WHERE id = ?", (version_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── 文章管理 CRUD ──────────────────────────────────────


def sync_articles(articles: list[dict]):
    """从 articles.json 同步文章列表到 DB。"""
    conn = _get_conn()
    for a in articles:
        conn.execute("""
            INSERT OR IGNORE INTO articles (seq, url, title, publish_time, status)
            VALUES (?, ?, ?, ?, 'pending')
        """, (a.get("seq"), a.get("url"), a.get("title"), a.get("publish_time", "")))
    conn.commit()
    conn.close()


def list_articles(status: str = None) -> list[dict]:
    """列出所有文章，可选按状态筛选。附带分析记录统计。"""
    conn = _get_conn()
    base = """
        SELECT a.*,
               COALESCE(stats.total_records, 0) AS total_records,
               COALESCE(stats.success_count, 0) AS success_count,
               COALESCE(stats.error_count, 0)   AS error_count,
               COALESCE(stats.pending_count, 0)  AS pending_count
        FROM articles a
        LEFT JOIN (
            SELECT article_id,
                   COUNT(*)                          AS total_records,
                   SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success_count,
                   SUM(CASE WHEN status='error'   THEN 1 ELSE 0 END) AS error_count,
                   SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending_count
            FROM analysis_records
            GROUP BY article_id
        ) stats ON a.id = stats.article_id
    """
    if status:
        rows = conn.execute(base + " WHERE a.status = ? ORDER BY a.publish_time DESC", (status,)).fetchall()
    else:
        rows = conn.execute(base + " ORDER BY a.publish_time DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_article(article_id: int) -> dict | None:
    """获取单篇文章详情（含分析记录统计）。"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT a.*,
               COALESCE(stats.total_records, 0) AS total_records,
               COALESCE(stats.success_count, 0) AS success_count,
               COALESCE(stats.error_count, 0)   AS error_count,
               COALESCE(stats.pending_count, 0)  AS pending_count
        FROM articles a
        LEFT JOIN (
            SELECT article_id,
                   COUNT(*)                          AS total_records,
                   SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success_count,
                   SUM(CASE WHEN status='error'   THEN 1 ELSE 0 END) AS error_count,
                   SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending_count
            FROM analysis_records
            GROUP BY article_id
        ) stats ON a.id = stats.article_id
        WHERE a.id = ?
    """, (article_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_article_by_seq(seq: int) -> dict | None:
    """按 seq 查找文章。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM articles WHERE seq = ?", (seq,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _extract_url_id(url: str) -> str:
    """提取 URL 尾部 ID 用于去重，如 https://mp.weixin.qq.com/s/Abc123 → Abc123。"""
    from urllib.parse import urlparse
    path = urlparse(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if "/" in path else url


def get_article_by_url(url: str) -> dict | None:
    """按 URL 尾部 ID 查找文章（去重用）。"""
    url_id = _extract_url_id(url)
    conn = _get_conn()
    # 用 LIKE 匹配所有包含该 ID 的 URL（兼容带/不带查询参数、不同域名等情况）
    row = conn.execute(
        "SELECT * FROM articles WHERE url LIKE ?", (f"%/{url_id}",)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def create_article(url: str, title: str = "", publish_time: str = "") -> int:
    """创建文章记录，返回 article_id。"""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO articles (url, title, publish_time, status) VALUES (?, ?, ?, 'pending')",
        (url, title, publish_time),
    )
    article_id = cur.lastrowid
    conn.commit()
    conn.close()
    return article_id


def update_article(article_id: int, **fields):
    """更新文章字段。"""
    if not fields:
        return
    fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [article_id]
    conn = _get_conn()
    conn.execute(f"UPDATE articles SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


# ── 图片分析记录 CRUD ────────────────────────────────


def create_analysis_record(article_id: int, image_index: int, image_path: str, image_url: str) -> int:
    """创建一条图片分析记录（已存在同一图片则忽略）。"""
    # 归一化路径为相对路径
    if image_path.startswith("/"):
        idx = image_path.find("data/images/")
        if idx >= 0:
            image_path = image_path[idx:]
    conn = _get_conn()
    cur = conn.execute("""
        INSERT OR IGNORE INTO analysis_records (article_id, image_index, image_path, image_url, status)
        VALUES (?, ?, ?, ?, 'pending')
    """, (article_id, image_index, image_path, image_url))
    conn.commit()
    if cur.lastrowid:
        rid = cur.lastrowid
    else:
        rid = conn.execute(
            "SELECT id FROM analysis_records WHERE image_path = ?", (image_path,)
        ).fetchone()[0]
    conn.close()
    return rid


def update_analysis_record(record_id: int, **fields):
    """更新分析记录字段。"""
    if not fields:
        return
    fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [record_id]
    conn = _get_conn()
    conn.execute(f"UPDATE analysis_records SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def list_all_analysis_records(search: str = None, limit: int = 200) -> list[dict]:
    """列出所有分析记录，关联文章信息，支持按指标名/指数名模糊搜索。"""
    conn = _get_conn()
    base = """
        SELECT ar.*, a.title as article_title,
               COALESCE(a.publish_time, ar.created_at) as publish_time,
               a.url as article_url
        FROM analysis_records ar
        LEFT JOIN articles a ON ar.article_id = a.id
    """
    params = []
    if search:
        base += " WHERE ar.index_name LIKE ? OR ar.index_code LIKE ? OR ar.metric_type LIKE ? OR a.title LIKE ?"
        q = f"%{search}%"
        params = [q, q, q, q]
    base += " ORDER BY COALESCE(a.publish_time, ar.created_at) DESC, ar.image_index LIMIT ?"
    params.append(limit)
    rows = conn.execute(base, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_analysis_records(article_id: int) -> list[dict]:
    """获取某篇文章的所有分析记录。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM analysis_records WHERE article_id = ? ORDER BY image_index",
        (article_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_analysis_record(record_id: int) -> dict | None:
    """获取单条分析记录。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM analysis_records WHERE id = ?", (record_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_valuation_by_image(image_path: str) -> dict | None:
    """按图片路径查找估值数据。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM index_valuations WHERE source_image = ? ORDER BY created_at DESC LIMIT 1",
        (image_path,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ══════════════════════════════════════════════════════
# Agent 系统 CRUD
# ══════════════════════════════════════════════════════

def _init_preset_agents(conn):
    """初始化预设 Agent（幂等，已存在则跳过）。"""
    presets = [
        {
            "name": "估值分析师",
            "description": "专注指数估值分析，结合历史分位点、趋势变化给出投资建议",
            "system_prompt": (
                "## 人设\n"
                "你是一位拥有 10 年经验的指数估值分析师，专注于 A 股和港股指数的估值研究。"
                "你的知识库包含大量指数的估值数据（市盈率、市净率、股息率、风险溢价等），"
                "以及历史分位点和趋势。\n\n"
                "## 分析框架\n"
                "### 估值水平判断\n"
                "- 使用历史分位点作为核心指标\n"
                "- 分位点 <20%：深度低估，建议分批建仓\n"
                "- 分位点 20-40%：相对低估，可适度配置\n"
                "- 分位点 40-60%：合理区间，持有观望\n"
                "- 分位点 60-80%：相对高估，考虑减仓\n"
                "- 分位点 >80%：深度高估，建议止盈\n\n"
                "### 趋势分析\n"
                "- 观察近 3-6 个月估值走势\n"
                "- 结合宏观经济环境判断趋势持续性\n"
                "- 关注资金流向和市场情绪\n\n"
                "### 风险收益比\n"
                "- 计算 z-score 衡量偏离程度\n"
                "- 对比历史最大回撤\n"
                "- 评估当前买入的潜在下行风险\n\n"
                "## 输出规范\n"
                "1. **结论先行**：先给出明确的判断（低估/合理/高估）\n"
                "2. **数据支撑**：引用具体分位点、PE、PB 等数据\n"
                "3. **趋势判断**：说明近期估值变化趋势\n"
                "4. **风险提示**：指出主要风险因素\n"
                "5. **操作建议**：给出具体但非时点性的建议\n\n"
                "## 思维链\n"
                "收到问题后，请按以下步骤思考：\n"
                "1. 理解用户的核心诉求（是问估值？问操作？问趋势？）\n"
                "2. 检索相关指数的最新估值数据\n"
                "3. 用分析框架进行系统分析\n"
                "4. 综合判断给出结论\n"
                "5. 标注置信度和主要风险\n\n"
                "## 知识边界\n"
                "- 擅长：指数估值分析、定投策略、行业轮动\n"
                "- 不擅长：个股深度分析、宏观经济预测、政策解读\n"
                "- 超出范围时说明：\"这超出了我的专业范围，建议咨询...\"\n\n"
                "回答时必须引用具体数字，不要泛泛而谈。"
            ),
            "knowledge_scope": '{"rag_types": ["valuation", "analysis"]}',
            "icon": "chart",
            "is_preset": 1,
        },
        {
            "name": "投资研究助手",
            "description": "综合型助手，可分析文章、解读市场数据、回答投资问题",
            "system_prompt": (
                "## 人设\n"
                "你是一位综合型投资研究助手，擅长解读投资文章、分析市场数据、"
                "回答投资问题、对比不同投资标的。\n\n"
                "## 分析框架\n"
                "### 文章解读\n"
                "- 提取核心观点和关键数据\n"
                "- 识别作者的投资逻辑\n"
                "- 评估观点的可信度和时效性\n\n"
                "### 市场分析\n"
                "- 结合估值数据和市场情绪\n"
                "- 关注资金流向和行业轮动\n"
                "- 给出多维度的分析视角\n\n"
                "### 投资对比\n"
                "- 从估值、趋势、风险三个维度对比\n"
                "- 给出明确的优劣分析\n"
                "- 提供选择建议\n\n"
                "## 输出规范\n"
                "1. **引用来源**：明确指出数据和观点的来源\n"
                "2. **区分主客观**：事实和观点要分开\n"
                "3. **风险提示**：每个建议都要有风险提示\n"
                "4. **操作建议**：适当给出可操作的建议\n\n"
                "## 思维链\n"
                "收到问题后，请按以下步骤思考：\n"
                "1. 理解用户的核心诉求\n"
                "2. 检索知识库获取相关信息\n"
                "3. 综合分析给出结论\n"
                "4. 标注信息来源和置信度\n\n"
                "## 知识边界\n"
                "- 擅长：文章解读、估值分析、投资对比\n"
                "- 不擅长：个股推荐、时机预测、政策解读\n"
                "- 超出范围时诚实说明"
            ),
            "knowledge_scope": '{"rag_types": ["article", "valuation", "analysis"]}',
            "icon": "research",
            "is_preset": 1,
        },
        {
            "name": "风险管理师",
            "description": "专注风险评估与控制，提供回撤分析、波动率评估、止损建议",
            "system_prompt": (
                "## 人设\n"
                "你是一位专业的风险管理师，专注于投资组合的风险评估与控制。"
                "你的目标是帮助用户识别、量化和管理投资风险。\n\n"
                "## 分析框架\n"
                "### 风险识别\n"
                "- 市场风险：系统性风险、行业风险\n"
                "- 估值风险：高估资产的回调风险\n"
                "- 流动性风险：小盘股、冷门指数\n"
                "- 集中风险：单一行业/主题过度集中\n\n"
                "### 风险量化\n"
                "- 最大回撤：历史最大回撤幅度\n"
                "- 波动率：近期波动率水平\n"
                "- z-score：当前估值偏离程度\n"
                "- 夏普比率：风险调整后收益\n\n"
                "### 风险控制\n"
                "- 仓位管理：单一标的不超过总仓位的 20%\n"
                "- 止损策略：根据波动率设定动态止损线\n"
                "- 再平衡：定期调整至目标配比\n"
                "- 对冲：通过债券、黄金等对冲风险\n\n"
                "## 输出规范\n"
                "1. **风险等级**：明确标注风险等级（低/中/高/极高）\n"
                "2. **风险来源**：列出主要风险因素\n"
                "3. **量化指标**：引用具体的风险指标\n"
                "4. **控制建议**：给出具体的风险控制措施\n\n"
                "## 思维链\n"
                "收到问题后，请按以下步骤思考：\n"
                "1. 识别用户问题中的风险点\n"
                "2. 量化相关风险指标\n"
                "3. 评估风险等级\n"
                "4. 给出风险控制建议\n\n"
                "## 知识边界\n"
                "- 擅长：风险评估、回撤分析、止损策略、仓位管理\n"
                "- 不擅长：收益预测、个股推荐、宏观政策\n"
                "- 超出范围时说明：\"风险评估是我的专长，但这个问题超出了我的能力范围\""
            ),
            "knowledge_scope": '{"rag_types": ["valuation", "analysis"]}',
            "icon": "shield",
            "is_preset": 1,
        },
        {
            "name": "资产配置师",
            "description": "专注资产配置策略，提供股债配比、行业轮动、定投策略建议",
            "system_prompt": (
                "## 人设\n"
                "你是一位专业的资产配置师，专注于帮助用户构建和优化投资组合。"
                "你的目标是通过科学的资产配置实现风险和收益的平衡。\n\n"
                "## 分析框架\n"
                "### 资产配置原则\n"
                "- 分散化：不要把鸡蛋放在一个篮子里\n"
                "- 再平衡：定期调整至目标配比\n"
                "- 风险匹配：配置与风险承受能力匹配\n"
                "- 长期视角：避免频繁交易\n\n"
                "### 配置策略\n"
                "- 股债配比：根据年龄和风险偏好确定\n"
                "- 行业轮动：根据估值和趋势调整\n"
                "- 定投策略：分批买入降低成本\n"
                "- 核心卫星：核心仓位宽基指数，卫星仓位行业主题\n\n"
                "### 定投策略\n"
                "- 普通定投：固定金额定期买入\n"
                "- 智慧定投：低估多买，高估少买\n"
                "- 目标定投：设定目标收益率止盈\n"
                "- 轮动定投：在不同指数间轮动\n\n"
                "## 输出规范\n"
                "1. **配置建议**：给出明确的资产配比建议\n"
                "2. **逻辑说明**：解释配置背后的逻辑\n"
                "3. **风险提示**：说明配置的风险点\n"
                "4. **调整建议**：给出何时需要调整的建议\n\n"
                "## 思维链\n"
                "收到问题后，请按以下步骤思考：\n"
                "1. 了解用户的风险偏好和投资目标\n"
                "2. 检索相关资产的估值数据\n"
                "3. 设计合理的资产配置方案\n"
                "4. 给出实施和调整建议\n\n"
                "## 知识边界\n"
                "- 擅长：资产配置、定投策略、组合优化\n"
                "- 不擅长：个股推荐、时机预测、衍生品\n"
                "- 超出范围时说明：\"资产配置是我的专长，但这个问题建议咨询...\""
            ),
            "knowledge_scope": '{"rag_types": ["valuation", "article"]}',
            "icon": "pie",
            "is_preset": 1,
        },
        {
            "name": "需求澄清",
            "description": "分析用户问题，判断任务复杂度，决定需要咨询哪些专家",
            "system_prompt": (
                "## 人设\n"
                "你是一位需求分析专家，负责理解用户的投资问题，并决定如何最优地回答它。\n\n"
                "## 分析任务\n"
                "收到用户问题后，你需要分析并返回以下 JSON 格式的结果：\n"
                "```json\n"
                "{\n"
                "  \"complexity\": \"simple|medium|complex\",\n"
                "  \"specialists\": [\"valuation_expert\", \"market_analyst\", \"risk_assessor\", \"allocation_advisor\"],\n"
                "  \"reason\": \"简要说明为什么这样分类\",\n"
                "  \"refined_query\": \"优化后的问题（如果需要）\"\n"
                "}\n"
                "```\n\n"
                "## 复杂度判断标准\n"
                "### simple（简单）\n"
                "- 单一数据查询：如\"沪深300估值多少\"、\"债市温度\"\n"
                "- 直接查表类：如\"PE是多少\"、\"百分位多少\"\n"
                "- 只需要1个专家即可回答\n\n"
                "### medium（中等）\n"
                "- 需要分析但范围明确：如\"白酒估值高吗\"、\"最近有什么新闻\"\n"
                "- 需要1-2个专家协作\n"
                "- 可能需要RAG知识库辅助\n\n"
                "### complex（复杂）\n"
                "- 投资决策类：如\"白酒能买吗\"、\"该加仓还是减仓\"\n"
                "- 多维度分析：如\"帮我做个定投方案\"、\"现在怎么配置\"\n"
                "- 需要3-4个专家协作\n"
                "- 必须结合估值、新闻、风险等多方面信息\n\n"
                "## 专家选择指南\n"
                "- **估值相关**（PE/PB/百分位/高估低估）→ valuation_expert\n"
                "- **新闻/政策/市场动态** → market_analyst\n"
                "- **风险/回撤/波动率/持仓风险** → risk_assessor\n"
                "- **配置/定投/股债配比/持仓配置** → allocation_advisor\n"
                "- **持仓/加仓/减仓/盈亏/我的基金** → 需要结合持仓数据，选 risk_assessor 或 allocation_advisor\n\n"
                "## 输出要求\n"
                "只输出 JSON，不要其他文字。"
            ),
            "knowledge_scope": '{}',
            "icon": "robot",
            "is_preset": 1,
        },
    ]
    for agent in presets:
        conn.execute("""
            INSERT OR IGNORE INTO agents (name, description, system_prompt, knowledge_scope, icon, is_preset)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (agent["name"], agent["description"], agent["system_prompt"],
              agent["knowledge_scope"], agent["icon"], agent["is_preset"]))
        # 更新已存在的预设 Agent 的 system_prompt
        conn.execute("""
            UPDATE agents SET description=?, system_prompt=?, knowledge_scope=?, icon=?
            WHERE name=? AND is_preset=1
        """, (agent["description"], agent["system_prompt"], agent["knowledge_scope"],
              agent["icon"], agent["name"]))


# ── Agent CRUD ──────────────────────────────────────

def list_agents() -> list[dict]:
    """列出所有 Agent。"""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM agents ORDER BY is_preset DESC, id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_agent(agent_id: int) -> dict | None:
    """获取单个 Agent。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_agent(name: str, system_prompt: str, description: str = "",
                 knowledge_scope: str = "", icon: str = "robot") -> int:
    """创建自定义 Agent。"""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO agents (name, description, system_prompt, knowledge_scope, icon) VALUES (?, ?, ?, ?, ?)",
        (name, description, system_prompt, knowledge_scope, icon),
    )
    agent_id = cur.lastrowid
    conn.commit()
    conn.close()
    return agent_id


def update_agent(agent_id: int, **fields):
    """更新 Agent 字段。"""
    if not fields:
        return
    conn = _get_conn()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [agent_id]
    conn.execute(f"UPDATE agents SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def delete_agent(agent_id: int):
    """删除 Agent（仅限非预设）。"""
    conn = _get_conn()
    conn.execute("DELETE FROM agents WHERE id = ? AND is_preset = 0", (agent_id,))
    conn.commit()
    conn.close()


# ── Conversation CRUD ──────────────────────────────────────

def list_conversations() -> list[dict]:
    """列出所有对话，按更新时间倒序。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT c.*, a.name as agent_name, a.icon as agent_icon,
               (SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) as message_count
        FROM conversations c
        LEFT JOIN agents a ON c.agent_id = a.id
        ORDER BY c.updated_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_conversation(conv_id: int) -> dict | None:
    """获取单个对话。"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT c.*, a.name as agent_name, a.icon as agent_icon
        FROM conversations c
        LEFT JOIN agents a ON c.agent_id = a.id
        WHERE c.id = ?
    """, (conv_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_conversation(title: str = "新对话", agent_id: int = None,
                        context_data: str = None) -> int:
    """创建对话。"""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO conversations (title, agent_id, context_data) VALUES (?, ?, ?)",
        (title, agent_id, context_data),
    )
    conv_id = cur.lastrowid
    conn.commit()
    conn.close()
    return conv_id


def update_conversation(conv_id: int, **fields):
    """更新对话字段。"""
    if not fields:
        return
    fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [conv_id]
    conn.execute(f"UPDATE conversations SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def delete_conversation(conv_id: int):
    """删除对话及其所有消息。"""
    conn = _get_conn()
    conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
    conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    conn.commit()
    conn.close()


# ── Message CRUD ──────────────────────────────────────

def get_messages(conv_id: int, limit: int = 50) -> list[dict]:
    """获取对话的消息历史（最近 N 条）。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT * FROM messages
        WHERE conversation_id = ?
        ORDER BY id DESC
        LIMIT ?
    """, (conv_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


def create_message(conv_id: int, role: str, content: str, metadata: str = None) -> int:
    """创建消息。"""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO messages (conversation_id, role, content, metadata) VALUES (?, ?, ?, ?)",
        (conv_id, role, content, metadata),
    )
    msg_id = cur.lastrowid
    conn.execute("UPDATE conversations SET updated_at = datetime('now','localtime') WHERE id = ?", (conv_id,))
    conn.commit()
    conn.close()
    return msg_id


def update_message_metadata(msg_id: int, metadata_dict: dict):
    """更新消息的 metadata（增量保存执行进度）。"""
    import json as _json
    conn = _get_conn()
    conn.execute("UPDATE messages SET metadata = ? WHERE id = ?",
                 (_json.dumps(metadata_dict, ensure_ascii=False), msg_id))
    conn.commit()
    conn.close()


def update_message_content_and_metadata(msg_id: int, content: str, metadata_dict: dict):
    """更新消息的 content 和 metadata（最终保存）。"""
    import json as _json
    conn = _get_conn()
    conn.execute("UPDATE messages SET content = ?, metadata = ? WHERE id = ?",
                 (content, _json.dumps(metadata_dict, ensure_ascii=False), msg_id))
    conn.commit()
    conn.close()


# ── 作者文章 CRUD ──────────────────────────────────────


def create_author_article(url: str, title: str = "", publish_time: str = "",
                          summary: str = "", article_type: str = "", tags: str = "",
                          read_count: int = None, like_count: int = None) -> int:
    """创建作者文章记录，返回 id。"""
    conn = _get_conn()
    cur = conn.execute(
        """INSERT OR IGNORE INTO author_articles
           (url, title, publish_time, summary, article_type, tags, read_count, like_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (url, title, publish_time, summary, article_type, tags, read_count, like_count),
    )
    row_id = cur.lastrowid
    if row_id == 0:
        row_id = conn.execute(
            "SELECT id FROM author_articles WHERE url = ?", (url,)
        ).fetchone()[0]
    conn.commit()
    conn.close()
    return row_id


def update_author_article(article_id: int, **fields):
    """更新作者文章字段。"""
    if not fields:
        return
    fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [article_id]
    conn = _get_conn()
    conn.execute(f"UPDATE author_articles SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def get_author_article_by_url(url: str) -> dict | None:
    """按 URL 查找作者文章（去重用）。"""
    url_id = _extract_url_id(url)
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM author_articles WHERE url LIKE ?", (f"%/{url_id}",)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_author_articles(status: str = None, search: str = None, limit: int = 200) -> list[dict]:
    """列出作者文章，可选按状态筛选和搜索。"""
    conn = _get_conn()
    base = "SELECT * FROM author_articles"
    conditions = []
    params = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if search:
        conditions.append("(title LIKE ? OR summary LIKE ? OR content_text LIKE ?)")
        q = f"%{search}%"
        params.extend([q, q, q])
    if conditions:
        base += " WHERE " + " AND ".join(conditions)
    base += " ORDER BY publish_time DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(base, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_author_article(article_id: int) -> dict | None:
    """获取单篇作者文章。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM author_articles WHERE id = ?", (article_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_author_article(article_id: int) -> bool:
    """删除作者文章。"""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM author_articles WHERE id = ?", (article_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def count_author_articles() -> dict:
    """统计作者文章数量（总数、各状态）。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM author_articles GROUP BY status"
    ).fetchall()
    conn.close()
    result = {"total": 0, "pending": 0, "crawling": 0, "done": 0, "error": 0}
    for r in rows:
        result[r["status"]] = r["cnt"]
        result["total"] += r["cnt"]
    return result


# ── 链接文章 CRUD ──────────────────────────────────────────

def create_linked_article(title: str = "", file_path: str = "",
                          file_size: int = 0, file_type: str = "") -> int:
    """创建文档记录，返回 id。"""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO linked_articles (title, file_path, file_size, file_type) VALUES (?, ?, ?, ?)",
        (title, file_path, file_size, file_type),
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


def list_linked_articles(limit: int = 200) -> list[dict]:
    """列出文档。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM linked_articles ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_linked_article(article_id: int) -> dict | None:
    """获取单篇文档。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM linked_articles WHERE id = ?", (article_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_linked_article_file(article_id: int, file_path: str):
    """更新文档的文件路径。"""
    conn = _get_conn()
    conn.execute("UPDATE linked_articles SET file_path = ? WHERE id = ?", (file_path, article_id))
    conn.commit()
    conn.close()


def delete_linked_article(article_id: int) -> bool:
    """删除文档记录。"""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM linked_articles WHERE id = ?", (article_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def update_linked_article_embed_status(article_id: int, status: str, chunks_count: int = 0):
    """更新文档的 embedding 状态。"""
    conn = _get_conn()
    conn.execute(
        "UPDATE linked_articles SET embed_status = ?, chunks_count = ? WHERE id = ?",
        (status, chunks_count, article_id),
    )
    conn.commit()
    conn.close()


def save_document_chunks(article_id: int, chunks: list[str]):
    """保存文档分块数据（先删旧的再插入新的）。"""
    conn = _get_conn()
    conn.execute("DELETE FROM document_chunks WHERE article_id = ?", (article_id,))
    for i, chunk in enumerate(chunks):
        conn.execute(
            "INSERT INTO document_chunks (article_id, chunk_index, content, char_count) VALUES (?, ?, ?, ?)",
            (article_id, i, chunk, len(chunk)),
        )
    conn.commit()
    conn.close()


def get_document_chunks(article_id: int) -> list[dict]:
    """获取文档的所有分块。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM document_chunks WHERE article_id = ? ORDER BY chunk_index",
        (article_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Agent Runs ──────────────────────────────────────────


def create_agent_run(conversation_id: int, message_id: int, agent_key: str,
                     agent_name: str, query: str, result: str = "",
                     tool_calls: str = "", duration_ms: int = 0) -> int:
    """记录一次专家 Agent 调用，返回 run_id。"""
    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO agent_runs (conversation_id, message_id, agent_key, agent_name,
                                query, result, tool_calls, duration_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (conversation_id, message_id, agent_key, agent_name,
          query, result, tool_calls, duration_ms))
    run_id = cur.lastrowid
    conn.commit()
    conn.close()
    return run_id


def get_agent_runs(conversation_id: int, limit: int = 50) -> list[dict]:
    """获取对话的专家 Agent 调用记录。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT * FROM agent_runs
        WHERE conversation_id = ?
        ORDER BY id DESC
        LIMIT ?
    """, (conversation_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 持仓管理 CRUD ──────────────────────────────────────


def create_holding(fund_code: str, fund_name: str, shares: float = 0,
                   cost_price: float = None, current_price: float = None,
                   index_code: str = None,
                   index_name: str = None, buy_date: str = None,
                   notes: str = None, user_id: str = "default",
                   account: str = "花无缺", fund_category: str = None) -> int:
    """新增持仓，返回 holding_id。自动分类基金类型（equity/bond/hybrid/money_market/index 等）。"""
    if fund_category is None:
        fund_category = classify_fund_category(fund_name)
    if cost_price is None:
        cost_price = current_price or 0
    total_cost = shares * cost_price
    current_value = shares * current_price if (current_price and current_price > 0) else None
    profit_loss = (current_value - total_cost) if current_value is not None else None
    profit_rate = (profit_loss / total_cost) if (profit_loss is not None and total_cost > 0) else None
    conn = _get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO portfolio_holdings
                (user_id, fund_code, fund_name, index_code, index_name,
                 shares, cost_price, total_cost, buy_date, notes,
                 current_price, current_value, profit_loss, profit_rate, account,
                 fund_category)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, fund_code, fund_name, index_code, index_name,
              shares, cost_price, total_cost, buy_date, notes,
              current_price, current_value, profit_loss, profit_rate, account,
              fund_category))
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError(f"基金 {fund_code} 在账户「{account}」中已存在")
    holding_id = cur.lastrowid

    # 同步创建一笔系统买入交易（is_system=1），确保 _recalculate_holding 能正确计算
    tx_date = buy_date or datetime.now().strftime("%Y-%m-%d")
    conn.execute("""
        INSERT INTO portfolio_transactions
            (holding_id, user_id, fund_code, transaction_type, amount, shares, price,
             transaction_date, status, notes, is_system)
        VALUES (?, ?, ?, 'buy', ?, ?, ?, ?, 'confirmed', '初始建仓', 1)
    """, (holding_id, user_id, fund_code, total_cost, shares, cost_price, tx_date))

    conn.commit()
    conn.close()
    return holding_id


def get_holding(holding_id: int) -> dict | None:
    """获取单个持仓。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM portfolio_holdings WHERE id = ?", (holding_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_holdings(user_id: str = "default", account: str = None) -> list[dict]:
    """获取用户所有持仓，可选按账号筛选。"""
    conn = _get_conn()
    if account:
        rows = conn.execute("""
            SELECT * FROM portfolio_holdings
            WHERE user_id = ? AND account = ?
            ORDER BY updated_at DESC
        """, (user_id, account)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM portfolio_holdings
            WHERE user_id = ?
            ORDER BY updated_at DESC
        """, (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_holding(holding_id: int, **fields):
    """更新持仓字段。自动重算 total_cost / current_value / profit_loss / profit_rate。"""
    if not fields:
        return
    fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 如果更新了 fund_name 且未指定 fund_category，自动分类
    if "fund_name" in fields and "fund_category" not in fields:
        fields["fund_category"] = classify_fund_category(fields["fund_name"])

    # 如果更新了 shares 或 cost_price，重算 total_cost
    conn = _get_conn()
    current = conn.execute("SELECT * FROM portfolio_holdings WHERE id = ?", (holding_id,)).fetchone()
    if not current:
        conn.close()
        return
    current = dict(current)

    shares = fields.get("shares", current.get("shares", 0))
    cost_price = fields.get("cost_price", current.get("cost_price", 0))
    current_price = fields.get("current_price", current.get("current_price"))

    fields["total_cost"] = shares * cost_price
    if current_price is not None and current_price > 0:
        fields["current_value"] = shares * current_price
        fields["profit_loss"] = fields["current_value"] - fields["total_cost"]
        fields["profit_rate"] = fields["profit_loss"] / fields["total_cost"] if fields["total_cost"] > 0 else 0

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [holding_id]
    conn.execute(f"UPDATE portfolio_holdings SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def delete_holding(holding_id: int) -> bool:
    """删除持仓及其交易记录。"""
    conn = _get_conn()
    conn.execute("DELETE FROM portfolio_transactions WHERE holding_id = ?", (holding_id,))
    cur = conn.execute("DELETE FROM portfolio_holdings WHERE id = ?", (holding_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def get_portfolio_summary(user_id: str = "default", account: str = None) -> dict:
    """获取持仓汇总：总市值、总成本、总盈亏、收益率、现金余额、总资产。排除已清仓记录。可选按账号筛选。"""
    holdings = list_holdings(user_id, account=account)
    active = [h for h in holdings if (h.get("shares") or 0) > 0]
    total_cost = sum(h.get("total_cost", 0) or 0 for h in active)
    total_value = sum(h.get("current_value", 0) or 0 for h in active)
    total_profit = total_value - total_cost
    profit_rate = total_profit / total_cost if total_cost > 0 else 0

    # 现金余额
    cash_info = get_cash_balance(user_id)
    cash_balance = cash_info.get("balance", 0) if cash_info else 0
    total_assets = total_value + cash_balance

    # 按基金类型分类统计
    fund_type_breakdown = {}
    for h in active:
        cat = h.get("fund_category") or "equity"
        if cat not in fund_type_breakdown:
            fund_type_breakdown[cat] = {"count": 0, "value": 0, "cost": 0}
        fund_type_breakdown[cat]["count"] += 1
        fund_type_breakdown[cat]["value"] += (h.get("current_value") or 0)
        fund_type_breakdown[cat]["cost"] += (h.get("total_cost") or 0)

    return {
        "holding_count": len(active),
        "total_cost": round(total_cost, 2),
        "total_value": round(total_value, 2),
        "total_profit": round(total_profit, 2),
        "profit_rate": round(profit_rate, 4),
        "cash_balance": round(cash_balance, 2),
        "total_assets": round(total_assets, 2),
        "fund_type_breakdown": fund_type_breakdown,
        "holdings": holdings,
    }


# ── 零钱账户 ──────────────────────────────────────


def get_cash_balance(user_id: str = "default") -> dict:
    """获取零钱余额（自动触发每日收益结算）。"""
    # 先触发每日收益
    interest_info = accrue_cash_interest(user_id)
    conn = _get_conn()
    row = conn.execute("SELECT * FROM portfolio_cash WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        conn.execute("INSERT INTO portfolio_cash (user_id, balance) VALUES (?, 0)", (user_id,))
        conn.commit()
        result = {"user_id": user_id, "balance": 0, "updated_at": None, "today_interest": 0, "last_interest_date": None}
    else:
        result = dict(row)
    conn.close()
    result["accrued"] = interest_info
    return result


def add_cash(user_id: str, amount: float) -> float:
    """增加（或减少）零钱余额。amount 可为负数（支出）。返回新余额。"""
    conn = _get_conn()
    conn.execute("""
        INSERT INTO portfolio_cash (user_id, balance, updated_at)
        VALUES (?, ?, datetime('now','localtime'))
        ON CONFLICT(user_id) DO UPDATE SET
            balance = balance + ?,
            updated_at = datetime('now','localtime')
    """, (user_id, amount, amount))
    conn.commit()
    row = conn.execute("SELECT balance FROM portfolio_cash WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row["balance"] if row else 0


def set_cash_balance(user_id: str, balance: float) -> float:
    """直接设置零钱余额（覆盖写入）。返回新余额。"""
    conn = _get_conn()
    conn.execute("""
        INSERT INTO portfolio_cash (user_id, balance, updated_at)
        VALUES (?, ?, datetime('now','localtime'))
        ON CONFLICT(user_id) DO UPDATE SET
            balance = ?,
            updated_at = datetime('now','localtime')
    """, (user_id, balance, balance))
    conn.commit()
    row = conn.execute("SELECT balance FROM portfolio_cash WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row["balance"] if row else 0


# ── 零钱每日收益 ──────────────────────────────────────

ANNUAL_YIELD_7D = 0.01512  # 7日年化 1.5120%


def accrue_cash_interest(user_id: str = "default") -> dict:
    """计算并发放零钱每日收益。每天只会执行一次。返回今日收益信息。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM portfolio_cash WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        conn.execute("INSERT INTO portfolio_cash (user_id, balance) VALUES (?, 0)", (user_id,))
        conn.commit()
        conn.close()
        return {"interest": 0, "balance": 0, "date": None}

    cash = dict(row)
    today = datetime.now().strftime("%Y-%m-%d")
    last_date = cash.get("last_interest_date")

    if last_date == today:
        conn.close()
        return {
            "interest": cash.get("today_interest", 0) or 0,
            "balance": cash["balance"],
            "date": today,
            "already_accrued": True,
        }

    balance = cash["balance"]
    if balance <= 0:
        # 余额为0，只更新日期标记，不产生收益
        conn.execute(
            "UPDATE portfolio_cash SET last_interest_date = ?, today_interest = 0 WHERE user_id = ?",
            (today, user_id),
        )
        conn.commit()
        conn.close()
        return {"interest": 0, "balance": 0, "date": today}

    # 每日收益 = 余额 × 年化 / 365
    daily_rate = ANNUAL_YIELD_7D / 365
    interest = round(balance * daily_rate, 2)
    new_balance = round(balance + interest, 2)

    conn.execute("""
        UPDATE portfolio_cash SET
            balance = ?,
            today_interest = ?,
            last_interest_date = ?,
            updated_at = datetime('now','localtime')
        WHERE user_id = ?
    """, (new_balance, interest, today, user_id))
    conn.commit()
    conn.close()
    return {"interest": interest, "balance": new_balance, "date": today, "already_accrued": False}


# ── 交易记录 CRUD ──────────────────────────────────────


def create_transaction(fund_code: str, transaction_type: str, amount: float,
                       transaction_date: str, shares: float = None,
                       price: float = None, holding_id: int = None,
                       notes: str = None, user_id: str = "default",
                       status: str = None, submitted_shares: float = None,
                       submitted_amount: float = None,
                       transaction_time: str = None,
                       expected_confirm_date: str = None) -> int:
    """新增交易记录，返回 transaction_id。自动更新持仓数据。

    status: 'pending' | 'confirmed' | None(默认confirmed)
      - pending: 买入时 amount 存入 submitted_amount，卖出时 shares 存入 submitted_shares
      - confirmed: 直接确认，amount/shares/price 存入实际值
    """
    # 确定状态
    if status is None:
        status = 'confirmed'

    if status == 'pending':
        # pending 交易：amount=0, shares=NULL，实际值存 submitted_* 字段
        actual_amount = 0
        actual_shares = None
        actual_price = None
        if transaction_type == 'buy':
            submitted_amount = submitted_amount or amount
        elif transaction_type == 'sell':
            submitted_shares = submitted_shares or shares
    else:
        actual_amount = amount
        actual_shares = shares
        actual_price = price

    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO portfolio_transactions
            (holding_id, user_id, fund_code, transaction_type, amount, shares, price,
             transaction_date, notes, status, submitted_shares, submitted_amount,
             transaction_time, expected_confirm_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (holding_id, user_id, fund_code, transaction_type, actual_amount, actual_shares,
          actual_price, transaction_date, notes, status, submitted_shares, submitted_amount,
          transaction_time, expected_confirm_date))
    tx_id = cur.lastrowid
    conn.commit()
    conn.close()

    # 只有 confirmed 状态才更新持仓数据
    if holding_id and status in ('confirmed', 'settled'):
        _recalculate_holding(holding_id)

    return tx_id


def list_transactions(fund_code: str = None, holding_id: int = None,
                      user_id: str = "default", limit: int = 100,
                      include_system: bool = False) -> list[dict]:
    """获取交易记录列表。默认不包含系统自动生成的（is_system=1）交易。"""
    conn = _get_conn()
    conditions = ["user_id = ?"]
    params = [user_id]
    if fund_code:
        conditions.append("fund_code = ?")
        params.append(fund_code)
    if holding_id:
        conditions.append("holding_id = ?")
        params.append(holding_id)
    if not include_system:
        conditions.append("(is_system IS NULL OR is_system = 0)")

    where = " AND ".join(conditions)
    params.append(limit)
    rows = conn.execute(f"""
        SELECT * FROM portfolio_transactions
        WHERE {where}
        ORDER BY transaction_date DESC, id DESC
        LIMIT ?
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _recalculate_holding(holding_id: int):
    """根据交易记录重新计算持仓数据。先处理买入再处理卖出，避免顺序问题。

    对于没有交易记录的持仓（直接导入/手动创建），保留原有数据不覆盖。
    """
    conn = _get_conn()
    holding = conn.execute("SELECT * FROM portfolio_holdings WHERE id = ?", (holding_id,)).fetchone()
    if not holding:
        conn.close()
        return
    holding = dict(holding)

    txs = conn.execute("""
        SELECT * FROM portfolio_transactions
        WHERE holding_id = ? AND (status IN ('confirmed', 'settled') OR status IS NULL)
        ORDER BY id ASC
    """, (holding_id,)).fetchall()

    # 如果没有任何已确认的交易，说明持仓是直接创建的，不重新计算
    if not txs:
        conn.close()
        return

    total_shares = 0.0
    total_cost = 0.0

    # 如果持仓有基准数据（直接导入/手动创建的初始持仓），先加入基准
    has_base = holding.get("has_base_position")
    print(f"[DEBUG _recalculate_holding] holding_id={holding_id}, has_base_position={has_base}, shares={holding.get('shares')}, total_cost={holding.get('total_cost')}, tx_count={len(txs)}")
    if has_base:
        total_shares = holding.get("shares") or 0
        total_cost = holding.get("total_cost") or 0

    current_price = holding.get("current_price") or 0

    # 先处理所有买入
    for tx in txs:
        tx = dict(tx)
        shares = tx.get("shares", 0) or 0
        amount = tx.get("amount", 0) or 0
        tx_price = tx.get("price") or 0
        if tx["transaction_type"] == "buy" and (shares > 0 or amount > 0):
            # 只有金额没有份额时，用净值自动估算份额
            if shares <= 0 and amount > 0:
                price = tx_price or current_price
                if price > 0:
                    shares = amount / price
            total_shares += shares
            total_cost += amount

    # 再处理所有卖出（按平均成本扣减）
    for tx in txs:
        tx = dict(tx)
        shares = tx.get("shares", 0) or 0
        if tx["transaction_type"] == "sell" and shares > 0:
            if total_shares > 0:
                avg_cost = total_cost / total_shares
                total_cost -= avg_cost * shares
            total_shares -= shares
        elif tx["transaction_type"] == "dividend":
            amount = tx.get("amount", 0) or 0
            if amount > 0:
                total_cost -= amount

    if total_shares < 0:
        total_shares = 0

    print(f"[DEBUG _recalculate_holding] RESULT: total_shares={total_shares}, total_cost={total_cost}")
    cost_price = total_cost / total_shares if total_shares > 0 else 0
    current_value = total_shares * current_price if current_price > 0 else None
    profit_loss = (current_value - total_cost) if current_value is not None else None
    profit_rate = (profit_loss / total_cost) if (profit_loss is not None and total_cost > 0) else None

    conn.execute("""
        UPDATE portfolio_holdings SET
            shares = ?, cost_price = ?, total_cost = ?,
            current_value = ?, profit_loss = ?, profit_rate = ?,
            updated_at = datetime('now','localtime')
        WHERE id = ?
    """, (total_shares, round(cost_price, 4), round(total_cost, 2),
          round(current_value, 2) if current_value is not None else None,
          round(profit_loss, 2) if profit_loss is not None else None,
          round(profit_rate, 4) if profit_rate is not None else None,
          holding_id))
    conn.commit()
    conn.close()


def confirm_transaction(tx_id: int, confirmed_price: float,
                        confirmed_shares: float = None,
                        confirmed_amount: float = None) -> bool:
    """确认交易：填入实际净值，计算实际份额/金额。

    买入：confirmed_shares = submitted_amount / confirmed_price
    卖出：confirmed_amount = submitted_shares * confirmed_price
    """
    conn = _get_conn()
    tx = conn.execute("SELECT * FROM portfolio_transactions WHERE id = ?", (tx_id,)).fetchone()
    if not tx:
        conn.close()
        return False
    tx = dict(tx)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tx_type = tx["transaction_type"]

    if tx_type == "buy":
        # 买入确认：金额 / 净值 = 份额
        sub_amount = confirmed_amount or tx.get("submitted_amount") or tx.get("amount") or 0
        if confirmed_price > 0:
            actual_shares = round(sub_amount / confirmed_price, 2)
        else:
            actual_shares = confirmed_shares or 0
        actual_amount = sub_amount
        actual_price = confirmed_price
    elif tx_type == "sell":
        # 卖出确认：份额 × 净值 = 金额
        sub_shares = confirmed_shares or tx.get("submitted_shares") or tx.get("shares") or 0
        actual_amount = round(sub_shares * confirmed_price, 2)
        actual_shares = sub_shares
        actual_price = confirmed_price
    else:
        # 分红等其他类型
        actual_amount = confirmed_amount or tx.get("amount") or 0
        actual_shares = confirmed_shares
        actual_price = confirmed_price

    conn.execute("""
        UPDATE portfolio_transactions SET
            status = 'confirmed', amount = ?, shares = ?, price = ?,
            confirmed_at = ?
        WHERE id = ?
    """, (actual_amount, actual_shares, actual_price, now, tx_id))
    conn.commit()
    conn.close()

    if tx.get("holding_id"):
        _recalculate_holding(tx["holding_id"])

    # 卖出确认后，自动将金额计入零钱
    if tx_type == "sell" and actual_amount > 0:
        add_cash(tx.get("user_id", "default"), actual_amount)

    return True


def settle_transaction(tx_id: int) -> bool:
    """标记卖出交易已到账。"""
    conn = _get_conn()
    tx = conn.execute("SELECT * FROM portfolio_transactions WHERE id = ?", (tx_id,)).fetchone()
    if not tx:
        conn.close()
        return False
    tx = dict(tx)
    if tx.get("status") != "confirmed":
        conn.close()
        return False

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("""
        UPDATE portfolio_transactions SET status = 'settled', settled_at = ? WHERE id = ?
    """, (now, tx_id))
    conn.commit()
    conn.close()
    return True


def delete_transaction(tx_id: int) -> bool:
    """删除交易记录（仅允许 pending 状态）。"""
    conn = _get_conn()
    tx = conn.execute("SELECT * FROM portfolio_transactions WHERE id = ?", (tx_id,)).fetchone()
    if not tx:
        conn.close()
        return False
    tx = dict(tx)
    if tx.get("status") not in (None, "pending"):
        conn.close()
        return False
    conn.execute("DELETE FROM transaction_tags WHERE transaction_id = ?", (tx_id,))
    conn.execute("DELETE FROM portfolio_transactions WHERE id = ?", (tx_id,))
    conn.commit()
    conn.close()
    return True


# ── 基金净值更新 ──────────────────────────────────────


def fetch_fund_nav(fund_code: str) -> dict | None:
    """
    通过 akshare 获取基金最新净值。

    返回: {"nav": 0.57, "date": "2026-05-22", "change_pct": -2.1} 或 None
    """
    try:
        import akshare as ak
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator='单位净值走势')
        if df is None or len(df) == 0:
            return None
        last = df.iloc[-1]
        return {
            "nav": float(last["单位净值"]),
            "date": str(last["净值日期"]),
            "change_pct": float(last["日增长率"]) if last.get("日增长率") else None,
        }
    except Exception as e:
        print(f"[db] 获取基金 {fund_code} 净值失败: {e}")
        return None


def get_fund_nav_history(fund_code: str, user_id: str = "default", days: int = 365) -> dict | None:
    """获取基金净值历史 + 交易点标记（用于交易行为图表）。"""
    try:
        import akshare as ak
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator='单位净值走势')
        if df is None or len(df) == 0:
            return None

        nav_history = []
        for _, row in df.iterrows():
            nav_history.append({
                "date": str(row["净值日期"]),
                "nav": float(row["单位净值"]),
            })
        if days > 0 and len(nav_history) > days:
            nav_history = nav_history[-days:]

        conn = _get_conn()
        txs = conn.execute("""
            SELECT t.transaction_type, t.shares, t.price, t.amount, t.transaction_date
            FROM portfolio_transactions t
            WHERE t.fund_code = ? AND t.user_id = ?
                AND (t.is_system IS NULL OR t.is_system = 0)
                AND t.status IN ('confirmed', 'settled')
            ORDER BY t.transaction_date ASC
        """, (fund_code, user_id)).fetchall()
        conn.close()

        return {
            "nav_history": nav_history,
            "transactions": [dict(t) for t in txs],
        }
    except Exception as e:
        print(f"[db] 获取基金 {fund_code} 净值历史失败: {e}")
        return None


def refresh_holding_price(holding_id: int) -> dict | None:
    """
    刷新单个持仓的最新净值并更新数据库。

    返回: {"nav": 0.57, "date": "2026-05-22", "change_pct": -2.1,
           "today_profit": -12.34, "today_change_pct": -2.1} 或 None
    """
    conn = _get_conn()
    holding = conn.execute("SELECT * FROM portfolio_holdings WHERE id = ?", (holding_id,)).fetchone()
    if not holding:
        conn.close()
        return None
    holding = dict(holding)
    fund_code = holding["fund_code"]

    nav_data = fetch_fund_nav(fund_code)
    if not nav_data:
        conn.close()
        return None

    nav = nav_data["nav"]
    nav_date = nav_data["date"]
    change_pct = nav_data.get("change_pct")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    shares = holding.get("shares", 0) or 0
    total_cost = holding.get("total_cost", 0) or 0
    current_value = shares * nav
    profit_loss = current_value - total_cost
    profit_rate = profit_loss / total_cost if total_cost > 0 else 0

    # 今日盈亏 = 份额 × (当前净值 - 昨日净值)，通过涨跌幅反算昨日净值
    if change_pct is not None and (100 + change_pct) != 0:
        today_profit = round(current_value * change_pct / (100 + change_pct), 2)
    else:
        today_profit = 0

    conn.execute("""
        UPDATE portfolio_holdings SET
            current_price = ?,
            current_value = ?,
            profit_loss = ?,
            profit_rate = ?,
            today_change_pct = ?,
            today_profit = ?,
            price_updated_at = ?,
            updated_at = ?
        WHERE id = ?
    """, (round(nav, 4), round(current_value, 2), round(profit_loss, 2),
          round(profit_rate, 4), change_pct, today_profit, nav_date, now, holding_id))
    conn.commit()
    conn.close()

    nav_data["today_profit"] = today_profit
    nav_data["today_change_pct"] = change_pct
    return nav_data


def refresh_all_fund_prices(user_id: str = "default") -> list[dict]:
    """
    批量刷新用户所有持仓的最新净值。

    返回: [{"fund_code": "161725", "fund_name": "...", "nav": 0.57, "date": "2026-05-22"}, ...]
    """
    holdings = list_holdings(user_id)
    results = []
    for h in holdings:
        # 跳过已清仓持仓
        if (h.get("shares") or 0) <= 0:
            results.append({
                "fund_code": h["fund_code"],
                "fund_name": h["fund_name"],
                "skipped": True,
                "reason": "已清仓",
            })
            continue
        nav_data = fetch_fund_nav(h["fund_code"])
        if not nav_data:
            results.append({
                "fund_code": h["fund_code"],
                "fund_name": h["fund_name"],
                "error": "净值获取失败",
            })
            continue

        nav = nav_data["nav"]
        nav_date = nav_data["date"]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        shares = h.get("shares", 0) or 0
        total_cost = h.get("total_cost", 0) or 0
        current_value = shares * nav
        profit_loss = current_value - total_cost
        profit_rate = profit_loss / total_cost if total_cost > 0 else 0

        conn = _get_conn()
        change_pct = nav_data.get("change_pct")
        if change_pct is not None and (100 + change_pct) != 0:
            today_profit = round(current_value * change_pct / (100 + change_pct), 2)
        else:
            today_profit = 0

        conn.execute("""
            UPDATE portfolio_holdings SET
                current_price = ?,
                current_value = ?,
                profit_loss = ?,
                profit_rate = ?,
                today_change_pct = ?,
                today_profit = ?,
                price_updated_at = ?,
                updated_at = ?
            WHERE id = ?
        """, (round(nav, 4), round(current_value, 2), round(profit_loss, 2),
              round(profit_rate, 4), change_pct, today_profit, nav_date, now, h["id"]))
        conn.commit()
        conn.close()

        results.append({
            "fund_code": h["fund_code"],
            "fund_name": h["fund_name"],
            "nav": nav,
            "date": nav_date,
            "change_pct": change_pct,
            "today_profit": today_profit,
        })

    return results


# ── 基金信息查询 ──────────────────────────────────────


def lookup_fund_info(fund_code: str) -> dict | None:
    """通过 akshare 查询基金基本信息，自动填充名称、类型、跟踪标的。"""
    try:
        import akshare as ak
        df = ak.fund_overview_em(symbol=fund_code)
        if df is None or len(df) == 0:
            return None
        row = df.iloc[0]
        fund_name = str(row.get("基金简称", ""))
        fund_type_str = str(row.get("基金类型", ""))
        return {
            "fund_code": str(row.get("基金代码", fund_code)),
            "fund_name": fund_name,
            "fund_full_name": str(row.get("基金全称", "")),
            "fund_type": fund_type_str,
            "fund_category": classify_fund_category(fund_name, fund_type_str),
            "tracking_index": str(row.get("跟踪标的", "")),
            "fund_manager": str(row.get("基金经理人", "")),
            "scale": str(row.get("净资产规模", "")),
            "established": str(row.get("成立日期/规模", "")),
            "benchmark": str(row.get("业绩比较基准", "")),
        }
    except Exception as e:
        print(f"[db] 查询基金信息失败 {fund_code}: {e}")
        return None


def classify_bond_type(bond_name: str) -> str:
    """根据债券名称推断类型：利率债/信用债/可转债。"""
    name = bond_name.strip()
    # 可转债
    if "转债" in name:
        return "可转债"
    # 利率债：国债、政金债（国开/进出/农发）、地方政府债
    rate_keywords = ("国债", "国开", "进出", "农发", "政金", "地方债", "政府债", "央行")
    for kw in rate_keywords:
        if kw in name:
            return "利率债"
    # 其余归为信用债
    return "信用债"


def classify_fund_category(fund_name: str, fund_type: str = "") -> str:
    """根据基金名称和类型分类：equity / bond / hybrid / money_market / index / other。"""
    name = fund_name.strip()

    # 货币基金
    if any(kw in name for kw in ("货币", "货基", "现金", "流动性", "添利", "增利宝")):
        return "money_market"
    if "同业存单" in name:
        return "money_market"

    # 债券基金 — 纯债
    if any(kw in name for kw in ("纯债", "短债", "长债", "中短债", "中长债", "利率债", "信用债")):
        return "bond"
    if any(kw in name for kw in ("债券", "债基", "国债", "政金")):
        return "bond"
    if "中债" in name and "指数" in name:
        return "bond_index"

    # 可转债基金
    if "可转债" in name or "转债" in name:
        return "convertible_bond"

    # 指数基金
    if any(kw in name for kw in ("指数", "ETF", "ETF联接", "联接")):
        # 排除债券指数已在上面判断
        if any(kw in name for kw in ("债", "国债", "政金")):
            return "bond_index"
        return "index"

    # 混合型 — 名字含"混合"但未被债券规则捕获的
    if "混合" in name or "平衡" in name or "灵活" in name:
        if any(kw in fund_type for kw in ("债券", "债")):
            return "bond"
        return "hybrid"

    # 根据 fund_type 补充判断
    if "债券型" in fund_type:
        return "bond"
    if "货币型" in fund_type:
        return "money_market"
    if "混合型" in fund_type:
        return "hybrid"
    if "股票型" in fund_type:
        return "equity"

    # 默认归为 equity
    return "equity"


def get_fund_holdings(fund_code: str, year: str = None) -> dict:
    """获取基金持仓详情：股票重仓 + 债券持仓 + 资产配置。"""
    if not year:
        from datetime import datetime
        year = str(datetime.now().year)

    result = {
        "fund_code": fund_code,
        "top_stocks": [],
        "bond_holdings": [],
        "asset_allocation": [],
        "industry_allocation": [],
        "bond_type_summary": {},
    }

    # 1. 股票持仓 Top 10
    try:
        import akshare as ak
        df = ak.fund_portfolio_hold_em(symbol=fund_code, date=year)
        if df is not None and len(df) > 0:
            # 取最新一期的前 10
            quarters = df["季度"].unique()
            if len(quarters) > 0:
                latest_q = quarters[-1]
                latest = df[df["季度"] == latest_q].head(10)
                for _, r in latest.iterrows():
                    result["top_stocks"].append({
                        "stock_code": str(r.get("股票代码", "")),
                        "stock_name": str(r.get("股票名称", "")),
                        "pct_nav": float(r.get("占净值比例", 0)),
                        "shares": float(r.get("持股数", 0)),
                        "market_value": float(r.get("持仓市值", 0)),
                    })
    except Exception as e:
        print(f"[db] 获取股票持仓失败 {fund_code}: {e}")

    # 2. 债券持仓
    bond_type_counter = {}
    try:
        import akshare as ak
        df = ak.fund_portfolio_bond_hold_em(symbol=fund_code, date=year)
        if df is not None and len(df) > 0:
            quarters = df["季度"].unique()
            if len(quarters) > 0:
                latest_q = quarters[-1]
                latest = df[df["季度"] == latest_q].head(10)
                for _, r in latest.iterrows():
                    bond_name = str(r.get("债券名称", ""))
                    btype = classify_bond_type(bond_name)
                    bond_type_counter[btype] = bond_type_counter.get(btype, 0) + float(r.get("占净值比例", 0))
                    result["bond_holdings"].append({
                        "bond_code": str(r.get("债券代码", "")),
                        "bond_name": bond_name,
                        "pct_nav": float(r.get("占净值比例", 0)),
                        "market_value": float(r.get("持仓市值", 0)),
                        "bond_type": btype,
                    })
    except Exception as e:
        print(f"[db] 获取债券持仓失败 {fund_code}: {e}")

    result["bond_type_summary"] = {k: round(v, 2) for k, v in bond_type_counter.items()}

    # 3. 资产配置（股票/债券/现金/其他）
    try:
        import akshare as ak
        df = ak.fund_individual_detail_hold_xq(symbol=fund_code)
        if df is not None and len(df) > 0:
            for _, r in df.iterrows():
                result["asset_allocation"].append({
                    "type": str(r.get("资产类型", "")),
                    "pct": str(r.get("仓位占比", "")),
                })
    except Exception as e:
        print(f"[db] 获取资产配置失败 {fund_code}: {e}")

    # 4. 行业配置
    try:
        import akshare as ak
        df = ak.fund_portfolio_industry_allocation_em(symbol=fund_code, date=year)
        if df is not None and len(df) > 0:
            for _, r in df.head(10).iterrows():
                result["industry_allocation"].append({
                    "industry": str(r.get("行业类别", "")),
                    "pct_nav": float(r.get("占净值比例", 0)),
                })
    except Exception as e:
        print(f"[db] 获取行业配置失败 {fund_code}: {e}")

    return result


# ── AI 市场分析 Agent + 历史 ──────────────────────────────────────


def _init_analysis_tables():
    """初始化 AI 分析相关的表。"""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analysis_agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            system_prompt TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
            updated_at TIMESTAMP DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analysis_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_code TEXT,
            index_name TEXT,
            agent_id INTEGER,
            agent_name TEXT,
            prompt_used TEXT,
            news_context TEXT,
            valuation_context TEXT,
            result TEXT NOT NULL,
            token_usage INTEGER,
            created_at TIMESTAMP DEFAULT (datetime('now','localtime'))
        )
    """)
    # 插入默认 agent（如果不存在）
    # 逐个插入默认 agent（如缺失）
    for name, desc, prompt in [
        ('市场日报分析师', '基于最新财经新闻生成 A 股市场快报，服务于基金配置决策', DEFAULT_MARKET_ANALYST_PROMPT),
        ('分散度分析师', '基于持仓数据和 MCP 数据，分析持仓分散度、集中度风险并给出改进建议', DEFAULT_DIVERSIFICATION_PROMPT),
        ('全景诊断分析师', '从全局视角诊断投资组合健康状况，给出评分和加减仓建议', DEFAULT_PANORAMA_PROMPT),
        ('基金深度分析师', '对单只基金进行深度投资分析，评估买入质量并给出建议', DEFAULT_FUND_DEEP_DIVE_PROMPT),
        ('交易复盘分析师', '分析交易行为模式，识别情绪化偏差，建立投资纪律', DEFAULT_TRADE_REVIEW_PROMPT),
        ('情景推演分析师', '模拟不同市场情景下的组合变化，帮助提前做好准备', DEFAULT_WHATIF_PROMPT),
        ('热点分析专家', '基于新闻、估值、持仓数据，分析今日投资机会并输出结构化推荐', DEFAULT_HOTSPOTS_PROMPT),
        ('债券配置顾问', '结合债市温度、宏观政策、现有持仓和基金排行榜，推荐具体债券基金配置方案', DEFAULT_BOND_PROMPT),
        ('指数深度分析师', '针对单个指数进行深度分析，结合估值趋势、知识库、政策新闻给出投资建议', DEFAULT_INDEX_DEEP_ANALYSIS_PROMPT),
    ]:
        row = conn.execute("SELECT id FROM analysis_agents WHERE name = ?", (name,)).fetchone()
        if not row:
            conn.execute("INSERT INTO analysis_agents (name, description, system_prompt) VALUES (?, ?, ?)", (name, desc, prompt))
        elif name == '债券配置顾问':
            # 债券配置顾问的 prompt 需要保持最新（包含宏观政策分析等）
            conn.execute("UPDATE analysis_agents SET system_prompt = ?, description = ? WHERE name = ?", (prompt, desc, name))
        elif name == '指数深度分析师':
            # 指数深度分析师的 prompt 需要保持最新
            conn.execute("UPDATE analysis_agents SET system_prompt = ?, description = ? WHERE name = ?", (prompt, desc, name))
    conn.commit()
    conn.close()


DEFAULT_MARKET_ANALYST_PROMPT = """你是一位资深基金投资经理，为投资者提供每日市场简报。请基于下方【数据区】中的真实数据撰写报告，严禁编造数据。

## 报告结构（按以下顺序输出，使用 markdown 格式）

### 1. 市场速览
用 2-3 句话概括今日市场整体状态（涨跌、成交量、情绪），引用【今日新闻】中的关键事件。

### 2. 估值温度计
基于【指数估值】数据，点评当前市场估值水平：
- 哪些指数处于历史低估区（百分位 <20%），值得重点关注
- 哪些指数偏高估（百分位 >70%），需要警惕
- 整体估值水位一句话总结

### 3. 板块机会与风险
结合【今日新闻】和【指数估值】，分析：
- **机会板块**：政策/事件驱动 + 估值安全边际 + 资金动向，2-3 个板块
- **风险板块**：回调原因 + 是短期调整还是趋势反转，1-2 个板块

### 4. 持仓诊断
基于【持仓明细】和【持仓概况】，点评：
- 持仓整体健康度（集中度、盈亏情况）
- 与当前市场环境的契合度
- 是否需要关注仓位调整（如某只基金亏损过大或盈利过高）

### 5. 今日行动建议
给出 2-3 条具体、可执行的建议，例如：
- 可关注的低估指数/板块（附百分位数据）
- 持仓中需要留意的基金（附原因）
- 债市信号（参考【债券市场】温度）
- 现金仓位建议（参考【可用现金】）

## 写作要求
- 每个板块引用具体数据（百分位、涨跌幅、温度值等），不要空泛议论
- 结论清晰，避免"可能""或许"等模糊措辞
- 总字数控制在 600-900 字
- 使用 markdown：**加粗**重点、- 列表呈现"""

DEFAULT_DIVERSIFICATION_PROMPT = """# 角色
你是一位拥有 12 年基金组合管理经验的资深投资顾问，曾在头部财富管理机构负责多资产配置与风险控制。你擅长将复杂的组合分析转化为易懂的诊断和建议，特别关注持仓集中度、相关性以及估值维度的风险收益平衡。你从不给出具体的买入/卖出操作指令，只提供专业、可执行的分散化改进方向。

# 数据源说明（你将在上下文中收到以下数据段）

每次分析请求下方会附带若干数据段，各段的含义和使用方式如下：

| 数据段 | 来源 | 用途 |
|--------|------|------|
| 「持仓概览」「持仓明细」 | 系统本地数据 | 计算基金集中度、识别超标项 |
| 「类型分布」 | 系统本地数据 | 判断基金类型层面的分散度 |
| 「资产大类穿透分析」 | 盈米 MCP（穿透基金底层持仓） | **核实**：表面买的基金类型 vs 底层实际资产类别（股票/债券/现金）是否一致，识别"伪分散" |
| 「基金相关性分析」 | 盈米 MCP（计算基金间日收益相关系数） | **抓取**：其中列出的相关系数数值，对比阈值判断相关性风险 |
| 「XX行业配置」 | 盈米 MCP（单只基金的行业分布） | **补充参考**：了解该基金的行业分布，辅助判断行业集中度 |
| 「市场行情」 | 盈米 MCP | **背景参考**：仅需了解市场风格（成长/价值、大盘/小盘），不直接参与判断 |
| 「市场热点」「XX相关新闻」 | 盈米 MCP（财经资讯搜索） | **辅助判断**：了解与持仓行业相关的政策动向和热点事件，辅助评估行业集中度的政策风险 |
| 「估值参考（跟踪指数）」 | 系统本地估值数据库 | **权衡判断**：结合估值分位判断集中度风险：分位<20%属低估区域可适当容忍集中，分位>80%属高估区域需警惕集中风险 |

> **重要**：MCP 数据段可能因调用失败而缺失。如果某个 MCP 段缺失，明确标注「该维度数据暂缺，分析基于剩余数据」，并在置信度中降级，**不要编造数据或强行给出结论**。

# 分析框架

分析时必须遵循以下量化标准（这些阈值你都记得，不需要从 MCP 文本中推测）：

- **基金集中度**：任一基金占总投资组合 >25% 为「高度集中」，>15% 为「适度集中」，≤15% 为「合理」
- **行业/风格集中度**：单行业间接暴露 >35% 为「过高」，>20% 为「偏高」
- **相关性风险**：任意两只重仓基金（>10%仓位）之间日收益相关系数 ≥0.85 为「强同向波动」，0.7–0.85 为「中等相关」，<0.7 为「分散有效」
- **估值辅助**：对持仓基金跟踪或重仓的指数，参考以下规则：
   - PE/PB 历史分位 >80%（高估）：即便集中度不高也应警惕，建议减配该方向
   - 分位 <20%（低估）：即使集中度超标也可**适度容忍**，此时建议「暂持不追、等修复再降」而非直接减仓
   - 分位 20-50%（适中）：按标准集中度规则执行
   - 分位 50-80%（偏高）：若同时集中度超标，应优先降仓
   - **当集中度超标与估值极低（分位<20%）冲突时**：建议逻辑为「当前估值具备安全边际，直接减仓可能卖在底部，建议持有观察但暂停追加，待估值修复至合理区域后再逐步降至合规比例」
   - （此维度依赖上下文中的「估值参考」数据段，若无则跳过）
- **债市温度**：温度 >70 债市偏热，纯债类占比不宜过高；<30 偏冷可适度提升（若无则跳过）

# 输出规范

回答必须严格按以下格式组织，每条都需有数据支撑：

1. **总体判断**（一句话：分散度合理/有待优化/存在明显集中风险）
2. **集中度一览**（列出超标项，标注具体比例和阈值）
3. **相关性警示**（列出高度相关的基金对及其风险，说明该数据是否来自 MCP）
4. **分散化改进建议**（基于估值和当前组合结构，给出增配/减配的方向，不含具体基金代码或名称）
5. **一句话总结**（简练收束）

全文使用中文，150–250 字，避免术语堆砌，可读性优先。每条结论标注数据来源。

# 思维链

每次分析必须按以下步骤思考（不输出思考过程，只输出最终回答）：

1. **理解核心诉求**——用户希望检查当前持仓的分散度健康度。
2. **扫描本地数据**——用「持仓明细」和「类型分布」计算单基金占比、类型分布比例，识别超标候选。
3. **执行框架检验**——将计算值与阈值对比，确定集中度超标项。
4. **读取 MCP 数据**：
   - 从「资产大类穿透分析」确认底层资产是否与表面类型一致，发现「伪分散」；
   - 从「基金相关性分析」中提取相关系数，对比阈值判断相关性风险；
   - 从「行业配置」辅助判断行业集中度（若有）。
5. **读取估值数据**——从「估值参考（跟踪指数）」中获取 PE/PB 历史分位和 z-score：
   - 若分位<20%（低估）：即使集中度超标，也不急于建议减仓，改为「持有+暂停追加」
   - 若分位>80%（高估）：即便集中度尚可，也应提示高估风险
6. **形成结论**——先定性（优/良/差），再给出直接原因。
6. **撰写建议**——以降低相关性、分散行业暴露、利用估值洼地为核心，提出 1–3 条具体行动方向。
7. **标注置信度**——根据数据完整度标注「高/中/低」，并在有缺失时说明原因。

# 知识边界

- **擅长**：公募基金组合的集中度与相关性诊断；利用估值、温度指标优化分散度；将分析翻译成非专业投资者可理解的语言。
- **不擅长**：个股分析、具体买卖时点、预测市场短期走势、评估私募或非标产品。
- **能力外时**：若关键数据段缺失导致无法判断，明确告知用户「当前缺少 XX 数据，无法准确评估，建议补充后再分析」。

# 负面约束

- 不要给出任何具体买卖点位、价格或操作时点。
- 不要编造数据，所有分析严格基于上下文提供的数据段。
- 不要推荐具体基金代码或产品名称，只描述类型和方向。
- 不要使用「必然上涨」「保证收益」等承诺性语言。
- 不要评价基金经理个人能力或预测未来业绩。
- 当数据不足时，不要强行给出结论，指明信息缺口。"""

DEFAULT_PANORAMA_PROMPT = """# 角色

你是一位经验丰富的基金投资组合诊断专家。你的任务是基于用户的完整持仓数据、分散度分析结果、估值数据和 MCP 资产穿透分析，从全局视角诊断投资组合的健康状况。

# 上下文数据

你将在用户消息中获得以下数据段（实际提供的数据以消息中的标记为准）：

<context_sections>
1. **持仓明细** — 所有持仓基金代码、名称、份额、成本、市值、盈亏、占比、所属账户
2. **类型分布** — 按基金类型（股票型/混合型/债券型/指数型等）的仓位分布
3. **指数分布** — 各持仓跟踪的指数及对应仓位占比
4. **集中度数据** — 前 3/5 大持仓占比
5. **估值参考** — 各持仓跟踪指数的 PE/PB 历史分位、z-score、当前估值水位
6. **MCP 资产穿透** — 底层资产大类分布（若有）
7. **MCP 相关性** — 基金间相关性矩阵（若有）
8. **MCP 行业配置** — 穿透后的行业分布（若有）
9. **MCP 市场行情** — 最新行情数据（若有）
10. **新闻热点** — 近期重要财经新闻和政策（若有）
</context_sections>

# 分析框架：4 维评分 + 综合打分

## 维度 1：集中度风险（权重 25%）
- 前 3 大持仓占比 < 50% → 优（9-10 分）
- 前 3 大持仓占比 50-65% → 中（6-8 分）
- 前 3 大持仓占比 > 65% → 差（0-5 分）
- 单只基金占比 > 20% 扣 2 分，> 30% 扣 4 分
- 同一类型（如全部为股票型）占比 > 80% 扣 2 分

## 维度 2：估值健康度（权重 25%）
- 整体加权 PE 分位 < 30%（低估）→ 优（8-10 分）
- 整体加权 PE 分位 30-60%（适中）→ 中（6-8 分）
- 整体加权 PE 分位 > 60%（偏高）→ 差（0-5 分）
- 如部分数据缺失，基于已有数据判断并说明缺失情况

## 维度 3：分散化程度（权重 25%）
- 覆盖 3+ 个指数/行业 → 优（8-10 分）
- 覆盖 2 个指数/行业 → 中（5-7 分）
- 集中于 1 个指数/行业 → 差（0-4 分）
- MCP 相关性数据显示高度相关对 > 3 对 → 扣 2 分
- MCP 底层资产穿透显示"伪分散" → 扣 3 分

## 维度 4：市场适配度（权重 25%）
- 当前市场风格与组合风格匹配程度
- 判断当前市场风格（成长/价值/大盘/小盘/均衡）
- 组合在该市场风格下的历史表现预期
- 适配 → 优（8-10 分），部分适配 → 中（5-7 分），不适配 → 差（0-4 分）
- 如缺乏市场风格判断依据，标记为「数据不足」并给 6 分

## 综合评分
- 综合 = 各维度得分 × 权重的加权和
- 优秀（85+）/ 良好（70-84）/ 一般（55-69）/ 需关注（40-54）/ 差（<40）

# 输出结构

每次分析必须按以下结构输出，严格使用 Markdown 格式：

```markdown
## 组合健康评分: {综合得分}/100

### 四维评分
| 维度 | 得分 | 评级 | 说明 |
|------|------|------|------|
| 集中度风险 | {分数}/10 | {优/中/差} | {一句话说明} |
| 估值健康度 | {分数}/10 | {优/中/差} | {一句话说明} |
| 分散化程度 | {分数}/10 | {优/中/差} | {一句话说明} |
| 市场适配度 | {分数}/10 | {优/中/差} | {一句话说明} |

### 当前市场环境
{AI 生成的一小段市场环境分析，包括当前市场风格判断}

### 组合风险评估
- **集中度**：前 3 大持仓占比 XX%，{是否超标}。{详细说明}
- **相关性**：{哪些基金高度相关，相关性风险描述。若 MCP 未提供数据则注明}
- **估值**：整体处于 XX 分位，{偏高/适中/偏低}。{详细说明}
- **账户对比**：{花无缺 vs 小鱼儿的风险差异（若有两个账户）}

### 加减仓建议
| 基金 | 操作建议 | 紧急度 | 理由 |
|------|----------|--------|------|
| {基金名称} | 增配/持有/减配/清仓 | 高/中/低 | {核心理由} |

### 重点关注
{列出当前最需要关注的 3 件事，按优先级排序}
```

# 思维链

每次分析必须按以下步骤思考（不输出思考过程，只输出最终回答）：

1. **数据完整性检查** — 检查所有所需数据段是否齐全，标记缺失的数据
2. **计算集中度指标** — 从前 3/5 持仓占比、单只上限、类型集中度三个角度计算
3. **评估估值水位** — 计算整体加权估值分位，判断是否高估/低估
4. **评估分散度** — 统计覆盖的指数/行业数量，检查 MCP 相关性数据
5. **判断市场风格** — 基于新闻热点和行情，判断当前市场风格
6. **计算 4 维评分** — 逐项打分，计算加权综合分
7. **生成建议** — 基于以上分析，按优先级生成加减仓建议

# 知识边界

- **擅长**：公募基金组合诊断、估值水位分析、相关性风险识别、配置建议
- **不擅长**：个股分析、短期市场预测、非标产品评估
- 数据缺失时应明确说明，不得编造

# 负面约束

- 不给出具体买卖点位或价格
- 不编造数据，严格基于提供的数据段
- 不推荐具体基金代码，只描述类型和方向
- 不使用承诺性语言
- 数据不足时指明缺口，不强给结论
"""

DEFAULT_FUND_DEEP_DIVE_PROMPT = """# 角色

你是一位专业的基金投资分析专家。你的任务是对用户指定的单只基金进行深度分析，包括角色定位、持有收益质量、买入/卖出操作质量评估，并给出具体建议。

# 上下文数据

你将在用户消息中获得以下数据段：

<context_sections>
1. **基金持仓信息** — 基金代码、名称、持有份额、成本净值、当前净值、市值、盈亏、占比、持有时间
2. **账户信息** — 所属账户（花无缺/小鱼儿）
3. **交易记录** — 该基金的所有买入/卖出流水（含时间、金额、份额、成交价）
4. **估值历史** — 跟踪指数的 PE/PB 历史分位、z-score、当前分位
5. **基金基本面** — 通过 akshare 查询的重仓股、行业配置、资产配置（若有）
6. **MCP 数据** — 该基金的行业穿透分析、诊断结果（若有）
7. **同类对比** — 与同类型基金的收益排名对比（若有）
8. **组合角色上下文** — 该基金在整体组合中的占比、与其他基金的相关性
</context_sections>

# 分析框架

## 1. 角色定位
- 该基金在组合中的角色：核心持仓（长期底仓）/ 卫星持仓（行业暴露）/ 战术配置（短期博弈）
- 功能重叠检查：是否与其他持仓基金追踪相同或高度相关的指数

## 2. 持有期收益质量
- 总持有时间（天）
- 简单年化收益率（总盈亏 / 成本 / 持有年数 × 100%）
- 最大浮亏（买入后最大回撤幅度，基于交易记录判断）
- 买入时估值分位 vs 当前估值分位对比，判断买贵了还是买便宜了

## 3. 买入操作质量评分
评估每笔买入操作的质量：
- 买入时对应指数的估值分位 < 30% → 好（加 2 分）
- 买入时估值分位 30-60% → 中（加 1 分）
- 买入时估值分位 > 60% → 差（扣 1 分）
- 后续补仓在更低分位 → 加分（平均成本降低）
- 后续补仓在更高分位 → 扣分（平均成本抬升）

## 4. 卖出操作质量评分
- 卖出时估值分位 > 70%（止盈合理）→ 好
- 卖出时估值分位 < 30%（恐慌卖出）→ 差
- 卖出后走势评估（若有后续数据）

## 5. 综合评分
- 总得分 = 买入质量 + 卖出质量（如有）
- 评级：A（优秀 90+）/ B（良好 70-89）/ C（一般 50-69）/ D（需改进 <50）

# 输出结构

```markdown
## {基金名称} 深度分析报告

### 总体评分：{分数}/100 | 评级：{A/B/C/D}
**建议：{继续持有/加仓/减仓/清仓}**

### 基本信息
| 项目 | 内容 |
|------|------|
| 组合角色 | {核心/卫星/战术} |
| 持有时间 | {X 天} |
| 累计盈亏 | {金额}（{百分比}） |
| 年化收益 | {百分比} |
| 当前估值分位 | {PE/PB 分位} |

### 买入质量评估
{逐笔分析买入操作，以时间线或表格展示}

### 卖出质量评估
{逐笔分析卖出操作（如有）}

### 当前估值位置
{估值分析及对后市的影响}

### 建议
{具体建议，含理由}
```

# 思维链

1. **定位角色** — 根据该基金在组合中的占比和持仓时间判断角色
2. **收集交易数据** — 梳理所有买入/卖出记录
3. **逐笔评估** — 结合估值分位评估每笔操作
4. **计算评分** — 基于评估结果打分
5. **形成建议** — 基于角色 + 估值 + 操作质量的综合判断

# 负面约束

- 不保证未来收益
- 不编造交易记录或估值数据
- 客观评价操作，不进行人身评价
- 数据不足时明确说明
"""

DEFAULT_TRADE_REVIEW_PROMPT = """# 角色

你是一位冷静客观的交易行为分析教练。你的任务是分析用户的交易记录，识别操作模式中的优点和问题，帮助用户建立更好的投资纪律。

# 上下文数据

你将在用户消息中获得以下数据段：

<context_sections>
1. **交易记录** — 所有买入/卖出流水（含时间、基金、金额、份额、价格、状态）
2. **交易标签** — 用户为交易打的标签（追涨/抄底/定投/止盈/止损/调仓等，若有）
3. **估值历史** — 各指数在交易时点的估值分位（若有）
4. **持仓变化** — 交易前后的持仓对比
5. **账户信息** — 所属账户
</context_sections>

# 分析框架

## 1. 操作总览
- 统计期间内买入 X 笔 / 卖出 X 笔
- 净投入金额
- 涉及的基金数量

## 2. 买入时机评估
- 每笔买入时对应指数的估值分位
- 低估区域买入占比（好）
- 高估区域买入占比（需改进）
- 买入频率评估：过于频繁提示情绪化风险

## 3. 卖出时机评估
- 每笔卖出时对应指数的估值分位
- 止盈评估（高估区域卖出 = 好）
- 止损评估（低估区域卖出 = 需改进）

## 4. 行为模式识别
- 追涨杀跌行为识别
- 定投纪律评分
- 情绪化交易识别
- 操作偏好总结

## 5. 改进建议
- 具体的行为改进建议
- 可执行的纪律规则

# 输出结构

```markdown
## 交易复盘报告 — {时间范围}

### 操作总览
买入 {X} 笔 · 卖出 {Y} 笔 · 净投入 {金额} · 涉及 {N} 只基金

### 买入质量评分：{X}/10
{评价和分析}

### 卖出质量评分：{X}/10
{评价和分析}

### 行为模式
{模式分析和建议}

### 改进计划
1. {具体建议}
2. {具体建议}
3. {具体建议}
```

# 负面约束
- 不预测未来走势
- 不评价个人性格，只分析行为
- 不给出具体买卖时点
"""

DEFAULT_WHATIF_PROMPT = """# 角色

你是一位投资组合情景分析专家。你的任务是模拟不同市场情景下用户组合的变化，帮助用户提前了解潜在风险和收益，做好应对准备。

# 上下文数据

你将在用户消息中获得以下数据段和用户选择的情景：

<context_sections>
1. **持仓数据** — 所有持仓基金代码、名称、份额、成本净值、当前净值、市值、占比
2. **估值数据** — 各基金跟踪指数的当前 PE/PB 分位、历史极值、中位数
3. **MCP 行情** — 最新市场行情数据（若有）
4. **用户选择的情景** — 用户指定的情景类型和参数
</context_sections>

# 支持的情景类型

## 情景 A：市场整体下跌 X%
用户指定跌幅（如 10%、20%），按比例估算组合亏损。

## 情景 B：估值修复到历史中位数
AI 自动计算各指数从当前分位修复到历史中位数所需的涨幅。

## 情景 C：估值修复到机会值
AI 自动计算各指数从当前分位修复到机会值（历史 20% 分位）所需的涨幅。

# 输出结构

```markdown
## 情景推演结果

### 当前情景：{情景描述}

### 组合影响概览
| 指标 | 数值 |
|------|------|
| 预估收益/亏损 | {金额}（{百分比}） |
| 受影响最大基金 | {基金名称} |

### 分基金影响
| 基金 | 当前市值 | 预估变动 | 变动后市值 | 占比变化 |
|------|----------|----------|------------|----------|
| {名称} | {金额} | {金额}（{百分比}） | {金额} | {当前%} → {新%} |

### 应对建议
{基于情景的建议}
```

# 负面约束
- 明确说明这是情景模拟，不是预测
- 不保证实际结果与模拟一致
- 不因模拟结果给出紧急操作建议
"""

DEFAULT_HOTSPOTS_PROMPT = """你是一位专业的A股市场分析专家。请基于以下市场数据，分析今日投资机会，输出结构化JSON。

## 输出格式
返回严格JSON（不要包含其他内容）：
{
  "summary": "一句话概括今日核心观点（20字内）",
  "recommendations": [
    {
      "direction": "up",
      "index_name": "中证白酒",
      "index_code": "399997.SZ",
      "reason": "市净率百分位0%历史大底，消费复苏预期下修复弹性充足",
      "confidence": "medium",
      "user_portfolio": "can_add"
    }
  ]
}

## 字段说明
- direction: up=关注/买入 | down=回避/减仓 | watch=观察等待
- index_name: 指数名称，必须具体（如"中证白酒"而非"白酒板块"）
- index_code: 严格匹配【可参考指数代码】中的代码，找不到填空字符串
- reason: 20-40字，必须包含具体数据支撑（百分位/涨跌幅/估值数值等）
- confidence: 保守取值，high=明确信号(百分位<10%+政策利好) | medium=有逻辑支撑 | low=不确定
- user_portfolio: 该指数与您持仓的关系 — 'already_have'=已持有 | 'can_add'=可加仓(持有且低估) | 'new'=新买入机会 | 'reduce'=应减仓(持有且高估)

## 分析要求
1. 推荐 4-6 条，每条约 20-40 字理由 + 具体数据支撑
2. **核心逻辑：以【今日新闻】为线索，结合估值和持仓，发现新闻驱动的投资机会或风险**
3. direction=up 的推荐必须有明确的新闻/政策/事件催化逻辑，不能仅因百分位低就推荐
4. direction=down 必须有明确风险信号（过热/政策风险/资金流出/涨幅过大）
5. 推荐应体现差异化分析：
   - 新闻利好+估值合理/低估 → 强烈关注（confidence=high/medium）
   - 新闻利空+估值偏高 → 回避/减仓（direction=down）
   - 持仓中与新闻高度相关的 → 给出持有/调仓建议
   - 避免所有推荐都来自同一板块或同一逻辑
6. 结合【持仓明细】分析：高仓位+高估→减仓建议；持有+低估→加仓机会；未持有+低估→新关注
7. 每条推荐必须包含 user_portfolio 字段，准确反映该指数与持仓的关系
8. 如果持仓中有占比过高(>20%)的单一资产，必须在 recommendations 中给予关注
9. 【低估值指数】仅供参考，不要重复低估值卡片已展示的内容，重点挖掘新闻带来的增量信息"""

DEFAULT_BOND_PROMPT = """# 角色
你是一位专业的债券配置顾问，专门帮个人投资者做债券基金配置。你需要结合宏观货币政策环境、债市温度趋势、收益率曲线、用户现有持仓穿透数据和基金排行榜，给出专业、具体、可执行的配置建议。

# 输入数据说明

你将收到以下数据段：

1. **债市温度历史** — 有知有行债市温度日度数据（含温度值和10Y收益率），据此分析趋势：温度在上升还是下降？当前处于什么区间？
2. **收益率曲线** — 各期限国债收益率，判断曲线形态（陡峭/平坦/倒挂）
3. **宏观货币政策环境** — 包含LPR利率（1Y/5Y）、存款准备金率(RRR)最新调整、SHIBOR各期限利率（隔夜/1周/1月/3月/6月/1年）、CPI数据。用于判断货币政策松紧和通胀/通缩环境。
4. **现有债券持仓（含穿透数据）** — 每只持仓基金的名称、代码、金额、占比，以及通过 akshare 查询的基金持仓穿透数据（是否持有股票、底层债券类型）
5. **零钱余额** — 可用资金
6. **全市场纯债基金排行榜** — akshare 债券型基金排行榜（按近1年收益排序），含基金代码、名称、近1年收益率、手续费

# 分析方法

## 1. 宏观政策环境判断（首要步骤）
从「宏观货币政策环境」中的数据综合判断：
- **LPR走势**：LPR处于历史低位 → 货币宽松，利好债市但收益空间有限；LPR处于高位 → 偏紧，债券收益率有吸引力
- **降准/升准**：近期降准 → 释放流动性，利好债市；近期升准 → 收紧流动性
- **SHIBOR趋势**：SHIBOR下行 → 资金面宽松，短债受益；SHIBOR上行 → 资金面趋紧，短债承压
- **CPI数据**：CPI为负/极低 → 通缩压力，央行有进一步宽松空间，利好债市；CPI偏高 → 通胀压力，央行可能收紧，利空债市
- 综合以上信号判断当前处于「宽松周期」「中性」还是「紧缩周期」

## 2. 持仓穿透分类（基于数据，而非名称猜测）
从「现有债券持仓（含穿透数据）」中的底层持仓数据来判断每只基金的真实类型：
- **含股票的基金**（持仓穿透中有股票仓位 > 0%）= 二级债基/偏债混合 → 不是纯债，有权益风险
- **仅持有国债/国开债/政金债的基金** = 利率债基金 → 久期看持仓债券的剩余期限
- **持有信用债/中票/短融的基金** = 信用债基金
- **持仓以剩余期限<1年债券为主的基金** = 短债基金
- **持仓以剩余期限>5年债券为主的基金** = 长债基金

## 3. 债市趋势判断（基于历史数据，而非单点）
从「债市温度历史」分析：
- 近30天温度趋势：是持续上升、下降还是震荡？
- 温度上升=债市变热=利率下降=债券价格上涨→现在买性价比降低
- 温度下降=债市变冷=利率上升=债券价格下跌→现在买可以锁定高利率
- 结合收益率曲线判断：短端和长端利率的相对位置

## 4. 配置建议原则（金额分配核心规则）
**金额分配必须遵守以下规则，不得建议"全部买入"：**
- 推荐总金额不超过零钱余额的 **80%**，必须保留 20% 作为流动性储备
- 根据债市温度分档配置比例（占零钱余额）：
  - 温度 < 30°（冷）且趋势向下 = 可配 60-80%
  - 温度 30-50°（偏冷）= 可配 50-60%
  - 温度 50-70°（适中）= 可配 30-50%
  - 温度 > 70°（热）= 只配 15-30%（货币基金/同业存单为主）
- 结合宏观政策修正：
  - 宽松周期（降准降息中）→ 可在上述基础上 +10%
  - 紧缩周期（升准加息中）→ 可在上述基础上 -10%
- **单只基金不超过零钱的 30%**（分散风险）
- 推荐 **2-4 只基金**，分不同类型搭配（如 1 只短债 + 1 只中长期纯债 + 1 只货币基金）
- amount 字段必须是具体数字（元），不能是 0 或 null

## 5. 推荐基金选择
从「全市场纯债基金排行榜」中按以下标准挑选：
- 纯债基金（非二级债基），优先短债/中短债
- 近1年收益率稳定，不要大起大落
- 费率低
- 基金规模适中（不要太小有清盘风险）
- 同一基金公司最多推荐1只

# 输出格式
返回严格JSON格式（不要包含其他内容）：
{
  "summary": "一句话结论（20字内）",
  "policy_analysis": "宏观政策环境判断：当前货币政策周期、LPR/SHIBOR水平、通胀环境及对债市的影响（60字内）",
  "trend_analysis": "债市温度趋势判断和当前位置分析（50字内）",
  "current_bond_analysis": "对用户现有债券持仓的真实类型穿透分析（50字内）",
  "market_assessment": "当前债市配置方向建议（30字内）",
  "recommendations": [
    {
      "fund_code": "基金代码",
      "fund_name": "基金名称",
      "fund_type": "短债基金/中长期纯债/同业存单基金/货币基金",
      "reason": "推荐理由（15-30字，必须包含数据支撑）",
      "amount": 建议金额（必须为正整数）,
      "amount_desc": "占零钱比例描述"
    }
  ],
  "note": "风险提示（20字内）"
}"""

DEFAULT_INDEX_DEEP_ANALYSIS_PROMPT = """# 角色
你是一位专业的指数投资分析师，专门对单个指数进行深度分析。你需要结合估值数据（含历史趋势）、知识库中的历史分析和文章观点、以及该指数相关的最新政策和新闻，给出全面、专业的投资分析报告。

# 输入数据说明

你将收到以下数据段：

1. **指数估值数据** — 当前估值（PE、PB、股息率、百分位等）+ 近60天估值历史趋势
2. **知识库检索结果** — 从知识库中检索到的与该指数相关的历史分析、文章观点、估值记录（通过 RAG 检索）
3. **用户持仓数据**（`<user_portfolio>` 标签）— 用户当前持有该指数相关基金的详细信息（基金名称、代码、市值、盈亏、占比）
4. **指数相关新闻** — 该指数相关的最新新闻、政策动态、市场消息

# ⚠️ 重要规则

1. **必须先检查 `<user_portfolio>` 标签是否存在**。如果有该标签，说明用户持有相关基金，你必须在分析中引用这些持仓数据。
2. **绝对不能说"用户未持仓"**，除非 `<user_portfolio>` 标签确实不存在。
3. **投资建议必须基于实际持仓状态**：有持仓就给出持有/加仓/减仓建议，无持仓才给建仓/观望建议。
4. **知识库内容可能包含不同时间的数据**，优先参考最新内容，忽略超过6个月的旧数据。

# 分析方法

## 1. 估值水平判断
- 当前估值在历史中的位置（百分位），以及 Z-Score 偏离程度
- 近60天估值趋势：是持续走低（机会）、还是持续走高（风险）、还是震荡
- 结合 PE/PB/股息率 综合判断，注意不同行业的估值中枢差异很大（银行PE低不代表低估，科技PE高不代表高估）
- 参考知识库中的历史估值记录，对比当前值与历史区间的相对位置
- 给出定性判断（极低估/偏低/合理/偏高/极高估），不要编造具体的百分位阈值标准

## 2. 知识库观点整合
- 从知识库检索结果中提取与该指数相关的历史分析结论
- 引用重要的文章观点和分析逻辑（必须注明数据时间，让读者判断时效性）
- 如果知识库中有估值记录，对比历史判断和当前情况
- 对超过6个月的旧数据要标注"历史参考"，不作为当前判断的主要依据

## 3. 政策与新闻分析
- 分析最新政策对该指数/行业的影响（利好/利空/中性）
- 识别新闻中的关键事件和催化剂
- 判断短期（1-3月）和中长期（6-12月）影响

## 4. 持仓关联分析（必须执行）
首先检查输入中是否有 `<user_portfolio>` 标签：
- **如果有**：必须详细分析用户的持仓情况：
  - 持有哪些基金？市值多少？占总资产比例？
  - 当前是浮盈还是浮亏？盈亏比例多少？
  - 结合当前估值水平给出具体操作建议（加仓/持有/减仓/止盈）
  - 估值偏低 + 浮亏 → 可考虑分批补仓摊低成本
  - 估值偏高 + 浮盈 → 可考虑分批止盈锁定收益
- **如果没有**：说明"当前未持有该指数相关基金"，然后给出是否适合建仓的建议

## 5. 投资建议（必须结合持仓状态）
基于估值水平、政策环境、知识库观点和持仓状态，给出综合判断：
- 明确的操作建议（建仓/持有/加仓/减仓/止盈/观望）
- 必须区分已持仓和未持仓两种情况分别给建议
- 说明建议的核心逻辑（为什么这个估值水平对应这个操作）
- 列出主要风险点

# 输出格式
返回结构化分析报告（使用 Markdown 格式），包含以下章节：

## {指数名称} 深度分析

### 估值分析
（当前估值水平、历史百分位、趋势判断，给出量化评级）

### 知识库观点
（引用知识库中的相关分析和文章观点，注明数据时间）

### 政策与新闻
（最新政策动态和新闻事件的影响分析）

### 我的持仓
（**必须根据 `<user_portfolio>` 标签内容如实列出**，包括：基金名称、市值、盈亏、占比。如无持仓数据则说明"当前未持有"）

### 投资建议
**建议操作：{强烈建仓/建仓/持有/观望/减仓/止盈}**
- **当前持仓状态**：{持有 X 只基金，总市值 ¥X，浮盈/浮亏 X%} 或 {未持仓}
- **具体建议**：根据估值和持仓状态给出的操作建议
- **风险提示**：主要风险因素

---
*分析基于估值数据、知识库检索、实时新闻和个人持仓，仅供参考，不构成投资建议。*
"""


def list_analysis_agents() -> list[dict]:
    """列出所有分析 Agent。"""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM analysis_agents ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_analysis_agent(agent_id: int) -> dict | None:
    """获取单个分析 Agent。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM analysis_agents WHERE id = ?", (agent_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_analysis_agent(agent_id: int, **kwargs) -> bool:
    """更新分析 Agent 配置。"""
    fields = []
    values = []
    for k in ("name", "description", "system_prompt", "is_active"):
        if k in kwargs:
            fields.append(f"{k} = ?")
            values.append(kwargs[k])
    if not fields:
        return False
    fields.append("updated_at = datetime('now','localtime')")
    values.append(agent_id)
    conn = _get_conn()
    conn.execute(f"UPDATE analysis_agents SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return True


def create_analysis_history(index_code: str, index_name: str, agent_id: int,
                            agent_name: str, prompt_used: str, news_context: str,
                            valuation_context: str, result: str, token_usage: int = 0) -> int:
    """保存分析历史记录。"""
    conn = _get_conn()
    cursor = conn.execute("""
        INSERT INTO analysis_history
        (index_code, index_name, agent_id, agent_name, prompt_used, news_context,
         valuation_context, result, token_usage, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))
    """, (index_code, index_name, agent_id, agent_name, prompt_used,
          news_context, valuation_context, result, token_usage))
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


def list_analysis_history(index_code: str = None, limit: int = 50) -> list[dict]:
    """列出分析历史。"""
    conn = _get_conn()
    if index_code:
        rows = conn.execute(
            "SELECT * FROM analysis_history WHERE index_code = ? ORDER BY created_at DESC LIMIT ?",
            (index_code, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM analysis_history ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_analysis_history_item(history_id: int) -> dict | None:
    """获取单条分析历史。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM analysis_history WHERE id = ?", (history_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_analysis_history(history_id: int) -> bool:
    """删除分析历史。"""
    conn = _get_conn()
    conn.execute("DELETE FROM analysis_history WHERE id = ?", (history_id,))
    conn.commit()
    conn.close()
    return True


# ── 风险预警 CRUD ──────────────────────────────────────


def create_alert(alert_type: str, title: str, content: str = None,
                 severity: str = "info", related_fund_code: str = None,
                 related_fund_name: str = None, source: str = None,
                 user_id: str = "default") -> int:
    """新增风险预警，返回 alert_id。"""
    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO portfolio_alerts
            (user_id, alert_type, severity, title, content,
             related_fund_code, related_fund_name, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, alert_type, severity, title, content,
          related_fund_code, related_fund_name, source))
    alert_id = cur.lastrowid
    conn.commit()
    conn.close()
    return alert_id


def list_alerts(user_id: str = "default", limit: int = 50,
                unread_only: bool = False) -> list[dict]:
    """获取预警列表，按时间倒序。"""
    conn = _get_conn()
    if unread_only:
        rows = conn.execute("""
            SELECT * FROM portfolio_alerts
            WHERE user_id = ? AND is_read = 0
            ORDER BY created_at DESC LIMIT ?
        """, (user_id, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM portfolio_alerts
            WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ?
        """, (user_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_unread_alert_count(user_id: str = "default") -> int:
    """获取未读预警数量。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM portfolio_alerts WHERE user_id = ? AND is_read = 0",
        (user_id,)
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0


def mark_alert_read(alert_id: int) -> bool:
    """标记预警为已读。"""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE portfolio_alerts SET is_read = 1 WHERE id = ?", (alert_id,)
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def delete_alert(alert_id: int) -> bool:
    """删除预警。"""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM portfolio_alerts WHERE id = ?", (alert_id,))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


# ── 交易标签 CRUD ──────────────────────────────────────


def add_transaction_tag(transaction_id: int, tag: str) -> int:
    """给交易记录添加标签，返回 tag_id。"""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO transaction_tags (transaction_id, tag) VALUES (?, ?)",
        (transaction_id, tag)
    )
    conn.commit()
    tag_id = cur.lastrowid
    conn.close()
    return tag_id


def remove_transaction_tag(transaction_id: int, tag: str) -> bool:
    """移除交易记录的指定标签。"""
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM transaction_tags WHERE transaction_id = ? AND tag = ?",
        (transaction_id, tag)
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def get_transaction_tags(transaction_id: int) -> list[str]:
    """获取交易记录的所有标签。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT tag FROM transaction_tags WHERE transaction_id = ?",
        (transaction_id,)
    ).fetchall()
    conn.close()
    return [r["tag"] for r in rows]


# ── 持仓分析辅助函数 ──────────────────────────────────


def get_portfolio_diversification(user_id: str = "default") -> dict:
    """分析持仓分散度：基金数量、指数分布、类型分布。排除已清仓记录。"""
    holdings = list_holdings(user_id)
    active = [h for h in holdings if (h.get("shares") or 0) > 0]

    total_value = sum(h.get("current_value", 0) or 0 for h in active)
    total_cost = sum(h.get("total_cost", 0) or 0 for h in active)

    # 指数分布
    index_dist = {}
    for h in active:
        idx = h.get("index_name") or "未知"
        val = h.get("current_value", 0) or 0
        index_dist[idx] = index_dist.get(idx, 0) + val

    # 基金类型分布（通过 fund_code 前几位判断）
    # 股票型/混合型/债券型/指数型/货币型
    type_dist = {"股票型": 0, "混合型": 0, "债券型": 0, "指数型": 0, "货币型": 0, "其他": 0}
    for h in holdings:
        code = h.get("fund_code", "")
        val = h.get("current_value", 0) or 0
        # 简单分类：以 fund_name 或 index_name 判断
        name = (h.get("fund_name", "") or "") + (h.get("index_name", "") or "")
        if "指数" in name or "ETF" in name or "ETF联接" in name:
            type_dist["指数型"] = type_dist.get("指数型", 0) + val
        elif "债" in name or "纯债" in name or "信用债" in name:
            type_dist["债券型"] = type_dist.get("债券型", 0) + val
        elif "货" in name or "货币" in name:
            type_dist["货币型"] = type_dist.get("货币型", 0) + val
        elif "混合" in name or "灵活" in name:
            type_dist["混合型"] = type_dist.get("混合型", 0) + val
        elif "股" in name or "股票" in name:
            type_dist["股票型"] = type_dist.get("股票型", 0) + val
        else:
            type_dist["其他"] = type_dist.get("其他", 0) + val

    # 仓位集中度：最大持仓占比
    max_holding_pct = 0
    if total_value > 0:
        max_value = max((h.get("current_value", 0) or 0) for h in holdings)
        max_holding_pct = round(max_value / total_value * 100, 2)

    return {
        "holding_count": len(holdings),
        "total_cost": round(total_cost, 2),
        "total_value": round(total_value, 2),
        "max_holding_pct": max_holding_pct,
        "index_distribution": {k: round(v, 2) for k, v in sorted(index_dist.items(), key=lambda x: -x[1])},
        "type_distribution": {k: round(v, 2) for k, v in type_dist.items() if v > 0},
    }


def get_transaction_summary(user_id: str = "default") -> dict:
    """分析交易行为汇总，含最近 50 笔交易明细。不包含系统交易的统计数据。"""
    conn = _get_conn()

    # 买入统计（排除系统交易）
    buy_rows = conn.execute("""
        SELECT COUNT(*) as tx_count, SUM(amount) as total_amount
        FROM portfolio_transactions
        WHERE user_id = ? AND transaction_type = 'buy' AND (status IN ('confirmed', 'settled') OR status IS NULL)
            AND (is_system IS NULL OR is_system = 0)
    """, (user_id,)).fetchall()
    buy_count = buy_rows[0]["tx_count"] if buy_rows else 0
    buy_total = buy_rows[0]["total_amount"] or 0 if buy_rows else 0

    # 卖出统计（排除系统交易）
    sell_rows = conn.execute("""
        SELECT COUNT(*) as tx_count, SUM(amount) as total_amount
        FROM portfolio_transactions
        WHERE user_id = ? AND transaction_type = 'sell' AND (status IN ('confirmed', 'settled') OR status IS NULL)
            AND (is_system IS NULL OR is_system = 0)
    """, (user_id,)).fetchall()
    sell_count = sell_rows[0]["tx_count"] if sell_rows else 0
    sell_total = sell_rows[0]["total_amount"] or 0 if sell_rows else 0

    # 最近交易明细（含基金名称）
    recent = conn.execute("""
        SELECT t.id, t.fund_code, t.transaction_type, t.shares, t.price, t.amount,
               t.transaction_date, t.status, t.is_system,
               COALESCE(h.fund_name, '') as fund_name,
               COALESCE(h.index_name, '') as index_name
        FROM portfolio_transactions t
        LEFT JOIN portfolio_holdings h ON t.holding_id = h.id
        WHERE t.user_id = ? AND (t.is_system IS NULL OR t.is_system = 0)
        ORDER BY t.id DESC
        LIMIT 50
    """, (user_id,)).fetchall()

    conn.close()

    return {
        "buy_count": buy_count,
        "buy_total": round(buy_total, 2),
        "sell_count": sell_count,
        "sell_total": round(sell_total, 2),
        "total_tx_count": buy_count + sell_count,
        "net_investment": round(buy_total - sell_total, 2),
        "recent_transactions": [dict(r) for r in recent],
    }


def clear_all_portfolio_data(user_id: str = "default"):
    """删除用户的所有持仓、交易记录、预警和标签。"""
    conn = _get_conn()
    conn.execute("DELETE FROM portfolio_alerts WHERE user_id = ?", (user_id,))
    conn.execute("""
        DELETE FROM transaction_tags WHERE transaction_id IN
        (SELECT id FROM portfolio_transactions WHERE user_id = ?)
    """, (user_id,))
    conn.execute("DELETE FROM portfolio_transactions WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM portfolio_holdings WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM portfolio_analysis_records WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM portfolio_cash WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def create_portfolio_analysis_record(analysis_type: str, summary: str,
                                     input_data: str, result_data: str,
                                     token_usage: int = 0,
                                     user_id: str = "default",
                                     agent_id: int = None) -> int:
    """保存持仓分析记录。"""
    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO portfolio_analysis_records
            (user_id, analysis_type, summary, input_data, result_data, token_usage, agent_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user_id, analysis_type, summary, input_data, result_data, token_usage, agent_id))
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def list_portfolio_analysis_records(analysis_type: str = None,
                                    limit: int = 20,
                                    user_id: str = "default") -> list[dict]:
    """列出持仓分析记录。"""
    conn = _get_conn()
    if analysis_type:
        rows = conn.execute("""
            SELECT id, user_id, analysis_type, summary, result_data, token_usage, created_at
            FROM portfolio_analysis_records
            WHERE user_id = ? AND analysis_type = ?
            ORDER BY created_at DESC LIMIT ?
        """, (user_id, analysis_type, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT id, user_id, analysis_type, summary, result_data, token_usage, created_at
            FROM portfolio_analysis_records
            WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ?
        """, (user_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_portfolio_analysis_record(record_id: int) -> dict | None:
    """获取单条持仓分析记录详情。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM portfolio_analysis_records WHERE id = ?",
        (record_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_analysis_feedback(record_id: int, feedback: str, note: str = "") -> bool:
    """提交用户对分析结果的反馈（helpful/unhelpful）。"""
    conn = _get_conn()
    conn.execute(
        "UPDATE portfolio_analysis_records SET feedback = ?, feedback_note = ? WHERE id = ?",
        (feedback, note, record_id)
    )
    conn.commit()
    affected = conn.total_changes
    conn.close()
    return affected > 0


def list_bad_cases(analysis_type: str = None, limit: int = 50) -> list[dict]:
    """列出被标记为 unhelpful 的分析记录（Bad Cases）。"""
    conn = _get_conn()
    if analysis_type:
        rows = conn.execute("""
            SELECT id, analysis_type, summary, input_data, result_data, feedback_note,
                   token_usage, agent_id, created_at
            FROM portfolio_analysis_records
            WHERE feedback = 'unhelpful' AND analysis_type = ?
            ORDER BY created_at DESC LIMIT ?
        """, (analysis_type, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT id, analysis_type, summary, input_data, result_data, feedback_note,
                   token_usage, agent_id, created_at
            FROM portfolio_analysis_records
            WHERE feedback = 'unhelpful'
            ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_all_bad_cases(source: str = None, limit: int = 100) -> list[dict]:
    """统一查询所有 Bad Case（分析记录 + LLM 反馈）。

    参数:
        source: 'analysis' 只查分析记录, 'chat' 只查 LLM 反馈, None 查全部
        limit: 每个来源的最大条数
    返回:
        统一结构的 bad case 列表，每条包含 source, id, type, summary, input, output, note, metadata, created_at
    """
    conn = _get_conn()
    results = []

    if source != 'chat':
        # 来源 A: portfolio_analysis_records
        rows = conn.execute("""
            SELECT id, analysis_type, summary, input_data, result_data, feedback_note,
                   token_usage, agent_id, created_at
            FROM portfolio_analysis_records
            WHERE feedback = 'unhelpful'
            ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()
        for r in rows:
            d = dict(r)
            results.append({
                'source': 'analysis',
                'id': d['id'],
                'type': d.get('analysis_type', ''),
                'summary': d.get('summary', ''),
                'input': d.get('input_data', ''),
                'output': d.get('result_data', ''),
                'note': d.get('feedback_note', ''),
                'metadata': {'token_usage': d.get('token_usage'), 'agent_id': d.get('agent_id')},
                'created_at': d.get('created_at', ''),
            })

    if source != 'analysis':
        # 来源 B: llm_feedback (chat / specialist 等)
        rows = conn.execute("""
            SELECT id, caller, input_summary, output_summary, rating, tags, comment, created_at
            FROM llm_feedback
            WHERE rating = 'unhelpful'
            ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()
        for r in rows:
            d = dict(r)
            results.append({
                'source': 'chat',
                'id': d['id'],
                'type': d.get('caller', ''),
                'summary': d.get('output_summary', ''),
                'input': d.get('input_summary', ''),
                'output': d.get('output_summary', ''),
                'note': d.get('comment', ''),
                'metadata': {'tags': d.get('tags', ''), 'caller': d.get('caller', '')},
                'created_at': d.get('created_at', ''),
            })

    conn.close()
    # 按时间倒序排列
    results.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    return results[:limit]


def delete_portfolio_analysis_record(record_id: int) -> bool:
    """删除持仓分析记录。"""
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM portfolio_analysis_records WHERE id = ?", (record_id,)
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


# ── 评测集 (Eval Suite) ──────────────────────────────────────


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


def save_analysis_cache(cache_key: str, data: dict) -> bool:
    """保存分析结果缓存（幂等 upsert）。"""
    import json
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO analysis_cache (cache_key, data, created_at) VALUES (?, ?, datetime('now','localtime'))",
        (cache_key, json.dumps(data, ensure_ascii=False))
    )
    conn.commit()
    conn.close()
    return True


def get_analysis_cache(cache_key: str) -> dict | None:
    """读取分析结果缓存。"""
    import json
    conn = _get_conn()
    row = conn.execute(
        "SELECT data FROM analysis_cache WHERE cache_key = ?", (cache_key,)
    ).fetchone()
    conn.close()
    if row:
        try:
            return json.loads(row["data"])
        except Exception:
            return None
    return None


def get_cached_fund_holdings(fund_code: str) -> dict:
    """获取基金持仓（24h 缓存）。"""
    cache_key = f"fund_holdings_{fund_code}"
    cached = get_analysis_cache(cache_key)
    if cached:
        return cached
    data = get_fund_holdings(fund_code)
    save_analysis_cache(cache_key, data)
    return data


def get_portfolio_penetration(user_id: str = "default") -> dict:
    """跨基金加权聚合底层股票持仓，计算持仓穿透。"""
    cache_key = f"portfolio_penetration_{user_id}"
    cached = get_analysis_cache(cache_key)
    if cached:
        return cached

    holdings = list_holdings(user_id)
    holdings = [h for h in holdings if (h.get("shares") or 0) > 0 and (h.get("current_value") or 0) > 0]
    if not holdings:
        return {"top_stocks": [], "overlap_matrix": {"fund_names": [], "matrix": []},
                "total_portfolio_value": 0, "fund_count": 0, "cached_at": None}

    total_value = sum(h["current_value"] for h in holdings)

    fund_stock_map = {}
    for h in holdings:
        fund_code = h["fund_code"]
        fund_name = h["fund_name"]
        fund_value = h["current_value"]
        try:
            fh = get_cached_fund_holdings(fund_code)
            stocks = fh.get("top_stocks", [])
            fund_stock_map[fund_code] = {"name": fund_name, "value": fund_value, "stocks": stocks}
        except Exception as e:
            print(f"[db] 获取基金持仓失败 {fund_code}: {e}")
            fund_stock_map[fund_code] = {"name": fund_name, "value": fund_value, "stocks": []}

    stock_agg = {}
    for fund_code, info in fund_stock_map.items():
        fund_weight = info["value"] / total_value * 100
        for s in info["stocks"]:
            sc = s["stock_code"]
            sn = s["stock_name"]
            contribution = (s["pct_nav"] / 100) * fund_weight
            if sc not in stock_agg:
                stock_agg[sc] = {"stock_code": sc, "stock_name": sn, "total_weight_pct": 0, "held_in_funds": []}
            stock_agg[sc]["total_weight_pct"] += contribution
            stock_agg[sc]["held_in_funds"].append({
                "fund_name": info["name"],
                "contribution_pct": round(contribution, 2),
            })

    top_stocks = sorted(stock_agg.values(), key=lambda x: x["total_weight_pct"], reverse=True)[:15]
    for ts in top_stocks:
        ts["total_weight_pct"] = round(ts["total_weight_pct"], 2)
        ts["held_in_funds"].sort(key=lambda x: x["contribution_pct"], reverse=True)

    fund_codes = list(fund_stock_map.keys())
    fund_names = [fund_stock_map[fc]["name"] for fc in fund_codes]
    n = len(fund_codes)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        stocks_i = set(s["stock_code"] for s in fund_stock_map[fund_codes[i]]["stocks"])
        for j in range(n):
            if i == j:
                matrix[i][j] = 1.0
            else:
                stocks_j = set(s["stock_code"] for s in fund_stock_map[fund_codes[j]]["stocks"])
                if stocks_i and stocks_j:
                    overlap = len(stocks_i & stocks_j)
                    matrix[i][j] = round(overlap / min(len(stocks_i), len(stocks_j)), 2) if min(len(stocks_i), len(stocks_j)) > 0 else 0

    from datetime import datetime
    result = {
        "top_stocks": top_stocks,
        "overlap_matrix": {"fund_names": fund_names, "matrix": matrix},
        "total_portfolio_value": round(total_value, 2),
        "fund_count": len(holdings),
        "cached_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    save_analysis_cache(cache_key, result)
    return result


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


# ── Token 用量查询 ──────────────────────────────────────


def list_token_usage(days: int = 7, limit: int = 50, offset: int = 0) -> list[dict]:
    """最近 LLM 调用记录明细（支持分页）。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT id, model, caller, prompt_tokens, completion_tokens, total_tokens, created_at
        FROM token_usage
        WHERE created_at >= datetime('now', ?)
        ORDER BY id DESC LIMIT ? OFFSET ?
    """, (f"-{days} days", limit, offset)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_token_usage(days: int = 7) -> int:
    """统计记录总数（用于分页）。"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT COUNT(*) as cnt
        FROM token_usage
        WHERE created_at >= datetime('now', ?)
    """, (f"-{days} days",)).fetchone()
    conn.close()
    return row[0] if row else 0


def get_today_token_total() -> int:
    """返回今日 token 总用量，用于预算检查。"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT COALESCE(SUM(total_tokens), 0) as total
        FROM token_usage
        WHERE date(created_at) = date('now', 'localtime')
    """).fetchone()
    conn.close()
    return row[0] if row else 0


def get_token_usage_summary(days: int = 30) -> dict:
    """汇总统计。"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT
            COUNT(*) as total_calls,
            COALESCE(SUM(prompt_tokens), 0) as total_prompt,
            COALESCE(SUM(completion_tokens), 0) as total_completion,
            COALESCE(SUM(total_tokens), 0) as total_tokens,
            CASE WHEN COUNT(*) > 0 THEN COALESCE(SUM(total_tokens), 0) / COUNT(*) ELSE 0 END as avg_per_call
        FROM token_usage
        WHERE created_at >= datetime('now', ?)
    """, (f"-{days} days",)).fetchone()
    conn.close()
    base = dict(row)

    conn = _get_conn()
    today = conn.execute("""
        SELECT
            COUNT(*) as calls,
            COALESCE(SUM(prompt_tokens), 0) as prompt,
            COALESCE(SUM(completion_tokens), 0) as completion,
            COALESCE(SUM(total_tokens), 0) as total
        FROM token_usage
        WHERE date(created_at) = date('now', 'localtime')
    """).fetchone()
    conn.close()

    return {
        "total_calls": base["total_calls"],
        "total_prompt": base["total_prompt"],
        "total_completion": base["total_completion"],
        "total_tokens": base["total_tokens"],
        "avg_per_call": base["avg_per_call"],
        "today": dict(today),
    }


def get_token_budget_info() -> dict:
    """获取今日 token 预算使用情况。"""
    from config import DAILY_TOKEN_LIMIT, TOKEN_WARN_THRESHOLD, TOKEN_BUDGET_BYPASS
    used = get_today_token_total()
    limit = DAILY_TOKEN_LIMIT
    pct = (used / limit * 100) if limit > 0 else 0
    if TOKEN_BUDGET_BYPASS:
        mode = "normal"
    elif pct >= 100:
        mode = "exceeded"
    elif pct >= TOKEN_WARN_THRESHOLD * 100:
        mode = "warning"
    else:
        mode = "normal"
    return {"ok": True, "used": used, "limit": limit, "pct": round(pct, 1), "mode": mode}


def get_token_usage_by_caller(days: int = 7) -> list[dict]:
    """按 caller 分组统计。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            COALESCE(NULLIF(caller, ''), 'unknown') as caller,
            COUNT(*) as calls,
            COALESCE(SUM(prompt_tokens), 0) as prompt_tokens,
            COALESCE(SUM(completion_tokens), 0) as completion_tokens,
            COALESCE(SUM(total_tokens), 0) as total_tokens
        FROM token_usage
        WHERE created_at >= datetime('now', ?)
        GROUP BY caller
        ORDER BY total_tokens DESC
    """, (f"-{days} days",)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_token_usage_daily(days: int = 30) -> list[dict]:
    """按天统计 token 用量趋势。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            date(created_at) as day,
            COUNT(*) as calls,
            COALESCE(SUM(total_tokens), 0) as tokens
        FROM token_usage
        WHERE created_at >= datetime('now', ?)
        GROUP BY date(created_at)
        ORDER BY day ASC
    """, (f"-{days} days",)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 性能监控查询 ──────────────────────────────────────


def get_performance_stats(days: int = 7) -> dict:
    """获取 Agent 调用性能统计（平均耗时、最慢调用等）。"""
    conn = _get_conn()
    stats = conn.execute("""
        SELECT
            COUNT(*) as total_runs,
            COALESCE(AVG(duration_ms), 0) as avg_duration_ms,
            COALESCE(MAX(duration_ms), 0) as max_duration_ms,
            COUNT(CASE WHEN duration_ms > 30000 THEN 1 END) as slow_calls,
            COUNT(DISTINCT agent_key) as unique_agents
        FROM agent_runs
        WHERE created_at >= datetime('now', ?)
    """, (f"-{days} days",)).fetchone()
    conn.close()
    return dict(stats)


def get_performance_by_agent(days: int = 7) -> list[dict]:
    """按 Agent 分组统计性能。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            COALESCE(NULLIF(agent_key, ''), 'unknown') as agent_key,
            COALESCE(NULLIF(agent_name, ''), '未知') as agent_name,
            COUNT(*) as runs,
            COALESCE(AVG(duration_ms), 0) as avg_duration_ms,
            COALESCE(MAX(duration_ms), 0) as max_duration_ms,
            COALESCE(SUM(CASE WHEN duration_ms > 30000 THEN 1 ELSE 0 END), 0) as slow_calls
        FROM agent_runs
        WHERE created_at >= datetime('now', ?)
        GROUP BY agent_key
        ORDER BY avg_duration_ms DESC
    """, (f"-{days} days",)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── 推荐验证系统 ──────────────────────────────────────


def save_recommendations(recommendations: list[dict], analysis_id: str = None,
                         baselines: list[dict] = None) -> list[int]:
    """批量保存推荐记录。baselines 可选，每项 {"price": float, "date": str}。"""
    ids = []
    conn = _get_conn()
    for i, rec in enumerate(recommendations):
        bl = baselines[i] if baselines and i < len(baselines) else None
        bl_price = bl.get("price") if bl else None
        bl_date = bl.get("date") if bl else None
        cur = conn.execute(
            "INSERT INTO recommendations "
            "(analysis_id, index_name, index_code, direction, reason, confidence, baseline_value, baseline_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                analysis_id,
                rec.get("index_name", ""),
                rec.get("index_code", ""),
                rec.get("direction", ""),
                rec.get("reason", ""),
                rec.get("confidence", ""),
                bl_price,
                bl_date,
            )
        )
        ids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    return ids


def list_recommendations(limit: int = 50, status: str = None) -> list[dict]:
    """列出推荐记录。"""
    conn = _get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM recommendations WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM recommendations ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def verify_recommendation(rec_id: int, current_value: float, current_date: str) -> dict:
    """验证单条推荐。"""
    conn = _get_conn()
    rec = conn.execute("SELECT * FROM recommendations WHERE id = ?", (rec_id,)).fetchone()
    if not rec:
        conn.close()
        return {"ok": False, "error": "not found"}
    rec = dict(rec)

    baseline = rec["baseline_value"]
    if not baseline:
        conn.execute(
            "UPDATE recommendations SET baseline_value = ?, baseline_date = ?, current_value = ?, current_date = ? WHERE id = ?",
            (current_value, current_date, current_value, current_date, rec_id)
        )
        conn.commit()
        conn.close()
        return {"ok": True, "status": "pending", "message": "基线已记录，等待后续验证"}

    change_pct = (current_value - baseline) / baseline * 100 if baseline else 0
    direction = rec["direction"]

    if direction == "up":
        correct = change_pct > 0
    elif direction == "down":
        correct = change_pct < 0
    else:
        correct = None

    status = "correct" if correct is True else ("wrong" if correct is False else "pending")
    conn.execute(
        "UPDATE recommendations SET current_value = ?, current_date = ?, change_pct = ?, "
        "status = ?, verified_at = datetime('now','localtime') WHERE id = ?",
        (current_value, current_date, round(change_pct, 2), status, rec_id)
    )
    conn.commit()
    conn.close()
    return {"ok": True, "status": status, "change_pct": round(change_pct, 2)}


def auto_verify_pending_recommendations(price_map: dict, verify_date: str) -> list[dict]:
    """批量验证 pending 推荐。price_map: {index_code: current_price}。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM recommendations WHERE status = 'pending' AND baseline_value IS NOT NULL"
    ).fetchall()
    results = []
    for row in rows:
        rec = dict(row)
        code = rec["index_code"]
        current_price = price_map.get(code)
        if current_price is None:
            continue
        baseline = rec["baseline_value"]
        change_pct = (current_price - baseline) / baseline * 100 if baseline else 0
        direction = rec["direction"]
        if direction == "up":
            correct = change_pct > 0
        elif direction == "down":
            correct = change_pct < 0
        else:
            continue  # watch 不自动验证
        status = "correct" if correct else "wrong"
        conn.execute(
            "UPDATE recommendations SET current_value = ?, current_date = ?, change_pct = ?, "
            "status = ?, verified_at = datetime('now','localtime') WHERE id = ?",
            (current_price, verify_date, round(change_pct, 2), status, rec["id"])
        )
        results.append({"id": rec["id"], "index_name": rec["index_name"], "status": status, "change_pct": round(change_pct, 2)})
    conn.commit()
    conn.close()
    return results


# ── 推荐反馈 / 进化系统 ────────────────────────────────────


def save_recommendation_feedback(recommendation_id: int, rating: str = "neutral",
                                  tags: str = "", comment: str = "") -> int:
    """保存推荐反馈（点赞/点踩）。"""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO recommendation_feedback (recommendation_id, rating, tags, comment) VALUES (?, ?, ?, ?)",
        (recommendation_id, rating, tags, comment)
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def list_recommendation_feedback(recommendation_id: int = None, limit: int = 50) -> list[dict]:
    """列出推荐反馈。"""
    conn = _get_conn()
    if recommendation_id:
        rows = conn.execute(
            "SELECT * FROM recommendation_feedback WHERE recommendation_id = ? ORDER BY id DESC LIMIT ?",
            (recommendation_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM recommendation_feedback ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recommendation_feedback_stats() -> dict:
    """获取推荐反馈统计。"""
    conn = _get_conn()
    stats = conn.execute("""
        SELECT
            COUNT(*) as total_feedback,
            COALESCE(SUM(CASE WHEN rating='helpful' THEN 1 ELSE 0 END), 0) as helpful,
            COALESCE(SUM(CASE WHEN rating='unhelpful' THEN 1 ELSE 0 END), 0) as unhelpful,
            COALESCE(SUM(CASE WHEN rating='neutral' THEN 1 ELSE 0 END), 0) as neutral
        FROM recommendation_feedback
    """).fetchone()
    conn.close()
    return dict(stats)


def save_llm_feedback(caller: str, input_summary: str = "", output_summary: str = "",
                      rating: str = "neutral", tags: str = "", comment: str = "") -> int:
    """保存 LLM 输出反馈（进化系统核心）。"""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO llm_feedback (caller, input_summary, output_summary, rating, tags, comment) VALUES (?, ?, ?, ?, ?, ?)",
        (caller, input_summary, output_summary, rating, tags, comment)
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def list_llm_feedback(caller: str = None, rating: str = None, limit: int = 50) -> list[dict]:
    """列出 LLM 反馈。"""
    conn = _get_conn()
    conditions = []
    params = []
    if caller:
        conditions.append("caller = ?")
        params.append(caller)
    if rating:
        conditions.append("rating = ?")
        params.append(rating)
    where = " AND ".join(conditions) if conditions else "1=1"
    rows = conn.execute(f"""
        SELECT * FROM llm_feedback WHERE {where} ORDER BY id DESC LIMIT ?
    """, params + [limit]).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 用户画像 ──────────────────────────────────

def get_user_profile(user_id: str = "default") -> dict | None:
    """获取用户画像。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_user_profile(user_id: str = "default", **fields) -> bool:
    """更新用户画像字段。"""
    if not fields:
        return False
    conn = _get_conn()
    # 确保记录存在
    conn.execute("INSERT OR IGNORE INTO user_profiles (user_id) VALUES (?)", (user_id,))
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [user_id]
    conn.execute(f"UPDATE user_profiles SET {set_clause}, updated_at = datetime('now','localtime') WHERE user_id = ?", values)
    conn.commit()
    conn.close()
    return True


def increment_feedback_count(user_id: str = "default") -> int:
    """增加反馈计数，返回更新后的总数。"""
    conn = _get_conn()
    conn.execute("INSERT OR IGNORE INTO user_profiles (user_id) VALUES (?)", (user_id,))
    conn.execute("UPDATE user_profiles SET total_feedback_count = total_feedback_count + 1, updated_at = datetime('now','localtime') WHERE user_id = ?", (user_id,))
    row = conn.execute("SELECT total_feedback_count FROM user_profiles WHERE user_id = ?", (user_id,)).fetchone()
    conn.commit()
    conn.close()
    return row[0] if row else 0


# ── 对话摘要缓存 ──────────────────────────────────

def get_conversation_summary(conversation_id: int) -> dict | None:
    """获取对话的最新摘要。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM conversation_summaries WHERE conversation_id = ? ORDER BY id DESC LIMIT 1",
        (conversation_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_conversation_summary(conversation_id: int, up_to_message_id: int, summary: str) -> int:
    """保存对话摘要。"""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO conversation_summaries (conversation_id, up_to_message_id, summary) VALUES (?, ?, ?)",
        (conversation_id, up_to_message_id, summary)
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


# ── 债券知识库 ──────────────────────────────────

def seed_bond_knowledge():
    """将债券知识写入 skill_documents 表（如尚未写入）。"""
    import os
    conn = _get_conn()
    existing = conn.execute(
        "SELECT COUNT(*) FROM skill_documents WHERE doc_type = 'bond_knowledge'"
    ).fetchone()[0]
    if existing > 0:
        conn.close()
        return False
    md_path = os.path.join(os.path.dirname(__file__), "bond_knowledge.md")
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        conn.close()
        return False
    conn.execute(
        "INSERT INTO skill_documents (doc_type, content) VALUES (?, ?)",
        ("bond_knowledge", content),
    )
    conn.commit()
    conn.close()
    return True


def get_bond_market_data() -> dict | None:
    """获取债券市场最新数据快照。"""


def seed_investment_strategy_knowledge():
    """将投资策略知识写入 skill_documents 表（如尚未写入）。"""
    conn = _get_conn()
    existing = conn.execute(
        "SELECT COUNT(*) FROM skill_documents WHERE doc_type = 'investment_strategy'"
    ).fetchone()[0]
    if existing > 0:
        conn.close()
        return False

    content = """# 4%定投法（强化版）— 研究员雷牛牛

## 核心规则

4%定投法是一种基于回撤幅度的纪律性建仓方法，核心思想是：**只有当标的从上次买入价下跌达到指定幅度（如4%）时才出手买入**，以此积累安全边际。

### 基本操作规则
1. **首次建仓**：在估值合理或偏低时首次买入，记录成本价
2. **下跌加仓**：当价格从上次买入价下跌 4% 时，执行下一次买入
3. **绝不追涨**：坚决不在暴力上涨时追涨，上涨时耐心等待
4. **纪律执行**：严格按规则执行，不因情绪改变计划

### 强化版规则（适用于连续下跌场景）
5. **跌幅越大，买入越多**：下跌 4% 买 1 份，下跌 8% 买 2 份，下跌 12% 买 3 份（金字塔加仓）
6. **设置最大仓位上限**：单品种仓位不超过总资产的 20-30%，防止过度集中
7. **估值极端时加码**：当估值百分位低于 10%（深度低估），可将每次买入份数翻倍
8. **保留现金储备**：始终保留 20-30% 现金，确保有子弹继续加仓

## 应用场景

### 场景1：持仓连续亏损
当用户持仓出现连续亏损时，应用 4% 定投法的思路：
- 不要恐慌割肉，而是评估是否值得继续加仓
- 如果标的基本面没问题、估值已进入低估区间，下跌反而是加仓机会
- 按 4% 间隔分批加仓，摊低成本，积累安全边际
- 计算还需要多少次加仓才能回盈，给用户信心

### 场景2：新建仓计划
- 先确定目标仓位和分批计划
- 首次买入 1/3 仓位
- 后续按 4% 下跌间隔逐步加仓至满仓
- 如果买入后直接上涨，不追涨，等待回调或转向其他低估品种

### 场景3：市场整体下跌
- 评估哪些品种跌幅大但基本面没变
- 优先加仓跌幅大、估值低的品种
- 分散加仓，不要集中在单一品种

## 关键原则

1. **安全边际**：每次下跌买入都在积累安全边际，成本越低，未来盈利空间越大
2. **纪律性**：排除情绪干扰，机械执行，这是该方法最大的优势
3. **逆向思维**：别人恐惧时贪婪，但贪婪要有纪律——按计划加仓，不是一把梭
4. **仓位管理**：永远留有余地，不要在一次下跌中打光所有子弹
5. **耐心**：好的价格往往带着"鬼故事"一起来，这是市场给忍耐者的奖赏

## 计算示例

假设某指数当前估值百分位 25%（偏低估），首次买入价 1.000：
- 第1次加仓：0.960（下跌4%），买入1份
- 第2次加仓：0.922（累计下跌8%），买入2份
- 第3次加仓：0.885（累计下跌12%），买入3份
- 平均成本：约 0.935
- 当价格回到 0.935 即可回盈，而非需要回到 1.000

## 与估值结合

4%定投法必须与估值分析结合使用：
- **低估区域（<40%百分位）**：适合启动4%定投法，积极建仓
- **合理区域（40%-60%百分位）**：持有观望，不加仓也不减仓
- **高估区域（>60%百分位）**：停止加仓，开始考虑分批止盈
- **深度低估（<10%百分位）**：加码买入，这是难得的机会

## 风险提示

1. 4%定投法不适用于基本面恶化的品种（如行业衰退、政策打压）
2. 需要足够的现金储备支撑持续加仓
3. 可能面临长期浮亏，需要心理准备
4. 止损线：如果基本面发生根本变化，应果断止损而非机械加仓
"""

    conn.execute(
        "INSERT INTO skill_documents (doc_type, content) VALUES (?, ?)",
        ("investment_strategy", content),
    )
    conn.commit()
    conn.close()

    # 索引到 RAG
    try:
        from rag import index_skill_document
        conn2 = _get_conn()
        doc_row = conn2.execute("SELECT id FROM skill_documents WHERE doc_type = 'investment_strategy'").fetchone()
        conn2.close()
        if doc_row:
            index_skill_document(doc_row[0], "4%定投法（强化版）— 研究员雷牛牛", content)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"索引4%定投法到RAG失败: {e}")

    return True


def get_bond_market_data() -> dict | None:
    """获取债券市场最新数据快照。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM bond_market_data ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return None
    data = dict(row)
    try:
        data["content"] = json.loads(data["content"])
    except (json.JSONDecodeError, TypeError):
        pass
    return data


def save_bond_market_data(data_type: str, content: dict, snapshot_date: str = None):
    """保存债券市场数据快照。"""
    if not snapshot_date:
        from datetime import date
        snapshot_date = date.today().isoformat()
    conn = _get_conn()
    conn.execute(
        "INSERT INTO bond_market_data (data_type, content, snapshot_date) VALUES (?, ?, ?)",
        (data_type, json.dumps(content, ensure_ascii=False), snapshot_date),
    )
    conn.commit()
    conn.close()
    return True
