"""账务守恒回归：只使用 conftest 提供的临时数据库。"""
from concurrent.futures import ThreadPoolExecutor
import pytest
from db import portfolio as p
from db._conn import _get_conn
from db.goal_buckets import create_goal_bucket, get_goal_bucket
from services.finance.bucket_engine import transfer_between_buckets
from services import fee_calculator as fees


def holding():
    return p.create_holding('000001', '测试基金', 100, 1, 1, buy_date='2026-01-01', account='测试')


def pending(hid, kind='sell'):
    return p.create_transaction('000001', kind, 0, '2026-02-01', holding_id=hid,
                                shares=50, status='pending', account='测试')


def test_cost_replay_sell_then_rebuy():
    hid = holding()
    p.create_transaction('000001', 'sell', 100, '2026-02-01', shares=100, price=1, holding_id=hid)
    p.create_transaction('000001', 'buy', 200, '2026-03-01', shares=100, price=2, holding_id=hid)
    assert p.get_holding(hid)['total_cost'] == 200
    assert p.get_holding(hid)['shares'] == 100


def test_cost_replay_backfilled_trade_date():
    hid = holding()
    p.create_transaction('000001', 'buy', 200, '2026-03-01', shares=100, price=2, holding_id=hid)
    p.create_transaction('000001', 'sell', 100, '2026-02-01', shares=100, price=1, holding_id=hid)
    p._recalculate_holding(hid)
    assert p.get_holding(hid)['total_cost'] == 200


def test_confirm_rolls_back_status_if_recalculation_fails(monkeypatch):
    hid = holding()
    tx = pending(hid)
    def fail(*args, **kwargs):
        raise RuntimeError('injected accounting failure')
    monkeypatch.setattr(p, '_recalculate_holding', fail)
    with pytest.raises(RuntimeError):
        p.confirm_transaction(tx, 2)
    assert p.get_transaction(tx)['status'] == 'pending'
    assert p.get_holding(hid)['shares'] == 100
    conn = _get_conn()
    try:
        row = conn.execute("SELECT balance FROM portfolio_cash WHERE user_id='default'").fetchone()
        assert row is None or row['balance'] == 0
    finally:
        conn.close()


def test_concurrent_confirmation_credits_cash_once():
    hid = holding()
    tx = pending(hid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda _: p.confirm_transaction(tx, 2), range(2)))
    assert sorted(result) == [False, True]
    assert p.get_holding(hid)['shares'] == 50
    conn = _get_conn()
    try:
        assert conn.execute("SELECT balance FROM portfolio_cash WHERE user_id='default'").fetchone()['balance'] == 100
    finally:
        conn.close()


def test_conversion_requires_target_and_target_nav():
    hid = holding()
    tx = pending(hid, 'convert')
    assert p.confirm_transaction(tx, 2) is False
    assert p.get_transaction(tx)['status'] == 'pending'
    assert p.confirm_transaction(tx, 2, target_fund_code='000002') is False
    assert p.get_holding(hid)['shares'] == 100


def test_conversion_uses_independent_target_nav():
    hid = holding()
    tx = pending(hid, 'convert')
    assert p.confirm_transaction(tx, 2, target_fund_code='000002', target_price=4, fee=4)
    target = p.get_holding_by_fund('000002', account='测试')
    assert target['shares'] == 24
    assert target['total_cost'] == 96
    assert p.get_holding(hid)['shares'] == 50


@pytest.mark.parametrize('amount', [0, -1, float('nan'), float('inf'), 101])
def test_bucket_rejects_invalid_transfer(amount):
    a = create_goal_bucket('a', 'stable', current_amount=100)
    b = create_goal_bucket('b', 'long_term', current_amount=10)
    assert transfer_between_buckets(a, b, amount) is False
    assert get_goal_bucket(a)['current_amount'] == 100
    assert get_goal_bucket(b)['current_amount'] == 10


def test_bucket_rejects_self_transfer():
    a = create_goal_bucket('a', 'stable', current_amount=100)
    assert transfer_between_buckets(a, a, 10) is False
    assert get_goal_bucket(a)['current_amount'] == 100


def test_concurrent_bucket_transfer_preserves_total():
    a = create_goal_bucket('a', 'stable', current_amount=100)
    b = create_goal_bucket('b', 'long_term', current_amount=0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: transfer_between_buckets(a, b, 80), range(2)))
    assert sorted(results) == [False, True]
    assert get_goal_bucket(a)['current_amount'] == 20
    assert get_goal_bucket(b)['current_amount'] == 80


@pytest.mark.parametrize('percent,expected', [(0, 0), (.15, .0015), (1, .01), (1.5, .015)])
def test_metadata_fee_percentage_units(percent, expected):
    fees._FUND_FEE_CACHE.clear()
    conn = _get_conn()
    try:
        conn.execute('INSERT INTO fund_metadata (fund_code, subscription_fee) VALUES (?, ?)', ('000001', percent))
        conn.commit()
    finally:
        conn.close()
    assert fees.get_fund_fee_rate('000001') == expected


def test_conversion_rolls_back_both_legs_on_target_failure(monkeypatch):
    hid = holding()
    tx = pending(hid, 'convert')
    real = p._recalculate_holding
    def recalc(holding_id, conn=None):
        if holding_id != hid:
            raise RuntimeError('target failure')
        return real(holding_id, conn=conn)
    monkeypatch.setattr(p, '_recalculate_holding', recalc)
    with pytest.raises(RuntimeError):
        p.confirm_transaction(tx, 2, target_fund_code='000002', target_price=4)
    assert p.get_transaction(tx)['status'] == 'pending'
    assert p.get_holding(hid)['shares'] == 100
    assert p.get_holding_by_fund('000002', account='测试') is None


def test_bucket_credit_failure_rolls_back_debit():
    a = create_goal_bucket('a', 'stable', current_amount=100)
    b = create_goal_bucket('b', 'long_term', current_amount=10)
    conn = _get_conn()
    try:
        conn.execute(f"CREATE TRIGGER fail_bucket_credit BEFORE UPDATE ON goal_buckets WHEN NEW.id={b} BEGIN SELECT RAISE(ABORT, 'credit failure'); END")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(Exception, match='credit failure'):
        transfer_between_buckets(a, b, 50)
    assert get_goal_bucket(a)['current_amount'] == 100
    assert get_goal_bucket(b)['current_amount'] == 10


def test_conversion_preserves_target_fund_category():
    hid = holding()
    tx = pending(hid, 'convert')
    assert p.confirm_transaction(tx, 2, target_fund_code='000002',
                                 target_fund_name='测试纯债基金', target_price=4)
    assert p.get_holding_by_fund('000002', account='测试')['fund_category'] == 'bond_pure'


def test_transfer_api_accepts_frontend_object_body():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.portfolio.bucket_routes import router
    app = FastAPI()
    app.include_router(router)
    a = create_goal_bucket('a','stable',current_amount=100)
    b = create_goal_bucket('b','long_term',current_amount=0)
    response = TestClient(app).post(f'/api/buckets/{a}/transfer/{b}',json={'amount':10})
    assert response.status_code == 200
    assert get_goal_bucket(a)['current_amount'] == 90
