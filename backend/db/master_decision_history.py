"""大师决策历史 — master_decision_history 表 CRUD。

记录每次大师评分（action≠hold），支持T+7/T+30回测验证。
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from db._conn import _get_conn

logger = logging.getLogger(__name__)


def init_master_decision_history_table(conn) -> None:
    """创建 master_decision_history 表（由 init_db 调用）。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS master_decision_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            master_key TEXT NOT NULL,
            master_name TEXT NOT NULL,
            fund_code TEXT NOT NULL,
            fund_name TEXT,
            target_type TEXT DEFAULT 'fund',
            action TEXT NOT NULL,
            score REAL,
            confidence REAL DEFAULT 0.5,
            reason TEXT,
            snapshot_json TEXT,
            baseline_price REAL,
            baseline_date TEXT NOT NULL,
            verify_7d_result TEXT,
            verify_7d_change_pct REAL,
            verify_7d_verified_at TEXT,
            verify_30d_result TEXT,
            verify_30d_change_pct REAL,
            verify_30d_verified_at TEXT,
            created_at TIMESTAMP DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_master_key ON master_decision_history(master_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fund_code ON master_decision_history(fund_code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON master_decision_history(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_verify_7d ON master_decision_history(verify_7d_result)")
    conn.commit()


def save_master_decision(
    master_key: str,
    master_name: str,
    fund_code: str,
    fund_name: str,
    action: str,
    score: float | None = None,
    confidence: float = 0.5,
    reason: str = "",
    snapshot: dict | None = None,
    baseline_price: float | None = None,
    baseline_date: str | None = None,
    target_type: str = "fund",
) -> int:
    """记录一条大师决策（action≠hold才记录）。

    Returns: 新记录id，失败返回0。
    """
    if action == "hold":
        return 0  # 持有建议不记录
    try:
        conn = _get_conn()
        conn.execute(
            """INSERT INTO master_decision_history
               (master_key, master_name, fund_code, fund_name, target_type,
                action, score, confidence, reason, snapshot_json,
                baseline_price, baseline_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                master_key, master_name, fund_code, fund_name, target_type,
                action, score, confidence, reason,
                json.dumps(snapshot, ensure_ascii=False) if snapshot else None,
                baseline_price, baseline_date or datetime.now().strftime("%Y-%m-%d"),
            ),
        )
        conn.commit()
        rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return rid
    except Exception as e:
        logger.warning(f"[master_decision] 保存失败 {master_key}/{fund_code}: {e}")
        return 0


def list_master_decisions(
    master_key: str | None = None,
    fund_code: str | None = None,
    days: int = 90,
    limit: int = 100,
    verified_only: bool = False,
) -> list[dict]:
    """查询大师决策历史。"""
    try:
        conn = _get_conn()
        sql = """SELECT * FROM master_decision_history
                 WHERE created_at >= datetime('now', ?)"""
        params: list = [f"-{days} days"]
        if master_key:
            sql += " AND master_key = ?"
            params.append(master_key)
        if fund_code:
            sql += " AND fund_code = ?"
            params.append(fund_code)
        if verified_only:
            sql += " AND verify_7d_result IS NOT NULL"
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[master_decision] 查询失败: {e}")
        return []


def list_pending_verification(window_days: int = 7) -> list[dict]:
    """查询待验证的大师决策（创建于[window*2, window]天前）。

    Args:
        window_days: 7 或 30
    """
    result_field = f"verify_{window_days}d_result"
    try:
        conn = _get_conn()
        sql = f"""SELECT * FROM master_decision_history
                  WHERE {result_field} IS NULL
                    AND created_at <= datetime('now', ?)
                    AND created_at >= datetime('now', ?)
                  ORDER BY created_at ASC"""
        rows = conn.execute(
            sql, (f"-{window_days} days", f"-{window_days * 2} days")
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[master_decision] 查询待验证失败: {e}")
        return []


def update_verification_result(
    decision_id: int,
    window_days: int,
    result: str,
    change_pct: float,
) -> bool:
    """更新验证结果。

    Args:
        window_days: 7 或 30
        result: correct/wrong/flat
        change_pct: 涨跌幅
    """
    result_field = f"verify_{window_days}d_result"
    change_field = f"verify_{window_days}d_change_pct"
    verified_at_field = f"verify_{window_days}d_verified_at"
    try:
        conn = _get_conn()
        conn.execute(
            f"""UPDATE master_decision_history
                SET {result_field} = ?, {change_field} = ?, {verified_at_field} = ?
                WHERE id = ?""",
            (result, change_pct, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), decision_id),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.warning(f"[master_decision] 更新验证结果失败 {decision_id}: {e}")
        return False


def get_master_accuracy_stats(days: int = 90) -> dict:
    """大师胜率统计。"""
    try:
        conn = _get_conn()
        # 按master_key分组统计
        rows = conn.execute(
            """SELECT
                master_key, master_name,
                COUNT(*) as total,
                SUM(CASE WHEN verify_7d_result IS NOT NULL THEN 1 ELSE 0 END) as verified_7d,
                SUM(CASE WHEN verify_7d_result = 'correct' THEN 1 ELSE 0 END) as correct_7d,
                SUM(CASE WHEN verify_7d_result = 'wrong' THEN 1 ELSE 0 END) as wrong_7d,
                SUM(CASE WHEN verify_7d_result = 'flat' THEN 1 ELSE 0 END) as flat_7d,
                AVG(CASE WHEN verify_7d_change_pct IS NOT NULL THEN verify_7d_change_pct END) as avg_change_7d,
                SUM(CASE WHEN verify_30d_result IS NOT NULL THEN 1 ELSE 0 END) as verified_30d,
                SUM(CASE WHEN verify_30d_result = 'correct' THEN 1 ELSE 0 END) as correct_30d,
                SUM(CASE WHEN verify_30d_result = 'wrong' THEN 1 ELSE 0 END) as wrong_30d
               FROM master_decision_history
               WHERE created_at >= datetime('now', ?)
               GROUP BY master_key, master_name
               ORDER BY correct_7d * 1.0 / MAX(verified_7d, 1) DESC""",
            (f"-{days} days",),
        ).fetchall()

        per_master = []
        total_all = 0
        verified_all = 0
        correct_all = 0
        for r in rows:
            d = dict(r)
            win_rate = (d["correct_7d"] / d["verified_7d"] * 100) if d["verified_7d"] > 0 else 0
            win_rate_30d = (d["correct_30d"] / d["verified_30d"] * 100) if d["verified_30d"] > 0 else 0
            per_master.append({
                "master_key": d["master_key"],
                "master_name": d["master_name"],
                "total": d["total"],
                "verified_7d": d["verified_7d"],
                "correct_7d": d["correct_7d"],
                "wrong_7d": d["wrong_7d"],
                "flat_7d": d["flat_7d"],
                "win_rate_7d": round(win_rate, 1),
                "avg_change_7d": round(d["avg_change_7d"], 2) if d["avg_change_7d"] else 0,
                "verified_30d": d["verified_30d"],
                "correct_30d": d["correct_30d"],
                "wrong_30d": d["wrong_30d"],
                "win_rate_30d": round(win_rate_30d, 1),
            })
            total_all += d["total"]
            verified_all += d["verified_7d"]
            correct_all += d["correct_7d"]

        conn.close()

        return {
            "per_master": per_master,
            "best_master": per_master[0] if per_master else None,
            "worst_master": per_master[-1] if per_master else None,
            "overall": {
                "total": total_all,
                "verified": verified_all,
                "correct": correct_all,
                "win_rate": round(correct_all / verified_all * 100, 1) if verified_all > 0 else 0,
            },
            "days": days,
        }
    except Exception as e:
        logger.warning(f"[master_decision] 胜率统计失败: {e}")
        return {"per_master": [], "overall": {"total": 0, "verified": 0, "correct": 0, "win_rate": 0}, "days": days}
