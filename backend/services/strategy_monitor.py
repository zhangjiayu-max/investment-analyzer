"""策略监控服务 — 定时检查策略触发条件，执行交易。"""

import json
import logging
from datetime import datetime, timedelta

from db import (
    create_strategy_monitoring, get_strategy_monitoring, list_strategy_monitoring,
    update_strategy_monitoring, delete_strategy_monitoring,
    create_strategy_trade, list_strategy_trades,
    get_best_valuation, get_portfolio_summary, get_cash_balance,
)

logger = logging.getLogger(__name__)


def create_monitor(strategy_name, strategy_type, target_code, parameters=None):
    return create_strategy_monitoring(
        backtest_id=None,
        strategy_name=strategy_name,
        strategy_type=strategy_type,
        target_code=target_code,
        parameters=parameters or {},
    )


def run_strategy_check(monitoring_id):
    monitor = get_strategy_monitoring(monitoring_id)
    if not monitor:
        return None

    params = json.loads(monitor.get('parameters', '{}'))
    strategy_type = monitor.get('strategy_type')
    target_code = monitor.get('target_code')

    if strategy_type == 'valuation_dca':
        return _check_valuation_dca(monitoring_id, target_code, params)
    elif strategy_type == 'percentile_trade':
        return _check_percentile_trade(monitoring_id, target_code, params)
    elif strategy_type == 'rebalance':
        return _check_rebalance(monitoring_id, target_code, params)

    return None


def _check_valuation_dca(monitoring_id, target_code, params):
    valuation = get_best_valuation(target_code)
    pct = valuation.get('percentile', 50) if valuation else 50

    buy_threshold = params.get('buy_threshold', 40)
    monthly_amount = params.get('monthly_amount', 1000)
    frequency = params.get('frequency', 30)

    if pct < buy_threshold:
        multiplier = _get_valuation_multiplier(pct)
        amount = monthly_amount * multiplier

        trade_id = create_strategy_trade(
            monitoring_id=monitoring_id,
            trade_type='buy',
            fund_code=target_code,
            amount=amount,
        )

        next_trigger = (datetime.now() + timedelta(days=frequency)).strftime('%Y-%m-%d %H:%M:%S')
        update_strategy_monitoring(
            monitoring_id,
            last_trigger_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            next_trigger_at=next_trigger,
        )

        logger.info(f"估值定投策略触发: target={target_code}, pct={pct}, amount={amount}")
        return {'triggered': True, 'trade_id': trade_id, 'amount': amount}

    return {'triggered': False}


def _check_percentile_trade(monitoring_id, target_code, params):
    valuation = get_best_valuation(target_code)
    pct = valuation.get('percentile', 50) if valuation else 50

    buy_threshold = params.get('buy_threshold', 20)
    sell_threshold = params.get('sell_threshold', 80)
    buy_amount = params.get('buy_amount', 1000)
    sell_ratio = params.get('sell_ratio', 0.5)

    portfolio = get_portfolio_summary()
    holdings = portfolio.get('holdings', {})
    position = holdings.get(target_code)

    if pct < buy_threshold:
        trade_id = create_strategy_trade(
            monitoring_id=monitoring_id,
            trade_type='buy',
            fund_code=target_code,
            amount=buy_amount,
        )
        update_strategy_monitoring(
            monitoring_id,
            last_trigger_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        )
        logger.info(f"分位策略买入触发: target={target_code}, pct={pct}, amount={buy_amount}")
        return {'triggered': True, 'trade_id': trade_id, 'action': 'buy'}

    elif pct > sell_threshold and position:
        sell_amount = position.get('current_value', 0) * sell_ratio
        trade_id = create_strategy_trade(
            monitoring_id=monitoring_id,
            trade_type='sell',
            fund_code=target_code,
            amount=sell_amount,
        )
        update_strategy_monitoring(
            monitoring_id,
            last_trigger_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        )
        logger.info(f"分位策略卖出触发: target={target_code}, pct={pct}, amount={sell_amount}")
        return {'triggered': True, 'trade_id': trade_id, 'action': 'sell'}

    return {'triggered': False}


def _check_rebalance(monitoring_id, target_code, params):
    portfolio = get_portfolio_summary()
    holdings = portfolio.get('holdings', {})
    position = holdings.get(target_code)

    if not position:
        return {'triggered': False}

    target_ratio = params.get('target_ratio', 0.2)
    tolerance = params.get('tolerance', 0.05)
    total_value = portfolio.get('total_value', 0)

    if total_value <= 0:
        return {'triggered': False}

    current_ratio = position.get('current_value', 0) / total_value
    deviation = abs(current_ratio - target_ratio)

    if deviation > tolerance:
        target_amount = total_value * target_ratio
        current_amount = position.get('current_value', 0)
        diff = target_amount - current_amount

        if diff > 0:
            trade_type = 'buy'
            amount = diff
        else:
            trade_type = 'sell'
            amount = abs(diff)

        trade_id = create_strategy_trade(
            monitoring_id=monitoring_id,
            trade_type=trade_type,
            fund_code=target_code,
            amount=amount,
        )
        update_strategy_monitoring(
            monitoring_id,
            last_trigger_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        )
        logger.info(f"再平衡策略触发: target={target_code}, ratio={current_ratio:.2f}, action={trade_type}, amount={amount}")
        return {'triggered': True, 'trade_id': trade_id, 'action': trade_type, 'amount': amount}

    return {'triggered': False}


def _get_valuation_multiplier(percentile):
    if percentile < 20:
        return 2.0
    elif percentile < 40:
        return 1.5
    elif percentile < 60:
        return 1.0
    elif percentile < 80:
        return 0.5
    else:
        return 0.2


def get_monitor_stats(monitoring_id):
    monitor = get_strategy_monitoring(monitoring_id)
    if not monitor:
        return None

    trades = list_strategy_trades(monitoring_id)
    total_buy = sum(t['amount'] for t in trades if t['trade_type'] == 'buy')
    total_sell = sum(t['amount'] for t in trades if t['trade_type'] == 'sell')

    return {
        'monitor_id': monitoring_id,
        'strategy_name': monitor.get('strategy_name'),
        'strategy_type': monitor.get('strategy_type'),
        'target_code': monitor.get('target_code'),
        'current_state': monitor.get('current_state'),
        'trade_count': len(trades),
        'total_buy_amount': total_buy,
        'total_sell_amount': total_sell,
        'last_trigger_at': monitor.get('last_trigger_at'),
        'next_trigger_at': monitor.get('next_trigger_at'),
    }
