import json
from datetime import date, timedelta


def _insert_tx(conn, **kw):
    cols = ["user_id", "fund_code", "transaction_type", "amount", "status", "transaction_date", "is_system", "is_hypothetical"]
    vals = [kw.get(c, {"user_id":"default", "fund_code":"000001", "transaction_type":"buy", "amount":100, "status":"confirmed", "transaction_date":"2026-01-01", "is_system":0, "is_hypothetical":0}[c]) for c in cols]
    conn.execute(f"INSERT INTO portfolio_transactions ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", vals)


def test_query_transaction_history_filters_before_limit_and_separates_pending(tmp_db):
    from db._conn import _get_conn
    from tools import _query_transaction_history
    conn = _get_conn()
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    _insert_tx(conn, transaction_date="2026-01-01", status="confirmed")
    _insert_tx(conn, transaction_date="2026-01-02", status="pending")
    _insert_tx(conn, transaction_date="2026-01-03", status="pending")
    _insert_tx(conn, transaction_date=tomorrow, status="confirmed")
    _insert_tx(conn, transaction_date="2026-01-05", status="confirmed", is_system=1)
    _insert_tx(conn, transaction_date="2026-01-06", status="confirmed", is_hypothetical=1)
    conn.commit(); conn.close()
    result = json.loads(_query_transaction_history({"fund_code":"000001", "limit":1}))
    assert result["count"] == 1
    assert result["confirmed_count"] == 1
    assert result["pending_count"] == 2
    assert len(result["pending_transactions"]) == 1
    assert result["transactions"][0]["status"] == "confirmed"
    assert result["date_scope"]["end"] == today


def test_calculate_metrics_uses_price_series_and_returns_unknown_without_it(tmp_db, monkeypatch):
    import tools
    monkeypatch.setattr(tools, "list_valuation_indexes", lambda: [{"index_code":"000300", "index_name":"沪深300"}])
    monkeypatch.setattr(tools, "get_index_price_history", lambda *a, **k: [
        {"trade_date":(date.today()-timedelta(days=3)).isoformat(), "close":100}, {"trade_date":(date.today()-timedelta(days=2)).isoformat(), "close":80}, {"trade_date":(date.today()-timedelta(days=1)).isoformat(), "close":120}
    ])
    result = json.loads(tools._calculate_metrics({"index_name":"沪深300", "metric_type":"max_drawdown", "period":"1年"}))
    assert result["max_drawdown"] == 20.0
    assert result["data_source"] == "index_price_history"
    monkeypatch.setattr(tools, "get_index_price_history", lambda *a, **k: [])
    unknown = json.loads(tools._calculate_metrics({"index_name":"沪深300", "metric_type":"annualized_return", "period":"1年"}))
    assert unknown["metric_status"] == "unknown"
    assert "valuation" not in unknown.get("data_source", "")


def test_memory_labels_history_source_and_date(tmp_db, monkeypatch):
    from db._conn import _get_conn
    conn = _get_conn()
    conn.execute("INSERT INTO conversations(title, created_at, updated_at) VALUES ('历史问题', '2026-08-01 10:00:00', '2026-08-02 11:00:00')")
    conn.execute("INSERT INTO user_memories(user_id,memory_type,content,source,created_at,last_accessed) VALUES ('default','preference','偏好分批买入','feedback','2026-08-03','2026-08-04')")
    conn.commit(); conn.close()
    from agent.memory.memory import build_user_memory_context
    result = build_user_memory_context("default")
    assert "历史会话" in result
    assert "更新时间2026-08-02" in result
    assert "feedback" in result
    assert "记录时间2026-08-03" in result
    assert "不代表本轮意图" in result


def test_memory_never_reads_another_users_topics(tmp_db):
    from db._conn import _get_conn
    from agent.memory.memory import build_user_memory_context
    conn = _get_conn()
    conn.execute("INSERT INTO conversations(title, context_data) VALUES (?, ?)", ("隐私话题", json.dumps({"user_id":"bob"})))
    conn.execute("INSERT INTO conversations(title) VALUES ('旧默认用户话题')")
    conn.commit(); conn.close()
    assert "隐私话题" not in build_user_memory_context("default")
    assert "旧默认用户话题" not in build_user_memory_context("alice")


def test_annualized_return_uses_elapsed_dates_and_sorts_history(monkeypatch):
    import tools
    monkeypatch.setattr(tools, "list_valuation_indexes", lambda: [{"index_code":"000300", "index_name":"沪深300"}])
    monkeypatch.setattr(tools, "get_index_price_history", lambda *a, **k: [
        {"trade_date":date.today().isoformat(), "close":110},
        {"trade_date":(date.today()-timedelta(days=365)).isoformat(), "close":100},
    ])
    result = json.loads(tools._calculate_metrics({"index_name":"沪深300", "metric_type":"annualized_return", "period":"1年"}))
    assert result["total_return"] == 10.0
    assert abs(result["annualized_return"]-10) < 0.02
    assert result["return_basis"].startswith("指数价格收益")


def test_transaction_type_and_date_filters_apply_before_limit(tmp_db):
    from db._conn import _get_conn
    from tools import _query_transaction_history
    conn = _get_conn()
    _insert_tx(conn, transaction_date="2026-01-01", transaction_type="sell", status="settled")
    _insert_tx(conn, transaction_date="2026-01-02", transaction_type="buy")
    _insert_tx(conn, transaction_date="2026-01-03", transaction_type="sell", user_id="other")
    conn.commit(); conn.close()
    result = json.loads(_query_transaction_history({"transaction_type":"sell", "limit":1, "start_date":"2026-01-01", "end_date":"2026-01-02"}))
    assert result["sell_count"] == 1
    assert result["transactions"][0]["transaction_date"] == "2026-01-01"
    assert result["confirmed_count"] == 1
