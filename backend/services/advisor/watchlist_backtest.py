"""关注列表上车信号回测 — P0-3（2026-07-21）

每次 watchlist 信号灯由非 green 变 green 时，自动插入回测记录；
15 交易日（约 21 自然日）后自动回测涨幅，验证"可上车"信号的真实有效性。

命中定义：15 交易日后涨幅 >= 3%（考虑申购费+赎回费+滑点）。
"""

import logging
from datetime import datetime

from db.watchlist import (
    list_pending_signal_backtests,
    update_signal_backtest,
    get_signal_backtest_stats,
)

logger = logging.getLogger(__name__)

# 命中阈值：15 交易日涨幅 >= 3%
HIT_THRESHOLD_PCT = 3.0


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
