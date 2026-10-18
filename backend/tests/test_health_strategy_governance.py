"""健康分与回测治理契约；仅临时库和替代行情，不调用外部服务。"""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize(('equity_nav', 'expected_total'), [(2.0, 1820.0), (0.5, 620.0)])
def test_two_eight_defers_rebalance_until_real_bond_quote_exists(monkeypatch, equity_nav, expected_total):
    from services.strategy import strategy_library as library
    monkeypatch.setattr(library, '_fetch_fund_nav_series', lambda code: [
        {'date': '2026-01-31', 'nav': 1.0},
        {'date': '2026-02-02', 'nav': 1.1},
    ])
    result = library.TwoEightStrategy({
        'bond_code': '000001', 'equity_ratio': 0.8, 'cost_enabled': False,
    }).run([
        {'date': '2026-01-31', 'nav': 1.0},
        {'date': '2026-02-01', 'nav': equity_nav},
        {'date': '2026-02-02', 'nav': equity_nav},
        {'date': '2026-02-03', 'nav': equity_nav},
    ], 1000)
    # 缺债基报价日不交易；恢复报价后以原有200份债基计价，再完成当月再平衡。
    assert result['nav_curve'][1]['value'] == pytest.approx(800 * equity_nav + 200)
    assert result['final_value'] == pytest.approx(expected_total)
    assert result['trades'] == 2


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    import db._conn as conn
    monkeypatch.setattr(conn, 'DB_PATH', tmp_path / 'health-strategy.db')
    from db.health_score import init_health_score_tables
    with conn._get_conn() as c:
        init_health_score_tables(c)


@pytest.fixture
def health(monkeypatch):
    from routers.analysis import health_score as h
    monkeypatch.setattr(h, 'list_holdings', lambda: [])
    monkeypatch.setattr(h, '_get_trade_records', lambda: [])
    monkeypatch.setattr(h, 'get_config', lambda key, default=None: 'false')
    monkeypatch.setattr(h, '_call_llm', Mock(side_effect=AssertionError('no live LLM')))
    monkeypatch.setattr(h, '_HEALTH_SCORE_CACHE', {'data': None, 'ts': 0})
    return h


def test_today_is_read_only_when_no_score(health, monkeypatch):
    calculate = Mock(side_effect=AssertionError('GET must not calculate'))
    monkeypatch.setattr(health, 'calc_health_score', calculate)
    assert asyncio.run(health.get_today_score()) == {'status': 'empty', 'result': None}
    calculate.assert_not_called()


def test_saved_score_has_same_dto_and_overwrites_one_day(health):
    from db.health_score import save_health_score, get_health_score, list_health_scores
    save_health_score('2026-10-18', 750, 150, 160, 170, 140, 130, ['建议'], {'quality': {'reason': '证据'}})
    save_health_score('2026-10-18', 760, 160, 160, 170, 140, 130, ['更新'], {})
    row = get_health_score('2026-10-18')
    assert row['scores']['quality'] == 160
    assert row['advice'] == ['更新']
    assert len(list_health_scores()) == 1


def test_valuation_preserves_real_zero_and_reads_metric_percentile(health):
    assert health.calc_valuation_score([{'valuation': {'metric_type': '市盈率', 'percentile': 0}, 'market_value': 100}])[0] == 200
    assert health.calc_valuation_score([{'valuation': {'metric_type': '市盈率', 'percentile': 95}, 'market_value': 100}])[0] == 30


def test_valuation_mapping_uses_index_code_and_pe_pb(health, monkeypatch):
    monkeypatch.setattr(health, 'list_valuation_indexes', lambda: [])
    calls = []
    def get(code, metric_type=None, **kwargs):
        calls.append((code, metric_type))
        return {'metric_type': metric_type, 'percentile': 90 if metric_type == '市盈率' else 80, 'snapshot_date': datetime.now().date().isoformat()}
    monkeypatch.setattr(health, 'get_latest_valuation', get)
    result = health._get_holding_valuations([{'fund_code': 'fund', 'fund_name': '完全不同名称', 'index_code': '000300'}])
    assert result[0]['valuation']['pe_percentile'] == 90
    assert result[0]['valuation']['pb_percentile'] == 80
    assert ('000300', '市盈率') in calls


def test_equity_ratio_uses_market_value(health):
    holdings = [{'fund_type': '股票型', 'market_value': 990000}, {'fund_type': '债券型', 'market_value': 10000}]
    assert health.calc_diversification_score(holdings)[1]['equity_ratio'] == 99


def test_empty_data_is_not_a_healthy_score(health):
    result = health._calc_health_score_sync()
    assert result['total_score'] is None
    assert result['data_quality']['status'] == 'insufficient'
    assert not any('健康' in a or '继续保持' in a for a in result['advice'])


def test_force_recalculate_bypasses_cache_and_errors_propagate(health, monkeypatch):
    monkeypatch.setattr(health, '_HEALTH_SCORE_CACHE', {'data': {'total_score': 700}, 'ts': __import__('time').time()})
    monkeypatch.setattr(health, '_calc_health_score_sync', lambda: {'total_score': 800})
    assert asyncio.run(health.calc_health_score(force=True))['total_score'] == 800
    monkeypatch.setattr(health, '_calc_health_score_sync', Mock(side_effect=RuntimeError('source unavailable')))
    with pytest.raises(RuntimeError, match='source unavailable'):
        asyncio.run(health.calc_health_score(force=True))


def test_market_outage_is_not_neutral(health, monkeypatch):
    import sys
    def fail(*args, **kwargs):
        raise RuntimeError('offline')
    monkeypatch.setitem(sys.modules, 'akshare', SimpleNamespace(**{name: fail for name in ['stock_zh_index_daily', 'stock_zh_a_spot_em', 'stock_hsgt_north_net_flow_in_em', 'stock_margin_sz']}))
    result = health._calc_fear_greed_index_sync()
    assert result['score'] is None
    assert result['data_quality']['status'] == 'insufficient'
    assert '正常' not in result['advice']


def test_fed_requires_pe_and_real_bond_yield(health, monkeypatch):
    import db.health_score as dbh
    calls = []
    def get(code, metric_type=None, **kwargs):
        calls.append(metric_type)
        return None if metric_type == '市盈率' else {'metric_type': '市净率', 'current_value': 1.5}
    monkeypatch.setattr(health, 'get_latest_valuation', get)
    monkeypatch.setattr(dbh, 'get_latest_bond_yield', lambda: None)
    result = health._calc_stock_bond_ratio_sync()
    assert result.get('error')
    assert set(calls) == {'市盈率'}


@pytest.fixture
def strategy(monkeypatch):
    from services.strategy import strategy_library as s
    monkeypatch.setattr(s, 'save_backtest', Mock(return_value=7))
    monkeypatch.setattr(s, 'get_config', lambda *args: 'true', raising=False)
    return s


def prices(count=120, nav=1):
    return [{'date': (datetime(2025, 1, 1) + timedelta(days=i)).date().isoformat(), 'nav': nav} for i in range(count)]


def test_dca_inflows_do_not_create_returns_or_volatility(strategy):
    r = strategy.DCAStrategy({'amount': 1000, 'interval_days': 30, 'cost_enabled': False}).run(prices(), 10000)
    assert r['total_return'] == 0
    assert r['sharpe_ratio'] == 0
    assert r['volatility'] == 0
    assert all(p['value'] == 1 for p in r['unit_nav_curve'])


def test_dca_inflows_do_not_hide_drawdowns(strategy):
    r = strategy.DCAStrategy({'amount': 1000, 'interval_days': 1, 'cost_enabled': False}).run([{'date': '2025-01-01', 'nav': 1}, {'date': '2025-02-01', 'nav': .9}], 1000)
    assert r['max_drawdown'] == pytest.approx(.1)
    assert r['total_return'] == pytest.approx(-.1)
    assert r['investor_return'] == pytest.approx(-.0667)


def test_dca_interval_is_calendar_days(strategy):
    series = [{'date': f'2025-01-{d:02d}', 'nav': 1} for d in [1, 8, 15]]
    assert len(strategy.DCAStrategy({'interval_days': 7}).generate_signals(series)) == 3


@pytest.mark.parametrize('params', [{'grid_pct': 0}, {'grid_steps': 0}])
def test_invalid_grid_parameters_are_rejected(strategy, params):
    with pytest.raises(ValueError):
        strategy.run_backtest('grid', 'x', params)


def test_core_satellite_rejects_unimplemented_multi_asset_parameters(strategy):
    with pytest.raises(ValueError, match='core_code|satellites'):
        strategy.run_backtest('core_satellite', '000300', {'core_code': '000905'})


def test_strategy_switch_blocks_before_fetch(strategy, monkeypatch):
    monkeypatch.setattr(strategy, 'get_config', lambda key, default: 'false', raising=False)
    fetch = Mock(side_effect=AssertionError('must not fetch'))
    monkeypatch.setattr(strategy, '_fetch_nav_series', fetch)
    with pytest.raises(ValueError, match='关闭'):
        strategy.run_backtest('dca', 'x', {})
    fetch.assert_not_called()


def test_save_failure_is_explicit_and_does_not_lose_calculation(strategy, monkeypatch):
    monkeypatch.setattr(strategy, 'get_config', lambda *args: 'true', raising=False)
    monkeypatch.setattr(strategy, '_fetch_nav_series', lambda *a, **k: prices())
    monkeypatch.setattr(strategy, '_fetch_benchmark_series', lambda *a, **k: [])
    monkeypatch.setattr(strategy, 'save_backtest', Mock(side_effect=RuntimeError('disk full')))
    result = strategy.run_backtest('dca', 'x', {'cost_enabled': False})
    assert result['status'] == 'ok'
    assert result['save_status'] == 'error'
    assert result['save_error']


def test_sweep_has_combination_limit(strategy):
    with pytest.raises(ValueError):
        strategy._expand_param_combinations({'amount': list(range(101)), 'interval_days': list(range(101))})


def test_known_fund_has_single_correct_benchmark(strategy):
    assert strategy._fund_to_index_code('161022') == '000905'


def test_equity_ratio_supports_real_portfolio_columns(health):
    _, detail = health.calc_diversification_score([
        {'fund_category': 'equity', 'current_value': 990000},
        {'fund_category': 'bond_pure', 'current_value': 10000},
    ])
    assert detail['equity_ratio'] == 99


def test_failed_background_health_task_does_not_become_done(health, monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(health, 'get_running_async_task', lambda *a: None)
    monkeypatch.setattr(health, 'create_async_task', lambda *a, **k: 76)
    update = Mock()
    monkeypatch.setattr(health, 'update_async_task', update)
    calculate = AsyncMock(side_effect=RuntimeError('行情不可用'))
    monkeypatch.setattr(health, 'calc_health_score', calculate)
    async def invoke():
        response = await health.calculate_health_score()
        await asyncio.gather(*list(health._background_tasks))
        return response
    assert asyncio.run(invoke())['status'] == 'running'
    calculate.assert_awaited_once_with(force=True)
    assert update.call_args.kwargs['status'] == 'error'


def test_sweep_route_passes_only_validated_request_arguments(strategy, monkeypatch):
    from routers.analysis import strategy_backtest as route
    run = Mock(return_value=[])
    monkeypatch.setattr(route, 'parameter_sweep', run)
    request = route.SweepRequest(strategy='dca', target_code='000300', param_ranges={'amount': [1000]})
    asyncio.run(route.sweep(request))
    run.assert_called_once_with('dca', '000300', {'amount': [1000]}, None, None, 100000, 'index')


@pytest.mark.parametrize('change', [{'initial_cash': 0}, {'target_type': 'stock'},
                                  {'params': {'grid_pct': 0}}, {'params': {'equity_ratio': 2}},
                                  {'start_date': '2025-02-30'}, {'start_date': '2025-02-01', 'end_date': '2025-01-01'}])
def test_http_request_rejects_invalid_backtest_parameters(change):
    from routers.analysis.strategy_backtest import BacktestRequest
    payload = {'strategy': 'grid' if 'grid_pct' in change.get('params', {}) else 'two_eight', 'target_code': '000300', **change}
    with pytest.raises(ValueError):
        BacktestRequest(**payload)


def test_backtest_history_retains_cashflow_adjusted_curve(strategy):
    from db.backtest_results import init_backtest_tables, save_backtest, get_backtest
    from db._conn import _get_conn
    with _get_conn() as conn:
        init_backtest_tables(conn)
    result = strategy.DCAStrategy({'cost_enabled': False}).run(prices(), 10000)
    id_ = save_backtest('test', '000300', 'index', 'dca', {}, result, {}, 4)
    saved = get_backtest(id_)
    assert saved['unit_nav_curve'] == result['unit_nav_curve']
    assert saved['investor_return'] == result['investor_return']
    assert saved['return_method'] == 'time_weighted_unit_nav'


def test_fed_rejects_expired_bond_cache(health, monkeypatch):
    import sys
    import db.health_score as dbh
    monkeypatch.setattr(health, 'get_latest_valuation', lambda *a, **kw: {'metric_type': '市盈率', 'current_value': 10})
    monkeypatch.setattr(dbh, 'get_latest_bond_yield', lambda: {'trade_date': '2020-01-01', 'yield_10y': 2.5})
    monkeypatch.setitem(sys.modules, 'akshare', SimpleNamespace(bond_zh_us_rate=Mock(side_effect=RuntimeError('offline'))))
    assert health._calc_stock_bond_ratio_sync().get('error')


def test_fed_uses_sub_one_percent_yield_as_percentage_points(health, monkeypatch):
    import db.health_score as dbh
    monkeypatch.setattr(health, 'get_latest_valuation', lambda *a, **kw: {'metric_type': '市盈率', 'current_value': 10, 'percentile': 50})
    monkeypatch.setattr(dbh, 'get_latest_bond_yield', lambda: {'trade_date': datetime.now().date().isoformat(), 'yield_10y': .8})
    r = health._calc_stock_bond_ratio_sync()
    assert r['spread'] == pytest.approx(9.2)
    assert r['bond_yield_10y'] == .8


def test_effective_strategy_params_are_saved(strategy, monkeypatch):
    monkeypatch.setattr(strategy, '_fetch_nav_series', lambda *a, **kw: prices())
    monkeypatch.setattr(strategy, '_fetch_benchmark_series', lambda *a, **kw: [])
    r = strategy.run_backtest('dca', 'x', {'cost_enabled': False})
    assert r['params']['interval_days'] == 30
    assert strategy.save_backtest.call_args.kwargs['params'] == r['params']


def test_backtest_http_roundtrip_and_disabled_gate(strategy, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.analysis.strategy_backtest import router
    monkeypatch.setattr(strategy, '_fetch_nav_series', lambda *a, **kw: prices())
    monkeypatch.setattr(strategy, '_fetch_benchmark_series', lambda *a, **kw: [])
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        payload = {'strategy': 'dca', 'target_code': '000300', 'params': {'cost_enabled': False}}
        result = client.post('/api/analysis/strategy/backtest', json=payload)
        assert result.status_code == 200
        assert result.json()['return_method'] == 'time_weighted_unit_nav'
        assert result.json()['save_status'] == 'saved'
        assert result.json()['benchmark_return'] is None
        assert client.post('/api/analysis/strategy/backtest', json={**payload, 'initial_cash': -1}).status_code == 422
        monkeypatch.setattr(strategy, 'get_config', lambda *a: 'false')
        assert client.post('/api/analysis/strategy/backtest', json=payload).status_code == 400
