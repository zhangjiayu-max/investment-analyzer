"""交易计划数据层 — CRUD 操作。"""

import json
from datetime import datetime

from db._conn import _get_conn, _row_to_dict


def create_trade_plan(recommendation_id, fund_code, fund_name, action='BUY',
                      amount=0, shares=0, target_price=None, batch_count=1,
                      batch_interval_days=7, stop_loss_pct=None, take_profit_pct=None,
                      execution_notes=None):
    conn = _get_conn()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur = conn.execute("""
        INSERT INTO trade_plans (
            recommendation_id, fund_code, fund_name, action, amount, shares,
            target_price, batch_count, batch_interval_days, stop_loss_pct,
            take_profit_pct, execution_notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (recommendation_id, fund_code, fund_name, action, amount, shares,
          target_price, batch_count, batch_interval_days, stop_loss_pct,
          take_profit_pct, execution_notes, now, now))
    conn.commit()
    conn.close()
    return cur.lastrowid


def get_trade_plan(plan_id):
    conn = _get_conn()
    row = conn.execute("SELECT * FROM trade_plans WHERE id = ?", (plan_id,)).fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def list_trade_plans(status=None, fund_code=None):
    conn = _get_conn()
    query = "SELECT * FROM trade_plans"
    params = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    elif fund_code:
        query += " WHERE fund_code = ?"
        params.append(fund_code)
    query += " ORDER BY created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def update_trade_plan(plan_id, **fields):
    conn = _get_conn()
    fields['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    set_clause = ', '.join(f"{k} = ?" for k in fields.keys())
    params = list(fields.values()) + [plan_id]
    conn.execute(f"UPDATE trade_plans SET {set_clause} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return True


def delete_trade_plan(plan_id):
    conn = _get_conn()
    cur = conn.execute("DELETE FROM trade_plans WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def list_pending_trade_plans():
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM trade_plans WHERE status = 'pending' ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]
