"""智能补仓建议快照表 CRUD — 反事实决策验证数据层。

每次 generate_smart_add_plan() 被调用时：
1. 落库一条建议快照（fund_code + snapshot_date 去重）
2. 同时自动创建一笔假设交易（is_hypothetical=1）
3. 关联 snapshot.hypothetical_tx_id
"""
import logging
from datetime import datetime

from db._conn import _get_conn

logger = logging.getLogger(__name__)


def create_snapshot_with_hypothetical(
    fund_code: str,
    fund_name: str,
    suggested_amount: float,
    suggested_tier: str | None,
    profit_rate_at_snapshot: float | None,
    valuation_zscore: float | None,
    current_price_at_snapshot: float | None,
    user_id: str = "default",
    notes: str = "",
) -> dict | None:
    """创建建议快照 + 自动假设交易（原子操作）。

    去重规则：同一 user_id + fund_code + snapshot_date 只保留最新一条。
    若已存在，删除旧的假设交易和快照，重新创建。

    Returns:
        {"snapshot_id": int, "hypothetical_tx_id": int} 或 None
    """
    snapshot_date = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 建议金额为 0 或负值则不创建（无意义的建议不需要假设验证）
    if not suggested_amount or suggested_amount <= 0:
        return None

    conn = _get_conn()
    try:
        conn.execute("BEGIN")

        # 1. 去重：删除同 user + fund + date 的旧快照及其假设交易
        old_rows = conn.execute(
            """SELECT id, hypothetical_tx_id FROM smart_add_snapshots
               WHERE user_id = ? AND fund_code = ? AND snapshot_date = ?""",
            (user_id, fund_code, snapshot_date),
        ).fetchall()
        for old in old_rows:
            if old["hypothetical_tx_id"]:
                conn.execute(
                    "DELETE FROM portfolio_transactions WHERE id = ? AND is_hypothetical = 1",
                    (old["hypothetical_tx_id"],),
                )
            conn.execute("DELETE FROM smart_add_snapshots WHERE id = ?", (old["id"],))

        # 2. 创建假设交易（is_hypothetical=1）
        # 买入价 = 当日净值（若快照价缺失则用1.0占位，验证时会从净值表取）
        buy_price = current_price_at_snapshot or 0
        buy_shares = round(suggested_amount / buy_price, 4) if buy_price > 0 else 0

        cursor = conn.execute(
            """INSERT INTO portfolio_transactions
               (user_id, fund_code, fund_name, transaction_type, amount, shares, price,
                transaction_date, status, notes, is_system, is_hypothetical, created_at)
               VALUES (?, ?, ?, 'buy', ?, ?, ?, ?, 'confirmed', ?, 0, 1, ?)""",
            (user_id, fund_code, fund_name, suggested_amount, buy_shares, buy_price,
             snapshot_date, f"假设补仓（系统建议）{notes}", now),
        )
        hypothetical_tx_id = cursor.lastrowid

        # 3. 创建快照并关联假设交易
        cursor = conn.execute(
            """INSERT INTO smart_add_snapshots
               (user_id, fund_code, fund_name, suggested_amount, suggested_tier,
                profit_rate_at_snapshot, valuation_zscore, current_price_at_snapshot,
                hypothetical_tx_id, snapshot_date, created_at, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, fund_code, fund_name, suggested_amount, suggested_tier,
             profit_rate_at_snapshot, valuation_zscore, current_price_at_snapshot,
             hypothetical_tx_id, snapshot_date, now, notes),
        )
        snapshot_id = cursor.lastrowid

        conn.commit()
        logger.info(
            f"[snapshot] 快照已落库 fund={fund_code} date={snapshot_date} "
            f"建议金额={suggested_amount} 假设交易id={hypothetical_tx_id}"
        )
        return {"snapshot_id": snapshot_id, "hypothetical_tx_id": hypothetical_tx_id}
    except Exception as e:
        conn.rollback()
        logger.warning(f"[snapshot] 创建快照失败 {fund_code}: {e}")
        return None
    finally:
        conn.close()


def list_snapshots(
    fund_code: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    user_id: str = "default",
    limit: int = 100,
) -> list[dict]:
    """查询历史建议快照列表。"""
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
            f"""SELECT * FROM smart_add_snapshots
                WHERE {where}
                ORDER BY snapshot_date DESC, created_at DESC LIMIT ?""",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_snapshot(snapshot_id: int) -> dict | None:
    """获取单条快照。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM smart_add_snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_hypothetical_txs(user_id: str = "default") -> list[dict]:
    """查询所有假设交易（is_hypothetical=1）。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT t.*, s.id as snapshot_id, s.suggested_amount as snapshot_suggested_amount,
                      s.profit_rate_at_snapshot, s.valuation_zscore
               FROM portfolio_transactions t
               LEFT JOIN smart_add_snapshots s ON s.hypothetical_tx_id = t.id
               WHERE t.user_id = ? AND t.is_hypothetical = 1
               ORDER BY t.transaction_date DESC, t.created_at DESC""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_hypothetical_tx(tx_id: int, user_id: str = "default") -> bool:
    """删除假设交易（同时清理快照关联）。"""
    conn = _get_conn()
    try:
        conn.execute("BEGIN")
        # 清理快照关联
        conn.execute(
            "UPDATE smart_add_snapshots SET hypothetical_tx_id = NULL WHERE hypothetical_tx_id = ?",
            (tx_id,),
        )
        cur = conn.execute(
            "DELETE FROM portfolio_transactions WHERE id = ? AND user_id = ? AND is_hypothetical = 1",
            (tx_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()
