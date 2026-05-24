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

    # 初始化预设 Agent
    _init_preset_agents(conn)

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
        SELECT ar.*, a.title as article_title, a.publish_time, a.url as article_url
        FROM analysis_records ar
        LEFT JOIN articles a ON ar.article_id = a.id
    """
    params = []
    if search:
        base += " WHERE ar.index_name LIKE ? OR ar.index_code LIKE ? OR ar.metric_type LIKE ? OR a.title LIKE ?"
        q = f"%{search}%"
        params = [q, q, q, q]
    base += " ORDER BY a.publish_time DESC, ar.image_index LIMIT ?"
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
                   cost_price: float = 0, index_code: str = None,
                   index_name: str = None, buy_date: str = None,
                   notes: str = None, user_id: str = "default") -> int:
    """新增持仓，返回 holding_id。"""
    total_cost = shares * cost_price
    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO portfolio_holdings
            (user_id, fund_code, fund_name, index_code, index_name,
             shares, cost_price, total_cost, buy_date, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, fund_code, fund_name, index_code, index_name,
          shares, cost_price, total_cost, buy_date, notes))
    holding_id = cur.lastrowid
    conn.commit()
    conn.close()
    return holding_id


def get_holding(holding_id: int) -> dict | None:
    """获取单个持仓。"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM portfolio_holdings WHERE id = ?", (holding_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_holdings(user_id: str = "default") -> list[dict]:
    """获取用户所有持仓，按更新时间倒序。"""
    conn = _get_conn()
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


def get_portfolio_summary(user_id: str = "default") -> dict:
    """获取持仓汇总：总市值、总成本、总盈亏、收益率。"""
    holdings = list_holdings(user_id)
    total_cost = sum(h.get("total_cost", 0) or 0 for h in holdings)
    total_value = sum(h.get("current_value", 0) or 0 for h in holdings)
    total_profit = total_value - total_cost
    profit_rate = total_profit / total_cost if total_cost > 0 else 0

    return {
        "holding_count": len(holdings),
        "total_cost": round(total_cost, 2),
        "total_value": round(total_value, 2),
        "total_profit": round(total_profit, 2),
        "profit_rate": round(profit_rate, 4),
        "holdings": holdings,
    }


# ── 交易记录 CRUD ──────────────────────────────────────


def create_transaction(fund_code: str, transaction_type: str, amount: float,
                       transaction_date: str, shares: float = None,
                       price: float = None, holding_id: int = None,
                       notes: str = None, user_id: str = "default") -> int:
    """新增交易记录，返回 transaction_id。自动更新持仓数据。"""
    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO portfolio_transactions
            (holding_id, user_id, fund_code, transaction_type, amount, shares, price, transaction_date, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (holding_id, user_id, fund_code, transaction_type, amount, shares, price, transaction_date, notes))
    tx_id = cur.lastrowid
    conn.commit()
    conn.close()

    # 自动更新持仓数据
    if holding_id:
        _recalculate_holding(holding_id)

    return tx_id


def list_transactions(fund_code: str = None, holding_id: int = None,
                      user_id: str = "default", limit: int = 100) -> list[dict]:
    """获取交易记录列表。"""
    conn = _get_conn()
    conditions = ["user_id = ?"]
    params = [user_id]
    if fund_code:
        conditions.append("fund_code = ?")
        params.append(fund_code)
    if holding_id:
        conditions.append("holding_id = ?")
        params.append(holding_id)

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
    """根据交易记录重新计算持仓数据。"""
    conn = _get_conn()
    holding = conn.execute("SELECT * FROM portfolio_holdings WHERE id = ?", (holding_id,)).fetchone()
    if not holding:
        conn.close()
        return
    holding = dict(holding)

    txs = conn.execute("""
        SELECT * FROM portfolio_transactions
        WHERE holding_id = ?
        ORDER BY transaction_date ASC
    """, (holding_id,)).fetchall()

    total_shares = 0
    total_cost = 0
    for tx in txs:
        tx = dict(tx)
        if tx["transaction_type"] == "buy":
            total_shares += tx.get("shares", 0) or 0
            total_cost += tx.get("amount", 0) or 0
        elif tx["transaction_type"] == "sell":
            sold_shares = tx.get("shares", 0) or 0
            if total_shares > 0:
                avg_cost = total_cost / total_shares
                total_cost -= avg_cost * sold_shares
            total_shares -= sold_shares
        elif tx["transaction_type"] == "dividend":
            total_cost -= tx.get("amount", 0) or 0  # 分红降低成本

    cost_price = total_cost / total_shares if total_shares > 0 else 0
    current_price = holding.get("current_price") or 0
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


def refresh_holding_price(holding_id: int) -> dict | None:
    """
    刷新单个持仓的最新净值并更新数据库。

    返回: {"nav": 0.57, "date": "2026-05-22", "change_pct": -2.1} 或 None
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
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    shares = holding.get("shares", 0) or 0
    total_cost = holding.get("total_cost", 0) or 0
    current_value = shares * nav
    profit_loss = current_value - total_cost
    profit_rate = profit_loss / total_cost if total_cost > 0 else 0

    conn.execute("""
        UPDATE portfolio_holdings SET
            current_price = ?,
            current_value = ?,
            profit_loss = ?,
            profit_rate = ?,
            price_updated_at = ?,
            updated_at = ?
        WHERE id = ?
    """, (round(nav, 4), round(current_value, 2), round(profit_loss, 2),
          round(profit_rate, 4), nav_date, now, holding_id))
    conn.commit()
    conn.close()

    return nav_data


def refresh_all_fund_prices(user_id: str = "default") -> list[dict]:
    """
    批量刷新用户所有持仓的最新净值。

    返回: [{"fund_code": "161725", "fund_name": "...", "nav": 0.57, "date": "2026-05-22"}, ...]
    """
    holdings = list_holdings(user_id)
    results = []
    for h in holdings:
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
        conn.execute("""
            UPDATE portfolio_holdings SET
                current_price = ?,
                current_value = ?,
                profit_loss = ?,
                profit_rate = ?,
                price_updated_at = ?,
                updated_at = ?
            WHERE id = ?
        """, (round(nav, 4), round(current_value, 2), round(profit_loss, 2),
              round(profit_rate, 4), nav_date, now, h["id"]))
        conn.commit()
        conn.close()

        results.append({
            "fund_code": h["fund_code"],
            "fund_name": h["fund_name"],
            "nav": nav,
            "date": nav_date,
            "change_pct": nav_data.get("change_pct"),
        })

    return results


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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 插入默认 agent（如果不存在）
    existing = conn.execute("SELECT COUNT(*) FROM analysis_agents").fetchone()[0]
    if existing == 0:
        conn.execute("""
            INSERT INTO analysis_agents (name, description, system_prompt) VALUES (?, ?, ?)
        """, (
            '市场日报分析师',
            '基于最新财经新闻生成 A 股市场快报，服务于基金配置决策',
            DEFAULT_MARKET_ANALYST_PROMPT,
        ))
    conn.commit()
    conn.close()


DEFAULT_MARKET_ANALYST_PROMPT = """你扮演一位专业的基金投资经理，为我提供一份今日的A股市场快报，重点服务于基金配置决策。报告需包含：

* **今日市况速览**：用一两句话总结市场整体情绪和主要特征。
* **板块掘金与排雷**：
  - **机会所在（热门板块）**：分析强势板块。请使用"政策/事件驱动 + 资金动向 + 估值安全边际"的框架进行分析。
  - **风险提示（回调板块）**：分析弱势板块。请说明回调原因，并判断是短期技术性调整还是基本面发生变化。
* **基金策略池**：根据上述分析，构建一个简单的基金组合建议，例如：
  - **进攻端**：推荐与强势板块对应的ETF或主动型基金。
  - **防御/均衡端**：推荐能覆盖"低估值+高股息"资产的基金，或选股能力较强的均衡型基金。
  - 请简要说明每只基金入围的理由及其与当前市场逻辑的契合点。

请确保分析有数据支撑，结论清晰明了。"""


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
    fields.append("updated_at = CURRENT_TIMESTAMP")
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
         valuation_context, result, token_usage)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    return results
