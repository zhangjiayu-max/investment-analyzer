"""数据库迁移与工具函数。"""

import sqlite3
from urllib.parse import urlparse


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
            today_change_pct REAL DEFAULT 0,
            today_profit REAL DEFAULT 0,
            fund_category TEXT DEFAULT '',
            has_base_position INTEGER DEFAULT 0,
            last_buy_price REAL,
            last_buy_date TEXT,
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
            price_updated_at, today_change_pct, today_profit, fund_category,
            has_base_position, last_buy_price, last_buy_date, created_at, updated_at
        )
        SELECT id, user_id, fund_code, fund_name, index_code, index_name,
            shares, cost_price, current_price, total_cost, current_value,
            profit_loss, profit_rate, buy_date, last_update, notes,
            price_updated_at,
            COALESCE(today_change_pct, 0),
            COALESCE(today_profit, 0),
            COALESCE(fund_category, ''),
            COALESCE(has_base_position, 0),
            last_buy_price,
            last_buy_date,
            created_at, updated_at
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


def _extract_url_id(url: str) -> str:
    """提取 URL 尾部 ID 用于去重，如 https://mp.weixin.qq.com/s/Abc123 → Abc123。"""
    path = urlparse(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if "/" in path else url
