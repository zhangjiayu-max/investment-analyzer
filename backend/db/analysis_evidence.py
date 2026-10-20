"""AI 分析使用的只读证据查询；区分成交行为与待确认计划。"""

from datetime import date

from db._conn import _get_conn


def query_trade_evidence(*, user_id="default", fund_code=None,
                         transaction_type=None, limit=50,
                         start_date=None, end_date=None):
    """统计指定日期窗口的真实成交，分别返回待确认记录。"""
    today = date.today().isoformat()
    end = min(date.fromisoformat(end_date).isoformat(), today) if end_date else today
    start = date.fromisoformat(start_date).isoformat() if start_date else None
    clauses = ["user_id = ?", "COALESCE(is_system, 0) = 0",
               "COALESCE(is_hypothetical, 0) = 0", "date(transaction_date) <= ?"]
    params = [user_id, end]
    for column, value in (("fund_code", fund_code), ("transaction_type", transaction_type)):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    if start:
        clauses.append("date(transaction_date) >= ?")
        params.append(start)
    where = " AND ".join(clauses)
    conn = _get_conn()
    try:
        def fetch(status_filter):
            return [dict(r) for r in conn.execute(
                f"SELECT * FROM portfolio_transactions WHERE {where} AND {status_filter} "
                "ORDER BY transaction_date DESC, id DESC LIMIT ?", [*params, limit])]

        completed = "status IN ('confirmed', 'settled')"
        txs = fetch(completed)
        pending = fetch("status = 'pending'")
        summary = dict(conn.execute(
            f"SELECT COUNT(*) AS confirmed_count, MIN(transaction_date) AS first_date, "
            f"MAX(transaction_date) AS last_date FROM portfolio_transactions WHERE {where} AND {completed}",
            params).fetchone())
        pending_count = conn.execute(
            f"SELECT COUNT(*) FROM portfolio_transactions WHERE {where} AND status = 'pending'",
            params).fetchone()[0]
    finally:
        conn.close()
    return {
        "transactions": txs, "confirmed_count": summary["confirmed_count"],
        "pending_count": pending_count, "pending_transactions": pending,
        "date_scope": {"start": start, "end": end,
                       "first_confirmed_date": summary["first_date"],
                       "last_confirmed_date": summary["last_date"]},
        "statistics_scope": "buy_count/sell_count/buy_total/sell_total 仅统计返回的已成交记录；confirmed_count/pending_count 为日期窗口总数",
        "behavior_evidence_note": "待确认记录仅代表计划；排除未来日期、系统及假设交易。返回最近 N 条不代表固定天数或周数。",
        "confirmed_statuses": ["confirmed", "settled"],
    }
