"""交易手续费计算工具。

根据费率配置自动计算申购费/赎回费/转换费：
- 申购费 = 买入金额 × 申购费率（默认 0.15%）
- 赎回费 = 卖出金额 × 持有期对应费率（<7天 1.5% / <1年 0.5% / <2年 0.25% / ≥2年 0%）
- 转换费 = 转出金额 × 转换费率（默认 0%）

开关：fee.auto_calc_enabled（默认 true），关闭后自动确认不计算手续费。
"""

from datetime import date, datetime

from db.config import get_config_bool, get_config_float


def _parse_date(s):
    """解析 YYYY-MM-DD 字符串，失败返回 None。"""
    if not s:
        return None
    if isinstance(s, date):
        return s
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _holding_days(buy_date_str) -> int:
    """计算持有天数。buy_date 缺失返回 0（按最高费率兜底）。"""
    d = _parse_date(buy_date_str)
    if not d:
        return 0
    return max((date.today() - d).days, 0)


def _sell_rate_by_holding(buy_date_str) -> tuple[float, str]:
    """按持有期返回 (费率, 说明)。"""
    days = _holding_days(buy_date_str)
    if days < 7:
        return get_config_float('fee.sell_rate_lt7d', 0.015), f"持有{days}天，赎回费1.5%"
    if days < 365:
        return get_config_float('fee.sell_rate_lt1y', 0.005), f"持有{days}天，赎回费0.5%"
    if days < 730:
        return get_config_float('fee.sell_rate_lt2y', 0.0025), f"持有{days}天，赎回费0.25%"
    return get_config_float('fee.sell_rate_ge2y', 0.0), f"持有{days}天，赎回费0%"


def is_auto_calc_enabled() -> bool:
    """自动计算手续费开关。"""
    return get_config_bool('fee.auto_calc_enabled', True)


def calc_buy_fee(amount: float) -> tuple[float, float, str]:
    """申购费 = 买入金额 × 申购费率。

    Returns: (fee, rate, basis)
    """
    rate = get_config_float('fee.buy_rate', 0.0015)
    fee = round(float(amount or 0) * rate, 2)
    return fee, rate, f"申购费率{rate*100:.2f}%"


def calc_sell_fee(shares: float, nav: float, holding: dict | None) -> tuple[float, float, str]:
    """赎回费 = 卖出金额 × 持有期对应费率。

    Returns: (fee, rate, basis)
    """
    holding = holding or {}
    rate, basis = _sell_rate_by_holding(holding.get('buy_date'))
    gross = float(shares or 0) * float(nav or 0)
    fee = round(gross * rate, 2)
    return fee, rate, basis


def calc_convert_fee(shares: float, nav: float) -> tuple[float, float, str]:
    """转换费 = 转出金额 × 转换费率。

    Returns: (fee, rate, basis)
    """
    rate = get_config_float('fee.convert_rate', 0.0)
    gross = float(shares or 0) * float(nav or 0)
    fee = round(gross * rate, 2)
    return fee, rate, f"转换费率{rate*100:.2f}%"


def calc_fee_for_tx(tx: dict, confirmed_price: float, holding: dict | None = None) -> tuple[float, str]:
    """根据交易记录自动计算手续费。

    开关关闭时返回 (0, "自动计算已关闭")。
    Returns: (fee, basis)
    """
    if not is_auto_calc_enabled():
        return 0.0, "自动计算已关闭"

    tx_type = tx.get('transaction_type')
    if tx_type == 'buy':
        sub_amount = tx.get('submitted_amount') or tx.get('amount') or 0
        fee, _, basis = calc_buy_fee(sub_amount)
        return fee, basis
    if tx_type == 'sell':
        sub_shares = tx.get('submitted_shares') or tx.get('shares') or 0
        fee, _, basis = calc_sell_fee(sub_shares, confirmed_price, holding)
        return fee, basis
    if tx_type == 'convert':
        sub_shares = tx.get('submitted_shares') or tx.get('shares') or 0
        fee, _, basis = calc_convert_fee(sub_shares, confirmed_price)
        return fee, basis
    return 0.0, "该交易类型无手续费"
