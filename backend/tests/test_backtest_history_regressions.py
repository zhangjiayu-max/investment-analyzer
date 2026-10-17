"""回测只消费指定区间历史数据，不借用当前持仓价。"""
from db._conn import _get_conn
from services.advisor import decision_backtest as bt


def seed_prices():
    conn = _get_conn()
    try:
        conn.executemany('INSERT INTO fund_nav_history (fund_code,nav_date,nav) VALUES (?,?,?)',
                         [('000001','2026-01-01',1),('000001','2026-01-15',1.1),('000001','2026-03-01',2)])
        conn.executemany('INSERT INTO index_price_history (index_code,trade_date,close) VALUES (?,?,?)',
                         [('000300.SH','2026-01-01',100),('000300.SH','2026-01-15',105),('000300.SH','2026-03-01',300)])
        conn.commit()
    finally:
        conn.close()


def test_historical_replay_never_uses_current_holding():
    seed_prices()
    dec = {'fund_code':'000001','created_at':'2026-01-01 12:00:00',
           'signal_snapshot':{'entry_price':1},'status':'confirmed'}
    sample = bt._resolve_review_sample(dec, '2026-01-15')
    assert sample['current_price'] == 1.1
    assert sample['price_date'] == '2026-01-15'
    assert sample['benchmark_pct'] == 5
    assert sample['entry_date'] == '2026-01-01'


def test_missing_historical_sample_does_not_learn(monkeypatch):
    import db.investment_decisions as ledger
    monkeypatch.setattr(ledger, 'get_decisions_due_for_review', lambda *a: [
        {'id':1,'fund_code':'000001','created_at':'2026-01-01','theme':'银行',
         'source_module':'opportunity_radar','signal_snapshot':{'entry_price':1}}])
    def fail(*a, **kw):
        raise AssertionError('missing data must not be reviewed or learned')
    monkeypatch.setattr(ledger, 'record_review', fail)
    monkeypatch.setattr(bt, 'adjust_theme_weight', fail)
    result = bt.run_decision_backtest(as_of_date='2026-01-15')
    assert result['processed'] == 0
    assert result['skipped'] == 1


def test_executed_price_needs_its_own_date():
    seed_prices()
    dec = {'fund_code':'000001','created_at':'2026-01-01','actual_price':1.05,
           'status':'executed','signal_snapshot':{}}
    assert bt._resolve_review_sample(dec, '2026-01-15') is None
