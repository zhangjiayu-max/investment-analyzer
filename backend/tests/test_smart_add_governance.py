"""补仓建议治理：关键输入、最终金额及同预算情景回归。"""
from datetime import date
import math
import pytest
from services.advisor import smart_add_planner as planner, position_sizing


@pytest.fixture
def single_plan(monkeypatch):
    cfg = {
        'enabled': True, 'pyramid_enabled': True, 'tiers': [{'loss_pct': -20, 'release_pct': 25, 'add_ratio': 10}, {'loss_pct': -10, 'release_pct': 15, 'add_ratio': 5}],
        'loss_threshold': -10, 'valuation_pause_pct': 60, 'max_single_position_pct': 25,
        'max_add_vs_position_mult': 1, 'stale_days': 14, 'trend_signal_enabled': False,
        'dip_signal_enabled': False, 'exit_signal_enabled': False,
    }
    holding = dict(fund_code='F', fund_name='测试宽基', index_code='I', shares=1000,
                   total_cost=1000, current_value=850, current_price=.85, profit_rate=-.15,
                   price_updated_at=date.today().isoformat(), price_date=date.today().isoformat())
    valuation = dict(percentile=20, zscore=-1, snapshot_date=date.today().isoformat(), days_old=0,
                     metric_type='市盈率', is_expired=False)
    monkeypatch.setattr(planner, 'get_best_valuation', lambda *a, **kw: valuation)
    monkeypatch.setattr(planner, 'calc_kelly_limit', lambda *a, **kw: dict(limit_pct=25))
    monkeypatch.setattr(planner, 'calc_recovery_time', lambda *a: {})
    monkeypatch.setattr(planner, 'calc_valuation_win_rate', lambda *a, **kw: {})
    monkeypatch.setattr(planner, '_detect_dip_4pct_signal', lambda *a: None)
    monkeypatch.setattr(planner, '_check_fund_health', lambda *a, **kw: None)
    monkeypatch.setattr(planner, '_detect_trend_signal', lambda *a: None)
    monkeypatch.setattr(planner, '_detect_dip_signal', lambda *a: None)
    monkeypatch.setattr(planner, 'generate_position_sizing_plan', lambda **kw: dict(target_driven_monthly=0))
    monkeypatch.setattr(planner, '_check_cooldown', lambda *a: (True, 0, 0, ''))
    def generate():
        return planner._generate_single_plan(holding.copy(), cfg, 10000, 1500, 50, valuation_snapshot={})
    return holding, valuation, cfg, generate


def test_future_tier_is_only_a_plan(single_plan):
    _, _, _, generate = single_plan
    result = generate()
    assert result['pyramid']['next_trigger']['loss_pct'] == -20
    assert result['pyramid']['released_amount'] == 0
    assert not any(s['type'] == 'pyramid' for s in result['triggered_signals'])


@pytest.mark.parametrize('kind', ['price', 'valuation', 'unknown_price_date'])
def test_stale_evidence_blocks_all_adds(single_plan, kind):
    holding, valuation, _, generate = single_plan
    if kind == 'price':
        holding.update(price_updated_at='2020-01-01', price_date='2020-01-01')
    elif kind == 'valuation':
        valuation.update(snapshot_date='2020-01-01', is_expired=True, days_old=100)
    else:
        holding.pop('price_updated_at'); holding.pop('price_date')
    result = generate()
    assert result['safety']['can_add'] is False
    assert result['final_suggested_amount'] == 0
    assert result['safety']['data_warning']


def test_hard_cap_cannot_be_overridden_by_strategy_target():
    p = dict(effective_base=4000, safety=dict(can_add=True, max_position_pct=5),
             position_sizing=dict(target_position=dict(target_pct=7)))
    assert planner._plan_hard_cap(p, 100000, 1) == 1000


def test_incremental_room_does_not_become_total_target(monkeypatch):
    monkeypatch.setattr(position_sizing, '_get_user_risk_multiplier', lambda *_: 2)
    result = position_sizing.calc_target_position(dict(limit_pct=5), dict(percentile=20), 'broad',
        dict(hard_cap_pct=40), dict(room_pct=1), dict(position_room_pct=1, data_available=True))
    assert result['target_pct'] == 5
    assert result['max_increment_pct'] == 1
    zero = position_sizing.calc_target_position(dict(limit_pct=5), dict(percentile=20), 'broad',
        dict(hard_cap_pct=40), dict(room_pct=0), dict(position_room_pct=1, data_available=True))
    assert zero['max_increment_pct'] == 0


def test_grid_without_persisted_anchor_is_observation():
    result = planner._generate_grid_plan(dict(current_price=1, current_value=1000), dict(percentile=50),
                                        dict(grid_enabled=True, grid_count=6))
    assert result['current_grid'] == 3
    assert result['triggered'] is False
    assert result['amount'] == 0


@pytest.mark.parametrize('drop,months,amount', [(-100, 6, 100), (math.nan, 6, 100), (-5, 0, 100), (-5, 6, -1)])
def test_simulation_boundaries(drop, months, amount):
    from services.advisor.smart_add_governance import simulate_cash_strategies
    with pytest.raises(ValueError):
        simulate_cash_strategies(dict(current_price=1, shares=1000, total_cost=1200), drop, months, 100, amount)


def test_same_budget_retained_cash_can_win_down_scenario():
    from services.advisor.smart_add_governance import simulate_cash_strategies
    result = simulate_cash_strategies(dict(current_price=1, shares=1000, total_cost=1200), -5, 6, 100, 1000, .001)
    assert result['best_strategy'] == '不补仓'
    assert {s['initial_wealth'] for s in result['strategies']} == {2000}
    for s in result['strategies']:
        assert s['total_added'] <= 1000
        assert s['remaining_cash'] >= 0
        assert s['final_wealth'] == pytest.approx(s['final_value'] + s['remaining_cash'], abs=.02)
        assert s['max_drawdown_pct'] >= 0


def test_final_quantization_follows_budget_and_minimum():
    from services.advisor.smart_add_governance import finalize_execution_amount
    p = dict(current_price=1.01, fund_name='指数ETF', final_suggested_amount=250)
    finalize_execution_amount(p, min_amount=10, lot_size=100)
    assert p['final_suggested_amount'] == 202
    assert p['execution_shares'] == 200
    p['final_suggested_amount'] = 5
    finalize_execution_amount(p, min_amount=10)
    assert p['final_suggested_amount'] == 0
    assert p['execution_shares'] == 0


def test_config_tiers_validation_is_atomic_and_lossless():
    from services.advisor.smart_add_governance import validate_config_patch
    assert validate_config_patch({'smart_add.pyramid_tiers': '10:15:5,20:25:10'})['smart_add.pyramid_tiers'] == '10:15:5,20:25:10'
    for value in ['10:15', '10:15:5,10:25:10', '0:10:5', '10:15:NaN']:
        with pytest.raises(ValueError):
            validate_config_patch({'smart_add.pyramid_tiers': value})
    with pytest.raises(ValueError):
        validate_config_patch({'smart_add.grid_count': 0})


def test_win_rate_reports_mature_horizon_and_metric(monkeypatch):
    from services.advisor import smart_add_metrics as metrics
    import services.index_history_fetcher as fetcher
    from datetime import timedelta
    rows = [dict(trade_date=(date(2020, 1, 1) + timedelta(days=i)).isoformat(), close=100 + i, pe_ttm=10, pb=None) for i in range(283)]
    monkeypatch.setattr(fetcher, 'get_index_price_history', lambda *a, **kw: rows)
    pe = metrics.calc_valuation_win_rate('I', 100)
    assert pe['sample_count_12m'] == 1
    assert pe['independent_count_12m'] == 1
    assert pe['win_rate_12m'] is None
    assert pe['confidence_interval_12m'] is not None
    pb = metrics.calc_valuation_win_rate('I', 100, metric_type='市净率')
    assert pb['sample_count_12m'] == 0
    assert pb['win_rate_12m'] is None
    assert pb['metric_type'] == '市净率'


def test_get_preview_has_no_persistence_side_effects(monkeypatch):
    import sys, types
    monkeypatch.setattr(planner, '_load_config', lambda: dict(enabled=True, pool_pct=15, base_dca_pct=4, max_add_vs_position_mult=1, exit_signal_enabled=False))
    monkeypatch.setattr(planner, 'get_portfolio_summary', lambda **kw: dict(total_assets=10000, holdings=[dict(fund_code='F')]))
    monkeypatch.setattr(planner, '_apply_goal_aware_adjustments', lambda *a: {})
    monkeypatch.setattr(planner, '_generate_single_plan', lambda *a, **kw: dict(fund_code='F', fund_name='F', profit_rate=0, current_price=1, final_suggested_amount=100, safety=dict(can_add=True), triggered_signals=[dict(triggered=True, type='dip', amount=100)], position_sizing=dict(cash_constraint=dict(data_available=True, usable_cash=1000)), effective_base=1000))
    monkeypatch.setattr(planner, '_apply_correlation_downweight', lambda *a: None)
    monkeypatch.setattr(planner, '_get_current_month_buy_amount', lambda *a: 0)
    monkeypatch.setattr(planner, '_calc_rebalance_suggestion', lambda *a: [])
    def forbidden(*a, **kw):
        raise AssertionError('预览不能写入')
    monkeypatch.setattr(planner, 'create_snapshot_with_hypothetical', forbidden)
    monkeypatch.setattr(planner, 'save_smart_add_plan', forbidden)
    monkeypatch.setattr(planner, '_plans_to_candidates', forbidden)
    guard = types.ModuleType('services.portfolio.decision_guardrails')
    contexts = []
    guard.build_decision_snapshot = lambda **kw: dict(cash=dict(available_now=50), snapshot_id='s')
    def evaluate(fund_code, requested_amount, **kw):
        contexts.append(kw['context'])
        return dict(status='allowed', executable_amount=requested_amount, reasons=[])
    guard.evaluate_add_decision = evaluate
    monkeypatch.setitem(sys.modules, 'services.portfolio.decision_guardrails', guard)
    result = planner.generate_smart_add_plan()
    assert result['plans'][0]['final_suggested_amount'] <= 50
    assert result['portfolio_view']['allocated_amount'] <= 50
    assert len(contexts) == 1


def test_snapshot_explicit_save_does_not_rewrite_assumed_execution():
    from db.smart_add_snapshots import create_snapshot_with_hypothetical, list_snapshots
    kwargs = dict(fund_code='F', fund_name='测试', suggested_tier=None, profit_rate_at_snapshot=0, valuation_zscore=None, current_price_at_snapshot=1)
    first = create_snapshot_with_hypothetical(suggested_amount=100, **kwargs)
    again = create_snapshot_with_hypothetical(suggested_amount=200, **kwargs)
    assert first == again
    assert list_snapshots()[0]['suggested_amount'] == 100


def test_no_future_nav_is_waiting_for_evaluation(monkeypatch):
    from services import counterfactual_verifier as verifier
    monkeypatch.setattr(verifier, '_get_fund_price_at_date', lambda *a: None)
    monkeypatch.setattr(verifier, '_get_latest_fund_price', lambda *a: (1., '2026-10-20'))
    result = verifier.verify_hypothetical_tx(dict(id=1, fund_code='F', transaction_date='2026-10-20', amount=100, price=1))
    assert result['status'] == 'pending_evaluation'


def test_final_shares_are_funded_in_full():
    from services.advisor.smart_add_governance import finalize_execution_amount
    p = dict(current_price=1.01999, fund_name='指数ETF', final_suggested_amount=250)
    finalize_execution_amount(p)
    assert p['execution_shares'] * p['current_price'] <= p['final_suggested_amount'] + 1e-8
    assert p['final_suggested_amount'] <= 250


def test_shadow_ledger_deducts_cash_and_deduplicates_events(monkeypatch):
    import json
    from services import counterfactual_verifier as verifier
    from db import smart_add_snapshots
    rows = [dict(id=i, fund_code='F', amount=80, transaction_date=f'2026-01-0{i}',
                 notes=json.dumps(dict(mode='suggestion_tracking', shadow_cash_budget=100,
                                       event_key='same' if i < 3 else 'new'))) for i in (1, 2, 3)]
    monkeypatch.setattr(smart_add_snapshots, 'list_hypothetical_txs', lambda **kw: rows)
    monkeypatch.setattr(verifier, '_get_next_nav_after', lambda *a: (1, '2026-01-05'))
    monkeypatch.setattr(verifier, '_get_latest_fund_price', lambda *a: (.5, '2026-02-01'))
    result = verifier.verify_all_hypothetical()
    assert result['summary']['remaining_shadow_cash'] == 20
    assert result['summary']['total_shadow_wealth'] == 60
    assert result['summary']['total_profit_loss'] == -40
    assert result['summary']['total_count'] == 1
    assert [r['status'] for r in reversed(result['hypothetical_txs'])] == ['verified', 'duplicate_event', 'insufficient_shadow_cash']
    assert result['comparison']['improvement'] is None


def test_common_cooldown_blocks_pyramid_and_va(single_plan, monkeypatch):
    holding, _, cfg, generate = single_plan
    holding['pyramid_state'] = dict(cycle_id='c', initialized_at='2026-01-01', executed_tiers=[])
    cfg['va_enabled'] = True
    monkeypatch.setattr(planner, '_check_cooldown', lambda *a: (False, 2, 100, '冷却期'))
    result = generate()
    assert result['final_suggested_amount'] == 0
    assert result['safety']['can_add'] is False


def test_executed_pyramid_tier_is_not_repeated(single_plan):
    holding, _, _, generate = single_plan
    holding['pyramid_state'] = dict(cycle_id='c', initialized_at='2026-01-01', executed_tiers=[-10])
    result = generate()
    assert result['pyramid']['released_amount'] == 0
    assert result['pyramid']['next_trigger']['loss_pct'] == -20


def test_config_endpoint_rejects_entire_invalid_patch_without_partial_save():
    import asyncio
    from db.config import get_config, _CONFIG_CACHE
    from routers.analysis.smart_add import update_config
    from fastapi import HTTPException
    before = get_config('smart_add.pool_pct')
    with pytest.raises(HTTPException) as err:
        asyncio.run(update_config({'smart_add.pool_pct': 20, 'smart_add.grid_count': 0}))
    _CONFIG_CACHE.pop('smart_add.pool_pct', None)
    assert get_config('smart_add.pool_pct') == before
    assert err.value.status_code == 422


def test_planner_reserves_previous_additions_for_same_index(monkeypatch):
    from services.portfolio import decision_guardrails as guard
    from datetime import datetime
    funds = [dict(fund_code=code, fund_name=code, index_code='I', fund_category='index', shares=1000,
                  current_price=1, current_value=1000, price_updated_at=date.today().isoformat()) for code in ('A', 'B')]
    ctx = guard.build_decision_snapshot(snapshot=dict(funds=funds, cash_balance=8000, valuation_complete=True),
        cashflow=dict(data_available=True), policy=dict(confirmed=True, max_single_position_pct=100,
        max_same_index_pct=30, max_equity_pct=100, max_stress_loss_pct=100, estimated_fee_rate=0),
        valuation_facts=dict(I=dict(percentile=20, snapshot_date=date.today().isoformat())))
    monkeypatch.setattr(guard, 'build_decision_snapshot', lambda **kw: ctx)
    monkeypatch.setattr(planner, '_load_config', lambda: dict(enabled=True, pool_pct=100, base_dca_pct=4,
        max_add_vs_position_mult=1, exit_signal_enabled=False))
    monkeypatch.setattr(planner, 'get_portfolio_summary', lambda **kw: dict(total_assets=10000, holdings=funds))
    monkeypatch.setattr(planner, '_apply_goal_aware_adjustments', lambda *a: {})
    monkeypatch.setattr(planner, '_generate_single_plan', lambda h, *a, **kw: dict(**h, profit_rate=0,
        effective_base=1000, final_suggested_amount=800, safety=dict(can_add=True, max_position_pct=100),
        triggered_signals=[dict(triggered=True, type='dip', amount=800)],
        position_sizing=dict(cash_constraint=dict(data_available=True, usable_cash=8000))))
    monkeypatch.setattr(planner, '_apply_correlation_downweight', lambda *a: None)
    monkeypatch.setattr(planner, '_get_current_month_buy_amount', lambda *a: 0)
    monkeypatch.setattr(planner, '_calc_rebalance_suggestion', lambda *a: [])
    result = planner.generate_smart_add_plan()
    assert sum(p['final_suggested_amount'] for p in result['plans']) == 1000
    assert ctx['proposed_additions'] == {'A': 800, 'B': 200}
    assert ctx['cash']['available_now'] == 8000


def test_counterfactual_execution_window_and_future_nav_are_bounded():
    from db._conn import _get_conn
    from services import counterfactual_verifier as verifier
    from datetime import timedelta
    today = date.today()
    conn = _get_conn()
    conn.executemany('INSERT INTO fund_nav_history(fund_code,nav_date,nav) VALUES(?,?,?)',
        [('OLD', (today - timedelta(days=1)).isoformat(), 2),
         ('FUTURE', (today + timedelta(days=1)).isoformat(), 3),
         ('FUTURE', (today - timedelta(days=1)).isoformat(), 1)])
    conn.commit(); conn.close()
    assert verifier._get_next_nav_after('OLD', (today - timedelta(days=90)).isoformat()) == (None, None)
    assert verifier._get_next_nav_after('FUTURE', today.isoformat()) == (None, None)
    assert verifier._get_latest_fund_price('FUTURE')[0] == 1


def test_fully_triggered_pyramid_has_no_future_tier(single_plan):
    holding, _, _, generate = single_plan
    holding['profit_rate'] = -.5
    result = generate()
    assert result['pyramid']['next_trigger'] is None


@pytest.mark.parametrize('drop,budget', [(5, 1000), (0, 1000), (-5, 0)])
def test_cash_scenarios_keep_equal_funding_for_rising_flat_and_empty_budget(drop, budget):
    from services.advisor.smart_add_governance import simulate_cash_strategies
    result = simulate_cash_strategies(dict(current_price=1, shares=1000, total_cost=1000), drop, 6, 100, budget, .01)
    for scenario in result['strategies']:
        assert scenario['initial_wealth'] == 1000 + budget
        assert scenario['total_added'] <= budget
        assert scenario['remaining_cash'] >= 0
        assert scenario['fees'] <= scenario['total_added']
        assert scenario['final_wealth'] == pytest.approx(scenario['final_value'] + scenario['remaining_cash'], abs=.02)


def test_shadow_total_waits_for_common_valuation_date(monkeypatch):
    import json
    from db import smart_add_snapshots
    from services import counterfactual_verifier as verifier
    rows = [dict(id=i, fund_code=code, amount=20, transaction_date='2026-01-01',
        notes=json.dumps(dict(mode='suggestion_tracking', shadow_cash_budget=100, event_key=code)))
        for i, code in enumerate(('A', 'B'), 1)]
    monkeypatch.setattr(smart_add_snapshots, 'list_hypothetical_txs', lambda **kw: rows)
    monkeypatch.setattr(verifier, '_get_next_nav_after', lambda *a: (1, '2026-01-02'))
    monkeypatch.setattr(verifier, '_get_latest_fund_price', lambda code: (1.1, '2026-01-03' if code == 'A' else '2026-01-04'))
    result = verifier.verify_all_hypothetical()
    assert result['summary']['status'] == 'waiting_common_valuation_date'
    assert result['summary']['total_shadow_wealth'] is None
    assert result['summary']['total_profit_rate'] is None
    assert result['summary']['remaining_shadow_cash'] == 60
