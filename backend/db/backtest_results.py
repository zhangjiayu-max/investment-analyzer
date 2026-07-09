"""回测结果持久化数据层。"""

import json
from db._conn import _get_conn, _row_to_dict
from db._utils import _add_column_if_not_exists


def init_backtest_tables(conn):
    """建表，启动时调用。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT DEFAULT 'default',
            name TEXT NOT NULL,
            target_code TEXT NOT NULL,
            target_type TEXT NOT NULL DEFAULT 'index',
            strategy TEXT NOT NULL,
            params_json TEXT NOT NULL DEFAULT '{}',
            initial_cash REAL DEFAULT 0,
            final_value REAL DEFAULT 0,
            total_return REAL DEFAULT 0,
            annual_return REAL DEFAULT 0,
            max_drawdown REAL DEFAULT 0,
            sharpe_ratio REAL DEFAULT 0,
            nav_curve_json TEXT DEFAULT '[]',
            benchmark_return REAL DEFAULT 0,
            months INTEGER DEFAULT 0,
            decision_id INTEGER,
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    # P3：新增 volatility + benchmark_nav_curve_json 列
    _add_column_if_not_exists(conn, "backtest_results", "volatility", "REAL DEFAULT 0")
    _add_column_if_not_exists(conn, "backtest_results", "benchmark_nav_curve_json", "TEXT DEFAULT '[]'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bt_user ON backtest_results(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bt_strategy ON backtest_results(strategy)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bt_created ON backtest_results(created_at)")


def save_backtest(
    name: str,
    target_code: str,
    target_type: str,
    strategy: str,
    params: dict,
    result: dict,
    benchmark: dict,
    months: int,
    notes: str = "",
    user_id: str = "default",
) -> int:
    """保存回测结果，返回 id。

    P3 新增字段：
      - volatility: result["volatility"]（年化波动率）
      - benchmark_nav_curve: benchmark["nav_curve"]（基准净值曲线）
    """
    conn = _get_conn()
    try:
        cursor = conn.execute("""
            INSERT INTO backtest_results
                (user_id, name, target_code, target_type, strategy, params_json,
                 initial_cash, final_value, total_return, annual_return,
                 max_drawdown, sharpe_ratio, volatility, nav_curve_json,
                 benchmark_return, benchmark_nav_curve_json, months, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, name, target_code, target_type, strategy,
            json.dumps(params, ensure_ascii=False),
            result.get("total_invested", 0),
            result.get("final_value", 0),
            result.get("total_return", 0),
            result.get("annual_return", 0),
            result.get("max_drawdown", 0),
            result.get("sharpe_ratio", 0),
            result.get("volatility", 0),
            json.dumps(result.get("nav_curve", []), ensure_ascii=False),
            benchmark.get("total_return", 0) if benchmark else 0,
            json.dumps(benchmark.get("nav_curve", []) if benchmark else [], ensure_ascii=False),
            months,
            notes,
        ))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def list_backtests(limit: int = 20, user_id: str = "default") -> list[dict]:
    """列出历史回测。"""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT id, name, target_code, target_type, strategy,
                   initial_cash, final_value, total_return, annual_return,
                   max_drawdown, sharpe_ratio, volatility, benchmark_return, months,
                   decision_id, notes, created_at
            FROM backtest_results
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (user_id, limit)).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_backtest(backtest_id: int) -> dict | None:
    """获取单条回测结果（含净值曲线 + 基准净值曲线）。"""
    conn = _get_conn()
    try:
        row = conn.execute("""
            SELECT * FROM backtest_results WHERE id = ?
        """, (backtest_id,)).fetchone()
        if not row:
            return None
        item = _row_to_dict(row)
        item["params_json"] = json.loads(item.get("params_json") or "{}")
        item["nav_curve_json"] = json.loads(item.get("nav_curve_json") or "[]")
        item["benchmark_nav_curve_json"] = json.loads(item.get("benchmark_nav_curve_json") or "[]")
        return item
    finally:
        conn.close()


def delete_backtest(backtest_id: int) -> bool:
    """删除回测记录。"""
    conn = _get_conn()
    try:
        cursor = conn.execute("DELETE FROM backtest_results WHERE id = ?", (backtest_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def link_backtest_to_decision(backtest_id: int, decision_id: int) -> bool:
    """关联回测到决策。"""
    conn = _get_conn()
    try:
        cursor = conn.execute("""
            UPDATE backtest_results SET decision_id = ? WHERE id = ?
        """, (decision_id, backtest_id))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
