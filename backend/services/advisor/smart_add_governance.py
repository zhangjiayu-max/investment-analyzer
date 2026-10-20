"""智能补仓的纯计算边界：配置、证据、份额和公平情景。"""
import math
from datetime import date, datetime, timedelta


def finite_number(value, name, low=0, high=None):
    if isinstance(value, bool):
        raise ValueError(f'{name}必须是有限数值')
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise ValueError(f'{name}必须是有限数值') from None
    if not math.isfinite(number) or number < low or (high is not None and number > high):
        raise ValueError(f'{name}超出有效范围')
    return number


def parse_pyramid_tiers(raw):
    """旧两段格式必须显式迁移，不能静默改变加仓比例。"""
    parts = raw.split(',') if isinstance(raw, str) else raw
    if not isinstance(parts, list) or not 1 <= len(parts) <= 20:
        raise ValueError('金字塔须有1至20个档位')
    tiers, seen = [], set()
    for part in parts:
        values = part.strip().split(':') if isinstance(part, str) else [abs(part['loss_pct']), part['release_pct'], part['add_ratio']]
        if len(values) != 3:
            raise ValueError('金字塔必须完整包含亏损:释放率:加仓比例；旧格式需核对后显式迁移')
        loss, release, add = [finite_number(v, '档位', .01, 100) for v in values]
        if loss in seen:
            raise ValueError('金字塔亏损档位不能重复')
        seen.add(loss)
        tiers.append(dict(loss_pct=-loss, release_pct=release, add_ratio=add))
    return sorted(tiers, key=lambda t: t['loss_pct'])


_BOOL_KEYS = {'enabled', 'pyramid_enabled', 'trend_signal_enabled', 'dip_signal_enabled',
              'exit_signal_enabled', 'va_enabled', 'grid_enabled', 'fund_health_enabled'}
_RANGES = {
    'base_dca_pct': (0, 100), 'pool_pct': (0, 100), 'loss_threshold': (-100, 0),
    'max_single_position_pct': (0, 100), 'valuation_pause_pct': (0, 100),
    'stale_days': (1, 365), 'max_add_vs_position_mult': (0, 10),
    'cooldown_days': (1, 365), 'max_buys_in_cooldown': (1, 100),
    'trend_base_ratio': (0, 100), 'trend_position_pct': (0, 100), 'dip_base_ratio': (0, 100),
    'dca_drop_step_pct': (.01, 99), 'grid_count': (2, 100), 'grid_range_pct': (.01, 99),
}
_INT_KEYS = {'stale_days', 'cooldown_days', 'max_buys_in_cooldown', 'grid_count'}


def validate_config_patch(config):
    result = {}
    for key, value in config.items():
        if not key.startswith('smart_add.'):
            raise ValueError(f'不支持的配置项：{key}')
        name = key.removeprefix('smart_add.')
        if name in _BOOL_KEYS:
            if isinstance(value, bool):
                result[key] = 'true' if value else 'false'
            elif isinstance(value, str) and value.lower() in ('true', 'false'):
                result[key] = value.lower()
            else:
                raise ValueError(f'{name}必须是布尔值')
        elif name == 'pyramid_tiers':
            tiers = parse_pyramid_tiers(value)
            result[key] = ','.join(f"{abs(t['loss_pct']):g}:{t['release_pct']:g}:{t['add_ratio']:g}" for t in reversed(tiers))
        elif name == 'dca_tiers':
            parts = str(value).split(',')
            if not 1 <= len(parts) <= 20:
                raise ValueError('定投档位数量无效')
            seen = set()
            for part in parts:
                pair = part.split(':')
                if len(pair) != 2:
                    raise ValueError('定投档位必须是跌幅:倍数')
                drop = finite_number(pair[0], '定投跌幅', .01, 99)
                finite_number(pair[1], '定投倍数', 0, 10)
                if drop in seen:
                    raise ValueError('定投档位不能重复')
                seen.add(drop)
            result[key] = value
        elif name in _RANGES:
            number = finite_number(value, name, *_RANGES[name])
            if name in _INT_KEYS and not number.is_integer():
                raise ValueError(f'{name}必须是整数')
            result[key] = str(int(number) if name in _INT_KEYS else number)
        else:
            raise ValueError(f'不支持的配置项：{key}')
    return result


def evidence_warnings(holding, valuation, fund_type, stale_days=14):
    warnings = []
    def parse_day(value):
        try:
            return date.fromisoformat(str(value)[:10])
        except (ValueError, TypeError):
            return None
    # 以净值所属日期为准；仅旧数据无所属日时兼容抓取日期。
    price_day = parse_day(holding.get('price_date') or holding.get('nav_date') or holding.get('current_price_date') or holding.get('price_updated_at'))
    today = date.today()
    business_age = None
    if price_day and price_day <= today:
        business_age = sum((price_day + timedelta(days=i)).weekday() < 5 for i in range(1, (today - price_day).days + 1))
    raw_price = holding.get('current_price')
    price_valid = isinstance(raw_price, (int, float)) and math.isfinite(raw_price) and raw_price > 0
    if not price_valid or business_age is None or business_age > 3:
        warnings.append('净值缺失、日期未知或超过3个工作日（未包含节假日历），等待更新')
    if fund_type != 'bond':
        val_day = parse_day((valuation or {}).get('snapshot_date'))
        percentile = (valuation or {}).get('percentile')
        percentile_valid = isinstance(percentile, (int, float)) and math.isfinite(percentile) and 0 <= percentile <= 100
        if not valuation or not percentile_valid or valuation.get('is_expired') or not val_day or not 0 <= (today - val_day).days <= stale_days:
            warnings.append('估值缺失、日期未知或已过期，等待补齐')
    if holding.get('trade_status') in ('suspended', 'closed', '暂停申购'):
        warnings.append('当前暂停申购')
    return warnings


def finalize_execution_amount(plan, min_amount=10, lot_size=100, share_step=.01):
    """所有预算缩减之后再量化，金额和份额只向下，不增加支出。"""
    price = float(plan.get('current_price') or 0)
    budget = max(0, float(plan.get('final_suggested_amount') or 0))
    fees = finite_number(plan.get('subscription_fee_rate', 0) or 0, '申购费率', 0, 1)
    text = (plan.get('fund_name') or '').upper()
    step = max(1, lot_size) if 'ETF' in text else share_step
    shares = math.floor((budget / (1 + fees) / price + 1e-10) / step) * step if price > 0 else 0
    amount = math.ceil((shares * price * (1 + fees) - 1e-9) * 100) / 100
    if amount < min_amount or amount > budget:
        amount, shares = 0., 0.
    plan['final_suggested_amount'] = round(amount, 2)
    plan['execution_shares'] = round(shares, 4)
    plan['execution_fee'] = round(amount * fees / (1 + fees), 2)
    plan['amount_basis'] = '最终风控、今日现金预算及份额取整后的金额（含已知费用）'
    sizing = plan.get('position_sizing')
    if sizing:
        sizing['shares'] = plan['execution_shares']
        sizing['shares_basis'] = '按最终可执行金额向下取整'
    if amount == 0 and budget > 0:
        plan['min_subscription_note'] = '最终分配不足起购金额或交易单位，暂不执行'
    return plan


def simulate_cash_strategies(holding, monthly_drop_pct, months, base_monthly, cash_budget, fee_rate=0, tiers=None):
    drop = finite_number(monthly_drop_pct, '月度变动', -99.99, 100)
    count = finite_number(months, '模拟月数', 1, 120)
    if not count.is_integer():
        raise ValueError('模拟月数必须为整数')
    count = int(count)
    budget = finite_number(cash_budget, '共同现金预算', 0, 1e12)
    base = finite_number(base_monthly, '月投金额', 0, 1e12)
    fee = finite_number(fee_rate, '费用率', 0, .1)
    price0 = finite_number(holding.get('current_price'), '净值', .000000001, 1e9)
    shares0 = finite_number(holding.get('shares'), '份额', .000000001, 1e15)
    cost0 = finite_number(holding.get('total_cost') or price0 * shares0, '成本', 0, 1e15)
    wealth0 = price0 * shares0 + budget
    prices = [price0 * (1 + drop / 100) ** i for i in range(count + 1)]
    tiers = sorted(tiers or [{'loss_pct': -10, 'add_ratio': 5}, {'loss_pct': -20, 'add_ratio': 10}, {'loss_pct': -30, 'add_ratio': 15}, {'loss_pct': -40, 'add_ratio': 20}, {'loss_pct': -50, 'add_ratio': 25}], key=lambda t: t['loss_pct'], reverse=True)
    strategies = []
    for name, icon in [('不补仓', 'minus-circle'), ('等额定投', 'calendar'), ('金字塔补仓', 'layers'), ('价值平均法', 'trending-up')]:
        cash, shares, added, fees = budget, shares0, 0., 0.
        peak, max_dd, max_loss, peak_added = wealth0, 0., 0., 0.
        executed, path = set(), []
        for month, price in enumerate(prices[1:], 1):
            demand = 0.
            if name == '等额定投':
                demand = base
            elif name == '金字塔补仓':
                loss = (price / (cost0 / shares0) - 1) * 100
                reached = [(i, t) for i, t in enumerate(tiers) if loss <= t['loss_pct'] and i not in executed]
                if reached:
                    i, tier = reached[-1]
                    demand = cost0 * tier['add_ratio'] / 100
                    executed.update(j for j, _ in reached)
            elif name == '价值平均法':
                demand = min(base * 3, max(0., price0 * shares0 + base * month - shares * price))
            spend = min(cash, demand)
            fees += spend * fee / (1 + fee)
            shares += spend / (1 + fee) / price
            cash -= spend
            added += spend
            peak_added = max(peak_added, added)
            wealth = shares * price + cash
            peak = max(peak, wealth)
            max_dd = max(max_dd, (peak - wealth) / peak * 100)
            max_loss = max(max_loss, wealth0 - wealth)
            path.append(dict(month=month, price=round(price, 8), wealth=round(wealth, 2), cash=round(cash, 2), added=round(spend, 2)))
        value = shares * prices[-1]
        strategies.append(dict(name=name, icon=icon, initial_wealth=round(wealth0, 2), cash_budget=budget,
            total_invested=round(cost0 + added, 2), total_added=round(added, 2), final_shares=round(shares, 4),
            final_cost=round((cost0 + added) / shares, 4), final_value=round(value, 2), remaining_cash=round(cash, 2),
            final_wealth=round(value + cash, 2), fees=round(fees, 2), max_loss=round(max_loss, 2),
            max_drawdown_pct=round(max_dd, 2), peak_additional_funding=round(peak_added, 2),
            profit_rate=round((value + cash - wealth0) / wealth0 * 100, 2), wealth_path=path,
            description='共同初始财富及有限现金预算；现金收益率0；无外部追加资金'))
    best = max(strategies, key=lambda s: s['final_wealth'])
    best['is_best'] = True
    return dict(scenario=dict(current_price=price0, monthly_drop_pct=drop, months=count, price_path=prices,
                    cash_budget=budget, fee_rate=fee, assumption='确定性情景推演，非预测或历史回测；不含分红及现金利息'),
                strategies=strategies, best_strategy=best['name'], ranking_basis='同初始财富下期末总财富（含剩余现金）')
