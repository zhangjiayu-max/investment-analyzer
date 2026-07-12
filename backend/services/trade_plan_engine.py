"""交易计划引擎 — 从建议生成完整交易计划（金额、时机、分批策略、止损止盈）。"""

import logging
from decimal import Decimal, ROUND_HALF_UP

from db import (
    create_trade_plan, list_pending_trade_plans, get_recommendation,
    get_portfolio_summary, get_cash_balance, get_best_valuation,
    classify_fund_category,
)


logger = logging.getLogger(__name__)


def _round_decimal(value, places=2):
    if value is None:
        return None
    return float(Decimal(str(value)).quantize(Decimal(f'0.{"0"*places}'), rounding=ROUND_HALF_UP))


def generate_trade_plan(recommendation_id):
    recommendation = get_recommendation(recommendation_id)
    if not recommendation:
        return None

    target = recommendation.get('target', '')
    action = recommendation.get('action', 'BUY')
    confidence = recommendation.get('confidence', 0.5)
    target_fund_code = recommendation.get('target_fund_code', '')
    target_fund_name = recommendation.get('target_fund_name', '')

    portfolio_summary = get_portfolio_summary()
    cash_balance = get_cash_balance()

    amount = _calculate_trade_amount(
        action, confidence, target, target_fund_code,
        portfolio_summary, cash_balance
    )

    batch_count, batch_interval = _determine_batch_strategy(
        confidence, action, target_fund_code
    )

    stop_loss_pct, take_profit_pct = _calculate_stop_loss_take_profit(
        action, confidence, target_fund_code
    )

    plan_id = create_trade_plan(
        recommendation_id=recommendation_id,
        fund_code=target_fund_code or target,
        fund_name=target_fund_name or target,
        action=action,
        amount=amount,
        batch_count=batch_count,
        batch_interval_days=batch_interval,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
    )

    logger.info(f"交易计划生成成功: plan_id={plan_id}, action={action}, amount={amount}")
    return plan_id


def _calculate_trade_amount(action, confidence, target, fund_code,
                            portfolio_summary, cash_balance):
    total_assets = portfolio_summary.get('total_value', 0) + cash_balance.get('balance', 0)

    if action.upper() == 'BUY':
        max_invest_pct = {
            'LOW': 0.02,
            'MEDIUM': 0.05,
            'HIGH': 0.10,
        }.get(confidence.upper() if isinstance(confidence, str) else '', 0.05)

        if isinstance(confidence, float):
            max_invest_pct = min(confidence * 0.15, 0.15)

        available_cash = cash_balance.get('balance', 0)
        max_amount = min(available_cash, total_assets * max_invest_pct)

        if fund_code:
            holding = portfolio_summary.get('holdings', {}).get(fund_code)
            if holding:
                current_ratio = holding.get('current_value', 0) / total_assets if total_assets > 0 else 0
                remaining_ratio = max_invest_pct - current_ratio
                if remaining_ratio > 0:
                    max_amount = min(max_amount, total_assets * remaining_ratio)
                else:
                    max_amount = 0

        return _round_decimal(max_amount)

    elif action.upper() == 'SELL':
        holding = None
        if fund_code:
            holding = portfolio_summary.get('holdings', {}).get(fund_code)
        elif portfolio_summary.get('holdings'):
            for code, h in portfolio_summary['holdings'].items():
                if target in h.get('fund_name', '') or code == target:
                    holding = h
                    break

        if holding:
            confidence_pct = confidence if isinstance(confidence, float) else 0.5
            sell_pct = 0.3 + (confidence_pct - 0.5) * 0.4
            sell_pct = max(0.1, min(1.0, sell_pct))
            amount = holding.get('current_value', 0) * sell_pct
            return _round_decimal(amount)

    return 0


def _determine_batch_strategy(confidence, action, fund_code):
    if action.upper() == 'SELL':
        return 1, 0

    confidence_val = confidence if isinstance(confidence, float) else 0.5

    if confidence_val >= 0.8:
        return 1, 0
    elif confidence_val >= 0.6:
        return 2, 7
    elif confidence_val >= 0.4:
        return 3, 7
    else:
        return 4, 14


def _calculate_stop_loss_take_profit(action, confidence, fund_code):
    fund_category = classify_fund_category('', fund_code=fund_code) if fund_code else 'equity'

    if action.upper() == 'BUY':
        base_stop_loss = {
            'equity': 0.10,
            'bond': 0.03,
            'mixed': 0.06,
        }.get(fund_category, 0.10)

        base_take_profit = {
            'equity': 0.20,
            'bond': 0.05,
            'mixed': 0.12,
        }.get(fund_category, 0.20)

        confidence_val = confidence if isinstance(confidence, float) else 0.5
        confidence_factor = 1 - (confidence_val - 0.5) * 0.3

        stop_loss = base_stop_loss * confidence_factor
        take_profit = base_take_profit / confidence_factor

        return _round_decimal(stop_loss, 4), _round_decimal(take_profit, 4)

    return None, None


def get_pending_trade_plans_summary():
    plans = list_pending_trade_plans()
    summary = {
        'total_plans': len(plans),
        'total_amount': sum(p.get('amount', 0) for p in plans),
        'buy_plans': [p for p in plans if p.get('action', 'BUY').upper() == 'BUY'],
        'sell_plans': [p for p in plans if p.get('action', 'BUY').upper() == 'SELL'],
    }
    return summary
