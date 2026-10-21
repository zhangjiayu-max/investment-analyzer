"""行情数据时效守卫：把"今日"锚定到真实交易日，避免拿隔夜数据当当天行情。

背景（2026-10-21 分析记录复核，log#139 / log#140）：同一天两份报告都写
"今日"，行情数字却完全相反。查库确认 `index_price_history` 最新只到
10-20，两份报告实际都在引用前一交易日收盘，却用"今日"叙述。

这里不改写正文数字，只在文末追加数据截止日声明，并列出文中出现日期
与最新交易日不一致的迹象。判据：
  1. 文本含"今日/今天"且含涨跌幅
  2. 库内最新交易日 < 今天
开关：agent.market_date_guard_enabled（默认开启）。
"""
from __future__ import annotations

import datetime
import logging
import re

logger = logging.getLogger(__name__)

_TODAY_WORDS = re.compile(r'今日|今天|当日')
# "今日"必须与行情数字/涨跌在同一句且相邻，避免"今日事件"这类无关表述误触发
_TODAY_MARKET = re.compile(
    r'(?:今日|今天|当日)[^。\n]{0,40}?(?:[-+＋]\s*\d+(?:\.\d+)?\s*%|涨|跌|成交)'
)


def _latest_trade_date() -> str:
    """库内最新有收盘数据的交易日（YYYY-MM-DD），取不到返回空串。"""
    try:
        from db._conn import _get_conn
        conn = _get_conn()
        try:
            row = conn.execute("SELECT MAX(trade_date) FROM index_price_history").fetchone()
            return (row[0] or "") if row else ""
        finally:
            conn.close()
    except Exception as exc:
        logger.warning('[market_date_guard] 读取最新交易日失败: %s', exc)
        return ""


def enforce_market_date_guard(answer: str, trace_id: str = '') -> tuple[str, list[str]]:
    """数据截止日与"今日"表述不一致时，追加时效声明。

    返回 (处理后的文本, 告警列表)；告警为空表示无需提示。
    """
    warnings: list[str] = []
    try:
        from db.config import get_config_bool
        if not get_config_bool('agent.market_date_guard_enabled', True):
            return answer, warnings
    except Exception:
        return answer, warnings
    if not answer or not _TODAY_MARKET.search(answer):
        return answer, warnings

    latest = _latest_trade_date()
    today = datetime.date.today().isoformat()
    if not latest or latest >= today:
        return answer, warnings

    warnings.append('stale_market_date:' + latest)
    logger.info('[trace:%s] [market_date_guard] 文中"今日"与最新交易日不一致: %s < %s',
                trace_id, latest, today)

    block = (
        '\n\n---\n\n**数据时效提示**\n\n'
        f'系统内最新可核验的行情交易日为 **{latest}**，本回复中的涨跌幅若标注为'
        f'"今日/今天"，实际对应的是该交易日收盘，而非 {today} 盘中。\n\n'
        '涉及"今天行情"的判断请以盘中实时数据复核后再执行；同一交易日内的多份报告'
        '应共用同一份行情快照，出现数字不一致时以带来源与截止日的一方为准。\n'
    )
    return answer.rstrip() + block, warnings
