"""统一分析快照：家庭现金、跨账户基金聚合和只读约束。"""
import pytest
from db._conn import _get_conn
from db import portfolio as p


def _seed():
    conn = _get_conn()
    for code, name, shares, value, account, category in [
        ('000001', '同一基金', 100, 100, '小鱼儿', 'equity'),
        ('000001', '同一基金', 100, 100, '花无缺', 'equity'),
        ('000002', '国债指数', 100, 300, '小鱼儿', 'bond_index'),
        ('000003', '已清仓', 0, 999, '小鱼儿', 'equity'),
    ]:
        conn.execute('INSERT INTO portfolio_holdings (user_id,fund_code,fund_name,shares,current_value,total_cost,account,fund_category) VALUES (?,?,?,?,?,?,?,?)',
                     ('default', code, name, shares, value, value, account, category))
    for account, balance in [('default', 9000), ('小鱼儿', 200), ('花无缺', 300)]:
        conn.execute('INSERT OR REPLACE INTO portfolio_cash(user_id,balance) VALUES (?,?)', (account, balance))
    conn.execute('INSERT OR REPLACE INTO user_profiles(user_id,max_single_position_pct) VALUES (?,?)', ('default',15))
    conn.commit()
    conn.close()


def test_snapshot_aggregates_funds_and_marks_denominators_without_cash_writes(monkeypatch):
    _seed()
    monkeypatch.setattr(p, 'get_cash_balance', lambda *a, **k: pytest.fail('分析不得结息/写现金'))
    snapshot = p.get_portfolio_snapshot()
    assert snapshot['total_value'] == 500
    assert snapshot['cash_balance'] == 500
    assert snapshot['total_assets'] == 1000
    assert snapshot['holding_count'] == 3
    assert snapshot['fund_count'] == 2
    assert snapshot['record_count'] == 4
    fund = next(f for f in snapshot['funds'] if f['fund_code'] == '000001')
    assert fund['current_value'] == 200
    assert fund['invested_pct'] == 40
    assert fund['total_assets_pct'] == 20
    assert len(fund['accounts']) == 2
    assert snapshot['bond_value'] == 300
    assert snapshot['concentration_policy']['max_single_position_pct'] == 15
    assert snapshot['concentration_policy']['denominator'] == 'total_assets'
    assert p.get_portfolio_summary()['cash_balance'] == 500


def test_context_and_facts_reuse_the_same_snapshot(monkeypatch):
    _seed()
    from services.portfolio.portfolio_context import build_portfolio_context, build_portfolio_summary_line
    from services.portfolio.portfolio_fact_layer import _build_snapshot
    snapshot = p.get_portfolio_snapshot()
    monkeypatch.setattr(p, 'get_portfolio_snapshot', lambda *a, **k: pytest.fail('不得二次读取快照'))
    context = build_portfolio_context(snapshot=snapshot)
    assert '¥1,000' in context
    assert '3 条有效持仓' in context
    assert '2 只基金' in context
    assert '总资产占比' in context and '持仓市值占比' in context
    assert '000001' in build_portfolio_summary_line(snapshot=snapshot)
    facts = _build_snapshot(snapshot=snapshot)
    assert facts['total_value'] == 500
    assert facts['bond_pct'] == 60
    assert {f['fund_code'] for f in facts['overconcentrated']} == {'000001', '000002'}


def test_diversification_counts_funds_and_classifies_bond_index():
    _seed()
    result = p.get_portfolio_diversification()
    assert result['fund_count'] == 2
    assert result['max_holding_pct'] == 60
    assert result['type_distribution']['债券型'] == 300
    assert result['max_holding_pct_denominator'] == 'invested_value'
