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


# ── P1-S5（2026-08-01）：per-fund 费率覆盖 ──
# fund_metadata.subscription_fee 字段优先于全局 fee.buy_rate
_FUND_FEE_CACHE: dict[str, tuple[float, float]] = {}  # fund_code → (rate, cached_ts)
_FUND_FEE_CACHE_TTL = 300.0  # 秒


def get_fund_fee_rate(fund_code: str | None) -> float | None:
    """查询基金专属申购费率，无则返回 None（调用方回退到全局 fee.buy_rate）。

    数据源：fund_metadata.subscription_fee（akshare 抓取，填充率不高，回退兜底）。
    带 5 分钟缓存，避免每次补仓都查 DB。
    """
    if not fund_code:
        return None
    import time as _time
    cached = _FUND_FEE_CACHE.get(fund_code)
    if cached and (_time.time() - cached[1]) < _FUND_FEE_CACHE_TTL:
        return cached[0]
    rate: float | None = None
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT subscription_fee FROM fund_metadata WHERE fund_code = ?",
                (fund_code,),
            ).fetchone()
        finally:
            conn.close()
        if row and row["subscription_fee"] is not None:
            r = float(row["subscription_fee"])
            # subscription_fee 存储为百分比（如 0.15 表示 0.15%），归一化为小数
            if r > 1.0:
                r = r / 100.0
            rate = r
    except Exception:
        rate = None
    _FUND_FEE_CACHE[fund_code] = (rate, _time.time())
    return rate


def calc_buy_fee(amount: float, fund_code: str | None = None) -> tuple[float, float, str]:
    """申购费 = 买入金额 × 申购费率。

    P1-S5：优先用 fund_metadata.subscription_fee（per-fund），无则回退 fee.buy_rate。

    Args:
        amount: 买入金额
        fund_code: 基金代码（可选），传入则查 per-fund 费率

    Returns: (fee, rate, basis)
    """
    # 优先 per-fund 费率
    fund_rate = get_fund_fee_rate(fund_code) if fund_code else None
    if fund_rate is not None:
        rate = fund_rate
        basis = f"基金专属申购费率{rate*100:.2f}%（fund_metadata）"
    else:
        rate = get_config_float('fee.buy_rate', 0.0015)
        basis = f"申购费率{rate*100:.2f}%"
    fee = round(float(amount or 0) * rate, 2)
    return fee, rate, basis


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
        # P1-S5：传 fund_code 以使用 per-fund 费率
        fee, _, basis = calc_buy_fee(sub_amount, tx.get('fund_code'))
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
