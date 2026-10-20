"""持仓账务治理回归：只用临时库，禁止外部连接。"""
import json
import socket
from concurrent.futures import ThreadPoolExecutor

import pytest

from db import portfolio as p
from db._conn import _get_conn


@pytest.fixture(autouse=True)
def offline(tmp_db, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('external TCP is disabled')
    from db.portfolio_accounting import init_portfolio_accounting_schema
    conn = _get_conn()
    init_portfolio_accounting_schema(conn)
    conn.commit()
    conn.close()
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(p, '_capture_valuation_snapshot', lambda *a: None)
    monkeypatch.setattr(p, '_auto_update_decision_on_confirm', lambda *a: None)


def holding(account='花无缺', price=1, shares=100, code='000001', user='default'):
    return p.create_holding(code, '测试债券', shares=shares, cost_price=1,
                            current_price=price, account=account, user_id=user,
                            fund_category='bond', buy_date='2025-01-01')


def pending(hid, kind='buy', amount=50, source='internal', shares=None):
    return p.create_transaction('000001', kind, 0, '2026-10-20', holding_id=hid,
                                submitted_amount=amount, submitted_shares=shares,
                                status='pending', funding_source=source)


def test_internal_purchase_freezes_then_debits_only_once(tmp_db):
    hid = holding()
    p.set_cash_balance('花无缺', 100)
    tx = pending(hid)
    cash = p.get_portfolio_cash_snapshot()
    assert cash['cash_balance'] == 100
    assert cash['frozen_cash'] == 50
    assert cash['available_cash'] == 50
    assert p.confirm_transaction(tx, 1)
    assert not p.confirm_transaction(tx, 1)
    assert p.get_portfolio_cash_snapshot()['cash_balance'] == 50
    assert p.get_portfolio_cash_snapshot()['frozen_cash'] == 0
    assert p.get_portfolio_summary()['total_assets'] == 200
    conn = _get_conn()
    assert conn.execute("SELECT COUNT(*) FROM portfolio_cash_ledger WHERE tx_id=? AND event_type='buy_payment'", (tx,)).fetchone()[0] == 1
    conn.close()


def test_frozen_cash_cannot_be_reused_and_cancel_releases(tmp_db):
    hid = holding()
    p.set_cash_balance('花无缺', 60)
    tx = pending(hid)
    with pytest.raises(ValueError, match='可用现金'):
        pending(hid, amount=20)
    assert p.delete_transaction(tx)
    assert p.get_portfolio_cash_snapshot()['available_cash'] == 60
    pending(hid, amount=60)


def test_external_buy_records_external_flow_without_using_cash(tmp_db):
    hid = holding()
    p.set_cash_balance('花无缺', 100)
    tx = pending(hid, source='external')
    assert p.confirm_transaction(tx, 1, fee=1)
    assert p.get_portfolio_cash_snapshot()['cash_balance'] == 100
    conn = _get_conn()
    flows = [dict(r) for r in conn.execute('SELECT * FROM portfolio_cash_ledger WHERE tx_id=? ORDER BY id', (tx,))]
    conn.close()
    assert [(r['event_type'], r['amount']) for r in flows] == [('external_contribution', 50), ('buy_payment', -50)]
    assert p.get_portfolio_summary()['total_assets'] == 249


def test_sell_is_account_receivable_until_explicit_settlement(tmp_db):
    hid = holding(account='小鱼儿')
    p.set_cash_balance('小鱼儿', 10)
    p.set_cash_balance('花无缺', 20)
    tx = pending(hid, kind='sell', shares=40)
    assert p.confirm_transaction(tx, 1, fee=1)
    cash = p.get_portfolio_cash_snapshot()
    assert cash['cash_balance'] == 30
    assert cash['receivable_cash'] == 39
    assert p.get_portfolio_summary()['total_assets'] == 129
    assert p.settle_transaction(tx)
    assert not p.settle_transaction(tx)
    cash = p.get_portfolio_cash_snapshot()
    assert cash['receivable_cash'] == 0
    assert cash['cash_balance'] == 69
    conn = _get_conn()
    assert conn.execute("SELECT balance FROM portfolio_cash WHERE user_id='小鱼儿'").fetchone()[0] == 49
    conn.close()


def test_legacy_confirmed_sale_does_not_credit_again_on_settle(tmp_db):
    hid = holding()
    p.set_cash_balance('default', 50)
    tx = p.create_transaction('000001', 'sell', 40, '2026-01-01', shares=40, price=1, holding_id=hid)
    assert p.settle_transaction(tx)
    assert p.get_portfolio_cash_snapshot()['cash_balance'] == 50


def test_concurrent_internal_confirms_debit_once(tmp_db):
    hid = holding()
    p.set_cash_balance('花无缺', 100)
    tx = pending(hid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: p.confirm_transaction(tx, 1), range(2)))
    assert sorted(results) == [False, True]
    assert p.get_portfolio_cash_snapshot()['cash_balance'] == 50


def test_edit_is_auditable_and_survives_replay(tmp_db):
    hid = holding()
    p.update_holding(hid, shares=200, cost_price=1.5)
    tx = p.create_transaction('000001', 'buy', 20, '2026-10-21', shares=20, price=1, holding_id=hid)
    assert p.get_holding(hid)['shares'] == 220
    assert p.get_holding(hid)['total_cost'] == 320
    p._recalculate_holding(hid)
    assert p.get_holding(hid)['total_cost'] == 320
    conn = _get_conn()
    adjustment = dict(conn.execute('SELECT * FROM portfolio_holding_adjustments WHERE holding_id=?', (hid,)).fetchone())
    conn.close()
    assert json.loads(adjustment['before_json'])['shares'] == 100
    assert json.loads(adjustment['after_json'])['shares'] == 200


def test_nav_preview_never_confirms(tmp_db, monkeypatch):
    hid = holding()
    tx = p.create_transaction('000001', 'buy', 0, '2026-10-19', holding_id=hid, submitted_amount=20, status='pending')
    monkeypatch.setattr(p, '_effective_trade_date', lambda *a: '2026-10-19')
    monkeypatch.setattr(p, 'fetch_fund_nav_on_or_before', lambda *a: {'nav': 1.2, 'date': '2026-10-19'})
    preview = p.preview_transaction_nav(tx)
    assert preview['confirmed_price'] == 1.2
    assert preview['preview_only'] is True
    assert p.get_transaction(tx)['status'] == 'pending'
    assert p.get_holding(hid)['shares'] == 100


def test_unknown_price_is_not_total_loss(tmp_db):
    holding(price=None)
    summary = p.get_portfolio_summary()
    assert summary['valuation_complete'] is False
    assert summary['total_profit'] is None
    assert summary['profit_rate'] is None
    assert summary['valuation_coverage']['missing_count'] == 1


def test_nav_refresh_reloads_shares_and_rejects_older_price(tmp_db, monkeypatch):
    hid = holding()
    def fetch(*a):
        p.update_holding(hid, shares=200)
        return {'nav': 2, 'date': '2026-10-20', 'change_pct': 0}
    monkeypatch.setattr(p, 'fetch_fund_nav', fetch)
    p.refresh_holding_price(hid)
    assert p.get_holding(hid)['current_value'] == 400
    monkeypatch.setattr(p, 'fetch_fund_nav', lambda *a: {'nav': 1.5, 'date': '2026-10-19'})
    result = p.refresh_holding_price(hid)
    assert result['skipped'] is True
    assert p.get_holding(hid)['current_price'] == 2


def test_household_concentration_groups_same_fund(tmp_db):
    holding(account='花无缺', shares=100)
    holding(account='小鱼儿', shares=100)
    holding(code='000002', shares=200)
    result = p.get_concentration_analysis()
    assert len(result['holdings']) == 2
    assert result['max_concentration'] == .5


def test_sell_preview_preserves_wealth_and_uses_holding_owner(tmp_db):
    hid = holding(user='alice')
    p.set_cash_balance('alice', 100)
    holding(user='default', shares=10000)
    preview = p.preview_sell(hid, 20)
    assert preview['total_assets_after'] == 200
    assert preview['cost_basis'] == 20
    assert preview['concentration_after'] == .4
    assert p.preview_sell(hid, -1)['error']


def test_actual_performance_separates_realized_dividends_and_fees(tmp_db):
    hid = holding(price=2)
    p.create_transaction('000001', 'sell', 79, '2026-10-19', shares=40, price=2, holding_id=hid)
    p.create_transaction('000001', 'dividend', 10, '2026-10-20', holding_id=hid)
    conn = _get_conn()
    conn.execute("UPDATE portfolio_transactions SET fee=1 WHERE transaction_type='sell'")
    conn.commit()
    conn.close()
    perf = p.get_portfolio_summary()['performance']
    assert perf['unrealized_profit'] == 60
    assert perf['realized_profit'] == 39
    assert perf['cash_dividends'] == 10
    assert perf['fees'] == 1
    assert perf['cumulative_investment_profit'] == 109
    assert perf['xirr']['value'] is None
    assert perf['xirr']['reason']


def test_external_buy_does_not_hide_legacy_default_cash(tmp_db):
    hid = holding()
    p.set_cash_balance('default', 100)
    tx = pending(hid, source='external')
    assert p.confirm_transaction(tx, 1)
    assert p.get_portfolio_cash_snapshot()['cash_balance'] == 100


def test_cash_adjustments_are_audited_and_cannot_spend_reserved_balance(tmp_db):
    hid = holding()
    p.set_cash_balance('花无缺', 100)
    tx = pending(hid, amount=80)
    with pytest.raises(ValueError, match='冻结'):
        p.add_cash('花无缺', -30)
    with pytest.raises(ValueError, match='冻结'):
        p.set_cash_balance('花无缺', 50)
    p.add_cash('花无缺', 20)
    ledger = p.list_cash_ledger(account='花无缺')
    assert any(r['event_type'] == 'cash_reconciliation' and r['amount'] == 100 for r in ledger)
    assert any(r['event_type'] == 'cash_deposit' and r['amount'] == 20 for r in ledger)


def test_other_user_cash_cannot_fund_purchase(tmp_db):
    hid = holding()
    p.set_cash_balance('alice', 10000)
    with pytest.raises(ValueError, match='所属'):
        p.create_transaction('000001','buy',0,'2026-10-20',holding_id=hid,
            status='pending',submitted_amount=50,funding_source='internal',cash_account='alice')


def test_pending_before_reconciliation_can_still_confirm_after_it(tmp_db):
    hid = holding()
    p.set_cash_balance('花无缺', 100)
    tx = pending(hid)
    # A later transaction exists, so a numeric high-water mark would wrongly skip tx.
    p.create_transaction('000001','dividend',1,'2026-10-20',holding_id=hid)
    p.update_holding(hid, shares=200, cost_price=1.5)
    assert p.confirm_transaction(tx, 1)
    assert p.get_holding(hid)['shares'] == 250
    assert p.get_holding(hid)['total_cost'] == 350


def test_realized_profit_uses_reconciled_cost_basis(tmp_db):
    hid = holding(price=2)
    p.update_holding(hid, shares=200,cost_price=1.5)
    p.create_transaction('000001','sell',100,'2026-10-20',holding_id=hid,shares=50,price=2)
    perf = p.get_portfolio_performance()
    assert perf['realized_profit'] == 25
    assert perf['cumulative_investment_profit'] == 100
    assert perf['history_complete'] is False


def test_new_confirmed_dividend_records_cash_without_reducing_basis(tmp_db):
    hid = holding(price=2)
    tx = p.create_transaction('000001','dividend',10,'2026-10-20',holding_id=hid,status='pending')
    assert p.confirm_transaction(tx,2)
    assert p.get_holding(hid)['total_cost'] == 100
    assert p.get_portfolio_cash_snapshot()['cash_balance'] == 10
    assert p.get_portfolio_summary()['performance']['cumulative_investment_profit'] == 110


def test_confirm_rejects_inconsistent_buy_amount_and_shares(tmp_db):
    hid=holding()
    tx=pending(hid,source='external')
    assert not p.confirm_transaction(tx,1,confirmed_shares=500)
    assert p.get_transaction(tx)['status'] == 'pending'


def test_preview_refuses_an_earlier_nav_and_invalid_share_inputs(tmp_db, monkeypatch):
    hid=holding()
    tx=pending(hid,source='external')
    monkeypatch.setattr(p,'_effective_trade_date',lambda *a:'2026-10-20')
    monkeypatch.setattr(p,'fetch_fund_nav_on_or_before',lambda *a:{'nav':1,'date':'2026-10-19'})
    with pytest.raises(ValueError,match='尚未披露'):
        p.preview_transaction_nav(tx)
    assert p.preview_sell(hid,float('nan'))['error']


def test_xirr_uses_actual_cashflows_when_complete(tmp_db):
    hid=holding(shares=0,price=1)
    p.create_transaction('000001','buy',100,'2025-01-01',holding_id=hid,shares=100,price=1,funding_source='external')
    conn=_get_conn()
    conn.execute("UPDATE portfolio_holdings SET current_price=1.1,current_value=110,profit_loss=10,price_updated_at='2026-01-01' WHERE id=?",(hid,))
    conn.commit();conn.close()
    perf=p.get_portfolio_performance()
    assert perf['history_complete'] is True
    assert perf['xirr']['value'] == pytest.approx(.1,abs=1e-6)


def test_failed_confirmation_rolls_back_cash_and_holding(tmp_db,monkeypatch):
    hid=holding()
    p.set_cash_balance('花无缺',100)
    tx=pending(hid)
    original=p._recalculate_holding
    def fail(*a,**kw):
        original(*a,**kw)
        raise ValueError('invalid replay')
    monkeypatch.setattr(p,'_recalculate_holding',fail)
    with pytest.raises(ValueError,match='invalid replay'):
        p.confirm_transaction(tx,1)
    assert p.get_transaction(tx)['status']=='pending'
    assert p.get_holding(hid)['shares']==100
    assert p.get_portfolio_cash_snapshot()['frozen_cash']==50
    assert p.get_portfolio_cash_snapshot()['cash_balance']==100


def test_concentration_denominator_includes_cash_and_sale_receivables(tmp_db):
    hid=holding()
    p.set_cash_balance('花无缺',100)
    result=p.get_concentration_analysis()
    assert result['max_concentration']==.5
    assert result['denominator']=='total_assets'
    tx=pending(hid,kind='sell',shares=20)
    assert p.confirm_transaction(tx,1)
    assert p.get_concentration_analysis()['max_concentration']==.4


def test_schema_migration_is_idempotent_and_does_not_rewrite_old_cash(tmp_db):
    from db.portfolio_accounting import init_portfolio_accounting_schema
    p.set_cash_balance('default',321)
    conn=_get_conn()
    init_portfolio_accounting_schema(conn)
    init_portfolio_accounting_schema(conn)
    conn.commit()
    assert conn.execute("SELECT balance FROM portfolio_cash WHERE user_id='default'").fetchone()[0]==321
    conn.close()


def test_transaction_api_requires_explicit_source_and_propagates_cash_account(tmp_db):
    import asyncio
    from fastapi import HTTPException
    from models.portfolio import CreateTransactionRequest
    from routers.portfolio.portfolio import create_transaction_api
    hid=holding()
    req={'fund_code':'000001','transaction_type':'buy','amount':0,'transaction_date':'2026-10-20',
         'holding_id':hid,'status':'pending','submitted_amount':30,'cash_account':'小鱼儿'}
    with pytest.raises(HTTPException) as err:
        asyncio.run(create_transaction_api(CreateTransactionRequest(**req)))
    assert err.value.status_code==400
    result=asyncio.run(create_transaction_api(CreateTransactionRequest(**req,funding_source='external')))
    tx=p.get_transaction(result['transaction_id'])
    assert tx['funding_source']=='external'
    assert tx['cash_account']=='小鱼儿'


def test_transaction_api_reports_insufficient_cash_as_client_error(tmp_db):
    import asyncio
    from fastapi import HTTPException
    from models.portfolio import CreateTransactionRequest
    from routers.portfolio.portfolio import create_transaction_api
    hid=holding()
    req=CreateTransactionRequest(fund_code='000001',transaction_type='buy',amount=0,
         transaction_date='2026-10-20',holding_id=hid,status='pending',submitted_amount=30,
         funding_source='internal',cash_account='花无缺')
    with pytest.raises(HTTPException) as err:
        asyncio.run(create_transaction_api(req))
    assert err.value.status_code==400


def test_accounting_static_api_routes_precede_holding_route(tmp_db):
    from routers.portfolio.portfolio import router
    paths=[r.path for r in router.routes]
    assert paths.index('/api/portfolio/cash-ledger') < paths.index('/api/portfolio/{holding_id}')
    assert paths.index('/api/portfolio/performance') < paths.index('/api/portfolio/{holding_id}')
    assert '/api/portfolio/transactions/{tx_id}/nav-preview' in paths


def test_unread_count_ignores_resolved_lifecycle_risks(tmp_db):
    from db.alert_lifecycle import create_alert
    aid=create_alert('loss_warning','风险',related_fund_code='000001')
    conn=_get_conn()
    conn.execute("UPDATE portfolio_alerts SET risk_status='resolved' WHERE id=?",(aid,))
    conn.commit();conn.close()
    assert p.get_unread_alert_count()==0


def test_explicit_direct_confirmed_sale_and_dividend_account_for_cash(tmp_db):
    hid=holding()
    sale=p.create_transaction('000001','sell',20,'2026-10-20',holding_id=hid,
         shares=20,price=1,status='confirmed',funding_source='internal')
    assert p.get_portfolio_cash_snapshot()['receivable_cash']==20
    assert p.settle_transaction(sale)
    assert p.get_portfolio_cash_snapshot()['cash_balance']==20
    p.create_transaction('000001','dividend',10,'2026-10-20',holding_id=hid,
                         status='confirmed',funding_source='internal')
    assert p.get_portfolio_cash_snapshot()['cash_balance']==30


def test_quick_entry_requires_funding_source_and_creates_pending_without_quotes(tmp_db):
    import asyncio
    from fastapi import HTTPException
    from infra.schemas import QuickEntryRequest
    from routers.portfolio.portfolio import quick_entry_api
    with pytest.raises(HTTPException) as err:
        asyncio.run(quick_entry_api(QuickEntryRequest(fund_code='000001',amount=50)))
    assert err.value.status_code==400
    req=QuickEntryRequest(fund_code='000001',amount=50,funding_source='external',cash_account='小鱼儿',account='小鱼儿')
    result=asyncio.run(quick_entry_api(req))
    tx=p.get_transaction(result['transaction_id'])
    assert tx['status']=='pending'
    assert tx['submitted_amount']==50
    assert tx['funding_source']=='external'
    assert tx['shares'] is None


def test_new_sale_settlement_preserves_existing_legacy_cash(tmp_db):
    hid=holding()
    p.set_cash_balance('default',100)
    tx=pending(hid,kind='sell',shares=20)
    assert p.confirm_transaction(tx,1)
    assert p.settle_transaction(tx)
    assert p.get_portfolio_cash_snapshot()['cash_balance']==120
    assert p.get_portfolio_summary()['total_assets']==200


def test_explicit_direct_buy_creates_actual_holding(tmp_db):
    tx=p.create_transaction('000001','buy',100,'2026-10-20',shares=100,price=1,
        fund_name='测试',account='花无缺',funding_source='external',status='confirmed')
    record=p.get_transaction(tx)
    assert record['holding_id']
    assert p.get_holding(record['holding_id'])['shares']==100


def test_confirmation_audit_is_part_of_atomic_facts(tmp_db,monkeypatch):
    hid=holding()
    tx=pending(hid,source='external')
    assert p.confirm_transaction(tx,1)
    conn=_get_conn()
    events=conn.execute("SELECT * FROM portfolio_tx_audit_log WHERE tx_id=? AND action='confirm'",(tx,)).fetchall()
    conn.close()
    assert len(events)==1
    assert events[0]['after_shares']==50


def test_legacy_non_cash_confirmation_keeps_arbitrary_holding_label(tmp_db):
    hid=holding(account='旧导入标签')
    tx=p.create_transaction('000001','buy',0,'2026-10-20',holding_id=hid,
                            submitted_amount=20,status='pending')
    assert p.confirm_transaction(tx,1)
    tx=p.create_transaction('000001','convert',0,'2026-10-20',holding_id=hid,
                            submitted_shares=20,status='pending')
    assert p.confirm_transaction(tx,1,target_fund_code='000002',target_price=1)
    assert p.get_portfolio_cash_snapshot()['cash_balance']==0


def test_legacy_sale_with_ambiguous_cash_owner_requires_reconciliation(tmp_db):
    hid=holding(account='旧导入标签')
    tx=p.create_transaction('000001','sell',0,'2026-10-20',holding_id=hid,
                            submitted_shares=20,status='pending')
    with pytest.raises(ValueError,match='所属'):
        p.confirm_transaction(tx,1)
    assert p.get_transaction(tx)['status']=='pending'


def test_delete_api_preserves_confirmed_facts_cash_and_pending_reservation(tmp_db):
    import asyncio
    from fastapi import HTTPException
    from routers.portfolio.portfolio import delete_holding_api
    hid=holding()
    p.set_cash_balance('花无缺',100)
    tx=pending(hid)
    assert p.confirm_transaction(tx,1)
    pending_tx=pending(hid,amount=20)
    with pytest.raises(HTTPException) as err:
        asyncio.run(delete_holding_api(hid))
    assert err.value.status_code==409
    assert '保留' in str(err.value.detail)
    assert p.get_holding(hid)['shares']==150
    assert p.get_transaction(tx)['status']=='confirmed'
    assert p.get_transaction(pending_tx)['status']=='pending'
    assert p.get_portfolio_cash_snapshot()['frozen_cash']==20
    assert p.get_portfolio_cash_snapshot()['cash_balance']==50


def test_clear_api_preserves_settled_trade_and_ledger(tmp_db):
    import asyncio
    from fastapi import HTTPException
    from routers.portfolio.portfolio import clear_portfolio_api
    hid=holding()
    tx=pending(hid,kind='sell',shares=20)
    assert p.confirm_transaction(tx,1)
    assert p.settle_transaction(tx)
    before=p.list_cash_ledger()
    with pytest.raises(HTTPException) as err:
        asyncio.run(clear_portfolio_api())
    assert err.value.status_code==409
    assert '保留' in str(err.value.detail)
    assert p.get_transaction(tx)['status']=='settled'
    assert p.get_holding(hid)['shares']==80
    assert p.get_portfolio_cash_snapshot()['cash_balance']==20
    assert p.list_cash_ledger()==before
