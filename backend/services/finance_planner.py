"""家庭财务规划服务 — 现金流预测、资产配置建议、风险压力测试。"""

import json
import logging
from datetime import datetime, timedelta

from db import (
    get_portfolio_summary, get_cash_balance, list_goal_buckets,
    list_holdings, list_recommendations,
)

logger = logging.getLogger(__name__)


def forecast_cash_flow(months=12):
    """预测未来现金流。"""
    portfolio = get_portfolio_summary()
    cash = get_cash_balance()
    buckets = list_goal_buckets()

    monthly_contribution = 0
    for bucket in buckets:
        if bucket.get('bucket_type') in ['long_term', 'opportunity']:
            target_amount = bucket.get('target_amount', 0)
            current_amount = bucket.get('current_amount', 0)
            monthly_contribution += max(0, (target_amount - current_amount) / 36)

    monthly_contribution = min(monthly_contribution, cash / 6)

    forecast = []
    current_cash = cash
    current_portfolio = portfolio.get('total_value', 0)

    for i in range(months):
        month_date = (datetime.now() + timedelta(days=i * 30)).strftime('%Y-%m')

        expected_return = current_portfolio * 0.06 / 12
        current_portfolio += expected_return
        current_cash += monthly_contribution

        forecast.append({
            'month': month_date,
            'cash_balance': round(current_cash, 2),
            'portfolio_value': round(current_portfolio, 2),
            'total_assets': round(current_cash + current_portfolio, 2),
            'monthly_contribution': round(monthly_contribution, 2),
        })

    return forecast


def generate_allocation_suggestion():
    """基于目标的资产配置建议。"""
    portfolio = get_portfolio_summary()
    holdings = portfolio.get('holdings', {})
    total_value = portfolio.get('total_value', 0)

    equity_value = 0
    bond_value = 0
    money_value = 0

    for fund_code, h in holdings.items():
        category = h.get('fund_category', 'equity')
        current_value = h.get('current_value', 0)
        if category == 'equity' or category == 'index':
            equity_value += current_value
        elif category == 'bond':
            bond_value += current_value
        elif category == 'money':
            money_value += current_value

    equity_ratio = equity_value / total_value if total_value > 0 else 0
    bond_ratio = bond_value / total_value if total_value > 0 else 0
    money_ratio = money_value / total_value if total_value > 0 else 0

    buckets = list_goal_buckets()
    target_ratios = {'equity': 0.5, 'bond': 0.3, 'money': 0.2}

    for bucket in buckets:
        bucket_type = bucket.get('bucket_type')
        if bucket_type == 'emergency':
            target_ratios['money'] += 0.1
            target_ratios['equity'] -= 0.05
            target_ratios['bond'] -= 0.05
        elif bucket_type == 'long_term':
            target_ratios['equity'] += 0.1
            target_ratios['bond'] -= 0.05
            target_ratios['money'] -= 0.05

    suggestions = []

    if equity_ratio > target_ratios['equity'] + 0.1:
        suggestions.append({
            'asset_class': 'equity',
            'current_ratio': round(equity_ratio, 2),
            'target_ratio': round(target_ratios['equity'], 2),
            'suggestion': '减持',
            'reason': f"权益类资产占比{equity_ratio*100:.0f}%，超出目标{target_ratios['equity']*100:.0f}%",
        })
    elif equity_ratio < target_ratios['equity'] - 0.1:
        suggestions.append({
            'asset_class': 'equity',
            'current_ratio': round(equity_ratio, 2),
            'target_ratio': round(target_ratios['equity'], 2),
            'suggestion': '增持',
            'reason': f"权益类资产占比{equity_ratio*100:.0f}%，低于目标{target_ratios['equity']*100:.0f}%",
        })

    if bond_ratio > target_ratios['bond'] + 0.05:
        suggestions.append({
            'asset_class': 'bond',
            'current_ratio': round(bond_ratio, 2),
            'target_ratio': round(target_ratios['bond'], 2),
            'suggestion': '减持',
            'reason': f"债券类资产占比{bond_ratio*100:.0f}%，超出目标{target_ratios['bond']*100:.0f}%",
        })
    elif bond_ratio < target_ratios['bond'] - 0.05:
        suggestions.append({
            'asset_class': 'bond',
            'current_ratio': round(bond_ratio, 2),
            'target_ratio': round(target_ratios['bond'], 2),
            'suggestion': '增持',
            'reason': f"债券类资产占比{bond_ratio*100:.0f}%，低于目标{target_ratios['bond']*100:.0f}%",
        })

    return {
        'current_allocation': {
            'equity': round(equity_ratio, 2),
            'bond': round(bond_ratio, 2),
            'money': round(money_ratio, 2),
            'total_value': round(total_value, 2),
        },
        'target_allocation': target_ratios,
        'suggestions': suggestions,
    }


def stress_test(scenario='moderate'):
    """风险压力测试。"""
    portfolio = get_portfolio_summary()
    holdings = portfolio.get('holdings', {})
    total_value = portfolio.get('total_value', 0)

    scenario_params = {
        'mild': {'equity_drop': 0.1, 'bond_drop': 0.02, 'duration': '1个月'},
        'moderate': {'equity_drop': 0.25, 'bond_drop': 0.05, 'duration': '3个月'},
        'severe': {'equity_drop': 0.4, 'bond_drop': 0.1, 'duration': '6个月'},
    }

    params = scenario_params.get(scenario, scenario_params['moderate'])

    equity_value = 0
    other_value = 0

    for fund_code, h in holdings.items():
        category = h.get('fund_category', 'equity')
        current_value = h.get('current_value', 0)
        if category == 'equity' or category == 'index':
            equity_value += current_value
        else:
            other_value += current_value

    stressed_equity = equity_value * (1 - params['equity_drop'])
    stressed_other = other_value * (1 - params['bond_drop'])
    stressed_total = stressed_equity + stressed_other
    max_drawdown = (total_value - stressed_total) / total_value if total_value > 0 else 0

    return {
        'scenario': scenario,
        'duration': params['duration'],
        'assumptions': {
            'equity_market_drop': f"-{params['equity_drop']*100:.0f}%",
            'bond_market_drop': f"-{params['bond_drop']*100:.0f}%",
        },
        'impact': {
            'original_total': round(total_value, 2),
            'stressed_total': round(stressed_total, 2),
            'loss_amount': round(total_value - stressed_total, 2),
            'max_drawdown': round(max_drawdown, 4),
            'drawdown_percent': f"-{max_drawdown*100:.1f}%",
        },
        'recovery': {
            'months_to_recover': round(max_drawdown / 0.005),
        },
    }


def get_goals_progress():
    """财务目标进度追踪。"""
    buckets = list_goal_buckets()
    portfolio = get_portfolio_summary()

    progress = []
    total_target = 0
    total_actual = 0

    for bucket in buckets:
        target_amount = bucket.get('target_amount', 0)
        current_amount = bucket.get('current_amount', 0)

        linked_holdings = bucket.get('linked_holdings')
        linked_ids = json.loads(linked_holdings) if linked_holdings else []
        if linked_ids:
            for fund_code, h in portfolio.get('holdings', {}).items():
                if h.get('id') in linked_ids:
                    current_amount += h.get('current_value', 0)

        ratio = current_amount / target_amount if target_amount > 0 else 0
        gap = target_amount - current_amount

        total_target += target_amount
        total_actual += current_amount

        progress.append({
            'id': bucket['id'],
            'name': bucket.get('name'),
            'bucket_type': bucket.get('bucket_type'),
            'target_amount': round(target_amount, 2),
            'current_amount': round(current_amount, 2),
            'gap': round(gap, 2),
            'progress_ratio': round(ratio, 2),
            'progress_percent': f"{ratio*100:.0f}%",
        })

    overall_ratio = total_actual / total_target if total_target > 0 else 0

    return {
        'goals': progress,
        'summary': {
            'total_target': round(total_target, 2),
            'total_actual': round(total_actual, 2),
            'total_gap': round(total_target - total_actual, 2),
            'overall_progress': round(overall_ratio, 2),
            'overall_percent': f"{overall_ratio*100:.0f}%",
        },
    }
