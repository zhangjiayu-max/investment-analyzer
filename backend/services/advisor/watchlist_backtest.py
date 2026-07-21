"""关注列表上车信号回测 — P0-3（2026-07-21）

每次 watchlist 信号灯由非 green 变 green 时，自动插入回测记录；
15 交易日（约 21 自然日）后自动回测涨幅，验证"可上车"信号的真实有效性。

命中定义：15 交易日涨幅 >= 3%（考虑申购费+赎回费+滑点）。

P1-B（2026-07-21）增强：
- _calc_review_date 用 akshare 交易日历精确计算 15 交易日，跳过周末和节假日
- 失败时兜底 +21 自然日（与原 P0-3 行为一致）
"""

import logging
from datetime import datetime, timedelta

from db.watchlist import (
    list_pending_signal_backtests,
    update_signal_backtest,
    get_signal_backtest_stats,
)

logger = logging.getLogger(__name__)

# 命中阈值：15 交易日涨幅 >= 3%
HIT_THRESHOLD_PCT = 3.0

# 默认回测交易日数
DEFAULT_REVIEW_TRADE_DAYS = 15


def _calc_review_date(signal_date_str: str, trade_days: int = DEFAULT_REVIEW_TRADE_DAYS) -> str:
    """计算 N 交易日后的日期（P1-B）。

    优先用 akshare 交易日历（tool_trade_date_hist_sina）；
    失败时兜底 +21 自然日（与原 P0-3 行为一致）。

    Args:
        signal_date_str: 信号触发日，格式 YYYY-MM-DD
        trade_days: 交易日数，默认 15

    Returns:
        review_date 字符串，格式 YYYY-MM-DD
    """
    # 读取开关
    try:
        from db.config import get_config_bool
        precision_enabled = get_config_bool("watchlist.review_date_precision_enabled", True)
    except Exception:
        precision_enabled = True

    if not precision_enabled:
        # 不启用精度时，直接 +21 自然日
        dt = datetime.strptime(signal_date_str, "%Y-%m-%d") + timedelta(days=21)
        return dt.strftime("%Y-%m-%d")

    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        # trade_date 列可能是 datetime 或字符串，统一为 YYYY-MM-DD
        trade_dates = set()
        for d in df['trade_date'].tolist():
            if hasattr(d, 'strftime'):
                trade_dates.add(d.strftime("%Y-%m-%d"))
            else:
                trade_dates.add(str(d)[:10])

        current = datetime.strptime(signal_date_str, "%Y-%m-%d")
        count = 0
        # 限制最多迭代 60 天，避免异常死循环
        max_iter = 60
        while count < trade_days and max_iter > 0:
            current += timedelta(days=1)
            max_iter -= 1
            if current.strftime("%Y-%m-%d") in trade_dates:
                count += 1
        if count == trade_days:
            return current.strftime("%Y-%m-%d")
        # 兜底
        dt = datetime.strptime(signal_date_str, "%Y-%m-%d") + timedelta(days=21)
        return dt.strftime("%Y-%m-%d")
    except Exception as e:
        logger.debug(f"[wl_backtest] 交易日历获取失败，兜底+21自然日: {e}")
        dt = datetime.strptime(signal_date_str, "%Y-%m-%d") + timedelta(days=21)
        return dt.strftime("%Y-%m-%d")


def _get_fund_nav_at_or_before(fund_code: str, target_date: str) -> float | None:
    """获取基金在指定日期（或之前最近一日）的净值。

    优先级：
    1. 本地 fund_nav_history 表
    2. fetch_fund_nav 实时拉取（仅当 target_date 是今日或昨日时）
    """
    # 1. 本地 fund_nav_history
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        try:
            row = conn.execute(
                """SELECT nav FROM fund_nav_history
                   WHERE fund_code = ? AND trade_date <= ?
                   ORDER BY trade_date DESC LIMIT 1""",
                (fund_code, target_date),
            ).fetchone()
            if row and row["nav"]:
                return float(row["nav"])
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"[wl_backtest] 本地净值查询失败 {fund_code} @ {target_date}: {e}")

    # 2. 实时拉取（仅当 target_date 接近今日时有效）
    try:
        from db.portfolio import fetch_fund_nav
        nav_data = fetch_fund_nav(fund_code)
        if nav_data and nav_data.get("nav"):
            return float(nav_data["nav"])
    except Exception as e:
        logger.debug(f"[wl_backtest] 实时净值拉取失败 {fund_code}: {e}")

    return None


def review_watchlist_signal_backtests() -> dict:
    """批量回测已到期的 watchlist 信号记录。

    Returns:
        {"reviewed": int, "hit": int, "miss": int, "skipped": int}
    """
    try:
        pending = list_pending_signal_backtests()
        if not pending:
            return {"reviewed": 0, "hit": 0, "miss": 0, "skipped": 0}

        reviewed = 0
        hit_count = 0
        miss_count = 0
        skipped = 0

        for bt in pending:
            try:
                fund_code = bt.get("fund_code")
                review_date = bt.get("review_date")
                entry_nav = bt.get("entry_nav")

                if not fund_code or not review_date or not entry_nav or entry_nav <= 0:
                    skipped += 1
                    continue

                review_nav = _get_fund_nav_at_or_before(fund_code, review_date)
                if not review_nav or review_nav <= 0:
                    logger.debug(f"[wl_backtest] 无法获取 review_nav {fund_code} @ {review_date}")
                    skipped += 1
                    continue

                change_pct = (review_nav - entry_nav) / entry_nav * 100
                hit = 1 if change_pct >= HIT_THRESHOLD_PCT else 0

                update_signal_backtest(bt["id"], {
                    "review_nav": review_nav,
                    "change_pct": round(change_pct, 2),
                    "hit": hit,
                    "reviewed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
                reviewed += 1
                if hit:
                    hit_count += 1
                else:
                    miss_count += 1
            except Exception as e:
                logger.warning(f"[wl_backtest] 回测单条失败 bt_id={bt.get('id')}: {e}")
                skipped += 1

        logger.info(
            f"[wl_backtest] 回测完成：reviewed={reviewed}, hit={hit_count}, "
            f"miss={miss_count}, skipped={skipped}"
        )
        return {
            "reviewed": reviewed,
            "hit": hit_count,
            "miss": miss_count,
            "skipped": skipped,
        }
    except Exception as e:
        logger.warning(f"[wl_backtest] 回测批量执行失败: {e}")
        return {"reviewed": 0, "hit": 0, "miss": 0, "skipped": 0, "error": str(e)}


def get_watchlist_backtest_summary(fund_code: str = None) -> dict:
    """获取回测命中率统计（封装层，给路由用）。"""
    return get_signal_backtest_stats(fund_code=fund_code)
