"""智能补仓计划持久化 CRUD — smart_add_plans 表操作。

S-1（2026-07-22）：将 generate_smart_add_plan 的内存对象持久化，
支持历史回溯、计划vs实际对比、所有信号的反事实验证。

表结构：
- user_id + fund_code + snapshot_date UNIQUE（每日每标的一条计划）
- triggered_signals_json: 触发的信号列表 [A/B/C/D/E]
- exit_signals_json: 退出信号列表 [F子信号]
- plan_detail_json: 完整 plan 对象（JSON）
"""
import json
import logging
from datetime import datetime, timedelta

from db._conn import _get_conn

logger = logging.getLogger(__name__)


def init_smart_add_plans_table(conn):
    """初始化 smart_add_plans 表。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS smart_add_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'default',
            fund_code TEXT NOT NULL,
            fund_name TEXT,
            snapshot_date TEXT NOT NULL,
            triggered_signals_json TEXT DEFAULT '[]',
            exit_signals_json TEXT DEFAULT '[]',
            total_suggested REAL DEFAULT 0,
            final_suggested_amount REAL DEFAULT 0,
            safety_status TEXT DEFAULT '',
            valuation_percentile REAL,
            profit_rate_pct REAL,
            position_pct REAL,
            plan_detail_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(user_id, fund_code, snapshot_date)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_smart_add_plans_user_date "
        "ON smart_add_plans(user_id, snapshot_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_smart_add_plans_fund "
        "ON smart_add_plans(fund_code, snapshot_date)"
    )


def _row_to_plan(row) -> dict:
    """数据库行 → 计划 dict。"""
    if row is None:
        return None
    item = dict(row)
    item["triggered_signals"] = json.loads(item.pop("triggered_signals_json", None) or "[]")
    item["exit_signals"] = json.loads(item.pop("exit_signals_json", None) or "[]")
    # plan_detail_json 仅在 SELECT * 时存在，list 查询不含此字段
    if "plan_detail_json" in item:
        item["plan_detail"] = json.loads(item.pop("plan_detail_json") or "{}")
    else:
        item["plan_detail"] = {}
    return item


def save_smart_add_plan(
    user_id: str,
    fund_code: str,
    fund_name: str,
    snapshot_date: str,
    triggered_signals: list[dict] | None = None,
    exit_signals: list[dict] | None = None,
    total_suggested: float = 0,
    final_suggested_amount: float = 0,
    safety_status: str = "",
    valuation_percentile: float | None = None,
    profit_rate_pct: float | None = None,
    position_pct: float | None = None,
    plan_detail: dict | None = None,
) -> int:
    """保存或更新智能补仓计划（每日每标的一条，UPSERT），返回 id。

    Args:
        snapshot_date: YYYY-MM-DD
        triggered_signals: 触发的信号列表 [{type, label, amount, ...}]
        exit_signals: 退出信号列表
        total_suggested: 信号触发金额汇总
        final_suggested_amount: 多维度最终金额
        safety_status: can_add/blocked/reason
        valuation_percentile: 估值分位
        profit_rate_pct: 盈亏率
        position_pct: 当前仓位占比
        plan_detail: 完整 plan 对象
    """
    conn = _get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO smart_add_plans
                (user_id, fund_code, fund_name, snapshot_date,
                 triggered_signals_json, exit_signals_json,
                 total_suggested, final_suggested_amount, safety_status,
                 valuation_percentile, profit_rate_pct, position_pct,
                 plan_detail_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))
            ON CONFLICT(user_id, fund_code, snapshot_date) DO UPDATE SET
                fund_name = excluded.fund_name,
                triggered_signals_json = excluded.triggered_signals_json,
                exit_signals_json = excluded.exit_signals_json,
                total_suggested = excluded.total_suggested,
                final_suggested_amount = excluded.final_suggested_amount,
                safety_status = excluded.safety_status,
                valuation_percentile = excluded.valuation_percentile,
                profit_rate_pct = excluded.profit_rate_pct,
                position_pct = excluded.position_pct,
                plan_detail_json = excluded.plan_detail_json,
                updated_at = datetime('now','localtime')
        """, (
            user_id, fund_code, fund_name, snapshot_date,
            json.dumps(triggered_signals or [], ensure_ascii=False),
            json.dumps(exit_signals or [], ensure_ascii=False),
            total_suggested, final_suggested_amount, safety_status,
            valuation_percentile, profit_rate_pct, position_pct,
            json.dumps(plan_detail or {}, ensure_ascii=False),
        ))
        conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        row = conn.execute(
            "SELECT id FROM smart_add_plans WHERE user_id=? AND fund_code=? AND snapshot_date=?",
            (user_id, fund_code, snapshot_date),
        ).fetchone()
        return row["id"] if row else 0
    finally:
        conn.close()


def list_smart_add_plans(
    fund_code: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    user_id: str = "default",
    limit: int = 50,
) -> list[dict]:
    """查询历史计划列表（不含 plan_detail，避免数据过大）。"""
    conn = _get_conn()
    try:
        conditions = ["user_id = ?"]
        params: list = [user_id]
        if fund_code:
            conditions.append("fund_code = ?")
            params.append(fund_code)
        if start_date:
            conditions.append("snapshot_date >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("snapshot_date <= ?")
            params.append(end_date)
        where = " AND ".join(conditions)
        params.append(limit)

        rows = conn.execute(
            f"""SELECT id, user_id, fund_code, fund_name, snapshot_date,
                       triggered_signals_json, exit_signals_json,
                       total_suggested, final_suggested_amount, safety_status,
                       valuation_percentile, profit_rate_pct, position_pct,
                       created_at, updated_at
                FROM smart_add_plans
                WHERE {where}
                ORDER BY snapshot_date DESC, id DESC LIMIT ?""",
            params,
        ).fetchall()
        return [_row_to_plan(r) for r in rows]
    finally:
        conn.close()


def get_smart_add_plan_detail(plan_id: int) -> dict | None:
    """获取单条计划完整详情（含 plan_detail）。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM smart_add_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        return _row_to_plan(row) if row else None
    finally:
        conn.close()


def get_plan_vs_actual(fund_code: str, user_id: str = "default", days: int = 30) -> dict:
    """计划vs实际交易对比。

    Args:
        fund_code: 基金代码
        days: 回溯天数

    Returns:
        {
            "fund_code": ...,
            "plans": [{snapshot_date, final_suggested_amount, ...}],
            "actual_txs": [{transaction_date, amount, ...}],
            "summary": {plan_total, actual_total, execution_rate}
        }
    """
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    # 查询历史计划
    plans = list_smart_add_plans(
        fund_code=fund_code, start_date=start_date, user_id=user_id, limit=100
    )

    # 查询实际交易（排除假设交易）
    conn = _get_conn()
    try:
        tx_rows = conn.execute(
            """SELECT transaction_date, amount, shares, price, status, notes
               FROM portfolio_transactions
               WHERE fund_code=? AND user_id=? AND transaction_type='buy'
                 AND (is_hypothetical=0 OR is_hypothetical IS NULL)
                 AND transaction_date >= ?
               ORDER BY transaction_date ASC""",
            (fund_code, user_id, start_date),
        ).fetchall()
    finally:
        conn.close()

    actual_txs = [dict(r) for r in tx_rows]

    plan_total = sum(p.get("final_suggested_amount", 0) for p in plans)
    actual_total = sum(t.get("amount", 0) or 0 for t in actual_txs if t.get("status") in ("confirmed", "submitted"))
    execution_rate = round(actual_total / plan_total * 100, 1) if plan_total > 0 else 0

    return {
        "fund_code": fund_code,
        "plans": plans,
        "actual_txs": actual_txs,
        "summary": {
            "plan_count": len(plans),
            "actual_tx_count": len(actual_txs),
            "plan_total": round(plan_total, 2),
            "actual_total": round(actual_total, 2),
            "execution_rate_pct": execution_rate,
        },
    }
