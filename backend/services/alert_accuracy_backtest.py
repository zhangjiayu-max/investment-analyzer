"""P2-4.3: 预警准确性回测服务。

设计稿：2026-07-05-预警与建议逻辑增强.md §4.3
作用：每周一回测过去 7 天所有预警的实际走势，为阈值调整提供数据支撑。

回测逻辑：
  - 查询周内所有涉及基金的预警（buy_drop/drawdown/valuation/concentration）
  - 对每条预警，查询预警后 7 天的净值变化
  - 按 alert_type + severity 分组统计：样本数、平均涨跌、胜率、中位数
  - 胜率定义：buy_drop/valuation_opportunity 看涨（后续>0），其他看跌（后续<0）

成本：0 LLM、0 MCP、0 akshare，纯 SQLite 读 fund_nav_history + 写 stats。
触发：每周一 02:00 定时任务，或手动调用。
"""
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from db._conn import _get_conn

logger = logging.getLogger(__name__)

# 参与回测的预警类型
BACKTEST_ALERT_TYPES = (
    "buy_drop_alert",
    "drawdown_alert",
    "valuation_alert",
    "valuation_opportunity",
    "concentration_alert",
)

# 看涨预警（后续走势 > 0 算胜）
BULLISH_ALERT_TYPES = ("buy_drop_alert", "valuation_opportunity")


def backtest_alert_accuracy(week_start: Optional[str] = None) -> dict:
    """回测过去 7 天所有预警的实际走势。

    参数:
        week_start: 周一日期字符串（YYYY-MM-DD）。None 时自动取上周一。

    返回:
        {
            "week_start": "2026-06-29",
            "week_end": "2026-07-06",
            "alert_count": 25,
            "stat_groups": [
                {"alert_type": "buy_drop_alert", "severity": "danger",
                 "sample_count": 5, "avg_followup_change": 4.2,
                 "win_rate": 100.0, "median_change": 3.8},
                ...
            ],
        }
    """
    if not week_start:
        # 默认回测上周
        today = datetime.now()
        week_start = (today - timedelta(days=today.weekday() + 7)).strftime("%Y-%m-%d")
    week_end = (datetime.strptime(week_start, "%Y-%m-%d") + timedelta(days=7)).strftime("%Y-%m-%d")

    conn = _get_conn()
    try:
        # 查询周内所有涉及基金的预警
        alerts = conn.execute("""
            SELECT alert_type, severity, related_fund_code, created_at
            FROM portfolio_alerts
            WHERE created_at >= ? AND created_at < ?
              AND related_fund_code IS NOT NULL
              AND related_fund_code != ''
              AND alert_type IN (%s)
        """ % ",".join(["?"] * len(BACKTEST_ALERT_TYPES)),
            (week_start, week_end, *BACKTEST_ALERT_TYPES),
        ).fetchall()

        # 按 alert_type + severity 分组统计
        stats: dict = defaultdict(lambda: {"count": 0, "changes": []})
        for a in alerts:
            code = a["related_fund_code"]
            alert_date = (a["created_at"] or "")[:10]
            if not code or not alert_date:
                continue
            # 查预警后 7 天净值变化
            post_navs = conn.execute("""
                SELECT nav FROM fund_nav_history
                WHERE fund_code = ? AND nav_date > ?
                ORDER BY nav_date LIMIT 7
            """, (code, alert_date)).fetchall()
            if len(post_navs) < 2:
                continue
            first = post_navs[0]["nav"]
            last = post_navs[-1]["nav"]
            if not first or first <= 0:
                continue
            ch = (last - first) / first * 100
            key = f"{a['alert_type']}:{a['severity']}"
            stats[key]["count"] += 1
            stats[key]["changes"].append(ch)

        # 写入统计表（先删除同周同组旧记录，避免重复）
        conn.execute(
            "DELETE FROM alert_accuracy_stats WHERE week_start = ?",
            (week_start,),
        )

        stat_groups = []
        for key, data in stats.items():
            alert_type, severity = key.split(":", 1)
            changes = data["changes"]
            if not changes:
                continue
            avg_ch = sum(changes) / len(changes)
            # 胜率定义
            if alert_type in BULLISH_ALERT_TYPES:
                wins = sum(1 for c in changes if c > 0)
            else:
                wins = sum(1 for c in changes if c < 0)
            win_rate = wins / len(changes) * 100
            sorted_changes = sorted(changes)
            median = sorted_changes[len(sorted_changes) // 2]

            conn.execute("""
                INSERT INTO alert_accuracy_stats
                (alert_type, severity, week_start, sample_count,
                 avg_followup_change, win_rate, median_change, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                alert_type, severity, week_start, len(changes),
                round(avg_ch, 2), round(win_rate, 1), round(median, 2),
                datetime.now().isoformat(),
            ))

            stat_groups.append({
                "alert_type": alert_type,
                "severity": severity,
                "sample_count": len(changes),
                "avg_followup_change": round(avg_ch, 2),
                "win_rate": round(win_rate, 1),
                "median_change": round(median, 2),
            })

        conn.commit()
        logger.info(
            f"预警准确性回测完成：week={week_start} alerts={len(alerts)} "
            f"groups={len(stat_groups)}"
        )
        return {
            "week_start": week_start,
            "week_end": week_end,
            "alert_count": len(alerts),
            "stat_groups": stat_groups,
        }
    finally:
        conn.close()


def get_alert_accuracy_stats(weeks: int = 4) -> list[dict]:
    """查询最近 N 周的预警准确性回测统计。"""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT alert_type, severity, week_start, sample_count,
                   avg_followup_change, win_rate, median_change, created_at
            FROM alert_accuracy_stats
            ORDER BY week_start DESC, alert_type, severity
            LIMIT ?
        """, (weeks * 20,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
