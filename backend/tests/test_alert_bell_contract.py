"""合并预警的展示、已读与删除必须采用一致的分组语义。"""
from db import _get_conn
from db.portfolio import list_alerts, mark_alert_read, delete_alert, get_unread_alert_count


def seed():
    conn = _get_conn()
    records = [
        ('default', 'loss_warning', '001', 'warning', '昨日亏损10%', 0),
        ('default', 'loss_warning', '001', 'warning', '今日亏损12%', 1),
        ('default', 'loss_warning', '002', 'warning', '昨日亏损10%', 0),
        ('other', 'loss_warning', '001', 'warning', '昨日亏损10%', 0),
        ('default', 'loss_warning', '001', 'danger', '昨日亏损10%', 0),
    ]
    ids = []
    for record in records:
        ids.append(conn.execute('''INSERT INTO portfolio_alerts
            (user_id, alert_type, related_fund_code, severity, title, is_read)
            VALUES (?, ?, ?, ?, ?, ?)''', record).lastrowid)
    conn.commit(); conn.close()
    return ids


def test_group_contains_identity_and_aggregate_read_state():
    ids = seed()
    row = next(a for a in list_alerts() if a['latest_id'] == ids[1])
    assert row['id'] == ids[1]
    assert row['cnt'] == 2
    assert row['unread_count'] == 1
    assert row['is_read'] == 0


def test_read_uses_same_group_as_listing_even_when_titles_change():
    ids = seed()
    assert mark_alert_read(ids[1])
    assert get_unread_alert_count() == 2
    assert get_unread_alert_count('other') == 1
    conn = _get_conn()
    states = {r['id']: r['is_read'] for r in conn.execute('SELECT id, is_read FROM portfolio_alerts')}
    conn.close()
    assert states[ids[0]] == 1
    assert states[ids[2]] == 0


def test_read_is_idempotent_but_missing_id_is_false():
    ids = seed()
    assert mark_alert_read(ids[0])
    assert mark_alert_read(ids[0])
    assert not mark_alert_read(999999)


def test_delete_uses_same_group_and_preserves_other_funds():
    ids = seed()
    assert delete_alert(ids[0])
    conn = _get_conn()
    remaining = {r['id'] for r in conn.execute('SELECT id FROM portfolio_alerts')}
    conn.close()
    assert remaining == set(ids[2:])


def test_null_and_empty_fund_codes_are_same_group():
    conn = _get_conn()
    ids = []
    for code in (None, ''):
        ids.append(conn.execute('''INSERT INTO portfolio_alerts
            (user_id, alert_type, related_fund_code, severity, title)
            VALUES ('default', 'capital_outflow', ?, 'warning', ?)''', (code, str(code))).lastrowid)
    conn.commit(); conn.close()
    assert mark_alert_read(ids[0])
    assert get_unread_alert_count() == 0


def test_risk_content_skips_missing_stock_fields_and_does_not_infer_fund_type(monkeypatch):
    from db import portfolio
    from services.advisor.alert_scanner import _build_risk_content
    monkeypatch.setattr(portfolio, 'get_fund_holdings', lambda code: {
        'top_stocks': [
            {'stock_name': '', 'pct_nav': None},
            {'stock_name': '甲公司', 'pct_nav': None},
            {'stock_name': '乙公司', 'pct_nav': 5.2},
        ],
        'asset_allocation': [{'type': '股票', 'pct': 95}],
    })
    text = _build_risk_content('001', '测试指数基金', 10, None, -12)
    assert 'None' not in text
    assert '甲公司' in text
    assert '乙公司(5.2%)' in text
    assert '股票仓位 95%' in text
    assert '混合型基金' not in text
