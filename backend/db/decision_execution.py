"""决策执行回写闭环数据层 — decision_execution_log 表 CRUD。

P0-1：记录决策"采纳→执行→账户实际盈亏"链路，用真实账户盈亏校准建议准确率。
"""

from __future__ import annotations

import logging

from db._conn import _get_conn

logger = logging.getLogger(__name__)

# 合法 action_type：execute=已执行 / skip=跳过未执行 / partial=部分执行
VALID_ACTION_TYPES = {"execute", "skip", "partial"}


def create_execution_log(
    decision_id: int,
    action_type: str,
    executed_at: str,
    executed_price: float | None = None,
    executed_amount: float | None = None,
    executed_shares: float | None = None,
    notes: str | None = None,
) -> int:
    """记录一条决策执行日志。返回新日志 id。

    actual_pnl / pnl_percentage / holding_period_days / attribution 在复盘阶段
    通过 update_execution_pnl() 回填，创建时不需要传。
    """
    if action_type not in VALID_ACTION_TYPES:
        action_type = "execute"

    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO decision_execution_log
                (decision_id, action_type, executed_price, executed_amount,
                 executed_shares, executed_at, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                action_type,
                executed_price,
                executed_amount,
                executed_shares,
                executed_at,
                notes,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_execution_log(log_id: int) -> dict | None:
    """获取单条执行日志。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM decision_execution_log WHERE id = ?",
            (log_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_execution_logs(decision_id: int | None = None, limit: int = 50) -> list[dict]:
    """列出执行日志，可按 decision_id 过滤，默认按执行时间倒序。"""
    conn = _get_conn()
    try:
        if decision_id is not None:
            rows = conn.execute(
                """
                SELECT * FROM decision_execution_log
                WHERE decision_id = ?
                ORDER BY executed_at DESC, id DESC
                LIMIT ?
                """,
                (decision_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM decision_execution_log
                ORDER BY executed_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_execution_pnl(
    log_id: int,
    actual_pnl: float,
    pnl_percentage: float,
    holding_period_days: int,
    attribution: str | None = None,
) -> bool:
    """更新执行盈亏（复盘时用）。返回是否更新成功。"""
    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            UPDATE decision_execution_log
            SET actual_pnl = ?,
                pnl_percentage = ?,
                holding_period_days = ?,
                attribution = ?
            WHERE id = ?
            """,
            (actual_pnl, pnl_percentage, holding_period_days, attribution, log_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_execution_accuracy_stats() -> dict:
    """统计决策执行准确率。

    基于 actual_pnl 已回填的记录计算胜率、平均盈亏百分比、平均持仓天数，
    并按 action_type 分组返回明细。
    """
    conn = _get_conn()
    try:
        # 总体统计：仅统计 actual_pnl 已回填的记录（即已完成复盘的执行）
        overall = conn.execute(
            """
            SELECT
                COUNT(*) AS total_executed,
                SUM(CASE WHEN actual_pnl > 0 THEN 1 ELSE 0 END) AS profitable_count,
                SUM(CASE WHEN actual_pnl < 0 THEN 1 ELSE 0 END) AS loss_count,
                AVG(CASE WHEN pnl_percentage IS NOT NULL THEN pnl_percentage END) AS avg_pnl_percentage,
                AVG(CASE WHEN holding_period_days IS NOT NULL THEN holding_period_days END) AS avg_holding_days
            FROM decision_execution_log
            WHERE actual_pnl IS NOT NULL
            """,
        ).fetchone()

        total_executed = overall["total_executed"] or 0 if overall else 0
        profitable_count = overall["profitable_count"] or 0 if overall else 0
        loss_count = overall["loss_count"] or 0 if overall else 0
        avg_pnl_percentage = overall["avg_pnl_percentage"] or 0 if overall else 0
        avg_holding_days = overall["avg_holding_days"] or 0 if overall else 0

        # 按 action_type 分组统计
        group_rows = conn.execute(
            """
            SELECT
                action_type,
                COUNT(*) AS total,
                SUM(CASE WHEN actual_pnl > 0 THEN 1 ELSE 0 END) AS profitable,
                SUM(CASE WHEN actual_pnl < 0 THEN 1 ELSE 0 END) AS loss,
                AVG(CASE WHEN pnl_percentage IS NOT NULL THEN pnl_percentage END) AS avg_pnl_pct,
                AVG(CASE WHEN holding_period_days IS NOT NULL THEN holding_period_days END) AS avg_holding_days
            FROM decision_execution_log
            GROUP BY action_type
            """,
        ).fetchall()

        by_action_type: dict[str, dict] = {}
        for row in group_rows:
            at = row["action_type"] or "unknown"
            sub_total = row["total"] or 0
            sub_profit = row["profitable"] or 0
            by_action_type[at] = {
                "total": sub_total,
                "profitable_count": sub_profit,
                "loss_count": row["loss"] or 0,
                "win_rate": round(sub_profit / sub_total * 100, 2) if sub_total else 0,
                "avg_pnl_percentage": round(row["avg_pnl_pct"] or 0, 2),
                "avg_holding_days": round(row["avg_holding_days"] or 0, 2),
            }

        return {
            "total_executed": total_executed,
            "profitable_count": profitable_count,
            "loss_count": loss_count,
            "win_rate": round(profitable_count / total_executed * 100, 2) if total_executed else 0,
            "avg_pnl_percentage": round(avg_pnl_percentage or 0, 2),
            "avg_holding_days": round(avg_holding_days or 0, 2),
            "by_action_type": by_action_type,
        }
    finally:
        conn.close()
