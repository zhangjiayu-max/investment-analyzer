"""策略监控数据层 — CRUD 操作。"""

import json
from datetime import datetime

from db._conn import _get_conn, _row_to_dict


def create_strategy_monitoring(backtest_id, strategy_name, strategy_type,
                                target_code, target_type='index', parameters=None):
    conn = _get_conn()
    params_json = json.dumps(parameters or {}, ensure_ascii=False)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur = conn.execute("""
        INSERT INTO strategy_monitoring (
            backtest_id, strategy_name, strategy_type, target_code,
            target_type, parameters, current_state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)
    """, (backtest_id, strategy_name, strategy_type, target_code,
          target_type, params_json, now, now))
    conn.commit()
    conn.close()
    return cur.lastrowid


def get_strategy_monitoring(monitoring_id):
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM strategy_monitoring WHERE id = ?", (monitoring_id,)
    ).fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def list_strategy_monitoring(status=None, target_code=None):
    conn = _get_conn()
    query = "SELECT * FROM strategy_monitoring"
    params = []
    conditions = []
    if status:
        conditions.append("current_state = ?")
        params.append(status)
    if target_code:
        conditions.append("target_code = ?")
        params.append(target_code)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def update_strategy_monitoring(monitoring_id, **fields):
    conn = _get_conn()
    fields['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    set_clause = ', '.join(f"{k} = ?" for k in fields.keys())
    params = list(fields.values()) + [monitoring_id]
    conn.execute(f"UPDATE strategy_monitoring SET {set_clause} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return True


def delete_strategy_monitoring(monitoring_id):
    conn = _get_conn()
    cur = conn.execute("DELETE FROM strategy_monitoring WHERE id = ?", (monitoring_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def create_strategy_trade(monitoring_id, trade_type, fund_code, amount, nav=None,
                          status='executed', error_message=None):
    conn = _get_conn()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur = conn.execute("""
        INSERT INTO strategy_trades (
            monitoring_id, trade_type, fund_code, amount, nav,
            trade_date, status, error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (monitoring_id, trade_type, fund_code, amount, nav, now, status, error_message))
    conn.commit()
    conn.close()
    return cur.lastrowid


def list_strategy_trades(monitoring_id=None, fund_code=None):
    conn = _get_conn()
    query = "SELECT * FROM strategy_trades"
    params = []
    conditions = []
    if monitoring_id:
        conditions.append("monitoring_id = ?")
        params.append(monitoring_id)
    if fund_code:
        conditions.append("fund_code = ?")
        params.append(fund_code)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY trade_date DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]
