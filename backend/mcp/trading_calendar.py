"""A股交易日历 — 判断交易日、计算 T+n 确认日。

从 akshare 加载交易日历并缓存，提供快速查询。
"""

import logging
from datetime import date, timedelta, datetime
from functools import lru_cache

logger = logging.getLogger(__name__)

_trade_days: set[date] | None = None
_cache_date: date | None = None


def _load_trade_days() -> set[date]:
    """从 akshare 加载交易日数据。"""
    global _trade_days, _cache_date
    try:
        import akshare as ak

        df = ak.tool_trade_date_hist_sina()
        days = set(df["trade_date"].tolist())
        _trade_days = days
        _cache_date = date.today()
        logger.info(f"交易日历已加载，共 {len(days)} 个交易日")
        return days
    except Exception as e:
        logger.warning(f"加载交易日历失败: {e}，使用周末判定作为回退")
        return set()


def _get_trade_days() -> set[date]:
    """获取交易日集合，按需加载。"""
    if _trade_days is None:
        _load_trade_days()
    return _trade_days or set()


def is_trading_day(d: date) -> bool:
    """判断指定日期是否为 A 股交易日。"""
    days = _get_trade_days()
    if days:
        return d in days
    # 回退：周一至周五
    return d.weekday() < 5


def next_trading_day(d: date, n: int = 1) -> date:
    """返回 d 之后的第 n 个交易日。"""
    days = _get_trade_days()
    if days:
        result = d
        count = 0
        while count < n:
            result += timedelta(days=1)
            if result in days:
                count += 1
        return result
    # 回退
    result = d
    while n > 0:
        result += timedelta(days=1)
        if result.weekday() < 5:
            n -= 1
    return result


def expected_confirm_date(trade_date: date, trade_time: str | None = None) -> date:
    """计算 T+1 确认日。

    规则：
    - 交易日 15:00 前提交 → 当日为 T 日，T+1 确认
    - 交易日 15:00 后提交 → 次日为 T 日，T+2 确认（实质 T+1）
    - 非交易日提交 → 下一交易日为 T 日，T+1 确认
    """
    days = _get_trade_days()

    if days and trade_date not in days:
        # 非交易日提交 → 下一交易日为 T 日
        t_day = next_trading_day(trade_date)
        return next_trading_day(t_day)

    if trade_time:
        try:
            hour, minute = map(int, trade_time.split(":"))
            if hour > 15 or (hour == 15 and minute > 0):
                # 15:00 后 → 下一交易日为 T 日
                t_day = next_trading_day(trade_date)
                return next_trading_day(t_day)
        except (ValueError, TypeError):
            pass

    # 默认 T+1
    return next_trading_day(trade_date)
