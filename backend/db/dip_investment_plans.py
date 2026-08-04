"""4%定投法 — 建仓配置 + 触发记录 CRUD。

来源：雷牛牛4%定投法（https://mp.weixin.qq.com/s/8Dhofw5taUPL_teHoYJ8-w）
核心：估值锁底 + 跌幅触发 + 固定份数 + 机械纪律

表结构：
- dip_investment_plans：用户建仓时填写的配置（投资上限/份数/跌幅%/估值门槛）
- dip_investment_triggers：每次触发记录（第几次/上一买入价/当前价/实际跌幅/建议金额）
"""
import logging
from datetime import datetime

from db._conn import _get_conn

logger = logging.getLogger(__name__)


def init_dip_investment_tables(conn=None):
    """建表，启动时调用。"""
    _close = False
    if conn is None:
        conn = _get_conn()
        _close = True
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dip_investment_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fund_code TEXT NOT NULL,
            fund_name TEXT,
            max_amount REAL NOT NULL,
            total_shares INTEGER DEFAULT 10,
            dip_pct REAL DEFAULT 4.0,
            valuation_threshold REAL DEFAULT 20,
            metric_type TEXT,
            enabled INTEGER DEFAULT 1,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(fund_code)
        );

        CREATE TABLE IF NOT EXISTS dip_investment_triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            fund_code TEXT NOT NULL,
            trigger_num INTEGER NOT NULL,
            trigger_date TEXT NOT NULL,
            prev_buy_price REAL,
            current_price REAL,
            actual_dip_pct REAL,
            valuation_percentile REAL,
            suggested_amount REAL,
            executed INTEGER DEFAULT 0,
            executed_date TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (plan_id) REFERENCES dip_investment_plans(id),
            UNIQUE(plan_id, trigger_num)
        );

        CREATE INDEX IF NOT EXISTS idx_dip_triggers_fund ON dip_investment_triggers(fund_code);
        CREATE INDEX IF NOT EXISTS idx_dip_triggers_plan ON dip_investment_triggers(plan_id);
    """)
    conn.commit()
    if _close:
        conn.close()


# ── Plan CRUD ──────────────────────────────────────

def create_dip_plan(fund_code: str, fund_name: str, max_amount: float,
                    total_shares: int = 10, dip_pct: float = 4.0,
                    valuation_threshold: float = 20.0, metric_type: str = None) -> int:
    """创建4%定投配置。"""
    conn = _get_conn()
    try:
        cursor = conn.execute("""
            INSERT INTO dip_investment_plans
                (fund_code, fund_name, max_amount, total_shares, dip_pct, valuation_threshold, metric_type)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fund_code) DO UPDATE SET
                fund_name=excluded.fund_name,
                max_amount=excluded.max_amount,
                total_shares=excluded.total_shares,
                dip_pct=excluded.dip_pct,
                valuation_threshold=excluded.valuation_threshold,
                metric_type=excluded.metric_type,
                enabled=1,
                status='active',
                updated_at=datetime('now','localtime')
        """, (fund_code, fund_name, max_amount, total_shares, dip_pct, valuation_threshold, metric_type))
        conn.commit()
        plan_id = cursor.lastrowid
        logger.info(f"[dip_4pct] 创建配置 fund={fund_code} max={max_amount} shares={total_shares} dip={dip_pct}%")
        return plan_id
    finally:
        conn.close()


def get_dip_plan(fund_code: str) -> dict | None:
    """查询单个配置。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM dip_investment_plans WHERE fund_code = ?", (fund_code,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_dip_plan_by_id(plan_id: int) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM dip_investment_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_dip_plans(active_only: bool = True) -> list[dict]:
    """列出所有配置。"""
    conn = _get_conn()
    try:
        sql = "SELECT * FROM dip_investment_plans"
        if active_only:
            sql += " WHERE enabled = 1 AND status = 'active'"
        sql += " ORDER BY created_at DESC"
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_dip_plan(plan_id: int, **fields) -> bool:
    """更新配置。"""
    if not fields:
        return False
    allowed = {"max_amount", "total_shares", "dip_pct", "valuation_threshold",
               "metric_type", "enabled", "status", "fund_name"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    updates["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [plan_id]
    conn = _get_conn()
    try:
        conn.execute(f"UPDATE dip_investment_plans SET {set_clause} WHERE id = ?", params)
        conn.commit()
        return True
    finally:
        conn.close()


def delete_dip_plan(plan_id: int) -> bool:
    """删除配置（同时删除触发记录）。"""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM dip_investment_triggers WHERE plan_id = ?", (plan_id,))
        cursor = conn.execute("DELETE FROM dip_investment_plans WHERE id = ?", (plan_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# ── Trigger CRUD ───────────────────────────────────

def create_dip_trigger(plan_id: int, fund_code: str, trigger_num: int,
                       trigger_date: str, prev_buy_price: float, current_price: float,
                       actual_dip_pct: float, valuation_percentile: float,
                       suggested_amount: float) -> int:
    """记录一次触发。"""
    conn = _get_conn()
    try:
        cursor = conn.execute("""
            INSERT INTO dip_investment_triggers
                (plan_id, fund_code, trigger_num, trigger_date, prev_buy_price,
                 current_price, actual_dip_pct, valuation_percentile, suggested_amount)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (plan_id, fund_code, trigger_num, trigger_date, prev_buy_price,
              current_price, actual_dip_pct, valuation_percentile, suggested_amount))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def list_dip_triggers(plan_id: int = None, fund_code: str = None) -> list[dict]:
    """查询触发历史。"""
    conn = _get_conn()
    try:
        sql = "SELECT * FROM dip_investment_triggers WHERE 1=1"
        params = []
        if plan_id:
            sql += " AND plan_id = ?"
            params.append(plan_id)
        if fund_code:
            sql += " AND fund_code = ?"
            params.append(fund_code)
        sql += " ORDER BY trigger_num"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_executed_triggers(plan_id: int) -> int:
    """统计已执行的触发次数（用于判断当前是第几次）。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM dip_investment_triggers WHERE plan_id = ? AND executed = 1",
            (plan_id,)
        ).fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()


def mark_trigger_executed(trigger_id: int, executed_date: str = None) -> bool:
    """标记触发为已执行。"""
    if executed_date is None:
        executed_date = datetime.now().strftime("%Y-%m-%d")
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "UPDATE dip_investment_triggers SET executed = 1, executed_date = ? WHERE id = ?",
            (executed_date, trigger_id)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_latest_trigger(plan_id: int) -> dict | None:
    """获取最近一次触发记录。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM dip_investment_triggers WHERE plan_id = ? ORDER BY trigger_num DESC LIMIT 1",
            (plan_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
